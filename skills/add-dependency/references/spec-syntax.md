# Add-Dependency — Spec Syntax and Examples

## Table of Contents

- [add spec syntax](#add-spec-syntax)
- [Full examples](#full-examples)

## add spec syntax

| Form | Result |
|------|--------|
| `name` | bare-string `"name"` (WARN: auto-tracks latest) |
| `name@marketplace` | `{"name": "name", "marketplace": "marketplace"}` |
| `name@marketplace@version` | full pin: `{"name", "marketplace", "version": "version"}` |
| `name@@version` | `{"name", "version"}` (no marketplace override) |

Names must be kebab-case (`[a-z][a-z0-9-]*`). Versions accept any semver-range expression supported by Node's semver package (`~1.2.0`, `^2.0`, `>=1.4`, `=2.1.0`).

## Full examples

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
```
