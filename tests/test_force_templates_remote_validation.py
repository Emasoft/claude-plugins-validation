"""--force-templates must not re-vendor the helper into a remote-validation plugin (TRDD-5NI88T3T)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import standardize_plugin as sp  # noqa: E402

HELPER = "scripts/cpv_network_resilience.py"


def _make_plugin(root: Path, profile: str) -> Path:
    """Build a minimal real plugin tree whose manifest pins the given pipeline profile."""
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "scripts").mkdir()
    manifest = {
        "name": "demo-plugin",
        "version": "1.0.0",
        "description": "demo",
        "author": {"name": "tester"},
        "cpv": {"pipeline_profile": profile},
    }
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "scripts" / "publish.py").write_text('print("remote_validation")\n', encoding="utf-8")
    return root


def test_remote_validation_force_does_not_vendor_helper(tmp_path: Path, capsys) -> None:
    """Force-templates on a remote-validation plugin leaves the helper absent and says why."""
    plugin = _make_plugin(tmp_path / "p", "remote-validation")
    sp.fix_missing_files(plugin, sp.audit_standard_files(plugin), force_templates=True)
    assert not (plugin / HELPER).exists()
    assert "de-vendored" in capsys.readouterr().out


def test_standard_force_still_creates_helper(tmp_path: Path) -> None:
    """Force-templates on a standard plugin still writes the helper."""
    plugin = _make_plugin(tmp_path / "p", "standard")
    sp.fix_missing_files(plugin, sp.audit_standard_files(plugin), force_templates=True)
    assert (plugin / HELPER).is_file()
