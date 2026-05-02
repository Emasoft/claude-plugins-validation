---
name: cpv-main-menu
description: Single entry point — interactive menu of every CPV command/skill/agent (validate, fix, create, manage, GitHub, semantic-grade)
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion, Skill, Edit, Write
argument-hint: "(none — this command is fully interactive)"
agent: cpv-main-menu-agent
user-invocable: true
---

# /cpv-main-menu — CPV Main Menu

The CPV plugin ships ~22 user-invocable commands plus a half-dozen agents
and skills. This single entry point routes you through them via an
interactive menu so you never need to remember individual command names.

Every menu, sub-menu, and sub-sub-menu includes a **Cancel / Exit**
option. Picking it terminates the command cleanly with no side effects.

## How it works

1. You invoke `/cpv-main-menu`
2. The orchestrator presents the **top-level menu** via `AskUserQuestion`
3. You pick a category → orchestrator drills into a **sub-menu** with the
   commands in that category (one-line description per entry)
4. You pick a leaf command → orchestrator asks for any required arguments
   (path, options) via `AskUserQuestion`, then **executes the chosen
   command inline** by following its instructions
5. At any prompt you can pick **Cancel / Exit** to abort

Detailed routing logic, each leaf's invocation pattern, and the full
menu tree live in [`references/menu-tree.md`](../skills/cpv-main-menu-skill/SKILL.md).
The skill is loaded automatically.

## Top-level menu (preview)

| # | Category | What it covers |
|---|---|---|
| 1 | **Validate** | plugin, skill, cache (CA-01..CA-06), marketplace, local-scope, project-scope, component |
| 2 | **Validate from GitHub** | plugin or marketplace by `owner/repo` (clones to tmpdir, scans, cleans up) |
| 3 | **Fix** | plugin findings, marketplace findings, cache findings (audit + fix loop) |
| 4 | **Create** | scaffold a new plugin, scaffold a new marketplace |
| 5 | **Manage** | list installed, doctor, install scanners, bump version, show CPV version |
| 6 | **GitHub setup** | branch protection rules, link plugin to marketplace |
| 7 | **Deep semantic analysis** | opus-driven A-F grading (expensive — 10-50x token cost) |
| 8 | **Help / About** | what each category does, list every command |
| **C** | **Cancel / Exit** | terminate without doing anything |

## Workflow (the orchestrator MUST follow this exact sequence)

1. **Show top-level menu** via `AskUserQuestion`. Present the 8 categories
   above plus the **Cancel / Exit** option. Each option line is one short
   sentence (no more than ~80 chars).
2. **On Cancel / Exit at any level** → respond with a single line
   `Cancelled — no actions taken.` and stop.
3. **On category pick** → load the corresponding sub-menu definition from
   `cpv-main-menu-skill` and present the sub-menu via another
   `AskUserQuestion`. Always include a **Back** option (returns to the
   top-level menu) AND a **Cancel / Exit** option (terminates cleanly).
4. **On leaf pick** → the skill's `references/menu-tree.md` documents
   what arguments to ask for and which underlying command to execute.
   Execute the chosen command's instructions inline (read its `.md`
   file, follow its bash). Do NOT just print "run /cpv-validate-plugin"
   — actually run the workflow.
5. **Always run via the launcher.** Every validator invocation must use
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" <alias>`
   — never call validate scripts directly from the plugin cache.
6. **Report back** what you did and where the report lives.

## What this command does NOT do

- It does NOT replace the individual slash commands. Power users who
  know exactly what they need can still invoke `/cpv-validate-plugin
  <path>` etc. directly. This menu is for discovery and one-stop
  navigation.
- It does NOT auto-install anything without confirmation. For
  `cpv-doctor --install-scanners` (which runs `brew install`,
  `pipx install`, etc. on your machine), the menu always asks before
  proceeding.
- It does NOT bypass the launcher. Every validator call routes through
  `remote_validation.py` for environment isolation.

## Examples

<example>
user: /cpv-main-menu
assistant: Presents top-level menu via AskUserQuestion (8 categories + Cancel).
user: picks "Validate"
assistant: Presents sub-menu (plugin / skill / cache / marketplace / local-scope / project-scope / component / Back / Cancel).
user: picks "plugin"
assistant: Asks for the plugin path via AskUserQuestion.
user: provides ~/Code/my-plugin/
assistant: Runs the launcher → plugin alias → reads stdout summary → returns the report path.
✓ Plugin Validation: PASS. Report: reports/validate_plugin/20260502_143012+0200-my-plugin.md
</example>

<example>
user: /cpv-main-menu
assistant: Presents top-level menu.
user: picks "Cancel / Exit"
assistant: Cancelled — no actions taken.
</example>

<example>
user: /cpv-main-menu
assistant: Presents top-level menu.
user: picks "Manage"
assistant: Presents sub-menu (list / doctor / install-scanners / bump-version / show-version / Back / Cancel).
user: picks "Back"
assistant: Re-presents the top-level menu.
user: picks "Cancel / Exit"
assistant: Cancelled — no actions taken.
</example>

## Related Commands

- `/cpv-validate-plugin <path>` — direct plugin validation (no menu)
- `/cpv-validate-skill <path>` — direct skill validation
- `/cpv-fix-validation <report>` — fix from a report
- `/cpv-doctor` — quick health check
- See the full list under "Help / About" → "List all commands" in the menu
