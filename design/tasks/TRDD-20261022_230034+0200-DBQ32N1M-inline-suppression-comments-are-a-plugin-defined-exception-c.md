---
trdd-id: DBQ32N1M
title: Inline suppression comments are a plugin-defined exception channel
column: backburner
status: tasked
created: 2026-10-22T23:00:34+0200
updated: 2026-10-22T23:02:17+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-22T23:00:34+0200
parent-trdd: N4GLZXZ6
---

# Inline suppression comments are a plugin-defined exception channel

## Problem

Owner policy (verbatim): "exceptions can be defined in the cpv plugin. but they must not be defined by the scanned plugin or passing argument, parameters, config or env vars when launching the validation or running the cpv scan. in no case an user should be able to "customize" the validation rules." (excerpt)

A scanned plugin can still switch off a CPV-run linter or scanner on any line or file with an inline comment, since the canonical .ruff.toml and .bandit.yml (issue 254) make these CPV-owned rule sets. These are exceptions defined by the plugin. Found in the v5.23.0 review of issues 254 and 257.

This is a separate card because it is out of scope for v5.23.0. It is related to the hardening card, parent-trdd N4GLZXZ6.

## Suppression channels (to verify)

| scanner | inline syntax | file-level ignore | verified |
|---|---|---|---|
| bandit | `# nosec` | .bandit / baseline | no |
| ruff | `# noqa` / `# ruff: noqa` | per-file-ignores in config | no |
| mypy | `# type: ignore` / `# mypy: ignore-errors` | per-module config | no |
| shellcheck | `# shellcheck disable=` | .shellcheckrc | no |
| markdownlint | `<!-- markdownlint-disable -->` | .markdownlintignore | no |
| yamllint | `# yamllint disable` | ignore in .yamllint | no |
| cspell | `cspell:disable` | ignorePaths | no |
| jscpd | `jscpd:ignore-start` | .jscpd.json ignore | no |
| checkov | `checkov:skip=` | `.checkov.yaml` | no |
| trivy | none | `.trivyignore` | no |
| trufflehog | `trufflehog:ignore` | exclude-paths | no |
| CPV's own validators | any honoured marker (unknown, first task: grep CPV validators) | unknown | no |

## Candidate fixes

To evaluate, not decided. The canonical Mega-Linter config passes `--ignore-nosec` to bandit and the local gate matches it. CPV reports every inline suppression in a scanned plugin as a finding. Separate CPV-authored canonical files (which carry drift-checked suppressions) from plugin-authored code.

Note that `--ignore-nosec` also cancels any `# nosec` in CPV-authored canonical files, so candidates 1 and 3 interact. Candidate 3 complies with the policy only if those canonical suppressions are drift-checked byte for byte.

## Done when

Every row of the Suppression channels table shows ignored or reported, and each row has a test. The severity is fixed in CPV, and no flag, config or environment variable changes it. A parity test runs the local gate and CI's linter set on a fixture containing every suppression form, and both agree.

## Approval log

- 2026-10-22T23:00:34+0200 — created as backburner follow-up; not approved for work
