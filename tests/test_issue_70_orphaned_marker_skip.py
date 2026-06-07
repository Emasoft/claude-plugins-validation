#!/usr/bin/env python3
"""Regression test for GitHub issue #70-C — an ORPHANED cached CPV version aborted
its own self-integrity check on a non-git install because the Claude Code
plugin-cache HOST drops a `.orphaned_at` marker into a superseded
`<cache>/<plugin>/<version>/` dir. The FS-walk manifest fallback then walked that
marker and flagged it as an "added/inoculated" file (not in the canonical
manifest), raising a CRITICAL integrity mismatch on an otherwise-intact version.

The fix adds the exact basename `.orphaned_at` to `_RUNTIME_CRUFT_BASENAMES`, so
the non-git walk (and the added-file detection that shares it) ignores it — the
same de-noising as the closed #66 `.in_use` PID-lock fix.

The fix is DELIBERATELY scoped: `.orphaned_at` is a host-generated, never-executed
timestamp marker that CPV never loads, so skipping this one exact basename does
NOT weaken tamper-detection of CPV's executable surface. The two-sided tests below
prove a genuine added `.py` is still caught and that the skip is an exact-basename
match (not a prefix/substring that could let an attacker smuggle a real file).
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


def _make_orphaned_install(tmp: Path) -> Path:
    """A NON-git plugin install dir marked orphaned by the host cache manager."""
    (tmp / ".claude-plugin").mkdir()
    (tmp / ".claude-plugin" / "plugin.json").write_text('{"name":"x","version":"2.122.0"}')
    (tmp / "SKILL.md").write_text("real shipped content")
    # The host drops this when a newer version supersedes 2.122.0. Real content
    # is a timestamp; the skip is by basename, so content is irrelevant.
    (tmp / ".orphaned_at").write_text("2026-06-06T19:00:00+0200")
    return tmp


def test_orphaned_marker_not_in_shipped_set() -> None:
    """enumerate_shipped_files (non-git walk) excludes the host `.orphaned_at` marker."""
    with tempfile.TemporaryDirectory() as d:
        root = _make_orphaned_install(Path(d))
        shipped = enumerate_shipped_files(root)
        assert ".orphaned_at" not in shipped, shipped
        # real files ARE still shipped
        assert "SKILL.md" in shipped


def test_orphaned_marker_not_flagged_as_added() -> None:
    """The #70-C abort: a host `.orphaned_at` marker must NOT be detected as added."""
    with tempfile.TemporaryDirectory() as d:
        root = _make_orphaned_install(Path(d))
        manifest = {".claude-plugin/plugin.json": "h", "SKILL.md": "h"}
        added = _detect_added_files(root, manifest)
        assert added == [], f".orphaned_at wrongly flagged as added: {added}"


def test_orphaned_marker_skipped_at_any_depth() -> None:
    """The basename skip applies at any depth, not just the install root."""
    with tempfile.TemporaryDirectory() as d:
        root = _make_orphaned_install(Path(d))
        nested = root / "skills" / "demo"
        nested.mkdir(parents=True)
        (nested / ".orphaned_at").write_text("2026-06-06T19:00:00+0200")
        shipped = enumerate_shipped_files(root)
        assert not any(s.endswith(".orphaned_at") for s in shipped), shipped


def test_genuine_added_file_still_detected_with_orphaned_marker_present() -> None:
    """FN-safety: a REAL inoculated file is still caught even when `.orphaned_at`
    is present — the whitelist is scoped to the marker, it does not blanket-skip."""
    with tempfile.TemporaryDirectory() as d:
        root = _make_orphaned_install(Path(d))
        (root / "inoculated.py").write_text("print('injected')")
        manifest = {".claude-plugin/plugin.json": "h", "SKILL.md": "h"}
        added = _detect_added_files(root, manifest)
        assert "inoculated.py" in added, added
        assert ".orphaned_at" not in added, added


def test_orphaned_marker_match_is_exact_basename() -> None:
    """Two-sided tightness: only the exact basename `.orphaned_at` is skipped.
    A file whose name merely contains/extends it (`orphaned_at`, `.orphaned_at.py`)
    is NOT whitelisted — so an attacker cannot smuggle a real file past the
    integrity check by giving it an `.orphaned_at`-like name."""
    with tempfile.TemporaryDirectory() as d:
        root = _make_orphaned_install(Path(d))
        (root / "orphaned_at").write_text("no leading dot — not the marker")
        (root / ".orphaned_at.py").write_text("print('payload')")
        manifest = {".claude-plugin/plugin.json": "h", "SKILL.md": "h"}
        added = _detect_added_files(root, manifest)
        assert "orphaned_at" in added, added
        assert ".orphaned_at.py" in added, added
        # …while the exact marker is still skipped
        assert ".orphaned_at" not in added, added
