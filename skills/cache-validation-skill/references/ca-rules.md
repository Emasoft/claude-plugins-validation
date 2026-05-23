# CA-01..CA-07 cache-invalidation rule pack

## Table of Contents

- [CA-01 — dynamic placeholders in cached content](#ca-01)
- [CA-02 — SessionStart / UserPromptSubmit / PreCompact write CLAUDE.md or settings](#ca-02)
- [CA-03 — hook scripts flip permissions / enabledMcpServers between turns](#ca-03)
- [CA-04 — `model:` frontmatter forces in-line model switch](#ca-04)
- [CA-05 — hook scripts run unbounded-output commands](#ca-05)
- [CA-06 — PreCompact / PostCompact / SubagentStart hooks don't preserve prefix](#ca-06)
- [CA-07 — `context: fork`/`branch` re-primes the cache from cold](#ca-07)
- [Why these specific seven](#why-these-specific-seven)

The six rules CPV's `validate_cache.py` enforces — derived from
*"Lessons from Building Claude Code: Prompt Caching Is Everything"* by
Thariq Shihipar (Anthropic) and the open-source
[ussumant/cache-audit](https://github.com/ussumant/cache-audit) corpus.

**Since v2.102.0 all six rules are WARNING severity** — a cache miss costs
tokens/latency but never makes a plugin invalid. `validate_plugin` CALLS this
scanner as a separate step that writes its own report (the main report carries
only a one-line pointer); the standalone `cpv-cache-optimize` audit/fix
commands act on the findings. The severity labels per rule below all read
WARNING for that reason.

## Rule index

- [CA-01 — dynamic placeholders in cached content](#ca-01)
- [CA-02 — SessionStart / UserPromptSubmit / PreCompact write CLAUDE.md or settings](#ca-02)
- [CA-03 — hook scripts flip permissions / enabledMcpServers between turns](#ca-03)
- [CA-04 — `model:` frontmatter forces in-line model switch](#ca-04)
- [CA-05 — hook scripts run unbounded-output commands](#ca-05)
- [CA-06 — PreCompact / PostCompact / SubagentStart hooks don't preserve prefix](#ca-06)
- [CA-07 — `context: fork`/`branch` re-primes the cache from cold](#ca-07)

## CA-01

**Severity:** WARNING.
**Catches:** Dynamic placeholders inside `CLAUDE.md`, `agents/*.md`,
`skills/*/SKILL.md` — `{{TIMESTAMP}}`, `$(date)`, `${RANDOM}`, etc.
Each rendered prefix becomes a unique cache key, so the cache hit-rate
collapses to zero.

**Fix recipe:** `skills/fix-validation/references/cache-fixes.md#ca-01`.
Move the dynamic value out to a `SessionStart` hook's
`additionalContext` block (post-cache).

## CA-02

**Severity:** WARNING.
**Catches:** `SessionStart` / `UserPromptSubmit` / `PreCompact` hooks
that WRITE to `CLAUDE.md` or `settings.json`. Each write rotates a byte
in the cached prefix, killing the cache for the next turn.

**Fix recipe:** `cache-fixes.md#ca-02`. Refactor the hook to emit
`additionalContext` instead of mutating files.

## CA-03

**Severity:** WARNING.
**Catches:** Hook scripts that flip `permissions.allow` /
`permissions.deny` / `enabledMcpServers` between turns. The settings
hash is part of the cache key.

**Fix recipe:** `cache-fixes.md#ca-03`. Declare the union of needed
permissions up front; gate behaviour on a runtime check, not a
permission rotation.

## CA-04

**Severity:** WARNING.
**Catches:** A `model:` frontmatter on ANY component — `agents/*.md`,
`commands/*.md`, or `skills/*/SKILL.md` — that forces an in-line model
switch. Each model keeps a separate cache, so a pinned component pays a
cold-cache miss on every dispatch instead of reusing the session's warm
prefix. `model: inherit` is exempt (it uses the session model, no switch).

**Fix recipe:** `cache-fixes.md#ca-04`. Drop the `model:` line so the
component inherits the session model (or use `model: inherit`). Do NOT
move the pin to an agent — as of v2.102.0 agents AND commands are flagged
too, because the cache cost is identical wherever the pin lives.

## CA-05

**Severity:** WARNING.
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

## CA-07

**Severity:** WARNING.
**Catches:** A `context: fork` (or its synonym `context: branch`) on a
skill or command. Forking spins up a fresh subagent whose system-prompt +
tool-schema prefix is re-primed from cold — up to ~1M tokens when the
harness carries many skills / MCP servers / tools. Only CLAUDE.md and the
rules files survive a fork unchanged.

**Fix recipe:** `cache-fixes.md#ca-07`. Drop the `context:` field to inherit
the parent context, UNLESS the work needs a fresh context (independent audit
/ error-checking, free of parent baggage) or the room a fresh context buys
(reading many files) — those cases justify the cost. For presentation-only
forks (e.g. a menu), prefer a post-turn `Stop`-hook emitter (the externalised
`claude-menu-system` plugin) over forking a subagent mid-turn.

## Why these specific seven

CA-01..CA-06 came from real-world incident reports and the cache-audit
corpus — each corresponds to a measurable cache-MISS pattern that scaled
out to >10x the expected token cost (the documented set as of CC v2.1.121).
CA-07 (v2.102.0) is CPV's addition: `context: fork`/`branch` re-primes the
whole prefix from cold, the single largest avoidable cache cost when it is
used for work that doesn't need a fresh context. CPV does NOT add
speculative rules — every rule maps to a concrete, measurable cost.
