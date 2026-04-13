#!/usr/bin/env python3
"""GitHub repository validation for Claude Code plugins and marketplaces.

Clones a GitHub repository to a temporary directory, runs the CPV validation
suite, optionally runs skill-audit for security scanning, reports results,
and cleans up. No installation or registration occurs.

Usage:
    uv run scripts/manage_github_validate.py --plugin <owner/repo>
    uv run scripts/manage_github_validate.py --plugin <owner/repo> --audit
    uv run scripts/manage_github_validate.py --marketplace <owner/repo>
    uv run scripts/manage_github_validate.py --marketplace <owner/repo> --audit
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cpv_management_common import err, info, ok, warn
from manage_marketplace import _normalize_github_source

__all__ = [
    "validate_github_plugin",
    "validate_github_marketplace",
    "audit_github_plugin",
    "audit_github_marketplace",
]


def _clone_repo(repo: str, dest: Path) -> bool:
    """Clone a GitHub repo to dest using gh CLI. Returns True on success."""
    gh_bin = shutil.which("gh")
    if not gh_bin:
        err("'gh' CLI not found on PATH. Install it: https://cli.github.com/")
        return False
    info(f"Cloning {repo}...")
    result = subprocess.run(
        [gh_bin, "repo", "clone", repo, str(dest), "--", "--depth", "1", "--quiet"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        err(f"Failed to clone {repo}: {result.stderr.strip()}")
        return False
    ok(f"Cloned {repo}")
    return True


def _run_cpv_validate(target: Path, script_name: str = "validate_plugin.py") -> int:
    """Run a CPV validation script on target. Returns exit code."""
    scripts_dir = Path(__file__).resolve().parent
    script = scripts_dir / script_name
    if not script.exists():
        err(f"Validation script not found: {script}")
        return 1
    info(f"Running {script_name}...")
    result = subprocess.run(
        [sys.executable, str(script), str(target)],
        timeout=300,
    )
    return result.returncode


def _run_skill_audit(target: Path) -> int:
    """Run skill-audit on target. Returns exit code."""
    audit_bin = shutil.which("skill-audit")
    if not audit_bin:
        warn("'skill-audit' not found. Install: pip install skill-audit")
        return 1
    info("Running security audit (skill-audit)...")
    result = subprocess.run(
        [audit_bin, str(target), "-v"],
        timeout=300,
    )
    return result.returncode


def validate_github_plugin(repo: str) -> int:
    """Clone and validate a GitHub plugin without installing."""
    repo = _normalize_github_source(repo)
    tmpdir = tempfile.mkdtemp(prefix="cpv-github-plugin-")
    target = Path(tmpdir) / "plugin"
    try:
        if not _clone_repo(repo, target):
            return 1
        return _run_cpv_validate(target, "validate_plugin.py")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def validate_github_marketplace(repo: str) -> int:
    """Clone and validate a GitHub marketplace without registering."""
    repo = _normalize_github_source(repo)
    tmpdir = tempfile.mkdtemp(prefix="cpv-github-mkt-")
    target = Path(tmpdir) / "marketplace"
    try:
        if not _clone_repo(repo, target):
            return 1
        return _run_cpv_validate(target, "validate_marketplace.py")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def audit_github_plugin(repo: str) -> int:
    """Clone, validate, and security-audit a GitHub plugin."""
    repo = _normalize_github_source(repo)
    tmpdir = tempfile.mkdtemp(prefix="cpv-audit-plugin-")
    target = Path(tmpdir) / "plugin"
    try:
        if not _clone_repo(repo, target):
            return 1
        # Run security audit first
        audit_rc = _run_skill_audit(target)
        # Then full validation
        val_rc = _run_cpv_validate(target, "validate_plugin.py")
        # Return worst exit code
        return max(audit_rc, val_rc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def audit_github_marketplace(repo: str) -> int:
    """Clone, validate, and security-audit a GitHub marketplace."""
    repo = _normalize_github_source(repo)
    tmpdir = tempfile.mkdtemp(prefix="cpv-audit-mkt-")
    target = Path(tmpdir) / "marketplace"
    try:
        if not _clone_repo(repo, target):
            return 1
        audit_rc = _run_skill_audit(target)
        val_rc = _run_cpv_validate(target, "validate_marketplace.py")
        return max(audit_rc, val_rc)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="GitHub plugin/marketplace validation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plugin", type=str, help="Validate a GitHub plugin (owner/repo)")
    group.add_argument("--marketplace", type=str, help="Validate a GitHub marketplace (owner/repo)")
    # Backward compat (hidden) — legacy standalone audit flags
    group.add_argument("--audit-plugin", type=str, help=argparse.SUPPRESS)
    group.add_argument("--audit-marketplace", type=str, help=argparse.SUPPRESS)
    # New --audit flag that combines with --plugin or --marketplace
    parser.add_argument("--audit", action="store_true", help="Also run security audit (skill-audit)")
    args = parser.parse_args()

    if args.audit_plugin:
        sys.exit(audit_github_plugin(args.audit_plugin))
    elif args.audit_marketplace:
        sys.exit(audit_github_marketplace(args.audit_marketplace))
    elif args.plugin:
        if args.audit:
            sys.exit(audit_github_plugin(args.plugin))
        else:
            sys.exit(validate_github_plugin(args.plugin))
    elif args.marketplace:
        if args.audit:
            sys.exit(audit_github_marketplace(args.marketplace))
        else:
            sys.exit(validate_github_marketplace(args.marketplace))


if __name__ == "__main__":
    main()
