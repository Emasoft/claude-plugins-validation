# skills-catalog — full per-skill index

## Table of Contents

- [Validation / diagnostic skills](#validation--diagnostic-skills)
- [Fix / migration skills](#fix--migration-skills)
- [Scaffold / build skills](#scaffold--build-skills)
- [Publish / release skills](#publish--release-skills)
- [Routing / UX skills](#routing--ux-skills)
- [Invocation pattern](#invocation-pattern)

The skills below are reachable from any CPV agent via the
fully-qualified `Skill` tool call:

```text
Skill({skill: "claude-plugins-validation:<name>", args: "<optional args>"})
```

No agent owns any of these — they are a shared library. The TRDD-478d9687
universal-loader pattern makes every skill available to every agent.

## Validation / diagnostic skills

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `plugin-validation-skill` | plugin path | structural findings + RC-NN codes | First step of any diagnose / fix flow |
| `skill-validation-skill` | SKILL.md path | single-skill findings (Nixtla strict mode) | Pin a single skill's frontmatter + body |
| `cache-validation-skill` | plugin path | CA-01..CA-06 cache-pattern findings | Cache audit / pre-publish check |
| `semantic-validation-skill` | skill/agent path | AI-driven A-F grade (opus[1m] only — expensive) | Explicit opt-in for semantic grading |

## Fix / migration skills

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `fix-validation` | validation report + plugin path | mechanical per-RC fix recipes | The validate→fix loop (`plugin-fixer` normal mode) |
| `fix-marketplace-validation` | marketplace report | mechanical marketplace fixes | Marketplace findings |
| `migrate-marketplace-architecture` | layout label + marketplace path | A↔B↔C conversion | When the layout itself is wrong |
| `canonical-pipeline` | plugin path | §0..§5 pipeline migration recipes | Legacy-plugin upgrade |
| `batch-fix-protocol` | shard manifest path | schema reference + per-finding fix mappings | `batch_shard` mode + `[BATCH_REQUIRED]` exits |
| `deterministic-codemod` | plugin path | zero-LLM mechanical codemods applied | Pre-LLM-pass quick wins |
| `marketplace-authoring-contract` | marketplace + upstream snapshot | drift findings + reconciliation steps | Cross-validation when authoring a new marketplace |

## Scaffold / build skills

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `standardize-plugin` | plugin path | applies current pipeline templates | Bring a plugin up to current standards |
| `create-plugin` | name + target dir + layout | new plugin tree | New plugin from scratch |
| `setup-plugin-repo` | plugin path | GitHub repo + CI/CD wired | Connect a plugin to its repo |
| `setup-github-marketplace` | name + owner | new marketplace tree | New marketplace from scratch |
| `setup-marketplace-auto-notification` | plugin path | auto-notify chain wired | Hook into the marketplace's auto-update flow |
| `link-plugin-marketplace` | plugin + marketplace | wires plugin into marketplace.json | Register a plugin |
| `pack-components` | folder of components | assembled plugin | Pack loose components |
| `add-component-to-plugin` | component path + plugin path | component added | Arbitrary component injection |
| `add-dependency` | spec + plugin path | dependency declared | Add a runtime dep |
| `add-hook` | hook spec + plugin path | hooks.json entry | New hook |
| `register-mcp` | mcp spec + plugin path | .mcp.json entry | New MCP server |
| `scaffold-agent` | name + plugin path | agents/<name>.md created | New agent |
| `scaffold-command` | name + plugin path | commands/<name>.md created | New slash command |
| `scaffold-skill` | name + plugin path | skills/<name>/SKILL.md created | New skill |

## Publish / release skills

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `strip-dev-submodules` | plugin path | dev-only submodules removed | Pre-publish cleanup |
| `refresh-readme` | plugin path | auto-sections regenerated | Pre-publish README sync |
| `bump-version` | plugin path + bump-level | versions bumped in plugin.json + pyproject.toml | Manual bump (publish.py handles it automatically) |
| `show-version` | plugin path | current version string | Read-only version check |
| `publish-to-marketplace` | plugin path | publish.py pipeline reference | Manual publish workflow |

## Routing / UX skills

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `plugin-management` | command + plugin name | install/uninstall/enable/disable/list operations | Plugin lifecycle |
| `cpv-main-menu-skill` | — | the /cpv-main-menu menu tree | Only `cpv-main-menu-agent` loads this — others should not |
| `cpv-format-menu` | menu JSON spec | Unicode-bordered menu table | Slash command bodies render menus via this |

## Invocation pattern

Every agent loading these skills must use the fully-qualified form so
the orphan-detection test recognises them:

```text
Skill({skill: "claude-plugins-validation:plugin-validation-skill", args: "/path/to/plugin"})
Skill({skill: "claude-plugins-validation:fix-validation"})
Skill({skill: "claude-plugins-validation:batch-fix-protocol"})
Skill({skill: "claude-plugins-validation:canonical-pipeline"})
Skill({skill: "claude-plugins-validation:cache-validation-skill"})
Skill({skill: "claude-plugins-validation:semantic-validation-skill"})
Skill({skill: "claude-plugins-validation:skill-validation-skill"})
Skill({skill: "claude-plugins-validation:fix-marketplace-validation"})
Skill({skill: "claude-plugins-validation:migrate-marketplace-architecture"})
Skill({skill: "claude-plugins-validation:marketplace-authoring-contract"})
Skill({skill: "claude-plugins-validation:deterministic-codemod"})
Skill({skill: "claude-plugins-validation:standardize-plugin"})
Skill({skill: "claude-plugins-validation:create-plugin"})
Skill({skill: "claude-plugins-validation:setup-plugin-repo"})
Skill({skill: "claude-plugins-validation:setup-github-marketplace"})
Skill({skill: "claude-plugins-validation:setup-marketplace-auto-notification"})
Skill({skill: "claude-plugins-validation:link-plugin-marketplace"})
Skill({skill: "claude-plugins-validation:pack-components"})
Skill({skill: "claude-plugins-validation:add-component-to-plugin"})
Skill({skill: "claude-plugins-validation:add-dependency"})
Skill({skill: "claude-plugins-validation:add-hook"})
Skill({skill: "claude-plugins-validation:register-mcp"})
Skill({skill: "claude-plugins-validation:scaffold-agent"})
Skill({skill: "claude-plugins-validation:scaffold-command"})
Skill({skill: "claude-plugins-validation:scaffold-skill"})
Skill({skill: "claude-plugins-validation:strip-dev-submodules"})
Skill({skill: "claude-plugins-validation:refresh-readme"})
Skill({skill: "claude-plugins-validation:bump-version"})
Skill({skill: "claude-plugins-validation:show-version"})
Skill({skill: "claude-plugins-validation:publish-to-marketplace"})
Skill({skill: "claude-plugins-validation:plugin-management"})
Skill({skill: "claude-plugins-validation:cpv-main-menu-skill"})
Skill({skill: "claude-plugins-validation:cpv-format-menu"})
Skill({skill: "claude-plugins-validation:the-skills-menu-create"})
```
