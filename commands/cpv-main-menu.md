---
name: cpv-main-menu
description: Single entry point — Stop-hook menu of every CPV command/skill/agent (validate, fix, create, manage, GitHub, semantic-grade)
argument-hint: "(none — this command is fully interactive)"
agent: cpv-main-menu-agent
user-invocable: true
---

# /cpv-main-menu — CPV Main Menu

The CPV plugin ships ~22 user-invocable commands plus a half-dozen agents
and skills. This single entry point routes you through them via a
**post-turn Stop-hook menu** so you never need to remember individual
command names.

Every menu, sub-menu, and sub-sub-menu includes a **`0 — Cancel / Exit`**
row. Picking it terminates cleanly with no side effects. Sub-menus also
include a **`B — Back`** row that returns to the parent menu (letter `B`,
not digit `9`, so it never collides with multi-digit option numbers in
long menus).

## How it works

1. You invoke `/cpv-main-menu`.
2. The orchestrator builds a menu spec and queues it via
   `scripts/cpv_menu.py`, then ENDS its turn. The `claude-menu-system`
   plugin's Stop hook emits the rendered menu via the hook JSON
   `systemMessage` field — so the menu is shown to you but NEVER enters
   the agent's transcript (zero token cost regardless of menu size).
3. You reply with the row key (number or letter) → orchestrator drills
   into the chosen sub-menu spec (queued the same way).
4. You reply with the leaf key → orchestrator asks for any required
   arguments (path, options) as plain-text questions, then **executes the
   chosen command inline** by following its instructions.
5. At any prompt you can type **`0`** to abort, or **`B`** in a sub-menu
   to go back.

Detailed routing logic, each leaf's invocation pattern, the full menu
tree, AND the FIXED-KEY ROUTING CONTRACT (which letter maps to which
action across the whole tree) live in
[`references/menu-tree.md`](../skills/cpv-main-menu-skill/references/menu-tree.md)
within the `cpv-main-menu-skill`. The skill is loaded automatically.

## Why the Stop hook (and not inline tables or interactive picker UI)

- The Stop hook emits via `systemMessage`, so the menu **never enters
  the agent's transcript or prompt cache** — zero context cost regardless
  of menu size.
- No subagent fork, so no prompt-cache re-prime.
- Menus are **unbounded** — the user can scroll back; there's no
  4-or-5-row UI cap.
- Menus support **multi-column metadata** when needed.
- Picking is **one keystroke** (the row key — number or letter).

## Top-level menu (canonical layout — v2.90.0)

The orchestrator queues this menu spec via `cpv_menu.py` and ends the
turn. The Stop hook emits it via `systemMessage`. Cancel/Exit is key `0`:

- **1 — Validate** — Check that a plugin / marketplace / component is well-formed
- **2 — Fix** — Auto-fix issues that a previous validation found
- **3 — Optimize for Cache** — Prompt-cache invalidation audit + cache-aware refactor (CA-01..06)
- **4 — Diagnose** — Deep audit + AI-graded quality review (semantic, opus, on request)
- **5 — Update** — Upgrade plugin to latest canonical pipeline standard
- **6 — Create** — Scaffold plugin, marketplace, skill, agent, command, hook, MCP
- **7 — Publish & Migrate** — Branch rules, link to marketplace, publish, migrate marketplace
- **8 — Manage** — List installed plugins, install / update / enable / disable / doctor
- **H — Help / About** — Category overview, command list, CPV version
- **A — Ask the agent** — Let the agent suggest the best next action right now
- **0 — Cancel / Exit** — Terminate without action

The fixed key→action map for the top-level menu:

| Key | Action ID | What it routes to |
|-----|-----------|-------------------|
| 1   | validate       | §3.1 Validate sub-menu |
| 2   | fix            | §3.2 Fix sub-menu |
| 3   | cache_optimize | §3.3 Optimize for Cache sub-menu |
| 4   | diagnose       | §3.4 Diagnose sub-menu |
| 5   | update         | §3.5 Update sub-menu |
| 6   | create         | §3.6 Create sub-menu |
| 7   | publish        | §3.7 Publish & Migrate sub-menu |
| 8   | manage         | §3.8 Manage sub-menu |
| H   | help           | §3.10H Help / About sub-menu |
| A   | ask            | Ask-the-agent free-form Opus chat |
| 0   | cancel         | Stop without action |

## Workflow (the orchestrator MUST follow this exact sequence)

1. **Run the top-level menu's heredoc recipe** from `menu-tree.md` §3.0
   (single Bash tool call: `python cpv_menu.py - >/dev/null <<'JSON' … JSON`).
   Then END THE TURN. **Emit ZERO chat text** — no "queued", no "Stop
   hook will emit", no "menu will appear", no narration of any kind.
   The menu IS the entire user-visible output. NEVER print the menu
   inline. NEVER use the Write or Edit tool to stage the spec — the
   heredoc passes JSON over stdin, which is exactly why this design is
   silent.
2. **Wait** for the user's next message. Parse the key (number or letter,
   case-insensitive).
3. **On `0` at any level** → respond with a single line `Cancelled — no
   actions taken.` and stop.
4. **On a category key** → look up the action_id in the fixed map above
   (or in the sub-menu-specific maps in `menu-tree.md`). Run the
   sub-menu's heredoc recipe and end the turn silently. Sub-menus
   include `B — Back` AND `0 — Cancel / Exit`.
5. **On a leaf key** → ask required arguments as plain-text questions,
   then execute the chosen command's instructions inline (read its `.md`
   file, follow its bash). Do NOT just print "run /cpv-validate-plugin" —
   actually run the workflow.
6. **Always run via the launcher.** Every validator invocation must use
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" <alias>`
   — never call validate scripts directly from the plugin cache.
7. **Report back** the compact summary + report-file path.
8. **Run the §3.99 "do something else?" heredoc** from `menu-tree.md`
   (or the §3.10 post-validate fix menu, for Validate leaves) and end
   the turn silently. On `0` → `Done.`

## What this command does NOT do

- It does NOT replace the individual slash commands that survived
  consolidation (the batch family, `the-skills-menu-create`,
  `cpv-pre-install-scan`). Power users who know exactly what they need
  can still invoke those directly. This menu is for discovery and
  one-stop navigation of every other workflow.
- It does NOT auto-install anything without confirmation. For
  `cpv-doctor --install-scanners` (which runs `brew install`,
  `pipx install`, etc. on your machine), the menu always asks before
  proceeding.
- It does NOT bypass the launcher. Every validator call routes through
  `remote_validation.py` for environment isolation.
- It does NOT use `AskUserQuestion`. Stop-hook menus + plain-text
  prompts only.
- It does NOT print menus inline. The Stop hook emits via `systemMessage`
  post-turn, so menus stay out of the agent transcript and the prompt
  cache. There is NO inline fallback — if `claude-menu-system` is not
  installed, `cpv_menu.py` fails fast with an install hint (per
  TRDD-4de479a0, no-legacy rule).

## Examples

<example>
user: /cpv-main-menu
assistant: [Queues top-level menu spec via cpv_menu.py and ends the turn. The CMS Stop hook emits the menu (11 rows including `0 — Cancel / Exit`) via systemMessage.]
user: 1
assistant: [Queues §3.1 Validate sub-menu spec; Stop hook emits the menu including `B — Back` and `0 — Cancel / Exit`.]
user: 1
assistant: Path to the plugin to validate? (e.g. ~/Code/my-plugin/)
user: ~/Code/my-plugin/
assistant: [Runs the launcher → plugin alias → reads stdout summary]
✓ Plugin Validation: PASS. Report: reports/validate_plugin/20260502_143012+0200-my-plugin.md
[Queues the §3.10 post-validate fix menu spec and ends the turn.]
user: 0
assistant: Done.
</example>

<example>
user: /cpv-main-menu
assistant: [Queues top-level menu spec.]
user: 0
assistant: Cancelled — no actions taken.
</example>

<example>
user: /cpv-main-menu
assistant: [Queues top-level menu spec.]
user: 8
assistant: [Queues §3.8 Manage sub-menu spec.]
user: B
assistant: [Re-queues the top-level menu spec.]
user: 0
assistant: Cancelled — no actions taken.
</example>

## Related Commands

- `/cpv-batch-validate <input>` — direct batch validation (skips this menu)
- `/cpv-batch-fix <input>` — direct batch fix (skips this menu)
- `/the-skills-menu-create <plugin>` — apply the skills-menu pattern to any plugin
- `/cpv-pre-install-scan <target>` — pre-install security gate
- See the full list under "Help / About" → "List every CPV command" in the menu
