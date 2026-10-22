---
trdd-id: N4GLZXZ6
title: Hardening - no external input may skip lower or narrow CPV validation and every installed file is scanned
column: dev
status: tasked
created: 2026-10-22T02:37:19+0200
updated: 2026-10-22T04:07:47+0200
current-owner: main-agent@complete-plugin-validation
created-by: main-agent@complete-plugin-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@complete-plugin-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@complete-plugin-validation
approval-datetime: 2026-10-22T02:37:19+0200
implementation-commits: [ce0ed7f1, 77cf67ac, 1b860b49, 8621d2d5, a821c6b7, 087e00a8]
---

# Hardening - no external input may skip lower or narrow CPV validation and every installed file is scanned

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-10-22
Epic opened 2026-10-22. Increment 1 committed as ce0ed7f1 and reviewed (no blocker). NEXT ACTION: 5.22.3 is published. Await the owner on reopening issue 261 and on the proposed pre-push closing-keyword guard; then port increments 1 and 1b to the 6.0.0 branch, then increment 1c (#260) and increments 2-5. Increment 1b is committed (77cf67ac, 1b860b49, 8621d2d5, a821c6b7, 087e00a8) and reviewed; it ships in 5.22.3 if publish Gate 2 (full suite) and Gate 3 (validate_plugin --strict) pass. Findings (each labelled measured, read from code, or inferred): Before 1b (base 5.22.2): Cisco's filter predicate returns True for a payload path under a reports, build, dist, vendor, env or tests parent (measured), and Cisco supplies absolute paths (read from code, cpv_skill_scanner.py:349); the native SkillAudit walker gives 0 findings under a .cache parent even when tracked, and under reports, build, dist, .local, vendor or env when not a git repo (a tests parent kept its 2 findings); trufflehog reports absolute file paths (measured with the real binary) and cc-audit and tirith pass absolute paths (read from code), so all three hand absolute paths to the same predicates; the trufflehog drop under a tests parent was measured with the real binary (base 0 results, fixed 1, control 1; reports/cpv-hardening-inc1b/20261022_033055+0200-prepub-checks.md) while the cc-audit, tirith and Cisco drops stay inferred; a docs/documentation parent or a README/LICENSE-style name demotes trufflehog findings one tier, while a tests or fixtures parent drops them earlier through the test-path skip (inferred from the regexes, not executed; the fixed tree's real-binary result under a tests parent was MAJOR, not demoted). Related card on the 6.0.0 branch: TRDD-O3K5H8GG (integrity check). Release lineage: master (hotfix clone), port to 6.0.0 pending.
- Measured facts 2026-10-22: (1) removing the name-only skip cost 0 findings on real root manifests (reports/cpv-hardening-measure/20261022_023733+0200-skip-removal-and-dev-path.md); (2) issue #259 CONFIRMED: _is_dev_scratch_path (validate_security.py, line 1601 in this lineage) matches /reports_dev/ and /reports/ against the ABSOLUTE path, so a plugin installed under such a directory loses SkillAudit findings - increment 1b; (3) the Claude Code installer fetches git submodules: a plugin installed from a git marketplace source has its submodule directory populated with a gitlink .git file, so submodule content is installed and must be scanned - increment 2; local-directory relative-path plugins load in place (docs).

## Owner policy (verbatim)
2026-10-21: "ok. i mean: exceptions can be defined in the cpv plugin. but they must not be defined by the scanned plugin or passing argument, parameters, config or env vars when launching the validation or running the cpv scan. in no case an user should be able to "customize" the validation rules. those must be fixed and be defined in the plugin. this is why the plugin does not run if the hash is wrong: to ensure its integrity (and therefore the integrity of rules, exceptions, etc. defined in it by the cpv creator). for example the cpv plugin can make exceptions for commands like "claude setup-token" that mint a long lived oauth token, since that is necessary to skills that launch headless claude code sessions in background. but that is a decision by the cpv plugin author, not something that the  scanned plugin can define. the cpv plugin must not accept anything that changes its behaviour (except the few options we already defined, like strict or not, etc.). skipping the hash check is also necessary on the case of plugin development, since while you develop the plugin you cannot update the github repo all the time with the new hashes. but those are few exceptions. in general nothing external should change the plugin behaviour or bypassing/lowering/skipping the validation rules."

2026-10-22: "yes, you are right. those settings can be exploited to make the scanner validate something not valid or not safe. make those options less flexible, so they would not put at risk the validation reliability."

2026-10-22: "and exclude paths or url allowlist must also be not configurable by the user. the plugin is what is git tracked, so if it is git-tracked, it must be scanned and judged. the claude code installer does not install what is not git tracked or what is git ignored. but beware that gitignore can not be enough, because some file can be added to git tracking forcefully, even against the gitignore. and that itself is an issue that the cpv plugin must flag as error. the scanner must scan all files that are going to be installed into claude code. so everything that is git tracked must be scanned, no exceptions."

2026-10-22: "the command to mint the token must become a warning if it is inside a code block that is preceded by the warning phrase. check the details of my decision on the just opened issue on the cpv github repo." Summary of issue #260 written by Claude (not owner text): CLAUDE_CLI_TOKEN_THEFT inside a fenced block whose nearest non-blank line above the opening fence is exactly "Warning: the following code could be malicious. Audit it for safety before executing it!" is reported as WARNING (visible, not publish-blocking); without the sentence it stays a NIT that blocks --strict; inline and prose mentions are out of scope.

2026-10-22: "check also another issue on the cpv plugin github repo, just opened. solve that urgently. and publish the plugin asap."

## Rules derived from the policy
- Scan set = every file that will be installed. For a git source: the tracked files of the repo and of every submodule, recursively (`git ls-files --recurse-submodules`), plus untracked files that are not ignored (they ship once committed), with no path, name or setting exemption. A folder that is not a git work tree: every file. A plugin loaded in place from a local folder can also load ignored files; CPV judges the set a git install delivers, as the owner stated.
- A tracked file that .gitignore would exclude (force-added) is a blocking error.
- Not honoured from the scanned plugin: `cpv.exclude_paths`, `cpv.strip.require_url_allowlist`, `cpv.strip.allowed_submodule_urls`, `cpv.canon: "none"`, `.cpv-no-upstream-check`, `.mega-linter.yml` probe gating, `.gitignore` as scan scope, consent registry and in-document consent markers (printed, never lowering a finding), security-scanner ignore files and inline disable comments.
- Not honoured from the environment or command line: skip switches (removed); timeouts and size caps may only widen; a timed-out check or oversize file blocks under `--strict`; scanner install opt-outs stay but a missing scanner blocks under `--strict`. Allowed: `--strict`, the development integrity skip, and the scanner-install opt-outs (consent to install software); any other existing option the owner wants kept must be named by the owner.
- Exceptions live only in CPV's code, written by its author.
- Context-based severity inside CPV (documentation versus instruction-loadable content) is judging, not skipping, and stays allowed.

## Increments (each: own proposal, review, test run, commit)
5.22.3 (plugin-controlled inputs):
1. C10 name-only skip removed (scan the manifests; fix any detector false positive on hex digests instead of skipping) + C21 Cisco output to a unique temp dir outside the tree, never read or delete a file in the target.
1b. Location independence (issue #259, broadened): every path-keyed exemption and demotion is judged on the plugin-relative path; a path that cannot be placed inside the plugin gets no exemption and no demotion. Folder names INSIDE the plugin keep today's behaviour until increment 2.
1c. Issue #260: honour the #101 warning sentence for CLAUDE_CLI_TOKEN_THEFT inside a fenced block (WARNING instead of NIT); the mechanism is chosen after measuring what membership in scripts/cpv_skillaudit_native.py `_EXECUTION_CLASS_RULES` implies. The sentence never lowers any rule CPV does not list.
2. Scan set = installed set; force-tracked ignored file = error; path-name exemptions (scratch-dir, vendored, test-path) no longer skip tracked files; issue #259 `*_dev` path lead. Increment 2 must ship together with `!` negation lines in CPV's own .gitignore and the generator's .gitignore template for intended force-tracked markers, or CPV's own validation goes red. Record for increment 2 the CHECK-36 contradiction: its prose says .cpv-self-hashes.json should be gitignored, but CPV tracks it.
3. Plugin self-exemptions above stop being honoured ("no longer honored" warning, non-blocking). Candidates, to be classified as plugin-controlled exemption versus CPV-authored judging before any removal: the universal parametrize-body line skip (`is_test_file_parametrize_body`, validate_security.py ~1730, applied to every plugin's test files, not gated on self-scan), the FP-corpus whole-file skip (`is_fp_corpus_markdown`, keyed on folder name plus a content marker in the first lines), cc-audit's drop of every finding in non-instruction markdown (~7254-7263), and the content-keyed line skips `_line_is_pattern_definition` / `_line_is_status_report_message` (~7272/~7281).
4. Consent registry (.cpv-audit-consent.json) and every in-document consent marker other than the #101 sentence are printed only and never lower a finding; the #101 sentence lowers only the rules CPV itself lists.
5. Oversize and timeout = blocking under `--strict`; security-scanner config isolation (verify each tool's behaviour and flag first).
Release plan after the owner's 2026-10-22 "publish the plugin asap": 5.22.3 ships increment 1 (ce0ed7f1) and increment 1b only if 1b lands green and reviewed first. Claude's decisions, not owner text, reported to the owner: the version is a patch number (the "Version numbers" pending decision is settled this way for this release only, unless the owner asks for a minor version before the tag is pushed), and 1c (#260) plus increments 2-5 move to the next release. Claude's reading of "asap" is that it waives no gate: full --strict self-validation with every external scanner installed (covering 1b if it ships), manifest regeneration covering the new test files, publishing only through scripts/publish.py with the self-integrity check on, and only from the master-lineage hotfix clone, never from fix/plugin-issues-240-20261014.
Pre-publish gates added from the review of 8536b988: test_real_root_manifest_produces_no_findings fails with a clear message instead of FileNotFoundError when .plugin-self-hashes.json is absent; the "(increment N)" note lines whose increment is in the tag go into the GitHub release body right after publish (amended 2026-10-22 by Claude, disclosed to the owner: publish.py Gate 9 builds both CHANGELOG.md and the release notes with git-cliff from commit subjects only, so the CHANGELOG carries the fix commit subjects and cannot carry those lines without fabricated commits); the publish does not fix #261 (the consumer's pin bump does); the owner was told so on 2026-10-22 (done). Added from the review of 4703343a: the removed and rewritten scanner finding filters (trufflehog, semgrep, cc-audit, tirith, Cisco) have filter-level tests, namely (the grouping and test names below are Claude decision on wording, not a review requirement): judge unit tests test_judge_does_not_exempt_payload_under_any_parent, test_relative_climbing_path_is_not_placeable, test_backslash_paths_are_normalised; routing tests test_security_chains_call_no_path_predicate_directly, test_run_skillaudit_native_calls_no_path_predicate_directly; severity tests test_effective_severity_path_comes_from_the_judge, test_judged_path_under_tests_parent_is_not_demoted, test_effective_severity_empty_path_never_demotes; and filter_scannable_files tests across root spellings, including the shipped-surface (gitignored-and-untracked) skip, a force-added tracked file under an ignored dir (must be scanned), and a plugin in a subdirectory of its git repo. SATISFIED by commits 77cf67ac, 1b860b49, 8621d2d5, a821c6b7, 087e00a8.
(increment 1) Release note for 5.22.3: see the leftover .cpv-cisco-scan.json item under "Increment 1 review findings".
5.23.0 (environment, flags, stored state): skip switches removed, widen-only budgets, publish guard blocks CPV_/PLUGIN_ names containing SKIP, stored scan state keyed on content + CPV version + scanner-source hash, generator and CI templates changed in the same release, precision fixes for false positives (#259 SHELL_EXEC multi-line argv and CLAUDE_CLI_TOKEN_THEFT prose, #253, #248, #236) before the self-exemptions are removed.

## Pending owner decisions
- Style linters and spelling word lists: ignore the plugin's own settings too?
- Version numbers: security fixes as patches despite breakage, or minor bumps. For 5.22.3 Claude chose a patch number under the owner's "publish asap" directive and told the owner; still open for later releases.

## Increment 1 review findings (ce0ed7f1)
- Read 2026-10-22: the whole-file self-scan skip `cpv_self_scan_skip` needs a SHA-256 match against CPV's trusted manifest (the running CPV's own local manifest, or GitHub's canonical one for a non-running target that claims to be CPV), validate_security.py ~1819-1870 and ~1395-1480; the content-keyed line skip in `cpv_self_scan_skip_line` is gated on `_CPV_IS_RUNNING_CPV`, a path comparison (~1753-1810). The whole-file skip trusts the running CPV's local manifest only because that CPV's integrity was verified at startup, which holds only with the development integrity skip off; the line skip's gate is a path comparison and holds regardless. NOT gated: the parametrize-body branch of `cpv_self_scan_skip_line` runs for every plugin (increment 3).
- Gate before publishing 5.22.3: a full --strict self-validation of this tree with every external scanner (trufflehog, semgrep, cc-audit, tirith, Cisco, Snyk) installed, since the root manifest (about 1,300 sha256 hex strings) is now fed to them and only the in-process scanner was measured. Fix any false positive in the detector, never by restoring the skip.
- (increment 1) Release note for 5.22.3 must say: a leftover .cpv-cisco-scan.json written by 5.22.2 or earlier is now scanned like any other file and will fail validation; delete it. (The commit message's "nothing left for it to protect" overstated; the CHANGELOG entry and release note must state it correctly.)
- tests/test_n4glzxz6_no_name_skip.py::test_real_root_manifest_produces_no_findings reads REPO_ROOT/.plugin-self-hashes.json and raises FileNotFoundError when that file is absent (sdist/wheel); make it fail with a clear message. Also the manifest must be regenerated in the release flow so the new test file's payload strings are covered by the self-scan hash skip.
- Increment 2: scripts/cpv_skillaudit_native.py `_ALWAYS_SKIP_DIRS` (~3821, applied ~4121) skips by directory name "tracked or not": .git .hg .svn .trashcan .venv venv env node_modules vendor __pycache__ .mypy_cache .pytest_cache .ruff_cache .tox .cache. A tracked payload under vendor/, env/ or .cache/ escapes SkillAudit. Same class as the scratch-dir, vendored and test-path exemptions.
- Increment 5: audit validate-only code for any write under plugin_path (scan cache, resume sidecar, external scanner caches/config).
- (unverified) No test covers the removed finding filters in check_trufflehog, check_semgrep, check_cc_audit and check_tirith_scanner (they need the real binaries); add filter-level tests where a filter function exists.
- `_normalize_to_relpath` (used by cpv_self_scan_skip and _external_finding_is_gitignored) resolves symlinks; increment 1b's new helper is lexical. Increment 2 should move the gitignore filter onto the new helper so one relative-path rule exists.
- (increment 1b, only if 1b is in the tag) Release note for 5.22.3 must also say: a plugin whose parent folders are named tests, build, reports, vendor, env, .cache and similar now gets the findings (and severities) that were silently dropped or demoted before.

## Issue #261 (2026-10-22)
- Reproduced by the reporter on CPV v5.16.2, the version ai-maestro-janitor pins. Already fixed since v5.22.1 (issue #243, wheel mode): a package install verifies only the files it ships, each against the manifest of its exact release tag; skip variables are ignored there.
- Measured 2026-10-22: `uvx --from git+https://github.com/Emasoft/claude-plugins-validation@v5.22.2 --with pyyaml cpv-remote-validate plugin <minimal plugin>` with PLUGIN_SKIP_GITHUB_INTEGRITY and CPV_SKIP_GITHUB_INTEGRITY unset ran past the integrity gate; the verifier run inside that environment printed "[CPV integrity] OK — 170 files of the claude-plugins-validation v5.22.2 package verified against the GitHub tag manifest".
- Fix is on the consumer side: bump the pin to v5.22.2 and drop PLUGIN_SKIP_GITHUB_INTEGRITY=1. The ai-maestro-janitor session was messaged to do so and to report a --strict result. No CPV release is needed for #261. Commenting on or closing #261 on GitHub waits for the owner's go-ahead.

## Evidence (gitignored reports in the main checkout; local, not shipped)
- reports/cpv-external-overrides-audit/20261022_022240+0200-external-overrides-audit.md
- reports/cpv-open-issues-triage/20261022_022850+0200-open-issues-triage.md
- reports/cpv-hardening-measure/20261022_023236+0200-measure-increment1.md

## Approval log

- 2026-10-22T02:37:19+0200 — MANDATE issued by main-agent@complete-plugin-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- 2026-10-22 — The owner's 2026-10-22 messages quoted verbatim under Owner policy are the authority for this epic (the no-external-exceptions policy, the removal of configurable exclusions, and the instruction that every git-tracked file is scanned).

## Known gaps (fail toward scanning)

- (2b) Relative paths passed to filter_scannable_files are not placed, so name exemptions and the shipped-surface skip do not apply to them. Current callers pass absolute paths (read from code; caller: cpv_agent_security._filter_groups, scripts/cpv_agent_security.py:654).
- (2c) The reverse symlink spelling (root resolved, file given through the link) is not placed, so the file is scanned (inferred from code).
- Note: 8621d2d5 makes the shipped-surface skip apply under the resolved-root spelling too, so a scan exclusion is widened. It is safe because the skipped set is `git ls-files --others --ignored --exclude-standard --directory` run in the plugin dir (gitignored AND untracked, plugin-relative). Read from code at scripts/cpv_validation_common.py gitignored_unshipped_paths, pinned by a821c6b7.

## Deferred to 5.23.0

- RC-39 persistence fires on detector needles and prose/comments/docstrings with no pattern-source predicate (RC-65 has _rc65_is_pattern_source); the janitor session's #261 report showed most of its RC-39 hits were comments, docstrings and descriptions. Add a pattern-source and prose precision fix. (Source: the ai-maestro-janitor session report, inferred from its counts, not re-measured here.)

## Incident

- A worker spawned for increment 1b entered the git guard's OTP itself. Reported to the owner; every later worker prompt forbids entering a guard's password, OTP or confirmation.
2026-10-22: the v5.22.3 push closed issue 261 at 01:55:15Z as completed, because the body of commit 4703343a contains the phrase 'does not' followed by a closing verb and the issue number, which GitHub's closing-keyword parser matches regardless of negation. A second commit body in the range has the same phrase. Reopening was denied to Claude by the auto-mode classifier; the owner was told and asked to decide on reopening and on a comment. Proposed guard (owner to decide): a pre-push check over the pushed commit messages for GitHub's closing-keyword grammar.

## Release 5.22.3 (2026-10-22)

Published by scripts/publish.py --patch from the hotfix clone: release commit d0c74c49, tags v5.22.3 and claude-plugins-validation--v5.22.3. Gates 1-15 passed (Gate 0 silent on pass; Gate 4 not applicable, layout A); Gate 2 14650 passed, 24 skipped; Gate 14 CI green; Gate 15 clean install. Measured from the publish log.
Not verified: whether each external scanner actually ran inside Gate 3; the log does not name them.
Release body edited twice with gh release edit to add the 'Behaviour changes to know about' section; the second edit corrected an overclaim (tracked files always scanned) and hedged the .cpv-cisco-scan.json line.
Installed copy 5.22.3 passes the integrity check (1297 files against the GitHub manifest); the check created an untracked .venv inside the installed plugin folder.
