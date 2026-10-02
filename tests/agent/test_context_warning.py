"""Tests for context window usage warnings (salvaged from hermes/hermes-865f6958)."""

from unittest.mock import MagicMock

from agent.context_compressor import ContextCompressor


class TestContextWarning:
    def _make_compressor(self, context_length=200_000):
        c = ContextCompressor(model="test/model", threshold_percent=0.50, quiet_mode=True)
        c.context_length = context_length
        c.threshold_tokens = int(context_length * 0.50)
        return c

    def test_no_warning_below_80_percent(self):
        c = self._make_compressor()
        c.update_from_response({"prompt_tokens": 100_000})  # 50%
        assert c.check_context_warning() is None

    def test_warning_at_80_percent(self):
        c = self._make_compressor()
        c.update_from_response({"prompt_tokens": 160_000})  # 80%
        warning = c.check_context_warning()
        assert warning is not None
        assert "80%" in warning
        assert "/compress" in warning

    def test_warning_at_95_percent(self):
        c = self._make_compressor()
        c.update_from_response({"prompt_tokens": 190_000})  # 95%
        warning = c.check_context_warning()
        assert warning is not None
        assert "95%" in warning
        assert "/new" in warning

    def test_warning_fires_only_once_per_threshold(self):
        c = self._make_compressor()
        c.update_from_response({"prompt_tokens": 170_000})  # 85%
        w1 = c.check_context_warning()
        assert w1 is not None  # First time at 80%

        c.update_from_response({"prompt_tokens": 175_000})  # Still above 80%
        w2 = c.check_context_warning()
        assert w2 is None  # Already warned

    def test_95_fires_after_80_already_warned(self):
        c = self._make_compressor()
        c.update_from_response({"prompt_tokens": 165_000})  # 82.5%
        w1 = c.check_context_warning()
        assert w1 is not None
        assert "Context window" in w1

        c.update_from_response({"prompt_tokens": 195_000})  # 97.5%
        w2 = c.check_context_warning()
        assert w2 is not None
        assert "nearly exhausted" in w2  # Escalated warning

    def test_no_warning_when_context_length_zero(self):
        c = self._make_compressor(context_length=0)
        c.update_from_response({"prompt_tokens": 100_000})
        assert c.check_context_warning() is None

    def test_no_warning_when_no_tokens(self):
        c = self._make_compressor()
        assert c.check_context_warning() is None

    def test_warning_includes_token_counts(self):
        c = self._make_compressor(context_length=100_000)
        c.update_from_response({"prompt_tokens": 85_000})
        warning = c.check_context_warning()
        assert "85,000" in warning
        assert "100,000" in warning

    def test_session_reset_clears_warned_flags(self):
        c = self._make_compressor()
        c.update_from_response({"prompt_tokens": 170_000})
        assert c.check_context_warning() is not None
        c._reset_session_compaction_state()
        c.update_from_response({"prompt_tokens": 170_000})
        assert c.check_context_warning() is not None  # fires again after reset
