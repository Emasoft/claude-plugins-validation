"""Tests for scripts/branch_rules_install.sh — the pure-bash variant.

These tests invoke the shell script as a subprocess. They only exercise the
paths that don't require a real GitHub API call (--help, --list-apps without
auth, missing --check error, malformed args, etc.) so the suite runs fast
and deterministically.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "branch_rules_install.sh"

PYTEST_BASH_SKIP = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Bash-only script, Windows CI is covered by the Python variants",
)


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke the bash script with the given CLI args."""
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        check=False,
    )


@PYTEST_BASH_SKIP
class TestFilePresence:
    """The script file must exist, be non-empty, and be executable."""

    def test_script_exists(self):
        """branch_rules_install.sh exists in scripts/."""
        assert SCRIPT.is_file()

    def test_script_non_empty(self):
        """The script has reasonable size (not an empty stub)."""
        assert SCRIPT.stat().st_size > 2000

    def test_script_has_shebang(self):
        """The script starts with a bash shebang so ./branch_rules_install.sh works."""
        first_line = SCRIPT.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("#!") and "bash" in first_line

    def test_script_is_executable(self):
        """The file has the executable bit set for the owner."""
        mode = SCRIPT.stat().st_mode
        assert mode & stat.S_IXUSR


@PYTEST_BASH_SKIP
class TestHelpOutput:
    """--help must exit 0 and print the full usage block."""

    def test_help_flag_exits_zero(self):
        """--help returns exit code 0."""
        result = _run(["--help"])
        assert result.returncode == 0

    def test_short_help_flag_exits_zero(self):
        """-h returns exit code 0."""
        result = _run(["-h"])
        assert result.returncode == 0

    def test_help_mentions_required_options(self):
        """--help output documents all the important flags."""
        result = _run(["--help"])
        for flag in (
            "--check",
            "--ruleset-name",
            "--add-bypass-app-id",
            "--reset-bypass",
            "--dry-run",
            "--list-apps",
        ):
            assert flag in result.stdout, f"missing {flag} in --help"

    def test_help_mentions_auto_detect_behavior(self):
        """--help explains that OWNER/REPO can be auto-detected from git remote."""
        result = _run(["--help"])
        assert "auto-detect" in result.stdout.lower() or "auto-detected" in result.stdout.lower()

    def test_help_documents_requirements(self):
        """--help names the two external dependencies (gh, jq)."""
        result = _run(["--help"])
        assert "gh" in result.stdout
        assert "jq" in result.stdout


@PYTEST_BASH_SKIP
class TestCliValidation:
    """CLI argument-shape validation before any API call."""

    def test_unknown_option_exits_nonzero(self):
        """Unknown option rejects with a clear error."""
        result = _run(["--nope"])
        assert result.returncode != 0
        assert "Unknown option" in result.stderr

    def test_multiple_slugs_rejected(self):
        """Passing two slugs in the same invocation is rejected."""
        result = _run(["a/b", "c/d"])
        assert result.returncode != 0
        assert "Multiple repo slugs" in result.stderr or "slug" in result.stderr.lower()

    def test_non_integer_app_id_rejected(self):
        """--add-bypass-app-id must be an integer."""
        result = _run(["a/b", "--check", "X", "--add-bypass-app-id", "abc"])
        assert result.returncode != 0
        assert "integer" in result.stderr.lower()

    def test_invalid_positional_arg_rejected(self, tmp_path: Path):
        """A bare positional arg without a slash is rejected as 'Invalid argument'.

        The arg parser matches slugs via the glob-style case `*/*` — anything
        without a slash falls through to the fallback branch and is rejected
        before the slug validator ever runs.
        """
        # Run in an empty tmp dir so git config has no origin
        result = _run(["no-slash", "--check", "X"], cwd=tmp_path)
        assert result.returncode != 0
        stderr_lower = result.stderr.lower()
        assert "invalid argument" in stderr_lower or "slug" in stderr_lower

    def test_missing_check_context_rejected(self, tmp_path: Path):
        """Without any --check flag (and not --list-apps) the script exits with an error."""
        # Put a fake git remote so auto-detect succeeds and we hit the --check gate
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            '[remote "origin"]\n\turl = https://github.com/foo/bar.git\n',
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.setdefault("HOME", str(tmp_path))  # avoid touching real gh auth
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=env,
            check=False,
        )
        # Could exit 1 (missing --check) OR 2 (gh auth missing) depending on environment;
        # either way it should NOT succeed and should NOT silently create a ruleset.
        assert result.returncode != 0


@PYTEST_BASH_SKIP
class TestShellcheck:
    """Run shellcheck on the script to catch regressions."""

    def test_shellcheck_clean(self):
        """shellcheck reports no issues."""
        try:
            result = subprocess.run(
                ["shellcheck", str(SCRIPT)],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            pytest.skip("shellcheck not installed")
        assert result.returncode == 0, (
            f"shellcheck reported issues:\n{result.stdout}\n{result.stderr}"
        )


@PYTEST_BASH_SKIP
class TestScriptContents:
    """Static checks against the script source to pin contract properties."""

    SOURCE = SCRIPT.read_text(encoding="utf-8")

    def test_uses_strict_mode(self):
        """set -euo pipefail at top prevents silent failures."""
        assert "set -euo pipefail" in self.SOURCE

    def test_defaults_ruleset_name_to_branch_rules(self):
        """Default RULESET_NAME is 'branch-rules' (matches Python variant)."""
        assert 'RULESET_NAME="branch-rules"' in self.SOURCE

    def test_admin_role_is_hardcoded_bypass(self):
        """Admin role id=5 is the only hardcoded bypass (no random app IDs)."""
        assert '"actor_id":5' in self.SOURCE
        assert '"actor_type":"RepositoryRole"' in self.SOURCE

    def test_payload_omits_automatic_copilot_field(self):
        """Payload must NOT include automatic_copilot_code_review_enabled."""
        assert "automatic_copilot_code_review_enabled" not in self.SOURCE

    def test_payload_omits_do_not_enforce_on_create(self):
        """Payload must NOT include do_not_enforce_on_create."""
        assert "do_not_enforce_on_create" not in self.SOURCE

    def test_payload_has_pull_request_rule(self):
        """Payload builds a pull_request rule."""
        assert '"pull_request"' in self.SOURCE or "pull_request" in self.SOURCE

    def test_payload_has_required_status_checks_rule(self):
        """Payload builds a required_status_checks rule."""
        assert "required_status_checks" in self.SOURCE

    def test_review_count_is_zero(self):
        """No manual approval required — bots can auto-merge."""
        assert "required_approving_review_count: 0" in self.SOURCE

    def test_strict_policy_is_false(self):
        """strict_required_status_checks_policy: false for auto-merge."""
        assert "strict_required_status_checks_policy: false" in self.SOURCE

    def test_auto_detects_from_git_remote(self):
        """The script calls `git config --get remote.origin.url`."""
        assert "git config --get remote.origin.url" in self.SOURCE

    def test_handles_ssh_url_form(self):
        """The URL parser handles the git@host:OWNER/REPO form."""
        assert "git@" in self.SOURCE  # sed strips git@ prefix

    def test_handles_https_url_form(self):
        """The URL parser handles the https://host/OWNER/REPO form."""
        assert "https?://" in self.SOURCE or "https://" in self.SOURCE

    def test_strips_git_suffix(self):
        """The URL parser strips the trailing .git suffix."""
        assert ".git$" in self.SOURCE or 'sed -E' in self.SOURCE

    def test_legacy_adoption_loop_present(self):
        """The script adopts bypass_actors from pre-existing legacy rulesets."""
        assert "Adopting bypass_actors from legacy ruleset" in self.SOURCE

    def test_dry_run_prints_diagnostic(self):
        """Dry-run mode prints actual check-run names as a diagnostic."""
        assert "check-runs currently reported on HEAD" in self.SOURCE

    def test_idempotent_update_path_uses_put(self):
        """When an existing ruleset is found, the script uses PUT (not POST)."""
        assert "--method PUT" in self.SOURCE

    def test_create_path_uses_post(self):
        """When no existing ruleset is found, the script uses POST."""
        assert "--method POST" in self.SOURCE
