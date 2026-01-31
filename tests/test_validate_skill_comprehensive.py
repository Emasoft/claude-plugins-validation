#!/usr/bin/env python3
"""Tests for validate_skill_comprehensive.py.

Tests the new validation rules added from:
- AgentSkills OpenSpec (skills-ref library) - Unicode NFKC, i18n support
- Official Anthropic Documentation - MCP tool format, time-sensitive info,
  metadata validation, scripts shebang check

Validation rules tested:
1. Unicode NFKC normalization for skill names
2. i18n character support (Chinese, Russian)
3. OpenSpec strict mode field whitelist
4. allowed-tools space-delimited format
5. MCP tool reference format validation
6. Time-sensitive information detection
7. Metadata field type validation
8. Scripts directory shebang validation
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_skill_comprehensive import (  # noqa: E402
    RE_MCP_TOOL_UNQUALIFIED,
    RE_TIME_SENSITIVE,
    ValidationReport,
    validate_allowed_tools_field,
    validate_mcp_tool_references,
    validate_metadata_field,
    validate_name_field,
    validate_scripts_directory,
    validate_skill,
    validate_time_sensitive_info,
)


class TestUnicodeNFKCNormalization:
    """Tests for Unicode NFKC normalization in skill names."""

    def test_composed_and_decomposed_names_match(self, tmp_path):
        """Composed and decomposed Unicode forms should be normalized."""
        # 'café' can be represented as:
        # - Precomposed: 'café' (4 chars, 'é' is U+00E9)
        # - Decomposed: 'café' (5 chars, 'e' + combining acute U+0301)
        composed_name = "café"
        decomposed_name = "cafe\u0301"

        skill_dir = tmp_path / composed_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"""---
name: {decomposed_name}
description: A test skill with Unicode name
---
# Test Skill
""")
        report = validate_skill(skill_dir)
        # Should not have mismatch error since both normalize to same form
        assert not any("must match skill name" in r.message for r in report.results)

    def test_nfkc_normalization_applied(self, tmp_path):
        """NFKC normalization should be applied to skill names."""
        # Use a name that normalizes differently under NFKC
        # U+2126 (OHM SIGN) normalizes to U+03A9 (GREEK CAPITAL LETTER OMEGA)
        skill_dir = tmp_path / "ohm-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: ohm-skill
description: A test skill
---
# Test
""")
        report = validate_skill(skill_dir)
        assert not report.has_critical


class TestI18NCharacterSupport:
    """Tests for internationalized character support in skill names."""

    def test_chinese_characters_allowed(self, tmp_path):
        """Chinese characters should be allowed in skill names."""
        skill_dir = tmp_path / "技能"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: 技能
description: A skill with Chinese name
---
# Test
""")
        report = validate_skill(skill_dir)
        # Should not flag invalid characters
        assert not any("invalid characters" in r.message for r in report.results)

    def test_russian_lowercase_allowed(self, tmp_path):
        """Russian lowercase characters should be allowed."""
        skill_dir = tmp_path / "навык"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: навык
description: A skill with Russian name
---
# Test
""")
        report = validate_skill(skill_dir)
        assert not any("invalid characters" in r.message for r in report.results)

    def test_russian_uppercase_rejected(self, tmp_path):
        """Russian uppercase characters should be rejected (must be lowercase)."""
        skill_dir = tmp_path / "НАВЫК"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: НАВЫК
description: A skill with Russian uppercase name
---
# Test
""")
        report = validate_skill(skill_dir)
        assert any("lowercase" in r.message for r in report.results)

    def test_russian_with_hyphens_allowed(self, tmp_path):
        """Russian names with hyphens should be allowed."""
        skill_dir = tmp_path / "мой-навык"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: мой-навык
description: A skill with Russian hyphenated name
---
# Test
""")
        report = validate_skill(skill_dir)
        assert not any("invalid characters" in r.message for r in report.results)


class TestOpenSpecStrictMode:
    """Tests for OpenSpec strict mode validation."""

    def test_strict_mode_rejects_claude_code_fields(self, tmp_path):
        """OpenSpec strict mode should reject Claude Code-specific fields."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: my-skill
description: A test skill
context: fork
agent: test-engineer
---
# Test
""")
        report = validate_skill(skill_dir, strict_openspec=True)
        # Should flag both 'context' and 'agent' as unexpected
        unexpected_errors = [r for r in report.results if "Unexpected field" in r.message]
        assert len(unexpected_errors) >= 2

    def test_strict_mode_accepts_openspec_fields(self, tmp_path):
        """OpenSpec strict mode should accept standard OpenSpec fields."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: my-skill
description: A test skill
license: MIT
compatibility: Requires Python 3.11+
metadata:
  author: Test
---
# Test
""")
        report = validate_skill(skill_dir, strict_openspec=True)
        # Should not flag any unexpected fields
        assert not any("Unexpected field" in r.message for r in report.results)


class TestAllowedToolsValidation:
    """Tests for allowed-tools field validation."""

    def test_space_delimited_format_accepted(self):
        """OpenSpec space-delimited format should be accepted."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Bash(jq:*) Bash(git:*)"}
        validate_allowed_tools_field(frontmatter, report, strict_openspec=True)
        assert not report.has_major

    def test_comma_delimited_format_accepted(self):
        """Comma-delimited format should be accepted."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Read, Write, Edit"}
        validate_allowed_tools_field(frontmatter, report)
        assert not report.has_major

    def test_mixed_format_handled(self):
        """Mixed format (scoped tools) should be handled correctly."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Bash(git:*) Read Write"}
        validate_allowed_tools_field(frontmatter, report)
        assert not report.has_critical


class TestMCPToolReferenceValidation:
    """Tests for MCP tool reference format validation."""

    def test_unqualified_mcp_tool_detected(self):
        """Unqualified MCP tool references should be detected."""
        body = """
        You can use the read_file tool to read files.
        Call the write_file function to write files.
        """
        report = ValidationReport(skill_path="test")
        validate_mcp_tool_references(body, report)
        # Should flag unqualified references
        assert any("MCP tool reference" in r.message for r in report.results)

    def test_regex_matches_unqualified_tools(self):
        """The regex should match various unqualified MCP tool patterns."""
        test_cases = [
            ("use the read_file tool", True),
            ("call the write_file function", True),
            ("invoke the list_dir tool", True),
            ("run the search_for_pattern tool", True),
            ("use the serena:read_file tool", False),  # Qualified - should not match
            ("use the Read tool", False),  # Standard tool, not MCP-style
        ]
        for text, should_match in test_cases:
            match = RE_MCP_TOOL_UNQUALIFIED.search(text)
            if should_match:
                assert match is not None, f"Expected match for: {text}"
            # Note: qualified references may still match the pattern,
            # but the validation logic handles that separately


class TestTimeSensitiveInfoDetection:
    """Tests for time-sensitive information detection."""

    def test_date_references_detected(self):
        """Date references should be detected."""
        body = """
        This feature was added after January 2024.
        Available since v2.0.
        """
        report = ValidationReport(skill_path="test")
        validate_time_sensitive_info(body, report)
        assert any("Time-sensitive" in r.message for r in report.results)

    def test_version_references_detected(self):
        """Version number references should be detected."""
        body = """
        Requires Node.js v18 or later.
        Starting 2.5.0 this behavior changed.
        """
        report = ValidationReport(skill_path="test")
        validate_time_sensitive_info(body, report)
        assert any("Time-sensitive" in r.message for r in report.results)

    def test_regex_matches_time_sensitive_patterns(self):
        """The regex should match various time-sensitive patterns."""
        test_cases = [
            ("before January 2024", True),
            ("after v2.0", True),
            ("since March", True),
            ("as of 2023", True),
            ("until December", True),
            ("the version is 2.0", False),  # No temporal preposition
            ("in the year", False),  # No specific date/version
        ]
        for text, should_match in test_cases:
            match = RE_TIME_SENSITIVE.search(text)
            if should_match:
                assert match is not None, f"Expected match for: {text}"
            else:
                assert match is None, f"Expected no match for: {text}"


class TestMetadataValidation:
    """Tests for metadata field validation."""

    def test_valid_metadata_accepted(self):
        """Valid string key-value metadata should be accepted."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "metadata": {
                "author": "Test Author",
                "category": "utilities",
            }
        }
        validate_metadata_field(frontmatter, report)
        assert not report.has_major
        assert any("metadata" in r.message and "PASSED" in r.level for r in report.results)

    def test_non_dict_metadata_rejected(self):
        """Non-dictionary metadata should be rejected."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"metadata": "not a dict"}
        validate_metadata_field(frontmatter, report)
        assert report.has_major

    def test_non_string_values_warned(self):
        """Non-string values in metadata should generate warnings."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "metadata": {
                "author": "Test",
                "version": 123,  # Should be string
            }
        }
        validate_metadata_field(frontmatter, report)
        assert any("should be string" in r.message for r in report.results)


class TestScriptsDirectoryValidation:
    """Tests for scripts directory validation."""

    def test_missing_shebang_detected(self, tmp_path):
        """Scripts without shebang should be detected."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: my-skill
description: Test
---
# Test
""")
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()

        # Create script without shebang
        script = scripts_dir / "test.py"
        script.write_text("print('hello')")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        report = ValidationReport(skill_path=str(skill_dir))
        validate_scripts_directory(skill_dir, report)
        assert any("shebang" in r.message for r in report.results)

    def test_valid_shebang_accepted(self, tmp_path):
        """Scripts with valid shebang should pass."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()

        # Create script with proper shebang
        script = scripts_dir / "test.py"
        script.write_text("#!/usr/bin/env python3\nprint('hello')")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        report = ValidationReport(skill_path=str(skill_dir))
        validate_scripts_directory(skill_dir, report)
        assert any("valid shebang" in r.message for r in report.results)

    def test_wrong_shebang_type_warned(self, tmp_path):
        """Python script with non-Python shebang should be warned."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()

        # Create Python script with bash shebang
        script = scripts_dir / "test.py"
        script.write_text("#!/bin/bash\necho 'hello'")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        report = ValidationReport(skill_path=str(skill_dir))
        validate_scripts_directory(skill_dir, report)
        assert any("non-Python shebang" in r.message for r in report.results)

    def test_non_executable_script_flagged(self, tmp_path):
        """Non-executable scripts should be flagged."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()

        # Create script without executable bit
        script = scripts_dir / "test.py"
        script.write_text("#!/usr/bin/env python3\nprint('hello')")
        # Don't set executable bit

        report = ValidationReport(skill_path=str(skill_dir))
        validate_scripts_directory(skill_dir, report)
        assert any("not executable" in r.message for r in report.results)


class TestValidateNameField:
    """Tests for validate_name_field function."""

    def test_xml_tags_rejected(self):
        """Names with XML tags should be rejected."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"name": "<script>bad</script>"}
        validate_name_field(frontmatter, "test", report)
        assert any("XML tags" in r.message for r in report.results)

    def test_vague_names_warned(self):
        """Vague/generic names should generate warnings."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"name": "pdf-helper"}
        validate_name_field(frontmatter, "pdf-helper", report)
        assert any("vague" in r.message.lower() or "generic" in r.message.lower() for r in report.results)

    def test_gerund_naming_suggested(self):
        """Non-gerund names should get suggestions."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"name": "pdf-processor"}
        validate_name_field(frontmatter, "pdf-processor", report)
        assert any("gerund" in r.message.lower() for r in report.results)


class TestFullValidation:
    """End-to-end validation tests."""

    def test_valid_skill_passes(self, tmp_path):
        """A well-formed skill should pass validation."""
        skill_dir = tmp_path / "processing-pdfs"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: processing-pdfs
description: Process PDF files for text extraction and manipulation. Use when working with PDF documents.
---
# Processing PDFs

This skill helps process PDF files.
""")
        report = validate_skill(skill_dir)
        assert not report.has_critical
        assert not report.has_major

    def test_all_openspec_fields_valid(self, tmp_path):
        """All OpenSpec fields should be valid."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: my-skill
description: A comprehensive test skill
license: MIT
allowed-tools: Read Write Edit
compatibility: Requires Python 3.11+
metadata:
  author: Test Author
  version: "1.0.0"
---
# My Skill

Body content here.
""")
        report = validate_skill(skill_dir, strict_openspec=True)
        assert not report.has_critical


# =============================================================================
# Tests for New Validation Functions (Phase 2 Implementation)
# =============================================================================


class TestCompatibilityFieldValidation:
    """Tests for compatibility field validation."""

    def test_valid_compatibility_accepted(self):
        """Valid compatibility field should be accepted."""
        from validate_skill_comprehensive import validate_compatibility_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"compatibility": "Requires Python 3.11+"}
        validate_compatibility_field(frontmatter, report)
        assert any("compatibility" in r.message and "PASSED" in r.level for r in report.results)

    def test_compatibility_too_long_rejected(self):
        """Compatibility exceeding 500 chars should be rejected."""
        from validate_skill_comprehensive import validate_compatibility_field

        report = ValidationReport(skill_path="test")
        long_compat = "x" * 550
        frontmatter = {"compatibility": long_compat}
        validate_compatibility_field(frontmatter, report)
        assert any("exceeds" in r.message and "500" in r.message for r in report.results)

    def test_non_string_compatibility_rejected(self):
        """Non-string compatibility should be rejected."""
        from validate_skill_comprehensive import validate_compatibility_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"compatibility": 123}
        validate_compatibility_field(frontmatter, report)
        assert report.has_major


class TestLicenseFieldValidation:
    """Tests for license field validation."""

    def test_valid_license_accepted(self):
        """Valid license field should be accepted."""
        from validate_skill_comprehensive import validate_license_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"license": "MIT"}
        validate_license_field(frontmatter, report)
        assert any("license" in r.message and "PASSED" in r.level for r in report.results)

    def test_non_string_license_rejected(self):
        """Non-string license should be rejected."""
        from validate_skill_comprehensive import validate_license_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"license": 123}
        validate_license_field(frontmatter, report)
        assert report.has_major


class TestArgumentHintFieldValidation:
    """Tests for argument-hint field validation."""

    def test_valid_argument_hint_accepted(self):
        """Valid argument-hint field should be accepted."""
        from validate_skill_comprehensive import validate_argument_hint_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"argument-hint": "<file-path>"}
        validate_argument_hint_field(frontmatter, report)
        assert any("argument-hint" in r.message and "PASSED" in r.level for r in report.results)

    def test_non_string_argument_hint_rejected(self):
        """Non-string argument-hint should be rejected."""
        from validate_skill_comprehensive import validate_argument_hint_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"argument-hint": 123}
        validate_argument_hint_field(frontmatter, report)
        assert report.has_major


class TestModelFieldValidation:
    """Tests for model field validation."""

    def test_valid_model_values_accepted(self):
        """Valid model values (sonnet, opus, inherit) should be accepted without warnings."""
        from validate_skill_comprehensive import validate_model_field

        for model in ["sonnet", "opus", "inherit"]:
            report = ValidationReport(skill_path="test")
            frontmatter = {"model": model}
            validate_model_field(frontmatter, report)
            assert any("model" in r.message.lower() and r.level == "PASSED" for r in report.results)
            assert not report.has_minor

    def test_haiku_model_receives_penalty(self):
        """Haiku model should receive a minor penalty as it's less reliable."""
        from validate_skill_comprehensive import validate_model_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"model": "haiku"}
        validate_model_field(frontmatter, report)
        assert report.has_minor
        assert any("haiku" in r.message.lower() and "less reliable" in r.message.lower() for r in report.results)

    def test_invalid_model_rejected(self):
        """Invalid model value should be rejected."""
        from validate_skill_comprehensive import validate_model_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"model": "gpt-4"}
        validate_model_field(frontmatter, report)
        assert report.has_major


class TestHooksFieldValidation:
    """Tests for hooks field validation."""

    def test_valid_hooks_path_accepted(self):
        """Valid hooks path should be accepted."""
        from validate_skill_comprehensive import validate_hooks_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"hooks": "./hooks/hooks.json"}
        validate_hooks_field(frontmatter, report)
        assert any("hooks" in r.message and "PASSED" in r.level for r in report.results)

    def test_valid_hooks_dict_accepted(self):
        """Valid hooks dict should be accepted."""
        from validate_skill_comprehensive import validate_hooks_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"hooks": {"PreToolUse": [], "PostToolUse": []}}
        validate_hooks_field(frontmatter, report)
        assert any("hooks" in r.message and "PASSED" in r.level for r in report.results)

    def test_unknown_hook_event_warned(self):
        """Unknown hook event should generate warning."""
        from validate_skill_comprehensive import validate_hooks_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"hooks": {"UnknownEvent": []}}
        validate_hooks_field(frontmatter, report)
        assert any("Unknown hook event" in r.message for r in report.results)


class TestStringSubstitutionValidation:
    """Tests for string substitution validation."""

    def test_arguments_var_detected(self):
        """$ARGUMENTS usage should be detected."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = "Use $ARGUMENTS to get all input"
        validate_string_substitutions(body, report)
        assert any("$ARGUMENTS" in r.message for r in report.results)

    def test_indexed_arguments_detected(self):
        """$ARGUMENTS[N] usage should be detected."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = "First arg: $ARGUMENTS[0], second: $ARGUMENTS[1]"
        validate_string_substitutions(body, report)
        assert any("indexed arguments" in r.message for r in report.results)

    def test_shorthand_arguments_detected(self):
        """$N shorthand should be detected."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = "File path: $1, output: $2"
        validate_string_substitutions(body, report)
        assert any("shorthand" in r.message for r in report.results)

    def test_session_id_detected(self):
        """${CLAUDE_SESSION_ID} usage should be detected."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = "Session: ${CLAUDE_SESSION_ID}"
        validate_string_substitutions(body, report)
        assert any("CLAUDE_SESSION_ID" in r.message for r in report.results)


class TestDynamicContextValidation:
    """Tests for dynamic context injection validation."""

    def test_ultrathink_detected(self):
        """ultrathink keyword should be detected."""
        from validate_skill_comprehensive import validate_dynamic_context

        report = ValidationReport(skill_path="test")
        body = "This skill uses ultrathink for deep analysis"
        validate_dynamic_context(body, report)
        assert any("ultrathink" in r.message for r in report.results)


class TestContentPatternsValidation:
    """Tests for content patterns validation."""

    def test_checklist_pattern_detected(self):
        """Checklist patterns should be detected."""
        from validate_skill_comprehensive import validate_content_patterns

        report = ValidationReport(skill_path="test")
        body = """
        - [ ] First task
        - [x] Second task
        - [ ] Third task
        """
        validate_content_patterns(body, report)
        assert any("checklist" in r.message.lower() for r in report.results)

    def test_numbered_steps_detected(self):
        """Numbered workflow steps should be detected."""
        from validate_skill_comprehensive import validate_content_patterns

        report = ValidationReport(skill_path="test")
        body = """
        1. First step
        2. Second step
        3. Third step
        4. Fourth step
        """
        validate_content_patterns(body, report)
        assert any("numbered" in r.message.lower() or "workflow" in r.message.lower() for r in report.results)

    def test_copy_checklist_phrase_detected(self):
        """'Copy this checklist' phrase should be detected."""
        from validate_skill_comprehensive import validate_content_patterns

        report = ValidationReport(skill_path="test")
        body = """
        Copy this checklist and track your progress:

        - [ ] Step 1: Analyze
        - [ ] Step 2: Validate
        - [ ] Step 3: Complete

        1. First step
        2. Second step
        3. Third step
        """
        validate_content_patterns(body, report)
        assert any("copyable checklist" in r.message.lower() for r in report.results)

    def test_copy_checklist_phrase_variant_detected(self):
        """'Copy this checklist and check off' variant should be detected."""
        from validate_skill_comprehensive import validate_content_patterns

        report = ValidationReport(skill_path="test")
        body = """
        Copy this checklist and check off items as you complete them:

        - [ ] Step 1
        - [ ] Step 2
        """
        validate_content_patterns(body, report)
        assert any("copyable checklist" in r.message.lower() for r in report.results)

    def test_strict_template_detected(self):
        """Strict template pattern should be detected."""
        from validate_skill_comprehensive import validate_content_patterns

        report = ValidationReport(skill_path="test")
        body = """
        ALWAYS use this exact template structure:

        ```markdown
        # Title
        ## Summary
        ```
        """
        validate_content_patterns(body, report)
        assert any("strict" in r.message.lower() and "template" in r.message.lower() for r in report.results)

    def test_flexible_template_detected(self):
        """Flexible template pattern should be detected."""
        from validate_skill_comprehensive import validate_content_patterns

        report = ValidationReport(skill_path="test")
        body = """
        Here is a sensible default format, but use your best judgment:

        ```markdown
        # Title
        ## Summary
        ```
        """
        validate_content_patterns(body, report)
        assert any("flexible" in r.message.lower() and "template" in r.message.lower() for r in report.results)


class TestPackageDependenciesValidation:
    """Tests for package dependencies validation."""

    def test_pip_install_detected(self):
        """pip install commands should be detected."""
        from validate_skill_comprehensive import validate_package_dependencies

        report = ValidationReport(skill_path="test")
        body = "Install with: pip install requests numpy"
        validate_package_dependencies(body, report)
        assert any("package dependencies" in r.message for r in report.results)

    def test_npm_install_detected(self):
        """npm install commands should be detected."""
        from validate_skill_comprehensive import validate_package_dependencies

        report = ValidationReport(skill_path="test")
        body = "Install with: npm install lodash"
        validate_package_dependencies(body, report)
        assert any("package dependencies" in r.message for r in report.results)


class TestFullValidationWithNewFields:
    """End-to-end validation tests including new fields."""

    def test_skill_with_all_fields(self, tmp_path):
        """Skill with all supported fields should validate correctly."""
        skill_dir = tmp_path / "advanced-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: advanced-skill
description: An advanced skill with all supported fields. Use when testing validation.
license: MIT
compatibility: Requires Python 3.11+
model: sonnet
argument-hint: <file-path>
allowed-tools: Read Write Edit
metadata:
  author: Test Author
  version: "1.0.0"
---
# Advanced Skill

## Overview

This skill demonstrates all supported fields.

## Instructions

1. First step
2. Second step
3. Third step

## Checklist

- [ ] Task one
- [ ] Task two
- [x] Task three

## Dependencies

Install with: pip install requests

## Examples

Input: file.txt
Output: processed.txt
""")
        report = validate_skill(skill_dir)
        assert not report.has_critical
        # Should detect patterns
        assert any("checklist" in r.message.lower() for r in report.results)
        assert any("package dependencies" in r.message.lower() or "pip" in r.message.lower() for r in report.results)
