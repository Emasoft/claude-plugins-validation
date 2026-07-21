---
name: cpv-the-skills-menu-create
description: "Convert any Claude Code plugin from static agent skill assignment to cpv-the-skills-menu method. Use when migrating a plugin so its agents load operational skills dynamically via the Skill() tool instead of preloading static lists. Trigger with /cpv-the-skills-menu-create or when the user asks to migrate / standardize / decouple a plugin's skill discovery. Used via cpv-the-skills-menu (TRDD-478d9687)."
user-invocable: true
---

# cpv-the-skills-menu-create — universal skill-discovery migrator

## Overview

Convert any Claude Code plugin from static `skills:` frontmatter
preloads to **cpv-the-skills-menu method**. After this skill runs,
every agent in the target plugin has only `cpv-the-skills-menu` in
its preload list and picks operational skills dynamically via the
`Skill()` tool.

The canonical spec lives at
[the-skills-menu-spec](references/the-skills-menu-spec.md) — see the
Resources section for the full heading list.

## Prerequisites

- A Claude Code plugin repository (local path OR Git URL OR plugin name resolvable in the current workspace).
- Git available on `$PATH` (for cloning remote targets).
- Write access to the target plugin's filesystem.

## Instructions

1. Resolve the target plugin per the spec's §Target resolution — try in order: explicit Git URL, explicit local path, plugin-in-marketplace expression, bare plugin name search.
2. Confirm plugin shape per §Plugin detection — `.claude-plugin/plugin.json` + `agents/` + `skills/`. If ambiguous, ask the user.
3. Discover agents in `<plugin-root>/agents/*.md` (must have YAML frontmatter).
4. Discover skills in `<plugin-root>/skills/*/SKILL.md`. Note any standalone (user/local/project-scope) skills the plugin references.
5. Detect the plugin namespace from `.claude-plugin/plugin.json` `name` field (fallback chain in §Plugin namespace detection).
6. Generate the catalog skill at `<plugin-root>/skills/cpv-the-skills-menu/SKILL.md` with the frontmatter + body shape from §Generated frontmatter + §Generated content. Required sections: `## Standalone Skills`, `## Plugin Skills`. Do NOT list `cpv-the-skills-menu` inside its own catalog.
7. Rewrite every agent's frontmatter `skills:` list to exactly `[cpv-the-skills-menu]` per §Agent frontmatter rewrite rule. Preserve every other field.
8. Insert the mandatory dynamic-loading instruction (§Agent body instruction rule) immediately after the frontmatter in every agent body.
9. Run the Skill independence review (§Skill independence review). Flag any skill that assumes a specific named caller agent.
10. Verify per §Verification checklist. Produce a diff summary.
11. Report per §Final report.

## Output

A migration report following the §Final report template plus the git
diff of the plugin tree. The plugin now contains the catalog skill +
agents that declare only the catalog and use the dynamic-loading line.

## Error Handling

| # | Error | Resolution |
|---|-------|------------|
| 1 | Target plugin cannot be located | Surface attempts; let the user disambiguate |
| 2 | Multiple candidates | List `.claude-plugin/plugin.json` `name` fields; ask user |
| 3 | Working tree is dirty | Refuse unless `--force-dirty`; ask user to commit / stash |
| 4 | Agent file lacks frontmatter | Skip + report in "Potential manual review needed" |
| 5 | Catalog skill already exists | Read first. Has both `## Standalone Skills` + `## Plugin Skills` headings → in-place refresh (re-derive the two sections, keep hand-written structural prose). Otherwise (a hand-curated skill that merely shares the name) → never clobber: back it up to a sibling .bak file, then ask the user to overwrite / merge / abort. See spec §Safety rules. |
| 6 | Skill body tightly coupled to a named caller agent | Flag for manual review unless `--full-cleanup` is on, then rewrite to be agent-agnostic |
| 7 | Plugin has no agents | Not an error — generate the catalog for reference, report 0 agents migrated, skip agent rewrites (spec §Agent discovery → "No agents found") |
| 8 | Plugin has only the catalog skill, no other skills | Not an error — generate catalog with placeholder sections, report 0 operational skills indexed (spec §Skill discovery → "No skills found") |

## Examples

```yaml
# Local plugin path
Skill({skill: "claude-plugins-validation:cpv-the-skills-menu-create", args: "~/code/projects/my-plugin/"})

# Plugin inside a marketplace on GitHub
Skill({skill: "claude-plugins-validation:cpv-the-skills-menu-create", args: "from the example-plugin in github.com/example-org/example-marketplace"})

# Bare plugin name (searches workspace + marketplaces)
Skill({skill: "claude-plugins-validation:cpv-the-skills-menu-create", args: "my-scraping-plugin"})

# With full cleanup of agent-coupled skill bodies
Skill({skill: "claude-plugins-validation:cpv-the-skills-menu-create", args: "~/code/my-plugin/ --full-cleanup"})
```

## Resources

- [the-skills-menu-spec](references/the-skills-menu-spec.md) — full canonical spec
  > Purpose · Invocation examples · What this skill must do · Target resolution · Plugin detection · Agent discovery · Skill discovery · Generated skill name + path · Generated frontmatter · Generated content · Plugin namespace detection · Agent frontmatter rewrite rule · Agent body instruction rule · Skill independence review · Safety rules · Verification · Final report · Expected result
