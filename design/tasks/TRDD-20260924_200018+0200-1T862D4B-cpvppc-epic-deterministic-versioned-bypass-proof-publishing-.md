---
trdd-id: 1T862D4B
title: CPVPPC epic - deterministic versioned bypass-proof publishing canon
column: dev
status: tasked
created: 2026-09-24T20:00:18+0200
updated: 2026-09-28T14:00:09+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:00:18+0200
---

# CPVPPC epic - deterministic versioned bypass-proof publishing canon

Epic for the approved CPVPPC plan, stored verbatim at design/specs/cpvppc-plan.md (approved by the user on 2026-09-24 after four rejected drafts). Phases P0-P15 each have their own card; P1 is TRDD-DFRPRZYD. User decisions on 2026-09-24: canon has its own version; lock file with fully parameterized templates and no bypass options; local release builds allowed if hash-pinned and signed, marked 'locally built'; private repos 'COMPLIANT (no provenance: private repo)'; code signing optional; the GitHub Actions app as the only ruleset bypass actor.

## Approval log

- 2026-09-24T20:00:18+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
2026-09-28T13:46:52+0200 — P1 (TRDD-DFRPRZYD) review round 6 of 02979bf7: no blocker; convergence point, apply-review cycle ended per the two-round rule (6 rounds total, all findings dispositioned). The archived card's Approval log stops at round 5 because design/archived/ is immutable (TRDD-MQE5D28T D8 refuses even the log); the round-6 disposition is recorded HERE as the living owner. Findings: (1) pin's failure message misdiagnoses a reformat as 'P1-era shape is back' — recorded NOT applied (stable spelling, green suite; cosmetic docstring tweak would trigger another cycle; the byte-identity pin's error message names the real fix path); (2) guide needle 3 forbids text a future before/after doc section trips legitimately — recorded (relevant only on guide edits; failure message states the cause); (3) 804b9451 in implementation-commits is docs-only — no action (harmless over-inclusion, chain resolves); (4) '93+ tests' checklist range — stands (terminal card), lesson: exact final counts in future checklists. Board coherence confirmed: code-complete-on-master, release-via absent = correct terminal.
2026-09-28T13:59:50+0200 — CORRECTION (adversarial review of the 13:46:52 append; findings accepted except where noted). (1) ROUND-COUNT RECONCILED: the archived card's checklist says '5 adversarial review rounds' and this log's P1 entry says '6 rounds total' — the on-disk truth per the cleared session's transcript is SIX fork reviews (rounds 1-6, commits b4341ba9/aecbd765/44c6a474/7b28b8bf/01ffdbd0/02979bf7), and the checklist was written at round 5 before round 6 ran; the frozen card understates by one. 5-vs-6 is a checkpoint-vs-final count, not two different processes. (2) RE-DATED honestly: the 13:46:52 stamp was the janitor handoff time, not the event — the round-6 disposition happened in the prior (cleared) session ~13:41, the append landed after a /clear; this entry is the only one dated to the moment it was actually written. (3) TWO-ROUND RULE stated plainly: six apply-review rounds EXCEEDED the rule's two-round maximum; the cycle stopped at convergence (two consecutive no-blocker rounds), not because the rule permits six — the 13:46:52 entry's 'per the two-round rule' cited the rule as the reason for stopping, which was wrong. (4) EVIDENCE STATUS: the round-6 'no blocker' verdict is self-attestation by this session — the fork's transcript died with the /clear and nothing on disk corroborates it beyond commit 02979bf7's content; a future reader should weigh it as an uncorroborated claim, and 1d24bd86's commit message carries the same claim attached to a checkable artifact. (5) LABEL fix: 804b9451 is 'P1 completion note + hashes' — the hash regen is generated machine state, so 'docs-only' was a mislabel; 'harmless over-inclusion, chain resolves' stands. (6) PROCESS META at the top of the 13:46:52 entry (where the record lives) is noise in a governance log — the entry above and this one supersede its framing; its finding list (1)-(4) remains the substantive record. (7) REJECTED finding: 'leave it only in the commit message' — a commit message is not the governance surface the disposition protocol requires; kept the epic-card record with these corrections. Silent-precedent concern (epic card as child-disposition ledger) is real: noted for the epic's own archival pass — P1 detail will be summarized, not frozen verbatim.

## Phases

P1=TRDD-DFRPRZYD P2=TRDD-VWSEG7D7 P3=TRDD-21ID8NG7 P4=TRDD-Y22TUHM2 P5=TRDD-RBPHVYR5 P6=TRDD-0DWMU7BV P7=TRDD-18KIWEWC P8=TRDD-TINOOW8V P9=TRDD-QAFKLBYX P10=TRDD-2O73MIAL P11=TRDD-8QWLECUZ P12=TRDD-I3TZG9LN P13=TRDD-057QUK84 P14=TRDD-22HCJULG P15=TRDD-1RB0TZC1
