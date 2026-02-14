#!/usr/bin/env python3
"""
Claude Plugins Validation - Hook Validator

Validates hook configuration files according to Claude Code hook spec.
Based on:
  - https://code.claude.com/docs/en/hooks.md
  - https://code.claude.com/docs/en/hooks-guide.md

Usage:
    uv run python scripts/validate_hook.py path/to/hooks.json
    uv run python scripts/validate_hook.py path/to/hooks.json --verbose
    uv run python scripts/validate_hook.py path/to/hooks.json --json

Exit codes:
    0 - All checks passed
    1 - CRITICAL issues found (hooks will not work)
    2 - MAJOR issues found (significant problems)
    3 - MINOR issues found (may affect behavior)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from validation_common import resolve_tool_command

# Validation result levels
Level = Literal["CRITICAL", "MAJOR", "MINOR", "INFO", "PASSED"]

# Valid hook event names per official docs
VALID_HOOK_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "SubagentStart",
    "PreCompact",
    "Setup",
    "SessionStart",
    "SessionEnd",
}

# Events that support matchers
EVENTS_WITH_MATCHERS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "PreCompact",
    "Setup",
    "SessionStart",
}

# Events that do NOT support matchers (matcher field is ignored)
EVENTS_WITHOUT_MATCHERS = {
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "SubagentStart",
    "SessionEnd",
}

# Valid hook types
VALID_HOOK_TYPES = {"command", "prompt"}

# Common tool names for matcher validation hints
COMMON_TOOL_NAMES = {
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Task",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
}

# Common notification types
COMMON_NOTIFICATION_TYPES = {
    "permission_prompt",
    "idle_prompt",
    "auth_success",
    "elicitation_dialog",
}

# Compact trigger types
COMPACT_TRIGGERS = {"manual", "auto"}

# Setup trigger types
SETUP_TRIGGERS = {"init", "maintenance"}

# SessionStart source types
SESSION_START_SOURCES = {"startup", "resume", "clear", "compact"}

# Environment variables available in hooks
VALID_ENV_VARS = {
    "CLAUDE_PLUGIN_ROOT",  # Plugin hooks only
    "CLAUDE_PROJECT_DIR",  # All hooks
    "CLAUDE_ENV_FILE",  # SessionStart and Setup only
    "CLAUDE_CODE_REMOTE",  # All hooks
}

# Script extensions that should be linted
LINTABLE_EXTENSIONS = {
    ".sh": "bash",
    ".bash": "bash",
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
}


@dataclass
class ValidationResult:
    """Single validation result."""

    level: Level
    message: str
    file: str | None = None
    line: int | None = None


@dataclass
class ValidationReport:
    """Complete validation report for a hook configuration."""

    hook_path: str
    results: list[ValidationResult] = field(default_factory=list)

    def add(
        self,
        level: Level,
        message: str,
        file: str | None = None,
        line: int | None = None,
    ) -> None:
        """Add a validation result."""
        self.results.append(ValidationResult(level, message, file, line))

    def passed(self, message: str, file: str | None = None) -> None:
        """Add a passed check."""
        self.add("PASSED", message, file)

    def info(self, message: str, file: str | None = None) -> None:
        """Add an info message."""
        self.add("INFO", message, file)

    def minor(self, message: str, file: str | None = None, line: int | None = None) -> None:
        """Add a minor issue."""
        self.add("MINOR", message, file, line)

    def major(self, message: str, file: str | None = None, line: int | None = None) -> None:
        """Add a major issue."""
        self.add("MAJOR", message, file, line)

    def critical(self, message: str, file: str | None = None, line: int | None = None) -> None:
        """Add a critical issue."""
        self.add("CRITICAL", message, file, line)

    @property
    def has_critical(self) -> bool:
        return any(r.level == "CRITICAL" for r in self.results)

    @property
    def has_major(self) -> bool:
        return any(r.level == "MAJOR" for r in self.results)

    @property
    def has_minor(self) -> bool:
        return any(r.level == "MINOR" for r in self.results)

    @property
    def exit_code(self) -> int:
        if self.has_critical:
            return 1
        if self.has_major:
            return 2
        if self.has_minor:
            return 3
        return 0


def validate_json_structure(hook_path: Path, report: ValidationReport) -> dict[str, Any] | None:
    """Validate hooks.json exists and is valid JSON."""
    if not hook_path.exists():
        report.critical(f"Hook file not found: {hook_path}")
        return None

    try:
        content = hook_path.read_text()
        data = json.loads(content)
        report.passed("Valid JSON syntax")
        return cast(dict[str, Any], data)
    except json.JSONDecodeError as e:
        report.critical(f"Invalid JSON: {e.msg} at line {e.lineno}")
        return None


def validate_top_level_structure(data: Any, report: ValidationReport) -> bool:
    """Validate top-level structure of hooks.json."""
    if not isinstance(data, dict):
        report.critical("Root must be a JSON object")
        return False

    # Optional description field
    if "description" in data:
        desc = data["description"]
        if not isinstance(desc, str):
            report.major(f"'description' must be a string, got {type(desc).__name__}")
        else:
            report.passed(f"Description: {desc[:50]}...")

    # Check for 'hooks' key
    if "hooks" not in data:
        report.critical("Missing required 'hooks' object")
        return False

    hooks = data["hooks"]
    if not isinstance(hooks, dict):
        report.critical(f"'hooks' must be an object, got {type(hooks).__name__}")
        return False

    report.passed("Valid top-level structure")
    return True


def validate_event_name(event_name: str, report: ValidationReport) -> bool:
    """Validate a hook event name."""
    if event_name not in VALID_HOOK_EVENTS:
        report.critical(f"Unknown hook event: '{event_name}'. Valid events: {sorted(VALID_HOOK_EVENTS)}")
        return False
    return True


def validate_matcher(matcher: Any, event_name: str, report: ValidationReport) -> bool:
    """Validate a matcher pattern."""
    # Events without matchers - warn if matcher provided
    if event_name in EVENTS_WITHOUT_MATCHERS:
        if matcher is not None and matcher != "":
            report.info(f"Matcher '{matcher}' provided for {event_name} (matchers are ignored for this event)")
        return True

    # Matcher is optional - empty or missing means "match all"
    if matcher is None or matcher == "" or matcher == "*":
        return True

    if not isinstance(matcher, str):
        report.major(f"Matcher must be a string, got {type(matcher).__name__}")
        return False

    # Validate regex syntax
    try:
        re.compile(matcher)
    except re.error as e:
        report.major(f"Invalid regex in matcher '{matcher}': {e}")
        return False

    # Check for common tool names (informational)
    if event_name in {"PreToolUse", "PostToolUse", "PermissionRequest"}:
        # Check if matcher looks like it's matching tool names
        parts = re.split(r"[|()]", matcher)
        for part in parts:
            part = part.strip()
            if part and part not in COMMON_TOOL_NAMES and not part.startswith("mcp__"):
                # Could be a regex pattern or custom tool
                if re.match(r"^[A-Z][a-zA-Z]+$", part):
                    report.info(f"Matcher '{part}' is not a common tool name (may be custom or MCP tool)")

    return True


def extract_script_path(command: str, plugin_root: Path | None) -> Path | None:
    """Extract script path from a command string."""
    # Handle environment variable substitution
    cmd = command.strip()

    # Common patterns:
    # "${CLAUDE_PLUGIN_ROOT}/scripts/foo.sh"
    # "$CLAUDE_PROJECT_DIR/.claude/hooks/foo.py"
    # /absolute/path/to/script.sh
    # ./relative/path/to/script.py

    # Extract the first path-like token
    # Remove leading quotes
    if cmd.startswith('"'):
        # Find matching quote
        end = cmd.find('"', 1)
        if end > 0:
            cmd = cmd[1:end]
        else:
            cmd = cmd[1:]

    # Split on spaces to get first token (the command/script)
    parts = cmd.split()
    if not parts:
        return None

    script_part = parts[0]

    # Substitute environment variables
    if plugin_root:
        script_part = script_part.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
        script_part = script_part.replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))

    # Skip if still has unresolved variables
    if "$" in script_part:
        return None

    # Check if it looks like a script path
    path = Path(script_part)
    suffix = path.suffix.lower()
    if suffix in LINTABLE_EXTENSIONS or suffix in {".rb", ".pl", ".php"}:
        return path

    return None


def lint_bash_script(script_path: Path, report: ValidationReport) -> None:
    """Lint a bash script using shellcheck."""
    shellcheck_cmd = resolve_tool_command("shellcheck")
    if not shellcheck_cmd:
        report.minor(f"shellcheck not available locally or via bunx/npx, skipping lint for {script_path.name}")
        return

    try:
        result = subprocess.run(
            shellcheck_cmd + ["-f", "json", str(script_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            report.passed(f"shellcheck: {script_path.name} OK")
            return

        try:
            issues = json.loads(result.stdout) if result.stdout else []
        except json.JSONDecodeError:
            issues = []

        for issue in issues:
            level = issue.get("level", "warning")
            msg = issue.get("message", "Unknown issue")
            line = issue.get("line", 0)
            code = issue.get("code", "")

            if level == "error":
                report.major(
                    f"shellcheck SC{code}: {msg}",
                    str(script_path),
                    line,
                )
            elif level == "warning":
                report.minor(
                    f"shellcheck SC{code}: {msg}",
                    str(script_path),
                    line,
                )

    except subprocess.TimeoutExpired:
        report.minor(f"shellcheck timeout for {script_path.name}")
    except Exception as e:
        report.minor(f"shellcheck error: {e}")


def lint_python_script(script_path: Path, report: ValidationReport) -> None:
    """Lint a Python script using ruff and mypy."""
    # Ruff check
    ruff_cmd = resolve_tool_command("ruff")
    if ruff_cmd:
        try:
            result = subprocess.run(
                ruff_cmd + ["check", "--output-format=json", str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                report.passed(f"ruff check: {script_path.name} OK")
            else:
                try:
                    issues = json.loads(result.stdout) if result.stdout else []
                except json.JSONDecodeError:
                    issues = []

                for issue in issues:
                    code = issue.get("code", "")
                    msg = issue.get("message", "Unknown issue")
                    loc = issue.get("location", {})
                    line = loc.get("row", 0)

                    report.major(
                        f"ruff {code}: {msg}",
                        str(script_path),
                        line,
                    )

        except subprocess.TimeoutExpired:
            report.minor(f"ruff timeout for {script_path.name}")
        except Exception as e:
            report.minor(f"ruff error: {e}")
    else:
        report.minor(f"ruff not available locally or via uvx, skipping lint for {script_path.name}")

    # Mypy check
    mypy_cmd = resolve_tool_command("mypy")
    if mypy_cmd:
        try:
            result = subprocess.run(
                mypy_cmd + [
                    "--ignore-missing-imports",
                    "--no-error-summary",
                    str(script_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode == 0:
                report.passed(f"mypy: {script_path.name} OK")
            else:
                # Parse mypy output
                for line in result.stdout.splitlines():
                    if ": error:" in line:
                        # Extract line number
                        match = re.match(r".*:(\d+):\d*: error: (.+)", line)
                        if match:
                            lineno = int(match.group(1))
                            msg = match.group(2)
                            report.major(
                                f"mypy: {msg}",
                                str(script_path),
                                lineno,
                            )

        except subprocess.TimeoutExpired:
            report.minor(f"mypy timeout for {script_path.name}")
        except Exception as e:
            report.minor(f"mypy error: {e}")
    else:
        report.minor(f"mypy not available locally or via uvx, skipping type check for {script_path.name}")


def lint_js_script(script_path: Path, report: ValidationReport) -> None:
    """Lint a JavaScript/TypeScript script using eslint."""
    eslint_cmd = resolve_tool_command("eslint")
    if not eslint_cmd:
        report.minor(f"eslint not available locally or via bunx/npx, skipping lint for {script_path.name}")
        return

    try:
        result = subprocess.run(
            eslint_cmd + ["--format=json", str(script_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            report.passed(f"eslint: {script_path.name} OK")
            return

        try:
            data = json.loads(result.stdout) if result.stdout else []
        except json.JSONDecodeError:
            data = []

        for file_result in data:
            for msg in file_result.get("messages", []):
                severity = msg.get("severity", 1)
                text = msg.get("message", "Unknown issue")
                line = msg.get("line", 0)
                rule = msg.get("ruleId", "")

                if severity >= 2:
                    report.major(
                        f"eslint {rule}: {text}",
                        str(script_path),
                        line,
                    )
                else:
                    report.minor(
                        f"eslint {rule}: {text}",
                        str(script_path),
                        line,
                    )

    except subprocess.TimeoutExpired:
        report.minor(f"eslint timeout for {script_path.name}")
    except Exception as e:
        report.minor(f"eslint error: {e}")


def validate_script(script_path: Path, report: ValidationReport) -> None:
    """Validate and lint a script file."""
    if not script_path.exists():
        report.major(f"Script not found: {script_path}")
        return

    # Check executable permission
    if not os.access(script_path, os.X_OK):
        report.major(f"Script not executable: {script_path.name}")
    else:
        report.passed(f"Script executable: {script_path.name}")

    # Lint based on extension
    suffix = script_path.suffix.lower()
    lang = LINTABLE_EXTENSIONS.get(suffix)

    if lang == "bash":
        lint_bash_script(script_path, report)
    elif lang == "python":
        lint_python_script(script_path, report)
    elif lang in {"javascript", "typescript"}:
        lint_js_script(script_path, report)


def validate_command_hook(
    hook: dict[str, Any],
    event_name: str,
    plugin_root: Path | None,
    report: ValidationReport,
) -> bool:
    """Validate a command-type hook."""
    if "command" not in hook:
        report.critical("Command hook missing required 'command' field")
        return False

    command = hook["command"]
    if not isinstance(command, str):
        report.critical(f"'command' must be a string, got {type(command).__name__}")
        return False

    if not command.strip():
        report.critical("'command' cannot be empty")
        return False

    report.passed(f"Command: {command[:60]}...")

    # Validate timeout if present (Claude Code uses milliseconds)
    if "timeout" in hook:
        timeout = hook["timeout"]
        if not isinstance(timeout, (int, float)):
            report.major(f"'timeout' must be a number, got {type(timeout).__name__}")
        elif timeout <= 0:
            report.major("'timeout' must be positive")
        elif timeout > 300000:  # 5 minutes in milliseconds
            report.minor(f"Long timeout ({timeout}ms / {timeout / 1000:.0f}s) may cause delays")

    # Check for environment variable usage
    if "CLAUDE_ENV_FILE" in command:
        if event_name not in {"SessionStart", "Setup"}:
            report.major("CLAUDE_ENV_FILE is only available in SessionStart and Setup hooks")

    # Extract and validate script path
    script_path = extract_script_path(command, plugin_root)
    if script_path and script_path.exists():
        validate_script(script_path, report)
    elif script_path:
        # Script path detected but doesn't exist
        if plugin_root and "${CLAUDE_PLUGIN_ROOT}" not in hook["command"]:
            # Absolute path that should exist
            report.major(f"Script not found: {script_path}")

    return True


def validate_prompt_hook(
    hook: dict[str, Any],
    event_name: str,
    report: ValidationReport,
) -> bool:
    """Validate a prompt-type hook."""
    if "prompt" not in hook:
        report.critical("Prompt hook missing required 'prompt' field")
        return False

    prompt = hook["prompt"]
    if not isinstance(prompt, str):
        report.critical(f"'prompt' must be a string, got {type(prompt).__name__}")
        return False

    if not prompt.strip():
        report.critical("'prompt' cannot be empty")
        return False

    # Prompt hooks are most useful for Stop/SubagentStop
    if event_name not in {
        "Stop",
        "SubagentStop",
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
    }:
        report.info(f"Prompt hooks for {event_name} may not be as effective as command hooks")

    # Check for $ARGUMENTS placeholder
    if "$ARGUMENTS" not in prompt:
        report.info("Prompt doesn't contain $ARGUMENTS placeholder (input JSON will be appended automatically)")

    report.passed(f"Prompt: {prompt[:60]}...")

    # Validate timeout if present
    if "timeout" in hook:
        timeout = hook["timeout"]
        if not isinstance(timeout, (int, float)):
            report.major(f"'timeout' must be a number, got {type(timeout).__name__}")
        elif timeout <= 0:
            report.major("'timeout' must be positive")

    return True


def validate_single_hook(
    hook: Any,
    event_name: str,
    plugin_root: Path | None,
    report: ValidationReport,
) -> bool:
    """Validate a single hook definition."""
    if not isinstance(hook, dict):
        report.critical(f"Hook must be an object, got {type(hook).__name__}")
        return False

    # Type is required
    if "type" not in hook:
        report.critical("Hook missing required 'type' field")
        return False

    hook_type = hook["type"]
    if hook_type not in VALID_HOOK_TYPES:
        report.critical(f"Invalid hook type: '{hook_type}'. Valid types: {sorted(VALID_HOOK_TYPES)}")
        return False

    # Validate based on type
    if hook_type == "command":
        if not validate_command_hook(hook, event_name, plugin_root, report):
            return False
    elif hook_type == "prompt":
        if not validate_prompt_hook(hook, event_name, report):
            return False

    # Validate 'once' field (only valid in skill hooks)
    if "once" in hook:
        once = hook["once"]
        if not isinstance(once, bool):
            report.major(f"'once' must be a boolean, got {type(once).__name__}")
        else:
            report.info("'once' field detected (only works in skill-defined hooks)")

    return True


def validate_matcher_block(
    matcher_block: Any,
    event_name: str,
    plugin_root: Path | None,
    report: ValidationReport,
) -> bool:
    """Validate a matcher block (contains matcher and hooks array)."""
    if not isinstance(matcher_block, dict):
        report.critical(f"Matcher block must be an object, got {type(matcher_block).__name__}")
        return False

    # Validate matcher (optional)
    matcher = matcher_block.get("matcher")
    if not validate_matcher(matcher, event_name, report):
        return False

    # Validate hooks array (required)
    if "hooks" not in matcher_block:
        report.critical("Matcher block missing required 'hooks' array")
        return False

    hooks = matcher_block["hooks"]
    if not isinstance(hooks, list):
        report.critical(f"'hooks' must be an array, got {type(hooks).__name__}")
        return False

    if not hooks:
        report.minor("'hooks' array is empty")
        return True

    # Validate each hook
    all_valid = True
    for i, hook in enumerate(hooks):
        report.info(f"Validating hook {i + 1} of {len(hooks)}...")
        if not validate_single_hook(hook, event_name, plugin_root, report):
            all_valid = False

    return all_valid


def validate_event_hooks(
    event_name: str,
    event_config: Any,
    plugin_root: Path | None,
    report: ValidationReport,
) -> bool:
    """Validate all hooks for a specific event."""
    if not isinstance(event_config, list):
        report.critical(f"Event config for '{event_name}' must be an array, got {type(event_config).__name__}")
        return False

    if not event_config:
        report.info(f"No hooks configured for {event_name}")
        return True

    report.info(f"Validating {len(event_config)} matcher block(s) for {event_name}")

    all_valid = True
    for i, matcher_block in enumerate(event_config):
        report.info(f"Matcher block {i + 1}...")
        if not validate_matcher_block(matcher_block, event_name, plugin_root, report):
            all_valid = False

    if all_valid:
        report.passed(f"All hooks valid for {event_name}")

    return all_valid


def validate_hooks(
    hook_path: Path,
    plugin_root: Path | None = None,
) -> ValidationReport:
    """Validate a complete hooks.json file.

    Args:
        hook_path: Path to the hooks.json file
        plugin_root: Optional plugin root directory for resolving paths

    Returns:
        ValidationReport with all results
    """
    report = ValidationReport(hook_path=str(hook_path))

    # Parse JSON
    data = validate_json_structure(hook_path, report)
    if data is None:
        return report

    # Validate top-level structure
    if not validate_top_level_structure(data, report):
        return report

    # Validate each event
    hooks = data["hooks"]
    for event_name, event_config in hooks.items():
        if not validate_event_name(event_name, report):
            continue

        validate_event_hooks(event_name, event_config, plugin_root, report)

    return report


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    # ANSI colors
    colors = {
        "CRITICAL": "\033[91m",  # Red
        "MAJOR": "\033[93m",  # Yellow
        "MINOR": "\033[94m",  # Blue
        "INFO": "\033[90m",  # Gray
        "PASSED": "\033[92m",  # Green
        "RESET": "\033[0m",
    }

    # Count by level
    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    # Print header
    print("\n" + "=" * 60)
    print(f"Hook Validation: {report.hook_path}")
    print("=" * 60)

    # Print summary
    print("\nSummary:")
    crit = colors["CRITICAL"]
    maj = colors["MAJOR"]
    minor = colors["MINOR"]
    info = colors["INFO"]
    passed = colors["PASSED"]
    rst = colors["RESET"]

    print(f"  {crit}CRITICAL: {counts['CRITICAL']}{rst}")
    print(f"  {maj}MAJOR:    {counts['MAJOR']}{rst}")
    print(f"  {minor}MINOR:    {counts['MINOR']}{rst}")
    if verbose:
        print(f"  {info}INFO:     {counts['INFO']}{rst}")
        print(f"  {passed}PASSED:   {counts['PASSED']}{rst}")

    # Print details
    print("\nDetails:")
    for r in report.results:
        if r.level == "PASSED" and not verbose:
            continue
        if r.level == "INFO" and not verbose:
            continue

        color = colors[r.level]
        file_info = f" ({r.file})" if r.file else ""
        line_info = f":{r.line}" if r.line else ""
        print(f"  {color}[{r.level}]{rst} {r.message}{file_info}{line_info}")

    # Print final status
    print("\n" + "-" * 60)
    if report.exit_code == 0:
        print(f"{passed}✓ Hook validation passed{rst}")
    elif report.exit_code == 1:
        print(f"{crit}✗ CRITICAL issues - hooks will not work{rst}")
    elif report.exit_code == 2:
        print(f"{maj}✗ MAJOR issues - significant problems{rst}")
    else:
        print(f"{minor}! MINOR issues - may affect behavior{rst}")

    print()


def print_json(report: ValidationReport) -> None:
    """Print validation results as JSON."""
    output = {
        "hook_path": report.hook_path,
        "exit_code": report.exit_code,
        "counts": {
            "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
            "major": sum(1 for r in report.results if r.level == "MAJOR"),
            "minor": sum(1 for r in report.results if r.level == "MINOR"),
            "info": sum(1 for r in report.results if r.level == "INFO"),
            "passed": sum(1 for r in report.results if r.level == "PASSED"),
        },
        "results": [
            {
                "level": r.level,
                "message": r.message,
                "file": r.file,
                "line": r.line,
            }
            for r in report.results
        ],
    }
    print(json.dumps(output, indent=2))


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Validate a Claude Code hooks.json file")
    parser.add_argument("hook_path", help="Path to the hooks.json file")
    parser.add_argument(
        "--plugin-root",
        help="Plugin root directory for resolving script paths",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all results including passed checks",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    hook_path = Path(args.hook_path)
    plugin_root = Path(args.plugin_root) if args.plugin_root else None

    if not hook_path.exists():
        print(f"Error: {hook_path} does not exist", file=sys.stderr)
        return 1

    report = validate_hooks(hook_path, plugin_root)

    if args.json:
        print_json(report)
    else:
        print_results(report, args.verbose)

    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
