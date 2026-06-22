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

**INCREMENT A SHIPPED — v2.142.0, CI GREEN (commit 8efa0a0, 2026-06-22).** Phase 2 spec-sync CORE done: cache-cold self-validate 0/0/0/0, ruff/mypy clean, +50 two-sided tests (627 focused pass), publish.py 13 gates green, CI + Release runs green. Feature commit f4ed7f4.

**Phase 2 REMAINDER (optional, LOW — note, do NOT rabbit-hole):** D1 — soften validate_task_tool_prohibition wording (nested spawn is depth-5 capped, not "infinite recursion"; only fires on legacy `context:fork`); terminalSequence output-field (validate_hook_output already has reloadSkills/sessionTitle). D3 — autoUpdate bool type-check on extraKnownMarketplaces; pluginSuggestionMarketplaces value-shape validator; allowedChannelPlugins managed-only key. MEMORY.md doc-counts (28→31 hook events, tools enumeration). All non-FP completeness; revisit only if cheap.

**PHASE 3 IN PROGRESS (Increment B).** V1/V2 verification DONE (reports in reports/spec-sync/): (the-skills-menu) req1 MET — validator SILENT by default (validate_plugin/validate_agent zero refs; only a permissive description accept-clause in validate_skill_comprehensive.py:846-859); req2 PARTIAL — the-skills-menu-create migrator exists + generate_plugin_repo bakes it into NEW plugins, but standardize_plugin.py / plugin-creator-existing-folder / plugin-fixer never invoke it. (publisher) green-CI loop SOUND (log-driven via `gh run view --log-failed`, oscillation-bound, no hardcoded cap) BUT ci-preflight NOT wired into plugin-creator → the local-green-vs-CI-red gap (CI's actionlint + Mega-Linter mypy + CIP-1..5 that publish.py's local gates skip).
DONE INLINE (Gap A + C3/C4): plugin-creator.md (A1 ci-preflight step before first publish.py; A3 oscillation wording → cpv_fix_loop_state multiset; C3 existing-folder the-skills-menu CONDITIONAL-canon enforcement), plugin-fixer.md (C4 migration-exit-contract clause d), plugin-creator-runbook.md (A2 step-13 ci-preflight).
**INCREMENT B SHIPPED — v2.143.0, CI GREEN (feature cd9d7f1, release 7b25795).** Unit-C (standardize_plugin.py `migrate_agents_to_skills_menu` under --force-templates) + plugin-creator/plugin-fixer/runbook edits landed; +12 two-sided tests; cache-cold self-validate 0/0/0/0.

**PHASE 4 (docs) DONE + COMMITTED (0bdb6c0; NOT yet shipped — ships bundled with Increment C).** CLAUDE.md → v2.143.0 (test files 365, ~10076 tests, +v2.142.0/v2.143.0 history entries); README hooks 28→31 events (+MessageDisplay, exec-form args) at both the validator-row and script-table rows + tests badge 8800→10000. Tree is CLEAN.

**⛔ BLOCKED — WEEKLY USAGE LIMIT hit 2026-06-22 (resets 2026-06-23 17:00 Europe/Rome).** Two Gap-B agents died on it: 1st a transient rate-limit (0 work); 2nd the weekly limit at 23 tool-uses with ZERO tracked edits (read/analyze phase only) — so generate_plugin_repo.py / publish.py are UNTOUCHED, tree clean. Did NOT attempt publish.py (a 13-gate run could fail mid-gate under the limit and leave a half-published state).

**NEXT ACTION (post-reset 2026-06-23 17:00) → Increment C / Gap B (Task #89):** add G2c actionlint + G2d mypy to `gen_publish_py`'s `--gate` in scripts/generate_plugin_repo.py — mirror the G2b jscpd probe-then-degrade-WARNING at ~L2259 (VERIFY mypy isn't already in the --gate path first; match the generated ci.yml's mypy target/flags) + the same in CPV's own scripts/publish.py if its gates lack actionlint; new tests/test_canon_gateparity_actionlint_mypy.py (two-sided: emitted-template-contains-blocks, tool-absent→WARNING-not-block, real-error→BLOCK); ruff+mypy+pytest verify; regen self-hashes LAST; cache-cold self-validate 0/0/0/0; then publish.py --minor → 2.144.0 (ships Gap B + the committed Phase-4 docs together). Re-launch the Gap B agent (background, opus, general-purpose) once the limit clears, OR do it inline if subagents are still constrained. The user's 4-part directive is otherwise COMPLETE + SHIPPED: spec-sync (v2.142.0), publisher ci-preflight + green-CI-loop verify + the-skills-menu CONDITIONAL canon (v2.143.0), docs (committed 0bdb6c0). Gap B is the belt-and-suspenders structural completion, not a gap in the directive's core asks.
DEFERRED → Increment C: Gap B (template gate-parity — add actionlint+mypy to gen_publish_py + CPV's own publish.py, probe-degrade-WARNING like G2b jscpd; the structural "generated CI won't fail" fix). NOTE: plugin-creator/plugin-fixer agent-body-length WARNINGs grew (non-blocking; consider a runbook-offload trim).

**INCREMENT B SHIPPED — v2.143.0, CI GREEN (feature cd9d7f1, release 7b25795, 2026-06-22).** Publisher ci-preflight wired (plugin-creator runs `remote_validation.py ci-preflight` before the first publish) + oscillation wording fixed; the-skills-menu CONDITIONAL canon (standardize `--force-templates` now runs `migrate_agents_to_skills_menu`, plugin-creator/plugin-fixer enforce it on the migrate/upgrade/publish path; validator stays SILENT by default — req1 verified MET). cache-cold self-validate 0/0/0/0; +12 two-sided tests; CI+Release green.

**NEXT ACTION → Phase 4 (docs+README — explicit un-done user ask, Stop-hook-flagged):** refresh README.md + the committed CLAUDE.md (version 2.141.1→2.143.0; test-file/test counts; the spec-sync + publisher-ci-preflight + the-skills-menu-canon changes; the Hooks 28→31 + tools −TeamCreate/−TeamDelete/+5 facts). THEN Increment C = Gap B (template gate-parity: add actionlint+mypy to gen_publish_py + CPV's own publish.py, probe-degrade-WARNING like G2b jscpd — the publisher-robustness completion so the GENERATED pre-push hook achieves CI-parity forever).

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
