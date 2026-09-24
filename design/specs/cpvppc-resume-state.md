# CPVPPC — resume state (2026-09-24)

Read this first after a context reset. Authoritative plan: `design/specs/cpvppc-plan.md` (approved by
the user on 2026-09-24, then refined by adversarial reviews; this copy includes every refinement).

## Cards

- Epic: TRDD-1T862D4B. Phases: P1 = TRDD-DFRPRZYD, P2 VWSEG7D7, P3 21ID8NG7, P4 Y22TUHM2, P5 RBPHVYR5,
  P6 0DWMU7BV, P7 18KIWEWC, P8 TINOOW8V, P9 QAFKLBYX, P10 2O73MIAL, P11 8QWLECUZ, P12 I3TZG9LN,
  P13 057QUK84, P14 22HCJULG, P15 1RB0TZC1.
- TRDD-8W80DZHI (the rollout investigation) is to be closed as superseded by the epic.

## User decisions (2026-09-24)

1. The canon has its own semver, separate from CPV's version.
2. Lock file `cpvppc.lock.json` plus author config `cpvppc.yaml`; templates fully parameterized
   (languages, mixed compilers, platforms, binaries-only, deps, vendored tools, installers, subfolder
   git root, local vs CI lint, extra checks); NO option may bypass or lower validation, security or
   privacy gates; no exceptions; maximum strictness.
3. Local release builds allowed if hash-pinned and signed with a declared key; marked "locally built".
   They reach CI through a `staging-v<version>` DRAFT release (the only human write).
4. Private repos: `COMPLIANT (no provenance: private repo, self-attested scan manifest)`.
5. Code signing optional; unsigned binaries marked; fetcher clears macOS quarantine after a hash match.
6. The GitHub Actions app is the only ruleset bypass actor; G-CONFIG proves no other workflow writes.
7. A plan must be split across multiple TRDD cards, each self-contained (not a pointer to the plan).

## Next actions

1. Verify the P0 worker's output: every P2-P15 card body is self-contained (copied plan sections), the
   epic lists the phase ids, DFRPRZYD retitled as P1 with the approval note, 8W80DZHI closed.
   Worker report: `docs_dev/cpvppc-p0-report.md`.
2. Split any phase card holding more than one atomic task (e.g. P1: framework vs marketplace
   assertions) into separate cards, each self-contained.
3. `trddgrep lint`, regenerate self-hash manifests, commit by file name.
4. Then start P1 per its card (lean-worker builds; orchestrator reviews every diff and re-runs checks).

## Session facts worth keeping

- `trddgrep new` now requires `--author main-agent@claude-plugins-validation`; `move` requires
  `--approver` (machine rule from the ai-maestro session).
- Investigation evidence: `reports/rollout-investigation/20260924_182730+0200-marketplace-canon-rollout.md`
  and `reports/cpvppc-measure/20260924_184823+0200-cpvppc-feature-inventory.md` (both gitignored).
- Nothing has been pushed; no outside repo has been changed.
