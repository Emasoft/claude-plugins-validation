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

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

# =============================================================================
# Type Definitions
# =============================================================================

# Validation result severity levels (uppercase for consistency)
Level = Literal["CRITICAL", "MAJOR", "MINOR", "INFO", "PASSED"]

# =============================================================================
# Exit Codes
# =============================================================================

EXIT_OK = 0  # All checks passed (or only INFO/PASSED)
EXIT_CRITICAL = 1  # CRITICAL issues found
EXIT_MAJOR = 2  # MAJOR issues found
EXIT_MINOR = 3  # MINOR issues found

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

# Severity ranges for categorization
SEVERITY_LOW = (SEVERITY_L1, SEVERITY_L2, SEVERITY_L3)  # confidence > 0.7
SEVERITY_MEDIUM = (SEVERITY_L4, SEVERITY_L5, SEVERITY_L6)  # confidence > 0.85
SEVERITY_HIGH = (SEVERITY_L7, SEVERITY_L8, SEVERITY_L9)  # confidence > 0.95
SEVERITY_CRITICAL = (SEVERITY_L10,)  # confidence > 0.99

# Confidence thresholds for each severity range
CONFIDENCE_THRESHOLDS = {
    "LOW": 0.7,
    "MEDIUM": 0.85,
    "HIGH": 0.95,
    "CRITICAL": 0.99,
}


def severity_to_level(severity: int) -> Level:
    """Convert L1-L10 severity to standard Level.

    Args:
        severity: Numeric severity (1-10)

    Returns:
        Corresponding Level (CRITICAL, MAJOR, MINOR, INFO)
    """
    if severity >= SEVERITY_L10:
        return "CRITICAL"
    elif severity >= SEVERITY_L7:
        return "MAJOR"
    elif severity >= SEVERITY_L4:
        return "MINOR"
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
        "INFO": SEVERITY_L2,
        "PASSED": SEVERITY_L1,
    }
    return mapping.get(level, SEVERITY_L1)


# =============================================================================
# Multi-Layer Validation Phase Constants
# =============================================================================

# Validation phases for multi-layer validation
PHASE_STRUCTURE = "structure"  # File/directory structure checks
PHASE_SEMANTIC = "semantic"  # Content and meaning validation
PHASE_SECURITY = "security"  # Security-related checks
PHASE_CROSS_REF = "cross-reference"  # Cross-file reference validation

# All phases in execution order
VALIDATION_PHASES = [
    PHASE_STRUCTURE,
    PHASE_SEMANTIC,
    PHASE_SECURITY,
    PHASE_CROSS_REF,
]

# =============================================================================
# Hook Event Types
# =============================================================================

# All valid hook event types in Claude Code
VALID_HOOK_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "UserPromptSubmit",
    "Notification",
    "Stop",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PreToolResponse",  # Less common but valid
}

# Events that require a matcher pattern (tool-specific events)
EVENTS_REQUIRING_MATCHER = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "SessionStart",
    "PreCompact",
}

# Events that do not support matchers (global events)
EVENTS_NO_MATCHER = {
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "SessionEnd",
}

# =============================================================================
# Common Constants
# =============================================================================

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
    "Task",
    "NotebookEdit",
    "AskFollowupQuestion",
    "AttemptCompletion",
    "ListFiles",
    "SearchFiles",
    "ListCodeDefinitionNames",
    "Browser",
    "MCP",
    "Computer",
    "TextEditor",
    "UrlScreenshot",
}

# Valid model values for agents
VALID_MODELS = {"haiku", "sonnet", "opus", "inherit"}

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
}

# =============================================================================
# Security Patterns
# =============================================================================

# Patterns that indicate potential secrets/credentials
# Note: Generic API Key pattern excludes env var placeholders like ${VAR} or $VAR
SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
    (re.compile(r"-----BEGIN (RSA |DSA |EC |OPENSSH )?PRIVATE KEY-----"), "Private Key"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "GitHub Personal Access Token"),
    (re.compile(r"sk-[a-zA-Z0-9]{48}"), "OpenAI API Key"),
    (re.compile(r"xox[baprs]-[0-9a-zA-Z-]+"), "Slack Token"),
    # Generic API key pattern excludes environment variable placeholders (${VAR} or $VAR)
    (re.compile(r"api[_-]?key['\"]?\s*[:=]\s*['\"](?!\$[\{A-Z_])[^'\"]{20,}['\"]", re.I), "Generic API Key"),
]

# Patterns for hardcoded user paths (should use ${CLAUDE_PLUGIN_ROOT} instead)
USER_PATH_PATTERNS = [
    re.compile(r"/Users/[^/\s]+/"),
    re.compile(r"C:\\Users\\[^\\\s]+\\"),
    re.compile(r"/home/[^/\s]+/"),
]

# Files that should never be in a plugin
DANGEROUS_FILES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    "credentials.json",
    "secrets.json",
    "config.secret.json",
    "private.key",
    "id_rsa",
    "id_ed25519",
}

# =============================================================================
# Validation Name Patterns
# =============================================================================

# Name validation pattern (kebab-case)
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Maximum recommended values for names and descriptions
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MIN_BODY_CHARS = 100
MAX_BODY_WORDS = 2000

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
    """

    level: Level
    message: str
    file: str | None = None
    line: int | None = None
    phase: str | None = None
    fixable: bool = False
    fix_id: str | None = None

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
    def exit_code(self) -> int:
        """Get appropriate exit code based on highest severity issue."""
        if self.has_critical:
            return EXIT_CRITICAL
        if self.has_major:
            return EXIT_MAJOR
        if self.has_minor:
            return EXIT_MINOR
        return EXIT_OK

    @property
    def score(self) -> int:
        """Calculate health score (0-100) based on validation results.

        Scoring:
        - Start at 100
        - Deduct 25 for each CRITICAL
        - Deduct 10 for each MAJOR
        - Deduct 3 for each MINOR
        - INFO and PASSED don't affect score
        """
        score = 100
        for r in self.results:
            if r.level == "CRITICAL":
                score -= 25
            elif r.level == "MAJOR":
                score -= 10
            elif r.level == "MINOR":
                score -= 3
        return max(0, score)

    def count_by_level(self) -> dict[str, int]:
        """Get count of results by level."""
        counts: dict[str, int] = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "INFO": 0, "PASSED": 0}
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
            "grade": calculate_letter_grade(self.score),
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


def calculate_letter_grade(score: int) -> str:
    """Convert numeric score (0-100) to letter grade.

    Grade scale:
    - A+ : 97-100
    - A  : 93-96
    - A- : 90-92
    - B+ : 87-89
    - B  : 83-86
    - B- : 80-82
    - C+ : 77-79
    - C  : 73-76
    - C- : 70-72
    - D  : 60-69
    - F  : 0-59
    """
    if score >= 97:
        return "A+"
    elif score >= 93:
        return "A"
    elif score >= 90:
        return "A-"
    elif score >= 87:
        return "B+"
    elif score >= 83:
        return "B"
    elif score >= 80:
        return "B-"
    elif score >= 77:
        return "C+"
    elif score >= 73:
        return "C"
    elif score >= 70:
        return "C-"
    elif score >= 60:
        return "D"
    else:
        return "F"


def is_valid_kebab_case(name: str) -> bool:
    """Check if name follows kebab-case convention."""
    return bool(NAME_PATTERN.match(name))


# =============================================================================
# Color Formatting (for terminal output)
# =============================================================================

# ANSI color codes
COLORS = {
    "CRITICAL": "\033[91m",  # Red
    "MAJOR": "\033[93m",  # Yellow
    "MAJOR_DARK": "\033[33m",  # Dark Yellow
    "MINOR": "\033[94m",  # Blue
    "INFO": "\033[90m",  # Gray
    "PASSED": "\033[92m",  # Green
    "RESET": "\033[0m",  # Reset
    "BOLD": "\033[1m",  # Bold
    "DIM": "\033[2m",  # Dim
}


def colorize(text: str, level: str) -> str:
    """Apply color to text based on level."""
    color = COLORS.get(level, "")
    return f"{color}{text}{COLORS['RESET']}"


def format_result(result: ValidationResult, show_file: bool = True) -> str:
    """Format a single validation result for terminal output."""
    color = COLORS.get(result.level, "")
    reset = COLORS["RESET"]

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
    grade = calculate_letter_grade(score)

    print(f"\n{'=' * 60}")
    print(f"{COLORS['BOLD']}{title}{COLORS['RESET']}")
    print(f"{'=' * 60}")

    # Print counts by level
    print(f"\n{COLORS['CRITICAL']}CRITICAL: {counts['CRITICAL']}{COLORS['RESET']}")
    print(f"{COLORS['MAJOR']}MAJOR:    {counts['MAJOR']}{COLORS['RESET']}")
    print(f"{COLORS['MINOR']}MINOR:    {counts['MINOR']}{COLORS['RESET']}")
    print(f"{COLORS['INFO']}INFO:     {counts['INFO']}{COLORS['RESET']}")
    print(f"{COLORS['PASSED']}PASSED:   {counts['PASSED']}{COLORS['RESET']}")

    # Print score and grade
    grade_color = COLORS["PASSED"] if score >= 80 else COLORS["MAJOR"] if score >= 60 else COLORS["CRITICAL"]
    print(
        f"\n{COLORS['BOLD']}Health Score:{COLORS['RESET']} {grade_color}{score}/100 (Grade: {grade}){COLORS['RESET']}"
    )

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
        "INFO": [],
        "PASSED": [],
    }

    for result in report.results:
        by_level[result.level].append(result)

    # Print each level
    for level in ["CRITICAL", "MAJOR", "MINOR"]:
        results = by_level[level]
        if results:
            print(f"\n{COLORS[level]}--- {level} ISSUES ({len(results)}) ---{COLORS['RESET']}")
            for result in results:
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
    if upper in ("CRITICAL", "MAJOR", "MINOR", "INFO", "PASSED"):
        return upper  # type: ignore
    # Default to INFO for unknown levels
    return "INFO"
