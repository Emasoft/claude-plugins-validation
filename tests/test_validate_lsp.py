#!/usr/bin/env python3
"""Tests for validate_lsp.py.

Tests the LSP server configuration validator covering:
- validate_env_var_syntax: env var brace matching and default value warnings
- validate_path_value: absolute path detection, env var delegation, file existence
- validate_lsp_server: field validation, transport, timeouts, unknown fields
- validate_lsp_config: JSON parsing, server key detection, top-level structure
- validate_plugin_lsp: multi-file discovery across standard config locations

Coverage: 10 tests, all executing real validation logic with no mocking.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_lsp import (  # noqa: E402
    validate_env_var_syntax,
    validate_lsp_config,
    validate_lsp_server,
    validate_path_value,
    validate_plugin_lsp,
)
from validation_common import ValidationReport  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _messages(report: ValidationReport) -> list[str]:
    """Return all result messages from a report."""
    return [r.message for r in report.results]


def _levels(report: ValidationReport) -> list[str]:
    """Return all result levels from a report."""
    return [r.level for r in report.results]


def _has_message_containing(report: ValidationReport, fragment: str) -> bool:
    """Check if any result message contains the given fragment."""
    return any(fragment in m for m in _messages(report))


# ---------------------------------------------------------------------------
# Tests for validate_env_var_syntax
# ---------------------------------------------------------------------------

class TestValidateEnvVarSyntax:
    """Tests for environment variable syntax validation."""

    def test_malformed_env_var_unclosed_brace(self):
        """Unclosed env var braces produce a MAJOR issue about malformed syntax."""
        report = ValidationReport()
        validate_env_var_syntax("${HOME", report, "test-field")
        assert report.has_major
        assert _has_message_containing(report, "Malformed env var syntax")
        # Also verify that a well-formed but default-less non-plugin var emits INFO
        report2 = ValidationReport()
        validate_env_var_syntax("${MY_CUSTOM_VAR}", report2, "test-field")
        assert any(r.level == "INFO" for r in report2.results)
        assert _has_message_containing(report2, "no default value")


# ---------------------------------------------------------------------------
# Tests for validate_path_value
# ---------------------------------------------------------------------------

class TestValidatePathValue:
    """Tests for path value validation in LSP configs."""

    def test_absolute_path_produces_major(self):
        """An absolute Unix path should produce a MAJOR portability warning."""
        report = ValidationReport()
        validate_path_value("/usr/bin/server", report, "command")
        assert report.has_major
        assert _has_message_containing(report, "Absolute path found")
        assert _has_message_containing(report, "CLAUDE_PLUGIN_ROOT")

    def test_relative_path_with_plugin_root_checks_existence(self, tmp_path):
        """A CLAUDE_PLUGIN_ROOT path pointing to a missing file should produce INFO."""
        report = ValidationReport()
        validate_path_value(
            "${CLAUDE_PLUGIN_ROOT}/bin/missing-server.sh",
            report,
            "command",
            plugin_root=tmp_path,
        )
        # The file does not exist so the validator should note it
        assert _has_message_containing(report, "may not exist")


# ---------------------------------------------------------------------------
# Tests for validate_lsp_server
# ---------------------------------------------------------------------------

class TestValidateLspServer:
    """Tests for single LSP server configuration validation."""

    def test_missing_command_field_is_critical(self):
        """A server config with no 'command' field should produce a CRITICAL issue."""
        report = ValidationReport()
        config = {"args": ["--stdio"]}
        validate_lsp_server("test-server", config, report)
        assert report.has_critical
        assert _has_message_containing(report, "missing required 'command' field")

    def test_invalid_transport_type_produces_major(self):
        """A transport value other than 'stdio' or 'socket' should produce a MAJOR issue."""
        report = ValidationReport()
        config = {
            "command": "pyright-langserver",
            "args": ["--stdio"],
            "transport": "pipe",
            "extensionToLanguage": {".py": "python"},
        }
        validate_lsp_server("pyright", config, report)
        assert report.has_major
        assert _has_message_containing(report, "must be 'stdio' or 'socket'")

    def test_unknown_field_produces_warning(self):
        """An unrecognized config field should produce a WARNING."""
        report = ValidationReport()
        config = {
            "command": "gopls",
            "bogusField": True,
            "extensionToLanguage": {".go": "go"},
        }
        validate_lsp_server("gopls", config, report)
        assert any(r.level == "WARNING" for r in report.results)
        assert _has_message_containing(report, "Unknown field 'bogusField'")

    def test_valid_server_config_passes(self):
        """A fully valid server config should collect PASSED results and no issues."""
        report = ValidationReport()
        config = {
            "command": "pyright-langserver",
            "args": ["--stdio"],
            "filetypes": ["python"],
            "rootPatterns": ["pyproject.toml", "setup.py"],
            "transport": "stdio",
            "startupTimeout": 5000,
            "extensionToLanguage": {".py": "python"},
        }
        validate_lsp_server("pyright", config, report)
        # No critical/major/minor issues
        assert not report.has_critical
        assert not report.has_major
        # Should have at least one PASSED result
        assert any(r.level == "PASSED" for r in report.results)


# ---------------------------------------------------------------------------
# Tests for validate_lsp_config
# ---------------------------------------------------------------------------

class TestValidateLspConfig:
    """Tests for LSP configuration file validation."""

    def test_invalid_json_produces_critical(self, tmp_path):
        """A config file with broken JSON should produce a CRITICAL parse error."""
        config_file = tmp_path / "lsp-config.json"
        config_file.write_text("{not valid json!!")
        report = validate_lsp_config(config_file, plugin_root=tmp_path)
        assert report.has_critical
        assert _has_message_containing(report, "Invalid JSON")

    def test_valid_config_with_servers_validates_all(self, tmp_path):
        """A valid config with languageServers should validate each server entry."""
        config_data = {
            "languageServers": {
                "gopls": {
                    "command": "gopls",
                    "args": ["-remote=auto"],
                    "filetypes": ["go"],
                    "rootPatterns": ["go.mod"],
                    "transport": "stdio",
                    "extensionToLanguage": {".go": "go"},
                },
            },
        }
        config_file = tmp_path / "lsp-config.json"
        config_file.write_text(json.dumps(config_data))
        report = validate_lsp_config(config_file, plugin_root=tmp_path)
        assert not report.has_critical
        assert not report.has_major
        assert _has_message_containing(report, "valid JSON")
        assert _has_message_containing(report, "1 LSP server")


# ---------------------------------------------------------------------------
# Tests for validate_plugin_lsp
# ---------------------------------------------------------------------------

class TestValidatePluginLsp:
    """Tests for plugin-level LSP validation (multi-file discovery)."""

    def test_no_lsp_configs_reports_info(self, tmp_path):
        """A plugin directory with no LSP config files should report INFO only."""
        report = validate_plugin_lsp(tmp_path)
        assert not report.has_critical
        assert not report.has_major
        assert _has_message_containing(report, "No LSP configuration files found")


# ---------------------------------------------------------------------------
# Additional tests for uncovered lines (appended)
# ---------------------------------------------------------------------------

from validate_lsp import is_absolute_path, main, print_results  # noqa: E402


class TestIsAbsolutePath:
    """Tests for the is_absolute_path helper function."""

    def test_windows_drive_letter_path_detected(self):
        """A Windows drive letter path like C:\\ should be detected as absolute (covers line 85)."""
        assert is_absolute_path("C:\\Users\\dev\\server.exe") is True

    def test_relative_path_not_detected(self):
        """A plain relative path should not be detected as absolute."""
        assert is_absolute_path("bin/server") is False

    def test_env_var_path_not_absolute(self):
        """A path starting with ${...} should not be treated as absolute even though it has /."""
        assert is_absolute_path("${CLAUDE_PLUGIN_ROOT}/bin/server") is False


class TestValidateLspServerExtended:
    """Extended tests for validate_lsp_server covering uncovered branches."""

    def test_command_not_a_string_produces_critical(self):
        """A non-string command value should produce a CRITICAL issue (covers line 148)."""
        report = ValidationReport()
        config = {"command": 42, "extensionToLanguage": {".py": "python"}}
        validate_lsp_server("bad-cmd", config, report)
        assert report.has_critical
        assert _has_message_containing(report, "'command' must be a string")

    def test_command_with_plugin_root_resolved_executable(self, tmp_path):
        """A CLAUDE_PLUGIN_ROOT command pointing to an executable file passes (covers lines 157-162)."""
        server_bin = tmp_path / "bin" / "my-lsp"
        server_bin.parent.mkdir(parents=True)
        server_bin.write_text("#!/bin/sh\necho ok")
        server_bin.chmod(0o755)
        report = ValidationReport()
        config = {
            "command": "${CLAUDE_PLUGIN_ROOT}/bin/my-lsp",
            "extensionToLanguage": {".txt": "text"},
        }
        validate_lsp_server("my-lsp", config, report, plugin_root=tmp_path)
        assert _has_message_containing(report, "command is executable")

    def test_command_with_plugin_root_not_executable(self, tmp_path):
        """A CLAUDE_PLUGIN_ROOT command pointing to a non-executable file produces MAJOR (covers lines 159-160)."""
        server_bin = tmp_path / "bin" / "my-lsp"
        server_bin.parent.mkdir(parents=True)
        server_bin.write_text("not executable")
        server_bin.chmod(0o644)
        report = ValidationReport()
        config = {
            "command": "${CLAUDE_PLUGIN_ROOT}/bin/my-lsp",
            "extensionToLanguage": {".txt": "text"},
        }
        validate_lsp_server("my-lsp", config, report, plugin_root=tmp_path)
        assert report.has_major
        assert _has_message_containing(report, "command not executable")

    def test_command_known_runtime_passes(self, monkeypatch):
        """A command using a known runtime (npx/node/python/python3) passes (covers line 166)."""
        import shutil as _shutil
        # Ensure shutil.which returns None so the runtime fallback branch is reached
        monkeypatch.setattr(_shutil, "which", lambda cmd: None)
        report = ValidationReport()
        config = {
            "command": "python3",
            "args": ["-m", "pylsp"],
            "extensionToLanguage": {".py": "python"},
        }
        validate_lsp_server("pylsp-via-runtime", config, report)
        assert _has_message_containing(report, "uses runtime: python3")

    def test_extension_to_language_not_dict_produces_critical(self):
        """A non-dict extensionToLanguage should produce CRITICAL (covers line 181)."""
        report = ValidationReport()
        config = {"command": "gopls", "extensionToLanguage": "wrong-type"}
        validate_lsp_server("gopls", config, report)
        assert report.has_critical
        assert _has_message_containing(report, "'extensionToLanguage' must be an object")

    def test_extension_without_dot_and_non_string_language(self):
        """Extension missing dot produces MINOR; non-string language produces MAJOR (covers lines 187, 191)."""
        report = ValidationReport()
        config = {
            "command": "gopls",
            "extensionToLanguage": {"go": 42},
        }
        validate_lsp_server("gopls", config, report)
        assert _has_message_containing(report, "should start with '.'")
        assert _has_message_containing(report, "language for 'go' must be a string")
        assert report.has_major

    def test_args_not_a_list_produces_major(self):
        """A non-list args value should produce a MAJOR issue (covers line 201)."""
        report = ValidationReport()
        config = {"command": "gopls", "args": "--stdio", "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config, report)
        assert report.has_major
        assert _has_message_containing(report, "'args' must be an array")

    def test_args_element_not_string_produces_major(self):
        """A non-string element in args should produce a MAJOR issue (covers line 205)."""
        report = ValidationReport()
        config = {"command": "gopls", "args": [123], "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config, report)
        assert report.has_major
        assert _has_message_containing(report, "args[0] must be a string")

    def test_filetypes_validation_branches(self):
        """filetypes not list, empty list, and non-string element each produce the right issue (covers lines 213, 216, 219)."""
        # filetypes not a list -> MAJOR
        report1 = ValidationReport()
        config1 = {"command": "gopls", "filetypes": "go", "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config1, report1)
        assert report1.has_major
        assert _has_message_containing(report1, "'filetypes' must be an array")

        # empty filetypes -> MINOR
        report2 = ValidationReport()
        config2 = {"command": "gopls", "filetypes": [], "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config2, report2)
        assert _has_message_containing(report2, "empty filetypes array")

        # non-string filetype -> MAJOR
        report3 = ValidationReport()
        config3 = {"command": "gopls", "filetypes": [42], "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config3, report3)
        assert report3.has_major
        assert _has_message_containing(report3, "filetype must be a string")

    def test_root_patterns_validation_branches(self):
        """rootPatterns not list and non-string pattern each produce MAJOR (covers lines 225, 229)."""
        # not a list
        report1 = ValidationReport()
        config1 = {"command": "gopls", "rootPatterns": "go.mod", "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config1, report1)
        assert report1.has_major
        assert _has_message_containing(report1, "'rootPatterns' must be an array")

        # non-string pattern
        report2 = ValidationReport()
        config2 = {"command": "gopls", "rootPatterns": [123], "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config2, report2)
        assert report2.has_major
        assert _has_message_containing(report2, "rootPattern must be a string")

    def test_init_options_and_settings_not_dict(self):
        """Non-dict initializationOptions and settings each produce MAJOR (covers lines 233-235, 239-241)."""
        report = ValidationReport()
        config = {
            "command": "gopls",
            "initializationOptions": ["wrong"],
            "settings": "also-wrong",
            "extensionToLanguage": {".go": "go"},
        }
        validate_lsp_server("gopls", config, report)
        assert report.has_major
        assert _has_message_containing(report, "'initializationOptions' must be an object")
        assert _has_message_containing(report, "'settings' must be an object")

    def test_env_validation_branches(self):
        """env not dict, non-string value, and string value with env var all covered (covers lines 245-253)."""
        # env not a dict
        report1 = ValidationReport()
        config1 = {"command": "gopls", "env": "bad", "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config1, report1)
        assert report1.has_major
        assert _has_message_containing(report1, "'env' must be an object")

        # env value not a string
        report2 = ValidationReport()
        config2 = {"command": "gopls", "env": {"PATH": 123}, "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config2, report2)
        assert report2.has_major
        assert _has_message_containing(report2, "env[PATH] must be a string")

        # env value with valid env var syntax - exercises validate_env_var_syntax delegation
        report3 = ValidationReport()
        config3 = {"command": "gopls", "env": {"GOPATH": "${HOME}/go"}, "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config3, report3)
        # HOME has no default so INFO is emitted
        assert _has_message_containing(report3, "no default value")

    def test_cwd_validation_branches(self):
        """cwd not a string produces MAJOR; cwd string delegates to path validation (covers lines 257-261)."""
        # cwd not a string
        report1 = ValidationReport()
        config1 = {"command": "gopls", "cwd": 42, "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config1, report1)
        assert report1.has_major
        assert _has_message_containing(report1, "'cwd' must be a string")

        # cwd with absolute path -> MAJOR via path validation
        report2 = ValidationReport()
        config2 = {"command": "gopls", "cwd": "/usr/local/go", "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config2, report2)
        assert report2.has_major
        assert _has_message_containing(report2, "Absolute path found")

    def test_timeout_fields_validation(self):
        """Non-numeric and non-positive timeout values produce MAJOR (covers lines 276, 278)."""
        # not a number
        report1 = ValidationReport()
        config1 = {"command": "gopls", "startupTimeout": "fast", "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config1, report1)
        assert report1.has_major
        assert _has_message_containing(report1, "'startupTimeout' must be a number")

        # non-positive
        report2 = ValidationReport()
        config2 = {"command": "gopls", "shutdownTimeout": -100, "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config2, report2)
        assert report2.has_major
        assert _has_message_containing(report2, "'shutdownTimeout' must be positive")

    def test_max_restarts_and_restart_on_crash_validation(self):
        """maxRestarts non-int/negative and restartOnCrash non-bool produce MAJOR (covers lines 282-292)."""
        # maxRestarts not an int
        report1 = ValidationReport()
        config1 = {"command": "gopls", "maxRestarts": 3.5, "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config1, report1)
        assert report1.has_major
        assert _has_message_containing(report1, "'maxRestarts' must be an integer")

        # maxRestarts negative
        report2 = ValidationReport()
        config2 = {"command": "gopls", "maxRestarts": -1, "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config2, report2)
        assert report2.has_major
        assert _has_message_containing(report2, "'maxRestarts' must be non-negative")

        # restartOnCrash not a bool
        report3 = ValidationReport()
        config3 = {"command": "gopls", "restartOnCrash": "yes", "extensionToLanguage": {".go": "go"}}
        validate_lsp_server("gopls", config3, report3)
        assert report3.has_major
        assert _has_message_containing(report3, "'restartOnCrash' must be a boolean")


class TestValidateLspConfigExtended:
    """Extended tests for validate_lsp_config covering uncovered branches."""

    def test_config_file_not_found_produces_info(self, tmp_path):
        """A non-existent config path should produce INFO and return early (covers lines 323-324)."""
        report = validate_lsp_config(tmp_path / "nonexistent.json", plugin_root=tmp_path)
        assert not report.has_critical
        assert _has_message_containing(report, "LSP config file not found")

    def test_relative_path_computation_with_plugin_root(self, tmp_path):
        """Config path outside plugin_root still works with fallback relative path (covers lines 319-320)."""
        import tempfile
        with tempfile.TemporaryDirectory() as other_dir:
            config_file = Path(other_dir) / "lsp-config.json"
            config_file.write_text(json.dumps({"languageServers": {"x": {"command": "x", "extensionToLanguage": {".x": "x"}}}}))
            # plugin_root is tmp_path but config is in other_dir - relative_to raises ValueError
            report = validate_lsp_config(config_file, plugin_root=tmp_path)
            # Should still validate successfully (uses filename as rel_path fallback)
            assert _has_message_containing(report, "valid JSON")

    def test_no_servers_key_produces_info(self, tmp_path):
        """A JSON file with no languageServers/lspServers/servers key returns INFO (covers lines 343-344)."""
        config_file = tmp_path / "lsp-config.json"
        config_file.write_text(json.dumps({"someOtherKey": {}}))
        report = validate_lsp_config(config_file, plugin_root=tmp_path)
        assert _has_message_containing(report, "No language server definitions found")

    def test_servers_key_not_dict_produces_critical(self, tmp_path):
        """A languageServers value that is not a dict produces CRITICAL (covers lines 349-350)."""
        config_file = tmp_path / "lsp-config.json"
        config_file.write_text(json.dumps({"languageServers": ["not", "a", "dict"]}))
        report = validate_lsp_config(config_file, plugin_root=tmp_path)
        assert report.has_critical
        assert _has_message_containing(report, "must be an object")

    def test_empty_servers_dict_produces_info(self, tmp_path):
        """An empty languageServers object produces INFO (covers lines 353-354)."""
        config_file = tmp_path / "lsp-config.json"
        config_file.write_text(json.dumps({"languageServers": {}}))
        report = validate_lsp_config(config_file, plugin_root=tmp_path)
        assert _has_message_containing(report, "No LSP servers defined")

    def test_server_config_not_dict_produces_critical(self, tmp_path):
        """A server entry that is not a dict produces CRITICAL (covers lines 361-362)."""
        config_file = tmp_path / "lsp-config.json"
        config_file.write_text(json.dumps({"languageServers": {"bad-server": "not-a-dict"}}))
        report = validate_lsp_config(config_file, plugin_root=tmp_path)
        assert report.has_critical
        assert _has_message_containing(report, "config must be an object")

    def test_lsp_servers_key_alias_works(self, tmp_path):
        """The 'lspServers' key alias should be recognized as a valid servers key."""
        config_file = tmp_path / "lsp-config.json"
        config_file.write_text(json.dumps({
            "lspServers": {
                "gopls": {
                    "command": "gopls",
                    "extensionToLanguage": {".go": "go"},
                },
            },
        }))
        report = validate_lsp_config(config_file, plugin_root=tmp_path)
        assert _has_message_containing(report, "1 LSP server")


class TestValidatePluginLspExtended:
    """Extended tests for validate_plugin_lsp with real config files."""

    def test_finds_existing_lsp_json_config(self, tmp_path):
        """A plugin with .lsp.json should discover and validate it (covers lines 396-397)."""
        lsp_file = tmp_path / ".lsp.json"
        config_data = {
            "languageServers": {
                "rust-analyzer": {
                    "command": "rust-analyzer",
                    "filetypes": ["rust"],
                    "rootPatterns": ["Cargo.toml"],
                    "transport": "stdio",
                    "extensionToLanguage": {".rs": "rust"},
                },
            },
        }
        lsp_file.write_text(json.dumps(config_data))
        report = validate_plugin_lsp(tmp_path)
        assert not _has_message_containing(report, "No LSP configuration files found")
        assert _has_message_containing(report, "valid JSON")
        assert _has_message_containing(report, "1 LSP server")


class TestPrintResults:
    """Tests for print_results output formatting (covers lines 407-461)."""

    def test_print_results_non_verbose(self, capsys):
        """print_results in non-verbose mode omits PASSED and INFO lines."""
        report = ValidationReport()
        report.passed("This should be hidden")
        report.info("This info should also be hidden")
        report.major("Visible major issue")
        print_results(report, verbose=False)
        captured = capsys.readouterr()
        assert "Visible major issue" in captured.out
        assert "This should be hidden" not in captured.out
        assert "This info should also be hidden" not in captured.out
        assert "MAJOR issues found" in captured.out

    def test_print_results_verbose(self, capsys):
        """print_results in verbose mode shows PASSED and INFO lines."""
        report = ValidationReport()
        report.passed("Visible passed check")
        report.info("Visible info note")
        print_results(report, verbose=True)
        captured = capsys.readouterr()
        assert "Visible passed check" in captured.out
        assert "Visible info note" in captured.out
        assert "All LSP checks passed" in captured.out

    def test_print_results_minor_exit_code(self, capsys):
        """print_results shows MINOR marker for minor-only issues."""
        report = ValidationReport()
        report.minor("A minor issue")
        print_results(report, verbose=False)
        captured = capsys.readouterr()
        assert "MINOR issues found" in captured.out


class TestMainFunction:
    """Tests for main() entry point (covers lines 466-521)."""

    def test_main_with_valid_config_file(self, tmp_path, monkeypatch):
        """main() with a valid LSP config file returns exit code 0 (covers main happy path)."""
        config_file = tmp_path / "lsp-config.json"
        config_data = {
            "languageServers": {
                "gopls": {
                    "command": "gopls",
                    "extensionToLanguage": {".go": "go"},
                },
            },
        }
        config_file.write_text(json.dumps(config_data))
        monkeypatch.setattr(sys, "argv", ["validate_lsp.py", str(config_file)])
        exit_code = main()
        assert exit_code == 0

    def test_main_with_nonexistent_path(self, tmp_path, monkeypatch, capsys):
        """main() with a nonexistent path prints error and returns 1."""
        monkeypatch.setattr(sys, "argv", ["validate_lsp.py", str(tmp_path / "does-not-exist")])
        exit_code = main()
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_main_with_json_output(self, tmp_path, monkeypatch, capsys):
        """main() with --json flag outputs valid JSON to stdout (covers lines 494-514)."""
        config_file = tmp_path / "lsp-config.json"
        config_data = {
            "languageServers": {
                "gopls": {
                    "command": "gopls",
                    "extensionToLanguage": {".go": "go"},
                },
            },
        }
        config_file.write_text(json.dumps(config_data))
        monkeypatch.setattr(sys, "argv", ["validate_lsp.py", "--json", str(config_file)])
        exit_code = main()
        assert exit_code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "exit_code" in output
        assert "counts" in output
        assert "results" in output

    def test_main_with_directory(self, tmp_path, monkeypatch):
        """main() with a plugin directory runs validate_plugin_lsp (covers line 491)."""
        monkeypatch.setattr(sys, "argv", ["validate_lsp.py", str(tmp_path)])
        exit_code = main()
        assert exit_code == 0

    def test_main_strict_mode(self, tmp_path, monkeypatch):
        """main() with --strict flag returns strict exit code that catches NIT issues (covers lines 518-519)."""
        config_file = tmp_path / "lsp-config.json"
        # Create a config that produces a NIT (missing extensionToLanguage is MINOR, not NIT)
        # A server with only MINOR issue to verify strict mode return value
        config_data = {
            "languageServers": {
                "gopls": {
                    "command": "gopls",
                    # missing extensionToLanguage -> MINOR
                },
            },
        }
        config_file.write_text(json.dumps(config_data))
        monkeypatch.setattr(sys, "argv", ["validate_lsp.py", "--strict", str(config_file)])
        exit_code = main()
        # MINOR issues yield exit code 3 in both normal and strict mode
        assert exit_code == 3

    def test_main_no_path_uses_cwd(self, tmp_path, monkeypatch):
        """main() with no path argument uses current working directory (covers lines 480-481)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["validate_lsp.py"])
        exit_code = main()
        assert exit_code == 0
