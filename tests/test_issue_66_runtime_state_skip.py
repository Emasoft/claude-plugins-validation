#!/usr/bin/env python3
"""Regression test for GitHub issue #66 — the integrity check aborted EVERY scan
on a fresh (non-git) install because CPV's own runtime-state dirs (`.in_use/`
PID-locks, `.trashcan/`, …) were walked by the FS-walk manifest fallback and then
flagged as "added" (not-in-manifest) files.

The fix adds those runtime dirs to `_SHIPPED_WALK_SKIP_DIRS`, so the non-git walk
(and therefore the added-file detection that shares it) ignores them — exactly as
`git ls-files` already does on a source checkout.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _plugin_compute_hashes import enumerate_shipped_files  # noqa: E402
from _plugin_verify_hashes import _detect_added_files  # noqa: E402


def _make_install(tmp: Path) -> Path:
    """A NON-git plugin install dir with real files + runtime-state dirs."""
    (tmp / ".claude-plugin").mkdir()
    (tmp / ".claude-plugin" / "plugin.json").write_text('{"name":"x","version":"1.0.0"}')
    (tmp / "SKILL.md").write_text("real shipped content")
    (tmp / ".in_use").mkdir()
    (tmp / ".in_use" / "12345.lock").write_text("pid")
    (tmp / ".trashcan").mkdir()
    (tmp / ".trashcan" / "deleted.txt").write_text("x")
    (tmp / ".rechecker").mkdir()
    (tmp / ".rechecker" / "progress.json").write_text("{}")
    return tmp


def test_runtime_state_dirs_not_in_shipped_set() -> None:
    """enumerate_shipped_files (non-git walk) excludes .in_use / .trashcan / .rechecker."""
    with tempfile.TemporaryDirectory() as d:
        root = _make_install(Path(d))
        shipped = enumerate_shipped_files(root)
        assert not any(s.startswith(".in_use") for s in shipped), shipped
        assert not any(s.startswith(".trashcan") for s in shipped), shipped
        assert not any(s.startswith(".rechecker") for s in shipped), shipped
        # real files ARE shipped
        assert "SKILL.md" in shipped


def test_in_use_lock_not_flagged_as_added() -> None:
    """The #66 abort: a .in_use/ PID-lock must NOT be detected as an added file."""
    with tempfile.TemporaryDirectory() as d:
        root = _make_install(Path(d))
        manifest = {".claude-plugin/plugin.json": "h", "SKILL.md": "h"}
        added = _detect_added_files(root, manifest)
        assert added == [], f"runtime-state files wrongly flagged as added: {added}"


def test_genuine_added_file_still_detected() -> None:
    """A REAL extra file (not runtime-state) is still caught — the fix is scoped."""
    with tempfile.TemporaryDirectory() as d:
        root = _make_install(Path(d))
        (root / "inoculated.py").write_text("print('injected')")
        manifest = {".claude-plugin/plugin.json": "h", "SKILL.md": "h"}
        added = _detect_added_files(root, manifest)
        assert "inoculated.py" in added, added
