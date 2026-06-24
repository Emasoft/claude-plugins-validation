---
trdd-id: 8eee537a-1381-437f-8c29-4295348c69da
title: Harden CPV fixer + upgrade agents — close the local-gate ↔ GitHub-CI parity gap (FM1) + wire green-CI/loop-state into the agents that lack them
column: published
created: 2026-06-21T21:47:27+0200
updated: 2026-06-24T03:27:35+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 2
severity: HIGH
effort: L
labels: [fixer-agents, upgrade-agents, ci-parity, canon-pipeline]
task-type: refactor
parent-trdd: null
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, lint, typecheck]
impacts: [ci-pipeline]
external-refs: []
implementation-commits: []
---

# TRDD-8eee537a — Harden CPV fixer + upgrade agents (CI-parity / green-CI / loop-state)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-21

**Current state:** Audit DONE (reports/fixer-upgrade-audit/20260621_214106+0200-gap-analysis.md), 8 grounded
gaps. **PHASE 1 DONE + shipping in v2.140.0** — `scripts/cpv_ci_parity_checks.py` (5 CIP static checks) +
`scripts/cpv_ci_preflight.py` + the `cpv-remote-validate ci-preflight <path>` subcommand; 34 two-sided tests;
`ci-preflight .` on CPV's own tree = EXIT 0 (no false-fire); ruff + mypy clean. **PHASE 2 DONE — shipping v2.141.0**
(3 file-disjoint opus wiring agents W1/W2/W3 + a naming-consistency sweep). All 8 gaps closed: CHECK-83..87 wired
into the migration matrix (82→87 checks / 17 categories, run via `cpv-remote-validate ci-preflight`); marketplace-fixer
green-CI loop (gap 3); cache-optimizer loop-state (gap 4); plugin-fixer non-migration preflight gate (gap 5);
markdown-poison guardrail in plugin-fixer + standardize-SKILL (gap 6); devitalizer/leaks FM1 cause pointer (gap 7);
fix-validation §6 (gap 8). CENTRAL-VERIFIED: self-validate --strict 0/0/0/0; contract + architecture-lock tests green.
User directive: "improve the fixer agents and the upgrade agents."

**Root cause (FM1):** the script layer (`generate_plugin_repo.py`, `standardize_plugin.py`) already
implements every #137-143 fix, but the AGENT/SKILL verification layer declares DONE on
`validate_plugin --strict` — which does NOT run jscpd, actionlint, mypy-strict, or `uv sync --extra dev`
— so an upgrade/fix that's locally clean still red-CIs. Agents reach real CI only on migration runs, and
even then the 82-check matrix has ZERO coverage of the 5 #137-143 defect shapes.

**The 8 gaps (from the audit, all grounded in real FMs):**
1. [HIGH] 82-check migration matrix has zero coverage of the 5 #137-143 CI-parity defects → new static checks + matrix CHECK-83..87.
2. [HIGH] No agent-runnable LOCAL CI-parity preflight → new `cpv-remote-validate ci-preflight <path>` (jscpd Gate-2b reuse + actionlint + mypy + dev-extra smoke + the 5 static checks). THE keystone.
3. [HIGH] marketplace-fixer has no green-CI loop → add publish→`gh run watch`→fix-cause loop (copy sibling block).
4. [MED] cache-optimizer-agent uses prose single-step heuristic, not `cpv_fix_loop_state.py` → wire it.
5. [MED] plugin-fixer §7d CI-green loop is migration-only → extend to non-migration runs touching workflows/pyproject/publish.py (run gap-2 preflight).
6. [MED] no markdown-poison guardrail in fixers/standardize → add the line-start `#`/`+ `/`* ` reword guardrail.
7. [LOW-MED] devitalizer/leaks-preventer green-CI loop has no FM1 cause recipe → add pointer.
8. [LOW-MED] fix-validation pipeline-migration index has no § for the 5 #137-143 defects → add §6.
NO FM5 (never-suppress) gaps — devitalizer/leaks-preventer are exemplary (do NOT touch their suppression discipline).

**PLAN (3 phases):**
- **Phase 1 (keystone, NEW scripts):** `scripts/cpv_ci_parity_checks.py` (the 5 static #137-143 detectors,
  each FN-safe two-sided) + `scripts/cpv_ci_preflight.py` (runs jscpd[reuse the publish.py Gate-2b
  probe-then-run], actionlint, mypy, `uv sync --extra dev` smoke, AND the static checks; degrades to
  WARNING when a tool is absent — NEVER false-fails) + a `ci-preflight` subcommand in
  `remote_validation.py`. Two-sided tests for every check. Exit 0=parity-clean, non-zero=a CI gate would fail.
- **Phase 2 (wiring, prompt/doc edits — gaps 1,3-8):** wire CHECK-83..87 into the migration matrix
  (calls cpv_ci_parity_checks); marketplace-fixer green-CI loop; cache-optimizer loop-state; plugin-fixer
  non-migration preflight gate; markdown-poison guardrail (plugin-fixer + standardize-SKILL);
  devitalizer/leaks-preventer FM1 cause pointer; fix-validation §6. File-disjoint opus agents.
- **Phase 3:** central-verify (serial suite + self-validate --strict 0/0/0/0 + mypy + ruff), update
  CLAUDE.md counts, regen self-hashes LAST, ship v2.140.0.

**`ci-preflight` CONTRACT (both phases agree on this):** `cpv-remote-validate ci-preflight <plugin-path>`
mirrors the parity gates GitHub CI's ci.yml Lint job runs that `validate_plugin --strict` does NOT:
(a) jscpd copy-paste (reuse the publish.py Gate-2b `--version` probe → degrade-WARNING if jscpd/npx absent,
BLOCK on over-threshold); (b) actionlint on `.github/workflows/*.yml` (degrade-WARNING if absent);
(c) mypy on `scripts/` (degrade-WARNING if absent); (d) `uv sync --extra dev` resolve smoke (catches #142
Defect-2 missing dev-extra); (e) the 5 static checks from `cpv_ci_parity_checks.py`. Tool-absent ALWAYS
degrades to WARNING (never false-blocks a fixer); a real over-threshold / static-defect / resolve-failure
is the only non-zero exit. This is the #129 degrade-gracefully pattern applied to the whole preflight.

**`cpv_ci_parity_checks.py` interface (Phase 1 must expose; Phase 2 matrix + ci-preflight call it):**
`check_ci_parity(plugin_path) -> list[ParityFinding(check_id, severity, message, file)]`. The 5 checks
(each FN-safe two-sided — FIRES on the #137-143 defect shape, PASSES on a clean/canon tree):
- CIP-1: inverted `CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}` in any `.github/workflows/*.yml` (#140).
- CIP-2: a conditional import-fallback shim (`try: import … except ImportError: def …`) carrying `# type: ignore[no-redef]` but missing `misc` (#142 Defect-1).
- CIP-3: ci.yml/release.yml run `uv sync --extra dev` but `pyproject.toml` lacks `[project.optional-dependencies].dev` (#142 Defect-2).
- CIP-4: a CPV-shipped superseded `validate.yml` present ALONGSIDE the consolidated `ci.yml` (#142 Defect-4).
- CIP-5: ci.yml enables Mega-Linter `COPYPASTE_JSCPD` but no `.jscpd.json` exists (jscpd parity, #143).

**Load-bearing facts:**
- CPV's OWN mypy gate is `uv run mypy scripts/ --ignore-missing-imports` (not --strict). Regen
  `.cpv-self-hashes.json` LAST after CLAUDE.md/TRDD edits; self-validate with `CPV_SCAN_CACHE=0
  PLUGIN_SKIP_GITHUB_INTEGRITY=1`. No line-start `#`/`+ `/`* ` markdown-poison in this TRDD.
- Do NOT weaken devitalizer/leaks-preventer never-suppress discipline (FM5 is exemplary; audit confirms).
- ci-preflight degrade-WARNING is MANDATORY — a fixer must never be blocked because a dev box lacks npx/actionlint.

## Background

User asked to "improve the fixer agents and the upgrade agents" (CPV tracker is empty — all issues shipped
through v2.139.0). The audit grounded this in 8 real gaps; the dominant theme is FM1 (local-gate ≠ CI), the
root cause of the entire #137-143 family. This TRDD builds the local CI-parity preflight + wires the existing
green-CI/loop-state hardening into the 2-3 agents that still lack it.

## Notes and lessons learned
