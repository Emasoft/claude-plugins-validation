"""TRDD-d1f74670 wiring: validate_local_scope's CLI runs the D9..D13 user-scope
recipes when (and only when) the audited target IS ``~/.claude``."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_local_scope  # noqa: E402


def _run_main(monkeypatch, capsys, target: Path) -> dict:
    monkeypatch.setattr(sys, "argv", ["validate_local_scope.py", str(target), "--json"])
    rc = validate_local_scope.main()
    payload = json.loads(capsys.readouterr().out)
    payload["_rc"] = rc
    return payload


def _make_stub_skill(claude_dir: Path) -> None:
    skill = claude_dir / "skills" / "broken-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: broken-skill\ndescription: x\n---\n404: Not Found\n",
        encoding="utf-8",
    )


def test_recipes_run_when_target_is_home_claude(monkeypatch, capsys, tmp_path):
    """Auditing ~/.claude itself surfaces D10 stub-file findings (RC-STUB-FILE-001)."""
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    _make_stub_skill(claude_dir)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    payload = _run_main(monkeypatch, capsys, claude_dir)
    msgs = [r["message"] for r in payload["results"]]
    assert any("RC-STUB-FILE-001" in m for m in msgs), msgs


def test_recipes_skipped_for_ordinary_project_target(monkeypatch, capsys, tmp_path):
    """The same stub under a NON-home target draws no RC-STUB-FILE finding (recipes gated to ~/.claude)."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    project = tmp_path / "some-project"
    _make_stub_skill(project / ".claude")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    payload = _run_main(monkeypatch, capsys, project)
    msgs = [r["message"] for r in payload["results"]]
    assert not any("RC-STUB-FILE-001" in m for m in msgs), msgs
