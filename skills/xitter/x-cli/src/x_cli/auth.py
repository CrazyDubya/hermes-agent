"""Auth: env var loading, OAuth 1.0a header generation, and OAuth 2.0 PKCE token management."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

TOKEN_URL = "https://api.twitter.com/2/oauth2/token"


@dataclass
class Credentials:
    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str
    bearer_token: str
    oauth2_client_id: str = ""
    oauth2_client_secret: str = ""


@dataclass
class OAuth2Tokens:
    access_token: str
    refresh_token: str
    expires_at: int  # unix ms

    def is_expired(self) -> bool:
        return int(time.time() * 1000) >= (self.expires_at - 60_000)


class OAuth2Manager:
    """Manages OAuth 2.0 tokens for bookmark operations. Auto-refreshes."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._tokens: OAuth2Tokens | None = None
        self._token_path = Path.home() / ".config" / "x-cli" / ".oauth2-tokens.json"

    def _load_tokens(self) -> OAuth2Tokens | None:
        if self._tokens and not self._tokens.is_expired():
            return self._tokens
        if self._token_path.exists():
            data = json.loads(self._token_path.read_text())
            self._tokens = OAuth2Tokens(**data)
            if not self._tokens.is_expired():
                return self._tokens
            # expired — try refresh
            return self._refresh()
        return None

    def _save_tokens(self, tokens: OAuth2Tokens) -> None:
        self._tokens = tokens
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(json.dumps({
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at,
        }, indent=2))

    def _basic_auth_header(self) -> str:
        """Match x-mcp: Basic base64(client_id:client_secret)"""
        import base64 as b64
        raw = f"{self.client_id}:{self.client_secret}"
        return f"Basic {b64.b64encode(raw.encode()).decode()}"

    def _refresh(self) -> OAuth2Tokens | None:
        if not self._tokens:
            return None
        try:
            # Match x-mcp exactly: client_id in body + Basic auth header
            from urllib.parse import urlencode
            body = urlencode({
                "grant_type": "refresh_token",
                "refresh_token": self._tokens.refresh_token,
                "client_id": self.client_id,
            })
            resp = httpx.post(
                TOKEN_URL,
                content=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": self._basic_auth_header(),
                },
                timeout=30.0,
            )
            if resp.status_code != 200:
                # token file is stale, nuke it
                self._token_path.unlink(missing_ok=True)
                self._tokens = None
                return None
            data = resp.json()
            tokens = OAuth2Tokens(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", self._tokens.refresh_token),
                expires_at=int(time.time() * 1000) + data.get("expires_in", 7200) * 1000,
            )
            self._save_tokens(tokens)
            return tokens
        except Exception:
            return None

    def get_access_token(self) -> str:
        tokens = self._load_tokens()
        if not tokens:
            raise RuntimeError(
                "OAuth2 not set up. Run the x-oauth2-setup.py script on a machine "
                "with a browser, then copy .oauth2-tokens.json to ~/.config/x-cli/"
            )
        return tokens.access_token


def load_credentials() -> Credentials:
    """Load credentials from env vars, with .env fallback."""
    # Try ~/.config/x-cli/.env then cwd .env
    config_env = Path.home() / ".config" / "x-cli" / ".env"
    if config_env.exists():
        load_dotenv(config_env)
    load_dotenv()  # cwd .env

    def require(name: str) -> str:
        val = os.environ.get(name)
        if not val:
            raise SystemExit(
                f"Missing env var: {name}. "
                "Set X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET, X_BEARER_TOKEN."
            )
        return val

    return Credentials(
        api_key=require("X_API_KEY"),
        api_secret=require("X_API_SECRET"),
        access_token=require("X_ACCESS_TOKEN"),
        access_token_secret=require("X_ACCESS_TOKEN_SECRET"),
        bearer_token=require("X_BEARER_TOKEN"),
        oauth2_client_id=os.environ.get("X_OAUTH2_CLIENT_ID", ""),
        oauth2_client_secret=os.environ.get("X_OAUTH2_CLIENT_SECRET", ""),
    )


def _percent_encode(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def generate_oauth_header(
    method: str,
    url: str,
    creds: Credentials,
    params: dict[str, str] | None = None,
) -> str:
    """Generate an OAuth 1.0a Authorization header (HMAC-SHA1)."""
    oauth_params = {
        "oauth_consumer_key": creds.api_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": creds.access_token,
        "oauth_version": "1.0",
    }

    # Combine oauth params with any query/body params for signature base
    all_params = {**oauth_params}
    if params:
        all_params.update(params)

    # Also include query string params from the URL
    parsed = urllib.parse.urlparse(url)
    if parsed.query:
        qs_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        for k, v in qs_params.items():
            all_params[k] = v[0]

    # Sort and encode
    sorted_params = sorted(all_params.items())
    param_string = "&".join(f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted_params)

    # Base URL (no query string)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    # Signature base string
    base_string = f"{method.upper()}&{_percent_encode(base_url)}&{_percent_encode(param_string)}"

    # Signing key
    signing_key = f"{_percent_encode(creds.api_secret)}&{_percent_encode(creds.access_token_secret)}"

    # HMAC-SHA1
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()

    oauth_params["oauth_signature"] = signature

    # Build header
    header_parts = ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_parts}"
