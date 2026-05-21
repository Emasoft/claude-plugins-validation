---
trdd-id: a175f78d-d700-41c8-98ff-caa565acac84
title: Batch scope-aware doctor skills (project-folder list + scope filter)
status: in-progress
created: 2026-05-21T15:44:00+0200
updated: 2026-05-21T16:35:23+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-a175f78d — Batch scope-aware doctor skills

## Source

User directive (verbatim, condensed):

> Add three scope-aware doctor skills:
>
> * `cpv-batch-scope-diagnose` — launch batches of doctor agents to
>   verify the extensions of a list of projects in a given scope.
>   Input: list of project folder paths (LOCAL only — a valid Claude
>   installation is required), and a scope parameter accepting
>   `full`, `user`, `project`, `local` as the four options. `full`
>   means scan and diagnose dependencies + conflicts across all
>   extension scopes (user, project, local).
> * `cpv-batch-scope-fix` — launch batches of doctor agents to fix
>   issues found by `cpv-batch-scope-diagnose`. Same input.
> * `cpv-batch-scope-diagnose-and-fix` — combine both in one run,
>   scan-verify-fix in the same turn to avoid the agents reading
>   the same code or reports twice.
>
> All three are user-invocable. All registered in `the-skills-menu`.

Companion to **TRDD-3dcbb37c** (the marketplace-input batch family);
that TRDD handles plugin-source-tree scans (URL or local). This TRDD
covers Claude-installation-level diagnostics across a fleet of
project directories — a different input shape and a different target
surface.

## §1 — Why URLs are forbidden as input

The doctor's diagnostic surface includes `~/.claude/*` (user scope),
`<project>/.claude/*` (project scope, git-tracked), and
`<project>/.claude/settings.local.json` (local scope, gitignored).
Two of these three live OUTSIDE any single git repository. A URL
input can't represent "the user's home + this project's .claude
tree" — both are local-filesystem state.

The skill therefore accepts only:

| Shape | Form | Resolved to |
|---|---|---|
| Single project folder | `/path/to/project` | `[("project", abs_path)]` |
| List of project folders (CLI) | `--list a b c` OR comma-separated | each token resolved as project root |
| List of project folders (file) | `@/path/to/list.txt` | each line resolved as project root |
| Current folder (default) | (no input) | `[$PWD]` |

URLs (`https://github.com/owner/repo`, `owner/repo`) are rejected
with a CRITICAL message:

```text
ERROR: cpv-batch-scope-* skills require LOCAL project paths.
       A valid Claude installation (~/.claude/) is necessary to
       diagnose user/project/local-scope extensions. URL inputs
       cannot reach the filesystem state of a Claude installation.
       Use cpv-batch-validate or cpv-batch-doctor for source-tree
       scans of remote plugins.
```

## §2 — Scope parameter semantics

The `--scope` argument is exactly four values:

| Value | Surface |
|---|---|
| `user` | `~/.claude/` ONLY. User-scoped agents, skills, commands, hooks, MCP servers, output styles, LSP servers, monitors. |
| `project` | `<project>/.claude/` ONLY — limited to git-tracked entries (i.e., `git ls-files <project>/.claude/`). |
| `local` | `<project>/.claude/settings.local.json` ONLY — the gitignored local extensions and their referenced files. |
| `full` (default) | All of the above merged. Also runs the cross-scope conflict checker: same skill/agent/hook name appearing in two scopes; project-scope entry shadowing a user-scope entry that the user actually wanted; local-scope settings overriding project-scope. |

The dispatched agent is `cpv-doctor-agent` (existing) with a NEW mode
keyword:

| Skill | `cpv-doctor-agent` mode |
|---|---|
| `cpv-batch-scope-diagnose` | `batch_scope_diagnose` (read-only) |
| `cpv-batch-scope-fix` | `batch_scope_fix` (applies fixes) |
| `cpv-batch-scope-diagnose-and-fix` | `batch_scope_same_turn` (scan + verify + fix in one turn) |

The cpv-doctor-agent already knows how to diagnose at multiple
scopes via the existing doctor recipes; the new modes only add the
batch-shard contract (read the plan path, run only those scopes,
write a status JSON, return one line).

## §3 — Cross-scope conflict detection (scope=full)

The cross-scope conflict checker enumerates every extension name
(skill/agent/hook/command/mcp/lsp/monitor/output-style) at each
scope. For every name with > 1 scope-occurrence it produces a
finding:

| Conflict shape | Severity | Why |
|---|---|---|
| Same name, two scopes, identical content | NIT | Duplicate — no behavioral effect, wastes disk. |
| Same name, two scopes, different content | MAJOR | The higher-precedence copy silently overrides; the user often forgot which one is loaded. |
| Project-scope entry referencing a file not git-tracked | MAJOR | This will silently disappear on `git clone` of the project. |
| Local-scope settings entry referencing a file not in the same `.claude/` tree | CRITICAL | The local entry will fail to load on any other machine; usually a misplaced setting. |
| User-scope hook AND project-scope hook on the same event | MINOR | Both fire — verify the order matters. |

The same-turn skill (`cpv-batch-scope-diagnose-and-fix`) auto-applies
the obvious fixes (delete-duplicate-NIT, move-misplaced-CRITICAL)
inline; MAJOR / MINOR are reported with a fix recipe but require
explicit user approval before applying (the agent does NOT silently
mutate user-scope state).

## §4 — Skill file structure

```text
commands/cpv-batch-scope-diagnose.md
commands/cpv-batch-scope-fix.md
commands/cpv-batch-scope-diagnose-and-fix.md
skills/cpv-batch-scope-diagnose/SKILL.md
skills/cpv-batch-scope-fix/SKILL.md
skills/cpv-batch-scope-diagnose-and-fix/SKILL.md
```

Frontmatter for every skill:

```yaml
---
name: cpv-batch-scope-<op>
description: "<one-liner> across project folder list, scope: full | user | project | local."
user-invocable: true
allowed-tools: Read, Write, Bash, Glob, Grep, AskUserQuestion
---
```

## §5 — Orchestrator skeleton

Per project, one `cpv-doctor-agent` dispatch. Per `--max-parallel`
(default 8), N agents run in parallel from a single main-session
message. Output is the per-project one-liner. The aggregator stacks
the per-project lines into a single summary table:

```text
DONE: projects=N diagnosed=X (fixed=Y) issues_total=Z. Report dir: <abs-path>
```

Per the iron-rule of the doctor: when scope=`full`, the per-project
agent emits ONE `.md` report under `$MAIN_ROOT/reports/scope-doctor/<project-name>-<ts>.md`
containing the per-scope findings AND the cross-scope conflicts.

## §6 — `the-skills-menu` integration

Add a new domain row `"Scope-aware diagnostics"` to `skills/the-skills-menu/SKILL.md`'s plugin-skills table:

```text
| 6 | Scope-aware diagnostics | cpv-batch-scope-diagnose, cpv-batch-scope-fix, cpv-batch-scope-diagnose-and-fix |
```

References table in `skills/the-skills-menu/references/skills-catalog.md` extended with per-skill rows (inputs + return contracts).

## §7 — File list

NEW:

* `scripts/cpv_scope_doctor_input.py` (~150 LOC) — resolver + scope-flag parser.
* `commands/cpv-batch-scope-diagnose.md`
* `commands/cpv-batch-scope-fix.md`
* `commands/cpv-batch-scope-diagnose-and-fix.md`
* `skills/cpv-batch-scope-diagnose/SKILL.md`
* `skills/cpv-batch-scope-fix/SKILL.md`
* `skills/cpv-batch-scope-diagnose-and-fix/SKILL.md`
* `tests/test_cpv_scope_doctor_input.py` (~15 tests)
* `tests/test_cpv_batch_scope_skills.py` (~20 tests including frontmatter, menu-integration, scope-flag validation)
* `tests/test_cross_scope_conflict_severities.py` (~10 tests)

MODIFIED:

* `agents/cpv-doctor-agent.md` — add `batch_scope_diagnose`, `batch_scope_fix`, `batch_scope_same_turn` modes; add the cross-scope conflict checker recipe.
* `skills/the-skills-menu/SKILL.md` — Scope-aware diagnostics row.
* `skills/the-skills-menu/references/skills-catalog.md` — per-skill rows.

## §8 — Acceptance

* [ ] All 6 new skill / command files exist + every SKILL.md is `user-invocable: true`.
* [ ] `the-skills-menu` lists all three under "Scope-aware diagnostics".
* [ ] Unit tests pass (input resolver 15/15, skills 20/20, conflict severities 10/10).
* [ ] `cpv-batch-scope-diagnose --scope full` against a curated multi-scope fixture surfaces every seeded conflict; `cpv-batch-scope-fix` applies the obvious fixes; the same-turn variant does both in ONE turn.
* [ ] CPV self-scan stays at 0/0/0/0 + WARNING-only.
* [ ] Full test suite passes.
* [ ] CI ✓ + Release ✓ + Notify Marketplace ✓ green.
* [ ] No publish before all of the above + TRDD-3dcbb37c's acceptance is green.

## §9 — Lesson reservation

This TRDD's scope (LOCAL-only, scope-aware) is the structural
opposite of TRDD-3dcbb37c (URL+marketplace, plugin-source-only).
Keeping them as two TRDDs prevents a future maintainer from
collapsing the input grammars — the LOCAL-only constraint exists for
a reason (the doctor needs filesystem access to `~/.claude/`), and
the marketplace-URL path exists for a reason (the validator/fixer
don't need any local state beyond a clone).
