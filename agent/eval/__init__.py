"""Eval harness (D7) — corpus-driven regression checks.

Public surface:

    from agent.eval import load_corpus, run_corpus, Case, CaseResult, Judge
"""
from .corpus import Case, load_corpus
from .judges import Judge, build_judge
from .runner import CaseResult, RunSummary, run_corpus

__all__ = [
    "Case",
    "CaseResult",
    "Judge",
    "RunSummary",
    "build_judge",
    "load_corpus",
    "run_corpus",
]
