"""Regression test for the publish.py __version__ regex (v2.104.1).

v2.104.0 shipped cpv_skillaudit_native.py with::

    __version__ = "2.103.4"  # bumped in lockstep with plugin.json by publish.py

The pre-v2.104.1 regex in update_python_versions anchored ``$`` immediately
after the closing quote, so any trailing whitespace or inline comment made
the line invisible to the bumper. publish.py --minor bumped plugin.json
and pyproject.toml to 2.104.0 but left __version__ at 2.103.4. The
TestModuleVersion.test_version_matches_plugin_json integration test caught
the drift on CI.

The fix extends the regex with an optional trailing group ``(\\s*(?:#.*)?)``
captured so the suffix is preserved on rewrite. These tests pin both
behaviours: lines with AND without trailing comments must be bumped.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import publish  # noqa: E402


def _write_plugin_root(tmp_path: Path) -> Path:
    """Create a minimal plugin root with .gitignore + .claude-plugin dir."""
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / ".gitignore").write_text("", encoding="utf-8")
    (plugin_root / ".claude-plugin").mkdir()
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "x", "version": "1.0.0"}\n', encoding="utf-8"
    )
    return plugin_root


def test_bumps_bare_version_line(tmp_path: Path) -> None:
    """The simplest case — no trailing whitespace or comment."""
    plugin_root = _write_plugin_root(tmp_path)
    (plugin_root / "mod.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")

    results = publish.update_python_versions(plugin_root, "2.0.0")

    text = (plugin_root / "mod.py").read_text(encoding="utf-8")
    assert text == '__version__ = "2.0.0"\n'
    assert any("mod.py: 1.0.0 → 2.0.0" in msg for _, msg in results)


def test_bumps_version_with_trailing_comment(tmp_path: Path) -> None:
    """The regression — trailing ``# ...`` comment must not block the bump.

    Pre-v2.104.1 the regex silently skipped this exact shape, causing the
    v2.104.0 CI failure on cpv_skillaudit_native.py.
    """
    plugin_root = _write_plugin_root(tmp_path)
    (plugin_root / "mod.py").write_text(
        '__version__ = "1.0.0"  # bumped in lockstep with plugin.json\n',
        encoding="utf-8",
    )

    results = publish.update_python_versions(plugin_root, "2.0.0")

    text = (plugin_root / "mod.py").read_text(encoding="utf-8")
    assert text == '__version__ = "2.0.0"  # bumped in lockstep with plugin.json\n'
    assert any("mod.py: 1.0.0 → 2.0.0" in msg for _, msg in results)


def test_bumps_version_with_trailing_whitespace_only(tmp_path: Path) -> None:
    """Trailing whitespace (no comment) also must not block the bump."""
    plugin_root = _write_plugin_root(tmp_path)
    (plugin_root / "mod.py").write_text(
        '__version__ = "1.0.0"   \n',  # 3 trailing spaces
        encoding="utf-8",
    )

    results = publish.update_python_versions(plugin_root, "2.0.0")

    text = (plugin_root / "mod.py").read_text(encoding="utf-8")
    assert text == '__version__ = "2.0.0"   \n'
    assert any("mod.py: 1.0.0 → 2.0.0" in msg for _, msg in results)


def test_skillaudit_native_version_in_lockstep_with_plugin_json() -> None:
    """End-to-end pin: cpv_skillaudit_native.__version__ matches plugin.json.

    This is the same invariant the integration test J5 wrote — keeping a
    test in this file ensures any future regression in publish.py's bumper
    fails fast in publish.py's own test suite, not only in the integration
    suite.
    """
    import json

    repo_root = Path(__file__).resolve().parent.parent
    plugin_ver = json.loads(
        (repo_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["version"]

    import cpv_skillaudit_native  # type: ignore[import-not-found]

    assert cpv_skillaudit_native.__version__ == plugin_ver, (
        f"cpv_skillaudit_native.__version__='{cpv_skillaudit_native.__version__}' "
        f"!= plugin.json version='{plugin_ver}' — publish.py bumper missed a line"
    )
