#!/usr/bin/env python3
"""Tests for generate_plugin_repo.py scaffold generator.

Tests the plugin repository scaffold generator:
- PluginParams dataclass creation and properties
- Individual gen_* template functions (plugin.json, pyproject.toml, gitignore, README, cliff.toml, workflows)
- Full generation (all files created, directories exist, file permissions)
- Dry run mode (no files written)
- Generated repo validation against validate_plugin.py rules

Coverage: 25 tests covering all generation functions and validation integration.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from generate_plugin_repo import (  # noqa: E402
    COMPONENT_DIRS,
    PluginParams,
    gen_ci_yml,
    gen_cliff_toml,
    gen_gitignore,
    gen_notify_marketplace_yml,
    gen_plugin_json,
    gen_pyproject_toml,
    gen_readme,
    gen_release_yml,
    gen_validate_yml,
    generate_all_files,
    generate_plugin_repo,
)
from validate_plugin import validate_gitignore, validate_readme, validate_structure  # noqa: E402

# =============================================================================
# Helper: standard PluginParams instance for tests
# =============================================================================


def _default_params(**overrides) -> PluginParams:
    """Create a PluginParams with sensible defaults, accepting overrides."""
    defaults = {
        "name": "my-test-plugin",
        "description": "A test plugin for unit tests",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)


# =============================================================================
# Group 1: PluginParams creation (3 tests)
# =============================================================================


class TestPluginParams:
    """Tests for PluginParams dataclass."""

    def test_valid_params_creation(self):
        """PluginParams with all fields creates successfully."""
        p = _default_params()
        assert p.name == "my-test-plugin"
        assert p.description == "A test plugin for unit tests"
        assert p.author == "Test Author"
        assert p.author_email == "test@example.com"
        assert p.version == "0.1.0"

    def test_repo_name_defaults_to_name(self):
        """repo_name property returns the plugin name."""
        p = _default_params(name="cool-plugin")
        assert p.repo_name == "cool-plugin"

    def test_github_url_property(self):
        """github_url property combines owner and repo_name correctly."""
        p = _default_params(github_owner="myorg", name="my-plugin")
        assert p.github_url == "https://github.com/myorg/my-plugin"


# =============================================================================
# Group 2: Individual gen_* functions (10 tests)
# =============================================================================


class TestGenPluginJson:
    """Tests for gen_plugin_json function."""

    def test_produces_valid_json(self):
        """gen_plugin_json output is valid JSON with required fields."""
        p = _default_params()
        content = gen_plugin_json(p)
        data = json.loads(content)
        assert data["name"] == p.name
        assert data["version"] == p.version
        assert data["description"] == p.description
        assert data["author"]["name"] == p.author
        assert data["author"]["email"] == p.author_email
        assert data["homepage"] == p.github_url
        assert data["license"] == p.license

    def test_json_ends_with_newline(self):
        """gen_plugin_json output ends with a newline for clean file writing."""
        p = _default_params()
        content = gen_plugin_json(p)
        assert content.endswith("\n")


class TestGenPyprojectToml:
    """Tests for gen_pyproject_toml function."""

    def test_contains_build_system(self):
        """Generated pyproject.toml contains hatchling build system."""
        p = _default_params()
        content = gen_pyproject_toml(p)
        assert "[build-system]" in content
        assert "hatchling" in content

    def test_contains_project_name_and_version(self):
        """Generated pyproject.toml contains project name and version."""
        p = _default_params(name="xyz-plugin", version="2.3.4")
        content = gen_pyproject_toml(p)
        assert 'name = "xyz-plugin"' in content
        assert 'version = "2.3.4"' in content

    def test_contains_ruff_config(self):
        """Generated pyproject.toml contains ruff configuration."""
        p = _default_params()
        content = gen_pyproject_toml(p)
        assert "[tool.ruff]" in content
        assert "line-length" in content


class TestGenGitignore:
    """Tests for gen_gitignore function."""

    def test_contains_required_entries(self):
        """Generated .gitignore includes __pycache__, .venv, .env, .claude, llm_externalizer_output, .tldr."""
        p = _default_params()
        content = gen_gitignore(p)
        for required in ["__pycache__", ".venv", ".env", ".claude/", "llm_externalizer_output/", ".tldr/"]:
            assert required in content, f"Missing required gitignore entry: {required}"

    def test_contains_node_modules(self):
        """Generated .gitignore includes node_modules."""
        p = _default_params()
        content = gen_gitignore(p)
        assert "node_modules" in content


class TestGenReadme:
    """Tests for gen_readme function."""

    def test_contains_plugin_name_and_description(self):
        """Generated README includes plugin name as heading and description."""
        p = _default_params(name="awesome-plugin", description="An awesome plugin")
        content = gen_readme(p)
        assert "# awesome-plugin" in content
        assert "An awesome plugin" in content

    def test_contains_install_command(self):
        """Generated README includes claude plugin install command."""
        p = _default_params(name="my-plugin", marketplace="my-market")
        content = gen_readme(p)
        assert "claude plugin install my-plugin@my-market" in content


class TestGenCliffToml:
    """Tests for gen_cliff_toml function."""

    def test_contains_changelog_section(self):
        """Generated cliff.toml contains [changelog] section."""
        content = gen_cliff_toml(_default_params())
        assert "[changelog]" in content

    def test_contains_git_section(self):
        """Generated cliff.toml contains [git] section with commit parsers."""
        content = gen_cliff_toml(_default_params())
        assert "[git]" in content
        assert "conventional_commits" in content
        assert "commit_parsers" in content


class TestGenWorkflows:
    """Tests for workflow generation functions."""

    def test_ci_yml_contains_checkout(self):
        """gen_ci_yml output includes actions/checkout@v4."""
        p = _default_params()
        content = gen_ci_yml(p)
        assert "actions/checkout@v4" in content
        assert "name: CI" in content

    def test_release_yml_contains_semver_trigger(self):
        """gen_release_yml output triggers on semver tags."""
        p = _default_params()
        content = gen_release_yml(p)
        assert "v*.*.*" in content
        assert "name: Release" in content

    def test_validate_yml_contains_plugin_validation(self):
        """gen_validate_yml output references validate_plugin.py."""
        p = _default_params()
        content = gen_validate_yml(p)
        assert "validate_plugin.py" in content
        assert "name: Plugin Validation" in content

    def test_notify_yml_contains_marketplace_repo(self):
        """gen_notify_marketplace_yml output references marketplace repo."""
        p = _default_params(marketplace="cool-marketplace", github_owner="myowner")
        content = gen_notify_marketplace_yml(p)
        assert "cool-marketplace" in content
        assert "name: Notify Marketplace" in content


# =============================================================================
# Group 3: Full generation (5 tests)
# =============================================================================


class TestFullGeneration:
    """Tests for generate_plugin_repo and generate_all_files."""

    def test_all_expected_files_created(self, tmp_path):
        """generate_plugin_repo creates all expected files on disk."""
        target = tmp_path / "my-plugin"
        target.mkdir()
        p = _default_params()
        created = generate_plugin_repo(target, p)
        assert len(created) > 0
        # Check key files exist
        assert (target / ".claude-plugin" / "plugin.json").exists()
        assert (target / "pyproject.toml").exists()
        assert (target / ".gitignore").exists()
        assert (target / "README.md").exists()
        assert (target / "LICENSE").exists()
        assert (target / "cliff.toml").exists()
        assert (target / "scripts" / "publish.py").exists()
        assert (target / ".github" / "workflows" / "ci.yml").exists()

    def test_directories_exist(self, tmp_path):
        """generate_plugin_repo creates all component directories."""
        target = tmp_path / "my-plugin"
        target.mkdir()
        p = _default_params()
        generate_plugin_repo(target, p)
        for dir_name in COMPONENT_DIRS:
            assert (target / dir_name).is_dir(), f"Missing directory: {dir_name}"

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix file permissions only")
    def test_executable_files_have_permissions(self, tmp_path):
        """Executable files (pre-push, publish.py) have execute bit set."""
        target = tmp_path / "my-plugin"
        target.mkdir()
        p = _default_params()
        generate_plugin_repo(target, p)
        pre_push = target / "git-hooks" / "pre-push"
        assert pre_push.exists()
        assert os.access(pre_push, os.X_OK), "git-hooks/pre-push should be executable"
        publish = target / "scripts" / "publish.py"
        assert publish.exists()
        assert os.access(publish, os.X_OK), "scripts/publish.py should be executable"

    def test_generate_all_files_returns_tuples(self):
        """generate_all_files returns list of (path, content, is_executable) tuples."""
        p = _default_params()
        files = generate_all_files(p)
        assert len(files) > 0
        for item in files:
            assert len(item) == 3
            rel_path, content, is_exec = item
            assert isinstance(rel_path, str)
            assert isinstance(content, str)
            assert isinstance(is_exec, bool)

    def test_plugin_json_content_is_valid(self, tmp_path):
        """The plugin.json written to disk is valid JSON matching params."""
        target = tmp_path / "my-plugin"
        target.mkdir()
        p = _default_params(name="validation-check", version="1.2.3")
        generate_plugin_repo(target, p)
        manifest = json.loads((target / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["name"] == "validation-check"
        assert manifest["version"] == "1.2.3"


# =============================================================================
# Group 4: Dry run (2 tests)
# =============================================================================


class TestDryRun:
    """Tests for dry run mode."""

    def test_dry_run_creates_no_files(self, tmp_path):
        """generate_plugin_repo with dry_run=True does not write files to disk."""
        target = tmp_path / "my-plugin"
        target.mkdir()
        p = _default_params()
        created = generate_plugin_repo(target, p, dry_run=True)
        # Should return paths in the created list
        assert len(created) > 0
        # But no actual files should exist (except the target dir itself)
        actual_files = [f for f in target.rglob("*") if f.is_file()]
        assert len(actual_files) == 0, f"Files should not be created in dry run mode, found: {actual_files}"

    def test_dry_run_returns_expected_paths(self, tmp_path):
        """Dry run returns the same number of paths as actual generation."""
        target_dry = tmp_path / "dry"
        target_dry.mkdir()
        target_real = tmp_path / "real"
        target_real.mkdir()
        p = _default_params()
        dry_paths = generate_plugin_repo(target_dry, p, dry_run=True)
        real_paths = generate_plugin_repo(target_real, p, dry_run=False)
        assert len(dry_paths) == len(real_paths)


# =============================================================================
# Group 5: Generated repo validation (5 tests)
# =============================================================================


class TestGeneratedRepoValidation:
    """Tests that generated repos pass validation rules from validate_plugin.py."""

    def _generate_and_validate(self, tmp_path, **overrides):
        """Helper to generate a plugin repo and run specific validators on it."""
        target = tmp_path / "validated-plugin"
        target.mkdir()
        p = _default_params(**overrides)
        generate_plugin_repo(target, p)
        return target

    def test_generated_gitignore_passes_validation(self, tmp_path):
        """Generated .gitignore passes validate_gitignore checks."""

        target = self._generate_and_validate(tmp_path)
        report = ValidationReport()
        validate_gitignore(target, report)
        msgs_major = [r.message for r in report.results if r.level == "MAJOR"]
        assert len(msgs_major) == 0, f"validate_gitignore found MAJOR issues: {msgs_major}"

    def test_generated_readme_passes_validation(self, tmp_path):
        """Generated README.md passes validate_readme checks (may warn about badge markers)."""

        target = self._generate_and_validate(tmp_path)
        report = ValidationReport()
        validate_readme(target, report)
        msgs_minor = [r.message for r in report.results if r.level == "MINOR"]
        assert len(msgs_minor) == 0, "Generated README should exist (no MINOR for missing)"

    def test_generated_structure_passes(self, tmp_path):
        """Generated plugin passes validate_structure checks."""

        target = self._generate_and_validate(tmp_path)
        report = ValidationReport()
        validate_structure(target, report)
        assert not report.has_critical, (
            f"Generated plugin has CRITICAL structure issues: {[r.message for r in report.results if r.level == 'CRITICAL']}"
        )

    def test_generated_pipeline_readiness_passes(self, tmp_path):
        """Generated plugin passes pipeline readiness checks."""
        from validate_plugin import validate_pipeline_readiness  # noqa: E402

        target = self._generate_and_validate(tmp_path)
        report = ValidationReport()
        validate_pipeline_readiness(target, report)
        # Generated repos should have pre-push hook, publish.py, cliff.toml, workflows
        passed = [r.message for r in report.results if r.level == "PASSED"]
        assert any("pre-push" in m.lower() for m in passed), "Should find pre-push hook"
        assert any("publish.py" in m for m in passed), "Should find publish.py"
        assert any("cliff.toml" in m for m in passed), "Should find cliff.toml"

    def test_generated_manifest_is_loadable(self, tmp_path):
        """Generated plugin.json can be loaded by validate_manifest."""
        from validate_plugin import validate_manifest  # noqa: E402

        target = self._generate_and_validate(tmp_path)
        report = ValidationReport()
        result = validate_manifest(target, report)
        assert result is not None, "validate_manifest should return the parsed manifest"
        assert result["name"] == "my-test-plugin"
        assert not report.has_critical


class TestPublishPyCornerstoneRule:
    """Verify gen_publish_py enforces 'no push without validation' (the cornerstone).

    These tests assert on the TEMPLATE STRING produced by gen_publish_py —
    they do NOT execute it. The generated script must:
    - Block direct `git push` via process-ancestry orchestrator check (G0)
    - Fetch the validator from GitHub via `uvx cpv-remote-validate` (never local)
    - Reject bypass env vars at start of pipeline
    - Have NO --skip-tests or similar flag
    - Make every gate mandatory (no silent skips when files are missing)
    """

    @staticmethod
    def _src() -> str:
        from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402

        params = PluginParams(
            name="cornerstone-test",
            description="test",
            author="Emasoft",
            author_email="t@e.com",
        )
        return gen_publish_py(params)

    def test_template_parses_as_python(self):
        """Generated publish.py must compile as valid Python."""
        src = self._src()
        compile(src, "<publish.py>", "exec")

    def test_has_orchestrator_ancestry_check(self):
        """Gate G0 must use process ancestry — env vars are trivially spoofable."""
        src = self._src()
        assert "_called_by_publish_orchestrator" in src
        assert "_get_process_ancestry" in src
        assert '["ps", "-p"' in src, "Must walk ancestry via ps(1)"
        assert "[G0] Checking push orchestrator" in src
        assert "Direct push not allowed" in src

    def test_has_bypass_guard_stage(self):
        """Pipeline must have stage_bypass_guard that rejects skip env vars."""
        src = self._src()
        assert "def stage_bypass_guard" in src
        assert "stage_bypass_guard()" in src  # called from main()
        for var in ["CPV_SKIP_TESTS", "CPV_SKIP_LINT", "CPV_SKIP_VALIDATE",
                    "CPV_FORCE_PUBLISH", "CPV_BYPASS_CHECKS", "NO_VERIFY"]:
            assert var in src, f"bypass_guard must list {var}"

    def test_no_skip_tests_flag(self):
        """The template must not expose a --skip-tests argparse flag."""
        src = self._src()
        assert 'add_argument("--skip-tests"' not in src
        assert "args.skip_tests" not in src

    def test_validation_uses_remote_cpv_from_github(self):
        """stage_validate and run_gate must fetch CPV from github via uvx."""
        src = self._src()
        assert "uvx" in src
        assert "git+https://github.com/Emasoft/claude-plugins-validation" in src
        assert "cpv-remote-validate" in src
        # Both stage_validate and run_gate must use the remote validator
        assert src.count("cpv-remote-validate") >= 2

    def test_stage_tests_has_no_skip_path(self):
        """stage_tests must block when tests/ is missing or zero tests collected."""
        src = self._src()
        tests_fn = src.split("def stage_tests")[1].split("def stage_")[0]
        assert "BLOCKED: tests/ directory missing" in tests_fn
        assert "BLOCKED: pytest collected 0 tests" in tests_fn

    def test_run_gate_g0_before_g1(self):
        """run_gate must run Gate G0 (orchestrator) before Gate G1 (version)."""
        src = self._src()
        gate_fn = src.split("def run_gate")[1].split("def stage_")[0]
        g0_idx = gate_fn.find("[G0]")
        g1_idx = gate_fn.find("[G1]")
        assert g0_idx >= 0, "Gate G0 missing"
        assert g1_idx >= 0, "Gate G1 missing"
        assert g0_idx < g1_idx, "Gate G0 must run before G1"

    def test_cornerstone_doc_present(self):
        """The cornerstone rule must be documented in the module docstring."""
        src = self._src()
        assert "Cornerstone rule" in src
        assert "0 issues (WARNING allowed)" in src
        assert "no exceptions" in src.lower()
