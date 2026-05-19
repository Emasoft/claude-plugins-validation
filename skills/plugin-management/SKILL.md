---
name: plugin-management
description: >
  Manage Claude Code plugins: install, validate, audit, enable, disable, search, doctor.
  Use when managing plugin lifecycle. Used dynamically via skills-index (TRDD-478d9687).
user-invocable: false
---

# Plugin Management

## Overview

Scripts at `${CLAUDE_PLUGIN_ROOT}/scripts/` for the full plugin lifecycle.

## Prerequisites

- `uv` and `git` on PATH
- CPV plugin installed
- `gh` CLI (for remote/GitHub operations)

## Instructions

1. **Install**: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" ./plugin/ my-mkt`
2. **Update**: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --update ./plugin-v2/ my-mkt`
3. **Uninstall**: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --uninstall name@mkt`
4. **Enable/Disable**: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin> [--scope local]`
5. **Validate** (via launcher — direct script call refused): `uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" plugin ./plugin/`
6. **List/Search** (via launcher): `uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" registry --list` or `--search <query>`
7. **Doctor** (via launcher; or direct for `--install-scanners`): `uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" doctor [--verbose] [--fix]` — for `--install-scanners` (installs all 5 external security scanners + fclones), use `uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --install-scanners`
8. **Bump version**: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --patch|--minor|--major`

Smart plugin name resolution — accepts: `plugin-name`, `plugin-name@marketplace`, `plugin-name@owner/marketplace`.

Run `/reload-plugins` after install/update/uninstall/enable/disable.

Copy this checklist and track your progress:
- [ ] Plugin installed/updated
- [ ] Validation passed
- [ ] Plugin enabled (user or local scope)
- [ ] `/reload-plugins` executed

## Output

- Status messages with color-coded severity
- Tables for list/search/marketplace queries
- Validation reports with severity counts and VALID/INVALID verdict

## Error Handling

| Error | Resolution |
|-------|------------|
| Plugin not found | Check name with `--list`, use full `name@marketplace` format |
| Validation fails | Fix ALL CRITICAL/MAJOR issues — MINOR/NIT don't block install |
| `ModuleNotFoundError: yaml` | Use `uv run --with pyyaml python` outside CPV venv |
| Scope precedence issue | User `True` overrides local `False` — see [Detailed Commands](references/detailed-commands.md#scope-precedence) |

## Examples

**Install and validate:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" ./my-plugin/ my-marketplace
```

**Enable locally only:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable my-plugin --scope local
```

## Resources

- [Detailed Commands](references/detailed-commands.md) — full command reference with all flags, scope precedence, remote plugins, creation/publishing
  > Local Install / Update / Uninstall · Enable / Disable (with smart name resolution and scope) · Validate (190+ rules) · Security Audit · List / Search / Doctor · Marketplace · Remote Plugins (GitHub marketplaces) · Version Bump · Creation & Publishing · Flags · Plugin Variables · Notes
- [Hard-Won Lessons](references/hard-won-lessons.md) — 19 lessons from real publish runs
  > uv run --with pyyaml · gh secret --body flag · Update notify-marketplace.yml · MARKETPLACE_PAT env var · Strip ANSI codes · grep -oE not -oP · Standardize exit code 1 · author.email check · CI uv sync --extra dev · Update notify before push · Local dry-run · Verify CI after push · Checkov CKV2_ prefix · pytest exit code 5 · __init__.py no shebang · Repository field required · validate_marketplace paths · git config user setup · README sections required

## Token Optimization

Use LLM Externalizer MCP for bounded analysis. Pass file paths via `input_files_paths`.
