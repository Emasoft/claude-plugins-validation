"""Tests for hash-manifest format v2 — TRDD-9065109a Phase G.

The v2 format extends the existing v1 schema with:
  - `format`     — explicit format tag ("cpv-hash-manifest-v2")
  - `git`        — `tag`, `sha`, `remote` of the manifest's source commit
  - `submodules` — for submodule-bundle plugins, per-submodule `url`+`sha`

Backward compatibility:
  - The verifier accepts both v1 and v2 (no abrupt break).
  - The writer can produce v2 when given the optional git/submodule info,
    but emits v1 (current behaviour) when those signals are absent — so
    plugins that never adopt v2 see zero behavioural change.

The actual writer lives in `scripts/_plugin_compute_hashes.py` (legacy
filename `compute_cpv_self_hashes.py`). This test file targets a new
helper module `cpv_hash_manifest_v2` that owns the v2-specific schema
logic so the existing module stays untouched for plugins that don't opt in.
"""

from __future__ import annotations

import json
from pathlib import Path


def _import_module():
    """Defer the import so test discovery works in the TDD-red phase."""
    import cpv_hash_manifest_v2

    return cpv_hash_manifest_v2


# -----------------------------------------------------------------------------
# build_v2_manifest() — pure function: takes a v1 manifest dict + optional
# git/submodule info, returns a v2 manifest dict.
# -----------------------------------------------------------------------------


class TestBuildV2Manifest:
    def test_promotes_v1_fields(self):
        """The 'files' / 'computed_at' / 'purpose' carry across unchanged."""
        v1 = {
            "version": 1,
            "computed_at": "2026-05-01T12:00:00+00:00",
            "purpose": "Hash manifest of …",
            "files": {"a.py": "sha256:abc", "b.py": "sha256:def"},
        }
        mod = _import_module()
        v2 = mod.build_v2_manifest(v1)
        assert v2["files"] == v1["files"]
        assert v2["computed_at"] == v1["computed_at"]
        assert v2["purpose"] == v1["purpose"]

    def test_sets_version_and_format(self):
        v1 = {"version": 1, "files": {}, "computed_at": "x", "purpose": "y"}
        mod = _import_module()
        v2 = mod.build_v2_manifest(v1)
        assert v2["version"] == 2
        assert v2["format"] == "cpv-hash-manifest-v2"

    def test_adds_git_block_when_provided(self):
        v1 = {"version": 1, "files": {}, "computed_at": "x", "purpose": "y"}
        mod = _import_module()
        v2 = mod.build_v2_manifest(
            v1,
            git={
                "tag": "v2.50.2",
                "sha": "b6b88240aa72e9bd7f1c5e4d0a0c3f8e9b7c5a3d",
                "remote": "https://github.com/Emasoft/claude-plugins-validation.git",
            },
        )
        assert v2["git"]["tag"] == "v2.50.2"
        assert v2["git"]["sha"].startswith("b6b8824")
        assert v2["git"]["remote"].startswith("https://github.com/")

    def test_adds_submodules_block_when_provided(self):
        v1 = {"version": 1, "files": {}, "computed_at": "x", "purpose": "y"}
        mod = _import_module()
        v2 = mod.build_v2_manifest(
            v1,
            submodules={
                "external/lib-rust": {
                    "url": "https://github.com/example/lib-rust.git",
                    "sha": "abc123",
                    "purpose": "Source for the bundled binary at servers/lib-rust",
                }
            },
        )
        assert "external/lib-rust" in v2["submodules"]
        assert v2["submodules"]["external/lib-rust"]["sha"] == "abc123"

    def test_omits_optional_blocks_when_not_provided(self):
        """No git/submodule info → v2 manifest has only the required keys."""
        v1 = {"version": 1, "files": {}, "computed_at": "x", "purpose": "y"}
        mod = _import_module()
        v2 = mod.build_v2_manifest(v1)
        assert "git" not in v2
        assert "submodules" not in v2


# -----------------------------------------------------------------------------
# normalize_to_files_dict() — read either v1 or v2, return the inner files
# map. Verifier helper for backward compatibility.
# -----------------------------------------------------------------------------


class TestNormalizeToFilesDict:
    def test_reads_v1_files(self):
        v1 = {"version": 1, "files": {"x.py": "sha256:1"}}
        mod = _import_module()
        assert mod.normalize_to_files_dict(v1) == {"x.py": "sha256:1"}

    def test_reads_v2_files(self):
        v2 = {
            "version": 2,
            "format": "cpv-hash-manifest-v2",
            "files": {"y.py": "sha256:2"},
        }
        mod = _import_module()
        assert mod.normalize_to_files_dict(v2) == {"y.py": "sha256:2"}

    def test_unknown_version_raises(self):
        bad = {"version": 99, "files": {}}
        mod = _import_module()
        try:
            mod.normalize_to_files_dict(bad)
        except ValueError as e:
            assert "99" in str(e)
            return
        raise AssertionError("Expected ValueError for unknown manifest version")

    def test_missing_version_raises(self):
        bad: dict[str, object] = {"files": {}}
        mod = _import_module()
        try:
            mod.normalize_to_files_dict(bad)
        except (KeyError, ValueError):
            return
        raise AssertionError("Expected an exception for missing version field")


# -----------------------------------------------------------------------------
# detect_format_version() — utility for callers that want to branch on the
# manifest format without unpacking the whole structure first.
# -----------------------------------------------------------------------------


class TestDetectFormatVersion:
    def test_v1_returns_1(self):
        assert _import_module().detect_format_version({"version": 1, "files": {}}) == 1

    def test_v2_returns_2(self):
        assert _import_module().detect_format_version({"version": 2, "files": {}}) == 2

    def test_no_version_returns_none(self):
        """A bare {} (very malformed) → None instead of crash, so callers
        can branch on the result."""
        assert _import_module().detect_format_version({}) is None


# -----------------------------------------------------------------------------
# round_trip — write v2 to disk, read it back, get the same dict.
# -----------------------------------------------------------------------------


class TestRoundTrip:
    def test_write_and_read_v2(self, tmp_path: Path):
        v1 = {
            "version": 1,
            "computed_at": "2026-05-01T12:00:00+00:00",
            "purpose": "Hash manifest …",
            "files": {"a.py": "sha256:abc"},
        }
        mod = _import_module()
        v2 = mod.build_v2_manifest(
            v1,
            git={"tag": "v1.0.0", "sha": "deadbeef", "remote": "https://github.com/x/y.git"},
        )
        out = tmp_path / "manifest.json"
        out.write_text(json.dumps(v2, indent=2), encoding="utf-8")

        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["version"] == 2
        assert loaded["format"] == "cpv-hash-manifest-v2"
        assert loaded["git"]["sha"] == "deadbeef"
        # Files dict is preserved bytes-for-bytes.
        assert mod.normalize_to_files_dict(loaded) == v1["files"]
