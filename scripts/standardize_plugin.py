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
import re
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
    (".github/workflows/ci.yml", False, "CI workflow (consolidated: lint + validate + test)"),
    (".github/workflows/release.yml", False, "Release workflow"),
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
# DRIFT DETECTION (TRDD-79638eb6)
# =============================================================================


# Mapping of pyproject-declared distribution names to the module name they
# install as when the two differ. Extend this as you encounter more drift
# between "pip install foo" and "import foo_bar".
_DIST_TO_MODULE: dict[str, str] = {
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
    "beautifulsoup4": "bs4",
    "pillow": "PIL",
    "msgpack-python": "msgpack",
    "protobuf": "google.protobuf",
    "grpcio": "grpc",
    "opencv-python": "cv2",
    "opencv-python-headless": "cv2",
    "scikit-learn": "sklearn",
    "scikit-image": "skimage",
    "python-jose": "jose",
    "pyjwt": "jwt",
    "pymongo": "pymongo",
    "psycopg2-binary": "psycopg2",
    "mysql-connector-python": "mysql.connector",
    "azure-storage-blob": "azure.storage.blob",
    "google-cloud-storage": "google.cloud.storage",
}

# Dependencies that are runtime tools (used via subprocess) and should not
# trigger "unused" warnings just because they don't appear as Python imports.
_RUNTIME_TOOLS: set[str] = {
    "ruff",
    "mypy",
    "pyright",
    "pytest",
    "coverage",
    "pre-commit",
    "tox",
    "nox",
    "black",
    "isort",
    "bandit",
    "safety",
    "uv",
    "hatch",
    "twine",
    "build",
    "setuptools",
    "wheel",
    "pip",
}


def _parse_pyproject_dependencies(pyproject_path: Path) -> list[str]:
    """Extract dependency distribution names from pyproject.toml.

    Parses the `[project].dependencies` array of PEP-621 and returns the
    bare distribution names (e.g. "requests" from "requests>=2.30,<3"). Also
    scans `[project.optional-dependencies]` groups so plugins that use extras
    for dev/test deps still get drift-checked.

    Uses tomllib when available (Python 3.11+), falls back to a very simple
    line-scan otherwise so this stays self-contained.
    """
    try:
        import tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

    try:
        raw = pyproject_path.read_bytes()
    except OSError:
        return []

    names: list[str] = []

    if tomllib is not None:
        try:
            data = tomllib.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            data = {}
        project = data.get("project", {}) if isinstance(data, dict) else {}
        if isinstance(project, dict):
            deps = project.get("dependencies", [])
            if isinstance(deps, list):
                for item in deps:
                    if isinstance(item, str):
                        names.append(_extract_dist_name(item))
            opt = project.get("optional-dependencies", {})
            if isinstance(opt, dict):
                for group_deps in opt.values():
                    if isinstance(group_deps, list):
                        for item in group_deps:
                            if isinstance(item, str):
                                names.append(_extract_dist_name(item))
    else:
        # Naive fallback — scan lines inside `dependencies = [ ... ]`.
        in_deps = False
        text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("dependencies") and "=" in s and "[" in s:
                in_deps = True
                continue
            if in_deps:
                if s.startswith("]"):
                    in_deps = False
                    continue
                # Lines like:  "requests>=2.30",
                if s.startswith('"') or s.startswith("'"):
                    stripped = s.strip().strip(",").strip("\"'")
                    if stripped:
                        names.append(_extract_dist_name(stripped))

    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _extract_dist_name(requirement: str) -> str:
    """Extract the bare distribution name from a PEP-508 requirement string.

    Strips version specifiers, extras, markers, and whitespace.
    Examples:
        "requests>=2.30"           -> "requests"
        "Flask[async] >= 2.0"      -> "Flask"
        "numpy (>=1.24); python_version>='3.10'" -> "numpy"
    """
    import re as _re

    # Strip environment markers (anything after ';')
    req = requirement.split(";", 1)[0]
    # Strip extras like [async]
    req = _re.sub(r"\[.*?\]", "", req)
    # Split on version specifiers and whitespace
    m = _re.match(r"\s*([A-Za-z0-9_.\-]+)", req)
    if not m:
        return ""
    return m.group(1).strip()


def _dist_to_import_candidates(dist_name: str) -> list[str]:
    """Return plausible module import names for a given distribution name.

    We check both the raw lowercased name and a normalized version because
    pyproject allows 'Flask' but code writes 'import flask'. PEP-503 normalizes
    separators to '-'; modules normalize them to '_'.
    """
    lower = dist_name.lower()
    candidates: set[str] = {lower}
    # Known mapping (e.g. pyyaml -> yaml)
    if lower in _DIST_TO_MODULE:
        candidates.add(_DIST_TO_MODULE[lower])
    # Dashes are not legal in Python module names — convert to underscore
    if "-" in lower:
        candidates.add(lower.replace("-", "_"))
    # Dots are fine (namespace packages) — keep as-is
    return sorted(candidates)


def _scan_python_imports(plugin_path: Path, directories: tuple[str, ...] = ("scripts", "hooks")) -> set[str]:
    """Return the set of top-level module names imported from any *.py file
    in the given subdirectories of plugin_path.

    This is a pure text scan — not an AST walk — so we catch both
    `import foo` and `from foo.bar import baz`. That's enough for drift
    detection; false positives from inline strings are harmless.
    """
    import re as _re

    found: set[str] = set()
    import_re = _re.compile(
        r"^\s*(?:from\s+([A-Za-z_][\w\.]*)|import\s+([A-Za-z_][\w\.]*(?:\s*,\s*[A-Za-z_][\w\.]*)*))", _re.MULTILINE
    )

    for subdir in directories:
        d = plugin_path / subdir
        if not d.is_dir():
            continue
        for py in d.rglob("*.py"):
            try:
                content = py.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in import_re.finditer(content):
                from_mod = match.group(1)
                import_mod = match.group(2)
                if from_mod:
                    found.add(from_mod.split(".")[0].lower())
                if import_mod:
                    # Handle `import foo, bar` — split on commas
                    for name in import_mod.split(","):
                        top = name.strip().split(".")[0].strip()
                        if top:
                            found.add(top.lower())
    return found


def audit_drift(plugin_path: Path) -> list[AuditItem]:
    """Cross-check pyproject.toml dependencies against actual imports.

    Flags as WARN any dependency declared in pyproject.toml `[project]` but
    never imported from `scripts/` or `hooks/`. Runtime tools (ruff, mypy,
    pytest, etc.) are exempt because they're invoked as subprocesses, not
    imported.

    Returns a list of AuditItem entries. Emits one PASS summary when all
    deps are referenced, or one WARN per unused dep.
    """
    items: list[AuditItem] = []
    pyproject_path = plugin_path / "pyproject.toml"
    if not pyproject_path.is_file():
        # Silent — not every plugin has a pyproject.toml
        return items

    declared = _parse_pyproject_dependencies(pyproject_path)
    if not declared:
        return items

    imports = _scan_python_imports(plugin_path, ("scripts", "hooks"))
    unused: list[str] = []
    for dist in declared:
        if not dist:
            continue
        lower = dist.lower()
        if lower in _RUNTIME_TOOLS:
            # Runtime tool — skip import-based drift check
            continue
        candidates = _dist_to_import_candidates(dist)
        if not any(c in imports for c in candidates):
            unused.append(dist)

    if unused:
        for dep in unused:
            items.append(
                AuditItem(
                    "drift",
                    f"dep:{dep}",
                    "WARN",
                    f"Declared dependency '{dep}' not imported in scripts/ or hooks/ — candidate for removal",
                )
            )
    else:
        items.append(
            AuditItem(
                "drift",
                "pyproject.toml deps",
                "PASS",
                f"All {len(declared)} declared dependencies are referenced",
            )
        )
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
    results.extend(audit_drift(plugin_path))
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
        "drift": "Project Drift (deps vs imports)",
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
        print(
            f"  {BOLD}Result:{NC} {GREEN}{total_pass} passed{NC}, {YELLOW}{total_issues} issues{NC} / {total} checks\n"
        )


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
    ".markdownlint.json": "gen_markdownlint_json",
    "scripts/publish.py": "gen_publish_py",
    "scripts/cpv_network_resilience.py": "gen_cpv_network_resilience_py",
    "git-hooks/pre-push": "gen_pre_push_hook",
    ".github/workflows/ci.yml": "gen_ci_yml",
    ".github/workflows/release.yml": "gen_release_yml",
    ".github/workflows/notify-marketplace.yml": "gen_notify_marketplace_yml",
}

# Files that should have the executable bit set
_EXECUTABLE_FILES: set[str] = {
    "scripts/publish.py",
    "scripts/cpv_network_resilience.py",
    "git-hooks/pre-push",
}

# Files safe to OVERWRITE in --force-templates mode. These are pure
# infrastructure (publish pipeline, CI, retry helpers, hook scripts) that
# the user is not expected to customise — keeping them in lockstep with
# the canonical CPV standard is the whole point of TRDD-bbff5bc5. README
# / pyproject.toml / .gitignore stay user-owned and are NEVER force-written.
_FORCE_TEMPLATE_FILES: set[str] = {
    "scripts/publish.py",
    "scripts/cpv_network_resilience.py",
    "git-hooks/pre-push",
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/notify-marketplace.yml",
    "cliff.toml",
    ".mega-linter.yml",
    ".markdownlint.json",
}

# Mirror of validate_plugin._LEGACY_PIPELINE_SCRIPTS — the names of older
# helpers that publish.py now subsumes. Kept here so the upgrade flow can
# move them without an extra import (avoids circular-import surprises during
# remote_validation launcher dispatch).
#
# Source-of-truth for severity + user-facing wording stays in validate_plugin;
# this list only needs the relative-path strings.
_LEGACY_PIPELINE_SCRIPTS_RELPATHS: tuple[str, ...] = (
    "scripts/bump_version.py",
    "scripts/release.sh",
    "scripts/release.py",
    "scripts/publish.sh",
    "scripts/lint.sh",
    "scripts/setup-hooks.sh",
    "scripts/compute_hashes.py",
    "scripts/verify_hashes.py",
    "scripts/changelog.py",
    "scripts/generate_changelog.py",
    "scripts/check_version.py",
    "scripts/install.sh",
)


def move_legacy_pipeline_scripts(plugin_path: Path, dry_run: bool = False) -> list[str]:
    """Move every legacy pipeline script (per ``_LEGACY_PIPELINE_SCRIPTS_RELPATHS``)
    from `scripts/` into `scripts_dev/` so the canonical publish.py is the
    only release entry point.

    Preservation guardrail: scripts are MOVED, not deleted, so the user can
    review the relocated files in `scripts_dev/` before final deletion. This
    matches the user's explicit feedback: "be careful with purging dead
    code or unreferenced scripts" — moving keeps the content git-recoverable
    if the user wants to bring something back.

    `scripts_dev/` is gitignored per the user's `.gitignore` convention so
    moved files won't be committed accidentally; the user can either delete
    them in a follow-up commit or run `git add scripts_dev/<file>` to keep
    them tracked.

    Returns the list of relative paths actually moved (or would-have-moved
    in dry-run mode).
    """
    moved: list[str] = []
    scripts_dev = plugin_path / "scripts_dev"

    for rel_path in _LEGACY_PIPELINE_SCRIPTS_RELPATHS:
        src = plugin_path / rel_path
        if not src.is_file():
            continue
        dest = scripts_dev / Path(rel_path).name
        if dry_run:
            print(f"  {BLUE}[dry-run] Would move{NC} {rel_path} → scripts_dev/{Path(rel_path).name}")
            moved.append(rel_path)
            continue
        scripts_dev.mkdir(parents=True, exist_ok=True)
        # If the destination already exists, append a `.<n>` suffix so we
        # don't clobber an earlier move (idempotent re-runs).
        if dest.exists():
            n = 1
            while True:
                candidate = dest.with_name(f"{dest.name}.{n}")
                if not candidate.exists():
                    dest = candidate
                    break
                n += 1
        src.rename(dest)
        rel_dest = dest.relative_to(plugin_path)
        print(f"  {GREEN}[moved]{NC} {rel_path} → {rel_dest}")
        moved.append(rel_path)

    return moved


_NOTIFY_MARKETPLACE_REL = ".github/workflows/notify-marketplace.yml"

# Issue #23: regex sources for detecting pre-existing values inside the
# plugin's notify-marketplace.yml. The MARKETPLACE_OWNER / MARKETPLACE_REPO
# patterns mirror the parser already in validate_plugin.py:2040 so the
# canonical regex stays in one place semantically. Quotes are optional —
# the field is YAML-quoted in canonical templates but plain in some forks.
_NOTIFY_OWNER_RE = re.compile(r"^\s*MARKETPLACE_OWNER:\s*['\"]?([^'\"\s]+)['\"]?\s*$", re.MULTILINE)
_NOTIFY_REPO_RE = re.compile(r"^\s*MARKETPLACE_REPO:\s*['\"]?([^'\"\s]+)['\"]?\s*$", re.MULTILINE)
# Match `secrets.NAME` references; we pick the FIRST hit because the file
# only ever references one PAT secret. The regex requires UPPER_SNAKE_CASE
# to filter out non-secret identifiers.
_NOTIFY_SECRET_RE = re.compile(r"secrets\.([A-Z][A-Z0-9_]*)")

# Placeholder values the canonical template emits when no real values are
# supplied. Detecting these prevents the migration from accidentally
# "preserving" the placeholder it just clobbered the real value with on a
# prior buggy run.
_NOTIFY_PLACEHOLDER_REPO = "my-plugins-marketplace"
_NOTIFY_PLACEHOLDER_OWNER = ""  # canonical template emits MARKETPLACE_OWNER: '<empty>' when github_owner is unset


def _detect_existing_notify_marketplace(plugin_path: Path) -> dict[str, str | None]:
    """Issue #23: extract pre-existing values from notify-marketplace.yml.

    Returns a dict ``{"owner": ..., "repo": ..., "secret_name": ...}`` with
    each entry set to ``None`` when not found OR when the value matches the
    canonical placeholder (so a re-migration of a previously-clobbered file
    doesn't keep the placeholder).
    """
    yml_path = plugin_path / _NOTIFY_MARKETPLACE_REL
    out: dict[str, str | None] = {"owner": None, "repo": None, "secret_name": None}
    if not yml_path.is_file():
        return out
    try:
        content = yml_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return out

    owner_match = _NOTIFY_OWNER_RE.search(content)
    if owner_match:
        owner_val = owner_match.group(1).strip()
        if owner_val and owner_val != _NOTIFY_PLACEHOLDER_OWNER:
            out["owner"] = owner_val

    repo_match = _NOTIFY_REPO_RE.search(content)
    if repo_match:
        repo_val = repo_match.group(1).strip()
        if repo_val and repo_val != _NOTIFY_PLACEHOLDER_REPO:
            out["repo"] = repo_val

    secret_match = _NOTIFY_SECRET_RE.search(content)
    if secret_match:
        out["secret_name"] = secret_match.group(1)

    return out


def _apply_notify_marketplace_overrides(
    params: PluginParams,
    plugin_path: Path,
    cli_marketplace: str | None,
) -> dict[str, tuple[str | None, str | None]]:
    """Issue #23: populate marketplace_owner / marketplace_secret_name on params.

    Precedence: CLI ``--marketplace`` flag > existing-YAML detection > defaults.
    Returns a dict mapping field name → (old_value, new_value) for every
    field that changed, so the caller can print a [migration] note.
    """
    changes: dict[str, tuple[str | None, str | None]] = {}
    detected = _detect_existing_notify_marketplace(plugin_path)

    # 1. CLI --marketplace=owner/repo wins for owner+repo (explicit user intent).
    cli_owner: str | None = None
    cli_repo: str | None = None
    if cli_marketplace and "/" in cli_marketplace:
        cli_owner, cli_repo = cli_marketplace.split("/", 1)

    # MARKETPLACE_OWNER resolution
    target_owner = cli_owner or detected["owner"]
    if target_owner and target_owner != params.marketplace_owner:
        changes["marketplace_owner"] = (params.marketplace_owner or None, target_owner)
        params.marketplace_owner = target_owner

    # MARKETPLACE_REPO resolution
    target_repo = cli_repo or detected["repo"]
    if target_repo and target_repo != params.marketplace:
        changes["marketplace"] = (params.marketplace or None, target_repo)
        params.marketplace = target_repo

    # v2.86.0 canon-name enforcement: the secret NAME is always
    # ``MARKETPLACE_PAT`` in CPV's canonical template. We record the
    # detected pre-existing name (when it differs) as a "deviation" so the
    # caller can emit a loud [ACTION REQUIRED] block telling the maintainer
    # to rename their gh secret. We do NOT plumb it back onto PluginParams
    # — the canon name wins.
    target_secret = detected["secret_name"]
    if target_secret and target_secret != "MARKETPLACE_PAT":
        changes["marketplace_secret_name__DEVIATION"] = (target_secret, "MARKETPLACE_PAT")

    return changes


# Issue #25 Defect D (v2.87.1): canonical workflows the migration installs
# (release.yml, ci.yml) run `uv run <tool>` for these tools. If the plugin's
# pre-existing pyproject.toml's [project.optional-dependencies].dev lacks any
# of them, `uv sync --extra dev` will not install them and the workflow step
# crashes on first push with "Failed to spawn: <tool>". pyproject.toml is
# user-owned (never force-overwritten — see _NEVER_FORCE_OVERWRITE), so we
# ALERT loudly rather than auto-edit, matching the issue-#23 pattern.
_CANONICAL_DEV_EXTRA_TOOLS: tuple[str, ...] = ("mypy", "pytest", "ruff")
_CANONICAL_DEV_EXTRA_FLOORS: dict[str, str] = {
    "mypy": ">=1.19.1",
    "pytest": ">=8.0.0",
    "ruff": ">=0.14.14",
}
_WORKFLOW_PATHS_REQUIRING_DEV_EXTRAS: frozenset[str] = frozenset(
    {".github/workflows/release.yml", ".github/workflows/ci.yml"}
)


def _canonical_dev_extras_missing(plugin_path: Path) -> list[str]:
    """Return canonical dev-extra tools missing from pyproject.toml.

    Read-only — pyproject.toml is user-owned, so this function only detects
    the gap. Callers emit the [ACTION REQUIRED] alert. Returns [] when
    pyproject.toml is absent (no Python toolchain to reconcile) or when
    every canonical tool is already declared in
    ``[project.optional-dependencies].dev``.
    """
    pyproject = plugin_path / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        # Python < 3.11 — refuse to guess. Plugins on those interpreters
        # were never going to run the canonical 3.12+ workflows anyway.
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    opt = project.get("optional-dependencies")
    if not isinstance(opt, dict):
        return []
    dev = opt.get("dev")
    if not isinstance(dev, list):
        return []
    declared: set[str] = set()
    for spec in dev:
        if not isinstance(spec, str):
            continue
        # PEP-508 name = everything before any version/extras/marker suffix.
        # Case-insensitive per PEP-503.
        name = re.split(r"[<>=~!\[;]", spec, 1)[0].strip().lower()
        if name:
            declared.add(name)
    return [tool for tool in _CANONICAL_DEV_EXTRA_TOOLS if tool not in declared]


def fix_missing_files(
    plugin_path: Path,
    results: list[AuditItem],
    dry_run: bool = False,
    marketplace: str | None = None,
    force_templates: bool = False,
) -> list[str]:
    """Generate missing standard files using templates from generate_plugin_repo.

    By default: only creates files that do not already exist (never overwrites).
    With force_templates=True: ALSO overwrites files in _FORCE_TEMPLATE_FILES
    (publish.py, ci/release/notify workflows, retry helpers, pre-push hook,
    cliff.toml, .mega-linter.yml). Existing copies are backed up to
    `<file>.bak` before being replaced. README / pyproject.toml / .gitignore
    are NEVER force-overwritten — those stay user-owned.

    If marketplace is provided (owner/repo), patches notify-marketplace.yml.
    Returns list of created (or would-create in dry-run) file paths.
    """
    import importlib

    # Identify which standard files are missing
    missing_files: set[str] = set()
    for item in results:
        if item.category == "files" and item.status in ("MISSING",) and item.name in _FILE_TO_GENERATOR:
            missing_files.add(item.name)

    # Force-overwrite mode: ALSO regenerate _FORCE_TEMPLATE_FILES even when
    # they already exist. Skipped when force_templates=False (default).
    force_overwrite: set[str] = set()
    if force_templates:
        for rel in _FORCE_TEMPLATE_FILES:
            if rel in _FILE_TO_GENERATOR:
                force_overwrite.add(rel)
        # Drop any missing-files duplicates so we don't process them twice.
        force_overwrite -= missing_files

    if not missing_files and not force_overwrite:
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

    # Issue #23 (v2.85.0): before generating notify-marketplace.yml, detect
    # values from the pre-existing file (if any) and let them override the
    # PluginParams defaults. Without this, --force-templates silently
    # clobbers a real MARKETPLACE_REPO with the literal placeholder and
    # rewrites the secret name to MARKETPLACE_PAT even when the repo's
    # configured secret is e.g. MARKETPLACE_DISPATCH_TOKEN.
    notify_changes: dict[str, tuple[str | None, str | None]] = {}
    will_emit_notify = _NOTIFY_MARKETPLACE_REL in missing_files or _NOTIFY_MARKETPLACE_REL in force_overwrite
    if will_emit_notify:
        notify_changes = _apply_notify_marketplace_overrides(params, plugin_path, marketplace)
        # Refuse-to-emit-placeholder guard: when --force-templates is on AND
        # an existing notify-marketplace.yml is being overwritten AND we
        # still have no real marketplace name (no CLI flag, nothing
        # detectable in the pre-existing YAML), refuse to ship the literal
        # placeholder. The caller's working YAML may have used a different
        # template version that doesn't match our regex; making them
        # supply --marketplace=owner/repo explicitly is safer than
        # silently breaking their notification chain.
        existing_yml = plugin_path / _NOTIFY_MARKETPLACE_REL
        if _NOTIFY_MARKETPLACE_REL in force_overwrite and existing_yml.is_file() and not params.marketplace:
            print(
                f"  {RED}REFUSED:{NC} cannot regenerate {_NOTIFY_MARKETPLACE_REL} — no marketplace "
                f"name detected in the existing file and no --marketplace=owner/repo flag was "
                f"passed. Emitting the placeholder '{_NOTIFY_PLACEHOLDER_REPO}' would silently "
                f"break the plugin's marketplace dispatch chain (issue #23). Re-run with "
                f"--marketplace=<owner>/<repo> to override, or check the existing file's "
                f"MARKETPLACE_REPO line is parseable."
            )
            # Drop notify-marketplace.yml from the work-set so the rest of
            # the migration proceeds. Other files still regenerate.
            force_overwrite.discard(_NOTIFY_MARKETPLACE_REL)
            missing_files.discard(_NOTIFY_MARKETPLACE_REL)
        elif notify_changes:
            # Surface the changes so the user notices when --force-templates
            # would alter a real value (e.g. owner override) AND emit a loud
            # [ACTION REQUIRED] block when a secret-name deviation is found.
            print(f"  {CYAN}[migration]{NC} notify-marketplace.yml derived from existing file:")
            deviation_key = "marketplace_secret_name__DEVIATION"
            for field_name, (old, new) in notify_changes.items():
                if field_name == deviation_key:
                    continue  # surfaced separately below with the action-required block
                if old != new:
                    print(f"    {DIM}{field_name}:{NC} {old!r} → {new!r}")

            if deviation_key in notify_changes:
                old_secret, _ = notify_changes[deviation_key]
                owner_for_gh = params.marketplace_owner or params.github_owner or "<owner>"
                repo_for_gh = params.repo_name or "<repo>"
                print()
                print(f"  {YELLOW}{BOLD}[ACTION REQUIRED]{NC} secret-name deviation detected")
                print(f"  The previous notify-marketplace.yml referenced {BOLD}secrets.{old_secret}{NC}.")
                print(
                    f"  CPV v2.86.0+ enforces the canonical secret name {BOLD}MARKETPLACE_PAT{NC} across all plugins —"
                )
                print(f"  the regenerated YAML now references {BOLD}secrets.MARKETPLACE_PAT{NC}.")
                print()
                print(f"  {GREEN}Run (assumes $MARKETPLACE_PAT is exported):{NC}")
                print(
                    f'    gh secret set MARKETPLACE_PAT --repo {owner_for_gh}/{repo_for_gh} --body "$MARKETPLACE_PAT"'
                )
                print()
                print(f"  {DIM}After the next push triggers a marketplace dispatch successfully:{NC}")
                print(f"    gh secret delete {old_secret} --repo {owner_for_gh}/{repo_for_gh}")
                print()

    # Import generator functions from generate_plugin_repo
    gen_module = importlib.import_module("generate_plugin_repo")

    created: list[str] = []

    # Process missing-then-force so the [create] / [overwrite] markers in the
    # output reflect the actual operation.
    process_set: list[tuple[str, str]] = [(p, "create") for p in sorted(missing_files)] + [
        (p, "overwrite") for p in sorted(force_overwrite)
    ]

    for rel_path, op_kind in process_set:
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
            tag = f"[dry-run] Would {op_kind}"
            print(f"  {BLUE}{tag}{NC} {file_path} ({len(content)} bytes){' [exec]' if is_executable else ''}")
            created.append(str(file_path))
            continue

        # Create parent directories
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # On overwrite, save a .bak alongside the original so the user can
        # diff / restore if the new template breaks something specific to
        # their plugin. Backup is silent — listed in the output line below.
        backup_str = ""
        if op_kind == "overwrite" and file_path.is_file():
            bak = file_path.with_suffix(file_path.suffix + ".bak")
            bak.write_bytes(file_path.read_bytes())
            backup_str = f" (backup: {bak.name})"

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

        verb = "Overwrote" if op_kind == "overwrite" else "Created"
        print(f"  {GREEN}{verb}:{NC} {file_path}{' [exec]' if is_executable else ''}{backup_str}")
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

    # Issue #25 Defect D (v2.87.1): when the migration emits release.yml or
    # ci.yml — both of which run `uv run mypy / pytest / ruff` under
    # `uv sync --extra dev` — alert the user if the pre-existing pyproject.toml
    # does not declare those tools in `[project.optional-dependencies].dev`.
    # Without this alert the workflow step crashes on first push with
    # `Failed to spawn: <tool>` even though the migration reported success.
    # pyproject.toml is user-owned, so we never auto-edit — we alert.
    workflow_emitted = bool(_WORKFLOW_PATHS_REQUIRING_DEV_EXTRAS & (missing_files | force_overwrite))
    if workflow_emitted and not dry_run:
        missing_tools = _canonical_dev_extras_missing(plugin_path)
        if missing_tools:
            print()
            print(f"  {YELLOW}{BOLD}[ACTION REQUIRED]{NC} pyproject.toml dev extras incomplete")
            print(
                f"  The CPV-shipped {BOLD}release.yml{NC} / {BOLD}ci.yml{NC} run "
                f"`uv run <tool>` for: {', '.join(_CANONICAL_DEV_EXTRA_TOOLS)}."
            )
            print(
                f"  Your pyproject.toml's {BOLD}[project.optional-dependencies].dev{NC} "
                f"is missing: {RED}{', '.join(missing_tools)}{NC}."
            )
            print(
                f"  `uv sync --extra dev` in CI will NOT install them — the workflow "
                f"step crashes on first push with {DIM}Failed to spawn: <tool>{NC}."
            )
            print()
            print(f"  {GREEN}Add to pyproject.toml's `dev` extra:{NC}")
            for tool in missing_tools:
                floor = _CANONICAL_DEV_EXTRA_FLOORS.get(tool, "")
                print(f'    "{tool}{floor}",')
            print()

    return created


# =============================================================================
# MAIN
# =============================================================================


def main() -> int:
    """Parse CLI arguments, run audit, optionally fix missing files."""
    from cpv_validation_common import launcher_epilog

    parser = argparse.ArgumentParser(
        description="Audit and standardize a Claude Code plugin repo against CPV standards.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (always invoke via the launcher):
  # Audit only (report gaps)
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize /path/to/plugin

  # Audit + fix missing files (never overwrites existing)
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize /path/to/plugin --fix

  # Dry-run fix (show what would be created)
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize /path/to/plugin --fix --dry-run

  # Save detailed report to file
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize /path/to/plugin --report audit.md

  # Also run full CPV validation
  uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize /path/to/plugin --validate

"""
        + launcher_epilog("standardize"),
    )
    parser.add_argument("plugin_path", type=Path, help="Path to the plugin repository root")
    parser.add_argument("--fix", action="store_true", help="Generate missing standard files from templates")
    parser.add_argument("--dry-run", action="store_true", help="Show what --fix would do without writing files")
    parser.add_argument("--report", type=Path, default=None, help="Save audit report to this file path")
    parser.add_argument(
        "--marketplace",
        type=str,
        help="Marketplace owner/repo for notify-marketplace.yml (e.g., Emasoft/emasoft-plugins)",
    )
    parser.add_argument("--validate", action="store_true", help="Also run validate_plugin.py for full validation")
    parser.add_argument(
        "--force-templates",
        action="store_true",
        help=(
            "OVERWRITE infrastructure files (publish.py, ci/release/notify "
            "workflows, retry helpers, pre-push hook, cliff.toml, .mega-linter.yml) "
            "with the canonical CPV templates. Existing copies are backed up to "
            "<file>.bak before being replaced. README, pyproject.toml, .gitignore "
            "are NEVER force-written. Use this to propagate TRDD-bbff5bc5 changes "
            "to existing plugins. Implies --fix and --clean-legacy."
        ),
    )
    parser.add_argument(
        "--clean-legacy",
        action="store_true",
        help=(
            "Move known-legacy pipeline scripts (bump_version.py, release.sh, "
            "lint.sh, compute_hashes.py, etc.) from scripts/ to scripts_dev/ — "
            "they are obsoleted by publish.py's 14-gate pipeline. Files are "
            "MOVED (not deleted) so the user can review before final removal. "
            "Auto-enabled when --force-templates is passed."
        ),
    )

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

    # Fix mode — generate missing files. --force-templates implies --fix.
    if args.fix or args.force_templates:
        mode_label = " (dry-run)" if args.dry_run else ""
        if args.force_templates:
            mode_label += " [FORCE TEMPLATES]"
        print(f"{BOLD}Fix Mode{NC}{mode_label}")
        created = fix_missing_files(
            plugin_path,
            results,
            dry_run=args.dry_run,
            marketplace=args.marketplace,
            force_templates=args.force_templates,
        )
        # Move legacy pipeline scripts (RC-LEGACY-PIPELINE-001) — auto-enabled
        # under --force-templates because the upgrade flow's whole point is
        # making publish.py the only release entry point.
        clean_legacy = args.clean_legacy or args.force_templates
        if clean_legacy:
            print(f"\n{BOLD}Legacy pipeline cleanup{NC}{mode_label}")
            moved = move_legacy_pipeline_scripts(plugin_path, dry_run=args.dry_run)
            if not moved:
                print(f"  {GREEN}No legacy pipeline scripts found.{NC}")
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
