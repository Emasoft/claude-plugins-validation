#!/usr/bin/env python3
"""Publish pipeline: test → validate → bump → commit → push.

Usage:
  uv run python scripts/publish.py --patch   # bump patch and publish
  uv run python scripts/publish.py --minor   # bump minor and publish
  uv run python scripts/publish.py --major   # bump major and publish
  uv run python scripts/publish.py --dry-run --patch  # preview only
"""

import argparse
import subprocess
import sys
from pathlib import Path


def get_plugin_root() -> Path:
    """Resolve plugin root from this script's location."""
    return Path(__file__).resolve().parent.parent


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, print it, and fail fast on error."""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    if check and result.returncode != 0:
        print(f"\n✗ FAILED (exit {result.returncode}): {' '.join(cmd)}", file=sys.stderr)
        sys.exit(result.returncode)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Test → Validate → Bump → Commit → Push")
    bump_group = parser.add_mutually_exclusive_group(required=True)
    bump_group.add_argument("--major", action="store_true", help="Bump major version")
    bump_group.add_argument("--minor", action="store_true", help="Bump minor version")
    bump_group.add_argument("--patch", action="store_true", help="Bump patch version")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest (use if tests were just run)")
    args = parser.parse_args()

    root = get_plugin_root()
    bump_type = "major" if args.major else "minor" if args.minor else "patch"

    # ── Step 1: Check for uncommitted changes ──
    print("\n═══ Step 1: Check working tree ═══")
    result = run(["git", "status", "--porcelain"], cwd=root, check=False)
    if result.stdout.strip():
        print("✗ Uncommitted changes detected. Commit or stash first.", file=sys.stderr)
        print(result.stdout.strip())
        return 1
    print("✓ Working tree clean")

    # ── Step 2: Run tests ──
    if not args.skip_tests:
        print("\n═══ Step 2: Run tests ═══")
        run(["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"], cwd=root)
        print("✓ All tests passed")
    else:
        print("\n═══ Step 2: Tests skipped (--skip-tests) ═══")

    # ── Step 3: Self-validate ──
    print("\n═══ Step 3: Self-validate plugin ═══")
    run(["uv", "run", "python", "scripts/validate_plugin.py", ".", "--strict"], cwd=root)
    print("✓ Plugin validation passed")

    # ── Step 4: Bump version ──
    print(f"\n═══ Step 4: Bump version ({bump_type}) ═══")
    bump_cmd = ["uv", "run", "python", "scripts/bump_version.py", f"--{bump_type}"]
    if args.dry_run:
        bump_cmd.append("--dry-run")
    run(bump_cmd, cwd=root)

    if args.dry_run:
        print("\n✓ Dry run complete — no changes made.")
        return 0

    # Read the new version from plugin.json after bump
    import json
    plugin_json = root / ".claude-plugin" / "plugin.json"
    new_version = json.loads(plugin_json.read_text())["version"]
    print(f"✓ Version bumped to {new_version}")

    # ── Step 5: Commit ──
    print("\n═══ Step 5: Commit version bump ═══")
    run(["git", "add", "-A"], cwd=root)
    run(["git", "commit", "-m", f"chore: bump version to {new_version}"], cwd=root)
    print(f"✓ Committed v{new_version}")

    # ── Step 6: Push ──
    print("\n═══ Step 6: Push to origin ═══")
    run(["git", "push", "origin", "HEAD"], cwd=root)
    print(f"\n✓ Published v{new_version}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
