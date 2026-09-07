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

^GHT8KH01 [desc:"CPV has no body-length cap on agents at any severity; only skills carry a 5000-token body cap, and validate_agent.py checks agent description length (300 tokens) and a MIN_BODY_CHARS=100 floor but no maximum", keywords: agents_have_no_body_limit do_agents_have_a_body_size_limit skill_body_token_limit_5000 validate_agent_min_body_chars_floor no_maximum_on_agent_body agent_description_token_limit_300 does_cpv_cap_agent_word_count, type: reference, ocd: 2026-07-22, lmd: 2026-09-06]
CPV imposes **NO body-length limit on AGENTS** — no word cap, no token cap, at any
severity. Only **SKILLS** carry a body-size limit: `SKILL_BODY_TOKEN_LIMIT = 5000`
tokens (`scripts/cpv_validation_common.py`), enforced in `validate_skill_comprehensive.py`,
because a skill body beyond ~5000 tokens loses its tail to Claude Code's skill
auto-compaction. `validate_agent.py` validates an agent's `description`
(`AGENT_DESCRIPTION_TOKEN_LIMIT = 300` tokens) and a `MIN_BODY_CHARS = 100` floor,
but has **no maximum** on the agent body.

^URE3JS30 [desc:"the no-agent-body-limit rule is a DELIBERATE divergence from Anthropic's keep-agents-lean guidance (user directive 2026-07-22) because an agent's whole body loads every run with no compaction, so trimming it removes capability for zero benefit", keywords: deliberate_divergence_from_anthropic_guidance why_no_agent_body_limit agent_body_loaded_every_run_no_compaction trimming_agent_body_no_runtime_benefit should_agents_be_kept_lean_like_skills user_directive_2026_07_22, type: reference, ocd: 2026-07-22, lmd: 2026-09-06]
This is a **DELIBERATE divergence from Anthropic's "keep agents lean / concise"
guidance** (user directive 2026-07-22). It is intentional and must not be "corrected"
back toward the guideline: an agent's ENTIRE body is loaded into context every time
the agent runs (unlike a skill, whose tail is dropped under compaction), so trimming
an agent body removes capability for **zero** runtime benefit. A verbose agent that
carries all its instructions is working as designed.

^03LWOFKX [desc:"the old validator emitted a MAX_BODY_WORDS=2000 Agent body is very long WARNING; it was noise on thorough agents and was removed in v3.1.x along with its constant, import, and the word-count check in validate_body_content(), while skills kept their 5000-token cap", keywords: why_was_the_agent_2000_word_warning_removed max_body_words_constant_removed agent_body_is_very_long_warning removed_in_v3_1_x validate_body_content_word_count_check_removed skills_keep_5000_token_cap, type: reference, ocd: 2026-07-22, lmd: 2026-09-06]
**Why:** the old validator emitted a `MAX_BODY_WORDS = 2000` "Agent body is very long"
WARNING on any agent over 2000 words. It was noise on legitimately-thorough agents and
pushed authors to delete real instructions to silence it — the opposite of what helps.
Removed in v3.1.x: the constant `MAX_BODY_WORDS`, its import in `validate_agent.py`, and
the word-count check in `validate_body_content()`. Skills keep their 5000-token cap
because the skill runtime genuinely truncates.

^CLMCM1GB [desc:"never add a word/token/char maximum to validate_agent.py::validate_body_content or any new agent validator; decline any future proposal citing Anthropic guidance and point here; the regression lock is tests/test_validate_agent.py::TestAgentBodyHasNoLengthLimit", keywords: how_to_apply_no_agent_body_cap decline_future_agent_body_cap_proposal regression_lock_test_agent_body_no_length_limit never_add_max_to_validate_body_content body_cap_belongs_only_on_skills supersedes_lean_agents_under_2000_words_note, type: reference, ocd: 2026-07-22, lmd: 2026-09-06]
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
