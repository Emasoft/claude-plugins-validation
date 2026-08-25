---
trdd-id: ef3fc7d8-04f2-438f-b861-66f23d40115b
title: Menu fixed/dynamic split — print_menu.py with skill-menus dirs and minimal dynamic payload
column: todo
created: 2026-05-24T13:07:53+0200
updated: 2026-08-25T17:25:45+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-ef3fc7d8 — Menu fixed/dynamic split (`print_menu.py`)

**Filename:** `design/tasks/TRDD-20260524_130753+0200-ef3fc7d8-menu-fixed-dynamic-split.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## Problem

Menus are emitted zero-token via the claude-menu-system Stop hook (TRDD-4de479a0),
but the QUEUE step still shows a verbose Bash card: the orchestrator inlines the
full JSON menu spec in a heredoc (~15-20 lines visible per menu). The menu CONTENT
is almost always static — only the NUMBERED rows are dynamic (detected files,
paths, plugin names, URLs, project folders, marketplaces). The agent should send
the *minimum*: for a fixed menu, just an index; for a dynamic menu, just the bare
list of detected things.

## User mandate (verbatim intent)

- Every skill's fixed menus are crafted in advance and shipped as JSON in a
  per-skill `skill-menus/` subdir with a number prefix.
- `print-menu.py fixed 6` → load the 6th fixed menu, queue it for the hook. No
  inline JSON.
- Dynamic menus (after picking validate/diagnose/upgrade/…): the agent sends ONLY
  the numbered detected entries. The script auto-appends "type a path" + standard
  nav (ask/back/main/exit) and sorts entries alphabetically. `print-menu.py
  dynamic '<json entries>'`.
- Custom dynamic menus with extra options: `print-menu.py dynamic --from-file <path>`.
- Split: numbers = dynamic positional list; letters = fixed actions/nav (the
  fixed-key contract from TRDD-4de479a0, now operationalized).

## Resolved decisions (approved)

- **Q1 = A:** CPV-side assembly. `print_menu.py` sorts + auto-appends + assembles
  the COMPLETE spec, then queues it through the UNCHANGED claude-menu-system hook.
  CMS stays generic — no cross-project change. (Observably identical to "the hook
  sorts"; the sort just happens in CPV's script.)
- **Q2 = rename:** new canonical `scripts/print_menu.py`; `scripts/cpv_menu.py` is
  retired by the end (no-legacy: one script in the end state). During migration
  `print_menu.py` reuses the bridge core (`resolve_cms_root`, `write_menu`,
  `MenuSystemUnavailable`, `_default_cache_base`) imported from `cpv_menu.py` (no
  duplication); the final phase relocates that core into `print_menu.py` and
  deletes `cpv_menu.py`.
- **Q3 = keys:** dynamic auto-append → `P` type-a-path, `A` ask, `B` back,
  `M` main menu, `0` exit. Entries are numbers `1..N`.
- **Q4 = resolution:** `fixed <N>` resolves `skill-menus/` from env var
  `CPV_SKILL_MENUS_DIR` (the skill body exports it once:
  `export CPV_SKILL_MENUS_DIR="$CLAUDE_PLUGIN_ROOT/skills/<skill>/skill-menus"`),
  with a `--dir <path>` override. `<NN>-*.json` matched by integer prefix
  (zero-padding tolerant).

## CLI contract (`scripts/print_menu.py`)

```
print_menu.py fixed <N> [--dir <skill-menus-dir>]
print_menu.py dynamic '<json>'            # json = array of entries OR {entries,...}
print_menu.py dynamic --from-file <path>  # json file: {entries, extra_options?, header?, footer?}
print_menu.py - | <spec.json>             # low-level raw passthrough (custom full spec)
```

- **fixed N:** load `<dir>/<NN>-*.json` (a complete CMS spec), queue verbatim.
- **dynamic:** build a spec from entries:
  - entries sorted alphabetically (case-insensitive, stable), numbered `1..N`.
  - then `P` type-a-path, then any `extra_options` (letter rows), then nav
    `A`/`B`/`M`/`0`.
  - default header/footer (overridable via `--from-file`).
  - entry item: a plain string (label == action target) OR
    `{"label": "...", "action_id": "..."}` (action_id defaults to the label).
- **raw passthrough** (`-`/file): the existing low-level path, retained for any
  caller that legitimately has a complete spec.
- All paths funnel through `write_menu` (renumber:false default), so the queued
  spec is always fixed-key.

## Dynamic spec shape produced (example)

`print_menu.py dynamic '["~/proj/b","~/proj/a"]'` →

```
1. ~/proj/a
2. ~/proj/b
P. Type a path explicitly
A. Ask
B. Back
M. Main menu
0. Exit
```

(rows 1..N are `action_id` = the entry; P/A/B/M/0 are fixed.)

## Phases (≤5 files each, verify per phase)

1. **Script + tests** — `scripts/print_menu.py` (fixed/dynamic/from-file/raw) +
   `tests/test_print_menu.py`. `cpv_menu.py` untouched (callers keep working;
   print_menu reuses its core via import).
2. **cpv-main-menu** — extract its fixed menus to
   `skills/cpv-main-menu-skill/skill-menus/NN-*.json`; rewrite recipes to
   `fixed N`; convert its dynamic target-pick flows to `dynamic`.
3. **doctor** — `agents/cpv-doctor-agent.md` first-contact/summary/breakdown/
   post-scan → fixed menus + dynamic.
4. **batch family** — 8 `cpv-batch-*` commands + skills + `cpv_batch_orchestrator.py`.
5. **Cleanup** — relocate the bridge core into `print_menu.py`; delete
   `cpv_menu.py` + `test_cpv_menu.py`; update the `validate_security.py` self-scan
   reference; grep proves no `cpv_menu` refs remain; regenerate the integrity
   manifest; full suite + self-scan green; bump version. (Push only on explicit ask.)

## Progress

- **Phase 1 — DONE** (commits `0bfb07d`, `88145cd`): `scripts/print_menu.py`
  with `tests/test_print_menu.py` (36 tests). Self-audit fixed slug
  precedence and dead code.
- **Phase 2 — DONE**: 26 fixed menus extracted to
  `skills/cpv-main-menu-skill/skill-menus/NN-*.json`; recipes rewritten to
  `print_menu.py fixed NN` (no heredocs left; `print_menu.py` used 82× in
  menu-tree.md, 19× in SKILL.md; 0 `cpv_menu.py` refs). `tests/test_cpv_main_menu_specs.py`
  (134 spec round-trip tests) green.
  - **Deviation / important finding (heuristic fix, commit `4f8d6ed`):** Phase 2
    surfaced 9 SkillAudit self-scan NIT findings that were *pre-existing* in
    v2.105.0 (proven by cold-scanning HEAD — identical 9, different line
    numbers). They were masked earlier by the scan cache (cache key =
    content_hash + catalog_hash + __version__; classifier-logic edits are NOT in
    the key, so warm-cache reads served stale clean results). Fixed via 3
    self-guarded `safe_literal` discriminators in `_skillaudit_markdown_context.py`
    (mnemonic-no-crypto-context, benign-recon-no-network-sink, inert-token-in-
    string) — NOT a catalog edit (avoids the cache cascade). Two-sided tests in
    `tests/test_skillaudit_markdown_certain_benign.py` (21). Cold self-scan now
    0/0/0/0 + WARNING-only.
  - **Follow-up worth tracking:** the scan-cache key should fold in the 5
    `_skillaudit_*_context.py` classifier hashes (alongside the catalog hash) so
    classifier edits invalidate the cache without needing `CPV_SCAN_CACHE=0`.
    User-facing correctness is already fine (publish bumps `__version__`), so this
    is a dev-ergonomics hardening, not a release blocker.
- **Phases 3-5 — pending.**

## Test scenarios (Phase 1)

- fixed: correct `NN-*.json` chosen by integer prefix; queued verbatim; missing
  index → fail-fast; missing/!env dir → fail-fast; `--dir` override.
- dynamic inline: alphabetical sort (case-insensitive), `1..N` numbering, P/A/B/M/0
  appended with exact keys+labels, renumber:false preserved, string + object
  entries, empty list (only fixed rows), `extra_options` ordering.
- dynamic --from-file: entries + extra_options + header/footer override + nav.
- raw passthrough unchanged.
- CMS absent → MenuSystemUnavailable (fail-fast).
- caller spec not mutated.

## Security / safety

- No new shell-outs beyond the existing `menu_write.py` subprocess. Entries are
  JSON data placed in spec fields (rendered by CMS), never executed.
- `fixed` only globs inside the resolved `skill-menus/` dir (no arbitrary path).
- Fail-fast everywhere (missing index, bad JSON, absent CMS) — no silent fallback.

## Derived tasks

- Every recipe rewrite must also update that skill's embedded letter→action map
  doc (the routing source of truth) to match the new auto-appended P/A/B/M/0.
- `the-skills-menu` catalog + any menu-invariant tests must reflect `print_menu.py`.
- Self-scan eligibility / absolute-path checks must accept the new
  `skill-menus/*.json` data files.
