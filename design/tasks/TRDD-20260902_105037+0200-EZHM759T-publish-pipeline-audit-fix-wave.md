---
trdd-id: EZHM759T
title: Publish pipeline audit fix wave — issues 223 224 plus canon, own pipeline, installers and docs findings
column: dev
created: 2026-09-02T10:50:37+0200
updated: 2026-09-02T10:50:37+0200
current-owner: claude-plugins-validation session
task-type: bugfix
min-approval-requirement: none
external-refs: [https://github.com/Emasoft/claude-plugins-validation/issues/223, https://github.com/Emasoft/claude-plugins-validation/issues/224]
---

# Publish pipeline audit fix wave

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-02

- USER order (2026-09-02): "solve those 2 issues, and also audit the publish.py pipeline (the canon and the agents/skills installing it in the plugins, and also the docs/readme/help)".
- Reports (gitignored, read first on resume): `reports/publish-audit/` — `*-issue-223-fix.md`, `*-issue-224-fix.md`, `*-canon-template-audit.md` (31 rows), `*-cpv-own-publish-audit.md` (22 rows), `*-installers-audit.md` (11 rows), `*-docs-help-audit.md` (7 rows).
- DONE: #223 (ENV_INJECTION added to `_EXECUTION_CLASS_RULES` + 3 tests; CLAUDE.md now cites the right set).
- IN FLIGHT: #224 (push timeout constants in canon + own; process-group kill in `cpv_network_resilience.py`; tests).
- NEXT ACTION: once #224 lands, run the code fix wave — ONE agent per file (`generate_plugin_repo.py`, `publish.py`, `cpv_network_resilience.py`), new test files per agent — then the docs wave LAST (stage numbering must match the final code), then full gates and `publish.py --patch` (v5.15.1).
- Decisions taken: add `--print-gates` to the consumer canon (parity; keeps the 87-check checklist valid) rather than delete CHECK-24/61; delete the four dead submodule helpers; wire `gen_release_binaries_yml` under the binary profile; Windows: G0 fails LOUD with an explicit "POSIX only" message instead of silently blocking; `PLUGIN_FORK_PARITY_CMD` narrowed to extra pytest ARGS; every env bypass listed in `--help`; the four `pipeline-rules.md` copies made byte-identical with a parity test.
- Measured: cold `uvx` build + full remote validate = 76 s (2026-09-02), so no cold-build cliff; the 300/600/1800 s budget mismatch is a consistency defect only.
- SUPERSEDED — do NOT carry forward: the canon audit's original row 4 rationale ("12-20 min cold build"); the own-publish audit's original CRITICAL on `CPV_PUBLISH_SKIP_INSTALL_SMOKE` (test-pinned, deliberate; defect is only that `--help` omits it).

## Acceptance

- [ ] #223 and #224 closed with a fix comment naming the release.
- [ ] Every CRITICAL/MAJOR row of the four audit reports either fixed or explicitly recorded here as by-design with the reason.
- [ ] `--print-gates` exists in the consumer canon; CHECK-24/61 pass on a freshly generated plugin.
- [ ] The four `pipeline-rules.md` copies are byte-identical and a test pins it.
- [ ] Ruff + mypy clean, strict self-validation 0/0/0/0, full suite green, published as v5.15.1 with CI green.

## Notes and lessons learned

- A doc citing the wrong symbol name hid #223 for months: CLAUDE.md said `_SHELL_EXECUTION_CLASS_RULES` (which has ENV_INJECTION) while the sentinel checks `_EXECUTION_CLASS_RULES` (which did not). Cite the symbol the code path actually reads.
