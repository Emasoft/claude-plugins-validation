---
trdd-id: EZHM759T
title: Publish pipeline audit fix wave — issues 223 224 plus canon, own pipeline, installers and docs findings
column: complete
created: 2026-09-02T10:50:37+0200
updated: 2026-09-02T17:00:00+0200
implementation-commits: [182824ce, 46025a3f, 57ae56f2, 9ef7ef52]
current-owner: claude-plugins-validation session
task-type: bugfix
min-approval-requirement: none
external-refs: [https://github.com/Emasoft/claude-plugins-validation/issues/223, https://github.com/Emasoft/claude-plugins-validation/issues/224, https://github.com/Emasoft/claude-plugins-validation/issues/225]
---

# Publish pipeline audit fix wave

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-02

- USER order (2026-09-02): "solve those 2 issues, and also audit the publish.py pipeline (the canon and the agents/skills installing it in the plugins, and also the docs/readme/help)".
- Reports (gitignored, read first on resume): `reports/publish-audit/` — `*-issue-223-fix.md`, `*-issue-224-fix.md`, `*-canon-template-audit.md` (31 rows), `*-cpv-own-publish-audit.md` (22 rows), `*-installers-audit.md` (11 rows), `*-docs-help-audit.md` (7 rows).
- DONE (2026-09-02 12:05): #223, #224, canon wave (29 rows + row-10 wiring), own-publish wave (19 rows), resilience (2 rows), docs waves A+B. Verified first-hand: ruff + mypy clean, 704 focused tests, strict self-val 0/0/0/0 + 15 WARNING after hash regen, all three canon profiles render/compile. CLAUDE.md v5.16.0 entry written.
- COMPLETE (2026-09-02 ~17:00): published as **v5.16.0** (release commit `9ef7ef52`); Gate 2 full suite 13380 passed / 0 failed, CI green on the released commit, clean-dir install smoke passed. #223, #224, #225 auto-closed on push and each carries a fix comment naming the release. First publish attempt was BLOCKED at Gate 2 by #225 (the audit wave's `[N/11]`→`[N/15]` renumber left 12 stage docstrings stale; the consistency test's hardcoded `/11` regex passed vacuously) — fixed in `57ae56f2`, then republished.
- NEXT ACTION: none — terminal.
- Decisions taken: add `--print-gates` to the consumer canon (parity; keeps the 87-check checklist valid) rather than delete CHECK-24/61; delete the four dead submodule helpers; wire `gen_release_binaries_yml` under the binary profile; Windows: G0 fails LOUD with an explicit "POSIX only" message instead of silently blocking; `PLUGIN_FORK_PARITY_CMD` narrowed to extra pytest ARGS; every env bypass listed in `--help`; the four `pipeline-rules.md` copies made byte-identical with a parity test.
- Measured: cold `uvx` build + full remote validate = 76 s (2026-09-02), so no cold-build cliff; the 300/600/1800 s budget mismatch is a consistency defect only.
- SUPERSEDED — do NOT carry forward: the canon audit's original row 4 rationale ("12-20 min cold build"); the own-publish audit's original CRITICAL on `CPV_PUBLISH_SKIP_INSTALL_SMOKE` (test-pinned, deliberate; defect is only that `--help` omits it).

## Acceptance

- [x] #223 and #224 closed with a fix comment naming the release (v5.16.0; #225 too).
- [x] Every CRITICAL/MAJOR row of the four audit reports either fixed or explicitly recorded here as by-design with the reason (see SUPERSEDED + Decisions).
- [x] `--print-gates` exists in the consumer canon; CHECK-24/61 pass on a freshly generated plugin.
- [x] The four `pipeline-rules.md` copies are byte-identical and a test pins it.
- [x] Ruff + mypy clean, strict self-validation 0/0/0/0, full suite green (13380 passed), published as v5.16.0 (was planned as 5.15.1; `--minor` because the canon gained `--print-gates`) with CI green.

## Notes and lessons learned

- A doc citing the wrong symbol name hid #223 for months: CLAUDE.md said `_SHELL_EXECUTION_CLASS_RULES` (which has ENV_INJECTION) while the sentinel checks `_EXECUTION_CLASS_RULES` (which did not). Cite the symbol the code path actually reads.
