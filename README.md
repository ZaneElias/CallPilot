# CallPilot

CallPilot uses ElevenLabs Conversational AI and Twilio to run voice agents that book appointments with providers. Solo mode places a single call; Swarm mode ranks providers and runs parallel outbound calls to the top matches. Confirmed bookings appear in the **Telemetry Terminal** and can be saved to Google Calendar and/or forwarded to Make.com.

## Quick start

1. Copy `.env.example` to `.env` and set:
   - **Required for calls:** `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `AGENT_ID`, `AGENT_PHONE_NUMBER_ID`
   - **Optional:** `MAKE_WEBHOOK_URL` (forward bookings to Make.com), `GOOGLE_CALENDAR_CREDENTIALS_PATH` and `GOOGLE_CALENDAR_ID` (save bookings to Google Calendar)
2. **ElevenLabs:** In your agent's **confirm_booking** Server Tool, set the URL to `https://<your-backend>/webhook/booking` so bookings reach your app.
3. `pip install -r requirements.txt`
4. `python main.py` or `uvicorn main:app --host 0.0.0.0 --port 8000`
5. Open `http://localhost:8000`

The UI shows **Agent** (configured when env vars are set) and **Calendar** (connected when Google credentials are valid). After a booking is confirmed, the Telemetry Terminal shows e.g. *Booking confirmed: Provider — 2025-02-10 at 14:00 (saved to calendar)* when the event was written to Google Calendar.

## Provider directory (data simulation)

The **provider directory** for the demo is the JSON file **`providers.json`** in the project root. It defines the list of providers (e.g. dental offices) that the app can rank and call.

- Each provider has: `id`, `name`, `phone`, `distance_miles`, `rating`, `availability_score`, `specialty`, and optional `lat`/`lng`.
- The backend scores providers with a weighted formula (rating, distance, availability) and uses this list for **Swarm** mode (parallel outreach) and for the **Discovery Engine** table in the UI.
- You can edit `providers.json` to add more rows or change fields for demos. For a full workflow showcase without calling real offices, use test phone numbers in `phone` and optionally pair with a simulated receptionist (see below).

## Receptionist interaction and simulation

- **Live calls:** By default, when you deploy Solo or Swarm, the agent (Jessica) calls the **Target Phone** you enter (or, in Swarm, your number is used so you receive the parallel calls). The other end of the call is a real number (e.g. the provider’s `phone` from `providers.json`, or your own phone for testing). There is no built-in simulated receptionist; the person (or voicemail) that answers is the real callee.
- **Simulated receptionist for demos:** To showcase the full booking workflow without calling real offices, you can:
  - Use a **second ElevenLabs agent** (or another Twilio number) that plays the role of “receptionist”: when Jessica calls that number, the receptionist agent answers and follows a script (e.g. offers times, confirms a slot). Configure that agent in the ElevenLabs dashboard and put its number in `providers.json` for demo providers.
  - Or point provider `phone` fields to **your own test number** and have a team member answer and play the receptionist manually.

The app does not include a dedicated “demo mode” toggle; simulation is achieved by choosing who (or which agent) is at the other end of the line.

## Saving bookings to Google Calendar

For the agent (Jessica) to save booked dates when she confirms an appointment:

1. **ElevenLabs:** In your agent's **confirm_booking** Server Tool, set the URL to your backend base URL + `/webhook/booking`, e.g. `https://your-callpilot.ngrok.io/webhook/booking` (or your deployed URL). The tool should POST JSON with `date`, `time`, `provider_name`, and optional `title` and `user_phone`.

2. **Make.com (optional):** Set `MAKE_WEBHOOK_URL` in `.env` to your Make.com custom webhook URL. The backend will forward each booking there so your Make.com scenario can create a Google Calendar event (or do something else).

3. **Direct Calendar (optional):** Set `GOOGLE_CALENDAR_CREDENTIALS_PATH` in `.env` to a service account JSON file that has write access (e.g. `calendar.events` scope). The backend will then create a Google Calendar event when it receives a booking at `POST /webhook/booking`, in addition to forwarding to Make.com if configured.

Use either Make.com or direct Calendar (or both). If neither is set, bookings are received but not persisted to a calendar.

### Troubleshooting: nothing saved to Calendar or Make.com

- **ElevenLabs webhook URL:** The agent's **confirm_booking** Server Tool in the ElevenLabs dashboard must have its URL set to your app's public base URL + `/webhook/booking`, e.g. `https://your-app.ngrok.io/webhook/booking`. If this is wrong or missing, no request reaches the backend and nothing is saved. Bookings that do reach the backend will still appear in the Telemetry Terminal.
- **Google Calendar with a service account:** The backend defaults to `GOOGLE_CALENDAR_ID=primary`. For a service account, "primary" is the service account's own calendar, not yours. To have events show up in your calendar: (1) In Google Calendar, share the target calendar with the service account email (from the JSON key). (2) Set `GOOGLE_CALENDAR_ID` in `.env` to that calendar's ID (e.g. your email or the calendar ID from Calendar settings).

## API overview

- **Calls:** `POST /start-call` (Solo), `POST /start-swarm` (Swarm). Both use the ElevenLabs Conv AI agent (Jessica) with a refined prompt and first message "for my client."
- **Bookings:** `POST /webhook/booking` receives confirm_booking from ElevenLabs; stores recent bookings in memory; forwards to Make.com if `MAKE_WEBHOOK_URL` is set; creates a Google Calendar event if credentials have write scope. `GET /bookings/recent` returns the last 20 bookings for the Telemetry Terminal.
- **Status:** `GET /agent/status` (agent configured?), `GET /calendar/status` (Calendar connected?), `GET /calendar/availability?date=YYYY-MM-DD` (busy slots).
- **Providers:** `GET /providers` returns ranked providers for the Discovery Engine table.

## Hints and resources

- **Backend:** FastAPI ([main.py](main.py)); Twilio used via ElevenLabs outbound.
- **Agent persona:** Jessica is an executive assistant booking on behalf of the user ("for my client"). First message and briefing are set in code; optional [force_update_agent.py](force_update_agent.py) updates the ElevenLabs dashboard default.
- **Agentic tools:** check_availability and confirm_booking (configured in the ElevenLabs agent; bookings POST to `/webhook/booking`, then to Make.com and/or Google Calendar).
- **Calendar:** Optional Google Calendar API (see `.env.example`). When credentials are set, the UI shows “Calendar connected” and you can use `/calendar/status` and `/calendar/availability` With write scope the backend creates events from `POST /webhook/booking`. Telemetry shows "(saved to calendar)" per booking when applicable.
- **Provider data:** Static directory in [providers.json](providers.json); optional future use of Google Places or Distance Matrix APIs for live ratings/distances.
