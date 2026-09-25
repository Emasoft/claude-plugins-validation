"""w9-followups #3b — the same unconditional-CI-backstop shape #228 fixed for
jscpd/cspell/checkov/etc still remained in ``_gate_actionlint``'s and
``_gate_mypy``'s "CI still enforces it" skip lines: they claimed a CI backstop
whenever the TOOL was merely absent/could-not-run locally, with no check that
any workflow actually RUNS actionlint or mypy.

Fixed with two new module constants mirroring ``MEGALINTER_WORKFLOW_PATTERN``:

* ``ACTIONLINT_WORKFLOW_PATTERN`` / ``_actionlint_workflow_wired`` — true when a
  workflow line names ``rhysd/actionlint`` (its own action, or the docker form)
  or literally runs the ``actionlint`` binary.
* ``MYPY_WORKFLOW_RUN_PATTERN`` / ``_mypy_workflow_wired`` — true when Mega-Linter
  is wired AND its config enables ``PYTHON_MYPY``, OR a workflow directly
  ``run:``s the ``mypy`` CLI.

Both gates now print "CI still enforces it" ONLY when the corresponding *_wired
check is True; otherwise they print a "this check was NOT run, and no workflow
enforces it" message that claims no backstop.

Two-sided per gate, plus the load-bearing FN test: CPV's own generated ci.yml
(the real ``generate_plugin_repo`` output) must be detected as running BOTH
actionlint and mypy — a fresh scaffold's tool-absent WARNING must keep today's
"CI ... enforces it" wording, never regress to the "NOT run" wording.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_ci_preflight as preflight  # noqa: E402
import generate_plugin_repo as gen  # noqa: E402
from cpv_ci_preflight import PreflightResult  # noqa: E402


def _workflow(root: Path, body: str, name: str = "ci.yml") -> None:
    wf = root / ".github" / "workflows" / name
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(
        "name: CI\non: [push]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n" + body,
        encoding="utf-8",
    )


def _mega_linter(root: Path, linters: list[str]) -> None:
    body = "APPLY_FIXES: none\nENABLE_LINTERS:\n" + "".join(f"  - {x}\n" for x in linters)
    (root / ".mega-linter.yml").write_text(body, encoding="utf-8")


# ── _actionlint_workflow_wired ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "      - uses: rhysd/actionlint@914e7df21a07ef503a81201c76d2b11c789d3fca # v1.7.12\n",
        "      - uses: docker://rhysd/actionlint:latest\n",
        "      - run: actionlint\n",
        "      - run: |\n          actionlint -color\n",
    ],
)
def test_actionlint_wired_forms_are_detected(tmp_path: Path, body: str) -> None:
    _workflow(tmp_path, body)
    assert preflight._actionlint_workflow_wired(tmp_path) is True


def test_actionlint_not_wired_on_a_plain_repo(tmp_path: Path) -> None:
    assert preflight._actionlint_workflow_wired(tmp_path) is False


def test_actionlint_comment_mentioning_it_does_not_wire(tmp_path: Path) -> None:
    _workflow(tmp_path, "      # actionlint runs elsewhere: rhysd/actionlint\n")
    assert preflight._actionlint_workflow_wired(tmp_path) is False


def test_actionlint_unrelated_workflow_does_not_wire(tmp_path: Path) -> None:
    _workflow(tmp_path, "      - uses: actions/checkout@v4\n")
    assert preflight._actionlint_workflow_wired(tmp_path) is False


# ── _mypy_workflow_wired ─────────────────────────────────────────────────────


def test_mypy_wired_via_megalinter_python_mypy(tmp_path: Path) -> None:
    _workflow(tmp_path, "      - uses: oxsecurity/megalinter@e08c2b05e3dbc40af4c23f41172ef1e068a7d651 # v8\n")
    _mega_linter(tmp_path, ["PYTHON_MYPY"])
    assert preflight._mypy_workflow_wired(tmp_path) is True


def test_mypy_not_wired_when_megalinter_wired_but_mypy_not_enabled(tmp_path: Path) -> None:
    _workflow(tmp_path, "      - uses: oxsecurity/megalinter@e08c2b05e3dbc40af4c23f41172ef1e068a7d651 # v8\n")
    _mega_linter(tmp_path, ["PYTHON_RUFF"])  # no PYTHON_MYPY
    assert preflight._mypy_workflow_wired(tmp_path) is False


def test_mypy_direct_run_without_megalinter(tmp_path: Path) -> None:
    _workflow(tmp_path, "      - run: uv run mypy scripts/ --ignore-missing-imports\n")
    assert preflight._mypy_workflow_wired(tmp_path) is True


def test_mypy_not_wired_on_a_plain_repo(tmp_path: Path) -> None:
    assert preflight._mypy_workflow_wired(tmp_path) is False


def test_mypy_comment_mentioning_it_does_not_wire(tmp_path: Path) -> None:
    _workflow(tmp_path, "      # we also run mypy in a separate job\n")
    assert preflight._mypy_workflow_wired(tmp_path) is False


# ── _gate_actionlint wording ─────────────────────────────────────────────────


def test_gate_actionlint_absent_tool_claims_backstop_only_when_wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workflow(tmp_path, "      - uses: rhysd/actionlint@914e7df21a07ef503a81201c76d2b11c789d3fca # v1.7.12\n")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    result = PreflightResult(plugin_path=tmp_path)
    preflight._gate_actionlint(result)
    f = result.findings[0]
    assert f.severity == "WARNING"
    assert "CI's Lint job runs actionlint" in f.message


def test_gate_actionlint_absent_tool_does_not_claim_backstop_when_not_wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workflow(tmp_path, "      - uses: actions/checkout@v4\n")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    result = PreflightResult(plugin_path=tmp_path)
    preflight._gate_actionlint(result)
    f = result.findings[0]
    assert f.severity == "WARNING"
    assert "NOT run" in f.message
    assert "no .github/workflows/ file runs actionlint" in f.message


def test_gate_actionlint_could_not_run_claims_backstop_only_when_wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workflow(tmp_path, "      - uses: actions/checkout@v4\n")  # not wired
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/actionlint")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr(preflight.subprocess, "run", boom)
    result = PreflightResult(plugin_path=tmp_path)
    preflight._gate_actionlint(result)
    f = result.findings[0]
    assert f.severity == "WARNING"
    assert "NOT run" in f.message


# ── _gate_mypy wording ────────────────────────────────────────────────────────


def _plugin_with_scripts(tmp_path: Path) -> Path:
    root = tmp_path / "plug"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return root


def test_gate_mypy_absent_tool_claims_backstop_only_when_wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _plugin_with_scripts(tmp_path)
    _workflow(root, "      - run: uv run mypy scripts/ --ignore-missing-imports\n")
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    result = PreflightResult(plugin_path=root)
    preflight._gate_mypy(result)
    f = result.findings[0]
    assert f.severity == "WARNING"
    assert "CI's Lint job runs mypy" in f.message


def test_gate_mypy_absent_tool_does_not_claim_backstop_when_not_wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _plugin_with_scripts(tmp_path)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    result = PreflightResult(plugin_path=root)
    preflight._gate_mypy(result)
    f = result.findings[0]
    assert f.severity == "WARNING"
    assert "NOT run" in f.message
    assert "does not run mypy" in f.message


# ── load-bearing FN test: CPV's own generated ci.yml is detected as wired ────


def _scaffold(tmp_path: Path, name: str = "issue-228b-sample") -> Path:
    params = gen.PluginParams(
        name=name, description="x", author="A", author_email="a@a.a", github_owner="Emasoft",
    )
    target = tmp_path / name
    target.mkdir()
    gen.generate_plugin_repo(target, params)
    return target


def test_generated_ci_yml_is_detected_as_running_actionlint(tmp_path: Path) -> None:
    target = _scaffold(tmp_path)
    assert preflight._actionlint_workflow_wired(target) is True


def test_generated_ci_yml_is_detected_as_running_mypy(tmp_path: Path) -> None:
    target = _scaffold(tmp_path)
    assert preflight._mypy_workflow_wired(target) is True


def test_generated_scaffold_keeps_todays_wording_when_actionlint_tool_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh scaffold, run on a box with no `actionlint` binary: the WARNING
    must keep the "CI's Lint job runs actionlint" wording — NEVER regress to
    the "this check was NOT run" wording, since the generated ci.yml genuinely
    runs actionlint."""
    target = _scaffold(tmp_path)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    result = PreflightResult(plugin_path=target)
    preflight._gate_actionlint(result)
    f = result.findings[0]
    assert f.severity == "WARNING"
    assert "CI's Lint job runs actionlint" in f.message
    assert "NOT run" not in f.message


def test_generated_scaffold_keeps_todays_wording_when_mypy_tool_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _scaffold(tmp_path)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    result = PreflightResult(plugin_path=target)
    preflight._gate_mypy(result)
    f = result.findings[0]
    assert f.severity == "WARNING"
    assert "CI's Lint job runs mypy" in f.message
    assert "NOT run" not in f.message
