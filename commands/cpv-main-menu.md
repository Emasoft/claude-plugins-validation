---
name: cpv-main-menu
description: Single entry point — numbered-table menu of every CPV command/skill/agent (validate, fix, create, manage, GitHub, semantic-grade)
allowed-tools: Read, Bash, Glob, Grep, Skill, Edit, Write
argument-hint: "(none — this command is fully interactive)"
agent: cpv-main-menu-agent
user-invocable: true
---

# /cpv-main-menu — CPV Main Menu

The CPV plugin ships ~22 user-invocable commands plus a half-dozen agents
and skills. This single entry point routes you through them via a
**numbered-table menu** so you never need to remember individual command
names.

Every menu, sub-menu, and sub-sub-menu includes a **`0 — Cancel / Exit`**
row. Picking it terminates cleanly with no side effects. Sub-menus also
include a **`9 — Back`** row that returns to the parent menu.

## How it works

1. You invoke `/cpv-main-menu`
2. The orchestrator prints the **top-level table** (Unicode box-drawing).
3. You reply with the row number → orchestrator drills into a **sub-menu
   table** with the commands in that category.
4. You reply with the leaf number → orchestrator asks for any required
   arguments (path, options) as plain-text questions, then **executes the
   chosen command inline** by following its instructions.
5. At any prompt you can type **`0`** to abort, or **`9`** in a sub-menu to
   go back.

Detailed routing logic, each leaf's invocation pattern, and the full menu
tree live in [`references/menu-tree.md`](../skills/cpv-main-menu-skill/references/menu-tree.md)
within the `cpv-main-menu-skill`. The skill is loaded automatically.

## Why a numbered table (and not interactive picker UI)

- Tables are **unbounded** — the user can scroll back; there's no
  4-or-5-row UI cap.
- Tables support **multi-column metadata**: `# / Option / Description /
  Use-case / Cost / Risk`. The semantic-validation menu uses a 4-column
  layout to surface the 10-50× cost warning right next to the option.
- Tables **render in any terminal**, including pure-text logs and
  copy-paste session transcripts.
- Picking is **one keystroke** (the row number).

## Top-level menu (canonical layout — v2.90.0)

The orchestrator prints this table verbatim. Cancel/Exit is row `0`:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Category                ┃ What it does                                                          ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Validate                │ Check that a plugin / marketplace / component is well-formed         │
│ 2 │ Fix                     │ Auto-fix issues that a previous validation found                     │
│ 3 │ Optimize for Cache      │ Prompt-cache invalidation audit + cache-aware refactor (CA-01..06)   │
│ 4 │ Diagnose                │ Deep audit + AI-graded quality review (semantic, opus, on request)   │
│ 5 │ Update                  │ Upgrade plugin to latest canonical pipeline standard                 │
│ 6 │ Create                  │ Scaffold plugin, marketplace, skill, agent, command, hook, MCP       │
│ 7 │ Publish & Migrate       │ Branch rules, link to marketplace, publish, migrate marketplace      │
│ 8 │ Manage                  │ List installed plugins, install / update / enable / disable / doctor │
│ H │ Help / About            │ Category overview, command list, CPV version                         │
│ A │ Ask the agent           │ Let the agent suggest the best next action right now                 │
│ 0 │ Cancel / Exit           │ Terminate without action                                             │
└───┴─────────────────────────┴──────────────────────────────────────────────────────────────────────┘
Type a number (or H for help, A to ask the agent) to choose:
```

## Workflow (the orchestrator MUST follow this exact sequence)

1. **Print the top-level table** verbatim (above). End with the prompt
   line `Type a number to choose:`.
2. **Wait** for the user's next message. Parse the leading integer.
3. **On `0` at any level** → respond with a single line `Cancelled — no
   actions taken.` and stop.
4. **On a category number** → load the corresponding sub-menu table from
   `cpv-main-menu-skill/references/menu-tree.md` and print it. Sub-menus
   include `9 — Back` AND `0 — Cancel / Exit`.
5. **On a leaf number** → ask required arguments as plain-text questions,
   then execute the chosen command's instructions inline (read its `.md`
   file, follow its bash). Do NOT just print "run /cpv-validate-plugin" —
   actually run the workflow.
6. **Always run via the launcher.** Every validator invocation must use
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" <alias>`
   — never call validate scripts directly from the plugin cache.
7. **Report back** the compact summary + report-file path.
8. **Print the §3.9 "do something else?" 2-row table**. On `0` → `Done.`

## What this command does NOT do

- It does NOT replace the individual slash commands. Power users who know
  exactly what they need can still invoke `/cpv-validate-plugin <path>`
  etc. directly. This menu is for discovery and one-stop navigation.
- It does NOT auto-install anything without confirmation. For
  `cpv-doctor --install-scanners` (which runs `brew install`,
  `pipx install`, etc. on your machine), the menu always asks before
  proceeding.
- It does NOT bypass the launcher. Every validator call routes through
  `remote_validation.py` for environment isolation.
- It does NOT use `AskUserQuestion`. Tables + plain-text prompts only.

## Examples

<example>
user: /cpv-main-menu
assistant: [Prints top-level table — 9 rows including `0 — Cancel / Exit`]
user: 1
assistant: [Prints Validate sub-menu table — 9 rows including `9 — Back` and `0 — Cancel / Exit`]
user: 1
assistant: Path to the plugin to validate? (e.g. ~/Code/my-plugin/)
user: ~/Code/my-plugin/
assistant: [Runs the launcher → plugin alias → reads stdout summary]
✓ Plugin Validation: PASS. Report: reports/validate_plugin/20260502_143012+0200-my-plugin.md
[Prints "do something else?" 2-row table]
user: 0
assistant: Done.
</example>

<example>
user: /cpv-main-menu
assistant: [Prints top-level table]
user: 0
assistant: Cancelled — no actions taken.
</example>

<example>
user: /cpv-main-menu
assistant: [Prints top-level table]
user: 5
assistant: [Prints Manage sub-menu table — 9 rows]
user: 9
assistant: [Re-prints top-level table]
user: 0
assistant: Cancelled — no actions taken.
</example>

## Related Commands

- `/cpv-validate-plugin <path>` — direct plugin validation (no menu)
- `/cpv-validate-skill <path>` — direct skill validation
- `/cpv-fix-validation <report>` — fix from a report
- `/cpv-doctor` — quick health check
- See the full list under "Help / About" → "List every CPV command" in the menu
