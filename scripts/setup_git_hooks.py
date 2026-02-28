#!/usr/bin/env python3
"""setup_git_hooks.py - Install git hooks for plugin validation (cross-platform).

This script sets up pre-push hooks that automatically validate and lint
the plugin before pushes. It replaces the bash version for cross-platform
compatibility (Linux, macOS, Windows).

Usage:
    python scripts/setup_git_hooks.py              # Install hooks (copy)
    python scripts/setup_git_hooks.py --symlink     # Install as symlinks
    python scripts/setup_git_hooks.py --remove      # Remove installed hooks
    python scripts/setup_git_hooks.py --help        # Show usage info
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Only pre-push is used (per project decision -- NOT pre-commit)
# ---------------------------------------------------------------------------
HOOKS: list[str] = ["pre-push"]


# ---------------------------------------------------------------------------
# Color helpers -- disabled on Windows cmd.exe / when stdout is not a tty
# ---------------------------------------------------------------------------


def _colors_supported() -> bool:
    """Return True when the terminal likely supports ANSI escape codes."""
    if os.name == "nt":
        # Windows Terminal and recent cmd.exe honour VIRTUAL_TERMINAL_PROCESSING,
        # but the safest heuristic is checking for the WT_SESSION env var (Windows
        # Terminal) or ANSICON. Fall back to False for classic cmd.exe.
        if os.environ.get("WT_SESSION") or os.environ.get("ANSICON"):
            return True
        return False
    # On Unix-like systems respect isatty
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_USE_COLOR = _colors_supported()

# ANSI escape sequences
_RED = "\033[0;31m" if _USE_COLOR else ""
_YELLOW = "\033[1;33m" if _USE_COLOR else ""
_GREEN = "\033[0;32m" if _USE_COLOR else ""
_BLUE = "\033[0;34m" if _USE_COLOR else ""
_NC = "\033[0m" if _USE_COLOR else ""


def _info(msg: str) -> None:
    print(f"{_BLUE}{msg}{_NC}")


def _ok(msg: str) -> None:
    print(f"{_GREEN}{msg}{_NC}")


def _warn(msg: str) -> None:
    print(f"{_YELLOW}{msg}{_NC}")


def _err(msg: str) -> None:
    print(f"{_RED}{msg}{_NC}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Derive the repository root from this script's location.

    The script lives at <repo>/scripts/setup_git_hooks.py, so the repo root
    is one directory up.
    """
    return Path(__file__).resolve().parent.parent


def _remove_hooks(dest_dir: Path) -> None:
    """Remove all managed hooks from .git/hooks/."""
    _info("Removing installed hooks...")
    for hook_name in HOOKS:
        hook_path = dest_dir / hook_name
        if hook_path.exists() or hook_path.is_symlink():
            hook_path.unlink()
            print(f"  {_GREEN}Removed:{_NC} {hook_name}")
        else:
            print(f"  {_YELLOW}Not found:{_NC} {hook_name}")
    print()
    _ok("Hooks removed successfully.")


def _install_hooks(src_dir: Path, dest_dir: Path, *, use_symlinks: bool) -> None:
    """Install hooks by copying or symlinking from git-hooks/ to .git/hooks/."""
    _info("Installing git hooks...")
    print()

    for hook_name in HOOKS:
        src_path = src_dir / hook_name
        dest_path = dest_dir / hook_name

        # Verify the source hook file exists
        if not src_path.is_file():
            print(f"  {_RED}ERROR:{_NC} Source hook not found: {src_path}")
            continue

        # Remove existing hook (regular file or dangling symlink)
        if dest_path.exists() or dest_path.is_symlink():
            print(f"  {_YELLOW}Replacing existing:{_NC} {hook_name}")
            dest_path.unlink()

        # Install: symlink or copy
        if use_symlinks:
            try:
                os.symlink(src_path, dest_path)
            except OSError as exc:
                # On older Windows without developer mode, symlinks need admin
                _err(
                    f"  Failed to create symlink for {hook_name}: {exc}\n"
                    "  On Windows you may need Administrator privileges or "
                    "Developer Mode enabled.\n"
                    "  Falling back to copy..."
                )
                shutil.copy2(src_path, dest_path)
                print(f"  {_GREEN}Copied (fallback):{_NC} {hook_name}")
            else:
                print(f"  {_GREEN}Symlinked:{_NC} {hook_name}")
        else:
            shutil.copy2(src_path, dest_path)
            print(f"  {_GREEN}Copied:{_NC} {hook_name}")

        # Make the hook executable (no-op on Windows but safe to call)
        _make_executable(dest_path)

    # Summary
    print()
    _info("----------------------------------------")
    _ok("Git hooks installed successfully!")
    print()
    _info("Installed hooks:")
    print("  - pre-push: Read-only linting + plugin validation (blocks ALL issues)")
    print()
    _info("To test the hooks:")
    print("  git push --dry-run origin HEAD")
    print()
    _info("To bypass hooks temporarily:")
    print("  git commit --no-verify -m 'message'")
    print("  git push --no-verify")
    print()
    if use_symlinks:
        _warn("Note: Hooks are symlinked. Changes to git-hooks/ are immediate.")
    else:
        _warn("Note: Hooks are copied. Re-run this script after changes to git-hooks/.")


def _make_executable(path: Path) -> None:
    """Set the executable bit on *path* (u+x, g+x, o+x).

    On Windows this is a harmless no-op because the OS ignores Unix permission
    bits, but calling it keeps the logic unconditional.
    """
    current_mode = path.stat().st_mode
    path.chmod(current_mode | 0o755)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install git hooks for plugin validation.",
        epilog="Default behavior: Copy hooks from git-hooks/ to .git/hooks/.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--symlink",
        action="store_true",
        help="Install hooks as symlinks (useful for development)",
    )
    group.add_argument(
        "--remove",
        action="store_true",
        help="Remove installed hooks",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Banner
    _info("========================================")
    _info("  Git Hooks Setup for Plugin Validation")
    _info("========================================")
    print()

    repo_root = _repo_root()
    git_dir = repo_root / ".git"
    hooks_src = repo_root / "git-hooks"
    hooks_dest = git_dir / "hooks"

    # Validate that we are inside a git repository
    if not git_dir.is_dir():
        _err("ERROR: .git directory not found.")
        print("This script must be run from within a git repository.")
        print()
        print("To initialize a git repository, run:")
        print("  git init")
        sys.exit(1)

    # Validate that the source hooks directory exists
    if not hooks_src.is_dir():
        _err("ERROR: git-hooks directory not found.")
        print(f"Expected location: {hooks_src}")
        sys.exit(1)

    # Create .git/hooks/ if it does not exist
    if not hooks_dest.is_dir():
        _info("Creating .git/hooks directory...")
        hooks_dest.mkdir(parents=True, exist_ok=True)

    # Dispatch
    if args.remove:
        _remove_hooks(hooks_dest)
    else:
        _install_hooks(hooks_src, hooks_dest, use_symlinks=args.symlink)


if __name__ == "__main__":
    main()
