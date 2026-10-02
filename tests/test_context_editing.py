"""Tests for Anthropic Context Editing API integration."""

import pytest
from agent.anthropic_adapter import build_anthropic_kwargs


class TestContextEditing:
    """Tests for context_management parameter injection via extra_body."""

    def _simple_messages(self):
        return [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

    def test_disabled_by_default(self):
        """No extra_body/context_management when context_editing is None."""
        kwargs = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=self._simple_messages(),
            tools=None,
            max_tokens=4096,
            reasoning_config=None,
        )
        assert "extra_body" not in kwargs
        assert "context_management" not in kwargs

    def test_disabled_when_false(self):
        """No context_management when enabled is False."""
        kwargs = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=self._simple_messages(),
            tools=None,
            max_tokens=4096,
            reasoning_config=None,
            context_editing={"enabled": False},
        )
        assert "extra_body" not in kwargs

    def test_enabled_adds_context_management(self):
        """context_management is added via extra_body when enabled."""
        kwargs = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=self._simple_messages(),
            tools=None,
            max_tokens=4096,
            reasoning_config=None,
            context_editing={"enabled": True},
        )
        assert "extra_body" in kwargs
        cm = kwargs["extra_body"]["context_management"]
        assert "edits" in cm
        # Without thinking enabled, only tool_uses edit is included
        assert len(cm["edits"]) == 1
        assert cm["edits"][0]["type"] == "clear_tool_uses_20250919"

    def test_thinking_edit_only_when_thinking_enabled(self):
        """clear_thinking is only added when reasoning/thinking is enabled."""
        # Without thinking
        kwargs_no_think = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=self._simple_messages(),
            tools=None,
            max_tokens=4096,
            reasoning_config=None,
            context_editing={"enabled": True},
        )
        edits = kwargs_no_think["extra_body"]["context_management"]["edits"]
        assert all(e["type"] != "clear_thinking_20251015" for e in edits)

        # With thinking enabled
        kwargs_with_think = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=self._simple_messages(),
            tools=None,
            max_tokens=16384,
            reasoning_config={"enabled": True, "effort": "medium"},
            context_editing={"enabled": True},
        )
        edits = kwargs_with_think["extra_body"]["context_management"]["edits"]
        assert len(edits) == 2
        assert edits[0]["type"] == "clear_thinking_20251015"
        assert edits[1]["type"] == "clear_tool_uses_20250919"

    def test_custom_values(self):
        """Custom config values are passed through."""
        kwargs = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=self._simple_messages(),
            tools=None,
            max_tokens=16384,
            reasoning_config={"enabled": True, "effort": "medium"},
            context_editing={
                "enabled": True,
                "trigger_tokens": 80000,
                "keep_tool_uses": 10,
                "keep_thinking_turns": 3,
                "clear_at_least_tokens": 20000,
                "exclude_tools": ["memory", "web_search"],
                "clear_tool_inputs": True,
            },
        )
        edits = kwargs["extra_body"]["context_management"]["edits"]

        thinking = edits[0]
        assert thinking["keep"]["value"] == 3

        tools = edits[1]
        assert tools["trigger"]["value"] == 80000
        assert tools["keep"]["value"] == 10
        assert tools["clear_at_least"]["value"] == 20000
        assert tools["exclude_tools"] == ["memory", "web_search"]
        assert tools["clear_tool_inputs"] is True

    def test_default_exclude_tools(self):
        """Default exclude_tools list is memory, skill_manage, todo."""
        kwargs = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=self._simple_messages(),
            tools=None,
            max_tokens=4096,
            reasoning_config=None,
            context_editing={"enabled": True},
        )
        exclude = kwargs["extra_body"]["context_management"]["edits"][0]["exclude_tools"]
        assert "memory" in exclude
        assert "skill_manage" in exclude
        assert "todo" in exclude

    def test_auto_scales_to_context_window(self):
        """Trigger and clear_at_least scale proportionally to context window."""
        kwargs = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=self._simple_messages(),
            tools=None,
            max_tokens=4096,
            reasoning_config=None,
            context_editing={"enabled": True},
        )
        tools_edit = kwargs["extra_body"]["context_management"]["edits"][0]
        trigger = tools_edit["trigger"]["value"]
        clear_at_least = tools_edit["clear_at_least"]["value"]
        # Should be proportional — trigger ~60%, clear_at_least ~10%
        assert trigger > 50000
        assert clear_at_least > 5000
        assert trigger > clear_at_least

    def test_empty_dict_does_nothing(self):
        """Empty config dict does not add context_management."""
        kwargs = build_anthropic_kwargs(
            model="claude-sonnet-4-20250514",
            messages=self._simple_messages(),
            tools=None,
            max_tokens=4096,
            reasoning_config=None,
            context_editing={},
        )
        assert "extra_body" not in kwargs
