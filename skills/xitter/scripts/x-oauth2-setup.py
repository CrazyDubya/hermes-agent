#!/usr/bin/env python3
"""
One-time OAuth2 PKCE setup for X/Twitter bookmarks.
Run this on a machine where you have a browser and are logged into X.

Usage:
  uv run x-oauth2-setup.py

It will ask for your Client ID and Client Secret, open your browser,
and save the tokens automatically.

To get Client ID + Secret:
  1. Go to https://developer.x.com/en/portal/dashboard
  2. Click your app -> "Keys and tokens" tab
  3. Under "OAuth 2.0 Client ID and Client Secret" -> generate/copy both
  4. Make sure your app has "Read and Write" permissions
  5. Under "User authentication settings":
     - Type: "Web App, Automated App or Bot"
     - Callback URL: http://127.0.0.1:3219/callback
     - Website URL: anything (e.g. https://example.com)
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///

import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
REDIRECT_URI = "http://127.0.0.1:3219/callback"
SCOPES = "bookmark.read bookmark.write tweet.read users.read offline.access"

HERMES_ENV = Path.home() / ".hermes" / ".env"
TOKEN_FILE = Path.home() / ".config" / "x-cli" / ".oauth2-tokens.json"

# PKCE: generate verifier + challenge
code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
code_challenge = (
    base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
    .rstrip(b"=")
    .decode()
)
state = secrets.token_urlsafe(16)

received_code = None
cid = None
csecret = None


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    """Match x-mcp: Basic base64(client_id:client_secret)"""
    raw = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(raw.encode()).decode()
    return f"Basic {encoded}"


def _append_to_hermes_env(key: str, value: str) -> None:
    """Append a key=value to ~/.hermes/.env if not already present."""
    HERMES_ENV.parent.mkdir(parents=True, exist_ok=True)

    if HERMES_ENV.exists():
        content = HERMES_ENV.read_text()
        for line in content.splitlines():
            if line.strip().startswith(f"{key}="):
                # Already present — update in place
                lines = content.splitlines()
                new_lines = []
                for l in lines:
                    if l.strip().startswith(f"{key}="):
                        new_lines.append(f"{key}={value}")
                    else:
                        new_lines.append(l)
                HERMES_ENV.write_text("\n".join(new_lines) + "\n")
                return

    with open(HERMES_ENV, "a") as f:
        f.write(f"{key}={value}\n")


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global received_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/callback":
            recv_state = params.get("state", [None])[0]
            if recv_state != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"State mismatch! CSRF detected. Try again.")
                return

            if "code" in params:
                received_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"""
                <html><body style="font-family:monospace;text-align:center;padding:60px;background:#111;color:#0f0">
                <h1>authorized. go back to your terminal.</h1>
                <p>you can close this tab.</p>
                </body></html>
                """)
            else:
                error = params.get("error", ["unknown"])[0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Auth failed: {error}".encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    global cid, csecret

    print("=" * 50)
    print("X/Twitter OAuth2 PKCE Setup")
    print("=" * 50)
    print()
    print("This gets you a refresh token for bookmark access.")
    print("You only need to do this once.")
    print()

    cid = input("Client ID: ").strip()
    csecret = input("Client Secret: ").strip()

    if not cid or not csecret:
        print(
            "Both are required. Get them from https://developer.x.com/en/portal/dashboard"
        )
        return

    # Build auth URL
    params = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    url = f"{AUTH_URL}?{urlencode(params)}"

    server = HTTPServer(("127.0.0.1", 3219), CallbackHandler)
    server.timeout = 120

    print()
    print("Opening browser for authorization...")
    print(f"If it doesn't open, go to:\n{url}")
    print()

    webbrowser.open(url)

    while received_code is None:
        server.handle_request()

    server.server_close()
    print("Got authorization code. Exchanging for tokens...")

    import httpx

    token_body = urlencode(
        {
            "grant_type": "authorization_code",
            "code": received_code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
            "client_id": cid,
        }
    )

    resp = httpx.post(
        TOKEN_URL,
        content=token_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": _basic_auth_header(cid, csecret),
        },
        timeout=30.0,
    )

    if resp.status_code != 200:
        print(f"Token exchange failed: {resp.status_code}")
        print(resp.text)
        return

    data = resp.json()
    expires_at = int(time.time() * 1000) + data.get("expires_in", 7200) * 1000

    result = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": expires_at,
    }

    # Save tokens to ~/.config/x-cli/.oauth2-tokens.json
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(result, indent=2))
    print(f"\nTokens saved to {TOKEN_FILE}")

    # Save client credentials to ~/.hermes/.env
    _append_to_hermes_env("X_OAUTH2_CLIENT_ID", cid)
    _append_to_hermes_env("X_OAUTH2_CLIENT_SECRET", csecret)
    print(f"Client credentials saved to {HERMES_ENV}")

    print()
    print("=" * 50)
    print("SUCCESS!")
    print("=" * 50)
    print()
    print("OAuth2 is fully configured. Bookmarks are ready to use.")
    print("The hourly token refresh cron will keep your tokens alive.")


if __name__ == "__main__":
    main()
