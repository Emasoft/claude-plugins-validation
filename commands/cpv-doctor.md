---
name: cpv-doctor
description: Health-check all installed Claude Code plugins, settings, and marketplaces (--fix to auto-repair)
user-invocable: true
---

Run the plugin health check:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py"
```

For full validation details: `--verbose`. To auto-fix orphaned entries: `--fix`.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --verbose --fix
```

Checks: CLI auth, settings integrity, marketplace registrations, plugin validation, orphaned entries, stale `settings.local.json` entries.

`--fix` auto-removes: orphaned marketplace registrations pointing to missing directories, orphaned `enabledPlugins` entries for missing plugins/marketplaces.

See the **plugin-management** skill for full details.
