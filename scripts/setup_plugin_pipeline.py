#!/usr/bin/env python3
"""setup_plugin_pipeline.py - Universal pipeline installer for Claude Code plugins.

This script sets up a complete, validated, rebase-safe development pipeline
for Claude Code plugins and marketplaces. It can be used:

1. By the plugin-validator agent to fix pipeline issues
2. By developers to bootstrap new plugin projects
3. By CI/CD to validate pipeline integrity

PIPELINE COMPONENTS:
====================
1. Git Hooks (rebase-safe v2 architecture)
   - pre-commit: Lint, validate, skip during rebase
   - pre-push: Full validation, blocks broken plugins
   - post-rewrite: Changelog after rebase/amend (fires ONCE)
   - post-merge: Changelog after merge

2. Validation Scripts
   - validate_plugin.py, validate_skill.py, validate_hook.py, etc.

3. CI/CD Templates
   - GitHub Actions workflow for validation on PR/push

4. Configuration Files
   - cliff.toml for changelog generation
   - .gitignore additions

USAGE:
======
    # Auto-detect and setup
    python setup_plugin_pipeline.py /path/to/project

    # Setup specific type
    python setup_plugin_pipeline.py /path/to/project --type marketplace
    python setup_plugin_pipeline.py /path/to/project --type plugin

    # Validate existing setup
    python setup_plugin_pipeline.py /path/to/project --validate-only

    # Fix issues automatically
    python setup_plugin_pipeline.py /path/to/project --fix

    # Show what would be done
    python setup_plugin_pipeline.py /path/to/project --dry-run
"""

import argparse
import configparser
import json
import os

# ANSI Colors - Enable Windows support
import platform as _platform
import stat
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

if _platform.system() == "Windows":
    # Enable ANSI escape sequences on Windows 10+
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except (AttributeError, OSError):
        pass  # Not Windows or older Windows without ANSI support

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
NC = "\033[0m"


class ProjectType(Enum):
    """Type of project being configured."""

    MARKETPLACE = "marketplace"  # Contains multiple plugins
    PLUGIN = "plugin"  # Single plugin
    PLUGIN_IN_MARKETPLACE = "plugin_in_marketplace"  # Plugin as submodule
    UNKNOWN = "unknown"


class IssueLevel(Enum):
    """Severity level of pipeline issues."""

    CRITICAL = "critical"  # Pipeline won't work
    MAJOR = "major"  # Some features broken
    MINOR = "minor"  # Warnings only
    INFO = "info"  # Informational


@dataclass
class PipelineIssue:
    """Represents an issue with the pipeline setup."""

    level: IssueLevel
    component: str
    message: str
    fix_available: bool = False
    fix_description: str = ""


@dataclass
class PipelineStatus:
    """Status of the pipeline validation."""

    project_type: ProjectType
    project_path: Path
    issues: list[PipelineIssue] = field(default_factory=list)
    hooks_installed: dict[str, bool] = field(default_factory=dict)
    config_files: dict[str, bool] = field(default_factory=dict)
    submodules: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Check if pipeline has no critical or major issues."""
        return not any(issue.level in (IssueLevel.CRITICAL, IssueLevel.MAJOR) for issue in self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.level == IssueLevel.CRITICAL)

    @property
    def major_count(self) -> int:
        return sum(1 for i in self.issues if i.level == IssueLevel.MAJOR)

    @property
    def minor_count(self) -> int:
        return sum(1 for i in self.issues if i.level == IssueLevel.MINOR)


# =============================================================================
# HOOK TEMPLATES
# =============================================================================

PRE_COMMIT_HOOK = '''#!/usr/bin/env python3
"""pre-commit hook: Validate staged changes before commit.

SKIPS during rebase/cherry-pick/merge to prevent conflicts.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

RED = "\\033[0;31m"
GREEN = "\\033[0;32m"
YELLOW = "\\033[1;33m"
BLUE = "\\033[0;34m"
NC = "\\033[0m"


def is_rebase_in_progress() -> bool:
    """Check if we're in the middle of a rebase or similar operation."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        return False

    git_dir = Path(result.stdout.strip()).resolve()
    indicators = [
        git_dir / "rebase-merge",
        git_dir / "rebase-apply",
        git_dir / "CHERRY_PICK_HEAD",
        git_dir / "MERGE_HEAD",
        git_dir / "BISECT_LOG",
    ]
    return any(i.exists() for i in indicators)


def get_staged_files() -> list[str]:
    """Get list of staged files."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0:
        return [f for f in result.stdout.strip().split("\\n") if f]
    return []


def lint_python_files(files: list[str]) -> bool:
    """Lint Python files with ruff."""
    py_files = [f for f in files if f.endswith(".py") and Path(f).exists()]
    if not py_files:
        return True

    try:
        result = subprocess.run(
            ["ruff", "check"] + py_files,
            capture_output=True, timeout=60
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}⚠ ruff timed out{NC}")
        return True  # Don't block on timeout
    except FileNotFoundError:
        print(f"{YELLOW}⚠ ruff not installed{NC}")
        return True


def validate_json_files(files: list[str]) -> tuple[bool, list[str]]:
    """Validate JSON syntax in staged JSON files."""
    json_files = [f for f in files if f.endswith(".json")]
    errors = []
    for f in json_files:
        try:
            with open(f) as fp:
                json.load(fp)
        except json.JSONDecodeError as e:
            errors.append(f"{f}: {e}")
        except FileNotFoundError:
            # File may have been deleted since staging - skip
            pass
        except OSError as e:
            errors.append(f"{f}: I/O error: {e}")
    return len(errors) == 0, errors


def check_sensitive_data(diff: str) -> list[str]:
    """Check for potential sensitive data in diff."""
    patterns = [
        (r'password\\s*[:=]\\s*[\\'\\"].+[\\'\\"]', "password"),
        (r'api[_-]?key\\s*[:=]\\s*[\\'\\"].+[\\'\\"]', "API key"),
        (r'secret\\s*[:=]\\s*[\\'\\"].+[\\'\\"]', "secret"),
        (r'token\\s*[:=]\\s*[\\'\\"][a-zA-Z0-9]{20,}[\\'\\"]', "token"),
    ]
    warnings = []
    for line in diff.split("\\n"):
        if line.startswith("-"):
            continue
        if any(x in line.lower() for x in ["example", "placeholder", "your_", "<"]):
            continue
        for pattern, name in patterns:
            if re.search(pattern, line, re.IGNORECASE):
                warnings.append(f"Potential {name} detected")
                break
    return warnings


def main() -> int:
    if is_rebase_in_progress():
        print(f"{BLUE}[pre-commit] Skipping during rebase/cherry-pick/merge{NC}")
        return 0

    print("Running pre-commit validations...")
    failed = False
    staged_files = get_staged_files()

    # Validate JSON files
    print("Checking JSON syntax... ", end="", flush=True)
    json_ok, json_errors = validate_json_files(staged_files)
    if json_ok:
        print(f"{GREEN}✔{NC}")
    else:
        print(f"{RED}✘{NC}")
        for err in json_errors:
            print(f"  {RED}{err}{NC}")
        failed = True

    # Lint Python files
    print("Linting Python files... ", end="", flush=True)
    if lint_python_files(staged_files):
        print(f"{GREEN}✔{NC}")
    else:
        print(f"{YELLOW}⚠ issues found (non-blocking){NC}")

    # Check for sensitive data
    print("Checking for sensitive data... ", end="", flush=True)
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--cached", "-U0"],
            capture_output=True, text=True, timeout=30
        )
        diff = diff_result.stdout
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}⚠ git diff timed out, skipping{NC}")
        diff = ""
    warnings = check_sensitive_data(diff)
    if not warnings:
        print(f"{GREEN}✔{NC}")
    else:
        print(f"{YELLOW}⚠ review recommended{NC}")
        for w in warnings[:3]:
            print(f"  {YELLOW}{w}{NC}")

    if failed:
        print(f"\\n{RED}Pre-commit validation failed.{NC}")
        print("To bypass (not recommended): git commit --no-verify")
        return 1

    print(f"{GREEN}Pre-commit validations passed{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

PRE_PUSH_HOOK = '''#!/usr/bin/env python3
"""pre-push hook: Full validation before pushing with auto-fix loop.

Implements a CI/CD loop that:
1. Runs linting/formatting with auto-fix (ruff)
2. Checks if files were modified
3. If modified, commits the fixes
4. Re-runs validation
5. Loops until clean or max iterations reached

Blocks push only if unfixable issues remain after all auto-fix attempts.
"""

import shutil
import subprocess
import sys
from pathlib import Path

RED = "\\033[0;31m"
GREEN = "\\033[0;32m"
YELLOW = "\\033[1;33m"
BLUE = "\\033[0;34m"
BOLD = "\\033[1m"
NC = "\\033[0m"

MAX_FIX_ITERATIONS = 5


def get_repo_root() -> Path:
    """Get repository root."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}⚠ git rev-parse timed out{NC}")
    except OSError as e:
        print(f"{RED}git error: {e}{NC}")
    # Fallback to current directory
    return Path.cwd()


def find_validator() -> Path | None:
    """Find the plugin validator script."""
    repo_root = get_repo_root()

    # Check common locations
    candidates = [
        repo_root / "scripts" / "validate_plugin.py",
        repo_root / "claude-plugins-validation" / "scripts" / "validate_plugin.py",
        Path(__file__).parent.parent / "scripts" / "validate_plugin.py",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def has_uncommitted_changes(repo_root: Path) -> bool:
    """Check if there are uncommitted changes in tracked files."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print(f"{YELLOW}⚠ git status failed{NC}")
            return False
        # Filter for modified/added files (not untracked)
        lines = [l for l in result.stdout.strip().split("\\n") if l and not l.startswith("??")]
        return len(lines) > 0
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}⚠ git status timed out{NC}")
        return False
    except OSError:
        return False


def detect_languages(repo_root: Path) -> dict[str, list[Path]]:
    """Detect which programming languages are present in the repo.

    Returns:
        Dictionary mapping language name to list of files.
    """
    # Directories to exclude from scanning
    exclude_dirs = {".venv", "venv", "__pycache__", ".git", "node_modules",
                    ".mypy_cache", ".ruff_cache", "build", "dist", ".tox"}

    def should_include(path: Path) -> bool:
        return not any(part in exclude_dirs for part in path.parts)

    languages: dict[str, list[Path]] = {}

    # Python
    py_files = [f for f in repo_root.glob("**/*.py") if should_include(f)]
    if py_files:
        languages["python"] = py_files

    # JavaScript/TypeScript
    js_files = [f for f in repo_root.glob("**/*.js") if should_include(f)]
    ts_files = [f for f in repo_root.glob("**/*.ts") if should_include(f)]
    jsx_files = [f for f in repo_root.glob("**/*.jsx") if should_include(f)]
    tsx_files = [f for f in repo_root.glob("**/*.tsx") if should_include(f)]
    all_js = js_files + ts_files + jsx_files + tsx_files
    if all_js:
        languages["javascript"] = all_js

    # Shell/Bash
    sh_files = [f for f in repo_root.glob("**/*.sh") if should_include(f)]
    bash_files = [f for f in repo_root.glob("**/*.bash") if should_include(f)]
    all_shell = sh_files + bash_files
    if all_shell:
        languages["shell"] = all_shell

    # Go
    go_files = [f for f in repo_root.glob("**/*.go") if should_include(f)]
    if go_files:
        languages["go"] = go_files

    # Rust
    rs_files = [f for f in repo_root.glob("**/*.rs") if should_include(f)]
    if rs_files:
        languages["rust"] = rs_files

    # Markdown
    md_files = [f for f in repo_root.glob("**/*.md") if should_include(f)]
    mdx_files = [f for f in repo_root.glob("**/*.mdx") if should_include(f)]
    all_md = md_files + mdx_files
    if all_md:
        languages["markdown"] = all_md

    # JSON
    json_files = [f for f in repo_root.glob("**/*.json") if should_include(f)]
    if json_files:
        languages["json"] = json_files

    # YAML
    yml_files = [f for f in repo_root.glob("**/*.yml") if should_include(f)]
    yaml_files = [f for f in repo_root.glob("**/*.yaml") if should_include(f)]
    all_yaml = yml_files + yaml_files
    if all_yaml:
        languages["yaml"] = all_yaml

    return languages


def install_python_tool(tool: str) -> bool:
    """Try to install a Python CLI tool.

    Priority:
    1. uv tool install (preferred - installs tools in isolated envs)
    2. uvx (just run, no install needed)
    3. pipx (fallback - similar to uv tool)
    4. pip install --user (last resort)

    Returns:
        True if installation succeeded, False otherwise.
    """
    last_error = ""

    # Method 1: uv tool install (preferred for CLI tools)
    # Always specify --python 3.12 for compatibility (some tools don't support 3.14+)
    if shutil.which("uv"):
        try:
            result = subprocess.run(
                ["uv", "tool", "install", "--python", "3.12", tool],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                print(f"{GREEN}  ✔ {tool} installed via uv tool (Python 3.12){NC}")
                return True
            # If already installed, that's fine
            if "already installed" in result.stderr.lower():
                print(f"{GREEN}  ✔ {tool} already installed via uv tool{NC}")
                return True
            last_error = result.stderr.strip() or result.stdout.strip()
        except subprocess.TimeoutExpired:
            last_error = "uv tool timed out after 120s"
        except OSError as e:
            last_error = str(e)

    # Method 2: pipx (similar to uv tool, if uv not available)
    if shutil.which("pipx"):
        try:
            result = subprocess.run(
                ["pipx", "install", tool],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                print(f"{GREEN}  ✔ {tool} installed via pipx{NC}")
                return True
            if "already installed" in result.stderr.lower() or "already installed" in result.stdout.lower():
                print(f"{GREEN}  ✔ {tool} already installed via pipx{NC}")
                return True
            last_error = result.stderr.strip() or result.stdout.strip()
        except subprocess.TimeoutExpired:
            last_error = "pipx timed out after 120s"
        except OSError as e:
            last_error = str(e)

    # Method 3: pip install --user (last resort)
    for pip_cmd in ["pip3", "pip"]:
        if shutil.which(pip_cmd):
            try:
                result = subprocess.run(
                    [pip_cmd, "install", "--user", tool],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    print(f"{GREEN}  ✔ {tool} installed via {pip_cmd} --user{NC}")
                    return True
                last_error = result.stderr.strip() or result.stdout.strip()
            except subprocess.TimeoutExpired:
                last_error = f"{pip_cmd} timed out after 120s"
            except OSError as e:
                last_error = str(e)

    if last_error:
        print(f"{RED}  Install error: {last_error[:200]}{NC}")
    return False


def ensure_linter_installed(language: str, repo_root: Path) -> bool:
    """Ensure the linter for a language is installed. Auto-install if possible.

    Returns:
        True if linter is available, False if cannot be installed.
    """
    if language == "python":
        # Check and install ruff
        if not shutil.which("ruff"):
            print(f"{YELLOW}  Installing ruff...{NC}")
            if not install_python_tool("ruff"):
                print(f"{RED}  ✘ Could not install ruff{NC}")
                return False

        # Check and install mypy (for type checking)
        if not shutil.which("mypy"):
            print(f"{YELLOW}  Installing mypy...{NC}")
            if not install_python_tool("mypy"):
                print(f"{YELLOW}  ⚠ Could not install mypy, type checking will be skipped{NC}")
                # Don't return False - mypy is optional, ruff is required

        return True

    elif language == "javascript":
        # Check for eslint in node_modules or globally
        local_eslint = repo_root / "node_modules" / ".bin" / "eslint"
        if local_eslint.exists() or shutil.which("eslint"):
            return True
        # Check for package.json to install eslint
        package_json = repo_root / "package.json"
        if package_json.exists():
            print(f"{YELLOW}  Installing eslint...{NC}")
            # Try bun, then npm, then pnpm
            for pkg_mgr in ["bun", "npm", "pnpm"]:
                if shutil.which(pkg_mgr):
                    result = subprocess.run(
                        [pkg_mgr, "install", "eslint", "--save-dev"],
                        cwd=repo_root,
                        capture_output=True, text=True, timeout=120
                    )
                    if result.returncode == 0:
                        print(f"{GREEN}  ✔ eslint installed via {pkg_mgr}{NC}")
                        return True
        print(f"{YELLOW}  ⚠ eslint not available, skipping JS/TS linting{NC}")
        return False

    elif language == "shell":
        if shutil.which("shellcheck"):
            return True
        # Try to auto-install shellcheck (cross-platform)
        print(f"{YELLOW}  Installing shellcheck...{NC}")
        import platform
        os_type = platform.system().lower()

        # Define package managers by platform and priority
        pkg_managers = []
        if os_type == "darwin":  # macOS
            pkg_managers = [
                ("brew", ["brew", "install", "shellcheck"]),
                ("port", ["sudo", "port", "install", "shellcheck"]),
            ]
        elif os_type == "linux":
            pkg_managers = [
                ("apt-get", ["sudo", "apt-get", "install", "-y", "shellcheck"]),
                ("dnf", ["sudo", "dnf", "install", "-y", "ShellCheck"]),
                ("yum", ["sudo", "yum", "install", "-y", "ShellCheck"]),
                ("pacman", ["sudo", "pacman", "-S", "--noconfirm", "shellcheck"]),
                ("zypper", ["sudo", "zypper", "install", "-y", "ShellCheck"]),
                ("apk", ["sudo", "apk", "add", "shellcheck"]),
                ("brew", ["brew", "install", "shellcheck"]),  # Linuxbrew
            ]
        elif os_type == "windows":
            pkg_managers = [
                ("scoop", ["scoop", "install", "shellcheck"]),
                ("choco", ["choco", "install", "shellcheck", "-y"]),
                ("winget", ["winget", "install", "--id", "koalaman.shellcheck", "-e"]),
            ]

        last_error = ""
        for pkg_mgr, cmd in pkg_managers:
            if shutil.which(pkg_mgr):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                    if result.returncode == 0:
                        print(f"{GREEN}  ✔ shellcheck installed via {pkg_mgr}{NC}")
                        return True
                    last_error = f"{pkg_mgr}: {result.stderr.strip() or result.stdout.strip()}"
                except subprocess.TimeoutExpired:
                    print(f"{YELLOW}  ⚠ {pkg_mgr} install timed out after 180s, trying next...{NC}")
                    last_error = f"{pkg_mgr}: timed out"
                except OSError as e:
                    last_error = f"{pkg_mgr}: {e}"

        if last_error:
            print(f"{YELLOW}  Last error: {last_error[:150]}{NC}")

        # Provide platform-specific install instructions
        install_hint = {
            "darwin": "brew install shellcheck",
            "linux": "apt install shellcheck  # or dnf/pacman/zypper",
            "windows": "scoop install shellcheck  # or choco/winget",
        }.get(os_type, "see https://github.com/koalaman/shellcheck#installing")
        print(f"{YELLOW}  ⚠ shellcheck not installed (install via: {install_hint}){NC}")
        return False

    elif language == "go":
        if shutil.which("gofmt"):
            return True
        # gofmt comes with Go installation, can't auto-install separately
        import platform
        os_type = platform.system().lower()
        install_hint = {
            "darwin": "brew install go  # or download from go.dev/dl",
            "linux": "apt install golang  # or dnf/pacman, or download from go.dev/dl",
            "windows": "scoop install go  # or choco install golang, or download from go.dev/dl",
        }.get(os_type, "https://go.dev/dl/")
        print(f"{YELLOW}  ⚠ Go tools not installed (install via: {install_hint}){NC}")
        return False

    elif language == "rust":
        if shutil.which("cargo"):
            # Check for rustfmt and clippy components
            # Note: rustup may not exist if Rust was installed via brew/system package
            has_rustup = shutil.which("rustup") is not None
            if not shutil.which("rustfmt"):
                if has_rustup:
                    print(f"{YELLOW}  Installing rustfmt via rustup...{NC}")
                    try:
                        result = subprocess.run(
                            ["rustup", "component", "add", "rustfmt"],
                            capture_output=True, text=True, timeout=120
                        )
                        if result.returncode != 0:
                            print(f"{YELLOW}  ⚠ rustfmt install failed: {result.stderr[:100]}{NC}")
                    except subprocess.TimeoutExpired:
                        print(f"{YELLOW}  ⚠ rustfmt install timed out{NC}")
                else:
                    print(f"{YELLOW}  ⚠ rustfmt not found and rustup unavailable{NC}")
            if not shutil.which("cargo-clippy"):
                if has_rustup:
                    print(f"{YELLOW}  Installing clippy via rustup...{NC}")
                    try:
                        result = subprocess.run(
                            ["rustup", "component", "add", "clippy"],
                            capture_output=True, text=True, timeout=120
                        )
                        if result.returncode != 0:
                            print(f"{YELLOW}  ⚠ clippy install failed: {result.stderr[:100]}{NC}")
                    except subprocess.TimeoutExpired:
                        print(f"{YELLOW}  ⚠ clippy install timed out{NC}")
                else:
                    print(f"{YELLOW}  ⚠ clippy not found and rustup unavailable{NC}")
            return True
        # Rust/Cargo not found
        import platform
        os_type = platform.system().lower()
        install_hint = {
            "darwin": "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh",
            "linux": "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh",
            "windows": "Download rustup-init.exe from https://rustup.rs/",
        }.get(os_type, "https://rustup.rs/")
        print(f"{YELLOW}  ⚠ Rust/Cargo not installed (install via: {install_hint}){NC}")
        return False

    elif language == "markdown":
        # Check for markdownlint-cli (preferred) or markdownlint
        # Try bun x / npx first (no global install needed)
        if shutil.which("bun") or shutil.which("npx"):
            return True  # Will use bun x or npx at runtime

        # Check for globally installed markdownlint-cli
        if shutil.which("markdownlint"):
            return True

        # Try to install globally
        print(f"{YELLOW}  Installing markdownlint-cli...{NC}")
        for pkg_mgr, cmd in [
            ("bun", ["bun", "add", "-g", "markdownlint-cli"]),
            ("npm", ["npm", "install", "-g", "markdownlint-cli"]),
        ]:
            if shutil.which(pkg_mgr):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                    if result.returncode == 0:
                        print(f"{GREEN}  ✔ markdownlint-cli installed via {pkg_mgr}{NC}")
                        return True
                except subprocess.TimeoutExpired:
                    print(f"{YELLOW}  ⚠ {pkg_mgr} install timed out{NC}")
                except OSError as e:
                    print(f"{YELLOW}  ⚠ {pkg_mgr} install failed: {e}{NC}")

        print(f"{YELLOW}  ⚠ markdownlint not available (install via: npm install -g markdownlint-cli){NC}")
        return False

    elif language == "json":
        # JSON validation uses built-in Python json module + optional prettier
        # We can always validate JSON with Python, so return True
        # Optional: check for prettier for formatting
        if shutil.which("bun") or shutil.which("npx") or shutil.which("prettier"):
            return True

        # JSON validation with Python is always available
        print(f"{BLUE}  Using Python json module for JSON validation{NC}")
        return True

    elif language == "yaml":
        # Check for yamllint
        if shutil.which("yamllint"):
            return True

        # Try to install via uv pip (preferred) or pip
        print(f"{YELLOW}  Installing yamllint...{NC}")
        if not install_python_tool("yamllint"):
            print(f"{YELLOW}  ⚠ Could not install yamllint{NC}")
            # Provide manual install hint
            print(f"{YELLOW}  ⚠ Install via: uv tool install --python 3.12 yamllint  OR  pipx install yamllint{NC}")
            return False

        return True

    return False


def lint_python(repo_root: Path) -> tuple[bool, bool]:
    """Lint Python files with ruff.

    Order: 1) lint+fix, 2) typecheck, 3) verify lint, 4) format (last)

    Returns:
        (success, files_changed)
    """
    files_changed = False

    # Step 1: Run ruff check with auto-fix
    print(f"{BLUE}    [1/4] ruff check --fix...{NC}")
    try:
        subprocess.run(
            ["ruff", "check", "--fix", "--select=E,F,W,I", str(repo_root)],
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    ruff check timed out{NC}")
    except FileNotFoundError:
        print(f"{RED}    ruff not found{NC}")
        return False, files_changed

    if has_uncommitted_changes(repo_root):
        files_changed = True

    # Step 2: Run type checker (mypy) if available
    if shutil.which("mypy"):
        print(f"{BLUE}    [2/4] mypy...{NC}")
        try:
            result = subprocess.run(
                ["mypy", "--ignore-missing-imports", str(repo_root)],
                capture_output=True, text=True, timeout=180
            )
            if result.returncode != 0:
                print(f"{RED}    Type errors found:{NC}")
                for line in result.stdout.strip().split("\\n")[:10]:
                    print(f"      {line}")
                return False, files_changed
        except subprocess.TimeoutExpired:
            print(f"{YELLOW}    mypy timed out, skipping{NC}")
    else:
        print(f"{YELLOW}    [2/4] mypy not installed, skipping typecheck{NC}")

    # Step 3: Verify lint check passes (no remaining unfixable issues)
    print(f"{BLUE}    [3/4] ruff check (verify)...{NC}")
    try:
        result = subprocess.run(
            ["ruff", "check", "--select=E,F,W", str(repo_root)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            # Lint issues remain that can't be auto-fixed
            print(f"{RED}    Unfixable lint issues remain{NC}")
            return False, files_changed
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    ruff verify timed out{NC}")

    # Step 4: Format ONLY if all above passed (formatting is last)
    print(f"{BLUE}    [4/4] ruff format...{NC}")
    try:
        subprocess.run(
            ["ruff", "format", str(repo_root)],
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    ruff format timed out{NC}")

    if has_uncommitted_changes(repo_root):
        files_changed = True

    return True, files_changed


def lint_javascript(repo_root: Path) -> tuple[bool, bool]:
    """Lint JavaScript/TypeScript files with eslint.

    Prefers: bun x eslint > npx eslint > local eslint > global eslint

    Returns:
        (success, files_changed)
    """
    files_changed = False

    # Find eslint - prefer bun/npx runners
    local_eslint = repo_root / "node_modules" / ".bin" / "eslint"
    if shutil.which("bun"):
        eslint_cmd = ["bun", "x", "eslint"]
    elif shutil.which("npx"):
        eslint_cmd = ["npx", "eslint"]
    elif local_eslint.exists():
        eslint_cmd = [str(local_eslint)]
    elif shutil.which("eslint"):
        eslint_cmd = ["eslint"]
    else:
        print(f"{YELLOW}    eslint not available, skipping{NC}")
        return True, False

    # Check if eslint config exists
    config_files = [".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml", "eslint.config.js"]
    has_config = any((repo_root / cfg).exists() for cfg in config_files)

    if not has_config:
        print(f"{YELLOW}    No eslint config found, skipping{NC}")
        return True, False

    # Run eslint with --fix
    print(f"{BLUE}    eslint --fix...{NC}")
    try:
        subprocess.run(
            eslint_cmd + ["--fix", "."],
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    eslint --fix timed out{NC}")
    except FileNotFoundError:
        print(f"{YELLOW}    eslint not found{NC}")
        return True, False

    if has_uncommitted_changes(repo_root):
        files_changed = True

    # Final check
    try:
        result = subprocess.run(
            eslint_cmd + ["."],
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0, files_changed
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    eslint verify timed out{NC}")
        return True, files_changed
    except FileNotFoundError:
        return True, files_changed


def lint_shell(repo_root: Path, files: list[Path]) -> tuple[bool, bool]:
    """Lint shell scripts with shellcheck.

    Returns:
        (success, files_changed) - shellcheck doesn't auto-fix, so files_changed is always False
    """
    print(f"{BLUE}    shellcheck...{NC}")

    all_passed = True
    for f in files:
        try:
            result = subprocess.run(
                ["shellcheck", "-x", str(f)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                all_passed = False
                print(f"{YELLOW}      {f.name}: issues found{NC}")
        except subprocess.TimeoutExpired:
            print(f"{YELLOW}      {f.name}: shellcheck timed out{NC}")
        except FileNotFoundError:
            print(f"{YELLOW}    shellcheck not found{NC}")
            return True, False  # Skip if shellcheck not available

    return all_passed, False


def lint_go(repo_root: Path) -> tuple[bool, bool]:
    """Lint Go files with gofmt and go vet.

    Returns:
        (success, files_changed)
    """
    files_changed = False

    # Run gofmt -w (auto-fix)
    print(f"{BLUE}    gofmt -w...{NC}")
    try:
        subprocess.run(
            ["gofmt", "-w", "."],
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    gofmt timed out{NC}")
    except FileNotFoundError:
        print(f"{RED}    gofmt not found{NC}")
        return False, files_changed

    if has_uncommitted_changes(repo_root):
        files_changed = True

    # Run go vet
    print(f"{BLUE}    go vet...{NC}")
    try:
        result = subprocess.run(
            ["go", "vet", "./..."],
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0, files_changed
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    go vet timed out{NC}")
        return True, files_changed
    except FileNotFoundError:
        return True, files_changed


def lint_rust(repo_root: Path) -> tuple[bool, bool]:
    """Lint Rust files with cargo fmt and cargo clippy.

    Returns:
        (success, files_changed)
    """
    files_changed = False

    # Check for Cargo.toml
    if not (repo_root / "Cargo.toml").exists():
        return True, False

    # Run cargo fmt
    print(f"{BLUE}    cargo fmt...{NC}")
    try:
        subprocess.run(
            ["cargo", "fmt"],
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    cargo fmt timed out{NC}")
    except FileNotFoundError:
        print(f"{RED}    cargo not found{NC}")
        return False, files_changed

    if has_uncommitted_changes(repo_root):
        files_changed = True

    # Run cargo clippy with auto-fix (if available)
    print(f"{BLUE}    cargo clippy --fix...{NC}")
    try:
        subprocess.run(
            ["cargo", "clippy", "--fix", "--allow-dirty", "--allow-staged"],
            cwd=repo_root,
            capture_output=True, text=True, timeout=180
        )
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    cargo clippy --fix timed out{NC}")

    if has_uncommitted_changes(repo_root):
        files_changed = True

    # Final check
    try:
        result = subprocess.run(
            ["cargo", "clippy"],
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0, files_changed
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    cargo clippy verify timed out{NC}")
        return True, files_changed
    except FileNotFoundError:
        return True, files_changed


def lint_markdown(repo_root: Path, files: list[Path]) -> tuple[bool, bool]:
    """Lint Markdown files with markdownlint-cli.

    Prefers: bun x markdownlint > npx markdownlint > global markdownlint

    Returns:
        (success, files_changed)
    """
    files_changed = False

    if not files:
        return True, False

    # Determine which runner to use
    if shutil.which("bun"):
        lint_cmd = ["bun", "x", "markdownlint-cli"]
    elif shutil.which("npx"):
        lint_cmd = ["npx", "markdownlint-cli"]
    elif shutil.which("markdownlint"):
        lint_cmd = ["markdownlint"]
    else:
        print(f"{YELLOW}    markdownlint not available, skipping{NC}")
        return True, False

    # Convert paths to strings for subprocess
    file_paths = [str(f) for f in files]

    # Run markdownlint with --fix
    print(f"{BLUE}    markdownlint --fix...{NC}")
    try:
        result = subprocess.run(
            lint_cmd + ["--fix"] + file_paths,
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
        # markdownlint returns 0 on success, 1 on lint errors
        # After --fix, some errors may remain (unfixable)
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    markdownlint --fix timed out{NC}")
    except FileNotFoundError:
        print(f"{YELLOW}    markdownlint command not found{NC}")
        return True, False

    if has_uncommitted_changes(repo_root):
        files_changed = True

    # Final check (without --fix)
    print(f"{BLUE}    markdownlint verify...{NC}")
    try:
        result = subprocess.run(
            lint_cmd + file_paths,
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0 and result.stdout:
            # Show first few issues
            lines = result.stdout.strip().split("\n")[:5]
            for line in lines:
                print(f"{YELLOW}    {line}{NC}")
            if len(result.stdout.strip().split("\n")) > 5:
                print(f"{YELLOW}    ... and more{NC}")
        return result.returncode == 0, files_changed
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    markdownlint verify timed out{NC}")
        return True, files_changed
    except FileNotFoundError:
        return True, files_changed


def lint_json(repo_root: Path, files: list[Path]) -> tuple[bool, bool]:
    """Lint and optionally format JSON files.

    Uses Python json module for validation (always available).
    Uses prettier for formatting if available (bun x / npx / global).

    Returns:
        (success, files_changed)
    """
    files_changed = False
    all_valid = True

    if not files:
        return True, False

    # Step 1: Validate JSON syntax with Python
    print(f"{BLUE}    json.load() validation...{NC}")
    invalid_files = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fp:
                json.load(fp)
        except json.JSONDecodeError as e:
            invalid_files.append((f, str(e)))
        except UnicodeDecodeError as e:
            invalid_files.append((f, f"Binary/encoding error: {e}"))
        except OSError as e:
            invalid_files.append((f, f"I/O error: {e}"))

    if invalid_files:
        all_valid = False
        for f, err in invalid_files[:5]:
            print(f"{RED}    {f.name}: {err[:80]}{NC}")
        if len(invalid_files) > 5:
            print(f"{RED}    ... and {len(invalid_files) - 5} more{NC}")

    # Step 2: Format with prettier if available
    if shutil.which("bun"):
        format_cmd = ["bun", "x", "prettier"]
    elif shutil.which("npx"):
        format_cmd = ["npx", "prettier"]
    elif shutil.which("prettier"):
        format_cmd = ["prettier"]
    else:
        # No formatter available, skip formatting
        return all_valid, files_changed

    # Convert paths to strings
    file_paths = [str(f) for f in files]

    print(f"{BLUE}    prettier --write (JSON)...{NC}")
    try:
        result = subprocess.run(
            format_cmd + ["--write", "--parser", "json"] + file_paths,
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    prettier timed out{NC}")
    except FileNotFoundError:
        pass  # Formatter not available

    if has_uncommitted_changes(repo_root):
        files_changed = True

    return all_valid, files_changed


def lint_yaml(repo_root: Path, files: list[Path]) -> tuple[bool, bool]:
    """Lint YAML files with yamllint.

    Returns:
        (success, files_changed)
    """
    files_changed = False  # yamllint doesn't auto-fix

    if not files:
        return True, False

    if not shutil.which("yamllint"):
        print(f"{YELLOW}    yamllint not available, skipping{NC}")
        return True, False

    # Convert paths to strings
    file_paths = [str(f) for f in files]

    # Run yamllint with relaxed config (warnings for style, errors for syntax)
    print(f"{BLUE}    yamllint...{NC}")
    try:
        # Use relaxed preset for less strict checking
        result = subprocess.run(
            ["yamllint", "-d", "relaxed", "--format", "parsable"] + file_paths,
            cwd=repo_root,
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            # Show first few issues
            lines = result.stdout.strip().split("\n")[:5] if result.stdout else []
            for line in lines:
                # parsable format: file:line:col: [error/warning] message
                if "[error]" in line:
                    print(f"{RED}    {line}{NC}")
                else:
                    print(f"{YELLOW}    {line}{NC}")
            total_lines = len(result.stdout.strip().split("\n")) if result.stdout else 0
            if total_lines > 5:
                print(f"{YELLOW}    ... and {total_lines - 5} more{NC}")

            # Only fail on errors, not warnings
            if "[error]" in (result.stdout or ""):
                return False, files_changed

        return True, files_changed
    except subprocess.TimeoutExpired:
        print(f"{YELLOW}    yamllint timed out{NC}")
        return True, files_changed
    except FileNotFoundError:
        print(f"{YELLOW}    yamllint not found{NC}")
        return True, files_changed


def run_linting(repo_root: Path) -> tuple[bool, bool]:
    """Detect languages and run appropriate linters with auto-fix.

    Returns:
        (success, files_changed): Whether all linting passed and if any files were modified.
    """
    files_changed = False
    all_passed = True

    # Detect languages present
    languages = detect_languages(repo_root)

    if not languages:
        print(f"{YELLOW}  No source files found to lint{NC}")
        return True, False

    print(f"{BLUE}  Detected languages: {', '.join(languages.keys())}{NC}")

    # Lint each detected language
    for lang, files in languages.items():
        print(f"{BLUE}  [{lang.upper()}] ({len(files)} files){NC}")

        # Ensure linter is installed
        if not ensure_linter_installed(lang, repo_root):
            continue

        # Run language-specific linter
        if lang == "python":
            passed, changed = lint_python(repo_root)
        elif lang == "javascript":
            passed, changed = lint_javascript(repo_root)
        elif lang == "shell":
            passed, changed = lint_shell(repo_root, files)
        elif lang == "go":
            passed, changed = lint_go(repo_root)
        elif lang == "rust":
            passed, changed = lint_rust(repo_root)
        elif lang == "markdown":
            passed, changed = lint_markdown(repo_root, files)
        elif lang == "json":
            passed, changed = lint_json(repo_root, files)
        elif lang == "yaml":
            passed, changed = lint_yaml(repo_root, files)
        else:
            continue

        if not passed:
            all_passed = False
        if changed:
            files_changed = True

    return all_passed, files_changed


def commit_auto_fixes(repo_root: Path, iteration: int) -> bool:
    """Stage and commit auto-fixed files.

    Returns:
        True if commit was successful, False otherwise.
    """
    print(f"{BLUE}  Staging auto-fixed files...{NC}")

    # Stage all modified tracked files (not untracked)
    result = subprocess.run(
        ["git", "add", "-u"],
        cwd=repo_root,
        capture_output=True, text=True, timeout=30
    )

    if result.returncode != 0:
        print(f"{RED}  ✘ Failed to stage files: {result.stderr}{NC}")
        return False

    # Check if there's anything to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_root,
        capture_output=True, timeout=30
    )

    if result.returncode == 0:
        # Nothing to commit
        return True

    # Commit the auto-fixes
    commit_msg = f"chore: Auto-fix lint/format issues (iteration {iteration})"
    print(f"{BLUE}  Committing: {commit_msg}{NC}")

    result = subprocess.run(
        ["git", "commit", "-m", commit_msg, "--no-verify"],
        cwd=repo_root,
        capture_output=True, text=True, timeout=30
    )

    if result.returncode != 0:
        print(f"{RED}  ✘ Failed to commit: {result.stderr}{NC}")
        return False

    print(f"{GREEN}  ✔ Auto-fix commit created{NC}")
    return True


def validate_plugin(plugin_path: Path, validator: Path) -> bool:
    """Validate a single plugin."""
    result = subprocess.run(
        ["python3", str(validator), str(plugin_path)],
        capture_output=True, timeout=120
    )
    return result.returncode == 0


def run_validation_cycle(repo_root: Path, validator: Path) -> list[str]:
    """Run full validation and return list of plugins with issues."""
    issues = []

    # Detect project type
    marketplace_json = repo_root / ".claude-plugin" / "marketplace.json"
    plugin_json = repo_root / ".claude-plugin" / "plugin.json"

    if marketplace_json.exists():
        # Marketplace - validate all plugins
        print(f"{BLUE}  Validating marketplace plugins...{NC}")
        import json
        with open(marketplace_json) as f:
            data = json.load(f)

        for plugin in data.get("plugins", []):
            name = plugin.get("name", "unknown")
            source = plugin.get("source", f"./{name}")
            plugin_path = repo_root / source.lstrip("./")

            print(f"{BLUE}    {name}...{NC}", end=" ", flush=True)
            if plugin_path.exists():
                if validate_plugin(plugin_path, validator):
                    print(f"{GREEN}✔{NC}")
                else:
                    print(f"{RED}✘{NC}")
                    issues.append(name)
            else:
                print(f"{YELLOW}⚠ not found{NC}")

    elif plugin_json.exists():
        # Single plugin
        print(f"{BLUE}  Validating plugin...{NC}", end=" ", flush=True)
        if validate_plugin(repo_root, validator):
            print(f"{GREEN}✔{NC}")
        else:
            print(f"{RED}✘{NC}")
            issues.append("plugin")

    return issues


def main() -> int:
    print(f"{BOLD}{'=' * 60}{NC}")
    print(f"{BOLD}Pre-Push Validation (with auto-fix loop){NC}")
    print(f"{BOLD}{'=' * 60}{NC}")
    print()

    repo_root = get_repo_root()
    validator = find_validator()

    if not validator:
        print(f"{YELLOW}⚠ Validator not found, skipping validation{NC}")
        return 0

    iteration = 0
    while iteration < MAX_FIX_ITERATIONS:
        iteration += 1
        print(f"{BOLD}--- Iteration {iteration}/{MAX_FIX_ITERATIONS} ---{NC}")

        # Step 1: Run linting with auto-fix
        print(f"{BLUE}[1] Linting and formatting...{NC}")
        lint_passed, files_changed = run_linting(repo_root)

        # Step 2: If files changed, commit the fixes and restart
        if files_changed:
            print(f"{YELLOW}[2] Files modified by auto-fix, committing...{NC}")
            if not commit_auto_fixes(repo_root, iteration):
                print(f"{RED}✘ Failed to commit auto-fixes{NC}")
                return 1

            # Restart the loop to re-validate after commit
            print(f"{BLUE}[3] Restarting validation cycle...{NC}")
            print()
            continue

        # Step 3: Check if there are unfixable lint issues
        # (lint failed but nothing was changed = can't be auto-fixed)
        if not lint_passed:
            print()
            print(f"{BOLD}{'=' * 60}{NC}")
            print(f"{RED}✘ LINT ISSUES CANNOT BE AUTO-FIXED - Push blocked{NC}")
            print(f"{RED}  Run 'ruff check .' to see remaining issues{NC}")
            print(f"{BOLD}{'=' * 60}{NC}")
            return 1

        # Step 4: Run plugin validation
        print(f"{BLUE}[2] Running plugin validation...{NC}")
        issues = run_validation_cycle(repo_root, validator)

        # Step 5: Check validation results
        if not issues:
            # All good!
            print()
            print(f"{BOLD}{'=' * 60}{NC}")
            print(f"{GREEN}✔ VALIDATION PASSED - Push allowed{NC}")
            if iteration > 1:
                print(f"{GREEN}  (Auto-fixed in {iteration - 1} iteration(s)){NC}")
            print(f"{BOLD}{'=' * 60}{NC}")
            return 0

        # Validation failed - these are non-lint issues (schema, structure, etc.)
        print()
        print(f"{BOLD}{'=' * 60}{NC}")
        print(f"{RED}✘ VALIDATION FAILED - Push blocked{NC}")
        print(f"{RED}  Issues in: {', '.join(issues)}{NC}")
        print(f"{RED}  (Not fixable by linting - manual fix required){NC}")
        print(f"{BOLD}{'=' * 60}{NC}")
        return 1

    # Max iterations reached
    print()
    print(f"{BOLD}{'=' * 60}{NC}")
    print(f"{RED}✘ MAX ITERATIONS REACHED ({MAX_FIX_ITERATIONS}) - Push blocked{NC}")
    print(f"{RED}  Manual intervention required{NC}")
    print(f"{BOLD}{'=' * 60}{NC}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
'''

POST_REWRITE_HOOK = '''#!/usr/bin/env python3
"""post-rewrite hook: Update CHANGELOG.md after rebase/amend completes.

Fires ONCE after rebase or amend, avoiding mid-rebase conflicts.
"""

import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    operation = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    if not shutil.which("git-cliff"):
        return 0

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=30
    )
    repo_root = Path(result.stdout.strip())

    cliff_toml = repo_root / "cliff.toml"
    if not cliff_toml.exists():
        return 0

    print(f"[post-rewrite] Regenerating CHANGELOG.md after {operation}...")

    result = subprocess.run(
        ["git-cliff", "-o", "CHANGELOG.md"],
        cwd=repo_root,
        capture_output=True, text=True, timeout=60
    )

    if result.returncode != 0:
        print(f"Warning: git-cliff failed: {result.stderr}")
        return 0

    status = subprocess.run(
        ["git", "diff", "--quiet", "CHANGELOG.md"],
        cwd=repo_root, capture_output=True, timeout=30
    )

    if status.returncode != 0:
        print("CHANGELOG.md updated - remember to commit it!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

POST_MERGE_HOOK = '''#!/usr/bin/env python3
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
        capture_output=True, text=True, timeout=30
    )
    repo_root = Path(result.stdout.strip())

    cliff_toml = repo_root / "cliff.toml"
    if not cliff_toml.exists():
        return 0

    print("[post-merge] Regenerating CHANGELOG.md...")

    result = subprocess.run(
        ["git-cliff", "-o", "CHANGELOG.md"],
        cwd=repo_root,
        capture_output=True, text=True, timeout=60
    )

    if result.returncode != 0:
        print(f"Warning: git-cliff failed: {result.stderr}")
        return 0

    status = subprocess.run(
        ["git", "diff", "--quiet", "CHANGELOG.md"],
        cwd=repo_root, capture_output=True, timeout=30
    )

    if status.returncode != 0:
        print("CHANGELOG.md updated - remember to commit it!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

# =============================================================================
# CONFIG TEMPLATES
# =============================================================================

CLIFF_TOML = '''# git-cliff configuration for changelog generation
# https://git-cliff.org

[changelog]
header = """
# Changelog

All notable changes to this project will be documented in this file.
"""
body = """
{% if version %}\\
    ## [{{ version | trim_start_matches(pat="v") }}] - {{ timestamp | date(format="%Y-%m-%d") }}
{% else %}\\
    ## [unreleased]
{% endif %}\\
{% for group, commits in commits | group_by(attribute="group") %}
    ### {{ group | striptags | trim | upper_first }}
    {% for commit in commits %}
        - {% if commit.scope %}*({{ commit.scope }})* {% endif %}\\
            {{ commit.message | upper_first }}\\
    {% endfor %}
{% endfor %}
"""
footer = """
"""
trim = true

[git]
conventional_commits = true
filter_unconventional = true
split_commits = false
commit_parsers = [
    { message = "^feat", group = "Features" },
    { message = "^fix", group = "Bug Fixes" },
    { message = "^doc", group = "Documentation" },
    { message = "^perf", group = "Performance" },
    { message = "^refactor", group = "Refactor" },
    { message = "^style", group = "Styling" },
    { message = "^test", group = "Testing" },
    { message = "^chore\\\\(release\\\\)", skip = true },
    { message = "^chore\\\\(deps.*\\\\)", skip = true },
    { message = "^chore|^ci", group = "Miscellaneous Tasks" },
    { body = ".*security", group = "Security" },
]
filter_commits = false
tag_pattern = "v[0-9].*"
'''

GITHUB_WORKFLOW = """name: Plugin Validation

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install ruff mypy pyyaml types-PyYAML

      - name: Find validator
        id: find-validator
        run: |
          if [ -f "scripts/validate_plugin.py" ]; then
            echo "validator=scripts/validate_plugin.py" >> $GITHUB_OUTPUT
          elif [ -f "claude-plugins-validation/scripts/validate_plugin.py" ]; then
            echo "validator=claude-plugins-validation/scripts/validate_plugin.py" >> $GITHUB_OUTPUT
          else
            echo "validator=" >> $GITHUB_OUTPUT
          fi

      - name: Validate plugin(s)
        if: steps.find-validator.outputs.validator != ''
        run: |
          python ${{ steps.find-validator.outputs.validator }} . --verbose
          exit_code=$?
          # Exit codes: 0=pass, 1=critical, 2=major, 3=minor (warnings only)
          # Allow exit code 3 (minor issues) to pass CI
          if [ $exit_code -eq 0 ] || [ $exit_code -eq 3 ]; then
            echo "✓ Validation passed (exit code: $exit_code)"
            exit 0
          else
            echo "✘ Validation failed (exit code: $exit_code)"
            exit $exit_code
          fi

      - name: Lint Python files
        run: |
          ruff check . --select=E,F,W --ignore=E501 || true
"""

GITIGNORE_ADDITIONS = """
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
venv/
ENV/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Build artifacts
dist/
build/
*.egg-info/

# Logs
*.log
logs/

# Dev folders
docs_dev/
scripts_dev/
tests_dev/
"""


# =============================================================================
# PIPELINE SETUP CLASS
# =============================================================================


class PipelineSetup:
    """Handles pipeline setup and validation for Claude Code plugins."""

    def __init__(self, project_path: Path, dry_run: bool = False, verbose: bool = False):
        self.project_path = project_path.resolve()
        self.dry_run = dry_run
        self.verbose = verbose
        self.status = PipelineStatus(project_type=ProjectType.UNKNOWN, project_path=self.project_path)

    def detect_project_type(self) -> ProjectType:
        """Detect what type of project this is."""
        marketplace_json = self.project_path / ".claude-plugin" / "marketplace.json"
        plugin_json = self.project_path / ".claude-plugin" / "plugin.json"

        # Check if this is a submodule
        git_file = self.project_path / ".git"
        is_submodule = git_file.is_file()  # .git is a file in submodules

        if marketplace_json.exists():
            self.status.project_type = ProjectType.MARKETPLACE
            # Find submodules
            self._detect_submodules()
        elif plugin_json.exists():
            if is_submodule:
                self.status.project_type = ProjectType.PLUGIN_IN_MARKETPLACE
            else:
                self.status.project_type = ProjectType.PLUGIN
        else:
            self.status.project_type = ProjectType.UNKNOWN

        return self.status.project_type

    def _detect_submodules(self) -> None:
        """Detect git submodules in the project."""
        gitmodules = self.project_path / ".gitmodules"
        if not gitmodules.exists():
            return

        try:
            config = configparser.ConfigParser()
            config.read(gitmodules)

            for section in config.sections():
                if section.startswith("submodule "):
                    name = section.replace("submodule ", "").strip('"')
                    path = config.get(section, "path", fallback=name)
                    if (self.project_path / path).exists():
                        self.status.submodules.append(path)
        except (configparser.Error, OSError) as e:
            # If we can't parse .gitmodules, just skip submodule detection
            if self.verbose:
                print(f"{YELLOW}Warning: Could not parse .gitmodules: {e}{NC}")

    def validate(self) -> PipelineStatus:
        """Validate the current pipeline setup."""
        self.detect_project_type()

        if self.status.project_type == ProjectType.UNKNOWN:
            self.status.issues.append(
                PipelineIssue(
                    level=IssueLevel.CRITICAL,
                    component="project",
                    message=(
                        "Not a valid plugin or marketplace (missing .claude-plugin/plugin.json or marketplace.json)"
                    ),
                    fix_available=False,
                )
            )
            return self.status

        # Check git repository
        if not (self.project_path / ".git").exists():
            self.status.issues.append(
                PipelineIssue(
                    level=IssueLevel.CRITICAL,
                    component="git",
                    message="Not a git repository",
                    fix_available=True,
                    fix_description="Initialize git repository",
                )
            )

        # Check hooks
        self._validate_hooks()

        # Check config files
        self._validate_config_files()

        # Check submodule hooks if marketplace
        if self.status.project_type == ProjectType.MARKETPLACE:
            self._validate_submodule_hooks()

        return self.status

    def _get_hooks_dir(self) -> Path:
        """Get the hooks directory for this project."""
        git_path = self.project_path / ".git"

        if git_path.is_file():
            # Submodule - read the gitdir from the file
            try:
                content = git_path.read_text(encoding="utf-8").strip()
                if content.startswith("gitdir: "):
                    git_dir = Path(content[8:])
                    if not git_dir.is_absolute():
                        git_dir = self.project_path / git_dir
                    return git_dir.resolve() / "hooks"
                else:
                    # Invalid .git file format - fall back to regular path
                    if self.verbose:
                        print(f"{YELLOW}Warning: .git file has unexpected format{NC}")
            except (OSError, UnicodeDecodeError) as e:
                # If we can't read .git file, fall back to regular path
                if self.verbose:
                    print(f"{YELLOW}Warning: Could not read .git file: {e}{NC}")

        return git_path / "hooks"

    def _validate_hooks(self) -> None:
        """Validate git hooks are installed correctly."""
        hooks_dir = self._get_hooks_dir()

        required_hooks = {
            "pre-commit": "Lint and validate before commit",
            "pre-push": "Full validation before push",
            "post-rewrite": "Changelog after rebase/amend",
            "post-merge": "Changelog after merge",
        }

        # Check for problematic post-commit hook
        post_commit = hooks_dir / "post-commit"
        if post_commit.exists():
            self.status.issues.append(
                PipelineIssue(
                    level=IssueLevel.MAJOR,
                    component="hooks",
                    message="post-commit hook exists (causes rebase conflicts)",
                    fix_available=True,
                    fix_description="Remove post-commit hook, use post-rewrite instead",
                )
            )

        for hook_name, description in required_hooks.items():
            hook_path = hooks_dir / hook_name
            self.status.hooks_installed[hook_name] = hook_path.exists()

            if not hook_path.exists():
                self.status.issues.append(
                    PipelineIssue(
                        level=IssueLevel.MAJOR,
                        component="hooks",
                        message=f"Missing {hook_name} hook ({description})",
                        fix_available=True,
                        fix_description=f"Install {hook_name} hook",
                    )
                )
            elif not os.access(hook_path, os.X_OK):
                self.status.issues.append(
                    PipelineIssue(
                        level=IssueLevel.MAJOR,
                        component="hooks",
                        message=f"{hook_name} hook is not executable",
                        fix_available=True,
                        fix_description=f"Make {hook_name} hook executable",
                    )
                )

    def _validate_config_files(self) -> None:
        """Validate configuration files exist."""
        config_files = {
            "cliff.toml": ("Changelog generation config", IssueLevel.MINOR),
            ".gitignore": ("Git ignore patterns", IssueLevel.MINOR),
        }

        for filename, (description, level) in config_files.items():
            file_path = self.project_path / filename
            self.status.config_files[filename] = file_path.exists()

            if not file_path.exists():
                self.status.issues.append(
                    PipelineIssue(
                        level=level,
                        component="config",
                        message=f"Missing {filename} ({description})",
                        fix_available=True,
                        fix_description=f"Create {filename}",
                    )
                )

        # Check for GitHub workflow
        workflow_dir = self.project_path / ".github" / "workflows"
        has_validation_workflow = False
        if workflow_dir.exists():
            for wf in workflow_dir.glob("*.yml"):
                try:
                    content = wf.read_text(encoding="utf-8")
                    if "validate" in content.lower() or "plugin" in content.lower():
                        has_validation_workflow = True
                        break
                except (OSError, UnicodeDecodeError) as e:
                    if self.verbose:
                        print(f"{YELLOW}Warning: Could not read {wf.name}: {e}{NC}")

        self.status.config_files["github_workflow"] = has_validation_workflow
        if not has_validation_workflow:
            self.status.issues.append(
                PipelineIssue(
                    level=IssueLevel.MINOR,
                    component="ci",
                    message="No GitHub Actions validation workflow found",
                    fix_available=True,
                    fix_description="Create .github/workflows/validate.yml",
                )
            )

    def _validate_submodule_hooks(self) -> None:
        """Validate hooks in submodules."""
        for submodule in self.status.submodules:
            # Hooks for submodules are stored in .git/modules/<submodule>/hooks/
            hooks_dir = self.project_path / ".git" / "modules" / submodule / "hooks"

            if not hooks_dir.exists():
                self.status.issues.append(
                    PipelineIssue(
                        level=IssueLevel.MINOR,
                        component="submodules",
                        message=f"Submodule {submodule} hooks directory not found",
                        fix_available=False,
                    )
                )
                continue

            # Check for problematic post-commit
            if (hooks_dir / "post-commit").exists():
                self.status.issues.append(
                    PipelineIssue(
                        level=IssueLevel.MAJOR,
                        component="submodules",
                        message=f"Submodule {submodule} has post-commit hook (causes rebase conflicts)",
                        fix_available=True,
                        fix_description=f"Remove post-commit, install post-rewrite for {submodule}",
                    )
                )

            # Check for required hooks
            for hook in ["post-rewrite", "post-merge"]:
                if not (hooks_dir / hook).exists():
                    self.status.issues.append(
                        PipelineIssue(
                            level=IssueLevel.MINOR,
                            component="submodules",
                            message=f"Submodule {submodule} missing {hook} hook",
                            fix_available=True,
                            fix_description=f"Install {hook} hook for {submodule}",
                        )
                    )

    def fix(self) -> int:
        """Fix all fixable issues."""
        if self.status.project_type == ProjectType.UNKNOWN:
            print(f"{RED}Cannot fix: not a valid plugin/marketplace project{NC}")
            return 1

        fixed_count = 0

        # Fix git hooks
        fixed_count += self._fix_hooks()

        # Fix config files
        fixed_count += self._fix_config_files()

        # Fix submodule hooks
        if self.status.project_type == ProjectType.MARKETPLACE:
            fixed_count += self._fix_submodule_hooks()

        return fixed_count

    def _fix_hooks(self) -> int:
        """Install/fix git hooks."""
        hooks_dir = self._get_hooks_dir()
        hooks_dir.mkdir(parents=True, exist_ok=True)

        fixed = 0

        # Remove problematic post-commit hook
        post_commit = hooks_dir / "post-commit"
        if post_commit.exists():
            if self.dry_run:
                print(f"{YELLOW}Would remove:{NC} {post_commit}")
            else:
                post_commit.unlink()
                print(f"{GREEN}✓{NC} Removed post-commit hook")
            fixed += 1

        # Install required hooks
        hooks = {
            "pre-commit": PRE_COMMIT_HOOK,
            "pre-push": PRE_PUSH_HOOK,
            "post-rewrite": POST_REWRITE_HOOK,
            "post-merge": POST_MERGE_HOOK,
        }

        for name, content in hooks.items():
            hook_path = hooks_dir / name
            if not hook_path.exists() or not self._hook_is_valid(hook_path):
                if self.dry_run:
                    print(f"{YELLOW}Would install:{NC} {name} hook")
                else:
                    try:
                        hook_path.write_text(content, encoding="utf-8")
                        hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        print(f"{GREEN}✓{NC} Installed {name} hook")
                        fixed += 1
                    except OSError as e:
                        print(f"{RED}✘{NC} Failed to install {name} hook: {e}")
                        self.status.issues.append(
                            PipelineIssue(
                                level=IssueLevel.CRITICAL,
                                component="hooks",
                                message=f"Permission denied installing {name} hook: {e}",
                                fix_available=False,
                            )
                        )

        return fixed

    def _hook_is_valid(self, hook_path: Path) -> bool:
        """Check if a hook file is valid (executable and has content)."""
        if not hook_path.exists():
            return False
        if not os.access(hook_path, os.X_OK):
            return False
        if hook_path.stat().st_size < 100:
            return False
        return True

    def _fix_config_files(self) -> int:
        """Create missing config files."""
        fixed = 0

        # cliff.toml
        cliff_toml = self.project_path / "cliff.toml"
        if not cliff_toml.exists():
            if self.dry_run:
                print(f"{YELLOW}Would create:{NC} cliff.toml")
            else:
                cliff_toml.write_text(CLIFF_TOML, encoding="utf-8")
                print(f"{GREEN}✓{NC} Created cliff.toml")
            fixed += 1

        # .gitignore
        gitignore = self.project_path / ".gitignore"
        if not gitignore.exists():
            if self.dry_run:
                print(f"{YELLOW}Would create:{NC} .gitignore")
            else:
                gitignore.write_text(GITIGNORE_ADDITIONS, encoding="utf-8")
                print(f"{GREEN}✓{NC} Created .gitignore")
            fixed += 1
        else:
            # Check if it needs additions - check for all expected patterns
            try:
                content = gitignore.read_text(encoding="utf-8")
                # Check for multiple markers to avoid duplicating content
                needs_update = not all(marker in content for marker in ["__pycache__", ".mypy_cache", "docs_dev/"])
                if needs_update:
                    if self.dry_run:
                        print(f"{YELLOW}Would update:{NC} .gitignore")
                    else:
                        with open(gitignore, "a", encoding="utf-8") as f:
                            f.write("\n" + GITIGNORE_ADDITIONS)
                        print(f"{GREEN}✓{NC} Updated .gitignore")
                    fixed += 1
            except (OSError, UnicodeDecodeError) as e:
                if self.verbose:
                    print(f"{YELLOW}Warning: Could not read .gitignore: {e}{NC}")

        # GitHub workflow
        workflow_dir = self.project_path / ".github" / "workflows"
        workflow_file = workflow_dir / "validate.yml"
        if not workflow_file.exists():
            if self.dry_run:
                print(f"{YELLOW}Would create:{NC} .github/workflows/validate.yml")
            else:
                workflow_dir.mkdir(parents=True, exist_ok=True)
                workflow_file.write_text(GITHUB_WORKFLOW, encoding="utf-8")
                print(f"{GREEN}✓{NC} Created .github/workflows/validate.yml")
            fixed += 1

        return fixed

    def _fix_submodule_hooks(self) -> int:
        """Fix hooks in submodules."""
        fixed = 0

        for submodule in self.status.submodules:
            hooks_dir = self.project_path / ".git" / "modules" / submodule / "hooks"
            if not hooks_dir.exists():
                continue

            # Remove post-commit
            post_commit = hooks_dir / "post-commit"
            if post_commit.exists():
                if self.dry_run:
                    print(f"{YELLOW}Would remove:{NC} {submodule}/post-commit")
                else:
                    try:
                        post_commit.unlink()
                        print(f"{GREEN}✓{NC} Removed {submodule}/post-commit hook")
                        fixed += 1
                    except OSError as e:
                        print(f"{RED}✘{NC} Failed to remove {submodule}/post-commit: {e}")

            # Install post-rewrite and post-merge
            for name, content in [("post-rewrite", POST_REWRITE_HOOK), ("post-merge", POST_MERGE_HOOK)]:
                hook_path = hooks_dir / name
                if not hook_path.exists():
                    if self.dry_run:
                        print(f"{YELLOW}Would install:{NC} {submodule}/{name}")
                    else:
                        try:
                            hook_path.write_text(content, encoding="utf-8")
                            hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                            print(f"{GREEN}✓{NC} Installed {submodule}/{name} hook")
                            fixed += 1
                        except OSError as e:
                            print(f"{RED}✘{NC} Failed to install {submodule}/{name}: {e}")

        return fixed


# =============================================================================
# CLI
# =============================================================================


def print_status(status: PipelineStatus) -> None:
    """Print pipeline status in a formatted way."""
    print(f"\n{BOLD}Pipeline Status{NC}")
    print("=" * 50)
    print(f"Project: {status.project_path}")
    print(f"Type: {status.project_type.value}")

    if status.submodules:
        print(f"Submodules: {', '.join(status.submodules)}")

    print(f"\n{BOLD}Hooks{NC}")
    for hook, installed in status.hooks_installed.items():
        icon = f"{GREEN}✓{NC}" if installed else f"{RED}✘{NC}"
        print(f"  {icon} {hook}")

    print(f"\n{BOLD}Config Files{NC}")
    for config, exists in status.config_files.items():
        icon = f"{GREEN}✓{NC}" if exists else f"{RED}✘{NC}"
        print(f"  {icon} {config}")

    if status.issues:
        print(f"\n{BOLD}Issues{NC}")
        for issue in status.issues:
            if issue.level == IssueLevel.CRITICAL:
                icon = f"{RED}✘{NC}"
            elif issue.level == IssueLevel.MAJOR:
                icon = f"{YELLOW}⚠{NC}"
            else:
                icon = f"{BLUE}ℹ{NC}"

            fix_note = " (fixable)" if issue.fix_available else ""
            print(f"  {icon} [{issue.component}] {issue.message}{fix_note}")

    print()
    print(
        f"Summary: {RED}{status.critical_count} critical{NC}, "
        f"{YELLOW}{status.major_count} major{NC}, "
        f"{BLUE}{status.minor_count} minor{NC}"
    )

    if status.is_valid:
        print(f"\n{GREEN}Pipeline is valid{NC}")
    else:
        print(f"\n{RED}Pipeline has issues that need fixing{NC}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Setup and validate Claude Code plugin development pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/project              # Auto-detect and setup
  %(prog)s /path/to/project --validate   # Validate only
  %(prog)s /path/to/project --fix        # Fix all issues
  %(prog)s /path/to/project --dry-run    # Show what would be done
        """,
    )

    parser.add_argument("path", nargs="?", default=".", help="Path to project (default: current directory)")

    parser.add_argument(
        "--type", choices=["marketplace", "plugin"], help="Force project type (auto-detected by default)"
    )

    parser.add_argument("--validate", "-v", action="store_true", help="Validate pipeline only (don't fix)")

    parser.add_argument("--fix", "-f", action="store_true", help="Fix all fixable issues")

    parser.add_argument("--dry-run", "-n", action="store_true", help="Show what would be done without making changes")

    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output (for CI)")

    parser.add_argument("--verbose", action="store_true", help="Show detailed output including warnings")

    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    project_path = Path(args.path).resolve()

    if not project_path.exists():
        print(f"{RED}Error: Path does not exist: {project_path}{NC}")
        return 1

    setup = PipelineSetup(project_path, dry_run=args.dry_run, verbose=args.verbose)

    # Validate
    status = setup.validate()

    # JSON output
    if args.json:
        output = {
            "project_path": str(status.project_path),
            "project_type": status.project_type.value,
            "is_valid": status.is_valid,
            "hooks": status.hooks_installed,
            "config_files": status.config_files,
            "submodules": status.submodules,
            "issues": [
                {
                    "level": i.level.value,
                    "component": i.component,
                    "message": i.message,
                    "fix_available": i.fix_available,
                }
                for i in status.issues
            ],
            "summary": {"critical": status.critical_count, "major": status.major_count, "minor": status.minor_count},
        }
        print(json.dumps(output, indent=2))
        return 0 if status.is_valid else 1

    # Print status
    if not args.quiet:
        print(f"{CYAN}{'=' * 60}{NC}")
        print(f"{CYAN}Claude Code Plugin Pipeline Setup{NC}")
        print(f"{CYAN}{'=' * 60}{NC}")
        print_status(status)

    # Fix if requested (works with or without --validate)
    if args.fix:
        # Check if there are any fixable issues (including minor ones)
        fixable_issues = [i for i in status.issues if i.fix_available]
        if not fixable_issues and not args.dry_run:
            print(f"{GREEN}No fixes needed - all issues require manual intervention{NC}")
        else:
            print(f"\n{BOLD}Fixing issues...{NC}")
            fixed = setup.fix()

            if args.dry_run:
                print(f"\n{YELLOW}Dry run - no changes made{NC}")
            else:
                print(f"\n{GREEN}Fixed {fixed} issue(s){NC}")

                # Re-validate
                setup.status = PipelineStatus(project_type=ProjectType.UNKNOWN, project_path=project_path)
                status = setup.validate()

                if status.is_valid and not status.issues:
                    print(f"{GREEN}Pipeline is now fully configured{NC}")
                elif status.is_valid:
                    print(f"{GREEN}Pipeline is valid (some minor issues remain){NC}")
                else:
                    print(f"{YELLOW}Some issues remain - manual intervention needed{NC}")

    # Exit code
    if args.validate or args.fix:
        return 0 if status.is_valid else 1

    # Default: setup (fix if needed)
    if not status.is_valid:
        print(f"\n{BOLD}Setting up pipeline...{NC}")
        setup.fix()
        print(f"\n{GREEN}Pipeline setup complete{NC}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
