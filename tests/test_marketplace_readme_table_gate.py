"""Tests for the marketplace README-plugin-table pipeline gate.

Covers the two checks added to validate_marketplace_pipeline.py that detect a
marketplace repo still on the OLD pipeline:
- validate_marketplace_workflows: update-submodules.yml must call
  scripts/render_readme_table.py to regenerate the README plugin table.
- validate_documentation: README.md must carry the
  <!-- PLUGIN-VERSIONS-START/END --> markers that script writes into.

Both checks fire at INFO level with ZERO score weight: the generated README
table is a CPV canon convention, not a plugin-marketplaces.md requirement, so
it must never move exit_code() (computed from the weighted score alone) or
retro-penalise a marketplace that predates the convention.

Both checks use the "marketplace_workflows" / "documentation" categories,
which is registered in CATEGORY_WEIGHTS - a mistyped category would silently
no-op (PipelineValidationReport.add() drops any unregistered category), so
each test also asserts the category is a known one.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_marketplace_pipeline import (
    CATEGORY_WEIGHTS,
    PipelineValidationReport,
    format_text_report,
    validate_documentation,
    validate_marketplace_workflows,
)

_PIPELINE_SOURCE = Path(__file__).resolve().parent.parent / "scripts" / "validate_marketplace_pipeline.py"

_TRIGGERS = "on:\n  repository_dispatch:\n    types: [plugin-updated]\n  workflow_dispatch:\n"

_WORKFLOW_WITHOUT_TABLE_RENDER = (
    "name: Update Submodules\n"
    f"{_TRIGGERS}"
    "jobs:\n"
    "  update:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - run: git submodule update --remote\n"
)

_WORKFLOW_WITH_TABLE_RENDER = (
    "name: Update Submodules\n"
    f"{_TRIGGERS}"
    "jobs:\n"
    "  update:\n"
    "    runs-on: ubuntu-latest\n"
    "    steps:\n"
    "      - run: git submodule update --remote\n"
    "      - run: python scripts/render_readme_table.py\n"
)

_README_WITHOUT_MARKERS = "# My Marketplace\n\nA marketplace of plugins.\n\n## Installation\n\nclaude plugin marketplace add owner/repo\n"

_README_WITH_MARKERS = (
    "# My Marketplace\n\n"
    "A marketplace of plugins.\n\n"
    "## Plugins\n\n"
    "<!-- PLUGIN-VERSIONS-START -->\n"
    "| Plugin | Version |\n"
    "|---|---|\n"
    "| foo | 1.0.0 |\n"
    "<!-- PLUGIN-VERSIONS-END -->\n\n"
    "## Installation\n\n"
    "claude plugin marketplace add owner/repo\n"
)


def _make_marketplace(tmp_path, *, workflow_content=None, readme_content=None):
    """Build a minimal marketplace dir with an optional workflow and README."""
    mp = tmp_path / "marketplace"
    mp.mkdir()
    if workflow_content is not None:
        wf_dir = mp / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "update-submodules.yml").write_text(workflow_content, encoding="utf-8")
    if readme_content is not None:
        (mp / "README.md").write_text(readme_content, encoding="utf-8")
    return mp


def test_categories_are_registered():
    """Both checks' categories must be real CATEGORY_WEIGHTS keys, or add() silently no-ops."""
    assert "marketplace_workflows" in CATEGORY_WEIGHTS
    assert "documentation" in CATEGORY_WEIGHTS


class TestWorkflowAbsentDoesNotCrash:
    """The table check reads workflow_content, which only exists when the workflow parses.

    Regression: the check was first written at function level, outside the else-branch
    that binds workflow_content, so validate_marketplace_workflows raised NameError on
    any marketplace whose update workflow was missing or had unparseable YAML. Every
    fixture above supplies a valid workflow, so the crash path was never exercised.
    """

    def test_no_workflow_at_all_does_not_raise(self, tmp_path):
        """Non-discriminating boundary control: an absent workflows dir returns early.

        This case does NOT exercise the crash — with no workflows directory at all,
        the function returns before Check 5b (and before `workflow_content` would even
        be assigned), so it passes against both the fixed AND the broken code. It is
        kept only as a boundary control; the discriminating regression test is
        test_unparseable_workflow_does_not_raise below, which mutation-testing showed
        actually kills the reintroduced NameError.
        """
        mp = _make_marketplace(tmp_path, readme_content=_README_WITH_MARKERS)
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_workflows(mp, report)  # must not raise NameError
        cat = report.categories["marketplace_workflows"]
        # The absent workflow earns its own finding; the table check is not added on
        # top of it, because there is no workflow to add a render step to.
        assert not any("README plugin table" in r.message for r in cat.results)

    def test_unparseable_workflow_does_not_raise(self, tmp_path):
        """A workflow whose YAML fails to parse must still validate, not crash.

        This is the discriminating regression test for the NameError bug: an
        unparseable workflow takes the `workflow_data is None` MAJOR branch and
        returns before Check 5b's `workflow_content` read is ever reached, so its
        only real assertion is "does not raise" — it does not, and cannot, exercise
        the README-table check itself (see TestReadmeTableWorkflowCheck for that).
        """
        mp = _make_marketplace(tmp_path, workflow_content="name: [unclosed\n  - {{{\n")
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_workflows(mp, report)  # must not raise NameError
        cat = report.categories["marketplace_workflows"]
        assert not any("README plugin table" in r.message for r in cat.results)


class TestReadmeTableWorkflowCheck:
    """update-submodules.yml must call scripts/render_readme_table.py."""

    def test_missing_table_render_call_fires_info(self, tmp_path):
        """A workflow that never regenerates the README table draws an INFO finding."""
        mp = _make_marketplace(tmp_path, workflow_content=_WORKFLOW_WITHOUT_TABLE_RENDER)
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_workflows(mp, report)
        cat = report.categories["marketplace_workflows"]
        infos = [r for r in cat.results if r.level == "INFO"]
        assert any("render_readme_table.py" in r.message for r in infos)

    def test_table_render_call_present_is_clean(self, tmp_path):
        """Positive control: a workflow that DOES call render_readme_table.py produces no finding."""
        mp = _make_marketplace(tmp_path, workflow_content=_WORKFLOW_WITH_TABLE_RENDER)
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_workflows(mp, report)
        cat = report.categories["marketplace_workflows"]
        infos = [r for r in cat.results if r.level == "INFO"]
        assert not any("render_readme_table.py" in r.message for r in infos)
        passed = [r for r in cat.results if r.level == "PASSED"]
        assert any("render_readme_table.py" in r.message.lower() or "readme plugin table" in r.message.lower() for r in passed)

    def test_workflow_check_is_zero_weight(self, tmp_path):
        """The workflow table check must never move the weighted score, in either state."""
        (tmp_path / "missing").mkdir()
        mp_missing = _make_marketplace(tmp_path / "missing", workflow_content=_WORKFLOW_WITHOUT_TABLE_RENDER)
        report_missing = PipelineValidationReport(marketplace_path=mp_missing)
        validate_marketplace_workflows(mp_missing, report_missing)
        cat_missing = report_missing.categories["marketplace_workflows"]
        table_findings_missing = [r for r in cat_missing.results if "render_readme_table.py" in r.message]
        assert table_findings_missing
        assert all(r.points_possible == 0.0 for r in table_findings_missing)

        (tmp_path / "present").mkdir()
        mp_present = _make_marketplace(tmp_path / "present", workflow_content=_WORKFLOW_WITH_TABLE_RENDER)
        report_present = PipelineValidationReport(marketplace_path=mp_present)
        validate_marketplace_workflows(mp_present, report_present)
        cat_present = report_present.categories["marketplace_workflows"]
        table_findings_present = [
            r
            for r in cat_present.results
            if "render_readme_table.py" in r.message.lower() or "readme plugin table" in r.message.lower()
        ]
        assert table_findings_present
        assert all(r.points_possible == 0.0 for r in table_findings_present)


class TestReadmeTableMarkersCheck:
    """README.md must carry the PLUGIN-VERSIONS-START/END markers."""

    def test_missing_markers_fires_info(self, tmp_path):
        """A README without the markers draws an INFO finding."""
        mp = _make_marketplace(tmp_path, readme_content=_README_WITHOUT_MARKERS)
        report = PipelineValidationReport(marketplace_path=mp)
        validate_documentation(mp, report)
        cat = report.categories["documentation"]
        infos = [r for r in cat.results if r.level == "INFO"]
        assert any("PLUGIN-VERSIONS" in r.message for r in infos)

    def test_markers_present_is_clean(self, tmp_path):
        """Positive control: a README carrying both markers produces no marker finding."""
        mp = _make_marketplace(tmp_path, readme_content=_README_WITH_MARKERS)
        report = PipelineValidationReport(marketplace_path=mp)
        validate_documentation(mp, report)
        cat = report.categories["documentation"]
        infos = [r for r in cat.results if r.level == "INFO"]
        assert not any("PLUGIN-VERSIONS" in r.message for r in infos)
        passed = [r for r in cat.results if r.level == "PASSED"]
        assert any("PLUGIN-VERSIONS" in r.message for r in passed)

    def test_markers_check_is_zero_weight(self, tmp_path):
        """The markers check must never move the weighted score, in either state."""
        (tmp_path / "missing").mkdir()
        mp_missing = _make_marketplace(tmp_path / "missing", readme_content=_README_WITHOUT_MARKERS)
        report_missing = PipelineValidationReport(marketplace_path=mp_missing)
        validate_documentation(mp_missing, report_missing)
        cat_missing = report_missing.categories["documentation"]
        marker_findings_missing = [r for r in cat_missing.results if "PLUGIN-VERSIONS" in r.message]
        assert marker_findings_missing
        assert all(r.points_possible == 0.0 for r in marker_findings_missing)


class TestReadmeTableSourceIndentationGuard:
    """Pins the source-level indentation defect fixed in Check 5b (v5.17.0)."""

    def test_render_readme_table_check_is_inside_the_else_branch(self):
        """The render_readme_table.py check must sit at >= 8 spaces of indentation.

        Regression: the check was first written at 4-space (function-level) indentation,
        outside the `else:` branch that binds `workflow_content` (assigned only when the
        workflow YAML parses). At 4 spaces it executes unconditionally and raises a bare
        NameError the instant a marketplace's update workflow is missing or unparseable —
        every existing fixture supplied a valid workflow, so the crash path went untested
        until TestWorkflowAbsentDoesNotCrash was added. This test reads the real source
        file as text and pins the indentation directly, so a future edit that dedents the
        check back to function level fails loudly instead of silently reintroducing the
        NameError.
        """
        source_path = _PIPELINE_SOURCE
        source = source_path.read_text(encoding="utf-8")
        for line in source.splitlines():
            if 'if not re.search(r"render_readme_table\\.py"' in line:
                indent = len(line) - len(line.lstrip(" "))
                assert indent >= 8, (
                    f"render_readme_table.py check indented at {indent} spaces, expected >= 8 "
                    "(must stay inside the else-branch that binds workflow_content)"
                )
                return
        raise AssertionError("could not find the render_readme_table.py check in the source file")


class TestAdvisoriesAreVisibleInTheTextReport:
    """An INFO finding must REACH A HUMAN, not just --json.

    Regression: format_text_report filtered INFO out of the per-category `issues` list,
    skipped any category holding only INFO, and omitted INFO from the SUMMARY line — while
    --verbose added PASSED and still not INFO. Every advisory in this module (the
    workflow-hardening set, and both README-table canon checks) was therefore emitted to
    the JSON output and to nothing anyone reads. That is the specific way a zero-weight
    WARN phase fails: the check exists, costs nothing, and reports nothing.
    """

    def test_info_finding_appears_in_the_non_verbose_text_report(self, tmp_path):
        """The advisory's own message text must be present in the default (non-verbose) report."""
        report = PipelineValidationReport(marketplace_path=tmp_path)
        report.info("documentation", "advisory-sentinel-xyz", "README.md")
        text = format_text_report(report, verbose=False)
        assert "advisory-sentinel-xyz" in text
        assert "README.md" in text

    def test_info_is_reported_separately_from_the_blocking_tiers(self, tmp_path):
        """Positive control: the advisory is counted as INFO, never as CRITICAL/MAJOR/MINOR.

        Without this, "make advisories visible" could be satisfied by promoting them into a
        blocking tier — which is exactly the outcome the zero-weight design rejects.
        """
        report = PipelineValidationReport(marketplace_path=tmp_path)
        report.info("documentation", "advisory-sentinel-xyz", "README.md")
        text = format_text_report(report, verbose=False)
        summary = next(line for line in text.splitlines() if line.startswith("SUMMARY: "))
        assert "0 CRITICAL" in summary
        assert "0 MAJOR" in summary
        assert "0 MINOR" in summary
        assert "1 INFO" in summary

    def test_summary_marker_prefix_survives(self, tmp_path):
        """`SUMMARY: ` is a cross-file proof-of-run marker (test_wave2_generator_publish_gate).

        The emitted marketplace pipeline greps for it to prove the validator actually ran, so
        a rename here silently downgrades every run to "infra failure".
        """
        report = PipelineValidationReport(marketplace_path=tmp_path)
        report.info("documentation", "advisory-sentinel-xyz", "README.md")
        assert "SUMMARY: " in format_text_report(report, verbose=False)

    def test_a_blocking_finding_still_renders_with_its_own_icon(self, tmp_path):
        """Control: splitting INFO out of `issues` must not disturb the blocking path."""
        report = PipelineValidationReport(marketplace_path=tmp_path)
        report.major("documentation", "blocking-sentinel-xyz", 2.0, "README.md")
        text = format_text_report(report, verbose=False)
        assert "[!] MAJOR: blocking-sentinel-xyz" in text

    def test_the_readme_marker_advisory_is_visible_end_to_end(self, tmp_path):
        """The user-facing claim: a marketplace on the old pipeline is TOLD SO in the report."""
        mp = _make_marketplace(tmp_path, readme_content=_README_WITHOUT_MARKERS)
        report = PipelineValidationReport(marketplace_path=mp)
        validate_documentation(mp, report)
        text = format_text_report(report, verbose=False)
        assert "PLUGIN-VERSIONS" in text

    def test_the_workflow_table_advisory_is_visible_end_to_end(self, tmp_path):
        """Same for the workflow half — the pre-existing advisories were invisible too."""
        mp = _make_marketplace(tmp_path, workflow_content=_WORKFLOW_WITHOUT_TABLE_RENDER)
        report = PipelineValidationReport(marketplace_path=mp)
        validate_marketplace_workflows(mp, report)
        text = format_text_report(report, verbose=False)
        assert "render_readme_table.py" in text
