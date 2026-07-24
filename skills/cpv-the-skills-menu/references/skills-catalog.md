# skills-catalog — full per-skill index

## Table of Contents

- [Validation / diagnostic skills](#validation--diagnostic-skills)
- [Fix / migration skills](#fix--migration-skills)
- [Scaffold / build skills](#scaffold--build-skills)
- [Publish / release skills](#publish--release-skills)
- [Routing / UX skills](#routing--ux-skills)
- [Batch / fleet skills](#batch--fleet-skills)
- [Scope-aware diagnostics](#scope-aware-diagnostics)
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
| `cpv-plugin-validation-skill` | plugin path | structural findings + RC-NN codes | First step of any diagnose / fix flow |
| `cpv-skill-validation-skill` | SKILL.md path | single-skill findings (Nixtla strict mode) | Pin a single skill's frontmatter + body |
| `cpv-cache-validation-skill` | plugin path | CA-01..CA-07 cache-pattern findings | Cache audit / pre-publish check |
| `cpv-semantic-validation-skill` | skill/agent path | AI-driven A-F grade (opus[1m] only — expensive) | Explicit opt-in for semantic grading |

## Fix / migration skills

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `cpv-fix-validation` | validation report + plugin path | mechanical per-RC fix recipes | The validate→fix loop (`cpv-plugin-fixer-agent` normal mode) |
| `cpv-fix-marketplace-validation` | marketplace report | mechanical marketplace fixes | Marketplace findings |
| `cpv-migrate-marketplace-architecture` | layout label + marketplace path | A↔B↔C conversion | When the layout itself is wrong |
| `cpv-canonical-pipeline` | plugin path | §0..§5 pipeline migration recipes | Legacy-plugin upgrade |
| `cpv-batch-fix-protocol` | shard manifest path | schema reference + per-finding fix mappings | `batch_shard` mode + `[BATCH_REQUIRED]` exits |
| `cpv-deterministic-codemod` | plugin path | zero-LLM mechanical codemods applied | Pre-LLM-pass quick wins |
| `cpv-marketplace-authoring-contract` | marketplace + upstream snapshot | drift findings + reconciliation steps | Cross-validation when authoring a new marketplace |
| `cpv-devitalize-threats` | security report + plugin path | per-shape inert-data transform recipes | The `cpv-plugin-devitalizer-agent` scan→devitalize loop |
| `cpv-harden-and-redact` | security report or plugin path | per-finding redact / harden / flag recipes | The `cpv-plugin-leaks-preventer-agent` scan→redact/harden loop |

## Scaffold / build skills

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `cpv-standardize-plugin` | plugin path | applies current pipeline templates | Bring a plugin up to current standards |
| `cpv-create-plugin` | name + target dir + layout | new plugin tree | New plugin from scratch |
| `cpv-setup-plugin-repo` | plugin path | GitHub repo + CI/CD wired | Connect a plugin to its repo |
| `cpv-setup-github-marketplace` | name + owner | new marketplace tree | New marketplace from scratch |
| `cpv-setup-marketplace-auto-notification` | plugin path | auto-notify chain wired | Hook into the marketplace's auto-update flow |
| `cpv-link-plugin-marketplace` | plugin + marketplace | wires plugin into marketplace.json | Register a plugin |
| `cpv-pack-components` | folder of components | assembled plugin | Pack loose components |
| `cpv-add-component-to-plugin` | component path + plugin path | component added | Arbitrary component injection |
| `cpv-add-dependency` | spec + plugin path | dependency declared | Add a runtime dep |
| `cpv-add-hook` | hook spec + plugin path | hooks.json entry | New hook |
| `cpv-register-mcp` | mcp spec + plugin path | .mcp.json entry | New MCP server |
| `cpv-scaffold-agent` | name + plugin path | agents/<name>.md created | New agent |
| `cpv-scaffold-command` | name + plugin path | commands/<name>.md created | New slash command |
| `cpv-scaffold-skill` | name + plugin path | skills/<name>/SKILL.md created | New skill |
| `cpv-create-mono-agent` | plugin path | agents/<slug>-mono-agent.md (all non-meta skills inlined) | EXPERIMENTAL prefill-everything mega-agent |
| `cpv-create-micro-agents-workflow` | plugin path | launcher agent + workflows/<base>-micro-agents.ts | EXPERIMENTAL RLM skill-per-agent workflow |

## Publish / release skills

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `cpv-strip-dev-submodules` | plugin path (+ visibility) | dev folders / compile source moved to a separate repo, `{path,url,sha}` recorded | Pre-publish cleanup; the ship-only-binary PUBLIC source-repo migration |
| `cpv-refresh-readme` | plugin path | auto-sections regenerated | Pre-publish README sync |
| `cpv-bump-version` | plugin path + bump-level | versions bumped in plugin.json + pyproject.toml | Manual bump (publish.py handles it automatically) |
| `cpv-show-version` | plugin path | current version string | Read-only version check |
| `cpv-publish-to-marketplace` | plugin path | publish.py pipeline reference | Manual publish workflow |

## Routing / UX skills

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `cpv-plugin-management` | command + plugin name | install/uninstall/enable/disable/list operations | Plugin lifecycle |
| `cpv-main-menu-skill` | — | the /cpv-main-menu menu tree | Loadable by any agent via cpv-the-skills-menu |
| `cpv-the-skills-menu-create` | plugin path | plugin migrated from static `skills:` preloads to cpv-the-skills-menu method | Decouple a plugin's agents from static skill lists (runtime `Skill()` discovery) |

## Batch / fleet skills

TRDD-3dcbb37c (v2.101.0). Each skill fans out N parallel
subagents from a single main-session message (default 8, cap 16)
across every plugin in the input spec. Input grammar: single
plugin / plugin URL / marketplace local/URL / list / `@listfile`
/ comma-separated.

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `cpv-batch-validate` | plugin / marketplace / list | per-plugin status table + DONE summary | Fleet-wide validation snapshot |
| `cpv-batch-security-audit` | plugin / marketplace / list | per-plugin security status + DONE summary | Fleet-wide supply-chain risk snapshot |
| `cpv-batch-caching-audit` | plugin / marketplace / list | per-plugin CA-* findings (read-only) | Fleet-wide caching snapshot, no fixes |
| `cpv-batch-caching-optimize` | plugin / marketplace / list | per-plugin caching before/after + DONE | Apply CA-01..CA-07 fixes across many plugins |
| `cpv-batch-fix` | plugin / marketplace / list | per-plugin fix status + DONE | Apply validation fixes (single-plugin → per-shard; marketplace → per-plugin fan-out) |
| `cpv-batch-validate-and-fix` | plugin / marketplace / list | per-plugin before/after + FP-verified count | Same-turn validate + fix (~3× cheaper than separate passes) |
| `cpv-batch-full-scan-and-fix` | plugin / marketplace / list | per-plugin before/after + by_checker | Maximum-coverage same-turn sweep (validate + security + cache + fix) |

## Scope-aware diagnostics

TRDD-a175f78d (v2.101.0). LOCAL paths only — URL inputs are
CRITICAL errors because the doctor needs filesystem access to
`~/.claude/` and `<project>/.claude/`. Input grammar: project
folder / list / `@listfile`. Default: `$PWD`.

| Skill | Inputs | Returns | When to invoke |
|-------|--------|---------|-----------------|
| `cpv-batch-scope-diagnose` | project folder + `--scope {user\|project\|local\|full}` | per-project findings + conflict count | Read-only scope-aware diagnostic across a fleet |
| `cpv-batch-scope-fix` | project folder + `--scope` | per-project before/after + pending_fixes | Apply mechanical NIT/CRITICAL fixes; record MAJOR/MINOR for approval |
| `cpv-batch-scope-diagnose-and-fix` | project folder + `--scope` | per-project before/after + pending_fixes | Same-turn scope-aware diagnose + apply safe fixes inline |

## Invocation pattern

Every agent loading these skills must use the fully-qualified form so
the orphan-detection test recognises them:

```text
Skill({skill: "claude-plugins-validation:cpv-plugin-validation-skill", args: "/path/to/plugin"})
Skill({skill: "claude-plugins-validation:cpv-fix-validation"})
Skill({skill: "claude-plugins-validation:cpv-batch-fix-protocol"})
Skill({skill: "claude-plugins-validation:cpv-canonical-pipeline"})
Skill({skill: "claude-plugins-validation:cpv-cache-validation-skill"})
Skill({skill: "claude-plugins-validation:cpv-semantic-validation-skill"})
Skill({skill: "claude-plugins-validation:cpv-skill-validation-skill"})
Skill({skill: "claude-plugins-validation:cpv-fix-marketplace-validation"})
Skill({skill: "claude-plugins-validation:cpv-migrate-marketplace-architecture"})
Skill({skill: "claude-plugins-validation:cpv-marketplace-authoring-contract"})
Skill({skill: "claude-plugins-validation:cpv-devitalize-threats"})
Skill({skill: "claude-plugins-validation:cpv-harden-and-redact"})
Skill({skill: "claude-plugins-validation:cpv-deterministic-codemod"})
Skill({skill: "claude-plugins-validation:cpv-standardize-plugin"})
Skill({skill: "claude-plugins-validation:cpv-create-plugin"})
Skill({skill: "claude-plugins-validation:cpv-setup-plugin-repo"})
Skill({skill: "claude-plugins-validation:cpv-setup-github-marketplace"})
Skill({skill: "claude-plugins-validation:cpv-setup-marketplace-auto-notification"})
Skill({skill: "claude-plugins-validation:cpv-link-plugin-marketplace"})
Skill({skill: "claude-plugins-validation:cpv-pack-components"})
Skill({skill: "claude-plugins-validation:cpv-add-component-to-plugin"})
Skill({skill: "claude-plugins-validation:cpv-add-dependency"})
Skill({skill: "claude-plugins-validation:cpv-add-hook"})
Skill({skill: "claude-plugins-validation:cpv-register-mcp"})
Skill({skill: "claude-plugins-validation:cpv-scaffold-agent"})
Skill({skill: "claude-plugins-validation:cpv-scaffold-command"})
Skill({skill: "claude-plugins-validation:cpv-scaffold-skill"})
Skill({skill: "claude-plugins-validation:cpv-create-mono-agent"})
Skill({skill: "claude-plugins-validation:cpv-create-micro-agents-workflow"})
Skill({skill: "claude-plugins-validation:cpv-strip-dev-submodules"})
Skill({skill: "claude-plugins-validation:cpv-refresh-readme"})
Skill({skill: "claude-plugins-validation:cpv-bump-version"})
Skill({skill: "claude-plugins-validation:cpv-show-version"})
Skill({skill: "claude-plugins-validation:cpv-publish-to-marketplace"})
Skill({skill: "claude-plugins-validation:cpv-plugin-management"})
Skill({skill: "claude-plugins-validation:cpv-main-menu-skill"})
Skill({skill: "claude-plugins-validation:cpv-the-skills-menu-create"})
Skill({skill: "claude-plugins-validation:cpv-batch-validate"})
Skill({skill: "claude-plugins-validation:cpv-batch-security-audit"})
Skill({skill: "claude-plugins-validation:cpv-batch-caching-audit"})
Skill({skill: "claude-plugins-validation:cpv-batch-caching-optimize"})
Skill({skill: "claude-plugins-validation:cpv-batch-fix"})
Skill({skill: "claude-plugins-validation:cpv-batch-validate-and-fix"})
Skill({skill: "claude-plugins-validation:cpv-batch-full-scan-and-fix"})
Skill({skill: "claude-plugins-validation:cpv-batch-scope-diagnose"})
Skill({skill: "claude-plugins-validation:cpv-batch-scope-fix"})
Skill({skill: "claude-plugins-validation:cpv-batch-scope-diagnose-and-fix"})
```
