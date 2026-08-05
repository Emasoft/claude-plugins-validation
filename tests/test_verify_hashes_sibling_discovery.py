#!/usr/bin/env python3
"""The integrity-failure recovery hint must only suggest REAL sibling cached versions.

Regression tests for `_discover_sibling_cached_versions` in
`scripts/_plugin_verify_hashes.py`. The old hint builder treated EVERY sibling
directory of `plugin_root.parent` as a "cached version" and printed
`python3 "<sib>/scripts/remote_validation.py" ...` without checking the file
exists. Correct in the plugin-cache layout
(`<cache>/<marketplace>/<plugin>/<version>/`), garbage in a dev checkout whose
parent is an arbitrary workspace: non-existent launcher paths inside
agent-writable dirs (reports/, docs_dev/, thoughts/) were endorsed as runnable
recovery commands. Two gates now apply: the sibling's NAME must be a dotted
numeric version AND the launcher file must actually exist.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from _plugin_verify_hashes import _discover_sibling_cached_versions  # noqa: E402


def _mk_sibling(parent: Path, name: str, *, launcher: bool) -> Path:
    d = parent / name
    if launcher:
        (d / "scripts").mkdir(parents=True)
        (d / "scripts" / "remote_validation.py").write_text("# launcher\n")
    else:
        d.mkdir(parents=True)
    return d


def _mk_plugin_root(parent: Path, name: str = "5.1.4") -> Path:
    root = parent / name
    root.mkdir(parents=True)
    return root


def test_cache_layout_siblings_with_launcher_are_listed_newest_first(tmp_path):
    """Real cache layout: version-named siblings shipping the launcher are returned, newest first."""
    root = _mk_plugin_root(tmp_path)
    _mk_sibling(tmp_path, "5.1.2", launcher=True)
    _mk_sibling(tmp_path, "5.1.3", launcher=True)
    got = _discover_sibling_cached_versions(root)
    assert [p.name for p in got] == ["5.1.3", "5.1.2"]


def test_workspace_layout_dirs_are_never_listed(tmp_path):
    """Dev-checkout layout: workspace sibling dirs (reports/, docs_dev/, thoughts/) yield nothing."""
    root = _mk_plugin_root(tmp_path, name="claude-plugins-validation")
    for name in ("reports", "reports_dev", "docs_dev", "thoughts", "tests"):
        _mk_sibling(tmp_path, name, launcher=False)
    assert _discover_sibling_cached_versions(root) == []


def test_version_named_sibling_without_launcher_is_excluded(tmp_path):
    """A version-named sibling that does NOT ship the launcher is excluded (existence gate)."""
    root = _mk_plugin_root(tmp_path)
    _mk_sibling(tmp_path, "5.1.3", launcher=False)
    assert _discover_sibling_cached_versions(root) == []


def test_non_version_named_sibling_with_launcher_is_excluded(tmp_path):
    """A non-version-named sibling is excluded even when it ships the launcher (name gate)."""
    root = _mk_plugin_root(tmp_path)
    _mk_sibling(tmp_path, "reports", launcher=True)
    assert _discover_sibling_cached_versions(root) == []


def test_sort_is_numeric_not_lexicographic(tmp_path):
    """Version ordering is numeric: 10.0.0 sorts ahead of 9.0.0 (lexicographic would invert it)."""
    root = _mk_plugin_root(tmp_path, name="10.1.0")
    _mk_sibling(tmp_path, "9.0.0", launcher=True)
    _mk_sibling(tmp_path, "10.0.0", launcher=True)
    got = _discover_sibling_cached_versions(root)
    assert [p.name for p in got] == ["10.0.0", "9.0.0"]


def test_plugin_root_itself_is_excluded(tmp_path):
    """The plugin root's own dir never lists itself, even though it matches both gates."""
    root = _mk_plugin_root(tmp_path)
    (root / "scripts").mkdir()
    (root / "scripts" / "remote_validation.py").write_text("# launcher\n")
    assert _discover_sibling_cached_versions(root) == []


def test_launcher_that_is_a_directory_is_excluded(tmp_path):
    """A directory named remote_validation.py does not satisfy the launcher-existence gate."""
    root = _mk_plugin_root(tmp_path)
    sib = tmp_path / "5.1.3"
    (sib / "scripts" / "remote_validation.py").mkdir(parents=True)
    assert _discover_sibling_cached_versions(root) == []


def test_unreadable_parent_yields_empty_not_crash(tmp_path):
    """A plugin_root whose parent cannot be iterated returns [] instead of raising."""
    ghost = tmp_path / "does-not-exist" / "5.1.4"
    assert _discover_sibling_cached_versions(ghost) == []
