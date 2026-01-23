#!/usr/bin/env python3
"""pre-push-hook.py - Prevent pushing broken plugins to GitHub.

This hook runs comprehensive validation before allowing git push.
If any CRITICAL or MAJOR issues are found, the push is blocked.

To install:
    cp scripts/pre-push-hook.py .git/hooks/pre-push
    chmod +x .git/hooks/pre-push

Exit codes:
    0 - All validations passed, push allowed
    1 - Validation failed, push blocked
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# ANSI Colors
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
NC = "\033[0m"


def run_command(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "Command timed out"
    except Exception as e:
        return 1, "", str(e)


def validate_json(file_path: Path) -> tuple[bool, str]:
    """Validate JSON file syntax."""
    try:
        with open(file_path) as f:
            json.load(f)
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    except FileNotFoundError:
        return False, "File not found"


def validate_semver(version: str) -> bool:
    """Validate semver format."""
    pattern = r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$"
    return bool(re.match(pattern, version))


def find_plugins(repo_root: Path) -> list[Path]:
    """Find all plugin directories."""
    plugins = []
    for item in repo_root.iterdir():
        if item.is_dir():
            plugin_json = item / ".claude-plugin" / "plugin.json"
            if plugin_json.exists():
                plugins.append(item)
    return plugins


def validate_plugin_manifest(plugin_dir: Path) -> list[tuple[str, str]]:
    """Validate plugin manifest. Returns list of (severity, message) tuples."""
    issues = []
    plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"

    if not plugin_json.exists():
        issues.append(("CRITICAL", f"Missing .claude-plugin/plugin.json in {plugin_dir.name}"))
        return issues

    valid, error = validate_json(plugin_json)
    if not valid:
        issues.append(("CRITICAL", f"Invalid plugin.json in {plugin_dir.name}: {error}"))
        return issues

    with open(plugin_json) as f:
        data = json.load(f)

    # Check required fields
    if not data.get("name"):
        issues.append(("CRITICAL", f"Missing 'name' in {plugin_dir.name}/plugin.json"))

    if not data.get("version"):
        issues.append(("CRITICAL", f"Missing 'version' in {plugin_dir.name}/plugin.json"))
    elif not validate_semver(data["version"]):
        issues.append(("MAJOR", f"Invalid semver '{data['version']}' in {plugin_dir.name}"))

    if not data.get("description"):
        issues.append(("MAJOR", f"Missing 'description' in {plugin_dir.name}/plugin.json"))

    # Validate agents field if present
    agents = data.get("agents")
    if agents is not None:
        if not isinstance(agents, list):
            issues.append(("CRITICAL", f"'agents' must be array in {plugin_dir.name}/plugin.json"))
        else:
            for agent in agents:
                if not isinstance(agent, str):
                    issues.append(("CRITICAL", f"Agent entry must be string path in {plugin_dir.name}"))
                elif not agent.endswith(".md"):
                    issues.append(("MAJOR", f"Agent path should end with .md: {agent}"))

    return issues


def validate_hooks_config(plugin_dir: Path) -> list[tuple[str, str]]:
    """Validate hooks configuration."""
    issues = []
    hooks_json = plugin_dir / "hooks" / "hooks.json"

    if not hooks_json.exists():
        return issues  # Hooks are optional

    valid, error = validate_json(hooks_json)
    if not valid:
        issues.append(("CRITICAL", f"Invalid hooks.json in {plugin_dir.name}: {error}"))
        return issues

    with open(hooks_json) as f:
        data = json.load(f)

    hooks = data.get("hooks", {})
    valid_events = [
        "PreToolUse", "PostToolUse", "PostToolUseFailure",
        "Notification", "Stop", "SubagentStop", "SubagentStart",
        "UserPromptSubmit", "PermissionRequest",
        "SessionStart", "SessionEnd", "PreCompact", "Setup"
    ]

    for event_name, event_hooks in hooks.items():
        if event_name not in valid_events:
            issues.append(("MAJOR", f"Unknown hook event '{event_name}' in {plugin_dir.name}"))

        if not isinstance(event_hooks, list):
            issues.append(("CRITICAL", f"Hook event '{event_name}' must be array"))
            continue

        for hook_entry in event_hooks:
            hook_list = hook_entry.get("hooks", [])
            for hook in hook_list:
                if hook.get("type") == "command":
                    cmd = hook.get("command", "")
                    if not cmd:
                        issues.append(("CRITICAL", f"Empty command in {event_name} hook"))
                    elif "${CLAUDE_PLUGIN_ROOT}" not in cmd and not cmd.startswith("/"):
                        issues.append(("MAJOR", f"Hook command should use ${{CLAUDE_PLUGIN_ROOT}}: {cmd}"))

    return issues


def validate_marketplace(repo_root: Path) -> list[tuple[str, str]]:
    """Validate marketplace.json."""
    issues = []
    marketplace_json = repo_root / ".claude-plugin" / "marketplace.json"

    if not marketplace_json.exists():
        issues.append(("CRITICAL", "Missing .claude-plugin/marketplace.json"))
        return issues

    valid, error = validate_json(marketplace_json)
    if not valid:
        issues.append(("CRITICAL", f"Invalid marketplace.json: {error}"))
        return issues

    with open(marketplace_json) as f:
        data = json.load(f)

    if not data.get("name"):
        issues.append(("CRITICAL", "Missing 'name' in marketplace.json"))

    plugins = data.get("plugins", [])
    if not plugins:
        issues.append(("MAJOR", "No plugins defined in marketplace.json"))

    for plugin in plugins:
        if not plugin.get("name"):
            issues.append(("CRITICAL", "Plugin entry missing 'name'"))
        if not plugin.get("source"):
            issues.append(("CRITICAL", f"Plugin '{plugin.get('name', '?')}' missing 'source'"))
        elif not isinstance(plugin.get("source"), str):
            issues.append(("CRITICAL", f"Plugin '{plugin.get('name', '?')}' source must be string path"))

    return issues


def check_version_consistency(repo_root: Path) -> list[tuple[str, str]]:
    """Check that plugin versions match marketplace.json."""
    issues = []
    marketplace_json = repo_root / ".claude-plugin" / "marketplace.json"

    if not marketplace_json.exists():
        return issues

    with open(marketplace_json) as f:
        marketplace = json.load(f)

    marketplace_plugins = {p["name"]: p.get("version") for p in marketplace.get("plugins", [])}

    for plugin_dir in find_plugins(repo_root):
        plugin_json = plugin_dir / ".claude-plugin" / "plugin.json"
        if plugin_json.exists():
            with open(plugin_json) as f:
                plugin = json.load(f)
            name = plugin.get("name")
            version = plugin.get("version")

            if name in marketplace_plugins:
                mp_version = marketplace_plugins[name]
                if mp_version and version != mp_version:
                    issues.append(("MAJOR", f"Version mismatch: {name} is {version} but marketplace has {mp_version}"))

    return issues


def run_external_validator(repo_root: Path) -> list[tuple[str, str]]:
    """Run external validation scripts if available."""
    issues = []
    validator = repo_root / "claude-plugins-validation" / "scripts" / "validate_marketplace.py"

    if not validator.exists():
        return issues

    _, stdout, stderr = run_command(
        ["uv", "run", "python", str(validator), str(repo_root)],
        cwd=repo_root / "claude-plugins-validation",
        timeout=60
    )

    output = stdout + stderr
    if "CRITICAL" in output:
        # Extract critical issues
        for line in output.split("\n"):
            if "[CRITICAL]" in line:
                issues.append(("CRITICAL", line.strip()))

    return issues


def main() -> int:
    """Main pre-push validation."""
    # Get repo root
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True
    )
    repo_root = Path(result.stdout.strip())

    print(f"{BOLD}{'=' * 60}{NC}")
    print(f"{BOLD}Pre-Push Validation - Blocking broken plugins{NC}")
    print(f"{BOLD}{'=' * 60}{NC}")
    print()

    all_issues: list[tuple[str, str]] = []

    # 1. Validate marketplace
    print(f"{BLUE}Validating marketplace...{NC}")
    all_issues.extend(validate_marketplace(repo_root))

    # 2. Validate each plugin
    for plugin_dir in find_plugins(repo_root):
        print(f"{BLUE}Validating plugin: {plugin_dir.name}...{NC}")
        all_issues.extend(validate_plugin_manifest(plugin_dir))
        all_issues.extend(validate_hooks_config(plugin_dir))

    # 3. Check version consistency
    print(f"{BLUE}Checking version consistency...{NC}")
    all_issues.extend(check_version_consistency(repo_root))

    # 4. Run external validators
    print(f"{BLUE}Running external validators...{NC}")
    all_issues.extend(run_external_validator(repo_root))

    # Categorize issues
    critical = [msg for sev, msg in all_issues if sev == "CRITICAL"]
    major = [msg for sev, msg in all_issues if sev == "MAJOR"]
    minor = [msg for sev, msg in all_issues if sev == "MINOR"]

    # Report
    print()
    print(f"{BOLD}{'=' * 60}{NC}")
    print(f"{BOLD}Validation Results{NC}")
    print(f"{BOLD}{'=' * 60}{NC}")

    if critical:
        print(f"\n{RED}CRITICAL Issues (push blocked):{NC}")
        for msg in critical:
            print(f"  {RED}✘{NC} {msg}")

    if major:
        print(f"\n{YELLOW}MAJOR Issues (push blocked):{NC}")
        for msg in major:
            print(f"  {YELLOW}⚠{NC} {msg}")

    if minor:
        print(f"\n{BLUE}MINOR Issues (warnings only):{NC}")
        for msg in minor:
            print(f"  {BLUE}ℹ{NC} {msg}")

    print()
    print(f"Summary: {RED}{len(critical)} critical{NC}, {YELLOW}{len(major)} major{NC}, {BLUE}{len(minor)} minor{NC}")
    print()

    # Decision
    if critical or major:
        print(f"{RED}{'=' * 60}{NC}")
        print(f"{RED}PUSH BLOCKED - Fix CRITICAL and MAJOR issues first{NC}")
        print(f"{RED}{'=' * 60}{NC}")
        print()
        print("To bypass (NOT RECOMMENDED): git push --no-verify")
        return 1
    print(f"{GREEN}{'=' * 60}{NC}")
    print(f"{GREEN}VALIDATION PASSED - Push allowed{NC}")
    print(f"{GREEN}{'=' * 60}{NC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
