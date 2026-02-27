#!/usr/bin/env python3
"""
Tests for validate_mcp.py

Validates MCP server configuration parsing and validation logic.
Tests cover: validate_env_var_syntax, validate_path_value, validate_mcp_server
(stdio, http, sse transports), validate_mcp_config, validate_plugin_mcp,
plus edge cases for malformed input.

Coverage: 10 tests covering all major code paths with real file I/O via tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_mcp import (
    extract_env_vars,
    validate_env_var_syntax,
    validate_mcp_config,
    validate_mcp_server,
    validate_path_value,
    validate_plugin_mcp,
)
from validation_common import ValidationReport


class TestValidateEnvVarSyntax:
    """Tests for environment variable syntax validation."""

    def test_malformed_and_valid_env_var_syntax(self):
        """Unclosed brace should be MAJOR; non-plugin var without default should be INFO."""
        # Unclosed brace case
        report = ValidationReport()
        validate_env_var_syntax("${UNCLOSED", report, "test-context")
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) == 1
        assert "unclosed braces" in majors[0].message.lower()

        # Non-plugin env var without default case
        report2 = ValidationReport()
        validate_env_var_syntax("${CUSTOM_VAR}", report2, "test-context")
        infos = [r for r in report2.results if r.level == "INFO"]
        assert len(infos) == 1
        assert "CUSTOM_VAR" in infos[0].message
        assert "no default" in infos[0].message.lower()


class TestValidatePathValue:
    """Tests for path value validation in MCP configs."""

    def test_absolute_path_flagged_as_major(self):
        """Absolute Unix path should be flagged as MAJOR for portability."""
        report = ValidationReport()
        validate_path_value("/usr/bin/node", report, "test-context")
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) == 1
        assert "Absolute path" in majors[0].message

    def test_relative_path_without_plugin_root_var_flagged(self, tmp_path):
        """Relative file path without CLAUDE_PLUGIN_ROOT should get a MINOR finding."""
        report = ValidationReport()
        validate_path_value("lib/server.js", report, "test-context", plugin_root=tmp_path)
        minors = [r for r in report.results if r.level == "MINOR"]
        assert len(minors) == 1
        assert "CLAUDE_PLUGIN_ROOT" in minors[0].message


class TestValidateMcpServerStdio:
    """Tests for stdio transport server validation."""

    def test_stdio_server_missing_command_is_critical(self):
        """A stdio server without a command field should be CRITICAL."""
        report = ValidationReport()
        config = {"type": "stdio", "args": ["--port", "8080"]}
        validate_mcp_server("my-server", config, report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert any("missing required 'command'" in c.message for c in criticals)

    def test_valid_stdio_server_with_npx_warns_remote_package(self):
        """stdio server using npx with remote package should produce a WARNING."""
        report = ValidationReport()
        config = {
            "type": "stdio",
            "command": "npx",
            "args": ["@modelcontextprotocol/server-filesystem", "/tmp"],
        }
        validate_mcp_server("fs-server", config, report)
        warnings = [r for r in report.results if r.level == "WARNING"]
        assert any("remote package" in w.message.lower() for w in warnings)


class TestValidateMcpServerHttpSse:
    """Tests for http and sse transport server validation."""

    def test_http_server_missing_url_is_critical(self):
        """An http server without a url field should be CRITICAL."""
        report = ValidationReport()
        config = {"type": "http"}
        validate_mcp_server("remote-server", config, report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert any("missing 'url'" in c.message.lower() for c in criticals)

    def test_sse_transport_deprecated_minor(self):
        """SSE transport should produce a MINOR deprecation finding."""
        report = ValidationReport()
        config = {"type": "sse", "url": "http://localhost:3000/sse"}
        validate_mcp_server("sse-server", config, report)
        minors = [r for r in report.results if r.level == "MINOR"]
        assert any("deprecated" in m.message.lower() for m in minors)


class TestValidateMcpConfig:
    """Tests for full .mcp.json file validation."""

    def test_valid_mcp_json_parses_servers(self, tmp_path):
        """A well-formed .mcp.json with one stdio server should produce PASSED results and no critical/major."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_data = {
            "mcpServers": {
                "test-server": {
                    "type": "stdio",
                    "command": "node",
                    "args": ["server.js"],
                }
            }
        }
        mcp_file.write_text(json.dumps(mcp_data))
        report = validate_mcp_config(mcp_file, plugin_root=tmp_path)
        assert not report.has_critical
        assert not report.has_major
        passed = [r for r in report.results if r.level == "PASSED"]
        assert len(passed) >= 1

    def test_invalid_json_is_critical(self, tmp_path):
        """Malformed JSON in .mcp.json should produce a CRITICAL finding."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text("{not valid json!!!")
        report = validate_mcp_config(mcp_file)
        assert report.has_critical
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert any("Invalid JSON" in c.message for c in criticals)


class TestValidatePluginMcp:
    """Tests for the top-level plugin MCP validation entry point."""

    def test_plugin_with_inline_mcp_servers_validates(self, tmp_path):
        """Plugin with inline mcpServers in plugin.json should validate each server."""
        claude_dir = tmp_path / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {
            "name": "test-plugin",
            "version": "1.0.0",
            "description": "A test plugin",
            "mcpServers": {
                "inline-server": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "my_mcp_server"],
                }
            },
        }
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = validate_plugin_mcp(tmp_path)
        # Should have INFO about inline servers found plus PASSED from server validation
        infos = [r for r in report.results if r.level == "INFO"]
        assert any("inline" in i.message.lower() for i in infos)
        passed = [r for r in report.results if r.level == "PASSED"]
        assert len(passed) >= 1
        assert not report.has_critical


# ============================================================================
# ADDITIONAL TESTS (appended to cover uncovered lines)
# ============================================================================

from validate_mcp import main, print_results


class TestExtractEnvVars:
    """Tests for extract_env_vars utility function."""

    def test_extract_env_vars_returns_name_and_default(self):
        """extract_env_vars should return (var_name, default) tuples from ${VAR:-default} syntax."""
        result = extract_env_vars("${MY_VAR:-fallback}/path/${OTHER}")
        assert ("MY_VAR", "fallback") in result
        assert ("OTHER", "") in result


class TestValidatePathValuePluginRoot:
    """Tests for path resolution when CLAUDE_PLUGIN_ROOT is used."""

    def test_plugin_root_path_resolved_file_missing_produces_info(self, tmp_path):
        """Path using CLAUDE_PLUGIN_ROOT that resolves to missing file should produce INFO."""
        report = ValidationReport()
        validate_path_value(
            "${CLAUDE_PLUGIN_ROOT}/lib/server.js", report, "test-ctx", plugin_root=tmp_path
        )
        infos = [r for r in report.results if r.level == "INFO"]
        assert any("may not exist" in i.message for i in infos)

    def test_plugin_root_path_resolved_file_exists_no_info(self, tmp_path):
        """Path using CLAUDE_PLUGIN_ROOT that resolves to existing file should NOT produce missing-file INFO."""
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "server.js").write_text("console.log('hi');")
        report = ValidationReport()
        validate_path_value(
            "${CLAUDE_PLUGIN_ROOT}/lib/server.js", report, "test-ctx", plugin_root=tmp_path
        )
        infos = [r for r in report.results if r.level == "INFO"]
        assert not any("may not exist" in i.message for i in infos)


class TestValidateMcpServerUnknownField:
    """Tests for unknown field and invalid transport detection."""

    def test_unknown_field_produces_warning(self):
        """Unknown configuration field should produce a WARNING."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "bogus_field": True}
        validate_mcp_server("test-srv", config, report)
        warnings = [r for r in report.results if r.level == "WARNING"]
        assert any("Unknown field 'bogus_field'" in w.message for w in warnings)

    def test_invalid_transport_type_produces_major(self):
        """Invalid transport type should produce MAJOR and fall back to stdio validation."""
        report = ValidationReport()
        config = {"type": "grpc", "command": "node", "args": ["server.js"]}
        validate_mcp_server("bad-transport", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("Invalid transport type 'grpc'" in m.message for m in majors)


class TestValidateMcpServerStdioAdvanced:
    """Advanced stdio transport tests covering executable checks and edge cases."""

    def test_command_with_plugin_root_not_executable_produces_major(self, tmp_path):
        """Command referencing CLAUDE_PLUGIN_ROOT that exists but is not executable should be MAJOR."""
        script = tmp_path / "my_server.py"
        script.write_text("print('hello')")
        script.chmod(0o644)
        report = ValidationReport()
        config = {"type": "stdio", "command": "${CLAUDE_PLUGIN_ROOT}/my_server.py"}
        validate_mcp_server("srv", config, report, plugin_root=tmp_path)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("not executable" in m.message for m in majors)

    def test_command_not_in_path_produces_info(self):
        """Command not found in PATH should produce INFO (may be resolved at runtime)."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "nonexistent_binary_xyz_12345"}
        validate_mcp_server("srv", config, report)
        infos = [r for r in report.results if r.level == "INFO"]
        assert any("not found" in i.message for i in infos)

    def test_stdio_server_with_url_field_produces_info(self):
        """stdio server with url field should produce INFO that url will be ignored."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "url": "http://localhost:3000"}
        validate_mcp_server("srv", config, report)
        infos = [r for r in report.results if r.level == "INFO"]
        assert any("url will be ignored" in i.message for i in infos)


class TestValidateMcpServerHttpAdvanced:
    """Advanced HTTP/SSE transport tests covering URL validation and security warnings."""

    def test_http_invalid_url_scheme_produces_major(self):
        """HTTP server with non-http(s) URL should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "http", "url": "ftp://example.com/mcp"}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("http(s)://" in m.message for m in majors)

    def test_http_remote_url_warns_and_flags_unencrypted(self):
        """Remote non-localhost URL should warn about trust; HTTP (no TLS) should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "http", "url": "http://remote-server.example.com/mcp"}
        validate_mcp_server("srv", config, report)
        warnings = [r for r in report.results if r.level == "WARNING"]
        assert any("remote URL" in w.message for w in warnings)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("unencrypted HTTP" in m.message for m in majors)

    def test_http_server_with_command_field_produces_info(self):
        """HTTP server with command field should produce INFO that command will be ignored."""
        report = ValidationReport()
        config = {"type": "http", "url": "http://localhost:3000/mcp", "command": "node"}
        validate_mcp_server("srv", config, report)
        infos = [r for r in report.results if r.level == "INFO"]
        assert any("command will be ignored" in i.message for i in infos)


class TestValidateMcpServerFieldValidation:
    """Tests for args, env, cwd, headers, timeout, oauth field validation."""

    def test_args_not_list_produces_major(self):
        """args field that is not a list should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "args": "not-a-list"}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("'args' must be an array" in m.message for m in majors)

    def test_args_non_string_element_produces_major(self):
        """Non-string element in args array should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "args": ["--port", 8080]}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("args[1] must be a string" in m.message for m in majors)

    def test_env_not_dict_produces_major(self):
        """env field that is not a dict should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "env": "FOO=bar"}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("'env' must be an object" in m.message for m in majors)

    def test_env_non_string_value_produces_major(self):
        """Non-string value in env dict should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "env": {"PORT": 8080}}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("env[PORT] must be a string" in m.message for m in majors)

    def test_cwd_not_string_produces_major(self):
        """cwd field that is not a string should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "cwd": 123}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("'cwd' must be a string" in m.message for m in majors)

    def test_cwd_string_triggers_path_validation(self, tmp_path):
        """String cwd should trigger path validation (e.g., MINOR if relative with plugin_root)."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "cwd": "subdir/work"}
        validate_mcp_server("srv", config, report, plugin_root=tmp_path)
        minors = [r for r in report.results if r.level == "MINOR"]
        assert any("CLAUDE_PLUGIN_ROOT" in m.message for m in minors)

    def test_headers_not_dict_produces_major(self):
        """headers field that is not a dict should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "http", "url": "http://localhost:3000", "headers": "bad"}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("'headers' must be an object" in m.message for m in majors)

    def test_headers_non_string_value_produces_major(self):
        """Non-string value in headers dict should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "http", "url": "http://localhost:3000", "headers": {"Accept": 42}}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("headers[Accept] must be a string" in m.message for m in majors)

    def test_hardcoded_authorization_header_produces_major(self):
        """Hardcoded credential in Authorization header should produce MAJOR."""
        report = ValidationReport()
        config = {
            "type": "http",
            "url": "http://localhost:3000",
            "headers": {"Authorization": "Bearer sk-1234567890abcdef"},
        }
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("hardcoded credential" in m.message.lower() for m in majors)

    def test_timeout_not_number_produces_major(self):
        """timeout field that is not a number should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "timeout": "30s"}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("'timeout' must be a number" in m.message for m in majors)

    def test_timeout_zero_or_negative_produces_major(self):
        """timeout <= 0 should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "timeout": -5}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("must be positive" in m.message for m in majors)

    def test_valid_timeout_produces_passed(self):
        """Valid positive timeout should produce PASSED."""
        report = ValidationReport()
        config = {"type": "stdio", "command": "node", "timeout": 30}
        validate_mcp_server("srv", config, report)
        passed = [r for r in report.results if r.level == "PASSED"]
        assert any("timeout: 30" in p.message for p in passed)

    def test_oauth_not_dict_produces_major(self):
        """oauth field that is not a dict should produce MAJOR."""
        report = ValidationReport()
        config = {"type": "http", "url": "http://localhost:3000", "oauth": "bad"}
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("'oauth' must be an object" in m.message for m in majors)

    def test_oauth_invalid_clientid_type_produces_major(self):
        """oauth.clientId that is not a string should produce MAJOR."""
        report = ValidationReport()
        config = {
            "type": "http",
            "url": "http://localhost:3000",
            "oauth": {"clientId": 12345, "callbackPort": 8080},
        }
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("oauth.clientId" in m.message for m in majors)

    def test_oauth_invalid_callbackport_type_produces_major(self):
        """oauth.callbackPort that is not an integer should produce MAJOR."""
        report = ValidationReport()
        config = {
            "type": "http",
            "url": "http://localhost:3000",
            "oauth": {"clientId": "my-client", "callbackPort": "8080"},
        }
        validate_mcp_server("srv", config, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("oauth.callbackPort" in m.message for m in majors)

    def test_valid_oauth_produces_passed(self):
        """Valid oauth config should produce PASSED."""
        report = ValidationReport()
        config = {
            "type": "http",
            "url": "http://localhost:3000",
            "oauth": {"clientId": "my-client", "callbackPort": 8080},
        }
        validate_mcp_server("srv", config, report)
        passed = [r for r in report.results if r.level == "PASSED"]
        assert any("OAuth configuration" in p.message for p in passed)


class TestValidateMcpConfigAdvanced:
    """Advanced tests for validate_mcp_config covering edge cases."""

    def test_config_not_found_produces_info(self, tmp_path):
        """Missing .mcp.json file should produce INFO, not crash."""
        report = validate_mcp_config(tmp_path / "nonexistent.mcp.json")
        infos = [r for r in report.results if r.level == "INFO"]
        assert any("not found" in i.message for i in infos)

    def test_config_relative_path_from_plugin_root(self, tmp_path):
        """Config path relative to plugin_root should be used in messages."""
        subdir = tmp_path / "configs"
        subdir.mkdir()
        mcp_file = subdir / ".mcp.json"
        mcp_file.write_text(json.dumps({"mcpServers": {}}))
        report = validate_mcp_config(mcp_file, plugin_root=tmp_path)
        infos = [r for r in report.results if r.level == "INFO"]
        assert any("configs/.mcp.json" in i.message for i in infos)

    def test_no_mcp_servers_field_produces_info(self, tmp_path):
        """JSON file without mcpServers field should produce INFO."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({"other": "data"}))
        report = validate_mcp_config(mcp_file)
        infos = [r for r in report.results if r.level == "INFO"]
        assert any("No 'mcpServers' field" in i.message for i in infos)

    def test_mcp_servers_not_dict_produces_critical(self, tmp_path):
        """mcpServers that is not a dict should produce CRITICAL."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({"mcpServers": ["not", "a", "dict"]}))
        report = validate_mcp_config(mcp_file)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert any("must be an object" in c.message for c in criticals)

    def test_empty_mcp_servers_produces_info(self, tmp_path):
        """Empty mcpServers dict should produce INFO."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({"mcpServers": {}}))
        report = validate_mcp_config(mcp_file)
        infos = [r for r in report.results if r.level == "INFO"]
        assert any("No MCP servers defined" in i.message for i in infos)

    def test_invalid_server_name_format_produces_minor(self, tmp_path):
        """Server name starting with number should produce MINOR."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {"123bad": {"type": "stdio", "command": "node"}}
        }))
        report = validate_mcp_config(mcp_file)
        minors = [r for r in report.results if r.level == "MINOR"]
        assert any("should be alphanumeric" in m.message for m in minors)

    def test_server_config_not_dict_produces_critical(self, tmp_path):
        """Server config that is not a dict should produce CRITICAL and skip validation."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {"bad-server": "not-a-dict"}
        }))
        report = validate_mcp_config(mcp_file)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert any("config must be an object" in c.message for c in criticals)


class TestValidatePluginMcpAdvanced:
    """Advanced tests for validate_plugin_mcp covering path references and edge cases."""

    def test_plugin_with_mcp_json_file_validates(self, tmp_path):
        """Plugin root with .mcp.json file should validate it."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {"srv": {"type": "stdio", "command": "node"}}
        }))
        report = validate_plugin_mcp(tmp_path)
        passed = [r for r in report.results if r.level == "PASSED"]
        assert len(passed) >= 1

    def test_plugin_json_mcp_servers_as_dotslash_path_reference(self, tmp_path):
        """mcpServers as string path starting with ./ should resolve relative to plugin root."""
        claude_dir = tmp_path / ".claude-plugin"
        claude_dir.mkdir()
        ext_config = tmp_path / "configs" / "mcp.json"
        ext_config.parent.mkdir()
        ext_config.write_text(json.dumps({
            "mcpServers": {"ext-srv": {"type": "stdio", "command": "node"}}
        }))
        manifest = {"name": "test", "mcpServers": "./configs/mcp.json"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = validate_plugin_mcp(tmp_path)
        passed = [r for r in report.results if r.level == "PASSED"]
        assert len(passed) >= 1

    def test_plugin_json_mcp_servers_as_path_without_dotslash(self, tmp_path):
        """mcpServers as string path without ./ prefix should resolve relative to plugin root."""
        claude_dir = tmp_path / ".claude-plugin"
        claude_dir.mkdir()
        ext_config = tmp_path / "mcp-config.json"
        ext_config.write_text(json.dumps({
            "mcpServers": {"ext-srv": {"type": "stdio", "command": "node"}}
        }))
        manifest = {"name": "test", "mcpServers": "mcp-config.json"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = validate_plugin_mcp(tmp_path)
        passed = [r for r in report.results if r.level == "PASSED"]
        assert len(passed) >= 1

    def test_plugin_json_mcp_servers_path_not_found_produces_major(self, tmp_path):
        """mcpServers string path pointing to non-existent file should produce MAJOR."""
        claude_dir = tmp_path / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "test", "mcpServers": "./missing/mcp.json"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = validate_plugin_mcp(tmp_path)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("not found" in m.message.lower() for m in majors)

    def test_plugin_json_inline_server_config_not_dict_produces_critical(self, tmp_path):
        """Inline mcpServers with non-dict server config should produce CRITICAL."""
        claude_dir = tmp_path / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {
            "name": "test",
            "mcpServers": {"bad-srv": "not-a-dict-config"},
        }
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = validate_plugin_mcp(tmp_path)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert any("config must be an object" in c.message for c in criticals)

    def test_plugin_json_mcp_servers_invalid_type_produces_major(self, tmp_path):
        """mcpServers that is neither string nor dict should produce MAJOR."""
        claude_dir = tmp_path / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "test", "mcpServers": 42}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = validate_plugin_mcp(tmp_path)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("must be a string (path) or object" in m.message for m in majors)

    def test_plugin_json_invalid_json_silently_skips(self, tmp_path):
        """plugin.json with invalid JSON should be silently handled (validated elsewhere)."""
        claude_dir = tmp_path / ".claude-plugin"
        claude_dir.mkdir()
        (claude_dir / "plugin.json").write_text("{broken json!!!")
        report = validate_plugin_mcp(tmp_path)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 0


class TestPrintResults:
    """Tests for print_results output formatting."""

    def test_print_results_non_verbose_hides_passed_and_info(self, capsys):
        """Non-verbose mode should hide PASSED and INFO results."""
        report = ValidationReport()
        report.passed("Check OK")
        report.info("Informational")
        report.warning("Watch out")
        print_results(report, verbose=False)
        out = capsys.readouterr().out
        assert "Watch out" in out
        assert "Check OK" not in out
        assert "Informational" not in out
        assert "MCP Configuration Validation Report" in out

    def test_print_results_verbose_shows_everything(self, capsys):
        """Verbose mode should show PASSED and INFO results too."""
        report = ValidationReport()
        report.passed("Check OK")
        report.info("Informational")
        report.minor("A minor issue")
        print_results(report, verbose=True)
        out = capsys.readouterr().out
        assert "Check OK" in out
        assert "Informational" in out
        assert "A minor issue" in out

    def test_print_results_with_issues_shows_failure_indicator(self, capsys):
        """Report with issues should show failure indicator."""
        report = ValidationReport()
        report.critical("Big problem")
        print_results(report, verbose=False)
        out = capsys.readouterr().out
        assert "Issues found" in out or "issues found" in out.lower()


class TestMainFunction:
    """Tests for the main() CLI entry point."""

    def test_main_with_valid_mcp_json_file(self, tmp_path, monkeypatch):
        """main() with a valid .mcp.json file path should return 0."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {"srv": {"type": "stdio", "command": "node", "args": ["server.js"]}}
        }))
        monkeypatch.setattr("sys.argv", ["validate_mcp", str(mcp_file)])
        result = main()
        assert result == 0

    def test_main_with_directory_path(self, tmp_path, monkeypatch):
        """main() with a plugin directory should validate it and return 0."""
        monkeypatch.setattr("sys.argv", ["validate_mcp", str(tmp_path)])
        result = main()
        assert result == 0

    def test_main_nonexistent_path_returns_1(self, tmp_path, monkeypatch):
        """main() with non-existent path should return 1."""
        monkeypatch.setattr("sys.argv", ["validate_mcp", str(tmp_path / "does_not_exist")])
        result = main()
        assert result == 1

    def test_main_json_output_mode(self, tmp_path, monkeypatch, capsys):
        """main() with --json flag should output valid JSON."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {"srv": {"type": "stdio", "command": "node"}}
        }))
        monkeypatch.setattr("sys.argv", ["validate_mcp", "--json", str(mcp_file)])
        result = main()
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert "exit_code" in parsed
        assert "counts" in parsed
        assert "results" in parsed
        assert result == 0

    def test_main_strict_mode(self, tmp_path, monkeypatch, capsys):
        """main() with --strict flag should use exit_code_strict."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({
            "mcpServers": {"srv": {"type": "stdio", "command": "node"}}
        }))
        monkeypatch.setattr("sys.argv", ["validate_mcp", "--strict", str(mcp_file)])
        result = main()
        assert result == 0
