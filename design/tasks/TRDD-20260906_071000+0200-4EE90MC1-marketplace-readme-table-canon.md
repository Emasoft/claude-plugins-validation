---
trdd-id: 4EE90MC1
title: Adopt the generated marketplace README table into the canon and fix the marketplace-version key
column: dev
created: 2026-09-06T07:10:00+0200
updated: 2026-09-06T07:10:00+0200
current-owner: main-session
task-type: feature
scope: project
project-id: claude-plugins-validation
relevant-rules: []
npt: []
eht: []
---

# Adopt the generated marketplace README table into the canon and fix the marketplace-version key

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-06

Colony run, 8 units, ledger at `docs_dev/DELEGATION.md` (gitignored, holds the full evidence).
Units 1-6 have LANDED in the working tree; rows 1-4 are `verified`, rows 5-6 pass their own
tests and still need a coordinator READ of the diffs (doc rows are verified by reading, not by
running their own assertions). Rows 7-8 (ai-maestro-plugins migration + PR) NOT STARTED.

DECIDED, with the measurement, so it is not re-litigated:
- Both new marketplace findings are **INFO with zero score weight**, not MINOR. `exit_code()`
  thresholds on the weighted score alone and `info()` is 0.0/0.0, so this cannot move any
  marketplace's grade. Measured: ai-maestro-plugins 19.0/F, emasoft-plugins 56.0/F, both
  already F beforehand; emasoft PASSES both checks. The table is CPV canon, not spec, so
  scoring it would invent a gate (v2.154.1 ruling) — this is the WARN phase of the same
  ladder RC-SHIP-BINARY-ONLY walked (v3.7.0 -> v3.14.0 -> v5.0.0).
- `metadata.version` alone = NIT; WARNING only when both keys disagree.

DEFECTS FOUND AND FIXED THIS SESSION (all mine or the workers', none shipped):
1. Check 5b read `workflow_content` at function level -> NameError on unparseable workflow
   YAML. Re-indented into the binding `else:`. Mutation-proven.
2. The ported renderer stamped `date.today()` into a block `--check` compares -> a DAILY false
   CI failure for every adopter. Date removed. **Upstream emasoft-plugins still has this.**
3. `test_audit_fix_b24.py::test_marketplace_guide_references_real_templates` pinned
   `generate-readme.py`, a name that was never a real template. Repointed + strengthened.

## ⏵ STATE UPDATE — 2026-09-06, later session

ALL 8 COLONY ROWS VERIFIED; ledger `docs_dev/DELEGATION.md` complete with a content-bound receipt
(checker re-ran all 8 acceptance commands, exit 0). PR opened:
https://github.com/Emasoft/ai-maestro-plugins/pull/18 — OPEN, MERGEABLE, its own `check-table` gate
PASSES. Built in a scratchpad clone, never the user's working copy; nothing pushed to `main`.

LANDED SINCE THE BLOCK BELOW WAS WRITTEN:
1. Both new findings are **INFO, zero weight** (not MINOR). `has_minor()` has no production
   consumer; `cpv_fix_ledger` cannot ingest this report (`to_dict()` has no top-level `results` —
   proved by CALLING it, after a first "proof" that was a could-not-fail sed range).
2. **INFO was invisible in the text report** — filtered at :1640, absent from SUMMARY. Fixed: third
   rendered bucket `[i] INFO:` + a separate SUMMARY count. Mutation-proven against HEAD's renderer.
   Side effect: the pre-existing workflow-hardening advisories are visible for the first time.
3. Doc defects found by READING (tests could not see them): dead `datetime` import in the
   doc-embedded renderer copy (now byte-parity tested), two stale "generated-timestamp" claims,
   and both fix recipes declaring MINOR while the code emits INFO.
4. `templates/github-workflows/update-submodules.yml`: `git add .` → `git add -u` (issue #186).
   Measured, not argued: `-u` stages gitlinks, skips untracked; it does NOT stage a NEWLY ADDED
   submodule, which is safe because this job only updates existing ones. Boundary written into the
   comment and pinned by a test.

TOP OPEN ITEM — **QUEUED, not dropped**: filed as `TRDD-FK9Y6NCL` at `column: todo`
(`design/tasks/TRDD-20260906_083702+0200-FK9Y6NCL-...md`) rather than started here, because it
carries a blocking design decision of its own (the canonical renderer REFUSES an empty plugin list
by design, while this generator scaffolds an EMPTY marketplace — so a naive swap ships a scaffold
whose own `--check` gate fails on its first run) and the table shape lives in THREE places in that
one file (`_readme` :195, `_readme_local` :283, `_update_catalog_script` :726). Starting a renderer
redesign on an uncommitted tree with a review outstanding would compound, not finish. The card
carries every fact measured below. ALSO FIXED here: the unit-6 test message calling
`update_catalog.py` a "non-existent script name" was FALSE — the assertion is right, the reason was
not; corrected in `tests/test_canon_docs_readme_table.py`. Original finding, verbatim:

CPV has TWO marketplace scaffolding
paths and only one was updated. `setup_marketplace_automation.py` copies the new
`render_readme_table.py`; `generate_marketplace_repo.py` still emits the older `update_catalog.py`
catalog mechanism with no PLUGIN-VERSIONS markers — so a marketplace scaffolded that way would
immediately draw the new advisories (CPV flagging its own scaffold as off-canon). Also: the unit-6
test message calling `update_catalog.py` a "non-existent script name" is FALSE — the generator
emits both the script (:1197) and the workflow that runs it (:678).

LANDED AFTER THE LIST ABOVE — the bare-`git add` class finished, not sampled. Fixing the workflow
template closed one instance; a sweep found **8 sites at HEAD, 7 of them unallowlisted** (my first
figure, "7, all non-allowlisted", was the edit list dressed as a sweep result — the allowlisted
`layout-a-migration.md:150` is a real pre-fix occurrence). All are recipes agents COPY into other
repos. Four became `git add -u` (submodule sync ×2 in
`README-marketplace.md`, the manual-sync block in `readme-template.md`, the workflow `run:` in
`marketplace-fixes.md:1445`); two `git init` recipes now stage BY NAME with a review step (`-u`
stages nothing before a first commit); one is KEPT and justified inline (a fresh `/tmp` clone where
`standardize --fix` CREATES the files); and the generator's printed scaffold hint had a caveat that
CONTRADICTED its own command, now rewritten. **A hypothesis of mine was disproved by reading the
context**: `README-marketplace.md:248` was NOT a fresh-scaffold init case — it is the same ongoing
`submodule update --remote --merge` → sync → commit shape as the workflow. The §5.18 comment was
also rewritten: it justified `-u` from the doc's own workflow variant, but the recipe lands in
marketplaces whose workflow DOES update submodules, which is exactly where `-u` is load-bearing.
TWO of the recipes were BROKEN, not merely impure, and only RUNNING them showed it. The `git init`
one took THREE repairs: naming fewer paths staged only the manifest into a repo `gh repo create
--push` publishes (silent, worse than the loud abort it replaced); naming five paths still silently
dropped every component NOT on the list (`hooks/`, `scripts/`, `bin/`, `.mcp.json`, `LICENSE` —
CPV's own shape), and the "loud failure" guarantee did not even hold, since git skips an
existing-but-EMPTY dir at exit 0. Repair 3 (`git add .` under a `git status --short` line) was the
SAME defect a third time — the review sat inside the same fenced block, so a paste ran add → commit →
`gh repo create --push` with no point at which a human could act; all three repairs put the
mitigation in a comment the execution never honours. SHIPPED (repair 4): the fence is SPLIT —
`git init && git status --short` is its own block, prose in between says what to delete (and names
the criterion: "not the plugin" = anything a fresh clone would not need), and staging is a SECOND
block. Honest about what that buys: nothing ENFORCES the review; the split makes intervention
POSSIBLE where 1-3 made it impossible — structural in layout, not enforcement. I first wrote
"STRUCTURAL" and the next review called that overclaim. Guard entry is file-keyed with a pinned
COUNT (2) and the justification must sit in the comment block ATTACHED to each site. A fixed 16-line
window was tried and DELETED: it could be outgrown by a reflow, and at 62 lines it would have let
one exempt site borrow the other's comment — the contiguous-block walk has neither failure mode and
removes the constant. Also corrected: I had
called an under-commit "strictly worse" than an untracked sweep — false, they differ on TWO axes and
I used one. An under-commit is quieter but cheap to fix; a swept secret is permanent (history, forks,
caches), which is why #186 exists and why the gate had to become structural. `git add .claude-plugin README.md` aborts (exit 128,
stages nothing) on a plugin with no README — which CPV's own validator rates a non-blocking WARNING — and the plugin-removal recipe could never run at all,
because `git config -f .gitmodules --remove-section` leaves `.gitmodules` dirty and the next
`git rm --cached` dies with exit 128 (pre-existing, identical under `git add .`; fixed by staging
`.gitmodules` first). Guard: `tests/test_no_bare_git_add_in_docs.py` (5 tests). Its first regex was
line-anchored and missed `&&`-chained, `;`-chained, quoted and `./` bare adds, and its suffix list
skipped `.py` (the shipped script templates) — both widened. Proof is a DERIVATION, not a checkout
run: the test's own regex over its own scope against `git show HEAD:<path>` gives 8 hits, 1
allowlisted, 7 blocking. The class is closed as measured today across six dirs; the guard covers
three of them.

## ⏵ VERIFICATION + RESUME — 2026-09-06

**Self-validate PASSED** on the settled tree: `SUMMARY: CRITICAL=0 MAJOR=0 MINOR=0 NIT=0
WARNING=16`, exit 0. WARNING is the one tier that never blocks even under `--strict`, so this is
a genuine pass — but **only 1 of the 16 WARNINGs was inspected** (the known "depends on
claude-menu-system, could not be resolved (no marketplace context)" advisory). Do not let that one
example stand in for the set. Exact command (every env var is load-bearing; `CPV_SCAN_CACHE=0`
most of all — a warm cache hides a classifier change):

```bash
CPV_SCAN_CACHE=0 PLUGIN_SKIP_GITHUB_INTEGRITY=1 CLAUDE_PRIVATE_USERNAMES="$(whoami)" \
  uv run --with pyyaml python scripts/remote_validation.py plugin . --strict -o /tmp/selfval.txt
```

**Full serial suite:** its verdict, if any, is the `FULLSUITE_EXIT=` line in `/tmp/finalZ.txt`. If
that file is absent or lacks the marker, RE-RUN it — never read a task notification's exit code,
which carries the LAST command in the chain. Measured three times this session; two chains
reported "completed (exit code 0)" whose real markers were `143/143` (SIGTERM).

**External work in flight:** <https://github.com/Emasoft/ai-maestro-plugins/pull/18> — OPEN,
MERGEABLE, its own `check-table` gate passing. Built in a scratchpad clone; nothing pushed to
`main`. That is a DIFFERENT repo (cross-project rule: never edit it directly).

**Three unrelated change-sets share this dirty tree** — the ai-maestro PR #18 work, the canon docs,
and the bare-`git add` sweep. `git status` lists them; it does not say which is which.

**Why `git add .` is allowlisted in two recipes** (do not re-open without reading this): naming
paths was tried TWICE and fails in BOTH directions — a named path the plugin lacks aborts the whole
add (exit 128, stages nothing), and any component NOT named is silently dropped from a repo pushed
one line later. The recipe comments carry the full argument.

**THE TRAP THAT COST THE MOST TIME:** ten verification chains were killed as stale because each
review round edited the tree its verification was measuring — a docstring-only edit still moves the
file hash and invalidates the manifest. If a review returns findings while a chain runs, QUEUE them.

ALSO OPEN: full serial suite was restarted against the final tree (the earlier run went stale when
test files were edited mid-run) — read `FULLSUITE_EXIT=` from `/tmp/fullsuite3.txt`, never the task
notification's exit code. CPV itself is UNCOMMITTED and UNPUSHED; no `publish.py` run.

NEXT ACTIONS, in order:
1. Wait for the in-flight unit1-repair worker (tests MINOR->INFO + a source-indentation
   regression guard), then re-run `scripts/_plugin_compute_hashes.py` — hashes LAST.
2. Read the unit 5 and unit 6 diffs, then flip rows 5-6.
3. Re-run the FULL suite and read `FULLSUITE_EXIT=` from the captured file — the background
   notification's "exit code 0" is the trailing command, not pytest (it lied once already).
4. Do units 7-8 in a clone under the scratchpad; open the PR, never push to master directly.
5. `node ~/.claude/skills/colony/scripts/ledger-check.mjs docs_dev/DELEGATION.md`.

## Landed as ONE commit — revert granularity is all-or-nothing

`6423883f` carries all four concerns (README table, version-key inversion, git-add doc
sweep, required-template SSOT). That was deliberate: the four are not independent —
the version-key fix without its test rename fails the suite, the SSOT refactor without
its fixture change fails — so staged commits would have been broken at intermediate
points. The git-add sweep is the one genuinely separable piece and it rode along.

**The cost to know about: `git revert 6423883f` takes all four back.** If the
version-key direction ever needs undoing, revert the hunk, not the commit. The
manifest was verified to match the committed tree by regenerating it and comparing the
parsed JSON with `computed_at` excluded — that field changes on every regen, so a bare
`git diff` on the manifest is never a staleness test.

## Why

Two defects, both verified first-hand.

**1. The marketplace README goes stale by construction.** The canon's Update-Versions workflow
stages only `.claude-plugin/marketplace.json`
(`skills/cpv-setup-github-marketplace/references/workflow-templates.md:335`), so nothing ever
rewrites the README plugin table. CPV documents a `generate-readme.py` that would do it
(`references/script-templates.md:1403-1672`) but `scripts/setup_marketplace_automation.py` never
copies it — a generator that exists only on paper. `Emasoft/emasoft-plugins` solved this in
production with `scripts/render_readme_table.py` plus a workflow step that runs it before the
change-check and a `--check` CI gate; that is what the canon now adopts.

**2. CPV pushes authors onto the legacy marketplace-version key.**
`scripts/validate_marketplace.py:4099-4107` emits a NIT reading *"Top-level 'version' is not
documented at plugin-marketplaces.md:172-176; prefer 'metadata.version'"*. The live spec
(`docs.claude.com/en/docs/claude-code/plugin-marketplaces.md`, fetched 2026-09-06) says the
opposite: top-level `version` is the documented "Marketplace manifest version", and
"`description` and `version` are also accepted under `metadata` for backward compatibility."
`Emasoft/ai-maestro-plugins` carries only `metadata.version: 1.0.0` — legacy and stale — which is
the reported symptom: the marketplace version never updates.

A third, smaller inconsistency falls out of the same reading: the two required-field sets disagree
(`validate_marketplace.py:134` `{name,owner,plugins}` vs
`validate_marketplace_pipeline.py:69` `{name,version,plugins}`). Per spec, `owner` is required and
`version` is optional-but-canonical, so the pipeline validator's set is wrong on both counts.

## Note on one premise

The request described the mechanism as an "svg table". It is a generated **Markdown** table
between `<!-- PLUGIN-VERSIONS-START -->` / `<!-- PLUGIN-VERSIONS-END -->` markers. No SVG exists in
either repo (grepped both). Same effect, so the plan is unchanged — recorded only so the next
reader is not hunting for an SVG generator.

## Scope

1. `validate_marketplace_pipeline.py` — MINOR when the update workflow never regenerates the table;
   MINOR when README lacks the markers.
2. Canon emits `templates/scripts/render_readme_table.py`, the workflow step, and the `--check` gate.
3. `validate_marketplace.py` — delete the inverted NIT. `metadata.version` alone is a **NIT**
   (the spec accepts the metadata form for backward compatibility, so it is deprecated-but-valid
   and a WARNING would call a spec-legal manifest an error); **WARNING** is reserved for both keys
   present and disagreeing. Neither key present → no finding, since `version` is optional.
   Revised from a flat WARNING after adversarial review.
4. `validate_plugin.py` — WARNING when a plugin cannot propagate its version to the marketplace
   (no `plugin-updated` dispatch, or no README version badge to update).
5. `skills/cpv-setup-github-marketplace/**` — docs match shipped templates.
6. Canon + agent skills (fixer, migrate, publish-marketplace, doctor) teach the new pipeline.
7. Port the canon to `Emasoft/ai-maestro-plugins` as a PR.

## Acceptance criteria

- [ ] Unit 1 verified: `uv run pytest tests/test_marketplace_readme_table_gate.py -q`
- [ ] Unit 2 verified: `uv run pytest tests/test_render_readme_table_template.py -q`
- [ ] Unit 3 verified: `uv run pytest tests/test_marketplace_version_key.py -q`
- [ ] Unit 4 verified: `uv run pytest tests/test_plugin_readme_chain_gate.py -q`
- [ ] Unit 5 verified: `uv run pytest tests/test_marketplace_doc_template_parity.py -q`
- [ ] Unit 6 verified: `uv run pytest tests/test_canon_docs_readme_table.py -q`
- [ ] Unit 7 verified: `render_readme_table.py --check` exits 0 in the ai-maestro-plugins clone, PR open
- [ ] Full suite green and the repo self-validates before any push

## Approval log
