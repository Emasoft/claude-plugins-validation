"""Issue #224: the release push must outlive its own pre-push gate, and a timed-out
attempt must not orphan the subtree it spawned.

Two canon defects, one symptom:

1. the push inherited ``cpv_network_resilience.DEFAULT_TIMEOUT_SEC`` (600s), but the
   branch-aware pre-push hook runs the WHOLE gate (validate + the full test suite)
   inside that wall clock — so every attempt died mid-gate and retried 60 times;
2. ``run_with_retry`` killed only the direct child on ``TimeoutExpired``, leaving the
   hook subtree running (reparented to pid 1) alongside the next attempt.

The text assertions pin the canon (both the emitted template and CPV's own
pipeline); the process-group test exercises the real behaviour with a real
grandchild.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_network_resilience import run_with_retry  # noqa: E402
from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _params(**overrides: object) -> PluginParams:
    """A PluginParams with sensible defaults, accepting field overrides."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


def _push_call(text: str) -> str:
    """The `git push --atomic` call expression from a publish.py source text."""
    anchor = text.index('"git", "push", "--atomic"')
    # Balance parens from the enclosing git_with_retry( so the slice is the WHOLE
    # call, not a window whose size would have to be guessed (and would drift).
    start = text.rindex("git_with_retry(", 0, anchor)
    depth = 0
    for i in range(text.index("(", start), len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError("unbalanced git_with_retry( call")


# ── (a) the emitted canon ────────────────────────────────────────────────────


def test_emitted_template_defines_push_timeout_constants() -> None:
    """The generated publish.py defines _PUSH_TIMEOUT_SEC and _PUSH_MAX_ATTEMPTS = 3."""
    text = gen_publish_py(_params())
    assert "_PUSH_TIMEOUT_SEC = " in text
    assert "_PUSH_MAX_ATTEMPTS = 3" in text


def test_emitted_template_push_timeout_exceeds_the_test_suite_bound() -> None:
    """The emitted push bound covers BOTH suite runs the hook makes (G4 + G4b)."""
    text = gen_publish_py(_params())
    assert "_PUSH_TIMEOUT_SEC = _CPV_TIMEOUT_SEC + 2 * _DEFAULT_TEST_SUITE_TIMEOUT + 1800.0" in text


def test_emitted_push_call_passes_both_overrides() -> None:
    """The emitted push call passes timeout=_PUSH_TIMEOUT_SEC and max_attempts=_PUSH_MAX_ATTEMPTS."""
    call = _push_call(gen_publish_py(_params()))
    assert "timeout=_PUSH_TIMEOUT_SEC" in call
    assert "max_attempts=_PUSH_MAX_ATTEMPTS" in call


def test_emitted_template_still_compiles() -> None:
    """The generated publish.py with the new constants is valid Python."""
    compile(gen_publish_py(_params()), "publish.py", "exec")


def test_issue_224_is_cited_in_the_emitted_template() -> None:
    """The emitted constants carry the #224 rationale so nobody re-tunes them blind."""
    assert "#224" in gen_publish_py(_params())


# ── (b) CPV's own pipeline ───────────────────────────────────────────────────


def test_cpv_publish_defines_push_timeout_constants() -> None:
    """CPV's own publish.py defines _PUSH_TIMEOUT_SEC and _PUSH_MAX_ATTEMPTS = 3."""
    text = (_REPO_ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert "_PUSH_TIMEOUT_SEC = _CPV_TIMEOUT_SEC + 2 * _TEST_SUITE_TIMEOUT_SEC + 1800.0" in text
    assert "_PUSH_MAX_ATTEMPTS = 3" in text


def test_cpv_publish_push_call_passes_both_overrides() -> None:
    """CPV's own push call passes the wider timeout and the 3-attempt budget."""
    call = _push_call((_REPO_ROOT / "scripts" / "publish.py").read_text(encoding="utf-8"))
    assert "timeout=_PUSH_TIMEOUT_SEC" in call
    assert "max_attempts=_PUSH_MAX_ATTEMPTS" in call


# ── (c) the real process-group behaviour ─────────────────────────────────────


def _pgid_is_alive(pgid: int) -> bool:
    """True while any process remains in the group (signal 0 probes without killing)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_timeout_kills_the_whole_process_group_not_just_the_child(tmp_path: Path) -> None:
    """A timed-out attempt kills the grandchild too, so no work is orphaned to pid 1."""
    marker = tmp_path / "pgid.txt"
    # The shell backgrounds a grandchild that outlives the direct child by far; it
    # records its own process group so the test can prove the group is gone.
    # `ps -o pgid=` (not $$) because the inner shell need not be the group leader.
    script = f"sh -c 'ps -o pgid= -p $$ | tr -d \" \" > {marker}; sleep 30 & sleep 30'"
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_retry(
            ["sh", "-c", script],
            timeout=1,
            max_attempts=1,
            capture_output=False,
        )
    pgid = int(marker.read_text().strip())
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _pgid_is_alive(pgid):
            break
        time.sleep(0.05)
    else:
        # Leave nothing behind even when the assertion is about to fail.
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        pytest.fail("the grandchild survived the timeout — the process group was not killed")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_a_non_timeout_exception_also_kills_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C must not leak the subtree: subprocess.run killed on ANY exception, Popen does not."""
    spawned: list[subprocess.Popen[str]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> "subprocess.Popen[str]":
        proc = real_popen(*args, **kwargs)  # type: ignore[arg-type,call-overload]
        spawned.append(proc)
        return proc

    def boom(self: object, timeout: float | None = None) -> tuple[str, str]:
        raise KeyboardInterrupt

    # Patch the CLASS method first — after `subprocess.Popen` is rebound to the
    # recording function there is no class left to patch.
    monkeypatch.setattr(real_popen, "communicate", boom)
    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    with pytest.raises(KeyboardInterrupt):
        run_with_retry(["sh", "-c", "sleep 30"], timeout=30, max_attempts=1, capture_output=False)
    assert spawned, "no process was spawned"
    assert spawned[0].poll() is not None, "the child survived the interrupt"


def test_a_fast_successful_command_still_returns_normally() -> None:
    """Positive control: the Popen rewrite did not break the ordinary success path."""
    result = run_with_retry(["sh", "-c", "printf ok"], timeout=30, max_attempts=1)
    assert result.returncode == 0
    assert result.stdout == "ok"
