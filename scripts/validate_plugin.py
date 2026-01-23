#!/usr/bin/env python3
"""
Claude Code Plugin Validator

Comprehensive validation suite for Claude Code plugins.
Validates structure, manifest, hooks, skills, scripts, and MCP servers.

Usage:
    uv run python scripts/validate_plugin.py /path/to/plugin
    uv run python scripts/validate_plugin.py --verbose
    uv run python scripts/validate_plugin.py --json
    uv run python scripts/validate_plugin.py --marketplace-only

Flags:
    --marketplace-only: Skip plugin.json requirement for marketplace-only
                        distribution (strict=false). When using strict=false,
                        plugin.json should NOT exist (causes CLI issues).

Exit codes:
    0 - All checks passed (or only INFO/PASSED)
    1 - CRITICAL issues found
    2 - MAJOR issues found
    3 - MINOR issues found
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from validate_hook import validate_hooks as validate_hook_file
from validate_mcp import validate_plugin_mcp

# Import component validators
from validate_skill import validate_skill as validate_skill_dir

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

    def minor(
        self, message: str, file: str | None = None, line: int | None = None
    ) -> None:
        """Add a minor issue."""
        self.add("MINOR", message, file, line)

    def major(
        self, message: str, file: str | None = None, line: int | None = None
    ) -> None:
        """Add a major issue."""
        self.add("MAJOR", message, file, line)

    def critical(
        self, message: str, file: str | None = None, line: int | None = None
    ) -> None:
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


def validate_manifest(
    plugin_root: Path, report: ValidationReport, marketplace_only: bool = False
) -> dict[str, Any] | None:
    """Validate plugin.json manifest.

    Args:
        plugin_root: Path to the plugin directory
        report: ValidationReport to add results to
        marketplace_only: If True, skip plugin.json requirement

    Returns:
        The manifest dict if valid, None otherwise
    """
    manifest_path = plugin_root / ".claude-plugin" / "plugin.json"

    if not manifest_path.exists():
        if marketplace_only:
            msg = "plugin.json correctly absent (marketplace-only, strict=false)"
            report.passed(msg, ".claude-plugin/plugin.json")
            return None
        else:
            report.critical("plugin.json not found", ".claude-plugin/plugin.json")
            return None

    if marketplace_only:
        report.major(
            "plugin.json EXISTS but should NOT for marketplace-only (strict=false). "
            "Remove .claude-plugin/plugin.json to fix CLI uninstall issues.",
            ".claude-plugin/plugin.json",
        )
        return None

    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        report.critical(
            f"Invalid JSON in plugin.json: {e}", ".claude-plugin/plugin.json"
        )
        return None

    report.passed("plugin.json is valid JSON", ".claude-plugin/plugin.json")

    # Required field: name (per Anthropic docs, ONLY 'name' is required)
    if "name" not in manifest:
        report.critical(
            "Missing required field 'name' in plugin.json",
            ".claude-plugin/plugin.json",
        )
    else:
        report.passed("Required field 'name' present", ".claude-plugin/plugin.json")

    # Recommended fields
    recommended_fields = ["version", "description"]
    for fld in recommended_fields:
        if fld not in manifest:
            report.minor(
                f"Missing recommended field '{fld}' in plugin.json",
                ".claude-plugin/plugin.json",
            )
        else:
            report.passed(
                f"Recommended field '{fld}' present",
                ".claude-plugin/plugin.json",
            )

    # Name validation
    if "name" in manifest:
        name = manifest["name"]
        if name != name.lower():
            report.major(
                f"Plugin name must be lowercase: {name}", ".claude-plugin/plugin.json"
            )
        if " " in name:
            report.major(
                f"Plugin name cannot contain spaces: {name}",
                ".claude-plugin/plugin.json",
            )
        if not re.match(r"^[a-z][a-z0-9-]*$", name):
            report.major(
                f"Plugin name must be kebab-case: {name}", ".claude-plugin/plugin.json"
            )

    # Version validation
    if "version" in manifest:
        version = manifest["version"]
        if not re.match(r"^\d+\.\d+\.\d+", version):
            report.major(
                f"Version must be semver format: {version}",
                ".claude-plugin/plugin.json",
            )

    # Check for unknown fields
    known_fields = {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "commands",
        "agents",
        "skills",
        "hooks",
        "mcpServers",
        "outputStyles",
        "lspServers",
    }
    for key in manifest.keys():
        if key not in known_fields:
            report.info(
                f"Unknown manifest field '{key}' (may be ignored by CLI)",
                ".claude-plugin/plugin.json",
            )

    # Validate component path fields start with ./
    path_fields = [
        "commands",
        "agents",
        "skills",
        "hooks",
        "mcpServers",
        "outputStyles",
        "lspServers",
    ]
    for key in path_fields:
        if key in manifest:
            value = manifest[key]
            if isinstance(value, str) and not value.startswith("./"):
                report.major(
                    f"Field '{key}' path must start with './': {value}",
                    ".claude-plugin/plugin.json",
                )
            elif isinstance(value, list):
                for i, path in enumerate(value):
                    if isinstance(path, str) and not path.startswith("./"):
                        report.major(
                            f"Field '{key}[{i}]' path must start with './': {path}",
                            ".claude-plugin/plugin.json",
                        )

    return cast(dict[str, Any], manifest)


def validate_structure(
    plugin_root: Path, report: ValidationReport, marketplace_only: bool = False
) -> None:
    """Validate plugin directory structure.

    Args:
        plugin_root: Path to the plugin directory
        report: ValidationReport to add results to
        marketplace_only: If True, .claude-plugin directory is optional
    """
    claude_plugin_dir = plugin_root / ".claude-plugin"
    if not claude_plugin_dir.is_dir():
        if marketplace_only:
            msg = ".claude-plugin absent (marketplace-only, uses marketplace.json)"
            report.passed(msg)
        else:
            report.critical(".claude-plugin directory not found")
            return
    else:
        report.passed(".claude-plugin directory exists")

    # Components must be at root, NOT in .claude-plugin
    for component in ["commands", "agents", "skills", "hooks", "schemas", "bin"]:
        wrong_path = plugin_root / ".claude-plugin" / component
        if wrong_path.exists():
            report.critical(
                f"{component}/ must be at plugin root, not in .claude-plugin/"
            )

    # Common directories
    common_dirs = {
        "commands": "INFO",
        "agents": "INFO",
        "skills": "INFO",
        "hooks": "INFO",
        "scripts": "INFO",
        "docs": "INFO",
    }

    for d, level in common_dirs.items():
        if (plugin_root / d).is_dir():
            report.passed(f"{d}/ directory exists")
        else:
            if level == "INFO":
                report.info(f"Optional directory {d}/ not found")
            else:
                report.minor(f"Directory {d}/ not found")


def validate_commands(plugin_root: Path, report: ValidationReport) -> None:
    """Validate command definitions."""
    commands_dir = plugin_root / "commands"

    if not commands_dir.is_dir():
        report.info("No commands/ directory found")
        return

    # Find all command files
    cmd_files = list(commands_dir.glob("*.md"))
    if not cmd_files:
        report.info("No command files (*.md) found in commands/")
        return

    report.info(f"Found {len(cmd_files)} command file(s)")

    for cmd_path in cmd_files:
        validate_command_file(cmd_path, report)


def validate_command_file(cmd_path: Path, report: ValidationReport) -> None:
    """Validate a single command file."""
    rel_path = f"commands/{cmd_path.name}"
    content = cmd_path.read_text()

    # Check frontmatter
    if not content.startswith("---"):
        report.critical("No frontmatter in command file", rel_path)
        return

    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            report.critical("Malformed frontmatter (missing closing ---)", rel_path)
            return

        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        report.critical(f"Invalid YAML frontmatter: {e}", rel_path)
        return

    if not frontmatter:
        report.critical("Empty frontmatter", rel_path)
        return

    report.passed("Valid YAML frontmatter", rel_path)

    # Required fields
    if "name" not in frontmatter:
        report.critical("Missing 'name' in frontmatter", rel_path)
    else:
        expected_name = cmd_path.stem
        if frontmatter["name"] != expected_name:
            report.major(
                f"Command name '{frontmatter['name']}' doesn't match "
                f"filename '{expected_name}'",
                rel_path,
            )

    if "description" not in frontmatter:
        report.major("Missing 'description' in frontmatter", rel_path)


def validate_agents(plugin_root: Path, report: ValidationReport) -> None:
    """Validate agent definitions."""
    agents_dir = plugin_root / "agents"

    if not agents_dir.is_dir():
        report.info("No agents/ directory found")
        return

    # Find all agent files
    agent_files = list(agents_dir.glob("*.md"))
    if not agent_files:
        report.info("No agent files (*.md) found in agents/")
        return

    report.info(f"Found {len(agent_files)} agent file(s)")

    for agent_path in agent_files:
        validate_agent_file(agent_path, report)


def validate_agent_file(agent_path: Path, report: ValidationReport) -> None:
    """Validate a single agent file."""
    rel_path = f"agents/{agent_path.name}"
    content = agent_path.read_text()

    # Check frontmatter
    if not content.startswith("---"):
        report.critical("No frontmatter in agent file", rel_path)
        return

    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            report.critical("Malformed frontmatter (missing closing ---)", rel_path)
            return

        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        report.critical(f"Invalid YAML frontmatter: {e}", rel_path)
        return

    if not frontmatter:
        report.critical("Empty frontmatter", rel_path)
        return

    report.passed("Valid YAML frontmatter", rel_path)

    # Required fields for agents
    if "name" not in frontmatter:
        report.critical("Missing 'name' in frontmatter", rel_path)

    if "description" not in frontmatter:
        report.major("Missing 'description' in frontmatter", rel_path)


def validate_hooks(plugin_root: Path, report: ValidationReport) -> None:
    """Validate hook configuration using comprehensive hook validator."""
    hooks_dir = plugin_root / "hooks"

    if not hooks_dir.is_dir():
        report.info("No hooks/ directory found")
        return

    hooks_json = hooks_dir / "hooks.json"
    if not hooks_json.exists():
        report.info("No hooks.json found")
        return

    # Use comprehensive hook validator
    hook_report = validate_hook_file(hooks_json, plugin_root)

    # Transfer all results to main report
    for result in hook_report.results:
        file_path = result.file
        if file_path:
            if file_path.startswith(str(plugin_root)):
                file_path = file_path[len(str(plugin_root)) + 1 :]
            if not file_path.startswith("hooks/"):
                file_path = f"hooks/{file_path}"
        else:
            file_path = "hooks/hooks.json"

        report.add(result.level, result.message, file_path, result.line)


def validate_mcp(plugin_root: Path, report: ValidationReport) -> None:
    """Validate MCP server configurations."""
    # Use comprehensive MCP validator
    mcp_report = validate_plugin_mcp(plugin_root)

    # Transfer all results to main report
    for result in mcp_report.results:
        report.add(result.level, result.message, result.file, result.line)


def validate_scripts(plugin_root: Path, report: ValidationReport) -> None:
    """Validate Python and shell scripts."""
    scripts_dir = plugin_root / "scripts"

    if not scripts_dir.is_dir():
        report.info("No scripts/ directory found")
        return

    # Python scripts
    py_files = list(scripts_dir.glob("*.py"))
    if py_files:
        # Ruff check - exclude E501 (line length) as it's configurable per project
        try:
            ruff_args = ["ruff", "check", "--select", "E,F,W", "--ignore", "E501"]
            # If pyproject.toml exists in plugin root, use it for config
            pyproject = plugin_root / "pyproject.toml"
            if pyproject.exists():
                ruff_args.extend(["--config", str(pyproject)])
            ruff_args.extend([str(f) for f in py_files])
            result = subprocess.run(
                ruff_args,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                report.passed(f"Ruff check passed for {len(py_files)} Python files")
            else:
                for line in result.stdout.strip().split("\n"):
                    if line:
                        report.major(f"Ruff: {line}")
        except FileNotFoundError:
            report.info("ruff not available, skipping Python lint check")

        # Mypy check
        try:
            mypy_args = ["mypy", "--ignore-missing-imports"]
            # If pyproject.toml exists in plugin root, use it for config
            pyproject = plugin_root / "pyproject.toml"
            if pyproject.exists():
                mypy_args.extend(["--config-file", str(pyproject)])
            mypy_args.extend([str(f) for f in py_files])
            result = subprocess.run(
                mypy_args,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                report.passed(f"Mypy check passed for {len(py_files)} Python files")
            else:
                for line in result.stdout.strip().split("\n"):
                    if line and not line.startswith("Success"):
                        report.minor(f"Mypy: {line}")
        except FileNotFoundError:
            report.info("mypy not available, skipping type check")

    # Shell scripts
    sh_files = list(scripts_dir.glob("*.sh"))
    for sh_file in sh_files:
        if not os.access(sh_file, os.X_OK):
            report.major(
                f"Shell script not executable: {sh_file.name}",
                f"scripts/{sh_file.name}",
            )
        else:
            report.passed(
                f"Shell script executable: {sh_file.name}", f"scripts/{sh_file.name}"
            )

        # Shellcheck
        try:
            result = subprocess.run(
                ["shellcheck", str(sh_file)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                report.passed(f"Shellcheck passed: {sh_file.name}")
            else:
                report.minor(
                    f"Shellcheck issues in {sh_file.name}", f"scripts/{sh_file.name}"
                )
        except FileNotFoundError:
            report.info("shellcheck not available, skipping shell lint")


def validate_skills(plugin_root: Path, report: ValidationReport) -> None:
    """Validate all skills in the plugin's skills/ directory."""
    skills_dir = plugin_root / "skills"

    if not skills_dir.is_dir():
        report.info("No skills/ directory found")
        return

    # Find all skill directories
    skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir()]

    if not skill_dirs:
        report.info("No skill directories found in skills/")
        return

    report.info(f"Found {len(skill_dirs)} skill(s) to validate")

    # Validate each skill
    for skill_dir in sorted(skill_dirs):
        skill_name = skill_dir.name
        skill_report = validate_skill_dir(skill_dir)

        # Transfer results to main report
        for result in skill_report.results:
            file_path = f"skills/{skill_name}/{result.file}" if result.file else None
            report.add(result.level, result.message, file_path, result.line)


def validate_readme(plugin_root: Path, report: ValidationReport) -> None:
    """Validate README.md exists."""
    readme = plugin_root / "README.md"
    if readme.exists():
        report.passed("README.md found")
    else:
        report.minor("README.md not found")


def validate_license(plugin_root: Path, report: ValidationReport) -> None:
    """Validate LICENSE file exists."""
    for license_name in ["LICENSE", "LICENSE.md", "LICENSE.txt"]:
        if (plugin_root / license_name).exists():
            report.passed(f"{license_name} found")
            return

    report.minor("No LICENSE file found")


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
    print("Plugin Validation Report")
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
        print(f"{colors['PASSED']}✓ All checks passed{colors['RESET']}")
    elif report.exit_code == 1:
        print(
            f"{colors['CRITICAL']}✗ CRITICAL issues found - "
            f"plugin will not work{colors['RESET']}"
        )
    elif report.exit_code == 2:
        print(
            f"{colors['MAJOR']}✗ MAJOR issues found - "
            f"significant problems{colors['RESET']}"
        )
    else:
        print(f"{colors['MINOR']}! MINOR issues found - may affect UX{colors['RESET']}")

    print()


def print_json(report: ValidationReport) -> None:
    """Print validation results as JSON."""
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
            {"level": r.level, "message": r.message, "file": r.file, "line": r.line}
            for r in report.results
        ],
    }
    print(json.dumps(output, indent=2))


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Validate Claude Code plugin")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all results including passed checks",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--marketplace-only",
        action="store_true",
        help="Skip plugin.json requirement (for strict=false marketplace distribution)",
    )
    parser.add_argument(
        "path", nargs="?", help="Plugin root path (default: parent of scripts/)"
    )
    args = parser.parse_args()

    # Determine plugin root
    if args.path:
        plugin_root = Path(args.path)
    else:
        plugin_root = Path(__file__).parent.parent

    if not plugin_root.is_dir():
        print(f"Error: {plugin_root} is not a directory", file=sys.stderr)
        return 1

    # Run validation
    report = ValidationReport()
    marketplace_only = args.marketplace_only

    validate_manifest(plugin_root, report, marketplace_only)
    validate_structure(plugin_root, report, marketplace_only)
    validate_commands(plugin_root, report)
    validate_agents(plugin_root, report)
    validate_hooks(plugin_root, report)
    validate_mcp(plugin_root, report)
    validate_scripts(plugin_root, report)
    validate_skills(plugin_root, report)
    validate_readme(plugin_root, report)
    validate_license(plugin_root, report)

    # Output
    if args.json:
        print_json(report)
    else:
        print_results(report, args.verbose)

    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
