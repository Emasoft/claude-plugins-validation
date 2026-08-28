#!/usr/bin/env python3
"""Issue #169 — the pre-push hook must be BRANCH-AWARE, two-sided.

The canonical pre-push hook refused EVERY push whose process ancestry did not
descend from ``scripts/publish.py``, and ``publish.py`` itself refuses to run
off the default branch — composed, a feature-branch push was impossible in any
repo carrying the pipeline, so a multi-agent fleet sharing work over feature
branches silently stalled (the refused push exits non-zero, unread).

The fix makes both hooks branch-aware:

* A push to the DEFAULT branch (main/master) or ANY tag is a RELEASE — it still
  requires publish.py (ancestry gate). UNCHANGED.
* A push to any OTHER (feature) branch is ALLOWED, but only after a passing
  secret scan of the pushed commits (trufflehog). trufflehog absent =>
  FAIL CLOSED (an unscanned push is never silently allowed).

Two hooks carry the logic and both are tested here:

* the GENERATED POSIX-sh template ``generate_plugin_repo.gen_pre_push_hook``
  (every downstream plugin CPV scaffolds), and
* CPV's OWN dogfooded ``git-hooks/pre-push`` (a Python hook).

Each behaviour is pinned from BOTH sides (a test that only asserts "the good
thing happened" is worthless without a control proving the SAME code path still
blocks/gates the bad case).

The tests are hermetic: a per-test PATH of stub binaries. A stub ``ps`` forces
CPV's process-ancestry check to resolve to "no publish.py" so the release-gate
tests are deterministic even when the suite itself runs under a real
``publish.py`` (its Gate-4 pytest step) — that real publish.py would otherwise
match the ancestry basename check.

The fake AWS credential used by the real-trufflehog end-to-end tests is
assembled from fragments at runtime so no secret-shaped literal ever appears in
this source (which would trip GitHub push-protection and CPV's own scanner).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from generate_plugin_repo import PluginParams, gen_pre_push_hook  # noqa: E402

CPV_HOOK = REPO_ROOT / "git-hooks" / "pre-push"
ZERO = "0000000000000000000000000000000000000000"
SH = "/bin/sh"

# trufflehog for the (optional) real end-to-end tests — the deterministic bulk
# of the suite stubs it and does not need it installed.
import shutil  # noqa: E402

_HAS_TRUFFLEHOG = shutil.which("trufflehog") is not None
_requires_trufflehog = pytest.mark.skipif(
    not _HAS_TRUFFLEHOG, reason="trufflehog not installed"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    """A real git repo on branch ``master`` with origin/HEAD -> origin/master."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    # Deterministic default branch name regardless of the host's init.defaultBranch.
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/master")
    (repo / "f.txt").write_text("clean\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-qm", "base")
    # A symbolic origin/HEAD -> origin/master, no real remote needed.
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master"],
        cwd=str(repo),
        capture_output=True,
    )
    # A real local `feat` ref, because every feature-push case below feeds the
    # hook a `refs/heads/feat` line and real git only produces one when the ref
    # exists locally. Without it the fixture was silently exercising the
    # unresolvable-ref shape of issue #213 — which the hook now (correctly)
    # blocks — while claiming to test the ordinary clean push.
    _git(repo, "branch", "feat")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, head


def _stub_bin(tmp_path: Path, *, trufflehog_exit: int | None) -> Path:
    """A minimal PATH dir.

    Contains real ``python3``/``git``, a benign stub ``ps`` (so CPV's ancestry
    check finds NO publish.py), and — when ``trufflehog_exit`` is not None — a
    stub ``trufflehog`` exiting with that code. Passing None omits trufflehog
    entirely to exercise the fail-closed path. No ``uv`` is provided, so the
    hooks fall back to invoking ``python3`` directly.
    """
    binp = tmp_path / "bin"
    binp.mkdir(exist_ok=True)
    # python3 -> the interpreter running the tests (guaranteed modern: the hook
    # uses PEP-604 unions that the macOS system python 3.9 cannot parse).
    (binp / "python3").symlink_to(sys.executable)
    git_real = shutil.which("git")
    assert git_real, "git must be installed to run these tests"
    (binp / "git").symlink_to(git_real)
    # A stub ps that reports a single benign ancestor (ppid 1, no publish.py).
    ps = binp / "ps"
    ps.write_text("#!/bin/sh\necho '1 test-runner'\n")
    ps.chmod(0o755)
    if trufflehog_exit is not None:
        th = binp / "trufflehog"
        th.write_text(f"#!/bin/sh\nexit {trufflehog_exit}\n")
        th.chmod(0o755)
    return binp


def _env(binp: Path) -> dict[str, str]:
    # Stub dir first; /usr/bin:/bin supplies sed/cat/etc. Homebrew (real
    # trufflehog/uv) is deliberately OFF this PATH.
    env = dict(os.environ)
    env["PATH"] = f"{binp}:/usr/bin:/bin"
    env["NO_COLOR"] = "1"
    return env


def _write_generated_hook(repo: Path, gate_exit: int = 7) -> Path:
    """Write the generated hook + a stub scripts/publish.py that records the gate
    invocation (marker file ``GATE_CALLED``) and exits ``gate_exit``."""
    params = PluginParams(
        name="demo-plugin",
        description="demo",
        author="Emasoft",
        author_email="demo@example.com",
        github_owner="Emasoft",
    )
    hook = repo / "hook.sh"
    hook.write_text(gen_pre_push_hook(params))
    hook.chmod(0o755)
    scripts = repo / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "publish.py").write_text(
        "import sys, pathlib\n"
        "pathlib.Path('GATE_CALLED').write_text(' '.join(sys.argv[1:]))\n"
        f"sys.exit({gate_exit})\n"
    )
    return hook


def _run_generated(repo: Path, binp: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    hook = repo / "hook.sh"
    return subprocess.run(
        [SH, str(hook), "origin", "file://origin"],
        cwd=str(repo),
        input=stdin,
        env=_env(binp),
        capture_output=True,
        text=True,
    )


def _run_cpv(repo: Path, binp: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    # Invoke via sys.executable so the hook always runs on a modern interpreter.
    return subprocess.run(
        [sys.executable, str(CPV_HOOK), "origin", "file://origin"],
        cwd=str(repo),
        input=stdin,
        env=_env(binp),
        capture_output=True,
        text=True,
    )


def _stdin(local_ref: str, local_sha: str, remote_sha: str = ZERO) -> str:
    # git feeds "<local ref> <local sha> <remote ref> <remote sha>".
    return f"{local_ref} {local_sha} {local_ref} {remote_sha}\n"


def _fake_aws_creds() -> str:
    """A fake AWS key pair assembled from fragments (no secret literal in source)."""
    akid = "AKIA" + "4NNXQ2ZG" + "TABCD3XY"
    secret = "Xb9v2Qk7pLmN3rTyU8wZ" + "1aScDfGhJkLpQrStUvWx"
    return f"AWS_ACCESS_KEY_ID={akid}\nAWS_SECRET_ACCESS_KEY={secret}\n"


def _add_feature_commit(repo: Path, filename: str, content: str) -> str:
    _git(repo, "checkout", "-q", "feat")
    (repo / filename).write_text(content)
    _git(repo, "add", filename)
    _git(repo, "commit", "-qm", "feature work")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# ===========================================================================
# The GENERATED POSIX-sh template (generate_plugin_repo.gen_pre_push_hook)
# ===========================================================================


class TestGeneratedHook:
    def test_feature_branch_allowed_when_scan_clean(self, tmp_path: Path) -> None:
        """Feature push + clean secret scan -> allowed (exit 0), gate NOT run."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        _write_generated_hook(repo)
        r = _run_generated(repo, binp, _stdin("refs/heads/feat", head))
        assert r.returncode == 0, r.stderr
        assert not (repo / "GATE_CALLED").exists(), "release gate must NOT run for a feature push"

    def test_feature_branch_blocked_when_remote_name_has_no_local_ref(self, tmp_path: Path) -> None:
        """Issue #213 — `git push origin HEAD:refs/heads/new-name` must BLOCK.

        Negative control for the test above: the stub trufflehog exits 0 here
        too, so nothing about the scanner's verdict differs. What differs is
        that the pushed remote branch name resolves to no local ref, which made
        the real trufflehog log 'unable to resolve ref', scan 0 bytes and exit
        0 — a push allowed on a scan that never looked.
        """
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        _write_generated_hook(repo)
        r = _run_generated(repo, binp, _stdin("refs/heads/no-such-branch", head))
        assert r.returncode != 0, "an unscannable push must fail closed, not pass"
        assert "does not resolve to a local ref" in r.stderr
        assert not (repo / "GATE_CALLED").exists(), "release gate must NOT run for a feature push"

    def test_feature_branch_blocked_when_scan_finds_secret(self, tmp_path: Path) -> None:
        """Feature push + secret found -> blocked (non-zero), gate NOT run.

        Positive control for the previous test: the SAME feature-push code path
        blocks the instant the scan reports a finding.
        """
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=183)  # trufflehog's "results found"
        _write_generated_hook(repo)
        r = _run_generated(repo, binp, _stdin("refs/heads/feat", head))
        assert r.returncode != 0
        assert not (repo / "GATE_CALLED").exists()

    def test_default_branch_master_still_gated(self, tmp_path: Path) -> None:
        """Push to master -> routed to publish.py --gate (release gate), blocked."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        _write_generated_hook(repo, gate_exit=7)
        r = _run_generated(repo, binp, _stdin("refs/heads/master", head))
        assert (repo / "GATE_CALLED").exists(), "master push MUST run the release gate"
        assert "--gate" in (repo / "GATE_CALLED").read_text()
        assert r.returncode == 7, "the gate's non-zero exit (blocked) must propagate"

    def test_default_branch_main_still_gated(self, tmp_path: Path) -> None:
        """Push to main (the other default name) -> release gate."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        _write_generated_hook(repo, gate_exit=7)
        r = _run_generated(repo, binp, _stdin("refs/heads/main", head))
        assert (repo / "GATE_CALLED").exists()
        assert r.returncode == 7

    def test_tag_push_still_gated(self, tmp_path: Path) -> None:
        """Push of a tag -> release gate (a release moves through publish.py)."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        _write_generated_hook(repo, gate_exit=7)
        r = _run_generated(repo, binp, _stdin("refs/tags/v1.2.3", head))
        assert (repo / "GATE_CALLED").exists()
        assert r.returncode == 7

    def test_feature_branch_fail_closed_without_trufflehog(self, tmp_path: Path) -> None:
        """Feature push with trufflehog absent -> FAIL CLOSED (blocked), gate NOT run."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=None)  # no trufflehog on PATH
        _write_generated_hook(repo)
        r = _run_generated(repo, binp, _stdin("refs/heads/feat", head))
        assert r.returncode != 0
        assert not (repo / "GATE_CALLED").exists()
        assert "trufflehog" in r.stderr.lower()

    def test_empty_push_is_conservatively_gated(self, tmp_path: Path) -> None:
        """Nothing parsed on stdin -> conservative fall-through to the release gate."""
        repo, _head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        _write_generated_hook(repo, gate_exit=7)
        r = _run_generated(repo, binp, "")
        assert (repo / "GATE_CALLED").exists()
        assert r.returncode == 7

    @_requires_trufflehog
    def test_feature_real_trufflehog_blocks_planted_secret(self, tmp_path: Path) -> None:
        """End-to-end with REAL trufflehog: a planted secret blocks the push."""
        repo, _head = _init_repo(tmp_path)
        feat = _add_feature_commit(repo, "creds.env", _fake_aws_creds())
        _write_generated_hook(repo)
        # Full real PATH so the real trufflehog resolves; new-branch push (ZERO).
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        r = subprocess.run(
            [SH, str(repo / "hook.sh"), "origin", "file://origin"],
            cwd=str(repo),
            input=_stdin("refs/heads/feat", feat),
            env=env,
            capture_output=True,
            text=True,
        )
        assert r.returncode != 0, "a planted secret must block the feature push"
        assert not (repo / "GATE_CALLED").exists()

    @_requires_trufflehog
    def test_feature_real_trufflehog_allows_clean(self, tmp_path: Path) -> None:
        """End-to-end with REAL trufflehog: a clean feature commit is allowed."""
        repo, _head = _init_repo(tmp_path)
        feat = _add_feature_commit(repo, "code.py", "print('nothing secret here')\n")
        _write_generated_hook(repo)
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        r = subprocess.run(
            [SH, str(repo / "hook.sh"), "origin", "file://origin"],
            cwd=str(repo),
            input=_stdin("refs/heads/feat", feat),
            env=env,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr
        assert not (repo / "GATE_CALLED").exists()


# ===========================================================================
# CPV's OWN dogfooded hook (git-hooks/pre-push)
# ===========================================================================


class TestCpvOwnHook:
    def test_feature_branch_allowed_when_scan_clean(self, tmp_path: Path) -> None:
        """Feature push + clean scan -> allowed (exit 0), NO publish.py ancestry needed."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        r = _run_cpv(repo, binp, _stdin("refs/heads/feat", head))
        assert r.returncode == 0, r.stdout + r.stderr

    def test_feature_branch_blocked_when_scan_finds_secret(self, tmp_path: Path) -> None:
        """Feature push + secret found -> blocked. Control for the clean case."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=183)
        r = _run_cpv(repo, binp, _stdin("refs/heads/feat", head))
        assert r.returncode != 0

    def test_default_branch_master_blocked_without_ancestry(self, tmp_path: Path) -> None:
        """Push to master without publish.py ancestry -> BLOCKED (release gate)."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        r = _run_cpv(repo, binp, _stdin("refs/heads/master", head))
        assert r.returncode != 0
        assert "Direct push not allowed" in r.stdout

    def test_default_branch_main_blocked_without_ancestry(self, tmp_path: Path) -> None:
        """Push to main without publish.py ancestry -> BLOCKED."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        r = _run_cpv(repo, binp, _stdin("refs/heads/main", head))
        assert r.returncode != 0
        assert "Direct push not allowed" in r.stdout

    def test_tag_push_blocked_without_ancestry(self, tmp_path: Path) -> None:
        """Tag push without publish.py ancestry -> BLOCKED (release still gated)."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=0)
        r = _run_cpv(repo, binp, _stdin("refs/tags/v9.9.9", head))
        assert r.returncode != 0
        assert "Direct push not allowed" in r.stdout

    def test_feature_branch_fail_closed_without_trufflehog(self, tmp_path: Path) -> None:
        """Feature push with trufflehog absent -> FAIL CLOSED (blocked)."""
        repo, head = _init_repo(tmp_path)
        binp = _stub_bin(tmp_path, trufflehog_exit=None)
        r = _run_cpv(repo, binp, _stdin("refs/heads/feat", head))
        assert r.returncode != 0
        assert "trufflehog is not installed" in r.stdout

    @_requires_trufflehog
    def test_feature_real_trufflehog_blocks_planted_secret(self, tmp_path: Path) -> None:
        """End-to-end with REAL trufflehog: planted secret blocks a feature push."""
        repo, _head = _init_repo(tmp_path)
        feat = _add_feature_commit(repo, "creds.env", _fake_aws_creds())
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        r = subprocess.run(
            [sys.executable, str(CPV_HOOK), "origin", "file://origin"],
            cwd=str(repo),
            input=_stdin("refs/heads/feat", feat),
            env=env,
            capture_output=True,
            text=True,
        )
        assert r.returncode != 0, r.stdout + r.stderr

    @_requires_trufflehog
    def test_feature_real_trufflehog_allows_clean(self, tmp_path: Path) -> None:
        """End-to-end with REAL trufflehog: a clean feature commit is allowed."""
        repo, _head = _init_repo(tmp_path)
        feat = _add_feature_commit(repo, "code.py", "print('nothing secret here')\n")
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        r = subprocess.run(
            [sys.executable, str(CPV_HOOK), "origin", "file://origin"],
            cwd=str(repo),
            input=_stdin("refs/heads/feat", feat),
            env=env,
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stdout + r.stderr
