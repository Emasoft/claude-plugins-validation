---
name: cpv-main-menu
description: Single entry point — Stop-hook menu of every CPV command/skill/agent (validate, fix, create, manage, GitHub, semantic-grade)
---

# /cpv-main-menu — CPV Main Menu

One inline entry point to every CPV command, skill, and agent, shown as a
post-turn **Stop-hook menu** (rendered via `systemMessage`, so it never enters
the transcript or prompt cache). Every menu has a `0 — Cancel / Exit` row;
sub-menus also have `B — Back`.

The full menu tree AND the fixed key→action routing map are the **single source
of truth** in
[`menu-tree.md`](../skills/cpv-main-menu-skill/references/menu-tree.md)
(`cpv-main-menu-skill`, auto-loaded). Do NOT restate the menu rows or the key
map here — read them there.

## Categories (the top menu — full rows + routing live in `menu-tree.md`)

**Validate** · **Fix** · **Optimize for Cache** · **Diagnose** · **Update** ·
**Create** · **Publish & Migrate** · **Manage** — plus `H` (Help / About),
`A` (Ask the agent), `0` (Cancel / Exit).

## Workflow (runs inline in the main session — no subagent fork)

1. **Queue the top menu.** One Bash call: export `CPV_SKILL_MENUS_DIR` to the
   skill's `skill-menus/` dir, then
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" fixed 5 >/dev/null 2>&1`
   (loads the pre-baked `skill-menus/05-main.json`). Then **END THE TURN emitting
   ZERO chat text** — the `claude-menu-system` Stop hook renders the menu via
   `systemMessage`, so it costs no context. NEVER print the menu inline; NEVER
   use Write/Edit to stage a spec (`print_menu.py` loads it by index — that is
   why the Bash card stays tiny and silent).
2. **Wait** for the user's key (number or letter, case-insensitive).
3. **`0`** at any level → reply `Cancelled — no actions taken.` and stop.
   **`B`** in a sub-menu → re-queue the parent menu.
4. **Category key** → look up its `action_id` in `menu-tree.md`, run that
   sub-menu's heredoc recipe, and end the turn silently.
5. **Leaf key** → ask any required arguments as plain-text questions, then
   execute that command's workflow inline (read its `.md`, run its bash) —
   actually run it, don't just name it. Every validator call goes through
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" <alias>` — never
   the plugin-cache scripts directly.
6. **Report** the compact summary + report-file path, then run the §3.99 "do
   something else?" heredoc (or the §3.10 post-validate menu for Validate
   leaves) and end silently. `0` → `Done.`

## Notes

- Does NOT replace the surviving direct commands (the batch family,
  `the-skills-menu-create`, `cpv-pre-install-scan`) — power users invoke those
  directly. This menu is for discovery and one-stop navigation.
- Never auto-installs without confirmation (`cpv-doctor --install-scanners`,
  which runs `brew`/`pipx` on your machine, always asks first).
- Never uses `AskUserQuestion` — Stop-hook menus + plain-text prompts only.
- No inline fallback: if `claude-menu-system` is not installed, `print_menu.py`
  fails fast with an install hint (TRDD-4de479a0, no-legacy rule).

## Example

<example>
user: /cpv-main-menu
assistant: [queues the top menu via `print_menu.py fixed 5`, ends the turn silently; the Stop hook emits it]
user: 1
assistant: [queues the §3.1 Validate sub-menu]
user: 1
assistant: Path to the plugin to validate?
user: ~/Code/my-plugin/
assistant: [runs the launcher] ✓ Plugin Validation: PASS. Report: reports/validate_plugin/20260502_143012+0200-my-plugin.md
[queues the §3.10 post-validate menu]
user: 0
assistant: Done.
</example>

## Related

- `/cpv-batch-validate <input>`, `/cpv-batch-fix <input>` — direct batch (skip the menu)
- `/the-skills-menu-create <plugin>` — apply the skills-menu pattern to any plugin
- `/cpv-pre-install-scan <target>` — pre-install security gate
