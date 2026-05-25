#!/usr/bin/env python3
"""Two-sided regression tests for the MANAGEMENT/MARKETPLACE audit findings.

10-agent whole-plugin audit (TRDD-021250b5 follow-up),
report 20260525_101538+0200-management-marketplace.md:

#4  MINOR — tar extraction on 3.12+ must turn a tarfile.FilterError (a
            malicious entry tripping filter="data") into the SAME clean
            abort (message + cleanup + exit 1) the ZIP path produces,
            instead of a raw traceback + partial tree.
#5  MINOR — update_marketplace_json writes marketplace.json ATOMICALLY via
            save_json_safe (tmp + os.replace + backup), never a bare
            open(w) that a mid-write crash can truncate.
#6  MINOR — a self-referential / cyclic @listfile raises InputResolutionError
            (the module's clean-error contract) instead of RecursionError.
#11 NIT   — find_active_versions surfaces a warn() on a corrupt settings
            file instead of silently swallowing it.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))


def _make_tar(path: Path, entries: dict[str, bytes]) -> None:
    """Write a .tar.gz with the given {arcname: content} members."""
    with tarfile.open(path, "w:gz") as tf:
        for arcname, content in entries.items():
            info = tarfile.TarInfo(name=arcname)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))


class TestTarFilterErrorIsCleanAbort:
    """#4 - a path-traversal tar aborts cleanly; a benign tar extracts fine."""

    def test_traversal_entry_aborts_cleanly(self):
        from cpv_management_common import extract_archive

        work = Path(tempfile.mkdtemp())
        try:
            archive = work / "evil.tar.gz"
            _make_tar(archive, {"../escape.txt": b"pwned"})
            dest = work / "dest"
            dest.mkdir()
            # Both the 3.12+ filter path and the pre-3.12 manual loop must
            # exit(1) — NOT raise a raw FilterError traceback.
            with pytest.raises(SystemExit) as ei:
                extract_archive(str(archive), dest)
            assert ei.value.code == 1
            # The escaping file must NOT have landed outside dest.
            assert not (work / "escape.txt").exists()
            # Partial extraction tree is cleaned up (matches the ZIP path).
            assert not dest.exists()
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)

    def test_benign_tar_extracts(self):
        """Two-sided: a safe tar extracts its files with no SystemExit."""
        from cpv_management_common import extract_archive

        work = Path(tempfile.mkdtemp())
        try:
            archive = work / "ok.tar.gz"
            _make_tar(archive, {"a/b.txt": b"hello", "a/c.txt": b"world"})
            dest = work / "dest"
            dest.mkdir()
            extract_archive(str(archive), dest)
            assert (dest / "a" / "b.txt").read_bytes() == b"hello"
            assert (dest / "a" / "c.txt").read_bytes() == b"world"
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)


class TestMarketplaceJsonAtomicWrite:
    """#5 - update_marketplace_json uses the atomic save_json_safe helper."""

    def _make_plugin(self, root: Path) -> None:
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "version": "1.2.3", "description": "x"}),
            encoding="utf-8",
        )

    def test_wiring_uses_atomic_helper(self):
        """The function imports the SAME save_json_safe as cpv_management_common."""
        import cpv_management_common
        import update_marketplace_metadata

        assert update_marketplace_metadata.save_json_safe is cpv_management_common.save_json_safe

    def test_successful_update_leaves_no_tmp(self):
        from update_marketplace_metadata import update_marketplace_json

        work = Path(tempfile.mkdtemp())
        try:
            self._make_plugin(work)
            mkt = work / "marketplace.json"
            mkt.write_text(json.dumps({"name": "m", "plugins": []}), encoding="utf-8")
            ok, msg, updated = update_marketplace_json(work, marketplace_path=mkt, force=True)
            assert ok is True, msg
            assert updated is True
            # Atomic helper leaves no sibling .tmp behind.
            assert not (work / "marketplace.tmp").exists()
            assert not mkt.with_suffix(".tmp").exists()
            data = json.loads(mkt.read_text(encoding="utf-8"))
            assert any(p.get("name") == "demo-plugin" for p in data["plugins"])
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)

    def test_write_failure_preserves_original(self, monkeypatch):
        """Two-sided: if the atomic write raises, the original file is intact
        and the function reports failure (no truncation)."""
        import update_marketplace_metadata
        from update_marketplace_metadata import update_marketplace_json

        work = Path(tempfile.mkdtemp())
        try:
            self._make_plugin(work)
            mkt = work / "marketplace.json"
            original = json.dumps({"name": "m", "plugins": []})
            mkt.write_text(original, encoding="utf-8")

            def _boom(_path, _data, dry_run=False):
                raise OSError("disk full")

            monkeypatch.setattr(update_marketplace_metadata, "save_json_safe", _boom)
            ok, _msg, updated = update_marketplace_json(work, marketplace_path=mkt, force=True)
            assert ok is False
            assert updated is False
            # Original content untouched — no partial/truncated write.
            assert mkt.read_text(encoding="utf-8") == original
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)


class TestCyclicListFileIsCleanError:
    """#6 - a cyclic @listfile raises InputResolutionError, not RecursionError."""

    def test_self_referential_listfile(self):
        from cpv_marketplace_input import InputResolutionError, resolve

        work = Path(tempfile.mkdtemp())
        try:
            lf = work / "self.txt"
            lf.write_text(f"@{lf}\n", encoding="utf-8")
            with pytest.raises(InputResolutionError) as ei:
                resolve(f"@{lf}")
            assert "circular" in str(ei.value).lower()
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)

    def test_two_file_cycle(self):
        from cpv_marketplace_input import InputResolutionError, resolve

        work = Path(tempfile.mkdtemp())
        try:
            a = work / "a.txt"
            b = work / "b.txt"
            a.write_text(f"@{b}\n", encoding="utf-8")
            b.write_text(f"@{a}\n", encoding="utf-8")
            with pytest.raises(InputResolutionError) as ei:
                resolve(f"@{a}")
            assert "circular" in str(ei.value).lower()
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)

    def test_acyclic_listfile_still_resolves(self):
        """Two-sided: a normal (non-cyclic) @listfile resolves its entries."""
        from cpv_marketplace_input import resolve

        work = Path(tempfile.mkdtemp())
        try:
            # A real local plugin so resolution succeeds.
            plug = work / "plug"
            (plug / ".claude-plugin").mkdir(parents=True)
            (plug / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "p", "version": "1.0.0"}), encoding="utf-8"
            )
            lf = work / "list.txt"
            lf.write_text(f"# a comment\n{plug}\n", encoding="utf-8")
            out = resolve(f"@{lf}", allow_url=False)
            assert len(out) == 1
            # Compare resolved forms (macOS /var -> /private/var symlink).
            assert out[0].abs_path.resolve() == plug.resolve()
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)


class TestFindActiveVersionsSurfacesCorruptSettings:
    """#11 - a corrupt settings.json produces a warn(), not silent swallow."""

    def test_corrupt_settings_warns(self, monkeypatch, capsys):
        import manage_doctor

        work = Path(tempfile.mkdtemp())
        try:
            bad = work / "settings.json"
            bad.write_text("{ this is not json", encoding="utf-8")
            # Point the user-scope settings file at the corrupt file and the
            # cache at an empty dir so only source-2 is exercised.
            monkeypatch.setattr(manage_doctor, "SETTINGS_FILE", bad)
            empty_cache = work / "cache"
            empty_cache.mkdir()
            manage_doctor.find_active_versions(cache_root=empty_cache)
            out = capsys.readouterr().out
            assert "enabled-plugin enumeration" in out
            assert str(bad) in out
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)

    def test_valid_settings_no_warn(self, monkeypatch, capsys):
        """Two-sided: a well-formed settings file produces no enumeration warning."""
        import manage_doctor

        work = Path(tempfile.mkdtemp())
        try:
            good = work / "settings.json"
            good.write_text(json.dumps({"enabledPlugins": {}}), encoding="utf-8")
            monkeypatch.setattr(manage_doctor, "SETTINGS_FILE", good)
            empty_cache = work / "cache"
            empty_cache.mkdir()
            manage_doctor.find_active_versions(cache_root=empty_cache)
            out = capsys.readouterr().out
            assert "enabled-plugin enumeration" not in out
        finally:
            import shutil

            shutil.rmtree(work, ignore_errors=True)
