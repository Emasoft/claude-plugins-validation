#!/usr/bin/env python3
"""Unified publish pipeline: test → lint → validate → marketplace-registration → bump → commit → push.

Absorbs all logic from bump_version.py and check_version_consistency.py into a single script.

Usage:
  uv run python scripts/publish.py --patch            # bump patch and publish
  uv run python scripts/publish.py --minor            # bump minor and publish
  uv run python scripts/publish.py --major            # bump major and publish
  uv run python scripts/publish.py --patch --dry-run  # preview only, no changes
  uv run python scripts/publish.py --print-gates      # print gate list and exit

HARD RULE: No checks can be skipped. There are no --skip-* flags, no env
var bypasses, no --force. Every gate must pass before any version bump,
tag, push, or GitHub release is performed. Publish is blocked on ANY
CRITICAL, MAJOR, MINOR, or NIT severity finding. WARNING is advisory only.

Exit codes:
    0 - Success
    1 - Preflight, tests, lint, version-consistency, marketplace-registration,
        bump, changelog, commit, tag, or push failed (fail-fast)
    1-4 - Plugin validation severity (1=CRITICAL, 2=MAJOR, 3=MINOR, 4=NIT)
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


# ── Gate list for --print-gates and --help ──────────────────────────────────

GATES: list[tuple[str, str]] = [
    ("Gate 0", "Bypass-var rejection (CPV_SKIP_*, SKIP_*, NO_VERIFY)"),
    ("Gate 1", "Clean working tree (git status --porcelain)"),
    ("Gate 2", "Lint + typecheck (ruff + mypy via scripts/lint_files.py)"),
    ("Gate 3", "Tests (uv run pytest tests/ -x)"),
    (
        "Gate 4",
        "Plugin validation (validate_plugin.py --strict) — blocks on CRITICAL/MAJOR/MINOR/NIT; WARNING advisory only",
    ),
    ("Gate 5", "Marketplace validation (validate_marketplace.py --strict) — Layout B only"),
    ("Gate 6", "Marketplace-registration check — verifies plugin is wired to its marketplace"),
    ("Gate 7", "Version consistency (plugin.json / pyproject.toml / __version__)"),
    ("Gate 8", "Bump version (auto from git-cliff, overridable via --major/--minor/--patch)"),
    ("Gate 9", "Generate CHANGELOG.md + release notes (git-cliff --bump --unreleased --tag)"),
    ("Gate 10", "Commit bump + changelog"),
    ("Gate 11", "Create annotated git tag vX.Y.Z"),
    ("Gate 12", "Push branch + tag to origin"),
    ("Gate 13", "Create GitHub release with notes (gh release create)"),
]


def print_gates() -> None:
    """Print the list of gates in order so users see exactly what will run."""
    print(f"{BLUE}Publish pipeline gates (all mandatory, fail-fast):{NC}")
    for name, desc in GATES:
        print(f"  {GREEN}{name}{NC}: {desc}")
    print(
        f"\n{YELLOW}Hard rule: no --skip-* flags, no env var bypasses, no --force.{NC}\n"
        f"{YELLOW}WARNING is the only severity that does not block.{NC}"
    )


# ── Layout detection and marketplace-registration check (Task 2) ─────────────


def find_parent_marketplace(plugin_root: Path) -> Path | None:
    """Walk up from plugin root looking for a parent marketplace.json.

    Returns the path to the marketplace repo root (the dir containing
    .claude-plugin/marketplace.json), or None if no parent marketplace found.
    Only returns a match if plugin_root is actually nested under plugins/<name>/
    of the marketplace repo (Layout B signature).
    """
    current = plugin_root.resolve().parent
    while current != current.parent:
        mp_json = current / ".claude-plugin" / "marketplace.json"
        if mp_json.is_file():
            # Confirm plugin_root is under <current>/plugins/<name>/
            try:
                rel = plugin_root.resolve().relative_to(current)
                parts = rel.parts
                if len(parts) >= 2 and parts[0] == "plugins":
                    return current
            except ValueError:
                pass
            return None
        current = current.parent
    return None


def detect_layout(plugin_root: Path) -> tuple[str, dict[str, str | Path | None]]:
    """Detect whether this repo is Layout A (standalone plugin), Layout B (nested),
    or 'none' (no marketplace wiring).

    Returns (layout, details) where layout is one of 'A', 'B', 'none' and
    details is a dict with layout-specific fields used by the check stage.
    """
    # Layout B check first: a plugin nested inside a marketplace repo
    parent_mp = find_parent_marketplace(plugin_root)
    if parent_mp is not None:
        plugin_name = plugin_root.name
        return "B", {"marketplace_root": parent_mp, "plugin_name": plugin_name}

    # Layout A check: standalone plugin that may reference a remote marketplace
    notify_wf = plugin_root / ".github" / "workflows" / "notify-marketplace.yml"
    if notify_wf.is_file():
        mkt_owner, mkt_repo = _parse_notify_workflow(notify_wf)
        if mkt_owner and mkt_repo:
            return "A", {"notify_workflow": notify_wf, "mkt_owner": mkt_owner, "mkt_repo": mkt_repo}
        return "A", {"notify_workflow": notify_wf, "mkt_owner": None, "mkt_repo": None}

    return "none", {}


def _parse_notify_workflow(path: Path) -> tuple[str | None, str | None]:
    """Extract MARKETPLACE_OWNER and MARKETPLACE_REPO from a notify-marketplace.yml.

    The workflow is small and well-known — we grep for two lines rather than
    pulling a YAML dep. Returns (owner, repo) or (None, None) if not found.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    m_owner = re.search(r"^\s*MARKETPLACE_OWNER:\s*['\"]?([^'\"\s]+)['\"]?\s*$", content, re.MULTILINE)
    m_repo = re.search(r"^\s*MARKETPLACE_REPO:\s*['\"]?([^'\"\s]+)['\"]?\s*$", content, re.MULTILINE)
    owner = m_owner.group(1) if m_owner else None
    repo = m_repo.group(1) if m_repo else None
    return owner, repo


def _gh_secret_exists(
    plugin_root: Path,
    secret_name: str,
    *,
    gh_bin: str | None = None,
) -> bool:
    """Check whether a GitHub secret with the given name is configured on this repo.

    Uses `gh secret list --repo <owner>/<repo>` or just `gh secret list` from
    within the repo; parses the output to check for the secret name. We never
    attempt to read the secret value itself — that would be impossible anyway.
    """
    if gh_bin is None:
        gh_bin = shutil.which("gh") or "gh"
    result = subprocess.run(
        [gh_bin, "secret", "list"],
        cwd=plugin_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        # `gh secret list` prints "SECRET_NAME\tUPDATED_AT" (tab-separated).
        first = line.split("\t", 1)[0].strip()
        if first == secret_name:
            return True
    return False


def _fetch_remote_marketplace_json(
    mkt_owner: str,
    mkt_repo: str,
    *,
    gh_bin: str | None = None,
) -> dict | None:
    """Fetch the remote marketplace.json using gh api. Returns parsed dict or None."""
    if gh_bin is None:
        gh_bin = shutil.which("gh") or "gh"
    result = subprocess.run(
        [
            gh_bin,
            "api",
            f"repos/{mkt_owner}/{mkt_repo}/contents/.claude-plugin/marketplace.json",
            "-H",
            "Accept: application/vnd.github.raw+json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _remote_has_receiver_workflow(
    mkt_owner: str,
    mkt_repo: str,
    *,
    gh_bin: str | None = None,
) -> bool:
    """Check whether the remote marketplace repo has a workflow with repository_dispatch."""
    if gh_bin is None:
        gh_bin = shutil.which("gh") or "gh"
    # List the workflow dir
    result = subprocess.run(
        [gh_bin, "api", f"repos/{mkt_owner}/{mkt_repo}/contents/.github/workflows"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return False
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        if not isinstance(name, str) or not name.endswith((".yml", ".yaml")):
            continue
        file_result = subprocess.run(
            [
                gh_bin,
                "api",
                f"repos/{mkt_owner}/{mkt_repo}/contents/.github/workflows/{name}",
                "-H",
                "Accept: application/vnd.github.raw+json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if file_result.returncode == 0 and "repository_dispatch" in file_result.stdout:
            return True
    return False


def _plugin_in_remote_marketplace(mkt_json: dict, plugin_name: str, expected_repo: str | None) -> bool:
    """Return True if marketplace.json lists plugin_name with github source pointing at expected_repo.

    If expected_repo is None, accept any github source entry matching plugin_name.
    """
    plugins = mkt_json.get("plugins")
    if not isinstance(plugins, list):
        return False
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != plugin_name:
            continue
        source = entry.get("source")
        if isinstance(source, dict):
            if source.get("source") != "github" and source.get("type") != "github":
                continue
            repo = source.get("repo")
            if expected_repo is None or repo == expected_repo:
                return True
        elif isinstance(source, str):
            # Bare directory source like "./plugins/foo"
            continue
    return False


def _current_repo_slug(plugin_root: Path) -> str | None:
    """Return the owner/repo slug for the current git origin, or None."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=plugin_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    # Match both SSH (git@github.com:owner/repo.git) and HTTPS (https://github.com/owner/repo.git)
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


# ── Pipeline stages — each returns 0 on success, non-zero on failure ─────────


def stage_bypass_guard() -> int:
    """Gate 0: reject any env var that could bypass checks."""
    forbidden = [
        "CPV_SKIP_TESTS",
        "CPV_SKIP_LINT",
        "CPV_SKIP_VALIDATE",
        "CPV_FORCE_PUBLISH",
        "CPV_BYPASS_CHECKS",
        "SKIP_TESTS",
        "SKIP_LINT",
        "SKIP_VALIDATE",
        "NO_VERIFY",
    ]
    attempted = [v for v in forbidden if os.environ.get(v)]
    if attempted:
        print(
            f"{RED}✗ Bypass attempt detected. These env vars are FORBIDDEN in publish:{NC}\n"
            f"  {', '.join(attempted)}\n"
            f"{RED}The publish pipeline enforces every check. Fix the failures, don't skip them.{NC}",
            file=sys.stderr,
        )
        return 1
    return 0


def stage_check_working_tree(plugin_root: Path) -> int:
    """Gate 1: clean working tree check. Auto-commits uv.lock if only diff."""
    print(f"\n{BLUE}═══ Gate 1: Check working tree ═══{NC}")
    result = run(["git", "status", "--porcelain"], cwd=plugin_root, check=False)
    dirty = result.stdout.strip()
    if dirty:
        dirty_files = {line[3:] for line in dirty.splitlines() if line.strip()}
        if dirty_files == {"uv.lock"}:
            print(f"{YELLOW}Auto-committing uv.lock (modified by uv run){NC}")
            run(["git", "add", "uv.lock"], cwd=plugin_root)
            run(["git", "commit", "-m", "chore: update uv.lock"], cwd=plugin_root)
        else:
            print(f"{RED}✗ Uncommitted changes detected. Commit or stash first.{NC}", file=sys.stderr)
            print(dirty)
            return 1
    print(f"{GREEN}✓ Working tree clean{NC}")
    return 0


def stage_run_tests(plugin_root: Path) -> int:
    """Gate 2: run tests — mandatory, cannot be skipped."""
    print(f"\n{BLUE}═══ Gate 2: Run tests (mandatory) ═══{NC}")
    run(["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"], cwd=plugin_root)
    print(f"{GREEN}✓ All tests passed{NC}")
    return 0


def stage_run_lint(plugin_root: Path) -> int:
    """Gate 3: run lint — mandatory, must pass with zero errors."""
    print(f"\n{BLUE}═══ Gate 3: Lint files (mandatory) ═══{NC}")
    run(["uv", "run", "python", "scripts/lint_files.py", "."], cwd=plugin_root)
    print(f"{GREEN}✓ Linting passed{NC}")
    return 0


def stage_validate_plugin(plugin_root: Path) -> int:
    """Gate 4: validate plugin in strict mode — blocks on ANY CRITICAL/MAJOR/MINOR/NIT.

    WARNING (exit code 0 with warning output) is advisory and does not block.
    Returns the validator's exit code directly (1-4 severity or 0 success).
    """
    print(f"\n{BLUE}═══ Gate 4: Validate plugin — ZERO errors required ═══{NC}")
    vresult = run(
        ["uv", "run", "python", "scripts/validate_plugin.py", ".", "--strict"],
        cwd=plugin_root,
        check=False,
    )
    if vresult.returncode != 0:
        severity_map = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR", 4: "NIT"}
        severity = severity_map.get(vresult.returncode, f"unknown (exit {vresult.returncode})")
        print(
            f"\n{RED}✗ {severity} validation issues found — PUBLISH BLOCKED{NC}\n"
            f"{RED}  Fix ALL issues before publishing. No severity level is allowed to slip through.{NC}\n"
            f"{RED}  Fix command: uv run python scripts/validate_plugin.py . --strict{NC}",
            file=sys.stderr,
        )
        return vresult.returncode
    print(f"{GREEN}✓ Plugin validation passed (zero errors){NC}")
    return 0


def stage_validate_marketplace(plugin_root: Path, layout: str) -> int:
    """Gate 5: validate marketplace in strict mode — only runs for Layout B.

    For Layout B, publish.py runs at the marketplace repo root, and the parent
    marketplace.json must also validate cleanly. For Layout A (standalone plugin),
    there is no local marketplace to validate so this gate is a no-op.
    """
    print(f"\n{BLUE}═══ Gate 5: Marketplace validation ═══{NC}")
    if layout != "B":
        print(f"  (skipped — not a marketplace repo, layout={layout})")
        print(f"{GREEN}✓ Marketplace validation not applicable{NC}")
        return 0
    vresult = run(
        ["uv", "run", "python", "scripts/validate_marketplace.py", ".", "--strict"],
        cwd=plugin_root,
        check=False,
    )
    if vresult.returncode != 0:
        severity_map = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR", 4: "NIT"}
        severity = severity_map.get(vresult.returncode, f"unknown (exit {vresult.returncode})")
        print(
            f"\n{RED}✗ {severity} marketplace validation issues found — PUBLISH BLOCKED{NC}\n"
            f"{RED}  Fix command: uv run python scripts/validate_marketplace.py . --strict{NC}",
            file=sys.stderr,
        )
        return vresult.returncode
    print(f"{GREEN}✓ Marketplace validation passed (zero errors){NC}")
    return 0


def stage_marketplace_registration_check(plugin_root: Path) -> int:
    """Gate 6: verify the plugin is wired to its marketplace for auto-updates.

    Layout A (standalone plugin referencing a remote marketplace):
      - .github/workflows/notify-marketplace.yml exists and parses
      - MARKETPLACE_PAT secret is configured on this repo
      - Remote marketplace lists this plugin with a github source
      - Remote marketplace has a workflow with repository_dispatch trigger

    Layout B (nested plugin inside a marketplace repo):
      - publish.py is running at the marketplace repo root, not the nested subfolder
      - Parent marketplace.json lists this plugin
      - Parent marketplace.json entry version matches (or will match after bump)

    No-marketplace mode: emits a WARNING (not an error) and proceeds — this is
    valid for first releases or experimental standalone plugins.

    Returns 0 on success (including WARNING mode), 1 on any hard failure.
    """
    print(f"\n{BLUE}═══ Gate 6: Marketplace-registration check ═══{NC}")
    layout, details = detect_layout(plugin_root)

    if layout == "none":
        print(
            f"{YELLOW}⚠ WARNING: no marketplace registration found for this plugin.{NC}\n"
            f"{YELLOW}  If you intend to publish this plugin to a marketplace, run the{NC}\n"
            f"{YELLOW}  setup-marketplace-auto-notification skill to wire up auto-updates.{NC}\n"
            f"{YELLOW}  Allowing release to proceed (standalone/experimental mode).{NC}"
        )
        return 0

    if layout == "A":
        return _check_layout_a(plugin_root, details)

    if layout == "B":
        return _check_layout_b(plugin_root, details)

    print(f"{RED}✗ Unknown layout '{layout}' — cannot verify marketplace registration{NC}", file=sys.stderr)
    return 1


def _check_layout_a(plugin_root: Path, details: dict) -> int:
    """Layout A verification: standalone plugin + remote marketplace."""
    print("  Layout A detected (standalone plugin repo)")
    notify_wf_raw = details.get("notify_workflow")
    mkt_owner_raw = details.get("mkt_owner")
    mkt_repo_raw = details.get("mkt_repo")
    # Narrow types for mypy — details is a loosely-typed dict from detect_layout
    notify_wf: Path | None = notify_wf_raw if isinstance(notify_wf_raw, Path) else None
    mkt_owner: str | None = mkt_owner_raw if isinstance(mkt_owner_raw, str) else None
    mkt_repo: str | None = mkt_repo_raw if isinstance(mkt_repo_raw, str) else None

    # 1. Notify workflow must exist (already checked by detect_layout)
    if notify_wf is None or not notify_wf.is_file():
        print(
            f"{RED}✗ .github/workflows/notify-marketplace.yml missing.{NC}\n"
            f"{RED}  Fix: run the setup-marketplace-auto-notification skill to generate it.{NC}",
            file=sys.stderr,
        )
        return 1

    # 2. Workflow must reference a real marketplace
    if not mkt_owner or not mkt_repo:
        print(
            f"{RED}✗ notify-marketplace.yml does not define MARKETPLACE_OWNER/MARKETPLACE_REPO.{NC}\n"
            f"{RED}  Fix: edit .github/workflows/notify-marketplace.yml or re-run{NC}\n"
            f"{RED}  the setup-marketplace-auto-notification skill.{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  target marketplace: {mkt_owner}/{mkt_repo}")

    gh_bin = shutil.which("gh")
    if gh_bin is None:
        print(
            f"{RED}✗ gh CLI not installed — cannot verify MARKETPLACE_PAT or remote marketplace.{NC}\n"
            f"{RED}  Install: brew install gh{NC}",
            file=sys.stderr,
        )
        return 1

    # 3. MARKETPLACE_PAT secret must exist on this repo (value is never read)
    if not _gh_secret_exists(plugin_root, "MARKETPLACE_PAT", gh_bin=gh_bin):
        print(
            f"{RED}✗ MARKETPLACE_PAT secret is not configured on this plugin repo.{NC}\n"
            f"{RED}  Fix: gh secret set MARKETPLACE_PAT  (value: a PAT with 'repo' scope){NC}\n"
            f"{RED}  Then re-run publish. See skill: setup-marketplace-auto-notification.{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}✓ MARKETPLACE_PAT secret configured{NC}")

    # 4. Plugin must be registered in the remote marketplace.json
    mkt_json = _fetch_remote_marketplace_json(mkt_owner, mkt_repo, gh_bin=gh_bin)
    if mkt_json is None:
        print(
            f"{RED}✗ Could not fetch marketplace.json from {mkt_owner}/{mkt_repo}.{NC}\n"
            f"{RED}  Fix: verify the marketplace repo exists and has .claude-plugin/marketplace.json{NC}\n"
            f"{RED}  Fix command: gh api repos/{mkt_owner}/{mkt_repo}/contents/.claude-plugin/marketplace.json{NC}",
            file=sys.stderr,
        )
        return 1
    plugin_name = _read_plugin_name(plugin_root)
    current_slug = _current_repo_slug(plugin_root)
    if not _plugin_in_remote_marketplace(mkt_json, plugin_name, current_slug):
        print(
            f"{RED}✗ Plugin '{plugin_name}' not registered in {mkt_owner}/{mkt_repo}/.claude-plugin/marketplace.json{NC}\n"
            f"{RED}  with a github source entry for {current_slug}.{NC}\n"
            f"{RED}  Fix: add an entry to the remote marketplace.json with:{NC}\n"
            f'{RED}    {{"name": "{plugin_name}", "source": {{"source": "github", "repo": "{current_slug}"}}}}{NC}\n'
            f"{RED}  See skill: setup-marketplace-auto-notification{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}✓ Plugin registered in remote marketplace.json{NC}")

    # 5. Remote marketplace must have a receiver workflow
    if not _remote_has_receiver_workflow(mkt_owner, mkt_repo, gh_bin=gh_bin):
        print(
            f"{RED}✗ Remote marketplace {mkt_owner}/{mkt_repo} has no workflow with{NC}\n"
            f"{RED}  a 'repository_dispatch' trigger. The notify-marketplace.yml event{NC}\n"
            f"{RED}  will arrive with nothing listening.{NC}\n"
            f"{RED}  Fix: add a workflow in the marketplace repo with:{NC}\n"
            f"{RED}    on: repository_dispatch: types: [plugin-updated]{NC}\n"
            f"{RED}  See skill: setup-marketplace-auto-notification{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}✓ Remote marketplace has receiver workflow{NC}")
    print(f"{GREEN}✓ Layout A marketplace registration verified{NC}")
    return 0


def _check_layout_b(plugin_root: Path, details: dict) -> int:
    """Layout B verification: nested plugin inside a marketplace repo.

    Because Layout B uses atomic marketplace tagging, publish.py must run at the
    MARKETPLACE repo root, not at the nested plugin subfolder. Bumping a nested
    plugin independently would break the marketplace version invariant.
    """
    print("  Layout B detected (nested plugin under marketplace repo)")
    marketplace_root_raw = details.get("marketplace_root")
    plugin_name_raw = details.get("plugin_name")
    # Narrow types for mypy
    marketplace_root: Path | None = marketplace_root_raw if isinstance(marketplace_root_raw, Path) else None
    plugin_name: str = plugin_name_raw if isinstance(plugin_name_raw, str) else plugin_root.name
    if marketplace_root is None:
        print(
            f"{RED}✗ Layout B detected but marketplace_root missing from details dict.{NC}\n"
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1

    # 1. Reject running at nested-plugin level
    if plugin_root.resolve() != marketplace_root.resolve():
        print(
            f"{RED}✗ This is a Layout B nested plugin. publish.py must be run at the{NC}\n"
            f"{RED}  MARKETPLACE repo root, not the nested plugin subfolder.{NC}\n"
            f"{RED}  Bumping a nested plugin independently breaks the atomic marketplace tag.{NC}\n"
            f"{RED}  Fix: cd {marketplace_root} && uv run python scripts/publish.py --patch{NC}\n"
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1

    # 2. Parent marketplace.json must list this plugin
    mp_json_path = marketplace_root / ".claude-plugin" / "marketplace.json"
    try:
        mp_data = json.loads(mp_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"{RED}✗ Could not read {mp_json_path}: {e}{NC}\n"
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1
    entries = mp_data.get("plugins") if isinstance(mp_data, dict) else None
    if not isinstance(entries, list):
        print(
            f"{RED}✗ marketplace.json has no 'plugins' array.{NC}\n"
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1

    registered = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == plugin_name:
            registered = True
            break
    if not registered:
        print(
            f"{RED}✗ Plugin '{plugin_name}' is not registered in {mp_json_path}.{NC}\n"
            f"{RED}  Fix: add an entry like:{NC}\n"
            f'{RED}    {{"name": "{plugin_name}", "source": "./plugins/{plugin_name}"}}{NC}\n'
            f"{RED}  Reference: skills/create-plugin/references/marketplace-layouts.md{NC}",
            file=sys.stderr,
        )
        return 1
    print(f"  {GREEN}✓ Plugin '{plugin_name}' registered in parent marketplace.json{NC}")
    print(f"{GREEN}✓ Layout B marketplace registration verified{NC}")
    return 0


def _read_plugin_name(plugin_root: Path) -> str:
    """Read plugin name from .claude-plugin/plugin.json (falls back to dir name)."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if pj.exists():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            name = data.get("name")
            if isinstance(name, str) and name:
                return name
        except (OSError, json.JSONDecodeError):
            pass
    return plugin_root.name


def stage_version_consistency(plugin_root: Path) -> int:
    """Gate 7: version consistency across plugin.json, pyproject.toml, __version__."""
    print(f"\n{BLUE}═══ Gate 7: Check version consistency ═══{NC}")
    ok, msg = check_version_consistency(plugin_root)
    print(f"  {msg}")
    if not ok:
        print(f"{RED}✗ Fix version mismatches before publishing.{NC}", file=sys.stderr)
        return 1
    print(f"{GREEN}✓ Version consistency OK{NC}")
    return 0


def detect_bump_type(plugin_root: Path) -> str:
    """Use git-cliff --bumped-version to pick the next semver bump automatically.

    git-cliff reads the conventional commits since the last tag and decides
    whether this release should be a major, minor, or patch bump. We compare
    the resulting version string against the current version to figure out
    which component changed.

    Fallback behavior:
      - git-cliff missing → patch (every push bumps something)
      - --bumped-version returns the current version → patch
      - output is malformed or version comparison fails → patch

    The cornerstone rule is "every push is a bump" — picking patch on fallback
    guarantees we never publish without changing the version, even when
    git-cliff can't make a more confident recommendation.
    """
    cliff_bin = shutil.which("git-cliff")
    if cliff_bin is None:
        print(f"{YELLOW}git-cliff not installed — auto-bump falls back to 'patch'.{NC}")
        return "patch"

    current = get_current_version(plugin_root)
    if not current:
        print(f"{YELLOW}Cannot read current version for auto-bump — falling back to 'patch'.{NC}")
        return "patch"

    try:
        result = subprocess.run(
            [cliff_bin, "--bumped-version"],
            capture_output=True,
            text=True,
            cwd=str(plugin_root),
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"{YELLOW}git-cliff --bumped-version failed ({exc}) — falling back to 'patch'.{NC}")
        return "patch"

    if result.returncode != 0:
        stderr = result.stderr.strip() or "no stderr"
        print(f"{YELLOW}git-cliff --bumped-version exit {result.returncode}: {stderr} — falling back to 'patch'.{NC}")
        return "patch"

    # git-cliff prints the predicted version (sometimes with a "v" prefix, sometimes bare),
    # possibly along with warning lines on stderr. stdout should be one line.
    bumped_raw = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    bumped = bumped_raw.lstrip("v").strip()
    if not bumped or bumped == current:
        return "patch"

    try:
        cur_parts = [int(p) for p in current.split(".")[:3]]
        new_parts = [int(p) for p in bumped.split(".")[:3]]
        while len(cur_parts) < 3:
            cur_parts.append(0)
        while len(new_parts) < 3:
            new_parts.append(0)
    except ValueError:
        return "patch"

    if new_parts[0] > cur_parts[0]:
        return "major"
    if new_parts[1] > cur_parts[1]:
        return "minor"
    return "patch"


def stage_bump(plugin_root: Path, bump_type: str, dry_run: bool) -> tuple[int, str | None]:
    """Gate 8: bump version across all files. Returns (exit_code, new_version)."""
    current = get_current_version(plugin_root)
    if current is None:
        print(f"{RED}✗ Cannot read current version from plugin.json{NC}", file=sys.stderr)
        return 1, None
    new_version = bump_semver(current, bump_type)
    if new_version is None:
        print(f"{RED}✗ Current version '{current}' is not valid semver{NC}", file=sys.stderr)
        return 1, None
    print(f"\n{BLUE}═══ Gate 8: Bump version ({bump_type}: {current} → {new_version}) ═══{NC}")
    if not do_bump(plugin_root, new_version, dry_run=dry_run):
        print(f"{RED}✗ Version bump failed{NC}", file=sys.stderr)
        return 1, None
    print(f"{GREEN}✓ Version bumped to {new_version}{NC}")
    # Also update the README version badge in-place so it never drifts.
    stage_update_readme_badge(plugin_root, current, new_version, dry_run)
    return 0, new_version


def stage_update_readme_badge(plugin_root: Path, old_version: str, new_version: str, dry_run: bool) -> None:
    """Part of Gate 8: update the README.md version badge in-place.

    Two-stage match strategy:
      1. Exact-string substitution `version-<old>-blue` → `version-<new>-blue`
      2. Regex fallback `version-\\d+\\.\\d+\\.\\d+-blue` for any drifted badge

    The fallback prevents the same "stale forever" trap that bit CPV's own
    README (the badge said 2.6.4 while real version was 2.12.25 — 20 releases
    of silent skip). When neither match succeeds, prints a WARNING (not a
    silent skip) so the author notices the README has no badge to update.
    """
    readme = plugin_root / "README.md"
    if not readme.is_file():
        return
    content = readme.read_text(encoding="utf-8")
    old_badge = f"version-{old_version}-blue"
    new_badge = f"version-{new_version}-blue"

    if old_badge in content:
        if dry_run:
            print(f"  Would update README badge (exact): {old_badge} → {new_badge}")
            return
        readme.write_text(content.replace(old_badge, new_badge, 1), encoding="utf-8")
        print(f"  {GREEN}✓ Updated README version badge{NC}")
        return

    # Regex fallback: catch any drifted version badge
    badge_re = re.compile(r"version-\d+\.\d+\.\d+-blue")
    match = badge_re.search(content)
    if match is None:
        print(
            f"  {YELLOW}WARNING: no version-X.Y.Z-blue badge found in README.md — "
            f"add a shields.io badge so future releases can update it automatically{NC}"
        )
        return
    found = match.group(0)
    if dry_run:
        print(f"  Would update README badge (regex): {found} → {new_badge}")
        return
    readme.write_text(badge_re.sub(new_badge, content, count=1), encoding="utf-8")
    print(f"  {GREEN}✓ Updated README version badge (was {found}, now {new_badge}){NC}")


def stage_changelog(plugin_root: Path, tag_name: str, new_version: str) -> tuple[int, Path | None]:
    """Gate 9: generate CHANGELOG.md and extract release notes via git-cliff."""
    print(f"\n{BLUE}═══ Gate 9: Generate CHANGELOG + release notes (git-cliff) ═══{NC}")
    cliff_bin = shutil.which("git-cliff")
    if cliff_bin is None:
        print(
            f"{RED}✗ git-cliff not installed. Required for changelog and release notes.{NC}\n"
            f"{RED}  Install: brew install git-cliff  OR  cargo install git-cliff{NC}",
            file=sys.stderr,
        )
        return 1, None
    cliff_toml = plugin_root / "cliff.toml"
    if not cliff_toml.is_file():
        print(f"{RED}✗ cliff.toml not found. Required for changelog generation.{NC}", file=sys.stderr)
        return 1, None
    # Use the pattern recommended by the git-cliff docs for release pipelines:
    #   git cliff --bump --unreleased --tag <NEXT> -o CHANGELOG.md
    # --bump          tells git-cliff to treat this as a release bump (so the
    #                 unreleased section is promoted to a dated tag entry)
    # --unreleased    process only commits since the last tag
    # --tag <NEXT>    label the new entry with our computed version
    # -o CHANGELOG.md write the full regenerated changelog back to disk
    run(
        [cliff_bin, "--bump", "--unreleased", "--tag", tag_name, "-o", "CHANGELOG.md"],
        cwd=plugin_root,
    )
    print(f"{GREEN}✓ CHANGELOG.md updated with {tag_name}{NC}")
    release_notes_file = plugin_root / "reports" / f"release-notes-{new_version}.md"
    release_notes_file.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            cliff_bin,
            "--unreleased",
            "--tag",
            tag_name,
            "--strip",
            "all",
            "-o",
            str(release_notes_file),
        ],
        cwd=plugin_root,
    )
    print(f"{GREEN}✓ Release notes extracted to {release_notes_file.relative_to(plugin_root)}{NC}")
    return 0, release_notes_file


def stage_commit_tag_push(plugin_root: Path, tag_name: str) -> int:
    """Gates 10-12: commit, tag, push."""
    print(f"\n{BLUE}═══ Gate 10: Commit version bump + changelog ═══{NC}")
    run(["git", "add", "-A"], cwd=plugin_root)
    run(["git", "commit", "-m", f"chore(release): {tag_name}"], cwd=plugin_root)
    print(f"{GREEN}✓ Committed {tag_name}{NC}")
    print(f"\n{BLUE}═══ Gate 11: Create git tag {tag_name} ═══{NC}")
    run(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"], cwd=plugin_root)
    print(f"{GREEN}✓ Tag {tag_name} created{NC}")
    print(f"\n{BLUE}═══ Gate 12: Push to origin (branch + tags) ═══{NC}")
    run(["git", "push", "origin", "HEAD"], cwd=plugin_root)
    run(["git", "push", "origin", tag_name], cwd=plugin_root)
    print(f"{GREEN}✓ Pushed branch and tag {tag_name}{NC}")
    return 0


def stage_github_release(plugin_root: Path, tag_name: str, release_notes_file: Path) -> int:
    """Gate 13: create GitHub release with notes. Warns (not errors) if gh missing."""
    print(f"\n{BLUE}═══ Gate 13: Create GitHub release ═══{NC}")
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        print(
            f"{YELLOW}⚠ gh CLI not installed. Tag pushed but GitHub release not created.{NC}\n"
            f"{YELLOW}  Install: brew install gh{NC}",
            file=sys.stderr,
        )
        return 0
    gh_result = run(
        [
            gh_bin,
            "release",
            "create",
            tag_name,
            "--title",
            f"Release {tag_name}",
            "--notes-file",
            str(release_notes_file),
        ],
        cwd=plugin_root,
        check=False,
    )
    if gh_result.returncode == 0:
        print(f"{GREEN}✓ GitHub release {tag_name} published{NC}")
    else:
        print(
            f"{YELLOW}⚠ gh release failed (tag is already pushed — you can create release manually){NC}",
            file=sys.stderr,
        )
    return 0


# ── Main pipeline orchestrator ────────────────────────────────────────────────


def main() -> int:
    gate_summary = "\n".join(f"  {name}: {desc}" for name, desc in GATES)
    parser = argparse.ArgumentParser(
        description="Publish pipeline: 14-gate fail-fast release with auto-bump (bypass-proof)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Gates (all mandatory, run in order):
{gate_summary}

HARD RULE: No checks can be skipped. Every gate must pass with ZERO
CRITICAL/MAJOR/MINOR/NIT findings before the version is bumped, committed,
tagged, pushed, or released. There is no --skip-tests, no --skip-lint, no
--skip-validate, no --force. WARNING is the only allowed severity and does
not block. If a gate fails, fix the underlying problem — don't bypass.

CORNERSTONE: every push is a version bump. Running publish.py with no flag
auto-detects the bump type from conventional commits (feat → minor, fix →
patch, BREAKING CHANGE → major) via `git-cliff --bumped-version`. Explicit
--major/--minor/--patch flags remain available as manual overrides.

Examples:
  %(prog)s                      # auto-detect bump from git-cliff and publish
  %(prog)s --patch              # force patch bump
  %(prog)s --minor              # force minor bump
  %(prog)s --major              # force major bump
  %(prog)s --dry-run            # preview only, stops before bump commit
  %(prog)s --print-gates        # print gate list and exit
        """,
    )
    bump_group = parser.add_mutually_exclusive_group()
    bump_group.add_argument("--major", action="store_true",
                            help="Force a major bump (override auto-detection)")
    bump_group.add_argument("--minor", action="store_true",
                            help="Force a minor bump (override auto-detection)")
    bump_group.add_argument("--patch", action="store_true",
                            help="Force a patch bump (override auto-detection)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--print-gates", action="store_true", help="Print gate list and exit")
    args = parser.parse_args()

    if args.print_gates:
        print_gates()
        return 0

    # ── Gate 0: bypass guard ──
    rc = stage_bypass_guard()
    if rc != 0:
        return rc

    root = get_plugin_root()

    # Auto-detect bump type from conventional commits (via git-cliff) unless
    # the user explicitly forced one. The cornerstone rule is "every push is
    # a bump" — running publish.py bare is the normal case, and git-cliff
    # reads the commit log to decide whether this release is a major, minor,
    # or patch bump. Explicit flags remain available for override.
    if args.major:
        bump_type = "major"
        print(f"{BLUE}Bump type: major (forced via --major){NC}")
    elif args.minor:
        bump_type = "minor"
        print(f"{BLUE}Bump type: minor (forced via --minor){NC}")
    elif args.patch:
        bump_type = "patch"
        print(f"{BLUE}Bump type: patch (forced via --patch){NC}")
    else:
        bump_type = detect_bump_type(root)
        print(f"{BLUE}Bump type: {bump_type} (auto-detected from git-cliff){NC}")

    # ── Gates 1-7: preflight ──
    # Order: clean tree → lint(+typecheck) → tests → validate → marketplace →
    # consistency. Lint comes BEFORE tests because type errors and syntax
    # issues should fail fast (cheap), before paying the cost of running the
    # test suite. Validate runs AFTER tests so the test suite catches any
    # behavioral regression before the validator checks structural rules.
    for stage in (
        lambda: stage_check_working_tree(root),
        lambda: stage_run_lint(root),
        lambda: stage_run_tests(root),
        lambda: stage_validate_plugin(root),
    ):
        rc = stage()
        if rc != 0:
            return rc

    layout, _ = detect_layout(root)
    rc = stage_validate_marketplace(root, layout)
    if rc != 0:
        return rc

    rc = stage_marketplace_registration_check(root)
    if rc != 0:
        return rc

    rc = stage_version_consistency(root)
    if rc != 0:
        return rc

    # ── Gate 8: bump ──
    rc, new_version = stage_bump(root, bump_type, args.dry_run)
    if rc != 0 or new_version is None:
        # Narrowing for mypy: stage_bump returns (0, str) or (nonzero, None).
        # The second branch catches the defensive case where rc is 0 but
        # new_version is None — should never happen, but fail-fast if it does.
        return rc if rc != 0 else 1
    if args.dry_run:
        print(f"\n{GREEN}✓ Dry run complete — no changes made.{NC}")
        return 0

    tag_name = f"v{new_version}"

    # ── Gates 9-13: changelog, commit, tag, push, release ──
    rc, release_notes_file = stage_changelog(root, tag_name, new_version)
    if rc != 0 or release_notes_file is None:
        return rc

    rc = stage_commit_tag_push(root, tag_name)
    if rc != 0:
        return rc

    rc = stage_github_release(root, tag_name, release_notes_file)
    if rc != 0:
        return rc

    print(f"\n{GREEN}✓ Published v{new_version}{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
