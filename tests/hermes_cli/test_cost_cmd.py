"""Tests for `hermes_cli.cost_cmd` (D1 CLI surface)."""
import csv
import io
import time
from argparse import Namespace

import pytest

from hermes_cli import cost_cmd
from hermes_state import SessionDB


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "state.db"

    # Patch SessionDB() with default path to use the tmp path.
    import hermes_state
    orig = hermes_state.SessionDB

    def _factory(path=None):
        return orig(path if path is not None else p)

    monkeypatch.setattr(hermes_state, "SessionDB", _factory)
    monkeypatch.setattr(cost_cmd, "SessionDB", _factory)
    return p


def _seed(db_path, rows):
    db = SessionDB(db_path)
    for row in rows:
        db.record_cost_event(**row)
    db.close()


class TestParseSince:
    @pytest.mark.parametrize("spec, secs", [("60s", 60), ("5m", 300), ("2h", 7200), ("1d", 86400)])
    def test_units(self, spec, secs):
        now = time.time()
        cutoff = cost_cmd._parse_since(spec)
        assert abs((now - cutoff) - secs) < 1.0

    def test_bad_unit(self):
        with pytest.raises(SystemExit):
            cost_cmd._parse_since("7y")

    def test_bad_number(self):
        with pytest.raises(SystemExit):
            cost_cmd._parse_since("abcd")


class TestLs:
    def test_empty_window_prints_hint(self, db_path, capsys):
        cost_cmd.cmd_ls(Namespace(since="1d", by="model", session=None))
        out = capsys.readouterr().out
        assert "No cost events" in out

    def test_groups_by_model(self, db_path, capsys):
        _seed(db_path, [
            {"session_id": "s1", "model": "claude-opus-4-7",
             "input_tokens": 1000, "output_tokens": 100, "cost_usd": 0.10},
            {"session_id": "s1", "model": "claude-opus-4-7",
             "input_tokens": 500, "output_tokens": 50, "cost_usd": 0.05},
            {"session_id": "s2", "model": "gpt-4",
             "input_tokens": 200, "output_tokens": 20, "cost_usd": 0.02},
        ])
        cost_cmd.cmd_ls(Namespace(since="30d", by="model", session=None))
        out = capsys.readouterr().out
        assert "claude-opus-4-7" in out
        assert "gpt-4" in out
        # opus has higher cost and should be sorted first
        assert out.index("claude-opus-4-7") < out.index("gpt-4")
        assert "TOTAL" in out

    def test_session_filter(self, db_path, capsys):
        _seed(db_path, [
            {"session_id": "s1", "model": "m1", "cost_usd": 1.0},
            {"session_id": "s2", "model": "m2", "cost_usd": 2.0},
        ])
        cost_cmd.cmd_ls(Namespace(since="30d", by="model", session="s1"))
        out = capsys.readouterr().out
        assert "m1" in out
        assert "m2" not in out


class TestShow:
    def test_missing_session_id_errors(self, db_path, capsys):
        rc = cost_cmd.cmd_show(Namespace(session_id=None))
        assert rc == 2

    def test_empty_session(self, db_path, capsys):
        rc = cost_cmd.cmd_show(Namespace(session_id="nope"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "No cost events" in out

    def test_shows_rows_chronologically(self, db_path, capsys):
        t0 = time.time()
        _seed(db_path, [
            {"session_id": "s1", "ts": t0,       "turn_index": 1, "model": "m", "cost_usd": 0.01},
            {"session_id": "s1", "ts": t0 + 10,  "turn_index": 2, "model": "m", "cost_usd": 0.02},
            {"session_id": "s1", "ts": t0 + 20,  "turn_index": 3, "model": "m", "cost_usd": 0.03},
        ])
        cost_cmd.cmd_show(Namespace(session_id="s1"))
        out = capsys.readouterr().out
        # Oldest first in show (reversed of DESC fetch).
        idx1 = out.find(" 1  ")
        idx2 = out.find(" 2  ")
        idx3 = out.find(" 3  ")
        assert idx1 < idx2 < idx3


class TestExport:
    def test_exports_csv_to_file(self, db_path, tmp_path):
        _seed(db_path, [
            {"session_id": "s1", "model": "m", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01},
            {"session_id": "s1", "model": "m", "input_tokens": 20, "output_tokens": 6, "cost_usd": 0.02},
        ])
        out_path = tmp_path / "events.csv"
        cost_cmd.cmd_export(Namespace(since="30d", session=None, output=str(out_path)))
        text = out_path.read_text()
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 2
        assert {r["model"] for r in rows} == {"m"}
        assert any(r["input_tokens"] == "10" for r in rows)
