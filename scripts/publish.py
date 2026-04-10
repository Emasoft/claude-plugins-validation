#!/usr/bin/env python3
"""Unified publish pipeline: test → lint → validate → consistency-check → bump → commit → push.

Absorbs all logic from bump_version.py and check_version_consistency.py into a single script.

Usage:
  uv run python scripts/publish.py --patch            # bump patch and publish
  uv run python scripts/publish.py --minor            # bump minor and publish
  uv run python scripts/publish.py --major            # bump major and publish
  uv run python scripts/publish.py --patch --dry-run   # preview only
  uv run python scripts/publish.py --patch --skip-tests # skip pytest

Exit codes:
    0 - Success
    1 - Any step failed (fail-fast)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── ANSI colors ──────────────────────────────────────────────────────────────

_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
RED = "\033[0;31m" if _USE_COLOR else ""
GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[1;33m" if _USE_COLOR else ""
BLUE = "\033[0;34m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""

# Lazy-initialized gitignore filter for file scanning
_gi_cache: dict = {}


def _get_gi(plugin_root: Path):  # noqa: ANN202
    """Get or create GitignoreFilter for the plugin root, keyed by resolved path."""
    key = str(plugin_root.resolve())
    if key not in _gi_cache:
        from gitignore_filter import GitignoreFilter
        _gi_cache[key] = GitignoreFilter(plugin_root)
    return _gi_cache[key]


# ── Helpers ──────────────────────────────────────────────────────────────────


def get_plugin_root() -> Path:
    """Resolve plugin root from this script's location (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def run(cmd: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, print it, stream output, and fail fast on error."""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        # Print stderr but don't double-print if it's just warnings
        print(result.stderr.strip(), file=sys.stderr)
    if check and result.returncode != 0:
        print(f"\n{RED}✗ FAILED (exit {result.returncode}): {' '.join(cmd)}{NC}", file=sys.stderr)
        sys.exit(result.returncode)
    return result


# ── Semver helpers (absorbed from bump_version.py) ───────────────────────────


def parse_semver(version: str) -> tuple[int, int, int] | None:
    """Parse 'X.Y.Z' into (major, minor, patch), or None if invalid."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def semver_gt(a: str, b: str) -> bool:
    """Return True if version a > version b."""
    pa, pb = parse_semver(a), parse_semver(b)
    if pa is None or pb is None:
        return False
    return pa > pb


def bump_semver(current: str, bump_type: str) -> str | None:
    """Bump version by type ('major', 'minor', 'patch'). Returns new version or None."""
    parts = parse_semver(current)
    if parts is None:
        return None
    major, minor, patch = parts
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return None


# ── Version read/write (absorbed from bump_version.py) ───────────────────────


def get_current_version(plugin_root: Path) -> str | None:
    """Read current version from .claude-plugin/plugin.json."""
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    if not plugin_json.exists():
        return None
    try:
        data = json.loads(plugin_json.read_text(encoding="utf-8"))
        v = data.get("version")
        return v if isinstance(v, str) else None
    except (json.JSONDecodeError, OSError, KeyError) as e:
        print(f"Warning: could not read version from {plugin_json}: {e}")
        return None


def update_plugin_json(plugin_root: Path, new_version: str) -> tuple[bool, str]:
    """Update version field in plugin.json."""
    path = plugin_root / ".claude-plugin" / "plugin.json"
    if not path.exists():
        return False, "plugin.json not found"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        old = data.get("version", "unknown")
        data["version"] = new_version
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True, f"plugin.json: {old} → {new_version}"
    except Exception as e:
        return False, f"plugin.json error: {e}"


def update_pyproject_toml(plugin_root: Path, new_version: str) -> tuple[bool, str]:
    """Update version field in pyproject.toml."""
    path = plugin_root / "pyproject.toml"
    if not path.exists():
        return True, "pyproject.toml not found (skipped)"
    try:
        content = path.read_text(encoding="utf-8")
        pattern = r'^(version\s*=\s*["\'])(\d+\.\d+\.\d+)(["\'])$'
        old_version = None

        def _replace(m: re.Match[str]) -> str:
            nonlocal old_version
            old_version = m.group(2)
            return f"{m.group(1)}{new_version}{m.group(3)}"

        new_content, count = re.subn(pattern, _replace, content, flags=re.MULTILINE)
        if count == 0:
            return True, "pyproject.toml has no version field (skipped)"
        path.write_text(new_content, encoding="utf-8")
        return True, f"pyproject.toml: {old_version} → {new_version}"
    except Exception as e:
        return False, f"pyproject.toml error: {e}"


def update_python_versions(plugin_root: Path, new_version: str) -> list[tuple[bool, str]]:
    """Update __version__ = 'X.Y.Z' in all Python files."""
    gi = _get_gi(plugin_root)
    results: list[tuple[bool, str]] = []
    for py_file in gi.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            pattern = r'^(__version__\s*=\s*["\'])(\d+\.\d+\.\d+)(["\'])$'
            old_v = None

            def _replace(m: re.Match[str]) -> str:
                nonlocal old_v
                old_v = m.group(2)
                return f"{m.group(1)}{new_version}{m.group(3)}"

            new_content, count = re.subn(pattern, _replace, content, flags=re.MULTILINE)
            if count > 0:
                py_file.write_text(new_content, encoding="utf-8")
                rel = py_file.relative_to(plugin_root)
                results.append((True, f"{rel}: {old_v} → {new_version}"))
        except Exception as e:
            rel = py_file.relative_to(plugin_root)
            results.append((False, f"{rel}: {e}"))
    return results


# ── Version consistency check (absorbed from check_version_consistency.py) ───


def check_version_consistency(plugin_root: Path) -> tuple[bool, str]:
    """Check all version sources match. Returns (ok, message)."""
    versions: dict[str, str] = {}  # source_label → version

    # plugin.json
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if pj.exists():
        try:
            v = json.loads(pj.read_text(encoding="utf-8")).get("version")
            if isinstance(v, str):
                versions["plugin.json"] = v
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read version from plugin.json: {e}")

    # pyproject.toml
    pp = plugin_root / "pyproject.toml"
    if pp.exists():
        try:
            m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', pp.read_text(encoding="utf-8"), re.MULTILINE)
            if m:
                versions["pyproject.toml"] = m.group(1)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read version from pyproject.toml: {e}")

    # Python __version__ variables
    gi = _get_gi(plugin_root)
    for py_file in gi.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if m:
                rel = str(py_file.relative_to(plugin_root))
                versions[rel] = m.group(1)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read version from {py_file}: {e}")

    if not versions:
        return True, "No version sources found"

    unique = set(versions.values())
    if len(unique) == 1:
        return True, f"All {len(versions)} sources consistent: {next(iter(unique))}"

    # Mismatch — build detail
    lines = ["Version mismatch detected:"]
    for src, ver in sorted(versions.items()):
        lines.append(f"  {src}: {ver}")
    return False, "\n".join(lines)


# ── Bump all files ───────────────────────────────────────────────────────────


def do_bump(plugin_root: Path, new_version: str, dry_run: bool = False) -> bool:
    """Bump version across all files. Returns True on success."""
    if dry_run:
        print(f"  [DRY-RUN] Would bump to {new_version}")
        return True

    all_results: list[tuple[bool, str]] = []
    all_results.append(update_plugin_json(plugin_root, new_version))
    all_results.append(update_pyproject_toml(plugin_root, new_version))
    all_results.extend(update_python_versions(plugin_root, new_version))

    errors = 0
    for ok, msg in all_results:
        status = f"{GREEN}[OK]{NC}" if ok else f"{RED}[ERROR]{NC}"
        print(f"  {status} {msg}")
        if not ok:
            errors += 1

    return errors == 0


# ── Main pipeline ────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish pipeline: test → lint → validate → consistency → bump → changelog → commit → tag → push → gh release",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --patch              # 1.0.0 → 1.0.1, commit, push
  %(prog)s --minor              # 1.0.0 → 1.1.0, commit, push
  %(prog)s --major              # 1.0.0 → 2.0.0, commit, push
  %(prog)s --patch --dry-run    # preview only, no changes

HARD RULE: No checks can be skipped. Every check (tests, lint, validation,
version consistency) must pass with ZERO errors before commit and push.
There is no --skip-tests, no --skip-lint, no --skip-validate, no --force.
If a check fails, fix the underlying problem. Do not bypass the pipeline.
        """,
    )
    bump_group = parser.add_mutually_exclusive_group(required=True)
    bump_group.add_argument("--major", action="store_true", help="Bump major version")
    bump_group.add_argument("--minor", action="store_true", help="Bump minor version")
    bump_group.add_argument("--patch", action="store_true", help="Bump patch version")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()

    # ── Hard rule enforcement: reject any environment variable that could bypass checks ──
    _forbidden_bypass_vars = [
        "CPV_SKIP_TESTS", "CPV_SKIP_LINT", "CPV_SKIP_VALIDATE",
        "CPV_FORCE_PUBLISH", "CPV_BYPASS_CHECKS", "SKIP_TESTS",
        "SKIP_LINT", "SKIP_VALIDATE", "NO_VERIFY",
    ]
    _bypass_attempted = [v for v in _forbidden_bypass_vars if os.environ.get(v)]
    if _bypass_attempted:
        print(
            f"{RED}✗ Bypass attempt detected. These env vars are FORBIDDEN in publish:{NC}\n"
            f"  {', '.join(_bypass_attempted)}\n"
            f"{RED}The publish pipeline enforces every check. Fix the failures, don't skip them.{NC}",
            file=sys.stderr,
        )
        return 1

    root = get_plugin_root()
    bump_type = "major" if args.major else "minor" if args.minor else "patch"

    # ── Step 1: Clean working tree ──
    print(f"\n{BLUE}═══ Step 1: Check working tree ═══{NC}")
    result = run(["git", "status", "--porcelain"], cwd=root, check=False)
    dirty = result.stdout.strip()
    if dirty:
        # Auto-commit uv.lock if it's the only dirty file (uv run modifies it)
        dirty_files = {line[3:] for line in dirty.splitlines() if line.strip()}
        if dirty_files == {"uv.lock"}:
            print(f"{YELLOW}Auto-committing uv.lock (modified by uv run){NC}")
            run(["git", "add", "uv.lock"], cwd=root)
            run(["git", "commit", "-m", "chore: update uv.lock"], cwd=root)
        else:
            print(f"{RED}✗ Uncommitted changes detected. Commit or stash first.{NC}", file=sys.stderr)
            print(dirty)
            return 1
    print(f"{GREEN}✓ Working tree clean{NC}")

    # ── Step 2: Tests (MANDATORY — cannot be skipped) ──
    print(f"\n{BLUE}═══ Step 2: Run tests (mandatory) ═══{NC}")
    run(["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"], cwd=root)
    print(f"{GREEN}✓ All tests passed{NC}")

    # ── Step 3: Lint (MANDATORY — must pass with zero errors) ──
    print(f"\n{BLUE}═══ Step 3: Lint files (mandatory) ═══{NC}")
    run(["uv", "run", "python", "scripts/lint_files.py", "."], cwd=root)
    print(f"{GREEN}✓ Linting passed{NC}")

    # ── Step 4: Validate (MANDATORY — ZERO errors of any severity) ──
    # Hard rule: publish is blocked on ANY non-zero severity: CRITICAL, MAJOR, MINOR, NIT.
    # WARNING is advisory and does not block. No exceptions.
    print(f"\n{BLUE}═══ Step 4: Validate plugin — ZERO errors required ═══{NC}")
    vresult = run(["uv", "run", "python", "scripts/validate_plugin.py", ".", "--strict"], cwd=root, check=False)
    if vresult.returncode != 0:
        severity_map = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR", 4: "NIT"}
        severity = severity_map.get(vresult.returncode, f"unknown (exit {vresult.returncode})")
        print(
            f"\n{RED}✗ {severity} validation issues found — PUBLISH BLOCKED{NC}\n"
            f"{RED}  Fix ALL issues before publishing. No severity level is allowed to slip through.{NC}",
            file=sys.stderr,
        )
        sys.exit(vresult.returncode)
    print(f"{GREEN}✓ Plugin validation passed (zero errors){NC}")

    # ── Step 5: Version consistency ──
    print(f"\n{BLUE}═══ Step 5: Check version consistency ═══{NC}")
    ok, msg = check_version_consistency(root)
    print(f"  {msg}")
    if not ok:
        print(f"{RED}✗ Fix version mismatches before publishing.{NC}", file=sys.stderr)
        return 1
    print(f"{GREEN}✓ Version consistency OK{NC}")

    # ── Step 6: Bump version ──
    current = get_current_version(root)
    if current is None:
        print(f"{RED}✗ Cannot read current version from plugin.json{NC}", file=sys.stderr)
        return 1

    new_version = bump_semver(current, bump_type)
    if new_version is None:
        print(f"{RED}✗ Current version '{current}' is not valid semver{NC}", file=sys.stderr)
        return 1

    print(f"\n{BLUE}═══ Step 6: Bump version ({bump_type}: {current} → {new_version}) ═══{NC}")
    if not do_bump(root, new_version, dry_run=args.dry_run):
        print(f"{RED}✗ Version bump failed{NC}", file=sys.stderr)
        return 1
    print(f"{GREEN}✓ Version bumped to {new_version}{NC}")

    if args.dry_run:
        print(f"\n{GREEN}✓ Dry run complete — no changes made.{NC}")
        return 0

    # ── Step 7: Generate CHANGELOG.md + release notes with git-cliff ──
    print(f"\n{BLUE}═══ Step 7: Generate CHANGELOG + release notes (git-cliff) ═══{NC}")
    cliff_bin = shutil.which("git-cliff")
    release_notes_file: Path | None = None
    if cliff_bin is None:
        print(
            f"{RED}✗ git-cliff not installed. Required for changelog and release notes.{NC}\n"
            f"{RED}  Install: brew install git-cliff  OR  cargo install git-cliff{NC}",
            file=sys.stderr,
        )
        return 1
    cliff_toml = root / "cliff.toml"
    if not cliff_toml.is_file():
        print(f"{RED}✗ cliff.toml not found. Required for changelog generation.{NC}", file=sys.stderr)
        return 1

    tag_name = f"v{new_version}"

    # Regenerate full CHANGELOG.md — git-cliff reads all git history + cliff.toml config.
    # Using --tag <new> tells git-cliff to label unreleased commits with the new tag.
    run([cliff_bin, "--tag", tag_name, "-o", "CHANGELOG.md"], cwd=root)
    print(f"{GREEN}✓ CHANGELOG.md regenerated with {tag_name}{NC}")

    # Extract release notes for just the new version (for gh release)
    release_notes_file = root / "reports_dev" / f"release-notes-{new_version}.md"
    release_notes_file.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            cliff_bin, "--unreleased", "--tag", tag_name,
            "--strip", "all", "-o", str(release_notes_file),
        ],
        cwd=root,
    )
    print(f"{GREEN}✓ Release notes extracted to {release_notes_file.relative_to(root)}{NC}")

    # ── Step 8: Commit version bump + CHANGELOG ──
    print(f"\n{BLUE}═══ Step 8: Commit version bump + changelog ═══{NC}")
    run(["git", "add", "-A"], cwd=root)
    run(["git", "commit", "-m", f"chore(release): {tag_name}"], cwd=root)
    print(f"{GREEN}✓ Committed {tag_name}{NC}")

    # ── Step 9: Create annotated git tag ──
    print(f"\n{BLUE}═══ Step 9: Create git tag {tag_name} ═══{NC}")
    run(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"], cwd=root)
    print(f"{GREEN}✓ Tag {tag_name} created{NC}")

    # ── Step 10: Push branch + tags ──
    # The pre-push hook verifies publish.py is in the process ancestry
    # (not via env var — that would be trivially spoofable).
    print(f"\n{BLUE}═══ Step 10: Push to origin (branch + tags) ═══{NC}")
    run(["git", "push", "origin", "HEAD"], cwd=root)
    run(["git", "push", "origin", tag_name], cwd=root)
    print(f"{GREEN}✓ Pushed branch and tag {tag_name}{NC}")

    # ── Step 11: Create GitHub release with notes ──
    print(f"\n{BLUE}═══ Step 11: Create GitHub release ═══{NC}")
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        print(
            f"{YELLOW}⚠ gh CLI not installed. Tag pushed but GitHub release not created.{NC}\n"
            f"{YELLOW}  Install: brew install gh{NC}",
            file=sys.stderr,
        )
    else:
        gh_result = run(
            [
                gh_bin, "release", "create", tag_name,
                "--title", f"Release {tag_name}",
                "--notes-file", str(release_notes_file),
            ],
            cwd=root,
            check=False,
        )
        if gh_result.returncode == 0:
            print(f"{GREEN}✓ GitHub release {tag_name} published{NC}")
        else:
            print(
                f"{YELLOW}⚠ gh release failed (tag is already pushed — you can create release manually){NC}",
                file=sys.stderr,
            )

    print(f"\n{GREEN}✓ Published v{new_version}{NC}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
