import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# You need to get these two from your Twilio Console Dashboard
# They are NOT in your .env yet unless you put them there.
# You can hardcode them here for the test if you want, or add to .env
TWILIO_ACCOUNT_SID = "AC98fb66ccddea6e637d90f4ade6068919" # Replace with yours
TWILIO_AUTH_TOKEN = "03d04b44bfcd40e63d750a750d1f72b6"           # Replace with yours
TWILIO_PHONE_NUMBER = "+12626834425"                  # Your Twilio Number (Source)

TARGET_PHONE_NUMBER = "+9779864893933"               # Your Nepal Number

try:
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    print(f"Attempting to call {TARGET_PHONE_NUMBER} from {TWILIO_PHONE_NUMBER}...")

    call = client.calls.create(
        twiml='<Response><Say>This is a test call from Twilio. If you hear this, the line works.</Say></Response>',
        to=TARGET_PHONE_NUMBER,
        from_=TWILIO_PHONE_NUMBER
    )

    print(f"Call initiated! SID: {call.sid}")
    print("Check your phone now...")

except Exception as e:
    print(f"❌ CALL FAILED: {e}")