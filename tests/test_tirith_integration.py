"""Tests for the tirith external scanner integration (Check #17).

Tirith is invoked as an external binary so cpv stays MIT-clean (the scanner
itself is AGPL-3.0). Tests exercise:

* the resolution order in ``_resolve_tirith_runner`` (PATH > docker > nix >
  install fallback, with ``CPV_NO_TIRITH_INSTALL`` honored)
* the JSON-shape parser inside ``check_tirith_scanner`` against the three
  documented response shapes (top-level list, ``{"findings": [...]}``,
  SARIF ``{"runs": [{"results": [...]}]}``)
* end-to-end ``--no-tirith`` opt-out via subprocess
* end-to-end runner via a fake ``tirith`` shim placed on PATH
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_security  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

# -----------------------------------------------------------------------------
# _resolve_tirith_runner — pure resolution logic
# -----------------------------------------------------------------------------


def test_resolver_prefers_path_when_tirith_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """If tirith is already on PATH, no docker/nix probe is needed."""
    monkeypatch.setattr(validate_security.shutil, "which", lambda name: "/usr/local/bin/tirith" if name == "tirith" else None)
    runner = validate_security._resolve_tirith_runner()
    assert runner == (["tirith"], "local")


def test_resolver_falls_back_to_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker is the second-preference runner — no install, no host mutation."""
    available = {"docker": "/usr/local/bin/docker"}
    monkeypatch.setattr(validate_security.shutil, "which", lambda name: available.get(name))
    runner = validate_security._resolve_tirith_runner()
    assert runner is not None
    prefix, mode = runner
    assert mode == "docker"
    assert prefix[0] == "docker"
    assert validate_security.TIRITH_IMAGE in prefix


def test_resolver_falls_back_to_nix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nix is preferred over auto-install when neither tirith nor docker is on PATH."""
    available = {"nix": "/run/current-system/sw/bin/nix"}
    monkeypatch.setattr(validate_security.shutil, "which", lambda name: available.get(name))
    runner = validate_security._resolve_tirith_runner()
    assert runner is not None
    prefix, mode = runner
    assert mode == "nix"
    assert prefix[:2] == ["nix", "run"]


def test_resolver_returns_none_when_install_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPV_NO_TIRITH_INSTALL=1 disables the install fallback even when brew/npm/cargo are present."""
    available = {"brew": "/opt/homebrew/bin/brew"}  # would normally trigger install
    monkeypatch.setattr(validate_security.shutil, "which", lambda name: available.get(name))
    monkeypatch.setenv("CPV_NO_TIRITH_INSTALL", "1")
    assert validate_security._resolve_tirith_runner() is None


def test_resolver_returns_none_when_nothing_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """No PATH hit, no docker, no nix, no installer probe — give up cleanly."""
    monkeypatch.setattr(validate_security.shutil, "which", lambda _name: None)
    monkeypatch.setenv("CPV_NO_TIRITH_INSTALL", "1")
    assert validate_security._resolve_tirith_runner() is None


# -----------------------------------------------------------------------------
# check_tirith_scanner — JSON shape parsing via fake-binary shim
# -----------------------------------------------------------------------------


def _write_shim(tmp_path: Path, name: str, json_payload: str, exit_code: int = 0) -> Path:
    """Write a tiny POSIX shell shim that emits ``json_payload`` on stdout.

    The shim ignores all arguments. This lets us drop a fake ``tirith``
    binary onto PATH and exercise ``check_tirith_scanner`` end-to-end
    without ever touching docker, nix, or a real install.
    """
    shim = tmp_path / name
    # printf %s preserves the literal JSON without injecting trailing newlines
    # that would change downstream parsing semantics.
    shim.write_text(
        "#!/bin/sh\n"
        f"printf '%s' {json_payload!r}\n"
        f"exit {exit_code}\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def _run_with_shim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str, exit_code: int = 0) -> ValidationReport:
    """Place a fake ``tirith`` on PATH, run the check, return the populated report."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    _write_shim(bin_dir, "tirith", payload, exit_code)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    report = ValidationReport()
    validate_security.check_tirith_scanner(plugin, report)
    return report


def test_check_tirith_top_level_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tirith JSON shape #1: bare list of finding objects."""
    payload = '[{"severity": "high", "rule": "pipe_to_interpreter", "message": "curl | bash detected", "file": "install.sh", "line": 42}]'
    report = _run_with_shim(monkeypatch, tmp_path, payload)
    msgs = [r.message for r in report.results]
    assert any("tirith pipe_to_interpreter" in m for m in msgs)
    # high → major severity
    assert any(r.level == "MAJOR" for r in report.results)


def test_check_tirith_findings_object(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tirith JSON shape #2: ``{"findings": [...]}`` wrapper."""
    payload = '{"findings": [{"verdict": "block", "ruleId": "homograph", "description": "Cyrillic lookalike domain", "location": {"file": "README.md", "line": 7}}]}'
    report = _run_with_shim(monkeypatch, tmp_path, payload)
    msgs = [r.message for r in report.results]
    assert any("tirith homograph" in m for m in msgs)


def test_check_tirith_sarif_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tirith JSON shape #3: SARIF runs/results structure."""
    payload = '{"runs": [{"results": [{"level": "low", "rule_id": "ansi_escape", "title": "ANSI escape sequence found"}]}]}'
    report = _run_with_shim(monkeypatch, tmp_path, payload)
    msgs = [r.message for r in report.results]
    assert any("tirith ansi_escape" in m for m in msgs)
    # low → warning
    assert any(r.level == "WARNING" for r in report.results)


def test_check_tirith_empty_clean_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Empty findings + exit 0 surfaces a PASSED message, no findings."""
    report = _run_with_shim(monkeypatch, tmp_path, "[]", exit_code=0)
    passed = [r.message for r in report.results if r.level == "PASSED"]
    assert any("tirith" in m and "no findings" in m for m in passed)


def test_check_tirith_unavailable_emits_one_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When no runner is reachable + install is disabled, one WARNING is added."""
    monkeypatch.setattr(validate_security.shutil, "which", lambda _name: None)
    monkeypatch.setenv("CPV_NO_TIRITH_INSTALL", "1")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    report = ValidationReport()
    validate_security.check_tirith_scanner(plugin, report)
    warnings = [r.message for r in report.results if r.level == "WARNING"]
    assert any("tirith" in m for m in warnings)


# -----------------------------------------------------------------------------
# CLI integration — tirith always runs (no opt-out flag)
# -----------------------------------------------------------------------------


def test_tirith_always_runs_via_cli(tmp_path: Path) -> None:
    """tirith ALWAYS runs from the CLI — there is no opt-out flag.

    The validator was changed so external scanners are no longer optional.
    Each scanner self-skips with an INFO marker if its source binary cannot
    be resolved on PATH or installed from its source URL.

    Reproducer: build a minimal plugin tree, run validate_security.py with
    ``CPV_NO_TIRITH_INSTALL=1`` (suppresses auto-install). When tirith is
    absent from PATH, the scan still INVOKES the tirith check — the check
    self-skips and emits a "tirith: scanner not available" message. That
    presence is the contract we now assert.

    The legacy ``--no-tirith`` flag was removed; passing it would cause an
    argparse "unrecognized arguments" error.
    """
    plugin = tmp_path / "demo-plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "0.0.1", "description": "test"}\n'
    )

    env = {**os.environ, "CPV_NO_TIRITH_INSTALL": "1"}
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "validate_security.py"),
            str(plugin),
            "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode in (0, 1, 2, 3), (
        f"unexpected exit {result.returncode}\nstderr: {result.stderr}"
    )
    # tirith runs unconditionally now. When the scanner binary is absent and
    # auto-install is disabled, the check_tirith_scanner step self-skips and
    # emits an advisory message starting with "tirith". That advisory IS the
    # signal we use to confirm the check ran end-to-end.
    import json as _json
    payload = _json.loads(result.stdout)
    messages = [r.get("message", "") for r in payload.get("results", [])]
    tirith_msgs = [m for m in messages if m.lower().startswith("tirith")]
    # Either the tirith check ran and self-skipped (one of: scanner not
    # available, install disabled, install failed) OR — when an operator
    # has tirith available locally — it produced findings/PASSED messages.
    # Either way we expect at least one tirith-prefixed message to confirm
    # the always-runs contract.
    assert tirith_msgs, (
        "Expected at least one 'tirith' message confirming the check ran. "
        "External scanners are now non-optional; the check should self-skip "
        "with an advisory rather than being silently bypassed."
    )


def test_legacy_no_tirith_flag_is_rejected(tmp_path: Path) -> None:
    """Old ``--no-tirith`` flag was removed; argparse must reject it.

    Tests the negative side of the contract change: callers passing the
    legacy opt-out flag get an explicit error rather than silently having
    the flag ignored. This guarantees no caller ever thinks they're
    skipping the scanner when in fact they aren't.
    """
    plugin = tmp_path / "demo-plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "0.0.1"}\n'
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "validate_security.py"),
            str(plugin),
            "--no-tirith",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr or "--no-tirith" in result.stderr
