"""Regression test: 2026-05-11 ai-maestro-visual-communicator-plugin incident.

The original failure: marketplace.json had
  - "name": "ai-maestro-visual-communicator"  (divergent — upstream is "...-plugin")
  - "version": "1.0.0"                         (stale — upstream is "1.2.2")
  - "scope": "local"                           (unknown field × 9 sibling entries)

CPV's validate_marketplace.py --strict returned VALID (NO findings). Then
`claude plugin install ai-maestro-visual-communicator-plugin@<marketplace>`
returned "not found" because the install resolver hashed the user-typed
name against the marketplace entry name and missed.

After TRDD-c0ee9543 Phases A + B, the same scenario MUST produce THREE
findings BEFORE publish:
  - 1× MAJOR (RC-MKPL-NAME-MISMATCH)
  - 1× MINOR (RC-MKPL-VERSION-DRIFT)
  - 1× MAJOR (RC-MKPL-UNKNOWN-FIELD) for the `scope` field
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Ensure scripts dir is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_ai_maestro_visual_communicator_incident_now_blocks():
    """End-to-end repro: would have caught the 2026-05-11 ship failure."""
    from validate_marketplace import validate_marketplace

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # Upstream plugin.json (what the actual plugin shipped).
        plugin_dir = tmp / "ai-maestro-visual-communicator-plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        plugin_pj = plugin_dir / ".claude-plugin" / "plugin.json"
        plugin_pj.parent.mkdir(parents=True, exist_ok=True)
        plugin_pj.write_text(
            json.dumps(
                {
                    "name": "ai-maestro-visual-communicator-plugin",
                    "version": "1.2.2",
                    "description": "AI Maestro visual communicator plugin.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # Marketplace.json with the exact triple-bug from the incident.
        mkpl_dir = tmp / ".claude-plugin"
        mkpl_dir.mkdir(parents=True, exist_ok=True)
        mkpl_dir.joinpath("marketplace.json").write_text(
            json.dumps(
                {
                    "name": "ai-maestro-plugins",
                    "owner": {"name": "Emasoft"},
                    "plugins": [
                        {
                            "name": "ai-maestro-visual-communicator",  # MAJOR: name mismatch
                            "version": "1.0.0",  # MINOR: stale version
                            "scope": "local",  # MAJOR: unknown field
                            "source": "./ai-maestro-visual-communicator-plugin",
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        report = validate_marketplace(tmp)

        # Find each of the three expected findings.
        name_mismatch = [
            r
            for r in report.results
            if r.level == "MAJOR" and "RC-MKPL-NAME-MISMATCH" in r.message
        ]
        version_drift = [
            r
            for r in report.results
            if r.level == "MINOR" and "RC-MKPL-VERSION-DRIFT" in r.message
        ]
        unknown_field = [
            r
            for r in report.results
            if r.level == "MAJOR"
            and "RC-MKPL-UNKNOWN-FIELD" in r.message
            and "scope" in r.message
        ]

        assert name_mismatch, (
            f"FAIL: Phase B failed to catch name mismatch. "
            f"Got findings: {[r.message for r in report.results]}"
        )
        assert version_drift, (
            f"FAIL: Phase B failed to catch version drift. "
            f"Got findings: {[r.message for r in report.results]}"
        )
        assert unknown_field, (
            f"FAIL: Phase A failed to catch unknown `scope` field. "
            f"Got findings: {[r.message for r in report.results]}"
        )

        # Final assert: the report's exit code is non-zero in strict mode.
        # MAJOR=2 + MINOR=1 means strict mode exit code != 0.
        assert report.has_major


def test_install_failure_hint_appears_in_output():
    """The format_report footer must hint at /cpv-doctor when MKPL findings exist."""
    from validate_marketplace import format_report, validate_marketplace

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        plugin_dir = tmp / "plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        plugin_pj = plugin_dir / ".claude-plugin" / "plugin.json"
        plugin_pj.parent.mkdir(parents=True, exist_ok=True)
        plugin_pj.write_text(
            json.dumps({"name": "right-name", "version": "1.0.0"}),
            encoding="utf-8",
        )
        mkpl_dir = tmp / ".claude-plugin"
        mkpl_dir.mkdir(parents=True, exist_ok=True)
        mkpl_dir.joinpath("marketplace.json").write_text(
            json.dumps(
                {
                    "name": "test-marketplace",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {"name": "wrong-name", "source": "./plugin"},
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        report = validate_marketplace(tmp)
        text = format_report(report)
        assert "/cpv-doctor" in text
        assert "marketplace-upstream-drift.md" in text
