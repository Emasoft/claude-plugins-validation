# Plugin Management — Detailed Command Reference

## Table of Contents

- [Local Install / Update / Uninstall](#local-install--update--uninstall)
- [Enable / Disable](#enable--disable-with-smart-name-resolution-and-scope)
- [Validate](#validate-190-rules)
- [Security Audit](#security-audit)
- [List / Search / Doctor](#list--search--doctor)
- [Marketplace](#marketplace)
- [Remote Plugins](#remote-plugins-github-marketplaces)
- [Version Bump](#version-bump)
- [Creation & Publishing](#creation--publishing)
- [Flags](#flags)
- [Plugin Variables](#plugin-variables)
- [Notes](#notes)

## Local Install / Update / Uninstall

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" ./plugin/ my-mkt
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" plugin.tar.gz my-mkt
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --update ./plugin-v2/ my-mkt
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --uninstall name@mkt
```

## Enable / Disable (with smart name resolution and scope)

Smart plugin name resolution — accepts 3 formats:
- `plugin-name` — auto-resolves if unique across all settings/marketplaces
- `plugin-name@marketplace` — explicit marketplace
- `plugin-name@owner/marketplace` — disambiguate same-name marketplaces

### User level (default — `~/.claude/settings.json`)
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin>
```

### Project-local (`<project>/.claude/settings.local.json`)
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin> --scope local
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin> --scope local
```

### Scope Precedence

Discovered from live testing with Claude Code restarts:
- User `True` **overrides** local `False` — a user-level enable cannot be disabled locally
- User **not set** + local `True` → plugin enabled only in this project
- `--scope local` + enable → sets local `True` AND **removes** the key from user settings (so user `True` doesn't override)

The script checks that the plugin is installed before enabling/disabling. Exits with error if not found.

## Validate (190+ rules)

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" ./plugin/
```

For GitHub repos without installing:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --plugin owner/repo
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --marketplace owner/repo
```

## Security Audit

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --audit-plugin owner/repo
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --audit-marketplace owner/repo
skill-audit ./plugin/ -v
```

Checks: prompt injection, secrets (TruffleHog), shell issues (ShellCheck), code vulnerabilities (Semgrep).

## List / Search / Doctor

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --list
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --search <query>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --marketplace <name|owner/name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" [--verbose] [--fix]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --version
```

- `--list`: All locally installed plugins with version and enabled status
- `--search <query>`: Search by component type or free text
- `--marketplace <name>`: List all plugins in a marketplace with version and enabled status

## Marketplace

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" add owner/repo
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" remove <name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" list [--json]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" update [name]
```

## Remote Plugins (GitHub marketplaces)

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" install <plugin>@<mkt>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" update <plugin>@<mkt>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" uninstall <plugin>@<mkt>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" enable <plugin>@<mkt>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" disable <plugin>@<mkt>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" list [--available] [--json]
```

## Version Bump

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --patch
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --minor
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --major
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --set 2.0.0
```

Updates plugin.json + pyproject.toml.

## Creation & Publishing

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_plugin_repo.py" <target> --name <n> --description <d> --author <a> --author-email <e> --github-owner <o>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_marketplace_repo.py" <target> --name <n> --owner-name <o> --github-owner <o> [--add-plugin owner/repo]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <path> --fix [--marketplace owner/repo]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <path> --fix
```

Related commands: `/cpv-create-local-plugin`, `/cpv-create-local-marketplace`, `/cpv-publish-a-plugin-as-github-repo`, `/cpv-create-a-github-marketplace`, `/cpv-publish-a-plugin-to-a-github-marketplace`, `/cpv-standardize`.

## Flags

`-f`/`--force` install despite errors | `-n`/`--dry-run` preview only | `-q`/`--quiet` suppress output | `-v`/`--verbose` full details + security audit | `--scope user|local` enable/disable target

## Plugin Variables

- `${CLAUDE_PLUGIN_ROOT}` — plugin installation directory (changes on update)
- `${CLAUDE_PLUGIN_DATA}` — persistent state directory (survives updates, at `~/.claude/plugins/data/{id}/`)

## Notes

- Run `/reload-plugins` after install/update/uninstall/enable/disable
- Backups: `~/.claude/backups/`
- Plugin persistent data: `${CLAUDE_PLUGIN_DATA}` survives updates; deleted on uninstall (use `--keep-data` to preserve)
- Settings: `~/.claude/settings.json` (user), `<project>/.claude/settings.local.json` (local)
