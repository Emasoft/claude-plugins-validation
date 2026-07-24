#!/usr/bin/env python3
"""False-negative-closing detectors for the agent validator (D1/D2/D3/D6).

Each detector here closes a VERIFIED false negative — a real-world agent that
scored 100/100 while carrying a real error the validator never looked for:

* **D1** duplicate top-level frontmatter key. ``yaml.safe_load`` keeps the LAST
  occurrence silently, so an agent with two ``tools:`` lines lost the first
  grant with no error anywhere. MAJOR.
* **D2** an MCP grant whose wildcard is joined by a SINGLE separator
  (``mcp__chrome-devtools-*``) cannot match any ``mcp__<server>__<tool>`` id, so
  the grant is inert. MAJOR when the body proves the cost, WARNING otherwise.
* **D3** a shell code fence in a body whose ``tools`` does not grant ``Bash``.
  WARNING only — an illustrative fence or a user-facing runbook is legitimate.
* **D6** a tool listed in BOTH ``tools`` and ``disallowedTools`` (documented to
  be REMOVED, voiding the grant) → MAJOR; a duplicate entry inside one list →
  WARNING.

Every detector is tested TWO-SIDED: a fixture that MUST fire at the stated
severity, and a near-miss that MUST NOT fire. CPV is universal — a detector
that calls a valid agent invalid is worse than the false negative it closes.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports (same pattern as test_validate_agent.py).
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_tool_permission_match import (  # noqa: E402
    ineffective_mcp_grants,
    iter_fenced_blocks,
    shell_fences_without_bash,
    uncovered_mcp_usages_for_server,
)
from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    extract_frontmatter_text,
    find_duplicate_frontmatter_keys,
    validate_no_duplicate_frontmatter_keys,
)
from validate_agent import (  # noqa: E402
    AgentValidationReport,
    validate_agent,
    validate_mcp_grant_hygiene,
    validate_shell_fence_tool_grant,
    validate_tool_grant_contradictions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _levels(report: ValidationReport, needle: str) -> list[str]:
    """Levels of every result whose message contains ``needle``."""
    return [r.level for r in report.results if needle in r.message]


def _messages(report: ValidationReport, level: str) -> list[str]:
    """Messages of every result at ``level``."""
    return [r.message for r in report.results if r.level == level]


# ---------------------------------------------------------------------------
# D1 — duplicate top-level frontmatter key
# ---------------------------------------------------------------------------


class TestD1DuplicateFrontmatterKeys:
    """A duplicated top-level key silently discards the earlier value."""

    def test_duplicate_tools_key_is_reported_with_both_lines(self):
        """The real case: two `tools:` lines — YAML keeps the last, first is lost."""
        fm_text = "name: helper\ntools: [Read, Grep]\ndescription: x\ntools: [Bash]"
        assert find_duplicate_frontmatter_keys(fm_text) == [("tools", [2, 4])]

    def test_single_occurrence_of_every_key_is_not_a_duplicate(self):
        """Near-miss: a normal frontmatter block reports nothing."""
        fm_text = "name: helper\ndescription: x\ntools: [Read, Grep]\nmodel: sonnet"
        assert find_duplicate_frontmatter_keys(fm_text) == []

    def test_indented_key_inside_block_scalar_is_not_a_duplicate(self):
        """Near-miss: block-scalar CONTENT is indented, so it is never a top-level key."""
        fm_text = "name: helper\ndescription: |\n  name: not-a-key\n  tools: also-not-a-key\ntools: [Read]"
        assert find_duplicate_frontmatter_keys(fm_text) == []

    def test_nested_mapping_key_is_not_a_duplicate(self):
        """Near-miss: an indented nested key repeating a top-level name is fine."""
        fm_text = "name: helper\nhooks:\n  name: inner\nmetadata:\n  name: inner"
        assert find_duplicate_frontmatter_keys(fm_text) == []

    def test_key_repeated_inside_a_multiline_flow_mapping_is_not_a_duplicate(self):
        """Near-miss: a flow collection can put a nested key at column 0."""
        fm_text = "metadata: {\nfoo: 1,\nfoo: 2,\n}\nname: helper"
        assert find_duplicate_frontmatter_keys(fm_text) == []

    def test_url_value_is_not_mistaken_for_a_key(self):
        """Near-miss: `http://` has no space after the colon, so it is not a key."""
        fm_text = "name: helper\nhttp: a\nsee http://example.com/http:\nother: b"
        assert find_duplicate_frontmatter_keys(fm_text) == []

    def test_comment_line_is_not_a_key(self):
        """Near-miss: a commented-out duplicate is not a duplicate."""
        fm_text = "name: helper\n# tools: [Read]\ntools: [Bash]"
        assert find_duplicate_frontmatter_keys(fm_text) == []

    def test_extract_frontmatter_text_returns_raw_block_and_offset(self):
        """The raw block starts on file line 2 (line 1 is the opening delimiter)."""
        assert extract_frontmatter_text("---\na: 1\nb: 2\n---\nbody\n") == ("a: 1\nb: 2", 2)

    def test_extract_returns_none_without_frontmatter(self):
        """Near-miss: a body-only file has no frontmatter block."""
        assert extract_frontmatter_text("# Just a heading\n") is None

    def test_reported_at_major_with_file_line_numbers(self):
        """MAJOR, and the line numbers are FILE lines (offset past the `---`)."""
        content = "---\nname: helper\ntools: [Read]\ntools: [Bash]\n---\nBody.\n"
        report = ValidationReport()
        validate_no_duplicate_frontmatter_keys(content, report, "helper.md")
        majors = _messages(report, "MAJOR")
        assert len(majors) == 1, majors
        assert "'tools'" in majors[0]
        assert "lines 3, 4" in majors[0], majors[0]
        assert "LAST occurrence" in majors[0]

    def test_clean_frontmatter_emits_nothing(self):
        """Near-miss: a clean file produces no result at all."""
        content = "---\nname: helper\ntools: [Read]\n---\nBody.\n"
        report = ValidationReport()
        validate_no_duplicate_frontmatter_keys(content, report, "helper.md")
        assert report.results == []

    def test_end_to_end_agent_validation_reports_the_duplicate(self, tmp_path: Path):
        """The full agent validator surfaces the duplicate as a MAJOR."""
        agent = tmp_path / "helper.md"
        agent.write_text(
            "---\n"
            "name: helper\n"
            "description: Use when the user needs a duplicated-key regression fixture.\n"
            "tools: [Read, Grep]\n"
            "tools: [Bash]\n"
            "---\n\n"
            "# Helper\n\nYou are a helper agent that reads files and reports findings.\n",
            encoding="utf-8",
        )
        report = validate_agent(agent)
        dupes = [m for m in _messages(report, "MAJOR") if "Duplicate top-level frontmatter key" in m]
        assert len(dupes) == 1, _messages(report, "MAJOR")

    def test_end_to_end_agent_without_duplicates_reports_none(self, tmp_path: Path):
        """Near-miss: the same agent with one `tools:` draws no duplicate finding."""
        agent = tmp_path / "helper.md"
        agent.write_text(
            "---\n"
            "name: helper\n"
            "description: Use when the user needs a clean single-key regression fixture.\n"
            "tools: [Read, Grep]\n"
            "---\n\n"
            "# Helper\n\nYou are a helper agent that reads files and reports findings.\n",
            encoding="utf-8",
        )
        report = validate_agent(agent)
        dupes = [m for m in _messages(report, "MAJOR") if "Duplicate top-level frontmatter key" in m]
        assert dupes == []


class TestD1WiredIntoSkillAndCommandValidators:
    """The same bug class exists for skills and commands — one shared check."""

    def test_skill_validator_reports_duplicate_key(self, tmp_path: Path):
        from validate_skill import validate_frontmatter  # noqa: PLC0415

        content = (
            "---\nname: my-skill\ndescription: Use when testing duplicates.\n"
            "description: Use when the last one wins.\n---\n\n# Skill\n"
        )
        report = ValidationReport()
        validate_frontmatter(tmp_path / "SKILL.md", content, report)
        assert any("Duplicate top-level frontmatter key" in m for m in _messages(report, "MAJOR"))

    def test_skill_validator_clean_frontmatter_has_no_duplicate_finding(self, tmp_path: Path):
        from validate_skill import validate_frontmatter  # noqa: PLC0415

        content = "---\nname: my-skill\ndescription: Use when testing the clean path.\n---\n\n# Skill\n"
        report = ValidationReport()
        validate_frontmatter(tmp_path / "SKILL.md", content, report)
        assert not any("Duplicate top-level frontmatter key" in m for m in _messages(report, "MAJOR"))

    def test_command_validator_reports_duplicate_key(self):
        from validate_command import CommandValidationReport, validate_frontmatter_exists  # noqa: PLC0415

        content = (
            "---\nname: my-command\ndescription: Use when testing duplicates.\n"
            "allowed-tools: Read\nallowed-tools: Bash\n---\n\nBody.\n"
        )
        report = CommandValidationReport()
        validate_frontmatter_exists(content, report, "my-command.md")
        assert any("Duplicate top-level frontmatter key" in m for m in _messages(report, "MAJOR"))

    def test_command_validator_clean_frontmatter_has_no_duplicate_finding(self):
        from validate_command import CommandValidationReport, validate_frontmatter_exists  # noqa: PLC0415

        content = "---\nname: my-command\ndescription: Use when testing the clean path.\nallowed-tools: Read\n---\n\nBody.\n"
        report = CommandValidationReport()
        validate_frontmatter_exists(content, report, "my-command.md")
        assert not any("Duplicate top-level frontmatter key" in m for m in _messages(report, "MAJOR"))


# ---------------------------------------------------------------------------
# D2 — MCP grant hygiene
# ---------------------------------------------------------------------------


class TestD2IneffectiveMcpGrants:
    """`mcp__server-*` (single separator) cannot reach `mcp__server__tool`."""

    def test_single_hyphen_wildcard_is_ineffective(self):
        """The real case: the author meant the whole chrome-devtools server."""
        assert ineffective_mcp_grants(["mcp__chrome-devtools-*"]) == [
            ("mcp__chrome-devtools-*", "chrome-devtools")
        ]

    def test_documented_server_glob_is_valid(self):
        """Near-miss: `mcp__<server>__*` is a DOCUMENTED form — never flag it."""
        assert ineffective_mcp_grants(["mcp__chrome-devtools__*"]) == []

    def test_bare_server_grant_is_valid(self):
        """Near-miss: `mcp__<server>` is a DOCUMENTED form — never flag it."""
        assert ineffective_mcp_grants(["mcp__chrome-devtools"]) == []

    def test_exact_tool_id_is_valid(self):
        """Near-miss: an exact tool id is the most precise valid grant."""
        assert ineffective_mcp_grants(["mcp__chrome-devtools__click"]) == []

    def test_global_mcp_wildcard_is_not_flagged(self):
        """Near-miss: `mcp__*` is documented for disallowedTools — out of scope."""
        assert ineffective_mcp_grants(["mcp__*"]) == []

    def test_underscore_wildcard_that_fnmatch_covers_is_not_flagged(self):
        """Near-miss: `mcp__s_*` DOES glob-match `mcp__s__tool`, so it is effective.

        The detector's gate is a PROOF (does the pattern reach a probe id of its
        own server?), not the token's shape — so a wildcard that happens to work
        is never called broken.
        """
        assert ineffective_mcp_grants(["mcp__server_*"]) == []

    def test_builtin_tools_are_ignored(self):
        """Near-miss: non-MCP rules are not MCP grants."""
        assert ineffective_mcp_grants(["Read", "Bash(git:*)", "Grep"]) == []

    def test_uncovered_usages_detects_the_cost(self):
        """The body names a tool of the very server the broken grant meant to allow."""
        body = "Call mcp__chrome-devtools__click to press the button.\n"
        uncovered = uncovered_mcp_usages_for_server(body, ["mcp__chrome-devtools-*"], "chrome-devtools")
        assert [u.name for u in uncovered] == ["mcp__chrome-devtools__click"]

    def test_uncovered_usages_empty_when_a_valid_grant_covers_them(self):
        """Near-miss: with the documented glob present nothing is uncovered."""
        body = "Call mcp__chrome-devtools__click to press the button.\n"
        assert uncovered_mcp_usages_for_server(body, ["mcp__chrome-devtools__*"], "chrome-devtools") == []


class TestD2Severity:
    """MAJOR only when the body proves the cost; WARNING when uncorroborated."""

    BODY_USING_DEVTOOLS = "Take a snapshot with mcp__chrome-devtools__take_snapshot, then click.\n"

    def test_corroborated_grant_is_major(self):
        frontmatter = {"tools": ["Read", "mcp__chrome-devtools-*"]}
        report = AgentValidationReport()
        validate_mcp_grant_hygiene(frontmatter, self.BODY_USING_DEVTOOLS, "a.md", report)
        majors = _messages(report, "MAJOR")
        assert len(majors) == 1, report.results
        assert "mcp__chrome-devtools-*" in majors[0]
        assert "mcp__chrome-devtools__take_snapshot" in majors[0]
        assert "root cause" in majors[0]

    def test_uncorroborated_grant_is_warning_only(self):
        """No body usage → WARNING, never a blocking severity."""
        frontmatter = {"tools": ["Read", "mcp__chrome-devtools-*"]}
        report = AgentValidationReport()
        validate_mcp_grant_hygiene(frontmatter, "Read files and summarise them.\n", "a.md", report)
        assert _messages(report, "MAJOR") == []
        warnings = _messages(report, "WARNING")
        assert len(warnings) == 1, report.results
        assert "mcp__chrome-devtools-*" in warnings[0]

    def test_valid_glob_with_body_usage_emits_nothing(self):
        """Near-miss: the documented glob grants the tools — no finding at all."""
        frontmatter = {"tools": ["Read", "mcp__chrome-devtools__*"]}
        report = AgentValidationReport()
        validate_mcp_grant_hygiene(frontmatter, self.BODY_USING_DEVTOOLS, "a.md", report)
        assert report.results == []

    def test_bare_server_grant_with_body_usage_emits_nothing(self):
        """Near-miss: the bare-server form grants every tool of that server."""
        frontmatter = {"tools": ["Read", "mcp__chrome-devtools"]}
        report = AgentValidationReport()
        validate_mcp_grant_hygiene(frontmatter, self.BODY_USING_DEVTOOLS, "a.md", report)
        assert report.results == []

    def test_absent_tools_field_emits_nothing(self):
        """Near-miss: no `tools` field means every tool is inherited."""
        report = AgentValidationReport()
        validate_mcp_grant_hygiene({}, self.BODY_USING_DEVTOOLS, "a.md", report)
        assert report.results == []


# ---------------------------------------------------------------------------
# D3 — shell fence without a Bash grant
# ---------------------------------------------------------------------------


class TestD3ShellFenceWithoutBash:
    """Advisory only — a documented runbook is as likely as a real invocation."""

    BODY_WITH_SHELL_FENCE = "Run this:\n\n```bash\nls -la\n```\n\nThen report.\n"

    def test_shell_fence_without_bash_is_warning(self):
        report = AgentValidationReport()
        validate_shell_fence_tool_grant(
            {"tools": ["Read", "Grep", "Glob"]}, self.BODY_WITH_SHELL_FENCE, "a.md", report
        )
        warnings = _messages(report, "WARNING")
        assert len(warnings) == 1, report.results
        assert "shell code fence" in warnings[0]
        assert "'Bash'" in warnings[0]

    def test_never_escalates_above_warning(self):
        """The FP risk is real, so this detector must never block a publish."""
        report = AgentValidationReport()
        validate_shell_fence_tool_grant(
            {"tools": ["Read"]}, self.BODY_WITH_SHELL_FENCE, "a.md", report
        )
        assert {r.level for r in report.results} == {"WARNING"}

    def test_agent_that_grants_bash_is_not_flagged(self):
        """Near-miss: the fence is executable, so there is no defect."""
        report = AgentValidationReport()
        validate_shell_fence_tool_grant(
            {"tools": ["Read", "Bash"]}, self.BODY_WITH_SHELL_FENCE, "a.md", report
        )
        assert report.results == []

    def test_scoped_bash_grant_counts_as_bash(self):
        """Near-miss: `Bash(git:*)` is still a Bash grant."""
        report = AgentValidationReport()
        validate_shell_fence_tool_grant(
            {"tools": ["Read", "Bash(git:*)"]}, self.BODY_WITH_SHELL_FENCE, "a.md", report
        )
        assert report.results == []

    def test_absent_tools_field_is_not_flagged(self):
        """Near-miss: an absent `tools` field inherits Bash."""
        report = AgentValidationReport()
        validate_shell_fence_tool_grant({}, self.BODY_WITH_SHELL_FENCE, "a.md", report)
        assert report.results == []

    def test_non_shell_fence_is_not_flagged(self):
        """Near-miss: a python/yaml fence is not a shell invocation."""
        body = "Config:\n\n```yaml\nkey: value\n```\n\n```python\nx = 1\n```\n"
        report = AgentValidationReport()
        validate_shell_fence_tool_grant({"tools": ["Read"]}, body, "a.md", report)
        assert report.results == []

    def test_multiple_fences_collapse_into_one_warning(self):
        """Report noise control: N fences yield ONE finding listing every line."""
        body = "```bash\nls\n```\n\ntext\n\n```sh\npwd\n```\n"
        report = AgentValidationReport()
        validate_shell_fence_tool_grant({"tools": ["Read"]}, body, "a.md", report)
        warnings = _messages(report, "WARNING")
        assert len(warnings) == 1, report.results
        assert "1, 7" in warnings[0], warnings[0]

    def test_helper_returns_opening_lines(self):
        body = "text\n```bash\nls -la\n```\n"
        assert shell_fences_without_bash(["Read"], body) == [2]

    def test_fence_tracker_still_flags_in_fence_lines(self):
        """The refactor must not change the existing in-fence flags."""
        body = "text\n```bash\nls -la\n```\nafter\n"
        blocks = iter_fenced_blocks(body)
        assert [(b.lang, b.open_line, b.close_line) for b in blocks] == [("bash", 2, 4)]


# ---------------------------------------------------------------------------
# D6 — grant contradictions
# ---------------------------------------------------------------------------


class TestD6GrantContradictions:
    """`disallowedTools` is applied first — a tool in both lists is REMOVED."""

    def test_tool_in_both_lists_is_major(self):
        report = AgentValidationReport()
        validate_tool_grant_contradictions(
            {"tools": ["Read", "Bash"], "disallowedTools": ["Bash"]}, "a.md", report
        )
        majors = _messages(report, "MAJOR")
        assert len(majors) == 1, report.results
        assert "Bash" in majors[0]
        assert "BOTH" in majors[0]

    def test_alias_overlap_is_major(self):
        """`Task` is an alias of `Agent`, so listing one in each list contradicts."""
        report = AgentValidationReport()
        validate_tool_grant_contradictions(
            {"tools": ["Task"], "disallowedTools": ["Agent"]}, "a.md", report
        )
        assert any("BOTH" in m for m in _messages(report, "MAJOR"))

    def test_disjoint_lists_are_not_flagged(self):
        """Near-miss: an allowlist and a denylist that do not overlap."""
        report = AgentValidationReport()
        validate_tool_grant_contradictions(
            {"tools": ["Read", "Grep"], "disallowedTools": ["Write", "Edit"]}, "a.md", report
        )
        assert report.results == []

    def test_bash_scope_refinement_is_not_a_contradiction(self):
        """Near-miss: allow one Bash scope, deny another — a REFINEMENT, not a clash.

        Comparing on the base tool name would flag this; comparing on the exact
        normalised token does not. This is the detector's key FP guard.
        """
        report = AgentValidationReport()
        validate_tool_grant_contradictions(
            {"tools": ["Bash(git:*)"], "disallowedTools": ["Bash(rm:*)"]}, "a.md", report
        )
        assert report.results == []

    def test_broad_grant_narrowed_by_scoped_denial_is_not_flagged(self):
        """Near-miss: `tools: Bash` + `disallowedTools: Bash(rm:*)` narrows the pool."""
        report = AgentValidationReport()
        validate_tool_grant_contradictions(
            {"tools": ["Bash"], "disallowedTools": ["Bash(rm:*)"]}, "a.md", report
        )
        assert report.results == []

    def test_duplicate_entry_inside_tools_is_warning(self):
        report = AgentValidationReport()
        validate_tool_grant_contradictions({"tools": ["Read", "Grep", "Read"]}, "a.md", report)
        warnings = _messages(report, "WARNING")
        assert len(warnings) == 1, report.results
        assert "'tools'" in warnings[0]
        assert "Read" in warnings[0]
        assert _messages(report, "MAJOR") == []

    def test_duplicate_entry_inside_disallowed_tools_is_warning(self):
        report = AgentValidationReport()
        validate_tool_grant_contradictions({"disallowedTools": ["Write", "Write"]}, "a.md", report)
        assert len(_messages(report, "WARNING")) == 1, report.results

    def test_no_duplicates_emits_nothing(self):
        """Near-miss: a clean list of distinct tools."""
        report = AgentValidationReport()
        validate_tool_grant_contradictions({"tools": ["Read", "Grep", "Glob"]}, "a.md", report)
        assert report.results == []

    def test_distinct_bash_scopes_are_not_duplicates(self):
        """Near-miss: two different Bash scopes are two different rules."""
        report = AgentValidationReport()
        validate_tool_grant_contradictions(
            {"tools": ["Bash(git:*)", "Bash(npm:*)"]}, "a.md", report
        )
        assert report.results == []

    def test_comma_separated_string_form_is_handled(self):
        """The string form of the fields is parsed the same way as the list form."""
        report = AgentValidationReport()
        validate_tool_grant_contradictions(
            {"tools": "Read, Bash", "disallowedTools": "Bash"}, "a.md", report
        )
        assert any("BOTH" in m for m in _messages(report, "MAJOR"))

    def test_absent_fields_emit_nothing(self):
        report = AgentValidationReport()
        validate_tool_grant_contradictions({}, "a.md", report)
        assert report.results == []

    def test_end_to_end_agent_validation_reports_the_contradiction(self, tmp_path: Path):
        agent = tmp_path / "conflicted.md"
        agent.write_text(
            "---\n"
            "name: conflicted\n"
            "description: Use when the user needs a contradictory grant fixture.\n"
            "tools: [Read, Bash]\n"
            "disallowedTools: [Bash]\n"
            "---\n\n"
            "# Conflicted\n\nYou are an agent whose grants contradict each other.\n",
            encoding="utf-8",
        )
        report = validate_agent(agent)
        assert any("BOTH" in m for m in _messages(report, "MAJOR")), _messages(report, "MAJOR")

    def test_end_to_end_disjoint_grants_report_no_contradiction(self, tmp_path: Path):
        agent = tmp_path / "clean.md"
        agent.write_text(
            "---\n"
            "name: clean\n"
            "description: Use when the user needs a clean disjoint grant fixture.\n"
            "tools: [Read, Grep]\n"
            "disallowedTools: [Write]\n"
            "---\n\n"
            "# Clean\n\nYou are an agent whose grants do not contradict each other.\n",
            encoding="utf-8",
        )
        report = validate_agent(agent)
        assert not any("BOTH" in m for m in _messages(report, "MAJOR"))


# ---------------------------------------------------------------------------
# Cross-detector: a fully valid agent must stay silent
# ---------------------------------------------------------------------------


class TestNoFalsePositivesOnAValidAgent:
    """The north star: none of the four detectors may fire on a valid agent."""

    def test_valid_agent_draws_no_new_finding(self, tmp_path: Path):
        agent = tmp_path / "valid.md"
        agent.write_text(
            "---\n"
            "name: valid\n"
            "description: Use when the user wants a fully valid agent that must scan clean.\n"
            "tools: [Read, Grep, Glob, Bash, mcp__chrome-devtools__take_snapshot]\n"
            "disallowedTools: [Write]\n"
            "model: sonnet\n"
            "---\n\n"
            "# Valid\n\n"
            "You are a valid agent.\n\n"
            "Run the check:\n\n"
            "```bash\nls -la\n```\n\n"
            "Then call mcp__chrome-devtools__take_snapshot and report.\n\n"
            "<example>\nuser: check it\nassistant: I'll check it.\n</example>\n\n"
            "<example>\nuser: check again\nassistant: I'll check again.\n</example>\n",
            encoding="utf-8",
        )
        report = validate_agent(agent)
        new_needles = (
            "Duplicate top-level frontmatter key",
            "matches no tool",
            "shell code fence",
            "appears in BOTH",
            "lists the same entry more than once",
        )
        fired = [r.message for r in report.results for n in new_needles if n in r.message]
        assert fired == [], fired

    def test_needles_are_present_when_the_agent_is_broken(self, tmp_path: Path):
        """Positive control: the same needles DO fire on a deliberately broken agent.

        Without this, the assertion above could pass vacuously if a needle string
        ever drifted away from the emitted message.
        """
        agent = tmp_path / "broken.md"
        agent.write_text(
            "---\n"
            "name: broken\n"
            "description: Use when the user wants an agent that trips every new detector.\n"
            "tools: [Read, Read, mcp__chrome-devtools-*]\n"
            "disallowedTools: [Read]\n"
            "tools: [Grep, Grep, mcp__chrome-devtools-*]\n"
            "---\n\n"
            "# Broken\n\n"
            "You are a broken agent.\n\n"
            "```bash\nls -la\n```\n\n"
            "Then call mcp__chrome-devtools__take_snapshot and report.\n",
            encoding="utf-8",
        )
        report = validate_agent(agent)
        messages = " || ".join(r.message for r in report.results)
        for needle in (
            "Duplicate top-level frontmatter key",
            "matches no tool",
            "shell code fence",
            "lists the same entry more than once",
        ):
            assert needle in messages, f"{needle} did not fire: {messages}"
        assert "MAJOR" in _levels(report, "Duplicate top-level frontmatter key")
