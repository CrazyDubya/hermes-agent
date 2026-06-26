"""Workflow visualiser backend (D10).

Aggregates three independent surfaces — Kanban cards, cron jobs (with
the D2 DAG), and delegated sub-agents — into one unified Workflow
graph the TUI/web frontends can render as nodes + edges.

This module ships the **data model + aggregator**. The actual web/TUI
view renders this JSON shape; that's a follow-up. Aggregator inputs
are pluggable functions so callers (tests, frontends, the dashboard)
can wire whichever data sources are live without this module pulling
half the codebase into its import graph.

Schema (every field optional except id, kind, label):

    {
      "nodes": [
        {"id": "...", "kind": "kanban|cron|delegate", "label": "...",
         "status": "open|done|error|...", "cost_usd": 0.012,
         "extra": {...}},
        ...
      ],
      "edges": [
        {"source": "...", "target": "...", "kind": "depends_on|triggers|spawned"},
        ...
      ],
      "totals": {"nodes": N, "edges": M, "cost_usd": ...},
    }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# Node / edge dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Node:
    id: str
    kind: str
    label: str
    status: Optional[str] = None
    cost_usd: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
        }
        if self.status is not None:
            d["status"] = self.status
        if self.cost_usd is not None:
            d["cost_usd"] = round(self.cost_usd, 4)
        if self.extra:
            d["extra"] = dict(self.extra)
        return d


@dataclass
class Edge:
    source: str
    target: str
    kind: str = "depends_on"

    def to_dict(self) -> dict:
        return {"source": self.source, "target": self.target, "kind": self.kind}


@dataclass
class Workflow:
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum((n.cost_usd or 0.0) for n in self.nodes)

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "totals": {
                "nodes": len(self.nodes),
                "edges": len(self.edges),
                "cost_usd": round(self.total_cost, 4),
            },
        }


# ---------------------------------------------------------------------------
# Per-surface adapters — pure functions, easy to test / swap.
# ---------------------------------------------------------------------------

def _cron_node_id(job: dict) -> str:
    return f"cron:{job['id']}"


def cron_to_nodes_edges(jobs: Iterable[dict]) -> tuple[List[Node], List[Edge]]:
    """Convert cron jobs (with the D2 depends_on schema) into nodes + edges.

    Resolves depends_on refs against the same jobs list — supports both
    direct id and ``name`` references (matching ``_filter_by_dependencies``
    in cron/jobs.py).
    """
    jobs = list(jobs)
    by_id = {j.get("id"): j for j in jobs}
    by_name: Dict[str, dict] = {}
    for j in jobs:
        n = j.get("name")
        if n and n not in by_name:
            by_name[n] = j

    nodes: List[Node] = []
    edges: List[Edge] = []
    for j in jobs:
        jid = _cron_node_id(j)
        nodes.append(Node(
            id=jid, kind="cron",
            label=j.get("name") or j.get("id") or "(unnamed)",
            status=j.get("state") or j.get("last_status"),
            extra={
                "schedule_display": j.get("schedule_display"),
                "last_run_at": j.get("last_run_at"),
                "next_run_at": j.get("next_run_at"),
                "retry_state": j.get("retry_state"),
            },
        ))
        for ref in (j.get("depends_on") or []):
            parent = by_id.get(ref) or by_name.get(ref)
            if parent is None:
                # Still emit a dangling edge to a synthetic node so the
                # UI surfaces the misconfiguration instead of dropping it.
                edges.append(Edge(
                    source=f"cron:{ref}", target=jid, kind="depends_on",
                ))
                continue
            edges.append(Edge(
                source=_cron_node_id(parent), target=jid, kind="depends_on",
            ))
    return nodes, edges


def kanban_to_nodes_edges(cards: Iterable[dict]) -> tuple[List[Node], List[Edge]]:
    """Convert kanban cards into nodes. Card->card 'blocks' relations
    surface as ``triggers`` edges.

    Card shape (forgiving): {id, title, status, blocks: [child_id, ...],
    cost_usd: ...}.
    """
    nodes: List[Node] = []
    edges: List[Edge] = []
    for c in cards:
        cid = f"kanban:{c['id']}"
        nodes.append(Node(
            id=cid, kind="kanban",
            label=c.get("title") or c.get("id") or "(card)",
            status=c.get("status"),
            cost_usd=c.get("cost_usd"),
            extra={
                "assignee": c.get("assignee"),
                "lane": c.get("lane"),
            },
        ))
        for child in c.get("blocks") or []:
            edges.append(Edge(
                source=cid, target=f"kanban:{child}", kind="triggers",
            ))
    return nodes, edges


def delegation_to_nodes_edges(spawns: Iterable[dict]) -> tuple[List[Node], List[Edge]]:
    """Convert delegation spawn events into nodes + edges.

    Each spawn: {id, parent_id, label, status, cost_usd}.
    """
    nodes: List[Node] = []
    edges: List[Edge] = []
    for s in spawns:
        sid = f"delegate:{s['id']}"
        nodes.append(Node(
            id=sid, kind="delegate",
            label=s.get("label") or s.get("id") or "(delegated)",
            status=s.get("status"),
            cost_usd=s.get("cost_usd"),
        ))
        parent = s.get("parent_id")
        if parent:
            edges.append(Edge(
                source=f"delegate:{parent}", target=sid, kind="spawned",
            ))
    return nodes, edges


# ---------------------------------------------------------------------------
# Cost overlay
# ---------------------------------------------------------------------------

def apply_cost_overlay(
    workflow: Workflow,
    cost_by_node_id: Dict[str, float],
) -> Workflow:
    """Mutates *workflow* in-place: stamps cost_usd onto nodes that have
    a matching entry in *cost_by_node_id*. Returns the same workflow."""
    for node in workflow.nodes:
        usd = cost_by_node_id.get(node.id)
        if usd is not None:
            node.cost_usd = (node.cost_usd or 0.0) + float(usd)
    return workflow


# ---------------------------------------------------------------------------
# Top-level aggregator
# ---------------------------------------------------------------------------

def build_workflow(
    *,
    cron_jobs: Optional[List[dict]] = None,
    kanban_cards: Optional[List[dict]] = None,
    delegation_spawns: Optional[List[dict]] = None,
    cost_by_node_id: Optional[Dict[str, float]] = None,
) -> Workflow:
    """Build a Workflow from any subset of the three surfaces.

    Each argument is independent — pass only what's live in the caller's
    environment. Empty/None inputs yield an empty section.
    """
    workflow = Workflow()
    if cron_jobs:
        nodes, edges = cron_to_nodes_edges(cron_jobs)
        workflow.nodes.extend(nodes)
        workflow.edges.extend(edges)
    if kanban_cards:
        nodes, edges = kanban_to_nodes_edges(kanban_cards)
        workflow.nodes.extend(nodes)
        workflow.edges.extend(edges)
    if delegation_spawns:
        nodes, edges = delegation_to_nodes_edges(delegation_spawns)
        workflow.nodes.extend(nodes)
        workflow.edges.extend(edges)
    if cost_by_node_id:
        apply_cost_overlay(workflow, cost_by_node_id)
    return workflow
