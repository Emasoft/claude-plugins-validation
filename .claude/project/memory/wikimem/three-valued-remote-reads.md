---
name: three-valued-remote-reads
description: "a destructive git action (tag delete, force push, retry) ran on a remote read that failed and was treated as absent; remote state checks must be three-valued present/absent/unknown and fail closed on unknown"
ocd: 2026-08-19
lmd: 2026-08-19
metadata:
  node_type: memory
  type: project
  tier: aspect
publish-globally: false
---

# three-valued-remote-reads


^ATOM-778F-38ZD [desc: "Remote-state probes gating destructive git actions return present/absent/unknown and fail closed on unknown", keywords: remote_tag_state ls-remote_failure_treated_as_absent three-valued_read fail_closed_unknown destructive_git_action_gate force_push_retry publish_tag_collision, type: project, ocd: 2026-08-19, lmd: 2026-08-19]

Any read of REMOTE state that gates a destructive git action (delete a remote tag, force-push, retry a failed push) must be THREE-VALUED: present / absent / unknown. A failed 'git ls-remote' is UNKNOWN, not absent — TRDD-6UW0KZVY made publish.py's _remote_tag_state fail CLOSED on unknown (abort the destructive branch) after the two-valued version treated a network error as 'tag absent' and would have re-pushed over an existing release tag. Pattern: return an enum, never a bool, from remote probes; the destructive caller proceeds only on a definite value.

## Notes and lessons learned
