---
name: install-plugin
description: >
  Install or manage Claude Code plugins locally without a GitHub marketplace.
  Use when installing or managing plugins from local paths. Trigger with /cpv-install-plugin.
tags:
  - plugin
  - install
  - local
  - management
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep, AskUserQuestion
user-invocable: false
---

# Local Plugin Installation

## Overview

Wraps `scripts/claude-plugin-install.py` — a self-contained Python tool that installs, validates, uninstalls, and manages plugins locally. Creates a local marketplace structure and registers it in Claude Code's runtime registry (`known_marketplaces.json`).

Use when the user wants to install a plugin **without** a GitHub marketplace. For GitHub-based distribution, use `setup-github-marketplace` instead.

## Prerequisites

- [ ] Python 3.8+ available (no external dependencies)
- [ ] The plugin to install has a valid `.claude-plugin/plugin.json` manifest
- [ ] Run via `uv run` to ensure the correct Python environment

## Instructions

Copy this checklist and track your progress:

1. Locate the plugin source (archive or directory with `.claude-plugin/plugin.json`)
2. Run the install command:
   ```bash
   uv run scripts/claude-plugin-install.py <archive-or-dir>
   ```
3. Verify installation succeeded:
   ```bash
   uv run scripts/claude-plugin-install.py --validate <name@marketplace>
   ```
4. Run health check to confirm everything is registered:
   ```bash
   uv run scripts/claude-plugin-install.py --doctor
   ```

### Additional commands

- **Custom marketplace name**: `uv run scripts/claude-plugin-install.py <source> my-plugins`
- **Force reinstall**: `uv run scripts/claude-plugin-install.py <source> --force`
- **Dry run**: `uv run scripts/claude-plugin-install.py <source> --dry-run`
- **List installed**: `uv run scripts/claude-plugin-install.py --list`
- **Uninstall**: `uv run scripts/claude-plugin-install.py --uninstall <name@marketplace>`
- **Update**: `uv run scripts/claude-plugin-install.py --update <new-source> <marketplace>`
- **Enable**: `uv run scripts/claude-plugin-install.py --enable <name@marketplace>`
- **Disable**: `uv run scripts/claude-plugin-install.py --disable <name@marketplace>`
- **Quiet mode**: Add `-q` or `--quiet` to suppress non-error output

## Output

The script prints colored status messages:
- Green `[OK]` — successful operation
- Yellow `[WARN]` — non-blocking advisory
- Red `[ERR]` — blocking error

Output includes: extraction status, validation results, registration, permissions fixes.

## Error Handling

| Problem | Solution |
|---------|----------|
| "Plugin manifest not found" | Ensure `.claude-plugin/plugin.json` exists in plugin root |
| "Marketplace already exists" | Use `--force` to overwrite |
| "Permission denied on hooks" | Script auto-fixes; if it fails, manually `chmod +x` hook scripts |
| "settings.json parse error" | Script handles JSONC (comments + trailing commas) automatically |

If the user reports issues, run `--doctor` first to diagnose.

## Examples

```bash
uv run scripts/claude-plugin-install.py ./my-plugin         # install from directory
uv run scripts/claude-plugin-install.py plugin.tar.gz mymp   # install from tarball
uv run scripts/claude-plugin-install.py ./plugin --dry-run   # preview changes
uv run scripts/claude-plugin-install.py --doctor             # health check
```

## Resources

- Script: `scripts/claude-plugin-install.py`
- Related: `/cpv-validate-plugin`, `/cpv-setup-github-marketplace`

## Token Optimization

- Use `--quiet` flag in automated contexts
- Use `--dry-run` first if unsure — avoids filesystem changes
- ALWAYS run via `uv run`; NEVER manually edit `known_marketplaces.json`
