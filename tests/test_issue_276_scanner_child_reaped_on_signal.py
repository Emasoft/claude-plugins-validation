"""Issue #276 (open part): a SIGTERM or SIGINT sent to the CPV process must not orphan the scanner child.

Real processes, no mocks. A python process starts the scanner child through the module's own
process-group runner (argv ends in ``sleep 271``), the test signals that python process and
checks that the ``sleep`` pid is gone. SIGKILL cannot be caught and is out of scope.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")

# Runs in the main thread (handlers are installed from the main thread only). The shell records
# its pid in the marker file, then exec's `sleep 271`, so the recorded pid is the sleep process.
_DRIVER = """
import sys
sys.path.insert(0, {scripts!r})
import cpv_skill_scanner as css
css._run_in_own_process_group(["sh", "-c", "echo $$ > {marker}; exec sleep 271"], 600)
"""

# Same, but the main thread holds the registry lock when the signal lands: the handler runs in
# that same thread and must re-enter the lock (a plain Lock would self-deadlock).
_DRIVER_LOCK_HELD = """
import subprocess, sys, time
sys.path.insert(0, {scripts!r})
import cpv_skill_scanner as css
css._install_signal_reapers()
proc = subprocess.Popen(["sh", "-c", "echo $$ > {marker}; exec sleep 271"], start_new_session=True)
css._live_scanners.add(proc)
with css._live_scanners_lock:
    time.sleep(120)
"""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _run_signal_case(tmp_path: Path, sig: int, driver_template: str = _DRIVER) -> int:
    """Start the driver, deliver ``sig`` to it, assert the sleep child is gone; return the driver's returncode."""
    marker = tmp_path / "sleep.pid"
    code = driver_template.format(scripts=str(REPO_ROOT / "scripts"), marker=str(marker))
    driver = subprocess.Popen([sys.executable, "-c", code], cwd=REPO_ROOT)
    sleep_pid = 0
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if marker.is_file() and marker.read_text(encoding="utf-8").strip().isdigit():
                sleep_pid = int(marker.read_text(encoding="utf-8").strip())
                break
            time.sleep(0.05)
        assert sleep_pid, "scanner child never started"
        assert _pid_alive(sleep_pid)
        time.sleep(0.3)  # let the driver reach communicate() and finish installing handlers
        driver.send_signal(sig)
        try:
            returncode = driver.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail(f"driver did not exit after signal {sig}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _pid_alive(sleep_pid):
            time.sleep(0.05)
        assert not _pid_alive(sleep_pid), f"scanner child {sleep_pid} orphaned after signal {sig}"
        return returncode
    finally:
        if driver.poll() is None:
            driver.kill()
            driver.wait()
        if sleep_pid and _pid_alive(sleep_pid):
            os.kill(sleep_pid, signal.SIGKILL)


@posix_only
def test_sigterm_reaps_scanner_child_and_dies_by_signal(tmp_path: Path) -> None:
    """SIGTERM kills the scanner child and the process still dies by SIGTERM."""
    returncode = _run_signal_case(tmp_path, signal.SIGTERM)
    assert returncode == -signal.SIGTERM


@posix_only
def test_sigint_reaps_scanner_child(tmp_path: Path) -> None:
    """SIGINT kills the scanner child (the KeyboardInterrupt still propagates)."""
    returncode = _run_signal_case(tmp_path, signal.SIGINT)
    assert returncode != 0


@posix_only
def test_sigterm_with_registry_lock_held_does_not_deadlock(tmp_path: Path) -> None:
    """SIGTERM landing while the main thread holds the scanner registry lock still reaps and exits."""
    returncode = _run_signal_case(tmp_path, signal.SIGTERM, _DRIVER_LOCK_HELD)
    assert returncode == -signal.SIGTERM
