---
name: phone-calls
description: Make outbound phone calls on the user's behalf using AI voice agents (Bland.ai or Vapi). Schedule appointments, make reservations, or deliver messages via realistic voice calls. Always confirm with user before dialing.
version: 2.0.0
author: NousResearch
license: MIT
metadata:
  hermes:
    tags: [phone, calling, voice, appointments, scheduling, bland.ai, vapi, elevenlabs, twilio, telephony]
    related_skills: [google-workspace, find-nearby]
---

# Phone Calls — AI Voice Agent

Make outbound phone calls on the user's behalf using AI voice agents. Uses the `phone_call.py` helper script (in this skill's `scripts/` directory) to call Bland.ai or Vapi APIs.

## When to Use

- User asks you to **call someone** (schedule appointment, make reservation, leave message)
- User asks to **schedule an appointment** (dentist, doctor, haircut, etc.)
- User asks to **make a reservation** (restaurant, hotel, etc.)
- User says "call", "phone", "ring", "dial", or mentions making a phone call

## Safety Rules — MANDATORY

1. **ALWAYS confirm with the user before making any call.** Show them:
   - The phone number you're about to call
   - A summary of what the AI will say/do
   - The voice and max duration
2. **Never call emergency numbers** (911, 112, 999, etc.)
3. **Only share user info they've explicitly authorized** (check memory/user profile)
4. **Never make calls with hostile, harassing, or offensive content**
5. **Phone number privacy:**
   - All phone numbers (except the user's own stored number) are SENSITIVE — never save to memory, never persist in session summaries or skills
   - Always mask numbers in responses: show last 4 digits only (e.g. "Called ***-***-1234")
   - The user's own number may only be shared with businesses during appointment booking when they need a callback/contact number — never in any other context
   - When confirming a call with the user, you may show the full number in the confirmation prompt, but mask it in all subsequent messages and summaries

## Providers

### Bland.ai (default — start here)
- All-in-one platform, simplest setup, one API key and you're calling
- Needs only `BLAND_API_KEY` env var
- Sign up free at https://app.bland.ai (~$2 trial credit)
- Voices: mason, josh, ryan, matt (male); evelyn, tina, june (female)
- ~$0.07-0.12/min
- Downside: voice quality is decent but noticeably robotic

### Vapi (upgrade — better voices)
- Flexible platform: plug in any voice (ElevenLabs, Deepgram, PlayHT, Cartesia) and any LLM
- Much more natural-sounding than Bland
- Requires a Twilio number for outbound calls (Vapi's free numbers are inbound-only)
- Setup:
  1. Sign up at https://dashboard.vapi.ai ($10 free credit)
  2. Sign up at https://twilio.com ($15 free credit)
  3. Buy a Twilio number (~$1/mo)
  4. Import it into Vapi (needs Twilio Account SID + Auth Token)
  5. Set `VAPI_API_KEY` and `VAPI_PHONE_NUMBER_ID`
- ~$0.10-0.25/min depending on voice/LLM choices
- If the user wants to upgrade from Bland, walk them through Twilio setup

## Helper Script

The script at `scripts/phone_call.py` handles all API calls. It uses only Python stdlib (no pip dependencies). Run it via `terminal` or `execute_code`.

```bash
# Locate the script
SCRIPT="$(find ~/.hermes/skills -path '*/phone-calls/scripts/phone_call.py' -print -quit)"

# Make a call
python3 "$SCRIPT" call "+15551234567" "Schedule a cleaning for Tuesday afternoon" --voice mason

# Check call result
python3 "$SCRIPT" status <call_id>

# Check call result with analysis questions (Bland only)
python3 "$SCRIPT" status <call_id> --analyze "Was appointment confirmed?,What time?"

# Check configuration
python3 "$SCRIPT" diagnose
```

## Configuration

Set via env vars (preferred) or `~/.hermes/config.yaml` under the `phone:` key. Env vars take priority.

**Bland.ai (quick start):**
```
BLAND_API_KEY=org_xxx
BLAND_DEFAULT_VOICE=mason     # optional
```

**Vapi:**
```
PHONE_PROVIDER=vapi
VAPI_API_KEY=xxx-xxx
VAPI_PHONE_NUMBER_ID=xxx-xxx
VAPI_VOICE_PROVIDER=11labs    # optional (default: 11labs)
VAPI_VOICE_ID=cjVigY5...     # optional (default: ElevenLabs "Eric")
VAPI_MODEL=gpt-4o             # optional
```

**config.yaml alternative:**
```yaml
phone:
    provider: bland          # or "vapi"
    bland:
        api_key: org_xxx
        default_voice: mason
    vapi:
        api_key: xxx-xxx
        phone_number_id: xxx-xxx
        default_voice_provider: 11labs
        default_voice_id: cjVigY5qzO86Huf0OWal
        model: gpt-4o
```

## Procedure

### Step 0: First-time setup (only once)

Run `diagnose` to check if a provider is configured:
```bash
python3 "$SCRIPT" diagnose
```

If not configured, ask the user to choose a provider and set the env vars or config.

### Step 1: Gather call details

Collect from the user:
- **Who to call**: Name and phone number (look up if needed)
- **Purpose**: What should the AI say/accomplish
- **User info to share**: Name, preferences, insurance, etc.
- **Constraints**: Preferred times, budget, special requests

### Step 2: Craft the task prompt

Write the task like you're briefing a human assistant. Include:
- All necessary details (names, dates, preferences)
- Fallback options ("if Tuesday isn't available, try Wednesday")
- Boundaries on what info to share
- A natural first sentence

**Name pronunciation**: If the user's name has a non-obvious pronunciation, spell it phonetically in the task prompt (e.g., "Morganne" for Morgane).

### Step 3: Confirm with user

Present a summary and wait for explicit approval:
```
I'm ready to call:
  Number:  +1 (555) 123-4567 (Dr. Smith's Dental Office)
  Purpose: Schedule a cleaning, Tuesday afternoon preferred
  Voice:   mason (male)
  Max:     3 minutes

Shall I go ahead?
```

### Step 4: Make the call

```bash
python3 "$SCRIPT" call "+15551234567" "You are calling Dr. Smith's Dental Office on behalf of Morganne. Schedule a dental cleaning for Tuesday afternoon. If Tuesday is not available, try Wednesday or Thursday. Morganne's phone number for callbacks is +14385551234." --voice mason --max-duration 3
```

### Step 5: Get results

Wait 60-90 seconds, then check the status:
```bash
python3 "$SCRIPT" status <call_id>
```

If the call is still in progress, wait and try again. Once completed, present a summary:
- Was the objective accomplished?
- Key details (date, time, location, confirmations)
- Any follow-up needed

For Bland.ai, you can also ask structured analysis questions:
```bash
python3 "$SCRIPT" status <call_id> --analyze "Was the appointment confirmed?,What date and time?,Any special instructions?"
```

## Importing a Twilio Number into Vapi

For Vapi outbound calls, you need to import a Twilio number:

1. Sign up at https://www.twilio.com/try-twilio
2. Buy a phone number in the Twilio console
3. Copy your Account SID and Auth Token from Account > API keys & tokens
4. Import into Vapi:
```bash
curl -X POST https://api.vapi.ai/phone-number \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "twilio",
    "number": "+1XXXXXXXXXX",
    "twilioAccountSid": "AC...",
    "twilioAuthToken": "..."
  }'
```
5. Use the returned `id` as `VAPI_PHONE_NUMBER_ID`

## Pitfalls

- **No pip dependencies needed**: The script uses only Python stdlib (`urllib`)
- **Call goes to voicemail**: Check `answered_by` field in status results
- **"Terrible voice"**: Switch from Bland to Vapi with ElevenLabs voices for much better quality
- **Vapi free numbers can't make outbound calls**: You must import a Twilio number
- **Vapi free numbers can't call international**: Canadian numbers count as international from US numbers
- **Name pronunciation**: ElevenLabs ignores phonetic hyphens — spell names literally as they should be pronounced
- **Transcript not ready**: Poll status a few times with 30-60s delays between attempts
