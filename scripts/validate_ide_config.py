#!/usr/bin/env python3
"""Claude Plugins Validation - IDE Configuration Hygiene Validator.

Companion to ``validate_security.scan_ide_config_files``.

The CRITICAL-severity scan_for_secrets pass against IDE configs lives in
``validate_security.py`` (call: ``scan_ide_config_files``). That pass
catches REAL secret VALUES that ended up inside an IDE config file.

This validator complements it by adding the NIT-level "additional"
checks called out under TRDD-8ccb9337 §"Additional: warn on .env in IDE
configs":

    Check if any IDE config contains references to .env or
    $SECRET_NAME patterns. These are usually safe (env var
    references) but emit NIT if the env var name matches a known
    secret prefix like API_KEY, TOKEN, etc.

The two passes are intentionally separate so the high-cost regex suite
in scan_for_secrets stays where it is, and the low-cost env-name
predicate runs without forcing edits to the security validator.

Usage::

    uv run python scripts/validate_ide_config.py path/to/plugin/
    uv run python scripts/validate_ide_config.py path/to/plugin/ --report /tmp/r.md
    uv run python scripts/validate_ide_config.py path/to/plugin/ --strict   # NIT blocks

Exit codes:

    0 - No blocking findings (NIT only — non-blocking unless --strict)
    1 - CRITICAL  (target missing / not a directory / no plugin.json)
    4 - NIT       (only in --strict mode — see ValidationReport.exit_code_strict)

Severity rationale: env-var REFERENCES are not credentials in
themselves. They are *signals* that a reviewer should confirm the
referenced env var is populated outside the repo. NIT (advisory) is
the right level — promoting these to MAJOR/CRITICAL would drown the
real secret-value findings from validate_security in noise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from cpv_validation_common import (
    EXIT_CRITICAL,
    EXIT_OK,
    ValidationReport,
    check_remote_execution_guard,
    get_gitignore_filter,
    is_binary_file,
    launcher_epilog,
    print_results_by_level,
    save_report_and_print_summary,
)

# =============================================================================
# IDE_CONFIG_PATHS — must mirror validate_security.IDE_CONFIG_PATHS
# =============================================================================
#
# Kept as a separate constant (not imported from validate_security) so this
# validator stays self-contained and a stale validate_security copy cannot
# silently widen / narrow the scan surface here. Both lists are guarded by
# tests against the canonical TRDD-8ccb9337 spec.

IDE_CONFIG_PATHS: tuple[str, ...] = (
    ".vscode/settings.json",
    ".vscode/tasks.json",
    ".vscode/launch.json",
    ".idea/workspace.xml",
    ".idea/*.xml",
    ".cursor/mcp.json",
    ".cursor/settings.json",
    ".zed/settings.json",
    ".zed/tasks.json",
)


# =============================================================================
# Secret-like env-var name predicate
# =============================================================================
#
# An env var with a name like OPENAI_API_KEY / GITHUB_TOKEN /
# AWS_SECRET_ACCESS_KEY / DB_PASSWORD is almost always a credential.
# When an IDE config references such a name we emit NIT — usually the
# reference is safe (e.g. ${env:OPENAI_API_KEY}), but the reviewer should
# confirm the variable is populated outside the repo.
#
# We deliberately use word-boundary anchoring so prefixes like
# `KEYSTORE` or `KEYBOARD_SHORTCUT` do NOT match. The `KEY` token must
# be a standalone word, not a substring.

SECRET_LIKE_ENV_NAME = re.compile(
    r"(?:^|_)"
    r"(?:API_KEY|APIKEY|ACCESS_KEY|ACCESS_TOKEN|AUTH_TOKEN|AUTHTOKEN|"
    r"SECRET_KEY|SECRET_ACCESS_KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE|"
    r"CREDENTIALS|PRIVATE_KEY|CLIENT_SECRET|REFRESH_TOKEN|BEARER|"
    r"SESSION_TOKEN|WEBHOOK_SECRET|SIGNING_SECRET)"
    r"(?:_|$)",
    re.IGNORECASE,
)


def is_secret_like_env_name(name: str) -> bool:
    """Return True when ``name`` looks like a credential env-var name.

    Matches names containing tokens like ``API_KEY``, ``TOKEN``,
    ``SECRET``, ``PASSWORD``, ``CREDENTIALS``, etc., bounded by
    underscores or string edges. Empty strings never match.

    Examples::

        >>> is_secret_like_env_name("OPENAI_API_KEY")
        True
        >>> is_secret_like_env_name("DB_PASSWORD")
        True
        >>> is_secret_like_env_name("PATH")
        False
        >>> is_secret_like_env_name("KEYBOARD_SHORTCUT")
        False
    """
    if not name:
        return False
    return bool(SECRET_LIKE_ENV_NAME.search(name))


# =============================================================================
# Patterns for env-var REFERENCES inside IDE config text
# =============================================================================

# Env-var reference shapes commonly used inside IDE configs:
#   - VS Code:                   "${env:NAME}"
#   - VS Code legacy:            "${NAME}"  (may collide with other expansions)
#   - JetBrains:                 "$NAME$"
#   - Generic shell/CI:          "$NAME"  / "${NAME}"
#   - .env loaders:              `envFile: ".env"`, `envFile: "path/to/.env"`
#
# We extract the bare NAME (and a separate `.env` path scan) and let the
# is_secret_like_env_name predicate decide whether to emit NIT.

_ENVVAR_REF_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ${env:NAME} — VS Code idiom (NAME ends at `}`)
    re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}"),
    # ${NAME} — generic shell expansion
    re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}"),
    # $NAME — bare expansion (terminate on non-word char or EOL)
    re.compile(r"(?<![\w.])\$([A-Za-z_][A-Za-z0-9_]*)\b"),
    # %NAME% — Windows expansion (used in tasks.json on Windows)
    re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%"),
    # $NAME$ — JetBrains macro form
    re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\$"),
)

# .env file references — emit NIT regardless of name. The reviewer should
# confirm the .env file is .gitignored on the user's side.
#
# Match string-bounded `.env` paths only (e.g. `"envFile": "path/to/.env"`)
# and the bare `.env` token when it appears as a field VALUE — never inside
# arbitrary prose. The leading bound (`/`, `"`, `'`, whitespace, `:`, or
# string-start) suppresses false positives on words like `.envrc.local`,
# `myproject.env.example`, etc.
_DOTENV_REFERENCE = re.compile(
    r"""(?xi)              # verbose, case-insensitive
    (?:^|[\s'":/=])        # bounded: line start OR a string/path delimiter
    (?P<env>\.env)         # the literal .env basename
    (?P<rest>(?:\.[A-Za-z0-9_-]+)?)  # optional suffix (.env.local, .env.production)
    (?=$|[\s'":,/])        # bounded ending
    """
)


# =============================================================================
# Single-file scanner
# =============================================================================


def scan_ide_config_for_env_refs(
    file_path: Path,
    report: ValidationReport,
    plugin_root: Path,
) -> int:
    """Emit NIT findings for env-var references with secret-like names.

    Walks the file line-by-line, extracts every env-var reference shape
    we know about, deduplicates them, and emits one NIT per UNIQUE
    secret-like name in the file.

    Also emits a single NIT when the file references ``.env`` (any form).

    Args:
        file_path: Absolute path to the IDE config file. Missing files
            and binary files are silently skipped (return value: 0).
        report: ValidationReport to add NIT findings to.
        plugin_root: Plugin root, used to compute relative paths in
            findings for clean reporting.

    Returns:
        Number of NIT findings appended to the report.
    """
    if not file_path.is_file():
        return 0
    if is_binary_file(file_path):
        return 0
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0

    try:
        rel_path = str(file_path.relative_to(plugin_root))
    except ValueError:
        rel_path = str(file_path)

    findings_added = 0

    # --- Collect unique secret-like env-var names seen in this file ----------
    seen_names: dict[str, int] = {}  # name -> first 1-based line number
    for line_no, line in enumerate(content.splitlines(), start=1):
        for pattern in _ENVVAR_REF_PATTERNS:
            for match in pattern.finditer(line):
                name = match.group(1)
                if not is_secret_like_env_name(name):
                    continue
                if name in seen_names:
                    continue
                seen_names[name] = line_no

    for name, line_no in sorted(seen_names.items(), key=lambda kv: (kv[1], kv[0])):
        report.nit(
            f"IDE config references secret-like env-var ${{{name}}} "
            "— confirm the variable is populated outside the repo "
            "(e.g. via a gitignored .env or shell profile, never committed)",
            rel_path,
            line_no,
        )
        findings_added += 1

    # --- Emit a single .env reference NIT (per file) -------------------------
    dotenv_match = _DOTENV_REFERENCE.search(content)
    if dotenv_match:
        # Compute 1-based line number of the first match
        offset = dotenv_match.start()
        line_no = content[:offset].count("\n") + 1
        report.nit(
            "IDE config references .env file — confirm the .env is gitignored on the user's side and never committed",
            rel_path,
            line_no,
        )
        findings_added += 1

    return findings_added


# =============================================================================
# Plugin-level orchestration
# =============================================================================


def _iter_ide_config_files(plugin_root: Path) -> list[Path]:
    """Resolve every IDE config file the plugin ships, deduped.

    Both literal entries (``.idea/workspace.xml``) and globs
    (``.idea/*.xml``) can match the same file — the seen-set ensures
    each file is yielded exactly once.

    Returns:
        Sorted list of absolute Path objects.
    """
    seen: set[Path] = set()
    for entry in IDE_CONFIG_PATHS:
        for match in plugin_root.glob(entry):
            if not match.is_file():
                continue
            try:
                resolved = match.resolve()
            except (OSError, RuntimeError):
                continue
            seen.add(resolved)
    return sorted(seen)


def scan_plugin_for_ide_config_hygiene(plugin_root: Path) -> ValidationReport:
    """Run IDE-config hygiene checks against an entire plugin tree.

    Validates:
      1. Plugin root exists and is a directory.
      2. ``.claude-plugin/plugin.json`` is present (sanity check that
         the path is actually a Claude Code plugin).
      3. For each IDE config file: skip if gitignored, otherwise scan
         for secret-like env-var references and ``.env`` mentions.

    Args:
        plugin_root: Plugin directory to scan.

    Returns:
        ValidationReport with CRITICAL findings on input errors and NIT
        findings on hygiene issues.
    """
    report = ValidationReport()
    source_root = str(plugin_root)

    if not plugin_root.exists():
        report.critical(f"Plugin path does not exist: {source_root}", source_root)
        return report
    if not plugin_root.is_dir():
        report.critical(f"Plugin path is not a directory: {source_root}", source_root)
        return report
    if not (plugin_root / ".claude-plugin" / "plugin.json").is_file():
        report.critical(
            f"No .claude-plugin/plugin.json found at {source_root} — is this actually a Claude Code plugin?",
            source_root,
        )
        return report

    gi = get_gitignore_filter(plugin_root)
    total_findings = 0
    files_scanned = 0
    files_skipped_gitignored = 0

    for path in _iter_ide_config_files(plugin_root):
        if gi.is_ignored(path):
            files_skipped_gitignored += 1
            continue
        files_scanned += 1
        total_findings += scan_ide_config_for_env_refs(path, report, plugin_root)

    if total_findings == 0:
        if files_scanned == 0:
            report.passed(
                "No IDE config files found in plugin tree.",
                source_root,
            )
        else:
            report.passed(
                f"Scanned {files_scanned} IDE config file(s) — no secret-like env-var "
                "references or .env mentions detected.",
                source_root,
            )

    if files_skipped_gitignored:
        report.info(
            f"Skipped {files_skipped_gitignored} gitignored IDE config file(s) "
            "(secrets in gitignored files are not shipped).",
            source_root,
        )

    return report


# =============================================================================
# CLI + reporting
# =============================================================================


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Human-readable summary reusing the shared ValidationReport printer."""
    print_results_by_level(report, verbose=verbose)


def main() -> int:
    """Main entry point for ``cpv-validate-ide-config``."""
    check_remote_execution_guard()

    parser = argparse.ArgumentParser(
        description=(
            "Validate IDE-config files (.vscode/, .idea/, .cursor/, .zed/) "
            "for secret-like env-var references and .env mentions."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
What this validator checks:
  - .vscode/settings.json, tasks.json, launch.json
  - .idea/workspace.xml and .idea/*.xml
  - .cursor/mcp.json, settings.json
  - .zed/settings.json, tasks.json

It emits NIT findings on:
  1. Env-var references (${VAR}, ${env:VAR}, $VAR, %VAR%, $VAR$)
     where the variable name looks credential-like (API_KEY, TOKEN,
     SECRET, PASSWORD, CREDENTIALS, etc.).
  2. References to a ".env" file (any path).

Files listed in .gitignore are skipped (their content is never shipped).

NOTE: REAL secret VALUES (sk-..., ghp_..., AKIA..., etc.) inside IDE
configs are caught at CRITICAL severity by validate_security.py via
scan_ide_config_files. This validator is the NIT-level companion only.

Exit codes:
  0 - No blocking findings (NIT alone never blocks)
  1 - CRITICAL (target missing, not a directory, or no plugin.json)
  4 - NIT (only when --strict is passed)

"""
        + launcher_epilog("ide-config"),
    )
    parser.add_argument("target", help="Path to a plugin directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show PASSED/INFO results")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with non-zero code when only NIT findings are present (default: ignore NITs)",
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
        # Mirror the in-report CRITICAL so the CLI exits 1 even with no plugin
        report = ValidationReport()
        report.critical(f"Plugin path does not exist: {target}", str(target))
        if args.report:
            save_report_and_print_summary(
                report,
                Path(args.report),
                "IDE Config Hygiene",
                print_results,
                args.verbose,
                plugin_path=str(target),
            )
        else:
            print_results(report, args.verbose)
        return EXIT_CRITICAL

    report = scan_plugin_for_ide_config_hygiene(target)

    if args.report:
        save_report_and_print_summary(
            report,
            Path(args.report),
            "IDE Config Hygiene",
            print_results,
            args.verbose,
            plugin_path=str(target),
        )
    else:
        print_results(report, args.verbose)

    if args.strict:
        return report.exit_code_strict()
    code = report.exit_code
    return code if code is not None else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
