#!/usr/bin/env python3
"""Audit and standardize a plugin repository to match CPV standards.

Compares an existing plugin repo against the standard file set and reports
gaps. With --fix, generates missing files without modifying existing ones.

Usage:
    uv run scripts/standardize_plugin.py <plugin-path>
    uv run scripts/standardize_plugin.py <plugin-path> --fix [--dry-run]
    uv run scripts/standardize_plugin.py <plugin-path> --report report.md
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from generate_plugin_repo import PluginParams
import sys
from pathlib import Path

# -- ANSI colors (disabled when NO_COLOR is set or stdout is not a tty) ------


def _colors_supported() -> bool:
    """Return True only when the terminal supports ANSI escape sequences."""
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_USE_COLOR = _colors_supported()

RED = "\033[0;31m" if _USE_COLOR else ""
GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[1;33m" if _USE_COLOR else ""
BLUE = "\033[0;34m" if _USE_COLOR else ""
CYAN = "\033[0;36m" if _USE_COLOR else ""
BOLD = "\033[1m" if _USE_COLOR else ""
DIM = "\033[2m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""

# =============================================================================
# CONSTANTS
# =============================================================================

# Standard file checklist: (relative_path, required, description)
# "required" means the file MUST exist for a valid plugin repo.
# plugin.json is checked separately — it's not our job to create manifests.
STANDARD_FILES: list[tuple[str, bool, str]] = [
    (".claude-plugin/plugin.json", True, "Plugin manifest"),
    ("pyproject.toml", False, "Python project configuration"),
    (".python-version", False, "Python version pin"),
    (".gitignore", False, "Git ignore rules"),
    ("README.md", False, "Project documentation"),
    ("cliff.toml", False, "git-cliff changelog config"),
    (".mega-linter.yml", False, "Mega-Linter configuration"),
    ("scripts/publish.py", False, "Publish pipeline script"),
    ("git-hooks/pre-push", False, "Pre-push quality gate hook"),
    (".github/workflows/ci.yml", False, "CI workflow"),
    (".github/workflows/release.yml", False, "Release workflow"),
    (".github/workflows/validate.yml", False, "Plugin validation workflow"),
    (".github/workflows/notify-marketplace.yml", False, "Marketplace notification workflow"),
]

# Required .gitignore entries that every plugin repo should have
REQUIRED_GITIGNORE_ENTRIES: list[str] = [
    ".claude/",
    ".tldr/",
    "llm_externalizer_output/",
    "*_dev/",
    "__pycache__/",
    ".venv/",
    ".env",
    "dist/",
    "build/",
    ".coverage",
    ".pytest_cache/",
    ".ruff_cache/",
    "node_modules/",
]

# README badge markers — patterns that indicate standard badges are present
README_BADGE_PATTERNS: list[tuple[str, str]] = [
    ("CI badge", "actions/workflows/ci.yml/badge.svg"),
    ("Version badge", "img.shields.io/badge/version-"),
    ("License badge", "img.shields.io/badge/license-"),
    ("Validation badge", "actions/workflows/validate.yml/badge.svg"),
]

# Standard component directories
COMPONENT_DIRS: list[str] = [
    ".claude-plugin",
    ".github/workflows",
    "agents",
    "commands",
    "git-hooks",
    "hooks",
    "scripts",
    "skills",
    "tests",
]


# =============================================================================
# AUDIT RESULT TYPES
# =============================================================================


class AuditItem:
    """Single audit finding with status and description."""

    def __init__(self, category: str, name: str, status: str, message: str) -> None:
        self.category = category  # e.g. "files", "gitignore", "badges", "dirs"
        self.name = name  # e.g. "pyproject.toml", ".claude/", "CI badge"
        self.status = status  # "PASS", "MISSING", "WARN"
        self.message = message  # Human-readable description

    def __repr__(self) -> str:
        return f"AuditItem({self.category}, {self.name}, {self.status})"


# =============================================================================
# AUDIT FUNCTIONS
# =============================================================================


def audit_standard_files(plugin_path: Path) -> list[AuditItem]:
    """Check which standard files exist in the plugin repo."""
    items: list[AuditItem] = []
    for rel_path, required, description in STANDARD_FILES:
        full_path = plugin_path / rel_path
        if full_path.exists():
            items.append(AuditItem("files", rel_path, "PASS", f"{description} exists"))
        else:
            # plugin.json is required but we don't generate it — special status
            status = "MISSING" if not required else "CRITICAL"
            items.append(AuditItem("files", rel_path, status, f"{description} is missing"))
    return items


def audit_component_dirs(plugin_path: Path) -> list[AuditItem]:
    """Check which standard component directories exist."""
    items: list[AuditItem] = []
    for dir_name in COMPONENT_DIRS:
        full_path = plugin_path / dir_name
        if full_path.is_dir():
            items.append(AuditItem("dirs", dir_name, "PASS", f"Directory {dir_name}/ exists"))
        else:
            items.append(AuditItem("dirs", dir_name, "MISSING", f"Directory {dir_name}/ is missing"))
    return items


def audit_gitignore(plugin_path: Path) -> list[AuditItem]:
    """Check .gitignore for required entries."""
    items: list[AuditItem] = []
    gitignore_path = plugin_path / ".gitignore"

    if not gitignore_path.exists():
        items.append(AuditItem("gitignore", ".gitignore", "MISSING", "No .gitignore file found"))
        return items

    content = gitignore_path.read_text(encoding="utf-8")
    # Parse active lines (strip comments and whitespace)
    active_lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            active_lines.append(stripped)

    for entry in REQUIRED_GITIGNORE_ENTRIES:
        # Check if the entry (or a pattern that covers it) is present
        found = any(entry in line or line == entry for line in active_lines)
        if found:
            items.append(AuditItem("gitignore", entry, "PASS", f"Entry '{entry}' present"))
        else:
            items.append(AuditItem("gitignore", entry, "WARN", f"Entry '{entry}' missing from .gitignore"))
    return items


def audit_readme_badges(plugin_path: Path) -> list[AuditItem]:
    """Check README.md for standard badge markers."""
    items: list[AuditItem] = []
    readme_path = plugin_path / "README.md"

    if not readme_path.exists():
        items.append(AuditItem("badges", "README.md", "MISSING", "No README.md file found"))
        return items

    content = readme_path.read_text(encoding="utf-8")
    for badge_name, pattern in README_BADGE_PATTERNS:
        if pattern in content:
            items.append(AuditItem("badges", badge_name, "PASS", f"{badge_name} found"))
        else:
            items.append(AuditItem("badges", badge_name, "WARN", f"{badge_name} not found in README.md"))
    return items


def audit_pyproject(plugin_path: Path) -> list[AuditItem]:
    """Check pyproject.toml exists and has key sections."""
    items: list[AuditItem] = []
    pyproject_path = plugin_path / "pyproject.toml"

    if not pyproject_path.exists():
        items.append(AuditItem("pyproject", "pyproject.toml", "MISSING", "No pyproject.toml found"))
        return items

    content = pyproject_path.read_text(encoding="utf-8")

    # Check for key sections
    checks = [
        ("[build-system]", "Build system configuration"),
        ("[project]", "Project metadata"),
        ("[tool.ruff]", "Ruff linter configuration"),
    ]
    for section, desc in checks:
        if section in content:
            items.append(AuditItem("pyproject", section, "PASS", f"{desc} present"))
        else:
            items.append(AuditItem("pyproject", section, "WARN", f"{desc} missing"))
    return items


def audit_python_version(plugin_path: Path) -> list[AuditItem]:
    """Check .python-version file exists."""
    items: list[AuditItem] = []
    pv_path = plugin_path / ".python-version"
    if pv_path.exists():
        ver = pv_path.read_text(encoding="utf-8").strip()
        items.append(AuditItem("python", ".python-version", "PASS", f"Python version pinned to {ver}"))
    else:
        items.append(AuditItem("python", ".python-version", "MISSING", "No .python-version file"))
    return items


# =============================================================================
# RUN FULL AUDIT
# =============================================================================


def run_audit(plugin_path: Path) -> list[AuditItem]:
    """Run all audit checks and return combined results."""
    results: list[AuditItem] = []
    results.extend(audit_standard_files(plugin_path))
    results.extend(audit_component_dirs(plugin_path))
    results.extend(audit_gitignore(plugin_path))
    results.extend(audit_readme_badges(plugin_path))
    results.extend(audit_pyproject(plugin_path))
    results.extend(audit_python_version(plugin_path))
    return results


# =============================================================================
# REPORTING
# =============================================================================


def print_audit_report(results: list[AuditItem], plugin_path: Path) -> None:
    """Print a formatted audit report to stdout."""
    print(f"\n{BOLD}CPV Standardization Audit{NC}")
    print(f"{DIM}Plugin: {plugin_path}{NC}\n")

    # Group by category
    categories: dict[str, list[AuditItem]] = {}
    for item in results:
        categories.setdefault(item.category, []).append(item)

    category_titles = {
        "files": "Standard Files",
        "dirs": "Component Directories",
        "gitignore": ".gitignore Entries",
        "badges": "README Badges",
        "pyproject": "pyproject.toml Sections",
        "python": "Python Version",
    }

    total_pass = 0
    total_issues = 0

    for cat_key, title in category_titles.items():
        items = categories.get(cat_key, [])
        if not items:
            continue

        print(f"  {BOLD}{title}{NC}")
        for item in items:
            if item.status == "PASS":
                icon = f"{GREEN}✓{NC}"
                total_pass += 1
            elif item.status == "CRITICAL":
                icon = f"{RED}✗{NC}"
                total_issues += 1
            elif item.status == "MISSING":
                icon = f"{YELLOW}✗{NC}"
                total_issues += 1
            else:  # WARN
                icon = f"{YELLOW}⚠{NC}"
                total_issues += 1
            print(f"    {icon} {item.message}")
        print()

    # Summary line
    total = total_pass + total_issues
    if total_issues == 0:
        print(f"  {GREEN}{BOLD}All {total} checks passed.{NC}\n")
    else:
        print(f"  {BOLD}Result:{NC} {GREEN}{total_pass} passed{NC}, {YELLOW}{total_issues} issues{NC} / {total} checks\n")


def save_report_to_file(results: list[AuditItem], plugin_path: Path, report_path: Path) -> None:
    """Save a plain-text audit report to a file."""
    lines: list[str] = []
    lines.append("CPV Standardization Audit Report")
    lines.append(f"Plugin: {plugin_path}")
    lines.append(f"{'=' * 60}")
    lines.append("")

    category_titles = {
        "files": "Standard Files",
        "dirs": "Component Directories",
        "gitignore": ".gitignore Entries",
        "badges": "README Badges",
        "pyproject": "pyproject.toml Sections",
        "python": "Python Version",
    }

    categories: dict[str, list[AuditItem]] = {}
    for item in results:
        categories.setdefault(item.category, []).append(item)

    for cat_key, title in category_titles.items():
        items = categories.get(cat_key, [])
        if not items:
            continue
        lines.append(f"## {title}")
        for item in items:
            status_icon = "PASS" if item.status == "PASS" else item.status
            lines.append(f"  [{status_icon}] {item.message}")
        lines.append("")

    total_pass = sum(1 for r in results if r.status == "PASS")
    total_issues = sum(1 for r in results if r.status != "PASS")
    lines.append(f"Summary: {total_pass} passed, {total_issues} issues / {total_pass + total_issues} checks")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  {BLUE}Report saved:{NC} {report_path}")


# =============================================================================
# FIX MODE — generate missing files from templates
# =============================================================================


def _read_plugin_json(plugin_path: Path) -> dict:
    """Read plugin.json and return parsed manifest, or empty dict if missing."""
    manifest_path = plugin_path / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        return {}
    result: dict = json.loads(manifest_path.read_text(encoding="utf-8"))
    return result


def _params_from_manifest(manifest: dict) -> PluginParams:
    """Build PluginParams from a plugin.json manifest dict.

    Falls back to sensible defaults for missing fields so that template
    generation always succeeds.
    """
    # Import PluginParams from sibling module
    from generate_plugin_repo import PluginParams

    author_obj = manifest.get("author", {})
    if isinstance(author_obj, str):
        author_name = author_obj
        author_email = ""
    else:
        author_name = author_obj.get("name", "Unknown")
        author_email = author_obj.get("email", "")

    return PluginParams(
        name=manifest.get("name", "unknown-plugin"),
        description=manifest.get("description", "A Claude Code plugin"),
        author=author_name,
        author_email=author_email,
        license=manifest.get("license", "MIT"),
        python_version="3.12",
        github_owner=_guess_github_owner(manifest),
        marketplace=manifest.get("marketplace", ""),
        version=manifest.get("version", "0.1.0"),
    )


def _guess_github_owner(manifest: dict) -> str:
    """Extract github owner from repository URL in manifest."""
    repo_url: str = manifest.get("repository", "") or manifest.get("homepage", "")
    if not repo_url:
        return ""
    # Parse github.com/<owner>/<repo> pattern
    parts = repo_url.rstrip("/").split("/")
    # URL like https://github.com/owner/repo → parts[-2] is owner
    if len(parts) >= 2 and "github.com" in repo_url:
        return parts[-2]
    return ""


# Map from standard file path to the gen_* function name in generate_plugin_repo
_FILE_TO_GENERATOR: dict[str, str] = {
    "pyproject.toml": "gen_pyproject_toml",
    ".python-version": "gen_python_version",
    ".gitignore": "gen_gitignore",
    "README.md": "gen_readme",
    "cliff.toml": "gen_cliff_toml",
    ".mega-linter.yml": "gen_mega_linter_yml",
    "scripts/publish.py": "gen_publish_py",
    "git-hooks/pre-push": "gen_pre_push_hook",
    ".github/workflows/ci.yml": "gen_ci_yml",
    ".github/workflows/release.yml": "gen_release_yml",
    ".github/workflows/validate.yml": "gen_validate_yml",
    ".github/workflows/notify-marketplace.yml": "gen_notify_marketplace_yml",
}

# Files that should have the executable bit set
_EXECUTABLE_FILES: set[str] = {
    "scripts/publish.py",
    "git-hooks/pre-push",
}


def fix_missing_files(plugin_path: Path, results: list[AuditItem], dry_run: bool = False, marketplace: str | None = None) -> list[str]:
    """Generate missing standard files using templates from generate_plugin_repo.

    Only creates files that do not already exist. Never overwrites existing files.
    If marketplace is provided (owner/repo), patches notify-marketplace.yml with the values.
    Returns list of created (or would-create in dry-run) file paths.
    """
    import importlib

    # Identify which standard files are missing
    missing_files: set[str] = set()
    for item in results:
        if item.category == "files" and item.status in ("MISSING",) and item.name in _FILE_TO_GENERATOR:
            missing_files.add(item.name)

    if not missing_files:
        print(f"  {GREEN}No fixable missing files.{NC}")
        return []

    # Add scripts/ to sys.path BEFORE importing generator modules
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Read plugin.json to populate template params
    manifest = _read_plugin_json(plugin_path)
    if not manifest:
        print(f"  {RED}Cannot fix: .claude-plugin/plugin.json not found.{NC}")
        print(f"  {DIM}The manifest is needed to populate template parameters.{NC}")
        return []

    params = _params_from_manifest(manifest)

    # Import generator functions from generate_plugin_repo
    gen_module = importlib.import_module("generate_plugin_repo")

    created: list[str] = []

    for rel_path in sorted(missing_files):
        gen_func_name = _FILE_TO_GENERATOR[rel_path]
        gen_func = getattr(gen_module, gen_func_name)

        # Some gen_* functions take no params (e.g. gen_cliff_toml)
        import inspect

        sig = inspect.signature(gen_func)
        if len(sig.parameters) == 0:
            content = gen_func()
        else:
            content = gen_func(params)

        file_path = plugin_path / rel_path
        is_executable = rel_path in _EXECUTABLE_FILES

        if dry_run:
            print(f"  {BLUE}[dry-run]{NC} Would create {file_path} ({len(content)} bytes)"
                  f"{' [exec]' if is_executable else ''}")
            created.append(str(file_path))
            continue

        # Create parent directories
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the file
        file_path.write_text(content, encoding="utf-8")

        # Set executable bit if needed
        if is_executable:
            file_path.chmod(file_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # Patch notify-marketplace.yml with marketplace owner/repo if provided
        if rel_path == ".github/workflows/notify-marketplace.yml" and marketplace:
            owner, repo = marketplace.split("/", 1)
            patched = file_path.read_text(encoding="utf-8")
            patched = patched.replace("MARKETPLACE_OWNER: ''", f"MARKETPLACE_OWNER: '{owner}'")
            patched = patched.replace("MARKETPLACE_REPO: 'my-plugins-marketplace'", f"MARKETPLACE_REPO: '{repo}'")
            file_path.write_text(patched, encoding="utf-8")

        print(f"  {GREEN}Created:{NC} {file_path}{' [exec]' if is_executable else ''}")
        created.append(str(file_path))

    # Also create missing component directories
    for item in results:
        if item.category == "dirs" and item.status == "MISSING":
            dir_path = plugin_path / item.name
            if dry_run:
                print(f"  {BLUE}[dry-run]{NC} Would create directory {dir_path}/")
            else:
                dir_path.mkdir(parents=True, exist_ok=True)
                print(f"  {GREEN}Created dir:{NC} {dir_path}/")
            created.append(str(dir_path) + "/")

    # Auto-add missing .gitignore entries when an existing .gitignore is present
    gitignore_path = plugin_path / ".gitignore"
    if not dry_run and gitignore_path.exists():
        content = gitignore_path.read_text(encoding="utf-8")
        missing = []
        for entry in REQUIRED_GITIGNORE_ENTRIES:
            if entry not in content:
                missing.append(entry)
        if missing:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                f.write("\n# Added by CPV standardize\n")
                for entry in missing:
                    f.write(f"{entry}\n")
            print(f"  {GREEN}Updated:{NC} .gitignore — added {len(missing)} missing entries")

    return created


# =============================================================================
# MAIN
# =============================================================================


def main() -> int:
    """Parse CLI arguments, run audit, optionally fix missing files."""
    parser = argparse.ArgumentParser(
        description="Audit and standardize a Claude Code plugin repo against CPV standards.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Audit only (report gaps)
  uv run scripts/standardize_plugin.py /path/to/plugin

  # Audit + fix missing files (never overwrites existing)
  uv run scripts/standardize_plugin.py /path/to/plugin --fix

  # Dry-run fix (show what would be created)
  uv run scripts/standardize_plugin.py /path/to/plugin --fix --dry-run

  # Save detailed report to file
  uv run scripts/standardize_plugin.py /path/to/plugin --report audit.md

  # Also run full CPV validation
  uv run scripts/standardize_plugin.py /path/to/plugin --validate
""",
    )
    parser.add_argument("plugin_path", type=Path, help="Path to the plugin repository root")
    parser.add_argument("--fix", action="store_true", help="Generate missing standard files from templates")
    parser.add_argument("--dry-run", action="store_true", help="Show what --fix would do without writing files")
    parser.add_argument("--report", type=Path, default=None, help="Save audit report to this file path")
    parser.add_argument("--marketplace", type=str, help="Marketplace owner/repo for notify-marketplace.yml (e.g., Emasoft/emasoft-plugins)")
    parser.add_argument("--validate", action="store_true", help="Also run validate_plugin.py for full validation")

    args = parser.parse_args()
    plugin_path: Path = args.plugin_path.resolve()

    # Validate plugin path exists
    if not plugin_path.is_dir():
        print(f"{RED}Error:{NC} Not a directory: {plugin_path}", file=sys.stderr)
        return 1

    # Check for plugin.json as a basic sanity check
    manifest_path = plugin_path / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        print(f"{YELLOW}Warning:{NC} No .claude-plugin/plugin.json found at {plugin_path}")
        print(f"{DIM}This may not be a Claude Code plugin repository.{NC}")
        print()

    # Run audit
    results = run_audit(plugin_path)

    # Print report
    print_audit_report(results, plugin_path)

    # Save report to file if requested
    if args.report:
        save_report_to_file(results, plugin_path, args.report.resolve())

    # Fix mode — generate missing files
    if args.fix:
        print(f"{BOLD}Fix Mode{NC} {'(dry-run)' if args.dry_run else ''}")
        created = fix_missing_files(plugin_path, results, dry_run=args.dry_run, marketplace=args.marketplace)
        if created and not args.dry_run:
            # Re-run audit after fixes to show updated status
            print(f"\n{BOLD}Post-fix audit:{NC}")
            post_results = run_audit(plugin_path)
            print_audit_report(post_results, plugin_path)

    # Optionally run full CPV validation
    if args.validate:
        print(f"\n{BOLD}Running full CPV validation...{NC}\n")
        import subprocess

        scripts_dir = Path(__file__).resolve().parent
        validate_script = scripts_dir / "validate_plugin.py"
        if validate_script.exists():
            result = subprocess.run(
                [sys.executable, str(validate_script), str(plugin_path)],
                cwd=str(scripts_dir.parent),
            )
            return result.returncode
        else:
            print(f"{RED}Error:{NC} validate_plugin.py not found at {validate_script}", file=sys.stderr)
            return 1

    # Return exit code based on audit results
    has_critical = any(r.status == "CRITICAL" for r in results)
    has_missing = any(r.status == "MISSING" for r in results)
    if has_critical:
        return 2  # Critical issues found
    if has_missing:
        return 1  # Non-critical issues found
    return 0


if __name__ == "__main__":
    sys.exit(main())
