---
name: install-plugin
description: >
  Use when installing, uninstalling, or managing Claude Code plugins locally
  without a GitHub marketplace. Trigger with "install plugin" or "manage local plugins".
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

This skill wraps `scripts/claude-plugin-install.py` — a self-contained Python tool that installs, validates, uninstalls, and manages Claude Code plugins locally. It creates a local marketplace structure and registers the plugin in Claude Code's runtime registry (`known_marketplaces.json`).

Use this when the user wants to install a plugin **without** setting up a GitHub marketplace. For GitHub-based distribution, use the `setup-github-marketplace` skill instead.

## Prerequisites

- [ ] Python 3.8+ available (the script has no external dependencies)
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

Install output includes: extraction status, validation results, marketplace registration, permissions fixes.

## Error Handling

| Problem | Solution |
|---------|----------|
| "Plugin manifest not found" | Ensure `.claude-plugin/plugin.json` exists in the plugin root |
| "Marketplace already exists" | Use `--force` to overwrite existing installation |
| "Permission denied on hooks" | The script auto-fixes permissions; if it fails, manually `chmod +x` hook scripts |
| "settings.json parse error" | The script handles JSONC (comments + trailing commas) automatically |

If the user reports issues, run `--doctor` first to diagnose.

## Examples

Install a plugin from a local directory:
```bash
uv run scripts/claude-plugin-install.py ./my-awesome-plugin
```

Install from a tarball with custom marketplace name:
```bash
uv run scripts/claude-plugin-install.py plugin-v1.0.tar.gz my-plugins
```

Dry-run to preview changes:
```bash
uv run scripts/claude-plugin-install.py ./plugin --dry-run
```

Validate and health-check after install:
```bash
uv run scripts/claude-plugin-install.py --validate my-plugin@local-my-plugin
uv run scripts/claude-plugin-install.py --doctor
```

## Resources

- Script: `scripts/claude-plugin-install.py`
- Command: `/cpv-install-plugin`
- Related: `/cpv-validate-plugin` (run after installation for full 190+ rule validation)
- Related: `/cpv-setup-github-marketplace` (for GitHub-hosted distribution instead)

## What the Script Does

1. **Extracts** the plugin from archive (.tar.gz, .zip, .tar.bz2, .tar.xz) or copies from directory
2. **Validates** the plugin structure (manifest, hooks, agents, skills, scripts, MCP servers)
3. **Wraps** the plugin into a local marketplace structure under `~/.claude/plugins/marketplaces/`
4. **Registers** the marketplace in Claude Code's `known_marketplaces.json` runtime registry
5. **Fixes permissions** on hook scripts and other executables (chmod +x)
6. **Creates backups** of all modified settings files before changes

## Critical Rules

1. ALWAYS run the script via `uv run` to ensure the correct Python environment
2. ALWAYS validate after installation: `uv run scripts/claude-plugin-install.py --validate name@marketplace`
3. NEVER manually edit `known_marketplaces.json` — use the script's install/uninstall commands
4. Cross-platform: works on macOS, Linux, and Windows
