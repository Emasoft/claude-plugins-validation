"""Issue #237 — a plugin-declared `cpv.exclude_paths` skips nothing.

Two-sided: the declared exclusion no longer removes a file from the scan and the
ignored key is warned about (once, by warn_inert_cpv_keys; validate_no_absolute_paths
no longer emits its own copy), while a plugin without the key gets no warning.
CPV-internal vendored-dir names still skip UNTRACKED content and never tracked
content (task G).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_validation_common as cvc  # noqa: E402
from cpv_validation_common import ValidationReport, validate_no_absolute_paths  # noqa: E402


def _plugin(root: Path, cpv_block: dict | None) -> None:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest: dict = {"name": "p", "version": "0.0.1"}
    if cpv_block is not None:
        manifest["cpv"] = cpv_block
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    cvc._load_cpv_config_cached.cache_clear()


def _leak(root: Path) -> None:
    target = root / "hidden" / "run.sh"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("cp x /lib/secret\n", encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _is_warning(r) -> bool:
    return r.level == "WARNING" and "cpv.exclude_paths is ignored" in r.message


def test_declared_exclude_paths_does_not_skip_tracked_file(tmp_path: Path) -> None:
    """is_vendored_path ignores cpv.exclude_paths: the declared subtree is not vendored."""
    _plugin(tmp_path, {"exclude_paths": ["hidden/"]})
    _leak(tmp_path)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "hidden/run.sh")
    assert not cvc.is_vendored_path(Path("hidden/run.sh"), tmp_path)


def test_declared_exclude_paths_still_scanned_and_warned_once(tmp_path: Path) -> None:
    """The path inside the declared-excluded subtree fires; the ignored key is warned exactly once."""
    _plugin(tmp_path, {"exclude_paths": ["hidden/"]})
    _leak(tmp_path)
    report = ValidationReport()
    validate_no_absolute_paths(tmp_path, report, respect_gitignore=False)
    assert any(r.level in ("MINOR", "MAJOR") and "Absolute path found" in r.message for r in report.results)
    # The scanner itself no longer warns (one site only: warn_inert_cpv_keys).
    assert not any(_is_warning(r) for r in report.results)
    cvc.warn_inert_cpv_keys(tmp_path, report)
    assert sum(1 for r in report.results if _is_warning(r)) == 1


def test_no_key_no_warning(tmp_path: Path) -> None:
    """A plugin without cpv.exclude_paths gets no ignored-key warning."""
    _plugin(tmp_path, None)
    report = ValidationReport()
    cvc.warn_inert_cpv_keys(tmp_path, report)
    assert not any(_is_warning(r) for r in report.results)


def test_cpv_internal_vendored_names_untracked_only(tmp_path: Path) -> None:
    """CPV vendored names skip UNTRACKED content only; the same path once tracked is scanned."""
    _plugin(tmp_path, None)
    _git(tmp_path, "init", "-q")
    target = tmp_path / "node_modules" / "x" / "y.md"
    target.parent.mkdir(parents=True)
    target.write_text("y\n", encoding="utf-8")
    assert cvc.is_vendored_path(Path("node_modules/x/y.md"), tmp_path)
    _git(tmp_path, "add", "-f", "node_modules/x/y.md")
    assert not cvc.is_vendored_path(Path("node_modules/x/y.md"), tmp_path)
