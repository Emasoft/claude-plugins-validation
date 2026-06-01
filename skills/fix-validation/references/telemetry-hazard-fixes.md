# Telemetry / Plugin-Shipped Env-Var Hazard Fixes (validate_telemetry.py — Phase 13, v2.29.0+)

## Table of Contents

- [Overview](#overview)
- [CRITICAL: Plugin ships CLAUDE_CODE_PLUGIN_SEED_DIR](#critical-plugin-ships-claude_code_plugin_seed_dir)
- [CRITICAL: Plugin ships CLAUDE_CODE_SHELL_PREFIX](#critical-plugin-ships-claude_code_shell_prefix)
- [CRITICAL: Plugin ships CLAUDE_CONFIG_DIR](#critical-plugin-ships-claude_config_dir)
- [CRITICAL: Plugin ships BETA_TRACING_ENDPOINT pointing at external host](#critical-plugin-ships-beta_tracing_endpoint-pointing-at-external-host)
- [CRITICAL: Plugin ships OTEL_LOG_RAW_API_BODIES set to a file URL](#critical-plugin-ships-otel_log_raw_api_bodies-set-to-a-file-url)
- [MAJOR: Plugin ships third-party-provider bypass env var](#major-plugin-ships-third-party-provider-bypass-env-var)
- [Reference: env vars plugins MUST NEVER ship](#reference-env-vars-plugins-must-never-ship)

## Checklist

- [ ] Identify the env var triggering the hazard rule (the report names it)
- [ ] Locate which plugin file ships it (`plugin.json::env`, `hooks/hooks.json::*.env`, MCP server `env`, etc.)
- [ ] Apply the fix from the matching section
- [ ] Re-run `validate_telemetry.py --strict` to confirm

## Overview

Phase 13 (v2.29.0) added 7 rules detecting plugin-shipped env vars that fundamentally bypass user / org / Anthropic-side controls. These are CRITICAL or MAJOR because the user installing the plugin almost certainly does not realize they are being opted into:
- Custom plugin source roots (silent installs)
- Shell prefix injection (RCE-class)
- Custom config dirs (auth-token leakage)
- External telemetry exfiltration
- Raw API body logging to disk
- Bypass of Anthropic API for third-party providers

The fix in every case is the same: **the plugin must not ship the env var**. Move it to user docs (README) and let the user opt in via their own env, settings, or CI.

## CRITICAL: Plugin ships CLAUDE_CODE_PLUGIN_SEED_DIR

| Field | Value |
|---|---|
| **Severity** | CRITICAL |
| **Hazard** | Bypasses Claude Code's marketplace-install path; lets the plugin install other plugins from a directory under its own control |

### Fix

1. Remove `CLAUDE_CODE_PLUGIN_SEED_DIR` from every `env` block in the plugin (plugin.json, hooks, MCP servers, settings.json files).
2. If the plugin genuinely needs to seed companion plugins, document it in README and instruct the user to set the env var in THEIR shell or `.env` before launching Claude Code.
3. Companion plugins should be declared as `dependencies` in `plugin.json` so the marketplace-install pipeline handles them.

## CRITICAL: Plugin ships CLAUDE_CODE_SHELL_PREFIX

| Field | Value |
|---|---|
| **Severity** | CRITICAL |
| **Hazard** | Prepends arbitrary shell text to every Bash tool invocation — silent persistence, full RCE surface |

### Fix

1. Remove `CLAUDE_CODE_SHELL_PREFIX` from every `env` block.
2. If the plugin needs every Bash command to load a venv or set PATH, do it via:
   - A `Setup` hook that writes a `.envrc` (direnv) the user opts in to
   - Documentation in README pointing the user at the canonical setup command
   - A `cwd` field on individual MCP server commands (per-server, not global)

## CRITICAL: Plugin ships CLAUDE_CONFIG_DIR

| Field | Value |
|---|---|
| **Severity** | CRITICAL |
| **Hazard** | Redirects Claude Code to read config / write OAuth tokens / write session data to a directory the plugin chose. Pure auth-credential exfiltration vector |

### Fix

1. Remove `CLAUDE_CONFIG_DIR` from every `env` block. Without exception. There is no legitimate reason for a plugin to redirect the user's config directory.
2. If you needed plugin-private storage, use `${CLAUDE_PLUGIN_DATA}` (the per-plugin data dir that survives updates).

## CRITICAL: Plugin ships BETA_TRACING_ENDPOINT pointing at external host

| Field | Value |
|---|---|
| **Severity** | CRITICAL |
| **Hazard** | Sends Anthropic's beta-tracing telemetry (which can include prompts, tool inputs, model responses) to a non-Anthropic URL the plugin chose |

### Fix

1. Remove `BETA_TRACING_ENDPOINT` from every plugin-shipped `env` block.
2. If the plugin really needs telemetry, ship its own OTel SDK with its own endpoint; do not piggyback on Claude Code's beta tracing.
3. Severity depends on the host. An **external** (non-loopback) URL is CRITICAL (exfiltration). A **localhost** endpoint (`http://localhost:*`, `http://127.0.0.1:*`, or any loopback host) is NOT exempt — it is still flagged as a **MAJOR** ("Detailed-beta tracing belongs in managed-settings.json, not plugin env"), because shipping the var at all from a plugin is wrong. A non-string / placeholder / empty value falls through to the generic "plugin ships an OTEL var" MINOR. In every case, do not ship the var — move it to the user's managed-settings.json.

## CRITICAL: Plugin ships OTEL_LOG_RAW_API_BODIES set to a file URL

| Field | Value |
|---|---|
| **Severity** | CRITICAL |
| **Hazard** | Writes the raw API request and response bodies (containing every prompt, every tool input, every model output) to a file. Any value of the form `file:*` is CRITICAL when plugin-shipped |

### Fix

1. Remove `OTEL_LOG_RAW_API_BODIES` from every plugin-shipped `env` block.
2. If you need to debug API traffic, document `export OTEL_LOG_RAW_API_BODIES=file:/tmp/anthropic.log` in README as a USER opt-in. Never enable it via the plugin itself.

## MAJOR: Plugin ships third-party-provider bypass env var

| Field | Value |
|---|---|
| **Severity** | MAJOR |
| **Triggering vars** | `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY`, `CLAUDE_CODE_USE_MANTLE` |
| **Hazard** | Forces every API call from this Claude Code session to go through the named third-party provider instead of Anthropic. The user did not consent to this rerouting |

### Fix

1. Remove the bypass env var from every plugin-shipped `env` block.
2. Document the provider preference in README and let the user export the env var themselves if they want to use that provider.
3. The plugin must work against the user's preferred provider — do not assume a specific one.

## Reference: env vars plugins MUST NEVER ship

| Env var | Severity if shipped | Why |
|---|---|---|
| `CLAUDE_CODE_PLUGIN_SEED_DIR` | CRITICAL | Silent install of companion plugins |
| `CLAUDE_CODE_SHELL_PREFIX` | CRITICAL | Shell-RCE-class injection |
| `CLAUDE_CONFIG_DIR` | CRITICAL | Auth/OAuth token redirection |
| `BETA_TRACING_ENDPOINT` (external) | CRITICAL | Telemetry exfiltration |
| `OTEL_LOG_RAW_API_BODIES=file:*` | CRITICAL | Full prompt/response logging to disk |
| `CLAUDE_CODE_USE_BEDROCK` | MAJOR | Forces Bedrock without user consent |
| `CLAUDE_CODE_USE_VERTEX` | MAJOR | Forces Vertex without user consent |
| `CLAUDE_CODE_USE_FOUNDRY` | MAJOR | Forces Foundry without user consent |
| `CLAUDE_CODE_USE_MANTLE` | MAJOR | Forces Bedrock-Mantle without user consent |

For all of these: the plugin's `env` block is the wrong place. Move them to README documentation and let the user choose.
