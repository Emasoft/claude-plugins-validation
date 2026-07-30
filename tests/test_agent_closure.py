#!/usr/bin/env python3
"""Agent → skill closure resolution (TRDD-7KS7KP7U, spec §2/§3).

The verified gap this closes: ``validate_agent.py`` was a SINGLE-FILE validator
with no plugin root, so it structurally could not resolve a skill NAME. An agent
declaring ``skills: [real-skill, totally-nonexistent-skill-xyz]`` and invoking
``Skill({skill: "another-nonexistent-skill-abc"})`` in its body scored 100/100
with ZERO findings on v3.24.0 — a preload that silently does nothing and a
runtime invocation that silently fails, both shipping green.

Every case here is TWO-SIDED: the finding fires on the defect AND stays silent
on the legitimate sibling. A suppression test without a positive control passes
vacuously, and CPV must never call a valid agent invalid.

The severity discipline under test:

* WARNING is the ONLY non-blocking tier under ``--strict``, so every advisory is
  WARNING — never MINOR/NIT.
* A MAJOR requires the NON-VACUITY GUARD: at least one OTHER named skill of the
  same agent must have resolved, proving the roots are right. Without that
  proof, "this skill does not exist" would be a fabricated finding on a
  single-file or moved-plugin scan, so it degrades to WARNING.
* AC3 escalates only when the named skill RESOLVES — resolution is what
  distinguishes a real invocation from prose.

Hermeticity: every test either passes EXPLICIT ``roots`` / ``skills_roots`` (which
suppresses auto-resolution entirely) or passes ``home=<tmp>``. The one
end-to-end auto-resolution test uses nonce-suffixed skill names that cannot
collide with a real ``~/.claude/skills`` entry on the developer's machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_agent_closure import (  # noqa: E402
    AgentClosure,
    SkillRef,
    agent_can_load_skills_at_runtime,
    available_skills,
    body_mentions_skill_name,
    closure_files,
    extract_preloaded_skill_names,
    extract_runtime_skill_refs,
    resolve_agent_closure,
    skill_blocks_preloading,
    skill_object_invocation_matcher,
    skill_search_roots,
    split_skill_ref_name,
)
from cpv_tool_permission_match import declared_tool_names, granted_builtin_tools  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_agent import validate_agent  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BLOCKING = ("CRITICAL", "MAJOR", "MINOR", "NIT")


def _levels(report: ValidationReport, needle: str) -> list[str]:
    """Levels of every FINDING whose message contains ``needle``.

    PASSED / INFO are excluded: the pre-existing ``'skills' field valid: [...]``
    PASSED line echoes every declared skill name, so including it would make
    every "exactly one finding for this name" assertion unreadable.
    """
    return [r.level for r in report.results if needle in r.message and r.level not in ("PASSED", "INFO")]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_skill(
    skills_root: Path,
    name: str,
    body: str = "Do the thing.",
    fm_name: str | None = None,
    *,
    extra_fm: str = "",
) -> Path:
    """Create ``<skills_root>/<name>/SKILL.md`` and return the SKILL.md path."""
    declared = name if fm_name is None else fm_name
    tail = f"{extra_fm}\n" if extra_fm else ""
    return _write(
        skills_root / name / "SKILL.md",
        f"---\nname: {declared}\ndescription: Fixture skill {name} used by the closure tests.\n{tail}---\n\n{body}\n",
    )


def _make_plugin(root: Path, plugin_name: str = "probe-plug") -> Path:
    """Create a minimal plugin tree and return its root."""
    _write(
        root / ".claude-plugin" / "plugin.json",
        '{"name": "%s", "version": "0.1.0", "description": "closure fixture"}\n' % plugin_name,
    )
    (root / "skills").mkdir(parents=True, exist_ok=True)
    (root / "agents").mkdir(parents=True, exist_ok=True)
    return root


# Long enough to clear MIN_BODY_CHARS (100) so no unrelated "body is very short"
# MINOR pollutes the "this agent has zero blocking findings" assertions.
_AGENT_TAIL = """
# Probe Agent

You are the probe agent. You coordinate the fixture work over the skills the
closure resolver is expected to find, and you report what you did.

## Workflow

1. Read the file the caller named.
2. Report the outcome in one line.
"""


def _make_agent(
    plugin_root: Path,
    name: str = "probe-agent",
    *,
    tools: str | None = "Read, Grep",
    disallowed: str | None = None,
    skills: list[str] | None = None,
    body: str = "",
) -> Path:
    fm = [
        "---",
        f"name: {name}",
        f"description: Probe agent {name} exercising the closure resolver end to end.",
    ]
    if tools is not None:
        fm.append(f"tools: {tools}")
    if disallowed is not None:
        fm.append(f"disallowedTools: {disallowed}")
    if skills is not None:
        fm.append("skills: [" + ", ".join(skills) + "]")
    fm.append("---")
    return _write(plugin_root / "agents" / f"{name}.md", "\n".join(fm) + "\n" + body + _AGENT_TAIL)


# ===========================================================================
# The `Skill` tool gate (spec §1) — getting this backwards flags the CORRECT
# dynamic-router pattern as a defect.
# ===========================================================================


class TestSkillToolGate:
    def test_no_tools_field_means_the_gate_is_open(self):
        """No `tools:` field == inherits every session tool, so the gate is OPEN."""
        assert agent_can_load_skills_at_runtime({"name": "a"}) is True

    def test_tools_granting_skill_opens_the_gate(self):
        assert agent_can_load_skills_at_runtime({"tools": "Read, Skill, Grep"}) is True

    def test_tools_granting_skill_as_a_yaml_list_opens_the_gate(self):
        assert agent_can_load_skills_at_runtime({"tools": ["Read", "Skill"]}) is True

    def test_tools_without_skill_shuts_the_gate(self):
        """Positive control for the two tests above: a real denial IS detected."""
        assert agent_can_load_skills_at_runtime({"tools": "Read, Grep"}) is False

    def test_empty_tools_list_shuts_the_gate(self):
        assert agent_can_load_skills_at_runtime({"tools": []}) is False

    def test_null_tools_value_is_treated_as_absent(self):
        """`tools:` with no value parses as None — the FP-safe reading is 'absent'."""
        assert agent_can_load_skills_at_runtime({"tools": None}) is True

    def test_a_specifier_carrying_skill_rule_still_grants_the_tool(self):
        """The tool NAME is what the gate turns on, not an exact bare token."""
        assert agent_can_load_skills_at_runtime({"tools": "Read, Skill(foo)"}) is True

    def test_disallowed_tools_shuts_the_gate_even_with_no_tools_field(self):
        """sub-agents.md: omit Skill from `tools` OR add it to `disallowedTools`.
        `disallowedTools` is applied FIRST, so deny wins over an absent `tools`."""
        assert agent_can_load_skills_at_runtime({"disallowedTools": ["Skill"]}) is False

    def test_disallowed_tools_shuts_the_gate_even_when_tools_grants_skill(self):
        assert agent_can_load_skills_at_runtime({"tools": "Read, Skill", "disallowedTools": ["Skill"]}) is False

    def test_disallowing_an_unrelated_tool_leaves_the_gate_open(self):
        """Positive control: the deny check must key on Skill, not on any denial."""
        assert agent_can_load_skills_at_runtime({"disallowedTools": ["Bash"]}) is True

    def test_disallowing_edit_does_not_deny_read_shaped_grants_of_skill(self):
        """A DENY list must not expand the documented cross-tool GRANTS (Edit→Read,
        Bash→Monitor); denying Edit denies Edit."""
        assert agent_can_load_skills_at_runtime({"tools": "Skill", "disallowedTools": ["Edit"]}) is True


# ===========================================================================
# Reference extraction — the ONE `Skill()` grammar
# ===========================================================================


class TestDeclaredToolNames:
    """The deny-list primitive the gate needs: NAMES, with no grant expansion."""

    def test_drops_a_specifier(self):
        assert declared_tool_names(["Bash(git:*)"]) == {"Bash"}

    def test_resolves_an_alias(self):
        assert declared_tool_names(["Task"]) == {"Agent"}

    def test_does_not_expand_cross_tool_grants(self):
        """`granted_builtin_tools` adds Read for Edit — correct for a GRANT list,
        wrong for a DENY list, which is exactly why this helper exists."""
        assert declared_tool_names(["Edit"]) == {"Edit"}
        assert "Read" in granted_builtin_tools(["Edit"])

    def test_ignores_mcp_rules(self):
        assert declared_tool_names(["mcp__github__create_issue"]) == set()


class TestPreloadExtraction:
    def test_extracts_the_skills_list_in_order(self):
        assert extract_preloaded_skill_names({"skills": ["b-one", "a-two"]}) == ["b-one", "a-two"]

    def test_missing_field_yields_nothing(self):
        assert extract_preloaded_skill_names({"name": "a"}) == []

    def test_non_list_value_yields_nothing(self):
        assert extract_preloaded_skill_names({"skills": "one-skill"}) == []

    def test_non_string_and_blank_items_are_skipped_but_valid_ones_kept(self):
        assert extract_preloaded_skill_names({"skills": ["ok-skill", 7, "   ", None]}) == ["ok-skill"]

    def test_duplicates_collapse(self):
        assert extract_preloaded_skill_names({"skills": ["dup-skill", "dup-skill"]}) == ["dup-skill"]


class TestRuntimeRefExtraction:
    def test_object_form_is_extracted_with_its_line(self):
        body = "intro\nCall Skill({skill: \"my-skill\"}) now.\n"
        assert extract_runtime_skill_refs(body) == [("my-skill", 2)]

    def test_namespaced_object_form_is_extracted_verbatim(self):
        body = 'Skill({skill: "cpv:cpv-fix-validation"})\n'
        assert extract_runtime_skill_refs(body) == [("cpv:cpv-fix-validation", 1)]

    def test_bare_call_form_is_extracted(self):
        body = "Run Skill(cpv:cpv-fix-validation) to fix.\n"
        assert extract_runtime_skill_refs(body) == [("cpv:cpv-fix-validation", 1)]

    def test_bare_call_with_trailing_arguments_is_extracted(self):
        body = "Run Skill(my-plugin:my-skill --json) now.\n"
        assert extract_runtime_skill_refs(body) == [("my-plugin:my-skill", 1)]

    def test_a_ref_inside_a_fenced_block_is_an_illustration_not_an_invocation(self):
        body = "prose\n```markdown\nSkill({skill: \"illustrated-skill\"})\n```\nafter\n"
        assert extract_runtime_skill_refs(body) == []

    def test_positive_control_the_same_ref_outside_the_fence_is_extracted(self):
        """Without this control the fence test above would pass vacuously."""
        body = "prose\nSkill({skill: \"illustrated-skill\"})\nafter\n"
        assert extract_runtime_skill_refs(body) == [("illustrated-skill", 2)]

    def test_prose_plural_skill_s_is_not_a_ref(self):
        """`Skill(s)` is English, not an invocation — the bare form needs a real name."""
        assert extract_runtime_skill_refs("Skill(s) are loaded dynamically.\n") == []

    def test_bare_skill_tool_mention_is_not_a_ref(self):
        assert extract_runtime_skill_refs("Use the `Skill()` tool to load them.\n") == []

    def test_skills_frontmatter_style_line_is_not_a_ref(self):
        assert extract_runtime_skill_refs("skills:\n  - not-a-runtime-ref\n") == []

    def test_on_skill_invoke_monitor_target_is_not_a_ref(self):
        assert extract_runtime_skill_refs('monitors: on-skill-invoke: "some-skill"\n') == []

    def test_split_skill_ref_name_separates_the_namespace(self):
        assert split_skill_ref_name("cpv:cpv-fix-validation") == ("cpv-fix-validation", "cpv")
        assert split_skill_ref_name("plain-skill") == ("plain-skill", None)


class TestPromotedGrammarIsTheOnlyCopy:
    def test_matcher_recognises_both_quote_styles(self):
        matcher = skill_object_invocation_matcher("my-skill")
        assert matcher.search('Skill({skill: "my-skill"})')
        assert matcher.search("Skill({skill: 'my-skill'})")

    def test_matcher_does_not_match_a_different_skill(self):
        assert skill_object_invocation_matcher("my-skill").search('Skill({skill: "other-skill"})') is None

    def test_matcher_escapes_regex_metacharacters_in_the_name(self):
        """A name is never a regex — a `.` must not become a wildcard."""
        matcher = skill_object_invocation_matcher("a.b")
        assert matcher.search('Skill({skill: "a.b"})')
        assert matcher.search('Skill({skill: "axb"})') is None

    def test_the_comprehensive_skill_validator_imports_the_grammar_back(self):
        """One grammar, one definition: the old copy must be gone (spec §2)."""
        source = (scripts_dir / "validate_skill_comprehensive.py").read_text(encoding="utf-8")
        assert "skill_object_invocation_matcher" in source, (
            "validate_skill_comprehensive must import the promoted grammar from cpv_agent_closure"
        )
        assert r"""skill\s*:\s*["']""" not in source, (
            "the local Skill({skill: ...}) regex is a second copy of the promoted grammar"
        )

    def test_the_self_recursion_detector_still_fires_after_the_promotion(self, tmp_path):
        """Positive control: promoting the grammar must not blind its original caller."""
        from validate_skill_comprehensive import validate_skill

        skill = tmp_path / "recursive-skill"
        _write(
            skill / "SKILL.md",
            "---\nname: recursive-skill\ndescription: A forked skill that invokes itself, "
            "which CC v2.1.145 fixed an infinite loop for.\ncontext: fork\n---\n\n"
            '# Recursive\n\nCall Skill({skill: "recursive-skill"}) again.\n',
        )
        report = validate_skill(skill)
        assert any("invokes itself" in r.message for r in report.results)

    def test_the_self_recursion_detector_stays_silent_on_a_different_skill(self, tmp_path):
        from validate_skill_comprehensive import validate_skill

        skill = tmp_path / "polite-skill"
        _write(
            skill / "SKILL.md",
            "---\nname: polite-skill\ndescription: A forked skill that delegates to a different "
            "skill instead of recursing into itself.\ncontext: fork\n---\n\n"
            '# Polite\n\nCall Skill({skill: "other-skill"}) instead.\n',
        )
        report = validate_skill(skill)
        assert not any("invokes itself" in r.message for r in report.results)


# ===========================================================================
# Root resolution + the skill inventory
# ===========================================================================


class TestSkillSearchRoots:
    def test_plugin_skills_dir_is_the_first_root(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        agent = _make_agent(plugin)
        roots = skill_search_roots(agent, home=tmp_path / "nohome")
        assert roots and roots[0] == (plugin / "skills").resolve()

    def test_a_manifestless_source_still_resolves_via_the_agents_sibling(self, tmp_path):
        """CPV scans UNINSTALLED sources — a missing manifest must not blind resolution."""
        root = tmp_path / "bare"
        (root / "skills").mkdir(parents=True)
        agent = _write(root / "agents" / "a.md", "---\nname: a\ndescription: bare agent fixture.\n---\n\nbody\n")
        roots = skill_search_roots(agent, home=tmp_path / "nohome")
        assert (root / "skills").resolve() in roots

    def test_project_scope_claude_skills_is_included(self, tmp_path):
        proj = tmp_path / "proj"
        (proj / ".claude" / "skills").mkdir(parents=True)
        agent = _write(
            proj / ".claude" / "agents" / "a.md",
            "---\nname: a\ndescription: project scope agent fixture.\n---\n\nbody\n",
        )
        roots = skill_search_roots(agent, home=tmp_path / "nohome")
        assert (proj / ".claude" / "skills").resolve() in roots

    def test_user_scope_home_skills_is_included(self, tmp_path):
        home = tmp_path / "home"
        (home / ".claude" / "skills").mkdir(parents=True)
        plugin = _make_plugin(tmp_path / "plug")
        agent = _make_agent(plugin)
        assert (home / ".claude" / "skills").resolve() in skill_search_roots(agent, home=home)

    def test_nonexistent_candidates_are_excluded(self, tmp_path):
        """Positive control for the inclusion tests: absence is really detected."""
        home = tmp_path / "home-without-skills"
        plugin = _make_plugin(tmp_path / "plug")
        agent = _make_agent(plugin)
        roots = skill_search_roots(agent, home=home)
        assert (home / ".claude" / "skills").resolve() not in roots

    def test_roots_are_deduplicated_by_resolved_path(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        agent = _make_agent(plugin)
        roots = skill_search_roots(agent, plugin_root=plugin, project_root=plugin, home=tmp_path / "nohome")
        assert len(roots) == len(set(roots))


class TestAvailableSkills:
    def test_indexes_directories_holding_a_skill_md(self, tmp_path):
        root = tmp_path / "skills"
        _make_skill(root, "alpha-skill")
        assert set(available_skills([root])) == {"alpha-skill"}

    def test_a_directory_without_a_skill_md_is_not_a_skill(self, tmp_path):
        root = tmp_path / "skills"
        _make_skill(root, "alpha-skill")
        (root / "not-a-skill").mkdir()
        assert "not-a-skill" not in available_skills([root])

    def test_the_frontmatter_name_is_indexed_as_an_alias(self, tmp_path):
        """Resolving under either spelling can never FABRICATE a 'does not exist'."""
        root = tmp_path / "skills"
        _make_skill(root, "dir-name-skill", fm_name="declared-name-skill")
        index = available_skills([root])
        assert "dir-name-skill" in index
        assert "declared-name-skill" in index

    def test_the_earlier_root_wins(self, tmp_path):
        first, second = tmp_path / "a", tmp_path / "b"
        want = _make_skill(first, "shadowed-skill")
        _make_skill(second, "shadowed-skill")
        assert available_skills([first, second])["shadowed-skill"] == want

    def test_no_roots_yields_an_empty_index(self):
        assert available_skills([]) == {}

    def test_a_missing_root_is_skipped_without_raising(self, tmp_path):
        assert available_skills([tmp_path / "does-not-exist"]) == {}


# ===========================================================================
# resolve_agent_closure
# ===========================================================================


class TestResolveAgentClosure:
    def test_returns_the_pinned_dataclasses(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, skills=["real-skill"])
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"])
        assert isinstance(closure, AgentClosure)
        assert all(isinstance(ref, SkillRef) for ref in closure.refs)
        assert closure.agent_path == str(agent)
        assert closure.skill_roots == (str((plugin / "skills")),)

    def test_a_resolving_preload_is_reachable_and_resolved(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        want = _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, skills=["real-skill"])
        ref = next(r for r in resolve_agent_closure(agent, roots=[plugin / "skills"]).refs if r.name == "real-skill")
        assert ref.origin == "preload"
        assert ref.reachable is True
        assert ref.resolved_path == str(want)
        assert ref.line == 0

    def test_a_preload_is_reachable_even_when_the_gate_is_shut(self, tmp_path):
        """A preload is injected at startup — the `Skill` grant is irrelevant to it."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read, Grep", skills=["real-skill"])
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"])
        assert closure.can_load_at_runtime is False
        assert all(r.reachable for r in closure.refs if r.origin == "preload")

    def test_a_runtime_ref_is_unreachable_when_the_gate_is_shut(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "called-skill")
        agent = _make_agent(plugin, tools="Read, Grep", body='Call Skill({skill: "called-skill"}).\n')
        ref = next(r for r in resolve_agent_closure(agent, roots=[plugin / "skills"]).refs if r.origin == "runtime")
        assert ref.reachable is False

    def test_a_runtime_ref_is_unreachable_when_disallowed_tools_denies_skill(self, tmp_path):
        """Even with NO `tools:` field: `disallowedTools` is applied first."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "called-skill")
        agent = _make_agent(
            plugin, tools=None, disallowed="[Skill]", body='Call Skill({skill: "called-skill"}).\n'
        )
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"])
        assert closure.can_load_at_runtime is False
        assert next(r for r in closure.refs if r.origin == "runtime").reachable is False

    def test_the_same_runtime_ref_is_reachable_when_the_gate_is_open(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "called-skill")
        agent = _make_agent(plugin, tools="Read, Skill", body='Call Skill({skill: "called-skill"}).\n')
        ref = next(r for r in resolve_agent_closure(agent, roots=[plugin / "skills"]).refs if r.origin == "runtime")
        assert ref.reachable is True

    def test_a_runtime_ref_line_is_file_relative(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "called-skill")
        agent = _make_agent(plugin, tools="Skill", body='Call Skill({skill: "called-skill"}).\n')
        ref = next(r for r in resolve_agent_closure(agent, roots=[plugin / "skills"]).refs if r.origin == "runtime")
        text = agent.read_text(encoding="utf-8").splitlines()
        assert "called-skill" in text[ref.line - 1]

    def test_tools_declared_is_none_when_the_field_is_absent(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        agent = _make_agent(plugin, tools=None)
        assert resolve_agent_closure(agent, roots=[plugin / "skills"]).tools_declared is None

    def test_tools_declared_is_populated_when_the_field_is_present(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        agent = _make_agent(plugin, tools="Read, Grep")
        assert resolve_agent_closure(agent, roots=[plugin / "skills"]).tools_declared == ("Read", "Grep")

    def test_a_namespaced_ref_resolves_on_the_bare_name(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Skill", body='Call Skill({skill: "probe-plug:real-skill"}).\n')
        ref = next(r for r in resolve_agent_closure(agent, roots=[plugin / "skills"]).refs if r.origin == "runtime")
        assert ref.name == "real-skill"
        assert ref.namespace == "probe-plug"
        assert ref.resolved_path is not None

    def test_a_foreign_namespace_is_recorded_unresolved_without_raising(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        agent = _make_agent(plugin, tools="Skill", body='Call Skill({skill: "other-plugin:their-skill"}).\n')
        ref = next(r for r in resolve_agent_closure(agent, roots=[plugin / "skills"]).refs if r.origin == "runtime")
        assert ref.namespace == "other-plugin"
        assert ref.resolved_path is None

    def test_a_transitive_ref_is_discovered_through_a_reachable_skill(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "parent-skill", body='Then Skill({skill: "child-skill"}).')
        _make_skill(plugin / "skills", "child-skill")
        agent = _make_agent(plugin, tools="Skill", skills=["parent-skill"])
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"])
        child = next(r for r in closure.refs if r.name == "child-skill")
        assert child.origin == "transitive"
        assert child.reachable is True
        assert child.source_file.endswith("parent-skill/SKILL.md")

    def test_a_transitive_ref_is_unreachable_when_the_gate_is_shut(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "parent-skill", body='Then Skill({skill: "child-skill"}).')
        _make_skill(plugin / "skills", "child-skill")
        agent = _make_agent(plugin, tools="Read", skills=["parent-skill"])
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"])
        child = next(r for r in closure.refs if r.name == "child-skill")
        assert child.reachable is False

    def test_a_cycle_terminates(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "a-skill", body='Then Skill({skill: "b-skill"}).')
        _make_skill(plugin / "skills", "b-skill", body='Then Skill({skill: "a-skill"}).')
        agent = _make_agent(plugin, tools="Skill", skills=["a-skill"])
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"])
        assert {r.name for r in closure.refs} == {"a-skill", "b-skill"}

    def test_depth_is_bounded(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        for idx in range(1, 6):
            _make_skill(plugin / "skills", f"s{idx}-skill", body='Then Skill({skill: "s%d-skill"}).' % (idx + 1))
        _make_skill(plugin / "skills", "s6-skill")
        agent = _make_agent(plugin, tools="Skill", skills=["s1-skill"])
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"], max_depth=3)
        names = {r.name for r in closure.refs}
        assert names == {"s1-skill", "s2-skill", "s3-skill"}
        assert closure.max_depth_reached == 3

    def test_a_deeper_max_depth_reaches_further(self, tmp_path):
        """Positive control: the bound above is the bound, not an accident."""
        plugin = _make_plugin(tmp_path / "plug")
        for idx in range(1, 6):
            _make_skill(plugin / "skills", f"s{idx}-skill", body='Then Skill({skill: "s%d-skill"}).' % (idx + 1))
        _make_skill(plugin / "skills", "s6-skill")
        agent = _make_agent(plugin, tools="Skill", skills=["s1-skill"])
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"], max_depth=5)
        assert "s5-skill" in {r.name for r in closure.refs}

    def test_empty_roots_leave_every_ref_unresolved_without_raising(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, skills=["real-skill"])
        closure = resolve_agent_closure(agent, roots=[])
        assert closure.ambient == ()
        assert all(r.resolved_path is None for r in closure.refs)

    def test_ambient_lists_the_palette_present_in_the_roots(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "one-skill")
        _make_skill(plugin / "skills", "two-skill")
        agent = _make_agent(plugin)
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"])
        assert set(closure.ambient) == {"one-skill", "two-skill"}

    def test_an_unreadable_agent_yields_no_refs_instead_of_raising(self, tmp_path):
        """Fail safe on I/O: an unreadable file yields no ref, never an exception."""
        plugin = _make_plugin(tmp_path / "plug")
        missing = plugin / "agents" / "not-there.md"
        closure = resolve_agent_closure(missing, roots=[plugin / "skills"])
        assert closure.refs == ()
        assert closure.can_load_at_runtime is True

    def test_a_binary_agent_file_yields_no_refs_instead_of_raising(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        blob = plugin / "agents" / "blob.md"
        blob.write_bytes(b"\xff\xfe\x00\x01not utf-8")
        assert resolve_agent_closure(blob, roots=[plugin / "skills"]).refs == ()

    def test_an_unreadable_skill_body_does_not_break_transitive_walking(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        bad = _make_skill(plugin / "skills", "bad-skill")
        bad.write_bytes(b"\xff\xfe\x00\x01")
        agent = _make_agent(plugin, tools="Skill", skills=["bad-skill"])
        closure = resolve_agent_closure(agent, roots=[plugin / "skills"])
        assert {r.name for r in closure.refs} == {"bad-skill"}


class TestClosureFiles:
    def test_collects_the_skill_md_and_its_references_and_scripts(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        skill_md = _make_skill(plugin / "skills", "real-skill")
        ref_doc = _write(plugin / "skills" / "real-skill" / "references" / "notes.md", "notes\n")
        script = _write(plugin / "skills" / "real-skill" / "scripts" / "run.py", "print(1)\n")
        agent = _make_agent(plugin, skills=["real-skill"])
        files = closure_files(resolve_agent_closure(agent, roots=[plugin / "skills"]))
        assert skill_md in files
        assert ref_doc in files
        assert script in files

    def test_an_unreachable_skill_is_excluded(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        reachable = _make_skill(plugin / "skills", "preloaded-skill")
        unreachable = _make_skill(plugin / "skills", "dead-skill")
        agent = _make_agent(
            plugin,
            tools="Read",
            skills=["preloaded-skill"],
            body='Call Skill({skill: "dead-skill"}).\n',
        )
        files = closure_files(resolve_agent_closure(agent, roots=[plugin / "skills"]))
        assert reachable in files
        assert unreachable not in files

    def test_no_duplicates_and_deterministic_order(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Skill", skills=["real-skill"], body='Skill({skill: "real-skill"})\n')
        files = closure_files(resolve_agent_closure(agent, roots=[plugin / "skills"]))
        assert len(files) == len(set(files))
        assert files == sorted(files)


# ===========================================================================
# AC1–AC4 in validate_agent  (spec §3)
# ===========================================================================


def _probe_plugin(tmp_path: Path) -> tuple[Path, Path]:
    """The fixture that proved the gap: one real preload, one bogus preload, one
    bogus runtime invocation, and a `tools:` field that denies `Skill`."""
    plugin = _make_plugin(tmp_path / "plug")
    _make_skill(plugin / "skills", "real-skill")
    agent = _make_agent(
        plugin,
        tools="Read, Grep",
        skills=["real-skill", "totally-nonexistent-skill-xyz"],
        body='When you need the other capability, call Skill({skill: "another-nonexistent-skill-abc"}).\n',
    )
    return plugin, agent


class TestAC1PreloadResolution:
    def test_a_nonexistent_preload_is_a_major_when_a_sibling_resolved(self, tmp_path):
        plugin, agent = _probe_plugin(tmp_path)
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "totally-nonexistent-skill-xyz") == ["MAJOR"]

    def test_a_resolving_preload_produces_no_finding(self, tmp_path):
        """Two-sided sibling: the legitimate preload must stay silent."""
        plugin, agent = _probe_plugin(tmp_path)
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert [lvl for lvl in _levels(report, "'real-skill'") if lvl in BLOCKING] == []

    def test_an_all_resolving_agent_gains_no_closure_finding(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read, Grep", skills=["real-skill"], name="clean-agent")
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert [r.level for r in report.results if r.level in BLOCKING] == []

    def test_the_non_vacuity_guard_degrades_the_major_to_a_warning(self, tmp_path):
        """Roots pointing nowhere means the ROOTS are wrong, not the agent."""
        plugin, agent = _probe_plugin(tmp_path)
        empty = tmp_path / "empty-roots"
        empty.mkdir()
        report = validate_agent(agent, skills_roots=[empty])
        assert _levels(report, "totally-nonexistent-skill-xyz") == ["WARNING"]
        assert [r.level for r in report.results if r.level in BLOCKING] == []

    def test_a_foreign_namespaced_preload_never_produces_a_finding(self, tmp_path):
        """It may legitimately live in another installed plugin."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill", "other-plugin:their-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "their-skill") == []

    def test_a_preload_namespaced_to_this_plugin_must_still_resolve(self, tmp_path):
        """Positive control: the OWN namespace is not an escape hatch."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill", "probe-plug:missing-local-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "missing-local-skill") == ["MAJOR"]


class TestAC2RuntimeResolution:
    def test_a_nonexistent_runtime_skill_is_a_major_when_a_sibling_resolved(self, tmp_path):
        plugin, agent = _probe_plugin(tmp_path)
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "another-nonexistent-skill-abc") == ["MAJOR"]

    def test_a_resolving_runtime_skill_produces_no_finding(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        _make_skill(plugin / "skills", "called-skill")
        agent = _make_agent(
            plugin, tools="Read, Skill", skills=["real-skill"], body='Call Skill({skill: "called-skill"}).\n'
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert [lvl for lvl in _levels(report, "called-skill") if lvl in BLOCKING] == []

    def test_the_guard_degrades_the_runtime_major_to_a_warning(self, tmp_path):
        plugin, agent = _probe_plugin(tmp_path)
        empty = tmp_path / "empty-roots"
        empty.mkdir()
        report = validate_agent(agent, skills_roots=[empty])
        assert _levels(report, "another-nonexistent-skill-abc") == ["WARNING"]

    def test_a_fenced_illustration_is_never_reported(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(
            plugin,
            tools="Read, Skill",
            skills=["real-skill"],
            body='Example:\n\n```markdown\nSkill({skill: "documented-only-skill"})\n```\n',
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "documented-only-skill") == []

    def test_positive_control_the_same_ref_outside_a_fence_is_reported(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(
            plugin,
            tools="Read, Skill",
            skills=["real-skill"],
            body='Call Skill({skill: "documented-only-skill"}) now.\n',
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "documented-only-skill") == ["MAJOR"]

    def test_a_transitive_broken_ref_is_not_reported_on_the_agent(self, tmp_path):
        """A skill's own broken ref belongs to the SKILL's report, not the agent's."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill", body='Then Skill({skill: "gone-from-the-skill"}).')
        agent = _make_agent(plugin, tools="Read, Skill", skills=["real-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "gone-from-the-skill") == []


class TestAC3InvocationAgainstAShutGate:
    def test_a_resolving_invocation_against_a_shut_gate_is_a_major(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        _make_skill(plugin / "skills", "called-skill")
        agent = _make_agent(
            plugin, tools="Read, Grep", skills=["real-skill"], body='Call Skill({skill: "called-skill"}).\n'
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert "MAJOR" in _levels(report, "cannot use the 'Skill' tool")

    def test_an_unresolved_invocation_does_not_escalate_on_the_gate_alone(self, tmp_path):
        """Unresolved == prose as far as we can prove; the existing WARNING stays."""
        plugin, agent = _probe_plugin(tmp_path)
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "cannot use the 'Skill' tool") == []
        assert any("body mentions the tool 'Skill'" in r.message for r in report.results)

    def test_an_open_gate_produces_no_ac3_finding(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "called-skill")
        agent = _make_agent(plugin, tools="Read, Skill", body='Call Skill({skill: "called-skill"}).\n')
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "cannot use the 'Skill' tool") == []

    def test_a_disallowed_tools_denial_fires_ac3_and_names_that_cause(self, tmp_path):
        """The remedy differs from the omitted-grant case, so the message must too."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "called-skill")
        agent = _make_agent(
            plugin, tools=None, disallowed="[Skill]", body='Call Skill({skill: "called-skill"}).\n'
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        hits = [r for r in report.results if "cannot use the 'Skill' tool" in r.message]
        assert [r.level for r in hits] == ["MAJOR"]
        assert "disallowedTools" in hits[0].message

    def test_an_absent_tools_field_produces_no_ac3_finding(self, tmp_path):
        """The dynamic-router pattern is CORRECT — flagging it would be the defect."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "called-skill")
        agent = _make_agent(plugin, tools=None, body='Call Skill({skill: "called-skill"}).\n')
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "cannot use the 'Skill' tool") == []
        assert [r.level for r in report.results if r.level in BLOCKING] == []


_AC4 = "is preloaded but the body never mentions it"


class TestAC4UnusedPreload:
    def test_an_unmentioned_preload_with_an_open_gate_is_a_warning(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read, Skill", skills=["real-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC4) == ["WARNING"]

    def test_a_skill_invoked_from_the_body_is_not_flagged(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(
            plugin, tools="Read, Skill", skills=["real-skill"], body='Call Skill({skill: "real-skill"}).\n'
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC4) == []

    def test_a_routing_table_row_naming_the_skill_counts_as_usage(self, tmp_path):
        """An ALL-IN-ONE agent preloads every skill and routes from a prose table —
        that IS usage, so demanding a Skill() call would flag the canonical shape."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "foo-skill")
        agent = _make_agent(
            plugin,
            tools="Read, Skill",
            skills=["foo-skill"],
            body="| skill | when |\n|---|---|\n| foo-skill | when a finding is mechanical |\n",
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC4) == []

    def test_a_prose_mention_counts_as_usage(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "foo-skill")
        agent = _make_agent(
            plugin,
            tools="Read, Skill",
            skills=["foo-skill"],
            body="Delegate mechanical findings to foo-skill and report back.\n",
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC4) == []

    def test_positive_control_a_body_that_never_names_the_skill_still_fires(self, tmp_path):
        """Without this control the two mention tests above would pass vacuously."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "foo-skill")
        agent = _make_agent(
            plugin,
            tools="Read, Skill",
            skills=["foo-skill"],
            body="| skill | when |\n|---|---|\n| something-else | when a finding is mechanical |\n",
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC4) == ["WARNING"]

    def test_a_mention_only_inside_a_fence_is_an_illustration_not_routing(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "foo-skill")
        agent = _make_agent(
            plugin,
            tools="Read, Skill",
            skills=["foo-skill"],
            body="Example frontmatter:\n\n```yaml\nskills: [foo-skill]\n```\n",
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC4) == ["WARNING"]

    def test_a_used_preload_matches_across_the_namespace(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(
            plugin, tools="Read, Skill", skills=["real-skill"], body='Call Skill({skill: "probe-plug:real-skill"}).\n'
        )
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC4) == []

    def test_a_shut_gate_makes_the_preload_the_only_access_so_it_is_not_flagged(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read, Grep", skills=["real-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC4) == []

    def test_an_unresolved_preload_is_not_double_reported_by_ac4(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read, Skill", skills=["real-skill", "missing-skill-abc"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, f"'missing-skill-abc' {_AC4}") == []


_AC5 = "which cannot be preloaded"


class TestAC5UnpreloadableSkill:
    """sub-agents.md: "You can't preload skills that set
    `disable-model-invocation: true`, since preloading draws from the same set of
    skills Claude can invoke. This includes the bundled `/verify` and
    `/code-review` skills." Such a preload is silently dropped."""

    def test_a_preload_of_a_model_invocation_disabled_skill_is_a_major(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill", extra_fm="disable-model-invocation: true")
        agent = _make_agent(plugin, tools="Read", skills=["manual-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == ["MAJOR"]

    def test_the_same_preload_without_the_flag_is_silent(self, tmp_path):
        """Two-sided: same agent, same preload, field ABSENT."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill")
        agent = _make_agent(plugin, tools="Read", skills=["manual-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == []

    def test_the_flag_set_false_is_silent(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill", extra_fm="disable-model-invocation: false")
        agent = _make_agent(plugin, tools="Read", skills=["manual-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == []

    def test_an_accepted_string_spelling_of_true_also_fires(self, tmp_path):
        """CC accepts yes/on/1 as frontmatter booleans, so `is True` would miss it."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill", extra_fm='disable-model-invocation: "yes"')
        agent = _make_agent(plugin, tools="Read", skills=["manual-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == ["MAJOR"]

    def test_ac5_needs_no_non_vacuity_guard(self, tmp_path):
        """Unlike AC1/AC2 the proof is POSITIVE — we read the skill's own
        frontmatter — so a MAJOR is right even when NO other skill resolved."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill", extra_fm="disable-model-invocation: true")
        agent = _make_agent(plugin, tools="Read", skills=["manual-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == ["MAJOR"]
        # The ONLY named skill of this agent is the unpreloadable one, so nothing
        # else could have satisfied a guard.
        assert _levels(report, "no such skill exists") == []

    def test_preloading_the_bundled_verify_skill_fires_ac5_not_ac1(self, tmp_path):
        """`verify` is never in a plugin's skills/, so AC1 would send the author
        hunting for a missing file instead of naming the real rule."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill", "verify"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "'verify', which cannot be preloaded") == ["MAJOR"]
        assert _levels(report, "preloads 'verify' but no such skill exists") == []

    def test_preloading_the_bundled_code_review_skill_fires_ac5(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill", "code-review"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "'code-review', which cannot be preloaded") == ["MAJOR"]

    def test_a_locally_shipped_skill_of_the_bundled_name_is_only_a_warning(self, tmp_path):
        """A plugin MAY ship `skills/verify/`. Which one a preload picks is not
        documented, so calling that agent INVALID would risk failing a valid
        plugin — the collision is reported, never blocked."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "verify")
        agent = _make_agent(plugin, tools="Read", skills=["verify"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "preloads 'verify'") == ["WARNING"]
        assert [r.level for r in report.results if r.level in BLOCKING] == []

    def test_positive_control_a_flagged_local_skill_of_that_name_still_majors(self, tmp_path):
        """The FLAG is positive proof, so it is judged before the name inference."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "verify", extra_fm="disable-model-invocation: true")
        agent = _make_agent(plugin, tools="Read", skills=["verify"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == ["MAJOR"]

    def test_a_similarly_named_local_skill_is_not_treated_as_bundled(self, tmp_path):
        """Positive control for the bundled list: only the exact names are special."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "verify-plugin")
        agent = _make_agent(plugin, tools="Read", skills=["verify-plugin"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == []

    def test_a_foreign_namespaced_preload_is_not_judged_by_a_local_namesake(self, tmp_path):
        """`other-plugin:manual-skill` names ANOTHER plugin's skill; a local skill
        of the same bare name is not evidence about that reference."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill", extra_fm="disable-model-invocation: true")
        agent = _make_agent(plugin, tools="Read", skills=["other-plugin:manual-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == []

    def test_positive_control_the_same_preload_without_a_namespace_majors(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill", extra_fm="disable-model-invocation: true")
        agent = _make_agent(plugin, tools="Read", skills=["manual-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == ["MAJOR"]

    def test_a_foreign_namespaced_bundled_name_is_not_flagged(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill", "other-plugin:verify"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "cannot be preloaded") == []

    def test_an_own_namespaced_preload_is_still_judged(self, tmp_path):
        """The OWN namespace is not an escape hatch (same rule as AC1)."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill", extra_fm="disable-model-invocation: true")
        agent = _make_agent(plugin, tools="Read", skills=["probe-plug:manual-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == ["MAJOR"]

    def test_ac4_does_not_pile_on_top_of_ac5(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill", extra_fm="disable-model-invocation: true")
        agent = _make_agent(plugin, tools="Read, Skill", skills=["manual-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == ["MAJOR"]
        assert _levels(report, _AC4) == []

    def test_a_runtime_invocation_of_such_a_skill_is_not_ac5(self, tmp_path):
        """AC5 is about PRELOADING. `disable-model-invocation` does block a model
        invocation too, but that is the SKILL's own reachability finding — the
        agent-side rule the docs state is specifically about the preload set."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "manual-skill", extra_fm="disable-model-invocation: true")
        agent = _make_agent(plugin, tools="Read, Skill", body='Call Skill({skill: "manual-skill"}).\n')
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, _AC5) == []


class TestSkillBlocksPreloading:
    def test_a_normal_skill_is_preloadable(self, tmp_path):
        skill_md = _make_skill(tmp_path / "skills", "plain-skill")
        assert skill_blocks_preloading("plain-skill", skill_md) is None

    def test_the_flag_yields_a_reason(self, tmp_path):
        skill_md = _make_skill(tmp_path / "skills", "manual-skill", extra_fm="disable-model-invocation: true")
        reason = skill_blocks_preloading("manual-skill", skill_md)
        assert reason is not None
        assert "disable-model-invocation" in reason

    def test_a_bundled_name_yields_a_reason_without_a_file(self):
        assert skill_blocks_preloading("verify", None) is not None
        assert skill_blocks_preloading("code-review", None) is not None

    def test_an_unresolved_ordinary_skill_yields_no_reason(self):
        """Fail safe: never call a preload un-preloadable because we couldn't read it."""
        assert skill_blocks_preloading("some-skill", None) is None

    def test_an_unreadable_skill_yields_no_reason(self, tmp_path):
        broken = tmp_path / "broken" / "SKILL.md"
        broken.parent.mkdir(parents=True)
        broken.write_bytes(b"\xff\xfe\x00\x01")
        assert skill_blocks_preloading("broken", broken) is None


class TestBodyMentionsSkillName:
    def test_a_bare_prose_mention_is_found(self):
        assert body_mentions_skill_name("Route to foo-skill when needed.\n", "foo-skill") is True

    def test_a_table_cell_mention_is_found(self):
        assert body_mentions_skill_name("| foo-skill | mechanical |\n", "foo-skill") is True

    def test_a_namespaced_mention_is_found(self):
        assert body_mentions_skill_name("Use my-plugin:foo-skill here.\n", "foo-skill") is True

    def test_a_mention_inside_a_fence_is_not_counted(self):
        assert body_mentions_skill_name("intro\n```yaml\nskills: [foo-skill]\n```\n", "foo-skill") is False

    def test_positive_control_the_same_mention_outside_the_fence_is_counted(self):
        assert body_mentions_skill_name("intro\nskills: [foo-skill]\nafter\n", "foo-skill") is True

    def test_a_hyphenated_superstring_is_a_different_skill(self):
        """`\\b` treats `-` as a boundary, so a plain word-boundary match would
        accept a DIFFERENT skill's name as evidence that this one is used."""
        assert body_mentions_skill_name("Route to my-foo-skill instead.\n", "foo-skill") is False
        assert body_mentions_skill_name("Route to foo-skill-two instead.\n", "foo-skill") is False

    def test_an_absent_name_is_not_found(self):
        assert body_mentions_skill_name("Nothing relevant here.\n", "foo-skill") is False

    def test_case_is_ignored(self):
        assert body_mentions_skill_name("Route to Foo-Skill.\n", "foo-skill") is True

    def test_an_empty_name_is_never_a_mention(self):
        assert body_mentions_skill_name("anything", "") is False


class TestSeverityDiscipline:
    def test_no_closure_finding_is_ever_minor_or_nit(self, tmp_path):
        """WARNING is the only non-blocking tier under --strict, so advisories are
        WARNING — a MINOR/NIT closure finding would fail a valid agent."""
        plugin, agent = _probe_plugin(tmp_path)
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        closure_findings = [
            r
            for r in report.results
            if "skill" in r.message.lower() and r.level in ("MINOR", "NIT") and "closure" in r.message.lower()
        ]
        assert closure_findings == []

    def test_the_probe_fixture_reports_exactly_ac1_ac2_and_the_prose_warning(self, tmp_path):
        """The end-to-end contract from the TRDD's verified-gap table."""
        plugin, agent = _probe_plugin(tmp_path)
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert _levels(report, "totally-nonexistent-skill-xyz") == ["MAJOR"]
        assert _levels(report, "another-nonexistent-skill-abc") == ["MAJOR"]
        assert any("body mentions the tool 'Skill'" in r.message for r in report.results)
        assert report.exit_code_strict() != 0

    def test_default_auto_resolution_fires_without_any_flag(self, tmp_path):
        """No flags: the roots auto-resolve from the agent's own plugin tree."""
        plugin, agent = _probe_plugin(tmp_path)
        report = validate_agent(agent)
        assert _levels(report, "totally-nonexistent-skill-xyz") == ["MAJOR"]

    def test_an_agent_outside_any_plugin_or_project_is_not_penalised(self, tmp_path):
        """A single-file scan has no roots to resolve — never fabricate a MAJOR."""
        loose = _write(
            tmp_path / "loose-agent.md",
            "---\nname: loose-agent\ndescription: A loose agent file validated on its own, "
            "outside any plugin or project tree.\ntools: Read\nskills: [some-skill]\n---\n\n"
            "You are the loose agent. You are validated as a single file, with no plugin "
            "tree around you, which is exactly the case the non-vacuity guard protects.\n\n"
            "## Workflow\n\n1. Read the file the caller named.\n2. Report the outcome.\n",
        )
        report = validate_agent(loose, skills_roots=[])
        assert [r.level for r in report.results if r.level in BLOCKING] == []
        assert _levels(report, "some-skill") == ["WARNING"]


class TestClosureFlags:
    def test_closure_rolls_the_reachable_skill_findings_into_the_agent_report(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        # A skill whose frontmatter name disagrees with its directory — a real
        # finding the skill validator raises and --closure must surface.
        _make_skill(plugin / "skills", "real-skill", fm_name="wrong-name")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"], closure=True)
        assert any("[closure real-skill]" in r.message for r in report.results)

    def test_without_the_flag_no_skill_finding_is_rolled_in(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill", fm_name="wrong-name")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"])
        assert not any("[closure " in r.message for r in report.results)

    def test_closure_ambient_also_validates_the_unnamed_palette(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        _make_skill(plugin / "skills", "ambient-only-skill", fm_name="mismatched")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"], closure_ambient=True)
        assert any("[closure ambient-only-skill]" in r.message for r in report.results)

    def test_closure_alone_does_not_validate_the_ambient_palette(self, tmp_path):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        _make_skill(plugin / "skills", "ambient-only-skill", fm_name="mismatched")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill"])
        report = validate_agent(agent, skills_roots=[plugin / "skills"], closure=True)
        assert not any("[closure ambient-only-skill]" in r.message for r in report.results)


class TestCLI:
    def _run(self, argv: list[str], monkeypatch) -> int:
        import validate_agent as va

        monkeypatch.setattr(sys, "argv", ["validate_agent.py", *argv])
        return va.main()

    def test_skills_root_is_repeatable_and_used(self, tmp_path, monkeypatch, capsys):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        other = tmp_path / "other-skills"
        _make_skill(other, "elsewhere-skill")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill", "elsewhere-skill"])
        code = self._run(
            [str(agent), "--skills-root", str(plugin / "skills"), "--skills-root", str(other), "--strict"],
            monkeypatch,
        )
        capsys.readouterr()
        assert code == 0

    def test_a_missing_second_root_makes_the_finding_fire(self, tmp_path, monkeypatch, capsys):
        """Positive control for the repeatable flag."""
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        other = tmp_path / "other-skills"
        _make_skill(other, "elsewhere-skill")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill", "elsewhere-skill"])
        code = self._run([str(agent), "--skills-root", str(plugin / "skills"), "--strict"], monkeypatch)
        out = capsys.readouterr().out
        assert code != 0
        assert "elsewhere-skill" in out

    def test_a_nonexistent_skills_root_is_rejected(self, tmp_path, monkeypatch, capsys):
        plugin = _make_plugin(tmp_path / "plug")
        agent = _make_agent(plugin)
        code = self._run([str(agent), "--skills-root", str(tmp_path / "nope")], monkeypatch)
        err = capsys.readouterr().err
        assert code != 0
        assert "nope" in err

    def test_closure_flag_is_accepted(self, tmp_path, monkeypatch, capsys):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill"])
        code = self._run([str(agent), "--skills-root", str(plugin / "skills"), "--closure"], monkeypatch)
        capsys.readouterr()
        assert code == 0

    def test_closure_ambient_flag_is_accepted(self, tmp_path, monkeypatch, capsys):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        agent = _make_agent(plugin, tools="Read", skills=["real-skill"])
        code = self._run(
            [str(agent), "--skills-root", str(plugin / "skills"), "--closure", "--closure-ambient"], monkeypatch
        )
        capsys.readouterr()
        assert code == 0

    def test_directory_mode_still_works_with_closure_flags(self, tmp_path, monkeypatch, capsys):
        plugin = _make_plugin(tmp_path / "plug")
        _make_skill(plugin / "skills", "real-skill")
        _make_agent(plugin, name="one-agent", tools="Read", skills=["real-skill"])
        _make_agent(plugin, name="two-agent", tools="Read", skills=["missing-skill-qqq", "real-skill"])
        code = self._run(
            [str(plugin / "agents"), "--skills-root", str(plugin / "skills"), "--strict"],
            monkeypatch,
        )
        out = capsys.readouterr().out
        assert code != 0
        assert "missing-skill-qqq" in out


class TestNoRegressionOnRealCpvAgents:
    """CPV's own agents are the largest real corpus available — every preload
    they declare resolves, so the new findings must add ZERO blocking results."""

    def test_every_cpv_agent_stays_free_of_blocking_closure_findings(self):
        repo = Path(__file__).parent.parent
        agents = sorted((repo / "agents").glob("*.md"))
        assert agents, "no CPV agents found — the corpus check would pass vacuously"
        offenders: list[str] = []
        for agent in agents:
            report = validate_agent(agent, skills_roots=[repo / "skills"])
            for r in report.results:
                if r.level in BLOCKING and ("preload" in r.message or "Skill' tool" in r.message):
                    offenders.append(f"{agent.name}: [{r.level}] {r.message}")
        assert offenders == [], "\n".join(offenders)

    def test_the_corpus_check_is_not_vacuous(self, tmp_path):
        """A mutated copy of a real CPV agent MUST fire, proving the check above
        would have caught a regression."""
        repo = Path(__file__).parent.parent
        source = (repo / "agents" / "cpv-agent.md").read_text(encoding="utf-8")
        mutated = source.replace("  - cpv-the-skills-menu", "  - cpv-the-skills-menu\n  - no-such-skill-zzz", 1)
        assert mutated != source
        target = _write(tmp_path / "plug" / "agents" / "cpv-agent.md", mutated)
        (tmp_path / "plug" / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        report = validate_agent(target, skills_roots=[repo / "skills"])
        assert _levels(report, "no-such-skill-zzz") == ["MAJOR"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
