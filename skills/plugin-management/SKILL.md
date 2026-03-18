---
name: cpm-plugin-management
description: >
  Install, validate, audit, and manage Claude Code plugins from local sources or GitHub
  marketplaces. Triggers when user mentions installing plugins, validating plugins, security
  auditing, managing marketplaces, health checks, searching, listing, bumping versions,
  or any plugin lifecycle operation.
---

# Plugin Management

Modular management scripts at `${CLAUDE_PLUGIN_ROOT}/scripts/`. Run: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/<script>" <args>`

## Local Install / Update / Uninstall

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" ./plugin/ my-mkt
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" plugin.tar.gz my-mkt
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --update ./plugin-v2/ my-mkt
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --uninstall name@mkt
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable name@mkt
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable name@mkt
```

## Validate (190+ rules: manifest, hooks, frontmatter, MCP, LSP, security, encoding, enterprise)

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
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" [--verbose]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --version
```

Search types: `commands`, `agents`, `skills`, `hooks`, `mcp`, `lsp`, `rules`, `output-styles`, or any text.

## Marketplace

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" add owner/repo
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" remove <name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" list [--json]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" update [name]
```

## Remote Plugins (GitHub marketplaces)

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" install <plugin>@<mkt> [--scope user|project|local]
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

## Flags

`-f`/`--force` install despite errors | `-n`/`--dry-run` preview only | `-q`/`--quiet` suppress output | `-v`/`--verbose` full details + security audit

## Plugin Variables

- `${CLAUDE_PLUGIN_ROOT}` — plugin installation directory (changes on update)
- `${CLAUDE_PLUGIN_DATA}` — persistent state directory (survives updates, at `~/.claude/plugins/data/{id}/`)

## Notes

- Run `/reload-plugins` after install/update/uninstall
- Backups: `~/.claude/backups/`
- Plugin persistent data: `${CLAUDE_PLUGIN_DATA}` survives updates; deleted on uninstall (use `--keep-data` to preserve)

## TOKEN OPTIMIZATION
Use `mcp__plugin_llm-externalizer_llm-externalizer__*` tools instead of reading files into your context. Always pass file paths via `input_files_paths`.
