"""The integrity check never skips silently: each skip warns on stderr and writes one audit line; a check that cannot verify refuses (issue #255)."""

from __future__ import annotations

import json
from pathlib import Path

import _plugin_verify_hashes as pvh
import pytest


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(pvh, "_VERIFIED_THIS_PROCESS", False)
    for var in ("PLUGIN_INTEGRITY_RELEASE_CANDIDATE", "PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pvh, "_AUDIT_LOG", tmp_path / "audit.log")
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "x", "version": "0.0.1"}))
    return plugin


def _lines() -> list[str]:
    log = pvh._AUDIT_LOG
    return log.read_text().splitlines() if log.exists() else []


def _run_and_check(root: Path, capsys: pytest.CaptureFixture[str], stderr_text: str, event: str) -> None:
    before = len(_lines())
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is True
    assert stderr_text in capsys.readouterr().err
    after = _lines()
    assert len(after) - before == 1
    assert json.loads(after[-1])["event"] == event


def _refuse_and_check(root: Path, capsys: pytest.CaptureFixture[str], stderr_text: str) -> None:
    """Issue #255: the check REFUSES (False), names the cause and the dev skip, and writes one `refused` line."""
    before = len(_lines())
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    err = capsys.readouterr().err
    assert stderr_text in err
    assert "PLUGIN_SKIP_GITHUB_INTEGRITY=1" in err
    assert "SKIPPED" not in err
    after = _lines()
    assert len(after) - before == 1
    assert json.loads(after[-1])["event"] == "refused"


def test_pytest_env_alone_is_not_a_skip(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Issue #255: PYTEST_CURRENT_TEST alone no longer skips; with no manifest the check refuses."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t::t (call)")
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda version: None)
    _refuse_and_check(root, capsys, "no hash manifest for tag v0.0.1")


def test_pytest_env_plus_skip_variable_audits_skip_variable(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PYTEST_CURRENT_TEST plus the skip variable warns (names the variable) and logs skip-variable-honoured."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t::t (call)")
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    _run_and_check(root, capsys, "SKIPPED by PLUGIN_SKIP_GITHUB_INTEGRITY", "skip-variable-honoured")


def test_skip_variable_without_pytest_env_audits(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard: the skip variable alone, outside a test run, warns and logs skip-variable-honoured."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    _run_and_check(root, capsys, "SKIPPED", "skip-variable-honoured")


def test_no_manifest_refuses_even_when_quiet(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No manifest available: the check refuses (issue #255 fail-closed), prints even when quiet, logs refused."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda version: None)
    _refuse_and_check(root, capsys, "no hash manifest for tag v0.0.1")


def test_malformed_manifest_refuses_even_when_quiet(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Malformed manifest: the check refuses (issue #255 fail-closed), prints even when quiet, logs refused."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda version: {"files": ["x"]})
    _refuse_and_check(root, capsys, "malformed or empty")
