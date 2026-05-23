#!/usr/bin/env python3
"""Regression tests for CC v2.1.147 multi-Agent(...) preservation.

CC v2.1.147 changelog: "Fixed plugin agents dropping all but last Agent(...)
type in tools: frontmatter".

The CC-side bug was that when a plugin agent's ``tools:`` list contained
multiple ``Agent(...)`` entries (e.g. ``[Bash, Agent(worker), Agent(researcher),
Read]``), CC's loader was dict-keyed by the base tool name — so the second
``Agent`` key overwrote the first, dropping ``Agent(worker)`` entirely.

CPV's validate_agent.py parses ``tools`` as a LIST (not a dict), so the bug
never reproduced here. These tests pin that invariant: each ``Agent(...)``
entry must be processed independently — neither parser nor validator may
collapse entries by base name.

If a future refactor key-deduplicates ``Agent(...)`` entries by base name,
these tests fail loudly, preventing CPV from regressing into the v2.1.147
CC-side bug.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports (same pattern as test_validate_agent.py).
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_agent import (  # noqa: E402
    AgentValidationReport,
    _parse_tool_reference,
    validate_disallowed_tools_field,
    validate_tools_field,
)


class TestV21147MultiAgentPreservation:
    """v2.1.147 — multiple Agent(...) entries in tools: must all be preserved.

    The CC-side regression collapsed dict-keyed entries by base name. CPV
    parses list-shaped tools as a list, so every entry is processed. These
    tests pin that behavior so a future refactor cannot regress.
    """

    def test_multiple_agent_entries_all_processed(self):
        """``tools: [Bash, Agent(worker), Agent(researcher), Read]`` — both Agent(...) entries reach the validator.

        Proof of preservation: each unknown spawnable must emit its own MINOR.
        If the validator silently dropped one entry, only one MINOR would
        appear and the count assertion below would fail.
        """
        report = AgentValidationReport()
        validate_tools_field(
            {"tools": ["Bash", "Agent(worker)", "Agent(researcher)", "Read"]},
            "agent.md",
            report,
        )
        # Each unknown spawnable produces a MINOR. Two distinct Agent(...) entries
        # with unknown spawnables → exactly two MINOR findings citing those names.
        worker_minors = [
            r for r in report.results
            if r.level == "MINOR" and "Agent(worker)" in r.message and "worker" in r.message
        ]
        researcher_minors = [
            r for r in report.results
            if r.level == "MINOR" and "Agent(researcher)" in r.message and "researcher" in r.message
        ]
        assert len(worker_minors) == 1, (
            f"Expected exactly 1 MINOR for Agent(worker); got {len(worker_minors)}. "
            f"All MINORs: {[r.message for r in report.results if r.level == 'MINOR']}"
        )
        assert len(researcher_minors) == 1, (
            f"Expected exactly 1 MINOR for Agent(researcher); got {len(researcher_minors)}. "
            f"All MINORs: {[r.message for r in report.results if r.level == 'MINOR']}"
        )

    def test_multiple_agent_entries_pass_count_reflects_all(self):
        """PASSED message reports the FULL count (4 tool(s)), not a deduplicated count."""
        report = AgentValidationReport()
        validate_tools_field(
            {"tools": ["Bash", "Agent(worker)", "Agent(researcher)", "Read"]},
            "agent.md",
            report,
        )
        passed = [r.message for r in report.results if r.level == "PASSED"]
        assert any("4 tool(s)" in m for m in passed), (
            f"Expected PASSED to mention 4 tool(s); got PASSED messages: {passed}"
        )

    def test_two_agent_entries_with_builtin_spawnables_both_pass(self):
        """``[Agent(Explore), Agent(Plan)]`` — both built-in spawnables pass without MAJOR/MINOR.

        If the second Agent(...) were dropped, the test would still pass
        accidentally because there's no negative signal. So we additionally
        assert the PASSED count = 2, which only holds when both entries
        survive list construction.
        """
        report = AgentValidationReport()
        validate_tools_field(
            {"tools": ["Agent(Explore)", "Agent(Plan)"]},
            "agent.md",
            report,
        )
        findings = [(r.level, r.message) for r in report.results if r.level in ("MAJOR", "MINOR")]
        assert not findings, f"Expected zero MAJOR/MINOR; got {findings}"
        passed = [r.message for r in report.results if r.level == "PASSED"]
        assert any("2 tool(s)" in m for m in passed), (
            f"Expected PASSED for 2 tool(s); got PASSED: {passed}"
        )

    def test_three_agent_entries_all_distinct(self):
        """Three Agent(...) entries with three different unknown spawnables produce three distinct MINORs."""
        report = AgentValidationReport()
        validate_tools_field(
            {"tools": ["Agent(alpha)", "Agent(beta)", "Agent(gamma)"]},
            "agent.md",
            report,
        )
        minor_messages = [r.message for r in report.results if r.level == "MINOR"]
        for name in ("alpha", "beta", "gamma"):
            matches = [m for m in minor_messages if name in m]
            assert len(matches) == 1, (
                f"Expected exactly 1 MINOR mentioning {name!r}; got {len(matches)}. "
                f"MINORs: {minor_messages}"
            )

    def test_comma_separated_string_form_preserves_multiple_agents(self):
        """``tools: "Bash, Agent(worker), Agent(researcher), Read"`` — string form also preserves all entries."""
        report = AgentValidationReport()
        validate_tools_field(
            {"tools": "Bash, Agent(worker), Agent(researcher), Read"},
            "agent.md",
            report,
        )
        worker_minors = [
            r for r in report.results
            if r.level == "MINOR" and "worker" in r.message
        ]
        researcher_minors = [
            r for r in report.results
            if r.level == "MINOR" and "researcher" in r.message
        ]
        assert len(worker_minors) == 1, (
            f"Expected exactly 1 MINOR for worker; got {len(worker_minors)}"
        )
        assert len(researcher_minors) == 1, (
            f"Expected exactly 1 MINOR for researcher; got {len(researcher_minors)}"
        )

    def test_disallowed_tools_multiple_agent_entries_preserved(self):
        """``disallowedTools: [Agent(worker), Agent(researcher)]`` — both entries reach the validator.

        The disallowed-tools validator reuses the same _parse_tool_reference
        helper. If a list-vs-dict refactor regressed validate_tools_field,
        it'd likely regress this validator too — this test covers the second
        site so both code paths stay safe.
        """
        report = AgentValidationReport()
        validate_disallowed_tools_field(
            {"disallowedTools": ["Agent(worker)", "Agent(researcher)"]},
            "agent.md",
            report,
        )
        passed = [r.message for r in report.results if r.level == "PASSED"]
        # disallowedTools doesn't emit per-spawnable MINORs (it just validates
        # the base name), so the count message is the surviving evidence.
        assert any("2 tool(s)" in m for m in passed), (
            f"Expected PASSED for 2 tool(s); got PASSED: {passed}"
        )

    def test_parser_does_not_collapse_separate_agent_entries(self):
        """Parser-level invariant: each ``Agent(...)`` string parses to its own (base, allowlist) pair.

        This catches a hypothetical regression where someone "optimizes" by
        memoizing on the base name. The parser must be a pure function of
        its input string.
        """
        a = _parse_tool_reference("Agent(worker)")
        b = _parse_tool_reference("Agent(researcher)")
        assert a == ("Agent", ["worker"], None)
        assert b == ("Agent", ["researcher"], None)
        assert a != b, "Two different Agent(...) entries must parse to different tuples"

    def test_repeated_identical_agent_entries_both_counted(self):
        """``tools: [Agent(worker), Agent(worker)]`` — duplicates are not silently deduplicated.

        Whether duplicates *should* be flagged is a separate question; the
        invariant being pinned here is "the validator doesn't drop the second
        one before anyone gets to see it". The PASSED count must reflect 2.
        """
        report = AgentValidationReport()
        validate_tools_field(
            {"tools": ["Agent(worker)", "Agent(worker)"]},
            "agent.md",
            report,
        )
        passed = [r.message for r in report.results if r.level == "PASSED"]
        assert any("2 tool(s)" in m for m in passed), (
            f"Expected PASSED for 2 tool(s) (duplicates preserved as-is); got: {passed}"
        )
