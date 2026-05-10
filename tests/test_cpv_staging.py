"""Tests for cpv_staging — hardlink/copy/symlink staging trees.

These tests exercise the same-fs hardlink path (which is what every CPV
scan uses on a normal dev machine). The cross-fs fallback (EXDEV →
COPY/SYMLINK) is mocked rather than driven by a real cross-fs setup
because configuring two filesystems in CI is fragile and the fallback
logic is a small enough wrapper that mocking is sufficient.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_staging as staging  # noqa: E402

# ── stage_target happy path (hardlinks) ──────────────────────────


class TestStageTargetHardlink:
    def test_single_directory_hardlink_mode(self, tmp_path: Path) -> None:
        target = tmp_path / "plugin"
        target.mkdir()
        (target / "a.md").write_text("alpha")
        (target / "skills").mkdir()
        (target / "skills" / "SKILL.md").write_text("body")

        result = staging.stage_target(target)
        try:
            assert result.mode == staging.StageMode.HARDLINK
            assert result.target_in_stage.is_dir()
            assert (result.target_in_stage / "a.md").exists()
            assert (result.target_in_stage / "skills" / "SKILL.md").exists()
            assert result.files_staged == 2
            assert result.bytes_staged == len("alpha") + len("body")
            assert result.supports_deletion is True
        finally:
            staging.cleanup_staging(result.stage_root)

    def test_single_file_target(self, tmp_path: Path) -> None:
        f = tmp_path / "skill.md"
        f.write_text("content")
        result = staging.stage_target(f)
        try:
            assert result.target_in_stage.is_file()
            assert result.target_in_stage.read_text() == "content"
            assert result.files_staged == 1
        finally:
            staging.cleanup_staging(result.stage_root)

    def test_hardlink_shares_inode_with_source(self, tmp_path: Path) -> None:
        """Same-fs hardlinks must share the inode — proves the staging is
        zero-copy and that deletions in staging don't affect the cache."""
        target = tmp_path / "p"
        target.mkdir()
        f = target / "a.md"
        f.write_text("x" * 100)

        result = staging.stage_target(target)
        try:
            staged = result.target_in_stage / "a.md"
            assert staged.stat().st_ino == f.stat().st_ino
        finally:
            staging.cleanup_staging(result.stage_root)

    def test_deleting_staged_hardlink_preserves_source(self, tmp_path: Path) -> None:
        """Crucial safety property: deleting a hardlink in staging only
        decrements the inode's link count — the source file remains."""
        target = tmp_path / "p"
        target.mkdir()
        f = target / "a.md"
        f.write_text("payload")

        result = staging.stage_target(target)
        try:
            staged = result.target_in_stage / "a.md"
            staged.unlink()
            assert not staged.exists()
            assert f.exists()
            assert f.read_text() == "payload"
        finally:
            staging.cleanup_staging(result.stage_root)

    def test_stage_name_override(self, tmp_path: Path) -> None:
        target = tmp_path / "weird-name"
        target.mkdir()
        (target / "f").write_text("x")
        result = staging.stage_target(target, stage_name="canonical")
        try:
            assert result.target_in_stage.name == "canonical"
        finally:
            staging.cleanup_staging(result.stage_root)

    def test_missing_target_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            staging.stage_target(tmp_path / "ghost")

    def test_symlinks_preserved_as_symlinks(self, tmp_path: Path) -> None:
        target = tmp_path / "p"
        target.mkdir()
        (target / "real").write_text("body")
        (target / "link").symlink_to("real")

        result = staging.stage_target(target)
        try:
            staged_link = result.target_in_stage / "link"
            assert staged_link.is_symlink()
            assert os.readlink(staged_link) == "real"
        finally:
            staging.cleanup_staging(result.stage_root)


# ── Cross-fs fallback (mocked EXDEV) ─────────────────────────────


class TestStageTargetCrossFsFallback:
    def test_exdev_falls_back_to_copy_for_small_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "p"
        target.mkdir()
        (target / "a").write_text("x" * 50)

        # Make hardlink_tree raise EXDEV on the first call.
        call_count = {"n": 0}

        def fake_hardlink_tree(src: Path, dst: Path):
            call_count["n"] += 1
            err = OSError(18, "EXDEV cross-device link")
            err.errno = 18
            raise err

        monkeypatch.setattr(staging, "hardlink_tree", fake_hardlink_tree)
        result = staging.stage_target(target)
        try:
            assert result.mode == staging.StageMode.COPY
            assert (result.target_in_stage / "a").read_text() == "x" * 50
            assert result.supports_deletion is True
        finally:
            staging.cleanup_staging(result.stage_root)

    def test_exdev_falls_back_to_symlink_for_large_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "huge"
        target.mkdir()
        f = target / "big"
        f.write_text("x" * 100)

        # EXDEV the hardlink path AND fake the size to exceed the budget.
        def fake_hardlink_tree(src: Path, dst: Path):
            err = OSError(18, "EXDEV")
            err.errno = 18
            raise err

        def fake_measure(src: Path) -> int:
            return 500_000_000  # 500 MiB > 100 MiB budget

        monkeypatch.setattr(staging, "hardlink_tree", fake_hardlink_tree)
        monkeypatch.setattr(staging, "_measure_tree_bytes", fake_measure)
        result = staging.stage_target(target)
        try:
            assert result.mode == staging.StageMode.SYMLINK
            assert result.supports_deletion is False
            staged_link = result.target_in_stage / "big"
            assert staged_link.is_symlink()
        finally:
            staging.cleanup_staging(result.stage_root)

    def test_non_exdev_oserror_propagates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "p"
        target.mkdir()
        (target / "a").write_text("x")

        # Permission error (errno 13) is not cross-fs — must propagate.
        def fake_hardlink_tree(src: Path, dst: Path):
            err = OSError(13, "permission denied")
            err.errno = 13
            raise err

        monkeypatch.setattr(staging, "hardlink_tree", fake_hardlink_tree)
        with pytest.raises(OSError) as exc_info:
            staging.stage_target(target)
        assert exc_info.value.errno == 13


# ── cleanup_staging ────────────────────────────────────────────────


class TestCleanupStaging:
    def test_removes_tree(self, tmp_path: Path) -> None:
        stage = tmp_path / "stage"
        stage.mkdir()
        (stage / "a").write_text("x")
        staging.cleanup_staging(stage)
        assert not stage.exists()

    def test_idempotent_on_missing(self, tmp_path: Path) -> None:
        # Should not raise.
        staging.cleanup_staging(tmp_path / "ghost")

    def test_swallows_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Even if rmtree raises (despite ignore_errors), don't propagate.
        def boom(*a, **kw):
            raise RuntimeError("simulated")

        monkeypatch.setattr(staging.shutil, "rmtree", boom)
        # Should not raise.
        staging.cleanup_staging(Path("/tmp/anything"))

    def test_none_argument_is_safe(self) -> None:
        # Defensive: caller may pass None when stage_target failed.
        staging.cleanup_staging(None)  # type: ignore[arg-type]


# ── StageResult shape ────────────────────────────────────────────


class TestStageResult:
    def test_supports_deletion_property(self) -> None:
        r1 = staging.StageResult(
            stage_root=Path("/tmp/stage"),
            target_in_stage=Path("/tmp/stage/p"),
            mode=staging.StageMode.HARDLINK,
        )
        assert r1.supports_deletion is True

        r2 = staging.StageResult(
            stage_root=Path("/tmp/stage"),
            target_in_stage=Path("/tmp/stage/p"),
            mode=staging.StageMode.COPY,
        )
        assert r2.supports_deletion is True

        r3 = staging.StageResult(
            stage_root=Path("/tmp/stage"),
            target_in_stage=Path("/tmp/stage/p"),
            mode=staging.StageMode.SYMLINK,
        )
        assert r3.supports_deletion is False

    def test_skipped_reasons_default_empty(self) -> None:
        r = staging.StageResult(
            stage_root=Path("/x"),
            target_in_stage=Path("/x/y"),
            mode=staging.StageMode.HARDLINK,
        )
        assert r.skipped_reasons == []
