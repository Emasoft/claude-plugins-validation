#!/usr/bin/env python3
"""Pure graph algorithms for the plugin-dependency-cascade validator.

TRDD-747d7bbc. This module has NO dependency on ``Report``/CLI machinery —
it is the pure-stdlib graph layer that ``validate_dependencies.py`` consumes.
Kept separate so the algorithms (Tarjan SCC, reverse-edge fan-in, BFS
cascade closure) are trivially unit-testable and hashable/diff-able between
two builds (cache invalidation when a marketplace is re-scanned).

Tarjan's SCC is implemented ITERATIVELY (never recursive) — Python's default
recursion limit trips on graphs well under real marketplace sizes (§13 of
the TRDD).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PluginNode:
    """One plugin's dependency-graph-relevant facts, extracted from plugin.json.

    ``deps`` is normalised (no version constraints — cascade analysis only
    needs the edge *names*). Frozen + tuple/frozenset fields so a node (and a
    whole graph) is hashable and trivially diff-able between two builds.
    """

    name: str
    version: str | None
    enabled: bool
    deps: tuple[str, ...]
    declared_components: frozenset[str] = field(default_factory=frozenset)
    source: Path | None = None


def build_reverse_edges(nodes: dict[str, PluginNode]) -> dict[str, list[str]]:
    """dep -> [names of plugins that depend on it]."""
    reverse: dict[str, list[str]] = defaultdict(list)
    for node in nodes.values():
        for dep in node.deps:
            reverse[dep].append(node.name)
    return dict(reverse)


def tarjan_scc(nodes: dict[str, PluginNode]) -> list[list[str]]:
    """Iterative Tarjan's SCC over the dependency graph.

    Returns every strongly-connected component (as a list of member names)
    whose size is > 1, OR a singleton with a self-loop — i.e. every genuine
    cycle. Nodes with no cycle involvement are omitted entirely (this is a
    CYCLE finder, not a full SCC dump).

    Edges point from a plugin to what it depends on. An edge to a name not
    present in ``nodes`` (a missing/external dependency) is simply not
    followed — that is a separate "missing dependency" finding, not a cycle.
    """
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    result: list[list[str]] = []

    # Iterative work-stack. Each frame is (node, iterator-position-index).
    for start in nodes:
        if start in indices:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                indices[v] = index_counter[0]
                lowlinks[v] = index_counter[0]
                index_counter[0] += 1
                stack.append(v)
                on_stack.add(v)

            neighbors = [d for d in nodes[v].deps if d in nodes]
            recursed = False
            while pi < len(neighbors):
                w = neighbors[pi]
                pi += 1
                if w not in indices:
                    work[-1] = (v, pi)
                    work.append((w, 0))
                    recursed = True
                    break
                if w in on_stack:
                    lowlinks[v] = min(lowlinks[v], indices[w])
            if recursed:
                continue

            work[-1] = (v, pi)
            if pi >= len(neighbors):
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[v])
                if lowlinks[v] == indices[v]:
                    component: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == v:
                            break
                    is_cycle = len(component) > 1 or (component[0] in nodes[component[0]].deps)
                    if is_cycle:
                        result.append(sorted(component))

    return result


def cascade_closure(reverse: dict[str, list[str]], target: str) -> set[str]:
    """BFS over the reverse-edge map: every plugin that would be orphaned
    (directly or transitively) if ``target`` were disabled.
    """
    seen: set[str] = set()
    queue: deque[str] = deque(reverse.get(target, []))
    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)
        for upstream in reverse.get(name, []):
            if upstream not in seen:
                queue.append(upstream)
    return seen
