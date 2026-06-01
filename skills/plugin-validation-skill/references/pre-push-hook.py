#!/usr/bin/env python3
"""pre-push-hook.py - Prevent pushing broken plugins to GitHub.

Thin wrapper around scripts/validate_plugin.py. Since CPV v2.64.0 the
validator owns repo-wide linting via cpv_lint_engine, so a single
invocation covers ruff + mypy + shellcheck + eslint + ... + structural
checks in one pass — there is no separate lint step.

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


def is_rebase_in_progress() -> bool:
    """Return True if a rebase is in progress — skip hook.

    Resolve the rebase state dirs via ``git rev-parse --git-path`` rather than
    hand-joining ``<repo_root>/.git``: in a linked worktree (and in a submodule)
    ``.git`` is a *file* pointing elsewhere, so ``<repo_root>/.git/rebase-merge``
    never exists and the hook would fail to skip during a worktree rebase. The
    git-path query returns the correct absolute location in every layout.
    """
    for state_dir in ("rebase-merge", "rebase-apply"):
        try:
            path = subprocess.check_output(
                ["git", "rev-parse", "--git-path", state_dir],
                text=True,
            ).strip()
        except subprocess.CalledProcessError:
            return False
        if path and os.path.isdir(path):
            return True
    return False


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
    """Run plugin validation (lint + structure) before push, fail-fast."""
    try:
        repo_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        print(f"{RED}ERROR: Not inside a git repository{NC}")
        return 1

    if is_rebase_in_progress():
        print(f"{YELLOW}Rebase in progress — skipping pre-push hook{NC}")
        return 0

    scripts_dir = find_scripts_dir(repo_root)
    if scripts_dir is None:
        print(f"{YELLOW}WARNING: scripts/ directory not found — skipping hook{NC}")
        return 0

    python = find_python()

    # Plugin validation — owns repo-wide lint via cpv_lint_engine since v2.64.0
    validate_script = os.path.join(scripts_dir, "validate_plugin.py")
    if not os.path.isfile(validate_script):
        print(f"{YELLOW}WARNING: validate_plugin.py not found — skipping hook{NC}")
        return 0

    print(f"{BOLD}Running plugin validation (lint + structure)...{NC}")
    result = subprocess.run([python, validate_script, repo_root, "--verbose"])
    if result.returncode != 0:
        print(f"{RED}{BOLD}Validation failed — push blocked (exit code: {result.returncode}){NC}")
        return result.returncode

    print(f"{GREEN}{BOLD}All checks passed — push allowed{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
