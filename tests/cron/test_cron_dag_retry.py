"""Tests for D2: cron DAG (depends_on) + retry policy + cycle detection."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from cron import jobs as jobs_mod
from cron.jobs import (
    compute_retry_delay,
    create_job,
    get_due_jobs,
    load_jobs,
    mark_job_run,
    save_jobs,
    update_job,
    _detect_dependency_cycle,
    _filter_by_dependencies,
    _normalize_depends_on,
    _normalize_retry,
)


@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    return tmp_path


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

class TestNormalizeDependsOn:
    def test_none(self):
        assert _normalize_depends_on(None) is None

    def test_empty_list(self):
        assert _normalize_depends_on([]) is None

    def test_string_becomes_singleton(self):
        assert _normalize_depends_on("parent") == ["parent"]

    def test_strips_empties(self):
        assert _normalize_depends_on(["a", "", " "]) == ["a"]

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            _normalize_depends_on(42)


class TestNormalizeRetry:
    def test_none(self):
        assert _normalize_retry(None) is None

    def test_defaults_applied(self):
        out = _normalize_retry({})
        assert out["max"] == 3
        assert out["backoff"] == "exp"
        assert out["jitter_pct"] == 0
        assert out["base_seconds"] == 60

    def test_custom(self):
        out = _normalize_retry({"max": 5, "backoff": "linear", "jitter_pct": 10})
        assert out["max"] == 5
        assert out["backoff"] == "linear"
        assert out["jitter_pct"] == 10

    def test_invalid_backoff_raises(self):
        with pytest.raises(ValueError):
            _normalize_retry({"backoff": "nuclear"})

    def test_invalid_jitter_range_raises(self):
        with pytest.raises(ValueError):
            _normalize_retry({"jitter_pct": 200})
        with pytest.raises(ValueError):
            _normalize_retry({"jitter_pct": -1})

    def test_invalid_max_raises(self):
        with pytest.raises(ValueError):
            _normalize_retry({"max": 0})


# ---------------------------------------------------------------------------
# Retry delay math
# ---------------------------------------------------------------------------

class TestComputeRetryDelay:
    def test_exp_curve(self):
        cfg = {"max": 5, "backoff": "exp", "jitter_pct": 0, "base_seconds": 60}
        assert compute_retry_delay(1, cfg) == 60
        assert compute_retry_delay(2, cfg) == 120
        assert compute_retry_delay(3, cfg) == 240
        assert compute_retry_delay(4, cfg) == 480

    def test_linear_curve(self):
        cfg = {"max": 5, "backoff": "linear", "jitter_pct": 0, "base_seconds": 30}
        assert compute_retry_delay(1, cfg) == 30
        assert compute_retry_delay(2, cfg) == 60
        assert compute_retry_delay(5, cfg) == 150

    def test_fixed_curve(self):
        cfg = {"max": 5, "backoff": "fixed", "jitter_pct": 0, "base_seconds": 45}
        for attempt in range(1, 6):
            assert compute_retry_delay(attempt, cfg) == 45

    def test_jitter_stays_in_band(self):
        cfg = {"max": 5, "backoff": "fixed", "jitter_pct": 25, "base_seconds": 100}
        # 100 * (1 - 0.25) = 75; 100 * (1 + 0.25) = 125
        for _ in range(200):
            d = compute_retry_delay(1, cfg)
            assert 75 <= d <= 125

    def test_min_one_second(self):
        cfg = {"max": 1, "backoff": "fixed", "jitter_pct": 0, "base_seconds": 0}
        # Even with base=0 we floor at 1s so the schedule advances.
        # (base_seconds=0 is invalid; check via fixed/jitter combination instead)
        cfg = {"max": 1, "backoff": "fixed", "jitter_pct": 99, "base_seconds": 1}
        for _ in range(50):
            assert compute_retry_delay(1, cfg) >= 1


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------

class TestCycleDetection:
    def test_no_cycle(self):
        jobs = [
            {"id": "a", "name": "A", "depends_on": None},
            {"id": "b", "name": "B", "depends_on": ["a"]},
        ]
        # New job c depending on b is fine.
        assert _detect_dependency_cycle("c", ["b"], jobs) is None

    def test_direct_cycle_detected(self):
        jobs = [
            {"id": "a", "name": "A", "depends_on": ["b"]},
            {"id": "b", "name": "B", "depends_on": None},
        ]
        # b → a edge would create a cycle with the existing a → b.
        path = _detect_dependency_cycle("b", ["a"], jobs)
        assert path is not None
        assert "b" in path

    def test_indirect_cycle_detected(self):
        jobs = [
            {"id": "a", "name": "A", "depends_on": ["b"]},
            {"id": "b", "name": "B", "depends_on": ["c"]},
            {"id": "c", "name": "C", "depends_on": None},
        ]
        # c → a would close the loop: a → b → c → a.
        path = _detect_dependency_cycle("c", ["a"], jobs)
        assert path is not None

    def test_self_dependency_detected(self):
        jobs = []
        path = _detect_dependency_cycle("a", ["a"], jobs)
        assert path is not None

    def test_name_resolution(self):
        jobs = [
            {"id": "abc123", "name": "parent", "depends_on": None},
        ]
        # Refer to parent by name, no cycle.
        assert _detect_dependency_cycle("xyz", ["parent"], jobs) is None


# ---------------------------------------------------------------------------
# Create / update integration
# ---------------------------------------------------------------------------

class TestCreateWithDeps:
    def test_create_records_depends_on(self, tmp_cron_dir):
        parent = create_job(prompt="parent task", schedule="every 1h")
        child = create_job(
            prompt="child task", schedule="every 1h",
            depends_on=[parent["id"]],
        )
        assert child["depends_on"] == [parent["id"]]

    def test_create_with_retry(self, tmp_cron_dir):
        job = create_job(
            prompt="flaky", schedule="every 1h",
            retry={"max": 5, "backoff": "exp", "jitter_pct": 20},
        )
        assert job["retry"]["max"] == 5
        assert job["retry"]["backoff"] == "exp"
        assert job["retry_state"] is None

    def test_create_with_cycle_raises(self, tmp_cron_dir):
        a = create_job(prompt="a", schedule="every 1h", name="A")
        b = create_job(prompt="b", schedule="every 1h", name="B", depends_on=[a["id"]])
        # Now try to make a depend on B — cycle.
        with pytest.raises(ValueError, match="cycle"):
            update_job(a["id"], {"depends_on": [b["id"]]})


# ---------------------------------------------------------------------------
# DAG gating
# ---------------------------------------------------------------------------

class TestDependencyGating:
    def _make(self, *, jid, last_status=None, last_run_at=None, depends_on=None):
        return {
            "id": jid, "name": jid,
            "last_status": last_status, "last_run_at": last_run_at,
            "depends_on": depends_on,
        }

    def test_no_deps_passes_through(self):
        due = [self._make(jid="a")]
        kept = _filter_by_dependencies(due, due)
        assert len(kept) == 1

    def test_parent_never_ran_blocks(self):
        parent = self._make(jid="p", last_status=None, last_run_at=None)
        child = self._make(jid="c", depends_on=["p"])
        kept = _filter_by_dependencies([child], [parent, child])
        assert kept == []

    def test_parent_failed_blocks(self):
        parent = self._make(jid="p", last_status="error", last_run_at="2026-05-01T00:00:00+00:00")
        child = self._make(jid="c", depends_on=["p"])
        kept = _filter_by_dependencies([child], [parent, child])
        assert kept == []

    def test_parent_succeeded_passes(self):
        parent = self._make(jid="p", last_status="ok", last_run_at="2026-05-01T00:00:00+00:00")
        child = self._make(jid="c", depends_on=["p"])
        kept = _filter_by_dependencies([child], [parent, child])
        assert len(kept) == 1

    def test_parent_stale_relative_to_child_blocks(self):
        # Child already ran AFTER parent's last run → parent hasn't completed
        # its own cycle since the child last ran → skip.
        parent = self._make(jid="p", last_status="ok", last_run_at="2026-01-01T00:00:00+00:00")
        child = self._make(
            jid="c", depends_on=["p"],
            last_run_at="2026-05-01T00:00:00+00:00",
        )
        kept = _filter_by_dependencies([child], [parent, child])
        assert kept == []

    def test_missing_parent_blocks_and_warns(self, caplog):
        child = self._make(jid="c", depends_on=["nonexistent"])
        kept = _filter_by_dependencies([child], [child])
        assert kept == []
        assert any("unknown parent" in rec.message for rec in caplog.records)

    def test_resolves_by_name(self):
        parent = {
            "id": "p1", "name": "DailySync",
            "last_status": "ok", "last_run_at": "2026-05-01T00:00:00+00:00",
            "depends_on": None,
        }
        child = self._make(jid="c", depends_on=["DailySync"])
        kept = _filter_by_dependencies([child], [parent, child])
        assert len(kept) == 1


# ---------------------------------------------------------------------------
# Retry policy via mark_job_run
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    def test_failure_with_retry_schedules_retry(self, tmp_cron_dir):
        job = create_job(
            prompt="flaky", schedule="every 1h",
            retry={"max": 3, "backoff": "fixed", "jitter_pct": 0, "base_seconds": 60},
        )
        mark_job_run(job["id"], success=False, error="boom")
        fresh = load_jobs()[0]
        assert fresh["retry_state"]["attempt"] == 1
        assert fresh["state"] == "retry"
        # Should NOT have incremented repeat.completed
        assert fresh["repeat"]["completed"] == 0
        # next_run_at should match retry_state.next_retry_at
        assert fresh["next_run_at"] == fresh["retry_state"]["next_retry_at"]

    def test_retry_state_increments(self, tmp_cron_dir):
        job = create_job(
            prompt="flaky", schedule="every 1h",
            retry={"max": 5, "backoff": "fixed", "jitter_pct": 0, "base_seconds": 60},
        )
        mark_job_run(job["id"], success=False, error="1")
        mark_job_run(job["id"], success=False, error="2")
        mark_job_run(job["id"], success=False, error="3")
        fresh = load_jobs()[0]
        assert fresh["retry_state"]["attempt"] == 3

    def test_retries_exhausted_advances_schedule(self, tmp_cron_dir):
        job = create_job(
            prompt="flaky", schedule="every 1h",
            retry={"max": 2, "backoff": "fixed", "jitter_pct": 0, "base_seconds": 60},
        )
        # First failure → retry scheduled (attempt 1 < max 2).
        mark_job_run(job["id"], success=False, error="boom")
        fresh = load_jobs()[0]
        assert fresh["retry_state"] is not None
        # Second failure → attempt would be 2, NOT < 2 → exhausted; clear
        # retry_state and advance schedule.
        mark_job_run(job["id"], success=False, error="boom2")
        fresh = load_jobs()[0]
        assert fresh["retry_state"] is None
        assert fresh["repeat"]["completed"] == 1

    def test_success_clears_retry_state(self, tmp_cron_dir):
        job = create_job(
            prompt="flaky", schedule="every 1h",
            retry={"max": 3, "backoff": "fixed", "jitter_pct": 0, "base_seconds": 60},
        )
        mark_job_run(job["id"], success=False, error="transient")
        mark_job_run(job["id"], success=True)
        fresh = load_jobs()[0]
        assert fresh["retry_state"] is None
        assert fresh["last_status"] == "ok"
        assert fresh["repeat"]["completed"] == 1

    def test_no_retry_config_advances_immediately(self, tmp_cron_dir):
        job = create_job(prompt="hard fail", schedule="every 1h")
        mark_job_run(job["id"], success=False, error="boom")
        fresh = load_jobs()[0]
        assert fresh.get("retry_state") in (None, {})
        assert fresh["repeat"]["completed"] == 1
