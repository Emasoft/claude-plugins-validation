# Canonical Launcher Invocation

## Table of Contents

- [Why the launcher is mandatory](#why-the-launcher-is-mandatory)
- [The one-liner](#the-one-liner-use-this-verbatim)
- [Full alias table](#full-alias-table)
- [Direct invocation (development only)](#direct-invocation-development-only)

## Why the launcher is mandatory

CPV scripts in the plugin cache REFUSE to run when invoked directly. The
`check_remote_execution_guard()` in `cpv_validation_common.py` exits with
"remote location" error to prevent the **target plugin's** local config
files (`pyproject.toml`, `.mypy.ini`, stale copies of
`cpv_validation_common.py`) from interfering with the validator.

The fix is to **always** launch through `remote_validation.py`, which sets
up an isolated environment before importing the actual validator module.

## The one-liner (use this verbatim)

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MAIN_ROOT="$(git worktree list | head -n1 | awk '{print $1}')"
else
  MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
mkdir -p "$MAIN_ROOT/reports/validate_<component>"
REPORT_FILE="$MAIN_ROOT/reports/validate_<component>/$(date +%Y%m%d_%H%M%S%z)-<slug>.md"

CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  <alias> "$TARGET_PATH" --report "$REPORT_FILE"
```

`${CLAUDE_PLUGIN_ROOT}` is exported automatically by Claude Code when the
slash command / agent runs. It points at the locally-installed CPV plugin
under `~/.claude/plugins/cache/<marketplace>/claude-plugins-validation/<version>/`.

Do NOT search for the launcher with `find`, do NOT browse
`~/.claude/plugins/cache/`, do NOT pick a version yourself.

## Full alias table

| Alias | Underlying script | Purpose |
|---|---|---|
| `plugin` | validate_plugin.py | full plugin validation (190+ rules) |
| `skill` | validate_skill_comprehensive.py | single-skill validation |
| `marketplace` | validate_marketplace.py | marketplace.json structural check |
| `security` | validate_security.py | security scan + 5 external scanners + fclones dedup |
| `cache` | validate_cache.py | prompt-cache invalidation audit (CA-01..CA-06) |
| `hook` | validate_hook.py | hook config validation (28 events, 5 types) |
| `agent` | validate_agent.py | agent definition validation |
| `command` | validate_command.py | command definition validation |
| `mcp` | validate_mcp.py | MCP server config validation |
| `lsp` | validate_lsp.py | LSP server config validation |
| `xref` | validate_xref.py | cross-reference validation |
| `docs` | validate_documentation.py | documentation completeness |
| `encoding` | validate_encoding.py | UTF-8 / BOM / line endings |
| `rules` | validate_rules.py | rules directory validation |
| `enterprise` | validate_enterprise.py | enterprise compliance |
| `scoring` | validate_scoring.py | quality score (0-100) |
| `settings-marketplace` | validate_settings_marketplace.py | extraKnownMarketplaces validation |
| `telemetry` | validate_telemetry.py | OTEL telemetry supply-chain risk |
| `local-scope` | validate_local_scope.py | non-git-tracked .claude/ |
| `project-scope` | validate_project_scope.py | git-tracked .claude/ |
| `doctor` | manage_doctor.py | health-check installed plugins |
| `registry` | manage_registry.py | plugin registry operations |
| `github` | manage_github_validate.py | validate plugin/marketplace from GitHub URL |
| `standardize` | standardize_plugin.py | audit + fix plugin against CPV standards |

## Direct invocation (development only)

If you have the CPV source tree at hand (a development checkout, NOT the
plugin cache), you may invoke validators directly:

```bash
uv run --with pyyaml python scripts/validate_plugin.py /path/to/plugin --strict
```

The remote-execution guard fires only when the script lives under the
plugin cache AND `CLAUDE_PLUGIN_ROOT` is unset OR points elsewhere. From
a CPV development checkout the guard does not block.
