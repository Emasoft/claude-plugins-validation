---
name: link-plugin-marketplace
description: Link an existing plugin to an existing marketplace (local or GitHub source). Use when appending an existing plugin to a marketplace.json. Used dynamically via the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks GitHub setup → Link plugin to marketplace, or any flow needs to append an existing plugin to a marketplace.json
user-invocable: false
allowed-tools: Bash(uv:*), Read, Glob
---

# link-plugin-marketplace

## Overview

Appends an existing plugin to an existing marketplace's `marketplace.json`, preserving existing entries. Uses the correct `source.source` schema key (not the legacy `source.type`). Loaded by `cpv-main-menu-agent` via the GitHub setup → Link plugin menu branch.

## Prerequisites

- `uv` on PATH
- A local marketplace directory containing `.claude-plugin/marketplace.json` (or `marketplace.json` at root)
- A plugin to link, given as either:
  - A local plugin directory path (will be converted to relative-to-marketplace source)
  - A GitHub repo slug `owner/repo` (emits `{source: "github", repo: "owner/repo"}`)

## Instructions

1. Confirm the marketplace's `marketplace.json` exists at the canonical location.
2. Choose the plugin source form: local path or `owner/repo` GitHub slug.
3. Invoke `manage_plugin.py --link-plugin <marketplace-path> <plugin-spec>`.
4. The script appends a new entry; existing entries are NEVER overwritten.
5. Re-validate the marketplace via `plugin-validation-skill` (or `validate_marketplace.py --strict`).

Copy this checklist and track your progress:

- [ ] Marketplace path confirmed valid
- [ ] Plugin spec form chosen (local path OR `owner/repo`)
- [ ] `manage_plugin.py --link-plugin` executed
- [ ] `marketplace.json` updated
- [ ] `validate_marketplace.py --strict` re-run, exit 0

## Output

- A new entry appended to `marketplace.json::plugins[]`.
- For local plugins: `source: "relative-path"` with relative path stored.
- For GitHub plugins: `source: "github"` with `repo: "owner/repo"`.

## Error Handling

| Error | Resolution |
|-------|------------|
| Marketplace not found | Verify path; check for `.claude-plugin/marketplace.json` |
| Plugin already linked | Re-runs are idempotent — duplicate entries are skipped |
| Local plugin not found | Verify path; `.claude-plugin/plugin.json` must exist |
| GitHub repo invalid | Verify `owner/repo` slug exists on GitHub |
| Validator regression after link | Check upstream cross-validation (RC-MKPL-*) findings |

## Examples

Link a local plugin into a local marketplace:

```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" \
  --link-plugin ~/marketplaces/my-hub ~/src/my-plugin
```

Link a GitHub plugin into a local marketplace:

```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" \
  --link-plugin ~/marketplaces/my-hub Emasoft/claude-plugins-validation
```

## Resources

- `create-plugin` skill — create a new marketplace from scratch
- `plugin-management` skill — full plugin lifecycle management
- `plugin-validation-skill` — validate a GitHub marketplace after linking
- `marketplace-authoring-contract` skill — entry shape rules (name, version, source)
