---
trdd-id: 479cde0c
title: CPV v2.22.0 Claude Code compliance sweep
column: complete
updated: 2026-08-25T17:25:22+0200
---

# TRDD-479cde0c — CPV v2.22.0 Claude Code compliance sweep

**TRDD ID:** `479cde0c-c781-4bfb-b62a-fbf40e91523f`
**Filename:** `design/tasks/TRDD-479cde0c-c781-4bfb-b62a-fbf40e91523f-v2.22-claude-code-compliance.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done (2026-05-10) — fully shipped across v2.22.0 → v2.22.3.
**Baseline:** CPV v2.21.3 (aligned with Claude Code v2.1.109)
**Target:** CPV v2.22.0 aligned with Claude Code v2.1.112 (current) + spec-complete against the
26 reference docs audited on 2026-04-17.

## Closing summary (2026-05-10)

All NOW + NEXT-RELEASE items in this TRDD landed in the v2.22.x release train —
**no items remain outstanding**. Items in the DEFERRED section were intentionally
moved to dedicated follow-up TRDDs (channel source-code audit shipped as the
new `channel-source-security` reference under the semantic-validation skill in
v2.22.3; hook output JSON schema split into TRDD-cf57bf86; cross-hook
precedence into TRDD-feb72fa4 — both implemented as standalone validators in
v2.22.3).

Closing commits:

| Item-block | Version | Commit |
|------------|---------|--------|
| NOW (1–24) — main spec-alignment sweep | v2.22.0 | `605b45f` (feat) + `8a4e78a` (release) |
| NEXT-RELEASE — `@path` imports, rules `paths:`, `Agent()` grammar, `defaultMode`, marketplace fuzzy-impersonation, `bin/`+`monitors/` recognition, version-in-both-places | v2.22.1 | `6d49c52` (feat) + `4fcc2e3` (release) |
| NEXT-RELEASE — pass-2 audit: agent color enum, SessionStart hook-type lockdown, per-plugin source-type cleanup, strictKnownMarketplaces narrow, agent-frontmatter hook event/type expansion, `asyncRewake`/`async` contradiction | v2.22.2 | `88c0e8c` (feat) + `6a62236` (release) |
| NEXT-RELEASE — exhaustive pass-2 follow-up: 5 new validators (`validate_hook_output.py`, `validate_hook_precedence.py`, `validate_telemetry.py`), 13 marketplace fixes, 6 plugin/agent fixes, channel-source-security pillar in semantic-validation skill | v2.22.3 | `54ff533` (feat) + `26e482a` (release) |

Test-count progression for the TRDD: 2088 → 2129 (+41 in v2.22.0) → 2171
(+42 in v2.22.1) → 2180 (+9 in v2.22.2) → 2336 (+156 in v2.22.3). Current
test count on master: 4459 passing, 5 skipped. All v2.22-era tests still
green.

Success-criteria audit (TRDD §"Success criteria", lines 109–120):

- [x] +40 new tests passing — actually +248 across v2.22.0–v2.22.3.
- [x] `uv run ruff check scripts/ tests/` clean (verified 2026-05-10).
- [x] `uv run mypy scripts/` clean for v2.22-era code; the lone outstanding
      `tomli` `import-not-found` in `scripts/cpv_lint_engine.py:1052` was
      introduced by the unrelated `feat(security+xplat)` work in commit
      `85fdc0ff` (2026-05-08), well after this TRDD shipped.
- [x] Skill 1,200-char description + 400-char `when_to_use` (combined 1,600)
      fires MAJOR; 1,400-char description alone does NOT — verified by
      `tests/test_validate_skill_comprehensive.py::TestPass2SkillFixes`.
- [x] `dependencies: ["helper-lib", {"name": "vault", "version": "~2.1.0"}]`
      validates clean — `TestV222PluginSchema::test_dependencies_*`.
- [x] `pathPattern` settings source type validates clean — present in
      `cpv_validation_common.VALID_SETTINGS_SOURCE_TYPES` at line 506.
- [x] `xhigh` effort validates clean on skills + agents — present in
      `cpv_validation_common.VALID_EFFORT_VALUES` at line 603.
- [x] `${CLAUDE_PLUGIN_DATA}/foo.py` does NOT emit unknown-substitution-token
      finding — handled in `validate_hook.py` substitution recognition.

This TRDD is closed. Future Claude Code spec-alignment work should branch a
fresh TRDD against the current Claude Code release pointer (v2.1.121 today).

## Source audit reports

All findings in this TRDD come from five spec-audit reports written by research agents:

1. `docs_dev/spec-audit-1-changelog-20260417-163011.md` — changelog + features-overview + CLI
   reference. 8 findings, 1 breaking change (`xhigh` effort rejection), 1 removal
   (`--enable-auto-mode`).
2. `docs_dev/spec-audit-2-plugins-20260417-163141.md` — plugins + plugins-reference +
   marketplaces + discover-plugins + plugin-dependencies. 16 fixes spanning CRITICAL (3),
   MAJOR (5), MINOR (4), NIT (4).
3. `docs_dev/spec-audit-3-elements-20260417-163226.md` — hooks + hooks-guide + sub-agents +
   skills + tools-reference. 10 element changes including the single-biggest correctness bug
   in v2.21.3: skill description cap is 1,536 chars on `description + when_to_use` combined,
   not 1,024 on `description` alone.
4. `docs_dev/spec-audit-4-settings-20260417-163133.md` — claude-directory + permission-modes +
   env-vars + memory + server-managed-settings. 9 taxonomy changes: 3 env vars, 2 MANAGED_ONLY
   keys, a new MANAGED_ONLY_NESTED_KEYS constant for 2 keys, the `memory` agent field, and
   `@path` import validation in CLAUDE.md.
5. `docs_dev/spec-audit-5-new-features-20260417-163011.md` — channels + channels-reference +
   scheduled-tasks + monitoring-usage + costs. 4 new validators recommended, plus 5 extensions
   to existing validators.

## Scope for v2.22.0 (not all findings land in this release)

### NOW — in v2.22.0

1. **CRITICAL: Skill description cap fix.** `validate_skill_comprehensive.py:81` must change
   `MAX_DESCRIPTION_LENGTH = 1024` to `1536` AND must test the combined length of
   `description + when_to_use` against 1,536 — not `description` alone. Any skill whose
   `description` is between 1,025 and 1,536 chars is being wrongly flagged as MAJOR in v2.21.3.
2. **CRITICAL: `dependencies` plugin.json field.** Add to `known_fields` in `validate_plugin.py`
   AND add a structural validator (bare string OR `{name, version?, marketplace?}`) with a
   minimal syntactic semver-range check.
3. **CRITICAL: `pathPattern` settings source type.** Add to
   `cpv_validation_common.VALID_SETTINGS_SOURCE_TYPES` + required-field map.
4. **MAJOR: `xhigh` effort level.** Accept it in `effort` validation for skill, agent, and CLI
   surfaces. Breaking for plugins using v2.1.111+ skills/agents otherwise.
5. **MAJOR: `PushNotification` tool.** Add to `VALID_TOOLS` (v2.1.110 new tool).
6. **MAJOR: New settings keys.** Add to recognized-keys surface: `tui`, `autoScrollEnabled`,
   `disableSkillShellExecution`, `otelHeadersHelper`, `autoMemoryEnabled`, `cleanupPeriodDays`,
   `claudeMdExcludes`, `outputStyle`.
7. **MAJOR: `subagentStatusLine` in plugin-root settings.** Add to
   `validate_plugin.py` recognized plugin-shipped settings keys (`{agent, extraKnownMarketplaces,
   subagentStatusLine}`).
8. **MAJOR: Taxonomy — managed keys.** Add `forceLoginMethod`, `forceLoginOrgUUID` to
   `MANAGED_ONLY_KEYS` in `cc_scope_rules.py`. Add new `MANAGED_ONLY_NESTED_KEYS` frozenset
   containing `("permissions", "disableAutoMode")` and
   `("permissions", "disableBypassPermissionsMode")`. Wire through `_flag_managed_only_nested_*`
   in both local and project scope validators.
9. **MAJOR: `memory` agent frontmatter field** — accept values `project`, `local`, `user` per
   sub-agents.md + claude-directory.md L374/L656. Update `validate_agent.py`.
10. **MAJOR: `isolation` agent enum** — only `"worktree"` is valid per plugins-reference.md:70.
11. **MAJOR: New env vars.** Add `CLAUDE_CODE_REMOTE_SESSION_ID`, `CLAUDE_CODE_TEAM_NAME`,
    `CLAUDE_CODE_TASK_LIST_ID`, `CLAUDE_CODE_DISABLE_CRON`, `CLAUDE_CODE_ENABLE_TELEMETRY`,
    `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`, `CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS`,
    `CLAUDE_CODE_MAX_RETRIES`, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`, `MAX_THINKING_TOKENS`,
    `TRACESTATE`, plus the OTEL_* family (~20 vars) to `VALID_PLUGIN_ENV_VARS` in
    `cpv_validation_common.py`.
12. **MAJOR: `userConfig` structural validator.** Each entry is a dict with optional
    `description` (string), `sensitive` (bool). Keys must be valid identifiers.
13. **MAJOR: `channels` structural validator.** Array of dicts; required `server` must match a
    key in the plugin's `mcpServers`; optional per-entry `userConfig`.
14. **MAJOR: `monitors` entry validator.** Each entry requires `name`, `command`, `description`;
    optional `when` must match `^always$|^on-skill-invoke:[a-z0-9-]+$`.
15. **MINOR: Path-traversal rejection in plugin.json path fields.** Skills/commands/agents/
    hooks/mcpServers/outputStyles/lspServers/monitors values: reject `..` segments.
16. **MINOR: `author.url` acknowledgement** — accept + lightly validate as string URL.
17. **MINOR: `bin/` recognized plugin-root directory** — don't flag as unknown.
18. **MINOR: Substitution tokens** — recognize `${CLAUDE_PLUGIN_DATA}`, `${user_config.KEY}`
    alongside `${CLAUDE_PLUGIN_ROOT}`.
19. **MINOR: `.claude/loop.md`** — scope validators recognize as known file; enforce 25 KB cap.
20. **MINOR: Version-declared-in-both-places** — marketplace entry + plugin.json, warn if diverge.
21. **NIT: `statusline-setup` and `Claude Code Guide`** in `BUILTIN_AGENT_TYPES`.
22. **NIT: Matcher-value completeness** — verify `bypass_permissions_disabled` (SessionEnd),
    `compact` (InstructionsLoaded), all 7 StopFailure errors are in CPV's matcher-value checks.
23. **NIT: `Setup` legacy-event comment** — update from "v2.1.98" to "v2.1.109".
24. **NIT: Remove `--enable-auto-mode`** from any CPV docs/templates.

### NEXT-RELEASE — defer to v2.22.1 / v2.23

- Cross-marketplace dependency allowlist mechanism — requires new marketplace.json field discovery.
- `@path` import validation in CLAUDE.md beyond basic absolute-path + traversal checks
  (recursive load-depth cap of 5, cycle detection).
- `.claude/rules/*.md` frontmatter validator (the `paths:` glob).
- Dedicated `validate_telemetry.py` standalone validator for OTEL supply-chain risks.
- Semantic validation of channel MCP server source (sender gating, permission relay).
- `Agent(name, name, ...)` subagent-spawning grammar parser in `tools` field.
- `permissions.defaultMode` value validation when present in settings.
- `sandbox.enabled` taxonomy entry.

### DEFERRED — future

- Full marketplace impersonation-detector (fuzzy matching on reserved names).
- Strict-mode conflict detector (marketplace entry + plugin.json components).
- Channel `claude/channel/permission` capability source-code auditor.

## Success criteria

- `uv run pytest tests/` shows at least +40 new tests passing (one per new behavior).
- `uv run ruff check scripts/ tests/` clean.
- `uv run mypy scripts/` clean.
- A skill with `description` = 1,200 chars + `when_to_use` = 400 chars (combined 1,600)
  fires a MAJOR; a skill with `description` = 1,400 chars alone does NOT (pre-v2.22 behavior
  was reversed on both).
- A plugin.json with `"dependencies": ["helper-lib", {"name": "vault", "version": "~2.1.0"}]`
  validates clean.
- A settings.json with `{"pathPattern": "/private-hub"}` under `strictKnownMarketplaces`
  validates clean.
- `xhigh` as an `effort` value validates clean on skills and agents.
- A hook command using `${CLAUDE_PLUGIN_DATA}/foo.py` does NOT emit "unknown substitution token".

## Non-goals

- Rebuilding the marketplace layout detection. Layout A and Layout B remain the only supported
  layouts; no new layout documented.
- Third-party MCP source code auditing. That belongs in `semantic-validator` skill.
- Full vixie-cron syntax enforcement beyond the size cap on `loop.md`.

## Approval log

- 2026-08-25T17:25:22+0200 — CLOSED as complete by the CPV session (board drain;
  authority delegated by USER 2026-08-25). SHIPPED v2.22.0-v2.22.3 (batch_ah).
