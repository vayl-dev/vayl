"""
Neo4j projection (`graph_store`) — INTEGRATION tests against a live Neo4j.

The fakes in test_apply.py model edge semantics, but only these tests execute the real Cypher, so
they are what catches a broken query (a wrong WHERE clause, a missing ns filter, a typo'd MATCH).
Skipped unless a Neo4j is reachable, like the Postgres suite.

    docker compose --profile graph up -d      # or any local Neo4j
    NEO4J_PASSWORD=… pytest tests/test_graph_store.py
"""
import os

import pytest

neo4j = pytest.importorskip("neo4j", reason="neo4j driver not installed (optional [graph] extra)")

from vayl.storage import graph_store as gs  # noqa: E402

URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PW = os.environ.get("NEO4J_PASSWORD", "testpass123")
NS_A, NS_B = "tenantA\x1f\x1f", "tenantB\x1f\x1f"


def _reachable():
    try:
        d = neo4j.GraphDatabase.driver(URI, auth=(USER, PW))
        d.verify_connectivity()
        d.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(), reason="requires a live Neo4j — start one and set NEO4J_* to run")


@pytest.fixture
def g(monkeypatch):
    # deterministic stub embedder: exercises the write path without touching the network
    monkeypatch.setattr(gs, "_embed", lambda texts: [[0.1, 0.2] for _ in texts])
    graph = gs.Neo4jGraph(URI, USER, PW)
    graph.wipe()
    yield graph
    graph.wipe()
    graph.close()


def live(graph, ns=None):
    """(head, rel, tail) of edges the graph still considers VALID."""
    return sorted((h, r, t) for h, r, t, v in graph.all_edges(valid_only=True, ns=ns))


# ── writes ───────────────────────────────────────────────────────────────────

def test_add_edge_persists_a_valid_edge(g):
    g.add_edge("Bob", "WORKS_AT", "Acme", ns=NS_A, subject="employer")
    assert live(g) == [("Bob", "WORKS_AT", "Acme")]


def test_additive_relation_keeps_every_tail(g):
    """functional=False (depends_on, member_of…): a second tail must NOT retire the first."""
    g.add_edge("auth", "DEPENDS_ON", "token", ns=NS_A, subject="dep1", functional=False)
    g.add_edge("auth", "DEPENDS_ON", "redis", ns=NS_A, subject="dep2", functional=False)
    assert live(g) == [("auth", "DEPENDS_ON", "redis"), ("auth", "DEPENDS_ON", "token")]


def test_functional_add_retires_the_previous_tail(g):
    """functional=True (routes_to, reports_to…): a re-point retires the stale edge."""
    g.add_edge("gateway", "ROUTES_TO", "v1", ns=NS_A, subject="route", functional=True)
    g.add_edge("gateway", "ROUTES_TO", "v2", ns=NS_A, subject="route", functional=True)
    assert live(g) == [("gateway", "ROUTES_TO", "v2")]


def test_functional_add_is_scoped_to_its_namespace(g):
    """A functional re-point must not retire another tenant's edge on the same (head, relation)."""
    g.add_edge("gateway", "ROUTES_TO", "v1", ns=NS_B, subject="route")
    g.add_edge("gateway", "ROUTES_TO", "v1", ns=NS_A, subject="route", functional=True)
    g.add_edge("gateway", "ROUTES_TO", "v2", ns=NS_A, subject="route", functional=True)
    assert live(g, ns=NS_A) == [("gateway", "ROUTES_TO", "v2")]
    assert live(g, ns=NS_B) == [("gateway", "ROUTES_TO", "v1")]   # untouched


def test_supersede_edge_invalidates_then_writes_the_new_tail(g):
    g.add_edge("Erin", "REPORTS_TO", "Frank", ns=NS_A, subject="mgr")
    g.supersede_edge("Erin", "REPORTS_TO", "Grace", ns=NS_A, subject="mgr")
    assert live(g) == [("Erin", "REPORTS_TO", "Grace")]


def test_retract_edge_invalidates_the_relation(g):
    g.add_edge("ServiceA", "CALLS", "ServiceB", ns=NS_A, subject="calls")
    g.retract_edge("ServiceA", "CALLS", ns=NS_A)
    assert live(g) == []
    assert len(g.all_edges(valid_only=False)) == 1   # retained, just not valid


def test_retire_subject_edges_is_head_agnostic(g):
    """The regression that motivated it: the superseding fact named a DIFFERENT head, so head-keyed
    retirement missed the stale edge. Retiring by slot subject must still catch it."""
    g.add_edge("API Gateway", "ROUTES_TO", "v1", ns=NS_A, subject="gw_route")
    g.retire_subject_edges("gw_route", ns=NS_A)
    g.add_edge("Org", "ROUTES_TO", "v2", ns=NS_A, subject="gw_route")   # different head entirely
    assert live(g) == [("Org", "ROUTES_TO", "v2")]                      # stale v1 is gone


def test_retire_subject_edges_does_not_cross_namespaces(g):
    g.add_edge("X", "REL", "1", ns=NS_A, subject="s")
    g.add_edge("X", "REL", "1", ns=NS_B, subject="s")
    g.retire_subject_edges("s", ns=NS_A)
    assert live(g, ns=NS_A) == [] and live(g, ns=NS_B) == [("X", "REL", "1")]


# ── namespace-scoped reads ───────────────────────────────────────────────────

def test_all_edges_is_namespace_scoped(g):
    g.add_edge("Acme", "OWNED_BY", "Globex", ns=NS_A, subject="own")
    g.add_edge("Acme", "OWNED_BY", "Umbrella", ns=NS_B, subject="own")
    assert live(g, ns=NS_A) == [("Acme", "OWNED_BY", "Globex")]
    assert live(g, ns=NS_B) == [("Acme", "OWNED_BY", "Umbrella")]
    assert len(live(g)) == 2                       # unscoped still sees both


def test_neighborhood_is_namespace_scoped(g):
    g.add_edge("Bob", "WORKS_AT", "Acme", ns=NS_A, subject="e")
    g.add_edge("Bob", "WORKS_AT", "EvilCorp", ns=NS_B, subject="e")
    got = {t for _h, _r, t, _v in g.neighborhood(["Bob"], hops=1, ns=NS_A)}
    assert got == {"Acme"}                          # tenant B never leaks in


def test_neighborhood_traverses_multiple_hops(g):
    g.add_edge("Bob", "WORKS_AT", "Acme", ns=NS_A, subject="a")
    g.add_edge("Acme", "OWNED_BY", "Globex", ns=NS_A, subject="b")
    reached = {t for _h, _r, t, _v in g.neighborhood(["Bob"], hops=2, ns=NS_A)}
    assert "Globex" in reached                      # 2-hop chain is followed


def test_neighborhood_excludes_invalidated_edges(g):
    g.add_edge("Bob", "WORKS_AT", "Acme", ns=NS_A, subject="a")
    g.retract_edge("Bob", "WORKS_AT", ns=NS_A)
    assert g.neighborhood(["Bob"], hops=1, valid_only=True, ns=NS_A) == []


# ── erasure ──────────────────────────────────────────────────────────────────

def test_delete_edges_hard_erases_by_namespace(g):
    g.add_edge("A", "REL", "B", ns=NS_A, subject="s")
    g.add_edge("C", "REL", "D", ns=NS_B, subject="s")
    g.delete_edges(ns=NS_A)
    assert g.all_edges(valid_only=False, ns=NS_A) == []          # gone, no tombstone
    assert len(g.all_edges(valid_only=False, ns=NS_B)) == 1


def test_delete_edges_can_narrow_to_one_subject(g):
    g.add_edge("A", "REL", "B", ns=NS_A, subject="keep")
    g.add_edge("C", "REL", "D", ns=NS_A, subject="drop")
    g.delete_edges(ns=NS_A, subject="drop")
    assert live(g, ns=NS_A) == [("A", "REL", "B")]


def test_delete_edges_by_prefix_erases_a_whole_user(g):
    """GDPR erasure across every (agent, run) space belonging to one user."""
    g.add_edge("A", "REL", "B", ns="u1\x1fplanner\x1f", subject="s")
    g.add_edge("C", "REL", "D", ns="u1\x1fcoder\x1f", subject="s")
    g.add_edge("E", "REL", "F", ns="u2\x1f\x1f", subject="s")
    g.delete_edges(ns_prefix="u1\x1f")
    remaining = {h for h, _r, _t, _v in g.all_edges(valid_only=False)}
    assert remaining == {"E"}
