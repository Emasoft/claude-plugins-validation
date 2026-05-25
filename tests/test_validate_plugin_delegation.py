#!/usr/bin/env python3
"""Delegation-proof tests for validate_plugin.py (TRDD-021250b5 Phase 3).

validate_plugin used to UNDER-validate several component types by running thin
inline duplicates of the comprehensive standalone validators. Phase 3 made it
DELEGATE to the comprehensive validators (single source of truth). These tests
prove the hole is closed: each plants a fault that ONLY the comprehensive
validator catches (the old thin inline path missed it), then asserts the
whole-plugin delegation wrapper now surfaces it.

- agents   -> validate_agents -> validate_agent (comprehensive): token-budget on
             the description (the thin inline only checked description-EXISTS)
- commands -> validate_commands -> validate_command (comprehensive): same
- encoding -> validate_encoding -> validate_encoding (whole-plugin): the inline
             path NEVER ran encoding at all (files were read errors="replace")
- lsp      -> validate_lsp -> validate_plugin_lsp: per-field type checks on
             INLINE plugin.json:lspServers entries (the comprehensive validator
             previously only tracked their names; restored in Phase 3)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import (  # noqa: E402
    validate_agents,
    validate_commands,
    validate_encoding,
    validate_hooks,
    validate_lsp,
    validate_manifest,
    validate_readme,
)


def _make_plugin(tmp_path: Path) -> Path:
    """Create a minimal valid plugin root with a plugin.json manifest."""
    root = tmp_path / "p"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "version": "1.0.0", "description": "x"}),
        encoding="utf-8",
    )
    return root


class TestAgentDelegation:
    """validate_agents delegates to the comprehensive agent validator."""

    def test_over_token_agent_description_is_caught(self, tmp_path):
        """An agent description over the 300-token limit is flagged MAJOR.

        The thin inline agent validator only checked that a description EXISTS;
        the token-budget check is comprehensive-only, so a MAJOR here proves the
        whole-plugin path now runs the comprehensive agent validator.
        """
        root = _make_plugin(tmp_path)
        (root / "agents").mkdir()
        long_desc = " ".join(f"word{i}" for i in range(500))  # ~1300 tokens
        (root / "agents" / "big.md").write_text(
            f"---\nname: big\ndescription: {long_desc}\n---\n\nYou are a helper agent.\n",
            encoding="utf-8",
        )
        report = ValidationReport()
        validate_agents(root, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("description" in m and "token" in m.lower() for m in majors), (
            f"expected a token-budget MAJOR on the agent description; got: {majors}"
        )

    def test_valid_agent_description_passes(self, tmp_path):
        """A short agent description produces no token-budget MAJOR (two-sided)."""
        root = _make_plugin(tmp_path)
        (root / "agents").mkdir()
        (root / "agents" / "ok.md").write_text(
            "---\nname: ok\ndescription: Use this agent to greet users politely.\n---\n\nYou are a greeter.\n",
            encoding="utf-8",
        )
        report = ValidationReport()
        validate_agents(root, report)
        token_majors = [
            r.message for r in report.results if r.level == "MAJOR" and "token" in r.message.lower()
        ]
        assert not token_majors, f"unexpected token MAJOR on a valid agent: {token_majors}"


class TestCommandDelegation:
    """validate_commands delegates to the comprehensive command validator."""

    def test_over_token_command_description_is_caught(self, tmp_path):
        """A command description over the 200-token limit is flagged MAJOR.

        The thin inline command validator only checked description-EXISTS; the
        200-token budget is comprehensive-only, so a MAJOR here proves delegation.
        """
        root = _make_plugin(tmp_path)
        (root / "commands").mkdir()
        long_desc = " ".join(f"word{i}" for i in range(400))  # ~1000 tokens
        (root / "commands" / "big.md").write_text(
            f"---\nname: big\ndescription: {long_desc}\n---\n\n# Big\n",
            encoding="utf-8",
        )
        report = ValidationReport()
        validate_commands(root, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("description" in m and "token" in m.lower() for m in majors), (
            f"expected a token-budget MAJOR on the command description; got: {majors}"
        )


class TestEncodingDelegation:
    """validate_encoding runs at all — the inline path never did (total gap)."""

    def test_invalid_utf8_file_is_caught(self, tmp_path):
        """A non-UTF-8 file anywhere in the plugin is flagged CRITICAL.

        The pre-Phase-3 whole-plugin path read files with errors="replace" and
        NEVER ran the encoding validator, so invalid bytes were silently hidden.
        A CRITICAL here proves the encoding validator now runs via delegation.
        """
        root = _make_plugin(tmp_path)
        skill = root / "skills" / "s"
        skill.mkdir(parents=True)
        # Valid frontmatter, but an invalid UTF-8 byte (0xff) in the body.
        skill.joinpath("SKILL.md").write_bytes(
            b"---\nname: s\ndescription: ok\n---\n\nBody with a bad byte: \xff here\n"
        )
        report = ValidationReport()
        validate_encoding(root, report)
        criticals = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("UTF-8" in m for m in criticals), (
            f"expected a non-UTF-8 CRITICAL from the encoding validator; got: {criticals}"
        )

    def test_clean_utf8_plugin_passes(self, tmp_path):
        """A clean UTF-8 plugin produces no encoding CRITICAL (two-sided)."""
        root = _make_plugin(tmp_path)
        skill = root / "skills" / "s"
        skill.mkdir(parents=True)
        skill.joinpath("SKILL.md").write_text(
            "---\nname: s\ndescription: ok\n---\n\nClean ASCII body.\n", encoding="utf-8"
        )
        report = ValidationReport()
        validate_encoding(root, report)
        enc_criticals = [r.message for r in report.results if r.level == "CRITICAL" and "UTF-8" in r.message]
        assert not enc_criticals, f"unexpected encoding CRITICAL on a clean plugin: {enc_criticals}"


class TestLspInlineDelegation:
    """validate_lsp validates INLINE plugin.json:lspServers configs (regression).

    Phase 3 removed the inline LSP type-checks from validate_manifest; the
    comprehensive validator initially only tracked inline server NAMES (for
    collision detection) and skipped their field validation. This proves the
    per-field validation of inline configs is restored.
    """

    def test_inline_lsp_bad_args_type_is_caught(self, tmp_path):
        """An inline lspServers entry with a non-array `args` is flagged MAJOR."""
        root = tmp_path / "p"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "p",
                    "version": "1.0.0",
                    "description": "x",
                    "lspServers": {
                        "pyright": {
                            "command": "pyright-langserver",
                            "extensionToLanguage": {".py": "python"},
                            "args": "--stdio",  # should be a list
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        report = ValidationReport()
        validate_lsp(root, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'args' must be an array" in m for m in majors), (
            f"expected an inline-lspServers args MAJOR; got: {majors}"
        )

    def test_inline_lsp_not_double_reported_by_manifest(self, tmp_path):
        """validate_manifest emits NO LSP findings (no double-report regression).

        Phase 3 moved inline lspServers validation out of validate_manifest into
        validate_lsp. Both run in the whole-plugin scan, so if validate_manifest
        still validated lspServers the scan would double-count every LSP fault.
        This guards that validate_manifest stays out of the LSP business.
        """
        root = tmp_path / "p"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "p",
                    "version": "1.0.0",
                    "description": "x",
                    "lspServers": {"pyright": {"command": "x", "args": "bad"}},
                }
            ),
            encoding="utf-8",
        )
        report = ValidationReport()
        validate_manifest(root, report)
        lsp_findings = [
            r.message
            for r in report.results
            if r.level in ("CRITICAL", "MAJOR", "MINOR") and "'args'" in r.message
        ]
        assert not lsp_findings, f"validate_manifest should not validate LSP; got: {lsp_findings}"


class TestHookDelegation:
    """validate_hooks delegates to the comprehensive hooks.json validator."""

    def test_malformed_hooks_json_is_caught(self, tmp_path):
        """A structurally-invalid hooks.json is flagged.

        The whole-plugin path delegates to the comprehensive hook validator,
        which checks the hooks.json schema (the inline path never did a deep
        structural check). A finding here proves the comprehensive validator runs.
        """
        root = _make_plugin(tmp_path)
        (root / "hooks").mkdir()
        # Missing the required top-level "hooks" wrapper object.
        (root / "hooks" / "hooks.json").write_text(
            json.dumps({"NotARealEvent": [{"matcher": "*", "hooks": [{"type": "command"}]}]}),
            encoding="utf-8",
        )
        report = ValidationReport()
        validate_hooks(root, report)
        blocking = [r.message for r in report.results if r.level in ("CRITICAL", "MAJOR")]
        assert blocking, "expected the comprehensive hook validator to flag a malformed hooks.json"


class TestDocumentationDelegation:
    """validate_readme delegates to the comprehensive documentation validator."""

    def test_broken_internal_link_is_caught(self, tmp_path):
        """A broken internal link in the README is flagged MAJOR.

        The thin inline validate_readme only checked README EXISTENCE; broken-
        link scanning is comprehensive-only, so a MAJOR here proves the
        whole-plugin path now runs the comprehensive documentation validator.
        """
        root = _make_plugin(tmp_path)
        (root / "README.md").write_text(
            "# P\n\nDesc.\n\n## Usage\n\nSee [the guide](docs/does-not-exist.md).\n",
            encoding="utf-8",
        )
        report = ValidationReport()
        validate_readme(root, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Broken internal link" in m for m in majors), (
            f"expected a broken-internal-link MAJOR from the doc validator; got: {majors}"
        )
