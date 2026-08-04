#!/usr/bin/env python3
"""
Claude Plugins Validation - Agent Validator

Validates individual agent markdown files according to Claude Code agent spec.
Based on: https://code.claude.com/docs/en/agents.md

Usage:
    uv run python scripts/validate_agent.py path/to/agent.md
    uv run python scripts/validate_agent.py path/to/agents/  # validate all agents in dir
    uv run python scripts/validate_agent.py path/to/agent.md --verbose
    uv run python scripts/validate_agent.py path/to/agent.md --json

Exit codes:
    0 - All checks passed
    1 - CRITICAL issues found (agent will not work)
    2 - MAJOR issues found (significant problems)
    3 - MINOR issues found (may affect UX)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from cpv_parallel_runner import ScanResult, parallel_scan
from cpv_validation_common import (
    AGENT_DESCRIPTION_TOKEN_LIMIT,
    BUILTIN_AGENT_TYPES,
    COLORS,
    MIN_BODY_CHARS,
    SECRET_PATTERNS,
    USER_PATH_PATTERNS,
    VALID_CONTEXT_VALUES,
    VALID_EFFORT_VALUES,
    VALID_MODELS,
    VALID_PERMISSION_MODES,
    VALID_TOOLS,
    ValidationReport,
    check_token_limit,
    check_utf8_encoding,
    is_accepted_frontmatter_bool,
    is_plugin_shipped_agent,
    is_valid_model,
    save_report_and_print_summary,
    validate_component_name,
    validate_no_duplicate_frontmatter_keys,
    validate_plugin_shipped_restrictions,
)

# Known frontmatter fields per official docs (agent-specific)
# Based on: https://code.claude.com/docs/en/sub-agents.md
KNOWN_FRONTMATTER_FIELDS = {
    # Required fields
    "name",
    "description",
    # Optional fields
    "tools",
    "disallowedTools",
    "model",
    "permissionMode",
    "skills",
    "hooks",
    "color",
    "capabilities",  # [legacy — emits WARNING] not in current sub-agents spec (v2.1.98)
    "effort",
    "maxTurns",
    "mcpServers",
    "memory",
    "background",
    "isolation",
    "initialPrompt",  # v2.1.83 — auto-submit prompt when agent starts
    # Claude Code-specific fields (legacy/extended — all emit WARNING when present)
    "context",  # [legacy — emits WARNING] not in current sub-agents spec (v2.1.98)
    "agent",  # [legacy — emits WARNING] not in current sub-agents spec (v2.1.98)
    "user-invocable",  # [legacy — emits WARNING] not in current sub-agents spec (v2.1.98)
    "system-prompt",  # [legacy — emits WARNING] not in current sub-agents spec (v2.1.98)
}

# GAP-79 (v2.22.3): Plugin-shipped agent allowed frontmatter fields. Per
# plugins-reference.md:70 the set of fields accepted for PLUGIN-shipped
# agents is intentionally narrower than the full project/user agent
# superset. Keys present on a plugin-shipped agent but OUTSIDE this set
# trigger a MINOR so authors notice CPV-legacy / non-plugin drift.
# ``hooks``/``mcpServers``/``permissionMode`` already produce MAJORs via
# ``PLUGIN_SHIPPED_AGENT_FORBIDDEN_FIELDS`` — we do NOT double-count them
# here.
PLUGIN_SHIPPED_AGENT_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "tools",
        "disallowedTools",
        "model",
        "effort",
        "skills",
        "system-prompt",
        "context",
        "memory",
        "isolation",
        "maxTurns",
        "background",
        "initialPrompt",
        "agent",
    }
)

# Valid values for the 'permissionMode' field. Aliased to the canonical
# ``VALID_PERMISSION_MODES`` in ``cpv_validation_common`` so agent frontmatter
# and settings ``permissions.defaultMode`` share the same enumeration
# (permission-modes.md L17-22).
# (Imported at module-level for the agent validator's type hints.)

# Built-in agent types per official docs — custom agent names are also valid.
# Aliased to the shared ``BUILTIN_AGENT_TYPES`` in ``cpv_validation_common`` to
# keep one source of truth (updated in v2.22.0 with ``statusline-setup`` and
# ``Claude Code Guide`` per sub-agents.md L29-74).
VALID_AGENT_VALUES = BUILTIN_AGENT_TYPES

# Valid values for the 'memory' field (persistent memory scope)
VALID_MEMORY_SCOPES = {"user", "project", "local"}

# Valid values for the 'isolation' field
VALID_ISOLATION_VALUES = {"worktree"}

# Minimum required example blocks for agent documentation
MIN_EXAMPLE_BLOCKS = 2

# Placeholder text patterns that indicate incomplete system prompts
PLACEHOLDER_PATTERNS = [
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bPLACEHOLDER\b", re.IGNORECASE),
    re.compile(r"\bFIXME\b", re.IGNORECASE),
    re.compile(r"\bXXX\b"),
    re.compile(r"\[.*INSERT.*\]", re.IGNORECASE),
    re.compile(r"\[.*FILL.*\]", re.IGNORECASE),
]

# PLUGIN_SHIPPED_AGENT_FORBIDDEN_FIELDS, is_plugin_shipped_agent, and
# validate_plugin_shipped_restrictions now live in cpv_validation_common so
# that validate_plugin.py and validate_agent.py call a single implementation
# with identical messages.


@dataclass
class AgentValidationReport(ValidationReport):
    """Validation report for an agent file, extends base ValidationReport with agent_path."""

    agent_path: str = ""

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        base = super().to_dict()
        base["agent_path"] = self.agent_path
        return base


def parse_frontmatter(content: str) -> tuple[dict[str, Any] | None, str, int]:
    """Parse YAML frontmatter from agent content.

    Returns:
        Tuple of (frontmatter_dict, body_content, frontmatter_end_line)
        Returns (None, content, 0) if no frontmatter found
    """
    if not content.startswith("---"):
        return None, content, 0

    # Split on the closing `---` DELIMITER LINE — never a bare `---` substring.
    # `content.split("---", 2)` corrupts valid frontmatter whose VALUE contains
    # `---` (e.g. `description: "use --- as a separator"`), truncating the YAML
    # and producing a false "Malformed frontmatter". The opener and closer must
    # each be `---` alone on their own line (CommonMark/YAML front-matter rule).
    lines = content.split("\n")
    if lines[0].strip() != "---":
        return None, content, 0
    closing_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing_idx = idx
            break
    if closing_idx is None:
        return None, content, 0

    fm_text = "\n".join(lines[1:closing_idx])
    body = "\n".join(lines[closing_idx + 1 :])
    try:
        frontmatter = yaml.safe_load(fm_text)
        if frontmatter is None:
            frontmatter = {}
        # 1-based line number of the closing `---`.
        fm_end_line = closing_idx + 1
        return frontmatter, body, fm_end_line
    except yaml.YAMLError:
        return None, content, 0


def validate_frontmatter_exists(content: str, report: AgentValidationReport, filename: str) -> dict[str, Any] | None:
    """Validate YAML frontmatter exists and is valid."""
    if not content.startswith("---"):
        report.critical("No YAML frontmatter found (required)", filename)
        return None

    frontmatter, *_ = parse_frontmatter(content)

    # parse_frontmatter returns None ONLY on a yaml.YAMLError (an empty-but-present
    # frontmatter block is normalized to {} there). content.startswith("---") is
    # already guaranteed True by the early return above, so this single branch
    # covers every None case — a separate "if frontmatter is None" guard below
    # would be unreachable dead code.
    if frontmatter is None:
        report.critical(
            "Malformed YAML frontmatter (missing closing --- or invalid YAML)",
            filename,
        )
        return None

    # Frontmatter MUST be a mapping (dict). yaml.safe_load can return any
    # valid YAML type (scalar, list, str, etc.); treating those as frontmatter
    # and calling .keys() on them would crash with AttributeError. Reject
    # non-dict frontmatter as malformed.
    if not isinstance(frontmatter, dict):
        report.critical(
            f"Frontmatter must be a YAML mapping, got {type(frontmatter).__name__}",
            filename,
        )
        return None

    report.passed("Valid YAML frontmatter", filename)

    # A duplicated top-level key parses cleanly but SILENTLY discards the
    # earlier value, so it is invisible in `frontmatter` — it has to be read
    # off the raw text (shared with the skill/command validators).
    validate_no_duplicate_frontmatter_keys(content, report, filename)

    # Check for unknown fields
    for key in frontmatter.keys():
        if key not in KNOWN_FRONTMATTER_FIELDS:
            report.warning(
                f"Unknown frontmatter field '{key}' (may be ignored by CLI)",
                filename,
            )

    return frontmatter


def validate_name_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'name' frontmatter field."""
    if "name" not in frontmatter:
        # Use filename as fallback name
        expected_name = Path(filename).stem
        report.info(
            f"No 'name' field (will use filename: {expected_name})",
            filename,
        )
        name = expected_name
    else:
        name = frontmatter["name"]
        report.passed(f"'name' field present: {name}", filename)

    if not isinstance(name, str):
        report.critical(f"'name' must be a string, got {type(name).__name__}", filename)
        return

    # Uniform naming validation via shared function
    validate_component_name(name, "agent", report)


def validate_description_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'description' frontmatter field."""
    if "description" not in frontmatter:
        report.major("Missing 'description' field (required)", filename)
        return

    desc = frontmatter["description"]

    if not isinstance(desc, str):
        report.critical(f"'description' must be a string, got {type(desc).__name__}", filename)
        return

    if not desc.strip():
        report.major("'description' cannot be empty", filename)
        return

    # Length checks
    if len(desc) < 10:
        report.minor(
            f"Description is very short ({len(desc)} chars) - may not help Claude decide when to use",
            filename,
        )

    check_token_limit(
        desc,
        AGENT_DESCRIPTION_TOKEN_LIMIT,
        report,
        filename,
        "Agent 'description'",
        "Tighten it to a focused trigger sentence — agents have no separate when_to_use.",
    )

    # NOTE: no angle-bracket check. Angle brackets in a description are VALID and
    # even recommended — Anthropic's own subagent pattern puts raw
    # <example>...</example> blocks IN the description field, and inline-code refs
    # like `<context>` / placeholders like <path> are common. The old blanket
    # "< or > -> MAJOR" rejected Anthropic-valid descriptions (and contradicted
    # validate_example_blocks, which WANTS <example> blocks). Removed in TRDD-021250b5.

    # Check for actionable description (should indicate WHEN to use)
    action_words = ["use when", "invoke", "call", "trigger", "run", "execute", "specialized in", "expert in"]
    has_action_hint = any(word in desc.lower() for word in action_words)
    if not has_action_hint:
        report.info(
            "Description should indicate WHEN to invoke the agent (e.g., 'Use when...')",
            filename,
        )

    # Check for proactive delegation hint (best practice from sub-agents docs)
    if "proactively" in desc.lower() or "use proactively" in desc.lower():
        report.passed("Description includes proactive delegation hint", filename)
    else:
        report.info(
            "Consider adding 'use proactively' to encourage Claude to delegate automatically",
            filename,
        )

    report.passed("'description' field valid", filename)


def _parse_tool_reference(raw: str) -> tuple[str, list[str] | None, str | None]:
    """Parse a tool reference string into (base_name, spawnable_subagents, error).

    Supports the v2.1.63+ grammar ``Agent(worker, researcher)`` (and the legacy
    alias ``Task(...)``) from sub-agents.md L296-318, where the parenthesized
    list is an allowlist of spawnable subagent types. Non-Agent/Task tools
    with parentheses (e.g. ``Bash(git *)``) pass through unchanged so the
    caller's existing Bash/MCP logic still sees them.

    Returns:
        (base_tool_name, spawnable_subagents, error)
        - base_tool_name: always the leading identifier (e.g. "Agent", "Bash")
        - spawnable_subagents: list of agent names when ``raw`` is
          ``Agent(...)``/``Task(...)``; ``None`` otherwise. An empty list means
          ``Agent()`` with an explicit empty allowlist.
        - error: non-None only when the reference is malformed (e.g.
          unbalanced parens). ``spawnable_subagents`` is ``None`` on error.
    """
    stripped = raw.strip()
    # Bare identifier like "Agent" or "Read" — no parens, no spawnable list.
    if "(" not in stripped:
        return stripped, None, None

    # Find the base name (identifier before the first '(').
    open_idx = stripped.index("(")
    base = stripped[:open_idx].strip()

    # Unbalanced / missing closing paren.
    if not stripped.endswith(")"):
        if base in ("Agent", "Task"):
            return base, None, "unbalanced parens"
        # For non-Agent/Task tools, let downstream validators surface their own
        # error (they've handled ``Bash(...)`` patterns historically).
        return base, None, None

    # Only Agent/Task use the spawnable-subagent list grammar.
    if base not in ("Agent", "Task"):
        return base, None, None

    inner = stripped[open_idx + 1 : -1]
    # Empty parens == explicit empty allowlist (distinct from bare "Agent").
    if not inner.strip():
        return base, [], None

    # Comma-separated, whitespace-tolerant, trailing-comma OK.
    names = [n.strip() for n in inner.split(",") if n.strip()]
    return base, names, None


def validate_tools_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'tools' frontmatter field."""
    if "tools" not in frontmatter:
        report.info("No 'tools' field (agent will inherit default tools)", filename)
        return

    tools = frontmatter["tools"]

    # Can be string (comma-separated) or list
    if isinstance(tools, str):
        tool_list = [t.strip() for t in tools.split(",") if t.strip()]
    elif isinstance(tools, list):
        tool_list = [str(t).strip() for t in tools if str(t).strip()]
    else:
        report.major(
            f"'tools' must be string or list, got {type(tools).__name__}",
            filename,
        )
        return

    if not tool_list:
        # Empty tools ([] / "") is a VALID, explicit "no tools" (chat-only)
        # declaration — distinct from an ABSENT field, which means "inherit all
        # tools". Non-blocking (WARNING) but surfaced because an empty array is
        # usually a mistaken attempt at "allow everything".
        report.warning(
            "'tools' is empty ([]) — this forbids ALL tools, only chatting is "
            "allowed. If this is not intentional, fix it. If it was a mistaken "
            "attempt at allowing all tools, omitting the 'tools' field entirely "
            "is the correct syntax (an absent field means all tools allowed).",
            filename,
        )
        return

    # Validate each tool name
    invalid_tools = []
    for tool in tool_list:
        base_tool, spawnables, error = _parse_tool_reference(tool)
        if error is not None:
            # Malformed Agent()/Task() grammar — surface as MAJOR and skip
            # further per-tool checks for this entry.
            report.major(
                f"malformed tool reference '{tool}': {error}",
                filename,
            )
            continue
        if base_tool not in VALID_TOOLS and not base_tool.startswith("mcp__"):
            invalid_tools.append(tool)
            continue
        # Only Agent()/Task() carry a spawnable-subagent allowlist. Bare
        # "Agent"/"Task" (spawnables is None) is still accepted as-is.
        if spawnables is not None:
            if not spawnables:
                report.info(
                    f"'{tool}' declares an explicit empty allowlist; this agent may spawn no subagents",
                    filename,
                )
            else:
                for name in spawnables:
                    if name not in BUILTIN_AGENT_TYPES:
                        report.minor(
                            f"'{tool}' references unknown spawnable agent '{name}' "
                            "(may be a custom plugin-shipped agent we cannot verify)",
                            filename,
                        )

    if invalid_tools:
        report.info(
            f"Unknown tools (may be custom): {', '.join(invalid_tools)}",
            filename,
        )

    # Deprecation warnings for renamed/soft-deprecated tools
    # (kept in VALID_TOOLS — these are still accepted as aliases).
    for tool in tool_list:
        base_tool, _, error = _parse_tool_reference(tool)
        if error is not None:
            continue
        if base_tool == "TaskOutput":
            report.warning(
                "Tool 'TaskOutput' is deprecated — prefer Read on the task's output file path",
                filename,
            )
        elif base_tool == "Task":
            report.warning(
                "Tool 'Task' was renamed to 'Agent' in v2.1.63; 'Task' still works as an alias",
                filename,
            )
        elif base_tool in ("TodoRead", "Notebook", "MultiEdit"):
            report.warning(
                f"Tool '{base_tool}' is not in the current tools-reference spec. Verify existence before shipping.",
                filename,
            )

    report.passed(f"'tools' field valid: {len(tool_list)} tool(s)", filename)


def validate_model_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'model' frontmatter field."""
    if "model" not in frontmatter:
        report.info("No 'model' field (agent will inherit parent model)", filename)
        return

    model = frontmatter["model"]

    if not isinstance(model, str):
        report.major(f"'model' must be a string, got {type(model).__name__}", filename)
        return

    # v2.1.74+: accept short names (haiku/sonnet/opus/fable/inherit) AND full model IDs (claude-opus-5).
    # VALID_MODELS is interpolated, so this message tracks the family set automatically;
    # only the full-ID example is spelled out and has to be kept current by hand.
    if not is_valid_model(model):
        report.major(
            f"Invalid 'model' value: {model}. Valid: {VALID_MODELS} or full ID like claude-opus-5",
            filename,
        )
        return

    report.passed(f"'model' field valid: {model}", filename)


AGENT_NAMED_COLORS: frozenset[str] = frozenset(
    {
        "red",
        "blue",
        "green",
        "yellow",
        "purple",
        "orange",
        "pink",
        "cyan",
    }
)


def validate_color_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'color' frontmatter field.

    Per sub-agents.md L247 the spec accepts exactly 8 named colors
    (red, blue, green, yellow, purple, orange, pink, cyan). CPV also
    accepts hex ``#RRGGBB`` values as a legacy/CPV-extension shape so
    existing agents keep validating, but emits a NIT when hex is used
    nudging authors toward the canonical named values.
    """
    if "color" not in frontmatter:
        return

    color = frontmatter["color"]

    if not isinstance(color, str):
        report.major(f"'color' must be a string, got {type(color).__name__}", filename)
        return

    if color in AGENT_NAMED_COLORS:
        report.passed(f"'color' field valid (named): {color}", filename)
        return

    hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
    if hex_pattern.match(color):
        report.nit(
            f"'color' uses legacy hex format ({color}); prefer a named color "
            f"from {sorted(AGENT_NAMED_COLORS)} per sub-agents.md L247",
            filename,
        )
        return

    report.major(
        f"'color' must be one of {sorted(AGENT_NAMED_COLORS)} or hex (#RRGGBB), got {color!r}",
        filename,
    )


def validate_capabilities_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'capabilities' frontmatter field."""
    if "capabilities" not in frontmatter:
        return

    # Legacy/extended field — not in the current sub-agents spec.
    report.warning(
        "Field 'capabilities' is not in the current sub-agents spec (v2.1.98). "
        "It may be legacy/extended. Verify it still works with your installed Claude Code version.",
        filename,
    )

    caps = frontmatter["capabilities"]

    if not isinstance(caps, list):
        report.major(
            f"'capabilities' must be an array, got {type(caps).__name__}",
            filename,
        )
        return

    for i, cap in enumerate(caps):
        if not isinstance(cap, str):
            report.major(
                f"'capabilities[{i}]' must be a string, got {type(cap).__name__}",
                filename,
            )

    report.passed(f"'capabilities' field valid: {len(caps)} capability(ies)", filename)


def validate_context_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'context' frontmatter field.

    Valid values: 'fork' (or empty/missing).
    The 'fork' context indicates this agent runs in a separate process.
    """
    if "context" not in frontmatter:
        # context is optional - missing is fine
        return

    # Legacy/extended field — not in the current sub-agents spec.
    report.warning(
        "Field 'context' is not in the current sub-agents spec (v2.1.98). "
        "It may be legacy/extended. Verify it still works with your installed Claude Code version.",
        filename,
    )

    context = frontmatter["context"]

    if not isinstance(context, str):
        report.major(f"'context' must be a string, got {type(context).__name__}", filename)
        return

    if context not in VALID_CONTEXT_VALUES:
        report.major(
            f"Invalid 'context' value: '{context}'. Valid values: {sorted(VALID_CONTEXT_VALUES)}",
            filename,
        )
        return

    report.passed(f"'context' field valid: {context}", filename)


def validate_agent_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'agent' frontmatter field.

    This field specifies specialized agent types.
    Standard values: api-coordinator, test-engineer, deploy-agent, debug-specialist, code-reviewer.
    Non-standard values are allowed but reported as INFO.
    """
    if "agent" not in frontmatter:
        # agent field is optional
        return

    # Legacy/extended field — not in the current sub-agents spec.
    report.warning(
        "Field 'agent' is not in the current sub-agents spec (v2.1.98). "
        "It may be legacy/extended. Verify it still works with your installed Claude Code version.",
        filename,
    )

    agent = frontmatter["agent"]

    if not isinstance(agent, str):
        report.major(f"'agent' must be a string, got {type(agent).__name__}", filename)
        return

    if agent not in VALID_AGENT_VALUES:
        report.info(
            f"Non-standard 'agent' value: '{agent}'. Standard values: {sorted(VALID_AGENT_VALUES)}",
            filename,
        )
    else:
        report.passed(f"'agent' field valid: {agent}", filename)


def validate_user_invocable_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'user-invocable' frontmatter field.

    Must be a boolean (true or false), not a string.
    """
    if "user-invocable" not in frontmatter:
        # user-invocable is optional
        return

    # Legacy/extended field — not in the current sub-agents spec.
    report.warning(
        "Field 'user-invocable' is not in the current sub-agents spec (v2.1.98). "
        "It may be legacy/extended. Verify it still works with your installed Claude Code version.",
        filename,
    )

    value = frontmatter["user-invocable"]

    if is_accepted_frontmatter_bool(value):
        report.passed(f"'user-invocable' field valid: {value}", filename)
    else:
        report.major(
            f"'user-invocable' must be boolean (true/false/yes/no/on/off/1/0), got: {type(value).__name__} = {value!r}",
            filename,
        )


def validate_system_prompt_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'system-prompt' frontmatter field.

    Checks for placeholder text like TODO, PLACEHOLDER, FIXME, etc.
    """
    if "system-prompt" not in frontmatter:
        # system-prompt is optional
        return

    # Legacy/extended field — not in the current sub-agents spec.
    report.warning(
        "Field 'system-prompt' is not in the current sub-agents spec (v2.1.98). "
        "It may be legacy/extended. Verify it still works with your installed Claude Code version.",
        filename,
    )

    prompt = frontmatter["system-prompt"]

    if not isinstance(prompt, str):
        report.major(f"'system-prompt' must be a string, got {type(prompt).__name__}", filename)
        return

    if not prompt.strip():
        report.major("'system-prompt' cannot be empty", filename)
        return

    # Check for placeholder text
    for pattern in PLACEHOLDER_PATTERNS:
        match = pattern.search(prompt)
        if match:
            report.major(
                f"'system-prompt' contains placeholder text: '{match.group()}'",
                filename,
            )
            return

    report.passed("'system-prompt' field valid", filename)


def validate_skills_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'skills' frontmatter field.

    The skills field specifies which skills the agent has access to.
    Must be a list of skill names (strings).
    """
    if "skills" not in frontmatter:
        # skills is optional
        return

    skills = frontmatter["skills"]

    if not isinstance(skills, list):
        report.major(f"'skills' must be a list, got {type(skills).__name__}", filename)
        return

    if not skills:
        report.minor("'skills' list is empty - consider removing if no skills needed", filename)
        return

    invalid_items = []
    valid_skills = []
    for i, skill in enumerate(skills):
        if not isinstance(skill, str):
            invalid_items.append(f"index {i}: {type(skill).__name__}")
        elif not skill.strip():
            invalid_items.append(f"index {i}: empty string")
        else:
            valid_skills.append(skill)

    if invalid_items:
        report.major(f"'skills' contains invalid items: {', '.join(invalid_items)}", filename)
        return

    report.passed(f"'skills' field valid: {valid_skills}", filename)


def validate_permission_mode_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'permissionMode' frontmatter field.

    Valid values per sub-agents docs:
    - default: Standard permission checking with prompts
    - acceptEdits: Auto-accept file edits
    - dontAsk: Auto-deny permission prompts (explicitly allowed tools still work)
    - bypassPermissions: Skip all permission checks (use with caution!)
    - plan: Plan mode (read-only exploration)
    """
    if "permissionMode" not in frontmatter:
        # permissionMode is optional - defaults to 'default'
        return

    mode = frontmatter["permissionMode"]

    if not isinstance(mode, str):
        report.major(f"'permissionMode' must be a string, got {type(mode).__name__}", filename)
        return

    if mode not in VALID_PERMISSION_MODES:
        report.major(
            f"Invalid 'permissionMode' value: '{mode}'. Valid values: {sorted(VALID_PERMISSION_MODES)}",
            filename,
        )
        return

    # Warn about dangerous permission modes
    if mode == "bypassPermissions":
        report.minor(
            "'permissionMode: bypassPermissions' skips ALL permission checks - use with caution!",
            filename,
        )

    report.passed(f"'permissionMode' field valid: {mode}", filename)


def validate_memory_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'memory' frontmatter field."""
    if "memory" not in frontmatter:
        return

    rel_path = filename
    memory_val = frontmatter["memory"]
    if not isinstance(memory_val, str):
        report.major(f"'memory' must be a string, got {type(memory_val).__name__}", rel_path)
    elif memory_val not in VALID_MEMORY_SCOPES:
        report.major(
            f"Invalid 'memory' value: '{memory_val}'. Must be one of: {sorted(VALID_MEMORY_SCOPES)}",
            rel_path,
        )
    else:
        report.passed(f"Valid memory scope: {memory_val}", rel_path)


def validate_isolation_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'isolation' frontmatter field.

    Per plugins-reference.md:70, ``"worktree"`` is the only documented value.
    Anything else (including an empty string) is rejected as MAJOR.
    """
    if "isolation" not in frontmatter:
        return

    rel_path = filename
    isolation_val = frontmatter["isolation"]
    if not isinstance(isolation_val, str):
        report.major(f"'isolation' must be a string, got {type(isolation_val).__name__}", rel_path)
    elif not isolation_val.strip():
        report.major(
            "'isolation' field cannot be empty. 'worktree' is the only valid value per plugins-reference.md:70.",
            rel_path,
        )
    elif isolation_val not in VALID_ISOLATION_VALUES:
        report.major(
            f"Invalid 'isolation' value: '{isolation_val}'. "
            "'worktree' is the only valid value per plugins-reference.md:70.",
            rel_path,
        )
    else:
        report.passed(f"Valid isolation mode: {isolation_val}", rel_path)


def validate_max_turns_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'maxTurns' frontmatter field."""
    if "maxTurns" not in frontmatter:
        return

    rel_path = filename
    max_turns = frontmatter["maxTurns"]
    if not isinstance(max_turns, int) or max_turns < 1:
        report.major(f"'maxTurns' must be a positive integer, got {max_turns!r}", rel_path)
    else:
        report.passed(f"Valid maxTurns: {max_turns}", rel_path)


def validate_background_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'background' frontmatter field."""
    if "background" not in frontmatter:
        return

    rel_path = filename
    bg_val = frontmatter["background"]
    if not is_accepted_frontmatter_bool(bg_val):
        report.major(
            f"'background' must be a boolean (true/false/yes/no/on/off/1/0), got {type(bg_val).__name__}", rel_path
        )
    else:
        report.passed(f"Valid background: {bg_val}", rel_path)


def validate_effort_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'effort' frontmatter field (v2.1.78+).

    Accepted values per sub-agents.md L244 + cli-reference.md --effort:
      low | medium | high | xhigh | max

    - ``xhigh`` was added in v2.1.111 for Opus 4.7 only — any non-Opus model
      with ``xhigh`` fails at runtime, so we mirror the same strict check
      applied to ``max``.
    - ``max`` remains supported (Opus 4.6 legacy) for backward compatibility.
    """
    if "effort" not in frontmatter:
        return

    rel_path = filename
    effort_val = frontmatter["effort"]
    if not isinstance(effort_val, str):
        report.major(f"'effort' must be a string, got {type(effort_val).__name__}", rel_path)
        return
    if not effort_val.strip():
        report.major("'effort' field cannot be empty", rel_path)
        return

    if effort_val.lower() not in VALID_EFFORT_VALUES:
        report.major(
            f"Invalid 'effort' value: '{effort_val}'. Must be one of: {sorted(VALID_EFFORT_VALUES)}",
            rel_path,
        )
        return

    report.passed(f"Valid effort: {effort_val}", rel_path)

    # "max" and "xhigh" effort require an Opus model.
    # "xhigh" is Opus 4.7 only; "max" is Opus 4.6 legacy. Both fail on non-Opus.
    if effort_val.lower() in {"max", "xhigh"}:
        model = frontmatter.get("model", "")
        model_str = str(model).lower() if model else ""
        # ``inherit`` resolves to the parent/session model AT RUNTIME, exactly
        # like an ABSENT model field — it MAY be Opus, so we cannot prove it
        # wrong statically. Treat it as the same uncertain (WARNING) case, not
        # a hard MAJOR: emitting MAJOR here calls a valid `model: inherit`
        # agent invalid (TRDD-021250b5 — CPV never calls a valid agent invalid).
        model_is_opus_uncertain = not model_str or model_str == "inherit"
        if not model_is_opus_uncertain and "opus" not in model_str:
            report.major(
                f"effort: {effort_val} requires an Opus model, but model is '{model}'. "
                "Use effort: high for non-Opus models, or set model: opus.",
                rel_path,
            )
        elif model_is_opus_uncertain:
            field_note = "No 'model' field set" if not model_str else "model: inherit resolves at runtime"
            report.warning(
                f"effort: {effort_val} only works with Opus models. {field_note} — "
                "this agent will fail if the session uses a non-Opus model. "
                "Consider adding 'model: opus' or using effort: high.",
                rel_path,
            )


def validate_disallowed_tools_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'disallowedTools' frontmatter field.

    Tools to deny, removed from inherited or specified tools list.
    Must be a comma-separated string or list of tool names.
    """
    if "disallowedTools" not in frontmatter:
        # disallowedTools is optional
        return

    tools = frontmatter["disallowedTools"]

    # Can be string (comma-separated) or list
    if isinstance(tools, str):
        tool_list = [t.strip() for t in tools.split(",") if t.strip()]
    elif isinstance(tools, list):
        tool_list = [str(t).strip() for t in tools if str(t).strip()]
    else:
        report.major(
            f"'disallowedTools' must be string or list, got {type(tools).__name__}",
            filename,
        )
        return

    if not tool_list:
        report.minor("'disallowedTools' field is empty - consider removing", filename)
        return

    # Validate each tool name. Reuse the shared Agent()/Task() grammar parser
    # so "Agent(worker, researcher)" entries don't get flagged as unknown.
    invalid_tools = []
    for tool in tool_list:
        base_tool, _, error = _parse_tool_reference(tool)
        if error is not None:
            report.major(
                f"malformed tool reference '{tool}' in disallowedTools: {error}",
                filename,
            )
            continue
        if base_tool not in VALID_TOOLS and not base_tool.startswith("mcp__"):
            invalid_tools.append(tool)

    if invalid_tools:
        report.info(
            f"Unknown tools in disallowedTools (may be custom): {', '.join(invalid_tools)}",
            filename,
        )

    report.passed(f"'disallowedTools' field valid: {len(tool_list)} tool(s)", filename)


def _normalize_tool_token(rule: str) -> str:
    """Normalise one declared tool rule for equality comparison.

    A BARE identifier is alias-resolved (``Task`` → ``Agent``) so the two names
    for one tool compare equal. A rule carrying a ``(specifier)`` is left
    verbatim: ``Bash(git:*)`` and ``Bash(rm:*)`` are DIFFERENT rules, and
    treating them as the same tool would flag a legitimate allow-scope /
    deny-scope refinement as a contradiction.
    """
    from cpv_tool_permission_match import resolve_alias  # noqa: PLC0415

    token = rule.strip()
    return token if "(" in token else resolve_alias(token)


def validate_tool_grant_contradictions(
    frontmatter: dict[str, Any], filename: str, report: AgentValidationReport
) -> None:
    """Detect self-contradictory / redundant entries across the two grant lists.

    Per sub-agents.md: "If both are set, ``disallowedTools`` is applied first,
    then ``tools`` is resolved against the remaining pool. **A tool listed in
    both is removed.**" So an identical entry in both lists silently voids the
    author's grant — MAJOR, and named so the author knows which one to drop.

    A duplicate entry WITHIN one list is harmless (the resolved set is
    identical), so it is only a WARNING.

    Comparison is on the EXACT normalised token, never the base tool name:
    ``tools: Bash(git:*)`` + ``disallowedTools: Bash(rm:*)`` is a deliberate
    scope refinement, not a contradiction, and must never be flagged.
    """
    from cpv_tool_permission_match import parse_declared_tools  # noqa: PLC0415

    normalised: dict[str, list[str]] = {}
    for field in ("tools", "disallowedTools"):
        rules = parse_declared_tools(frontmatter.get(field))
        if not rules:
            continue
        tokens = [_normalize_tool_token(rule) for rule in rules]
        normalised[field] = tokens

        duplicates = sorted({token for token in tokens if tokens.count(token) > 1})
        if duplicates:
            report.warning(
                f"'{field}' lists the same entry more than once: {', '.join(duplicates)}. "
                "Duplicates are harmless (the resolved tool set is unchanged) but usually "
                "signal an editing mistake — remove the extra entries.",
                filename,
            )

    overlap = sorted(set(normalised.get("tools", [])) & set(normalised.get("disallowedTools", [])))
    if overlap:
        report.major(
            f"{', '.join(overlap)} appears in BOTH 'tools' and 'disallowedTools'. "
            "'disallowedTools' is applied first and a tool listed in both is REMOVED, so the "
            "'tools' grant is silently voided. Drop the entry from whichever list is wrong.",
            filename,
        )


def validate_mcp_grant_hygiene(
    frontmatter: dict[str, Any], body: str, filename: str, report: AgentValidationReport
) -> None:
    """Flag an MCP grant whose wildcard cannot match any tool of its own server.

    Real MCP tool ids are ``mcp__<server>__<tool>`` (DOUBLE underscore), and the
    only documented server-level patterns are ``mcp__<server>`` and
    ``mcp__<server>__*``. A single-separator wildcard (``mcp__chrome-devtools-*``)
    therefore grants NOTHING — the author believes a whole server is allowed
    while every one of its tools stays denied.

    Severity is evidence-led: MAJOR only when the body actually references tools
    of that server which the declared rules fail to cover (the grant is then the
    proven root cause of the accompanying body-vs-tools finding); WARNING when
    uncorroborated, since an unusual-but-unused pattern is not worth failing a
    plugin over.
    """
    from cpv_tool_permission_match import (  # noqa: PLC0415
        ineffective_mcp_grants,
        parse_declared_tools,
        uncovered_mcp_usages_for_server,
    )

    rules = parse_declared_tools(frontmatter.get("tools"))
    if not rules:
        return

    for pattern, server in ineffective_mcp_grants(rules):
        detail = (
            f"MCP grant '{pattern}' in 'tools' matches no tool. Real MCP tool ids are "
            f"'mcp__{server}__<tool>' (double underscore), so this single-separator wildcard "
            f"grants nothing — every tool of the '{server}' server stays denied. Use "
            f"'mcp__{server}__*' for the whole server, or list the exact "
            f"'mcp__{server}__<tool>' ids the body needs."
        )
        uncovered = uncovered_mcp_usages_for_server(body, rules, server)
        if uncovered:
            names = ", ".join(sorted({usage.name for usage in uncovered}))
            lines = ", ".join(str(line) for line in sorted({usage.line for usage in uncovered}))
            report.major(
                f"{detail} The body already uses {names} (body line(s) {lines}): this grant is "
                "the root cause of the accompanying body-vs-'tools' finding, and those calls "
                "fail silently at runtime.",
                filename,
                uncovered[0].line,
            )
        else:
            report.warning(
                f"{detail} (No body usage of that server was found, so this may be an unused "
                "or deliberately broad pattern — only 'mcp__<server>' and 'mcp__<server>__*' "
                "are documented.)",
                filename,
            )


def validate_shell_fence_tool_grant(
    frontmatter: dict[str, Any], body: str, filename: str, report: AgentValidationReport
) -> None:
    """Advise when a body carries a shell fence but 'tools' does not grant Bash.

    WARNING, never higher: an illustrative snippet, a user-facing runbook, and
    "hand this to your Bash-capable subagent" are all legitimate, and there is
    no reliable way to tell "the agent runs this" from "the agent documents
    this". Skipped entirely when 'tools' is absent (the agent inherits Bash).
    """
    from cpv_tool_permission_match import shell_fences_without_bash  # noqa: PLC0415

    fence_lines = shell_fences_without_bash(frontmatter.get("tools"), body)
    if not fence_lines:
        return

    locations = ", ".join(str(line) for line in fence_lines)
    plural = "s" if len(fence_lines) > 1 else ""
    report.warning(
        f"Body contains {len(fence_lines)} shell code fence{plural} (body line{plural} {locations}) "
        "but 'tools' does not grant 'Bash', so this agent cannot execute them. If the commands are "
        "illustrative or meant for the user / another agent, ignore this; if the agent is supposed "
        "to run them, add 'Bash' to 'tools'.",
        filename,
        fence_lines[0],
    )


def validate_agent_skill_closure(
    agent_path: Path,
    frontmatter: dict[str, Any],
    body: str,
    filename: str,
    report: AgentValidationReport,
    *,
    skills_roots: Sequence[Path] | None = None,
    closure: bool = False,
    closure_ambient: bool = False,
) -> None:
    """Resolve every skill this agent names and report the broken references.

    Findings (spec §3 of ``design/specs/agent-closure-and-variants.md``):

    * **AC1** a ``skills:`` preload name that resolves in no root.
    * **AC2** a body ``Skill()`` invocation naming a skill that resolves nowhere.
    * **AC3** the body invokes ``Skill()``, ``tools:`` denies ``Skill``, and the
      named skill RESOLVES — resolution is what proves a real invocation rather
      than prose, so it is the only case that escalates on the tool gate.
    * **AC4** a resolved preload the body never MENTIONS while the gate is open
      (a preload injects the skill's FULL content into every invocation). A bare
      name mention counts as usage — an ALL-IN-ONE agent routes to its preloaded
      skills from a prose table, and demanding a ``Skill()`` call would turn a
      token-economy advisory into an architecture preference.
    * **AC5** a preload that CANNOT be preloaded: the skill sets
      ``disable-model-invocation: true``, or it is a bundled user-only skill
      (``verify`` / ``code-review``). MAJOR with NO guard — unlike AC1/AC2 the
      proof is positive (we READ the skill's own frontmatter), so there is no
      "maybe the roots are wrong" case to protect against.

    THE NON-VACUITY GUARD, and why every escalation depends on it: if zero of
    this agent's named skills resolved, the ROOTS are probably wrong (a
    single-file scan, a moved plugin, an uninstalled source) and "this skill does
    not exist" would be a fabricated finding. So a MAJOR requires that at least
    one OTHER named skill of the same agent DID resolve; absent that proof the
    finding degrades to WARNING — visible, never blocking.

    WARNING is the ONLY non-blocking tier under ``--strict``, so every advisory
    here is WARNING and never MINOR/NIT: CPV must not call a valid agent invalid.

    A FOREIGN namespace (``other-plugin:their-skill``) that does not resolve
    locally produces NO finding — it may legitimately live in another installed
    plugin. A reference namespaced to THIS plugin is local and must resolve, so
    the own-namespace form is not an escape hatch.
    """
    from cpv_agent_closure import (  # noqa: PLC0415
        NEVER_PRELOADABLE_SKILLS,
        body_mentions_skill_name,
        find_plugin_root,
        plugin_namespace,
        resolve_agent_closure,
        skill_blocks_preloading,
        skill_disables_model_invocation,
    )

    result = resolve_agent_closure(agent_path, roots=skills_roots)
    local_ns = plugin_namespace(find_plugin_root(agent_path))

    def is_local(ref: Any) -> bool:
        return ref.namespace is None or (local_ns is not None and ref.namespace == local_ns)

    named = [ref for ref in result.refs if ref.origin in ("preload", "runtime")]
    resolved_names = {ref.name for ref in named if ref.resolved_path is not None}

    def emit(ref: Any, message: str) -> None:
        """MAJOR when another named skill proved the roots are right, else WARNING."""
        if any(name != ref.name for name in resolved_names):
            report.major(message, filename, ref.line or None)
        else:
            report.warning(
                f"{message} (Only a WARNING because NONE of this agent's other named skills "
                "resolved either, so the skill search roots are probably wrong rather than the "
                "agent — pass --skills-root to point at the right skills/ directory.)",
                filename,
                ref.line or None,
            )

    roots_hint = ", ".join(result.skill_roots) if result.skill_roots else "(none found)"

    # AC5 first: a preload that CANNOT be preloaded gets exactly one finding, and
    # it is this one. Reporting a bundled user-only skill as "does not exist in
    # your skills/" (AC1) would send the author looking for a missing file, and
    # AC4's "you never mention it" is moot once the remedy is "remove it".
    unpreloadable: dict[str, str] = {}
    for ref in result.refs:
        if ref.origin != "preload":
            continue
        if not is_local(ref):
            # A FOREIGN-namespaced preload names another plugin's skill. Even when
            # a local skill of that bare name happens to exist, we cannot prove it
            # is the one referenced — so neither its frontmatter flag nor the
            # bundled-name inference is evidence about THIS reference.
            continue
        resolved = Path(ref.resolved_path) if ref.resolved_path else None
        reason = skill_blocks_preloading(ref.name, resolved)
        if reason is None:
            continue
        unpreloadable[ref.name] = reason
        # Only the NAME-based inference degrades to a WARNING. When the skill's own
        # frontmatter carries the flag we have positive proof, so it stays MAJOR
        # even for a locally-shipped skill whose name collides with a bundled one.
        if (
            not skill_disables_model_invocation(resolved)
            and ref.name in NEVER_PRELOADABLE_SKILLS
            and ref.resolved_path is not None
        ):
            # A locally-shipped skill whose NAME collides with a bundled user-only
            # one. Which of the two a preload picks is not documented, so calling
            # the agent invalid here would risk failing a valid plugin — WARNING.
            report.warning(
                f"'skills' preloads {ref.name!r}, and {reason}. A local skill of the same name does "
                f"exist ({ref.resolved_path}), so which one this preload picks is undefined — rename "
                f"the local skill to remove the collision.",
                filename,
            )
            continue
        report.major(
            f"'skills' preloads {ref.name!r}, which cannot be preloaded: {reason}. The preload is "
            f"silently dropped, so the agent never gets that content — remove it from 'skills' and "
            f"invoke the skill at runtime instead (or drop the flag on the skill if the preload is "
            f"the intent).",
            filename,
        )

    for ref in named:
        if ref.resolved_path is not None or not is_local(ref):
            continue
        if ref.origin == "preload":
            if ref.name in unpreloadable:
                continue
            # AC1
            emit(
                ref,
                f"'skills' preloads {ref.name!r} but no such skill exists in any skill search "
                f"root ({roots_hint}). Claude Code SKIPS a missing or disabled preload and only "
                f"logs a warning to the debug log, so the agent silently runs without it — fix the "
                f"name or ship the skill.",
            )
        else:
            # AC2
            emit(
                ref,
                f"Body invokes Skill() on {ref.name!r} but no such skill exists in any skill "
                f"search root ({roots_hint}). That invocation fails silently at runtime — fix "
                f"the name, ship the skill, or namespace it to the plugin that owns it.",
            )

    if not result.can_load_at_runtime:
        # Name the ACTUAL cause: per sub-agents.md the gate is shut either by
        # omitting Skill from 'tools' OR by listing it in 'disallowedTools', and
        # the two have different remedies.
        from cpv_tool_permission_match import declared_tool_names, parse_declared_tools  # noqa: PLC0415

        denied = parse_declared_tools(frontmatter.get("disallowedTools")) or []
        if "Skill" in declared_tool_names(denied):
            cause = "'disallowedTools' lists 'Skill', and 'disallowedTools' is applied FIRST"
            remedy = "Remove 'Skill' from 'disallowedTools'"
        else:
            cause = "'tools' is declared without it"
            remedy = "Add 'Skill' to 'tools' (or drop the 'tools' field to inherit every tool)"
        for ref in result.refs:
            if ref.origin != "runtime" or ref.resolved_path is None:
                continue
            # AC3 — MAJOR unconditionally: the named skill RESOLVES, which proves
            # this is a real invocation and not prose, so the non-vacuity guard
            # has nothing left to protect against.
            report.major(
                f"Body invokes Skill() on {ref.name!r} (which exists at {ref.resolved_path}) but "
                f"this agent cannot use the 'Skill' tool: {cause}, so the invocation is DEAD. "
                f"{remedy}; a 'skills:' preload is otherwise this agent's only skill access.",
                filename,
                ref.line or None,
            )

    if result.can_load_at_runtime:
        for ref in result.refs:
            if ref.origin != "preload" or ref.resolved_path is None:
                continue
            if ref.name in unpreloadable:
                # AC5 already told the author to remove it; "you never mention
                # it" on top of that is noise pointing the wrong way.
                continue
            # A bare NAME MENTION anywhere outside a fence counts as usage: an
            # ALL-IN-ONE agent preloads every skill and routes to them from a
            # prose table, so requiring a Skill() call would flag the canonical
            # pattern. A mention only inside a fence is an illustration, not
            # routing, so it does NOT count.
            if body_mentions_skill_name(body, ref.name):
                continue
            # AC4
            report.warning(
                f"Skill {ref.name!r} is preloaded but the body never mentions it. A preload injects "
                f"the skill's FULL content into EVERY invocation of this agent, so an unused one is "
                f"paid for every turn; this agent can use the 'Skill' tool, so it could load the "
                f"skill on demand instead. Either route to it from the body (a prose/table mention "
                f"is enough) or drop it from 'skills'.",
                filename,
            )

    report.info(
        f"Skill closure: {len(named)} named + {len(result.refs) - len(named)} transitive reference(s), "
        f"{len(result.ambient)} skill(s) ambient in {len(result.skill_roots)} root(s) "
        f"[{roots_hint}]; runtime Skill() gate {'OPEN' if result.can_load_at_runtime else 'SHUT'}; "
        f"max depth reached {result.max_depth_reached}",
        filename,
    )

    if closure or closure_ambient:
        _roll_in_closure_skill_reports(result, report, filename, ambient=closure_ambient)


def _roll_in_closure_skill_reports(
    result: Any,
    report: AgentValidationReport,
    filename: str,
    *,
    ambient: bool,
) -> None:
    """Validate each reachable skill (and, with ``ambient``, the whole palette)
    and merge its findings into the AGENT's report.

    Opt-in only (``--closure`` / ``--closure-ambient``): validating an entire
    skill palette on every agent scan would be pure noise, and it would make one
    skill's defect fail every agent that can reach it.

    PASSED results are collapsed into ONE line per clean skill — merging them
    verbatim would bury the agent's own findings under hundreds of lines.
    """
    from validate_skill_comprehensive import validate_skill  # noqa: PLC0415

    targets: dict[str, Path] = {}
    for ref in result.refs:
        if ref.reachable and ref.resolved_path is not None:
            skill_dir = Path(ref.resolved_path).parent
            targets.setdefault(skill_dir.name, skill_dir)

    if ambient:
        for root in result.skill_roots:
            root_path = Path(root)
            try:
                entries = sorted(root_path.iterdir())
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir() and (entry / "SKILL.md").is_file():
                        targets.setdefault(entry.name, entry)
                except OSError:
                    continue

    for name in sorted(targets):
        skill_report = validate_skill(targets[name])
        findings = [r for r in skill_report.results if r.level not in ("PASSED", "INFO")]
        if not findings:
            report.passed(f"[closure {name}] skill validation clean", filename)
            continue
        for r in findings:
            getattr(report, r.level.lower())(f"[closure {name}] {r.message}", r.file or filename, r.line)


def validate_initial_prompt_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'initialPrompt' frontmatter field.

    Auto-submitted as the first user turn when this agent runs as the main
    session agent (via --agent or the agent setting). Skills and commands
    are processed. Prepended to any user-provided prompt.
    """
    if "initialPrompt" not in frontmatter:
        return

    val = frontmatter["initialPrompt"]
    if not isinstance(val, str):
        report.major(f"'initialPrompt' must be a string, got {type(val).__name__}", filename)
        return
    if not val.strip():
        report.minor("'initialPrompt' is empty — if present, should contain a prompt", filename)
        return
    report.passed(f"'initialPrompt' field valid ({len(val)} chars)", filename)


def validate_mcp_servers_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'mcpServers' frontmatter field.

    Each entry is either:
    - A string referencing an already-configured server by name
    - An inline definition: {server_name: {type: "stdio", command: "...", args: [...]}}
    """
    if "mcpServers" not in frontmatter:
        return

    val = frontmatter["mcpServers"]
    if not isinstance(val, list):
        report.major(f"'mcpServers' must be a list, got {type(val).__name__}", filename)
        return

    for i, entry in enumerate(val):
        if isinstance(entry, str):
            if not entry.strip():
                report.minor(f"'mcpServers[{i}]' is an empty string — must be a server name", filename)
        elif isinstance(entry, dict):
            # Inline definition: {name: {type, command, ...}}
            for name, config in entry.items():
                if not isinstance(config, dict):
                    report.major(f"'mcpServers[{i}].{name}' must be an object, got {type(config).__name__}", filename)
                elif "command" not in config and "url" not in config:
                    report.minor(f"'mcpServers[{i}].{name}' has no 'command' or 'url' field", filename)
        else:
            report.major(f"'mcpServers[{i}]' must be a string or object, got {type(entry).__name__}", filename)

    report.passed(f"'mcpServers' field valid: {len(val)} server(s)", filename)


def validate_hooks_field(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate the 'hooks' frontmatter field.

    Hooks scoped to this subagent. Per hooks.md L422 (v2.1.109): "All hook
    events are supported" in agent frontmatter. CPV therefore accepts every
    event name from VALID_HOOK_EVENTS (all 26 + legacy Setup).
    """
    from cpv_validation_common import VALID_HOOK_EVENTS

    if "hooks" not in frontmatter:
        # hooks is optional
        return

    hooks = frontmatter["hooks"]

    if not isinstance(hooks, dict):
        report.major(f"'hooks' must be an object, got {type(hooks).__name__}", filename)
        return

    for event_name, event_config in hooks.items():
        if event_name not in VALID_HOOK_EVENTS:
            report.major(
                f"Invalid hook event for agent: '{event_name}'. Valid events: {sorted(VALID_HOOK_EVENTS)}",
                filename,
            )
            continue

        if not isinstance(event_config, list):
            report.major(
                f"Hook event '{event_name}' must be an array of matcher blocks",
                filename,
            )
            continue

        for i, matcher_block in enumerate(event_config):
            if not isinstance(matcher_block, dict):
                report.major(
                    f"Hook '{event_name}[{i}]' must be an object",
                    filename,
                )
                continue

            # Check for required 'hooks' array in matcher block
            if "hooks" not in matcher_block:
                report.major(
                    f"Hook '{event_name}[{i}]' missing required 'hooks' array",
                    filename,
                )
                continue

            inner_hooks = matcher_block["hooks"]
            if not isinstance(inner_hooks, list):
                report.major(
                    f"Hook '{event_name}[{i}].hooks' must be an array",
                    filename,
                )
                continue

            # Validate each hook in the array
            for j, hook in enumerate(inner_hooks):
                if not isinstance(hook, dict):
                    report.major(
                        f"Hook '{event_name}[{i}].hooks[{j}]' must be an object",
                        filename,
                    )
                    continue

                # Check for required 'type' field
                if "type" not in hook:
                    report.major(
                        f"Hook '{event_name}[{i}].hooks[{j}]' missing required 'type' field",
                        filename,
                    )
                    continue

                hook_type = hook["type"]
                # The 5 valid hook types as of v2.1.118 are
                # {command, http, mcp_tool, prompt, agent} — kept in lockstep
                # with validate_hook.VALID_HOOK_TYPES (the authority). Agent-scoped
                # hooks accept the same set; omitting mcp_tool was a false positive
                # that rejected a valid hook config.
                if hook_type not in {"command", "http", "mcp_tool", "prompt", "agent"}:
                    report.major(
                        f"Invalid hook type '{hook_type}' in '{event_name}[{i}].hooks[{j}]'. "
                        "Valid types: command, http, mcp_tool, prompt, agent",
                        filename,
                    )
                    continue

                # Cross-platform + persistent-data checks for command hooks
                # defined in agent frontmatter. Reuses the same engine that
                # validates hooks/hooks.json so the rules stay in lockstep.
                if hook_type == "command":
                    cmd = hook.get("command")
                    if isinstance(cmd, str) and cmd.strip():
                        try:
                            from validate_hook import check_hook_command_cross_platform

                            check_hook_command_cross_platform(cmd, report, file_label=filename)
                        except ImportError:
                            pass  # validate_hook unavailable in this scope

    report.passed("'hooks' field structure valid", filename)


def validate_plugin_shipped_allowed_fields(
    frontmatter: dict[str, Any],
    filename: str,
    report: AgentValidationReport,
    is_plugin_shipped: bool,
) -> None:
    """GAP-79 (v2.22.3): Enforce the narrower plugin-shipped agent field list.

    Per plugins-reference.md:70, plugin-shipped agents accept exactly these 15
    fields: ``name, description, tools, disallowedTools, model, effort, skills,
    system-prompt, context, memory, isolation, maxTurns, background,
    initialPrompt, agent``. Fields OUTSIDE this set (but inside the broader
    KNOWN_FRONTMATTER_FIELDS superset accepted for project/user agents) emit a
    MINOR so authors notice the drift.

    ``hooks``/``mcpServers``/``permissionMode`` are NOT double-reported here:
    those already trigger MAJORs via PLUGIN_SHIPPED_AGENT_FORBIDDEN_FIELDS
    (security restriction, stricter level). Truly-unknown keys are handled
    by ``validate_frontmatter_exists`` upstream (WARNING).
    """
    if not is_plugin_shipped:
        return

    from cpv_validation_common import PLUGIN_SHIPPED_AGENT_FORBIDDEN_FIELDS

    forbidden = set(PLUGIN_SHIPPED_AGENT_FORBIDDEN_FIELDS)
    for key in frontmatter:
        if key in PLUGIN_SHIPPED_AGENT_ALLOWED_FIELDS:
            continue
        if key in forbidden:
            # Covered by validate_plugin_shipped_restrictions (MAJOR) — no double hit.
            continue
        if key not in KNOWN_FRONTMATTER_FIELDS:
            # Unknown to the agent spec entirely — upstream WARNING already fires.
            continue
        report.minor(
            f"Field '{key}' is not in the plugin-shipped agent allowed set "
            f"({sorted(PLUGIN_SHIPPED_AGENT_ALLOWED_FIELDS)}) — plugins-reference.md:70. "
            "It may be a CPV-legacy / non-plugin agent field and could be ignored "
            "by plugin-shipped agent runtimes.",
            filename,
        )


def validate_task_tool_prohibition(frontmatter: dict[str, Any], filename: str, report: AgentValidationReport) -> None:
    """Validate that subagents (context: fork) do not have Task tool.

    If an agent has context: fork (meaning it's meant to be spawned as a subagent),
    it should NOT have Task in its allowed tools to prevent infinite recursion.
    """
    context = frontmatter.get("context")
    if context != "fork":
        # Not a subagent, no restriction needed
        return

    tools = frontmatter.get("tools")
    if tools is None:
        # An ABSENT 'tools' field means the agent inherits ALL tools — which
        # includes Task (validate_tools_field documents this same inherit-all
        # semantics). That is exactly the infinite-recursion hazard this check
        # exists to catch, so the absent case must NOT be silently skipped:
        # a fork agent that inherits Task can spawn itself just as a fork agent
        # that explicitly lists Task can.
        report.major(
            "Subagent (context: fork) omits 'tools' so it inherits Task - "
            "may cause infinite recursion; declare an explicit 'tools' list "
            "without Task",
            filename,
        )
        return

    # Parse tools field (can be string or list)
    if isinstance(tools, str):
        tool_list = [t.strip() for t in tools.split(",") if t.strip()]
    elif isinstance(tools, list):
        tool_list = [str(t).strip() for t in tools if str(t).strip()]
    else:
        return

    # Check for Task tool (handle patterns like "Task" or "Task(pattern)")
    for tool in tool_list:
        base_tool = tool.split("(")[0].strip()
        if base_tool == "Task":
            report.major(
                "Subagent (context: fork) has Task tool - may cause infinite recursion",
                filename,
            )
            return


def validate_example_blocks(content: str, filename: str, report: AgentValidationReport) -> None:
    """Validate that agent has sufficient example blocks.

    Agent documentation should have 2-3+ <example> blocks with proper structure:
    - <example> opening tag
    - Context line (optional but recommended)
    - user: line with user message
    - assistant: line with assistant response
    - <commentary> block (recommended)
    - </example> closing tag
    """
    _, body, _ = parse_frontmatter(content)

    if not body.strip():
        # No body content - already flagged elsewhere
        return

    # Find all example blocks
    example_pattern = re.compile(r"<example>(.*?)</example>", re.DOTALL)
    examples = example_pattern.findall(body)

    example_count = len(examples)

    # Anthropic RECOMMENDS <example> blocks for trigger quality but does NOT
    # require them — an agent without examples is valid (e.g. agents dispatched
    # by name rather than auto-triggered). WARNING (advisory), not MAJOR, so CPV
    # never calls a valid agent invalid (TRDD-021250b5).
    if example_count == 0:
        report.warning(
            f"No <example> blocks found (recommended: at least {MIN_EXAMPLE_BLOCKS} for trigger quality; not required)",
            filename,
        )
        return

    if example_count < MIN_EXAMPLE_BLOCKS:
        report.warning(
            f"Only {example_count} <example> block(s) found (recommended: at least {MIN_EXAMPLE_BLOCKS}; not required)",
            filename,
        )
    else:
        report.passed(f"Has {example_count} <example> block(s)", filename)

    # Validate each example block structure
    for i, example in enumerate(examples, 1):
        has_user = re.search(r"^\s*user:", example, re.MULTILINE | re.IGNORECASE) is not None
        has_assistant = re.search(r"^\s*assistant:", example, re.MULTILINE | re.IGNORECASE) is not None
        has_commentary = "<commentary>" in example and "</commentary>" in example

        if not has_user:
            report.minor(
                f"Example {i} missing 'user:' line",
                filename,
            )

        if not has_assistant:
            report.minor(
                f"Example {i} missing 'assistant:' line",
                filename,
            )

        if not has_commentary:
            report.info(
                f"Example {i} has no <commentary> block (recommended for clarity)",
                filename,
            )


def validate_body_content(content: str, filename: str, report: AgentValidationReport) -> None:
    """Validate agent body content (after frontmatter)."""
    _, body, _ = parse_frontmatter(content)

    if not body.strip():
        report.major("Agent has no content after frontmatter", filename)
        return

    body_text = body.strip()

    # Minimum content check
    if len(body_text) < MIN_BODY_CHARS:
        report.minor(
            f"Agent body is very short ({len(body_text)} chars, recommended: >{MIN_BODY_CHARS})",
            filename,
        )

    # Agents intentionally have NO body-length limit (user directive 2026-07-22):
    # Anthropic imposes no agent body-length cap, and an agent's full instructions
    # are always loaded, so trimming an agent body loses capability for no runtime
    # benefit. Only SKILLS carry a body-size limit (SKILL_BODY_TOKEN_LIMIT = 5000
    # tokens), because a skill body beyond ~5000 tokens loses its tail to
    # auto-compaction. Do NOT re-add a word/token cap for agents.

    # Role definition check — advisory only; Anthropic does not require a role line.
    # Recognize BOTH second-person ("You are …") and third-person identity statements
    # ("The <Name> Agent is a …", a `## Identity` section, "Acts as …",
    # "… is a … agent that …"). Reporting "missing" when a substantive 3rd-person
    # role-def is present is a false positive (#97).
    _lower = body_text.lower()
    _has_second_person = "you are" in _lower
    # Third-person identity: a `## Identity` heading, OR an "X is a[n] ... agent ..."
    # / "X Agent is a ..." / "Acts as ..." sentence. re-checked on the raw body.
    _has_identity_heading = bool(
        re.search(r"(?m)^\s*#{1,6}\s*identity\b", body_text, re.IGNORECASE)
    )
    _has_third_person_role = bool(
        re.search(
            r"\b(?:is|acts?)\s+(?:a|an|the)\s+[^.\n]{0,80}?\bagent\b"  # "is a … agent"
            r"|\bagent\s+is\s+(?:a|an|the)\b"  # "<Name> Agent is a …"
            r"|\bacts?\s+as\s+(?:a|an|the)\b",  # "Acts as a …"
            body_text,
            re.IGNORECASE,
        )
    )
    if _has_second_person:
        report.passed("Role definition present ('You are...')", filename)
    elif _has_identity_heading or _has_third_person_role:
        # A role IS defined, just in third person. Soft advisory only — never claim
        # the definition is absent. Second person tends to steer a model better.
        report.info(
            "Role definition is written in third person; a second-person 'You are …' "
            "statement is recommended for prompt effectiveness",
            filename,
        )
    else:
        report.warning(
            "Agent body should include a role definition ('You are...' or a clear "
            "third-person identity statement)",
            filename,
        )

    # Check for common sections
    sections_found = []

    if re.search(r"##\s*capabilities", body_text, re.IGNORECASE):
        sections_found.append("Capabilities")
        report.passed("Has '## Capabilities' section", filename)

    if re.search(r"##\s*workflow", body_text, re.IGNORECASE):
        sections_found.append("Workflow")
        report.passed("Has '## Workflow' section", filename)

    if re.search(r"##\s*(approach|guidelines|instructions)", body_text, re.IGNORECASE):
        sections_found.append("Approach/Guidelines")
        report.passed("Has approach/guidelines section", filename)

    if not sections_found:
        report.info(
            "Consider adding structured sections (## Capabilities, ## Workflow, etc.)",
            filename,
        )


def validate_security(content: str, filename: str, report: AgentValidationReport) -> None:
    """Check for security issues in agent content."""
    # Check for hardcoded secrets
    for pattern, description in SECRET_PATTERNS:
        if pattern.search(content):
            report.critical(f"SECURITY: Contains {description}", filename)

    # Check for hardcoded user paths
    for pattern in USER_PATH_PATTERNS:
        match = pattern.search(content)
        if match:
            report.major(
                f"Contains hardcoded user path: {match.group()}",
                filename,
            )

    # Check for ${CLAUDE_PLUGIN_ROOT} or ${CLAUDE_PLUGIN_DATA} usage (good practice)
    if "/scripts/" in content or "\\scripts\\" in content:
        if (
            "${CLAUDE_PLUGIN_ROOT}" not in content
            and "$CLAUDE_PLUGIN_ROOT" not in content
            and "${CLAUDE_PLUGIN_DATA}" not in content
        ):
            report.info(
                "Consider using ${CLAUDE_PLUGIN_ROOT} or ${CLAUDE_PLUGIN_DATA} for plugin-relative paths",
                filename,
            )


def validate_agent(
    agent_path: Path,
    *,
    skills_roots: Sequence[Path] | None = None,
    closure: bool = False,
    closure_ambient: bool = False,
) -> AgentValidationReport:
    """Validate a complete agent file.

    Args:
        agent_path: Path to the agent .md file
        skills_roots: Explicit skill search roots for the closure checks. ``None``
            (the default) auto-resolves them from the agent's own plugin /
            project / user scope; an explicit list — including ``[]`` — replaces
            auto-resolution entirely, which is how a caller gets a hermetic,
            machine-independent answer.
        closure: Also validate every REACHABLE skill and roll its findings into
            this report (opt-in — see ``_roll_in_closure_skill_reports``).
        closure_ambient: Also validate the ambient skill palette present in the
            search roots. Implies the ``closure`` merge behaviour.

    Returns:
        AgentValidationReport with all results

    The three keyword arguments are keyword-ONLY and defaulted, so every existing
    positional caller (``scan_one_agent``, ``validate_scoring``,
    ``validate_plugin``, ``cpv_agent_preflight``) is unaffected.
    """
    report = AgentValidationReport(agent_path=str(agent_path))
    filename = agent_path.name

    # Check file exists
    if not agent_path.exists():
        report.critical(f"Agent file not found: {agent_path}")
        return report

    if not agent_path.is_file():
        report.critical(f"Agent path is not a file: {agent_path}")
        return report

    # Check file extension
    if agent_path.suffix.lower() != ".md":
        report.major(f"Agent file should have .md extension, got: {agent_path.suffix}", filename)

    # Read file content (binary first for encoding check)
    content_bytes = agent_path.read_bytes()

    # Check encoding using shared function
    if not check_utf8_encoding(content_bytes, report, filename):
        return report

    report.passed("File is valid UTF-8", filename)

    content = content_bytes.decode("utf-8")

    # Validate frontmatter
    frontmatter = validate_frontmatter_exists(content, report, filename)

    if frontmatter is not None:
        # Validate individual frontmatter fields
        validate_name_field(frontmatter, filename, report)
        validate_description_field(frontmatter, filename, report)
        validate_tools_field(frontmatter, filename, report)
        # TRDD-94e06820: the body must not invoke a tool the declared 'tools'
        # field does not grant — that call fails silently at runtime. Skipped
        # when 'tools' is absent (agent inherits all tools).
        from cpv_tool_permission_match import validate_body_tool_consistency  # noqa: PLC0415

        _, _body_for_tools, _ = parse_frontmatter(content)
        validate_body_tool_consistency(
            frontmatter.get("tools"), _body_for_tools, report, filename=filename, field_name="tools"
        )
        # A malformed MCP grant is the ROOT CAUSE of the cross-check finding
        # above, so it runs right after it; the shell-fence advisory shares the
        # same body + 'tools' inputs.
        validate_mcp_grant_hygiene(frontmatter, _body_for_tools, filename, report)
        validate_shell_fence_tool_grant(frontmatter, _body_for_tools, filename, report)
        # TRDD-7KS7KP7U: resolve the agent → skill closure and report the broken
        # references. Runs right after the tool-grant checks because AC3 depends
        # on the same 'Skill' grant those checks parse.
        validate_agent_skill_closure(
            agent_path,
            frontmatter,
            _body_for_tools,
            filename,
            report,
            skills_roots=skills_roots,
            closure=closure,
            closure_ambient=closure_ambient,
        )
        validate_model_field(frontmatter, filename, report)
        validate_color_field(frontmatter, filename, report)
        validate_capabilities_field(frontmatter, filename, report)

        # Validate Claude Code-specific fields
        validate_context_field(frontmatter, filename, report)
        validate_agent_field(frontmatter, filename, report)
        validate_user_invocable_field(frontmatter, filename, report)
        validate_system_prompt_field(frontmatter, filename, report)
        validate_skills_field(frontmatter, filename, report)

        # Validate sub-agent specific fields (from sub-agents.md spec)
        validate_permission_mode_field(frontmatter, filename, report)
        validate_disallowed_tools_field(frontmatter, filename, report)
        validate_hooks_field(frontmatter, filename, report)
        validate_initial_prompt_field(frontmatter, filename, report)
        validate_mcp_servers_field(frontmatter, filename, report)

        # Validate new official fields
        validate_memory_field(frontmatter, filename, report)
        validate_isolation_field(frontmatter, filename, report)
        validate_max_turns_field(frontmatter, filename, report)
        validate_background_field(frontmatter, filename, report)
        validate_effort_field(frontmatter, filename, report)

        # Cross-field validations
        validate_task_tool_prohibition(frontmatter, filename, report)
        validate_tool_grant_contradictions(frontmatter, filename, report)

        # Plugin-shipped agent field restrictions
        # (hooks, mcpServers, permissionMode are forbidden when shipped in a plugin)
        # Detect whether this agent file is inside a plugin directory.
        plugin_shipped = is_plugin_shipped_agent(agent_path)
        validate_plugin_shipped_restrictions(frontmatter, filename, report, plugin_shipped)
        # GAP-79 (v2.22.3): plugin-shipped agents only accept 11 fields per
        # plugins-reference.md:70 — flag any CPV-legacy / non-plugin fields.
        validate_plugin_shipped_allowed_fields(frontmatter, filename, report, plugin_shipped)

    # Validate body content
    validate_body_content(content, filename, report)

    # Validate example blocks
    validate_example_blocks(content, filename, report)

    # Security checks
    validate_security(content, filename, report)

    return report


def scan_one_agent(agent_path: Path) -> list[AgentValidationReport]:
    """Top-level worker for the shared ``parallel_scan`` harness (task #384).

    The harness contract requires a pickleable, top-level callable that takes
    one ``Path`` and returns a ``list``. Validators conventionally return a
    list of "findings"; for the per-agent case there's only ONE report per
    file, so we return a single-element ``list[AgentValidationReport]``.
    Wrapping the report in a list keeps the harness contract uniform across
    validators (cache, security, xref, etc. return many findings per file).

    MUST stay at module scope — ``ProcessPoolExecutor`` pickles the callable
    by qualified name and a nested function or closure would raise
    ``PicklingError`` at submit time. Capturing no external state also means
    the worker can run in a freshly-forked process without needing the
    parent's mutable globals.
    """
    return [validate_agent(agent_path)]


def validate_agents_directory(
    agents_dir: Path,
    *,
    skills_roots: Sequence[Path] | None = None,
    closure: bool = False,
    closure_ambient: bool = False,
) -> list[AgentValidationReport]:
    """Validate all agent files in a directory.

    Args:
        agents_dir: Path to the agents/ directory
        skills_roots / closure / closure_ambient: forwarded to
            :func:`validate_agent`. When ANY of them is set the scan runs
            SERIALLY: ``parallel_scan`` pickles a top-level one-argument worker by
            qualified name, so per-call options can only reach a worker through a
            module global — and under the ``spawn`` start method (which
            ``cpv_fork_safety`` pins) a child does not inherit globals. A serial
            loop is correct and costs nothing here, because these options are
            opt-in and low-volume.

    Returns:
        List of AgentValidationReport for each agent, in alphabetical filename
        order (preserved across the parallel harness).

    Implementation note (task #384): per-file validation runs in parallel via
    ``cpv_parallel_runner.parallel_scan`` (``ProcessPoolExecutor``-backed).
    Output order matches input order so callers downstream of this function
    keep the alphabetical invariant they always had. ``ScanResult.error`` set
    by the harness (worker process crashed / unpickleable result / timeout)
    is surfaced as a per-file MAJOR via a synthesized report rather than
    crashing the whole directory scan.
    """
    if not agents_dir.is_dir():
        report = AgentValidationReport(agent_path=str(agents_dir))
        report.critical(f"Not a directory: {agents_dir}")
        return [report]

    # Case-insensitive .md match — Path.glob is case-sensitive on POSIX, so
    # files named Agent.MD / foo.Md would be silently skipped on Linux/macOS
    # even though Claude Code treats them as valid agent files.
    agent_files = [p for p in agents_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"]

    if not agent_files:
        report = AgentValidationReport(agent_path=str(agents_dir))
        report.info("No agent files (*.md) found in directory")
        return [report]

    # Sort once so the harness sees deterministic input order, then trust
    # the harness's input-order preservation contract for output order.
    sorted_files = sorted(agent_files)

    if skills_roots is not None or closure or closure_ambient:
        return [
            validate_agent(
                path,
                skills_roots=skills_roots,
                closure=closure,
                closure_ambient=closure_ambient,
            )
            for path in sorted_files
        ]

    scan_results: list[ScanResult] = parallel_scan(sorted_files, scan_one_agent)

    reports: list[AgentValidationReport] = []
    for sr in scan_results:
        if sr.error is not None:
            # Worker process raised before producing a report (e.g. segfault,
            # OOM, unpickleable result). Surface as a per-file MAJOR so the
            # whole directory scan still returns useful data for the OTHER
            # files instead of crashing the validator. We pick MAJOR over
            # CRITICAL because the file MAY still be valid — we just couldn't
            # prove it. Authors who see this in CI know to re-run or
            # investigate the worker crash separately.
            fail_report = AgentValidationReport(agent_path=str(sr.file_path))
            fail_report.major(
                f"Parallel worker failed for {sr.file_path.name}: {sr.error}",
                sr.file_path.name,
            )
            reports.append(fail_report)
            continue
        # Normal path: scan_one_agent returns [report]. Unpack the single
        # element. If the harness ever returns something else here it's a
        # contract violation — assert loudly during development.
        assert len(sr.findings) == 1, f"scan_one_agent must return exactly one report, got {len(sr.findings)}"
        reports.append(sr.findings[0])

    return reports


def print_results(report: AgentValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    # Count by level
    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    # Print header
    print("\n" + "=" * 60)
    print(f"Agent Validation: {report.agent_path}")
    print("=" * 60)

    # Print summary
    print("\nSummary:")
    print(f"  {COLORS['CRITICAL']}CRITICAL: {counts['CRITICAL']}{COLORS['RESET']}")
    print(f"  {COLORS['MAJOR']}MAJOR:    {counts['MAJOR']}{COLORS['RESET']}")
    print(f"  {COLORS['MINOR']}MINOR:    {counts['MINOR']}{COLORS['RESET']}")
    print(f"  {COLORS['NIT']}NIT:      {counts['NIT']}{COLORS['RESET']}")
    print(f"  {COLORS['WARNING']}WARNING:  {counts['WARNING']}{COLORS['RESET']}")
    if verbose:
        print(f"  {COLORS['INFO']}INFO:     {counts['INFO']}{COLORS['RESET']}")
        print(f"  {COLORS['PASSED']}PASSED:   {counts['PASSED']}{COLORS['RESET']}")

    # Print score
    score = report.score
    score_color = COLORS["PASSED"] if score >= 80 else COLORS["MAJOR"] if score >= 60 else COLORS["CRITICAL"]
    print(f"\n  Score: {score_color}{score}/100{COLORS['RESET']}")

    # Print details
    print("\nDetails:")
    for r in report.results:
        if r.level == "PASSED" and not verbose:
            continue
        if r.level == "INFO" and not verbose:
            continue

        color = COLORS[r.level]
        reset = COLORS["RESET"]
        file_info = f" ({r.file})" if r.file else ""
        line_info = f":{r.line}" if r.line else ""
        print(f"  {color}[{r.level}]{reset} {r.message}{file_info}{line_info}")

    # Print final status
    print("\n" + "-" * 60)
    if report.exit_code == 0:
        print(f"{COLORS['PASSED']}PASSED: Agent validation passed{COLORS['RESET']}")
    elif report.exit_code == 1:
        print(f"{COLORS['CRITICAL']}FAILED: CRITICAL issues - agent will not work{COLORS['RESET']}")
    elif report.exit_code == 2:
        print(f"{COLORS['MAJOR']}WARNING: MAJOR issues - significant problems{COLORS['RESET']}")
    else:
        print(f"{COLORS['MINOR']}INFO: MINOR issues - may affect UX{COLORS['RESET']}")

    print()


def print_json(report: AgentValidationReport) -> None:
    """Print validation results as JSON."""
    output = {
        "agent_path": report.agent_path,
        "exit_code": report.exit_code,
        "score": report.score,
        "counts": {
            "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
            "major": sum(1 for r in report.results if r.level == "MAJOR"),
            "minor": sum(1 for r in report.results if r.level == "MINOR"),
            "info": sum(1 for r in report.results if r.level == "INFO"),
            "passed": sum(1 for r in report.results if r.level == "PASSED"),
        },
        "results": [{"level": r.level, "message": r.message, "file": r.file, "line": r.line} for r in report.results],
    }
    print(json.dumps(output, indent=2))


def main() -> int:
    """Main entry point."""
    from cpv_validation_common import launcher_epilog

    parser = argparse.ArgumentParser(
        description="Validate a Claude Code agent file or directory of agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=launcher_epilog("agent"),
    )
    parser.add_argument("path", help="Path to agent .md file or agents/ directory")
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
    parser.add_argument(
        "--skills-root",
        action="append",
        default=None,
        dest="skills_roots",
        metavar="PATH",
        help=(
            "Skill directory to resolve the agent's skills against (repeatable). "
            "Replaces auto-resolution from the plugin / project / user scope, which is what "
            "makes the closure checks hermetic and machine-independent."
        ),
    )
    parser.add_argument(
        "--closure",
        action="store_true",
        help="Also validate every reachable skill and roll its findings into the agent's report",
    )
    parser.add_argument(
        "--closure-ambient",
        action="store_true",
        help="Also validate the ambient skill palette present in the search roots (noisy — opt in)",
    )
    args = parser.parse_args()

    skills_roots: list[Path] | None = None
    if args.skills_roots is not None:
        skills_roots = []
        for raw in args.skills_roots:
            root = Path(raw).expanduser()
            if not root.is_dir():
                # Fail loudly: silently dropping a bad root would leave every
                # name unresolved and turn the whole closure check vacuous.
                print(f"Error: --skills-root {root} is not a directory", file=sys.stderr)
                return 1
            skills_roots.append(root.resolve())

    path = Path(args.path).resolve()

    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        return 1

    # Verify content type — must be .md file or directory containing .md files.
    # Use case-insensitive suffix check to stay consistent with validate_agent()
    # (which compares suffix.lower()) and to handle case-sensitive filesystems
    # where .MD / .Md are legal filenames.
    if path.is_file() and path.suffix.lower() != ".md":
        print(f"Error: {path} is not a Markdown (.md) agent file", file=sys.stderr)
        return 1
    if path.is_dir() and not any(p.suffix.lower() == ".md" for p in path.iterdir() if p.is_file()):
        print(f"Error: No agent definition files (.md) found in {path}", file=sys.stderr)
        return 1

    # Handle directory vs file
    if path.is_dir():
        reports = validate_agents_directory(
            path,
            skills_roots=skills_roots,
            closure=args.closure,
            closure_ambient=args.closure_ambient,
        )
    else:
        reports = [
            validate_agent(
                path,
                skills_roots=skills_roots,
                closure=args.closure,
                closure_ambient=args.closure_ambient,
            )
        ]

    # Output
    if args.json:
        if len(reports) == 1:
            print_json(reports[0])
        else:
            combined = {
                "agents": [
                    {
                        "agent_path": r.agent_path,
                        "exit_code": r.exit_code,
                        "score": r.score,
                        "counts": {
                            "critical": sum(1 for x in r.results if x.level == "CRITICAL"),
                            "major": sum(1 for x in r.results if x.level == "MAJOR"),
                            "minor": sum(1 for x in r.results if x.level == "MINOR"),
                            "info": sum(1 for x in r.results if x.level == "INFO"),
                            "passed": sum(1 for x in r.results if x.level == "PASSED"),
                        },
                        "results": [
                            {"level": x.level, "message": x.message, "file": x.file, "line": x.line} for x in r.results
                        ],
                    }
                    for r in reports
                ],
                "overall_exit_code": max(r.exit_code for r in reports),
            }
            print(json.dumps(combined, indent=2))
    else:
        for report in reports:
            if args.report:
                agent_file = report.agent_path or args.path
                save_report_and_print_summary(
                    report,
                    Path(args.report),
                    f"Agent Validation: {agent_file}",
                    print_results,
                    args.verbose,
                    plugin_path=args.path,
                )
            else:
                print_results(report, args.verbose)

    # Return worst exit code — in strict mode, NIT issues also block validation
    if args.strict:
        return max(r.exit_code_strict() for r in reports)
    return max(r.exit_code for r in reports)


if __name__ == "__main__":
    sys.exit(main())
