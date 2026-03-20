---
name: cpv-doctor
description: Health-check all installed Claude Code plugins, settings, and marketplaces
agent: plugin-manager
user-invocable: true
---

Run the plugin health check:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py"
```

For full validation details and security scanning, add `--verbose`:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --verbose
```

The doctor checks: Claude CLI authentication, settings integrity, marketplace registration, `claude plugin validate` on each marketplace, plugin validation, and orphaned entries. Report results clearly, highlighting issues and suggesting fixes.
