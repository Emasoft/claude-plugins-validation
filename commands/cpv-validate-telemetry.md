---
name: cpv-validate-telemetry
description: Audit a plugin or settings.json for OTEL telemetry supply-chain risks (otelHeadersHelper, OTEL_LOG_*, endpoint hijack)
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion
argument-hint: "<plugin_or_settings_path> [--settings] [--managed] [--verbose] [--report PATH]"
user-invocable: true
---

# /cpv-validate-telemetry Command

Runs the **telemetry supply-chain validator** (`validate_telemetry.py`) against
a Claude Code plugin OR a `settings.json` file. The validator catches the
OTEL-shaped attack surface introduced by the `monitoring-usage.md` spec —
roughly 25 environment variables and one new settings.json key
(`otelHeadersHelper`) that a malicious plugin can use to exfiltrate user
prompts, redirect telemetry to an attacker-controlled endpoint, or get
periodic arbitrary code execution on the user's machine.

| Rule | Severity | What it catches |
|---|---|---|
| **TEL-01** | CRITICAL | `otelHeadersHelper` key in plugin-shipped `settings.json` (Claude Code runs it every ~29 min — admin-managed only) |
| **TEL-02** | CRITICAL | `OTEL_LOG_RAW_API_BODIES=1` in plugin env (per spec, implies consent to ALL `OTEL_LOG_*` exfil flags) |
| **TEL-03** | MAJOR    | `OTEL_LOG_USER_PROMPTS=1` / `OTEL_LOG_TOOL_DETAILS=1` / `OTEL_LOG_TOOL_CONTENT=1` in plugin env |
| **TEL-04** | MAJOR    | `OTEL_EXPORTER_OTLP_*ENDPOINT` pointed at a non-loopback URL (silent exfiltration target) |
| **TEL-05** | MINOR    | Any other `OTEL_*` variable shipped from plugin env (telemetry config belongs in admin's `managed-settings.json`) |
| **TEL-06** | INFO/MINOR | `OTEL_RESOURCE_ATTRIBUTES` malformed syntax (commas/quotes inside values silently break the whole attribute string) |

These rules sit on top of the project's general `validate_security.py` Phase
13 hazard checks (`CLAUDE_CODE_PLUGIN_SEED_DIR`, `CLAUDE_CODE_SHELL_PREFIX`,
`CLAUDE_CONFIG_DIR`, third-party-provider bypass) — those CRITICAL/MAJOR
findings also fire here when a plugin ships them in an env block.

## Usage

```
/cpv-validate-telemetry <plugin_or_settings_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin_or_settings_path` | Yes | Path to a plugin directory (`.claude-plugin/plugin.json` must exist), or a `settings.json` file when `--settings` is passed |

## Options

| Option | Description |
|---|---|
| `--settings` | Treat target as a `settings.json` file instead of a plugin directory |
| `--managed` | Mark the file as admin-managed (skips the `otelHeadersHelper` CRITICAL — the helper is allowed in managed settings) |
| `--verbose` | Show all checks including PASSED |
| `--report PATH` | Write the full aggregated report to PATH explicitly |
| `--json` | Output as JSON (skips the auto-saved report file) |

> **Default output is path-only.** Without `--json` or `--report`, the
> script auto-saves to `${CLAUDE_PROJECT_DIR}/reports/telemetry/<timestamp>-<slug>.md`
> and prints **only** the compact summary (counts table + verdict + plugin
> path + report path). Token-bounded so the calling agent never gets flooded.

## Workflow

### Canonical invocation (always via the remote-validation launcher)

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MAIN_ROOT="$(git worktree list | head -n1 | awk '{print $1}')"
else
  MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
REPORT_DIR="$MAIN_ROOT/reports/validate_telemetry"
mkdir -p "$REPORT_DIR"
REPORT_FILE="$REPORT_DIR/$(date +%Y%m%d_%H%M%S%z)-$(basename "$TARGET_PATH").md"

CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  telemetry "$TARGET_PATH" $OPTIONS --report "$REPORT_FILE"
```

> The `telemetry` alias forwards to `validate_telemetry.py` via the launcher,
> which sets up environment isolation. Do NOT call `validate_telemetry.py`
> directly from `~/.claude/plugins/cache/...` — the script will refuse with
> a "remote location" error. `${CLAUDE_PLUGIN_ROOT}` is set automatically and
> points at the locally-installed CPV plugin.

The validator walks the plugin tree (or reads the single settings file),
applies TEL-01..TEL-06 in order, and writes a per-rule aggregated report.
The summary printed to stdout includes per-severity counts, the verdict, the
plugin path, and the report path.

## Privacy Check (REQUIRED)

The one-liner above already injects `CLAUDE_PRIVATE_USERNAMES="$(whoami)"`.
If `$(whoami)` is unreliable in your shell, use `AskUserQuestion` to ask the
user for their system username and substitute the result into the env var.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | No telemetry supply-chain risks found |
| `1` | CRITICAL finding (TEL-01 / TEL-02 / Phase-13 hazard env var) — block publish |
| `2` | MAJOR finding (TEL-03 / TEL-04) — fix before publish |
| `3` | MINOR finding (TEL-05) — recommended to fix (move to README) |

## Examples

```
# Standard plugin scan — auto-saves report, prints compact summary
/cpv-validate-telemetry ./my-plugin/

# Scan a single settings.json file (project-scope or local-scope)
/cpv-validate-telemetry ./my-project/.claude/settings.json --settings

# A managed-settings.json with otelHeadersHelper is legitimate
/cpv-validate-telemetry /etc/claude-code/managed-settings.json --settings --managed

# Save to a specific path for CI artifacts
/cpv-validate-telemetry ./my-plugin/ --report /tmp/telemetry-audit.md

# Verbose output (includes PASSED checks)
/cpv-validate-telemetry ./my-plugin/ --verbose
```

## Related

- `/cpv-validate-plugin` — Full plugin validation (structure, security,
  skills, etc.). Runs telemetry as a sub-validator since v2.80.0.
- `/cpv-validate-local-scope` / `/cpv-validate-project-scope` — Scope-aware
  validation for `settings.json` files; both invoke this validator on the
  `env` blocks they encounter.
- `scripts/validate_security.py` — Phase 13 hazard env-var rules
  (`CLAUDE_CODE_*`, third-party providers). Telemetry validator covers OTEL
  surface; security validator covers the broader plugin-shipped env risks.

## Reference

The TEL-01..TEL-06 rule pack is derived from Anthropic's
`monitoring-usage.md` documentation (~25 OTEL env vars + `otelHeadersHelper`
settings key). See `design/tasks/TRDD-e3e74f69-*.md` for the full design.
