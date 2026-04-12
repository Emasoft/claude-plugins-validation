# TRDD-8ccb9337-e6e5-4371-8c4c-5cf64f4d5eb9 — IDE-Config Secret Scan

**TRDD ID:** `8ccb9337-e6e5-4371-8c4c-5cf64f4d5eb9`
**Filename:** `design/tasks/TRDD-8ccb9337-e6e5-4371-8c4c-5cf64f4d5eb9-ide-config-secret-scan.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Not started
**Priority:** LOW
**Effort:** SMALL
**Source:** `docs_dev/cpv_workflow_audit_20260412.md` section B6 / C11

## Problem

`scripts/validate_security.py` does not scan IDE configuration files for
secrets. Common leak vectors:

- `.vscode/settings.json` — API tokens in `"terminal.integrated.env.*"`
- `.vscode/tasks.json` / `.vscode/launch.json` — tokens in task env vars
- `.idea/workspace.xml` and other `.idea/*.xml` — JetBrains IDE project secrets
- `.cursor/mcp.json` / `.cursor/settings.json` — Cursor LLM API keys
- `.zed/settings.json` / `.zed/tasks.json` — Zed LLM API keys

These files are often `.gitignore`'d but not always — and a plugin
directory may contain them inadvertently.

Currently `validate_security.py::scan_for_secrets` scans .py / .sh /
.json / .yaml / .md files and AI-facing markdown, but NOT the IDE-config
JSON/XML files listed above.

## Scope

Extend `validate_security.py::scan_for_secrets` (and any file-walk helper
it uses) to include:

```python
IDE_CONFIG_PATHS = [
    ".vscode/settings.json",
    ".vscode/tasks.json",
    ".vscode/launch.json",
    ".idea/workspace.xml",
    ".idea/*.xml",
    ".cursor/mcp.json",
    ".cursor/settings.json",
    ".zed/settings.json",
    ".zed/tasks.json",
]
```

Walk each match, run the existing secret regexes on the content, flag as
CRITICAL (or MAJOR — match current severity for `.env` files).

### Important: respect gitignore

If an IDE config file is in `.gitignore`, skip it (secrets in gitignored
files are not shipped). Use `git check-ignore` or a gitignore parser.

### Additional: warn on .env in IDE configs

Check if any IDE config contains references to `.env` or `$SECRET_NAME`
patterns. These are usually safe (env var references) but emit NIT if the
env var name matches a known secret prefix like `API_KEY`, `TOKEN`, etc.

## Success criteria

- [ ] Fixture with `.vscode/settings.json` containing `"OPENAI_API_KEY": "sk-..."`
      triggers CRITICAL
- [ ] Fixture with `.cursor/mcp.json` containing a real-looking API key triggers CRITICAL
- [ ] Gitignored IDE configs are skipped
- [ ] Clean fixture with empty IDE configs passes

## Out of scope

- Fixing the secrets (CPV is read-only for the plugin)
- Editor-specific features unrelated to secrets
- XML-specific secret patterns (cover with existing regex suite)
