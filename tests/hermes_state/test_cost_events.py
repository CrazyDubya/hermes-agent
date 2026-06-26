"""Tests for the D1 `cost_events` SQLite surface.

Covers:
- table creation under the v14 schema
- `record_cost_event` happy path + best-effort behaviour on bad input
- index existence for the documented query shapes
"""
import time

import pytest

from hermes_state import SCHEMA_VERSION, SessionDB


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


class TestSchema:
    def test_schema_version_bumped_to_14(self):
        assert SCHEMA_VERSION >= 14

    def test_cost_events_table_exists(self, db):
        rows = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cost_events'"
        ).fetchall()
        assert len(rows) == 1

    def test_cost_events_indexes_exist(self, db):
        names = {
            r[0] for r in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cost_events'"
            ).fetchall()
        }
        assert "idx_cost_events_session" in names
        assert "idx_cost_events_ts" in names
        assert "idx_cost_events_model" in names

    def test_cost_events_columns(self, db):
        cols = {row[1] for row in db._conn.execute("PRAGMA table_info(cost_events)").fetchall()}
        expected = {
            "id", "session_id", "turn_index", "ts", "model", "provider", "base_url",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "reasoning_tokens", "cost_usd", "cost_status", "cost_source", "latency_ms",
            "skill", "cron_job", "attribution",
        }
        assert expected.issubset(cols)


class TestRecordCostEvent:
    def test_records_minimal_event(self, db):
        db.record_cost_event("s1", model="claude-opus-4-7")
        rows = db._conn.execute("SELECT session_id, model FROM cost_events").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "s1"
        assert rows[0][1] == "claude-opus-4-7"

    def test_records_full_event(self, db):
        ts = time.time()
        db.record_cost_event(
            "s1",
            ts=ts, turn_index=3,
            model="m", provider="anthropic", base_url="https://api",
            input_tokens=1000, output_tokens=200,
            cache_read_tokens=500, cache_write_tokens=10,
            reasoning_tokens=50,
            cost_usd=0.0123, cost_status="actual", cost_source="provider_cost_api",
            latency_ms=850, skill="creative/write", cron_job="daily-brief",
            attribution="kanban:42",
        )
        row = db._conn.execute(
            "SELECT turn_index, input_tokens, output_tokens, cache_read_tokens, "
            "cost_usd, latency_ms, skill, cron_job, attribution FROM cost_events"
        ).fetchone()
        assert row[0] == 3
        assert row[1] == 1000
        assert row[2] == 200
        assert row[3] == 500
        assert abs(row[4] - 0.0123) < 1e-9
        assert row[5] == 850
        assert row[6] == "creative/write"
        assert row[7] == "daily-brief"
        assert row[8] == "kanban:42"

    def test_default_ts_is_now(self, db):
        before = time.time()
        db.record_cost_event("s1")
        after = time.time()
        ts = db._conn.execute("SELECT ts FROM cost_events").fetchone()[0]
        assert before <= ts <= after

    def test_failure_is_swallowed(self, db, monkeypatch):
        # Force the write path to raise; helper must not propagate.
        def _boom(_fn):
            raise RuntimeError("simulated lock")

        monkeypatch.setattr(db, "_execute_write", _boom)
        # Should not raise.
        db.record_cost_event("s1", model="m")

    def test_many_events_distinct_ids(self, db):
        for i in range(5):
            db.record_cost_event("s1", turn_index=i, input_tokens=i * 10)
        ids = [r[0] for r in db._conn.execute("SELECT id FROM cost_events ORDER BY id").fetchall()]
        assert len(ids) == 5
        assert len(set(ids)) == 5  # all distinct
