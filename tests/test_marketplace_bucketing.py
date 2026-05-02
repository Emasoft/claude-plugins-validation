"""Tests for the v2.48 Phase 4 cross-plugin finding-bucketing logic.

When the corpus dedup deletes a duplicate file from one plugin's staging
(because another plugin's copy is the canonical), the second plugin's scan
won't see that file. The bucketing helper closes this coverage gap by
propagating the findings emitted on the canonical to peer plugins that
originally contained a copy.

These tests construct hand-built per-plugin reports + dedup_maps so we can
verify the bucketing logic without needing a real fclones run or scanner
invocation. The end-to-end behavior is also exercised by the marketplace
smoke tests in test_validate_security.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_security import _bucket_canonical_findings_into_plugins  # noqa: E402


def _build_report_with_finding(
    file_path: Path, level: str = "major", message: str = "test issue"
) -> ValidationReport:
    """Helper: build a ValidationReport containing one finding on file_path."""
    report = ValidationReport()
    getattr(report, level.lower())(message, str(file_path), 10)
    return report


class TestBucketingPropagation:
    def test_finding_on_canonical_propagates_to_all_peer_plugins(
        self, tmp_path: Path
    ) -> None:
        """Setup: 3 plugins originally had identical SKILL.md. After dedup,
        only p1 still has it on disk (canonical). A finding emitted on
        p1's SKILL.md must propagate into p2 and p3's reports too."""
        # Original cache paths (where users see their plugins live).
        orig_p1 = tmp_path / "cache/p1"
        orig_p2 = tmp_path / "cache/p2"
        orig_p3 = tmp_path / "cache/p3"
        for p in (orig_p1, orig_p2, orig_p3):
            (p / "skills").mkdir(parents=True)
            (p / "skills" / "SKILL.md").write_text("identical content")

        # Stage paths (where scanners actually run).
        stage = tmp_path / "stage"
        stage.mkdir()
        stage_p1 = stage / "p1"
        stage_p2 = stage / "p2"
        stage_p3 = stage / "p3"
        for sp in (stage_p1, stage_p2, stage_p3):
            (sp / "skills").mkdir(parents=True)

        # Canonical lives in p1's stage. p2 and p3 had peers (now deleted).
        canonical = stage_p1 / "skills" / "SKILL.md"
        peer_in_p2 = stage_p2 / "skills" / "SKILL.md"
        peer_in_p3 = stage_p3 / "skills" / "SKILL.md"
        # Only the canonical is on disk; the peers represent the
        # pre-deletion paths recorded in dedup_map.
        canonical.touch()

        dedup_map = {canonical: [canonical, peer_in_p2, peer_in_p3]}
        plugin_paths = {"p1": stage_p1, "p2": stage_p2, "p3": stage_p3}
        original_paths = {"p1": orig_p1, "p2": orig_p2, "p3": orig_p3}

        # p1's report has the finding on the canonical; p2/p3 are empty.
        p1_report = _build_report_with_finding(canonical)
        p2_report = ValidationReport()
        p3_report = ValidationReport()

        propagated = _bucket_canonical_findings_into_plugins(
            {"p1": p1_report, "p2": p2_report, "p3": p3_report},
            dedup_map,
            plugin_paths,
            original_paths,
        )

        assert propagated == 2, "expected one propagation per peer (p2 and p3)"
        # p1's report unchanged
        assert len(p1_report.results) == 1
        # p2 and p3 each gained a finding pointing at THEIR cache copy
        assert len(p2_report.results) == 1
        assert len(p3_report.results) == 1
        assert p2_report.results[0].file == str(orig_p2 / "skills" / "SKILL.md")
        assert p3_report.results[0].file == str(orig_p3 / "skills" / "SKILL.md")
        # Severity, message, line, etc. preserved
        assert p2_report.results[0].level == p1_report.results[0].level
        assert p2_report.results[0].message == p1_report.results[0].message
        assert p2_report.results[0].line == p1_report.results[0].line

    def test_finding_on_unique_file_not_propagated(self, tmp_path: Path) -> None:
        """A finding on a file that's NOT a canonical-with-duplicates must
        NOT propagate anywhere (even when an unrelated dedup_map exists)."""
        stage = tmp_path / "stage"
        stage_p1 = stage / "p1"
        stage_p2 = stage / "p2"
        for sp in (stage_p1, stage_p2):
            sp.mkdir(parents=True)

        # dedup_map only mentions a different file
        unrelated_canonical = stage_p1 / "common.md"
        unrelated_peer = stage_p2 / "common.md"
        unrelated_canonical.touch()
        dedup_map = {unrelated_canonical: [unrelated_canonical, unrelated_peer]}

        # p1 has a finding on a UNIQUE file (not in dedup_map)
        unique_file = stage_p1 / "unique.md"
        p1_report = _build_report_with_finding(unique_file)
        p2_report = ValidationReport()

        plugin_paths = {"p1": stage_p1, "p2": stage_p2}
        original_paths = {"p1": tmp_path / "cache/p1", "p2": tmp_path / "cache/p2"}

        propagated = _bucket_canonical_findings_into_plugins(
            {"p1": p1_report, "p2": p2_report},
            dedup_map,
            plugin_paths,
            original_paths,
        )

        assert propagated == 0, "unique-file finding should not propagate"
        assert len(p1_report.results) == 1
        assert len(p2_report.results) == 0

    def test_empty_dedup_map_no_propagation(self, tmp_path: Path) -> None:
        p1_report = _build_report_with_finding(tmp_path / "stage/p1/x.md")
        p2_report = ValidationReport()
        propagated = _bucket_canonical_findings_into_plugins(
            {"p1": p1_report, "p2": p2_report},
            dedup_map={},
            plugin_paths={"p1": tmp_path / "stage/p1", "p2": tmp_path / "stage/p2"},
            original_paths={"p1": tmp_path / "orig/p1", "p2": tmp_path / "orig/p2"},
        )
        assert propagated == 0
        assert len(p2_report.results) == 0

    def test_propagated_finding_marked_in_suggestion(self, tmp_path: Path) -> None:
        """The propagated finding's suggestion should be tagged so the
        report consumer can distinguish "directly-found" from
        "inherited-via-dedup" findings."""
        stage = tmp_path / "stage"
        stage_p1 = stage / "p1"
        stage_p2 = stage / "p2"
        for sp in (stage_p1, stage_p2):
            (sp / "skills").mkdir(parents=True)

        canonical = stage_p1 / "skills" / "SKILL.md"
        peer = stage_p2 / "skills" / "SKILL.md"
        canonical.touch()
        dedup_map = {canonical: [canonical, peer]}

        p1_report = _build_report_with_finding(canonical, message="critical secret")
        p2_report = ValidationReport()

        _bucket_canonical_findings_into_plugins(
            {"p1": p1_report, "p2": p2_report},
            dedup_map,
            {"p1": stage_p1, "p2": stage_p2},
            {"p1": tmp_path / "cache/p1", "p2": tmp_path / "cache/p2"},
        )

        assert len(p2_report.results) == 1
        suggestion = p2_report.results[0].suggestion or ""
        assert "propagated from cross-plugin duplicate" in suggestion

    def test_canonical_owner_does_not_self_propagate(self, tmp_path: Path) -> None:
        """The canonical owner's report must NOT receive a duplicate of its
        own finding (we only propagate to PEERS, not to ourselves)."""
        stage_p1 = tmp_path / "stage/p1"
        stage_p2 = tmp_path / "stage/p2"
        for sp in (stage_p1, stage_p2):
            (sp / "skills").mkdir(parents=True)

        canonical = stage_p1 / "skills" / "x.md"
        peer = stage_p2 / "skills" / "x.md"
        canonical.touch()
        dedup_map = {canonical: [canonical, peer]}

        p1_report = _build_report_with_finding(canonical)
        p2_report = ValidationReport()

        _bucket_canonical_findings_into_plugins(
            {"p1": p1_report, "p2": p2_report},
            dedup_map,
            {"p1": stage_p1, "p2": stage_p2},
            {"p1": tmp_path / "cache/p1", "p2": tmp_path / "cache/p2"},
        )

        assert len(p1_report.results) == 1, "canonical owner unchanged"
        assert len(p2_report.results) == 1, "peer received exactly one"


class TestPathRewriting:
    def test_rewrite_paths_to_original_absolute(self, tmp_path: Path) -> None:
        """An absolute finding-path under the staged tree must be rewritten
        to the equivalent path under the original tree."""
        from validate_security import _rewrite_finding_paths_to_original

        staged_root = tmp_path / "stage/p1"
        original_root = tmp_path / "cache/p1"
        report = _build_report_with_finding(staged_root / "skills/SKILL.md")
        _rewrite_finding_paths_to_original(report, staged_root, original_root)
        assert report.results[0].file == str(original_root / "skills/SKILL.md")

    def test_rewrite_leaves_relative_paths_alone(self, tmp_path: Path) -> None:
        from validate_security import _rewrite_finding_paths_to_original

        staged_root = tmp_path / "stage/p1"
        original_root = tmp_path / "cache/p1"
        report = ValidationReport()
        report.major("test", "skills/SKILL.md", 10)  # relative path
        _rewrite_finding_paths_to_original(report, staged_root, original_root)
        # Relative path unchanged
        assert report.results[0].file == "skills/SKILL.md"

    def test_rewrite_leaves_unrelated_absolute_paths_alone(
        self, tmp_path: Path
    ) -> None:
        from validate_security import _rewrite_finding_paths_to_original

        staged_root = tmp_path / "stage/p1"
        original_root = tmp_path / "cache/p1"
        unrelated = tmp_path / "elsewhere/file.md"
        report = _build_report_with_finding(unrelated)
        _rewrite_finding_paths_to_original(report, staged_root, original_root)
        # Path unchanged because it doesn't start with staged_root
        assert report.results[0].file == str(unrelated)

    def test_rewrite_handles_findings_without_file(self) -> None:
        from validate_security import _rewrite_finding_paths_to_original

        report = ValidationReport()
        report.warning("general advisory")  # no file, no line
        _rewrite_finding_paths_to_original(
            report, Path("/stage/p1"), Path("/cache/p1")
        )
        # No file → no rewrite, no crash
        assert report.results[0].file is None
