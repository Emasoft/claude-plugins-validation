---
trdd-id: QOZXF6A6
title: setup_branch_rules.py emits stale baseline payloads that regress repos to the pre-ruling shape
column: dev
created: 2026-08-18T20:29:38+0200
updated: 2026-08-18T20:35:00+0200
current-owner: cpv-session
task-type: bugfix
priority: 1
labels: [phase-2, hub-dispatch, branch-protection]
external-refs: [TRDD-BRRJK57P, TRDD-DD0M4QL7]
---

# setup_branch_rules.py stale baseline payloads (P1, fleet-blocking)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-08-18

Authored from the hub's Phase-2 dispatch (TRDD-BRRJK57P, USER delegation in its Approval
log). All findings re-verified first-hand in this tree on 2026-08-18.

**NEXT ACTION:** rewrite `scripts/setup_branch_rules.py` payload builders to the ratified
shape, semantics taken from the janitor's code SSOT
`branch_protection_lib.baseline_ruleset_payloads` (NEVER from prose — the machine-global
prose in `manager-approval-defaults.md` still states the pre-ruling shape and would
re-impose the lockout this card removes).

## Verified defects (CPV tree, 2026-08-18)

1. `build_baseline_history_protect_ruleset()` (scripts/setup_branch_rules.py:780) emits
   `"bypass_actors": []`. The USER's Tier-3 ruling of 2026-08-13 abolished that lockout:
   the ratified shape carries the admin bypass
   `{actor_id: 5, actor_type: RepositoryRole, bypass_mode: always}`.
2. `build_baseline_pr_and_checks_ruleset()` emits
   `required_approving_review_count: 1` — unsatisfiable on a solo-owner repo (GitHub
   forbids self-approval), the "eternally stuck branches" failure. Ratified: `0`, and the
   whole `pull_request` rule is CONDITIONAL (harness/other-owner repos only — see
   `require_pull_request_for` semantics in the SSOT).
3. The `required_status_checks` rule is always emitted, even with an empty context list —
   GitHub 422s an empty `required_status_checks` array and the 422 fails the WHOLE
   ruleset write. Ratified: OMIT the rule entirely when no CI contexts are detectable.
4. No `baseline-tag-protect` ruleset (target: tag, rules deletion+update, bypass `[]`).
5. The stale payload flows :807 → action=UPDATE (:948-956) → apply_ruleset POST-or-PUT
   (:964-978), so the script actively REGRESSES every already-correct repo it touches.

## Sequencing (why this lands FIRST)

The janitor's gate-fix card TRDD-DD0M4QL7 waits on this: with its gate fixed, a working
applier aimed at the wrong payload would rewrite the fleet wrong. This fix must land
before the janitor's.

## Acceptance criteria

- [ ] Payloads byte-equivalent in semantics to the janitor SSOT: history-protect WITH
      admin bypass; pr-and-checks approvals=0 with conditional pull_request rule;
      required_status_checks omitted when no contexts; tag-protect present with bypass [].
- [ ] A repo PRESENT-BY-NAME but STALE-BY-CONTENT gets PATCHed (updated) to the ratified
      shape.
- [ ] Post-apply, a NON-ADMIN actor is still refused deletion / non_fast_forward.
- [ ] Regression tests for all of the above; suite green.

## Trap

Do NOT build payloads from the baseline PROSE (manager-approval-defaults.md §F or any
rule file) — it is known stale and describes the pre-ruling shape. Code SSOT only.

## Approval log

- 2026-08-18T20:29:38+0200 — Authored at `todo` under the USER delegation recorded in
  TRDD-BRRJK57P (hub dispatch, Phase 2). Tier-0 derived work of an approved fleet task.
