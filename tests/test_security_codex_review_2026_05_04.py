"""Regression tests for the three Codex adversarial-review findings of 2026-05-04.

Each test asserts the FIX, not just the surface behavior — if a future
edit silently weakens any of these defenses the test fails fast.

Findings covered:
  #1 (HIGH)   Symlink-following in GitignoreFilter (gitignore_filter.py)
  #2 (HIGH)   Blanket integrity-bypass in publish.run (publish.py)
  #3 (MEDIUM) Archive extraction had no resource limits (cpv_management_common.py)
"""

from __future__ import annotations

import io
import os
import struct
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# Make scripts/ importable
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

import cpv_management_common as mgmt  # noqa: E402
import gitignore_filter  # noqa: E402
import publish  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Finding #1 — Symlinks must NOT be followed by GitignoreFilter
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def plugin_with_symlink(tmp_path: Path) -> tuple[Path, Path]:
    """A plugin tree containing one regular file plus one symlink that
    escapes the plugin root by pointing at a host file outside it.
    """
    # Host secret OUTSIDE the plugin root
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    host_secret = host_dir / "secret.env"
    host_secret.write_text("AWS_SECRET=hunter2\n", encoding="utf-8")

    plugin_root = tmp_path / "evil-plugin"
    plugin_root.mkdir()
    (plugin_root / "regular.py").write_text("print('ok')\n", encoding="utf-8")
    (plugin_root / "escape").symlink_to(host_secret)

    return plugin_root, host_secret


def test_finding1_walk_skips_symlinks_by_default(
    plugin_with_symlink: tuple[Path, Path],
) -> None:
    """The default walker must NOT yield the symlink that escapes the root."""
    plugin_root, _ = plugin_with_symlink
    gi = gitignore_filter.GitignoreFilter(plugin_root)
    files: list[str] = []
    for _dirpath, _subdirs, filenames in gi.walk():
        files.extend(filenames)
    assert "regular.py" in files
    assert "escape" not in files, "GitignoreFilter must reject symlinks by default — finding #1 regression."


def test_finding1_iterdir_skips_symlinks_by_default(
    plugin_with_symlink: tuple[Path, Path],
) -> None:
    """iterdir must also reject symlinks by default."""
    plugin_root, _ = plugin_with_symlink
    gi = gitignore_filter.GitignoreFilter(plugin_root)
    names = {p.name for p in gi.iterdir()}
    assert "regular.py" in names
    assert "escape" not in names


def test_finding1_rglob_skips_symlinks_by_default(
    plugin_with_symlink: tuple[Path, Path],
) -> None:
    """rglob must also reject symlinks by default."""
    plugin_root, _ = plugin_with_symlink
    gi = gitignore_filter.GitignoreFilter(plugin_root)
    names = {p.name for p in gi.rglob("*")}
    assert "regular.py" in names
    assert "escape" not in names


def test_finding1_follow_symlinks_only_when_target_inside_root(tmp_path: Path) -> None:
    """opt-in follow_symlinks=True must STILL reject symlinks whose
    resolved target leaves the plugin root."""
    plugin_root = tmp_path / "p"
    plugin_root.mkdir()
    inside_target = plugin_root / "real.py"
    inside_target.write_text("x", encoding="utf-8")
    outside_target = tmp_path / "outside.py"
    outside_target.write_text("y", encoding="utf-8")

    (plugin_root / "good_link").symlink_to(inside_target)
    (plugin_root / "bad_link").symlink_to(outside_target)

    gi = gitignore_filter.GitignoreFilter(plugin_root, follow_symlinks=True)
    names = {p.name for p in gi.iterdir()}
    assert "real.py" in names
    assert "good_link" in names, "in-root symlink should be allowed under opt-in"
    assert "bad_link" not in names, "escape-the-root symlink must still be refused"


def test_finding1_broken_symlink_is_unsafe_under_opt_in(tmp_path: Path) -> None:
    """Broken symlinks fail-closed even under follow_symlinks=True."""
    plugin_root = tmp_path / "p"
    plugin_root.mkdir()
    (plugin_root / "broken").symlink_to(plugin_root / "does_not_exist")

    gi = gitignore_filter.GitignoreFilter(plugin_root, follow_symlinks=True)
    names = {p.name for p in gi.iterdir()}
    assert "broken" not in names


# ─────────────────────────────────────────────────────────────────────────────
# Finding #2 — publish.run must NOT inject the integrity bypass
# ─────────────────────────────────────────────────────────────────────────────


def test_finding2_publish_run_does_not_inject_bypass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """publish.run() must run subprocesses with plain os.environ — no
    integrity bypass injected. That helper is for non-validator commands
    (git, gh, lint). The bypass lives in run_with_integrity_bypass()."""
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], cwd: Path, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    publish.run(["echo", "hi"], cwd=tmp_path)

    # env=None means subprocess inherits os.environ unchanged
    assert captured["env"] is None, "publish.run() must not inject env overrides — finding #2 regression."


def test_finding2_publish_run_with_integrity_bypass_does_inject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dedicated wrapper SHOULD inject the bypass — that is its sole job."""
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], cwd: Path, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    publish.run_with_integrity_bypass(["echo", "hi"], cwd=tmp_path)

    env = captured["env"]
    assert env is not None
    assert env.get("PLUGIN_SKIP_GITHUB_INTEGRITY") == "1"
    # Legacy alias kept for one release per TRDD-bbff5bc5
    assert env.get("CPV_SKIP_GITHUB_INTEGRITY") == "1"


def test_finding2_validate_plugin_uses_bypass_helper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Gate 4 (validate_plugin) MUST go through the bypass wrapper, not plain run().

    Since the bypass wrapper is implemented as a thin call into run(), monkey-
    patching `publish.run` lets us assert (a) the validator got called AND
    (b) the call carried the bypass env. The previous implementation called
    `publish.run` directly without env, which is the regression we're guarding."""
    plugin_root = tmp_path / "p"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text('{"name":"x","version":"1.0.0"}', encoding="utf-8")
    (plugin_root / "scripts").mkdir()

    captured: dict[str, Any] = {}

    def fake_run(
        cmd: list[str], cwd: Path, *, check: bool = True, env: Any = None, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(publish, "run", fake_run)
    publish.stage_validate_plugin(plugin_root)

    assert any("validate_plugin.py" in part for part in captured["cmd"])
    env = captured["env"]
    assert env is not None, "Gate 4 must call run_with_integrity_bypass (which sets env) — finding #2 regression."
    assert env.get("PLUGIN_SKIP_GITHUB_INTEGRITY") == "1"


# ─────────────────────────────────────────────────────────────────────────────
# Finding #3 — Archive extraction must enforce quotas
# ─────────────────────────────────────────────────────────────────────────────


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _write_tar(path: Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, fileobj=io.BytesIO(data))


def test_finding3_zip_within_limits_extracts_cleanly(tmp_path: Path) -> None:
    """A normal-size zip (well under every default) must extract without complaint."""
    src = tmp_path / "ok.zip"
    _write_zip(src, {"a.txt": b"hello", "sub/b.txt": b"world"})
    dest = tmp_path / "out"
    mgmt.extract_archive(str(src), dest)
    assert (dest / "a.txt").read_text() == "hello"
    assert (dest / "sub" / "b.txt").read_text() == "world"


def test_finding3_zip_too_many_entries_aborts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Zip with > max_entries must abort and clean up partial extraction."""
    monkeypatch.setenv("CPV_ARCHIVE_MAX_ENTRIES", "5")
    src = tmp_path / "many.zip"
    _write_zip(src, {f"f{i}.txt": b"x" for i in range(20)})
    dest = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        mgmt.extract_archive(str(src), dest)
    assert exc.value.code != 0
    assert not dest.exists() or not any(dest.iterdir()), "Aborted extraction must clean up the dest directory."


def test_finding3_zip_compression_ratio_bomb_aborts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A zip whose decompressed/compressed ratio exceeds the limit aborts.

    1 MB of zeros compresses to ≈1 KB → ratio ≈ 1024x. With max_ratio=10
    the gate must trip.
    """
    monkeypatch.setenv("CPV_ARCHIVE_MAX_RATIO", "10")
    src = tmp_path / "bomb.zip"
    _write_zip(src, {"zeros.bin": b"\x00" * (1024 * 1024)})
    dest = tmp_path / "out"
    with pytest.raises(SystemExit):
        mgmt.extract_archive(str(src), dest)


def test_finding3_zip_per_file_size_aborts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A single oversized entry trips max_per_file_bytes."""
    monkeypatch.setenv("CPV_ARCHIVE_MAX_PER_FILE_BYTES", "1024")
    monkeypatch.setenv("CPV_ARCHIVE_MAX_RATIO", "1000000")  # disable ratio check
    src = tmp_path / "big.zip"
    _write_zip(src, {"big.bin": b"A" * 4096})
    dest = tmp_path / "out"
    with pytest.raises(SystemExit):
        mgmt.extract_archive(str(src), dest)


def test_finding3_zip_nesting_depth_aborts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A deeply-nested entry trips max_nesting."""
    monkeypatch.setenv("CPV_ARCHIVE_MAX_NESTING", "3")
    src = tmp_path / "deep.zip"
    deep = "a/b/c/d/e/f/file.txt"
    _write_zip(src, {deep: b"x"})
    dest = tmp_path / "out"
    with pytest.raises(SystemExit):
        mgmt.extract_archive(str(src), dest)


def test_finding3_tar_too_many_entries_aborts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CPV_ARCHIVE_MAX_ENTRIES", "3")
    src = tmp_path / "many.tar.gz"
    _write_tar(src, {f"f{i}.txt": b"x" for i in range(10)})
    dest = tmp_path / "out"
    with pytest.raises(SystemExit):
        mgmt.extract_archive(str(src), dest)


def test_finding3_tar_compression_ratio_bomb_aborts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CPV_ARCHIVE_MAX_RATIO", "10")
    src = tmp_path / "bomb.tar.gz"
    _write_tar(src, {"zeros.bin": b"\x00" * (1024 * 1024)})
    dest = tmp_path / "out"
    with pytest.raises(SystemExit):
        mgmt.extract_archive(str(src), dest)


def test_finding3_default_limits_are_llm_friendly() -> None:
    """Sanity check on the documented defaults — if these get tightened
    accidentally, real LLM-dev workloads would start to fail. Bumping
    these is fine; tightening below the floor is what we're guarding against.
    """
    limits = mgmt._archive_limits()
    assert limits["max_bytes"] >= 100 * 1024**3, "max_bytes < 100 GB would block legitimate 70B-param checkpoints"
    assert limits["max_per_file_bytes"] >= 10 * 1024**3, (
        "max_per_file_bytes < 10 GB would block multi-shard safetensors files"
    )
    assert limits["max_entries"] >= 50_000, "max_entries < 50k would block typical HuggingFace datasets"
    assert limits["max_ratio"] >= 50, "max_ratio < 50 would false-positive on highly-compressible source code"


def test_finding3_env_var_override_increases_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user with non-standard workloads can crank the limits up via env var."""
    monkeypatch.setenv("CPV_ARCHIVE_MAX_BYTES", str(500 * 1024**3))
    limits = mgmt._archive_limits()
    assert limits["max_bytes"] == 500 * 1024**3


def test_finding3_invalid_env_var_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garbage env values fall back to the default — never silently disable a quota."""
    monkeypatch.setenv("CPV_ARCHIVE_MAX_ENTRIES", "not-a-number")
    limits = mgmt._archive_limits()
    assert limits["max_entries"] == mgmt.DEFAULT_ARCHIVE_MAX_ENTRIES

    monkeypatch.setenv("CPV_ARCHIVE_MAX_ENTRIES", "-5")  # nonsense → default
    limits = mgmt._archive_limits()
    assert limits["max_entries"] == mgmt.DEFAULT_ARCHIVE_MAX_ENTRIES


# Silence unused-import lint
_ = (mock, struct, os)
