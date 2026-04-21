#!/usr/bin/env python3
"""Tests for new validation rules added in CPV v2.1.0.

Tests the 7 new validation rules across validate_plugin.py and validate_xref.py:
- Pipeline readiness checks (pre-push hook, publish.py, cliff.toml, workflows, notify)
- Workflow best practices (uvx vs pip install --system, unpinned actions/checkout)
- Script permissions (_has_shebang + executable checks)
- .gitignore new entries (.claude/, llm_externalizer_output/, .tldr/)
- README badge markers (<!--BADGES-START--> / <!--BADGES-END-->)
- pyproject.toml + .python-version detection for Python plugins
- SKILL.md version sync in validate_xref.py

Coverage: 45 tests covering all code paths across 7 rule groups.
"""

from __future__ import annotations

import json
import platform
import stat
import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import (  # noqa: E402
    _has_shebang,
    validate_gitignore,
    validate_pipeline_readiness,
    validate_readme,
    validate_structure,
    validate_workflow_best_practices,
)
from validate_xref import (  # noqa: E402
    CrossReferenceValidationReport,
    validate_version_sync,
)

# =============================================================================
# Helper: create a minimal valid plugin skeleton for tests that need it
# =============================================================================


def _make_plugin(
    tmp_path: Path,
    *,
    gitignore: str | None = None,
    readme: str | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Create a minimal plugin directory with optional overrides."""
    plugin = tmp_path / "test-plugin"
    plugin.mkdir()
    # .claude-plugin/plugin.json
    cp = plugin / ".claude-plugin"
    cp.mkdir()
    (cp / "plugin.json").write_text(
        json.dumps(
            {
                "name": "test-plugin",
                "version": "1.0.0",
                "description": "Test",
                "author": {"name": "A", "email": "a@b.c"},
            }
        )
    )
    # commands/ for minimal content
    (plugin / "commands").mkdir()
    if gitignore is not None:
        (plugin / ".gitignore").write_text(gitignore)
    if readme is not None:
        (plugin / "README.md").write_text(readme)
    if extra_files:
        for rel, content in extra_files.items():
            p = plugin / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return plugin


# =============================================================================
# Group 1: Pipeline Readiness (8 tests)
# =============================================================================


class TestPipelineReadiness:
    """Tests for validate_pipeline_readiness function."""

    def test_pre_push_hook_in_githooks_passes(self, tmp_path):
        """Pre-push hook detected in .githooks/ directory reports PASSED."""
        plugin = _make_plugin(tmp_path, extra_files={".githooks/pre-push": "#!/bin/sh\nexit 0\n"})
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Pre-push hook found" in m for m in msgs)

    def test_pre_push_hook_in_git_hooks_passes(self, tmp_path):
        """Pre-push hook detected in git-hooks/ directory reports PASSED."""
        plugin = _make_plugin(tmp_path, extra_files={"git-hooks/pre-push": "#!/bin/sh\nexit 0\n"})
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Pre-push hook found" in m for m in msgs)

    def test_no_pre_push_hook_warns(self, tmp_path):
        """Missing pre-push hook reports MINOR."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("pre-push" in m.lower() for m in msgs)

    def test_publish_py_detected(self, tmp_path):
        """scripts/publish.py presence reports PASSED."""
        plugin = _make_plugin(
            tmp_path, extra_files={"scripts/publish.py": "#!/usr/bin/env python3\nprint('publish')\n"}
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("publish.py" in m for m in msgs)

    def test_no_publish_py_warns(self, tmp_path):
        """Missing scripts/publish.py reports WARNING."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("publish.py" in m for m in msgs)

    def test_cliff_toml_detected(self, tmp_path):
        """cliff.toml presence reports PASSED."""
        plugin = _make_plugin(tmp_path, extra_files={"cliff.toml": "[changelog]\nheader = ''\n"})
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("cliff.toml" in m for m in msgs)

    def test_workflow_directory_detected(self, tmp_path):
        """Presence of .github/workflows/*.yml reports PASSED."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                ".github/workflows/ci.yml": "name: CI\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            },
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("workflows" in m.lower() for m in msgs)

    def test_no_workflows_warns(self, tmp_path):
        """Missing workflow directory reports MINOR."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("workflow" in m.lower() for m in msgs)


# =============================================================================
# Group 2: Notify Marketplace Workflow (4 tests)
# =============================================================================


class TestNotifyMarketplace:
    """Tests for marketplace notification workflow detection in pipeline readiness."""

    def test_notify_marketplace_yml_detected(self, tmp_path):
        """notify-marketplace.yml detected reports PASSED."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                ".github/workflows/notify-marketplace.yml": "name: Notify\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            },
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("notification" in m.lower() or "notify" in m.lower() or "marketplace" in m.lower() for m in msgs)

    def test_notify_yml_detected(self, tmp_path):
        """notify.yml variant detected reports PASSED."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                ".github/workflows/notify.yml": "name: Notify\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            },
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("notification" in m.lower() or "notify" in m.lower() for m in msgs)

    def test_marketplace_notify_yml_detected(self, tmp_path):
        """marketplace-notify.yml variant detected reports PASSED."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                ".github/workflows/marketplace-notify.yml": "name: Notify\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            },
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("notification" in m.lower() or "notify" in m.lower() for m in msgs)

    def test_no_notify_workflow_warns(self, tmp_path):
        """Missing notify-marketplace.yml warns when workflows dir exists."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                ".github/workflows/ci.yml": "name: CI\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            },
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("notify" in m.lower() or "marketplace" in m.lower() for m in msgs)


# =============================================================================
# Group 3: Workflow Best Practices (6 tests)
# =============================================================================


class TestWorkflowBestPractices:
    """Tests for validate_workflow_best_practices function."""

    def test_uvx_warning_for_pip_install_system(self, tmp_path):
        """Workflow using 'uv pip install --system' reports NIT."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                ".github/workflows/ci.yml": "name: CI\non:\n  push:\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: uv pip install --system ruff\n"
            },
        )
        report = ValidationReport()
        validate_workflow_best_practices(plugin, report)
        msgs = [r.message for r in report.results if r.level == "NIT"]
        assert any("uvx" in m for m in msgs)

    def test_unpinned_actions_checkout_warns(self, tmp_path):
        """Workflow using 'actions/checkout' without version pin reports NIT."""
        wf_content = "name: CI\non:\n  push:\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout\n"
        plugin = _make_plugin(tmp_path, extra_files={".github/workflows/ci.yml": wf_content})
        report = ValidationReport()
        validate_workflow_best_practices(plugin, report)
        msgs = [r.message for r in report.results if r.level == "NIT"]
        assert any("actions/checkout" in m and "pin" in m for m in msgs)

    def test_pinned_actions_checkout_no_warning(self, tmp_path):
        """Workflow using 'actions/checkout@v4' does not report NIT about checkout."""
        wf_content = "name: CI\non:\n  push:\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
        plugin = _make_plugin(tmp_path, extra_files={".github/workflows/ci.yml": wf_content})
        report = ValidationReport()
        validate_workflow_best_practices(plugin, report)
        msgs = [r.message for r in report.results if r.level == "NIT"]
        assert not any("actions/checkout" in m for m in msgs)

    def test_clean_workflow_passes(self, tmp_path):
        """Workflow with no anti-patterns produces no NIT results."""
        wf_content = "name: CI\non:\n  push:\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - run: uvx ruff check .\n"
        plugin = _make_plugin(tmp_path, extra_files={".github/workflows/ci.yml": wf_content})
        report = ValidationReport()
        validate_workflow_best_practices(plugin, report)
        nits = [r for r in report.results if r.level == "NIT"]
        assert len(nits) == 0

    def test_no_workflows_dir_is_noop(self, tmp_path):
        """Plugin with no .github/workflows/ produces no results."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_workflow_best_practices(plugin, report)
        assert len(report.results) == 0

    def test_multiple_workflow_files_checked(self, tmp_path):
        """All workflow files in the directory are checked for anti-patterns."""
        files = {
            ".github/workflows/ci.yml": "name: CI\non:\n  push:\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: uv pip install --system foo\n",
            ".github/workflows/release.yml": "name: Release\non:\n  push:\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout\n",
        }
        plugin = _make_plugin(tmp_path, extra_files=files)
        report = ValidationReport()
        validate_workflow_best_practices(plugin, report)
        nits = [r for r in report.results if r.level == "NIT"]
        # Should have at least 2 NITs: one for uvx, one for unpinned checkout
        assert len(nits) >= 2


# =============================================================================
# Group 4: Script Permissions - _has_shebang (4 tests)
# =============================================================================


class TestScriptPermissions:
    """Tests for _has_shebang helper and shebang+executable integration."""

    def test_has_shebang_with_python_shebang(self, tmp_path):
        """Python script with shebang returns True."""
        f = tmp_path / "script.py"
        f.write_text("#!/usr/bin/env python3\nprint('hello')\n")
        assert _has_shebang(f) is True

    def test_has_shebang_without_shebang(self, tmp_path):
        """Script without shebang returns False."""
        f = tmp_path / "script.py"
        f.write_text("print('hello')\n")
        assert _has_shebang(f) is False

    def test_has_shebang_with_bash_shebang(self, tmp_path):
        """Bash script with shebang returns True."""
        f = tmp_path / "script.sh"
        f.write_text("#!/bin/bash\necho hello\n")
        assert _has_shebang(f) is True

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix executable permissions only")
    def test_shebang_without_executable_warns(self, tmp_path):
        """Python script with shebang but not executable produces WARNING in validate_scripts."""
        # This tests the integration in validate_scripts, not _has_shebang alone
        from validate_plugin import validate_scripts  # noqa: E402

        plugin = _make_plugin(tmp_path, extra_files={"scripts/tool.py": "#!/usr/bin/env python3\nprint('tool')\n"})
        # Ensure the file is NOT executable
        script_path = plugin / "scripts" / "tool.py"
        script_path.chmod(script_path.stat().st_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)
        report = ValidationReport()
        validate_scripts(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("shebang" in m.lower() and "executable" in m.lower() for m in msgs)


# =============================================================================
# Group 5: .gitignore New Entries (6 tests)
# =============================================================================


class TestGitignoreNewEntries:
    """Tests for new .gitignore category checks: .claude/, llm_externalizer_output/, .tldr/."""

    def _full_gitignore(self):
        """Return a gitignore content that covers ALL expected categories."""
        return """__pycache__/
node_modules/
.mypy_cache/
dist/
.DS_Store
*.swp
.env
.venv/
.claude/
llm_externalizer_output/
.tldr/
reports/
reports_dev/
"""

    def test_claude_dir_missing_warns(self, tmp_path):
        """Missing .claude/ in gitignore reports MINOR — but only when the
        folder actually exists. v2.25.0 rule: never flag non-existent
        artifacts."""
        gi = self._full_gitignore().replace(".claude/\n", "")
        plugin = _make_plugin(tmp_path, gitignore=gi)
        (plugin / ".claude").mkdir()  # the artifact must exist to be flagged
        report = ValidationReport()
        validate_gitignore(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any(".claude" in m.lower() for m in msgs)

    def test_llm_externalizer_output_missing_warns(self, tmp_path):
        """Missing llm_externalizer_output/ in gitignore reports WARNING —
        only when the folder actually exists."""
        gi = self._full_gitignore().replace("llm_externalizer_output/\n", "")
        plugin = _make_plugin(tmp_path, gitignore=gi)
        (plugin / "llm_externalizer_output").mkdir()
        report = ValidationReport()
        validate_gitignore(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("llm externalizer" in m.lower() for m in msgs)

    def test_tldr_dir_missing_warns(self, tmp_path):
        """Missing .tldr/ in gitignore reports WARNING — only when the
        folder actually exists."""
        gi = self._full_gitignore().replace(".tldr/\n", "")
        plugin = _make_plugin(tmp_path, gitignore=gi)
        (plugin / ".tldr").mkdir()
        report = ValidationReport()
        validate_gitignore(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any(".tldr" in m.lower() for m in msgs)

    def test_all_new_entries_present_passes(self, tmp_path):
        """Gitignore with all expected categories produces PASSED."""
        plugin = _make_plugin(tmp_path, gitignore=self._full_gitignore())
        report = ValidationReport()
        validate_gitignore(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("all expected categories" in m.lower() for m in msgs)

    def test_nonexistent_folder_not_flagged(self, tmp_path):
        """Categories whose artifact does NOT exist in the plugin must not
        be flagged even if the pattern is missing from .gitignore.
        v2.25.0 rule: never speculate on future files."""
        gi = self._full_gitignore().replace(".claude/\n", "")
        plugin = _make_plugin(tmp_path, gitignore=gi)
        # note: no (.claude/) folder created
        report = ValidationReport()
        validate_gitignore(plugin, report)
        for r in report.results:
            assert ".claude" not in r.message.lower(), (
                f"flagged non-existent .claude/ — v2.25.0 should suppress: {r.message}"
            )

    def test_reports_dir_missing_reports_major(self, tmp_path):
        """Missing reports/ in gitignore reports MAJOR — only when reports/
        actually exists (v2.25.0 rule)."""
        gi = self._full_gitignore().replace("reports/\n", "")
        plugin = _make_plugin(tmp_path, gitignore=gi)
        (plugin / "reports").mkdir()
        report = ValidationReport()
        validate_gitignore(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("reports/" in m for m in msgs)

    def test_reports_dev_dir_missing_warns(self, tmp_path):
        """Missing reports_dev/ in gitignore reports WARNING — only when the
        folder actually exists (v2.25.0 rule)."""
        gi = self._full_gitignore().replace("reports_dev/\n", "")
        plugin = _make_plugin(tmp_path, gitignore=gi)
        (plugin / "reports_dev").mkdir()
        report = ValidationReport()
        validate_gitignore(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("reports_dev/" in m for m in msgs)

    def test_reports_pattern_does_not_false_match_reports_dev(self, tmp_path):
        """`reports_dev/` in .gitignore must NOT satisfy the `reports/`
        requirement (regression guard: trailing-slash substring-matching)."""
        gi = self._full_gitignore().replace("reports/\n", "")
        plugin = _make_plugin(tmp_path, gitignore=gi)
        (plugin / "reports").mkdir()
        (plugin / "reports_dev").mkdir()
        report = ValidationReport()
        validate_gitignore(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("reports/" in m for m in msgs), (
            "reports_dev/ satisfied the reports/ requirement — the validator "
            "is substring-matching without the trailing slash"
        )

    def test_no_gitignore_reports_major(self, tmp_path):
        """Plugin without .gitignore reports MAJOR."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_gitignore(plugin, report)
        assert report.has_major

    def test_env_missing_reports_major(self, tmp_path):
        """Missing .env pattern in gitignore reports MAJOR — only when a
        .env file actually exists (v2.25.0 rule)."""
        gi = self._full_gitignore().replace(".env\n", "")
        plugin = _make_plugin(tmp_path, gitignore=gi)
        (plugin / ".env").write_text("SECRET=x")
        report = ValidationReport()
        validate_gitignore(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("env" in m.lower() for m in msgs)


# =============================================================================
# Group 6: README Badge Markers (4 tests)
# =============================================================================


class TestReadmeBadgeMarkers:
    """Tests for README.md badge marker validation."""

    def test_both_badge_markers_present_passes(self, tmp_path):
        """README with both <!--BADGES-START--> and <!--BADGES-END--> reports PASSED."""
        readme = "# Plugin\n\n<!--BADGES-START-->\n![badge](url)\n<!--BADGES-END-->\n\nDescription.\n"
        plugin = _make_plugin(tmp_path, readme=readme)
        report = ValidationReport()
        validate_readme(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("badge" in m.lower() for m in msgs)

    def test_missing_badge_markers_warns(self, tmp_path):
        """README without badge markers reports WARNING."""
        readme = "# Plugin\n\nDescription.\n"
        plugin = _make_plugin(tmp_path, readme=readme)
        report = ValidationReport()
        validate_readme(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("badge" in m.lower() for m in msgs)

    def test_only_start_marker_warns(self, tmp_path):
        """README with only <!--BADGES-START--> (missing END) reports WARNING."""
        readme = "# Plugin\n\n<!--BADGES-START-->\n![badge](url)\n\nDescription.\n"
        plugin = _make_plugin(tmp_path, readme=readme)
        report = ValidationReport()
        validate_readme(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("badge" in m.lower() for m in msgs)

    def test_no_readme_reports_minor(self, tmp_path):
        """Missing README.md reports MINOR and no badge check."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_readme(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("readme" in m.lower() for m in msgs)


# =============================================================================
# Group 7: pyproject.toml + .python-version (6 tests)
# =============================================================================


class TestPyprojectPythonVersion:
    """Tests for pyproject.toml and .python-version detection in validate_structure."""

    def test_pyproject_present_with_py_scripts_passes(self, tmp_path):
        """pyproject.toml present when scripts/*.py exist reports PASSED."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                "scripts/tool.py": "print('tool')\n",
                "pyproject.toml": "[project]\nname = 'test'\nversion = '1.0.0'\n",
            },
        )
        report = ValidationReport()
        validate_structure(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("pyproject.toml" in m for m in msgs)

    def test_pyproject_absent_with_py_scripts_warns(self, tmp_path):
        """Missing pyproject.toml when scripts/*.py exist reports MINOR."""
        plugin = _make_plugin(tmp_path, extra_files={"scripts/tool.py": "print('tool')\n"})
        report = ValidationReport()
        validate_structure(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("pyproject.toml" in m for m in msgs)

    def test_pyproject_absent_without_py_scripts_no_warning(self, tmp_path):
        """Missing pyproject.toml without Python scripts does not report MINOR about pyproject."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_structure(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert not any("pyproject.toml" in m for m in msgs)

    def test_python_version_present_passes(self, tmp_path):
        """Existing .python-version with scripts/*.py reports PASSED."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                "scripts/tool.py": "print('tool')\n",
                "pyproject.toml": "[project]\nname = 'test'\nversion = '1.0.0'\n",
                ".python-version": "3.12\n",
            },
        )
        report = ValidationReport()
        validate_structure(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any(".python-version" in m for m in msgs)

    def test_python_version_absent_with_py_scripts_warns(self, tmp_path):
        """Missing .python-version with scripts/*.py reports WARNING."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                "scripts/tool.py": "print('tool')\n",
                "pyproject.toml": "[project]\nname = 'test'\nversion = '1.0.0'\n",
            },
        )
        report = ValidationReport()
        validate_structure(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any(".python-version" in m for m in msgs)

    def test_python_version_absent_without_py_scripts_no_warning(self, tmp_path):
        """Missing .python-version without Python scripts does not warn about .python-version."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_structure(plugin, report)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert not any(".python-version" in m for m in msgs)


# =============================================================================
# Group 8: SKILL.md Version Sync (5 tests)
# =============================================================================


class TestSkillMdVersionSync:
    """Tests for SKILL.md version in frontmatter sync via validate_version_sync."""

    def test_matching_version_passes(self, tmp_path):
        """Plugin.json and SKILL.md with same version reports PASSED."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                "skills/my-skill/SKILL.md": "---\nname: my-skill\nversion: 1.0.0\n---\n\n# My Skill\n",
            },
        )
        # Plugin manifest already has version 1.0.0
        report = CrossReferenceValidationReport()
        validate_version_sync(plugin, report)
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("agree" in m.lower() for m in msgs)
        assert report.version_sources.get("skills/my-skill/SKILL.md") == "1.0.0"

    def test_mismatching_version_warns(self, tmp_path):
        """Plugin.json 1.0.0 and SKILL.md 2.0.0 reports MAJOR version mismatch."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                "skills/my-skill/SKILL.md": "---\nname: my-skill\nversion: 2.0.0\n---\n\n# My Skill\n",
            },
        )
        report = CrossReferenceValidationReport()
        validate_version_sync(plugin, report)
        msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("mismatch" in m.lower() for m in msgs)

    def test_no_version_field_in_skill_is_fine(self, tmp_path):
        """SKILL.md without version field does not add a version source."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                "skills/my-skill/SKILL.md": "---\nname: my-skill\n---\n\n# My Skill\n",
            },
        )
        report = CrossReferenceValidationReport()
        validate_version_sync(plugin, report)
        assert "skills/my-skill/SKILL.md" not in report.version_sources

    def test_multiple_skills_checked(self, tmp_path):
        """Multiple SKILL.md files with differing versions are all detected."""
        plugin = _make_plugin(
            tmp_path,
            extra_files={
                "skills/skill-a/SKILL.md": "---\nname: skill-a\nversion: 1.0.0\n---\n\n# A\n",
                "skills/skill-b/SKILL.md": "---\nname: skill-b\nversion: 1.0.0\n---\n\n# B\n",
            },
        )
        report = CrossReferenceValidationReport()
        validate_version_sync(plugin, report)
        assert "skills/skill-a/SKILL.md" in report.version_sources
        assert "skills/skill-b/SKILL.md" in report.version_sources
        msgs = [r.message for r in report.results if r.level == "PASSED"]
        # All 3 sources (plugin.json + 2 skills) should agree on 1.0.0
        assert any("3 version sources agree" in m for m in msgs)

    def test_single_version_source_skips(self, tmp_path):
        """Only one version source triggers INFO skip message."""
        plugin = tmp_path / "bare-plugin"
        plugin.mkdir()
        # No plugin.json, no README with version, just a SKILL.md
        (plugin / "skills" / "x").mkdir(parents=True)
        (plugin / "skills" / "x" / "SKILL.md").write_text("---\nname: x\nversion: 1.0.0\n---\n\n# X\n")
        report = CrossReferenceValidationReport()
        validate_version_sync(plugin, report)
        msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("sync check skipped" in m.lower() for m in msgs)
