"""Tests for `hermes_cli.eval_cmd` (D7 CLI)."""
from argparse import Namespace
from pathlib import Path

import pytest

from hermes_cli import eval_cmd
from hermes_state import SessionDB


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "state.db"
    import hermes_state
    orig = hermes_state.SessionDB

    def _factory(path=None):
        return orig(path if path is not None else p)

    monkeypatch.setattr(hermes_state, "SessionDB", _factory)
    monkeypatch.setattr(eval_cmd, "SessionDB", _factory)
    return p


def _write_corpus(tmp_path, content):
    f = tmp_path / "case.yaml"
    f.write_text(content)
    return tmp_path


class TestRun:
    def test_passes_with_self_referencing_corpus(self, tmp_path, db_path, capsys):
        # The default echo model returns case.prompt → contains 'hi' passes.
        corpus = _write_corpus(tmp_path, "prompt: say hi\nexpect: hi\n")
        rc = eval_cmd.cmd_run(Namespace(
            corpus=str(corpus), model="echo", json=False, no_persist=False,
        ))
        assert rc == 0
        out = capsys.readouterr().out
        assert "pass_rate=100.0%" in out
        # Persisted?
        db = SessionDB(db_path)
        assert len(db.get_recent_eval_runs()) == 1
        db.close()

    def test_fails_when_judge_doesnt_match(self, tmp_path, db_path, capsys):
        corpus = _write_corpus(tmp_path,
                               "prompt: hi\nexpect: definitely-not-there\n")
        rc = eval_cmd.cmd_run(Namespace(
            corpus=str(corpus), model="echo", json=False, no_persist=False,
        ))
        assert rc == 1  # non-zero exit when any case fails

    def test_no_persist_skips_db(self, tmp_path, db_path, capsys):
        corpus = _write_corpus(tmp_path, "prompt: hi\nexpect: hi\n")
        eval_cmd.cmd_run(Namespace(
            corpus=str(corpus), model="echo", json=False, no_persist=True,
        ))
        db = SessionDB(db_path)
        assert db.get_recent_eval_runs() == []
        db.close()

    def test_missing_corpus(self, tmp_path, db_path, capsys):
        rc = eval_cmd.cmd_run(Namespace(
            corpus=str(tmp_path / "nope"), model="echo",
            json=False, no_persist=True,
        ))
        assert rc == 2


class TestListShow:
    def test_list_empty(self, db_path, capsys):
        eval_cmd.cmd_list(Namespace(limit=10, corpus=None))
        out = capsys.readouterr().out
        assert "No eval runs" in out

    def test_show_unknown_run(self, db_path, capsys):
        rc = eval_cmd.cmd_show(Namespace(run_id="ghost"))
        assert rc == 1


class TestDiff:
    def _seed_runs(self, db_path, runs):
        """runs = list of (run_id, [(case_id, passed)...])."""
        from agent.eval.runner import RunSummary, CaseResult
        db = SessionDB(db_path)
        for run_id, results in runs:
            cases = [
                CaseResult(case_id=cid, passed=p, text="", tool_calls=[],
                           elapsed_ms=0.0)
                for cid, p in results
            ]
            summary = RunSummary(
                run_id=run_id, corpus_path="/fixture",
                started_at=0.0, finished_at=0.0, model="echo",
                case_count=len(cases),
                passed=sum(1 for c in cases if c.passed),
                failed=sum(1 for c in cases if not c.passed),
                errored=0, case_results=cases,
            )
            db.persist_eval_run(summary)
        db.close()

    def test_no_changes(self, db_path, capsys):
        self._seed_runs(db_path, [
            ("r1", [("a", True), ("b", True)]),
            ("r2", [("a", True), ("b", True)]),
        ])
        rc = eval_cmd.cmd_diff(Namespace(run_a=None, run_b=None, corpus=None))
        assert rc == 0

    def test_regression_detected(self, db_path, capsys):
        self._seed_runs(db_path, [
            ("r1", [("a", True), ("b", True)]),
            # r2 is newer; b regressed.
            ("r2", [("a", True), ("b", False)]),
        ])
        # r1 sorts older because started_at==0 for both but recent picks
        # by INSERT order via DESC; ensure both runs are visible.
        # Use explicit run-a/run-b to avoid ordering ambiguity.
        rc = eval_cmd.cmd_diff(Namespace(run_a="r1", run_b="r2", corpus=None))
        out = capsys.readouterr().out
        assert rc == 1
        assert "Regression" in out
        assert "- b" in out

    def test_fixed_case(self, db_path, capsys):
        self._seed_runs(db_path, [
            ("r1", [("a", False)]),
            ("r2", [("a", True)]),
        ])
        rc = eval_cmd.cmd_diff(Namespace(run_a="r1", run_b="r2", corpus=None))
        out = capsys.readouterr().out
        assert rc == 0
        assert "Fixed" in out

    def test_needs_two_runs(self, db_path, capsys):
        self._seed_runs(db_path, [("only", [("a", True)])])
        rc = eval_cmd.cmd_diff(Namespace(run_a=None, run_b=None, corpus=None))
        assert rc == 1
