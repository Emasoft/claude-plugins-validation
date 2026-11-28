"""All-platform guard for the generated binary-release scaffold (issue #5290).

Owner rule 2026-11-28: every plugin must be multiplatform; binaries for every
platform ship in the plugin. ``gen_release_binaries_yml`` must therefore build
for macOS, Linux AND Windows (x86_64 + aarch64 each), name Windows assets
``<bin>-<target>.exe``, and smoke-compile on all three OS families on push/PR.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_pipeline_profile as prof  # noqa: E402
from generate_plugin_repo import PluginParams, gen_release_binaries_yml  # noqa: E402

_ALL_TARGETS = {
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-gnu",
    "aarch64-pc-windows-msvc",
    "x86_64-pc-windows-msvc",
}
_USES_RE = re.compile(r"^\s*(?:-\s*)?uses\s*:\s*(\S+)", re.MULTILINE)


def _yml() -> str:
    return gen_release_binaries_yml(
        PluginParams(
            name="my-test-plugin",
            description="A test plugin",
            author="Test Author",
            author_email="test@example.com",
            github_owner="test-owner",
        )
    )


def _doc() -> dict:
    return yaml.safe_load(_yml())


def test_generated_yaml_parses() -> None:
    """The generated workflow is valid YAML with the three expected jobs."""
    assert set(_doc()["jobs"]) == {"build-smoke", "build", "release"}


def test_matrix_covers_all_six_targets() -> None:
    """The build matrix has macOS, Linux and Windows targets for both arches."""
    include = _doc()["jobs"]["build"]["strategy"]["matrix"]["include"]
    assert {e["target"] for e in include} == _ALL_TARGETS


def test_windows_targets_run_on_windows_and_get_exe() -> None:
    """Windows targets use windows-latest and the .exe suffix; others have none."""
    include = _doc()["jobs"]["build"]["strategy"]["matrix"]["include"]
    for e in include:
        if e["target"].endswith("pc-windows-msvc"):
            assert e["os"] == "windows-latest"
            assert e["exe"] == ".exe"
        else:
            assert e["exe"] == ""


def test_staging_names_assets_with_exe_suffix() -> None:
    """The staging step builds `<bin>-<target><exe>` assets and checksums them."""
    steps = _doc()["jobs"]["build"]["steps"]
    stage = next(s for s in steps if s["name"].startswith("Stage binary"))
    run = stage["run"]
    assert "my-test-plugin-${{ matrix.target }}${{ matrix.exe }}" in run
    assert "release/my-test-plugin${{ matrix.exe }}" in run
    assert ".sha256" in run
    # Windows Git-Bash has no guaranteed shasum: sha256sum must be preferred.
    assert "sha256sum" in run


def test_build_jobs_force_bash_shell() -> None:
    """build and build-smoke default to bash so the same script runs on Windows."""
    jobs = _doc()["jobs"]
    for name in ("build", "build-smoke"):
        assert jobs[name]["defaults"]["run"]["shell"] == "bash"


def test_release_job_checksums_every_asset() -> None:
    """SHA256SUMS is built by looping over every downloaded file (.exe included)."""
    steps = _doc()["jobs"]["release"]["steps"]
    combine = next(s for s in steps if "SHA256SUMS" in s["name"])
    assert "for f in *" in combine["run"]
    assert ">> SHA256SUMS" in combine["run"]


def test_build_smoke_matrix_covers_three_os_families() -> None:
    """build-smoke compiles on ubuntu, macos and windows on push/PR."""
    smoke = _doc()["jobs"]["build-smoke"]
    assert smoke["runs-on"] == "${{ matrix.os }}"
    assert set(smoke["strategy"]["matrix"]["os"]) == {
        "ubuntu-latest",
        "macos-latest",
        "windows-latest",
    }


def test_still_canonical_shape() -> None:
    """All four canonical invariants still hold for the generated file."""
    ok, missing = prof.is_binary_release_canonical_shape(_yml())
    assert ok, missing
    assert missing == []


def test_exactly_one_contents_write_job() -> None:
    """Least-privilege split: only `release` has contents: write."""
    jobs = _doc()["jobs"]
    writers = [n for n, j in jobs.items() if j.get("permissions", {}).get("contents") == "write"]
    assert writers == ["release"]


def test_every_uses_is_sha_pinned() -> None:
    """Every `uses:` pins a full 40-hex commit SHA."""
    refs = _USES_RE.findall(_yml())
    assert refs
    for ref in refs:
        assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", ref), ref
