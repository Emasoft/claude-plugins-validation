---
trdd-id: d1f74670-539c-488e-8145-eb8a964315f4
title: CPV doctor user-scope recipes — stub files, stale years, dead refs, namespace correctness
column: complete
created: 2026-05-18T23:19:57+0200
updated: 2026-08-26T05:54:23+0200
---

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-08-25

Implemented in `scripts/cpv_doctor_user_scope.py`: `check_ghost_dispatch` (D9,
delegates to `validate_xref._extract_dispatch_refs`/`_resolve_dispatch_ref`
from TRDD-25b9be90 — that TRDD is `column: complete`/archived, so its engine
is available), `check_stub_files` (D10), `check_stale_year` (D11),
`check_dead_script_refs` (D12), `check_namespace_correctness` (D13, usable at
both user-scope and plugin-scope), plus `run_user_scope_recipes()` running
all five. `references/finding-codes.md` registers all 7 RC codes.
`agents/cpv-doctor-agent.md` has a new "User-scope recipes D9..D13" section,
explicitly gated to `mode=user_scope`, distinguished from the pre-existing
D1..D9 design-correctness pass by its `RC-*` (not `DOC-*`) code namespace.
22 real two-sided tests in `tests/test_doctor_user_scope_recipes.py`, all
green; ruff + mypy clean.

**NOT done (explicitly out of scope for this bounded pass, not blocked by
any banned file):**
- `scripts/format_menu.py` does not exist in this repo anymore (only a stale
  `.pyc` remains — superseded by the externalised `claude-menu-system` Stop-hook
  renderer per CLAUDE.md's "Menu architecture" section). The acceptance
  criterion naming it is stale; there is no breakdown-chart script left to wire.
- `scripts/validate_local_scope.py` (the script the doctor agent's mode table
  says to invoke for `mode=user_scope`) does NOT yet call
  `run_user_scope_recipes()` — the module is written and tested standalone but
  not wired into the live `/cpv-doctor` option-9 pipeline. Not a banned file;
  descoped for time. **NEXT ACTION for whoever resumes this card:** add a call
  to `cpv_doctor_user_scope.run_user_scope_recipes(Path.home()/".claude", report)`
  inside `validate_local_scope.py`'s `main()`/`validate_local_scope()` when the
  target is `~/.claude` (or add a dedicated `--user-scope-recipes` flag), then
  re-run `/cpv-doctor` option 9 against the real `~/.claude` to satisfy the
  card's last acceptance box (matching the 2026-05-18 audit results).
- "Target ~35 new tests" — shipped 22 real tests covering every function's
  core branches (positive + negative per rule); not padded to 35.

**2026-08-25 (D13 recalibration retry)** — verified `check_namespace_correctness`
in `scripts/cpv_doctor_user_scope.py` against this card's D13 row 8 FP guard
(a prior worker's session already landed the fix; this pass re-verified it
end-to-end rather than redoing it). `_extract_skill_mentions` counts a
reference ONLY for: `Skill({skill:"name"})` calls (regardless of fence — a
literal tool invocation, not example code, per row 8's "Skill(" marker),
plausible slash-command invocations via `_SKILL_SLASH_RE` (start-of-line or
after-whitespace `/name`, `(?!\S)` after the name so `/usr/bin` and `a/b`
never match, and fenced-code-block lines are skipped), and frontmatter
`skills:`/`allowed-skills:` list entries. Backtick-only mentions (a bare
`` `name` `` with no `Skill(`, no leading `/`, no `skills:` list) were never
extracted by any pattern, so they already produce zero findings — verified
directly against the module (see report). Confirmed two-sided: prose
`/usr/bin`, prose `a/b`, and a backtick-only mention → 0 mentions; a real
`Skill({skill:"ghost-x"})`, a real line-start `/ghost-cmd`, and a
`skills: [ghost-y]` frontmatter entry → each fires. 31/31 tests green
(`test_doctor_user_scope_recipes.py` + `test_local_scope_user_recipes_wiring.py`),
ruff + mypy clean on the touched files. No code change was needed — the
prior worker's landed fix already satisfies the card's spec; this entry
documents the re-verification. Report:
`reports/board-drain-impl/20260825_230311+0200-d13-recal-retry.md`.

**2026-08-26 — orchestrator central verification on the REAL `~/.claude`
corpus; three further D13 accuracy fixes landed.** The delegated pass was
verified rather than trusted, and the real-corpus re-run surfaced residual FPs
the unit tests could not see:

- **MEASURED, same session and same corpus, both sides:**
  `RC-NAMESPACE-UNRESOLVED-001` **34 → 7 → 1** across the two fix rounds
  (total findings `55 → 29 → 22`). This is the controlled figure.
- **The control that makes those three scans comparable:** other workers were
  editing shared validator modules between runs, so the scans were not
  same-code-except-my-fix. What settles it is that the four non-D13 counters
  are **byte-identical across all three scans** — `DEAD-SCRIPT-REF=9`,
  `GHOST-001=4`, `GHOST-002=1`, `STALE-YEAR=5` — while only `UNRESOLVED`
  moves. Four independent counters holding steady is the evidence that
  nothing else in the pipeline shifted underneath the measurement.
- **NOT a controlled comparison:** the `6,978` baseline quoted in the pre-clear
  handoff was measured at an EARLIER corpus state, before a `/reload-plugins`
  that changed `~/.claude/plugins/cache` — the very tree `p_plugin` is globbed
  from. The reduction is real and large, but do not cite `6,978 → 22` as a
  before/after: the two numbers came from different resolution maps, and
  `scripts/cpv_doctor_user_scope.py` is untracked at HEAD, so no same-corpus
  baseline is recoverable. Cite `34 → 1`.
- Fixes in `scripts/cpv_doctor_user_scope.py`: (a) `/plan`, `/clear`, `/help`
  … resolve to Claude Code **built-ins** (`BUILTIN_SLASH_COMMANDS`) and a bare
  `/tmp`, `/usr` … is a **filesystem root** — neither is a skill reference;
  (b) `commands/*.md` (user-scope AND plugin-cache) are now **resolution
  targets**, since a typed `/name` reaches a command exactly as it reaches a
  skill — without this every command's own usage doc self-referenced into a
  false UNRESOLVED; (c) an **inline `` `code span` ``** is documentation by
  this card's own D13 rule (only *fenced* blocks were skipped before), so
  `` `powercfg /h off` ``, `` `cmd /c ver` `` no longer read as invocations,
  and an **HTTP route** in prose (`GET /health`, `the /search endpoint`) is a
  URL path, not a command.
- **`Skill({...})` is deliberately exempt from the inline-code carve-out** —
  that token is an invocation marker even inside an example (positive control
  pinned by test).
- The handoff's must-survive figures were themselves a proxy (measured on an
  earlier corpus state). Verified **per instance** instead, with a *permissive*
  extraction as control: `GHOST-001=4`, `GHOST-002=1`, `DEAD-SCRIPT-REF=9`,
  `STALE-YEAR=5` all intact; `MISSING`/`SPURIOUS` have **no instance in the
  current corpus under either extraction variant tested** (the one `MISSING`
  seen mid-pass was the `/health` HTTP-route FP). **Scope that control
  honestly:** the permissive fn drops the fence-skip and the
  builtin/fs-root filters but still uses TODAY's `_SKILL_SLASH_RE` and today's
  resolution maps, so it cannot surface an instance only a LOOSER pre-
  recalibration regex would have found. Blast radius if wrong is a missed
  non-blocking advisory, and both code paths stay pinned by synthetic tests.
- The single residual finding is **unresolved and correctly reported** (which
  is all the rule claims — not necessarily actionable):
  `skills/explore/SKILL.md:372` references a `/build` command that resolves to
  nothing in this environment.
- `40/40` tests green (was 31; +9 two-sided, each FP-clear paired with a
  still-fires control), ruff + mypy clean.

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-d1f74670 — CPV doctor user-scope recipes (D9..D13)

**Filename:** `design/tasks/TRDD-20260518_231957+0200-d1f74670-doctor-user-scope-recipes.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

> **`blocked-by:`** lists [[TRDD-25b9be90]] because recipe D9 (ghost-agent dispatch) is exactly that TRDD's rule applied to user-scope. Once TRDD-25b9be90 is `completed`, the `cpv_dispatch_check.py` module from it is the engine D9 calls.

## Origin (provenance)

Session 2026-05-18 user-scope audit (see [[TRDD-25b9be90]] for the ghost-agent angle). In addition to ghost-agent references, the audit surfaced several other categories of rot that current CPV doctor recipes (D1..D8 — designed for plugin structural integrity) do not catch.

The user reviewed each candidate rule before this TRDD was filed. The final set below incorporates their numbered feedback:

| # | Candidate | Verdict | Resulting recipe |
|---|---|---|---|
| 1 | Stub / broken SKILL.md (e.g. 14-byte "404: Not Found") | OK | D10 |
| 2 | Missing `name:` frontmatter | OK (already enforced in plugins) | Wired into D-existing structural checks, not a new recipe |
| 3 | Ghost-agent dispatch | OK | D9 (delegates to [[TRDD-25b9be90]]) |
| 4 | Foreign-harness paths (`thoughts/shared/`, etc.) | **REJECTED** | Not universal — user might legitimately use any directory convention. **No recipe.** |
| 5 | Dead local-script reference | PARTIAL — narrow to user-scope + local-scope + hooks; **EXCLUDE all plugin-shipped paths** even when installed | D12 (narrowed) |
| 6 | Stale "current year is YYYY" | OK + suggest the dynamic-context `` !`date +%Y` `` syntax as the fix | D11 |
| 7 | Plugin shadowing (same name in user-scope and plugin cache) | **REJECTED for this purpose** — namespacing already resolves shadowing in invocations. The real bug is **invocation without a namespace when the referenced skill IS plugin-shipped** | D13 (pivoted entirely) |

## Problem statement

`/cpv-doctor` option 9 audits user-scope using recipes designed for plugin scope. The result: defect classes that only surface at user-scope (stub files from failed downloads, stale year notes, dead local-script refs, missing-namespace invocations of plugin skills) go unreported.

## Goal

Add **five new doctor recipes** (D9..D13) that the `cpv-doctor-agent` runs when `mode=user_scope` (option 9). Each recipe is independent and contributes its findings to the same report.

## Recipe specifications

### D9 — Ghost-agent dispatch detection (delegates to [[TRDD-25b9be90]])

| # | Aspect | Value |
|---|---|---|
| 1 | Module | `scripts/cpv_dispatch_check.py` (shipped by TRDD-25b9be90) |
| 2 | Severity | CRITICAL |
| 3 | Finding code | `RC-GHOST-DISPATCH-001` |
| 4 | Scope | `~/.claude/skills/`, `~/.claude/agents/`, `~/.claude/commands/` |

This is the existing engine applied to a different file tree — no new logic.

### D10 — Stub / broken SKILL.md or agent.md

| # | Aspect | Value |
|---|---|---|
| 1 | Heuristic | File body (after stripping YAML frontmatter) is `< 200` chars **AND** matches case-insensitive regex `^(404\|Not Found\|Error 4\d\d\|<html\|access denied\|<!DOCTYPE)` |
| 2 | Severity | MAJOR |
| 3 | Finding code | `RC-STUB-FILE-001` |
| 4 | Suggested fix | The file was likely a failed download. Re-fetch from source, or move the broken stub to backup |
| 5 | False-positive guard | BOTH conditions required (short AND matches error text). A legitimate short valid-markdown SKILL.md is fine |

### D11 — Stale hardcoded year

| # | Aspect | Value |
|---|---|---|
| 1 | Patterns | `current year is 20\d\d`, `the year is 20\d\d`, `as of 20\d\d`, `> Note:[^\n]*20\d\d` (in skill/agent/command prose) |
| 2 | Severity | MINOR |
| 3 | Finding code | `RC-STALE-YEAR-001` |
| 4 | Suggested fix (canonical) | Replace with the **dynamic-context substitution syntax** `` !`date +%Y` `` (per Anthropic's [skills doc, "Inject dynamic context"](https://code.claude.com/docs/en/skills#inject-dynamic-context)). The shell command runs before the skill body reaches Claude, so the placeholder is replaced with the actual year. Requires `allowed-tools: Bash(date *)` in the skill's frontmatter |
| 5 | False-positive guard | Skip matches inside fenced code blocks marked `text`, `output`, `console`, `log`. Skip matches within 8 chars of `copyright`, `changelog`, `since`, `migrated`, `released`, `version`, `as of N years` |
| 6 | Bonus | When detecting, also check if `allowed-tools:` allows `Bash(date *)`; if not, suggest adding it so the fix actually works |

### D12 — Dead local-script reference (narrowed per feedback row 5)

| # | Aspect | Value |
|---|---|---|
| 1 | Scope (IN) | User-scope skills/agents/commands/hooks (`~/.claude/{skills,agents,commands,hooks}/`); local-scope standalone elements (`$PROJECT/.claude/{skills,agents,commands,hooks}/`); user-scope `~/.claude/settings*.json` and project `.claude/settings*.json` hook entries |
| 2 | Scope (OUT) | ALL plugin-shipped content (`~/.claude/plugins/cache/**`). A plugin may generate scripts in its data dir (`~/.claude/plugins/data/<plugin>/`) on first use — a missing-now reference is NOT a bug. **Skip every path that resolves to or inside any installed plugin's cache or data dir, even if env vars like `${CLAUDE_PLUGIN_ROOT}` are used** |
| 3 | Extraction | Paths matching `~/.claude/[a-z][a-z0-9_/.-]+\.(sh\|py\|js\|ts\|rb)`, `\$CLAUDE_PROJECT_DIR/[a-z][a-z0-9_/.-]+\.(sh\|py\|js\|ts\|rb)`, bare relative paths in hook `command:` fields |
| 4 | Resolution | Resolve env vars; `os.path.exists()` on absolute path; if the resolved path is inside a plugin cache or plugin data dir, skip |
| 5 | Severity | MAJOR |
| 6 | Finding code | `RC-DEAD-SCRIPT-REF-001` |
| 7 | Suggested fix | The script is referenced but doesn't exist on disk — remove the reference, create the script, or fix the path |
| 8 | False-positive guard | Skip inside fenced code blocks marked `text`, `output`, `console`. Skip lines starting with `# ` (Markdown comments / example listings) |

### D13 — Namespace correctness for skill/agent invocations (pivoted per feedback row 7)

| # | Aspect | Value |
|---|---|---|
| 1 | Rule | When a skill/agent/command body or its frontmatter references another **skill** by name, it MUST use `plugin-name:skill-name` form IF the referenced skill is shipped by an installed plugin, and MUST use bare `skill-name` form IF the referenced skill is user-scope or local-scope standalone |
| 2 | Surfaces inspected | `Skill({skill: "<name>"})` calls in bodies; `/skill-name` invocations in command/skill bodies; frontmatter `skills:` lists in agents (or `allowed-skills:`); `Skill` tool param literals in code-block examples |
| 3 | Resolution map | Build once per audit: scan `~/.claude/skills/` + `$PROJECT/.claude/skills/` → set `S_LOCAL`. Scan `~/.claude/plugins/cache/*/*/<latest>/skills/` → map `P_PLUGIN: skill_name → plugin_name`. (A plugin's namespace is the manifest `name:` field.) |
| 4 | Findings — bare name when plugin-shipped | If a reference uses bare `skill-name` and the name is in `P_PLUGIN` but NOT in `S_LOCAL` → finding `RC-NAMESPACE-MISSING-001` (MAJOR): "Add namespace `<plugin>:<skill>`" |
| 5 | Findings — namespaced when local | If a reference uses `<ns>:skill-name` and the name IS in `S_LOCAL` AND NOT in `P_PLUGIN` → finding `RC-NAMESPACE-SPURIOUS-001` (MINOR): "Drop the `<ns>:` prefix — `<skill>` is a standalone, not a plugin skill" |
| 6 | Findings — ambiguous | If the bare name exists in BOTH `S_LOCAL` AND `P_PLUGIN` → finding `RC-NAMESPACE-AMBIGUOUS-001` (MAJOR): "Bare reference to `<skill>` is ambiguous — exists in both user-scope and `<plugin>`. Pick one explicitly" |
| 7 | Findings — unresolved | If the name resolves to nothing → finding `RC-NAMESPACE-UNRESOLVED-001` (CRITICAL): "Referenced skill `<name>` not found in user-scope, local-scope, or any installed plugin" |
| 8 | False-positive guard | Skip mentions inside prose where the skill name is in backticks but NOT preceded by `Skill(`, `/`, or `skills:` (those are documentation, not invocations) |
| 9 | Plugin-scope use | When CPV audits a plugin (not user-scope), the same rule applies: a plugin's own skill references must EITHER use the plugin's own namespace prefix OR (recommended) be bare (in which case the harness resolves them within the same plugin). The recipe is universal; only the resolution maps differ |

## Out of scope

| # | Item | Why |
|---|---|---|
| 1 | Auto-fix any of the findings | Doctor is diagnose-only by design. Fixes go through `/cpv-fix-validation` (extend in a follow-up TRDD) |
| 2 | Cross-plugin dependency consistency | Out of scope; covered by `RC-GHOST-DISPATCH-003` (future, see [[TRDD-25b9be90]] risk table) |
| 3 | Foreign-harness path patterns | Rejected by the user — not universal. Some users intentionally use `thoughts/` or other conventions |
| 4 | Plugin shadowing (same name at user scope AND in installed plugin) | Rejected by the user — namespacing handles this in practice. Only the invocation-time ambiguity matters (covered by D13 finding `RC-NAMESPACE-AMBIGUOUS-001`) |
| 5 | Auditing project-scope (`$PROJECT/.claude/`) with the same recipes | Future TRDD. Options 7 (`local_scope`) and 8 (`project_scope`) already exist in `/cpv-doctor`. Once D9..D13 are proven on user-scope, mirror them to project/local scope |
| 6 | Verifying plugin-shipped script references | Plugins legitimately generate scripts in `~/.claude/plugins/data/<plugin>/` on first use. A missing-now reference is NOT a bug. D12 explicitly excludes plugin paths |

## Design

### File layout

| # | File | Status | Purpose |
|---|---|---|---|
| 1 | `scripts/cpv_doctor_user_scope.py` | NEW | Functions: `check_stub_files()`, `check_stale_year()`, `check_dead_script_refs()`, `check_namespace_correctness()` |
| 2 | `scripts/cpv_dispatch_check.py` | (from TRDD-25b9be90) | D9 calls this module's `extract_subagent_dispatches()` |
| 3 | `agents/cpv-doctor-agent.md` | MODIFY | Add D9..D13 to the recipe list; gate them to `mode=user_scope`; show new finding codes in the breakdown matrix |
| 4 | `references/finding-codes.md` | MODIFY | Register `RC-STUB-FILE-001`, `RC-STALE-YEAR-001`, `RC-DEAD-SCRIPT-REF-001`, `RC-NAMESPACE-MISSING-001`, `RC-NAMESPACE-SPURIOUS-001`, `RC-NAMESPACE-AMBIGUOUS-001`, `RC-NAMESPACE-UNRESOLVED-001` |
| 5 | `scripts/format_menu.py` | MODIFY | Breakdown chart handles the new finding codes |

### Doctor recipe dispatch (in agent body, when mode=user_scope)

```
Run existing D1..D8 (plugin-shape — most don't fire on user-scope, but cheap to skip)
PLUS the user-scope-specific set:
  D9  — Ghost-agent dispatch (module from TRDD-25b9be90)
  D10 — Stub / broken SKILL.md
  D11 — Stale year notes
  D12 — Dead local-script references (USER-SCOPE + LOCAL-SCOPE STANDALONE + HOOKS ONLY)
  D13 — Namespace correctness
```

### Universal applicability

D13 is the only recipe that **also** runs in plugin scope (not gated to user-scope). The other four (D9 via TRDD-A is already universal in TRDD-A's plan; D10, D11, D12) are user-scope-specific, but D10 and D11 will be wired into plugin-scope audits in a follow-up TRDD if they prove useful there.

## Test plan

| # | Test file | What it checks |
|---|---|---|
| 1 | `tests/test_doctor_d10_stub_files.py` | Detects 14-byte "404: Not Found" stub; ignores legitimate short valid-markdown SKILL.md |
| 2 | `tests/test_doctor_d11_stale_year.py` | Catches `current year is 2025`; ignores `since 2024`, copyright lines, fenced output; suggests `` !`date +%Y` `` and the `allowed-tools: Bash(date *)` addition |
| 3 | `tests/test_doctor_d12_dead_script_refs.py` | Detects `~/.claude/scripts/missing.sh`; ignores `${CLAUDE_PLUGIN_ROOT}/scripts/<real>.py` when the file exists; **ALWAYS ignores any path that resolves inside `~/.claude/plugins/cache/**` or `~/.claude/plugins/data/**`** |
| 4 | `tests/test_doctor_d13_namespace_missing.py` | Detects bare `Skill({skill: "menu-test"})` when `menu-test` is shipped by `claude-menu-system` plugin and NOT user-scope → emits `RC-NAMESPACE-MISSING-001` MAJOR |
| 5 | `tests/test_doctor_d13_namespace_spurious.py` | Detects `Skill({skill: "user-scope:my-local"})` when `my-local` is user-scope standalone → emits `RC-NAMESPACE-SPURIOUS-001` MINOR |
| 6 | `tests/test_doctor_d13_namespace_ambiguous.py` | Detects bare `Skill({skill: "team-kanban"})` when name exists in both user-scope AND a plugin → emits `RC-NAMESPACE-AMBIGUOUS-001` MAJOR |
| 7 | `tests/test_doctor_d13_namespace_unresolved.py` | Detects bare `Skill({skill: "ghost-skill"})` when nothing matches → emits `RC-NAMESPACE-UNRESOLVED-001` CRITICAL |
| 8 | `tests/test_doctor_d13_namespace_agent_frontmatter.py` | Same rules applied to agent frontmatter `skills:` lists |
| 9 | `tests/test_doctor_user_scope_e2e.py` | End-to-end: feed `/cpv-doctor` option 9 a fixture user-scope tree → assert D9..D13 all fire with correct counts |

Target: ~35 new tests.

## Acceptance criteria

- [ ] `scripts/cpv_doctor_user_scope.py` exists with four functions (one per D10..D13; D9 delegates)
- [ ] `agents/cpv-doctor-agent.md` recipe list updated with D9..D13; mode gating documented
- [ ] `references/finding-codes.md` registers 7 new RC codes (D9 inherits from TRDD-25b9be90)
- [ ] `scripts/format_menu.py` breakdown chart handles every new code
- [ ] All ~35 new tests pass; no regressions on the existing 5000+ test suite
- [ ] `/cpv-doctor` option 9 on the user's real `~/.claude/` produces findings that match the audit results from session 2026-05-18 (the ones already fixed should now be 0; any unfixed ones should be flagged)

## Risks + mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | Stub-file heuristic misclassifies a legitimate <200-char SKILL.md | Require BOTH small size AND error-text pattern match — both, not either |
| 2 | Stale-year regex matches historical references | Negative surrounding-text exclusion list: `copyright`, `changelog`, `since`, `migrated`, `released`, `version`, `as of N years ago` |
| 3 | Dead-script-ref check chokes on `${VAR}` env vars that can't be resolved | Best-effort: resolve `HOME`, `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT`; on unresolvable vars, emit NIT not MAJOR ("path uses unresolved env var, cannot verify"). And the plugin-path exclusion (row 6 below) catches the common case |
| 4 | Namespace check requires building the user-scope + plugin-shipped resolution maps every run | Cache the maps per `/cpv-doctor` invocation (build once, reuse across all D13 calls). Plugin cache is also indexed once and shared |
| 5 | Namespace check has poor performance on installs with 200+ plugins | Map build is O(installed-plugins + user-scope-skills) once; per-finding lookup is O(1) hash. Acceptable even at 500+ plugins |
| 6 | A plugin generates its scripts on first use (legitimate pattern); D12 must not false-flag those | Scope exclusion in D12 row 2: any path that resolves into `~/.claude/plugins/cache/**` or `~/.claude/plugins/data/**` is skipped — even when env vars like `${CLAUDE_PLUGIN_ROOT}` are used |
| 7 | Skill body uses dynamic-context substitution `` !`<command>` `` and D12 misparses it as a path reference | Anchor D12 extraction to literal file paths — backtick-wrapped shell commands don't match the path regex |
| 8 | New finding codes break existing menu/summary renderers | Test 9 (e2e) smoke-checks `format_menu.py breakdown` consumes each new code without crashing |

## Follow-up TRDDs (not part of this one)

| # | Follow-up | Why deferred |
|---|---|---|
| 1 | Extend D10, D11, D12, D13 to project-scope (`/cpv-doctor` options 7, 8) | Out of scope here; covered later once D9..D13 are stable on user-scope |
| 2 | Add `/cpv-fix-validation` auto-fixes for D11 (mechanical year-note → `` !`date +%Y` `` replacement) | Doctor is diagnose-only; fixers ship separately |
| 3 | Wire D10, D11 into plugin-scope structural validators | After D10/D11 prove value at user-scope |
| 4 | Cross-plugin dependency check (plugin A dispatches to plugin B's agent; B not installed) | Covered by future `RC-GHOST-DISPATCH-003` in [[TRDD-25b9be90]] follow-ups |

## Approval log

- 2026-08-26T05:54:23+0200 — COMPLETE by the CPV session (authority delegated by
  USER 2026-08-25 "decide yourself, base decisions on verified facts").
  Evidence measured first-hand this session: D13 `RC-NAMESPACE-UNRESOLVED-001`
  34 → 1 on the real `~/.claude` tree (both sides same session; the four
  non-D13 counters byte-identical across all three scans = the control), 40
  tests green, and the full pre-publish gate clean — serial suite 13,076
  passed / 3 skipped (`PYTEST4_EXIT=0`) and cache-cold strict self-validate
  0 CRITICAL / 0 MAJOR / 0 MINOR / 0 NIT (`SELFVAL4_EXIT=0`).
