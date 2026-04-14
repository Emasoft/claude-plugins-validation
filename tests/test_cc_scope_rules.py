#!/usr/bin/env python3
"""Tests for cc_scope_rules.py — scope taxonomy and git-tracking classifier.

Covers:
- Taxonomy constants (MANAGED_ONLY_KEYS, GLOBAL_CONFIG_KEYS, PROJECT_REJECTED_KEYS)
- Secret detection (is_secret_value, looks_like_secret_key_name)
- Absolute-home-path detection (contains_absolute_home_path)
- Claude variable expansion safety (uses_claude_var_expansion)
- Git classification (is_git_tracked, is_git_ignored,
  classify_file_scope, classify_folder_scope, find_git_root)

All git tests use real ``git`` subprocess calls on a temporary repo built
under ``tmp_path``. No mocks — per the project rule that mocked tests
produce no real signal.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cc_scope_rules import (  # noqa: E402
    CLAUDE_VAR_PREFIXES,
    GLOBAL_CONFIG_KEYS,
    MANAGED_ONLY_KEYS,
    PROJECT_REJECTED_KEYS,
    PROJECT_REJECTED_NESTED_KEYS,
    SECRET_VALUE_PATTERNS,
    classify_file_scope,
    classify_folder_scope,
    contains_absolute_home_path,
    find_git_root,
    is_git_ignored,
    is_git_tracked,
    is_secret_value,
    looks_like_secret_key_name,
    uses_claude_var_expansion,
)

# =============================================================================
# Helpers: build a real git repo under tmp_path
# =============================================================================


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the repo, fail the test on non-zero exit."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = f"git {' '.join(args)} failed: {result.stderr}"
        raise AssertionError(msg)
    return result


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Build an empty initialised git repo under tmp_path."""
    if shutil.which("git") is None:
        pytest.skip("git binary not available")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


@pytest.fixture
def non_git_dir(tmp_path: Path) -> Path:
    """Plain directory with no .git folder."""
    d = tmp_path / "plain"
    d.mkdir()
    return d


# =============================================================================
# Taxonomy constants
# =============================================================================


class TestTaxonomyConstants:
    """The taxonomy constants must contain the keys documented in settings.md."""

    def test_project_rejected_keys_contains_auto_memory_directory(self) -> None:
        """PROJECT_REJECTED_KEYS lists autoMemoryDirectory — settings.md flags it explicitly."""
        assert "autoMemoryDirectory" in PROJECT_REJECTED_KEYS

    def test_project_rejected_keys_contains_auto_mode(self) -> None:
        """PROJECT_REJECTED_KEYS lists autoMode — not read from shared project settings."""
        assert "autoMode" in PROJECT_REJECTED_KEYS
        assert "useAutoModeDuringPlan" in PROJECT_REJECTED_KEYS

    def test_project_rejected_nested_includes_skip_dangerous_prompt(self) -> None:
        """skipDangerousModePermissionPrompt is documented as nested under permissions."""
        assert ("permissions", "skipDangerousModePermissionPrompt") in PROJECT_REJECTED_NESTED_KEYS

    def test_managed_only_keys_contains_mcp_allow_deny(self) -> None:
        """MANAGED_ONLY_KEYS covers the MCP allow/deny lists documented as managed-only."""
        assert "allowedMcpServers" in MANAGED_ONLY_KEYS
        assert "deniedMcpServers" in MANAGED_ONLY_KEYS

    def test_managed_only_keys_contains_managed_flags(self) -> None:
        """MANAGED_ONLY_KEYS covers allowManaged* flags documented as policy-only."""
        for key in (
            "allowManagedHooksOnly",
            "allowManagedMcpServersOnly",
            "allowManagedPermissionRulesOnly",
        ):
            assert key in MANAGED_ONLY_KEYS

    def test_global_config_keys_contains_editor_mode(self) -> None:
        """GLOBAL_CONFIG_KEYS lists editorMode — a ~/.claude.json-only key."""
        assert "editorMode" in GLOBAL_CONFIG_KEYS
        assert "autoConnectIde" in GLOBAL_CONFIG_KEYS
        assert "teammateMode" in GLOBAL_CONFIG_KEYS

    def test_taxonomy_sets_are_disjoint(self) -> None:
        """The three key classes must not overlap — a key belongs to exactly one bucket."""
        assert PROJECT_REJECTED_KEYS.isdisjoint(MANAGED_ONLY_KEYS)
        assert PROJECT_REJECTED_KEYS.isdisjoint(GLOBAL_CONFIG_KEYS)
        assert MANAGED_ONLY_KEYS.isdisjoint(GLOBAL_CONFIG_KEYS)

    def test_taxonomy_sets_are_non_empty(self) -> None:
        """Sanity: constants were populated, not left empty by a refactor."""
        assert PROJECT_REJECTED_KEYS
        assert MANAGED_ONLY_KEYS
        assert GLOBAL_CONFIG_KEYS
        assert PROJECT_REJECTED_NESTED_KEYS
        assert SECRET_VALUE_PATTERNS


# =============================================================================
# Secret detection
# =============================================================================


class TestSecretDetection:
    """is_secret_value, looks_like_secret_key_name, SECRET_VALUE_PATTERNS."""

    def test_detects_anthropic_api_key_format(self) -> None:
        """Anthropic API keys (sk-ant-...) are flagged as secrets."""
        assert is_secret_value("sk-ant-api03-" + "a" * 40)

    def test_detects_github_pat(self) -> None:
        """GitHub personal access tokens (ghp_...) are flagged as secrets."""
        assert is_secret_value("ghp_" + "a" * 36)

    def test_detects_google_api_key(self) -> None:
        """Google API keys (AIza...) are flagged as secrets."""
        assert is_secret_value("AIza" + "B" * 35)

    def test_detects_aws_access_key_id(self) -> None:
        """AWS access key IDs (AKIA + 16 alnum) are flagged as secrets."""
        assert is_secret_value("AKIA" + "A" * 16)

    def test_detects_jwt_token(self) -> None:
        """JWT tokens (eyJ... . eyJ... . sig) are flagged as secrets."""
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.SflKxwRJSMeKKF2QT4f"
        assert is_secret_value(jwt)

    def test_env_var_expansion_is_not_a_secret(self) -> None:
        """${VAR} and ${VAR:-default} are the portable pattern — NOT secrets."""
        assert not is_secret_value("${ANTHROPIC_API_KEY}")
        assert not is_secret_value("${API_KEY:-default-value}")

    def test_empty_string_is_not_a_secret(self) -> None:
        """Empty strings return False."""
        assert not is_secret_value("")
        assert not is_secret_value("   ")

    def test_non_string_is_not_a_secret(self) -> None:
        """Non-string inputs (None, int, dict) return False without crashing."""
        assert not is_secret_value(None)
        assert not is_secret_value(42)
        assert not is_secret_value({"a": 1})

    def test_ordinary_strings_are_not_secrets(self) -> None:
        """Words and short strings are not flagged as secrets."""
        assert not is_secret_value("hello")
        assert not is_secret_value("production")
        assert not is_secret_value("/path/to/file")

    def test_secret_key_name_matches_api_key(self) -> None:
        """looks_like_secret_key_name catches API_KEY, ACCESS_TOKEN, SECRET_*."""
        assert looks_like_secret_key_name("ANTHROPIC_API_KEY")
        assert looks_like_secret_key_name("github_token")
        assert looks_like_secret_key_name("DB_PASSWORD")
        assert looks_like_secret_key_name("access_key")
        assert looks_like_secret_key_name("bearer_token")

    def test_secret_key_name_does_not_match_ordinary_names(self) -> None:
        """Ordinary env var names like HOSTNAME, LOCALE are not matched."""
        assert not looks_like_secret_key_name("HOSTNAME")
        assert not looks_like_secret_key_name("LOCALE")
        assert not looks_like_secret_key_name("PATH")


# =============================================================================
# Absolute home path detection
# =============================================================================


class TestAbsoluteHomePathDetection:
    """contains_absolute_home_path + uses_claude_var_expansion."""

    def test_detects_macos_user_home(self) -> None:
        """/Users/alice/... is flagged."""
        assert contains_absolute_home_path("/Users/alice/bin/tool")

    def test_detects_linux_user_home(self) -> None:
        """/home/bob/... is flagged."""
        assert contains_absolute_home_path("/home/bob/.local/bin/x")

    def test_detects_windows_user_home(self) -> None:
        r"""C:\Users\Alice\... is flagged (case-insensitive)."""
        assert contains_absolute_home_path(r"C:\Users\Alice\AppData\tool.exe")

    def test_detects_tilde_home(self) -> None:
        """~/.claude/... is flagged."""
        assert contains_absolute_home_path("~/.claude/hooks/check.sh")

    def test_ignores_portable_claude_project_dir(self) -> None:
        """$CLAUDE_PROJECT_DIR paths are portable — not flagged."""
        assert not contains_absolute_home_path('"$CLAUDE_PROJECT_DIR"/.claude/hooks/check.sh')
        assert not contains_absolute_home_path("$CLAUDE_PROJECT_DIR/scripts/run.sh")

    def test_ignores_portable_plugin_root(self) -> None:
        """${CLAUDE_PLUGIN_ROOT} paths are portable — not flagged."""
        assert not contains_absolute_home_path("${CLAUDE_PLUGIN_ROOT}/bin/x")

    def test_ignores_relative_paths(self) -> None:
        """Relative paths have no home and are not flagged."""
        assert not contains_absolute_home_path("./scripts/run.sh")
        assert not contains_absolute_home_path("scripts/run.sh")

    def test_ignores_non_string_inputs(self) -> None:
        """None / int / dict inputs return False safely."""
        assert not contains_absolute_home_path(None)
        assert not contains_absolute_home_path(42)
        assert not contains_absolute_home_path({"x": 1})

    def test_uses_claude_var_expansion_all_prefixes(self) -> None:
        """uses_claude_var_expansion recognises all documented Claude variables."""
        for prefix in CLAUDE_VAR_PREFIXES:
            value = f"{prefix}/some/path"
            assert uses_claude_var_expansion(value), f"Should recognise {prefix}"


# =============================================================================
# Git classifier — find_git_root
# =============================================================================


class TestFindGitRoot:
    """find_git_root walks up to find the nearest .git ancestor."""

    def test_returns_repo_for_root(self, git_repo: Path) -> None:
        """Called on the repo root itself, find_git_root returns the repo root."""
        assert find_git_root(git_repo) == git_repo.resolve()

    def test_returns_repo_for_nested_path(self, git_repo: Path) -> None:
        """Called on a nested subdir, find_git_root returns the repo root."""
        nested = git_repo / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert find_git_root(nested) == git_repo.resolve()

    def test_returns_none_for_non_git_dir(self, non_git_dir: Path) -> None:
        """Called on a directory with no .git ancestor, returns None."""
        # Note: /tmp itself may not have .git ancestors on most systems.
        # We create a subdirectory to avoid false positives.
        deep = non_git_dir / "nested"
        deep.mkdir()
        # If a parent of tmp_path happens to have .git, skip.
        parent = deep
        for _ in range(50):
            if (parent / ".git").exists():
                pytest.skip("tmp_path lives inside a git checkout")
            if parent.parent == parent:
                break
            parent = parent.parent
        assert find_git_root(deep) is None


# =============================================================================
# Git classifier — is_git_tracked / is_git_ignored
# =============================================================================


class TestGitTrackingClassifier:
    """is_git_tracked + is_git_ignored against a real git repo."""

    def test_tracked_file_is_tracked(self, git_repo: Path) -> None:
        """A committed file is classified as tracked."""
        f = git_repo / "tracked.txt"
        f.write_text("hello\n", encoding="utf-8")
        _git(git_repo, "add", "tracked.txt")
        _git(git_repo, "commit", "-m", "add tracked.txt")
        assert is_git_tracked(f, git_repo)

    def test_untracked_file_is_not_tracked(self, git_repo: Path) -> None:
        """A file that exists but was never added returns False."""
        f = git_repo / "untracked.txt"
        f.write_text("hello\n", encoding="utf-8")
        assert not is_git_tracked(f, git_repo)

    def test_ignored_file_is_not_tracked(self, git_repo: Path) -> None:
        """A file matching .gitignore returns False for is_git_tracked."""
        (git_repo / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
        _git(git_repo, "add", ".gitignore")
        _git(git_repo, "commit", "-m", "add gitignore")
        secret = git_repo / "secret.txt"
        secret.write_text("shh\n", encoding="utf-8")
        assert not is_git_tracked(secret, git_repo)

    def test_ignored_file_is_ignored(self, git_repo: Path) -> None:
        """A file matching .gitignore returns True for is_git_ignored."""
        (git_repo / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
        _git(git_repo, "add", ".gitignore")
        _git(git_repo, "commit", "-m", "add gitignore")
        secret = git_repo / "secret.txt"
        secret.write_text("shh\n", encoding="utf-8")
        assert is_git_ignored(secret, git_repo)

    def test_tracked_file_is_not_ignored(self, git_repo: Path) -> None:
        """A committed file returns False for is_git_ignored."""
        f = git_repo / "tracked.txt"
        f.write_text("x\n", encoding="utf-8")
        _git(git_repo, "add", "tracked.txt")
        _git(git_repo, "commit", "-m", "add")
        assert not is_git_ignored(f, git_repo)

    def test_missing_file_is_not_tracked(self, git_repo: Path) -> None:
        """A path that does not exist returns False for is_git_tracked."""
        ghost = git_repo / "ghost.txt"
        assert not is_git_tracked(ghost, git_repo)

    def test_file_outside_repo_is_not_tracked(self, git_repo: Path, tmp_path: Path) -> None:
        """A file that exists but is outside the repo returns False."""
        outside = tmp_path / "outside.txt"
        outside.write_text("x\n", encoding="utf-8")
        assert not is_git_tracked(outside, git_repo)

    def test_non_git_repo_file_is_not_tracked(self, non_git_dir: Path) -> None:
        """A file in a directory without .git returns False for is_git_tracked."""
        # Guard: skip if tmp_path happens to live in a git checkout
        for parent in [non_git_dir, *non_git_dir.parents]:
            if (parent / ".git").exists():
                pytest.skip("tmp_path lives inside a git checkout")
        f = non_git_dir / "plain.txt"
        f.write_text("x\n", encoding="utf-8")
        assert not is_git_tracked(f)

    def test_auto_detects_repo_root(self, git_repo: Path) -> None:
        """When repo_root is None, classifier walks up from the path to find it."""
        nested = git_repo / "a" / "b"
        nested.mkdir(parents=True)
        f = nested / "file.txt"
        f.write_text("x\n", encoding="utf-8")
        _git(git_repo, "add", "a/b/file.txt")
        _git(git_repo, "commit", "-m", "add nested")
        assert is_git_tracked(f)  # no repo_root arg, must auto-detect


# =============================================================================
# Git classifier — classify_folder_scope
# =============================================================================


class TestClassifyFolderScope:
    """classify_folder_scope with various .gitignore and tracking patterns."""

    def test_tracked_folder_is_project(self, git_repo: Path) -> None:
        """A folder with committed files classifies as project-scope."""
        (git_repo / ".claude" / "agents").mkdir(parents=True)
        (git_repo / ".claude" / "agents" / "alice.md").write_text("---\nname: alice\n---\n")
        _git(git_repo, "add", ".claude/agents/alice.md")
        _git(git_repo, "commit", "-m", "add agent")
        scope = classify_folder_scope(git_repo / ".claude" / "agents", git_repo)
        assert scope == "project"

    def test_ignored_folder_is_local(self, git_repo: Path) -> None:
        """A folder listed in .gitignore classifies as local-scope."""
        (git_repo / ".gitignore").write_text(".claude/agents/\n", encoding="utf-8")
        _git(git_repo, "add", ".gitignore")
        _git(git_repo, "commit", "-m", "ignore agents")
        (git_repo / ".claude" / "agents").mkdir(parents=True)
        (git_repo / ".claude" / "agents" / "alice.md").write_text("---\nname: alice\n---\n")
        scope = classify_folder_scope(git_repo / ".claude" / "agents", git_repo)
        assert scope == "local"

    def test_untracked_folder_with_no_tracked_files_is_local(self, git_repo: Path) -> None:
        """A folder that exists but has zero tracked files is local-scope."""
        (git_repo / ".claude" / "skills").mkdir(parents=True)
        (git_repo / ".claude" / "skills" / "draft.md").write_text("draft\n")
        # Never added to git
        scope = classify_folder_scope(git_repo / ".claude" / "skills", git_repo)
        assert scope == "local"

    def test_missing_folder(self, git_repo: Path) -> None:
        """A non-existent folder classifies as 'missing'."""
        scope = classify_folder_scope(git_repo / ".claude" / "absent", git_repo)
        assert scope == "missing"

    def test_non_git_folder_is_no_git(self, non_git_dir: Path) -> None:
        """A folder with no .git ancestor classifies as 'no-git'."""
        for parent in [non_git_dir, *non_git_dir.parents]:
            if (parent / ".git").exists():
                pytest.skip("tmp_path lives inside a git checkout")
        (non_git_dir / "sub").mkdir()
        scope = classify_folder_scope(non_git_dir / "sub")
        assert scope == "no-git"


# =============================================================================
# Git classifier — classify_file_scope
# =============================================================================


class TestClassifyFileScope:
    """classify_file_scope with various tracking patterns."""

    def test_tracked_file_is_project(self, git_repo: Path) -> None:
        """A committed file classifies as project-scope."""
        f = git_repo / ".claude" / "settings.json"
        f.parent.mkdir()
        f.write_text("{}\n", encoding="utf-8")
        _git(git_repo, "add", ".claude/settings.json")
        _git(git_repo, "commit", "-m", "add settings")
        assert classify_file_scope(f, git_repo) == "project"

    def test_ignored_file_is_local(self, git_repo: Path) -> None:
        """A gitignored file classifies as local-scope."""
        (git_repo / ".gitignore").write_text(".claude/settings.local.json\n", encoding="utf-8")
        _git(git_repo, "add", ".gitignore")
        _git(git_repo, "commit", "-m", "ignore local settings")
        f = git_repo / ".claude" / "settings.local.json"
        f.parent.mkdir(exist_ok=True)
        f.write_text("{}\n", encoding="utf-8")
        assert classify_file_scope(f, git_repo) == "local"

    def test_untracked_file_is_local(self, git_repo: Path) -> None:
        """A file that exists but was never added classifies as local-scope."""
        f = git_repo / ".claude" / "settings.json"
        f.parent.mkdir()
        f.write_text("{}\n", encoding="utf-8")
        # Never `git add`ed
        assert classify_file_scope(f, git_repo) == "local"

    def test_missing_file_is_missing(self, git_repo: Path) -> None:
        """A non-existent file classifies as 'missing'."""
        f = git_repo / ".claude" / "nope.json"
        assert classify_file_scope(f, git_repo) == "missing"
