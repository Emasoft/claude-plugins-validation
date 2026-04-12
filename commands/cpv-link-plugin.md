---
name: cpv-link-plugin
description: Link an existing plugin to an existing marketplace (local or GitHub source)
allowed-tools: Read, Bash, Glob
argument-hint: "<marketplace-path> <plugin-spec>"
user-invocable: true
---

# /cpv-link-plugin

Appends an existing plugin to an existing marketplace's `marketplace.json`, preserving existing entries.

## Usage

```
/cpv-link-plugin <marketplace-path> <plugin-spec>
```

Arguments:
- `marketplace-path` — local directory containing `.claude-plugin/marketplace.json` (or `marketplace.json` at root)
- `plugin-spec` — one of:
  - `./relative/path` — local plugin directory (converted to relative-to-marketplace source)
  - `owner/repo` — GitHub source (emits `{source: "github", repo: "owner/repo"}`)

## Examples

Link a local plugin into a local marketplace:
```
/cpv-link-plugin ~/marketplaces/my-hub ~/src/my-plugin
```

Link a GitHub plugin into a local marketplace:
```
/cpv-link-plugin ~/marketplaces/my-hub Emasoft/claude-plugins-validation
```

## Execution

```bash
uv run python scripts/manage_plugin.py --link-plugin "$1" "$2"
```

The command uses the correct `source.source` schema key (not the legacy `source.type`).

## Related

- `/cpv-create` — create a new marketplace from scratch
- `/cpv-manage` — full plugin lifecycle management
- `/cpv-validate-github-marketplace` — validate a GitHub marketplace after linking
