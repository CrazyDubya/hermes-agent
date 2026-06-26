"""Eval judges (D7).

A judge inspects a model response and returns (passed: bool, reason: str).
The response shape:

    {"text": str, "tool_calls": [{"name": str, "arguments": dict}, ...]}

Built-in judges:

    contains    — text contains a substring (case-sensitive by default).
                  Spec: {"kind": "contains", "value": "...", "ignore_case": False}
    not_contains — text does NOT contain a substring.
                  Spec: {"kind": "not_contains", "value": "..."}
    regex       — text matches a regex.
                  Spec: {"kind": "regex", "pattern": "..."}
    tool_called — at least one tool call has the given name.
                  Spec: {"kind": "tool_called", "name": "..."}
    tool_not_called — no tool call has the given name.
                  Spec: {"kind": "tool_not_called", "name": "..."}
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict


JudgeFn = Callable[[Dict[str, Any]], "JudgeResult"]


@dataclass
class JudgeResult:
    passed: bool
    reason: str


@dataclass
class Judge:
    name: str
    spec: dict
    fn: JudgeFn

    def __call__(self, response: dict) -> JudgeResult:
        return self.fn(response)


def _contains(spec: dict) -> JudgeFn:
    value = spec.get("value")
    if not isinstance(value, str) or not value:
        raise ValueError("contains judge requires 'value' (non-empty string)")
    ignore_case = bool(spec.get("ignore_case", False))

    def _fn(response: dict) -> JudgeResult:
        text = response.get("text") or ""
        hay = text.lower() if ignore_case else text
        needle = value.lower() if ignore_case else value
        if needle in hay:
            return JudgeResult(True, f"text contains {value!r}")
        return JudgeResult(False, f"text does not contain {value!r}")

    return _fn


def _not_contains(spec: dict) -> JudgeFn:
    value = spec.get("value")
    if not isinstance(value, str) or not value:
        raise ValueError("not_contains judge requires 'value' (non-empty string)")
    ignore_case = bool(spec.get("ignore_case", False))

    def _fn(response: dict) -> JudgeResult:
        text = response.get("text") or ""
        hay = text.lower() if ignore_case else text
        needle = value.lower() if ignore_case else value
        if needle in hay:
            return JudgeResult(False, f"text unexpectedly contains {value!r}")
        return JudgeResult(True, f"text does not contain {value!r}")

    return _fn


def _regex(spec: dict) -> JudgeFn:
    pattern = spec.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("regex judge requires 'pattern' (non-empty string)")
    flags = re.IGNORECASE if spec.get("ignore_case") else 0
    rx = re.compile(pattern, flags)

    def _fn(response: dict) -> JudgeResult:
        text = response.get("text") or ""
        if rx.search(text):
            return JudgeResult(True, f"text matches /{pattern}/")
        return JudgeResult(False, f"text does not match /{pattern}/")

    return _fn


def _tool_called(spec: dict) -> JudgeFn:
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("tool_called judge requires 'name' (non-empty string)")

    def _fn(response: dict) -> JudgeResult:
        calls = response.get("tool_calls") or []
        for call in calls:
            if call.get("name") == name:
                return JudgeResult(True, f"tool {name!r} was called")
        return JudgeResult(False, f"tool {name!r} was not called")

    return _fn


def _tool_not_called(spec: dict) -> JudgeFn:
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("tool_not_called judge requires 'name' (non-empty string)")

    def _fn(response: dict) -> JudgeResult:
        calls = response.get("tool_calls") or []
        for call in calls:
            if call.get("name") == name:
                return JudgeResult(False, f"tool {name!r} was unexpectedly called")
        return JudgeResult(True, f"tool {name!r} was not called")

    return _fn


_JUDGES: dict[str, Callable[[dict], JudgeFn]] = {
    "contains": _contains,
    "not_contains": _not_contains,
    "regex": _regex,
    "tool_called": _tool_called,
    "tool_not_called": _tool_not_called,
}


def build_judge(spec: dict) -> Judge:
    """Build a Judge from a spec mapping. Raises ValueError on bad input."""
    if not isinstance(spec, dict):
        raise ValueError(f"judge spec must be a mapping, got {type(spec).__name__}")
    kind = spec.get("kind")
    if not kind:
        raise ValueError("judge spec must include 'kind'")
    factory = _JUDGES.get(kind)
    if factory is None:
        raise ValueError(
            f"unknown judge kind {kind!r}; known: {sorted(_JUDGES)}"
        )
    return Judge(name=kind, spec=spec, fn=factory(spec))
