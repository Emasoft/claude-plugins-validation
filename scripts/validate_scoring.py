#!/usr/bin/env python3
"""
Claude Plugins Validation - Quality Scoring Module

Aggregates validation results from all validators and computes quality scores.
Provides category scores (0-10 scale) and overall quality score (0-100).

Usage:
    uv run python scripts/validate_scoring.py /path/to/plugin
    uv run python scripts/validate_scoring.py /path/to/plugin --verbose
    uv run python scripts/validate_scoring.py /path/to/plugin --json

Exit codes (standard severity-based convention):
    0 - PASS: No issues found
    1 - CRITICAL: Critical issues found
    2 - MAJOR: Major issues found (no critical)
    3 - MINOR: Minor issues only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Parallel-scan harness (task #384). The scoring validator is the heaviest
# composite validator (it runs every other validator end-to-end) so the
# per-task parallelism win is the biggest of the family. See
# ``_build_scoring_work_units`` for the work-unit design.
from cpv_parallel_runner import parallel_scan

# Import shared validation infrastructure
from cpv_validation_common import (
    COLORS,
    EXIT_CRITICAL,
    EXIT_MAJOR,
    EXIT_MINOR,
    EXIT_OK,
    ValidationReport,
    ValidationResult,
)

# Import all validators.
# The dedicated per-element validators are imported under their own names
# (validate_agent / validate_command / validate_hooks / validate_plugin_mcp /
# validate_skill) and own all per-element findings. The plugin "bundle" unit
# runs ONLY the plugin-LEVEL checks below (manifest / structure / scripts /
# readme / license) — it deliberately does NOT re-import validate_plugin's
# per-element wrappers, which would double-count every agent/skill/command/
# hook/mcp finding (see scan_one_scoring_unit's kind=="plugin" branch).
from validate_agent import validate_agent
from validate_command import validate_command
from validate_hook import validate_hooks
from validate_mcp import validate_plugin_mcp
from validate_plugin import (
    validate_license,
    validate_manifest,
    validate_readme,
    validate_scripts,
    validate_structure,
)
from validate_security import validate_security
from validate_skill import validate_skill

# =============================================================================
# Scoring Constants
# =============================================================================

# Category minimum thresholds (0-10 scale)
CATEGORY_THRESHOLDS = {
    "schema_compliance": 8,  # Minimum 8/10 - Required for proper functioning
    "security": 8,  # Minimum 8/10 - CRITICAL security requirements
    "matcher_validity": 7,  # Minimum 7/10 - Hook matchers must work
    "script_existence": 7,  # Minimum 7/10 - Scripts must exist and be executable
    "hook_types": 9,  # Minimum 9/10 - Hook types must be valid
    "documentation": 5,  # Minimum 5/10 - Documentation is helpful but not critical
    "maintainability": 6,  # Minimum 6/10 - Code should be maintainable
}

# Category weights for overall score calculation
CATEGORY_WEIGHTS = {
    "schema_compliance": 0.20,  # 20% of overall score
    "security": 0.25,  # 25% - security is most important
    "matcher_validity": 0.15,  # 15%
    "script_existence": 0.15,  # 15%
    "hook_types": 0.10,  # 10%
    "documentation": 0.08,  # 8%
    "maintainability": 0.07,  # 7%
}

# Rating descriptors
RATING_DESCRIPTIONS = {
    "9-10": "Excellent - Ready for production",
    "7-8": "Good - Minor improvements recommended",
    "5-6": "Fair - Significant improvements needed",
    "0-4": "Poor - Major revision required",
}

EXIT_PASS = EXIT_OK  # Alias for scoring module backward compat


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class CategoryScore:
    """Score for a single validation category.

    Attributes:
        name: Category name (e.g., "schema_compliance", "security")
        score: Numeric score (0-10 scale)
        threshold: Minimum required score
        passed: Whether the category meets its threshold
        issues_critical: Count of critical issues in this category
        issues_major: Count of major issues in this category
        issues_minor: Count of minor issues in this category
        issues_passed: Count of passed checks in this category
        rating: Rating descriptor ("Excellent", "Good", "Fair", "Poor")
        recommendations: List of improvement recommendations
    """

    name: str
    score: float
    threshold: int
    passed: bool
    issues_critical: int = 0
    issues_major: int = 0
    issues_minor: int = 0
    issues_passed: int = 0
    rating: str = ""
    recommendations: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Calculate rating based on score."""
        if self.score >= 9:
            self.rating = "Excellent"
        elif self.score >= 7:
            self.rating = "Good"
        elif self.score >= 5:
            self.rating = "Fair"
        else:
            self.rating = "Poor"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "score": round(self.score, 2),
            "threshold": self.threshold,
            "passed": self.passed,
            "rating": self.rating,
            "issues": {
                "critical": self.issues_critical,
                "major": self.issues_major,
                "minor": self.issues_minor,
                "passed": self.issues_passed,
            },
            "recommendations": self.recommendations,
        }


@dataclass
class QualityScoreReport:
    """Complete quality score report with category breakdown.

    Attributes:
        plugin_path: Path to the validated plugin
        overall_score: Overall quality score (0-100)
        status: Overall status (PASS, CONDITIONAL_PASS, FAIL)
        category_scores: Individual category scores
        critical_failures: List of critical failures that cause automatic fail
        recommendations: Prioritized list of improvement recommendations
        validator_reports: Raw reports from each validator
    """

    plugin_path: str
    overall_score: float = 0.0
    status: str = "FAIL"
    category_scores: dict[str, CategoryScore] = field(default_factory=dict)
    critical_failures: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    validator_reports: dict[str, ValidationReport] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "plugin_path": self.plugin_path,
            "overall_score": round(self.overall_score, 2),
            "status": self.status,
            "category_scores": {name: cat.to_dict() for name, cat in self.category_scores.items()},
            "critical_failures": self.critical_failures,
            "recommendations": self.recommendations,
            "validator_summaries": {
                name: {
                    "score": report.score,
                    "critical": report.count_by_level().get("CRITICAL", 0),
                    "major": report.count_by_level().get("MAJOR", 0),
                    "minor": report.count_by_level().get("MINOR", 0),
                }
                for name, report in self.validator_reports.items()
            },
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


# =============================================================================
# Category Scoring Functions
# =============================================================================


def calculate_category_score(
    results: list[ValidationResult],
    max_score: float = 10.0,
) -> tuple[float, int, int, int, int]:
    """Calculate score for a category based on its validation results.

    Scoring formula:
    - Start at max_score (10)
    - Deduct 3 points for each CRITICAL issue
    - Deduct 1.5 points for each MAJOR issue
    - Deduct 0.5 points for each MINOR issue
    - Minimum score is 0

    Args:
        results: List of validation results for this category
        max_score: Maximum possible score (default 10)

    Returns:
        Tuple of (score, critical_count, major_count, minor_count, passed_count)
    """
    score = max_score
    critical_count = 0
    major_count = 0
    minor_count = 0
    passed_count = 0

    for result in results:
        if result.level == "CRITICAL":
            score -= 3.0
            critical_count += 1
        elif result.level == "MAJOR":
            score -= 1.5
            major_count += 1
        elif result.level == "MINOR":
            score -= 0.5
            minor_count += 1
        elif result.level == "PASSED":
            passed_count += 1

    return max(0.0, score), critical_count, major_count, minor_count, passed_count


def categorize_results(
    reports: dict[str, ValidationReport],
) -> dict[str, list[ValidationResult]]:
    """Categorize validation results into scoring categories.

    Maps validator results to scoring categories based on the nature of each check.

    Args:
        reports: Dictionary of validator name -> ValidationReport

    Returns:
        Dictionary of category name -> list of ValidationResults
    """
    categories: dict[str, list[ValidationResult]] = {
        "schema_compliance": [],
        "security": [],
        "matcher_validity": [],
        "script_existence": [],
        "hook_types": [],
        "documentation": [],
        "maintainability": [],
    }

    # Helper to categorize based on message content
    def categorize_result(result: ValidationResult, validator_name: str) -> None:
        msg_lower = result.message.lower()

        # Security category - from security validator or security-related messages
        if validator_name == "security" or any(
            keyword in msg_lower
            for keyword in ["security", "secret", "credential", "injection", "traversal", "dangerous", "unsafe"]
        ):
            categories["security"].append(result)

        # Schema compliance - manifest, JSON, required fields
        elif any(
            keyword in msg_lower
            for keyword in ["json", "manifest", "plugin.json", "required field", "schema", "kebab-case", "name must"]
        ):
            categories["schema_compliance"].append(result)

        # Matcher validity - hook matchers
        elif any(keyword in msg_lower for keyword in ["matcher", "regex", "pattern invalid", "tool name", "wildcard"]):
            categories["matcher_validity"].append(result)

        # Script existence - scripts, executables
        elif any(
            keyword in msg_lower
            for keyword in ["script", "executable", "shebang", "chmod", "file not found", "command not found"]
        ):
            categories["script_existence"].append(result)

        # Hook types - hook configuration
        elif any(
            keyword in msg_lower
            for keyword in ["hook type", "event type", "pretooluse", "posttooluse", "stop", "sessionstart"]
        ):
            categories["hook_types"].append(result)

        # Documentation - README, descriptions, comments
        elif any(keyword in msg_lower for keyword in ["readme", "description", "documentation", "missing docstring"]):
            categories["documentation"].append(result)

        # Maintainability - code quality, structure
        elif any(
            keyword in msg_lower
            for keyword in ["version", "structure", "duplicate", "unused", "deprecated", "lint", "format"]
        ):
            categories["maintainability"].append(result)

        # Default to schema_compliance if no specific category matches
        else:
            categories["schema_compliance"].append(result)

    # Process all results from all validators
    for validator_name, report in reports.items():
        for result in report.results:
            categorize_result(result, validator_name)

    return categories


def generate_recommendations(category_scores: dict[str, CategoryScore]) -> list[str]:
    """Generate prioritized recommendations based on category scores.

    Recommendations are ordered by:
    1. Critical failures (must fix)
    2. Categories below threshold
    3. Categories with room for improvement

    Args:
        category_scores: Dictionary of category name -> CategoryScore

    Returns:
        List of recommendation strings, ordered by priority
    """
    recommendations: list[str] = []

    # Priority 1: Categories with critical issues
    for name, cat in category_scores.items():
        if cat.issues_critical > 0:
            recommendations.append(
                f"[CRITICAL] {name.replace('_', ' ').title()}: Fix {cat.issues_critical} critical issue(s) immediately"
            )

    # Priority 2: Categories below threshold
    for name, cat in category_scores.items():
        if not cat.passed and cat.issues_critical == 0:
            gap = cat.threshold - cat.score
            recommendations.append(
                f"[REQUIRED] {name.replace('_', ' ').title()}: "
                f"Score {cat.score:.1f}/10 is below minimum {cat.threshold}/10 "
                f"(need +{gap:.1f} points)"
            )

    # Priority 3: Categories with major issues
    for name, cat in category_scores.items():
        if cat.issues_major > 0 and cat.passed:
            recommendations.append(
                f"[RECOMMENDED] {name.replace('_', ' ').title()}: "
                f"Address {cat.issues_major} major issue(s) to improve quality"
            )

    # Priority 4: Categories with minor issues
    for name, cat in category_scores.items():
        if cat.issues_minor > 0 and cat.passed and cat.issues_major == 0:
            recommendations.append(
                f"[OPTIONAL] {name.replace('_', ' ').title()}: Consider fixing {cat.issues_minor} minor issue(s)"
            )

    return recommendations


# =============================================================================
# Task #384 — Parallel per-task dispatch via the shared parallel_scan harness
# =============================================================================
#
# The scoring validator's wall-clock cost is dominated by ``run_all_validators``,
# which sequentially runs ~9 independent top-level validators end-to-end
# (plugin manifest+structure batch, security, hooks, mcp) plus per-file loops
# over agents/, skills/, and commands/. Each top-level call is independent —
# none of them share mutable state — so the loop is embarrassingly parallel.
# The per-file inner loops are also independent (one file at a time), so we
# fan THOSE out too.
#
# Strategy: build a uniform ``_ScoringWorkUnit`` per logical task and dispatch
# them all to the shared ``parallel_scan`` harness. Each worker returns a
# ``(target_key, ValidationReport)`` tuple. The parent merges them back into
# the ``reports`` dict using the existing key convention ("plugin",
# "security", "hooks", "mcp", "agents", "skills", "commands"), so the public
# API of ``run_all_validators`` is bit-identical to the pre-task-384
# behavior (same dict keys, same merged result sequences, same exit code).
#
# Why a single uniform work-unit dispatch instead of one pool per task kind:
# (1) one queue lets the harness load-balance across all CPU cores regardless
# of task heterogeneity (security takes minutes, one agent.md takes
# milliseconds), (2) one merge loop is simpler than four, (3) the harness
# already preserves input order so we can rely on it for the "by-key"
# grouping below without re-sorting.
#
# The "plugin" task is special: it runs 9 validate_plugin.* calls that all
# mutate the SAME shared ``plugin_report``. Splitting them across workers
# would require an aggregator merge that reorders findings non-deterministically.
# Instead we keep all 9 calls INSIDE ONE worker (kind="plugin") so the
# resulting plugin_report is bit-identical to the serial path.
#
# Env-var escape hatch ``CPV_SCORING_PARALLEL=0`` forces the serial path
# (consistent with ``CPV_CACHE_PARALLEL`` / ``CPV_HOOK_PARALLEL`` /
# ``CPV_SECURITY_PARALLEL``).


@dataclass(frozen=True)
class _ScoringWorkUnit:
    """One per-task scoring unit plus its discriminator + context.

    Frozen + primitives-only so the unit pickles cleanly across the
    ProcessPoolExecutor worker boundary. Paths are passed as strings then
    reconstructed inside the worker — same defensive choice the cache
    validator's ``_CacheWorkUnit`` makes (dodges any Path-pickling quirks
    on Windows worker processes).

    Fields:
        kind: ``"plugin"`` | ``"security"`` | ``"hooks"`` | ``"mcp"``
            | ``"agent"`` | ``"skill"`` | ``"command"``. Selects which
            top-level validator the worker dispatches to.
        target_key: The dict key in the merged ``reports`` dict where this
            unit's result lands. ``"plugin"`` / ``"security"`` / ``"hooks"``
            / ``"mcp"`` map 1:1; ``"agent"`` units share key ``"agents"``,
            ``"skill"`` units share key ``"skills"``, ``"command"`` units
            share key ``"commands"``.
        plugin_root_str: Absolute plugin root (always set; some workers
            need it even when ``target_path_str`` points at a specific
            file).
        target_path_str: For per-file units (agent / skill / command), the
            absolute path to the target file or directory. Empty string for
            whole-plugin units (plugin / security / hooks / mcp).
    """

    kind: str
    target_key: str
    plugin_root_str: str
    target_path_str: str = ""


def scan_one_scoring_unit(
    unit: _ScoringWorkUnit,
) -> list[tuple[str, ValidationReport]]:
    """Top-level pickleable worker: run one scoring task and return its report.

    Dispatches on ``unit.kind`` to the matching validator. Returns a
    SINGLE-ELEMENT list ``[(target_key, ValidationReport)]`` — the
    one-element list satisfies the shared ``parallel_scan`` harness
    contract (``scan_func`` returns ``list``), and the parent unwraps it
    and merges the report into ``reports[target_key]``.

    Why wrap in a list instead of returning the tuple directly: the
    harness puts the worker's return value verbatim into
    ``ScanResult.findings``. The harness CONTRACT is that ``findings``
    is a list — other validators put their per-file finding lists in
    there directly. We follow the same shape (a list) but make the list
    a 1-element list carrying a tuple of (key, report). The parent's
    ``payload[0]`` unwrap is the symmetric move.

    For per-file units (agent/skill/command), the returned report holds
    findings for the ONE target file; the parent folds them into the
    shared ``reports["agents"]`` (resp. "skills", "commands") using
    ``ValidationReport.merge``.

    Worker-level exceptions are CAUGHT here and surfaced as a CRITICAL
    finding on the returned report (matching the pre-task-384 try/except
    blocks). This keeps the spec contract — "per-file worker exceptions
    never crash the validator" — while preserving the existing
    user-visible message format ("<Validator> validation failed: ...").
    """
    plugin_root = Path(unit.plugin_root_str)
    report = ValidationReport()

    try:
        if unit.kind == "plugin":
            # Plugin-LEVEL checks only — manifest / structure / scripts /
            # readme / license. validate_plugin's per-element wrappers
            # (validate_agents / validate_skills / validate_commands /
            # validate_hooks / validate_mcp) are NOT run here: they delegate to
            # the SAME comprehensive validators (validate_agent / validate_skill
            # / validate_command / validate_hooks / validate_plugin_mcp) that the
            # dedicated agent/skill/command/hooks/mcp work-units already invoke.
            # Running both would land every per-element finding in BOTH
            # reports["plugin"] and reports["agents"|"skills"|...], so
            # categorize_results would deduct each issue twice. Keep this set the
            # single source of truth for plugin-level findings; the per-element
            # units own the rest.
            _ = validate_manifest(plugin_root, report)
            validate_structure(plugin_root, report)
            validate_scripts(plugin_root, report)
            validate_readme(plugin_root, report)
            validate_license(plugin_root, report)
            return [(unit.target_key, report)]

        if unit.kind == "security":
            return [(unit.target_key, validate_security(plugin_root))]

        if unit.kind == "hooks":
            hooks_path = Path(unit.target_path_str)
            return [(unit.target_key, validate_hooks(hooks_path, plugin_root))]

        if unit.kind == "mcp":
            return [(unit.target_key, validate_plugin_mcp(plugin_root))]

        if unit.kind == "agent":
            agent_file = Path(unit.target_path_str)
            return [(unit.target_key, validate_agent(agent_file))]

        if unit.kind == "skill":
            skill_dir = Path(unit.target_path_str)
            return [(unit.target_key, validate_skill(skill_dir))]

        if unit.kind == "command":
            cmd_file = Path(unit.target_path_str)
            return [(unit.target_key, validate_command(cmd_file))]

    except Exception as e:
        # Match the legacy per-validator try/except message format so
        # downstream consumers (categorize_results, recommendations) see
        # the exact same string they did pre-parallelism.
        report.critical(_legacy_error_message(unit.kind, unit.target_path_str, e))
        return [(unit.target_key, report)]

    # Unknown kind — return empty report rather than raising. Mirrors
    # validate_cache.scan_one_cache_unit's defense against enum drift.
    return [(unit.target_key, report)]


def _legacy_error_message(kind: str, target_path_str: str, exc: BaseException) -> str:
    """Reproduce the legacy try/except error-message format for each kind.

    Pre-task-384 the per-validator try/except blocks raised messages like:
        "Plugin validation failed: <exc>"            (kind="plugin")
        "Security validation failed: <exc>"          (kind="security")
        "Hook validation failed: <exc>"              (kind="hooks")
        "MCP validation failed: <exc>"               (kind="mcp")
        "Agent validation failed for x.md: <exc>"    (kind="agent")
        "Skill validation failed for foo: <exc>"     (kind="skill")
        "Command validation failed for c.md: <exc>"  (kind="command")
    Preserve that exact wording so categorize_results / recommendation
    generation stays bit-identical with the serial path. Returns the
    full assembled message; the caller passes it straight to
    ``report.critical``.
    """
    if kind == "plugin":
        return f"Plugin validation failed: {exc}"
    if kind == "security":
        return f"Security validation failed: {exc}"
    if kind == "hooks":
        return f"Hook validation failed: {exc}"
    if kind == "mcp":
        return f"MCP validation failed: {exc}"
    if kind == "agent":
        return f"Agent validation failed for {Path(target_path_str).name}: {exc}"
    if kind == "skill":
        return f"Skill validation failed for {Path(target_path_str).name}: {exc}"
    if kind == "command":
        return f"Command validation failed for {Path(target_path_str).name}: {exc}"
    return f"{kind.title()} validation failed: {exc}"


def _scoring_parallel_enabled() -> bool:
    """Read the ``CPV_SCORING_PARALLEL`` env-var.

    Returns False when set to ``"0"`` / ``"false"`` / ``"no"`` / ``"off"``
    (case-insensitive) — the serial path is taken. Any other value, or no
    value at all, returns True (default = parallel). Mirrors the parsing
    in ``_cache_parallel_enabled`` for cross-validator consistency.
    """
    val = os.environ.get("CPV_SCORING_PARALLEL")
    if val is None:
        return True
    return val.strip().lower() not in {"0", "false", "no", "off"}


def _build_scoring_work_units(plugin_path: Path) -> list[_ScoringWorkUnit]:
    """Enumerate every scoring task as a uniform work unit.

    Order matches the legacy ``run_all_validators`` traversal EXACTLY
    (plugin → security → hooks → mcp → agents → skills → commands) so the
    merged ``reports`` dict has the same key-insertion order it did pre-
    task-384. Per-file units within agents/skills/commands are emitted in
    the same glob/iterdir order the serial loop visited them (alphabetical
    on macOS+Linux for ``.glob("*.md")`` and ``.iterdir()`` on most
    filesystems — sorted explicitly to remove any FS-dependent jitter).
    """
    units: list[_ScoringWorkUnit] = []
    plugin_root_str = str(plugin_path)

    # 1. plugin — one bundled unit (9 validate_plugin.* calls share one report).
    units.append(
        _ScoringWorkUnit(
            kind="plugin",
            target_key="plugin",
            plugin_root_str=plugin_root_str,
        )
    )

    # 2. security — single heavy unit.
    units.append(
        _ScoringWorkUnit(
            kind="security",
            target_key="security",
            plugin_root_str=plugin_root_str,
        )
    )

    # 3. hooks (if hooks.json exists)
    hooks_path = plugin_path / "hooks" / "hooks.json"
    if hooks_path.exists():
        units.append(
            _ScoringWorkUnit(
                kind="hooks",
                target_key="hooks",
                plugin_root_str=plugin_root_str,
                target_path_str=str(hooks_path),
            )
        )

    # 4. mcp (if .mcp.json exists)
    mcp_path = plugin_path / ".mcp.json"
    if mcp_path.exists():
        units.append(
            _ScoringWorkUnit(
                kind="mcp",
                target_key="mcp",
                plugin_root_str=plugin_root_str,
            )
        )

    # 5. agents — one unit per agent .md file. Sorted to remove FS-order jitter.
    agents_dir = plugin_path / "agents"
    if agents_dir.exists():
        for agent_file in sorted(agents_dir.glob("*.md")):
            units.append(
                _ScoringWorkUnit(
                    kind="agent",
                    target_key="agents",
                    plugin_root_str=plugin_root_str,
                    target_path_str=str(agent_file),
                )
            )

    # 6. skills — one unit per skill directory. Skip hidden dirs (matching
    # the legacy loop's startswith(".") guard). Sorted by name.
    skills_dir = plugin_path / "skills"
    if skills_dir.exists():
        skill_dirs = sorted(
            d for d in skills_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
        for skill_dir in skill_dirs:
            units.append(
                _ScoringWorkUnit(
                    kind="skill",
                    target_key="skills",
                    plugin_root_str=plugin_root_str,
                    target_path_str=str(skill_dir),
                )
            )

    # 7. commands — one unit per command .md file.
    commands_dir = plugin_path / "commands"
    if commands_dir.exists():
        for cmd_file in sorted(commands_dir.glob("*.md")):
            units.append(
                _ScoringWorkUnit(
                    kind="command",
                    target_key="commands",
                    plugin_root_str=plugin_root_str,
                    target_path_str=str(cmd_file),
                )
            )

    return units


# =============================================================================
# Main Scoring Function
# =============================================================================


def run_all_validators(plugin_path: Path) -> dict[str, ValidationReport]:
    """Run all validators and collect their reports.

    Two execution paths:
      * Serial path (when ``CPV_SCORING_PARALLEL=0`` is set) — runs the
        legacy per-task loop bit-identically to the pre-task-384 behavior.
        Used by the parity regression test and by anyone debugging a
        parallel-path regression.
      * Parallel path (default) — enumerates every scoring task as a
        uniform ``_ScoringWorkUnit``, dispatches them across a
        ``ProcessPoolExecutor`` via the shared ``parallel_scan`` harness,
        then merges per-task reports back into the dict using the same
        ``"plugin"`` / ``"security"`` / ``"hooks"`` / ``"mcp"`` /
        ``"agents"`` / ``"skills"`` / ``"commands"`` keys the serial path
        produced. Per-file agent/skill/command units share the same dict
        key, so the parent loops over results in input order and merges
        them via ``ValidationReport.merge`` to preserve the legacy per-file
        finding sequence.

    Args:
        plugin_path: Path to the plugin directory

    Returns:
        Dictionary of validator name -> ValidationReport
    """
    if not _scoring_parallel_enabled():
        # Serial path — kept BIT-IDENTICAL to the pre-task-384 behavior.
        # Used by the parity regression test (which runs both paths and
        # asserts the per-key ValidationResult sequences match exactly).
        return _run_all_validators_serial(plugin_path)

    return _run_all_validators_parallel(plugin_path)


def _run_all_validators_serial(plugin_path: Path) -> dict[str, ValidationReport]:
    """Legacy serial implementation — preserved bit-identically.

    The body matches the pre-task-384 ``run_all_validators`` exactly so
    parity tests can assert serial-vs-parallel equivalence. Kept as a
    private helper so we don't have to dig through git history when
    debugging a parallel-path regression.
    """
    reports: dict[str, ValidationReport] = {}

    # Run plugin validator (main manifest and structure)
    # Uses multiple functions from validate_plugin.py
    # Note: validate_plugin uses its own ValidationReport class with compatible interface
    try:
        # Plugin-LEVEL checks only (see scan_one_scoring_unit's kind=="plugin"
        # branch). The per-element validators are intentionally NOT called here
        # — the dedicated agent/skill/command/hooks/mcp work-units below own
        # those findings. Running both double-counted every per-element issue.
        plugin_report = ValidationReport()
        _ = validate_manifest(plugin_path, plugin_report)
        validate_structure(plugin_path, plugin_report)
        validate_scripts(plugin_path, plugin_report)
        validate_readme(plugin_path, plugin_report)
        validate_license(plugin_path, plugin_report)
        reports["plugin"] = plugin_report
    except Exception as e:
        error_report = ValidationReport()
        error_report.critical(f"Plugin validation failed: {e}")
        reports["plugin"] = error_report

    # Run security validator (comprehensive security scan)
    try:
        security_report = validate_security(plugin_path)
        reports["security"] = security_report
    except Exception as e:
        error_report = ValidationReport()
        error_report.critical(f"Security validation failed: {e}")
        reports["security"] = error_report

    # Run hook validator if hooks.json exists (detailed hook validation)
    # Note: validate_hooks returns its own ValidationReport with compatible interface
    hooks_path = plugin_path / "hooks" / "hooks.json"
    if hooks_path.exists():
        try:
            hook_report = validate_hooks(hooks_path, plugin_path)
            reports["hooks"] = hook_report
        except Exception as e:
            error_report = ValidationReport()
            error_report.critical(f"Hook validation failed: {e}")
            reports["hooks"] = error_report

    # Run MCP validator if .mcp.json exists
    # Note: validate_plugin_mcp returns its own ValidationReport with compatible interface
    mcp_path = plugin_path / ".mcp.json"
    if mcp_path.exists():
        try:
            mcp_report = validate_plugin_mcp(plugin_path)
            reports["mcp"] = mcp_report
        except Exception as e:
            error_report = ValidationReport()
            error_report.critical(f"MCP validation failed: {e}")
            reports["mcp"] = error_report

    # Run detailed agent validator for each agent file
    # NOTE: sorted() here (and in skills/commands below) to match
    # ``_build_scoring_work_units`` so serial-vs-parallel parity tests
    # see identical per-file order regardless of filesystem traversal
    # quirks.
    agents_dir = plugin_path / "agents"
    if agents_dir.exists():
        agent_report = ValidationReport()
        for agent_file in sorted(agents_dir.glob("*.md")):
            try:
                agent_single_report = validate_agent(agent_file)
                agent_report.merge(agent_single_report)
            except Exception as e:
                agent_report.critical(f"Agent validation failed for {agent_file.name}: {e}")
        reports["agents"] = agent_report

    # Run detailed skill validator for each skill directory
    # Note: validate_skill returns its own ValidationReport with compatible interface
    skills_dir = plugin_path / "skills"
    if skills_dir.exists():
        skill_report = ValidationReport()
        skill_dirs = sorted(
            d for d in skills_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
        for skill_dir in skill_dirs:
            try:
                skill_single_report = validate_skill(skill_dir)
                skill_report.merge(skill_single_report)
            except Exception as e:
                skill_report.critical(f"Skill validation failed for {skill_dir.name}: {e}")
        reports["skills"] = skill_report

    # Run detailed command validator for each command file
    commands_dir = plugin_path / "commands"
    if commands_dir.exists():
        command_report = ValidationReport()
        for cmd_file in sorted(commands_dir.glob("*.md")):
            try:
                cmd_single_report = validate_command(cmd_file)
                command_report.merge(cmd_single_report)
            except Exception as e:
                command_report.critical(f"Command validation failed for {cmd_file.name}: {e}")
        reports["commands"] = command_report

    return reports


def _run_all_validators_parallel(plugin_path: Path) -> dict[str, ValidationReport]:
    """Parallel implementation — dispatches scoring units via parallel_scan.

    Build one work unit per scoring task in the same order the serial
    loop visits them, then dispatch the whole set to the shared harness.
    ``parallel_scan`` preserves input order, so the merge loop iterates
    by index without sorting.

    Per-file units (agent/skill/command) share their ``target_key`` with
    siblings of the same kind; the merge loop concatenates them in input
    order via ``ValidationReport.merge`` to reproduce the legacy
    per-file finding sequence under "agents"/"skills"/"commands".

    Per-task worker errors: if ``ScanResult.error`` is set the dispatch
    code raises a synthetic CRITICAL into the target report and continues
    — never crashes the whole scoring run. This matches the spec
    contract: "per-file worker exceptions never crash the validator;
    surface as one per-file WARNING in the report".
    """
    reports: dict[str, ValidationReport] = {}

    units = _build_scoring_work_units(plugin_path)
    if not units:
        return reports

    # The harness's first arg is typed ``Sequence[Path]`` but it never
    # introspects the items — it just forwards them to ``scan_func``.
    # We pass a list of dataclasses; type-checkers complain, so we
    # silence with a per-call ignore rather than weakening the harness's
    # annotation (other validators DO pass real Paths).
    scan_results = parallel_scan(units, scan_one_scoring_unit)  # type: ignore[arg-type]

    for idx, sr in enumerate(scan_results):
        unit = units[idx]
        if sr.error is not None:
            # Worker raised — spec mandates "surface as a per-file
            # WARNING in the report (don't crash the whole validator)".
            # We fold it into the target report so downstream
            # categorize_results / recommendations see it consistently.
            target = reports.setdefault(unit.target_key, ValidationReport())
            target.warning(
                f"Scoring scan worker raised on {unit.kind} unit: {sr.error}",
                unit.target_path_str or str(plugin_path),
            )
            continue

        # ``findings`` is a list with exactly one element: the
        # ``(target_key, ValidationReport)`` tuple returned by
        # ``scan_one_scoring_unit``. The harness wraps single-return
        # values in a list per its contract ("scan_func returns list").
        # We unwrap and merge into the right dict slot.
        payload = sr.findings
        if not payload:
            continue
        target_key, worker_report = payload[0]
        if target_key in reports:
            reports[target_key].merge(worker_report)
        else:
            reports[target_key] = worker_report

    return reports


def compute_quality_score(plugin_path: Path) -> QualityScoreReport:
    """Compute comprehensive quality score for a plugin.

    This function:
    1. Runs all validators
    2. Categorizes results
    3. Computes category scores
    4. Calculates overall weighted score
    5. Determines pass/fail status
    6. Generates recommendations

    Args:
        plugin_path: Path to the plugin directory

    Returns:
        QualityScoreReport with complete scoring breakdown
    """
    report = QualityScoreReport(plugin_path=str(plugin_path))

    # Run all validators
    validator_reports = run_all_validators(plugin_path)
    report.validator_reports = validator_reports

    # Categorize all results
    categorized = categorize_results(validator_reports)

    # Calculate category scores
    for category_name, results in categorized.items():
        threshold = CATEGORY_THRESHOLDS.get(category_name, 5)
        score, critical, major, minor, passed = calculate_category_score(results)

        cat_score = CategoryScore(
            name=category_name,
            score=score,
            threshold=threshold,
            passed=score >= threshold,
            issues_critical=critical,
            issues_major=major,
            issues_minor=minor,
            issues_passed=passed,
        )

        # Track critical failures
        if critical > 0:
            for result in results:
                if result.level == "CRITICAL":
                    report.critical_failures.append(f"[{category_name}] {result.message}")

        report.category_scores[category_name] = cat_score

    # Calculate weighted overall score (0-100)
    weighted_sum = 0.0
    total_weight = 0.0
    for category_name, cat_score in report.category_scores.items():
        weight = CATEGORY_WEIGHTS.get(category_name, 0.1)
        weighted_sum += (cat_score.score / 10.0) * weight * 100
        total_weight += weight

    # Normalize if weights don't sum to 1.0
    if total_weight > 0:
        report.overall_score = weighted_sum / total_weight
    else:
        report.overall_score = 0.0

    # Determine letter grade
    # letter_grade removed — no grading in syntactic validation

    # Determine pass/fail status
    has_critical = len(report.critical_failures) > 0
    all_categories_pass = all(cat.passed for cat in report.category_scores.values())

    if has_critical or report.overall_score < 60:
        report.status = "FAIL"
    elif report.overall_score >= 80 and all_categories_pass:
        report.status = "PASS"
    else:
        report.status = "CONDITIONAL_PASS"

    # Generate recommendations
    report.recommendations = generate_recommendations(report.category_scores)

    return report


# =============================================================================
# Output Formatting
# =============================================================================


def print_quality_report(report: QualityScoreReport, verbose: bool = False) -> None:
    """Print a formatted quality report to stdout.

    Args:
        report: QualityScoreReport to print
        verbose: If True, show detailed breakdown
    """
    print(f"\n{'=' * 70}")
    print(f"{COLORS['BOLD']}Plugin Quality Score Report{COLORS['RESET']}")
    print(f"{'=' * 70}")
    print(f"Plugin: {report.plugin_path}")

    # Overall score with color coding
    if report.status == "PASS":
        status_color = COLORS["PASSED"]
        status_symbol = "PASS"
    elif report.status == "CONDITIONAL_PASS":
        status_color = COLORS["MAJOR"]
        status_symbol = "CONDITIONAL PASS"
    else:
        status_color = COLORS["CRITICAL"]
        status_symbol = "FAIL"

    print(f"\n{COLORS['BOLD']}Overall Score:{COLORS['RESET']} ", end="")
    print(f"{status_color}{report.overall_score:.1f}/100{COLORS['RESET']}")
    print(f"{COLORS['BOLD']}Status:{COLORS['RESET']} {status_color}{status_symbol}{COLORS['RESET']}")

    # Category breakdown
    print(f"\n{COLORS['BOLD']}Category Scores (0-10 scale):{COLORS['RESET']}")
    print("-" * 70)

    for name, cat in sorted(report.category_scores.items()):
        # Color based on pass/fail
        if cat.passed:
            score_color = COLORS["PASSED"]
        elif cat.score >= cat.threshold - 2:
            score_color = COLORS["MAJOR"]
        else:
            score_color = COLORS["CRITICAL"]

        # Format category name
        display_name = name.replace("_", " ").title()

        # Build status string
        status = "PASS" if cat.passed else "FAIL"
        status_indicator = f"[{status}]"

        print(f"  {display_name:25} {score_color}{cat.score:5.1f}/10{COLORS['RESET']} ", end="")
        print(f"(min: {cat.threshold}/10) ", end="")
        print(f"{score_color}{status_indicator:8}{COLORS['RESET']} ", end="")
        print(f"[{cat.rating}]")

        if verbose:
            if cat.issues_critical > 0:
                print(f"    {COLORS['CRITICAL']}- Critical: {cat.issues_critical}{COLORS['RESET']}")
            if cat.issues_major > 0:
                print(f"    {COLORS['MAJOR']}- Major: {cat.issues_major}{COLORS['RESET']}")
            if cat.issues_minor > 0:
                print(f"    {COLORS['MINOR']}- Minor: {cat.issues_minor}{COLORS['RESET']}")
            if cat.issues_passed > 0:
                print(f"    {COLORS['PASSED']}- Passed: {cat.issues_passed}{COLORS['RESET']}")

    # Critical failures (always show)
    if report.critical_failures:
        print(f"\n{COLORS['CRITICAL']}Critical Failures:{COLORS['RESET']}")
        print("-" * 70)
        for failure in report.critical_failures[:10]:  # Limit to 10
            print(f"  {COLORS['CRITICAL']}- {failure}{COLORS['RESET']}")
        if len(report.critical_failures) > 10:
            print(f"  ... and {len(report.critical_failures) - 10} more")

    # Recommendations
    if report.recommendations:
        print(f"\n{COLORS['BOLD']}Recommendations:{COLORS['RESET']}")
        print("-" * 70)
        for rec in report.recommendations[:10]:  # Limit to 10
            if "[CRITICAL]" in rec:
                print(f"  {COLORS['CRITICAL']}{rec}{COLORS['RESET']}")
            elif "[REQUIRED]" in rec:
                print(f"  {COLORS['MAJOR']}{rec}{COLORS['RESET']}")
            elif "[RECOMMENDED]" in rec:
                print(f"  {COLORS['MINOR']}{rec}{COLORS['RESET']}")
            else:
                print(f"  {COLORS['INFO']}{rec}{COLORS['RESET']}")
        if len(report.recommendations) > 10:
            print(f"  ... and {len(report.recommendations) - 10} more")

    # Rating guide
    print(f"\n{COLORS['BOLD']}Rating Guide:{COLORS['RESET']}")
    print("-" * 70)
    for score_range, description in RATING_DESCRIPTIONS.items():
        print(f"  {score_range}: {description}")

    print(f"\n{'=' * 70}")


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> int:
    """CLI entry point for quality scoring.

    Returns:
        Exit code based on highest severity issue found:
        - 0: No issues (PASS)
        - 1: Critical issues found
        - 2: Major issues found (no critical)
        - 3: Minor issues only
    """
    from cpv_validation_common import launcher_epilog

    parser = argparse.ArgumentParser(
        description="Compute quality score for Claude Code plugin",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit codes (standard severity-based convention):
    0 - PASS: No issues found
    1 - CRITICAL: Critical issues found
    2 - MAJOR: Major issues found (no critical)
    3 - MINOR: Minor issues only

Rating scale (0-10 per category):
    9-10: Excellent - Ready for production
    7-8:  Good - Minor improvements recommended
    5-6:  Fair - Significant improvements needed
    0-4:  Poor - Major revision required

"""
        + launcher_epilog("scoring"),
    )

    parser.add_argument(
        "plugin_path",
        type=Path,
        help="Path to the plugin directory to validate",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed breakdown including issue counts per category",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of formatted text",
    )
    # NOTE: no --strict flag. Scoring's CategoryScore model tracks only
    # CRITICAL/MAJOR/MINOR (NIT/WARNING are not part of the score), so there is
    # no NIT-blocking concept to gate on. Advertising --strict here would be a
    # broken CLI contract — a CI gate running `validate_scoring.py --strict`
    # expecting NIT to block would be silently wrong. The other validators
    # (encoding/rules/xref/documentation/scope) DO have NIT and honor --strict.
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )

    args = parser.parse_args()

    # Resolve to absolute path so relative_to() works correctly
    plugin_path = args.plugin_path.resolve()

    # Validate plugin path exists
    if not plugin_path.exists():
        print(f"Error: Plugin path does not exist: {plugin_path}", file=sys.stderr)
        return EXIT_CRITICAL

    if not plugin_path.is_dir():
        print(f"Error: Plugin path is not a directory: {plugin_path}", file=sys.stderr)
        return EXIT_CRITICAL

    # Verify this is a plugin directory
    if not (plugin_path / ".claude-plugin").is_dir():
        print(
            f"Error: No Claude Code plugin found at {plugin_path}\nExpected a .claude-plugin/ directory.",
            file=sys.stderr,
        )
        return EXIT_CRITICAL

    # Compute quality score
    report = compute_quality_score(plugin_path)

    # Output results
    if args.json:
        print(report.to_json())
    elif args.report:
        import io as _io

        # Capture the full text report into a buffer
        _buf = _io.StringIO()
        _original_stdout = sys.stdout
        try:
            sys.stdout = _buf
            print_quality_report(report, verbose=args.verbose)
        finally:
            sys.stdout = _original_stdout

        # Write full report to file
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_buf.getvalue())

        # Print compact summary to stdout
        if report.status == "PASS":
            verdict = f"{COLORS['PASSED']}PASS{COLORS['RESET']}"
        elif report.status == "CONDITIONAL_PASS":
            verdict = f"{COLORS['MAJOR']}CONDITIONAL PASS{COLORS['RESET']}"
        else:
            verdict = f"{COLORS['CRITICAL']}FAIL{COLORS['RESET']}"
        print(f"{COLORS['BOLD']}Plugin Quality Score{COLORS['RESET']}: {verdict}")
        print(f"  Score: {report.overall_score:.1f}/100")
        print(f"  Report: {report_path}")
    else:
        print_quality_report(report, verbose=args.verbose)

    # Determine exit code based on highest severity issue found
    # Count issues across all category scores
    total_critical = sum(cat.issues_critical for cat in report.category_scores.values())
    total_major = sum(cat.issues_major for cat in report.category_scores.values())
    total_minor = sum(cat.issues_minor for cat in report.category_scores.values())

    if total_critical > 0:
        return EXIT_CRITICAL
    elif total_major > 0:
        return EXIT_MAJOR
    elif total_minor > 0:
        return EXIT_MINOR
    else:
        return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
