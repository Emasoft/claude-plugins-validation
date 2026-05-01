---
name: cache-validation-skill
description: Validate Claude Code plugins / projects against Anthropic's prompt-cache invalidation patterns (CA-01..CA-06). Loaded by the cache-optimizer agent for both audit and fix workflows.
when_to_use: When auditing a plugin or project for prompt-cache regressions before release, or when an existing scan flagged CA-01..CA-06 issues that need fixing. Always loaded by cache-optimizer; never invoke directly.
user-invocable: false
allowed-tools: Read, Bash, Glob, Grep, Edit, Write
---

# Cache-Audit Skill (loaded by cache-optimizer)

## Overview

Anthropic's prompt cache caches the rendered system-prompt prefix — `CLAUDE.md`, cached agent/skill bodies, and settings-derived blocks. The cache is invalidated whenever ANY byte in that prefix changes. A 200K-token system-prompt cache MISS costs ~10x normal token rate vs a HIT. CPV's cache-audit rule pack catches the six documented patterns that silently break caching, force expensive re-renders, or fork the cached prompt into many distinct cache keys (one per session).

This skill is the agent-facing wrapper around `scripts/validate_cache.py`. It documents the validator's contract, the six rules, and how the cache-optimizer agent should consume the report.

## Prerequisites

- A Claude Code plugin directory OR a project root that uses Claude Code (`.claude/` configs, `CLAUDE.md`).
- `uv` available on PATH so the validator can run via `uv run python scripts/validate_cache.py`.

## Scanner contract (mirrors `validate_security.py`)

The cache validator follows the same I/O contract as the security validator so any agent that loads this skill knows what to expect:

- **Default output is path-only.** Without `--json` or `--report`, the script auto-saves the aggregated report to `${CLAUDE_PROJECT_DIR}/reports/cache/<timestamp>-<slug>.md` and prints **only** the compact summary (counts table + verdict + plugin path + report path) to stdout. The agent reads the report file when it needs the details.
- **Aggregated reporting.** Findings group by `(level, rule_id)` so each CA rule's full explanation appears once, followed by a count and capped file:line list — token-bounded.
- **Self-scan filter chain.** The validator skips files marked as catalog/test/dev-scratch (`cpv_self_scan_skip`, `_is_vendored_dep_path`, `_is_dev_scratch_path`, `_is_test_file_path`, `is_fp_corpus_markdown`) so a plugin that ships its own validator catalogs doesn't trigger CA findings on its own catalogs.

## The six rules

| Rule | Severity | What it catches | Fix reference |
|---|---|---|---|
| **CA-01** | MAJOR | Dynamic placeholders in cached content (`{{TIMESTAMP}}`, `$(date)`, `${RANDOM}`, etc.) inside `CLAUDE.md`, `agents/*.md`, `skills/*/SKILL.md` | `skills/fix-validation/references/cache-fixes.md#ca-01` |
| **CA-02** | MAJOR | `SessionStart` / `UserPromptSubmit` / `PreCompact` hooks that WRITE to `CLAUDE.md` or `settings.json` | `cache-fixes.md#ca-02` |
| **CA-03** | MAJOR | Hook scripts that flip `permissions.allow` / `permissions.deny` / `enabledMcpServers` between turns | `cache-fixes.md#ca-03` |
| **CA-04** | MINOR | `SKILL.md model:` frontmatter forcing in-line model switch (use a dedicated agent instead) | `cache-fixes.md#ca-04` |
| **CA-05** | MINOR | Hook scripts running unbounded-output commands (`git status`, `find`, `ls -laR`, `cat <large-file>`) without size caps | `cache-fixes.md#ca-05` |
| **CA-06** | WARNING | `PreCompact` / `PostCompact` / `SubagentStart` hooks that don't preserve the cached prefix | `cache-fixes.md#ca-06` |

## Audit workflow (read-only)

1. Determine `MAIN_ROOT` from the running git worktree (or `CLAUDE_PROJECT_DIR` when not a git repo).
2. Build a timestamped report path: `${MAIN_ROOT}/reports/cache/$(date +%Y%m%d_%H%M%S%z)-<slug>.md` (the `${...}` braces tell the skill validator this is a shell-resolved variable, not a Claude `$<name>` substitution).
3. Run:
   ```bash
   uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_cache.py" <plugin_or_project_path> --report <report_path>
   ```
4. Read the compact summary from stdout and the full per-rule aggregated report from the report file.

## Fix workflow (cache-optimizer only)

When the agent has read a report and decided to fix issues:

1. Group findings by rule (CA-NN) so the same fix recipe is applied to every occurrence at once.
2. For each rule, follow `skills/fix-validation/references/cache-fixes.md#ca-nn`.
3. Apply edits via `Edit` tool. Re-read each file before each edit (auto-compaction may have stale state).
4. Re-run `validate_cache.py` after each batch. Iterate until verdict = VALID.
5. If the user asked for broader cache-aware improvements (skills/agents/commands/CLAUDE.md/rules), proceed beyond strict CA-01..CA-06:
   - Identify cached content that could be made smaller without losing semantic value
   - Move dynamic content out of cached prefix into hook output (`additionalContext`)
   - Split bloated `CLAUDE.md` into a small cached core + larger uncached references
   - Audit `model:` frontmatter across skills (per CA-04, prefer agent-level model switches)
   - Document the cache-cost rationale in a `## Cache Notes` block at the end of `CLAUDE.md` so future maintainers don't regress

## Output

When called for AUDIT only: return the report path.
When called for FIX: return the new report path AFTER fixes plus the diff stats (commits made, findings before/after).

## Resources

- `scripts/validate_cache.py` — the validator (CA-01..CA-06 implementation)
- `skills/fix-validation/references/cache-fixes.md` — per-rule fix recipes
- `tests/test_validate_cache.py` — 36 tests covering positive + negative for each rule
- *"Lessons from Building Claude Code: Prompt Caching Is Everything"* — Thariq Shihipar (Anthropic), the underlying reference
- [ussumant/cache-audit](https://github.com/ussumant/cache-audit) — the open-source corpus the rule pack derives from
