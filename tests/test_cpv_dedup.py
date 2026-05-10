"""Tests for the fclones-backed dedup pipeline.

The tests cover three layers:
  1. Pure parsing (``parse_dedup_groups``) — accepts modern + legacy + flat
     fclones JSON shapes, sorts members lexicographically, drops singletons.
  2. apply_dedup — deletes only non-canonical members; honors dry-run;
     tolerates missing/permission-denied files.
  3. Integration (``run_fclones``) — end-to-end with the real fclones
     binary on a tmp tree with intentional duplicates. Skipped at module
     scope when fclones isn't installed (so CI without fclones still runs
     the parsing tests).

Bucket helper (``bucket_canonical_to_members``) is tested by passing it
hand-crafted dedup_maps; no fclones needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_dedup as dedup  # noqa: E402


def _has_fclones() -> bool:
    return dedup.is_fclones_available()


fclones_only = pytest.mark.skipif(
    not _has_fclones(),
    reason="fclones binary not available — skipping integration tests",
)


# ── parse_dedup_groups (pure JSON parsing) ─────────────────────────


class TestParseDedupGroups:
    def test_modern_format_with_files_objects(self) -> None:
        payload = {
            "groups": [
                {
                    "file_len": 100,
                    "file_hash": "deadbeef",
                    "files": [
                        {"path": "/stage/c/SKILL.md"},
                        {"path": "/stage/a/SKILL.md"},
                        {"path": "/stage/b/SKILL.md"},
                    ],
                },
                {
                    "file_len": 50,
                    "file_hash": "cafebabe",
                    "files": [{"path": "/stage/lonely.txt"}],  # singleton; should drop
                },
            ]
        }
        result = dedup.parse_dedup_groups(payload)
        assert len(result) == 1
        # Members are sorted; canonical = first lexicographically
        canonical = Path("/stage/a/SKILL.md")
        assert canonical in result
        assert result[canonical] == [
            Path("/stage/a/SKILL.md"),
            Path("/stage/b/SKILL.md"),
            Path("/stage/c/SKILL.md"),
        ]

    def test_modern_format_with_string_paths(self) -> None:
        # Some fclones builds emit paths as bare strings instead of {"path": ...}
        payload = {
            "groups": [
                {"files": ["/stage/x", "/stage/y"]},
            ]
        }
        result = dedup.parse_dedup_groups(payload)
        assert result == {Path("/stage/x"): [Path("/stage/x"), Path("/stage/y")]}

    def test_array_of_arrays_legacy(self) -> None:
        payload = [
            ["/stage/b", "/stage/a"],
            ["/stage/lonely"],  # singleton → drop
        ]
        result = dedup.parse_dedup_groups(payload)
        assert result == {Path("/stage/a"): [Path("/stage/a"), Path("/stage/b")]}

    def test_single_group_flat_shape(self) -> None:
        payload = {"files": [{"path": "/stage/x"}, {"path": "/stage/y"}]}
        result = dedup.parse_dedup_groups(payload)
        assert result == {Path("/stage/x"): [Path("/stage/x"), Path("/stage/y")]}

    def test_empty_payload(self) -> None:
        assert dedup.parse_dedup_groups({}) == {}
        assert dedup.parse_dedup_groups({"groups": []}) == {}
        assert dedup.parse_dedup_groups([]) == {}

    def test_singletons_dropped(self) -> None:
        payload = {"groups": [{"files": [{"path": "/x"}]}]}
        assert dedup.parse_dedup_groups(payload) == {}

    def test_canonical_is_lexicographic_first(self) -> None:
        """Determinism: re-running fclones on the same tree must produce
        identical bucketing. The canonical choice is sorted-by-path."""
        payload = {
            "groups": [
                {
                    "files": [
                        {"path": "/z/file"},
                        {"path": "/a/file"},
                        {"path": "/m/file"},
                    ]
                }
            ]
        }
        result = dedup.parse_dedup_groups(payload)
        assert list(result.keys()) == [Path("/a/file")]
        assert result[Path("/a/file")][0] == Path("/a/file")


# ── apply_dedup (file deletion) ────────────────────────────────────


class TestApplyDedup:
    def test_deletes_only_non_canonical(self, tmp_path: Path) -> None:
        canonical = tmp_path / "canonical"
        dup1 = tmp_path / "dup1"
        dup2 = tmp_path / "dup2"
        for p in (canonical, dup1, dup2):
            p.write_text("same content")

        dedup_map = {canonical: [canonical, dup1, dup2]}
        files_removed, _ = dedup.apply_dedup(dedup_map)
        assert files_removed == 2
        assert canonical.exists()
        assert not dup1.exists()
        assert not dup2.exists()

    def test_dry_run_deletes_nothing(self, tmp_path: Path) -> None:
        canonical = tmp_path / "canonical"
        dup = tmp_path / "dup"
        canonical.write_text("x")
        dup.write_text("x")

        files_removed, bytes_saved = dedup.apply_dedup({canonical: [canonical, dup]}, dry_run=True)
        assert files_removed == 1
        assert bytes_saved == 1  # "x" is 1 byte
        # Both files still on disk
        assert canonical.exists()
        assert dup.exists()

    def test_missing_files_silently_ignored(self, tmp_path: Path) -> None:
        canonical = tmp_path / "canonical"
        canonical.write_text("x")
        # dup has a path that doesn't exist
        dup = tmp_path / "ghost"
        files_removed, _ = dedup.apply_dedup({canonical: [canonical, dup]})
        # Best-effort semantics: missing duplicate isn't a fatal error
        assert files_removed == 0
        assert canonical.exists()

    def test_empty_dedup_map(self) -> None:
        assert dedup.apply_dedup({}) == (0, 0)

    def test_bytes_saved_is_sum_of_file_sizes(self, tmp_path: Path) -> None:
        canonical = tmp_path / "canonical"
        dup1 = tmp_path / "dup1"
        dup2 = tmp_path / "dup2"
        canonical.write_text("a" * 10)
        dup1.write_text("b" * 30)  # different bytes; size for accounting only
        dup2.write_text("c" * 20)

        _, bytes_saved = dedup.apply_dedup({canonical: [canonical, dup1, dup2]})
        assert bytes_saved == 50  # 30 + 20


# ── bucket_canonical_to_members ────────────────────────────────────


class TestBucketCanonicalToMembers:
    def test_canonical_with_duplicates_expands(self) -> None:
        canonical = Path("/stage/canonical")
        dup = Path("/stage/dup")
        dedup_map = {canonical: [canonical, dup]}

        result = dedup.bucket_canonical_to_members([canonical], dedup_map)
        assert result == {canonical: [canonical, dup]}

    def test_non_canonical_path_passes_through(self) -> None:
        unrelated = Path("/stage/somewhere/else.md")
        result = dedup.bucket_canonical_to_members([unrelated], {})
        assert result == {unrelated: [unrelated]}

    def test_mixed_inputs(self) -> None:
        canonical = Path("/c")
        dup = Path("/d")
        unrelated = Path("/u")
        dedup_map = {canonical: [canonical, dup]}

        result = dedup.bucket_canonical_to_members([canonical, unrelated], dedup_map)
        assert result[canonical] == [canonical, dup]
        assert result[unrelated] == [unrelated]


# ── is_fclones_available ─────────────────────────────────────────


class TestIsFclonesAvailable:
    def test_returns_bool(self) -> None:
        # Whatever the answer is on this system, it must be a bool.
        assert isinstance(dedup.is_fclones_available(), bool)


# ── Integration: real fclones on tmp tree ──────────────────────────


@fclones_only
class TestRunFclonesIntegration:
    def test_finds_known_duplicates(self, tmp_path: Path) -> None:
        # Build 3 identical files + 1 unique file
        same = "shared body\n"
        (tmp_path / "a").write_text(same)
        (tmp_path / "b").write_text(same)
        (tmp_path / "c").write_text(same)
        (tmp_path / "unique").write_text("not the same")

        result = dedup.run_fclones(tmp_path)
        assert result.attempted is True
        assert result.succeeded is True
        # Exactly one duplicate group of 3 members
        assert len(result.dedup_map) == 1
        members = list(result.dedup_map.values())[0]
        assert len(members) == 3
        assert all((tmp_path / m.name).exists() for m in members)

    def test_no_duplicates_returns_empty_map(self, tmp_path: Path) -> None:
        (tmp_path / "a").write_text("alpha")
        (tmp_path / "b").write_text("beta")
        (tmp_path / "c").write_text("gamma")
        result = dedup.run_fclones(tmp_path)
        assert result.attempted is True
        assert result.succeeded is True
        assert result.dedup_map == {}

    def test_missing_stage_root(self, tmp_path: Path) -> None:
        ghost = tmp_path / "does_not_exist"
        result = dedup.run_fclones(ghost)
        assert result.attempted is False
        assert "is not a directory" in result.skipped_reason

    def test_apply_dedup_deletes_disk_files(self, tmp_path: Path) -> None:
        same = "same"
        for n in ("p1.md", "p2.md", "p3.md"):
            (tmp_path / n).write_text(same)
        result = dedup.run_fclones(tmp_path)
        assert result.dedup_map  # at least one group

        files_removed, bytes_saved = dedup.apply_dedup(result.dedup_map)
        assert files_removed >= 2
        assert bytes_saved >= len(same) * 2
        # The canonical (lowest path) survives
        canonical = list(result.dedup_map.keys())[0]
        assert canonical.exists()


# ── Skipped path: fclones missing ──────────────────────────────────


class TestRunFclonesMissing:
    def test_skipped_reason_when_fclones_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dedup, "is_fclones_available", lambda: False)
        result = dedup.run_fclones(Path("/tmp"))
        assert result.attempted is False
        assert result.succeeded is False
        assert "fclones" in result.skipped_reason.lower()

    def test_invocation_failure_recorded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(dedup, "is_fclones_available", lambda: True)

        def fake_run(*a, **kw) -> object:
            class M:
                returncode = 7
                stdout = ""
                stderr = "fclones boom"

            return M()

        monkeypatch.setattr(dedup.subprocess, "run", fake_run)
        result = dedup.run_fclones(tmp_path)
        assert result.attempted is True
        assert result.succeeded is False
        assert "exited 7" in result.skipped_reason
        assert "boom" in result.skipped_reason

    def test_invalid_json_recorded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(dedup, "is_fclones_available", lambda: True)

        class M:
            returncode = 0
            stdout = "this is not json {"
            stderr = ""

        monkeypatch.setattr(dedup.subprocess, "run", lambda *a, **kw: M())
        result = dedup.run_fclones(tmp_path)
        assert result.attempted is True
        assert result.succeeded is False
        assert "JSON parse failed" in result.skipped_reason

    def test_timeout_recorded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(dedup, "is_fclones_available", lambda: True)

        def fake_run(*a, **kw):
            raise dedup.subprocess.TimeoutExpired(cmd="fclones", timeout=1)

        monkeypatch.setattr(dedup.subprocess, "run", fake_run)
        result = dedup.run_fclones(tmp_path)
        assert result.attempted is True
        assert result.succeeded is False
        assert "TimeoutExpired" in result.skipped_reason


# ── DedupResult dataclass shape ────────────────────────────────────


class TestDedupResultShape:
    def test_default_construction(self) -> None:
        r = dedup.DedupResult()
        assert r.attempted is False
        assert r.succeeded is False
        assert r.dedup_map == {}
        assert r.files_removed == 0
        assert r.bytes_saved == 0
        assert r.fclones_elapsed_seconds == 0.0
        assert r.skipped_reason == ""

    def test_explicit_fields(self) -> None:
        r = dedup.DedupResult(
            attempted=True,
            succeeded=True,
            dedup_map={Path("/x"): [Path("/x"), Path("/y")]},
            files_removed=1,
            bytes_saved=10,
            fclones_elapsed_seconds=0.5,
        )
        assert r.attempted is True
        assert r.files_removed == 1


# ── JSON dumping/loading round-trip used in integration ──────────


class TestParseRealisticFclonesOutput:
    """Exercise the parser against a literal fclones-style JSON blob."""

    def test_realistic_output(self) -> None:
        # This is the actual shape fclones 0.34+ emits for `--format json`.
        blob = json.dumps(
            {
                "header": {
                    "command": "fclones group /tmp/test --format json",
                    "version": "0.34.0",
                    "timestamp": "2026-05-01T12:34:56+0000",
                },
                "groups": [
                    {
                        "file_len": 12,
                        "file_hash": "abcd1234",
                        "files": [
                            {"path": "/tmp/test/a.md", "len": 12},
                            {"path": "/tmp/test/b.md", "len": 12},
                        ],
                    },
                ],
            }
        )
        result = dedup.parse_dedup_groups(json.loads(blob))
        assert len(result) == 1
        members = list(result.values())[0]
        assert sorted(p.name for p in members) == ["a.md", "b.md"]
