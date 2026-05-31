#!/usr/bin/env python3
"""Regression tests for batch-b05 audit fixes in validate_skill_comprehensive.py.

Each test pins a confirmed audit finding so the corrected behaviour cannot
silently regress. Findings covered:

- #55  validate_time_sensitive_info now skips the FULL fenced code block, not
       just lines that begin with a backtick (inner code lines were scanned).
- #56  RE_GERUND_NAME matches the gerund as the FIRST segment ('processing-pdfs'),
       which is what Anthropic recommends and what this validator suggests — not
       the gerund-last form ('pdf-processing').
- #57  RE_DYNAMIC_CONTEXT requires the bang immediately before the backtick;
       the space variant ('! `cmd`') is no longer accepted as correct syntax.
- #137 The strict-mode 'Trigger with ...' MINOR message no longer claims the
       phrase is "required" (its severity is MINOR, i.e. recommended).
- #138 The Windows-backslash escape-sequence exemption is per-match, so a real
       '\\drive'-style path on the same line as a benign escape is still caught.
- #139 The module docstring no longer maps exit codes 1:1 to letter grades.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_skill_comprehensive as vsc  # noqa: E402
from validate_skill_comprehensive import (  # noqa: E402
    RE_DYNAMIC_CONTEXT,
    RE_GERUND_NAME,
    ComprehensiveSkillReport,
    ValidationReport,
    validate_description_field,
    validate_dynamic_context,
    validate_name_field,
    validate_path_formats,
    validate_time_sensitive_info,
)


class TestTimeSensitiveSkipsCodeBlocks:
    """#55 — content INSIDE a fenced code block must not be flagged as stale prose."""

    def test_version_inside_code_fence_not_flagged(self):
        """A version/date reference inside a ```fence``` is skipped entirely."""
        body = (
            "Prose with no temporal references here.\n"
            "\n"
            "```bash\n"
            "# pinned since 2020 and supported until v3.0\n"
            'echo "released after December"\n'
            "```\n"
            "\n"
            "More clean prose.\n"
        )
        report = ComprehensiveSkillReport()
        validate_time_sensitive_info(body, report)
        stale = [r for r in report.results if "stale" in r.message]
        assert stale == [], f"code-block lines wrongly flagged: {[r.message for r in stale]}"

    def test_version_in_prose_still_flagged(self):
        """Guard: a real temporal reference in PROSE is still detected (fix did not over-skip)."""
        body = "This feature is supported until v3.0 of the tool.\n"
        report = ComprehensiveSkillReport()
        validate_time_sensitive_info(body, report)
        assert any("stale" in r.message for r in report.results)

    def test_prose_after_fence_still_scanned(self):
        """Guard: fence state toggles off correctly — prose AFTER the block is scanned."""
        body = (
            "```\n"
            "code line with no temporal token\n"
            "```\n"
            "Released after January 2024 in prose.\n"
        )
        report = ComprehensiveSkillReport()
        validate_time_sensitive_info(body, report)
        assert any("stale" in r.message for r in report.results)


class TestGerundRegexWordOrder:
    """#56 — gerund must be the FIRST segment (Anthropic-recommended form)."""

    def test_gerund_first_names_match(self):
        """'processing-pdfs', 'analyzing-data', 'building-apis' are valid gerund names."""
        for name in ("processing-pdfs", "analyzing-data", "building-apis", "creating-reports"):
            assert RE_GERUND_NAME.match(name), f"{name!r} should match gerund-first pattern"

    def test_gerund_last_names_do_not_match(self):
        """The inverted 'pdf-processing' form is NOT the recommended gerund pattern."""
        for name in ("pdf-processing", "data-analyzing"):
            assert not RE_GERUND_NAME.match(name), f"{name!r} must not match (gerund is last)"

    def test_recommended_name_gets_no_gerund_suggestion(self):
        """A skill named with the recommended gerund-first form is not nagged to use it."""
        report = ValidationReport(skill_path="test")
        validate_name_field({"name": "processing-pdfs"}, "processing-pdfs", report)
        gerund_hints = [r for r in report.results if "gerund" in r.message.lower()]
        assert gerund_hints == [], (
            "recommended gerund-first name wrongly received a 'consider gerund naming' hint: "
            f"{[r.message for r in gerund_hints]}"
        )

    def test_non_gerund_name_still_gets_suggestion(self):
        """Guard: a genuinely non-gerund name still gets the advisory suggestion."""
        report = ValidationReport(skill_path="test")
        validate_name_field({"name": "pdf-processor"}, "pdf-processor", report)
        assert any("gerund" in r.message.lower() for r in report.results)


class TestDynamicContextBangAdjacency:
    """#57 — '!`cmd`' is valid dynamic context; '! `cmd`' (with space) is not."""

    def test_correct_syntax_matches(self):
        """The adjacent bang-backtick form is recognised as correct."""
        assert RE_DYNAMIC_CONTEXT.findall("Run !`ls -la` now.") == ["!`ls -la`"]

    def test_space_variant_not_correct(self):
        """A space between '!' and the backtick is NOT valid dynamic context."""
        assert RE_DYNAMIC_CONTEXT.findall("Run ! `ls -la` now.") == []

    def test_space_variant_not_counted_as_valid_context(self):
        """End-to-end: the space variant is NOT counted as a valid dynamic-context use.

        Pre-fix the `\\s*` made '! `cmd`' match the 'correct' pattern, so the
        validator emitted a misleading 'uses dynamic context injection' info
        line for broken syntax. After the fix it is no longer accepted.
        """
        report = ValidationReport(skill_path="test")
        validate_dynamic_context("Run ! `ls -la` to list files.", report)
        assert not any(
            "dynamic context injection" in r.message for r in report.results
        ), "space-separated bang must not be counted as valid dynamic context"

    def test_correct_syntax_counted_as_valid_context(self):
        """Guard: the correct adjacent form IS still recognised as valid dynamic context."""
        report = ValidationReport(skill_path="test")
        validate_dynamic_context("Run !`ls -la` to list files.", report)
        assert any(
            "dynamic context injection" in r.message for r in report.results
        ), "correct '!`cmd`' form must still be recognised"


class TestTriggerWithMessageSeverityMatch:
    """#137 — the MINOR 'Trigger with' message must not claim the phrase is 'required'."""

    def test_trigger_with_minor_message_not_required(self):
        """Missing 'Trigger with' is emitted as MINOR and the text does not say 'required'."""
        # 'Use when ...' is present (so no MAJOR), 'Trigger with ...' is absent.
        frontmatter = {"description": "Use when the user wants to do the thing."}
        report = ValidationReport(skill_path="test")
        validate_description_field(frontmatter, "body text", report, strict_mode=True)
        trigger_msgs = [r for r in report.results if "Trigger with" in r.message]
        assert trigger_msgs, "expected a 'Trigger with' advisory"
        for r in trigger_msgs:
            assert r.level == "MINOR", f"'Trigger with' advisory should be MINOR, got {r.level}"
            assert "required" not in r.message.lower(), (
                "MINOR message must not claim 'Trigger with' is required: " + r.message
            )

    def test_use_when_missing_is_major(self):
        """Guard: 'Use when ...' remains the hard MAJOR requirement in strict mode."""
        frontmatter = {"description": "Trigger with the phrase do-the-thing."}
        report = ValidationReport(skill_path="test")
        validate_description_field(frontmatter, "body text", report, strict_mode=True)
        assert any(
            r.level == "MAJOR" and "Use when" in r.message for r in report.results
        ), "missing 'Use when ...' must still be a MAJOR finding"


class TestWindowsPathEscapeExemptionPerMatch:
    """#138 — a real backslash path on the same line as a benign escape is still flagged."""

    def test_real_path_with_benign_escape_same_line(self):
        """'\\drive\\folder' beside a benign '\\t' is flagged (line-level exemption was a FN)."""
        report = ValidationReport(skill_path="test")
        validate_path_formats("Edit config at \\drive\\folder and print \\t tab.\n", report)
        assert any(
            "Windows-style path" in r.message for r in report.results
        ), "genuine backslash path must not be suppressed by an unrelated escape on the line"

    def test_pure_escape_line_stays_clean(self):
        """Guard: a line with ONLY string escapes (no path) is not flagged (no false positive)."""
        report = ValidationReport(skill_path="test")
        validate_path_formats("Use \\n for a newline and \\t for a tab.\n", report)
        assert not any(
            "Windows-style path" in r.message for r in report.results
        ), "escape-only line must not be flagged as a Windows path"

    def test_plain_path_no_escapes_flagged(self):
        """Guard: a plain backslash path with no escapes is still flagged."""
        report = ValidationReport(skill_path="test")
        validate_path_formats("Open \\drive\\data here.\n", report)
        assert any("Windows-style path" in r.message for r in report.results)


class TestModuleDocstringExitCodeGradeDecoupled:
    """#139 — the module docstring must not assert a false exit-code-to-grade mapping."""

    def test_docstring_does_not_map_exit0_to_grade_ab(self):
        """The stale 'Grade A/B' / 'Grade F' / 'Grade D' / 'Grade C' mapping is gone."""
        doc = vsc.__doc__ or ""
        assert "Grade A/B" not in doc
        assert "(Grade F)" not in doc
        assert "(Grade D)" not in doc
        assert "(Grade C)" not in doc

    def test_docstring_states_exit_codes_severity_based(self):
        """The corrected docstring describes exit codes by severity, not grade."""
        doc = vsc.__doc__ or ""
        assert "CRITICAL" in doc and "MAJOR" in doc and "MINOR" in doc
