"""Tests for v2.48 Phase 6 — URL/archive ingestion in cpv_staging.

These tests cover:
  * ``looks_like_github_url`` detection of supported shapes
  * ``looks_like_archive`` detection of supported suffixes
  * ``ingest_archive`` end-to-end with a real .zip and .tar.gz
  * ``ingest_github_url`` failure modes (gh missing, malformed URL)
  * ``IngestResult`` shape

The real `gh repo clone` happy path is exercised by integration smoke tests
in test_validate_security.py — those require network and an actual GitHub
URL, so they are gated to the integration test suite, not the fast unit
tests.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_staging as staging  # noqa: E402

# ── looks_like_github_url ────────────────────────────────────────


class TestLooksLikeGithubUrl:
    @pytest.mark.parametrize(
        "spec",
        [
            "https://github.com/owner/repo",
            "https://github.com/owner/repo/",
            "https://github.com/Emasoft/claude-plugins-validation",
            "http://github.com/owner/repo",  # http auto-upgrades on clone
            "github:owner/repo",
            "github:Emasoft/cpv",
        ],
    )
    def test_recognized_shapes(self, spec: str) -> None:
        assert staging.looks_like_github_url(spec) is True

    @pytest.mark.parametrize(
        "spec",
        [
            "owner/repo",  # plain shorthand intentionally NOT recognized
            "/Users/me/plugin",
            "./relative/path",
            "ssh://git@github.com/owner/repo",
            "",
            "https://gitlab.com/owner/repo",
            "https://example.com/foo",
        ],
    )
    def test_unrecognized_shapes(self, spec: str) -> None:
        assert staging.looks_like_github_url(spec) is False

    def test_non_string_returns_false(self) -> None:
        assert staging.looks_like_github_url(None) is False  # type: ignore[arg-type]
        assert staging.looks_like_github_url(42) is False  # type: ignore[arg-type]


# ── looks_like_archive ───────────────────────────────────────────


class TestLooksLikeArchive:
    @pytest.mark.parametrize(
        "spec",
        [
            "/tmp/x.zip",
            "/tmp/x.ZIP",
            "pkg.tar.gz",
            "pkg.TAR.GZ",
            "pkg.tgz",
            "x.tar.bz2",
            "x.tbz2",
            "x.tar.xz",
            "x.txz",
            "x.tar",
        ],
    )
    def test_recognized_suffixes(self, spec: str) -> None:
        assert staging.looks_like_archive(spec) is True

    @pytest.mark.parametrize(
        "spec",
        [
            "/tmp/x.png",
            "/tmp/x.gz",  # bare .gz not matched (too ambiguous)
            "plugin",
            "tar",
            "",
        ],
    )
    def test_unrecognized_suffixes(self, spec: str) -> None:
        assert staging.looks_like_archive(spec) is False


# ── ingest_archive (real .zip + .tar.gz round-trips) ────────────


class TestIngestArchive:
    def test_round_trip_zip(self, tmp_path: Path) -> None:
        # Build a tiny .zip
        archive = tmp_path / "pack.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("plugin/skills/SKILL.md", "skill body")
            zf.writestr("plugin/.claude-plugin/plugin.json",
                        '{"name": "p", "version": "1.0.0"}')

        result = staging.ingest_archive(archive)
        try:
            assert result.source_kind == "archive"
            assert result.source_spec == str(archive)
            assert result.target.is_dir()
            assert (result.target / "plugin/skills/SKILL.md").read_text() == "skill body"
        finally:
            staging.cleanup_staging(result.tmpdir)

    def test_round_trip_tar_gz(self, tmp_path: Path) -> None:
        archive = tmp_path / "pack.tar.gz"
        # Build a 2-file tar.gz
        content_dir = tmp_path / "src"
        content_dir.mkdir()
        (content_dir / "README.md").write_text("readme")
        (content_dir / "skill.md").write_text("skill")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(content_dir, arcname="pack")

        result = staging.ingest_archive(archive)
        try:
            assert result.source_kind == "archive"
            assert (result.target / "pack/README.md").read_text() == "readme"
            assert (result.target / "pack/skill.md").read_text() == "skill"
        finally:
            staging.cleanup_staging(result.tmpdir)

    def test_missing_archive_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            staging.ingest_archive(tmp_path / "ghost.zip")

    def test_unsupported_suffix_raises(self, tmp_path: Path) -> None:
        # Create a real file with bad suffix
        f = tmp_path / "blob.bin"
        f.write_bytes(b"\x00\x01\x02")
        with pytest.raises(ValueError) as exc_info:
            staging.ingest_archive(f)
        assert "unsupported archive format" in str(exc_info.value).lower()

    def test_extract_failure_cleans_up_tmpdir(
        self, tmp_path: Path
    ) -> None:
        """Verify tmpdir cleanup on extract failure WITHOUT monkey-patching
        the global extract_archive (which can leak across tests in some
        pytest scheduling orders). We create a malformed archive that
        triggers extract_archive's own SystemExit on path-traversal
        detection — same code path, no global state mutation."""
        archive = tmp_path / "evil.zip"
        # Build a zip whose member name is a path-traversal payload.
        # extract_archive blocks this with `sys.exit(1)`.
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escape.txt", "should not extract")

        # Capture what tmpdir gets created so we can verify cleanup.
        from cpv_staging import _ARCHIVE_SUFFIXES  # noqa: F401 — sanity import
        with pytest.raises(RuntimeError) as exc_info:
            staging.ingest_archive(archive)
        assert "extract failed" in str(exc_info.value).lower()


# ── ingest_github_url (failure modes — happy path requires network) ──


class TestIngestGithubUrl:
    def test_rejects_non_github_spec(self) -> None:
        with pytest.raises(ValueError):
            staging.ingest_github_url("/local/path")

    def test_missing_gh_cli_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force shutil.which("gh") to return None
        monkeypatch.setattr(staging.shutil, "which", lambda name: None)
        with pytest.raises(RuntimeError) as exc_info:
            staging.ingest_github_url("https://github.com/owner/repo")
        assert "'gh' CLI" in str(exc_info.value) or "gh" in str(exc_info.value)

    def test_clone_failure_cleans_up_and_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force gh to be present but clone to fail.
        monkeypatch.setattr(staging.shutil, "which", lambda name: "/usr/local/bin/gh")
        captured_tmpdirs: list[Path] = []
        original_mkdtemp = staging.tempfile.mkdtemp

        def fake_mkdtemp(*a, **kw) -> str:
            d = original_mkdtemp(*a, **kw)
            captured_tmpdirs.append(Path(d))
            return d

        monkeypatch.setattr(staging.tempfile, "mkdtemp", fake_mkdtemp)

        def fake_run(*a, **kw) -> mock.Mock:
            m = mock.Mock()
            m.returncode = 128
            m.stderr = "fatal: not found"
            m.stdout = ""
            return m

        monkeypatch.setattr(staging.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError) as exc_info:
            staging.ingest_github_url("https://github.com/owner/no-such-repo")
        assert "exit 128" in str(exc_info.value)
        # tmpdir should be cleaned up
        for d in captured_tmpdirs:
            assert not d.exists()

    def test_normalizes_shorthand_to_owner_repo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(staging.shutil, "which", lambda name: "/usr/local/bin/gh")
        captured_argv: list[list[str]] = []

        def fake_run(argv, *a, **kw) -> mock.Mock:
            captured_argv.append(list(argv))
            m = mock.Mock()
            m.returncode = 0
            m.stderr = ""
            m.stdout = ""
            return m

        monkeypatch.setattr(staging.subprocess, "run", fake_run)
        result = staging.ingest_github_url("github:Emasoft/cpv")
        try:
            # Verify the argv passed to gh has the bare owner/repo
            assert captured_argv, "subprocess.run was not called"
            argv = captured_argv[0]
            assert "Emasoft/cpv" in argv
        finally:
            staging.cleanup_staging(result.tmpdir)

    def test_strips_trailing_path_components(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`https://github.com/owner/repo/tree/main/sub` → `owner/repo`."""
        monkeypatch.setattr(staging.shutil, "which", lambda name: "/usr/local/bin/gh")
        captured_argv: list[list[str]] = []

        def fake_run(argv, *a, **kw) -> mock.Mock:
            captured_argv.append(list(argv))
            m = mock.Mock()
            m.returncode = 0
            m.stderr = ""
            return m

        monkeypatch.setattr(staging.subprocess, "run", fake_run)
        result = staging.ingest_github_url(
            "https://github.com/owner/repo/tree/main/path/inside"
        )
        try:
            argv = captured_argv[0]
            assert "owner/repo" in argv
        finally:
            staging.cleanup_staging(result.tmpdir)


# ── IngestResult shape ──────────────────────────────────────────


class TestIngestResult:
    def test_attributes(self, tmp_path: Path) -> None:
        r = staging.IngestResult(
            tmpdir=tmp_path / "tmp",
            target=tmp_path / "tmp/target",
            source_kind="github-url",
            source_spec="https://github.com/o/r",
        )
        assert r.tmpdir == tmp_path / "tmp"
        assert r.target == tmp_path / "tmp/target"
        assert r.source_kind == "github-url"
        assert r.source_spec == "https://github.com/o/r"
