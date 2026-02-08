"""
CallPilot FastAPI Backend
Loads env vars, refines tasks via OpenAI, triggers ElevenLabs outbound calls.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("AGENT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AGENT_PHONE_NUMBER_ID = os.getenv("AGENT_PHONE_NUMBER_ID")


def _validate_env():
    """Fail fast at startup if required env vars are missing."""
    missing = []
    if not ELEVENLABS_API_KEY:
        missing.append("ELEVENLABS_API_KEY")
    if not AGENT_ID:
        missing.append("AGENT_ID")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


_validate_env()

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
ELEVENLABS_OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
PROVIDERS_PATH = Path(__file__).parent / "providers.json"
USER_PREFERENCES_PATH = Path(__file__).parent / "user_preferences.json"

CHECK_AVAILABILITY_URL = os.getenv("CHECK_AVAILABILITY_URL")
CONFIRM_BOOKING_URL = os.getenv("CONFIRM_BOOKING_URL")


def load_providers() -> list[dict]:
    """Load and parse providers.json. Raises HTTPException on failure."""
    try:
        with open(PROVIDERS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="providers.json not found")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"providers.json invalid: {e}")


def load_user_preferences() -> dict:
    """Load user_preferences.json. Returns defaults if file missing."""
    defaults = {"max_distance": 5.0, "min_rating": 4.0, "preferred_time": "morning"}
    try:
        with open(USER_PREFERENCES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {**defaults, **data} if isinstance(data, dict) else defaults
    except (FileNotFoundError, json.JSONDecodeError):
        return defaults


def calculate_provider_score(provider: dict, prefs: "UserPreferences | dict") -> float:
    """
    Rating is 20 points per star (Max 100).
    Deduct 5 points per mile.
    Elite bonus for 4.7+ stars.
    """
    score = provider.get("rating", 0) * 20
    score -= provider.get("distance_miles", provider.get("distance", 0)) * 5
    if provider.get("rating", 0) > 4.7:
        score += 15
    return round(max(0, score), 1)


async def check_calendar(client: httpx.AsyncClient, preferences: dict) -> list[str]:
    """
    Call CHECK_AVAILABILITY_URL to get free slots.
    Returns fallback [preferred_time] if URL unset or request fails.
    """
    fallback = [preferences.get("preferred_time", "morning")]
    if not CHECK_AVAILABILITY_URL:
        return fallback
    try:
        resp = await client.post(
            CHECK_AVAILABILITY_URL,
            headers={"Content-Type": "application/json"},
            json={"preferred_time": preferences.get("preferred_time", "morning")},
            timeout=10.0,
        )
        if resp.status_code >= 400:
            return fallback
        data = resp.json() if resp.text else {}
        slots = data.get("free_slots") or data.get("slots") or data.get("available_slots") or []
        return slots if isinstance(slots, list) and slots else fallback
    except Exception:
        return fallback


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Shared httpx.AsyncClient for all API calls."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        app.state.http_client = client
        yield


app = FastAPI(title="CallPilot", lifespan=lifespan)


class StartCallRequest(BaseModel):
    phone_number: str
    task: str


class UserPreferences(BaseModel):
    max_distance: float = 5.0
    min_rating: float = 4.0
    prioritize_rating: bool = False
    prioritize_distance: bool = False


async def refine_instruction(client: httpx.AsyncClient, user_input: str) -> str:
    """
    Turn raw user input into a detailed voice agent system prompt using GPT-4o.
    Instructs the agent to be concise and to use Make.com tools when appropriate.
    """
    system_prompt = """You write system prompts for an AI voice agent that makes outbound calls (e.g., to receptionists).

Rules:
1. Transform the user's short input into a clear, detailed instruction the voice agent will follow.
2. The resulting instructions MUST tell the voice agent to be extremely concise and avoid long introductions. Receptionists are busy; the agent should state the purpose of the call in the first 10 seconds.
3. The agent has Make.com tools available: check_availability and confirm_booking. When booking appointments or checking schedules, instruct the agent to use check_availability to find open slots and confirm_booking to finalize the appointment.
4. Output only the refined instruction text—no meta-commentary or markdown."""

    try:
        response = await client.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input},
                ],
                "temperature": 0.7,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI API error: {e.response.text or str(e)}",
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"OpenAI request failed: {str(e)}",
        )


async def _trigger_call(
    client: httpx.AsyncClient, phone: str, prompt: str
) -> dict:
    """
    Trigger a single ElevenLabs outbound call.
    Returns {success, conversation_id, call_sid} or {success: False, error: "..."}.
    """
    payload = {
        "agent_id": AGENT_ID,
        "agent_phone_number_id": AGENT_PHONE_NUMBER_ID,
        "to_number": phone,
        "conversation_initiation_client_data": {
            "conversation_config_override": {
                "agent": {"prompt": {"prompt": prompt}},
            }
        },
    }
    try:
        resp = await client.post(
            ELEVENLABS_OUTBOUND_URL,
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
                detail = err_body.get("detail", err_body.get("message", resp.text))
            except Exception:
                detail = resp.text or resp.reason_phrase
            return {"success": False, "error": detail}
        data = resp.json()
        return {
            "success": True,
            "conversation_id": data.get("conversation_id"),
            "call_sid": data.get("callSid"),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


class StartSwarmRequest(BaseModel):
    user_phone: str
    objective: str
    preferences: UserPreferences | None = None


@app.post("/start-call")
async def start_call(request: StartCallRequest):
    """Refine the task via OpenAI, then trigger an ElevenLabs outbound call."""
    print(">> User Objective Received")
    print(f"   phone_number={request.phone_number}, task={request.task}")

    if not AGENT_PHONE_NUMBER_ID:
        raise HTTPException(
            status_code=500,
            detail="AGENT_PHONE_NUMBER_ID not set. Add your ElevenLabs phone number ID to .env.",
        )

    client: httpx.AsyncClient = request.app.state.http_client
    refined = await refine_instruction(client, request.task)
    print(">> OpenAI Refined Prompt:")
    print(f"   {refined}")

    result = await _trigger_call(client, request.phone_number, refined)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Call failed"))

    print(">> ElevenLabs Call Dispatched")
    return {
        "success": True,
        "conversation_id": result.get("conversation_id"),
        "callSid": result.get("call_sid"),
    }


@app.post("/start-swarm")
async def start_swarm(request: StartSwarmRequest):
    """Ranked swarm: load providers, filter by preferences, score, calendar check, dispatch top 3 in parallel."""
    print(">> Swarm Objective Received")
    print(f"   user_phone={request.user_phone}, objective={request.objective}")

    if not AGENT_PHONE_NUMBER_ID:
        raise HTTPException(
            status_code=500,
            detail="AGENT_PHONE_NUMBER_ID not set. Add your ElevenLabs phone number ID to .env.",
        )

    client: httpx.AsyncClient = request.app.state.http_client

    prefs = request.preferences or UserPreferences()
    file_prefs = load_user_preferences()
    preferences = {
        "max_distance": prefs.max_distance,
        "min_rating": prefs.min_rating,
        "preferred_time": file_prefs.get("preferred_time", "morning"),
    }

    providers = load_providers()
    min_rating = preferences.get("min_rating", 0)
    max_distance = preferences.get("max_distance", 999)

    filtered = [
        p
        for p in providers
        if p.get("rating", 0) >= min_rating
        and (p.get("distance_miles", p.get("distance", 999)) <= max_distance)
    ]

    scored = [(p, calculate_provider_score(p, prefs)) for p in filtered]
    top3_with_scores = sorted(scored, key=lambda x: x[1], reverse=True)[:3]
    top3 = [p for p, _ in top3_with_scores]

    free_slots = await check_calendar(client, preferences)
    refined = await refine_instruction(client, request.objective)

    print(">> SWARM DEPLOYED: Calling", ", ".join(p.get("name", "?") for p in top3) + "...")

    async def dispatch_one(p: dict, rank: int, score: float) -> dict:
        to_number = (
            request.user_phone
            if p.get("phone") == "USER_TEST_PHONE"
            else p.get("phone", "")
        )
        dist = p.get("distance_miles", p.get("distance", 0))
        prompt = (
            f"You are calling {p.get('name', 'the provider')}. "
            f"They have a match score of {score} and are {dist} miles away. "
            f"They are ranked #{rank}. "
            f"Your goal is to negotiate a {preferences.get('preferred_time', 'morning')} slot. "
            f"The user is free during these times: {', '.join(free_slots)}. "
            f"Only request slots that fall within these windows. {refined}"
        )
        res = await _trigger_call(client, to_number, prompt)
        return {
            "provider_id": p.get("id"),
            "name": p.get("name"),
            "phone": to_number,
            "conversation_id": res.get("conversation_id") if res.get("success") else None,
            "call_sid": res.get("call_sid") if res.get("success") else None,
            "success": res.get("success", False),
            "error": res.get("error") if not res.get("success") else None,
        }

    results = await asyncio.gather(
        *[dispatch_one(p, rank, score) for (p, score), rank in zip(top3_with_scores, range(1, 4))],
        return_exceptions=True,
    )

    deployed = []
    for i, r in enumerate(results):
        p = top3[i]
        to_number = (
            request.user_phone
            if p.get("phone") == "USER_TEST_PHONE"
            else p.get("phone", "")
        )
        if isinstance(r, Exception):
            deployed.append({
                "provider_id": p.get("id"),
                "name": p.get("name"),
                "phone": to_number,
                "conversation_id": None,
                "call_sid": None,
                "success": False,
                "error": str(r),
            })
        else:
            deployed.append(r)

    print(f">> Swarm: {len(deployed)} calls dispatched")
    return {"deployed_agents": deployed}


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/", StaticFiles(directory="static", html=True), name="static")
