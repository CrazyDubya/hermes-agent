"""`hermes cost` — turn-level cost & latency telemetry (D1).

Reads from the `cost_events` SQLite table populated by
`agent.conversation_loop` after every LLM call.

Subcommands:
  ls      Summary table over a time window, grouped by model / session / day.
  show    All events for a single session, newest first.
  export  Dump events as CSV (stdout or file).

Best-effort: if the table is empty or unavailable, commands print a hint
rather than tracebacking.
"""
from __future__ import annotations

import csv
import sys
import time
from typing import Any, Iterable

from hermes_state import SessionDB


_VALID_GROUPS = {"model", "session", "day", "provider"}


def _parse_since(spec: str) -> float:
    """Parse a relative window like '7d', '24h', '90m' into a unix-cutoff."""
    s = spec.strip().lower()
    if not s:
        return 0.0
    unit = s[-1]
    try:
        n = float(s[:-1])
    except ValueError:
        raise SystemExit(f"cost: invalid --since value '{spec}' (use e.g. 7d, 24h, 30m)")
    factor = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}.get(unit)
    if factor is None:
        raise SystemExit(f"cost: invalid --since unit '{unit}' (use d/h/m/s)")
    return time.time() - n * factor


def _fetch_rows(db: SessionDB, cutoff: float, session_id: str | None) -> list[dict]:
    sql = (
        "SELECT id, session_id, turn_index, ts, model, provider, base_url, "
        "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
        "reasoning_tokens, cost_usd, cost_status, cost_source, latency_ms "
        "FROM cost_events WHERE ts >= ?"
    )
    params: list[Any] = [cutoff]
    if session_id:
        sql += " AND session_id = ?"
        params.append(session_id)
    sql += " ORDER BY ts DESC"
    cur = db._conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _group_key(row: dict, group: str) -> str:
    if group == "model":
        return row.get("model") or "(unknown)"
    if group == "session":
        return row.get("session_id") or "(none)"
    if group == "provider":
        return row.get("provider") or "(unknown)"
    if group == "day":
        ts = row.get("ts") or 0
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    return "all"


def _aggregate(rows: Iterable[dict], group: str) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
        k = _group_key(row, group)
        b = buckets.setdefault(
            k,
            {
                "key": k, "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cost_usd": 0.0, "latency_ms": 0,
            },
        )
        b["calls"] += 1
        b["input_tokens"] += row.get("input_tokens") or 0
        b["output_tokens"] += row.get("output_tokens") or 0
        b["cache_read_tokens"] += row.get("cache_read_tokens") or 0
        b["cost_usd"] += float(row.get("cost_usd") or 0.0)
        b["latency_ms"] += int(row.get("latency_ms") or 0)
    out = sorted(buckets.values(), key=lambda b: b["cost_usd"], reverse=True)
    return out


def _fmt_usd(v: float) -> str:
    if v == 0.0:
        return "$0.00"
    if v < 0.01:
        return f"${v:.4f}"
    return f"${v:.2f}"


def cmd_ls(args) -> int:
    cutoff = _parse_since(args.since) if args.since else 0.0
    db = SessionDB()
    try:
        rows = _fetch_rows(db, cutoff, args.session)
    finally:
        db.close()

    if not rows:
        print("No cost events recorded in the selected window.")
        print("(Cost telemetry is populated on every LLM call once D1 is wired.)")
        return 0

    buckets = _aggregate(rows, args.by)
    label = {"model": "Model", "session": "Session", "day": "Day", "provider": "Provider"}[args.by]
    print(f"{label:<32}  Calls   In/Out tokens     Cache      Cost    Avg latency")
    print("-" * 96)
    for b in buckets:
        avg_latency = (b["latency_ms"] / b["calls"]) if b["calls"] else 0
        tok_str = f"{b['input_tokens']:>7}/{b['output_tokens']:<7}"
        cache_str = f"{b['cache_read_tokens']:>7}"
        print(
            f"{b['key'][:32]:<32}  {b['calls']:>5}   {tok_str}   "
            f"{cache_str}  {_fmt_usd(b['cost_usd']):>8}  {avg_latency:>7.0f} ms"
        )
    total_cost = sum(b["cost_usd"] for b in buckets)
    total_calls = sum(b["calls"] for b in buckets)
    print("-" * 96)
    print(f"{'TOTAL':<32}  {total_calls:>5}   {'':<15}   {'':<8}  {_fmt_usd(total_cost):>8}")
    return 0


def cmd_show(args) -> int:
    if not args.session_id:
        print("cost show: --session required", file=sys.stderr)
        return 2
    db = SessionDB()
    try:
        rows = _fetch_rows(db, 0.0, args.session_id)
    finally:
        db.close()
    if not rows:
        print(f"No cost events for session {args.session_id}")
        return 0
    print(f"{'Time':<20} {'Turn':>4}  {'Model':<24} {'In':>7}/{'Out':<6}  {'Cache':>7}  {'Cost':>8}  {'Lat':>6}")
    print("-" * 96)
    for row in reversed(rows):
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ts"]))
        model = (row.get("model") or "")[:24]
        latency = row.get("latency_ms") or 0
        cost = _fmt_usd(float(row.get("cost_usd") or 0.0))
        print(
            f"{ts_str:<20} {row.get('turn_index') or '-':>4}  {model:<24} "
            f"{row.get('input_tokens') or 0:>7}/{row.get('output_tokens') or 0:<6}  "
            f"{row.get('cache_read_tokens') or 0:>7}  {cost:>8}  {latency:>4}ms"
        )
    return 0


def cmd_export(args) -> int:
    cutoff = _parse_since(args.since) if args.since else 0.0
    db = SessionDB()
    try:
        rows = _fetch_rows(db, cutoff, args.session)
    finally:
        db.close()

    out = open(args.output, "w", newline="") if args.output else sys.stdout
    try:
        cols = [
            "id", "session_id", "turn_index", "ts", "model", "provider", "base_url",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "reasoning_tokens", "cost_usd", "cost_status", "cost_source", "latency_ms",
        ]
        writer = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    finally:
        if args.output:
            out.close()
            print(f"wrote {len(rows)} events to {args.output}", file=sys.stderr)
    return 0


def add_cost_subparser(subparsers) -> None:
    """Register the `cost` subcommand on a top-level argparse subparsers."""
    p = subparsers.add_parser(
        "cost",
        help="Show per-call token usage, cost, and latency telemetry",
        description=(
            "Turn-level cost & latency telemetry. Populated automatically on "
            "every LLM call by agent/conversation_loop.py."
        ),
    )
    sub = p.add_subparsers(dest="cost_action", required=True)

    p_ls = sub.add_parser("ls", help="Summary table over a window")
    p_ls.add_argument("--since", default="7d", help="Window (e.g. 7d, 24h, 30m)")
    p_ls.add_argument(
        "--by", choices=sorted(_VALID_GROUPS), default="model",
        help="Group by model / session / day / provider (default: model)",
    )
    p_ls.add_argument("--session", help="Restrict to one session id")
    p_ls.set_defaults(func=cmd_ls)

    p_show = sub.add_parser("show", help="All events for a single session")
    p_show.add_argument("session_id", help="Session id to inspect")
    p_show.set_defaults(func=cmd_show)

    p_export = sub.add_parser("export", help="CSV dump (stdout by default)")
    p_export.add_argument("--since", default="30d", help="Window (e.g. 30d)")
    p_export.add_argument("--session", help="Restrict to one session id")
    p_export.add_argument("--output", "-o", help="Write to file instead of stdout")
    p_export.set_defaults(func=cmd_export)
