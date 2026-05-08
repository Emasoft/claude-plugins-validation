---
name: cpv-upgrade-plugin
description: Upgrade an existing plugin to the current CPV pipeline standards (idempotent publish.py, cpv_lint_engine, cross-platform Python + pathlib, sanitized inputs, no bash scripts). Dispatches plugin-fixer with the full migration prompt.
argument-hint: <plugin-path> [--critical-only]
user-invocable: true
---

# /cpv-upgrade-plugin

Upgrade an existing Claude Code plugin to the current pipeline standards
documented in `skills/fix-validation/references/pipeline-migration.md`:

| Migration | What it does |
|---|---|
| §1 — Stale script references | Replace removed lint scripts with `cpv_lint_engine` in CI; drop from pre-push hook (validator covers it). |
| §2 — Whole-repo lint via cpv_lint_engine | Delete legacy lint scripts; consolidate per-language CI lint steps into one call. |
| §3 — Cross-platform Python | Convert shipped `.sh` scripts to Python (§3a); convert bash hook commands to Python delegation (§3b); convert `os.path`/`os.system`/hardcoded paths to `pathlib`+`subprocess.run([...], timeout=N)` (§3c). |
| §4 — Idempotent publish.py | Regenerate via `gen_publish_py`, OR add the 5 helpers (`_read_remote_version`, `_infer_bump_type`, `_git_porcelain_clean`, `_head_commit_message`, `_local_tag_exists`) + idempotent guards in `stage_bump` / `stage_commit_and_push`. |
| §5 — Sanitize every script-input parameter | Validate every CLI flag / env-var / JSON field at the boundary using a canonical regex (REPO_PATTERN, SEMVER_PATTERN, NAME_PATTERN, TAG_PATTERN). Never `shell=True`. |

## Usage

```bash
# Full upgrade — apply every migration that applies (recommended).
/cpv-upgrade-plugin /path/to/my-plugin

# Only fix CRITICAL findings (publish-blockers + security blockers).
/cpv-upgrade-plugin /path/to/my-plugin --critical-only
```

## Behavior

The command dispatches the **plugin-fixer agent** (model: opus) with a
prompt requesting the full pipeline migration. The agent runs
validate→fix→re-validate in a loop until the plugin is clean. Returns
when:

- All findings ≥ `min_severity` are fixed, OR
- The fixer exhausted its 5-iteration budget (escalates to user), OR
- A finding requires human judgment (the agent surfaces it and stops).

For a deep diagnosis BEFORE fixing (and to also audit security +
cross-platform + marketplace registration + cache sync), run
`/cpv-diagnose-plugin` first and then pick the upgrade option from the
follow-up menu.

## When NOT to use

- For a fresh plugin you're scaffolding from scratch — use `/cpv-create`
  instead. Newly-scaffolded plugins already ship with current standards.
- When you only want to fix specific findings from a validation report —
  use `/cpv-fix-validation <report.md>` with a `min_severity`.

## Output

The plugin-fixer writes the iteration log to
`$MAIN_ROOT/reports/plugin-fixer/<ts±tz>-<plugin-name>.md` and returns
a 3-line summary + the path. After completion, the post-fix table
prompts: "Do something else?" with options for re-diagnose, register
marketplace, or end.
