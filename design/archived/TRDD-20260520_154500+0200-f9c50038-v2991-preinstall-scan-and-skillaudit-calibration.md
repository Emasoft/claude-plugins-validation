---
trdd-id: f9c50038-c4cb-4790-a089-dea94afa4d05
title: v2.99.1 pre-install scan command + skillaudit calibration + mandatory pipeline hookup
column: complete
created: 2026-05-20T15:45:00+0200
updated: 2026-08-25T17:25:14+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-f9c50038 — v2.99.1 pre-install scan + skillaudit calibration

## Source

User request (verbatim): *"make sure to create a special command and remote script to scan any skill or plugin before installing it, so that it will not add those dangerous things to the user system... prevention is better, since you never know what hidden things can be left on the system by those things..  make the scanner mandatory in every validation scan.. test it verify the plugin is not giving false positives.. and calibrate the reports to align with our security system.. but keep the classification of the threats or integrate it, since it has additional categories we lacked.."*

Follow-up directive (verbatim): *"remember that when you fix false positives you must not remove the rule.. you must improve the rule and add further contextual checks to better discriminate real threats from false ones. multiple heuristic and improved regexes can work, but you must always be better safe than sorry. so if in doubt even after all the additional checks, you must still report the thing as suspicious in the warnings. Also the fix agents and security agents (or the security skills, since we are now in the-skills-menu paradigm) must further check via agents the most suspicious warnings to disambiguate or verify/deny."*

## Scope

Five concurrent changes:

1. **`/cpv-pre-install-scan` command + backing script** — a new top-level slash command (`commands/cpv-pre-install-scan.md`) + Python script (`scripts/cpv_pre_install_scan.py`) that scan any skill / plugin / marketplace BEFORE it lands in `~/.claude/plugins/cache/`. Sandboxed via `tempfile.mkdtemp()`, never executes target code, runs the full CPV pipeline including MANDATORY native skillaudit.

2. **Skillaudit in `validate_plugin.py` pipeline** — Check 27 now runs from `validate_plugin.py::main()` (not only from `validate_security.py::main()`). The `_run_skillaudit_native` helper arms `_set_cpv_self_scan`, applies the same filter chain validate_security uses, and respects gitignore during self-scan.

3. **Three-way confidence classifier** — `_should_suppress` is now a thin wrapper around `_confidence` which returns `suppress` / `demote` / `keep`. Per the user's "better safe than sorry" directive: matches that are LITERALLY impossible (placeholder tokens) get suppressed; matches with reasonable documentation context (markdown tables, data-only fenced blocks, short-shell-token substrings, Python docstrings, GitHub Actions context expressions) get DEMOTED to NIT severity with a ⚠ marker rather than silently dropped. Reviewers and the downstream security skills/agents can triage the ⚠ findings.

4. **Threat-category prefix in messages** — `[skillaudit:<category> <rule_id>] <name>` so reviewers see the threat domain at a glance. Skillaudit ships 21 categories CPV didn't have before (`agent_manipulation`, `authentication`, `code_execution`, `credential_reference`, `credential_theft`, `crypto_theft`, `cryptography`, `data_exfiltration`, `denial_of_service`, `evasion`, `filesystem`, `injection`, `network`, `obfuscation`, `persistence`, `privilege_escalation`, `prompt_injection`, `reconnaissance`, `resource_abuse`, `supply_chain`, `tool_manipulation`).

5. **Rule pattern improvements** — added `\b` word boundaries to CMD_INJECTION patterns 6/7/8 in `rules/skillaudit_patterns.json` so `ls`/`id`/`cat` no longer match as substrings inside `skills`/`validation`/`concatenate`. Per the user's directive, NO RULES were removed — only the regex patterns were strengthened with word-boundary anchors.

## False-positive calibration corpus

Self-scan on CPV: **0 CRITICAL, 0 MAJOR, 2 MINOR, 1 NIT** (was 0/0/0/0 pre-hookup — the 2 MINOR + 1 NIT are calibrated demoted findings visible for agent triage).

External scan on Emasoft/token-reporter v1.11.2 (our own clean plugin): finds the real `curl -LsSf https://... | sh` install pattern in README.md as MAJOR (not CRITICAL — markdown context demotes from the bash-fence uplift). The plugin author can document or refactor; the security agents can verify.

Self-scan eligibility additions in this release (all hash-anchored):

- `rules/*.json` — CPV's own pattern catalogs
- root-level docs: `README.md`, `CHANGELOG.md`, `SHIPLOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `code_of_conduct.md`, `support.md`
- `references/**/*.md` (CPV ships canonical-pipeline-migration-checklist.md etc. at the repo root)
- `design/audits/**/*.md`
- `.github/workflows/*.yml` (CPV's own CI/release/notify-marketplace workflows)
- Additional script names in `is_validator_script`: `format_menu.py`, `remote_validation.py`, `spec_rule_extractor.py`, `add_dependencies.py`, `update_marketplace_metadata.py`, `agent_emission_audit.py`, `fixture_grid_generator.py`, `cpv_vs_cli_diff.py`

## Iron-rule preservation

- No `CPV_NO_SKILLAUDIT` / `CPV_SKIP_SKILLAUDIT` / `SKILLAUDIT_SKIP` / `PLUGIN_SKIP_SKILLAUDIT` env-var is honored.
- Missing rule catalog → `report.critical(...)` ("scan could not run — packaging integrity issue").
- The publish-time bypass guard (`PLUGIN_SKIP_*`, `CPV_SKIP_*`, `SKIP_*`, `NO_VERIFY`) still catches any synthetic skillaudit-skip name.
- Demoted findings are NEVER silently dropped — they emit at NIT with a ⚠ marker so the downstream security agents/skills triage them.

## Tests

`tests/test_skillaudit_v299_calibration.py` (24 new tests):

- Three-way confidence classifier (suppress / demote / keep) on placeholder, substring shell tokens, markdown tables, GitHub Actions SSTI, Python docstrings.
- Demoted findings stay visible at NIT with ⚠ marker; category prefix in messages.
- `validate_plugin.py` invokes `_run_skillaudit_native` after `validate_telemetry` and documents MANDATORY status.
- CMD_INJECTION patterns now have `\b` word boundaries.
- `/cpv-pre-install-scan` command + backing script exist, document iron rule + sandbox, never execute target code, never write to `~/.claude/plugins/cache/`.
- Suppression frozensets (`_MD_TABLE_SUPPRESSED_RULES`, `_DATA_LANG_FENCES`, `_SHORT_SHELL_TOKENS`) stay populated.

Updated:

- `tests/test_consolidation_v211.py` allowed-set bumped 3→4 commands.
- `tests/test_menu_unification_v290.py` allowed-set bumped 3→4 commands.
- `tests/test_menu_visibility.py` allowed-set bumped 3→4 commands.

Each test's docstring now references TRDD-84525d4a (v2.99.1) explaining why the pre-install gate is a 4th top-level command.

## Acceptance

- [x] `/cpv-pre-install-scan` slash command exists + executes its backing script
- [x] Backing script never executes target code, never writes to `~/.claude/plugins/cache/`
- [x] Skillaudit native scan runs from `validate_plugin.py::main()` (not only `validate_security.py`)
- [x] Three-way confidence classifier in `cpv_skillaudit_native._confidence`
- [x] Demoted findings emitted at NIT with ⚠ marker
- [x] Threat-category prefix in message format
- [x] CMD_INJECTION patterns 6/7/8 now have `\b` word boundaries
- [x] Self-scan: 0 CRITICAL, 0 MAJOR, ≤5 MINOR, ≤5 NIT
- [x] Full test suite: 5485 passed, 1 skipped

## Lesson

When a security scanner produces false positives, the temptation is to silence the rule. The user's directive is more durable: improve the rule (better regex, more contextual heuristics) AND keep the finding visible at a lower severity ("demote, not drop"). The downstream security agents — those running on opus — are the right place to perform the LLM-based disambiguation a regex cannot. Static analyzers should NOT make the final "this is benign" call; they should rank the certainty and hand uncertain matches to the agents.

## Approval log

- 2026-08-25T17:25:14+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED — commands/cpv-pre-install-scan.md live; card status already completed (batch_ac)
