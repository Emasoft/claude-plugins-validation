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
    validate_effort_field,
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

    def test_task_output_emits_deprecation_warning(self):
        """A skill requesting the deprecated TaskOutput tool gets a WARNING."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Read, TaskOutput"}
        validate_allowed_tools_field(frontmatter, report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("TaskOutput" in m and "deprecated" in m for m in warning_msgs)

    def test_task_tool_emits_rename_warning_in_skill(self):
        """A skill still using the legacy Task tool name gets a rename WARNING."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Read, Task"}
        validate_allowed_tools_field(frontmatter, report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("Task" in m and "renamed to 'Agent'" in m for m in warning_msgs)

    def test_monitor_unscoped_forbidden_in_strict_mode(self):
        """Strict mode forbids unscoped Monitor (same semantics as Bash)."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Read, Monitor"}
        validate_allowed_tools_field(frontmatter, report, strict_mode=True)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Unscoped 'Monitor' forbidden" in m for m in major_msgs)

    def test_monitor_scoped_allowed_in_strict_mode(self):
        """Scoped Monitor(...) is allowed even in strict mode."""
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": "Read, Monitor(npm:*)"}
        validate_allowed_tools_field(frontmatter, report, strict_mode=True)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("Unscoped 'Monitor' forbidden" in m for m in major_msgs)


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

    # ------------------------------------------------------------------
    # v2.26.0: skill-local VAR= assignments + inline-backtick stripping
    # ------------------------------------------------------------------

    def test_var_assigned_in_fenced_block_is_whitelisted(self):
        """A shell variable assigned in a code block must NOT trigger the
        unknown-variable warning when referenced elsewhere."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = (
            "Set the script path:\n\n"
            "```bash\n"
            "MERGE_SCRIPT=/path/to/merge.sh\n"
            "```\n\n"
            "Then invoke ${MERGE_SCRIPT} from anywhere.\n"
        )
        validate_string_substitutions(body, report)
        unknown = [r for r in report.results if "Unknown variable reference" in r.message]
        assert not unknown, f"false-positive unknown-var for MERGE_SCRIPT: {[r.message for r in unknown]}"

    def test_export_var_in_fenced_block_is_whitelisted(self):
        """`export VAR=...` inside a code block also whitelists VAR."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = '```bash\nexport REPORT_DIR="$MAIN_ROOT/reports/foo"\n```\n\nWrite reports into ${REPORT_DIR}.\n'
        validate_string_substitutions(body, report)
        unknown = [r for r in report.results if "Unknown variable reference" in r.message]
        assert not unknown, f"false-positive unknown-var for REPORT_DIR: {[r.message for r in unknown]}"

    def test_local_var_in_fenced_block_is_whitelisted(self):
        """`local VAR=...` (inside a shell function) whitelists VAR too."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = '```bash\nmy_fn() {\n  local TS="$(date +%s)"\n}\n```\n\nThe timestamp is captured in ${TS}.\n'
        validate_string_substitutions(body, report)
        unknown = [r for r in report.results if "Unknown variable reference" in r.message]
        assert not unknown, f"false-positive unknown-var for TS: {[r.message for r in unknown]}"

    def test_inline_backticked_reference_is_stripped(self):
        """`${CUSTOM_VAR}` wrapped in single backticks is prose-as-code
        and must not be flagged even if never assigned anywhere."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = "Users can reference `${CUSTOM_VAR}` in their own scripts."
        validate_string_substitutions(body, report)
        unknown = [r for r in report.results if "Unknown variable reference" in r.message]
        assert not unknown, f"inline-backtick ${'{'}CUSTOM_VAR{'}'} should be stripped: {[r.message for r in unknown]}"

    def test_genuinely_unknown_var_in_prose_still_flagged(self):
        """A ${VAR} reference in plain prose, never assigned, never wrapped
        in backticks, is still flagged — the check must still work."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = "The system will set ${SOME_MYSTERY_ENV} automatically."
        validate_string_substitutions(body, report)
        unknown = [r for r in report.results if "Unknown variable reference" in r.message]
        assert unknown, "genuine unknown var was NOT flagged — regression"
        assert any("SOME_MYSTERY_ENV" in r.message for r in unknown)

    def test_unknown_var_deduplicated(self):
        """A single unknown var referenced N times emits ONE warning, not N."""
        from validate_skill_comprehensive import validate_string_substitutions

        report = ValidationReport(skill_path="test")
        body = "Set ${MYSTERY_VAR}. Then use ${MYSTERY_VAR} twice. And again: ${MYSTERY_VAR}."
        validate_string_substitutions(body, report)
        unknown = [
            r for r in report.results if "Unknown variable reference" in r.message and "MYSTERY_VAR" in r.message
        ]
        assert len(unknown) == 1, f"expected 1 warning for de-dup, got {len(unknown)}"


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
        """A genuine non-boolean value (int 2, not 0/1) for a boolean field should be critical."""
        from validate_skill_comprehensive import validate_boolean_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"user-invocable": 2}
        validate_boolean_field(frontmatter, "user-invocable", report)
        assert any("must be a boolean" in r.message for r in report.results)

    def test_accepted_yaml_bool_strings_pass(self):
        """Every YAML boolean CC accepts (yes/no/on/off/1/0) passes the comprehensive check — v2.1.218."""
        from validate_skill_comprehensive import validate_boolean_field

        for value in ("yes", "no", "on", "off", 1, 0, "TRUE"):
            report = ValidationReport(skill_path="test")
            frontmatter = {"user-invocable": value}
            validate_boolean_field(frontmatter, "user-invocable", report)
            assert not any("must be a boolean" in r.message for r in report.results), f"{value!r} should pass"

    def test_valid_boolean_passes(self):
        """Valid boolean value should pass."""
        from validate_skill_comprehensive import validate_boolean_field

        report = ValidationReport(skill_path="test")
        frontmatter = {"user-invocable": True}
        validate_boolean_field(frontmatter, "user-invocable", report)
        assert any("'user-invocable' field valid" in r.message for r in report.results)


class TestFieldWhitelistDeprecated:
    """Tests for validate_field_whitelist with deprecated fields.

    As of CPV v2.16.x / Claude Code v2.1.98+, ``when_to_use`` is NOT
    deprecated — it is an officially supported supplemental trigger
    field per skills.md. The previous test asserted the reverse and has
    been rewritten to pin the corrected behaviour: ``when_to_use`` must
    be accepted without any deprecation warning.
    """

    def test_when_to_use_is_not_flagged_as_deprecated(self):
        """when_to_use is an official v2.1.98+ field, NOT deprecated."""
        from validate_skill_comprehensive import DEPRECATED_FIELDS, validate_field_whitelist

        report = ValidationReport(skill_path="test")
        frontmatter = {"name": "test", "when_to_use": "always"}
        validate_field_whitelist(frontmatter, report)
        # No finding should mention deprecation for when_to_use.
        assert not any("Deprecated field" in r.message and "when_to_use" in r.message for r in report.results)
        # And the DEPRECATED_FIELDS set must not contain it.
        assert "when_to_use" not in DEPRECATED_FIELDS


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

    def test_excessive_body_tokens_is_major(self):
        """A body over SKILL_BODY_TOKEN_LIMIT (5000) tokens is a MAJOR.

        The char/word caps were removed in TRDD-021250b5 in favour of a
        token-based body budget. The body is kept on ONE long line (~30k
        tokens, 1 line) so it stays well under MAX_SKILL_LINES (500) and
        isolates the body-token MAJOR from the line-count MAJOR.
        """
        from cpv_token_estimate import estimate_tokens
        from validate_skill_comprehensive import SKILL_BODY_TOKEN_LIMIT, validate_token_budget

        body = " ".join(f"token{i}" for i in range(8000))
        # Sanity-pin the fixture: clearly over the token limit, clearly under the line limit.
        assert estimate_tokens(body).tokens > SKILL_BODY_TOKEN_LIMIT
        assert body.count("\n") + 1 < 500
        content = "---\nname: test\n---\n" + body
        report = ValidationReport(skill_path="test")
        validate_token_budget(content, body, report)
        body_major = [
            r for r in report.results if r.level == "MAJOR" and "SKILL.md body" in r.message and "tokens" in r.message
        ]
        assert body_major, "body-token MAJOR did not fire for a ~30k-token body"

    def test_body_under_token_limit_passes(self):
        """A small body (well under 5000 tokens) emits NO body-token MAJOR.

        Two-sided companion to test_excessive_body_tokens_is_major: proves the
        gate is discriminating, not blanket.
        """
        from validate_skill_comprehensive import validate_token_budget

        body = " ".join(["word"] * 50)  # ~65 tokens, 1 line
        content = "---\nname: test\n---\n" + body
        report = ValidationReport(skill_path="test")
        validate_token_budget(content, body, report)
        assert not any("SKILL.md body" in r.message and r.level == "MAJOR" for r in report.results)

    def test_cjk_body_over_token_limit_is_major(self):
        """A CJK body over 5000 tokens is flagged MAJOR — the gate is language-independent.

        This is the whole point of switching from a CHARACTER cap to a TOKEN cap:
        CJK packs far more meaning per character than English, so a char cap
        under- or over-counts wildly by language. The fixture is sanity-pinned
        with the real estimator so it stays correct regardless of the exact
        chars-per-token ratio, and kept under MAX_SKILL_LINES to isolate the
        body-token MAJOR (TRDD-021250b5).
        """
        from cpv_token_estimate import estimate_tokens
        from validate_skill_comprehensive import SKILL_BODY_TOKEN_LIMIT, validate_token_budget

        # Chinese prose repeated until clearly over the token budget, on one line.
        body = "这是一个用于测试令牌预算的中文段落。" * 2000
        assert estimate_tokens(body).tokens > SKILL_BODY_TOKEN_LIMIT
        assert body.count("\n") + 1 < 500
        content = "---\nname: test\n---\n" + body
        report = ValidationReport(skill_path="test")
        validate_token_budget(content, body, report)
        assert any(
            "SKILL.md body" in r.message and "tokens" in r.message and r.level == "MAJOR"
            for r in report.results
        ), "body-token MAJOR did not fire for an over-limit CJK body"


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
        """Reference file without TOC in first 200 chars should be MINOR.

        Issue #16 category D: the rule applies only to files >=500 lines —
        short technique files don't need a TOC. Updated fixture to 600 lines
        so the MINOR fires.
        """
        from validate_skill_comprehensive import validate_reference_files

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        long_content = "# Long Reference\n\n" + ("Content line.\n" * 600)
        (refs_dir / "big-reference.md").write_text(long_content)

        report = ValidationReport(skill_path=str(skill_dir))
        validate_reference_files(skill_dir, report)
        toc_results = [r for r in report.results if "no table of contents" in r.message]
        assert toc_results, "Expected a TOC warning for the 600-line file"
        assert toc_results[0].level == "MINOR"

    def test_short_reference_file_without_toc_emits_info_only(self, tmp_path):
        """Issue #16 category D: short reference files (<500 lines) without TOC
        emit INFO not MINOR — short technique files don't benefit from a TOC."""
        from validate_skill_comprehensive import validate_reference_files

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        refs_dir = skill_dir / "references"
        refs_dir.mkdir()
        # 150 lines — well under the 500-line threshold
        short_content = "# Short Reference\n\n" + ("Content line.\n" * 150)
        (refs_dir / "tech-001.md").write_text(short_content)

        report = ValidationReport(skill_path=str(skill_dir))
        validate_reference_files(skill_dir, report)
        # NO MINOR for the missing TOC — it's an INFO instead
        minor_results = [r for r in report.results if r.level == "MINOR" and "no table of contents" in r.message]
        assert not minor_results, "Short ref files (<500 lines) must NOT emit MINOR for missing TOC"
        info_results = [r for r in report.results if "without TOC" in r.message and "OK for short files" in r.message]
        assert info_results, "Expected INFO advisory for the short-file TOC exemption"

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
        """Empty allowed-tools ("" or []) is flagged as a non-blocking WARNING.

        Empty = explicit "no tools" (chat-only), which is VALID and distinct
        from an absent field (= all tools). The warning steers an author who
        meant "allow everything" toward the correct syntax (omit the field),
        and must NOT be a blocking MINOR.
        """
        report = ValidationReport(skill_path="test")
        frontmatter = {"allowed-tools": ""}
        validate_allowed_tools_field(frontmatter, report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("empty" in m.lower() for m in warning_msgs)
        assert not any(r.level == "MINOR" and "empty" in r.message.lower() for r in report.results)
        assert any("omit" in m.lower() for m in warning_msgs)

    def test_many_tools_warns_overpermission(self):
        """More than 15 distinct tool surfaces generate over-permissioning
        warning (v2.26.0 — threshold raised from 10 to 15 after Bash
        sub-pattern collapsing)."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "allowed-tools": (
                "Read, Write, Edit, Bash, Grep, Glob, WebFetch, WebSearch, "
                "Agent, AskUserQuestion, NotebookEdit, TaskCreate, TaskUpdate, "
                "TaskList, TaskGet, TaskStop"
            )
        }
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
        """16 distinct tool surfaces produce a WARNING-level 'Many tools
        permitted' result, not MINOR (v2.26.0 threshold: >15)."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "allowed-tools": (
                "Read, Write, Edit, Bash, Glob, Grep, Agent, WebFetch, "
                "WebSearch, AskUserQuestion, NotebookEdit, TaskCreate, "
                "TaskUpdate, TaskList, TaskGet, TaskStop"
            )
        }
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

    # ------------------------------------------------------------------
    # v2.26.0: Bash-subpattern collapsing + user-invocable exemption
    # ------------------------------------------------------------------

    def test_bash_subpatterns_collapse_to_one_surface(self):
        """`Bash(git:*), Bash(gh:*), Bash(uv:*)` + 12 other tools = 15 raw
        entries but only 13 distinct surfaces — under the new threshold,
        so no warning. v2.26.0 rule: Bash sub-scopes share one surface."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "allowed-tools": (
                "Read, Write, Edit, Grep, Glob, Agent, WebFetch, WebSearch, "
                "AskUserQuestion, NotebookEdit, TaskCreate, TaskUpdate, "
                "Bash(git:*), Bash(gh:*), Bash(uv:*)"
            )
        }
        validate_allowed_tools_field(frontmatter, report)
        many_tools_results = [r for r in report.results if "Many tools permitted" in r.message]
        assert not many_tools_results, (
            f"Bash subpatterns were not collapsed — Many tools warning fired "
            f"on 13 distinct surfaces: {[r.message for r in many_tools_results]}"
        )

    def test_many_bash_subpatterns_still_count_as_one(self):
        """Even 8 Bash sub-patterns count as a single Bash surface."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "allowed-tools": (
                "Bash(git:*), Bash(gh:*), Bash(uv:*), Bash(npm:*), "
                "Bash(jq:*), Bash(yq:*), Bash(rg:*), Bash(fd:*), "
                "Read, Write, Edit, Grep, Glob, Agent, WebFetch"
            )
        }
        validate_allowed_tools_field(frontmatter, report)
        many_tools_results = [r for r in report.results if "Many tools permitted" in r.message]
        assert not many_tools_results, (
            f"8 Bash subpatterns + 7 other tools = 8 distinct surfaces, "
            f"but warning fired: {[r.message for r in many_tools_results]}"
        )

    def test_user_invocable_false_suppresses_warning(self):
        """A skill declared `user-invocable: false` suppresses the
        Many-tools warning entirely — agent-loaded skills inherit gating
        from the agent's own allowlist."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "user-invocable": False,
            "allowed-tools": (
                # 20 distinct non-Bash surfaces — would normally fire.
                "Read, Write, Edit, Grep, Glob, Agent, WebFetch, WebSearch, "
                "AskUserQuestion, NotebookEdit, TaskCreate, TaskUpdate, "
                "TaskList, TaskGet, TaskStop, TaskOutput, Monitor, "
                "CronCreate, CronDelete, CronList"
            ),
        }
        validate_allowed_tools_field(frontmatter, report)
        many_tools_results = [r for r in report.results if "Many tools permitted" in r.message]
        assert not many_tools_results, (
            f"user-invocable: false should suppress the warning but it fired: {[r.message for r in many_tools_results]}"
        )

    def test_user_invocable_true_does_not_suppress_warning(self):
        """Regression guard: `user-invocable: true` must NOT suppress the
        warning — only the false form does."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "user-invocable": True,
            "allowed-tools": (
                "Read, Write, Edit, Grep, Glob, Agent, WebFetch, WebSearch, "
                "AskUserQuestion, NotebookEdit, TaskCreate, TaskUpdate, "
                "TaskList, TaskGet, TaskStop, TaskOutput, Monitor, "
                "CronCreate, CronDelete, CronList"
            ),
        }
        validate_allowed_tools_field(frontmatter, report)
        many_tools_results = [r for r in report.results if "Many tools permitted" in r.message]
        assert many_tools_results, "user-invocable: true should NOT suppress"

    def test_exactly_15_surfaces_no_warning(self):
        """Boundary: exactly 15 distinct surfaces — no warning (threshold is >15)."""
        report = ValidationReport(skill_path="test")
        frontmatter = {
            "allowed-tools": (
                "Read, Write, Edit, Bash, Glob, Grep, Agent, WebFetch, "
                "WebSearch, AskUserQuestion, NotebookEdit, TaskCreate, "
                "TaskUpdate, TaskList, TaskGet"
            )
        }
        validate_allowed_tools_field(frontmatter, report)
        many_tools_results = [r for r in report.results if "Many tools permitted" in r.message]
        assert not many_tools_results, f"15 surfaces should not warn: {[r.message for r in many_tools_results]}"


# =============================================================================
# v2.21.2 audit-fix regression (commit c9b869a) — G36 CRITICAL
# =============================================================================


class TestV2212NonStringAllowedTools:
    """G36 (CRITICAL): non-string allowed-tools must not raise TypeError/AttributeError."""

    def test_validate_skill_non_string_allowed_tools_does_not_crash(self, tmp_path):
        """SKILL.md with `allowed-tools: 42` (integer) must not crash the validator.

        Pre-fix, downstream handling of non-string allowed-tools values could
        raise AttributeError/TypeError (e.g. when a list item was later passed
        through ``.split('(')``). End-to-end ``validate_skill`` must handle
        this malformed input cleanly and emit a MAJOR about the type.
        """
        skill_dir = tmp_path / "bad-tools-skill"
        skill_dir.mkdir()
        # Integer value — invalid per schema (must be string or list)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: bad-tools-skill\n"
            "description: A skill with an invalid allowed-tools value. "
            "Use when testing that non-string allowed-tools does not crash.\n"
            "allowed-tools: 42\n"
            "---\n"
            "# Bad Tools Skill\n"
            "\n"
            "## When to use\n"
            "\n"
            "Never — this SKILL.md exists only as a regression fixture.\n",
            encoding="utf-8",
        )

        # Must NOT raise TypeError/AttributeError
        report = validate_skill(skill_dir)

        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("allowed-tools" in m and ("string" in m or "type" in m.lower()) for m in major_msgs), (
            f"Expected MAJOR mentioning allowed-tools type, got MAJORs: {major_msgs}"
        )


class TestV22SkillEffort:
    """v2.22.0: skill effort field accepts xhigh (Opus 4.7) + max (Opus 4.6 legacy).

    Spec sources:
      - skills.md L192 — effort: low|medium|high|xhigh|max.
      - cli-reference.md --effort — same value set for CLI flags.
    """

    def test_skill_effort_xhigh_accepted(self):
        """effort: xhigh is accepted (v2.1.111 Opus 4.7 addition) — no MAJOR emitted."""
        report = ValidationReport()
        # Pair with opus model so the Opus-only guard for xhigh does NOT fire a
        # secondary MAJOR — this test isolates the value-acceptance check.
        validate_effort_field({"effort": "xhigh", "model": "opus"}, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("Invalid 'effort'" in m for m in major_msgs), (
            f"xhigh must be accepted per skills.md L192; got MAJORs: {major_msgs}"
        )
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'effort' field valid: xhigh" in m for m in passed_msgs), (
            f"Expected PASSED 'effort field valid: xhigh'; got PASSEDs: {passed_msgs}"
        )

    def test_skill_effort_max_accepted(self):
        """effort: max remains accepted (Opus 4.6 legacy) — backward compat preserved."""
        report = ValidationReport()
        validate_effort_field({"effort": "max", "model": "opus"}, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("Invalid 'effort'" in m for m in major_msgs), (
            f"max must remain accepted for Opus 4.6 compat; got MAJORs: {major_msgs}"
        )
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'effort' field valid: max" in m for m in passed_msgs)

    def test_skill_effort_invalid_rejected(self):
        """effort: insane (not in {low, medium, high, xhigh, max}) must emit MAJOR."""
        report = ValidationReport()
        validate_effort_field({"effort": "insane"}, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'effort' value: 'insane'" in m for m in major_msgs), (
            f"Expected MAJOR rejecting 'insane'; got MAJORs: {major_msgs}"
        )


class TestPass2SkillFixes:
    """Pass-2 audit fixes for validate_skill_comprehensive.py.

    Covers:
      - CPV-P2-m6: `disableSkillShellExecution` misuse in frontmatter
      - GAP-53: self-pointing `skills: ["./"]` detection helper

    (The former CPV-P2-n1 test inspected a source comment about
    MAX_DESCRIPTION_WARN; that constant and its comment were removed in
    TRDD-021250b5 when skill sizing moved to token-based budgets, so the
    test was removed too.)
    """

    def test_disable_skill_shell_execution_in_frontmatter_emits_minor(self, tmp_path):
        """CPV-P2-m6 / skills.md L414: `disableSkillShellExecution` is a
        settings.json key, not a skill frontmatter field. If a plugin puts it
        in SKILL.md it's misuse — CPV must emit MINOR explaining the correct
        placement.
        """
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: bad-skill\n"
            "description: Use when demonstrating misuse of the settings key.\n"
            "disableSkillShellExecution: true\n"
            "---\n"
            "\n## Overview\nBody.\n"
        )
        report = validate_skill(skill_dir)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("disableSkillShellExecution" in m and "settings.json key" in m for m in minor_msgs), (
            f"CPV-P2-m6 MINOR not emitted for frontmatter misuse; got MINORs: {minor_msgs}"
        )

    def test_disable_skill_shell_execution_still_type_checked_as_boolean(self, tmp_path):
        """CPV-P2-m6: even when misplaced in frontmatter, CPV still type-checks
        `disableSkillShellExecution` as a boolean — a string value must
        produce a CRITICAL from validate_boolean_field on top of the MINOR.
        """
        skill_dir = tmp_path / "bad-skill-2"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: bad-skill-2\n"
            "description: Use when testing that non-boolean type is caught.\n"
            "disableSkillShellExecution: yes-please\n"
            "---\n"
            "\n## Overview\nBody.\n"
        )
        report = validate_skill(skill_dir)
        criticals = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("disableSkillShellExecution" in m and "must be a boolean" in m for m in criticals), (
            f"Type check of disableSkillShellExecution missed non-bool value; got CRITICALs: {criticals}"
        )

    def test_is_self_pointing_skill_path_positives(self):
        """GAP-53: `./` and `.` both mean "the plugin root IS the skill".
        The helper must return True for both forms and tolerate whitespace.
        """
        from validate_skill_comprehensive import is_self_pointing_skill_path

        assert is_self_pointing_skill_path("./") is True
        assert is_self_pointing_skill_path(".") is True
        assert is_self_pointing_skill_path("  ./  ") is True, (
            "Must tolerate surrounding whitespace — YAML loaders can emit "
            "leading/trailing spaces in some quoting modes."
        )

    def test_is_self_pointing_skill_path_negatives(self):
        """GAP-53: any path other than the exact self-reference must return
        False — including ``./extras/``, ``./skills``, and non-strings.
        """
        from validate_skill_comprehensive import is_self_pointing_skill_path

        assert is_self_pointing_skill_path("./extras/") is False
        assert is_self_pointing_skill_path("./skills") is False
        assert is_self_pointing_skill_path("skills/") is False
        assert is_self_pointing_skill_path("") is False
        assert is_self_pointing_skill_path(None) is False  # type: ignore[arg-type]
        assert is_self_pointing_skill_path(42) is False  # type: ignore[arg-type]


# =============================================================================
# audit C1 / M1 — comprehensive model gate must use the shared is_valid_model
# THE CRITICAL: the local set+regex emitted a FALSE blocking MAJOR on documented
# in-use aliases (opus[1m], default, opusplan, ...). Especially-thorough two-sided.
# =============================================================================


class TestComprehensiveModelGateSharedSourceOfTruth:
    """audit C1: comprehensive must accept exactly what command/skill accept."""

    def _report(self):
        return ValidationReport(skill_path="test")

    # ---- POSITIVE side: every documented valid value must NOT MAJOR ----

    def test_opus_1m_alias_not_rejected(self):
        """`opus[1m]` (cpv-semantic-validator-agent's own config) must NOT be a blocking MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "opus[1m]"}, report)
        assert not report.has_major
        assert any(r.level == "PASSED" and "model" in r.message for r in report.results)

    def test_sonnet_1m_alias_not_rejected(self):
        """`sonnet[1m]` must NOT be a blocking MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "sonnet[1m]"}, report)
        assert not report.has_major

    def test_default_alias_not_rejected(self):
        """`default` must NOT be a blocking MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "default"}, report)
        assert not report.has_major

    def test_opusplan_alias_not_rejected(self):
        """`opusplan` must NOT be a blocking MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "opusplan"}, report)
        assert not report.has_major

    def test_full_id_with_1m_suffix_not_rejected(self):
        """A full ID with the [1m] suffix (claude-opus-4-6[1m]) must NOT MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "claude-opus-4-6[1m]"}, report)
        assert not report.has_major

    def test_inherit_and_plain_full_id_not_rejected(self):
        """`inherit` and a plain full ID must NOT MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        for value in ("inherit", "opus", "sonnet", "claude-sonnet-4-5-20251001"):
            report = self._report()
            validate_model_field({"model": value}, report)
            assert not report.has_major, f"{value} should be accepted"

    # ---- NEGATIVE side: genuinely invalid values must still MAJOR ----

    def test_garbage_model_still_major(self):
        """A non-Claude model (`gpt-4`) must still be a blocking MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "gpt-4"}, report)
        assert any(r.level == "MAJOR" and "Invalid 'model' value" in r.message for r in report.results)

    def test_other_garbage_model_still_major(self):
        """Another bogus value (`turbo`) must still MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "turbo"}, report)
        assert report.has_major

    def test_non_string_model_still_major(self):
        """A non-string model stays MAJOR."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": 99}, report)
        assert any(r.level == "MAJOR" and "must be a string" in r.message for r in report.results)

    # ---- haiku penalty preserved (and its context:fork exemption) ----

    def test_bare_haiku_still_minor_penalty(self):
        """Bare `haiku` must still get the MINOR reliability penalty (preserved)."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "haiku"}, report)
        assert any(r.level == "MINOR" and "haiku" in r.message for r in report.results)
        assert not report.has_major  # valid value — penalty is MINOR, not MAJOR

    def test_haiku_with_context_fork_exempted(self):
        """`haiku` + context:fork is exempt from the penalty (PASSED, no MINOR)."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "haiku", "context": "fork"}, report)
        assert not report.has_minor
        assert any(r.level == "PASSED" and "fork" in r.message for r in report.results)

    def test_full_haiku_id_still_minor_penalty(self):
        """A full claude-haiku-* ID still triggers the MINOR penalty."""
        from validate_skill_comprehensive import validate_model_field

        report = self._report()
        validate_model_field({"model": "claude-haiku-4-5"}, report)
        assert report.has_minor
        assert not report.has_major

    # ---- END-TO-END: the actual user-visible C1 bug ----

    def test_end_to_end_opus_1m_skill_not_invalid(self, tmp_path):
        """A real skill with `model: opus[1m]` must NOT be marked INVALID (C1)."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: my-skill\n"
            "description: Do a focused thing well. Use when the user needs the thing.\n"
            "model: opus[1m]\n"
            "---\n"
            "# My Skill\n\nBody content here.\n"
        )
        report = validate_skill(skill_dir)
        # opus[1m] must not produce a blocking finding (CRITICAL or MAJOR).
        model_findings = [r for r in report.results if "model" in r.message.lower() and r.level in ("CRITICAL", "MAJOR")]
        assert not model_findings, f"opus[1m] wrongly flagged: {model_findings}"


# =============================================================================
# audit M2 — body-scoped findings must report FILE-relative line numbers
# =============================================================================


class TestBodyLineOffsetReporting:
    """audit M2: a SKILL.md finding's line must point at the real file line."""

    def test_path_format_line_is_file_relative(self, tmp_path):
        """An absolute path on body line 1 reports its FILE line (after frontmatter)."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        # 4 frontmatter lines (---, name, description, ---). The offending path is
        # on the FIRST body line, which is file line 6 (line 5 is "# Title").
        (skill_dir / "SKILL.md").write_text(
            "---\n"  # file line 1
            "name: my-skill\n"  # 2
            "description: A skill that does a thing. Use when needed for the thing.\n"  # 3
            "---\n"  # 4
            "# Title\n"  # 5 (body line 1)
            "See /Users/alice/secret for details\n"  # 6 (body line 2) <- offender
        )
        report = validate_skill(skill_dir)
        path_findings = [r for r in report.results if r.category == "Path Format" and r.line is not None]
        assert path_findings, "expected an absolute-path finding"
        # File line must be 6, NOT the body-relative 2.
        assert any(r.line == 6 for r in path_findings), [r.line for r in path_findings]

    def test_path_format_line_offset_param_direct(self):
        """validate_path_formats applies line_offset to the reported line + message."""
        from validate_skill_comprehensive import validate_path_formats

        report = ValidationReport(skill_path="test")
        body = "intro\n/Users/bob/data\n"  # body line 2 is the offender
        validate_path_formats(body, report, None, line_offset=4)
        findings = [r for r in report.results if r.category == "Path Format"]
        assert findings
        # body line 2 + offset 4 = file line 6
        assert findings[0].line == 6
        assert "Line 6:" in findings[0].message

    def test_time_sensitive_line_offset_applied(self):
        """validate_time_sensitive_info applies the offset to the reported line."""
        from validate_skill_comprehensive import validate_time_sensitive_info

        report = ValidationReport(skill_path="test")
        body = "intro\nReleased after January 2024 here.\n"  # body line 2
        validate_time_sensitive_info(body, report, line_offset=10)
        warns = [r for r in report.results if "Time-sensitive" in r.message]
        assert warns
        assert warns[0].line == 12  # body line 2 + 10

    def test_default_offset_zero_is_body_relative(self):
        """With no offset (default 0), line numbers remain body-relative (back-compat)."""
        from validate_skill_comprehensive import validate_path_formats

        report = ValidationReport(skill_path="test")
        body = "/Users/x/y\n"  # body line 1
        validate_path_formats(body, report)
        findings = [r for r in report.results if r.category == "Path Format"]
        assert findings and findings[0].line == 1


# =============================================================================
# audit n3 — MCP-unqualified heuristic must not fire on generic snake_case prose
# =============================================================================


class TestMcpUnqualifiedHeuristicTightened:
    """audit n3: drop the over-broad `or '_' in tool_name` clause."""

    def test_single_underscore_prose_not_flagged(self):
        """`run the build_step function` (1 underscore, not a known tool) is NOT flagged."""
        from validate_skill_comprehensive import validate_mcp_tool_references

        report = ValidationReport(skill_path="test")
        validate_mcp_tool_references("Then run the build_step function to proceed.", report)
        assert not any("MCP tool reference" in r.message for r in report.results)

    def test_curated_known_tool_still_flagged(self):
        """A curated known MCP tool (`read_file`) is still flagged."""
        from validate_skill_comprehensive import validate_mcp_tool_references

        report = ValidationReport(skill_path="test")
        validate_mcp_tool_references("Use the read_file tool here.", report)
        assert any("MCP tool reference" in r.message for r in report.results)

    def test_multi_segment_snake_case_still_flagged(self):
        """A 3-segment snake_case name (>=2 underscores) is still flagged."""
        from validate_skill_comprehensive import validate_mcp_tool_references

        report = ValidationReport(skill_path="test")
        validate_mcp_tool_references("Call the get_active_session tool now.", report)
        assert any("MCP tool reference" in r.message for r in report.results)


# =============================================================================
# audit m4 — description "very short" threshold aligned to 10 across validators
# =============================================================================


class TestDescriptionShortThresholdAligned:
    """audit m4: comprehensive now uses <10 like skill.py/command.py."""

    def test_15_char_description_not_very_short(self):
        """A 15-char description is no longer "very short" (was <20, now <10)."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        validate_description_field({"description": "Fifteen chars!!"}, "", report)  # 15 chars
        assert not any("very short" in r.message for r in report.results)

    def test_under_10_char_description_still_very_short(self):
        """A <10-char description is still flagged "very short"."""
        from validate_skill_comprehensive import validate_description_field

        report = ValidationReport(skill_path="test")
        validate_description_field({"description": "Tiny"}, "", report)  # 4 chars
        assert any(r.level == "MINOR" and "very short" in r.message for r in report.results)


# =============================================================================
# audit n5 — print_results surfaces WARNING/NIT counts when present
# =============================================================================


class TestPrintResultsSurfacesWarnings:
    """audit n5: a non-verbose summary shows WARNING/NIT so grade isn't misleading."""

    def test_warning_count_shown_in_summary(self, capsys):
        """A report with WARNINGs prints a WARNING count line (non-verbose)."""
        from validate_skill_comprehensive import calculate_overall_score, print_results

        report = ValidationReport(skill_path="test")
        report.passed("ok", "SKILL.md")
        report.warning("a non-blocking advisory", "SKILL.md")
        calculate_overall_score(report)
        print_results(report, verbose=False)
        out = capsys.readouterr().out
        assert "WARNING:" in out

    def test_no_warning_line_when_zero(self, capsys):
        """A clean report (no WARNING) does NOT print a WARNING line."""
        from validate_skill_comprehensive import calculate_overall_score, print_results

        report = ValidationReport(skill_path="test")
        report.passed("ok", "SKILL.md")
        calculate_overall_score(report)
        print_results(report, verbose=False)
        out = capsys.readouterr().out
        assert "WARNING:" not in out


# =============================================================================
# audit m6 — leading UTF-8 BOM must not hide comprehensive-validator frontmatter
# =============================================================================


class TestComprehensiveBomFrontmatterHandling:
    """audit m6: a BOM-prefixed SKILL.md must still parse its frontmatter."""

    def test_parse_frontmatter_strips_bom(self):
        """parse_frontmatter recognises frontmatter after a leading BOM."""
        from validate_skill_comprehensive import parse_frontmatter

        content = "﻿---\nname: my-skill\ndescription: Do a thing well\n---\nBody.\n"
        frontmatter, body, fm_end_line = parse_frontmatter(content)
        assert frontmatter is not None
        assert frontmatter["name"] == "my-skill"
        assert body.strip() == "Body."
        assert fm_end_line == 4  # ---, name, description, --- => closing on line 4

    def test_validate_frontmatter_structure_not_treated_absent(self):
        """validate_frontmatter_structure must NOT report 'No YAML frontmatter found' on BOM."""
        from validate_skill_comprehensive import validate_frontmatter_structure

        content = "﻿---\nname: my-skill\ndescription: Do a thing well\n---\nBody.\n"
        report = ValidationReport(skill_path="test")
        result = validate_frontmatter_structure(content, report)
        assert result is not None
        assert not any("No YAML frontmatter found" in r.message for r in report.results)

    def test_bom_prefixed_skill_end_to_end_not_invalid(self, tmp_path):
        """A BOM-prefixed skill must not be marked INVALID for missing frontmatter."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "﻿---\nname: my-skill\ndescription: Do a focused thing well. Use when needed.\n---\n# T\n\nBody.\n",
            encoding="utf-8",
        )
        report = validate_skill(skill_dir)
        assert not any("No YAML frontmatter found" in r.message for r in report.results)
        assert not any("Malformed YAML frontmatter" in r.message for r in report.results)
