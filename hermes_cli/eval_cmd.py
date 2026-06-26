"""`hermes eval` — corpus-driven regression checks (D7).

Subcommands:
  run    Load corpus, execute every case, persist a RunSummary,
         print a brief table. Uses a deterministic-echo model when no
         provider is wired (sufficient for substring/regex tests of
         fixture-driven judges).
  list   Show the recent eval_runs rows.
  show   Print all case results for one run id.
  diff   Compare two runs (default: last two for the same corpus) and
         print a markdown summary of pass/fail flips and timing deltas.

The runner deliberately accepts a model callable (see agent.eval.runner).
Wiring a real AIAgent is a one-line adapter that lives outside this CLI
slice so the harness can run offline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_state import SessionDB


def _build_echo_model():
    """Default 'model' that mirrors the prompt back.

    Useful for smoke-testing the harness and for substring judges that
    target the prompt itself. Real-model wiring is a follow-up slice.
    """
    def _fn(case):
        return {"text": case.prompt, "tool_calls": [], "elapsed_ms": 0.0}
    return _fn


def _print_summary_table(summary_dict: dict) -> None:
    cases = summary_dict.get("case_results") or []
    print(f"Run {summary_dict['run_id']}  corpus={summary_dict.get('corpus_path') or '(stdin)'}")
    print(
        f"  cases={summary_dict['case_count']}  "
        f"passed={summary_dict['passed']}  "
        f"failed={summary_dict['failed']}  "
        f"errored={summary_dict['errored']}  "
        f"pass_rate={summary_dict.get('pass_rate', 0.0):.1%}"
    )
    if cases:
        print()
        print(f"{'Case':<32}  {'Status':<8}  {'ms':>6}  Reasons")
        print("-" * 78)
        for cr in cases:
            status = "PASS" if cr["passed"] else ("ERROR" if cr.get("error") else "FAIL")
            reasons = "; ".join(
                (jr.get("reason") or "") for jr in (cr.get("judge_results") or [])
                if not jr.get("passed")
            ) or "ok"
            if cr.get("error"):
                reasons = cr["error"]
            print(
                f"{cr['case_id'][:32]:<32}  {status:<8}  "
                f"{cr.get('elapsed_ms') or 0:>6.0f}  {reasons[:40]}"
            )


def cmd_run(args) -> int:
    from agent.eval import load_corpus, run_corpus

    corpus_path = Path(args.corpus).resolve()
    try:
        cases = load_corpus(corpus_path)
    except Exception as exc:
        print(f"eval: failed to load corpus {corpus_path}: {exc}", file=sys.stderr)
        return 2
    if not cases:
        print(f"eval: no cases found under {corpus_path}", file=sys.stderr)
        return 1

    model_fn = _build_echo_model()
    summary = run_corpus(
        cases, model_fn,
        corpus_path=str(corpus_path),
        model=args.model or "echo",
    )

    if not args.no_persist:
        db = SessionDB()
        try:
            db.persist_eval_run(summary)
        finally:
            db.close()

    summary_dict = summary.to_dict()
    if args.json:
        print(json.dumps(summary_dict, indent=2, default=str))
    else:
        _print_summary_table(summary_dict)
    # Exit non-zero when any case failed or errored — CI-friendly.
    return 0 if (summary.failed == 0 and summary.errored == 0) else 1


def cmd_list(args) -> int:
    db = SessionDB()
    try:
        rows = db.get_recent_eval_runs(limit=args.limit, corpus_path=args.corpus)
    finally:
        db.close()
    if not rows:
        print("No eval runs recorded.")
        return 0
    print(f"{'Run ID':<14}  {'Started':<20}  {'Cases':>5}  {'Pass':>5}  {'Fail':>5}  {'Err':>4}  Corpus")
    print("-" * 100)
    import time as _t
    for r in rows:
        started = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(r["started_at"]))
        corpus = (r.get("corpus_path") or "")[-44:]
        print(
            f"{r['run_id']:<14}  {started:<20}  "
            f"{r['case_count']:>5}  {r['passed']:>5}  {r['failed']:>5}  {r['errored']:>4}  {corpus}"
        )
    return 0


def cmd_show(args) -> int:
    db = SessionDB()
    try:
        rows = db.get_eval_case_results(args.run_id)
    finally:
        db.close()
    if not rows:
        print(f"No case results for run {args.run_id}", file=sys.stderr)
        return 1
    for r in rows:
        status = "PASS" if r["passed"] else ("ERROR" if r.get("error") else "FAIL")
        print(f"[{status}] {r['case_id']}  ({r.get('elapsed_ms') or 0:.0f} ms)")
        if r.get("error"):
            print(f"  ! {r['error']}")
        try:
            judges = json.loads(r.get("judge_results") or "[]")
        except Exception:
            judges = []
        for jr in judges:
            mark = "✓" if jr.get("passed") else "✗"
            print(f"    {mark} {jr.get('reason')}")
    return 0


def _index_by_case(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["case_id"]: r for r in rows}


def cmd_diff(args) -> int:
    db = SessionDB()
    try:
        if args.run_a and args.run_b:
            a_rows = db.get_eval_case_results(args.run_a)
            b_rows = db.get_eval_case_results(args.run_b)
            label_a, label_b = args.run_a, args.run_b
        else:
            recent = db.get_recent_eval_runs(limit=2, corpus_path=args.corpus)
            if len(recent) < 2:
                print("eval diff: need at least 2 runs to compare", file=sys.stderr)
                return 1
            label_b, label_a = recent[0]["run_id"], recent[1]["run_id"]
            a_rows = db.get_eval_case_results(label_a)
            b_rows = db.get_eval_case_results(label_b)
    finally:
        db.close()

    a_idx = _index_by_case(a_rows)
    b_idx = _index_by_case(b_rows)
    all_cases = sorted(set(a_idx) | set(b_idx))
    fixed: List[str] = []
    broken: List[str] = []
    same_pass: List[str] = []
    same_fail: List[str] = []
    added: List[str] = []
    removed: List[str] = []
    for cid in all_cases:
        if cid not in a_idx:
            added.append(cid)
            continue
        if cid not in b_idx:
            removed.append(cid)
            continue
        a_passed = bool(a_idx[cid]["passed"])
        b_passed = bool(b_idx[cid]["passed"])
        if a_passed and b_passed:
            same_pass.append(cid)
        elif not a_passed and not b_passed:
            same_fail.append(cid)
        elif b_passed:
            fixed.append(cid)
        else:
            broken.append(cid)

    print(f"# Eval diff  {label_a}  →  {label_b}")
    print()
    print(f"- Cases compared: {len(all_cases)}  "
          f"(both: {len(same_pass) + len(same_fail)}, added: {len(added)}, removed: {len(removed)})")
    if broken:
        print(f"## ❌ Regressions ({len(broken)})")
        for c in broken:
            print(f"- {c}")
        print()
    if fixed:
        print(f"## ✅ Fixed ({len(fixed)})")
        for c in fixed:
            print(f"- {c}")
        print()
    if added:
        print(f"## ➕ Added cases ({len(added)})")
        for c in added:
            print(f"- {c}")
        print()
    if removed:
        print(f"## ➖ Removed cases ({len(removed)})")
        for c in removed:
            print(f"- {c}")
        print()
    # Exit code: non-zero when there are regressions, for CI gating.
    return 0 if not broken else 1


def add_eval_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "eval",
        help="Run a corpus-driven regression check",
        description=(
            "Evaluate the agent on a YAML corpus of (prompt, expect) cases. "
            "Persists results to ~/.hermes/state.db for later diffs."
        ),
    )
    sub = p.add_subparsers(dest="eval_action", required=True)

    p_run = sub.add_parser("run", help="Execute a corpus")
    p_run.add_argument("--corpus", required=True, help="Corpus directory or YAML file")
    p_run.add_argument("--model", help="Model label to record (default: echo)")
    p_run.add_argument("--json", action="store_true", help="Output the run summary as JSON")
    p_run.add_argument("--no-persist", action="store_true", help="Don't write to state.db")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="List recent runs")
    p_list.add_argument("--limit", type=int, default=10)
    p_list.add_argument("--corpus", help="Filter by corpus path")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show case results for a run id")
    p_show.add_argument("run_id")
    p_show.set_defaults(func=cmd_show)

    p_diff = sub.add_parser("diff", help="Compare two runs (default: last two)")
    p_diff.add_argument("--run-a", help="Baseline run id")
    p_diff.add_argument("--run-b", help="New run id")
    p_diff.add_argument("--corpus", help="Filter by corpus path when picking last two")
    p_diff.set_defaults(func=cmd_diff)
