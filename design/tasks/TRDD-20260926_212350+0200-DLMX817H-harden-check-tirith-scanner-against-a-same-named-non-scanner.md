---
trdd-id: DLMX817H
title: Harden check_tirith_scanner against a same-named non-scanner on PATH
column: backburner
status: tasked
created: 2026-09-26T21:23:50+0200
updated: 2026-09-26T21:24:05+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-26T21:23:50+0200
---

# Harden check_tirith_scanner against a same-named non-scanner on PATH

## Approval log

- 2026-09-26T21:23:50+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Notes

Residual from the TRDD-21ES7XEX adversarial review (2026-09-26): check_tirith_scanner treats ANY tirith on PATH as the sheeki03 scanner, but the name is shared — a wrong PyPI package (a WAMP monitor, exactly what this host had installed) answers rc=1 with non-JSON stdout and turns a clean scanner-miss into a JSON-parse finding. The 09-25 investigation itself raised the fail-closed-on-non-JSON alternative and it was not built. Two candidate directions to evaluate together: (1) fail-closed — a non-JSON response from the resolved runner is reported as 'tirith: runner produced non-JSON output (wrong binary or crash)' rather than a parse finding, NEVER a pass; (2) identity probe — a cheap version/subcommand sanity check (the real scanner answers 'tirith 0.4.x' and has a scan subcommand) before trusting PATH resolution. Severity discipline: cannot-check is never clean ([[lesson-cannot-check-is-not-clean]]); a WARNING naming the possibility is the minimum, never silent suppression. Also record on this card: the archived 21ES7XEX closure overstated its mechanism claim ('confirmed as fact' for an unobserved hypothesis) and the review caught it — the archived card carries the correction only in its git history note, not in the file itself.
