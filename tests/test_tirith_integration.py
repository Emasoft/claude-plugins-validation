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
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_security  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_tirith_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let this test file install a real ``tirith`` onto the host.

    ``check_tirith_scanner`` / ``_resolve_tirith_runner`` (validate_security.py)
    fall back to a real ``brew``/``npm``/``cargo`` install of tirith whenever
    ``CPV_NO_TIRITH_INSTALL`` is unset — a test suite must never mutate the
    developer's machine. This autouse fixture forces the opt-out for every
    test in this module, including the subprocess-invoked ones (monkeypatch's
    ``setenv`` mutates ``os.environ`` in place, which subprocess.run's default
    ``env=None`` inherits). Individual tests that already set the var
    explicitly are unaffected — setting it twice to the same value is a no-op.
    """
    monkeypatch.setenv("CPV_NO_TIRITH_INSTALL", "1")

# -----------------------------------------------------------------------------
# _resolve_tirith_runner — pure resolution logic
# -----------------------------------------------------------------------------


def test_resolver_prefers_path_when_tirith_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """If tirith is already on PATH, no docker/nix probe is needed."""
    monkeypatch.setattr(
        validate_security.shutil, "which", lambda name: "/usr/local/bin/tirith" if name == "tirith" else None
    )
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
    shim.write_text(f"#!/bin/sh\nprintf '%s' {json_payload!r}\nexit {exit_code}\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def _run_with_shim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: str, exit_code: int = 0
) -> ValidationReport:
    """Place a fake ``tirith`` on PATH, run the check, return the populated report.

    ``tmp_path`` may name a subdirectory that does not exist yet (callers
    that run several shims in one test pass distinct names)."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
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
    payload = (
        '{"runs": [{"results": [{"level": "low", "rule_id": "ansi_escape", "title": "ANSI escape sequence found"}]}]}'
    )
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


def test_check_tirith_nested_files_shape_trdd_dlmx817h(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tirith 0.4.x shape #4: findings NESTED under ``files[].findings[]``.

    The 0.4.x scan JSON carries per-file objects (path on the parent,
    findings dicts with ``rule_id`` + uppercase severity, no file key of
    their own). Before the parser gained this branch, a REAL findings-present
    scan parsed as zero findings and read "external scan clean" — a silent
    FN confirmed first-hand 2026-09-26 (TRDD-DLMX817H).
    """
    payload = (
        '{"total_findings": 2, "schema_version": 5, "files": ['
        '{"path": "/abs/plugin/install.sh", "findings": ['
        '{"severity": "HIGH", "rule_id": "workflow_dangerous_trigger", "description": "pull_request_target with rw token"},'
        '{"severity": "MEDIUM", "rule_id": "workflow_unpinned_action", "description": "unpinned action"}'
        ']}]}'
    )
    report = _run_with_shim(monkeypatch, tmp_path, payload)
    msgs = [r.message for r in report.results]
    # Both findings survive the parse — the FN was 0.
    assert any("tirith workflow_dangerous_trigger" in m for m in msgs)
    assert any("tirith workflow_unpinned_action" in m for m in msgs)
    # Severity map works unchanged on 0.4.x casing/keys: HIGH → major, MEDIUM → minor.
    assert any(r.level == "MAJOR" for r in report.results)
    assert any(r.level == "MINOR" for r in report.results)
    # The parent files[].path is threaded so gitignore/self-scan filters can
    # see the path (file must not be empty).
    file_refs = [str(r.file or "") for r in report.results if "tirith" in r.message]
    assert all(refs for refs in file_refs), f"file lost on nested findings: {file_refs!r}"
    assert any("install.sh" in ref for ref in file_refs)


def test_check_tirith_nested_files_with_empty_file_list_is_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Control: 0.4.x schema with files but NO findings still reads clean.

    Without this sibling, a fix that counts files[] entries could mislabel
    a genuinely clean scan as findings-present."""
    payload = '{"total_findings": 0, "files": [{"path": "/abs/plugin/x.sh", "findings": []}]}'
    report = _run_with_shim(monkeypatch, tmp_path, payload)
    passed = [r.message for r in report.results if r.level == "PASSED"]
    blocking = [r for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT")]
    assert any("tirith" in m and "no findings" in m for m in passed)
    assert blocking == []


def test_check_tirith_nested_and_legacy_shapes_coexist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Both-shapes coexistence: files[].findings[] AND a top-level findings
    key must BOTH survive the parse.

    The legacy key loop originally ASSIGNED the top-level list, silently
    discarding whatever _flatten_nested_files had already appended — the
    next schema-drift FN (review round 8). This test pins the extend
    semantics: 2 nested + 1 top-level = EXACTLY 3 findings.

    The count pin is load-bearing, not cosmetic: presence-only assertions
    (any(...)) cannot catch a cardinality regression — if a future parser
    change re-flattens or double-appends, all three rule-id messages still
    appear but the counts are wrong. A dual-shape payload as a COMPATIBILITY
    MIRROR (same findings under both shapes) would then double-count; this
    test can't distinguish coexist-from-mirror, but it guarantees the
    count is whatever the shapes literally add up to, so a mirroring
    regression at least changes a pinned number instead of passing
    silently."""
    payload = (
        '{"total_findings": 3, "files": ['
        '{"path": "/abs/plugin/one.sh", "findings": ['
        '{"severity": "HIGH", "rule_id": "rule_nested_one", "description": "nested finding one"}'
        "]},"
        '{"path": "/abs/plugin/two.sh", "findings": ['
        '{"severity": "HIGH", "rule_id": "rule_nested_two", "description": "nested finding two"}'
        "]}],"
        '"findings": ['
        '{"severity": "medium", "rule": "rule_top_level", "message": "top level finding", "file": "three.sh"}'
        "]}"
    )
    report = _run_with_shim(monkeypatch, tmp_path, payload)
    msgs = [r.message for r in report.results]
    assert any("tirith rule_nested_one" in m for m in msgs)
    assert any("tirith rule_nested_two" in m for m in msgs)
    assert any("tirith rule_top_level" in m for m in msgs)
    # Cardinality pin: exactly the three findings the payload carries, no
    # double-emission and no drop.
    tirith_msgs = [m for m in msgs if "tirith rule_" in m]
    assert len(tirith_msgs) == 3, f"expected exactly 3 tirith findings, got {len(tirith_msgs)}: {tirith_msgs!r}"


def test_check_tirith_nested_files_with_empty_top_level_findings_survive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The empty-shadow case the extend fix closed silently (review finding 3a):
    populated files[].findings[] PLUS an empty top-level ``findings: []``.

    Under the round-7 code the assign discarded the nested findings and the
    ``if not findings`` check then read the payload as CLEAN — a second FN
    sibling of the same root cause. Under extend they survive."""
    payload = (
        '{"total_findings": 2, "files": ['
        '{"path": "/abs/plugin/evil.sh", "findings": ['
        '{"severity": "HIGH", "rule_id": "rule_shadow_a", "description": "shadowed by empty top-level"},'
        '{"severity": "HIGH", "rule_id": "rule_shadow_b", "description": "also shadowed"}'
        "]}],"
        '"findings": []}'
    )
    report = _run_with_shim(monkeypatch, tmp_path, payload)
    msgs = [r.message for r in report.results]
    assert any("tirith rule_shadow_a" in m for m in msgs)
    assert any("tirith rule_shadow_b" in m for m in msgs)
    blocking = [r for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT")]
    assert len(blocking) == 2, f"expected 2 blocking findings, got {len(blocking)}"


def test_check_tirith_total_findings_mismatch_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Schema-drift canary (review round 9): a parse that misses a shape
    leaves len(findings) short of the scanner's own total_findings count —
    the exact FN the 0.4.x nesting fix closed, now caught loudly.

    A parser regression that drops findings must NOT read clean again."""
    payload = (
        '{"total_findings": 2, "files": ['
        '{"path": "/abs/plugin/x.sh", "findings": ['
        '{"severity": "HIGH", "rule_id": "rule_a", "description": "one"},'
        '{"severity": "HIGH", "rule_id": "rule_b", "description": "two"}'
        "]}],"
        '"other_findings_key": []}'
    )
    report = _run_with_shim(monkeypatch, tmp_path, payload)
    msgs = [r.message for r in report.results]
    # Neither legacy key matches, so the nested findings are the only ones —
    # count matches and NO canary warning fires.
    assert not any("total_findings" in m for m in msgs)
    assert any("tirith rule_a" in m for m in msgs)

    # Mismatch: the payload claims 5 but the parser can only see 2.
    payload_mismatch = (
        '{"total_findings": 5, "files": ['
        '{"path": "/abs/plugin/x.sh", "findings": ['
        '{"severity": "HIGH", "rule_id": "rule_a", "description": "one"},'
        '{"severity": "HIGH", "rule_id": "rule_b", "description": "two"}'
        "]}]}"
    )
    report2 = _run_with_shim(monkeypatch, tmp_path / "mismatch", payload_mismatch)
    msgs2 = [r.message for r in report2.results]
    assert any("total_findings" in m and "UNVERIFIED" in m for m in msgs2), (
        f"canary did not fire on a count mismatch: {msgs2!r}"
    )

    # Control: a version that OMITS the field never warns (guarded canary —
    # presence-gated, so an older schema cannot false-WARN).
    payload_no_field = (
        '{"files": [{"path": "/abs/plugin/x.sh", "findings": ['
        '{"severity": "HIGH", "rule_id": "rule_a", "description": "one"}'
        "]}]}"
    )
    report3 = _run_with_shim(monkeypatch, tmp_path / "nofield", payload_no_field)
    msgs3 = [r.message for r in report3.results]
    assert not any("total_findings" in m for m in msgs3)
    assert any("tirith rule_a" in m for m in msgs3)


def test_check_tirith_bool_total_findings_never_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Bool guard control (review round 9): a JSON bool `total_findings` (e.g.
    a schema that repurposed the key as a flag) must NEVER fire the canary —
    `isinstance(True, int)` is True in Python, so without the explicit
    `not isinstance(total_raw, bool)` gate a truthy mismatch would false-WARN."""
    payload = (
        '{"total_findings": true, "files": ['
        '{"path": "/abs/plugin/x.sh", "findings": ['
        '{"severity": "HIGH", "rule_id": "rule_a", "description": "one"}'
        "]}]}"
    )
    report = _run_with_shim(monkeypatch, tmp_path / "boolfield", payload)
    msgs = [r.message for r in report.results]
    # Counts "mismatch" (1 parsed vs bool True), but the bool guard keeps the
    # canary silent — the field is not a count.
    assert not any("UNVERIFIED" in m for m in msgs), f"canary fired on a bool total_findings: {msgs!r}"
    assert any("tirith rule_a" in m for m in msgs)


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

    assert result.returncode in (0, 1, 2, 3), f"unexpected exit {result.returncode}\nstderr: {result.stderr}"
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
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "demo", "version": "0.0.1"}\n')

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


# -----------------------------------------------------------------------------
# TRDD-21ES7XEX defect B — proof that this file's autouse fixture actually
# stops _resolve_tirith_runner from installing a real binary onto the host.
# -----------------------------------------------------------------------------


def test_no_real_install_attempted_under_the_autouse_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the module's autouse ``CPV_NO_TIRITH_INSTALL`` fixture in effect,
    forcing the "tirith not found" branch (no tirith/docker/nix on PATH) must
    never invoke brew/npm/cargo — verified by recording every subprocess.run
    call ``_resolve_tirith_runner`` makes, not by trusting its return value.
    """
    # Only the three installer probe binaries resolve; tirith/docker/nix don't.
    monkeypatch.setattr(
        validate_security.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"brew", "npm", "cargo"} else None,
    )
    install_calls: list[list[str]] = []
    real_run = validate_security.subprocess.run

    def _recording_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        install_calls.append(argv)
        kwargs.pop("timeout", None)
        return real_run(["true"], **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(validate_security.subprocess, "run", _recording_run)

    runner = validate_security._resolve_tirith_runner()

    assert runner is None, "opted out — no install fallback should ever resolve to a runner"
    assert install_calls == [], f"CPV_NO_TIRITH_INSTALL=1 must block every install attempt; got {install_calls}"


def test_control_without_the_opt_out_the_same_setup_would_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: with the SAME setup as the test above but
    ``CPV_NO_TIRITH_INSTALL`` explicitly cleared, ``_resolve_tirith_runner``
    DOES attempt an install — proving the assertion above can actually fail
    (i.e. it is not vacuously true regardless of the guard)."""
    monkeypatch.delenv("CPV_NO_TIRITH_INSTALL", raising=False)
    monkeypatch.setattr(
        validate_security.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"brew", "npm", "cargo"} else None,
    )
    install_calls: list[list[str]] = []
    real_run = validate_security.subprocess.run

    def _recording_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        install_calls.append(argv)
        kwargs.pop("timeout", None)
        return real_run(["true"], **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(validate_security.subprocess, "run", _recording_run)

    validate_security._resolve_tirith_runner()

    assert install_calls, "control setup should attempt an install when the opt-out is cleared"
    assert install_calls[0] == ["brew", "install", "sheeki03/tap/tirith"]
