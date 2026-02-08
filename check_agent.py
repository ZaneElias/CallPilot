import os
import httpx
import asyncio
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("AGENT_ID")

async def check_agent_settings():
    url = f"https://api.elevenlabs.io/v1/convai/agents/{AGENT_ID}"
    headers = {"xi-api-key": API_KEY}

    print(f"🕵️ CHECKING AGENT: {AGENT_ID}...")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            name = data.get('name')
            config = data.get('conversation_config', {})
            tts = config.get('tts', {})
            security = data.get('platform_settings', {}).get('security', {})
            
            print("\n--- 📋 AGENT REPORT ---")
            print(f"Name: {name}")
            print(f"Voice ID (Default): {tts.get('voice_id')}")
            print(f"First Message (Default): {config.get('agent', {}).get('first_message')}")
            print("\n--- 🔒 SECURITY SETTINGS (CRITICAL) ---")
            print(f"Allow Overrides (General): {security.get('allow_banned_terms')}") # Sometime grouped
            # Note: The API structure for security flags can vary, printing whole block
            print(f"Full Security Block: {security}")
            
            print("\n--- DIAGNOSIS ---")
            if str(security).find("true") == -1 and str(security).find("True") == -1: 
                # This is a loose check, but if security is restrictive, overrides fail
                print("❌ WARNING: It looks like Overrides might be disabled or restricted.")
            else:
                print("✅ Security looks okay.")
                
        else:
            print(f"❌ ERROR: Could not find agent. Status: {response.status_code}")
            print(f"Reason: {response.text}")

if __name__ == "__main__":
    asyncio.run(check_agent_settings())