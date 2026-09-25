#!/usr/bin/env python3
"""Regression test for GitHub issue #230.

``cpv_pre_install_scan._run_validate_plugin`` used to spawn a bare
``uv run python validate_plugin.py --strict --json`` subprocess. ``uv run``
builds its venv from whatever ``pyproject.toml`` it discovers by walking UP
from the CURRENT WORKING DIRECTORY — and a pre-install scan is routinely
invoked from an arbitrary cwd with no CPV pyproject in reach (that is the
whole point of a sandboxed pre-install scan). Such a venv has no ``pyyaml``,
so ``validate_plugin.py`` (which imports it) died with
``ModuleNotFoundError: yaml`` before emitting any JSON, and the pre-install
scan's CLI silently returned exit 2 (no verdict) instead of a real scan.

The fix routes the sub-invocation through ``remote_validation.py`` via
``uv run --with pyyaml``, which is self-contained regardless of cwd.

This test drives the REAL ``cpv-pre-install-scan`` CLI as a subprocess from a
cwd with no CPV pyproject.toml in any ancestor, and proves the OLD invocation
shape really would have failed from that cwd before asserting the NEW one
succeeds — a precondition, not an assumption, per the non-vacuity rule.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CLI = _REPO_ROOT / "scripts" / "cpv_pre_install_scan.py"


def _no_pyproject_in_ancestors(start: Path) -> bool:
    """True iff no ``pyproject.toml`` exists in ``start`` or any parent."""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return False
    return True


@pytest.fixture
def minimal_plugin_fixture(tmp_path: Path) -> Path:
    """A minimal, structurally-valid plugin directory (not CPV's own tree)."""
    plugin_dir = tmp_path / "fixture-plugin"
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": "issue-230-fixture", "version": "0.1.0", "description": "minimal test plugin"}),
        encoding="utf-8",
    )
    return plugin_dir


@pytest.fixture
def blocking_plugin_fixture(tmp_path: Path) -> Path:
    """A plugin whose manifest is missing the required ``name`` field.

    ``validate_plugin --strict`` reports this as a CRITICAL finding — used to
    prove a genuinely-BLOCKED verdict (not just "clean") survives the
    ``remote_validation.py`` sub-invocation hop end to end.
    """
    plugin_dir = tmp_path / "blocking-fixture-plugin"
    manifest_dir = plugin_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"version": "0.1.0", "description": "manifest missing the required name field"}),
        encoding="utf-8",
    )
    return plugin_dir


def test_no_pyproject_toml_precondition(tmp_path: Path) -> None:
    """Precondition: tmp_path's ancestor chain carries no CPV pyproject.toml.

    Without this, the "cwd with no pyproject" premise the whole test relies
    on would be unverified — pytest's own tmp_path is always outside the
    repo, but this pins that fact rather than assuming it.
    """
    assert _no_pyproject_in_ancestors(tmp_path), (
        f"expected no pyproject.toml above {tmp_path}, but one was found — "
        "the test's cwd precondition does not hold on this machine"
    )


def test_old_invocation_shape_fails_from_this_cwd(tmp_path: Path) -> None:
    """Non-vacuity: the OLD bare `uv run python -c "import yaml"` shape fails.

    Reproduces the root cause directly: from a cwd with no reachable
    pyproject.toml, `uv run` builds an environment with no pyyaml, so
    importing it dies. If this unexpectedly SUCCEEDS on this machine (e.g. a
    global uv config or cache makes pyyaml available anyway), the rest of
    this module would pass vacuously — so it is skipped with a clear reason
    instead of silently proving nothing.
    """
    result = subprocess.run(
        ["uv", "run", "python", "-c", "import yaml"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode == 0:
        pytest.skip(
            "bare `uv run python -c \"import yaml\"` unexpectedly succeeded from a "
            "pyproject-less cwd on this machine (yaml is available some other way) — "
            "the #230 root cause cannot be reproduced here, so the non-vacuity "
            "precondition for this test module does not hold"
        )
    assert result.returncode != 0
    assert "yaml" in (result.stdout + result.stderr).lower()


def test_pre_install_scan_reaches_a_real_verdict_from_pyproject_less_cwd(
    minimal_plugin_fixture: Path, tmp_path: Path
) -> None:
    """The real CLI, run from a pyproject-less cwd, reaches a verdict (not exit 2)."""
    assert _no_pyproject_in_ancestors(tmp_path)

    run_cwd = tmp_path / "run-here"
    run_cwd.mkdir()

    result = subprocess.run(
        [sys.executable, str(_CLI), str(minimal_plugin_fixture), "--json"],
        cwd=run_cwd,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    combined = result.stdout + result.stderr
    assert "ModuleNotFoundError" not in combined, (
        f"the #230 regression reproduced — exit={result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    # exit 2 is documented as "fetch/usage error, no verdict reached" — the
    # exact failure #230 produced. A real scan of a valid plugin must not
    # collapse to it.
    assert result.returncode != 2, (
        f"expected a real verdict, got exit 2 (no-verdict) — STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    payload = json.loads(result.stdout)
    assert "result" in payload
    assert "kind" in payload
    # A weak "exit != 2" alone cannot distinguish "the plugin was genuinely
    # scanned" from "remote_validation.py failed a different way before ever
    # reaching validate_plugin.py" (e.g. its own argparse/import error, which
    # also returns a non-2, non-JSON-producing exit). Pin the actual scan
    # shape: a real verdict carries a "summary" counts dict under "result",
    # and this specific fixture plugin has no CRITICAL findings.
    scan_result = payload["result"]
    assert "summary" in scan_result, f"no 'summary' in scan result — got: {scan_result}"
    counts = scan_result["summary"]
    assert "critical" in counts
    assert counts["critical"] == 0


def test_pre_install_scan_blocked_verdict_survives_the_remote_validation_hop(
    blocking_plugin_fixture: Path, tmp_path: Path
) -> None:
    """A plugin with a real CRITICAL finding still reports it, from a
    pyproject-less cwd, after the #230 fix routed the sub-invocation through
    ``remote_validation.py``.

    #230 only fixed the "reach a verdict at all" case (a clean plugin used to
    silently collapse to exit 2). It is a DIFFERENT, unverified claim that a
    genuinely non-zero verdict (real findings) still parses through the same
    hop correctly — a bug that swallowed findings while still exiting non-zero
    would pass a bare `exit != 2` check. This pins the actual counts.
    """
    assert _no_pyproject_in_ancestors(tmp_path)

    run_cwd = tmp_path / "run-here-blocked"
    run_cwd.mkdir()

    result = subprocess.run(
        [sys.executable, str(_CLI), str(blocking_plugin_fixture), "--json"],
        cwd=run_cwd,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert "ModuleNotFoundError" not in (result.stdout + result.stderr)
    assert result.returncode not in (0, 2), (
        f"expected a BLOCKED (non-zero, non-usage-error) verdict — "
        f"exit={result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    payload = json.loads(result.stdout)
    scan_result = payload["result"]
    counts = scan_result["summary"]
    assert counts["critical"] > 0, f"expected a CRITICAL finding on a name-less manifest — got: {counts}"

    findings = scan_result.get("findings") or []
    assert any(f.get("severity") == "critical" for f in findings), (
        f"expected at least one critical finding recorded — got: {findings}"
    )
