---
trdd-id: 8ccb9337
title: TRDD-8ccb9337-e6e5-4371-8c4c-5cf64f4d5eb9 — IDE-Config Secret Scan
column: complete
updated: 2026-08-25T17:25:27+0200
---

# TRDD-8ccb9337-e6e5-4371-8c4c-5cf64f4d5eb9 — IDE-Config Secret Scan

**TRDD ID:** `8ccb9337-e6e5-4371-8c4c-5cf64f4d5eb9`
**Filename:** `design/tasks/TRDD-8ccb9337-e6e5-4371-8c4c-5cf64f4d5eb9-ide-config-secret-scan.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Done
**Priority:** LOW
**Effort:** SMALL
**Source:** `docs_dev/cpv_workflow_audit_20260412.md` section B6 / C11

## Resolution (TRDD-8ccb9337 implementation, 2026-05-10)

The CRITICAL-severity portion of this TRDD was already shipped in
`scripts/validate_security.py::scan_ide_config_files` (and
`IDE_CONFIG_PATHS` constant, plus the `TestScanIdeConfigFiles` test class
in `tests/test_validate_security.py`). The implementation already covers:

  - All 9 IDE config paths from the spec (.vscode/.idea/.cursor/.zed)
  - Uses `get_gitignore_filter` to skip gitignored files
  - Uses the existing `SECRET_PATTERNS` regex suite via `scan_for_secrets`
  - Flags REAL secret values at CRITICAL severity
  - Dedupe between literal entries and globs (e.g. `.idea/workspace.xml`
    vs `.idea/*.xml`)

The remaining "Additional: warn on .env in IDE configs" feature — NIT
warnings on env-var REFERENCES with secret-like names (`API_KEY`,
`TOKEN`, etc.) — is shipped as a sibling validator in
`scripts/validate_ide_config.py`. The two scanners are intentionally
separate so the high-cost regex suite in `scan_for_secrets` stays put
and the low-cost env-name predicate runs in its own module.

Sibling validator emits NIT findings on:

  1. Env-var references (`${VAR}`, `${env:VAR}`, `$VAR`, `%VAR%`,
     `$VAR$`) where the variable name matches `SECRET_LIKE_ENV_NAME`
     (API_KEY, TOKEN, SECRET, PASSWORD, CREDENTIALS, BEARER, etc.).
  2. References to a `.env` file (any form: `.env`, `.env.local`,
     `path/to/.env`, …).

Both scanners use the same `IDE_CONFIG_PATHS` constant and the same
gitignore filter, so coverage is symmetric.

CLI entry point: `cpv-validate-ide-config <plugin-path> [--strict] [--report PATH]`.
NITs do NOT block by default; pass `--strict` to make them block.

Test coverage: `tests/test_validate_ide_config.py` (37 tests across 5
classes — TestSpec, TestIsSecretLikeEnvName, TestScanSingleFile,
TestPluginOrchestration, TestCLI). Existing
`TestScanIdeConfigFiles` in test_validate_security.py keeps the
CRITICAL-severity coverage.

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

## Approval log

- 2026-08-25T17:25:27+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED — scan_ide_config_files + validate_ide_config.py live, commit a362798e (batch_ai)
