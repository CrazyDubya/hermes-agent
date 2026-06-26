"""`hermes cache stats` — prompt-cache hit telemetry (D6).

Reads from cost_events (already capturing cache_read / cache_write tokens
via D1) and reports hit-rate, tokens saved, and an estimated USD savings
per model.

Estimated savings assume the cache_read tokens would otherwise have been
billed at the full input rate. USD figures use the same pricing surface
as `hermes insights` (agent.usage_pricing.get_pricing_entry).
"""
from __future__ import annotations

import sys
import time
from decimal import Decimal
from typing import Optional

from hermes_state import SessionDB


def _parse_since(spec: str) -> float:
    s = spec.strip().lower()
    if not s:
        return 0.0
    unit = s[-1]
    try:
        n = float(s[:-1])
    except ValueError:
        raise SystemExit(f"cache: invalid --since value '{spec}'")
    factor = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}.get(unit)
    if factor is None:
        raise SystemExit(f"cache: invalid --since unit '{unit}'")
    return time.time() - n * factor


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_pct(n: float) -> str:
    return f"{n * 100:.1f}%"


def _fmt_usd(v: float) -> str:
    if v == 0.0:
        return "$0.00"
    if v < 0.01:
        return f"${v:.4f}"
    return f"${v:.2f}"


def _estimate_saved_usd(
    model: str, cache_read_tokens: int, provider: Optional[str] = None
) -> Optional[float]:
    """USD that would have been charged if cache_read tokens were billed as input."""
    if cache_read_tokens <= 0:
        return 0.0
    try:
        from agent.usage_pricing import get_pricing_entry
    except Exception:
        return None
    entry = get_pricing_entry(model or "", provider=provider)
    if entry is None or entry.input_cost_per_million is None:
        return None
    input_per_m = entry.input_cost_per_million or Decimal("0")
    cache_per_m = entry.cache_read_cost_per_million or Decimal("0")
    delta = input_per_m - cache_per_m
    if delta <= 0:
        return 0.0
    saved = (Decimal(cache_read_tokens) / Decimal(1_000_000)) * delta
    return float(saved)


def _aggregate(rows, *, by: str) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
        if by == "model":
            key = row.get("model") or "(unknown)"
        else:
            key = row.get("provider") or "(unknown)"
        b = buckets.setdefault(
            key,
            {
                "key": key, "calls": 0,
                "input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
                "saved_usd": 0.0, "model": row.get("model"),
                "provider": row.get("provider"),
            },
        )
        b["calls"] += 1
        b["input_tokens"] += int(row.get("input_tokens") or 0)
        b["cache_read_tokens"] += int(row.get("cache_read_tokens") or 0)
        b["cache_write_tokens"] += int(row.get("cache_write_tokens") or 0)
    # Estimate savings per bucket (after aggregation so we only call pricing once per bucket).
    for b in buckets.values():
        est = _estimate_saved_usd(
            b["model"] or b["key"],
            b["cache_read_tokens"],
            provider=b["provider"],
        )
        b["saved_usd"] = est if est is not None else 0.0
        b["saved_known"] = est is not None
    return sorted(buckets.values(), key=lambda b: b["cache_read_tokens"], reverse=True)


def cmd_stats(args) -> int:
    cutoff = _parse_since(args.since) if args.since else 0.0
    db = SessionDB()
    try:
        cur = db._conn.execute(
            "SELECT model, provider, input_tokens, cache_read_tokens, "
            "cache_write_tokens FROM cost_events WHERE ts >= ?",
            (cutoff,),
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        db.close()

    if not rows:
        print("No cost events in the selected window — cache stats unavailable.")
        return 0

    buckets = _aggregate(rows, by=args.by)

    label = {"model": "Model", "provider": "Provider"}[args.by]
    print(
        f"{label:<32}  Calls   Input tokens  Cache read  Cache write   Hit rate  Est. saved"
    )
    print("-" * 102)
    total_input = 0
    total_read = 0
    total_write = 0
    total_saved = 0.0
    any_unknown = False
    for b in buckets:
        # Hit rate = cache_read / (input + cache_read + cache_write) — what
        # fraction of the *prompt-side* token budget was served from cache.
        prompt_total = b["input_tokens"] + b["cache_read_tokens"] + b["cache_write_tokens"]
        hit = (b["cache_read_tokens"] / prompt_total) if prompt_total else 0.0
        saved_str = _fmt_usd(b["saved_usd"]) if b["saved_known"] else "  ?"
        if not b["saved_known"]:
            any_unknown = True
        print(
            f"{b['key'][:32]:<32}  {b['calls']:>5}   "
            f"{_fmt_int(b['input_tokens']):>12}  "
            f"{_fmt_int(b['cache_read_tokens']):>10}  "
            f"{_fmt_int(b['cache_write_tokens']):>11}   "
            f"{_fmt_pct(hit):>7}  {saved_str:>10}"
        )
        total_input += b["input_tokens"]
        total_read += b["cache_read_tokens"]
        total_write += b["cache_write_tokens"]
        if b["saved_known"]:
            total_saved += b["saved_usd"]
    total_prompt = total_input + total_read + total_write
    total_hit = (total_read / total_prompt) if total_prompt else 0.0
    print("-" * 102)
    print(
        f"{'TOTAL':<32}  {sum(b['calls'] for b in buckets):>5}   "
        f"{_fmt_int(total_input):>12}  {_fmt_int(total_read):>10}  "
        f"{_fmt_int(total_write):>11}   {_fmt_pct(total_hit):>7}  {_fmt_usd(total_saved):>10}"
    )
    if any_unknown:
        print()
        print("Note: '?' = pricing data unavailable for that model/route.")
    if total_hit < 0.20 and total_prompt > 10_000:
        print()
        print(
            "Hint: low hit rate (<20%). Consider tightening the system prompt or "
            "moving stable content into skills so it can be reused across turns."
        )
    return 0


def add_cache_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "cache",
        help="Prompt-cache hit telemetry (read from cost_events)",
        description=(
            "Reports cache hit rate, tokens saved, and estimated USD savings "
            "per model or provider. Powered by D1's cost_events table."
        ),
    )
    sub = p.add_subparsers(dest="cache_action", required=True)
    p_stats = sub.add_parser("stats", help="Hit-rate + savings summary over a window")
    p_stats.add_argument("--since", default="7d", help="Window (e.g. 7d, 24h)")
    p_stats.add_argument(
        "--by", choices=["model", "provider"], default="model",
        help="Group by model or provider (default: model)",
    )
    p_stats.set_defaults(func=cmd_stats)
