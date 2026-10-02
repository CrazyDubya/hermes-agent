#!/usr/bin/env python3
"""Phone Call CLI — Make and check outbound AI voice calls.

Usage:
    python3 phone_call.py call <phone_number> <task> [--voice NAME] [--first-sentence TEXT] [--max-duration MIN]
    python3 phone_call.py status <call_id> [--analyze "question1,question2"]
    python3 phone_call.py diagnose

Providers:
    Bland.ai (default): set BLAND_API_KEY env var
    Vapi:               set VAPI_API_KEY + VAPI_PHONE_NUMBER_ID env vars
                        and PHONE_PROVIDER=vapi

Configuration via env vars:
    PHONE_PROVIDER          "bland" (default) or "vapi"
    BLAND_API_KEY           Bland.ai organization key
    BLAND_DEFAULT_VOICE     Bland voice name (default: mason)
    VAPI_API_KEY            Vapi private key
    VAPI_PHONE_NUMBER_ID    Vapi phone number ID (imported Twilio number)
    VAPI_VOICE_PROVIDER     Voice provider for Vapi (default: 11labs)
    VAPI_VOICE_ID           Voice ID for Vapi (default: ElevenLabs "Eric")
    VAPI_MODEL              LLM model for Vapi assistant (default: gpt-4o)

Or via ~/.hermes/config.yaml under the 'phone:' key (env vars take priority).

Examples:
    # Make a call with Bland.ai
    BLAND_API_KEY=org_xxx python3 phone_call.py call "+15551234567" "Schedule a cleaning for Tuesday afternoon"

    # Check call result
    python3 phone_call.py status abc-123-def

    # Check config
    python3 phone_call.py diagnose
"""

import json
import os
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
BLAND_API_BASE = "https://api.bland.ai/v1"
BLAND_DEFAULT_VOICE = "mason"
BLAND_DEFAULT_MODEL = "enhanced"
BLAND_VOICES = {
    "mason": "Male, natural, friendly (recommended)",
    "josh": "Male, conversational",
    "ryan": "Male, professional",
    "matt": "Male, casual",
    "evelyn": "Female, natural, warm (recommended)",
    "tina": "Female, warm, friendly",
    "june": "Female, conversational",
}

VAPI_API_BASE = "https://api.vapi.ai"
VAPI_DEFAULT_VOICE_PROVIDER = "11labs"
VAPI_DEFAULT_VOICE_ID = "cjVigY5qzO86Huf0OWal"  # ElevenLabs "Eric"
VAPI_DEFAULT_MODEL = "gpt-4o"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def _load_config() -> dict:
    """Load phone config from ~/.hermes/config.yaml, falling back to {}."""
    config_path = os.path.expanduser("~/.hermes/config.yaml")
    if not os.path.exists(config_path):
        return {}
    try:
        import yaml  # optional dependency
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("phone", {})
    except ImportError:
        return {}
    except Exception:
        return {}


def _env_or_config(env_key: str, config_path: list, default: str = "") -> str:
    """Get a value from env var first, then config.yaml, then default."""
    val = os.environ.get(env_key, "")
    if val:
        return val
    cfg = _load_config()
    for key in config_path:
        if isinstance(cfg, dict):
            cfg = cfg.get(key, {})
        else:
            return default
    return str(cfg) if cfg and not isinstance(cfg, dict) else default


def _get_provider() -> str:
    return _env_or_config("PHONE_PROVIDER", ["provider"], "bland").lower().strip()


# ---------------------------------------------------------------------------
# HTTP helper (stdlib only — no requests dependency)
# ---------------------------------------------------------------------------
def _http(method: str, url: str, headers: dict, data: dict | None = None) -> dict:
    """Make an HTTP request and return parsed JSON."""
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else ""
        print(f"HTTP {e.code}: {e.reason}", file=sys.stderr)
        if err_body:
            print(err_body, file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Bland.ai
# ---------------------------------------------------------------------------
def _bland_api_key() -> str:
    return _env_or_config("BLAND_API_KEY", ["bland", "api_key"])


def bland_call(phone_number: str, task: str, voice: str | None = None,
               first_sentence: str | None = None, max_duration: int = 3) -> dict:
    api_key = _bland_api_key()
    if not api_key:
        print("Error: No Bland.ai API key. Set BLAND_API_KEY or add to ~/.hermes/config.yaml under phone.bland.api_key", file=sys.stderr)
        print("Sign up free at https://app.bland.ai", file=sys.stderr)
        sys.exit(1)

    if voice is None:
        voice = _env_or_config("BLAND_DEFAULT_VOICE", ["bland", "default_voice"], BLAND_DEFAULT_VOICE)

    payload = {
        "phone_number": phone_number,
        "task": task,
        "voice": voice,
        "model": BLAND_DEFAULT_MODEL,
        "max_duration": max_duration,
        "record": True,
        "wait_for_greeting": True,
    }
    if first_sentence:
        payload["first_sentence"] = first_sentence

    result = _http("POST", f"{BLAND_API_BASE}/calls",
                    {"Content-Type": "application/json", "authorization": api_key},
                    payload)

    call_id = result.get("call_id")
    if not call_id:
        print(f"Error: Bland.ai returned no call_id: {json.dumps(result)}", file=sys.stderr)
        sys.exit(1)

    return {
        "success": True,
        "provider": "bland",
        "call_id": call_id,
        "phone_number": phone_number,
        "voice": voice,
        "max_duration": max_duration,
        "message": "Call initiated. Use 'status' command to check results.",
    }


def bland_status(call_id: str, analyze: str | None = None) -> dict:
    api_key = _bland_api_key()
    if not api_key:
        print("Error: No Bland.ai API key.", file=sys.stderr)
        sys.exit(1)

    data = _http("GET", f"{BLAND_API_BASE}/calls/{call_id}",
                  {"authorization": api_key})

    result = {
        "success": True,
        "provider": "bland",
        "status": data.get("status"),
        "duration_minutes": data.get("call_length"),
        "answered_by": data.get("answered_by"),
        "transcript": data.get("concatenated_transcript", ""),
        "recording_url": data.get("recording_url"),
    }

    if analyze and data.get("status") == "completed":
        questions = [[q.strip(), "string"] for q in analyze.split(",") if q.strip()]
        if questions:
            try:
                analysis = _http("POST", f"{BLAND_API_BASE}/calls/{call_id}/analyze",
                                 {"Content-Type": "application/json", "authorization": api_key},
                                 {"questions": questions})
                result["analysis"] = analysis
            except Exception as e:
                result["analysis_error"] = str(e)

    return result


# ---------------------------------------------------------------------------
# Vapi
# ---------------------------------------------------------------------------
def _vapi_api_key() -> str:
    return _env_or_config("VAPI_API_KEY", ["vapi", "api_key"])


def _vapi_phone_number_id() -> str:
    return _env_or_config("VAPI_PHONE_NUMBER_ID", ["vapi", "phone_number_id"])


def vapi_call(phone_number: str, task: str, voice_id: str | None = None,
              first_sentence: str | None = None, max_duration: int = 3) -> dict:
    api_key = _vapi_api_key()
    if not api_key:
        print("Error: No Vapi API key. Set VAPI_API_KEY or add to ~/.hermes/config.yaml under phone.vapi.api_key", file=sys.stderr)
        print("Sign up at https://dashboard.vapi.ai", file=sys.stderr)
        sys.exit(1)

    phone_number_id = _vapi_phone_number_id()
    if not phone_number_id:
        print("Error: No Vapi phone number ID. Vapi requires a Twilio number for outbound calls.", file=sys.stderr)
        print("Setup: 1) Sign up at twilio.com  2) Buy a number  3) Import into Vapi  4) Set VAPI_PHONE_NUMBER_ID", file=sys.stderr)
        sys.exit(1)

    voice_provider = _env_or_config("VAPI_VOICE_PROVIDER", ["vapi", "default_voice_provider"], VAPI_DEFAULT_VOICE_PROVIDER)
    if voice_id is None:
        voice_id = _env_or_config("VAPI_VOICE_ID", ["vapi", "default_voice_id"], VAPI_DEFAULT_VOICE_ID)
    model = _env_or_config("VAPI_MODEL", ["vapi", "model"], VAPI_DEFAULT_MODEL)

    assistant = {
        "model": {
            "provider": "openai",
            "model": model,
            "messages": [{"role": "system", "content": task}],
        },
        "voice": {
            "provider": voice_provider,
            "voiceId": voice_id,
        },
        "maxDurationSeconds": max_duration * 60,
    }
    if first_sentence:
        assistant["firstMessage"] = first_sentence

    payload = {
        "phoneNumberId": phone_number_id,
        "customer": {"number": phone_number},
        "assistant": assistant,
    }

    result = _http("POST", f"{VAPI_API_BASE}/call",
                    {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    payload)

    call_id = result.get("id")
    if not call_id:
        print(f"Error: Vapi returned no call id: {json.dumps(result)}", file=sys.stderr)
        sys.exit(1)

    return {
        "success": True,
        "provider": "vapi",
        "call_id": call_id,
        "phone_number": phone_number,
        "voice_provider": voice_provider,
        "voice_id": voice_id,
        "max_duration": max_duration,
        "message": "Call initiated. Use 'status' command to check results.",
    }


def vapi_status(call_id: str) -> dict:
    api_key = _vapi_api_key()
    if not api_key:
        print("Error: No Vapi API key.", file=sys.stderr)
        sys.exit(1)

    data = _http("GET", f"{VAPI_API_BASE}/call/{call_id}",
                  {"Authorization": f"Bearer {api_key}"})

    return {
        "success": True,
        "provider": "vapi",
        "status": data.get("status"),
        "duration_seconds": data.get("duration"),
        "ended_reason": data.get("endedReason"),
        "transcript": data.get("transcript", ""),
        "recording_url": data.get("recordingUrl"),
        "summary": data.get("summary"),
        "cost": data.get("cost"),
    }


# ---------------------------------------------------------------------------
# Diagnose — check config
# ---------------------------------------------------------------------------
def diagnose():
    provider = _get_provider()
    print(f"Phone Call Tool — Diagnostics")
    print("=" * 45)
    print(f"  Active provider: {provider}")

    # Bland
    bland_key = _bland_api_key()
    bland_voice = _env_or_config("BLAND_DEFAULT_VOICE", ["bland", "default_voice"], BLAND_DEFAULT_VOICE)
    print(f"\n  Bland.ai:")
    print(f"    API key:  {'set' if bland_key else 'NOT SET (BLAND_API_KEY)'}")
    print(f"    Voice:    {bland_voice}")

    # Vapi
    vapi_key = _vapi_api_key()
    vapi_phone = _vapi_phone_number_id()
    vapi_voice_provider = _env_or_config("VAPI_VOICE_PROVIDER", ["vapi", "default_voice_provider"], VAPI_DEFAULT_VOICE_PROVIDER)
    vapi_voice_id = _env_or_config("VAPI_VOICE_ID", ["vapi", "default_voice_id"], VAPI_DEFAULT_VOICE_ID)
    vapi_model = _env_or_config("VAPI_MODEL", ["vapi", "model"], VAPI_DEFAULT_MODEL)
    print(f"\n  Vapi:")
    print(f"    API key:      {'set' if vapi_key else 'NOT SET (VAPI_API_KEY)'}")
    print(f"    Phone number: {'set' if vapi_phone else 'NOT SET (VAPI_PHONE_NUMBER_ID)'}")
    print(f"    Voice:        {vapi_voice_provider}:{vapi_voice_id}")
    print(f"    Model:        {vapi_model}")

    print(f"\n  Bland.ai voices:")
    for name, desc in BLAND_VOICES.items():
        print(f"    {name:10s} — {desc}")

    # Ready?
    ready = False
    if provider == "bland" and bland_key:
        ready = True
    elif provider == "vapi" and vapi_key and vapi_phone:
        ready = True
    print(f"\n  Ready: {'YES' if ready else 'NO — configure API keys above'}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        sys.exit(0)

    command = args[0]

    if command == "diagnose":
        diagnose()
        return

    if command == "call":
        if len(args) < 3:
            print("Usage: phone_call.py call <phone_number> <task> [options]", file=sys.stderr)
            sys.exit(1)

        phone_number = args[1]
        task = args[2]

        # Parse optional flags
        voice = None
        first_sentence = None
        max_duration = 3
        i = 3
        while i < len(args):
            if args[i] == "--voice" and i + 1 < len(args):
                voice = args[i + 1]; i += 2
            elif args[i] == "--first-sentence" and i + 1 < len(args):
                first_sentence = args[i + 1]; i += 2
            elif args[i] == "--max-duration" and i + 1 < len(args):
                max_duration = int(args[i + 1]); i += 2
            else:
                print(f"Unknown option: {args[i]}", file=sys.stderr); sys.exit(1)

        if not phone_number.startswith("+"):
            print(f"Error: Phone number must be E.164 format (e.g. +15551234567), got: {phone_number}", file=sys.stderr)
            sys.exit(1)

        provider = _get_provider()
        if provider == "vapi":
            result = vapi_call(phone_number, task, voice_id=voice,
                               first_sentence=first_sentence, max_duration=max_duration)
        else:
            result = bland_call(phone_number, task, voice=voice,
                                first_sentence=first_sentence, max_duration=max_duration)

        print(json.dumps(result, indent=2))

    elif command == "status":
        if len(args) < 2:
            print("Usage: phone_call.py status <call_id> [--analyze 'q1,q2']", file=sys.stderr)
            sys.exit(1)

        call_id = args[1]
        analyze = None
        if len(args) > 2 and args[2] == "--analyze" and len(args) > 3:
            analyze = args[3]

        provider = _get_provider()
        if provider == "vapi":
            result = vapi_status(call_id)
        else:
            result = bland_status(call_id, analyze=analyze)

        print(json.dumps(result, indent=2, ensure_ascii=False))

    else:
        print(f"Unknown command: {command}. Use 'call', 'status', or 'diagnose'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
