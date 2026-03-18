---
description: Install a Claude Code plugin from a local directory, archive, or remote marketplace
---

Install a plugin using the CPV management CLI.

**For local installs**, provide a source (directory or archive) and a marketplace name:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" <source> <marketplace>
```

**For remote installs** from a registered marketplace:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" install <plugin>@<marketplace> [--scope user|project|local]
```

**For updates**, use `--update`:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --update <source> <marketplace>
```

Parse the user's request to determine:
1. Whether this is a local install, remote install, or update
2. The source path or plugin name
3. The marketplace name (ask if not provided)
4. Any flags (--force, --dry-run, --quiet, --scope)

Run the appropriate command and report the result. After successful install, remind the user to run `/reload-plugins`.

If the command fails, show the error and suggest: check the source path exists, ensure `.claude-plugin/plugin.json` is present, or try `/cpv-validate-plugin` first.
