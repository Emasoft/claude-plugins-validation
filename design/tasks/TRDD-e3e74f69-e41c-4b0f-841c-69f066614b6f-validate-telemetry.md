# TRDD-e3e74f69 — Standalone `validate_telemetry.py` OTEL supply-chain validator

**TRDD ID:** `e3e74f69-e41c-4b0f-841c-69f066614b6f`
**Filename:** `design/tasks/TRDD-e3e74f69-e41c-4b0f-841c-69f066614b6f-validate-telemetry.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Not started
**Deferred from:** TRDD-479cde0c §v2.22.1 "NEXT-RELEASE"
**Parent audit report:** `docs_dev/spec-audit-5-new-features-20260417-163011.md` §V2

## Problem

monitoring-usage.md introduces ~25 OTEL environment variables and one new
settings.json key (`otelHeadersHelper`) that together form a substantial
supply-chain attack surface for plugin-shipped settings:

1. **`otelHeadersHelper`** — path to a script Claude Code runs every 29
   minutes (default) to refresh OTEL export auth headers. A plugin that
   ships this setting can execute arbitrary code on the user's machine
   periodically. Should be admin-managed only.
2. **`OTEL_LOG_USER_PROMPTS=1`** — exfiltrates every user prompt to the
   configured OTEL endpoint. If a plugin ships this in its `env` block,
   the user's conversation leaks by default.
3. **`OTEL_LOG_RAW_API_BODIES=1`** — "enabling this implies consent to
   everything `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS`, and
   `OTEL_LOG_TOOL_CONTENT` would reveal." Massive data-leak.
4. **`OTEL_EXPORTER_OTLP_ENDPOINT`** pointed at an attacker-controlled
   URL silently exfiltrates telemetry.

## What CPV needs to do

Add a new standalone validator `scripts/validate_telemetry.py` that runs
per plugin package and per settings file:

### Rules

- **CRITICAL** when `otelHeadersHelper` appears in a plugin-shipped
  `settings.json` — this is the supply-chain vector. The user's admin
  can set it via managed-settings, but a plugin cannot.
- **CRITICAL** when a plugin's `env` block sets `OTEL_LOG_RAW_API_BODIES=1`.
- **MAJOR** when a plugin's `env` block sets `OTEL_LOG_USER_PROMPTS=1`
  or `OTEL_LOG_TOOL_DETAILS=1` or `OTEL_LOG_TOOL_CONTENT=1`.
- **MAJOR** when a plugin sets `OTEL_EXPORTER_OTLP_ENDPOINT` to a non-HTTPS
  URL, OR to a URL containing an IP literal (suggests dev-server exfil).
- **MINOR** when a plugin sets any `OTEL_*` variable at all — plugin
  authors shouldn't configure the user's telemetry; that's the admin's
  job. Suggest moving the declaration to documentation (README) instead
  of shipping it.
- **INFO** when `OTEL_RESOURCE_ATTRIBUTES` has malformed syntax (spaces,
  quotes, commas in values silently break the whole attribute string).

### Wire points

- Invoked by `validate_plugin.py` for every plugin (as a sub-validator).
- Invoked by `validate_local_scope.py` + `validate_project_scope.py` for
  settings.json `env` blocks.
- Slash command: `/cpv-validate-telemetry <plugin_path>` as a direct-script
  command for standalone runs.
- Aliases in `remote_validation.py`: `telemetry`, `otel`.

### New files

- `scripts/validate_telemetry.py` — the standalone validator.
- `commands/cpv-validate-telemetry.md` — slash command.
- `tests/test_validate_telemetry.py` — regression suite.

### Known fields to flag (the complete list from monitoring-usage.md)

Privacy-sensitive content flags:
- `OTEL_LOG_USER_PROMPTS`
- `OTEL_LOG_TOOL_DETAILS`
- `OTEL_LOG_TOOL_CONTENT`
- `OTEL_LOG_RAW_API_BODIES`

Endpoint pointers (flag non-HTTPS):
- `OTEL_EXPORTER_OTLP_ENDPOINT`
- `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`
- `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`

Supply-chain script helper (settings.json, not env):
- `otelHeadersHelper`

Cardinality and miscellaneous (INFO if plugin ships them):
- `OTEL_METRICS_INCLUDE_SESSION_ID`
- `OTEL_METRICS_INCLUDE_VERSION`
- `OTEL_METRICS_INCLUDE_ACCOUNT_UUID`
- `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`
- `OTEL_RESOURCE_ATTRIBUTES`
- `OTEL_METRICS_EXPORTER`
- `OTEL_LOGS_EXPORTER`
- `OTEL_TRACES_EXPORTER`
- `OTEL_EXPORTER_OTLP_PROTOCOL` (+ per-signal variants)
- `OTEL_EXPORTER_OTLP_HEADERS`
- `OTEL_METRIC_EXPORT_INTERVAL`
- `OTEL_LOGS_EXPORT_INTERVAL`
- `OTEL_TRACES_EXPORT_INTERVAL`
- `OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY` (mTLS)
- `OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE` (mTLS)

Core CC-specific:
- `CLAUDE_CODE_ENABLE_TELEMETRY`
- `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`
- `CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS`

## Tests

Full matrix of env-block combinations. At minimum:

- `test_plugin_shipping_otel_log_raw_api_bodies_critical`
- `test_plugin_shipping_otel_log_user_prompts_major`
- `test_plugin_shipping_otel_headers_helper_in_settings_critical`
- `test_plugin_shipping_non_https_otel_endpoint_major`
- `test_plugin_shipping_ip_literal_otel_endpoint_major`
- `test_plugin_shipping_benign_otel_disable_ok` (user legitimately sets
  `CLAUDE_CODE_ENABLE_TELEMETRY=0` — should not fire)
- `test_plugin_shipping_any_otel_var_minor` (generic suggestion to move
  to documentation)
- `test_otel_resource_attributes_malformed_info` (comma inside a value)

## Success criteria

- New validator runs as `cpv-validate-telemetry <path>` and exits with
  CPV's standard severity-coded exit codes.
- `validate_plugin.py --all` includes telemetry in its sub-validator
  dispatch.
- README + `plugin-error-index.md` document the new finding codes.
- All matrix tests pass.
