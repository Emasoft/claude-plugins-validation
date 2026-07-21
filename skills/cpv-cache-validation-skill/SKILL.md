---
name: cpv-cache-validation-skill
description: Validate plugins / projects against Anthropic's prompt-cache invalidation patterns (CA-01..CA-07). Use when auditing for cache regressions or fixing CA-01..CA-07 findings. Used dynamically via cpv-the-skills-menu (TRDD-478d9687).
when_to_use: When auditing a plugin or project for prompt-cache regressions before release, or fixing an existing CA-01..CA-07 scan finding. Always loaded by cpv-cache-optimizer-agent; never invoke directly.
user-invocable: false
---

# Cache-Audit Skill (loaded by cpv-cache-optimizer-agent)

## Overview

Anthropic's prompt cache caches the rendered system-prompt prefix —
`CLAUDE.md`, cached agent/skill bodies, and settings-derived blocks. The
cache is invalidated whenever ANY byte in that prefix changes. A 200K-token
prefix cache MISS costs ~10x normal token rate vs a HIT. CPV's cache-audit
rule pack catches the seven documented patterns that silently break caching
or fork the cached prompt into many distinct keys.

**Severity:** every CA-01..CA-07 finding is a **WARNING** (non-blocking — a
cache miss costs tokens/latency but never makes a plugin invalid).
`validate_plugin` CALLS this scanner as a SEPARATE step (its own report + a
one-line pointer in the main report); this skill + `cpv-cache-optimize` act
on the findings. CA-04 covers a `model:` frontmatter on ANY component
(agents, commands, skills); `model: inherit` is exempt.

Per-rule severity, catch description, and fix-recipe pointer live in
references/ca-rules.md. Full TOC of every
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
5. (Fix workflow only — cpv-cache-optimizer-agent.) Group findings by CA-NN, apply `cache-fixes.md#ca-nn` from `skills/cpv-fix-validation/references/`, re-run, iterate until ZERO CA findings remain. Every CA finding is a WARNING, so the verdict is VALID from the start — terminate on an empty findings set, not on VALID.

## Examples

```bash
# Audit a plugin (or any project root with CLAUDE.md / .claude/)
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  cache ~/Code/my-plugin/ --report "$REPORT"
```

## Error Handling

- **`uv` missing on PATH** — the `uv run` launcher fails at the shell
  level (exit 127, "command not found"); `remote_validation.py` never
  runs. Install via `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **Target does not exist** — `validate_cache.py` prints
  `Error: <target> does not exist` and exits 1 (`EXIT_CRITICAL`).
- **Target has no `.claude/` and no `CLAUDE.md`** — the scan returns a
  clean report (no CA findings, no cached content to audit), exit 0.
- **Report-path unwritable** — the report writer auto-creates the parent
  dir, then `write_text` raises an uncaught `OSError`/`PermissionError`;
  the process exits 1 with a traceback (e.g. a read-only filesystem).
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
- [ ] (Fix only) Re-run after each batch until zero CA findings remain (verdict is always VALID — cache findings are WARNING)

## Resources

- `scripts/validate_cache.py` — validator
- [references/ca-rules.md](references/ca-rules.md) — per-rule details
  > CA-01 — dynamic placeholders in cached content · CA-02 — SessionStart / UserPromptSubmit / PreCompact write CLAUDE.md or settings · CA-03 — hook scripts flip permissions / enabledMcpServers between turns · CA-04 — `model:` frontmatter forces in-line model switch · CA-05 — hook scripts run unbounded-output commands · CA-06 — PreCompact / PostCompact / SubagentStart hooks don't preserve prefix · CA-07 — `context: fork`/`branch` re-primes the cache from cold · Why these specific seven
- `skills/cpv-fix-validation/references/cache-fixes.md` — fix recipes
- `tests/test_validate_cache.py` — 54 tests
- [ussumant/cache-audit](https://github.com/ussumant/cache-audit) — corpus
