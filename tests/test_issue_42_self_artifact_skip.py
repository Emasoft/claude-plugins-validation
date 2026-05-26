#!/usr/bin/env python3
"""Regression lock for issue #42: skillaudit must NOT self-match against
plugins that ship byte-identical copies of CPV's own scanner artifacts.

Bug (v2.103.3 / pre-fix): a plugin bundling CPV's pattern catalog +
scanner sources for offline auditor packaging (e.g.,
``ai-maestro-visual-communicator-plugin@1.3.5``) produced **262**
``skillaudit:*`` findings against the bundled files alone — because the
scanner's own ~490 detection patterns matched its own pattern catalog +
context-classifier sources (catalog literally CONTAINS the strings
``/etc/passwd``, ``eval(``, ``curl … | sh`` as DESCRIPTIONS of malice).
Result: every plugin shipping the offline auditor had to carry a 30+
entry ``_intentional_validator_false_positives`` allowlist purely to
silence CPV scanning its own data.

Fix: hash-anchored basename skip in ``_iter_scannable_files``. A file
whose basename matches a known CPV scanner artifact AND whose SHA256
matches CPV's installed manifest entry for that basename is silently
skipped (it's an unmodified copy — scanning it just self-matches).

CRITICAL security gate: the skip is hash-anchored, NOT name-only. A
malicious plugin that NAMES a payload ``skillaudit_patterns.json`` but
ships different bytes does NOT get the skip — it falls through and is
scanned normally. The two-sided tests below pin BOTH outcomes.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_skillaudit_native as csn  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CPV_CATALOG = REPO_ROOT / "scripts" / "rules" / "skillaudit_patterns.json"
CPV_SCANNER = REPO_ROOT / "scripts" / "cpv_skillaudit_native.py"


def _reset_hash_cache() -> None:
    """Clear the lazy-loaded hash cache between tests so each one gets
    a fresh load (some tests deliberately tweak the install layout)."""
    csn._CPV_INSTALL_ARTIFACT_HASHES = None


def _make_target_plugin(tmp_path: Path, name: str = "downstream-plugin") -> Path:
    """Create a minimal plugin tree under tmp_path that the scanner will
    walk (needs at least one file with a scanned extension to iterate)."""
    plugin = tmp_path / name
    (plugin / "scripts").mkdir(parents=True)
    return plugin


class TestArtifactBasenamesAllowlist:
    """The allowlist must cover the artifacts that actually exist in the
    CPV install (so the hash check is reachable for real bundled copies)."""

    def test_catalog_basename_is_allowlisted(self) -> None:
        assert "skillaudit_patterns.json" in csn._SELF_ARTIFACT_BASENAMES

    def test_scanner_module_basename_is_allowlisted(self) -> None:
        assert "cpv_skillaudit_native.py" in csn._SELF_ARTIFACT_BASENAMES

    def test_all_context_classifiers_are_allowlisted(self) -> None:
        for stem in ("python", "json", "yaml", "markdown", "typescript"):
            assert f"_skillaudit_{stem}_context.py" in csn._SELF_ARTIFACT_BASENAMES


class TestHashLoaderResilience:
    """The lazy hash loader must never crash the scanner — a missing or
    malformed manifest just returns an empty dict (safe fallback)."""

    def test_returns_dict_in_normal_install(self) -> None:
        _reset_hash_cache()
        hashes = csn._load_cpv_install_artifact_hashes()
        # CPV's own install ships the manifest, so we expect entries.
        assert isinstance(hashes, dict)
        assert "skillaudit_patterns.json" in hashes

    def test_basename_keys_only_no_paths(self) -> None:
        """The returned map is keyed by BASENAME, not relative path —
        the consumer compares ``p.name`` against the map keys."""
        _reset_hash_cache()
        for key in csn._load_cpv_install_artifact_hashes():
            assert "/" not in key, f"key {key!r} leaked a path separator"

    def test_hash_values_are_hex_sha256(self) -> None:
        _reset_hash_cache()
        for h in csn._load_cpv_install_artifact_hashes().values():
            assert len(h) == 64 and all(c in "0123456789abcdef" for c in h.lower())


class TestLegitimateCopyIsSkipped:
    """Positive side: an unmodified bundled copy of CPV's catalog/scanner
    is silently skipped — zero findings, zero noise."""

    def test_byte_identical_catalog_copy_yields_no_findings(self, tmp_path: Path) -> None:
        _reset_hash_cache()
        plugin = _make_target_plugin(tmp_path)
        # Shipped offline auditor: byte-identical copy of CPV's catalog.
        shutil.copy2(CPV_CATALOG, plugin / "scripts" / "skillaudit_patterns.json")
        # Force a re-scan so cached results from earlier suite runs don't mask.
        findings, _ = csn.scan_path(plugin)
        catalog_findings = [
            f for f in findings if f.get("file", "").endswith("skillaudit_patterns.json")
        ]
        assert catalog_findings == [], (
            f"Issue #42 regression: byte-identical bundled copy of CPV's "
            f"`skillaudit_patterns.json` must produce ZERO findings (the "
            f"hash-anchored self-artifact skip was supposed to eat them all). "
            f"Got {len(catalog_findings)} findings."
        )

    def test_is_self_artifact_copy_matches_real_catalog(self, tmp_path: Path) -> None:
        _reset_hash_cache()
        plugin = _make_target_plugin(tmp_path)
        dest = plugin / "scripts" / "skillaudit_patterns.json"
        shutil.copy2(CPV_CATALOG, dest)
        assert csn._is_self_artifact_copy(dest) is True


class TestSpoofedBasenameIsStillScanned:
    """Negative side (the security gate): a file with a self-artifact
    BASENAME but DIFFERENT bytes is NOT skipped — the scanner runs.
    This is the critical anti-evasion property."""

    def test_spoofed_catalog_basename_is_not_skipped(self, tmp_path: Path) -> None:
        _reset_hash_cache()
        plugin = _make_target_plugin(tmp_path)
        # A "malicious" file masquerading as CPV's catalog — content is
        # arbitrary JSON (not the real catalog), so SHA256 differs and the
        # skip MUST refuse it.
        spoofed = plugin / "scripts" / "skillaudit_patterns.json"
        spoofed.write_text('{"oh": "not the real catalog"}', encoding="utf-8")
        assert csn._is_self_artifact_copy(spoofed) is False, (
            "Issue #42 SECURITY: a file named `skillaudit_patterns.json` "
            "with different bytes than the real CPV catalog MUST NOT be "
            "skipped — that would be a basename-spoofing evasion. The "
            "hash-anchored gate must reject it so it gets scanned normally."
        )

    def test_spoofed_scanner_module_is_not_skipped(self, tmp_path: Path) -> None:
        _reset_hash_cache()
        plugin = _make_target_plugin(tmp_path)
        # Payload named after CPV's scanner module but with arbitrary code.
        spoofed = plugin / "scripts" / "cpv_skillaudit_native.py"
        spoofed.write_text("# not the real scanner — arbitrary payload here\n", encoding="utf-8")
        assert csn._is_self_artifact_copy(spoofed) is False

    def test_unrelated_basename_is_not_skipped(self, tmp_path: Path) -> None:
        """Sanity: a file with a completely unrelated basename is not in
        the allowlist, so the hash-anchored skip never even considers
        it — the gate fast-paths to False on basename mismatch."""
        _reset_hash_cache()
        plugin = _make_target_plugin(tmp_path)
        other = plugin / "scripts" / "ordinary_helper.py"
        other.write_text("# just a normal helper\n", encoding="utf-8")
        assert csn._is_self_artifact_copy(other) is False


class TestIterScannableFilesHonorsSkip:
    """End-to-end: ``_iter_scannable_files`` must omit a byte-identical
    bundled artifact from its yielded set, but include a spoofed file
    of the same basename."""

    def test_real_catalog_copy_is_not_yielded(self, tmp_path: Path) -> None:
        _reset_hash_cache()
        plugin = _make_target_plugin(tmp_path)
        catalog_copy = plugin / "scripts" / "skillaudit_patterns.json"
        shutil.copy2(CPV_CATALOG, catalog_copy)
        # Add an ordinary scannable file so iteration has work to do.
        (plugin / "scripts" / "ordinary.py").write_text("x = 1\n", encoding="utf-8")
        yielded = list(csn._iter_scannable_files(plugin))
        assert catalog_copy not in yielded, (
            "Real CPV catalog copy must be skipped by _iter_scannable_files."
        )
        # Sanity: the ordinary file IS yielded (so we didn't break iteration).
        assert any(p.name == "ordinary.py" for p in yielded)

    def test_spoofed_catalog_basename_is_yielded(self, tmp_path: Path) -> None:
        _reset_hash_cache()
        plugin = _make_target_plugin(tmp_path)
        spoofed = plugin / "scripts" / "skillaudit_patterns.json"
        spoofed.write_text('{"spoof": true}', encoding="utf-8")
        yielded = list(csn._iter_scannable_files(plugin))
        assert spoofed in yielded, (
            "Spoofed basename (different bytes) MUST still be yielded — "
            "otherwise a malicious plugin could evade scanning by naming "
            "its payload after a CPV artifact."
        )
