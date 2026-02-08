import os
import json
import asyncio
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional

# #region agent log
def _dlog(location: str, message: str, data: dict, hypothesis_id: str = ""):
    try:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cursor")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "debug.log")
        line = json.dumps({"location": location, "message": message, "data": data, "hypothesisId": hypothesis_id, "timestamp": __import__("time").time() * 1000}) + "\n"
        with open(log_path, "a") as f:
            f.write(line)
    except Exception:
        pass
# #endregion

load_dotenv()

app = FastAPI()

# #region agent log
@app.middleware("http")
async def _debug_log_middleware(request, call_next):
    if request.url.path == "/start-call" and request.method == "POST":
        _dlog("main.py:middleware:start-call", "POST /start-call request received", {"path": request.url.path}, "H1")
    try:
        response = await call_next(request)
        if request.url.path == "/start-call":
            _dlog("main.py:middleware:start-call", "POST /start-call response", {"status_code": response.status_code}, "H4")
        return response
    except Exception as e:
        _dlog("main.py:middleware:start-call", "POST /start-call exception", {"error": str(e), "type": type(e).__name__}, "H4")
        raise
# #endregion

# 1. CORS: Allow the Frontend (Lovable) to talk to the Backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (good for local development)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DATA MODELS ---
class UserPreferences(BaseModel):
    max_distance: float = 20.0
    min_rating: float = 1.0
    prioritize_rating: bool = False
    prioritize_distance: bool = False

class SwarmRequest(BaseModel):
    user_phone: str
    objective: str
    preferences: Optional[UserPreferences] = None

class CallRequest(BaseModel):
    phone_number: str
    objective: str

class BookingWebhook(BaseModel):
    """Payload from ElevenLabs confirm_booking tool; forwarded to Make.com."""
    date: str  # YYYY-MM-DD
    time: str  # e.g. 14:00 or 2:00 PM
    provider_name: str
    title: Optional[str] = None
    user_phone: Optional[str] = None

# In-memory store of recent bookings for Telemetry Terminal (max 20, newest first)
RECENT_BOOKINGS: List[dict] = []
RECENT_BOOKINGS_MAX = 20

# --- HELPER FUNCTIONS ---
def load_providers():
    with open('providers.json', 'r') as f:
        return json.load(f)

def calculate_provider_score(provider, prefs: UserPreferences):
    """Weighted formula: (Rating * 0.4) + (Distance Score * 0.3) + (Availability Score * 0.3)."""
    rating = provider.get('rating', 0)
    distance_miles = provider.get('distance_miles', 0)
    availability = provider.get('availability_score', 0)

    # Rating component (0.4): normalize 0-5 -> 0-100, then weight
    rating_normalized = (rating / 5.0) * 100
    rating_component = (rating_normalized / 100.0) * 0.4

    # Distance score (0.3): closer = higher (0-100 scale), then weight
    distance_score = max(0, min(100, 100 - distance_miles * 10))
    distance_component = (distance_score / 100.0) * 0.3

    # Availability score (0.3): already 0-100, then weight
    availability_normalized = min(100, max(0, availability))
    availability_component = (availability_normalized / 100.0) * 0.3

    # Combined 0-1, scale to 0-100
    raw = rating_component + distance_component + availability_component
    score = round(raw * 100, 1)
    return max(0, min(100, score))

async def refine_instruction(objective, context=""):
    """Uses GPT-4o to create a strict Executive Assistant (Jessica) briefing with tool use."""
    # #region agent log
    _dlog("main.py:refine_instruction:entry", "refine_instruction called", {"objective_len": len(objective) if objective else 0, "context_len": len(context) if context else 0}, "H2")
    openai_key = os.getenv("OPENAI_API_KEY")
    _dlog("main.py:refine_instruction:before_openai", "OPENAI_API_KEY check", {"has_key": bool(openai_key), "key_len": len(openai_key) if openai_key else 0}, "H2")
    # #endregion
    system = (
        "You are briefing Jessica, a strict Executive Assistant voice agent. She is professional and concise. "
        "Jessica is booking on behalf of the user (the client). She must never say she is booking 'for herself' or 'for me'; she says 'for my client' or 'for you' as appropriate. "
        "Jessica MUST use the check_availability tool when the receptionist offers or discusses appointment times. "
        "Jessica MUST use the confirm_booking tool when a time is agreed to lock in the appointment. "
        "Output only a 2-sentence briefing for Jessica. Do not alter or generalize the user's objective."
    )
    user_content = (
        f"Context: {context}\n\n"
        f"User objective (do not alter or generalize): {objective}\n\n"
        "Create a 2-sentence briefing for Jessica that preserves this exact objective and instructs her to use check_availability and confirm_booking when appropriate."
    )
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content}
                ]
            },
            timeout=10.0
        )
    # #region agent log
    resp_json = response.json()
    _dlog("main.py:refine_instruction:after_openai", "OpenAI response", {"status_code": response.status_code, "has_choices": "choices" in resp_json, "choices_len": len(resp_json.get("choices", [])) if isinstance(resp_json.get("choices"), list) else 0, "error": resp_json.get("error", {}).get("message") if "error" in resp_json else None}, "H2")
    # #endregion
    if response.status_code != 200 or "error" in resp_json:
        err_msg = (resp_json.get("error") or {}).get("message") if isinstance(resp_json.get("error"), dict) else str(resp_json.get("error", "Unknown error"))
        if not os.getenv("OPENAI_API_KEY"):
            err_msg = "OPENAI_API_KEY is not set. Add it to your .env file and restart the server."
        raise HTTPException(status_code=503, detail=f"OpenAI briefing failed: {err_msg}")
    choices = resp_json.get("choices") or []
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise HTTPException(status_code=503, detail="OpenAI returned an unexpected response format.")
    return choices[0]["message"].get("content") or ""

async def trigger_single_call(phone, prompt):
    """Triggers the ElevenLabs Call via the Twilio Integration."""
    # #region agent log
    _dlog("main.py:trigger_single_call:entry", "trigger_single_call called", {"phone": phone[:6] + "..." if phone and len(phone) > 6 else phone, "prompt_len": len(prompt) if prompt else 0}, "H5")
    _dlog("main.py:trigger_single_call:env", "ElevenLabs env", {"has_xi_key": bool(os.getenv("ELEVENLABS_API_KEY")), "has_agent_id": bool(os.getenv("AGENT_ID")), "has_phone_id": bool(os.getenv("AGENT_PHONE_NUMBER_ID"))}, "H5")
    # #endregion
    url = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
    
    headers = {
        "xi-api-key": os.getenv("ELEVENLABS_API_KEY"),
        "Content-Type": "application/json"
    }
    
    # 1. DEFINE THE OPENING LINE
    # This forces the AI to speak this FIRST, overriding the dashboard default.
    first_sentence = "Hello, I'm calling to book an appointment for my client."
    
    voice_id = os.getenv("JESSICA_VOICE_ID", "cgSgspJ2msm6clMCkdW9")
    payload = {
        "agent_id": os.getenv("AGENT_ID"),
        "agent_phone_number_id": os.getenv("AGENT_PHONE_NUMBER_ID"),
        "to_number": phone,
        "conversation_config": {
            "overrides": {
                "agent": {
                    "prompt": {"prompt": prompt},
                    "first_message": first_sentence,
                    "language": "en"
                },
                "tts": {
                    "voice_id": voice_id
                }
            }
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(url, json=payload, headers=headers)
            res.raise_for_status()
            return {"status": "success", "data": res.json()}
        except Exception as e:
            # #region agent log
            _dlog("main.py:trigger_single_call:exception", "ElevenLabs call exception", {"error": str(e), "type": type(e).__name__}, "H5")
            # #endregion
            print(f"Call failed: {str(e)}")
            if 'res' in locals():
                print(f"API Response: {res.text}")
            return {"status": "error", "error": str(e)}

# --- ROUTES ---

@app.post("/start-call")
async def start_call(request: CallRequest):
    # #region agent log
    _dlog("main.py:start_call:entry", "start_call entry", {"phone_number": (request.phone_number or "")[:8] + "...", "objective_len": len(request.objective) if request.objective else 0}, "H1")
    # #endregion
    print(f">> SINGLE CALL REQUEST: {request.phone_number}")
    try:
        refined_prompt = await refine_instruction(request.objective, "Single call mode.")
        # #region agent log
        _dlog("main.py:start_call:after_refine", "refine_instruction ok", {"prompt_len": len(refined_prompt) if refined_prompt else 0}, "H4")
        # #endregion
        result = await trigger_single_call(request.phone_number, refined_prompt)
        # #region agent log
        _dlog("main.py:start_call:after_trigger", "trigger_single_call result", {"status": result.get("status"), "has_error": "error" in result}, "H4")
        # #endregion
        return result
    except Exception as e:
        # #region agent log
        _dlog("main.py:start_call:exception", "start_call exception", {"error": str(e), "type": type(e).__name__}, "H4")
        # #endregion
        raise

@app.post("/start-swarm")
async def start_swarm(request: SwarmRequest):
    print(f">> SWARM REQUEST RECEIVED: {request.objective}")
    
    prefs = request.preferences or UserPreferences()
    providers = load_providers()
    
    # 1. Score and Filter
    scored_providers = []
    for p in providers:
        # Basic filtering
        if p['distance_miles'] > prefs.max_distance: continue
        if p['rating'] < prefs.min_rating: continue
        
        # Calculate Score
        p['match_score'] = calculate_provider_score(p, prefs)
        scored_providers.append(p)
    
    # 2. Rank (Top 3)
    top_providers = sorted(scored_providers, key=lambda x: x['match_score'], reverse=True)[:3]
    
    # 3. Dispatch Calls (Parallel)
    tasks = []
    for p in top_providers:
        # INJECTING THE CONTEXT IS CRITICAL FOR THE DEMO
        context = (
            f"You are calling {p['name']}, ranked #{top_providers.index(p)+1} with a match score of {p['match_score']}. "
            f"They are {p['distance_miles']} miles away. "
            "Use the 'check_availability' tool immediately if they offer a time."
        )
        
        refined_prompt = await refine_instruction(request.objective, context)
        
        # Use USER phone for demo purposes so you receive the call
        tasks.append(trigger_single_call(request.user_phone, refined_prompt))
    
    # Fire all calls simultaneously
    results = await asyncio.gather(*tasks)
    
    print(f">> SWARM DEPLOYED: {len(top_providers)} agents active.")
    
    return {
        "status": "Swarm Deployed",
        "deployed_agents": len(top_providers),
        "swarmed_providers": top_providers
    }

@app.get("/providers")
def get_providers():
    """Return providers ranked by weighted score for the Live Ranking Table."""
    prefs = UserPreferences()
    providers = load_providers()
    scored = []
    for p in providers:
        if p['distance_miles'] > prefs.max_distance or p['rating'] < prefs.min_rating:
            continue
        p_copy = dict(p)
        p_copy['match_score'] = calculate_provider_score(p_copy, prefs)
        scored.append(p_copy)
    ranked = sorted(scored, key=lambda x: x['match_score'], reverse=True)
    return ranked

def _get_calendar_service(read_only=True):
    """Return Google Calendar API service if credentials are configured, else None."""
    creds_path = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_PATH") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path or not os.path.isfile(creds_path):
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"] if read_only else ["https://www.googleapis.com/auth/calendar.events"]
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        return build("calendar", "v3", credentials=creds)
    except Exception:
        return None

def _create_calendar_event_from_booking(payload: BookingWebhook) -> bool:
    """Create a Google Calendar event from a booking payload. Returns True if created, False otherwise."""
    service = _get_calendar_service(read_only=False)
    if not service:
        return False
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    try:
        date_str = payload.date
        time_str = (payload.time or "09:00").strip()
        if not date_str:
            return False
        if ":" not in time_str:
            time_str = "09:00"
        hour, minute = 9, 0
        if "AM" in time_str.upper() or "PM" in time_str.upper():
            from datetime import datetime
            for fmt in ("%I:%M %p", "%I:%M%p", "%I %p"):
                try:
                    t = datetime.strptime(time_str.replace(".", ""), fmt)
                    hour, minute = t.hour, t.minute
                    break
                except ValueError:
                    continue
        else:
            parts = time_str.replace(".", ":").split(":")
            if len(parts) >= 2:
                try:
                    hour, minute = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
        from datetime import datetime, timedelta
        start_dt = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=30)
        start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
        title = payload.title or f"Appointment at {payload.provider_name}"
        body = {
            "summary": title,
            "description": f"Booked via CallPilot. Provider: {payload.provider_name}.",
            "start": {"dateTime": start_iso, "timeZone": "UTC"},
            "end": {"dateTime": end_iso, "timeZone": "UTC"},
        }
        service.events().insert(calendarId=calendar_id, body=body).execute()
        print(f">> BOOKING WEBHOOK: created Google Calendar event for {payload.provider_name}")
        return True
    except Exception as e:
        print(f">> BOOKING WEBHOOK: Calendar create failed: {e}")
        return False

@app.get("/calendar/status")
def calendar_status():
    """Return whether Google Calendar is connected (for UI 'Calendar connected' indicator)."""
    service = _get_calendar_service()
    if not service:
        return {"connected": False}
    try:
        service.calendarList().list(maxResults=1).execute()
        return {"connected": True}
    except Exception:
        return {"connected": False}

@app.get("/calendar/availability")
def calendar_availability(date: str):
    """
    Return busy slots for the given date (YYYY-MM-DD) to support double-book prevention.
    Uses primary calendar; optional GOOGLE_CALENDAR_ID in .env to override.
    """
    service = _get_calendar_service()
    if not service:
        return {"connected": False, "busy": [], "free_slots": []}
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    try:
        from datetime import datetime
        time_min = f"{date}T00:00:00Z"
        time_max = f"{date}T23:59:59Z"
        events = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        busy = []
        for item in events.get("items", []):
            start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
            end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
            if start and end:
                busy.append({"start": start, "end": end})
        return {"connected": True, "busy": busy, "date": date}
    except Exception as e:
        return {"connected": False, "busy": [], "error": str(e)}

@app.get("/bookings/recent")
def get_recent_bookings():
    """Return recent bookings for Telemetry Terminal (newest first)."""
    return {"bookings": list(RECENT_BOOKINGS)}

@app.get("/agent/status")
def agent_status():
    """Return whether the AI agent is configured (for UI indicator). Does not verify ElevenLabs connectivity."""
    configured = bool(os.getenv("ELEVENLABS_API_KEY") and os.getenv("AGENT_ID") and os.getenv("AGENT_PHONE_NUMBER_ID"))
    return {"configured": configured}

@app.post("/webhook/booking")
async def webhook_booking(payload: BookingWebhook):
    """
    Receive confirm_booking from ElevenLabs. Forwards to Make.com if configured,
    and optionally creates a Google Calendar event when credentials have write scope.
    """
    body = payload.model_dump(mode="json")
    received_at = __import__("time").time() * 1000
    forwarded = False
    make_url = os.getenv("MAKE_WEBHOOK_URL")
    if make_url:
        async with httpx.AsyncClient() as client:
            try:
                res = await client.post(make_url, json=body, timeout=10.0)
                res.raise_for_status()
                print(f">> BOOKING WEBHOOK: forwarded to Make.com -> {res.status_code}")
                forwarded = True
            except Exception as e:
                print(f">> BOOKING WEBHOOK: Make.com forward failed: {e}")
    else:
        print(">> BOOKING WEBHOOK: MAKE_WEBHOOK_URL not set; skipping Make.com.")
    calendar_created = _create_calendar_event_from_booking(payload)
    entry = {
        "id": f"{payload.provider_name}|{payload.date}|{payload.time}|{int(received_at)}",
        "date": payload.date,
        "time": payload.time,
        "provider_name": payload.provider_name,
        "title": payload.title,
        "received_at": received_at,
        "calendar_created": calendar_created,
        "forwarded": forwarded,
    }
    RECENT_BOOKINGS.insert(0, entry)
    if len(RECENT_BOOKINGS) > RECENT_BOOKINGS_MAX:
        RECENT_BOOKINGS.pop()
    return {"status": "received", "forwarded": forwarded, "calendar_created": calendar_created}

# Serve the frontend from root
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)