# Script Templates

## Table of Contents
- [Placeholder Reference](#placeholder-reference)
- [sync_marketplace_versions.py](#sync_marketplace_versionspy)
- [pre-commit-hook.py](#pre-commit-hookpy)
- [pre-push-hook.py](#pre-push-hookpy)
- [setup-hooks.py](#setup-hookspy)
- [push-plugins.sh](#push-pluginssh)
- [render_readme_table.py](#render_readme_tablepy)

Ready-to-use scripts for managing a Claude Code plugin marketplace repository.
Each script is based on production code from a production marketplace. Replace
all `<placeholder-for-...>` values with your actual configuration before use.

## Checklist

- [ ] Pick the scripts you need (sync/pre-commit/pre-push/setup-hooks/push/render-readme-table)
- [ ] Copy each into the marketplace repo's `scripts/` directory
- [ ] Replace every `<placeholder-for-*>` — grep to confirm zero remain
- [ ] `chmod +x` each script
- [ ] Run `setup-hooks.py` to wire them

## Placeholder Reference

| Placeholder | Description | Where Used |
|-------------|-------------|------------|
| `<placeholder-for-marketplace-owner>` | GitHub username or organization | push-plugins.sh |
| `<placeholder-for-marketplace-repo-name>` | GitHub repository name | push-plugins.sh |
| `<placeholder-for-marketplace-dir>` | Local path to marketplace repo root | push-plugins.sh |
| `<placeholder-for-cpv-subdir>` | Relative path from repo root to CPV install | pre-commit-hook.py |
| `<placeholder-for-submodule-names>` | Comma-separated list of submodule names | setup-hooks.py |
| `<placeholder-for-validation-plugin-name>` | CPV plugin name as it appears in the plugin cache (e.g. `claude-plugins-validation`) | pre-push-hook.py, push-plugins.sh |

---

## sync_marketplace_versions.py

Syncs plugin versions in marketplace.json by reading each plugin's plugin.json,
supporting both URL-based sources (dict with `"source": "github"`) and
path-based sources (string starting with `./`).

**Install location:** `scripts/sync_marketplace_versions.py`

```python
#!/usr/bin/env python3
"""
sync_marketplace_versions.py - Sync plugin versions from plugin sources to marketplace.json

This script reads version information from each plugin's plugin.json and updates
the corresponding entry in marketplace.json. It supports both URL-based sources
(dict with "source": "github") and path-based sources (string starting with "./").

Usage:
    python sync_marketplace_versions.py [--marketplace PATH] [--dry-run]

Exit codes:
    0 - Success (updated or already in sync)
    1 - Error (missing files, invalid JSON, etc.)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def find_marketplace_json(start_path: Path) -> Path | None:
    """Find marketplace.json in common locations."""
    candidates = [
        start_path / ".claude-plugin" / "marketplace.json",
        start_path / "marketplace.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_json(path: Path) -> dict[str, Any] | None:
    """Load and parse a JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error loading {path}: {e}", file=sys.stderr)
        return None


def save_json(path: Path, data: dict[str, Any]) -> bool:
    """Save data to a JSON file with pretty formatting."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return True
    except Exception as e:
        print(f"Error saving {path}: {e}", file=sys.stderr)
        return False


def get_plugin_version(plugin_dir: Path) -> str | None:
    """Get version from a plugin's plugin.json."""
    plugin_json_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not plugin_json_path.exists():
        return None

    data = load_json(plugin_json_path)
    if data is None:
        return None

    return data.get("version")


def sync_versions(
    marketplace_path: Path, dry_run: bool = False, verbose: bool = True
) -> tuple[bool, list[str]]:
    """
    Sync plugin versions from plugin sources (URL-based or path-based) to marketplace.json.

    Args:
        marketplace_path: Path to marketplace.json
        dry_run: If True, don't write changes
        verbose: If True, print progress

    Returns:
        Tuple of (success, list of updated plugin names)
    """
    marketplace_dir = marketplace_path.parent
    if marketplace_path.parent.name == ".claude-plugin":
        marketplace_dir = marketplace_path.parent.parent

    # Load marketplace.json
    marketplace_data = load_json(marketplace_path)
    if marketplace_data is None:
        return False, []

    plugins = marketplace_data.get("plugins", [])
    if not plugins:
        if verbose:
            print("No plugins found in marketplace.json")
        return True, []

    updated_plugins: list[str] = []
    changes_made = False

    for plugin in plugins:
        plugin_name = plugin.get("name", "")
        if not plugin_name:
            continue

        # Determine plugin directory from source
        source = plugin.get("source", f"./{plugin_name}")
        if isinstance(source, str) and source.startswith("./"):
            # Path-based source (legacy): plugin dir is relative to marketplace
            plugin_dir = marketplace_dir / source[2:]
        elif isinstance(source, dict) and source.get("source") in ("github", "url"):
            # URL-based source: look in OUTPUT_SKILLS/ for local dev copy
            plugin_dir = marketplace_dir / "OUTPUT_SKILLS" / plugin_name
            if not plugin_dir.exists():
                # Also try parent's OUTPUT_SKILLS (if marketplace_dir is a subdirectory)
                plugin_dir = marketplace_dir.parent / "OUTPUT_SKILLS" / plugin_name
        else:
            plugin_dir = marketplace_dir / plugin_name

        if not plugin_dir.exists():
            if verbose:
                print(f"  [SKIP] {plugin_name}: directory not found at {plugin_dir}")
            continue

        # Get version from plugin.json
        actual_version = get_plugin_version(plugin_dir)
        if actual_version is None:
            if verbose:
                print(f"  [SKIP] {plugin_name}: could not read version")
            continue

        marketplace_version = plugin.get("version", "")

        if actual_version != marketplace_version:
            if verbose:
                print(f"  [UPDATE] {plugin_name}: {marketplace_version} -> {actual_version}")
            plugin["version"] = actual_version
            updated_plugins.append(plugin_name)
            changes_made = True
        else:
            if verbose:
                print(f"  [OK] {plugin_name}: {actual_version}")

    # Save changes
    if changes_made and not dry_run:
        if not save_json(marketplace_path, marketplace_data):
            return False, updated_plugins

    return True, updated_plugins


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync plugin versions from plugin sources to marketplace.json"
    )
    parser.add_argument(
        "--marketplace",
        type=Path,
        default=None,
        help="Path to marketplace.json (auto-detected if not specified)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making changes",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress output except errors",
    )

    args = parser.parse_args()

    # Find marketplace.json
    if args.marketplace:
        marketplace_path = args.marketplace
    else:
        marketplace_path = find_marketplace_json(Path.cwd())

    if marketplace_path is None:
        print("Error: Could not find marketplace.json", file=sys.stderr)
        print("Use --marketplace to specify the path", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"Syncing versions in {marketplace_path}")
        if args.dry_run:
            print("(dry run - no changes will be made)")

    success, updated = sync_versions(
        marketplace_path, dry_run=args.dry_run, verbose=not args.quiet
    )

    if not success:
        return 1

    if not args.quiet:
        if updated:
            print(f"\nUpdated {len(updated)} plugin(s): {', '.join(updated)}")
        else:
            print("\nAll versions are in sync")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## pre-commit-hook.py

Pre-commit validation for marketplace and plugin repos. Validates
marketplace.json, plugin.json files (JSON syntax, required fields, semver),
hooks.json files, Python linting, version consistency, and sensitive data
patterns. Automatically skips during rebase/cherry-pick/merge/am operations.

**Install location:** `scripts/pre-commit-hook.py`
**Git hook target:** `.git/hooks/pre-commit`

```python
#!/usr/bin/env python3
"""pre-commit-hook.py - Pre-commit validation for marketplace and plugins.

This script validates:
1. marketplace.json if changed
2. plugin.json files (JSON syntax, required fields, semver)
3. hooks.json files
4. Python files (linting)
5. Version consistency
6. Sensitive data patterns

IMPORTANT: This hook is SKIPPED during:
- git rebase (interactive or not)
- git cherry-pick
- git merge (during conflict resolution)
- git am (applying patches)

This prevents validation errors during history rewriting operations.

To install as git hook:
    cp scripts/pre-commit-hook.py .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ANSI Colors
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"


def is_rebase_in_progress(git_dir: Path) -> bool:
    """Check if we're in the middle of a rebase or other history-rewriting operation.

    During rebase/cherry-pick/merge, commits are being replayed and we should
    skip validation to avoid conflicts and slowdowns.
    """
    # Check for rebase indicators
    rebase_indicators = [
        git_dir / "rebase-merge",      # git rebase (interactive)
        git_dir / "rebase-apply",       # git rebase (non-interactive) / git am
        git_dir / "CHERRY_PICK_HEAD",   # git cherry-pick
        git_dir / "MERGE_HEAD",         # git merge in progress
        git_dir / "BISECT_LOG",         # git bisect
    ]

    for indicator in rebase_indicators:
        if indicator.exists():
            return True

    # Also check environment variable (some git operations set this)
    if os.environ.get("GIT_AUTHOR_DATE"):
        # During rebase, git sets GIT_AUTHOR_DATE to preserve original timestamps
        # This is a secondary indicator
        pass  # Not conclusive on its own

    return False


def get_git_dir() -> Path:
    """Get the .git directory path (handles both regular repos and submodules)."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        timeout=10
    )
    if result.returncode == 0:
        return Path(result.stdout.strip()).resolve()
    # Fallback
    return Path(".git")


def run_command(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)


def get_staged_files() -> list[str]:
    """Get list of staged files."""
    code, stdout, _ = run_command(["git", "diff", "--cached", "--name-only"])
    if code == 0:
        return [f for f in stdout.strip().split("\n") if f]
    return []


def get_staged_diff() -> str:
    """Get staged diff content."""
    code, stdout, _ = run_command(["git", "diff", "--cached", "-U0"])
    return stdout if code == 0 else ""


def validate_semver(version: str) -> bool:
    """Validate semver format."""
    pattern = r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$"
    return bool(re.match(pattern, version))


def validate_marketplace_json(repo_root: Path) -> tuple[bool, str]:
    """Validate marketplace.json."""
    # <placeholder-for-cpv-subdir> — relative path from repo root to CPV installation
    validator = repo_root / "<placeholder-for-cpv-subdir>" / "scripts" / "validate_marketplace.py"
    if not validator.exists():
        return True, "validator not found"

    code, stdout, stderr = run_command(
        ["uv", "run", "python", str(validator), str(repo_root)],
        cwd=repo_root / "<placeholder-for-cpv-subdir>"
    )
    if code == 0:
        return True, ""
    return False, "Run: uv run --with pyyaml python ${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py marketplace . --verbose"


def validate_plugin_json(file_path: Path) -> tuple[bool, str]:
    """Validate a plugin.json file."""
    try:
        with open(file_path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, f"invalid JSON: {e}"

    name = data.get("name")
    version = data.get("version")

    if not name:
        return False, "missing 'name' field"
    if not version:
        return False, "missing 'version' field"
    if not validate_semver(version):
        return False, f"invalid version format: {version}"

    return True, ""


def validate_hooks_json(file_path: Path, repo_root: Path) -> tuple[bool, str]:
    """Validate a hooks.json file."""
    # <placeholder-for-cpv-subdir> — relative path from repo root to CPV installation
    validator = repo_root / "<placeholder-for-cpv-subdir>" / "scripts" / "validate_hook.py"

    if validator.exists():
        code, _, _ = run_command(
            ["uv", "run", "python", str(validator), str(file_path), "--quiet"],
            cwd=repo_root / "<placeholder-for-cpv-subdir>"
        )
        if code == 0:
            return True, ""
        return True, "has issues (non-blocking)"  # Non-blocking, still return True

    # Fallback: just check JSON validity
    try:
        with open(file_path) as f:
            json.load(f)
        return True, "JSON valid"
    except json.JSONDecodeError:
        return False, "invalid JSON"


def lint_python_files(files: list[str], repo_root: Path) -> tuple[bool, str]:
    """Lint Python files with ruff."""
    existing_files = [f for f in files if (repo_root / f).exists()]
    if not existing_files:
        return True, "no files to lint"

    code, _, _ = run_command(
        ["uv", "run", "ruff", "check"] + existing_files,
        cwd=repo_root
    )
    if code == 0:
        return True, ""
    return False, "linting issues (non-blocking)"


def check_version_consistency(repo_root: Path) -> tuple[bool, bool]:
    """Check version consistency. Returns (passed, was_fixed)."""
    sync_script = repo_root / "scripts" / "sync-versions.py"
    if not sync_script.exists():
        return True, False

    code, _, _ = run_command(
        ["python3", str(sync_script), "--check", str(repo_root)],
        cwd=repo_root
    )
    if code == 0:
        return True, False

    # Auto-fix by syncing
    run_command(["python3", str(sync_script), str(repo_root)], cwd=repo_root)

    # Stage the updated marketplace.json
    marketplace_json = repo_root / ".claude-plugin" / "marketplace.json"
    if marketplace_json.exists():
        run_command(["git", "add", str(marketplace_json)], cwd=repo_root)
        return True, True

    return True, True


def check_sensitive_data(diff: str) -> bool:
    """Check for sensitive data patterns in diff."""
    patterns = [
        r"password\s*[:=]",
        r"api[_-]?key\s*[:=]",
        r"secret\s*[:=]",
        r"token\s*[:=].*['\"][a-zA-Z0-9]{20,}['\"]",
        r"private[_-]?key",
    ]

    for line in diff.split("\n"):
        # Skip removed lines
        if line.startswith("-"):
            continue
        # Skip obvious placeholders
        if any(x in line.lower() for x in ["example", "placeholder", "your_", "<", "todo"]):
            continue

        for pattern in patterns:
            if re.search(pattern, line, re.IGNORECASE):
                return True
    return False


def main() -> int:
    """Main pre-commit hook function."""
    # Get git directory and check for rebase
    git_dir = get_git_dir()

    if is_rebase_in_progress(git_dir):
        print(f"{BLUE}[pre-commit] Skipping validation during rebase/cherry-pick/merge{NC}")
        return 0  # Allow commit to proceed without validation

    repo_root = Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True
    ).stdout.strip())

    print("Running pre-commit validations...")

    validation_failed = False
    staged_files = get_staged_files()

    # 1. Validate marketplace.json if changed
    if any("marketplace.json" in f for f in staged_files):
        print("Validating marketplace.json... ", end="", flush=True)
        passed, msg = validate_marketplace_json(repo_root)
        if passed:
            print(f"{GREEN}OK{NC}")
        else:
            print(f"{RED}FAIL{NC}")
            print(f"{RED}Marketplace validation failed. {msg}{NC}")
            validation_failed = True

    # 2. Validate changed plugin.json files
    plugin_jsons = [f for f in staged_files if f.endswith("plugin.json")]
    for plugin_json in plugin_jsons:
        file_path = repo_root / plugin_json
        if file_path.exists():
            print(f"Validating {plugin_json}... ", end="", flush=True)
            passed, msg = validate_plugin_json(file_path)
            if passed:
                print(f"{GREEN}OK{NC}")
            else:
                print(f"{RED}FAIL {msg}{NC}")
                validation_failed = True

    # 3. Validate changed hooks.json files
    hooks_jsons = [f for f in staged_files if f.endswith("hooks.json")]
    for hooks_json in hooks_jsons:
        file_path = repo_root / hooks_json
        if file_path.exists():
            print(f"Validating {hooks_json}... ", end="", flush=True)
            passed, msg = validate_hooks_json(file_path, repo_root)
            if passed:
                print(f"{GREEN}OK{NC}" + (f" ({msg})" if msg else ""))
            else:
                print(f"{YELLOW}WARN {msg}{NC}")

    # 4. Lint changed Python files
    py_files = [f for f in staged_files if f.endswith(".py")]
    if py_files:
        print("Linting Python files... ", end="", flush=True)
        passed, msg = lint_python_files(py_files, repo_root)
        if passed:
            print(f"{GREEN}OK{NC}" + (f" ({msg})" if msg else ""))
        else:
            print(f"{YELLOW}WARN {msg}{NC}")

    # 5. Check version consistency
    print("Checking version consistency... ", end="", flush=True)
    passed, was_fixed = check_version_consistency(repo_root)
    if passed and not was_fixed:
        print(f"{GREEN}OK{NC}")
    elif was_fixed:
        print(f"{YELLOW}WARN versions out of sync, syncing...{NC}")
        print("  Staged updated marketplace.json")
    else:
        print(f"{YELLOW}WARN sync script not found{NC}")

    # 6. Check for sensitive data
    print("Checking for sensitive data... ", end="", flush=True)
    diff = get_staged_diff()
    if check_sensitive_data(diff):
        print(f"{YELLOW}WARN potential sensitive data detected - please review{NC}")
    else:
        print(f"{GREEN}OK{NC}")

    # Final result
    if validation_failed:
        print()
        print(f"{RED}Pre-commit validation failed. Please fix the issues above.{NC}")
        print("To bypass (not recommended): git commit --no-verify")
        return 1

    print(f"{GREEN}Pre-commit validations passed{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## pre-push-hook.py

Validates the local repo before allowing `git push`. Finds CPV from the
installed plugin cache and runs `validate_plugin.py --strict` or
`validate_marketplace.py --strict` depending on repo type. Blocks push on
any validation failures.

**Install location:** `scripts/pre-push-hook.py`
**Git hook target:** `.git/hooks/pre-push`

```python
#!/usr/bin/env python3
"""pre-push-hook.py - Validate the current repo before allowing git push.

Installed as .git/hooks/pre-push in any plugin or marketplace repo.
Uses CPV (claude-plugins-validation) from the plugin cache with --strict.
Validates the LOCAL repo (the one being pushed), blocking if any issues found.

To install:
    python3 scripts/setup-hooks.py
    # OR manually:
    cp scripts/pre-push-hook.py .git/hooks/pre-push
    chmod +x .git/hooks/pre-push

Exit codes:
    0 - All validations passed, push allowed
    1 - Validation failed, push blocked
"""

import os
import subprocess
import sys
from pathlib import Path

# ANSI Colors
_USE_COLOR = (
    not os.environ.get("NO_COLOR")
    and os.name != "nt"
    and hasattr(sys.stdout, "isatty")
    and sys.stdout.isatty()
)
RED = "\033[0;31m" if _USE_COLOR else ""
GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[1;33m" if _USE_COLOR else ""
BLUE = "\033[0;34m" if _USE_COLOR else ""
BOLD = "\033[1m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""


def find_cpv_dir() -> Path | None:
    """Find the CPV plugin directory from the installed plugin cache."""
    # <placeholder-for-marketplace-owner> — your GitHub org or username
    cache_base = Path.home() / ".claude" / "plugins" / "cache" / "<placeholder-for-marketplace-owner>" / "<placeholder-for-validation-plugin-name>"
    if not cache_base.is_dir():
        return None

    def version_key(d: Path) -> tuple[int, ...]:
        # Sort version dir names numerically, not lexically. A plain string
        # sort orders "2.9.0" AFTER "2.115.0" (because '9' > '1'), which would
        # pick an OLDER CPV as "latest". Parsing each dot-separated segment to
        # an int reproduces `sort -V` semantics without a packaging dependency;
        # non-numeric segments fall back to 0 so a malformed dir can't crash.
        parts: list[int] = []
        for seg in d.name.split("."):
            parts.append(int(seg) if seg.isdigit() else 0)
        return tuple(parts)

    versions = sorted(
        [d for d in cache_base.iterdir() if d.is_dir()],
        key=version_key,
    )
    if not versions:
        return None
    latest = versions[-1]
    if (latest / "scripts" / "validate_plugin.py").is_file():
        return latest
    return None


def get_repo_root() -> Path:
    """Return the git repository root."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip())


def is_marketplace(repo_root: Path) -> bool:
    """Check if this repo is a marketplace (has marketplace.json)."""
    return (repo_root / ".claude-plugin" / "marketplace.json").is_file()


def is_plugin(repo_root: Path) -> bool:
    """Check if this repo is a plugin (has plugin.json)."""
    return (repo_root / ".claude-plugin" / "plugin.json").is_file()


def run_validator(
    cpv_dir: Path,
    script_name: str,
    target: Path,
    timeout: int = 120,
) -> tuple[int, str]:
    """Run a CPV validation script with --strict. Returns (exit_code, output)."""
    script = cpv_dir / "scripts" / script_name
    if not script.is_file():
        return -1, f"Validator not found: {script}"

    cmd = ["uv", "run", "python", str(script), str(target), "--strict"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            cwd=str(cpv_dir),
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, f"TIMEOUT: {script_name} exceeded {timeout}s"
    except FileNotFoundError:
        return -1, "uv not found -- install uv first"


def main() -> int:
    repo_root = get_repo_root()

    print(f"{BOLD}{'=' * 60}{NC}")
    print(f"{BOLD}Pre-Push Validation (--strict){NC}")
    print(f"{BOLD}{'=' * 60}{NC}")
    print()

    # Find CPV
    cpv_dir = find_cpv_dir()
    if cpv_dir is None:
        print(f"{RED}ERROR: CPV plugin not found in cache.{NC}")
        print("Install it: claude plugin install claude-plugins-validation@<marketplace-name>")
        return 1

    print(f"{BLUE}Repo:{NC}     {repo_root}")
    print(f"{BLUE}CPV:{NC}      {cpv_dir}")
    print()

    # Detect what kind of repo this is and validate accordingly
    if is_marketplace(repo_root):
        print(f"{BLUE}Detected: marketplace repo{NC}")
        print(f"{BLUE}Validating marketplace.json with --strict...{NC}")
        code, output = run_validator(cpv_dir, "validate_marketplace.py", repo_root)
    elif is_plugin(repo_root):
        print(f"{BLUE}Detected: plugin repo{NC}")
        print(f"{BLUE}Validating plugin with --strict...{NC}")
        code, output = run_validator(cpv_dir, "validate_plugin.py", repo_root)
    else:
        print(f"{YELLOW}Not a plugin or marketplace repo. Skipping validation.{NC}")
        return 0

    # Show relevant output lines
    for line in output.splitlines():
        if any(sev in line for sev in ("CRITICAL", "MAJOR", "MINOR", "NIT", "PASSED", "Plugin Validation", "Marketplace Validation")):
            print(f"  {line.strip()}")

    # Verdict
    print()
    print(f"{BOLD}{'=' * 60}{NC}")
    if code == 0:
        print(f"{GREEN}  PASSED -- push allowed{NC}")
        print(f"{BOLD}{'=' * 60}{NC}")
        return 0
    else:
        print(f"{RED}  BLOCKED -- validation issues found (exit {code}){NC}")
        print(f"{RED}  Fix ALL issues before pushing.{NC}")
        print(f"{BOLD}{'=' * 60}{NC}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

---

## setup-hooks.py

Installs git hooks for the marketplace repo and all submodules. Uses a
rebase-safe architecture: pre-commit validates and lints (skips during
rebase), pre-push runs full CPV validation, post-rewrite regenerates
changelog after rebase/amend, post-merge regenerates changelog after merge.
Removes legacy post-commit hooks that cause rebase conflicts.

**Install location:** `scripts/setup-hooks.py`

```python
#!/usr/bin/env python3
"""setup-hooks.py - Install git hooks for marketplace and all submodules.

Hook Architecture (v2 - rebase-safe):
- pre-commit: Lint, validate, version sync (skips during rebase)
- pre-push: Full validation, blocks broken plugins
- post-rewrite: Regenerate changelog after rebase/amend (fires once)
- post-merge: Regenerate changelog after merge

Usage:
    python scripts/setup-hooks.py
"""

import os
import shutil
import stat
import sys
from pathlib import Path

# ANSI Colors
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
BLUE = "\033[0;34m"
NC = "\033[0m"


def check_git_cliff() -> bool:
    """Check if git-cliff is installed."""
    return shutil.which("git-cliff") is not None


def make_executable(path: Path) -> None:
    """Make a file executable."""
    current = os.stat(path)
    os.chmod(path, current.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def create_pre_commit_hook(hooks_dir: Path, repo_root: Path) -> None:
    """Create pre-commit hook for main repo with rebase detection."""
    source = repo_root / "scripts" / "pre-commit-hook.py"
    target = hooks_dir / "pre-commit"

    if source.exists():
        shutil.copy2(source, target)
        make_executable(target)
        print(f"{GREEN}OK{NC} Created pre-commit hook")
    else:
        print(f"{YELLOW}WARN{NC} pre-commit-hook.py not found, skipping")


def create_pre_push_hook(hooks_dir: Path, repo_root: Path) -> None:
    """Create pre-push hook for main repo."""
    source = repo_root / "scripts" / "pre-push-hook.py"
    target = hooks_dir / "pre-push"

    if source.exists():
        shutil.copy2(source, target)
        make_executable(target)
        print(f"{GREEN}OK{NC} Created pre-push hook")
    else:
        print(f"{YELLOW}WARN{NC} pre-push-hook.py not found, skipping")


def create_post_rewrite_hook(hooks_dir: Path, repo_name: str = "main repo") -> None:
    """Create post-rewrite hook for changelog generation after rebase/amend.

    post-rewrite fires ONCE after:
    - git rebase completes (all commits replayed)
    - git commit --amend completes

    This avoids the mid-rebase CHANGELOG conflicts.
    """
    hook_content = f'''#!/usr/bin/env python3
"""post-rewrite hook: Update CHANGELOG.md after rebase/amend completes.

This hook fires ONCE after rebase or amend operations complete,
avoiding the mid-rebase conflicts that post-commit causes.

Arguments passed by git:
- $1: "rebase" or "amend"
- stdin: list of rewritten commits (old-sha new-sha)
"""

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    operation = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    if not shutil.which("git-cliff"):
        return 0  # Silent skip if git-cliff not installed

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    repo_root = Path(result.stdout.strip())

    cliff_toml = repo_root / "cliff.toml"
    if not cliff_toml.exists():
        return 0  # Silent skip if no cliff.toml

    print(f"[post-rewrite] Regenerating CHANGELOG.md after {{operation}}...")

    result = subprocess.run(
        ["git-cliff", "-o", "CHANGELOG.md"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        print(f"Warning: git-cliff failed: {{result.stderr}}")
        return 0

    # Check if changelog changed
    status = subprocess.run(
        ["git", "diff", "--quiet", "CHANGELOG.md"],
        cwd=repo_root,
        capture_output=True,
        timeout=30,
    )

    if status.returncode != 0:
        print("CHANGELOG.md updated - remember to commit it!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

    target = hooks_dir / "post-rewrite"
    target.write_text(hook_content)
    make_executable(target)
    print(f"{GREEN}OK{NC} Created post-rewrite hook ({repo_name})")


def create_post_merge_hook(hooks_dir: Path, repo_name: str = "main repo") -> None:
    """Create post-merge hook for changelog generation after merge."""
    hook_content = f'''#!/usr/bin/env python3
"""post-merge hook: Update CHANGELOG.md after merge completes."""

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if not shutil.which("git-cliff"):
        return 0

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    repo_root = Path(result.stdout.strip())

    cliff_toml = repo_root / "cliff.toml"
    if not cliff_toml.exists():
        return 0

    print("[post-merge] Regenerating CHANGELOG.md...")

    result = subprocess.run(
        ["git-cliff", "-o", "CHANGELOG.md"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        print(f"Warning: git-cliff failed: {{result.stderr}}")
        return 0

    status = subprocess.run(
        ["git", "diff", "--quiet", "CHANGELOG.md"],
        cwd=repo_root,
        capture_output=True,
        timeout=30,
    )

    if status.returncode != 0:
        print("CHANGELOG.md updated - remember to commit it!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

    target = hooks_dir / "post-merge"
    target.write_text(hook_content)
    make_executable(target)
    print(f"{GREEN}OK{NC} Created post-merge hook ({repo_name})")


def remove_old_post_commit_hook(hooks_dir: Path, repo_name: str = "main repo") -> None:
    """Remove the old post-commit hook that caused rebase conflicts."""
    post_commit = hooks_dir / "post-commit"
    if post_commit.exists():
        post_commit.unlink()
        print(f"{YELLOW}REMOVED{NC} old post-commit hook ({repo_name})")


def setup_submodule_hooks(submodule_name: str, repo_root: Path) -> bool:
    """Set up hooks for a submodule."""
    hooks_dir = repo_root / ".git" / "modules" / submodule_name / "hooks"

    if not hooks_dir.exists():
        print(f"{RED}FAIL{NC} Submodule {submodule_name} not found or not initialized")
        return False

    # Remove old problematic post-commit hook
    remove_old_post_commit_hook(hooks_dir, submodule_name)

    # Install new hooks
    create_post_rewrite_hook(hooks_dir, submodule_name)
    create_post_merge_hook(hooks_dir, submodule_name)

    return True


def main() -> int:
    """Main setup function."""
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent

    print(f"{BLUE}{'=' * 60}{NC}")
    print(f"{BLUE}Git Hooks Setup v2 - Rebase-Safe Architecture{NC}")
    print(f"{BLUE}{'=' * 60}{NC}")
    print(f"Repository root: {repo_root}")
    print()

    # Check dependencies
    if not check_git_cliff():
        print(f"{YELLOW}Warning:{NC} git-cliff not installed. Install: brew install git-cliff")
    print()

    # Main repository hooks
    print(f"{BLUE}Main repository hooks{NC}")
    print("-" * 40)

    main_hooks_dir = repo_root / ".git" / "hooks"
    main_hooks_dir.mkdir(parents=True, exist_ok=True)

    # Remove old post-commit hook
    remove_old_post_commit_hook(main_hooks_dir, "main repo")

    # Install new hooks
    create_pre_commit_hook(main_hooks_dir, repo_root)
    create_pre_push_hook(main_hooks_dir, repo_root)
    create_post_rewrite_hook(main_hooks_dir, "main repo")
    create_post_merge_hook(main_hooks_dir, "main repo")

    # Submodule hooks
    # <placeholder-for-submodule-names> — comma-separated list of your plugin submodule names
    print()
    print(f"{BLUE}Submodule hooks{NC}")
    print("-" * 40)

    for submodule in "<placeholder-for-submodule-names>".split(","):
        submodule = submodule.strip()
        if submodule and not submodule.startswith("<placeholder"):
            setup_submodule_hooks(submodule, repo_root)

    # Summary
    print()
    print(f"{GREEN}{'=' * 60}{NC}")
    print(f"{GREEN}All git hooks installed successfully!{NC}")
    print(f"{GREEN}{'=' * 60}{NC}")
    print()
    print("Hook architecture (v2 - rebase-safe):")
    print()
    print("  Main repo:")
    print("    pre-commit    -> Lint, validate, version sync (skips during rebase)")
    print("    pre-push      -> Full validation, blocks broken plugins")
    print("    post-rewrite  -> Changelog after rebase/amend (fires ONCE)")
    print("    post-merge    -> Changelog after merge")
    print()
    print("  Submodules:")
    print("    post-rewrite  -> Changelog after rebase/amend (fires ONCE)")
    print("    post-merge    -> Changelog after merge")
    print()
    print(f"{YELLOW}NOTE:{NC} post-commit hooks removed to prevent rebase conflicts.")
    print(f"      Changelog is now generated only after rebase/amend/merge completes.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## push-plugins.sh

Validates and pushes local plugin repos, then pushes the marketplace.
For each plugin path provided: validates with CPV `validate_plugin.py --strict`,
pushes to its git origin, syncs marketplace.json versions, validates the
marketplace with `validate_marketplace.py --strict`, and pushes the
marketplace repo. Supports `--dry-run` and `--no-validate` flags.

**Install location:** `scripts/push-plugins.sh`

```bash
#!/usr/bin/env bash
# Validate and push local plugin repos, then push marketplace.
# Usage: ./scripts/push-plugins.sh [/path/to/plugin ...] [--dry-run] [--no-validate]
#
# Each path must be a local git clone of a plugin repo.
# The script:
#   1. Validates each local plugin with CPV validate_plugin.py --strict
#   2. Pushes each plugin to its own git origin
#   3. Syncs marketplace.json versions
#   4. Validates marketplace with CPV validate_marketplace.py --strict
#   5. Pushes the marketplace repo
#
# If no plugin paths given, only the marketplace is validated and pushed.
#
# Examples:
#   ./scripts/push-plugins.sh ~/Code/my-plugin                # One plugin + marketplace
#   ./scripts/push-plugins.sh ~/Code/plugin1 ~/Code/plugin2   # Multiple + marketplace
#   ./scripts/push-plugins.sh --dry-run ~/Code/my-plugin      # Validate only, no push
#   ./scripts/push-plugins.sh                                 # Marketplace only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MARKETPLACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Find the CPV validator from the plugin cache (latest installed version)
find_cpv_dir() {
    # <placeholder-for-marketplace-owner> — your GitHub org or username
    local cache_base="$HOME/.claude/plugins/cache/<placeholder-for-marketplace-owner>/<placeholder-for-validation-plugin-name>"
    if [ ! -d "$cache_base" ]; then
        echo ""
        return
    fi
    local latest
    latest=$(ls -1d "$cache_base"/*/ 2>/dev/null | sort -t/ -k"$(echo "$cache_base" | tr -cd '/' | wc -c | tr -d ' ')" -V | tail -1)
    if [ -n "$latest" ] && [ -f "${latest}scripts/validate_plugin.py" ]; then
        echo "${latest}"
    else
        echo ""
    fi
}

# -- Parse arguments --------------------------------------------------------

DRY_RUN=""
VALIDATE="yes"
declare -a PLUGIN_PATHS=()

for arg in "$@"; do
    if [ "$arg" = "--dry-run" ]; then
        DRY_RUN="yes"
    elif [ "$arg" = "--no-validate" ]; then
        VALIDATE=""
    else
        # Resolve to absolute path
        resolved="$(cd "$arg" 2>/dev/null && pwd)" || {
            echo "ERROR: '$arg' is not a valid directory"
            exit 1
        }
        PLUGIN_PATHS+=("$resolved")
    fi
done

# Track results
declare -a VALIDATED=()
declare -a PUSHED=()
declare -a VALIDATION_FAILED_LIST=()
declare -a PUSH_FAILED_LIST=()
declare -a SKIPPED=()

PLUGIN_COUNT=${#PLUGIN_PATHS[@]}
echo "============================================================"
if [ "$PLUGIN_COUNT" -gt 0 ]; then
    echo "  Validate + push $PLUGIN_COUNT plugin(s) + marketplace"
else
    echo "  Push marketplace only (no plugin paths given)"
fi
echo "============================================================"
for p in "${PLUGIN_PATHS[@]}"; do echo "  - $p"; done
echo ""

# -- Find CPV ---------------------------------------------------------------

CPV_DIR=$(find_cpv_dir)
if [ -z "$CPV_DIR" ]; then
    echo "ERROR: CPV plugin not found in cache. Install it first:"
    echo "  claude plugin install claude-plugins-validation@<marketplace-name>"
    exit 1
fi

VALIDATOR="$CPV_DIR/scripts/validate_plugin.py"
MARKETPLACE_VALIDATOR="$CPV_DIR/scripts/validate_marketplace.py"

echo "Using CPV from: $CPV_DIR"
echo ""

# -- Validate + push each plugin --------------------------------------------

VALIDATION_FAILED=0

if [ "$PLUGIN_COUNT" -gt 0 ]; then
    echo "--- plugin validation + push (--strict) ---"

    set +e

    for plugin_path in "${PLUGIN_PATHS[@]}"; do
        plugin_name=$(basename "$plugin_path")
        echo -n "  $plugin_name ($plugin_path)... "

        # Verify it's a git repo
        if [ ! -d "$plugin_path/.git" ]; then
            echo "NOT A GIT REPO"
            VALIDATION_FAILED_LIST+=("$plugin_name (not a git repo)")
            VALIDATION_FAILED=1
            continue
        fi

        # Verify it has plugin.json
        if [ ! -f "$plugin_path/.claude-plugin/plugin.json" ]; then
            echo "NOT A PLUGIN (no .claude-plugin/plugin.json)"
            VALIDATION_FAILED_LIST+=("$plugin_name (not a plugin)")
            VALIDATION_FAILED=1
            continue
        fi

        # Validate with CPV --strict
        if [ -n "$VALIDATE" ]; then
            VOUTPUT=$(cd "$CPV_DIR" && uv run python "$VALIDATOR" "$plugin_path" --strict 2>&1)
            VCODE=$?
            if [ "$VCODE" -eq 0 ]; then
                echo -n "PASSED "
                VALIDATED+=("$plugin_name")
            else
                echo "BLOCKED (exit $VCODE)"
                echo "$VOUTPUT" | grep -E "CRITICAL|MAJOR|MINOR|NIT" | head -10
                VALIDATION_FAILED_LIST+=("$plugin_name (exit $VCODE)")
                VALIDATION_FAILED=1
                continue
            fi
        fi

        # Push to origin
        BRANCH=$(cd "$plugin_path" && git symbolic-ref --short HEAD 2>/dev/null || echo "main")
        if [ -n "$DRY_RUN" ]; then
            echo "DRY-RUN (would push to origin/$BRANCH)"
            SKIPPED+=("$plugin_name (dry-run)")
        else
            if (cd "$plugin_path" && git push origin "$BRANCH" 2>&1); then
                echo "PUSHED"
                PUSHED+=("$plugin_name")
            else
                echo "PUSH FAILED"
                PUSH_FAILED_LIST+=("$plugin_name")
            fi
        fi
    done

    set -e

    echo ""
    if [ "$VALIDATION_FAILED" -eq 1 ]; then
        echo "ERROR: Validation failed for some plugins. Fix issues before pushing."
        echo ""
        echo "Failed:"
        for f in "${VALIDATION_FAILED_LIST[@]}"; do echo "  ! $f"; done
        exit 1
    fi
fi

# -- Validate marketplace ---------------------------------------------------

echo "--- marketplace validation (--strict) ---"

if [ -n "$VALIDATE" ] && [ -f "$MARKETPLACE_VALIDATOR" ]; then
    echo -n "  marketplace.json... "
    set +e
    VOUTPUT=$(cd "$CPV_DIR" && uv run python "$MARKETPLACE_VALIDATOR" "$MARKETPLACE_DIR" --strict 2>&1)
    VCODE=$?
    set -e
    if [ "$VCODE" -eq 0 ]; then
        echo "PASSED"
    else
        echo "BLOCKED (exit $VCODE)"
        echo "$VOUTPUT" | grep -E "CRITICAL|MAJOR|MINOR|NIT" | head -10
        echo ""
        echo "ERROR: Marketplace validation failed. Fix issues before pushing."
        exit 1
    fi
else
    echo "  SKIPPED"
fi
echo ""

# -- Sync versions into marketplace.json ------------------------------------

echo "--- marketplace version sync ---"
cd "$MARKETPLACE_DIR"

if [ -f "scripts/sync_marketplace_versions.py" ]; then
    echo "  Syncing versions..."
    uv run python scripts/sync_marketplace_versions.py --quiet 2>&1 || true
fi

# Commit if marketplace.json changed
if ! git diff --quiet .claude-plugin/marketplace.json 2>/dev/null; then
    git add .claude-plugin/marketplace.json
    git commit -m "chore: sync marketplace.json plugin versions"
    echo "  Committed version sync"
fi

# -- Push marketplace -------------------------------------------------------

echo ""
echo "--- marketplace push ---"

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "main")
LOCAL=$(git rev-parse HEAD 2>/dev/null)
REMOTE=$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "none")

if [ "$LOCAL" = "$REMOTE" ] && git diff --staged --quiet 2>/dev/null; then
    echo "  SKIP: marketplace already up to date"
    SKIPPED+=("marketplace (up to date)")
else
    if [ -n "$DRY_RUN" ]; then
        echo "  DRY-RUN: would push marketplace to origin/$BRANCH"
        SKIPPED+=("marketplace (dry-run)")
    else
        MARKETPLACE_PUSHED=0
        for attempt in 1 2 3; do
            echo "  Push attempt $attempt/3..."
            git pull origin "$BRANCH" --rebase 2>&1 || true
            if git push origin "$BRANCH" 2>&1; then
                MARKETPLACE_PUSHED=1
                break
            fi
            echo "  Push attempt $attempt failed, retrying in 5s..."
            sleep 5
        done
        if [ "$MARKETPLACE_PUSHED" -eq 1 ]; then
            echo "  PUSHED marketplace"
            PUSHED+=("marketplace")
        else
            echo "  FAILED marketplace (after 3 attempts)"
            exit 1
        fi
    fi
fi

# -- Summary ----------------------------------------------------------------

echo ""
echo "============================================================"
echo "  Results"
echo "============================================================"
echo ""
if [ ${#VALIDATED[@]} -gt 0 ]; then
    echo "  VALIDATED (${#VALIDATED[@]}):"
    for p in "${VALIDATED[@]}"; do echo "    OK $p"; done
fi
if [ ${#PUSHED[@]} -gt 0 ]; then
    echo "  PUSHED (${#PUSHED[@]}):"
    for p in "${PUSHED[@]}"; do echo "    OK $p"; done
fi
if [ ${#SKIPPED[@]} -gt 0 ]; then
    echo "  SKIPPED (${#SKIPPED[@]}):"
    for s in "${SKIPPED[@]}"; do echo "    - $s"; done
fi
if [ ${#PUSH_FAILED_LIST[@]} -gt 0 ]; then
    echo "  PUSH FAILED (${#PUSH_FAILED_LIST[@]}):"
    for f in "${PUSH_FAILED_LIST[@]}"; do echo "    FAIL $f"; done
fi
echo ""
echo "Done."
```

---

## render_readme_table.py

Regenerates the README's `## Plugin Versions` table directly from
`.claude-plugin/marketplace.json`, between the canonical
`<!-- PLUGIN-VERSIONS-START -->` / `<!-- PLUGIN-VERSIONS-END -->` markers (see
[readme-template.md](readme-template.md)). marketplace.json is the single
source of truth for both the versions AND which plugins are listed — the
Update Versions workflow only ever touches marketplace.json, so nothing else
would rewrite the README if this script didn't exist.

### Usage

```bash
python3 scripts/render_readme_table.py            # rewrite README.md
python3 scripts/render_readme_table.py --check    # exit 1 if it would change
```

`--check` is a **CI gate**, not a preview: it makes no changes and exits
non-zero the moment the rendered table would differ from what's on disk — see
the "Verify README table is up to date" step in
[workflow-templates.md](workflow-templates.md). Running it with no flags
rewrites `README.md` in place.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | README already matches (or was rewritten) |
| `1` | `--check` found drift, or an input was missing/invalid (no `.claude-plugin/marketplace.json`, no `README.md`, invalid JSON, empty `plugins` array, or the markers are missing from README.md) |

### Script

```python
#!/usr/bin/env python3
"""Regenerate the README plugin table from .claude-plugin/marketplace.json.

The table between the PLUGIN-VERSIONS markers is DERIVED data. Hand-maintaining
it drifts silently: the Update Submodules workflow only touches marketplace.json,
so nothing else ever rewrote the README. This script makes marketplace.json the
single source of truth for both the versions AND which plugins are listed.

Usage:
    python3 scripts/render_readme_table.py            # rewrite README.md
    python3 scripts/render_readme_table.py --check    # exit 1 if it would change

Exit codes:
    0 - README already matches (or was rewritten)
    1 - --check found drift, or an input was missing/invalid
"""

import argparse
import json
import sys
from pathlib import Path

START = "<!-- PLUGIN-VERSIONS-START -->"
END = "<!-- PLUGIN-VERSIONS-END -->"


def cell(text: str) -> str:
    """Escape a value so it cannot break out of its markdown table cell.

    A literal `|` in a description would silently add a column and shift every
    later cell, so it is escaped rather than trusted. Newlines collapse for the
    same reason.
    """
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def repo_url(plugin: dict) -> str | None:
    """Prefer the explicit repository URL; fall back to the github source."""
    url = plugin.get("repository")
    if isinstance(url, str) and url.startswith("http"):
        return url
    src = plugin.get("source")
    if isinstance(src, dict) and src.get("source") == "github" and src.get("repo"):
        return f"https://github.com/{src['repo']}"
    return None


def render(plugins: list[dict]) -> str:
    lines = [
        START,
        "## Plugin Versions",
        "",
        "<!-- GENERATED by scripts/render_readme_table.py from .claude-plugin/marketplace.json — do not edit by hand. -->",
        "",
        "| Plugin | Version | Category | Description |",
        "|--------|---------|----------|-------------|",
    ]
    if not plugins:
        # A legitimately empty marketplace (a fresh scaffold) gets an explicit
        # placeholder row: a header with no body reads as a rendering failure, and
        # the scaffold has to pass its own --check gate on its very first run. FOUR
        # cells, because this table has four columns — the scaffolded
        # update_catalog.py uses the same wording on its own THREE-column table, and
        # copying that row verbatim would emit a malformed one here (MD056).
        lines.append("| *(no plugins yet)* | | | |")
    for p in sorted(plugins, key=lambda x: x.get("name", "")):
        name = cell(p.get("name", "?"))
        url = repo_url(p)
        label = f"[{name}]({url})" if url else name
        # "developer-tools" is the manifest's slug form; the table shows prose.
        category = cell(p.get("category", "")).replace("-", " ").replace("_", " ").title()
        lines.append(
            f"| {label} | {cell(p.get('version', '?'))} | {category} | {cell(p.get('description', ''))} |"
        )
    # No generation DATE in the rendered block. The reference implementation stamped
    # date.today() here, which makes --check compare a value that changes every midnight:
    # a repo whose README was rendered yesterday fails the CI gate today on an unchanged
    # manifest. The count is derived from the manifest and is stable; git already records
    # when the table last changed.
    lines += ["", f"*{len(plugins)} plugins*", "", END]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--check", action="store_true", help="exit 1 on drift instead of rewriting")
    args = ap.parse_args()

    mj_path = args.root / ".claude-plugin" / "marketplace.json"
    readme_path = args.root / "README.md"
    for p in (mj_path, readme_path):
        if not p.is_file():
            print(f"error: missing {p}", file=sys.stderr)
            return 1

    try:
        manifest = json.loads(mj_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        print(f"error: {mj_path} is not valid JSON: {err}", file=sys.stderr)
        return 1

    # The guard is on the manifest SHAPE, not on emptiness, and it runs first and
    # unconditionally. The hazard the old emptiness guard named — "a rendering bug
    # must not look like 'the marketplace has no plugins'" — is a MISSING or
    # wrongly-typed `plugins` key, and that is what a shape check catches, with the
    # real reason instead of a misleading one. It also covers a case emptiness never
    # reached: a TRUTHY non-list (a dict, a non-empty string) skipped the old guard
    # entirely and died in `sorted(...)` with a raw AttributeError.
    #
    # `"plugins": []` is NOT that hazard. A marketplace with no plugins genuinely has
    # no plugins, and refusing it left every fresh scaffold unable to pass the --check
    # gate it ships with.
    if not isinstance(manifest, dict):
        print(f"error: {mj_path} is a JSON {type(manifest).__name__}, expected an object", file=sys.stderr)
        return 1
    if "plugins" not in manifest:
        print(f'error: {mj_path} has no "plugins" key', file=sys.stderr)
        return 1
    plugins = manifest["plugins"]
    if not isinstance(plugins, list):
        print(f'error: "plugins" in {mj_path} is a {type(plugins).__name__}, expected a list', file=sys.stderr)
        return 1

    text = readme_path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        print(f"error: {readme_path} has no {START} / {END} markers", file=sys.stderr)
        return 1

    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    updated = head + render(plugins) + tail

    if updated == text:
        print(f"README table already current ({len(plugins)} plugins)")
        return 0
    if args.check:
        print("README table is STALE — run scripts/render_readme_table.py", file=sys.stderr)
        return 1
    readme_path.write_text(updated, encoding="utf-8")
    print(f"README table regenerated ({len(plugins)} plugins)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Action Version Reference

All scripts in this document are tested with:

| Tool | Version | Notes |
|------|---------|-------|
| `uv` | 0.5+ | Python package manager and runner |
| `ruff` | 0.8+ | Python linter/formatter |
| `git-cliff` | 2.0+ | Changelog generator (optional) |
| `gh` | 2.40+ | GitHub CLI for API access |
