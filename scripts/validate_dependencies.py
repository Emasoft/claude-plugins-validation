#!/usr/bin/env python3
"""
Claude Plugins Validation - Dependency-graph (cascade) validator

TRDD-747d7bbc. CC v2.1.143 added a reverse-edge scan to
``claude plugin disable``: disabling a plugin ``T`` is refused when another
enabled plugin declares ``T`` as a dependency. This validator catches the
broken-graph states that would trip that runtime enforcement (or a plain
install/enable failure) *before* the user ever runs the CLI:

  * cycles (self / length-2 / length-N) — Tarjan's SCC, iterative
  * missing dependencies (unresolvable in the current scope)
  * a dependency pointing at a disabled plugin (the v2.1.143 cascade case)
  * a dependency name that matches a component, not a plugin (author typo)
  * duplicate dependency entries
  * high-fanin plugins (informational) and cascade traps that exceed a
    configurable threshold (transitive downstream-disable closure)

Usage:
    uv run scripts/validate_dependencies.py <plugin-path>
    uv run scripts/validate_dependencies.py --marketplace <marketplace-root>
    uv run scripts/validate_dependencies.py --marketplace <root> --plugin <name>

Exit codes: 0 clean, 1 CRITICAL, 2 MAJOR, 3 MINOR, 4 NIT (--strict only).

Known scope reduction (documented, not silently dropped): the
cross-marketplace allowlist (TRDD-20108ab7) is a SOFT dependency per §5.7 of
the TRDD — this validator ships without full cross-marketplace resolution.
An unresolvable dependency name present in ``MarketplaceContext.allowlist``
clears with no finding; anything else unresolved is reported as a missing
dependency at the context-appropriate severity, per the TRDD's own
"ship without it, emit MAJOR-with-warning until it arrives" guidance.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cpv_dependency_graph import (  # noqa: E402
    PluginNode,
    build_reverse_edges,
    cascade_closure,
    tarjan_scc,
)
from cpv_management_common import load_jsonc  # noqa: E402
from cpv_validation_common import (  # noqa: E402
    MarketplaceContext,
    ValidationReport,
)

DEFAULT_CASCADE_THRESHOLD = 5
MAX_CYCLE_MEMBERS_REPORTED = 1000
SCHEMA_VERSION = 1

# Component directories whose entries can be mistaken for a plugin name
# (§8 of the TRDD — "dependency points at a component, not a plugin").
_COMPONENT_DIRS = ("skills", "agents", "commands")


def normalize_deps(raw: Any) -> list[str]:
    """Normalise both the object-shape and bare-list dependency declarations.

    Object shape: ``{"dependencies": {"a": ">=1.0.0", "b": "^2.0.0"}}``
    List shape:   ``{"dependencies": ["a", "b"]}``
    Pinned shape: ``{"dependencies": [{"name": "a", "version": ">=1.0.0"}]}``

    The pinned element form is NOT optional to support: it is the shape
    ``cpv_dependency_schema.DEPENDENCY_SUBKEYS`` blesses, the one CPV's own
    manifest uses, and the one CPV ADVISES authors to use in order to pin a
    dependency. Falling back to ``str(element)`` on it yields the repr of a
    dict as a plugin NAME, so a correctly-pinned dependency could never
    resolve against any marketplace and every pinned dep was reported
    unresolvable.

    Only the *names* are needed for graph analysis (version-range
    satisfaction is out of scope — TRDD §4). Order and duplicates are
    preserved so the duplicate-entry detector has something to find.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [str(k) for k in raw]
    if isinstance(raw, list):
        out: list[str] = []
        for x in raw:
            if isinstance(x, dict):
                name = x.get("name")
                # A malformed element with no usable name is SKIPPED, not
                # stringified: `cpv_dependency_schema.validate_dependency_element`
                # is what reports that defect, and inventing a bogus graph node
                # here would emit a second, wrong finding for the same cause.
                if isinstance(name, str) and name:
                    out.append(name)
                continue
            out.append(str(x))
        return out
    return []


def declared_components(plugin_root: Path) -> frozenset[str]:
    """Component names a plugin exposes (skill/agent/command name -> owner
    resolution for the "dependency points at a component" detector).
    """
    names: set[str] = set()
    for sub in _COMPONENT_DIRS:
        d = plugin_root / sub
        if not d.is_dir():
            continue
        for child in d.iterdir():
            if sub == "skills" and child.is_dir():
                names.add(child.name)
            elif child.is_file() and child.suffix == ".md":
                names.add(child.stem)
    return frozenset(names)


def load_plugin_node(plugin_root: Path) -> PluginNode | None:
    """Parse one plugin.json into a PluginNode. None if the manifest is absent
    or unreadable (§7 scenario 20 — the caller demotes this to a defensive
    skip rather than crashing the graph build).
    """
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return None
    try:
        data = load_jsonc(manifest)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None
    return PluginNode(
        name=name,
        version=data.get("version") if isinstance(data.get("version"), str) else None,
        enabled=data.get("enabled", True) is not False,
        deps=tuple(normalize_deps(data.get("dependencies"))),
        declared_components=declared_components(plugin_root),
        source=manifest,
    )


def build_dependency_graph(marketplace_ctx: MarketplaceContext) -> dict[str, PluginNode]:
    """Return the node map already carried by a MarketplaceContext.

    Kept as its own function (rather than inlining ``marketplace_ctx.plugins``
    everywhere) so a future caller building a context lazily has one place to
    hook. ``build_marketplace_context`` is what actually walks the
    marketplace and calls ``load_plugin_node`` per entry.
    """
    return dict(marketplace_ctx.plugins)


def build_marketplace_context(marketplace_root: Path, allowlist: set[str] | None = None) -> MarketplaceContext:
    """Walk a marketplace bundle's local plugins and build the shared context
    once (§5.1 — "built once per marketplace scan and reused").

    Only LOCAL, filesystem-resolvable plugin entries are read (a marketplace
    entry that only points at a remote/upstream source contributes no node —
    it is simply invisible to this graph, matching "for standalone plugin
    validation... only report potentially-broken reference").
    """
    plugins: dict[str, PluginNode] = {}
    mp_json = marketplace_root / ".claude-plugin" / "marketplace.json"
    if not mp_json.is_file():
        mp_json = marketplace_root / "marketplace.json"
    if not mp_json.is_file():
        return MarketplaceContext(plugins=plugins, allowlist=allowlist or set())

    try:
        data = load_jsonc(mp_json)
    except (OSError, ValueError, json.JSONDecodeError):
        return MarketplaceContext(plugins=plugins, allowlist=allowlist or set())

    entries = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return MarketplaceContext(plugins=plugins, allowlist=allowlist or set())

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        rel: str | None = None
        if isinstance(source, str):
            rel = source
        elif isinstance(source, dict) and isinstance(source.get("source"), str):
            rel = source["source"]
        if rel is None:
            name = entry.get("name")
            rel = f"./{name}" if isinstance(name, str) else None
        if rel is None or not rel.startswith("./"):
            continue
        candidate = (marketplace_root / rel[2:]).resolve()
        node = load_plugin_node(candidate)
        if node is not None:
            plugins[node.name] = node

    return MarketplaceContext(plugins=plugins, allowlist=allowlist or set())


def _cycle_findings(target: str, nodes: dict[str, PluginNode], report: ValidationReport) -> None:
    """Emit one CRITICAL per cycle the ``target`` plugin belongs to.

    A marketplace-level scan calls ``validate_dependencies`` once per plugin
    over the SAME shared graph (§5.5 — "runs the dependency validator once
    per bundle... reuses the existing per-plugin loop"). Without a dedup
    rule, every plugin in a cycle would re-report the whole cycle on its own
    turn. The fix: only the CANONICAL member (lexicographically smallest
    name in the component) reports it, and only when validating a member of
    that specific cycle — so N calls over a bundle still produce exactly one
    finding per cycle, not N.
    """
    for component in tarjan_scc(nodes):
        if target not in component:
            continue
        if target != min(component):
            continue
        capped = component[:MAX_CYCLE_MEMBERS_REPORTED]
        suffix = ""
        if len(component) > MAX_CYCLE_MEMBERS_REPORTED:
            suffix = f" ... ({len(component) - MAX_CYCLE_MEMBERS_REPORTED} members truncated)"
        if len(component) == 1:
            name = component[0]
            report.critical(f"Self-dependency: plugin '{name}' declares itself as a dependency", str(nodes[name].source))
        else:
            report.critical(
                "Dependency cycle detected: cycle_members=" + json.dumps(capped) + suffix,
                str(nodes[component[0]].source),
            )


def _resolution_findings(
    node: PluginNode,
    nodes: dict[str, PluginNode],
    marketplace_ctx: MarketplaceContext | None,
    report: ValidationReport,
) -> None:
    seen_in_this_node: set[str] = set()
    for dep in node.deps:
        if dep == node.name:
            continue  # already reported by the cycle detector
        if dep in seen_in_this_node:
            report.minor(f"Duplicate dependency entry '{dep}' in '{node.name}'", str(node.source))
            continue
        seen_in_this_node.add(dep)

        if dep in nodes:
            target = nodes[dep]
            if not target.enabled:
                report.major(
                    f"'{node.name}' depends on '{dep}', which is disabled — "
                    "installing/enabling will hit the v2.1.143 cascade refusal",
                    str(node.source),
                )
            continue

        allowlist = marketplace_ctx.allowlist if marketplace_ctx else set()
        if dep in allowlist:
            continue

        # Component-name shadow: the name resolves to a skill/agent/command
        # owned by some OTHER plugin, but no plugin of that name exists.
        owner = next((n.name for n in nodes.values() if dep in n.declared_components), None)
        if owner is not None:
            report.major(
                f"'{node.name}' depends on '{dep}', which is not a plugin — "
                f"did you mean plugin '{owner}', which provides this component?",
                str(node.source),
            )
            continue

        if marketplace_ctx is not None:
            # PROVEN missing: we hold the marketplace and the name is not in it.
            report.critical(
                f"'{node.name}' depends on '{dep}', which does not exist in this marketplace",
                str(node.source),
            )
        else:
            # COULD NOT CHECK — not proven missing. Without a marketplace the
            # dep may live in one this run never saw, so a blocking severity
            # here is not STRICTER, it is UNFALSIFIABLE: nothing the author can
            # do to a correct plugin clears it. That is the exact shape of the
            # v2.156.0 ruling on RC-DEP-TAG (an offline scan cannot know
            # whether a plugin has dependents, so blocking would fail every
            # standalone plugin — it stays a WARNING), and of v2.154.1, where
            # an over-strict severity WAS the defect.
            #
            # It would also fail EVERY standalone scan of any plugin that
            # declares a dependency — including CPV's own self-scan, which
            # TRDD-747d7bbc's acceptance criterion #4 requires to stay 0/0/0/0.
            # That card's §5.3 table says MAJOR here and so CONTRADICTS its own
            # AC#4; the acceptance criterion names an observable outcome, so it
            # wins. Do not "restore" §5.3 without re-reading AC#4.
            #
            # This is severity-correctness, NOT gate relaxation: the PROVEN
            # case (marketplace held, name absent) keeps CRITICAL above, no
            # rule is muted, no allowlist is added, and the finding stays
            # visible. "Cannot check" is never a pass, and never a failure.
            report.warning(
                f"'{node.name}' depends on '{dep}', which could not be resolved "
                "(no marketplace context — re-run with --marketplace to verify)",
                str(node.source),
            )


def _cascade_findings(
    nodes: dict[str, PluginNode],
    reverse: dict[str, list[str]],
    cascade_threshold: int,
    report: ValidationReport,
) -> None:
    for name, node in nodes.items():
        direct_fanin = len(set(reverse.get(name, [])))
        if direct_fanin >= cascade_threshold:
            report.info(
                f"High-fanin plugin '{name}': {direct_fanin} plugin(s) depend on it directly "
                "(cascade may be intentional)",
                str(node.source),
            )
            closure = cascade_closure(reverse, name)
            if len(closure) > cascade_threshold:
                report.major(
                    f"Transitive cascade exceeded for '{name}': disabling it would orphan "
                    f"{len(closure)} plugin(s) (threshold {cascade_threshold})",
                    str(node.source),
                )


def validate_dependencies(
    plugin_root: Path,
    marketplace_ctx: MarketplaceContext | None,
    report: ValidationReport,
    *,
    cascade_threshold: int = DEFAULT_CASCADE_THRESHOLD,
) -> None:
    """Validate one plugin's dependency graph and merge findings into ``report``.

    §8 acceptance criterion 2 — zero added cost when a plugin declares no
    ``dependencies`` and no marketplace context is in play (short-circuit).
    """
    node = load_plugin_node(plugin_root)
    if node is None:
        return  # §7 scenario 20 — no manifest; the plugin gate already flags this elsewhere.

    if not node.deps and marketplace_ctx is None:
        return  # short-circuit — nothing to check, no marketplace-wide analysis to run.

    if marketplace_ctx is not None:
        nodes = build_dependency_graph(marketplace_ctx)
        nodes = dict(nodes)
        nodes[node.name] = node  # ensure the plugin under test is present even if not pre-scanned
    else:
        nodes = {node.name: node}

    reverse = build_reverse_edges(nodes)

    cycle_scope = {node.name: node} if marketplace_ctx is None else nodes
    _cycle_findings(node.name, cycle_scope, report)
    _resolution_findings(node, nodes, marketplace_ctx, report)
    if marketplace_ctx is not None:
        _cascade_findings(nodes, reverse, cascade_threshold, report)


def _report_to_json(report: ValidationReport, plugin_name: str) -> dict:
    findings = []
    for r in report.results:
        if r.level == "PASSED":
            continue
        findings.append(
            {
                "severity": r.level,
                "rule_id": "DEP-GRAPH",
                "detector": "dependency_graph",
                "plugin": plugin_name,
                "message": r.message,
                "file": r.file,
            }
        )
    return {"schema_version": SCHEMA_VERSION, "plugin": plugin_name, "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a plugin's dependency graph for cascade-disable traps.")
    parser.add_argument("plugin_path", nargs="?", help="Path to a single plugin directory")
    parser.add_argument("--marketplace", type=str, default=None, help="Path to a marketplace bundle root")
    parser.add_argument("--plugin", type=str, default=None, help="Validate only this plugin name within --marketplace")
    parser.add_argument("--cascade-threshold", type=int, default=DEFAULT_CASCADE_THRESHOLD)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("-o", "--output", type=str, default=None)
    args = parser.parse_args()

    if args.marketplace:
        marketplace_root = Path(args.marketplace).resolve()
        ctx = build_marketplace_context(marketplace_root)
        targets = [args.plugin] if args.plugin else list(ctx.plugins.keys())
        overall = ValidationReport()
        for name in targets:
            node = ctx.plugins.get(name)
            if node is None or node.source is None:
                continue
            validate_dependencies(node.source.parent.parent, ctx, overall, cascade_threshold=args.cascade_threshold)
        report = overall
        label = args.marketplace
    else:
        if not args.plugin_path:
            parser.error("plugin_path is required unless --marketplace is given")
        plugin_root = Path(args.plugin_path).resolve()
        report = ValidationReport()
        validate_dependencies(plugin_root, None, report, cascade_threshold=args.cascade_threshold)
        label = args.plugin_path

    if args.format == "json":
        payload = _report_to_json(report, label)
        text = json.dumps(payload, indent=2)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        else:
            print(text)
    else:
        counts = report.count_by_level()
        summary = f"Dependency graph — {label}\n" + " | ".join(
            f"{level}:{counts.get(level, 0)}" for level in ("PASSED", "CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING", "INFO")
        )
        for r in report.results:
            if r.level not in ("PASSED",):
                summary += f"\n  [{r.level}] {r.message}"
        if args.output:
            Path(args.output).write_text(summary, encoding="utf-8")
        print(summary)

    return report.exit_code_strict() if args.strict else min(report.exit_code, 3)


if __name__ == "__main__":
    sys.exit(main())
