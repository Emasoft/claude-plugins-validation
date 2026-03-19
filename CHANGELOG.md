# Changelog

All notable changes to the Claude Plugins Validation plugin will be documented in this file.

## [2.1.1] - 2026-03-19

### Command Consolidation + Canonical Pipeline Standard

Consolidated 43 commands → 37 commands by merging overlapping commands.

#### Command Changes
- **Renamed**: `cpv-create-plugin-repo` → `cpv-create-local-plugin` (emphasizes local-only operation)
- **Renamed**: `cpv-create-marketplace-repo` → `cpv-create-local-marketplace` (emphasizes local-only operation)
- **Enhanced**: `cpv-publish-a-plugin-as-github-repo` — absorbs `cpv-setup-plugin-repo` workflow
- **Enhanced**: `cpv-create-github-marketplace` — absorbs `cpv-setup-github-marketplace` workflow
- **Enhanced**: `cpv-publish-plugin-to-marketplace` — absorbs `cpv-publish-to-marketplace` workflow
- **Enhanced**: `cpv-validate-github-plugin` — adds `--audit` flag (absorbs `cpv-audit-github-plugin`)
- **Enhanced**: `cpv-validate-github-marketplace` — adds `--audit` flag (absorbs `cpv-audit-security`)
- **New**: `cpv-standardize` — auto-detects plugin vs marketplace, replaces both `cpv-standardize-plugin` and `cpv-standardize-marketplace`
- **Removed** (moved to scripts_dev/): `cpv-setup-plugin-repo`, `cpv-setup-github-marketplace`, `cpv-publish-to-marketplace`, `cpv-audit-github-plugin`, `cpv-audit-security`, `cpv-standardize-marketplace`, `cpv-standardize-plugin`

#### Script Changes
- `manage_github_validate.py`: Added `--audit` flag for combined `--plugin/--marketplace` + security audit. Legacy `--audit-plugin` and `--audit-marketplace` flags kept as hidden backward-compat aliases.

#### New Skill
- `canonical-pipeline/SKILL.md`: Documents the canonical file structure, CI/CD workflows, git hooks, and release pipeline standard for all Emasoft plugins and marketplaces.

#### Agent Updates
- `plugin-creator`: Replaced `setup-plugin-repo`, `setup-github-marketplace`, `publish-to-marketplace` skills with `canonical-pipeline` skill
- `plugin-fixer`: Replaced `setup-plugin-repo`, `setup-github-marketplace` skills with `canonical-pipeline` skill; updated body references

#### Documentation
- Updated README command tables and directory structure listing
- Updated setup-plugin-repo skill trigger reference

## [2.1.0] - 2026-03-18

### Validation Rules Completion + Universal Standards

#### 7 New Validation Rules (Phase A)
- **Version consistency**: SKILL.md frontmatter version checked against plugin.json (validate_xref.py)
- **Pipeline readiness**: pre-push hook, publish.py, cliff.toml, workflows, notify-marketplace.yml (validate_plugin.py)
- **Workflow best practices**: flags `uv pip install --system` (use uvx), unpinned actions/checkout (validate_plugin.py)
- **Script permissions**: Python scripts with shebang should be executable on Unix (validate_plugin.py)
- **.gitignore requirements**: must ignore `.claude/`, `llm_externalizer_output/`, `.tldr/` (validate_plugin.py)
- **README badge markers**: `<!--BADGES-START-->` / `<!--BADGES-END-->` for automated badge updates (validate_plugin.py)
- **pyproject.toml + .python-version**: recommended for Python plugins (validate_plugin.py)

#### Plugin/Marketplace Repo Generators (Phase B)
- **generate_plugin_repo.py** (1489 LOC): Scaffolds complete plugin repos with 16 files — manifest, pyproject.toml, .gitignore, README with badges, LICENSE, cliff.toml, publish.py, setup-hooks, pre-push hook, 4 GitHub workflows, component dirs, test skeleton
- **generate_marketplace_repo.py** (890 LOC): Scaffolds marketplace HUB repos — marketplace.json with `{source: "github", repo: "owner/repo"}` entries (HUBS ONLY, no plugin code), auto-generated README catalog, CI/CD workflows, catalog updater
- **standardize_plugin.py**: Audits existing plugin repos against CPV standards, optionally fixes gaps
- **standardize_marketplace.py**: Audits existing marketplaces, flags local-path sources as errors

#### New Components (Phase C)
- **Agent**: `plugin-creator.md` — guides plugin/marketplace creation workflow
- **Skills**: `create-plugin/SKILL.md`, `standardize-plugin/SKILL.md`
- **Commands**: `/cpv-create-plugin-repo`, `/cpv-create-marketplace-repo`, `/cpv-standardize-plugin`, `/cpv-standardize-marketplace`

#### Marketplace Architecture Enforcement
- Marketplaces must be HUBS ONLY — pointers to external GitHub repos, never plugin code
- Plugin sources validated: `{"source": "github", "repo": "owner/repo"}` format enforced
- Local paths (`./plugins/...`) flagged as errors in marketplace validation

## [2.0.0] - 2026-03-18

### MAJOR: Merged claude-plugins-management (CPM v1.4.0) into CPV

Integrates all 25 unique features from the claude-plugins-management plugin, creating a unified plugin for both validation (190+ rules) AND lifecycle management.

#### New Management Scripts (8)
- **cpv_management_common.py**: Shared infrastructure — JSONC parser (comments + trailing commas), atomic JSON I/O with timestamped backups, archive extraction (zip/tar/tgz/bz2/xz) with path traversal prevention, cross-platform Windows support, color output helpers
- **manage_plugin.py**: Plugin lifecycle — install from directories or archives (gitignore-aware copy), uninstall with cache cleanup, update (uninstall + reinstall), enable/disable via settings.local.json
- **manage_registry.py**: List installed plugins with version/status/components, search by component type (commands/agents/skills/hooks/mcp/lsp/rules/output-styles) or free text
- **manage_doctor.py**: Health check — Claude CLI auth, settings integrity, marketplace validation (reserved names, impersonation, kebab-case), per-plugin validation, orphaned entry detection
- **manage_marketplace.py**: Marketplace CRUD — add/remove/list/update registrations via `claude` CLI, normalize GitHub URLs (HTTPS/SSH/git://owner/repo)
- **manage_remote.py**: Remote plugin operations — install/update/uninstall/enable/disable via `claude` CLI delegation, scoped installation (user/project/local)
- **manage_github_validate.py**: GitHub repo validation — clone with `gh repo clone --depth 1`, run CPV validators, optional skill-audit security scan, temp dir cleanup
- **bump_version.py**: Semantic version bumping — patch/minor/major/set across plugin.json and pyproject.toml

#### New Commands (16)
- `/cpv-install-plugin`, `/cpv-uninstall-plugin`, `/cpv-update-plugin`
- `/cpv-enable-plugin`, `/cpv-disable-plugin`, `/cpv-version`
- `/cpv-list-plugins`, `/cpv-search-plugins`, `/cpv-doctor`
- `/cpv-manage-marketplaces`, `/cpv-manage-remote-plugins`
- `/cpv-validate-github-plugin`, `/cpv-validate-github-marketplace`
- `/cpv-audit-security`, `/cpv-audit-github-plugin`, `/cpv-bump-version`

#### New Agent (1)
- **plugin-manager.md**: Autonomous management agent (sonnet, maxTurns: 20) for install/uninstall/search/doctor/marketplace operations

#### New Skill (1)
- **plugin-management/SKILL.md**: Complete lifecycle management documentation with all script references

#### Architecture
- CPM's monolithic 4365-line `claude-plugin-install.py` decomposed into 7 focused modules + 1 shared library
- Management modules call CPV validators via subprocess (no circular imports, clean separation)
- CPM's validation code discarded — CPV's 190+ rule validators are strictly superior
- All 20 existing `/cpv-*` commands preserved with identical behavior
- All 1150 existing tests pass unchanged (0.84s)

## [1.12.0] - 2026-03-18

### Claude Code v2.1.76–v2.1.78 Alignment

- **StopFailure hook event** (v2.1.78): fires on API errors (rate limit, auth failure). Added to all event lists.
- **${CLAUDE_PLUGIN_DATA} env var** (v2.1.78): persistent data directory surviving updates. Recognized in 8 validators, skip filesystem resolution for DATA paths, updated lookbehind patterns.
- **Agent `effort` frontmatter** (v2.1.78): low/medium/high model effort. New `validate_effort_field()` function.
- **`branch` context value** (v2.1.77): /fork renamed to /branch, both accepted.
- **LLM Externalizer MCP instructions** added to all 4 agents (plugin-validator, skill-validation-agent, plugin-fixer, semantic-validator).
- **Fixed old LLM Externalizer tool prefix** in CHANGELOG (`mcp__llm-externalizer__*` → `mcp__plugin_llm-externalizer_llm-externalizer__*`).
- **Updated hook-validation.md** reference: 23 events (was 19), fixed incorrect PreToolResponse reference, corrected event categorizations.
- **Updated plugin-structure.md** reference: CLAUDE_PLUGIN_DATA env var, SessionStart hook pattern for dependency management, persistent data best practices.
- **Updated README.md**: 23 hook events, env var list, deep path/URL validation mention.
- All 1150 tests pass

## [1.11.0] - 2026-03-14

### Claude Code v2.1.0–v2.1.76 Changelog Alignment

- **3 new hook events**: `PostCompact`, `Elicitation`, `ElicitationResult` (v2.1.76)
- **HTTP hook type**: `"http"` now valid alongside command/prompt/agent (v2.1.63)
- **Full model IDs**: Agent/skill/command `model:` fields now accept `claude-opus-4-6`, `claude-sonnet-4-6`, etc. (v2.1.74)
- **5 new tool matchers**: `ExitWorktree`, `TaskOutput`, `CronCreate`, `CronDelete`, `CronList` (v2.1.71–72)
- **HTTP hook validation**: `validate_http_hook()` checks url, headers, timeout
- **`is_valid_model()` function**: Centralized model validation accepting short names + full IDs
- Updated COMMAND_ONLY_EVENTS, EVENTS_WITHOUT_MATCHERS, COMMON_TOOL_NAMES, VALID_TOOLS
- Updated all 4 model validators (agent, command, skill, install script)
- All 1120+ tests pass, 0 validation issues

## [1.10.7] - 2026-03-12

### LLM Externalizer MCP Integration

- **Added LLM Externalizer instruction** to all 8 skill SKILL.md Token Optimization sections
- Agents now instructed to prefer `mcp__plugin_llm-externalizer_llm-externalizer__*` for bounded analysis tasks
- Compressed existing text in tight SKILL.md files to stay under 4000 bytes
- All 1120 tests pass

## [1.10.6] - 2026-03-12

### Backtick Reference Detection

- **New validator feature**: Detects backtick-enclosed `.md` references in SKILL.md files
- Added `_BACKTICK_REF_RE` regex and `_build_fenced_line_set()` for code block exclusion
- Backtick refs always report format MINOR (invisible to progressive discovery)
- Backtick refs to existing `.md` files get full TOC embedding check
- Deduplication: files already checked via markdown links skip double TOC check
- **Moved Error Index** from fix-validation/SKILL.md to `references/error-index.md`
- **Fixed all 37 backtick MINORs** across 5 SKILL.md files
- 10 new tests in `TestBacktickRefDetection` (1120 total)

## [1.10.5] - 2026-03-08

### Fix All 14 TOC Embedding Warnings

- **Embedded complete TOC headings** in all 5 SKILL.md files (plugin-validation-skill, publish-to-marketplace, setup-github-marketplace, setup-plugin-repo, skill-validation-skill)
- Every reference `.md` link now has the full TOC copied immediately after it, enabling progressive discovery by agents
- Compressed Examples, Token Optimization, and Checklist sections to stay under 4000 byte SKILL.md limit
- Updated CHANGELOG with all missing versions since v1.9.2
- Validation: 0 CRITICAL, 0 MAJOR, 0 MINOR, 0 NIT, 0 WARNING
- All 1110 tests pass

## [1.10.4] - 2026-03-08

### Audit Bug Fixes

- **Fixed `_TOC_ENTRY_RE` regex**: Now requires list markers (`-`, `*`, `+`, `1.`) — previously matched prose paragraphs as TOC entries
- **Fixed `publish.py` uv.lock dirty state**: Auto-commits `uv.lock` if it's the only dirty file (caused by `uv run` side effect)
- **Fixed code fence language tags** in `plugin-binary-builds.md` (bare `` ``` `` → `` ```text ``)

## [1.10.3] - 2026-03-08

### Progressive Discovery Messaging

- **Improved validation messages**: TOC embedding warnings now explain that missing headings break the progressive discovery algorithm — content becomes invisible to agents
- Updated both WARNING (list-item links) and MINOR (standalone links) messages

## [1.10.2] - 2026-03-08

### Strict TOC Embedding Enforcement

- **Require ALL TOC headings embedded**, not just `min(2, len)` — partial embedding defeats progressive discovery
- Updated `validate_toc_embedding()` to check `embedded_count == len(toc_headings)`

## [1.10.1] - 2026-03-08

### Publish Pipeline Enforcement

- **Block direct `git push`**: Pre-push hook now checks for `CPV_PUBLISH_PIPELINE` env var — only `publish.py` can push
- `publish.py` sets `CPV_PUBLISH_PIPELINE=1` before calling `git push`
- All pushes must go through `uv run python scripts/publish.py --patch|--minor|--major`

## [1.10.0] - 2026-03-08

### Binary Compilation Support & Marketplace Publishing

- **New skill: `publish-to-marketplace`** — PAT setup, notification workflow, publish pipeline for any marketplace
- **New reference: `plugin-binary-builds.md`** — cross-compilation workflow (5 platforms), binary distribution, platform detection
- **Binary build phases** added to pre-push hook, publish.py, and CI workflow templates
- Generic marketplace support — no hardcoded repo names, uses `<placeholder-for-...>` tokens
- Updated README with 3 new commands, 4 new agents/skills, fixed directory tree
- Fixed broken ref in semantic-validator agent, script advice in plugin-fixer
- All validators now use `GitignoreFilter` instead of hardcoded skip sets

## [1.9.9] - 2026-03-07

### Plugin Repo Templates

- Enhanced README templates with marketplace links, structure rationale
- Added compilation instructions placeholder for binary plugins

## [1.9.8] - 2026-03-07

### Template Standardization

- Enhanced README templates, marketplace enforcement, compilation instructions
- Standardized placeholder format across all templates

## [1.9.7] - 2026-03-07

### Template Security

- Redacted repo-specific values from all templates
- Standardized `<placeholder-for-...>` format across templates

## [1.9.6] - 2026-03-07

### Pipeline Knowledge

- Added pipeline knowledge (`plugin-hooks-and-scripts.md`, `plugin-workflows.md`) to plugin-fixer agent

## [1.9.5] - 2026-03-07

### Fix Validation Skill

- **New skill: `fix-validation`** with reference files for remediation steps
- Moved fix reference files from `agents/references/` to `skills/fix-validation/references/`
- Enforced 5000 char / 500 line limits, TOC in first 200 chars for reference files
- Resolved MINOR validation issues in skill SKILL.md files

## [1.9.4] - 2026-03-07

### GitignoreFilter Adoption

- All validators now respect `.gitignore` — skip `*_dev` directories
- Refactored all validators to use `GitignoreFilter` instead of hardcoded skip lists

## [1.9.3] - 2026-03-07

### Unified Publish Pipeline

- **Merged publish scripts** into unified `publish.py` pipeline (test → lint → validate → consistency → bump → commit → push)
- Enforced version bump check in pre-push hook
- Excluded `_dev` directories from `lint_files.py`

## [1.9.2] - 2026-03-07

### Internal Improvements

- Internal refactoring and incremental improvements

## [1.9.1] - 2026-03-07

### Accurate Token Cost Measurement

- **Rewrote `cpv_token_cost.py`**: Replaced opaque `total_tokens` approach with transcript-based parsing that reads the agent's own JSONL transcript for full per-API-call usage breakdown (input, output, cache_write, cache_read)
- **Created `hooks/hooks.json`**: SubagentStop hook fires after every validation agent completes, auto-reporting token cost via systemMessage
- **4-category pricing**: Accurate per-model cost using separate rates for input, output, cache_creation, and cache_read tokens (cache reads are 90% cheaper than regular input)
- **Dual-mode script**: Works as SubagentStop hook (stdin JSON) and CLI tool (`--transcript PATH`)
- **Added `plugin_path` to compact summary**: All 15 validation scripts now display the validated plugin/skill path in compact output
- **Updated `print_compact_summary()` and `save_report_and_print_summary()`** in `cpv_validation_common.py` to accept `plugin_path` parameter
- **Added `hooks` key to `plugin.json`** manifest
- All 1090 tests pass, Pyright clean

## [1.9.0] - 2026-03-06

### Dual Scoring System

- Syntactic validation: severity counts + binary VALID/INVALID verdict (no grades, no tiers)
- Semantic validation: A-F letter grades via Opus AI (explicit opt-in only)
- Removed `calculate_letter_grade` and `calculate_syntactic_tier` from codebase

## [1.8.8] - 2026-03-06

### Full Plugin Audit — Bug Fixes and Consistency

- **CRITICAL**: Fixed pyproject.toml version mismatch (was 1.8.2, now synced with plugin.json)
- **CRITICAL**: Fixed `cpv-install-plugin.md` and `install-plugin/SKILL.md` — `marketplace` is positional, not `--marketplace` flag
- **CRITICAL**: Added missing `--strict` flag to `validate_xref.py` with `exit_code_strict()` support
- **MAJOR**: Synced `VALID_TOOLS` in `cpv_validation_common.py` — added 6 missing tools (MultiEdit, Notebook, TodoRead, TodoWrite, LSP, Agent)
- **MAJOR**: Fixed `setup-github-marketplace/SKILL.md` — corrected 4 non-existent script/workflow references
- **MAJOR**: Added missing `--report`, `--marketplace-only`, `--skip-platform-checks` docs to `cpv-validate-plugin.md`
- **MAJOR**: Updated README — added `--report` to options, mentioned both agents, added self-validation section
- Fixed all `python3 -c` references to `uv run python -c` across 8 command/agent files
- Added `cpv-setup-github-marketplace.md` Execution section explaining agent-driven workflow
- Added `setup_marketplace_automation.py` to README utility scripts table
- Added missing CHANGELOG note for versions 1.8.0-1.8.1
- Reformatted all 20 Python scripts with ruff
- Self-validation: PASS 157/157, 1101 tests pass

## [1.8.7] - 2026-03-06

### Token Optimization — Callers Updated

- Updated all 15 validator command files to use `--report` in execution sections
- Removed duplicate Output Example, Exit Codes, and Severity Levels sections from 6 large commands (~164 lines saved)
- Kept `cpv-validate-plugin.md` as canonical reference for exit codes and severity levels
- Updated all script invocations in 3 skill SKILL.md files to use `--report` flag
- Added "Report Output (MANDATORY)" section to `skill-validation-skill/SKILL.md`
- Updated `plugin-validation-skill/SKILL.md` report instructions to reference `--report` flag
- Fixed all agent .md examples to demonstrate `--report` usage with compact summary output
- Zero bare validate_* invocations remain across all commands, skills, and agents

## [1.8.6] - 2026-03-06

### Token Optimization — `--report` Flag

- Added `--report PATH` flag to all 17 validator scripts
- When `--report` is used: saves full detailed output to file, prints only 3-line compact summary to stdout (severity counts + verdict + report path)
- Added `print_compact_summary()` and `save_report_and_print_summary()` to `cpv_validation_common.py`
- Updated agent instructions (plugin-validator.md, skill-validation-agent.md) to always use `--report` flag
- Agents now provide report file paths to users instead of reading verbose output into context
- Estimated ~60% reduction in per-validation token consumption
- Fixed type mismatch in `validate_marketplace_pipeline.py` (inline compact summary for `PipelineValidationReport`)
- All 1101 tests pass, ruff clean, Pyright clean, 98/100 A+ self-validation

## [1.8.5] - 2026-03-06

### Code Deduplication & Audit Fixes

- Centralized `BINARY_EXTENSIONS`, `is_binary_file()`, `should_skip_directory()` in `cpv_validation_common.py`
- Centralized `SKILL_FRONTMATTER_FIELDS` — shared by `validate_skill.py` and `validate_skill_comprehensive.py`
- Replaced local `colors = {...}` dicts with `COLORS` import in 10 validators
- Replaced hardcoded `valid_hook_events` in `validate_skill_comprehensive.py` with `VALID_HOOK_EVENTS` import
- Replaced hardcoded `PLUGIN_ENV_VARS` in `validate_lsp.py` and `validate_mcp.py` with `VALID_PLUGIN_ENV_VARS` import
- Imported `EXIT_*` constants in `validate_scoring.py` from common instead of redefining
- Updated `KNOWN_TOOL_MATCHERS` in installer with all modern Claude Code tools
- Fixed help text in `setup_git_hooks.py` (commit → push bypass instruction)
- Removed redundant `ruff check` step from embedded GitHub Actions workflow in `setup_plugin_pipeline.py`
- All 1101 tests pass, ruff clean, mypy clean, 98/100 A+ self-validation

## [1.8.4] - 2026-03-06

### Documentation & Consistency Fixes

- Documented `--update`, `--enable`, `--disable`, `-q/--quiet` flags in install-plugin command and skill
- Documented `git-subdir` source type in marketplace-validation reference
- Documented `authServerMetadataUrl` and `callbackPort` OAuth fields in mcp-validation reference
- Fixed tool patterns in 2 skill SKILL.md files (`Bash(uv:*)` → `Bash(uv*)`)
- Updated README: commands table, install examples, directory tree with 8 missing command files
- Fixed tomli mypy `import-not-found` in lint_files.py
- Made 16 scripts executable (chmod +x) for shebang consistency
- Deep audit of all 28 scripts: ruff clean, mypy clean, no TODO/FIXME/HACK

## [1.8.3] - 2026-03-05

### Quality Score Audit

- Achieved 98/100 A+ self-validation score (up from 58/100 F)
- Fixed 39 MAJOR command issues: shortened descriptions to ≤60 chars, fixed `Bash(uv:*,python:*)` tool patterns
- Fixed 5 hardcoded example paths in command/agent docs
- Fixed 16 mypy type annotation errors in `claude-plugin-install.py`
- All 7 scoring categories now pass at Excellent level

## [1.8.2] - 2026-03-05

### Claude Code 2.1.69 Compatibility

- **New hook event**: `InstructionsLoaded` — fires when CLAUDE.md or .claude/rules/*.md files load
  Added to all VALID_HOOK_EVENTS sets across cpv_validation_common.py, validate_hook.py,
  validate_skill_comprehensive.py, and claude-plugin-install.py
- **New env var**: `${CLAUDE_SKILL_DIR}` — skills can reference their own directory in SKILL.md
- **New MCP OAuth config**: `oauth.authServerMetadataUrl` — custom OAuth metadata discovery URL
- **New source type**: `git-subdir` — points to a subdirectory within a git repo (requires repo + subdir)
- **Synced hook events**: claude-plugin-install.py was missing Setup, ConfigChange events

### Security Validator

- Eliminated all false positives when scanning own plugin (86 CRITICAL + 8 MAJOR → 0)
- Context-aware heuristics: KNOWN_EXAMPLE_SECRETS, is_shell_like_file(), Python-aware
  backtick/eval/pipe-to-shell skips, relative path handling, Python string literal detection

## [1.8.0] - [1.8.1] — Internal Development

Internal refactoring and incremental improvements. No user-facing changes.

## [1.7.9] - 2026-03-03

### Code Quality

- Resolved all remaining Pyright hints in `claude-plugin-install.py`
  (ctypes.windll Windows-only, unused unpack variables, reserved parameters)
- Full clean sweep: Pyright 0/0/0, Ruff passed, Mypy 0 issues (28 files), 1101 tests pass
- Self-validation: 0 CRITICAL/MAJOR/MINOR/NIT

## [1.7.8] - 2026-03-03

### Features

- New `/cpv-install-plugin` command — wraps `claude-plugin-install.py` as a slash command
- New `install-plugin` skill — instructions for local plugin installation without GitHub marketplace
- Agent `plugin-validator.md` updated with local install responsibility and script documentation

### Bug Fixes

- Fixed tool-count parsing in `validate_skill_comprehensive.py` and `validate_skill.py`:
  `Bash(git:*,gh:*)` now counts as 1 tool instead of being split at internal commas
- Fixed all 14 ruff + 17 mypy errors in `claude-plugin-install.py`
- Fixed `plugin-validation-skill/SKILL.md` line count (under 500 limit)

## [1.7.7] - 2026-03-03

### Code Quality

- Fix 4 pre-existing Pyright diagnostics across validation scripts
- `validate_xref.py`: Use `available_agents` set instead of redundant filesystem check in `validate_subagent_type_matching()`
- `cpv_validation_common.py`: Idiomatic `_` for unused tuple unpack
- `validate_skill.py`: Prefix/del for reserved parameters
- `validate_marketplace_pipeline.py`: Del for reserved `_verbose` parameter
- Shellcheck resolution moved outside loop (single "not available" message)
- Extracted `_check_matcher_values()` helper (eliminates 3 duplicated blocks)
- Added `encoding="utf-8"` to all 36 `read_text()` calls across 11 scripts

## [1.7.6] - 2026-03-03

### Improvements

- Expanded known directories whitelist: `git-hooks`, `shared`, `fixtures`, `vendor`, `src`, `dist`, `build`, `out`, `target` no longer trigger "non-standard directory" warnings
- Excluded `git-hooks/`, `tests/`, `fixtures/` from cross-platform script scan (developer tooling, not end-user components)

## [1.7.5] - 2026-03-03

### New Validation Checks (from claude-plugin-install.py gap analysis)

- **validate_plugin.py:** Add `scripts/` to misplaced-component check (was missing from the `.claude-plugin/` guard)
- **validate_plugin.py:** Validate plugin-shipped `settings.json` (JSON parse + unrecognized key warnings)
- **validate_plugin.py:** Warn when plugin has a manifest but zero actual content (no commands/, skills/, agents/, hooks/, etc.)
- **validate_plugin.py:** Check shebangs on plugin-level script files (.py, .sh, .rb, .pl, .php, .bash)
- **validate_hook.py:** Fuzzy "did you mean?" suggestions for misspelled hook event names (via `difflib`)
- **validate_hook.py:** Activate dead constants — validate Notification, SessionStart, and PreCompact matcher values
- **validate_hook.py:** Bash command portability checks: script without interpreter, `~/` paths, bare `cd`, backslash paths
- **validate_hook.py:** Warn on relative `./` paths in hook commands without `${CLAUDE_PLUGIN_ROOT}`

### Tests

- Added 16 tests for all 8 new validation checks (`test_new_validation_checks.py`)
- Total test count: 1101

## [1.7.4] - 2026-03-03

### Security Fixes (from CPV upstream audit)

- **CPV-001 (CRITICAL):** `smart_exec.py` — Deno `run -A` replaced with minimal permissions (`--allow-read=.`, `--allow-write=.`, `--allow-env`, `--allow-net`, `--no-prompt`)
- **CPV-002 (CRITICAL):** `smart_exec.py` — Docker volume mount changed from read-write to read-only (`:ro`) with `--security-opt=no-new-privileges` and `--cap-drop=ALL`
- **CPV-003 (MAJOR):** `smart_exec.py` — PowerShell module/cmdlet name validation via regex to prevent command injection
- **CPV-004 (MAJOR):** `smart_exec.py` — `resolve_tool()` now rejects unknown tools (raises `ValueError`) instead of auto-installing arbitrary npm packages via `npx --yes`

### Bug Fixes

- **CPV-005 (MAJOR):** `git-hooks/pre-push` — `run_script()` now has a 180s timeout to prevent indefinite hangs
- **CPV-006 (MAJOR):** `git-hooks/pre-push` — `run_script()` now passes `cwd=repo_root` so scripts run from the correct directory
- **CPV-007 (MUST-FIX):** `lint_files.py` — ANSI colors now respect `NO_COLOR` env var and non-TTY output
- **CPV-008 (MUST-FIX):** `setup_plugin_pipeline.py` — Same ANSI color guard as CPV-007
- **CPV-010 (MINOR):** `git-hooks/pre-push` — Added `NO_COLOR` check to `_colors_supported()`
- **CPV-011 (NIT):** `cpv_validation_common.py` — Moved `import getpass` from lazy to top-level
- **CPV-013 (NIT):** `smart_exec.py` — Added docstrings to all command builder functions

### Code Quality

- Uniform `(repo_root, files)` signature for all 15 lint functions in `lint_files.py`
- Replaced lambda dispatch table with direct function references
- Ruff lint + format clean across all files

## [1.7.3] - 2026-02-28

### Bug Fixes

- **Content-type early exit:** All 16 validators now detect wrong content at the given path and exit with a clear error message instead of crashing or producing confusing output (agent, command, hook, skill, MCP, LSP, rules, documentation, security, encoding, enterprise, xref, scoring, plugin, marketplace, marketplace_pipeline).
- **Path resolution:** All 16 validators resolve paths to absolute via `.resolve()` in `main()`, preventing `ValueError` crashes from `relative_to()` when relative paths are used.
- **Code formatting:** Ruff format applied to 7 files (cpv_validation_common, gitignore_filter, lint_files, setup_git_hooks, conftest, test_cpv_validation_common, test_extended_linting).

### Tests

- Added 37 early-exit tests in `tests/test_validator_early_exit.py` covering all 16 validators (1085 total tests).

## [1.7.2] - 2026-02-28

### Bug Fixes

- **Crash fix:** Running validator with a relative path (`.`) from a non-plugin directory no longer crashes with `ValueError: ... is not in the subpath`. All paths are now resolved to absolute paths early in `main()`.
- **Early exit for missing plugins:** When no `.claude-plugin/` directory is found at the given path, the validator now exits with a clear error message instead of proceeding and crashing in `validate_cross_platform`.

## [1.7.1] - 2026-02-28

### New Features

- **`scripts/gitignore_filter.py`:** Helper module with `GitignoreFilter` class — pathlib-based, cross-platform gitignore-aware file scanning (walk, rglob, iterdir)
- **Gitignore-aware validation:** All file scans in `validate_plugin.py` now skip gitignored files/directories, eliminating false positives for `.pyc`, `__pycache__`, etc.
- **Tool count check downgraded to WARNING:** `allowed-tools` with >6 tools now produces a non-blocking WARNING instead of MINOR

### Tests

- Added 30 new tests (1048 total): GitignoreFilter class (15), extended gitignore parsing (8), validate_mcp fixes (3), JSON output counts (2), tool count severity (2)

## [1.7.0] - 2026-02-28

### Bug Fixes

- **validate_mcp.py:** Removed duplicate SSE deprecation warning that fired twice for each SSE-transport server
- **validate_mcp.py:** Fixed `IndexError` in `print_results` when `exit_code >= 4` (NIT in strict mode)
- **validate_mcp.py:** Fixed misleading comment (copy-paste artifact from SSE deprecation block)
- **validate_lsp.py:** Added missing `nit` and `warning` counts to JSON output for consistent schema
- **validate_skill.py:** Added missing `nit` and `warning` counts to JSON output for consistent schema
- **cpv_validation_common.py:** Fixed `is_path_gitignored()` — `**` glob patterns now match at any depth (was only matching one level)
- **cpv_validation_common.py:** Fixed `is_path_gitignored()` — negation patterns (`!important.txt`) now properly un-ignore files
- **git-hooks/pre-push:** Fixed early return that skipped validation of subsequent refs when pushing multiple branches
- **validate-marketplace.yml template:** Fixed subshell pipe bug — `FAILED` variable was always 0 due to `while read` running in subshell
- **update-submodules.yml template:** Updated Python version from 3.11 to 3.12

### Improvements

- **GITHUB_WORKFLOW template:** Changed `uv run python` to `python3` (uv not installed in CI), added `--exclude .venv` to ruff check
- **GITIGNORE_ADDITIONS template:** Added `.mypy_cache/` pattern to prevent false positive update detection
- **PRE_COMMIT_HOOK/PRE_PUSH_HOOK templates:** Added `sys.stdout.isatty()` guard for color codes (no ANSI in non-TTY)
- **notify-marketplace.yml template:** Changed path filter from `.claude-plugin/plugin.json` to `.claude-plugin/**` for consistency with skill docs
- **Structural venv detection:** `_is_python_venv()` detects virtualenvs by structure (pyvenv.cfg, bin/activate) instead of name
- **Venvs flagged as MAJOR** if not gitignored (upgraded from WARNING)

### Documentation

- Updated hook event count from 13 to 18 across all documentation (plugin-validator.md, SKILL.md, validation-procedures.md, hook-validation.md)
- Fixed hook-validation.md: Setup event incorrectly listed as command-only (now correctly supports prompt/agent)
- Updated SKILL.md hook types from "command vs prompt" to "command, prompt, agent"
- Fixed skill-validation-skill references: wrong command name, non-existent skill reference
- Fixed push-plugins.sh → push-plugins.py references in setup-github-marketplace SKILL.md
- Updated README.md directory tree: added setup-github-marketplace skill, missing commands, agent references, test files, lint_files.py

## [1.6.0] - 2026-02-28

### Breaking Changes

- **Linting architecture refactored:** All 15 lint functions extracted from embedded PRE_PUSH_HOOK string template into standalone `scripts/lint_files.py` module (single source of truth)
- **All linting is now read-only:** Removed `--fix`, `--write`, `gofmt -w`, `ruff format`, and auto-commit from all lint functions. Linters report issues only.
- **Pre-commit hook reduced:** No longer performs linting at commit time. Only checks for sensitive data (API keys, tokens, passwords).
- **Pre-push hook is a thin wrapper:** Calls `scripts/lint_files.py` and `scripts/validate_plugin.py` as subprocesses instead of embedding 1500+ lines of lint logic.

### New Features

- **`scripts/lint_files.py`:** Standalone importable module with 15 read-only lint functions, cross-platform tool resolution, install hints, and CLI entry point
- **Cross-platform git hooks:** All git hooks (`pre-push`, `pre-commit`) converted from bash to Python for Windows/macOS/Linux compatibility
- **`scripts/setup_git_hooks.py`:** Cross-platform Python replacement for `setup_git_hooks.sh` with `--symlink`, `--remove`, `--help` flags and Windows symlink fallback
- **ESLint flat config support:** JavaScript linting now detects `.mjs`, `.cjs`, `.ts` config files (ESLint 9+)
- **push-plugins.py template:** Marketplace orchestration script template converted from bash to Python

### Bug Fixes

- **`_resolve_tool()` crash:** Wrapped `cpv_validation_common` import in try/except — no longer crashes when module is not importable
- **ruff timeout silently passes:** After `subprocess.TimeoutExpired`, `lint_python()` now correctly returns `False` instead of falling through to `True`
- **GitHub Actions `set +e` missing:** Added `set +e`/`set -e` around validation step in workflow template to properly capture exit codes
- **Markdownlint stderr handling:** `lint_markdown()` now reads both stdout and stderr for lint output
- **`Callable` import location:** Moved from `typing` inside function body to `collections.abc` at module level

### Templates Updated

- All hook templates in `setup_plugin_pipeline.py` are now Python (were already converted in this release)
- `script-templates.md`: PRE_COMMIT_HOOK, PRE_PUSH_HOOK, and push-plugins templates converted from bash to Python
- All agent docs, validation checklists, and skill references updated to reflect read-only linting architecture
- GitHub Actions workflow templates updated to v4 actions, added `lint_files.py` step

### Documentation

- README.md updated with `setup_git_hooks.py` reference
- Agent and skill docs purged of `--fix` references
- Marketplace architecture docs updated for consistent naming

## [1.5.0] - 2026-02-27

### Breaking Changes

- **Severity hierarchy redesigned:** New levels NIT and WARNING added between MINOR and INFO
  - MAJOR/MINOR: always block validation (non-zero exit code)
  - NIT: blocks only in `--strict` mode (exit code 4)
  - WARNING: never blocks, always reported — for security advisories and best practices
- All validators now accept `--strict` flag for NIT-blocking mode
- Some checks previously at MINOR/INFO reclassified to WARNING (see below)

### New Features

- **validate_rules:** New rules/ directory validator
  - Validates `.md` files in rules/ (plain markdown, optional `paths` frontmatter)
  - Language-aware token estimation (CJK, Latin, Cyrillic, Arabic, etc.)
  - Warns if total rules exceed 10k token budget
  - Wired into validate_plugin.py main pipeline
- **cpv_validation_common:** Add WARNING level — never blocks validation, always reported
- **cpv_validation_common:** Add NIT level — blocks only in --strict mode
- **cpv_validation_common:** Add `exit_code_strict()` method for --strict mode
- **cpv_validation_common:** Add `warning()`, `nit()`, `has_warning`, `has_nit` to ValidationReport
- **All validators:** Add NIT and WARNING to Level type, colors, counts, print functions
- **All validators:** Add `--strict` flag (NIT issues block with exit code 4)

### Severity Reclassifications (to WARNING)

- Unknown manifest fields (was MINOR) → WARNING
- Unknown hook fields (was INFO) → WARNING
- Unknown MCP/LSP/skill/command/agent fields (was INFO) → WARNING
- Non-standard plugin directories (was INFO) → WARNING
- Platform-specific scripts — bash, powershell, zsh, etc. (was MINOR) → WARNING
- Missing binary platforms (was MINOR) → WARNING
- Compiled source with build system but no binaries (was MINOR) → WARNING
- Package executors (npx, uvx, bunx, pipx) in hooks and MCP (was MINOR) → WARNING
- Remote MCP servers (was MINOR) → WARNING (HTTP without TLS stays MAJOR)
- Missing .gitignore categories except .env (was MINOR/INFO) → WARNING
- Artifacts not covered by .gitignore (was MINOR) → WARNING

### Features (from v1.4.0 work)

- Add multi-language linter and dependency verification
- **validate_hook:** Add "agent" hook type support (command, prompt, agent)
- **validate_hook:** Add 5 new hook events: TeammateIdle, TaskCompleted, ConfigChange, WorktreeCreate, WorktreeRemove
- **validate_hook:** Add COMMAND_ONLY_EVENTS validation (Setup, PreCompact, Notification)
- **validate_hook:** Add timeout units check (seconds vs milliseconds confusion detection)
- **validate_hook:** Add statusMessage and model field validation
- **validate_hook:** Add absolute path check for command field
- **validate_hook:** Add package executor security warning
- **validate_agent:** Add maxTurns, mcpServers, memory, background, isolation frontmatter fields
- **validate_agent:** Fix VALID_AGENT_VALUES to match official docs (Explore, Plan, general-purpose)
- **validate_mcp:** Add OAuth server config validation
- **validate_mcp:** Add SSE transport deprecation warning (recommend http/streamable-http)
- **validate_mcp:** Add timeout type and range validation
- **validate_mcp:** Add remote server security warning
- **validate_mcp:** Add package executor security warning
- **validate_plugin:** Add author object structure validation
- **validate_plugin:** Add keywords array type validation
- **validate_plugin:** Add inline hooks/mcpServers/lspServers object handling
- **validate_plugin:** Add homepage and license field type checks
- **validate_plugin:** Add .gitignore validation (8 categories)
- **validate_plugin:** Add cross-platform script and binary validation
- **validate_plugin:** Add rules/ directory validation
- **validate_marketplace:** Add "owner" as required marketplace field
- **validate_marketplace:** Add "pip" source type support
- **validate_marketplace:** Make "source" required in plugin entries
- **validate_marketplace:** Add reserved marketplace names check
- **validate_marketplace:** Add SHA-40 hex format validation
- **validate_lsp:** Add extensionToLanguage critical field validation
- **validate_lsp:** Add transport validation (stdio, pipe)
- **validate_lsp:** Add startupTimeout, shutdownTimeout, maxRestarts, restartOnCrash type checks
- **cpv_validation_common:** Update VALID_HOOK_EVENTS to 18 events
- **cpv_validation_common:** Expand VALID_TOOLS with 11 new tools (Skill, AskUserQuestion, EnterPlanMode, etc.)
- **cpv_validation_common:** Add VALID_PLUGIN_ENV_VARS constant
- **cpv_validation_common:** Add system absolute path detection with shebang exclusion

### Documentation

- Update hook-validation reference with new events, agent hook type, and JSON schema rules
- Update marketplace-validation reference with owner, pip source, and reserved names
- Update mcp-validation reference with OAuth, SSE deprecation, and timeout validation
- Update validation-checklist with all new validation rules

### Architecture Refactoring

- **ValidationReport class unification**: Consolidated 11 duplicated ValidationReport implementations into a single class hierarchy
  - Canonical `ValidationReport` and `ValidationResult` in `cpv_validation_common.py`
  - Subclasses: `HookValidationReport`, `SkillValidationReport`, `ComprehensiveSkillReport`, `MarketplaceValidationReport`
  - `PipelineValidationReport` kept standalone (incompatible interface)
  - 829+ lines of duplicated code eliminated
- **Severity casing standardized**: All 74 lowercase severity strings in `validate_marketplace.py` converted to UPPERCASE
- **validate_plugin.py bug fixes**: Ruff error count inflation fixed (aggregated per-file), --strict properly propagated
- **validate_scoring.py**: Removed all 13 `# type: ignore` comments (now type-safe)
- **Security patterns expanded**: Added JWT token, AWS Secret Access Key patterns; added kubeconfig, PEM files to DANGEROUS_FILES

### Testing

- Test count increased from 132 to 299+ tests (17 test files covering all validators)
- Every validator now has dedicated unit tests

### CI/CD

- validate.yml converted from pip to uv
- notify-marketplace.yml updated peter-evans/repository-dispatch from v2 to v4
- Removed non-existent hooks/** trigger path

## [1.2.0] through [1.4.0] - Internal Development

Versions 1.2.0 through 1.4.0 were internal development milestones. All changes
from these versions are included in the v1.5.0 release above.

## [1.1.0] - 2026-01-23

### Bug Fixes

- **validate_marketplace:** Detect git source type with local plugins

### Documentation

- Add marketplace installation notice to README
- **skill:** Document CRITICAL source schema error for local plugins
- Update CHANGELOG.md

### Features

- **validation:** Bump version to 1.1.0

### Miscellaneous Tasks

- Add git-cliff configuration and changelog
- Update CHANGELOG.md with latest changes

---
*Generated by [git-cliff](https://git-cliff.org)*
