# Pack-Components — Detail Reference

## Table of Contents

- [Component types discovered](#component-types-discovered)
- [Marketplace integration](#marketplace-integration)

## Component types discovered

| Type | Source locations | Plugin destination |
|------|------------------|--------------------|
| `skill` | root SKILL frontmatter file, `skills/<name>/SKILL.md` | `skills/<name>/` |
| `agent` | `agents/*.md`, root-level `*.md` (heuristic) | `agents/<name>.md` |
| `command` | `commands/*.md`, .md w/ `allowed-tools:` | `commands/<name>.md` |
| `hook` | `hooks/hooks.json` | `hooks/hooks.json` |
| `mcp` | `.mcp.json` | `.mcp.json` |
| `lsp` | `.lsp.json` | `.lsp.json` |
| `monitor` | `monitors/monitors.json` | `monitors/monitors.json` |
| `output-style` | `output-styles/*.md` | `output-styles/<name>.md` |

## Marketplace integration

```bash
# After packing, register the new plugin in an existing marketplace
... --add-to-marketplace /path/to/marketplace-repo

# Or bootstrap a brand new marketplace AND register the plugin in it
... --create-marketplace /path/to/new-marketplace
```
