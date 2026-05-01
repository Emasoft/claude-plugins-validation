---
name: cache-validation-skill
description: Validate plugins / projects against Anthropic's prompt-cache invalidation patterns (CA-01..CA-06). Loaded by cache-optimizer-agent. Use when auditing for cache regressions or fixing CA-01..CA-06 findings.
when_to_use: When auditing a plugin or project for prompt-cache regressions before release, or fixing an existing CA-01..CA-06 scan finding. Always loaded by cache-optimizer-agent; never invoke directly.
user-invocable: false
allowed-tools: Read, Bash(uv:*), Bash(git:*), Bash(mkdir:*), Bash(date:*), Glob, Grep, Edit, Write
---

# Cache-Audit Skill (loaded by cache-optimizer-agent)

## Overview

Anthropic's prompt cache caches the rendered system-prompt prefix —
`CLAUDE.md`, cached agent/skill bodies, and settings-derived blocks. The
cache is invalidated whenever ANY byte in that prefix changes. A 200K-token
prefix cache MISS costs ~10x normal token rate vs a HIT. CPV's cache-audit
rule pack catches the six documented patterns that silently break caching
or fork the cached prompt into many distinct keys.

Per-rule severity, catch description, and fix-recipe pointer live in
[references/ca-rules.md](references/ca-rules.md):

- CA-01 — dynamic placeholders in cached content
- CA-02 — SessionStart / UserPromptSubmit / PreCompact write CLAUDE.md or settings
- CA-03 — hook scripts flip permissions / enabledMcpServers between turns
- CA-04 — `model:` frontmatter forces in-line model switch
- CA-05 — hook scripts run unbounded-output commands
- CA-06 — PreCompact / PostCompact / SubagentStart hooks don't preserve prefix
- Why these specific six

## Prerequisites

- A Claude Code plugin directory OR a project root that uses Claude Code.
- `uv` available on PATH so the validator can run via
  `uv run python scripts/validate_cache.py`.

## Scanner contract

Same I/O contract as `validate_security.py`:

- **Default output is path-only.** Without `--json` / `--report` the
  script auto-saves the report and prints only the compact summary
  (counts + verdict + paths) to stdout.
- **Aggregated reporting.** Findings group by `(level, rule_id)` so each
  rule's full explanation appears once with a count + capped file:line
  list.
- **Self-scan filter chain.** Skips catalog / test / dev-scratch files
  (`cpv_self_scan_skip`, `_is_vendored_dep_path`, `_is_dev_scratch_path`,
  `_is_test_file_path`, `is_fp_corpus_markdown`).

## Instructions

Do steps 1–4 in ONE Bash tool call so shell variables persist:

1. Resolve `MAIN_ROOT` to the **main checkout root** (first entry of
   `git worktree list`). NEVER the worktree's own root — its
   `./reports/` is gitignored and disappears on merge.
2. Build the report path under `${MAIN_ROOT}/reports/cache/`.
3. `mkdir -p` the parent.
4. Run `validate_cache.py` with `--report`.
5. Read summary from stdout, details from the report file.
6. (Fix workflow only — cache-optimizer-agent.) Group findings by
   CA-NN, apply `cache-fixes.md#ca-nn` from
   `skills/fix-validation/references/`, re-run, iterate until VALID.

```bash
MAIN_ROOT="$(git worktree list | head -n1 | awk '{print $1}')"
[ -z "${MAIN_ROOT}" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
REPORT="${MAIN_ROOT}/reports/cache/$(date +%Y%m%d_%H%M%S%z)-<slug>.md"
mkdir -p "$(dirname "$REPORT")"
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_cache.py" \
  <plugin_or_project_path> --report "$REPORT"
```

## Examples

```bash
# Audit a plugin (or any project root with CLAUDE.md / .claude/)
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_cache.py" \
  ~/Code/my-plugin/ --report "$REPORT"
```

## Error Handling

- **`uv` missing on PATH** — exit code 4. Install via
  `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **Target has no `.claude/` and no `CLAUDE.md`** — INFO + "no cached
  content to audit".
- **Report-path unwritable** — exit 4 + prints the bad path.
- **`MAIN_ROOT` empty (no git)** — the prologue's fallback handles this:
  `[ -z "${MAIN_ROOT}" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"`.

## Output

- AUDIT call: return the report path.
- FIX call: return the new report path AFTER fixes plus the diff stats
  (commits made, findings before/after).

## Checklist

Copy this checklist and track your progress:

- [ ] Resolve `MAIN_ROOT` via `git worktree list | head -n1`
- [ ] Build report path under `${MAIN_ROOT}/reports/cache/<TS>-<slug>.md`
- [ ] `mkdir -p` the parent directory
- [ ] Run `validate_cache.py --report` against the target
- [ ] Read summary from stdout, details from the report file
- [ ] (Fix only) Re-run after each batch until verdict = VALID

## Resources

- `scripts/validate_cache.py` — validator
- [references/ca-rules.md](references/ca-rules.md) — per-rule details
- `skills/fix-validation/references/cache-fixes.md` — fix recipes
- `tests/test_validate_cache.py` — 36 tests
- [ussumant/cache-audit](https://github.com/ussumant/cache-audit) — corpus
