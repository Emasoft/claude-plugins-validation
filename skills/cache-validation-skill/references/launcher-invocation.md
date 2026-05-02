# Canonical Launcher Invocation (cache alias)

## Table of Contents

- [The one-liner](#the-one-liner)
- [Why the launcher is mandatory](#why-the-launcher-is-mandatory)
- [Direct invocation (development only)](#direct-invocation-development-only)

## The one-liner

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MAIN_ROOT="$(git worktree list | head -n1 | awk '{print $1}')"
else
  MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
mkdir -p "$MAIN_ROOT/reports/validate_cache"
REPORT="$MAIN_ROOT/reports/validate_cache/$(date +%Y%m%d_%H%M%S%z)-<slug>.md"

CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  cache "<plugin_or_project_path>" --report "$REPORT"
```

## Why the launcher is mandatory

CPV's `validate_cache.py` refuses direct invocation from the plugin
cache via `check_remote_execution_guard()`. The launcher
(`remote_validation.py`) sets up environment isolation BEFORE importing
the validator module, so the **target plugin's** `pyproject.toml` /
`.mypy.ini` / stale `cpv_validation_common.py` cannot interfere.

`${CLAUDE_PLUGIN_ROOT}` is exported automatically by Claude Code and
points at the locally-installed CPV plugin. Do NOT search for the
launcher with `find` — `${CLAUDE_PLUGIN_ROOT}` already resolves it.

## Direct invocation (development only)

From a CPV development checkout (NOT the plugin cache):

```bash
uv run --with pyyaml python scripts/validate_cache.py /path/to/plugin --strict
```
