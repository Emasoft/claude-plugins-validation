# Batch-fix protocol — plugin-fixer agent modes

## Table of Contents

- [1. Batch-shard mode (`mode: batch_shard`, TRDD-71e68ab5)](#1-batch-shard-mode-mode-batch_shard-trdd-71e68ab5)
- [2. Batch-per-plugin mode (`mode: batch_per_plugin`, TRDD-3dcbb37c)](#2-batch-per-plugin-mode-mode-batch_per_plugin-trdd-3dcbb37c)
- [3. Batch same-turn modes (`batch_same_turn_validate_fix` / `batch_same_turn_full`, TRDD-3dcbb37c §3)](#3-batch-same-turn-modes-batch_same_turn_validate_fix--batch_same_turn_full-trdd-3dcbb37c-3)

This reference documents the per-mode workflow + status-JSON shapes the
`plugin-fixer` agent follows when dispatched in a batch mode. The three
core JSON file shapes (`index.json`, shard manifest, shard status) live in
[`json-schemas.md`](json-schemas.md). This file adds the two *agent-side*
status shapes that the planner does not write (`batch_per_plugin` and the
two `batch_same_turn` variants) plus the per-mode workflow rules.

Common rules for EVERY batch mode. Write the status JSON **before exit** —
even on `partial`/`error`/`failed`. Return **EXACTLY ONE line** to the
orchestrator — no prose, menus, or tables. A shard/agent **cannot spawn
other subagents** (Anthropic spec); where a single plugin exceeds the
safe-ceiling, consume shard manifests *sequentially* within the same
context (never spawn sub-subagents).

## 1. Batch-shard mode (`mode: batch_shard`, TRDD-71e68ab5)

You are one of N parallel shard-fixers. The `<context>` carries
`shard_manifest`, `shard_id`, `shard_of`, `status_path`, `plugin_path`.

**Scope ownership (schema v2).** Each `scopes[]` entry is a *scope*, not a
single file:

- `scope_kind: "skill_dir"` — the whole `skills/<name>/` tree (SKILL.md,
  `references/`, helper scripts). You MAY split an oversized SKILL.md into
  multiple smaller focused skills by **creating new sibling skill
  directories**, but their names MUST use a prefix matching the original (a
  `skills/<orig>/` scope may spawn `skills/<orig>-alpha/` +
  `skills/<orig>-beta/`) so they never collide with another shard's scope.
- `scope_kind: "file"` — a single file; no create/delete of siblings.

Workflow:

1. Read the shard manifest (`scopes[i].findings[]` lists your findings) — that IS
   your compact finding surface (a per-scope slice; never read the full report).
   Each scope's findings already carry file+line; optionally normalise via
   `cpv_fix_ledger.py build` for the by-file MECH/INTEL split.
2. Do NOT browse outside your scopes, do NOT re-run `validate_plugin.py`,
   do NOT read other shards' manifests — other shards edit concurrently.
3. **MECH first, then INTEL fix-as-you-go.** Auto-apply the deterministic set for
   your scope — `cpv_codemod.py apply --json <scope-findings.json> --apply` (zero
   LLM) — then fix the INTEL residual ONE FILE AT A TIME: read only each finding's
   line ranges (`tldr slice`/`Read` offset+limit — never the whole file), apply ALL
   of that file's fixes in the same turn (`fastedit`/`Edit`), never re-read a file.
   Route recipes via the `fix-validation` error-to-fix mappings (open a rule-TYPE's
   recipe once, not per finding).
4. When you create a sibling skill, declare it via `per_file[].errors[]`
   (e.g. `created sibling skill skills/<orig>-alpha/SKILL.md (split from
   skills/<orig>/)`) so the aggregator's report shows the rename.
5. Checkpoint `status_path` after each file (atomic) so a mid-shard crash
   is recoverable.

Return:

```text
[shard-K] done: <fixed>/<total> fixed, <remaining> remaining (status: <status_path>)
```

`agent_exit_reason`: `clean` (all fixed), `partial` (ran out of turns /
needs human), `error` (unrecoverable; capture in `per_file[].errors[]`).

**Re-validation is OUT OF SCOPE for a shard** — the full re-validate
happens after the aggregator returns. Status shape: see
[`json-schemas.md` §3](json-schemas.md#3-per-shard-status-shard-kstatusjson).

## 2. Batch-per-plugin mode (`mode: batch_per_plugin`, TRDD-3dcbb37c)

One of N parallel per-plugin fixers (marketplace/list input resolved to
>1 plugin). The `<context>` carries `plugin_index`, `plugin_path`,
`source_url`, `display_name`, `session_dir`, `status_path`,
`min_severity`, `shard_size`.

Apply the standard validate → fix → re-validate loop to the whole plugin
tree (no shard restrictions). If the plugin's finding count exceeds your
safe-ceiling, run `scripts/cpv_batch_planner.py` on this single plugin and
consume the shard manifests **sequentially** within this same context.
**Re-validation IS in scope** — run the final clean-room check before
reporting.

Status JSON (`status_path`):

```json
{
  "schema_version": 1,
  "plugin_index": <int>,
  "started_at": "<ISO8601±TZ>",
  "finished_at": "<ISO8601±TZ>",
  "status_symbol": "✓" | "✗" | "⚠",
  "status_label": "clean" | "fixed" | "partial" | "failed",
  "before": {"critical": <int>, "major": <int>, "minor": <int>, "nit": <int>, "warning": <int>},
  "after":  {"critical": <int>, "major": <int>, "minor": <int>, "nit": <int>, "warning": <int>},
  "report_path": "<abs-path-to-final-validation-report>",
  "notes": "<short summary>"
}
```

Return:

```text
[plugin-<plugin_index>] <label>: fixed=<X> remaining=<Y> (status: <status_path>)
```

Labels: `clean` (zero findings any severity), `fixed` (every finding
at-or-above `min_severity` fixed; residual below floor allowed),
`partial` (ran out / needs human), `failed` (unrecoverable; capture in
`notes`).

## 3. Batch same-turn modes (`batch_same_turn_validate_fix` / `batch_same_turn_full`, TRDD-3dcbb37c §3)

One of N per-plugin same-turn fixers (`/cpv-batch-validate-and-fix` /
`/cpv-batch-full-scan-and-fix`). Same `<context>` shape as
`batch_per_plugin`. Differences from per-plugin mode:

1. **Single-pass reads** — read each file ONCE, not the three times
   validate → fix → re-validate needs; track touched files.
2. **Inline FP verification** — for uncertain findings, call
   `llm-externalizer` with the **file-range syntax**
   (`<file>:<start>-<end>`, ≤ 200 LOC); never pass whole files when a
   range will do.
3. **No intermediate JSON report** — only the per-plugin status JSON is
   written; it is the source of truth.
4. **Checker scope:**

   | Mode | Checkers run |
   |---|---|
   | `batch_same_turn_validate_fix` | validate_plugin (full pipeline) + the v2.100.x context classifier as FP gate |
   | `batch_same_turn_full` | validate_plugin + validate_security (5 external scanners) + validate_cache + lint + xref + encoding + the v2.100.x context classifier as FP gate |

5. Status JSON adds two keys vs `batch_per_plugin`:

   ```json
   {
     "by_checker": {                          // batch_same_turn_full ONLY
       "validate": {"before": <int>, "after": <int>},
       "security": {"before": <int>, "after": <int>},
       "cache":    {"before": <int>, "after": <int>}
     },
     "fps_verified": <int>                    // total FP-verify calls
   }
   ```

   Return:

   ```text
   [plugin-<plugin_index>] <label>: fixed=<X> remaining=<Y> fps=<Z> (status: <status_path>)
   ```

6. **Iron rule for FPs** — silence a finding ONLY when the
   llm-externalizer call CONFIRMS the classifier's "looks-like-FP"
   hypothesis (the classifier returns `unknown` for many contexts and
   falls back to the heuristic chain). NEVER silence on the classifier
   alone. Record every silenced FP in the per-file `notes` (e.g.
   `agents/foo.md:42 RC-XYZ silenced — verified non-exploit shape via
   llm-externalizer`) so auditability is preserved without intermediate
   reports.
7. **Re-validation IS in scope** — run the final clean-room check exactly
   once, after every file is fixed; that produces `report_path`.
