---
trdd-id: FK9Y6NCL
title: Align generate_marketplace_repo.py with the generated README plugin-table canon
column: todo
created: 2026-09-06T08:37:02+0200
updated: 2026-09-06T08:37:02+0200
current-owner: main-session
task-type: refactor
scope: project
project-id: claude-plugins-validation
parent-trdd: 4EE90MC1
relevant-rules: []
npt: []
eht: []
---

# Align generate_marketplace_repo.py with the generated README plugin-table canon

## Why

TRDD-4EE90MC1 adopted the generated README plugin table (a block between
`<!-- PLUGIN-VERSIONS-START -->` / `<!-- PLUGIN-VERSIONS-END -->`, rendered from
`.claude-plugin/marketplace.json` by `scripts/render_readme_table.py`, with a
`--check` CI drift gate) as CPV canon, and added two advisories for a marketplace
that lacks it.

CPV has **two** marketplace scaffolding paths, and only one was updated:

| Path | Status |
|---|---|
| `scripts/setup_marketplace_automation.py` | copies the canonical `render_readme_table.py` — ON canon |
| `scripts/generate_marketplace_repo.py` | still emits `update_catalog.py` + `update-catalog.yml` — OFF canon |

So a marketplace scaffolded by the second generator is **expected to draw** the
two new advisories once it has plugins: **CPV flagging its own scaffold as
off-canon**. That expectation is INFERRED from reading the generator and the two
checks — it has not been measured by running the pipeline validator against a
real scaffold, which is why acceptance criterion 1 requires exactly that run. The
advisories are INFO / zero-weight and never block a publish, so nothing is broken
— but the canon says one mechanism, not two.

Verified first-hand (`scripts/generate_marketplace_repo.py`):

- `_update_catalog_script` (:726) emits a README renderer that rewrites a
  **heading-delimited** region (`## Plugins` → next `##`), with **no version
  column** and **no PLUGIN-VERSIONS markers**.
- `_update_catalog_workflow` (:639) runs it on a `marketplace.json` push. There
  is **no `--check` drift gate** anywhere.
- Emission sites: workflow :1191, script :1197.
- The README itself is generated in **two** more places — `_readme` (:195) and
  `_readme_local` (:283) — each with its own hand-built plugin table and its own
  `(no plugins yet)` placeholder. So the table shape lives in **three** places in
  this one file.

## The blocking design decision (settle this FIRST)

The canonical renderer **refuses an empty plugin list** by design:

```python
if not plugins:
    # Refuse to blank the table on an empty or unexpected marketplace file:
    # a rendering bug must not look like "the marketplace has no plugins".
    print(f"error: no plugins in {mj_path}", file=sys.stderr)
    return 1
```

But `generate_marketplace_repo.py` scaffolds an **empty** marketplace. A naive
swap therefore ships a scaffold whose own `--check` CI gate fails on its first
run — a scaffold that cannot pass its own canonical lint, which is precisely the
defect the emitted `update-catalog.yml` comment already warns about.

Do **not** resolve this by adding an `--allow-empty` flag: a caller-asserted
escape hatch is the self-declared-suppression shape CPV has ruled against.

A candidate that keeps the blank-protection intact and is precisely testable:
refuse only when the README's **current** block is non-empty while the manifest
yields zero plugins (that is the rendering-bug case); render an explicit
`(no plugins yet)` row when both are empty (that is the scaffold case). This
needs its own two-sided tests.

**Constraint:** `templates/scripts/render_readme_table.py` is pinned
byte-identical by `test_render_readme_table_template.py` to the copy embedded in
`skills/cpv-setup-github-marketplace/references/script-templates.md`, and a
byte-identical copy is already deployed in
<https://github.com/Emasoft/ai-maestro-plugins/pull/18>. Any renderer change must
update the doc copy in the same commit and be reflected in that PR, or the two
drift.

## Scope

1. Settle the empty-marketplace behaviour above, with two-sided tests.
2. Emit `scripts/render_readme_table.py` from `generate_marketplace_repo.py`,
   byte-identical to `templates/scripts/render_readme_table.py`.
3. Emit the README with the PLUGIN-VERSIONS markers — in **both** `_readme` and
   `_readme_local`.
4. Point the update workflow at the renderer, running it **before** the
   change-check so a README-only diff still commits.
5. Emit a `validate-readme-table.yml` running `--check` on push/PR.
6. Decide the fate of `update_catalog.py`: remove it (and `test_audit_fix_b14.py`'s
   `update_catalog` cases) or keep it as a deliberate second mechanism with a
   recorded reason. Do not leave it undecided — two mechanisms with no ruling is
   how the drift returns.
7. Re-run the two advisories against a freshly scaffolded marketplace and prove
   they no longer fire.

## Acceptance criteria

- [ ] A marketplace scaffolded by `generate_marketplace_repo.py` and then given
      one plugin draws **zero** PLUGIN-VERSIONS advisories from
      `validate_marketplace_pipeline.py`.
- [ ] A freshly scaffolded (empty) marketplace passes its own emitted `--check`
      gate — proven by running the emitted workflow's command, not by reading it.
- [ ] `actionlint` exits 0 on every emitted workflow.
- [ ] The renderer emitted by this generator is byte-identical to
      `templates/scripts/render_readme_table.py` (pinned by a test).
- [ ] Item 6 has a recorded ruling in this card.
- [ ] Full serial suite green; `--strict` self-validate 0/0/0/0.

## Approval log
