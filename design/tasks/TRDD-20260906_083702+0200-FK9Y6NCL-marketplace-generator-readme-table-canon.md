---
trdd-id: FK9Y6NCL
title: Align generate_marketplace_repo.py with the generated README plugin-table canon
column: dev
created: 2026-09-06T08:37:02+0200
updated: 2026-09-06T11:35:06+0200
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
- [x] Item 6 has a recorded ruling in this card (R2 — KEEP, regions and columns
      are disjoint; R3 enforces the disjointness).
- [ ] Full serial suite green; `--strict` self-validate 0/0/0/0.

## Rulings (settled 2026-09-06, from first-hand reads of the two emitters)

### R1 — the empty-marketplace guard becomes a MANIFEST-SHAPE guard, not an emptiness guard

The candidate in "The blocking design decision" proposed keying the refusal on the
README's current block. Reading `templates/scripts/render_readme_table.py`
first-hand shows a sharper discriminator that needs no markdown parsing.

`plugins = json.loads(...).get("plugins", [])` is empty in exactly three ways:

| manifest state | what it means |
|---|---|
| `"plugins": []` | a legitimately empty marketplace (the scaffold case) |
| `plugins` key absent | a structurally invalid marketplace — the key is required |
| `"plugins"` not a list | an invalid type |

The current code coerces all three to `[]` and then reports the same wrong reason
("no plugins in ..."). The comment's stated hazard — *a rendering bug must not
look like "the marketplace has no plugins"* — is rows 2 and 3, and those are the
ones a shape check catches **precisely**. Row 1 is not a hazard: a marketplace
with no plugins genuinely has no plugins.

So:

- `plugins` key **absent** → refuse (exit 1), message naming the missing key.
- `plugins` present but **not a list** → refuse (exit 1), message naming the type.
- `plugins == []` → **render** an explicit `(no plugins yet)` row and `*0 plugins*`,
  exit 0.

This is a strict tightening of rows 2/3 (today they exit 1 with a misleading
reason; they still exit 1, with the real one) and a widening of row 1 only.

**A README-row heuristic is deliberately NOT added on top.** Its one distinct case
is "the README shows rows and the manifest is now `[]`", i.e. every plugin was
removed on purpose — where refusing would be *wrong*, and where `git` already
records the diff and `--check` already reports the drift. A second mechanism whose
only distinguishable case it decides incorrectly is not belt-and-braces.

`(no plugins yet)` is not invented here: the emitted `update_catalog.py` already
uses that exact placeholder for its own empty case, so the two scaffolded tables
read consistently.

### R2 — `update_catalog.py` is KEPT; the two are complementary, not duplicate

Item 6 asked for a ruling and assumed the two were rival renderers of one region.
They are not. Verified first-hand:

| emitter | region | columns |
|---|---|---|
| `update_catalog.py` (`generate_marketplace_repo.py:726`) | `## Plugins` → next `## ` heading | Plugin \| Description \| **Install** |
| `render_readme_table.py` | between the PLUGIN-VERSIONS markers (`## Plugin Versions`) | Plugin \| **Version** \| **Category** \| Description |

Their regions are disjoint and their columns are disjoint in the fields that
matter: only `update_catalog.py` emits the **install command**, which is a
marketplace README's primary job, and only the canonical renderer emits
**versions** — the whole point of TRDD-4EE90MC1. Deleting either loses
information; folding Install into the canonical renderer would change a table
that is byte-pinned across three copies (template, the doc copy in
`skills/cpv-setup-github-marketplace/references/script-templates.md`, and the
deployed downstream copy), which is far outside this card's blast radius.

So both stay, and this ruling is the recorded reason.

### R3 — the disjointness must be ENFORCED, because it is one line from breaking

`update_catalog.py` replaces everything from `## Plugins` until the next line
starting with `## `. The canonical block opens with the **comment** line
`<!-- PLUGIN-VERSIONS-START -->` and only then `## Plugin Versions`. So if the
versions block is emitted immediately after `## Plugins`, `update_catalog.py`
consumes the START marker (a comment is not a `## ` line) and stops at
`## Plugin Versions` — silently destroying the marker and breaking the renderer's
`--check` gate on the next run. Two guards, both required:

1. Emit the PLUGIN-VERSIONS block **before** the `## Plugins` section in both
   `_readme` and `_readme_local`, so the interaction cannot arise by construction.
2. Harden `update_catalog.py`'s region scan to stop at `<!-- PLUGIN-VERSIONS-START -->`
   as well as at a `## ` heading, so a human reordering their README later cannot
   reintroduce it.

Guard 1 alone is fragile to a later edit; guard 2 alone leaves the emitted default
depending on a scan subtlety. Both, each with its own two-sided test.

### R4 — the downstream copy is REPORTED, never pushed from here

`templates/scripts/render_readme_table.py` and the copy embedded in
`skills/cpv-setup-github-marketplace/references/script-templates.md` change in the
**same commit** (they are byte-pinned by `test_render_readme_table_template.py`).
The third copy lives in another project's PR
(`Emasoft/ai-maestro-plugins` #18) — per the cross-project rule it is not edited
from this session; the divergence is reported to the user with the exact diff to
apply.

## Approval log
