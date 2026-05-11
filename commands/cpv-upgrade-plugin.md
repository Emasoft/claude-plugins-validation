---
name: cpv-upgrade-plugin
description: Upgrade an existing plugin to the current CPV pipeline standards (idempotent publish.py, cpv_lint_engine, cross-platform Python + pathlib, sanitized inputs, no bash scripts). Runs the 82-check Pre-completion verification matrix AND a real publish.py + gh run watch on the resulting tag — total time 10-15 minutes.
argument-hint: <plugin-path> [--critical-only]
user-invocable: true
skills:
  - marketplace-authoring-contract
---

# /cpv-upgrade-plugin

## Marketplace Authoring Contract (MANDATORY READ)

BEFORE drafting, modifying, or migrating ANY `marketplace.json`, read:
`skills/marketplace-authoring-contract/SKILL.md` and ALL its references.

Failure to apply the contract produces user-facing install failures —
the doctor agent catches these after the fact but at high opus token
cost. The user expects this agent to produce correct output on the
FIRST try, not after N validator retries.

Upgrade an existing Claude Code plugin to the current pipeline standards
documented in `skills/fix-validation/references/pipeline-migration.md`:

| Migration | What it does |
|---|---|
| §1 — Stale script references | Replace removed lint scripts with `cpv_lint_engine` in CI; drop from pre-push hook (validator covers it). |
| §2 — Whole-repo lint via cpv_lint_engine | Delete legacy lint scripts; consolidate per-language CI lint steps into one call. |
| §3 — Cross-platform Python | Convert shipped `.sh` scripts to Python (§3a); convert bash hook commands to Python delegation (§3b); convert `os.path`/`os.system`/hardcoded paths to `pathlib`+`subprocess.run([...], timeout=N)` (§3c). |
| §4 — Idempotent publish.py | Regenerate via `gen_publish_py`, OR add the 5 helpers (`_read_remote_version`, `_infer_bump_type`, `_git_porcelain_clean`, `_head_commit_message`, `_local_tag_exists`) + idempotent guards in `stage_bump` / `stage_commit_and_push`. |
| §5 — Sanitize every script-input parameter | Validate every CLI flag / env-var / JSON field at the boundary using a canonical regex (REPO_PATTERN, SEMVER_PATTERN, NAME_PATTERN, TAG_PATTERN). Never `shell=True`. |

## What this will do (read carefully — total time 10-15 minutes)

This is **not just a code rewrite**. It is a full migration with a
hard exit gate. Per [issue #21 ask #1](https://github.com/Emasoft/claude-plugins-validation/issues/21),
the agent will **only** declare success after BOTH of:

1. **The 82-check Pre-completion verification matrix** in
   [`references/canonical-pipeline-migration-checklist.md`](../references/canonical-pipeline-migration-checklist.md)
   returns exit 0 (every BLOCKER + MAJOR check passes). The matrix covers
   workflow YAML integrity, Python source quality, hook shape, publish.py,
   plugin.json, .gitignore, CPV self-validate, canonical-template parity,
   tests, git state, smoke-test publish, marketplace, notification chain,
   hooks.json, MCP servers, and docs & changelog. Output is a
   Unicode-bordered Markdown table the agent will show you.
2. **A real `publish.py --patch` run** completes AND `gh run watch
   --exit-status` reports green CI on the resulting tag. **If the plugin
   lives in a Layout-C marketplace** (or is registered in any external
   marketplace), the marketplace's own `publish.py` ALSO runs to green CI
   on its own tag.

If either fails, the agent returns `[PARTIAL]` (NOT `[DONE]`) with the
specific `CHECK-NN` failures or the failing CI job's log URL, and asks
you what to do next:

- **(a) Fix manually** — surface the failures, you fix, re-invoke.
- **(b) Re-run with `--force-templates`** — overwrites canonical files
  (publish.py, ci.yml, pre-push, cliff.toml, etc.) with templates.
  **WARNING**: hand-tuned customisations to those files will be lost.
- **(c) Abort** — leave the plugin in its current state, stop.

The agent will **never** silently `--force-templates` when checks fail.

## Usage

```bash
# Full upgrade — apply every migration that applies + run the 82-check
# matrix + real publish + gh run watch (recommended).
/cpv-upgrade-plugin /path/to/my-plugin

# Only fix CRITICAL findings (publish-blockers + security blockers).
# Skips the 82-check matrix and the real-publish step.
/cpv-upgrade-plugin /path/to/my-plugin --critical-only
```

## Behavior

The command dispatches the **plugin-fixer agent** (model: opus) with a
prompt requesting the full pipeline migration. The agent runs
validate→fix→re-validate in a loop until the plugin is clean, then
runs the 82-check Pre-completion verification matrix and the real-publish
gate. Returns when:

- The 82-check matrix passes AND `gh run watch` reports green CI for the
  plugin (and marketplace, if applicable) tags → **[DONE]**, OR
- The 82-check matrix fails on a BLOCKER/MAJOR check → **[PARTIAL]** with
  the failing CHECK-NN list, OR
- `gh run watch` reports a failed CI run on the tag → **[PARTIAL]** with
  the failing job's `gh run view` URL, OR
- A finding requires human judgment (the agent surfaces it and stops).

### Marketplace upstream cross-check gate (TRDD-c0ee9543 Phase F)

Before declaring `[DONE]`, the plugin-fixer agent MUST run
`validate_marketplace.py --strict` on any marketplace.json that lists
this plugin (Layout C self-marketplace, sibling Layout A hub, or
Layout B parent monorepo) and confirm exit 0 with NO
`RC-MKPL-NAME-MISMATCH`, `RC-MKPL-UNKNOWN-FIELD`, or
`RC-MKPL-UNKNOWN-SOURCE-FIELD` findings.

These three MAJOR codes block install at runtime (per the 2026-05-11
`ai-maestro-visual-communicator-plugin` incident — the plugin was at
v1.2.2 but the marketplace entry pinned v1.0.0, declared a divergent
name, and carried unrecognised `scope` fields on 9 sibling entries;
the upgrade agent shipped the marketplace without re-aligning the
entries and `claude plugin install` failed with "not found").

Fix recipes:
[skills/fix-validation/references/marketplace-upstream-drift.md](../skills/fix-validation/references/marketplace-upstream-drift.md).

The agent MUST distinguish:
- **Agent-introduced drift** (no `_cpv_skip_upstream_check` flag,
  no `.cpv-no-upstream-check` sentinel): refuse to ship, realign
  the marketplace entry to upstream `plugin.json` via §1 / §3 / §4
  recipes. NEVER add the opt-out flag silently.
- **User-blessed drift** (per-entry opt-out OR sentinel present BEFORE
  the upgrade ran): pass through.

For a deep diagnosis BEFORE fixing (and to also audit security +
cross-platform + marketplace registration + cache sync), run
`/cpv-diagnose-plugin` first and then pick the upgrade option from the
follow-up menu.

## When NOT to use

- For a fresh plugin you're scaffolding from scratch — use `/cpv-create`
  instead. Newly-scaffolded plugins already ship with current standards.
- When you only want to fix specific findings from a validation report —
  use `/cpv-fix-validation <report.md>` with a `min_severity`. That path
  skips the 82-check matrix and the real-publish step.
- When you cannot let the agent push to GitHub (offline / no network /
  PAT not configured). The real-publish step is non-skippable; without
  push permissions the agent returns `[PARTIAL]` at step 7d.

## Output

The plugin-fixer writes the iteration log to
`$MAIN_ROOT/reports/plugin-fixer/<ts±tz>-<plugin-name>.md` and the
82-check matrix table to
`$MAIN_ROOT/reports/canonical-pipeline-migration/<ts±tz>-run-all.md`.
Returns a 3-line summary + the report paths + the green CI run URL.
After completion, the post-fix table prompts: "Do something else?" with
options for re-diagnose, register marketplace, or end.
