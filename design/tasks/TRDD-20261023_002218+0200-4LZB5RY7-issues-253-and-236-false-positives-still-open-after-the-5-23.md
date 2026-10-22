---
trdd-id: 4LZB5RY7
title: Issues 253 and 236 false positives still open after the 5.23.0 precision attempt was dropped
column: backburner
status: tasked
created: 2026-10-23T00:22:18+0200
updated: 2026-10-23T00:33:47+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:22:18+0200
parent-trdd: N4GLZXZ6
---

# Issues 253 and 236 false positives still open after the 5.23.0 precision attempt was dropped

## Symptom
Search words: a negated sentence about not sending secrets is still flagged as exfiltration intent; a Rust assert comparing an auth header literal is still flagged as token theft.

## Parked work
A precision change was built and then DROPPED from 5.23.0. It is parked on git branch wip/f-precision-253-236 (commit dca09fe6). Its tests and corpus are in the gitignored tests_dev/f-precision-523-parked/ in the main checkout.

## Why it was dropped (final review)
- All 4 reporter-exact samples still fired, so it closed neither issue.
- It read the scan root from the environment variable CPV_SKILLAUDIT_WORKER_PLUGIN_ROOT, which breaks the owner policy that nothing set at launch may change CPV's behaviour.
- A renamed import (use crate::evil as assert;) or a #[macro_use] dependency evades its macro-shadow check.
- The crate walk followed symlinked .rs files with no read cap.
- It widened the base pattern with api keys without measuring the effect.

## Next steps for issue 253
The reporter's text is a refusal verb (Refusing to ...), plus error text inside a code span. Whether refusal verbs count as negation is a design decision for the OWNER. Ask before building.

## Next steps for issue 236
Fetch the reporter's real source line (read-only). A clear is acceptable only if CPV can prove that assert resolves to the standard-library macro; a list of known shadowing spellings is not proof (the dropped attempt was evaded by a renamed import). If that proof is not feasible, keep flagging and close 236 as won't-fix by owner decision. Constraints any attempt must meet:
- the scan root is derived by CPV from the validated target path and passed as a function argument, never from the environment or any caller-supplied override;
- no fallback root walk, so with no root there is no clear;
- even with that proof, still refuse the clear when any as assert* import, #[macro_use], use ...assert or proc_macro appears under the root (an extra refusal, never a substitute for the proof);
- lstat each file and read regular files only, with caps (2 MiB per file, plus a total file count);
- key the cache on (root, scan nonce); every cache key, including the deep cache, carries the crate flag;
- use an internal sentinel for the INFO trail instead of a string key.

Fail toward flagging; no plugin-side exception (owner policy).
Found during the 5.23.0 release review.

## Approval log

- 2026-10-23T00:22:18+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- 2026-10-23 — Backlog record only; not approved as a work order.
