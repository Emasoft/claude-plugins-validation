#!/usr/bin/env python3
"""Audit and standardize a marketplace repository to match CPV standards.

Validates marketplace.json structure, ensures all plugins use external GitHub
repo sources (not local paths), and checks for standard CI/CD infrastructure.

Marketplaces are HUBS ONLY -- they contain pointers to external plugin repos,
never plugin code itself.

Usage:
    uv run scripts/standardize_marketplace.py <marketplace-path>
    uv run scripts/standardize_marketplace.py <marketplace-path> --fix [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ANSI colors for terminal output
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
NC = "\033[0m"

# Kebab-case: lowercase letters/digits/hyphens, starts with letter, ends with letter/digit
KEBAB_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")

# Reserved marketplace names (aligned with validate_marketplace.py)
RESERVED_MARKETPLACE_NAMES = frozenset(
    {
        "claude-code-marketplace",
        "claude-code-plugins",
        "claude-plugins-official",
        "anthropic-marketplace",
        "anthropic-plugins",
        "agent-skills",
        "life-sciences",
        # Single-word reserved names (aligned with generate_marketplace_repo.py)
        "official",
        "anthropic",
        "claude",
        "test",
        "example",
        "demo",
    }
)

# Impersonation keywords -- marketplace names containing BOTH a brand word AND
# "official" are flagged as potential impersonation attempts
BRAND_KEYWORDS = {"anthropic", "claude", "claude-code", "claude-plugins"}

# Standard files that every marketplace repo should have
STANDARD_FILES: list[tuple[str, bool]] = [
    # (relative path, is_required)
    (".claude-plugin/marketplace.json", True),
    ("README.md", False),
    (".gitignore", False),
    (".github/workflows/validate.yml", False),
    (".github/workflows/update-catalog.yml", False),
    ("scripts/update_catalog.py", False),
    ("cliff.toml", False),
    (".githooks/pre-push", False),
]

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

# Severity levels: ERROR blocks validity, WARN is advisory
SEVERITY_ERROR = "ERROR"
SEVERITY_WARN = "WARN"
SEVERITY_OK = "OK"


class Finding:
    """A single audit finding."""

    __slots__ = ("severity", "message")

    def __init__(self, severity: str, message: str) -> None:
        self.severity = severity
        self.message = message

    def __str__(self) -> str:
        color = {SEVERITY_ERROR: RED, SEVERITY_WARN: YELLOW, SEVERITY_OK: GREEN}.get(self.severity, NC)
        return f"  {color}[{self.severity}]{NC} {self.message}"


# ---------------------------------------------------------------------------
# Marketplace JSON validation
# ---------------------------------------------------------------------------


def validate_marketplace_json(marketplace_dir: Path) -> tuple[dict | None, list[Finding]]:
    """Validate marketplace.json exists, is valid JSON, and has correct structure.

    Returns the parsed data (or None) and a list of findings.
    """
    findings: list[Finding] = []
    mj_path = marketplace_dir / ".claude-plugin" / "marketplace.json"

    # -- Existence check --
    if not mj_path.exists():
        findings.append(Finding(SEVERITY_ERROR, "marketplace.json not found at .claude-plugin/marketplace.json"))
        return None, findings

    findings.append(Finding(SEVERITY_OK, "marketplace.json exists"))

    # -- JSON parse --
    try:
        with open(mj_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        findings.append(Finding(SEVERITY_ERROR, f"marketplace.json is not valid JSON: {exc}"))
        return None, findings

    findings.append(Finding(SEVERITY_OK, "marketplace.json is valid JSON"))

    # -- Name validation --
    name = data.get("name")
    if not name:
        findings.append(Finding(SEVERITY_ERROR, "Missing required field: name"))
    elif not isinstance(name, str):
        findings.append(Finding(SEVERITY_ERROR, f"Field 'name' must be a string, got {type(name).__name__}"))
    else:
        if not KEBAB_RE.match(name):
            findings.append(Finding(SEVERITY_ERROR, f"Name '{name}' is not valid kebab-case"))
        if name.lower() in RESERVED_MARKETPLACE_NAMES:
            findings.append(Finding(SEVERITY_ERROR, f"Name '{name}' is a reserved marketplace name"))
        # Impersonation check: brand keyword + "official" together
        lower_name = name.lower()
        has_brand = any(kw in lower_name for kw in BRAND_KEYWORDS)
        has_official = "official" in lower_name
        if has_brand and has_official:
            findings.append(Finding(SEVERITY_ERROR, f"Name '{name}' may impersonate an official Anthropic marketplace"))

    # -- Owner validation --
    owner = data.get("owner")
    if not owner:
        findings.append(Finding(SEVERITY_ERROR, "Missing required field: owner"))
    elif not isinstance(owner, dict):
        findings.append(Finding(SEVERITY_ERROR, f"Field 'owner' must be an object, got {type(owner).__name__}"))
    elif not owner.get("name"):
        findings.append(Finding(SEVERITY_ERROR, "Missing required field: owner.name"))

    # -- Plugins array validation --
    plugins = data.get("plugins")
    if plugins is None:
        findings.append(Finding(SEVERITY_ERROR, "Missing required field: plugins"))
    elif not isinstance(plugins, list):
        findings.append(Finding(SEVERITY_ERROR, f"Field 'plugins' must be an array, got {type(plugins).__name__}"))
    else:
        findings.append(Finding(SEVERITY_OK, f"plugins array found with {len(plugins)} entries"))
        # Validate each plugin entry
        seen_names: set[str] = set()
        for i, plugin in enumerate(plugins):
            if not isinstance(plugin, dict):
                findings.append(Finding(SEVERITY_ERROR, f"Plugin entry {i}: expected object, got {type(plugin).__name__}"))
                continue
            pname = plugin.get("name", f"(entry {i})")

            if not plugin.get("name"):
                findings.append(Finding(SEVERITY_ERROR, f"Plugin entry {i}: missing name"))
                continue

            if pname in seen_names:
                findings.append(Finding(SEVERITY_ERROR, f"Plugin '{pname}': duplicate name"))
            seen_names.add(pname)

            # -- Source validation (CRITICAL: must be GitHub, not local) --
            source = plugin.get("source")
            if not source:
                findings.append(Finding(SEVERITY_ERROR, f"Plugin '{pname}': missing source"))
            elif isinstance(source, str):
                # String source = local path -- this is WRONG for a marketplace hub
                findings.append(
                    Finding(
                        SEVERITY_ERROR,
                        f"Plugin '{pname}': source is a local path string '{source}' "
                        f"-- marketplaces must use {{\"source\": \"github\", \"repo\": \"owner/repo\"}}",
                    )
                )
            elif isinstance(source, dict):
                src_type = source.get("source", "")
                repo = source.get("repo", "")

                if src_type != "github":
                    findings.append(
                        Finding(
                            SEVERITY_ERROR,
                            f"Plugin '{pname}': source.source is '{src_type}' "
                            f"-- marketplaces must use 'github' sources only",
                        )
                    )
                elif not repo:
                    findings.append(Finding(SEVERITY_ERROR, f"Plugin '{pname}': source.repo is missing"))
                elif "/" not in repo:
                    findings.append(
                        Finding(SEVERITY_ERROR, f"Plugin '{pname}': source.repo '{repo}' must be owner/repo format")
                    )
                else:
                    parts = repo.split("/")
                    if len(parts) != 2 or not parts[0] or not parts[1]:
                        findings.append(
                            Finding(
                                SEVERITY_ERROR,
                                f"Plugin '{pname}': source.repo '{repo}' must be exactly owner/repo",
                            )
                        )
                    else:
                        findings.append(Finding(SEVERITY_OK, f"Plugin '{pname}': valid GitHub source -> {repo}"))

                # Flag local path fields as errors on marketplace plugins
                if plugin.get("path"):
                    findings.append(
                        Finding(
                            SEVERITY_ERROR,
                            f"Plugin '{pname}': has 'path' field -- marketplaces must not use local paths",
                        )
                    )
            else:
                findings.append(
                    Finding(SEVERITY_ERROR, f"Plugin '{pname}': source has invalid type {type(source).__name__}")
                )

    return data, findings


# ---------------------------------------------------------------------------
# Standard file checks
# ---------------------------------------------------------------------------


def check_standard_files(marketplace_dir: Path) -> list[Finding]:
    """Check for standard marketplace infrastructure files."""
    findings: list[Finding] = []

    for rel_path, required in STANDARD_FILES:
        full_path = marketplace_dir / rel_path
        if full_path.exists():
            findings.append(Finding(SEVERITY_OK, f"Found {rel_path}"))
        elif required:
            findings.append(Finding(SEVERITY_ERROR, f"Missing required file: {rel_path}"))
        else:
            findings.append(Finding(SEVERITY_WARN, f"Missing recommended file: {rel_path}"))

    return findings


# ---------------------------------------------------------------------------
# Fix mode: generate missing standard files using generator templates
# ---------------------------------------------------------------------------


def fix_missing_files(marketplace_dir: Path, data: dict, dry_run: bool) -> list[Finding]:
    """Generate missing standard files using templates from generate_marketplace_repo.

    Only generates files that do NOT already exist -- never overwrites.
    """
    # Import template generators from the sibling module
    from generate_marketplace_repo import (
        _cliff_toml,
        _gitignore,
        _pre_push_hook,
        _readme,
        _update_catalog_script,
        _update_catalog_workflow,
        _validate_workflow,
        make_executable,
        write_file,
    )

    findings: list[Finding] = []
    name = data.get("name", "my-marketplace")
    owner = data.get("owner", {})
    owner_name = owner.get("name", "Unknown") if isinstance(owner, dict) else "Unknown"
    description = data.get("metadata", {}).get("description", f"{name} marketplace")
    plugins = data.get("plugins", [])

    # Infer github_owner from the first plugin repo, or fall back to owner_name
    github_owner = owner_name.lower().replace(" ", "-")
    for p in plugins:
        src = p.get("source", {})
        if isinstance(src, dict) and src.get("repo"):
            github_owner = src["repo"].split("/")[0]
            break

    # Map of relative path -> (content, needs_executable)
    file_templates: dict[str, tuple[str, bool]] = {
        "README.md": (_readme(name, description, github_owner, plugins), False),
        ".gitignore": (_gitignore(), False),
        ".github/workflows/validate.yml": (_validate_workflow(), False),
        ".github/workflows/update-catalog.yml": (_update_catalog_workflow(name), False),
        "scripts/update_catalog.py": (_update_catalog_script(name), False),
        "cliff.toml": (_cliff_toml(name, github_owner), False),
        ".githooks/pre-push": (_pre_push_hook(), True),
    }

    generated_count = 0
    for rel_path, (content, executable) in file_templates.items():
        full_path = marketplace_dir / rel_path
        if full_path.exists():
            continue  # Never overwrite existing files

        write_file(full_path, content, dry_run)
        if executable:
            make_executable(full_path, dry_run)

        prefix = "[DRY-RUN] Would generate" if dry_run else "Generated"
        findings.append(Finding(SEVERITY_OK, f"{prefix}: {rel_path}"))
        generated_count += 1

    if generated_count == 0:
        findings.append(Finding(SEVERITY_OK, "All standard files already exist -- nothing to generate"))

    return findings


# ---------------------------------------------------------------------------
# Main audit orchestration
# ---------------------------------------------------------------------------


def audit_marketplace(marketplace_dir: Path, fix: bool, dry_run: bool) -> int:
    """Run the full marketplace audit. Returns exit code (0=ok, 1=errors found)."""
    marketplace_dir = marketplace_dir.resolve()

    print(f"{BOLD}Marketplace Audit: {marketplace_dir}{NC}")
    print()

    # -- Phase 1: Validate marketplace.json --
    print(f"{CYAN}[1/3] Validating marketplace.json{NC}")
    data, json_findings = validate_marketplace_json(marketplace_dir)
    for f in json_findings:
        print(f)
    print()

    # -- Phase 2: Check standard files --
    print(f"{CYAN}[2/3] Checking standard infrastructure files{NC}")
    file_findings = check_standard_files(marketplace_dir)
    for f in file_findings:
        print(f)
    print()

    # -- Phase 3: Fix mode (optional) --
    fix_findings: list[Finding] = []
    if fix and data is not None:
        print(f"{CYAN}[3/3] Generating missing standard files{NC}")
        fix_findings = fix_missing_files(marketplace_dir, data, dry_run)
        for f in fix_findings:
            print(f)
        print()
    elif fix and data is None:
        print(f"{RED}[3/3] Cannot fix: marketplace.json is missing or invalid{NC}")
        print()

    # -- Summary --
    all_findings = json_findings + file_findings + fix_findings
    error_count = sum(1 for f in all_findings if f.severity == SEVERITY_ERROR)
    warn_count = sum(1 for f in all_findings if f.severity == SEVERITY_WARN)
    ok_count = sum(1 for f in all_findings if f.severity == SEVERITY_OK)

    print(f"{BOLD}Summary:{NC}")
    print(f"  {GREEN}{ok_count} passed{NC}  |  {YELLOW}{warn_count} warnings{NC}  |  {RED}{error_count} errors{NC}")

    if error_count > 0:
        print(f"\n{RED}RESULT: FAILED -- {error_count} error(s) found{NC}")
        return 1

    if warn_count > 0:
        print(f"\n{YELLOW}RESULT: PASSED with {warn_count} warning(s){NC}")
    else:
        print(f"\n{GREEN}RESULT: PASSED -- marketplace is fully standardized{NC}")

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Parse arguments and run the marketplace audit."""
    parser = argparse.ArgumentParser(
        description="Audit and standardize a marketplace repository to match CPV standards.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Audit only (no changes)
  uv run scripts/standardize_marketplace.py /path/to/marketplace

  # Audit and generate missing standard files
  uv run scripts/standardize_marketplace.py /path/to/marketplace --fix

  # Preview what --fix would generate without writing
  uv run scripts/standardize_marketplace.py /path/to/marketplace --fix --dry-run
""",
    )

    parser.add_argument(
        "marketplace_path",
        type=Path,
        help="Path to the marketplace repository root",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Generate missing standard files using CPV templates",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what --fix would generate without writing files",
    )

    args = parser.parse_args()

    if not args.marketplace_path.is_dir():
        print(f"{RED}Error:{NC} Not a directory: {args.marketplace_path}", file=sys.stderr)
        return 1

    if args.dry_run and not args.fix:
        print(f"{YELLOW}Warning:{NC} --dry-run has no effect without --fix", file=sys.stderr)

    return audit_marketplace(args.marketplace_path, fix=args.fix, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
