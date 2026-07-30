#!/usr/bin/env python3
"""Tests for convert_agent.py — ALL-IN-ONE / ONE-FOR-ALL / PLUGIN-OMNI conversion.

The two acceptance tests that matter most, both mechanical:

1. **THE INLINING PROHIBITION** (``TestNoInlining``) — no emitted agent body may
   contain a substring of any closure skill's body beyond its NAME. A skill has to
   stay INDEPENDENT so it can be shared and fixed ONCE; an inlined copy is a second
   source that rots silently.
2. **Every emitted agent passes ``validate_agent`` with ZERO blocking findings,
   AC1–AC5 included** — a generator that emits an unresolvable preload (AC1) or an
   un-preloadable one (AC5) has produced a BROKEN agent: the preload is silently
   dropped at dispatch and only a debug-log line records it.

Every check is two-sided: the refusal fires on the defect AND the legitimate
sibling still converts.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from convert_agent import (  # noqa: E402
    COMPANION_SKILL_NAME,
    GENERATED_MENU_SKILL_NAME,
    apply_node_frontmatter,
    convert,
    ensure_companion_skill,
    find_menu_skill,
    plan_node_frontmatter,
    read_markdown_parts,
    route_skills,
)
from validate_agent import validate_agent  # noqa: E402

BLOCKING = ("CRITICAL", "MAJOR", "MINOR", "NIT")

# Long, distinctive skill-body lines. The no-inlining test asserts NONE of these
# reaches an emitted agent body, so they must be long and unmistakable.
GREET_LINE = "Open with the caller's preferred salutation and never repeat it twice in one turn."
FAREWELL_LINE = "Close the exchange by summarising every promise made and naming the next owner."


def _skill(plugin: Path, name: str, body_line: str, *, extra_frontmatter: str = "") -> Path:
    d = plugin / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    skill_md = d / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {name} skill. Use when needed.\n"
        f"user-invocable: false\n{extra_frontmatter}---\n\n"
        f"# {name}\n\n## Overview\n\n{body_line}\n",
        encoding="utf-8",
    )
    return skill_md


SOURCE_BODY = """# tp-helper

You are the helper agent for tp.

## Step 1 — open

Use `tp-greet` when the caller has not been addressed yet.

## Step 2 — close

Reach for `tp-farewell` once every promise is recorded.
"""


def _make_plugin(
    tmp_path: Path,
    *,
    source_tools: str | None = "Read, Grep",
    source_skills: tuple[str, ...] = ("tp-greet", "tp-farewell"),
    with_other_agent: bool = False,
    with_user_only_skill: bool = False,
    with_missing_skill: bool = False,
) -> Path:
    plugin = tmp_path / "tp"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "tp", "version": "0.1.0", "description": "test plugin"}\n', encoding="utf-8"
    )
    _skill(plugin, "tp-greet", GREET_LINE)
    _skill(plugin, "tp-farewell", FAREWELL_LINE)
    if with_user_only_skill:
        _skill(
            plugin,
            "tp-user-only",
            "A user-only skill body that must never be preloaded by a generated agent.",
            extra_frontmatter="disable-model-invocation: true\n",
        )

    names = list(source_skills)
    if with_user_only_skill:
        names.append("tp-user-only")
    if with_missing_skill:
        names.append("tp-totally-missing-skill")
    fm = ["---", "name: tp-helper", "description: Helper agent for tp. Use when the caller needs help."]
    if source_tools is not None:
        fm.append(f"tools: {source_tools}")
    fm.append("skills:")
    fm.extend(f"  - {n}" for n in names)
    fm.append("---")
    (plugin / "agents").mkdir(parents=True, exist_ok=True)
    (plugin / "agents" / "tp-helper.md").write_text("\n".join(fm) + "\n\n" + SOURCE_BODY, encoding="utf-8")

    if with_other_agent:
        (plugin / "agents" / "tp-other.md").write_text(
            "---\nname: tp-other\ndescription: Other agent for tp. Use when greeting only.\n"
            "skills:\n  - tp-greet\n---\n\n# tp-other\n\nYou are another agent that uses `tp-greet`.\n",
            encoding="utf-8",
        )
    return plugin


def _roots(plugin: Path) -> list[Path]:
    """Hermetic skill roots — the machine's ~/.claude/skills must never decide a test."""
    return [plugin / "skills"]


def _blocking(agent_md: Path, plugin: Path) -> list[str]:
    report = validate_agent(agent_md, skills_roots=_roots(plugin))
    return [f"{r.level}: {r.message}" for r in report.results if r.level in BLOCKING]


def _convert(plugin: Path, mode: str, **kw):
    return convert(plugin / "agents" / "tp-helper.md", mode, roots=_roots(plugin), **kw)


class TestAllInOne:
    """`--to all-in-one`: every reachable closure skill by NAME, routing in the body."""

    def test_lists_reachable_skills_and_the_companion(self, tmp_path):
        """`skills:` holds every reachable closure skill plus the mandatory companion."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "all-in-one")
        assert result.ok, result.errors
        assert result.skills == ("tp-greet", "tp-farewell", COMPANION_SKILL_NAME)
        fm, _body = read_markdown_parts(Path(result.agent_path))
        assert fm["skills"] == ["tp-greet", "tp-farewell", COMPANION_SKILL_NAME]

    def test_emitted_agent_has_zero_blocking_findings(self, tmp_path):
        """The real acceptance test — validate_agent, AC1-AC5 included, must be clean."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "all-in-one")
        assert result.ok, result.errors
        assert _blocking(Path(result.agent_path), plugin) == []

    def test_skill_tool_is_granted(self, tmp_path):
        """All three architectures need the `Skill` gate OPEN, so `Skill` joins `tools`."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "all-in-one")
        fm, _ = read_markdown_parts(Path(result.agent_path))
        assert fm["tools"] == "Read, Grep, Skill"

    def test_absent_tools_stays_absent(self, tmp_path):
        """No `tools:` means inherit-all (gate already open) — materialising a list
        would REVOKE every other tool, so the field stays absent."""
        plugin = _make_plugin(tmp_path, source_tools=None)
        result = _convert(plugin, "all-in-one")
        fm, _ = read_markdown_parts(Path(result.agent_path))
        assert "tools" not in fm
        assert _blocking(Path(result.agent_path), plugin) == []

    def test_denied_skill_is_removed_from_disallowed_tools(self, tmp_path):
        """A denied `Skill` is applied FIRST and would kill every routed invocation."""
        plugin = _make_plugin(tmp_path)
        agent = plugin / "agents" / "tp-helper.md"
        agent.write_text(
            agent.read_text(encoding="utf-8").replace(
                "tools: Read, Grep", "tools: Read, Grep\ndisallowedTools: Skill, Write"
            ),
            encoding="utf-8",
        )
        result = _convert(plugin, "all-in-one")
        fm, _ = read_markdown_parts(Path(result.agent_path))
        assert fm["disallowedTools"] == ["Write"]
        assert any("disallowedTools" in n for n in result.notes)

    def test_unresolvable_and_unpreloadable_skills_are_excluded_and_reported(self, tmp_path):
        """AC1 (no such skill) and AC5 (cannot be preloaded) are filtered, never listed."""
        plugin = _make_plugin(tmp_path, with_user_only_skill=True, with_missing_skill=True)
        result = _convert(plugin, "all-in-one")
        assert result.ok, result.errors
        assert "tp-user-only" not in result.skills
        assert "tp-totally-missing-skill" not in result.skills
        reasons = dict(result.excluded)
        assert "AC5" in reasons["tp-user-only"]
        assert "AC1" in reasons["tp-totally-missing-skill"]
        assert _blocking(Path(result.agent_path), plugin) == []

    def test_routing_uses_the_source_branches(self, tmp_path):
        """Branches come from the SOURCE agent's own headings, not an invented order."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "all-in-one")
        body = Path(result.agent_path).read_text(encoding="utf-8")
        assert "### Step 1 — open" in body
        assert "### Step 2 — close" in body
        assert body.index("### Step 1 — open") < body.index("### Step 2 — close")

    def test_unmentioned_skill_lands_in_a_flat_choose_by_intent_group(self, tmp_path):
        """Where the source gives NO ordering we emit a flat table, never a fake sequence."""
        plugin = _make_plugin(tmp_path)
        _skill(plugin, "tp-audit", "An audit skill the source agent's body never mentions at all.")
        agent = plugin / "agents" / "tp-helper.md"
        agent.write_text(
            agent.read_text(encoding="utf-8").replace("  - tp-farewell", "  - tp-farewell\n  - tp-audit"),
            encoding="utf-8",
        )
        result = _convert(plugin, "all-in-one")
        assert result.ok, result.errors
        body = Path(result.agent_path).read_text(encoding="utf-8")
        assert "### Choose by intent" in body
        assert "| `tp-audit` | — |" in body

    def test_every_preloaded_skill_is_mentioned_in_the_body(self, tmp_path):
        """AC4's exact predicate: an unmentioned preload is paid for on every turn."""
        from cpv_agent_closure import body_mentions_skill_name

        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "all-in-one")
        _fm, body = read_markdown_parts(Path(result.agent_path))
        for name in result.skills:
            assert body_mentions_skill_name(body, name), name


class TestNoInlining:
    """THE acceptance test for the inlining prohibition — all three modes."""

    def _assert_no_skill_content(self, agent_md: Path, plugin: Path) -> None:
        emitted = agent_md.read_text(encoding="utf-8")
        for skill_md in sorted((plugin / "skills").glob("*/SKILL.md")):
            parts = read_markdown_parts(skill_md)
            assert parts is not None
            _fm, skill_body = parts
            assert skill_body.strip() not in emitted, f"{skill_md} body was copied wholesale"
            name = skill_md.parent.name
            for line in skill_body.splitlines():
                text = line.strip()
                if len(text) < 40 or name in text:
                    continue
                assert text not in emitted, f"{skill_md}:{text!r} was copied into {agent_md}"

    def test_all_in_one_copies_no_skill_content(self, tmp_path):
        """ALL-IN-ONE references skills by name; it copies nothing."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "all-in-one")
        assert result.ok, result.errors
        self._assert_no_skill_content(Path(result.agent_path), plugin)

    def test_one_for_all_copies_no_skill_content(self, tmp_path):
        """ONE-FOR-ALL adds frontmatter in place; it copies no body either."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "one-for-all")
        assert result.ok, result.errors
        self._assert_no_skill_content(Path(result.agent_path), plugin)

    def test_plugin_omni_copies_no_skill_content(self, tmp_path):
        """PLUGIN-OMNI routes through the menu; it copies no skill body."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "plugin-omni")
        assert result.ok, result.errors
        self._assert_no_skill_content(Path(result.agent_path), plugin)

    def test_the_guard_is_not_vacuous(self, tmp_path):
        """POSITIVE CONTROL: the same assertion FAILS on a body that did inline a skill."""
        plugin = _make_plugin(tmp_path)
        faked = plugin / "agents" / "faked.md"
        faked.write_text(
            "---\nname: faked\ndescription: inlines a skill body on purpose.\n---\n\n"
            f"# faked\n\n{GREET_LINE}\n",
            encoding="utf-8",
        )
        try:
            self._assert_no_skill_content(faked, plugin)
        except AssertionError:
            return
        raise AssertionError("the no-inlining guard passed on an inlined body — it is vacuous")


class TestOneForAll:
    """`--to one-for-all`: identical to all-in-one EXCEPT where the skills run."""

    def test_differs_from_all_in_one_only_by_execution(self, tmp_path):
        """The routing table is byte-identical; only the execution layer differs."""
        plugin_a = _make_plugin(tmp_path / "a")
        plugin_b = _make_plugin(tmp_path / "b")
        a = _convert(plugin_a, "all-in-one")
        b = _convert(plugin_b, "one-for-all")
        assert a.ok and b.ok, (a.errors, b.errors)
        assert [r.name for r in a.routed] == [r.name for r in b.routed]
        assert a.skills == b.skills

        def table(path: str) -> str:
            body = Path(path).read_text(encoding="utf-8")
            start = body.index("## Workflow — skill routing")
            end = body.index("## ", body.index("\n", start) + 1)
            return body[start:end]

        assert table(a.agent_path) == table(b.agent_path)

    def test_a_refusal_leaves_the_tree_untouched(self, tmp_path):
        """PLAN then WRITE: an earlier draft created the companion skill and only THEN
        refused on a shared skill — exactly the half-applied state a refusal prevents."""
        plugin = _make_plugin(tmp_path, with_other_agent=True)
        before = sorted(p.relative_to(plugin).as_posix() for p in plugin.rglob("*"))
        result = _convert(plugin, "one-for-all")
        assert not result.ok
        after = sorted(p.relative_to(plugin).as_posix() for p in plugin.rglob("*"))
        assert after == before
        assert not (plugin / "skills" / COMPANION_SKILL_NAME).exists()

    def test_refuses_to_mutate_a_shared_skill_without_force(self, tmp_path):
        """Adding `context: fork` changes execution for EVERY agent listing the skill,
        and there is no private copy to change instead — so it needs consent."""
        plugin = _make_plugin(tmp_path, with_other_agent=True)
        result = _convert(plugin, "one-for-all")
        assert not result.ok
        assert any("SHARED" in e and "--force" in e for e in result.errors)
        assert any("tp-other.md" in e for e in result.errors)
        assert not (plugin / "agents" / "tp-helper-one-for-all.md").exists()
        # And the shared skill was NOT touched.
        assert "context: fork" not in (plugin / "skills" / "tp-greet" / "SKILL.md").read_text(encoding="utf-8")

    def test_reports_how_many_other_agents_list_each_shared_skill(self, tmp_path):
        """The consequence has to be VISIBLE before it happens."""
        plugin = _make_plugin(tmp_path, with_other_agent=True)
        result = _convert(plugin, "one-for-all", dry_run=True)
        shared = {n.name: n.other_agents for n in result.nodes}
        assert len(shared["tp-greet"]) == 1
        assert shared["tp-farewell"] == ()

    def test_force_converts_the_nodes_in_place(self, tmp_path):
        """With consent the SHARED skill gains the fork keys in its OWN frontmatter."""
        plugin = _make_plugin(tmp_path, with_other_agent=True)
        result = _convert(plugin, "one-for-all", force=True)
        assert result.ok, result.errors
        text = (plugin / "skills" / "tp-greet" / "SKILL.md").read_text(encoding="utf-8")
        assert "context: fork" in text
        assert "background: false" in text
        # The body is untouched — only frontmatter changed.
        assert GREET_LINE in text

    def test_unshared_skills_convert_without_force(self, tmp_path):
        """The refusal is about SHARED skills only; a skill only this agent reaches
        is not somebody else's business."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "one-for-all")
        assert result.ok, result.errors
        for name in ("tp-greet", "tp-farewell"):
            text = (plugin / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            assert "context: fork" in text
            assert "background: false" in text

    def test_background_false_and_its_version_requirement_are_recorded(self, tmp_path):
        """`background` defaults to TRUE, so without `background: false` a node returns
        NOTHING inline — the requirement and its CC version must be on the record."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "one-for-all")
        assert any("2.1.218" in n for n in result.notes)
        body = Path(result.agent_path).read_text(encoding="utf-8")
        assert "background: false" in body
        assert "2.1.218" in body

    def test_agent_key_is_opt_in_and_never_the_mechanism(self, tmp_path):
        """`agent:` alone does NOTHING — `context: fork` is the mechanism, so `agent:`
        is only written when --node-agent asks for it."""
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "one-for-all")
        assert result.ok, result.errors
        text = (plugin / "skills" / "tp-greet" / "SKILL.md").read_text(encoding="utf-8")
        assert "\nagent:" not in text

        plugin2 = _make_plugin(tmp_path / "second")
        result2 = _convert(plugin2, "one-for-all", node_agent="Explore")
        assert result2.ok, result2.errors
        text2 = (plugin2 / "skills" / "tp-greet" / "SKILL.md").read_text(encoding="utf-8")
        assert "agent: Explore" in text2
        assert "context: fork" in text2

    def test_emitted_agent_has_zero_blocking_findings(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "one-for-all")
        assert result.ok, result.errors
        assert _blocking(Path(result.agent_path), plugin) == []

    def test_refuses_a_self_invoking_skill(self, tmp_path):
        """A `context: fork` skill that re-invokes ITSELF is the v2.1.145 antipattern
        (a blocking finding), so the conversion must refuse rather than break the plugin."""
        plugin = _make_plugin(tmp_path)
        skill_md = plugin / "skills" / "tp-greet" / "SKILL.md"
        skill_md.write_text(
            skill_md.read_text(encoding="utf-8") + '\nRe-run yourself with Skill({skill: "tp-greet"}).\n',
            encoding="utf-8",
        )
        result = _convert(plugin, "one-for-all", force=True)
        assert not result.ok
        assert any("invokes ITSELF" in e for e in result.errors)
        assert "context: fork" not in skill_md.read_text(encoding="utf-8")

    def test_conversion_is_idempotent(self, tmp_path):
        """A second run adds NO duplicate key — a duplicate top-level key is a MAJOR
        (YAML silently keeps only the LAST one)."""
        plugin = _make_plugin(tmp_path)
        assert _convert(plugin, "one-for-all").ok
        text_first = (plugin / "skills" / "tp-greet" / "SKILL.md").read_text(encoding="utf-8")
        assert _convert(plugin, "one-for-all", force=True).ok
        text_second = (plugin / "skills" / "tp-greet" / "SKILL.md").read_text(encoding="utf-8")
        assert text_first == text_second
        assert text_second.count("context:") == 1
        assert text_second.count("background:") == 1


class TestPluginOmni:
    """`--to plugin-omni`: exactly the menu + the companion, routing through the menu."""

    def test_lists_exactly_the_menu_and_the_companion(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "plugin-omni")
        assert result.ok, result.errors
        assert result.skills == (GENERATED_MENU_SKILL_NAME, COMPANION_SKILL_NAME)
        assert len(result.skills) == 2

    def test_generates_a_menu_from_the_real_inventory(self, tmp_path):
        """A generated menu must never be an EMPTY catalog — that would make the agent
        inert while looking correct."""
        plugin = _make_plugin(tmp_path)
        assert find_menu_skill(plugin) is None
        result = _convert(plugin, "plugin-omni")
        assert result.menu_created is True
        menu = plugin / "skills" / GENERATED_MENU_SKILL_NAME / "SKILL.md"
        text = menu.read_text(encoding="utf-8")
        assert "tp-greet" in text
        assert "tp-farewell" in text
        assert COMPANION_SKILL_NAME in text
        assert "This plugin has no operational skills yet" not in text

    def test_never_clobbers_an_existing_menu_and_adds_the_companion_row(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        menu_dir = plugin / "skills" / "tp-the-skills-menu"
        menu_dir.mkdir(parents=True)
        (menu_dir / "SKILL.md").write_text(
            "---\nname: tp-the-skills-menu\ndescription: Catalog for tp. Use when picking a skill.\n"
            "user-invocable: false\n---\n\n# tp-the-skills-menu\n\n"
            "## Plugin Skills\n\n| # | Domain | Skills |\n|---|--------|--------|\n"
            "| 1 | greeting | `tp-greet` |\n\n## Resources\n\nnone\n",
            encoding="utf-8",
        )
        result = _convert(plugin, "plugin-omni")
        assert result.ok, result.errors
        assert result.menu_created is False
        assert result.menu_name == "tp-the-skills-menu"
        text = (menu_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "| 1 | greeting | `tp-greet` |" in text  # the author's row survived
        assert COMPANION_SKILL_NAME in text  # and the companion was registered
        assert result.menu_row_added is True

    def test_refuses_when_the_plugin_has_no_operational_skills(self, tmp_path):
        """An empty catalog is REPORTED, never emitted as a shell."""
        plugin = tmp_path / "bare"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "bare"}\n', encoding="utf-8")
        (plugin / "agents").mkdir()
        agent = plugin / "agents" / "solo.md"
        agent.write_text(
            "---\nname: solo\ndescription: Solo agent. Use when nothing else applies.\n---\n\n"
            "# solo\n\nYou are a solo agent with no skills.\n",
            encoding="utf-8",
        )
        result = convert(agent, "plugin-omni", roots=[plugin / "skills"])
        assert not result.ok
        assert any("EMPTY" in e for e in result.errors)
        assert not (plugin / "agents" / "solo-plugin-omni.md").exists()

    def test_emitted_agent_has_zero_blocking_findings(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "plugin-omni")
        assert result.ok, result.errors
        assert _blocking(Path(result.agent_path), plugin) == []


class TestCompanionSkill:
    """The MANDATORY companion, and its hard interaction with AC1."""

    def test_every_mode_carries_the_companion(self, tmp_path):
        for mode in ("all-in-one", "one-for-all", "plugin-omni"):
            plugin = _make_plugin(tmp_path / mode)
            result = _convert(plugin, mode)
            assert result.ok, result.errors
            assert COMPANION_SKILL_NAME in result.skills
            assert (plugin / "skills" / COMPANION_SKILL_NAME / "SKILL.md").is_file()

    def test_written_from_the_template_when_absent(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "all-in-one")
        assert result.companion_created is True
        text = (plugin / "skills" / COMPANION_SKILL_NAME / "SKILL.md").read_text(encoding="utf-8")
        assert "NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE" in text

    def test_never_overwrites_an_adapted_companion(self, tmp_path):
        """The user may have adapted it — clobbering would destroy work while
        reporting success."""
        plugin = _make_plugin(tmp_path)
        target = plugin / "skills" / COMPANION_SKILL_NAME / "SKILL.md"
        target.parent.mkdir(parents=True)
        adapted = (
            f"---\nname: {COMPANION_SKILL_NAME}\ndescription: Adapted locally. Use when claiming done.\n"
            "---\n\n# adapted\n\nMY OWN RULES.\n"
        )
        target.write_text(adapted, encoding="utf-8")
        path, created, error = ensure_companion_skill(plugin)
        assert error is None
        assert created is False
        assert path.read_text(encoding="utf-8") == adapted


class TestRefusals:
    """Two-sided guards: refuse loudly, write nothing."""

    def test_refuses_to_overwrite_without_force(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        first = _convert(plugin, "all-in-one")
        assert first.ok
        stamp = Path(first.agent_path).read_text(encoding="utf-8")
        second = _convert(plugin, "all-in-one")
        assert not second.ok
        assert any("--force" in e for e in second.errors)
        assert Path(first.agent_path).read_text(encoding="utf-8") == stamp
        third = _convert(plugin, "all-in-one", force=True)
        assert third.ok, third.errors

    def test_empty_closure_is_reported_not_emitted(self, tmp_path):
        """An agent naming no usable skill would produce a SHELL — refuse instead."""
        plugin = _make_plugin(tmp_path, source_skills=())
        agent = plugin / "agents" / "tp-helper.md"
        agent.write_text(
            "---\nname: tp-helper\ndescription: Helper agent for tp. Use when help is needed.\n---\n\n"
            "# tp-helper\n\nYou are the helper agent with no skills at all.\n",
            encoding="utf-8",
        )
        result = _convert(plugin, "all-in-one")
        assert not result.ok
        assert any("empty shell" in e for e in result.errors)
        assert not (plugin / "agents" / "tp-helper-all-in-one.md").exists()

    def test_only_unresolvable_skills_is_an_empty_closure(self, tmp_path):
        plugin = _make_plugin(tmp_path, source_skills=("nope-one", "nope-two"))
        result = _convert(plugin, "all-in-one")
        assert not result.ok
        assert {n for n, _ in result.excluded} == {"nope-one", "nope-two"}

    def test_unknown_mode_is_refused(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "mono")
        assert not result.ok
        assert any("unknown mode" in e for e in result.errors)

    def test_out_outside_the_plugin_is_flagged(self, tmp_path):
        """A skill name resolves relative to the AGENT's location, so an agent written
        outside its plugin can no longer see that plugin's skills."""
        plugin = _make_plugin(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        result = _convert(plugin, "all-in-one", out_dir=elsewhere)
        assert result.ok, result.errors
        assert any("OUTSIDE its plugin" in n for n in result.notes)
        # …and the same conversion into the plugin's own agents/ says no such thing.
        inside = _convert(plugin, "all-in-one", name="tp-inside")
        assert inside.ok, inside.errors
        assert not any("OUTSIDE its plugin" in n for n in inside.notes)

    def test_dry_run_writes_nothing(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        result = _convert(plugin, "all-in-one", dry_run=True)
        assert result.ok, result.errors
        assert result.written is False
        assert not Path(result.agent_path).exists()
        assert not (plugin / "skills" / COMPANION_SKILL_NAME).exists()


class TestNodeFrontmatterEditing:
    """The surgical in-place frontmatter edit — no duplicate keys, no collateral damage."""

    def test_adds_the_missing_keys_only(self):
        text = "---\nname: s\ndescription: d\n---\n\nbody\n"
        plan = plan_node_frontmatter(text, node_agent=None)
        assert plan is not None
        to_add, conflicts = plan
        assert set(to_add) == {"context", "background"}
        assert conflicts == []
        out = apply_node_frontmatter(text, to_add, conflicts)
        assert out == "---\nname: s\ndescription: d\ncontext: fork\nbackground: false\n---\n\nbody\n"

    def test_existing_correct_value_is_a_no_op(self):
        text = "---\nname: s\ncontext: fork\nbackground: false\n---\n\nbody\n"
        plan = plan_node_frontmatter(text, node_agent=None)
        assert plan == ({}, [])

    def test_conflicting_value_is_reported_as_a_conflict(self):
        text = "---\nname: s\ncontext: compact\n---\n\nbody\n"
        plan = plan_node_frontmatter(text, node_agent=None)
        assert plan is not None
        to_add, conflicts = plan
        assert conflicts == [("context", "compact", "fork")]
        out = apply_node_frontmatter(text, to_add, conflicts)
        assert "context: fork" in out
        assert "context: compact" not in out
        assert out.count("context:") == 1

    def test_quoted_and_capitalised_values_compare_equal(self):
        text = '---\nname: s\ncontext: "fork"\nbackground: False\n---\n\nbody\n'
        assert plan_node_frontmatter(text, node_agent=None) == ({}, [])

    def test_indented_key_is_not_mistaken_for_a_top_level_one(self):
        """A nested mapping key must not satisfy the top-level check, or the real key
        would be ADDED again and YAML would keep only the last one."""
        text = "---\nname: s\nmetadata:\n  context: nested\n---\n\nbody\n"
        plan = plan_node_frontmatter(text, node_agent=None)
        assert plan is not None
        to_add, _ = plan
        assert "context" in to_add

    def test_crlf_line_endings_are_preserved(self):
        text = "---\r\nname: s\r\ndescription: d\r\n---\r\n\r\nbody\r\n"
        plan = plan_node_frontmatter(text, node_agent=None)
        assert plan is not None
        out = apply_node_frontmatter(text, *plan)
        assert "\r\ncontext: fork\r\n" in out
        assert "\nbody\r\n" in out
        assert out.count("\n") == out.count("\r\n")

    def test_no_frontmatter_is_reported_as_unconvertible(self):
        assert plan_node_frontmatter("# just a body\n", node_agent=None) is None


class TestRouteSkills:
    """Routing hints come from the SOURCE agent — never from a skill's own content."""

    def test_hint_and_branch_come_from_the_source_body(self):
        rows = route_skills(SOURCE_BODY, ["tp-greet", "tp-farewell"])
        by_name = {r.name: r for r in rows}
        assert by_name["tp-greet"].branch == "Step 1 — open"
        assert "not been addressed" in by_name["tp-greet"].when
        assert by_name["tp-farewell"].branch == "Step 2 — close"

    def test_unmentioned_skill_gets_no_branch_and_no_hint(self):
        rows = route_skills(SOURCE_BODY, ["never-mentioned"])
        assert rows == [type(rows[0])(name="never-mentioned", branch="", when="")]

    def test_pipe_in_a_hint_cannot_break_the_table(self):
        body = "## H\n\nUse `x-skill` when a | pipe appears.\n"
        rows = route_skills(body, ["x-skill"])
        assert r"\|" in rows[0].when

    def test_a_mention_inside_a_fence_is_an_illustration_not_routing(self):
        body = "## H\n\n```text\nUse `x-skill` here in a fence only.\n```\n"
        rows = route_skills(body, ["x-skill"])
        assert rows[0].when == ""

    def test_a_table_row_is_reduced_to_the_when_cell(self):
        """Carrying the whole row produced an unreadable pipe-escaped mess on a real
        agent; the cell that does NOT name the skill IS the 'when'."""
        body = '## H\n\n| Task | Skill |\n|---|---|\n| Per-error fix steps | `Skill({skill: "x-skill"})` |\n'
        rows = route_skills(body, ["x-skill"])
        assert rows[0].when == "Per-error fix steps"

    def test_a_carried_skill_invocation_is_stripped(self):
        """A carried `Skill()` would become a REAL runtime reference in the emitted
        agent — and an unresolvable one would be an AC2 MAJOR the generator invented."""
        body = '## H\n\nUse it via `Skill({skill: "ghost-skill"})` when routing to `x-skill`.\n'
        rows = route_skills(body, ["x-skill"])
        assert "Skill(" not in rows[0].when
        assert "ghost-skill" not in rows[0].when


class TestNoAbsolutePathsInTheEmittedAgent:
    """An absolute developer path in a shipped component is a hardcoded-user-path
    MAJOR (and a privacy leak). The resolved roots belong in the CLI report only."""

    def test_exclusion_notes_carry_no_absolute_path(self, tmp_path):
        plugin = _make_plugin(tmp_path, with_missing_skill=True, with_user_only_skill=True)
        result = _convert(plugin, "all-in-one")
        assert result.ok, result.errors
        body = Path(result.agent_path).read_text(encoding="utf-8")
        assert str(tmp_path) not in body
        assert str(plugin) not in body
        # …while the report DOES name the roots, so the operator can still fix them.
        from convert_agent import render_report

        assert str(plugin / "skills") in render_report(result)

    def test_exclusions_are_grouped_by_reason(self, tmp_path):
        """One note per REASON, not per skill — a real agent produced eight
        near-identical lines that buried the notes that mattered."""
        plugin = _make_plugin(tmp_path, source_skills=("tp-greet", "ghost-a", "ghost-b"))
        result = _convert(plugin, "all-in-one")
        assert result.ok, result.errors
        grouped = [n for n in result.notes if n.startswith("NOT preloaded")]
        assert len(grouped) == 1
        assert "`ghost-a`" in grouped[0] and "`ghost-b`" in grouped[0]

    def test_scope_label_uses_the_manifest_name_not_the_directory(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        renamed = tmp_path / "some-checkout-dir"
        plugin.rename(renamed)
        result = convert(
            renamed / "agents" / "tp-helper.md", "all-in-one", roots=[renamed / "skills"]
        )
        assert result.ok, result.errors
        body = Path(result.agent_path).read_text(encoding="utf-8")
        assert "`tp` plugin" in body
        assert "some-checkout-dir" not in body
