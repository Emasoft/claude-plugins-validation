---
name: cpv-format-menu
description: Renders a Unicode-bordered menu/summary/breakdown/status_table from a JSON spec file. Loaded by cpv-doctor, cpv-fix-validation, cpv-fix-marketplace-validation, cpv-cache-optimize. Use when a CPV orchestrator needs to render a dynamic menu in an isolated haiku context so the render does not inherit the parent session's conversation history.
user-invocable: false
allowed-tools: Bash(python3:*), Bash(cat:*), Read
context: fork
model: haiku
agent: general-purpose
arguments: mode spec_path action_map_path
---

# CPV format-menu fork-skill

## Overview

Single-turn rendering helper invoked by a CPV menu-orchestrator command
(`cpv-doctor`, `cpv-fix-validation`, `cpv-fix-marketplace-validation`,
`cpv-cache-optimize`). Runs in an isolated haiku context — no parent
history, no follow-up questions. Reads the JSON spec at `$spec_path`,
runs `scripts/format_menu.py`, returns the rendered text verbatim. The
orchestrator copies that text into its own user-facing response.

`model: haiku` on a command/skill only takes effect "for the rest of the
current turn" (per the Claude Code skills doc). When the parent is opus
with a 1M-token context, switching mid-turn is unsafe — the override
silently degrades. `context: fork` solves this: the fork runs as a fresh
`general-purpose` subagent with no inherited history, so `model: haiku`
actually takes effect.

## Prerequisites

- Invoked via the Skill tool from one of the four orchestrator commands
  (this skill is `user-invocable: false`).
- `${CLAUDE_PLUGIN_ROOT}` is set by Claude Code.
- The orchestrator has already written a valid JSON spec to `$spec_path`
  matching the schema `scripts/format_menu.py` expects for `$mode`.

## Instructions

1. Read the JSON spec from `$spec_path` (Bash `cat` or Read tool).
2. Run the renderer. The mode is `$mode` (one of `menu`, `summary`,
   `breakdown`, `status_table`). For menu mode, capture the action-id
   map to `$action_map_path` if provided; otherwise discard it:

   ```bash
   SPEC="$(cat "$spec_path")"
   if [ -n "$action_map_path" ]; then
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" "$mode" "$SPEC" 2>"$action_map_path"
   else
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" "$mode" "$SPEC" 2>/dev/null
   fi
   ```

3. Emit the script's stdout VERBATIM as the text response. Wrap nothing
   around it — no commentary, no headings, no instructions. The
   orchestrator copies the text into its own user-facing response.

### What this skill does NOT do

- Does NOT ask follow-up questions (no history; user can't reply).
- Does NOT call any tool other than Bash and Read.
- Does NOT explain or guide — that is the orchestrator's job.
- Does NOT add Markdown around the table.

## Output

One text response containing the rendered table. No preamble, no
postamble. The orchestrator copies the text verbatim into its own
user-facing message.

For `menu` mode, the action-id map (`{action_map: {key: action_id, ...}}`)
is written to `$action_map_path` as JSON for the orchestrator's routing
code to read on the next user reply.

## Error Handling

- Non-zero exit (malformed spec, missing keys, invalid JSON) → emit the
  script's stderr message as the text response.
- `$spec_path` missing or unreadable → emit a one-line error naming the
  failed path (orchestrator bug; spec must be written before fork).
- `$mode` invalid → the renderer prints its usage message to stderr;
  emit verbatim.

## Examples

Example 1 — render a post-scan menu (orchestrator: `cpv-doctor`):

```
args: "menu /tmp/cpv-doctor-postscan-spec.json /tmp/cpv-doctor-postscan-map.json"
```

The skill runs `format_menu.py menu` on the spec, captures the action-id
map to `/tmp/cpv-doctor-postscan-map.json`, returns the rendered table.

Example 2 — render a per-recipe breakdown:

```
args: "breakdown /tmp/cpv-doctor-breakdown-spec.json"
```

The skill runs `format_menu.py breakdown` (no action-id map needed for
breakdown mode), returns the rendered breakdown matrix.

## Resources

- `scripts/format_menu.py` — the underlying renderer. Four modes
  (`menu`, `summary`, `breakdown`, `status_table`). Uses
  `unicodedata.east_asian_width` so cell widths match terminal columns
  (BMP dingbats are 1 column; real U+1F000+ emoji are 2).

## How orchestrators invoke this skill

```
Skill({
  skill: "claude-plugins-validation:cpv-format-menu",
  args: "menu /tmp/cpv-doctor-postscan-spec.json /tmp/cpv-doctor-postscan-map.json"
})
```

The orchestrator copies the Skill tool's text result verbatim into its
next response. The action-id map at `/tmp/<cmd>-<purpose>-map.json` is
read by the orchestrator's routing code on the next user reply.
</content>
