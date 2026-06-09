---
trdd-id: a0ab2363-5fbd-402d-922d-b00d6bd85516
title: Red-team FP discriminators + devitalize transforms for FN-holes, broad-audit all security docs/skills/code, fix + publish
column: dev
created: 2026-06-09T12:33:17+0200
updated: 2026-06-09T15:29:20+0200
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

**NEXT ACTION:** await Wave 1 (`w6xmn4g6c`) → for each group read fixer summary + reverify verdict; re-dispatch any group whose reverify ≠ pass (throttled). Then run Wave 2 (doc fixes consistent with W1 code). Then central verify+publish (see WAVE PLAN). Any reverify that says holes_closed=false is a STILL-OPEN hole — do not proceed to publish with an open fn-hole.

**AUDIT COMPLETE (workflow wcks98lk8 → resumed wbegf1odv):** 42 CONFIRMED findings, 12 FN-holes, by severity CRITICAL=9 HIGH=13 MEDIUM=10 LOW=8 NIT=2. Consolidated triage: `reports/security-audit-redteam/_CONSOLIDATED-findings.json` (id/kind/severity/file/line/title/proposed_fix per finding); per-job detail reports in same dir. TOP CRITICAL FN-holes: RT2/RT4 (`_skillaudit_python_context.py` _classify_call) reassembled-var `os.system(cmd)` RCE passes 100/100; RT4-plugin-gate (`validate_plugin.py`) plugin gate skips the RC security pass so plain `os.system("curl|bash")` passes; RT3 (`validate_security.py`) self-scan trust flippable by plugin.json NAME; RT5×2 aliased/getattr exec sinks evade (`cpv_taint_engine.py`,`cpv_validation_common.py`); RT4 rot13/charcode decoders unrecognized (`cpv_validation_common.py`,`cpv_skillaudit_native.py`); RT1 (HIGH) cc-audit `r"` hint clears exec lines. Plus HIGH shell/md over-suppressions + doc bugs (devitalize/harden recipes, README/menu).

**WAVE PLAN:** W1 = 8 engine code files (groups A-H), each opus fixer (disjoint file) FN-safe + two-sided test, then independent reverify re-runs the red-team repro to confirm hole CLOSED + benign still clears. W2 = doc fixes (devitalize-threats, harden-and-redact, plugin-devitalizer, menu-tree, README — groups I/J/K) made CONSISTENT with the W1-fixed code. Then central: stage all + REGEN MANIFEST + full pytest -n auto + plugin-level self-validate --strict (PLUGIN_SKIP_GITHUB_INTEGRITY=1) + re-run red-team probes (loop-until-dry) + publish (bump, CI green).

**RATE-LIMIT LESSON (this session):** launching 8 opus agents at once into a hot quota (session had spent ~17M subagent tokens) → ALL 8 throw "Server is temporarily limiting requests" at launch. FIX = throttle the fan-out: cap 3 concurrent + ~9s ramp + jittered exponential backoff (agentRetry/pool in the wave-1 script). Per CLAUDE.md corpus-distillation rule. The dispatcher's `[janitor-resume] rate-limit cleared after Ns` is the clear-signal.

**WAVE 1 DONE (engine, 8 groups) — ALL HOLES CLOSED + VERIFIED.** 7/8 passed workflow reverify (B,C,D,E,F,G,H); group A failed reverify (its classifier fix was DEAD CODE for 6 of 8 always-shell sinks — regex layer never matched os.popen/subprocess.getoutput) → completion agent extended `scripts/rules/skillaudit_patterns.json` SHELL_EXEC regex for all 8 sinks' bare-call form + end-to-end tests → I INDEPENDENTLY spot-checked (my own /tmp fixture): os.popen(<var>)+subprocess.getoutput(<var>) now FIRE SHELL_EXEC end-to-end (INVALID/CRITICAL on the sink line), benign literals clear. Changed: 10 source files (_skillaudit_markdown_context, _skillaudit_python_context, _skillaudit_shell_context, cpv_fp_classifier, cpv_fp_classifier_rules, cpv_skillaudit_native, cpv_taint_engine, cpv_validation_common, validate_plugin, validate_security) + scripts/rules/skillaudit_patterns.json + 8 new tests/test_secaudit_*.py + edited tests/test_issues_40_42_skillaudit_fp.py (legit STRENGTHENING — suppress→demote, verified). ruff+mypy+pyright ALL CLEAN on the 10 files.

**CENTRAL VERIFY (partial):** full suite was 5 failed / 8833 passed / 2 skip — ALL 5 caused by group A's catalog edit needing DERIVED-METADATA regen: (3,4,5) `scripts/rules/re2_compatibility.json` stale → RAN `uv run python scripts_dev/regen_re2_compat.py` (SHELL_EXEC 7→8, still re2-compatible, total 528) → those 3 now PASS; (1,2) `test_issue_42_self_artifact_skip` compares a catalog copy vs the MANIFEST hash → will pass after the FINAL manifest regen. re2 IS importable here.

**CHANGELOG ALIGNMENT (CC 2.1.169):** 9 aligned / 1 gap / 2 n-a, NO blocking-FP. The 1 gap = `scripts/validate_hook_output.py` must add `additionalContext` to Stop(~185)+SubagentStop(~179) output sets (CC 2.1.163) — being fixed in Wave 2 group M. Report under reports/cc-changelog-alignment/.

**WAVE 2 DONE — 4/4 pass** (I-devitalize-docs, J-harden-docs, K-menu-readme, M-cc-align-hook-output; consistent_with_code + no_new_findings; skills Grade A --strict; recipes scanner-verified inert; M added additionalContext to validate_hook_output Stop/SubagentStop + 2-sided test).

**CENTRAL VERIFY (post all waves):** staged 30 files + REGENERATED MANIFEST (878 hashes, .plugin-self-hashes.json + .cpv-self-hashes.json staged). ruff+mypy+pyright ALL CLEAN on 11 changed py. FULL SUITE = **8843 passed, 2 skipped, 0 failed** (the earlier 5 failures fixed by re2_compat regen + manifest regen). Consolidated red-team spot-check: plugin-gate on a combined-attack fixture = INVALID/CRITICAL:7 (reassembled os.popen/getoutput, plain curl|bash, getattr-exec, rot13, charcode ALL fire); RT3 name-spoof fixture (named "claude-plugins-validation") → malicious os.system(r"…|sh") @scripts/evil_patterns.py:3 STILL FIRES RC-136 (name does NOT silence).

**REGRESSION caught by self-validate (the central step's value):** `remote_validation.py plugin . --strict` on CPV ITSELF = INVALID (MAJOR:1 MINOR:5 NIT:1), ALL RC-73 taint "tainted function-param reaches getattr(obj,<tainted attr>)" on CPV's OWN benign reflection (publish.py:134 getattr(self._real,name); 5 tests). ROOT CAUSE (verified): RC-73 getattr-sink + function-param-source are PRE-EXISTING by design (cpv_taint_engine 369-373/621-629); group F's `_run_security_execclass_gate` now runs check_phase10_taint at the PLUGIN gate (didn't before) WITHOUT applying CPV's self-scan-skip → CPV flags its own files. FIX in flight: agent `a4dea558a2b4c986c` (F2) — apply self-scan-skip to group F's pass (B1; SHA-verified `_CPV_IS_RUNNING_CPV` identity, NOT spoofable) or tighten getattr-sink (B2) if security subcommand also surfaces it; 2-sided (benign getattr clears, getattr(os,untrusted)() fires); re-validate CPV → 0/0/0.

**RESUME STATUS:** F2 fix agent `a4dea558a2b4c986c` running. ON F2 DONE: re-stage edited files + RE-REGEN MANIFEST (validate_plugin.py / cpv_taint_engine.py changed again) + re-run FULL suite (0 failed) + re-run self-validate (CPV 0/0/0) + final consolidated red-team spot-check. THEN commit + publish.

**NEXT (publish):** commit the whole hardening (stage by NAME, never -A) → `publish.py --minor` (feat: 12 FN-holes closed + 30 findings + CC 2.1.169 align). publish.py G2 tests run BEFORE G8 manifest-refresh → manifest MUST be committed-current first. Verify CI+Release+Notify green. Then update GitHub issues if any + MEMORY.md.

**NEXT (after Wave 2):** (1) move/keep stray reverifier scratch out of tests/ (already moved test_secaudit_D_reverify.py → reports_dev/secaudit-scratch/); (2) `git add` all new+changed BY NAME (never -A); (3) REGEN MANIFEST `uv run python scripts/_plugin_compute_hashes.py` (fixes self-artifact tests 1,2); (4) FULL suite `uv run pytest -q -n auto` → must be 0 failed; (5) plugin-level self-validate `PLUGIN_SKIP_GITHUB_INTEGRITY=1 … remote_validation.py plugin .` --strict → clean; (6) red-team re-run loop-until-dry (my fixtures + the workflow reverifiers already confirmed each hole); (7) publish via publish.py (`--minor` — this is a feat: FN-hole hardening + CC alignment) → CI+Release+Notify green. publish.py G2 tests run BEFORE G8 manifest-refresh, so the manifest MUST be regenerated+committed BEFORE publish or tests 1,2 fail at G2.

**CONFIRMED FINDING RT1 (HIGH fn-hole) — read `reports/security-audit-redteam/20260609_123843+0200-RT1-execline-rawstring.md`:** `_line_is_pattern_definition` (validate_security.py:2269) `_PATTERN_DEFINITION_HINTS` (2257) includes BARE `r"` / `r'` → returns True on an EXEC line (`os.system(r"curl|sh")`, `subprocess.run(r"…", shell=True)`) → DROPS the cc-audit finding on that line (sole call site @6941). Proven: True on 5 exec sinks; cc-audit MW-018/MW-002 CRITICALs carry the `r"` hint. NOT total today (in-process RC-122/123/136/34/skillaudit backstop the tested shapes) but a cc-audit-ONLY rule (e.g. MW-018 /etc/passwd) on a raw-string line has NO backstop → fully dropped. FIX (RT1 report): replace the @6941 guard with the flow-sensitive `is_pattern_source_line` (cpv_pattern_source_predicate; returns False for raw-string→exec-sink — VERIFIED), delete `_line_is_pattern_definition`+`_PATTERN_DEFINITION_HINTS` (no other callers), 2-sided test (real `re.compile(r"…/etc/passwd…")` still suppressed / `os.system(r"…")` stays visible). MINIMAL alt: drop `r"`/`r'` from hints + require match INSIDE a real regex literal.

**Load-bearing facts / gotchas:**
- The user's core worry (verbatim): "be more strict — are you sure no malicious code can run?" The prime suspect is `_line_is_pattern_definition` (validate_security.py:2269) whose hints `_PATTERN_DEFINITION_HINTS` (2257) include a BARE `r"` / `r'` — which may wrongly clear a line that is ALSO an exec sink (`os.system(r"evil")`, `exec(r"…")`). This filter runs on cc-audit findings (~6941). RT1 tests exactly this. If confirmed → CRITICAL FN-hole → fix (the hint must NOT clear a line containing an exec sink).
- never-suppress invariant: ONLY provably-inert data auto-clears; any execution path BLOCKS; every suppression needs a 2-sided test (benign clears + malicious sibling STILL fires). [[feedback-never-suppress-never-relax-gate]]
- ALWAYS opus for security analysis. [[feedback-opus-for-security-analysis]]
- Self-scan-skip by SHA: after editing CPV's OWN files, REGEN MANIFEST (`uv run python scripts/_plugin_compute_hashes.py`) BEFORE trusting a local self-validate, else stale-manifest self-scan artifacts fire as false findings. Local self-scan needs `PLUGIN_SKIP_GITHUB_INTEGRITY=1`.
- validator launcher: `CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml python scripts/remote_validation.py security|plugin <path> -o <out>` (running validate_security.py directly errors).
- Two outstanding USER asks beyond the audit: (a) refine the #67 close comment with the PRECISE detector-catalog transform (pattern→raw-string/regex signature [if RT1/RT2 prove it sound], description→reword) — my posted comment said "elide a fragment" which is WRONG for a detector pattern; (b) run `plugin-devitalizer` against a detector-catalog fixture end-to-end, extend `devitalize-threats` if it doesn't cover pattern→raw-string.

**SUPERSEDED — do NOT carry forward:**
- ✗ My earlier confident claim that "a raw-string/regex literal provably can't shell out, and the existing `_line_is_pattern_definition` clears it FN-safely" is now CONFIRMED FALSE by RT1. `_line_is_pattern_definition` is FN-UNSAFE (bare `r"` hint clears exec lines). The FN-SAFE discriminator is the flow-sensitive `is_pattern_source_line` (returns False for raw-string→exec-sink). Any user-facing answer / #67 comment that cited `_line_is_pattern_definition` as the safe mechanism must be corrected to cite `is_pattern_source_line` AFTER the @6941 swap is shipped. The detector-catalog devitalize transform (pattern→raw-string/regex) is still sound, but ONLY because the flow-sensitive predicate (not the broken helper) is what proves a DATA-context literal inert while an exec-context one stays flagged.

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
