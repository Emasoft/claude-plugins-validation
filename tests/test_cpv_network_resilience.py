"""Tests for cpv_network_resilience.py.

Covers transient/permanent error classification (subprocess + HTTP),
the run_with_retry loop (success-on-first, success-on-Nth, terminal failure,
permanent-no-retry), and gh_with_retry / git_with_retry environment +
config injection.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import urllib.error
from http.client import BadStatusLine, RemoteDisconnected
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_network_resilience as cnr  # noqa: E402

# ── is_transient_subprocess_error ────────────────────────────────────────────


@pytest.mark.parametrize("stderr", [
    "fatal: unable to access 'https://github.com/...': Could not resolve host: github.com",
    "fatal: unable to access 'https://github.com/...': Failed to connect to github.com port 443",
    "Connection timed out after 60 seconds",
    "Connection reset by peer",
    "RPC failed; HTTP 500 curl 22",
    "RPC failed; HTTP 502 curl 22",
    "early EOF: unexpected end of stream",
    "fatal: the remote end hung up unexpectedly",
    "Service Unavailable",
    "HTTP 503: Service Unavailable",
    "Bad Gateway",
    "Gateway Timeout",
    "Rate limit exceeded",
    "Too Many Requests",
    "the operation timed out",
    "gnutls_handshake() failed: A TLS packet with unexpected length was received.",
])
def test_transient_subprocess_signatures(stderr: str):
    assert cnr.is_transient_subprocess_error(stderr, returncode=1) is True


@pytest.mark.parametrize("stderr", [
    "! [rejected] main -> main (non-fast-forward)",
    "Permission denied (publickey)",
    "HTTP 404: Not Found",
    "HTTP 422: Validation Failed",
    "Authentication failed for 'https://github.com/...'",
    "401 Unauthorized",
    "403 Forbidden",
    "404 Not Found",
    "name already exists on this account",
    "refusing to overwrite",
])
def test_permanent_subprocess_signatures(stderr: str):
    assert cnr.is_transient_subprocess_error(stderr, returncode=1) is False


def test_permanent_wins_when_both_match():
    """If stderr contains both transient (5xx) AND permanent (404)
    signatures, permanent classification wins — never retry on auth errors
    even if a 5xx appears in the chained error report.
    """
    mixed = "HTTP 503: Service Unavailable\nHTTP 401 Unauthorized: bad token"
    assert cnr.is_transient_subprocess_error(mixed, returncode=1) is False


def test_returncode_zero_never_transient():
    """Even with a transient-looking string, exit 0 means success."""
    assert cnr.is_transient_subprocess_error("HTTP 503", returncode=0) is False


def test_empty_stderr_not_transient():
    """No stderr → can't classify → not retryable."""
    assert cnr.is_transient_subprocess_error("", returncode=1) is False


def test_unknown_stderr_not_transient():
    """Stderr we don't recognize → conservative: don't retry."""
    assert cnr.is_transient_subprocess_error("totally unknown error", returncode=1) is False


# ── is_transient_http_error ──────────────────────────────────────────────────


def test_transient_http_socket_timeout():
    assert cnr.is_transient_http_error(socket.timeout()) is True


def test_transient_http_timeout_error():
    assert cnr.is_transient_http_error(TimeoutError()) is True


def test_transient_http_remote_disconnected():
    assert cnr.is_transient_http_error(RemoteDisconnected("EOF")) is True
    assert cnr.is_transient_http_error(BadStatusLine("xxx")) is True


def test_transient_http_5xx_codes():
    for code in (500, 502, 503, 504):
        exc = urllib.error.HTTPError(
            url="x", code=code, msg="x", hdrs=None, fp=None,  # type: ignore[arg-type]
        )
        assert cnr.is_transient_http_error(exc) is True, f"code {code} should be transient"


def test_transient_http_408_429():
    for code in (408, 429):
        exc = urllib.error.HTTPError(
            url="x", code=code, msg="x", hdrs=None, fp=None,  # type: ignore[arg-type]
        )
        assert cnr.is_transient_http_error(exc) is True


def test_permanent_http_4xx_codes():
    for code in (400, 401, 403, 404, 422):
        exc = urllib.error.HTTPError(
            url="x", code=code, msg="x", hdrs=None, fp=None,  # type: ignore[arg-type]
        )
        assert cnr.is_transient_http_error(exc) is False, f"code {code} should be permanent"


def test_transient_url_error_unwraps_reason():
    inner = socket.timeout("read timed out")
    outer = urllib.error.URLError(inner)
    assert cnr.is_transient_http_error(outer) is True


def test_transient_connection_error():
    assert cnr.is_transient_http_error(ConnectionError("reset")) is True


def test_none_not_transient():
    assert cnr.is_transient_http_error(None) is False


# ── run_with_retry ───────────────────────────────────────────────────────────


def test_run_with_retry_success_on_first_attempt(tmp_path):
    """Successful command exits cleanly; no retries used."""
    result = cnr.run_with_retry(
        ["true"], cwd=tmp_path, max_attempts=3, backoff=0.0,
    )
    assert result.returncode == 0


def test_run_with_retry_permanent_failure_no_retry(tmp_path, capsys):
    """Permanent error fails immediately without retry attempts."""
    # Use `false` plus a fake permanent stderr via a wrapper script.
    # Easier: patch transient_check to return False always.
    call_count = {"n": 0}

    def fake_check(stderr: str, rc: int) -> bool:
        call_count["n"] += 1
        return False

    with pytest.raises(subprocess.CalledProcessError):
        cnr.run_with_retry(
            ["false"], cwd=tmp_path, max_attempts=10, backoff=0.0,
            transient_check=fake_check,
        )
    # transient_check is called once; no retries.
    assert call_count["n"] == 1


def test_run_with_retry_transient_then_success(tmp_path):
    """Fail twice with transient, succeed on third — retries until success."""

    def flaky_check(stderr: str, rc: int) -> bool:
        del stderr, rc
        return True  # always treat as transient

    # We need a command that fails N times then succeeds. Easiest: use a
    # state file the script checks.
    state = tmp_path / "tries.txt"
    helper = tmp_path / "flaky.sh"
    helper.write_text(
        "#!/bin/bash\n"
        f'STATE="{state}"\n'
        'COUNT=$(cat "$STATE" 2>/dev/null || echo 0)\n'
        'COUNT=$((COUNT + 1))\n'
        'echo "$COUNT" > "$STATE"\n'
        'if [ "$COUNT" -lt 3 ]; then\n'
        '  echo "Service Unavailable" >&2\n'
        '  exit 1\n'
        'fi\n'
        'echo "ok"\n',
        encoding="utf-8",
    )
    helper.chmod(0o755)

    result = cnr.run_with_retry(
        [str(helper)], cwd=tmp_path,
        max_attempts=5, backoff=0.0,
        transient_check=flaky_check,
    )
    assert result.returncode == 0
    assert "ok" in (result.stdout or "")
    assert int(state.read_text().strip()) == 3


def test_run_with_retry_exhausts_attempts(tmp_path):
    """Always-transient failure exhausts max_attempts then raises."""
    helper = tmp_path / "always_fail.sh"
    helper.write_text(
        "#!/bin/bash\necho 'Service Unavailable' >&2\nexit 1\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        cnr.run_with_retry(
            [str(helper)], cwd=tmp_path,
            max_attempts=3, backoff=0.0,
        )
    assert exc_info.value.returncode == 1


def test_run_with_retry_check_false_returns_result(tmp_path):
    """check=False → return the failed CompletedProcess instead of raising."""
    helper = tmp_path / "always_fail.sh"
    helper.write_text(
        "#!/bin/bash\necho 'Service Unavailable' >&2\nexit 7\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    result = cnr.run_with_retry(
        [str(helper)], cwd=tmp_path,
        max_attempts=2, backoff=0.0, check=False,
    )
    assert result.returncode == 7


def test_run_with_retry_on_retry_callback(tmp_path):
    """on_retry callback is invoked with (attempt_n, last_result)."""
    helper = tmp_path / "always_fail.sh"
    helper.write_text(
        "#!/bin/bash\necho 'Service Unavailable' >&2\nexit 1\n",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    invocations: list[tuple[int, int]] = []

    def callback(attempt: int, result: subprocess.CompletedProcess[str]) -> None:
        invocations.append((attempt, result.returncode))

    with pytest.raises(subprocess.CalledProcessError):
        cnr.run_with_retry(
            [str(helper)], cwd=tmp_path,
            max_attempts=4, backoff=0.0, check=True,
            on_retry=callback,
        )
    # 4 attempts total → callback fires after attempts 1, 2, 3 (not 4 — that's terminal)
    assert [att for att, _ in invocations] == [1, 2, 3]


# ── gh_with_retry / git_with_retry ───────────────────────────────────────────


def test_gh_with_retry_sets_http_timeout_env(tmp_path, monkeypatch):
    """gh_with_retry auto-injects GH_HTTP_TIMEOUT when not already set."""
    captured_env = {}

    def fake_run(cmd, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("GH_HTTP_TIMEOUT", raising=False)
    cnr.gh_with_retry(["gh", "auth", "status"], max_attempts=1)
    assert captured_env.get("GH_HTTP_TIMEOUT") == str(cnr.GH_HTTP_TIMEOUT_SEC)


def test_gh_with_retry_preserves_existing_http_timeout(tmp_path, monkeypatch):
    """If user explicitly sets GH_HTTP_TIMEOUT, don't override."""
    captured_env = {}

    def fake_run(cmd, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cnr.gh_with_retry(
        ["gh", "auth", "status"], max_attempts=1,
        env={"GH_HTTP_TIMEOUT": "999"},
    )
    assert captured_env.get("GH_HTTP_TIMEOUT") == "999"


def test_git_with_retry_injects_slow_transfer_config(tmp_path, monkeypatch):
    """git_with_retry prepends `-c http.lowSpeedLimit=…` config to the command."""
    captured_cmd: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured_cmd.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cnr.git_with_retry(["git", "push", "origin", "main"], cwd=tmp_path, max_attempts=1)
    assert captured_cmd, "subprocess.run was not called"
    cmd = captured_cmd[0]
    assert cmd[0] == "git"
    assert "-c" in cmd
    assert any("http.lowSpeedLimit" in part for part in cmd)
    assert any("http.lowSpeedTime" in part for part in cmd)
    assert "push" in cmd
    assert "origin" in cmd
    assert "main" in cmd


def test_git_with_retry_rejects_non_git_command():
    with pytest.raises(ValueError):
        cnr.git_with_retry(["gh", "release", "create"], max_attempts=1)
