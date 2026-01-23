#!/usr/bin/env python3
"""
Marketplace Validator for Claude Code Plugins.

Validates marketplace configuration files (marketplace.json) according to
Claude Code marketplace specifications.

A marketplace is a collection of plugins that can be installed via:
  claude plugin install <plugin-name>@<marketplace-name>

Exit Codes:
  0 - All checks passed
  1 - Critical issues found (marketplace unusable)
  2 - Major issues found (some plugins may fail)
  3 - Minor issues only (warnings)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ValidationResult:
    """Result of a single validation check."""

    level: str  # "critical", "major", "minor", "info"
    category: str  # "structure", "manifest", "plugin", "config"
    message: str
    file_path: str | None = None
    line_number: int | None = None
    suggestion: str | None = None


@dataclass
class ValidationReport:
    """Complete validation report for a marketplace."""

    marketplace_path: Path
    marketplace_name: str | None = None
    results: list[ValidationResult] = field(default_factory=list)
    plugins_found: list[str] = field(default_factory=list)
    plugins_validated: int = 0
    plugins_failed: int = 0

    def add(self, result: ValidationResult) -> None:
        """Add a validation result."""
        self.results.append(result)

    def has_critical(self) -> bool:
        """Check if there are critical issues."""
        return any(r.level == "critical" for r in self.results)

    def has_major(self) -> bool:
        """Check if there are major issues."""
        return any(r.level == "major" for r in self.results)

    def has_minor(self) -> bool:
        """Check if there are minor issues."""
        return any(r.level == "minor" for r in self.results)

    def exit_code(self) -> int:
        """Return appropriate exit code based on results."""
        if self.has_critical():
            return 1
        if self.has_major():
            return 2
        if self.has_minor():
            return 3
        return 0


# =============================================================================
# Constants
# =============================================================================

# Valid source types for plugins in a marketplace
VALID_SOURCE_TYPES = {"git", "local", "npm", "url"}

# Required fields in marketplace.json
REQUIRED_MARKETPLACE_FIELDS = {"name", "plugins"}

# Required fields for each plugin entry
REQUIRED_PLUGIN_FIELDS = {"name"}

# Optional plugin fields
OPTIONAL_PLUGIN_FIELDS = {
    "version",
    "description",
    "source",
    "path",
    "repository",
    "author",
    "tags",
    "dependencies",
    "enabled",
}

# Source-specific required fields
SOURCE_REQUIRED_FIELDS = {
    "git": {"repository"},
    "local": {"path"},
    "npm": {"package"},
    "url": {"url"},
}

# Name validation pattern (kebab-case)
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Version pattern (semver-like)
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$")

# Required README sections for GitHub deployment
# These patterns match common section header formats (# Section, ## Section, ### Section)
REQUIRED_README_SECTIONS = {
    "installation": re.compile(r"^#{1,3}\s*installation", re.IGNORECASE | re.MULTILINE),
    "update": re.compile(r"^#{1,3}\s*(update|updating)", re.IGNORECASE | re.MULTILINE),
    "uninstall": re.compile(r"^#{1,3}\s*(uninstall|remove|removal)", re.IGNORECASE | re.MULTILINE),
    "troubleshooting": re.compile(r"^#{1,3}\s*troubleshooting", re.IGNORECASE | re.MULTILINE),
}

# Required installation sub-steps (should be present in Installation section)
REQUIRED_INSTALLATION_STEPS = {
    "add_marketplace": re.compile(
        r"(marketplace\s+add|add\s+.*marketplace|claude\s+plugin\s+marketplace\s+add)",
        re.IGNORECASE,
    ),
    "install_plugin": re.compile(
        r"(plugin\s+install|install\s+.*plugin|claude\s+plugin\s+install)",
        re.IGNORECASE,
    ),
    "verify": re.compile(r"(verify|check|confirm|list)", re.IGNORECASE),
    "restart": re.compile(r"(restart|reload|relaunch)", re.IGNORECASE),
}


# =============================================================================
# Validation Functions
# =============================================================================


def validate_marketplace_file(
    marketplace_path: Path,
) -> tuple[dict[str, Any] | None, list[ValidationResult]]:
    """
    Validate and load a marketplace.json file.

    Args:
        marketplace_path: Path to marketplace directory or marketplace.json file

    Returns:
        Tuple of (parsed JSON data or None, list of validation results)
    """
    results: list[ValidationResult] = []

    # Determine the marketplace.json location
    # Can be at root (marketplace.json) or in .claude-plugin/ subdirectory
    if marketplace_path.is_file():
        json_path = marketplace_path
        marketplace_dir = marketplace_path.parent
    else:
        # Try root first
        json_path = marketplace_path / "marketplace.json"
        marketplace_dir = marketplace_path
        # If not found, try .claude-plugin/ subdirectory
        if not json_path.exists():
            json_path = marketplace_path / ".claude-plugin" / "marketplace.json"

    # Check file exists
    if not json_path.exists():
        results.append(
            ValidationResult(
                level="critical",
                category="structure",
                message=f"Marketplace configuration not found: {json_path}",
                file_path=str(json_path),
                suggestion="Create a marketplace.json file with name and plugins fields",
            )
        )
        return None, results

    # Parse JSON
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        results.append(
            ValidationResult(
                level="critical",
                category="manifest",
                message=f"Invalid JSON in marketplace.json: {e}",
                file_path=str(json_path),
                line_number=e.lineno,
                suggestion="Fix JSON syntax error",
            )
        )
        return None, results
    except Exception as e:
        results.append(
            ValidationResult(
                level="critical",
                category="manifest",
                message=f"Error reading marketplace.json: {e}",
                file_path=str(json_path),
            )
        )
        return None, results

    # Check it's a dict
    if not isinstance(data, dict):
        results.append(
            ValidationResult(
                level="critical",
                category="manifest",
                message="marketplace.json must be a JSON object",
                file_path=str(json_path),
                suggestion="Root element should be a JSON object with name and plugins fields",
            )
        )
        return None, results

    # Store the directory for later use
    data["_marketplace_dir"] = str(marketplace_dir)
    data["_json_path"] = str(json_path)

    return data, results


def validate_marketplace_name(name: Any, json_path: str) -> list[ValidationResult]:
    """Validate the marketplace name field."""
    results: list[ValidationResult] = []

    if not isinstance(name, str):
        results.append(
            ValidationResult(
                level="critical",
                category="manifest",
                message=f"Marketplace name must be a string, got {type(name).__name__}",
                file_path=json_path,
            )
        )
        return results

    if not name:
        results.append(
            ValidationResult(
                level="critical",
                category="manifest",
                message="Marketplace name cannot be empty",
                file_path=json_path,
            )
        )
        return results

    # Warn if not kebab-case
    if not NAME_PATTERN.match(name):
        results.append(
            ValidationResult(
                level="minor",
                category="manifest",
                message=f"Marketplace name '{name}' should use kebab-case (lowercase with hyphens)",
                file_path=json_path,
                suggestion="Use format: my-marketplace-name",
            )
        )

    return results


def validate_plugin_entry(
    plugin: dict[str, Any],
    index: int,
    marketplace_dir: Path,
    json_path: str,
) -> list[ValidationResult]:
    """Validate a single plugin entry in the marketplace."""
    results: list[ValidationResult] = []
    plugin_id = plugin.get("name", f"plugins[{index}]")

    # Check required fields
    for field_name in REQUIRED_PLUGIN_FIELDS:
        if field_name not in plugin:
            results.append(
                ValidationResult(
                    level="critical",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' missing required field: {field_name}",
                    file_path=json_path,
                )
            )

    # Validate name format
    name = plugin.get("name")
    if isinstance(name, str) and name:
        if not NAME_PATTERN.match(name):
            results.append(
                ValidationResult(
                    level="minor",
                    category="plugin",
                    message=f"Plugin name '{name}' should use kebab-case",
                    file_path=json_path,
                    suggestion="Use format: my-plugin-name",
                )
            )

    # Validate version if present
    version = plugin.get("version")
    if version is not None:
        if not isinstance(version, str):
            results.append(
                ValidationResult(
                    level="major",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' version must be a string",
                    file_path=json_path,
                )
            )
        elif not VERSION_PATTERN.match(version):
            results.append(
                ValidationResult(
                    level="minor",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' version '{version}' should follow semver format",
                    file_path=json_path,
                    suggestion="Use format: X.Y.Z (e.g., 1.0.0)",
                )
            )

    # Validate source configuration
    source = plugin.get("source")
    if source is not None:
        results.extend(
            validate_plugin_source(plugin, plugin_id, marketplace_dir, json_path)
        )

    # Validate local path if present
    local_path = plugin.get("path")
    if local_path is not None:
        results.extend(
            validate_local_path(local_path, plugin_id, marketplace_dir, json_path)
        )

    # Validate repository URL if present
    repository = plugin.get("repository")
    if repository is not None:
        results.extend(validate_repository_url(repository, plugin_id, json_path))

    # Check for unknown fields
    known_fields = REQUIRED_PLUGIN_FIELDS | OPTIONAL_PLUGIN_FIELDS
    for field_name in plugin:
        if field_name not in known_fields:
            results.append(
                ValidationResult(
                    level="info",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' has unknown field: {field_name}",
                    file_path=json_path,
                )
            )

    # Validate tags if present
    tags = plugin.get("tags")
    if tags is not None:
        if not isinstance(tags, list):
            results.append(
                ValidationResult(
                    level="minor",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' tags must be an array",
                    file_path=json_path,
                )
            )
        elif not all(isinstance(t, str) for t in tags):
            results.append(
                ValidationResult(
                    level="minor",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' tags must be strings",
                    file_path=json_path,
                )
            )

    # Validate dependencies if present
    deps = plugin.get("dependencies")
    if deps is not None:
        if not isinstance(deps, list):
            results.append(
                ValidationResult(
                    level="major",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' dependencies must be an array",
                    file_path=json_path,
                )
            )
        elif not all(isinstance(d, str) for d in deps):
            results.append(
                ValidationResult(
                    level="major",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' dependencies must be strings",
                    file_path=json_path,
                )
            )

    return results


def validate_plugin_source(
    plugin: dict[str, Any],
    plugin_id: str,
    _marketplace_dir: Path,  # Reserved for future source path validation
    json_path: str,
) -> list[ValidationResult]:
    """Validate the source configuration for a plugin."""
    results: list[ValidationResult] = []
    source = plugin.get("source")

    if not isinstance(source, dict):
        # Source can also be a string shorthand
        if isinstance(source, str):
            # Accept relative paths (./path or ../path) as local source
            if source.startswith("./") or source.startswith("../"):
                pass  # Valid local path shorthand
            elif source not in VALID_SOURCE_TYPES:
                results.append(
                    ValidationResult(
                        level="major",
                        category="plugin",
                        message=f"Plugin '{plugin_id}' has invalid source type: {source}",
                        file_path=json_path,
                        suggestion=f"Valid source types: {', '.join(sorted(VALID_SOURCE_TYPES))} or relative path (./path)",
                    )
                )
        else:
            results.append(
                ValidationResult(
                    level="major",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' source must be a string or object",
                    file_path=json_path,
                )
            )
        return results

    # Validate source type
    source_type = source.get("type")
    if source_type is None:
        results.append(
            ValidationResult(
                level="major",
                category="plugin",
                message=f"Plugin '{plugin_id}' source missing 'type' field",
                file_path=json_path,
                suggestion=f"Add type: {', '.join(sorted(VALID_SOURCE_TYPES))}",
            )
        )
    elif source_type not in VALID_SOURCE_TYPES:
        results.append(
            ValidationResult(
                level="major",
                category="plugin",
                message=f"Plugin '{plugin_id}' has invalid source type: {source_type}",
                file_path=json_path,
                suggestion=f"Valid source types: {', '.join(sorted(VALID_SOURCE_TYPES))}",
            )
        )
    else:
        # Check source-specific required fields
        required = SOURCE_REQUIRED_FIELDS.get(source_type, set())
        for field_name in required:
            if field_name not in source and field_name not in plugin:
                results.append(
                    ValidationResult(
                        level="major",
                        category="plugin",
                        message=f"Plugin '{plugin_id}' with source type '{source_type}' requires '{field_name}'",
                        file_path=json_path,
                    )
                )

    return results


def validate_local_path(
    local_path: Any,
    plugin_id: str,
    marketplace_dir: Path,
    json_path: str,
) -> list[ValidationResult]:
    """Validate a local file path for a plugin."""
    results: list[ValidationResult] = []

    if not isinstance(local_path, str):
        results.append(
            ValidationResult(
                level="major",
                category="plugin",
                message=f"Plugin '{plugin_id}' path must be a string",
                file_path=json_path,
            )
        )
        return results

    # Resolve the path
    if local_path.startswith("/"):
        # Absolute path
        resolved = Path(local_path)
    else:
        # Relative to marketplace directory
        resolved = marketplace_dir / local_path

    # Check path exists
    if not resolved.exists():
        results.append(
            ValidationResult(
                level="major",
                category="plugin",
                message=f"Plugin '{plugin_id}' local path does not exist: {resolved}",
                file_path=json_path,
                suggestion="Ensure the path is relative to the marketplace directory or use absolute path",
            )
        )
    elif not resolved.is_dir():
        results.append(
            ValidationResult(
                level="major",
                category="plugin",
                message=f"Plugin '{plugin_id}' local path is not a directory: {resolved}",
                file_path=json_path,
            )
        )
    else:
        # Check for plugin.json in the plugin directory
        plugin_json = resolved / ".claude-plugin" / "plugin.json"
        if not plugin_json.exists():
            # Also check root plugin.json (legacy)
            alt_plugin_json = resolved / "plugin.json"
            if not alt_plugin_json.exists():
                results.append(
                    ValidationResult(
                        level="major",
                        category="plugin",
                        message=f"Plugin '{plugin_id}' directory missing plugin.json",
                        file_path=str(resolved),
                        suggestion="Add .claude-plugin/plugin.json to the plugin directory",
                    )
                )

    # Check for path traversal
    if ".." in local_path:
        results.append(
            ValidationResult(
                level="minor",
                category="plugin",
                message=f"Plugin '{plugin_id}' path contains '..' (path traversal)",
                file_path=json_path,
                suggestion="Use absolute paths or paths without parent directory references",
            )
        )

    return results


def validate_repository_url(
    repository: Any,
    plugin_id: str,
    json_path: str,
) -> list[ValidationResult]:
    """Validate a repository URL."""
    results: list[ValidationResult] = []

    if not isinstance(repository, str):
        results.append(
            ValidationResult(
                level="minor",
                category="plugin",
                message=f"Plugin '{plugin_id}' repository must be a string",
                file_path=json_path,
            )
        )
        return results

    # Try to parse as URL
    try:
        parsed = urlparse(repository)
        if not parsed.scheme:
            # Could be a GitHub shorthand (owner/repo)
            if "/" in repository and not repository.startswith("."):
                pass  # Valid shorthand
            else:
                results.append(
                    ValidationResult(
                        level="minor",
                        category="plugin",
                        message=f"Plugin '{plugin_id}' repository URL may be invalid: {repository}",
                        file_path=json_path,
                        suggestion="Use full URL or GitHub shorthand (owner/repo)",
                    )
                )
        elif parsed.scheme not in ("http", "https", "git", "ssh"):
            results.append(
                ValidationResult(
                    level="minor",
                    category="plugin",
                    message=f"Plugin '{plugin_id}' repository has unusual scheme: {parsed.scheme}",
                    file_path=json_path,
                )
            )
    except Exception:
        results.append(
            ValidationResult(
                level="minor",
                category="plugin",
                message=f"Plugin '{plugin_id}' repository URL could not be parsed",
                file_path=json_path,
            )
        )

    return results


def validate_plugins_array(
    plugins: Any,
    marketplace_dir: Path,
    json_path: str,
) -> tuple[list[str], list[ValidationResult]]:
    """Validate the plugins array in marketplace.json."""
    results: list[ValidationResult] = []
    plugin_names: list[str] = []

    if not isinstance(plugins, list):
        results.append(
            ValidationResult(
                level="critical",
                category="manifest",
                message="plugins field must be an array",
                file_path=json_path,
                suggestion="plugins: [{name: 'plugin-a'}, {name: 'plugin-b'}]",
            )
        )
        return plugin_names, results

    if len(plugins) == 0:
        results.append(
            ValidationResult(
                level="minor",
                category="manifest",
                message="plugins array is empty",
                file_path=json_path,
            )
        )
        return plugin_names, results

    # Validate each plugin
    seen_names: set[str] = set()
    for i, plugin in enumerate(plugins):
        if not isinstance(plugin, dict):
            results.append(
                ValidationResult(
                    level="critical",
                    category="plugin",
                    message=f"plugins[{i}] must be an object, got {type(plugin).__name__}",
                    file_path=json_path,
                )
            )
            continue

        # Track plugin name
        name = plugin.get("name")
        if isinstance(name, str):
            plugin_names.append(name)

            # Check for duplicates
            if name in seen_names:
                results.append(
                    ValidationResult(
                        level="major",
                        category="plugin",
                        message=f"Duplicate plugin name: {name}",
                        file_path=json_path,
                        suggestion="Each plugin must have a unique name",
                    )
                )
            seen_names.add(name)

        # Validate the plugin entry
        results.extend(validate_plugin_entry(plugin, i, marketplace_dir, json_path))

    return plugin_names, results


def validate_github_deployment(
    marketplace_dir: Path,
    plugins: list[dict[str, Any]],
) -> list[ValidationResult]:
    """
    Validate GitHub deployment structure for a marketplace.

    Checks:
    - Main README.md exists at marketplace root
    - README.md has required sections (Installation, Update, Uninstall, Troubleshooting)
    - Installation section has all required steps
    - Each plugin subfolder has its own README.md

    Args:
        marketplace_dir: Path to marketplace directory
        plugins: List of plugin entries from marketplace.json

    Returns:
        List of validation results
    """
    results: list[ValidationResult] = []

    # Check main README.md exists
    readme_path = marketplace_dir / "README.md"
    if not readme_path.exists():
        # Also check lowercase
        readme_path = marketplace_dir / "readme.md"

    if not readme_path.exists():
        results.append(
            ValidationResult(
                level="major",
                category="deployment",
                message="Missing README.md at marketplace root",
                file_path=str(marketplace_dir),
                suggestion="Create a README.md with installation instructions for users",
            )
        )
    else:
        # Validate README content
        results.extend(validate_readme_content(readme_path))

    # Check each plugin subfolder has README.md
    for plugin in plugins:
        source = plugin.get("source")
        plugin_name = plugin.get("name", "unknown")

        # Determine plugin path
        plugin_path: Path | None = None
        if isinstance(source, str) and source.startswith("./"):
            plugin_path = marketplace_dir / source[2:]
        elif isinstance(source, str) and not source.startswith(("http", "git@")):
            plugin_path = marketplace_dir / source
        elif "path" in plugin:
            path_val = plugin["path"]
            if isinstance(path_val, str):
                if path_val.startswith("./"):
                    plugin_path = marketplace_dir / path_val[2:]
                elif not path_val.startswith("/"):
                    plugin_path = marketplace_dir / path_val

        if plugin_path and plugin_path.exists() and plugin_path.is_dir():
            plugin_readme = plugin_path / "README.md"
            if not plugin_readme.exists():
                plugin_readme = plugin_path / "readme.md"

            if not plugin_readme.exists():
                results.append(
                    ValidationResult(
                        level="minor",
                        category="deployment",
                        message=f"Plugin '{plugin_name}' subfolder missing README.md",
                        file_path=str(plugin_path),
                        suggestion="Add README.md to plugin subfolder describing the plugin",
                    )
                )

    return results


def validate_readme_content(readme_path: Path) -> list[ValidationResult]:
    """
    Validate README.md has required sections for marketplace deployment.

    Args:
        readme_path: Path to README.md file

    Returns:
        List of validation results
    """
    results: list[ValidationResult] = []

    try:
        content = readme_path.read_text(encoding="utf-8")
    except Exception as e:
        results.append(
            ValidationResult(
                level="major",
                category="deployment",
                message=f"Could not read README.md: {e}",
                file_path=str(readme_path),
            )
        )
        return results

    # Check for required sections
    missing_sections: list[str] = []
    for section_name, pattern in REQUIRED_README_SECTIONS.items():
        if not pattern.search(content):
            missing_sections.append(section_name)

    if missing_sections:
        results.append(
            ValidationResult(
                level="major",
                category="deployment",
                message=f"README.md missing required sections: {', '.join(missing_sections)}",
                file_path=str(readme_path),
                suggestion="Add sections: ## Installation, ## Update, ## Uninstall, ## Troubleshooting",
            )
        )

    # Check installation section has required steps
    if "installation" not in missing_sections:
        missing_steps: list[str] = []
        for step_name, pattern in REQUIRED_INSTALLATION_STEPS.items():
            if not pattern.search(content):
                missing_steps.append(step_name.replace("_", " "))

        if missing_steps:
            results.append(
                ValidationResult(
                    level="minor",
                    category="deployment",
                    message=f"README.md Installation section may be incomplete. Missing: {', '.join(missing_steps)}",
                    file_path=str(readme_path),
                    suggestion="Include steps for: add marketplace, install plugin, verify installation, restart Claude Code",
                )
            )

    # Check for placeholder content
    placeholder_patterns = [
        r"\[TODO\]",
        r"\[INSERT",
        r"<your-",
        r"PLACEHOLDER",
        r"TBD",
    ]
    for placeholder_pattern in placeholder_patterns:
        if re.search(placeholder_pattern, content, re.IGNORECASE):
            results.append(
                ValidationResult(
                    level="minor",
                    category="deployment",
                    message="README.md contains placeholder content",
                    file_path=str(readme_path),
                    suggestion="Replace all placeholders with actual content before publishing",
                )
            )
            break

    return results


def validate_marketplace(marketplace_path: Path) -> ValidationReport:
    """
    Validate a complete marketplace configuration.

    Args:
        marketplace_path: Path to marketplace directory or marketplace.json

    Returns:
        ValidationReport with all findings
    """
    report = ValidationReport(marketplace_path=marketplace_path)

    # Load and validate the marketplace.json file
    data, load_results = validate_marketplace_file(marketplace_path)
    report.results.extend(load_results)

    if data is None:
        return report

    json_path = data.get("_json_path", str(marketplace_path))
    marketplace_dir = Path(data.get("_marketplace_dir", marketplace_path))

    # Check required fields
    for field_name in REQUIRED_MARKETPLACE_FIELDS:
        if field_name not in data:
            report.add(
                ValidationResult(
                    level="critical",
                    category="manifest",
                    message=f"Missing required field: {field_name}",
                    file_path=json_path,
                )
            )

    # Validate name
    name = data.get("name")
    if name is not None:
        report.marketplace_name = name if isinstance(name, str) else None
        report.results.extend(validate_marketplace_name(name, json_path))

    # Validate plugins
    plugins = data.get("plugins")
    if plugins is not None:
        plugin_names, plugin_results = validate_plugins_array(
            plugins, marketplace_dir, json_path
        )
        report.plugins_found = plugin_names
        report.results.extend(plugin_results)

        # Validate GitHub deployment structure
        if isinstance(plugins, list):
            deployment_results = validate_github_deployment(
                marketplace_dir, plugins
            )
            report.results.extend(deployment_results)

    # Validate optional fields
    if "description" in data and not isinstance(data["description"], str):
        report.add(
            ValidationResult(
                level="minor",
                category="manifest",
                message="description field must be a string",
                file_path=json_path,
            )
        )

    if "version" in data:
        version = data["version"]
        if not isinstance(version, str):
            report.add(
                ValidationResult(
                    level="minor",
                    category="manifest",
                    message="version field must be a string",
                    file_path=json_path,
                )
            )
        elif not VERSION_PATTERN.match(version):
            report.add(
                ValidationResult(
                    level="minor",
                    category="manifest",
                    message=f"Marketplace version '{version}' should follow semver format",
                    file_path=json_path,
                )
            )

    return report


# =============================================================================
# CLI Interface
# =============================================================================


def format_report(report: ValidationReport, verbose: bool = False) -> str:
    """Format the validation report for display."""
    lines: list[str] = []

    # Header
    lines.append("=" * 60)
    lines.append("Marketplace Validation Report")
    lines.append("=" * 60)
    lines.append(f"Path: {report.marketplace_path}")
    if report.marketplace_name:
        lines.append(f"Name: {report.marketplace_name}")
    lines.append(f"Plugins Found: {len(report.plugins_found)}")
    if report.plugins_found:
        lines.append(f"  - {', '.join(report.plugins_found)}")
    lines.append("")

    # Group results by level
    critical = [r for r in report.results if r.level == "critical"]
    major = [r for r in report.results if r.level == "major"]
    minor = [r for r in report.results if r.level == "minor"]
    info = [r for r in report.results if r.level == "info"]

    # Summary
    lines.append(f"Critical Issues: {len(critical)}")
    lines.append(f"Major Issues: {len(major)}")
    lines.append(f"Minor Issues: {len(minor)}")
    if verbose:
        lines.append(f"Info: {len(info)}")
    lines.append("")

    # Details
    def format_result(r: ValidationResult) -> list[str]:
        result_lines = [f"  [{r.level.upper()}] [{r.category}] {r.message}"]
        if r.file_path:
            loc = r.file_path
            if r.line_number:
                loc += f":{r.line_number}"
            result_lines.append(f"    Location: {loc}")
        if r.suggestion:
            result_lines.append(f"    Suggestion: {r.suggestion}")
        return result_lines

    if critical:
        lines.append("--- CRITICAL ISSUES ---")
        for r in critical:
            lines.extend(format_result(r))
        lines.append("")

    if major:
        lines.append("--- MAJOR ISSUES ---")
        for r in major:
            lines.extend(format_result(r))
        lines.append("")

    if minor:
        lines.append("--- MINOR ISSUES ---")
        for r in minor:
            lines.extend(format_result(r))
        lines.append("")

    if verbose and info:
        lines.append("--- INFO ---")
        for r in info:
            lines.extend(format_result(r))
        lines.append("")

    # Final status
    lines.append("=" * 60)
    if report.has_critical():
        lines.append("RESULT: FAILED (critical issues found)")
    elif report.has_major():
        lines.append("RESULT: FAILED (major issues found)")
    elif report.has_minor():
        lines.append("RESULT: PASSED with warnings")
    else:
        lines.append("RESULT: PASSED")
    lines.append("=" * 60)

    return "\n".join(lines)


def main() -> int:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="Validate Claude Code plugin marketplace configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit codes:
  0 - All checks passed
  1 - Critical issues found
  2 - Major issues found
  3 - Minor issues only

Examples:
  %(prog)s ./my-marketplace
  %(prog)s ./my-marketplace/marketplace.json --verbose
  %(prog)s ./my-marketplace --json
        """,
    )
    parser.add_argument(
        "marketplace_path",
        type=Path,
        help="Path to marketplace directory or marketplace.json file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show all issues including info level",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    # Run validation
    report = validate_marketplace(args.marketplace_path)

    # Output results
    if args.json:
        output = {
            "marketplace_path": str(report.marketplace_path),
            "marketplace_name": report.marketplace_name,
            "plugins_found": report.plugins_found,
            "results": [
                {
                    "level": r.level,
                    "category": r.category,
                    "message": r.message,
                    "file_path": r.file_path,
                    "line_number": r.line_number,
                    "suggestion": r.suggestion,
                }
                for r in report.results
            ],
            "summary": {
                "critical": sum(1 for r in report.results if r.level == "critical"),
                "major": sum(1 for r in report.results if r.level == "major"),
                "minor": sum(1 for r in report.results if r.level == "minor"),
                "info": sum(1 for r in report.results if r.level == "info"),
            },
            "exit_code": report.exit_code(),
        }
        print(json.dumps(output, indent=2))
    else:
        print(format_report(report, args.verbose))

    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
