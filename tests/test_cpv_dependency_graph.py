"""Pure graph-algorithm tests for cpv_dependency_graph.py (TRDD-747d7bbc).

Covers Tarjan's iterative SCC/cycle finder, the reverse-edge fan-in map, and
the BFS cascade closure — with no Report/CLI machinery involved.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_dependency_graph import (  # noqa: E402
    PluginNode,
    build_reverse_edges,
    cascade_closure,
    tarjan_scc,
)


def _node(name: str, deps: tuple[str, ...] = ()) -> PluginNode:
    """Test docstring: build a minimal PluginNode for graph-algorithm fixtures."""
    return PluginNode(name=name, version="1.0.0", enabled=True, deps=deps)


class TestTarjanSCC:
    """Test docstring: Tarjan's iterative SCC correctly finds cycles, not mere SCCs."""

    def test_acyclic_graph_yields_no_cycles(self) -> None:
        """Test docstring: a DAG (diamond shape) reports zero cycles."""
        nodes = {
            "a": _node("a", ("b", "c")),
            "b": _node("b", ("d",)),
            "c": _node("c", ("d",)),
            "d": _node("d"),
        }
        assert tarjan_scc(nodes) == []

    def test_self_loop_is_a_singleton_cycle(self) -> None:
        """Test docstring: a self-dependency is reported as a size-1 cycle component."""
        nodes = {"a": _node("a", ("a",))}
        assert tarjan_scc(nodes) == [["a"]]

    def test_length_two_cycle(self) -> None:
        """Test docstring: a reciprocal A<->B edge is detected as one 2-member cycle."""
        nodes = {"a": _node("a", ("b",)), "b": _node("b", ("a",))}
        assert tarjan_scc(nodes) == [["a", "b"]]

    def test_length_n_cycle(self) -> None:
        """Test docstring: a 4-node chain cycle A->B->C->D->A is one SCC of size 4."""
        nodes = {
            "a": _node("a", ("b",)),
            "b": _node("b", ("c",)),
            "c": _node("c", ("d",)),
            "d": _node("d", ("a",)),
        }
        result = tarjan_scc(nodes)
        assert result == [["a", "b", "c", "d"]]

    def test_two_independent_cycles_are_reported_separately(self) -> None:
        """Test docstring: two disjoint cycles yield two separate SCC entries."""
        nodes = {
            "a": _node("a", ("b",)),
            "b": _node("b", ("a",)),
            "c": _node("c", ("d",)),
            "d": _node("d", ("c",)),
        }
        result = tarjan_scc(nodes)
        assert sorted(result) == [["a", "b"], ["c", "d"]]

    def test_missing_dependency_edge_is_not_followed_into_a_cycle(self) -> None:
        """Test docstring: an edge to a name outside the node map is not a cycle path."""
        nodes = {"a": _node("a", ("ghost",))}
        assert tarjan_scc(nodes) == []

    def test_large_scc_is_iterative_not_recursive(self) -> None:
        """Test docstring: a 500-node cycle does not trip Python's recursion limit."""
        n = 500
        names = [f"p{i}" for i in range(n)]
        nodes = {names[i]: _node(names[i], (names[(i + 1) % n],)) for i in range(n)}
        result = tarjan_scc(nodes)
        assert len(result) == 1
        assert sorted(result[0]) == sorted(names)

    def test_diamond_with_shared_dependency_is_not_a_cycle(self) -> None:
        """Test docstring: A->B->D and A->C->D (a legal DAG) reports no cycle."""
        nodes = {
            "a": _node("a", ("b", "c")),
            "b": _node("b", ("d",)),
            "c": _node("c", ("d",)),
            "d": _node("d"),
        }
        assert tarjan_scc(nodes) == []


class TestReverseEdges:
    """Test docstring: build_reverse_edges inverts the dependency graph correctly."""

    def test_empty_graph(self) -> None:
        """Test docstring: an empty node map yields an empty reverse map."""
        assert build_reverse_edges({}) == {}

    def test_fan_in_counted_correctly(self) -> None:
        """Test docstring: three plugins depending on 'core' all appear in reverse['core']."""
        nodes = {
            "core": _node("core"),
            "a": _node("a", ("core",)),
            "b": _node("b", ("core",)),
            "c": _node("c", ("core",)),
        }
        reverse = build_reverse_edges(nodes)
        assert sorted(reverse["core"]) == ["a", "b", "c"]

    def test_no_incoming_edges_absent_from_map(self) -> None:
        """Test docstring: a plugin nobody depends on has no reverse-edge entry."""
        nodes = {"a": _node("a"), "b": _node("b", ("a",))}
        reverse = build_reverse_edges(nodes)
        assert "b" not in reverse


class TestCascadeClosure:
    """Test docstring: cascade_closure BFS finds every transitively-orphaned plugin."""

    def test_direct_dependents_only(self) -> None:
        """Test docstring: a leaf target's closure is exactly its direct dependents."""
        nodes = {"core": _node("core"), "a": _node("a", ("core",)), "b": _node("b", ("core",))}
        reverse = build_reverse_edges(nodes)
        assert cascade_closure(reverse, "core") == {"a", "b"}

    def test_transitive_chain(self) -> None:
        """Test docstring: a chain core<-mid<-leaf orphans both mid and leaf."""
        nodes = {
            "core": _node("core"),
            "mid": _node("mid", ("core",)),
            "leaf": _node("leaf", ("mid",)),
        }
        reverse = build_reverse_edges(nodes)
        assert cascade_closure(reverse, "core") == {"mid", "leaf"}

    def test_no_dependents_yields_empty_closure(self) -> None:
        """Test docstring: a plugin nobody depends on has an empty cascade closure."""
        nodes = {"a": _node("a")}
        reverse = build_reverse_edges(nodes)
        assert cascade_closure(reverse, "a") == set()

    def test_diamond_closure_deduplicates(self) -> None:
        """Test docstring: a diamond fan-in reports each orphaned plugin exactly once."""
        nodes = {
            "core": _node("core"),
            "a": _node("a", ("core",)),
            "b": _node("b", ("core",)),
            "top": _node("top", ("a", "b")),
        }
        reverse = build_reverse_edges(nodes)
        assert cascade_closure(reverse, "core") == {"a", "b", "top"}

    def test_cyclic_reverse_graph_terminates(self) -> None:
        """Test docstring: a cycle in the reverse graph does not infinite-loop the BFS."""
        nodes = {"a": _node("a", ("b",)), "b": _node("b", ("a",)), "c": _node("c", ("a",))}
        reverse = build_reverse_edges(nodes)
        result = cascade_closure(reverse, "a")
        assert result == {"a", "b", "c"}
