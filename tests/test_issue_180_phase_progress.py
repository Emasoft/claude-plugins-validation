"""Per-phase progress markers — the second half of #180.

The reporter asked for `tee` **or** per-phase progress on stderr. v3.22.0
shipped the `tee`, which preserves whatever was emitted — but the validator
emitted almost nothing before its final report, so there was nothing to
preserve and a killed job still could not be attributed to a phase. I said
so publicly rather than closing the issue as done; this is that half.

Every phase announces START before running and DONE with its elapsed time
after, so a killed run ends with STARTs that never got a DONE — and those are
exactly the phases that were in flight. Verified by observation, not by
inspecting the diff: `test_killed_run_identifies_the_stuck_phase` actually
kills a run and reconstructs the answer from the log.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cpv_validation_common import (  # noqa: E402
    emit_phase_done,
    emit_phase_start,
    phase_progress_enabled,
)

_REPO = Path(__file__).resolve().parents[1]


def _plugin(tmp_path: Path) -> Path:
    root = tmp_path / "probe"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"probe","description":"progress marker probe plugin",'
        '"version":"0.1.0","author":{"name":"t"}}',
        encoding="utf-8",
    )
    return root


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {**os.environ, "PLUGIN_SKIP_GITHUB_INTEGRITY": "1", "CPV_SCAN_CACHE": "0"}
    env.update(extra or {})
    return env


# remote_validation.py is the ONLY supported entry point — invoking
# validate_plugin.py directly trips the CPV integrity gate.
_ENTRY = ["scripts/remote_validation.py", "plugin"]


def _run(root: Path, *extra: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(_REPO / _ENTRY[0]), _ENTRY[1], str(root), *extra],
        capture_output=True,
        text=True,
        env=_env(env_extra),
        cwd=str(_REPO),
        timeout=300,
        check=False,
    )


@pytest.fixture(scope="module")
def default_run(tmp_path_factory: pytest.TempPathFactory) -> subprocess.CompletedProcess[str]:
    """One shared plain run. Each of these spawns a full validation, so they
    are shared rather than repeated — a slow suite is its own kind of bug."""
    return _run(_plugin(tmp_path_factory.mktemp("default")))


# ---------------------------------------------------------------------------
# The toggle
# ---------------------------------------------------------------------------


def test_progress_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOAD-BEARING: the value of this is being there for the hang you did NOT
    predict. An opt-in flag is useless in exactly that case."""
    monkeypatch.delenv("PLUGIN_PROGRESS", raising=False)
    assert phase_progress_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " 0 "])
def test_progress_can_be_silenced(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PLUGIN_PROGRESS", value)
    assert phase_progress_enabled() is False


@pytest.mark.parametrize("value", ["1", "yes", "", "anything"])
def test_non_falsy_values_keep_progress_on(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PLUGIN_PROGRESS", value)
    assert phase_progress_enabled() is True


# ---------------------------------------------------------------------------
# The markers go to stderr, never stdout
# ---------------------------------------------------------------------------


def test_markers_are_written_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    emit_phase_start("demo_phase")
    emit_phase_done("demo_phase", 1.25)
    captured = capsys.readouterr()
    assert captured.out == "", "a progress marker reached stdout"
    assert "START demo_phase" in captured.err
    assert "DONE  demo_phase 1.2s" in captured.err


def test_a_broken_stderr_pipe_cannot_fail_a_run() -> None:
    """SECURITY-ADJACENT ROBUSTNESS: `… 2>&1 | head` closes the pipe once head
    has its lines. Emitting ~90 markers instead of almost none made that
    likely, and an unguarded write raises BrokenPipeError — a validation that
    fails because nobody was reading its log. Verified to raise before the
    guard was added."""
    import os as _os

    read_fd, write_fd = _os.pipe()
    _os.close(read_fd)  # reader gone: every write to write_fd now fails
    saved = _os.dup(2)
    try:
        _os.dup2(write_fd, 2)
        for i in range(200):  # enough to overflow any buffer and force a write
            emit_phase_start(f"phase{i}")
            emit_phase_done(f"phase{i}", 0.1)
    finally:
        _os.dup2(saved, 2)
        _os.close(saved)
        _os.close(write_fd)


def test_a_closed_stderr_cannot_fail_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other refusal mode: an embedding process already closed stderr."""
    import io

    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)
    emit_phase_start("x")
    emit_phase_done("x", 1.0)


def test_markers_are_ascii_only() -> None:
    """These land in Windows consoles and CI logs of every encoding, and CPV
    itself validates cross-platform compliance."""
    emit_phase_start("x")  # smoke: must not raise
    for text in ("[cpv-phase] START x", "[cpv-phase] DONE  x 0.0s"):
        text.encode("ascii")  # raises on any non-ASCII


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_every_phase_reports_start_and_done(default_run: subprocess.CompletedProcess[str]) -> None:
    starts = re.findall(r"\[cpv-phase\] START (\S+)", default_run.stderr)
    dones = re.findall(r"\[cpv-phase\] DONE  (\S+)", default_run.stderr)
    assert starts, "no phase markers emitted at all"
    assert sorted(starts) == sorted(dones), "a completed run left an unmatched phase"


def test_the_reported_hang_phase_is_instrumented(default_run: subprocess.CompletedProcess[str]) -> None:
    """#180's hang was the dead-link sweep, which lives in this phase. If it
    is not named, the reporter's own case is still unattributable."""
    assert "START validate_md_content_references" in default_run.stderr


def test_json_stdout_stays_pure(tmp_path: Path) -> None:
    """The --json contract is stdout = the JSON object ONLY, so a consumer can
    json.loads() the whole buffer. Progress must never break that."""
    import json

    proc = _run(_plugin(tmp_path), "--json")
    assert "cpv-phase" not in proc.stdout
    json.loads(proc.stdout)  # raises if anything else leaked onto stdout
    assert "cpv-phase" in proc.stderr, "positive control: markers should still be on stderr"


def test_silenced_run_emits_no_markers(tmp_path: Path) -> None:
    proc = _run(_plugin(tmp_path), env_extra={"PLUGIN_PROGRESS": "0"})
    assert "cpv-phase" not in proc.stderr
    assert "cpv-phase" not in proc.stdout


def test_killed_run_identifies_the_stuck_phase() -> None:
    """THE POINT OF THE FEATURE, verified by observing it rather than by
    reading the diff — the discipline #180 itself taught me twice.

    A run killed partway must leave the in-flight phase identifiable as a
    START with no matching DONE.
    """
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, str(_REPO / _ENTRY[0]), _ENTRY[1], str(_REPO)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(),
        cwd=str(_REPO),
        # Own process group so the KILL below reaches the whole tree. The
        # validator fans out to multiprocessing children; killing only the
        # parent leaves them holding the stderr pipe open, and the follow-up
        # communicate() then blocks forever waiting for an EOF that never
        # comes. (Cost a hung run while writing this test.)
        start_new_session=True,
    )
    try:
        stderr = proc.communicate(timeout=8)[1]
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # pragma: no cover - race
            proc.kill()
        stderr = proc.communicate(timeout=60)[1] or ""

    starts = re.findall(r"\[cpv-phase\] START (\S+)", stderr)
    dones = set(re.findall(r"\[cpv-phase\] DONE  (\S+)", stderr))
    if not starts:
        pytest.skip("machine finished or produced no markers before the kill")
    in_flight = [s for s in starts if s not in dones]
    assert in_flight, "a killed run left NO phase in flight — the stuck phase would be unidentifiable"
