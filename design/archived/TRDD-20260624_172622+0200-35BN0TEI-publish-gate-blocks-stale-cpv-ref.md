---
trdd-id: 35BN0TEI
title: Publish gate blocks a stale CPV git ref — fold CIP-6 into validate_plugin (interim) + PyPI migration (follow-on)
column: published
created: 2026-06-24T17:26:22+0200
updated: 2026-06-24T19:44:50+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 0
severity: HIGH
effort: M
labels: [ci-green, publish-gate, cip6, cpv-ref, pypi, fleet-blocked]
task-type: bugfix
parent-trdd: TRDD-HZSI0BZ6
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
merge-strategy: squash
test-requirements: [unit, lint, typecheck]
audit-requirements: [security-scan]
review-requirements: [code-review]
runtime-targets: [macos, linux]
impacts: [ci-pipeline]
external-refs: []
attempts: 1
implementation-commits: [139edc5, 56bb277]
published-version: 2.148.0
published-at: 2026-06-24T18:09:19+0200
---

# TRDD-35BN0TEI — Publish gate blocks a stale CPV ref (interim) + PyPI migration (follow-on)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-24

**User report (verbatim):** "why the hell the cpv upgrade agent is failing to
upgrade the plugins? … i got the whole plugins fleet blocked." Then: "maybe we
should not host the validation script on github. Is there some other place where
to host python scripts executable remotely with uvx?" — **user chose "PyPI +
interim gate".**

**ROOT CAUSE (verified from source, not assumed):** the dominant fleet-blocking
failure is a plugin whose `.github/workflows/*.yml` pins
`git+https://github.com/Emasoft/claude-plugins-validation@main` (an OLD CPV ≤v2.137
migrated it; CPV's default branch is `master`, so `@main` 404s → `uvx --from
git+…@main` fails `Git operation failed / Updating … (main)` → red CI forever).
v2.147.0 shipped a CIP-6 detector + a `repin_stale_cpv_ref` — but BOTH live OUTSIDE
the publish gate:
- `validate_plugin` (the `--strict` gate that `publish.py` Gate 3 runs) has NO
  stale-CPV-ref rule (verified: grep found none).
- `publish.py` NEVER runs `ci-preflight` where CIP-6 lives (verified: grep found none).
So a `@main` workflow sails clean through publish → pushed → red CI. CIP-6/repin
only fire if an agent SEPARATELY remembers to run `ci-preflight` / `standardize
--fix` — which is exactly the "remember to" step that gets skipped. My v2.147.0
"DELIVERED" was over-claimed (detector built, gate enforcement missing).

**THE FIX — two parts, this TRDD tracks BOTH:**

- **INTERIM (this ship, no PyPI dependency) — fold the stale-CPV-ref rule into the
  publish gate.** New `validate_workflow_cpv_ref(plugin_root, report)` in
  `validate_plugin.py`, registered in the validator list (~line 7393), scanning
  `.github/workflows/*.yml|*.yaml` for
  `git+https://github.com/Emasoft/claude-plugins-validation[.git]@<ref>` and firing
  **MAJOR** (blocks under `--strict`) when `<ref>` is NOT resolvable (anything but
  `master` / a `v<semver>` tag / a 7-40 hex SHA). Reuses the EXACT CIP-6 rule
  (valid = master|v-semver|sha), kept self-contained per the established
  standardize↔CIP-6 "share by construction, not by import" pattern (a 3rd copy with
  a SYNC comment listing all three locations; SSOT consolidation into one shared
  helper is a noted follow-up). Gates on workflow CONTENT, never the install slug
  (compatible with the uninstalled-plugin rule). FAIL-SAFE, two-sided, re2-safe.
  Now `publish.py` Gate 3 REFUSES to ship a `@main` pipeline; the fix path (the
  existing `repin_stale_cpv_ref` under `standardize --fix`) makes re-validate pass.

- **FOLLOW-ON (after PyPI live — user-gated) — switch canon templates git→PyPI.**
  CPV is already built for PyPI: `.github/workflows/publish-pypi.yml` uses Trusted
  Publishing (OIDC), DORMANT behind `vars.PYPI_PUBLISH_ENABLED == 'true'` (the
  "PyPI skipped" in release logs); PyPI name `claude-plugins-validation` is FREE.
  USER does the 3-step setup (pending publisher at pypi.org → `pypi` GH env → `gh
  variable set PYPI_PUBLISH_ENABLED --body true`). THEN (CPV-side, me) switch the
  canon workflow templates (`generate_plugin_repo.py` + `_FORCE_TEMPLATE_FILES`)
  from `uvx --from git+…@<ref>` to `uvx --from claude-plugins-validation
  cpv-remote-validate …`, and update the repin to rewrite git→PyPI form. STRICT
  ORDERING: PyPI must be live before the template switch (else new plugins
  reference a nonexistent package). This removes the entire git-ref failure class.

**IMMEDIATE FLEET UNBLOCK (works today, any path):** per stuck plugin,
`uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml
cpv-remote-validate standardize <plugin> --fix` repins `@main`→`@master`, then
commit+push the workflow change.

**INTERIM GATE — DONE + VERIFIED (2026-06-24T17:39):** `validate_workflow_cpv_ref`
implemented in `validate_plugin.py`, registered in the dispatch list (~line 7395).
18 two-sided unit tests + the registration guard ALL pass; 52 sibling tests
(CIP-6 + repin + canon-fp) still green; ruff + mypy clean. E2E through the REAL
umbrella `validate_plugin … --strict` (with `PLUGIN_SKIP_GITHUB_INTEGRITY=1`):
exit 2 (blocked), fires `[MAJOR] non-resolvable CPV ref @main … release.yml:7`,
and flags ONLY the `@main` workflow, not the sibling `@master` one (two-sided in
the real flow). The finding message + docstring were scrubbed of the (now-dropped)
PyPI suggestion — it points only at the real fix (`standardize --fix` repins to a
`@v<semver>` tag).

**PyPI FOLLOW-ON — CANCELLED (USER decision, 2026-06-24):** the user is right —
CPV-the-plugin is marketplace-only (you cannot install the Claude plugin from
PyPI; nobody proposed to). PyPI would only ever have been a fetch *source* for the
`cpv-remote-validate` CLI that downstream CI runs via `uvx --from …`. But the
generator ALREADY pins downstream to a stable VERSION TAG `@v<CPV-version>` (since
issue #139 — `_default_cpv_ref()`), which is as reproducible as a PyPI version and
always resolves. PyPI adds nothing but a marginally faster fetch — not worth the
setup or the conceptual confusion. So the COMPLETE fix needs ZERO PyPI: (1) the
generator emits `@v<version>` (since #139), (2) the repin rewrites legacy `@main`→
`@v<version>`, (3) THIS interim gate blocks any lingering `@main` at the publish
gate. `publish-pypi.yml` stays DORMANT; no template switch needed.

**DONE — SHIPPED v2.148.0, CI GREEN (2026-06-24T18:09):** commits 139edc5 (fix +
tests + docs) + 56bb277 (release bump). publish.py all 13 gates passed; pushed +
tagged + GitHub release created. CI / Release / Notify Marketplace all
`completed/success`; Publish-to-PyPI `skipped` (dormant, as expected). Column →
`published`.

**OPEN (USER's call, NOT autonomously actioned):** the non-default
`--cpv-source pypi` generator option emits a `uvx --from
claude-plugins-validation==<ver>` pipeline that 404s today (CPV isn't on PyPI).
It is opt-in, fully built + unit-tested (ready to flip on when PyPI goes live),
and guarding-vs-enabling it is a distribution-strategy decision for the USER —
deliberately left untouched. The git default (`@v<version>`, resolvable since
issue 139) needs zero PyPI, so the fleet-blocker is fully resolved without it.

## Verification gates
- Two-sided: FIRES MAJOR on `@main`/`@develop`/`@HEAD`/`@feature-x`/`.git@main`;
  PASSES (zero findings) on `@master`/`@v2.147.1`/`@v…-rc.1`/7-hex/40-hex SHA/
  no-`@`-ref/PyPI `--from claude-plugins-validation` form.
- MAJOR severity confirmed → blocks `validate --strict` (and thus publish.py Gate 3).
- re2-safe (no lookaround); gates on workflow content, not the install slug.
- ruff + mypy clean; cache-cold (`CPV_SCAN_CACHE=0`) self-validate 0/0/0/0.
- CPV's own CI + Release green after publish.
- NO gate relaxed (strictly ADDS a blocking check).
