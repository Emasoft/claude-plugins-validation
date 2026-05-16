#!/usr/bin/env python3
"""Tests for the Claude Code v2.1.143 changelog catch-up (TRDD-ebc745b5).

Four changelog items overlap with the plugin validator's surface area:

* Item A — hook field ``terminalSequence`` for desktop notifications,
  window titles, and bells without a controlling terminal. ALREADY
  enforced by CPV v2.84.0 (``UNIVERSAL_OUTPUT_FIELDS`` in
  ``scripts/validate_hook_output.py``). The test here is a regression
  lock so the field cannot silently disappear from the universal
  allow-list.
* Item B — env var
  ``CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY=1`` opts out of
  PowerShell's ``-ExecutionPolicy Bypass`` default. CPV must recognise
  the name so plugin docs/settings referencing it do not produce
  false-positive "unknown env var" findings.
* Item C — env var ``CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`` overrides the
  default cap of 8 consecutive hook blocks before a turn ends with a
  warning. Same recognition requirement as Item B.
* Item D — settings key ``worktree.bgIsolation`` (initially: enum
  ``"none"``) lets background sessions edit the working copy directly
  without ``EnterWorktree``. The top-level ``worktree`` key was already
  allow-listed in CPV; this test pins the documented sub-key set
  (``sparsePaths``, ``baseRef``, ``bgIsolation``) and proves a settings
  file using the new sub-key validates cleanly.

The other items in the v2.1.143 changelog (PowerShell default change,
``claude agents`` flags, ``/bg`` flag preservation, ``/goal``
behavioural fixes, daemon-fallback fixes) are CLI behaviours with no
plugin-validator footprint.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    VALID_PLUGIN_ENV_VARS,
    ValidationReport,
    is_valid_plugin_env_var,
)


class TestEnvVarsRegistered:
    """Item B + Item C — new env vars must be recognised."""

    def test_powershell_respect_execution_policy_recognised(self) -> None:
        """``CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY`` is documented in
        the v2.1.143 changelog — plugin docs that reference it must not
        produce false-positive unknown-env-var findings."""
        assert "CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY" in VALID_PLUGIN_ENV_VARS
        assert is_valid_plugin_env_var("CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY")

    def test_stop_hook_block_cap_recognised(self) -> None:
        """``CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`` — same recognition rule."""
        assert "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP" in VALID_PLUGIN_ENV_VARS
        assert is_valid_plugin_env_var("CLAUDE_CODE_STOP_HOOK_BLOCK_CAP")

    def test_unrelated_env_var_still_unknown(self) -> None:
        """Sanity check: random non-CC env var must NOT be recognised, so the
        positive checks above are not vacuously passing."""
        assert not is_valid_plugin_env_var("CLAUDE_CODE_THIS_DOES_NOT_EXIST_2026")


class TestTerminalSequenceUniversalHookField:
    """Item A — regression lock on the v2.84.0 universal hook-output field."""

    def test_terminal_sequence_in_universal_output_fields(self) -> None:
        """``terminalSequence`` must remain in ``UNIVERSAL_OUTPUT_FIELDS`` so
        every hook event accepts it without a per-event allow-list edit."""
        from validate_hook_output import UNIVERSAL_OUTPUT_FIELDS  # noqa: PLC0415

        assert "terminalSequence" in UNIVERSAL_OUTPUT_FIELDS, (
            "terminalSequence regressed out of the universal hook-output "
            "field set. v2.1.143 reaffirms it as a top-level hook output "
            "field — re-add it to UNIVERSAL_OUTPUT_FIELDS in "
            "scripts/validate_hook_output.py."
        )

    def test_terminal_sequence_must_be_string(self) -> None:
        """A non-string ``terminalSequence`` value must surface as MAJOR (the
        runtime writes the string straight to the terminal, so a non-string
        would crash the hook channel)."""
        from validate_hook_output import validate_output_payload  # noqa: PLC0415

        bad_payload = {"terminalSequence": 12345, "continue": True}
        report = validate_output_payload("Stop", bad_payload)
        bad_msgs = [r.message for r in report.results if r.level == "MAJOR" and "terminalSequence" in r.message]
        assert bad_msgs, (
            f"Expected a 'terminalSequence' MAJOR finding, got results: "
            f"{[(r.level, r.message) for r in report.results]}"
        )

    def test_terminal_sequence_string_passes(self) -> None:
        """A well-formed ``terminalSequence`` string must NOT raise a MAJOR/CRITICAL finding."""
        from validate_hook_output import validate_output_payload  # noqa: PLC0415

        good_payload = {"terminalSequence": "\x1b]0;custom title\x07", "continue": True}
        report = validate_output_payload("Stop", good_payload)
        for result in report.results:
            if result.level in ("MAJOR", "CRITICAL"):
                assert "terminalSequence" not in result.message, (
                    f"terminalSequence string should be accepted, but got: {result.message}"
                )


class TestWorktreeBgIsolationSubKey:
    """Item D — ``worktree.bgIsolation`` (new sub-key) round-trips."""

    def test_worktree_top_level_key_allowlisted(self) -> None:
        """``worktree`` itself must remain in ``KNOWN_SETTINGS_KEYS`` so the
        sub-key check below stays meaningful (if the parent key were
        removed, the sub-key would never be reached at runtime)."""
        from cc_scope_rules import KNOWN_SETTINGS_KEYS  # noqa: PLC0415

        assert "worktree" in KNOWN_SETTINGS_KEYS

    def test_worktree_sub_keys_documented_in_comment(self) -> None:
        """The ``worktree`` comment in cc_scope_rules.py must enumerate the
        documented sub-keys (``sparsePaths``, ``baseRef``, ``bgIsolation``).
        This is a documentation pin — future changelog catch-ups should
        keep the comment in sync."""
        scope_rules_src = (Path(__file__).parent.parent / "scripts" / "cc_scope_rules.py").read_text(encoding="utf-8")
        worktree_line = next(
            (line for line in scope_rules_src.splitlines() if '"worktree"' in line and "v2.1.76" in line),
            None,
        )
        assert worktree_line is not None, (
            "Could not find the worktree allow-list entry in cc_scope_rules.py. "
            "Expected a line like:\n"
            '        "worktree",  # v2.1.76 — top-level object (sub-keys: sparsePaths, baseRef v2.1.133, bgIsolation v2.1.143)'
        )
        for sub_key in ("sparsePaths", "baseRef", "bgIsolation"):
            assert sub_key in worktree_line, (
                f"worktree comment must mention sub-key {sub_key!r} so future "
                f"changelog catch-ups know the supported sub-key set. Current "
                f"comment: {worktree_line.strip()}"
            )

    def test_settings_with_bg_isolation_validates_clean(self, tmp_path: Path) -> None:
        """A ``settings.local.json`` carrying ``worktree.bgIsolation: "none"``
        must NOT produce any CRITICAL/MAJOR/MINOR finding from the local-scope
        validator (the key is documented in v2.1.143)."""
        from validate_local_scope import validate_settings_local_json  # noqa: PLC0415

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_path = claude_dir / "settings.local.json"
        settings_payload = {
            "worktree": {
                "bgIsolation": "none",
                "baseRef": "head",
                "sparsePaths": ["src/", "tests/"],
            },
        }
        settings_path.write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")

        report = ValidationReport()
        result = validate_settings_local_json(settings_path, report)

        assert isinstance(result, dict), "Settings file must parse as JSON object"
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert not critical_msgs, f"Unexpected CRITICAL findings: {critical_msgs}"
        assert not major_msgs, f"Unexpected MAJOR findings: {major_msgs}"
        assert not minor_msgs, f"Unexpected MINOR findings: {minor_msgs}"


class TestTrddCrossReference:
    """Sanity — the TRDD that motivates this test file must exist."""

    def test_trdd_file_exists(self) -> None:
        trdd = (
            Path(__file__).parent.parent
            / "design"
            / "tasks"
            / "TRDD-ebc745b5-5563-44af-860d-5e2f5e002f46-cc-changelog-v2_1_143.md"
        )
        assert trdd.is_file(), f"Expected TRDD at {trdd}"
        body = trdd.read_text(encoding="utf-8")
        assert "TRDD-ebc745b5" in body
        assert "v2.1.143" in body
