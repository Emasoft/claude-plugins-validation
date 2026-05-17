---
name: pack-components
description: Pack a folder of standalone components (skill/agent/command/hook/mcp/lsp/monitor/output-style) into a new installable plugin
when_to_use: When the cpv-main-menu user picks Create → Pack components, or any flow needs to bundle a folder of standalone components into a single installable plugin
user-invocable: false
allowed-tools: Bash(uv:*)
---

# pack-components

Convert a folder of standalone Claude Code components into a single
installable plugin. Useful for:

- Recovering from "Phase 0 plugin-shape detection refused" — wrap the
  detected components into a real plugin shape that loads correctly.
- Rolling skills / agents / commands from disparate projects into a
  shared plugin.
- Migrating ad-hoc component folders to publishable plugins without
  hand-editing manifests.

The script discovers every supported component type, validates the
selection, then scaffolds a fresh plugin and copies the components
into their canonical locations.

## Usage

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
  --include "agent=my-agent" \
  --include "command="    # empty list = "all of this type"

# JSON / remote-API mode
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_pack_components.py" \
  /path/to/source /path/to/target --name my-plugin --all --json
```

## Component types discovered

| Type           | Source locations                           | Plugin destination               |
|----------------|--------------------------------------------|----------------------------------|
| `skill`        | `SKILL.md` at root, `skills/<name>/SKILL.md` | `skills/<name>/`               |
| `agent`        | `agents/*.md`, root-level *.md (heuristic) | `agents/<name>.md`               |
| `command`      | `commands/*.md`, .md w/ `allowed-tools:`   | `commands/<name>.md`             |
| `hook`         | `hooks/hooks.json`                         | `hooks/hooks.json`               |
| `mcp`          | `.mcp.json`                                | `.mcp.json`                      |
| `lsp`          | `.lsp.json`                                | `.lsp.json`                      |
| `monitor`      | `monitors/monitors.json`                   | `monitors/monitors.json`         |
| `output-style` | `output-styles/*.md`                       | `output-styles/<name>.md`        |

## Marketplace integration (optional)

```bash
# After packing, register the new plugin in an existing marketplace
... --add-to-marketplace /path/to/marketplace-repo

# Or bootstrap a brand new marketplace AND register the plugin in it
... --create-marketplace /path/to/new-marketplace
```

## Exit codes

| Code | Meaning                                                |
|------|--------------------------------------------------------|
| 0    | OK                                                     |
| 1    | Invalid args / source not found                        |
| 2    | Source folder has no detectable components             |
| 3    | Selection conflict (duplicates, --all + --include, …)  |
| 4    | Scaffolding or pack step failed                        |
| 5    | Marketplace operation failed                           |

## After packing

1. `cd /path/to/new-plugin && git init && git add -A && git commit -m "Initial pack"`
2. Run plugin-validation-skill on `/path/to/new-plugin --strict` to confirm
   every component lands cleanly.
3. If any CRITICAL / MAJOR finding appears, dispatch the **plugin-fixer
   agent** with `min_severity=MAJOR` to remediate before publishing.
