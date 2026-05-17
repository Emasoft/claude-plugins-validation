---
name: link-plugin-marketplace
description: Link an existing plugin to an existing marketplace (local or GitHub source)
when_to_use: When the cpv-main-menu user picks GitHub setup → Link plugin to marketplace, or any flow needs to append an existing plugin to a marketplace.json
user-invocable: false
allowed-tools: Read, Bash, Glob
---

# link-plugin-marketplace

Appends an existing plugin to an existing marketplace's `marketplace.json`, preserving existing entries.

## Usage

Arguments:
- `marketplace-path` — local directory containing `.claude-plugin/marketplace.json` (or `marketplace.json` at root)
- `plugin-spec` — one of:
  - `./relative/path` — local plugin directory (converted to relative-to-marketplace source)
  - `owner/repo` — GitHub source (emits `{source: "github", repo: "owner/repo"}`)

## Examples

Link a local plugin into a local marketplace:
```
~/marketplaces/my-hub ~/src/my-plugin
```

Link a GitHub plugin into a local marketplace:
```
~/marketplaces/my-hub Emasoft/claude-plugins-validation
```

## Execution

```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --link-plugin "$1" "$2"
```

The skill uses the correct `source.source` schema key (not the legacy `source.type`).

## Related

- `create-plugin` skill — create a new marketplace from scratch
- `plugin-management` skill — full plugin lifecycle management
- `plugin-validation-skill` — validate a GitHub marketplace after linking
