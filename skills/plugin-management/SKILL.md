---
name: plugin-management
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

### Scope precedence (discovered from live testing)
- User `True` **overrides** local `False` — a user-level enable cannot be disabled locally
- User **not set** + local `True` → plugin enabled only in this project
- `--scope local` + enable → sets local `True` AND **removes** the key from user settings (so user `True` doesn't override)

The script checks that the plugin is installed before enabling/disabling. Exits with error if not found.

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
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --marketplace <name|owner/name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" [--verbose]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --version
```

- `--list`: All locally installed plugins with version and enabled status
- `--search <query>`: Search by component type (`commands`, `agents`, `skills`, `hooks`, `mcp`, `lsp`, `rules`, `output-styles`) or free text
- `--marketplace <name>`: List all plugins in a marketplace with version, user-level and project-local enabled status

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

## Creation & Publishing (used by plugin-creator agent)

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

## Hard-Won Lessons (from real publish runs)

1. **Always `uv run --with pyyaml python`** when running CPV scripts from outside the CPV venv. Without it: `ModuleNotFoundError: No module named 'yaml'`.
2. **Always `--body` flag for `gh secret set`**. Piping does NOT work. Use: `gh secret set NAME --repo owner/repo --body "$VALUE"`
3. **Always update notify-marketplace.yml** after standardize. MARKETPLACE_OWNER/MARKETPLACE_REPO are placeholders.
4. **Check `$MARKETPLACE_PAT` env var** before asking the user: `test -n "$MARKETPLACE_PAT"` first.
5. **Strip ANSI codes** when processing validation output: `| sed 's/\x1b\[[0-9;]*m//g'`
6. **Use `grep -oE` not `grep -oP`** — macOS grep has no Perl regex.
7. **standardize_plugin.py exit code 1 is expected** after `--fix` if warnings remain.
8. **Check `author.email`** in plugin.json — suggest GitHub noreply format if missing.
9. **CI needs `uv sync --extra dev`** not just `uv sync` — without it ruff/pytest/mypy are missing.
10. **Update notify-marketplace.yml BEFORE the first push** — use `--marketplace` flag with standardize.
11. **Run local dry-run BEFORE first push**: `publish.py --gate` and `publish.py --patch --dry-run`.
12. **Verify CI AFTER first push**: `gh run list --repo <owner>/<name> --limit 5`.
13. **Checkov uses `CKV2_` prefix** for GitHub Actions (not `CKV_`).
14. **pytest exit code 5 = no tests collected** — OK for fresh plugins.
15. **`__init__.py` files do NOT need shebangs** — validator excludes them.
16. **Marketplace entries MUST include `repository` field** — without it, MAJOR validation error.
17. **`validate_marketplace.py` accepts both paths** — `marketplace.json` at root or `.claude-plugin/`.
18. **Set `git config user.name/email`** before committing in /tmp directories.
19. **Marketplace README needs Uninstall + Troubleshooting sections** — validator blocks on missing.

## TOKEN OPTIMIZATION
Use `mcp__plugin_llm-externalizer_llm-externalizer__*` tools instead of reading files into your context. Always pass file paths via `input_files_paths`.
