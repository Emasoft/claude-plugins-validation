"""Tests for validate_marketplace_pipeline.py - Marketplace Publishing Pipeline Validator.

Tests cover:
- validate_marketplace_structure: Marketplace structure validation (marketplace.json, .gitmodules)
- validate_submodule_health: Git submodule health checks
- validate_marketplace_workflows: GitHub workflow automation validation
- validate_marketplace_pipeline: Main entry point running all category validations
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_marketplace_pipeline import (
    PipelineValidationReport,
    _check_workflow_hardening,
    validate_marketplace_pipeline,
    validate_marketplace_structure,
    validate_marketplace_workflows,
    validate_submodule_health,
)


def _make_marketplace(tmp_path, marketplace_json=None, gitmodules=None, workflows=None):
    """Helper to create a marketplace directory structure for testing.

    Args:
        tmp_path: pytest tmp_path fixture
        marketplace_json: dict for marketplace.json content, or None to skip
        gitmodules: str content for .gitmodules, or None to skip
        workflows: dict mapping filename to YAML str content, or None to skip

    Returns:
        Path to the marketplace directory
    """
    mp = tmp_path / "marketplace"
    mp.mkdir()
    if marketplace_json is not None:
        (mp / "marketplace.json").write_text(json.dumps(marketplace_json, indent=2), encoding="utf-8")
    if gitmodules is not None:
        (mp / ".gitmodules").write_text(gitmodules, encoding="utf-8")
    if workflows is not None:
        wf_dir = mp / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        for name, content in workflows.items():
            (wf_dir / name).write_text(content, encoding="utf-8")
    return mp


class TestValidateMarketplaceStructure:
    """Tests for validate_marketplace_structure function."""

    def test_valid_marketplace_structure(self, tmp_path):
        """validate_marketplace_structure passes for a marketplace with valid JSON and gitmodules."""
        mp = _make_marketplace(
            tmp_path,
            marketplace_json={"name": "my-marketplace", "version": "1.0.0", "plugins": [{"name": "plugin-a"}]},
            gitmodules='[submodule "plugin-a"]\n\tpath = plugin-a\n\turl = https://github.com/owner/plugin-a.git\n',
        )
        # Create plugin submodule directory
        (mp / "plugin-a").mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        result = validate_marketplace_structure(mp, report)
        assert result is not None
        assert result["name"] == "my-marketplace"
        cat = report.categories["marketplace_structure"]
        passed_results = [r for r in cat.results if r.level == "PASSED"]
        assert len(passed_results) >= 3  # marketplace.json exists, valid JSON, required fields

    def test_missing_marketplace_json(self, tmp_path):
        """validate_marketplace_structure reports CRITICAL when marketplace.json is missing."""
        mp = _make_marketplace(tmp_path)
        report = PipelineValidationReport(marketplace_path=mp)
        result = validate_marketplace_structure(mp, report)
        assert result is None
        cat = report.categories["marketplace_structure"]
        critical_results = [r for r in cat.results if r.level == "CRITICAL"]
        assert any("marketplace.json not found" in r.message for r in critical_results)

    def test_invalid_json_in_marketplace_json(self, tmp_path):
        """validate_marketplace_structure reports CRITICAL for invalid JSON content."""
        mp = _make_marketplace(tmp_path)
        (mp / "marketplace.json").write_text("{invalid json content!!", encoding="utf-8")
        report = PipelineValidationReport(marketplace_path=mp)
        result = validate_marketplace_structure(mp, report)
        assert result is None
        cat = report.categories["marketplace_structure"]
        critical_results = [r for r in cat.results if r.level == "CRITICAL"]
        assert any("invalid JSON" in r.message for r in critical_results)

    def test_invalid_json_does_not_bank_version_consistency_points(self, tmp_path):
        """Check 6 must NOT award PASSED points for version consistency on invalid JSON (m4).

        A fundamentally broken marketplace previously banked 3.0 PASSED points
        for a check that could not possibly have run, inflating its grade.
        """
        mp = _make_marketplace(tmp_path)
        (mp / "marketplace.json").write_text("{invalid json content!!", encoding="utf-8")
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_structure(mp, report)
        cat = report.categories["marketplace_structure"]
        # The "version consistency" line must be an INFO (0 points), not a PASSED 3.0.
        version_lines = [r for r in cat.results if "Version consistency check skipped" in r.message]
        assert version_lines, "expected a version-consistency skip line"
        assert all(r.level == "INFO" for r in version_lines)
        assert all(r.points_possible == 0.0 and r.points_earned == 0.0 for r in version_lines)
        assert all("invalid" in r.message for r in version_lines)

    def test_valid_json_no_plugins_still_passes_version_consistency(self, tmp_path):
        """With valid JSON but an empty plugins list, the version check legitimately PASSES 3.0 (m4, benign side).

        The bottom `else` (legitimate skip) fires only when marketplace_data is
        valid AND no plugins were found — distinct from the invalid-JSON case
        which must NOT award points.
        """
        mp = _make_marketplace(
            tmp_path,
            marketplace_json={"name": "ok-mp", "version": "1.0.0", "plugins": []},
        )
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_structure(mp, report)
        cat = report.categories["marketplace_structure"]
        version_lines = [r for r in cat.results if "Version consistency check skipped" in r.message]
        assert version_lines, "expected a version-consistency skip line"
        assert all(r.level == "PASSED" for r in version_lines)
        assert all(r.points_earned == 3.0 for r in version_lines)
        assert all("no plugins with versions" in r.message for r in version_lines)

    def test_missing_required_fields(self, tmp_path):
        """validate_marketplace_structure reports CRITICAL when required fields (name, version, plugins) are missing."""
        mp = _make_marketplace(tmp_path, marketplace_json={"description": "incomplete"})
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_structure(mp, report)
        # The function returns the parsed data even if required fields are missing
        # but it should report CRITICAL for the missing fields
        cat = report.categories["marketplace_structure"]
        critical_results = [r for r in cat.results if r.level == "CRITICAL"]
        assert any("missing required fields" in r.message for r in critical_results)


class TestValidateSubmoduleHealth:
    """Tests for validate_submodule_health function."""

    def test_no_gitmodules_reports_critical(self, tmp_path):
        """validate_submodule_health reports CRITICAL when .gitmodules does not exist."""
        mp = _make_marketplace(tmp_path)
        report = PipelineValidationReport(marketplace_path=mp)
        validate_submodule_health(mp, report)
        cat = report.categories["submodule_health"]
        critical_results = [r for r in cat.results if r.level == "CRITICAL"]
        assert len(critical_results) >= 1
        assert any(".gitmodules not found" in r.message for r in critical_results)

    def test_empty_gitmodules_awards_full_points(self, tmp_path):
        """validate_submodule_health awards full points when .gitmodules exists but has no submodules."""
        mp = _make_marketplace(tmp_path, gitmodules="# No submodules defined\n")
        report = PipelineValidationReport(marketplace_path=mp)
        validate_submodule_health(mp, report)
        cat = report.categories["submodule_health"]
        passed_results = [r for r in cat.results if r.level == "PASSED"]
        assert len(passed_results) >= 1


class TestValidateMarketplaceWorkflows:
    """Tests for validate_marketplace_workflows function."""

    def test_no_workflows_directory(self, tmp_path):
        """validate_marketplace_workflows reports MAJOR when .github/workflows/ is missing."""
        mp = _make_marketplace(tmp_path)
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_workflows(mp, report)
        cat = report.categories["marketplace_workflows"]
        major_results = [r for r in cat.results if r.level == "MAJOR"]
        assert any(".github/workflows/" in r.message for r in major_results)

    def test_valid_workflow_with_triggers(self, tmp_path):
        """validate_marketplace_workflows passes for workflow with repository_dispatch and workflow_dispatch."""
        workflow_content = "name: Update Submodules\non:\n  repository_dispatch:\n    types: [plugin-updated]\n  workflow_dispatch:\njobs:\n  update:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo sync\n"
        mp = _make_marketplace(tmp_path, workflows={"update-submodules.yml": workflow_content})
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_workflows(mp, report)
        cat = report.categories["marketplace_workflows"]
        passed_results = [r for r in cat.results if r.level == "PASSED"]
        assert any("repository_dispatch" in r.message for r in passed_results)
        assert any("workflow_dispatch" in r.message for r in passed_results)


class TestWorkflowHardeningAdvisory:
    """audit #10 - advisory (INFO) GHA hardening checks on marketplace workflows.

    CPV now surfaces the same hardening it ships (permissions / timeout / SHA-pin)
    for a marketplace's hand-written workflows, as INFO so the grade is untouched.
    """

    _UNHARDENED = (
        "name: Update\n"
        "on:\n  repository_dispatch:\n    types: [plugin-updated]\n  workflow_dispatch:\n"
        "jobs:\n"
        "  sync:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: astral-sh/setup-uv@v4\n"
        "      - run: echo sync\n"
    )

    _HARDENED = (
        "name: Update\n"
        "on:\n  repository_dispatch:\n    types: [plugin-updated]\n  workflow_dispatch:\n"
        "permissions:\n  contents: read\n"
        "jobs:\n"
        "  sync:\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 15\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: astral-sh/setup-uv@e4db8464a088ece1b920f60402e813ea4de65b8f # v4\n"
        "      - run: echo sync\n"
    )

    def _infos(self, report, category="marketplace_workflows"):
        cat = report.categories[category]
        return [r for r in cat.results if r.level == "INFO"]

    def test_unhardened_workflow_flags_all_three(self, tmp_path):
        wf = tmp_path / "update-submodules.yml"
        wf.write_text(self._UNHARDENED, encoding="utf-8")
        report = PipelineValidationReport(marketplace_path=tmp_path)
        _check_workflow_hardening(wf, report, "marketplace_workflows")
        msgs = " ".join(r.message for r in self._infos(report))
        assert "permissions" in msgs
        assert "timeout-minutes" in msgs
        assert "SHA-pinned" in msgs  # astral-sh/setup-uv@v4 is third-party, tag-pinned

    def test_hardened_workflow_flags_nothing(self, tmp_path):
        """Two-sided: a fully hardened workflow produces zero INFO advisories."""
        wf = tmp_path / "update-submodules.yml"
        wf.write_text(self._HARDENED, encoding="utf-8")
        report = PipelineValidationReport(marketplace_path=tmp_path)
        _check_workflow_hardening(wf, report, "marketplace_workflows")
        assert self._infos(report) == []

    def test_first_party_action_tag_is_not_flagged(self, tmp_path):
        """actions/* and github/* may use tag refs — only third-party needs a SHA."""
        wf = tmp_path / "ci.yml"
        wf.write_text(
            "name: CI\non:\n  push:\n"
            "permissions:\n  contents: read\n"
            "jobs:\n  build:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n"
            "    steps:\n      - uses: actions/checkout@v4\n      - uses: github/codeql-action@v3\n",
            encoding="utf-8",
        )
        report = PipelineValidationReport(marketplace_path=tmp_path)
        _check_workflow_hardening(wf, report, "marketplace_workflows")
        # No SHA-pin advisory for actions/checkout or github/codeql-action.
        assert not any("SHA-pinned" in r.message for r in self._infos(report))

    def test_advisory_does_not_change_grade(self, tmp_path):
        """INFO advisories add 0 points — score is identical with/without them."""
        mp = _make_marketplace(
            tmp_path,
            marketplace_json={"name": "m", "version": "1.0.0", "plugins": []},
            gitmodules="# empty\n",
            workflows={"update-submodules.yml": self._UNHARDENED},
        )
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_workflows(mp, report)
        cat = report.categories["marketplace_workflows"]
        # INFO results carry 0 possible points (don't inflate the denominator).
        info_possible = sum(r.points_possible for r in cat.results if r.level == "INFO")
        assert info_possible == 0.0
        assert len([r for r in cat.results if r.level == "INFO"]) >= 1


class TestValidateMarketplacePipeline:
    """Tests for validate_marketplace_pipeline main entry point."""

    def test_complete_marketplace(self, tmp_path):
        """validate_marketplace_pipeline returns a report with score for a complete marketplace setup."""
        workflow_content = "name: Update\non:\n  repository_dispatch:\n    types: [plugin-updated]\n  workflow_dispatch:\njobs:\n  sync:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo sync\n"
        mp = _make_marketplace(
            tmp_path,
            marketplace_json={"name": "test-marketplace", "version": "1.0.0", "plugins": []},
            gitmodules="# empty\n",
            workflows={"update-submodules.yml": workflow_content},
        )
        (mp / "README.md").write_text(
            "# Test Marketplace\n\n## Installation\n\nclaude plugin marketplace add test\n", encoding="utf-8"
        )
        report = validate_marketplace_pipeline(mp)
        assert isinstance(report, PipelineValidationReport)
        assert 0.0 <= report.total_score <= 100.0
        assert report.grade in ("A", "B", "C", "D", "F")

    def test_empty_directory(self, tmp_path):
        """validate_marketplace_pipeline returns low score for an empty directory."""
        mp = tmp_path / "empty-marketplace"
        mp.mkdir()
        report = validate_marketplace_pipeline(mp)
        assert isinstance(report, PipelineValidationReport)
        assert report.total_score < 50.0
        assert report.has_critical()


# =============================================================================
# Additional tests for uncovered lines
# =============================================================================

from validate_marketplace_pipeline import (
    CategoryScore,
    PipelineValidationResult,
    check_python_syntax,
    format_text_report,
    load_yaml_file,
    main,
    parse_gitmodules,
    validate_documentation,
    validate_plugin_workflows,
    validate_sync_scripts,
)


class TestPipelineValidationResultToDict:
    """Tests for PipelineValidationResult.to_dict serialization."""

    def test_to_dict_returns_all_fields(self):
        """to_dict returns a dictionary with all fields including level, category, message, file_path, suggestion, and points."""
        result = PipelineValidationResult(
            level="CRITICAL",
            category="marketplace_structure",
            message="marketplace.json not found",
            file_path="/tmp/marketplace/marketplace.json",
            suggestion="Create marketplace.json",
            points_earned=0.0,
            points_possible=5.0,
        )
        d = result.to_dict()
        assert d["level"] == "CRITICAL"
        assert d["category"] == "marketplace_structure"
        assert d["message"] == "marketplace.json not found"
        assert d["file_path"] == "/tmp/marketplace/marketplace.json"
        assert d["suggestion"] == "Create marketplace.json"
        assert d["points_earned"] == 0.0
        assert d["points_possible"] == 5.0

    def test_to_dict_empty_file_path_becomes_none(self):
        """to_dict returns None for file_path and suggestion when they are empty strings."""
        result = PipelineValidationResult(
            level="PASSED",
            category="sync_scripts",
            message="All good",
            file_path="",
            suggestion="",
            points_earned=3.0,
            points_possible=3.0,
        )
        d = result.to_dict()
        assert d["file_path"] is None
        assert d["suggestion"] is None


class TestCategoryScorePercentage:
    """Tests for CategoryScore.percentage edge case."""

    def test_percentage_returns_100_when_no_points_possible(self):
        """percentage returns 100.0 when points_possible is zero to avoid division by zero."""
        cat = CategoryScore(name="test_cat", weight=10, points_earned=0.0, points_possible=0.0)
        assert cat.percentage == 100.0


class TestGradeAndGradeDescription:
    """Tests for PipelineValidationReport grade branches and grade_description."""

    def test_grade_a_for_score_above_90(self, tmp_path):
        """grade returns 'A' and grade_description returns 'Pipeline fully operational' for score >= 90."""
        mp = tmp_path / "mp"
        mp.mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        # Add enough passed points across all categories to achieve >= 90
        for cat_name, cat in report.categories.items():
            report.passed(cat_name, "All good", 100.0)
        assert report.grade == "A"
        assert report.grade_description == "Pipeline fully operational"

    def test_grade_b_for_score_80_to_89(self, tmp_path):
        """grade returns 'B' and grade_description returns 'Minor gaps, mostly functional' for 80 <= score < 90."""
        mp = tmp_path / "mp"
        mp.mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        # Earn 85% in each category by adding passed and failed results
        for cat_name, cat in report.categories.items():
            report.passed(cat_name, "Most good", 85.0)
            report.add("MINOR", cat_name, "Small issue", 15.0, 0.0)
        assert report.grade == "B"
        assert report.grade_description == "Minor gaps, mostly functional"

    def test_grade_d_for_score_60_to_69(self, tmp_path):
        """grade returns 'D' and grade_description returns 'Manual updates required' for 60 <= score < 70."""
        mp = tmp_path / "mp"
        mp.mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        for cat_name in report.categories:
            report.passed(cat_name, "Some good", 65.0)
            report.add("MAJOR", cat_name, "Big issue", 35.0, 0.0)
        assert report.grade == "D"
        assert report.grade_description == "Manual updates required"

    def test_grade_f_for_score_below_60(self, tmp_path):
        """grade returns 'F' and grade_description returns 'Pipeline broken or not configured' for score < 60."""
        mp = tmp_path / "mp"
        mp.mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        for cat_name in report.categories:
            report.passed(cat_name, "Little good", 30.0)
            report.add("CRITICAL", cat_name, "Fatal issue", 70.0, 0.0)
        assert report.grade == "F"
        assert report.grade_description == "Pipeline broken or not configured"


class TestReportHelpers:
    """Tests for has_major, has_minor, exit_code, and to_dict on PipelineValidationReport."""

    def test_has_major_returns_true_when_major_exists(self, tmp_path):
        """has_major returns True when at least one MAJOR result exists in any category."""
        mp = tmp_path / "mp"
        mp.mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        report.major("marketplace_structure", "Missing field", 5.0)
        assert report.has_major() is True

    def test_has_minor_returns_true_when_minor_exists(self, tmp_path):
        """has_minor returns True when at least one MINOR result exists in any category."""
        mp = tmp_path / "mp"
        mp.mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        report.minor("documentation", "No diagram", 4.0)
        assert report.has_minor() is True

    def test_exit_code_branches(self, tmp_path):
        """exit_code returns 0 for A, 3 for B/C, 2 for D, 1 for F based on total_score."""
        mp = tmp_path / "mp"
        mp.mkdir()
        # F grade (score < 60) -> exit_code 1 (EXIT_CRITICAL — worst grade = most severe exit)
        report_f = PipelineValidationReport(marketplace_path=mp)
        for cat_name in report_f.categories:
            report_f.passed(cat_name, "ok", 20.0)
            report_f.add("CRITICAL", cat_name, "bad", 80.0, 0.0)
        assert report_f.exit_code() == 1  # EXIT_CRITICAL for F

        # D grade (60 <= score < 70) -> exit_code 2 (EXIT_MAJOR)
        report_d = PipelineValidationReport(marketplace_path=mp)
        for cat_name in report_d.categories:
            report_d.passed(cat_name, "ok", 65.0)
            report_d.add("MAJOR", cat_name, "issue", 35.0, 0.0)
        assert report_d.exit_code() == 2  # EXIT_MAJOR for D

        # B grade (80 <= score < 90) -> exit_code 3 (EXIT_MINOR — minor gaps only)
        report_b = PipelineValidationReport(marketplace_path=mp)
        for cat_name in report_b.categories:
            report_b.passed(cat_name, "ok", 85.0)
            report_b.add("MINOR", cat_name, "nit", 15.0, 0.0)
        assert report_b.exit_code() == 3  # EXIT_MINOR for B/C

    def test_to_dict_serialization(self, tmp_path):
        """to_dict returns a complete JSON-serializable dictionary with all report fields."""
        mp = tmp_path / "mp"
        mp.mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        report.marketplace_name = "test-mp"
        report.plugins_found = ["plugin-a"]
        report.submodules_found = ["plugin-a"]
        report.passed("marketplace_structure", "JSON valid", 5.0)
        d = report.to_dict()
        assert d["marketplace_name"] == "test-mp"
        assert d["plugins_found"] == ["plugin-a"]
        assert d["submodules_found"] == ["plugin-a"]
        assert "total_score" in d
        assert "grade" in d
        assert "grade_description" in d
        assert "categories" in d
        assert "marketplace_structure" in d["categories"]
        # Verify it is JSON-serializable
        json.dumps(d)


class TestParseGitmodules:
    """Tests for parse_gitmodules helper function."""

    def test_nonexistent_file_returns_empty_dict(self, tmp_path):
        """parse_gitmodules returns empty dict when the file does not exist."""
        result = parse_gitmodules(tmp_path / "nonexistent_gitmodules")
        assert result == {}

    def test_regex_fallback_parses_malformed_gitmodules(self, tmp_path):
        """parse_gitmodules falls back to regex when configparser cannot parse the file."""
        gitmodules_path = tmp_path / ".gitmodules"
        # Write content with NUL byte to make configparser fail, but with the
        # path/url lines immediately after the [submodule] header so the regex
        # pattern (which expects path=... right after header) can match them.
        gitmodules_path.write_bytes(b'\x00[submodule "beta"]\npath = beta\nurl = https://github.com/org/beta.git\n')
        result = parse_gitmodules(gitmodules_path)
        # The regex fallback should find the submodule
        assert "beta" in result
        assert result["beta"]["path"] == "beta"
        assert result["beta"]["url"] == "https://github.com/org/beta.git"

    def test_regex_fallback_handles_url_before_path(self, tmp_path):
        """Regex fallback must capture `path` even when `url` is written first (M2).

        url-before-path is perfectly legal git config. The old hardcoded
        path-then-url pattern returned path=None in this ordering, corrupting the
        submodule map and producing false 'missing submodule directory' findings.
        """
        gitmodules_path = tmp_path / ".gitmodules"
        # Leading NUL forces configparser to fail → exercises the regex fallback.
        gitmodules_path.write_bytes(
            b'\x00[submodule "beta"]\nurl = https://github.com/org/beta.git\npath = plugins/beta\n'
        )
        result = parse_gitmodules(gitmodules_path)
        assert "beta" in result
        assert result["beta"]["path"] == "plugins/beta"
        assert result["beta"]["url"] == "https://github.com/org/beta.git"

    def test_regex_fallback_strips_trailing_whitespace(self, tmp_path):
        """Regex fallback must not leak trailing whitespace into captured values (M2)."""
        gitmodules_path = tmp_path / ".gitmodules"
        gitmodules_path.write_bytes(
            b'\x00[submodule "baz"]\npath = plugins/baz   \nurl = https://github.com/org/baz.git  \n'
        )
        result = parse_gitmodules(gitmodules_path)
        assert result["baz"]["path"] == "plugins/baz"
        assert result["baz"]["url"] == "https://github.com/org/baz.git"

    def test_regex_fallback_handles_two_submodules(self, tmp_path):
        """Regex fallback must isolate each submodule's body so fields don't bleed across sections (M2)."""
        gitmodules_path = tmp_path / ".gitmodules"
        gitmodules_path.write_bytes(
            b'\x00[submodule "a"]\n\tpath = plugins/a\n\turl = https://github.com/org/a.git\n'
            b'[submodule "b"]\n\turl = https://github.com/org/b.git\n\tpath = plugins/b\n'
        )
        result = parse_gitmodules(gitmodules_path)
        assert result["a"] == {"path": "plugins/a", "url": "https://github.com/org/a.git"}
        assert result["b"] == {"path": "plugins/b", "url": "https://github.com/org/b.git"}


class TestLoadYamlFile:
    """Tests for load_yaml_file helper function."""

    def test_valid_yaml_loads_correctly(self, tmp_path):
        """load_yaml_file returns parsed dict for valid YAML content."""
        yaml_file = tmp_path / "test.yml"
        yaml_file.write_text(
            "name: test\non:\n  push:\njobs:\n  build:\n    runs-on: ubuntu-latest\n", encoding="utf-8"
        )
        result = load_yaml_file(yaml_file)
        assert result is not None
        assert result["name"] == "test"

    def test_invalid_yaml_returns_none(self, tmp_path):
        """load_yaml_file returns None when the YAML file has syntax errors."""
        yaml_file = tmp_path / "broken.yml"
        yaml_file.write_text("name: test\n  broken:\n- not: valid: yaml: {{{\n", encoding="utf-8")
        result = load_yaml_file(yaml_file)
        assert result is None


class TestCheckPythonSyntax:
    """Tests for check_python_syntax helper function."""

    def test_valid_python_returns_true(self, tmp_path):
        """check_python_syntax returns True for a syntactically valid Python file."""
        py_file = tmp_path / "valid.py"
        py_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
        assert check_python_syntax(py_file) is True

    def test_invalid_python_returns_false(self, tmp_path):
        """check_python_syntax returns False for a Python file with syntax errors."""
        py_file = tmp_path / "invalid.py"
        py_file.write_text("def broken(\n    return 'missing paren'\n", encoding="utf-8")
        assert check_python_syntax(py_file) is False


class TestValidatePluginWorkflows:
    """Tests for validate_plugin_workflows with real plugin submodule directories."""

    def test_plugin_with_complete_notify_workflow(self, tmp_path):
        """validate_plugin_workflows passes all checks when plugin has notify-marketplace.yml with push trigger and repository_dispatch."""
        mp = tmp_path / "marketplace"
        mp.mkdir()
        # Create .gitmodules with one plugin
        gitmodules_content = '[submodule "plugin-a"]\n\tpath = plugin-a\n\turl = https://github.com/org/plugin-a.git\n'
        (mp / ".gitmodules").write_text(gitmodules_content, encoding="utf-8")
        # Create plugin directory with workflow
        plugin_dir = mp / "plugin-a"
        plugin_dir.mkdir()
        wf_dir = plugin_dir / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        notify_content = "name: Notify Marketplace\non:\n  push:\n    branches: [main]\njobs:\n  notify:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: peter-evans/repository-dispatch@v2\n"
        (wf_dir / "notify-marketplace.yml").write_text(notify_content, encoding="utf-8")

        report = PipelineValidationReport(marketplace_path=mp)
        validate_plugin_workflows(mp, report)
        cat = report.categories["plugin_workflows"]
        passed_results = [r for r in cat.results if r.level == "PASSED"]
        # Should pass all 4 checks: workflows dir, notify workflow, push trigger, repository_dispatch
        assert len(passed_results) == 4

    def test_plugin_without_workflows_dir(self, tmp_path):
        """validate_plugin_workflows reports MAJOR when plugin lacks .github/workflows/ directory."""
        mp = tmp_path / "marketplace"
        mp.mkdir()
        gitmodules_content = '[submodule "plugin-b"]\n\tpath = plugin-b\n\turl = https://github.com/org/plugin-b.git\n'
        (mp / ".gitmodules").write_text(gitmodules_content, encoding="utf-8")
        # Create plugin dir without workflows
        (mp / "plugin-b").mkdir()

        report = PipelineValidationReport(marketplace_path=mp)
        validate_plugin_workflows(mp, report)
        cat = report.categories["plugin_workflows"]
        major_results = [r for r in cat.results if r.level == "MAJOR"]
        assert any("No plugins have .github/workflows/" in r.message for r in major_results)


class TestValidateSyncScripts:
    """Tests for validate_sync_scripts with real script files."""

    def test_complete_sync_scripts_setup(self, tmp_path):
        """validate_sync_scripts passes all checks with scripts/ dir, sync script, executable bit, and valid syntax."""
        mp = tmp_path / "marketplace"
        mp.mkdir()
        scripts_dir = mp / "scripts"
        scripts_dir.mkdir()
        sync_script = scripts_dir / "sync_marketplace_versions.py"
        sync_script.write_text("#!/usr/bin/env python3\nimport json\n\ndef sync():\n    pass\n", encoding="utf-8")
        sync_script.chmod(0o755)

        report = PipelineValidationReport(marketplace_path=mp)
        validate_sync_scripts(mp, report)
        cat = report.categories["sync_scripts"]
        passed_results = [r for r in cat.results if r.level == "PASSED"]
        # Should pass: scripts/ exists, sync script exists, is executable, valid syntax
        assert len(passed_results) == 4

    def test_sync_script_not_found_reports_major(self, tmp_path):
        """validate_sync_scripts reports MAJOR when scripts/ exists but sync script is missing."""
        mp = tmp_path / "marketplace"
        mp.mkdir()
        (mp / "scripts").mkdir()

        report = PipelineValidationReport(marketplace_path=mp)
        validate_sync_scripts(mp, report)
        cat = report.categories["sync_scripts"]
        major_results = [r for r in cat.results if r.level == "MAJOR"]
        assert any("sync_marketplace_versions.py not found" in r.message for r in major_results)


class TestValidateDocumentation:
    """Tests for validate_documentation with real README files."""

    def test_readme_with_mermaid_diagram_and_install_instructions(self, tmp_path):
        """validate_documentation passes all checks for README with mermaid diagram and installation section."""
        mp = tmp_path / "marketplace"
        mp.mkdir()
        readme_content = (
            "# My Marketplace\n\n"
            "## Architecture\n\n"
            "```mermaid\ngraph TD\n  A-->B\n```\n\n"
            "## Installation\n\n"
            "claude plugin marketplace add my-marketplace\n"
        )
        (mp / "README.md").write_text(readme_content, encoding="utf-8")

        report = PipelineValidationReport(marketplace_path=mp)
        validate_documentation(mp, report)
        cat = report.categories["documentation"]
        passed_results = [r for r in cat.results if r.level == "PASSED"]
        assert len(passed_results) == 3  # exists, diagram, install

    def test_readme_without_diagram_or_install(self, tmp_path):
        """validate_documentation reports MINOR for README missing architecture diagram and installation instructions."""
        mp = tmp_path / "marketplace"
        mp.mkdir()
        (mp / "README.md").write_text("# Marketplace\n\nJust a bare README.\n", encoding="utf-8")

        report = PipelineValidationReport(marketplace_path=mp)
        validate_documentation(mp, report)
        cat = report.categories["documentation"]
        minor_results = [r for r in cat.results if r.level == "MINOR"]
        assert any("architecture diagram" in r.message for r in minor_results)
        assert any("installation instructions" in r.message for r in minor_results)


class TestFormatTextReport:
    """Tests for format_text_report output generation."""

    def test_format_text_report_contains_score_and_grade(self, tmp_path):
        """format_text_report produces text containing the overall score, grade, and category breakdown."""
        mp = tmp_path / "mp"
        mp.mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        report.marketplace_name = "test-marketplace"
        report.passed("marketplace_structure", "JSON valid", 5.0)
        report.critical("submodule_health", "No .gitmodules", 20.0)
        text = format_text_report(report, verbose=True)
        assert "MARKETPLACE PIPELINE VALIDATION REPORT" in text
        assert "test-marketplace" in text
        assert "OVERALL SCORE:" in text
        assert "OVERALL SCORE:" in text
        assert "CATEGORY BREAKDOWN:" in text
        assert "SUMMARY:" in text
        # Verbose mode should show passed results
        assert "[OK] JSON valid" in text
        # Should show critical issues
        assert "CRITICAL" in text

    def test_format_text_report_non_verbose_hides_passed_details(self, tmp_path):
        """format_text_report in non-verbose mode omits passed result details but shows issues."""
        mp = tmp_path / "mp"
        mp.mkdir()
        report = PipelineValidationReport(marketplace_path=mp)
        report.passed("marketplace_structure", "All checks passed", 25.0)
        report.major("documentation", "README missing diagram", 4.0, suggestion="Add mermaid diagram")
        text = format_text_report(report, verbose=False)
        # Non-verbose should not show "[OK] All checks passed" line
        assert "[OK] All checks passed" not in text
        # But should still show the MAJOR issue
        assert "README missing diagram" in text
        assert "Fix: Add mermaid diagram" in text


class TestMainCLI:
    """Tests for the main() CLI entry point."""

    def test_main_with_valid_marketplace_path(self, tmp_path):
        """main() returns an exit code and prints report for a valid marketplace directory."""
        mp = tmp_path / "marketplace"
        mp.mkdir()
        (mp / "marketplace.json").write_text(
            json.dumps({"name": "cli-test", "version": "1.0.0", "plugins": []}), encoding="utf-8"
        )
        (mp / ".gitmodules").write_text("# empty\n", encoding="utf-8")
        with patch("sys.argv", ["validate_marketplace_pipeline.py", str(mp)]):
            exit_code = main()
        assert isinstance(exit_code, int)
        assert exit_code in (0, 1, 2, 3)

    def test_main_with_json_output(self, tmp_path, capsys):
        """main() with --json flag outputs valid JSON to stdout."""
        mp = tmp_path / "marketplace"
        mp.mkdir()
        (mp / "marketplace.json").write_text(
            json.dumps({"name": "json-test", "version": "1.0.0", "plugins": []}), encoding="utf-8"
        )
        (mp / ".gitmodules").write_text("# empty\n", encoding="utf-8")
        with patch("sys.argv", ["validate_marketplace_pipeline.py", str(mp), "--json"]):
            main()
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["marketplace_name"] == "json-test"
        assert "total_score" in parsed
        assert "grade" in parsed

    def test_main_with_nonexistent_path(self, tmp_path, capsys):
        """main() returns EXIT_MINOR when the provided path does not exist."""
        fake_path = tmp_path / "does-not-exist"
        with patch("sys.argv", ["validate_marketplace_pipeline.py", str(fake_path)]):
            exit_code = main()
        assert exit_code == 3  # EXIT_MINOR
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_strict_flag_accepted_and_is_a_noop(self, tmp_path):
        """main() must accept --strict (generated CI passes it) and treat it as a no-op (M1).

        The score-band model has no NIT tier, so --strict must not error AND must
        produce the same exit code as a plain run — proving it is a coherent
        no-op rather than a silently-broken flag.
        """
        mp = tmp_path / "marketplace"
        mp.mkdir()
        (mp / "marketplace.json").write_text(
            json.dumps({"name": "strict-test", "version": "1.0.0", "plugins": []}), encoding="utf-8"
        )
        (mp / ".gitmodules").write_text("# empty\n", encoding="utf-8")

        with patch("sys.argv", ["validate_marketplace_pipeline.py", str(mp)]):
            plain_exit = main()
        with patch("sys.argv", ["validate_marketplace_pipeline.py", str(mp), "--strict"]):
            strict_exit = main()
        assert strict_exit == plain_exit
        assert strict_exit in (0, 1, 2, 3)


class TestMarketplaceStructureVersionMismatch:
    """Tests for version mismatch detection in validate_marketplace_structure."""

    def test_version_mismatch_between_marketplace_and_plugin_json(self, tmp_path):
        """validate_marketplace_structure reports MAJOR when plugin.json version differs from marketplace.json version."""
        mp = tmp_path / "marketplace"
        mp.mkdir()
        marketplace_data = {
            "name": "ver-test",
            "version": "1.0.0",
            "plugins": [{"name": "plugin-a", "version": "2.0.0"}],
        }
        (mp / "marketplace.json").write_text(json.dumps(marketplace_data), encoding="utf-8")
        gitmodules_content = '[submodule "plugin-a"]\n\tpath = plugin-a\n\turl = https://github.com/org/plugin-a.git\n'
        (mp / ".gitmodules").write_text(gitmodules_content, encoding="utf-8")
        # Create plugin dir with plugin.json that has a DIFFERENT version
        plugin_dir = mp / "plugin-a"
        plugin_dir.mkdir()
        cp_dir = plugin_dir / ".claude-plugin"
        cp_dir.mkdir()
        (cp_dir / "plugin.json").write_text(json.dumps({"name": "plugin-a", "version": "1.5.0"}), encoding="utf-8")

        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_structure(mp, report)
        cat = report.categories["marketplace_structure"]
        major_results = [r for r in cat.results if r.level == "MAJOR"]
        assert any("Version mismatch" in r.message for r in major_results)
