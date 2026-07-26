"""The launch-time agent preflight: detect broken agent definitions, and stay silent otherwise.

`cpv-agent` runs this on every launch, so it has two failure modes of equal weight:

* MISSING a real defect — the preflight is then decoration, and a broken agent (an MCP
  grant that silently matches nothing, a duplicate frontmatter key that discards half the
  tool list) keeps failing at runtime with no diagnostic;
* FIRING on a clean corpus — `cpv-agent` would then dispatch a fixer to edit the user's
  agent files on every unrelated launch, which is worse than the bug it set out to fix.

So every detection test below is paired with a silence test. The INFO-does-not-block test is
the specific guard against per-launch churn.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.cpv_agent_preflight import (  # noqa: E402
    EXIT_CLEAN,
    EXIT_ERROR,
    EXIT_FINDINGS,
    main,
    preflight,
    resolve_agent_dirs,
)

# The body must clear the validator's minimum length — a too-short body is itself a
# MINOR finding, and CPV counts MINOR as blocking. A fixture that trips an unrelated
# rule would make the silence tests below prove nothing.
_BODY = """Do one small, well-scoped thing and report the result as a single line of text.

Read only what the task needs, make the change, verify it, and return a one-line
status plus the path to any report you wrote. Never edit files outside the task scope.
"""

CLEAN_AGENT = f"""---
name: {{name}}
description: A deliberately clean test agent used to prove the preflight stays silent on it.
---

# {{name}}

{_BODY}"""

DUP_KEY_AGENT = f"""---
name: dupkey-agent
description: A test agent whose frontmatter duplicates the tools key, discarding the first.
tools: Read
tools: Read, Write
---

# dupkey-agent

{_BODY}"""


def _write_agent(directory: Path, filename: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# resolve_agent_dirs — what is in scope for one launch
# --------------------------------------------------------------------------


def test_resolves_user_scope_agents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_agent(home / ".claude" / "agents", "a.md", CLEAN_AGENT.format(name="a"))
    dirs = resolve_agent_dirs(None, home=home)
    assert dirs == [(home / ".claude" / "agents").resolve()]


def test_resolves_target_plugin_agents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    target = tmp_path / "plugin"
    _write_agent(target / "agents", "b.md", CLEAN_AGENT.format(name="b"))
    dirs = resolve_agent_dirs(target, home=home)
    assert (target / "agents").resolve() in dirs


def test_resolves_project_scope_agents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    target = tmp_path / "proj"
    _write_agent(target / ".claude" / "agents", "c.md", CLEAN_AGENT.format(name="c"))
    dirs = resolve_agent_dirs(target, home=home)
    assert (target / ".claude" / "agents").resolve() in dirs


def test_missing_directories_are_skipped_not_errors(tmp_path: Path) -> None:
    """A plugin with no agents/ at all is the common case, not a failure."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    assert resolve_agent_dirs(tmp_path / "nonexistent", home=home) == []


def test_directory_without_md_files_is_skipped(tmp_path: Path) -> None:
    """An empty agents/ dir would make the underlying validator error out."""
    home = tmp_path / "home"
    (home / ".claude" / "agents").mkdir(parents=True)
    (home / ".claude" / "agents" / "README.txt").write_text("not an agent\n", encoding="utf-8")
    assert resolve_agent_dirs(None, home=home) == []


def test_same_directory_is_never_scanned_twice(tmp_path: Path) -> None:
    """LOAD-BEARING: a duplicate would be handed to the FIXER twice."""
    home = tmp_path / "home"
    agents = home / ".claude" / "agents"
    _write_agent(agents, "d.md", CLEAN_AGENT.format(name="d"))
    # target's project-scope dir IS the user-scope dir here
    dirs = resolve_agent_dirs(home, home=home)
    assert len(dirs) == len(set(dirs)) == 1


# --------------------------------------------------------------------------
# preflight — detection, and silence
# --------------------------------------------------------------------------


def test_clean_corpus_yields_clean_verdict(tmp_path: Path) -> None:
    """LOAD-BEARING: firing here would edit the user's agents on every launch."""
    agents = tmp_path / "agents"
    for n in ("one", "two", "three"):
        _write_agent(agents, f"{n}.md", CLEAN_AGENT.format(name=n))
    result = preflight([agents])
    assert result["verdict"] == "CLEAN", f"clean corpus reported findings: {result['blocking']}"
    assert result["scanned"] == 3
    assert result["blocking_count"] == 0


def test_duplicate_frontmatter_key_is_detected(tmp_path: Path) -> None:
    """The defect that motivated this: YAML silently keeps the LAST duplicate key."""
    agents = tmp_path / "agents"
    _write_agent(agents, "clean.md", CLEAN_AGENT.format(name="clean"))
    _write_agent(agents, "dupkey.md", DUP_KEY_AGENT)
    result = preflight([agents])
    assert result["verdict"] == "FINDINGS"
    assert result["blocking_count"] == 1, f"expected only the dup-key agent: {result['blocking']}"
    flagged = result["blocking"][0]
    assert flagged["path"].endswith("dupkey.md")
    assert any("uplicate" in f["message"] for f in flagged["findings"]), flagged["findings"]


def test_only_blocking_severities_are_reported(tmp_path: Path) -> None:
    """INFO/PASSED must never reach the fixer — auto-editing advisories churns files."""
    agents = tmp_path / "agents"
    _write_agent(agents, "dupkey.md", DUP_KEY_AGENT)
    result = preflight([agents])
    levels = {f["level"] for item in result["blocking"] for f in item["findings"]}
    assert levels, "no findings captured at all"
    assert levels <= {"CRITICAL", "MAJOR", "MINOR", "NIT"}, f"advisory level leaked through: {levels}"


def test_scanned_count_covers_every_directory(tmp_path: Path) -> None:
    """A silently-dropped directory would look identical to a clean one."""
    a, b = tmp_path / "a", tmp_path / "b"
    _write_agent(a, "one.md", CLEAN_AGENT.format(name="one"))
    _write_agent(b, "two.md", CLEAN_AGENT.format(name="two"))
    assert preflight([a, b])["scanned"] == 2


# --------------------------------------------------------------------------
# main — exit codes are the contract cpv-agent branches on
# --------------------------------------------------------------------------


def test_main_exits_clean_on_clean_corpus(tmp_path: Path, capsys) -> None:
    agents = tmp_path / "agents"
    _write_agent(agents, "ok.md", CLEAN_AGENT.format(name="ok"))
    assert main(["--dir", str(agents)]) == EXIT_CLEAN
    assert "CLEAN" in capsys.readouterr().out


def test_main_exits_findings_on_broken_agent(tmp_path: Path, capsys) -> None:
    agents = tmp_path / "agents"
    _write_agent(agents, "dupkey.md", DUP_KEY_AGENT)
    assert main(["--dir", str(agents)]) == EXIT_FINDINGS
    assert "FINDINGS" in capsys.readouterr().out


def test_main_errors_on_nonexistent_explicit_dir(tmp_path: Path) -> None:
    """An explicit --dir that does not exist is a caller mistake, not a clean result."""
    assert main(["--dir", str(tmp_path / "nope")]) == EXIT_ERROR


def test_main_reports_clean_when_nothing_is_in_scope(tmp_path: Path, capsys, monkeypatch) -> None:
    """No agent dirs anywhere must be CLEAN, never an error — most plugins ship no agents."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "empty-home"))
    assert main([str(tmp_path / "plugin-without-agents")]) == EXIT_CLEAN
    assert "CLEAN" in capsys.readouterr().out


def test_main_json_output_is_parseable(tmp_path: Path, capsys) -> None:
    import json

    agents = tmp_path / "agents"
    _write_agent(agents, "dupkey.md", DUP_KEY_AGENT)
    main(["--dir", str(agents), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "FINDINGS"
    assert payload["blocking"][0]["path"].endswith("dupkey.md")


# --------------------------------------------------------------------------
# The agent contract — cpv-agent must actually invoke this
# --------------------------------------------------------------------------


def test_cpv_agent_wires_the_preflight_in() -> None:
    """A preflight nothing calls is dead code; cpv-agent's body is what launches it."""
    body = (Path(__file__).resolve().parents[1] / "agents" / "cpv-agent.md").read_text(encoding="utf-8")
    assert "cpv_agent_preflight.py" in body, "cpv-agent no longer runs the agent preflight at launch"
    assert "cpv-plugin-fixer-agent" in body, "cpv-agent must delegate repairs to the fixer, never hand-edit"
