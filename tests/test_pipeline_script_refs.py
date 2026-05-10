#!/usr/bin/env python3
"""Tests for `validate_pipeline_script_refs` — root fix for the "renamed
script breaks CI/hooks" class of bugs.

Background: every time a script in `scripts/` is renamed or removed, multiple
consumers silently break — `.github/workflows/*.yml`, the locally-installed
`.git/hooks/pre-push`, the published `setup_plugin_pipeline.py` PRE_PUSH_HOOK
template, and the plugin-validation-skill reference hooks all hardcode
`scripts/<name>.py` paths. The v2.65.0 lint consolidation triggered exactly
this regression — `lint_files.py` was removed but the CI workflow + the
local pre-push hook still invoked it, breaking every push until a follow-up
patch.

This validator catches the regression at PR / release time so it can never
ship to production again.

These tests verify:
1. A workflow file referencing a non-existent `scripts/<name>.py` emits MAJOR
   with the file:line and a clear message.
2. A workflow referencing only EXISTING scripts emits no findings.
3. The hook file `.git/hooks/pre-push` is also scanned when present.
4. The plugin-validation-skill reference template is also scanned.
5. `setup_plugin_pipeline.py` (the template generator) is scanned.
6. A plugin without a `scripts/` directory is gracefully skipped.
7. Non-script `scripts/<name>.py.bak` (with extra suffix) does NOT trigger
   the regex (word boundary check).
8. Multiple references in a single file are reported individually.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import validate_pipeline_script_refs  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin(tmp_path: Path, *, scripts: list[str] | None = None) -> Path:
    """Build a minimal plugin tree with an optional list of script files."""
    plugin_root = tmp_path / "plugin"
    (plugin_root / "scripts").mkdir(parents=True)
    for name in scripts or []:
        (plugin_root / "scripts" / name).write_text("# stub\n", encoding="utf-8")
    return plugin_root


def _has_major(report: ValidationReport, fragment: str) -> bool:
    return any(r.level == "MAJOR" and fragment in r.message for r in report.results)


# ---------------------------------------------------------------------------
# Workflow file references
# ---------------------------------------------------------------------------


class TestWorkflowReferences:
    def test_dangling_workflow_reference_emits_major(self, tmp_path: Path) -> None:
        """The original bug: CI references `scripts/lint_files.py` but the
        file was removed. Validator must catch this."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        wf_dir = plugin_root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  lint:\n    steps:\n      - run: uv run python scripts/lint_files.py .\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        assert _has_major(report, "Dangling reference to scripts/lint_files.py")
        assert _has_major(report, ".github/workflows/ci.yml:4")

    def test_existing_workflow_reference_emits_nothing(self, tmp_path: Path) -> None:
        """Workflow referencing an EXISTING script must not trigger anything."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        wf_dir = plugin_root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  validate:\n    steps:\n      - run: uv run python scripts/validate_plugin.py .\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        assert not _has_major(report, "Dangling reference")

    def test_yaml_extension_also_scanned(self, tmp_path: Path) -> None:
        """Both .yml and .yaml workflow files should be scanned."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        wf_dir = plugin_root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "release.yaml").write_text(
            "      - run: uv run python scripts/missing.py\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        assert _has_major(report, "scripts/missing.py")
        assert _has_major(report, ".github/workflows/release.yaml")

    def test_multiple_dangling_refs_emit_separate_findings(self, tmp_path: Path) -> None:
        """Each dangling reference is reported individually so the maintainer
        can see all of them in one validation pass."""
        plugin_root = _make_plugin(tmp_path)
        wf_dir = plugin_root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n"
            "  lint:\n"
            "    steps:\n"
            "      - run: scripts/lint_files.py\n"
            "      - run: scripts/old_validator.py\n"
            "      - run: scripts/legacy.py\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) >= 3, f"expected ≥3 MAJOR findings, got {len(majors)}"
        assert _has_major(report, "scripts/lint_files.py")
        assert _has_major(report, "scripts/old_validator.py")
        assert _has_major(report, "scripts/legacy.py")


# ---------------------------------------------------------------------------
# Pre-push hook references
# ---------------------------------------------------------------------------


class TestHookReferences:
    def test_dangling_local_hook_reference_emits_major(self, tmp_path: Path) -> None:
        """The actual bug we hit: the locally-installed `.git/hooks/pre-push`
        called `scripts/lint_files.py`."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        hook_dir = plugin_root / ".git" / "hooks"
        hook_dir.mkdir(parents=True)
        (hook_dir / "pre-push").write_text(
            "#!/usr/bin/env python3\nimport subprocess\nsubprocess.run(['python', 'scripts/lint_files.py', '.'])\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        assert _has_major(report, "scripts/lint_files.py")
        assert _has_major(report, ".git/hooks/pre-push")

    def test_git_tracked_hook_template_scanned(self, tmp_path: Path) -> None:
        """The git-tracked source-of-truth hooks under git-hooks/ are what
        setup_git_hooks.py copies into .git/hooks/. A stale ref here propagates
        to every fresh install. This is the gap that let the v2.65.0
        lint_files.py-removal regression slip through — the locally-installed
        .git/hooks/pre-push had been hand-patched, but git-hooks/pre-push
        (the source) still referenced the removed script."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        git_hooks_dir = plugin_root / "git-hooks"
        git_hooks_dir.mkdir()
        (git_hooks_dir / "pre-push").write_text(
            "#!/usr/bin/env python3\nimport subprocess\nsubprocess.run(['python', 'scripts/lint_files.py', '.'])\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        assert _has_major(report, "scripts/lint_files.py")
        assert _has_major(report, "git-hooks/pre-push")

    def test_git_tracked_pre_commit_scanned(self, tmp_path: Path) -> None:
        """git-hooks/pre-commit is also scanned (multiple hook types covered)."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        git_hooks_dir = plugin_root / "git-hooks"
        git_hooks_dir.mkdir()
        (git_hooks_dir / "pre-commit").write_text(
            "#!/bin/bash\npython scripts/old_format.py .\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        assert _has_major(report, "scripts/old_format.py")
        assert _has_major(report, "git-hooks/pre-commit")

    def test_skill_reference_template_scanned(self, tmp_path: Path) -> None:
        """The template hook in plugin-validation-skill is the source for new
        plugin scaffolds; stale refs there propagate to every newly-created
        plugin."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        ref_dir = plugin_root / "skills" / "plugin-validation-skill" / "references"
        ref_dir.mkdir(parents=True)
        (ref_dir / "pre-push-hook.py").write_text(
            "lint_script = 'scripts/old_lint.py'\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        assert _has_major(report, "scripts/old_lint.py")
        assert _has_major(report, "plugin-validation-skill/references/pre-push-hook.py")

    def test_setup_plugin_pipeline_template_scanned(self, tmp_path: Path) -> None:
        """The PRE_PUSH_HOOK template inside setup_plugin_pipeline.py is the
        source-of-truth for newly-scaffolded plugins."""
        plugin_root = _make_plugin(tmp_path, scripts=["setup_plugin_pipeline.py"])
        (plugin_root / "scripts" / "setup_plugin_pipeline.py").write_text(
            'PRE_PUSH_HOOK = """\nlint_script = scripts/missing_lint.py\n"""\n',
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        assert _has_major(report, "scripts/missing_lint.py")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_no_scripts_directory_skips_gracefully(self, tmp_path: Path) -> None:
        """Plugins without scripts/ are skipped entirely (validator is no-op)."""
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        # No scripts/ dir
        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        assert not report.results, "expected no findings when no scripts/ exists"

    def test_word_boundary_avoids_substring_false_positives(self, tmp_path: Path) -> None:
        """`scripts/lint_files.py.bak` should NOT match the regex — that's a
        backup file, not a runtime reference."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        wf_dir = plugin_root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "      - run: tar czf scripts/lint_files.py.bak.gz some_path\n"
            "      - run: scripts/lint_files.pyc.something\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        # No findings: neither line ends with `.py` at a word boundary
        assert not _has_major(report, "Dangling reference to scripts/lint_files.py")

    def test_finding_includes_line_excerpt(self, tmp_path: Path) -> None:
        """The finding message includes the actual offending line for fast
        diagnosis."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        wf_dir = plugin_root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "      - run: uv run python scripts/missing.py --strict --report report.txt\n",
            encoding="utf-8",
        )

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert majors
        assert "uv run python scripts/missing.py --strict --report report.txt" in majors[0].message

    def test_empty_workflow_file_handled(self, tmp_path: Path) -> None:
        """An empty workflow file should not crash the validator."""
        plugin_root = _make_plugin(tmp_path, scripts=["validate_plugin.py"])
        wf_dir = plugin_root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "empty.yml").write_text("", encoding="utf-8")

        report = ValidationReport()
        validate_pipeline_script_refs(plugin_root, report)
        # No crash, no findings
        assert not _has_major(report, "Dangling reference")
