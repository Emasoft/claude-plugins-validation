"""Tests for the marketplace top-level `version` vs `metadata.version` key handling.

Per plugin-marketplaces.md (fetched live 2026-09-06): top-level `version` IS
the documented field ("Marketplace manifest version"), and `metadata.version`
is explicitly the backward-compatibility alias. A prior revision of
`validate_marketplace_optional_fields` had this INVERTED — it told authors
top-level `version` was undocumented and to prefer `metadata.version`. This
suite pins the corrected behavior:

  - top-level `version` only            -> no version-related finding (the
                                            load-bearing regression test)
  - `metadata.version` only             -> NIT (spec-conformant backward-
                                            compat alias; a migration nudge,
                                            never a WARNING)
  - both, agreeing                      -> no warning
  - both, disagreeing                   -> WARNING naming both values (the
                                            actual bug: a split source of
                                            truth silently ships the wrong
                                            version)
  - non-string top-level `version`      -> MINOR
  - non-semver top-level `version`      -> MINOR
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _write_marketplace(tmp: Path, payload: dict) -> Path:
    mkpl_dir = tmp / ".claude-plugin"
    mkpl_dir.mkdir(parents=True, exist_ok=True)
    mkpl_file = mkpl_dir / "marketplace.json"
    mkpl_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return tmp


def _base_payload(**extra) -> dict:
    payload = {
        "name": "test-marketplace",
        "owner": {"name": "Tester"},
        "plugins": [],
    }
    payload.update(extra)
    return payload


def _run(tmp_path: Path, payload: dict):
    from validate_marketplace import validate_marketplace

    root = _write_marketplace(tmp_path, payload)
    return validate_marketplace(root)


def test_top_level_version_only_emits_no_version_finding(tmp_path):
    """Top-level `version` alone must draw NO version-related finding (positive control)."""
    report = _run(tmp_path, _base_payload(version="1.2.3"))
    version_findings = [r for r in report.results if "version" in r.message.lower()]
    assert not any("prefer 'metadata.version'" in r.message for r in version_findings), (
        f"top-level version must never be told to prefer metadata.version: {[r.message for r in version_findings]}"
    )
    assert version_findings == [], f"expected zero version findings, got: {[r.message for r in version_findings]}"


def test_metadata_version_only_emits_nit_not_warning(tmp_path):
    """`metadata.version` alone is spec-conformant (backward-compat alias) -> NIT migration nudge, not WARNING."""
    report = _run(tmp_path, _base_payload(metadata={"version": "1.0.0"}))
    nits = [r for r in report.results if r.level == "NIT" and "metadata.version" in r.message]
    assert len(nits) == 1, f"expected exactly one NIT, got: {[r.message for r in report.results]}"
    assert "backward-compat" in nits[0].message.lower() or "alias" in nits[0].message.lower()
    warnings = [r for r in report.results if r.level == "WARNING" and "metadata.version" in r.message]
    assert warnings == [], f"metadata.version alone must never WARNING (it is spec-conformant): {warnings}"


def test_both_agreeing_versions_emit_no_warning(tmp_path):
    """Top-level `version` and `metadata.version` agreeing must not warn."""
    report = _run(tmp_path, _base_payload(version="2.0.0", metadata={"version": "2.0.0"}))
    warnings = [r for r in report.results if r.level == "WARNING" and "version" in r.message.lower()]
    assert warnings == [], f"expected no version WARNING, got: {[r.message for r in warnings]}"


def test_both_disagreeing_versions_emit_warning_naming_both(tmp_path):
    """Top-level `version` and `metadata.version` disagreeing must WARN, naming both values."""
    report = _run(tmp_path, _base_payload(version="1.0.0", metadata={"version": "2.0.0"}))
    warnings = [r for r in report.results if r.level == "WARNING" and "version" in r.message.lower()]
    assert len(warnings) == 1, f"expected exactly one WARNING, got: {[r.message for r in report.results]}"
    assert "1.0.0" in warnings[0].message
    assert "2.0.0" in warnings[0].message


def test_non_string_version_is_minor(tmp_path):
    """A non-string top-level `version` must be MINOR."""
    report = _run(tmp_path, _base_payload(version=123))
    minors = [r for r in report.results if r.level == "MINOR" and "version field must be a string" in r.message]
    assert len(minors) == 1, f"expected one MINOR, got: {[r.message for r in report.results]}"


def test_non_semver_version_is_minor(tmp_path):
    """A non-semver top-level `version` must be MINOR."""
    report = _run(tmp_path, _base_payload(version="not-a-version"))
    minors = [r for r in report.results if r.level == "MINOR" and "semver" in r.message.lower()]
    assert len(minors) == 1, f"expected one MINOR, got: {[r.message for r in report.results]}"
