---
name: agent-skill-closure-and-architectures
description: "my agent's skills: list names a skill that does not exist and nothing complains / an agent preloads a skill but it silently does nothing / does skills: frontmatter control WHICH skills an agent may us"
ocd: 2026-07-30
lmd: 2026-07-30
metadata:
  node_type: memory
  type: project
  tier: aspect
---

# agent-skill-closure-and-architectures


^ATOM-GBIZ-GHUE [desc:"An agent's reachable skill set is TOOL-GATED on the Skill tool; skills: frontmatter only decides what is PRELOADED.", keywords: skills_frontmatter_is_a_preload_hint_not_an_ACL which_skills_can_an_agent_access Skill_tool_gate disallowedTools_shuts_the_gate runtime_Skill_call_is_dead, type: reference, ocd: 2026-07-30, lmd: 2026-07-30]

Per the official `sub-agents.md` ("Preload skills into subagents"): *"This field
controls which skills are preloaded, NOT which skills the subagent can access:
without it, the subagent can still discover and invoke project, user, and plugin
skills through the Skill tool during execution. To prevent a subagent from invoking
skills entirely, omit `Skill` from the `tools` list or add it to `disallowedTools`."*

So reachability is:

- `Skill` in `disallowedTools` → gate SHUT (deny is applied first, so it wins even
  with no `tools:` field at all);
- else no `tools:` field → OPEN (inherits every session tool);
- else `Skill` present in `tools` → OPEN;
- else → SHUT, and every runtime `Skill()` call in that body is DEAD.

CPV implements this in `scripts/cpv_agent_closure.py` as the SSOT. [^1]


^ATOM-UQNL-208L [desc:"context: fork is what forks a subagent; agent: only picks the type, and background defaults to true so a node returns nothing inline.", keywords: context_fork_runs_a_skill_in_a_subagent agent_field_alone_does_nothing background_defaults_to_true forked_skill_returns_nothing_inline one-skill-agent, type: reference, ocd: 2026-07-30, lmd: 2026-07-30]

From `skills.md`'s frontmatter table: `context` — *"Set to `fork` to run in a forked
subagent context"*; `agent` — *"Which subagent type to use **when `context: fork` is
set**"* (default `general-purpose`; `Explore`/`Plan` also skip CLAUDE.md, which is
how "minimal context" is actually achieved); `background` — only applies with
`context: fork`, *"Set to `false` to wait for the forked subagent's result in the
turn that invoked the skill"*, default **`true`**, needs CC **v2.1.218+**.

Two consequences:

1. **A skill carrying `agent:` alone does nothing.** `context: fork` is the mechanism.
2. **A forked skill returns NOTHING inline by default** — the result arrives as a
   notification. A pipeline threading one node's output into the next step needs
   `background: false`, or the steps appear to run and silently deliver nothing. [^2]


^ATOM-NK5K-ZXKB [desc:"Two preload entries that silently do nothing: a missing skill, and one setting disable-model-invocation: true.", keywords: disable-model-invocation_cannot_be_preloaded verify_and_code-review_cannot_be_preloaded missing_preload_skipped_with_only_a_debug_log_warning slash_skills_shows_a_user-only_badge, type: reference, ocd: 2026-07-30, lmd: 2026-07-30]

`sub-agents.md`: *"You can't preload skills that set `disable-model-invocation:
true`, since preloading draws from the same set of skills Claude can invoke. This
includes the bundled `/verify` and `/code-review` skills."* And: *"If a listed skill
is missing or disabled, Claude Code skips it and logs a warning to the debug log."*

So BOTH failure modes are silent — visible only in the debug log, which nobody
reads. Confirm in a live session with **`/skills`** (a "user-only" badge marks the
`disable-model-invocation` case) or **`/context`** (shows what actually loaded).

CPV catches these as **AC5** (unpreloadable) and **AC1** (unresolvable). AC5 needs
no non-vacuity guard because resolution IS the proof — we read that skill's own
frontmatter and saw the field. [^3]


^ATOM-H822-T1NF [desc:"The project's three canonical agent architectures; they differ only in the skills list and WHERE a skill executes.", keywords: ALL-IN-ONE_agent ONE-FOR-ALL_agent PLUGIN-OMNI_agent never_inline_skill_bodies_into_an_agent convert_an_agent_to_a_variant three_agent_architectures, type: project, ocd: 2026-07-30, lmd: 2026-07-30]

USER-defined vocabulary (2026-07-30). All three pair their `skills:` list with the
**`Skill` tool**, so all three need the gate OPEN.

| architecture | `skills:` lists | body carries | skills execute in |
|---|---|---|---|
| **ALL-IN-ONE** | every skill it needs | how to use each skill, at the right time and in the right choice branch | the same agent |
| **ONE-FOR-ALL** | every skill it needs | the SAME routing / choice tree | a separate subagent per skill (a one-skill-agent, minimal context) |
| **PLUGIN-OMNI** | exactly ONE — the plugin's `the-skills-menu` | routing through that menu | resolved at runtime from the menu |

**ALL-IN-ONE and ONE-FOR-ALL are otherwise the same construction** — the only
difference is WHERE a skill runs, achieved by adding `context: fork` in place to the
shared skill. Generated by `scripts/convert_agent.py --to <mode>`; every variant also
carries `verification-before-completion`. Compared by
`scripts/cpv_agent_eval.py` — a STATIC cost model that ranks nothing, and whose
numbers are only a comparison when every preload actually priced. [^4] Scanned
per-agent by `scripts/cpv_agent_security.py`, which must ARM the same
suppression state the plugin gate arms. [^5]

## Governed by

- [[claude-plugins-validation-overview]] — the project hub.

## See also

- [[agent-prompt-cache-and-context-economy]] — what `skills:` frontmatter costs
  (it injects each named skill's FULL content into every invocation) and why a
  sometimes-needed skill should load at runtime instead. That page answers "what
  does it cost"; this one answers "what can the agent actually reach, and does it
  exist".

## Notes and lessons learned

[^1]: [id:ATOM-EYP1-XTMR, status:valid, desc:"The DRY reason skill content is never copied into an agent.", keywords:"inlined_skill_copy_rots N_stale_copies_with_no_drift_signal keep_skills_independent_and_shared mono_agent_concatenated_bodies", ocd:2026-07-30, lmd:2026-07-30] DO NOT copy, concatenate, or inline a skill's content into an agent, BECAUSE a skill must stay INDEPENDENT so it can be shared by many agents and edited/fixed/updated ONCE — an inlined copy is a second source of truth that rots the moment the original changes, and with N agents inlining it there are N stale copies and no signal that any drifted. DO reference skills BY NAME in `skills:` frontmatter and nowhere else; the frontmatter list IS the preload, so the copy buys nothing and costs maintenance.
[^2]: [id:ATOM-H8NN-4Y7U, status:valid, desc:"Confirming a frontmatter field is VALID does not tell you what it DOES.", keywords:"agent_field_valid_but_does_nothing field_validity_is_not_field_semantics checked_the_constant_not_the_doc", ocd:2026-07-30, lmd:2026-07-30] DO NOT conclude a frontmatter field's BEHAVIOUR from the fact that a validator's constant list accepts it, BECAUSE `agent:` is genuinely a valid skill field and still does nothing on its own — `context: fork` is what forks the subagent. Checking `SKILL_FRONTMATTER_FIELDS` proved validity and I read it as semantics, which put a wrong mechanism into a spec three workstreams were building against. DO read the field's own doc row before designing on it; validity and semantics are separate questions.
[^4]: [id:ATOM-4KQW-9M2D, status:valid, desc:"An input the tool could not price was published as a measurement, and the delta came out with the wrong SIGN.", keywords:"variant_read_as_cheaper_but_is_more_expensive delta_wrong_sign unresolvable_preload_priced_as_zero cost_comparison_not_like_for_like forgot_--skills-root", ocd:2026-07-30, lmd:2026-07-30] DO NOT publish a cost/perf delta computed over inputs the tool could not actually measure, BECAUSE an unpriced item silently counts as ZERO and understates whichever side carries it — measured: an ALL-IN-ONE variant read as 9,264 tokens CHEAPER than its original with its preloads unresolvable, and 13,545 tokens more EXPENSIVE once `--skills-root` resolved them, so the headline carried the WRONG SIGN while the per-item caveats sat in a notes table below it. DO mark the comparison NOT-like-for-like at the point the number is read, naming how many items on each side went unpriced. A concrete instance of [[lesson-cannot-check-is-not-clean]] — the same failure in cost clothing.
[^5]: [id:ATOM-7VJE-3PQL, status:valid, desc:"A suppression chain is a NO-OP unless something ARMS it, so a new entry point re-flags what the old one exempts.", keywords:"new_entry_point_reports_CRITICAL_where_plugin_gate_reports_zero self_scan_exemption_not_armed suppression_chain_is_a_noop scanning_its_own_source narrow_scan_harsher_than_broad", ocd:2026-07-30, lmd:2026-07-30] DO NOT assume importing the shared suppression predicate makes a new scan entry point agree with the existing one, BECAUSE that chain can be gated on module-global state some CALLER must arm — CPV's SHA-verified self-scan exemption is armed by `_set_cpv_self_scan`, and an entry point that never called it had `cpv_self_scan_skip` return False unconditionally, so scanning CPV's OWN agent reported a CRITICAL on its own plugin-management prose ("Enable / Disable … Security Audit") that `plugin --strict` reported ZERO of. DO diff the new path against the old on IDENTICAL content in BOTH directions (harsher AND softer — [^3] caught softer, this caught harsher), arm the state the way the reference caller does, and always DISARM in a `finally`: the flag is global, so one left armed lets the NEXT target read stale state and wrongly suppress its findings.
[^3]: [id:ATOM-TNDD-1J4R, status:valid, desc:"How a new narrow-scope scan entry point silently became weaker than the existing one.", keywords:"narrow_scan_reports_VALID_where_the_broad_scan_reports_INVALID external_scanners_silently_omitted verdict_says_VALID_next_to_a_nonzero_exit_code", ocd:2026-07-30, lmd:2026-07-30] DO NOT accept a new narrow-scope scan (one agent, one file, one component) as clean without diffing it against the EXISTING broad scan on identical content, BECAUSE a narrow entry point that reuses only the in-process engine silently omits every EXTERNAL scanner and then prints a verdict as if the scan were complete — measured: the plugin path returned INVALID with 11 MAJOR (cisco + snyk) where the agent-scoped path returned NIT:2 and "Verdict: VALID" on the same payload. DO run both paths on one fixture and compare severity tiers, and treat "Verdict: VALID" printed beside a non-zero exit code as a contradiction to explain, never a pass.
