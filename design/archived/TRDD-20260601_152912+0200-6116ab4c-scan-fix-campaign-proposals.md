---
trdd-id: 6116ab4c-5bfc-4b8b-8ca9-5f78ade68da4
title: Scan-and-fix campaign — structural/design proposals for maintainer evaluation
column: superseded
superseded-by: [TRDD-WT0FLTMM, TRDD-627GMQLD]
created: 2026-06-01T15:29:12+0200
updated: 2026-08-25T17:25:45+0200
---

# TRDD-6116ab4c — Scan-and-fix campaign design proposals

**Filename:** `design/proposals/TRDD-20260601_152912+0200-6116ab4c-scan-fix-campaign-proposals.md`
**Tracked in:** this repo (design/ is git-tracked)
**Status:** `proposal` — NOT planned. The maintainer evaluates each section
below and, for any accepted, spins it out into its own `status: not-started`
TRDD under `design/tasks/`.

## Origin

The `/workflow-codebase-scan-and-fix` campaign (run dir
`reports/workflows/20260531_191441+0200-codebase-scan-and-fix/`) processed
423 source files with one Opus agent each (lint → deep scan → SERENA +
adversarial verify → fix). It fixed ~364 verified defects in-place, but the
agents also surfaced **58 structural/design issues that cannot be fixed
inside a single file** (cross-cutting, shared-constant, or
requirement-decision in nature). De-duplicated and clustered, they form the
**7 proposals** below. Evidence: `cycle-*.json` +
`design_proposals_unique.json` in the run dir.

---

## Proposal 1 — Doc↔code drift-guard test/lint suite (HIGH value)

**Problem.** The single largest finding class (~28 of 58): documentation
embeds values that silently drift from the code they describe — rule/pattern
counts ("N rules / M patterns"), enum lists (valid hook events, source types,
model aliases, `BUILTIN_AGENT_TYPES`, reserved marketplace names), severity
tags (`[CRITICAL]`/`[MAJOR]`…), verbatim error messages, and worked-example
`[SEVERITY] message` lines in the `fix-*/SKILL.md` guides. The campaign found
many already-drifted instances (stale counts, paraphrased error messages,
example severities not matching the live catalog).

**Proposed approach.** A parametrized pytest module (`tests/test_doc_code_parity.py`)
that, for each doc-embedded claim, imports the live constant and asserts
equality:
- extract fenced ```json``` blocks claiming to be plugin.json/marketplace.json
  examples and run them through the validator they document;
- parse `**Error message**:` / `**Message** (verbatim):` lines and assert each
  exists in the live message catalog;
- assert documented enum lists == the imported `VALID_*` / `RESERVED_*` /
  `BUILTIN_AGENT_TYPES` sets;
- assert documented counts ("<N> valid hook events") == `len(VALID_HOOK_EVENTS)`.

**Affected (evidence):** `references/*-fixes.md`, `skills/*/SKILL.md`,
`validate_hook.py`, `validate_mcp.py`, `validate_marketplace.py`,
`cpv_validation_common.py`, `scripts/rules/*.json`.

---

## Proposal 2 — Ban raw source-line citations in docs

**Problem.** Docs cite `script.py:NNN` / `L<NNNN>` line numbers that rot the
moment the source shifts. The campaign found multiple stale citations (one
group had 4 stale `file.py:NNN` refs, one pointing at an unrelated function).

**Proposed approach.** A lint gate (extend `validate_documentation.py` or a
new pytest) that flags `\b\w+\.py:\d+\b` and `\bL\d{2,}\b` patterns inside
`references/*.md` and `skills/**/*.md`, recommending symbol-name references
instead. Allow an explicit opt-out comment for the rare intentional case.

---

## Proposal 3 — Template ↔ generator parity

**Problem.** `templates/github-workflows/*.yml` and `templates/scripts/*.py`
have drifted from the canonical generators (`generate_plugin_repo.py`,
`generate_marketplace_repo.py`) that are supposed to emit them — including
GitHub-Actions action-version divergence (`@v4` vs `@v6` vs pinned SHA) and
`.markdownlint.json` generator drift (phantom `MD057`, missing `MD025`).

**Proposed approach.** Either (a) generate the `templates/` files from the
generators at build time and assert byte-equality in CI, or (b) a parity test
that diffs each shipped template against the generator's output. Single-source
the GHA action versions into one constant consumed by both.

**Affected:** `templates/github-workflows/`, `templates/scripts/sync_marketplace_versions.py`,
`generate_plugin_repo.py`, `generate_marketplace_repo.py`.

---

## Proposal 4 — GitHub-Actions expression-injection detector + receiver hardening

**Problem.** No CPV detector rule currently flags `${{ ... }}` interpolated
directly into `run:` / `echo` / `git commit -m` GitHub-Actions steps
(expression-injection / CWE-94). The marketplace auto-notify receiver
workflow also lacks input-hardening (plugin/version regex validation,
env-passed commit).

**Proposed approach.** Add a skillaudit/validate_security rule for
`${{ ... }}` in shell contexts of workflow YAML, and ship a hardened
"receiver" snippet (regex-validate `plugin`/`version`, pass values via `env:`
not inline) reused by `setup-marketplace-auto-notification`.

---

## Proposal 5 — Archive-extraction zip-bomb hardening (security)

**Problem.** `cpv_management_common._extract_zip` / `_extract_tar` preflight
the quota using the **archive-declared** uncompressed size
(`info.file_size` / `member.size`). A crafted archive that under-reports its
declared size bypasses the `max_bytes` / `max_per_file_bytes` / `max_ratio`
gates while extraction still writes the real (large) data. (Entry-count,
nesting, and path-traversal defenses remain intact.)

**Proposed approach.** Switch to **streaming decompression with a running
byte cap** — abort when actual bytes written exceed the limit, independent of
the declared size. This is a substantial extractor rewrite, hence a proposal
rather than an in-file fix.

---

## Proposal 6 — Shared helpers / single sources of truth

**Problem.** Several duplicated code paths the campaign could not unify from
within one file:
- `read_jsonc_or_report(path, max_bytes, report, label)` — the JSONC-load +
  size-guard + report-on-error dance is copy-pasted across validators.
- The local-markdown-link existence check is duplicated in `validate_skill.py`
  and `validate_skill_comprehensive.py`.
- **Two** severity ladders + two `demote_severity()` signatures
  (`cpv_fp_classifier._SEVERITY_DEMOTION_ORDER` vs
  `cpv_validation_common.SEVERITY_TIERS`).
- The CA-rule range (`CA-01..CA-07`) is restated as a literal in
  `validate_cache.py` and multiple docs.

**Proposed approach.** Extract each into one shared definition in
`cpv_validation_common.py`; update call sites + the affected tests
(`test_cpv_fp_classifier.py`, `test_fp_reduction.py`) in the same change.

---

## Proposal 7 — Miscellaneous small design nits (triage individually)

Low-severity, each self-contained but needing a requirement/design decision:

1. `cpv_skill_scanner.py` — the Cisco scratch file `.cpv-cisco-scan.json` is
   written **into** the scanned plugin tree; relocate to a temp/`reports` dir.
2. `validate_mcp.py` — enforce `oauth.serverUrl` (+ scopes array) which
   `mcp-validation.md` documents as required; add a `timeout > 1000` advisory
   the doc already describes.
3. `cpv_management_common` `_extract_*` — see Proposal 5.
4. `scripts/audit/fixture_grid_generator.py::materialize_all` — never wipes a
   stale fixture dir at `grid_root` (renamed descriptors leave orphans);
   decide harness-level cleanup (data-loss-safe).
5. `cpv_codemod._apply_wrap_placeholder_paths` — no fence tracking, unlike the
   sibling transform; decide whether placeholder-wrapping should be fence-aware.
6. `cpv_fp_classifier_rules` — `file_role_of` can return `'sample'` but no
   classifier branches on it (sample-role findings stay full-severity);
   requirement decision on whether `sample/` code should be demotable.
7. `_minimal_yaml.py` — out-of-subset numeric coercion gap (`0xFF`/`1.5`/`+5`
   kept as strings vs pyyaml); intended per docstring — decide if parity wanted.
8. `cliff.toml` — the `{ body = ".*security" }` parser sits after `^feat`/`^fix`
   (first-match-wins) so it never fires for conventional commits; decide whether
   a real Security changelog section is wanted (fix belongs in BOTH generators).
9. `_plugin_verify_hashes.py:438` — sibling-version recovery hint sorts cached
   versions lexically (`v2.9.0` > `v2.10.0`); use a semver-aware sort.
10. `remote_validation.py` — `-o/--output` is unconditionally forwarded as
    `--report`; add a scope guard.
11. Dangling standalone-command references: `~9` skill/agent/reference files
    still mention `/cpv-fix-validation`, `/cpv-semantic-validation`,
    `/cpv-doctor --install-scanners`, `/cpv-strip-dev-parts` as slash commands,
    but those were folded into `/cpv-main-menu` at v2.90.0 (no standalone
    `commands/*.md` exists). Pre-existing (not from this campaign) and NOT
    flagged by CPV's own xref validation (self-scan is 0/0/0/0), so it is a
    doc-consistency cleanup, not a validation error — decide whether to rewrite
    the references to the menu path or keep them as conceptual command names.

---

## Maintainer action

For each proposal accepted: create a `status: not-started` TRDD under
`design/tasks/` (one per proposal), referencing this document's `trdd-id`.
Reject/defer the rest by noting it here and flipping this TRDD's status to
`superseded` (by the spun-out tasks) or leaving it `proposal` for later.

## Decision (2026-08-25, CPV session, authority delegated by USER)

- **P4 ACCEPTED** → TRDD-WT0FLTMM (GHA expression-injection detector +
  receiver hardening). Verified unshipped first-hand before acceptance.
- **P5 ACCEPTED** → TRDD-627GMQLD (streaming extraction byte caps).
  Verified unshipped first-hand (declared-size preflight still trusted).
- **P1/P2/P3/P6 DECLINED** — internal doc/template/refactor hygiene with no
  observed recurrence since June; the intervening ~100 releases repeatedly
  fixed drift at the source (self-hash manifest, spec-sync cadence, CI
  parity gates) and none of these four classes resurfaced in an issue or a
  gate failure. Reversible: re-propose if the class recurs.
- **P7 DECLINED as a batch** — 11 individually-small nits from a May
  campaign; none has bitten since. Any that recurs gets its own card.

## Approval log

- 2026-08-25T17:25:45+0200 — CLOSED as superseded (by TRDD-WT0FLTMM,
  TRDD-627GMQLD) by the CPV session (board drain; authority delegated by
  USER 2026-08-25). P4+P5 accepted and spun out; the rest declined with
  reasons above.
