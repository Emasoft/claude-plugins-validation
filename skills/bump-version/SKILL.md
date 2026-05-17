---
name: bump-version
description: Bump plugin version + run the full publish pipeline (plugin.json, pyproject.toml, README badge, CHANGELOG, push, release)
when_to_use: When the cpv-main-menu user picks Manage → Bump version, or any flow needs to bump version + publish the current plugin via publish.py
user-invocable: false
allowed-tools: Bash(uv:*)
---

# bump-version

Bump version AND run the full publish pipeline (TRDD-bbff5bc5 — single
entry point):

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --patch   # 1.2.0 -> 1.2.1
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --minor   # 1.2.0 -> 1.3.0
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --major   # 1.2.0 -> 2.0.0
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --dry-run --patch  # preview only
```

`publish.py` is the canonical entry point — it bumps the version in
plugin.json + pyproject.toml + `__version__` vars, refreshes the README
badge, regenerates the CHANGELOG, refreshes `.plugin-self-hashes.json`
(if present), commits + tags + pushes, then creates the GitHub release.
Every gate must pass before any push (no `--skip-*` flags exist).

For version-only bumps without push (rare — e.g. CI smoke tests):

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --patch   # local-only
```

`bump_version.py` is now a thin wrapper around `publish.bump_semver` and
exists only for callers that need the version number without triggering
the full pipeline. New code should call `publish.py` directly.

See the **plugin-management** skill for version bump details.
