# Canonical Launcher Invocation (skill alias for semantic baseline)

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
mkdir -p "$MAIN_ROOT/reports/validate_skill"
REPORT="$MAIN_ROOT/reports/validate_skill/$(date +%Y%m%d_%H%M%S%z)-semantic-baseline.md"

CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  skill "<skill_path>" --strict --report "$REPORT"
```

## Why the launcher is mandatory

The launcher (`remote_validation.py`) sets up environment isolation
BEFORE importing `validate_skill_comprehensive.py`, so the **target
plugin's** `pyproject.toml` / `.mypy.ini` / stale
`cpv_validation_common.py` cannot interfere. Concretely it forces CPV's
own `scripts/` dir to the front of `sys.path`, writes a clean temporary
mypy config, strips `MYPYPATH` / `PYTHONPATH`, and sets
`CPV_REMOTE_VALIDATION=1` — all at import time, before the validator
module is loaded. Running the validator directly from the plugin cache
skips that setup, so the target's local config can shadow CPV's modules.

The semantic-validator runs the syntactic baseline FIRST (cheap, ~95% of
issues) before paying for opus-driven semantic evaluation.

## Direct invocation (development only)

From a CPV development checkout (NOT the plugin cache):

```bash
uv run --with pyyaml python scripts/validate_skill_comprehensive.py /path/to/skill --strict
```
