"""Tests for `hermes_cli.cache_cmd` (D6)."""
import time
from argparse import Namespace

import pytest

from hermes_cli import cache_cmd
from hermes_state import SessionDB


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = tmp_path / "state.db"
    import hermes_state
    orig = hermes_state.SessionDB

    def _factory(path=None):
        return orig(path if path is not None else p)

    monkeypatch.setattr(hermes_state, "SessionDB", _factory)
    monkeypatch.setattr(cache_cmd, "SessionDB", _factory)
    return p


def _seed(db_path, rows):
    db = SessionDB(db_path)
    for row in rows:
        db.record_cost_event(**row)
    db.close()


class TestAggregate:
    def test_groups_by_model_and_sorts_by_cache_read(self):
        rows = [
            {"model": "m1", "provider": "p", "input_tokens": 1000, "cache_read_tokens": 0,
             "cache_write_tokens": 0},
            {"model": "m2", "provider": "p", "input_tokens": 100, "cache_read_tokens": 9000,
             "cache_write_tokens": 0},
        ]
        # Bypass pricing lookups in aggregate (no DB needed).
        buckets = cache_cmd._aggregate(rows, by="model")
        assert [b["key"] for b in buckets] == ["m2", "m1"]
        assert buckets[0]["cache_read_tokens"] == 9000


class TestStats:
    def test_empty_window(self, db_path, capsys):
        cache_cmd.cmd_stats(Namespace(since="1d", by="model"))
        out = capsys.readouterr().out
        assert "No cost events" in out

    def test_renders_hit_rate_total(self, db_path, capsys):
        _seed(db_path, [
            {"session_id": "s1", "model": "m", "provider": "p",
             "input_tokens": 1000, "cache_read_tokens": 4000, "cache_write_tokens": 0},
            {"session_id": "s1", "model": "m", "provider": "p",
             "input_tokens": 1000, "cache_read_tokens": 0, "cache_write_tokens": 0},
        ])
        cache_cmd.cmd_stats(Namespace(since="30d", by="model"))
        out = capsys.readouterr().out
        # Total cache_read = 4000, total prompt = 1000+4000+1000+0 = 6000 → 66.7%
        assert "66.7%" in out
        assert "TOTAL" in out

    def test_low_hit_rate_hint(self, db_path, capsys):
        _seed(db_path, [
            {"session_id": "s1", "model": "m", "provider": "p",
             "input_tokens": 100_000, "cache_read_tokens": 1_000, "cache_write_tokens": 0},
        ])
        cache_cmd.cmd_stats(Namespace(since="30d", by="model"))
        out = capsys.readouterr().out
        assert "low hit rate" in out.lower()

    def test_high_hit_rate_no_hint(self, db_path, capsys):
        _seed(db_path, [
            {"session_id": "s1", "model": "m", "provider": "p",
             "input_tokens": 1_000, "cache_read_tokens": 100_000, "cache_write_tokens": 0},
        ])
        cache_cmd.cmd_stats(Namespace(since="30d", by="model"))
        out = capsys.readouterr().out
        assert "low hit rate" not in out.lower()

    def test_group_by_provider(self, db_path, capsys):
        _seed(db_path, [
            {"session_id": "s1", "model": "m1", "provider": "anthropic",
             "input_tokens": 100, "cache_read_tokens": 500, "cache_write_tokens": 0},
            {"session_id": "s2", "model": "m2", "provider": "anthropic",
             "input_tokens": 100, "cache_read_tokens": 700, "cache_write_tokens": 0},
            {"session_id": "s3", "model": "m3", "provider": "openai",
             "input_tokens": 100, "cache_read_tokens": 50, "cache_write_tokens": 0},
        ])
        cache_cmd.cmd_stats(Namespace(since="30d", by="provider"))
        out = capsys.readouterr().out
        # anthropic should appear before openai (higher cache_read)
        assert out.index("anthropic") < out.index("openai")


class TestParseSince:
    @pytest.mark.parametrize("spec, secs", [("7d", 7 * 86400), ("24h", 24 * 3600)])
    def test_units(self, spec, secs):
        now = time.time()
        cutoff = cache_cmd._parse_since(spec)
        assert abs((now - cutoff) - secs) < 1.0

    def test_bad_unit(self):
        with pytest.raises(SystemExit):
            cache_cmd._parse_since("3z")


class TestEstimateSavedUsd:
    def test_zero_cache_read_returns_zero(self):
        assert cache_cmd._estimate_saved_usd("any-model", 0) == 0.0

    def test_unknown_model_returns_none_or_zero(self):
        # Either pricing isn't known (None) or delta works out to zero — both
        # are valid "we can't estimate" signals. Just must not raise.
        val = cache_cmd._estimate_saved_usd("totally-not-a-real-model-xyz", 1000)
        assert val is None or val == 0.0
