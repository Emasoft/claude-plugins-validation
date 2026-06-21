#!/usr/bin/env python3
"""D2 spec-sync — plugin.json manifest ``displayName`` field (v2.1.143).

``displayName`` is a documented standard-metadata manifest field (the
human-readable name shown in the ``/plugin`` picker; falls back to ``name`` when
omitted, never used for namespacing). It must live in ``validate_plugin``'s
``known_fields`` set so a plugin that legitimately sets it does NOT draw a
spurious ``Unknown manifest field 'displayName'`` WARNING.

Two-sided (so the positive assertion can never pass vacuously against a validator
that allowlists everything): the known field is silent AND a genuinely-unknown
field still WARNs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import validate_manifest  # noqa: E402


def _write_manifest(plugin_root: Path, extra: dict[str, object]) -> None:
    manifest_dir = plugin_root / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "name": "demo-plugin",
        "version": "1.0.0",
        "description": "A demo plugin for the displayName manifest-field test.",
    }
    manifest.update(extra)
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")


def _unknown_field_warnings(report: ValidationReport, field: str) -> list:
    """The ``Unknown manifest field '<field>'`` WARNINGs naming ``field``."""
    return [
        r
        for r in report.results
        if r.level == "WARNING" and "Unknown manifest field" in r.message and field in r.message
    ]


class TestDisplayNameManifestField:
    """v2.1.143 — ``displayName`` is a known plugin.json manifest field."""

    def test_display_name_does_not_warn(self, tmp_path: Path) -> None:
        """A manifest carrying ``displayName`` raises no 'Unknown manifest field'
        WARNING for it — it is the documented v2.1.143 /plugin-picker label."""
        _write_manifest(tmp_path, {"displayName": "Demo Plugin"})
        report = ValidationReport()
        validate_manifest(tmp_path, report)
        assert not _unknown_field_warnings(report, "displayName")

    def test_unknown_field_still_warns(self, tmp_path: Path) -> None:
        """Two-sided sanity: a genuinely-unknown manifest field STILL draws the
        'Unknown manifest field' WARNING, so the positive assertion is not vacuous."""
        _write_manifest(tmp_path, {"fooBarBaz2026": "x"})
        report = ValidationReport()
        validate_manifest(tmp_path, report)
        assert _unknown_field_warnings(report, "fooBarBaz2026")
