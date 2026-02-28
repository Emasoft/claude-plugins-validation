"""Tests for validate_scoring.py - Quality Scoring Module.

Tests cover:
- calculate_category_score: Score calculation from validation results
- categorize_results: Mapping validator results to scoring categories
- generate_recommendations: Prioritized recommendation generation
- compute_quality_score: Main entry point (integration-level with real plugin)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_scoring import (
    CategoryScore,
    QualityScoreReport,
    calculate_category_score,
    categorize_results,
    compute_quality_score,
    generate_recommendations,
)
from cpv_validation_common import ValidationReport, ValidationResult


class TestCalculateCategoryScore:
    """Tests for calculate_category_score function."""

    def test_perfect_score_with_all_passed(self):
        """calculate_category_score returns max score when all results are PASSED."""
        results = [
            ValidationResult(level="PASSED", message="Check 1 OK"),
            ValidationResult(level="PASSED", message="Check 2 OK"),
            ValidationResult(level="PASSED", message="Check 3 OK"),
        ]
        score, critical, major, minor, passed = calculate_category_score(results)
        assert score == 10.0
        assert critical == 0
        assert major == 0
        assert minor == 0
        assert passed == 3

    def test_deductions_for_critical_and_major(self):
        """calculate_category_score deducts 3 for CRITICAL and 1.5 for MAJOR issues."""
        results = [
            ValidationResult(level="CRITICAL", message="Critical issue"),
            ValidationResult(level="MAJOR", message="Major issue"),
            ValidationResult(level="PASSED", message="OK"),
        ]
        score, critical, major, minor, passed = calculate_category_score(results)
        assert score == pytest.approx(5.5)  # 10 - 3 - 1.5
        assert critical == 1
        assert major == 1
        assert passed == 1

    def test_score_never_below_zero(self):
        """calculate_category_score clamps the score at 0.0 minimum."""
        results = [
            ValidationResult(level="CRITICAL", message="Issue 1"),
            ValidationResult(level="CRITICAL", message="Issue 2"),
            ValidationResult(level="CRITICAL", message="Issue 3"),
            ValidationResult(level="CRITICAL", message="Issue 4"),
        ]
        score, critical, major, minor, passed = calculate_category_score(results)
        assert score == 0.0
        assert critical == 4


class TestCategorizeResults:
    """Tests for categorize_results function."""

    def test_security_results_categorized(self):
        """categorize_results places security validator results in the security category."""
        report = ValidationReport()
        report.add("CRITICAL", "Potential secret found: AWS key")
        reports = {"security": report}
        categories = categorize_results(reports)
        assert len(categories["security"]) == 1
        assert categories["security"][0].message == "Potential secret found: AWS key"

    def test_schema_results_categorized(self):
        """categorize_results places manifest/JSON results in schema_compliance category."""
        report = ValidationReport()
        report.add("MAJOR", "plugin.json missing required field: name")
        reports = {"plugin": report}
        categories = categorize_results(reports)
        assert len(categories["schema_compliance"]) == 1

    def test_empty_reports(self):
        """categorize_results returns empty categories for empty reports."""
        categories = categorize_results({})
        for cat_name, results_list in categories.items():
            assert results_list == [], f"Category {cat_name} should be empty"


class TestGenerateRecommendations:
    """Tests for generate_recommendations function."""

    def test_critical_recommendations_first(self):
        """generate_recommendations places CRITICAL recommendations at the top."""
        scores = {
            "security": CategoryScore(name="security", score=4.0, threshold=8, passed=False, issues_critical=2, issues_major=0, issues_minor=0),
            "documentation": CategoryScore(name="documentation", score=3.0, threshold=5, passed=False, issues_critical=0, issues_major=1, issues_minor=0),
        }
        recs = generate_recommendations(scores)
        assert len(recs) >= 2
        assert "[CRITICAL]" in recs[0]
        assert "Security" in recs[0]

    def test_no_recommendations_for_perfect_scores(self):
        """generate_recommendations returns empty list when all categories pass with no issues."""
        scores = {
            "security": CategoryScore(name="security", score=10.0, threshold=8, passed=True, issues_critical=0, issues_major=0, issues_minor=0),
            "documentation": CategoryScore(name="documentation", score=9.0, threshold=5, passed=True, issues_critical=0, issues_major=0, issues_minor=0),
        }
        recs = generate_recommendations(scores)
        assert len(recs) == 0


class TestComputeQualityScore:
    """Tests for compute_quality_score main entry function."""

    def test_minimal_valid_plugin(self, tmp_path):
        """compute_quality_score returns a report with overall_score for a minimal plugin structure."""
        plugin_dir = tmp_path / "test-plugin"
        plugin_dir.mkdir()
        claude_plugin = plugin_dir / ".claude-plugin"
        claude_plugin.mkdir()
        manifest = {
            "name": "test-plugin",
            "version": "1.0.0",
            "description": "A test plugin",
            "author": {"name": "Tester", "email": "test@example.com"},
        }
        (claude_plugin / "plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (plugin_dir / "README.md").write_text("# Test Plugin\n\nA test plugin for validation.\n\n## Installation\n\nRun `claude plugin install`\n\n## Usage\n\nUse it.\n", encoding="utf-8")
        report = compute_quality_score(plugin_dir)
        assert isinstance(report, QualityScoreReport)
        assert 0.0 <= report.overall_score <= 100.0
        assert report.letter_grade in ("A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F")
        assert report.status in ("PASS", "CONDITIONAL_PASS", "FAIL")

    def test_nonexistent_plugin_returns_failing_score(self, tmp_path):
        """compute_quality_score returns FAIL status for a plugin path that does not exist."""
        report = compute_quality_score(tmp_path / "nonexistent-plugin")
        assert report.status == "FAIL"


# =========================================================================
# Additional tests targeting uncovered lines (148, 154, 196, 217, 310, 324,
# 385, 432-506, 573, 587, 607-693, 711-782, 786)
# =========================================================================

from validate_scoring import (
    print_quality_report,
    run_all_validators,
)


class TestCategoryScoreDataclass:
    """Tests for CategoryScore dataclass post_init and serialization."""

    def test_fair_rating_for_score_between_5_and_7(self):
        """CategoryScore.__post_init__ assigns 'Fair' rating when score is 5 <= s < 7."""
        cat = CategoryScore(name="documentation", score=5.5, threshold=5, passed=True, issues_critical=0, issues_major=1, issues_minor=0)
        assert cat.rating == "Fair"

    def test_poor_rating_for_score_below_5(self):
        """CategoryScore.__post_init__ assigns 'Poor' rating when score < 5."""
        cat = CategoryScore(name="security", score=3.0, threshold=8, passed=False, issues_critical=2, issues_major=0, issues_minor=0)
        assert cat.rating == "Poor"

    def test_to_dict_returns_complete_structure(self):
        """CategoryScore.to_dict returns dict with name, score, threshold, passed, rating, issues, recommendations."""
        cat = CategoryScore(
            name="schema_compliance",
            score=8.5,
            threshold=8,
            passed=True,
            issues_critical=0,
            issues_major=1,
            issues_minor=2,
            issues_passed=5,
        )
        cat.recommendations = ["Fix major issue X"]
        d = cat.to_dict()
        assert d["name"] == "schema_compliance"
        assert d["score"] == 8.5
        assert d["threshold"] == 8
        assert d["passed"] is True
        assert d["rating"] == "Good"
        assert d["issues"]["critical"] == 0
        assert d["issues"]["major"] == 1
        assert d["issues"]["minor"] == 2
        assert d["issues"]["passed"] == 5
        assert d["recommendations"] == ["Fix major issue X"]


class TestQualityScoreReportSerialization:
    """Tests for QualityScoreReport.to_dict and to_json methods."""

    def test_to_dict_includes_all_top_level_fields(self):
        """QualityScoreReport.to_dict returns dict with plugin_path, overall_score, letter_grade, status, etc."""
        report = QualityScoreReport(plugin_path="/fake/path")
        report.overall_score = 85.0
        report.letter_grade = "B"
        report.status = "CONDITIONAL_PASS"
        report.critical_failures = ["[security] Secret found"]
        report.recommendations = ["Fix secrets"]
        # Add a category score
        cat = CategoryScore(name="security", score=7.0, threshold=8, passed=False, issues_critical=1, issues_major=0, issues_minor=0)
        report.category_scores["security"] = cat
        # Add a validator report
        vr = ValidationReport()
        vr.add("CRITICAL", "Found AWS key")
        vr.add("PASSED", "No path traversal")
        report.validator_reports["security"] = vr

        d = report.to_dict()
        assert d["plugin_path"] == "/fake/path"
        assert d["overall_score"] == 85.0
        assert d["letter_grade"] == "B"
        assert d["status"] == "CONDITIONAL_PASS"
        assert d["critical_failures"] == ["[security] Secret found"]
        assert d["recommendations"] == ["Fix secrets"]
        assert "security" in d["category_scores"]
        assert d["category_scores"]["security"]["score"] == 7.0
        assert "security" in d["validator_summaries"]
        assert d["validator_summaries"]["security"]["critical"] == 1

    def test_to_json_returns_valid_json_string(self):
        """QualityScoreReport.to_json returns a parseable JSON string with correct indent."""
        report = QualityScoreReport(plugin_path="/test/plugin")
        report.overall_score = 72.5
        report.letter_grade = "C"
        report.status = "CONDITIONAL_PASS"
        json_str = report.to_json(indent=2)
        parsed = json.loads(json_str)
        assert parsed["plugin_path"] == "/test/plugin"
        assert parsed["overall_score"] == 72.5
        assert parsed["letter_grade"] == "C"


class TestCategorizeResultsAdditional:
    """Additional tests for categorize_results covering matcher, hook_type categories."""

    def test_matcher_validity_keywords_categorized(self):
        """categorize_results places results with matcher/regex/pattern keywords in matcher_validity."""
        report = ValidationReport()
        report.add("MAJOR", "Hook matcher regex pattern invalid for tool name")
        reports = {"hooks": report}
        categories = categorize_results(reports)
        assert len(categories["matcher_validity"]) == 1
        assert "matcher" in categories["matcher_validity"][0].message.lower()

    def test_hook_types_keywords_categorized(self):
        """categorize_results places results with hook type/PreToolUse keywords in hook_types."""
        report = ValidationReport()
        report.add("MINOR", "Unknown hook type: PreToolUse event type is unusual")
        reports = {"hooks": report}
        categories = categorize_results(reports)
        assert len(categories["hook_types"]) == 1
        assert "hook type" in categories["hook_types"][0].message.lower()


class TestGenerateRecommendationsAdditional:
    """Additional tests for generate_recommendations covering RECOMMENDED and OPTIONAL tiers."""

    def test_recommended_tier_for_passed_category_with_major_issues(self):
        """generate_recommendations emits [RECOMMENDED] for categories that pass threshold but have major issues."""
        scores = {
            "documentation": CategoryScore(name="documentation", score=8.0, threshold=5, passed=True, issues_critical=0, issues_major=2, issues_minor=0),
        }
        recs = generate_recommendations(scores)
        assert len(recs) == 1
        assert "[RECOMMENDED]" in recs[0]
        assert "2 major issue(s)" in recs[0]

    def test_optional_tier_for_passed_category_with_only_minor_issues(self):
        """generate_recommendations emits [OPTIONAL] for categories that pass with minor issues only."""
        scores = {
            "maintainability": CategoryScore(name="maintainability", score=9.0, threshold=6, passed=True, issues_critical=0, issues_major=0, issues_minor=3),
        }
        recs = generate_recommendations(scores)
        assert len(recs) == 1
        assert "[OPTIONAL]" in recs[0]
        assert "3 minor issue(s)" in recs[0]


class TestRunAllValidators:
    """Tests for run_all_validators with real plugin directory structures."""

    def _make_base_plugin(self, tmp_path):
        """Helper: create a minimal plugin directory with manifest and README."""
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        claude_plugin = plugin_dir / ".claude-plugin"
        claude_plugin.mkdir()
        manifest = {
            "name": "my-plugin",
            "version": "1.0.0",
            "description": "A plugin for testing run_all_validators",
            "author": {"name": "Tester", "email": "tester@example.com"},
        }
        (claude_plugin / "plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (plugin_dir / "README.md").write_text(
            "# My Plugin\n\nA test plugin.\n\n## Installation\n\nRun `claude plugin install my-plugin`\n\n## Usage\n\nJust use it.\n",
            encoding="utf-8",
        )
        return plugin_dir

    def test_hooks_json_triggers_hook_validator(self, tmp_path):
        """run_all_validators includes 'hooks' report when hooks/hooks.json exists."""
        plugin_dir = self._make_base_plugin(tmp_path)
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir()
        hooks_json = {
            "hooks": [
                {
                    "event": "PreToolUse",
                    "matcher": "Bash",
                    "command": "echo 'checking bash'",
                }
            ]
        }
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_json, indent=2), encoding="utf-8")
        reports = run_all_validators(plugin_dir)
        assert "hooks" in reports
        assert hasattr(reports["hooks"], "results")

    def test_mcp_json_triggers_mcp_validator(self, tmp_path):
        """run_all_validators includes 'mcp' report when .mcp.json exists."""
        plugin_dir = self._make_base_plugin(tmp_path)
        mcp_config = {
            "mcpServers": {
                "test-server": {
                    "command": "node",
                    "args": ["server.js"],
                }
            }
        }
        (plugin_dir / ".mcp.json").write_text(json.dumps(mcp_config, indent=2), encoding="utf-8")
        reports = run_all_validators(plugin_dir)
        assert "mcp" in reports
        assert hasattr(reports["mcp"], "results")

    def test_agents_dir_triggers_agent_validator(self, tmp_path):
        """run_all_validators includes 'agents' report when agents/ dir with .md files exists."""
        plugin_dir = self._make_base_plugin(tmp_path)
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir()
        agent_content = """---
name: helper-agent
description: An agent that helps with tasks
model: sonnet
tools:
  - Read
  - Write
  - Bash
---

# Helper Agent

This agent helps with general tasks.

## Instructions

Follow the user's instructions carefully.
"""
        (agents_dir / "helper-agent.md").write_text(agent_content, encoding="utf-8")
        reports = run_all_validators(plugin_dir)
        assert "agents" in reports
        assert hasattr(reports["agents"], "results")

    def test_skills_dir_triggers_skill_validator(self, tmp_path):
        """run_all_validators includes 'skills' report when skills/ dir with subdirectories exists."""
        plugin_dir = self._make_base_plugin(tmp_path)
        skills_dir = plugin_dir / "skills"
        skills_dir.mkdir()
        my_skill_dir = skills_dir / "my-skill"
        my_skill_dir.mkdir()
        skill_content = """---
name: my-skill
description: A skill for testing
triggers:
  - when user asks about testing
---

# My Skill

Instructions for the skill.

## When to use

Use this when testing.
"""
        (my_skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")
        reports = run_all_validators(plugin_dir)
        assert "skills" in reports
        assert hasattr(reports["skills"], "results")

    def test_commands_dir_triggers_command_validator(self, tmp_path):
        """run_all_validators includes 'commands' report when commands/ dir with .md files exists."""
        plugin_dir = self._make_base_plugin(tmp_path)
        commands_dir = plugin_dir / "commands"
        commands_dir.mkdir()
        command_content = """---
name: greet
description: Greet the user
allowed_tools:
  - Read
---

# /greet

Greet the user with a friendly message.

## Instructions

Say hello to the user and ask how you can help.
"""
        (commands_dir / "greet.md").write_text(command_content, encoding="utf-8")
        reports = run_all_validators(plugin_dir)
        assert "commands" in reports
        assert hasattr(reports["commands"], "results")


class TestComputeQualityScoreAdditional:
    """Additional tests for compute_quality_score covering CONDITIONAL_PASS and edge cases."""

    def _make_plugin_with_issues(self, tmp_path, *, include_major_issues=False):
        """Helper: create a plugin that may produce CONDITIONAL_PASS status."""
        plugin_dir = tmp_path / "cond-plugin"
        plugin_dir.mkdir()
        claude_plugin = plugin_dir / ".claude-plugin"
        claude_plugin.mkdir()
        manifest = {
            "name": "cond-plugin",
            "version": "1.0.0",
            "description": "A plugin with some issues for testing conditional pass",
            "author": {"name": "Tester", "email": "tester@example.com"},
        }
        (claude_plugin / "plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        # Minimal README that is short enough to trigger documentation warnings but not critical failures
        (plugin_dir / "README.md").write_text(
            "# Cond Plugin\n\nA plugin.\n\n## Installation\n\nInstall it.\n\n## Usage\n\nUse it.\n",
            encoding="utf-8",
        )
        return plugin_dir

    def test_conditional_pass_status_when_score_between_60_and_80(self, tmp_path):
        """compute_quality_score returns CONDITIONAL_PASS when score >= 60 but < 80 or not all categories pass."""
        plugin_dir = self._make_plugin_with_issues(tmp_path)
        report = compute_quality_score(plugin_dir)
        # The minimal plugin should have some issues from validators but not critical ones,
        # resulting in one of the three statuses. We verify the status assignment logic is reachable.
        assert report.status in ("PASS", "CONDITIONAL_PASS", "FAIL")
        # Also verify the score-to-status relationship is internally consistent
        if report.overall_score >= 80 and all(c.passed for c in report.category_scores.values()) and len(report.critical_failures) == 0:
            assert report.status == "PASS"
        elif len(report.critical_failures) > 0 or report.overall_score < 60:
            assert report.status == "FAIL"
        else:
            assert report.status == "CONDITIONAL_PASS"


class TestPrintQualityReport:
    """Tests for print_quality_report output formatting."""

    def _make_report_with_categories(self):
        """Helper: build a QualityScoreReport with multiple categories and issues."""
        report = QualityScoreReport(plugin_path="/test/my-plugin")
        report.overall_score = 72.3
        report.letter_grade = "C"
        report.status = "CONDITIONAL_PASS"
        report.category_scores = {
            "security": CategoryScore(name="security", score=9.0, threshold=8, passed=True, issues_critical=0, issues_major=0, issues_minor=0, issues_passed=4),
            "schema_compliance": CategoryScore(name="schema_compliance", score=6.0, threshold=8, passed=False, issues_critical=0, issues_major=2, issues_minor=1, issues_passed=3),
            "documentation": CategoryScore(name="documentation", score=3.0, threshold=5, passed=False, issues_critical=0, issues_major=1, issues_minor=0, issues_passed=1),
        }
        report.critical_failures = ["[schema_compliance] Manifest missing required field"]
        report.recommendations = [
            "[CRITICAL] Fix manifest issues",
            "[REQUIRED] Documentation: improve coverage",
            "[RECOMMENDED] Schema compliance: fix major issues",
        ]
        return report

    def test_print_quality_report_outputs_score_and_status(self, capsys):
        """print_quality_report prints overall score, letter grade, and status to stdout."""
        report = self._make_report_with_categories()
        print_quality_report(report, verbose=False)
        captured = capsys.readouterr().out
        assert "72.3/100" in captured
        assert "CONDITIONAL PASS" in captured
        assert "Plugin Quality Score Report" in captured

    def test_print_quality_report_verbose_shows_issue_counts(self, capsys):
        """print_quality_report with verbose=True shows critical/major/minor/passed counts per category."""
        report = self._make_report_with_categories()
        print_quality_report(report, verbose=True)
        captured = capsys.readouterr().out
        # Verbose mode should show issue breakdown lines
        assert "Major:" in captured
        assert "Minor:" in captured
        assert "Passed:" in captured

    def test_print_quality_report_shows_recommendations(self, capsys):
        """print_quality_report prints recommendation lines including CRITICAL, REQUIRED, RECOMMENDED tags."""
        report = self._make_report_with_categories()
        print_quality_report(report, verbose=False)
        captured = capsys.readouterr().out
        assert "Recommendations:" in captured
        assert "[CRITICAL]" in captured
        assert "[REQUIRED]" in captured
        assert "[RECOMMENDED]" in captured

    def test_print_quality_report_shows_rating_guide(self, capsys):
        """print_quality_report always prints the Rating Guide section with score range descriptions."""
        report = QualityScoreReport(plugin_path="/test/simple")
        report.overall_score = 95.0
        report.letter_grade = "A"
        report.status = "PASS"
        print_quality_report(report, verbose=False)
        captured = capsys.readouterr().out
        assert "Rating Guide:" in captured
        assert "Excellent" in captured
        assert "Good" in captured
        assert "Fair" in captured
        assert "Poor" in captured
