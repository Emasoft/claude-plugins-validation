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
import ast
import difflib
import fnmatch
import json
import os
import re
import stat
from typing import TYPE_CHECKING, TypeGuard

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


def _gitignore_line_covers_entry(entry: str, line: str) -> bool:
    """Return True if gitignore ``line`` actually ignores the required ``entry``.

    The earlier implementation used a naive ``entry in line`` substring test,
    which gave FALSE POSITIVES: a line like ``.env.example`` (a tracked example
    file) reported the secrets-bearing ``.env`` entry as covered, ``redist/``
    reported ``dist/`` as covered, ``prebuild/`` reported ``build/``, etc. That
    masked genuinely-missing entries — a security-relevant false negative.

    Coverage is true only when:
      * the line is exactly the entry (after a leading-``/`` anchor is dropped,
        since the required entries are unanchored), or
      * the line is a glob pattern (contains ``*``/``?``/``[``) that MATCHES the
        entry path — this preserves the legitimate "a broader pattern covers
        it" case (e.g. ``*_cache/`` covering ``.pytest_cache/``).

    A plain literal line that merely *contains* the entry as a substring no
    longer counts.
    """
    line = line.strip()
    entry = entry.strip()
    # Drop a leading "/" anchor: required entries are written unanchored, and a
    # repo-root-anchored "/dist/" still covers the unanchored "dist/".
    pattern = line[1:] if line.startswith("/") else line
    if entry in (line, pattern):
        return True
    # Beyond an exact (optionally anchored) literal, only a wildcard pattern may
    # cover a *different* literal — a plain literal that merely shares a
    # substring with the entry must NOT count (that was the false-PASS bug).
    if not any(ch in pattern for ch in "*?["):
        return False
    candidates = [entry]
    if entry.endswith("/"):
        candidates.append(entry.rstrip("/"))  # "dist/" should match "dist*"
    return any(fnmatch.fnmatch(cand, pattern) or fnmatch.fnmatch(cand, pattern.rstrip("/")) for cand in candidates)


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
        # Present if an active line equals the entry or is a broader glob that
        # actually matches it — NOT merely a line that contains it as a substring.
        found = any(_gitignore_line_covers_entry(entry, line) for line in active_lines)
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

    # Issue #142 Defect #2 (audit half): the canonical ci.yml / release.yml run
    # `uv sync --extra dev`, so the `dev` extra MUST declare pytest/ruff/mypy or
    # CI fails at install with "Extra `dev` is not defined …" / "Failed to spawn".
    # The AUDIT path only REPORTS the gap — it never mutates pyproject (that is
    # the job of the --fix provisioning in fix_missing_files). Gated on a
    # canonical workflow being PRESENT (or about to be emitted) so a plugin that
    # does not use the canonical pipeline is never falsely flagged.
    workflows_dir = plugin_path / ".github" / "workflows"
    has_canonical_workflow = any((workflows_dir / Path(rel).name).is_file() for rel in _WORKFLOW_PATHS_REQUIRING_DEV_EXTRAS)
    if has_canonical_workflow:
        missing_dev = _canonical_dev_extras_missing(plugin_path)
        if missing_dev:
            items.append(
                AuditItem(
                    "pyproject",
                    "[project.optional-dependencies].dev",
                    "WARN",
                    f"dev extra missing CI tools: {', '.join(missing_dev)} "
                    f"(uv sync --extra dev in ci.yml/release.yml will fail) — run --fix to provision",
                )
            )
    return items


def audit_jscpd_config(plugin_path: Path) -> list[AuditItem]:
    """Audit the jscpd copy-paste gate parity (issue #143) — WARN-only.

    Surfaces, without mutating anything: a missing `.jscpd.json` (the local
    `publish.py --gate` jscpd check + CI's Mega-Linter both read it), and a
    scripts/publish.py that predates the gate. The actual findings are sourced
    from ``provision_jscpd_config(..., dry_run=True)`` so the audit text and the
    --fix behaviour can never drift. A PASS is emitted when the config is present
    AND publish.py already carries the gate, so a fully-canonical plugin still
    reports the dimension.
    """
    notes = provision_jscpd_config(plugin_path, dry_run=True)
    if not notes:
        return [AuditItem("jscpd", _JSCPD_CONFIG_REL, "PASS", "jscpd copy-paste gate parity OK")]
    return [AuditItem("jscpd", _JSCPD_CONFIG_REL, "WARN", note) for note in notes]


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
    # The `import` branch captures the rest of the line up to a comment /
    # semicolon as a SINGLE character class (`[^\n;#]+`) — NOT a quantified
    # comma-list group. The comma list is parsed in Python below.
    # Rationale: a quantified group whose body also ends in a quantifier trips the
    # skillaudit REGEX_DOS heuristic (and is a genuine backtracking-risk shape);
    # the single char class is provably linear (no catastrophic backtracking) AND
    # parses `as` aliases / multi-name lists more correctly than the old regex did.
    import_re = _re.compile(
        r"^\s*(?:from\s+([A-Za-z_][\w\.]*)|import\s+([^\n;#]+))", _re.MULTILINE
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
                    # import_mod is the rest of the `import ...` line (up to a
                    # comment/semicolon). Split the comma list; for each entry take
                    # the first whitespace token (so `foo as f` -> foo) then its
                    # top-level package (`foo.bar` -> foo).
                    for name in import_mod.split(","):
                        tokens = name.strip().split()
                        if tokens:
                            found.add(tokens[0].split(".")[0].lower())
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
    results.extend(audit_jscpd_config(plugin_path))
    results.extend(audit_cspell_config(plugin_path))
    results.extend(audit_commitlint_config(plugin_path))
    results.extend(audit_inverted_private_usernames(plugin_path))
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
        "jscpd": "Copy-paste Gate (jscpd ↔ CI parity)",
        "cspell": "Spell Gate (cspell ↔ CI parity)",
        "commitlint": "Commit Gate (commitlint ↔ CI parity)",
        "ci-env": "CI Validate Env (inverted CLAUDE_PRIVATE_USERNAMES)",
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

    # Must stay in sync with print_audit_report's category_titles — the loop
    # below only renders categories whose key is present here, so a missing key
    # silently drops every finding in that category from the saved file while
    # the summary line still counts them (making totals not add up). "drift"
    # was previously absent, so all audit_drift findings vanished from the file.
    category_titles = {
        "files": "Standard Files",
        "dirs": "Component Directories",
        "gitignore": ".gitignore Entries",
        "badges": "README Badges",
        "pyproject": "pyproject.toml Sections",
        "jscpd": "Copy-paste Gate (jscpd ↔ CI parity)",
        "cspell": "Spell Gate (cspell ↔ CI parity)",
        "commitlint": "Commit Gate (commitlint ↔ CI parity)",
        "ci-env": "CI Validate Env (inverted CLAUDE_PRIVATE_USERNAMES)",
        "python": "Python Version",
        "drift": "Project Drift (deps vs imports)",
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

# Issue #145b / #144Bb — the RC the skip messages point a reader at when a
# --force-templates overwrite is declined because the plugin's file is already
# at/AHEAD of canon (i.e. the validator's "would downgrade" case).
_PIPELINE_DRIFT_RC: str = "RC-PIPELINE-DRIFT-001"

# the-skills-menu canon migration (TRDD-478d9687 / the-skills-menu-create spec).
# The EXACT mandatory dynamic-loading instruction every migrated agent body must
# carry, taken verbatim from the the-skills-menu-create spec
# (skills/the-skills-menu-create/references/the-skills-menu-spec.md §"Agent body
# instruction rule"). That spec is the SINGLE SOURCE OF TRUTH for the rewrite —
# this constant must stay byte-identical to it. The string starts with "You must
# load …", so it never begins with a markdown-special char (#, +, *, -); the
# markdown-poison guard (standardize-plugin SKILL.md) below is therefore a
# defensive assertion, not a transform.
_SKILLS_MENU_BODY_INSTRUCTION: str = (
    "You must load the skills you need dynamically. Use the Skill() tool to load "
    "them. Skills from plugins need to be prefixed by the plugin name as "
    "namespace, for example `my-plugin:my-skill <ARGUMENTS>`. Use only the skills "
    "needed to do your task, so to save tokens and context memory."
)

# Markdown-special line-start chars the inserted body paragraph must never begin
# with (markdownlint MD018/MD004 would fire and block --strict). The canonical
# instruction above is guaranteed safe; this set backs the defensive guard.
_MD_POISON_LINE_START: tuple[str, ...] = ("#", "+ ", "* ", "- ")


def _manifest_intentional_divergence(manifest: dict) -> set[str]:
    """Return the set of repo-relative paths the plugin marks as a deliberate
    divergence from canon — ``cpv.pipeline.intentional_divergence`` in
    plugin.json (issue #144Ba; the manifest key is authored by C3).

    The key is a list of repo-relative path strings. Anything else (a missing
    key, a non-list value, non-string elements) is treated as "no divergence
    declared" — the conservative direction: we never let a malformed manifest
    silently suppress an overwrite we'd otherwise perform.

    SELECTOR not suppressor: this only governs whether ``--force-templates``
    leaves a file in place; it does NOT silence the validator's drift WARNING
    for that file (the validator owns that, per the divergence-is-noted-not-
    suppressed contract).
    """
    cpv = manifest.get("cpv")
    if not isinstance(cpv, dict):
        return set()
    pipeline = cpv.get("pipeline")
    if not isinstance(pipeline, dict):
        return set()
    raw = pipeline.get("intentional_divergence")
    if not isinstance(raw, list):
        return set()
    return {p for p in raw if isinstance(p, str)}


def _force_template_skip_reason(
    plugin_file: Path,
    rel_path: str,
    canon_content: str,
    divergence: set[str],
) -> str | None:
    """Decide whether a ``--force-templates`` overwrite of ``rel_path`` must be
    SKIPPED (issue #145b / #144Bb).

    Returns the COMPLETE skip line to print (so each condition controls its own
    wording exactly) when the overwrite must be declined, or ``None`` when the
    file should be overwritten as before.

    Two skip conditions:

    1. ``rel_path`` is in ``divergence`` (the plugin's
       ``cpv.pipeline.intentional_divergence`` manifest list) — skip regardless
       of drift direction; the plugin deliberately diverges.
    2. The plugin's CURRENT file is at/AHEAD of canon — i.e. force-overwriting
       it would DOWNGRADE a hardened/ahead file (the exact case the validator
       flags "at or AHEAD of canon … Do NOT --force-templates"). We classify
       direction by REUSING ``validate_plugin._classify_drift_direction`` on a
       unified diff of (expected=CANON, actual=PLUGIN). ``ahead`` and ``mixed``
       both mean "do not downgrade" → skip; ``behind`` and ``plain`` mean the
       plugin lacks canon's hardening / is just stale → overwrite.

    An ABSENT plugin file is never skipped here (a new file must be written);
    in practice force-overwrite only ever processes existing files, but this
    keeps the helper correct for any caller. An IDENTICAL file falls through to
    ``"plain"`` (no diff lines, no hardening markers either side) → overwrite,
    which is a harmless no-op rewrite of byte-identical content.
    """
    if rel_path in divergence:
        return f"skipped {rel_path} — marked intentional_divergence"

    if not plugin_file.is_file():
        # Nothing to downgrade — let the caller write the new file.
        return None

    plugin_content = plugin_file.read_text(encoding="utf-8")
    if plugin_content == canon_content:
        # Byte-identical; nothing to skip (and nothing to downgrade).
        return None

    # Issue #165 — a canon YAML file is MERGED, never blind-overwritten (see
    # _merge_canon_yaml, which the caller invokes). A blind overwrite deletes the
    # author's own keys AND the comment paragraphs justifying them — the real
    # `.mega-linter.yml` case, where CKV_DOCKER_2 and its 8-line rationale were
    # silently dropped. The merge is handled in the caller's overwrite path, so
    # there is nothing to SKIP here.
    if rel_path in _CANON_YAML_MERGE_FILES:
        return None

    # Diff order MUST be (expected=CANON, actual=PLUGIN) so a marker on a `+`
    # line means "the PLUGIN added hardening" (ahead) and a marker on a `-`
    # line means "CANON carries hardening the plugin lacks" (behind) — the
    # exact contract _classify_drift_direction documents.
    diff_lines = list(
        difflib.unified_diff(
            canon_content.splitlines(),
            plugin_content.splitlines(),
            lineterm="",
        )
    )

    # Lazy import to avoid a circular-import surprise during remote_validation
    # launcher dispatch (standardize_plugin ↔ validate_plugin). Read-only — the
    # signature is kept stable by C3 specifically so this import works.
    from validate_plugin import _classify_drift_direction  # noqa: E402

    direction = _classify_drift_direction(diff_lines)
    if direction in ("ahead", "mixed"):
        return f"skipped force-overwrite of {rel_path} — at/AHEAD of canon (would downgrade); see {_PIPELINE_DRIFT_RC}"
    return None


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


# Issue #25 Defect D (v2.87.1) / issue #142 Defect #2: the canonical workflows
# the migration installs (release.yml, ci.yml) run `uv sync --extra dev`, so the
# plugin's [project.optional-dependencies].dev must declare these tools. The
# AUDIT path WARNs when they're missing (audit_pyproject); the --fix path
# auto-provisions them (provision_dev_extra). The set below is the ALWAYS-REQUIRED
# DETECTION list (what CI needs unconditionally); the EXACT provisioned literal is
# _PROVISION_DEV_EXTRA, which must stay byte-identical to the generator's default.
_CANONICAL_DEV_EXTRA_TOOLS: tuple[str, ...] = ("mypy", "pytest", "ruff")
_WORKFLOW_PATHS_REQUIRING_DEV_EXTRAS: frozenset[str] = frozenset(
    {".github/workflows/release.yml", ".github/workflows/ci.yml"}
)

# ─────────────────────────────────────────────────────────────────────────
# RC-9 — a SHARDED pytest matrix requires the `pytest-split` distribution
# ─────────────────────────────────────────────────────────────────────────
# CI-failure forensics 2026-07-13, run 28959141245:
#
#     pytest: error: unrecognized arguments: --splits --group
#
# The canonical ci.yml emits a SHARDED test matrix (`pytest … --splits N --group K`),
# but `--splits`/`--group` exist ONLY when `pytest-split` is installed. The
# generator has declared it since the shard landed — but THIS module's dev-extra
# provisioner did not, so a plugin migrated with `--force-templates` got the
# sharded ci.yml AND a dev extra without `pytest-split`: every shard died.
# That migration path is how RC-9 actually reached CI.
#
# The coupling is therefore made CONDITIONAL AND STRUCTURAL: the requirement is
# derived from the workflows ON DISK (does any of them run `pytest … --splits`?),
# which is exactly the condition under which the flags are used. Because
# fix_missing_files writes the force-templated ci.yml BEFORE provisioning the dev
# extra, the same on-disk probe answers correctly for BOTH paths — the canonical
# (sharded) migration AND a plain --fix on a repo already carrying a sharded
# workflow. A plugin whose CI is NOT sharded never has `pytest-split` added
# (that would be inventing a dependency it does not use).
#
# The rule is the CIP-8 detector's rule (cpv_ci_parity_checks). Like the CIP-6
# re-pin above, it is kept SELF-CONTAINED here — the two agree by construction
# (identical regex + identical "declared anywhere a `uv sync` would install it"
# semantics), not by import, so this migrator has no cross-module dependency on
# the detector. re2-safe: character classes + bounded quantifiers, no lookaround.
_PYTEST_SPLITS_RE = re.compile(r"\bpytest\b[^\n]*--splits\b")
_PYTEST_SPLIT_DIST = "pytest-split"
# Mirrors generate_plugin_repo.PYTEST_SPLIT_REQUIREMENT; used only if the lazy
# import fails (CPV installed as a wheel with the generator unavailable).
_FALLBACK_PYTEST_SPLIT_REQUIREMENT = "pytest-split>=0.9"


def _pytest_split_requirement() -> str:
    """The EXACT `pytest-split` requirement literal the generator emits.

    Imported lazily from ``generate_plugin_repo`` (which exports it precisely so
    the scaffold and this migrator cannot desync); falls back to the mirrored
    literal when the generator is unavailable.
    """
    try:
        from generate_plugin_repo import PYTEST_SPLIT_REQUIREMENT

        return str(PYTEST_SPLIT_REQUIREMENT).strip() or _FALLBACK_PYTEST_SPLIT_REQUIREMENT
    except Exception:
        return _FALLBACK_PYTEST_SPLIT_REQUIREMENT


def _workflow_files(plugin_path: Path) -> list[Path]:
    """Every `.github/workflows/*.yml|*.yaml` file, sorted (missing dir → [])."""
    workflows = plugin_path / ".github" / "workflows"
    if not workflows.is_dir():
        return []
    return sorted(p for p in workflows.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml"))


def _workflow_runs_sharded_pytest(plugin_path: Path) -> bool:
    """Whether any workflow runs a SHARDED pytest (`pytest … --splits`)."""
    for wf in _workflow_files(plugin_path):
        try:
            text = wf.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _PYTEST_SPLITS_RE.search(text):
            return True
    return False


def _normalize_dist_name(name: str) -> str:
    """PEP-503 normalization: lowercase, runs of `-_.` collapsed to a single `-`.

    So `pytest_split`, `PyTest.Split` and `pytest-split` are ONE name — but
    `pytest-splitter` (a DIFFERENT distribution) stays distinct and can never
    satisfy the requirement.
    """
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _requirement_dist_name(spec: str) -> str:
    """The normalized distribution name of a PEP-508 requirement string."""
    return _normalize_dist_name(re.split(r"[<>=~!\[;\s]", spec, 1)[0])


def _project_declares_pytest_split(plugin_path: Path) -> bool:
    """Whether `pytest-split` is declared ANYWHERE a `uv sync` would install it.

    Checks ``[project].dependencies``, EVERY ``optional-dependencies`` extra, and
    every PEP-735 ``[dependency-groups]`` group — the CIP-8 detector's exact
    surface. Broad on purpose: a plugin that already declares it (in a
    `test` extra, say) must NOT have a duplicate injected into `dev`.

    Returns False when pyproject is absent/unparseable — "no signal" means the
    provisioner does nothing anyway (it bails on an unreadable pyproject).
    """
    pyproject = plugin_path / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    if not isinstance(data, dict):
        return False

    specs: list[str] = []

    def _collect(container: object) -> None:
        """Collect every requirement string from a list, or from a dict of lists."""
        if isinstance(container, list):
            specs.extend(s for s in container if isinstance(s, str))
        elif isinstance(container, dict):
            for value in container.values():
                if isinstance(value, list):
                    specs.extend(s for s in value if isinstance(s, str))

    project = data.get("project")
    if isinstance(project, dict):
        _collect(project.get("dependencies"))
        _collect(project.get("optional-dependencies"))
    _collect(data.get("dependency-groups"))

    return any(_requirement_dist_name(spec) == _PYTEST_SPLIT_DIST for spec in specs)


def _required_dev_extra_tools(plugin_path: Path) -> tuple[str, ...]:
    """The dev-extra tools THIS plugin's CI actually needs.

    The always-required canonical trio, plus ``pytest-split`` iff a workflow runs
    a sharded pytest. Deriving the requirement from the workflows on disk is what
    makes the matrix ↔ dependency coupling impossible to desync (RC-9).
    """
    tools = list(_CANONICAL_DEV_EXTRA_TOOLS)
    if _workflow_runs_sharded_pytest(plugin_path):
        tools.append(_PYTEST_SPLIT_DIST)
    return tuple(tools)


def _canonical_dev_extras_missing(plugin_path: Path) -> list[str]:
    """Return canonical dev-extra tools missing from pyproject.toml.

    Read-only — only DETECTS the gap (the --fix provisioner acts on it).
    Returns [] when pyproject.toml is absent (no Python toolchain to
    reconcile), when the interpreter predates tomllib, or when the file is
    unparseable — those are not actionable "missing tool" states.

    Issue #142 Defect #2: when pyproject EXISTS but the
    ``[project.optional-dependencies]`` table OR its ``dev`` key is ABSENT,
    EVERY canonical tool is reported missing — a plugin shipping the canonical
    ci.yml / release.yml (which run ``uv sync --extra dev``) with no dev extra
    fails CI with "Extra `dev` is not defined …". (The prior behaviour wrongly
    returned [] for an absent dev extra, masking exactly this defect.)

    RC-9: ``pytest-split`` joins the list iff a workflow runs a sharded pytest
    AND the distribution is declared NOWHERE a `uv sync` would install it (not
    just the `dev` extra) — so a plugin that already declares it elsewhere never
    gets a duplicate.
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
    # An absent [project] / optional-dependencies / dev means NOTHING is
    # declared — fall through to an empty `declared` set (→ all tools missing),
    # rather than returning [] and masking the gap.
    project = data.get("project")
    opt = project.get("optional-dependencies") if isinstance(project, dict) else None
    dev = opt.get("dev") if isinstance(opt, dict) else None
    declared: set[str] = set()
    if isinstance(dev, list):
        for spec in dev:
            if not isinstance(spec, str):
                continue
            # PEP-508 name = everything before any version/extras/marker suffix,
            # PEP-503-normalized (so `pytest_split` counts as `pytest-split`).
            name = _requirement_dist_name(spec)
            if name:
                declared.add(name)

    missing: list[str] = []
    for tool in _required_dev_extra_tools(plugin_path):
        if tool == _PYTEST_SPLIT_DIST:
            # Satisfiable from ANY dependency surface, not just the dev extra.
            if not _project_declares_pytest_split(plugin_path):
                missing.append(tool)
        elif tool not in declared:
            missing.append(tool)
    return missing


# Issue #142 Defect #2 (provision half): the EXACT literal dev-extra list the
# generator (generate_plugin_repo.py) sets as the default. It MUST match the
# generator byte-for-byte so a `standardize --fix` adoption and a freshly
# scaffolded plugin declare the same `dev` extra. The canonical trio is unpinned
# by design — the generator owns any future pinning, and provisioning must not
# invent floors the generator does not also emit. `pytest-split` IS pinned,
# because the generator pins it (PYTEST_SPLIT_REQUIREMENT) — mirroring the
# generator is not inventing a floor.
_PROVISION_DEV_EXTRA: tuple[str, ...] = ("pytest", "ruff", "mypy", _PYTEST_SPLIT_DIST)


def _dev_extra_spec(tool: str) -> str:
    """The requirement literal to EMIT for a provisioned dev-extra tool."""
    if tool == _PYTEST_SPLIT_DIST:
        return _pytest_split_requirement()
    return tool


def _format_dev_extra_entries(tools: list[str]) -> str:
    """Render dev-extra tool names as the inner lines of a TOML array.

    Returns e.g. ``    "pytest",\n    "ruff",\n    "mypy",`` (4-space indent,
    trailing comma per entry) so the result drops straight into a
    ``dev = [\n…\n]`` block matching the generator's formatting. A tool whose
    canonical requirement carries a version floor (``pytest-split``) is rendered
    with it (``    "pytest-split>=0.9",``).
    """
    return "".join(f'    "{_dev_extra_spec(tool)}",\n' for tool in tools)


def provision_dev_extra(plugin_path: Path, dry_run: bool = False) -> list[str]:
    """Provision (or augment) ``[project.optional-dependencies].dev`` so the
    canonical ci.yml / release.yml ``uv sync --extra dev`` step succeeds.

    Issue #142 Defect #2. Three cases, all format-preserving (text edit, NOT a
    TOML re-serialize — the project ships no TOML writer, and a re-serialize
    would drop comments + reflow every other table):

    1. No ``[project.optional-dependencies]`` table  → append a new table with
       ``dev = [_PROVISION_DEV_EXTRA…]``.
    2. Table present but no ``dev`` key              → insert a ``dev = [...]``
       line into the existing table (other extras preserved).
    3. ``dev`` present but incomplete               → AUGMENT: add ONLY the
       missing tools as new list entries; existing entries (with their pins)
       and every other extra/table are preserved verbatim.

    The lockfile (``uv.lock``) is refreshed via ``uv lock`` when one exists so
    the new extra is resolved; a missing/failed ``uv`` is non-fatal (CI's
    ``uv sync`` regenerates it). Returns a list of human-readable change notes
    (empty when pyproject is absent or the dev extra already declares every
    canonical tool).

    pyproject.toml stays user-owned for everything ELSE — this function only
    ever ADDS the missing CI tools; it never rewrites or removes existing
    content.
    """
    pyproject = plugin_path / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:
        return []
    try:
        original = pyproject.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = tomllib.loads(original)
    except tomllib.TOMLDecodeError:
        return []
    if not isinstance(data, dict):
        return []

    missing = _canonical_dev_extras_missing(plugin_path)
    if not missing:
        return []
    # Provision in the generator's canonical ORDER, restricted to what's absent.
    to_add = [tool for tool in _PROVISION_DEV_EXTRA if tool in missing]
    if not to_add:
        # Defensive: a tool the generator does not list is missing — nothing the
        # canonical provisioner is responsible for. The audit WARNING still fires.
        return []

    project = data.get("project")
    opt = project.get("optional-dependencies") if isinstance(project, dict) else None
    dev_exists = isinstance(opt, dict) and isinstance(opt.get("dev"), list)
    table_exists = isinstance(opt, dict)

    new_text = original
    note: str
    if not dev_exists and not table_exists:
        # Case 1 — append a fresh table block.
        block = "\n[project.optional-dependencies]\ndev = [\n" + _format_dev_extra_entries(to_add) + "]\n"
        if not new_text.endswith("\n"):
            new_text += "\n"
        new_text += block
        note = f"created [project.optional-dependencies].dev = {list(to_add)}"
    elif not dev_exists and table_exists:
        # Case 2 — insert a `dev = [...]` line just under the existing table
        # header, preserving every other extra in the table.
        header_re = re.compile(r"(?m)^\[project\.optional-dependencies\][^\n]*\n")
        m = header_re.search(new_text)
        if not m:
            # tomllib saw the table but the regex didn't — bail rather than guess.
            return []
        dev_line = "dev = [\n" + _format_dev_extra_entries(to_add) + "]\n"
        insert_at = m.end()
        new_text = new_text[:insert_at] + dev_line + new_text[insert_at:]
        note = f"added dev extra to existing table = {list(to_add)}"
    else:
        # Case 3 — AUGMENT the existing `dev = [ ... ]` array with the missing
        # tools only. Find the array's closing bracket and inject entries before
        # it; existing entries (and their version pins) are untouched.
        dev_re = re.compile(r"(?ms)^(?P<indent>[ \t]*)dev\s*=\s*\[(?P<body>.*?)\]")
        m = dev_re.search(new_text)
        if not m:
            # A single-line `dev = ["x"]` or unusual layout the multiline regex
            # missed — refuse to mutate rather than risk corrupting the file.
            return []
        body = m.group("body")
        addition = _format_dev_extra_entries(to_add)
        # Preserve a trailing newline before the closing bracket so the injected
        # entries land on their own lines regardless of the prior body shape.
        if body.strip() and not body.rstrip(" \t").endswith("\n"):
            new_body = body.rstrip() + ",\n" + addition
        else:
            new_body = body + addition
        new_text = new_text[: m.start()] + f"{m.group('indent')}dev = [{new_body}]" + new_text[m.end() :]
        note = f"augmented dev extra with {list(to_add)}"

    if new_text == original:
        return []

    if dry_run:
        return [f"[dry-run] pyproject.toml: {note}"]

    pyproject.write_text(new_text, encoding="utf-8")
    notes = [f"pyproject.toml: {note}"]

    # Refresh the lockfile so the new extra resolves. Non-fatal: CI's `uv sync`
    # regenerates the lock, and a plugin without uv installed locally still has
    # a correct pyproject. Never raise — provisioning succeeded the moment the
    # pyproject was written.
    lock = plugin_path / "uv.lock"
    if lock.is_file():
        import shutil
        import subprocess

        uv = shutil.which("uv")
        if uv:
            try:
                proc = subprocess.run(
                    [uv, "lock"],
                    cwd=str(plugin_path),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if proc.returncode == 0:
                    notes.append("uv.lock refreshed")
                else:
                    notes.append("uv.lock refresh skipped (uv lock failed — CI will regenerate)")
            except (OSError, subprocess.SubprocessError):
                notes.append("uv.lock refresh skipped (uv unavailable — CI will regenerate)")
        else:
            notes.append("uv.lock refresh skipped (uv not on PATH — CI will regenerate)")
    return notes


# Issue #142 Defect #4: the canonical consolidated ci.yml carries a `Validate`
# job that runs `cpv-remote-validate plugin . --strict`, fully replacing the
# old standalone "Plugin Validation" validate.yml that pre-v2.12.32 CPV scaffolds
# shipped. Standardize must remove that superseded file (else ci.yml's actionlint
# Lint job trips on validate.yml's pre-existing SC2086) — but ONLY when the file
# is recognisably a CPV-shipped plugin-validate workflow, NEVER an unrelated user
# workflow that merely happens to be named validate.yml.
_SUPERSEDED_VALIDATE_YML_REL = ".github/workflows/validate.yml"

# Identity markers. We require BOTH a CPV-validate COMMAND marker AND a
# recognisable workflow NAME so an unrelated `validate.yml` (e.g. a project's own
# test or schema-validation workflow) is never deleted.
_CPV_VALIDATE_CMD_MARKERS: tuple[str, ...] = (
    "cpv-remote-validate plugin",
    "remote_validation.py plugin",
    "validate_plugin.py",
)
# The canonical CPV plugin-validate workflow names across CPV template history.
_CPV_VALIDATE_NAME_MARKERS: tuple[str, ...] = (
    "plugin validation",
    "validate plugin",
)
_CPV_VALIDATE_NAME_RE = re.compile(r"(?im)^\s*name:\s*['\"]?(?P<name>[^'\"\n]+)['\"]?\s*$")


def _is_cpv_shipped_validate_yml(path: Path) -> bool:
    """Return True only when ``path`` is recognisably a CPV-shipped plugin
    validate.yml (the workflow ci.yml's Validate job supersedes).

    Conservative by construction — requires BOTH:
      * a CPV plugin-validate COMMAND (cpv-remote-validate plugin / validate_plugin.py), AND
      * a top-level workflow ``name:`` matching a known CPV-validate name.

    An unrelated workflow named validate.yml that lacks either marker is NEVER
    matched, so this can never delete a user's own validation workflow.
    """
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    low = content.lower()
    has_cmd = any(marker in low for marker in _CPV_VALIDATE_CMD_MARKERS)
    if not has_cmd:
        return False
    for m in _CPV_VALIDATE_NAME_RE.finditer(content):
        wf_name = m.group("name").strip().lower()
        if any(marker in wf_name for marker in _CPV_VALIDATE_NAME_MARKERS):
            return True
    return False


def remove_superseded_validate_yml(plugin_path: Path, dry_run: bool = False) -> list[str]:
    """Remove the superseded CPV ``validate.yml`` when the consolidated ci.yml
    (which carries the replacement Validate job) is present.

    Issue #142 Defect #4. Safe-deletion: the file is moved into
    ``scripts_dev/superseded-workflows/`` (gitignored, git-recoverable) rather
    than hard-deleted, mirroring ``move_legacy_pipeline_scripts``'s preservation
    guardrail. Only runs when ``_is_cpv_shipped_validate_yml`` confirms the
    file's identity, so an unrelated user workflow is never touched.

    Returns a list of human-readable notes (including the mandatory
    branch-protection follow-up), or [] when there is nothing to remove.
    """
    validate_yml = plugin_path / _SUPERSEDED_VALIDATE_YML_REL
    if not validate_yml.is_file():
        return []
    # Only supersede when the replacement ci.yml actually exists — otherwise we
    # would strip the plugin's ONLY validation workflow.
    if not (plugin_path / ".github" / "workflows" / "ci.yml").is_file():
        return []
    if not _is_cpv_shipped_validate_yml(validate_yml):
        # An unrelated validate.yml — leave it untouched.
        return []

    note_branch = (
        "[ACTION REQUIRED] branch protection: re-point the required check "
        '"Plugin Validation" to ci.yml\'s "Validate" / "Test" jobs '
        "(the standalone validate.yml has been superseded)."
    )

    if dry_run:
        return [
            f"[dry-run] would remove superseded {_SUPERSEDED_VALIDATE_YML_REL} "
            "(replaced by ci.yml's Validate job)",
            note_branch,
        ]

    dest_dir = plugin_path / "scripts_dev" / "superseded-workflows"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "validate.yml"
    if dest.exists():
        n = 1
        while True:
            candidate = dest.with_name(f"validate.yml.{n}")
            if not candidate.exists():
                dest = candidate
                break
            n += 1
    validate_yml.rename(dest)
    rel_dest = dest.relative_to(plugin_path)
    return [
        f"removed superseded {_SUPERSEDED_VALIDATE_YML_REL} → {rel_dest} "
        "(replaced by ci.yml's Validate job)",
        note_branch,
    ]


# =============================================================================
# TRDD-HZSI0BZ6 — re-pin a STALE / INVALID CPV ref on a plain --fix
# =============================================================================
# A plugin migrated by an OLD CPV (≤v2.137, pre-#139) pins
# `git+https://github.com/Emasoft/claude-plugins-validation@main` in its
# `.github/workflows/*.yml` — but CPV's default branch is `master`, so
# `uvx --from git+…@main` 404s (`Git operation failed / Updating … (main)`) and
# the workflow red-CIs forever. `--force-templates` already re-pins these files
# because ci.yml / release.yml / notify-marketplace.yml are in
# _FORCE_TEMPLATE_FILES (their whole body is regenerated with `cpv_ref_resolved`).
# But a NORMAL `--fix` only CREATES missing files; it never touches an existing
# workflow, so a stale `@main` survives every plain `--fix`. This targeted
# re-pin closes that gap: on ANY `--fix` run it rewrites a STALE
# `claude-plugins-validation@<bad-ref>` to `@<cpv_ref_resolved>` in place,
# without otherwise rewriting the workflow (a customised-but-correct workflow is
# preserved). It is SELECTOR-scoped — it only acts on the CPV ref, never any
# other action ref.
#
# "bad ref" uses the EXACT CIP-6 rule (TRDD-HZSI0BZ6): valid = `master`, a
# `v<semver>` tag, or a 7-40 hex commit SHA; anything else (`@main` / `@develop`
# / `@HEAD` / `@feature-x`) is stale and gets re-pinned. This rule is kept
# self-contained HERE (not imported from cpv_ci_parity_checks) so standardize
# has no cross-module dependency on the CIP-6 detector — the two share the rule
# by construction (identical regexes), not by import. re2-safe: the regexes use
# only character classes, anchors and bounded quantifiers (no lookaround).

# Capture the CPV ref pinned on a `git+…/claude-plugins-validation[.git]@<ref>`
# URL. The ref runs up to the first whitespace, `'`, `"`, or `#` (so a trailing
# `#egg=` / inline comment / quote does not bleed into the captured ref).
_CPV_REF_PIN_RE = re.compile(
    r"(?P<prefix>git\+https://github\.com/Emasoft/claude-plugins-validation(?:\.git)?@)"
    r"(?P<ref>[^\s'\"#]+)"
)
# A `v<semver>` tag: v + MAJOR.MINOR.PATCH, optional pre-release / build metadata.
_CPV_VALID_SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")
# A 7-40 hex commit SHA (abbreviated or full).
_CPV_VALID_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _cpv_ref_is_valid(ref: str) -> bool:
    """Return True when ``ref`` is a CPV ref that actually resolves.

    Valid = ``master`` (CPV's default branch), a ``v<semver>`` tag, or a 7-40
    hex commit SHA. Everything else (``main`` / ``develop`` / ``HEAD`` / a
    branch name) is treated as stale. This is the EXACT CIP-6 rule; keeping it
    here lets the non-force re-pin and the CIP-6 detector agree without an
    import dependency.
    """
    if ref == "master":
        return True
    if _CPV_VALID_SEMVER_TAG_RE.match(ref):
        return True
    return bool(_CPV_VALID_SHA_RE.match(ref))


def _resolved_cpv_ref() -> str:
    """Return the CPV ref the scaffolding CPV would pin (``_default_cpv_ref()``).

    Imported lazily (like the other generate_plugin_repo callsites in this
    module) so importing standardize_plugin never eagerly pulls the generator.
    Falls back to ``master`` if the generator is somehow unavailable — the same
    conservative default the generator itself uses (``_FALLBACK_CPV_REF``).
    """
    try:
        from generate_plugin_repo import _default_cpv_ref

        ref = _default_cpv_ref().strip()
        return ref or "master"
    except Exception:
        return "master"


def _repin_workflow_text(content: str, resolved: str) -> tuple[str, set[str]]:
    """Rewrite every STALE CPV ref in ``content`` to ``@{resolved}``.

    Returns ``(new_content, stale_refs)`` where ``stale_refs`` is the set of the
    invalid refs that were replaced (empty when none). A VALID ref (``master`` /
    ``v<semver>`` / SHA) is left exactly as-is — that is the two-sided guarantee:
    a correctly-pinned workflow comes back byte-identical with an empty set.
    Kept as a free function (not an in-loop closure) so the per-file replacement
    binds ``resolved`` cleanly and is unit-testable on raw text.
    """
    stale_refs: set[str] = set()

    def _replace(m: re.Match[str]) -> str:
        ref = m.group("ref")
        if _cpv_ref_is_valid(ref):
            return m.group(0)
        stale_refs.add(ref)
        return f"{m.group('prefix')}{resolved}"

    return _CPV_REF_PIN_RE.sub(_replace, content), stale_refs


def repin_stale_cpv_ref(plugin_path: Path, dry_run: bool = False) -> list[str]:
    """Re-pin a STALE/INVALID ``claude-plugins-validation@<ref>`` in every
    ``.github/workflows/*.yml`` to the current resolved CPV ref.

    TRDD-HZSI0BZ6. Runs on ANY ``--fix`` (force or not). For each workflow file
    it rewrites ONLY the CPV ref occurrences whose ref is invalid per
    ``_cpv_ref_is_valid``; a valid ref (``master`` / ``v<semver>`` / SHA) is
    left untouched, so a correctly-pinned workflow is never rewritten (two-sided
    by construction). No other content of the workflow is modified — this is a
    surgical in-place re-pin, not a template overwrite.

    Returns a list of human-readable notes, or [] when nothing was stale.
    """
    workflows_dir = plugin_path / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []
    resolved = _resolved_cpv_ref()
    notes: list[str] = []
    for wf in sorted(workflows_dir.glob("*.yml")):
        if not wf.is_file():
            continue
        try:
            content = wf.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_content, stale_refs = _repin_workflow_text(content, resolved)
        if not stale_refs:
            continue
        rel = wf.relative_to(plugin_path)
        stale_list = ", ".join(f"@{r}" for r in sorted(stale_refs))
        if dry_run:
            notes.append(f"[dry-run] would re-pin stale CPV ref ({stale_list}) → @{resolved} in {rel}")
            continue
        wf.write_text(new_content, encoding="utf-8")
        notes.append(f"re-pinned stale CPV ref ({stale_list}) → @{resolved} in {rel}")
    return notes


# =============================================================================
# Issue #165 — inject the DEPENDENCY-RESOLUTION TAG stage into an EXISTING publish.py
# =============================================================================
# Since Claude Code 2.1.110 a version-constrained plugin dependency
# (`{"name": "x", "version": ">=1.2"}`) is resolved by listing the dependency
# repo's tags, keeping ONLY those starting with `{plugin-name}--v`, and fetching
# the highest one satisfying the range. The plain `vX.Y.Z` tag is IGNORED by that
# resolver — so a plugin that publishes only `vX.Y.Z` CANNOT BE DEPENDED UPON:
# every dependent fails to install with `no-matching-tag` and is DISABLED.
#
# CPV's GENERATED publish.py has minted that tag since v2.156.0
# (generate_plugin_repo.gen_publish_py). But standardize is deliberately
# PROFILE-AWARE (issues #145 / #140) and REFUSES to overwrite an EXISTING
# scripts/publish.py — so every plugin that ALREADY has one (i.e. every plugin
# that would need this migration) is standardized WITHOUT ever gaining the stage.
# There was no upgrade path short of `--force-templates`, which is precisely the
# thing a customized/ahead-of-canon plugin cannot safely run.
#
# This closes that gap the same way `repin_stale_cpv_ref` (above) closes the
# stale-@main one: detect → ONE targeted in-place edit → report. It runs on ANY
# `--fix`, never force-overwrites publish.py, and is a no-op on a publish.py that
# already carries the stage (idempotent by the detection predicate below).

# Detection predicate — the ONE definition lives in `cpv_validation_common.
# publish_py_creates_dependency_tag`, shared with `validate_plugin.
# check_dependency_resolution_tags` (the RC-DEP-TAG-PIPELINE signal). Two hand-synced
# copies drifted before (issue #167): this one keyed on the VARIABLE NAME
# (`dependency_tag` / `dep_tag`), so a publish.py that builds the tag correctly but
# names it `resolver_tag` read as "never mints the tag" — and the migration would then
# inject a SECOND stage into a file that already had one, pushing two tag refs. The
# SSOT keys on the CONSTRUCTION SHAPE (`--v` + a format/concat token) instead, so the
# author's choice of variable name cannot change the verdict.
#
# THE FOUR RELEASE-PUSH SHAPES IN THE WILD (measured across the real fleet, #167) —
# any regex pinned to one of them silently skips the rest, and a silent skip is the
# exact defect this migration exists to fix:
#
#   A  ["git", "push", "--atomic", "origin", "HEAD", tag, dep_tag]   (already migrated)
#   B  ["git", "push", "--atomic", "origin", "HEAD", tag, resolver_tag]  (ditto, other name)
#   C  ["git", "push", "--atomic", "origin", "HEAD", tag]
#   D  ["git", "push", "origin", "HEAD"] THEN ["git", "push", "origin", f"v{new_version}"]
#
# So the anchor is found with the AST, not a regex: locate the `git push` argv list
# that carries the RELEASE TAG ref (a `*tag*` variable, or an `f"v{...}"` literal) and
# extend THAT list. Shape D's first call pushes only HEAD and is correctly ignored.

# A `git push` argv element that names the release tag.
_TAG_NAME_HINT = "tag"
# A `git push` flag that pushes refs in BULK — there is no single ref list to extend,
# so such a call is never a migration anchor.
_BULK_PUSH_FLAGS: frozenset[str] = frozenset({"--tags", "--follow-tags", "--mirror", "--all"})

# The names the injected helper call is bound to, in priority order. DETECTED from the
# push's real scope (params, locals assigned above it, and module globals) — never
# assumed. The pre-#167 code demanded parameters literally named `root` and `new_ver`,
# which refused every vintage that names them otherwise (`new_version`, `REPO_ROOT`) or
# pushes from a parameterless `main()`.
#
# ROOT: the helper READS `<root>/.claude-plugin/plugin.json`, so the MANIFEST-bearing
# root is what it needs. `plugin_root` is therefore preferred over `git_root` in the
# two-root vintages: the manifest lives under the plugin root, while git works from ANY
# directory inside the repo (it discovers `.git` upward). The manifest-bearing root is
# correct for BOTH uses; the git root is correct for only one.
_ROOT_NAME_PRIORITY: tuple[str, ...] = ("plugin_root", "root", "repo_root", "git_root")
# VERSION: the version BEING RELEASED, so `new_ver`/`new_version` outrank a bare,
# possibly-ambient `version`.
_VERSION_NAME_PRIORITY: tuple[str, ...] = ("new_ver", "new_version", "version", "ver")

# The stage, injected verbatim at module level of the plugin's publish.py. It is
# SELF-CONTAINED (needs only json / subprocess / Path, all of which the caller
# proves are imported) so it cannot depend on a helper a given publish.py vintage
# happens to lack. Written to be ruff-clean under the canonical line-length.
_DEP_TAG_STAGE_SOURCE = '''\
# ── DEPENDENCY-RESOLUTION TAG (added by `cpv standardize --fix`, CPV issue #165) ──
# Since Claude Code 2.1.110 a version-constrained dependency on this plugin
# ({"name": "<this-plugin>", "version": ">=1.2"}) is resolved by listing THIS repo's
# tags, keeping ONLY those starting with "<this-plugin>--v", and fetching the highest
# one satisfying the range. The plain vX.Y.Z tag is IGNORED by that resolver.
#
# So without the tag below, releases of this plugin are UN-DEPENDABLE: every dependent
# fails to install with `no-matching-tag` and is DISABLED. The breakage is invisible
# from the depending side (an already-installed dependent keeps working), which is how
# it stayed hidden for months in the wild — do NOT remove this stage because "nothing
# seems to need it". NOTE the separator is a DOUBLE hyphen (`--v`); the single-hyphen
# `-v` form seen on some ecosystem tags matches the resolver's prefix filter and is
# therefore silently useless.


def _cpv_dependency_tag_name(root: Path, new_ver: str) -> str | None:
    """The `{plugin-name}--v{version}` tag Claude Code resolves dependencies against.

    The name is read from the MANIFEST, never from the directory name, so renaming
    the checkout (or the plugin) cannot silently desync the tag from the plugin it
    names. Returns None when the name is unreadable — the caller then WARNS loudly
    rather than inventing a name, because a SILENT skip is exactly how this defect
    survived unnoticed across many releases.
    """
    manifest = root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return None
    try:
        name = json.loads(manifest.read_text(encoding="utf-8")).get("name")
    except (json.JSONDecodeError, OSError):
        return None
    return f"{name}--v{new_ver}" if name else None


def _cpv_dependency_push_refs(root: Path, new_ver: str) -> list[str]:
    """Create the dependency-resolution tag locally and return it as a push-ref list.

    Idempotent: an existing tag is left alone. Returns [] when the tag cannot be
    built, so the release still pushes rather than crashing the pipeline.

    It is called from INSIDE the release push's argv so the tag lands in the SAME
    push as the release tag — a release can never ship with one ref and not the other.
    """
    dep_tag = _cpv_dependency_tag_name(root, new_ver)
    if dep_tag is None:
        print(
            "  WARNING: cannot read the plugin name from .claude-plugin/plugin.json "
            "- SKIPPING the dependency tag. Plugins depending on this one will fail "
            "to resolve this release with `no-matching-tag`."
        )
        return []
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{dep_tag}"],
        capture_output=True,
        text=True,
        cwd=str(root),
        check=False,
        timeout=10,
    )
    if probe.returncode != 0:
        subprocess.run(
            ["git", "tag", "-a", dep_tag, "-m", dep_tag.replace("--v", " ")],
            cwd=str(root),
            check=True,
            timeout=10,
        )
        print(f"  Dependency tag {dep_tag} created.")
    return [dep_tag]


'''


def _publish_py_has_dependency_tag_stage(text: str) -> bool:
    """True when this publish.py ALREADY mints the `{name}--v{ver}` dependency tag.

    Delegates to the SSOT predicate (`cpv_validation_common.
    publish_py_creates_dependency_tag`) so the migration and the validator can never
    disagree about whether a file needs the stage.

    The idempotence gate: a second `--fix` run sees the stage it injected and does
    nothing. It recognises the CANONICAL v2.156+ stage AND any hand-rolled equivalent
    under ANY variable name, so a plugin that already ships the tag is never
    double-patched into pushing two conflicting refs (issue #167).

    Imported lazily, matching this module's established pattern (`main()` does the
    same) — `cpv_validation_common` is only importable once `scripts/` is on the path.
    """
    from cpv_validation_common import publish_py_creates_dependency_tag

    return publish_py_creates_dependency_tag(text)


def _publish_py_creates_release_tag(text: str) -> bool:
    """True when this publish.py creates a release tag at all.

    Mirrors validate_plugin's `creates_plain_tag`. A publish.py that tags nothing
    has no release to make dependable — there is nothing to migrate, so we leave
    it completely alone rather than inventing a tagging stage it never had.
    """
    return '"git", "tag"' in text or "git tag -a" in text


def _unmigratable_note(reason: str) -> str:
    """The report line for a publish.py this migration will NOT touch.

    ALWAYS non-empty: a silent skip is the exact defect #165/#167 exist to fix, so an
    unrecognised file must be LOUD. It names the reason and tells the maintainer how to
    add the stage by hand — the two shortcuts a reader would otherwise reach for are
    BOTH wrong, so they are called out explicitly rather than left to be rediscovered:
    `--force-templates` overwrites a customized publish.py (which is precisely why such
    a plugin cannot run it), and `claude plugin tag` takes a plugin PATH, not a tag
    name, so it silently mints nothing.
    """
    return (
        "scripts/publish.py lacks the dependency-resolution tag ({name}--v{version}) "
        "and CANNOT be migrated automatically — " + reason + ". Its releases are "
        "un-dependable. ADD THE STAGE BY HAND: read `name` from "
        ".claude-plugin/plugin.json, build the `{name}--v{version}` ref, create it with "
        "`git tag -a`, and push it in the SAME `git push` as the release tag. Do NOT "
        "run `standardize --force-templates` (it OVERWRITES a customized publish.py) "
        "and do NOT run `claude plugin tag` (it takes a plugin PATH, not a tag name, so "
        "it will not mint this ref)."
    )


def _abs_offset(lines: list[str], lineno: int, col_offset: int) -> int:
    """Absolute character index in the source for an AST ``(lineno, col_offset)``.

    ``ast`` column offsets are UTF-8 BYTE offsets within their line, so a line carrying
    a non-ASCII character (these files are full of em-dashes) would splice at the wrong
    place if the value were used as a character index. Decoding the byte prefix converts
    it back to a character count.
    """
    line_start = sum(len(line) for line in lines[: lineno - 1])
    prefix = lines[lineno - 1].encode("utf-8")[:col_offset].decode("utf-8")
    return line_start + len(prefix)


def _is_release_tag_push(node: ast.AST) -> TypeGuard[ast.List]:
    """True when ``node`` is a ``["git", "push", ...]`` argv list carrying the release tag.

    The tag ref is either a variable whose name contains `tag` (shapes A/B/C) or an
    ``f"v{...}"`` literal (shape D's second call). A bulk-push flag disqualifies the
    call: `git push --tags` has no per-ref list to extend, so extending it would be a
    no-op that still reported success.
    """
    if not isinstance(node, ast.List) or len(node.elts) < 2:
        return False
    head = node.elts[:2]
    if not all(isinstance(e, ast.Constant) and e.value == want for e, want in zip(head, ("git", "push"))):
        return False
    has_tag_ref = False
    for elt in node.elts[2:]:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            if elt.value in _BULK_PUSH_FLAGS:
                return False
            continue
        if isinstance(elt, ast.Name) and _TAG_NAME_HINT in elt.id.lower():
            has_tag_ref = True
        elif isinstance(elt, ast.JoinedStr):
            lead = elt.values[0] if elt.values else None
            if isinstance(lead, ast.Constant) and isinstance(lead.value, str) and lead.value.startswith("v"):
                has_tag_ref = True
    return has_tag_ref


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every parameter name bound by ``func``."""
    a = func.args
    names = {arg.arg for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    for extra in (a.vararg, a.kwarg):
        if extra is not None:
            names.add(extra.arg)
    return names


def _stored_names(node: ast.AST, before_lineno: int | None = None) -> set[str]:
    """Names ``node`` binds by assignment (optionally: only those above ``before_lineno``).

    Covers every binding form at once — plain/annotated/augmented assignment, `for`
    targets, `with ... as`, walrus — because they all surface as a `Name` in a `Store`
    context.
    """
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            if before_lineno is None or sub.lineno < before_lineno:
                names.add(sub.id)
    return names


def _module_level_names(tree: ast.Module) -> set[str]:
    """Module-level (global) names. They are bound at import, so they are always in
    scope at the push regardless of where in the file they are written."""
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        names |= _stored_names(stmt)
    return names


def _pick_name(priority: tuple[str, ...], available: set[str]) -> str | None:
    """The highest-priority name actually in scope, matched case-insensitively.

    Case-insensitive so a module constant (`REPO_ROOT`) matches the same slot as a
    parameter (`repo_root`); the ORIGINAL spelling is returned, since that is what has
    to appear in the emitted call.
    """
    lowered = {name.lower(): name for name in sorted(available)}
    for want in priority:
        if want in lowered:
            return lowered[want]
    return None


def _inject_dependency_tag_stage(text: str) -> tuple[str | None, str]:
    """Inject the dependency-tag stage into ``text`` (a publish.py source).

    Returns ``(new_text, note)``. ``new_text`` is None when the file must NOT be
    rewritten — either because nothing needs doing (empty note), or because the file's
    shape is not recognisable and a partial edit would be worse than none (FAIL-FAST: we
    never half-migrate a release pipeline; the note is then always non-empty and LOUD).

    EXACTLY TWO edits, no more:
      1. the two helpers, inserted at module level above the function that pushes;
      2. the release push's argv list, extended with the helper's ref list.

    Nothing else in the plugin's file is touched.
    """
    if not _publish_py_creates_release_tag(text):
        return None, ""
    if _publish_py_has_dependency_tag_stage(text):
        return None, ""

    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return None, _unmigratable_note(f"it does not parse as Python ({exc.msg})")

    # The injected helpers call json / subprocess and annotate with Path. A publish.py
    # missing any of those imports would not even compile after the edit — refuse.
    missing = [
        mod
        for mod, needle in (("json", "import json"), ("subprocess", "import subprocess"), ("Path", "import Path"))
        if needle not in text
    ]
    if missing:
        return None, _unmigratable_note(f"it does not import {', '.join(missing)}")

    # Exactly ONE release-push target, or we refuse: with several equally-plausible
    # anchors there is no way to know which push ships the release, and extending the
    # wrong one would report success while still shipping an un-dependable release.
    pushes = [node for node in ast.walk(tree) if _is_release_tag_push(node)]
    if len(pushes) != 1:
        found = "no" if not pushes else str(len(pushes))
        return None, _unmigratable_note(
            f"its release-push shape was not recognised — {found} `git push` argv carrying "
            "the release tag found, expected exactly 1"
        )
    push = pushes[0]
    push_lineno = push.lineno

    # The enclosing MODULE-LEVEL def: the helpers go above it, so they land at module
    # level (they must, to be callable) and below the module's own imports (they must,
    # to be importable).
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            if stmt.lineno <= push_lineno <= (stmt.end_lineno or stmt.lineno):
                enclosing = stmt
                break
    if enclosing is None:
        return None, _unmigratable_note("the release push is not inside a module-level function")

    # A decorator sits ABOVE its `def`; inserting between the two is a syntax error.
    anchor_lineno = min([d.lineno for d in enclosing.decorator_list] + [enclosing.lineno])
    if anchor_lineno <= 1:
        return None, _unmigratable_note(
            "the release push's function starts at line 1, leaving no room to insert the "
            "helpers below the module's imports"
        )

    # The names the emitted call binds to, DETECTED from the push's real scope: the
    # params and locals of every function enclosing it (nested defs included), plus the
    # module globals. Nothing is assumed about how this vintage spells them.
    scope: set[str] = _module_level_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.lineno <= push_lineno <= (node.end_lineno or node.lineno):
                scope |= _param_names(node)
                scope |= _stored_names(node, before_lineno=push_lineno)

    root_name = _pick_name(_ROOT_NAME_PRIORITY, scope)
    version_name = _pick_name(_VERSION_NAME_PRIORITY, scope)
    absent = [
        f"no {label} name (expected one of: {', '.join(priority)})"
        for label, value, priority in (
            ("root", root_name, _ROOT_NAME_PRIORITY),
            ("version", version_name, _VERSION_NAME_PRIORITY),
        )
        if value is None
    ]
    if absent:
        return None, _unmigratable_note("the release push's scope has " + " and ".join(absent))

    lines = text.splitlines(keepends=True)
    end = _abs_offset(lines, push.end_lineno or push_lineno, push.end_col_offset or 0)
    new_text = text[:end] + f" + _cpv_dependency_push_refs({root_name}, {version_name})" + text[end:]

    # The argv edit adds no NEWLINE, so the original line numbering still holds — but
    # re-splitting keeps the two edits order-independent regardless.
    new_lines = new_text.splitlines(keepends=True)
    insert_idx = anchor_lineno - 1
    # Insert ABOVE any comment block glued to the def, so we never orphan a comment
    # from the function it documents.
    while insert_idx > 0 and new_lines[insert_idx - 1].startswith("#"):
        insert_idx -= 1
    new_lines.insert(insert_idx, _DEP_TAG_STAGE_SOURCE)
    return "".join(new_lines), (
        "injected the dependency-resolution tag stage ({name}--v{version}) into "
        "scripts/publish.py — releases were un-dependable without it (CC 2.1.110+)"
    )


def migrate_publish_py_dependency_tag(plugin_path: Path, dry_run: bool = False) -> list[str]:
    """Give an EXISTING ``scripts/publish.py`` the ``{name}--v{ver}`` tag stage (#165).

    Runs on ANY ``--fix`` — NOT gated behind ``--force-templates``, because the
    plugins that need this are exactly the ones that cannot safely force-template
    (a customized or ahead-of-canon publish.py). publish.py is never overwritten:
    this is a surgical in-place injection of ONE stage, in the idiom of
    ``repin_stale_cpv_ref``.

    Idempotent: a publish.py that already mints the tag (canonical or previously
    injected) comes back byte-identical and reports nothing.
    """
    publish = plugin_path / "scripts" / "publish.py"
    if not publish.is_file():
        return []
    try:
        text = publish.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    new_text, note = _inject_dependency_tag_stage(text)
    if new_text is None:
        # A note with no rewrite = the shape is unrecognised (surfaced so the
        # maintainer can act). An empty note = genuinely nothing to do.
        return [note] if note else []
    if dry_run:
        return [f"[dry-run] would {note}"]
    publish.write_text(new_text, encoding="utf-8")
    return [note]


# =============================================================================
# Issue #165 — MERGE (never clobber) the canon config files under --force-templates
# =============================================================================
# `--force-templates` blind-overwrites the shared-canon config files, which DELETES
# a plugin's own linter suppressions AND the rationale comments justifying them.
# Issue #145 fixed exactly one symptom (MD025); the general class stayed open. Two
# real cases from a migrating plugin:
#
#   * .markdownlint.json — `"MD010": {"code_blocks": false}` was deleted. It is
#     LOAD-BEARING: that plugin's skill documents Makefile recipes, which REQUIRE
#     literal tabs; without the suppression markdownlint blocks `--strict` forever.
#   * .mega-linter.yml — a `CKV_DOCKER_2` skip was dropped along with the 8-line
#     comment explaining why it is safe.
#
# So: merge canon IN, keep the plugin's own keys. For JSON that is exact (no
# comments to lose). For YAML it is NOT — a round-trip-safe merge needs a
# comment-preserving loader (ruamel.yaml), which is NOT a CPV dependency and will
# not be added for this. Instead, a YAML canon file carrying CUSTOM top-level keys
# is SKIPPED (left byte-identical) rather than silently stripped: losing the
# author's suppression is a worse outcome than missing a canon refresh, and the
# skip is visible.

# Canon YAML files whose custom keys must never be clobbered.
_CANON_YAML_MERGE_FILES: frozenset[str] = frozenset({".mega-linter.yml"})

# A top-level YAML mapping key (column 0, no leading space). Deliberately shallow:
# it is all that can be judged reliably without a real YAML parser, and it is what
# distinguishes an author's added config block from a canon one.
_YAML_TOP_LEVEL_KEY_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_.\-]*)\s*:")


def _yaml_top_level_keys(text: str) -> list[str]:
    """The top-level mapping keys of ``text``, in order, without a YAML parser."""
    keys: list[str] = []
    for line in text.splitlines():
        m = _YAML_TOP_LEVEL_KEY_RE.match(line)
        if m:
            keys.append(m.group("key"))
    return keys


def _yaml_key_blocks(text: str) -> dict[str, str]:
    """Map each top-level key to its FULL block: leading comments + key + body.

    A key's "block" starts at the first line of the comment paragraph directly
    above it (an unbroken run of ``#`` lines) and runs to just before the next
    top-level key. The leading comments are part of the block ON PURPOSE — a
    canon key is worthless without the rationale that explains it, and an
    author's rationale is exactly what issue #165 was about losing.
    """
    lines = text.splitlines()
    key_at: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        m = _YAML_TOP_LEVEL_KEY_RE.match(line)
        if m:
            key_at.append((idx, m.group("key")))

    blocks: dict[str, str] = {}
    for pos, (idx, key) in enumerate(key_at):
        # Walk UP over the unbroken run of comment lines directly above the key.
        start = idx
        while start > 0:
            above = lines[start - 1].strip()
            if above.startswith("#"):
                start -= 1
                continue
            break
        # Never swallow a comment paragraph that belongs to the PREVIOUS key's
        # body (i.e. one that starts before that key's own line).
        if pos > 0:
            start = max(start, key_at[pos - 1][0] + 1)
        end = key_at[pos + 1][0] if pos + 1 < len(key_at) else len(lines)
        # Trim the trailing blank lines that separate blocks.
        while end > idx + 1 and not lines[end - 1].strip():
            end -= 1
        blocks.setdefault(key, "\n".join(lines[start:end]))
    return blocks


def _merge_canon_yaml(plugin_text: str, canon_text: str) -> tuple[str, list[str], list[str]]:
    """Merge canon INTO a plugin's YAML config using the PLUGIN file as the base.

    Returns ``(merged_text, kept_keys, added_keys)``.

    THE DIRECTION IS THE WHOLE FIX (issue #165). The obvious merge — start from
    the canon file and port the plugin's bits across — cannot preserve comments
    without a round-trip YAML loader (``ruamel.yaml``, which CPV does not depend
    on). Inverting the base removes the need for one entirely: we start from the
    PLUGIN's own file and only APPEND the canon keys it is missing. Every byte the
    author wrote — values, key order, and the comment paragraphs justifying them —
    survives because we never rewrite a line they own.

    A canon key the plugin ALREADY declares is left exactly as the plugin has it,
    even when the value differs. That is deliberate: we cannot distinguish "the
    author customized this" from "this is an older canon value", and the real case
    proves which way to err — the maintainer plugin extended canon's
    ``REPOSITORY_CHECKOV_ARGUMENTS: "--skip-check CKV2_GHA_1"`` to
    ``"...,CKV_DOCKER_2"`` because every Dockerfile it ships is an ephemeral
    run-once container for which a HEALTHCHECK is meaningless. Overwriting that
    value re-breaks their lint gate. Missing a canon refresh is an inconvenience;
    silently deleting a load-bearing suppression is a broken build. The kept keys
    are RETURNED so the caller can name them and the author can reconcile.
    """
    plugin_keys = _yaml_top_level_keys(plugin_text)
    plugin_key_set = set(plugin_keys)
    canon_blocks = _yaml_key_blocks(canon_text)
    plugin_blocks = _yaml_key_blocks(plugin_text)

    added: list[str] = []
    kept: list[str] = []
    for key in _yaml_top_level_keys(canon_text):
        if key in plugin_key_set:
            if plugin_blocks.get(key, "").strip() != canon_blocks.get(key, "").strip():
                kept.append(key)
        elif key not in added:
            added.append(key)

    if not added:
        return plugin_text, kept, []

    body = plugin_text.rstrip("\n")
    appended = "\n\n".join(canon_blocks[k] for k in added)
    merged = (
        f"{body}\n\n"
        "# --- Added by `cpv standardize --force-templates` (canonical pipeline) ---\n"
        f"{appended}\n"
    )
    return merged, kept, added


def _merge_canon_json(plugin_text: str, canon_text: str) -> tuple[str | None, list[str]]:
    """Merge canon INTO a plugin's JSON config, preserving the plugin's own keys.

    Returns ``(merged_text, preserved_keys)``. ``merged_text`` is None when the
    plugin's file is not a JSON object (nothing to preserve — the caller then
    overwrites with canon, which is the pre-existing behaviour and an improvement
    on a broken config).

    Canon WINS on a key canon declares — that is the point of `--force-templates`,
    and a plugin that deliberately diverges on a canon key has
    `cpv.pipeline.intentional_divergence` / the at-or-ahead-of-canon skip for that.
    A key canon does NOT declare is the plugin's OWN (e.g. an `MD010` suppression a
    skill's Makefile recipes depend on) and is carried over verbatim.
    """
    try:
        plugin_obj = json.loads(plugin_text)
        canon_obj = json.loads(canon_text)
    except json.JSONDecodeError:
        return None, []
    if not isinstance(plugin_obj, dict) or not isinstance(canon_obj, dict):
        return None, []

    merged = dict(canon_obj)
    preserved: list[str] = []
    for key, value in plugin_obj.items():
        if key not in canon_obj:
            merged[key] = value
            preserved.append(key)
    return json.dumps(merged, indent=2) + "\n", preserved


# =============================================================================
# CIP-1 MIGRATION — drop the INVERTED `CLAUDE_PRIVATE_USERNAMES` CI env (#140)
# =============================================================================
# CI-failure forensics 2026-07-13 (RC-2, 3 failures across 2 repos):
#
#     [CRITICAL] Private path leaked: username 'emasoft' in path - 'Emasoft'
#     SUMMARY: CRITICAL=22 …  →  Validate job FAILS under --strict
#
# An old canonical template set, on the CPV validate step:
#
#     CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}
#
# That env names the PRIVATE usernames CPV must FLAG as leaks. Setting it to the
# PUBLIC repo owner therefore told CPV to flag every legitimate
# `github.com/<owner>/…` URL and the owner's no-reply email as a private-path
# leak → 22 false CRITICALs → red CI, forever, on every legacy repo. The
# generator dropped the line in v2.137.1, but a repo migrated before that keeps
# it: CIP-1 only DETECTS it. This migrator REMOVES it.
#
# THE FIX IS DELETION, not correction: a CI runner has no developer
# local-username to protect, so the correct canonical value is *no line at all*
# (`PLUGIN_SKIP_GITHUB_INTEGRITY: '1'` stays). The leak rule itself is NOT
# weakened — it keeps firing on a genuine leak; we are removing a *misconfigured
# input* that was feeding it the wrong username list.
#
# TWO-SIDED BY CONSTRUCTION — the regex is anchored to the YAML mapping form
# `CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}` (the CIP-1 detector's
# exact shape, kept in sync here by construction, not by import — the CIP-6
# precedent). The CORRECT LOCAL idiom `CLAUDE_PRIVATE_USERNAMES="$(whoami)"` is a
# SHELL ASSIGNMENT (`=`, no `: ${{ … }}`) and can never match, so a workflow — or
# a docs/`run:` block — carrying the local scan idiom is left byte-identical.

# The whole LINE, so the removal is line-exact. `(?m)` + `^…$` anchor it to a
# standalone YAML mapping entry; a trailing comment is tolerated. re2-safe.
_INVERTED_PRIVATE_USERNAMES_LINE_RE = re.compile(
    r"(?m)^[ \t]*CLAUDE_PRIVATE_USERNAMES[ \t]*:[ \t]*\$\{\{[ \t]*github\.repository_owner[ \t]*\}\}[ \t]*(?:#[^\n]*)?$"
)
# A YAML `env:` block opener (the only parent we will ever remove, and only when
# dropping the inverted line would leave it childless — a childless `env:` is a
# null mapping GitHub Actions rejects).
_ENV_BLOCK_RE = re.compile(r"^[ \t]*env[ \t]*:[ \t]*(?:#[^\n]*)?$")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _strip_inverted_private_usernames(text: str) -> tuple[str, int]:
    """Remove every inverted ``CLAUDE_PRIVATE_USERNAMES`` line from a workflow.

    Returns ``(new_text, removed_count)``. When removing the line would leave its
    parent ``env:`` mapping with no keys at all, the ``env:`` opener (and any
    comment lines that belonged only to it) is removed too — a childless ``env:``
    is a null value the Actions schema rejects, so a "fix" that produced one
    would trade a validation failure for a syntax failure.

    A workflow with no inverted line comes back byte-identical with a count of 0
    — the positive-control half of the two-sided guarantee.
    """
    lines = text.splitlines(keepends=True)
    drop: set[int] = {
        i for i, line in enumerate(lines) if _INVERTED_PRIVATE_USERNAMES_LINE_RE.match(line.rstrip("\r\n"))
    }
    removed = len(drop)
    if not removed:
        return text, 0

    for i in sorted(drop):
        indent = _indent_of(lines[i])
        # Walk up to the nearest line at a SHALLOWER indent — the block's parent.
        parent: int | None = None
        for j in range(i - 1, -1, -1):
            stripped = lines[j].strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _indent_of(lines[j]) < indent:
                parent = j
                break
        if parent is None or not _ENV_BLOCK_RE.match(lines[parent].rstrip("\r\n")):
            continue
        # Does the env: block keep any (non-dropped, non-comment) key?
        p_indent = _indent_of(lines[parent])
        block: list[int] = []
        survivors = 0
        for k in range(parent + 1, len(lines)):
            stripped = lines[k].strip()
            if not stripped:
                continue
            if _indent_of(lines[k]) <= p_indent:
                break
            block.append(k)
            if k not in drop and not stripped.startswith("#"):
                survivors += 1
        if survivors == 0:
            drop.add(parent)
            drop.update(k for k in block if lines[k].strip().startswith("#"))

    return "".join(line for i, line in enumerate(lines) if i not in drop), removed


def remove_inverted_private_usernames(plugin_path: Path, dry_run: bool = False) -> list[str]:
    """CIP-1 MIGRATION: drop the inverted ``CLAUDE_PRIVATE_USERNAMES`` env from
    every ``.github/workflows/*.yml|*.yaml``.

    Runs on ANY ``--fix`` (force or not): ``--force-templates`` regenerates
    ci.yml / release.yml and so drops it there, but a plain ``--fix`` never
    touches an existing workflow, and a NON-canonical workflow (one not in
    ``_FORCE_TEMPLATE_FILES``) keeps the defect even under ``--force-templates``.
    This surgical rewrite closes both gaps.

    Surgical: ONLY the offending line (plus a parent ``env:`` that the removal
    would leave childless) is touched. A workflow without the inverted env is
    left byte-identical — never rewritten, never reformatted.

    Returns human-readable notes, or [] when nothing was inverted.
    """
    notes: list[str] = []
    for wf in _workflow_files(plugin_path):
        try:
            content = wf.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_content, removed = _strip_inverted_private_usernames(content)
        if not removed or new_content == content:
            continue
        rel = wf.relative_to(plugin_path)
        what = f"{removed} inverted CLAUDE_PRIVATE_USERNAMES line(s)"
        if dry_run:
            notes.append(
                f"[dry-run] would remove {what} from {rel} — it sets the env to the PUBLIC "
                f"repo owner, but that env lists PRIVATE usernames to redact, so CPV flags "
                f"every owner GitHub URL as a leak and CI fails under --strict (#140)"
            )
            continue
        wf.write_text(new_content, encoding="utf-8")
        notes.append(f"removed {what} from {rel} (#140 — CI has no local username to protect)")
    return notes


def audit_inverted_private_usernames(plugin_path: Path) -> list[AuditItem]:
    """Audit the CIP-1 inverted-env defect (#140) — WARN-only, never mutates.

    Surfaces whatever ``remove_inverted_private_usernames(..., dry_run=True)``
    would do, so the audit text and the --fix behaviour cannot drift (the
    ``audit_jscpd_config`` pattern).
    """
    notes = remove_inverted_private_usernames(plugin_path, dry_run=True)
    if not notes:
        return [AuditItem("ci-env", ".github/workflows", "PASS", "no inverted CLAUDE_PRIVATE_USERNAMES env")]
    return [AuditItem("ci-env", ".github/workflows", "WARN", note) for note in notes]


# Issue #143: the canonical local pre-push gate (`publish.py --gate`) gains a
# jscpd copy-paste check at PARITY with CI's Mega-Linter COPYPASTE_JSCPD. Both
# the local gate and CI read ONE source-of-truth config — `.jscpd.json` — so the
# threshold (5%) and the ignore globs are identical on both sides (jscpd
# auto-discovers `.jscpd.json` at the repo root). Standardize must provision this
# file for an existing adopter plugin so the gate it runs has a config to read.
#
# This canonical content is kept HERE as standardize's own copy (NOT imported
# from generate_plugin_repo) — exactly like _PROVISION_DEV_EXTRA — so a
# `standardize --fix` adoption and a freshly scaffolded plugin write the SAME
# .jscpd.json. The `ignore` globs mirror the canonical `.mega-linter.yml`'s
# FILTER_REGEX_EXCLUDE dirs (dev-submodules, fixtures, vendored trees); threshold
# 5 matches COPYPASTE_JSCPD_ARGUMENTS "--threshold 5".
_JSCPD_CONFIG_REL = ".jscpd.json"
_CANONICAL_JSCPD_CONFIG: dict[str, object] = {
    "threshold": 5,
    "minTokens": 50,
    "gitignore": True,
    "reporters": ["console"],
    "ignore": [
        "**/tests_dev/**",
        "**/docs_dev/**",
        "**/scripts_dev/**",
        "**/samples_dev/**",
        "**/examples_dev/**",
        "**/builds_dev/**",
        "**/downloads_dev/**",
        "**/libs_dev/**",
        "**/llm_externalizer_output/**",
        "**/.claude/**",
        "**/.tldr/**",
        "**/tests/fixtures/**",
        "**/test/fixtures/**",
        "**/spec/fixtures/**",
        "**/__fixtures__/**",
        "**/testdata/**",
        "**/fixtures/**",
        "**/node_modules/**",
        "**/.git/**",
    ],
}

# Markers proving a publish.py already carries the issue-#143 jscpd copy-paste
# gate (Gate 2b). A plugin whose publish.py predates the gate is SURFACED (audit
# WARN) so the adopter knows to refresh the template with --force-templates; we
# never silently rewrite their publish.py on a plain --fix.
_PUBLISH_JSCPD_GATE_MARKERS: tuple[str, ...] = ("jscpd", "copy-paste")


def _render_canonical_jscpd_config() -> str:
    """Render the canonical `.jscpd.json` content (2-space indent + trailing LF).

    A single renderer so the provisioned file and any test/assertion compare the
    SAME bytes. `json.dumps(indent=2)` produces the exact shape the canonical
    template + CI's Mega-Linter expect (jscpd auto-reads `.jscpd.json`).
    """
    return json.dumps(_CANONICAL_JSCPD_CONFIG, indent=2) + "\n"


def _publish_py_has_jscpd_gate(plugin_path: Path) -> bool:
    """Return True when the plugin's scripts/publish.py already carries the
    issue-#143 jscpd copy-paste gate (case-insensitive marker match).

    Returns False when publish.py is absent or unreadable — the caller only uses
    this to decide whether to SURFACE a "refresh publish.py" note, never to
    mutate, so a missing/unreadable file simply yields no note.
    """
    publish = plugin_path / "scripts" / "publish.py"
    if not publish.is_file():
        return False
    try:
        text = publish.read_text(encoding="utf-8").lower()
    except (OSError, UnicodeDecodeError):
        return False
    return any(marker in text for marker in _PUBLISH_JSCPD_GATE_MARKERS)


def provision_jscpd_config(plugin_path: Path, dry_run: bool = False) -> list[str]:
    """Provision the canonical `.jscpd.json` so the local `publish.py --gate`
    jscpd copy-paste check has the same config CI's Mega-Linter reads.

    Issue #143. Mirrors the #142 provisioners (identity-guarded, format-preserving,
    audit path WARN-only):

    * ``dry_run=False`` (the --fix path): CREATE `.jscpd.json` if ABSENT (write
      the canonical content). If it already exists, LEAVE it untouched — a user's
      tuned config is never clobbered on a plain --fix (the --force-templates
      template refresh is the only path that overwrites it).
    * ``dry_run=True`` (the AUDIT path): never mutate. Surface ".jscpd.json
      missing", and — when scripts/publish.py exists but lacks the jscpd gate —
      "publish.py lacks the jscpd copy-paste gate (run with --force-templates to
      refresh)".

    Returns a list of human-readable action/finding lines (empty when nothing is
    actionable: the config already exists AND publish.py already has the gate, or
    in dry-run the config exists AND there is no stale-publish.py note).
    """
    config = plugin_path / _JSCPD_CONFIG_REL
    notes: list[str] = []

    if dry_run:
        # AUDIT path — WARN-only, never write.
        if not config.is_file():
            notes.append(
                f"{_JSCPD_CONFIG_REL} missing — the local publish.py --gate "
                "jscpd copy-paste check (parity with CI Mega-Linter) has no "
                "config to read; run --fix to provision it"
            )
        # Surface a publish.py that predates the gate regardless of whether the
        # config is present (the gate code itself lives in publish.py).
        publish = plugin_path / "scripts" / "publish.py"
        if publish.is_file() and not _publish_py_has_jscpd_gate(plugin_path):
            notes.append(
                "scripts/publish.py lacks the jscpd copy-paste gate (issue #143) "
                "— run with --force-templates to refresh publish.py to parity "
                "with CI"
            )
        return notes

    # --fix path — create the config when absent; never clobber an existing one.
    if config.is_file():
        return []
    config.write_text(_render_canonical_jscpd_config(), encoding="utf-8")
    return [f"created {_JSCPD_CONFIG_REL} (jscpd copy-paste threshold 5, parity with CI)"]


# =============================================================================
# RC-1 / CIP-7 — the commitlint config a commitlint GATE cannot run without
# =============================================================================
# CI-failure forensics 2026-07-13: the single biggest ongoing red-signal source
# (4 failures, and one on EVERY future Dependabot PR across the whole fleet):
#
#     ✖ body's lines must not be longer than 100 characters [body-max-line-length]
#
# With NO commitlint config in the repo, `wagoid/commitlint-github-action` falls
# back to bare `@commitlint/config-conventional`, whose `body-max-line-length` is
# 100 — and Dependabot's machine-generated commit body embeds a long YAML
# dependency block. The generator now ships `.commitlintrc.json`
# (gen_commitlintrc_json) disabling that ONE cosmetic rule, but a MIGRATED repo
# gets the fixed ci.yml and no config, so RC-1 persists on every one of them.
#
# NOT force-templated ON PURPOSE. `.commitlintrc.json` is an author-owned config
# (they may add their own `type-enum`, `scope-enum`, …), and
# `_FORCE_TEMPLATE_FILES` is a blind overwrite — it would destroy those rules.
# This provisioner follows the `.jscpd.json` / `.cspell.json` precedent instead:
# CREATE when absent, AUGMENT when the one rule is simply not mentioned, and
# NEVER overwrite a value the author set deliberately.
#
# The gate is NOT weakened: `type-enum`, `subject-empty`, `header-max-length`,
# `type-case` … all stay enforced, so RC-5 (a genuinely non-conventional commit
# type) still fails CI exactly as before. Only the *body line length* of a
# machine-generated body — which carries zero signal — stops gating.
_COMMITLINT_CONFIG_REL = ".commitlintrc.json"
# Every config form commitlint auto-discovers. If the author owns one in ANOTHER
# form we leave the whole dimension alone (adding a second config would be
# ambiguous, and rewriting theirs would clobber it) — the `.cspell.json`
# precedent.
_COMMITLINT_CONFIG_NAMES: tuple[str, ...] = (
    ".commitlintrc.json",
    ".commitlintrc",
    ".commitlintrc.yml",
    ".commitlintrc.yaml",
    ".commitlintrc.js",
    ".commitlintrc.cjs",
    ".commitlintrc.mjs",
    ".commitlintrc.ts",
    "commitlint.config.js",
    "commitlint.config.cjs",
    "commitlint.config.mjs",
    "commitlint.config.ts",
)
# A workflow that actually RUNS commitlint. Without a commitlint gate there is
# nothing to configure, and shipping a config would be unrequested noise.
_COMMITLINT_GATE_RE = re.compile(r"(?i)(?:wagoid/commitlint-github-action|\bcommitlint\b[^\n]*--)")
_BODY_MAX_LINE_LENGTH = "body-max-line-length"
# Mirrors gen_commitlintrc_json; used only if the lazy generator import fails.
_FALLBACK_COMMITLINT_CONFIG = (
    '{\n  "extends": ["@commitlint/config-conventional"],\n'
    '  "rules": {\n    "' + _BODY_MAX_LINE_LENGTH + '": [0]\n  }\n}\n'
)


def _render_canonical_commitlintrc() -> str:
    """The canonical `.commitlintrc.json`, from the generator (single source of
    truth), so a scaffolded plugin and a migrated one ship the same file.

    ``gen_commitlintrc_json`` ignores its ``PluginParams`` (the config carries no
    plugin-specific value), but the dataclass still has required fields — they are
    filled with inert placeholders that never reach the output. A test pins this
    against the fallback literal, so a generator change cannot silently desync the
    migrated file from the scaffolded one.
    """
    try:
        from generate_plugin_repo import PluginParams, gen_commitlintrc_json

        return gen_commitlintrc_json(
            PluginParams(name="placeholder", description="", author="", author_email="")
        )
    except Exception:
        return _FALLBACK_COMMITLINT_CONFIG


def _workflow_runs_commitlint(plugin_path: Path) -> bool:
    """Whether any workflow runs a commitlint gate."""
    for wf in _workflow_files(plugin_path):
        try:
            text = wf.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _COMMITLINT_GATE_RE.search(text):
            return True
    return False


def _existing_commitlint_config(plugin_path: Path) -> Path | None:
    """The commitlint config the repo already owns, if any.

    Includes the `package.json` → `"commitlint"` key form (commitlint reads it),
    reported as the package.json path.
    """
    for name in _COMMITLINT_CONFIG_NAMES:
        candidate = plugin_path / name
        if candidate.is_file():
            return candidate
    pkg = plugin_path / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(data, dict) and "commitlint" in data:
            return pkg
    return None


def provision_commitlintrc_config(plugin_path: Path, dry_run: bool = False) -> list[str]:
    """Provision (or augment) `.commitlintrc.json` so a commitlint gate does not
    red-CI on every Dependabot PR (RC-1 / CIP-7).

    Only acts when the repo actually RUNS commitlint. Then:

    * no commitlint config in any form → CREATE the canonical `.commitlintrc.json`.
    * `.commitlintrc.json` present, `body-max-line-length` NOT mentioned → AUGMENT
      its `rules` object with `"body-max-line-length": [0]` (text edit; every
      other rule and key preserved verbatim).
    * `.commitlintrc.json` present and the author SET that rule → report, never
      overwrite. A deliberate author value is not ours to change.
    * a config in another form (js / yaml / package.json) → leave it alone
      entirely and say so.

    Returns human-readable action/finding lines ([] when nothing is actionable).
    """
    if not _workflow_runs_commitlint(plugin_path):
        return []

    existing = _existing_commitlint_config(plugin_path)

    if existing is None:
        note = (
            f"{_COMMITLINT_CONFIG_REL} missing — the commitlint gate falls back to bare "
            f"@commitlint/config-conventional ({_BODY_MAX_LINE_LENGTH} = 100), so EVERY "
            f"Dependabot PR fails CI on its machine-generated commit body (RC-1); "
            f"run --fix to provision it"
        )
        if dry_run:
            return [note]
        (plugin_path / _COMMITLINT_CONFIG_REL).write_text(_render_canonical_commitlintrc(), encoding="utf-8")
        return [f"created {_COMMITLINT_CONFIG_REL} (disables {_BODY_MAX_LINE_LENGTH}; every other rule enforced)"]

    if existing.name != _COMMITLINT_CONFIG_REL:
        rel = existing.relative_to(plugin_path)
        return [
            f"commitlint config is author-owned ({rel}) — NOT modified. If Dependabot PRs "
            f"fail on {_BODY_MAX_LINE_LENGTH}, disable that one rule there: `[0]`."
        ]

    try:
        original = existing.read_text(encoding="utf-8")
        data = json.loads(original)
    except (OSError, json.JSONDecodeError):
        return [
            f"{_COMMITLINT_CONFIG_REL} is not valid JSON — commitlint will hard-error. NOT "
            f"modified (a file this tool cannot parse is never rewritten); fix it by hand."
        ]
    if not isinstance(data, dict):
        return []

    rules = data.get("rules")
    if isinstance(rules, dict) and _BODY_MAX_LINE_LENGTH in rules:
        # The author set it. Even a NON-disabling value is theirs to keep — we
        # surface the consequence instead of overriding a deliberate choice.
        if rules[_BODY_MAX_LINE_LENGTH] in ([0], [0, "always", 0]):
            return []
        return [
            f"{_COMMITLINT_CONFIG_REL} sets {_BODY_MAX_LINE_LENGTH} = "
            f"{json.dumps(rules[_BODY_MAX_LINE_LENGTH])} — NOT modified (an explicit author "
            f"value is never overwritten). Dependabot PRs will keep failing on it (RC-1); "
            f"set it to [0] to disable that one cosmetic rule."
        ]

    note = (
        f"{_COMMITLINT_CONFIG_REL} does not disable {_BODY_MAX_LINE_LENGTH} — Dependabot's "
        f"machine-generated commit body fails the gate (RC-1); run --fix to add the rule"
    )
    if dry_run:
        return [note]

    rule_entry = f'"{_BODY_MAX_LINE_LENGTH}": [0]'
    if isinstance(rules, dict):
        # AUGMENT the existing `rules` object — insert right after its opening
        # brace so every existing rule is preserved verbatim.
        m = re.search(r'(?ms)"rules"\s*:\s*\{', original)
        if not m:
            return []  # tomllib-style disagreement — refuse to guess.
        tail = original[m.end() :]
        sep = "" if tail.lstrip().startswith("}") else ","
        new_text = original[: m.end()] + f"\n    {rule_entry}{sep}" + tail
    else:
        # No `rules` key at all — insert one after the top-level `{`.
        brace = original.find("{")
        if brace < 0:
            return []
        tail = original[brace + 1 :]
        sep = "" if tail.lstrip().startswith("}") else ","
        new_text = original[: brace + 1] + f'\n  "rules": {{\n    {rule_entry}\n  }}{sep}' + tail

    # Corruption guard: a text edit on the author's file must never leave it
    # unparseable (commitlint would then hard-error on every commit — the very CI
    # failure this provisioner exists to prevent). Verify BOTH that it still
    # parses AND that it gained the rule; on any doubt, leave the file untouched.
    try:
        verified = json.loads(new_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(verified, dict):
        return []
    got = verified.get("rules")
    if not isinstance(got, dict) or got.get(_BODY_MAX_LINE_LENGTH) != [0]:
        return []

    existing.write_text(new_text, encoding="utf-8")
    return [f"augmented {_COMMITLINT_CONFIG_REL} — disabled {_BODY_MAX_LINE_LENGTH} (RC-1)"]


def audit_commitlint_config(plugin_path: Path) -> list[AuditItem]:
    """Audit the commitlint gate parity (RC-1 / CIP-7) — WARN-only, never mutates.

    Mirrors ``audit_jscpd_config``: the audit text IS what ``--fix`` would do
    (``dry_run=True``), so the two cannot drift.
    """
    notes = provision_commitlintrc_config(plugin_path, dry_run=True)
    if not notes:
        return [AuditItem("commitlint", _COMMITLINT_CONFIG_REL, "PASS", "commitlint gate parity OK")]
    return [AuditItem("commitlint", _COMMITLINT_CONFIG_REL, "WARN", note) for note in notes]


# =============================================================================
# cspell PROJECT DICTIONARY — the SPELL local↔CI parity hole (RC-3)
# =============================================================================
#
# The canonical `.mega-linter.yml` ENABLES `SPELL_CSPELL`, but CPV shipped NO
# project dictionary with it. In CI, cspell then falls back to Mega-Linter's
# default word list, which knows nothing of a plugin's own proper nouns (its
# name, its agent/skill/command names, project vocabulary like `wikimem` or
# `TRDD`) — so every one of them is an "Unknown word" and the Lint job goes RED.
# Worse, the LOCAL preflight could not reproduce it: a bare local `cspell` with
# no config trips on ordinary tech terms (pyproject / venv / pipefail / endfor)
# that CI passes, so `cpv_ci_preflight._gate_cspell` SKIPPED the probe entirely.
# Net effect: the author's preflight said GREEN and GitHub CI then said RED —
# a defect the author could not see until CI ran.
#
# The fix is ONE source of truth, exactly like `.jscpd.json` (issue #143):
# cspell auto-discovers `.cspell.json` at the repo root, so the SAME dictionary
# is read by the local probe AND by CI's Mega-Linter cspell. Parity then holds
# BY CONSTRUCTION — a word this file accepts is accepted on both sides, and a
# word it does not is rejected on both sides — which is what lets the preflight
# probe stop skipping and actually FAIL on a real spelling error.
#
# `_CSPELL_BASE_WORDS` seeds the ordinary tech terms a bare cspell trips on
# (verified on a fresh scaffold). That seeding is load-bearing for the
# never-false-block contract: it is what stops the now-live local probe from
# flagging words CI would have passed. It can never make CI *stricter* than
# local, because CI reads this very file.
#
# NEVER clobbers an existing config — the author owns their dictionary:
#   * no cspell config at all      → CREATE the canonical `.cspell.json`.
#   * `.cspell.json` already there → AUGMENT its `words` array with the missing
#     plugin terms only (format-preserving text edit, like provision_dev_extra);
#     every other key, comment, and existing word is preserved verbatim.
#   * any OTHER cspell config form (yaml / jsonc / word-list) → LEAVE IT ALONE.
#     cspell discovers exactly ONE config; writing a second would be ambiguous.
# `.cspell.json` is deliberately NOT in _FORCE_TEMPLATE_FILES — a template
# refresh must not overwrite a dictionary the author has curated.
_CSPELL_CONFIG_REL = ".cspell.json"

# Every cspell config / dictionary filename cspell auto-discovers.
# MUST STAY IN SYNC with `cpv_ci_preflight._CSPELL_CONFIG_NAMES` — that tuple is
# the preflight probe's "does this plugin have a config?" gate, and this one is
# what standardize provisions against. A drift between them means standardize
# would write a second, ambiguous config next to one the preflight already
# recognized. `tests/test_cspell_parity.py` pins the two tuples equal.
_CSPELL_CONFIG_NAMES: tuple[str, ...] = (
    ".cspell.json",
    "cspell.json",
    ".cspell.jsonc",
    "cspell.jsonc",
    ".cspell.config.json",
    "cspell.config.json",
    "cspell.config.yaml",
    "cspell.config.yml",
    "cspell.config.js",
    "cspell.config.cjs",
    "cspell.config.mjs",
    ".cspell.yaml",
    ".cspell.yml",
    "cspell.yaml",
    "cspell.yml",
    ".cspell-words.txt",
    "project-words.txt",
)

# Paths cspell must not spell-check. Mirrors the canonical `.mega-linter.yml`'s
# FILTER_REGEX_EXCLUDE (dev submodules, fixtures, vendored trees) and its
# SPELL_CSPELL_FILTER_REGEX_EXCLUDE ('(uv\.lock|\.json)'), so the LOCAL
# `cspell lint .` sees the same file set CI's Mega-Linter feeds cspell.
# `useGitignore` covers the rest: CI only ever sees tracked files, while a local
# `cspell lint .` would otherwise walk gitignored trees (reports/, .venv/) and
# false-block on noise that CI never reads.
_CSPELL_IGNORE_PATHS: tuple[str, ...] = (
    "**/*.json",
    "**/uv.lock",
    "**/package-lock.json",
    "**/*.lock",
    "**/.git/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/tests_dev/**",
    "**/docs_dev/**",
    "**/scripts_dev/**",
    "**/samples_dev/**",
    "**/examples_dev/**",
    "**/builds_dev/**",
    "**/downloads_dev/**",
    "**/libs_dev/**",
    "**/reports/**",
    "**/reports_dev/**",
    "**/llm_externalizer_output/**",
    "**/.claude/**",
    "**/.tldr/**",
    "**/tests/fixtures/**",
    "**/test/fixtures/**",
    "**/spec/fixtures/**",
    "**/__fixtures__/**",
    "**/testdata/**",
    "**/fixtures/**",
)

# The project-vocabulary seed. TWO groups, both load-bearing:
#
#  (a) ORDINARY TECH TERMS a bare cspell rejects but CI's Mega-Linter cspell
#      accepts (pyproject / venv / pipefail / toplevel / endfor — verified on a
#      fresh scaffold). Without these the now-live local probe would FALSE-BLOCK
#      a plugin whose CI is green. This is the never-false-block half.
#  (b) The CPV / Claude-Code / AI-Maestro vocabulary every standardized plugin's
#      docs use (skillaudit, devitalizer, wikimem, TRDD, uvx, megalinter …).
#
# Lowercase by convention: a lowercase cspell dictionary word matches the
# lowercase, Capitalized and UPPERCASE forms of the same token, so `trdd` also
# accepts `TRDD`. Words shorter than 4 chars are omitted — cspell's default
# `minWordLength` is 4, so it never flags them and listing them is dead weight.
#
# HOW THIS LIST IS DERIVED — MEASURE, never guess. Scaffold a pristine plugin
# and run the REAL checker against it:
#
#     npx cspell lint .        # in a freshly generated plugin
#
# Every word it reports is a word the GENERATOR'S OWN TEMPLATES emit, so a fresh
# scaffold would fail its own publish gate (Gate 3b) on a box that has cspell on
# PATH. Add exactly those, then re-run to exit 0. Two-sided check, mandatory:
# inject a real typo afterwards and confirm cspell still exits 1 on it. That is
# what proves this is a DICTIONARY and not a mute button — if a typo stops being
# caught, a word here is too broad. (Last measured 2026-07-13: 21 words, all from
# the emitted `publish.py` / `cpv_network_resilience.py` / `cliff.toml` templates.)
_CSPELL_BASE_WORDS: tuple[str, ...] = (
    # -- Python / packaging toolchain ---------------------------------------
    "addopts",
    "asyncio",
    "autouse",
    "caplog",
    "conftest",
    "dataclass",
    "dataclasses",
    "dotenv",
    "hatchling",
    "isort",
    "kwargs",
    "levelname",
    "mkdtemp",
    "monkeypatch",
    "mypy",
    "pathlib",
    "pycache",
    "pyproject",
    "pyright",
    "pytest",
    "pyyaml",
    "redef",
    "rglob",
    "ruff",
    "setuptools",
    "stacklevel",
    "stype",
    "testdata",
    "tomli",
    "tomllib",
    "venv",
    "virtualenv",
    "xdist",
    # -- shell / CI / lint toolchain ----------------------------------------
    "actionlint",
    "bandit",
    "checkov",
    "commitlint",
    "cspell",
    "dependabot",
    "endfor",
    "gitleaks",
    "jscpd",
    "jsonlint",
    "markdownlint",
    "megalinter",
    "pipefail",
    "shellcheck",
    "shfmt",
    "toplevel",
    "trivy",
    "trufflehog",
    "yamllint",
    "zizmor",
    # -- JS / node toolchain -------------------------------------------------
    "esbuild",
    "eslint",
    "jsdelivr",
    "nodejs",
    "pnpm",
    "prettier",
    "tsconfig",
    "unpkg",
    # -- Claude Code / plugin ecosystem -------------------------------------
    "anthropic",
    "claude",
    "gitignore",
    "jsonc",
    "kebab",
    "monorepo",
    "semver",
    "subagent",
    "subagents",
    # -- POSIX / process / crypto -------------------------------------------
    # The emitted `publish.py` and `cpv_network_resilience.py` templates shell
    # out, reap child processes and surface TLS errors verbatim.
    "getpid",
    "gnutls",
    "pids",
    "ppid",
    "publickey",
    # -- CPV / AI-Maestro project vocabulary --------------------------------
    "aimaestro",
    "bypassable",
    "cprint",
    "defence",
    "desync",
    "devitalize",
    "devitalizer",
    "fastweb",
    "frontmatter",
    "janitor",
    "kanban",
    "maestro",
    "memgrep",
    "pdata",
    "postprocessors",
    "precheck",
    "prrd",
    "skillaudit",
    "spoofable",
    "topo",
    "trdd",
    "trdds",
    "unparseable",
    "wikimem",
)

# A plugin-term token must be ≥ this long to be worth listing (cspell's default
# minWordLength — shorter tokens are never flagged, so seeding them is noise).
_CSPELL_MIN_WORD_LEN = 4


def _cspell_tokens(text: str) -> list[str]:
    """Split an identifier into the lowercase alphabetic tokens cspell checks.

    cspell tokenizes on non-letters, so `plugin-devitalizer` is checked as the
    two words `plugin` and `devitalizer` — those, not the hyphenated compound,
    are what a dictionary entry must cover. Tokens shorter than
    `_CSPELL_MIN_WORD_LEN` are dropped (cspell never flags them).
    """
    return [t.lower() for t in re.findall(r"[A-Za-z]+", text) if len(t) >= _CSPELL_MIN_WORD_LEN]


def _cspell_plugin_terms(plugin_path: Path) -> list[str]:
    """Collect the plugin's OWN proper nouns — the words CI flags and a generic
    dictionary can never know.

    Sources, all offline and never-failing (a missing/unparseable file simply
    contributes nothing — this must work on an UNINSTALLED, marketplace-less
    source tree):

    * the repo directory name and the manifest `name` (the plugin's own name
      appears in its README, its badges, and every doc heading),
    * the manifest `author` (a proper noun in the README byline),
    * every agent / command file stem and every skill directory name.

    Returned lowercase + deduped, sorted for a stable file.
    """
    raw: list[str] = [plugin_path.name]

    manifest = plugin_path / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            name = data.get("name")
            if isinstance(name, str):
                raw.append(name)
            author = data.get("author")
            if isinstance(author, str):
                raw.append(author)
            elif isinstance(author, dict) and isinstance(author.get("name"), str):
                raw.append(author["name"])

    for sub in ("agents", "commands"):
        d = plugin_path / sub
        if d.is_dir():
            raw.extend(p.stem for p in sorted(d.glob("*.md")))
    skills = plugin_path / "skills"
    if skills.is_dir():
        raw.extend(p.name for p in sorted(skills.iterdir()) if p.is_dir())

    terms: set[str] = set()
    for item in raw:
        terms.update(_cspell_tokens(item))
    return sorted(terms)


def _cspell_seed_words(plugin_path: Path) -> list[str]:
    """The full canonical word list: the base vocabulary + this plugin's own terms."""
    return sorted(set(_CSPELL_BASE_WORDS) | set(_cspell_plugin_terms(plugin_path)))


def _render_canonical_cspell_config(plugin_path: Path) -> str:
    """Render the canonical `.cspell.json` (2-space indent + trailing LF).

    A single renderer so the provisioned file and any test/assertion compare the
    SAME bytes. Deliberately declares NO `dictionaries` list: cspell always
    merges its own bundled default dictionaries, and naming a dictionary package
    the local install lacks would produce a diagnostic CI does not have — the
    opposite of parity. The `words` list plus cspell's built-ins is the whole
    contract, and both sides read it from this one file.
    """
    config: dict[str, object] = {
        "version": "0.2",
        "language": "en",
        # CI only ever spell-checks tracked files; a local `cspell lint .` would
        # otherwise walk gitignored trees and fail on content CI never sees.
        "useGitignore": True,
        "ignorePaths": list(_CSPELL_IGNORE_PATHS),
        "words": _cspell_seed_words(plugin_path),
    }
    return json.dumps(config, indent=2) + "\n"


def _existing_cspell_config(plugin_path: Path) -> Path | None:
    """Return the cspell config the plugin already ships, or None.

    Checks the recognized filenames in order, then a `.cspell/` directory (a
    word-list folder cspell auto-discovers). The FIRST hit wins — the caller
    only needs to know *whether* the author already owns a config, and which
    file it is when that file is the canonical `.cspell.json` it may augment.
    """
    for name in _CSPELL_CONFIG_NAMES:
        candidate = plugin_path / name
        if candidate.is_file():
            return candidate
    dot_dir = plugin_path / ".cspell"
    if dot_dir.is_dir():
        return dot_dir
    return None


def _cspell_config_parses(config: Path) -> bool:
    """True when an existing `.cspell.json` is a JSON object we can safely edit.

    Kept separate from `_cspell_words_missing` so the caller can tell "nothing to
    add" (missing == []) apart from "cannot understand this file" — otherwise an
    unparseable dictionary would silently report as fully provisioned, and a
    corrupt `.cspell.json` is WORSE than none (cspell hard-errors on every file).
    """
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict)


def _cspell_words_missing(config: Path, seeds: list[str]) -> list[str]:
    """Return the seed words absent from an existing `.cspell.json`'s `words`.

    Case-insensitive (cspell matches a lowercase dictionary word against any
    casing). Only called once `_cspell_config_parses` has vouched for the file, so
    a parse failure here is defensive and yields [] — the caller must never mutate
    a file it cannot understand.
    """
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    words = data.get("words")
    have = {w.lower() for w in words if isinstance(w, str)} if isinstance(words, list) else set()
    return [w for w in seeds if w.lower() not in have]


def _format_cspell_word_entries(words: list[str], indent: str = "    ") -> str:
    """Render word names as the inner lines of a JSON array, one per line.

    Comma-SEPARATED with NO trailing comma after the last entry. Unlike TOML
    (where `_format_dev_extra_entries` can end every line with a comma), a
    trailing comma is INVALID JSON — it would make the file cspell writes
    unparseable by cspell itself. Returns e.g. ``    "alpha",\\n    "beta"``
    with no leading or trailing newline; the caller supplies those.
    """
    return ",\n".join(f'{indent}"{w}"' for w in words)


def provision_cspell_config(plugin_path: Path, dry_run: bool = False) -> list[str]:
    """Provision (or augment) the canonical `.cspell.json` so the local cspell
    probe and CI's Mega-Linter SPELL_CSPELL read ONE dictionary.

    RC-3. Mirrors the #142 / #143 provisioners (identity-guarded,
    format-preserving, audit path WARN-only):

    * ``dry_run=False`` (the --fix path):
      - no cspell config at all → CREATE `.cspell.json` with the canonical
        content (base vocabulary + this plugin's own agent/skill/command terms).
      - `.cspell.json` present → AUGMENT its `words` array with the missing seed
        words ONLY. Every other key and every existing word is preserved
        verbatim (text edit, not a re-serialize — a re-serialize would reflow the
        author's file and drop any JSONC comments).
      - a DIFFERENT cspell config form present → leave it entirely alone.
    * ``dry_run=True`` (the AUDIT path): never mutate; surface what --fix would do.

    Returns a list of human-readable action/finding lines (empty when nothing is
    actionable: the dictionary is already complete, or the author owns a
    non-`.cspell.json` config form).
    """
    existing = _existing_cspell_config(plugin_path)
    seeds = _cspell_seed_words(plugin_path)

    # An author-owned config in some OTHER form (yaml / jsonc / word-list dir).
    # cspell discovers exactly one config; adding `.cspell.json` beside it would
    # be ambiguous, and rewriting theirs would clobber it. Report nothing, do
    # nothing — the preflight probe already treats their config as present and
    # will run cspell against it for real.
    if existing is not None and existing.name != _CSPELL_CONFIG_REL:
        return []

    if existing is None:
        note = (
            f"{_CSPELL_CONFIG_REL} missing — the canonical .mega-linter.yml ENABLES "
            f"SPELL_CSPELL, so CI's cspell will hard-error on this plugin's own proper "
            f"nouns (name, agents, skills, commands) with no dictionary to read; "
            f"run --fix to provision it"
        )
        if dry_run:
            return [note]
        (plugin_path / _CSPELL_CONFIG_REL).write_text(
            _render_canonical_cspell_config(plugin_path), encoding="utf-8"
        )
        return [f"created {_CSPELL_CONFIG_REL} ({len(seeds)} words, parity with CI Mega-Linter cspell)"]

    # `.cspell.json` exists but we cannot parse it. NEVER mutate a file we do not
    # understand — but never stay silent either: a corrupt dictionary is WORSE than
    # a missing one (cspell hard-errors on every file), and an audit that reported
    # PASS here would hide it. Surface it in BOTH paths; the author fixes it by hand.
    if not _cspell_config_parses(existing):
        return [
            f"{_CSPELL_CONFIG_REL} is not valid JSON — cspell will hard-error on every "
            f"file it reads. NOT modified (a file this tool cannot parse is never "
            f"rewritten); fix the JSON by hand, then re-run."
        ]

    # AUGMENT its `words` array with what is missing.
    missing = _cspell_words_missing(existing, seeds)
    if not missing:
        return []
    if dry_run:
        return [
            f"{_CSPELL_CONFIG_REL} is missing {len(missing)} project term(s) "
            f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}) — CI's cspell "
            f"will flag them; run --fix to augment the words list"
        ]

    original = existing.read_text(encoding="utf-8")
    addition = _format_cspell_word_entries(missing)
    # Reaching here PROVES the file is strict JSON: `_cspell_words_missing`
    # json.loads()-ed it (a parse failure returns [] → the early exit above), so
    # the text edit below operates on a known-good document and the parse-verify
    # at the end is a true corruption guard, not a JSONC false alarm.
    words_re = re.compile(r'(?ms)(?P<head>"words"\s*:\s*\[)(?P<body>.*?)(?P<tail>\])')
    m = words_re.search(original)
    if m:
        # AUGMENT the existing array: keep every existing entry verbatim, close
        # it with a comma if it does not already carry one, then append the
        # missing words. `body.rstrip()` drops the whitespace that sat before the
        # closing bracket so the injected entries land on their own lines.
        body = m.group("body")
        stripped = body.rstrip()
        if stripped and not stripped.endswith(","):
            stripped += ","
        new_body = (stripped + "\n" if stripped else "\n") + addition + "\n  "
        new_text = (
            original[: m.start()] + m.group("head") + new_body + m.group("tail") + original[m.end() :]
        )
        note = f"augmented {_CSPELL_CONFIG_REL} words with {len(missing)} project term(s)"
    else:
        # No `words` key at all — insert one right after the opening brace. Every
        # other key is untouched. A file with no top-level `{` is not a cspell
        # JSON config we can safely edit → refuse to mutate.
        brace = original.find("{")
        if brace < 0:
            return []
        block = '\n  "words": [\n' + addition + "\n  ],"
        new_text = original[: brace + 1] + block + original[brace + 1 :]
        note = f"added a words list to {_CSPELL_CONFIG_REL} with {len(missing)} project term(s)"

    if new_text == original:
        return []

    # Corruption guard: a text edit on someone else's file must never leave it
    # unparseable — cspell would then hard-error on EVERY file and we would have
    # created the very CI failure this provisioner exists to prevent. Verify the
    # result is still valid JSON AND that it actually gained the words; on any
    # doubt, leave the author's file exactly as it was.
    try:
        verified = json.loads(new_text)
    except json.JSONDecodeError:
        return []
    if not isinstance(verified, dict):
        return []
    got = verified.get("words")
    if not isinstance(got, list):
        return []
    have = {w.lower() for w in got if isinstance(w, str)}
    if not all(w.lower() in have for w in missing):
        return []

    existing.write_text(new_text, encoding="utf-8")
    return [note]


def audit_cspell_config(plugin_path: Path) -> list[AuditItem]:
    """Audit the cspell SPELL gate parity (RC-3) — WARN-only.

    Surfaces, without mutating anything, whatever `provision_cspell_config(...,
    dry_run=True)` would do, so the audit text and the --fix behaviour can never
    drift (the `audit_jscpd_config` pattern). A PASS is emitted when the plugin
    already ships a complete dictionary (or an author-owned config in another
    form), so a fully-canonical plugin still reports the dimension.
    """
    notes = provision_cspell_config(plugin_path, dry_run=True)
    if not notes:
        return [AuditItem("cspell", _CSPELL_CONFIG_REL, "PASS", "cspell SPELL gate parity OK")]
    return [AuditItem("cspell", _CSPELL_CONFIG_REL, "WARN", note) for note in notes]


# =============================================================================
# the-skills-menu CANON MIGRATION (force-templates only)
# =============================================================================
#
# Under --force-templates (the canon UPGRADE verb), every agent in the plugin is
# migrated to the-skills-menu method: its frontmatter `skills:` list is rewritten
# to exactly `[the-skills-menu]` (all other fields preserved) and the mandatory
# dynamic-loading instruction is inserted into its body. A per-plugin
# skills/the-skills-menu/SKILL.md catalog is created if absent (reusing
# generate_plugin_repo.gen_the_skills_menu_skill so scaffold-new and
# upgrade-existing emit byte-identical catalogs). Plain --fix NEVER touches an
# agent — only adds missing files. Implements the the-skills-menu-create spec
# (§"Agent frontmatter rewrite rule" + §"Agent body instruction rule"), which is
# the single source of truth.


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """Split a `.md` file's leading YAML frontmatter from its body.

    Returns (frontmatter_inner, body) where ``frontmatter_inner`` is the text
    BETWEEN the opening and closing ``---`` fences (without the fences), and
    ``body`` is everything after the closing fence. Returns None when the file
    has no frontmatter (no leading ``---`` line) — the caller treats that as
    "skip + report for manual review" per the spec (Error #4/#7), never a crash.
    """
    # Frontmatter must START the file (allow a leading BOM / blank lines? No —
    # the harness requires --- on line 1; mirror that strictly so a stray "---"
    # mid-body is never mistaken for frontmatter).
    if not text.startswith("---"):
        return None
    # The opening fence is the first line; it must be exactly "---" (optionally
    # with trailing whitespace), not e.g. "----" or "--- foo".
    m = re.match(r"^---[^\S\n]*\n(.*?\n)^---[^\S\n]*\n?", text, re.DOTALL | re.MULTILINE)
    if not m:
        return None
    inner = m.group(1)
    body = text[m.end() :]
    return inner, body


def _rewrite_agent_skills_field(frontmatter_inner: str) -> tuple[str, bool]:
    """Rewrite the frontmatter `skills:` list to exactly `[the-skills-menu]`.

    Handles BOTH YAML shapes, preserving every other field and overall ordering:
      - block list:  ``skills:\\n  - a\\n  - b``  (consumes the indented items)
      - flow list:   ``skills: [a, b]``           (single line)
      - scalar:      ``skills: a``                 (single line)
    When no `skills:` key exists, one is appended at the end of the frontmatter
    (so an agent that declared none is still migrated to the canonical single
    entry). Returns (new_inner, changed).
    """
    canonical_block = "skills:\n  - the-skills-menu"
    lines = frontmatter_inner.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    replaced = False
    n = len(lines)
    while i < n:
        line = lines[i]
        # Match a top-level `skills:` key (no leading indentation — a nested
        # `skills:` under some other mapping is not the agent's skill list).
        if re.match(r"^skills[^\S\n]*:", line):
            # Determine block vs inline by what follows the colon on this line.
            after = line.split(":", 1)[1].strip()
            # Emit the canonical block in place of the old key, preserving the
            # original line's trailing newline convention.
            newline = "\n" if line.endswith("\n") else ""
            out.append(canonical_block + newline)
            i += 1
            if after == "":
                # Block-list form: consume following indented `- item` (and blank)
                # lines that belong to this list.
                while i < n and re.match(r"^[^\S\n]+(-|\Z)", lines[i]):
                    i += 1
            # inline form (after != "") consumed only this single line.
            replaced = True
            continue
        out.append(line)
        i += 1
    if replaced:
        new_inner = "".join(out)
        return new_inner, new_inner != frontmatter_inner
    # No skills: key — append the canonical block at the end (ensure the
    # preceding content ends with a newline so the new key starts on its own
    # line).
    base = frontmatter_inner if frontmatter_inner.endswith("\n") or frontmatter_inner == "" else frontmatter_inner + "\n"
    new_inner = base + canonical_block + "\n"
    return new_inner, True


def _insert_body_instruction(body: str) -> tuple[str, bool]:
    """Insert the mandatory dynamic-loading instruction into an agent body.

    Placement (spec §"Agent body instruction rule"): as the FIRST body paragraph
    AFTER the opening ``# Title`` H1 if the body starts with one; otherwise as the
    very first body line. A blank line is left before and after so it renders as
    its own paragraph. Idempotent — if the exact instruction already appears
    verbatim, the body is returned unchanged (no duplicate; mirrors the
    add_component.py duplicate-guard).

    Returns (new_body, changed).
    """
    if _SKILLS_MENU_BODY_INSTRUCTION in body:
        return body, False  # already present — never duplicate

    # Defensive markdown-poison guard: the canonical instruction starts with
    # "You must load …" and so never begins with a markdown-special char, but
    # assert it explicitly so a future edit to the constant can't silently ship a
    # body line that markdownlint MD018/MD004 would flag and block --strict.
    if _SKILLS_MENU_BODY_INSTRUCTION.startswith(_MD_POISON_LINE_START):
        raise ValueError("the-skills-menu body instruction must not start with a markdown-special char")

    para = _SKILLS_MENU_BODY_INSTRUCTION

    # Find a leading H1 (the first non-blank body line being `# ...`). Skip any
    # leading blank lines that may sit between the frontmatter and the H1.
    lines = body.splitlines(keepends=True)
    idx = 0
    while idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    if idx < len(lines) and re.match(r"^#[^\S\n]+\S", lines[idx]):
        # Insert AFTER the H1 line, as its own paragraph.
        head = "".join(lines[: idx + 1])
        tail = "".join(lines[idx + 1 :])
        head = head if head.endswith("\n") else head + "\n"
        new_body = head + "\n" + para + "\n" + ("\n" if tail and not tail.startswith("\n") else "") + tail
        return new_body, True
    # No leading H1 — insert as the very first body line.
    new_body = para + "\n\n" + body.lstrip("\n")
    return new_body, True


def _skill_frontmatter_field(skill_md_text: str, field: str) -> str | None:
    """Return a single scalar frontmatter value from a SKILL.md, or None.

    Deliberately dependency-free (PyYAML is NOT imported by standardize) and
    forgiving: it reads the leading ``---`` … ``---`` block and returns the first
    top-level ``<field>:`` scalar, stripping surrounding single/double quotes.
    A block value (``field:`` with nothing after the colon → list/mapping on the
    following lines) returns None — the catalog only needs the scalar ``name``
    and ``description``. Returns None when the file has no frontmatter at all.
    """
    split = _split_frontmatter(skill_md_text)
    if split is None:
        return None
    inner, _body = split
    for line in inner.splitlines():
        m = re.match(rf"^{re.escape(field)}[^\S\n]*:[^\S\n]*(.*)$", line)
        if m is None:
            continue
        value = m.group(1).strip()
        if value == "":
            return None  # block scalar / list — not what the catalog wants
        # Strip a single matched pair of surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value.strip() or None
    return None


def scan_plugin_skills_inventory(plugin_path: Path) -> list[tuple[str, str]]:
    """Scan ``skills/<name>/SKILL.md`` and return the real operational skills.

    Returns a sorted list of ``(skill_name, one_line_description)`` for every
    skill directory under ``skills/`` that ships a ``SKILL.md`` — EXCLUDING
    ``the-skills-menu`` itself (the catalog never lists itself) and any
    ``the-skills-menu-create`` migrator copy. The skill's NAME comes from its
    frontmatter ``name:`` when present, otherwise from the directory name (so a
    skill with a malformed/missing name is still discovered, never silently
    dropped). The description is the frontmatter ``description:`` first sentence,
    trimmed to a single readable line; missing → a neutral placeholder.

    This is the population source for the the-skills-menu catalog: WITHOUT it,
    the standardizer wrote the empty-stub "no operational skills yet" placeholder
    even on a plugin with many real skills (issue #150).
    """
    skills_dir = plugin_path / "skills"
    if not skills_dir.is_dir():
        return []
    excluded = {"the-skills-menu", "the-skills-menu-create"}
    found: list[tuple[str, str]] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir() or child.name in excluded:
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        text = skill_md.read_text(encoding="utf-8")
        name = _skill_frontmatter_field(text, "name") or child.name
        if name in excluded:
            continue
        desc = _skill_frontmatter_field(text, "description")
        if desc:
            # Keep it to one readable line: first sentence (up to the first
            # ". ") and a hard cap so a paragraph-length description does not
            # bloat the catalog table.
            first = desc.split(". ", 1)[0].strip().rstrip(".")
            one_line = first if len(first) <= 160 else first[:157].rstrip() + "..."
        else:
            one_line = "(no description — see the skill's SKILL.md)"
        found.append((name, one_line))
    return found


def _render_skills_menu_catalog(params: object, skills: list[tuple[str, str]]) -> str:
    """Render the the-skills-menu SKILL.md, POPULATED from the real inventory.

    Starts from generate_plugin_repo.gen_the_skills_menu_skill (the single source
    of truth for the catalog shape), then applies two issue-#150 fixes:

      1. Replace the empty-stub ``## Plugin Skills`` block ("This plugin has no
         operational skills yet" + a placeholder table row) with a real table
         listing every discovered skill (name + one-line description). When
         ``skills`` is empty the stub is left as-is (an empty catalog), but the
         CALLER must not migrate agents in that case — see
         migrate_agents_to_skills_menu.
      2. Drop the ``allowed-tools: Read`` frontmatter line — a skill must not
         carry tool frontmatter (the tool surface is dynamic; only commands
         declare allowed-tools).

    Both transforms are applied verbatim to the generator's text so a future
    change to the catalog shape flows through automatically.
    """
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from generate_plugin_repo import gen_the_skills_menu_skill

    text = gen_the_skills_menu_skill(params)  # type: ignore[arg-type]

    # (2) Strip the `allowed-tools:` frontmatter line (issue #150 secondary).
    text = re.sub(r"(?m)^allowed-tools:.*\n", "", text)

    # (1) Populate the Plugin Skills table when there are real skills.
    if skills:
        plugin_name = getattr(params, "name", "this-plugin")
        rows = "\n".join(f"| {i} | `{name}` | {desc} |" for i, (name, desc) in enumerate(skills, start=1))
        populated = (
            "## Plugin Skills\n"
            "\n"
            f"The {plugin_name} plugin ships the operational skills below. "
            "Pick the one your task needs and load it on demand:\n"
            "\n"
            "| # | Skill | What it does |\n"
            "|---|-------|--------------|\n"
            f"{rows}\n"
            "\n"
            "All entries above are invoked as\n"
            f"`Skill({{skill: \"{plugin_name}:<name>\"}})`."
        )
        # Replace from the `## Plugin Skills` heading up to (but not including)
        # the next top-level `## ` heading. The generator always emits a
        # `## Resources` section after Plugin Skills, so an anchor exists.
        text = re.sub(
            r"(?ms)^## Plugin Skills\n.*?(?=^## )",
            populated + "\n\n",
            text,
            count=1,
        )
    return text


def _ensure_skills_menu_catalog(plugin_path: Path, dry_run: bool) -> tuple[str | None, int]:
    """Create a POPULATED skills/the-skills-menu/SKILL.md if absent.

    Reuses generate_plugin_repo.gen_the_skills_menu_skill for the catalog shape,
    then populates its ``## Plugin Skills`` table from the plugin's REAL skill
    inventory (issue #150 — the old code wrote the empty-stub placeholder even
    when the plugin had many skills) and drops the ``allowed-tools`` frontmatter.

    Returns ``(rel_path_or_None, n_real_skills)`` where ``rel_path_or_None`` is
    the repo-relative path that was (or would be) created, or None when the
    catalog already exists OR when there are no skills to list, and
    ``n_real_skills`` is the count of operational skills discovered under
    ``skills/`` (used by the caller to decide whether the migration is safe to
    perform). Never clobbers an existing catalog — refreshing a hand-curated
    catalog is the the-skills-menu-create skill's job.

    CRITICAL (issue #150): an EMPTY-stub catalog is NEVER written. The old code
    wrote the "no operational skills yet" placeholder whenever the catalog was
    absent — and that empty catalog then made the migration look usable, so the
    agent got stripped into a menu with nothing in it. Now, with zero real
    skills and no existing catalog, this writes NOTHING and returns (None, 0) so
    the caller skips the migration entirely.
    """
    rel = "skills/the-skills-menu/SKILL.md"
    target = plugin_path / rel
    skills = scan_plugin_skills_inventory(plugin_path)
    if target.exists():
        return None, len(skills)
    if not skills:
        # Nothing to populate the catalog with — do NOT write an empty stub.
        return None, 0

    manifest = _read_plugin_json(plugin_path)
    params = _params_from_manifest(manifest)
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_skills_menu_catalog(params, skills), encoding="utf-8")
    return rel, len(skills)


def migrate_agents_to_skills_menu(plugin_path: Path, dry_run: bool = False) -> int:
    """Migrate every agent in the plugin to the-skills-menu method.

    For each ``agents/*.md`` file WITH YAML frontmatter:
      - rewrite frontmatter ``skills:`` → ``[the-skills-menu]`` (preserve all
        other fields), and
      - insert the mandatory dynamic-loading instruction into the body.
    An already-migrated agent (canonical skills list + instruction present) is a
    clean no-op — no duplicate instruction. An agent file lacking frontmatter is
    SKIPPED and reported for manual review (spec Error #4/#7), never crashed on.
    A per-plugin skills/the-skills-menu/SKILL.md catalog is created if absent.

    Profile-agnostic: every profile keeps its agents, so all are migrated.

    SAFETY GATE (issue #150): an agent is migrated to the-skills-menu ONLY when
    the catalog can actually list skills — i.e. the plugin has real skills under
    ``skills/`` (or already ships a hand-curated catalog). If population yields
    ZERO skills AND no catalog exists, NO agent is touched and a WARNING is
    emitted (the caller must NOT report success): stripping an agent's ``skills:``
    while the menu is empty would leave the agent unable to preload its core
    skills AND unable to discover them — a strictly broken agent.

    Returns the count of agent files actually migrated (changed). Printed output
    summarises created/migrated/skipped; the count excludes skips and no-ops.
    """
    agents_dir = plugin_path / "agents"
    catalog_rel, n_skills = _ensure_skills_menu_catalog(plugin_path, dry_run)
    catalog_path = plugin_path / "skills" / "the-skills-menu" / "SKILL.md"
    # The catalog is "usable" when it lists real skills, OR a catalog already
    # exists on disk (hand-curated — its contents are the author's business and
    # may already list skills we cannot parse). catalog_rel is non-None only when
    # we just (would) create one; an EXISTING catalog returns catalog_rel=None.
    catalog_preexisting = catalog_rel is None and catalog_path.exists()
    catalog_usable = n_skills > 0 or catalog_preexisting

    if not catalog_usable:
        # Genuinely no skills to discover and no catalog to fall back on. Do NOT
        # migrate (would strip agents into an empty menu). Do NOT report success.
        print(
            f"  {YELLOW}the-skills-menu migration SKIPPED:{NC} no operational skills found "
            f"under skills/ and no existing catalog. Migrating now would strip each agent's "
            f"skills into an EMPTY menu (a broken agent). Add real skills, then re-run "
            f"--force-templates, or run the the-skills-menu-create command to build the catalog first."
        )
        return 0

    if catalog_rel is not None:
        verb = "would create" if dry_run else "created"
        detail = f"{n_skills} skill(s) listed" if n_skills else "from existing catalog"
        print(f"  {GREEN}{verb}{NC} {catalog_rel} (the-skills-menu catalog — {detail})")

    if not agents_dir.is_dir():
        print(f"  {DIM}No agents/ directory — nothing to migrate.{NC}")
        return 0

    migrated = 0
    skipped: list[str] = []
    for agent_file in sorted(agents_dir.glob("*.md")):
        text = agent_file.read_text(encoding="utf-8")
        split = _split_frontmatter(text)
        if split is None:
            # No frontmatter → not a valid agent definition. Skip + report.
            skipped.append(agent_file.name)
            continue
        inner, body = split
        new_inner, fm_changed = _rewrite_agent_skills_field(inner)
        new_body, body_changed = _insert_body_instruction(body)
        if not fm_changed and not body_changed:
            continue  # already migrated — clean no-op, no duplicate
        new_text = f"---\n{new_inner}---\n{new_body}"
        if not dry_run:
            agent_file.write_text(new_text, encoding="utf-8")
        verb = "would migrate" if dry_run else "migrated"
        print(f"  {GREEN}{verb}{NC} agents/{agent_file.name} → the-skills-menu")
        migrated += 1

    if skipped:
        print(
            f"  {YELLOW}Manual review needed:{NC} {len(skipped)} agent file(s) lack YAML "
            f"frontmatter and were NOT migrated: {', '.join(skipped)}"
        )
    return migrated


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

    # Profile-aware regeneration (TRDD-e9f13df1, #128-A / Piece D): resolve the
    # plugin's pipeline profile so a profile-parameterized gen_* (currently
    # gen_publish_py) regenerates the PROFILE-APPROPRIATE variant. Without this,
    # `--force-templates` would clobber a submodule-build plugin's submodule-aware
    # publish.py with the standard one and break its releases — the exact #128
    # breakage PSS reported. Best-effort: resolve_pipeline_profile falls back to
    # "standard" on any error, so a standard plugin is byte-identically unaffected.
    from cpv_pipeline_profile import (
        resolve_pipeline_profile,  # noqa: E402 — sibling import after the scripts/ path insert above
    )

    profile = resolve_pipeline_profile(plugin_path)

    # Issue #145b / #144Bb — paths the plugin deliberately diverges on (read
    # once from the already-parsed manifest). A force-overwrite of any of these
    # is skipped regardless of drift direction.
    divergence = _manifest_intentional_divergence(manifest)

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
        elif "profile" in sig.parameters:
            # Profile-aware (TRDD-e9f13df1, #128-A): pass the resolved profile so a
            # submodule-build plugin regenerates its submodule-aware publish.py,
            # never the standard one. SELECTOR not suppressor — a standard plugin
            # resolves to "standard" and gets the byte-identical standard output.
            content = gen_func(params, profile=profile)
        else:
            content = gen_func(params)

        file_path = plugin_path / rel_path
        is_executable = rel_path in _EXECUTABLE_FILES

        # Issue #145b / #144Bb — profile-AWARE force-overwrite. Before clobbering
        # an existing shared-canon file, check whether the plugin's copy is
        # already at/AHEAD of canon (force-overwriting would DOWNGRADE it — the
        # exact case the validator flags) or is explicitly marked as an
        # intentional divergence. Either way, SKIP the overwrite and leave the
        # plugin's file untouched. Only applies to the force-overwrite branch; a
        # genuinely MISSING file (op_kind == "create") is always written.
        if op_kind == "overwrite":
            skip_line = _force_template_skip_reason(file_path, rel_path, content, divergence)
            if skip_line is not None:
                print(f"  {YELLOW}{skip_line}{NC}")
                continue

            # Issue #165 — MERGE, don't clobber, a canon JSON config. A plugin's own
            # keys (e.g. `"MD010": {"code_blocks": false}`, load-bearing for a skill
            # that documents tab-indented Makefile recipes) are carried over; canon
            # wins only on the keys canon itself declares. JSON has no comments, so
            # this merge loses nothing.
            if rel_path.endswith(".json") and file_path.is_file():
                merged, preserved = _merge_canon_json(file_path.read_text(encoding="utf-8"), content)
                if merged is not None and preserved:
                    content = merged
                    print(f"  {GREEN}[merge]{NC} {rel_path} — preserved custom key(s): {', '.join(preserved)}")

            # Issue #165 — same for a canon YAML config, but the plugin's file is the
            # BASE (see _merge_canon_yaml): we only APPEND the canon keys it lacks, so
            # its values AND the comment paragraphs justifying them survive verbatim.
            # This is what saves the real `.mega-linter.yml` case — the author extended
            # canon's `REPOSITORY_CHECKOV_ARGUMENTS` with `,CKV_DOCKER_2` (a HEALTHCHECK
            # skip, load-bearing because every Dockerfile they ship is an ephemeral
            # run-once container) and a blind overwrite deleted it plus its 8-line
            # rationale. A custom KEY detector cannot see this: the divergence is a
            # custom VALUE inside a key canon also declares.
            if rel_path in _CANON_YAML_MERGE_FILES and file_path.is_file():
                merged_yaml, kept, added = _merge_canon_yaml(file_path.read_text(encoding="utf-8"), content)
                content = merged_yaml
                if added:
                    print(f"  {GREEN}[merge]{NC} {rel_path} — added canon key(s): {', '.join(added)}")
                if kept:
                    print(
                        f"  {YELLOW}[merge]{NC} {rel_path} — kept YOUR value for {', '.join(kept)} "
                        f"(canon differs; reconcile by hand if you did not customize it)"
                    )
                if not added and not kept:
                    print(f"  {GREEN}[merge]{NC} {rel_path} — already at canon")

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

    # Auto-add missing .gitignore entries when an existing .gitignore is present.
    # Use the SAME coverage logic as audit_gitignore so the two never disagree.
    # A naive `entry not in content` substring test would (a) wrongly skip
    # adding ".env" when only ".env.example" is present — leaving the audit
    # permanently reporting it missing — and (b) miss the legitimate broader-glob
    # coverage case that audit_gitignore honours.
    gitignore_path = plugin_path / ".gitignore"
    if not dry_run and gitignore_path.exists():
        content = gitignore_path.read_text(encoding="utf-8")
        active_lines = [
            stripped for raw in content.splitlines() if (stripped := raw.strip()) and not stripped.startswith("#")
        ]
        missing = []
        for entry in REQUIRED_GITIGNORE_ENTRIES:
            if not any(_gitignore_line_covers_entry(entry, line) for line in active_lines):
                missing.append(entry)
        if missing:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                f.write("\n# Added by CPV standardize\n")
                for entry in missing:
                    f.write(f"{entry}\n")
            print(f"  {GREEN}Updated:{NC} .gitignore — added {len(missing)} missing entries")

    # Issue #142 Defect #2 (provision half — supersedes the issue-#25 WARN-only
    # behaviour HERE, in the --fix path): when the migration emits release.yml or
    # ci.yml — both of which run `uv sync --extra dev` — auto-PROVISION the
    # canonical dev extra so CI does not fail with
    # "Extra `dev` is not defined …" / "Failed to spawn: <tool>". fix_missing_files
    # is only ever reached under --fix/--force-templates, so mutating pyproject
    # here is authorized; the AUDIT-only path (run_audit) still merely WARNs via
    # audit_pyproject and never mutates.
    #
    # RC-9 widens the trigger: a plugin whose EXISTING ci.yml already runs a
    # SHARDED pytest (`--splits`) needs `pytest-split` in the dev extra even when
    # this run emits no workflow at all — that is the plain-`--fix`-on-an-already-
    # migrated-repo case, and without it every shard dies with
    # `pytest: error: unrecognized arguments: --splits --group`. The probe reads
    # the workflows ON DISK, and the force-templated (sharded) ci.yml is written
    # ABOVE this point, so one probe answers correctly for both paths.
    workflow_emitted = bool(_WORKFLOW_PATHS_REQUIRING_DEV_EXTRAS & (missing_files | force_overwrite))
    if workflow_emitted or _workflow_runs_sharded_pytest(plugin_path):
        for note in provision_dev_extra(plugin_path, dry_run=dry_run):
            print(f"  {GREEN}[dev-extra]{NC} {note}")

    # Issue #142 Defect #4: ci.yml's Validate job supersedes the standalone
    # "Plugin Validation" validate.yml — remove it (identity-guarded) so ci.yml's
    # actionlint Lint job does not trip on validate.yml's pre-existing SC2086.
    ci_emitted = ".github/workflows/ci.yml" in (missing_files | force_overwrite)
    if ci_emitted or (plugin_path / ".github" / "workflows" / "ci.yml").is_file():
        for note in remove_superseded_validate_yml(plugin_path, dry_run=dry_run):
            print(f"  {YELLOW}[validate.yml]{NC} {note}")

    # TRDD-HZSI0BZ6: re-pin a stale/invalid CPV ref (e.g. `@main`, which 404s —
    # CPV's default branch is `master`) in every existing .github/workflows/*.yml
    # to the current resolved ref. --force-templates already re-pins ci/release/
    # notify by regenerating them, but a plain --fix never touches an existing
    # workflow, so a workflow migrated by an OLD CPV keeps its stale ref forever.
    # Surgical in-place rewrite of ONLY the CPV ref; a valid ref is left alone.
    for note in repin_stale_cpv_ref(plugin_path, dry_run=dry_run):
        print(f"  {YELLOW}[cpv-ref]{NC} {note}")

    # Issue #165: give an EXISTING scripts/publish.py the `{name}--v{version}`
    # dependency-resolution tag stage. CC 2.1.110+ resolves a version-constrained
    # dependency ONLY against that tag, so a pipeline without it publishes releases
    # nobody can depend on. The GENERATED template has minted it since v2.156.0, but
    # standardize never overwrites an existing publish.py (#145/#140) — so a plugin
    # that already had one could never gain the stage. Surgical in-place injection,
    # on ANY --fix (a plugin that cannot safely --force-templates is exactly the one
    # that needs this), idempotent, and publish.py is NOT overwritten.
    for note in migrate_publish_py_dependency_tag(plugin_path, dry_run=dry_run):
        print(f"  {YELLOW}[dep-tag]{NC} {note}")

    # CIP-1 (#140): drop the INVERTED `CLAUDE_PRIVATE_USERNAMES: ${{ github.
    # repository_owner }}` env from every workflow. It tells CPV that the PUBLIC
    # owner is a PRIVATE username, so every legitimate owner GitHub URL is flagged
    # as a leak (22 false CRITICALs → red CI). --force-templates regenerates
    # ci.yml/release.yml and drops it there; this ALSO fixes a plain --fix and any
    # non-canonical workflow. Surgical: only that line (never the correct LOCAL
    # `CLAUDE_PRIVATE_USERNAMES="$(whoami)"` shell idiom, which cannot match).
    for note in remove_inverted_private_usernames(plugin_path, dry_run=dry_run):
        print(f"  {YELLOW}[ci-env]{NC} {note}")

    # Issue #143: provision the canonical .jscpd.json so the local
    # `publish.py --gate` jscpd copy-paste check reads the SAME threshold/ignore
    # config CI's Mega-Linter does (gate parity). Create-if-absent, never clobber
    # an existing one on a plain --fix; runs unconditionally under --fix because
    # the gate is part of the canonical publish.py the migration installs.
    for note in provision_jscpd_config(plugin_path, dry_run=dry_run):
        print(f"  {GREEN}[jscpd]{NC} {note}")

    # RC-3: provision the canonical .cspell.json so the local cspell probe and
    # CI's Mega-Linter SPELL_CSPELL (which the canonical .mega-linter.yml ENABLES)
    # read the SAME dictionary — closing the local-GREEN / CI-RED parity hole.
    # Create-if-absent, augment-if-present, never clobber the author's config;
    # runs unconditionally under --fix because SPELL_CSPELL is part of the
    # canonical .mega-linter.yml the migration installs.
    for note in provision_cspell_config(plugin_path, dry_run=dry_run):
        print(f"  {GREEN}[cspell]{NC} {note}")

    # RC-1 / CIP-7: provision `.commitlintrc.json` when the repo runs a commitlint
    # gate. With no config the gate falls back to bare config-conventional
    # (body-max-line-length = 100) and EVERY Dependabot PR fails on its
    # machine-generated body. Create-if-absent / augment-if-silent; an explicit
    # author value is never overwritten (which is also why `.commitlintrc.json` is
    # deliberately NOT in _FORCE_TEMPLATE_FILES — that list is a blind overwrite).
    for note in provision_commitlintrc_config(plugin_path, dry_run=dry_run):
        print(f"  {GREEN}[commitlint]{NC} {note}")

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
        # the-skills-menu canon migration — ONLY under --force-templates (the
        # canon UPGRADE verb). Plain --fix never touches an agent. Migrates every
        # agent's frontmatter skills: → [the-skills-menu] + body instruction, and
        # creates skills/the-skills-menu/SKILL.md if absent.
        if args.force_templates:
            print(f"\n{BOLD}the-skills-menu migration{NC}{mode_label}")
            n_migrated = migrate_agents_to_skills_menu(plugin_path, dry_run=args.dry_run)
            # The "all already migrated" success line is only truthful when the
            # migration was NOT skipped for an empty catalog (issue #150). When
            # skipped, migrate_agents_to_skills_menu already printed the WARNING
            # explaining why — never report success on top of it.
            _menu_catalog = plugin_path / "skills" / "the-skills-menu" / "SKILL.md"
            _catalog_usable = scan_plugin_skills_inventory(plugin_path) or _menu_catalog.exists()
            if n_migrated == 0 and _catalog_usable:
                print(f"  {GREEN}All agents already on the-skills-menu (or none to migrate).{NC}")
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
