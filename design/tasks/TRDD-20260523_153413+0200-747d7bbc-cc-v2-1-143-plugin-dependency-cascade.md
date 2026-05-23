---
trdd-id: 747d7bbc-6cbc-4acc-8a89-a4bdacb3a17e
title: CC v2.1.143 plugin dependency cascade detection — static graph validation
status: not-started
created: 2026-05-23T15:34:13+0200
updated: 2026-05-23T15:34:13+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-747d7bbc — CC v2.1.143 plugin dependency cascade detection — static graph validation

**Filename:** `design/tasks/TRDD-20260523_153413+0200-747d7bbc-cc-v2-1-143-plugin-dependency-cascade.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## 1. User request (verbatim)

> Add CC v2.1.143 plugin dependency cascade detection to CPV's validator.
>
> CC v2.1.143 added enforcement: `claude plugin disable` now refuses if
> another plugin depends on the target. CPV should validate the
> dependency graph statically (detect cycles, detect missing/orphan
> dependencies, detect cascade-disable that would orphan a plugin).

## 2. Background

### What CC v2.1.143 shipped (the runtime change)

`claude plugin disable <plugin>` previously turned a plugin off without
inspecting any other installed plugin. v2.1.143 added a **reverse-edge
scan**: before disabling target `T`, the CLI enumerates every other
enabled plugin `P` whose `plugin.json` declares `T` in its `dependencies`
(or equivalent dependency declaration). If any such `P` exists, the
disable is **refused** with a "cascade would orphan" error listing the
dependent plugins.

This makes plugin dependencies a first-class runtime concern. The
existing CC behaviour treated `dependencies` as advisory documentation;
v2.1.143 promotes it to an enforced constraint at disable time. The same
graph is also implicitly relevant at:

- **install time** — installing `P` that depends on missing `T` would
  produce a runtime failure on the first invocation of `P`'s component
  that needs `T`.
- **enable time** — enabling `P` while its declared `T` is disabled is
  the symmetric case of the disable cascade.
- **uninstall time** — uninstalling `T` is structurally a forced disable
  followed by file deletion; the same reverse-edge scan applies.

CPV's job is to catch the broken states **statically** before the user
ever runs `claude plugin disable / install / enable` against a plugin or
a marketplace bundle.

### Why CPV must validate the graph at author time

A plugin author writing `plugin.json` today can:

1. Declare a dependency on a plugin that doesn't exist in any reachable
   marketplace — a typo in the slug, a stale rename, a plugin pulled
   from the marketplace after the dep was authored.
2. Declare a dependency on themselves (self-edge cycle).
3. Declare a mutual dependency with another plugin (length-2 cycle).
4. Declare a chain `A -> B -> C -> A` (length-N cycle).
5. Declare a dependency on a plugin marked as a leaf / non-enableable
   (e.g. a hook-only plugin that doesn't expose components — depending
   on it is a no-op at best, a runtime trap at worst).
6. Declare a dependency that *would* form a cascade-disable trap once
   the bundle ships — e.g. plugin `core` is depended on by 15 plugins
   in the same marketplace, and `core` itself has `enabled: false` in
   the user's local scope.

All six cases are silent under the v2.1.142 CLI; v2.1.143 turns case
(6) into a runtime error. CPV should report all six classes at
validate time so the author fixes them before publish.

### Where the dependency declaration lives

The current Claude Code plugin manifest accepts a top-level
`dependencies` field in `plugin.json` (mirroring npm/PyPI shape). The
canonical form is:

```json
{
  "name": "my-plugin",
  "version": "1.2.3",
  "dependencies": {
    "core-plugin": ">=1.0.0",
    "other-plugin": "^2.0.0"
  }
}
```

A bare-list variant is also tolerated by the loader:

```json
{ "dependencies": ["core-plugin", "other-plugin"] }
```

CPV must accept both shapes; the cascade validator only needs the
*names* of the dependency edges (not the version constraints) for
graph analysis. Version-range validation is a separate concern
(out of scope for this TRDD — flagged in §11).

### Marketplace context

Most plugins ship inside a marketplace bundle (`marketplace.json` ->
`plugins[]`). The marketplace is the natural scope for graph
validation: every dep edge from plugin `P` in marketplace `M` should
resolve to another plugin in `M`, OR to a plugin in an
**explicitly-allowed external marketplace** (per the existing
TRDD-20108ab7 cross-marketplace dependency allowlist work). An edge
to a name that is neither in `M` nor in the allowlist is a broken
reference.

For standalone plugin validation (no marketplace context), CPV can
only report "potentially-broken reference" since the dep might live
in some marketplace the validator never sees. We emit MAJOR for
standalone validation, CRITICAL when run with marketplace context.

## 3. Goal

Add a **dependency-graph validator** to CPV that, given either a
single plugin or a marketplace bundle, detects every broken-state
class that would cause CC v2.1.143+ to refuse a disable / produce a
runtime failure at install or enable.

Specifically:

1. **Cycle detection** — any directed cycle in the dep graph (self,
   length-2, length-N).
2. **Missing dependency** — dep edge to a name that does not exist
   in the marketplace bundle (or, for standalone plugins, in any
   reachable scope).
3. **Cross-marketplace dependency without allowlist** — dep edge to
   a name in a marketplace that the current bundle does not
   explicitly allow. (Composes with TRDD-20108ab7.)
4. **Orphan-cascade trap** — a plugin that, if disabled, would
   prevent N other plugins from being disabled (because they depend
   on it). This is the v2.1.143 enforcement case. CPV reports
   "high-fanin" plugins as INFO (not an error — the cascade may be
   intentional) and emits a MAJOR finding when a transitive cascade
   exceeds a configurable threshold (default 5).
5. **Self-dependency** — `P` declares itself as a dep. CRITICAL
   (always a typo or paste bug; runtime undefined).
6. **Duplicate dep entries** — same name listed twice with same or
   different version specs. MINOR (loader takes last; author intent
   ambiguous).
7. **Non-existent dependent of a disabled plugin** — when a plugin
   marks itself disabled via `enabled: false` in its `plugin.json`
   AND some plugin in the same bundle depends on it. MAJOR — at
   install the user will hit the cascade refusal immediately.
8. **Dependency points at a component, not a plugin** — author wrote
   `"my-skill"` (a skill name) instead of the plugin slug that owns
   it. CPV resolves the name across both spaces and emits a
   MAJOR-with-hint when the name matches a component but no plugin.

## 4. Non-goals (out of scope for this TRDD)

- **Version-range satisfaction** (`>=1.0.0`, `^2.0.0`). Tracked
  separately; the cascade validator only consumes names.
- **Lockfile generation**. CC has no lockfile concept; the validator
  works directly off `plugin.json` declarations.
- **Dynamic / runtime dep resolution** (e.g. one plugin lazy-Skill-
  invokes another by name without a declared dep). Covered by
  TRDD-25b9be90 (ghost-agent dispatch detection) — orthogonal.
- **Cross-scope dependencies** (user-scope plugin depending on a
  project-scope plugin or vice versa). Phase-2 concern; CC scope
  semantics for the dep graph have not stabilised yet.

## 5. Design

### 5.1 New validator module: `scripts/validate_dependencies.py`

A new top-level validator joining the existing `validate_*.py` family
(see `scripts/validate_xref.py`, `scripts/validate_marketplace.py`,
`scripts/validate_hook_precedence.py` for the established shape).

```
scripts/validate_dependencies.py
```

CLI entrypoint (consistent with sibling validators):

```bash
uv run scripts/validate_dependencies.py <plugin-path>
uv run scripts/validate_dependencies.py --marketplace <marketplace-root>
uv run scripts/validate_dependencies.py --marketplace <root> --plugin <name>
```

Importable function for `validate_plugin.py` integration:

```python
def validate_dependencies(plugin_root: Path,
                          marketplace_ctx: MarketplaceContext | None = None,
                          report: Report,
                          *, cascade_threshold: int = 5) -> None: ...
```

`MarketplaceContext` is a lightweight dataclass (added to
`cpv_validation_common.py`) holding `{plugins: dict[name, PluginNode],
allowlist: set[str], scope: Scope}`. A single instance is built once
per marketplace scan and reused across every per-plugin validation —
identical to how `marketplace_resolver` caches in the existing
`validate_marketplace.py`.

### 5.2 Graph construction

`PluginNode` dataclass:

```python
@dataclass(frozen=True)
class PluginNode:
    name: str
    version: str | None
    enabled: bool                 # plugin.json["enabled"], default True
    deps: tuple[str, ...]         # normalised dep names (no version constraints)
    declared_components: frozenset[str]   # skills, agents, commands, hooks, mcpServers, lspServers
    source: Path                  # plugin.json absolute path
```

`build_dependency_graph(marketplace_ctx) -> dict[str, PluginNode]`
walks every plugin in the marketplace, parses `plugin.json` with the
shared JSONC parser (`cpv_management_common.load_jsonc`), normalises
both dep shapes (object vs list), and returns the node map.

### 5.3 Detection algorithms

| Detector | Algorithm | Output severity |
|----------|-----------|-----------------|
| Self-dep | `name in node.deps` | CRITICAL |
| Length-2 cycle | reciprocal edge scan | CRITICAL |
| Length-N cycle | Tarjan's SCC; any SCC with size>1 OR singleton with self-loop is a cycle | CRITICAL |
| Missing dep | `dep not in node_map and dep not in allowlist` | CRITICAL (marketplace ctx) / MAJOR (standalone) |
| Cross-marketplace unallowed | `dep in another marketplace and dep not in allowlist` | MAJOR |
| Disabled-target dep | `dep in node_map and node_map[dep].enabled is False` | MAJOR |
| Component-name shadow | `dep not in plugins but dep in any plugin.declared_components` | MAJOR (with hint: "did you mean plugin '<owner>' which provides this component?") |
| Duplicate dep entry | repeated name in deps list | MINOR |
| High-fanin (cascade trap) | reverse-edge fanin > cascade_threshold | INFO (always) |
| Transitive cascade exceeded | downstream-disable closure > cascade_threshold | MAJOR |

Tarjan's SCC is the canonical cycle detector (O(V+E), reports all
SCCs in one pass, naturally enumerates the minimal cycle node set
for each finding — the report's "cycle members:" line should list
the SCC verbatim so the author sees exactly which edges to cut).

Reverse-edge map is built once: `reverse: dict[str, list[str]] =
defaultdict(list)`; for `node` in nodes, for `dep` in node.deps:
`reverse[dep].append(node.name)`. Transitive cascade closure for
plugin `T` is `set()` accumulator filled by BFS over `reverse`.

### 5.4 Wiring into `validate_plugin.py`

`validate_plugin.py::main` invokes the new validator after the
existing `validate_xref` call (cross-reference integrity is a
prerequisite — broken xrefs can produce false positives in dep
graph analysis). The marketplace context is read from
`CPVContext.marketplace_root` if set, otherwise None.

```python
# in validate_plugin.main(), after validate_xref(...):
from .validate_dependencies import validate_dependencies, MarketplaceContext

mp_ctx = MarketplaceContext.from_cli(args)  # None for standalone
validate_dependencies(plugin_root, marketplace_ctx=mp_ctx, report=report,
                      cascade_threshold=args.cascade_threshold or 5)
```

`--cascade-threshold N` is a new CLI flag (default 5). Surfaced in
the `cpv-main-menu` advanced-options pane.

### 5.5 Wiring into `validate_marketplace.py`

The marketplace-level entrypoint runs the dependency validator
**once per bundle**, not once per plugin, to avoid O(N) re-builds
of the graph. It reuses the existing per-plugin loop to feed each
plugin's findings into the same `Report`. Output uses the shared
`save_report_and_print_summary` from `cpv_validation_common.py`
(no new reporting machinery).

### 5.6 New batch skill: `cpv-batch-dependency-audit`

Mirrors the v2.101.0 batch-skills family — accepts marketplace-URL /
list / `@listfile` / mixed-input via the existing
`cpv_marketplace_input.resolve()` resolver. Dispatches one
`plugin-validator` subagent per shard in `batch_dependency_audit`
mode (new mode added to `plugin-validator-agent.md`). The agent
loads only `cpv-batch-dependency-audit` skill, runs the validator
in dep-only mode (`--only dependencies`), and emits one report per
shard. The aggregator (`plugin-validator-aggregator`) merges the
per-shard reports into one fleet-wide dep-graph view.

### 5.7 Integration with TRDD-20108ab7 (cross-marketplace allowlist)

The allowlist semantics already designed in TRDD-20108ab7 plug
directly into `MarketplaceContext.allowlist`. No duplicate logic —
the dep validator consumes the allowlist set as opaque
`set[str]`. Implementation order: TRDD-20108ab7 ships the
allowlist parser first; this TRDD then consumes it. (Listed in §10
"Sequencing" as a soft dep, not a hard `blocked-by` — the dep
validator can ship without cross-marketplace support and emit
MAJOR-with-warning for any extra-marketplace edge until the
allowlist arrives.)

## 6. File list

NEW files:

| Path | Purpose |
|------|---------|
| `scripts/validate_dependencies.py` | Main validator (CLI + importable) |
| `scripts/cpv_dependency_graph.py` | Graph dataclasses + Tarjan SCC + BFS closure (pure stdlib, no deps on Report/CLI) |
| `tests/test_validate_dependencies.py` | Unit tests for every detector in §5.3 |
| `tests/test_cpv_dependency_graph.py` | Pure graph-algorithm tests (Tarjan, reverse edges, cascade closure) |
| `tests/fixtures/dependency_graphs/` | JSON fixtures: cycle-self, cycle-2, cycle-N, missing-dep, disabled-target, etc. (one fixture per detector) |
| `skills/cpv-batch-dependency-audit/SKILL.md` | New batch skill (per §5.6) |
| `commands/cpv-validate-dependencies.md` | Optional convenience command (calls validate_dependencies.py directly; zero LLM tokens) |

MODIFIED files:

| Path | Change |
|------|--------|
| `scripts/validate_plugin.py` | Wire `validate_dependencies` call after `validate_xref` |
| `scripts/validate_marketplace.py` | Wire bundle-level dep validation; reuse single graph build |
| `scripts/cpv_validation_common.py` | Add `MarketplaceContext` dataclass; extend `Report` only if a new severity helper is needed (likely not — existing `critical/major/minor` cover all dep findings) |
| `scripts/cli.py` | Register `validate-dependencies` subcommand |
| `agents/plugin-validator-agent.md` | New mode `batch_dependency_audit` |
| `commands/cpv-main-menu.md` | Add §3.x entry for "Audit plugin dependencies" |
| `skills/the-skills-menu/SKILL.md` | Add row for new batch skill |
| `tests/test_agent_model_tiers.py` | Update model-tier invariant to include new mode |
| `tests/test_the_skills_menu_invariants.py` | Add row for new batch skill |
| `scripts/cpv_marketplace_input.py` | No change expected — resolver is dep-class-agnostic |
| `tests/scenarios/SCEN-NNN_dependency_cascade.scen.md` | Scenario walking through a marketplace with intentional cycle + missing-dep + disabled-target |

NO change to: `validate_security.py`, `validate_skill.py`,
`validate_skill_comprehensive.py`, `validate_cache.py`,
`validate_telemetry.py`, `cpv_skillaudit_native.py`,
`cpv_skill_scanner.py`, `cpv_sbom_writer.py` — none of them touch
the dep graph.

## 7. Test scenarios

Each scenario is a self-contained fixture under
`tests/fixtures/dependency_graphs/<scenario-id>/` with a
`marketplace.json` + N `plugins/<name>/plugin.json`. The scenario's
expected output (severity counts, finding shapes) is pinned in
`tests/test_validate_dependencies.py::test_scenario_<id>`.

| # | Scenario | Expected | Notes |
|---|----------|----------|-------|
| 1 | Valid acyclic graph, 5 plugins, 6 edges | 0/0/0/0 + 0 warnings | Baseline pass case |
| 2 | Self-dep: `A.deps = [A]` | 1 CRITICAL | Self-edge classification |
| 3 | Length-2 cycle: `A.deps=[B], B.deps=[A]` | 1 CRITICAL | Reciprocal-edge fast path |
| 4 | Length-4 cycle: `A->B->C->D->A` | 1 CRITICAL with `cycle_members=[A,B,C,D]` | Tarjan SCC enumeration |
| 5 | Two independent cycles | 2 CRITICAL | One finding per SCC |
| 6 | Missing dep (no marketplace ctx): `A.deps=[ghost]` | 1 MAJOR | Standalone severity tier |
| 7 | Missing dep (with marketplace ctx): same | 1 CRITICAL | Marketplace-context severity tier |
| 8 | Disabled-target dep: `A.deps=[B]`, `B.enabled=false` | 1 MAJOR | v2.1.143 cascade case |
| 9 | Cross-marketplace dep, no allowlist | 1 MAJOR | Composes with TRDD-20108ab7 |
| 10 | Cross-marketplace dep, in allowlist | 0 findings | Allowlist honoured |
| 11 | Component-name shadow: `A.deps=[my-skill]`, no plugin `my-skill`, but plugin `B` declares skill `my-skill` | 1 MAJOR with hint "did you mean plugin 'B'?" | Author-aid hint |
| 12 | Duplicate dep entry: `A.deps=[B, B]` | 1 MINOR | Dedup advice |
| 13 | High-fanin: `core` is dep of 8 plugins, threshold=5 | 1 INFO, 1 MAJOR (transitive cascade exceeded) | INFO informational, MAJOR for threshold breach |
| 14 | Transitive cascade boundary: fanin=5, threshold=5 | 1 INFO, 0 MAJOR | Off-by-one boundary test |
| 15 | Diamond: `A->B->D, A->C->D` (legal DAG) | 0 findings | DAG not a cycle |
| 16 | Empty deps `dependencies: {}` | 0 findings | No-op case |
| 17 | Missing `dependencies` key entirely | 0 findings | Optional field |
| 18 | Bare-list shape `dependencies: [A, B]` | normalised same as object shape | Both shapes accepted |
| 19 | Mixed scopes: dep across user/project/local — explicitly REJECTED with note "out of scope, see §4" | n/a | Documents non-goal |
| 20 | Plugin with no `plugin.json` (manifest absent) | propagated as CRITICAL from validate_plugin, dep validator skipped | Defensive guard |
| 21 | Very large marketplace: 200 plugins, 500 edges, no cycles | <2 sec wall, 0 findings | Performance budget |
| 22 | Pathological cycle: 50-node SCC | 1 CRITICAL with full member list | Stress test, ensures no truncation |
| 23 | Component shadow + missing dep AND in allowlist | finding triage order: missing-dep first, then shadow demoted to hint | Finding-priority correctness |
| 24 | Marketplace bundle with one disabled plugin and three of its dependents enabled | 3 MAJOR (one per dependent) | v2.1.143 cascade case at scale |

Performance gate: scenario #21 runs in <2 s on M-series Mac; the
graph builder must not re-parse any `plugin.json` it has already
seen in the marketplace scan.

## 8. Acceptance criteria

1. `uv run scripts/validate_dependencies.py <plugin>` exits 0 on a
   valid graph and >0 on any CRITICAL/MAJOR finding. Severity-count
   summary table emitted via shared
   `save_report_and_print_summary`.
2. `validate_plugin.py` calls the new validator automatically when a
   plugin's `plugin.json` declares a `dependencies` field. Plugins
   without deps incur **zero added cost** (the validator short-
   circuits when `deps == ()` and no marketplace context is in
   play).
3. All 24 scenarios in §7 pass.
4. CPV self-scan (`uv run scripts/validate_plugin.py
   claude-plugins-validation`) remains 0/0/0/0 + 0 warnings.
5. Batch skill `cpv-batch-dependency-audit` accepts the same input
   grammar as the rest of the v2.101.0 batch family (single plugin
   / URL / marketplace / list / `@listfile` / mixed). Verified by
   reusing `tests/test_cpv_marketplace_input.py` parametrisation.
6. CI green: `ci.yml`, `release.yml`, `notify-marketplace.yml`.
7. README updated with a one-paragraph blurb in the "What CPV
   checks" section.
8. Backward compatibility: existing plugins with no `dependencies`
   key behave identically to today (no new findings, no new
   warnings, no schema migration).
9. Idempotence: running the validator twice on the same plugin
   produces byte-identical reports (no temp-file timestamps in
   output, no order-of-iteration leakage).
10. The validator's CLI honours `--format json` and emits a stable
    schema (one entry per finding, fields: `severity, rule_id,
    detector, plugin, dep, cycle_members?, cascade_size?, hint?`).
    Schema versioned via `schema_version: 1` so downstream agents
    can detect future changes.

## 9. Security considerations

- The validator only reads `plugin.json` files — no code execution,
  no network. Defends against malicious dep-graph payloads (very
  long names, deeply nested JSON, BOM-prefixed files) by reusing
  the hardened JSONC parser from `cpv_management_common.py`.
- Graph algorithms are O(V+E) worst-case. A pathological
  marketplace with 10000 plugins and 100000 edges completes in
  <30s on commodity hardware (well within the 600 s per-call
  budget LLM Externalizer uses for comparison). No DoS surface.
- Cycle reporting emits the SCC verbatim. If a malicious
  marketplace declared a 1M-node cycle, the report would be 1M
  lines. The reporter caps per-finding payload at 1000 cycle
  members and emits `... (N members truncated)` for the remainder.
- The validator does NOT pretty-print or echo `plugin.json`
  contents into the report — only the structured fields it
  consumed. Defends against the "report contains untrusted
  long-form text" anti-pattern.

## 10. Sequencing (soft deps with other TRDDs)

| TRDD | Relationship | Notes |
|------|--------------|-------|
| TRDD-20108ab7 (cross-marketplace allowlist) | Soft dep — this TRDD can ship without it and emit MAJOR-with-warning for extra-marketplace edges; gains CRITICAL severity for unallowlisted edges once 20108ab7 lands | Not a `blocked-by:` |
| TRDD-25b9be90 (ghost-agent dispatch) | Orthogonal — that one finds dynamic dispatch by string literal; this one finds declared dep cycles | No interaction |
| TRDD-0028dd34 (hook-validator runtime deps) | Orthogonal — that one tracks `npm install` shell deps in hook scripts; this one tracks plugin-graph deps | No interaction |
| Existing `validate_xref.py` | Hard prerequisite — must run first so broken backtick refs are reported before they confuse dep-resolution hints | Already enforced by source-order in `validate_plugin.main` |

## 11. Follow-ups (deferred, NOT part of this TRDD)

- Version-range satisfaction (`>=1.0.0`, `^2.0.0`). Reuses the
  graph but layers `semver` matching on top. Separate TRDD when
  CC stabilises the version-spec grammar.
- Cross-scope dep resolution (user / project / local). Requires
  CC to clarify whether scope-crossing deps are even legal.
- IDE diagnostics integration — emit dep findings as LSP-style
  diagnostics so editors can underline the offending dep entry.
- Auto-fix proposals — for cycle findings, suggest the minimum
  edge set to cut (NP-hard in general but tractable for typical
  marketplace sizes).
- Dependency-graph visualisation — emit a `.dot` or Mermaid graph
  for human review.

## 12. Cross-references

- **CPV validators that share infrastructure**:
  `scripts/validate_xref.py`, `scripts/validate_marketplace.py`,
  `scripts/validate_hook_precedence.py`,
  `scripts/cpv_validation_common.py`,
  `scripts/cpv_management_common.py`.
- **CPV constants / lookups**: `BUILTIN_AGENTS` (validate_xref.py),
  `marketplace_resolver` (validate_marketplace.py),
  `SKILL_REF_PATTERN` (validate_xref.py).
- **CC v2.1.143 changelog**: the disable-cascade enforcement entry
  (record exact wording during implementation; add to plugin
  docs).
- **Related TRDDs**: TRDD-20108ab7, TRDD-25b9be90, TRDD-0028dd34.
- **Shared report machinery**: `save_report_and_print_summary` and
  `print_compact_summary` in `cpv_validation_common.py`.
- **Batch skills family** (v2.101.0 — `cpv-batch-validate`,
  `cpv-batch-security-audit`, `cpv-batch-caching-audit`,
  `cpv-batch-caching-optimize`, `cpv-batch-fix`,
  `cpv-batch-validate-and-fix`, `cpv-batch-full-scan-and-fix`):
  new batch skill follows the same input grammar + same
  orchestrator dispatch pattern.

## 13. Notes for the implementer

- Tarjan's SCC has a stable iterative implementation; do NOT use
  the recursive form (Python recursion limit will trip on
  500-node graphs). Reference:
  `cpv_dependency_graph.tarjan_scc(nodes, edges)`.
- Use `frozenset` and tuples in `PluginNode` so the graph is
  hashable and trivially diff-able between two builds (for cache
  invalidation when the marketplace is re-scanned).
- The `MarketplaceContext` should be built ONCE per CLI invocation
  and threaded through every per-plugin validator that needs it —
  identical to how `ScannerCache` is plumbed in
  `cpv_lint_engine.py`.
- All new fixture JSONs MUST be lint-clean under the existing
  `validate_marketplace.py` so they don't false-positive in the
  CPV self-scan when they live in `tests/fixtures/`.
- Add a `pragma: no skillaudit` comment in any test fixture that
  intentionally contains payloads the security scanner would
  otherwise flag — the dep validator's fixtures should not trip
  unrelated detectors.
