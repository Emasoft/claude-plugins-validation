"""The Linux fork-parity probe (TRDD-4KQXN8ZW).

The probe re-runs a command with the interpreter's default multiprocessing
start method forced to ``fork``, so a developer on macOS (default ``spawn``)
exercises the path Linux CI will actually take.

Verified two-sided against the real v3.23.0 defect before this was written:
buggy code under ``spawn`` passed 16 tests (blind); the SAME code under ``fork``
failed with ``TimeoutExpired`` — the exact signature CI produced.

The tests below are deliberately about the probe's CONTRACT, especially the
three ways it must refuse to lie: a hang is a finding, an unrunnable platform is
a WARNING and never a block, and "cannot check" is never reported as "clean".
"""

from __future__ import annotations

import multiprocessing as mp
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))

from cpv_fork_parity import (  # noqa: E402
    ForkParityResult,
    fork_parity_supported,
    run_under_linux_fork_default,
    write_sitecustomize,
)

# ---------------------------------------------------------------------------
# The sitecustomize actually changes the start method — including in children
# ---------------------------------------------------------------------------


def _fork_available() -> bool:
    return "fork" in mp.get_all_start_methods()


requires_fork = pytest.mark.skipif(not _fork_available(), reason="fork unavailable on this platform")


@requires_fork
def test_probe_forces_fork_in_the_target_process(tmp_path: Path) -> None:
    probe = tmp_path / "show.py"
    probe.write_text("import multiprocessing as m; print(m.get_start_method())", encoding="utf-8")
    res = run_under_linux_fork_default([sys.executable, str(probe)], cwd=tmp_path, timeout=120)
    if res.status != "ran":
        pytest.skip(f"probe not runnable here: {res.detail}")
    assert res.returncode == 0
    assert "fork" in res.output


@requires_fork
def test_forcing_reaches_a_SUBPROCESS(tmp_path: Path) -> None:
    """LOAD-BEARING: the v3.23.0 deadlock happened in a subprocess the suite
    spawned, not in pytest's own process. If the forcing stopped at the first
    process the probe would have been blind to the very defect it exists for."""
    probe = tmp_path / "nested.py"
    probe.write_text(
        textwrap.dedent(
            """
            import subprocess, sys
            out = subprocess.run(
                [sys.executable, "-c", "import multiprocessing as m; print(m.get_start_method())"],
                capture_output=True, text=True,
            ).stdout
            print("child:", out.strip())
            """
        ),
        encoding="utf-8",
    )
    res = run_under_linux_fork_default([sys.executable, str(probe)], cwd=tmp_path, timeout=120)
    if res.status != "ran":
        pytest.skip(f"probe not runnable here: {res.detail}")
    assert "child: fork" in res.output


def test_sitecustomize_chains_to_a_project_one(tmp_path: Path) -> None:
    """Forcing the start method must not silently disable a sitecustomize the
    project itself relies on — shadowing one would be a side effect nobody
    asked for."""
    ours = tmp_path / "ours"
    write_sitecustomize(ours)
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    (theirs / "sitecustomize.py").write_text("import os; os.environ['PROJECT_SITECUSTOMIZE'] = 'ran'", encoding="utf-8")

    script = tmp_path / "check.py"
    script.write_text("import os; print('chained:', os.environ.get('PROJECT_SITECUSTOMIZE'))", encoding="utf-8")

    import os as _os

    env = dict(_os.environ)
    env["PYTHONPATH"] = f"{ours}{_os.pathsep}{theirs}"
    out = subprocess.run(  # noqa: S603
        [sys.executable, str(script)], capture_output=True, text=True, env=env, cwd=str(tmp_path), check=False
    )
    assert "chained: ran" in out.stdout


# ---------------------------------------------------------------------------
# The refusal contract
# ---------------------------------------------------------------------------


def test_a_hang_is_reported_as_a_FINDING_not_as_unrunnable(tmp_path: Path) -> None:
    """A hang IS the signature of this defect class. Reporting a timeout as
    'could not check' would turn the one true positive into a shrug."""
    hang = tmp_path / "hang.py"
    hang.write_text("import time; time.sleep(120)", encoding="utf-8")
    res = run_under_linux_fork_default([sys.executable, str(hang)], cwd=tmp_path, timeout=3)
    if res.status == "already-native":
        pytest.skip("platform default is already fork")
    if res.status == "unsupported":
        pytest.skip(f"probe unsupported here: {res.detail}")
    assert res.status == "ran"
    assert res.returncode == 124
    assert res.blocked is True
    assert "deadlock" in res.detail


@requires_fork
def test_a_real_failure_blocks(tmp_path: Path) -> None:
    fail = tmp_path / "fail.py"
    fail.write_text("import sys; sys.exit(3)", encoding="utf-8")
    res = run_under_linux_fork_default([sys.executable, str(fail)], cwd=tmp_path, timeout=60)
    if res.status != "ran":
        pytest.skip(f"probe not runnable here: {res.detail}")
    assert res.returncode == 3
    assert res.blocked is True


@requires_fork
def test_a_passing_command_does_not_block(tmp_path: Path) -> None:
    ok = tmp_path / "ok.py"
    ok.write_text("print('fine')", encoding="utf-8")
    res = run_under_linux_fork_default([sys.executable, str(ok)], cwd=tmp_path, timeout=60)
    if res.status != "ran":
        pytest.skip(f"probe not runnable here: {res.detail}")
    assert res.returncode == 0
    assert res.blocked is False


@pytest.mark.parametrize("status", ["already-native", "unsupported"])
def test_an_unrunnable_probe_never_blocks(status: str) -> None:
    """CANNOT-CHECK IS NOT CLEAN — but it is also not a block. It degrades to a
    WARNING, matching the G2b..G2f degrade-if-absent idiom, so a machine without
    fork can still publish."""
    assert ForkParityResult(status=status, returncode=1).blocked is False


def test_a_ran_probe_with_nonzero_rc_blocks() -> None:
    assert ForkParityResult(status="ran", returncode=1).blocked is True


def test_on_linux_the_probe_reports_already_native(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux the ordinary test run already exercises fork, so the probe must
    skip rather than double every CI run."""
    monkeypatch.setattr(mp, "get_all_start_methods", lambda: ["fork", "spawn", "forkserver"])
    monkeypatch.setattr(mp, "get_start_method", lambda allow_none=False: "fork")
    runnable, reason = fork_parity_supported()
    assert runnable is False
    assert "already fork" in reason


def test_on_a_fork_less_platform_the_probe_is_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mp, "get_all_start_methods", lambda: ["spawn"])
    monkeypatch.setattr(mp, "get_start_method", lambda allow_none=False: "spawn")
    runnable, reason = fork_parity_supported()
    assert runnable is False
    assert "unavailable" in reason


def test_on_macos_the_probe_is_runnable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The case that matters: a dev box whose default is spawn while CI forks."""
    monkeypatch.setattr(mp, "get_all_start_methods", lambda: ["spawn", "fork", "forkserver"])
    monkeypatch.setattr(mp, "get_start_method", lambda allow_none=False: "spawn")
    runnable, _ = fork_parity_supported()
    assert runnable is True
