#!/usr/bin/env python3
"""RC-DEP-TAG-* — detect a plugin that cannot be DEPENDED UPON.

Since Claude Code 2.1.110 a version-constrained dependency is resolved by listing
the dependency repo's tags, keeping only those starting with ``{name}--v``, and
fetching the highest satisfying the range. A plain ``vX.Y.Z`` tag is IGNORED.

A plugin publishing only ``vX.Y.Z`` therefore cannot be depended upon: every
dependent fails with ``no-matching-tag`` and is DISABLED — and it is invisible
until a clean install, because an installed dependent keeps working. That is how
it silently broke a real plugin pair for months (#163, claude-menu-system#2).

Both signals are exercised through the REAL check. Each "fires" case is paired
with a "stays quiet" case, so neither a dead detector nor a noisy one can pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _findings(root: Path) -> list[str]:
    from cpv_validation_common import ValidationReport  # type: ignore[import-not-found]
    from validate_plugin import check_dependency_resolution_tags  # type: ignore[import-not-found]

    report = ValidationReport()
    check_dependency_resolution_tags(root, report)
    return [
        str(getattr(r, "message", r)) for r in report.results if "RC-DEP-TAG" in str(getattr(r, "message", r))
    ]


def _plugin(tmp_path: Path, name: str = "demo-plugin") -> Path:
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "description": "d"}), encoding="utf-8"
    )
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def _git_repo(root: Path, tags: list[str]) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("x", encoding="utf-8")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-qm", "c")
    for t in tags:
        _git(root, "tag", t)


# ── Signal 1: the pipeline never emits the dependency tag ────────────────


class TestPipelineSignal:
    def test_publish_py_tagging_only_plain_version_fires(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path)
        (root / "scripts").mkdir()
        (root / "scripts" / "publish.py").write_text(
            'run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], cwd=root)\n', encoding="utf-8"
        )
        found = _findings(root)
        assert [f for f in found if "RC-DEP-TAG-PIPELINE" in f], found

    def test_publish_py_that_also_tags_the_dependency_ref_is_quiet(self, tmp_path: Path) -> None:
        """The fixed canonical pipeline must NOT be flagged."""
        root = _plugin(tmp_path)
        (root / "scripts").mkdir()
        (root / "scripts" / "publish.py").write_text(
            'dep_tag = _dependency_tag_name(root, new_ver)  # "{name}--v{ver}"\n'
            'run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], cwd=root)\n'
            'run(["git", "tag", "-a", dep_tag, "-m", "dep"], cwd=root)\n',
            encoding="utf-8",
        )
        assert [f for f in _findings(root) if "RC-DEP-TAG-PIPELINE" in f] == []

    def test_plugin_without_publish_py_is_quiet(self, tmp_path: Path) -> None:
        """Not every plugin uses the canonical pipeline — do not invent noise."""
        assert [f for f in _findings(_plugin(tmp_path)) if "RC-DEP-TAG-PIPELINE" in f] == []


# ── Signal 2: released, but no resolvable tag ────────────────────────────


class TestTagSignal:
    def test_releases_without_dependency_tag_fire(self, tmp_path: Path) -> None:
        """The exact real-world shape: v0.1.6 / v0.2.0 and nothing resolvable."""
        root = _plugin(tmp_path, "claude-menu-system")
        _git_repo(root, ["v0.1.6", "v0.2.0"])
        found = [f for f in _findings(root) if "RC-DEP-TAG-MISSING" in f]
        assert found, _findings(root)
        assert "claude-menu-system--v" in found[0]

    def test_single_hyphen_near_miss_is_named(self, tmp_path: Path) -> None:
        """`name-v1.2.3` LOOKS right but resolves nothing — say so explicitly."""
        root = _plugin(tmp_path, "perfect-skill-suggester")
        _git_repo(root, ["v1.9.0", "perfect-skill-suggester-v1.9.0"])
        found = [f for f in _findings(root) if "RC-DEP-TAG-MISSING" in f]
        assert found, _findings(root)
        assert "SINGLE hyphen" in found[0]
        assert "DOUBLE hyphen" in found[0]

    def test_correct_dependency_tag_is_quiet(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path, "claude-menu-system")
        _git_repo(root, ["v0.2.0", "claude-menu-system--v0.2.0"])
        assert [f for f in _findings(root) if "RC-DEP-TAG-MISSING" in f] == []

    def test_repo_with_no_releases_is_quiet(self, tmp_path: Path) -> None:
        """Nothing released yet → nothing to resolve against → no noise."""
        root = _plugin(tmp_path)
        _git_repo(root, [])
        assert [f for f in _findings(root) if "RC-DEP-TAG-MISSING" in f] == []

    def test_non_git_source_is_quiet(self, tmp_path: Path) -> None:
        """CPV scans UNINSTALLED, tag-less sources — the tag signal must stay silent."""
        assert [f for f in _findings(_plugin(tmp_path)) if "RC-DEP-TAG-MISSING" in f] == []


class TestCanonicalTemplateEmitsTheDependencyTag:
    """The ROOT fix: every plugin CPV scaffolds/migrates must tag both refs.

    The detector above catches an already-broken plugin; this stops CPV from
    GENERATING broken ones. Without it, the bug is re-seeded into every new plugin.
    """

    def _generated(self) -> str:
        from generate_plugin_repo import PluginParams, gen_publish_py  # type: ignore[import-not-found]

        return gen_publish_py(
            PluginParams(
                name="demo-plugin",
                description="d",
                author="A",
                author_email="a@b.c",
                license="MIT",
                python_version="3.12",
                github_owner="Emasoft",
                marketplace="emasoft-plugins",
            )
        )

    def test_template_compiles(self, tmp_path: Path) -> None:
        import py_compile

        out = tmp_path / "publish.py"
        out.write_text(self._generated(), encoding="utf-8")
        py_compile.compile(str(out), doraise=True)

    def test_template_derives_and_pushes_the_dependency_tag(self, tmp_path: Path) -> None:
        """Execute the EMITTED code — not just grep it for a substring."""
        import importlib.util

        out = tmp_path / "gen_publish.py"
        out.write_text(self._generated(), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("gen_publish_under_test", out)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["gen_publish_under_test"] = mod
        spec.loader.exec_module(mod)

        root = _plugin(tmp_path, "demo-plugin")
        assert mod._dependency_tag_name(root, "1.0.1") == "demo-plugin--v1.0.1"
        # No manifest -> no invented name (the caller warns and skips).
        assert mod._dependency_tag_name(tmp_path / "nope", "1.0.1") is None

    def test_template_would_not_be_flagged_by_our_own_detector(self, tmp_path: Path) -> None:
        """Close the loop: the pipeline CPV generates must pass CPV's own check."""
        root = _plugin(tmp_path, "demo-plugin")
        (root / "scripts").mkdir()
        (root / "scripts" / "publish.py").write_text(self._generated(), encoding="utf-8")
        assert [f for f in _findings(root) if "RC-DEP-TAG-PIPELINE" in f] == []


class TestNonBlocking:
    def test_findings_are_warnings_and_never_block_strict(self, tmp_path: Path) -> None:
        """WARN-only, on purpose.

        Claude Code does not require the tag for a plugin nobody depends on, and CPV
        must not invent a publish gate the spec does not have. The finding must be
        visible without failing --strict.
        """
        from cpv_validation_common import ValidationReport  # type: ignore[import-not-found]
        from validate_plugin import check_dependency_resolution_tags  # type: ignore[import-not-found]

        root = _plugin(tmp_path, "claude-menu-system")
        _git_repo(root, ["v0.2.0"])
        report = ValidationReport()
        check_dependency_resolution_tags(root, report)
        hits = [r for r in report.results if "RC-DEP-TAG" in str(getattr(r, "message", r))]
        assert hits, "expected the detector to fire"
        assert all(getattr(r, "level", None) == "WARNING" for r in hits)
        assert report.exit_code_strict() == 0
