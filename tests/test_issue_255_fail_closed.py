"""Issue #255: the CPV self-integrity check fails CLOSED in plugin mode.

Pins, two-sided where a precision change is involved:
- no `main`-branch fallback: a version's manifest comes only from its own tag;
- the manifest cache is keyed by the exact tag (an old `github-manifest-v<x>`
  file, which could hold a branch manifest, is never read);
- an unreadable or non-release version, an unobtainable or malformed manifest,
  an enumeration failure, or an unreadable file REFUSES (exit 2) and names
  both remedies (cache the tag once with network; the dev skip);
- PYTEST_CURRENT_TEST is no longer a skip; the sanctioned skip variable still
  skips, with exactly one WARNING and one audit line; the release-candidate
  anchor and wheel mode still ignore it.

Every test runs the real check: both skip variable names are deleted (the
suite sets PLUGIN_SKIP_GITHUB_INTEGRITY in conftest.py), HOME, the cache and the
audit log are sandboxed, and the network is a stub (nothing reaches GitHub).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError

import _plugin_verify_hashes as pvh
import pytest

# Read at import time, i.e. after conftest.py's module level ran and before any
# fixture deletes it: guards the conftest block against being re-indented under
# its neighbouring `if` (it once was).
_CONFTEST_SKIP_VALUE = os.environ.get("PLUGIN_SKIP_GITHUB_INTEGRITY")

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "_plugin_verify_hashes.py"
VERSION = "1.2.3"
TAG_URL = pvh.REPO_RAW_TAG_URL.format(version=VERSION)
DEV_HINT = "set PLUGIN_SKIP_GITHUB_INTEGRITY=1 for the run"
USER_HINT = "run once with network access"
SKIP_VARS = ("PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY")


class _Net:
    """urlopen stand-in: serves `served` by URL, raises URLError otherwise, records every URL asked."""

    def __init__(self) -> None:
        self.served: dict[str, object] = {}
        self.asked: list[str] = []

    def __call__(self, req, timeout=None):  # noqa: ANN001, ANN204
        url = req.full_url
        self.asked.append(url)
        if url not in self.served:
            raise URLError("unreachable (stubbed)")
        body = json.dumps(self.served[url]).encode("utf-8")

        class _Resp:
            def read(self) -> bytes:
                return body

            def __enter__(self):  # noqa: ANN204
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        return _Resp()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Net:
    """Arm the real check: no skip variable, no anchor, sandboxed HOME/cache/audit log, network stubbed."""
    for var in (*SKIP_VARS, pvh.RELEASE_CANDIDATE_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(pvh, "_VERIFIED_THIS_PROCESS", False)
    monkeypatch.setattr(pvh, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pvh, "_AUDIT_LOG", tmp_path / "audit.log")
    net = _Net()
    monkeypatch.setattr(pvh, "urlopen", net)
    return net


def _sha(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _plugin(tmp_path: Path, version: object = VERSION) -> Path:
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    meta: dict[str, object] = {"name": "x"}
    if version is not None:
        meta["version"] = version
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(meta), encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _manifest(root: Path) -> dict[str, object]:
    return {
        "files": {
            ".claude-plugin/plugin.json": _sha(root / ".claude-plugin" / "plugin.json"),
            "scripts/a.py": _sha(root / "scripts" / "a.py"),
        }
    }


def _events(tmp_path: Path) -> list[str]:
    log = tmp_path / "audit.log"
    return [json.loads(line)["event"] for line in log.read_text().splitlines()] if log.exists() else []


def _cache_tag(version: str = VERSION) -> Path:
    return pvh.CACHE_DIR / f"github-manifest-tag-v{version}.json"


# --------------------------------------------------------------------------- conftest guard


def test_conftest_sets_the_sanctioned_skip_at_module_level() -> None:
    """The suite-wide dev skip is set unconditionally by conftest.py (the indent-bug guard)."""
    assert _CONFTEST_SKIP_VALUE == "1"


# --------------------------------------------------------------------------- no main fallback, tag-keyed cache


def test_fetch_never_asks_a_branch_url(env: _Net) -> None:
    """Unreachable tag: only the two tag URLs (new + legacy filename) are asked, never `/main/`."""
    assert pvh._fetch_github_manifest(VERSION, prefer_cache=False) is None
    assert env.asked == [TAG_URL, pvh.REPO_RAW_TAG_URL_LEGACY.format(version=VERSION)]
    assert not any("/main/" in url for url in env.asked)


def test_fetch_returns_and_caches_the_tag_manifest(env: _Net) -> None:
    """Two-sided: a served tag manifest is returned and cached under the tag key."""
    env.served[TAG_URL] = {"files": {"a": "b"}}
    assert pvh._fetch_github_manifest(VERSION, prefer_cache=False) == {"files": {"a": "b"}}
    assert _cache_tag().is_file()


def test_main_manifest_under_the_old_cache_key_is_ignored(env: _Net, tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """Offline: a branch manifest left under the pre-#255 key `github-manifest-v<x>.json` is never used."""
    root = _plugin(tmp_path)
    pvh.CACHE_DIR.mkdir(parents=True)
    (pvh.CACHE_DIR / f"github-manifest-v{VERSION}.json").write_text(json.dumps(_manifest(root)))
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    assert f"no hash manifest for tag v{VERSION}" in capsys.readouterr().err


def test_offline_with_a_cache_of_the_same_tag_passes(env: _Net, tmp_path: Path) -> None:
    """Two-sided: the cache of the SAME tag verifies the install with the network down."""
    root = _plugin(tmp_path)
    pvh.CACHE_DIR.mkdir(parents=True)
    _cache_tag().write_text(json.dumps(_manifest(root)))
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is True


def test_offline_without_cache_exits_2_naming_url_cache_and_both_hints(env: _Net, tmp_path: Path, capsys) -> None:  # noqa: ANN001
    root = _plugin(tmp_path)
    with pytest.raises(SystemExit) as exc:
        pvh.verify_self_integrity(root, fail_on_mismatch=True, quiet=True)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert TAG_URL in err
    assert str(_cache_tag()) in err
    assert USER_HINT in err
    assert DEV_HINT in err
    assert _events(tmp_path)[-1] == "refused"


# --------------------------------------------------------------------------- versions


@pytest.mark.parametrize("version", [None, 5, "main", "1.0/../x", "../x", ""])
def test_missing_or_unsafe_version_refuses_with_no_network_and_no_cache_read(
    env: _Net,
    tmp_path: Path,
    capsys,
    monkeypatch,
    version: object,  # noqa: ANN001
) -> None:
    """No request and no cache read: the spy urlopen and cache reader record nothing."""
    reads: list[object] = []
    monkeypatch.setattr(pvh, "_read_cached_manifest", lambda v: reads.append(v))
    root = _plugin(tmp_path, version=version)
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    err = capsys.readouterr().err
    assert "plugin version could not be read" in err
    assert DEV_HINT in err
    assert env.asked == []
    assert reads == []


def test_release_version_with_suffix_is_accepted(env: _Net, tmp_path: Path) -> None:
    """Two-sided: a pre-release suffix is still a release-shaped version and is fetched."""
    root = _plugin(tmp_path, version="1.2.3-rc.1")
    pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True)
    assert env.asked[0] == pvh.REPO_RAW_TAG_URL.format(version="1.2.3-rc.1")


# --------------------------------------------------------------------------- malformed manifests


@pytest.mark.parametrize(
    "manifest",
    [{}, {"files": {}}, {"files": []}, ["not", "a", "dict"], "text", {"files": {"scripts/a.py": 5}}],
)
def test_malformed_manifest_refuses_without_crash(env: _Net, tmp_path: Path, capsys, monkeypatch, manifest) -> None:  # noqa: ANN001
    root = _plugin(tmp_path)
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda v, prefer_cache=True: manifest)
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    err = capsys.readouterr().err
    assert "malformed or empty" in err
    assert DEV_HINT in err


def test_served_non_dict_json_is_not_a_manifest(env: _Net, tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """A tag URL that serves a JSON list reads as "no manifest": refuse."""
    root = _plugin(tmp_path)
    env.served[TAG_URL] = ["x"]
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    assert f"no hash manifest for tag v{VERSION}" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_unreadable_file_is_reported(env: _Net, tmp_path: Path, capsys) -> None:  # noqa: ANN001
    root = _plugin(tmp_path)
    env.served[TAG_URL] = _manifest(root)
    target = root / "scripts" / "a.py"
    target.chmod(0)
    try:
        assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    finally:
        target.chmod(0o644)
    assert "scripts/a.py  (unreadable" in capsys.readouterr().err


def test_matching_manifest_passes(env: _Net, tmp_path: Path) -> None:
    """Two-sided control for the refusals above."""
    root = _plugin(tmp_path)
    env.served[TAG_URL] = _manifest(root)
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is True


# --------------------------------------------------------------------------- refusal contract


def test_refusal_is_not_memoized_and_prints_when_quiet(env: _Net, tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """fail_on_mismatch=False returns False on EVERY call, and quiet=True still prints the refusal."""
    root = _plugin(tmp_path)
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    assert pvh._VERIFIED_THIS_PROCESS is False
    assert capsys.readouterr().err.count(pvh.REFUSAL_HEADLINE) == 2


# --------------------------------------------------------------------------- _detect_added_files


def test_enumerator_import_failure_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "_plugin_compute_hashes", None)
    with pytest.raises(pvh._AddedFilesError):
        pvh._detect_added_files(tmp_path, {})


def test_enumerator_os_error_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import _plugin_compute_hashes

    def boom(_root: Path) -> set[str]:
        raise OSError("listing failed (stubbed)")

    monkeypatch.setattr(_plugin_compute_hashes, "enumerate_shipped_files", boom)
    with pytest.raises(pvh._AddedFilesError, match="listing failed"):
        pvh._detect_added_files(tmp_path, {})


@pytest.mark.parametrize("failure", ["import", "oserror"])
def test_enumeration_failure_refuses_plain_path(env: _Net, tmp_path: Path, capsys, monkeypatch, failure: str) -> None:  # noqa: ANN001
    root = _plugin(tmp_path)
    env.served[TAG_URL] = _manifest(root)
    if failure == "import":
        monkeypatch.setitem(sys.modules, "_plugin_compute_hashes", None)
        expected = "could not load enumerate_shipped_files"
    else:
        import _plugin_compute_hashes

        def boom(_root: Path) -> set[str]:
            raise OSError("listing failed (stubbed)")

        monkeypatch.setattr(_plugin_compute_hashes, "enumerate_shipped_files", boom)
        expected = "listing failed (stubbed)"
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    assert expected in capsys.readouterr().err


def _fake_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A git-free release candidate: git answers are stubbed; returns the matching anchor."""
    (tmp_path / "a.py").write_text("x = 1\n")
    manifest = {"files": {"a.py": _sha(tmp_path / "a.py")}}
    (tmp_path / pvh.MANIFEST_FILE).write_bytes(json.dumps(manifest).encode())
    sha = hashlib.sha256((tmp_path / pvh.MANIFEST_FILE).read_bytes()).hexdigest()
    head = "c" * 40

    def fake_git(_root: Path, *args: str) -> bytes:
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path.resolve()).encode()
        if args[:2] == ("rev-parse", "--verify"):
            return head.encode()
        if args[0] == "status":
            return b""
        if args[0] == "show":
            return (tmp_path / pvh.MANIFEST_FILE).read_bytes()
        if args[0] == "ls-files":
            return b"a.py\0"
        raise AssertionError(args)

    monkeypatch.setattr(pvh, "_git_bytes", fake_git)
    return f"{sha}:{head}"


@pytest.mark.parametrize("failure", ["import", "oserror"])
def test_enumeration_failure_refuses_anchor_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str) -> None:
    """The anchor path turns the failure into a named reason (not a traceback, not a pass)."""
    anchor = _fake_candidate(tmp_path, monkeypatch)
    if failure == "import":
        monkeypatch.setitem(sys.modules, "_plugin_compute_hashes", None)
        expected = "could not load enumerate_shipped_files"
    else:
        import _plugin_compute_hashes

        def boom(_root: Path) -> set[str]:
            raise OSError("listing failed (stubbed)")

        monkeypatch.setattr(_plugin_compute_hashes, "enumerate_shipped_files", boom)
        expected = "listing failed (stubbed)"
    reasons, _checked = pvh._release_candidate_reasons(tmp_path.resolve(), anchor)
    assert any(expected in r for r in reasons)


def test_anchor_path_passes_when_enumeration_works(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two-sided control: the same fake candidate with a working enumerator has no reasons."""
    anchor = _fake_candidate(tmp_path, monkeypatch)
    import _plugin_compute_hashes

    monkeypatch.setattr(_plugin_compute_hashes, "enumerate_shipped_files", lambda _r: {"a.py"})
    assert pvh._release_candidate_reasons(tmp_path.resolve(), anchor) == ([], 1)


# --------------------------------------------------------------------------- skips


def test_pytest_current_test_is_not_a_skip(env: _Net, tmp_path: Path, capsys, monkeypatch) -> None:  # noqa: ANN001
    """PYTEST_CURRENT_TEST alone: the check runs (the tag URL is asked) and refuses with no manifest."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t::t (call)")
    root = _plugin(tmp_path)
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    assert TAG_URL in env.asked
    assert "SKIPPED" not in capsys.readouterr().err
    assert "test-run-skip" not in _events(tmp_path)


def test_subprocess_with_pytest_env_and_no_skip_variable_refuses(tmp_path: Path) -> None:
    """A real child process (no monkeypatch reaches it) with PYTEST_CURRENT_TEST set and no skip refuses.

    The version "main" refuses before any network or cache access, so the
    run is hermetic; before #255 the PYTEST_CURRENT_TEST skip returned 0 first.
    """
    root = _plugin(tmp_path, version="main")
    child_env = {k: v for k, v in os.environ.items() if k not in (*SKIP_VARS, pvh.RELEASE_CANDIDATE_ENV)}
    child_env["PYTEST_CURRENT_TEST"] = "t::t (call)"
    child_env["HOME"] = str(tmp_path / "home")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        capture_output=True,
        text=True,
        env=child_env,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 2, proc.stderr
    assert "plugin version could not be read" in proc.stderr
    assert "SKIPPED" not in proc.stderr


@pytest.mark.parametrize("var", SKIP_VARS)
def test_sanctioned_skip_writes_one_warning_and_one_audit_line(
    env: _Net,
    tmp_path: Path,
    capsys,
    monkeypatch,
    var: str,  # noqa: ANN001
) -> None:
    monkeypatch.setenv(var, "1")
    root = _plugin(tmp_path)
    assert pvh.verify_self_integrity(root, fail_on_mismatch=True, quiet=True) is True
    assert env.asked == []
    err = capsys.readouterr().err
    assert err.count("WARNING: integrity check SKIPPED by PLUGIN_SKIP_GITHUB_INTEGRITY") == 1
    assert _events(tmp_path) == ["skip-variable-honoured"]


@pytest.mark.parametrize("var", SKIP_VARS)
def test_anchor_is_enforced_even_with_the_skip_variable(
    env: _Net, tmp_path: Path, capsys, monkeypatch, var: str
) -> None:  # noqa: ANN001
    """With the anchor set, a skip variable cannot pass a candidate that fails it."""
    monkeypatch.setenv(var, "1")
    monkeypatch.setenv(pvh.RELEASE_CANDIDATE_ENV, "not-an-anchor")
    root = _plugin(tmp_path)
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    err = capsys.readouterr().err
    assert "is malformed" in err
    assert "SKIPPED" not in err


def _wheel_site(tmp_path: Path) -> Path:
    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    (site / "scripts").mkdir(parents=True)
    (site / "scripts" / "_plugin_verify_hashes.py").write_text("# installed copy\n")
    dist_info = site / f"claude_plugins_validation-{VERSION}.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: claude-plugins-validation\nVersion: {VERSION}\n")
    (dist_info / "RECORD").write_text("scripts/_plugin_verify_hashes.py,sha256=x,1\n")
    return site


@pytest.mark.parametrize("var", SKIP_VARS)
def test_wheel_mode_ignores_the_skip_variable(env: _Net, tmp_path: Path, capsys, monkeypatch, var: str) -> None:  # noqa: ANN001
    """A package install refuses with no tag manifest even with a skip variable set, and says it ignored it."""
    site = _wheel_site(tmp_path)
    monkeypatch.setattr(pvh, "_MODULE_FILE", site / "scripts" / "_plugin_verify_hashes.py")
    monkeypatch.setattr(pvh, "_WHEEL_FETCH_RETRY_SLEEP_SEC", 0)
    monkeypatch.setenv(var, "1")
    assert pvh.verify_self_integrity(fail_on_mismatch=False, quiet=True) is False
    err = capsys.readouterr().err
    assert var in err.split("is ignored for package installs")[0]
    assert pvh.REFUSAL_HEADLINE in err
