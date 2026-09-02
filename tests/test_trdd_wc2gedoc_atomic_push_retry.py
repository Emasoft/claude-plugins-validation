"""TRDD-WC2GEDOC — the atomic release push must be able to RETRY.

The defect chain: publish.py's `git push --atomic` passed
`capture_output=False`, so `result.stderr` was None, and the transient
classifier's `if not stderr: return False` treated EVERY failure as permanent
— the retry wrapper never retried the release push. Hub-verified in 12 of 22
fleet publish.py copies; this is the template-level fix.

Three layers, each two-sided:
  1. The MECHANISM: with stderr captured the classifier retries a transient
     failure; with stderr None (the old shape) it cannot.
  2. CPV's own publish.py call site captures and echoes stderr.
  3. The emitted canon (gen_publish_py) carries the same fix and still
     compiles with every referenced name importable.
"""

from __future__ import annotations

import ast
import importlib
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

nr = importlib.import_module("scripts.cpv_network_resilience")
gpr = importlib.import_module("scripts.generate_plugin_repo")

PUBLISH_SRC = (REPO_ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")


def _atomic_push_block(text: str) -> str:
    """The ~1500 chars following the atomic-push invocation line."""
    idx = text.find('"git", "push", "--atomic"')
    assert idx != -1, "atomic push call not found"
    return text[idx - 800 : idx + 1500]


# ── 1. Mechanism ───────────────────────────────────────────────────────────


class _FakePopen:
    """Minimal stand-in for the Popen run_with_retry drives (issue #224)."""

    def __init__(self, cmd, returncode=0, stdout="", stderr=""):
        self.args = cmd
        self.pid = -1
        self.returncode = returncode
        self._out = (stdout, stderr)

    def communicate(self, timeout=None):
        return self._out

    def poll(self):
        return self.returncode


class TestClassifierNeedsStderr:
    """Why capture_output=False could never retry: the classifier is stderr-keyed."""

    def test_transient_stderr_is_retryable(self):
        assert nr.is_transient_subprocess_error("fatal: unable to access: 503 Service Unavailable", 1)

    def test_none_stderr_shape_is_never_retryable(self):
        """The old capture_output=False shape: stderr None -> '' -> permanent."""
        assert not nr.is_transient_subprocess_error("", 1)

    def test_real_git_dns_failure_stderr_is_transient(self):
        """REAL producer shape (captured from an actual `git push` to an
        unresolvable host on 2026-08-18, exit 128) — not synthesized from the
        classifier's own pattern list."""
        real = (
            "fatal: unable to access 'https://nonexistent-host-cpv-test.invalid/o/r.git/': "
            "Could not resolve host: nonexistent-host-cpv-test.invalid\n"
        )
        assert nr.is_transient_subprocess_error(real, 128)

    def test_real_git_non_fast_forward_stderr_is_permanent(self):
        """REAL producer shape (captured from an actual rejected push after an
        amend, exit 1) — permanent, never retried."""
        real = (
            "To /tmp/bare\n"
            " ! [rejected]        HEAD -> master (non-fast-forward)\n"
            "error: failed to push some refs to '/tmp/bare'\n"
        )
        assert not nr.is_transient_subprocess_error(real, 1)

    def test_run_with_retry_retries_when_stderr_is_captured(self):
        """End-to-end through run_with_retry: transient stderr on attempt 1,
        success on attempt 2 — the retry MUST happen."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return _FakePopen(cmd, 1, "", "error: RPC failed; HTTP 502")
            return _FakePopen(cmd, 0, "", "ok")

        # issue #224: run_with_retry owns the pid via Popen so it can kill the
        # whole process group on timeout — patch Popen, not run.
        with mock.patch.object(nr.subprocess, "Popen", fake_run), mock.patch.object(nr.time, "sleep"):
            result = nr.run_with_retry(["git", "push"], max_attempts=3, backoff=0)
        assert result.returncode == 0
        assert len(calls) == 2

    def test_run_with_retry_does_not_retry_a_permanent_failure(self):
        """Two-sided control: auth failure is permanent — exactly one attempt."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _FakePopen(cmd, 1, "", "fatal: Authentication failed")

        with mock.patch.object(nr.subprocess, "Popen", fake_run), mock.patch.object(nr.time, "sleep"):
            try:
                nr.run_with_retry(["git", "push"], max_attempts=3, backoff=0)
                raise AssertionError("expected CalledProcessError")
            except subprocess.CalledProcessError as exc:
                assert "Authentication failed" in (exc.stderr or "")
        assert len(calls) == 1


# ── 2. CPV's own publish.py call site ──────────────────────────────────────


class TestOwnPublishAtomicPushCaptures:
    def test_no_capture_output_false_on_the_atomic_push_call(self):
        """The CALL (not prose) must not disable capture. Parse the AST and
        find the git_with_retry call whose argv starts the atomic push."""
        tree = ast.parse(PUBLISH_SRC)
        found = False
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "git_with_retry"):
                continue
            rendered = ast.dump(node)
            if "'--atomic'" not in rendered:
                continue
            found = True
            for kw in node.keywords:
                if kw.arg == "capture_output":
                    assert not (
                        isinstance(kw.value, ast.Constant) and kw.value.value is False
                    ), "atomic push passes capture_output=False — the retry classifier is blind again"
        assert found, "no git_with_retry --atomic call found in publish.py"

    def test_stderr_is_echoed_on_success_and_failure(self):
        block = _atomic_push_block(PUBLISH_SRC)
        assert "push_result.stderr" in block
        assert "exc.stderr" in block


# ── 3. The emitted canon ───────────────────────────────────────────────────


class TestEmittedCanonAtomicPushCaptures:
    def _body(self) -> str:
        from test_canon_143_genrepo import _params

        return gpr.gen_publish_py(_params())

    def test_emitted_call_does_not_disable_capture(self):
        body = self._body()
        block = _atomic_push_block(body)
        # Assert on the CALL region after the try:, not the explanatory comment.
        call_region = block[block.index("try:") :]
        assert "capture_output=False" not in call_region

    def test_emitted_body_compiles_and_resolves_names(self):
        body = self._body()
        ast.parse(body)
        assert re.search(r"^import subprocess$", body, re.M)
        assert re.search(r"^import sys$", body, re.M)
        assert "_push_exc.stderr" in body and "_push_res.stderr" in body
