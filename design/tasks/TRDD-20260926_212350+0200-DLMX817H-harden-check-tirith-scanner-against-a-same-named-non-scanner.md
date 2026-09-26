---
trdd-id: DLMX817H
title: Harden check_tirith_scanner against a same-named non-scanner on PATH
column: todo
status: tasked
created: 2026-09-26T21:23:50+0200
updated: 2026-09-26T22:05:27+0200
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
- 2026-09-26T22:05:02+0200 — column → todo by main-agent@claude-plugins-validation.

## Notes

Residual from the TRDD-21ES7XEX adversarial review (2026-09-26): check_tirith_scanner treats ANY tirith on PATH as the sheeki03 scanner, but the name is shared — a wrong PyPI package (a WAMP monitor, exactly what this host had installed) answers rc=1 with non-JSON stdout and turns a clean scanner-miss into a JSON-parse finding. The 09-25 investigation itself raised the fail-closed-on-non-JSON alternative and it was not built. Two candidate directions to evaluate together: (1) fail-closed — a non-JSON response from the resolved runner is reported as 'tirith: runner produced non-JSON output (wrong binary or crash)' rather than a parse finding, NEVER a pass; (2) identity probe — a cheap version/subcommand sanity check (the real scanner answers 'tirith 0.4.x' and has a scan subcommand) before trusting PATH resolution. Severity discipline: cannot-check is never clean ([[lesson-cannot-check-is-not-clean]]); a WARNING naming the possibility is the minimum, never silent suppression. Also record on this card: the archived 21ES7XEX closure overstated its mechanism claim ('confirmed as fact' for an unobserved hypothesis) and the review caught it — the archived card carries the correction only in its git history note, not in the file itself.
REVIEW ROUND 2 (2026-09-26): four sharpening requirements recorded so this card's framing does not become a defective spec. (1) Non-JSON is NOT one case — the implemented guard must distinguish: non-zero exit WITH valid JSON (scanner failure mode, not wrong-binary), exit 0 with EMPTY output (a genuinely clean scan, NOT wrong-binary), and non-JSON output (the wrong-binary/crash case). Mislabeling empty output as 'wrong binary' would be an FP on real clean scans; mislabeling non-zero-with-JSON as wrong-binary would be an FP on real scanner failures. Acceptance criteria needed: a positive control per case, two-sided. (2) The archived 21ES7XEX box-1 sentence 'mechanism CONFIRMED as fact' must be read as DOWNGRADED to evidence-consistent-hypothesis; the supersession record lives HERE — the archived file is immutable. A future reader of 21ES7XEX should check this card before trusting box 1's wording. (3) Box-1's [x] tick itself stays standing but is disputed by review round 1 — record kept here because the archived card cannot carry it. (4) Whether the overclaiming line qualifies for the trdd-design-tasks §12 machine-verifiable-false-line removal exception was raised by review round 2 and deliberately NOT pursued: the exception is narrow, the sentence's falsity is interpretive (hypothesis stated as fact, not a false factual claim), and re-litigating an archived card's immutability costs more than this pointer. Review round 2 also caught the missed hash regen after this card's commit — fixed in commit 81d5a07a.
REVIEW ROUND 3 (final, cap reached) — two additions. (1) FOURTH runner case for the guard spec: exit 0 with well-formed JSON of the WRONG SCHEMA (schema_version mismatch) — a same-named collision tool can emit valid non-scanner JSON that both the empty-output and non-JSON branches would mislabel; the identity probe must check schema_version (the real scanner emits schema_version 5), not just version-string presence. All four cases need per-case two-sided controls. (2) EPISTEMIC CHAIN, surfaced to the user: box 1's downgrade to hypothesis propagates — boxes 2/3's 'no code change was needed, the defect was host-environmental' conclusion inherits the same epistemic status. Mitigation already in place: the code gap is tracked on THIS card regardless of which hypothesis holds, so no exposure is created by leaving the archived card alone; reopening 21ES7XEX is the user's call.
E2E PROOF FOUND A REAL FN (2026-09-26) — this card is now in todo. The behavioral probe the review demanded (real tirith 0.4.2 binary over a real finding fixture, driven through check_tirith_scanner) exposed a schema mismatch the shim tests cannot see: tirith 0.4.2 emits findings NESTED under files[].findings[] (per-file objects carrying rule_id/severity HIGH/evidence), with a top-level total_findings count. check_tirith_scanner's parser (validate_security.py ~7507) accepts only top-level list / findings|results|verdicts|issues keys / SARIF runs[].results — NONE match, so a REAL 4-finding scan (pull_request_target workflow fixture, severity HIGH) parses as ZERO findings and the check emits 'tirith (local): no findings (external scan clean)'. CONFIRMED first-hand: issues_found=0, findings emitted=0 against the fixture. The severity map is also doubly-mismatched: it keys on rule/severity/verdict strings while 0.4.2 emits rule_id + uppercase HIGH. Fix scope: extend the parser with the files[].findings[] shape + lowercase severity, add the four runner cases (empty-output / non-zero-with-JSON / non-JSON / wrong-schema) as acceptance criteria with per-case two-sided controls INCLUDING a real-0.4.2-schema positive control that FAILS against the current parser, and keep the identity-probe idea as defense-in-depth. Note: the clean-scan branch works (0 findings reads clean correctly); the FN is only on findings-present scans — silent, the worst direction, and exactly the cannot-check-is-not-clean shape.
