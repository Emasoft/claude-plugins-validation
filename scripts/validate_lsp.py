#!/usr/bin/env python3
"""
Claude Plugins Validation - LSP Server Configuration Validator

Validates LSP (Language Server Protocol) server configurations in Claude Code plugins.
Checks language server definitions, initialization options, and workspace settings.

Based on:
  - https://microsoft.github.io/language-server-protocol/specification
  - https://code.claude.com/docs/en/plugins-reference.md

Usage:
    uv run python scripts/validate_lsp.py path/to/lsp-config.json
    uv run python scripts/validate_lsp.py path/to/plugin/
    uv run python scripts/validate_lsp.py path/to/lsp-config.json --verbose
    uv run python scripts/validate_lsp.py path/to/lsp-config.json --json

Exit codes:
    0 - All checks passed
    1 - CRITICAL issues found
    2 - MAJOR issues found
    3 - MINOR issues found
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# Validation result levels
Level = Literal["CRITICAL", "MAJOR", "MINOR", "INFO", "PASSED"]


@dataclass
class ValidationResult:
    """Single validation result."""

    level: Level
    message: str
    file: str | None = None
    line: int | None = None


@dataclass
class ValidationReport:
    """Complete validation report."""

    results: list[ValidationResult] = field(default_factory=list)

    def add(
        self,
        level: Level,
        message: str,
        file: str | None = None,
        line: int | None = None,
    ) -> None:
        """Add a validation result."""
        self.results.append(ValidationResult(level, message, file, line))

    def passed(self, message: str, file: str | None = None) -> None:
        """Add a passed check."""
        self.add("PASSED", message, file)

    def info(self, message: str, file: str | None = None) -> None:
        """Add an info message."""
        self.add("INFO", message, file)

    def minor(self, message: str, file: str | None = None, line: int | None = None) -> None:
        """Add a minor issue."""
        self.add("MINOR", message, file, line)

    def major(self, message: str, file: str | None = None, line: int | None = None) -> None:
        """Add a major issue."""
        self.add("MAJOR", message, file, line)

    def critical(self, message: str, file: str | None = None, line: int | None = None) -> None:
        """Add a critical issue."""
        self.add("CRITICAL", message, file, line)

    @property
    def has_critical(self) -> bool:
        return any(r.level == "CRITICAL" for r in self.results)

    @property
    def has_major(self) -> bool:
        return any(r.level == "MAJOR" for r in self.results)

    @property
    def has_minor(self) -> bool:
        return any(r.level == "MINOR" for r in self.results)

    @property
    def exit_code(self) -> int:
        if self.has_critical:
            return 1
        if self.has_major:
            return 2
        if self.has_minor:
            return 3
        return 0


# Known LSP server configuration fields
KNOWN_LSP_FIELDS = {
    "command",  # Server executable path
    "args",  # Command-line arguments
    "filetypes",  # Associated file types
    "rootPatterns",  # Root directory indicators
    "initializationOptions",  # Server initialization options
    "settings",  # Workspace settings
    "env",  # Environment variables
    "cwd",  # Working directory
    "transport",  # Transport type (stdio, pipe, socket)
}

# Common language servers and their expected commands
KNOWN_LANGUAGE_SERVERS = {
    "pyright": "pyright-langserver",
    "pylsp": "pylsp",
    "typescript": "typescript-language-server",
    "tsserver": "tsserver",
    "rust-analyzer": "rust-analyzer",
    "gopls": "gopls",
    "clangd": "clangd",
    "lua-language-server": "lua-language-server",
    "yaml-language-server": "yaml-language-server",
    "json-language-server": "vscode-json-languageserver",
    "html-language-server": "vscode-html-languageserver",
    "css-language-server": "vscode-css-languageserver",
}

# Environment variable pattern
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# Plugin-specific environment variables
PLUGIN_ENV_VARS = {"CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"}


def is_absolute_path(path: str) -> bool:
    """Check if a path appears to be an absolute path."""
    if path.startswith("/") and not path.startswith("${"):
        return True
    if len(path) > 2 and path[1] == ":" and path[2] == "\\":
        return True
    return False


def validate_env_var_syntax(value: str, report: ValidationReport, context: str) -> None:
    """Validate environment variable syntax in a string value."""
    if "${" in value:
        open_count = value.count("${")
        close_count = value.count("}")
        if open_count != close_count:
            report.major(f"Malformed env var syntax (unclosed braces) in {context}")
            return

        for match in ENV_VAR_PATTERN.finditer(value):
            var_name = match.group(1)
            default = match.group(2)

            if default is None and var_name not in PLUGIN_ENV_VARS:
                report.info(f"Env var ${{{var_name}}} has no default value in {context}")


def validate_path_value(
    value: str,
    report: ValidationReport,
    context: str,
    plugin_root: Path | None = None,
) -> None:
    """Validate a path value in LSP configuration."""
    if is_absolute_path(value):
        report.major(f"Absolute path found in {context}: {value} - use ${{CLAUDE_PLUGIN_ROOT}} for portability")
        return

    validate_env_var_syntax(value, report, context)

    if plugin_root and "${CLAUDE_PLUGIN_ROOT}" in value:
        resolved = value.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
        resolved_path = Path(resolved)
        if "." in resolved_path.name or resolved_path.suffix:
            if not resolved_path.exists():
                report.info(f"Referenced file may not exist: {value}")


def validate_lsp_server(
    server_name: str,
    config: dict[str, Any],
    report: ValidationReport,
    plugin_root: Path | None = None,
    file_context: str = "lsp-config",
) -> None:
    """Validate a single LSP server configuration."""
    ctx = f"{file_context}:{server_name}"

    # Check for unknown fields
    for key in config.keys():
        if key not in KNOWN_LSP_FIELDS:
            report.info(f"Unknown field '{key}' in server {server_name}")

    # Validate command (required for local servers)
    if "command" not in config:
        report.critical(f"Server {server_name} missing required 'command' field")
    else:
        command = config["command"]
        if not isinstance(command, str):
            report.critical(f"Server {server_name} 'command' must be a string")
        else:
            # Check if command is known language server
            cmd_base = Path(command).name if "/" in command or "\\" in command else command
            if cmd_base in KNOWN_LANGUAGE_SERVERS.values():
                report.passed(f"Server {server_name} uses known LSP: {cmd_base}")

            # Check if command exists
            if plugin_root and "${CLAUDE_PLUGIN_ROOT}" in command:
                resolved = command.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
                if Path(resolved).exists():
                    if not os.access(resolved, os.X_OK):
                        report.major(f"Server {server_name} command not executable")
                    else:
                        report.passed(f"Server {server_name} command is executable")
            elif shutil.which(command):
                report.passed(f"Server {server_name} command '{command}' found in PATH")
            elif command in ("npx", "node", "python", "python3"):
                report.passed(f"Server {server_name} uses runtime: {command}")
            else:
                report.info(f"Server {server_name} command '{command}' not found in PATH")

            validate_path_value(command, report, f"{ctx}:command", plugin_root)

    # Validate args
    if "args" in config:
        args = config["args"]
        if not isinstance(args, list):
            report.major(f"Server {server_name} 'args' must be an array")
        else:
            for i, arg in enumerate(args):
                if not isinstance(arg, str):
                    report.major(f"Server {server_name} args[{i}] must be a string")
                else:
                    validate_env_var_syntax(arg, report, f"{ctx}:args[{i}]")

    # Validate filetypes
    if "filetypes" in config:
        filetypes = config["filetypes"]
        if not isinstance(filetypes, list):
            report.major(f"Server {server_name} 'filetypes' must be an array")
        else:
            if not filetypes:
                report.minor(f"Server {server_name} has empty filetypes array")
            for ft in filetypes:
                if not isinstance(ft, str):
                    report.major(f"Server {server_name} filetype must be a string")

    # Validate rootPatterns
    if "rootPatterns" in config:
        patterns = config["rootPatterns"]
        if not isinstance(patterns, list):
            report.major(f"Server {server_name} 'rootPatterns' must be an array")
        else:
            for pattern in patterns:
                if not isinstance(pattern, str):
                    report.major(f"Server {server_name} rootPattern must be a string")

    # Validate initializationOptions
    if "initializationOptions" in config:
        init_opts = config["initializationOptions"]
        if not isinstance(init_opts, dict):
            report.major(f"Server {server_name} 'initializationOptions' must be an object")

    # Validate settings
    if "settings" in config:
        settings = config["settings"]
        if not isinstance(settings, dict):
            report.major(f"Server {server_name} 'settings' must be an object")

    # Validate env
    if "env" in config:
        env = config["env"]
        if not isinstance(env, dict):
            report.major(f"Server {server_name} 'env' must be an object")
        else:
            for key, value in env.items():
                if not isinstance(value, str):
                    report.major(f"Server {server_name} env[{key}] must be a string")
                else:
                    validate_env_var_syntax(value, report, f"{ctx}:env[{key}]")

    # Validate cwd
    if "cwd" in config:
        cwd = config["cwd"]
        if not isinstance(cwd, str):
            report.major(f"Server {server_name} 'cwd' must be a string")
        else:
            validate_path_value(cwd, report, f"{ctx}:cwd", plugin_root)

    report.passed(f"Server {server_name} configuration validated")


def validate_lsp_config(
    config_path: Path,
    plugin_root: Path | None = None,
    report: ValidationReport | None = None,
) -> ValidationReport:
    """Validate an LSP configuration file.

    Args:
        config_path: Path to the LSP config file
        plugin_root: Optional path to plugin root for path resolution
        report: Optional existing report to add to

    Returns:
        ValidationReport with all validation results
    """
    if report is None:
        report = ValidationReport()

    rel_path = config_path.name
    if plugin_root:
        try:
            rel_path = str(config_path.relative_to(plugin_root))
        except ValueError:
            pass

    if not config_path.exists():
        report.info(f"LSP config file not found: {rel_path}")
        return report

    # Parse JSON
    try:
        config = json.loads(config_path.read_text())
    except json.JSONDecodeError as e:
        report.critical(f"Invalid JSON in {rel_path}: {e}")
        return report

    report.passed(f"{rel_path} is valid JSON")

    # Check for languageServers field
    servers_key = None
    for key in ("languageServers", "lspServers", "servers"):
        if key in config:
            servers_key = key
            break

    if servers_key is None:
        report.info(f"No language server definitions found in {rel_path}")
        return report

    servers = config[servers_key]

    if not isinstance(servers, dict):
        report.critical(f"'{servers_key}' must be an object in {rel_path}")
        return report

    if not servers:
        report.info(f"No LSP servers defined in {rel_path}")
        return report

    report.info(f"Found {len(servers)} LSP server(s) in {rel_path}")

    # Validate each server
    for server_name, server_config in servers.items():
        if not isinstance(server_config, dict):
            report.critical(f"Server '{server_name}' config must be an object")
            continue

        validate_lsp_server(server_name, server_config, report, plugin_root, rel_path)

    return report


def validate_plugin_lsp(
    plugin_root: Path,
    report: ValidationReport | None = None,
) -> ValidationReport:
    """Validate all LSP configurations in a plugin.

    Args:
        plugin_root: Path to the plugin root directory
        report: Optional existing report to add to

    Returns:
        ValidationReport with all validation results
    """
    if report is None:
        report = ValidationReport()

    # Check for common LSP config locations
    lsp_config_paths = [
        plugin_root / ".lsp.json",
        plugin_root / "lsp.json",
        plugin_root / "lsp-config.json",
        plugin_root / ".vscode" / "settings.json",
    ]

    found_any = False
    for config_path in lsp_config_paths:
        if config_path.exists():
            found_any = True
            validate_lsp_config(config_path, plugin_root, report)

    if not found_any:
        report.info("No LSP configuration files found")

    return report


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    colors = {
        "CRITICAL": "\033[91m",
        "MAJOR": "\033[93m",
        "MINOR": "\033[94m",
        "INFO": "\033[90m",
        "PASSED": "\033[92m",
        "RESET": "\033[0m",
    }

    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    print("\n" + "=" * 60)
    print("LSP Configuration Validation Report")
    print("=" * 60)

    print("\nSummary:")
    print(f"  {colors['CRITICAL']}CRITICAL: {counts['CRITICAL']}{colors['RESET']}")
    print(f"  {colors['MAJOR']}MAJOR:    {counts['MAJOR']}{colors['RESET']}")
    print(f"  {colors['MINOR']}MINOR:    {counts['MINOR']}{colors['RESET']}")
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
        print(f"{colors['PASSED']}✓ All LSP checks passed{colors['RESET']}")
    else:
        status_color = colors[["PASSED", "CRITICAL", "MAJOR", "MINOR"][report.exit_code]]
        print(f"{status_color}✗ Issues found{colors['RESET']}")

    print()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Validate LSP configuration")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all results")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to LSP config file or plugin directory",
    )
    args = parser.parse_args()

    # Determine path
    if args.path:
        path = Path(args.path)
    else:
        path = Path.cwd()

    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        return 1

    # Determine if it's a file or directory
    if path.is_file():
        report = validate_lsp_config(path, path.parent)
    else:
        report = validate_plugin_lsp(path)

    # Output
    if args.json:
        output = {
            "exit_code": report.exit_code,
            "counts": {
                "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
                "major": sum(1 for r in report.results if r.level == "MAJOR"),
                "minor": sum(1 for r in report.results if r.level == "MINOR"),
                "info": sum(1 for r in report.results if r.level == "INFO"),
                "passed": sum(1 for r in report.results if r.level == "PASSED"),
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
        print_results(report, args.verbose)

    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
