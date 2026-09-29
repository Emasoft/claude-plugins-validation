---
trdd-id: 3R4KYH6R
title: Bare-CLI importability rule for cpvppc entry points + sidecar symlink resolution check
column: backburner
status: tasked
created: 2026-09-29T02:56:53+0200
updated: 2026-09-29T03:15:16+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-29T02:56:53+0200
---

# Bare-CLI importability rule for cpvppc entry points + sidecar symlink resolution check

## Approval log

- 2026-09-29T02:56:53+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Problem

Review R12 carries: (a) no test pins every cpvppc entry module bare-CLI importable (P2 ModuleNotFoundError was suite-masked; found by ad-hoc smoke) — add parametrized import test incl. P3 adapters. (b) Sidecar hooks-path gate symlink resolution unverified (v5.16.2 resolved-path lesson): in-plugin symlink pointing outside could get read/linted. Verify, fix if unresolved. (c) Interpreter-form relative-token MINOR is a new finding surface — record as retro-break in release notes. (d) DONE: Q1 doc cites recorded at change site (hooks.md:416/:601); tolerated-unknown-key probe clean (retained keys silent).

## R13 dispositions

Q2 ANSWERED by live probe: a genuinely-unknown plausible key (telemetryRetentionPeriod, invented, absent from the live settings-reference fetched this session) correctly draws the INFO — the detector fires on the unknown stratum and is silent on known/retained. The CC-tolerates-but-CPV-doesnt-know residue is unenumerable by definition; INFO visibility is the accepted typo-detector trade. Q1 fixed: comment now names the live re-derive URL. Q3b noted (card bundling repeat).

## R14 dispositions

Q1: cite stays line-anchored but the QUOTED FRAGMENTS are the durable anchors (already in the comment); at next touch of the file, drop the line numbers or mark them point-in-time. Q2: headline overreach accepted — probe proves the sampled member fires, not the stratum (body text already says this); severity-bounded regardless (INFO never blocks). Q5b: the interpreter-form MINOR retro-break (card item c) is a RELEASE-NOTES obligation for THIS publish — surfaced here so the notes author sees it: bash hooks/x.sh interpreter-form relative tokens newly draw a MINOR (was invisible before fa0a8982).

## R15 disposition

Q1/Q4 accepted: a backburner card cannot bind the publish step; the retro-break note now lives in the session handoff checklist (item 9) — the surface the publish step reads. Card keeps the record; handoff carries the obligation. Q3 noted: disposition-ledger accumulation is a recurring shape — future review rounds get their own cards, not appends.

## R16 disposition

Q5a accepted as THE binding action: before publish, write BOTH changelog lines directly into CHANGELOG.md (Unreleased) or verify git-cliff emitted them from fa0a8982's message — (1) MINOR retro-break: interpreter-form relative hook tokens (bash hooks/x.sh) newly draw the relative-path MINOR (was invisible); (2) FP fix, reverse direction: bare relative tokens (./hooks/pre.sh) STOP drawing Script-not-found MAJOR when the file exists in-plugin. Q2 folds in: the FP fix gets its own explicit line, not a clause. Handoff item 9 remains the reminder; CHANGELOG is the binding surface.

## R18 disposition (VERIFIED BY RENDER)

Read cliff.toml + Gate 9 (the missed fact): filter_unconventional + message=subject means git-cliff renders commit SUBJECTS only; a hand-written Unreleased block is destroyed at Gate 9. Actions taken: dead block reverted (73f89444); empty carrier commit 80f87de1 whose SUBJECT is the changelog-ready wording for both hook changes; git-cliff --bump DRY-RUN executed — the 5.22.0 section renders and contains the retro-break line. Carrier chain closed with an executed render, not prose.

## R19 disposition (PARTIAL CLOSED)

Q1: Gate 9 argv read = [cliff_bin, --bump, --tag <tag>, -o CHANGELOG.md], no --config, repo-root cwd — same invocation shape as the dry-run; divergence risk closed. Q3/Q5a: fix(hooks)-prefixed empty carrier commit mints the RETRO-BREAK line; dry-run 2 confirms it renders under ### Bug Fixes in 5.22.0 (line 11). Accepted costs: the docs: carrier line double-lists (honest record), R17/R18 noise lines render (pre-existing repo pattern), 132-skip warning pre-existing. BREAKING marker not used: not a breaking API change, a tightening — the Bug Fixes placement + RETRO-BREAK wording carries it.

## R20 disposition (channel closed)

Release-notes surface closed from the record, not re-verified live: CLAUDE.md v5.3.0 records Gate 9/13's awk extractor takes THIS RELEASE'S SECTION ONLY from the freshly-rendered CHANGELOG.md (measured 277 chars) — the Bug Fixes line transitively reaches the GitHub release body. Q2 corrected: the R17/R18 noise lines are SESSION-AUTHORED this wave, not pre-existing convention — wrong to classify them as inherited. NIT adopted: empty subject-carrier commits are a sanctioned changelog channel; prefix semantically (fix:/feat:/docs:) — misuse guard.

## R21 wording corrections

STILL OPEN (the card's original deliverables — do not read 'channel closed' as card-wide): (a) bare-CLI importability test for cpvppc entry modules; (b) sidecar hooks-path symlink-resolution verification. Channel saga wording softened: closed per the v5.3.0 record; live-mechanism identity (awk extractor vs the --strip call) unverified, conclusion robust under EITHER extractor (both render commit subjects). Bump-coupling noted: final section header [5.22.0] vs other depends on publish-time bump — cosmetic.

## R22 disposition (saga closed on evidence)

Second git-cliff path EXERCISED with its exact Gate 9 argv (cliff --unreleased --tag v5.22.0 --strip all): the release-notes file contains 'hooks: Interpreter-form relative script paths newly draw the relative-path MINOR — RETRO-BREAK' at line 12 under Bug Fixes. Both render paths now proven by execution; 'either extractor' is no longer an assumption. Stopping rule adopted: future rounds only for defects in shipped artifacts, not card prose. Carrier-commit emptiness: sanctioned convention (R20 note). Next action on this card = WORK deliverable (a) bare-CLI import test or (b) sidecar symlink check, not review.
