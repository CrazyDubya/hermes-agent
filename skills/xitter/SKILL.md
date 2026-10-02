---
name: xitter
description: "Post tweets, read timelines, search, bookmark, and engage on X/Twitter via the x-cli command-line tool. Use this skill whenever the user wants to interact with X/Twitter — posting, reading timelines, searching tweets, managing bookmarks, liking, retweeting, looking up users, or checking mentions. Also trigger when the user mentions 'tweet', 'X', 'Twitter', or any social media posting task targeting X."
version: 1.0.0
author: alt-glitch (x-cli upstream: Infatoshi)
platforms: [linux, macos]
metadata:
  hermes:
    tags: [twitter, x, social-media]
    requires_toolsets: [terminal]
---

# Xitter — X/Twitter CLI Skill

Interact with X/Twitter through `x-cli`, a Python CLI that talks directly to the X API v2. Supports posting, reading, searching, engagement, and bookmarks.

> **Pay-per-use X API tier required.** The free tier does not support most endpoints and returns misleading 403 errors. You need at least the Basic tier ($200/month) from https://developer.x.com/en/portal/products

## When to Use

Any X/Twitter task:
- Posting tweets, replies, quote tweets, polls
- Reading timelines, mentions, user profiles
- Searching tweets
- Managing bookmarks (add, remove, list)
- Liking and retweeting
- Looking up followers/following

## Setup

If `x-cli` is not installed or the user hasn't configured credentials yet, walk them through this setup. Each step has direct links — give them to the user verbatim.

### Step 1: Install x-cli

Install from the bundled source in this skill directory:

```bash
uv tool install <SKILL_DIR>/x-cli/
```

Replace `<SKILL_DIR>` with the absolute path to this skill's directory.

Verify: `x-cli --help` should show the command list.

### Step 2: Create an X Developer App

Direct the user to: **https://developer.x.com/en/portal/dashboard**

1. Sign in with their X account
2. If no developer account exists, sign up (free tier exists but **pay-per-use is required** for API access — see note above)
3. Go to **Apps** in the left sidebar → **Create App**
4. Enter any app name (e.g. `hermes-xitter`)
5. After creation, three credentials appear on screen:
   - **API Key** (Consumer Key) → this is `X_API_KEY`
   - **API Secret** (Consumer Secret) → this is `X_API_SECRET`
   - **Bearer Token** → this is `X_BEARER_TOKEN`

**Tell the user to save all three immediately. The secret won't be shown again.**

### Step 3: Enable Write Permissions

Without this, posting/liking/retweeting fails with a 403 error.

On the app's page in the developer portal:
1. Scroll to **User authentication settings** → click **Set up**
2. Set these values:
   - **App permissions**: **Read and write** (NOT just Read)
   - **Type of App**: **Web App, Automated App or Bot**
   - **Callback URI / Redirect URL**: `http://127.0.0.1:3219/callback`
   - **Website URL**: `https://example.com` (any valid URL)
3. Click **Save**

It will show an OAuth 2.0 Client Secret — save it for Step 6.

### Step 4: Generate Access Token & Secret

**This MUST be done AFTER Step 3.** If tokens existed before enabling write perms, they must be regenerated.

1. Go to the app's **Keys and Tokens** page: **https://developer.x.com/en/portal/dashboard** → click app → **Keys and tokens** tab
2. Under **Access Token and Secret** → click **Generate** (or **Regenerate**)
3. Save both:
   - **Access Token** → `X_ACCESS_TOKEN`
   - **Access Token Secret** → `X_ACCESS_TOKEN_SECRET`
4. **Verify** the Access Token section shows **"Read and Write"**, not just "Read"

### Step 5: Save Credentials

Append these 5 variables to `~/.hermes/.env`:

```bash
X_API_KEY=<API Key from Step 2>
X_API_SECRET=<API Secret from Step 2>
X_BEARER_TOKEN=<Bearer Token from Step 2>
X_ACCESS_TOKEN=<Access Token from Step 4>
X_ACCESS_TOKEN_SECRET=<Access Token Secret from Step 4>
```

Test with: `x-cli me mentions` — should return recent mentions (or an empty list).

### Step 6: OAuth2 PKCE Setup (for Bookmarks)

Bookmarks use a separate OAuth 2.0 flow. This step requires a browser.

**If running over SSH**: The setup script starts a local callback server on `127.0.0.1:3219`. For the browser redirect to reach the remote machine, the user must set up SSH port forwarding first:

```bash
ssh -L 3219:127.0.0.1:3219 <user>@<host>
```

Then they can open the printed URL in their local browser and the callback will tunnel through. If they're already in an SSH session, they can add the tunnel from another terminal.

**If running natively on Mac/Linux**: The script will open the browser automatically. No extra steps needed.

1. In the developer portal (**https://developer.x.com/en/portal/dashboard** → app → **Keys and tokens** tab), find **OAuth 2.0 Client ID and Client Secret**. Generate them if they don't exist yet.
2. Run the setup script:

```bash
uv run <SKILL_DIR>/scripts/x-oauth2-setup.py
```

3. It will ask for Client ID and Client Secret, open the browser (or print the URL if no browser is available) for authorization, then automatically:
   - Save `X_OAUTH2_CLIENT_ID` and `X_OAUTH2_CLIENT_SECRET` to `~/.hermes/.env`
   - Save tokens to `~/.config/x-cli/.oauth2-tokens.json`

Test with: `x-cli me bookmarks` — should return bookmarked tweets.

### Step 7: Token Refresh Cron

OAuth2 access tokens expire every 2 hours. Set up an hourly cron to keep them alive:

Create a hermes scheduled task:
- **Schedule**: every 1 hour
- **Command**: `uv run <SKILL_DIR>/scripts/refresh-oauth2.py`
- **Delivery**: local (silent on success)

If the refresh token itself dies (~6 months or revocation), the script exits with code 1 and prints a message. The user will need to re-run `x-oauth2-setup.py`.

## Command Reference

### Tweet Commands (`x-cli tweet <action>`)

| Command | Args | Flags | Description |
|---------|------|-------|-------------|
| `post` | `TEXT` | `--poll OPTIONS` `--poll-duration MINS` | Post a tweet (optionally with poll) |
| `get` | `ID_OR_URL` | | Fetch a tweet with metadata |
| `delete` | `ID_OR_URL` | | Delete a tweet |
| `reply` | `ID_OR_URL` `TEXT` | | Reply to a tweet (restricted — see Pitfalls) |
| `quote` | `ID_OR_URL` `TEXT` | | Quote-retweet a tweet |
| `search` | `QUERY` | `--max N` | Search recent tweets (last 7 days) |
| `metrics` | `ID_OR_URL` | | Get engagement metrics |

### User Commands (`x-cli user <action>`)

| Command | Args | Flags | Description |
|---------|------|-------|-------------|
| `get` | `USERNAME` | | Look up a user profile |
| `timeline` | `USERNAME` | `--max N` | Get a user's recent posts |
| `followers` | `USERNAME` | `--max N` | List a user's followers |
| `following` | `USERNAME` | `--max N` | List who a user follows |

### Self Commands (`x-cli me <action>`)

| Command | Args | Flags | Description |
|---------|------|-------|-------------|
| `mentions` | | `--max N` | Your recent mentions |
| `bookmarks` | | `--max N` | Your bookmarks (OAuth2) |
| `bookmark` | `ID_OR_URL` | | Bookmark a tweet (OAuth2) |
| `unbookmark` | `ID_OR_URL` | | Remove a bookmark (OAuth2) |

### Top-Level Commands

| Command | Args | Description |
|---------|------|-------------|
| `like` | `ID_OR_URL` | Like a tweet |
| `retweet` | `ID_OR_URL` | Retweet a tweet |

### Output Flags

All commands accept these flags (placed before the subcommand, e.g. `x-cli -j user get ...`):
- `-j` / `--json` — Raw JSON output (add `-v` for full response including `includes` and `meta`)
- `-p` / `--plain` — TSV format for piping
- `-md` / `--markdown` — Markdown tables/headings
- `-v` / `--verbose` — Include timestamps, metrics, metadata, pagination tokens
- Default: TSV (`-p`) — agent-friendly tab-separated output. Use `-j` when you need structured data for parsing.

### Search Query Syntax

The `search` command supports X's full query language:
- `from:username` — posts by a user
- `to:username` — replies to a user
- `#hashtag` — hashtag search
- `"exact phrase"` — exact match
- `has:media` / `has:links` / `has:images`
- `is:reply` / `-is:retweet`
- `lang:en` — language filter
- Combine with spaces (AND) or `OR`

## Auth Architecture

x-cli uses three auth methods depending on the endpoint:

| Method | Endpoints | Credentials |
|--------|-----------|-------------|
| **Bearer Token** | Public reads: `get_tweet`, `search`, `get_user`, `get_timeline`, `get_followers`, `get_following` | `X_BEARER_TOKEN` |
| **OAuth 1.0a** | Writes + authenticated reads: `post`, `delete`, `like`, `retweet`, `reply`, `quote`, `mentions`, `metrics` | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` |
| **OAuth 2.0 PKCE** | Bookmarks only: `bookmarks`, `bookmark`, `unbookmark` | `X_OAUTH2_CLIENT_ID`, `X_OAUTH2_CLIENT_SECRET` + token file |

## Credential Locations

| What | Where | Written by |
|------|-------|-----------|
| API keys (7 vars) | `~/.hermes/.env` | User (Steps 2-5) + setup script (Step 6) |
| OAuth2 tokens | `~/.config/x-cli/.oauth2-tokens.json` | `x-oauth2-setup.py`, then auto-refreshed by cron |

## Pitfalls

**Pay-per-use API required**: The free tier returns 403 errors on most endpoints. The error message says "oauth1-permissions" which is misleading — the real issue is the API tier. Basic tier costs $200/month.

**403 "oauth1-permissions"**: If you're on the right tier and still get this, the Access Token was generated before write permissions were enabled. Fix: go to the app's User Authentication Settings, confirm "Read and write" is set, then **regenerate** the Access Token and Secret.

**Reply restrictions**: Since Feb 2024, X restricts programmatic replies. `x-cli tweet reply` only works if the original tweet's author @mentioned you or quoted your post. For everything else, use `x-cli tweet quote` instead.

**OAuth2 token expiry**: Access tokens last 2 hours. The hourly cron (Step 7) handles this. If the cron isn't running, `x-cli me bookmarks` will fail with a RuntimeError. The refresh token itself lasts ~6 months — if it dies, re-run `x-oauth2-setup.py`.

**Rate limits**: X API has per-endpoint rate limits. When hit, the error includes a reset timestamp. Wait until then.
