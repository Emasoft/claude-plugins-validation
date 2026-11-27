"""Issue #276: the Cisco scanner's process tree must not outlive a timeout or the caller.

Real processes, no mocks. A grandchild stands in for the Python scanner that uvx spawns.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_skill_scanner as css  # noqa: E402

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="process-group kill is POSIX-specific here")


def _grandchild_command(marker: Path) -> list[str]:
    """Command that starts a grandchild (which records its pid in ``marker``) then sleeps."""
    grandchild_code = f"import time,os; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(60)"
    child_code = (
        f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild_code!r}]); time.sleep(60)"
    )
    return [sys.executable, "-c", child_code]


def _wait_for_pid(marker: Path, seconds: float = 5.0) -> int:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            if text.isdigit():
                return int(text)
        time.sleep(0.05)
    raise AssertionError(f"grandchild never wrote its pid to {marker}")


def _is_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _force_kill(pid: int) -> None:
    """Test hygiene: never leave the sleeping grandchild behind if an assertion failed."""
    try:
        os.kill(pid, 9)
    except OSError:
        pass


@posix_only
def test_timeout_kills_grandchild(tmp_path: Path) -> None:
    """A timed-out scan leaves no grandchild process running."""
    marker = tmp_path / "grandchild.pid"
    with pytest.raises(subprocess.TimeoutExpired):
        css._run_in_own_process_group(_grandchild_command(marker), timeout=3)
    pid = _wait_for_pid(marker)
    try:
        assert _is_gone(pid), f"grandchild {pid} survived the timeout kill"
    finally:
        _force_kill(pid)
    assert not css._live_scanners


def test_registry_empty_after_normal_exit() -> None:
    """A scan that finishes normally is returned intact and deregistered."""
    completed = css._run_in_own_process_group([sys.executable, "-c", "print('ok')"], timeout=30)
    assert completed.returncode == 0
    assert completed.stdout == "ok\n"
    assert not css._live_scanners


@posix_only
def test_kill_live_scanner_groups_reaps_running_group(tmp_path: Path) -> None:
    """The exit-time reaper kills a scan that is still running."""
    marker = tmp_path / "grandchild.pid"
    outcome: dict[str, object] = {}

    def scan() -> None:
        try:
            outcome["completed"] = css._run_in_own_process_group(_grandchild_command(marker), timeout=30)
        except BaseException as exc:  # noqa: BLE001 — surfaced through the assertion below
            outcome["error"] = exc

    thread = threading.Thread(target=scan, daemon=True)
    thread.start()
    pid = _wait_for_pid(marker)
    try:
        css.kill_live_scanner_groups()
        assert _is_gone(pid), f"grandchild {pid} survived kill_live_scanner_groups"
        thread.join(timeout=10)
        assert not thread.is_alive(), "scan thread did not finish after its group was killed"
    finally:
        _force_kill(pid)
    assert "error" not in outcome, outcome.get("error")
    assert not css._live_scanners


@posix_only
def test_watchdog_reap_children_kills_scanner_group(tmp_path: Path) -> None:
    """The watchdog's reaper kills a live scanner group before os._exit."""
    import cpv_watchdog  # noqa: PLC0415

    marker = tmp_path / "grandchild.pid"
    thread = threading.Thread(
        target=lambda: css._run_in_own_process_group(_grandchild_command(marker), timeout=30), daemon=True
    )
    thread.start()
    pid = _wait_for_pid(marker)
    try:
        cpv_watchdog._reap_children()
        assert _is_gone(pid), f"grandchild {pid} survived the watchdog reaper"
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        _force_kill(pid)
