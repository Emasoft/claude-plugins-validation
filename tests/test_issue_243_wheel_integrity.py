"""Issue #243 — self-integrity of a WHEEL (uvx / pip) install of CPV.

Under `uvx --from git+...@vX.Y.Z cpv-remote-validate plugin . --strict` the
module lives in `<site-packages>/scripts/`. The old code treated site-packages
as a plugin root, read no version, compared against the `main` manifest and
reported every repository-only file as "deleted locally" (exit 2).

These tests build a FAKE site-packages tree (module file + dist-info RECORD +
METADATA) and stub only the network (`urlopen`), so the real detection, fetch,
cache, mapping and added-file logic run. No network is touched.

Set CPV_TEST243_SCRIPTS_DIR to import `_plugin_verify_hashes` from another
directory (used to prove these tests fail on the pre-fix file).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from urllib.error import URLError

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(os.environ.get("CPV_TEST243_SCRIPTS_DIR") or (REPO_ROOT / "scripts"))
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import _plugin_verify_hashes as pvh  # noqa: E402

VERSION = "5.22.0"
TAG_URL = f"https://raw.githubusercontent.com/Emasoft/claude-plugins-validation/v{VERSION}/.plugin-self-hashes.json"
HEADLINE = (
    "CPV refused to run: its own integrity could not be verified. This is not a verdict on the plugin being validated."
)


def _sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _build_site(
    tmp_path: Path,
    *,
    dist_name: str = "claude-plugins-validation",
    version: str | None = VERSION,
    record_lists_self: bool = True,
    site_dir_name: str = "site-packages",
) -> Path:
    """A fake installed wheel: module + shipped files + one dist-info."""
    site = tmp_path / "venv" / "lib" / "python3.12" / site_dir_name
    _write(site / "scripts" / "_plugin_verify_hashes.py", "# installed copy\n")
    _write(site / "scripts" / "validate_x.py", "print('x')\n")
    _write(site / "scripts" / "rules" / "r.json", "{}\n")
    _write(site / "scripts" / ".markdownlint.json", '{"default": true}\n')
    dist_info = site / f"{_norm(dist_name)}-{version or '0'}.dist-info"
    meta = f"Metadata-Version: 2.1\nName: {dist_name}\n" + (f"Version: {version}\n" if version else "")
    _write(dist_info / "METADATA", meta)
    self_row = "scripts/_plugin_verify_hashes.py,sha256=x,1\n" if record_lists_self else ""
    _write(dist_info / "RECORD", self_row + "scripts/validate_x.py,sha256=y,2\n")
    return site


def _manifest_for(site: Path) -> dict[str, object]:
    """Tag manifest as the repo builds it: REPOSITORY paths + repo-only entries."""
    return {
        "version": 1,
        "files": {
            "scripts/_plugin_verify_hashes.py": _sha(site / "scripts" / "_plugin_verify_hashes.py"),
            "scripts/validate_x.py": _sha(site / "scripts" / "validate_x.py"),
            "scripts/rules/r.json": _sha(site / "scripts" / "rules" / "r.json"),
            # force-included: repo key differs from the installed path
            ".markdownlint.json": _sha(site / "scripts" / ".markdownlint.json"),
            # (5.22.x wheels force-include no templates; a templates/ key is repository-only)
            # repository-only entries a wheel never ships
            ".github/workflows/x.yml": "sha256:" + "1" * 64,
            ".claude-plugin/plugin.json": "sha256:" + "2" * 64,
            ".claude/project/memory/note.md": "sha256:" + "3" * 64,
            "templates/README-marketplace.md": "sha256:" + "4" * 64,
            "tests/test_x.py": "sha256:" + "5" * 64,
        },
    }


class _Net:
    """urlopen stub: serves a dict per exact URL, records every URL asked."""

    def __init__(self, served: dict[str, object] | None = None) -> None:
        self.served = served or {}
        self.asked: list[str] = []

    def __call__(self, req, timeout=None):  # noqa: ANN001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.asked.append(url)
        if url not in self.served:
            raise URLError("404 (stubbed)")
        body = json.dumps(self.served[url]).encode("utf-8")

        class _Resp(io.BytesIO):
            def __enter__(self):  # noqa: ANN204
                return self

            def __exit__(self, *a):  # noqa: ANN002, ANN204
                return False

        return _Resp(body)


@pytest.fixture
def armed(monkeypatch, tmp_path):
    """Arm the real verify path; network stubbed, cache isolated."""
    monkeypatch.setattr(pvh, "_VERIFIED_THIS_PROCESS", False)
    monkeypatch.setattr(pvh, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("PLUGIN_SKIP_GITHUB_INTEGRITY", raising=False)
    monkeypatch.delenv("CPV_SKIP_GITHUB_INTEGRITY", raising=False)
    # no real backoff in tests (raising=False: the pre-fix module lacks it)
    monkeypatch.setattr(pvh, "_WHEEL_FETCH_RETRY_SLEEP_SEC", 0, raising=False)

    def arm(site: Path, served: dict[str, object] | None = None) -> _Net:
        # PYTEST_CURRENT_TEST is re-set for the call phase, so delete it here.
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        # raising=False so the plugin-mode tests also run against the pre-fix
        # module (which has no _MODULE_FILE) when proving HEAD behaviour.
        monkeypatch.setattr(pvh, "_MODULE_FILE", site / "scripts" / "_plugin_verify_hashes.py", raising=False)
        net = _Net(served)
        monkeypatch.setattr(pvh, "urlopen", net)
        return net

    return arm


def _run(fail: bool = False) -> bool:
    return pvh.verify_self_integrity(fail_on_mismatch=fail, quiet=True)


# --------------------------------------------------------------------------- pass path


def test_wheel_install_verifies_against_exact_tag_only(armed, tmp_path, capsys):
    site = _build_site(tmp_path)
    net = armed(site, {TAG_URL: _manifest_for(site)})
    assert _run() is True
    assert net.asked == [TAG_URL], "wheel mode must ask ONLY for the exact tag manifest"
    err = capsys.readouterr().err
    assert "deleted locally" not in err
    assert HEADLINE not in err


def test_repo_only_entries_are_not_reported_missing(armed, tmp_path, capsys):
    """Repo-only keys (.github/, .claude-plugin/, tests/, unshipped templates) never count.

    A real mismatch is planted so the refusal listing is printed; it must name
    the tampered file and none of the repository-only entries.
    """
    site = _build_site(tmp_path)
    manifest = _manifest_for(site)
    (site / "scripts" / "validate_x.py").write_text("tampered\n", encoding="utf-8")
    armed(site, {TAG_URL: manifest})
    assert _run() is False
    err = capsys.readouterr().err
    assert "scripts/validate_x.py" in err
    for repo_only in (".github/workflows/x.yml", ".claude-plugin/plugin.json", "tests/test_x.py", "README-marketplace"):
        assert repo_only not in err


def test_pycache_is_not_an_added_file(armed, tmp_path, capsys):
    site = _build_site(tmp_path)
    _write(site / "scripts" / "__pycache__" / "validate_x.cpython-312.pyc", "bytecode")
    armed(site, {TAG_URL: _manifest_for(site)})
    assert pvh.verify_self_integrity(fail_on_mismatch=False, quiet=False) is True
    assert "verified against the GitHub tag manifest" in capsys.readouterr().err


# --------------------------------------------------------------------------- refusals


def test_one_changed_byte_refuses_exit_2(armed, tmp_path, capsys):
    site = _build_site(tmp_path)
    manifest = _manifest_for(site)
    p = site / "scripts" / "validate_x.py"
    p.write_bytes(p.read_bytes()[:-1] + b"!")
    armed(site, {TAG_URL: manifest})
    with pytest.raises(SystemExit) as exc:
        _run(fail=True)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert HEADLINE in err, "refusal must be printed even when quiet=True"
    assert "scripts/validate_x.py" in err
    assert pvh._VERIFIED_THIS_PROCESS is False


def test_changed_force_included_file_refuses(armed, tmp_path, capsys):
    """.markdownlint.json is hashed at its INSTALLED path scripts/.markdownlint.json."""
    site = _build_site(tmp_path)
    manifest = _manifest_for(site)
    (site / "scripts" / ".markdownlint.json").write_text('{"default": false}\n', encoding="utf-8")
    armed(site, {TAG_URL: manifest})
    assert _run() is False
    assert "scripts/.markdownlint.json" in capsys.readouterr().err


def test_missing_shipped_file_refuses(armed, tmp_path, capsys):
    site = _build_site(tmp_path)
    manifest = _manifest_for(site)
    (site / "scripts" / "rules" / "r.json").unlink()
    armed(site, {TAG_URL: manifest})
    assert _run() is False
    assert "scripts/rules/r.json" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    ["scripts/evil.py", "scripts/build/evil.py", "scripts/reports/evil.py", "templates/evil.sh"],
)
def test_extra_file_refuses(armed, tmp_path, capsys, extra):
    site = _build_site(tmp_path)
    manifest = _manifest_for(site)
    _write(site / extra, "import os\n")
    armed(site, {TAG_URL: manifest})
    assert _run() is False
    assert extra in capsys.readouterr().err


def test_unknown_version_refuses(armed, tmp_path, capsys):
    site = _build_site(tmp_path, version=None)
    net = armed(site, {TAG_URL: _manifest_for(site)})
    with pytest.raises(SystemExit) as exc:
        _run(fail=True)
    assert exc.value.code == 2
    assert HEADLINE in capsys.readouterr().err
    assert net.asked == [], "no manifest may be fetched without a known version"


def test_dist_info_name_mismatch_refuses(armed, tmp_path, capsys):
    """A dist-info whose METADATA Name is another project is not trusted for the version."""
    site = _build_site(tmp_path)
    meta = next(site.glob("*.dist-info")) / "METADATA"
    meta.write_text(f"Metadata-Version: 2.1\nName: something-else\nVersion: {VERSION}\n", encoding="utf-8")
    armed(site, {TAG_URL: _manifest_for(site)})
    assert _run() is False
    assert HEADLINE in capsys.readouterr().err


def test_no_manifest_refuses_and_never_asks_main(armed, tmp_path, capsys):
    site = _build_site(tmp_path)
    main_url = "https://raw.githubusercontent.com/Emasoft/claude-plugins-validation/main/.plugin-self-hashes.json"
    net = armed(site, {main_url: _manifest_for(site)})  # main reachable, tag 404
    with pytest.raises(SystemExit) as exc:
        _run(fail=True)
    assert exc.value.code == 2
    assert HEADLINE in capsys.readouterr().err
    assert main_url not in net.asked
    assert all("/main/" not in u for u in net.asked)


@pytest.mark.parametrize("bad", [{"files": []}, {"files": {}}, {"hashed_files": None}, {"version": 1}])
def test_malformed_or_empty_manifest_refuses(armed, tmp_path, capsys, bad):
    site = _build_site(tmp_path)
    armed(site, {TAG_URL: bad})
    assert _run() is False
    assert HEADLINE in capsys.readouterr().err


def test_stale_main_and_plugin_mode_caches_are_ignored(armed, tmp_path, capsys):
    """Network down: a `main` cache and the plugin-mode per-version cache must NOT be used."""
    site = _build_site(tmp_path)
    good = json.dumps(_manifest_for(site))
    cache = tmp_path / "cache"
    _write(cache / "github-manifest-vmain.json", good)
    _write(cache / f"github-manifest-v{VERSION}.json", good)  # may hold a main fallback
    armed(site, {})
    assert _run() is False
    assert HEADLINE in capsys.readouterr().err


def test_exact_tag_cache_is_used_when_offline(armed, tmp_path, capsys):
    site = _build_site(tmp_path)
    cache = tmp_path / "cache"
    _write(cache / f"github-manifest-tag-v{VERSION}.json", json.dumps(_manifest_for(site)))
    armed(site, {})
    assert pvh.verify_self_integrity(fail_on_mismatch=False, quiet=False) is True
    # a real verification, not the old fail-open "could not fetch" pass
    assert "verified against the GitHub tag manifest" in capsys.readouterr().err


def test_fetched_tag_manifest_is_cached_under_exact_ref(armed, tmp_path):
    site = _build_site(tmp_path)
    armed(site, {TAG_URL: _manifest_for(site)})
    assert _run() is True
    assert (tmp_path / "cache" / f"github-manifest-tag-v{VERSION}.json").is_file()
    assert not (tmp_path / "cache" / "github-manifest-vmain.json").exists()


def test_two_claiming_dist_infos_refuse(armed, tmp_path, capsys):
    site = _build_site(tmp_path)
    twin = site / f"{_norm('claude-plugins-validation')}-9.9.9.dist-info"
    _write(twin / "METADATA", "Metadata-Version: 2.1\nName: claude-plugins-validation\nVersion: 9.9.9\n")
    _write(twin / "RECORD", "scripts/_plugin_verify_hashes.py,sha256=x,1\n")
    armed(site, {TAG_URL: _manifest_for(site)})
    assert _run() is False
    assert HEADLINE in capsys.readouterr().err


def test_walk_does_not_import_a_file_under_verification(armed, tmp_path, capsys, monkeypatch):
    """Trust cycle: the walk applies its skip rule locally, never importing _plugin_compute_hashes.

    That module is itself one of the verified files; importing it first would let a tampered copy
    decide what the walk ignores. With the import made impossible, verification still completes.
    """
    site = _build_site(tmp_path)
    armed(site, {TAG_URL: _manifest_for(site)})
    monkeypatch.setitem(sys.modules, "_plugin_compute_hashes", None)  # any import would raise ImportError
    assert pvh.verify_self_integrity(fail_on_mismatch=False, quiet=False) is True
    assert "verified against the GitHub tag manifest" in capsys.readouterr().err


@pytest.mark.parametrize("var", ["PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY", "PYTEST_CURRENT_TEST"])
def test_skip_variables_are_ignored_in_wheel_mode(armed, tmp_path, capsys, monkeypatch, var):
    """Owner policy: a package install is always hash-verified; skip switches are ignored (and said so)."""
    site = _build_site(tmp_path)
    manifest = _manifest_for(site)
    p = site / "scripts" / "validate_x.py"
    p.write_bytes(p.read_bytes()[:-1] + b"!")
    armed(site, {TAG_URL: manifest})
    monkeypatch.setenv(var, "1")
    with pytest.raises(SystemExit) as exc:
        _run(fail=True)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert HEADLINE in err
    assert f"{var} is ignored for package installs" in err


def test_release_candidate_anchor_is_ignored_and_audited_in_wheel_mode(armed, tmp_path, capsys, monkeypatch):
    """A package install ignores PLUGIN_INTEGRITY_RELEASE_CANDIDATE, says so, audits it, and still refuses a tamper."""
    site = _build_site(tmp_path)
    manifest = _manifest_for(site)
    p = site / "scripts" / "validate_x.py"
    p.write_bytes(p.read_bytes()[:-1] + b"!")
    armed(site, {TAG_URL: manifest})
    log = tmp_path / "audit.log"
    monkeypatch.setattr(pvh, "_AUDIT_LOG", log)
    monkeypatch.setenv(pvh.RELEASE_CANDIDATE_ENV, "a" * 64 + ":" + "b" * 40)
    with pytest.raises(SystemExit) as exc:
        _run(fail=True)
    assert exc.value.code == 2
    assert f"{pvh.RELEASE_CANDIDATE_ENV} is ignored for package installs" in capsys.readouterr().err
    events = [json.loads(line)["event"] for line in log.read_text().splitlines()]
    assert events[0] == "release-candidate-ignored"
    assert "refused" in events


@pytest.mark.parametrize("var", ["PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY"])
def test_plugin_mode_keeps_its_skips(armed, tmp_path, monkeypatch, var):
    """Plugin mode (a root with plugin.json) still honours the sanctioned skip switches (out of #243 scope)."""
    root = tmp_path / "plugin"
    _write(root / ".claude-plugin" / "plugin.json", json.dumps({"name": "x", "version": "1.2.3"}))
    armed(_build_site(tmp_path / "unused"))
    called: list[object] = []
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda v, prefer_cache=True: called.append(v))
    monkeypatch.setenv(var, "1")
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is True
    assert called == []


def test_plugin_mode_pytest_env_is_not_a_skip(armed, tmp_path, monkeypatch):
    """Issue #255: PYTEST_CURRENT_TEST no longer skips plugin mode; the check runs (and refuses with no manifest)."""
    root = tmp_path / "plugin"
    _write(root / ".claude-plugin" / "plugin.json", json.dumps({"name": "x", "version": "1.2.3"}))
    armed(_build_site(tmp_path / "unused"))
    called: list[object] = []
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda v, prefer_cache=True: called.append(v))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    assert called == ["1.2.3"]


@pytest.mark.parametrize(
    "rel", ["scripts/x.pyc", "scripts/rules/x.pyc", "templates/x.pyc", "scripts/__pycache__/evil.py"]
)
def test_sourceless_or_non_cache_bytecode_dir_files_refuse(armed, tmp_path, capsys, rel):
    """A .pyc outside __pycache__ is importable; a non-.pyc inside __pycache__ is a namespace path."""
    site = _build_site(tmp_path)
    manifest = _manifest_for(site)
    _write(site / rel, "x")
    armed(site, {TAG_URL: manifest})
    assert _run() is False
    assert rel in capsys.readouterr().err


def test_fetch_retries_once_then_succeeds(armed, tmp_path, monkeypatch):
    """One transient failure must not fall straight back to the cache (or refuse)."""
    site = _build_site(tmp_path)
    net = armed(site, {TAG_URL: _manifest_for(site)})
    state = {"n": 0}

    def flaky(req, timeout=None):  # noqa: ANN001, ANN202
        state["n"] += 1
        if state["n"] == 1:
            net.asked.append(req.full_url)
            raise URLError("transient (stubbed)")
        return net(req, timeout)

    monkeypatch.setattr(pvh, "urlopen", flaky)
    assert _run() is True
    assert net.asked == [TAG_URL, TAG_URL]


# --------------------------------------------------------------------------- detection / neither mode


def test_dist_packages_is_detected(tmp_path):
    site = _build_site(tmp_path, site_dir_name="dist-packages")
    assert pvh._detect_wheel_install(site / "scripts" / "_plugin_verify_hashes.py") is not None


def test_record_not_listing_self_is_not_wheel_mode(tmp_path):
    site = _build_site(tmp_path, record_lists_self=False)
    assert pvh._detect_wheel_install(site / "scripts" / "_plugin_verify_hashes.py") is None


def test_editable_dev_install_is_not_wheel_mode(tmp_path):
    """A module in a source checkout (no site-packages parent) is never wheel mode."""
    repo = tmp_path / "repo"
    _write(repo / "scripts" / "_plugin_verify_hashes.py", "#\n")
    # even with a dist-info beside it, the parent is not site-packages
    _write(repo / "claude_plugins_validation-1.0.0.dist-info" / "RECORD", "scripts/_plugin_verify_hashes.py,,\n")
    assert pvh._detect_wheel_install(repo / "scripts" / "_plugin_verify_hashes.py") is None


def test_neither_mode_refuses_site_packages_without_dist_info(armed, tmp_path, capsys):
    """Module under site-packages, no matching dist-info, no plugin.json -> refuse (no main fallback)."""
    site = _build_site(tmp_path, record_lists_self=False)
    net = armed(site, {})
    with pytest.raises(SystemExit) as exc:
        _run(fail=True)
    assert exc.value.code == 2
    assert HEADLINE in capsys.readouterr().err
    assert net.asked == []


def test_neither_mode_refuses_explicit_root_without_plugin_json(armed, tmp_path, capsys, monkeypatch):
    root = tmp_path / "not-a-plugin"
    _write(root / "scripts" / "x.py", "#\n")
    armed(_build_site(tmp_path / "unused"))
    fetched: list[object] = []
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda v, prefer_cache=True: fetched.append(v))
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    err = capsys.readouterr().err
    assert HEADLINE in err
    assert "not recognised as a package install" in err
    assert fetched == [], "neither mode must not reach the version-None/main fetch"


@pytest.mark.parametrize("var", ["PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY", "PYTEST_CURRENT_TEST"])
def test_neither_mode_ignores_skip_variables(armed, tmp_path, capsys, monkeypatch, var):
    """No version to anchor: neither mode refuses even with a skip switch set, and says it ignored it."""
    root = tmp_path / "not-a-plugin"
    _write(root / "scripts" / "x.py", "#\n")
    armed(_build_site(tmp_path / "unused"))
    fetched: list[object] = []
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda v, prefer_cache=True: fetched.append(v))
    monkeypatch.setenv(var, "1")
    with pytest.raises(SystemExit) as exc:
        pvh.verify_self_integrity(root, fail_on_mismatch=True, quiet=True)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert HEADLINE in err
    assert f"{var} is ignored for a root that is neither a package install nor a plugin" in err
    assert fetched == []
    assert pvh._VERIFIED_THIS_PROCESS is False


@pytest.mark.parametrize("var", ["PLUGIN_SKIP_GITHUB_INTEGRITY", "PYTEST_CURRENT_TEST"])
def test_neither_mode_self_located_ignores_skip_variables(armed, tmp_path, capsys, monkeypatch, var):
    """Self-located under site-packages with no proven dist-info and no plugin.json, skip var set -> refuse."""
    site = _build_site(tmp_path, record_lists_self=False)
    armed(site, {})
    monkeypatch.setenv(var, "1")
    assert _run() is False
    err = capsys.readouterr().err
    assert HEADLINE in err
    assert f"{var} is ignored" in err


def test_plugin_mode_unchanged(armed, tmp_path, monkeypatch):
    """A plugin root with plugin.json still goes through _fetch_github_manifest(version)."""
    root = tmp_path / "plugin"
    _write(root / ".claude-plugin" / "plugin.json", json.dumps({"name": "x", "version": "1.2.3"}))
    _write(root / "scripts" / "a.py", "#\n")
    armed(_build_site(tmp_path / "unused"))
    seen: list[object] = []
    manifest = {
        "files": {
            ".claude-plugin/plugin.json": _sha(root / ".claude-plugin" / "plugin.json"),
            "scripts/a.py": _sha(root / "scripts" / "a.py"),
        }
    }

    def fake_fetch(v, prefer_cache=True):  # noqa: ANN001, ANN202
        seen.append(v)
        return manifest

    monkeypatch.setattr(pvh, "_fetch_github_manifest", fake_fetch)
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is True
    assert seen == ["1.2.3"]


# --------------------------------------------------------------------------- pyproject pins


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_dist_name_matches_pyproject():
    assert pvh.DIST_NAME == _pyproject()["project"]["name"]


def test_force_include_map_matches_pyproject():
    wheel = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert dict(pvh._WHEEL_FORCE_INCLUDE) == dict(wheel["force-include"])
    assert wheel["packages"] == ["scripts"]


def test_detector_finds_dist_info_named_from_pyproject_name(tmp_path, monkeypatch):
    """The dist-info glob is derived from [project].name (PEP 427/503), not hardcoded."""
    name = _pyproject()["project"]["name"]
    site = _build_site(tmp_path, dist_name=name)
    expected_glob = f"{_norm(name)}-*.dist-info"
    assert len(list(site.glob(expected_glob))) == 1
    assert pvh._detect_wheel_install(site / "scripts" / "_plugin_verify_hashes.py") is not None


def test_detector_follows_dist_name_constant(tmp_path, monkeypatch):
    """A rename changes one constant; the detector follows it (and stops matching the old name)."""
    site = _build_site(tmp_path, dist_name="Complete.Plugin_Validation")
    module = site / "scripts" / "_plugin_verify_hashes.py"
    monkeypatch.setattr(pvh, "DIST_NAME", "complete-plugin-validation")
    install = pvh._detect_wheel_install(module)
    assert install is not None and install.dist_info.name.startswith("complete_plugin_validation-")
    assert pvh._wheel_version(install) == VERSION
    monkeypatch.setattr(pvh, "DIST_NAME", "claude-plugins-validation")
    assert pvh._detect_wheel_install(module) is None
