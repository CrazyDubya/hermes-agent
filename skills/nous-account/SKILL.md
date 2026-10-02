---
name: nous-account
description: Use Hermes's stored Nous Portal OAuth access token to call portal.nousresearch.com OAuth/account endpoints. By default, pretty-print account/subscription/balance info for users; return raw JSON unchanged only when they explicitly ask for JSON/raw output.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [nous, oauth, portal, account, subscription, auth.json, raw-json]
    related_skills: [hermes-agent]
---

# Nous Portal OAuth API

Use this skill when you need to call a Nous Portal API endpoint that expects a **Bearer OAuth access token**, especially:

- `https://portal.nousresearch.com/api/oauth/account`
- other `portal.nousresearch.com/api/oauth/*` endpoints

## Key Rule

For **Nous Portal** OAuth endpoints, use the stored **`access_token`** from Hermes auth storage.

Do **not** use:

- the `agent_key` (`sk-...`) — that is for inference API usage, not portal OAuth endpoints
- browser/web extraction without auth headers — those return `{"error":"invalid_token"...}`

## Workflow

1. **Verify Hermes is logged into Nous**

   Run:

   ```bash
   hermes status
   ```

   Look for:

   - `Provider: Nous Portal`
   - `Nous Portal ✓ logged in`
   - auth file path if shown

2. **Find the auth file**

   Common locations:

   - `~/.hermes/auth.json`
   - `/opt/data/auth.json` in hosted/containerized deployments

   In this environment, `hermes status` showed:

   - auth file: `/opt/data/auth.json`

3. **Read the token from auth.json**

   Structure:

   ```json
   {
     "providers": {
       "nous": {
         "access_token": "...",
         "refresh_token": "...",
         "expires_at": "...",
         "agent_key": "sk-..."
       }
     }
   }
   ```

   Use `providers.nous.access_token`.

4. **Call the endpoint with the Authorization header**

   Prefer Python stdlib if `curl` is unavailable or inconvenient:

   ```bash
   python3 - <<'PY'
   import json, urllib.request
   from pathlib import Path

   auth = json.loads(Path('/opt/data/auth.json').read_text())
   token = auth['providers']['nous']['access_token']

   req = urllib.request.Request(
       'https://portal.nousresearch.com/api/oauth/account',
       headers={
           'Authorization': f'Bearer {token}',
           'Accept': 'application/json',
       },
   )

   with urllib.request.urlopen(req, timeout=30) as r:
       print(r.read().decode())
   PY
   ```

5. **Choose output style based on the user's request**

   - If the user explicitly asks for **JSON**, **raw JSON**, **exact response**, or says **don't parse it**, return the response body unchanged.
   - Otherwise, **pretty-print the useful fields** in a short human-friendly summary.

   Default pretty-print format:

   - Plan
   - Tier
   - Current period end
   - Credits remaining
   - Rollover credits
   - Purchased credits remaining

   Keep the pretty-printed response concise unless the user asks for more detail.

## Pitfalls

- `web_extract` and browser navigation do **not** automatically inject the Nous OAuth token.
- A plain request to the endpoint returns:

  ```json
  {"error":"invalid_token","error_description":"Missing or invalid Authorization header"}
  ```

- `agent_key` and `access_token` are different credentials for different services.
- `access_token` is short-lived. If it is expired, refresh by re-authenticating Hermes:

  ```bash
  hermes login --provider nous
  ```

  Then re-read `auth.json` and retry.

## Verification

A successful call to `/api/oauth/account` returns JSON such as:

```json
{"subscription":{"plan":"Max","tier":4,"current_period_end":"...","credits_remaining":...},"purchased_credits_remaining":...}
```

## Decision Table

- **User wants raw account/subscription JSON** → fetch with `access_token`, return exact body
- **User wants account/subscription/balance info without asking for JSON** → fetch with `access_token`, pretty-print the useful fields
- **User wants just balance/credits** → fetch same endpoint, then summarize requested fields
- **User wants inference access** → use `agent_key`, not `access_token`
