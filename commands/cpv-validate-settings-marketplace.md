---
name: cpv-validate-settings-marketplace
description: Validate extraKnownMarketplaces in a settings.json file
allowed-tools: Read, Bash, Glob
argument-hint: "<settings.json-path>"
user-invocable: true
---

# /cpv-validate-settings-marketplace

Validates the `extraKnownMarketplaces` block inside a Claude Code `settings.json` file against the v2.1.80+ spec.

This is DIFFERENT from `/cpv-validate-github-marketplace` and `cpv-validate-plugin` — those validate `marketplace.json` files. This command validates the marketplace-source declarations that live in `settings.json` (user, project, or managed level).

## Schema

```json
{
  "extraKnownMarketplaces": {
    "<marketplace-name>": {
      "source": {
        "source": "github" | "url" | "git-subdir" | "npm" | "settings",
        // per-type required fields
      }
    }
  }
}
```

Supported source types:
- `github`: requires `repo: "owner/name"`
- `url`: requires `url`
- `git-subdir`: requires `url` and `path`
- `npm`: requires `package`
- `settings`: inline marketplace, requires `name` and `plugins` (array)

## Usage

```
/cpv-validate-settings-marketplace path/to/settings.json
```

## Execution

```bash
uv run python scripts/validate_settings_marketplace.py "$ARG" --strict
```

## Related

- `/cpv-validate-plugin` — validates plugin.json (different schema)
- `/cpv-validate-github-marketplace` — validates marketplace.json (different schema)
