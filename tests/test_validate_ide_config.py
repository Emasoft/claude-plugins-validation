"""Tests for validate_ide_config — NIT-level env-var-reference checks
covering IDE configuration files (.vscode/, .idea/, .cursor/, .zed/).

The CRITICAL-severity secret-leak detection lives in
``validate_security.scan_ide_config_files`` (and its tests live in
``test_validate_security.TestScanIdeConfigFiles``). This validator is
the SIBLING that adds the NIT-level "env-var name looks suspiciously
like a secret name" warnings the TRDD-8ccb9337 spec calls out under
"Additional: warn on .env in IDE configs".

Each rule has positive (fires) and negative (does NOT fire) tests so a
future regex tweak that silently weakens the detector is caught.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_ide_config import (  # noqa: E402
    IDE_CONFIG_PATHS,
    SECRET_LIKE_ENV_NAME,
    is_secret_like_env_name,
    scan_ide_config_for_env_refs,
    scan_plugin_for_ide_config_hygiene,
)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _make_plugin(tmp_path: Path, name: str = "test-plugin") -> Path:
    """Create a minimal plugin scaffold (`.claude-plugin/plugin.json`)."""
    plugin = tmp_path / name
    plugin.mkdir()
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0", "description": "x"})
    )
    return plugin


# -----------------------------------------------------------------------------
# Spec-level invariants
# -----------------------------------------------------------------------------


class TestSpec:
    """Module-level invariants the validator must uphold."""

    def test_ide_config_paths_match_trdd_spec(self) -> None:
        """The validator's IDE_CONFIG_PATHS list must match the TRDD-8ccb9337 spec."""
        expected = {
            ".vscode/settings.json",
            ".vscode/tasks.json",
            ".vscode/launch.json",
            ".idea/workspace.xml",
            ".idea/*.xml",
            ".cursor/mcp.json",
            ".cursor/settings.json",
            ".zed/settings.json",
            ".zed/tasks.json",
        }
        assert set(IDE_CONFIG_PATHS) == expected

    def test_secret_like_env_name_compiled(self) -> None:
        """The shared regex must be a compiled re.Pattern for cheap reuse."""
        import re as _re

        assert isinstance(SECRET_LIKE_ENV_NAME, _re.Pattern)


# -----------------------------------------------------------------------------
# is_secret_like_env_name — predicate around the SECRET_LIKE_ENV_NAME regex
# -----------------------------------------------------------------------------


class TestIsSecretLikeEnvName:
    """The predicate that classifies env-var NAMES as 'secret-like'."""

    def test_api_key_suffix_matches(self) -> None:
        """`OPENAI_API_KEY` is the canonical secret-like env-var name."""
        assert is_secret_like_env_name("OPENAI_API_KEY")

    def test_token_suffix_matches(self) -> None:
        """`GITHUB_TOKEN` is a secret-like env-var name."""
        assert is_secret_like_env_name("GITHUB_TOKEN")

    def test_secret_suffix_matches(self) -> None:
        """`AWS_SECRET_ACCESS_KEY` is a secret-like env-var name."""
        assert is_secret_like_env_name("AWS_SECRET_ACCESS_KEY")

    def test_password_suffix_matches(self) -> None:
        """`DB_PASSWORD` is a secret-like env-var name."""
        assert is_secret_like_env_name("DB_PASSWORD")

    def test_passphrase_matches(self) -> None:
        """`SSH_PASSPHRASE` should match — passphrase is secret-like."""
        assert is_secret_like_env_name("SSH_PASSPHRASE")

    def test_credentials_matches(self) -> None:
        """`AWS_CREDENTIALS` is a secret-like env-var name."""
        assert is_secret_like_env_name("AWS_CREDENTIALS")

    def test_generic_path_var_does_not_match(self) -> None:
        """`PATH` is not a credential — must not match."""
        assert not is_secret_like_env_name("PATH")

    def test_node_options_does_not_match(self) -> None:
        """`NODE_OPTIONS` is a runtime config var — must not match."""
        assert not is_secret_like_env_name("NODE_OPTIONS")

    def test_lower_case_normalized(self) -> None:
        """Predicate must be case-insensitive — `api_key` should match."""
        assert is_secret_like_env_name("api_key")

    def test_empty_string_does_not_match(self) -> None:
        """Empty string is never a credential name."""
        assert not is_secret_like_env_name("")


# -----------------------------------------------------------------------------
# scan_ide_config_for_env_refs — single-file NIT scanner
# -----------------------------------------------------------------------------


class TestScanSingleFile:
    """Single-file scanner that emits NIT findings on env-var references with secret-like names."""

    def test_envvar_reference_with_secret_like_name_emits_nit(self, tmp_path: Path) -> None:
        """`"OPENAI_API_KEY": "${OPENAI_API_KEY}"` is a textbook safe pattern, but
        worth flagging at NIT so a reviewer can confirm the env var is actually
        populated outside the repo."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "settings.json").write_text(
            '{\n  "terminal.integrated.env.osx": {\n    "OPENAI_API_KEY": "${env:OPENAI_API_KEY}"\n  }\n}\n'
        )

        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "settings.json", report, plugin)
        assert nit_count >= 1
        nits = [r for r in report.results if r.level == "NIT"]
        assert any("OPENAI_API_KEY" in r.message for r in nits)
        assert any(".vscode/settings.json" in (r.file or "") for r in nits)

    def test_dotenv_reference_emits_nit(self, tmp_path: Path) -> None:
        """An IDE config that references a `.env` file gets a NIT — reviewers
        should confirm the .env is actually gitignored on the user's side."""
        plugin = _make_plugin(tmp_path)
        cursor = plugin / ".cursor"
        cursor.mkdir()
        (cursor / "mcp.json").write_text(
            '{\n  "mcpServers": {\n    "x": {\n'
            '      "command": "node",\n'
            '      "args": ["server.js"],\n'
            '      "envFile": ".env"\n'
            "    }\n  }\n}\n"
        )

        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".cursor" / "mcp.json", report, plugin)
        assert nit_count >= 1
        nits = [r for r in report.results if r.level == "NIT"]
        assert any(".env" in r.message for r in nits)

    def test_clean_config_emits_no_nit(self, tmp_path: Path) -> None:
        """A config that uses no secret-like env vars must not emit any NIT."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "tasks.json").write_text(
            '{\n  "version": "2.0.0",\n  "tasks": [\n    {\n'
            '      "label": "build", "type": "shell", "command": "make"\n'
            "    }\n  ]\n}\n"
        )

        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "tasks.json", report, plugin)
        assert nit_count == 0
        nits = [r for r in report.results if r.level == "NIT"]
        assert nits == []

    def test_env_var_reference_with_benign_name_emits_no_nit(self, tmp_path: Path) -> None:
        """`${env:NODE_ENV}` is a runtime config var — must not emit NIT."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "settings.json").write_text(
            '{\n  "terminal.integrated.env.osx": {\n    "NODE_ENV": "${env:NODE_ENV}"\n  }\n}\n'
        )

        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "settings.json", report, plugin)
        assert nit_count == 0

    def test_missing_file_returns_zero(self, tmp_path: Path) -> None:
        """Scanning a non-existent file is a no-op (returns 0, no exceptions)."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "missing.json", report, plugin)
        assert nit_count == 0
        assert report.results == []

    def test_dotenv_path_in_value_emits_nit(self, tmp_path: Path) -> None:
        """A `.env` reference inside a path value (e.g. `path/to/.env`) emits NIT once."""
        plugin = _make_plugin(tmp_path)
        zed = plugin / ".zed"
        zed.mkdir()
        (zed / "settings.json").write_text(
            '{\n  "language_servers": {\n    "rust-analyzer": {\n'
            '      "settings": { "envFile": "config/.env" }\n'
            "    }\n  }\n}\n"
        )
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".zed" / "settings.json", report, plugin)
        assert nit_count >= 1

    def test_duplicate_secret_envvar_in_same_file_emits_one_nit(self, tmp_path: Path) -> None:
        """Same env-var name appearing on multiple lines emits exactly ONE NIT."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        # Two references to the same name on different lines
        (vscode / "settings.json").write_text(
            '{\n  "macos_env": { "OPENAI_API_KEY": "${env:OPENAI_API_KEY}" },\n'
            '  "linux_env": { "OPENAI_API_KEY": "${OPENAI_API_KEY}" }\n}\n'
        )
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "settings.json", report, plugin)
        # Dedupe should keep this at exactly 1 NIT for OPENAI_API_KEY
        api_key_nits = [r for r in report.results if "OPENAI_API_KEY" in r.message]
        assert len(api_key_nits) == 1, f"Expected 1 NIT, got {len(api_key_nits)}: {api_key_nits}"
        assert nit_count == 1

    def test_multiple_distinct_secret_envvars_each_get_one_nit(self, tmp_path: Path) -> None:
        """Distinct env-var names each produce their own NIT."""
        plugin = _make_plugin(tmp_path)
        cursor = plugin / ".cursor"
        cursor.mkdir()
        (cursor / "mcp.json").write_text(
            '{\n  "env": {\n'
            '    "OPENAI_API_KEY": "${OPENAI_API_KEY}",\n'
            '    "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",\n'
            '    "GITHUB_TOKEN": "${GITHUB_TOKEN}"\n'
            "  }\n}\n"
        )
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".cursor" / "mcp.json", report, plugin)
        nits = [r for r in report.results if r.level == "NIT"]
        names_flagged = {
            n for n in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN"] if any(n in r.message for r in nits)
        }
        assert names_flagged == {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN"}
        assert nit_count == 3

    def test_keystore_path_substring_does_not_match_key(self, tmp_path: Path) -> None:
        """Names like `KEYSTORE_PATH`, `KEYBOARD_LAYOUT` must NOT trip the secret-like predicate."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "settings.json").write_text(
            '{\n  "java.configuration.maven.userSettings": "${KEYSTORE_PATH}",\n'
            '  "editor.keyboard.layout": "${KEYBOARD_LAYOUT}"\n}\n'
        )
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "settings.json", report, plugin)
        # KEYSTORE_PATH and KEYBOARD_LAYOUT are NOT secret-like — must not match
        assert nit_count == 0

    def test_dotenv_inside_arbitrary_word_does_not_match(self, tmp_path: Path) -> None:
        """`environment` and `myproject.env.example` are not `.env` refs."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "settings.json").write_text('{\n  "comment": "Use environment variables for secrets"\n}\n')
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "settings.json", report, plugin)
        # `environment` is a word — must not match the .env reference regex
        dotenv_nits = [r for r in report.results if ".env" in r.message]
        assert dotenv_nits == [], f"`environment` substring must not match: {dotenv_nits}"
        assert nit_count == 0

    def test_dotenv_local_variant_emits_nit(self, tmp_path: Path) -> None:
        """`.env.local`, `.env.production` etc are also flagged."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "settings.json").write_text('{\n  "env": { "envFile": ".env.local" }\n}\n')
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "settings.json", report, plugin)
        assert nit_count >= 1
        nits = [r for r in report.results if r.level == "NIT"]
        assert any(".env" in r.message for r in nits)

    def test_jetbrains_macro_form_triggers_nit(self, tmp_path: Path) -> None:
        """JetBrains-style `$NAME$` macro references are caught."""
        plugin = _make_plugin(tmp_path)
        idea = plugin / ".idea"
        idea.mkdir()
        (idea / "workspace.xml").write_text(
            '<?xml version="1.0"?>\n<project>\n  <env name="OPENAI_API_KEY" value="$OPENAI_API_KEY$" />\n</project>\n'
        )
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".idea" / "workspace.xml", report, plugin)
        assert nit_count >= 1
        nits = [r for r in report.results if r.level == "NIT"]
        assert any("OPENAI_API_KEY" in r.message for r in nits)

    def test_windows_percent_form_triggers_nit(self, tmp_path: Path) -> None:
        """Windows-style `%NAME%` references are caught."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "tasks.json").write_text(
            '{\n  "tasks": [{\n    "label": "deploy",\n    "command": "%API_KEY%"\n  }]\n}\n'
        )
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "tasks.json", report, plugin)
        assert nit_count >= 1
        nits = [r for r in report.results if r.level == "NIT"]
        assert any("API_KEY" in r.message for r in nits)

    def test_binary_file_is_silently_skipped(self, tmp_path: Path) -> None:
        """Binary content masquerading as JSON must not crash the scanner."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        # Write null-byte-laden binary content
        (vscode / "settings.json").write_bytes(b"\x00\x01\x02\xff" * 100)
        report = ValidationReport()
        nit_count = scan_ide_config_for_env_refs(plugin / ".vscode" / "settings.json", report, plugin)
        assert nit_count == 0
        assert [r for r in report.results if r.level == "NIT"] == []


# -----------------------------------------------------------------------------
# scan_plugin_for_ide_config_hygiene — full plugin orchestration
# -----------------------------------------------------------------------------


class TestPluginOrchestration:
    """Full plugin walk — gitignore-aware, multi-file, multi-IDE."""

    def test_clean_plugin_returns_no_findings(self, tmp_path: Path) -> None:
        """A plugin with NO IDE configs at all produces zero findings."""
        plugin = _make_plugin(tmp_path)
        report = scan_plugin_for_ide_config_hygiene(plugin)
        nits = [r for r in report.results if r.level == "NIT"]
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert nits == []
        assert criticals == []
        assert majors == []

    def test_plugin_with_secret_like_envvars_emits_nits(self, tmp_path: Path) -> None:
        """Multi-IDE config plugin produces NITs across all matching files."""
        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "settings.json").write_text(
            '{\n  "terminal.integrated.env.osx": {\n    "ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}"\n  }\n}\n'
        )

        cursor = plugin / ".cursor"
        cursor.mkdir()
        (cursor / "mcp.json").write_text(
            '{\n  "mcpServers": {\n    "p": {\n      "env": { "GITHUB_TOKEN": "${GITHUB_TOKEN}" }\n    }\n  }\n}\n'
        )

        report = scan_plugin_for_ide_config_hygiene(plugin)
        nits = [r for r in report.results if r.level == "NIT"]
        # At least one NIT per file (vscode/settings + cursor/mcp = 2 files)
        files_flagged = {r.file for r in nits if r.file}
        assert any(".vscode/settings.json" in f for f in files_flagged)
        assert any(".cursor/mcp.json" in f for f in files_flagged)

    def test_gitignored_ide_configs_are_skipped(self, tmp_path: Path) -> None:
        """If the IDE config is gitignored, the NIT scanner must not flag it."""
        plugin = _make_plugin(tmp_path)
        (plugin / ".gitignore").write_text(".vscode/\n")
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "settings.json").write_text('{\n  "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}"\n}\n')

        report = scan_plugin_for_ide_config_hygiene(plugin)
        nits = [r for r in report.results if r.level == "NIT"]
        assert nits == [], f"Gitignored IDE configs must not produce NITs, got: {nits}"

    def test_missing_plugin_root_emits_critical(self, tmp_path: Path) -> None:
        """Validator must fail-fast on a non-existent plugin path."""
        missing = tmp_path / "does-not-exist"
        report = scan_plugin_for_ide_config_hygiene(missing)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert criticals
        assert "does not exist" in criticals[0].message.lower()

    def test_plugin_path_must_be_directory(self, tmp_path: Path) -> None:
        """Validator must fail-fast on a regular-file path."""
        f = tmp_path / "regular-file.json"
        f.write_text("{}")
        report = scan_plugin_for_ide_config_hygiene(f)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert criticals
        assert "not a directory" in criticals[0].message.lower()

    def test_plugin_must_have_plugin_json(self, tmp_path: Path) -> None:
        """Validator must require .claude-plugin/plugin.json."""
        bare = tmp_path / "bare"
        bare.mkdir()
        report = scan_plugin_for_ide_config_hygiene(bare)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert criticals
        assert "plugin.json" in criticals[0].message

    def test_idea_glob_dedupes_with_workspace_xml(self, tmp_path: Path) -> None:
        """`.idea/workspace.xml` matches BOTH the literal entry AND the `.idea/*.xml` glob.
        The plugin orchestrator must scan it exactly once via dedupe."""
        plugin = _make_plugin(tmp_path)
        idea = plugin / ".idea"
        idea.mkdir()
        # Single XML file with a secret-like env-var reference
        (idea / "workspace.xml").write_text(
            '<?xml version="1.0"?>\n<project>\n  <env name="API_KEY" value="${API_KEY}" />\n</project>\n'
        )

        report = scan_plugin_for_ide_config_hygiene(plugin)
        nits = [r for r in report.results if r.level == "NIT"]
        # Only one file scanned → at most one NIT for the API_KEY env-var name
        api_key_nits = [r for r in nits if "API_KEY" in r.message]
        assert len(api_key_nits) == 1, (
            f"workspace.xml must be scanned ONCE despite literal+glob overlap, got: {api_key_nits}"
        )


# -----------------------------------------------------------------------------
# CLI entry-point smoke test
# -----------------------------------------------------------------------------


class TestCLI:
    """The CLI entry point exits with a sane code on every input."""

    def test_main_exits_zero_on_clean_plugin(self, tmp_path: Path, monkeypatch) -> None:
        """A clean plugin (no IDE configs at all) exits 0 — NIT does NOT block."""
        from validate_ide_config import main

        plugin = _make_plugin(tmp_path)
        monkeypatch.setenv("CPV_REMOTE_VALIDATION", "1")  # bypass remote-execution guard
        monkeypatch.setattr("sys.argv", ["validate_ide_config.py", str(plugin)])
        rc = main()
        assert rc == 0

    def test_main_exits_zero_on_nit_only(self, tmp_path: Path, monkeypatch) -> None:
        """NIT findings DO NOT block validation in default mode (only --strict)."""
        from validate_ide_config import main

        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "settings.json").write_text(
            '{\n  "terminal.integrated.env.osx": {\n    "OPENAI_API_KEY": "${env:OPENAI_API_KEY}"\n  }\n}\n'
        )
        monkeypatch.setenv("CPV_REMOTE_VALIDATION", "1")
        monkeypatch.setattr("sys.argv", ["validate_ide_config.py", str(plugin)])
        rc = main()
        assert rc == 0

    def test_main_strict_blocks_on_nit(self, tmp_path: Path, monkeypatch) -> None:
        """--strict flag must promote NIT findings to a non-zero exit code."""
        from validate_ide_config import main

        plugin = _make_plugin(tmp_path)
        vscode = plugin / ".vscode"
        vscode.mkdir()
        (vscode / "settings.json").write_text('{\n  "ANTHROPIC_API_KEY": "${env:ANTHROPIC_API_KEY}"\n}\n')
        monkeypatch.setenv("CPV_REMOTE_VALIDATION", "1")
        monkeypatch.setattr("sys.argv", ["validate_ide_config.py", "--strict", str(plugin)])
        rc = main()
        assert rc != 0

    def test_main_critical_when_target_missing(self, tmp_path: Path, monkeypatch) -> None:
        """A non-existent target path exits 1 (CRITICAL)."""
        from validate_ide_config import main

        monkeypatch.setenv("CPV_REMOTE_VALIDATION", "1")
        monkeypatch.setattr("sys.argv", ["validate_ide_config.py", str(tmp_path / "missing")])
        rc = main()
        assert rc == 1
