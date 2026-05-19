# CPV Finding Codes Registry

Every CPV finding emits a stable `RC-<FAMILY>-<NUMBER>` code so consumers
can filter, route, and auto-fix programmatically. This file is the
canonical registry. Codes are immutable once published — superseded
codes get a `[DEPRECATED]` tag but the number is never reused.

## Format

```
RC-<FAMILY>-<NUMBER>   |   Severity   |   Description
```

- `<FAMILY>` — short kebab-case category (e.g. `GHOST-DISPATCH`, `STRIP-GITMODULES`).
- `<NUMBER>` — zero-padded three digits, monotonically allocated per family.

## Ghost-agent dispatch (TRDD-25b9be90)

Findings emitted by `scripts/validate_xref.py` when a `Task()` /
`subagent_type:` literal references an agent that doesn't exist.
Silent-failure class: at runtime the dispatch is a no-op, the calling
skill thinks it spawned a worker, nothing happens.

| Code | Severity | When emitted |
|------|----------|--------------|
| `RC-GHOST-DISPATCH-001` | **CRITICAL** | Literal reference (quoted or kebab-case bare) to an agent name that resolves to nothing — not a built-in (`general-purpose` / `Explore` / `Plan` / `statusline-setup`), not in the plugin's own `agents/`, not (when scanning user-scope) in `~/.claude/agents/`. Runtime will silently no-op. |
| `RC-GHOST-DISPATCH-002` | MINOR | Dynamic dispatch — the value is a variable reference (e.g. `Task(subagent_type=picked_agent)`), not a literal. CPV cannot statically verify the target; emit a reminder. |
| `RC-GHOST-DISPATCH-003` | NIT | Namespaced reference to a different plugin (e.g. `subagent_type: "other-plugin:remote-agent"`). Cannot statically verify cross-plugin dependencies; the target plugin may or may not be installed at runtime. |

### Resolution algorithm

When validating a reference `ref` against the plugin's `available_agents`
and (optionally) `user_scope_agents`:

1. If `_normalize_subagent_type(ref)` matches a built-in (case/separator-
   insensitive), return `ok`.
2. If `ref` contains `:`:
   - Split into `<ns>:<agent>`.
   - If `<ns>` matches the current plugin's manifest `name`, look up
     `<agent>` in `available_agents` (with v2.1.140 fuzzy match) →
     `ok` / `ok-fuzzy` / `ghost`.
   - Else (different namespace) → `cross_plugin`.
3. If `ref` is bare:
   - Look up in `available_agents` (exact, then v2.1.140 fuzzy) →
     `ok` / `ok-fuzzy`.
   - If `user_scope_agents` provided, look there too.
   - Else → `ghost`.

### Built-in agents allow-list

Verified 2026-05-19 against the Claude Code harness tool listing:

- `general-purpose` — universal catch-all agent
- `Explore` — fast read-only search agent (matched case/separator-insensitively)
- `Plan` — software architect planning agent
- `statusline-setup` — built-in status line config agent

The previous list contained `scout`, `oracle`, `basic`, `task`, `haiku`,
`sonnet`, `opus` — all incorrect: `scout`/`oracle` are user-scope agent
names that don't ship as built-ins; the model names (`haiku`/`sonnet`/
`opus`) are model IDs, not agent types.

## Other finding codes

This registry intentionally only documents codes added or revised by
TRDD-25b9be90. Pre-existing codes (e.g. `RC-MKPL-METADATA-DRIFT`,
`RC-NONSTD-DIR-001`, `RC-STRIP-GITMODULES-IMPORT-FAILED`, `RC-DATA-INSTALLER-001`,
`RC-021`, `RC-110..148`) are documented inline at their emission sites
in `scripts/cpv_validation_common.py`, `scripts/validate_plugin.py`,
and other validator modules.

A future consolidation TRDD may unify all codes into this single
registry — for now, this file is the authoritative reference for the
`GHOST-DISPATCH` family.
