#!/usr/bin/env python3
"""Tests for the hook-command cross-platform rules added in v2.65.2.

Covers three new validations:

1. ``check_hook_command_cross_platform`` — bash-only constructs trigger MAJOR
   (set -euo pipefail, ``[[ ]]``, ``$(<file)``, process substitution,
   brace expansion).
2. POSIX-only tools (``jq``, ``sed``, ``awk``, ``shellcheck``) used directly
   in hook commands → MINOR (unless wrapped in ``python3 -c`` / ``bash -c``).
3. The same checks fire when hooks are defined in agent / skill frontmatter,
   not just in ``hooks/hooks.json`` — the function is shared across both
   validation paths.

The rule that hook commands must NOT write to ``${CLAUDE_PLUGIN_ROOT}`` is
covered by ``test_hook_writes_to_plugin_root.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import (  # noqa: E402
    check_hook_command_cross_platform,
    validate_command_hook,
)


def _has_major(report: ValidationReport, fragment: str) -> bool:
    return any(r.level == "MAJOR" and fragment in r.message for r in report.results)


def _has_minor(report: ValidationReport, fragment: str) -> bool:
    return any(r.level == "MINOR" and fragment in r.message for r in report.results)


# ---------------------------------------------------------------------------
# Bash-only constructs → MAJOR
# ---------------------------------------------------------------------------


class TestBashOnlyConstructs:
    def test_set_euo_pipefail_major(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform("set -euo pipefail; echo ok", report)
        assert _has_major(report, "bash-only constructs")

    def test_set_e_alone_major(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform("set -e ; do_things", report)
        assert _has_major(report, "bash-only constructs")

    def test_double_bracket_major(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform('[[ -z "$X" ]] && echo empty', report)
        assert _has_major(report, "bash-only constructs")

    def test_dollar_lt_file_major(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform('VERSION=$(<VERSION.txt)', report)
        assert _has_major(report, "bash-only constructs")

    def test_process_substitution_major(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform("diff <(cat a) <(cat b)", report)
        assert _has_major(report, "bash-only constructs")

    def test_brace_expansion_major(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform("rm -rf out/{tmp,build,dist}", report)
        assert _has_major(report, "bash-only constructs")

    def test_posix_command_no_finding(self) -> None:
        """POSIX-portable commands must not trigger the bash-only rule."""
        report = ValidationReport()
        check_hook_command_cross_platform(
            'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py"', report
        )
        assert not _has_major(report, "bash-only constructs")

    def test_arithmetic_no_finding(self) -> None:
        """``$((...))`` IS POSIX-portable and must not be flagged."""
        report = ValidationReport()
        check_hook_command_cross_platform('count=$((count + 1)); echo $count', report)
        assert not _has_major(report, "bash-only constructs")


# ---------------------------------------------------------------------------
# POSIX-only tools → MINOR (unless wrapped)
# ---------------------------------------------------------------------------


class TestPosixOnlyTools:
    def test_jq_direct_minor(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform("gh api repos/X | jq '.name'", report)
        assert _has_minor(report, "POSIX-only tool 'jq'")

    def test_sed_direct_minor(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform("sed -i 's/foo/bar/' file.txt", report)
        assert _has_minor(report, "POSIX-only tool 'sed'")

    def test_awk_direct_minor(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform("awk '{print $1}' input.txt", report)
        assert _has_minor(report, "POSIX-only tool 'awk'")

    def test_shellcheck_direct_minor(self) -> None:
        report = ValidationReport()
        check_hook_command_cross_platform("shellcheck scripts/hook.sh", report)
        assert _has_minor(report, "POSIX-only tool 'shellcheck'")

    def test_python_c_wrapper_no_finding(self) -> None:
        """``python3 -c "..."`` opt-out — the user has chosen the platform."""
        report = ValidationReport()
        check_hook_command_cross_platform(
            'python3 -c "import json; print(json.loads(open(\\"x.json\\").read())[\\"name\\"])"',
            report,
        )
        assert not _has_minor(report, "POSIX-only tool")

    def test_bash_c_wrapper_no_finding(self) -> None:
        """``bash -c "..."`` opt-out — explicit shell decision."""
        report = ValidationReport()
        check_hook_command_cross_platform(
            'bash -c "jq .name file.json"', report
        )
        assert not _has_minor(report, "POSIX-only tool")

    def test_wsl_wrapper_no_finding(self) -> None:
        """``wsl bash -c "..."`` opt-out — Windows-aware shell decision."""
        report = ValidationReport()
        check_hook_command_cross_platform(
            'wsl bash -c "sed -i s/x/y/ file"', report
        )
        assert not _has_minor(report, "POSIX-only tool")


# ---------------------------------------------------------------------------
# Inline-hook integration (hooks.json) — the same rules fire via the full
# validate_command_hook entry point.
# ---------------------------------------------------------------------------


class TestInlineHookIntegration:
    def test_inline_hook_bash_isms_major(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": "set -euo pipefail; do_thing"},
            "PostToolUse",
            None,
            report,
        )
        assert _has_major(report, "bash-only constructs")

    def test_inline_hook_jq_minor(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": "gh api X | jq .name"},
            "PostToolUse",
            None,
            report,
        )
        assert _has_minor(report, "POSIX-only tool 'jq'")


# ---------------------------------------------------------------------------
# Frontmatter-hook integration: agent + skill frontmatter command-type hooks
# go through check_hook_command_cross_platform too (added in v2.65.2).
# ---------------------------------------------------------------------------


class TestAgentFrontmatterHookIntegration:
    def test_agent_frontmatter_hook_bash_isms_caught(self) -> None:
        """Agent frontmatter hooks with bash-only commands must surface MAJOR."""
        from validate_agent import (  # noqa: PLC0415
            AgentValidationReport,
            validate_hooks_field,
        )

        frontmatter = {
            "hooks": {
                "PostToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "set -e; do_x",
                            }
                        ]
                    }
                ]
            }
        }
        report = AgentValidationReport()
        validate_hooks_field(frontmatter, "agents/foo.md", report)
        assert any(
            r.level == "MAJOR" and "bash-only constructs" in r.message
            for r in report.results
        )

    def test_agent_frontmatter_hook_writes_plugin_root_critical(self) -> None:
        """Agent frontmatter command writing to PLUGIN_ROOT → CRITICAL."""
        from validate_agent import (  # noqa: PLC0415
            AgentValidationReport,
            validate_hooks_field,
        )

        frontmatter = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'echo X > "${CLAUDE_PLUGIN_ROOT}/state.txt"',
                            }
                        ]
                    }
                ]
            }
        }
        report = AgentValidationReport()
        validate_hooks_field(frontmatter, "agents/foo.md", report)
        assert any(
            r.level == "CRITICAL" and "CLAUDE_PLUGIN_ROOT" in r.message and "REPLACED" in r.message
            for r in report.results
        )


class TestSkillFrontmatterHookIntegration:
    def test_skill_frontmatter_hook_bash_isms_caught(self) -> None:
        from validate_skill import validate_hooks_field  # noqa: PLC0415

        frontmatter = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "[[ -f /tmp/x ]] && echo ok",
                            }
                        ]
                    }
                ]
            }
        }
        report = ValidationReport()
        validate_hooks_field(frontmatter, report)
        assert _has_major(report, "bash-only constructs")
