#!/usr/bin/env python3
"""pre-push-hook.py - Prevent pushing broken plugins to GitHub.

Thin wrapper that delegates to scripts/lint_files.py (read-only linting)
and scripts/validate_plugin.py (plugin validation).

To install:
    cp scripts/pre-push-hook.py .git/hooks/pre-push
    chmod +x .git/hooks/pre-push

Exit codes:
    0 - All validations passed, push allowed
    1 - Validation failed, push blocked
"""

import os
import subprocess
import sys

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BOLD = "\033[1m"
NC = "\033[0m"


def is_rebase_in_progress(git_dir: str) -> bool:
    """Return True if a rebase is in progress — skip hook."""
    return (
        os.path.isdir(os.path.join(git_dir, "rebase-merge"))
        or os.path.isdir(os.path.join(git_dir, "rebase-apply"))
    )


def find_scripts_dir(repo_root: str) -> str | None:
    """Locate scripts/ directory — may be at root or in a subdirectory."""
    candidates = [
        os.path.join(repo_root, "scripts"),
        os.path.join(repo_root, "claude-plugins-validation", "scripts"),
    ]
    for d in candidates:
        if os.path.isdir(d):
            return d
    return None


def find_python() -> str:
    """Return best available Python interpreter."""
    import shutil
    for name in ("python3", "python"):
        if shutil.which(name):
            return name
    return sys.executable


def main() -> int:
    """Run linting and validation sequentially, fail-fast."""
    try:
        repo_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        print(f"{RED}ERROR: Not inside a git repository{NC}")
        return 1

    git_dir = os.path.join(repo_root, ".git")
    if is_rebase_in_progress(git_dir):
        print(f"{YELLOW}Rebase in progress — skipping pre-push hook{NC}")
        return 0

    scripts_dir = find_scripts_dir(repo_root)
    if scripts_dir is None:
        print(f"{YELLOW}WARNING: scripts/ directory not found — skipping hook{NC}")
        return 0

    python = find_python()
    overall = 0

    # Step 1: Read-only linting (lint_files.py)
    lint_script = os.path.join(scripts_dir, "lint_files.py")
    if os.path.isfile(lint_script):
        print(f"{BOLD}Running file linting (read-only)...{NC}")
        result = subprocess.run([python, lint_script, repo_root])
        if result.returncode != 0:
            print(f"{RED}Linting failed — push blocked{NC}")
            overall = 1

    # Step 2: Plugin validation (validate_plugin.py)
    validate_script = os.path.join(scripts_dir, "validate_plugin.py")
    if os.path.isfile(validate_script):
        print(f"{BOLD}Running plugin validation...{NC}")
        result = subprocess.run([python, validate_script, repo_root, "--verbose"])
        if result.returncode != 0:
            print(f"{RED}Validation failed — push blocked{NC}")
            overall = max(overall, result.returncode)

    if overall == 0:
        print(f"{GREEN}{BOLD}All checks passed — push allowed{NC}")
    else:
        print(f"{RED}{BOLD}Push blocked (exit code: {overall}){NC}")

    return overall


if __name__ == "__main__":
    sys.exit(main())
