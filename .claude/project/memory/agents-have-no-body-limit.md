---
name: agents-have-no-body-limit
description: "a CPV validator, spec-sync pass, or fixer wants to add or restore a body-length (word/token) cap on AGENTS — often citing Anthropic 'keep agents lean' guidance — is that allowed? / why was the agent 2000-word 'body is very long' warning removed? / do agents have a body-size limit the way skills do (5000 tokens)?"
ocd: 2026-07-22
lmd: 2026-09-03
metadata:
  node_type: memory
  type: project
  tier: aspect
publish-globally: false
---

CPV imposes **NO body-length limit on AGENTS** — no word cap, no token cap, at any
severity. Only **SKILLS** carry a body-size limit: `SKILL_BODY_TOKEN_LIMIT = 5000`
tokens (`scripts/cpv_validation_common.py`), enforced in `validate_skill_comprehensive.py`,
because a skill body beyond ~5000 tokens loses its tail to Claude Code's skill
auto-compaction. `validate_agent.py` validates an agent's `description`
(`AGENT_DESCRIPTION_TOKEN_LIMIT = 300` tokens) and a `MIN_BODY_CHARS = 100` floor,
but has **no maximum** on the agent body.

This is a **DELIBERATE divergence from Anthropic's "keep agents lean / concise"
guidance** (user directive 2026-07-22). It is intentional and must not be "corrected"
back toward the guideline: an agent's ENTIRE body is loaded into context every time
the agent runs (unlike a skill, whose tail is dropped under compaction), so trimming
an agent body removes capability for **zero** runtime benefit. A verbose agent that
carries all its instructions is working as designed.

**Why:** the old validator emitted a `MAX_BODY_WORDS = 2000` "Agent body is very long"
WARNING on any agent over 2000 words. It was noise on legitimately-thorough agents and
pushed authors to delete real instructions to silence it — the opposite of what helps.
Removed in v3.1.x: the constant `MAX_BODY_WORDS`, its import in `validate_agent.py`, and
the word-count check in `validate_body_content()`. Skills keep their 5000-token cap
because the skill runtime genuinely truncates.

**How to apply:** never add a word/token/char MAXIMUM to the agent path
(`validate_agent.py::validate_body_content`, or any new agent validator). If a future
CC-spec-sync or a well-meaning agent proposes an agent body cap "because Anthropic
recommends concise agents", DECLINE it and point here — the divergence is a project
decision, not an oversight. The regression lock is
`tests/test_validate_agent.py::TestAgentBodyHasNoLengthLimit` (a 6000-word body emits
no length finding; the `MIN_BODY_CHARS` floor still fires). A body cap belongs ONLY on
skills. Supersedes the "lean agents under 2000 words" note in
agent-trim-and-release-gotchas.

## Applies to

- (radiates to any future agent/body-length validator this project writes — no
  such component page exists yet in this scope; wire the reciprocal
  `## Governed by` on it as it is added)

## Governed by

- [[claude-plugins-validation-overview]] — the project hub.

## Notes and lessons learned
