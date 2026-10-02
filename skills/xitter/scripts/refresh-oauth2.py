#!/usr/bin/env python3
"""
Refresh X/Twitter OAuth2 tokens. Intended to run as an hourly cron job.

Access tokens expire every 2h. This script refreshes them proactively
so bookmark operations never hit an expired token.

Exit codes:
  0 — tokens refreshed or still valid
  1 — refresh token is dead (user must re-run x-oauth2-setup.py)

Usage:
  uv run refresh-oauth2.py
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///

import base64
import json
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
import os

TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
HERMES_ENV = Path.home() / ".hermes" / ".env"
TOKEN_FILE = Path.home() / ".config" / "x-cli" / ".oauth2-tokens.json"
EXPIRY_BUFFER_MS = 60_000  # refresh 60s before actual expiry


def main() -> int:
    # Load credentials from ~/.hermes/.env
    if HERMES_ENV.exists():
        load_dotenv(HERMES_ENV)

    client_id = os.environ.get("X_OAUTH2_CLIENT_ID", "")
    client_secret = os.environ.get("X_OAUTH2_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("ERROR: X_OAUTH2_CLIENT_ID and X_OAUTH2_CLIENT_SECRET not found in ~/.hermes/.env")
        return 1

    # Load tokens
    if not TOKEN_FILE.exists():
        print("ERROR: No token file at ~/.config/x-cli/.oauth2-tokens.json")
        print("Run x-oauth2-setup.py first.")
        return 1

    tokens = json.loads(TOKEN_FILE.read_text())
    now_ms = int(time.time() * 1000)

    # Check if still valid (with 60s buffer)
    if now_ms < (tokens["expires_at"] - EXPIRY_BUFFER_MS):
        remaining_min = (tokens["expires_at"] - now_ms) / 60_000
        print(f"OK: token still valid ({remaining_min:.0f}min remaining)")
        return 0

    # Refresh
    print("Token expired or expiring soon. Refreshing...")

    raw = f"{client_id}:{client_secret}"
    basic_auth = f"Basic {base64.b64encode(raw.encode()).decode()}"

    from urllib.parse import urlencode
    body = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client_id,
    })

    try:
        resp = httpx.post(
            TOKEN_URL,
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": basic_auth,
            },
            timeout=30.0,
        )
    except Exception as e:
        print(f"ERROR: network request failed: {e}")
        return 1

    if resp.status_code != 200:
        print(f"ERROR: refresh failed with status {resp.status_code}")
        print(resp.text)
        if resp.status_code == 401:
            print("\nRefresh token is dead. Re-run x-oauth2-setup.py to get new tokens.")
            TOKEN_FILE.unlink(missing_ok=True)
        return 1

    data = resp.json()
    new_tokens = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", tokens["refresh_token"]),
        "expires_at": int(time.time() * 1000) + data.get("expires_in", 7200) * 1000,
    }

    TOKEN_FILE.write_text(json.dumps(new_tokens, indent=2))
    print("OK: tokens refreshed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
