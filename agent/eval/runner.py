"""Eval runner (D7).

Iterates a corpus, invokes a model callable per case, runs the case's
judges, and returns a RunSummary the CLI can persist / print.

The runner is deliberately decoupled from the real AIAgent so:
  - tests can drop a stub callable (no API keys, no network),
  - future model providers (Anthropic, OpenAI, local) plug in as one-line
    adapters, and
  - the harness can run against pre-recorded fixtures for offline diffs.

A model_fn has the signature::

    def model_fn(case: Case) -> dict:
        return {"text": "...", "tool_calls": [...], "elapsed_ms": 12.3}
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

from .corpus import Case
from .judges import JudgeResult, build_judge


ModelFn = Callable[[Case], dict]


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    text: str
    tool_calls: list
    elapsed_ms: float
    judge_results: List[JudgeResult] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "text": self.text,
            "tool_calls": self.tool_calls,
            "elapsed_ms": self.elapsed_ms,
            "judge_results": [asdict(j) for j in self.judge_results],
            "error": self.error,
        }


@dataclass
class RunSummary:
    run_id: str
    corpus_path: str
    started_at: float
    finished_at: float
    model: Optional[str]
    case_count: int
    passed: int
    failed: int
    errored: int
    case_results: List[CaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.case_count == 0:
            return 0.0
        return self.passed / self.case_count

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "corpus_path": self.corpus_path,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "model": self.model,
            "case_count": self.case_count,
            "passed": self.passed,
            "failed": self.failed,
            "errored": self.errored,
            "pass_rate": self.pass_rate,
            "case_results": [c.to_dict() for c in self.case_results],
        }


def _run_case(case: Case, model_fn: ModelFn) -> CaseResult:
    start = time.monotonic()
    try:
        response = model_fn(case)
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        return CaseResult(
            case_id=case.id, passed=False, text="", tool_calls=[],
            elapsed_ms=elapsed, error=f"{type(exc).__name__}: {exc}",
        )
    elapsed = response.get("elapsed_ms")
    if elapsed is None:
        elapsed = (time.monotonic() - start) * 1000
    text = response.get("text") or ""
    tool_calls = response.get("tool_calls") or []

    judge_results: List[JudgeResult] = []
    overall = True
    for spec in case.expect:
        try:
            judge = build_judge(spec)
        except ValueError as exc:
            judge_results.append(JudgeResult(False, f"bad judge spec: {exc}"))
            overall = False
            continue
        res = judge({"text": text, "tool_calls": tool_calls})
        judge_results.append(res)
        if not res.passed:
            overall = False
    return CaseResult(
        case_id=case.id, passed=overall, text=text, tool_calls=tool_calls,
        elapsed_ms=float(elapsed), judge_results=judge_results,
    )


def run_corpus(
    cases: List[Case], model_fn: ModelFn, *,
    corpus_path: str = "",
    model: Optional[str] = None,
) -> RunSummary:
    """Run every case through *model_fn*, returning a RunSummary."""
    run_id = uuid.uuid4().hex[:12]
    started = time.time()
    results: List[CaseResult] = []
    passed = failed = errored = 0
    for case in cases:
        r = _run_case(case, model_fn)
        results.append(r)
        if r.error:
            errored += 1
        elif r.passed:
            passed += 1
        else:
            failed += 1
    finished = time.time()
    return RunSummary(
        run_id=run_id, corpus_path=corpus_path,
        started_at=started, finished_at=finished, model=model,
        case_count=len(cases), passed=passed, failed=failed, errored=errored,
        case_results=results,
    )
