"""Tests for tools.mcp_broker (D5 — broker data model)."""
import pytest

from tools.mcp_broker import (
    ClientIdentity,
    McpBroker,
    StubUpstream,
    is_tool_allowed,
)


# ---------------------------------------------------------------------------
# ClientIdentity
# ---------------------------------------------------------------------------

class TestClientIdentity:
    def test_requires_client_id(self):
        with pytest.raises(ValueError):
            ClientIdentity(client_id="")

    def test_key_with_session(self):
        i = ClientIdentity(client_id="acp-vscode", session_key="window-2")
        assert i.key == "acp-vscode:window-2"

    def test_key_without_session(self):
        i = ClientIdentity(client_id="cli")
        assert i.key == "cli"


# ---------------------------------------------------------------------------
# ACL helper
# ---------------------------------------------------------------------------

class TestACL:
    def test_none_allows_all(self):
        assert is_tool_allowed("anything", None) is True

    def test_empty_denies_all(self):
        assert is_tool_allowed("anything", []) is False

    def test_exact_match(self):
        assert is_tool_allowed("messages_send", ["messages_send"]) is True
        assert is_tool_allowed("messages_send", ["other"]) is False

    def test_glob(self):
        assert is_tool_allowed("messages_send", ["messages_*"]) is True
        assert is_tool_allowed("approvals_list", ["messages_*"]) is False


# ---------------------------------------------------------------------------
# Broker dispatch
# ---------------------------------------------------------------------------

@pytest.fixture
def broker():
    return McpBroker()


@pytest.fixture
def upstream():
    return StubUpstream(
        name="hermes",
        tools=[
            {"name": "messages_send", "description": "send a message"},
            {"name": "messages_read", "description": "read messages"},
            {"name": "permissions_respond", "description": "respond to approval"},
        ],
    )


class TestBrokerLifecycle:
    def test_register_and_list_upstreams(self, broker, upstream):
        broker.register_upstream(upstream)
        assert broker.list_upstreams() == ["hermes"]

    def test_duplicate_upstream_raises(self, broker, upstream):
        broker.register_upstream(upstream)
        with pytest.raises(ValueError):
            broker.register_upstream(upstream)

    def test_unregister_closes_upstream(self, broker, upstream):
        broker.register_upstream(upstream)
        broker.unregister_upstream("hermes")
        assert broker.list_upstreams() == []
        assert upstream.closed is True

    def test_connect_disconnect_client_keeps_upstream(self, broker, upstream):
        broker.register_upstream(upstream)
        ident = ClientIdentity("cli")
        broker.connect_client(ident)
        assert broker.list_clients() == ["cli"]
        broker.disconnect_client(ident)
        assert broker.list_clients() == []
        # Upstream survives client disconnect — the whole point.
        assert upstream.closed is False


class TestDispatch:
    def test_list_tools_returns_upstream_tools(self, broker, upstream):
        broker.register_upstream(upstream)
        ident = ClientIdentity("cli")
        broker.connect_client(ident)
        tools = broker.list_tools_for(ident)
        names = {t["name"] for t in tools}
        assert names == {"messages_send", "messages_read", "permissions_respond"}
        # Each tool tagged with its upstream.
        assert all(t["upstream"] == "hermes" for t in tools)

    def test_list_tools_respects_allowlist(self, broker, upstream):
        broker.register_upstream(upstream)
        ident = ClientIdentity("cli")
        broker.connect_client(ident, allowed_tools=["messages_*"])
        names = {t["name"] for t in broker.list_tools_for(ident)}
        assert names == {"messages_send", "messages_read"}

    def test_call_dispatches_to_upstream(self, broker, upstream):
        broker.register_upstream(upstream)
        ident = ClientIdentity("acp-zed", session_key="w1")
        broker.connect_client(ident)
        result = broker.call(ident, "hermes", "messages_send", {"to": "u"})
        assert "result" in result
        # The stub recorded the identity carry-through.
        assert upstream.calls[0][2].key == "acp-zed:w1"

    def test_call_denied_by_acl(self, broker, upstream):
        broker.register_upstream(upstream)
        ident = ClientIdentity("cli")
        broker.connect_client(ident, allowed_tools=["messages_read"])
        with pytest.raises(PermissionError):
            broker.call(ident, "hermes", "messages_send", {})

    def test_call_unknown_upstream(self, broker, upstream):
        broker.register_upstream(upstream)
        ident = ClientIdentity("cli")
        broker.connect_client(ident)
        result = broker.call(ident, "missing", "any", {})
        assert "error" in result

    def test_call_unknown_client(self, broker, upstream):
        broker.register_upstream(upstream)
        ident = ClientIdentity("ghost")
        with pytest.raises(PermissionError):
            broker.call(ident, "hermes", "messages_send", {})

    def test_call_after_disconnect_refuses(self, broker, upstream):
        broker.register_upstream(upstream)
        ident = ClientIdentity("cli")
        broker.connect_client(ident)
        broker.disconnect_client(ident)
        with pytest.raises(PermissionError):
            broker.call(ident, "hermes", "messages_send", {})


class TestMultiplex:
    def test_two_clients_share_one_upstream(self, broker, upstream):
        broker.register_upstream(upstream)
        a = ClientIdentity("cli-a")
        b = ClientIdentity("cli-b")
        broker.connect_client(a)
        broker.connect_client(b)
        broker.call(a, "hermes", "messages_read", {})
        broker.call(b, "hermes", "messages_read", {})
        # Both calls landed on the same single upstream.
        assert len(upstream.calls) == 2
        keys = {c[2].key for c in upstream.calls}
        assert keys == {"cli-a", "cli-b"}

    def test_per_client_acl_independent(self, broker, upstream):
        broker.register_upstream(upstream)
        a = ClientIdentity("cli-a")
        b = ClientIdentity("cli-b")
        broker.connect_client(a, allowed_tools=["messages_send"])
        broker.connect_client(b, allowed_tools=["messages_read"])
        broker.call(a, "hermes", "messages_send", {})
        broker.call(b, "hermes", "messages_read", {})
        with pytest.raises(PermissionError):
            broker.call(a, "hermes", "messages_read", {})
        with pytest.raises(PermissionError):
            broker.call(b, "hermes", "messages_send", {})


class TestHealth:
    def test_records_calls_and_latency(self, broker, upstream):
        broker.register_upstream(upstream)
        ident = ClientIdentity("cli")
        broker.connect_client(ident)
        for _ in range(3):
            broker.call(ident, "hermes", "messages_send", {})
        status = broker.status()
        h = status["upstreams"]["hermes"]["tool_health"]["messages_send"]
        assert h["calls"] == 3
        assert h["errors"] == 0
        assert h["last_ms"] >= 0

    def test_records_errors(self, broker):
        up = StubUpstream(name="hermes", tools=[{"name": "boom"}],
                          error_for_tools=["boom"])
        broker.register_upstream(up)
        ident = ClientIdentity("cli")
        broker.connect_client(ident)
        result = broker.call(ident, "hermes", "boom", {})
        assert "error" in result
        h = broker.status()["upstreams"]["hermes"]["tool_health"]["boom"]
        assert h["errors"] == 1
        assert h["error_rate"] == 1.0


class TestStatus:
    def test_status_lists_clients(self, broker, upstream):
        broker.register_upstream(upstream)
        broker.connect_client(ClientIdentity("a"))
        broker.connect_client(ClientIdentity("b", session_key="s1"))
        keys = {c["identity"] for c in broker.status()["clients"]}
        assert keys == {"a", "b:s1"}
