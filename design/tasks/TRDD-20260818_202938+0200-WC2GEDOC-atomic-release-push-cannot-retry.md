---
trdd-id: WC2GEDOC
title: canonical publish.py atomic release push cannot retry on transient errors
column: ai_review
created: 2026-08-18T20:29:38+0200
updated: 2026-08-19T00:05:00+0200
current-owner: cpv-session
task-type: bugfix
priority: 1
labels: [phase-2, hub-dispatch, canonical-pipeline]
external-refs: [TRDD-BRRJK57P]
---

# Atomic release push cannot retry (P1, CANONICAL template fix)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-08-18

**NEXT ACTION:** fix `scripts/publish.py:2775` — the `git push --atomic` call passes
`capture_output=False`, so `result.stderr` is `None`, `git_with_retry`'s classifier hits
`if not stderr: return False` (cpv_network_resilience.py:116) and a transient network
failure is treated as permanent: the retry wrapper NEVER retries the release push.

## Verified defect chain (CPV tree, 2026-08-18)

publish.py:2775 `git_with_retry([...push --atomic...], capture_output=False)` →
cpv_network_resilience.py:242 `stderr = result.stderr or ""` →
`is_transient_subprocess_error("")` → line 116 `if not stderr: return False` → break.
Hub-verified fleet-wide: 12 of 22 publish.py copies share the defect. This card is
TEMPLATE-LEVEL: fix the canon here; downstream repos inherit on their next template sync.

## Reference implementation

assistant-role's fix `7b4e8ba`: capture stderr so the transient classifier sees it, echo
the captured stderr on final failure (the user must still see git's output when the push
genuinely fails), regression test.

## Acceptance criteria

- [ ] The atomic push captures stderr; a transient failure (simulated 5xx/connection
      reset stderr) is retried; a permanent failure (auth, non-fast-forward) is not.
- [ ] On final failure the captured stderr is echoed so nothing is swallowed.
- [ ] Regression test covering the transient-retries and permanent-no-retry paths.
- [ ] The emitted canon (what canonical-pipeline ships to downstream repos) carries the
      fix — per the "fix what the tool EMITS" lesson, not just this repo's own copy.

## Approval log

- 2026-08-18T20:29:38+0200 — Authored at `todo` under the USER delegation recorded in
  TRDD-BRRJK57P (hub dispatch, Phase 2).
