#!/usr/bin/env python3
"""Issue #166 — Claude Code v2.1.207 plugin-config spec changes.

Verbatim from the CC v2.1.207 changelog (the ground truth these tests encode):

    "Plugin hooks/monitors/MCP headersHelper: ${user_config.*} in shell-form
     commands is now rejected (shell-injection fix). Hooks: use exec form
     (`args` array) or $CLAUDE_PLUGIN_OPTION_<KEY>; monitors and headersHelper:
     read the value inside the script (config file or the server's `env` block)."

    "Plugin option values (`pluginConfigs`) are no longer read from project-level
     `.claude/settings.json`; only user, `--settings`, and managed settings are
     honored"

EVERY rule here is tested TWO-SIDED: a POSITIVE case (the violating input fires
the finding) AND a NEGATIVE case (the legal input does NOT). The negatives carry
most of the weight, because the two ways to comply with the fix —

    * exec form:  {"command": "node", "args": ["${user_config.key}"]}
    * env read:   $CLAUDE_PLUGIN_OPTION_KEY

— are the very shapes a careless token-grep would flag. Flagging the remedy would
be a hard failure of this rule, so those negatives are regression LOCKS, not
decoration.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import (  # noqa: E402
    find_user_config_interpolations,
    hook_is_exec_form,
    validate_command_hook,
)
from validate_mcp import validate_mcp_server  # noqa: E402
from validate_plugin import (  # noqa: E402
    check_project_settings_plugin_configs,
    validate_inline_hooks_user_config,
    validate_monitors_entries,
)

SHELL_INJECT_CODE = "[RC-USERCFG-SHELL-INJECT]"
PROJECT_SETTINGS_CODE = "[RC-USERCFG-PROJECT-SETTINGS]"


def _messages(report: ValidationReport, code: str, level: str | None = None) -> list[str]:
    """Every message carrying ``code`` (optionally filtered to one severity)."""
    return [
        r.message
        for r in report.results
        if code in r.message and (level is None or r.level == level)
    ]


def _blocking(report: ValidationReport) -> list[str]:
    """Findings at a severity that blocks ``--strict`` (CRITICAL/MAJOR/MINOR/NIT)."""
    return [r.message for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT")]


# ---------------------------------------------------------------------------
# The exec-form predicate — the discriminator the whole rule rests on.
# ---------------------------------------------------------------------------


class TestHookIsExecForm:
    """The SSOT predicate that separates the rejected form from the prescribed fix."""

    def test_command_plus_args_is_exec_form(self) -> None:
        """The canonical exec form (command + non-empty args) is exec form."""
        assert hook_is_exec_form({"command": "node", "args": ["x.js"]}) is True

    def test_args_only_is_exec_form(self) -> None:
        """The args-only legacy form (argv[0] is the executable) is exec form."""
        assert hook_is_exec_form({"args": ["node", "x.js"]}) is True

    def test_command_without_args_is_shell_form(self) -> None:
        """A bare command string is SHELL form — a shell parses it."""
        assert hook_is_exec_form({"command": "node x.js"}) is False

    def test_empty_args_is_not_exec_form(self) -> None:
        """FAIL-SAFE: an empty args list must not buy an exemption from the rule."""
        assert hook_is_exec_form({"command": "sh", "args": []}) is False

    def test_non_list_args_is_not_exec_form(self) -> None:
        """FAIL-SAFE: a malformed (non-list) args must not buy an exemption."""
        assert hook_is_exec_form({"command": "sh", "args": "not-a-list"}) is False

    def test_non_dict_input_is_not_exec_form(self) -> None:
        """A non-dict from a malformed manifest must not crash or exempt."""
        assert hook_is_exec_form("just a string") is False
        assert hook_is_exec_form(None) is False


class TestTokenGrammar:
    """The `${user_config.<key>}` token grammar (deliberately permissive)."""

    def test_finds_plain_token(self) -> None:
        """The ordinary `${user_config.key}` token is found."""
        assert find_user_config_interpolations("echo ${user_config.api_token}") == [
            "${user_config.api_token}"
        ]

    def test_finds_unusual_key(self) -> None:
        """An unusual/dotted/hyphenated key cannot slip through as a false negative."""
        assert find_user_config_interpolations("x ${user_config.a.b-c}") == ["${user_config.a.b-c}"]

    def test_env_var_form_is_not_a_token(self) -> None:
        """$CLAUDE_PLUGIN_OPTION_<KEY> is an env READ — never an interpolation."""
        assert find_user_config_interpolations("echo $CLAUDE_PLUGIN_OPTION_API_TOKEN") == []

    def test_other_substitutions_are_not_tokens(self) -> None:
        """Sibling substitution tokens must not be swept up by the grammar."""
        assert find_user_config_interpolations("${CLAUDE_PLUGIN_ROOT}/x.sh") == []

    def test_non_string_input_returns_empty(self) -> None:
        """A non-string `command` from a malformed manifest must not crash the scan."""
        assert find_user_config_interpolations(123) == []
        assert find_user_config_interpolations(None) == []


# ---------------------------------------------------------------------------
# SURFACE 1 — hooks (hooks/hooks.json, via validate_command_hook)
# ---------------------------------------------------------------------------


class TestHookShellForm:
    """Hooks: the token is rejected in shell form, legal in exec form."""

    def test_shell_form_token_is_critical(self, tmp_path: Path) -> None:
        """POSITIVE: `${user_config.*}` in a shell-form hook command → CRITICAL."""
        report = ValidationReport()
        hook = {"type": "command", "command": "curl -H ${user_config.api_token} https://x"}
        validate_command_hook(hook, "PreToolUse", tmp_path, report)
        hits = _messages(report, SHELL_INJECT_CODE, "CRITICAL")
        assert len(hits) == 1, f"shell-form token must fire exactly one CRITICAL; got {hits}"
        assert "${user_config.api_token}" in hits[0]

    def test_exec_form_token_is_not_flagged(self, tmp_path: Path) -> None:
        """NEGATIVE (the prescribed fix): the token inside `args` is spawned with no
        shell — flagging it would flag the remedy."""
        report = ValidationReport()
        hook = {
            "type": "command",
            "command": "node",
            "args": ["${CLAUDE_PLUGIN_ROOT}/x.js", "${user_config.api_token}"],
        }
        validate_command_hook(hook, "PreToolUse", tmp_path, report)
        assert _messages(report, SHELL_INJECT_CODE) == [], (
            "EXEC form is the fix the CC v2.1.207 changelog prescribes — it must NEVER fire"
        )

    def test_args_only_exec_form_token_is_not_flagged(self, tmp_path: Path) -> None:
        """NEGATIVE: the args-only exec form is equally shell-free."""
        report = ValidationReport()
        hook = {"type": "command", "args": ["node", "x.js", "${user_config.api_token}"]}
        validate_command_hook(hook, "PreToolUse", tmp_path, report)
        assert _messages(report, SHELL_INJECT_CODE) == []

    def test_plugin_option_env_var_is_not_flagged(self, tmp_path: Path) -> None:
        """NEGATIVE (the other prescribed fix): $CLAUDE_PLUGIN_OPTION_<KEY> in a
        shell-form command is an env read, not an interpolation."""
        report = ValidationReport()
        hook = {"type": "command", "command": 'curl -H "$CLAUDE_PLUGIN_OPTION_API_TOKEN" https://x'}
        validate_command_hook(hook, "PreToolUse", tmp_path, report)
        assert _messages(report, SHELL_INJECT_CODE) == []

    def test_ordinary_shell_command_is_not_flagged(self, tmp_path: Path) -> None:
        """NEGATIVE: a shell-form command with no token is untouched."""
        report = ValidationReport()
        hook = {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/fmt.sh"}
        validate_command_hook(hook, "PreToolUse", tmp_path, report)
        assert _messages(report, SHELL_INJECT_CODE) == []

    def test_multiple_tokens_collapse_into_one_finding(self, tmp_path: Path) -> None:
        """POSITIVE: several tokens on one command produce ONE finding naming both."""
        report = ValidationReport()
        hook = {
            "type": "command",
            "command": "x ${user_config.a} ${user_config.b}",
        }
        validate_command_hook(hook, "PreToolUse", tmp_path, report)
        hits = _messages(report, SHELL_INJECT_CODE, "CRITICAL")
        assert len(hits) == 1
        assert "${user_config.a}" in hits[0] and "${user_config.b}" in hits[0]

    def test_injection_payload_shape_is_flagged(self, tmp_path: Path) -> None:
        """POSITIVE: the shape the fix exists for — the value lands where a shell
        parses it, so a `;`-carrying config value would execute."""
        report = ValidationReport()
        hook = {"type": "command", "command": "sh -c 'deploy ${user_config.target}'"}
        validate_command_hook(hook, "PreToolUse", tmp_path, report)
        assert len(_messages(report, SHELL_INJECT_CODE, "CRITICAL")) == 1


# ---------------------------------------------------------------------------
# SURFACE 2 — monitors (plugin.json inline + monitors.json)
# ---------------------------------------------------------------------------


def _monitor(command: str) -> dict[str, Any]:
    return {"name": "watch", "command": command, "description": "d"}


class TestMonitorShellForm:
    """Monitors have NO exec form — every token in a monitor command is rejected."""

    def test_inline_monitor_token_is_critical(self, tmp_path: Path) -> None:
        """POSITIVE: `${user_config.*}` in an inline monitor command → CRITICAL."""
        report = ValidationReport()
        manifest = {"monitors": [_monitor("tail -f ${user_config.log_path}")]}
        validate_monitors_entries(manifest, tmp_path, report)
        hits = _messages(report, SHELL_INJECT_CODE, "CRITICAL")
        assert len(hits) == 1, f"monitor token must fire one CRITICAL; got {hits}"
        assert "${user_config.log_path}" in hits[0]

    def test_monitors_file_token_is_critical(self, tmp_path: Path) -> None:
        """POSITIVE: the same rule applies to a monitors.json file reference."""
        monitors_file = tmp_path / "monitors.json"
        monitors_file.write_text(json.dumps([_monitor("run ${user_config.key}")]), encoding="utf-8")
        report = ValidationReport()
        validate_monitors_entries({"monitors": "./monitors.json"}, tmp_path, report)
        assert len(_messages(report, SHELL_INJECT_CODE, "CRITICAL")) == 1

    def test_monitor_plugin_option_env_var_is_not_flagged(self, tmp_path: Path) -> None:
        """NEGATIVE (the prescribed fix): read the value inside the script via
        $CLAUDE_PLUGIN_OPTION_<KEY>."""
        report = ValidationReport()
        manifest = {"monitors": [_monitor('tail -f "$CLAUDE_PLUGIN_OPTION_LOG_PATH"')]}
        validate_monitors_entries(manifest, tmp_path, report)
        assert _messages(report, SHELL_INJECT_CODE) == []

    def test_ordinary_monitor_is_not_flagged(self, tmp_path: Path) -> None:
        """NEGATIVE: a monitor with no token is untouched."""
        report = ValidationReport()
        manifest = {"monitors": [_monitor("${CLAUDE_PLUGIN_ROOT}/scripts/watch.sh")]}
        validate_monitors_entries(manifest, tmp_path, report)
        assert _messages(report, SHELL_INJECT_CODE) == []

    def test_monitor_args_key_buys_no_exemption(self, tmp_path: Path) -> None:
        """POSITIVE (fail-safe): a monitor is shell-form by schema — an author cannot
        smuggle in an `args` key to escape the rule (it is not even a monitor field)."""
        report = ValidationReport()
        entry = _monitor("run ${user_config.key}")
        entry["args"] = ["${user_config.key}"]
        validate_monitors_entries({"monitors": [entry]}, tmp_path, report)
        assert len(_messages(report, SHELL_INJECT_CODE, "CRITICAL")) == 1


# ---------------------------------------------------------------------------
# SURFACE 3 — hooks declared INLINE in plugin.json
# ---------------------------------------------------------------------------


class TestInlinePluginJsonHooks:
    """The inline `hooks` object never reaches validate_hook — cover it here."""

    def test_inline_shell_form_token_is_critical(self) -> None:
        """POSITIVE: a shell-form inline hook command carrying the token → CRITICAL."""
        report = ValidationReport()
        manifest = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "x ${user_config.api_token}"}],
                    }
                ]
            }
        }
        validate_inline_hooks_user_config(manifest, report)
        hits = _messages(report, SHELL_INJECT_CODE, "CRITICAL")
        assert len(hits) == 1, f"inline shell-form hook must fire one CRITICAL; got {hits}"

    def test_inline_exec_form_is_not_flagged(self) -> None:
        """NEGATIVE (the prescribed fix): exec form inline is as legal as in hooks.json."""
        report = ValidationReport()
        manifest = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "node",
                                "args": ["x.js", "${user_config.api_token}"],
                            }
                        ],
                    }
                ]
            }
        }
        validate_inline_hooks_user_config(manifest, report)
        assert _messages(report, SHELL_INJECT_CODE) == []

    def test_inline_plugin_option_env_var_is_not_flagged(self) -> None:
        """NEGATIVE: the env-read fix, inline."""
        report = ValidationReport()
        manifest = {
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "x $CLAUDE_PLUGIN_OPTION_KEY"}]}]
            }
        }
        validate_inline_hooks_user_config(manifest, report)
        assert _messages(report, SHELL_INJECT_CODE) == []

    def test_inline_empty_args_still_fires(self) -> None:
        """POSITIVE (fail-safe): a malformed empty `args` is NOT exec form — a
        malformed hook must never buy an exemption from a security rule."""
        report = ValidationReport()
        manifest = {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "x ${user_config.k}", "args": []}]}
                ]
            }
        }
        validate_inline_hooks_user_config(manifest, report)
        assert len(_messages(report, SHELL_INJECT_CODE, "CRITICAL")) == 1

    def test_hooks_as_path_string_is_ignored(self) -> None:
        """NEGATIVE: the path form points at a hooks file that validate_hook owns —
        no double-report, no crash."""
        report = ValidationReport()
        validate_inline_hooks_user_config({"hooks": "./hooks/extra.json"}, report)
        assert report.results == []

    def test_no_hooks_field_is_a_no_op(self) -> None:
        """NEGATIVE: a manifest with no hooks field costs nothing and reports nothing."""
        report = ValidationReport()
        validate_inline_hooks_user_config({"name": "p"}, report)
        assert report.results == []


# ---------------------------------------------------------------------------
# SURFACE 4 — MCP headersHelper
# ---------------------------------------------------------------------------


class TestMcpHeadersHelper:
    """headersHelper has no exec-form companion — every token in it is rejected."""

    def test_headers_helper_token_is_critical(self, tmp_path: Path) -> None:
        """POSITIVE: `${user_config.*}` in headersHelper → CRITICAL."""
        report = ValidationReport()
        config = {
            "type": "http",
            "url": "https://api.example.com",
            "headersHelper": "auth.sh --token ${user_config.api_token}",
        }
        validate_mcp_server("srv", config, report, tmp_path)
        hits = _messages(report, SHELL_INJECT_CODE, "CRITICAL")
        assert len(hits) == 1, f"headersHelper token must fire one CRITICAL; got {hits}"
        assert "${user_config.api_token}" in hits[0]

    def test_ordinary_headers_helper_is_not_flagged(self, tmp_path: Path) -> None:
        """NEGATIVE: a helper with no token is untouched."""
        report = ValidationReport()
        config = {
            "type": "http",
            "url": "https://api.example.com",
            "headersHelper": "${CLAUDE_PLUGIN_ROOT}/scripts/auth.sh",
        }
        validate_mcp_server("srv", config, report, tmp_path)
        assert _messages(report, SHELL_INJECT_CODE) == []

    def test_env_block_token_is_not_flagged(self, tmp_path: Path) -> None:
        """NEGATIVE (the prescribed fix): the changelog says to read the value from
        the server's `env` block — so a token THERE is the remedy, not the defect.
        A token-grep over the whole server config would break this."""
        report = ValidationReport()
        config = {
            "command": "node",
            "args": ["server.js"],
            "env": {"API_TOKEN": "${user_config.api_token}"},
        }
        validate_mcp_server("srv", config, report, tmp_path)
        assert _messages(report, SHELL_INJECT_CODE) == [], (
            "the MCP `env` block is where CC v2.1.207 tells authors to put the value"
        )

    def test_stdio_command_token_is_not_flagged(self, tmp_path: Path) -> None:
        """NEGATIVE: the changelog names hooks, monitors and headersHelper — NOT the
        MCP server `command` (an stdio server is spawned with an argv vector). The
        rule must not over-reach into a surface the spec does not name."""
        report = ValidationReport()
        config = {"command": "node", "args": ["server.js", "${user_config.api_token}"]}
        validate_mcp_server("srv", config, report, tmp_path)
        assert _messages(report, SHELL_INJECT_CODE) == []


# ---------------------------------------------------------------------------
# B2 — pluginConfigs is no longer read from project-level settings
# ---------------------------------------------------------------------------


def _write_settings(plugin_root: Path, filename: str, data: dict[str, Any]) -> None:
    settings_dir = plugin_root / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / filename).write_text(json.dumps(data), encoding="utf-8")


class TestProjectSettingsPluginConfigs:
    """CC v2.1.207: only user / --settings / managed settings are honored."""

    def test_settings_json_plugin_configs_warns(self, tmp_path: Path) -> None:
        """POSITIVE: pluginConfigs in project .claude/settings.json → WARNING."""
        _write_settings(tmp_path, "settings.json", {"pluginConfigs": {"my-plugin": {"k": "v"}}})
        report = ValidationReport()
        check_project_settings_plugin_configs(tmp_path, report)
        hits = _messages(report, PROJECT_SETTINGS_CODE, "WARNING")
        assert len(hits) == 1, f"project-level pluginConfigs must warn; got {hits}"
        assert "settings.json" in hits[0]

    def test_settings_local_json_plugin_configs_warns(self, tmp_path: Path) -> None:
        """POSITIVE: settings.local.json is the same project scope — equally ignored."""
        _write_settings(tmp_path, "settings.local.json", {"pluginConfigs": {"p": {}}})
        report = ValidationReport()
        check_project_settings_plugin_configs(tmp_path, report)
        assert len(_messages(report, PROJECT_SETTINGS_CODE, "WARNING")) == 1

    def test_warning_never_blocks_strict(self, tmp_path: Path) -> None:
        """The finding is ADVISORY: it must never reach a --strict-blocking severity."""
        _write_settings(tmp_path, "settings.json", {"pluginConfigs": {"p": {}}})
        report = ValidationReport()
        check_project_settings_plugin_configs(tmp_path, report)
        assert _blocking(report) == [], "a dead setting is not a broken plugin — WARN only"

    def test_settings_without_plugin_configs_is_silent(self, tmp_path: Path) -> None:
        """NEGATIVE: ordinary project settings draw nothing."""
        _write_settings(tmp_path, "settings.json", {"permissions": {"allow": ["Bash"]}})
        report = ValidationReport()
        check_project_settings_plugin_configs(tmp_path, report)
        assert _messages(report, PROJECT_SETTINGS_CODE) == []

    def test_no_claude_dir_is_silent(self, tmp_path: Path) -> None:
        """NEGATIVE: a plugin with no .claude/ dir costs nothing and reports nothing."""
        report = ValidationReport()
        check_project_settings_plugin_configs(tmp_path, report)
        assert report.results == []

    def test_malformed_settings_is_silent_here(self, tmp_path: Path) -> None:
        """NEGATIVE: a broken settings file is the JSON validator's finding, not ours —
        never guess at the contents of unparseable JSON."""
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text("{ not json", encoding="utf-8")
        report = ValidationReport()
        check_project_settings_plugin_configs(tmp_path, report)
        assert _messages(report, PROJECT_SETTINGS_CODE) == []


# ---------------------------------------------------------------------------
# Template sweep — the canon scaffold must never TEACH the rejected shape.
# ---------------------------------------------------------------------------


class TestCanonTemplatesEmitNoShellFormToken:
    """A generated plugin must not ship the shape CC v2.1.207 rejects."""

    def test_generated_repo_has_no_user_config_interpolation(self, tmp_path: Path) -> None:
        """REGRESSION LOCK: no file the canon generator emits carries a
        `${user_config.*}` token. The scaffold is the shape every downstream plugin
        inherits, so a token here would propagate the vulnerable form fleet-wide."""
        from generate_plugin_repo import PluginParams, generate_plugin_repo

        params = PluginParams(
            name="sample-plugin",
            description="x",
            author="A",
            author_email="a@a.a",
            github_owner="Emasoft",
        )
        generate_plugin_repo(tmp_path, params)

        offenders: list[str] = []
        for path in tmp_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if find_user_config_interpolations(text):
                offenders.append(str(path.relative_to(tmp_path)))
        assert offenders == [], (
            f"canon templates must not emit a ${{user_config.*}} interpolation: {offenders}"
        )
