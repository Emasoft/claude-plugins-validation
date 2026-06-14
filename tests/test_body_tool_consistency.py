#!/usr/bin/env python3
"""Tests for cpv_tool_permission_match — body-vs-frontmatter tool consistency.

TRDD-94e06820 Phase 1. A component that DECLARES allowed-tools/tools but whose
BODY uses a tool the field does not grant fails silently at runtime; this is a
CRITICAL bug (WARNING when the usage is only a prose mention, per the Balanced
FP policy). An ABSENT field means all tools allowed → no check.
"""

from __future__ import annotations

import cpv_tool_permission_match as m
import validate_agent as _va
import validate_skill_comprehensive as _vsc

_CONSISTENCY_MARKERS = ("does not grant", "fail at runtime", "no tools allowed")


def _has_consistency_critical(report: object) -> bool:
    """True when the report carries a CRITICAL from the body-tool-consistency rule."""
    return any(
        r.level == "CRITICAL" and any(mark in r.message for mark in _CONSISTENCY_MARKERS)
        for r in report.results  # type: ignore[attr-defined]
    )


class FakeReport:
    """Minimal report capturing (level, message, file, line) tuples."""

    def __init__(self) -> None:
        self.findings: list[tuple[str, str, str | None, int | None]] = []

    def critical(self, message: str, file: str | None = None, line: int | None = None) -> None:
        self.findings.append(("CRITICAL", message, file, line))

    def warning(self, message: str, file: str | None = None, line: int | None = None) -> None:
        self.findings.append(("WARNING", message, file, line))

    def levels(self) -> list[str]:
        return [f[0] for f in self.findings]


def _fence(*lines: str) -> str:
    """Wrap lines in a ```text fenced code block."""
    return "```text\n" + "\n".join(lines) + "\n```\n"


# ── parse_declared_tools ──────────────────────────────────────────────────────


def test_parse_absent_field_returns_none() -> None:
    """An absent field (value None) parses to None → caller skips the check."""
    assert m.parse_declared_tools(None) is None


def test_parse_empty_list_returns_empty_list() -> None:
    """An empty list ([]) parses to [] → 'no tools allowed' declaration."""
    assert m.parse_declared_tools([]) == []


def test_parse_empty_string_returns_empty_list() -> None:
    """An empty/whitespace string parses to [] → 'no tools allowed'."""
    assert m.parse_declared_tools("") == []
    assert m.parse_declared_tools("   ") == []


def test_parse_csv_string_splits_on_comma() -> None:
    """A CSV string splits into individual rules, trimming whitespace."""
    assert m.parse_declared_tools("Read, Write, Bash") == ["Read", "Write", "Bash"]


def test_parse_respects_parenthesised_scopes() -> None:
    """Commas INSIDE a scope like Bash(git:*,gh:*) do not split the rule."""
    assert m.parse_declared_tools("Read, Bash(git:*,gh:*), Grep") == [
        "Read",
        "Bash(git:*,gh:*)",
        "Grep",
    ]


def test_parse_space_delimited_openspec() -> None:
    """A space-delimited (OpenSpec) string splits on spaces when no comma present."""
    assert m.parse_declared_tools("Bash(jq:*) Read") == ["Bash(jq:*)", "Read"]


def test_parse_list_passthrough() -> None:
    """A YAML list passes through, stringified and trimmed."""
    assert m.parse_declared_tools(["Read", " Bash "]) == ["Read", "Bash"]


def test_parse_unparseable_type_returns_empty() -> None:
    """A non-str/list/None value (e.g. int) parses to [] so the check never crashes."""
    assert m.parse_declared_tools(42) == []


def test_parse_mixed_space_tools_with_comma_scope() -> None:
    """Space-separated tools with a comma INSIDE a scope split correctly on both delimiters."""
    assert m.parse_declared_tools("Read Bash(git:*,gh:*)") == ["Read", "Bash(git:*,gh:*)"]


def test_parse_comma_and_space_run_collapses() -> None:
    """A ', ' (comma + space) run yields no empty tokens."""
    assert m.parse_declared_tools("Read,  Grep") == ["Read", "Grep"]


def test_parse_list_item_that_is_csv_string() -> None:
    """A YAML list item that is itself a CSV string is re-split, not kept as one bogus rule."""
    assert m.parse_declared_tools(["Read, Bash(git:*)"]) == ["Read", "Bash(git:*)"]


def test_parse_strips_surrounding_quotes() -> None:
    """Tokens whose surrounding quotes survived YAML normalise to the bare tool name."""
    assert m.parse_declared_tools('"Read", "Write"') == ["Read", "Write"]


def test_parse_drops_stray_top_level_close_paren() -> None:
    """A malformed stray top-level ')' is dropped so the rule name stays matchable."""
    assert m.parse_declared_tools("Read)") == ["Read"]
    assert "Read" in m.granted_builtin_tools(m.parse_declared_tools("Read)"))


# ── granted_builtin_tools (alias + cross-grant expansion) ─────────────────────


def test_grant_edit_also_grants_read() -> None:
    """A declared Edit rule grants Read (documented Edit→Read path-access grant)."""
    assert "Read" in m.granted_builtin_tools(["Edit(/src/**)"])


def test_grant_bash_also_grants_monitor() -> None:
    """A declared Bash rule grants Monitor (Monitor reuses Bash permission rules)."""
    assert "Monitor" in m.granted_builtin_tools(["Bash(git:*)"])


def test_grant_task_alias_resolves_to_agent() -> None:
    """Declared Task resolves to Agent (alias) in the granted set."""
    assert "Agent" in m.granted_builtin_tools(["Task"])


def test_grant_read_does_not_grant_grep() -> None:
    """Read does NOT grant Grep — allow-lists name each tool explicitly."""
    assert "Grep" not in m.granted_builtin_tools(["Read"])


def test_grant_ignores_mcp_rules() -> None:
    """MCP rules are not counted as built-in tool grants."""
    assert m.granted_builtin_tools(["mcp__serena__find_symbol"]) == set()


# ── mcp_usage_allowed ─────────────────────────────────────────────────────────


def test_mcp_exact_match_allowed() -> None:
    """An exact mcp pattern grants exactly that tool."""
    assert m.mcp_usage_allowed("mcp__serena__find_symbol", ["mcp__serena__find_symbol"])


def test_mcp_glob_match_allowed() -> None:
    """A wildcard mcp__server__* pattern grants any tool from that server."""
    assert m.mcp_usage_allowed("mcp__serena__find_symbol", ["mcp__serena__*"])


def test_mcp_bare_server_prefix_allowed() -> None:
    """A bare server pattern (mcp__serena) grants every mcp__serena__<tool>."""
    assert m.mcp_usage_allowed("mcp__serena__find_symbol", ["mcp__serena"])


def test_mcp_different_server_not_allowed() -> None:
    """A pattern for a DIFFERENT server does not grant the usage."""
    assert not m.mcp_usage_allowed("mcp__serena__find_symbol", ["mcp__github__*"])


def test_detect_hyphenated_mcp_server_name() -> None:
    """An MCP tool whose server name contains hyphens is detected in full."""
    body = "Use mcp__plugin_llm-externalizer_llm-externalizer__chat(...) to offload.\n"
    usages = m.detect_body_usages(body)
    assert len(usages) == 1
    assert usages[0].is_mcp is True
    assert usages[0].name == "mcp__plugin_llm-externalizer_llm-externalizer__chat"


def test_hyphenated_mcp_glob_allowed() -> None:
    """A hyphenated MCP usage is granted by its server wildcard pattern."""
    usage = "mcp__plugin_llm-externalizer_llm-externalizer__chat"
    assert m.mcp_usage_allowed(usage, ["mcp__plugin_llm-externalizer_llm-externalizer__*"])


def test_mcp_regex_no_redos_on_adversarial_underscores() -> None:
    """The MCP detector is LINEAR on a pathological all-underscore body (no ReDoS).

    CPV scans untrusted third-party plugin bodies, so the detector must not be
    susceptible to catastrophic-backtracking denial-of-service. A nested-quantifier
    pattern with overlapping ``_`` would hang here; the single-run pattern is
    linear and returns near-instantly.
    """
    import time

    body = "mcp__" + ("_" * 50000) + " end"
    start = time.time()
    usages = m.detect_body_usages(body)
    assert time.time() - start < 2.0
    assert len(usages) == 1 and usages[0].is_mcp


# ── detect_body_usages ────────────────────────────────────────────────────────


def test_detect_tool_call_in_prose_not_in_fence() -> None:
    """A tool call in prose is detected with in_fence=False."""
    usages = m.detect_body_usages("Then call Read(file_path) to load it.\n")
    assert len(usages) == 1
    assert usages[0].name == "Read"
    assert usages[0].in_fence is False


def test_detect_tool_call_in_fence_flagged() -> None:
    """A tool call inside a fenced block is detected with in_fence=True."""
    usages = m.detect_body_usages(_fence("Skill({skill: 'x'})"))
    assert len(usages) == 1
    assert usages[0].name == "Skill"
    assert usages[0].in_fence is True


def test_detect_task_alias_normalised_to_agent() -> None:
    """A Task(...) call is normalised to the canonical Agent name."""
    usages = m.detect_body_usages("Spawn via Task({subagent_type: 'x'}).\n")
    assert usages[0].name == "Agent"


def test_detect_method_call_not_matched() -> None:
    """A method-style `.Read(` or substring `xRead(` is NOT a tool call."""
    assert m.detect_body_usages("obj.Read(x)\nmyRead(y)\n") == []


def test_detect_space_before_paren_not_matched() -> None:
    """Prose like 'Read (the file)' (space before paren) is NOT a tool call."""
    assert m.detect_body_usages("## Read (the file) section\n") == []


def test_detect_mcp_usage() -> None:
    """An mcp__server__tool reference is detected as an MCP usage."""
    usages = m.detect_body_usages("Call mcp__serena__find_symbol here.\n")
    assert len(usages) == 1
    assert usages[0].is_mcp is True
    assert usages[0].name == "mcp__serena__find_symbol"


def test_detect_dedups_same_tool_same_line() -> None:
    """The same tool invoked twice on one line yields a single usage."""
    usages = m.detect_body_usages("Read(a) then Read(b)\n")
    assert len(usages) == 1


def test_detect_markdown_link_and_namespaced_call_not_matched() -> None:
    """A markdown link [Read](url) and namespaced calls (ns::Read(, a:Write() are not tool calls."""
    body = "See [Read](http://x) and ns::Read(y) and a:Write(z) and pre>Edit(w).\n"
    assert m.detect_body_usages(body) == []


def test_detect_4space_indented_fence_is_not_a_fence() -> None:
    """A fence delimiter indented 4+ spaces is an indented code block, not a fence (CommonMark)."""
    body = "Para.\n\n    ```\n    Bash(x)\n    ```\n"
    usages = m.detect_body_usages(body)
    assert len(usages) == 1
    assert usages[0].in_fence is False  # not inside a real fence


def test_detect_fence_closer_with_info_string_does_not_close() -> None:
    """A ```lang line is not a valid closer (CommonMark), so the fence stays open."""
    body = "```\nSkill({})\n```text\nEdit(x)\n```\n"
    in_fence = {u.name: u.in_fence for u in m.detect_body_usages(body)}
    assert in_fence.get("Skill") is True
    assert in_fence.get("Edit") is True  # still inside the fence (```text didn't close it)


# ── check_body_tool_consistency: absent / empty ───────────────────────────────


def test_absent_field_no_findings() -> None:
    """Field absent (None) → all tools allowed → zero findings even if body uses tools."""
    assert m.check_body_tool_consistency(None, _fence("Bash(deploy)", "Edit(x)", "Agent({})")) == []


def test_empty_list_plus_fenced_usage_is_critical() -> None:
    """Empty [] + a fenced tool call → CRITICAL (declared no tools, uses one)."""
    findings = m.check_body_tool_consistency([], _fence("Read(x)"))
    assert [f.severity for f in findings] == ["CRITICAL"]


def test_empty_list_plus_prose_usage_is_critical() -> None:
    """Empty [] + even a PROSE tool call → CRITICAL (empty is always critical)."""
    findings = m.check_body_tool_consistency([], "Use Read(x) to load.\n")
    assert [f.severity for f in findings] == ["CRITICAL"]


def test_empty_list_no_usage_no_findings() -> None:
    """Empty [] + a body with NO tool call → no findings (valid chat-only body)."""
    assert m.check_body_tool_consistency([], "This skill only summarises text.\n") == []


# ── check_body_tool_consistency: non-empty subset ─────────────────────────────


def test_declared_subset_fenced_undeclared_is_critical() -> None:
    """Declared [Read] + fenced Bash(...) → CRITICAL (Bash not granted)."""
    findings = m.check_body_tool_consistency(["Read"], _fence("Bash(npm test)"))
    assert [f.severity for f in findings] == ["CRITICAL"]


def test_declared_subset_prose_undeclared_is_warning() -> None:
    """Declared [Read] + prose Bash(...) → WARNING (likely documentation)."""
    findings = m.check_body_tool_consistency(["Read"], "You can run Bash(ls) optionally.\n")
    assert [f.severity for f in findings] == ["WARNING"]


def test_declared_grants_all_used_no_findings() -> None:
    """Declared [Read, Bash] + body uses Read + Bash → no findings."""
    body = _fence("Read(x)", "Bash(ls)")
    assert m.check_body_tool_consistency(["Read", "Bash(git:*)"], body) == []


def test_edit_grant_covers_read_usage() -> None:
    """Declared [Edit] + body uses Read() → no findings (Edit grants Read)."""
    assert m.check_body_tool_consistency(["Edit(/src/**)"], _fence("Read(x)")) == []


def test_agent_alias_covers_task_usage() -> None:
    """Declared [Agent] + body uses Task() → no findings (alias)."""
    assert m.check_body_tool_consistency(["Agent"], _fence("Task({x: 1})")) == []


def test_task_declared_covers_agent_usage() -> None:
    """Declared [Task] + body uses Agent() → no findings (reverse alias)."""
    assert m.check_body_tool_consistency(["Task"], _fence("Agent({x: 1})")) == []


# ── check_body_tool_consistency: MCP ──────────────────────────────────────────


def test_mcp_declared_exact_no_findings() -> None:
    """Declared MCP tool + body uses it → no findings."""
    body = _fence("mcp__serena__find_symbol(...)")
    assert m.check_body_tool_consistency(["mcp__serena__find_symbol"], body) == []


def test_mcp_glob_covers_usage_no_findings() -> None:
    """Declared mcp__serena__* + body uses any serena tool → no findings."""
    body = _fence("mcp__serena__find_symbol(...)")
    assert m.check_body_tool_consistency(["mcp__serena__*"], body) == []


def test_mcp_undeclared_fenced_is_critical() -> None:
    """Declared a DIFFERENT server + fenced MCP usage → CRITICAL."""
    body = _fence("mcp__serena__find_symbol(...)")
    findings = m.check_body_tool_consistency(["mcp__github__*"], body)
    assert [f.severity for f in findings] == ["CRITICAL"]


def test_mcp_undeclared_prose_is_warning() -> None:
    """Undeclared MCP usage in prose → WARNING."""
    findings = m.check_body_tool_consistency(["Read"], "Optionally call mcp__github__get_issue now.\n")
    assert [f.severity for f in findings] == ["WARNING"]


def test_empty_plus_mcp_usage_is_critical() -> None:
    """Empty [] + any MCP usage → CRITICAL."""
    findings = m.check_body_tool_consistency([], _fence("mcp__x__y(...)"))
    assert [f.severity for f in findings] == ["CRITICAL"]


# ── validate_body_tool_consistency: report emission ───────────────────────────


def test_emit_critical_on_report() -> None:
    """validate_body_tool_consistency emits a CRITICAL on the report for fenced undeclared usage."""
    report = FakeReport()
    m.validate_body_tool_consistency(["Read"], _fence("Bash(x)"), report, filename="SKILL.md")
    assert report.levels() == ["CRITICAL"]
    assert report.findings[0][2] == "SKILL.md"


def test_emit_uses_field_name_in_message() -> None:
    """The emitted message references the caller-supplied field name (tools vs allowed-tools)."""
    report = FakeReport()
    m.validate_body_tool_consistency([], _fence("Bash(x)"), report, filename="agent.md", field_name="tools")
    assert "'tools'" in report.findings[0][1]


def test_emit_nothing_when_absent() -> None:
    """No emission when the field is absent (None)."""
    report = FakeReport()
    m.validate_body_tool_consistency(None, _fence("Bash(x)"), report, filename="SKILL.md")
    assert report.findings == []


# ── validate_body_tool_consistency: prose-WARNING collapse (issue #109) ────────


def test_emit_collapses_multiple_prose_warnings_into_one_summary() -> None:
    """≥2 prose MCP mentions + a non-empty field → ONE summary WARNING listing every line and tool.

    A documentation-heavy body that merely DESCRIBES optional MCP tooling produced one
    near-identical WARNING per mention (issue #109). They now collapse into a single
    information-preserving summary WARNING; CRITICAL invocations are unaffected.
    """
    body = (
        "If the LLM Externalizer is available, prefer mcp__llmx__chat for offloading.\n"
        "You may also call mcp__github__get_issue to fetch context.\n"
    )
    report = FakeReport()
    m.validate_body_tool_consistency(["Read"], body, report, filename="agent.md", field_name="tools")
    assert report.levels() == ["WARNING"]  # exactly one finding, collapsed
    msg = report.findings[0][1]
    assert "prose mentions of tools not granted" in msg  # summary phrasing, not per-mention
    assert "'tools'" in msg  # references the caller-supplied field name
    assert "lines 1, 2" in msg  # every line number preserved
    assert "mcp__llmx__chat" in msg and "mcp__github__get_issue" in msg  # every tool name preserved
    assert report.findings[0][3] == 1  # anchored at the first prose line


def test_emit_critical_usages_never_collapsed() -> None:
    """Tool usages inside a code fence stay CRITICAL and per-mention, never collapsed.

    The collapse is prose-WARNING-only — a real undeclared INVOCATION fails silently at
    runtime and must keep surfacing individually (FN-safe).
    """
    body = _fence("Bash(npm test)", "Agent({subagent_type: 'x'})")
    report = FakeReport()
    m.validate_body_tool_consistency(["Read"], body, report, filename="SKILL.md")
    assert report.levels() == ["CRITICAL", "CRITICAL"]  # both fenced usages, per-mention
    assert all("prose mentions of tools not granted" not in f[1] for f in report.findings)


def test_emit_single_prose_warning_unchanged() -> None:
    """Exactly one prose mention → that one WARNING is emitted verbatim (no summary phrasing)."""
    report = FakeReport()
    m.validate_body_tool_consistency(["Read"], "You can run Bash(ls) optionally.\n", report, filename="SKILL.md")
    assert report.levels() == ["WARNING"]
    msg = report.findings[0][1]
    assert "prose mentions of tools not granted" not in msg  # NOT the summary form
    assert "(in prose)" in msg and "'Bash'" in msg  # the original per-mention message


def test_emit_mixed_critical_and_prose_yields_one_critical_per_mention_plus_one_summary() -> None:
    """1 fenced CRITICAL + 3 prose WARNINGs → 1 CRITICAL (per-mention) + 1 summary WARNING."""
    body = (
        "## Steps\n"
        "First, optionally call mcp__a__x to warm up.\n"
        "You may also use mcp__b__y here.\n"
        "And mcp__c__z is another option.\n"
        "\n"
        "```text\n"
        "Bash(npm run build)\n"
        "```\n"
    )
    report = FakeReport()
    m.validate_body_tool_consistency(["Read"], body, report, filename="agent.md", field_name="tools")
    levels = report.levels()
    assert levels.count("CRITICAL") == 1  # the fenced Bash invocation, per-mention
    assert levels.count("WARNING") == 1  # the 3 prose mentions collapsed into one summary
    summary = next(f for f in report.findings if f[0] == "WARNING")[1]
    assert "3 prose mentions of tools not granted" in summary
    assert "mcp__a__x" in summary and "mcp__b__y" in summary and "mcp__c__z" in summary
    crit = next(f for f in report.findings if f[0] == "CRITICAL")[1]
    assert "'Bash'" in crit and "fail at runtime" in crit  # real silent-failure invocation preserved


# ── End-to-end integration through the real validators ────────────────────────


def _write_skill(tmp_path, name: str, frontmatter_extra: str, body: str):
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    desc = "Test skill for the body-tool-consistency check. Use when verifying undeclared tool usage."
    (skill_dir / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "{desc}"\n{frontmatter_extra}---\n\n{body}',
        encoding="utf-8",
    )
    return skill_dir


def test_integration_skill_declared_read_body_uses_bash_is_critical(tmp_path) -> None:
    """validate_skill (comprehensive): allowed-tools=Read + fenced Bash(...) → CRITICAL."""
    skill = _write_skill(
        tmp_path,
        "tc-skill-bash",
        "allowed-tools: Read\n",
        "## Instructions\n\nRun the build:\n\n```text\nBash(npm test)\n```\n",
    )
    report = _vsc.validate_skill(skill)
    assert _has_consistency_critical(report)


def test_integration_skill_absent_field_no_consistency_critical(tmp_path) -> None:
    """validate_skill: NO allowed-tools field + fenced Bash(...) → no consistency CRITICAL."""
    skill = _write_skill(
        tmp_path,
        "tc-skill-absent",
        "",
        "## Instructions\n\nRun the build:\n\n```text\nBash(npm test)\n```\n",
    )
    report = _vsc.validate_skill(skill)
    assert not _has_consistency_critical(report)


def test_integration_agent_declared_read_body_uses_skill_is_critical(tmp_path) -> None:
    """validate_agent: tools=[Read] + fenced Skill({...}) → CRITICAL (Skill not granted)."""
    agent = tmp_path / "tc-agent.md"
    agent.write_text(
        '---\nname: tc-agent\ndescription: "Test agent for the body-tool-consistency check in agent bodies."\n'
        'tools:\n  - Read\n---\n\n## Role\n\nLoad a skill:\n\n```text\nSkill({skill: "x"})\n```\n',
        encoding="utf-8",
    )
    report = _va.validate_agent(agent)
    assert _has_consistency_critical(report)


def test_integration_agent_absent_tools_no_consistency_critical(tmp_path) -> None:
    """validate_agent: NO tools field + fenced Skill({...}) → no consistency CRITICAL (inherits all)."""
    agent = tmp_path / "tc-agent2.md"
    agent.write_text(
        '---\nname: tc-agent2\ndescription: "Test agent without a tools field for the consistency check."\n'
        '---\n\n## Role\n\nLoad a skill:\n\n```text\nSkill({skill: "x"})\n```\n',
        encoding="utf-8",
    )
    report = _va.validate_agent(agent)
    assert not _has_consistency_critical(report)


# ── CPV self-consistency regression guard ─────────────────────────────────────
#
# The v2.101.0 batch commands declared `allowed-tools: Read, Bash, Glob, Grep`
# but their bodies dispatch sub-agents via the Agent tool — a silent-failure
# bug that shipped because nothing strictly validated CPV's own commands for
# CRITICALs. CPV has since removed allowed-tools/tools from ALL its commands,
# skills, and agents (every CPV component may use all tools), so these guards
# pass by skip today — and would catch a future regression that re-introduces a
# restrictive allowed-tools/tools field on a component whose body uses a tool it
# does not grant.

import pathlib  # noqa: E402

import validate_command as _vc  # noqa: E402

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _consistency_criticals(report: object) -> list[str]:
    return [
        r.message
        for r in report.results  # type: ignore[attr-defined]
        if r.level == "CRITICAL" and any(mark in r.message for mark in _CONSISTENCY_MARKERS)
    ]


def test_cpv_own_commands_have_no_consistency_criticals() -> None:
    """Every CPV command stays consistency-clean (0 CRITICAL).

    CPV commands declare no allowed-tools (all tools allowed), so the check is
    skipped and this passes today. Regression guard for TRDD-94e06820: it would
    fail if a future change re-introduced a restrictive allowed-tools to a
    command whose body invokes a tool it does not grant — exactly the bug the
    v2.101.0 batch commands had before the field was removed.
    """
    offenders: list[str] = []
    for cmd in sorted((_PLUGIN_ROOT / "commands").glob("*.md")):
        for msg in _consistency_criticals(_vc.validate_command(cmd)):
            offenders.append(f"{cmd.name}: {msg[:130]}")
    assert not offenders, "CPV commands invoke undeclared tools:\n" + "\n".join(offenders)


def test_cpv_own_skills_have_no_consistency_criticals() -> None:
    """Every CPV skill stays consistency-clean (skills declare no allowed-tools → check skipped)."""
    offenders: list[str] = []
    for skill_md in sorted((_PLUGIN_ROOT / "skills").glob("*/SKILL.md")):
        for msg in _consistency_criticals(_vsc.validate_skill(skill_md.parent)):
            offenders.append(f"{skill_md.parent.name}: {msg[:130]}")
    assert not offenders, "CPV skills invoke undeclared tools:\n" + "\n".join(offenders)


def test_cpv_own_agents_have_no_consistency_criticals() -> None:
    """Every CPV agent stays consistency-clean (agents declare no tools → check skipped)."""
    offenders: list[str] = []
    for agent_md in sorted((_PLUGIN_ROOT / "agents").glob("*.md")):
        for msg in _consistency_criticals(_va.validate_agent(agent_md)):
            offenders.append(f"{agent_md.name}: {msg[:130]}")
    assert not offenders, "CPV agents invoke undeclared tools:\n" + "\n".join(offenders)
