---
trdd-id: HZSI0BZ6
title: Make plugin agents publish without failing GitHub CI — ungate the safety net + CIP-6 stale-ref + Mega-Linter parity
column: dev
created: 2026-06-24T03:49:44+0200
updated: 2026-06-24T03:49:44+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 1
severity: HIGH
effort: L
labels: [ci-green, fixer-agents, ci-preflight, cip6, mega-linter, wiring]
task-type: bugfix
parent-trdd: null
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
external-refs: ["reports/ci-green-investigation/20260624_033206+0200-remaining-ci-failure-modes.md", "github.com/Emasoft/ai-maestro-orchestrator-agent/actions/runs/27940566923", "github.com/Emasoft/ai-maestro-maintainer-agent/actions/runs/27940763588"]
attempts: 0
implementation-commits: []
---

# TRDD-HZSI0BZ6 — CI-green: ungate the safety net + CIP-6 stale-ref + Mega-Linter parity

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-24

**User directive (verbatim, 2026-06-24):** "Be sure to make the plugin agents finally
capable of publishing a plugin without failing the ci runs on github everytime."

**Grounded diagnosis (read-only opus investigation, evidence-backed, report at
`reports/ci-green-investigation/20260624_033206+0200-remaining-ci-failure-modes.md`):**
the recurrence is **WIRING + SYSTEMIC, NOT template defects**. The generator
(`generate_plugin_repo.py`) is already correct — VERIFIED 0 `@main` emissions; the
`master` fallback (#139), version-pin (#2/#114), CIP-1..5 detectors, the
Test-matrix→aggregate-`Test` job, dev-extra (#142), notify guard (#9), jscpd (#143)
are all solid (do NOT re-touch). Probed 7 real adopters; every RED reduces to exactly
THREE causes:
1. **Stale `@main` CPV pin** (orchestrator run 27940566923: `Failed to resolve --with
   requirement / Git operation failed` `Updating …claude-plugins-validation (main)` —
   CPV's default branch is `master`, `@main` does not exist). The plugin was migrated
   by an OLD CPV (≤v2.137, pre-#139) and **never re-published**, so nothing re-pins it.
2. **Mega-Linter sub-linters CPV enables but `ci-preflight` cannot reproduce**
   (maintainer run 27940763588: `REPOSITORY checkov: 7 error(s)` + `trivy: 1 error` on
   Dockerfiles; claude-menu run 26320181237: `SPELL cspell: 45 error(s)`). CPV's
   `gen_mega_linter_yml` enables checkov/trivy/cspell/bandit/shellcheck/etc., but
   `cpv_ci_preflight.py` runs ONLY jscpd+actionlint+mypy+uv-sync+CIP-1..5 → the agent
   declares DONE on a clean preflight, publishes, CI fails on a gate it never saw.
3. **Leftover `validate.yml` alongside `ci.yml`** (CIP-4 shape; integrator's standalone
   "Plugin Validation" runs concurrency-`cancelled`). CIP-4 detects it; standardize's
   removal didn't run on that migration.

   Underlying both 1 and 3: **the green-CI loop + `ci-preflight` are wired but GATED** —
   plugin-fixer runs ci-preflight only "if a fix touched `.github/`/`pyproject`/`publish.py`"
   and the green-CI loop is "(migration ONLY)". So a normal publish-bound run never
   re-checks the plugin's actual deployed state.

**THE FIX (this TRDD) — four file-disjoint pieces, all STRICTLY TIGHTEN (add a detector,
ungate safety nets, add degrade-gracefully probes; ZERO gate relaxation, never suppress):**

- **F1 — CIP-6 stale/invalid CPV-ref detector** (`scripts/cpv_ci_parity_checks.py`):
  new `_check_stale_cpv_ref` registered in `check_ci_parity`. FIRES **MAJOR** when a
  `.github/workflows/*.yml` pins `claude-plugins-validation@<ref>` (in a `git+…@<ref>`
  / `uvx --from git+…@<ref>` form) where `<ref>` is NOT one of {`master`, a `v<semver>`
  tag, a 7-40 hex commit SHA}. STATIC + offline + FN-safe (a valid tag/SHA/`master`
  passes; `@main`/`@develop`/`@HEAD` fire). re2-safe. Auto-flows into `ci-preflight` via
  `_gate_static_checks` (MAJOR→FAIL). Update the module docstring to CIP-1..6. This alone
  catches the DOMINANT failure pre-publish.
- **F2 — ungate the safety net in the agents** (`agents/plugin-fixer.md`,
  `agents/marketplace-fixer.md`, `agents/plugin-creator.md`): run `ci-preflight` on
  EVERY publish-bound run (drop "if no fix touched those paths, skip"); make the
  publish→`gh run watch`→fix-cause→re-publish GREEN-CI loop UNCONDITIONAL whenever the
  agent publishes (drop "migration ONLY"); treat a `cancelled` required run as a failure
  to investigate (Mode 3). Prompt-only.
- **F3 — re-pin + unconditional validate.yml cleanup on migration**
  (`scripts/standardize_plugin.py` + the agent prompts in F2): VERIFY `standardize
  --force-templates` re-pins the CPV ref to `cpv_ref_resolved` (it rewrites the workflow,
  so it should — confirm; add a targeted re-pin only if a non-force path misses it);
  ensure the leftover-`validate.yml` removal runs. The agent (F2) re-pins + removes on a
  CIP-6 / CIP-4 finding.
- **F4 — Mega-Linter parity probes in `ci-preflight`** (`scripts/cpv_ci_preflight.py`):
  add degrade-gracefully gates for checkov, trivy, cspell, bandit, shellcheck (the
  evidence-backed ones) + shfmt/jsonlint/yamllint/markdownlint, EACH following the EXACT
  existing contract — tool on PATH → run + FAIL on a real error; tool ABSENT → WARNING
  (never false-block). Only probe a linter the plugin's generated `.mega-linter.yml`
  actually enables (read it). Closes the blind spot WITHOUT changing the default-enabled
  Mega-Linter set (never weaken security; the agent fixes the finding by adding its own
  skip-checks/.trivyignore, as maintainer-agent did).

**HELD as a follow-up TRDD (do NOT do tonight — needs care / a USER call):**
- **F5 (dogfood)** — a "canonical sample plugin" CI fixture whose CI runs the FULL
  generated pipeline (incl. Mega-Linter) so a generator-side gate regression is caught in
  CPV's own CI. Changes CPV's CI cost/shape (Docker, time) → its own TRDD.
- **Mode 2b (policy)** — whether `REPOSITORY_CHECKOV`/`REPOSITORY_TRIVY` belong in the
  DEFAULT enabled set, or ship the canonical `.mega-linter.yml` with documented Dockerfile
  skip-checks. A SECURITY-POSTURE decision — never relax silently; surface to USER.

**IMPLEMENTATION:** 4 file-disjoint opus agents in parallel (A: cpv_ci_parity_checks.py;
B: cpv_ci_preflight.py; C: standardize_plugin.py; D: the 3 agent .md). Then CENTRAL-VERIFY
(read every diff; the CIP-6 + F4 two-sided through the real preflight with malformed-ref +
real-linter-error siblings the agents did NOT enumerate); ruff + mypy; cache-cold
self-validate 0/0/0/0; a real `ci-preflight` dogfood on a scaffolded sample + on a
`@main`-poisoned sample (must FAIL CIP-6); update CLAUDE.md + README + help; ship via
publish.py; watch CI green.

**NEXT ACTION:** dispatch the 4 file-disjoint opus agents (prompts prepared), then
central-verify.

## Verification gates
- Two-sided tests for CIP-6 (F1) and each F4 probe (FIRES on the defect, PASSES clean).
- FN-safe + re2-safe (no lookaround) for every new regex.
- Degrade-gracefully for F4 (tool-absent → WARNING, never FAIL) — proven by tests.
- NO rule suppression / NO `--strict` relaxation / NO weakening of the default Mega-Linter set.
- `mypy scripts/ --ignore-missing-imports` + `ruff` clean.
- Cache-cold (`CPV_SCAN_CACHE=0`) whole-plugin self-validate stays 0/0/0/0.
- Real `ci-preflight` dogfood: a clean scaffolded sample → PARITY-CLEAN; a `@main`-pinned
  sample → CIP-6 FAIL (CI WOULD FAIL).
- CPV's own CI + Release green after publish.

## Grounding artifacts (read before acting)
- `reports/ci-green-investigation/20260624_033206+0200-remaining-ci-failure-modes.md` — the
  evidence (real run IDs + root-cause excerpts + the "already solid" do-not-touch list).
