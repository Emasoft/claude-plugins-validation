"""Tests for marketplace entry strict-field allowlist (Phase A of TRDD-c0ee9543).

The original `ai-maestro-visual-communicator-plugin` incident shipped because
marketplace.json carried a non-spec `scope: "local"` field on 9 plugin entries
and CPV silently accepted it (emitted only INFO via the legacy `known_fields`
union). Phase A promotes this to MAJOR via a strict-allowlist check and adds
the source sub-field allowlist (`github` requires `repo`, etc.).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Ensure scripts dir is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _write_marketplace(tmp: Path, payload: dict) -> Path:
    """Write a marketplace.json under tmp/.claude-plugin/ and return path."""
    mkpl_dir = tmp / ".claude-plugin"
    mkpl_dir.mkdir(parents=True, exist_ok=True)
    mkpl_file = mkpl_dir / "marketplace.json"
    mkpl_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return tmp


class TestUnknownEntryFieldDetection:
    """Phase A.1 — unknown top-level entry fields must be MAJOR."""

    def test_unknown_scope_field_emits_major_with_fix_diff(self):
        """The exact bug from the ai-maestro-visual-communicator incident."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace(
                tmp,
                {
                    "name": "test-marketplace",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {
                            "name": "test-plugin",
                            "source": {"source": "github", "repo": "owner/test-plugin"},
                            "scope": "local",  # bogus field — not in spec
                        }
                    ],
                },
            )

            report = validate_marketplace(root)
            findings = [
                r
                for r in report.results
                if r.level == "MAJOR" and "RC-MKPL-UNKNOWN-FIELD" in r.message
            ]
            assert len(findings) >= 1, (
                f"Expected RC-MKPL-UNKNOWN-FIELD MAJOR for `scope` field, got: {report.results}"
            )
            assert any("scope" in r.message for r in findings)

    def test_multiple_unknown_fields_each_emit_their_own_finding(self):
        """Two extra fields → two findings (don't collapse into one)."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace(
                tmp,
                {
                    "name": "test-marketplace",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {
                            "name": "test-plugin",
                            "source": {"source": "github", "repo": "owner/test-plugin"},
                            "scope": "local",
                            "audience": "internal",
                        }
                    ],
                },
            )

            report = validate_marketplace(root)
            findings = [
                r
                for r in report.results
                if r.level == "MAJOR" and "RC-MKPL-UNKNOWN-FIELD" in r.message
            ]
            assert len(findings) == 2, (
                f"Expected 2 RC-MKPL-UNKNOWN-FIELD findings, got: {[r.message for r in findings]}"
            )

    def test_all_known_fields_accepted(self):
        """A spec-conformant entry MUST NOT emit any MKPL-UNKNOWN findings."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace(
                tmp,
                {
                    "name": "test-marketplace",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {
                            "name": "test-plugin",
                            "version": "1.0.0",
                            "description": "Test plugin",
                            "author": {"name": "Tester"},
                            "homepage": "https://example.com",
                            "repository": "https://example.com/x",
                            "license": "MIT",
                            "keywords": ["test"],
                            "tags": ["test"],
                            "category": "utility",
                            "source": {"source": "github", "repo": "owner/test-plugin"},
                        }
                    ],
                },
            )

            report = validate_marketplace(root)
            mkpl_unknown = [
                r for r in report.results if "RC-MKPL-UNKNOWN-FIELD" in r.message
            ]
            assert not mkpl_unknown, (
                f"Spec-conformant entry should not emit MKPL-UNKNOWN, got: {mkpl_unknown}"
            )


class TestUnknownSourceSubFieldDetection:
    """Phase A.2 — unknown sub-fields inside `source` must be MAJOR."""

    def test_unknown_source_subfield_emits_major(self):
        """github source has `repo` and `ref` only; rejects `branch` etc."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace(
                tmp,
                {
                    "name": "test-marketplace",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {
                            "name": "test-plugin",
                            "source": {
                                "source": "github",
                                "repo": "owner/test-plugin",
                                "branch": "main",  # NOT a valid github source field
                            },
                        }
                    ],
                },
            )

            report = validate_marketplace(root)
            findings = [
                r
                for r in report.results
                if r.level == "MAJOR" and "RC-MKPL-UNKNOWN-SOURCE-FIELD" in r.message
            ]
            assert len(findings) >= 1, (
                f"Expected MAJOR for unknown source sub-field `branch`, got: {report.results}"
            )

    def test_known_source_subfields_accepted(self):
        """github with `repo` and `ref` (both spec) emits no source-field findings."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace(
                tmp,
                {
                    "name": "test-marketplace",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {
                            "name": "test-plugin",
                            "source": {
                                "source": "github",
                                "repo": "owner/test-plugin",
                                "ref": "v1.0.0",
                            },
                        }
                    ],
                },
            )

            report = validate_marketplace(root)
            findings = [
                r for r in report.results if "RC-MKPL-UNKNOWN-SOURCE-FIELD" in r.message
            ]
            assert not findings, (
                f"Spec-conformant source should not emit findings, got: {findings}"
            )

    def test_git_subdir_subdir_field_accepted(self):
        """git-subdir source supports `subdir` field per spec."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace(
                tmp,
                {
                    "name": "test-marketplace",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {
                            "name": "test-plugin",
                            "source": {
                                "source": "git-subdir",
                                "url": "https://example.com/repo.git",
                                "subdir": "plugins/test",
                            },
                        }
                    ],
                },
            )

            report = validate_marketplace(root)
            findings = [
                r for r in report.results if "RC-MKPL-UNKNOWN-SOURCE-FIELD" in r.message
            ]
            assert not findings, (
                f"git-subdir with subdir should be accepted, got: {findings}"
            )


class TestLayoutBNestedEntriesStrict:
    """Phase A.3 — Layout B (nested plugin entries) gets the same allowlist."""

    def test_layout_b_nested_entries_also_strict(self):
        """Per-plugin entries inside Layout B also reject unknown fields."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace(
                tmp,
                {
                    "name": "test-monorepo",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {
                            "name": "nested-plugin",
                            "source": {
                                "source": "directory",
                                "path": "./plugins/nested-plugin",
                            },
                            "scope": "private",  # bogus
                        }
                    ],
                },
            )

            report = validate_marketplace(root)
            findings = [
                r
                for r in report.results
                if r.level == "MAJOR" and "RC-MKPL-UNKNOWN-FIELD" in r.message
            ]
            assert len(findings) >= 1, (
                f"Expected MAJOR for unknown field on Layout B entry, got: {report.results}"
            )


class TestUnderscoreFieldsAcceptedWithoutWarning:
    """Phase A.4 — fields starting with `_` are CPV-private (per-entry opt-out flags)."""

    def test_underscore_field_no_unknown_finding(self):
        """`_cpv_skip_upstream_check` and similar private flags must not emit findings."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            root = _write_marketplace(
                tmp,
                {
                    "name": "test-marketplace",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {
                            "name": "test-plugin",
                            "source": {"source": "github", "repo": "owner/test-plugin"},
                            "_cpv_skip_upstream_check": True,
                        }
                    ],
                },
            )

            report = validate_marketplace(root)
            findings = [
                r
                for r in report.results
                if "RC-MKPL-UNKNOWN-FIELD" in r.message and "_cpv" in r.message
            ]
            assert not findings, (
                f"Private `_cpv_*` field should not emit MKPL-UNKNOWN, got: {findings}"
            )
