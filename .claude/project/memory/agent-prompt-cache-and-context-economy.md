---
name: agent-prompt-cache-and-context-economy
description: "does loading a skill at runtime break the prompt cache / do a skill's progressive-disclosure reference files bust the cache / should skills go in the agent's skills: frontmatter or be loaded on demand / is inline !cmd in an agent body a cache hazard / why is my agent expensive per invocation / what does skills: frontmatter actually inject / are MCP tool wildcards in tools: costly / how do I make an agent cache-friendly"
ocd: 2026-07-25
lmd: 2026-07-25
metadata:
  node_type: memory
  type: project
  tier: aspect
---

Verified against the official documentation (Anthropic prompt-caching, Agent Skills
overview, Claude Code sub-agents) on 2026-07-25. Several widely-repeated beliefs about
agent "cache optimization" are **false**, and acting on them makes agents *more*
expensive. These are the mechanics.

## What the cache actually is

The prompt cache is a **PREFIX cache** over `tools → system → messages`, in that order.
Appending content after the cached prefix does **not** invalidate it — *"the cached prefix
remains valid and is read from cache while new content after the breakpoint is processed
fresh… the cache point moves forward automatically as conversations grow"* (with a
20-block lookback). Only a change **at or before** the breakpoint busts it, because the
hash is cumulative. Changing **tool definitions invalidates the entire cache**.

## Where each thing lands

| Content | Lands in | Breaks the cached prefix? |
|---|---|---|
| An agent's `.md` body (it IS the agent's system prompt) | `system` | Only if the body itself changes |
| `tools:` grants | `tools` (FIRST in the prefix) | Yes — tool-definition changes invalidate everything |
| Skill L1 metadata (name + description, ~100 tokens/skill) | `system` | No (static) |
| A skill preloaded via `skills:` frontmatter — **its FULL content** | `system`, **every invocation** | No, but it is a permanent per-run tax |
| A skill loaded at runtime via `Skill()` | messages (tail) | **No** |
| A skill's L3 reference files, read on demand | messages (tail) | **No** |

**So progressive disclosure does NOT break the cache.** Reference files are read via bash
and *"load into context when read"* — they append to the volatile tail while the prefix
keeps hitting cache. Different reference files on different turns cost only their own
tokens; they never retroactively invalidate anything. Progressive disclosure exists
precisely to keep the always-loaded part small.

**Inline `!cmd` does not execute in an agent body.** Dynamic `!` execution is a
*command-file* feature; an agent body is a static system prompt, so a `!cmd` there is
inert literal text — not a cache hazard. (An author who expected it to run has a latent
bug, which is a different problem.)

## The rules that follow

1. **Preload a skill in `skills:` only if the agent needs it on ~every run.** Frontmatter
   preloading injects the skill's *entire* content into every invocation. A ~4,600-token
   skill preloaded into an agent that rarely uses it costs that much on every call,
   forever. For a **dynamic router** agent that picks 1 of N skills, runtime `Skill()` is
   both cheaper and correct — preloading all N is the pathological case.
2. **Keep `tools:` minimal and explicit.** Tools are first in the cached prefix. An MCP
   wildcard grant (`mcp__server__*` or a bare server name) injects that server's *whole*
   tool schema set into every turn; list only the `mcp__server__<tool>` ids the body uses.
   A malformed single-hyphen form (`mcp__server-*`) matches nothing at all — the grant is
   silently ineffective while the body still calls the tools.
3. **`tools:` must equal exactly what the body uses** — nothing missing (a used-but-ungranted
   tool is a runtime failure: a `bash` fence with no `Bash` grant, a `Skill()` call with no
   `Skill` grant), and nothing spare.
4. **The real levers are prefix-shaped, not skill-shaped:** minimal tool surface; read fixed
   inputs once (never re-read the same file in a later turn — the second copy rides forward
   forever); scoped/ranged reads instead of whole-file dumps; extract the fact from a large
   tool result, screenshot, or snapshot and then drop the blob; batch deterministic steps
   into one turn.

## Governed by

- [[claude-plugins-validation-overview]] — the project hub.

## Notes and lessons learned

[^1]: [id:ATOM-RUNTIME-SKILL-NOT-CACHE-BREAK, status:valid, keywords:"runtime skill load breaks cache progressive_disclosure reference_files skills_frontmatter_preload cache_miss_per_turn router_agent", ocd:2026-07-25, lmd:2026-07-25] DO NOT "optimize" an agent by moving its runtime `Skill()` calls into `skills:` frontmatter, BECAUSE a runtime load lands in the MESSAGE TAIL and cannot invalidate the cached prefix, while `skills:` injects the skill's FULL content into EVERY invocation — so the "optimization" adds a permanent per-run tax and is strictly worse for a sometimes-needed skill (and worst of all for a router that needs 1 of N). DO preload only always-needed skills, and measure the skill's real token size before deciding.

[^2]: [id:ATOM-VERIFY-PLATFORM-FACT-BEFORE-ENCODING, status:valid, keywords:"plausible mechanism wrong verify_against_docs before_writing_a_detector cache_folklore encoded_a_false_premise", ocd:2026-07-25, lmd:2026-07-25] DO NOT encode a platform behaviour into a rule, a detector, or agent guidance from a plausible-sounding mechanism, BECAUSE two such premises here — "a runtime `Skill()` busts the prefix" and "inline `!cmd` in an agent body injects changing output" — were BOTH false, and shipping them would have produced a validator that flags the CORRECT pattern as a defect. DO read the primary documentation first and state which region (`tools`/`system`/`messages`) or which loader you are relying on; a rule whose mechanism you cannot name is a guess.

[^3]: [id:ATOM-PURGE-A-PREMISE-CORPUS-WIDE, status:valid, keywords:"false premise in many docs purge_scope grep_missed_a_wording superseded_label doc_correction_sweep stale_doc_teaches_falsehood", ocd:2026-07-25, lmd:2026-07-25] DO NOT scope the correction of a disproven premise to the one document you found it in, or to the exact phrases in your search, BECAUSE authors restate a premise in their own words — one sweep here found the same false claim in FIVE documents, and a fourth wording escaped a three-pattern grep; every surviving copy keeps teaching the falsehood to the next agent. DO sweep with several phrasings plus the inverse, and LABEL the old claim superseded in place rather than deleting it, so a reader who remembers the old rule learns that it was wrong.

[^4]: [id:ATOM-AGENT-DONE-IS-NOT-PROOF, status:valid, keywords:"subagent reported DONE not proof verify_independently background_task_exit_code wrapper_echo over_minimization broke_the_agent", ocd:2026-07-25, lmd:2026-07-25] DO NOT accept a worker's `[DONE]` (or a background task's "exit code 0") as evidence the work is correct, BECAUSE the exit code often belongs to the wrapper rather than the real command, and a confidently-reported change can still be wrong in a way only the artifact shows — a "minimize the tool list" pass can strip a capability the agent genuinely needs. DO re-run the gate yourself, read the real exit status out of the log file, and spot-check the highest-risk edit against the artifact before reporting completion upward.
