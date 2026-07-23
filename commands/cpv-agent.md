---
name: cpv-agent
description: Direct free-form entry to the general-purpose CPV worker. Pass any plugin-quality request as arguments and it routes to the right CPV skill, agent, or script and runs the whole job in an isolated context — validate, scan, fix, optimize cache, create, publish, migrate a marketplace, manage installed plugins, or update the pipeline.
argument-hint: "<free-form CPV request, e.g. 'update the pipeline'>"
user-invocable: true
---

# /cpv-agent — free-form CPV worker (direct dispatch)

`$ARGUMENTS` is a free-form plugin-quality request. Hand it verbatim to the
`claude-plugins-validation:cpv-agent` subagent, which reads the intent-to-action
map in `cpv-the-skills-menu` and autonomously routes the request to the right CPV
skill, agent, or script, running the whole job in its own isolated context (so the
verbose per-file work never enters this session).

This is the direct-slash shortcut to the same worker the main menu reaches via its
`A — Ask the agent` row. Use it when you already know you want the CPV agent and do
not want to open the menu first.

## Workflow

1. If `$ARGUMENTS` is empty, ask the user what CPV task they want, then stop.
2. Otherwise dispatch ONE `claude-plugins-validation:cpv-agent` subagent via the
   Agent tool, passing `$ARGUMENTS` as the task. The default target is the current
   plugin unless the request names another path or GitHub URL. For substantive or
   multi-file work the subagent may itself fan out to specialised CPV work agents
   (`cpv-plugin-fixer-agent`, `cpv-cache-optimizer-agent`, and the like).
3. Relay the subagent's final one-line result and the path to the report it wrote
   under `reports/`.

## Notes

- The `cpv-agent` subagent is also dispatchable directly by other agents as
  `subagent_type: claude-plugins-validation:cpv-agent` — this command only adds the
  user-facing `/cpv-agent` slash surface on top of that existing agent.
- For one bounded edit prefer `cpv-spark-agent`; for an interactive numbered menu
  use `/cpv-main-menu`.
