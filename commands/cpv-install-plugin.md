---
name: cpv-install-plugin
description: Install and manage Claude Code plugins locally
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep, AskUserQuestion
argument-hint: "<archive-or-dir> [<marketplace>] [--force] [--dry-run] | --update <src> <mkt> | --enable|--disable <name@mkt> | --list | --doctor"
agent: plugin-validator
user-invocable: true
---

# Install Plugin Locally

Install, validate, uninstall, and manage Claude Code plugins on your local machine without a GitHub marketplace.

## Usage

```
/cpv-install-plugin ./my-plugin.tar.gz
/cpv-install-plugin ./my-plugin-dir/ my-local-market
/cpv-install-plugin --validate ./my-plugin-dir/
/cpv-install-plugin --list
/cpv-install-plugin --uninstall my-plugin@my-local-market
/cpv-install-plugin --update ./new-version.tar.gz my-local-market
/cpv-install-plugin --enable my-plugin@my-local-market
/cpv-install-plugin --disable my-plugin@my-local-market
/cpv-install-plugin --doctor
```

## What It Does

1. Validates the plugin archive or directory against structural and manifest rules
2. Wraps the plugin into a local marketplace directory structure
3. Registers the plugin in Claude Code's runtime registry (`settings.json`)
4. Manages permissions and allowed-tools entries for the installed plugin
5. Supports full lifecycle: install, list, validate, uninstall, and health check

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `<archive-or-dir>` | Yes (for install) | Path to plugin `.tar.gz`/`.zip` archive or directory |
| `<marketplace>` | No | Target marketplace name (positional, defaults to `local`) |
| `--force` | No | Overwrite existing plugin installation |
| `--dry-run` | No | Simulate install without writing any files |
| `--uninstall <name@marketplace>` | No | Remove an installed plugin by `name@marketplace` |
| `--update <source> <marketplace>` | No | Update a plugin (uninstalls old, reinstalls from new source) |
| `--enable <name@marketplace>` | No | Enable a previously disabled plugin |
| `--disable <name@marketplace>` | No | Disable a plugin without removing it |
| `--validate <path>` | No | Validate a plugin directory without installing |
| `--list` | No | List all locally installed plugins |
| `--doctor` | No | Run health checks on all installed plugins and registries |
| `-q, --quiet` | No | Suppress non-error output and auto-confirm prompts |

## When To Use

When you want to install a plugin locally without setting up a GitHub marketplace. The script wraps the plugin into a local marketplace structure and registers it in Claude Code's runtime registry.

## Notes

- The script (`scripts/claude-plugin-install.py`) is self-contained (Python 3.8+, no external deps)
- Cross-platform: macOS, Linux, and Windows
- Creates backups of modified settings files before any writes
- For GitHub-hosted marketplace distribution, use `/cpv-setup-github-marketplace` instead
- Run `/cpv-validate-plugin` on the installed plugin to verify it passes all 190+ validation rules
