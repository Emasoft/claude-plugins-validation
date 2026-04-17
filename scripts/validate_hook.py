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
import ast
import difflib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from cpv_validation_common import (
    COLORS,
    VALID_HOOK_EVENTS,
    ValidationReport,
    resolve_tool_command,
    save_report_and_print_summary,
)

# Events that support matchers
EVENTS_WITH_MATCHERS = {
    "PreToolUse",  # matcher: tool_name
    "PostToolUse",  # matcher: tool_name
    "PostToolUseFailure",  # matcher: tool_name
    "PermissionRequest",  # matcher: tool_name
    "Notification",  # matcher: notification_type (permission_prompt, idle_prompt, auth_success, elicitation_dialog)
    "PreCompact",  # matcher: manual, auto
    "PostCompact",  # matcher: manual, auto (v2.1.76)
    "Setup",  # matcher: (legacy — not in official docs as of v2.1.109)
    "SessionStart",  # matcher: startup, resume, clear, compact
    "SessionEnd",  # matcher: clear, resume, logout, prompt_input_exit, bypass_permissions_disabled, other (hooks.md L192)
    "SubagentStart",  # matcher: agent name (Bash, Explore, Plan, custom)
    "SubagentStop",  # matcher: agent name
    "ConfigChange",  # matcher: user_settings, project_settings, local_settings, policy_settings, skills
    "StopFailure",  # matcher: rate_limit, authentication_failed, billing_error, invalid_request, server_error, max_output_tokens, unknown (v2.1.78)
    "InstructionsLoaded",  # matcher: session_start, nested_traversal, path_glob_match, include, compact (v2.1.69)
    "Elicitation",  # matcher: MCP server name (v2.1.76)
    "ElicitationResult",  # matcher: MCP server name (v2.1.76)
    "FileChanged",  # matcher: filename/basename pattern (v2.1.83)
    "PermissionDenied",  # matcher: tool_name (v2.1.89) — fires when auto mode classifier denies
}

# Events that do NOT support matchers (matcher field is ignored)
EVENTS_WITHOUT_MATCHERS = {
    "UserPromptSubmit",
    "Stop",
    "TeammateIdle",
    "TaskCompleted",
    "TaskCreated",  # v2.1.84
    "WorktreeCreate",
    "WorktreeRemove",
    "CwdChanged",  # v2.1.83
}

# Valid hook types (v2.1.63+: "http" hooks POST JSON to a URL)
VALID_HOOK_TYPES = {"command", "http", "prompt", "agent"}

# Events that only support "command" or "http" hooks (not prompt or agent)
COMMAND_ONLY_EVENTS = {
    "ConfigChange",
    "InstructionsLoaded",
    "Notification",
    "PreCompact",
    "PostCompact",  # v2.1.76
    "SessionEnd",
    "SessionStart",
    "SubagentStart",
    "TeammateIdle",
    "WorktreeCreate",
    "WorktreeRemove",
    "Elicitation",  # v2.1.76
    "ElicitationResult",  # v2.1.76
    "CwdChanged",  # v2.1.83
    "FileChanged",  # v2.1.83
    "TaskCreated",  # v2.1.84
}

# Events that only support "command" hooks — a STRICT subset of
# COMMAND_ONLY_EVENTS. Per hooks.md L687 and L2109, SessionStart rejects
# `http`, `prompt`, and `agent` hook types unconditionally.
COMMAND_STRICT_EVENTS = {
    "SessionStart",
}

# Common tool names for matcher validation hints
COMMON_TOOL_NAMES = {
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Task",
    "Agent",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
    "EnterWorktree",
    "ExitWorktree",
    "ToolSearch",
    "TaskCreate",
    "CronCreate",
    "CronDelete",
    "CronList",
    "LSP",
    "Skill",
    "AskUserQuestion",
    "PowerShell",  # v2.1.84 — Windows opt-in preview
    "SendMessage",  # Agent teams — message teammates or resume subagents
    "TeamCreate",  # Agent teams
    "TeamDelete",  # Agent teams
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
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

# SessionEnd reason values (hooks.md L192).
# `bypass_permissions_disabled` was added in a later v2.1.x point release alongside
# `prompt_input_exit`; CPV accepts it as of v2.22.0 (spec-audit-3 §1.5).
SESSION_END_REASONS = {
    "clear",
    "resume",
    "logout",
    "prompt_input_exit",
    "bypass_permissions_disabled",
    "other",
}

# StopFailure error values (hooks.md L200).
# 7 values total; `max_output_tokens` was the last add (v2.1.78).
STOPFAILURE_ERRORS = {
    "rate_limit",
    "authentication_failed",
    "billing_error",
    "invalid_request",
    "server_error",
    "max_output_tokens",
    "unknown",
}

# InstructionsLoaded load_reason values (hooks.md L201 + L787).
# `compact` fires when instruction files are re-loaded after a compaction event.
INSTRUCTIONS_LOADED_REASONS = {
    "session_start",
    "nested_traversal",
    "path_glob_match",
    "include",
    "compact",
}

# ConfigChange source values (hooks.md L197).
CONFIG_CHANGE_SOURCES = {
    "user_settings",
    "project_settings",
    "local_settings",
    "policy_settings",
    "skills",
}

# PreToolUse `permissionDecision` output values (hooks.md L984, L1013-1053).
# `"defer"` was added in Claude Code v2.1.89+ and only applies in non-interactive
# `-p` mode. Precedence: deny > defer > ask > allow.
#
# CPV does not currently validate the output JSON schema of hook responses, but
# this constant is exported so downstream validators and tests can reference the
# authoritative allowed-values set.
PRETOOLUSE_PERMISSION_DECISIONS = {"allow", "deny", "ask", "defer"}

# PreToolUse `hookSpecificOutput` known fields (hooks.md L1013-1053 and
# v2.1.110 `additionalContext` retention on tool failure — GAP-19).
# `additionalContext` was added as a retention field for context that should
# survive tool failure. CPV does not currently validate hook *output* shape,
# but exposing this constant prevents downstream validators from flagging
# `additionalContext` as unknown should output validation ever land.
PRETOOLUSE_HOOK_SPECIFIC_OUTPUT_FIELDS = {
    "hookEventName",  # hooks.md L1015 — required, == "PreToolUse"
    "permissionDecision",  # see PRETOOLUSE_PERMISSION_DECISIONS
    "permissionDecisionReason",  # free-form explanation
    "additionalContext",  # v2.1.110 — retained on tool failure (GAP-19)
}

# Permission-update-entry type enum (hooks.md L1115-1141, PermissionRequest
# output schema). 6 types total. Exposed for downstream validators.
# CPV-P2-m2 requested these constants exist even if unused by today's
# validators so that future output validation doesn't have to rediscover them.
PERMISSION_UPDATE_TYPES = {
    "addRules",
    "replaceRules",
    "removeRules",
    "setMode",
    "addDirectories",
    "removeDirectories",
}

# Permission-update `behavior` enum (hooks.md L1121).
PERMISSION_BEHAVIORS = {"allow", "deny", "ask"}

# Permission-update `destination` enum (hooks.md L1134-1139).
PERMISSION_DESTINATIONS = {
    "session",
    "localSettings",
    "projectSettings",
    "userSettings",
}

# PermissionDenied `hookSpecificOutput` fields (plugins-reference.md L117:
# "Return `{retry: true}` to tell the model it may retry the denied tool call.")
# GAP-17: recognizing this output shape. When the output dict is parsed (not
# currently wired into the validator pipeline), `retry` must be a boolean.
PERMISSION_DENIED_HOOK_SPECIFIC_OUTPUT_FIELDS = {
    "hookEventName",  # == "PermissionDenied"
    "retry",  # boolean — tell the model to retry the denied call
}


def validate_permission_denied_output(output: Any) -> list[str]:
    """Validate a PermissionDenied hook's JSON output shape.

    GAP-17: plugins-reference.md L117 states PermissionDenied hooks return
    ``{retry: true}`` to tell the model it may retry the denied tool call.
    This helper is not wired into the main validator pipeline (CPV does not
    currently execute hooks to capture their output), but is exposed so that
    future output validation, integration tests, and hooks authored alongside
    CPV can confirm the shape.

    Returns a list of human-readable issue strings; an empty list means OK.
    Each issue is MINOR-class (the hook still runs, the model just can't
    retry in the expected way).
    """
    issues: list[str] = []
    if not isinstance(output, dict):
        return [f"PermissionDenied hook output must be a JSON object, got {type(output).__name__}"]
    # Accept hookSpecificOutput wrapper or flat shape — both are observed in
    # the spec examples. If wrapped, unwrap to look at the inner shape.
    payload = output.get("hookSpecificOutput", output)
    if not isinstance(payload, dict):
        return ["PermissionDenied hookSpecificOutput must be a JSON object"]
    if "retry" in payload and not isinstance(payload["retry"], bool):
        issues.append(
            f"PermissionDenied 'retry' must be a boolean (plugins-reference.md L117), "
            f"got {type(payload['retry']).__name__}"
        )
    return issues


# Environment variables available in hooks are sourced from
# cpv_validation_common.VALID_PLUGIN_ENV_VARS + is_valid_plugin_env_var
# (which also accepts the dynamic CLAUDE_PLUGIN_OPTION_<KEY> pattern).
# Do NOT maintain a local copy here — it will diverge.

# Script extensions that should be linted
LINTABLE_EXTENSIONS = {
    ".sh": "bash",
    ".bash": "bash",
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
}

# Shell builtins / side-effect-only simple commands — when a compound hook
# command begins with one of these, it has no script payload and we move on
# to the NEXT simple command to find the actual invocation.
#   e.g. `unset VIRTUAL_ENV; python3 foo.py`  → skip "unset VIRTUAL_ENV", scan "python3 foo.py"
#   e.g. `cd /tmp && python3 foo.py`          → skip "cd /tmp", scan "python3 foo.py"
SHELL_NOOPS = {
    "unset",
    "export",
    "cd",
    "source",
    ".",
    "set",
    "shift",
    "alias",
    "umask",
    "ulimit",
    "pushd",
    "popd",
}

# Matches a bash-style environment-variable assignment prefix like `FOO=bar`
# which, per POSIX shell semantics, exports FOO=bar for the NEXT command:
#   e.g. `NODE_ENV=production node foo.js`
#   e.g. `PYTHONPATH=./lib python3 foo.py`
# These tokens must be SKIPPED when hunting for the interpreter — they are
# not the command itself. The variable name must start with a letter/_.
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Python stdlib module names — everything NOT in this set (and not a local
# sibling module inside the plugin's scripts/ dir) is treated as third-party.
# Python 3.10+: sys.stdlib_module_names is the authoritative source.
# For older Pythons we supply a conservative fallback. CPV itself requires
# Python 3.11+ (see pyproject.toml) so this fallback is a safety net only.
_STDLIB_FALLBACK = frozenset({
    "abc", "argparse", "ast", "asyncio", "base64", "binascii", "bisect", "builtins",
    "bz2", "calendar", "cgi", "cmath", "cmd", "codecs", "collections", "colorsys",
    "compileall", "concurrent", "configparser", "contextlib", "contextvars", "copy",
    "csv", "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib",
    "dis", "doctest", "email", "encodings", "enum", "errno", "fcntl", "filecmp",
    "fileinput", "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "graphlib", "grp", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "imaplib", "imp", "importlib", "inspect", "io",
    "ipaddress", "itertools", "json", "keyword", "lib2to3", "linecache", "locale",
    "logging", "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes",
    "mmap", "modulefinder", "multiprocessing", "netrc", "numbers", "operator", "optparse",
    "os", "pathlib", "pdb", "pickle", "pickletools", "pkgutil", "platform", "plistlib",
    "poplib", "posix", "posixpath", "pprint", "profile", "pstats", "pty", "pwd",
    "py_compile", "pyclbr", "pydoc", "queue", "quopri", "random", "re", "readline",
    "reprlib", "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
    "selectors", "shelve", "shlex", "shutil", "signal", "site", "smtplib", "sndhdr",
    "socket", "socketserver", "spwd", "sqlite3", "ssl", "stat", "statistics",
    "string", "stringprep", "struct", "subprocess", "sys", "sysconfig", "syslog",
    "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "textwrap", "threading",
    "time", "timeit", "tkinter", "token", "tokenize", "tomllib", "trace", "traceback",
    "tracemalloc", "tty", "turtle", "types", "typing", "unicodedata", "unittest",
    "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
    "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile",
    "zipimport", "zlib", "zoneinfo",
})
PYTHON_STDLIB_MODULES: frozenset[str] = (
    frozenset(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else _STDLIB_FALLBACK
)


@dataclass(frozen=True)
class ScriptRef:
    """A reference to a script invoked by a hook command.

    invocation_mode captures HOW the script is being launched, which determines
    whether third-party imports will resolve at runtime:
      - "direct"              → `./foo.py` or `/abs/foo.sh` (first token IS the script)
      - "interpreter-python"  → `python3 foo.py` — runs under the hook's ambient
                                python3; third-party imports fail unless globally
                                installed or PEP 723 metadata + uv.
      - "uv-run-script"       → `uv run --script foo.py` — uv resolves deps from
                                the script's PEP 723 inline metadata block.
      - "uv-run-with"         → `uv run --with pkg[,pkg] foo.py` — deps supplied
                                explicitly on the command line.
      - "venv-python"         → `${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py` —
                                runs inside a plugin-scoped venv (which must be
                                set up by a SessionStart install hook).
      - "node" / "bash" / "ruby" / "perl" / "php" → language-specific; no Python
                                runtime-dep reconciliation applies.
    """

    path: Path
    invocation_mode: str
    simple_command: tuple[str, ...] = field(default_factory=tuple)
    # For uv-run-with: the packages passed via --with flags.
    explicit_deps: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class HookValidationReport(ValidationReport):
    """Hook validation report with hook-specific metadata."""

    hook_path: str = ""


def validate_json_structure(hook_path: Path, report: ValidationReport) -> dict[str, Any] | None:
    """Validate hooks.json exists and is valid JSON."""
    if not hook_path.exists():
        report.critical(f"Hook file not found: {hook_path}")
        return None

    try:
        content = hook_path.read_text(encoding="utf-8")
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

    # Check for 'disableAllHooks' field (valid top-level boolean)
    if "disableAllHooks" in data:
        if not isinstance(data["disableAllHooks"], bool):
            report.major(f"'disableAllHooks' must be a boolean, got {type(data['disableAllHooks']).__name__}")
        else:
            report.passed(f"disableAllHooks: {data['disableAllHooks']}")

    # Check for unknown top-level fields. `$schema` is a standard JSON Schema
    # declaration recognized by editors and linters and should not be flagged.
    known_top_level = {"hooks", "description", "disableAllHooks", "$schema"}
    for key in data:
        if key not in known_top_level:
            report.warning(f"Unknown top-level field '{key}' in hooks config")

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
        # Fuzzy match for "did you mean?" suggestions
        close = difflib.get_close_matches(event_name, sorted(VALID_HOOK_EVENTS), n=1, cutoff=0.6)
        if close:
            report.critical(
                f"Unknown hook event: '{event_name}' — did you mean '{close[0]}'? Valid events: {sorted(VALID_HOOK_EVENTS)}"
            )
        else:
            report.critical(f"Unknown hook event: '{event_name}'. Valid events: {sorted(VALID_HOOK_EVENTS)}")
        return False
    # Legacy/extended events — still accepted but nudge users toward the current spec.
    if event_name == "Setup":
        report.warning(
            "Hook event 'Setup' is not in the current official spec (as of Claude Code v2.1.98). "
            "It may be legacy or deprecated. Verify intent; remove if unused."
        )
    return True


def _check_matcher_values(
    matcher: str,
    known_values: set[str],
    event_label: str,
    values_label: str,
    report: ValidationReport,
) -> None:
    """Check matcher parts against a set of known values, reporting info for unknowns."""
    parts = re.split(r"[|()]", matcher)
    for part in parts:
        part = part.strip()
        # Skip empty, wildcard, and regex patterns (contain metacharacters)
        if not part or part == "*" or re.escape(part) != part:
            continue
        if part not in known_values:
            report.info(
                f"{event_label} matcher '{part}' is not a known {values_label} — known values: {', '.join(sorted(known_values))}"
            )


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

    # Validate matcher values against known sets for specific event types.
    # These are INFO-level checks (per _check_matcher_values) — unknown values
    # are hinted at, not rejected, because the spec may grow faster than CPV
    # catches up and plugins can still legitimately use future values.
    if event_name == "Notification":
        _check_matcher_values(matcher, COMMON_NOTIFICATION_TYPES, "Notification", "type", report)
    if event_name == "SessionStart":
        _check_matcher_values(matcher, SESSION_START_SOURCES, "SessionStart", "source", report)
    if event_name == "SessionEnd":
        _check_matcher_values(matcher, SESSION_END_REASONS, "SessionEnd", "reason", report)
    if event_name == "PreCompact":
        _check_matcher_values(matcher, COMPACT_TRIGGERS, "PreCompact", "trigger", report)
    if event_name == "PostCompact":
        _check_matcher_values(matcher, COMPACT_TRIGGERS, "PostCompact", "trigger", report)
    if event_name == "StopFailure":
        _check_matcher_values(matcher, STOPFAILURE_ERRORS, "StopFailure", "error", report)
    if event_name == "InstructionsLoaded":
        _check_matcher_values(matcher, INSTRUCTIONS_LOADED_REASONS, "InstructionsLoaded", "load_reason", report)
    if event_name == "ConfigChange":
        _check_matcher_values(matcher, CONFIG_CHANGE_SOURCES, "ConfigChange", "source", report)

    # GAP-18: FileChanged `matcher` is a FILENAME glob (plugins-reference.md L131),
    # not a tool-name regex. Authors occasionally paste a tool name here by
    # analogy with PreToolUse — emit a NIT-level info when that happens so the
    # misconfiguration doesn't silently match nothing.
    if event_name == "FileChanged":
        # Split on typical glob/regex separators and check whether every part
        # looks like a tool identifier (PascalCase, no dot, no slash, no wildcard).
        parts = [p.strip() for p in re.split(r"[|()]", matcher) if p.strip()]
        tool_like = [
            p for p in parts
            if p in COMMON_TOOL_NAMES and "." not in p and "/" not in p and "*" not in p
        ]
        if tool_like and len(tool_like) == len(parts):
            report.info(
                f"FileChanged matcher '{matcher}' looks like a tool name, but per "
                "plugins-reference.md L131 the FileChanged `matcher` is a FILENAME "
                "glob (e.g. '*.py', 'src/**/*.ts'), not a tool name."
            )

    return True


def _split_compound_command(command: str) -> list[str]:
    """Split a shell command string on top-level `;`, `&&`, `||`, `|`.

    Quote-aware: operators inside single- or double-quoted strings are NOT
    treated as separators. Backslash escapes outside single quotes are
    preserved verbatim (we do not expand them — shlex.split handles that
    later on each simple command).

    Example:
        "unset VIRTUAL_ENV; python3 'a;b.py'"
            → ["unset VIRTUAL_ENV", "python3 'a;b.py'"]
    """
    parts: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if in_single:
            buf.append(c)
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(c)
            if c == "\\" and i + 1 < n:
                buf.append(command[i + 1])
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(command[i + 1])
            i += 2
            continue
        if c == "'":
            in_single = True
            buf.append(c)
            i += 1
            continue
        if c == '"':
            in_double = True
            buf.append(c)
            i += 1
            continue
        # Two-char operators first
        if command[i : i + 2] in ("&&", "||"):
            parts.append("".join(buf).strip())
            buf = []
            i += 2
            continue
        if c in (";", "|"):
            parts.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    last = "".join(buf).strip()
    if last:
        parts.append(last)
    return [p for p in parts if p]


def _tokenize_hook_command(
    command: str,
    malformed_out: list[str] | None = None,
) -> list[list[str]]:
    """Tokenize a hook command into a list of simple-command token lists.

    Splits compound commands on `;`, `&&`, `||`, `|` (via _split_compound_command,
    which is quote-aware), then shlex-splits each simple command in POSIX mode.
    A trailing backgrounding `&` on the whole command is stripped from the
    final simple command.

    Quotes are stripped from values but env-var syntax is preserved literally:
    `"${CLAUDE_PLUGIN_ROOT}/foo.py"` becomes a single token `${CLAUDE_PLUGIN_ROOT}/foo.py`.

    When ``malformed_out`` is provided, every simple command that ``shlex.split``
    cannot tokenize (typically unbalanced quotes) is appended to it. Callers
    with access to a ``ValidationReport`` surface these as MAJOR findings —
    a silent fallback would let an attacker-authored hook with a deliberately
    malformed quote smuggle a script past ``extract_script_paths``.
    """
    s = command.rstrip()
    # Strip trailing single `&` (background marker); preserve `&&`.
    if s.endswith("&") and not s.endswith("&&"):
        s = s[:-1].rstrip()

    simple_commands = _split_compound_command(s)
    out: list[list[str]] = []
    for sc in simple_commands:
        try:
            tokens = shlex.split(sc, posix=True)
        except ValueError:
            # Unterminated quote or other shlex error — fall back to a single
            # whole-string token so downstream code can still make a best-effort
            # path guess, but record the malformed command so the caller can
            # raise a MAJOR (unbalanced quotes obscure what the hook will
            # actually run at event time).
            if malformed_out is not None:
                malformed_out.append(sc)
            tokens = [sc]
        if tokens:
            out.append(tokens)
    return out


def _resolve_plugin_vars(token: str, plugin_root: Path | None) -> str:
    """Substitute only CLAUDE_PLUGIN_ROOT references (both ${...} and $...).

    Other env vars (CLAUDE_PROJECT_DIR, CLAUDE_PLUGIN_DATA, $HOME, ...) are
    intentionally NOT substituted — they are resolved at runtime by Claude
    Code / the shell, and their values are not knowable statically.
    """
    if plugin_root:
        token = token.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
        token = token.replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))
    return token


def _is_lintable_script(path_str: str) -> bool:
    """True if path's suffix is in the lintable-extensions set."""
    suffix = Path(path_str).suffix.lower()
    return suffix in LINTABLE_EXTENSIONS or suffix in {".rb", ".pl", ".php"}


# Interpreter name → invocation_mode tag. First token (basename) is looked up
# here; a version suffix like python3.12 is accepted via regex fallback.
# `py` is the Windows Python Launcher (ships with Python 3 on Windows); its
# version selectors like `-3.12` are handled by the generic boolean-flag
# skipper in _find_script_in_interpreter_args.
# `tsx` and `ts-node` are TypeScript runners that accept a .ts script path
# directly (like a Python interpreter). Treating them under the "node" mode
# is correct for runtime-dep-reconciliation purposes (they don't go through
# the uv/PEP 723 path).
_INTERPRETER_TAGS: dict[str, str] = {
    "python": "interpreter-python",
    "python3": "interpreter-python",
    "py": "interpreter-python",  # Windows Python Launcher
    "node": "node",
    "deno": "node",
    "bun": "node",
    "tsx": "node",
    "ts-node": "node",
    "bash": "bash",
    "sh": "bash",
    "zsh": "bash",
    "dash": "bash",
    "ruby": "ruby",
    "perl": "perl",
    "php": "php",
}
_PYTHON_VERSIONED_RE = re.compile(r"^python3\.\d+$")


def _classify_interpreter(first_token_name: str) -> str | None:
    """Given the basename of a simple command's first token, return the
    invocation_mode tag if it is a known interpreter — else None.

    Strips a trailing `.exe` (case-insensitive) to handle Windows binaries
    and normalizes to lowercase so case-insensitive filesystems (APFS,
    NTFS, FAT) produce stable results.
    """
    if not first_token_name:
        return None
    # Windows binaries carry a .exe suffix; strip (case-insensitively) first.
    lower = first_token_name.lower()
    if lower.endswith(".exe"):
        lower = lower[:-4]
    tag = _INTERPRETER_TAGS.get(lower)
    if tag:
        return tag
    if _PYTHON_VERSIONED_RE.match(lower):
        return "interpreter-python"
    return None


def _detect_venv_python(resolved_token: str) -> bool:
    """True if token looks like a path to a venv's Python binary.

    Matches both POSIX (`.venv/bin/python...`) and Windows (`.venv\\Scripts\\python.exe`)
    layouts. The directory name must literally be ".venv" or "venv" since other
    patterns (e.g. "/usr/bin/python3") must NOT be misclassified.
    Accepts any python binary name in that directory — `python`, `python3`,
    `python3.12`, with or without the Windows `.exe` suffix.
    """
    return bool(
        re.search(
            r"(?:^|[/\\])(?:\.venv|venv)[/\\](?:bin|Scripts)[/\\]python(?:3(?:\.\d+)?)?(?:\.exe)?\b",
            resolved_token,
            flags=re.IGNORECASE,
        )
    )


def _find_script_in_interpreter_args(tokens: list[str]) -> int | None:
    """Given an interpreter-led simple command, return the index of the first
    positional that looks like a script path, or None if there is none.

    Skips common option-with-value flags that do NOT precede a script:
      -m MODULE  / -c CODE   → script-less modes; return None entirely
      -X OPT   / -W OPT      → consumes one value then continues
      --                     → ends option parsing; next token is the script
      other single-dash flags (-u, -B, -E, ...) → boolean-ish, consume one token

    Note on `-W`: Python's `-W` flag takes a warning-action argument (e.g.
    `python -W ignore script.py`). Without special-casing it here, the
    argument `ignore` would be misclassified as the script path and the
    real script would be skipped. Same applies to `-X` (implementation
    options like `-X dev`).
    """
    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok in ("-m", "-c"):
            # -m MODULE / -c CODE — no script path follows. Report as "no script".
            return None
        if tok in ("-X", "-W"):
            # -X OPT / -W ACTION — consume the next token as the option value
            # and continue. If the flag is the last token, just advance.
            i += 2
            continue
        if tok == "--":
            i += 1
            if i < n:
                return i
            return None
        if tok.startswith("-"):
            # Generic single-dash flag without a paired value (e.g. -u, -B).
            i += 1
            continue
        return i
    return None


def _find_script_in_uv_run(tokens: list[str]) -> tuple[int, str, tuple[str, ...]] | None:
    """Parse `uv run [flags] path.py [script-args]`.

    Returns (script_index, invocation_mode, explicit_deps) or None.

    invocation_mode is:
      - "uv-run-script"  if any `--script` flag was supplied
      - "uv-run-with"    if any `--with pkg[,pkg]` was supplied (and not --script)
      - "interpreter-python" otherwise (uv run transparently executes via project python)

    explicit_deps is the tuple of packages passed via --with flags (comma-split).
    """
    if len(tokens) < 3 or tokens[1] != "run":
        return None

    has_script = False
    with_deps: list[str] = []
    # Flags that consume their value as a separate token.
    # Verified against https://docs.astral.sh/uv/reference/cli/#uv-run
    # Do NOT include purely-boolean flags here — they consume 0 args.
    TWO_ARG_FLAGS = {
        "--python",
        "--with",
        "--with-editable",
        "--with-requirements",
        "--directory",
        "--project",
        "--extra",
        "--index",
        "--default-index",
        "--upgrade-package",
        "--reinstall-package",
        "--resolution",
        "--package",
        "--link-mode",
        "--exclude-newer",
        "--env-file",
    }

    i = 2
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        # POSIX end-of-options marker. Everything after `--` is passed to the
        # subprocess verbatim — not parsed as uv flags. The next token (if
        # lintable) is the command/script; flag-looking tokens after `--` are
        # script args, NOT uv options.
        if tok == "--":
            i += 1
            break
        # `--script` and `--gui-script` both mark PEP-723-metadata-driven runs.
        if tok in ("--script", "--gui-script"):
            has_script = True
            i += 1
            continue
        # `--module MODULE` — like `python -m`, no script path follows; the
        # next positional is a module name, not a file. Signal "no script".
        if tok == "--module":
            return None
        # --flag=value form
        if tok.startswith("--") and "=" in tok:
            name, _, value = tok.partition("=")
            if name == "--with":
                with_deps.extend(p.strip() for p in value.split(",") if p.strip())
            i += 1
            continue
        if tok in TWO_ARG_FLAGS:
            # value is the next token
            if tok == "--with" and i + 1 < n:
                with_deps.extend(p.strip() for p in tokens[i + 1].split(",") if p.strip())
            i += 2
            continue
        if tok.startswith("-"):
            # Generic boolean flag (e.g. --quiet, --no-sync, --isolated,
            # --compile-bytecode, --all-extras, --no-editable, -q).
            i += 1
            continue
        # First positional — the script
        break

    if i >= n:
        return None
    script_token = tokens[i]
    if not _is_lintable_script(script_token):
        # Could be `uv run my-tool` (a script entry point), not a file path.
        return None

    if has_script:
        mode = "uv-run-script"
    elif with_deps:
        mode = "uv-run-with"
    else:
        mode = "interpreter-python"
    return (i, mode, tuple(with_deps))


def extract_script_paths(
    command: str,
    plugin_root: Path | None,
    malformed_out: list[str] | None = None,
) -> list[ScriptRef]:
    """Extract every script reference from a hook command, with invocation_mode.

    Handles compound commands (`;`, `&&`, `||`, `|`), interpreter invocations
    (`python3 foo.py`, `node foo.js`, `bash foo.sh`), `uv run [--script]`,
    `${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py`, `env python3 foo.py`,
    and direct script invocations (`./foo.py`). Skips pure side-effect simple
    commands like `unset VAR` and `cd /dir`.

    When ``malformed_out`` is a list, any simple command that ``shlex`` refuses
    to tokenize is appended to it. The canonical caller
    (``validate_command_hook``) surfaces these as MAJOR findings —
    unbalanced-quote commands produce ambiguous parses that can hide scripts
    from lint coverage.

    The new canonical API — prefer this over extract_script_path which is
    retained for backwards compatibility.
    """
    refs: list[ScriptRef] = []
    for original_tokens in _tokenize_hook_command(command, malformed_out=malformed_out):
        if not original_tokens:
            continue
        # Strip leading env-var-assignment tokens: `FOO=bar python3 foo.py`
        # → the command proper is `python3 foo.py`; the assignment only sets
        # env vars for the child process and is semantically invisible to the
        # "what's being invoked" question.
        tokens = list(original_tokens)
        while tokens and _ENV_ASSIGNMENT_RE.match(tokens[0]):
            tokens.pop(0)
        if not tokens:
            continue
        first = _resolve_plugin_vars(tokens[0], plugin_root)
        first_name = Path(first).name

        # 1. Skip pure-side-effect simple commands (unset / cd / export / source ...)
        if first_name in SHELL_NOOPS:
            continue

        # 2. uv / uvx — uvx is a package executor, not a local-script runner
        if first_name == "uv":
            r = _find_script_in_uv_run(tokens)
            if r is not None:
                idx, mode, deps = r
                script_token = _resolve_plugin_vars(tokens[idx], plugin_root)
                if "$" not in script_token:
                    refs.append(
                        ScriptRef(
                            path=Path(script_token),
                            invocation_mode=mode,
                            simple_command=tuple(tokens),
                            explicit_deps=deps,
                        )
                    )
            continue
        if first_name in ("uvx", "pipx"):
            # These invoke remote packages; no local script to lint. The
            # package-executor WARN in validate_command_hook covers the risk.
            continue

        # 3. Venv Python binary — the hook explicitly targets a venv's python
        if _detect_venv_python(first):
            venv_idx: int | None = _find_script_in_interpreter_args(tokens)
            if venv_idx is not None:
                script_token = _resolve_plugin_vars(tokens[venv_idx], plugin_root)
                if _is_lintable_script(script_token) and "$" not in script_token:
                    refs.append(
                        ScriptRef(
                            path=Path(script_token),
                            invocation_mode="venv-python",
                            simple_command=tuple(tokens),
                        )
                    )
            continue

        # 4. Standard interpreter (python3, node, bash, ruby, perl, php, ...)
        interp_mode: str | None = _classify_interpreter(first_name)
        if interp_mode is not None:
            interp_idx: int | None = _find_script_in_interpreter_args(tokens)
            if interp_idx is not None and interp_idx < len(tokens):
                script_token = _resolve_plugin_vars(tokens[interp_idx], plugin_root)
                if _is_lintable_script(script_token) and "$" not in script_token:
                    refs.append(
                        ScriptRef(
                            path=Path(script_token),
                            invocation_mode=interp_mode,
                            simple_command=tuple(tokens),
                        )
                    )
            continue

        # 5. `env [-S] [-i] [...] [VAR=value ...] python3 foo.py`
        # The env utility accepts VAR=value assignments before the command
        # (this is its primary use case — setting env vars for a subprocess).
        # e.g. `env FOO=bar PYTHONPATH=./lib python3 foo.py`. Skip those so
        # we land on the actual interpreter token.
        if first_name == "env":
            j = 1
            while j < len(tokens):
                t = tokens[j]
                if t == "-S":
                    j += 1
                    continue
                if t == "--":
                    j += 1
                    break
                if t.startswith("-"):
                    j += 1
                    continue
                if _ENV_ASSIGNMENT_RE.match(t):
                    # VAR=value before the command — consume and continue.
                    j += 1
                    continue
                break
            if j < len(tokens):
                sub = tokens[j:]
                sub_name = Path(_resolve_plugin_vars(sub[0], plugin_root)).name
                sub_mode = _classify_interpreter(sub_name)
                if sub_mode is not None:
                    env_idx: int | None = _find_script_in_interpreter_args(sub)
                    if env_idx is not None and env_idx < len(sub):
                        script_token = _resolve_plugin_vars(sub[env_idx], plugin_root)
                        if _is_lintable_script(script_token) and "$" not in script_token:
                            refs.append(
                                ScriptRef(
                                    path=Path(script_token),
                                    invocation_mode=sub_mode,
                                    simple_command=tuple(tokens),
                                )
                            )
            continue

        # 6. Direct script invocation — first token IS the script
        first_resolved = first
        if "$" not in first_resolved and _is_lintable_script(first_resolved):
            refs.append(
                ScriptRef(
                    path=Path(first_resolved),
                    invocation_mode="direct",
                    simple_command=tuple(tokens),
                )
            )

    return refs


def extract_script_path(command: str, plugin_root: Path | None) -> Path | None:
    """Legacy single-path extractor.

    Retained for backwards compatibility with existing callers and tests.
    Returns the FIRST script path found by extract_script_paths, or None.
    New code should call extract_script_paths directly and act on the
    invocation_mode of each ScriptRef.
    """
    refs = extract_script_paths(command, plugin_root)
    return refs[0].path if refs else None


# ---------------------------------------------------------------------------
# Python script analysis helpers — used by the runtime-dep reconciliation
# check to decide whether a hook's invocation style will actually be able to
# resolve the referenced script's imports.
# ---------------------------------------------------------------------------


def detect_python_third_party_imports(
    script_path: Path, plugin_script_dir: Path | None = None
) -> set[str]:
    """Parse a Python file with ast and return the set of third-party module
    root names it imports.

    - Stdlib modules (per sys.stdlib_module_names) are excluded.
    - Intra-plugin imports (sibling modules in scripts/ named the same) are
      excluded so plugins using multi-file layouts do not trip the check.
    - Relative imports (from . import x) are never third-party.
    - Dynamic imports (importlib.import_module, __import__) are out of scope —
      static-only detection by design.

    On SyntaxError or missing file, returns an empty set; the caller is
    expected to surface those errors through separate channels.
    """
    if not script_path.exists() or not script_path.is_file():
        return set()
    try:
        source = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    try:
        tree = ast.parse(source, filename=str(script_path))
    except SyntaxError:
        return set()

    local_siblings: set[str] = set()
    if plugin_script_dir and plugin_script_dir.is_dir():
        for sibling in plugin_script_dir.iterdir():
            if sibling.is_file() and sibling.suffix == ".py":
                local_siblings.add(sibling.stem)
            elif sibling.is_dir() and (sibling / "__init__.py").exists():
                local_siblings.add(sibling.name)

    third_party: set[str] = set()
    # Walk Import / ImportFrom at any depth so try/except-guarded imports
    # (e.g. `try: import pycozo except ImportError: ...`) are still detected —
    # the PSS bug fits this exact pattern.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root and root not in PYTHON_STDLIB_MODULES and root not in local_siblings:
                    third_party.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import
            if node.module is None:
                continue
            root = node.module.split(".", 1)[0]
            if root and root not in PYTHON_STDLIB_MODULES and root not in local_siblings:
                third_party.add(root)
    return third_party


_PEP723_BLOCK_RE = re.compile(
    r"(?m)^# /// script\s*$\n(?P<body>(?:^#(?: .*)?\n)+)^# ///\s*$",
)


def detect_pep723_deps(script_path: Path) -> list[str] | None:
    """Parse a PEP 723 inline script metadata block and return its
    dependencies list.

    Returns:
      - list[str] if the block exists and parses (empty list is possible).
      - None if there is no PEP 723 block at all.

    A malformed block (unbalanced markers, invalid TOML, non-list deps field)
    returns an empty list — the caller can treat that as "block present but
    unusable" which still fails reconciliation. We do not surface parse
    errors here; that belongs in a dedicated linter pass if ever needed.
    """
    if not script_path.exists() or not script_path.is_file():
        return None
    try:
        source = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _PEP723_BLOCK_RE.search(source)
    if not match:
        return None
    body_lines = match.group("body").splitlines()
    toml_source_lines: list[str] = []
    for line in body_lines:
        # Each line starts with "#"; strip one optional space after it.
        if line.startswith("# "):
            toml_source_lines.append(line[2:])
        elif line.startswith("#"):
            toml_source_lines.append(line[1:])
        else:
            toml_source_lines.append(line)
    toml_text = "\n".join(toml_source_lines)

    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover — CPV requires Python 3.11+
        return []

    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError:
        return []
    deps = data.get("dependencies")
    if not isinstance(deps, list):
        return []
    return [str(d) for d in deps]


# Well-known import-name ≠ PyPI-name mappings. When a PEP 723 `dependencies`
# block declares e.g. "pillow", the script's `import PIL` statement should be
# considered covered. Without this map, the reconciler would flag every such
# case as a missing declaration. This is a CONSERVATIVE list — only mappings
# that are standard and unambiguous are included. When in doubt, rely on the
# per-module case: import name == PyPI name (after case / dash normalization).
#
# Each entry maps ONE PyPI distribution name to the set of import-time module
# roots it provides. PyPI names use the canonical form (lowercase, dashes).
_PYPI_TO_IMPORT_NAMES: dict[str, frozenset[str]] = {
    "pillow": frozenset({"pil"}),
    "beautifulsoup4": frozenset({"bs4"}),
    "opencv_python": frozenset({"cv2"}),
    "opencv_python_headless": frozenset({"cv2"}),
    "opencv_contrib_python": frozenset({"cv2"}),
    "pyyaml": frozenset({"yaml"}),
    "scikit_learn": frozenset({"sklearn"}),
    "scikit_image": frozenset({"skimage"}),
    "attrs": frozenset({"attr", "attrs"}),
    "msgpack_python": frozenset({"msgpack"}),
    "python_dateutil": frozenset({"dateutil"}),
    "python_levenshtein": frozenset({"levenshtein", "_levenshtein"}),
    "python_dotenv": frozenset({"dotenv"}),
    "python_multipart": frozenset({"multipart"}),
    "python_ldap": frozenset({"ldap"}),
    "mysqlclient": frozenset({"mysqldb", "_mysql"}),
    "psycopg2": frozenset({"psycopg2"}),
    "psycopg2_binary": frozenset({"psycopg2"}),
    "pyjwt": frozenset({"jwt"}),
    "pycryptodome": frozenset({"crypto"}),
    "pycryptodomex": frozenset({"cryptodome"}),
    "protobuf": frozenset({"google"}),  # google.protobuf
    "google_api_python_client": frozenset({"googleapiclient"}),
    "google_cloud_storage": frozenset({"google"}),
    "docopt_ng": frozenset({"docopt"}),
    "click_spinner": frozenset({"click_spinner"}),
    "pynacl": frozenset({"nacl"}),
    "qrcode": frozenset({"qrcode"}),
    "pyserial": frozenset({"serial"}),
    "pygments": frozenset({"pygments"}),
    "pymongo": frozenset({"pymongo", "bson", "gridfs"}),
    "paho_mq": frozenset({"paho"}),
    "paho_mqtt": frozenset({"paho"}),
    "grpcio": frozenset({"grpc"}),
    "grpcio_tools": frozenset({"grpc_tools"}),
    "azure_storage_blob": frozenset({"azure"}),
    "azure_identity": frozenset({"azure"}),
    "boto3": frozenset({"boto3", "botocore"}),
}


def _strip_dep_name(dep_spec: str) -> str:
    """Extract the normalized project name from a PEP 508 requirement spec.

    Examples:
      "pycozo[embedded]>=0.7.6" → "pycozo"
      "httpx ; python_version>='3.10'" → "httpx"
      "my-pkg" → "my_pkg" (normalized to importable name form)
      "Scikit-Learn==1.5" → "scikit_learn"
    """
    # Strip environment markers (after ;), extras (brackets), version specifiers.
    spec = dep_spec.split(";", 1)[0].strip()
    spec = re.split(r"[\[<>=!~ ]", spec, maxsplit=1)[0].strip()
    # PEP 503 name normalization: lowercase, runs of [-_.] collapse to single _
    return re.sub(r"[-_.]+", "_", spec).lower()


def _import_names_covered_by(dep_spec: str) -> set[str]:
    """Return all import-name roots considered "covered" by a single PEP 508 dep.

    For most packages this is just {normalized_name}. For known-alias packages
    (see _PYPI_TO_IMPORT_NAMES) the set is extended with the alias imports.
    All names are normalized (lowercase, underscores).
    """
    canonical = _strip_dep_name(dep_spec)
    covered = {canonical}
    aliases = _PYPI_TO_IMPORT_NAMES.get(canonical)
    if aliases:
        covered.update(aliases)
    return covered


def _is_sys_exit_call(node: ast.AST) -> bool:
    """Recognize `sys.exit(...)`, `os._exit(...)`, `exit(...)`, or `quit(...)`.

    All four terminate the hook process at import time, short-circuiting the
    event handler that Claude Code expects to run. `os._exit` skips cleanup
    handlers — the PSS-class "fatal dep missing" pattern could equally well
    use it.
    """
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    if isinstance(func, ast.Name) and func.id in ("exit", "quit"):
        return True
    if isinstance(func, ast.Attribute):
        target = func.value
        if func.attr == "exit" and isinstance(target, ast.Name) and target.id == "sys":
            return True
        if func.attr == "_exit" and isinstance(target, ast.Name) and target.id == "os":
            return True
    return False


def _is_raise_system_exit(node: ast.AST) -> bool:
    """Recognize `raise SystemExit(...)` or bare `raise SystemExit`."""
    if not isinstance(node, ast.Raise) or node.exc is None:
        return False
    exc = node.exc
    if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "SystemExit":
        return True
    if isinstance(exc, ast.Name) and exc.id == "SystemExit":
        return True
    return False


def _walk_module_scope(node: ast.AST, hits: list[int]) -> None:
    """Walk a module-scope AST subtree, collecting `sys.exit`/`SystemExit`
    line numbers. Descends into import-time-reachable constructs (If, Try,
    For, While, With — all execute on module load) but STOPS at function,
    async function, and class definition bodies (those only run when called).

    This catches the PSS v3.1.0 pattern where the fatal `sys.exit` lived
    inside a top-level `try/except ImportError:` block, plus:
      - if-blocks (documented by the original implementation)
      - try/except/else/finally (new — the PSS pattern)
      - for/while loops evaluated at import time (rare but legal)
      - with-blocks at module scope
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return  # Stop: these bodies don't run at import time.
    # Statement nodes (Expr, Raise, If, Try, For, While, With, ...) always have
    # a `lineno` attribute — ast.AST itself does not, hence the explicit check.
    if _is_sys_exit_call(node) and isinstance(node, ast.stmt):
        hits.append(node.lineno)
        return
    if _is_raise_system_exit(node) and isinstance(node, ast.stmt):
        hits.append(node.lineno)
        return
    # Descend into import-time-reachable container nodes.
    for child in ast.iter_child_nodes(node):
        _walk_module_scope(child, hits)


def detect_module_scope_sys_exit(script_path: Path) -> list[int]:
    """Return line numbers where the script calls sys.exit(), exit(), or
    raises SystemExit at MODULE scope.

    Module-scope SystemExit is especially dangerous in hook scripts because
    ANY importer (including the hook process itself, via module load) is
    killed. This was the proximate mechanism of the PSS v3.1.0 hook crash.

    The detector recurses through every import-time-reachable AST construct
    (module body, if/else, try/except/else/finally, for, while, with) but
    stops at function/class bodies — those only execute when explicitly
    invoked, not on import.

    Returns a list of 1-based line numbers. Empty if none found or the file
    cannot be parsed.
    """
    if not script_path.exists() or not script_path.is_file():
        return []
    try:
        source = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(script_path))
    except SyntaxError:
        return []

    hits: list[int] = []
    for node in tree.body:
        _walk_module_scope(node, hits)
    return sorted(set(hits))


# ---------------------------------------------------------------------------
# Runtime-dep reconciliation
# ---------------------------------------------------------------------------


def reconcile_python_runtime_deps(
    ref: ScriptRef,
    plugin_root: Path | None,
    hooks_json_data: dict[str, Any] | None,
    report: ValidationReport,
) -> None:
    """For a Python ScriptRef, verify that the hook's invocation method
    can actually resolve the script's third-party imports.

    Matrix (see TRDD-0028dd34 §6.5):
      interpreter-python + third-party imports                    → MAJOR
      uv-run-script      + PEP 723 covers all imports             → PASSED
      uv-run-script      + PEP 723 missing / incomplete            → MAJOR
      uv-run-with        + --with covers all imports               → PASSED
      uv-run-with        + --with incomplete                      → MAJOR
      venv-python        + SessionStart venv-setup hook present    → PASSED
      venv-python        + no SessionStart setup                   → MINOR
      stdlib-only (any mode)                                      → silent PASS
    """
    if ref.invocation_mode not in (
        "interpreter-python",
        "uv-run-script",
        "uv-run-with",
        "venv-python",
    ):
        return  # non-Python script (node/bash/...) or direct .py — out of scope

    script_path = ref.path
    if not script_path.exists():
        return  # absence reported elsewhere

    plugin_script_dir = plugin_root / "scripts" if plugin_root else None
    imports = detect_python_third_party_imports(script_path, plugin_script_dir)
    if not imports:
        return  # stdlib-only — any invocation style works

    def _covered_by(dep_specs: list[str]) -> tuple[set[str], set[str]]:
        """Set-difference the script's imports against the declared deps,
        expanding each dep through the PyPI→import-name alias map so that
        e.g. `pillow` in dependencies covers `import PIL` in the script.
        """
        declared: set[str] = set()
        for spec in dep_specs:
            declared.update(_import_names_covered_by(spec))
        needed = {re.sub(r"[-_.]+", "_", i).lower() for i in imports}
        missing = needed - declared
        covered = needed & declared
        return covered, missing

    label = f"{script_path.name} (imports: {', '.join(sorted(imports))})"

    if ref.invocation_mode == "interpreter-python":
        report.major(
            f"Hook invokes {label} via plain interpreter — third-party imports "
            f"{sorted(imports)} will fail at runtime unless satisfied via "
            f"`uv run --script` + PEP 723 metadata, `uv run --with`, or a "
            f"${{CLAUDE_PLUGIN_DATA}}/.venv/bin/python set up by a SessionStart hook. "
            f"(Note: do NOT substitute `uvx` — `uvx` / `uv tool run` runs installable "
            f"PyPI packages via entry-points, not local script files with PEP 723 metadata. "
            f"There is no `uvx --script` flag. `uv run --script` is the correct tool here.)"
        )
        return

    if ref.invocation_mode == "uv-run-script":
        deps = detect_pep723_deps(script_path)
        if deps is None:
            report.major(
                f"Hook uses `uv run --script` on {script_path.name} but the "
                f"script has no PEP 723 inline metadata block. Add a `# /// script` "
                f"header declaring dependencies: {sorted(imports)}."
            )
            return
        _, missing = _covered_by(deps)
        if missing:
            report.major(
                f"PEP 723 metadata in {script_path.name} is missing declarations "
                f"for imported third-party modules: {sorted(missing)}. "
                f"Add them to the `dependencies` list in the `# /// script` block."
            )
        else:
            report.passed(
                f"Runtime-dep reconciliation: {script_path.name} via `uv run --script` — "
                f"PEP 723 metadata covers all third-party imports."
            )
        return

    if ref.invocation_mode == "uv-run-with":
        _, missing = _covered_by(list(ref.explicit_deps))
        if missing:
            report.major(
                f"`uv run --with` flags do not cover imported modules "
                f"{sorted(missing)} in {script_path.name}. Add them to --with."
            )
        else:
            report.passed(
                f"Runtime-dep reconciliation: {script_path.name} — "
                f"`uv run --with` covers all third-party imports."
            )
        return

    if ref.invocation_mode == "venv-python":
        # Look for a SessionStart hook that sets up the venv.
        has_setup = False
        if hooks_json_data:
            sessions = hooks_json_data.get("hooks", {}).get("SessionStart", [])
            for block in sessions if isinstance(sessions, list) else []:
                for h in block.get("hooks", []) if isinstance(block, dict) else []:
                    cmd = h.get("command", "") if isinstance(h, dict) else ""
                    if not isinstance(cmd, str):
                        continue
                    # Heuristic: any venv-creation or pip-install command targeting
                    # ${CLAUDE_PLUGIN_DATA} counts as setup.
                    if "CLAUDE_PLUGIN_DATA" in cmd and re.search(
                        r"\b(uv\s+venv|python\s+-m\s+venv|pip\s+install)\b", cmd
                    ):
                        has_setup = True
                        break
                if has_setup:
                    break
        if has_setup:
            report.passed(
                f"Runtime-dep reconciliation: {script_path.name} via "
                f"${{CLAUDE_PLUGIN_DATA}}/.venv/bin/python — SessionStart venv-setup hook present."
            )
        else:
            report.minor(
                f"Hook invokes {script_path.name} via ${{CLAUDE_PLUGIN_DATA}}/.venv/bin/python "
                f"but no SessionStart hook was found that creates the venv (expected: a command "
                f"containing `uv venv` or `pip install` targeting ${{CLAUDE_PLUGIN_DATA}}). "
                f"First-install runs will fail with ImportError for {sorted(imports)}."
            )
        return


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
    # --ignore-missing-imports is KEPT deliberately. Without it, every hook
    # script that imports anything outside its own venv floods the report
    # with "Library stubs not installed / module not found" noise. The real
    # "will my import resolve at runtime?" question is answered precisely by
    # reconcile_python_runtime_deps(), which cross-references actual script
    # imports against the hook's invocation method (uv run --script + PEP 723,
    # --with flags, or a SessionStart-provisioned venv). Don't re-enable the
    # flag expecting a PSS-style regression catch — that job belongs to the
    # reconciliation check, not mypy.
    mypy_cmd = resolve_tool_command("mypy")
    if mypy_cmd:
        try:
            result = subprocess.run(
                mypy_cmd
                + [
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
    hooks_json_data: dict[str, Any] | None = None,
) -> bool:
    """Validate a command-type hook.

    hooks_json_data is the parsed top-level hooks.json (passed through from
    validate_hooks) so the runtime-dep reconciliation check can look for a
    SessionStart venv-setup hook elsewhere in the same file. Defaults to
    None for backwards compatibility with callers that only have a single
    hook in hand.
    """
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

    # Check for hardcoded absolute paths — plugins must use env vars for portability
    cmd_first_token = command.strip().split()[0] if command.strip() else ""
    # Strip quotes from the token
    cmd_first_token = cmd_first_token.strip("'\"")
    if (
        cmd_first_token.startswith("/")
        and not cmd_first_token.startswith("${")
        and not cmd_first_token.startswith("$CLAUDE_")
    ):
        report.major(
            f"Command uses absolute path '{cmd_first_token}' — use ${{CLAUDE_PLUGIN_ROOT}} or ${{CLAUDE_PROJECT_DIR}} for portability"
        )

    # Bash command portability checks
    stripped_cmd = command.strip()

    # 3a: Script file as first token without an explicit interpreter prefix
    script_extensions = {".py", ".js", ".ts", ".sh", ".rb", ".pl"}
    if cmd_first_token and any(cmd_first_token.endswith(ext) for ext in script_extensions):
        # Check if the script is being invoked directly (first token) vs via an interpreter
        # e.g. "python3 script.py" -> first token is "python3", second is script -> OK
        # e.g. "./script.py" -> first token IS the script -> warn
        cmd_tokens = command.strip().split()
        interpreter_names = {"python", "python3", "node", "bun", "deno", "bash", "sh", "ruby", "perl", "env"}
        has_interpreter = len(cmd_tokens) >= 2 and cmd_tokens[0] in interpreter_names
        if not has_interpreter:
            report.minor(
                f"Command runs '{cmd_first_token}' without an explicit interpreter — add one (e.g. python3, node, bash) for cross-platform reliability"
            )

    # 3b: Tilde path that may not expand in hook commands
    if re.search(r"(^|\s)~/", stripped_cmd):
        report.minor(
            "Command uses '~/' path — tilde expansion may not work in hook commands. Use $HOME/ or ${CLAUDE_PLUGIN_ROOT}/ instead."
        )

    # 3c: Bare 'cd' without chained command (no effect in fresh shell)
    if (
        (stripped_cmd.startswith("cd ") or stripped_cmd == "cd")
        and "&&" not in stripped_cmd
        and ";" not in stripped_cmd
    ):
        report.minor(
            "'cd' alone has no effect — each hook runs in a fresh shell. Combine with your command: 'cd /dir && your-command'"
        )

    # 3d: Windows-style backslash paths. JSON parsing has already un-escaped
    # `\\` → `\`, so by the time we see the command string the backslashes
    # are single chars. Match either a drive-letter prefix (`C:\`) or a
    # path-segment separator where a backslash is followed by an alphanumeric
    # path component (`\scripts`). We require the backslash to come after a
    # path-ish anchor (start of token, `/`, or drive colon) to avoid matching
    # escape sequences like `\n` / `\t` that can legitimately appear inside
    # quoted string arguments.
    if re.search(r"[A-Za-z]:\\[A-Za-z0-9_.\- ]|(?:^|[\s/])\\[A-Za-z]", command):
        report.minor(
            "Command contains Windows-style backslash paths — use forward slashes for cross-platform compatibility"
        )

    # 3e: Path-traversal detection. Hook commands MUST NOT reference script
    # paths that escape the plugin root via `..` segments. A path like
    # `${CLAUDE_PLUGIN_ROOT}/../other-plugin/foo.py` breaks plugin isolation
    # and may violate the security model. Same rule for `${CLAUDE_PROJECT_DIR}`,
    # `${CLAUDE_PLUGIN_DATA}`, and bare `$HOME` references.
    #
    # We check the original (unresolved) command string for `..` segments that
    # appear immediately after an env-var prefix OR as a path component
    # anywhere inside a path-looking token. Tokenizer-level detection would
    # miss traversal inside quoted strings that shlex normalizes.
    _TRAVERSAL_RE = re.compile(
        r"""
        (?:
            # env-var-prefixed (POSIX forward slash):  ${VAR}/.. or $VAR/..
            \$\{?CLAUDE_[A-Z_]+\}?/\.\./
            |
            # env-var-prefixed (Windows backslash): ${VAR}\.. or $VAR\..
            \$\{?CLAUDE_[A-Z_]+\}?\\\.\.\\
            |
            # POSIX absolute path containing /../ (not //./ or similar)
            /[A-Za-z0-9_.\-]+/\.\./
            |
            # Windows path containing \..\  — matches both C:\foo\..\bar
            # and UNC-style \\server\share\..\bar. Excludes \. escape
            # sequences (e.g. \t, \n) by requiring an actual \..\ segment
            # between path components.
            \\[A-Za-z0-9_.\-]+\\\.\.\\
        )
        """,
        re.VERBOSE,
    )
    if _TRAVERSAL_RE.search(command):
        report.warning(
            "Command contains a `..` path segment that escapes the plugin/project root — "
            "this is a path-traversal pattern that may break plugin isolation or enable "
            "cross-plugin interference. If the traversal is intentional (e.g. accessing "
            "a sibling directory in a monorepo), document it; otherwise rewrite the path "
            "to reference `${CLAUDE_PLUGIN_ROOT}/...` or `${CLAUDE_PROJECT_DIR}/...` directly."
        )

    # Relative path without $CLAUDE_PLUGIN_ROOT or $CLAUDE_PLUGIN_DATA — may not resolve at runtime
    if (
        cmd_first_token.startswith("./")
        and "${CLAUDE_PLUGIN_ROOT}" not in command
        and "$CLAUDE_PLUGIN_ROOT" not in command
        and "${CLAUDE_PLUGIN_DATA}" not in command
        and "$CLAUDE_PLUGIN_DATA" not in command
    ):
        report.minor(
            f"Command uses relative path '{cmd_first_token}' without ${{CLAUDE_PLUGIN_ROOT}} — hook working directory is not guaranteed. Use ${{CLAUDE_PLUGIN_ROOT}}/... for reliability."
        )

    # Security warning for package executors running remote packages in hooks
    package_executors = {"npx", "bunx", "uvx", "pipx", "pnpx"}
    if cmd_first_token in package_executors:
        # Extract the package name from the rest of the command
        cmd_parts = command.strip().split()
        # Skip flags (--yes, -y, etc.) to find the package name
        pkg_name = None
        for part in cmd_parts[1:]:
            if not part.startswith("-"):
                pkg_name = part
                break
        if pkg_name and not pkg_name.startswith((".", "/", "${")):
            report.warning(
                f"Hook command uses {cmd_first_token} to execute remote package '{pkg_name}' — this downloads and runs code from a registry. Verify the package is trusted and consider pinning a version."
            )

    # Validate timeout if present (Claude Code hooks use seconds; default 600 for command)
    if "timeout" in hook:
        timeout = hook["timeout"]
        if not isinstance(timeout, (int, float)):
            report.major(f"'timeout' must be a number, got {type(timeout).__name__}")
        elif timeout <= 0:
            report.major("'timeout' must be positive")
        elif timeout > 10000:
            report.warning(
                f"Command hook timeout is {timeout}s — this looks like milliseconds. Hook timeouts are in SECONDS (default: 600 for command hooks)."
            )
        elif timeout > 600:
            report.warning(f"Command hook timeout is {timeout}s — exceeds default 600s")

    # Check for environment variable usage
    if "CLAUDE_ENV_FILE" in command:
        if event_name not in {"SessionStart", "Setup"}:
            report.major("CLAUDE_ENV_FILE is only available in SessionStart and Setup hooks")

    # Extract and validate every script referenced by this hook command.
    # Unlike the legacy extractor, this recognizes interpreter forms
    # (`python3 foo.py`), compound commands (`unset VAR; python3 foo.py`),
    # `uv run [--script]`, `${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py`,
    # and `env python3 foo.py` — the extractor also reports the invocation_mode
    # which drives runtime-dep reconciliation and module-scope sys.exit checks.
    #
    # ``malformed_parts`` collects any simple command that ``shlex`` could not
    # tokenize (unbalanced quote, etc.). These land as MAJOR findings because
    # silent fallback would let an attacker-authored hook smuggle a script
    # past lint coverage via a deliberately malformed quote.
    malformed_parts: list[str] = []
    refs = extract_script_paths(command, plugin_root, malformed_out=malformed_parts)
    for malformed in malformed_parts:
        report.major(
            f"Hook command simple-command portion is unparseable (likely unbalanced quote): "
            f"{malformed!r}. Fix the quoting — the validator can only make a best-effort "
            "path guess and may miss malicious payloads smuggled inside the broken region."
        )

    # Antipattern: env-stripping that defeats isolation without providing any
    # replacement. Three patterns exist in the wild:
    #
    #   1. `unset VIRTUAL_ENV; python3 foo.py`
    #      → BAD. PSS v3.1.0 did exactly this, fell back to system python3 with
    #        no pycozo. The user explicitly sheds the project venv and then
    #        depends on ambient python3 having the deps.
    #
    #   2. `unset VIRTUAL_ENV; uv run --script foo.py`
    #      → LEGITIMATE. Defensive belt-and-suspenders — uv respects VIRTUAL_ENV
    #        by default and might try to sync into it; unsetting it forces uv
    #        to create its own script-scoped cache venv.
    #
    #   3. `unset VIRTUAL_ENV; ${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py`
    #      → LEGITIMATE. Direct-invocation of a venv's python resolves sys.prefix
    #        from the binary path regardless of VIRTUAL_ENV, so unsetting it is
    #        redundant but harmless.
    #
    # Warn only on case 1 — the combination of env-stripping with a plain
    # interpreter fallback is the actual foot-gun.
    has_plain_python = any(ref.invocation_mode == "interpreter-python" for ref in refs)
    has_safer_python = any(
        ref.invocation_mode in ("uv-run-script", "uv-run-with", "venv-python") for ref in refs
    )
    if re.search(r"\bunset\s+VIRTUAL_ENV\b", command) and has_plain_python and not has_safer_python:
        report.warning(
            "Command runs `unset VIRTUAL_ENV` and then invokes a plain `python3` interpreter. "
            "Unsetting VIRTUAL_ENV removes the user's venv but leaves you depending on whatever "
            "`python3` resolves to on PATH — typically system Python with none of the project's "
            "dependencies. This is how PSS v3.1.0's UserPromptSubmit hook crashed. Isolate "
            "properly: `uv run --script` with PEP 723 metadata, or "
            "`${CLAUDE_PLUGIN_DATA}/.venv/bin/python` (its sys.prefix is resolved from the binary "
            "path, so VIRTUAL_ENV is ignored without needing to unset it)."
        )
    if re.search(r"\bunset\s+PYTHONPATH\b", command) and has_plain_python and not has_safer_python:
        report.warning(
            "Command runs `unset PYTHONPATH` before a plain `python3` invocation — by itself this "
            "does not provide meaningful isolation. Prefer `uv run --script` or direct-invocation "
            "of a venv's python binary."
        )
    for ref in refs:
        script_path = ref.path
        # Path-traversal check: verify the resolved script path does not escape
        # the plugin root. This catches cases the command-level regex misses —
        # e.g. a path that uses `..` via a pass-through wrapper or a script
        # that legitimately looks clean in the command string but whose
        # components resolve outside the plugin on the filesystem.
        if plugin_root is not None:
            try:
                plugin_root_resolved = plugin_root.resolve(strict=False)
                script_resolved = script_path.resolve(strict=False)
                # On Python 3.9+ Path.is_relative_to is available.
                if not script_resolved.is_relative_to(plugin_root_resolved):
                    # Only warn if the script path used an env var that SHOULD
                    # anchor it to the plugin root — external paths like
                    # ${CLAUDE_PROJECT_DIR}/... or bare /usr/... are legitimate.
                    cmd_original = hook.get("command", "")
                    if (
                        "${CLAUDE_PLUGIN_ROOT}" in cmd_original
                        or "$CLAUDE_PLUGIN_ROOT" in cmd_original
                    ) and "CLAUDE_PROJECT_DIR" not in cmd_original:
                        report.warning(
                            f"Script path `{script_path}` resolves OUTSIDE the plugin root "
                            f"`{plugin_root_resolved}` via `..` segments — this breaks plugin "
                            f"isolation. If cross-plugin access is truly required, use "
                            f"${{CLAUDE_PROJECT_DIR}} to anchor the path at the project root "
                            f"instead, or document the dependency explicitly."
                        )
            except (OSError, ValueError):
                # Path resolution failed (e.g. permission error, invalid
                # filename) — the subsequent exists() check will catch any
                # truly broken paths. Don't surface noise here.
                pass
        if script_path.exists():
            validate_script(script_path, report)
            # Python-only: reconcile script third-party imports against the hook's
            # declared resolution path. Covered modes:
            #   - interpreter-python  (python3 foo.py)
            #   - uv-run-script       (uv run --script foo.py, PEP 723 metadata)
            #   - uv-run-with         (uv run --with pkg foo.py)
            #   - venv-python         (${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py)
            #   - direct              (./foo.py — relies on shebang; SAME runtime
            #                          risk as interpreter-python because the
            #                          shebang resolves to an ambient python.
            #                          This is the pattern used when a hook
            #                          script is chmod +x and invoked directly.)
            #
            # For the `direct` mode we promote to `interpreter-python` semantics
            # for reconciliation purposes — the diagnosis and fix are identical.
            is_python_reconcilable = (
                script_path.suffix.lower() == ".py"
                and ref.invocation_mode in (
                    "interpreter-python",
                    "uv-run-script",
                    "uv-run-with",
                    "venv-python",
                    "direct",
                )
            )
            if is_python_reconcilable:
                if ref.invocation_mode == "direct":
                    # Reconcile as if it were interpreter-python — the shebang
                    # resolves to whatever `python` / `python3` means on PATH.
                    ref = ScriptRef(
                        path=ref.path,
                        invocation_mode="interpreter-python",
                        simple_command=ref.simple_command,
                        explicit_deps=ref.explicit_deps,
                    )
                reconcile_python_runtime_deps(ref, plugin_root, hooks_json_data, report)
                # Module-scope sys.exit / raise SystemExit — any importer (including
                # the hook process itself, via module load) is killed on import.
                exit_lines = detect_module_scope_sys_exit(script_path)
                if exit_lines:
                    report.major(
                        f"{script_path.name} calls sys.exit()/exit()/raise SystemExit at MODULE "
                        f"scope (line(s): {exit_lines}) — the hook process will be killed at import "
                        f"time if the call path is reached. Move such exits inside a function "
                        f"guarded by `if __name__ == '__main__':` or raise ImportError instead."
                    )
        else:
            # Script path detected but doesn't exist — report only if the command
            # used a resolvable root (${CLAUDE_PLUGIN_ROOT}) or an absolute path.
            # Paths using ${CLAUDE_PROJECT_DIR} or ${CLAUDE_PLUGIN_DATA} are
            # resolved at runtime and may legitimately not exist during validation.
            if (
                plugin_root
                and "${CLAUDE_PLUGIN_ROOT}" not in hook["command"]
                and "${CLAUDE_PLUGIN_DATA}" not in hook["command"]
                and "$CLAUDE_PROJECT_DIR" not in hook["command"]
                and "${CLAUDE_PROJECT_DIR}" not in hook["command"]
            ):
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

    # Validate optional model field
    if "model" in hook:
        if not isinstance(hook["model"], str) or not hook["model"].strip():
            report.major("Prompt hook 'model' must be a non-empty string")

    # Validate timeout if present (seconds; default 30 for prompt hooks per hooks.md L2147).
    # CPV-P2-m1: prompt hook default is 30s. A timeout > 300s is already 10× the
    # spec default and usually indicates a misconfiguration (e.g., user meant
    # milliseconds). The absurd-looking ">10000" check below was unreachable
    # because it came after "> 600"; we now order the checks so each branch is
    # actually reachable: millisecond-likely > very-long > suspiciously-long.
    if "timeout" in hook:
        timeout = hook["timeout"]
        if not isinstance(timeout, (int, float)):
            report.major(f"'timeout' must be a number, got {type(timeout).__name__}")
        elif timeout <= 0:
            report.major("'timeout' must be positive")
        elif timeout > 10000:
            # Almost certainly someone typed milliseconds — 10000s = 2.7 hours.
            report.warning(
                f"Prompt hook timeout is {timeout}s — this looks like milliseconds. Hook timeouts are in SECONDS (default: 30 for prompt hooks)."
            )
        elif timeout > 600:
            report.warning(f"Prompt hook timeout is {timeout}s — exceeds 600s")
        elif timeout > 300:
            # CPV-P2-m1: prompt default is 30s; >10× is suspicious. MINOR nudge,
            # not a block — a legitimate long-running analysis prompt is possible.
            report.minor(
                f"Prompt hook timeout is {timeout}s — more than 10× the 30s default "
                "(hooks.md L2147). Confirm this is intentional."
            )

    return True


def validate_http_hook(
    hook: dict[str, Any],
    event_name: str,
    report: ValidationReport,
) -> bool:
    """Validate an HTTP-type hook (v2.1.63+: POST JSON to a URL)."""
    if "url" not in hook:
        report.critical("HTTP hook missing required 'url' field")
        return False

    url = hook["url"]
    if not isinstance(url, str):
        report.critical(f"HTTP hook 'url' must be a string, got {type(url).__name__}")
        return False

    url_stripped = url.strip()
    if not url_stripped:
        report.critical("HTTP hook 'url' cannot be empty")
        return False

    # Basic URL validation — must start with http:// or https://
    if not url_stripped.startswith(("http://", "https://")):
        report.major(f"HTTP hook 'url' should start with http:// or https://, got: {url_stripped[:40]}")

    # Validate optional headers field
    if "headers" in hook:
        headers = hook["headers"]
        if not isinstance(headers, dict):
            report.major(f"HTTP hook 'headers' must be an object, got {type(headers).__name__}")
        else:
            for k, v in headers.items():
                if not isinstance(v, str):
                    report.major(f"HTTP hook header '{k}' value must be a string")

    # Validate optional allowedEnvVars field (list of env var names for header interpolation)
    if "allowedEnvVars" in hook:
        allowed = hook["allowedEnvVars"]
        if not isinstance(allowed, list):
            report.major(f"HTTP hook 'allowedEnvVars' must be an array, got {type(allowed).__name__}")
        elif not all(isinstance(v, str) for v in allowed):
            report.major("HTTP hook 'allowedEnvVars' must contain only strings")

    # Validate timeout if present (seconds)
    # Latency-sensitive events block user interaction: UserPromptSubmit and
    # PreToolUse run synchronously on the critical path, so a slow HTTP hook
    # here degrades every interaction. Warn on generous timeouts for those.
    # (This is the sole reason validate_http_hook takes `event_name` — without
    # it the parameter would be dead weight.)
    LATENCY_SENSITIVE_EVENTS = {
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
        "SessionStart",
    }
    if "timeout" in hook:
        timeout = hook["timeout"]
        if not isinstance(timeout, (int, float)):
            report.major(f"HTTP hook 'timeout' must be a number, got {type(timeout).__name__}")
        elif timeout <= 0:
            report.major("HTTP hook 'timeout' must be positive")
        elif timeout > 600:
            report.warning(f"HTTP hook timeout is {timeout}s — exceeds 600s")
        elif event_name in LATENCY_SENSITIVE_EVENTS and timeout > 5:
            report.warning(
                f"HTTP hook on '{event_name}' has a {timeout}s timeout — this event "
                f"blocks user interaction, so every invocation can stall for up to "
                f"{timeout}s on a slow/failing endpoint. Consider `async: true` or "
                f"a shorter timeout (<= 5s)."
            )

    report.passed(f"HTTP hook URL: {url_stripped[:60]}")
    return True


def validate_single_hook(
    hook: Any,
    event_name: str,
    plugin_root: Path | None,
    report: HookValidationReport,
    hooks_json_data: dict[str, Any] | None = None,
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

    # Validate hook type is allowed for this event
    if event_name in COMMAND_STRICT_EVENTS and hook_type != "command":
        # hooks.md L687/L2109 — SessionStart supports ONLY command hooks.
        hook_path_str = report.hook_path
        report.critical(
            f"Event '{event_name}' only supports 'command' hooks, not '{hook_type}'. "
            "Per hooks.md L687/L2109, http/prompt/agent hooks are not supported for this event.",
            hook_path_str,
        )
    elif hook_type in {"prompt", "agent"} and event_name in COMMAND_ONLY_EVENTS:
        hook_path_str = report.hook_path
        report.critical(
            f"Event '{event_name}' only supports 'command' or 'http' hooks, not '{hook_type}'. Prompt and agent hooks are not supported for this event.",
            hook_path_str,
        )

    # Validate async field (only valid on command/http hooks)
    if hook.get("async") is True and hook_type not in {"command", "http"}:
        hook_path_str = report.hook_path
        report.major(
            f"'async: true' is only supported on 'command' or 'http' hooks, not '{hook_type}'. Prompt and agent hooks cannot run asynchronously.",
            hook_path_str,
        )

    # asyncRewake implies async per hooks.md L305. Flag contradictions.
    if "asyncRewake" in hook:
        rewake_val = hook.get("asyncRewake")
        if rewake_val is True and hook.get("async") is False:
            hook_path_str = report.hook_path
            report.minor(
                "'asyncRewake: true' implies 'async: true' (hooks.md L305) — "
                "'async: false' contradicts it and the hook will still run in the background.",
                hook_path_str,
            )

    # Validate based on type
    if hook_type == "command":
        if not validate_command_hook(hook, event_name, plugin_root, report, hooks_json_data):
            return False
    elif hook_type == "http":
        if not validate_http_hook(hook, event_name, report):
            return False
    elif hook_type == "prompt":
        if not validate_prompt_hook(hook, event_name, report):
            return False
    elif hook_type == "agent":
        # Agent hooks: require prompt, support model and timeout
        hook_path_str = report.hook_path
        if "prompt" not in hook:
            report.critical(
                "Agent hook missing required 'prompt' field",
                hook_path_str,
            )
        elif not isinstance(hook["prompt"], str) or not hook["prompt"].strip():
            report.major(
                "Agent hook 'prompt' must be a non-empty string",
                hook_path_str,
            )
        # Validate optional model field
        if "model" in hook:
            if not isinstance(hook["model"], str) or not hook["model"].strip():
                report.major("Agent hook 'model' must be a non-empty string", hook_path_str)
        # Agent hooks have a default timeout of 60s
        if "timeout" in hook:
            timeout = hook["timeout"]
            if not isinstance(timeout, (int, float)):
                report.major("Agent hook 'timeout' must be a number (seconds)", hook_path_str)
            elif timeout <= 0:
                report.major("Agent hook 'timeout' must be positive", hook_path_str)
            elif timeout > 600:
                report.minor("Agent hook timeout exceeds 10 minutes", hook_path_str)

    # Validate statusMessage field (common to all hook types)
    if "statusMessage" in hook:
        if not isinstance(hook["statusMessage"], str):
            report.major("'statusMessage' must be a string")

    # Validate 'once' field (only valid in skill hooks)
    if "once" in hook:
        once = hook["once"]
        if not isinstance(once, bool):
            report.major(f"'once' must be a boolean, got {type(once).__name__}")
        else:
            report.info("'once' field detected (only works in skill-defined hooks)")

    # Validate 'async' field — only valid on command hooks
    if "async" in hook:
        async_val = hook["async"]
        if not isinstance(async_val, bool):
            report.major(f"'async' must be a boolean, got {type(async_val).__name__}")
        elif hook_type != "command":
            report.minor(f"'async' field is only valid on command hooks, not '{hook_type}' hooks")

    # Validate 'model' field — only valid on prompt/agent hooks
    if "model" in hook and hook_type not in ("prompt", "agent"):
        report.minor(f"'model' field is only valid on prompt/agent hooks, not '{hook_type}' hooks")

    # Validate 'shell' field — only valid on command hooks, values: "bash" or "powershell"
    if "shell" in hook:
        shell_val = hook["shell"]
        if hook_type != "command":
            report.minor(f"'shell' field is only valid on command hooks, not '{hook_type}' hooks")
        elif not isinstance(shell_val, str):
            report.major(f"'shell' must be a string, got {type(shell_val).__name__}")
        elif shell_val not in ("bash", "powershell"):
            report.major(f"'shell' must be 'bash' or 'powershell', got '{shell_val}'")

    # Validate 'if' field — only valid on tool events (PreToolUse, PostToolUse, PostToolUseFailure)
    if "if" in hook:
        if_val = hook["if"]
        if not isinstance(if_val, str):
            report.major(f"'if' must be a string (permission rule syntax), got {type(if_val).__name__}")
        elif event_name and event_name not in {
            "PreToolUse",
            "PostToolUse",
            "PostToolUseFailure",
            "PermissionRequest",
            "PermissionDenied",
        }:
            report.warning(
                f"'if' field is designed for tool events (PreToolUse, PostToolUse, etc.), not '{event_name}'"
            )

    # Check for unknown fields — warn but don't block, as custom fields
    # may be consumed by plugin scripts or external tooling
    known_hook_fields = {
        "type",
        "command",
        "prompt",
        "url",  # HTTP hooks (v2.1.63+)
        "headers",  # HTTP hooks (v2.1.63+)
        "allowedEnvVars",  # HTTP hooks — env vars for header interpolation
        "model",
        "timeout",
        "async",
        "asyncRewake",  # v2.1.98+ — background hook, wakes Claude on exit code 2 (implies async)
        "matcher",
        "statusMessage",
        "once",
        "description",
        "if",  # v2.1.85 — conditional execution using permission rule syntax
        "shell",  # v2.1.84 — "bash" (default) or "powershell" (Windows)
    }
    for key in hook:
        if key not in known_hook_fields:
            report.warning(
                f"Unknown hook field '{key}' — not part of the Claude Code hook spec. If used by plugin scripts, consider documenting it."
            )

    return True


def validate_matcher_block(
    matcher_block: Any,
    event_name: str,
    plugin_root: Path | None,
    report: HookValidationReport,
    hooks_json_data: dict[str, Any] | None = None,
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
        if not validate_single_hook(hook, event_name, plugin_root, report, hooks_json_data):
            all_valid = False

    return all_valid


def validate_event_hooks(
    event_name: str,
    event_config: Any,
    plugin_root: Path | None,
    report: HookValidationReport,
    hooks_json_data: dict[str, Any] | None = None,
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
        if not validate_matcher_block(matcher_block, event_name, plugin_root, report, hooks_json_data):
            all_valid = False

    if all_valid:
        report.passed(f"All hooks valid for {event_name}")

    return all_valid


def validate_hooks(
    hook_path: Path,
    plugin_root: Path | None = None,
) -> HookValidationReport:
    """Validate a complete hooks.json file.

    Args:
        hook_path: Path to the hooks.json file
        plugin_root: Optional plugin root directory for resolving paths

    Returns:
        ValidationReport with all results
    """
    report = HookValidationReport(hook_path=str(hook_path))

    # Parse JSON
    data = validate_json_structure(hook_path, report)
    if data is None:
        return report

    # Validate top-level structure
    if not validate_top_level_structure(data, report):
        return report

    # Validate each event. Pass the parsed top-level document through so
    # per-event checks (runtime-dep reconciliation, specifically) can look at
    # sibling events — e.g. a UserPromptSubmit hook that invokes
    # ${CLAUDE_PLUGIN_DATA}/.venv/bin/python needs to know whether a
    # SessionStart hook elsewhere in the same file sets up that venv.
    hooks = data["hooks"]
    for event_name, event_config in hooks.items():
        if not validate_event_name(event_name, report):
            continue

        validate_event_hooks(event_name, event_config, plugin_root, report, hooks_json_data=data)

    return report


def print_results(report: HookValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    # ANSI colors
    colors = COLORS

    # Count by level
    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0, "INFO": 0, "PASSED": 0}
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
    nit_c = colors["NIT"]
    warn_c = colors["WARNING"]
    print(f"  {nit_c}NIT:      {counts['NIT']}{rst}")
    print(f"  {warn_c}WARNING:  {counts['WARNING']}{rst}")
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


def print_json(report: HookValidationReport) -> None:
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
            "nit": sum(1 for r in report.results if r.level == "NIT"),
            "warning": sum(1 for r in report.results if r.level == "WARNING"),
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
    parser = argparse.ArgumentParser(
        description="Validate a Claude Code hooks.json file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: uv run python scripts/validate_hook.py hooks/hooks.json --plugin-root .",
    )
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
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block validation")
    args = parser.parse_args()

    hook_path = Path(args.hook_path).resolve()
    plugin_root = Path(args.plugin_root).resolve() if args.plugin_root else None

    # Early-exit errors: write minimal report if --report is specified
    early_error = None
    if not hook_path.exists():
        early_error = f"Error: {hook_path} does not exist"
    elif not hook_path.is_file():
        early_error = f"Error: {hook_path} is not a file (expected hooks.json)"
    elif hook_path.suffix != ".json":
        early_error = f"Error: {hook_path} is not a JSON file (expected hooks.json)"

    if early_error:
        if args.report:
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(f"# Hook Validation\n\nCRITICAL: {early_error}\n", encoding="utf-8")
            print("Hook Validation: FAIL (critical)")
            print("  CRITICAL:1")
            print(f"  Report: {report_path}")
        else:
            print(early_error, file=sys.stderr)
        return 1

    report = validate_hooks(hook_path, plugin_root)

    if args.json:
        print_json(report)
    elif args.report:
        save_report_and_print_summary(
            report,
            Path(args.report),
            f"Hook Validation: {hook_path}",
            print_results,
            args.verbose,
            plugin_path=args.hook_path,
        )
    else:
        print_results(report, args.verbose)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
