#!/usr/bin/env python3
"""Regression tests for the 7 marketplace authoring pitfalls (TRDD-962fdc55 §8.3).

Each test reproduces one PIT-NNN pattern from
`skills/marketplace-authoring-contract/references/common-pitfalls.md` and
asserts that the validator detects it. The tests use Wave 6's RC-MKPL-*
error codes where applicable.

Pitfall coverage map (verified 2026-05-11 against Wave 6 validator):

| PIT     | Pattern                                | Detection                                                                 |
|---------|----------------------------------------|---------------------------------------------------------------------------|
| PIT-001 | Entry name differs from upstream       | `RC-MKPL-NAME-MISMATCH` MAJOR via cross-validation                        |
| PIT-002 | Stale `version` on remote source       | `RC-MKPL-VERSION-DRIFT` MINOR via cross-validation                        |
| PIT-003 | Top-level `scope` field                | `RC-MKPL-UNKNOWN-FIELD` MAJOR via strict-allowlist                        |
| PIT-004 | Layout C self-entry without source='./'| MAJOR via `validate_plugin.py:validate_layout_c_consistency`              |
| PIT-005 | github source with full URL in `repo`  | `RC-MKPL-UNKNOWN-FIELD` MAJOR when `source: "github"` is a string         |
|         |                                        | shorthand and `repo` is top-level (the common shape agents emit).         |
| PIT-006 | Homepage points at wrong repo          | `RC-MKPL-METADATA-DRIFT` NIT via cross-validation (homepage diverges)     |
| PIT-007 | Category with arbitrary user value     | NOT currently enforced — test documents the gap.                          |

PIT-007's enforcement gap is intentional: Wave 6 did not ship a canonical
category-taxonomy validator. The pitfall is documented in
`common-pitfalls.md` so agents avoid emitting it, but post-hoc detection
relies on the cross-validator's metadata-drift check (which catches it
only when the user-string differs from the upstream plugin.json's
category). The TRDD-962fdc55 §10 acceptance lists PIT-007 as caught at
authoring time (contract guidance) rather than at validation time.

All tests use local relative-path fixtures so no network I/O occurs.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Make scripts importable for the validators.
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_plugin_json(plugin_root: Path, manifest: dict) -> None:
    """Write `.claude-plugin/plugin.json` at plugin_root."""
    pj_dir = plugin_root / ".claude-plugin"
    pj_dir.mkdir(parents=True, exist_ok=True)
    pj_dir.joinpath("plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _write_marketplace(
    marketplace_root: Path,
    entry: dict,
    *,
    extra_plugins: list[dict] | None = None,
) -> None:
    """Write `.claude-plugin/marketplace.json` at marketplace_root."""
    mkpl_dir = marketplace_root / ".claude-plugin"
    mkpl_dir.mkdir(parents=True, exist_ok=True)
    plugins = [entry, *(extra_plugins or [])]
    payload = {
        "name": "test-marketplace",
        "owner": {"name": "Tester"},
        "plugins": plugins,
    }
    mkpl_dir.joinpath("marketplace.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _setup_layout_b_fixture(
    tmp: Path,
    entry: dict,
    upstream_manifest: dict,
    plugin_subdir: str = "plugin",
) -> Path:
    """Create a Layout-B-style fixture: marketplace.json + sibling plugin dir.

    The entry's `source` is set to `f"./{plugin_subdir}"` so the validator
    can locally cross-check against upstream plugin.json without network.
    """
    plugin_root = tmp / plugin_subdir
    plugin_root.mkdir(parents=True, exist_ok=True)
    _write_plugin_json(plugin_root, upstream_manifest)
    full_entry = {**entry, "source": f"./{plugin_subdir}"}
    _write_marketplace(tmp, full_entry)
    return tmp


def _find_findings(report, *, level: str | None = None, code: str) -> list:
    """Filter the report's results for a specific RC-MKPL-* code (and level)."""
    return [r for r in report.results if code in (r.message or "") and (level is None or r.level == level)]


# ---------------------------------------------------------------------------
# PIT-001 — Name Mismatch via Suffix Stripping
# ---------------------------------------------------------------------------


def test_pit_001_name_mismatch_emits_major() -> None:
    """PIT-001: entry strips the `-plugin` suffix; resolver breaks.

    Reproduces the 2026-05-11 incident where the marketplace entry was
    `ai-maestro-visual-communicator` but upstream plugin.json was
    `ai-maestro-visual-communicator-plugin`. Install fails with "not
    found" because the resolver matches on the entry name string-for-
    string.

    Expected: MAJOR with code RC-MKPL-NAME-MISMATCH.
    """
    from validate_marketplace import validate_marketplace

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup_layout_b_fixture(
            tmp,
            entry={"name": "ai-maestro-visual-communicator"},  # mismatch!
            upstream_manifest={
                "name": "ai-maestro-visual-communicator-plugin",
                "version": "1.2.2",
            },
        )

        report = validate_marketplace(tmp)
        findings = _find_findings(report, level="MAJOR", code="RC-MKPL-NAME-MISMATCH")
        assert len(findings) == 1, (
            f"PIT-001: expected exactly 1 MAJOR RC-MKPL-NAME-MISMATCH finding, "
            f"got {len(findings)}. All findings: "
            f"{[(r.level, r.message[:80]) for r in report.results]}"
        )
        msg = findings[0].message
        assert "ai-maestro-visual-communicator" in msg
        assert "ai-maestro-visual-communicator-plugin" in msg


# ---------------------------------------------------------------------------
# PIT-002 — Stale Version on Remote Source
# ---------------------------------------------------------------------------


def test_pit_002_stale_version_emits_minor() -> None:
    """PIT-002: marketplace pins `1.0.0` while upstream is at `1.2.2`.

    The version field on a remote/local source goes stale within hours of
    each upstream release. The cross-validator emits MINOR with the
    suggestion to DROP the field (remote sources) or sync it (local).

    Expected: MINOR with code RC-MKPL-VERSION-DRIFT.
    """
    from validate_marketplace import validate_marketplace

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup_layout_b_fixture(
            tmp,
            entry={"name": "foo-plugin", "version": "1.0.0"},  # stale
            upstream_manifest={"name": "foo-plugin", "version": "1.2.2"},
        )

        report = validate_marketplace(tmp)
        findings = _find_findings(report, level="MINOR", code="RC-MKPL-VERSION-DRIFT")
        assert len(findings) == 1, (
            f"PIT-002: expected exactly 1 MINOR RC-MKPL-VERSION-DRIFT finding, "
            f"got {len(findings)}. All findings: "
            f"{[(r.level, r.message[:80]) for r in report.results]}"
        )
        msg = findings[0].message
        assert "1.0.0" in msg
        assert "1.2.2" in msg


# ---------------------------------------------------------------------------
# PIT-003 — Top-Level Scope Field
# ---------------------------------------------------------------------------


def test_pit_003_scope_field_emits_major() -> None:
    """PIT-003: entry declares a top-level `scope` field.

    The agent confuses the marketplace's per-entry fields with the
    `claude plugin install --scope <X>` install flag. The strict
    allowlist (TRDD-c0ee9543 Phase A) rejects every unknown field.

    Expected: MAJOR with code RC-MKPL-UNKNOWN-FIELD mentioning 'scope'.
    """
    from validate_marketplace import validate_marketplace

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup_layout_b_fixture(
            tmp,
            entry={"name": "foo-plugin", "scope": "user"},  # forbidden field
            upstream_manifest={"name": "foo-plugin", "version": "1.0.0"},
        )

        report = validate_marketplace(tmp)
        findings = _find_findings(report, level="MAJOR", code="RC-MKPL-UNKNOWN-FIELD")
        scope_findings = [r for r in findings if "scope" in r.message]
        assert len(scope_findings) == 1, (
            f"PIT-003: expected exactly 1 MAJOR RC-MKPL-UNKNOWN-FIELD finding "
            f"mentioning 'scope', got {len(scope_findings)}. All findings: "
            f"{[(r.level, r.message[:80]) for r in report.results]}"
        )


# ---------------------------------------------------------------------------
# PIT-004 — Layout C Self-Entry Missing Source
# ---------------------------------------------------------------------------


def test_pit_004_layout_c_missing_source_emits_major() -> None:
    """PIT-004: Layout C self-entry omits the `source: "./"` literal.

    Layout C is the single-repo layout where `.claude-plugin/plugin.json`
    AND `.claude-plugin/marketplace.json` coexist at the same root. The
    marketplace's self-entry MUST declare `source: "./"` explicitly —
    the resolver does not infer Layout C from file colocation.

    Detection lives in `validate_plugin.py:validate_layout_c_consistency`,
    NOT in `validate_marketplace.py` (the cross-manifest check is a
    plugin-level concern).

    Expected: MAJOR with a "Layout C" message mentioning source='./' /
    relative.
    """
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_layout_c_consistency

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pj_dir = tmp / ".claude-plugin"
        pj_dir.mkdir(parents=True, exist_ok=True)
        pj_dir.joinpath("plugin.json").write_text(
            json.dumps({"name": "foo-plugin", "version": "1.0.0"}), encoding="utf-8"
        )
        # Layout C marketplace.json with self-entry but NO source
        pj_dir.joinpath("marketplace.json").write_text(
            json.dumps(
                {
                    "name": "foo-marketplace",
                    "owner": {"name": "Tester"},
                    "plugins": [
                        {
                            "name": "foo-plugin",
                            "version": "1.0.0",
                            # Missing: "source": "./"
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_layout_c_consistency(tmp, report)
        findings = [
            r for r in report.results if r.level == "MAJOR" and "Layout C" in r.message and "source" in r.message
        ]
        assert len(findings) == 1, (
            f"PIT-004: expected exactly 1 MAJOR Layout C source finding, "
            f"got {len(findings)}. All findings: "
            f"{[(r.level, r.message[:120]) for r in report.results]}"
        )
        msg = findings[0].message
        assert "'./'" in msg or "relative" in msg


# ---------------------------------------------------------------------------
# PIT-005 — Source GitHub With Full URL
# ---------------------------------------------------------------------------


def test_pit_005_github_url_in_repo_emits_major() -> None:
    """PIT-005: github-string entry puts a full URL in top-level `repo`.

    When the agent emits `source: "github"` as a string shorthand and a
    top-level `repo` field, the validator's strict-allowlist treats `repo`
    as an unknown top-level entry field — emitting MAJOR
    RC-MKPL-UNKNOWN-FIELD. This catches the common copy-from-URL-bar
    mistake even though the validator does not have a dedicated
    "url-in-github-shorthand" detector.

    Expected: MAJOR with code RC-MKPL-UNKNOWN-FIELD mentioning 'repo'.
    """

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _write_marketplace(
            tmp,
            entry={
                "name": "foo-plugin",
                "source": "github",
                # PIT-005: full URL in top-level repo instead of "owner/repo" shorthand
                "repo": "https://github.com/owner/foo-plugin.git",
            },
        )

        from validate_marketplace import validate_marketplace as vm

        report = vm(tmp)
        findings = _find_findings(report, level="MAJOR", code="RC-MKPL-UNKNOWN-FIELD")
        repo_findings = [r for r in findings if "'repo'" in r.message]
        assert len(repo_findings) >= 1, (
            f"PIT-005: expected at least 1 MAJOR RC-MKPL-UNKNOWN-FIELD finding "
            f"mentioning 'repo' (top-level repo with github string source), "
            f"got {len(repo_findings)}. All findings: "
            f"{[(r.level, r.message[:80]) for r in report.results]}"
        )


# ---------------------------------------------------------------------------
# PIT-006 — Homepage Pointing at Wrong Repo
# ---------------------------------------------------------------------------


def test_pit_006_homepage_repo_mismatch_emits_nit() -> None:
    """PIT-006: entry's `homepage` differs from upstream plugin.json's homepage.

    Copy-paste from a sibling entry leaves homepage pointing at the wrong
    repo. The cross-validator detects metadata drift between marketplace
    entry and upstream plugin.json and emits NIT
    RC-MKPL-METADATA-DRIFT — homepage is among the metadata fields
    tracked by the diff helper.

    Expected: NIT with code RC-MKPL-METADATA-DRIFT mentioning 'homepage'.
    """
    from validate_marketplace import validate_marketplace

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup_layout_b_fixture(
            tmp,
            entry={
                "name": "foo-plugin",
                # PIT-006: points at the wrong repo
                "homepage": "https://github.com/owner/bar-plugin",
            },
            upstream_manifest={
                "name": "foo-plugin",
                "version": "1.0.0",
                "homepage": "https://github.com/owner/foo-plugin",
            },
        )

        report = validate_marketplace(tmp)
        findings = _find_findings(report, level="NIT", code="RC-MKPL-METADATA-DRIFT")
        homepage_findings = [r for r in findings if "homepage" in r.message]
        assert len(homepage_findings) >= 1, (
            f"PIT-006: expected at least 1 NIT RC-MKPL-METADATA-DRIFT finding "
            f"mentioning 'homepage', got {len(homepage_findings)}. "
            f"All findings: {[(r.level, r.message[:80]) for r in report.results]}"
        )


# ---------------------------------------------------------------------------
# PIT-007 — Category With Arbitrary Value
# ---------------------------------------------------------------------------


def test_pit_007_category_validation_gap_is_documented() -> None:
    """PIT-007: category-taxonomy validation is intentionally not enforced.

    PIT-007's primary defence is AUTHORING-TIME contract guidance
    (the contract's `common-pitfalls.md §PIT-007` + `known-fields.md`
    explain the canonical 15-category taxonomy). Wave 6 deliberately did
    NOT ship a runtime category-taxonomy validator because:

    1. The canonical taxonomy is documented in the contract but not
       hard-coded into the validator — letting agents emit valid
       free-form-but-reasonable categories during the taxonomy's
       evolution.
    2. The cross-validator's metadata-drift diff (see
       `cpv_upstream_plugin_json.py:519-531`) tracks description,
       author, keywords, and homepage — but NOT category. Adding
       category to that diff would emit NIT noise whenever any
       Layout-B plugin's category sync lags upstream by a few commits.

    This test guards the documented gap: PIT-007 reports do NOT fire
    at validation time, but the pitfall IS documented in the
    authoring-contract reference. The test will start failing if the
    validator ever begins emitting RC-MKPL-METADATA-DRIFT for category
    drift — at which point the test should be updated to assert the
    new behaviour.

    A future TRDD that adds category-taxonomy enforcement can flip
    this test from "assert gap" to "assert detection".
    """
    from validate_marketplace import validate_marketplace

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup_layout_b_fixture(
            tmp,
            entry={
                "name": "foo-plugin",
                # PIT-007: arbitrary user-string instead of canonical taxonomy
                "category": "My personal coding tools",
            },
            upstream_manifest={
                "name": "foo-plugin",
                "version": "1.0.0",
                "category": "productivity",
            },
        )

        report = validate_marketplace(tmp)
        # Confirm the gap: no category-specific drift finding fires today.
        category_findings = [
            r
            for r in report.results
            if "RC-MKPL-METADATA-DRIFT" in (r.message or "") and "category" in (r.message or "")
        ]
        # The contract is the primary defence — verify the pitfall IS documented
        # in common-pitfalls.md so authoring-time guidance covers the case.
        pitfalls_md = PLUGIN_ROOT / "skills" / "marketplace-authoring-contract" / "references" / "common-pitfalls.md"
        assert pitfalls_md.is_file(), "common-pitfalls.md missing — contract reference unavailable"
        pitfalls_text = pitfalls_md.read_text(encoding="utf-8")
        assert "PIT-007" in pitfalls_text, (
            "PIT-007 must remain documented in common-pitfalls.md — agents rely "
            "on this reference to avoid emitting non-canonical category values."
        )
        assert "category" in pitfalls_text.lower(), (
            "common-pitfalls.md must discuss 'category' to give agents guidance about the canonical taxonomy."
        )
        # When this assertion flips (validator gains category-taxonomy enforcement),
        # convert this test to assert the new RC-MKPL-METADATA-DRIFT finding fires.
        assert not category_findings, (
            "PIT-007 validation-time detection was added — convert this test to "
            "assert RC-MKPL-METADATA-DRIFT fires on category drift. Findings: "
            f"{[(r.level, r.message[:120]) for r in category_findings]}"
        )
