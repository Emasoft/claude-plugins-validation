---
trdd-id: FK9Y6NCL
title: Align generate_marketplace_repo.py with the generated README plugin-table canon
column: dev
created: 2026-09-06T08:37:02+0200
updated: 2026-09-06T11:45:45+0200
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
- [ ] R5: the emitted update workflow SKIPS its write step on a zero-plugin
      manifest and warns — proven two-sided (empty → skipped + README untouched;
      populated → runs normally).
- [ ] The legacy warn fires: `setup_marketplace_automation.py` warns on a target
      carrying an un-hardened `scripts/update_catalog.py`, and is silent on a
      hardened one or none.
- [ ] The R3 negative control asserts `mutated_source != original_source` before
      exec (anti-vacuity).
- [ ] Full serial suite green; `--strict` self-validate 0/0/0/0.
      (Left for the orchestrator — the implementer cannot prove this without the
      manifest regen it is instructed not to run.)

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

**A README-row heuristic is deliberately NOT added on top — and the case it would
cover is AMBIGUOUS, not wrong.** An earlier draft of this ruling claimed the
heuristic's only distinct case ("README shows rows, manifest is now `[]`") is
deliberate removal, where refusing would be wrong. That overstated it: the same
state is *also* produced by an accidentally-emptied manifest, and R1 accepts that
branch silently. The write path really does lose a guard — before R1 an
accidentally-emptied manifest printed `error: no plugins in ...`; after R1 it
blanks the table without comment.

It is accepted anyway, for two reasons worth recording so nobody re-derives the
heuristic believing it was never weighed:

1. An emptied `plugins` array is a glaring, reviewable git diff — the accident is
   visible in exactly the artifact a reviewer reads.
2. **Under `--check` the empty case still exits 1** against a populated README
   ("README table is STALE"), so CI cannot flip red→green on its own.

**Reason 2 is TRUE ONLY BECAUSE OF R5, and an earlier draft of this ruling stated
it unconditionally — which was false.** Scope item 4 puts the renderer in the
update workflow in WRITE mode, unattended, on every `marketplace.json` push. Left
like that, an accidentally-emptied manifest has its README blanked and committed
by CI, and `--check` then **passes**, because the README now matches the empty
manifest: the gate arrives after the thing it would have caught was already
committed. R5 is what restores the property, by keeping the unattended writer away
from the empty case entirely. Reason 1 was never conditional and stands on its own.

`(no plugins yet)` is not invented here: the emitted `update_catalog.py` already
uses that placeholder for its own empty case, so the two scaffolded tables read
consistently. **The row is FOUR cells**, matching this table's header — the
`update_catalog.py` precedent is a three-column table, so copying it verbatim
emits a malformed row:

```text
| *(no plugins yet)* | | | |
```

**The `isinstance` check runs FIRST and unconditionally**, before the emptiness
branch. A *truthy* non-list (`{"a": {...}}`, `"abc"`) never reaches the current
emptiness guard at all — it falls into
`sorted(plugins, key=lambda x: x.get("name", ""))` and dies with a raw
`AttributeError` traceback. So the shape check fixes more than the table above
claims, and the tests must cover an empty dict (falsy) AND a non-empty dict
(truthy) AND a non-empty string.

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

**Reproduced in source, not inferred.** An adversarial review challenged this as
resting on anchor lines rather than the loop body, and offered a competing model
(only table-looking rows are replaced, comments preserved) under which the guard
would guard nothing. Reading `generate_marketplace_repo.py:803-819` settles it —
inside `if in_plugins_section:` the scan appends only a `## ` heading and
`continue`s on **everything else with no append**:

```python
if in_plugins_section:
    if line.startswith("## ") and line.strip() != "## Plugins":
        in_plugins_section = False
        new_lines.append(line)
    # Skip everything else in the plugins section (old table lines)
    continue
```

So the replacement really is wholesale. The canonical block opens with the
**comment** `<!-- PLUGIN-VERSIONS-START -->` and only then `## Plugin Versions`;
emitted immediately after `## Plugins`, the marker is a non-heading line inside
the section and is **dropped**, breaking the renderer's `--check` gate on the
next run.

**The remedy is ONE guard plus one interaction test — an earlier draft mandated
two guards and the ordering one is now dropped.** With the marker-aware stop in
place the ordering constraint is redundant, and it costs README quality: the
install table is a marketplace README's primary content and belongs first.

- **Guard (required):** `update_catalog.py`'s region scan stops at
  `<!-- PLUGIN-VERSIONS-START -->` as well as at a `## ` heading.
- **Ordering (dropped):** emit the PLUGIN-VERSIONS block **after** `## Plugins`,
  where it reads best. **For a NEW scaffold** safety comes from the guard, not
  from layout — see the legacy carve-out below, which is the population the guard
  cannot reach.
- **Interaction test (required, and stronger than either guard stated
  abstractly):** scaffold a marketplace, render the versions table into the
  emitted README, then RUN the emitted `update_catalog.py` against it and assert
  both markers and the whole rendered block survive byte-identical.

That test needs THREE cases, because two of them alone cannot fail for the reason
claimed: the preservation assertion above, a positive control proving the scan
still replaces a normal `## Plugins` region, and a **negative control proving the
PRE-hardening scan destroys the marker**. Without the third, the guard ships with
a test that would pass under the competing model too.

The negative control is written by **source mutation** — read the EMITTED
`update_catalog.py`, strip the marker-stop condition with a targeted replacement,
exec it, assert the marker is destroyed. No vendored copy to rot, no stash, runs
in CI. It MUST assert `mutated_source != original_source` **before** exec:
otherwise a later respelling of that condition makes the replacement a silent
no-op and the control passes while mutating nothing — the same vacuity it exists
to prevent, one level down.

#### The legacy population the guard cannot reach

Guard 2 lives in the **emitted** `update_catalog.py`, so it only ever reaches a
repo scaffolded after this card lands. Verified first-hand:
`setup_marketplace_automation.py`'s `REQUIRED_TEMPLATES` has exactly three entries
(`update-submodules.yml`, `sync_marketplace_versions.py`,
`render_readme_table.py`) and `grep -c update_catalog` on that file is **0**.

So the exposed upgrade path is: a marketplace scaffolded by an OLDER
`generate_marketplace_repo.py` keeps its un-hardened `update_catalog.py`; the new
`setup_marketplace_automation.py` hands it the renderer but never replaces that
script; and because the renderer refuses to insert markers itself
(`if START not in text or END not in text: return 1`) the user hand-adds them —
after which the old scan destroys them on the next push.

`setup_marketplace_automation.py` therefore emits a **loud, non-fatal warning**
when the target has a `scripts/update_catalog.py` whose source lacks the
marker-stop string, naming the hazard and the one-line fix. **Warn, do not
auto-patch:** CPV does not own that file in that repo, and `REQUIRED_TEMPLATES`
excludes it deliberately — silently rewriting a script the tool does not provision
is a larger liberty than the hazard warrants. That reason belongs in a comment at
the check, so nobody later "upgrades" the warning into a rewrite.

### R5 — the UNATTENDED writer never runs on an empty manifest

Scope item 4 puts the renderer in the update workflow in **write mode**, on every
`marketplace.json` push, with no human present. R1 permits blanking a table when
the manifest is legitimately empty — which is right for a human running the
writer deliberately, and wrong for a bot: an accidentally-emptied manifest would
be blanked and committed by CI, and `--check` would then pass against its own
freshly-committed output.

So the emitted update workflow **skips the write step entirely** when the manifest
yields zero plugins, printing a warning that names the manifest:

| situation | behaviour |
|---|---|
| fresh empty scaffold | nothing to write (the generator already emitted the placeholder) — the skip is a no-op and `--check` still passes |
| populated → emptied manifest | README keeps its rows, `--check` goes **red**, a human investigates |

This is the README-row heuristic R1 rejected, placed where it is actually correct.
R1 rejected it **in the renderer**, where it would block a deliberate human edit;
R5 applies the same idea **at the unattended writer**, where the absence of a
human is exactly what makes silent blanking unacceptable. The discriminator is not
the README's contents — it is who is running the tool.

Implemented as a cheap manifest read in the workflow (plugin count == 0), **not**
as a new flag on `render_readme_table.py`: the renderer stays byte-pinned and
flag-free, and a flag there would be the caller-asserted escape hatch this card
already ruled out.

### R4 — the downstream copy is REPORTED, never pushed from here

`templates/scripts/render_readme_table.py` and the copy embedded in
`skills/cpv-setup-github-marketplace/references/script-templates.md` change in the
**same commit** (they are byte-pinned by `test_render_readme_table_template.py`).
The third copy lives in another project's PR
(`Emasoft/ai-maestro-plugins` #18) — per the cross-project rule it is not edited
from this session; the divergence is reported to the user with the exact diff to
apply.

## Approval log
