---
trdd-id: e9f13df1-556f-4c02-9fba-5a62ac248eda
title: Canon-profiles — profile-aware + direction-aware canonical-pipeline model
column: dev
created: 2026-06-16T23:40:45+0200
updated: 2026-06-20T04:30:50+0200
current-owner: claude-plugins-validation
assignee: claude-plugins-validation
priority: 2
severity: MEDIUM
effort: XL
labels: [canon, pipeline-drift, profiles, false-positive]
task-type: feature
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, integration, lint, typecheck]
audit-requirements: []
review-requirements: []
impacts: [public-api, ci-pipeline]
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/118", "github.com/Emasoft/claude-plugins-validation/issues/128", "github.com/Emasoft/claude-plugins-validation/issues/130", "github.com/Emasoft/claude-plugins-validation/issues/115"]
---

# TRDD-e9f13df1 — Canon-profiles model

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-19

**Progress (2026-06-19, this session — user said "resume"):** Piece **A+B SHIPPED** —
`scripts/cpv_pipeline_profile.py` (4 profiles + shape detectors: `has_build_source_submodule`,
`has_committed_bin_artifacts`, `is_submodule_build_shape`, `is_binary_release_shape`,
`resolve_pipeline_profile`) exists, and `validate_plugin.py` resolves the profile in BOTH the
drift detector (≈:5611) and readiness (≈:4644); `tests/test_pipeline_profile.py` covers it.
Now doing **Piece C, split into C1 → C2** for incremental ship: **C1 (submodule-build #128)**
delegated to an opus agent this session (spec `docs_dev/piece-c1-submodule-build-spec.md`):
`gen_publish_py(p, profile)` + a submodule-build variant + the #128 source-change-detection fix.
**REFERENCE CORRECTION — do NOT carry forward the §Decomposition names:** the binary-release
reference is janitor `.github/workflows/memgrep-release.yml` (it ships the memgrep Rust binary);
the `release-binaries.yml` / `stage.sh` named in §Decomposition **DO NOT EXIST** in
`Emasoft/ai-maestro-janitor`. PSS refs intact: `scripts/publish.py` (48KB), `build-binaries.yml`,
and `.gitmodules`.

**Goal:** make CPV's canonical-pipeline drift detector + generator + upgrade agent
PROFILE-AWARE and DIRECTION-AWARE, so a plugin whose architecture legitimately
diverges from the single "standard vendored" canon shape stops being flagged as
drift-to-downgrade. Resolves the 4 remaining open issues as ONE model:
- **#130** remote-validation (de-vendored) plugins — drift by design (CAA).
- **#128** submodule-build-source + pre-compiled binaries (PSS).
- **#115** compiled-binary release pipelines (matrix build + stage + smoke) + cron/daemon (janitor).
- **#118 defect 2** direction-aware drift — "ahead of canon" must mean upstream/accept, NOT downgrade (maintainer). (#118 defect 1 already shipped in v2.127.0.)

**Current state:** contract designed (below). Foundation already in the tree:
- `validate_canonical_pipeline_drift` (validate_plugin.py:5362) byte-compares each
  `_CANONICAL_PIPELINE_FILES` entry (the 9-tuple at :5349) against its `gen_*(params)` output.
- a crude `already_hardened` direction-heuristic at :5514 (diff-keyword based) softens the
  message but still WARNs every run, and `--force-templates` still downgrades.
- submodule helpers exist: `is_plugin_in_submodule` (:5943), `validate_submodule_containment` (:6006);
  strip-dev-parts already models a per-plugin `tests/`→submodule (gen at generate_plugin_repo.py:703).
- `gen_publish_py` at generate_plugin_repo.py:1245.

**C2 VERIFY-FIRST FINDING (2026-06-19):** built a realistic binary-release sample (canonical
publish.py/ci.yml/release.yml + janitor's real `memgrep-release.yml`) and ran the current detectors:
the binary-release profile is correctly detected AND **A+B already handles the drift-recognition** —
NO false RC-PIPELINE-DRIFT on the binary-release workflow (it's a NEW file not in
`_CANONICAL_PIPELINE_FILES`, so never byte-compared) and the by-design branch at
`validate_plugin.py:5725` emits the SELECTOR-not-suppressor advisory. So #115's original drift-pain is
ALREADY resolved. **#115 is a multi-part CANON-EXTENSION**, remaining scope: (1) `gen_release_binaries_yml`
template + a shared `stage.sh` + a **CI smoke job** in ci.yml (the convergence ask — janitor wants to
ADOPT canon); (5) an **"untested-until-release" heuristic** — flag an artifact-producing workflow step
reachable ONLY from tag/release triggers (the most on-MANDATE detection-add; catches the real janitor
v0.7.0 incident where a tag-only staging step broke at release); + a `cron-daemon` orthogonal trait
(test-gate reaps processes) + multi-language publish.py gates (Python+Rust+shell). These are
independent sub-pieces of varying value — surfaced to USER for priority (the detection heuristic is the
most aligned with CPV's core mandate; the generator/template is canon-extension the reporter can stage).

**NEXT ACTION (updated 2026-06-19):** **C1 SHIPPED v2.135.0** (CI/Release/Notify green) —
`gen_publish_py` submodule variant, the #128 `git -C` source-change fix, profile-aware drift,
standard byte-identical (HEAD-baseline proven). **#128-A DONE (Piece D core), shipping v2.135.1** —
found during verify: `standardize_plugin.py::fix_missing_files` clobbered the submodule-aware
publish.py because it called `gen_func(params)` with NO profile; it now resolves the profile and
passes it to a profile-parameterized `gen_*`; 3 two-sided tests guard it.
**C2 part-5 (untested-until-release heuristic) SHIPPED v2.136.0** — the user-chosen #115 piece: a
NON-BLOCKING `RC-UNTESTED-UNTIL-RELEASE` WARNING flagging a release-only workflow that builds/stages a
compiled BINARY with no push/PR CI smoke. Detector in `cpv_pipeline_profile.py`
(`classify_workflow_triggers`/`workflow_has_compiled_artifact_build`/`repo_has_ci_build_smoke`/`untested_until_release_workflows`)
plus `check_untested_until_release` in `validate_plugin.py`. The make-or-break FP guard (standard
`release.yml` is tag-triggered + uploads SHA256SUMS but has NO build/stage) verified against the REAL
`gen_release_yml` → 0 findings; delegated to one opus agent + CENTRAL-VERIFIED (independent probe 5/5 +
real end-to-end validator run + CPV self-validate VALID 0/0/0/0, 0 RC-UNTESTED on own tree). +40 tests.
Remaining #115 canon-extension, in order: the `gen_release_binaries_yml` template + shared `stage.sh` +
the CI smoke-job GENERATOR (the convergence ask — distinct from this DETECTOR), multi-language publish.py
gates, the cron-daemon trait; then the rest of **Piece D** — remote-validation "don't re-vendor
validators" (#130) and the upgrade-agent prompt's profile awareness. KEY for the generator half: #115's
drift recognition is STRUCTURAL (SHA-pins, least-priv build/release split, `SHA256SUMS`, build matrix),
NOT a byte-compare; reference is janitor `.github/workflows/memgrep-release.yml`.

**Load-bearing facts / gotchas:**
- Drift compare must select the **gen VARIANT** for the resolved profile, not always the standard one.
- `cpv.pipeline_profile` is a **SELECTOR**, never a **suppressor**. (The removed `allow_pipeline_drift`
  key was a suppressor — a malicious author could silence every finding. A profile selector cannot:
  declaring `remote-validation` HOLDS the plugin to the remote-validation canon, which still enforces
  SHA-pins / least-priv perms / the notify chain / version consistency / atomic push.)
- **Auto-detect** the profile so existing plugins need zero opt-in; the manifest key only OVERRIDES detection.
- INTENT-class + security checks are untouched — this only changes pipeline-drift comparison.
- FN-safe two-sided per piece: the by-design divergence clears AND a genuinely-broken/behind file of the
  SAME profile still WARNs (and a real security regression still fires at its real severity elsewhere).

## The profile contract

### Profiles (one PRIMARY per plugin; `cron-daemon` is an orthogonal trait)

| Profile | Who | Shape | Validation |
|---|---|---|---|
| `standard` (default) | most | vendored validators + standard workflows | current behavior, unchanged |
| `remote-validation` | CAA (#130) | NO vendored validator scripts; publish.py / hooks / ci drive ONLY the remote `uvx cpv-remote-validate … --strict` gate | remote gate |
| `submodule-build` | PSS (#128) | build sources in a git submodule (e.g. `rust/`) + pre-compiled binaries committed to `bin/` + N-file synchronized version bump + submodule-aware publish.py | vendored or remote |
| `binary-release` | janitor (#115) | ships compiled binaries as RELEASE ASSETS via a matrix build + a shared stage script + a CI smoke job + SHA256SUMS | vendored or remote |

Orthogonal trait: `cron-daemon` (heartbeat/daemon runtime) — only relaxes test-gate expectations
(tests spawn/reap detached processes); does not change the pipeline file set.

### Detection (auto; manifest key overrides)

`resolve_pipeline_profile(plugin_root) -> Profile` (new, in a small module e.g.
`scripts/cpv_pipeline_profile.py`):
1. If `plugin.json` → `cpv.pipeline_profile` is set to a known value, RETURN it (authoritative override).
2. Else detect by shape (first match wins; document the signature each uses):
   - `remote-validation`: `scripts/publish.py` (and/or `.github/workflows/*.yml`) invoke
     `cpv-remote-validate` / `cpv_remote_validate` AND the vendored validator scripts
     (`scripts/validate_plugin.py`, `scripts/cpv_lint_engine.py`, …) are ABSENT.
   - `submodule-build`: `.gitmodules` registers a build-source submodule (not the `tests/` strip-dev one)
     AND a committed `bin/` with prebuilt artifacts exists.
   - `binary-release`: a release workflow contains a build MATRIX producing binary assets +
     `gh release upload` of binaries + a `SHA256SUMS` step.
   - else `standard`.
3. Detection is best-effort and side-effect-free; any failure falls back to `standard`.

### Drift semantics per profile (#118-d2, #130, #128, #115)

`validate_canonical_pipeline_drift` becomes profile-aware:
- Resolve the profile, then for each pipeline file pick the **profile-appropriate expected content**:
  - `gen_*` gains a `profile` parameter where the variant differs (esp. `gen_publish_py(params, profile)`).
  - For `remote-validation`, the vendored-validator files are NOT in the expected set (their ABSENCE is
    not a gap — `validate_pipeline_readiness` must also learn this so it stops reporting them missing);
    publish.py / pre-push / ci compare against the remote-gate variants. The process-ancestry pre-push
    gate is recognized as a canonical (preferred) alternative to the env-var gate.
  - For `submodule-build`, compare publish.py against the submodule-aware variant; `bin/` + the gitlink
    are expected, not flagged.
  - For `binary-release`, a new `gen_release_binaries_yml` is the expected template; the matrix build +
    stage script + smoke job are canonical, not drift.
- **Direction-awareness:** when a file still differs, classify AHEAD (structural superset / strictly more
  hardened) vs BEHIND. Strengthen the `already_hardened` heuristic into a real check where feasible.
  AHEAD → "upstream or accept as intentional divergence" (never recommend removing hardening);
  BEHIND → "upgrade." Default to the safe (AHEAD/neutral) message when uncertain — NEVER tell a plugin to downgrade.
- Drift stays **WARNING / non-blocking** and remains **non-suppressible** (TRDD-02e1672b). The profile
  selector changes WHICH canon a file is compared against — it does not silence anything.

### Generator + publish per profile (#128, #115)

- `gen_publish_py(params, profile)` branches: `standard` | `remote-validation` | `submodule-build`.
  The `submodule-build` variant carries PSS's load-bearing behaviors: submodule-commit-before-gitlink,
  submodule-push-before-parent, multi-platform binary rebuild on source change, gitlink-tolerant clean-tree preflight.
- **Submodule source-change detection fix (#128 concrete bug):** detect source changes via
  `git -C <submodule> diff <tag-or-recorded-sha> -- <src-globs>` (or "did the gitlink move since the last
  tag"), NOT the parent-repo `*.rs` glob (which only sees the `160000` gitlink and ships stale binaries).
- New `gen_release_binaries_yml(params)` for `binary-release`: matrix build per target, least-priv split
  (build jobs `contents: read`, one attach job `contents: write`), SHA-pinned actions, `SHA256SUMS`,
  `gh release upload --clobber`, a `workflow_dispatch` `tag` input, the shared `stage.sh` pattern, and a
  CI smoke job that runs the SAME stage script on push (the untested-until-release guard).

### Upgrade agent + diagnose skill (#128-A)

- `/cpv-upgrade-plugin` / `standardize --fix --force-templates`: resolve the profile and
  generate/PRESERVE the profile-appropriate files — never clobber a submodule-aware publish.py with the
  standard one, never re-vendor validators for a remote-validation plugin.
- The `diagnose-plugin-architecture` skill (gap-1, shipped v2.126.33) reports the detected profile.

## Decomposition (delegated, dependency-ordered)

- **Piece A+B** (one opus agent; new `cpv_pipeline_profile.py` + rewire `validate_canonical_pipeline_drift` +
  `validate_pipeline_readiness` to be profile-aware + strengthen direction-awareness): resolves #130 and
  #118-d2; foundation for C/D. FN-safe two-sided tests per profile. Fetch CAA's publish.py (remote-validation
  signature) + the maintainer's hardened release.yml (ahead-of-canon case) as reference.
- **Piece C** (one opus agent; `gen_publish_py(profile)` + `gen_release_binaries_yml` + submodule
  source-change detection): resolves #128 + #115. Depends on A. Fetch PSS `publish.py`/`build-binaries.yml` +
  janitor `release-binaries.yml`/`stage.sh` (commits f7104d6 + v0.7.1) as reference.
- **Piece D** (one opus agent; upgrade-agent + diagnose-skill profile awareness): resolves #128-A. Depends on A + C.
- Upstream acceptance: take the maintainer's offered hardened `release.yml` + `notify-marketplace.yml`
  PAT-preflight into canon (clears drift at the source) — fold into Piece C or a follow-up.

Each piece: I write the precise spec (grounded in this contract), delegate to a `model: opus` cpv-spark
agent, then central-adversarial-verify (own probe + full suite) before shipping. Ship incrementally
(A+B, then C, then D), one release per landed piece (or batched), each closing the issues it resolves.
