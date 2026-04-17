#!/usr/bin/env python3
"""
Claude Plugins Validation - settings.json extraKnownMarketplaces Validator

Validates the `extraKnownMarketplaces` block of a Claude Code settings.json
file. This is DIFFERENT from validate_marketplace.py which validates
per-plugin sources inside a marketplace.json. This validator works at the
settings.json level and understands the v2.1.80 "settings" inline
marketplace source type.

Based on:
  - https://code.claude.com/docs/en/settings.md
  - v2.1.80 release notes (inline marketplace via extraKnownMarketplaces)

Usage:
    uv run python scripts/validate_settings_marketplace.py path/to/settings.json
    uv run python scripts/validate_settings_marketplace.py path/to/settings.json --verbose
    uv run python scripts/validate_settings_marketplace.py path/to/settings.json --json
    uv run python scripts/validate_settings_marketplace.py path/to/settings.json --report out.md

Exit codes:
    0 - All checks passed
    1 - CRITICAL issues found
    2 - MAJOR issues found
    3 - MINOR issues found
    4 - NIT issues found (only in --strict mode)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from cpv_management_common import load_jsonc
from cpv_validation_common import (
    COLORS,
    NAME_PATTERN,
    SETTINGS_SOURCE_REQUIRED_FIELDS,
    STRICT_KNOWN_MARKETPLACES_ALLOWED_SOURCE_TYPES,
    VALID_SETTINGS_SOURCE_TYPES,
    ValidationReport,
    save_report_and_print_summary,
)

# Top-level keys in settings.json that we validate
EXTRA_KNOWN_MARKETPLACES_KEY = "extraKnownMarketplaces"
STRICT_KNOWN_MARKETPLACES_KEY = "strictKnownMarketplaces"


def _fmt_ctx(marketplace_name: str, *, field: str | None = None) -> str:
    """Build a human-readable context string for a finding."""
    base = f"{EXTRA_KNOWN_MARKETPLACES_KEY}.{marketplace_name}"
    if field:
        return f"{base}.{field}"
    return base


def validate_source_object(
    source_obj: dict[str, Any],
    marketplace_name: str,
    report: ValidationReport,
    file_label: str,
    *,
    strict_mode: bool = False,
) -> None:
    """Validate a single source object from extraKnownMarketplaces[<name>].source.

    Args:
        source_obj: The source dict (must contain a 'source' key with type name)
        marketplace_name: Name of the marketplace entry this source belongs to
        report: ValidationReport to add results to
        file_label: Filename for error messages (e.g. "settings.json")
        strict_mode: If True, the source object lives inside
            ``strictKnownMarketplaces`` where plugin-marketplaces.md:625-669
            restricts source types to {github, url, hostPattern, pathPattern}.
            Other otherwise-accepted settings-level types (npm, git, file,
            directory, settings, git-subdir) emit a MAJOR because Claude Code
            refuses them at runtime in strict mode.
    """
    ctx = _fmt_ctx(marketplace_name, field="source")

    # 1. Must have a 'source' key (type discriminator)
    source_type = source_obj.get("source")
    if source_type is None:
        report.major(
            f"{ctx}: missing required 'source' key (expected one of: {', '.join(sorted(VALID_SETTINGS_SOURCE_TYPES))})",
            file_label,
        )
        return

    if not isinstance(source_type, str):
        report.major(
            f"{ctx}.source: must be a string, got {type(source_type).__name__}",
            file_label,
        )
        return

    # 2. 'source' value must be a known type
    if source_type not in VALID_SETTINGS_SOURCE_TYPES:
        report.major(
            f"{ctx}.source: unknown source type '{source_type}' "
            f"(valid: {', '.join(sorted(VALID_SETTINGS_SOURCE_TYPES))})",
            file_label,
        )
        return

    # 2b. strict mode narrows the allowlist per plugin-marketplaces.md:625-669
    if strict_mode and source_type not in STRICT_KNOWN_MARKETPLACES_ALLOWED_SOURCE_TYPES:
        report.major(
            f"{ctx}.source: '{source_type}' is not allowed inside strictKnownMarketplaces "
            f"(only {', '.join(sorted(STRICT_KNOWN_MARKETPLACES_ALLOWED_SOURCE_TYPES))} are accepted "
            "per plugin-marketplaces.md:625-669)",
            file_label,
        )
        return

    # 3. Required fields per source type
    required = SETTINGS_SOURCE_REQUIRED_FIELDS.get(source_type, set())
    missing = sorted(required - set(source_obj.keys()))
    if missing:
        report.major(
            f"{ctx}: source type '{source_type}' missing required field(s): {', '.join(missing)}",
            file_label,
        )
        # Continue — we still want to sanity-check fields that ARE present

    # 4. Per-type sanity checks (shape of present fields)
    if source_type == "github":
        repo = source_obj.get("repo")
        if repo is not None:
            if not isinstance(repo, str):
                report.major(f"{ctx}.repo: must be a string, got {type(repo).__name__}", file_label)
            elif "/" not in repo:
                report.major(
                    f"{ctx}.repo: '{repo}' is not in 'owner/name' format",
                    file_label,
                )
            else:
                report.passed(f"{ctx}: github source valid ({repo})", file_label)

    elif source_type in ("url", "git", "git-subdir"):
        url = source_obj.get("url")
        if url is not None:
            if not isinstance(url, str):
                report.major(f"{ctx}.url: must be a string, got {type(url).__name__}", file_label)
            elif not (url.startswith("http://") or url.startswith("https://") or url.startswith("git://")):
                report.minor(
                    f"{ctx}.url: '{url}' does not start with http://, https://, or git://",
                    file_label,
                )
            else:
                report.passed(f"{ctx}: {source_type} source has valid URL", file_label)

        if source_type == "git-subdir":
            path_val = source_obj.get("path")
            if path_val is not None and not isinstance(path_val, str):
                report.major(
                    f"{ctx}.path: must be a string, got {type(path_val).__name__}",
                    file_label,
                )

    elif source_type == "npm":
        package = source_obj.get("package")
        if package is not None:
            if not isinstance(package, str):
                report.major(f"{ctx}.package: must be a string, got {type(package).__name__}", file_label)
            elif not package.strip():
                report.major(f"{ctx}.package: must be a non-empty string", file_label)
            else:
                report.passed(f"{ctx}: npm source valid ({package})", file_label)
        # Optional version
        version = source_obj.get("version")
        if version is not None and not isinstance(version, str):
            report.minor(
                f"{ctx}.version: should be a string, got {type(version).__name__}",
                file_label,
            )

    elif source_type == "directory":
        path_val = source_obj.get("path")
        if path_val is not None:
            if not isinstance(path_val, str):
                report.major(f"{ctx}.path: must be a string, got {type(path_val).__name__}", file_label)
            else:
                report.warning(
                    f"{ctx}: directory source points to a local path ('{path_val}') — "
                    "only usable on this machine; do not ship this in a plugin settings snippet",
                    file_label,
                )

    elif source_type in ("hostPattern", "pathPattern"):
        # v2.22.3 — GAP-5: the `hostPattern`/`pathPattern` values are regex
        # strings (plugin-marketplaces.md:645-669). Previously CPV only
        # checked presence. Now also attempt `re.compile()` and emit MINOR
        # when the pattern is syntactically invalid so authors don't ship a
        # marketplace that silently never matches at runtime.
        pattern_val = source_obj.get(source_type)
        if pattern_val is not None:
            if not isinstance(pattern_val, str):
                report.major(
                    f"{ctx}.{source_type}: must be a string, got {type(pattern_val).__name__}",
                    file_label,
                )
            else:
                try:
                    re.compile(pattern_val)
                except re.error as exc:
                    report.minor(
                        f"{ctx}.{source_type}: invalid regex '{pattern_val}' — {exc}",
                        file_label,
                    )

    elif source_type == "settings":
        # Inline marketplace: must declare name + plugins array
        name_val = source_obj.get("name")
        plugins_val = source_obj.get("plugins")

        if name_val is not None:
            if not isinstance(name_val, str):
                report.major(
                    f"{ctx}.name: must be a string, got {type(name_val).__name__}",
                    file_label,
                )
            elif not NAME_PATTERN.match(name_val):
                report.minor(
                    f"{ctx}.name: '{name_val}' should be kebab-case (lowercase, digits, hyphens)",
                    file_label,
                )

        if plugins_val is not None:
            if not isinstance(plugins_val, list):
                report.major(
                    f"{ctx}.plugins: must be a list, got {type(plugins_val).__name__}",
                    file_label,
                )
            elif not plugins_val:
                report.minor(
                    f"{ctx}.plugins: inline marketplace has empty plugins list",
                    file_label,
                )
            else:
                for idx, plugin_entry in enumerate(plugins_val):
                    entry_ctx = f"{ctx}.plugins[{idx}]"
                    if not isinstance(plugin_entry, dict):
                        report.major(
                            f"{entry_ctx}: must be an object, got {type(plugin_entry).__name__}",
                            file_label,
                        )
                        continue
                    if "name" not in plugin_entry:
                        report.major(f"{entry_ctx}: missing required 'name' field", file_label)
                    else:
                        p_name = plugin_entry["name"]
                        if not isinstance(p_name, str):
                            report.major(
                                f"{entry_ctx}.name: must be a string, got {type(p_name).__name__}",
                                file_label,
                            )
                        elif not NAME_PATTERN.match(p_name):
                            report.minor(
                                f"{entry_ctx}.name: '{p_name}' should be kebab-case",
                                file_label,
                            )
                    if "source" not in plugin_entry:
                        report.major(f"{entry_ctx}: missing required 'source' field", file_label)
                    else:
                        # Plugin-level source: uses validate_marketplace.py source types,
                        # NOT the settings-level ones. We intentionally do only a light
                        # sanity check here and leave deep validation to that validator.
                        p_source = plugin_entry["source"]
                        if not (isinstance(p_source, str) or isinstance(p_source, dict)):
                            report.major(
                                f"{entry_ctx}.source: must be a string or object, got {type(p_source).__name__}",
                                file_label,
                            )
                if not report.has_critical and not report.has_major:
                    report.passed(f"{ctx}: inline settings source with {len(plugins_val)} plugin(s) valid", file_label)


def validate_extra_known_marketplaces(
    block: dict[str, Any],
    report: ValidationReport,
    file_label: str,
) -> None:
    """Validate the entire extraKnownMarketplaces block.

    Args:
        block: The raw dict from settings.json::extraKnownMarketplaces
        report: ValidationReport to add results to
        file_label: Filename for error messages
    """
    if not isinstance(block, dict):
        report.critical(
            f"{EXTRA_KNOWN_MARKETPLACES_KEY}: must be an object mapping names to marketplace configs",
            file_label,
        )
        return

    if not block:
        # Empty dict is legal but unusual — just INFO
        report.info(f"{EXTRA_KNOWN_MARKETPLACES_KEY}: block is empty", file_label)
        return

    for marketplace_name, entry in block.items():
        if not isinstance(marketplace_name, str) or not marketplace_name:
            report.major(
                f"{EXTRA_KNOWN_MARKETPLACES_KEY}: marketplace key must be a non-empty string",
                file_label,
            )
            continue

        if not NAME_PATTERN.match(marketplace_name):
            report.minor(
                f"{_fmt_ctx(marketplace_name)}: name '{marketplace_name}' should be kebab-case "
                "(lowercase, digits, hyphens)",
                file_label,
            )

        if not isinstance(entry, dict):
            report.major(
                f"{_fmt_ctx(marketplace_name)}: must be an object, got {type(entry).__name__}",
                file_label,
            )
            continue

        source_obj = entry.get("source")
        if source_obj is None:
            report.major(
                f"{_fmt_ctx(marketplace_name)}: missing required 'source' object",
                file_label,
            )
            continue

        if not isinstance(source_obj, dict):
            report.major(
                f"{_fmt_ctx(marketplace_name, field='source')}: must be an object, got {type(source_obj).__name__}",
                file_label,
            )
            continue

        validate_source_object(source_obj, marketplace_name, report, file_label)


def validate_strict_known_marketplaces(
    block: list[Any],
    report: ValidationReport,
    file_label: str,
) -> None:
    """Validate the strictKnownMarketplaces array.

    Per plugin-marketplaces.md:625-669 this is an allowlist used in managed
    Claude Code installs. Entries are source objects — the spec's allowed
    source types are NARROWER than extraKnownMarketplaces: only
    {github, url, hostPattern, pathPattern} are accepted.
    """
    if not isinstance(block, list):
        report.critical(
            f"{STRICT_KNOWN_MARKETPLACES_KEY}: must be an array, got {type(block).__name__}",
            file_label,
        )
        return

    if not block:
        report.info(
            f"{STRICT_KNOWN_MARKETPLACES_KEY}: empty array — lockdown applies, no marketplaces allowed",
            file_label,
        )
        return

    for idx, entry in enumerate(block):
        synthetic_name = f"[{idx}]"
        if not isinstance(entry, dict):
            report.major(
                f"{STRICT_KNOWN_MARKETPLACES_KEY}{synthetic_name}: must be an object, got {type(entry).__name__}",
                file_label,
            )
            continue

        # strictKnownMarketplaces items are flat source objects, e.g.
        # {"source": "github", "repo": "owner/name"} — the entry itself IS
        # the source object (unlike extraKnownMarketplaces where sources are
        # nested under a marketplace-name key with a "source" sub-object).
        validate_source_object(
            entry,
            synthetic_name,
            report,
            file_label,
            strict_mode=True,
        )


def validate_settings_marketplace_file(
    settings_path: Path,
    report: ValidationReport | None = None,
) -> ValidationReport:
    """Validate settings.json extraKnownMarketplaces block.

    Args:
        settings_path: Path to settings.json file
        report: Existing report to append to (creates new one if None)

    Returns:
        ValidationReport with all findings
    """
    if report is None:
        report = ValidationReport()

    file_label = settings_path.name

    if not settings_path.exists():
        report.critical(f"settings.json not found: {settings_path}", file_label)
        return report

    if not settings_path.is_file():
        report.critical(f"settings.json path is not a regular file: {settings_path}", file_label)
        return report

    # Parse JSONC (settings.json supports comments + trailing commas)
    try:
        data = load_jsonc(settings_path)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        report.critical(f"settings.json: JSON parse error: {e}", file_label)
        return report

    if not isinstance(data, dict):
        report.critical("settings.json: root must be a JSON object", file_label)
        return report

    saw_any_block = False
    if EXTRA_KNOWN_MARKETPLACES_KEY in data:
        saw_any_block = True
        validate_extra_known_marketplaces(data[EXTRA_KNOWN_MARKETPLACES_KEY], report, file_label)

    if STRICT_KNOWN_MARKETPLACES_KEY in data:
        saw_any_block = True
        validate_strict_known_marketplaces(data[STRICT_KNOWN_MARKETPLACES_KEY], report, file_label)

    if not saw_any_block:
        report.passed(
            f"settings.json has no '{EXTRA_KNOWN_MARKETPLACES_KEY}' or "
            f"'{STRICT_KNOWN_MARKETPLACES_KEY}' block — nothing to validate",
            file_label,
        )
        return report

    # Summary PASSED line if nothing went wrong
    if not report.has_critical and not report.has_major and not report.has_minor:
        report.passed(
            "settings.json: all marketplace blocks valid",
            file_label,
        )

    return report


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    colors = COLORS

    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    print("\n" + "=" * 60)
    print("settings.json extraKnownMarketplaces Validation Report")
    print("=" * 60)

    print("\nSummary:")
    print(f"  {colors['CRITICAL']}CRITICAL: {counts['CRITICAL']}{colors['RESET']}")
    print(f"  {colors['MAJOR']}MAJOR:    {counts['MAJOR']}{colors['RESET']}")
    print(f"  {colors['MINOR']}MINOR:    {counts['MINOR']}{colors['RESET']}")
    print(f"  {colors['NIT']}NIT:      {counts['NIT']}{colors['RESET']}")
    print(f"  {colors['WARNING']}WARNING:  {counts['WARNING']}{colors['RESET']}")
    if verbose:
        print(f"  {colors['INFO']}INFO:     {counts['INFO']}{colors['RESET']}")
        print(f"  {colors['PASSED']}PASSED:   {counts['PASSED']}{colors['RESET']}")

    print("\nDetails:")
    for r in report.results:
        if r.level == "PASSED" and not verbose:
            continue
        if r.level == "INFO" and not verbose:
            continue

        color = colors[r.level]
        reset = colors["RESET"]
        file_info = f" ({r.file})" if r.file else ""
        line_info = f":{r.line}" if r.line else ""
        print(f"  {color}[{r.level}]{reset} {r.message}{file_info}{line_info}")

    print("\n" + "-" * 60)
    if report.exit_code == 0:
        print(f"{colors['PASSED']}All settings.json marketplace checks passed{colors['RESET']}")
    elif report.exit_code == 1:
        print(f"{colors['CRITICAL']}CRITICAL issues found{colors['RESET']}")
    elif report.exit_code == 2:
        print(f"{colors['MAJOR']}MAJOR issues found{colors['RESET']}")
    elif report.exit_code == 3:
        print(f"{colors['MINOR']}MINOR issues found{colors['RESET']}")
    else:
        print(f"{colors['NIT']}NIT issues found (--strict mode){colors['RESET']}")

    print()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate the extraKnownMarketplaces block of a Claude Code settings.json file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all results")
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to settings.json file",
    )
    args = parser.parse_args()

    if not args.path:
        print("Error: settings.json path is required", file=sys.stderr)
        parser.print_help(sys.stderr)
        return 1

    path = Path(args.path).resolve()
    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        return 1

    if path.is_dir():
        # Allow passing a directory — look for settings.json inside
        candidate = path / "settings.json"
        if not candidate.exists():
            print(f"Error: no settings.json found in directory {path}", file=sys.stderr)
            return 1
        path = candidate

    if path.suffix != ".json" and path.name != "settings.json":
        print(f"Error: {path} is not a settings.json file", file=sys.stderr)
        return 1

    report = validate_settings_marketplace_file(path)

    if args.json:
        output = {
            "exit_code": report.exit_code,
            "counts": {
                "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
                "major": sum(1 for r in report.results if r.level == "MAJOR"),
                "minor": sum(1 for r in report.results if r.level == "MINOR"),
                "info": sum(1 for r in report.results if r.level == "INFO"),
                "passed": sum(1 for r in report.results if r.level == "PASSED"),
                "nit": sum(1 for r in report.results if r.level == "NIT"),
                "warning": sum(1 for r in report.results if r.level == "WARNING"),
            },
            "results": [
                {
                    "level": r.level,
                    "message": r.message,
                    "file": r.file,
                    "line": r.line,
                }
                for r in report.results
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        if args.report:
            save_report_and_print_summary(
                report,
                Path(args.report),
                "settings.json Marketplace Validation",
                print_results,
                args.verbose,
                plugin_path=str(path),
            )
        else:
            print_results(report, args.verbose)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
