#!/usr/bin/env python3
"""
Claude Plugins Validation - OTEL Telemetry Supply-Chain Validator

Scans plugin packages and settings.json files for the OTEL telemetry
supply-chain attack surface introduced by monitoring-usage.md.

The validator catches two families of risk:

1. ``otelHeadersHelper`` in plugin-shipped ``settings.json`` — a key that
   points to an executable that Claude Code runs every ~29 minutes to
   refresh OTEL export auth headers. If a plugin can set it, the plugin
   can execute arbitrary code on the user's machine periodically. This
   is admin-managed (managed-settings.json) only — CRITICAL in plugins.

2. OTEL environment variables shipped in a plugin's ``env`` block
   (plugin.json, hooks.json, settings.json). Specific flags leak the
   user's conversation to the configured endpoint:

   - ``OTEL_LOG_RAW_API_BODIES=1`` — CRITICAL (per monitoring-usage.md,
     "enabling this implies consent to everything OTEL_LOG_USER_PROMPTS,
     OTEL_LOG_TOOL_DETAILS, and OTEL_LOG_TOOL_CONTENT would reveal").
   - ``OTEL_LOG_USER_PROMPTS=1`` — MAJOR (prompt exfiltration).
   - ``OTEL_LOG_TOOL_DETAILS=1`` / ``OTEL_LOG_TOOL_CONTENT=1`` — MAJOR.
   - ``OTEL_EXPORTER_OTLP_ENDPOINT`` set to an external URL — MAJOR.
   - Any other OTEL_* var — MINOR (plugin authors should document in
     README instead of shipping values).

Usage::

    uv run python scripts/validate_telemetry.py path/to/plugin/
    uv run python scripts/validate_telemetry.py path/to/settings.json --settings
    uv run python scripts/validate_telemetry.py path/to/plugin/ --report /tmp/t.md

Exit codes (standard CPV severity-coded):

    0 - No blocking issues
    1 - CRITICAL
    2 - MAJOR
    3 - MINOR
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from cpv_validation_common import (
    EXIT_CRITICAL,
    EXIT_OK,
    ValidationReport,
    check_remote_execution_guard,
    print_results_by_level,
    save_report_and_print_summary,
)

# =============================================================================
# OTEL Constants
# =============================================================================

# Log exfiltration flags — when set to "1" in a plugin-shipped env block,
# user data leaks to the configured OTEL endpoint.
OTEL_LOG_EXFIL_VARS: frozenset[str] = frozenset(
    {
        "OTEL_LOG_USER_PROMPTS",
        "OTEL_LOG_RAW_API_BODIES",
        "OTEL_LOG_TOOL_DETAILS",
        "OTEL_LOG_TOOL_CONTENT",
    }
)

# Endpoint-pointer env vars. A plugin that sets these to an attacker-
# controlled URL silently exfiltrates telemetry.
OTEL_ENDPOINT_VARS: frozenset[str] = frozenset(
    {
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    }
)

# Full OTEL env-var surface per monitoring-usage.md (25 vars total).
# Any variable in this set SHIPPED in a plugin env block earns at least
# a MINOR; the individual categories above may upgrade the severity.
OTEL_ALL_ENV_VARS: frozenset[str] = frozenset(
    {
        # Log exfiltration
        "OTEL_LOG_USER_PROMPTS",
        "OTEL_LOG_RAW_API_BODIES",
        "OTEL_LOG_TOOL_DETAILS",
        "OTEL_LOG_TOOL_CONTENT",
        # Endpoint pointers
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        # Protocol + headers
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY",
        "OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE",
        # Exporter selection
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER",
        "OTEL_TRACES_EXPORTER",
        # Cardinality / inclusion flags
        "OTEL_METRICS_INCLUDE_SESSION_ID",
        "OTEL_METRICS_INCLUDE_VERSION",
        "OTEL_METRICS_INCLUDE_ACCOUNT_UUID",
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
        # Export intervals
        "OTEL_METRIC_EXPORT_INTERVAL",
        "OTEL_LOGS_EXPORT_INTERVAL",
        "OTEL_TRACES_EXPORT_INTERVAL",
        # Resource attributes
        "OTEL_RESOURCE_ATTRIBUTES",
    }
)

# Files checked for env blocks inside a plugin root.
_PLUGIN_ENV_CANDIDATES: tuple[tuple[str, ...], ...] = (
    (".claude-plugin", "plugin.json"),
    ("hooks", "hooks.json"),
    (".claude-plugin", "settings.json"),
    ("settings.json",),
)

# Placeholder patterns — values that look like ${VAR} or {{VAR}} are
# unresolved at packaging time. We treat them as non-concrete values.
_PLACEHOLDER_RE = re.compile(r"\$\{[^}]+\}|\{\{[^}]+\}\}|\$[A-Z_][A-Z0-9_]*")

# Loopback / private network heuristics — not authoritative, just used
# to distinguish "localhost test setup" from "probable exfil target".
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


# =============================================================================
# Helpers
# =============================================================================


def _is_truthy_one(value: Any) -> bool:
    """Return True iff the value literally equals "1", 1, or True.

    OTEL consent semantics per monitoring-usage.md is "set to 1". We do
    NOT treat arbitrary truthy strings like "yes" as opt-in because
    Claude Code itself only reads literal "1".
    """
    if value is True:
        return True
    if isinstance(value, int) and value == 1:
        return True
    if isinstance(value, str) and value.strip() == "1":
        return True
    return False


def _is_placeholder(value: Any) -> bool:
    """Return True if the value contains an unresolved template placeholder."""
    if not isinstance(value, str):
        return False
    return bool(_PLACEHOLDER_RE.search(value))


def _is_external_endpoint(value: Any) -> bool:
    """Return True if the URL clearly points at a non-loopback host.

    Conservative: if the value is not a string, contains a placeholder,
    or is empty, we return False (the caller can emit a MINOR on the
    generic "plugin ships an OTEL var" rule).
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped or _is_placeholder(stripped):
        return False
    # Parse protocol/host cheaply without urllib to avoid schema surprises.
    match = re.match(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?P<host>[^/:?#]+)", stripped)
    if not match:
        return False
    host = match.group("host").lower()
    if host in _LOOPBACK_HOSTS:
        return False
    # Private-network RFC1918 literals are still "external" from the
    # user's perspective (they could be a LAN exfil endpoint) but we
    # treat them as external to be safe.
    return True


def _load_json_file(path: Path) -> dict[str, Any] | None:
    """Load a JSON file, returning None on any read/parse error.

    We never leak the file contents into error messages — only the
    exception type name — because settings files often contain secrets.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _extract_env_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract every ``env`` mapping nested anywhere in the config.

    Claude Code supports ``env`` blocks at multiple points:

    - top-level ``env`` in settings.json / plugin.json
    - inside ``hooks[*]`` and inside hooks.json matcher entries
    - inside ``mcpServers[name].env``

    We return ONLY dict-shaped env blocks — lists or scalars are not
    valid Claude Code env maps and would be caught by other validators.
    """
    found: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            env = node.get("env")
            if isinstance(env, dict):
                found.append(env)
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(data)
    return found


def _is_managed_settings_path(settings_path: Path) -> bool:
    """Return True for well-known admin-managed settings locations.

    Managed-settings.json is written by admins, never by plugins, so
    ``otelHeadersHelper`` there is PASSED. Anywhere else, it is a
    CRITICAL supply-chain finding.
    """
    try:
        resolved = str(settings_path.resolve())
    except OSError:
        resolved = str(settings_path)
    # Common UNIX/Windows/macOS managed paths per managed-settings docs.
    markers = (
        "/etc/claude-code/managed-settings.json",
        "/Library/Application Support/ClaudeCode/managed-settings.json",
        "\\ProgramData\\ClaudeCode\\managed-settings.json",
    )
    return any(resolved.endswith(marker) for marker in markers)


# =============================================================================
# Core scanning functions
# =============================================================================


def _validate_env_block(
    env: dict[str, Any],
    report: ValidationReport,
    source: str,
) -> None:
    """Check a single env block for risky OTEL variables.

    Emits findings on ``report``. ``source`` is the human-readable file
    label used in result messages.
    """
    for name, value in env.items():
        if name not in OTEL_ALL_ENV_VARS:
            continue

        # --- 1. Log-exfil vars -----------------------------------------
        if name in OTEL_LOG_EXFIL_VARS:
            if _is_truthy_one(value):
                if name == "OTEL_LOG_RAW_API_BODIES":
                    report.critical(
                        f"Plugin env sets {name}=1. Per monitoring-usage.md, this "
                        "implies consent to everything OTEL_LOG_USER_PROMPTS, "
                        "OTEL_LOG_TOOL_DETAILS, and OTEL_LOG_TOOL_CONTENT "
                        "would reveal. Move telemetry configuration to the "
                        "user's managed-settings.json.",
                        source,
                    )
                else:
                    report.major(
                        f"Plugin env sets {name}=1. This exfiltrates user "
                        "data to the configured OTEL endpoint. Do not ship "
                        "this in a plugin — let the admin configure it via "
                        "managed-settings.json.",
                        source,
                    )
                continue
            # Shipped but disabled / placeholder — still a MINOR
            # because the plugin is mandating telemetry posture.
            if _is_placeholder(value):
                report.minor(
                    f"Plugin env references {name} via placeholder. "
                    "Telemetry configuration should live in the user's "
                    "managed-settings, not in plugin-shipped env. "
                    "Document the variable in README instead.",
                    source,
                )
            else:
                report.minor(
                    f"Plugin env sets {name} (value: {value!r}). "
                    "Even when disabled, shipping this key pre-commits "
                    "the user to a telemetry posture. Document in "
                    "README instead of setting it.",
                    source,
                )
            continue

        # --- 2. Endpoint pointer vars ----------------------------------
        if name in OTEL_ENDPOINT_VARS:
            if _is_external_endpoint(value):
                report.major(
                    f"Plugin env sets {name} to an external URL "
                    f"({value!r}). Silently redirects telemetry to an "
                    "attacker-controllable host. Only admins should "
                    "configure OTEL endpoints.",
                    source,
                )
            else:
                report.minor(
                    f"Plugin env sets {name}. OTEL endpoints should be "
                    "admin-configured via managed-settings.json, not "
                    "shipped inside a plugin.",
                    source,
                )
            continue

        # --- 3. Any other OTEL_* var -----------------------------------
        report.minor(
            f"Plugin env sets {name}. Plugin authors should document "
            "OTEL variables in README rather than shipping values — "
            "telemetry belongs in managed-settings.json.",
            source,
        )


def scan_settings_for_telemetry(
    settings_path: Path,
    report: ValidationReport | None = None,
    plugin_shipped: bool | None = None,
) -> ValidationReport:
    """Scan a settings.json-shaped file for telemetry supply-chain issues.

    Args:
        settings_path: Absolute or relative path to a settings.json.
        report: Optional existing report to append findings to; a fresh
            report is created when omitted.
        plugin_shipped: If True, treat the file as plugin-shipped (the
            ``otelHeadersHelper`` key becomes a CRITICAL). If False,
            treat it as admin-managed (PASSED). If None, auto-detect
            from the path using ``_is_managed_settings_path``.

    Returns:
        The report (same instance as passed in when ``report`` is given).
    """
    if report is None:
        report = ValidationReport()

    source = str(settings_path)

    if not settings_path.exists():
        report.critical(
            f"Settings file does not exist: {source}",
            source,
        )
        return report

    data = _load_json_file(settings_path)
    if data is None:
        report.critical(
            "Failed to parse settings.json (invalid JSON or unreadable)",
            source,
        )
        return report

    # Auto-detect managed vs plugin-shipped when caller doesn't specify.
    if plugin_shipped is None:
        plugin_shipped = not _is_managed_settings_path(settings_path)

    # --- otelHeadersHelper check --------------------------------------
    if "otelHeadersHelper" in data:
        helper = data.get("otelHeadersHelper")
        if plugin_shipped:
            report.critical(
                "Plugin-shipped settings.json contains 'otelHeadersHelper' "
                f"(value: {helper!r}). Claude Code executes this script every "
                "~29 minutes to refresh OTEL auth headers — a plugin that "
                "ships this key gets periodic arbitrary code execution on "
                "the user's machine. This key is admin-managed only "
                "(managed-settings.json).",
                source,
            )
        else:
            report.passed(
                "otelHeadersHelper is present in a managed-settings file — "
                "admin-managed configuration, allowed.",
                source,
            )

    # --- env block checks ---------------------------------------------
    env_blocks = _extract_env_blocks(data)
    for env in env_blocks:
        if plugin_shipped:
            _validate_env_block(env, report, source)

    if not report.results:
        report.passed(
            "No telemetry supply-chain risks detected in settings.",
            source,
        )

    return report


def scan_plugin_for_telemetry(plugin_root: Path) -> ValidationReport:
    """Scan an entire plugin directory for telemetry supply-chain issues.

    Walks known config locations:
    - ``.claude-plugin/plugin.json``
    - ``hooks/hooks.json``
    - ``.claude-plugin/settings.json`` (if present)
    - top-level ``settings.json`` (if present)

    Args:
        plugin_root: Path to the plugin directory (must contain
            ``.claude-plugin/plugin.json``).

    Returns:
        A fresh ValidationReport with all findings.
    """
    report = ValidationReport()
    source_root = str(plugin_root)

    if not plugin_root.exists():
        report.critical(
            f"Plugin path does not exist: {source_root}",
            source_root,
        )
        return report
    if not plugin_root.is_dir():
        report.critical(
            f"Plugin path is not a directory: {source_root}",
            source_root,
        )
        return report

    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        report.critical(
            f"No .claude-plugin/plugin.json found at {source_root}",
            source_root,
        )
        return report

    any_checked = False
    for parts in _PLUGIN_ENV_CANDIDATES:
        candidate = plugin_root.joinpath(*parts)
        if not candidate.is_file():
            continue
        any_checked = True
        scan_settings_for_telemetry(candidate, report=report, plugin_shipped=True)

    if not any_checked:
        report.passed(
            "Plugin has no config files that could ship OTEL settings.",
            source_root,
        )
        return report

    # If scans touched files but found nothing, add an explicit PASSED
    # so consumers can see the validator ran.
    if not any(
        r.level in ("CRITICAL", "MAJOR", "MINOR") for r in report.results
    ):
        report.passed(
            "No telemetry supply-chain risks detected in plugin.",
            source_root,
        )

    return report


# =============================================================================
# CLI + reporting
# =============================================================================


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Human-readable summary reusing the shared ValidationReport printer."""
    print_results_by_level(report, verbose=verbose)


def print_json(report: ValidationReport) -> None:
    """Emit the full report as JSON."""
    print(json.dumps(report.to_dict(), indent=2))


def main() -> int:
    """Main entry point for ``cpv-validate-telemetry``."""
    check_remote_execution_guard()

    parser = argparse.ArgumentParser(
        description="Validate OTEL telemetry supply-chain risks in a plugin "
        "or settings.json file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Checks performed:
  1. otelHeadersHelper key in plugin-shipped settings.json (CRITICAL).
  2. OTEL_LOG_RAW_API_BODIES=1 in plugin env (CRITICAL).
  3. OTEL_LOG_USER_PROMPTS=1 / OTEL_LOG_TOOL_* = 1 in plugin env (MAJOR).
  4. OTEL_EXPORTER_OTLP_ENDPOINT pointing at external URL (MAJOR).
  5. Any other OTEL_* var shipped in plugin env (MINOR).

Exit codes:
  0 - No blocking issues
  1 - CRITICAL issues
  2 - MAJOR issues
  3 - MINOR issues
""",
    )
    parser.add_argument(
        "target",
        help="Path to a plugin directory or a settings.json file",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="Treat target as a settings.json file rather than a plugin dir",
    )
    parser.add_argument(
        "--managed",
        action="store_true",
        help="Explicitly mark the settings file as admin-managed "
        "(skips the otelHeadersHelper CRITICAL)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show PASSED/INFO results",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit results as JSON",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Save detailed report to file, print only summary to stdout",
    )
    args = parser.parse_args()

    target = Path(args.target).resolve()

    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        return EXIT_CRITICAL

    if args.settings or target.is_file():
        plugin_shipped = None if not args.managed else False
        report = scan_settings_for_telemetry(
            target, plugin_shipped=plugin_shipped
        )
    else:
        report = scan_plugin_for_telemetry(target)

    if args.json:
        print_json(report)
    elif args.report:
        save_report_and_print_summary(
            report,
            Path(args.report),
            "Telemetry Validation",
            print_results,
            args.verbose,
            plugin_path=str(target),
        )
    else:
        print_results(report, args.verbose)

    return report.exit_code if report.exit_code is not None else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
