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

load_dotenv()

app = FastAPI()

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

# --- HELPER FUNCTIONS ---
def load_providers():
    with open('providers.json', 'r') as f:
        return json.load(f)

def calculate_provider_score(provider, prefs: UserPreferences):
    # Base Score: Rating * 20 (Max 100)
    score = provider.get('rating', 0) * 20
    
    # Distance Penalty: -5 points per mile
    score -= (provider.get('distance_miles', 0) * 5)
    
    # Elite Bonus: +15 for ratings > 4.7
    if provider.get('rating', 0) > 4.7:
        score += 15
        
    # User Preferences
    if prefs.prioritize_rating and provider.get('rating', 0) >= 4.5:
        score += 10
    if prefs.prioritize_distance and provider.get('distance_miles', 0) < 3.0:
        score += 10

    return round(max(0, min(100, score)), 1)  # Clamp between 0-100

async def refine_instruction(objective, context=""):
    """Uses OpenAI to turn a simple objective into a professional briefing."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "You are an expert agent briefer. Be concise. receptionists are busy."},
                    {"role": "user", "content": f"Context: {context}\nTask: {objective}\nCreate a 2-sentence briefing for the voice agent."}
                ]
            },
            timeout=10.0
        )
    return response.json()['choices'][0]['message']['content']

async def trigger_single_call(phone, prompt):
    """Triggers the ElevenLabs Call via the Twilio Integration."""
    url = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
    
    headers = {
        "xi-api-key": os.getenv("ELEVENLABS_API_KEY"),
        "Content-Type": "application/json"
    }
    
    # We need a short opening sentence so the AI starts talking immediately
    # We extract this from the prompt or just genericize it.
    # For a hackathon, let's make it direct.
    first_sentence = "Hello, I am calling to book an appointment."
    
    payload = {
        "agent_id": os.getenv("AGENT_ID"),
        "agent_phone_number_id": os.getenv("AGENT_PHONE_NUMBER_ID"),
        "to_number": phone,
        "conversation_config": {
            "overrides": {
                "agent": {
                    "prompt": {"prompt": prompt},
                    "first_message": first_sentence  # <--- THIS IS THE FIX
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
            print(f"Call failed: {str(e)}")
            return {"status": "error", "error": str(e)}

# --- ROUTES ---

@app.post("/start-call")
async def start_call(request: CallRequest):
    print(f">> SINGLE CALL REQUEST: {request.phone_number}")
    refined_prompt = await refine_instruction(request.objective, "Single call mode.")
    result = await trigger_single_call(request.phone_number, refined_prompt)
    return result

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
        "swarmed_providers": top_providers # This sends the data Lovable needs to display cards
    }

# Serve the Lovable Frontend (Optional - if you build it)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)