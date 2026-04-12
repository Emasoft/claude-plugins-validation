# TRDD-26f7fbfc-8472-47ca-8a00-e4be0b29bb67 — Document v2.1.80+ and v2.1.98 Plugin Features

**TRDD ID:** `26f7fbfc-8472-47ca-8a00-e4be0b29bb67`
**Filename:** `design/tasks/TRDD-26f7fbfc-8472-47ca-8a00-e4be0b29bb67-v2-1-80-plugin-features-docs.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Not started
**Priority:** HIGH
**Effort:** MEDIUM
**Source:** `docs_dev/cpv_workflow_audit_20260412.md` section A7 / C2

## Context

CPV validators recognize the following Claude Code v2.1.80+ and v2.1.98 features, but
the creation skills (`create-plugin`, `canonical-pipeline`, `setup-plugin-repo`) do not
mention them. Plugin authors following CPV's guides produce plugins that only use
pre-v2.1.80 features.

| Feature | Validator location | Missing from docs |
|---|---|---|
| `Monitor` tool | `scripts/cpv_validation_common.py:287` | create-plugin, canonical-pipeline |
| `CLAUDE_PLUGIN_OPTION_<KEY>` env vars | `scripts/cpv_validation_common.py:335-346` | all skills |
| `userConfig` in plugin.json | `scripts/validate_plugin.py:283-301` | create-plugin, canonical-pipeline |
| `channels` in plugin.json | `scripts/validate_plugin.py:303-322` | create-plugin, canonical-pipeline |
| `source: "settings"` inline marketplace | v2.1.80 — belongs in settings.json, NOT marketplace.json | setup-github-marketplace |
| `managed-settings.d/` drop-in dir | spec audit section 10 | settings skill (none exists) |
| Plugin skill `name` field for stable invocation | spec audit section 5.10 | skill-development |

## Scope

Create a new reference file in each relevant skill:

1. `skills/create-plugin/references/v2-1-80-features.md`
2. `skills/canonical-pipeline/references/v2-1-80-features.md`
3. `skills/setup-plugin-repo/references/v2-1-80-features.md` (cross-link)

Also add a short section to each SKILL.md pointing at the reference.

## Content outline per reference

```
# Claude Code v2.1.80+ Plugin Features

## Monitor tool
Background command execution, feeds each stdout line to Claude. Same
permission rules as Bash. Declare in agent `tools: [Monitor]`.

## userConfig (plugin.json)
User-configurable values prompted at plugin enable time. Schema:
{"userConfig": {"KEY": {"description": "...", "sensitive": false}}}
Access in hooks/MCP/LSP via ${user_config.KEY}.
Also exported as CLAUDE_PLUGIN_OPTION_<KEY> env var.

## channels (plugin.json)
Channel declarations for message injection. Each channel has
`server` (must match a key in mcpServers) and optional nested userConfig.

## CLAUDE_PLUGIN_OPTION_<KEY> env vars
Auto-exported from each userConfig key. Usable in skill string
substitutions as ${CLAUDE_PLUGIN_OPTION_KEY} (non-sensitive only).

## Inline marketplace (settings.json)
Use source: "settings" in extraKnownMarketplaces to declare a small
marketplace inline — no separate repo needed. Each plugin still needs
a real source (github, npm, etc.). Each must still be enabled in
enabledPlugins.

## managed-settings.d/ drop-in directory
Teams can drop independent settings fragments into
managed-settings.d/*.json. Merged alphabetically on top of
managed-settings.json. See platform-specific paths in spec.

## Plugin skill name field
When a plugin declares "skills": ["./"] pointing to the plugin root,
the SKILL.md frontmatter `name:` determines the skill's invocation
name. Falls back to directory basename if unset. Recommend always
setting `name:` explicitly in v2.1.98+.
```

## Success criteria

- [ ] Each file lists every feature with a valid JSON example
- [ ] Each example validates against CPV's own validators (CI test)
- [ ] SKILL.md files link to the new references
- [ ] A demo plugin in `tests/fixtures/` uses every feature and passes
      `validate_plugin.py` in strict mode
- [ ] README's "What CPV validates" table mentions v2.1.80+ features

## Out of scope

- Changes to the validators themselves (they already accept these)
- Changes to the generator scripts (separate TRDD)
