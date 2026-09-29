---
trdd-id: 3R4KYH6R
title: Bare-CLI importability rule for cpvppc entry points + sidecar symlink resolution check
column: backburner
status: tasked
created: 2026-09-29T02:56:53+0200
updated: 2026-09-29T02:57:09+0200
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
