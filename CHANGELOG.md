# Changelog

All notable changes to the Claude Plugins Validation plugin will be documented in this file.

## [2.12.11] - 2026-04-12

### Features

- Align validators with Claude Code v2.1.98 spec

## [2.12.10] - 2026-04-10

### Miscellaneous Tasks

- Upgrade actions to Node.js 24 compatible versions
- Update uv.lock

## [2.12.9] - 2026-04-10

### Bug Fixes

- Ruff isort import ordering in validate_plugin.py

### Miscellaneous Tasks

- Update uv.lock

## [2.12.8] - 2026-04-10

### Bug Fixes

- Skip archive tests in CI (scripts_dev is gitignored)

### Miscellaneous Tasks

- Update uv.lock

## [2.12.7] - 2026-04-10

### Hooks

- Verify publish.py via process ancestry, not env vars

## [2.12.6] - 2026-04-10

### Hooks

- Pre-push always runs full validation — never skips

## [2.12.5] - 2026-04-10

### Hooks

- Skip pre-push validation on tag-only pushes

## [2.12.4] - 2026-04-10

### Bug Fixes

- Remove invalid 'hooks' key from plugin.json manifest
- Remove SubagentStop hook — token cost runs as direct script call
- Remove redundant default-path declarations from manifest
- Resolve 19 MINOR validation issues — TOC embeds and SKILL.md improvements
- Resolve last 5 WARNINGs — embed TOC sections for all referenced .md files in SKILL.md
- Exclude _dev directories from lint_files.py (gitignored, not shipped)
- All validators respect gitignore — skip *_dev directories
- Resolve MINOR validation issues in skill SKILL.md files
- Use GitignoreFilter instead of hardcoded skip sets for directory walking
- Broken ref in semantic-validator, script advice in fixer, binary-builds improvements
- Make publish-to-marketplace skill generic for any marketplace repo
- Replace real marketplace names with generic examples in placeholder table
- Add heading to cpv-publish-to-marketplace command (MD041 lint)
- Require ALL TOC headings embedded, not just 2
- Require ALL TOC headings embedded in SKILL.md, not just 2
- Improve TOC validation messages with progressive discovery explanation
- 3 bugs from audit — TOC regex, uv.lock auto-commit, code fence tags
- Embed complete TOC headings in all 5 SKILL.md files, update CHANGELOG
- Comprehensive audit — 9 CRITICAL, 17 MAJOR, 12 MINOR addressed
- Resolve 9 bugs from code auditor self-audit ([#1](https://github.com/Emasoft/claude-plugins-validation/issues/1))
- Add url/headers to known_hook_fields for HTTP hooks
- Rename loop var 'field' to 'field_name' to avoid shadowing dataclasses import (F402)
- Rename remaining 'field' loop vars to 'field_name' to resolve all F402 lint errors
- Upgrade backtick path validation with plugin-internal awareness
- Update LLM Externalizer MCP tool prefix to plugin format
- V2.0.0 — Address 9 audit findings from integration review
- V2.1.0 — Fix 21 audit findings (7 CRITICAL, 14 HIGH)
- 6 bugs from audit — crash prevention + correctness
- 6 template issues from PSS audit — align with canonical pipeline
- Add --body flag to gh secret set + marketplace config instructions
- Apply 8 lessons from rechecker-plugin publish post-mortem
- Pre-push hook must block ALL issues except WARNINGs + mandatory fix loop
- CI workflow template uses uv sync --extra dev + pyyaml in dev deps
- Add errors 9-10 to post-mortem + pipeline rules + agent lessons
- Publish.py template lint errors + CI uv sync --extra dev
- Checkov check ID is CKV2_GHA_1 (not CKV_GHA_1) — template + pipeline rules
- Template lint issues and pytest exit-5 handling
- Bugs found during marketplace publish testing
- Exclude __init__.py from shebang check (false positive)
- Apply all lessons learned from marketplace publish testing
- Update cpv-manage-remote-plugins with scope, smart resolution, marketplace listing
- Audit fixes — broken quoting, table alignment, README count
- Validation blocking install on MINOR/NIT issues (pre-existing bug)
- Update README, skill, and command frontmatter for consistency
- Remove ~/.claude/settings.local.json — not a valid Claude Code location
- Remove --scope local — Claude Code only reads enabledPlugins from user-level
- Restore --scope local with correct precedence semantics
- 7 bugs from deep code audit
- README heading hierarchy — use H2 for parts, H3/H4 for sections
- Remove unnecessary f-string prefix in marketplace workflow template (F541)
- Remove 5 unnecessary f-string prefixes (ruff F541)
- Remove unused imports (sys in manage_doctor, Dict in manage_plugin)
- Eliminate false positives from lint pipeline
- Exclude _dev directories from mypy to prevent duplicate module errors
- Make mypy type warnings non-blocking in lint pipeline (pre-existing issues)
- Resolve all validation issues for clean publish pipeline
- Resolve last mypy type warning in test_consolidation_v211.py
- Resolve 8 validation false positives and parser limitations ([#4](https://github.com/Emasoft/claude-plugins-validation/issues/4))
- Clean known_marketplaces.json on uninstall/remove/doctor
- Doctor --fix now deletes stale ~/.claude/settings.local.json entirely
- Remove agent impersonation check (too many false positives)
- **validate_security:** Resolve mypy type errors in cc-audit findings parser
- Remove trailing space in .serena/project.yml causing yamllint error
- Publish.py tolerates MINOR/NIT validation issues, chmod +x cli.py
- Update all stale references after v2.5.0 Claude Code alignment
- Comprehensive hook validator alignment with official Claude Code hooks reference
- Update test_elicitation_no_matchers for new Elicitation matcher support
- Stale VALID_ENV_VARS in validate_hook.py, version badge, CHANGELOG entries
- Resolve all 61 mypy no-any-return errors via mypy_path config
- Align plugin.json manifest validation with official spec
- Patch KNOWN_MARKETPLACES_FILE in doctor test to prevent host leakage
- Deep audit — 11 issues fixed (parsing, edge cases, version drift)
- Deep code audit — 17 real bugs fixed across 12 scripts

### Documentation

- Update README with 3 missing commands, 4 missing agents/skills, fix directory tree
- V2.0.0 — Update README and CHANGELOG for management integration
- Update cross-references for command consolidation (Step 8)
- Update CHANGELOG, README, and commands for v2.3.0
- Restructure README into Validation + Management sections
- Add table of contents to README
- Update README badges and CHANGELOG for v2.3.2
- Rewrite README for clarity and non-programmer accessibility
- Restructure README into two clear sections
- Restructure README into two clear sections
- Add detailed requirements, Anthropic docs links, and --with pyyaml to uvx commands
- Remove Agent SDK links from Claude Code documentation table
- Trim Claude Code links to just discover-plugins and release notes
- Align LLM Externalizer references with latest plugin update

### Features

- Accurate token cost measurement via transcript parsing, v1.9.1
- CRITICAL check for redundant default-path declarations in plugin.json
- Add --strict to pre-push hook template
- Uniform naming validation across all component types
- Add token optimization guardrails to agents, skills, and commands
- Add publish.py pipeline script (test → validate → bump → commit → push)
- Merge publish scripts into unified pipeline + enforce version bump in pre-push
- Add binary compilation support to plugin pipeline templates
- Add publish-to-marketplace skill with PAT setup and notification pipeline
- Enforce publish pipeline — block direct git push
- Detect backtick references in SKILL.md validation + fix all 37 MINORs
- Add LLM Externalizer MCP instruction to all 8 skill files
- Update LLM Externalizer references — write tools removed, add specific tool guidance
- Align with Claude Code v2.1.0-v2.1.76 changelog — bump to v1.11.0
- Align marketplace validator with official Anthropic spec
- Add deep path and URL validation inside .md files ([#3](https://github.com/Emasoft/claude-plugins-validation/issues/3))
- V2.0.0 — Merge CPM plugin management features into CPV
- V2.1.0 — Phase A+C: 7 new validation rules + creation/standardization components
- V2.1.0 — Phase B.1-B.2: Plugin and marketplace repo generators
- V2.1.0 — Phase B.3-B.4+C+E: Standardizers, docs, version bump
- V2.1.0 — 3 end-to-end publishing commands + enhanced plugin-creator agent
- Rename local-only commands (Step 1)
- Enhance unified commands + create cpv-standardize (Steps 2+4)
- Update agents for command consolidation (Step 7)
- Propagate pipeline rules to ALL plugin creation/setup/fix commands and skills
- Replace manual lint with Mega-Linter v8 in CI templates
- Harden publish pipeline — 8 fixes from rechecker post-mortem
- Align publish.py template with PSS architecture + rename commands
- Rename marketplace commands + add marketplace publish prompt
- Add --scope user|local flag to enable/disable plugin commands
- Smart plugin resolution + project-local scope for enable/disable
- Add /cpv-list-mp-plugins command — list plugins in a marketplace
- Update install/uninstall commands + skill/agent with scope docs
- Rename install/uninstall commands to disambiguate local vs remote
- Doctor --fix mode + uninstall cleans all settings files
- Validator warns when ~/.claude/settings.local.json exists (should not be at user level)
- Add 9 AI-specific security checks to validate_security.py
- Integrate cc-audit external scanner into security validation
- Add uvx CLI entry points for running validators without installing
- Align with Claude Code v2.1.79-v2.1.86
- Align skill validator with official Claude Code skills spec
- Detect broken dynamic context injection backticks in skills
- Warn on potential missing backticks in skill dynamic context injection
- Enforce effort:max requires Opus model in skills and agents
- Add missing skill/agent validation rules from official spec
- Add CLAUDE_PLUGIN_DATA dependency persistence rules

### Miscellaneous Tasks

- Stage pre-rewrite state (grading removal + cost range estimate)
- Bump version to 1.9.2
- Update uv.lock
- Bump version to 1.9.3
- Bump version to 1.9.4
- Add fix-validation skill, enforce 5000 char / 500 line limits, TOC in first 200 chars
- Bump version to 1.9.5
- Bump version to 1.9.6
- Bump version to 1.9.7
- Bump version to 1.9.8
- Bump version to 1.9.9
- Update uv.lock
- Bump version to 1.10.0
- Update uv.lock
- Bump version to 1.10.1
- Update uv.lock
- Bump version to 1.10.2
- Update uv.lock
- Bump version to 1.10.3
- Update uv.lock
- Bump version to 1.10.4
- Update uv.lock
- Bump version to 1.10.5
- Bump version to 1.10.6
- Update uv.lock
- Bump version to 1.10.7
- Bump version to 1.10.8
- Update uv.lock
- Bump version to 1.10.9
- Bump version to 1.11.1
- Update uv.lock
- Bump version to 1.11.2
- Add .claude/ and llm_externalizer_output/ to .gitignore
- Update uv.lock
- Bump version to 1.11.3
- Clean up .gitignore — deduplicate .claude/, add .tldr/ and .tldrignore
- Update uv.lock
- Bump version to 1.11.4
- Update uv.lock
- Bump version to 1.11.5
- Update uv.lock
- Bump version to 1.11.6
- Remove remaining install-plugin references from README and script-templates
- Update uv.lock
- Bump version to 1.11.7
- Update uv.lock
- Bump version to 1.11.8
- Update uv.lock
- Bump version to 1.11.9
- Update uv.lock
- Bump version to 1.11.10
- Update uv.lock
- Bump version to 1.11.11
- Bump version to 1.12.0
- Update uv.lock
- Bump version to 1.12.1
- Update uv.lock
- Bump version to 1.12.2
- Update uv.lock
- Bump version to 1.12.3
- Snapshot before v2.1.1 command consolidation
- Snapshot before bug fixes from audit
- Update uv.lock
- Bump version to 2.3.2
- Update uv.lock
- Bump version to 2.3.3
- Update uv.lock
- Bump version to 2.3.4
- Update uv.lock
- Bump version to 2.3.5
- Clean up rechecker worktree artifacts
- Add .rechecker/ to .gitignore
- Extend .gitignore with tldr session files and rechecker merge-pending files
- Bump version to 2.3.6
- Add Serena project config and update uv.lock
- Align LLM Externalizer refs with v3.2.8
- Bump version to 2.3.7
- Bump version to 2.4.0
- Bump version to 2.4.1
- Bump version to 2.4.2
- Update uv.lock
- Bump version to 2.4.3
- Update uv.lock
- Bump version to 2.4.4
- Update uv.lock
- Bump version to 2.4.5
- Bump version to 2.5.0
- Bump version to 2.5.1
- Bump version to 2.5.2
- Bump version to 2.5.3
- Bump version to 2.5.4
- Update uv.lock
- Bump version to 2.5.5
- Update uv.lock
- Bump version to 2.5.6
- Bump version to 2.6.0
- Bump version to 2.6.1
- Bump version to 2.6.2
- Bump version to 2.6.3
- Bump version to 2.6.4
- Bump version to 2.6.5
- Bump version to 2.7.0
- Bump version to 2.7.1
- Update uv.lock
- Bump version to 2.7.2
- Update uv.lock
- Bump version to 2.7.3
- Bump version to 2.7.4
- Bump version to 2.7.5
- Bump version to 2.7.6
- Bump version to 2.8.0
- Bump version to 2.8.1
- Bump version to 2.8.2
- Bump version to 2.8.3
- Bump version to 2.8.4
- Bump version to 2.8.5
- Bump version to 2.8.6
- Bump version to 2.9.0
- Bump version to 2.9.1
- Bump version to 2.9.2
- Bump version to 2.9.3
- Bump version to 2.10.0
- Bump version to 2.11.0
- Bump version to 2.11.1
- Bump version to 2.11.2
- Bump version to 2.12.0
- Bump version to 2.12.1
- Bump version to 2.12.2
- Bump version to 2.12.3

### Performance

- Move loop-internal constant tuples to module level

### Refactor

- All validators use GitignoreFilter instead of hardcoded skip lists
- Move fix reference files from agents/references/ to skills/fix-validation/references/
- Remove claude-plugin-install.py — now in claude-plugins-management
- Move superseded commands to scripts_dev (Step 1)
- Move 7 obsolete commands to scripts_dev (Step 3)
- Consolidate plugin management into single source of truth

### Security

- Scan AI-facing markdown for secrets, path traversal, exfiltration

### Testing

- Add is_valid_model and changelog-driven tests for v1.11.0
- Fix exit_code_branches assertions after E-001 exit code correction
- Add test for HTTP hook fields not triggering unknown-field warnings
- V2.0.0 — Add 275 tests for management modules
- V2.1.0 — Phase D: 102 tests for new validation rules + generators
- Add 22 tests for v2.1.1 command consolidation
- Add 33 tests for new management features

### Bump

- Version 2.1.1 → 2.1.2
- Version 2.1.2 → 2.1.3
- Version 2.1.3 → 2.1.4
- Version 2.1.4 → 2.2.0
- Version 2.2.0 → 2.3.0
- Version 2.3.0 → 2.3.1

### Cc-audit

- Warn when npx missing instead of silent skip

### Publish

- Enforce all checks — no skips, no bypass, zero errors
- Integrate git-cliff for CHANGELOG + GitHub release notes

### Rechecker

- Automated review fixes

### Release

- V2.1.1 — Command Consolidation + Canonical Pipeline Standard

### V1.8.6

- Add --report flag to all 17 validators for token optimization

### V1.8.7

- Token optimization — update all callers to use --report

### V1.8.8

- Full plugin audit — bug fixes and consistency

## [1.8.5] - 2026-03-06

### V1.8.5

- Code deduplication — centralize shared constants and functions

## [1.8.4] - 2026-03-06

### V1.8.4

- Documentation & consistency fixes

## [1.8.3] - 2026-03-05

### V1.8.3

- Quality score audit — 98/100 A+ self-validation

## [1.8.2] - 2026-03-05

### Bump

- V1.8.2 — Claude Code 2.1.69 compatibility updates

## [1.8.1] - 2026-03-03

### Bug Fixes

- Eliminate security validator false positives with context-aware heuristics
- Remove literal path from comment to avoid MINOR validation flag

### Bump

- V1.8.1 — fix security validator false positives

## [1.8.0] - 2026-03-03

### Bug Fixes

- Comprehensive audit fixes — dead code removal, missing commands/references, modernized types

### Documentation

- Update README with all 17 commands + improve --help across all scripts

### Miscellaneous Tasks

- Pre-audit checkpoint before swarm fixes

### Security

- V1.8.0 — audit fixes, 17 commands, improved --help

## [1.7.9] - 2026-03-03

### Bug Fixes

- Resolve remaining Pyright hints in claude-plugin-install.py

### Documentation

- Update README with all v1.7.x additions + raise tool threshold to 10

### Bump

- V1.7.9 — all checks clean

## [1.7.8] - 2026-03-03

### Features

- Add local plugin install command + skill + tool-count parsing fix

### Bump

- V1.7.8 — local plugin install command + skill + tool-count fix

## [1.7.7] - 2026-03-03

### Bug Fixes

- Audit remediation — 10 issues + 2 test bugs fixed
- Shellcheck once, matcher helper, encoding=utf-8 across all scripts
- Resolve 4 pre-existing Pyright diagnostics

### Miscellaneous Tasks

- Pre-fix checkpoint before audit remediation
- Remove temp files from audit
- Update uv.lock

### Bump

- V1.7.7 — code quality fixes

## [1.7.6] - 2026-03-03

### Bug Fixes

- Expand known dirs whitelist, skip git-hooks from platform scan (v1.7.6)

## [1.7.5] - 2026-03-03

### Documentation

- Add fix instructions for 8 new v1.7.5 validation checks

### Features

- Add 8 validation checks from claude-plugin-install.py gap analysis (v1.7.5)

## [1.7.4] - 2026-03-03

### Bug Fixes

- Expand ALLOWED_DOC_PATH_PREFIXES for common system paths
- Add /usr/lib64/ to ALLOWED_DOC_PATH_PREFIXES
- Security hardening + cross-platform fixes (v1.7.4)

## [1.7.3] - 2026-02-28

### Bug Fixes

- Resolve all validator paths to absolute — prevent relative_to() crashes
- Add content-type early exit checks to all 16 validators

### Testing

- Add 37 early-exit tests for all 16 validators (1085 total)

### Release

- V1.7.3 — content-type early exit, path resolution, ruff format

## [1.7.2] - 2026-02-28

### Bug Fixes

- Resolve relative path crash in validate_plugin.py (v1.7.2)

## [1.7.1] - 2026-02-28

### Bug Fixes

- Sort imports in validate_plugin.py and validate_scoring.py (CI lint)

### Features

- Gitignore-aware file scanning + pathlib-based cross-platform walk

### Testing

- Add 30 tests for v1.7.0 features

### Release

- V1.7.1 — gitignore-aware validation, 30 new tests

## [1.7.0] - 2026-02-28

### Bug Fixes

- Track fenced code block state in backslash path detection
- Eliminate false positives in absolute path scanner and auto-resolve cache dirs
- **v1.5.1:** Namespace validation_common.py → cpv_validation_common.py
- **v1.5.3:** Hook timeouts are milliseconds, binary search is recursive
- **setup-marketplace:** Hub-and-spoke architecture, batch ops, full autonomy
- Skip indented TOC links in validate_toc_embedding (false positive)
- Robust TOC false positive filter — check resolved path, not indentation
- Nuanced TOC validation — list-item ambiguity, exempt files, NIT/WARNING
- Resolve all MAJOR validation issues, reduce MINOR to tool-count only
- Remove bash script, fix .venv/bin false positive in validator
- Upgrade .venv gitignore check to MAJOR, keep rglob(bin) exclusion
- V1.7.0 — comprehensive audit fixes across validators, templates, docs

### Documentation

- Update validator invocation for standalone use
- Add marketplace installation instructions with --scope local
- Update README with --scope user installation instructions
- Add mandatory report file output to skill, agent, and command

### Features

- Add remote execution fallback for linting tools
- Integrate smart_exec.py for comprehensive linter resolution
- Add workflow inline Python quoting validator
- V1.5.0 — ValidationReport unification, 987 tests, 72% coverage
- Structural venv detection — detect by pyvenv.cfg, not name

### Miscellaneous Tasks

- Safety commit before migration
- Dereference validation symlinks for publishing
- Dereference validation symlinks for publishing
- Dereference validation symlinks for publishing
- Bump version to 1.3.3
- Bump version to 1.3.4
- Bump version to 1.3.5
- Sync validation scripts, hooks, and workflows from CPV
- Bump version to 1.3.6
- Bump version to 1.5.2

### V1.5.4

- Embed reference file TOCs in SKILL.md for progressive discovery

### V1.5.5

- Agent remediation guides + TOC embedding validator

### V1.5.6

- Add /cpv-setup-github-marketplace skill for automated marketplace creation

### V1.6.0

- Read-only linting architecture + cross-platform Python hooks

## [1.3.1] - 2026-02-08

### Bug Fixes

- **validate_marketplace:** Detect git source type with local plugins
- Pipeline setup now runs fix when --validate --fix used together
- Address audit issues in setup_plugin_pipeline.py and plugin-validator.md
- Correct CI/CD loop logic to block unfixable issues
- Fix regex escaping and reorder lint steps
- Add type annotations to fix mypy errors
- Wrap long lines to pass E501 lint check
- Remove --quiet flag from validator call (flag doesn't exist)
- **ci:** Handle exit code 3 (minor issues) as CI pass
- Correct marketplace repo name in notify workflow
- Allow MINOR issues (exit code 3) to pass CI + pipeline templates
- Remove unused imports and make lint step non-blocking
- Detect duplicate hooks.json that causes plugin load error
- Handle anchor links in resource reference validation
- Use regex for exact section header matching
- **validator:** Catch repository type and unknown manifest keys

### Documentation

- Add marketplace installation notice to README
- **skill:** Document CRITICAL source schema error for local plugins
- Update CHANGELOG.md
- **agent:** Comprehensive update to plugin-validator agent

### Features

- Add git submodule validation for marketplace plugins
- **validation:** Bump version to 1.1.0
- Add multi-language linter and dependency verification
- Add universal pipeline installer and update validator agent
- Implement CI/CD auto-fix loop in pre-push hook
- Add multi-language linting support with auto-installation
- **pipeline:** Add comprehensive auto-installation for all languages
- **pipeline:** Add cross-platform support for Linux, macOS, Windows
- Add comprehensive marketplace pipeline validation
- Add troubleshooting topic validation for README files
- Add fuzzy matching, auto-discovery, and privacy detection
- **v1.3.0:** Version bump and validation improvements
- **cpv:** Bump version to 1.3.1

### Miscellaneous Tasks

- Add git-cliff configuration and changelog
- Update CHANGELOG.md with latest changes
- Update CHANGELOG.md
- Auto-fix lint/format issues (iteration 1)
- Auto-fix lint/format issues (iteration 1)
- Auto-fix lint/format issues (iteration 1)
- Auto-fix lint/format issues (iteration 1)
- Auto-fix lint/format issues (iteration 1)
- Auto-fix lint/format issues (iteration 1)
- Bump version to 1.2.0 with audit fixes
- Trigger notify-marketplace workflow
- Bump version to 1.2.0
- Gitignore all *_dev folders with wildcard pattern

---
*Generated by [git-cliff](https://git-cliff.org)*
