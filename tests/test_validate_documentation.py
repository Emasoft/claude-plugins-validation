#!/usr/bin/env python3
"""
Tests for validate_documentation.py

Tests the 13 documentation validation rules:
1. README.md should exist at plugin root
2. README should contain installation instructions
3. README should contain usage examples
4. README should contain description section
5. README should have proper markdown formatting
6. No broken internal links
7. CHANGELOG.md recommended
8. Heading hierarchy should have no skips
9. Code blocks should be closed
10. Code blocks should have language tags
11. List formatting should be proper
12. Table structure should be valid
13. Image references should be valid
"""

from __future__ import annotations

from pathlib import Path

# Import will fail until module is created
from validate_documentation import (
    DocumentationValidationReport,
    validate_broken_links,
    validate_changelog_exists,
    validate_code_block_closed,
    validate_code_block_language_tags,
    validate_description_section,
    validate_documentation,
    validate_heading_hierarchy,
    validate_image_references,
    validate_installation_section,
    validate_list_formatting,
    validate_readme_exists,
    validate_table_structure,
    validate_usage_section,
)


class TestDocumentationValidationReport:
    """Tests for DocumentationValidationReport class."""

    def test_report_inherits_validation_report(self):
        """DocumentationValidationReport should inherit from ValidationReport."""
        report = DocumentationValidationReport(plugin_path="/test/path")
        assert hasattr(report, "results")
        assert hasattr(report, "add")
        assert hasattr(report, "passed")
        assert hasattr(report, "critical")

    def test_report_stores_plugin_path(self):
        """Report should store the plugin path."""
        report = DocumentationValidationReport(plugin_path="/some/plugin")
        assert report.plugin_path == "/some/plugin"


class TestRule1ReadmeExists:
    """Tests for Rule 1: README.md should exist at plugin root."""

    def test_readme_exists_passes(self, tmp_path: Path):
        """When README.md exists, validation should pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# My Plugin\nDescription here.")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_readme_exists(tmp_path, report)
        assert not report.has_critical
        assert any(r.level == "PASSED" and "README.md" in r.message for r in report.results)

    def test_readme_missing_warning(self, tmp_path: Path):
        """When README.md is missing, should report WARNING (advisory, not blocking).

        Per TRDD-021250b5: a missing README is a documentation-quality matter,
        not runtime breakage or Anthropic-invalidity, so it is advisory — a
        README-less plugin is VALID with a warning.
        """
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_readme_exists(tmp_path, report)
        assert not report.has_critical
        assert any("README.md" in r.message and r.level == "WARNING" for r in report.results)


class TestRule2InstallationSection:
    """Tests for Rule 2: README should contain installation instructions."""

    def test_installation_section_exists_passes(self, tmp_path: Path):
        """README with Installation section should pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\n## Installation\nRun `npm install`")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_installation_section(tmp_path, report)
        assert not report.has_major
        assert any(r.level == "PASSED" and "installation" in r.message.lower() for r in report.results)

    def test_installation_section_missing_warning(self, tmp_path: Path):
        """README without Installation section should report WARNING (advisory, non-blocking)."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\nJust a description.")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_installation_section(tmp_path, report)
        assert not report.has_major
        assert report.has_warning
        assert any("installation" in r.message.lower() and r.level == "WARNING" for r in report.results)

    def test_installation_alternate_headings(self, tmp_path: Path):
        """README with 'Getting Started' or 'Setup' should also pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\n## Getting Started\nFollow these steps.")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_installation_section(tmp_path, report)
        assert not report.has_major


class TestRule3UsageSection:
    """Tests for Rule 3: README should contain usage examples."""

    def test_usage_section_exists_passes(self, tmp_path: Path):
        """README with Usage section should pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\n## Usage\n```python\nimport plugin\n```")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_usage_section(tmp_path, report)
        assert not report.has_major

    def test_usage_section_missing_warning(self, tmp_path: Path):
        """README without Usage section should report WARNING (advisory, non-blocking)."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\nNo usage info here.")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_usage_section(tmp_path, report)
        assert not report.has_major
        assert report.has_warning


class TestRule4DescriptionSection:
    """Tests for Rule 4: README should contain description section."""

    def test_description_exists_passes(self, tmp_path: Path):
        """README with description content should pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\n\nThis plugin does amazing things.\n\n## Installation")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_description_section(tmp_path, report)
        assert not report.has_major

    def test_description_missing_warning(self, tmp_path: Path):
        """README without description should report WARNING (advisory, non-blocking)."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\n## Installation")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_description_section(tmp_path, report)
        assert not report.has_major
        assert report.has_warning


class TestRule5MarkdownFormatting:
    """Tests for Rule 5: README should have proper markdown formatting.

    Note: This is a meta-check covered by rules 8-12.
    The main function should aggregate formatting issues.
    """

    pass  # Covered by individual formatting rule tests


class TestRule6BrokenLinks:
    """Tests for Rule 6: No broken internal links."""

    def test_valid_links_pass(self, tmp_path: Path):
        """Valid internal links should pass."""
        readme = tmp_path / "README.md"
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("# Guide")
        readme.write_text("# Plugin\nSee [guide](docs/guide.md)")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_broken_links(tmp_path, report)
        assert not report.has_major

    def test_broken_links_major(self, tmp_path: Path):
        """Broken internal links should report MAJOR."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\nSee [missing](docs/nonexistent.md)")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_broken_links(tmp_path, report)
        assert report.has_major
        assert any("nonexistent.md" in r.message for r in report.results)

    def test_external_links_ignored(self, tmp_path: Path):
        """External URLs should not be checked."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\nSee [docs](https://example.com/docs)")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_broken_links(tmp_path, report)
        assert not report.has_major


class TestRule7Changelog:
    """Tests for Rule 7: CHANGELOG.md recommended."""

    def test_changelog_exists_passes(self, tmp_path: Path):
        """When CHANGELOG.md exists, validation should pass."""
        (tmp_path / "CHANGELOG.md").write_text("# Changelog\n## 1.0.0")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_changelog_exists(tmp_path, report)
        assert any(r.level == "PASSED" and "CHANGELOG" in r.message for r in report.results)

    def test_changelog_missing_warning(self, tmp_path: Path):
        """When CHANGELOG.md is missing, should report WARNING (advisory, non-blocking)."""
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_changelog_exists(tmp_path, report)
        assert not report.has_minor
        assert report.has_warning
        assert any("CHANGELOG" in r.message and r.level == "WARNING" for r in report.results)


class TestRule8HeadingHierarchy:
    """Tests for Rule 8: Heading hierarchy should have no skips."""

    def test_valid_hierarchy_passes(self, tmp_path: Path):
        """Proper heading hierarchy (h1 -> h2 -> h3) should pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# Title\n## Section\n### Subsection\n## Another")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_heading_hierarchy(tmp_path, report)
        assert not report.has_minor

    def test_skipped_heading_warning(self, tmp_path: Path):
        """Skipped heading level (h1 -> h3) should report WARNING (advisory, non-blocking)."""
        readme = tmp_path / "README.md"
        readme.write_text("# Title\n### Subsection without h2")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_heading_hierarchy(tmp_path, report)
        assert not report.has_minor
        assert report.has_warning
        assert any("heading" in r.message.lower() and "skip" in r.message.lower() for r in report.results)


class TestRule9CodeBlocksClosed:
    """Tests for Rule 9: Code blocks should be closed."""

    def test_closed_code_blocks_pass(self, tmp_path: Path):
        """Properly closed code blocks should pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# Code\n```python\nprint('hi')\n```")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_code_block_closed(tmp_path, report)
        assert not report.has_major

    def test_unclosed_code_block_major(self, tmp_path: Path):
        """Unclosed code block should report MAJOR."""
        readme = tmp_path / "README.md"
        readme.write_text("# Code\n```python\nprint('hi')\nno closing fence")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_code_block_closed(tmp_path, report)
        assert report.has_major
        assert any("unclosed" in r.message.lower() or "code block" in r.message.lower() for r in report.results)


class TestRule10CodeBlockLanguageTags:
    """Tests for Rule 10: Code blocks should have language tags."""

    def test_tagged_code_blocks_pass(self, tmp_path: Path):
        """Code blocks with language tags should pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# Code\n```python\nprint('hi')\n```")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_code_block_language_tags(tmp_path, report)
        assert not report.has_minor

    def test_untagged_code_block_warning(self, tmp_path: Path):
        """Code block without language tag should report WARNING (advisory, non-blocking)."""
        readme = tmp_path / "README.md"
        readme.write_text("# Code\n```\nprint('hi')\n```")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_code_block_language_tags(tmp_path, report)
        assert not report.has_minor
        assert report.has_warning
        assert any("language" in r.message.lower() for r in report.results)


class TestRule11ListFormatting:
    """Tests for Rule 11: List formatting should be proper."""

    def test_valid_list_passes(self, tmp_path: Path):
        """Properly formatted lists should pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# List\n- Item 1\n- Item 2\n  - Nested")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_list_formatting(tmp_path, report)
        assert not report.has_minor

    def test_inconsistent_list_markers_warning(self, tmp_path: Path):
        """Inconsistent list markers should report WARNING (advisory, non-blocking)."""
        readme = tmp_path / "README.md"
        readme.write_text("# List\n- Item 1\n* Item 2\n+ Item 3")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_list_formatting(tmp_path, report)
        assert not report.has_minor
        assert report.has_warning


class TestRule12TableStructure:
    """Tests for Rule 12: Table structure should be valid."""

    def test_valid_table_passes(self, tmp_path: Path):
        """Properly formatted table should pass."""
        readme = tmp_path / "README.md"
        readme.write_text("# Table\n| Col1 | Col2 |\n|------|------|\n| A | B |")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_table_structure(tmp_path, report)
        assert not report.has_minor

    def test_malformed_table_warning(self, tmp_path: Path):
        """Malformed table (mismatched columns) should report WARNING (advisory, non-blocking)."""
        readme = tmp_path / "README.md"
        readme.write_text("# Table\n| Col1 | Col2 |\n|------|\n| A | B | C |")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_table_structure(tmp_path, report)
        assert not report.has_minor
        assert report.has_warning


class TestTildeFencesAndLonePipe:
    """m6/n6 regression: structural rules 9-12 must honor ~~~ fences and not
    treat a lone '|' as a table row."""

    def test_tilde_fence_closed_no_false_unclosed(self, tmp_path: Path):
        """m6: a properly closed ~~~ fence must NOT report 'unclosed code block'.

        Before the fix the code-block-closed rule tracked only ``` via
        startswith, so the closing ~~~ was seen as body text and the block
        looked unterminated → false MAJOR (which can mark a plugin INVALID).
        """
        readme = tmp_path / "README.md"
        readme.write_text("# Doc\n\n~~~python\nprint('hi')\n~~~\n\nAfter.\n")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_code_block_closed(tmp_path, report)
        assert not report.has_major, [r.message for r in report.results if r.level == "MAJOR"]
        assert any("properly closed" in r.message for r in report.results if r.level == "PASSED")

    def test_genuinely_unclosed_tilde_fence_still_major(self, tmp_path: Path):
        """m6 two-sided: a genuinely unterminated ~~~ fence is still a MAJOR."""
        readme = tmp_path / "README.md"
        readme.write_text("# Doc\n\n~~~python\nprint('hi')\nno closing fence here\n")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_code_block_closed(tmp_path, report)
        assert report.has_major
        assert any("Unclosed code block" in r.message for r in report.results if r.level == "MAJOR")

    def test_tagged_tilde_fence_no_language_warning(self, tmp_path: Path):
        """m6: a ~~~ fence WITH a language tag must not warn about a missing tag."""
        readme = tmp_path / "README.md"
        readme.write_text("# Doc\n\n~~~python\nprint('hi')\n~~~\n")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_code_block_language_tags(tmp_path, report)
        assert not report.has_warning, [r.message for r in report.results if r.level == "WARNING"]

    def test_untagged_tilde_fence_warns(self, tmp_path: Path):
        """m6 two-sided: a ~~~ fence WITHOUT a language tag still warns."""
        readme = tmp_path / "README.md"
        readme.write_text("# Doc\n\n~~~\nprint('hi')\n~~~\n")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_code_block_language_tags(tmp_path, report)
        assert report.has_warning
        assert any("language" in r.message.lower() for r in report.results)

    def test_list_markers_inside_tilde_fence_ignored(self, tmp_path: Path):
        """m6: list-like lines inside a ~~~ fence must NOT be scanned as list
        markers (so mixed markers inside code don't trigger a false warning)."""
        readme = tmp_path / "README.md"
        # Outside fence: only '-' markers. Inside fence: '*' and '+' — must be ignored.
        readme.write_text("# Doc\n\n- real item\n- another\n\n~~~text\n* fake\n+ fake\n~~~\n")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_list_formatting(tmp_path, report)
        assert not report.has_warning, [r.message for r in report.results if r.level == "WARNING"]

    def test_table_rows_inside_tilde_fence_ignored(self, tmp_path: Path):
        """m6: table-like lines inside a ~~~ fence must NOT be scanned as a table
        (so a deliberately ragged ASCII table in code doesn't false-warn)."""
        readme = tmp_path / "README.md"
        readme.write_text("# Doc\n\n~~~text\n| a | b |\n| c |\n~~~\n\nProse.\n")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_table_structure(tmp_path, report)
        assert not report.has_warning, [r.message for r in report.results if r.level == "WARNING"]

    def test_lone_pipe_line_not_treated_as_table(self, tmp_path: Path):
        """n6: a line that is exactly '|' must not open a 1-column table context.

        Uses a lone '|' as the final character (no trailing newline) so no
        following line resets the table state — that is the shape that, before
        the fix, made the lone '|' a 1-column 'table' and emitted a false
        'Table structure is valid' PASSED.
        """
        readme = tmp_path / "README.md"
        readme.write_text("# Doc\n\nsome prose\n\n|")  # lone '|' as last char, no newline
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_table_structure(tmp_path, report)
        assert not report.has_warning
        assert not any("Table structure is valid" in r.message for r in report.results if r.level == "PASSED"), (
            "lone '|' was wrongly treated as a valid 1-column table"
        )

    def test_real_single_column_table_still_recognized(self, tmp_path: Path):
        """n6 two-sided: a legitimate single-column table ('| value |') must be
        recognized — the len>=2 guard drops only the degenerate lone '|', not
        real 1-col rows. Table is at EOF (no trailing newline) so the table
        state survives to emit the PASSED line, proving the rows parsed."""
        readme = tmp_path / "README.md"
        readme.write_text("# Doc\n\n| Header |\n|--------|\n| value |")  # at EOF, no newline
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_table_structure(tmp_path, report)
        # No column-mismatch warning — all rows have 1 column.
        assert not report.has_warning, [r.message for r in report.results if r.level == "WARNING"]
        # The table WAS recognized (PASSED emitted), proving the len>=2 guard
        # did not drop the real 1-column rows.
        assert any("Table structure is valid" in r.message for r in report.results if r.level == "PASSED")


class TestRule13ImageReferences:
    """Tests for Rule 13: Image references should be valid."""

    def test_valid_images_pass(self, tmp_path: Path):
        """Valid image references should pass."""
        readme = tmp_path / "README.md"
        images = tmp_path / "images"
        images.mkdir()
        (images / "logo.png").write_bytes(b"PNG")
        readme.write_text("# Plugin\n![Logo](images/logo.png)")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_image_references(tmp_path, report)
        assert not report.has_major

    def test_missing_image_major(self, tmp_path: Path):
        """Missing referenced image should report MAJOR."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\n![Logo](images/missing.png)")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_image_references(tmp_path, report)
        assert report.has_major

    def test_external_images_ignored(self, tmp_path: Path):
        """External image URLs should not be checked."""
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\n![Logo](https://example.com/logo.png)")
        report = DocumentationValidationReport(plugin_path=str(tmp_path))
        validate_image_references(tmp_path, report)
        assert not report.has_major


class TestFullValidation:
    """Integration tests for validate_documentation function."""

    def test_validates_all_rules(self, tmp_path: Path):
        """validate_documentation should check all 13 rules."""
        # Create a complete, valid plugin documentation
        readme = tmp_path / "README.md"
        readme.write_text("""# My Plugin

This is a great plugin that does things.

## Installation

```bash
claude plugin install my-plugin
```

## Usage

```python
import my_plugin
my_plugin.run()
```
""")
        (tmp_path / "CHANGELOG.md").write_text("# Changelog\n## 1.0.0\n- Initial")

        report = validate_documentation(tmp_path)

        # Should have no critical/major issues
        assert not report.has_critical
        assert not report.has_major

    def test_reports_multiple_issues(self, tmp_path: Path):
        """Should accumulate all issues found across blocking and advisory severities."""
        # Minimal README with multiple issues
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\n### Skipped h2\n```\nno lang tag\n")  # unclosed block

        report = validate_documentation(tmp_path)

        # Multiple advisory checks (missing installation/usage/changelog, skipped
        # heading, untagged code block) were recalibrated to WARNING in
        # TRDD-021250b5, so the accumulated issues now span both severities.
        accumulated = [r for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR", "WARNING")]
        assert len(accumulated) >= 3
        # The unclosed code block stays a blocking MAJOR finding.
        assert report.has_major


class TestCLI:
    """Tests for CLI functionality."""

    def test_main_returns_exit_code(self, tmp_path: Path, monkeypatch):
        """main() should return appropriate exit code."""
        import sys

        from validate_documentation import main

        (tmp_path / ".claude-plugin").mkdir()
        readme = tmp_path / "README.md"
        readme.write_text("# Plugin\n\nDescription.\n\n## Installation\n\n## Usage")
        (tmp_path / "CHANGELOG.md").write_text("# Changelog")

        monkeypatch.setattr(sys, "argv", ["validate_documentation.py", str(tmp_path)])
        exit_code = main()
        assert exit_code in (0, 1, 2, 3)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
