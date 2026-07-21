# CPV Doctor — deep-diagnostic recipes, menu surfaces & scope-batch specs

This reference holds the verbose detail extracted from
`agents/cpv-doctor-agent.md` so the agent body stays under the CPV
length budget with ZERO function loss. The agent body keeps a one-line
summary + a pointer to each section here; this file is the
authoritative spec for the detail.

## Table of Contents

- [1. D1..D9 deep-diagnostic recipes](#1-d1d9-deep-diagnostic-recipes)
- [2. Menu surfaces & fixed key→action contract (TRDD-4de479a0)](#2-menu-surfaces--fixed-keyaction-contract-trdd-4de479a0)
- [3. Scope-aware batch modes (TRDD-a175f78d)](#3-scope-aware-batch-modes-trdd-a175f78d)
- [4. Report markdown template](#4-report-markdown-template)

## 1. D1..D9 deep-diagnostic recipes

Run for every mode targeting a plugin / marketplace / skill folder
(`single_plugin`, `current_folder`, `github_plugin`, `scan_all_installed`,
`local_marketplace`, `github_marketplace`, `local_scope`, `project_scope`,
`user_scope`, `single_skill`). Skip for the operational modes
(`cache_cleanup`, `install_scanners`, `auto_fix_orphans`,
`quick_health_check`, `dependency_tree`, `add_dependencies`).

Findings keyed by DOC-NN, appended under `## Design-correctness findings`.

### D1 — Shape detection (is the target what it claims to be?)

- Has `SKILL.md` at root but no `.claude-plugin/plugin.json` → `DOC-001 [MINOR] target looks like a bare skill, not a plugin — consider /cpv-pack-components`.
- Has `.claude-plugin/marketplace.json` inside a plugin (Layout C) → `DOC-002 [WARNING] marketplace-in-plugin (Layout C) detected — verify the self-entry name/version match plugin.json`.
- Parent dir containing N plugin children → `DOC-003 [INFO] parent directory with N plugin children — diagnosing each in turn`.

(Required-fields presence is the validator's job; D1 only confirms shape.)

### D2 — Command coverage audit (per `commands/*.md`)

- No `description` in frontmatter → `DOC-010 [MINOR] command <name> missing description`.
- `user-invocable: true` (default) but body empty/stub → `DOC-011 [MAJOR] command <name> is user-invocable but body is empty/stub`.
- Two commands with near-duplicate descriptions (cosine >0.8 on text or jaccard >0.7 on tokens) → `DOC-012 [MINOR] commands <a> and <b> have near-duplicate descriptions — consider consolidating`.
- A feature in `README.md`'s feature list with no command to invoke it → `DOC-013 [MAJOR] README mentions feature "<X>" but no command exists to invoke it`.

### D3 — Skill invocability audit (per `skills/<name>/SKILL.md`)

- `user-invocable: true`/absent but no command body references `/skill-name` AND description triggers are weak → `DOC-020 [MINOR] skill <name> is user-invocable but unreachable — mark user-invocable: false or wire a command`.
- `user-invocable: false` but no agent's `skills:` list references it → `DOC-021 [MAJOR] skill <name> is user-invocable: false AND no agent loads it — dead skill`.
- Two skills with identical `name:` → `DOC-022 [CRITICAL] skill name collision: <name> in <path1> and <path2>`.

### D4 — Design-conflict scan

- Duplicate `name:` across commands ∪ agents ∪ skills (case-insensitive) → `DOC-030 [MAJOR] name collision: <name> in <path1> and <path2>`.
- Shadowing a Claude Code built-in (`/clear`, `/usage`, `/help`, `/compact`, `/loop`, …) → `DOC-031 [MAJOR] command <name> shadows the built-in /<name>`.
- Two agents whose `description:` overlap ≥60% token-jaccard → `DOC-032 [MINOR] agents <a> and <b> have overlapping description triggers — risk of activation collision`.

### D5 — Manifest / marketplace consistency

These four version sources must ALL agree: (1) `plugin.json` `version`; (2) latest git tag (`git tag --sort=-version:refname | head -1`); (3) `marketplace.json` plugin entry (Layout A/C); (4) CHANGELOG.md top section. Any pairwise mismatch → single finding `DOC-040 [MAJOR] version drift: plugin.json=<X> tag=<Y> marketplace=<Z> changelog=<W>`.

Layout A only: verify the marketplace entry's `source.repo` resolves. Dead repo → `DOC-041 [CRITICAL] marketplace.json points at a dead repo: <slug> returns 404`.

### D6 — Canonical-pipeline presence

For each file: present + non-empty + (where applicable) shape-match against the cpv-canonical-pipeline skill's templates.

| Missing file | Finding |
|---|---|
| `scripts/publish.py` | `DOC-050 [MAJOR] missing scripts/publish.py — cannot ship via the canonical pipeline` |
| `scripts/bump_version.py` | `DOC-051 [MAJOR] missing scripts/bump_version.py` |
| `.github/workflows/release.yml` | `DOC-052 [MAJOR] missing release workflow` |
| `.github/workflows/notify-marketplace.yml` (Layout A) | `DOC-053 [MAJOR] missing notify-marketplace workflow — marketplace cache goes stale after publish` |
| `cliff.toml` | `DOC-054 [MINOR] missing cliff.toml — CHANGELOG won't auto-generate` |
| `CHANGELOG.md` | `DOC-055 [MINOR] missing CHANGELOG.md` |
| `reports/` + `reports_dev/` not in `.gitignore` | `DOC-056 [MAJOR] reports/ and reports_dev/ not gitignored — reports may leak private data` |

### D7 — README / CONTRIBUTING coverage

`README.md`: missing Installation section (`^##? +\bInstall(ation)?\b`) → `DOC-070 [MINOR]`; missing Usage/Quick Start → `DOC-071 [MINOR]`; no slash-command list → `DOC-072 [NIT]`; version badge missing/mismatched vs plugin.json → `DOC-073 [NIT]`.

`CONTRIBUTING.md` (only if it exists — optional): missing dev-setup → `DOC-074 [NIT]`; missing test-run instructions → `DOC-075 [NIT]`.

### D8 — Cross-reference integrity

- `references/<file>` named in a `SKILL.md` but absent → `DOC-080 [MAJOR] dangling reference: skill <skill> references <file> which does not exist`.
- `agent: <name>` in command frontmatter but no `agents/<name>.md` → `DOC-081 [MAJOR] command <cmd> declares agent: <name> but the file does not exist`.
- `subagent_type: <name>` in an agent's dispatch prose but no such agent ships → `DOC-082 [MAJOR]`.
- `skills:` entry naming a skill with no `skills/<name>/SKILL.md` → `DOC-083 [MAJOR]`.

### D9 — cpv-the-skills-menu method adoption (advisory)

The-skills-menu method (TRDD-9dd64dbf) decouples skills from agents — agents declare only `skills: [cpv-the-skills-menu]` and load operational skills at runtime. Adoption is NOT mandatory, so findings are advisory.

- Plugin has ≥1 agent + ≥1 operational skill + no `skills/cpv-the-skills-menu/SKILL.md` → `DOC-090 [NIT] not adopted — consider /cpv-the-skills-menu-create`.
- Agent whose `skills:` list has >1 entry → `DOC-091 [NIT] agent <name>'s skills: list has N entries (the method would reduce to 1)`.
- Agent whose `skills:` is exactly `[cpv-the-skills-menu]` but body lacks the dynamic-loading instruction (substring "Use the Skill() tool to load them") → `DOC-092 [MINOR]`.
- Operational skill whose description still names a specific caller agent ("Loaded by X", …) while the plugin HAS adopted the method → `DOC-093 [NIT] rewrite the description to be agent-agnostic`.

When D9 produces findings, the post-scan menu offers "Migrate to cpv-the-skills-menu method" (Surface 4 key `T`), dispatching `cpv-the-skills-menu-create` on the target.

## 2. Menu surfaces & fixed key→action contract (TRDD-4de479a0)

The doctor's menu lifecycle has **four surfaces**, all rendered by the orchestrator (main session) — the agent never re-renders one. The FIXED letter→action maps below are the SOLE routing reference; the orchestrator routes the user's typed key from THESE tables, never from the rendered output. Numbers `1..N` index alphabetically-sorted dynamic lists; letters are stable fixed actions; `M`/`B`/`X` = Main / Back / Exit. An action that doesn't apply is **omitted** (never relettered) — every surviving key keeps its meaning across runs.

### Surface 1 — First-contact "Diagnose what?" menu (`mode: menu`)

Emitted by `/cpv-main-menu` (Diagnose category) BEFORE this agent runs; the user's pick becomes the `<context>` `mode` + `target_path`. Anchored here as the single audit point keeping orchestrator ↔ agent in sync.

| Key | action_id → mode | Key | action_id → mode |
|---|---|---|---|
| `P` | diagnose_single_plugin → single_plugin | `R` | diagnose_single_mcp → single_mcp |
| `H` | diagnose_current_folder → current_folder | `Q` | diagnose_single_monitor → single_monitor |
| `G` | diagnose_github_plugin → github_plugin | `Y` | diagnose_single_output_style → single_output_style |
| `S` | diagnose_scan_all_installed → scan_all_installed | `V` | diagnose_single_lsp → single_lsp |
| `L` | diagnose_local_marketplace → local_marketplace | `C` | cache_cleanup |
| `K` | diagnose_github_marketplace → github_marketplace | `N` | install_scanners |
| `J` | diagnose_project_scope → project_scope | `F` | auto_fix_orphans |
| `O` | diagnose_local_scope → local_scope | `T` | quick_health_check |
| `U` | diagnose_user_scope → user_scope | `W` | dependency_tree |
| `I` | diagnose_single_skill → single_skill | `D` | add_dependencies |
| `E` | diagnose_single_agent → single_agent | `A` | ask_doctor_freeform |
| `Z` | diagnose_single_hook → single_hook | `M`/`B`/`X` | nav (Main / Back / Exit) |

Mnemonics where free (`P`lugin, `G`itHub, `S`can-all, `U`ser, `C`ache, `F`ix-orphans, `D`ependencies, `A`sk); `H`ere/l`O`cal/st`Y`le chosen to free `B`/`M`/`X` for nav. Every mode is a fixed action (no dynamic rows); each letter → exactly one action.

### Surfaces 2 & 3 — SUMMARY + BREAKDOWN (presentation only)

Emitted post-scan from `<report>.summary.json` and `<report>.breakdown.json`. Pure presentation — NO rows-with-keys, NO routing. Summary = counts + verdict + report path; Breakdown = Unicode-bordered recipes × severities matrix. Both emit alongside Surface 4 in the same post-scan turn-end (the orchestrator can queue multiple CMS specs per turn).

### Surface 4 — Post-scan ACTION menu (`mode: menu`)

Emitted post-scan. Rows are OMITTED (not relettered) when "Emitted when" is unmet.

| Key | action_id | Label | Emitted when |
|---|---|---|---|
| `F` | fix_all_findings | Fix ALL findings (auto-route to cpv-plugin-fixer-agent / batch-fix) | `findings > 0` |
| `R` | revalidate_now | Re-validate now (validator + D1..D9) | always |
| `O` | open_report | Open the full markdown report | always |
| `D` | show_breakdown | Re-show the per-recipe breakdown matrix | always |
| `S` | show_summary | Re-show the severity summary | always |
| `T` | migrate_to_skills_menu | Migrate to cpv-the-skills-menu method | only when D9 produced findings |
| `P` | batch_fix_parallel | Batch-fix via parallel sharding (`/cpv-batch-fix`) | only when `— recommend-batch-fix` was emitted |
| `A` | ask_about_findings | Ask the doctor about a finding (free-form) | always |
| `N` | rescan_next_target | Re-scan a different target | always |
| `M`/`B`/`X` | nav | Main / Back / Exit | always |

`F`ix is the canonical default. `D` = brea`D`down (`B` reserved for Back). `T`/`P` reuse Surface-1 letters without collision (different menu). Numbers unused → `renumber: false`, printed keys match this table verbatim.

### Orchestrator emit contract (canonical)

```bash
SLUG="doctor-<scan-id>"
# sidecars 1+2 already on disk as <report>.{summary,breakdown}.json — copy/symlink
# into spec paths; build the ACTION spec from the Surface-4 map, omitting
# conditional rows that don't apply.
python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_menu.py" "$SUMMARY_SPEC"
python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_menu.py" "$BREAKDOWN_SPEC"
python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_menu.py" "$ACTION_SPEC"
# END TURN. The CMS Stop hook emits all three menus via systemMessage.
```

**Iron rule:** NEVER print a menu inline. Never call the legacy format_menu renderer (removed in TRDD-4de479a0 Phase 4) or embed a Unicode-bordered table in prose — both bypass the zero-cost emit and re-enter the cached transcript.

## 3. Scope-aware batch modes (TRDD-a175f78d)

When `<context>` has `mode: batch_scope_diagnose` / `batch_scope_fix` / `batch_scope_same_turn`, you are one of N parallel per-project scope-aware doctors dispatched by `/cpv-batch-scope-diagnose` (or its `-fix` / `-diagnose-and-fix` siblings):

```text
<context>
source: /cpv-batch-scope-diagnose (or sibling)
mode: batch_scope_diagnose | batch_scope_fix | batch_scope_same_turn
scope: user | project | local | full
plugin_index: <int>
target_path: <absolute project folder>
display_name: <project name>
session_dir: /tmp/cpv-batch/<ts>-cpv-doctor-agent/
status_path: <session_dir>/plugin-<plugin_index>.status.json
</context>
```

Surfaces by scope: `user` = `~/.claude/` global extensions; `project` = `<target_path>/.claude/` limited to `git ls-files` (tracked entries); `local` = `<target_path>/.claude/settings.local.json` + files it references; `full` = all three merged PLUS the cross-scope conflict checker.

### Cross-scope conflict checker (scope=`full`)

For every extension name appearing in >1 scope:

| Conflict | Severity | Why |
|---|---|---|
| Same name, 2 scopes, identical content | NIT | Duplicate, no behaviour change, wastes disk. |
| Same name, 2 scopes, different content | MAJOR | Higher-precedence copy silently overrides. |
| Project-scope entry referencing a non-git-tracked file | MAJOR | Disappears on `git clone` of the project. |
| Local-scope entry referencing a file outside `<target_path>/.claude/` | CRITICAL | Fails to load on any other machine. |
| User-scope hook AND project-scope hook on the same event | MINOR | Both fire — verify order matters. |

### Per-mode behaviour

| Mode | Runs |
|---|---|
| `batch_scope_diagnose` | All recipes; NO fixes. |
| `batch_scope_fix` | All recipes + apply NIT & CRITICAL silently; record MAJOR/MINOR in `pending_fixes[]` unapplied. |
| `batch_scope_same_turn` | Read each scope file ONCE; apply NIT, CRITICAL, and safe-MAJOR/safe-MINOR inline; unsafe → `pending_fixes[]`. |

A SAFE recipe has no semantic impact (e.g. delete a byte-identical-modulo-whitespace duplicate). UNSAFE = the user might legitimately have meant the duplicate/drift (e.g. an intentional override).

### Status JSON shape (write to `status_path`)

```json
{
  "schema_version": 1,
  "plugin_index": 0,
  "scope": "full",
  "started_at": "<ISO8601±TZ>", "finished_at": "<ISO8601±TZ>",
  "status_symbol": "✓ | ✗ | ⚠",
  "status_label": "clean | findings | fixed | partial | failed | warning-only",
  "counts": {"critical": 0, "major": 0, "minor": 0, "nit": 0, "warning": 0},
  "before": {"...": 0}, "after": {"...": 0},   // fix modes only
  "conflicts": 0,                              // full mode only
  "pending_fixes": ["<MAJOR/MINOR recipes needing user approval>"],
  "report_path": "<abs-path-to-scope-doctor-report>",
  "notes": "<short summary>"
}
```

### Return contract (scope-aware)

Return EXACTLY one line:

```text
[project-<plugin_index>] <label>: <C>/<M>/<m>/<n>/<w> conflicts=<X> (status: <status_path>)   # diagnose
[project-<plugin_index>] <label>: fixed=<X> pending=<Y> (status: <status_path>)                # fix / same-turn
```

Do NOT render menus. Do NOT mutate `~/.claude/` beyond the iron-rule fix categories. Per-scope reports live under `$MAIN_ROOT/reports/scope-doctor/<ts±tz>-<project>.md`.

## 4. Report markdown template

Write ALL findings (validator + D1..D9) to ONE report at `$MAIN_ROOT/reports/cpv-plugin-diagnoser-agent/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` (`$MAIN_ROOT` = `git worktree list | head -n1 | awk '{print $1}'`):

```markdown
# CPV Doctor report — <target>
Generated: <ISO8601 local timestamp>
Target: <abs-path or owner/repo slug>
Mode: <mode>

## Severity summary
<one-line counts>

## Findings by recipe
| Recipe | CRITICAL | MAJOR | MINOR | NIT | WARNING | Total |
|---|---|---|---|---|---|---|
| Schema validation | … | … | … | … | … | … |
| D1 Shape detection … D9 cpv-the-skills-menu adoption | (one row each) | | | | | |
| **TOTAL** | … | … | … | … | … | … |

## Schema-correctness findings (validator)
<per-finding rows: severity / RC-id / file / line / message>

## Design-correctness findings (D1..D9)
<per-finding rows: severity / DOC-id / file / line / message>

## Verdict
VALID / INVALID
```
