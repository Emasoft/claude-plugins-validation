"""Tests for the legacy-pipeline-script detector + mover (#159).

Coverage:
- validate_plugin.validate_legacy_pipeline_scripts emits MINOR per legacy
  script, with [RC-LEGACY-PIPELINE-001] code and the canonical replacement
  in the message.
- standardize_plugin.move_legacy_pipeline_scripts moves files to scripts_dev/
  (preservation guardrail) and is idempotent across re-runs.
- The validator is silent on plugins with NO legacy scripts (no false alarms).
- The validator self-skips on the CPV plugin itself (CPV is the canonical
  source; the listed files don't ship in CPV's scripts/).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import standardize_plugin  # noqa: E402
import validate_plugin  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _make_plugin(tmp_path: Path, name: str = "demo-plugin") -> Path:
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "description": "x", "author": {"name": "t", "email": "t@e.com"}}),
        encoding="utf-8",
    )
    (root / "scripts").mkdir()
    return root


# ── validate_legacy_pipeline_scripts ─────────────────────────────────────────


class TestValidateLegacyPipelineScripts:
    """The detector must surface every legacy script with the right code."""

    def test_clean_plugin_no_findings(self, tmp_path: Path):
        """A plugin with zero legacy scripts → no findings."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_plugin.validate_legacy_pipeline_scripts(plugin, report)
        legacy = [r for r in report.results if "RC-LEGACY-PIPELINE-001" in r.message]
        assert legacy == []

    def test_bump_version_py_flagged(self, tmp_path: Path):
        """`scripts/bump_version.py` → one MINOR finding mentioning publish.py."""
        plugin = _make_plugin(tmp_path)
        (plugin / "scripts" / "bump_version.py").write_text("# legacy\n", encoding="utf-8")
        report = ValidationReport()
        validate_plugin.validate_legacy_pipeline_scripts(plugin, report)
        legacy = [r for r in report.results if "RC-LEGACY-PIPELINE-001" in r.message]
        assert len(legacy) == 1
        assert legacy[0].level == "MINOR"
        assert "bump_version.py" in legacy[0].message
        assert "publish.py" in legacy[0].message
        # The fixer hint must be present so the user knows what to do.
        assert "/cpv-upgrade-plugin" in legacy[0].message

    def test_multiple_legacy_scripts_each_flagged(self, tmp_path: Path):
        """Three legacy scripts → three independent MINOR findings."""
        plugin = _make_plugin(tmp_path)
        (plugin / "scripts" / "release.sh").write_text("#!/bin/bash\necho release\n", encoding="utf-8")
        (plugin / "scripts" / "lint.sh").write_text("#!/bin/bash\nruff check .\n", encoding="utf-8")
        (plugin / "scripts" / "compute_hashes.py").write_text("# legacy\n", encoding="utf-8")
        report = ValidationReport()
        validate_plugin.validate_legacy_pipeline_scripts(plugin, report)
        legacy = [r for r in report.results if "RC-LEGACY-PIPELINE-001" in r.message]
        names = sorted([next(part for part in r.message.split("`") if "/" in part) for r in legacy])
        assert names == ["scripts/compute_hashes.py", "scripts/lint.sh", "scripts/release.sh"]
        for r in legacy:
            assert r.level == "MINOR"

    def test_preservation_guardrail_in_message(self, tmp_path: Path):
        """Message must explicitly mention "moved" not deletion (preservation
        guardrail per the user's feedback "be careful with purging dead code")."""
        plugin = _make_plugin(tmp_path)
        (plugin / "scripts" / "release.sh").write_text("x\n", encoding="utf-8")
        report = ValidationReport()
        validate_plugin.validate_legacy_pipeline_scripts(plugin, report)
        msg = next(r.message for r in report.results if "RC-LEGACY-PIPELINE-001" in r.message)
        assert "scripts_dev/" in msg
        assert "moved" in msg.lower()

    def test_cpv_self_scan_skipped(self, tmp_path: Path):
        """When plugin.json::name == claude-plugins-validation, the rule
        self-skips even if a legacy file is dropped in (CPV doesn't have these
        in its real scripts/, but the early-return keeps the rule cheap)."""
        plugin = _make_plugin(tmp_path, name="claude-plugins-validation")
        (plugin / "scripts" / "bump_version.py").write_text("# would normally fire\n", encoding="utf-8")
        report = ValidationReport()
        validate_plugin.validate_legacy_pipeline_scripts(plugin, report)
        legacy = [r for r in report.results if "RC-LEGACY-PIPELINE-001" in r.message]
        assert legacy == []


# ── move_legacy_pipeline_scripts ─────────────────────────────────────────────


class TestMoveLegacyPipelineScripts:
    """The mover must move (not delete) and must be idempotent."""

    def test_no_legacy_scripts_returns_empty(self, tmp_path: Path):
        """No legacy files → no moves, no scripts_dev/ folder created."""
        plugin = _make_plugin(tmp_path)
        moved = standardize_plugin.move_legacy_pipeline_scripts(plugin)
        assert moved == []
        assert not (plugin / "scripts_dev").exists()

    def test_moves_to_scripts_dev(self, tmp_path: Path):
        """A legacy script is MOVED — gone from scripts/, present in scripts_dev/."""
        plugin = _make_plugin(tmp_path)
        legacy = plugin / "scripts" / "release.sh"
        legacy.write_text("#!/bin/bash\necho release\n", encoding="utf-8")
        moved = standardize_plugin.move_legacy_pipeline_scripts(plugin)
        assert moved == ["scripts/release.sh"]
        assert not legacy.exists()
        moved_path = plugin / "scripts_dev" / "release.sh"
        assert moved_path.is_file()
        assert moved_path.read_text(encoding="utf-8") == "#!/bin/bash\necho release\n"

    def test_idempotent_re_run_uses_suffix(self, tmp_path: Path):
        """Re-running with a new legacy file of the same name → suffixed dest."""
        plugin = _make_plugin(tmp_path)
        # First run.
        (plugin / "scripts" / "release.sh").write_text("v1\n", encoding="utf-8")
        first = standardize_plugin.move_legacy_pipeline_scripts(plugin)
        assert first == ["scripts/release.sh"]
        # User re-creates a NEW release.sh (e.g. they downloaded an old release
        # tarball into scripts/). Second run must NOT overwrite the first move.
        (plugin / "scripts" / "release.sh").write_text("v2\n", encoding="utf-8")
        second = standardize_plugin.move_legacy_pipeline_scripts(plugin)
        assert second == ["scripts/release.sh"]
        # Both versions preserved.
        assert (plugin / "scripts_dev" / "release.sh").read_text() == "v1\n"
        assert (plugin / "scripts_dev" / "release.sh.1").read_text() == "v2\n"

    def test_dry_run_makes_no_changes(self, tmp_path: Path):
        """`--dry-run`: print only, no moves."""
        plugin = _make_plugin(tmp_path)
        legacy = plugin / "scripts" / "bump_version.py"
        legacy.write_text("legacy\n", encoding="utf-8")
        moved = standardize_plugin.move_legacy_pipeline_scripts(plugin, dry_run=True)
        assert moved == ["scripts/bump_version.py"]
        # Original still present, scripts_dev/ NOT created.
        assert legacy.is_file()
        assert not (plugin / "scripts_dev").exists()

    def test_full_legacy_set_moved(self, tmp_path: Path):
        """All 12 known-legacy scripts get moved cleanly when present."""
        plugin = _make_plugin(tmp_path)
        for rel in standardize_plugin._LEGACY_PIPELINE_SCRIPTS_RELPATHS:
            target = plugin / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("legacy\n", encoding="utf-8")
        moved = standardize_plugin.move_legacy_pipeline_scripts(plugin)
        assert sorted(moved) == sorted(standardize_plugin._LEGACY_PIPELINE_SCRIPTS_RELPATHS)
        for rel in standardize_plugin._LEGACY_PIPELINE_SCRIPTS_RELPATHS:
            assert not (plugin / rel).exists()
            assert (plugin / "scripts_dev" / Path(rel).name).is_file()
