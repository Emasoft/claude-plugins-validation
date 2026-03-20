#!/usr/bin/env python3
"""Generate a Claude Code marketplace hub repository scaffold.

Marketplaces are HUBS ONLY -- they contain plugin metadata and pointers to
external GitHub repos, never plugin code. Each plugin lives in its own repo.

Usage:
    uv run scripts/generate_marketplace_repo.py <target-dir> \\
      --name <marketplace-name> --owner-name <owner-display-name> \\
      --description <desc> --github-owner <github-username> \\
      [--add-plugin <owner/repo>]... \\
      [--dry-run]

Exit codes:
    0 - Success
    1 - Error (invalid args, target exists, etc.)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# -- Constants ----------------------------------------------------------------

# Reserved marketplace names that will be rejected by the validator
RESERVED_NAMES = frozenset({"official", "anthropic", "claude", "test", "example", "demo"})

# Kebab-case pattern: lowercase letters, digits, hyphens only
KEBAB_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")

# ANSI colors for terminal output
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"


# -- Template Generators ------------------------------------------------------


def _marketplace_json(
    name: str,
    owner_name: str,
    description: str,
    plugins: list[dict],
) -> dict:
    """Build the marketplace.json data structure.

    Each plugin entry uses the hub-and-spoke source format:
    {"source": "github", "repo": "owner/repo"} -- never local paths.
    """
    return {
        "name": name,
        "version": "1.0.0",
        "owner": {"name": owner_name},
        "metadata": {"description": description},
        "plugins": plugins,
    }


def _plugin_entry(owner_repo: str) -> dict:
    """Build a single plugin entry from an owner/repo string.

    The description is a placeholder since we cannot fetch it without network
    access. Users should update it after generation.
    """
    # Extract the repo name as the plugin name (last segment of owner/repo)
    repo_name = owner_repo.split("/")[-1]
    return {
        "name": repo_name,
        "description": f"(update description for {repo_name})",
        "source": {"source": "github", "repo": owner_repo},
        "repository": f"https://github.com/{owner_repo}",
    }


def _readme(
    name: str,
    description: str,
    github_owner: str,
    plugins: list[dict],
) -> str:
    """Generate the README.md with a plugin catalog table."""
    # Build the plugin table rows
    rows = []
    for p in plugins:
        pname = p["name"]
        source = p.get("source", {})
        repo = source.get("repo", "") if isinstance(source, dict) else ""
        desc = p.get("description", "")
        repo_url = f"https://github.com/{repo}" if repo else ""
        install_cmd = f"`claude plugin install {pname}@{name}`"
        rows.append(f"| [{pname}]({repo_url}) | {desc} | {install_cmd} |")

    plugin_table = "\n".join(rows) if rows else "| (no plugins yet) | | |"

    return f"""# {name}

<!--BADGES-START-->
<!--BADGES-END-->

{description}

## Plugins

| Plugin | Description | Install |
|--------|-------------|---------|
{plugin_table}

## Adding Your Plugin

To list your plugin in this marketplace:
1. Create your plugin in its own GitHub repo
2. Open a PR adding your plugin to the `plugins` array in `.claude-plugin/marketplace.json`

## Installation

```bash
claude plugin marketplace add {github_owner}/{name}
```

Restart Claude Code after adding the marketplace for it to take effect.

## Installing Plugins

```bash
# List available plugins
claude plugin search @{name}

# Install a specific plugin
claude plugin install <plugin-name>@{name}
```

## Updating Plugins

```bash
claude plugin update <plugin-name>@{name}
```

## Uninstall

```bash
# Remove a single plugin
claude plugin uninstall <plugin-name>@{name}

# Remove the marketplace
claude plugin marketplace remove {github_owner}/{name}
```

After uninstalling, restart Claude Code for changes to take effect.

## Troubleshooting

| Issue | Resolution |
|-------|------------|
| Plugin not found | Run `claude plugin search @{name}` to list available plugins |
| Install fails | Ensure `gh` CLI is authenticated: `gh auth status` |
| Plugin not loading | Restart Claude Code after install: exit and reopen |
| Marketplace not listed | Re-add: `claude plugin marketplace add {github_owner}/{name}` |
| Hook path not found after update | Run `git config core.hooksPath git-hooks` in the plugin directory |
| Old version after update | Run `claude plugin update <plugin-name>@{name}` and restart Claude Code |
"""


def _gitignore() -> str:
    """Generate the .gitignore file."""
    return """# OS
.DS_Store
Thumbs.db

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
*.egg-info/
.eggs/
dist/
build/
.venv/
venv/

# Type checking / linting
.mypy_cache/
.ruff_cache/

# IDE
.idea/
.vscode/
*.swp
*.swo
*~

# Node.js
node_modules/

# Logs
*.log
logs/

# Temp
tmp/
temp/
*.tmp

# Dev folders (never published)
*_dev/

# Environment
.env

# Claude Code local settings
.claude/

# TLDR cache
.tldr/

# LLM Externalizer output
llm_externalizer_output/
"""


def _validate_workflow() -> str:
    """Generate .github/workflows/validate.yml for marketplace CI."""
    return """name: Marketplace Validation

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

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Validate marketplace structure
        run: |
          echo "=== Validating Marketplace Structure ==="

          # Check marketplace.json exists and is valid JSON
          if [ -f ".claude-plugin/marketplace.json" ]; then
            echo "OK marketplace.json exists"
            python -c "import json; json.load(open('.claude-plugin/marketplace.json'))" && echo "OK marketplace.json is valid JSON"
          else
            echo "FAIL marketplace.json not found"
            exit 1
          fi

          # Validate all plugin entries have required fields and valid sources.
          #
          # IMPORTANT: This is inline Python inside a YAML double-quoted shell string.
          # Shell quoting rules apply -- the shell strips inner double quotes before
          # Python sees the code. Therefore:
          #   - NEVER use dict["key"] inside f-strings (shell eats the quotes)
          #   - ALWAYS extract dict values into local variables first
          #   - Use only single quotes inside f-strings
          python3 -c "
          import json, sys
          with open('.claude-plugin/marketplace.json') as f:
              data = json.load(f)
          if not data.get('name'):
              print('FAIL: missing marketplace name')
              sys.exit(1)
          plugins = data.get('plugins', [])
          for p in plugins:
              name = p.get('name', '?')
              if not p.get('name'):
                  print(f'FAIL: plugin entry missing name')
                  sys.exit(1)
              source = p.get('source')
              if not source:
                  print(f'FAIL: plugin {name} missing source')
                  sys.exit(1)
              if isinstance(source, dict):
                  src_type = source.get('source')
                  repo = source.get('repo', '')
                  if src_type == 'github' and repo:
                      print(f'OK {name}: GitHub source -> {repo}')
                  else:
                      print(f'FAIL: plugin {name} invalid source object (needs source+repo)')
                      sys.exit(1)
              else:
                  print(f'FAIL: plugin {name} invalid source type (must be object)')
                  sys.exit(1)
          print(f'')
          print(f'=== All {len(plugins)} plugin entries validated ===')
          "

      - name: Lint marketplace scripts
        run: |
          echo "=== Linting marketplace scripts ==="
          pip install ruff
          if [ -d "scripts" ]; then
            ruff check scripts/ --select=E,F,W --ignore=E501 || echo "WARNING Some lint warnings (non-blocking)"
          else
            echo "No scripts/ directory to lint"
          fi
"""


def _update_catalog_workflow(name: str) -> str:
    """Generate .github/workflows/update-catalog.yml to regenerate README."""
    _ = name  # unused but kept for consistent signature
    return """name: Update Catalog

on:
  push:
    branches: [main, master]
    paths:
      - '.claude-plugin/marketplace.json'

permissions:
  contents: write

# Serialize to prevent race conditions on git push
concurrency:
  group: update-catalog
  cancel-in-progress: false

jobs:
  update-readme:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Configure git
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

      - name: Regenerate README catalog
        run: python scripts/update_catalog.py

      - name: Check for changes
        id: changes
        run: |
          if git diff --quiet README.md; then
            echo "has_changes=false" >> $GITHUB_OUTPUT
          else
            echo "has_changes=true" >> $GITHUB_OUTPUT
          fi

      - name: Commit and push
        if: steps.changes.outputs.has_changes == 'true'
        run: |
          git add README.md
          git commit -m "docs: regenerate plugin catalog from marketplace.json"
          for attempt in 1 2 3; do
            if git push; then
              echo "Push succeeded on attempt $attempt"
              break
            fi
            echo "Push failed (attempt $attempt), pulling and retrying..."
            git pull --rebase origin main
          done

      - name: Summary
        run: |
          echo "## Catalog Update Summary" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "README.md regenerated from marketplace.json" >> $GITHUB_STEP_SUMMARY
"""


def _update_catalog_script(name: str) -> str:
    """Generate scripts/update_catalog.py that reads marketplace.json and updates README."""
    return f'''#!/usr/bin/env python3
"""update_catalog.py - Regenerate the plugin catalog table in README.md.

Reads .claude-plugin/marketplace.json and writes the plugin table into
README.md between the Plugins heading and the next heading.

Usage:
    python scripts/update_catalog.py [--marketplace-dir PATH]

Exit codes:
    0 - Success
    1 - Error
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    """Regenerate plugin catalog table in README.md from marketplace.json."""
    # Determine marketplace directory (repo root)
    if len(sys.argv) > 1 and sys.argv[1] != "--help":
        marketplace_dir = Path(sys.argv[1])
    else:
        marketplace_dir = Path.cwd()

    marketplace_json = marketplace_dir / ".claude-plugin" / "marketplace.json"
    readme_path = marketplace_dir / "README.md"

    if not marketplace_json.exists():
        print(f"Error: {{marketplace_json}} not found", file=sys.stderr)
        return 1

    # Load marketplace data
    with open(marketplace_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    mkt_name = data.get("name", "{name}")
    plugins = data.get("plugins", [])

    # Build plugin table rows
    rows = []
    for p in plugins:
        pname = p.get("name", "unknown")
        source = p.get("source", {{}})
        repo = source.get("repo", "") if isinstance(source, dict) else ""
        desc = p.get("description", "")
        repo_url = f"https://github.com/{{repo}}" if repo else ""
        install_cmd = f"`claude plugin install {{pname}}@{{mkt_name}}`"
        rows.append(f"| [{{pname}}]({{repo_url}}) | {{desc}} | {{install_cmd}} |")

    table_header = """| Plugin | Description | Install |
|--------|-------------|---------|"""
    table = table_header + "\n" + "\n".join(rows) if rows else table_header + "\n| (no plugins yet) | | |"

    # Read existing README
    if not readme_path.exists():
        print(f"Error: {{readme_path}} not found", file=sys.stderr)
        return 1

    content = readme_path.read_text(encoding="utf-8")

    # Replace the Plugins section table (between "## Plugins" and the next "##")
    lines = content.split("\n")
    new_lines: list[str] = []
    in_plugins_section = False
    table_replaced = False

    for line in lines:
        if line.strip() == "## Plugins":
            in_plugins_section = True
            new_lines.append(line)
            new_lines.append("")
            # Insert the new table
            new_lines.append(table)
            new_lines.append("")
            table_replaced = True
            continue

        if in_plugins_section:
            # Skip old table content until we hit the next heading or end
            if line.startswith("## ") and line.strip() != "## Plugins":
                in_plugins_section = False
                new_lines.append(line)
            # Skip everything else in the plugins section (old table lines)
            continue

        new_lines.append(line)

    if not table_replaced:
        print("Warning: ## Plugins section not found in README.md", file=sys.stderr)
        return 0

    readme_path.write_text("\n".join(new_lines), encoding="utf-8")
    print(f"Updated README.md with {{len(plugins)}} plugin(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _cliff_toml(name: str, github_owner: str) -> str:
    """Generate cliff.toml for git-cliff changelog generation."""
    return f"""# git-cliff configuration for {name} marketplace
# https://git-cliff.org/docs/configuration

[changelog]
# changelog header
header = \"\"\"
# Changelog

All notable changes to this project will be documented in this file.

\"\"\"
# template for the changelog body
body = \"\"\"
{{% if version %}}\\
    ## [{{{{ version | trim_start_matches(pat="v") }}}}] - {{{{ timestamp | date(format="%Y-%m-%d") }}}}
{{% else %}}\\
    ## [Unreleased]
{{% endif %}}\\
{{% for group, commits in commits | group_by(attribute="group") %}}
    ### {{{{ group | striptags | trim | upper_first }}}}
    {{% for commit in commits %}}
        - {{% if commit.scope %}}**{{{{ commit.scope }}}}:** {{% endif %}}\\
            {{{{ commit.message | upper_first }}}}\\
            {{% if commit.breaking %}} [**BREAKING**]{{% endif %}}\\
    {{% endfor %}}
{{% endfor %}}\\n
\"\"\"
# template for the changelog footer
footer = \"\"\"
---
*Generated by [git-cliff](https://git-cliff.org)*
\"\"\"
# remove the leading and trailing whitespace from the templates
trim = true
# postprocessors
postprocessors = []

[git]
# parse the commits based on https://www.conventionalcommits.org
conventional_commits = true
# filter out the commits that are not conventional
filter_unconventional = true
# process each line of a commit as an individual commit
split_commits = false
# regex for preprocessing the commit messages
commit_preprocessors = [
  # Replace issue numbers
  {{ pattern = '\\((\\ w+\\s)?#([0-9]+)\\)', replace = "([#${{2}}](https://github.com/{github_owner}/{name}/issues/${{2}}))" }},
  # Remove trailing whitespace
  {{ pattern = '\\s+$', replace = "" }},
]
# regex for parsing and grouping commits
commit_parsers = [
  {{ message = "^feat", group = "Features" }},
  {{ message = "^fix", group = "Bug Fixes" }},
  {{ message = "^doc", group = "Documentation" }},
  {{ message = "^perf", group = "Performance" }},
  {{ message = "^refactor", group = "Refactor" }},
  {{ message = "^style", group = "Styling" }},
  {{ message = "^test", group = "Testing" }},
  {{ message = "^chore\\\\(release\\\\)", skip = true }},
  {{ message = "^chore\\\\(deps\\\\)", skip = true }},
  {{ message = "^chore\\\\(pr\\\\)", skip = true }},
  {{ message = "^chore\\\\(pull\\\\)", skip = true }},
  {{ message = "^chore|^ci", group = "Miscellaneous Tasks" }},
  {{ body = ".*security", group = "Security" }},
  {{ message = "^revert", group = "Revert" }},
]
# protect breaking changes from being skipped due to matching a skipping commit_parser
protect_breaking_commits = false
# filter out the commits that are not matched by commit parsers
filter_commits = false
# regex for matching git tags
tag_pattern = "v[0-9].*"
# regex for skipping tags
skip_tags = ""
# regex for ignoring tags
ignore_tags = ""
# sort the tags topologically
topo_order = false
# sort the commits inside sections by oldest/newest order
sort_commits = "oldest"
"""


def _pre_push_hook() -> str:
    """Generate .githooks/pre-push that validates marketplace.json."""
    return """#!/usr/bin/env python3
\"\"\"pre-push hook - Validates marketplace.json before push.

Ensures marketplace.json is valid JSON with required fields and all plugin
entries have proper source objects with the hub-and-spoke format.

Install:
    git config core.hooksPath .githooks
\"\"\"

from __future__ import annotations

import json
import sys
from pathlib import Path

RED = "\\033[0;31m"
GREEN = "\\033[0;32m"
NC = "\\033[0m"


def validate_marketplace_json(repo_root: Path) -> tuple[bool, list[str]]:
    \"\"\"Validate marketplace.json structure and plugin entries.\"\"\"
    errors: list[str] = []
    mj_path = repo_root / ".claude-plugin" / "marketplace.json"

    if not mj_path.exists():
        errors.append("marketplace.json not found at .claude-plugin/marketplace.json")
        return False, errors

    try:
        with open(mj_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"marketplace.json is not valid JSON: {e}")
        return False, errors

    # Required top-level fields
    if not data.get("name"):
        errors.append("Missing required field: name")
    if not data.get("owner"):
        errors.append("Missing required field: owner")
    elif not data.get("owner", {}).get("name"):
        errors.append("Missing required field: owner.name")
    if "plugins" not in data:
        errors.append("Missing required field: plugins")

    # Validate each plugin entry
    seen_names: set[str] = set()
    for i, p in enumerate(data.get("plugins", [])):
        pname = p.get("name", f"(entry {i})")

        if not p.get("name"):
            errors.append(f"Plugin entry {i}: missing name")
            continue

        if pname in seen_names:
            errors.append(f"Plugin '{pname}': duplicate name")
        seen_names.add(pname)

        source = p.get("source")
        if not source:
            errors.append(f"Plugin '{pname}': missing source")
        elif isinstance(source, dict):
            if source.get("source") != "github":
                errors.append(f"Plugin '{pname}': source.source must be 'github'")
            if not source.get("repo"):
                errors.append(f"Plugin '{pname}': source.repo is required")
            elif "/" not in source.get("repo", ""):
                errors.append(f"Plugin '{pname}': source.repo must be owner/repo format")
        else:
            errors.append(f"Plugin '{pname}': source must be an object (hub-and-spoke)")

    return len(errors) == 0, errors


def main() -> int:
    \"\"\"Run pre-push validation.\"\"\"
    repo_root = Path(__file__).resolve().parent.parent
    print("Running pre-push marketplace validation...")

    passed, errors = validate_marketplace_json(repo_root)

    if passed:
        print(f"{GREEN}Marketplace validation passed{NC}")
        return 0

    print(f"{RED}Marketplace validation FAILED:{NC}")
    for err in errors:
        print(f"  - {err}")
    print(f"\\n{RED}Push blocked. Fix the issues above.{NC}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
"""


# -- Validation ---------------------------------------------------------------


def validate_name(name: str) -> str | None:
    """Validate marketplace name. Returns error message or None if valid."""
    if not name:
        return "Marketplace name cannot be empty"
    if name in RESERVED_NAMES:
        return f"'{name}' is a reserved marketplace name"
    if not KEBAB_RE.match(name):
        return f"'{name}' is not valid kebab-case (lowercase letters, digits, hyphens)"
    return None


def validate_plugin_repo(repo: str) -> str | None:
    """Validate an owner/repo string. Returns error message or None if valid."""
    if "/" not in repo:
        return f"'{repo}' must be in owner/repo format"
    parts = repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return f"'{repo}' must be in owner/repo format (exactly two parts)"
    return None


# -- File Writing -------------------------------------------------------------


def write_file(path: Path, content: str, dry_run: bool) -> None:
    """Write content to a file, creating parent directories as needed."""
    if dry_run:
        print(f"  {YELLOW}[DRY-RUN]{NC} Would create {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  {GREEN}Created{NC} {path}")


def write_json(path: Path, data: dict, dry_run: bool) -> None:
    """Write a JSON file with pretty formatting."""
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    write_file(path, content, dry_run)


def make_executable(path: Path, dry_run: bool) -> None:
    """Make a file executable (chmod +x)."""
    if dry_run:
        return
    path.chmod(path.stat().st_mode | 0o111)


# -- Main Logic ---------------------------------------------------------------


def generate_marketplace_repo(
    target_dir: Path,
    name: str,
    owner_name: str,
    description: str,
    github_owner: str,
    add_plugins: list[str],
    dry_run: bool,
) -> int:
    """Generate the complete marketplace hub repository scaffold.

    Returns 0 on success, 1 on error.
    """
    # Validate marketplace name
    name_error = validate_name(name)
    if name_error:
        print(f"{RED}Error:{NC} {name_error}", file=sys.stderr)
        return 1

    # Validate plugin repos
    for repo in add_plugins:
        repo_error = validate_plugin_repo(repo)
        if repo_error:
            print(f"{RED}Error:{NC} {repo_error}", file=sys.stderr)
            return 1

    # Check target directory
    if target_dir.exists() and any(target_dir.iterdir()):
        print(f"{RED}Error:{NC} Target directory is not empty: {target_dir}", file=sys.stderr)
        return 1

    # Build plugin entries
    plugins = [_plugin_entry(repo) for repo in add_plugins]

    print(f"{BOLD}Generating marketplace hub scaffold: {name}{NC}")
    print(f"  Target: {target_dir}")
    print(f"  Owner:  {owner_name} ({github_owner})")
    print(f"  Plugins: {len(plugins)}")
    if dry_run:
        print(f"  {YELLOW}(dry-run mode -- no files will be written){NC}")
    print()

    # 1. .claude-plugin/marketplace.json
    mj_data = _marketplace_json(name, owner_name, description, plugins)
    write_json(target_dir / ".claude-plugin" / "marketplace.json", mj_data, dry_run)

    # 2. README.md
    readme_content = _readme(name, description, github_owner, plugins)
    write_file(target_dir / "README.md", readme_content, dry_run)

    # 3. .gitignore
    write_file(target_dir / ".gitignore", _gitignore(), dry_run)

    # 4. .github/workflows/validate.yml
    write_file(
        target_dir / ".github" / "workflows" / "validate.yml",
        _validate_workflow(),
        dry_run,
    )

    # 5. .github/workflows/update-catalog.yml
    write_file(
        target_dir / ".github" / "workflows" / "update-catalog.yml",
        _update_catalog_workflow(name),
        dry_run,
    )

    # 6. scripts/update_catalog.py
    write_file(
        target_dir / "scripts" / "update_catalog.py",
        _update_catalog_script(name),
        dry_run,
    )

    # 7. cliff.toml
    write_file(target_dir / "cliff.toml", _cliff_toml(name, github_owner), dry_run)

    # 8. .githooks/pre-push
    pre_push_path = target_dir / ".githooks" / "pre-push"
    write_file(pre_push_path, _pre_push_hook(), dry_run)
    make_executable(pre_push_path, dry_run)

    # Summary
    print()
    print(f"{GREEN}Marketplace hub scaffold generated successfully!{NC}")
    print()
    print("Next steps:")
    print(f"  1. cd {target_dir}")
    print("  2. git init && git config core.hooksPath .githooks")
    print("  3. Update plugin descriptions in .claude-plugin/marketplace.json")
    print("  4. git add -A && git commit -m 'feat: initial marketplace scaffold'")
    print(f"  5. gh repo create {github_owner}/{name} --public --source=. --push")
    print()
    print("To add more plugins later:")
    print("  Edit .claude-plugin/marketplace.json and add entries to the plugins array.")
    print("  Each plugin source MUST be: {\"source\": \"github\", \"repo\": \"owner/repo\"}")
    print("  Then push -- the update-catalog workflow will regenerate README.md.")
    return 0


def main() -> int:
    """Parse arguments and run the generator."""
    parser = argparse.ArgumentParser(
        description="Generate a Claude Code marketplace hub repository scaffold.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create an empty marketplace
  uv run scripts/generate_marketplace_repo.py ./my-marketplace \\
    --name my-marketplace --owner-name "My Org" \\
    --description "Curated Claude Code plugins" \\
    --github-owner my-org

  # Create with initial plugins
  uv run scripts/generate_marketplace_repo.py ./my-marketplace \\
    --name my-marketplace --owner-name "My Org" \\
    --description "Curated Claude Code plugins" \\
    --github-owner my-org \\
    --add-plugin my-org/plugin-a \\
    --add-plugin my-org/plugin-b

  # Preview without writing files
  uv run scripts/generate_marketplace_repo.py ./my-marketplace \\
    --name my-marketplace --owner-name "My Org" \\
    --description "Curated Claude Code plugins" \\
    --github-owner my-org --dry-run
""",
    )

    parser.add_argument(
        "target_dir",
        type=Path,
        help="Directory to create the marketplace scaffold in",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Marketplace name (kebab-case, e.g. 'my-plugins')",
    )
    parser.add_argument(
        "--owner-name",
        required=True,
        help="Owner display name (e.g. 'My Organization')",
    )
    parser.add_argument(
        "--description",
        required=True,
        help="Marketplace description",
    )
    parser.add_argument(
        "--github-owner",
        required=True,
        help="GitHub username or org (e.g. 'my-org')",
    )
    parser.add_argument(
        "--add-plugin",
        action="append",
        default=[],
        dest="add_plugins",
        metavar="OWNER/REPO",
        help="Add a plugin by GitHub owner/repo (repeatable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without writing files",
    )

    args = parser.parse_args()

    return generate_marketplace_repo(
        target_dir=args.target_dir.resolve(),
        name=args.name,
        owner_name=args.owner_name,
        description=args.description,
        github_owner=args.github_owner,
        add_plugins=args.add_plugins,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
