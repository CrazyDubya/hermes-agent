---
name: shopify
description: >
  Personal shopping assistant via Shop (shop.app) — search millions of products
  across Shopify and beyond, compare prices, find similar items, track orders,
  manage returns, and re-order past purchases. No auth required for product
  search; OAuth device flow for order/tracking/return/reorder. Works with CLI
  and all messaging platforms.
version: 0.0.28
author: Shopify
license: MIT-0
metadata:
  hermes:
    tags: [shopping, commerce, products, orders, tracking, returns, reorder, shopify, shop.app]
    category: commerce
    requires_toolsets: [terminal]
    homepage: https://shop.app
    upstream: https://shop.app/SKILL.md
---

# Shop Skill (shop.app)

Personal shopping assistant — search, buy, track, return, re-order. Ported
from the canonical `https://shop.app/SKILL.md` so it runs natively inside
Hermes using the `terminal` tool and standard `curl`. No SDK required.

## When to use

The user wants to:
- shop, search products, discover brands, compare prices
- find items similar to something they already have (by variant ID or photo)
- check an order status or tracking (needs auth)
- start a return (needs auth)
- re-order a previous purchase (needs auth)

## How to use (tool mapping)

All endpoints are plain HTTP. Use `terminal` with `curl`:

```
curl -sS "https://shop.app/agents/search?query=wireless+earbuds&limit=10&ships_to=US"
```

Responses are plain-text markdown with products separated by `\n\n---\n\n`.
Parse them as markdown; do not attempt JSON decoding.

For authenticated endpoints (orders, returns, reorder), open the sign-in URL
with `browser_navigate` (if available) or instruct the user to open it
themselves. Never ask the user to paste tokens into chat.

Store `access_token`, `refresh_token`, `device_id`, and `country` in your
working memory for the current turn chain. Do NOT persist them to files,
memory tool, or skill storage — they are ephemeral session credentials.

---

## Product Search (no auth)

**Endpoint:** `GET https://shop.app/agents/search`

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | Yes | — | Search keywords |
| `limit` | int | No | 10 | Results 1–10 |
| `ships_to` | string | No | `US` | ISO 3166 country code (currency + availability) |
| `ships_from` | string | No | — | ISO 3166 origin |
| `min_price` | decimal | No | — | Min price |
| `max_price` | decimal | No | — | Max price |
| `available_for_sale` | int | No | 1 | `1` = in-stock only |
| `include_secondhand` | int | No | 1 | `0` = new only |
| `categories` | string | No | — | Comma-delimited Shopify taxonomy IDs |
| `shop_ids` | string | No | — | Filter to specific shops |
| `products_limit` | int | No | 10 | Variants per product, 1–10 |

**Example:**
```
curl -sS "https://shop.app/agents/search?query=wireless+earbuds&limit=10&ships_to=US"
```

**Fields to extract per product block:**
- **Title** — first line
- **Price + Brand + Rating** — second line (`$PRICE at BRAND — RATING`)
- **Product URL** — line starting with `https://`
- **Image URL** — line starting with `Img: `
- **Product ID** — line starting with `id: `
- **Variant IDs** — in the Variants section, or from the `variant=` query param on the product URL
- **Checkout URL** — line starting with `Checkout: ` — replace `{id}` (may appear URL-encoded as `%7Bid%7D`) with the actual variant ID before use

**No pagination.** For more results, vary the query (synonyms, broader/narrower terms). Up to 3 search rounds.

**Error format:** `# Error\n\nquery is missing (400)` — always plain-text markdown, never JSON.

---

## Find Similar Products

### By variant ID (GET)

```
curl -sS "https://shop.app/agents/search?variant_id=33169831854160&limit=10&ships_to=US"
```

`variant_id` must come from the `variant=` query param of a product URL.
The `id:` field from search results is **not** accepted.

### By image (POST)

Download the image first and base64-encode it. URLs are not accepted.

```bash
B64=$(curl -sS "IMAGE_URL" | base64 -w0)
curl -sS -X POST "https://shop.app/agents/search" \
  -H "Content-Type: application/json" \
  -d "{\"similarTo\":{\"media\":{\"contentType\":\"image/jpeg\",\"base64\":\"$B64\"}},\"limit\":10}"
```

---

## Authentication (OAuth Device Flow, RFC 8628)

Only needed for orders, tracking, returns, and reorder. Product search does
NOT require auth. The code is always 8 uppercase characters (A–Z) in
`XXXXXXXX` format. No `client_secret`, no localhost callback.

### Flow

1. **Request a device code:**
   ```bash
   curl -sS -X POST "https://shop.app/agents/auth/device-code"
   ```
   Response includes `device_code`, `user_code`, `sign_in_url`, `interval`, `expires_in`.

2. **Present the sign-in URL to the user.** On the CLI, print it plainly and
   ask the user to open it in a browser. On a messaging platform, send the
   URL as a plain-text message. If `browser_navigate` is enabled, you may
   also open the URL directly for desktop users.

3. **Poll the token endpoint** every `interval` seconds:
   ```bash
   curl -sS -X POST "https://shop.app/agents/auth/token" \
     -d "grant_type=urn:ietf:params:oauth:grant-type:device_code&device_code=<device_code>"
   ```
   Handle responses:
   - `authorization_pending` — user hasn't approved yet, keep polling
   - `slow_down` — add 5 seconds to the interval
   - `expired_token` / `access_denied` — restart device flow
   - Success — store `access_token` and `refresh_token` in working memory

4. **Validate the token:**
   ```bash
   curl -sS "https://shop.app/agents/auth/userinfo" \
     -H "Authorization: Bearer $ACCESS_TOKEN"
   ```
   Returns `sub`, `email`, `name`, `picture`.

5. **Refresh on 401:**
   ```bash
   curl -sS -X POST "https://shop.app/agents/auth/token" \
     -d "grant_type=refresh_token&refresh_token=$REFRESH_TOKEN"
   ```
   If refresh fails, restart the device flow.

### Session state (working memory only)

| Key | When set | Lifetime | Description |
|---|---|---|---|
| `access_token` | After successful auth | Until expired/401 | Bearer token |
| `refresh_token` | After successful auth | Until refresh fails | Renews `access_token` |
| `device_id` | First authenticated request | Current session | `shop-skill--<uuid>` — generate once, reuse |
| `country` | First search | Current session | ISO country code (infer from the user if possible) |

**Never** write tokens to disk, memory tool, or skill state. **Never** ask
the user to paste tokens into chat. Tokens are discarded when the turn
chain ends.

---

## Orders

> Order data covers **all stores** (not just Shopify) — Shop aggregates from
> email receipts the user connects in their Shop app account.

**Status progression:** `paid → fulfilled → in_transit → out_for_delivery → delivered`
**Other:** `attempted_delivery`, `refunded`, `cancelled`, `buyer_action_required`

### Fetch orders

```bash
curl -sS "https://shop.app/agents/orders?limit=50" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "x-device-id: $DEVICE_ID"
```

| Parameter | Default | Description |
|---|---|---|
| `limit` | 20 | Results 1–50 |
| `cursor` | — | Pagination cursor from previous response |

**Fields per order block:**
- **Order UUID** — `uuid:`
- **Store** — `at <store>`, `Store domain:`, `Store URL:`
- **Price** — line after Store URL (e.g. `98.00 USD`)
- **Date** — `Ordered:`
- **Status / delivery** — `Status:`, `Delivery:`
- **Reorder eligibility** — `Can reorder: yes`
- **Items** — under `— Items —`, each may have `[product:ID]`, `[variant:ID]`, `Img:`
- **Tracking** — under `— Tracking —` with tracking URL, carrier, code
- **Tracker ID** — `tracker_id:` (standalone trackers)
- **Return URL** — `Return URL:` (if eligible)

**Pagination:** If the first line is `cursor:<value>`, pass `?cursor=<value>` on the next call. Keep fetching until no cursor line appears.

**Filtering:** Client-side after fetch — by `Ordered:` date, `Delivery:` status, or text match on items / store name.

**On 401:** refresh the token and retry.
**On 429:** wait 10s and retry.

### Order detail & tracking

Use the fetch pattern with `limit=50`, find by `uuid:`. Tracking is under the `— Tracking —` section:

```
delivered via UPS — 1Z999AA10123456784
Tracking URL: https://ups.com/track?num=...
ETA: Arrives Tuesday
```

If `Ordered:` is months old but the delivery status is still `in_transit`, tell the user the tracking may be stale.

---

## Returns

Two sources:

**1. Order-level return URLs** — already in the order fetch response:
```
Return URL: https://store.com/returns/start
Status page: https://store.com/orders/status
```

**2. Product-level return policy:**
```bash
curl -sS "https://shop.app/agents/returns?product_id=29923377167" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "x-device-id: $DEVICE_ID"
```

Returns `Returnable` (yes/no/unknown), `Return window` (days), `Return policy URL`, `Shipping policy URL`.

If `Returnable: yes`, mention the window. For the full policy text, fetch the Return policy URL (HTML — strip tags before showing).

---

## Reorder

1. Fetch orders with `limit=50`, find the target by `uuid:`.
2. Confirm `Can reorder: yes`.
3. Extract `[variant:ID]` and item title from `— Items —`.
4. Get the domain from `Store domain:` or `Store URL:`.
5. Build the checkout URL: `https://{domain}/cart/{variant_id}:{quantity}`.

**Example:** `at Allbirds` + `Store domain: allbirds.myshopify.com` + `[variant:789012]` → `https://allbirds.myshopify.com/cart/789012:1`.

**Skipped items:** If a line item has no `[variant:ID]` (Amazon orders, etc.), give a search link instead: `https://{domain}/search?q={title}`.

---

## Build a checkout URL

Pattern: `https://{store}/cart/{variant_id}:{qty},{variant_id}:{qty}?checkout[email]=...`

| Pre-fill | Description |
|---|---|
| `email` | Only use info the user already told you |
| `city` | Same |
| `country` | Same |

- **Default:** link the product page so the user can browse.
- **"Buy now":** the checkout URL with variant ID.
- **Multi-item same store:** combine into one `items` array.
- **Multi-store:** separate checkout calls per store; tell the user.
- **Never imply the purchase is complete.** Payment happens on the store's site.

The `Checkout:` line in search results has `{id}` as a placeholder — replace it with the real variant ID before showing.

---

## Store policies

When a product ID isn't handy, fetch directly from the store:
```bash
curl -sS "https://$SHOP_DOMAIN/policies/shipping-policy"
curl -sS "https://$SHOP_DOMAIN/policies/refund-policy"
```
Returns HTML. Strip tags before presenting.

---

## Virtual Try-On & Visualization

**This is a killer feature. Offer it when `image_generate` is available.**

Offer to visualize products using the user's photo or a room photo:
- Clothing / shoes / accessories → virtual try-on with the user's photo
- Furniture / decor → place in the user's room photo
- Art / prints → preview on the user's wall

Mention it **once**, the first time the user searches one of those
categories. Example: "Want to see how any of these would look on you?
Send a photo and I'll render it." Results are approximate — inspiration, not
exact representation.

---

## How to be an A+ shopping bot

Lead with products, not narration.

### Search strategy
1. **Search broadly.** Vary terms, try synonyms, mix category + brand. Use filters (`min_price`, `max_price`, `ships_to`, `categories`) when they apply.
2. **Evaluate.** Aim for 8–10 results across price points, brands, styles. Re-search with different queries if thin. Up to 3 rounds. No pagination — different keywords, not "page 2".
3. **Organize** into 2–4 themes (use case, price tier, style, type).
4. **Present** 3–6 products per group with the required fields (see Formatting).
5. **Recommend** 1–2 standouts with specific reasons ("4.8 stars across 2,000+ reviews").
6. **Ask one** follow-up question that moves toward a decision.

**Discovery requests** ("earbuds for running"): search immediately, don't interview first.
**Refinement** ("under $50", "in blue"): acknowledge, present matches, re-search if thin.
**Comparisons:** lead with the key trade-off, put specs side-by-side, give a situational pick.

**Weak results?** Try broader terms, strip adjectives, go to category level, try brand names, or split compound queries. Example: `dimmable vintage bulbs e27` → `vintage edison bulbs` → `e27 dimmable bulbs` → `filament bulbs`.

### Order lookup strategy
1. Fetch broadly (`limit=50`).
2. Scan for matching store name (`at <store>`) or title under `— Items —`. Match loosely — "Yoto" matches "Yoto Ltd".
3. Act: tracking → `— Tracking —` section; returns → `/agents/returns`; reorder → build cart URL.
4. No match → paginate with `cursor:`, or ask the user for more details.

| User says | Strategy |
|---|---|
| "Where's my Yoto order?" | Fetch 50 → find "Yoto" → show tracking |
| "Show me recent orders" | Fetch 20 (default) |
| "Return the shoes from January?" | Fetch 50 → filter by `Ordered:` January → check returns |
| "Reorder the coffee" | Fetch 50 → find coffee → build checkout URL |
| "Did I order one of these before?" | Fetch 50 → match against the current search's products |

---

## Formatting (Hermes-native)

For every product, include:
- Product image (see platform section below)
- Product name with brand
- Price (local currency where available, ranges when min ≠ max)
- Rating + review count
- One-sentence differentiator from actual product data
- Available options summary ("6 colors, sizes S–XXL")
- Product page URL (always shown so the user can browse)
- Buy Now checkout URL (always shown — built from variant ID using the `Checkout:` pattern)

For orders:
- Summarize naturally — don't paste raw data
- Highlight ETAs for in-transit, delivery dates for delivered
- Offer follow-ups: "Want tracking details?", "Want to re-order?"
- Remember: order data covers all stores in the user's Shop account, not just Shopify

### Platform delivery in Hermes

Hermes delivers images consistently across surfaces — you don't call a
separate message tool per platform. Drop image paths/URLs inline and the
gateway handles per-platform rendering.

**Messaging gateway (Telegram, Discord, Slack, WhatsApp, iMessage, Signal, etc.):**
To attach a product image, download it to a temp file and emit a `MEDIA:`
tag on its own line in your response. The gateway intercepts `MEDIA:<path>`
and delivers it as a native image attachment.

```bash
IMG_PATH=$(mktemp --suffix=.jpg)
curl -sS "$IMAGE_URL" -o "$IMG_PATH"
```

Then in the response text:
```
MEDIA:/tmp/tmpXXXX.jpg

**Brand Product Name** — $49.99 · ⭐ 4.6 (1,200)
Wireless earbuds with 8-hour battery and deep bass. 4 colors.
Product: https://store.com/product
Buy now: https://store.com/cart/ID:1
```

**CLI:** No media channel — just include the `Img:` URL as plain text in the
output. Terminals that support image protocols (kitty, iTerm2) render
automatically; others show the URL, which the user can click.

**General rules for all surfaces:**
- Never fabricate URLs or specs.
- One image per product is enough; don't flood.
- Keep blurbs short — price, rating, one differentiator, options summary, two links.
- On messaging platforms, don't paste raw CDN URLs in the text body — use `MEDIA:` for the image, plain URLs only for product/buy-now links.

---

## Rules

- Use what you already know (country, size, style, budget) — don't re-ask.
- Never fabricate URLs, specs, prices, or reviews.
- Never imply a purchase happened. Payment is always on the store's site.
- Tokens live in working memory only. Never persist, never show.
- Order data covers all stores in the user's Shop account (email receipts), not only Shopify stores.
