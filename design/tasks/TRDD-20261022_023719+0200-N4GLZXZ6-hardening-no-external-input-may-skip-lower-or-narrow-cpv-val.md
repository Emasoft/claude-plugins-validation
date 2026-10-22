---
trdd-id: N4GLZXZ6
title: Hardening - no external input may skip lower or narrow CPV validation and every installed file is scanned
column: dev
status: tasked
created: 2026-10-22T02:37:19+0200
updated: 2026-10-22T02:48:39+0200
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
---

# Hardening - no external input may skip lower or narrow CPV validation and every installed file is scanned

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-10-22
Epic opened 2026-10-22. Increment 1 implemented and reviewed (C10 name-only skip removed at every use site; Cisco output in a temp dir outside the plugin; CPV no longer deletes a target-shipped .cpv-cisco-scan.json), pending commit. NEXT ACTION: increment 1b = issue #259 fix (`_is_dev_scratch_path` must match the plugin-relative path, not the absolute path; a plugin or marketplace named `reports` reaches it through the install cache path). Related card on the 6.0.0 branch: TRDD-O3K5H8GG (integrity check). Release lineage: master (hotfix clone), port to 6.0.0 pending.
- Measured facts 2026-10-22: (1) removing the name-only skip cost 0 findings on real root manifests (reports/cpv-hardening-measure/20261022_023733+0200-skip-removal-and-dev-path.md); (2) issue #259 CONFIRMED: _is_dev_scratch_path (validate_security.py, ~:1658 in the 6.0.0 tree; relocate in this lineage) matches /reports_dev/ and /reports/ against the ABSOLUTE path, so a plugin installed under such a directory loses SkillAudit findings - increment 1b; (3) the Claude Code installer fetches git submodules: a plugin installed from a git marketplace source has its submodule directory populated with a gitlink .git file, so submodule content is installed and must be scanned - increment 2; local-directory relative-path plugins load in place (docs).

## Owner policy (verbatim)
2026-10-21: "ok. i mean: exceptions can be defined in the cpv plugin. but they must not be defined by the scanned plugin or passing argument, parameters, config or env vars when launching the validation or running the cpv scan. in no case an user should be able to "customize" the validation rules. those must be fixed and be defined in the plugin. this is why the plugin does not run if the hash is wrong: to ensure its integrity (and therefore the integrity of rules, exceptions, etc. defined in it by the cpv creator). for example the cpv plugin can make exceptions for commands like "claude setup-token" that mint a long lived oauth token, since that is necessary to skills that launch headless claude code sessions in background. but that is a decision by the cpv plugin author, not something that the  scanned plugin can define. the cpv plugin must not accept anything that changes its behaviour (except the few options we already defined, like strict or not, etc.). skipping the hash check is also necessary on the case of plugin development, since while you develop the plugin you cannot update the github repo all the time with the new hashes. but those are few exceptions. in general nothing external should change the plugin behaviour or bypassing/lowering/skipping the validation rules."

2026-10-22: "yes, you are right. those settings can be exploited to make the scanner validate something not valid or not safe. make those options less flexible, so they would not put at risk the validation reliability."

2026-10-22: "and exclude paths or url allowlist must also be not configurable by the user. the plugin is what is git tracked, so if it is git-tracked, it must be scanned and judged. the claude code installer does not install what is not git tracked or what is git ignored. but beware that gitignore can not be enough, because some file can be added to git tracking forcefully, even against the gitignore. and that itself is an issue that the cpv plugin must flag as error. the scanner must scan all files that are going to be installed into claude code. so everything that is git tracked must be scanned, no exceptions."

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
1b. Issue #259 absolute-path scratch-dir match: `_is_dev_scratch_path` must match the plugin-relative path, not the absolute path.
2. Scan set = installed set; force-tracked ignored file = error; path-name exemptions (scratch-dir, vendored, test-path) no longer skip tracked files; issue #259 `*_dev` path lead. Increment 2 must ship together with `!` negation lines in CPV's own .gitignore and the generator's .gitignore template for intended force-tracked markers, or CPV's own validation goes red. Record for increment 2 the CHECK-36 contradiction: its prose says .cpv-self-hashes.json should be gitignored, but CPV tracks it.
3. Plugin self-exemptions above stop being honoured ("no longer honored" warning, non-blocking).
4. Consent registry / in-document consent markers print only.
5. Oversize and timeout = blocking under `--strict`; security-scanner config isolation (verify each tool's behaviour and flag first).
Publish 5.22.3 only after increments 1 to 5 have all landed.
Release note for 5.22.3: delete leftover `.cpv-cisco-scan.json` files written by older versions.
5.23.0 (environment, flags, stored state): skip switches removed, widen-only budgets, publish guard blocks CPV_/PLUGIN_ names containing SKIP, stored scan state keyed on content + CPV version + scanner-source hash, generator and CI templates changed in the same release, precision fixes for false positives (#259 SHELL_EXEC multi-line argv and CLAUDE_CLI_TOKEN_THEFT prose, #253, #248, #236) before the self-exemptions are removed.

## Pending owner decisions
- Style linters and spelling word lists: ignore the plugin's own settings too?
- The `claude setup-token` exception: write it, and for documentation prose only or code too?
- Version numbers: security fixes as patches despite breakage, or minor bumps.

## Evidence (gitignored reports in the main checkout; local, not shipped)
- reports/cpv-external-overrides-audit/20261022_022240+0200-external-overrides-audit.md
- reports/cpv-open-issues-triage/20261022_022850+0200-open-issues-triage.md
- reports/cpv-hardening-measure/20261022_023236+0200-measure-increment1.md

## Approval log

- 2026-10-22T02:37:19+0200 — MANDATE issued by main-agent@complete-plugin-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- 2026-10-22 — The owner's 2026-10-22 messages quoted verbatim under Owner policy are the authority for this epic (the no-external-exceptions policy, the removal of configurable exclusions, and the instruction that every git-tracked file is scanned).
