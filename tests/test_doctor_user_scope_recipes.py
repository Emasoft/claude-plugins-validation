"""Tests for scripts/cpv_doctor_user_scope.py (TRDD-d1f74670, D9..D13).

Real end-to-end checks against real filesystem fixtures — no mocking of the
code under test. Each test's docstring is a one-line description used by any
results table.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_doctor_user_scope as m  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# D10 — stub files
# ---------------------------------------------------------------------------


def test_d10_detects_short_404_stub(tmp_path: Path) -> None:
    """D10 flags a 14-byte '404: Not Found' SKILL.md body as a stub."""
    root = tmp_path
    _write(root / "skills" / "broken" / "SKILL.md", "---\nname: broken\n---\n404: Not Found")
    report = ValidationReport()
    m.check_stub_files(root, report)
    codes = [r.message for r in report.results]
    assert any("RC-STUB-FILE-001" in c for c in codes)


def test_d10_ignores_legitimate_short_skill(tmp_path: Path) -> None:
    """D10 does not flag a genuinely short but valid SKILL.md."""
    root = tmp_path
    _write(
        root / "skills" / "tiny" / "SKILL.md",
        "---\nname: tiny\n---\nThis is a small but real skill that does one thing well.",
    )
    report = ValidationReport()
    m.check_stub_files(root, report)
    assert not any("RC-STUB-FILE-001" in r.message for r in report.results)


def test_d10_ignores_long_body_even_with_error_text(tmp_path: Path) -> None:
    """D10 does not flag a long body that merely mentions '404' in passing."""
    root = tmp_path
    body = "This skill explains how to handle a 404 Not Found error. " * 10
    _write(root / "skills" / "doc" / "SKILL.md", f"---\nname: doc\n---\n{body}")
    report = ValidationReport()
    m.check_stub_files(root, report)
    assert not any("RC-STUB-FILE-001" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# D11 — stale year
# ---------------------------------------------------------------------------


def test_d11_detects_current_year_is(tmp_path: Path) -> None:
    """D11 flags a hardcoded 'current year is 2025' note."""
    root = tmp_path
    _write(root / "skills" / "s" / "SKILL.md", "---\nname: s\n---\nThe current year is 2025, so plan accordingly.")
    report = ValidationReport()
    m.check_stale_year(root, report)
    assert any("RC-STALE-YEAR-001" in r.message for r in report.results)


def test_d11_ignores_since_year(tmp_path: Path) -> None:
    """D11 does not flag 'since 2024' (historical reference exclusion)."""
    root = tmp_path
    _write(root / "skills" / "s" / "SKILL.md", "---\nname: s\n---\nAs of 2024, we have supported since 2024.")
    report = ValidationReport()
    m.check_stale_year(root, report)
    assert not any("RC-STALE-YEAR-001" in r.message for r in report.results)


def test_d11_ignores_copyright_line(tmp_path: Path) -> None:
    """D11 does not flag a copyright notice near a year token."""
    root = tmp_path
    _write(root / "skills" / "s" / "SKILL.md", "---\nname: s\n---\nCopyright as of 2023 all rights reserved.")
    report = ValidationReport()
    m.check_stale_year(root, report)
    assert not any("RC-STALE-YEAR-001" in r.message for r in report.results)


def test_d11_ignores_fenced_output_block(tmp_path: Path) -> None:
    """D11 does not flag a stale-year phrase inside a ```text fenced block."""
    root = tmp_path
    content = "---\nname: s\n---\n```text\nthe year is 2020\n```\n"
    _write(root / "skills" / "s" / "SKILL.md", content)
    report = ValidationReport()
    m.check_stale_year(root, report)
    assert not any("RC-STALE-YEAR-001" in r.message for r in report.results)


def test_d11_suggests_bash_date_when_allowed_tools_missing(tmp_path: Path) -> None:
    """D11 additionally notes a missing Bash(date *) grant when it flags a stale year."""
    root = tmp_path
    _write(root / "skills" / "s" / "SKILL.md", "---\nname: s\nallowed-tools: Read\n---\nThe current year is 2025.")
    report = ValidationReport()
    m.check_stale_year(root, report)
    assert any("Bash(date" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# D12 — dead local-script refs
# ---------------------------------------------------------------------------


def test_d12_detects_missing_script(tmp_path: Path) -> None:
    """D12 flags a referenced user-scope script that does not exist on disk."""
    root = tmp_path
    _write(
        root / "skills" / "s" / "SKILL.md",
        "---\nname: s\n---\nRun ~/.claude/scripts/missing.sh to do the thing.",
    )
    report = ValidationReport()
    m.check_dead_script_refs(root, report)
    assert any("RC-DEAD-SCRIPT-REF-001" in r.message for r in report.results)


def test_d12_ignores_plugin_cache_path(tmp_path: Path) -> None:
    """D12 never flags a path resolving inside a plugin cache/data dir."""
    root = tmp_path
    _write(
        root / "skills" / "s" / "SKILL.md",
        "---\nname: s\n---\nSee ~/.claude/plugins/data/foo/scripts/generated.py for output.",
    )
    report = ValidationReport()
    m.check_dead_script_refs(root, report)
    assert not any("RC-DEAD-SCRIPT-REF-001" in r.message for r in report.results)


def test_d12_ignores_existing_script(tmp_path: Path) -> None:
    """D12 does not flag a referenced script that genuinely exists."""
    root = tmp_path
    real_script = root / "scripts" / "real.py"
    _write(real_script, "print('hi')\n")
    _write(
        root / "skills" / "s" / "SKILL.md",
        f"---\nname: s\n---\nRun {real_script} to do the thing.",
    )
    report = ValidationReport()
    m.check_dead_script_refs(root, report)
    assert not any("RC-DEAD-SCRIPT-REF-001" in r.message for r in report.results)


def test_d12_ignores_markdown_comment_line(tmp_path: Path) -> None:
    """D12 skips a script path mentioned on a Markdown comment line ('# ...')."""
    root = tmp_path
    _write(
        root / "skills" / "s" / "SKILL.md",
        "---\nname: s\n---\n# See ~/.claude/scripts/example-missing.sh for an example\nReal body.",
    )
    report = ValidationReport()
    m.check_dead_script_refs(root, report)
    assert not any("RC-DEAD-SCRIPT-REF-001" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# D13 — namespace correctness
# ---------------------------------------------------------------------------


def _make_plugin_cache_skill(cache_root: Path, marketplace: str, plugin: str, version: str, skill: str) -> None:
    skill_dir = cache_root / marketplace / plugin / version / "skills" / skill
    _write(skill_dir / "SKILL.md", f"---\nname: {skill}\n---\nA plugin skill.")


def test_d13_bare_name_when_plugin_shipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 flags a bare Skill() reference to a name only a plugin ships."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    cache_root = fake_home / ".claude" / "plugins" / "cache"
    _make_plugin_cache_skill(cache_root, "market", "claude-menu-system", "1.0.0", "menu-test")

    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        '---\nname: a\n---\nDispatch: Skill({skill: "menu-test"})',
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-MISSING-001" in r.message for r in report.results)


def test_d13_namespaced_when_local_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 flags a spuriously namespaced reference to a user-scope-only skill."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(root / "skills" / "my-local" / "SKILL.md", "---\nname: my-local\n---\nLocal skill.")
    _write(
        root / "agents" / "a" / "a.md",
        '---\nname: a\n---\nDispatch: Skill({skill: "user-scope:my-local"})',
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-SPURIOUS-001" in r.message for r in report.results)


def test_d13_ambiguous_when_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 flags a bare reference that exists in both user-scope and a plugin."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    cache_root = fake_home / ".claude" / "plugins" / "cache"
    _make_plugin_cache_skill(cache_root, "market", "some-plugin", "1.0.0", "team-kanban")

    root = fake_home / ".claude"
    _write(root / "skills" / "team-kanban" / "SKILL.md", "---\nname: team-kanban\n---\nLocal skill.")
    _write(
        root / "agents" / "a" / "a.md",
        '---\nname: a\n---\nDispatch: Skill({skill: "team-kanban"})',
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-AMBIGUOUS-001" in r.message for r in report.results)


def test_d13_unresolved_when_nothing_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 flags a bare reference that resolves to no known skill (CRITICAL)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        '---\nname: a\n---\nDispatch: Skill({skill: "ghost-skill"})',
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    crit = [r for r in report.results if "RC-NAMESPACE-UNRESOLVED-001" in r.message]
    assert crit and crit[0].level == "CRITICAL"


def test_d13_agent_frontmatter_skills_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 applies the same rules to an agent's frontmatter `skills:` list."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    cache_root = fake_home / ".claude" / "plugins" / "cache"
    _make_plugin_cache_skill(cache_root, "market", "some-plugin", "1.0.0", "remote-skill")

    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        "---\nname: a\nskills:\n  - remote-skill\n---\nBody.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-MISSING-001" in r.message for r in report.results)


def test_d13_no_finding_when_correctly_namespaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 emits nothing when a plugin skill is already correctly namespaced."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    cache_root = fake_home / ".claude" / "plugins" / "cache"
    _make_plugin_cache_skill(cache_root, "market", "some-plugin", "1.0.0", "remote-skill")

    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        '---\nname: a\n---\nDispatch: Skill({skill: "some-plugin:remote-skill"})',
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any(
        code in r.message
        for r in report.results
        for code in ("RC-NAMESPACE-MISSING-001", "RC-NAMESPACE-SPURIOUS-001", "RC-NAMESPACE-AMBIGUOUS-001", "RC-NAMESPACE-UNRESOLVED-001")
    )


_D13_CODES = (
    "RC-NAMESPACE-MISSING-001",
    "RC-NAMESPACE-SPURIOUS-001",
    "RC-NAMESPACE-AMBIGUOUS-001",
    "RC-NAMESPACE-UNRESOLVED-001",
)


def test_d13_prose_absolute_path_is_not_a_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 does not treat a prose `/usr/bin` path as a skill invocation."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "skills" / "s" / "SKILL.md",
        "---\nname: s\n---\nAdd /usr/bin to your PATH before running this.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any(code in r.message for r in report.results for code in _D13_CODES)


def test_d13_prose_slash_ratio_is_not_a_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 does not treat prose `a/b` (a slash mid-word) as a skill invocation."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "skills" / "s" / "SKILL.md",
        "---\nname: s\n---\nThe ratio a/b determines the outcome here.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any(code in r.message for r in report.results for code in _D13_CODES)


def test_d13_backtick_only_mention_is_not_a_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 does not treat a bare backtick-wrapped skill name as an invocation."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "skills" / "s" / "SKILL.md",
        "---\nname: s\n---\nSee the `ghost-skill` documentation for background.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any(code in r.message for r in report.results for code in _D13_CODES)


def test_d13_non_instruction_md_file_is_never_scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 never scans a .md file outside skills/agents/commands (e.g. a top-level README)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "README.md",
        '---\nname: readme\n---\nDispatch: Skill({skill: "ghost-skill"})',
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any(code in r.message for r in report.results for code in _D13_CODES)


def test_d13_real_skill_call_still_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 still flags a real Skill({skill: "ghost-x"}) invocation of an unresolved name."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        '---\nname: a\n---\nDispatch: Skill({skill: "ghost-x"})',
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-UNRESOLVED-001" in r.message for r in report.results)


def test_d13_line_start_slash_command_still_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 still flags a line-start /ghost-cmd slash-command invocation in an agent body."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        "---\nname: a\n---\n/ghost-cmd runs the routine.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-UNRESOLVED-001" in r.message for r in report.results)


def test_d13_frontmatter_skills_list_still_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 still flags a `skills: [ghost-y]` frontmatter entry that resolves to nothing."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        "---\nname: a\nskills: [ghost-y]\n---\nBody.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-UNRESOLVED-001" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# D9 — ghost-agent dispatch (delegated engine)
# ---------------------------------------------------------------------------


def test_d9_flags_ghost_agent_dispatch(tmp_path: Path) -> None:
    """D9 flags a subagent_type literal that resolves to no real agent."""
    root = tmp_path
    _write(
        root / "skills" / "s" / "SKILL.md",
        '---\nname: s\n---\nTask(subagent_type="oracle", prompt="do it")',
    )
    report = ValidationReport()
    m.check_ghost_dispatch(root, report)
    crit = [r for r in report.results if "RC-GHOST-DISPATCH-001" in r.message]
    assert crit and crit[0].level == "CRITICAL"


def test_d9_accepts_existing_user_scope_agent(tmp_path: Path) -> None:
    """D9 does not flag dispatch to a real user-scope agent."""
    root = tmp_path
    _write(root / "agents" / "real-agent.md", "---\nname: real-agent\n---\nA real agent.")
    _write(
        root / "skills" / "s" / "SKILL.md",
        '---\nname: s\n---\nTask(subagent_type="real-agent", prompt="do it")',
    )
    report = ValidationReport()
    m.check_ghost_dispatch(root, report)
    assert not any("RC-GHOST-DISPATCH-001" in r.message for r in report.results)


def test_d9_accepts_builtin_agent(tmp_path: Path) -> None:
    """D9 does not flag dispatch to a Claude-Code built-in agent name."""
    root = tmp_path
    _write(
        root / "skills" / "s" / "SKILL.md",
        '---\nname: s\n---\nTask(subagent_type="general-purpose", prompt="do it")',
    )
    report = ValidationReport()
    m.check_ghost_dispatch(root, report)
    assert not any("RC-GHOST-DISPATCH-001" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# End-to-end
# ---------------------------------------------------------------------------


def test_run_user_scope_recipes_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run_user_scope_recipes fires all applicable D9..D13 findings on one fixture tree."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(root / "skills" / "stub" / "SKILL.md", "---\nname: stub\n---\n404: Not Found")
    _write(
        root / "skills" / "yeardoc" / "SKILL.md",
        "---\nname: yeardoc\n---\nThe current year is 2025.",
    )
    _write(
        root / "skills" / "dead" / "SKILL.md",
        "---\nname: dead\n---\nSee ~/.claude/scripts/nope.sh",
    )
    _write(
        root / "agents" / "ghost" / "ghost.md",
        '---\nname: ghost\n---\nTask(subagent_type="not-a-real-agent")',
    )
    report = ValidationReport()
    m.run_user_scope_recipes(root, report)
    codes = {
        "RC-STUB-FILE-001",
        "RC-STALE-YEAR-001",
        "RC-DEAD-SCRIPT-REF-001",
        "RC-GHOST-DISPATCH-001",
    }
    fired = {code for code in codes for r in report.results if code in r.message}
    assert fired == codes


def test_d13_builtin_slash_command_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 never flags a prose /plan or /clear — they resolve to CC built-ins."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "skills" / "s" / "SKILL.md",
        "---\nname: s\n---\nRun /plan first, then /clear the session.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any("RC-NAMESPACE" in r.message for r in report.results)


def test_d13_fs_root_slash_is_not_a_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 never flags a bare filesystem root like "write it to /tmp"."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        "---\nname: a\n---\nSave scratch output to /tmp and clean up after.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any("RC-NAMESPACE" in r.message for r in report.results)


def test_d13_user_command_self_reference_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 resolves /name against user-scope commands/*.md, not only skills."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(root / "commands" / "my-cmd.md", "---\nname: my-cmd\n---\nType /my-cmd to run this.")
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any("RC-NAMESPACE" in r.message for r in report.results)


def test_d13_plugin_command_bare_ref_gets_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 indexes plugin commands too — a bare ref to one draws MISSING-001."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    cache_root = fake_home / ".claude" / "plugins" / "cache"
    cmd_md = cache_root / "market" / "some-plugin" / "1.0.0" / "commands" / "plug-cmd.md"
    _write(cmd_md, "---\nname: plug-cmd\n---\nA plugin command.")
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        '---\nname: a\n---\nDispatch: Skill({skill: "plug-cmd"})',
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-MISSING-001" in r.message for r in report.results)


def test_d13_inline_code_cli_flag_is_not_a_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 never flags a CLI flag inside an inline `code span` (Windows /h, /tn, /c)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "skills" / "s" / "SKILL.md",
        "---\nname: s\n---\nRun `powercfg /h off` then `cmd /c ver` to check.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any("RC-NAMESPACE" in r.message for r in report.results)


def test_d13_unmatched_backtick_fails_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unmatched backtick yields no code span, so the match still FIRES (FN-safe)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        "---\nname: a\n---\nA stray ` backtick then /ghost-open-cmd here.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-UNRESOLVED-001" in r.message for r in report.results)


def test_d13_http_route_in_prose_is_not_a_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D13 never flags an HTTP route — "GET /health", "the /search endpoint"."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        "---\nname: a\n---\nBuild GET /health and POST /echo; the /search endpoint is slow.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert not any("RC-NAMESPACE" in r.message for r in report.results)


def test_d13_skill_call_inside_backticks_still_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive control: the inline-code carve-out applies to /slash only, never Skill()."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        '---\nname: a\n---\nDispatch with `Skill({skill: "ghost-in-code"})` here.',
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-UNRESOLVED-001" in r.message for r in report.results)


def test_d13_slash_outside_backticks_on_same_line_still_fires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: a real invocation beside an inline code span still fires."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        "---\nname: a\n---\nRun `powercfg /h off` then invoke /ghost-cmd-here to finish.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-UNRESOLVED-001" in r.message for r in report.results)


def test_d13_unknown_slash_name_still_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive control: the built-in/fs-root carve-outs do not mute a genuine ghost."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    root = fake_home / ".claude"
    _write(
        root / "agents" / "a" / "a.md",
        "---\nname: a\n---\nInvoke /definitely-ghost-cmd when done.",
    )
    report = ValidationReport()
    m.check_namespace_correctness(root, report)
    assert any("RC-NAMESPACE-UNRESOLVED-001" in r.message for r in report.results)
