---
name: cache-validation-skill
description: Validate plugins / projects against Anthropic's prompt-cache invalidation patterns (CA-01..CA-06). Use when auditing for cache regressions or fixing CA-01..CA-06 findings. Used dynamically via skills-index (TRDD-478d9687).
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
[references/ca-rules.md](references/ca-rules.md). Full TOC of every
rule is embedded under "Resources" at the bottom of this file.

## Prerequisites

- A Claude Code plugin directory OR a project root that uses Claude Code.
- `uv` available on PATH so the validator can run via the launcher
  (`uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" cache <path>`).

## Scanner contract

Same I/O contract as `validate_security.py`: path-only stdout (auto-save
report, print compact summary), aggregated `(level, rule_id)` grouping,
self-scan filter chain (skips catalog / test / dev-scratch files).

## Instructions

1. Read [references/launcher-invocation.md](references/launcher-invocation.md) for the canonical bash one-liner (alias `cache`).
   > The one-liner · Why the launcher is mandatory · Direct invocation (development only)
2. Resolve `MAIN_ROOT` via `git worktree list | head -n1`.
3. Run the launcher with `--report "$MAIN_ROOT/reports/validate_cache/<TS>-<slug>.md"`.
4. Read the compact summary from stdout, full details from the report file.
5. (Fix workflow only — cache-optimizer-agent.) Group findings by CA-NN, apply `cache-fixes.md#ca-nn` from `skills/fix-validation/references/`, re-run, iterate until VALID.

## Examples

```bash
# Audit a plugin (or any project root with CLAUDE.md / .claude/)
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  cache ~/Code/my-plugin/ --report "$REPORT"
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
- [ ] Build report path under `${MAIN_ROOT}/reports/validate_cache/<TS>-<slug>.md`
- [ ] `mkdir -p` the parent directory
- [ ] Run via launcher: `python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" cache <path> --report <path>`
- [ ] Read summary from stdout, details from the report file
- [ ] (Fix only) Re-run after each batch until verdict = VALID

## Resources

- `scripts/validate_cache.py` — validator
- [references/ca-rules.md](references/ca-rules.md) — per-rule details
  > CA-01 — dynamic placeholders in cached content · CA-02 — SessionStart / UserPromptSubmit / PreCompact write CLAUDE.md or settings · CA-03 — hook scripts flip permissions / enabledMcpServers between turns · CA-04 — `model:` frontmatter forces in-line model switch · CA-05 — hook scripts run unbounded-output commands · CA-06 — PreCompact / PostCompact / SubagentStart hooks don't preserve prefix · Why these specific six
- `skills/fix-validation/references/cache-fixes.md` — fix recipes
- `tests/test_validate_cache.py` — 36 tests
- [ussumant/cache-audit](https://github.com/ussumant/cache-audit) — corpus
