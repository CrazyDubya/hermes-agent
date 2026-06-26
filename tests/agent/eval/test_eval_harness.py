"""Tests for D7 eval harness (corpus + judges + runner + persistence)."""
from pathlib import Path

import pytest

from agent.eval import Case, build_judge, load_corpus, run_corpus
from agent.eval.judges import JudgeResult
from agent.eval.runner import RunSummary
from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def _write(p: Path, content: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


class TestLoadCorpus:
    def test_single_case_per_file(self, tmp_path):
        _write(tmp_path / "a.yaml", "prompt: hi\nexpect: hello\n")
        _write(tmp_path / "b.yaml", "prompt: bye\nexpect: goodbye\n")
        cases = load_corpus(tmp_path)
        assert len(cases) == 2
        ids = {c.id for c in cases}
        assert ids == {"a", "b"}
        # Bare-string expect becomes a contains judge.
        assert cases[0].expect[0]["kind"] == "contains"

    def test_multi_case_per_file(self, tmp_path):
        _write(tmp_path / "x.yaml",
               "- prompt: one\n  expect: o\n- prompt: two\n  expect: t\n")
        cases = load_corpus(tmp_path)
        assert len(cases) == 2
        assert {c.id for c in cases} == {"x#0", "x#1"}

    def test_missing_prompt_raises(self, tmp_path):
        _write(tmp_path / "bad.yaml", "expect: x\n")
        with pytest.raises(ValueError, match="prompt"):
            load_corpus(tmp_path)

    def test_yml_extension_picked_up(self, tmp_path):
        _write(tmp_path / "a.yml", "prompt: hi\nexpect: hi\n")
        cases = load_corpus(tmp_path)
        assert len(cases) == 1

    def test_nested_dirs(self, tmp_path):
        _write(tmp_path / "a/b/c.yaml", "prompt: hi\nexpect: hi\n")
        cases = load_corpus(tmp_path)
        assert len(cases) == 1

    def test_load_single_file(self, tmp_path):
        f = _write(tmp_path / "one.yaml", "prompt: hi\nexpect: hi\n")
        cases = load_corpus(f)
        assert len(cases) == 1

    def test_unknown_extra_preserved(self, tmp_path):
        _write(tmp_path / "a.yaml",
               "prompt: hi\nexpect: hi\ncustom_field: blue\n")
        cases = load_corpus(tmp_path)
        assert cases[0].extra == {"custom_field": "blue"}


# ---------------------------------------------------------------------------
# Judges
# ---------------------------------------------------------------------------

class TestJudges:
    def test_contains_pass(self):
        j = build_judge({"kind": "contains", "value": "hello"})
        r = j({"text": "say hello!", "tool_calls": []})
        assert r.passed is True

    def test_contains_fail(self):
        j = build_judge({"kind": "contains", "value": "hello"})
        r = j({"text": "no greeting", "tool_calls": []})
        assert r.passed is False

    def test_contains_ignore_case(self):
        j = build_judge({"kind": "contains", "value": "HELLO", "ignore_case": True})
        r = j({"text": "say hello", "tool_calls": []})
        assert r.passed is True

    def test_not_contains(self):
        j = build_judge({"kind": "not_contains", "value": "error"})
        assert j({"text": "all good", "tool_calls": []}).passed is True
        assert j({"text": "had an error", "tool_calls": []}).passed is False

    def test_regex(self):
        j = build_judge({"kind": "regex", "pattern": r"\d{4}"})
        assert j({"text": "year 2026", "tool_calls": []}).passed is True
        assert j({"text": "no year", "tool_calls": []}).passed is False

    def test_tool_called(self):
        j = build_judge({"kind": "tool_called", "name": "search_web"})
        assert j({"text": "", "tool_calls": [{"name": "search_web"}]}).passed is True
        assert j({"text": "", "tool_calls": [{"name": "other"}]}).passed is False

    def test_tool_not_called(self):
        j = build_judge({"kind": "tool_not_called", "name": "send_email"})
        assert j({"text": "", "tool_calls": []}).passed is True
        assert j({"text": "", "tool_calls": [{"name": "send_email"}]}).passed is False

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="unknown judge kind"):
            build_judge({"kind": "magic"})

    def test_missing_required_field_raises(self):
        with pytest.raises(ValueError):
            build_judge({"kind": "contains"})  # no value


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class TestRunner:
    def _case(self, cid, prompt, **expect):
        return Case(id=cid, prompt=prompt, expect=[expect] if expect else [])

    def test_all_pass(self):
        cases = [
            self._case("a", "hi", kind="contains", value="hi"),
            self._case("b", "bye", kind="contains", value="bye"),
        ]
        summary = run_corpus(cases, lambda c: {"text": c.prompt, "tool_calls": []})
        assert summary.passed == 2
        assert summary.failed == 0
        assert summary.errored == 0
        assert summary.pass_rate == 1.0

    def test_one_fail(self):
        cases = [
            self._case("a", "hi", kind="contains", value="hi"),
            self._case("b", "hi", kind="contains", value="bye"),
        ]
        summary = run_corpus(cases, lambda c: {"text": c.prompt, "tool_calls": []})
        assert summary.passed == 1
        assert summary.failed == 1
        assert summary.pass_rate == 0.5

    def test_model_exception_marks_errored(self):
        def boom(case):
            raise RuntimeError("network down")
        cases = [self._case("a", "hi", kind="contains", value="hi")]
        summary = run_corpus(cases, boom)
        assert summary.errored == 1
        assert summary.passed == 0
        assert summary.case_results[0].error.startswith("RuntimeError:")

    def test_tool_calls_passed_through(self):
        def model(case):
            return {"text": "ok", "tool_calls": [{"name": "search_web"}]}
        cases = [
            Case(id="a", prompt="search please",
                 expect=[{"kind": "tool_called", "name": "search_web"}]),
        ]
        summary = run_corpus(cases, model)
        assert summary.passed == 1

    def test_summary_serializable(self):
        cases = [self._case("a", "hi", kind="contains", value="hi")]
        summary = run_corpus(cases, lambda c: {"text": c.prompt, "tool_calls": []})
        d = summary.to_dict()
        assert d["case_count"] == 1
        assert d["pass_rate"] == 1.0
        # Round-trippable through JSON
        import json
        json.dumps(d)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_persist_and_fetch(self, tmp_path):
        db = SessionDB(tmp_path / "state.db")
        cases = [
            Case(id="a", prompt="hi", expect=[{"kind": "contains", "value": "hi"}]),
            Case(id="b", prompt="hi", expect=[{"kind": "contains", "value": "bye"}]),
        ]
        summary = run_corpus(
            cases, lambda c: {"text": c.prompt, "tool_calls": []},
            corpus_path="/tmp/fixture", model="echo",
        )
        db.persist_eval_run(summary)
        recent = db.get_recent_eval_runs(limit=5)
        assert len(recent) == 1
        assert recent[0]["case_count"] == 2
        assert recent[0]["passed"] == 1
        assert recent[0]["failed"] == 1
        rows = db.get_eval_case_results(summary.run_id)
        assert {r["case_id"] for r in rows} == {"a", "b"}
        db.close()

    def test_persist_replaces_prior_rows_for_same_run(self, tmp_path):
        db = SessionDB(tmp_path / "state.db")
        summary = run_corpus(
            [Case(id="a", prompt="hi", expect=[{"kind": "contains", "value": "hi"}])],
            lambda c: {"text": c.prompt, "tool_calls": []},
        )
        db.persist_eval_run(summary)
        # Persist again with the same run_id.
        db.persist_eval_run(summary)
        rows = db.get_eval_case_results(summary.run_id)
        assert len(rows) == 1  # no duplicates
        db.close()

    def test_filter_by_corpus(self, tmp_path):
        db = SessionDB(tmp_path / "state.db")
        s1 = run_corpus([Case(id="a", prompt="x", expect=[{"kind": "contains", "value": "x"}])],
                        lambda c: {"text": c.prompt, "tool_calls": []},
                        corpus_path="/path/A")
        s2 = run_corpus([Case(id="a", prompt="y", expect=[{"kind": "contains", "value": "y"}])],
                        lambda c: {"text": c.prompt, "tool_calls": []},
                        corpus_path="/path/B")
        db.persist_eval_run(s1)
        db.persist_eval_run(s2)
        rows = db.get_recent_eval_runs(corpus_path="/path/A")
        assert len(rows) == 1
        assert rows[0]["corpus_path"] == "/path/A"
        db.close()
