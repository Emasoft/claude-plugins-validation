#!/usr/bin/env python3
"""Generate a complete Claude Code plugin repository scaffold.

Creates all standard files for a plugin repo: manifest, pyproject.toml,
.gitignore, README with badge markers, LICENSE, cliff.toml, CI/CD workflows,
git hooks, publish script, and empty component directories.

Usage:
    uv run scripts/generate_plugin_repo.py <target-dir> \\
      --name <plugin-name> --description <desc> \\
      --author <name> --author-email <email> \\
      --license MIT --python-version 3.12 \\
      --github-owner <owner> --marketplace <mkt-name> \\
      [--dry-run]
"""

import argparse
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

# -- ANSI colors (disabled when NO_COLOR is set or stdout is not a tty) ------


def _colors_supported() -> bool:
    """Return True only when the terminal supports ANSI escape sequences."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except (AttributeError, OSError):
            pass
        return bool(os.environ.get("WT_SESSION") or os.environ.get("ANSICON"))
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_USE_COLOR = _colors_supported()

RED = "\033[0;31m" if _USE_COLOR else ""
GREEN = "\033[0;32m" if _USE_COLOR else ""
YELLOW = "\033[1;33m" if _USE_COLOR else ""
BLUE = "\033[0;34m" if _USE_COLOR else ""
BOLD = "\033[1m" if _USE_COLOR else ""
NC = "\033[0m" if _USE_COLOR else ""


# =============================================================================
# DATA CLASS
# =============================================================================


@dataclass
class PluginParams:
    """All parameters needed to scaffold a plugin repository."""

    name: str
    description: str
    author: str
    author_email: str
    license: str = "MIT"
    python_version: str = "3.12"
    github_owner: str = ""
    marketplace: str = ""
    version: str = "0.1.0"

    @property
    def repo_name(self) -> str:
        """GitHub repo name — defaults to plugin name."""
        return self.name

    @property
    def github_url(self) -> str:
        """Full GitHub URL for the plugin."""
        return f"https://github.com/{self.github_owner}/{self.repo_name}"


# =============================================================================
# TEMPLATE GENERATORS
# =============================================================================


def gen_plugin_json(p: PluginParams) -> str:
    """Generate .claude-plugin/plugin.json manifest content."""
    manifest = {
        "name": p.name,
        "version": p.version,
        "description": p.description,
        "author": {
            "name": p.author,
            "email": p.author_email,
        },
        "homepage": p.github_url,
        "repository": p.github_url,
        "license": p.license,
        "keywords": [],
    }
    return json.dumps(manifest, indent=2) + "\n"


def gen_pyproject_toml(p: PluginParams) -> str:
    """Generate pyproject.toml with hatchling build system and ruff config."""
    return f"""[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["scripts"]

[project]
name = "{p.name}"
version = "{p.version}"
description = "{p.description}"
readme = "README.md"
requires-python = ">={p.python_version}"
dependencies = []

[project.optional-dependencies]
dev = [
    "mypy>=1.19.1",
    "pyyaml>=6.0",
    "pytest>=8.0.0",
    "pytest-cov>=4.1.0",
    "ruff>=0.14.14",
]

[tool.ruff]
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/*.py" = ["E402"]

[tool.mypy]
python_version = "{p.python_version}"
warn_return_any = true
warn_unused_configs = true

[tool.pyright]
pythonVersion = "{p.python_version}"
extraPaths = ["scripts", "tests"]
reportMissingImports = "warning"
typeCheckingMode = "basic"
"""


def gen_python_version(p: PluginParams) -> str:
    """Generate .python-version file."""
    return f"{p.python_version}\n"


def gen_gitignore(p: PluginParams) -> str:
    """Generate comprehensive .gitignore for a Claude Code plugin repo."""
    _ = p  # unused but kept for consistent signature
    return """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
.eggs/
dist/
build/
.coverage
.venv/
venv/
.pytest_cache/

# Type checking
.mypy_cache/
.dmypy.json
dmypy.json

# Linting
.ruff_cache/

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Environment
.env
.env.*

# Dev folders (NEVER PUBLISH - development artifacts only)
# Wildcard pattern catches all: docs_dev, scripts_dev, tests_dev, samples_dev,
# examples_dev, downloads_dev, libs_dev, builds_dev, etc.
*_dev/

# Node
node_modules/

# Claude Code
.claude/
llm_externalizer_output/
.tldr/

# Mega-Linter
megalinter-reports/
mega-linter.log

# Rust (remove Cargo.lock line for binary plugins)
target/
Cargo.lock
"""


def gen_readme(p: PluginParams) -> str:
    """Generate README.md with badges, installation, usage, and development sections."""
    owner = p.github_owner
    repo = p.repo_name
    # Skip badge URLs if github_owner is empty (avoids broken // in URLs)
    if owner:
        badges = (
            f"[![CI](https://github.com/{owner}/{repo}/actions/workflows/ci.yml/badge.svg)]"
            f"(https://github.com/{owner}/{repo}/actions/workflows/ci.yml)\n"
            f"[![Version](https://img.shields.io/badge/version-{p.version}-blue)]"
            f"(https://github.com/{owner}/{repo})\n"
            f"[![License](https://img.shields.io/badge/license-{p.license}-green)](LICENSE)\n"
            f"[![Validation](https://github.com/{owner}/{repo}/actions/workflows/validate.yml/badge.svg)]"
            f"(https://github.com/{owner}/{repo}/actions/workflows/validate.yml)"
        )
    else:
        badges = "<!-- Badges will appear here once github_owner is set -->"
    return f"""# {p.name}

<!--BADGES-START-->
{badges}
<!--BADGES-END-->

{p.description}

## Installation

### From Marketplace

```bash
claude plugin install {p.name}@{p.marketplace}
```

### From GitHub

```bash
gh repo clone {owner}/{repo}
cd {repo}
uv venv --python {p.python_version}
source .venv/bin/activate
uv pip install -e .
```

### As a Claude Code Plugin

Add to your Claude Code configuration:

```json
{{
  "plugins": [
    "https://github.com/{owner}/{repo}"
  ]
}}
```

## Uninstall

```bash
claude plugin uninstall {p.name}
```

## Update

```bash
claude plugin update {p.name}@{p.marketplace}
```

## Usage

```bash
# Run the plugin
uv run python scripts/main.py --help
```

## Development

### Prerequisites

- Python >= {p.python_version}
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
uv venv --python {p.python_version}
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Testing

```bash
uv run pytest tests/ -v
```

### Linting & Formatting

```bash
uv run ruff check scripts/ tests/
uv run ruff format scripts/ tests/
uv run mypy scripts/
```

## Project Structure

```
{repo}/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── .github/
│   └── workflows/           # CI/CD workflows
├── git-hooks/               # Git hooks (pre-push)
├── scripts/                 # Plugin source code
├── tests/                   # Test suite
├── pyproject.toml           # Project configuration
├── cliff.toml               # Changelog generation config
├── README.md                # This file
├── LICENSE                  # License file
└── .gitignore               # Git ignore rules
```

## Marketplace

This plugin is available on the [{p.marketplace} marketplace](https://github.com/{owner}/{p.marketplace}).

## License

This project is licensed under the {p.license} License. See [LICENSE](LICENSE) for details.

## Author

**{p.author}** - [GitHub](https://github.com/{owner})
"""


def gen_license_mit(p: PluginParams) -> str:
    """Generate MIT license text."""
    return f"""MIT License

Copyright (c) 2025 {p.author}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def gen_cliff_toml(p: PluginParams) -> str:
    """Generate cliff.toml for git-cliff changelog generation."""
    # TOML uses triple-double-quotes (""") for multi-line strings, which collides
    # with Python triple-quoted strings. We inject them via a variable.
    tq = '"""\n'
    body_template = (
        "{% if version %}\\\n"
        '    ## [{{ version | trim_start_matches(pat="v") }}]'
        ' - {{ timestamp | date(format="%Y-%m-%d") }}\n'
        "{% else %}\\\n"
        "    ## [Unreleased]\n"
        "{% endif %}\\\n"
        "{% for group, commits in commits | group_by(attribute="
        '"group") %}\n'
        "    ### {{ group | striptags | trim | upper_first }}\n"
        "    {% for commit in commits %}\n"
        "        - {% if commit.scope %}*({{ commit.scope }})* "
        "{% endif %}\\\n"
        "            {{ commit.message | upper_first }}\\\n"
        "    {% endfor %}\n"
        "{% endfor %}\n"
    )
    lines = [
        "# git-cliff configuration for changelog generation",
        "# https://git-cliff.org",
        "",
        "[changelog]",
    ]
    # Build the TOML content as a list of lines, then join
    # We handle the triple-quoted TOML strings by direct string building
    result = "\n".join(lines) + "\n"
    result += 'header = ' + tq
    result += "# Changelog\n\nAll notable changes to this project will be documented in this file.\n\n"
    result += tq
    result += 'body = ' + tq
    result += body_template
    result += tq
    result += 'footer = ' + tq
    result += "---\n*Generated by [git-cliff](https://git-cliff.org)*\n"
    result += tq
    result += "trim = true\n"
    result += "postprocessors = []\n"
    result += "\n"
    result += "[git]\n"
    result += "conventional_commits = true\n"
    result += "filter_unconventional = true\n"
    result += "split_commits = false\n"
    result += "commit_preprocessors = [\n"
    result += r"  { pattern = '\((\w+\s)?#([0-9]+)\)',"
    result += f' replace = "([#${{2}}](https://github.com/{p.github_owner}/{p.name}/issues/${{2}}))" }},\n'
    result += r"  { pattern = '\s+$', replace = " + '"" },\n'
    result += "]\n"
    result += 'commit_parsers = [\n'
    result += '  { message = "^feat", group = "Features" },\n'
    result += '  { message = "^fix", group = "Bug Fixes" },\n'
    result += '  { message = "^doc", group = "Documentation" },\n'
    result += '  { message = "^perf", group = "Performance" },\n'
    result += '  { message = "^refactor", group = "Refactor" },\n'
    result += '  { message = "^style", group = "Styling" },\n'
    result += '  { message = "^test", group = "Testing" },\n'
    result += '  { message = "^chore\\\\(release\\\\)", skip = true },\n'
    result += '  { message = "^chore\\\\(deps\\\\)", skip = true },\n'
    result += '  { message = "^chore\\\\(pr\\\\)", skip = true },\n'
    result += '  { message = "^chore\\\\(pull\\\\)", skip = true },\n'
    result += '  { message = "^chore|^ci", group = "Miscellaneous Tasks" },\n'
    result += '  { body = ".*security", group = "Security" },\n'
    result += '  { message = "^revert", group = "Revert" },\n'
    result += "]\n"
    result += "protect_breaking_commits = false\n"
    result += "filter_commits = false\n"
    result += 'tag_pattern = "v[0-9].*"\n'
    result += 'skip_tags = ""\n'
    result += 'ignore_tags = ""\n'
    result += "topo_order = false\n"
    result += 'sort_commits = "oldest"\n'
    return result


def gen_publish_py(p: PluginParams) -> str:
    """Generate scripts/publish.py — unified publish pipeline with --gate mode."""
    _ = p  # unused but kept for consistent signature
    return r'''#!/usr/bin/env python3
"""Unified publish pipeline: lint -> validate -> test -> bump -> badge -> changelog -> commit -> push.

Modes:
  --gate           Pre-push gate: lint + validate + tests only (no bump/push).
                   Called by git-hooks/pre-push automatically.
  --install-hook   Install git-hooks/pre-push into .git/hooks/ and set core.hooksPath.
  --patch/--minor/--major  Full release pipeline (12 stages).

Pipeline stages (all fail-fast — any failure aborts):
   1. Check working tree is clean
   2. Lint files (ruff)
   3. Validate plugin (validate_plugin.py --strict)
   4. Run tests (pytest)
   5. Check version consistency across all sources
   6. Bump version in plugin.json, pyproject.toml, and __version__ vars
   7. Update README version badge
   8. Generate changelog (git-cliff)
   9. Commit, tag, push
  10. Create GitHub release (gh CLI)

Gate stages (--gate mode, called by pre-push hook):
   G1. Version bump check (local vs remote)
   G2. Lint (ruff)
   G3. Validate (--strict, blocks on CRITICAL/MAJOR/MINOR/NIT)
   G4. Tests (pytest)

Usage:
    uv run python scripts/publish.py --gate
    uv run python scripts/publish.py --install-hook
    uv run python scripts/publish.py --patch
    uv run python scripts/publish.py --minor
    uv run python scripts/publish.py --major
    uv run python scripts/publish.py --patch --dry-run
    uv run python scripts/publish.py --patch --skip-tests
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# -- ANSI colors ---------------------------------------------------------------


def _colors_ok() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_C = _colors_ok()
RED    = "\033[0;31m" if _C else ""
GREEN  = "\033[0;32m" if _C else ""
YELLOW = "\033[1;33m" if _C else ""
BLUE   = "\033[0;34m" if _C else ""
BOLD   = "\033[1m" if _C else ""
NC     = "\033[0m" if _C else ""


# -- Helpers -------------------------------------------------------------------


def cprint(msg: str) -> None:
    print(msg, flush=True)

def run(
    cmd: list[str], cwd: Path | None = None, *, check: bool = True, capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, stream output, fail-fast on error."""
    cprint(f"  {BLUE}$ {' '.join(cmd)}{NC}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True,
                            capture_output=capture, timeout=300)
    if check and result.returncode != 0:
        cprint(f"  {RED}Command failed (exit {result.returncode}){NC}")
        sys.exit(result.returncode)
    return result

def get_repo_root() -> Path:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, check=True)
    return Path(r.stdout.strip())


# -- Semver --------------------------------------------------------------------

def parse_semver(version: str) -> tuple[int, int, int] | None:
    """Parse 'X.Y.Z' into (major, minor, patch)."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))

def bump_semver(current: str, bump_type: str) -> str | None:
    """Bump version by major/minor/patch. Returns new version string or None."""
    parsed = parse_semver(current)
    if not parsed:
        return None
    major, minor, patch = parsed
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return None


# -- Version readers/writers ---------------------------------------------------

def get_current_version(plugin_root: Path) -> str | None:
    """Read version from .claude-plugin/plugin.json."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        ver = data.get("version")
        return str(ver) if ver is not None else None
    except (json.JSONDecodeError, OSError):
        return None

def update_plugin_json(root: Path, new_ver: str) -> tuple[bool, str]:
    """Write version to .claude-plugin/plugin.json."""
    pj = root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return False, "plugin.json not found"
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        data["version"] = new_ver
        pj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return True, f"plugin.json -> {new_ver}"
    except (json.JSONDecodeError, OSError) as e:
        return False, f"plugin.json update failed: {e}"

def update_pyproject_toml(root: Path, new_ver: str) -> tuple[bool, str]:
    """Write version to pyproject.toml."""
    pp = root / "pyproject.toml"
    if not pp.is_file():
        return False, "pyproject.toml not found"
    try:
        content = pp.read_text(encoding="utf-8")
        updated = re.sub(
            r'^(version\s*=\s*")[^"]*(")',
            rf'\g<1>{new_ver}\2',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if updated == content:
            return False, "pyproject.toml: version field not found"
        pp.write_text(updated, encoding="utf-8")
        return True, f"pyproject.toml -> {new_ver}"
    except OSError as e:
        return False, f"pyproject.toml update failed: {e}"

def update_python_versions(root: Path, new_ver: str) -> list[tuple[bool, str]]:
    """Update __version__ = '...' in all .py files under scripts/."""
    results: list[tuple[bool, str]] = []
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        return results
    pattern = re.compile(r'^(__version__\s*=\s*["\'])([^"\']*)(["\']\s*)$', re.MULTILINE)
    for py_file in scripts_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if not pattern.search(content):
            continue
        updated = pattern.sub(rf"\g<1>{new_ver}\3", content)
        if updated != content:
            py_file.write_text(updated, encoding="utf-8")
            results.append((True, f"{py_file.relative_to(root)} -> {new_ver}"))
    return results

def check_version_consistency(root: Path) -> tuple[bool, str]:
    """Verify all version sources match."""
    versions: dict[str, str | None] = {}

    # plugin.json
    pj = root / ".claude-plugin" / "plugin.json"
    if pj.is_file():
        try:
            versions["plugin.json"] = json.loads(pj.read_text(encoding="utf-8")).get("version")
        except (json.JSONDecodeError, OSError):
            versions["plugin.json"] = None

    # pyproject.toml
    pp = root / "pyproject.toml"
    if pp.is_file():
        m = re.search(r'^version\s*=\s*"([^"]*)"', pp.read_text(encoding="utf-8"), re.MULTILINE)
        versions["pyproject.toml"] = m.group(1) if m else None

    found = {k: v for k, v in versions.items() if v is not None}
    if not found:
        return False, "No version sources found"
    unique = set(found.values())
    if len(unique) == 1:
        return True, f"All versions match: {unique.pop()}"
    details = ", ".join(f"{k}={v}" for k, v in found.items())
    return False, f"Version mismatch: {details}"

def do_bump(root: Path, new_ver: str, dry_run: bool = False) -> bool:
    """Orchestrate all version updates."""
    cprint(f"\n{BOLD}Bumping to {new_ver}{' (dry-run)' if dry_run else ''}{NC}")

    if dry_run:
        cprint(f"  Would update plugin.json -> {new_ver}")
        cprint(f"  Would update pyproject.toml -> {new_ver}")
        cprint(f"  Would update __version__ vars -> {new_ver}")
        return True

    ok1, msg1 = update_plugin_json(root, new_ver)
    cprint(f"  {'OK' if ok1 else 'FAIL'}: {msg1}")

    ok2, msg2 = update_pyproject_toml(root, new_ver)
    cprint(f"  {'OK' if ok2 else 'FAIL'}: {msg2}")

    py_results = update_python_versions(root, new_ver)
    for ok, msg in py_results:
        cprint(f"  {'OK' if ok else 'FAIL'}: {msg}")

    return ok1 and ok2


# -- Hook installer ------------------------------------------------------------

def install_hook(root: Path) -> int:
    """Copy git-hooks/pre-push to .git/hooks/pre-push and set core.hooksPath."""
    cprint(f"\n{BOLD}Installing git hooks...{NC}")
    source = root / "git-hooks" / "pre-push"
    if not source.is_file():
        cprint(f"  {RED}git-hooks/pre-push not found{NC}")
        return 1
    git_dir = root / ".git"
    if not git_dir.is_dir():
        cprint(f"  {RED}.git/ not found — is this a git repository?{NC}")
        return 1
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / "pre-push"
    shutil.copy2(source, dest)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    cprint(f"  {GREEN}Installed: git-hooks/pre-push -> .git/hooks/pre-push{NC}")
    # Also set core.hooksPath so git finds hooks in git-hooks/ directly
    subprocess.run(["git", "config", "core.hooksPath", "git-hooks"],
                   cwd=str(root), check=False)
    cprint(f"  {GREEN}Set git config core.hooksPath = git-hooks{NC}")
    return 0


# -- Gate mode (pre-push quality checks) --------------------------------------

def run_gate(root: Path) -> int:
    """Pre-push gate: blocks on any quality issue. Returns 0 if clean."""
    cprint(f"\n{BOLD}Pre-push gate checks{NC}\n")

    # Gate 1: Version bump check — local vs remote
    cprint(f"{BLUE}[G1] Checking version bump...{NC}")
    local_ver = get_current_version(root)
    if local_ver:
        try:
            r = subprocess.run(
                ["git", "show", "origin/main:.claude-plugin/plugin.json"],
                capture_output=True, text=True, cwd=str(root))
            if r.returncode == 0:
                remote_ver = json.loads(r.stdout).get("version")
                if remote_ver and local_ver == remote_ver:
                    cprint(f"  {RED}BLOCKED: Version not bumped ({local_ver}){NC}")
                    return 1
                cprint(f"  {GREEN}Version bump OK: {remote_ver} -> {local_ver}{NC}")
        except Exception:
            cprint(f"  {YELLOW}Could not check remote version (new repo?){NC}")

    # Gate 2: Lint with ruff directly
    cprint(f"\n{BLUE}[G2] Linting...{NC}")
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        lint_result = subprocess.run(
            ["uv", "run", "ruff", "check", "scripts/"],
            cwd=str(root), timeout=120)
        if lint_result.returncode != 0:
            cprint(f"  {RED}BLOCKED: Lint issues found{NC}")
            return 1
        cprint(f"  {GREEN}Lint passed.{NC}")
    else:
        cprint(f"  {YELLOW}No scripts/ directory — skipping lint.{NC}")

    # Gate 3: Validate plugin (--strict, blocks on CRITICAL/MAJOR/MINOR/NIT)
    cprint(f"\n{BLUE}[G3] Validating plugin...{NC}")
    validator = root / "scripts" / "validate_plugin.py"
    if validator.is_file():
        ve = subprocess.run(
            ["uv", "run", "python", str(validator), ".", "--strict"],
            cwd=str(root), timeout=180).returncode
        # Exit codes: 0=pass, 1=CRITICAL, 2=MAJOR, 3=MINOR, 4=NIT, 5+=WARNING
        if ve != 0 and ve < 5:
            labels = {1: "CRITICAL", 2: "MAJOR", 3: "MINOR", 4: "NIT"}
            cprint(f"  {RED}BLOCKED: {labels.get(ve, f'exit {ve}')} issues found{NC}")
            return 1
        cprint(f"  {GREEN}Validation passed.{NC}")
    else:
        cprint(f"  {YELLOW}No validate_plugin.py — skipping.{NC}")

    # Gate 4: Tests
    cprint(f"\n{BLUE}[G4] Running tests...{NC}")
    test_dir = root / "tests"
    if test_dir.is_dir() and any(test_dir.glob("test_*.py")):
        try:
            te = subprocess.run(
                ["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"],
                cwd=str(root), timeout=300).returncode
        except subprocess.TimeoutExpired:
            cprint(f"  {YELLOW}Tests timed out after 300s, skipping.{NC}")
            te = 0
        if te == 5:
            cprint(f"  {YELLOW}No tests collected — skipping.{NC}")
        elif te != 0:
            cprint(f"  {RED}BLOCKED: Tests failed{NC}")
            return 1
        else:
            cprint(f"  {GREEN}Tests passed.{NC}")
    else:
        cprint(f"  {YELLOW}No test files found — skipping.{NC}")

    cprint(f"\n{GREEN}{BOLD}All gates passed.{NC}")
    return 0


# -- Pipeline stages -----------------------------------------------------------

def stage_check_clean(root: Path) -> None:
    """Step 1: Working tree must be clean."""
    cprint(f"\n{BOLD}[1/10] Checking working tree...{NC}")
    r = run(["git", "status", "--porcelain"], cwd=root, capture=True)
    if r.stdout.strip():
        cprint(f"  {RED}Working tree is dirty. Commit or stash changes first.{NC}")
        cprint(r.stdout)
        sys.exit(1)
    cprint(f"  {GREEN}Clean.{NC}")

def stage_lint(root: Path) -> None:
    """Step 2: Lint with ruff."""
    cprint(f"\n{BOLD}[2/10] Linting...{NC}")
    run(["uv", "run", "ruff", "check", "scripts/"], cwd=root)
    cprint(f"  {GREEN}Lint passed.{NC}")

def stage_validate(root: Path) -> None:
    """Step 3: Validate plugin structure."""
    cprint(f"\n{BOLD}[3/10] Validating plugin...{NC}")
    validator = root / "scripts" / "validate_plugin.py"
    if not validator.is_file():
        cprint(f"  {YELLOW}No validate_plugin.py — skipping.{NC}")
        return
    run(["uv", "run", "python", str(validator), ".", "--strict"], cwd=root)
    cprint(f"  {GREEN}Validation passed.{NC}")

def stage_tests(root: Path) -> None:
    """Step 4: Run pytest."""
    cprint(f"\n{BOLD}[4/10] Running tests...{NC}")
    test_dir = root / "tests"
    if not test_dir.is_dir():
        cprint(f"  {YELLOW}No tests/ directory — skipping.{NC}")
        return
    # pytest exit code 5 = no tests collected, which is OK for fresh plugins
    r = run(["uv", "run", "pytest", "tests/", "-x", "-q", "--tb=short"], cwd=root, check=False)
    if r.returncode == 5:
        cprint(f"  {YELLOW}No tests collected — skipping.{NC}")
    elif r.returncode != 0:
        cprint(f"  {RED}Tests failed (exit {r.returncode}).{NC}")
        sys.exit(r.returncode)
    else:
        cprint(f"  {GREEN}Tests passed.{NC}")

def stage_consistency(root: Path) -> None:
    """Step 5: Check version consistency."""
    cprint(f"\n{BOLD}[5/10] Checking version consistency...{NC}")
    ok, msg = check_version_consistency(root)
    cprint(f"  {msg}")
    if not ok:
        cprint(f"  {RED}Fix version mismatch before publishing.{NC}")
        sys.exit(1)
    cprint(f"  {GREEN}Consistent.{NC}")

def stage_bump(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 6: Bump version."""
    cprint(f"\n{BOLD}[6/10] Bumping version...{NC}")
    if not do_bump(root, new_ver, dry_run=dry_run):
        cprint(f"  {RED}Version bump failed.{NC}")
        sys.exit(1)
    cprint(f"  {GREEN}Version bumped to {new_ver}.{NC}")

def stage_update_badges(root: Path, old_ver: str, new_ver: str, dry_run: bool) -> None:
    """Step 7: Replace version badge in README.md."""
    cprint(f"\n{BOLD}[7/10] Updating README badge...{NC}")
    readme = root / "README.md"
    if not readme.exists():
        cprint(f"  {YELLOW}No README.md — skipping badge update.{NC}")
        return
    content = readme.read_text(encoding="utf-8")
    old_badge = f"version-{old_ver}-blue"
    new_badge = f"version-{new_ver}-blue"
    if old_badge not in content:
        cprint(f"  {YELLOW}Version badge not found in README.md, skipping.{NC}")
        return
    if dry_run:
        cprint(f"  Would update badge: {old_badge} -> {new_badge}")
        return
    readme.write_text(content.replace(old_badge, new_badge, 1), encoding="utf-8")
    cprint(f"  {GREEN}Updated README badge: {old_ver} -> {new_ver}{NC}")

def stage_changelog(root: Path, dry_run: bool) -> None:
    """Step 8: Generate changelog with git-cliff."""
    cprint(f"\n{BOLD}[8/10] Generating changelog...{NC}")
    if not shutil.which("git-cliff"):
        cprint(f"  {YELLOW}git-cliff not installed — skipping changelog.{NC}")
        return
    cliff_toml = root / "cliff.toml"
    if not cliff_toml.is_file():
        cprint(f"  {YELLOW}No cliff.toml — skipping changelog.{NC}")
        return
    if dry_run:
        cprint("  Would run: git-cliff -o CHANGELOG.md")
        return
    run(["git-cliff", "-o", "CHANGELOG.md"], cwd=root)
    cprint(f"  {GREEN}Changelog generated.{NC}")

def stage_commit_and_push(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 9: Commit, tag, push."""
    cprint(f"\n{BOLD}[9/10] Committing and pushing...{NC}")
    tag = f"v{new_ver}"
    if dry_run:
        cprint(f"  Would commit: chore: bump version to {new_ver}")
        cprint(f"  Would tag: {tag}")
        cprint("  Would push: origin HEAD --tags")
        return
    run(["git", "add", "-A"], cwd=root)
    run(["git", "commit", "-m", f"chore: bump version to {new_ver}"], cwd=root)
    run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], cwd=root)
    run(["git", "push", "origin", "HEAD", "--tags"], cwd=root)
    cprint(f"  {GREEN}Pushed {tag}.{NC}")

def stage_gh_release(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 10: Create GitHub release via gh CLI."""
    cprint(f"\n{BOLD}[10/10] Creating GitHub release...{NC}")
    tag = f"v{new_ver}"
    if not shutil.which("gh"):
        cprint(f"  {YELLOW}gh CLI not installed — skipping release.{NC}")
        return
    if dry_run:
        cprint(f"  Would create release: {tag}")
        return
    changelog_file = root / "CHANGELOG.md"
    args = ["gh", "release", "create", tag, "--title", tag, "--generate-notes"]
    if changelog_file.is_file():
        args.extend(["--notes-file", str(changelog_file)])
    run(args, cwd=root, check=False)
    cprint(f"  {GREEN}Release created.{NC}")


# -- Main ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Unified publish pipeline for Claude Code plugins.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Mutually exclusive: --gate / --install-hook / --patch/--minor/--major
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--gate", action="store_true",
                            help="Pre-push gate mode: lint + validate + tests only (no bump/push)")
    mode_group.add_argument("--install-hook", action="store_true",
                            help="Install pre-push hook into .git/hooks/ and set core.hooksPath")
    mode_group.add_argument("--patch", action="store_const", dest="bump", const="patch",
                            help="Bump patch version and publish")
    mode_group.add_argument("--minor", action="store_const", dest="bump", const="minor",
                            help="Bump minor version and publish")
    mode_group.add_argument("--major", action="store_const", dest="bump", const="major",
                            help="Bump major version and publish")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest step")
    args = parser.parse_args()

    root = get_repo_root()

    # --install-hook mode: just set up the hook and exit
    if args.install_hook:
        return install_hook(root)

    # --gate mode: run quality checks only (called by pre-push hook)
    if args.gate:
        return run_gate(root)

    # Full publish pipeline (--patch/--minor/--major)
    current = get_current_version(root)
    if not current:
        cprint(f"{RED}Cannot read version from .claude-plugin/plugin.json{NC}")
        return 1

    new_ver = bump_semver(current, args.bump)
    if not new_ver:
        cprint(f"{RED}Cannot parse current version: {current}{NC}")
        return 1

    cprint(f"\n{BOLD}Publish pipeline: {current} -> {new_ver}{NC}")
    if args.dry_run:
        cprint(f"{YELLOW}(dry-run mode — no changes will be made){NC}")

    stage_check_clean(root)
    stage_lint(root)
    stage_validate(root)
    if not args.skip_tests:
        stage_tests(root)
    stage_consistency(root)
    stage_bump(root, new_ver, args.dry_run)
    stage_update_badges(root, current, new_ver, args.dry_run)
    stage_changelog(root, args.dry_run)
    stage_commit_and_push(root, new_ver, args.dry_run)
    stage_gh_release(root, new_ver, args.dry_run)

    cprint(f"\n{GREEN}{BOLD}Published {new_ver} successfully!{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def gen_setup_hooks_py() -> str:
    """Generate scripts/setup-hooks.py — installs git hooks from git-hooks/ into .git/hooks/."""
    return '''#!/usr/bin/env python3
"""Install git hooks from git-hooks/ into .git/hooks/.

Usage: uv run python scripts/setup-hooks.py
"""

from __future__ import annotations

import shutil
import stat
import sys
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

    print(f"\\nDone. {len(hooks)} hook(s) installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def gen_pre_push_hook(p: PluginParams) -> str:
    """Generate git-hooks/pre-push — thin bash delegator to publish.py --gate."""
    _ = p  # unused but kept for consistent signature
    return '''#!/usr/bin/env bash
# Pre-push hook — delegates to publish.py --gate for all quality checks.
# Follows the PSS (perfect-skill-suggester) pattern: one script, two modes.
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
if command -v uv &> /dev/null; then
    uv run python scripts/publish.py --gate
else
    python3 scripts/publish.py --gate
fi
exit $?
'''


def gen_ci_yml(p: PluginParams) -> str:
    """Generate .github/workflows/ci.yml — Mega-Linter + validate + test."""
    _ = p  # unused but kept for consistent signature
    return """name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  mega-linter:
    name: Mega-Linter
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Mega-Linter
        uses: oxsecurity/megalinter@v8
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          VALIDATE_ALL_CODEBASE: false

      - name: Upload Mega-Linter reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: mega-linter-reports
          path: |
            megalinter-reports/
            mega-linter.log

  validate:
    name: Plugin Validation
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync --extra dev

      - name: Validate plugin
        run: |
          if [ -f "scripts/validate_plugin.py" ]; then
            uv run python scripts/validate_plugin.py . --verbose
          fi

  test:
    name: Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync --extra dev

      - name: Run tests
        run: |
          if [ -d "tests" ] && ls tests/test_*.py 1>/dev/null 2>&1; then
            uv run pytest tests/ -v
          else
            echo "No test files found, skipping"
          fi
"""


def gen_release_yml(p: PluginParams) -> str:
    """Generate .github/workflows/release.yml — GitHub Release on semver tag."""
    _ = p  # unused but kept for consistent signature
    return """name: Release

on:
  push:
    tags:
      - 'v*.*.*'

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync --extra dev

      - name: Run full plugin validation
        run: |
          set +e
          uv run python scripts/validate_plugin.py . --verbose > validation-report.txt 2>&1
          exit_code=$?
          set -e
          cat validation-report.txt
          if [ $exit_code -le 2 ] && [ $exit_code -ge 1 ]; then
            echo "::error::Validation failed with exit code $exit_code (critical/major issues found)"
            exit $exit_code
          fi

      - name: Run tests
        run: |
          if [ -d "tests" ]; then
            uv run pytest tests/ -v
          fi

      - name: Lint Python scripts
        run: uv run ruff check scripts/

      - name: Type check
        run: uv run mypy scripts/ --ignore-missing-imports

      - name: Generate changelog
        id: changelog
        run: |
          PREV_TAG=$(git describe --tags --abbrev=0 HEAD^ 2>/dev/null || echo "")
          if [ -z "$PREV_TAG" ]; then
            CHANGELOG=$(git log --pretty=format:"- %s (%h)" HEAD)
          else
            CHANGELOG=$(git log --pretty=format:"- %s (%h)" ${PREV_TAG}..HEAD)
          fi
          echo "$CHANGELOG" > changelog.txt
          echo "changelog_file=changelog.txt" >> $GITHUB_OUTPUT

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          body_path: changelog.txt
          files: |
            validation-report.txt
          generate_release_notes: true
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
"""


def gen_validate_yml(p: PluginParams) -> str:
    """Generate .github/workflows/validate.yml — CPV plugin validation only (linting is in ci.yml via Mega-Linter)."""
    _ = p  # unused but kept for consistent signature
    return """name: Plugin Validation

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync --extra dev

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

      - name: Validate plugin
        if: steps.find-validator.outputs.validator != ''
        run: |
          set +e
          uv run python ${{ steps.find-validator.outputs.validator }} . --verbose
          exit_code=$?
          set -e
          if [ $exit_code -eq 0 ]; then
            echo "Validation passed"
            exit 0
          else
            echo "Validation failed (exit code: $exit_code)"
            exit $exit_code
          fi
"""


def gen_mega_linter_yml(p: PluginParams) -> str:
    """Generate .mega-linter.yml — Mega-Linter configuration."""
    _ = p  # unused but kept for consistent signature
    return """# Mega-Linter configuration
# https://megalinter.io/latest/configuration/

# Only lint changed files (faster, less noise)
APPLY_FIXES: none
VALIDATE_ALL_CODEBASE: false

# Enable these linter groups
ENABLE_LINTERS:
  - PYTHON_RUFF
  - PYTHON_MYPY
  - PYTHON_BANDIT
  - BASH_SHELLCHECK
  - BASH_SHFMT
  - JSON_JSONLINT
  - YAML_YAMLLINT
  - MARKDOWN_MARKDOWNLINT
  - SPELL_CSPELL
  - COPYPASTE_JSCPD
  - REPOSITORY_CHECKOV
  - REPOSITORY_GITLEAKS
  - REPOSITORY_TRIVY

# Exclude paths
FILTER_REGEX_EXCLUDE: "(tests_dev/|docs_dev/|scripts_dev/|samples_dev/|examples_dev/|builds_dev/|downloads_dev/|libs_dev/|llm_externalizer_output/|\\.claude/|\\.tldr/)"

# Python settings
PYTHON_RUFF_ARGUMENTS: "--select=E,F,W,I --ignore=E501"
PYTHON_MYPY_ARGUMENTS: "--ignore-missing-imports"

# Copy-paste detection — allow up to 5% duplication (0% is too strict for plugins)
COPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"

# Checkov — skip workflow-level permission checks (we set permissions per-job)
REPOSITORY_CHECKOV_ARGUMENTS: "--skip-check CKV2_GHA_1"

# Markdown settings — allow long lines in README (badges)
MARKDOWN_MARKDOWNLINT_FILTER_REGEX_EXCLUDE: "CHANGELOG\\.md"

# Spell check — add project-specific words
SPELL_CSPELL_FILTER_REGEX_EXCLUDE: "(uv\\.lock|\\.json)"

# Disable reporters that create PR comments (we handle that ourselves)
DISABLE_REPORTERS:
  - GITHUB_COMMENT_REPORTER
"""


def gen_notify_marketplace_yml(p: PluginParams) -> str:
    """Generate .github/workflows/notify-marketplace.yml — marketplace notification."""
    marketplace_owner = p.github_owner
    marketplace_repo = p.marketplace if p.marketplace else "my-plugins-marketplace"
    return f"""# Notify marketplace repo when this plugin is updated
# Requires MARKETPLACE_PAT secret (Personal Access Token with repo scope)

name: Notify Marketplace

on:
  push:
    branches: [main]
    paths:
      - '.claude-plugin/plugin.json'
      - 'hooks/**'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'scripts/**'

env:
  MARKETPLACE_OWNER: '{marketplace_owner}'
  MARKETPLACE_REPO: '{marketplace_repo}'

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Get plugin info
        id: plugin
        run: |
          echo "name=${{{{ github.event.repository.name }}}}" >> $GITHUB_OUTPUT
          echo "ref=${{{{ github.sha }}}}" >> $GITHUB_OUTPUT

      - name: Trigger marketplace update
        uses: peter-evans/repository-dispatch@v4
        with:
          token: ${{{{ secrets.MARKETPLACE_PAT }}}}
          repository: ${{{{ env.MARKETPLACE_OWNER }}}}/${{{{ env.MARKETPLACE_REPO }}}}
          event-type: plugin-updated
          client-payload: |
            {{
              "plugin": "${{{{ steps.plugin.outputs.name }}}}",
              "ref": "${{{{ steps.plugin.outputs.ref }}}}",
              "source_repo": "${{{{ github.repository }}}}",
              "triggered_by": "${{{{ github.actor }}}}"
            }}

      - name: Summary
        run: |
          echo "## Marketplace Notification" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "Triggered update in ${{{{ env.MARKETPLACE_OWNER }}}}/${{{{ env.MARKETPLACE_REPO }}}}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "- Plugin: ${{{{ steps.plugin.outputs.name }}}}" >> $GITHUB_STEP_SUMMARY
          echo "- Commit: ${{{{ steps.plugin.outputs.ref }}}}" >> $GITHUB_STEP_SUMMARY
"""


def gen_tests_init() -> str:
    """Generate tests/__init__.py placeholder."""
    return '"""Test suite for the plugin."""\n'


def gen_scripts_init(p: PluginParams) -> str:
    """Generate scripts/__init__.py with version."""
    return f'"""Plugin scripts for {p.name}."""\n\n__version__ = "{p.version}"\n'


# =============================================================================
# FILE ASSEMBLY
# =============================================================================


def generate_all_files(p: PluginParams) -> list[tuple[str, str, bool]]:
    """Return list of (relative_path, content, is_executable) for all scaffold files."""
    files: list[tuple[str, str, bool]] = [
        # Manifest
        (".claude-plugin/plugin.json", gen_plugin_json(p), False),
        # Project config
        ("pyproject.toml", gen_pyproject_toml(p), False),
        (".python-version", gen_python_version(p), False),
        (".gitignore", gen_gitignore(p), False),
        # Documentation
        ("README.md", gen_readme(p), False),
        ("LICENSE", gen_license_mit(p), False),
        # Changelog config
        ("cliff.toml", gen_cliff_toml(p), False),
        # Scripts
        ("scripts/__init__.py", gen_scripts_init(p), False),
        ("scripts/publish.py", gen_publish_py(p), True),
        ("scripts/setup-hooks.py", gen_setup_hooks_py(), True),
        # Git hooks
        ("git-hooks/pre-push", gen_pre_push_hook(p), True),
        # Mega-Linter config
        (".mega-linter.yml", gen_mega_linter_yml(p), False),
        # CI/CD workflows
        (".github/workflows/ci.yml", gen_ci_yml(p), False),
        (".github/workflows/release.yml", gen_release_yml(p), False),
        (".github/workflows/validate.yml", gen_validate_yml(p), False),
        (".github/workflows/notify-marketplace.yml", gen_notify_marketplace_yml(p), False),
        # Test suite placeholder
        ("tests/__init__.py", gen_tests_init(), False),
    ]
    return files


# =============================================================================
# DIRECTORY CREATION
# =============================================================================

# Standard component directories that every plugin repo should have
COMPONENT_DIRS = [
    ".claude-plugin",
    ".github/workflows",
    "agents",
    "commands",
    "git-hooks",
    "hooks",
    "scripts",
    "skills",
    "tests",
]


def generate_plugin_repo(target: Path, p: PluginParams, dry_run: bool = False) -> list[str]:
    """Write all scaffold files to target directory. Returns list of created file paths."""
    created: list[str] = []

    # Create component directories (including empty ones for plugin structure)
    for dir_name in COMPONENT_DIRS:
        dir_path = target / dir_name
        if dry_run:
            print(f"  {BLUE}[dry-run]{NC} mkdir -p {dir_path}")
        else:
            dir_path.mkdir(parents=True, exist_ok=True)
        created.append(str(dir_path) + "/")

    # Write all generated files
    all_files = generate_all_files(p)
    for rel_path, content, is_executable in all_files:
        file_path = target / rel_path

        if dry_run:
            print(f"  {BLUE}[dry-run]{NC} write {file_path} ({len(content)} bytes)"
                  f"{' [exec]' if is_executable else ''}")
            created.append(str(file_path))
            continue

        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the file
        file_path.write_text(content, encoding="utf-8")

        # Set executable bit if needed
        if is_executable:
            file_path.chmod(file_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        created.append(str(file_path))

    return created


# =============================================================================
# MAIN
# =============================================================================


def main() -> int:
    """Parse CLI arguments and generate the plugin repository scaffold."""
    parser = argparse.ArgumentParser(
        description="Generate a complete Claude Code plugin repository scaffold.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run scripts/generate_plugin_repo.py /tmp/my-plugin \\
    --name my-plugin --description "A cool plugin" \\
    --author "John Doe" --author-email "john@example.com" \\
    --github-owner johndoe --marketplace my-marketplace

  uv run scripts/generate_plugin_repo.py ./new-plugin \\
    --name new-plugin --description "Plugin desc" \\
    --author Emasoft --author-email "713559+Emasoft@users.noreply.github.com" \\
    --github-owner Emasoft --marketplace claude-plugins-marketplace \\
    --dry-run
""",
    )
    parser.add_argument("target_dir", type=Path, help="Target directory for the new plugin repo")
    parser.add_argument("--name", required=True, help="Plugin name (lowercase, hyphens allowed)")
    parser.add_argument("--description", required=True, help="One-line plugin description")
    parser.add_argument("--author", required=True, help="Author display name")
    parser.add_argument("--author-email", required=True, help="Author email")
    parser.add_argument("--license", default="MIT", help="SPDX license identifier (default: MIT)")
    parser.add_argument("--python-version", default="3.12", help="Minimum Python version (default: 3.12)")
    parser.add_argument("--github-owner", default="", help="GitHub account or organization name")
    parser.add_argument("--marketplace", default="", help="Marketplace name for install commands")
    parser.add_argument("--version", default="0.1.0", help="Initial version (default: 0.1.0)")
    parser.add_argument("--dry-run", action="store_true", help="Preview files without writing")

    args = parser.parse_args()

    # Build params
    params = PluginParams(
        name=args.name,
        description=args.description,
        author=args.author,
        author_email=args.author_email,
        license=args.license,
        python_version=args.python_version,
        github_owner=args.github_owner,
        marketplace=args.marketplace,
        version=args.version,
    )

    target = args.target_dir.resolve()

    # Check target directory
    if target.exists() and any(target.iterdir()):
        print(f"{YELLOW}WARNING: Target directory is not empty: {target}{NC}")
        print(f"{YELLOW}Files will be added/overwritten.{NC}")

    print(f"\n{BOLD}Generating plugin scaffold: {params.name}{NC}")
    print(f"  Target: {target}")
    print(f"  Version: {params.version}")
    print(f"  Author: {params.author} <{params.author_email}>")
    print(f"  License: {params.license}")
    if params.github_owner:
        print(f"  GitHub: {params.github_url}")
    if params.marketplace:
        print(f"  Marketplace: {params.marketplace}")
    if args.dry_run:
        print(f"  {YELLOW}(dry-run mode){NC}")
    print()

    created = generate_plugin_repo(target, params, dry_run=args.dry_run)

    # Summary
    file_count = sum(1 for f in created if not f.endswith("/"))
    dir_count = sum(1 for f in created if f.endswith("/"))
    print(f"\n{GREEN}{BOLD}Done!{NC} Created {file_count} files in {dir_count} directories.")

    if not args.dry_run:
        print(f"\n{BOLD}Next steps:{NC}")
        print(f"  cd {target}")
        print("  git init && git add -A && git commit -m 'Initial scaffold'")
        print(f"  uv venv --python {params.python_version} && source .venv/bin/activate")
        print("  uv pip install -e .")
        print("  uv run python scripts/setup-hooks.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())
