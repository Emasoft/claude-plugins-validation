---
name: cpv-diagnose-plugin-architecture
description: "Advisory diagnostic that detects when a Claude Code plugin ships files not needed at runtime — build-only source, dependency trees, dev-only dirs, regenerable build caches — and recommends the existing CPV lean-separation. Use when the user asks 'is my plugin too big', 'what files ship', 'reduce install size', 'lean the plugin', 'diagnose plugin architecture', 'unneeded files shipped', or wants to know which paths bloat an install. Read-only: it diagnoses and points at the separate strip-dev / ${CLAUDE_PLUGIN_DATA} step; it NEVER moves or deletes anything. Used dynamically via cpv-the-skills-menu; loaded by cpv-plugin-diagnoser-agent agent."
when_to_use: When a plugin install looks bloated and you want a read-only report of which shipped paths are build-only / dev-only / runtime-deps / build-caches, with the exact existing CPV remediation for each — without touching anything runtime-essential.
user-invocable: false
---

# cpv-diagnose-plugin-architecture

## Overview

This skill detects when a Claude Code plugin ships files that are not
needed at install/runtime, and recommends the EXISTING CPV lean-separation
machinery for each. It is the DETECTION front-end; the actual separation is
a SEPARATE step (`cpv strip-dev-parts`, `.gitignore`, the
`${CLAUDE_PLUGIN_DATA}` install-on-first-use pattern).

**This skill is ADVISORY and read-only.** It runs a diagnostic engine,
parses the result, presents a findings table, and gives per-category
remediation pointers. It NEVER moves, deletes, gitignores, or mutates
anything in the target plugin. The user runs the separate remediation step.

It NEVER recommends touching a runtime-essential component (manifest,
skills, commands, agents, hooks, MCP/LSP configs, monitors, `bin/`
binaries, themes, output styles, settings). When a path's status is in
doubt, the engine classifies it ship-always.

**Profile note (submodule-build / binary-release plugins).** When the
plugin's pipeline profile (`resolve_pipeline_profile()` in
`scripts/cpv_pipeline_profile.py`) is `submodule-build` or
`binary-release`, the build-source submodule and the committed `bin/`
binaries are BY-DESIGN shipped artifacts — the diagnosis EXCLUDES them
from strip/gitignore recommendations. So an `UNKNOWN`-category dir on
such a plugin may simply be that build-source submodule or its
build-output; surface it for manual review rather than treating it as
bloat to strip.

## When to use

Use this skill when the user wants to know which shipped files bloat an
install — phrased as "is my plugin too big", "what files actually ship",
"reduce / shrink the install size", "lean the plugin", "diagnose plugin
architecture", "unneeded files shipped", or "what can I strip". It answers
that question and hands back the exact existing CPV remediation per finding.

Do NOT use it to perform the separation — that is the `cpv-strip-dev-submodules`
skill (`cpv strip-dev-parts`) plus manual `.gitignore` / hook edits. This
skill only diagnoses and recommends.

## Prerequisites

- `claude-plugins-validation` plugin installed (the engine ships in its
  `scripts/`).
- LOCAL filesystem access — the engine walks the plugin tree on disk. A URL
  cannot be diagnosed; pass a local plugin path.
- `python3` available (the engine has no third-party deps).

## Inputs

| Shape | Example |
|---|---|
| A local plugin root | the absolute path to the plugin directory |
| Size threshold (optional) | `--threshold-mb N` to change the large-file/dir cutoff |
| Default (no arg) | the current directory, if it is a plugin |

A URL or `owner/repo` is NOT a valid input — the engine needs filesystem
access to measure byte sizes and resolve `${CLAUDE_PLUGIN_ROOT}`
references.

## Workflow

Treat the engine `scripts/cpv_diagnose_architecture.py` as a BLACK BOX.
Invoke it via the documented CLI and parse the documented JSON — do not
read or reason about its internals.

1. **Run the engine in JSON mode** against the target plugin:

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_diagnose_architecture.py" <plugin_path> --json
   ```

   It exits 0 always (advisory; it never blocks). Add `--threshold-mb N` to
   change the large-file/dir size cutoff. Capture stdout — it is EXACTLY
   one JSON object and nothing else.

2. **Parse the JSON.** The documented schema:

   ```json
   {
     "plugin_path": "<abs>",
     "total_tracked_bytes": 0,
     "shipped_runtime_bytes": 0,
     "strippable_bytes": 0,
     "findings": [
       {"path": "rust/", "category": "BUILD_SOURCE", "bytes": 0,
        "reason": "<one line>", "remediation": "<one line>",
        "strip_extract_entry": {"src": "rust/", "submodule": "<owner>/<repo>-rust"},
        "tracked": true}
     ],
     "recommendations": {
       "strip_extract": [ {"src": "rust/", "submodule": "..."} ],
       "gitignore_add": ["target/", "node_modules/"],
       "claude_plugin_data": ["node_modules/"]
     }
   }
   ```

   `category` is one of `RUNTIME_ESSENTIAL`, `BUILD_SOURCE`, `RUNTIME_DEP`,
   `DEV_ONLY`, `BUILD_CACHE`, `UNKNOWN`. `strip_extract_entry` is `null`
   when the finding is not a strip candidate. Empty `findings` /
   `recommendations` means the plugin is already lean.

3. **Present a numbered findings table** with a leftmost `#` column, one
   row per finding (skip `RUNTIME_ESSENTIAL` — those are not problems), and
   a size summary line:

   ```text
   | # | Path           | Category     | Size   | Tracked | Recommendation (one line) |
   |---|----------------|--------------|--------|---------|---------------------------|
   | 1 | rust/          | BUILD_SOURCE | 4.2 MB | yes     | Strip into a git submodule |
   | 2 | node_modules/  | RUNTIME_DEP  | 31 MB  | yes     | Install on first use; gitignore |
   | 3 | target/        | BUILD_CACHE  | 88 MB  | yes     | Gitignore (currently tracked → invalid) |

   Potential install savings: <strippable_bytes as MB> across <N> paths.
   ```

   Convert bytes to MB for display. Take the savings line from
   `strippable_bytes` and the finding count.

4. **Give the per-category remediation** for each finding, pointing at the
   EXISTING machinery (see [the quick map below](#remediation-by-category)).

5. **State clearly that this is advisory** — list the separate commands /
   edits the user runs themselves; do NOT run them.

## Remediation by category

For each finding, recommend by its `category` (full detail in
[build-only-taxonomy.md](references/build-only-taxonomy.md)):

- **`BUILD_SOURCE`** (source that only produces `bin/`) — strip into a
  per-plugin git submodule. Paste the finding's `strip_extract_entry` into
  `cpv.strip.extract[]` in `plugin.json`, then the user runs the SEPARATE
  step (preview first):

  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" <plugin_path> --dry-run
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" <plugin_path> --auto
  ```

  (Equivalently `cpv strip-dev-parts <plugin_path>`.) Claude Code's
  shallow clone does not recurse submodules, so the stripped content never
  reaches end-user installs.

- **`RUNTIME_DEP`** (dependency trees like `node_modules/`, `.venv/`) — do
  NOT ship them. Recommend the `${CLAUDE_PLUGIN_DATA}` install-on-first-use
  pattern: a `SessionStart` hook that populates `${CLAUDE_PLUGIN_DATA}` on
  first run (the canonical npm hook is in
  [anthropic-plugin-components.md](references/anthropic-plugin-components.md#the-two-path-variables-that-drive-the-taxonomy)),
  AND add the path to `.gitignore` so it stops being tracked. The engine
  lists these in `recommendations.claude_plugin_data` and
  `recommendations.gitignore_add`.

- **`DEV_ONLY`** (`tests/`, `design/`, `examples/`, ...) — strip into a
  submodule via `cpv.strip.extract[]` (the strip-dev DEFAULT already
  extracts `tests/`), OR gitignore if the folder is regenerable. `.github/`
  workflows are INFO-priority only — repo-needed, not install-needed; do
  not recommend removing them.

- **`BUILD_CACHE`** (`target/`, `__pycache__/`, `dist/`, ...) — gitignore
  it. If the finding's `"tracked": true`, it currently ships and is
  INVALID: recommend `git rm -r --cached <path>` then add it to
  `.gitignore`. The engine lists cache paths in
  `recommendations.gitignore_add`.

- **`RUNTIME_ESSENTIAL`** — leave it alone. Never recommend touching it.

- **`UNKNOWN`** — surface it for manual review; never auto-recommend
  removal.

## Output

A numbered findings table (leftmost `#` column) of non-runtime paths with
size + category + tracked status + one-line remediation, a "Potential
install savings: X MB across N paths" line, and the per-category
remediation pointers. Plus an explicit reminder that the skill is advisory
— the user runs the separate strip / gitignore / hook step.

When `findings` is empty, report that the plugin is already lean (nothing
non-runtime ships) and stop.

## Error Handling

| Situation | Handling |
|---|---|
| Input is a URL / `owner/repo` | The engine needs filesystem access — ask for a LOCAL plugin path instead. |
| Path is not a plugin (no `.claude-plugin/plugin.json`, no `SKILL.md` at root) | Report it is not a recognizable plugin; do not invent findings. |
| Engine prints non-JSON or non-zero exit | Surface the engine's message verbatim; do not fabricate a result. Re-run without `--json` for the human table if helpful. |
| A finding's category is `UNKNOWN` | Present it for manual review; never auto-recommend removal. |
| User asks to APPLY a fix | This skill is advisory — hand off to `cpv-strip-dev-submodules` / manual `.gitignore` / hook edits; do not mutate the plugin here. |

## Resources

- [Runtime-essential components](references/anthropic-plugin-components.md) — the authoritative ship-always list from the Anthropic plugins-reference
  > Ship-always component table · The ${CLAUDE_PLUGIN_ROOT} reference rule · Why bin/ ships but its source does not · Conventional small root files · The two path variables that drive the taxonomy
- [Build-only taxonomy](references/build-only-taxonomy.md) — the 4 categories, per-language pattern lists, and per-category remediation recipe
  > Category 1 — BUILD_SOURCE · Category 2 — RUNTIME_DEP · Category 3 — DEV_ONLY · Category 4 — BUILD_CACHE · FN-safety rules every recommendation obeys · Category to remediation quick map
- `cpv-strip-dev-submodules` skill — the SEPARATE engine that performs the submodule extraction this skill recommends.
- `cpv-canonical-pipeline` skill — pipeline standards for the plugin shape.
