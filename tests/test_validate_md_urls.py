#!/usr/bin/env python3
"""Regression tests for validate_md_urls() — issues #12, #13.

Covers the v2.26.1 flakiness fix for GitHub "Dead URL (unreachable)"
false-positives:

1. Transient network errors (socket.timeout, SSL, 429/503) trigger retry
2. Real dead URLs (404) are reported immediately without retry
3. Per-host semaphore caps concurrent HEADs against github.com-family
4. HEAD → GET fallback on both 405 AND transient errors
5. Exception type/reason surfaces in the WARNING message
6. CPV_SKIP_URL_CHECK=1 short-circuits the check
7. 401/403 auth-protected URLs are treated as alive
8. Permanent URLErrors (DNS failures) are not retried

These tests mock `urllib.request.urlopen` so no real network is touched.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import urllib.error
from http.client import BadStatusLine, RemoteDisconnected
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    _DEFAULT_PER_HOST_CONCURRENCY,
    _STRICT_HOST_CONCURRENCY,
    _TRANSIENT_HTTP_CODES,
    ValidationReport,
    _format_url_exception,
    _is_transient_url_error,
    validate_md_urls,
)


class _FakeResponse:
    """Stand-in for urllib's urlopen() context-manager response."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _warnings(report: ValidationReport) -> list[str]:
    """Extract WARNING messages from a ValidationReport."""
    return [r.message for r in report.results if r.level == "WARNING"]


def _make_md(tmp_path: Path, url: str = "https://github.com/Emasoft/test") -> Path:
    """Create a README.md with a single link for testing."""
    md = tmp_path / "README.md"
    md.write_text(f"See [the link]({url}) for details.\n")
    return md


# ---------------------------------------------------------------------------
# Helper predicates
# ---------------------------------------------------------------------------


class TestIsTransientUrlError:
    """Unit tests for _is_transient_url_error()."""

    def test_socket_timeout_is_transient(self):
        """`socket.timeout` — the most common GitHub flake symptom."""
        assert _is_transient_url_error(socket.timeout()) is True

    def test_timeout_error_is_transient(self):
        """Bare `TimeoutError` — modern alias for socket.timeout on Py3.10+."""
        assert _is_transient_url_error(TimeoutError()) is True

    def test_remote_disconnected_is_transient(self):
        """GitHub closes HEAD connections under parallel load → RemoteDisconnected."""
        assert _is_transient_url_error(RemoteDisconnected("")) is True

    def test_bad_status_line_is_transient(self):
        """Partial read from closed socket surfaces as BadStatusLine."""
        assert _is_transient_url_error(BadStatusLine("")) is True

    def test_connection_reset_is_transient(self):
        """TCP RST mid-handshake → ConnectionResetError."""
        assert _is_transient_url_error(ConnectionResetError()) is True

    def test_ssl_error_is_transient(self):
        """SSL handshake timeouts at the edge are retry-worthy."""
        import ssl

        assert _is_transient_url_error(ssl.SSLError("handshake timed out")) is True

    def test_transient_http_codes(self):
        """429/503/504/etc. mean 'try again later'."""
        for code in _TRANSIENT_HTTP_CODES:
            exc = urllib.error.HTTPError("u", code, "", None, None)  # pyright: ignore[reportArgumentType, reportCallIssue]
            assert _is_transient_url_error(exc) is True, f"{code} should be transient"

    def test_permanent_http_codes(self):
        """404/400/410 will not clear up — don't retry."""
        for code in (400, 404, 410, 418):
            exc = urllib.error.HTTPError("u", code, "", None, None)  # pyright: ignore[reportArgumentType, reportCallIssue]
            assert _is_transient_url_error(exc) is False, f"{code} should be permanent"

    def test_urlerror_wrapping_transient(self):
        """URLError(reason=socket.timeout) must be unwrapped and classified."""
        exc = urllib.error.URLError(socket.timeout())
        assert _is_transient_url_error(exc) is True

    def test_urlerror_wrapping_dns_failure_not_transient(self):
        """DNS NXDOMAIN is not transient — the name doesn't exist."""
        exc = urllib.error.URLError("[Errno -2] Name or service not known")
        assert _is_transient_url_error(exc) is False

    def test_none_not_transient(self):
        """Defensive guard — None/no-exception means success, not flake."""
        assert _is_transient_url_error(None) is False


class TestFormatUrlException:
    """Unit tests for _format_url_exception() — WARNING message rendering."""

    def test_none_renders_as_unreachable(self):
        assert _format_url_exception(None) == "unreachable"

    def test_socket_timeout_surfaces_type(self):
        msg = _format_url_exception(socket.timeout("connect timed out"))
        assert "timeout" in msg.lower()

    def test_urlerror_surfaces_wrapped_type(self):
        msg = _format_url_exception(urllib.error.URLError(socket.timeout()))
        assert "URLError" in msg
        assert "timeout" in msg.lower()

    def test_urlerror_with_string_reason_preserves_string(self):
        msg = _format_url_exception(urllib.error.URLError("DNS failure"))
        assert "DNS failure" in msg

    def test_long_messages_truncated_to_type_only(self):
        """Very long exception strings fall back to bare type name."""
        long_msg = "X" * 500
        exc = ConnectionResetError(long_msg)
        out = _format_url_exception(exc)
        assert "ConnectionResetError" in out
        assert len(out) < 100  # keep WARNINGs readable


# ---------------------------------------------------------------------------
# validate_md_urls — main flakiness fix (issues #12, #13)
# ---------------------------------------------------------------------------


class TestValidateMdUrlsRetry:
    """Retry-on-transient is the core fix: flaky github.com HEADs recover."""

    def test_transient_timeout_recovers_on_retry(self, tmp_path: Path):
        """socket.timeout on attempts 1-2, then 200 — must NOT warn."""
        md = _make_md(tmp_path)
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            if counter["n"] <= 2:
                raise socket.timeout("simulated github flake")
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01, max_retries=2)
        assert counter["n"] == 3
        assert _warnings(report) == []

    def test_remote_disconnected_recovers_on_retry(self, tmp_path: Path):
        """GitHub's signature failure mode — RemoteDisconnected then 200."""
        md = _make_md(tmp_path)
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            if counter["n"] == 1:
                raise RemoteDisconnected("Remote end closed connection")
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert counter["n"] == 2
        assert _warnings(report) == []

    def test_429_recovers_on_retry(self, tmp_path: Path):
        """Rate-limited first attempt, 200 on retry."""
        md = _make_md(tmp_path)
        state = {"n": 0}

        def fake_urlopen(req, **kw):
            state["n"] += 1
            if state["n"] == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", None, None)  # pyright: ignore[reportArgumentType, reportCallIssue]
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert state["n"] == 2
        assert _warnings(report) == []

    def test_503_recovers_on_retry(self, tmp_path: Path):
        """Service Unavailable → retry."""
        md = _make_md(tmp_path)
        state = {"n": 0}

        def fake_urlopen(req, **kw):
            state["n"] += 1
            if state["n"] == 1:
                raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", None, None)  # pyright: ignore[reportArgumentType, reportCallIssue]
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert state["n"] == 2
        assert _warnings(report) == []

    def test_persistent_timeout_respects_max_retries(self, tmp_path: Path):
        """max_retries=1 → exactly 2 attempts on a non-bonus host.

        Uses example.org (no per-host retry bonus) so the test exercises
        the bare `max_retries` cap, not the github.com `_HOST_TRANSIENT_RETRY_BONUS`
        path. WARNING surfaces the exception type.
        """
        md = _make_md(tmp_path, url="https://docs.astral.sh/uv/")
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            raise socket.timeout("persistent")

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01, max_retries=1)
        assert counter["n"] == 2  # 1 initial + 1 retry
        warns = _warnings(report)
        assert any("timeout" in w.lower() for w in warns)

    def test_github_com_gets_extra_retry_bonus(self, tmp_path: Path):
        """github.com URLs receive +2 bonus retries vs the bare max_retries
        param — mirrors the github-timeouts rule's "be patient with GitHub
        transient failures" semantics. Default max_retries=2 + 2 bonus + 1
        initial = 5 attempts before giving up.

        v2.98.0 (test-speed): mock ``time.sleep`` to skip the linear
        backoff sleeps — they consume ~15s wall-clock per run without
        adding any test value (the assertion is on attempt COUNT, not
        on real backoff timing). Per-host retry-backoff lookup inside
        validate_md_urls ignores the test's ``retry_backoff`` arg for
        github.com (uses ``_HOST_RETRY_BACKOFF["github.com"]`` >> 0.01).
        """
        md = _make_md(tmp_path)  # default URL is on github.com
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            raise socket.timeout("persistent")

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("time.sleep", return_value=None):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01, max_retries=2)
        # 1 initial + 2 bare retries + 2 github bonus retries = 5
        assert counter["n"] == 5, f"expected 5 attempts on github.com, got {counter['n']}"


class TestValidateMdUrlsPermanentFailures:
    """Non-transient failures must NOT retry (wasted wall-clock on --strict)."""

    def test_http_404_no_retry(self, tmp_path: Path):
        md = _make_md(tmp_path)
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)  # pyright: ignore[reportArgumentType, reportCallIssue]

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01, max_retries=3)
        assert counter["n"] == 1, "404 must be reported on first attempt"
        assert any("HTTP 404" in w for w in _warnings(report))

    def test_dns_urlerror_no_retry(self, tmp_path: Path):
        """Non-transient URLError (DNS failure) reports immediately."""
        md = _make_md(tmp_path)
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            raise urllib.error.URLError("[Errno -2] Name or service not known")

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01, max_retries=3)
        assert counter["n"] == 1
        assert any("URLError" in w for w in _warnings(report))


class TestValidateMdUrlsHeadGetFallback:
    """HEAD → GET fallback covers CDNs and the github HEAD-drop case."""

    def test_http_405_falls_back_to_get(self, tmp_path: Path):
        md = _make_md(tmp_path)
        methods = []

        def fake_urlopen(req, **kw):
            methods.append(req.get_method())
            if req.get_method() == "HEAD":
                raise urllib.error.HTTPError(req.full_url, 405, "Method Not Allowed", None, None)  # pyright: ignore[reportArgumentType, reportCallIssue]
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert methods == ["HEAD", "GET"]
        assert _warnings(report) == []

    def test_transient_head_retries_with_get(self, tmp_path: Path):
        """GitHub-specific: HEAD times out, GET retry succeeds."""
        md = _make_md(tmp_path)
        methods = []

        def fake_urlopen(req, **kw):
            methods.append(req.get_method())
            if req.get_method() == "HEAD":
                raise socket.timeout("HEAD dropped")
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01, max_retries=2)
        assert methods[0] == "HEAD"
        assert "GET" in methods[1:], "Retry must switch to GET"
        assert _warnings(report) == []


class TestValidateMdUrlsAuthProtected:
    """401/403 mean the URL exists but anonymous access is denied — treat as alive."""

    def test_http_401_is_alive(self, tmp_path: Path):
        md = _make_md(tmp_path)

        def fake_urlopen(req, **kw):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, None)  # pyright: ignore[reportArgumentType, reportCallIssue]

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert _warnings(report) == []

    def test_http_403_is_alive(self, tmp_path: Path):
        md = _make_md(tmp_path)

        def fake_urlopen(req, **kw):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", None, None)  # pyright: ignore[reportArgumentType, reportCallIssue]

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert _warnings(report) == []


class TestValidateMdUrlsEnvOptOut:
    """CPV_SKIP_URL_CHECK=1 escape hatch for air-gapped CI and publish pipelines."""

    @pytest.fixture(autouse=True)
    def _clear_env(self):
        saved = os.environ.pop("CPV_SKIP_URL_CHECK", None)
        yield
        if saved is not None:
            os.environ["CPV_SKIP_URL_CHECK"] = saved
        else:
            os.environ.pop("CPV_SKIP_URL_CHECK", None)

    def test_env_1_short_circuits(self, tmp_path: Path):
        md = _make_md(tmp_path)
        os.environ["CPV_SKIP_URL_CHECK"] = "1"
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            raise socket.timeout()

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert counter["n"] == 0, "No network calls when skipped"
        assert not report.results

    def test_env_true_short_circuits(self, tmp_path: Path):
        md = _make_md(tmp_path)
        os.environ["CPV_SKIP_URL_CHECK"] = "true"
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert counter["n"] == 0

    def test_env_0_does_not_skip(self, tmp_path: Path):
        """CPV_SKIP_URL_CHECK=0 means 'do NOT skip' — the normal check runs."""
        md = _make_md(tmp_path)
        os.environ["CPV_SKIP_URL_CHECK"] = "0"
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert counter["n"] == 1


class TestValidateMdUrlsPerHostConcurrency:
    """github.com-family hosts get a cap of 2 parallel HEADs (issues #12, #13)."""

    def test_strict_host_caps_configured(self):
        """github.com and siblings must be capped at ≤ 2."""
        for host in ("github.com", "www.github.com", "api.github.com", "raw.githubusercontent.com"):
            assert _STRICT_HOST_CONCURRENCY.get(host) == 2, f"{host} not capped at 2"

    def test_default_cap_conservative(self):
        """Default per-host cap must be low enough to not hammer random upstreams."""
        assert _DEFAULT_PER_HOST_CONCURRENCY <= 8
        assert _DEFAULT_PER_HOST_CONCURRENCY >= 1

    def test_github_parallelism_never_exceeds_cap(self, tmp_path: Path):
        """Under heavy fan-out, the per-host semaphore must hold the cap."""
        # Many distinct github.com URLs to force contention.
        urls = [f"https://github.com/Emasoft/pkg{i}" for i in range(20)]
        md = tmp_path / "README.md"
        md.write_text("\n".join(f"* [pkg{i}]({u})" for i, u in enumerate(urls)) + "\n")

        in_flight = {"now": 0, "max": 0}
        lock = threading.Lock()
        barrier = threading.Event()

        def fake_urlopen(req, **kw):
            with lock:
                in_flight["now"] += 1
                if in_flight["now"] > in_flight["max"]:
                    in_flight["max"] = in_flight["now"]
            # Simulate slow upstream so parallel window is observable.
            barrier.wait(timeout=0.2)
            with lock:
                in_flight["now"] -= 1
            return _FakeResponse(200)

        def releaser():
            # Give the pool time to ramp up, then release.
            import time as _t

            _t.sleep(0.05)
            barrier.set()

        t = threading.Thread(target=releaser, daemon=True)
        t.start()

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)

        t.join(timeout=1.0)
        assert in_flight["max"] <= _STRICT_HOST_CONCURRENCY["github.com"], (
            f"Observed {in_flight['max']} parallel HEADs to github.com; cap is "
            f"{_STRICT_HOST_CONCURRENCY['github.com']} (issues #12, #13)"
        )


class TestValidateMdUrlsCacheAndSkips:
    """Existing cache/skip behavior must not regress."""

    def test_url_cache_hit_skips_network(self, tmp_path: Path):
        md = _make_md(tmp_path, url="https://github.com/Emasoft/test-pkg")
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            return _FakeResponse(200)

        # Pre-populated cache marks URL alive — no network call should happen.
        # (_sanitize_url is applied before cache lookup, so we cache the sanitized form.)
        from cpv_validation_common import _sanitize_url

        safe = _sanitize_url("https://github.com/Emasoft/test-pkg")
        assert safe is not None
        cache: dict[str, bool] = {safe: True}

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache=cache, retry_backoff=0.01)
        assert counter["n"] == 0
        assert _warnings(report) == []

    def test_url_cache_negative_hit_warns_without_network(self, tmp_path: Path):
        md = _make_md(tmp_path, url="https://github.com/Emasoft/dead-link")
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            return _FakeResponse(200)

        from cpv_validation_common import _sanitize_url

        safe = _sanitize_url("https://github.com/Emasoft/dead-link")
        assert safe is not None
        cache: dict[str, bool] = {safe: False}

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache=cache, retry_backoff=0.01)
        assert counter["n"] == 0
        assert any("Dead URL" in w for w in _warnings(report))

    def test_example_com_skipped(self, tmp_path: Path):
        md = _make_md(tmp_path, url="https://example.com/foo")
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert counter["n"] == 0

    def test_fenced_code_block_urls_ignored(self, tmp_path: Path):
        """URLs inside ``` fences are code examples — don't validate them."""
        md = tmp_path / "README.md"
        md.write_text(
            "```\n"
            "curl https://github.com/Emasoft/in-code-block\n"
            "```\n"
            "Real [link](https://github.com/Emasoft/real-link).\n"
        )
        seen_urls = []

        def fake_urlopen(req, **kw):
            seen_urls.append(req.full_url)
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert all("in-code-block" not in u for u in seen_urls)
        assert any("real-link" in u for u in seen_urls)

    def test_bare_host_url_is_skipped(self, tmp_path: Path):
        """Bare-host URLs like https://github.com/ (path is "/") are
        parser artefacts or homepage references — meaningless to probe.
        Skipping prevents false-positive WARNINGs from rate-limited HEADs."""
        md = tmp_path / "README.md"
        md.write_text("Visit https://github.com/ to browse,\nor check https://gitlab.com for the GitLab equivalent.\n")
        counter = {"n": 0}

        def fake_urlopen(req, **kw):
            counter["n"] += 1
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        # Both URLs have empty/`/` path → skipped → no network call.
        assert counter["n"] == 0
        assert _warnings(report) == []

    def test_backtick_wrapped_url_extracts_cleanly(self, tmp_path: Path):
        """An inline-code URL must capture WITHOUT trailing backtick + punctuation.

        Before the backtick stop-char fix the extractor produced
        "https://github.com/`," → stripped to bare "https://github.com/"
        → false-positive WARNING. Now it stops at the backtick and the
        full path-segment is preserved.

        Uses /Emasoft/cpv-real to bypass the generic-placeholder skip
        (which catches /owner/, /user/, etc.).
        """
        md = tmp_path / "README.md"
        md.write_text("Do not include `https://github.com/Emasoft/cpv-real`, treat it as text.\n")
        seen_urls: list[str] = []

        def fake_urlopen(req, **kw):
            seen_urls.append(req.full_url)
            return _FakeResponse(200)

        report = ValidationReport()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            validate_md_urls(md, tmp_path, report, timeout=1.0, url_cache={}, retry_backoff=0.01)
        assert seen_urls == ["https://github.com/Emasoft/cpv-real"], f"unexpected URL captures: {seen_urls}"
