# xitter

X/Twitter skill for Hermes Agent, powered by [x-cli](https://github.com/Infatoshi/x-cli).

## Credits

The bundled `x-cli/` is a patched fork of **Infatoshi's** work:

- **x-cli** (CLI tool): https://github.com/Infatoshi/x-cli
- **x-mcp** (MCP server): https://github.com/Infatoshi/x-mcp

The patch adds OAuth 2.0 PKCE support for the X Bookmarks API, adapting
the token exchange and refresh flow from x-mcp's `oauth2.ts` into x-cli's
Python `OAuth2Manager`.

## What's Changed from Upstream

- `auth.py`: Added `OAuth2Manager` class with PKCE token refresh
- `api.py`: Added `_oauth2_request()` for bookmark endpoints (`get_bookmarks`, `bookmark_tweet`, `unbookmark_tweet`)
- `cli.py`: Added `me bookmarks`, `me bookmark`, `me unbookmark` commands

Everything else (OAuth 1.0a signing, Bearer token auth, formatters, utils) is upstream as-is.
