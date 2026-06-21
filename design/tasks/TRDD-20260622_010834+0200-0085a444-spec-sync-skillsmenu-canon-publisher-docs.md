---
trdd-id: 0085a444-d961-4a89-b0fa-e70dc05e3d2e
title: Spec-sync to CC v2.1.185 + the-skills-menu canon enforcement + publisher green-CI hardening + docs refresh
column: dev
created: 2026-06-22T01:08:34+0200
updated: 2026-06-22T01:08:34+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 1
severity: HIGH
effort: XL
labels: [spec-sync, the-skills-menu, canon, publisher, docs, claude-code-specs]
task-type: refactor
parent-trdd: null
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, lint, typecheck]
impacts: [public-api, config-schema, ci-pipeline]
external-refs: ["https://code.claude.com/docs/en/changelog.md", "https://code.claude.com/docs/en/plugins-reference", "https://code.claude.com/docs/en/sub-agents", "https://code.claude.com/docs/en/hooks"]
---

# TRDD-0085a444 — Spec-sync + the-skills-menu canon + publisher green-CI + docs

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-22

**User directive (verbatim intent):** (1) update ALL docs + README; (2) a NEW plugin is being built and CPV will publish it (build pipeline + register to marketplace) — ensure the PUBLISHER agent (plugin-creator) is perfectly up-to-date, uses the necessary the-skills-menu skills, and sets up a CI that passes WITHOUT errors; the green-CI loop must keep fixing until CI is clean — VERIFY the loop actually works; (3) the-skills-menu architecture must be AUTO-ENFORCED as part of the canon, BUT conditionally — the VALIDATOR must NOT flag a plugin for missing the-skills-menu unless the user invokes migrate/upgrade/publish; when migrate/upgrade/publish IS invoked, ALL of the plugin's agents must be migrated to the-skills-menu system; (4) investigate the recent CC spec changes (10 linked docs) and update ALL validation scripts + skills accordingly — especially subagents-spawning-subagents and whether the no-hooks-in-plugin-agent-frontmatter limit still holds.

**Current state:** Phase 1 DONE (3 deep-dives synthesized, reports in reports/spec-sync/). KEY ANSWERS: (Q1) subagents CAN spawn subagents v2.1.172 depth-5 — capability, no validator change; (Q2) hooks/mcpServers/permissionMode plugin-agent prohibition STILL HOLDS (plugins-ref L72 + sub-agents L236) — CPV correct, KEEP. Phase 2 (apply) IN PROGRESS: A1 (bg, cpv-spark/opus) = cpv_validation_common VALID_TOOLS −TeamCreate/−TeamDelete +5 new tools, VALID_MODELS +fable, env +CLAUDE_CODE_CHILD_SESSION, validate_hook exec-form `args` + script-lint; A2 (bg) = validate_marketplace 4 FPs (sha/registry/displayName/+6 reserved); A3 (inline, DONE) = validate_plugin known_fields +displayName (v2.1.143) + tests/test_spec_sync_d2_plugin_manifest.py (two-sided, run at central-verify).

**NEXT ACTION:** await A1/A2 → central-verify (`CPV_SCAN_CACHE=0 PLUGIN_SKIP_GITHUB_INTEGRITY=1` self-validate `--strict` + ruff + mypy scripts/ + serial full suite) → regen self-hashes LAST → ship Increment A (version bump, publish.py) → Phase 3 → Phase 4.

**PHASE 3 RECON (conflict-free, done 2026-06-22 — mostly VERIFICATION not greenfield):**
- the-skills-menu enforcement sites = generate_plugin_repo.py (canon gen), add_component.py (`add_component.py:109` TRDD-9dd64dbf — acts only "when a plugin HAS ADOPTED the-skills-menu"), validate_skill_comprehensive.py. NO validator finding/rule for "missing the-skills-menu" → user's "stay silent by default" looks ALREADY satisfied. VERIFY by reading the actual logic (claim-verification) — confirm no default finding fires.
- plugin-creator (PUBLISHER) ALREADY has the green-CI loop: §"CI-green guarantee phase — MANDATORY (publish, then LOOP UNTIL CI IS GREEN)" (L118-129), no hardcoded cap, oscillation-bounded via cpv_fix_loop_state.py, transient-rerun; lists `the-skills-menu` in skills (L10); "Done = green CI" (L37). The v2.140/141 ci-preflight work targeted exactly the user's "CI fails despite local-green" complaint.
- Green-CI loop wired in 8 agents (marketplace-fixer, cpv, plugin-creator, plugin-leaks-preventer, plugin-diagnoser, plugin-devitalizer, cpv-doctor-agent, plugin-fixer).
- Phase 3 work = (i) VERIFY validator stays silent on missing the-skills-menu by default; (ii) VERIFY the migrate/upgrade/publish PATH (standardize/plugin-creator/plugin-fixer) migrates ALL plugin agents to the-skills-menu when invoked; (iii) VERIFY plugin-creator's green-CI loop + ci-preflight integration genuinely closes the local-vs-CI gap for the new plugin; close any gap found.

## CHANGELOG DELTA (CC v2.1.121 → v2.1.185, fetched 2026-06-22)

**Answers the user's 2 flagged questions:**
- **Subagents CAN spawn subagents** — v2.1.172, up to 5 levels deep. (Old limitation LIFTED; any CPV rule/doc claiming otherwise must update.)
- **Hooks-in-plugin-agent-frontmatter** — NOT settled by changelog; v2.1.153 shows agent-frontmatter `mcpServers` is now honored-under-policy (not stripped). MUST re-verify the "plugin agents MUST NOT have hooks/mcpServers/permissionMode" rule against the authoritative plugins-reference (D1 owns this).

**Hooks:** NEW event `MessageDisplay` (v2.1.152). NEW fields: `args: string[]` exec-form (v2.1.157); output `terminalSequence` (v2.1.141); SessionStart `reloadSkills:true` + `hookSpecificOutput.sessionTitle` (v2.1.157); Stop/SubagentStop `hookSpecificOutput.additionalContext` (v2.1.145/163); PostToolUse `continueOnBlock` config (v2.1.152); Stop/SubagentStop INPUT `background_tasks` + `session_crons` (v2.1.145).
**Tools:** `TeamCreate`/`TeamDelete` REMOVED (v2.1.178); implicit team via Agent `name` param under `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`; `Tool(param:value)` permission-rule syntax e.g. `Agent(model:opus)` (v2.1.178); skills/commands `disallowed-tools` frontmatter (v2.1.152); multiple `Agent(...)` types in `tools:` no longer dropped (v2.1.142).
**plugin.json / marketplace:** `defaultEnabled:false` in plugin.json or marketplace entry (v2.1.157); `skipLfs` on github/git marketplace sources (v2.1.153); plugin dependency enforcement (v2.1.143); root-level SKILL.md → surfaced as skill (v2.1.142); plugins in `.claude/skills` auto-load + `claude plugin init` (v2.1.157); nested `.claude/skills` dir-qualified `<dir>:<name>` (v2.1.174).
**Env vars:** `ANTHROPIC_WORKSPACE_ID` (v2.1.141); `CLAUDE_CODE_PLUGIN_PREFER_HTTPS` (v2.1.141); stdio MCP subprocs get `CLAUDE_CODE_SESSION_ID`+`CLAUDECODE=1` (v2.1.157).
**MCP/LSP in plugins:** subagent-frontmatter mcpServers now honor `--strict-mcp-config`/managed policies (v2.1.153); `mcp__*` in subagent `disallowedTools` no longer ignored (v2.1.153).

## PLAN (4 phases)

- **Phase 1 — Spec investigation (IN PROGRESS):** D1/D2/D3 deep-dives verify authoritative specifics → per-validator change-list.
- **Phase 2 — Apply validator/skill updates:** validate_hook (events+fields), validate_agent (hooks/mcpServers verdict + tools), validate_command/validate_skill (disallowed-tools), validate_plugin (defaultEnabled), validate_marketplace (skipLfs), env-var lists, tools list (TeamCreate/Delete). FN-safe, two-sided tests, update CLAUDE.md spec facts.
- **Phase 3 — the-skills-menu conditional canon + publisher:** (a) standardize/migrate/upgrade/publish agents migrate ALL plugin agents to the-skills-menu; (b) the VALIDATOR stays SILENT about missing the-skills-menu unless those agents run (no new default finding); (c) harden plugin-creator (publisher) — uses the-skills-menu skills, sets up passing CI, runs the green-CI loop until clean — VERIFY the loop on a real plugin.
- **Phase 4 — Docs + README refresh** across the whole plugin.

## Load-bearing facts
- CPV's last spec sync ≈ CC v2.1.121 (CLAUDE.md Hooks/Tools sections). Current target ≈ v2.1.185.
- the-skills-menu machinery: skills/the-skills-menu + skills/the-skills-menu-create + commands/the-skills-menu-create.md; canon = standardize_plugin.py + generate_plugin_repo.py + validate_plugin.py drift.
- Publisher = agents/plugin-creator.md (creates→publishes→watches CI green). Green-CI loop wiring (v2.141.0) lives in plugin-fixer/marketplace-fixer; verify plugin-creator has/uses it.
- Conditional-finding nuance is the crux of (3): missing the-skills-menu is NOT a default validator finding — only the migrate/upgrade/publish PATH enforces it.
- Regen self-hashes LAST after CLAUDE.md/agent/skill edits; self-validate CPV_SCAN_CACHE=0 PLUGIN_SKIP_GITHUB_INTEGRITY=1; no line-start markdown-poison.

## Notes and lessons learned
