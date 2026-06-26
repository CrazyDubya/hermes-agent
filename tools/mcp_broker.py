"""MCP broker (D5).

Multiplexes N client sessions onto K upstream MCP servers. Without a
broker, every client (Discord, web, ACP, CLI) that wants to call the
same upstream MCP server spawns its own subprocess, paying the
startup + OAuth cost N times.

This module ships the **broker data model and dispatch surface** —
the parts that can be unit-tested without an actual MCP subprocess.
The subprocess + JSON-RPC stdio loop is a follow-up; ``UpstreamConnection``
is currently an interface that real connections implement and a
``StubUpstream`` already implements for tests.

Key contracts (preserved from the security branch's F4/F5 work):

- Every call carries a ``ClientIdentity(client_id, session_key)``.
- Per-client target ACLs (``allowed_tools`` globs) gate dispatch.
- The broker survives client disconnects without tearing down upstreams.
"""
from __future__ import annotations

import fnmatch
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Identity / ACL
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClientIdentity:
    """Stable identity stamped onto every broker call.

    *client_id* groups all sessions from one logical client (e.g.
    ``"acp-vscode"``). *session_key* distinguishes simultaneous
    connections from the same client (e.g. two open Zed windows).
    """
    client_id: str
    session_key: str = ""

    def __post_init__(self):
        if not self.client_id:
            raise ValueError("ClientIdentity.client_id must be non-empty")

    @property
    def key(self) -> str:
        return f"{self.client_id}:{self.session_key}" if self.session_key else self.client_id


def is_tool_allowed(tool_name: str, allowed_globs: Optional[List[str]]) -> bool:
    """fnmatch-style ACL. ``None`` allowlist = unrestricted; ``[]`` = deny-all."""
    if allowed_globs is None:
        return True
    if not allowed_globs:
        return False
    for pat in allowed_globs:
        if fnmatch.fnmatchcase(tool_name, pat):
            return True
    return False


# ---------------------------------------------------------------------------
# Upstream connection interface
# ---------------------------------------------------------------------------

class UpstreamConnection(Protocol):
    """Anything the broker can fan calls to.

    Real implementation lives in the follow-up subprocess slice; the
    in-memory ``StubUpstream`` below already satisfies this for tests.
    """
    name: str

    def list_tools(self) -> List[Dict[str, Any]]: ...

    def call_tool(
        self, tool_name: str, arguments: Dict[str, Any], *,
        identity: ClientIdentity,
    ) -> Dict[str, Any]: ...

    def close(self) -> None: ...


@dataclass
class StubUpstream:
    """In-memory upstream for tests.

    Tools and their return values are wired at construction. Errors are
    raised by adding the tool name to ``error_for_tools``.
    """
    name: str
    tools: List[Dict[str, Any]] = field(default_factory=list)
    handler: Optional[Callable[[str, Dict[str, Any], ClientIdentity], Any]] = None
    error_for_tools: List[str] = field(default_factory=list)
    closed: bool = False
    calls: List[Tuple[str, Dict[str, Any], ClientIdentity]] = field(default_factory=list)

    def list_tools(self) -> List[Dict[str, Any]]:
        if self.closed:
            raise RuntimeError(f"upstream {self.name} is closed")
        return list(self.tools)

    def call_tool(
        self, tool_name: str, arguments: Dict[str, Any], *,
        identity: ClientIdentity,
    ) -> Dict[str, Any]:
        if self.closed:
            raise RuntimeError(f"upstream {self.name} is closed")
        self.calls.append((tool_name, dict(arguments), identity))
        if tool_name in self.error_for_tools:
            return {"error": f"{tool_name}: simulated upstream failure"}
        if self.handler is not None:
            return {"result": self.handler(tool_name, arguments, identity)}
        return {"result": {"echo": tool_name, "args": arguments}}

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Health stats
# ---------------------------------------------------------------------------

@dataclass
class UpstreamHealth:
    """Rolling latency + error counters per upstream tool."""
    calls: int = 0
    errors: int = 0
    last_error: Optional[str] = None
    last_ms: float = 0.0
    p95_ms: float = 0.0
    _window: Deque[float] = field(default_factory=lambda: deque(maxlen=200))

    def record(self, ms: float, error: Optional[str] = None) -> None:
        self.calls += 1
        if error:
            self.errors += 1
            self.last_error = error
        self.last_ms = ms
        self._window.append(ms)
        if self._window:
            # Lightweight p95 over the rolling window.
            srt = sorted(self._window)
            self.p95_ms = srt[int(0.95 * (len(srt) - 1))]

    @property
    def error_rate(self) -> float:
        return (self.errors / self.calls) if self.calls else 0.0

    def to_dict(self) -> dict:
        return {
            "calls": self.calls, "errors": self.errors,
            "error_rate": round(self.error_rate, 4),
            "last_ms": round(self.last_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "last_error": self.last_error,
        }


# ---------------------------------------------------------------------------
# Client session record
# ---------------------------------------------------------------------------

@dataclass
class ClientSession:
    identity: ClientIdentity
    allowed_tools: Optional[List[str]] = None
    # Reverse mapping: which upstreams is this client subscribed to.
    upstreams: List[str] = field(default_factory=list)
    connected_at: float = field(default_factory=time.time)
    last_call_at: float = 0.0
    closed: bool = False


# ---------------------------------------------------------------------------
# The broker
# ---------------------------------------------------------------------------

class McpBroker:
    """Fan-in / fan-out broker over upstream MCP connections.

    Thread-safe — every public method takes ``_lock``. Real concurrency
    isolation (per-upstream worker threads) is the follow-up slice.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._upstreams: Dict[str, UpstreamConnection] = {}
        self._health: Dict[str, Dict[str, UpstreamHealth]] = {}  # upstream -> tool -> health
        self._clients: Dict[str, ClientSession] = {}             # identity.key -> session

    # -- upstream lifecycle ----------------------------------------------

    def register_upstream(self, conn: UpstreamConnection) -> None:
        with self._lock:
            if conn.name in self._upstreams:
                raise ValueError(f"upstream {conn.name!r} already registered")
            self._upstreams[conn.name] = conn
            self._health[conn.name] = {}

    def unregister_upstream(self, name: str) -> None:
        with self._lock:
            conn = self._upstreams.pop(name, None)
            self._health.pop(name, None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def list_upstreams(self) -> List[str]:
        with self._lock:
            return sorted(self._upstreams)

    # -- client lifecycle -------------------------------------------------

    def connect_client(
        self,
        identity: ClientIdentity, *,
        allowed_tools: Optional[List[str]] = None,
        upstreams: Optional[List[str]] = None,
    ) -> ClientSession:
        with self._lock:
            session = ClientSession(
                identity=identity,
                allowed_tools=list(allowed_tools) if allowed_tools is not None else None,
                upstreams=list(upstreams) if upstreams else list(self._upstreams),
            )
            self._clients[identity.key] = session
            return session

    def disconnect_client(self, identity: ClientIdentity) -> None:
        with self._lock:
            session = self._clients.pop(identity.key, None)
            if session is not None:
                session.closed = True
                # Upstreams are NOT closed here — that's the whole point.

    def list_clients(self) -> List[str]:
        with self._lock:
            return sorted(self._clients)

    # -- dispatch ---------------------------------------------------------

    def list_tools_for(self, identity: ClientIdentity) -> List[Dict[str, Any]]:
        with self._lock:
            session = self._clients.get(identity.key)
            if session is None or session.closed:
                raise PermissionError(f"unknown or closed client {identity.key!r}")
            results: List[Dict[str, Any]] = []
            for up_name in session.upstreams:
                conn = self._upstreams.get(up_name)
                if conn is None:
                    continue
                try:
                    for t in conn.list_tools():
                        t = dict(t)
                        t.setdefault("upstream", up_name)
                        if is_tool_allowed(t.get("name", ""), session.allowed_tools):
                            results.append(t)
                except Exception as exc:
                    logger.warning("list_tools(%s) failed: %s", up_name, exc)
            return results

    def call(
        self,
        identity: ClientIdentity,
        upstream_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Route a tool call. Returns the upstream's response dict.

        Raises PermissionError when the caller is unknown or the tool
        isn't in their allowlist. Returns ``{"error": ...}`` for
        upstream-level failures (the upstream's own error envelope is
        preserved when present).
        """
        with self._lock:
            session = self._clients.get(identity.key)
            if session is None or session.closed:
                raise PermissionError(f"unknown or closed client {identity.key!r}")
            if not is_tool_allowed(tool_name, session.allowed_tools):
                raise PermissionError(
                    f"tool {tool_name!r} not in allowlist for {identity.key!r}"
                )
            conn = self._upstreams.get(upstream_name)
            if conn is None:
                return {"error": f"upstream {upstream_name!r} not registered"}
            session.last_call_at = time.time()
            health = self._health[upstream_name].setdefault(tool_name, UpstreamHealth())

        start = time.monotonic()
        err: Optional[str] = None
        result: Dict[str, Any]
        try:
            result = conn.call_tool(tool_name, arguments, identity=identity)
            if isinstance(result, dict) and "error" in result:
                err = str(result["error"])
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            result = {"error": err}
        elapsed_ms = (time.monotonic() - start) * 1000.0
        with self._lock:
            health.record(elapsed_ms, error=err)
        return result

    # -- status -----------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Snapshot of upstreams, clients, and per-tool health."""
        with self._lock:
            return {
                "upstreams": {
                    up: {
                        "tool_health": {
                            tool: hh.to_dict() for tool, hh in tools.items()
                        }
                    }
                    for up, tools in self._health.items()
                },
                "clients": [
                    {
                        "identity": s.identity.key,
                        "allowed_tools": s.allowed_tools,
                        "upstreams": s.upstreams,
                        "connected_at": s.connected_at,
                        "last_call_at": s.last_call_at,
                        "closed": s.closed,
                    }
                    for s in self._clients.values()
                ],
            }

    def close(self) -> None:
        """Tear down every upstream connection."""
        with self._lock:
            names = list(self._upstreams)
        for n in names:
            self.unregister_upstream(n)
