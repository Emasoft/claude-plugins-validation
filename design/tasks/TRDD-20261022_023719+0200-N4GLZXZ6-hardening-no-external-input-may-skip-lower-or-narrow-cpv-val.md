---
trdd-id: N4GLZXZ6
title: Hardening - no external input may skip lower or narrow CPV validation and every installed file is scanned
column: dev
status: tasked
created: 2026-10-22T02:37:19+0200
updated: 2026-10-22T02:37:19+0200
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
Epic opened 2026-10-22. NEXT ACTION: increment 1 (name-only skip C10 removed, Cisco output moved outside the scanned tree C21), waiting on a measurement of the false-positive cost of scanning the hash manifests and of issue #259's `reports_dev` path report. Related card on the 6.0.0 branch: TRDD-O3K5H8GG (integrity check). Release lineage: master (hotfix clone), port to 6.0.0 pending.

## Owner policy (verbatim)
2026-10-21: "ok. i mean: exceptions can be defined in the cpv plugin. but they must not be defined by the scanned plugin or passing argument, parameters, config or env vars when launching the validation or running the cpv scan. in no case an user should be able to "customize" the validation rules. those must be fixed and be defined in the plugin. this is why the plugin does not run if the hash is wrong: to ensure its integrity (and therefore the integrity of rules, exceptions, etc. defined in it by the cpv creator). for example the cpv plugin can make exceptions for commands like "claude setup-token" that mint a long lived oauth token, since that is necessary to skills that launch headless claude code sessions in background. but that is a decision by the cpv plugin author, not something that the  scanned plugin can define. the cpv plugin must not accept anything that changes its behaviour (except the few options we already defined, like strict or not, etc.). skipping the hash check is also necessary on the case of plugin development, since while you develop the plugin you cannot update the github repo all the time with the new hashes. but those are few exceptions. in general nothing external should change the plugin behaviour or bypassing/lowering/skipping the validation rules."

2026-10-22: "yes, you are right. those settings can be exploited to make the scanner validate something not valid or not safe. make those options less flexible, so they would not put at risk the validation reliability."

2026-10-22: "and exclude paths or url allowlist must also be not configurable by the user. the plugin is what is git tracked, so if it is git-tracked, it must be scanned and judged. the claude code installer does not install what is not git tracked or what is git ignored. but beware that gitignore can not be enough, because some file can be added to git tracking forcefully, even against the gitignore. and that itself is an issue that the cpv plugin must flag as error. the scanner must scan all files that are going to be installed into claude code. so everything that is git tracked must be scanned, no exceptions."

## Rules derived from the policy
- Scan set = every file that will be installed. For a git source: every tracked file, no path, name or setting exemption. A non-git folder: every file. (Pending verification of what Claude Code installs from local-folder sources and whether submodules are fetched.)
- A tracked file that .gitignore would exclude (force-added) is a blocking error.
- Not honoured from the scanned plugin: `cpv.exclude_paths`, `cpv.strip.require_url_allowlist`, `cpv.strip.allowed_submodule_urls`, `cpv.canon: "none"`, `.cpv-no-upstream-check`, `.mega-linter.yml` probe gating, `.gitignore` as scan scope, consent registry and in-document consent markers (printed, never lowering a finding), security-scanner ignore files and inline disable comments.
- Not honoured from the environment or command line: skip switches (removed); timeouts and size caps may only widen; a timed-out check or oversize file blocks under `--strict`; scanner install opt-outs stay but a missing scanner blocks under `--strict`. Allowed: `--strict`, the development integrity skip.
- Exceptions live only in CPV's code, written by its author.

## Increments (each: own proposal, review, test run, commit)
5.22.3 (plugin-controlled inputs):
1. C10 name-only skip removed (scan the manifests; fix any detector false positive on hex digests instead of skipping) + C21 Cisco output to a unique temp dir outside the tree, never read or delete a file in the target.
2. Scan set = installed set; force-tracked ignored file = error; path-name exemptions (scratch-dir, vendored, test-path) no longer skip tracked files; issue #259 `*_dev` path lead.
3. Plugin self-exemptions above stop being honoured ("no longer honored" warning, non-blocking).
4. Consent registry / in-document consent markers print only.
5. Oversize and timeout = blocking under `--strict`; security-scanner config isolation (verify each tool's behaviour and flag first).
5.23.0 (environment, flags, stored state): skip switches removed, widen-only budgets, publish guard blocks CPV_/PLUGIN_ names containing SKIP, stored scan state keyed on content + CPV version + scanner-source hash, generator and CI templates changed in the same release, precision fixes for false positives (#259 #253 #248 #236) before the self-exemptions are removed.

## Pending owner decisions
- Style linters and spelling word lists: ignore the plugin's own settings too?
- The `claude setup-token` exception: write it, and for documentation prose only or code too?
- Version numbers: security fixes as patches despite breakage, or minor bumps.

## Evidence (gitignored reports in the main checkout)
- reports/cpv-external-overrides-audit/20261022_022240+0200-external-overrides-audit.md
- reports/cpv-open-issues-triage/20261022_022850+0200-open-issues-triage.md
- reports/cpv-hardening-measure/20261022_023236+0200-measure-increment1.md

## Approval log

- 2026-10-22T02:37:19+0200 — MANDATE issued by main-agent@complete-plugin-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
