"""Tests for tui_gateway.workflow_view (D10)."""
import json

import pytest

from tui_gateway.workflow_view import (
    Edge,
    Node,
    Workflow,
    apply_cost_overlay,
    build_workflow,
    cron_to_nodes_edges,
    delegation_to_nodes_edges,
    kanban_to_nodes_edges,
)


# ---------------------------------------------------------------------------
# Cron → graph
# ---------------------------------------------------------------------------

class TestCronAdapter:
    def test_no_jobs_empty(self):
        nodes, edges = cron_to_nodes_edges([])
        assert nodes == []
        assert edges == []

    def test_single_job_no_deps(self):
        nodes, edges = cron_to_nodes_edges([{
            "id": "j1", "name": "DailySync",
            "state": "scheduled",
            "schedule_display": "every 1h",
            "depends_on": None,
        }])
        assert len(nodes) == 1
        assert nodes[0].id == "cron:j1"
        assert nodes[0].kind == "cron"
        assert nodes[0].label == "DailySync"
        assert nodes[0].status == "scheduled"
        assert edges == []

    def test_dep_resolved_by_id(self):
        jobs = [
            {"id": "parent", "name": "P", "depends_on": None},
            {"id": "child", "name": "C", "depends_on": ["parent"]},
        ]
        nodes, edges = cron_to_nodes_edges(jobs)
        assert len(nodes) == 2
        assert any(e.source == "cron:parent" and e.target == "cron:child" for e in edges)

    def test_dep_resolved_by_name(self):
        jobs = [
            {"id": "abc123", "name": "Parent", "depends_on": None},
            {"id": "xyz789", "name": "Child", "depends_on": ["Parent"]},
        ]
        _, edges = cron_to_nodes_edges(jobs)
        assert any(e.source == "cron:abc123" and e.target == "cron:xyz789" for e in edges)

    def test_dangling_dep_emitted(self):
        jobs = [
            {"id": "c", "name": "C", "depends_on": ["nonexistent"]},
        ]
        _, edges = cron_to_nodes_edges(jobs)
        # Dangling parent gets a synthetic node id so the UI can flag it.
        assert any(e.source == "cron:nonexistent" for e in edges)


# ---------------------------------------------------------------------------
# Kanban → graph
# ---------------------------------------------------------------------------

class TestKanbanAdapter:
    def test_card_with_blocks(self):
        cards = [
            {"id": "a", "title": "Card A", "status": "open", "blocks": ["b"]},
            {"id": "b", "title": "Card B", "status": "open", "cost_usd": 0.05},
        ]
        nodes, edges = kanban_to_nodes_edges(cards)
        assert len(nodes) == 2
        assert any(n.id == "kanban:b" and n.cost_usd == 0.05 for n in nodes)
        assert any(
            e.source == "kanban:a" and e.target == "kanban:b" and e.kind == "triggers"
            for e in edges
        )


# ---------------------------------------------------------------------------
# Delegation → graph
# ---------------------------------------------------------------------------

class TestDelegationAdapter:
    def test_parent_child(self):
        spawns = [
            {"id": "root", "parent_id": None, "label": "Root agent"},
            {"id": "child", "parent_id": "root", "label": "Sub-agent",
             "cost_usd": 0.01, "status": "done"},
        ]
        nodes, edges = delegation_to_nodes_edges(spawns)
        assert {n.id for n in nodes} == {"delegate:root", "delegate:child"}
        child = next(n for n in nodes if n.id == "delegate:child")
        assert child.cost_usd == 0.01
        assert any(
            e.source == "delegate:root" and e.target == "delegate:child"
            and e.kind == "spawned" for e in edges
        )


# ---------------------------------------------------------------------------
# Cost overlay
# ---------------------------------------------------------------------------

class TestCostOverlay:
    def test_stamps_cost_on_matching_node(self):
        wf = Workflow(nodes=[Node(id="cron:j1", kind="cron", label="J1")])
        apply_cost_overlay(wf, {"cron:j1": 0.42})
        assert wf.nodes[0].cost_usd == 0.42

    def test_accumulates_when_node_already_has_cost(self):
        wf = Workflow(nodes=[Node(id="x", kind="kanban", label="X", cost_usd=0.1)])
        apply_cost_overlay(wf, {"x": 0.05})
        assert abs(wf.nodes[0].cost_usd - 0.15) < 1e-9

    def test_unmatched_node_unchanged(self):
        wf = Workflow(nodes=[Node(id="a", kind="cron", label="A")])
        apply_cost_overlay(wf, {"b": 1.0})
        assert wf.nodes[0].cost_usd is None


# ---------------------------------------------------------------------------
# build_workflow + serialization
# ---------------------------------------------------------------------------

class TestBuildWorkflow:
    def test_combines_all_three_surfaces(self):
        wf = build_workflow(
            cron_jobs=[{"id": "j", "name": "J", "depends_on": None}],
            kanban_cards=[{"id": "c", "title": "C"}],
            delegation_spawns=[{"id": "d", "parent_id": None, "label": "D"}],
        )
        kinds = {n.kind for n in wf.nodes}
        assert kinds == {"cron", "kanban", "delegate"}

    def test_cost_overlay_after_combine(self):
        wf = build_workflow(
            cron_jobs=[{"id": "j", "name": "J", "depends_on": None}],
            cost_by_node_id={"cron:j": 1.5},
        )
        assert wf.nodes[0].cost_usd == 1.5
        assert wf.total_cost == 1.5

    def test_empty_inputs_yield_empty_workflow(self):
        wf = build_workflow()
        d = wf.to_dict()
        assert d["nodes"] == []
        assert d["edges"] == []
        assert d["totals"]["nodes"] == 0

    def test_to_dict_is_json_serializable(self):
        wf = build_workflow(
            cron_jobs=[{"id": "j", "name": "J", "depends_on": None}],
            kanban_cards=[{"id": "c", "title": "C", "cost_usd": 0.1}],
        )
        json.dumps(wf.to_dict())  # must not raise

    def test_totals_aggregate_cost(self):
        wf = build_workflow(
            kanban_cards=[
                {"id": "a", "title": "A", "cost_usd": 0.1},
                {"id": "b", "title": "B", "cost_usd": 0.2},
            ],
        )
        assert wf.to_dict()["totals"]["cost_usd"] == 0.3
