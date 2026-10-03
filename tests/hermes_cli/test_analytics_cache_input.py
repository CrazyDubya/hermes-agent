"""Usage analytics must count cache tokens as input.

Salvaged from fix/analytics-include-cache-tokens: Anthropic rows store the
uncached portion in input_tokens and the rest in cache_read/cache_write.
"""

from pathlib import Path

import pytest

from hermes_cli.web_routers import analytics
from hermes_state import SessionDB


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    db_path = Path(tmp_path) / "state.db"
    live = SessionDB(db_path=db_path)

    def _open(profile=None, read_only=False):
        return SessionDB(db_path=db_path, read_only=read_only)

    monkeypatch.setattr(analytics, "_open_session_db_for_profile", _open)
    try:
        yield live
    finally:
        live.close()


def test_usage_analytics_folds_cache_tokens_into_input(usage_db):
    usage_db.create_session(session_id="cache-analytics-test", source="cli", model="anthropic/claude")
    usage_db.update_token_counts(
        "cache-analytics-test",
        input_tokens=10,
        output_tokens=4,
        cache_read_tokens=100,
        cache_write_tokens=20,
        model="anthropic/claude",
    )

    data = analytics._get_usage_analytics(days=7)
    totals = data["totals"]
    assert totals["total_input"] == 130
    assert totals["total_cache_read"] == 100
    assert totals["total_output"] == 4
    daily = next(row for row in data["daily"] if row["input_tokens"] == 130)
    assert daily["cache_read_tokens"] == 100
    by_model = next(row for row in data["by_model"] if row["model"] == "anthropic/claude")
    assert by_model["input_tokens"] == 130
