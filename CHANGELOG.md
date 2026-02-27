# Changelog

All notable changes to the Claude Plugins Validation plugin will be documented in this file.

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
- **validation_common:** Add WARNING level — never blocks validation, always reported
- **validation_common:** Add NIT level — blocks only in --strict mode
- **validation_common:** Add `exit_code_strict()` method for --strict mode
- **validation_common:** Add `warning()`, `nit()`, `has_warning`, `has_nit` to ValidationReport
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
- **validation_common:** Update VALID_HOOK_EVENTS to 18 events
- **validation_common:** Expand VALID_TOOLS with 11 new tools (Skill, AskUserQuestion, EnterPlanMode, etc.)
- **validation_common:** Add VALID_PLUGIN_ENV_VARS constant
- **validation_common:** Add system absolute path detection with shebang exclusion

### Documentation

- Update hook-validation reference with new events, agent hook type, and JSON schema rules
- Update marketplace-validation reference with owner, pip source, and reserved names
- Update mcp-validation reference with OAuth, SSE deprecation, and timeout validation
- Update validation-checklist with all new validation rules

### Architecture Refactoring

- **ValidationReport class unification**: Consolidated 11 duplicated ValidationReport implementations into a single class hierarchy
  - Canonical `ValidationReport` and `ValidationResult` in `validation_common.py`
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
