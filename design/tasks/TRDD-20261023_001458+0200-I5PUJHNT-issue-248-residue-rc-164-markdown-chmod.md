---
trdd-id: I5PUJHNT
title: Issue 248 residue RC-164 Markdown chmod
column: backburner
status: tasked
created: 2026-10-23T00:14:58+0200
updated: 2026-10-23T00:36:38+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: bugfix
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:14:58+0200
parent-trdd: N4GLZXZ6
---

# Issue 248 residue RC-164 Markdown chmod

## Symptom
Residue of issue 248 (RC-164, Markdown chmod narrowing), taken from worker A's 248 report (local, not committed), REVISION 2. All remaining items:
1. (m5) An uppercase CHMOD +x is never judged. _CHMOD_EXEC_PATTERNS is case-sensitive, and on a case-insensitive filesystem CHMOD runs /bin/chmod. Pre-existing gap, not changed in 248.
2. English create verbs in prose (Create the file ...) are not counted as creation; only command words and > are.
3. The file-wide directory-change flag fires on CI/CD and similar words in prose, which turns the narrowing off for such pages. Accepted because it fails toward flagging; revisit only if it is measured as noise.
4. The NFC retry also applies to a word under the HEAD handling, which makes it stricter only. Recorded, no action unless a regression shows.
5. (release step, handled by the 5.23.0 manifest regeneration; not residue)
6. One mutant survives: the per-file memoisation of the git index stamp. It changes no verdict (equivalent for correctness); the growth-ratio test does not catch it.
7. (unrelated to 248) Pre-existing ruff format debt elsewhere in scripts/cpv_inplugin_write_guard.py is unchanged.
Search words: an uppercase CHMOD in a Markdown setup guide is never judged as an in-plugin write.

## Code location
scripts/cpv_inplugin_write_guard.py:976 (_MD_CHMOD_LINE_RE), :940 (_MD_PLAIN_COMMANDS), :162 (_CHMOD_EXEC_PATTERNS, case-sensitive).

## Proposed fix
Item 1: match chmod case-insensitively in _CHMOD_EXEC_PATTERNS and the Markdown chmod judgement. Items 2 to 7: decide per item whether to fix, measure, or close; each is small.

Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:14:58+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.

## Shipped part

The partial fix for issue 248 shipped in commit e951f181 (5.23.0).
