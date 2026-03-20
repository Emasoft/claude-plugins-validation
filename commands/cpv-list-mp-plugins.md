---
name: cpv-list-mp-plugins
description: List all plugins available from a marketplace with version and enabled status
argument-hint: "<marketplace-name|owner/marketplace-name>"
user-invocable: true
---

List all plugins registered in a marketplace, showing each plugin's name, version, and whether it's enabled at user level and/or project-local level.

## Usage

```bash
# By marketplace name
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --marketplace <marketplace-name>

# By owner/marketplace name
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --marketplace <owner>/<marketplace-name>
```

## Examples

```
/cpv-list-mp-plugins emasoft-plugins
/cpv-list-mp-plugins Emasoft/emasoft-plugins
```

## Output

```
Marketplace: Emasoft/emasoft-plugins  v1.0.0
Plugins: 15

  Plugin                                   Version    User       Local
  ──────────────────────────────────────── ────────── ────────── ──────────
  claude-plugins-validation                2.2.0      enabled    --
  perfect-skill-suggester                  2.7.2      enabled    disabled
  token-reporter                           1.2.2      disabled   enabled
```

| Column | Meaning |
|--------|---------|
| Plugin | Plugin name as registered in marketplace.json |
| Version | Version from marketplace.json (may differ from installed) |
| User | Status in `~/.claude/settings.json`: enabled / disabled / -- (not set) |
| Local | Status in project `.claude/settings.local.json`: enabled / disabled / -- (not set) |

## Error Handling

| Error | Resolution |
|-------|------------|
| Marketplace not found | Check the name; run `/cpv-manage-marketplaces list` to see registered marketplaces |
| Cannot read marketplace.json | The marketplace may be corrupted; try `claude plugin marketplace update` |
