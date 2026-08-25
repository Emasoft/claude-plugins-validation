---
trdd-id: 81e7fa34
title: TRDD-81e7fa34 — Doctor depth + menu rendering fix
column: superseded
superseded-by: TRDD-4de479a0
updated: 2026-08-25T17:25:27+0200
---

# TRDD-81e7fa34 — Doctor depth + menu rendering fix

**TRDD ID:** `81e7fa34-f5c7-452b-a928-1c737b303b26`
**Filename:** `design/tasks/TRDD-81e7fa34-f5c7-452b-a928-1c737b303b26-doctor-depth-and-menu-rendering.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)
**Status:** In progress
**Created:** 2026-05-16

---

## Context

User exercised the v2.89.0 main-session menu orchestrators end-to-end
(`/cpv-doctor` → row 2 current-folder → row 3 fix-MINOR → row 0 exit)
and reported four concrete defects:

1. **Menu shows greyed-out empty rows.** When the work agent returns
   findings with `CRITICAL=0, MAJOR=0`, the post-scan menu's first two
   rows render as `— (no CRITICAL findings)` / `— (no MAJOR findings)`.
   The user wants disabled rows DROPPED entirely with the remaining
   rows renumbered.
2. **No severity-summary table after the scan.** The orchestrator goes
   straight from the work-agent's prose return to the post-scan menu.
   The user expects an explicit Unicode-bordered summary table
   (CRITICAL / MAJOR / MINOR / NIT / WARNING counts + verdict) BEFORE
   the menu.
3. **Doctor is too shallow.** The v2.89.0 `cpv-doctor-agent` dispatch
   essentially runs `validate_plugin.py` — same code path as
   `/cpv-validate-plugin`. The user wants the doctor to do DEEPER
   diagnostic analysis: shape detection (plugin vs skill vs marketplace
   vs half-formed), command coverage audit, skill invocability audit,
   design-conflict scan, manifest/marketplace consistency,
   canonical-pipeline presence, CONTRIBUTING/README coverage. Validators
   check schema correctness; the doctor must check design correctness.
4. **Menu right-edge jagged.** Cell padding uses `len()` which returns
   codepoint count, not display columns. Box-drawing characters and
   wide-Asian characters break alignment.

All four defects share the same surface — the v2.89.0 main-session
menu-orchestrator command bodies and the `cpv-doctor-agent` definition.

User scope decision (recorded via AskUserQuestion 2026-05-16):

- **All four fixes ship together** in v2.89.3 (no split into
  rendering-only patch + doctor-depth patch).
- **New helper `scripts/format_menu.py`** owns menu/summary rendering;
  all 4 command bodies call it via Bash rather than baking inline
  Python.

## Architecture

### Phase A — `scripts/format_menu.py` (new)

Single CLI entry-point with two subcommands:

```
python3 scripts/format_menu.py menu   <json_payload>
python3 scripts/format_menu.py summary <json_payload>
```

**`menu`** mode:

Input JSON shape:

```json
{
  "header": "What now?",
  "rows": [
    {"key": "1", "label": "Fix all CRITICAL findings", "disabled": true},
    {"key": "2", "label": "Fix all MAJOR findings",    "disabled": true},
    {"key": "3", "label": "Fix all MINOR findings (2 will be fixed)"},
    {"key": "5", "label": "Re-validate now (no fixes)"},
    {"key": "6", "label": "Open the report in your editor"},
    ...
    {"key": "0", "label": "Exit"}
  ],
  "footer": "Type a number to choose:",
  "renumber": true
}
```

Behavior:

- Drop every row with `disabled: true` BEFORE rendering.
- If `renumber: true` (default), assign new sequential keys to the
  remaining rows in input order — except for the special keys `"0"`
  (Exit/Cancel) and `"A"` (free-form / Ask) which keep their literal
  key. Maps the new key → action_id pairs back via the second JSON
  block on stderr so the orchestrator knows what action each
  user-visible number actually triggers.
- Compute each cell's display width via display columns (wcwidth if
  installed; fallback to `unicodedata.east_asian_width` heuristic +
  per-block lookup for box-drawing / emoji presentation selector).
- Pad each cell to the max display width of any value in its column.
- Render the standard `┏━┳━┓ │ │ ┡╇┩ │ │ └─┴─┘` Unicode-bordered
  table on stdout.

**`summary`** mode:

Input JSON shape:

```json
{
  "title": "Findings summary",
  "counts": {"critical": 0, "major": 0, "minor": 2, "nit": 0, "warning": 1},
  "verdict": "VALID",
  "report_path": "reports/plugin-diagnoser/20260516_134027+0200-claude-plugins-validation.md"
}
```

Behavior:

- Render a 5-column Unicode-bordered table with columns
  `Severity | Count | Verdict`.
- Color-coded via ANSI escapes (CRITICAL=red, MAJOR=yellow,
  MINOR=blue, NIT=cyan, WARNING=magenta) when stdout is a TTY,
  monochrome otherwise.
- Include `Verdict: <VALID|INVALID>` in the table footer.
- Print the report path as a clickable file line beneath the table.

Implementation notes:

- Pure stdlib (use `unicodedata.east_asian_width` for width). If
  `wcwidth` happens to be installed, prefer it as a faster/correct
  oracle. No new dependency added to `pyproject.toml`.
- The script is self-contained — no imports from CPV's own modules
  so it stays fast (the menu render is the hot path in the user-facing
  loop).
- Exit codes: 0 on success, 2 on bad JSON, 3 on bad shape.

### Phase B — Rewrite all 4 command bodies

`commands/cpv-doctor.md`, `commands/cpv-fix-validation.md`,
`commands/cpv-fix-marketplace-validation.md`,
`commands/cpv-cache-optimize.md`:

For each command:

1. Replace the inline Unicode menu in `Step 1` (first-contact menu)
   with an instruction to call `format_menu.py menu` with the row
   set. Show one canonical "example invocation" Bash block so the
   orchestrator knows exactly what JSON to pipe in. The orchestrator
   prints the helper's stdout verbatim and uses the action_id
   mapping (from stderr) when parsing the user's reply.
2. After dispatching the work agent and receiving findings, add an
   explicit Step "Render the summary table" that calls
   `format_menu.py summary` with the parsed counts/verdict.
3. Replace the inline post-scan menu in `Step 4` with the same
   pattern (call `format_menu.py menu` after marking rows that have
   zero applicable findings as `disabled: true`).
4. Update the routing table to use action_ids (e.g.
   `fix_at_critical`, `revalidate`, `open_report`) instead of bare
   row numbers, since `format_menu.py` may renumber.

### Phase C — Expand `agents/cpv-doctor-agent.md`

Add eight concrete diagnostic recipes the doctor MUST run beyond
`validate_plugin.py`:

| # | Recipe | What it produces |
|---|---|---|
| D1 | **Shape detection** — is the target a plugin (`.claude-plugin/plugin.json`), a bare skill (`SKILL.md` at root), a marketplace (`.claude-plugin/marketplace.json`), or a parent dir with N children? | A `target_shape` field + suggestions when the shape is ambiguous |
| D2 | **Command coverage audit** — for every `*.md` in `commands/`, is its functionality also reachable via at least one agent/skill/CLI? Are there commands marked `user-invocable: true` that have no slash-command alias? | List of unreachable commands + redundant duplicates |
| D3 | **Skill invocability audit** — for every `skills/*/SKILL.md`, is `user-invocable` set explicitly? If `user-invocable: true` but no command references it, flag as "orphaned". If `user-invocable: false` but no agent's `skills:` list references it, flag as "dead". | List of orphaned + dead skills |
| D4 | **Design-conflict scan** — duplicate `name:` across commands/agents/skills (case-insensitive). Collisions with Claude Code built-ins (`/clear`, `/usage`, etc.). Two agents with overlapping descriptions that risk activation collision. | Pairwise conflict list |
| D5 | **Manifest/marketplace consistency** — `plugin.json.version` vs latest git tag vs marketplace.json entry vs CHANGELOG.md latest section. All four MUST agree on the same version string. | Per-source version table + drift flags |
| D6 | **Canonical-pipeline presence** — `scripts/publish.py`, `scripts/bump_version.py`, `.github/workflows/release.yml`, `.github/workflows/notify-marketplace.yml`, `cliff.toml`, `CHANGELOG.md`. Missing files → flag. | Per-file presence + line-count delta vs canonical |
| D7 | **CONTRIBUTING/README coverage** — README.md has install instructions, basic usage example, link to commands list. CONTRIBUTING.md (if present) covers dev-setup, test-run, publish flow. | Section-presence checklist |
| D8 | **Cross-reference integrity** — every `references/<file>` referenced from a SKILL.md actually exists. Every `agent: <name>` referenced from a command frontmatter actually exists. Every `subagent_type: <name>` referenced from agent prose actually exists. | List of dangling references |

Each recipe outputs **structured findings** with a stable `recipe_id`
(D1..D8), a severity (CRITICAL/MAJOR/MINOR/NIT/WARNING), a `file`,
and a `line` (when applicable). These flow into the same Markdown
report `validate_plugin.py` writes — so the post-scan menu's
"Fix at severity" routes can act on them via the same plugin-fixer
pipeline. Findings get rule_ids in the `DOC-NNN` namespace
(DOC-001..DOC-008) to distinguish them from validator findings.

The doctor STILL runs `validate_plugin.py` first (schema correctness
is a prerequisite). The eight D1..D8 recipes run AFTER and append to
the same report.

### Phase D — Tests

- `tests/test_format_menu.py` (NEW, ~20 tests):
  - Menu rendering: drop disabled rows, renumber, keep `"0"`/`"A"`
    literal, display-width padding, JSON parse errors, action_id
    stderr mapping.
  - Summary rendering: counts table, verdict line, color/no-color
    auto-detection, report-path line.
  - Width helper: ASCII, box-drawing, Asian-wide chars, emoji.
- `tests/test_v2_89_3_command_body_refactor.py` (NEW): asserts each
  of the 4 command bodies calls `scripts/format_menu.py` for both
  the first-contact and post-scan/post-fix menus, AND for the
  summary table after dispatch.
- `tests/test_cpv_doctor_recipes.py` (NEW): asserts
  `agents/cpv-doctor-agent.md` documents recipes D1..D8 (one section
  header per recipe, output schema referenced).
- Updated `tests/test_agent_model_tiers.py`: still passes after the
  command body rewrites (the structural assertions stay correct).

## Files to modify

| File | Action |
|---|---|
| `scripts/format_menu.py` | NEW |
| `commands/cpv-doctor.md` | Rewrite (Phase B) |
| `commands/cpv-fix-validation.md` | Rewrite (Phase B) |
| `commands/cpv-fix-marketplace-validation.md` | Rewrite (Phase B) |
| `commands/cpv-cache-optimize.md` | Rewrite (Phase B) |
| `agents/cpv-doctor-agent.md` | Expand with D1..D8 recipes |
| `tests/test_format_menu.py` | NEW |
| `tests/test_v2_89_3_command_body_refactor.py` | NEW |
| `tests/test_cpv_doctor_recipes.py` | NEW |

## Verification

```bash
cd "${CLAUDE_PLUGIN_ROOT}"
uv run pytest tests/test_format_menu.py tests/test_v2_89_3_command_body_refactor.py tests/test_cpv_doctor_recipes.py -v
uv run pytest tests/ -q --tb=short
uv run ruff check .
PLUGIN_SKIP_GITHUB_INTEGRITY=1 CPV_SKIP_GITHUB_INTEGRITY=1 \
  uv run python scripts/validate_plugin.py . --strict
```

## Release

```bash
uv run python scripts/publish.py --patch    # v2.89.2 → v2.89.3
```

## Cross-references

- v2.89.0 menu architecture: `TRDD-bcbceeed-...-menu-orchestrator-haiku-main-session.md`
- v2.89.2 id-reuse fix: commit `c91c823` on master
- Test driving this TRDD: end-to-end `/cpv-doctor` run on 2026-05-16
  that surfaced all four defects in a single interactive session.

## Approval log

- 2026-08-25T17:25:27+0200 — CLOSED as superseded by the CPV session (board drain; authority delegated by USER 2026-08-25). superseded-by: TRDD-4de479a0. shipped v2.89.3 then menu rendering re-architected; format_menu.py removed (batch_ah)
