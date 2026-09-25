---
trdd-id: AOPLOGYD
title: The plugin gate drops every RC-39 finding because the rule id is in no execution-class set
column: todo
status: tasked
created: 2026-09-25T14:12:50+0200
updated: 2026-09-25T15:08:34+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-25T14:12:50+0200
---

# The plugin gate drops every RC-39 finding because the rule id is in no execution-class set

Same defect shape as TRDD-3T170X2M (RC-164, closed 2026-09-25 in commit 60f43ca4): the plugin-mode execution-class merge in validate_plugin keeps a row only when its rule id is in Bucket A or _EXECCLASS_RCE_RULE_IDS. RC-39 (persistence / daemon-install) is in neither set, so every RC-39 finding is silently dropped from the --strict publish gate; only the security subcommand shows it. RC-39 rows are today generated-and-dropped, exactly as RC-164 was since v2.146.0.

Fix (after measuring): add RC-39 to the execution-class set the gate admits, with a WHY comment, plus a positive-control test through the REAL plugin gate (planted persistence install reaches the --strict verdict; the same tree without it stays clean) — the v5.17.0 lesson: through the GATE, not the emitter.

MEASURE FIRST, never assume: census the RC-39 emission population over the plugin cache before flipping (the RC-164 flip was measured first: 1375 writes). If the population is prose-token FPs of the TRDD-RU0POO65 class, fix those first — this card must not land the same prose-wave RU0POO65 just closed. Check whether the cpv_persistence_target.py four-ALLOW discriminator already handles the FP surface (C1-C4 fail-safe) — if yes the flip is likely clean; if no, that work is a prerequisite card.

## Approval log

- 2026-09-25T14:12:50+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
2026-09-25: Sibling predicate VERIFIED in-process (import validate_plugin, cold): RC-39 in _EXECCLASS_RCE_RULE_IDS=False (control RC-141=True, landed RC-164=True). The gate-drop is fact, not inference. Emission census still owed before flipping — see body MEASURE FIRST.
