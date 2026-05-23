# Cache-Audit Fixes (CA-01..CA-06, validate_cache.py — v2.27.0+)

## Table of Contents

- [Overview](#overview)
- [CA-01 — Static prefix violation in cached content](#ca-01--static-prefix-violation-in-cached-content)
- [CA-02 — Hook writes to cached files (CLAUDE.md / settings.json)](#ca-02--hook-writes-to-cached-files-claudemd--settingsjson)
- [CA-03 — Hook flips MCP server enabled/disabled or permission allow/deny](#ca-03--hook-flips-mcp-server-enableddisabled-or-permission-allowdeny)
- [CA-04 — `model:` frontmatter forces an in-line model switch (any component)](#ca-04--model-frontmatter-forces-an-in-line-model-switch-any-component)
- [CA-05 — Hook script runs unbounded output commands](#ca-05--hook-script-runs-unbounded-output-commands)
- [CA-06 — Compaction/SubagentStart hook does not preserve cached prefix](#ca-06--compactionsubagentstart-hook-does-not-preserve-cached-prefix)
- [CA-07 — `context: fork`/`branch` re-primes the cache from cold](#ca-07--context-forkbranch-re-primes-the-cache-from-cold)

## Checklist

- [ ] Identify the CA-NN rule from the validation message
- [ ] Locate the offending file (the report names the path)
- [ ] Apply the fix from the matching section below
- [ ] Re-run `validate_cache.py --strict` to confirm
- [ ] If a hook is being modified, also re-run `validate_hook.py --strict`

## Overview

The Anthropic prompt cache caches the rendered system-prompt prefix (CLAUDE.md content + cached agent/skill bodies + settings-derived blocks). The cache is invalidated whenever any byte in that prefix changes. CPV's cache-audit rules catch six patterns that silently break caching, force expensive re-renders, or fork the cached prompt into many distinct cache keys (one per session).

These six rules are inexpensive to fix. The cost of NOT fixing them is paid every prompt: a 200K-token system-prompt cache miss costs ~10x normal token-rate vs a hit.

Since v2.102.0 EVERY cache finding (CA-01..CA-06) is reported at **WARNING** severity — a cache miss costs tokens/latency but never makes a plugin invalid. `validate_plugin` CALLS the cache validator as a separate step that writes its OWN report (the main report carries only a one-line pointer to it); the standalone `cpv-cache-optimize` audit/fix commands remain the way to act on these findings. The fixer applies these fixes only when the user asks to fix WARNING-level findings too.

## CA-01 — Static prefix violation in cached content

| Field | Value |
|---|---|
| **Severity** | WARNING |
| **Validator** | `validate_cache.py` |
| **Triggered by** | A `{{TIMESTAMP}}` placeholder, `$(date)`/backtick-date subshell, `${RANDOM}`, or any inline-evaluated dynamic substitution found in `CLAUDE.md`, `agents/*.md`, or `skills/*/SKILL.md` |
| **Why it matters** | These substitutions evaluate at session start to a fresh value, mutating every cached byte downstream and forcing a full re-render |
| **Root cause** | The author meant to display the current time/date/randomness in cached content. The cache cannot survive that |

### Fix

1. Remove the dynamic substitution entirely if the cached content does not actually need to change between sessions. The cache will then survive.
2. If the dynamic value is ESSENTIAL, move it OUT of the cached prefix into a hook that runs AFTER the prefix renders:
   - For per-prompt freshness, use a `UserPromptSubmit` hook that emits the timestamp into the message body
   - For per-session freshness, use a `SessionStart` hook with `additionalContext` (the additionalContext text appears AFTER the cached prefix and does NOT invalidate the cache)
3. If the value is per-skill, accept the cost: add a comment explaining why the dynamism is essential, and accept the cache miss for that skill.

```markdown
# WRONG — invalidates cache every session
This skill was loaded at {{TIMESTAMP}}.
The current date is $(date).

# RIGHT — static cached body, dynamic content via hook output
This skill renders the current time when invoked. See SessionStart hook output.
```

## CA-02 — Hook writes to cached files (CLAUDE.md / settings.json)

| Field | Value |
|---|---|
| **Severity** | WARNING |
| **Validator** | `validate_cache.py` |
| **Triggered by** | A `SessionStart`, `UserPromptSubmit`, or `PreCompact` hook that writes to `CLAUDE.md`, `.claude/CLAUDE.md`, or any `settings.json` file |
| **Why it matters** | The hook fires before/during cache rendering. Modifying cached source files mid-render forks the cache key per session |

### Fix

1. Move the write target OUT of cached files:
   - For dynamic context: emit `additionalContext` from the hook (gets appended after the cached prefix, no invalidation)
   - For one-time setup: do the write in a `Setup` hook (runs once at install) instead of every session
2. If the hook MUST modify CLAUDE.md (e.g. recording session metadata), make the write a no-op-when-unchanged:
   - Read the file, compute the new content, and write ONLY if it differs (avoids touching mtime when nothing changed)
   - Better yet: write to `.claude/session-state.json` (NOT cached) and reference it from a hook that emits additionalContext

## CA-03 — Hook flips MCP server enabled/disabled or permission allow/deny

| Field | Value |
|---|---|
| **Severity** | WARNING |
| **Validator** | `validate_cache.py` |
| **Triggered by** | A hook script (any event) that mutates `enabledMcpServers`, `disabledMcpServers`, `permissions.allow`, or `permissions.deny` in any `settings.json` |
| **Why it matters** | Tool-list and permission state are part of the cached system prompt. Flipping them mid-session forks the cache and loses tool-search optimization |

### Fix

1. Set MCP server enable/disable state declaratively in `.claude/settings.json` (project) or `~/.claude/settings.json` (user) — once, not via runtime hook
2. Use `permissionMode` on the agent/skill instead of mutating `permissions.allow` at runtime
3. If conditional enabling is genuinely needed, use the `if:` field on the hook entry to gate the hook itself rather than flipping state

## CA-04 — `model:` frontmatter forces an in-line model switch (any component)

| Field | Value |
|---|---|
| **Severity** | WARNING |
| **Validator** | `validate_cache.py` |
| **Triggered by** | A `model:` field in the frontmatter of ANY component — agent (`agents/*.md`), command (`commands/*.md`), or skill (`skills/*/SKILL.md`). `model: inherit` is exempt (it uses the session model, so it triggers no switch). |
| **Why it matters** | Each model keeps a SEPARATE prompt cache. Pinning a component to a model forces an in-line model switch when it runs, so the pinned component pays a cold-cache miss on every dispatch instead of reusing the session's warm prefix. A miss costs ~10x token-rate vs a hit — but it never makes a plugin invalid, so this is a WARNING. |

### Fix

1. Remove the `model:` frontmatter line so the component inherits the session model and keeps the cache warm. This is the right fix in almost every case.
2. If you must name the model explicitly, use `model: inherit` — it documents intent without forcing a switch (and is exempt from CA-04).
3. Only keep a concrete `model:` pin when the component genuinely cannot work on the session model (e.g. a 1M-context analysis that needs a specific model). Accept the cache cost knowingly; it is a WARNING, not a blocker.

> Pre-v2.102.0 guidance said "refactor a model-pinned skill into an agent." That no longer applies: CA-04 now flags `model:` on agents AND commands too, because the cache cost is identical wherever the pin lives. The fix is to drop the pin (or use `inherit`), not to move it.

```yaml
# WRONG — forces a cold-cache miss on every dispatch (agent, command OR skill)
---
name: heavy-analysis-skill
model: opus
---

# RIGHT — inherit the session model, keep the cache warm
---
name: heavy-analysis-skill
description: Deep semantic analysis.
---

# ALSO FINE — explicit inherit (documents intent, no switch, CA-04-exempt)
---
name: heavy-analysis-skill
model: inherit
---
```

## CA-05 — Hook script runs unbounded output commands

| Field | Value |
|---|---|
| **Severity** | WARNING |
| **Validator** | `validate_cache.py` |
| **Triggered by** | A hook script that runs `git status`, `find`, `ls -laR`, `cat <large-file>` without bounding output (no `head`, no `--max-depth`, no `--name-only`) |
| **Why it matters** | An unbounded git/find output dumped into hook output can be megabytes. That payload is appended to the prompt every fire, blowing the context window and triggering early auto-compact |

### Fix

Bound the output of every shell command in hook scripts:

| Original | Bounded fix |
|---|---|
| `git status` | `git status --short \| head -200` |
| `find . -type f` | `find . -type f -maxdepth 3 \| head -500` |
| `ls -laR` | `ls -laR \| head -300` (or use a more targeted `find`) |
| `cat <log-file>` | `tail -100 <log-file>` |
| `git log` | `git log --oneline -20` |

If the unbounded output is genuinely needed (rare), redirect it to a file and have the hook emit only a SUMMARY of the file's contents.

## CA-06 — Compaction/SubagentStart hook does not preserve cached prefix

| Field | Value |
|---|---|
| **Severity** | WARNING |
| **Validator** | `validate_cache.py` |
| **Triggered by** | A `PreCompact`, `PostCompact`, or `SubagentStart` hook that emits text not framed as additionalContext, such that the new turn's prompt rendering re-orders or re-renders cached blocks |
| **Why it matters** | These three events specifically need to PRESERVE the cached prefix so the resumed/forked subagent can re-use cache hits. If the hook emits arbitrary additional system-prompt content, the prefix re-renders and all cache benefit is lost |

### Fix

1. For PreCompact/PostCompact hooks: emit ONLY a JSON blob with `{ "additionalContext": "<text>" }` — never re-emit the original system prompt or cached agent definitions
2. For SubagentStart hooks: same rule — supply only NEW context for the subagent, never re-render cached parents
3. If the hook needs to inspect cached state, use `Read` against `.claude/cache-snapshot.json` (or similar) instead of re-emitting it

## CA-07 — `context: fork`/`branch` re-primes the cache from cold

| Field | Value |
|---|---|
| **Severity** | WARNING |
| **Validator** | `validate_cache.py` |
| **Triggered by** | A `context: fork` (or its synonym `context: branch`) in a skill or command frontmatter |
| **Why it matters** | Forking spins up a fresh subagent whose system-prompt + tool-schema prefix must be re-primed from cold — up to ~1M tokens re-tokenised per fork when the harness carries many skills / MCP servers / tools. Only CLAUDE.md and the rules files survive a fork unchanged. |

### Fix

1. Drop the `context:` field so the component inherits the parent context and keeps the cache warm. This is the right fix when the component does deterministic, bounded work (rendering a table, formatting text, running a script).
2. KEEP the fork only when it is genuinely justified, and accept the cost knowingly:
   - **Freshness** — an independent audit / error-check that must NOT be biased by the parent's conversation (a fresh agent gives a more objective judgement).
   - **Room** — reading many files, where a fresh context leaves more space before exhaustion.
3. If a forked render is just for presentation (e.g. a menu), prefer a mechanism that runs AFTER the turn without forking: a `Stop`-hook menu emitter (see the externalised `claude-menu-system` plugin) renders the menu post-turn with zero cache cost, instead of forking a subagent mid-turn.

> WARNING-tier: CA-07 never blocks — it is a reminder to confirm the fork earns its ~1M-token cost.

## Verification

After applying any cache-audit fix (always via the launcher — direct script call refused by environment-isolation guard):

```bash
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  cache <plugin-root>
```

All CA-NN findings should clear. If you also touched a hook script, also run the hook validator: `uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" hook <plugin-root>/hooks/hooks.json --strict`.

For empirical confirmation that cache hits are happening at runtime, look for `cache_creation_input_tokens` and `cache_read_input_tokens` in the API response telemetry — a cache hit shows a high `cache_read` and low `cache_creation`.
