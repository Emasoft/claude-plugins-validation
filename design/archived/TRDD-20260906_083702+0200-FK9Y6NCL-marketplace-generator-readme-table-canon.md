---
trdd-id: FK9Y6NCL
title: Align generate_marketplace_repo.py with the generated README plugin-table canon
column: published
created: 2026-09-06T08:37:02+0200
updated: 2026-09-06T15:02:00+0200
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

- [x] A marketplace scaffolded by `generate_marketplace_repo.py` and then given
      one plugin draws **zero** PLUGIN-VERSIONS advisories from
      `validate_marketplace_pipeline.py`.
      (Measured: `SUMMARY: … 0 INFO`. Discriminating — stripping the block from the
      same tree yields `1 INFO`, the marker advisory. **Scope caveat, stated here so
      it is not read as broader than it is:** only the DOCUMENTATION advisory is
      exercised. Check 5b, the workflow-side one, keys on `update-submodules.yml`
      and its three alternatives, none of which a hub-and-spoke scaffold has — check
      2 returns early at `validate_marketplace_pipeline.py:989` before 5b is
      reached. So 5b is structurally unreachable on this scaffold shape, in BOTH
      states, and `0 INFO` is no evidence about it either way. The criterion holds;
      half of it holds because the check cannot fire, not because it was fixed.)
- [x] A freshly scaffolded (empty) marketplace passes its own emitted `--check`
      gate — proven by running the emitted workflow's command, not by reading it.
      (Ran `python3 scripts/render_readme_table.py --check` in the scaffold: exit 0.)
- [x] `actionlint` exits 0 on every emitted workflow.
      (`actionlint -verbose`: 3 files linted, 0 errors — validate.yml,
      update-catalog.yml, validate-readme-table.yml.)
- [x] The renderer emitted by this generator is byte-identical to
      `templates/scripts/render_readme_table.py` (pinned by a test).
      (`test_emitted_renderer_is_byte_identical_to_the_template`.)
- [x] Item 6 has a recorded ruling in this card (R2 — KEEP, regions and columns
      are disjoint; R3 enforces the disjointness).
- [x] R5: the emitted update workflow skips the renderer invocation and the commit
      ONLY on present-list-empty `plugins`, asserting the README is
      **byte-unchanged** — with an absent key and a non-list key both falling
      through to the renderer and failing the job (four cases, not two).
- [x] The legacy warn fires: `setup_marketplace_automation.py` warns on a target
      carrying an un-hardened `scripts/update_catalog.py`, and is silent on a
      hardened one or none.
- [x] The R3 negative control asserts `mutated_source != original_source` before
      exec (anti-vacuity).
- [x] Full serial suite green; `--strict` self-validate 0/0/0/0.
      (Suite: 13647 passed, 7 skipped, `SUITE_EXIT=0`, read from the captured file
      — never from a task notification, whose exit code is the last command in the
      chain. Self-validate, cache-cold: `CRITICAL=0 MAJOR=0 MINOR=0 NIT=0
      WARNING=15`, exit 0. The 15 WARNINGs were READ, not counted: the only
      skipped-check hit is the pre-existing `claude-menu-system` dependency
      advisory, which matches the prior baseline exactly. A skipped check and a
      passing check produce the same exit code, so the count alone proves nothing.)

## Verification (orchestrator, against the DIFF — not the implementer's report)

Every gate checked first-hand before the commit:

| Gate | Evidence |
|---|---|
| G1 properties 1-3 | The guard is a `python -c` predicate INSIDE an `if`, so its exit status is consumed and never aborts under `bash -e`. `sys.exit(0 if isinstance(p, list) and not p else 1)` sends a missing key, a non-list, and malformed JSON to the `else` branch, which runs the renderer. BOTH branches write `$GITHUB_OUTPUT`, so unwritten-vs-false cannot arise, and the gate is `!= 'true'` — skip is opt-in. Property 3 is only PARTLY met: `2>/dev/null` on the predicate discards a JSON parse error, but the renderer then runs and emits R1's own diagnostic, so the reason still reaches the log. |
| G2 route | Workflow, matching R5. The "renderer route is better, do not correct it backwards" branch did not apply. |
| G3 collection | `27 tests collected in 0.05s`, exit 0. Pyright's unresolved imports on that file are a path artifact for an untracked test, not an ImportError — which would have made all 27 silently UNCOLLECTED and read as green. |
| no dead emitters | `_render_versions_block` → :264, :367; `_renderer_source` → :1366. |
| legacy warn | Present and WARN-only, and it documents its own known FALSE NEGATIVE: a script merely mentioning the marker in a comment reads as hardened. |
| R3 anti-vacuity | `assert mutated != original, "mutation was a no-op — this control would pass vacuously"`. |
| ruff / mypy | Clean on all five changed sources. |

**Using `python -c` rather than the `jq` I specified was better than the spec.** It
removes the `jq`-absent case and the `jq -e`-exits-1-on-false trap that made the
snippet in an earlier draft of G1 wrong.

**Manifest ordering, and a trap worth recording:** `_plugin_compute_hashes.py`
enumerates `git ls-files`, so a NEW file must be `git add`-ed BEFORE the regen or
it is absent from the skip list. Regenerating in the obvious order left the count
at 1206; staging first took it to **1207**.

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

**That warning is necessary and NOT sufficient, and the ownership argument above
does not cover the gap.** Setup runs **once**, at adoption; the destruction
happens later, on the first `marketplace.json` push after the user hand-adds the
markers — separated from the warning by an arbitrary interval and by the user's
own intervening edit. It will not be on screen when it matters. The ownership
argument is an argument against *patching* the file, not against *detecting* it.

FOLLOW-UP (deliberately NOT in this card's implementation pass, to stop a spec
from moving under an agent already building it): `render_readme_table.py` already
reads the README and already fails on missing markers, and that error is the one
the user actually lands on. It should detect an un-hardened `update_catalog.py`
sibling and name it as the probable cause, turning an opaque "no markers" error
into a diagnosis. It runs on every push, not once. It touches the byte-pinned
renderer, so all three copies move together — done in the tree after the
implementation lands, not specified at the agent mid-flight.

**The risk window is smaller than that framing suggests, and stating it correctly
matters because it is what makes the deferral cheap rather than merely
convenient.** Exposure does not begin when this card ships; it begins when a
LEGACY marketplace adopts the renderer, and that requires a human to hand-add the
markers first. No legacy repo has markers today, so nobody is exposed right now.
The deferral costs approximately nothing — but it stops costing nothing the day
the first legacy adoption happens, which is why the follow-up is recorded here
rather than left to memory.

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

Implemented as a manifest read in the workflow, **not** as a new flag on
`render_readme_table.py`: the renderer stays byte-pinned and flag-free, and a flag
there would be the caller-asserted escape hatch this card already ruled out.

**The skip condition is a THREE-WAY test, not a count — an earlier draft of R5
said "plugin count == 0" and that was a defect.** A missing `plugins` key and a
non-list `plugins` both read as zero under any naive count (`.plugins // [] |
length` is 0 for both), so the two cases R1 had just promoted to loud exit-1
failures would have become silent workflow skips: R1's own inversion reintroduced
one layer up, on the only path with no human watching. A renamed or retyped
`plugins` key would print "no plugins, skipping", exit 0, and leave the README
carrying a now-unbacked table.

Skip **only** when all three hold — the same discrimination R1 mandates in the
renderer:

- `plugins` is PRESENT, and
- it IS a list, and
- it is EMPTY.

Anything else (absent key, non-list, malformed JSON) falls through and invokes the
renderer, so R1's shape guard fires and the job goes red.

**What is skipped is the RENDERER INVOCATION AND THE COMMIT — nothing else.** Every
other step in that workflow runs unchanged; the job is not skipped. Scope item 4's
"run it before the change-check so a README-only diff still commits" is unaffected,
because on the empty path there is no diff to commit. The acceptance assertion is
that **the README is byte-unchanged** — not merely that nothing was committed, since
an implementation that lets the writer blank the README and only suppresses the
commit would pass a no-commit assertion while defeating R5 entirely.

**Attribution, stated precisely:** on a populated→emptied manifest it is not the
skip that turns CI red — the skip is silent by design. It is the `--check` gate
from scope item 5, running on the unchanged README, that reports drift. The two
are both required, and neither is implemented yet; this paragraph specifies
behaviour, it does not describe behaviour that exists.

### R4 — the downstream copy is REPORTED, never pushed from here

`templates/scripts/render_readme_table.py` and the copy embedded in
`skills/cpv-setup-github-marketplace/references/script-templates.md` change in the
**same commit** (they are byte-pinned by `test_render_readme_table_template.py`).
The third copy lives in another project's PR
(`Emasoft/ai-maestro-plugins` #18) — per the cross-project rule it is not edited
from this session; the divergence is reported to the user with the exact diff to
apply.

## Review-time gates (the orchestrator checks these against the DIFF, not the report)

The correction cycle to the implementer is CLOSED. Two known defects were found
after closing it, and both are cheaper to fix in the tree than to send as a fourth
mid-flight spec change. They are recorded here so they cannot be lost to a
compaction, and because a reviewer who does not know to look for them will read
past both.

**G1 — the R5 guard must not abort the step, and must not swallow the reason.**
Stated as PROPERTIES to check, deliberately not as a recipe. An earlier draft of
this gate carried a concrete `jq -e` snippet, and that snippet was wrong in three
ways at once — no `id:` on the guard step (so `steps.<id>.outputs.skip` evaluates
empty, `'' != 'true'`, the renderer always runs and R5 never fires), an undefined
`$MJ` (jq then reads **stdin** and the step HANGS, burning the job timeout with no
diagnostic), and a `2>&1` that discards jq's parse error — the exact reason R1
exists to surface. A gate is read as authoritative, so a defective recipe inside
one is worse than no recipe: the reader pastes it instead of thinking. The
properties:

1. The guard must not abort the step when its condition is FALSE. GitHub's default
   `run:` shell is `bash -e {0}` and `jq -e` exits 1 on a false result — which is
   the NORMAL populated-marketplace case — so a bare `jq -e` statement kills the
   workflow on every push that has plugins.
2. The skip decision must DEFAULT TO RUNNING THE RENDERER whenever the guard does
   not produce a clean true. The decision rule is sound in every failure mode
   (false → 1, malformed JSON → 5, missing file → 2, jq absent → 127; all
   correctly mean "do not skip") — what must be structural is that "anything other
   than a clean true runs the renderer", rather than a rule the reader applies.
3. The manifest's own error text must SURVIVE to the log. R1's whole purpose is
   failing loudly with the reason.

**G2 — "README byte-unchanged" must not be satisfied by a string match, and there
are THREE implementations, not the two an earlier draft listed.** A YAML-text
assertion cannot detect a logic error; extracting the guard's shell and running it
against a manifest fixture can. But the option most likely to be right is the one
that earlier draft missed: **put the predicate in the renderer, not the workflow.**
A `should_skip(manifest_path) -> bool` is directly unit-testable against all four
manifest shapes, the workflow just branches on an exit code, and G1's entire
failure class disappears with the shell.

R5's stated rationale for choosing the workflow — keeping the renderer "flag-free"
— **conflated two different things**, and an earlier draft of this paragraph then
stated the distinction badly enough that it would not survive reuse. It said a
read-only predicate "asserts nothing and derives its own answer". Test that
against `--allow-empty`, which can equally be framed as the caller reporting a
fact about their own repo: the objection would be "no, the caller *asserts*",
which merely restates the conclusion.

**The discriminator that holds is WHO SUPPLIES THE VALUE THE DECISION TURNS ON.**
With `should_skip(path)` the tool reads the manifest and computes the verdict; no
caller input can change it for a given repo state. With `--allow-empty` the same
repo state yields a different verdict depending on what the caller passed. That
caller-controlled divergence from the tool's own reading is the escape hatch — not
"assertion" as a speech act. The reframing also correctly PERMITS `--check`, which
changes what the tool does with its verdict rather than the verdict itself.

The real cost of the renderer route is only that it moves three byte-pinned copies
— a genuine trade, but not the one originally recorded.

**Settled by the implementation:** the workflow route was taken, matching R5 as
ruled. So R5 stands as written and this section records a road not taken, not a
supersession. Had the renderer route been taken it would have been acceptable and
preferred; a future reader must not read R5's "not as a new flag on
`render_readme_table.py`" as banning a read-only predicate there.

At review: if the implementation put the predicate in the renderer, that is
**BETTER and the gate is satisfied differently** — do not "correct" it back toward
the workflow. If it asserts on YAML text, either convert it to extract-and-run or
downgrade the acceptance criterion here to say plainly that it is a text
assertion. Never leave a text assertion standing under a criterion phrased as
behaviour.

**G3 — confirm the new test file actually imports.** The diagnostics stream shows
`tests/test_marketplace_generator_readme_table.py` failing to resolve
`generate_marketplace_repo` and `setup_marketplace_automation`. Probably Pyright's
path config for an untracked file — but an ImportError makes the whole new suite
silently **uncollected rather than failed**, which reads as green. Run it and read
the collected count.

## Approval log
