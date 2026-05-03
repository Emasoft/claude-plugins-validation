"""Tests for the Phase 6 slurp flags in generate_plugin_repo.py.

Covers --from / --skill / --agent / --command / --mcp-server / --scripts
classification + copy behavior.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import generate_plugin_repo as g  # noqa: E402

# ── frontmatter parser ──────────────────────────────────────────────────────


def test_read_md_frontmatter_basic(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("---\nname: foo\ndescription: bar\n---\n# body\n", encoding="utf-8")
    fm = g._read_md_frontmatter(f)
    assert fm["name"] == "foo"
    assert fm["description"] == "bar"


def test_read_md_frontmatter_no_frontmatter(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("just content\n", encoding="utf-8")
    assert g._read_md_frontmatter(f) == {}


def test_read_md_frontmatter_missing_close(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("---\nname: foo\n# never closed", encoding="utf-8")
    assert g._read_md_frontmatter(f) == {}


def test_read_md_frontmatter_missing_file(tmp_path):
    assert g._read_md_frontmatter(tmp_path / "no.md") == {}


# ── classifier ───────────────────────────────────────────────────────────────


def test_classify_md_skill_filename(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: my-skill\n---\n", encoding="utf-8")
    assert g._classify_md(f) == "skill"


def test_classify_md_command_via_allowed_tools(tmp_path):
    f = tmp_path / "do-thing.md"
    f.write_text("---\nname: do-thing\nallowed-tools: Bash(uv:*)\n---\n", encoding="utf-8")
    assert g._classify_md(f) == "command"


def test_classify_md_default_agent(tmp_path):
    f = tmp_path / "helper.md"
    f.write_text("---\nname: helper\ndescription: x\ntools: Read, Write\n---\n", encoding="utf-8")
    assert g._classify_md(f) == "agent"


# ── slurp_one ────────────────────────────────────────────────────────────────


@pytest.fixture
def empty_plugin(tmp_path):
    """A bare plugin tree (just .claude-plugin/plugin.json) — slurp targets."""
    plugin = tmp_path / "p"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "version": "0.1.0", "description": "x"}),
    )
    return plugin


def test_slurp_skill_dir_with_skill_md(empty_plugin, tmp_path):
    src = tmp_path / "src" / "my-skill"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: my-skill\n---\n# x\n", encoding="utf-8")
    (src / "references" / "ref1.md").parent.mkdir(parents=True, exist_ok=True)
    (src / "references" / "ref1.md").write_text("# ref\n", encoding="utf-8")
    n = g._slurp_one(empty_plugin, src, "skill")
    assert n == 2
    assert (empty_plugin / "skills" / "my-skill" / "SKILL.md").is_file()
    assert (empty_plugin / "skills" / "my-skill" / "references" / "ref1.md").is_file()


def test_slurp_skill_md_file_only(empty_plugin, tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: imported\n---\n# x\n", encoding="utf-8")
    n = g._slurp_one(empty_plugin, f, "skill")
    assert n == 1
    assert (empty_plugin / "skills" / "imported" / "SKILL.md").is_file()


def test_slurp_agent(empty_plugin, tmp_path):
    f = tmp_path / "my-agent.md"
    f.write_text("---\nname: my-agent\ndescription: x\n---\n", encoding="utf-8")
    n = g._slurp_one(empty_plugin, f, "agent")
    assert n == 1
    assert (empty_plugin / "agents" / "my-agent.md").is_file()


def test_slurp_command(empty_plugin, tmp_path):
    f = tmp_path / "do.md"
    f.write_text("---\nname: do\nallowed-tools: Bash\n---\n", encoding="utf-8")
    n = g._slurp_one(empty_plugin, f, "command")
    assert n == 1
    assert (empty_plugin / "commands" / "do.md").is_file()


def test_slurp_mcp_json_only(empty_plugin, tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text('{"mcpServers": {}}', encoding="utf-8")
    n = g._slurp_one(empty_plugin, f, "mcp")
    assert n == 1
    assert (empty_plugin / ".mcp.json").is_file()


def test_slurp_mcp_directory(empty_plugin, tmp_path):
    src = tmp_path / "mcp-bundle"
    src.mkdir()
    (src / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    (src / "server.js").write_text("console.log('hi')", encoding="utf-8")
    n = g._slurp_one(empty_plugin, src, "mcp")
    assert n == 2
    assert (empty_plugin / ".mcp.json").is_file()
    assert (empty_plugin / "mcp-server" / "server.js").is_file()


def test_slurp_scripts_dir(empty_plugin, tmp_path):
    src = tmp_path / "myscripts"
    src.mkdir()
    (src / "a.py").write_text("# a\n", encoding="utf-8")
    (src / "b.py").write_text("# b\n", encoding="utf-8")
    n = g._slurp_one(empty_plugin, src, "scripts")
    assert n == 2
    assert (empty_plugin / "scripts" / "a.py").is_file()
    assert (empty_plugin / "scripts" / "b.py").is_file()


def test_slurp_warns_on_invalid_input(empty_plugin, tmp_path, capsys):
    """Invalid type for the requested kind → WARN, return 0."""
    f = tmp_path / "not-skill.md"
    f.write_text("# not a skill", encoding="utf-8")
    n = g._slurp_one(empty_plugin, f, "skill")  # not SKILL.md, not a dir
    assert n == 0


# ── _do_slurp end-to-end ────────────────────────────────────────────────────


def test_do_slurp_combines_all_kinds(empty_plugin, tmp_path):
    skill = tmp_path / "MySkill" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: myskill\n---\n", encoding="utf-8")

    agent = tmp_path / "ag.md"
    agent.write_text("---\nname: ag\n---\n", encoding="utf-8")

    cmd = tmp_path / "cmd.md"
    cmd.write_text("---\nname: cmd\nallowed-tools: Bash\n---\n", encoding="utf-8")

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "x.py").write_text("# x", encoding="utf-8")

    n = g._do_slurp(
        empty_plugin,
        from_paths=[],
        skill_paths=[skill],
        agent_paths=[agent],
        command_paths=[cmd],
        mcp_paths=[],
        scripts_paths=[scripts_dir],
    )
    assert n >= 4
    assert (empty_plugin / "skills" / "myskill" / "SKILL.md").is_file()
    assert (empty_plugin / "agents" / "ag.md").is_file()
    assert (empty_plugin / "commands" / "cmd.md").is_file()
    assert (empty_plugin / "scripts" / "x.py").is_file()


def test_do_slurp_from_autoclassifies_skill_md(empty_plugin, tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: auto-classified\n---\n", encoding="utf-8")
    n = g._do_slurp(empty_plugin, from_paths=[f], skill_paths=[],
                    agent_paths=[], command_paths=[],
                    mcp_paths=[], scripts_paths=[])
    assert n == 1
    assert (empty_plugin / "skills" / "auto-classified" / "SKILL.md").is_file()


def test_do_slurp_from_autoclassifies_command(empty_plugin, tmp_path):
    f = tmp_path / "do.md"
    f.write_text("---\nname: do\nallowed-tools: Bash\n---\n", encoding="utf-8")
    n = g._do_slurp(empty_plugin, from_paths=[f], skill_paths=[],
                    agent_paths=[], command_paths=[],
                    mcp_paths=[], scripts_paths=[])
    assert n == 1
    assert (empty_plugin / "commands" / "do.md").is_file()


def test_do_slurp_from_autoclassifies_agent(empty_plugin, tmp_path):
    f = tmp_path / "ag.md"
    f.write_text("---\nname: ag\ndescription: x\n---\n", encoding="utf-8")
    n = g._do_slurp(empty_plugin, from_paths=[f], skill_paths=[],
                    agent_paths=[], command_paths=[],
                    mcp_paths=[], scripts_paths=[])
    assert n == 1
    assert (empty_plugin / "agents" / "ag.md").is_file()


def test_do_slurp_from_autoclassifies_mcp(empty_plugin, tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text('{"mcpServers":{}}', encoding="utf-8")
    n = g._do_slurp(empty_plugin, from_paths=[f], skill_paths=[],
                    agent_paths=[], command_paths=[],
                    mcp_paths=[], scripts_paths=[])
    assert n == 1
    assert (empty_plugin / ".mcp.json").is_file()


def test_do_slurp_from_skips_nonexistent(empty_plugin, tmp_path):
    n = g._do_slurp(empty_plugin, from_paths=[tmp_path / "nope"],
                    skill_paths=[], agent_paths=[], command_paths=[],
                    mcp_paths=[], scripts_paths=[])
    assert n == 0


# ── End-to-end: real generator + slurp ─────────────────────────────────────


def test_generator_with_from_flag_e2e(tmp_path):
    """Spawn the real generator with --from and verify slurped files arrive."""
    src_skill = tmp_path / "my-skill" / "SKILL.md"
    src_skill.parent.mkdir()
    src_skill.write_text("---\nname: my-skill\n---\n# x\n", encoding="utf-8")

    target = tmp_path / "out"
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "generate_plugin_repo.py"),
        str(target),
        "--name", "demo", "--description", "x",
        "--author", "A", "--author-email", "a@a.a",
        "--github-owner", "Emasoft",
        "--from", str(src_skill),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False,
                         env={"PLUGIN_SKIP_GITHUB_INTEGRITY": "1",
                              "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert res.returncode == 0, f"generator failed: {res.stderr}\n{res.stdout}"
    assert (target / "skills" / "my-skill" / "SKILL.md").is_file(), \
        f"Slurped skill missing. stdout: {res.stdout}"
