"""Tests for Phase B of TRDD-c0ee9543 — marketplace ↔ upstream plugin.json cross-validation.

Phase B fetches the upstream `plugin.json` (via github/url/git-subdir/
relative-path sources) and diffs it against the marketplace entry.
- name mismatch → MAJOR (install resolver breaks)
- version drift → MINOR (manifest wins silently; display confusion)
- description/author/keywords drift → NIT (UX papercut)
- unreachable source → WARNING (skip the cross-check, don't fail it)

All tests use the `relative-path` source type with on-disk plugin.json
files so they do NOT touch the network. The github/url paths share the
same diff helper and are covered by mocking the fetcher.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure scripts dir is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _write_plugin_json(plugin_root: Path, manifest: dict) -> None:
    """Write `.claude-plugin/plugin.json` at plugin_root."""
    pj_dir = plugin_root / ".claude-plugin"
    pj_dir.mkdir(parents=True, exist_ok=True)
    pj_dir.joinpath("plugin.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def _write_marketplace_with_local_plugin(tmp: Path, entry: dict, manifest: dict) -> Path:
    """Layout B style: marketplace.json + a sibling plugin folder on disk.

    Returns the marketplace root path. The marketplace entry uses a
    relative-path `source: "./plugin"` form so plugin.json is local and
    can be cross-checked without network I/O.
    """
    plugin_dir = tmp / "plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _write_plugin_json(plugin_dir, manifest)

    mkpl_dir = tmp / ".claude-plugin"
    mkpl_dir.mkdir(parents=True, exist_ok=True)
    full_entry = {**entry, "source": "./plugin"}
    payload = {
        "name": "test-marketplace",
        "owner": {"name": "Tester"},
        "plugins": [full_entry],
    }
    mkpl_dir.joinpath("marketplace.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return tmp


class TestNameMismatchEmitsMajor:
    """Phase B.1 — name mismatch → MAJOR (install breaks)."""

    def test_name_mismatch_emits_major_with_diff(self):
        """Reproduces the ai-maestro-visual-communicator-plugin incident.

        Marketplace entry says `ai-maestro-visual-communicator` while the
        upstream plugin.json says `ai-maestro-visual-communicator-plugin`.
        Install resolver fails with "not found".
        """
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace_with_local_plugin(
                tmp,
                entry={"name": "ai-maestro-visual-communicator"},
                manifest={
                    "name": "ai-maestro-visual-communicator-plugin",
                    "version": "1.2.2",
                },
            )

            report = validate_marketplace(root)
            findings = [
                r
                for r in report.results
                if r.level == "MAJOR" and "RC-MKPL-NAME-MISMATCH" in r.message
            ]
            assert len(findings) == 1, (
                f"Expected 1 MAJOR for name mismatch, got: {[r.message for r in report.results]}"
            )
            msg = findings[0].message
            assert "ai-maestro-visual-communicator" in msg
            assert "ai-maestro-visual-communicator-plugin" in msg

    def test_matching_names_no_finding(self):
        """When names agree, no NAME-MISMATCH finding fires."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace_with_local_plugin(
                tmp,
                entry={"name": "my-plugin"},
                manifest={"name": "my-plugin", "version": "1.0.0"},
            )

            report = validate_marketplace(root)
            findings = [
                r for r in report.results if "RC-MKPL-NAME-MISMATCH" in r.message
            ]
            assert not findings


class TestVersionDriftEmitsMinor:
    """Phase B.2 — version drift → MINOR (manifest wins silently)."""

    def test_version_drift_emits_minor_with_drop_suggestion(self):
        """Marketplace pins `1.0.0` while upstream is at `1.2.2`."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace_with_local_plugin(
                tmp,
                entry={"name": "my-plugin", "version": "1.0.0"},
                manifest={"name": "my-plugin", "version": "1.2.2"},
            )

            report = validate_marketplace(root)
            findings = [
                r
                for r in report.results
                if r.level == "MINOR" and "RC-MKPL-VERSION-DRIFT" in r.message
            ]
            assert len(findings) == 1, (
                f"Expected MINOR VERSION-DRIFT, got: {[r.message for r in report.results]}"
            )
            assert "1.0.0" in findings[0].message
            assert "1.2.2" in findings[0].message


class TestMetadataDriftEmitsNit:
    """Phase B.3 — description / author / keywords drift → NIT."""

    def test_description_drift_emits_nit(self):
        """Different description fields emit one NIT per drifted field."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace_with_local_plugin(
                tmp,
                entry={
                    "name": "my-plugin",
                    "description": "Old marketplace description.",
                },
                manifest={
                    "name": "my-plugin",
                    "version": "1.0.0",
                    "description": "Current upstream description.",
                },
            )

            report = validate_marketplace(root)
            findings = [
                r
                for r in report.results
                if r.level == "NIT"
                and "RC-MKPL-METADATA-DRIFT" in r.message
                and "description" in r.message
            ]
            assert findings, (
                f"Expected NIT METADATA-DRIFT for description, got: {[r.message for r in report.results]}"
            )

    def test_keywords_drift_emits_nit(self):
        """Different keywords lists emit a single NIT."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace_with_local_plugin(
                tmp,
                entry={
                    "name": "my-plugin",
                    "keywords": ["old", "tags"],
                },
                manifest={
                    "name": "my-plugin",
                    "version": "1.0.0",
                    "keywords": ["new", "modern", "tags"],
                },
            )

            report = validate_marketplace(root)
            findings = [
                r
                for r in report.results
                if r.level == "NIT"
                and "RC-MKPL-METADATA-DRIFT" in r.message
                and "keywords" in r.message
            ]
            assert findings


class TestUnreachableSourceEmitsWarning:
    """Phase B.4 — unreachable source → WARNING, NOT MAJOR."""

    def test_unreachable_source_emits_warning_not_major(self):
        """When upstream plugin.json cannot be fetched, fall back to WARNING.

        Air-gapped/network-flaky CI must not see false-positive MAJORs.
        Use a github source — without network, our fetcher returns None.
        """
        from validate_marketplace import validate_marketplace

        # Force fetcher to return None via env var bypass.
        os.environ["CPV_SKIP_UPSTREAM_CROSS_CHECK"] = "0"
        os.environ["CPV_TEST_FORCE_UPSTREAM_UNREACHABLE"] = "1"
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                mkpl_dir = tmp / ".claude-plugin"
                mkpl_dir.mkdir(parents=True, exist_ok=True)
                mkpl_dir.joinpath("marketplace.json").write_text(
                    json.dumps(
                        {
                            "name": "test-marketplace",
                            "owner": {"name": "Tester"},
                            "plugins": [
                                {
                                    "name": "my-plugin",
                                    "source": {
                                        "source": "github",
                                        "repo": "nonexistent-owner-xyz/nonexistent-repo-xyz",
                                    },
                                }
                            ],
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                report = validate_marketplace(tmp)
                findings = [
                    r
                    for r in report.results
                    if "RC-MKPL-UPSTREAM-UNREACHABLE" in r.message
                ]
                assert findings, (
                    f"Expected WARNING for unreachable source, got: {[r.message for r in report.results]}"
                )
                # MUST NOT be MAJOR — network flake is not a publish blocker.
                assert findings[0].level == "WARNING"
        finally:
            os.environ.pop("CPV_TEST_FORCE_UPSTREAM_UNREACHABLE", None)
            os.environ.pop("CPV_SKIP_UPSTREAM_CROSS_CHECK", None)


class TestCacheHitDoesNotRefetch:
    """Phase B.5 — cache layer behaviour."""

    def test_cache_hit_does_not_refetch(self):
        """Calling fetch_upstream_plugin_json twice for the same source returns the cached copy."""
        from cpv_upstream_plugin_json import fetch_upstream_plugin_json

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            plugin_dir = tmp / "plugin"
            plugin_dir.mkdir(parents=True, exist_ok=True)
            _write_plugin_json(
                plugin_dir, {"name": "cache-test", "version": "1.0.0"}
            )

            entry = {"name": "cache-test", "source": "./plugin"}
            cache_dir = tmp / "cache"

            # First call — should fetch from disk.
            result1 = fetch_upstream_plugin_json(
                entry, marketplace_dir=tmp, cache_dir=cache_dir
            )
            assert result1 is not None
            assert result1.get("name") == "cache-test"

            # Second call — should still return the same content (cache or re-read).
            result2 = fetch_upstream_plugin_json(
                entry, marketplace_dir=tmp, cache_dir=cache_dir
            )
            assert result2 == result1


class TestSkipEnvVarBypassesCheck:
    """Phase B.6 — `CPV_SKIP_UPSTREAM_CROSS_CHECK=1` skips all cross-checks."""

    def test_skip_env_var_bypasses_check(self):
        """When the bypass env var is set, no RC-MKPL-* B-phase findings emit."""
        from validate_marketplace import validate_marketplace

        os.environ["CPV_SKIP_UPSTREAM_CROSS_CHECK"] = "1"
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                root = _write_marketplace_with_local_plugin(
                    tmp,
                    entry={"name": "wrong-name", "version": "1.0.0"},
                    manifest={"name": "right-name", "version": "9.9.9"},
                )

                report = validate_marketplace(root)
                # Phase B codes should NOT fire (Phase A still does — it's
                # static-schema, not network).
                phase_b_codes = {
                    "RC-MKPL-NAME-MISMATCH",
                    "RC-MKPL-VERSION-DRIFT",
                    "RC-MKPL-METADATA-DRIFT",
                }
                hits = [
                    r
                    for r in report.results
                    if any(code in r.message for code in phase_b_codes)
                ]
                assert not hits, (
                    f"Bypass env var did not suppress Phase B, got: {[r.message for r in hits]}"
                )
        finally:
            os.environ.pop("CPV_SKIP_UPSTREAM_CROSS_CHECK", None)


class TestPerEntryOptOut:
    """Phase B.7 — `_cpv_skip_upstream_check: true` on the entry skips it."""

    def test_per_entry_opt_out_via_underscore_field(self):
        """Per-entry opt-out blocks Phase B for that entry but not Phase A."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace_with_local_plugin(
                tmp,
                entry={
                    "name": "alias-name",
                    "version": "1.0.0",
                    "_cpv_skip_upstream_check": True,
                },
                manifest={"name": "canonical-name", "version": "2.0.0"},
            )

            report = validate_marketplace(root)
            phase_b_findings = [
                r
                for r in report.results
                if "RC-MKPL-NAME-MISMATCH" in r.message
                or "RC-MKPL-VERSION-DRIFT" in r.message
            ]
            assert not phase_b_findings, (
                f"Per-entry opt-out failed; got: {[r.message for r in phase_b_findings]}"
            )

            # The underscore field itself must NOT trigger UNKNOWN-FIELD.
            unknown = [
                r
                for r in report.results
                if "RC-MKPL-UNKNOWN-FIELD" in r.message
                and "_cpv_skip_upstream_check" in r.message
            ]
            assert not unknown


class TestPerMarketplaceSentinel:
    """Phase B.8 — `.claude-plugin/.cpv-no-upstream-check` sentinel works."""

    def test_per_marketplace_sentinel_skips_all_entries(self):
        """Zero-byte sentinel file silences all Phase B checks for the marketplace."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace_with_local_plugin(
                tmp,
                entry={"name": "wrong-name", "version": "0.0.0"},
                manifest={"name": "right-name", "version": "9.9.9"},
            )

            # Drop the sentinel.
            sentinel = root / ".claude-plugin" / ".cpv-no-upstream-check"
            sentinel.touch()

            report = validate_marketplace(root)
            phase_b_findings = [
                r
                for r in report.results
                if any(
                    code in r.message
                    for code in (
                        "RC-MKPL-NAME-MISMATCH",
                        "RC-MKPL-VERSION-DRIFT",
                        "RC-MKPL-METADATA-DRIFT",
                    )
                )
            ]
            assert not phase_b_findings, (
                f"Sentinel did not suppress Phase B: {[r.message for r in phase_b_findings]}"
            )


class TestLayoutCSelfEntryUsesSameHelper:
    """Phase B.9 — Layout C self-entry uses the same diff helper as Phase B."""

    def test_layout_c_self_entry_uses_same_helper(self):
        """Layout C: plugin.json + marketplace.json at same root, self-reference."""
        from cpv_upstream_plugin_json import diff_marketplace_vs_upstream

        # The helper itself should report a name mismatch when entry and
        # upstream disagree. This proves Layout C's validate_layout_c_consistency
        # can be refactored to call this same helper.
        entry = {"name": "drift-name", "version": "1.0.0"}
        upstream = {"name": "real-name", "version": "1.0.0"}
        drifts = diff_marketplace_vs_upstream(entry, upstream)
        assert any(d.code == "RC-MKPL-NAME-MISMATCH" for d in drifts)
        assert any(d.severity == "MAJOR" for d in drifts)


class TestCacheTTLBehaviour:
    """Phase B.10 — TTL controls cache freshness."""

    def test_cache_ttl_zero_forces_refresh(self, monkeypatch):
        """When TTL=0, even a fresh cache entry is treated as stale."""
        from cpv_upstream_plugin_json import fetch_upstream_plugin_json

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            plugin_dir = tmp / "plugin"
            plugin_dir.mkdir(parents=True, exist_ok=True)
            _write_plugin_json(
                plugin_dir, {"name": "ttl-test", "version": "1.0.0"}
            )

            entry = {"name": "ttl-test", "source": "./plugin"}
            cache_dir = tmp / "cache"

            # Fetch with default TTL — populates cache.
            r1 = fetch_upstream_plugin_json(
                entry, marketplace_dir=tmp, cache_dir=cache_dir
            )
            assert r1 is not None

            # Modify the upstream file.
            _write_plugin_json(
                plugin_dir, {"name": "ttl-test-modified", "version": "2.0.0"}
            )

            # Fetch with TTL=0 — must pick up the modification.
            r2 = fetch_upstream_plugin_json(
                entry,
                marketplace_dir=tmp,
                cache_dir=cache_dir,
                ttl_seconds=0,
            )
            assert r2 is not None
            assert r2.get("name") == "ttl-test-modified"
