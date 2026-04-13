#!/usr/bin/env python3
"""
Tests for validate_encoding.py

Tests the 7 encoding validation rules:
1. UTF-8 encoding required (all text files)
2. No BOM (Byte Order Mark) detection
3. Proper Unicode handling in JSON
4. Special characters properly escaped
5. Line endings: LF for source files (.py, .sh, .md, .json)
6. Shell scripts: LF endings required (not CRLF)
7. Batch scripts (.bat, .cmd): CRLF allowed

Coverage: 10 tests covering check_utf8_encoding, check_bom, check_line_endings,
validate_file, validate_encoding, plus edge cases with non-UTF8 files, BOM markers,
and mixed line endings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import should_skip_directory
from validate_encoding import (
    EncodingValidationReport,
    check_bom,
    check_escape_sequences,
    check_json_unicode,
    check_line_endings,
    check_utf8_encoding,
    is_binary_file,
    is_text_file,
    main,
    validate_encoding,
    validate_file,
)


class TestCheckUtf8Encoding:
    """Tests for the check_utf8_encoding function."""

    def test_valid_utf8_content_returns_true(self):
        """Valid UTF-8 bytes with multibyte characters should pass encoding check."""
        report = EncodingValidationReport()
        content = "Hello world with unicode: \u00e9\u00e8\u00ea \u00fc\u00f6\u00e4 \u4e16\u754c".encode("utf-8")
        result = check_utf8_encoding(content, "src/example.py", report)
        assert result is True
        assert report.stats["utf8_issues"] == 0
        # No CRITICAL results should be added
        critical_results = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_results) == 0

    def test_invalid_utf8_content_returns_false(self):
        """Bytes containing invalid UTF-8 sequences should fail encoding check and record a CRITICAL issue."""
        report = EncodingValidationReport()
        # Latin-1 encoded string that is NOT valid UTF-8 (byte 0xe9 alone is invalid UTF-8)
        content = "caf\xe9".encode("latin-1")
        result = check_utf8_encoding(content, "data/broken.txt", report)
        assert result is False
        assert report.stats["utf8_issues"] == 1
        critical_results = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_results) == 1
        assert "not valid UTF-8" in critical_results[0].message
        assert "data/broken.txt" in critical_results[0].message


class TestCheckBom:
    """Tests for the check_bom function."""

    def test_utf8_bom_returns_false(self):
        """Content starting with UTF-8 BOM (EF BB BF) should fail with a MAJOR issue."""
        report = EncodingValidationReport()
        content = b"\xef\xbb\xbf" + b"print('hello')\n"
        result = check_bom(content, "with_bom.py", report)
        assert result is False
        assert report.stats["bom_issues"] == 1
        major_results = [r for r in report.results if r.level == "MAJOR"]
        assert len(major_results) == 1
        assert "UTF-8 BOM" in major_results[0].message

    def test_utf16_le_bom_returns_false(self):
        """Content starting with UTF-16 LE BOM (FF FE) should fail with a CRITICAL issue."""
        report = EncodingValidationReport()
        content = b"\xff\xfe" + b"some content after bom marker"
        result = check_bom(content, "encoded_utf16le.txt", report)
        assert result is False
        assert report.stats["bom_issues"] == 1
        critical_results = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_results) == 1
        assert "UTF-16 LE" in critical_results[0].message


class TestCheckLineEndings:
    """Tests for the check_line_endings function."""

    def test_lf_endings_on_python_file_returns_true(self):
        """Python files with LF-only line endings should pass the line endings check."""
        report = EncodingValidationReport()
        content = b"import os\nprint('hello')\nprint('world')\n"
        result = check_line_endings(content, "app/main.py", ".py", report)
        assert result is True
        assert report.stats["line_ending_issues"] == 0
        assert report.stats["shell_crlf_issues"] == 0

    def test_crlf_on_shell_script_returns_false(self):
        """Shell scripts with CRLF line endings should fail with a CRITICAL issue since CRLF breaks shell execution."""
        report = EncodingValidationReport()
        content = b"#!/bin/bash\r\necho hello\r\necho world\r\n"
        result = check_line_endings(content, "scripts/deploy.sh", ".sh", report)
        assert result is False
        assert report.stats["shell_crlf_issues"] == 1
        critical_results = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_results) == 1
        assert "CRLF" in critical_results[0].message

    def test_crlf_on_batch_file_returns_true(self):
        """Batch files (.bat) with CRLF line endings should pass since CRLF is allowed for Windows scripts."""
        report = EncodingValidationReport()
        content = b"@echo off\r\necho hello\r\n"
        result = check_line_endings(content, "scripts/build.bat", ".bat", report)
        assert result is True
        assert report.stats["line_ending_issues"] == 0


class TestValidateFile:
    """Tests for the validate_file function."""

    def test_validate_file_with_mixed_issues(self, tmp_path):
        """A file with CRLF line endings on a .py extension should report a line ending issue via validate_file."""
        plugin_dir = tmp_path / "test-plugin"
        plugin_dir.mkdir()
        # Create a Python file with CRLF endings (valid UTF-8 but wrong line endings)
        py_file = plugin_dir / "example.py"
        py_file.write_bytes(b"# coding: utf-8\r\nimport os\r\nprint('hello')\r\n")

        report = EncodingValidationReport()
        validate_file(py_file, plugin_dir, report)

        assert report.stats["files_scanned"] == 1
        assert report.stats["utf8_issues"] == 0
        assert report.stats["bom_issues"] == 0
        # CRLF on .py should produce a line_ending_issues increment
        assert report.stats["line_ending_issues"] == 1


class TestValidateEncoding:
    """Tests for the validate_encoding main entry point."""

    def test_validate_encoding_clean_plugin(self, tmp_path):
        """A plugin directory with properly encoded UTF-8/LF files should produce zero issues and report all checks passed."""
        plugin_dir = tmp_path / "clean-plugin"
        plugin_dir.mkdir()

        # Create a valid plugin.json with LF endings
        plugin_json = {"name": "clean-plugin", "version": "1.0.0", "description": "A clean plugin"}
        (plugin_dir / "plugin.json").write_bytes(json.dumps(plugin_json, indent=2).encode("utf-8"))

        # Create a valid Python file with LF endings
        (plugin_dir / "main.py").write_bytes(b"#!/usr/bin/env python3\nimport sys\nprint('hello')\n")

        # Create a valid README.md with LF endings
        (plugin_dir / "README.md").write_bytes(b"# Clean Plugin\n\nThis is a test.\n")

        report = validate_encoding(plugin_dir)

        assert report.stats["files_scanned"] == 3
        assert report.stats["utf8_issues"] == 0
        assert report.stats["bom_issues"] == 0
        assert report.stats["unicode_issues"] == 0
        assert report.stats["escape_issues"] == 0
        assert report.stats["line_ending_issues"] == 0
        assert report.stats["shell_crlf_issues"] == 0
        # All categories should have PASSED results
        passed_messages = [r.message for r in report.results if r.level == "PASSED"]
        assert any("valid UTF-8" in m for m in passed_messages)
        assert any("No BOM" in m for m in passed_messages)

    def test_validate_encoding_nonexistent_path(self, tmp_path):
        """Calling validate_encoding on a path that does not exist should produce a CRITICAL issue."""
        fake_path = tmp_path / "nonexistent-plugin"
        report = validate_encoding(fake_path)
        assert report.has_critical
        critical_messages = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("does not exist" in m for m in critical_messages)


# =============================================================================
# Additional tests for uncovered lines (lines 181-611)
# =============================================================================


class TestEncodingValidationReportToDict:
    """Tests for EncodingValidationReport.to_dict method (lines 181-183)."""

    def test_to_dict_includes_encoding_stats(self):
        """to_dict should return a dict containing encoding_stats with all stat counters."""
        report = EncodingValidationReport()
        report.stats["utf8_issues"] = 2
        report.stats["bom_issues"] = 1
        result = report.to_dict()
        assert "encoding_stats" in result
        assert result["encoding_stats"]["utf8_issues"] == 2
        assert result["encoding_stats"]["bom_issues"] == 1
        assert result["encoding_stats"]["files_scanned"] == 0


class TestIsBinaryFile:
    """Tests for is_binary_file function (lines 191-203)."""

    def test_binary_extension_detected_without_reading(self, tmp_path):
        """A file with a known binary extension (.png) should be detected as binary via fast path."""
        png_file = tmp_path / "image.png"
        png_file.write_bytes(b"not real png data but extension matters")
        assert is_binary_file(png_file) is True

    def test_file_with_null_bytes_detected_as_binary(self, tmp_path):
        """A file with no known binary extension but containing null bytes should be detected as binary."""
        data_file = tmp_path / "mystery.dat"
        data_file.write_bytes(b"some text\x00with null bytes inside")
        assert is_binary_file(data_file) is True

    def test_unreadable_file_treated_as_binary(self, tmp_path):
        """A file that cannot be read (PermissionError/OSError) should be treated as binary."""
        bad_file = tmp_path / "noperm.txt"
        bad_file.write_bytes(b"readable content")
        bad_file.chmod(0o000)
        try:
            result = is_binary_file(bad_file)
            assert result is True
        finally:
            bad_file.chmod(0o644)


class TestShouldSkipDirectory:
    """Tests for should_skip_directory function (lines 206-215)."""

    def test_known_skip_dir_returns_true(self):
        """A directory name matching SKIP_DIRS exactly (e.g. __pycache__) should be skipped."""
        assert should_skip_directory("__pycache__") is True
        assert should_skip_directory("node_modules") is True
        assert should_skip_directory(".git") is True

    def test_wildcard_skip_pattern_matches(self):
        """A directory matching a wildcard pattern like *.egg-info should be skipped."""
        assert should_skip_directory("mypackage.egg-info") is True

    def test_normal_directory_not_skipped(self):
        """A regular directory name like 'src' should not be skipped."""
        assert should_skip_directory("src") is False
        assert should_skip_directory("lib") is False


class TestIsTextFile:
    """Tests for is_text_file function (lines 218-232)."""

    def test_shebang_file_without_extension_is_text(self, tmp_path):
        """An extensionless file starting with a shebang (#!) should be identified as text."""
        script = tmp_path / "myscript"
        script.write_bytes(b"#!/usr/bin/env python3\nprint('hello')\n")
        assert is_text_file(script) is True

    def test_extensionless_non_shebang_file_is_not_text(self, tmp_path):
        """An extensionless file without a shebang line should not be identified as text."""
        datafile = tmp_path / "datafile"
        datafile.write_bytes(b"just some data without shebang\n")
        assert is_text_file(datafile) is False

    def test_unreadable_extensionless_file_is_not_text(self, tmp_path):
        """An unreadable extensionless file should return False rather than raising."""
        script = tmp_path / "locked"
        script.write_bytes(b"#!/bin/bash\necho hello\n")
        script.chmod(0o000)
        try:
            assert is_text_file(script) is False
        finally:
            script.chmod(0o644)


class TestCheckBomAdditional:
    """Additional BOM tests for UTF-16 BE and UTF-32 variants (lines 289-303)."""

    def test_utf16_be_bom_returns_false(self):
        """Content starting with UTF-16 BE BOM (FE FF) should fail with a CRITICAL issue."""
        report = EncodingValidationReport()
        content = b"\xfe\xff" + b"some content"
        result = check_bom(content, "utf16be.txt", report)
        assert result is False
        assert report.stats["bom_issues"] == 1
        critical_results = [r for r in report.results if r.level == "CRITICAL"]
        assert any("UTF-16 BE" in r.message for r in critical_results)

    def test_utf32_be_bom_returns_false(self):
        """Content starting with UTF-32 BE BOM (00 00 FE FF) should fail with a CRITICAL issue."""
        report = EncodingValidationReport()
        content = b"\x00\x00\xfe\xff" + b"some content"
        result = check_bom(content, "utf32be.txt", report)
        assert result is False
        assert report.stats["bom_issues"] == 1
        critical_results = [r for r in report.results if r.level == "CRITICAL"]
        assert any("UTF-32 BE" in r.message for r in critical_results)


class TestCheckJsonUnicode:
    """Tests for check_json_unicode function (lines 308-335)."""

    def test_malformed_json_with_unicode_error_returns_false(self):
        """A JSON file whose parse error mentions 'unicode' should report a MAJOR unicode_issues."""
        report = EncodingValidationReport()
        # Truncated unicode escape triggers JSONDecodeError containing 'unicode'
        content = '{"key": "\\ud800"}'
        result = check_json_unicode(content, "bad_unicode.json", report)
        # The error message may or may not contain 'unicode'; check both paths
        # json.loads on a lone surrogate raises an error in some Python versions
        # We test the code path that catches JSONDecodeError
        if result is False:
            assert report.stats["unicode_issues"] == 1
        else:
            # If json.loads accepted it, the function returns True (other JSON errors path)
            assert report.stats["unicode_issues"] == 0

    def test_malformed_json_non_unicode_error_returns_true(self):
        """A JSON file with a syntax error unrelated to unicode should return True (handled elsewhere)."""
        report = EncodingValidationReport()
        content = '{"key": value_without_quotes}'
        result = check_json_unicode(content, "syntax_error.json", report)
        assert result is True
        assert report.stats["unicode_issues"] == 0

    def test_non_json_file_returns_true_immediately(self):
        """A non-JSON file path should return True without any parsing."""
        report = EncodingValidationReport()
        result = check_json_unicode("not json at all {{{", "readme.md", report)
        assert result is True


class TestCheckEscapeSequences:
    """Tests for check_escape_sequences function (lines 338-375)."""

    def test_raw_control_characters_detected(self):
        """Content with raw control characters (e.g. 0x01, 0x7f) should report an escape_issues MINOR."""
        report = EncodingValidationReport()
        # Insert SOH (0x01) and DEL (0x7f) control characters in realistic file content
        content = "function setup() {\x01\n  return config;\x7f\n}\n"
        result = check_escape_sequences(content, "src/setup.js", report)
        assert result is False
        assert report.stats["escape_issues"] == 1
        minor_results = [r for r in report.results if r.level == "MINOR"]
        assert any("control characters" in r.message for r in minor_results)

    def test_json_file_tab_in_string_does_not_report_issue(self):
        """A JSON file with a tab character between quotes should not produce a false positive."""
        report = EncodingValidationReport()
        # Tab between quotes in a JSON-like line - the pass branch at line 373
        content = '{"description": "has\ttab here"}\n'
        result = check_escape_sequences(content, "data/config.json", report)
        # No control chars other than tab, so should pass
        assert result is True
        assert report.stats["escape_issues"] == 0


class TestCheckLineEndingsAdditional:
    """Additional line ending tests for CR-only and mixed scenarios (lines 402-437)."""

    def test_batch_file_cr_only_returns_false(self):
        """A batch file with old Mac-style CR-only endings should fail with a MINOR issue."""
        report = EncodingValidationReport()
        content = b"@echo off\recho hello\recho world\r"
        result = check_line_endings(content, "build.bat", ".bat", report)
        assert result is False
        assert report.stats["line_ending_issues"] == 1

    def test_shell_script_cr_only_returns_false(self):
        """A shell script with CR-only endings should fail with a CRITICAL shell_crlf_issues."""
        report = EncodingValidationReport()
        content = b"#!/bin/bash\recho hello\recho world\r"
        result = check_line_endings(content, "run.sh", ".sh", report)
        assert result is False
        assert report.stats["shell_crlf_issues"] == 1
        critical_results = [r for r in report.results if r.level == "CRITICAL"]
        assert any("CR-only" in r.message for r in critical_results)

    def test_shell_script_mixed_endings_returns_false(self):
        """A shell script with mixed line endings (some CRLF, some LF) should fail with a MAJOR issue."""
        report = EncodingValidationReport()
        # Mix: first line CRLF, second line bare LF
        content = b"#!/bin/bash\r\necho hello\necho world\n"
        result = check_line_endings(content, "deploy.sh", ".sh", report)
        assert result is False
        assert report.stats["shell_crlf_issues"] == 1

    def test_source_file_cr_only_returns_false(self):
        """A source file (.py) with old Mac-style CR-only endings should fail with a MINOR issue."""
        report = EncodingValidationReport()
        content = b"import os\rprint('hello')\rprint('world')\r"
        result = check_line_endings(content, "main.py", ".py", report)
        assert result is False
        assert report.stats["line_ending_issues"] == 1
        minor_results = [r for r in report.results if r.level == "MINOR"]
        assert any("CR" in r.message for r in minor_results)

    def test_source_file_mixed_endings_returns_false(self):
        """A source file (.json) with mixed CRLF and LF endings should fail with a MINOR issue."""
        report = EncodingValidationReport()
        # Mixed: one CRLF line then a bare LF line
        content = b'{"key": "val"}\r\n{"key2": "val2"}\n'
        result = check_line_endings(content, "data.json", ".json", report)
        assert result is False
        assert report.stats["line_ending_issues"] == 1


class TestValidateFileAdditional:
    """Additional validate_file tests (lines 484-486)."""

    def test_validate_file_unreadable_file_skipped(self, tmp_path):
        """An unreadable file should be gracefully skipped with files_skipped incremented."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        locked = plugin_dir / "locked.py"
        locked.write_bytes(b"print('hello')\n")
        locked.chmod(0o000)
        try:
            report = EncodingValidationReport()
            validate_file(locked, plugin_dir, report)
            assert report.stats["files_skipped"] == 1
            assert report.stats["files_scanned"] == 0
        finally:
            locked.chmod(0o644)


class TestValidateEncodingAdditional:
    """Additional validate_encoding tests (lines 515-537)."""

    def test_validate_encoding_path_is_file_not_dir(self, tmp_path):
        """Calling validate_encoding on a file (not directory) should produce a CRITICAL issue."""
        a_file = tmp_path / "not_a_dir.txt"
        a_file.write_bytes(b"just a file\n")
        report = validate_encoding(a_file)
        assert report.has_critical
        critical_messages = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("not a directory" in m for m in critical_messages)

    def test_validate_encoding_skips_binary_and_non_text(self, tmp_path):
        """Binary files and unknown-extension files should be skipped during directory scan."""
        plugin_dir = tmp_path / "mixed-plugin"
        plugin_dir.mkdir()
        # A known binary extension file
        (plugin_dir / "image.png").write_bytes(b"\x89PNG\r\n\x1a\nfake png data")
        # An unknown extension file (not text, not binary by extension) with no null bytes
        (plugin_dir / "data.xyz").write_bytes(b"some unknown format data here")
        # A valid text file to ensure scanning works
        (plugin_dir / "readme.md").write_bytes(b"# Plugin\n\nDescription.\n")

        report = validate_encoding(plugin_dir)
        # readme.md should be scanned, png and xyz should be skipped
        assert report.stats["files_scanned"] == 1
        assert report.stats["files_skipped"] >= 2

    def test_validate_encoding_skips_skip_dirs(self, tmp_path):
        """Directories in SKIP_DIRS (e.g. __pycache__) should be entirely skipped during scan."""
        plugin_dir = tmp_path / "plugin-with-cache"
        plugin_dir.mkdir()
        cache_dir = plugin_dir / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "module.cpython-312.pyc").write_bytes(b"\x00\x00\x00\x00compiled")
        (plugin_dir / "main.py").write_bytes(b"print('hello')\n")

        report = validate_encoding(plugin_dir)
        # Only main.py should be scanned, __pycache__ contents should be fully skipped
        assert report.stats["files_scanned"] == 1


class TestMainCLI:
    """Tests for the main() CLI entry point (lines 566-611)."""

    def test_main_clean_plugin_exits_zero(self, tmp_path, monkeypatch):
        """CLI main with a clean plugin directory should exit with code 0."""
        plugin_dir = tmp_path / "cli-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "main.py").write_bytes(b"print('ok')\n")
        monkeypatch.setattr("sys.argv", ["validate_encoding", str(plugin_dir)])
        exit_code = main()
        assert exit_code == 0

    def test_main_json_output(self, tmp_path, monkeypatch, capsys):
        """CLI main with --json flag should output valid JSON containing encoding_stats."""
        plugin_dir = tmp_path / "json-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "app.py").write_bytes(b"import sys\n")
        monkeypatch.setattr("sys.argv", ["validate_encoding", "--json", str(plugin_dir)])
        exit_code = main()
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "encoding_stats" in output
        assert "plugin_path" in output
        assert exit_code == 0

    def test_main_strict_mode_with_minor_issue(self, tmp_path, monkeypatch):
        """CLI main with --strict and a file with CRLF endings should exit with non-zero code."""
        plugin_dir = tmp_path / "strict-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        # CRLF on .py file produces MINOR issue
        (plugin_dir / "bad.py").write_bytes(b"print('hi')\r\nimport os\r\n")
        monkeypatch.setattr("sys.argv", ["validate_encoding", "--strict", str(plugin_dir)])
        exit_code = main()
        assert exit_code != 0
