---
trdd-id: abda272d-41aa-4923-a3b3-8ae03d3dfd9f
title: Canon publish.py --gate ↔ ci.yml gate-parity gap — local gate omits the jscpd copy-paste check CI enforces (issue 143)
column: dev
created: 2026-06-21T17:18:02+0200
updated: 2026-06-21T17:18:02+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 2
severity: HIGH
effort: M
labels: [canon-pipeline, publish-gate, ci-parity, jscpd]
task-type: bugfix
parent-trdd: null
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, lint, typecheck]
impacts: [ci-pipeline]
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/143"]
implementation-commits: []
---

# TRDD-abda272d — Canon publish.py --gate ↔ ci.yml jscpd gate-parity gap (issue 143)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-21

**Current state:** IMPLEMENTED + central-verified; ready to publish v2.139.0. Two file-disjoint opus
agents landed it (A: generate_plugin_repo.py — `gen_jscpd_json` + `.jscpd.json` wired + Gate 2b in the
publish.py template + `--gate` help; B: standardize_plugin.py — `provision_jscpd_config`/`audit_jscpd_config`
plus `_render_canonical_jscpd_config`, `_publish_py_has_jscpd_gate`, and both pipeline-rules.md docs). 27
two-sided tests (test_canon_143_genrepo.py 14, test_canon_143_standardize.py 13). CENTRAL-VERIFIED: the
two agents' `.jscpd.json` content is semantically identical (threshold 5, minTokens 50, same ignores —
no drift); 57 canon tests pass; mypy `--ignore-missing-imports` clean (123 files); ruff clean; the G2b
gate carries the never-false-block `--version` probe + degrade-WARNING + BLOCK-on-over-threshold exactly
per design.

**The bug (issue 143, filed by assistant-manager-agent, evidence v2.12.6):** the generated
`publish.py --gate` (run by the strict pre-push hook) runs Gate 2 = `ruff check scripts/` only,
but the generated `ci.yml` Lint job runs **Mega-Linter** with `COPYPASTE_JSCPD --threshold 5`.
So a publish passes every local gate, exits 0, bumps+tags+pushes+releases, and THEN CI fails on
jscpd (the tagged/released version ships with red CI). Green `publish.py` ≠ green CI for the
copy-paste dimension. CPV owns the template; the *duplication* is the adopter's job, but the
*gate-parity gap* is CPV's. **CPV itself is NOT affected** (no `.jscpd.json`/`.mega-linter.yml`,
its own workflows don't run Mega-Linter) — this is purely a generated-template + standardize fix.

**The fix (single source of truth + graceful degradation, the #129 pattern):**

1. **New `.jscpd.json` canon file** (`gen_jscpd_json` in `generate_plugin_repo.py`) — read by BOTH
   CI's Mega-Linter jscpd AND the local gate (jscpd auto-discovers `.jscpd.json`), so parity is
   exact and there is one source of truth for threshold + ignores:
   ```json
   {
     "threshold": 5,
     "minTokens": 50,
     "gitignore": true,
     "reporters": ["console"],
     "ignore": [
       "**/tests_dev/**", "**/docs_dev/**", "**/scripts_dev/**", "**/samples_dev/**",
       "**/examples_dev/**", "**/builds_dev/**", "**/downloads_dev/**", "**/libs_dev/**",
       "**/llm_externalizer_output/**", "**/.claude/**", "**/.tldr/**",
       "**/tests/fixtures/**", "**/test/fixtures/**", "**/spec/fixtures/**",
       "**/__fixtures__/**", "**/testdata/**", "**/fixtures/**",
       "**/node_modules/**", "**/.git/**"
     ]
   }
   ```
   The `ignore` globs mirror `.mega-linter.yml`'s `FILTER_REGEX_EXCLUDE` (same dirs). Threshold 5
   matches `COPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"`. (Keep the mega-linter.yml arg as-is — same
   value, harmless; jscpd reads `.jscpd.json` for the ignores either way.)

2. **New gate G2b in the publish.py template** (`gen_publish_py`, in `--gate` mode, inserted right
   AFTER Gate 2 ruff / BEFORE Gate 3 validate — entire `--gate` run is pre-push so position only
   affects ordering). Robust probe-then-run so a tool-unavailable case DEGRADES (never false-blocks
   a push), and a real over-threshold case BLOCKS:
   ```python
   # Gate 2b: Copy-paste detection (jscpd) — PARITY with ci.yml Mega-Linter COPYPASTE_JSCPD.
   # CI's Lint job fails on jscpd duplication over the .jscpd.json threshold; surface it locally
   # BEFORE the bump/tag/push. jscpd needs Node/npx; if it cannot be obtained, DEGRADE to a
   # non-blocking WARNING (CI still enforces it) — a green gate then does NOT guarantee green CI
   # for the copy-paste dimension (issue #143). NEVER false-block a push on a tool-install failure.
   cprint(f"\n{BLUE}[G2b] Copy-paste check (jscpd, parity with CI)...{NC}")
   jscpd_bin = shutil.which("jscpd")
   base_cmd = [jscpd_bin] if jscpd_bin else ([shutil.which("npx"), "--yes", "jscpd"] if shutil.which("npx") else None)
   if base_cmd is None:
       cprint(f"  {YELLOW}WARNING: jscpd/npx not found — copy-paste check SKIPPED locally.{NC}")
       cprint(f"  {YELLOW}CI's Mega-Linter WILL enforce it (.jscpd.json threshold). A green gate does")
       cprint(f"  {YELLOW}NOT guarantee green CI for the copy-paste dimension (issue #143). Install")
       cprint(f"  {YELLOW}Node/npx for full local parity.{NC}")
   else:
       # Probe distinguishes 'jscpd unavailable/uninstallable' (WARN) from 'jscpd ran, found dupes' (BLOCK).
       probe = subprocess.run(base_cmd + ["--version"], cwd=str(root),
                              capture_output=True, text=True, timeout=180)
       if probe.returncode != 0:
           cprint(f"  {YELLOW}WARNING: jscpd could not run (npx fetch/install failed) — SKIPPED locally.{NC}")
           cprint(f"  {YELLOW}CI's Mega-Linter WILL enforce it; green gate ≠ green CI for copy-paste (issue #143).{NC}")
       else:
           cp = subprocess.run(base_cmd + ["."], cwd=str(root), timeout=300).returncode
           if cp != 0:
               cprint(f"  {RED}BLOCKED: jscpd found copy-paste duplication over the .jscpd.json threshold{NC}")
               cprint(f"  {RED}(parity with CI Mega-Linter). Reduce duplication or raise the threshold in .jscpd.json.{NC}")
               return 1
           cprint(f"  {GREEN}Copy-paste check passed.{NC}")
   ```
   Also update the `--gate` help/docstring stage list to mention the jscpd/copy-paste gate.

3. **standardize_plugin.py**: on `--fix`, PROVISION `.jscpd.json` if missing (create from the canon
   content; format-preserving create, never overwrite a user's existing one without --force-templates).
   The publish.py template refresh (`--force-templates`) already carries G2b for free; for a plain
   `--fix` (no force), detect a publish.py that lacks the jscpd gate and SURFACE it (audit WARN) +
   provision `.jscpd.json`. Mirror the #142 `provision_dev_extra` / `remove_superseded_validate_yml`
   shape (identity-guarded, format-preserving, audit path WARN-only).

**NEXT ACTION:** spawn 2 file-disjoint opus agents (A: generate_plugin_repo.py + gen-test; B:
standardize_plugin.py + standardize-test + 2 skill pipeline-rules.md docs). Central-verify (50/50
changed tests + mypy `uv run mypy scripts/ --ignore-missing-imports` + ruff + self-validate --strict
0/0/0/0 + Gate-2 full suite), then publish v2.139.0, close #143 self-id'd.

**Load-bearing facts:**
- jscpd exits non-zero iff duplication % > `threshold` (from `.jscpd.json`). The `--version` probe
  returns 0 only when jscpd is actually runnable → distinguishes unavailable (WARN) from over-thresh (BLOCK).
- CPV's OWN mypy gate is `uv run mypy scripts/ --ignore-missing-imports` (NOT --strict). Regen
  `.cpv-self-hashes.json` LAST, after this TRDD + CLAUDE.md edits (the regen-LAST lesson), then
  self-validate with `CPV_SCAN_CACHE=0 PLUGIN_SKIP_GITHUB_INTEGRITY=1`.
- No line-start `#`/`+ `/`* ` markdown-poison in this TRDD (MD018/MD004 NIT blocks --strict).

## Background

Issue 143 (assistant-manager-agent, v2.12.6 evidence): a 5th canonical-pipeline gate-parity gap in
the #137-142 family — the local pre-push gate and the CI Lint job disagree on the copy-paste (jscpd)
dimension, so a publish can tag+release a commit that then fails CI. Distinct from #142's four
defects (mypy ignore, dev-extra, inverted env, superseded validate.yml); this one is a missing
gate, not a malformed one.

## Notes and lessons learned
