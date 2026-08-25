---
trdd-id: 06JG1XC9
title: Security-scan a single agent together with its reachable skill closure
column: complete
created: 2026-07-30T11:55:20+0200
updated: 2026-08-25T17:25:16+0200
current-owner: cpv-main-session
task-type: security
approval-tier: 0
parent-trdd: 7KS7KP7U
blocked-by: []
relevant-rules: []
---

# Security-scan a single agent together with its reachable skill closure

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-30

- **Design:** `design/specs/agent-closure-and-variants.md` §4 is NORMATIVE
  (§1–§2 for the closure surface this consumes).
- **NEXT ACTION:** add `scripts/cpv_agent_security.py` + the `agent-security`
  command in `remote_validation.py`.

## The gap

`validate_security.py` targets a PLUGIN (a directory, a GitHub URL, or an
archive) or a whole marketplace. There is no way to scan ONE agent — and an
agent's real attack surface is not its own file: it is every skill that agent can
load, because a reachable skill's body enters the agent's context as
instructions.

## Scope

Target = one agent file. Scan set = the agent `.md` **plus** `closure_files()`:
each reachable `SKILL.md`, its `references/**`, its `scripts/**`.

## Invariants

- **SSOT.** Reuse `validate_security`'s scanning machinery. Do not copy a rule, a
  pattern, or a severity mapping. If the needed entry point is plugin-scoped,
  extract the file-set-scoped core and have the plugin path call it — one
  definition, two callers. A second copy of a security grammar drifts, and a
  drifted copy is a false negative.
- Honour every existing suppression chain (self-scan, vendored, dev-scratch,
  test, gitignored-and-untracked).
- **"Cannot reach" is not "clean".** A closure skill that is UNREACHABLE because
  the `Skill` gate is shut is reported in a separate `unreachable` section and
  does not gate — it cannot execute. It is never silently dropped.
- Never suppress a rule, never relax `--strict`.
- Works on an UNINSTALLED source: no dependence on an install slug, marketplace,
  or cache path.

## Pass criteria

- `remote_validation.py agent-security <agent.md>` scans the agent and its
  closure; identical severity/exit-code/`--json`/`--strict` contract as
  `validate_security`.
- Two-sided: a planted payload in a REACHABLE closure skill is reported and
  gates; the same payload in an UNREACHABLE one is reported but does not gate;
  a clean closure is clean.
- `ruff` + `mypy` clean; cold strict self-validate 0/0/0/0.

## Approval log

- 2026-07-30T11:55:20+0200 — Tier 0, authored directly as authorized work: the
  USER directed this feature explicitly ("add the option to security scan single
  agents").
- 2026-08-25T17:25:16+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v4.0.0 — scripts/cpv_agent_security.py live, verified first-hand (batch_af)
