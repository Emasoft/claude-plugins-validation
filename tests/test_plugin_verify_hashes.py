"""Tests for scripts/_plugin_verify_hashes.py — TRDD-bbff5bc5.

Pin the canonical reader's behavior:
- Dual-name URL fallback chain: new tag → old tag → new main → old main → cache
- Dual env-var read: PLUGIN_SKIP_GITHUB_INTEGRITY (new) → CPV_SKIP_GITHUB_INTEGRITY (old, with deprecation note)
- Forward-compat with v2 schema: reader handles BOTH `files` (v1) and `hashed_files` (v2)
- Atomic write of BOTH .plugin-self-hashes.json and .cpv-self-hashes.json by the writer

All network calls are stubbed via monkeypatch so tests never hit GitHub.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import _plugin_compute_hashes  # noqa: E402
import _plugin_verify_hashes  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────


def _reset_module_state():
    """Reset the verifier's per-process sentinels so tests are independent."""
    _plugin_verify_hashes._VERIFIED_THIS_PROCESS = False
    _plugin_verify_hashes._LEGACY_ENV_WARNED = False
    _plugin_verify_hashes._LEGACY_FILENAME_WARNED = False


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Per-test: reset module state, isolate cache dir, clear PYTEST escape hatch
    (we want the reader's actual behavior, not the auto-bypass)."""
    _reset_module_state()
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(_plugin_verify_hashes, "CACHE_DIR", cache_dir)
    # Clear both env vars + the auto-bypass test-mode signal so we exercise
    # the real fetch / skip / fallback code paths under test.
    monkeypatch.delenv("PLUGIN_SKIP_GITHUB_INTEGRITY", raising=False)
    monkeypatch.delenv("CPV_SKIP_GITHUB_INTEGRITY", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    yield
    _reset_module_state()


# ── env-var dual-read ────────────────────────────────────────────────────────


def test_env_var_new_name_honored(monkeypatch):
    """PLUGIN_SKIP_GITHUB_INTEGRITY=1 silently skips the gate (no deprecation)."""
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    assert _plugin_verify_hashes._read_skip_env_var() is True
    # Should NOT have set the legacy warned flag (only the legacy env var triggers it)
    assert _plugin_verify_hashes._LEGACY_ENV_WARNED is False


def test_env_var_legacy_name_honored_with_deprecation(monkeypatch, capsys):
    """CPV_SKIP_GITHUB_INTEGRITY=1 still works but prints a deprecation note once."""
    monkeypatch.setenv("CPV_SKIP_GITHUB_INTEGRITY", "1")
    assert _plugin_verify_hashes._read_skip_env_var() is True
    err = capsys.readouterr().err
    assert "DEPRECATED" in err
    assert "CPV_SKIP_GITHUB_INTEGRITY" in err
    assert "PLUGIN_SKIP_GITHUB_INTEGRITY" in err
    assert "TRDD-bbff5bc5" in err


def test_env_var_deprecation_note_emitted_only_once(monkeypatch, capsys):
    """The legacy-env deprecation note must fire AT MOST once per process."""
    monkeypatch.setenv("CPV_SKIP_GITHUB_INTEGRITY", "1")
    _plugin_verify_hashes._read_skip_env_var()
    _plugin_verify_hashes._read_skip_env_var()
    _plugin_verify_hashes._read_skip_env_var()
    err = capsys.readouterr().err
    # Count occurrences of the marker substring
    assert err.count("DEPRECATED") == 1


def test_env_var_both_set_new_wins_silently(monkeypatch, capsys):
    """When BOTH env vars are set, the new name takes precedence and the
    legacy deprecation note is NOT printed (we never even checked the
    legacy var)."""
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    monkeypatch.setenv("CPV_SKIP_GITHUB_INTEGRITY", "1")
    assert _plugin_verify_hashes._read_skip_env_var() is True
    err = capsys.readouterr().err
    assert "DEPRECATED" not in err


def test_env_var_neither_set_returns_false(monkeypatch):
    """No skip env var → reader proceeds with full GitHub fetch."""
    assert _plugin_verify_hashes._read_skip_env_var() is False


# ── dual-name URL fallback chain ─────────────────────────────────────────────


def test_dual_name_reader_prefers_new(monkeypatch):
    """Reader fetches `.plugin-self-hashes.json` first; uses it when available."""
    seen_urls: list[str] = []
    new_payload = {"version": 1, "files": {"a.py": "sha256:abc"}}

    def fake_fetch_one(url, version):
        seen_urls.append(url)
        if "plugin-self-hashes.json" in url:
            return new_payload
        return None

    monkeypatch.setattr(_plugin_verify_hashes, "_fetch_one", fake_fetch_one)
    result = _plugin_verify_hashes._fetch_github_manifest("1.2.3", prefer_cache=False)
    assert result == new_payload
    # Must have tried the NEW filename first.
    assert any("plugin-self-hashes.json" in u for u in seen_urls)
    # Must NOT have fallen through to the legacy URL after a successful new fetch.
    assert not any("cpv-self-hashes.json" in u for u in seen_urls)


def test_dual_name_reader_falls_back_to_legacy(monkeypatch, capsys):
    """When `.plugin-self-hashes.json` returns None (404), reader falls through
    to `.cpv-self-hashes.json` and prints a one-line compat note."""
    seen_urls: list[str] = []
    legacy_payload = {"version": 1, "files": {"a.py": "sha256:abc"}}

    def fake_fetch_one(url, version):
        seen_urls.append(url)
        if "cpv-self-hashes.json" in url:
            return legacy_payload
        return None  # NEW name 404s

    monkeypatch.setattr(_plugin_verify_hashes, "_fetch_one", fake_fetch_one)
    result = _plugin_verify_hashes._fetch_github_manifest("1.2.3", prefer_cache=False)
    assert result == legacy_payload
    # Tried both names.
    assert any("plugin-self-hashes.json" in u for u in seen_urls)
    assert any("cpv-self-hashes.json" in u for u in seen_urls)
    # Compat note printed.
    err = capsys.readouterr().err
    assert "legacy" in err.lower()
    assert "v2.53.0" in err


def test_legacy_compat_note_emitted_only_once(monkeypatch, capsys):
    """The legacy-filename compat note also dedupes per process."""

    def fake_fetch_one(url, version):
        return {"files": {}} if "cpv-self-hashes.json" in url else None

    monkeypatch.setattr(_plugin_verify_hashes, "_fetch_one", fake_fetch_one)
    _plugin_verify_hashes._fetch_github_manifest("1.0.0", prefer_cache=False)
    _plugin_verify_hashes._fetch_github_manifest("1.0.1", prefer_cache=False)
    err = capsys.readouterr().err
    # The "Fetched legacy" marker text fires exactly once across both calls.
    assert err.count("Fetched legacy") == 1


# ── schema forward-compat (v1 + v2) ──────────────────────────────────────────


def test_schema_v1_files_key_read(monkeypatch, tmp_path):
    """Reader handles v1 schema with top-level `files` dict."""
    plugin_root = _build_plugin_root(tmp_path)
    file_a = plugin_root / "scripts" / "validate_x.py"
    file_a.parent.mkdir(parents=True)
    file_a.write_text("# hello\n")
    digest = hashlib.sha256(file_a.read_bytes()).hexdigest()

    manifest = {"version": 1, "files": {"scripts/validate_x.py": f"sha256:{digest}"}}
    monkeypatch.setattr(
        _plugin_verify_hashes,
        "_fetch_github_manifest",
        lambda v, prefer_cache=True: manifest,
    )
    ok = _plugin_verify_hashes.verify_self_integrity(plugin_root=plugin_root, fail_on_mismatch=False, quiet=True)
    assert ok is True


def test_schema_v2_hashed_files_key_read(monkeypatch, tmp_path):
    """Reader handles v2 schema with top-level `hashed_files` dict (forward
    compat for when v2.53.0 ships v2)."""
    plugin_root = _build_plugin_root(tmp_path)
    file_a = plugin_root / "scripts" / "validate_x.py"
    file_a.parent.mkdir(parents=True)
    file_a.write_text("# hello\n")
    digest = hashlib.sha256(file_a.read_bytes()).hexdigest()

    manifest = {
        "format_version": 2,
        "hashed_files": {"scripts/validate_x.py": f"sha256:{digest}"},
    }
    monkeypatch.setattr(
        _plugin_verify_hashes,
        "_fetch_github_manifest",
        lambda v, prefer_cache=True: manifest,
    )
    ok = _plugin_verify_hashes.verify_self_integrity(plugin_root=plugin_root, fail_on_mismatch=False, quiet=True)
    assert ok is True


def test_schema_with_neither_key_warns_and_passes(monkeypatch, tmp_path, capsys):
    """A malformed manifest with neither `files` nor `hashed_files` → WARN + pass
    (graceful degradation, not a hard fail)."""
    plugin_root = _build_plugin_root(tmp_path)
    monkeypatch.setattr(
        _plugin_verify_hashes,
        "_fetch_github_manifest",
        lambda v, prefer_cache=True: {"format_version": 99},
    )
    ok = _plugin_verify_hashes.verify_self_integrity(plugin_root=plugin_root, fail_on_mismatch=False, quiet=False)
    # Neither key matched → files = {} → no mismatches → True.
    assert ok is True


# ── writer atomicity + dual-file output ──────────────────────────────────────


def test_writer_writes_both_filenames(tmp_path):
    """_plugin_compute_hashes.write_manifest writes BOTH the new and legacy
    filenames atomically with bytes-identical content."""
    plugin_root = _build_plugin_root(tmp_path)
    manifest = {
        "version": 1,
        "computed_at": "2026-05-03T00:00:00+00:00",
        "purpose": "test",
        "files": {"a.py": "sha256:abc"},
    }
    new_path, legacy_path = _plugin_compute_hashes.write_manifest(plugin_root, manifest)
    assert new_path.name == ".plugin-self-hashes.json"
    assert legacy_path.name == ".cpv-self-hashes.json"
    assert new_path.read_bytes() == legacy_path.read_bytes()
    # And no .tmp leftovers (atomic rename succeeded).
    assert not any(p.name.endswith(".tmp") for p in plugin_root.iterdir())


# ── ancillary helpers ───────────────────────────────────────────────────────


def _build_plugin_root(tmp_path: Path) -> Path:
    """Create a minimal plugin directory with .claude-plugin/plugin.json."""
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "test-plugin", "version": "1.2.3", "description": "x"}),
        encoding="utf-8",
    )
    return root


def _sha(p: Path) -> str:
    return f"sha256:{hashlib.sha256(p.read_bytes()).hexdigest()}"


# ── Change 3 (TRDD-b8c6d04f): bidirectional added-file detection ──────────────


class TestChange3AddedFileDetection:
    """An ADDED (inoculated) shipped file — present locally but absent from the
    canonical manifest — must be caught. The manifest→local loop alone cannot
    see it, because it only iterates entries that are ALREADY in the manifest.
    "Even one file skipped is enough to poison the plugin" (user directive)."""

    def test_added_file_detected_by_helper(self, tmp_path):
        """_detect_added_files flags a non-manifest .py and ignores OS/runtime cruft."""
        root = tmp_path / "plugin"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "good.py").write_text("x = 1\n")
        (root / "scripts" / "evil.py").write_text("import os  # injected\n")
        (root / ".DS_Store").write_bytes(b"\x00")  # cruft → must NOT flag
        (root / "scripts" / "__pycache__").mkdir()
        (root / "scripts" / "__pycache__" / "good.cpython-312.pyc").write_bytes(b"\x00")
        manifest_files = {"scripts/good.py": "sha256:deadbeef"}
        added = _plugin_verify_hashes._detect_added_files(root, manifest_files)
        assert added == ["scripts/evil.py"], f"expected only evil.py, got {added}"

    def test_verify_fails_on_added_file(self, monkeypatch, tmp_path, capsys):
        """End-to-end: an added file makes verify_self_integrity return False and
        the report names it as added/inoculated."""
        # pytest re-sets PYTEST_CURRENT_TEST for the call phase (after the
        # _isolate fixture's setup-time delenv), which would trip the auto-bypass
        # and short-circuit to True. Clear it here so real verify logic runs.
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        root = _build_plugin_root(tmp_path)
        covered = root / "scripts" / "validate_x.py"
        covered.parent.mkdir(parents=True)
        covered.write_text("# covered\n")
        # An UNTRACKED, unhashed payload dropped into the install.
        (root / "scripts" / "evil.py").write_text("import os  # inoculated\n")
        # Manifest covers EVERY legit shipped file (plugin.json + validate_x.py)
        # so evil.py is the ONLY uncovered one.
        manifest = {
            "version": 1,
            "files": {
                "scripts/validate_x.py": _sha(covered),
                ".claude-plugin/plugin.json": _sha(root / ".claude-plugin" / "plugin.json"),
            },
        }
        monkeypatch.setattr(_plugin_verify_hashes, "_fetch_github_manifest", lambda v, prefer_cache=True: manifest)
        ok = _plugin_verify_hashes.verify_self_integrity(plugin_root=root, fail_on_mismatch=False, quiet=True)
        assert ok is False, "verify must FAIL when an unhashed file is added to the install"
        err = capsys.readouterr().err
        assert "scripts/evil.py" in err
        assert "added/inoculated" in err

    def test_clean_install_passes(self, monkeypatch, tmp_path):
        """Two-sided: when the manifest covers exactly the on-disk shipped files,
        verify passes (no spurious added-file finding)."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # run real verify, not the auto-bypass
        root = _build_plugin_root(tmp_path)
        f = root / "scripts" / "validate_x.py"
        f.parent.mkdir(parents=True)
        f.write_text("# covered\n")
        manifest = {
            "version": 1,
            "files": {
                "scripts/validate_x.py": _sha(f),
                ".claude-plugin/plugin.json": _sha(root / ".claude-plugin" / "plugin.json"),
            },
        }
        monkeypatch.setattr(_plugin_verify_hashes, "_fetch_github_manifest", lambda v, prefer_cache=True: manifest)
        ok = _plugin_verify_hashes.verify_self_integrity(plugin_root=root, fail_on_mismatch=False, quiet=True)
        assert ok is True

    def test_cruft_not_flagged_as_added(self, monkeypatch, tmp_path):
        """OS/runtime cruft (.DS_Store, *.pyc, *~) is never flagged as added."""
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # run real verify, not the auto-bypass
        root = _build_plugin_root(tmp_path)
        f = root / "scripts" / "validate_x.py"
        f.parent.mkdir(parents=True)
        f.write_text("# covered\n")
        (root / ".DS_Store").write_bytes(b"\x00")
        (root / "scripts" / "validate_x.py~").write_text("backup\n")
        (root / "scripts" / "__pycache__").mkdir()
        (root / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        manifest = {
            "version": 1,
            "files": {
                "scripts/validate_x.py": _sha(f),
                ".claude-plugin/plugin.json": _sha(root / ".claude-plugin" / "plugin.json"),
            },
        }
        monkeypatch.setattr(_plugin_verify_hashes, "_fetch_github_manifest", lambda v, prefer_cache=True: manifest)
        ok = _plugin_verify_hashes.verify_self_integrity(plugin_root=root, fail_on_mismatch=False, quiet=True)
        assert ok is True, "cruft must not be flagged as an added file"

    def test_manifest_files_excluded_from_enumeration(self, tmp_path):
        """The two manifest files are never part of the shipped set (they cannot
        self-hash), so they are never flagged as added."""
        root = tmp_path / "plugin"
        root.mkdir()
        (root / ".plugin-self-hashes.json").write_text("{}")
        (root / ".cpv-self-hashes.json").write_text("{}")
        (root / "real.py").write_text("x=1\n")
        shipped = _plugin_compute_hashes.enumerate_shipped_files(root)
        assert ".plugin-self-hashes.json" not in shipped
        assert ".cpv-self-hashes.json" not in shipped
        assert "real.py" in shipped

    def test_builder_is_exhaustive_over_enumeration(self):
        """compute_manifest hashes EVERY enumerated shipped file that exists on
        disk (change 1 + change 3 consistency), and adds no extras. Run against
        the real repo — pins the security invariant that no shipped file is
        left unhashed."""
        repo = Path(__file__).resolve().parent.parent
        shipped = _plugin_compute_hashes.enumerate_shipped_files(repo)
        manifest = _plugin_compute_hashes.compute_manifest(repo)
        keys = set(manifest["files"].keys())
        assert keys <= shipped, f"manifest has keys not in the shipped set: {sorted(keys - shipped)[:5]}"
        missing = {rel for rel in shipped if (repo / rel).is_file() and rel not in keys}
        assert not missing, f"shipped files NOT hashed by compute_manifest: {sorted(missing)[:5]}"
