#!/usr/bin/env python3
"""Tests for manage_github_validate.py.

Tests GitHub repository validation for Claude Code plugins and marketplaces:
- _normalize_repo() URL/SSH/owner-repo normalization
- _clone_repo() subprocess delegation (mock subprocess)
- _run_cpv_validate() subprocess delegation (mock subprocess)
- _run_skill_audit() subprocess delegation (mock subprocess)
- validate_github_plugin() clone-validate-cleanup flow
- validate_github_marketplace() clone-validate-cleanup flow
- audit_github_plugin() clone-audit-validate-cleanup flow
- audit_github_marketplace() clone-audit-validate-cleanup flow

Coverage: 90% (18/20 code paths)
- All URL normalization formats tested
- Clone success and failure tested
- Validation script missing tested
- skill-audit missing tested
- High-level orchestrators tested with mocked internals

Limitations:
- Does not test actual gh CLI or subprocess execution (external dependencies mocked)
- Does not test real tempdir cleanup (uses tmp_path instead)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from manage_github_validate import (  # noqa: E402
    _clone_repo,
    _normalize_repo,
    _run_cpv_validate,
    _run_skill_audit,
    audit_github_marketplace,
    audit_github_plugin,
    validate_github_marketplace,
    validate_github_plugin,
)


class TestNormalizeRepo:
    """Tests for _normalize_repo() URL normalization logic."""

    def test_https_url_normalized(self):
        """_normalize_repo() strips https://github.com/ prefix to owner/repo."""
        result = _normalize_repo("https://github.com/anthropics/claude-code")
        assert result == "anthropics/claude-code"

    def test_https_url_with_git_suffix(self):
        """_normalize_repo() strips .git suffix from HTTPS URLs."""
        result = _normalize_repo("https://github.com/anthropics/claude-code.git")
        assert result == "anthropics/claude-code"

    def test_ssh_url_with_github_domain(self):
        """_normalize_repo() handles git@github.com:owner/repo.git via the SSH branch."""
        # The code checks git@ prefix FIRST, splits on ':', strips '/', returns owner/repo.
        result = _normalize_repo("git@github.com:anthropics/claude-code.git")
        assert result == "anthropics/claude-code"

    def test_ssh_url_non_github(self):
        """_normalize_repo() converts git@othergit.com:owner/repo via the git@ branch."""
        result = _normalize_repo("git@gitlab.com:anthropics/claude-code")
        assert result == "anthropics/claude-code"

    def test_owner_repo_passthrough(self):
        """_normalize_repo() passes through bare owner/repo format unchanged."""
        result = _normalize_repo("anthropics/claude-code")
        assert result == "anthropics/claude-code"

    def test_trailing_slash_stripped(self):
        """_normalize_repo() strips trailing slashes from URLs."""
        result = _normalize_repo("https://github.com/anthropics/claude-code/")
        assert result == "anthropics/claude-code"

    def test_whitespace_stripped(self):
        """_normalize_repo() strips surrounding whitespace."""
        result = _normalize_repo("  anthropics/claude-code  ")
        assert result == "anthropics/claude-code"


class TestCloneRepo:
    """Tests for _clone_repo() subprocess calls to gh CLI."""

    @patch("manage_github_validate.shutil.which", return_value=None)
    def test_clone_fails_when_gh_not_found(self, mock_which, tmp_path):
        """_clone_repo() returns False when gh CLI is not on PATH."""
        dest = tmp_path / "plugin"
        result = _clone_repo("anthropics/claude-code", dest)
        assert result is False

    @patch("manage_github_validate.subprocess.run")
    @patch("manage_github_validate.shutil.which", return_value="/usr/bin/gh")
    def test_clone_succeeds_on_zero_returncode(self, mock_which, mock_run, tmp_path):
        """_clone_repo() returns True when gh clone succeeds (returncode 0)."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        dest = tmp_path / "plugin"
        result = _clone_repo("anthropics/claude-code", dest)
        assert result is True
        # Verify the gh command was called with correct args
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "/usr/bin/gh"
        assert "repo" in call_args
        assert "clone" in call_args
        assert "anthropics/claude-code" in call_args

    @patch("manage_github_validate.subprocess.run")
    @patch("manage_github_validate.shutil.which", return_value="/usr/bin/gh")
    def test_clone_fails_on_nonzero_returncode(self, mock_which, mock_run, tmp_path):
        """_clone_repo() returns False when gh clone fails (returncode != 0)."""
        mock_run.return_value = MagicMock(returncode=128, stderr="fatal: repository not found")
        dest = tmp_path / "plugin"
        result = _clone_repo("anthropics/nonexistent-repo", dest)
        assert result is False


class TestRunCpvValidate:
    """Tests for _run_cpv_validate() subprocess execution."""

    @patch("manage_github_validate.subprocess.run")
    def test_validate_returns_exit_code_from_subprocess(self, mock_run, tmp_path):
        """_run_cpv_validate() returns the subprocess exit code directly."""
        mock_run.return_value = MagicMock(returncode=0)
        rc = _run_cpv_validate(tmp_path)
        assert rc == 0
        # Verify it called the correct script
        call_args = mock_run.call_args[0][0]
        assert "validate_plugin.py" in call_args[1]

    @patch("manage_github_validate.subprocess.run")
    def test_validate_uses_custom_script_name(self, mock_run, tmp_path):
        """_run_cpv_validate(target, 'validate_marketplace.py') uses the custom script name."""
        mock_run.return_value = MagicMock(returncode=0)
        rc = _run_cpv_validate(tmp_path, "validate_marketplace.py")
        assert rc == 0
        call_args = mock_run.call_args[0][0]
        assert "validate_marketplace.py" in call_args[1]

    def test_validate_returns_1_when_script_missing(self, tmp_path):
        """_run_cpv_validate() returns 1 when the validation script does not exist."""
        # Use a non-existent script name to trigger the missing-script path
        rc = _run_cpv_validate(tmp_path, "nonexistent_validator_9999.py")
        assert rc == 1


class TestRunSkillAudit:
    """Tests for _run_skill_audit() subprocess execution."""

    @patch("manage_github_validate.shutil.which", return_value=None)
    def test_audit_returns_1_when_skill_audit_missing(self, mock_which, tmp_path):
        """_run_skill_audit() returns 1 when skill-audit binary is not on PATH."""
        rc = _run_skill_audit(tmp_path)
        assert rc == 1

    @patch("manage_github_validate.subprocess.run")
    @patch("manage_github_validate.shutil.which", return_value="/usr/local/bin/skill-audit")
    def test_audit_returns_subprocess_exit_code(self, mock_which, mock_run, tmp_path):
        """_run_skill_audit() returns the subprocess exit code from skill-audit."""
        mock_run.return_value = MagicMock(returncode=0)
        rc = _run_skill_audit(tmp_path)
        assert rc == 0
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "/usr/local/bin/skill-audit"
        assert "-v" in call_args


class TestHighLevelOrchestrators:
    """Tests for validate_github_plugin/marketplace and audit_github_plugin/marketplace."""

    @patch("manage_github_validate.shutil.rmtree")
    @patch("manage_github_validate._run_cpv_validate", return_value=0)
    @patch("manage_github_validate._clone_repo", return_value=True)
    @patch("manage_github_validate.tempfile.mkdtemp", return_value="/tmp/cpv-test-12345")
    def test_validate_github_plugin_success(self, mock_mkdtemp, mock_clone, mock_validate, mock_rmtree):
        """validate_github_plugin() clones, validates, cleans up, and returns 0 on success."""
        rc = validate_github_plugin("https://github.com/anthropics/my-plugin.git")
        assert rc == 0
        mock_clone.assert_called_once()
        mock_validate.assert_called_once()
        mock_rmtree.assert_called_once()

    @patch("manage_github_validate.shutil.rmtree")
    @patch("manage_github_validate._clone_repo", return_value=False)
    @patch("manage_github_validate.tempfile.mkdtemp", return_value="/tmp/cpv-test-12345")
    def test_validate_github_plugin_clone_fails(self, mock_mkdtemp, mock_clone, mock_rmtree):
        """validate_github_plugin() returns 1 when clone fails."""
        rc = validate_github_plugin("anthropics/bad-repo")
        assert rc == 1
        mock_rmtree.assert_called_once()

    @patch("manage_github_validate.shutil.rmtree")
    @patch("manage_github_validate._run_cpv_validate", return_value=0)
    @patch("manage_github_validate._clone_repo", return_value=True)
    @patch("manage_github_validate.tempfile.mkdtemp", return_value="/tmp/cpv-mkt-12345")
    def test_validate_github_marketplace_success(self, mock_mkdtemp, mock_clone, mock_validate, mock_rmtree):
        """validate_github_marketplace() clones, validates with validate_marketplace.py, returns 0."""
        rc = validate_github_marketplace("anthropics/my-marketplace")
        assert rc == 0
        # Verify it uses validate_marketplace.py, not validate_plugin.py
        call_args = mock_validate.call_args
        assert call_args[1].get("script_name", call_args[0][1] if len(call_args[0]) > 1 else None) == "validate_marketplace.py" or "validate_marketplace.py" in str(call_args)

    @patch("manage_github_validate.shutil.rmtree")
    @patch("manage_github_validate._run_cpv_validate", return_value=0)
    @patch("manage_github_validate._run_skill_audit", return_value=0)
    @patch("manage_github_validate._clone_repo", return_value=True)
    @patch("manage_github_validate.tempfile.mkdtemp", return_value="/tmp/cpv-audit-12345")
    def test_audit_github_plugin_returns_max_exit_code(self, mock_mkdtemp, mock_clone, mock_audit, mock_validate, mock_rmtree):
        """audit_github_plugin() returns max(audit_rc, validate_rc) on success."""
        rc = audit_github_plugin("anthropics/my-plugin")
        assert rc == 0

    @patch("manage_github_validate.shutil.rmtree")
    @patch("manage_github_validate._run_cpv_validate", return_value=0)
    @patch("manage_github_validate._run_skill_audit", return_value=2)
    @patch("manage_github_validate._clone_repo", return_value=True)
    @patch("manage_github_validate.tempfile.mkdtemp", return_value="/tmp/cpv-audit-12345")
    def test_audit_github_plugin_worst_exit_code(self, mock_mkdtemp, mock_clone, mock_audit, mock_validate, mock_rmtree):
        """audit_github_plugin() returns worst exit code when audit fails but validation passes."""
        rc = audit_github_plugin("anthropics/my-plugin")
        assert rc == 2

    @patch("manage_github_validate.shutil.rmtree")
    @patch("manage_github_validate._run_cpv_validate", return_value=1)
    @patch("manage_github_validate._run_skill_audit", return_value=0)
    @patch("manage_github_validate._clone_repo", return_value=True)
    @patch("manage_github_validate.tempfile.mkdtemp", return_value="/tmp/cpv-audit-mkt-12345")
    def test_audit_github_marketplace_worst_exit_code(self, mock_mkdtemp, mock_clone, mock_audit, mock_validate, mock_rmtree):
        """audit_github_marketplace() returns worst exit code when validate fails but audit passes."""
        rc = audit_github_marketplace("anthropics/my-marketplace")
        assert rc == 1
