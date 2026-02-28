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
"""pre-commit hook: Sensitive data check before commit.

SKIPS during rebase/cherry-pick/merge to prevent conflicts.
Linting and JSON validation are deferred to pre-push.
"""

import re
import subprocess
import sys
from pathlib import Path

if sys.stdout.isatty():
    RED = "\\033[0;31m"
    GREEN = "\\033[0;32m"
    YELLOW = "\\033[1;33m"
    BLUE = "\\033[0;34m"
    NC = "\\033[0m"
else:
    RED = GREEN = YELLOW = BLUE = NC = ""


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

    # Check for sensitive data in staged diff
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
        print(f"{RED}✘ review required{NC}")
        for w in warnings[:3]:
            print(f"  {RED}{w}{NC}")
        failed = True

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
"""pre-push hook: Lint and validate before pushing.

Thin wrapper that delegates to scripts/lint_files.py and
scripts/validate_plugin.py — the single source of truth.
"""

import os
import subprocess
import sys

if sys.stdout.isatty():
    RED = "\\033[0;31m"
    GREEN = "\\033[0;32m"
    YELLOW = "\\033[1;33m"
    BOLD = "\\033[1m"
    NC = "\\033[0m"
else:
    RED = GREEN = YELLOW = BOLD = NC = ""


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
    # Determine repo root
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

    # Step 1: Read-only linting
    lint_script = os.path.join(scripts_dir, "lint_files.py")
    if os.path.isfile(lint_script):
        print(f"{BOLD}Running file linting (read-only)...{NC}")
        result = subprocess.run([python, lint_script, repo_root])
        if result.returncode != 0:
            print(f"{RED}Linting failed — push blocked{NC}")
            overall = 1

    # Step 2: Plugin validation
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
          python3 -m pip install --upgrade pip
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

      - name: Lint all source files (read-only)
        run: python3 scripts/lint_files.py .

      - name: Validate plugin(s)
        if: steps.find-validator.outputs.validator != ''
        run: |
          set +e
          python3 ${{ steps.find-validator.outputs.validator }} . --verbose
          exit_code=$?
          set -e
          # Exit codes: 0=pass, 1=critical, 2=major, 3=minor
          # Strict mode: ALL non-zero exit codes block the pipeline
          if [ $exit_code -eq 0 ]; then
            echo "✓ Validation passed"
            exit 0
          else
            echo "✘ Validation failed (exit code: $exit_code)"
            exit $exit_code
          fi

      - name: Lint Python files
        run: |
          ruff check . --exclude .venv --select=E,F,W --ignore=E501 || true
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

# Type checking
.mypy_cache/

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
