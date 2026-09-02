#!/usr/bin/env python3
"""Network-resilience helpers for CPV.

Provides retry-wrapped subprocess.run for git/gh CLI operations, plus
HTTP-error classification for urllib calls. Lives in its own module so
publish.py + cpv_strip_dev.py + standalone scripts can depend on it
without dragging in cpv_validation_common's full surface.

Pattern reference: ~/.claude/rules/github-timeouts.md.

Public API:
    is_transient_subprocess_error(stderr: str, returncode: int) -> bool
    is_transient_http_error(exc: BaseException | None) -> bool
    run_with_retry(cmd, *, ...) -> subprocess.CompletedProcess[str]
    gh_with_retry(cmd, ...)  — gh CLI defaults + GH_HTTP_TIMEOUT env
    git_with_retry(cmd, ...) — git CLI defaults + slow-transfer config

Defaults match the rule's documented retry budgets:
    gh: 30 attempts, 6s sleep, 300s per-attempt HTTP timeout
    git: 60 attempts, 4s sleep, 100 B/s slow-transfer floor over 300s

Why these numbers: the SLEEP-only sums (gh 30x6=180s, git 60x4=240s) are
NOT the worst case a caller can actually see -- each attempt also pays up
to DEFAULT_TIMEOUT_SEC=600s before it can even fail and trigger the sleep.
worst_case_seconds(max_attempts, timeout, sleep) = max_attempts * (timeout
+ sleep) - sleep (no sleep follows the final, non-retried attempt). With
the defaults (both use DEFAULT_TIMEOUT_SEC=600 as the per-attempt subprocess
timeout) that is gh: worst_case_seconds(30, 600, 6) = 18174s (~5.0h), git:
worst_case_seconds(60, 600, 4) = 36236s (~10.1h). There is still no aggregate
wall-clock deadline -- a caller that must bound total retry time should pass
a smaller max_attempts/timeout or enforce its own budget.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
from collections.abc import Callable
from http.client import BadStatusLine, RemoteDisconnected
from typing import Any

# ── Default budgets per ~/.claude/rules/github-timeouts.md ────────────────────

GH_MAX_ATTEMPTS: int = 30
GH_BACKOFF_SEC: float = 6.0
GH_HTTP_TIMEOUT_SEC: int = 300

GIT_MAX_ATTEMPTS: int = 60
GIT_BACKOFF_SEC: float = 4.0
GIT_LOW_SPEED_LIMIT: int = 100  # bytes/sec floor below which transfer is "stalled"
GIT_LOW_SPEED_TIME: int = 300  # seconds to tolerate stalled before aborting

DEFAULT_TIMEOUT_SEC: float = 600.0  # per-attempt subprocess timeout


def worst_case_seconds(max_attempts: int, timeout: float, sleep: float) -> float:
    """Aggregate worst-case wall-clock time for a `run_with_retry`-shaped loop.

    Every attempt can burn up to `timeout` seconds before it even fails and
    triggers `sleep`; only max_attempts - 1 sleeps happen (none follows the
    final, non-retried attempt). This is what the module docstring's ceiling
    figures are computed from -- kept as a callable so those numbers cannot
    silently drift out of sync with the constants (test_trdd_ezhm759t_resilience.py
    pins the docstring text against this function's output).
    """
    return max_attempts * (timeout + sleep) - sleep

# ── Transient-error classification (subprocess stderr) ───────────────────────

# These signatures indicate a network-layer transient that may clear up.
_TRANSIENT_SUBPROCESS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"could not resolve host", re.IGNORECASE),
    re.compile(r"failed to connect to .* port", re.IGNORECASE),
    re.compile(r"connection (?:timed out|reset|refused by peer)", re.IGNORECASE),
    re.compile(r"rpc failed.*\bhttp\s*5\d\d\b", re.IGNORECASE),
    re.compile(r"unexpected end of (?:stream|remote)", re.IGNORECASE),
    re.compile(r"the remote end hung up unexpectedly", re.IGNORECASE),
    re.compile(r"recv failure: connection reset", re.IGNORECASE),
    re.compile(r"server is currently unreachable", re.IGNORECASE),
    re.compile(r"\bhttp\s*5\d\d\b", re.IGNORECASE),
    re.compile(r"\bservice unavailable\b", re.IGNORECASE),
    re.compile(r"\bbad gateway\b", re.IGNORECASE),
    re.compile(r"\bgateway timeout\b", re.IGNORECASE),
    re.compile(r"\brate limit exceeded\b", re.IGNORECASE),
    re.compile(r"\btoo many requests\b", re.IGNORECASE),
    re.compile(r"the operation timed out", re.IGNORECASE),
    re.compile(r"gnutls_handshake\(\) failed", re.IGNORECASE),
    re.compile(r"openssl ssl_read.* error", re.IGNORECASE),
    re.compile(r"network is unreachable", re.IGNORECASE),
    re.compile(r"transient .* failure", re.IGNORECASE),
    # Go net package errors (gh CLI is Go-built; transient on flaky links).
    # Examples seen in the wild:
    #   `dial tcp 140.82.121.6:443: i/o timeout`
    #   `read tcp 192.168.1.5:55432->140.82.121.6:443: i/o timeout`
    #   `Get "https://api.github.com/...": context deadline exceeded`
    re.compile(r"\bi/o timeout\b", re.IGNORECASE),
    re.compile(r"\bcontext deadline exceeded\b", re.IGNORECASE),
    re.compile(r"\bdial tcp\b.*\btimeout\b", re.IGNORECASE),
    re.compile(r"\bno such host\b", re.IGNORECASE),  # transient DNS hiccup
]

# These signatures indicate a permanent failure — NEVER retry. Permanent
# wins over transient if both match (e.g. "401 Unauthorized: rate limit"
# with a 401 in there → permanent because the auth half is the real issue).
_PERMANENT_SUBPROCESS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"non-fast-forward", re.IGNORECASE),
    re.compile(r"permission denied \(publickey\)", re.IGNORECASE),
    re.compile(r"\bhttp\s*40[0134]\b", re.IGNORECASE),  # 400/401/403/404
    re.compile(r"\bhttp\s*422\b", re.IGNORECASE),
    re.compile(r"authentication failed", re.IGNORECASE),
    re.compile(r"\b401\s+unauthorized\b", re.IGNORECASE),
    re.compile(r"\b403\s+forbidden\b", re.IGNORECASE),
    re.compile(r"\b404\s+not\s+found\b", re.IGNORECASE),
    re.compile(r"name already exists on this account", re.IGNORECASE),
    re.compile(r"refusing to (?:overwrite|update)", re.IGNORECASE),
    re.compile(r"unable to access .* the requested url returned error: 4\d\d", re.IGNORECASE),
]


# git/gh exit codes seen on a network failure when stderr was never captured
# (run_with_retry(capture_output=False)). These are AMBIGUOUS -- git returns
# 128 for most fatal errors, transient or not, so a 128 with no stderr is not
# proof of a transient glitch. The caller (run_with_retry) caps retries at 1
# for this fallback path rather than the full attempt budget, since we are
# guessing rather than reading the actual error text.
_AMBIGUOUS_NO_STDERR_TRANSIENT_CODES: frozenset[int] = frozenset({128})


def is_transient_subprocess_error(stderr: str | None, returncode: int = 1) -> bool:
    """True iff the subprocess failure looks like a transient network glitch.

    Permanent matches always win — we never retry on auth failures or
    non-fast-forward errors, even if the stderr also contains a 5xx
    mention (which sometimes happens in chained error reports).

    `stderr=None` (as opposed to `""`) means stderr was never captured at all
    (run_with_retry(capture_output=False)) -- with no text to search, this
    falls back to a small allowlist of exit codes known to plausibly mean a
    transient git/gh network failure, rather than unconditionally reporting
    "permanent" and silently disabling retries.
    """
    if returncode == 0:
        return False
    if stderr is None:
        return returncode in _AMBIGUOUS_NO_STDERR_TRANSIENT_CODES
    if not stderr:
        return False
    for pat in _PERMANENT_SUBPROCESS_PATTERNS:
        if pat.search(stderr):
            return False
    for pat in _TRANSIENT_SUBPROCESS_PATTERNS:
        if pat.search(stderr):
            return True
    return False


# ── Transient-error classification (HTTP / urllib) ───────────────────────────

# 408 Request Timeout, 429 Too Many Requests, 5xx server errors.
_TRANSIENT_HTTP_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


def is_transient_http_error(exc: BaseException | None) -> bool:
    """True iff `exc` is a network error that may clear up on retry.

    Mirrors cpv_validation_common._is_transient_url_error but is dependency-free
    so this module can be imported anywhere without dragging the validation
    common surface.
    """
    if exc is None:
        return False
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, (RemoteDisconnected, BadStatusLine)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_HTTP_CODES
    if isinstance(exc, urllib.error.URLError):
        return is_transient_http_error(getattr(exc, "reason", None))
    if isinstance(exc, ConnectionError):
        return True
    return False


# ── Subprocess retry wrapper ─────────────────────────────────────────────────

# Seconds to wait for a timed-out process group to die on SIGTERM before SIGKILL.
_KILL_GRACE_SEC: float = 3.0


def _new_process_group_kwargs() -> dict[str, Any]:
    """Popen kwargs that put the child in its OWN process group.

    Issue #224: a retried command is often a wrapper (`git push` → the pre-push
    hook → publish.py --gate → pytest). `subprocess.run(timeout=...)` kills only
    the DIRECT child, so every timed-out attempt orphaned that whole subtree to
    pid 1 while the retry started a second copy alongside it. Owning the group is
    the only way to kill what the child spawned.
    """
    if os.name == "nt":
        # Attribute exists only on Windows, hence the getattr (mypy checks POSIX).
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _kill_process_group(proc: "subprocess.Popen[Any]") -> None:
    """SIGTERM the child's whole group, brief grace, then SIGKILL. Never raises.

    A gone process (already reaped, or never grouped) is not an error here — the
    caller is on the timeout path and must proceed to the retry either way.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # No process groups to signal portably; terminate the child and let the
        # console-group break (sent first) ask its children to stop.
        with contextlib.suppress(OSError, ValueError):
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        with contextlib.suppress(OSError):
            proc.kill()
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(OSError):
            proc.kill()
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=_KILL_GRACE_SEC)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, signal.SIGKILL)


def run_with_retry(
    cmd: list[str],
    *,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
    max_attempts: int = GH_MAX_ATTEMPTS,
    backoff: float = GH_BACKOFF_SEC,
    transient_check: Callable[[str | None, int], bool] = is_transient_subprocess_error,
    on_retry: Callable[[int, "subprocess.CompletedProcess[str]"], None] | None = None,
    print_cmd: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command with bounded retries on transient failures.

    Returns the final CompletedProcess (success or terminal failure). When
    `check=True` (default), raises CalledProcessError on terminal failure.

    `transient_check(stderr, returncode) -> bool` decides whether to retry.
    Defaults to `is_transient_subprocess_error`.

    `on_retry(attempt, last_result)` is called before each sleep; default
    prints a one-line "[retry N/M] transient: <last stderr line>" to stderr.

    `print_cmd=True` prints the command before the FIRST attempt (handy
    when wrapping a previously-print-and-run helper).
    """
    # Fail fast on a nonsensical budget rather than silently returning None.
    # A bare `assert` at the tail would vanish under `python -O` and let the
    # function return None (violating the declared CompletedProcess return),
    # so validate explicitly — matching git_with_retry's ValueError idiom.
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    if timeout is None:
        timeout = DEFAULT_TIMEOUT_SEC
    if print_cmd:
        print(f"  $ {' '.join(cmd)}")

    last_result: subprocess.CompletedProcess[str] | None = None
    # With capture_output=False, result.stderr is always None -- the transient
    # decision then falls back to a guess from the exit code alone (see
    # _AMBIGUOUS_NO_STDERR_TRANSIENT_CODES). A guess should not burn the full
    # attempt budget on a failure we cannot actually confirm is transient, so
    # this path is capped at ONE retry regardless of max_attempts.
    ambiguous_no_stderr_retry_used = False
    # A per-attempt timeout is the canonical symptom of a stalled network
    # transfer (hung git push / stuck gh API call) — exactly what this module
    # exists to survive. subprocess.run raises TimeoutExpired instead of
    # returning a non-zero CompletedProcess, so without this handling a single
    # stall would escape uncaught and burn ZERO of the retry budget. Treat it
    # as a transient failure: retry up to max_attempts, then re-raise the
    # timeout (fail-fast — never swallow it into a fake "success").
    for attempt in range(1, max_attempts + 1):
        # Popen (not subprocess.run) because we must OWN the pid to kill the whole
        # process group on timeout — see _new_process_group_kwargs (issue #224).
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=text,
            **_new_process_group_kwargs(),
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            result: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
                cmd, proc.returncode, stdout, stderr
            )
        except subprocess.TimeoutExpired:
            # Kill the SUBTREE, not just the direct child, before retrying —
            # otherwise each attempt leaves its work running and the next attempt
            # competes with it.
            _kill_process_group(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=_KILL_GRACE_SEC)
            if attempt < max_attempts:
                if on_retry is not None:
                    # No CompletedProcess exists on timeout; synthesize a
                    # placeholder (returncode 124 = the conventional SIGTERM-
                    # by-timeout code) so the callback signature still holds.
                    on_retry(
                        attempt,
                        subprocess.CompletedProcess(cmd, 124, stdout=None, stderr=None),
                    )
                else:
                    print(
                        f"  [retry {attempt}/{max_attempts}] transient: timed out after {timeout:g}s",
                        file=sys.stderr,
                    )
                time.sleep(backoff)
                continue
            # Budget exhausted on a timeout — propagate the real cause so the
            # caller sees TimeoutExpired, not a swallowed/fake result.
            raise
        except BaseException:
            # subprocess.run kills its child on ANY exception and re-raises; Popen
            # does not. Without this a KeyboardInterrupt would leak the whole gate
            # subtree — the exact orphaning #224 is about, and Ctrl-C is how the
            # reporter actually stopped their run.
            _kill_process_group(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.communicate(timeout=_KILL_GRACE_SEC)
            raise
        if result.returncode == 0:
            return result
        last_result = result
        # Preserve the None/"" distinction for the classifier (a captured-but-
        # empty stderr and an uncaptured stderr are different evidence); only
        # coerce to "" for the on_retry summary line below.
        raw_stderr = result.stderr
        if not transient_check(raw_stderr, result.returncode):
            break  # permanent failure — don't waste retries
        if raw_stderr is None:
            if ambiguous_no_stderr_retry_used:
                break  # already spent our one guess-based retry — stop guessing
            ambiguous_no_stderr_retry_used = True
        stderr = raw_stderr or ""
        if attempt < max_attempts:
            if on_retry is not None:
                on_retry(attempt, result)
            else:
                last_line = ""
                for line in reversed(stderr.strip().splitlines()):
                    line = line.strip()
                    if line:
                        last_line = line
                        break
                if not last_line:
                    last_line = "(no stderr; treating as transient)"
                print(
                    f"  [retry {attempt}/{max_attempts}] transient: {last_line[:160]}",
                    file=sys.stderr,
                )
            time.sleep(backoff)

    # If the loop ended on a non-zero CompletedProcess, handle it here.
    if last_result is not None:
        if check and last_result.returncode != 0:
            raise subprocess.CalledProcessError(
                last_result.returncode,
                cmd,
                output=last_result.stdout,
                stderr=last_result.stderr,
            )
        return last_result
    # Unreachable: with max_attempts >= 1 the loop either returns a result,
    # breaks with last_result set, or re-raises TimeoutExpired on the final
    # attempt. Raise explicitly (not a bare `assert`, which `-O` strips) so any
    # future refactor that breaks this invariant fails loudly instead of
    # returning None against the declared CompletedProcess return type.
    raise AssertionError("run_with_retry produced no result despite max_attempts >= 1")


# ── gh / git convenience wrappers ────────────────────────────────────────────


def gh_with_retry(
    cmd: list[str],
    *,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture_output: bool = True,
    timeout: float | None = None,
    max_attempts: int = GH_MAX_ATTEMPTS,
    backoff: float = GH_BACKOFF_SEC,
    print_cmd: bool = False,
) -> subprocess.CompletedProcess[str]:
    """gh CLI invocation with retry. Auto-sets GH_HTTP_TIMEOUT for slow-link
    tolerance; preserves the rest of the caller's environment.
    """
    merged_env = dict(env) if env is not None else dict(os.environ)
    merged_env.setdefault("GH_HTTP_TIMEOUT", str(GH_HTTP_TIMEOUT_SEC))
    return run_with_retry(
        cmd,
        cwd=cwd,
        env=merged_env,
        check=check,
        capture_output=capture_output,
        timeout=timeout,
        max_attempts=max_attempts,
        backoff=backoff,
        print_cmd=print_cmd,
    )


def git_with_retry(
    cmd: list[str],
    *,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture_output: bool = True,
    timeout: float | None = None,
    max_attempts: int = GIT_MAX_ATTEMPTS,
    backoff: float = GIT_BACKOFF_SEC,
    print_cmd: bool = False,
) -> subprocess.CompletedProcess[str]:
    """git invocation with retry + slow-transfer config injected.

    Auto-prepends `-c http.lowSpeedLimit=100 -c http.lowSpeedTime=300` to
    tolerate slow uploads on flaky links per the rules doc.
    """
    if not cmd or cmd[0] != "git":
        raise ValueError("git_with_retry requires cmd[0] == 'git'")
    augmented = [
        cmd[0],
        "-c",
        f"http.lowSpeedLimit={GIT_LOW_SPEED_LIMIT}",
        "-c",
        f"http.lowSpeedTime={GIT_LOW_SPEED_TIME}",
        *cmd[1:],
    ]
    return run_with_retry(
        augmented,
        cwd=cwd,
        env=env,
        check=check,
        capture_output=capture_output,
        timeout=timeout,
        max_attempts=max_attempts,
        backoff=backoff,
        print_cmd=print_cmd,
    )
