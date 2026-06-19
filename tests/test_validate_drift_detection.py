#!/usr/bin/env python3
"""Tests for the drift / submodule / lockfile validators wired into
validate_plugin.py (TRDD-79638eb6 — Parts 3 + 4 + audit_drift).

Covers:
- is_plugin_in_submodule: positive (registered submodule), negative
  (standalone plugin), bottom-of-fs walk (no .gitmodules anywhere),
  malformed .gitmodules.
- validate_submodule_containment: emits exactly one INFO when the plugin
  is a submodule, no INFO otherwise.
- validate_project_languages: INFO summary lists every detected language;
  emits a different INFO for empty trees.
- validate_lockfiles: NIT for orphan lockfile (lockfile with no matching
  language), WARNING for gitignored lockfile, neither for a healthy
  matched lockfile, AND ts/js lockfile-language equivalence.
- audit_drift: WARN AuditItem per declared-but-unimported dep, exempts
  the runtime-tools allowlist, PASS when every dep is referenced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts/ importable
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_validation_common import ValidationReport  # noqa: E402
from standardize_plugin import _scan_python_imports, audit_drift  # noqa: E402
from validate_plugin import (  # noqa: E402
    _lockfile_is_gitignored,
    is_plugin_in_submodule,
    validate_lockfiles,
    validate_project_languages,
    validate_submodule_containment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _info_count(report: ValidationReport) -> int:
    """Return the number of INFO-level results in a report."""
    return sum(1 for r in report.results if r.level == "INFO")


def _info_messages(report: ValidationReport) -> list[str]:
    """Return all INFO-level messages from a report."""
    return [r.message for r in report.results if r.level == "INFO"]


def _nit_messages(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "NIT"]


def _warning_messages(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "WARNING"]


# ---------------------------------------------------------------------------
# Part 3 — Submodule detection
# ---------------------------------------------------------------------------


class TestIsPluginInSubmodule:
    """Direct tests on the helper that walks up the parent chain."""

    def test_registered_submodule_is_detected(self, tmp_path):
        """Plugin path matching a 'path = ' entry in parent .gitmodules is detected."""
        parent = tmp_path / "parent-repo"
        parent.mkdir()
        plugin = parent / "vendor" / "my-plugin"
        plugin.mkdir(parents=True)
        (parent / ".gitmodules").write_text(
            '[submodule "my-plugin"]\n    path = vendor/my-plugin\n    url = https://example.com/x.git\n'
        )
        result = is_plugin_in_submodule(plugin)
        assert result is not None
        assert result.resolve() == parent.resolve()

    def test_standalone_plugin_returns_none(self, tmp_path):
        """A plugin whose ancestors have no .gitmodules returns None."""
        plugin = tmp_path / "standalone"
        plugin.mkdir()
        result = is_plugin_in_submodule(plugin)
        assert result is None

    def test_unrelated_gitmodules_returns_none(self, tmp_path):
        """An ancestor .gitmodules that lists OTHER submodule paths returns None."""
        parent = tmp_path / "parent-repo"
        parent.mkdir()
        plugin = parent / "vendor" / "my-plugin"
        plugin.mkdir(parents=True)
        (parent / ".gitmodules").write_text(
            '[submodule "other"]\n    path = vendor/some-other-thing\n    url = https://example.com/y.git\n'
        )
        result = is_plugin_in_submodule(plugin)
        assert result is None

    def test_malformed_gitmodules_does_not_raise(self, tmp_path):
        """Garbage content in .gitmodules is ignored, no exception."""
        parent = tmp_path / "parent-repo"
        parent.mkdir()
        plugin = parent / "vendor" / "my-plugin"
        plugin.mkdir(parents=True)
        (parent / ".gitmodules").write_text("\x00not\x00binary\x00garbage\x00")
        # Should return None silently, not raise
        result = is_plugin_in_submodule(plugin)
        assert result is None

    def test_unreadable_gitmodules_returns_none(self, tmp_path):
        """If .gitmodules cannot be opened, return None instead of raising."""
        # Simulate by passing a non-existent absolute path that triggers OSError
        # on resolve(). We can't easily make a real unreadable file in CI, so
        # we just confirm a missing parent walk works.
        plugin = tmp_path / "not-real-anywhere"
        # Don't even mkdir — the function should still cope.
        # NOTE: resolve() on a path that doesn't exist is a no-op on POSIX,
        # so this just exercises the missing-.gitmodules cascade.
        result = is_plugin_in_submodule(plugin)
        assert result is None

    def test_grandparent_gitmodules_is_walked(self, tmp_path):
        """The walk continues up multiple levels until .gitmodules is found."""
        grandparent = tmp_path / "grand"
        grandparent.mkdir()
        parent = grandparent / "vendor"
        parent.mkdir()
        plugin = parent / "deeper" / "my-plugin"
        plugin.mkdir(parents=True)
        # Submodule registered at the grandparent, not the immediate parent
        (grandparent / ".gitmodules").write_text(
            '[submodule "p"]\n    path = vendor/deeper/my-plugin\n    url = https://example.com/x.git\n'
        )
        result = is_plugin_in_submodule(plugin)
        assert result is not None
        assert result.resolve() == grandparent.resolve()


class TestValidateSubmoduleContainment:
    """The wrapper that emits an INFO line when a plugin is a submodule."""

    def test_submodule_emits_info(self, tmp_path):
        """When is_plugin_in_submodule returns a parent, an INFO is recorded."""
        parent = tmp_path / "parent-repo"
        parent.mkdir()
        plugin = parent / "vendor" / "my-plugin"
        plugin.mkdir(parents=True)
        (parent / ".gitmodules").write_text('[submodule "x"]\n    path = vendor/my-plugin\n    url = https://x\n')

        report = ValidationReport()
        validate_submodule_containment(plugin, report)
        msgs = _info_messages(report)
        assert any("submodule" in m.lower() for m in msgs)
        assert any("parent-repo" in m for m in msgs)

    def test_standalone_emits_no_info(self, tmp_path):
        """A plugin not in a submodule emits no INFO (the validator stays quiet)."""
        plugin = tmp_path / "standalone"
        plugin.mkdir()
        report = ValidationReport()
        validate_submodule_containment(plugin, report)
        # Submodule containment is silent for the common (non-submodule) case.
        assert all("submodule" not in m.lower() for m in _info_messages(report))


# ---------------------------------------------------------------------------
# Part 1+3 wired-in — language detection
# ---------------------------------------------------------------------------


class TestValidateProjectLanguages:
    """The wrapper that emits the 'Detected project languages: ...' INFO."""

    def test_python_plugin_emits_python_info(self, tmp_path):
        """A pyproject.toml-only tree emits an INFO mentioning 'python'."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        report = ValidationReport()
        result = validate_project_languages(tmp_path, report)
        assert "python" in result
        msgs = _info_messages(report)
        assert any("python" in m for m in msgs)
        assert any("pyproject.toml" in m for m in msgs)

    def test_no_languages_emits_default_info(self, tmp_path):
        """An empty tree emits the 'No language markers detected' INFO."""
        report = ValidationReport()
        result = validate_project_languages(tmp_path, report)
        assert result == {}
        msgs = _info_messages(report)
        assert any("No language markers detected" in m for m in msgs)

    def test_polyglot_summary_lists_each_language(self, tmp_path):
        """A polyglot tree's INFO mentions each detected language."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "package.json").write_text('{"name":"x"}')
        (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
        report = ValidationReport()
        result = validate_project_languages(tmp_path, report)
        assert {"python", "js", "rust"}.issubset(result.keys())
        joined = " ".join(_info_messages(report))
        for lang in ("python", "js", "rust"):
            assert lang in joined


# ---------------------------------------------------------------------------
# Part 2 wired-in — lockfile detection
# ---------------------------------------------------------------------------


class TestValidateLockfilesOrphan:
    """A lockfile without its corresponding language emits a NIT."""

    def test_uv_lock_without_python_is_orphan(self, tmp_path):
        """uv.lock with no pyproject/setup/requirements emits a NIT."""
        (tmp_path / "uv.lock").write_text("")
        report = ValidationReport()
        validate_lockfiles(tmp_path, report, detected_languages={})
        msgs = _nit_messages(report)
        assert any("uv.lock" in m and "orphan" in m.lower() for m in msgs)

    def test_matched_lockfile_emits_no_nit(self, tmp_path):
        """uv.lock + pyproject.toml = no orphan NIT."""
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        # Simulate detected_languages
        detected = {"python": tmp_path / "pyproject.toml"}
        report = ValidationReport()
        validate_lockfiles(tmp_path, report, detected_languages=detected)
        # No NIT mentioning orphan
        msgs = _nit_messages(report)
        assert all("orphan" not in m.lower() for m in msgs)

    def test_js_lockfile_matches_ts_language(self, tmp_path):
        """package-lock.json with a TypeScript-only project (ts but no js) is NOT orphan.

        The js/ts lockfile equivalence rule lives in validate_lockfiles —
        a TS project legitimately uses the JS lockfiles.
        """
        (tmp_path / "package-lock.json").write_text("{}")
        # Simulate a TS-only language detection (e.g. tsconfig.json + .ts files)
        detected = {"ts": tmp_path / "tsconfig.json"}
        report = ValidationReport()
        validate_lockfiles(tmp_path, report, detected_languages=detected)
        msgs = _nit_messages(report)
        assert all(not (m.startswith("Lockfile package-lock.json") and "orphan" in m.lower()) for m in msgs)


class TestValidateLockfilesGitignored:
    """A lockfile listed in .gitignore emits a WARNING."""

    def test_uv_lock_in_gitignore_emits_warning(self, tmp_path):
        """uv.lock with `uv.lock` in .gitignore triggers a WARNING."""
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / ".gitignore").write_text("uv.lock\n")
        # Provide matching language so we don't also get an orphan NIT
        detected = {"python": tmp_path / "pyproject.toml"}
        report = ValidationReport()
        validate_lockfiles(tmp_path, report, detected_languages=detected)
        msgs = _warning_messages(report)
        assert any("uv.lock" in m and ".gitignore" in m for m in msgs)

    def test_glob_in_gitignore_emits_warning(self, tmp_path):
        """A glob pattern like `*.lock` covering uv.lock triggers a WARNING."""
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / ".gitignore").write_text("*.lock\n")
        detected = {"python": tmp_path / "pyproject.toml"}
        report = ValidationReport()
        validate_lockfiles(tmp_path, report, detected_languages=detected)
        msgs = _warning_messages(report)
        assert any("uv.lock" in m for m in msgs)

    def test_no_gitignore_no_warning(self, tmp_path):
        """A matched lockfile in a tree with no .gitignore emits no WARNING."""
        (tmp_path / "uv.lock").write_text("")
        detected = {"python": tmp_path / "pyproject.toml"}
        report = ValidationReport()
        validate_lockfiles(tmp_path, report, detected_languages=detected)
        msgs = _warning_messages(report)
        assert all("gitignore" not in m.lower() for m in msgs)

    def test_anchored_slash_pattern_is_recognized(self, tmp_path):
        """`/Cargo.lock` (anchored to repo root) still matches Cargo.lock."""
        (tmp_path / "Cargo.lock").write_text("")
        (tmp_path / ".gitignore").write_text("/Cargo.lock\n")
        detected = {"rust": tmp_path / "Cargo.toml"}
        report = ValidationReport()
        validate_lockfiles(tmp_path, report, detected_languages=detected)
        msgs = _warning_messages(report)
        assert any("Cargo.lock" in m for m in msgs)


class TestLockfileIsGitignoredHelper:
    """The internal _lockfile_is_gitignored helper covers the pattern matching."""

    def test_exact_match(self):
        """An exact filename match returns True."""
        assert _lockfile_is_gitignored("uv.lock", ["uv.lock"])

    def test_case_insensitive_match(self):
        """A pattern whose case differs from the filename still matches."""
        assert _lockfile_is_gitignored("uv.lock", ["UV.LOCK"])

    def test_glob_match(self):
        """fnmatch globs cover wildcard patterns like *.lock."""
        assert _lockfile_is_gitignored("uv.lock", ["*.lock"])

    def test_no_match(self):
        """An unrelated pattern does NOT match."""
        assert not _lockfile_is_gitignored("uv.lock", ["*.tmp", "build/"])

    def test_anchored_slash(self):
        """A leading / is stripped (anchored basename match)."""
        assert _lockfile_is_gitignored("Cargo.lock", ["/Cargo.lock"])

    def test_empty_patterns(self):
        """An empty pattern list returns False."""
        assert not _lockfile_is_gitignored("uv.lock", [])

    def test_blank_pattern_is_skipped(self):
        """A blank pattern (just a slash) is silently skipped."""
        assert not _lockfile_is_gitignored("uv.lock", ["/"])


# ---------------------------------------------------------------------------
# Part 4 — audit_drift in standardize_plugin.py
# ---------------------------------------------------------------------------


class TestAuditDrift:
    """Cross-check declared deps against actual scripts/ + hooks/ imports."""

    def test_unused_dep_emits_warn(self, tmp_path):
        """A dep declared but not imported anywhere emits one WARN AuditItem."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\ndependencies = ['requests>=2.30']\n")
        # No scripts/ or hooks/ imports referencing requests
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "main.py").write_text("import json\n")
        items = audit_drift(tmp_path)
        warns = [it for it in items if it.status == "WARN"]
        assert len(warns) == 1
        assert "requests" in warns[0].message

    def test_used_dep_emits_no_warn(self, tmp_path):
        """A dep imported in scripts/ produces a PASS, not a WARN."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\ndependencies = ['requests>=2.30']\n")
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "main.py").write_text("import requests\n")
        items = audit_drift(tmp_path)
        warns = [it for it in items if it.status == "WARN"]
        passes = [it for it in items if it.status == "PASS"]
        assert warns == []
        assert len(passes) == 1
        assert "All 1 declared dependencies" in passes[0].message

    def test_runtime_tool_dep_is_exempt(self, tmp_path):
        """ruff / pytest / mypy etc. don't get drift-checked even if not imported."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\ndependencies = ['ruff', 'pytest', 'mypy']\n")
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "main.py").write_text("import json\n")
        items = audit_drift(tmp_path)
        warns = [it for it in items if it.status == "WARN"]
        # All three are in the runtime-tools allowlist, so no WARN
        assert warns == []
        # And the PASS summary still fires
        assert any(it.status == "PASS" for it in items)

    def test_dist_to_module_mapping_recognized(self, tmp_path):
        """A dep like 'pyyaml' that imports as 'yaml' is correctly matched."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\ndependencies = ['pyyaml>=6.0']\n")
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "main.py").write_text("import yaml\n")
        items = audit_drift(tmp_path)
        warns = [it for it in items if it.status == "WARN"]
        # No WARN — the pyyaml -> yaml mapping is recognized
        assert warns == []

    def test_hooks_dir_imports_count_too(self, tmp_path):
        """A dep imported in hooks/ (not scripts/) is also recognized."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\ndependencies = ['requests']\n")
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "pre.py").write_text("import requests\n")
        items = audit_drift(tmp_path)
        warns = [it for it in items if it.status == "WARN"]
        assert warns == []

    def test_no_pyproject_returns_empty(self, tmp_path):
        """A plugin without pyproject.toml returns no audit items (silent)."""
        items = audit_drift(tmp_path)
        assert items == []

    def test_optional_dependencies_included(self, tmp_path):
        """[project.optional-dependencies] groups also get drift-checked."""
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname='x'\ndependencies = []\n[project.optional-dependencies]\ndev = ['requests']\n"
        )
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "main.py").write_text("import json\n")
        items = audit_drift(tmp_path)
        warns = [it for it in items if it.status == "WARN"]
        assert any("requests" in w.message for w in warns)


# ---------------------------------------------------------------------------
# _scan_python_imports — the import regex was rewritten to a provably-linear
# single char class (no nested-quantifier group that trips the skillaudit
# REGEX_DOS heuristic). This locks the parsing behavior, which the rewrite also
# made MORE correct (as-aliases, multi-name lists, comments, semicolons). The
# REGEX_DOS-safety itself is guarded by the publish gate, which scans this
# module's source every release.
# ---------------------------------------------------------------------------


def test_scan_imports_handles_aliases_lists_comments_semicolons(tmp_path):
    """Import scan resolves top-level modules across every statement shape."""
    sc = tmp_path / "scripts"
    sc.mkdir()
    (sc / "m.py").write_text(
        "import foo\n"
        "from bar.baz import qux\n"
        "import one, two.sub, three\n"
        "import aliased as al\n"
        "import multi as m, second as s\n"
        "import withcomment  # trailing, comment, with commas\n"
        "import semi; import colon\n",
        encoding="utf-8",
    )
    found = _scan_python_imports(tmp_path)
    # every imported top-level package is registered...
    for pkg in ("foo", "bar", "one", "two", "three", "aliased", "multi", "second", "withcomment", "semi"):
        assert pkg in found, f"{pkg!r} missing from {sorted(found)}"
    # ...and comment words / alias keywords never leak in as fake modules.
    for noise in ("comment", "commas", "as", "al", "trailing", "with"):
        assert noise not in found, f"{noise!r} wrongly captured in {sorted(found)}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
