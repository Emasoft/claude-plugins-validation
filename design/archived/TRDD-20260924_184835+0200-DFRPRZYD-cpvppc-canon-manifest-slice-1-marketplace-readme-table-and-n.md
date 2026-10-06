---
trdd-id: DFRPRZYD
title: CPVPPC P1 - framework and marketplace README-table assertions
column: complete
status: archived
created: 2026-09-24T18:48:35+0200
updated: 2026-09-28T13:41:04+0200
current-owner: emanuelesabetta
created-by: emanuelesabetta
task-type: feature
min-approval-requirement: none
assignee: emanuelesabetta
mandate: true
mandated-by: none
approved: true
approval-judge: emanuelesabetta
approval-datetime: 2026-09-24T18:48:35+0200
implementation-commits: [1e79599d, 804b9451, b4341ba9, aecbd765, 44c6a474, 7b28b8bf, 01ffdbd0]
---

# CPVPPC canon manifest slice 1 - marketplace README table and notify payload

## User directive (verbatim, 2026-09-24)

> for the remaining things, i explained to you that everything is based on the functionality of the cpv plugin to publish plugins and marketplaces. their structure and workflow is defined as cpv publishing pipeline canon (cpvppc) and includes many automations like automatic update of the marketplace readme with the list/table of plugins with each respective version and link. but there are much more functions. if you  need to formalize the CPVPPC canon i suggest to create a specs file or a manifest schema of some sort, to version and maintain with the list of features detailed.
>
> yes go on

(The second message approved continuing with the proposal as revised after its adversarial review.)

## Decisions (from the reviewed proposal)

- ONE source of truth: `scripts/cpvppc_manifest.json` (ships in the wheel; hatchling ships only `scripts/`). The human spec `design/specs/cpvppc.md` is GENERATED from it; a test asserts the committed md equals the rendered one.
- No separate canon semver: each feature carries the CPV version that introduced it (`since`).
- Canon-version declaration is ADDITIVE: keep reading `CANON_VERSION` in a generated `publish.py` for detection; new declaration is added alongside, never replacing it.
- Every canon check is WARNING (never blocking) by default — no retro-break of green repos (#170). Fixer/upgrade agents treat these WARNINGs as work to do (agent-gate policy, not validator severity).
- Every feature has a fixture test: a canon-complete fixture passes the feature's check, and a copy with only that feature removed fails it (no vacuous name-exists pins).
- Detection must work on uninstalled source trees and on marketplaces, including Layout A (url/github sources, no .gitmodules).
- Spec from INTENT, then fix emitters/validators until the tests pass — do not record today's defects (D2-D9) as canon.
Approval (2026-09-24): the user approved the full plan design/specs/cpvppc-plan.md; this card is its phase P1. The earlier note that 'yes go on' approved the slice is superseded: it approved the direction only.

## Slice 1 scope

Features: the marketplace README plugin-versions table (renderer byte-identical to the template, markers, update workflow re-renders and stages README, `--check` gate, top-level manifest `version`) and the notify payload keyed on the plugin.json `name` (defect D9). Deliverables: manifest + generated spec + detector + fixture tests + the D9 emitter fix. Later slices: remaining features, the agent route ("upgrade an existing marketplace and its plugins"), multi-marketplace notification. Evidence base: TRDD-8W80DZHI.

## Out of scope

Any push or PR to another repo. The one-line data fixes (web-scenario-tester 0.1.3→0.1.7, the retired claude-plugins-management entry) stay the user's choice.

## Approval log

- 2026-09-24T18:48:35+0200 — MANDATE issued by emanuelesabetta (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- 2026-09-28T12:30:00+0200 — P1 slice 1 complete (commit 1e79599d): framework+canon.json+spec+remote_validation cpvppc mode, D9 notify fix (generator+template+guide), marketplace README fixture tests; 93 new tests, 175-test slice green, strict self-validate 0/0/0/0. Central-verified first-hand.
- 2026-09-28T13:05:00+0200 — P1 adversarial review (fork) returned no blocker; 3 accepted findings fixed and committed in the same session: (b) D9 non-string 'name' hole closed (type check in the emitted step + test — 'name': 123 previously passed -z and notified as "123"); (a) exec-line filter hardened (compound echo-&&-invocation now credited, heredoc bodies excluded) + 2 pinning tests; (c) cpvppc's ignored -o documented in launcher help + branch comment; (d) entry-level workflows shape coverage VERIFIED already-present (validate_plugin path-field block includes workflows since v5.21.0) — no change. Also: remote_validation alias test extended with an in-process exemption + guard test.
- 2026-09-28T14:10:00+0200 — Reviews rounds 3-4 (commits 44c6a474, 7b28b8bf): F1 newline rejection landed with test; template propagated. CORRECTION recorded per review round 4: the round-4 commit message overclaimed — its negative tests (non-string/ws-only/newline) bind the GENERATOR fixture only and never executed against the template; the P1-era template passed every test on the books. Closed by: byte-identity pin test (template step == emitted step), guide's stale P1-era block updated (third surface), template verified reference-only (not in REQUIRED_TEMPLATES; standardize routes through gen_notify_marketplace_yml) — no second live emission path. Recorded-not-taken: middle-\r/\t names pass every check (isprintable hardening available); F2 closing-tag whitespace comparison optional.
- 2026-09-28T14:45:00+0200 — Review round 5 of commit 01ffdbd0: no blocker. Accepted and closed: (1) guide-side alignment pin added (needle-based — the guide's comment block deliberately differs from the emitted one, so full identity is wrong for prose; test_doc_step_carries_the_hardened_assert fails if the guide reverts to the P1-era shape); (2) implementation-commits backfilled with all seven commits. Process: card leaves dev (work complete, five review rounds survived). Recorded-not-taken: the byte-identity pin is deliberately over-tight (comment tweaks in the generator force a template re-copy — the intended one-SSOT discipline, cost stated).
- 2026-09-28T13:41:04+0200 — COMPLETE by main-agent@claude-plugins-validation. P1 complete + 5 adversarial review rounds, all findings dispositioned; commits recorded.

## Acceptance checklist

- [x] scripts/cpvppc/ framework: canon.json 0.1.0 with MKT-001..007, verify.py verdict API (0/1/5/6, UNKNOWN-never-0, never-execute-repo-code), deterministic render_spec.py
- [x] cpvppc mode in remote_validation.py with verdict passthrough
- [x] D9 notify fix on all three surfaces (generator, template, guide), aligned by byte-identity + needle pins
- [x] Marketplace README fixture tests: canon-complete scaffold passes 001-006, only 007 fails (positive control)
- [x] Exec-line filter: compound invocations credited, heredoc bodies excluded, both pinned
- [x] Every canon check WARNING (never blocking) per card decision
- [x] 93+ tests green, ruff+mypy clean, hashes regen last, cache-cold strict self-validate 0/0/0/0
- [x] 5 adversarial review rounds, every finding dispositioned with a stated reason
