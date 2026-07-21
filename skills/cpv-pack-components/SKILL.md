---
name: cpv-pack-components
description: Pack a folder of standalone components (skill/agent/command/hook/mcp/lsp/monitor/output-style) into a new installable plugin. Use when bundling loose components into a single publishable plugin. Used dynamically via cpv-the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Create → Pack components, or any flow needs to bundle a folder of standalone components into a single installable plugin
user-invocable: false
---

# cpv-pack-components

## Overview

Converts a folder of standalone Claude Code components into a single installable plugin. Useful for recovering from "Phase 0 plugin-shape detection refused" — wrap the detected components into a real plugin shape that loads correctly. Also useful for rolling skills / agents / commands from disparate projects into a shared plugin, or migrating ad-hoc component folders to publishable plugins without hand-editing manifests. The script discovers every supported component type, validates the selection, then scaffolds a fresh plugin and copies the components into their canonical locations. Loaded dynamically via cpv-the-skills-menu, reached via the Create → Pack components menu branch.

## Prerequisites

- `uv` on PATH
- A source folder containing one or more detectable components
- For marketplace integration: optional marketplace path

## Instructions

1. Run `--list-only` first to discover what's in the source folder (no writes).
2. Choose `--all` (pack every discovered component) or `--include` (multi-select per type).
3. Provide the new plugin's `--name`, `--description`, `--author`, `--author-email`, and optionally `--github-owner`.
4. Optionally chain `--add-to-marketplace` or `--create-marketplace` for one-shot publishing.
5. After packing, run plugin validation on the new plugin (see `cpv-plugin-validation-skill`).
6. If any CRITICAL / MAJOR finding appears, dispatch the **cpv-plugin-fixer-agent agent** with `min_severity=MAJOR` to remediate before publishing.

Copy this checklist and track your progress:

- [ ] `--list-only` run, components confirmed
- [ ] `--all` or `--include` selection finalized
- [ ] Plugin metadata (name, description, author) supplied
- [ ] Pack invocation executed
- [ ] New plugin git-initialised and committed
- [ ] `validate_plugin --strict` exit 0 (or fixer dispatched)

## Output

- A new plugin directory at the target path with:
  - `.claude-plugin/plugin.json`
  - Canonical component subdirectories (`skills/`, `agents/`, `commands/`, `hooks/`, etc.)
  - The source components copied to their canonical locations
- Optional marketplace registration if `--add-to-marketplace` or `--create-marketplace` was passed.

## Error Handling

| Code | Meaning |
|------|---------|
| 0 | OK |
| 1 | Invalid args / source not found |
| 2 | Source folder has no detectable components |
| 3 | Selection conflict (duplicates, `--all` + `--include`, ...) |
| 4 | Scaffolding or pack step failed |
| 5 | Marketplace operation failed |

## Examples

```bash
# Discover what's there (no writes)
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_pack_components.py" \
  /path/to/source-folder --list-only

# Pack EVERY discovered component into a new plugin
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_pack_components.py" \
  /path/to/source-folder /path/to/new-plugin \
  --name my-plugin --description "What the plugin does" \
  --author "Alice" --author-email "alice@example.com" \
  --github-owner alice \
  --all

# Pack only a subset (multi-select via --include)
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_pack_components.py" \
  /path/to/source-folder /path/to/new-plugin \
  --name my-plugin \
  --author "Alice" --author-email "alice@example.com" \
  --include "skill=my-skill,other-skill" \
  --include "agent=my-agent"

# JSON / remote-API mode
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_pack_components.py" \
  /path/to/source /path/to/target --name my-plugin --all --json
```

## Resources

- [Components and Marketplace](references/components-and-marketplace.md) — discovered types, marketplace integration flags
  > Component types discovered · Marketplace integration
- `cpv-create-plugin` skill — scaffold a plugin from scratch
- `cpv-plugin-validation-skill` — validate the newly packed plugin
- `cpv-fix-validation` skill — fix-up recipes after packing
- `cpv-marketplace-authoring-contract` skill — when chaining `--add-to-marketplace`
