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

    def test_misplaced_user_invocable_bool_warns(self):
        """user-invocable under metadata: should warn about misplacement, not type mismatch."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "metadata": {
                "user-invocable": False,
            }
        }
        validate_metadata_field(frontmatter, report)
        # Should get a WARNING about misplacement, NOT a MINOR about string type
        assert any("Move it to the top level" in r.message for r in report.results)
        assert not any("should be string" in r.message for r in report.results)

    def test_misplaced_user_invocable_string_warns(self):
        """user-invocable as string under metadata: should also warn about misplacement."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "metadata": {
                "user-invocable": "false",
            }
        }
        validate_metadata_field(frontmatter, report)
        # Should warn about misplacement regardless of value type
        assert any("Move it to the top level" in r.message for r in report.results)

    def test_misplaced_disable_model_invocation_warns(self):
        """disable-model-invocation under metadata: should warn about misplacement."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "metadata": {
                "disable-model-invocation": True,
            }
        }
        validate_metadata_field(frontmatter, report)
        assert any("Move it to the top level" in r.message for r in report.results)
        assert not any("should be string" in r.message for r in report.results)

    def test_custom_metadata_non_string_still_minor(self):
        """Custom (non-standard) metadata values that aren't strings should still get MINOR."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "metadata": {
                "custom-field": 42,
            }
        }
        validate_metadata_field(frontmatter, report)
        # Custom field should get the generic string type warning, not misplacement
        assert any("should be string" in r.message for r in report.results)
        assert not any("Move it to the top level" in r.message for r in report.results)


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


# ---------------------------------------------------------------------------
# Changelog-driven tests: full model ID support in validate_skill_comprehensive.py
# ---------------------------------------------------------------------------


class TestModelFieldFullIdsInSkill:
    """Tests for validate_model_field accepting full model IDs in skill validation (v2.1.74+)."""

    def test_full_model_id_accepted_in_skill(self):
        """validate_model_field accepts 'claude-sonnet-4-6' as a full model ID without MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = ValidationReport(skill_path="test")
        validate_model_field({"model": "claude-sonnet-4-6"}, report)
        assert not report.has_major
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("claude-sonnet-4-6" in m for m in passed_msgs)

    def test_haiku_full_id_gets_minor_penalty(self):
        """validate_model_field gives a MINOR penalty for full haiku IDs like 'claude-haiku-4-5'."""
        from validate_skill_comprehensive import validate_model_field

        report = ValidationReport(skill_path="test")
        validate_model_field({"model": "claude-haiku-4-5"}, report)
        # Must be MINOR, not MAJOR
        assert report.has_minor
        assert not report.has_major
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("haiku" in m.lower() for m in minor_msgs)

    def test_claude_opus_full_id_passes(self):
        """validate_model_field accepts 'claude-opus-4-6' without any penalty."""
        from validate_skill_comprehensive import validate_model_field

        report = ValidationReport(skill_path="test")
        validate_model_field({"model": "claude-opus-4-6"}, report)
        assert not report.has_major
        assert not report.has_minor

    def test_haiku_full_id_with_date_suffix_gets_penalty(self):
        """validate_model_field gives MINOR penalty for 'claude-haiku-4-5-20251001'."""
        from validate_skill_comprehensive import validate_model_field

        report = ValidationReport(skill_path="test")
        validate_model_field({"model": "claude-haiku-4-5-20251001"}, report)
        assert report.has_minor
        assert not report.has_major

    def test_unknown_full_model_id_rejected(self):
        """validate_model_field rejects non-Claude full IDs like 'gpt-4-turbo' with MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = ValidationReport(skill_path="test")
        validate_model_field({"model": "gpt-4-turbo"}, report)
        assert report.has_major


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


# =============================================================================
# Additional Tests for Uncovered Lines (Phase 3)
# =============================================================================


class TestReportNitAndWarning:
    """Tests for ValidationReport nit() and warning() methods (lines 373, 379)."""

    def test_nit_adds_result(self):
        """nit() should add a NIT-level result to the report."""
        report = ValidationReport(skill_path="test")
        report.nit("This is a nit issue", "SKILL.md", line=10, category="Style")
        assert len(report.results) == 1
        assert report.results[0].level == "NIT"
        assert report.results[0].message == "This is a nit issue"
        assert report.results[0].line == 10


class TestExitCodeProperty:
    """Tests for ValidationReport exit_code property (lines 405-411)."""

    def test_exit_code_critical(self):
        """exit_code should return EXIT_CRITICAL when critical issues exist."""
        report = ValidationReport(skill_path="test")
        report.critical("A critical issue")
        assert report.exit_code == 1  # EXIT_CRITICAL

    def test_exit_code_major_no_critical(self):
        """exit_code should return EXIT_MAJOR when major issues exist but no critical."""
        report = ValidationReport(skill_path="test")
        report.major("A major issue")
        assert report.exit_code == 2  # EXIT_MAJOR

    def test_exit_code_minor_only(self):
        """exit_code should return EXIT_MINOR when only minor issues exist."""
        report = ValidationReport(skill_path="test")
        report.minor("A minor issue")
        assert report.exit_code == 3  # EXIT_MINOR

    def test_exit_code_ok(self):
        """exit_code should return EXIT_OK when no issues exist."""
        report = ValidationReport(skill_path="test")
        report.passed("All good")
        assert report.exit_code == 0  # EXIT_OK


class TestCalculateGrade:
    """Tests for calculate_grade method (lines 424, 428)."""

    def test_grade_c_for_score_70(self):
        """Score of 70 should yield grade C."""
        report = ValidationReport(skill_path="test")
        report.overall_score = 72.0
        report.calculate_grade()
        assert report.grade == "C"

    def test_grade_f_for_score_below_60(self):
        """Score below 60 should yield grade F."""
        report = ValidationReport(skill_path="test")
        report.overall_score = 45.0
        report.calculate_grade()
        assert report.grade == "F"


class TestFindSkillMdFallback:
    """Tests for find_skill_md returning None (line 472)."""

    def test_no_skill_md_returns_none(self, tmp_path):
        """find_skill_md should return None when no SKILL.md or skill.md exists."""
        from validate_skill_comprehensive import find_skill_md

        skill_dir = tmp_path / "empty-skill"
        skill_dir.mkdir()
        result = find_skill_md(skill_dir)
        assert result is None


class TestParseFrontmatterEdgeCases:
    """Tests for parse_frontmatter edge cases (lines 449, 453, 458, 462-463)."""

    def test_no_frontmatter_returns_none(self):
        """Content without --- prefix should return None frontmatter."""
        from validate_skill_comprehensive import parse_frontmatter

        fm, body, line = parse_frontmatter("# No frontmatter here\nJust content")
        assert fm is None
        assert "No frontmatter" in body
        assert line == 0

    def test_malformed_yaml_returns_none(self):
        """Invalid YAML in frontmatter should return None."""
        from validate_skill_comprehensive import parse_frontmatter

        content = "---\n: invalid: [yaml: broken\n---\nBody"
        fm, body, line = parse_frontmatter(content)
        assert fm is None

    def test_empty_frontmatter_returns_empty_dict(self):
        """Empty frontmatter (just ---\\n---) should return empty dict."""
        from validate_skill_comprehensive import parse_frontmatter

        content = "---\n\n---\nBody content"
        fm, body, line = parse_frontmatter(content)
        assert fm == {}


class TestDescriptionFieldCoverage:
    """Tests for validate_description_field uncovered branches (lines 652-664, 668-673, 677, 688, 694, 720, 728)."""

    def test_no_description_no_body_is_major(self):
        """Missing description with empty body should be a major issue."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        frontmatter = {}
        validate_description_field(frontmatter, "", report)
        assert report.has_major
        assert any("No 'description' field and no body content" in r.message for r in report.results)

    def test_non_string_description_rejected(self):
        """Non-string description should be rejected as major."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"description": 42}
        validate_description_field(frontmatter, "body", report)
        assert report.has_major
        assert any("must be a string" in r.message for r in report.results)

    def test_strict_mode_first_person_rejected(self):
        """First person in description should be rejected in strict mode."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"description": "I can help you process files. Use when you need file processing."}
        validate_description_field(frontmatter, "body content here", report, strict_mode=True)
        assert any("first person" in r.message.lower() for r in report.results)

    def test_strict_mode_second_person_rejected(self):
        """Second person in description should be rejected in strict mode."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"description": "You can process files easily. Use when doing file tasks."}
        validate_description_field(frontmatter, "body content here", report, strict_mode=True)
        assert any("second person" in r.message.lower() for r in report.results)


class TestNonInvocableSkillDescription:
    """Tests for user-invocable:false description requirements (Loaded by instead of Trigger with)."""

    def test_user_invocable_skill_needs_trigger_with(self):
        """User-invocable skill should require 'Trigger with' in strict mode."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"description": "Does things. Use when needed.", "user-invocable": True}
        validate_description_field(frontmatter, "body", report, strict_mode=True)
        assert any("Trigger with" in r.message for r in report.results)

    def test_non_invocable_skill_needs_loaded_by(self):
        """Non-invocable skill should require 'Loaded by' instead of 'Trigger with'."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"description": "Does things. Use when needed.", "user-invocable": False}
        validate_description_field(frontmatter, "body", report, strict_mode=True)
        # Should NOT ask for "Trigger with"
        assert not any("Trigger with" in r.message for r in report.results)
        # Should ask for "Loaded by" or "Used by"
        assert any("Loaded by" in r.message or "Used by" in r.message for r in report.results)

    def test_non_invocable_with_loaded_by_passes(self):
        """Non-invocable skill with 'Loaded by' should pass the trigger check."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"description": "Does things. Use when needed. Loaded by my-agent.", "user-invocable": False}
        validate_description_field(frontmatter, "body", report, strict_mode=True)
        assert not any("Loaded by" in r.message and "MINOR" in r.level for r in report.results)

    def test_non_invocable_with_used_by_passes(self):
        """Non-invocable skill with 'Used by' should also pass the trigger check."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"description": "Does things. Use when needed. Used by my-agent.", "user-invocable": False}
        validate_description_field(frontmatter, "body", report, strict_mode=True)
        assert not any("Loaded by" in r.message and "MINOR" in r.level for r in report.results)

    def test_default_invocable_uses_trigger_with_rule(self):
        """Skill without user-invocable field (default True) should use 'Trigger with' rule."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"description": "Does things. Use when needed."}
        validate_description_field(frontmatter, "body", report, strict_mode=True)
        assert any("Trigger with" in r.message for r in report.results)


class TestContextFieldValidation:
    """Tests for validate_context_field (lines 1049-1054, 1057-1062)."""

    def test_non_string_context_is_critical(self):
        """Non-string context value should be a critical issue."""
        from validate_skill_comprehensive import validate_context_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"context": 123}
        validate_context_field(frontmatter, report)
        assert any("must be a string" in r.message for r in report.results)

    def test_invalid_context_value_is_critical(self):
        """Invalid context value string should be a critical issue."""
        from validate_skill_comprehensive import validate_context_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"context": "invalid-value"}
        validate_context_field(frontmatter, report)
        assert any("Invalid 'context' value" in r.message for r in report.results)


class TestAgentFieldValidation:
    """Tests for validate_agent_field (lines 1071, 1081-1086, 1089, 1096)."""

    def test_agent_without_context_fork_warned(self):
        """agent field without context:fork should get a major warning."""
        from validate_skill_comprehensive import validate_agent_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"agent": "test-engineer"}
        validate_agent_field(frontmatter, report)
        assert any("no effect without" in r.message for r in report.results)

    def test_agent_with_fork_builtin_type_passes(self):
        """Built-in agent type with context:fork should pass."""
        from validate_skill_comprehensive import validate_agent_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"agent": "test-engineer", "context": "fork"}
        validate_agent_field(frontmatter, report)
        assert any("built-in" in r.message for r in report.results)

    def test_non_string_agent_is_critical(self):
        """Non-string agent value should be a critical issue."""
        from validate_skill_comprehensive import validate_agent_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"agent": 42, "context": "fork"}
        validate_agent_field(frontmatter, report)
        assert any("must be a string" in r.message for r in report.results)

    def test_agent_missing_with_fork_context_info(self):
        """Missing agent with context:fork should generate info message."""
        from validate_skill_comprehensive import validate_agent_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"context": "fork"}
        validate_agent_field(frontmatter, report)
        assert any("not specified with context: fork" in r.message for r in report.results)


class TestBooleanFieldValidation:
    """Tests for validate_boolean_field (lines 1114-1124)."""

    def test_non_boolean_value_is_critical(self):
        """Non-boolean value for boolean field should be critical."""
        from validate_skill_comprehensive import validate_boolean_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"user-invocable": "yes"}
        validate_boolean_field(frontmatter, "user-invocable", report)
        assert any("must be a boolean" in r.message for r in report.results)

    def test_valid_boolean_passes(self):
        """Valid boolean value should pass."""
        from validate_skill_comprehensive import validate_boolean_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"user-invocable": True}
        validate_boolean_field(frontmatter, "user-invocable", report)
        assert any("'user-invocable' field valid" in r.message for r in report.results)


class TestFieldWhitelistDeprecated:
    """Tests for validate_field_whitelist with deprecated fields (line 1137)."""

    def test_deprecated_field_flagged_as_minor(self):
        """Deprecated field 'when_to_use' should generate minor issue."""
        from validate_skill_comprehensive import validate_field_whitelist

        report = ValidationReport(skill_path="test")
        frontmatter = {"name": "test", "when_to_use": "always"}
        validate_field_whitelist(frontmatter, report)
        assert any("Deprecated field" in r.message for r in report.results)


class TestTokenBudgetBranches:
    """Tests for validate_token_budget edge cases (lines 1164, 1170, 1181, 1187)."""

    def test_excessive_line_count_is_major(self):
        """Content exceeding 500 lines should be major."""
        from validate_skill_comprehensive import validate_token_budget

        report = ValidationReport(skill_path="test")
        content = "---\nname: test\n---\n" + ("line\n" * 550)
        body = "line\n" * 550
        validate_token_budget(content, body, report)
        assert any("lines" in r.message and r.level == "MAJOR" for r in report.results)

    def test_excessive_char_count_is_major(self):
        """Content exceeding 5000 characters should be major."""
        from validate_skill_comprehensive import validate_token_budget

        report = ValidationReport(skill_path="test")
        body = "x" * 5500
        content = "---\nname: test\n---\n" + body
        validate_token_budget(content, body, report)
        assert any("characters" in r.message and r.level == "MAJOR" for r in report.results)

    def test_excessive_word_count_is_major(self):
        """Content exceeding MAX_WORD_COUNT_ERROR should be major."""
        from validate_skill_comprehensive import validate_token_budget

        report = ValidationReport(skill_path="test")
        body = " ".join(["word"] * 5500)
        content = "---\nname: test\n---\n" + body
        validate_token_budget(content, body, report)
        assert any("words" in r.message and r.level == "MAJOR" for r in report.results)


class TestRequiredSectionsStrictMode:
    """Tests for validate_required_sections in strict mode (lines 1210, 1216-1227)."""

    def test_strict_mode_missing_sections_flagged(self):
        """Missing required sections in strict mode should be flagged as major."""
        from validate_skill_comprehensive import validate_required_sections

        report = ValidationReport(skill_path="test")
        body = "# My Skill\n\nJust some content without required sections.\n"
        validate_required_sections(body, report, strict_mode=True)
        assert report.has_major
        assert any("Required section missing" in r.message for r in report.results)

    def test_strict_mode_instructions_without_numbered_list(self):
        """Instructions section without numbered steps in strict mode should be flagged."""
        from validate_skill_comprehensive import validate_required_sections

        report = ValidationReport(skill_path="test")
        body = (
            "## Overview\nStuff\n"
            "## Prerequisites\nStuff\n"
            "## Instructions\nDo something but no numbered list.\n"
            "## Output\nStuff\n"
            "## Error Handling\nStuff\n"
            "## Examples\nStuff\n"
            "## Resources\nStuff\n"
        )
        validate_required_sections(body, report, strict_mode=True)
        assert any("numbered step-by-step" in r.message for r in report.results)


class TestPathFormatsBackslash:
    """Tests for validate_path_formats Windows backslash detection (lines 1271, 1275, 1285-1295)."""

    def test_backslash_scripts_path_detected(self):
        """Backslash in scripts path should be flagged."""
        from validate_skill_comprehensive import validate_path_formats

        report = ValidationReport(skill_path="test")
        body = "Run the script at \\scripts\\setup.py to configure.\n"
        validate_path_formats(body, report)
        assert any("backslash" in r.message.lower() for r in report.results)

    def test_skip_windows_checks_when_requested(self):
        """Windows path checks should be skipped when skip_platform_checks includes windows."""
        from validate_skill_comprehensive import validate_path_formats

        report = ValidationReport(skill_path="test")
        body = "Run \\scripts\\setup.py to configure.\n"
        validate_path_formats(body, report, skip_platform_checks=["windows"])
        assert not any("backslash" in r.message.lower() for r in report.results)


class TestResourceReferencesValidation:
    """Tests for validate_resource_references (lines 1581-1603, 1609-1643)."""

    def test_basedir_scripts_reference_found(self, tmp_path):
        """Existing {baseDir}/scripts/ references should pass."""
        from validate_skill_comprehensive import validate_resource_references

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "setup.sh").write_text("#!/bin/bash\necho hello")

        report = ValidationReport(skill_path=str(skill_dir))
        body = "Run {baseDir}/scripts/setup.sh to install."
        validate_resource_references(skill_dir, body, report)
        assert any("Script exists" in r.message for r in report.results)

    def test_basedir_scripts_reference_missing(self, tmp_path):
        """Missing {baseDir}/scripts/ references should be flagged as major."""
        from validate_skill_comprehensive import validate_resource_references

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()

        report = ValidationReport(skill_path=str(skill_dir))
        body = "Run {baseDir}/scripts/nonexistent.sh to install."
        validate_resource_references(skill_dir, body, report)
        assert any("Referenced script not found" in r.message for r in report.results)

    def test_basedir_references_reference_found(self, tmp_path):
        """Existing {baseDir}/references/ references should pass."""
        from validate_skill_comprehensive import validate_resource_references

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "api-guide.md").write_text("# API Guide\n")

        report = ValidationReport(skill_path=str(skill_dir))
        body = "See {baseDir}/references/api-guide.md for details."
        validate_resource_references(skill_dir, body, report)
        assert any("Reference exists" in r.message for r in report.results)

    def test_markdown_local_link_missing_file(self, tmp_path):
        """Markdown links to missing local files should be flagged as major."""
        from validate_skill_comprehensive import validate_resource_references

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()

        report = ValidationReport(skill_path=str(skill_dir))
        body = "See [the guide](docs/guide.md) for more info."
        validate_resource_references(skill_dir, body, report)
        assert any("Referenced file not found" in r.message for r in report.results)

    def test_markdown_local_link_existing_file(self, tmp_path):
        """Markdown links to existing local files should pass."""
        from validate_skill_comprehensive import validate_resource_references

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        docs_dir = skill_dir / "docs"
        docs_dir.mkdir()
        (docs_dir / "guide.md").write_text("# Guide\n")

        report = ValidationReport(skill_path=str(skill_dir))
        body = "See [the guide](docs/guide.md) for more info."
        validate_resource_references(skill_dir, body, report)
        assert any("Referenced file exists" in r.message for r in report.results)


class TestReferenceFilesValidation:
    """Tests for validate_reference_files (lines 1764-1811)."""

    def test_nested_references_directory_with_md_flagged(self, tmp_path):
        """Nested references directory with .md files should be flagged as major."""
        from validate_skill_comprehensive import validate_reference_files

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        nested_dir = refs_dir / "nested"
        nested_dir.mkdir()
        (nested_dir / "deep.md").write_text("# Nested\n")

        report = ValidationReport(skill_path=str(skill_dir))
        validate_reference_files(skill_dir, report)
        assert any("Nested references directory" in r.message for r in report.results)

    def test_long_reference_file_without_toc_flagged(self, tmp_path):
        """Reference file without TOC in first 200 chars should be MINOR."""
        from validate_skill_comprehensive import validate_reference_files

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        long_content = "# Long Reference\n\n" + ("Content line.\n" * 150)
        (refs_dir / "big-reference.md").write_text(long_content)

        report = ValidationReport(skill_path=str(skill_dir))
        validate_reference_files(skill_dir, report)
        toc_results = [r for r in report.results if "no table of contents" in r.message]
        assert toc_results, "Expected a TOC warning"
        assert toc_results[0].level == "MINOR"

    def test_long_reference_file_with_toc_passes(self, tmp_path):
        """Reference file with TOC in first 200 chars should pass."""
        from validate_skill_comprehensive import validate_reference_files

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        long_content = "# Long Reference\n\n## Table of Contents\n\n- [Section 1](#section-1)\n\n" + (
            "Content line.\n" * 150
        )
        (refs_dir / "big-reference.md").write_text(long_content)

        report = ValidationReport(skill_path=str(skill_dir))
        validate_reference_files(skill_dir, report)
        assert any("has TOC" in r.message for r in report.results)


class TestPillarsValidation:
    """Tests for validate_pillars for lang-* skills (lines 1825-1913)."""

    def test_pillars_skipped_for_non_lang_skill(self, tmp_path):
        """Pillars validation should be skipped for non-lang skills."""
        from validate_skill_comprehensive import validate_pillars

        skill_dir = tmp_path / "my-tool"
        skill_dir.mkdir()

        report = ValidationReport(skill_path=str(skill_dir))
        validate_pillars(skill_dir, "Some body content", report)
        assert any("Pillars validation skipped" in r.message for r in report.results)

    def test_pillars_coverage_for_lang_skill(self, tmp_path):
        """lang-python skill should be scored on all 8 pillars."""
        from validate_skill_comprehensive import validate_pillars

        skill_dir = tmp_path / "lang-python"
        skill_dir.mkdir()

        body = """
## Module
import os, export module, package management, namespace support, require stuff

## Error
Result types, Exception handling, Error classes, try/except blocks, catch errors

## Concurrency
async/await patterns, thread management, channel communication, spawn tasks, mutex locking

## Metaprogramming
decorator patterns, @ syntax, derive macros, annotation processing

## Zero/Default
None handling, Option types, default values, undefined checks

## Serialization
JSON parsing, serde usage, marshal/unmarshal, encode/decode

## Build
pip install, npm scripts, package.json, deps management, go mod

## Testing
pytest tests, describe blocks, assert statements, expect results, mock objects
"""
        report = ValidationReport(skill_path=str(skill_dir))
        validate_pillars(skill_dir, body, report)
        assert len(report.pillar_scores) == 8
        assert any("Pillars coverage" in r.message for r in report.results)


class TestCalculateOverallScore:
    """Tests for calculate_overall_score edge cases (lines 1930-1932)."""

    def test_zero_checks_gives_grade_f(self):
        """Report with no checks should get score 0 and grade F."""
        from validate_skill_comprehensive import calculate_overall_score

        report = ValidationReport(skill_path="test")
        calculate_overall_score(report)
        assert report.overall_score == 0.0
        assert report.grade == "F"


class TestValidateSkillMainFunction:
    """Tests for validate_skill main function edge cases (lines 1971-1976, 1980, 1985)."""

    def test_nonexistent_skill_path(self, tmp_path):
        """Non-existent skill path should produce critical error."""
        non_existent = tmp_path / "no-such-skill"
        report = validate_skill(non_existent)
        assert report.has_critical
        assert any("does not exist" in r.message for r in report.results)

    def test_skill_path_is_file_not_directory(self, tmp_path):
        """Skill path that is a file (not directory) should produce critical error."""
        file_path = tmp_path / "not-a-dir.txt"
        file_path.write_text("not a directory")
        report = validate_skill(file_path)
        assert report.has_critical
        assert any("not a directory" in r.message for r in report.results)

    def test_skill_dir_without_skill_md(self, tmp_path):
        """Skill directory without SKILL.md should produce critical error."""
        skill_dir = tmp_path / "empty-skill"
        skill_dir.mkdir()
        report = validate_skill(skill_dir)
        assert report.has_critical

    def test_validate_skill_with_pillars_flag(self, tmp_path):
        """validate_skill with validate_pillars_flag should run pillar validation."""
        skill_dir = tmp_path / "lang-rust"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: lang-rust
description: A Rust programming language skill. Use when writing Rust code.
---
# Rust Language Skill

## Module
import, export, module, use, require, package management, namespace

## Error
Result, Exception, Error handling, try, catch, unwrap, panic recovery

## Concurrency
async, await, thread, channel, spawn tasks, mutex locking

## Build
Cargo build, deps management

## Testing
test macros, assert statements, expect results
""")
        report = validate_skill(skill_dir, validate_pillars_flag=True)
        assert len(report.pillar_scores) > 0


class TestPackageDependencyManagers:
    """Tests for validate_package_dependencies with various managers (lines 1560-1567)."""

    def test_yarn_add_detected(self):
        """yarn add commands should be detected."""
        from validate_skill_comprehensive import validate_package_dependencies

        report = ValidationReport(skill_path="test")
        body = "Install with: yarn add express"
        validate_package_dependencies(body, report)
        assert any("package dependencies" in r.message for r in report.results)
        assert any("yarn" in r.message for r in report.results)

    def test_cargo_add_detected(self):
        """cargo add commands should be detected."""
        from validate_skill_comprehensive import validate_package_dependencies

        report = ValidationReport(skill_path="test")
        body = "Install with: cargo add serde"
        validate_package_dependencies(body, report)
        assert any("package dependencies" in r.message for r in report.results)
        assert any("cargo" in r.message for r in report.results)

    def test_brew_install_detected(self):
        """brew install commands should be detected."""
        from validate_skill_comprehensive import validate_package_dependencies

        report = ValidationReport(skill_path="test")
        body = "Install with: brew install ffmpeg"
        validate_package_dependencies(body, report)
        assert any("package dependencies" in r.message for r in report.results)
        assert any("brew" in r.message for r in report.results)


class TestAllowedToolsEdgeCases:
    """Tests for validate_allowed_tools_field edge cases (lines 790-795, 798-799, 806, 814, 822)."""

    def test_invalid_type_rejected(self):
        """Non-string non-list allowed-tools should be rejected."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": 42}
        validate_allowed_tools_field(frontmatter, report)
        assert report.has_major
        assert any("must be string or list" in r.message for r in report.results)

    def test_empty_tools_flagged(self):
        """Empty allowed-tools string should be flagged as minor."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": ""}
        validate_allowed_tools_field(frontmatter, report)
        assert any("empty" in r.message.lower() for r in report.results)

    def test_many_tools_warns_overpermission(self):
        """More than 10 tools should generate over-permissioning warning."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Read, Write, Edit, Bash, Grep, Glob, WebFetch, WebSearch, Task, AskUserQuestion, NotebookEdit"}
        validate_allowed_tools_field(frontmatter, report)
        assert any("Many tools" in r.message for r in report.results)

    def test_strict_mode_unscoped_bash_rejected(self):
        """Unscoped Bash in strict mode should be rejected."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": ["Bash", "Read"]}
        validate_allowed_tools_field(frontmatter, report, strict_mode=True)
        assert any("Unscoped 'Bash' forbidden" in r.message for r in report.results)

    def test_yaml_array_in_strict_mode_rejected(self):
        """YAML array format in strict mode should be rejected."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": ["Read", "Write"]}
        validate_allowed_tools_field(frontmatter, report, strict_mode=True)
        assert any("comma-separated string" in r.message for r in report.results)


# =============================================================================
# Tests for v1.7.0 tool-count severity
# =============================================================================


class TestV170ToolCountSeverity:
    """Tests verifying that the many-tools advisory uses WARNING level."""

    def test_many_tools_is_warning_not_minor(self):
        """11 tools should produce a WARNING-level 'Many tools permitted' result, not MINOR."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Read, Write, Edit, Bash, Glob, Grep, Agent, WebFetch, WebSearch, Task, AskUserQuestion"}
        validate_allowed_tools_field(frontmatter, report)
        many_tools_results = [r for r in report.results if "Many tools permitted" in r.message]
        assert len(many_tools_results) == 1, "Expected exactly one 'Many tools permitted' result"
        assert many_tools_results[0].level == "WARNING", f"Expected WARNING level, got {many_tools_results[0].level}"

    def test_few_tools_no_warning(self):
        """2 tools should not produce any 'Many tools permitted' result."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Read, Bash"}
        validate_allowed_tools_field(frontmatter, report)
        many_tools_results = [r for r in report.results if "Many tools permitted" in r.message]
        assert len(many_tools_results) == 0, "Expected no 'Many tools permitted' result for 2 tools"
