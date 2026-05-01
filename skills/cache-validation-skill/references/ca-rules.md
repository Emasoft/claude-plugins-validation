# CA-01..CA-06 cache-invalidation rule pack

## Table of Contents

- [CA-01 — dynamic placeholders in cached content](#ca-01)
- [CA-02 — SessionStart / UserPromptSubmit / PreCompact write CLAUDE.md or settings](#ca-02)
- [CA-03 — hook scripts flip permissions / enabledMcpServers between turns](#ca-03)
- [CA-04 — `model:` frontmatter forces in-line model switch](#ca-04)
- [CA-05 — hook scripts run unbounded-output commands](#ca-05)
- [CA-06 — PreCompact / PostCompact / SubagentStart hooks don't preserve prefix](#ca-06)
- [Why these specific six](#why-these-specific-six)

The six rules CPV's `validate_cache.py` enforces — derived from
*"Lessons from Building Claude Code: Prompt Caching Is Everything"* by
Thariq Shihipar (Anthropic) and the open-source
[ussumant/cache-audit](https://github.com/ussumant/cache-audit) corpus.

## Rule index

- [CA-01 — dynamic placeholders in cached content](#ca-01)
- [CA-02 — SessionStart / UserPromptSubmit / PreCompact write CLAUDE.md or settings](#ca-02)
- [CA-03 — hook scripts flip permissions / enabledMcpServers between turns](#ca-03)
- [CA-04 — `model:` frontmatter forces in-line model switch](#ca-04)
- [CA-05 — hook scripts run unbounded-output commands](#ca-05)
- [CA-06 — PreCompact / PostCompact / SubagentStart hooks don't preserve prefix](#ca-06)

## CA-01

**Severity:** MAJOR.
**Catches:** Dynamic placeholders inside `CLAUDE.md`, `agents/*.md`,
`skills/*/SKILL.md` — `{{TIMESTAMP}}`, `$(date)`, `${RANDOM}`, etc.
Each rendered prefix becomes a unique cache key, so the cache hit-rate
collapses to zero.

**Fix recipe:** `skills/fix-validation/references/cache-fixes.md#ca-01`.
Move the dynamic value out to a `SessionStart` hook's
`additionalContext` block (post-cache).

## CA-02

**Severity:** MAJOR.
**Catches:** `SessionStart` / `UserPromptSubmit` / `PreCompact` hooks
that WRITE to `CLAUDE.md` or `settings.json`. Each write rotates a byte
in the cached prefix, killing the cache for the next turn.

**Fix recipe:** `cache-fixes.md#ca-02`. Refactor the hook to emit
`additionalContext` instead of mutating files.

## CA-03

**Severity:** MAJOR.
**Catches:** Hook scripts that flip `permissions.allow` /
`permissions.deny` / `enabledMcpServers` between turns. The settings
hash is part of the cache key.

**Fix recipe:** `cache-fixes.md#ca-03`. Declare the union of needed
permissions up front; gate behaviour on a runtime check, not a
permission rotation.

## CA-04

**Severity:** MINOR.
**Catches:** `SKILL.md model:` frontmatter that forces an in-line
model switch. Each model switch forks the cache into per-model variants.

**Fix recipe:** `cache-fixes.md#ca-04`. Move the model selection to
a dedicated agent that owns the model — keeps the skill cache-shareable.

## CA-05

**Severity:** MINOR.
**Catches:** Hook scripts running unbounded-output commands like
`git status`, `find`, `ls -laR`, `cat <large-file>` without size caps.
Output bloat lands in `additionalContext` and breaks ETag-style cache
heuristics.

**Fix recipe:** `cache-fixes.md#ca-05`. Cap output via `head -c`,
`--max-count`, or `wc -l` checks.

## CA-06

**Severity:** WARNING.
**Catches:** `PreCompact` / `PostCompact` / `SubagentStart` hooks that
don't preserve the cached prefix on compaction. After the next compact,
the prefix's byte layout shifts and the cache is invalidated.

**Fix recipe:** `cache-fixes.md#ca-06`. Have the hook emit only
`additionalContext`; never write to files in the cached prefix.

## Why these specific six

The rules came from real-world incident reports and the cache-audit
corpus. Each rule corresponds to a measurable cache-MISS pattern that
scaled out to >10x the expected token cost. CPV does NOT add
speculative rules — the six are the documented set as of CC v2.1.121.
