---
trdd-id: 0DWMU7BV
title: CPVPPC P6 - CI template
column: backburner
status: tasked
created: 2026-09-24T20:01:00+0200
updated: 2026-09-24T20:26:16+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:00+0200
---

# CPVPPC P6 - CI template

Files: templates/0.x/plugin/ci.yml. Key tests: gates 1-9 as jobs; per-target build on push/PR with SHA-pinned caches; permissions: {}; runner-label check; custom sandbox (a custom step writing publish.py fails). Done-when: actionlint clean; fixture matrix green. Plan: design/specs/cpvppc-plan.md section 6, row P6. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:00+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Plan excerpt: section 4.1 (verbatim)
### 4.1 Gate sequence
CI runs gates 1-9 on every push and PR (every declared target builds as a dry run: "test parity").
The release workflow (`workflow_dispatch`, triggered by `publish.py`) first runs step 0 — version bump
in every declared version file, the project's OWN version inside lockfiles only (e.g. `cargo update -p
<self> --offline`, `uv lock` for the root version), CHANGELOG by git-cliff, README sections,
`bin/manifest.json` — in its workspace; G-CONFIG asserts the step-0 diff touches only version fields
(a dependency change fails, so `--locked` / `npm ci` / `--frozen-lockfile` keep working); then gates
1-12 run on exactly that tree, and G-RELEASE commits exactly that tree. The latest-CPV re-scan makes a
release depend on the newest rules: a retry of the same release may need fixes if CPV changed. Local `publish.py` runs 1-9 as an advisory preflight
(`preflight.local`). Because pushes made with the workflow token trigger no other workflow, the
"green run" for a release commit is the release workflow run that produced it, and marketplace
notification is a job inside the release workflow (cross-repo dispatch uses `MARKETPLACE_PAT`).
| # | Gate | What it runs | Blocks on |
|---|---|---|---|
| 1 | G-CONFIG | config schema; lock consistent with config; managed files match their render; workflows free of bypass constructs (`continue-on-error` on a gate, `if: false`, `\|\| true`, `\|\| echo`, skip inputs, repo-variable reads); only the canon writer workflow has `contents: write`; `curl \| sh` absent from every executable and canon-managed file, including vendored ones | any failure |
| 2 | G-CPV | CPV pinned by commit SHA in the lock, installed from the official repo; `validate_plugin --strict` (marketplace: `validate_marketplace --strict`) on the plugin subtree; in the release workflow also a re-scan with the latest CPV release, which must be clean for a new release (so `publish.py` users and agents hit it alike) | CRITICAL/MAJOR/MINOR/NIT |
| 3 | G-UNITS | each unit's adapter lint, typecheck, test with CPV-controlled args; `quality` floors | any failure; skips/deselection above threshold |
| 4 | G-CUSTOM | `checks.custom[]` and docgen in a sandboxed job (argv only, read-only token, no secrets, network denied except declared hosts); managed files re-hashed after | non-zero exit; any managed-file change |
| 5 | G-BUILD | every target by its method on GitHub-hosted runners, network denied except declared hosts, fetched files hash-checked; outputs named by template; committed `dist/` rebuild-and-diff; reproducible vendor rebuild-and-compare; `local_release` targets are fetched from the staging draft and signature/hash-verified in the release run, and reported "verified at release time" on push/PR runs | any failure; any missing CI-built target |
| 6 | G-ASSEMBLE | release artifact = plugin subtree + built outputs + vendor outputs + external artifacts (hash-checked) + generated README sections; submodules recursed | hash mismatch; undeclared content; executable-type file outside every unit |
| 7 | G-SECURITY | CPV security scan (skillaudit, RC rules, external scanners) on the assembled artifact AND on the sources of `external-repo` units, reading no repo ignore/config; supply-chain and runtime-download checks; SBOM generated | any blocking finding |
| 8 | G-PRIVACY | home-directory paths (`/Users/…`, `/home/…`, `C:\Users\…`), git author names/emails of the release range, hostnames, email addresses; runtime hosts ⊆ declared; generated text (README sections, release notes, scan manifest) scanned; private targets never rendered into a public README; no allowlist from the repo | any finding |
| 9 | G-SECRETS | trufflehog, all result buckets, on the release range of history and the assembled artifact | any finding |
| 10 | G-ATTEST (release) | `SHA256SUMS`, scan manifest, SBOM; build provenance for every artifact and for those three files | failure |
| 11 | G-RELEASE (release) | the workflow commits the version bump + `bin/` (if `committed-bin`) + manifest + README sections as ONE release commit whose tree equals the scanned artifact, creates both tags, creates the GitHub release, uploads assets, sends notifications; idempotent on re-run | any error |
| 12 | G-POST (release) | remote verify: tags, assets hash-match, provenance, install smoke from each target marketplace, listing matches manifest | any failure (job red) |
Writers per repo kind: plugin repo → the release workflow (the only job with `contents: write`);
marketplace repo → the update workflow. Rulesets: CPV's ratified `baseline-*` trio on the default
branch plus tag protection (`v*`, `*--v*`: no update, no deletion), with the GitHub Actions app as the
only bypass actor.


