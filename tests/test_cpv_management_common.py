#!/usr/bin/env python3
"""Tests for cpv_management_common.py.

Coverage: targets all public functions in the management common module:
- strip_jsonc_comments() — JSONC comment stripping (// and /* */)
- strip_trailing_commas() — trailing comma removal before } or ]
- load_jsonc() — full JSONC file loading
- backup_file() — timestamped backup with retention policy
- load_json_safe() — safe JSON/JSONC loading with fallback
- save_json_safe() — atomic JSON write with backup and dry_run
- extract_archive() — archive dispatch (zip/tar)
- _extract_zip() — zip extraction with path traversal prevention
- _extract_tar() — tar extraction with security filtering
- _validate_safe_name() — path safety validation
- supports_color() — terminal color detection
- ok/info/warn/err — colored output helpers
"""

from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_management_common import (  # noqa: E402
    _extract_tar,
    _extract_zip,
    _validate_safe_name,
    backup_file,
    err,
    extract_archive,
    load_json_safe,
    load_jsonc,
    ok,
    save_json_safe,
    strip_jsonc_comments,
    strip_trailing_commas,
    supports_color,
)

# ── strip_jsonc_comments tests ──────────────────────────────


class TestStripJsoncComments:
    """Tests for strip_jsonc_comments() — strips // and /* */ from JSONC text."""

    def test_single_line_comment_removed(self):
        """Single-line // comments are fully removed from the output."""
        text = '{"key": "value"} // this is a comment'
        result = strip_jsonc_comments(text)
        assert result == '{"key": "value"} '
        # Verify the result is valid JSON
        assert json.loads(result) == {"key": "value"}

    def test_block_comment_removed(self):
        """Block /* */ comments are fully removed from the output."""
        text = '{"key": /* inline comment */ "value"}'
        result = strip_jsonc_comments(text)
        assert result == '{"key":  "value"}'
        assert json.loads(result) == {"key": "value"}

    def test_multiline_block_comment_removed(self):
        """Multi-line block comments spanning several lines are removed."""
        text = '{\n/* this\nis\na\ncomment */\n"key": "value"\n}'
        result = strip_jsonc_comments(text)
        assert json.loads(result) == {"key": "value"}

    def test_comment_inside_string_preserved(self):
        """// inside a JSON string value must NOT be stripped."""
        text = '{"url": "https://example.com"}'
        result = strip_jsonc_comments(text)
        assert result == text
        assert json.loads(result)["url"] == "https://example.com"

    def test_block_comment_markers_inside_string_preserved(self):
        """/* */ inside a JSON string value must NOT be stripped."""
        text = '{"note": "use /* markers */ carefully"}'
        result = strip_jsonc_comments(text)
        assert result == text
        assert "/* markers */" in json.loads(result)["note"]

    def test_escaped_quote_inside_string_not_ending_string(self):
        r"""Escaped \" inside a string must not end the string context."""
        text = r'{"msg": "say \"hello\" // not a comment"}'
        result = strip_jsonc_comments(text)
        # The // is inside the string, so must be preserved
        assert "// not a comment" in result

    def test_multiple_comments_removed(self):
        """Multiple comments of different types are all stripped."""
        text = '{\n  // line comment\n  "a": 1, /* block */\n  "b": 2 // another\n}'
        result = strip_jsonc_comments(text)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_unterminated_block_comment_handled(self):
        """Unterminated /* comment consumes to end of input without crashing."""
        text = '{"key": "value"} /* unterminated'
        result = strip_jsonc_comments(text)
        # Everything after /* is consumed
        assert "unterminated" not in result
        assert '{"key": "value"} ' == result


# ── strip_trailing_commas tests ─────────────────────────────


class TestStripTrailingCommas:
    """Tests for strip_trailing_commas() — removes trailing commas before } or ]."""

    def test_trailing_comma_before_closing_brace(self):
        """Trailing comma before } is removed."""
        text = '{"a": 1, "b": 2,}'
        result = strip_trailing_commas(text)
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_trailing_comma_before_closing_bracket(self):
        """Trailing comma before ] is removed."""
        text = '{"items": [1, 2, 3,]}'
        result = strip_trailing_commas(text)
        assert json.loads(result) == {"items": [1, 2, 3]}

    def test_trailing_comma_with_whitespace(self):
        """Trailing comma followed by whitespace then } is removed."""
        text = '{\n  "a": 1,\n  "b": 2,\n}'
        result = strip_trailing_commas(text)
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_comma_inside_string_preserved(self):
        """Commas inside JSON string values must NOT be removed."""
        text = '{"text": "hello, world,}"}'
        result = strip_trailing_commas(text)
        assert json.loads(result)["text"] == "hello, world,}"

    def test_nested_trailing_commas(self):
        """Trailing commas in nested structures are all removed."""
        text = '{"outer": {"inner": [1, 2,], "x": 3,},}'
        result = strip_trailing_commas(text)
        parsed = json.loads(result)
        assert parsed == {"outer": {"inner": [1, 2], "x": 3}}

    def test_escaped_quote_in_string_with_trailing_comma(self):
        r"""Escaped \" in string near trailing comma does not break parsing."""
        text = '{"msg": "say \\"hi\\",", "b": 2,}'
        result = strip_trailing_commas(text)
        parsed = json.loads(result)
        assert parsed["b"] == 2
        assert '"hi",' in parsed["msg"]


# ── load_jsonc tests ────────────────────────────────────────


class TestLoadJsonc:
    """Tests for load_jsonc() — loads JSONC files with comments and trailing commas."""

    def test_load_jsonc_with_comments_and_trailing_commas(self, tmp_path):
        """JSONC file with both comments and trailing commas loads correctly."""
        content = (
            "{\n"
            "  // This is a config file\n"
            '  "name": "test-plugin",\n'
            '  "version": "1.0.0",\n'
            "  /* multi-line\n"
            "     comment */\n"
            '  "enabled": true,\n'
            "}\n"
        )
        f = tmp_path / "config.jsonc"
        f.write_text(content, encoding="utf-8")
        result = load_jsonc(f)
        assert result == {"name": "test-plugin", "version": "1.0.0", "enabled": True}

    def test_load_jsonc_pure_json(self, tmp_path):
        """Standard JSON without comments loads correctly."""
        data = {"key": "value", "number": 42}
        f = tmp_path / "data.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        assert load_jsonc(f) == data

    def test_load_jsonc_invalid_json_raises(self, tmp_path):
        """JSONC file with invalid JSON (after comment stripping) raises JSONDecodeError."""
        f = tmp_path / "bad.json"
        f.write_text("{ not valid json at all", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_jsonc(f)


# ── backup_file tests ──────────────────────────────────────


class TestBackupFile:
    """Tests for backup_file() — creates timestamped backups with 5-file retention."""

    def test_backup_creates_file_in_backups_dir(self, tmp_path):
        """Backup creates a .bak file in a backups/ subdirectory."""
        # Patch CLAUDE_DIR so backups go into our tmp_path
        fake_claude = tmp_path / "claude"
        fake_claude.mkdir()
        source = fake_claude / "settings.json"
        source.write_text('{"original": true}', encoding="utf-8")

        with patch("cpv_management_common.CLAUDE_DIR", fake_claude):
            backup_file(source)

        backup_dir = fake_claude / "backups"
        assert backup_dir.exists()
        backups = list(backup_dir.glob("settings.json.*.bak"))
        assert len(backups) == 1
        # Verify backup content matches original
        assert backups[0].read_text(encoding="utf-8") == '{"original": true}'

    def test_backup_nonexistent_file_does_nothing(self, tmp_path):
        """Calling backup_file on a non-existent path does nothing."""
        fake_claude = tmp_path / "claude"
        fake_claude.mkdir()
        nonexistent = fake_claude / "missing.json"

        with patch("cpv_management_common.CLAUDE_DIR", fake_claude):
            backup_file(nonexistent)

        backup_dir = fake_claude / "backups"
        # backup_dir may not even be created
        if backup_dir.exists():
            assert list(backup_dir.glob("*.bak")) == []

    def test_backup_retention_keeps_only_5(self, tmp_path):
        """Only the 5 most recent backups are kept; older ones are deleted."""
        fake_claude = tmp_path / "claude"
        fake_claude.mkdir()
        backup_dir = fake_claude / "backups"
        backup_dir.mkdir()

        source = fake_claude / "test.json"
        source.write_text("{}", encoding="utf-8")

        # Pre-create 6 old backups with distinct mtimes
        for i in range(6):
            old = backup_dir / f"test.json.20230101_00000{i}.bak"
            old.write_text(f"backup {i}", encoding="utf-8")
            # Ensure distinct modification times
            os.utime(old, (1000000 + i, 1000000 + i))

        with patch("cpv_management_common.CLAUDE_DIR", fake_claude):
            backup_file(source)

        # After backup_file, there should be at most 5 backups
        remaining = list(backup_dir.glob("test.json.*.bak"))
        assert len(remaining) == 5

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod not meaningful on Windows")
    def test_backup_permissions_owner_only(self, tmp_path):
        """Backup file permissions are set to 0o600 (owner-only) on Unix."""
        fake_claude = tmp_path / "claude"
        fake_claude.mkdir()
        source = fake_claude / "secret.json"
        source.write_text('{"secret": true}', encoding="utf-8")

        with patch("cpv_management_common.CLAUDE_DIR", fake_claude):
            with patch("cpv_management_common.IS_WINDOWS", False):
                backup_file(source)

        backup_dir = fake_claude / "backups"
        backups = list(backup_dir.glob("secret.json.*.bak"))
        assert len(backups) == 1
        mode = oct(backups[0].stat().st_mode & 0o777)
        assert mode == oct(0o600)


# ── load_json_safe tests ───────────────────────────────────


class TestLoadJsonSafe:
    """Tests for load_json_safe() — safe JSON loading returning {} on failure."""

    def test_load_valid_json(self, tmp_path):
        """Valid JSON file is loaded correctly."""
        data = {"name": "test", "version": "1.0"}
        f = tmp_path / "valid.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        assert load_json_safe(f) == data

    def test_load_missing_file_returns_empty_dict(self, tmp_path):
        """Missing file returns empty dict without raising."""
        f = tmp_path / "nonexistent.json"
        assert load_json_safe(f) == {}

    def test_load_corrupt_json_returns_empty_dict(self, tmp_path):
        """Corrupt/invalid JSON returns empty dict and warns."""
        f = tmp_path / "corrupt.json"
        f.write_text("{{{{not json at all", encoding="utf-8")
        result = load_json_safe(f)
        assert result == {}

    def test_load_jsonc_content_via_safe(self, tmp_path):
        """JSONC content (with comments) is handled by load_json_safe."""
        content = '{\n  // comment\n  "key": "val",\n}'
        f = tmp_path / "config.jsonc"
        f.write_text(content, encoding="utf-8")
        result = load_json_safe(f)
        assert result == {"key": "val"}


# ── save_json_safe tests ───────────────────────────────────


class TestSaveJsonSafe:
    """Tests for save_json_safe() — atomic JSON write with backup."""

    def test_save_creates_file_with_correct_content(self, tmp_path):
        """Saved JSON file contains correctly formatted data."""
        f = tmp_path / "output.json"
        data = {"name": "test", "items": [1, 2, 3]}

        with patch("cpv_management_common.CLAUDE_DIR", tmp_path):
            save_json_safe(f, data)

        assert f.exists()
        loaded = json.loads(f.read_text(encoding="utf-8"))
        assert loaded == data

    def test_save_dry_run_does_not_write(self, tmp_path):
        """dry_run=True skips writing entirely."""
        f = tmp_path / "should_not_exist.json"
        save_json_safe(f, {"key": "value"}, dry_run=True)
        assert not f.exists()

    def test_save_creates_parent_directories(self, tmp_path):
        """Parent directories are created if they do not exist."""
        f = tmp_path / "deep" / "nested" / "dir" / "file.json"

        with patch("cpv_management_common.CLAUDE_DIR", tmp_path):
            save_json_safe(f, {"nested": True})

        assert f.exists()
        assert json.loads(f.read_text(encoding="utf-8")) == {"nested": True}

    def test_save_backs_up_existing_file(self, tmp_path):
        """Existing file is backed up before overwrite."""
        fake_claude = tmp_path / "claude"
        fake_claude.mkdir()

        f = fake_claude / "data.json"
        f.write_text('{"old": true}', encoding="utf-8")

        with patch("cpv_management_common.CLAUDE_DIR", fake_claude):
            save_json_safe(f, {"new": True})

        backup_dir = fake_claude / "backups"
        backups = list(backup_dir.glob("data.json.*.bak"))
        assert len(backups) >= 1
        # Backup should contain old content
        assert json.loads(backups[0].read_text(encoding="utf-8")) == {"old": True}
        # File should contain new content
        assert json.loads(f.read_text(encoding="utf-8")) == {"new": True}

    def test_save_tmp_file_cleaned_on_error(self, tmp_path):
        """Temporary file is cleaned up if writing fails."""
        f = tmp_path / "fail.json"

        with patch("cpv_management_common.CLAUDE_DIR", tmp_path):
            with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
                with pytest.raises(OSError, match="disk full"):
                    save_json_safe(f, {"data": True})

        # The .tmp file should not remain
        assert not (tmp_path / "fail.tmp").exists()


# ── _validate_safe_name tests ──────────────────────────────


class TestValidateSafeName:
    """Tests for _validate_safe_name() — rejects unsafe path names."""

    def test_valid_name_returned(self):
        """A safe alphanumeric name is returned unchanged."""
        assert _validate_safe_name("my-plugin", "plugin") == "my-plugin"

    def test_empty_name_exits(self):
        """Empty name causes sys.exit(1)."""
        with pytest.raises(SystemExit) as exc_info:
            _validate_safe_name("", "plugin")
        assert exc_info.value.code == 1

    def test_path_traversal_dots_exits(self):
        """Name containing '..' is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _validate_safe_name("../evil", "plugin")
        assert exc_info.value.code == 1

    def test_forward_slash_exits(self):
        """Name containing '/' is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _validate_safe_name("path/traversal", "plugin")
        assert exc_info.value.code == 1

    def test_backslash_exits(self):
        r"""Name containing '\\' is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _validate_safe_name("path\\traversal", "plugin")
        assert exc_info.value.code == 1

    def test_null_byte_exits(self):
        """Name containing null byte is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _validate_safe_name("evil\0name", "plugin")
        assert exc_info.value.code == 1

    def test_leading_dot_exits(self):
        """Name starting with '.' is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _validate_safe_name(".hidden", "marketplace")
        assert exc_info.value.code == 1

    def test_leading_dash_exits(self):
        """Name starting with '-' is rejected."""
        with pytest.raises(SystemExit) as exc_info:
            _validate_safe_name("-flag", "plugin")
        assert exc_info.value.code == 1


# ── extract_archive tests ──────────────────────────────────


class TestExtractArchive:
    """Tests for extract_archive() and helpers — archive extraction with security."""

    def test_extract_zip_normal(self, tmp_path):
        """Normal zip file extracts its contents correctly."""
        # Create a zip with a simple file
        zip_path = tmp_path / "test.zip"
        dest = tmp_path / "output"
        dest.mkdir()

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("hello.txt", "Hello, World!")
            zf.writestr("subdir/nested.txt", "Nested content")

        _extract_zip(zip_path, dest)

        assert (dest / "hello.txt").read_text() == "Hello, World!"
        assert (dest / "subdir" / "nested.txt").read_text() == "Nested content"

    def test_extract_zip_path_traversal_rejected(self, tmp_path):
        """Zip entry with ../ path traversal is rejected with sys.exit."""
        zip_path = tmp_path / "evil.zip"
        dest = tmp_path / "output"
        dest.mkdir()

        # Create a zip with a path-traversal entry
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../../etc/passwd", "evil content")

        with pytest.raises(SystemExit) as exc_info:
            _extract_zip(zip_path, dest)
        assert exc_info.value.code == 1

    def test_extract_zip_absolute_path_rejected(self, tmp_path):
        """Zip entry with absolute path is rejected with sys.exit."""
        zip_path = tmp_path / "abs.zip"
        dest = tmp_path / "output"
        dest.mkdir()

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("/etc/passwd", "absolute path attack")

        with pytest.raises(SystemExit) as exc_info:
            _extract_zip(zip_path, dest)
        assert exc_info.value.code == 1

    def test_extract_tar_gz_normal(self, tmp_path):
        """Normal tar.gz file extracts its contents correctly."""
        tar_path = tmp_path / "test.tar.gz"
        dest = tmp_path / "output"
        dest.mkdir()

        with tarfile.open(tar_path, "w:gz") as tf:
            data = b"Tar content here"
            info = tarfile.TarInfo(name="readme.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        _extract_tar(tar_path, dest, "r:gz")

        assert (dest / "readme.txt").read_bytes() == b"Tar content here"

    def test_extract_archive_dispatches_zip(self, tmp_path):
        """extract_archive() dispatches .zip to _extract_zip."""
        zip_path = tmp_path / "plugin.zip"
        dest = tmp_path / "output"
        dest.mkdir()

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("plugin.json", '{"name": "test"}')

        extract_archive(str(zip_path), dest)

        assert (dest / "plugin.json").exists()
        assert json.loads((dest / "plugin.json").read_text()) == {"name": "test"}

    def test_extract_archive_dispatches_tar_gz(self, tmp_path):
        """extract_archive() dispatches .tar.gz to _extract_tar."""
        tar_path = tmp_path / "plugin.tar.gz"
        dest = tmp_path / "output"
        dest.mkdir()

        with tarfile.open(tar_path, "w:gz") as tf:
            data = b'{"name": "test"}'
            info = tarfile.TarInfo(name="plugin.json")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

        extract_archive(str(tar_path), dest)

        assert (dest / "plugin.json").exists()

    def test_extract_archive_unsupported_format_exits(self, tmp_path):
        """Unsupported archive format (.rar) causes sys.exit."""
        fake = tmp_path / "archive.rar"
        fake.write_text("not real", encoding="utf-8")
        dest = tmp_path / "output"
        dest.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            extract_archive(str(fake), dest)
        assert exc_info.value.code == 1

    def test_extract_archive_missing_file_exits(self, tmp_path):
        """Non-existent archive file causes sys.exit."""
        dest = tmp_path / "output"
        dest.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            extract_archive(str(tmp_path / "missing.zip"), dest)
        assert exc_info.value.code == 1


# ── supports_color tests ───────────────────────────────────


class TestSupportsColor:
    """Tests for supports_color() — terminal color capability detection."""

    def test_supports_color_non_tty_returns_false(self):
        """Non-TTY stdout returns False for color support."""
        with patch("cpv_management_common.IS_WINDOWS", False):
            with patch("sys.stdout", new_callable=io.StringIO):
                assert supports_color() is False

    def test_supports_color_tty_returns_true(self):
        """TTY stdout returns True for color support."""
        mock_stdout = io.StringIO()
        mock_stdout.isatty = lambda: True  # type: ignore[attr-defined]
        with patch("cpv_management_common.IS_WINDOWS", False):
            with patch("sys.stdout", mock_stdout):
                assert supports_color() is True


# ── ok/info/warn/err output tests ──────────────────────────


class TestOutputHelpers:
    """Tests for ok(), info(), warn(), err() colored output functions."""

    def test_ok_prints_message(self, capsys):
        """ok() prints the message to stdout."""
        ok("Operation succeeded")
        captured = capsys.readouterr()
        assert "Operation succeeded" in captured.out

    def test_err_prints_message(self, capsys):
        """err() prints the message to stdout."""
        err("Something failed")
        captured = capsys.readouterr()
        assert "Something failed" in captured.out
