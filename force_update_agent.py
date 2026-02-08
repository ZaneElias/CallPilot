import os
import httpx
import asyncio
from dotenv import load_dotenv

load_dotenv()

# LOAD KEYS
API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("AGENT_ID")

# --- CONFIGURATION ---
# 1. Set Jessica as the permanent voice
NEW_VOICE_ID = "cgSgspJ2msm6clMCkdW9" 

# 2. Set the permanent opening line
NEW_FIRST_MESSAGE = "Hello, I'm calling from CallPilot to book an appointment for my client."

async def force_update_agent():
    if not API_KEY or not AGENT_ID:
        print("❌ ERROR: Missing API_KEY or AGENT_ID in .env file.")
        return

    url = f"https://api.elevenlabs.io/v1/convai/agents/{AGENT_ID}"
    headers = {"xi-api-key": API_KEY}
    
    print(f"🔧 UPDATING AGENT: {AGENT_ID}...")
    print(f"   -> Setting Voice to: Jessica ({NEW_VOICE_ID})")
    print(f"   -> Setting Message to: '{NEW_FIRST_MESSAGE}'")
    
    # Payload to permanently change the agent's brain
    payload = {
        "conversation_config": {
            "agent": {
                "first_message": NEW_FIRST_MESSAGE,
                "language": "en"
            },
            "tts": {
                "voice_id": NEW_VOICE_ID # Forces Jessica
            }
        },
        "platform_settings": {
            "security": {
                # This UNLOCKS the agent so future overrides will work too
                "allow_custom_rules": True,
                "allow_banned_terms": True
            }
        }
    }

    async with httpx.AsyncClient() as client:
        # We use PATCH to update existing settings
        response = await client.patch(url, json=payload, headers=headers)
        
        if response.status_code == 200:
            print("\n✅ SUCCESS! Agent settings have been rewritten.")
            print("------------------------------------------------")
            print("👉 NOW: Restart your 'main.py' server and make a call.")
            print("   You should hear Jessica immediately.")
        else:
            print(f"❌ ERROR: Failed to update. Status: {response.status_code}")
            print(f"Reason: {response.text}")

if __name__ == "__main__":
    asyncio.run(force_update_agent())