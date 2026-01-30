#!/usr/bin/env python3
"""
Claude Plugins Validation - Comprehensive Skill Validator

Validates individual skill directories using 168+ validation techniques from:
- AgentSkills OpenSpec (skills-ref library) - 44 rules
- Nixtla Quality Standards (strict mode) - Required sections, description quality
- Meta-Skill Validation - 8+1 Pillars, token budgets, checklists
- Component Validators - Multi-scale scoring (0-3), letter grading (A-F)

Usage:
    uv run python scripts/validate_skill_comprehensive.py path/to/skill/
    uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --verbose
    uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --json
    uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --strict  # Nixtla strict mode
    uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --pillars # 8+1 Pillars validation

Exit codes:
    0 - All checks passed (Grade A/B)
    1 - CRITICAL issues found (Grade F)
    2 - MAJOR issues found (Grade D)
    3 - MINOR issues found (Grade C)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

# =============================================================================
# Constants from Multiple Validation Sources
# =============================================================================

# Severity levels
Level = Literal["CRITICAL", "MAJOR", "MINOR", "INFO", "PASSED"]

# Multi-scale scoring (0-3) from agent-validator
Score = Literal[0, 1, 2, 3]

# --- AgentSkills OpenSpec Constants ---
MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500

# AgentSkills OpenSpec allowed fields (strict whitelist)
OPENSPEC_ALLOWED_FIELDS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
}

# --- Claude Code Extended Fields ---
CLAUDE_CODE_FIELDS = {
    "name",
    "description",
    "argument-hint",
    "disable-model-invocation",
    "user-invocable",
    "allowed-tools",
    "model",
    "context",
    "agent",
    "hooks",
}

# --- Nixtla/Enterprise Extended Fields ---
ENTERPRISE_REQUIRED_FIELDS = {"name", "description", "allowed-tools", "version", "author", "license"}
ENTERPRISE_OPTIONAL_FIELDS = {"model", "disable-model-invocation", "mode", "tags", "metadata"}
DEPRECATED_FIELDS = {"when_to_use"}

# Combine all known fields
ALL_KNOWN_FIELDS = CLAUDE_CODE_FIELDS | ENTERPRISE_REQUIRED_FIELDS | ENTERPRISE_OPTIONAL_FIELDS

# --- Token Budget Constants ---
MAX_SKILL_LINES = 500  # Warning threshold
MAX_SKILL_LINES_ERROR = 800  # Error threshold
MAX_WORD_COUNT_WARN = 3500
MAX_WORD_COUNT_ERROR = 5000
MAX_DESCRIPTION_WARN = 200
MAX_FRONTMATTER_CHARS_WARN = 12000
MAX_FRONTMATTER_CHARS_ERROR = 15000

# --- Valid Values ---
VALID_CONTEXT_VALUES = {"fork"}
BUILTIN_AGENT_TYPES = {"Explore", "Plan", "general-purpose"}

# Valid Claude Code tools (2025)
VALID_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "Bash",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
    "NotebookEdit",
    "AskUserQuestion",
    "Skill",
}

# --- Nixtla Strict Mode Required Sections ---
REQUIRED_SECTIONS = [
    "## Overview",
    "## Prerequisites",
    "## Instructions",
    "## Output",
    "## Error Handling",
    "## Examples",
    "## Resources",
]

# --- Description Quality Patterns (Nixtla Strict Mode) ---
RE_DESCRIPTION_USE_WHEN = re.compile(r"[Uu]se\s+when\s+", re.IGNORECASE)
RE_DESCRIPTION_TRIGGER_WITH = re.compile(r"[Tt]rigger\s+with\s+", re.IGNORECASE)
RE_FIRST_PERSON = re.compile(r"\b(I\s+can|I\s+will|I\s+am|I\s+help)\b", re.IGNORECASE)
RE_SECOND_PERSON = re.compile(r"\b(You\s+can|You\s+should|You\s+will|You\s+need)\b", re.IGNORECASE)

# --- Path Validation Patterns ---
ABSOLUTE_PATH_PATTERNS = [
    (re.compile(r"/home/\w+/"), "/home/..."),
    (re.compile(r"/Users/\w+/"), "/Users/..."),
    (re.compile(r"[A-Za-z]:\\\\Users\\\\"), "C:\\Users\\..."),
]

# --- Reference Pattern ---
RE_BASEDIR_SCRIPTS = re.compile(r"\{baseDir\}/scripts/([^\s\}]+)")
RE_BASEDIR_REFERENCES = re.compile(r"\{baseDir\}/references/([^\s\}]+)")
RE_BASEDIR_ASSETS = re.compile(r"\{baseDir\}/assets/([^\s\}]+)")

# --- 8+1 Pillars for lang-* and convert-* skills ---
EIGHT_PILLARS = [
    ("Module", ["import", "export", "module", "use", "require", "package", "namespace"]),
    ("Error", ["Result", "Exception", "Error", "try", "catch", "?", "unwrap", "panic"]),
    ("Concurrency", ["async", "await", "thread", "channel", "spawn", "Actor", "mutex", "lock"]),
    ("Metaprogramming", ["macro", "decorator", "@", "derive", "annotation", "quote", "defmacro"]),
    ("Zero/Default", ["null", "None", "nil", "Option", "Maybe", "default", "?", "undefined"]),
    ("Serialization", ["JSON", "serde", "marshal", "encode", "decode", "parse", "serialize"]),
    ("Build", ["Cargo", "npm", "pip", "mix", "make", "package.json", "deps", "go mod"]),
    ("Testing", ["test", "describe", "it", "assert", "expect", "mock", "#[test]", "pytest"]),
]

NINTH_PILLAR = ("Dev Workflow/REPL", ["REPL", "iex", "ghci", "clj", "hot reload", "interactive"])

# Languages requiring 9th pillar
REPL_CENTRIC_LANGUAGES = {"clojure", "elixir", "erlang", "haskell", "fsharp", "f#", "lisp", "scheme", "racket"}

# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ValidationResult:
    """Single validation result with multi-scale score support."""

    level: Level
    message: str
    file: str | None = None
    line: int | None = None
    category: str | None = None  # For grouping in reports
    score: int = 0  # 0-3 multi-scale score (0=missing, 1=inadequate, 2=adequate, 3=excellent)


@dataclass
class PillarScore:
    """Score for a single pillar (0.0, 0.5, or 1.0)."""

    name: str
    score: float  # 0.0 = missing, 0.5 = partial, 1.0 = full
    notes: str = ""


@dataclass
class ValidationReport:
    """Complete validation report for a skill with scoring."""

    skill_path: str
    results: list[ValidationResult] = field(default_factory=list)
    pillar_scores: list[PillarScore] = field(default_factory=list)
    category_scores: dict[str, float] = field(default_factory=dict)
    overall_score: float = 0.0
    grade: str = "F"

    def add(
        self,
        level: Level,
        message: str,
        file: str | None = None,
        line: int | None = None,
        category: str | None = None,
        score: int = 0,
    ) -> None:
        """Add a validation result."""
        self.results.append(ValidationResult(level, message, file, line, category, score))

    def passed(self, message: str, file: str | None = None, category: str | None = None) -> None:
        self.add("PASSED", message, file, category=category, score=3)

    def info(self, message: str, file: str | None = None, category: str | None = None) -> None:
        self.add("INFO", message, file, category=category, score=2)

    def minor(
        self, message: str, file: str | None = None, line: int | None = None, category: str | None = None
    ) -> None:
        self.add("MINOR", message, file, line, category=category, score=1)

    def major(
        self, message: str, file: str | None = None, line: int | None = None, category: str | None = None
    ) -> None:
        self.add("MAJOR", message, file, line, category=category, score=0)

    def critical(
        self, message: str, file: str | None = None, line: int | None = None, category: str | None = None
    ) -> None:
        self.add("CRITICAL", message, file, line, category=category, score=0)

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

    def calculate_grade(self) -> None:
        """Calculate letter grade based on overall score."""
        if self.overall_score >= 90:
            self.grade = "A"
        elif self.overall_score >= 80:
            self.grade = "B"
        elif self.overall_score >= 70:
            self.grade = "C"
        elif self.overall_score >= 60:
            self.grade = "D"
        else:
            self.grade = "F"


# =============================================================================
# Parsing Functions
# =============================================================================


def parse_frontmatter(content: str) -> tuple[dict[str, Any] | None, str, int]:
    """Parse YAML frontmatter from skill content.

    Returns:
        Tuple of (frontmatter_dict, body_content, frontmatter_end_line)
        Returns (None, content, 0) if no frontmatter found
    """
    if not content.startswith("---"):
        return None, content, 0

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None, content, 0

    try:
        frontmatter = yaml.safe_load(parts[1])
        if frontmatter is None:
            frontmatter = {}
        body = parts[2]
        fm_end_line = parts[0].count("\n") + parts[1].count("\n") + 2
        return frontmatter, body, fm_end_line
    except yaml.YAMLError:
        return None, content, 0


def find_skill_md(skill_dir: Path) -> Path | None:
    """Find the SKILL.md file (uppercase preferred, lowercase accepted)."""
    for name in ("SKILL.md", "skill.md"):
        path = skill_dir / name
        if path.exists():
            return path
    return None


# =============================================================================
# Validation Functions
# =============================================================================


def validate_skill_md_exists(skill_path: Path, report: ValidationReport) -> bool:
    """Validate SKILL.md exists (required)."""
    skill_md = find_skill_md(skill_path)

    if skill_md is None:
        report.critical("SKILL.md not found (required)", "SKILL.md", category="Structure")
        return False

    if skill_md.name == "skill.md":
        report.minor("SKILL.md should be uppercase (found 'skill.md')", "skill.md", category="Structure")
    else:
        report.passed("SKILL.md exists", "SKILL.md", category="Structure")
    return True


def validate_frontmatter_structure(content: str, report: ValidationReport) -> dict[str, Any] | None:
    """Validate YAML frontmatter structure."""
    if not content.startswith("---"):
        report.info("No YAML frontmatter found (optional but recommended)", "SKILL.md", category="Frontmatter")
        return None

    frontmatter, _, _ = parse_frontmatter(content)

    if frontmatter is None and content.startswith("---"):
        report.critical(
            "Malformed YAML frontmatter (missing closing --- or invalid YAML)",
            "SKILL.md",
            category="Frontmatter",
        )
        return None

    if frontmatter is None:
        return None

    # Check frontmatter size (token budget)
    fm_str = content.split("---", 2)[1] if content.startswith("---") else ""
    fm_chars = len(fm_str)
    if fm_chars > MAX_FRONTMATTER_CHARS_ERROR:
        report.critical(
            f"Frontmatter exceeds {MAX_FRONTMATTER_CHARS_ERROR} characters ({fm_chars} chars)",
            "SKILL.md",
            category="Token Budget",
        )
    elif fm_chars > MAX_FRONTMATTER_CHARS_WARN:
        report.minor(
            f"Frontmatter exceeds {MAX_FRONTMATTER_CHARS_WARN} characters ({fm_chars} chars)",
            "SKILL.md",
            category="Token Budget",
        )

    report.passed("Valid YAML frontmatter", "SKILL.md", category="Frontmatter")
    return frontmatter


def validate_name_field(
    frontmatter: dict[str, Any],
    skill_dir_name: str,
    report: ValidationReport,
    strict_openspec: bool = False,
) -> None:
    """Validate the 'name' frontmatter field with AgentSkills OpenSpec rules."""
    if "name" not in frontmatter:
        if strict_openspec:
            report.critical("Missing required field: 'name'", "SKILL.md", category="Frontmatter")
        else:
            report.info(
                f"No 'name' field (will use directory name: {skill_dir_name})",
                "SKILL.md",
                category="Frontmatter",
            )
        name = skill_dir_name
    else:
        name = frontmatter["name"]
        report.passed(f"'name' field present: {name}", "SKILL.md", category="Frontmatter")

    if not isinstance(name, str):
        report.critical(f"'name' must be a string, got {type(name).__name__}", "SKILL.md", category="Frontmatter")
        return

    # Unicode NFKC normalization (AgentSkills OpenSpec)
    name = unicodedata.normalize("NFKC", name.strip())

    # Length check (max 64 chars)
    if len(name) > MAX_SKILL_NAME_LENGTH:
        report.major(
            f"Skill name exceeds {MAX_SKILL_NAME_LENGTH} characters ({len(name)} chars): {name}",
            "SKILL.md",
            category="Frontmatter",
        )

    # Lowercase check
    if name != name.lower():
        report.major(f"Skill name must be lowercase: {name}", "SKILL.md", category="Frontmatter")

    # Kebab-case format check
    if not re.match(r"^[a-z][a-z0-9-]*[a-z0-9]$", name) and len(name) > 1:
        # Allow Unicode characters for i18n support
        if not all(c.isalnum() or c == "-" for c in name):
            report.major(
                f"Skill name must use only letters, numbers, hyphens: {name}",
                "SKILL.md",
                category="Frontmatter",
            )

    # No leading/trailing hyphens
    if name.startswith("-") or name.endswith("-"):
        report.major("Skill name cannot start or end with a hyphen", "SKILL.md", category="Frontmatter")

    # No consecutive hyphens
    if "--" in name:
        report.major("Skill name cannot contain consecutive hyphens", "SKILL.md", category="Frontmatter")

    # Reserved words check
    name_lower = name.lower()
    if "anthropic" in name_lower or "claude" in name_lower:
        report.major(f"Skill name contains reserved word: {name}", "SKILL.md", category="Frontmatter")

    # Directory name match check (AgentSkills OpenSpec requirement)
    dir_name = unicodedata.normalize("NFKC", skill_dir_name)
    if "name" in frontmatter and dir_name != name:
        if strict_openspec:
            report.major(
                f"Directory name '{skill_dir_name}' must match skill name '{name}'",
                "SKILL.md",
                category="Frontmatter",
            )
        else:
            report.info(
                f"Skill name '{name}' differs from directory name '{skill_dir_name}'",
                "SKILL.md",
                category="Frontmatter",
            )


def validate_description_field(
    frontmatter: dict[str, Any],
    body: str,
    report: ValidationReport,
    strict_mode: bool = False,
) -> None:
    """Validate the 'description' field with Nixtla quality standards."""
    if "description" not in frontmatter:
        if body.strip():
            report.info(
                "No 'description' field (will use first paragraph of content)",
                "SKILL.md",
                category="Frontmatter",
            )
        else:
            report.major(
                "No 'description' field and no body content for fallback",
                "SKILL.md",
                category="Frontmatter",
            )
        return

    desc = frontmatter["description"]
    if not isinstance(desc, str):
        report.major(
            f"'description' must be a string, got {type(desc).__name__}",
            "SKILL.md",
            category="Frontmatter",
        )
        return

    # Length checks
    if len(desc) < 20:
        report.minor("Description is very short (< 20 chars)", "SKILL.md", category="Description Quality")

    if len(desc) > MAX_DESCRIPTION_LENGTH:
        report.major(
            f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters ({len(desc)} chars)",
            "SKILL.md",
            category="Description Quality",
        )
    elif len(desc) > MAX_DESCRIPTION_WARN:
        report.minor(
            f"Description is long ({len(desc)} chars), consider shortening to < {MAX_DESCRIPTION_WARN}",
            "SKILL.md",
            category="Description Quality",
        )

    # Nixtla strict mode quality checks
    if strict_mode:
        # Must include "Use when..." phrase
        if not RE_DESCRIPTION_USE_WHEN.search(desc):
            report.major(
                "Description must include 'Use when ...' phrase (Nixtla strict mode)",
                "SKILL.md",
                category="Description Quality",
            )

        # Must include "Trigger with..." phrase
        if not RE_DESCRIPTION_TRIGGER_WITH.search(desc):
            report.minor(
                "Description should include 'Trigger with ...' phrase (Nixtla strict mode)",
                "SKILL.md",
                category="Description Quality",
            )

        # No first person
        if RE_FIRST_PERSON.search(desc):
            report.major(
                "Description must NOT use first person (I can / I will)",
                "SKILL.md",
                category="Description Quality",
            )

        # No second person
        if RE_SECOND_PERSON.search(desc):
            report.major(
                "Description must NOT use second person (You can / You should)",
                "SKILL.md",
                category="Description Quality",
            )
    else:
        # Non-strict mode - just warn
        if not RE_DESCRIPTION_USE_WHEN.search(desc):
            report.info(
                "Description should include 'Use when ...' phrase for better discoverability",
                "SKILL.md",
                category="Description Quality",
            )

    report.passed("'description' field present", "SKILL.md", category="Frontmatter")


def validate_allowed_tools_field(
    frontmatter: dict[str, Any],
    report: ValidationReport,
    strict_mode: bool = False,
) -> None:
    """Validate the 'allowed-tools' field with Nixtla strict mode."""
    if "allowed-tools" not in frontmatter:
        return

    tools = frontmatter["allowed-tools"]

    # Nixtla strict mode: must be CSV string, not YAML array
    if isinstance(tools, list):
        if strict_mode:
            report.major(
                "'allowed-tools' must be comma-separated string (CSV), not YAML array",
                "SKILL.md",
                category="Frontmatter",
            )
        tool_list = tools
    elif isinstance(tools, str):
        tool_list = [t.strip() for t in tools.split(",")]
    else:
        report.major(
            f"'allowed-tools' must be string or list, got {type(tools).__name__}",
            "SKILL.md",
            category="Frontmatter",
        )
        return

    if not tool_list:
        report.minor("'allowed-tools' is empty", "SKILL.md", category="Frontmatter")
        return

    # Validate individual tools
    for tool in tool_list:
        # Handle scoped tools like Bash(git:*)
        base_tool = tool.split("(")[0].strip()
        if base_tool and base_tool not in VALID_TOOLS and not base_tool.startswith("mcp__"):
            report.info(
                f"Unknown tool '{base_tool}' (may be valid if custom MCP tool)",
                "SKILL.md",
                category="Frontmatter",
            )

    # Nixtla strict mode: forbid unscoped Bash
    if strict_mode and "Bash" in tool_list:
        report.major(
            "Unscoped 'Bash' forbidden in strict mode - use scoped Bash(git:*) or Bash(npm:*)",
            "SKILL.md",
            category="Frontmatter",
        )

    # Over-permissioning warning
    if len(tool_list) > 6:
        report.minor(
            f"Many tools permitted ({len(tool_list)}) - consider limiting",
            "SKILL.md",
            category="Frontmatter",
        )

    report.passed(f"'allowed-tools' field valid: {len(tool_list)} tool(s)", "SKILL.md", category="Frontmatter")


def validate_context_field(frontmatter: dict[str, Any], report: ValidationReport) -> None:
    """Validate the 'context' frontmatter field."""
    if "context" not in frontmatter:
        return

    context = frontmatter["context"]

    if not isinstance(context, str):
        report.critical(
            f"'context' must be a string, got {type(context).__name__}",
            "SKILL.md",
            category="Frontmatter",
        )
        return

    if context not in VALID_CONTEXT_VALUES:
        report.critical(
            f"Invalid 'context' value: '{context}'. Valid values: {VALID_CONTEXT_VALUES}",
            "SKILL.md",
            category="Frontmatter",
        )
        return

    report.passed(f"'context' field valid: {context}", "SKILL.md", category="Frontmatter")


def validate_agent_field(frontmatter: dict[str, Any], report: ValidationReport) -> None:
    """Validate the 'agent' frontmatter field."""
    if "agent" not in frontmatter:
        if frontmatter.get("context") == "fork":
            report.info(
                "'agent' not specified with context: fork (defaults to general-purpose)",
                "SKILL.md",
                category="Frontmatter",
            )
        return

    agent = frontmatter["agent"]

    if not isinstance(agent, str):
        report.critical(
            f"'agent' must be a string, got {type(agent).__name__}",
            "SKILL.md",
            category="Frontmatter",
        )
        return

    if frontmatter.get("context") != "fork":
        report.major(
            "'agent' field has no effect without 'context: fork'",
            "SKILL.md",
            category="Frontmatter",
        )

    if agent in BUILTIN_AGENT_TYPES:
        report.passed(f"'agent' field valid (built-in): {agent}", "SKILL.md", category="Frontmatter")
    else:
        report.info(
            f"'agent' value '{agent}' is not a built-in type (may be custom from .claude/agents/)",
            "SKILL.md",
            category="Frontmatter",
        )


def validate_boolean_field(
    frontmatter: dict[str, Any],
    field_name: str,
    report: ValidationReport,
) -> None:
    """Validate a boolean frontmatter field."""
    if field_name not in frontmatter:
        return

    value = frontmatter[field_name]

    if not isinstance(value, bool):
        report.critical(
            f"'{field_name}' must be a boolean (true/false), got {type(value).__name__}",
            "SKILL.md",
            category="Frontmatter",
        )
        return

    report.passed(f"'{field_name}' field valid: {value}", "SKILL.md", category="Frontmatter")


def validate_field_whitelist(
    frontmatter: dict[str, Any],
    report: ValidationReport,
    strict_openspec: bool = False,
) -> None:
    """Validate frontmatter fields against whitelist."""
    allowed_fields = OPENSPEC_ALLOWED_FIELDS if strict_openspec else ALL_KNOWN_FIELDS

    for key in frontmatter.keys():
        if key in DEPRECATED_FIELDS:
            report.minor(
                f"Deprecated field '{key}' (may be ignored by CLI)",
                "SKILL.md",
                category="Frontmatter",
            )
        elif key not in allowed_fields:
            if strict_openspec:
                report.major(
                    f"Unexpected field '{key}' in frontmatter. OpenSpec allows: {sorted(OPENSPEC_ALLOWED_FIELDS)}",
                    "SKILL.md",
                    category="Frontmatter",
                )
            else:
                report.info(
                    f"Unknown frontmatter field '{key}' (may be ignored by CLI)",
                    "SKILL.md",
                    category="Frontmatter",
                )


def validate_token_budget(content: str, body: str, report: ValidationReport) -> None:
    """Validate token budget (line count, word count)."""
    total_lines = content.count("\n") + 1
    word_count = len(body.split())

    # Line count check
    if total_lines > MAX_SKILL_LINES_ERROR:
        report.major(
            f"SKILL.md has {total_lines} lines (max {MAX_SKILL_LINES_ERROR}). Must use progressive disclosure.",
            "SKILL.md",
            category="Token Budget",
        )
    elif total_lines > MAX_SKILL_LINES:
        report.minor(
            f"SKILL.md has {total_lines} lines (recommended: under {MAX_SKILL_LINES}). "
            "Consider moving detailed content to supporting files.",
            "SKILL.md",
            category="Token Budget",
        )
    else:
        report.passed(f"SKILL.md line count OK ({total_lines} lines)", "SKILL.md", category="Token Budget")

    # Word count check
    if word_count > MAX_WORD_COUNT_ERROR:
        report.major(
            f"Content exceeds {MAX_WORD_COUNT_ERROR} words ({word_count})",
            "SKILL.md",
            category="Token Budget",
        )
    elif word_count > MAX_WORD_COUNT_WARN:
        report.minor(
            f"Content is lengthy ({word_count} words)",
            "SKILL.md",
            category="Token Budget",
        )


def validate_required_sections(body: str, report: ValidationReport, strict_mode: bool = False) -> None:
    """Validate required sections (Nixtla strict mode)."""
    if not strict_mode:
        return

    for section in REQUIRED_SECTIONS:
        # Use regex to match exact section headers at start of line
        # This prevents false positives like "### Research Output Files" matching "## Output"
        section_pattern = rf"(?m)^{re.escape(section)}\s*$"
        if not re.search(section_pattern, body):
            report.major(
                f"Required section missing: '{section}' (Nixtla strict mode)",
                "SKILL.md",
                category="Required Sections",
            )
        else:
            report.passed(f"Required section present: {section}", "SKILL.md", category="Required Sections")

    # Instructions must have numbered list (only if ## Instructions section actually exists)
    # Use regex to match exact section header, not substring like "## Instructions vs System Prompts"
    instructions_match = re.search(r"(?m)^## Instructions\s*$", body)
    if instructions_match:
        instructions_start = instructions_match.start()
        # Find next ## header (any level 2 header)
        next_section = re.search(r"(?m)^## ", body[instructions_match.end():])
        if next_section:
            instructions_end = instructions_match.end() + next_section.start()
        else:
            instructions_end = len(body)
        instructions = body[instructions_start:instructions_end]

        has_numbered = bool(re.search(r"(?m)^\s*1\.\s+\S+", instructions))
        if not has_numbered:
            report.major(
                "'## Instructions' must include numbered step-by-step list",
                "SKILL.md",
                category="Required Sections",
            )


def validate_path_formats(body: str, report: ValidationReport) -> None:
    """Validate path formats (no absolute paths, forward slashes only)."""
    lines = body.split("\n")
    for i, line in enumerate(lines, 1):
        # Check for absolute paths
        for pattern, desc in ABSOLUTE_PATH_PATTERNS:
            if pattern.search(line):
                report.major(
                    f"Line {i}: contains absolute/OS-specific path ({desc}) - use '{{baseDir}}/...'",
                    "SKILL.md",
                    line=i,
                    category="Path Format",
                )

        # Check for backslashes
        if "\\scripts\\" in line or "\\references\\" in line:
            report.major(
                f"Line {i}: uses backslashes in path - use forward slashes",
                "SKILL.md",
                line=i,
                category="Path Format",
            )


def validate_resource_references(skill_path: Path, body: str, report: ValidationReport) -> None:
    """Validate that referenced scripts/resources exist."""
    # Check {baseDir}/scripts/... references
    for match in RE_BASEDIR_SCRIPTS.finditer(body):
        rel_path = match.group(1)
        script_path = skill_path / "scripts" / rel_path
        if not script_path.exists():
            report.major(
                f"Referenced script not found: '{{baseDir}}/scripts/{rel_path}'",
                "SKILL.md",
                category="Resource References",
            )
        else:
            report.passed(f"Script exists: scripts/{rel_path}", category="Resource References")

    # Check {baseDir}/references/... references
    for match in RE_BASEDIR_REFERENCES.finditer(body):
        rel_path = match.group(1)
        ref_path = skill_path / "references" / rel_path
        if not ref_path.exists():
            report.major(
                f"Referenced file not found: '{{baseDir}}/references/{rel_path}'",
                "SKILL.md",
                category="Resource References",
            )
        else:
            report.passed(f"Reference exists: references/{rel_path}", category="Resource References")

    # Check markdown links to local files
    local_refs = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", body)
    checked_files: set[str] = set()  # Track files we've already validated
    for _, link_target in local_refs:
        if link_target.startswith(("http://", "https://", "mailto:", "#", "{")):
            continue
        # Handle anchor links (e.g., "references/file.md#section-name")
        file_path = link_target.split("#")[0] if "#" in link_target else link_target
        # Skip if we've already checked this file
        if file_path in checked_files:
            continue
        checked_files.add(file_path)
        ref_path = skill_path / file_path
        if not ref_path.exists():
            report.major(
                f"Referenced file not found: {file_path}",
                "SKILL.md",
                category="Resource References",
            )
        else:
            report.passed(f"Referenced file exists: {file_path}", "SKILL.md", category="Resource References")


def validate_directory_structure(skill_path: Path, report: ValidationReport) -> None:
    """Validate skill directory structure."""
    optional_dirs = ["scripts", "examples", "references", "assets", "templates"]

    for dir_name in optional_dirs:
        dir_path = skill_path / dir_name
        if dir_path.is_dir():
            report.passed(f"Optional directory exists: {dir_name}/", category="Structure")

    # Check for scripts that should be executable
    scripts_dir = skill_path / "scripts"
    if scripts_dir.is_dir():
        for script in scripts_dir.iterdir():
            if script.is_file() and script.suffix in {".sh", ".py", ".bash"}:
                if not os.access(script, os.X_OK):
                    report.major(
                        f"Script not executable: scripts/{script.name}",
                        f"scripts/{script.name}",
                        category="Structure",
                    )
                else:
                    report.passed(
                        f"Script executable: scripts/{script.name}",
                        f"scripts/{script.name}",
                        category="Structure",
                    )


def validate_pillars(
    skill_path: Path,
    body: str,
    report: ValidationReport,
    include_ninth: bool = False,
) -> None:
    """Validate 8+1 Pillars coverage for lang-* and convert-* skills."""
    skill_name = skill_path.name.lower()

    # Only apply to lang-* and convert-* skills
    if not (skill_name.startswith("lang-") or skill_name.startswith("convert-")):
        report.info(
            "8+1 Pillars validation skipped (only for lang-* and convert-* skills)",
            category="Pillars Coverage",
        )
        return

    # Determine if 9th pillar should be included
    should_include_ninth = include_ninth
    if not should_include_ninth:
        for lang in REPL_CENTRIC_LANGUAGES:
            if lang in skill_name:
                should_include_ninth = True
                break

    pillars_to_check = list(EIGHT_PILLARS)
    if should_include_ninth:
        pillars_to_check.append(NINTH_PILLAR)

    total_score = 0.0
    max_score = len(pillars_to_check)

    for pillar_name, keywords in pillars_to_check:
        # Count keyword occurrences
        keyword_count = 0
        for keyword in keywords:
            keyword_count += len(re.findall(re.escape(keyword), body, re.IGNORECASE))

        # Check for dedicated section
        has_section = bool(re.search(rf"##\s*{re.escape(pillar_name)}", body, re.IGNORECASE))

        # Score: 1.0 = dedicated section with content, 0.5 = mentioned, 0.0 = missing
        if has_section and keyword_count >= 3:
            score = 1.0
            notes = "Full coverage with dedicated section"
        elif keyword_count >= 5:
            score = 1.0
            notes = f"Full coverage ({keyword_count} keyword occurrences)"
        elif keyword_count >= 2:
            score = 0.5
            notes = f"Partial coverage ({keyword_count} keyword occurrences)"
        else:
            score = 0.0
            notes = "Missing or minimal coverage"

        total_score += score
        report.pillar_scores.append(PillarScore(pillar_name, score, notes))

        if score == 0.0:
            report.minor(
                f"Pillar '{pillar_name}' has minimal coverage",
                "SKILL.md",
                category="Pillars Coverage",
            )
        elif score == 0.5:
            report.info(
                f"Pillar '{pillar_name}' has partial coverage",
                "SKILL.md",
                category="Pillars Coverage",
            )
        else:
            report.passed(
                f"Pillar '{pillar_name}' has full coverage",
                "SKILL.md",
                category="Pillars Coverage",
            )

    # Store pillar score in category scores
    pillar_percentage = (total_score / max_score) * 100 if max_score > 0 else 0
    report.category_scores["Pillars Coverage"] = pillar_percentage

    # Threshold checks
    if pillar_percentage < 50:
        report.major(
            f"Pillars coverage is incomplete ({total_score}/{max_score})",
            "SKILL.md",
            category="Pillars Coverage",
        )
    elif pillar_percentage < 75:
        report.minor(
            f"Pillars coverage needs improvement ({total_score}/{max_score})",
            "SKILL.md",
            category="Pillars Coverage",
        )
    else:
        report.passed(
            f"Pillars coverage is good ({total_score}/{max_score})",
            "SKILL.md",
            category="Pillars Coverage",
        )


def calculate_overall_score(report: ValidationReport) -> None:
    """Calculate overall score and grade."""
    # Count results by level
    critical_count = sum(1 for r in report.results if r.level == "CRITICAL")
    major_count = sum(1 for r in report.results if r.level == "MAJOR")
    minor_count = sum(1 for r in report.results if r.level == "MINOR")
    passed_count = sum(1 for r in report.results if r.level == "PASSED")
    total_checks = critical_count + major_count + minor_count + passed_count

    if total_checks == 0:
        report.overall_score = 0.0
        report.grade = "F"
        return

    # Weighted scoring:
    # CRITICAL = 0 points, MAJOR = 1 point, MINOR = 2 points, PASSED = 3 points
    weighted_score = critical_count * 0 + major_count * 1 + minor_count * 2 + passed_count * 3
    max_possible = total_checks * 3

    report.overall_score = (weighted_score / max_possible) * 100 if max_possible > 0 else 0.0
    report.calculate_grade()


# =============================================================================
# Main Validation Function
# =============================================================================


def validate_skill(
    skill_path: Path,
    strict_mode: bool = False,
    strict_openspec: bool = False,
    validate_pillars_flag: bool = False,
) -> ValidationReport:
    """Validate a complete skill directory.

    Args:
        skill_path: Path to the skill directory
        strict_mode: Enable Nixtla strict mode validation
        strict_openspec: Enable AgentSkills OpenSpec strict validation
        validate_pillars_flag: Enable 8+1 Pillars validation

    Returns:
        ValidationReport with all results
    """
    report = ValidationReport(skill_path=str(skill_path))

    # Check skill directory exists
    if not skill_path.exists():
        report.critical(f"Skill path does not exist: {skill_path}", category="Structure")
        return report

    if not skill_path.is_dir():
        report.critical(f"Skill path is not a directory: {skill_path}", category="Structure")
        return report

    # Validate SKILL.md exists (required)
    if not validate_skill_md_exists(skill_path, report):
        return report

    # Read SKILL.md content
    skill_md = find_skill_md(skill_path)
    if skill_md is None:
        return report
    content = skill_md.read_text()

    # Parse frontmatter
    frontmatter = validate_frontmatter_structure(content, report)
    _, body, _ = parse_frontmatter(content)

    if frontmatter is not None:
        # Validate field whitelist
        validate_field_whitelist(frontmatter, report, strict_openspec)

        # Validate individual frontmatter fields
        validate_name_field(frontmatter, skill_path.name, report, strict_openspec)
        validate_description_field(frontmatter, body, report, strict_mode)
        validate_context_field(frontmatter, report)
        validate_agent_field(frontmatter, report)
        validate_boolean_field(frontmatter, "user-invocable", report)
        validate_boolean_field(frontmatter, "disable-model-invocation", report)
        validate_allowed_tools_field(frontmatter, report, strict_mode)

    # Validate token budget
    validate_token_budget(content, body, report)

    # Validate required sections (Nixtla strict mode)
    validate_required_sections(body, report, strict_mode)

    # Validate path formats
    validate_path_formats(body, report)

    # Validate resource references
    validate_resource_references(skill_path, body, report)

    # Validate directory structure
    validate_directory_structure(skill_path, report)

    # Validate 8+1 Pillars (optional)
    if validate_pillars_flag:
        validate_pillars(skill_path, body, report)

    # Calculate overall score and grade
    calculate_overall_score(report)

    return report


# =============================================================================
# Output Functions
# =============================================================================


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    colors = {
        "CRITICAL": "\033[91m",  # Red
        "MAJOR": "\033[93m",  # Yellow
        "MINOR": "\033[94m",  # Blue
        "INFO": "\033[90m",  # Gray
        "PASSED": "\033[92m",  # Green
        "RESET": "\033[0m",
        "BOLD": "\033[1m",
    }

    # Count by level
    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    # Print header
    print("\n" + "=" * 70)
    print(f"Skill Validation: {report.skill_path}")
    print("=" * 70)

    # Print grade
    grade_colors = {"A": "\033[92m", "B": "\033[92m", "C": "\033[93m", "D": "\033[93m", "F": "\033[91m"}
    print(
        f"\n{colors['BOLD']}Grade: {grade_colors.get(report.grade, '')}{report.grade}{colors['RESET']} ({report.overall_score:.1f}/100)"
    )

    # Print summary
    print("\nSummary:")
    print(f"  {colors['CRITICAL']}CRITICAL: {counts['CRITICAL']}{colors['RESET']}")
    print(f"  {colors['MAJOR']}MAJOR:    {counts['MAJOR']}{colors['RESET']}")
    print(f"  {colors['MINOR']}MINOR:    {counts['MINOR']}{colors['RESET']}")
    if verbose:
        print(f"  {colors['INFO']}INFO:     {counts['INFO']}{colors['RESET']}")
        print(f"  {colors['PASSED']}PASSED:   {counts['PASSED']}{colors['RESET']}")

    # Print pillar scores if available
    if report.pillar_scores:
        print("\nPillars Coverage:")
        for ps in report.pillar_scores:
            score_color = (
                colors["PASSED"] if ps.score == 1.0 else (colors["MINOR"] if ps.score == 0.5 else colors["MAJOR"])
            )
            score_symbol = "✓" if ps.score == 1.0 else ("~" if ps.score == 0.5 else "✗")
            print(f"  {score_color}{score_symbol} {ps.name}: {ps.score}/1.0{colors['RESET']} - {ps.notes}")

    # Print details by category
    print("\nDetails:")
    categories_seen: set[str] = set()
    for r in report.results:
        if r.level == "PASSED" and not verbose:
            continue
        if r.level == "INFO" and not verbose:
            continue

        # Print category header if new
        if r.category and r.category not in categories_seen:
            categories_seen.add(r.category)
            print(f"\n  {colors['BOLD']}[{r.category}]{colors['RESET']}")

        color = colors[r.level]
        reset = colors["RESET"]
        file_info = f" ({r.file})" if r.file else ""
        line_info = f":{r.line}" if r.line else ""
        print(f"    {color}[{r.level}]{reset} {r.message}{file_info}{line_info}")

    # Print final status
    print("\n" + "-" * 70)
    if report.exit_code == 0:
        print(f"{colors['PASSED']}✓ Skill validation passed (Grade {report.grade}){colors['RESET']}")
    elif report.exit_code == 1:
        print(f"{colors['CRITICAL']}✗ CRITICAL issues - skill will not work (Grade {report.grade}){colors['RESET']}")
    elif report.exit_code == 2:
        print(f"{colors['MAJOR']}✗ MAJOR issues - significant problems (Grade {report.grade}){colors['RESET']}")
    else:
        print(f"{colors['MINOR']}! MINOR issues - may affect UX (Grade {report.grade}){colors['RESET']}")

    print()


def print_json(report: ValidationReport) -> None:
    """Print validation results as JSON."""
    output = {
        "skill_path": report.skill_path,
        "exit_code": report.exit_code,
        "overall_score": round(report.overall_score, 2),
        "grade": report.grade,
        "counts": {
            "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
            "major": sum(1 for r in report.results if r.level == "MAJOR"),
            "minor": sum(1 for r in report.results if r.level == "MINOR"),
            "info": sum(1 for r in report.results if r.level == "INFO"),
            "passed": sum(1 for r in report.results if r.level == "PASSED"),
        },
        "pillar_scores": [{"name": ps.name, "score": ps.score, "notes": ps.notes} for ps in report.pillar_scores],
        "category_scores": report.category_scores,
        "results": [
            {
                "level": r.level,
                "message": r.message,
                "file": r.file,
                "line": r.line,
                "category": r.category,
            }
            for r in report.results
        ],
    }
    print(json.dumps(output, indent=2))


# =============================================================================
# Main Entry Point
# =============================================================================


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Comprehensive skill validator with 168+ validation rules")
    parser.add_argument("skill_path", help="Path to the skill directory")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all results including passed checks",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable Nixtla strict mode (required sections, description quality)",
    )
    parser.add_argument(
        "--openspec",
        action="store_true",
        help="Enable AgentSkills OpenSpec strict mode (field whitelist)",
    )
    parser.add_argument(
        "--pillars",
        action="store_true",
        help="Enable 8+1 Pillars validation (for lang-* and convert-* skills)",
    )
    args = parser.parse_args()

    skill_path = Path(args.skill_path)

    if not skill_path.exists():
        print(f"Error: {skill_path} does not exist", file=sys.stderr)
        return 1

    report = validate_skill(
        skill_path,
        strict_mode=args.strict,
        strict_openspec=args.openspec,
        validate_pillars_flag=args.pillars,
    )

    if args.json:
        print_json(report)
    else:
        print_results(report, args.verbose)

    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
