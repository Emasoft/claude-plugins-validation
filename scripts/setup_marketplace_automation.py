#!/usr/bin/env python3
"""
setup_marketplace_automation.py - Set up CI/CD automation for marketplace repos

This script configures automatic submodule updates and version syncing for
Claude Code marketplace repositories.

What it sets up:
  1. GitHub Actions workflow to auto-update submodules
  2. Python script to sync versions from plugins to marketplace.json
  3. README with architecture diagram
  4. (Optional) Workflow templates for plugin repos to notify marketplace

Usage:
    python setup_marketplace_automation.py [--marketplace-dir PATH] [--dry-run]
    python setup_marketplace_automation.py --marketplace-dir PATH --full
    python setup_marketplace_automation.py --marketplace-dir PATH --check-plugins
    python setup_marketplace_automation.py --pat-instructions

Exit codes:
    0 - Success
    1 - Error
"""

import argparse
import shutil
import sys
from pathlib import Path
from typing import TypedDict


class PluginWorkflowStatus(TypedDict):
    """Type for plugin notification workflow check result."""

    has_workflow: bool
    workflow_path: str
    needs_configuration: bool


class PluginNotificationResult(TypedDict):
    """Type for setup_plugin_notifications result."""

    missing: list[str]
    needs_config: list[str]
    configured: list[str]


class WorkflowStatusDict(TypedDict):
    """Type for workflow status in get_full_status."""

    exists: bool
    path: str


class WorkflowsDict(TypedDict):
    """Type for workflows section in FullStatus."""

    update_submodules: WorkflowStatusDict


class ScriptsDict(TypedDict):
    """Type for scripts section in FullStatus."""

    sync_versions: WorkflowStatusDict
    notify_template: WorkflowStatusDict


class ReadmeDict(TypedDict):
    """Type for readme section in FullStatus."""

    exists: bool
    has_diagram: bool
    path: str


class PluginsDict(TypedDict):
    """Type for plugins section in FullStatus."""

    total: int
    configured: int
    needs_config: int
    missing: int


class FullStatus(TypedDict):
    """Type for get_full_status return value."""

    marketplace_dir: str
    is_valid_marketplace: bool
    workflows: WorkflowsDict
    scripts: ScriptsDict
    readme: ReadmeDict
    plugins: PluginsDict


def get_template_dir() -> Path:
    """Get the templates directory."""
    script_dir = Path(__file__).resolve().parent
    plugin_dir = script_dir.parent
    return plugin_dir / "templates"


def get_submodule_paths(marketplace_dir: Path) -> list[dict[str, str]]:
    """
    Get list of git submodules in the marketplace directory.

    Returns:
        List of dicts with 'name' and 'path' keys for each submodule
    """
    gitmodules_path = marketplace_dir / ".gitmodules"
    if not gitmodules_path.exists():
        return []

    submodules = []
    current_submodule: dict[str, str] = {}

    with open(gitmodules_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("[submodule"):
                if current_submodule:
                    submodules.append(current_submodule)
                # Extract name from [submodule "name"]
                name = line.split('"')[1] if '"' in line else ""
                current_submodule = {"name": name, "path": ""}
            elif line.startswith("path = "):
                current_submodule["path"] = line.split("=", 1)[1].strip()
            elif line.startswith("url = "):
                current_submodule["url"] = line.split("=", 1)[1].strip()

    if current_submodule:
        submodules.append(current_submodule)

    return submodules


def check_plugin_notification_workflow(plugin_path: Path) -> PluginWorkflowStatus:
    """
    Check if a plugin has the notification workflow configured.

    Args:
        plugin_path: Path to the plugin directory

    Returns:
        Dict with 'has_workflow', 'workflow_path', and 'needs_configuration' keys
    """
    workflow_path = plugin_path / ".github" / "workflows" / "notify-marketplace.yml"
    result: PluginWorkflowStatus = {
        "has_workflow": workflow_path.exists(),
        "workflow_path": str(workflow_path),
        "needs_configuration": False,
    }

    if workflow_path.exists():
        # Check if it still has placeholder values
        content = workflow_path.read_text()
        if "YOUR_GITHUB_USERNAME" in content or "YOUR_MARKETPLACE_REPO_NAME" in content:
            result["needs_configuration"] = True

    return result


def setup_plugin_notifications(
    marketplace_dir: Path,
    dry_run: bool = False,  # noqa: ARG001  # Kept for API compatibility
    verbose: bool = True,
) -> PluginNotificationResult:
    """
    Check all plugin submodules for notification workflow status.

    Since plugins are separate git repos, this function only REPORTS status.
    Users must manually copy the template to each plugin repo.

    Args:
        marketplace_dir: Path to the marketplace repository
        dry_run: Kept for API compatibility (unused, this function only reports)
        verbose: If True, print progress

    Returns:
        Dict with 'missing', 'needs_config', and 'configured' lists of plugin names
    """
    del dry_run  # Unused but kept for API compatibility
    result: PluginNotificationResult = {
        "missing": [],
        "needs_config": [],
        "configured": [],
    }

    submodules = get_submodule_paths(marketplace_dir)

    if not submodules:
        if verbose:
            print("\nNo submodules found in marketplace.")
        return result

    if verbose:
        print(f"\nChecking {len(submodules)} plugin submodules for notification workflow...\n")

    template_dir = get_template_dir()
    template_path = template_dir / "github-workflows" / "notify-marketplace.yml"

    for submodule in submodules:
        plugin_path = marketplace_dir / submodule["path"]
        plugin_name = submodule["name"]

        if not plugin_path.exists():
            if verbose:
                print(f"  [SKIP] {plugin_name} - submodule not initialized")
            continue

        status = check_plugin_notification_workflow(plugin_path)

        if not status["has_workflow"]:
            result["missing"].append(plugin_name)
            if verbose:
                print(f"  [MISSING] {plugin_name}")
                print(f"            Copy template to: {plugin_path}/.github/workflows/notify-marketplace.yml")
        elif status["needs_configuration"]:
            result["needs_config"].append(plugin_name)
            if verbose:
                print(f"  [NEEDS CONFIG] {plugin_name}")
                print("                 Update MARKETPLACE_OWNER and MARKETPLACE_REPO in the workflow")
        else:
            result["configured"].append(plugin_name)
            if verbose:
                print(f"  [OK] {plugin_name}")

    if verbose:
        print("\n" + "=" * 60)
        print("Plugin Notification Workflow Summary")
        print("=" * 60)
        print(f"  Configured:        {len(result['configured'])}")
        print(f"  Needs Config:      {len(result['needs_config'])}")
        print(f"  Missing Workflow:  {len(result['missing'])}")

        if result["missing"]:
            print("\n  To set up missing plugins:")
            print(f"  1. Copy {template_path}")
            print("  2. Place in each plugin's .github/workflows/notify-marketplace.yml")
            print("  3. Update MARKETPLACE_OWNER and MARKETPLACE_REPO values")
            print("  4. Add MARKETPLACE_PAT secret to each plugin repo")

    return result


def setup_readme_with_diagram(
    marketplace_dir: Path,
    dry_run: bool = False,
    verbose: bool = True,
) -> bool:
    """
    Check and set up README.md with architecture diagram.

    Args:
        marketplace_dir: Path to the marketplace repository
        dry_run: If True, show what would be done without making changes
        verbose: If True, print progress

    Returns:
        True if successful or already configured, False on error
    """
    template_dir = get_template_dir()
    readme_template = template_dir / "README-marketplace.md"
    readme_path = marketplace_dir / "README.md"

    if verbose:
        print("\nChecking README.md configuration...")

    # Architecture diagram to append if missing
    architecture_section = """

## Architecture

```mermaid
flowchart TB
    subgraph Marketplace["Marketplace Repository"]
        MJ[marketplace.json]
        UW[update-submodules.yml]
        SV[sync_marketplace_versions.py]
    end

    subgraph Plugins["Plugin Repositories"]
        P1[plugin-a]
        P2[plugin-b]
        P3[plugin-n]
    end

    subgraph Automation["GitHub Actions"]
        CRON[Scheduled Check<br/>every 6 hours]
        DISPATCH[Repository Dispatch<br/>plugin-updated event]
    end

    P1 -->|submodule| Marketplace
    P2 -->|submodule| Marketplace
    P3 -->|submodule| Marketplace

    P1 -->|notify-marketplace.yml| DISPATCH
    P2 -->|notify-marketplace.yml| DISPATCH
    P3 -->|notify-marketplace.yml| DISPATCH

    CRON -->|triggers| UW
    DISPATCH -->|triggers| UW
    UW -->|runs| SV
    SV -->|updates| MJ
```

### How It Works

1. **Plugin Update**: When a plugin repo pushes changes, its `notify-marketplace.yml` workflow sends a repository dispatch event
2. **Marketplace Update**: The marketplace's `update-submodules.yml` workflow triggers (via dispatch or scheduled cron)
3. **Version Sync**: The `sync_marketplace_versions.py` script reads each plugin's version and updates `marketplace.json`
4. **Auto Commit**: Changes are committed and pushed automatically

"""

    if not readme_path.exists():
        # README doesn't exist - copy template if available
        if readme_template.exists():
            if verbose:
                print("  [CREATE] README.md from template")
            if not dry_run:
                shutil.copy2(readme_template, readme_path)
            return True
        else:
            # Create a basic README with the diagram
            if verbose:
                print("  [CREATE] README.md with architecture diagram")
            if not dry_run:
                marketplace_name = marketplace_dir.name
                content = f"# {marketplace_name}\n\nClaude Code plugin marketplace.\n{architecture_section}"
                readme_path.write_text(content)
            return True

    # README exists - check for mermaid diagram
    content = readme_path.read_text()

    if "```mermaid" in content:
        if verbose:
            print("  [OK] README.md already contains mermaid diagram")
        return True

    # No diagram - offer to append
    if verbose:
        print("  [UPDATE] README.md - will append architecture diagram")

    if not dry_run:
        with open(readme_path, "a") as f:
            f.write(architecture_section)

    return True


def print_pat_setup_instructions() -> None:
    """Print clear instructions for setting up GitHub PAT for marketplace notifications."""
    print("""
================================================================================
GitHub Personal Access Token (PAT) Setup Instructions
================================================================================

To enable automatic marketplace updates when plugins change, you need to set up
a GitHub Personal Access Token (PAT) in each plugin repository.

STEP 1: Create a GitHub PAT
---------------------------
1. Go to https://github.com/settings/tokens
2. Click "Generate new token" -> "Generate new token (classic)"
3. Give it a descriptive name (e.g., "Marketplace Notification")
4. Select the 'repo' scope (Full control of private repositories)
   - This is needed to trigger workflows in other repos
5. Set an appropriate expiration (recommend 90 days, set reminder to renew)
6. Click "Generate token"
7. IMPORTANT: Copy the token immediately - you won't see it again!

STEP 2: Add PAT as Secret to Each Plugin Repository
----------------------------------------------------
For EACH plugin repository that should notify the marketplace:

1. Go to the plugin repo on GitHub
2. Click Settings -> Secrets and variables -> Actions
3. Click "New repository secret"
4. Name: MARKETPLACE_PAT
5. Value: Paste the PAT you created
6. Click "Add secret"

STEP 3: Configure the Notification Workflow
-------------------------------------------
In each plugin's .github/workflows/notify-marketplace.yml, update:

  env:
    MARKETPLACE_OWNER: 'your-github-username'    # <-- Update this
    MARKETPLACE_REPO: 'your-marketplace-repo'    # <-- Update this

Example:
  env:
    MARKETPLACE_OWNER: 'Emasoft'
    MARKETPLACE_REPO: 'claude-plugins-marketplace'

STEP 4: Test the Setup
----------------------
1. Make a small change to a plugin (e.g., bump version in plugin.json)
2. Commit and push to main/master
3. Check the plugin repo's Actions tab for the "Notify Marketplace" workflow
4. Check the marketplace repo's Actions tab for the triggered update

TROUBLESHOOTING
---------------
- "Resource not accessible by integration": PAT missing 'repo' scope
- Workflow not triggering: Check the 'paths' filter in notify-marketplace.yml
- 404 errors: Verify MARKETPLACE_OWNER and MARKETPLACE_REPO are correct

================================================================================
""")


def get_full_status(
    marketplace_dir: Path,
    verbose: bool = True,
) -> FullStatus:
    """
    Get complete status of marketplace automation setup.

    Args:
        marketplace_dir: Path to the marketplace repository
        verbose: If True, print detailed status

    Returns:
        Dict with status information for all components
    """
    status: FullStatus = {
        "marketplace_dir": str(marketplace_dir),
        "is_valid_marketplace": False,
        "workflows": {
            "update_submodules": {"exists": False, "path": ""},
        },
        "scripts": {
            "sync_versions": {"exists": False, "path": ""},
            "notify_template": {"exists": False, "path": ""},
        },
        "readme": {
            "exists": False,
            "has_diagram": False,
            "path": "",
        },
        "plugins": {
            "total": 0,
            "configured": 0,
            "needs_config": 0,
            "missing": 0,
        },
    }

    # Check marketplace validity
    marketplace_json = marketplace_dir / ".claude-plugin" / "marketplace.json"
    if not marketplace_json.exists():
        marketplace_json = marketplace_dir / "marketplace.json"
    status["is_valid_marketplace"] = marketplace_json.exists()

    if not status["is_valid_marketplace"]:
        if verbose:
            print(f"Error: Not a valid marketplace directory: {marketplace_dir}")
        return status

    # Check workflows
    workflow_path = marketplace_dir / ".github" / "workflows" / "update-submodules.yml"
    status["workflows"]["update_submodules"]["exists"] = workflow_path.exists()
    status["workflows"]["update_submodules"]["path"] = str(workflow_path)

    # Check scripts
    sync_script = marketplace_dir / "scripts" / "sync_marketplace_versions.py"
    status["scripts"]["sync_versions"]["exists"] = sync_script.exists()
    status["scripts"]["sync_versions"]["path"] = str(sync_script)

    notify_template = marketplace_dir / "scripts" / "notify-marketplace.yml.template"
    status["scripts"]["notify_template"]["exists"] = notify_template.exists()
    status["scripts"]["notify_template"]["path"] = str(notify_template)

    # Check README
    readme_path = marketplace_dir / "README.md"
    status["readme"]["exists"] = readme_path.exists()
    status["readme"]["path"] = str(readme_path)
    if readme_path.exists():
        content = readme_path.read_text()
        status["readme"]["has_diagram"] = "```mermaid" in content

    # Check plugins
    submodules = get_submodule_paths(marketplace_dir)
    status["plugins"]["total"] = len(submodules)

    for submodule in submodules:
        plugin_path = marketplace_dir / submodule["path"]
        if plugin_path.exists():
            plugin_status = check_plugin_notification_workflow(plugin_path)
            if not plugin_status["has_workflow"]:
                status["plugins"]["missing"] += 1
            elif plugin_status["needs_configuration"]:
                status["plugins"]["needs_config"] += 1
            else:
                status["plugins"]["configured"] += 1

    if verbose:
        print("\n" + "=" * 60)
        print("Marketplace Automation Status")
        print("=" * 60)
        print(f"\nMarketplace: {marketplace_dir}")
        print(f"Valid: {'Yes' if status['is_valid_marketplace'] else 'No'}")

        print("\n--- Marketplace Components ---")
        wf_status = "[OK]" if status["workflows"]["update_submodules"]["exists"] else "[MISSING]"
        print(f"  {wf_status} .github/workflows/update-submodules.yml")

        sc_status = "[OK]" if status["scripts"]["sync_versions"]["exists"] else "[MISSING]"
        print(f"  {sc_status} scripts/sync_marketplace_versions.py")

        nt_status = "[OK]" if status["scripts"]["notify_template"]["exists"] else "[MISSING]"
        print(f"  {nt_status} scripts/notify-marketplace.yml.template")

        if status["readme"]["exists"]:
            dg_status = "[OK]" if status["readme"]["has_diagram"] else "[NO DIAGRAM]"
            print(f"  {dg_status} README.md")
        else:
            print("  [MISSING] README.md")

        print("\n--- Plugin Notification Status ---")
        print(f"  Total plugins:     {status['plugins']['total']}")
        print(f"  Configured:        {status['plugins']['configured']}")
        print(f"  Needs config:      {status['plugins']['needs_config']}")
        print(f"  Missing workflow:  {status['plugins']['missing']}")

    return status


def setup_marketplace_automation(
    marketplace_dir: Path,
    dry_run: bool = False,
    verbose: bool = True,
) -> bool:
    """
    Set up automation for a marketplace repository.

    Args:
        marketplace_dir: Path to the marketplace repository
        dry_run: If True, show what would be done without making changes
        verbose: If True, print progress

    Returns:
        True if successful, False otherwise
    """
    template_dir = get_template_dir()

    if not template_dir.exists():
        print(f"Error: Templates directory not found: {template_dir}", file=sys.stderr)
        return False

    # Verify this is a marketplace
    marketplace_json = marketplace_dir / ".claude-plugin" / "marketplace.json"
    if not marketplace_json.exists():
        marketplace_json = marketplace_dir / "marketplace.json"
    if not marketplace_json.exists():
        print(f"Error: Not a marketplace directory (no marketplace.json): {marketplace_dir}", file=sys.stderr)
        return False

    if verbose:
        print(f"Setting up automation for: {marketplace_dir}")
        if dry_run:
            print("(dry run - no changes will be made)\n")

    # 1. Set up .github/workflows directory
    workflows_dir = marketplace_dir / ".github" / "workflows"
    if not dry_run:
        workflows_dir.mkdir(parents=True, exist_ok=True)

    # 2. Copy update-submodules.yml workflow
    src_workflow = template_dir / "github-workflows" / "update-submodules.yml"
    dst_workflow = workflows_dir / "update-submodules.yml"

    if src_workflow.exists():
        if verbose:
            status = "[EXISTS]" if dst_workflow.exists() else "[CREATE]"
            print(f"  {status} .github/workflows/update-submodules.yml")
        if not dry_run:
            shutil.copy2(src_workflow, dst_workflow)
    else:
        print("  [SKIP] update-submodules.yml template not found", file=sys.stderr)

    # 3. Set up scripts directory
    scripts_dir = marketplace_dir / "scripts"
    if not dry_run:
        scripts_dir.mkdir(parents=True, exist_ok=True)

    # 4. Copy sync_marketplace_versions.py script
    src_script = template_dir / "scripts" / "sync_marketplace_versions.py"
    dst_script = scripts_dir / "sync_marketplace_versions.py"

    if src_script.exists():
        if verbose:
            status = "[EXISTS]" if dst_script.exists() else "[CREATE]"
            print(f"  {status} scripts/sync_marketplace_versions.py")
        if not dry_run:
            shutil.copy2(src_script, dst_script)
            # Make executable
            dst_script.chmod(dst_script.stat().st_mode | 0o111)
    else:
        print("  [SKIP] sync_marketplace_versions.py template not found", file=sys.stderr)

    # 5. Copy notify-marketplace.yml template (for reference)
    src_notify = template_dir / "github-workflows" / "notify-marketplace.yml"
    dst_notify = scripts_dir / "notify-marketplace.yml.template"

    if src_notify.exists():
        if verbose:
            status = "[EXISTS]" if dst_notify.exists() else "[CREATE]"
            print(f"  {status} scripts/notify-marketplace.yml.template")
            print("         (Copy to each plugin repo's .github/workflows/ and configure)")
        if not dry_run:
            shutil.copy2(src_notify, dst_notify)

    if verbose:
        print("\n" + "=" * 60)
        print("Setup complete!")
        print("=" * 60)
        print("\nNext steps:")
        print("  1. Review the generated files")
        print("  2. Commit and push to enable the workflow")
        print("  3. (Optional) Set up plugin notification:")
        print("     - Create a PAT with 'repo' scope")
        print("     - Add as MARKETPLACE_PAT secret in each plugin repo")
        print("     - Copy notify-marketplace.yml.template to each plugin")
        print("\nThe workflow will:")
        print("  - Run every 6 hours to check for updates")
        print("  - Auto-update submodules to latest commits")
        print("  - Sync versions in marketplace.json")
        print("  - Commit and push changes automatically")

    return True


def run_full_pipeline(
    marketplace_dir: Path,
    dry_run: bool = False,
    verbose: bool = True,
) -> bool:
    """
    Run complete marketplace automation setup pipeline.

    This runs all setup steps in order:
    1. Set up marketplace workflows (update-submodules.yml)
    2. Set up sync scripts (sync_marketplace_versions.py)
    3. Set up README with architecture diagram
    4. Check plugin notification status
    5. Print PAT instructions

    Args:
        marketplace_dir: Path to the marketplace repository
        dry_run: If True, show what would be done without making changes
        verbose: If True, print progress

    Returns:
        True if all steps successful, False otherwise
    """
    if verbose:
        print("=" * 60)
        print("Full Marketplace Automation Pipeline")
        print("=" * 60)
        if dry_run:
            print("(dry run - no changes will be made)\n")

    # Step 1 & 2: Set up marketplace workflows and scripts
    if verbose:
        print("\n[Step 1/5] Setting up marketplace workflows and scripts...")
    success = setup_marketplace_automation(
        marketplace_dir,
        dry_run=dry_run,
        verbose=verbose,
    )

    if not success:
        return False

    # Step 3: Set up README with diagram
    if verbose:
        print("\n[Step 3/5] Setting up README with architecture diagram...")
    setup_readme_with_diagram(
        marketplace_dir,
        dry_run=dry_run,
        verbose=verbose,
    )

    # Step 4: Check plugin notification status
    if verbose:
        print("\n[Step 4/5] Checking plugin notification workflows...")
    setup_plugin_notifications(
        marketplace_dir,
        dry_run=dry_run,
        verbose=verbose,
    )

    # Step 5: Print PAT instructions
    if verbose:
        print("\n[Step 5/5] PAT Setup Instructions")
    print_pat_setup_instructions()

    if verbose:
        print("\n" + "=" * 60)
        print("Full Pipeline Complete!")
        print("=" * 60)
        if dry_run:
            print("\nThis was a dry run. Run without --dry-run to apply changes.")

    return True


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Set up CI/CD automation for marketplace repos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script configures automatic submodule updates for Claude Code marketplaces.

After running:
  1. The marketplace will auto-update submodules every 6 hours
  2. marketplace.json versions will stay in sync with plugins
  3. Plugin repos can trigger immediate updates (with additional setup)

Examples:
  %(prog)s --marketplace-dir /path/to/my-marketplace
  %(prog)s --dry-run  # Preview changes without applying
  %(prog)s --marketplace-dir /path --full  # Full pipeline setup
  %(prog)s --marketplace-dir /path --check-plugins  # Check plugin status only
  %(prog)s --pat-instructions  # Print PAT setup instructions
        """,
    )

    parser.add_argument(
        "--marketplace-dir",
        type=Path,
        default=Path.cwd(),
        help="Path to marketplace repository (default: current directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress output except errors",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Run full pipeline: workflows, scripts, README, check plugins, PAT instructions",
    )
    parser.add_argument(
        "--check-plugins",
        action="store_true",
        help="Check plugin submodules for notification workflow status only",
    )
    parser.add_argument(
        "--pat-instructions",
        action="store_true",
        help="Print GitHub PAT setup instructions and exit",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show complete status of marketplace automation setup",
    )
    parser.add_argument(
        "--setup-readme",
        action="store_true",
        help="Set up or update README.md with architecture diagram only",
    )

    args = parser.parse_args()

    # Handle mutually exclusive operations
    if args.pat_instructions:
        print_pat_setup_instructions()
        return 0

    if args.status:
        status = get_full_status(
            args.marketplace_dir,
            verbose=not args.quiet,
        )
        return 0 if status["is_valid_marketplace"] else 1

    if args.check_plugins:
        result = setup_plugin_notifications(
            args.marketplace_dir,
            dry_run=args.dry_run,
            verbose=not args.quiet,
        )
        # Return 0 if all plugins configured, 1 if any missing/need config
        if result["missing"] or result["needs_config"]:
            return 1
        return 0

    if args.setup_readme:
        success = setup_readme_with_diagram(
            args.marketplace_dir,
            dry_run=args.dry_run,
            verbose=not args.quiet,
        )
        return 0 if success else 1

    if args.full:
        success = run_full_pipeline(
            args.marketplace_dir,
            dry_run=args.dry_run,
            verbose=not args.quiet,
        )
        return 0 if success else 1

    # Default: just set up marketplace automation (original behavior)
    success = setup_marketplace_automation(
        args.marketplace_dir,
        dry_run=args.dry_run,
        verbose=not args.quiet,
    )

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
