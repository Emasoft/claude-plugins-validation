# TRDD-ebc745b5 — Claude Code v2.1.143 changelog catch-up

**TRDD ID:** `ebc745b5-5563-44af-860d-5e2f5e002f46`
**Filename:** `design/tasks/TRDD-ebc745b5-5563-44af-860d-5e2f5e002f46-cc-changelog-v2_1_143.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)
**Status:** In progress
**Created:** 2026-05-16

---

## Context

Claude Code released v2.1.143 on 2026-05-15. CPV's last catch-up shipped
in v2.87.0 (TRDD-81250f5a) and covered up to v2.1.142. This TRDD covers
the gap.

Changelog entries for v2.1.143 (https://code.claude.com/docs/en/changelog.md):

- **New hook field** `terminalSequence` — desktop notifications, window
  titles, bells without a controlling terminal
- **New env var** `CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY=1`
  — opt out of PowerShell's `-ExecutionPolicy Bypass` default
- **New env var** `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` — override the
  default cap of 8 consecutive hook blocks before turn ends with a
  warning
- **New setting** `worktree.bgIsolation: "none"` — allow background
  sessions to edit the working copy directly without `EnterWorktree`
  for repos where worktrees are impractical
- **PowerShell tool default change** — now enabled by default on
  Windows for Bedrock/Vertex/Foundry users (opt out with
  `CLAUDE_CODE_USE_POWERSHELL_TOOL=0`)
- **`claude agents` flags** — `--add-dir`, `--settings`, `--mcp-config`,
  `--plugin-dir`, `--permission-mode`, `--model`, `--effort`,
  `--dangerously-skip-permissions`
- **`/bg` flag preservation** — `--mcp-config`, `--settings`,
  `--add-dir`, `--plugin-dir`, `--strict-mcp-config`,
  `--fallback-model`, `--allow-dangerously-skip-permissions`
- **Behavioural fixes** — `/goal` evaluator timing, background daemon
  fallback when launcher missing, stale-fragment rendering on Windows
  Terminal, 5xx error messages on custom gateways

## Audit — what is in scope for the plugin validator?

| Item | Status | Action |
|---|---|---|
| Hook field `terminalSequence` | Already in CPV v2.84.0 (`UNIVERSAL_OUTPUT_FIELDS` in `scripts/validate_hook_output.py`) | Regression test only |
| Env var `CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY` | NEW | Add to `VALID_PLUGIN_ENV_VARS` |
| Env var `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` | NEW | Add to `VALID_PLUGIN_ENV_VARS` |
| Setting `worktree.bgIsolation` | NEW | Document on the `worktree` allow-list comment; no enum-narrowing because the spec only lists one value (`"none"`) so far. Add a positive regression test that round-trips it through the project/local scope validators without false-flagging. |
| PowerShell default behaviour | CLI behaviour, no validator impact | None |
| `claude agents` flags | CLI behaviour, no validator impact | None |
| `/bg` flag preservation | CLI behaviour, no validator impact | None |
| `/goal`, daemon-fallback, render fixes | CLI behaviour, no validator impact | None |

All items in scope are additions — no validator semantics change,
no breaking change.

## Files to modify

1. **`scripts/cpv_validation_common.py`** — append the two new env
   vars to `VALID_PLUGIN_ENV_VARS` (alongside the existing
   `CLAUDE_CODE_USE_POWERSHELL_TOOL` line block), each with a v2.1.143
   provenance comment.
2. **`scripts/cc_scope_rules.py`** — expand the `worktree`
   allow-list comment to enumerate the supported sub-keys
   (`sparsePaths` since v2.1.76, `baseRef` since v2.1.133,
   `bgIsolation` since v2.1.143) for documentation clarity. No code
   change — `worktree.*` already passes through because the
   allow-list only inspects top-level key names.
3. **`tests/test_v2_1_143_changelog.py`** — NEW, mirrors
   `test_v2_1_142_changelog.py`:
   - Positive: both new env var names recognised by
     `is_valid_plugin_env_var()`.
   - Positive: a `settings.json` carrying `{"worktree": {"bgIsolation":
     "none"}}` does not raise an unknown-key warning in either
     `validate_local_scope.py` or `validate_project_scope.py`.
   - Regression: `terminalSequence` still in the universal hook-output
     allow-list (pinning the v2.84.0 work in case of future drift).

## Verification

```bash
cd "${CLAUDE_PLUGIN_ROOT}"   # plugin root resolved via the runtime env var
uv run pytest tests/test_v2_1_143_changelog.py -x -q
uv run pytest tests/ -x -q --tb=short
uv run ruff check .
```

## Release

After all tests pass:

```bash
uv run python scripts/publish.py --minor    # v2.87.1 → v2.88.0
```

Minor bump (not patch) because two new env vars and a new settings
sub-key extend the validation surface area.

## Cross-references

- Previous catch-up: `TRDD-81250f5a-...-cc-changelog-v2_1_142.md`
- Earlier sweep: `TRDD-3199124d-...-cc-changelog-v2_1_141.md`
- Hook output field set: `scripts/validate_hook_output.py`
  `UNIVERSAL_OUTPUT_FIELDS`
- Env var allow-list: `scripts/cpv_validation_common.py`
  `VALID_PLUGIN_ENV_VARS`
- Settings key allow-list: `scripts/cc_scope_rules.py`
  `KNOWN_SETTINGS_KEYS`
