---
trdd-id: a0ab2363-5fbd-402d-922d-b00d6bd85516
title: Red-team FP discriminators + devitalize transforms for FN-holes, broad-audit all security docs/skills/code, fix + publish
column: dev
created: 2026-06-09T12:33:17+0200
updated: 2026-06-09T12:33:17+0200
current-owner: main-session
assignee: main-session
priority: 1
severity: HIGH
effort: XL
task-type: security
parent-trdd: null
relevant-rules: []
release-via: publish
test-requirements: [unit, lint, typecheck]
audit-requirements: [security-scan, adversarial-scan]
review-requirements: [code-review]
impacts: [public-api]
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/67"]
---

# Security audit + red-team of CPV FP discriminators / devitalize transforms, then fix + publish

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-06-09

**Current state:**
- Workflow `wcks98lk8` (run `wf_bb1014b3-edb`) is RUNNING in background: 6 red-team probes (real fixtures + real validator) + 7 audit groups, each finding adversarially verified. Script at `.../workflows/scripts/cpv-security-audit-redteam-wf_bb1014b3-edb.js`. Returns `{summary, fn_holes, confirmed, reports, per_job}`.
- Issue #67 already CLOSED (2026-06-09) working-as-designed; comment 4658749983.

**NEXT ACTION (when workflow notifies):** read the returned `fn_holes` + `confirmed` findings and the per-job report paths under `reports/security-audit-redteam/`. Triage: any CONFIRMED fn-hole is a real CPV security bug to FIX. Then dispatch parallel opus fix agents (DISJOINT files), verify each fix two-sided (malicious sibling MUST still fire), loop-until-dry re-audit, then validate + test + publish (real release, bump version).

**Load-bearing facts / gotchas:**
- The user's core worry (verbatim): "be more strict — are you sure no malicious code can run?" The prime suspect is `_line_is_pattern_definition` (validate_security.py:2269) whose hints `_PATTERN_DEFINITION_HINTS` (2257) include a BARE `r"` / `r'` — which may wrongly clear a line that is ALSO an exec sink (`os.system(r"evil")`, `exec(r"…")`). This filter runs on cc-audit findings (~6941). RT1 tests exactly this. If confirmed → CRITICAL FN-hole → fix (the hint must NOT clear a line containing an exec sink).
- never-suppress invariant: ONLY provably-inert data auto-clears; any execution path BLOCKS; every suppression needs a 2-sided test (benign clears + malicious sibling STILL fires). [[feedback-never-suppress-never-relax-gate]]
- ALWAYS opus for security analysis. [[feedback-opus-for-security-analysis]]
- Self-scan-skip by SHA: after editing CPV's OWN files, REGEN MANIFEST (`uv run python scripts/_plugin_compute_hashes.py`) BEFORE trusting a local self-validate, else stale-manifest self-scan artifacts fire as false findings. Local self-scan needs `PLUGIN_SKIP_GITHUB_INTEGRITY=1`.
- validator launcher: `CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml python scripts/remote_validation.py security|plugin <path> -o <out>` (running validate_security.py directly errors).
- Two outstanding USER asks beyond the audit: (a) refine the #67 close comment with the PRECISE detector-catalog transform (pattern→raw-string/regex signature [if RT1/RT2 prove it sound], description→reword) — my posted comment said "elide a fragment" which is WRONG for a detector pattern; (b) run `plugin-devitalizer` against a detector-catalog fixture end-to-end, extend `devitalize-threats` if it doesn't cover pattern→raw-string.

**SUPERSEDED — do NOT carry forward:**
- ✗ My earlier confident claim that "a raw-string/regex literal provably can't shell out" is UNDER RED-TEAM — do NOT treat it as established until RT1/RT2 verdicts are in. If RT1 confirms the `r"` hint clears an exec line, the claim is FALSE and there is a real bug.

**Durable artifacts to read before acting:**
- `reports/issue-67-remnants/20260608_180451+0200-verify-67-remnants.md` — the #67 verify-first reproduction (is_pattern_source_line proven attacker-satisfiable; cc-audit never traverses the classifier).
- `reports/security-audit-redteam/*` — per-job red-team + audit reports (written by the running workflow).

## The directive (user, 2026-06-09)

> "You must be more strict. Are you sure this is enough to ensure there will be no chance of malicious code being run? Verify. And about your question: yes, both things you said and also check all docs, security code and skills for related errors. And since you are already reading all security related docs, skills and code, audit them all for errors, missing things, wrong instructions, and potential issues. Iterate until you get no issues found. Use ultracode if you need to. Fix everything and test/publish."

## Plan

1. **[in progress] Find + verify** (workflow `wcks98lk8`): red-team every FP discriminator + devitalize transform for FN-holes with real fixtures; broad-audit all security docs/skills/code; adversarially verify each finding.
2. **Triage** the confirmed findings (FN-holes first).
3. **Fix** (parallel opus agents, disjoint files; 2-sided tests; manifest regen last).
4. **(a)** Refine the #67 close comment with the precise transform once RT1/RT2 settle the raw-string soundness question.
5. **(b)** Test `plugin-devitalizer` against a detector-catalog fixture; extend `devitalize-threats` if pattern→raw-string isn't covered.
6. **Loop-until-dry** re-audit until zero issues.
7. **Validate + test + publish** (real release, bump version, CI green).

## DERIVED tasks / risks

- Fixing the `r"`-hint over-permissiveness (if confirmed) must NOT regress the legitimate regex-definition clears that #67/#42/#57 rely on — re-run those issues' two-sided tests after the fix.
- Tightening any discriminator risks re-introducing the FPs it was added to suppress — every fix re-runs the relevant issue's benign-clears test AND adds the malicious-sibling test.
- A real publish re-runs the plugin's full test suite as the G4 gate — budget time; fix any flake in CPV's OWN tests, not by relaxing the gate.
