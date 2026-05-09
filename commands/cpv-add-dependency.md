---
name: cpv-add-dependency
description: Add plugin dependencies to a target plugin (explicit `--add` specs or `--from` copy from another plugin's plugin.json) per plugin-dependencies.md
allowed-tools: Bash(uv:*)
user-invocable: true
---

# /cpv-add-dependency

Add one or more plugin dependencies to a target plugin's `plugin.json::dependencies` array. Two input modes that can be combined; the engine deduplicates by name (last-write-wins), sorts the result alphabetically, writes atomically, and rolls back from a `.bak` if the post-write validation introduces any new CRITICAL/MAJOR finding.

Spec reference: [plugin-dependencies.md](https://code.claude.com/docs/en/plugin-dependencies.md).

## Usage

```bash
# Explicit single dep, version-pinned
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_dependencies.py" /path/to/my-plugin \
  --add dev-browser@@~1.2.0

# Cross-marketplace pin
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_dependencies.py" /path/to/my-plugin \
  --add audit-logger@acme-shared@^2.0

# Copy ALL deps from another local plugin
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_dependencies.py" /path/to/my-plugin \
  --from /path/to/other-plugin

# Copy from a git URL (shallow clone to tmp)
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_dependencies.py" /path/to/my-plugin \
  --from https://github.com/Emasoft/dev-browser-plugin

# Combine: copy from another plugin + add an extra
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_dependencies.py" /path/to/my-plugin \
  --from /path/to/template-plugin \
  --add custom-skill@my-marketplace

# Always preview first with --dry-run
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_dependencies.py" /path/to/my-plugin \
  --add dev-browser \
  --dry-run
```

## --add spec syntax

| Form                              | Result                                                      |
|-----------------------------------|-------------------------------------------------------------|
| `name`                            | bare-string `"name"` (WARN: auto-tracks latest)             |
| `name@marketplace`                | `{"name": "name", "marketplace": "marketplace"}`            |
| `name@marketplace@version`        | full pin: `{"name", "marketplace", "version": "version"}`   |
| `name@@version`                   | `{"name", "version"}` (no marketplace override)             |

Names must be kebab-case (`[a-z][a-z0-9-]*`). Versions accept any semver-range expression supported by Node's semver package (`~1.2.0`, `^2.0`, `>=1.4`, `=2.1.0`).

## Behavior

- **Idempotent**: re-runs with the same args produce no diff (same name → same dedup key → same merge result; sorted output is stable).
- **Atomic write**: tmp-and-rename so a crash mid-write never leaves a partial `plugin.json`.
- **Rollback on regression**: the engine re-runs `validate_plugin --strict` after the write. If any new CRITICAL/MAJOR finding appears, the `.bak` is restored and the script exits 3.
- **WARN on unversioned bare-string adds**: per plugin-dependencies.md:9-11, an unversioned dep auto-tracks the latest tag — the next upstream release can break the consumer without warning. The validator emits `WARNING [RC-DEP-VERSION-001]`. Suppress intentional cases with `cpv.allow_unversioned_dependencies: true` in plugin.json.
- **Cross-marketplace allowlist**: when an `--add` spec uses a marketplace OTHER than the hosting one, the root marketplace's `marketplace.json::allowCrossMarketplaceDependenciesOn` MUST list that marketplace name. Otherwise `validate_plugin --strict` emits MAJOR `[plugin-dependencies.md cross-marketplace blocked]` and the engine rolls back.

## Exit codes

| Code | Meaning                                                                                |
|------|----------------------------------------------------------------------------------------|
| 0    | OK — `dependencies` array updated                                                      |
| 1    | invalid args / target not a plugin / target malformed                                  |
| 2    | `--from` source unreadable (path missing OR git clone failed)                          |
| 3    | merge introduced new CRITICAL/MAJOR — rolled back from .bak                            |
| 4    | atomic write failed — target untouched                                                 |

## Where this lives in the menus

- **Main menu** § 3.4 Create → row 9 "Add dependencies (existing plugin)"
- **Doctor menu** option 22 "Add a dependency to a plugin (explicit URL/path OR copy from another plugin)"
- **Direct slash command**: `/cpv-add-dependency` (this command)

## See also

- [plugin-dependencies.md](https://code.claude.com/docs/en/plugin-dependencies.md) — official spec
- `/cpv-doctor` option 21 — show the dependency tree + runtime errors before adding
- `/cpv-validate-plugin` — run `validate_plugin --strict` on the target after adding
