# Script Templates

Ready-to-use scripts for managing a Claude Code plugin marketplace repository.
Each script is complete, functional, and can be copy-pasted into the appropriate location.

## Table of Contents

- [Placeholder Reference](#placeholder-reference)
- [sync_marketplace_versions.py](#sync_marketplace_versionspy)
- [generate-readme.py](#generate-readmepy)
- [setup-hooks.py](#setup-hookspy)
- [pre-push-hook.py](#pre-push-hookpy)
- [push-plugins.sh](#push-pluginssh)

## Placeholder Reference

| Placeholder | Description | Where Used |
|-------------|-------------|------------|
| `{{MARKETPLACE_NAME}}` | Marketplace display name (e.g. "My Plugin Marketplace") | generate-readme.py |
| `{{MARKETPLACE_OWNER}}` | GitHub username or organization | push-plugins.sh |
| `{{MARKETPLACE_REPO}}` | GitHub repository name | push-plugins.sh |
| `{{MARKETPLACE_DIR}}` | Local path to marketplace repo root | push-plugins.sh |

---

## sync_marketplace_versions.py

Syncs plugin versions in marketplace.json by reading each plugin's plugin.json,
either from local submodule directories or from their GitHub repos via the `gh` CLI.

**Install location:** `scripts/sync_marketplace_versions.py`

```python
#!/usr/bin/env python3
"""
sync_marketplace_versions.py - Sync plugin versions to marketplace.json

Reads version information from each plugin's plugin.json (locally or via GitHub API)
and updates the corresponding entry in marketplace.json.

Usage:
    python sync_marketplace_versions.py [--marketplace PATH] [--dry-run] [--check-only]

Exit codes:
    0 - Success (versions updated or already in sync)
    1 - Error (missing files, invalid JSON, network failure, etc.)
    2 - Out of sync (--check-only mode, versions differ)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def find_marketplace_json(start_path: Path) -> Path | None:
    """Find marketplace.json by checking common locations relative to start_path."""
    candidates = [
        start_path / ".claude-plugin" / "marketplace.json",
        start_path / "marketplace.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_json(path: Path) -> dict[str, Any] | None:
    """Load and parse a JSON file, returning None on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error loading {path}: {e}", file=sys.stderr)
        return None


def save_json(path: Path, data: dict[str, Any]) -> bool:
    """Save data to a JSON file with 2-space indent and trailing newline."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return True
    except OSError as e:
        print(f"Error saving {path}: {e}", file=sys.stderr)
        return False


def get_local_plugin_version(plugin_dir: Path) -> str | None:
    """Read version from a local plugin's plugin.json."""
    plugin_json_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not plugin_json_path.exists():
        return None
    data = load_json(plugin_json_path)
    if data is None:
        return None
    return data.get("version")


def get_remote_plugin_version(repo_url: str) -> str | None:
    """Fetch version from a plugin's plugin.json on GitHub via the gh CLI."""
    # Extract owner/repo from URL (handles https://github.com/owner/repo.git)
    repo_url = repo_url.rstrip("/").removesuffix(".git")
    parts = repo_url.split("/")
    if len(parts) < 2:
        return None
    owner_repo = f"{parts[-2]}/{parts[-1]}"

    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/contents/.claude-plugin/plugin.json",
             "--jq", ".content"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        import base64
        content = base64.b64decode(result.stdout.strip()).decode("utf-8")
        data = json.loads(content)
        return data.get("version")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        print(f"Warning: Could not fetch version from {owner_repo}: {e}", file=sys.stderr)
        return None


def resolve_marketplace_root(marketplace_path: Path) -> Path:
    """Given the path to marketplace.json, return the repository root directory."""
    if marketplace_path.parent.name == ".claude-plugin":
        return marketplace_path.parent.parent
    return marketplace_path.parent


def get_submodule_urls(marketplace_dir: Path) -> dict[str, str]:
    """Parse .gitmodules to build a mapping of submodule name -> remote URL."""
    gitmodules = marketplace_dir / ".gitmodules"
    if not gitmodules.exists():
        return {}
    urls: dict[str, str] = {}
    current_name = ""
    with open(gitmodules, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("[submodule"):
                current_name = line.split('"')[1] if '"' in line else ""
            elif line.startswith("url = ") and current_name:
                urls[current_name] = line.split("=", 1)[1].strip()
    return urls


def sync_versions(
    marketplace_path: Path,
    *,
    dry_run: bool = False,
    check_only: bool = False,
    verbose: bool = True,
) -> tuple[bool, list[str]]:
    """
    Sync plugin versions from submodules/GitHub to marketplace.json.

    Returns:
        (success, list_of_updated_plugin_names)
    """
    marketplace_dir = resolve_marketplace_root(marketplace_path)
    marketplace_data = load_json(marketplace_path)
    if marketplace_data is None:
        return False, []

    plugins = marketplace_data.get("plugins", [])
    if not plugins:
        if verbose:
            print("No plugins found in marketplace.json")
        return True, []

    submodule_urls = get_submodule_urls(marketplace_dir)
    updated_plugins: list[str] = []
    out_of_sync = False

    for plugin in plugins:
        plugin_name = plugin.get("name", "")
        if not plugin_name:
            continue

        # Try local directory first
        source = plugin.get("source", f"./{plugin_name}")
        if source.startswith("./"):
            plugin_dir = marketplace_dir / source[2:]
        else:
            plugin_dir = marketplace_dir / plugin_name

        actual_version: str | None = None
        if plugin_dir.exists():
            actual_version = get_local_plugin_version(plugin_dir)

        # Fall back to GitHub API if local version not available
        if actual_version is None:
            repo_url = plugin.get("repository", submodule_urls.get(plugin_name, ""))
            if repo_url:
                actual_version = get_remote_plugin_version(repo_url)

        if actual_version is None:
            if verbose:
                print(f"  [SKIP] {plugin_name}: could not determine version")
            continue

        marketplace_version = plugin.get("version", "")
        if actual_version != marketplace_version:
            if verbose:
                print(f"  [UPDATE] {plugin_name}: {marketplace_version} -> {actual_version}")
            if not check_only:
                plugin["version"] = actual_version
            updated_plugins.append(plugin_name)
            out_of_sync = True
        else:
            if verbose:
                print(f"  [OK] {plugin_name}: {actual_version}")

    # Save changes unless dry-run or check-only
    if updated_plugins and not dry_run and not check_only:
        if not save_json(marketplace_path, marketplace_data):
            return False, updated_plugins

    if check_only and out_of_sync:
        return False, updated_plugins

    return True, updated_plugins


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync plugin versions from submodules to marketplace.json"
    )
    parser.add_argument(
        "--marketplace", type=Path, default=None,
        help="Path to marketplace.json (auto-detected if not specified)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing to disk",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Exit with code 2 if any versions are out of sync (no writes)",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="Suppress output except errors",
    )
    args = parser.parse_args()

    marketplace_path = args.marketplace or find_marketplace_json(Path.cwd())
    if marketplace_path is None:
        print("Error: Could not find marketplace.json", file=sys.stderr)
        print("Use --marketplace to specify the path", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"Syncing versions in {marketplace_path}")
        if args.dry_run:
            print("(dry run - no changes will be made)")
        if args.check_only:
            print("(check only - no changes will be made)")

    success, updated = sync_versions(
        marketplace_path,
        dry_run=args.dry_run,
        check_only=args.check_only,
        verbose=not args.quiet,
    )

    if not success:
        if args.check_only and updated:
            if not args.quiet:
                print(f"\nOut of sync: {', '.join(updated)}")
            return 2
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

## generate-readme.py

Generates the marketplace README.md automatically from marketplace.json data,
including a Mermaid architecture diagram and an auto-generated plugin table.

**Install location:** `scripts/generate-readme.py`

```python
#!/usr/bin/env python3
"""
generate-readme.py - Generate marketplace README.md from marketplace.json

Reads marketplace.json and produces a complete README with:
  - Title and description
  - Mermaid architecture diagram
  - Auto-generated plugin table
  - Installation instructions
  - Developer setup guide
  - Last-updated timestamp

Usage:
    python generate-readme.py [--marketplace PATH] [--output PATH]

Exit codes:
    0 - Success
    1 - Error
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_marketplace(path: Path) -> dict[str, Any] | None:
    """Load and validate marketplace.json."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error loading {path}: {e}", file=sys.stderr)
        return None

    if "name" not in data:
        print(f"Error: marketplace.json missing 'name' field", file=sys.stderr)
        return None
    return data


def build_plugin_table(plugins: list[dict[str, Any]]) -> str:
    """Build a markdown table of plugins from the plugins array."""
    if not plugins:
        return "_No plugins registered yet._\n"

    lines = [
        "| Plugin | Version | Description | Repository |",
        "|--------|---------|-------------|------------|",
    ]
    for p in plugins:
        name = p.get("name", "unknown")
        version = p.get("version", "-")
        description = p.get("description", "-")
        repo = p.get("repository", "")
        repo_link = f"[source]({repo})" if repo else "-"
        lines.append(f"| **{name}** | `{version}` | {description} | {repo_link} |")

    return "\n".join(lines) + "\n"


def build_mermaid_diagram(marketplace_name: str, plugins: list[dict[str, Any]]) -> str:
    """Build a Mermaid architecture diagram showing marketplace structure."""
    nodes: list[str] = []
    links: list[str] = []

    nodes.append(f'    MP["{marketplace_name}"]')
    nodes.append('    MJ["marketplace.json"]')
    links.append("    MP --> MJ")

    for i, p in enumerate(plugins):
        node_id = f"P{i}"
        name = p.get("name", f"plugin-{i}")
        nodes.append(f'    {node_id}["{name}"]')
        links.append(f"    MJ --> {node_id}")

    diagram_lines = ["```mermaid", "graph TD"] + nodes + [""] + links + ["```"]
    return "\n".join(diagram_lines) + "\n"


def generate_readme(data: dict[str, Any]) -> str:
    """Generate the full README markdown from marketplace data."""
    name = data.get("name", "Plugin Marketplace")
    description = data.get("description", "A Claude Code plugin marketplace.")
    plugins = data.get("plugins", [])
    owner = data.get("owner", "")
    repo_url = data.get("repository", "")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sections: list[str] = []

    # Title and description
    sections.append(f"# {name}\n")
    sections.append(f"{description}\n")

    # Architecture diagram
    sections.append("## Architecture\n")
    sections.append(build_mermaid_diagram(name, plugins))

    # Plugin table
    sections.append("## Plugins\n")
    sections.append(build_plugin_table(plugins))

    # Installation instructions
    sections.append("## Installation\n")
    sections.append("### Quick Install (single plugin)\n")
    sections.append("```bash")
    sections.append("# Install a specific plugin from this marketplace")
    if repo_url:
        sections.append(f'claude plugin install "{repo_url}#<plugin-name>"')
    else:
        sections.append('claude plugin install "<marketplace-url>#<plugin-name>"')
    sections.append("```\n")

    sections.append("### Install All Plugins\n")
    sections.append("```bash")
    sections.append("# Clone the marketplace and install all plugins")
    if repo_url:
        sections.append(f"git clone --recurse-submodules {repo_url}")
    else:
        sections.append("git clone --recurse-submodules <marketplace-url>")
    sections.append("cd <marketplace-directory>")
    sections.append("# Each subdirectory is a plugin - install them individually")
    for p in plugins:
        pname = p.get("name", "plugin")
        sections.append(f'claude plugin install "./{pname}"')
    sections.append("```\n")

    # Developer setup
    sections.append("## Developer Setup\n")
    sections.append("```bash")
    sections.append("# Clone with submodules")
    if repo_url:
        sections.append(f"git clone --recurse-submodules {repo_url}")
    else:
        sections.append("git clone --recurse-submodules <marketplace-url>")
    sections.append("")
    sections.append("# Install validation tools")
    sections.append("pip install claude-plugins-validation")
    sections.append("")
    sections.append("# Validate the marketplace")
    sections.append("cpv validate-marketplace .")
    sections.append("")
    sections.append("# Sync plugin versions")
    sections.append("python scripts/sync_marketplace_versions.py")
    sections.append("")
    sections.append("# Set up git hooks")
    sections.append("python scripts/setup-hooks.py")
    sections.append("```\n")

    # Footer
    sections.append("---\n")
    if owner:
        sections.append(f"Maintained by **{owner}**.\n")
    sections.append(f"_Last updated: {timestamp}_\n")
    sections.append("_This README was auto-generated from marketplace.json._\n")

    return "\n".join(sections)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate marketplace README.md from marketplace.json"
    )
    parser.add_argument(
        "--marketplace", type=Path, default=None,
        help="Path to marketplace.json (searches current directory if not specified)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output path for README.md (defaults to same directory as marketplace.json)",
    )
    args = parser.parse_args()

    # Locate marketplace.json
    if args.marketplace:
        mp_path = args.marketplace
    else:
        for candidate in [
            Path.cwd() / ".claude-plugin" / "marketplace.json",
            Path.cwd() / "marketplace.json",
        ]:
            if candidate.exists():
                mp_path = candidate
                break
        else:
            print("Error: Could not find marketplace.json", file=sys.stderr)
            return 1

    data = load_marketplace(mp_path)
    if data is None:
        return 1

    readme_content = generate_readme(data)

    # Determine output path
    if args.output:
        out_path = args.output
    else:
        root = mp_path.parent
        if root.name == ".claude-plugin":
            root = root.parent
        out_path = root / "README.md"

    out_path.write_text(readme_content, encoding="utf-8")
    print(f"Generated {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## setup-hooks.py

Installs (or uninstalls) git hooks for marketplace validation.

**Install location:** `scripts/setup-hooks.py`

```python
#!/usr/bin/env python3
"""
setup-hooks.py - Install git hooks for marketplace validation

Creates pre-push and pre-commit hooks that validate the marketplace
before allowing changes to be pushed.

Usage:
    python setup-hooks.py [--repo-dir PATH] [--uninstall]

Exit codes:
    0 - Success
    1 - Error (not a git repo, permission denied, etc.)
"""

import argparse
import stat
import sys
from pathlib import Path

PRE_COMMIT_HOOK = '''\
#!/usr/bin/env bash
# Pre-commit hook: validate marketplace.json syntax
# Installed by setup-hooks.py

set -euo pipefail

MARKETPLACE_JSON=""
for candidate in ".claude-plugin/marketplace.json" "marketplace.json"; do
    if [ -f "$candidate" ]; then
        MARKETPLACE_JSON="$candidate"
        break
    fi
done

if [ -z "$MARKETPLACE_JSON" ]; then
    # No marketplace.json found - nothing to validate
    exit 0
fi

# Only validate if marketplace.json is staged
if git diff --cached --name-only | grep -q "marketplace.json"; then
    echo "Validating marketplace.json syntax..."
    if ! python3 -c "import json, sys; json.load(open('$MARKETPLACE_JSON'))" 2>/dev/null; then
        echo "ERROR: marketplace.json contains invalid JSON"
        echo "Fix syntax errors before committing."
        exit 1
    fi
    echo "marketplace.json syntax OK"
fi
'''

PRE_PUSH_HOOK = '''\
#!/usr/bin/env bash
# Pre-push hook: validate marketplace before pushing
# Installed by setup-hooks.py

set -euo pipefail

MARKETPLACE_JSON=""
for candidate in ".claude-plugin/marketplace.json" "marketplace.json"; do
    if [ -f "$candidate" ]; then
        MARKETPLACE_JSON="$candidate"
        break
    fi
done

if [ -z "$MARKETPLACE_JSON" ]; then
    echo "WARNING: No marketplace.json found - skipping validation"
    exit 0
fi

echo "Running marketplace validation..."

# Check JSON syntax
if ! python3 -c "import json, sys; json.load(open('$MARKETPLACE_JSON'))" 2>/dev/null; then
    echo "ERROR: marketplace.json contains invalid JSON"
    exit 1
fi

# Run full validation if cpv is available
if command -v cpv &>/dev/null; then
    if ! cpv validate-marketplace .; then
        echo "ERROR: Marketplace validation failed"
        echo "Fix validation errors before pushing."
        exit 1
    fi
elif [ -f "scripts/validate_marketplace.py" ]; then
    if ! python3 scripts/validate_marketplace.py .; then
        echo "ERROR: Marketplace validation failed"
        exit 1
    fi
else
    echo "NOTE: cpv not installed - only JSON syntax was checked"
fi

# Optionally check version sync
if [ -f "scripts/sync_marketplace_versions.py" ]; then
    if ! python3 scripts/sync_marketplace_versions.py --check-only --quiet; then
        echo "WARNING: Plugin versions may be out of sync"
        echo "Run: python scripts/sync_marketplace_versions.py"
    fi
fi

echo "Marketplace validation passed"
'''


def install_hook(hooks_dir: Path, hook_name: str, hook_content: str) -> bool:
    """Write a hook file and make it executable."""
    hook_path = hooks_dir / hook_name
    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8")
        if "setup-hooks.py" in existing:
            # Our hook already installed - overwrite with latest version
            pass
        else:
            print(f"  WARNING: {hook_name} already exists (not ours) - skipping")
            print(f"  To overwrite, remove {hook_path} and re-run this script")
            return False

    hook_path.write_text(hook_content, encoding="utf-8")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  Installed {hook_path}")
    return True


def uninstall_hook(hooks_dir: Path, hook_name: str) -> bool:
    """Remove a hook if it was installed by this script."""
    hook_path = hooks_dir / hook_name
    if not hook_path.exists():
        print(f"  {hook_name}: not found (nothing to remove)")
        return True
    content = hook_path.read_text(encoding="utf-8")
    if "setup-hooks.py" not in content:
        print(f"  {hook_name}: not installed by setup-hooks.py - skipping")
        return False
    hook_path.unlink()
    print(f"  Removed {hook_path}")
    return True


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Install git hooks for marketplace validation"
    )
    parser.add_argument(
        "--repo-dir", type=Path, default=Path.cwd(),
        help="Path to the git repository root (default: current directory)",
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Remove hooks installed by this script",
    )
    args = parser.parse_args()

    git_dir = args.repo_dir / ".git"
    if not git_dir.is_dir():
        print(f"Error: {args.repo_dir} is not a git repository", file=sys.stderr)
        return 1

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    hooks = {
        "pre-commit": PRE_COMMIT_HOOK,
        "pre-push": PRE_PUSH_HOOK,
    }

    if args.uninstall:
        print("Uninstalling marketplace hooks...")
        for name in hooks:
            uninstall_hook(hooks_dir, name)
        print("Done.")
        return 0

    print("Installing marketplace hooks...")
    all_ok = True
    for name, content in hooks.items():
        if not install_hook(hooks_dir, name, content):
            all_ok = False

    if all_ok:
        print("All hooks installed successfully.")
    else:
        print("Some hooks were skipped (see warnings above).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## pre-push-hook.py

Standalone Python pre-push hook for marketplace validation.
Can be used directly as `.git/hooks/pre-push` or kept in `scripts/` and symlinked.

**Install location:** `.git/hooks/pre-push` or `scripts/pre-push-hook.py`

```python
#!/usr/bin/env python3
"""
pre-push-hook.py - Pre-push validation for marketplace repositories

Validates marketplace.json structure and plugin entries before allowing a push.
Can be installed directly as .git/hooks/pre-push or called from a shell hook.

Usage:
    python pre-push-hook.py           # Run validation
    python pre-push-hook.py --strict  # Fail on warnings too

Exit codes:
    0 - Validation passed
    1 - Validation failed (errors found)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REQUIRED_MARKETPLACE_FIELDS = {"name", "version", "plugins"}
REQUIRED_PLUGIN_FIELDS = {"name", "version", "description"}


def find_marketplace_json() -> Path | None:
    """Locate marketplace.json in the repository."""
    for candidate in [
        Path(".claude-plugin/marketplace.json"),
        Path("marketplace.json"),
    ]:
        if candidate.exists():
            return candidate
    return None


def load_and_validate_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Load JSON and return (data, errors). Errors list is empty on success."""
    errors: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON in {path}: {e}")
        return None, errors
    except FileNotFoundError:
        errors.append(f"File not found: {path}")
        return None, errors

    if not isinstance(data, dict):
        errors.append(f"{path} root must be a JSON object")
        return None, errors

    return data, errors


def validate_marketplace(data: dict[str, Any], strict: bool = False) -> list[str]:
    """Validate marketplace.json structure and plugin entries."""
    errors: list[str] = []
    warnings: list[str] = []

    # Check required top-level fields
    missing_fields = REQUIRED_MARKETPLACE_FIELDS - set(data.keys())
    if missing_fields:
        errors.append(f"Missing required fields: {', '.join(sorted(missing_fields))}")

    # Validate plugins array
    plugins = data.get("plugins")
    if plugins is None:
        errors.append("No 'plugins' array found")
        return errors + (warnings if strict else [])

    if not isinstance(plugins, list):
        errors.append("'plugins' must be an array")
        return errors + (warnings if strict else [])

    seen_names: set[str] = set()
    for i, plugin in enumerate(plugins):
        prefix = f"plugins[{i}]"

        if not isinstance(plugin, dict):
            errors.append(f"{prefix}: must be an object")
            continue

        # Check required plugin fields
        missing_plugin = REQUIRED_PLUGIN_FIELDS - set(plugin.keys())
        if missing_plugin:
            errors.append(f"{prefix}: missing fields: {', '.join(sorted(missing_plugin))}")

        # Check for duplicate names
        pname = plugin.get("name", "")
        if pname:
            if pname in seen_names:
                errors.append(f"{prefix}: duplicate plugin name '{pname}'")
            seen_names.add(pname)

        # Warn on missing optional but recommended fields
        if "repository" not in plugin:
            warnings.append(f"{prefix} ({pname}): no 'repository' URL")

    result = list(errors)
    if strict:
        result.extend(warnings)
    return result


def check_version_sync() -> list[str]:
    """Optionally check if versions are in sync (non-blocking warning)."""
    sync_script = Path("scripts/sync_marketplace_versions.py")
    if not sync_script.exists():
        return []

    try:
        result = subprocess.run(
            [sys.executable, str(sync_script), "--check-only", "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 2:
            return ["Plugin versions are out of sync - run sync_marketplace_versions.py"]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return []


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Pre-push marketplace validation")
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as errors",
    )
    args = parser.parse_args()

    mp_path = find_marketplace_json()
    if mp_path is None:
        print("No marketplace.json found - skipping validation")
        return 0

    print(f"Validating {mp_path}...")

    data, parse_errors = load_and_validate_json(mp_path)
    if parse_errors:
        for err in parse_errors:
            print(f"  ERROR: {err}")
        return 1

    assert data is not None
    validation_errors = validate_marketplace(data, strict=args.strict)
    sync_warnings = check_version_sync()

    if validation_errors:
        for err in validation_errors:
            print(f"  ERROR: {err}")
        return 1

    for warn in sync_warnings:
        print(f"  WARNING: {warn}")

    plugin_count = len(data.get("plugins", []))
    print(f"  OK: {plugin_count} plugin(s) validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## push-plugins.sh

Master orchestration script for pushing updates to all plugins and the marketplace.
Iterates over plugins, validates, optionally bumps versions, and pushes everything.

**Install location:** `scripts/push-plugins.sh`

```bash
#!/usr/bin/env bash
# push-plugins.sh - Push updates to all plugins and the marketplace
#
# Iterates over plugin directories (from marketplace.json or subdirectories),
# validates each plugin, optionally bumps versions, commits, and pushes.
# After all plugins are updated, syncs marketplace versions, regenerates
# the README, validates, commits, and pushes the marketplace itself.
#
# Usage:
#   ./push-plugins.sh [OPTIONS]
#
# Options:
#   --dry-run           Show what would happen without making changes
#   --skip-validation   Skip plugin and marketplace validation steps
#   --message MSG       Custom commit message (default: "Update plugins")
#   --help              Show this help text
#
# Exit codes:
#   0 - Success
#   1 - Error

set -euo pipefail

# -- Color output helpers --
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# -- Default configuration --
DRY_RUN=false
SKIP_VALIDATION=false
COMMIT_MSG="Update plugins"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# -- Parse arguments --
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --skip-validation)
            SKIP_VALIDATION=true
            shift
            ;;
        --message)
            COMMIT_MSG="$2"
            shift 2
            ;;
        --help)
            head -25 "$0" | tail -20
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# -- Locate marketplace.json --
MARKETPLACE_JSON=""
for candidate in "$REPO_ROOT/.claude-plugin/marketplace.json" "$REPO_ROOT/marketplace.json"; do
    if [ -f "$candidate" ]; then
        MARKETPLACE_JSON="$candidate"
        break
    fi
done

if [ -z "$MARKETPLACE_JSON" ]; then
    error "Could not find marketplace.json in $REPO_ROOT"
    exit 1
fi

info "Marketplace: $MARKETPLACE_JSON"
if $DRY_RUN; then
    warn "DRY RUN - no changes will be made"
fi

# -- Discover plugin directories --
# Uses jq to parse marketplace.json if available, otherwise falls back to subdirectories
get_plugin_dirs() {
    if command -v jq &>/dev/null; then
        jq -r '.plugins[]? | .source // ("./"+.name)' "$MARKETPLACE_JSON" 2>/dev/null | while read -r src; do
            local dir="${src#./}"
            if [ -d "$REPO_ROOT/$dir" ]; then
                echo "$dir"
            fi
        done
    else
        # Fallback: find directories that contain .claude-plugin/plugin.json
        for dir in "$REPO_ROOT"/*/; do
            dir_name="$(basename "$dir")"
            if [ -f "$dir/.claude-plugin/plugin.json" ]; then
                echo "$dir_name"
            fi
        done
    fi
}

# -- Validate a single plugin --
validate_plugin() {
    local plugin_dir="$1"
    local plugin_path="$REPO_ROOT/$plugin_dir"

    if ! [ -f "$plugin_path/.claude-plugin/plugin.json" ]; then
        warn "$plugin_dir: no plugin.json found - skipping validation"
        return 1
    fi

    # Check JSON syntax
    if ! python3 -c "import json; json.load(open('$plugin_path/.claude-plugin/plugin.json'))" 2>/dev/null; then
        error "$plugin_dir: plugin.json has invalid JSON"
        return 1
    fi

    # Run cpv if available
    if command -v cpv &>/dev/null; then
        if ! cpv validate-plugin "$plugin_path" --quiet 2>/dev/null; then
            error "$plugin_dir: validation failed"
            return 1
        fi
    fi

    return 0
}

# -- Push a single plugin --
push_plugin() {
    local plugin_dir="$1"
    local plugin_path="$REPO_ROOT/$plugin_dir"

    if ! [ -d "$plugin_path/.git" ]; then
        warn "$plugin_dir: not a git repo (not a submodule?) - skipping push"
        return 0
    fi

    cd "$plugin_path"

    # Check for uncommitted changes
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        info "$plugin_dir: staging and committing changes..."
        if ! $DRY_RUN; then
            git add -A
            git commit -m "$COMMIT_MSG"
        fi
    fi

    # Push if there are unpushed commits
    local branch
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
    if [ -z "$branch" ]; then
        warn "$plugin_dir: could not determine branch"
        cd "$REPO_ROOT"
        return 1
    fi

    local local_hash remote_hash
    local_hash="$(git rev-parse HEAD 2>/dev/null)"
    remote_hash="$(git rev-parse "origin/$branch" 2>/dev/null || echo "")"

    if [ "$local_hash" != "$remote_hash" ]; then
        info "$plugin_dir: pushing to origin/$branch..."
        if ! $DRY_RUN; then
            git push origin "$branch"
        fi
        success "$plugin_dir: pushed"
    else
        success "$plugin_dir: already up to date"
    fi

    cd "$REPO_ROOT"
}

# -- Main workflow --
info "Discovering plugins..."
PLUGIN_DIRS=()
while IFS= read -r dir; do
    PLUGIN_DIRS+=("$dir")
done < <(get_plugin_dirs)

if [ ${#PLUGIN_DIRS[@]} -eq 0 ]; then
    warn "No plugin directories found"
    exit 0
fi

info "Found ${#PLUGIN_DIRS[@]} plugin(s): ${PLUGIN_DIRS[*]}"

# Step 1: Validate all plugins
if ! $SKIP_VALIDATION; then
    info "Validating plugins..."
    VALIDATION_FAILED=false
    for plugin_dir in "${PLUGIN_DIRS[@]}"; do
        if ! validate_plugin "$plugin_dir"; then
            VALIDATION_FAILED=true
        fi
    done

    if $VALIDATION_FAILED; then
        error "Some plugins failed validation. Fix errors or use --skip-validation."
        exit 1
    fi
    success "All plugins validated"
fi

# Step 2: Push each plugin
info "Pushing plugins..."
for plugin_dir in "${PLUGIN_DIRS[@]}"; do
    push_plugin "$plugin_dir"
done

# Step 3: Sync marketplace versions
if [ -f "$SCRIPT_DIR/sync_marketplace_versions.py" ]; then
    info "Syncing marketplace versions..."
    SYNC_ARGS=("$SCRIPT_DIR/sync_marketplace_versions.py" "--marketplace" "$MARKETPLACE_JSON")
    if $DRY_RUN; then
        SYNC_ARGS+=("--dry-run")
    fi
    python3 "${SYNC_ARGS[@]}"
fi

# Step 4: Regenerate README
if [ -f "$SCRIPT_DIR/generate-readme.py" ]; then
    info "Regenerating README..."
    GEN_ARGS=("$SCRIPT_DIR/generate-readme.py" "--marketplace" "$MARKETPLACE_JSON")
    if ! $DRY_RUN; then
        python3 "${GEN_ARGS[@]}"
    fi
fi

# Step 5: Validate the marketplace itself
if ! $SKIP_VALIDATION; then
    info "Validating marketplace..."
    if command -v cpv &>/dev/null; then
        if ! cpv validate-marketplace "$REPO_ROOT" --quiet 2>/dev/null; then
            error "Marketplace validation failed after sync"
            exit 1
        fi
    fi
    success "Marketplace validation passed"
fi

# Step 6: Commit and push the marketplace
cd "$REPO_ROOT"
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    info "Committing marketplace changes..."
    if ! $DRY_RUN; then
        git add -A
        git commit -m "chore: sync plugin versions and regenerate README"
    fi
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
LOCAL="$(git rev-parse HEAD 2>/dev/null)"
REMOTE="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "")"

if [ "$LOCAL" != "$REMOTE" ]; then
    info "Pushing marketplace to origin/$BRANCH..."
    if ! $DRY_RUN; then
        git push origin "$BRANCH"
    fi
    success "Marketplace pushed"
else
    success "Marketplace already up to date"
fi

echo ""
success "All done!"
```
