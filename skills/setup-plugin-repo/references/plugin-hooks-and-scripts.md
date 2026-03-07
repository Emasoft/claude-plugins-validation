# Plugin Hooks and Scripts

## Table of Contents
- [pre-push Hook Template](#pre-push-hook-template)
- [publish.py Pipeline Template](#publishpy-pipeline-template)
- [setup-hooks.py Template](#setup-hookspy-template)
- [Placeholder Reference](#placeholder-reference)

---

## pre-push Hook Template

Git pre-push hook enforcing three quality gates before any push is allowed:
1. **Version bump check** -- blocks if local version matches remote (forces semver bump)
2. **Lint** -- runs the lint script on the entire repo
3. **Validate** -- runs the plugin validator in strict mode (zero tolerance)

Also checks `marketplace.json` version consistency when present.

Save as `git-hooks/pre-push`:

```python
#!/usr/bin/env python3
"""Pre-push hook for {{PLUGIN_NAME}}.

Exit codes from {{VALIDATE_SCRIPT}}:
  0 - All checks passed
  1 - CRITICAL issues found (blocks push)
  2 - MAJOR issues found (blocks push)
  3 - MINOR issues found (blocks push)

Strict mode (default): ALL non-zero exit codes block push.
"""

import fnmatch, json, os, subprocess, sys
from pathlib import Path

# -- ANSI colors (disabled when NO_COLOR is set or stdout is not a tty) ------

def _colors_supported() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.name == "nt":
        try:
            import colorama; colorama.init(); return True
        except ImportError:
            return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

_USE_COLOR = _colors_supported()
RED    = "\033[0;31m" if _USE_COLOR else ""
YELLOW = "\033[1;33m" if _USE_COLOR else ""
GREEN  = "\033[0;32m" if _USE_COLOR else ""
BLUE   = "\033[0;34m" if _USE_COLOR else ""
NC     = "\033[0m"    if _USE_COLOR else ""

# -- Plugin file patterns (trigger full validation when changed) -------------

PLUGIN_PATTERNS = [
    ".claude-plugin/*", "agents/*", "commands/*", "skills/*",
    "hooks/*", "scripts/*.py", "scripts/*.sh", "*.mcp.json",
]

ZERO_SHA = "0" * 40  # all-zeros SHA = branch creation/deletion

# -- Helpers -----------------------------------------------------------------

def cprint(msg: str) -> None:
    print(msg, flush=True)

def get_repo_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, check=True)
    return Path(r.stdout.strip())

def find_python_command() -> list[str]:
    """Prefer uv run python; fall back to python3."""
    try:
        subprocess.run(["uv", "--version"], capture_output=True, check=True)
        return ["uv", "run", "python"]
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ["python3"]

def file_matches_plugin_patterns(filepath: str) -> bool:
    normalised = filepath.replace(os.sep, "/")
    return any(fnmatch.fnmatch(normalised, p) for p in PLUGIN_PATTERNS)

def get_changed_files(local_sha: str, remote_sha: str) -> list[str]:
    if remote_sha == ZERO_SHA:
        try:
            r = subprocess.run(["git", "diff", "--name-only", local_sha, "HEAD~10"],
                               capture_output=True, text=True, check=True)
            return r.stdout.strip().splitlines()
        except subprocess.CalledProcessError:
            r = subprocess.run(["git", "ls-tree", "-r", "--name-only", local_sha],
                               capture_output=True, text=True, check=True)
            return r.stdout.strip().splitlines()
    else:
        try:
            r = subprocess.run(["git", "diff", "--name-only", f"{remote_sha}..{local_sha}"],
                               capture_output=True, text=True, check=True)
            return r.stdout.strip().splitlines()
        except subprocess.CalledProcessError:
            return []

def run_script(python_cmd, script, args=None, timeout=180, cwd=None) -> int:
    cmd = [*python_cmd, str(script)] + (args or [])
    try:
        return subprocess.run(cmd, timeout=timeout,
                              cwd=str(cwd) if cwd else None).returncode
    except subprocess.TimeoutExpired:
        cprint(f"  {YELLOW}TIMEOUT: {script.name} exceeded {timeout}s{NC}")
        return 0

def extract_version(filepath: Path) -> str | None:
    try:
        return json.loads(filepath.read_text(encoding="utf-8")).get("version")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

# -- Main logic --------------------------------------------------------------

def main() -> int:
    repo_root = get_repo_root()
    cprint(f"{BLUE}  Pre-push Full Plugin Validation{NC}\n")

    remote = sys.argv[1] if len(sys.argv) > 1 else "<unknown>"
    cprint(f"{BLUE}Pushing to:{NC} {remote}\n")

    # Parse stdin: each line is LOCAL_REF LOCAL_SHA REMOTE_REF REMOTE_SHA
    plugin_files_changed = False
    for line in sys.stdin.read().strip().splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        _, local_sha, _, remote_sha = parts[:4]
        if local_sha == ZERO_SHA:
            continue
        if any(file_matches_plugin_patterns(f)
               for f in get_changed_files(local_sha, remote_sha)):
            plugin_files_changed = True
            break

    if not plugin_files_changed:
        cprint(f"{GREEN}No plugin files changed. Skipping validation.{NC}")
        return 0

    # Gate 1: Version bump enforcement
    cprint(f"{BLUE}Checking version bump...{NC}")
    local_version = extract_version(repo_root / ".claude-plugin" / "plugin.json")
    if local_version:
        try:
            r = subprocess.run(
                ["git", "show", "{{DEFAULT_BRANCH}}:.claude-plugin/plugin.json"],
                capture_output=True, text=True, cwd=str(repo_root))
            if r.returncode == 0:
                rv = json.loads(r.stdout).get("version")
                if rv and local_version == rv:
                    cprint(f"{RED}BLOCKED: Version not bumped! "
                           f"Local={local_version} Remote={rv}{NC}")
                    return 1
                cprint(f"{GREEN}Version bump OK: {rv} -> {local_version}{NC}")
        except Exception:
            cprint(f"{YELLOW}Could not check remote version{NC}")

    # Gate 2: Lint
    python_cmd = find_python_command()
    cprint(f"{BLUE}Running linting...{NC}")
    if run_script(python_cmd, repo_root / "scripts" / "{{LINT_SCRIPT}}",
                  [str(repo_root)], cwd=repo_root) != 0:
        cprint(f"{RED}BLOCKED: Linting issues found{NC}")
        return 1

    # Gate 3: Validate (strict mode)
    cprint(f"{BLUE}Running validation...{NC}")
    ve = run_script(python_cmd, repo_root / "scripts" / "{{VALIDATE_SCRIPT}}",
                    [".", "--verbose", "--strict"], cwd=repo_root)

    # Optional: marketplace.json consistency
    mj = repo_root / "marketplace.json"
    if mj.is_file():
        pv = extract_version(repo_root / ".claude-plugin" / "plugin.json")
        mv = extract_version(mj)
        if pv and mv and pv != mv:
            cprint(f"{YELLOW}WARNING: plugin.json={pv} != marketplace.json={mv}{NC}")

    if ve == 0:
        cprint(f"{GREEN}PASSED: Push allowed.{NC}")
    else:
        labels = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR"}
        cprint(f"{RED}BLOCKED: {labels.get(ve, f'exit {ve}')} issues found{NC}")
    return 0 if ve == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

---

## publish.py Pipeline Template

Unified publish pipeline: test -> lint -> validate -> consistency -> bump -> commit -> push.

**Key function signatures** (see inline comments for details):

| Function | Signature | Purpose |
|---|---|---|
| `run` | `(cmd, cwd, *, check=True)` | Run command, stream output, fail-fast |
| `parse_semver` | `(version: str) -> tuple | None` | Parse `X.Y.Z` into `(major, minor, patch)` |
| `bump_semver` | `(current, bump_type) -> str | None` | Bump by `major`/`minor`/`patch` |
| `get_current_version` | `(plugin_root) -> str | None` | Read version from `plugin.json` |
| `update_plugin_json` | `(root, new_ver) -> (ok, msg)` | Write version to `plugin.json` |
| `update_pyproject_toml` | `(root, new_ver) -> (ok, msg)` | Write version to `pyproject.toml` |
| `update_python_versions` | `(root, new_ver) -> list[(ok, msg)]` | Update `__version__` in all `.py` files |
| `check_version_consistency` | `(root) -> (ok, msg)` | Verify all version sources match |
| `do_bump` | `(root, new_ver, dry_run=False) -> bool` | Orchestrate all version updates |

**Pipeline stages** (all fail-fast -- any failure aborts the pipeline):

```
Step 1: Check working tree     git status --porcelain (must be clean)
Step 2: Run tests               uv run pytest {{TEST_DIR}}/ -x -q --tb=short
Step 3: Lint files               uv run python scripts/{{LINT_SCRIPT}} .
Step 4: Validate plugin          uv run python scripts/{{VALIDATE_SCRIPT}} . --strict
Step 5: Version consistency      check all version sources match
Step 6: Bump version             update plugin.json, pyproject.toml, __version__ vars
Step 7: Commit                   git add -A && git commit -m "chore: bump version to X.Y.Z"
Step 8: Push                     git push origin HEAD
```

**CLI usage:**

```bash
uv run python scripts/publish.py --patch              # 1.0.0 -> 1.0.1
uv run python scripts/publish.py --minor              # 1.0.0 -> 1.1.0
uv run python scripts/publish.py --major              # 1.0.0 -> 2.0.0
uv run python scripts/publish.py --patch --dry-run    # preview only
uv run python scripts/publish.py --patch --skip-tests # skip pytest
```

**Dependencies:** Requires `gitignore-filter` (`uv add gitignore-filter`) for scanning
Python files while respecting `.gitignore`. If your project does not use `__version__`
scanning, remove `_get_gi()`, `update_python_versions()`, and the corresponding lines
in `check_version_consistency()`.

---

## setup-hooks.py Template

Installs git hooks from `git-hooks/` into `.git/hooks/` and makes them executable.

Save as `scripts/setup-hooks.py`:

```python
#!/usr/bin/env python3
"""Install git hooks from git-hooks/ into .git/hooks/.

Usage: uv run python scripts/setup-hooks.py
"""

from __future__ import annotations
import os, shutil, stat, sys
from pathlib import Path


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    repo_root = get_repo_root()
    source_dir = repo_root / "git-hooks"
    target_dir = repo_root / ".git" / "hooks"

    if not source_dir.is_dir():
        print(f"ERROR: {source_dir} does not exist.", file=sys.stderr)
        return 1
    if not target_dir.is_dir():
        print(f"ERROR: {target_dir} does not exist. Is this a git repo?",
              file=sys.stderr)
        return 1

    hooks = [h for h in source_dir.iterdir() if not h.name.startswith(".")]
    if not hooks:
        print("No hooks found in git-hooks/.")
        return 0

    for hook_src in hooks:
        hook_dst = target_dir / hook_src.name
        shutil.copy2(hook_src, hook_dst)
        hook_dst.chmod(hook_dst.stat().st_mode
                       | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  Installed: {hook_src.name} -> .git/hooks/{hook_src.name}")

    print(f"\nDone. {len(hooks)} hook(s) installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## Placeholder Reference

All repo-specific values use `{{PLACEHOLDER}}` tokens. Replace before use.

| Placeholder | Description | Example Value |
|---|---|---|
| `{{PLUGIN_NAME}}` | Plugin name (used in docstrings) | `my-awesome-plugin` |
| `{{DEFAULT_BRANCH}}` | Remote branch for version comparison | `origin/master` or `origin/main` |
| `{{LINT_SCRIPT}}` | Lint script filename in `scripts/` | `lint_files.py` |
| `{{VALIDATE_SCRIPT}}` | Validation script filename in `scripts/` | `validate_plugin.py` |
| `{{TEST_DIR}}` | Test suite directory | `tests` |

### Installation Quick-Start

```bash
# 1. Create git-hooks directory and save the pre-push hook
mkdir -p git-hooks
# Copy pre-push template above into git-hooks/pre-push

# 2. Save publish and setup scripts
# Copy templates above into scripts/publish.py and scripts/setup-hooks.py

# 3. Install hooks
uv run python scripts/setup-hooks.py

# 4. Test with dry-run
uv run python scripts/publish.py --patch --dry-run

# 5. Publish for real
uv run python scripts/publish.py --patch
```
