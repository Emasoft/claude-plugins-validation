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
    MAX_FRONTMATTER_BYTES,
    MAX_YAML_ALIASES,
    PROJECT_REJECTED_KEYS,
    PROJECT_REJECTED_NESTED_KEYS,
    SECRET_VALUE_PATTERNS,
    OversizedFileError,
    classify_file_scope,
    classify_folder_scope,
    contains_absolute_home_path,
    find_git_root,
    gitignore_covers_path,
    is_git_ignored,
    is_git_tracked,
    is_secret_value,
    list_tracked_files_under,
    looks_like_secret_key_name,
    redact_home_path,
    resolve_within,
    safe_load_jsonc,
    safe_parse_frontmatter,
    safe_read_text,
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

    def test_v2_1_119_pr_url_template_in_known_settings(self) -> None:
        """prUrlTemplate (v2.1.119) is a recognized top-level settings key."""
        from cc_scope_rules import KNOWN_SETTINGS_KEYS  # noqa: PLC0415
        assert "prUrlTemplate" in KNOWN_SETTINGS_KEYS

    def test_v2_1_118_wsl_inherits_in_managed_only(self) -> None:
        """wslInheritsWindowsSettings (v2.1.118) is a managed-only policy key."""
        from cc_scope_rules import KNOWN_SETTINGS_KEYS  # noqa: PLC0415
        assert "wslInheritsWindowsSettings" in MANAGED_ONLY_KEYS
        assert "wslInheritsWindowsSettings" in KNOWN_SETTINGS_KEYS


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


# =============================================================================
# Bounded I/O — safe_read_text + safe_load_jsonc + safe_parse_frontmatter
# =============================================================================


class TestSafeReadText:
    """safe_read_text enforces size caps and lets smaller reads through."""

    def test_small_file_reads_successfully(self, tmp_path: Path) -> None:
        """A file under the cap is returned verbatim."""
        f = tmp_path / "small.txt"
        f.write_text("hello\n", encoding="utf-8")
        assert safe_read_text(f, 1024) == "hello\n"

    def test_oversize_file_raises(self, tmp_path: Path) -> None:
        """A file larger than max_bytes raises OversizedFileError."""
        f = tmp_path / "big.txt"
        f.write_text("x" * 2048, encoding="utf-8")
        with pytest.raises(OversizedFileError):
            safe_read_text(f, 1024)

    def test_missing_file_raises_oserror(self, tmp_path: Path) -> None:
        """A missing file raises FileNotFoundError (subclass of OSError)."""
        with pytest.raises(OSError):
            safe_read_text(tmp_path / "nope.txt", 1024)


class TestSafeLoadJsonc:
    """safe_load_jsonc size-caps JSONC file reads."""

    def test_small_json_loads(self, tmp_path: Path) -> None:
        """Small JSON parses into a dict."""
        f = tmp_path / "settings.json"
        f.write_text('{"model": "opus"}\n', encoding="utf-8")
        result = safe_load_jsonc(f, 1024)
        assert result == {"model": "opus"}

    def test_oversize_json_raises(self, tmp_path: Path) -> None:
        """A JSON file over max_bytes raises OversizedFileError."""
        f = tmp_path / "huge.json"
        f.write_text("{" + '"k":"v",' * 5000 + '"last":1}', encoding="utf-8")
        with pytest.raises(OversizedFileError):
            safe_load_jsonc(f, 1024)


class TestSafeParseFrontmatter:
    """safe_parse_frontmatter rejects oversize + YAML-bomb content."""

    def test_parses_small_frontmatter(self) -> None:
        """Well-formed small frontmatter parses to a dict."""
        content = "---\nname: test\ndescription: hi\n---\nBody.\n"
        fm, body = safe_parse_frontmatter(content)
        assert fm is not None
        assert fm.get("name") == "test"
        assert body == "Body.\n" or body == "Body."

    def test_no_frontmatter_returns_none(self) -> None:
        """A document without --- fence returns (None, original content)."""
        content = "Just a body.\n"
        fm, body = safe_parse_frontmatter(content)
        assert fm is None
        assert body == content

    def test_unterminated_frontmatter_returns_none(self) -> None:
        """A document with opening --- but no closing fence returns None."""
        content = "---\nname: x\n"  # no closing ---
        fm, _body = safe_parse_frontmatter(content)
        assert fm is None

    def test_oversize_frontmatter_is_rejected(self) -> None:
        """Frontmatter larger than MAX_FRONTMATTER_BYTES is rejected."""
        padding = "x" * (MAX_FRONTMATTER_BYTES + 1024)
        content = f"---\nname: big\ndescription: {padding}\n---\nbody\n"
        fm, body = safe_parse_frontmatter(content)
        assert fm is None
        assert "body" in body

    def test_yaml_bomb_is_rejected(self) -> None:
        """A YAML document with too many anchor/alias markers is rejected.

        This is the billion-laughs mitigation — safe_parse_frontmatter
        pre-scans for &name and *name markers and bails when the count
        exceeds MAX_YAML_ALIASES, BEFORE invoking yaml.safe_load.
        """
        # Build a frontmatter with MAX_YAML_ALIASES + 10 anchor/alias markers
        markers = "\n".join(f"k{i}: &a{i} x" for i in range(MAX_YAML_ALIASES + 10))
        content = f"---\n{markers}\n---\nbody\n"
        fm, _body = safe_parse_frontmatter(content)
        assert fm is None

    def test_malformed_yaml_returns_none(self) -> None:
        """Syntactically broken YAML returns None, not an exception."""
        content = "---\n: : : : not valid\n---\n"
        fm, _body = safe_parse_frontmatter(content)
        assert fm is None


# =============================================================================
# Symlink containment — resolve_within
# =============================================================================


class TestResolveWithin:
    """resolve_within rejects paths that escape the project root."""

    def test_normal_path_inside_root(self, tmp_path: Path) -> None:
        """A normal path inside the root resolves."""
        root = tmp_path / "proj"
        root.mkdir()
        inside = root / "file.md"
        inside.write_text("x", encoding="utf-8")
        result = resolve_within(inside, root)
        assert result is not None
        assert result.name == "file.md"

    def test_symlink_outside_root_is_rejected(self, tmp_path: Path) -> None:
        """A symlink that targets outside the project returns None."""
        root = tmp_path / "proj"
        root.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        link = root / "link.md"
        link.symlink_to(outside)
        result = resolve_within(link, root)
        assert result is None

    def test_parent_directory_escape_is_rejected(self, tmp_path: Path) -> None:
        """A `..` path escape is rejected."""
        root = tmp_path / "proj"
        root.mkdir()
        sibling = tmp_path / "sibling.md"
        sibling.write_text("x", encoding="utf-8")
        # Craft a path that resolves outside
        result = resolve_within(root / ".." / "sibling.md", root)
        assert result is None


# =============================================================================
# Redaction — redact_home_path
# =============================================================================


class TestRedactHomePath:
    """redact_home_path replaces usernames with <REDACTED>."""

    def test_redacts_macos_username(self) -> None:
        """/Users/alice/... → /Users/<REDACTED>/..."""
        result = redact_home_path("/Users/alice/secret/file.sh")
        assert "alice" not in result
        assert "<REDACTED>" in result
        assert result.endswith("/secret/file.sh")

    def test_redacts_linux_username(self) -> None:
        """/home/bob/... → /home/<REDACTED>/..."""
        result = redact_home_path("/home/bob/.local/bin/tool")
        assert "bob" not in result
        assert "<REDACTED>" in result

    def test_redacts_windows_username(self) -> None:
        r"""C:\Users\Alice\... → C:\Users\<REDACTED>\..."""
        result = redact_home_path(r"C:\Users\Alice\AppData\tool.exe")
        assert "Alice" not in result
        assert "<REDACTED>" in result

    def test_non_home_path_unchanged(self) -> None:
        """Paths without a home segment pass through unchanged."""
        assert redact_home_path("/usr/local/bin/python") == "/usr/local/bin/python"
        assert redact_home_path("relative/path") == "relative/path"

    def test_empty_string_unchanged(self) -> None:
        """Empty string passes through."""
        assert redact_home_path("") == ""

    def test_non_string_unchanged(self) -> None:
        """Non-string inputs pass through unchanged (returned as-is)."""
        assert redact_home_path(None) is None  # type: ignore[arg-type]


# =============================================================================
# Case-insensitive home path detection (aegis LOW-1)
# =============================================================================


class TestCaseInsensitiveHomeDetection:
    """Unix home-path regexes catch case variants (macOS filesystem)."""

    def test_lowercase_users_is_detected(self) -> None:
        """/users/alice/ (lowercase u) is caught on case-insensitive FS."""
        assert contains_absolute_home_path("/users/alice/secret")

    def test_mixed_case_home_is_detected(self) -> None:
        """/Home/Bob/ is caught."""
        assert contains_absolute_home_path("/Home/Bob/file")

    def test_lowercase_root_is_detected(self) -> None:
        """/ROOT/ uppercase is caught."""
        assert contains_absolute_home_path("/ROOT/data")


# =============================================================================
# Batched git ls-files — list_tracked_files_under + gitignore_covers_path
# =============================================================================


class TestBatchedGitHelpers:
    """list_tracked_files_under returns a set of absolute tracked Paths."""

    def test_returns_tracked_files(self, git_repo: Path) -> None:
        """A folder with 3 tracked files returns a set of 3 Paths."""
        for name in ("a.md", "b.md", "c.md"):
            (git_repo / "dir" / name).parent.mkdir(exist_ok=True)
            (git_repo / "dir" / name).write_text("x", encoding="utf-8")
        _git(git_repo, "add", "dir/")
        _git(git_repo, "commit", "-m", "add dir", "--quiet")
        result = list_tracked_files_under(git_repo / "dir", git_repo)
        assert result is not None
        assert len(result) == 3
        names = {p.name for p in result}
        assert names == {"a.md", "b.md", "c.md"}

    def test_returns_empty_set_for_untracked_folder(self, git_repo: Path) -> None:
        """An untracked folder returns an empty set (git ls-files returns nothing)."""
        (git_repo / "new").mkdir()
        (git_repo / "new" / "x.md").write_text("x", encoding="utf-8")
        result = list_tracked_files_under(git_repo / "new", git_repo)
        assert result == set()

    def test_gitignore_covers_path_via_git(self, git_repo: Path) -> None:
        """gitignore_covers_path uses git check-ignore for accurate pattern matching."""
        (git_repo / ".gitignore").write_text("*.local.*\n", encoding="utf-8")
        _git(git_repo, "add", ".gitignore")
        _git(git_repo, "commit", "-m", "add gitignore", "--quiet")
        assert gitignore_covers_path(".claude/settings.local.json", git_repo)
        assert not gitignore_covers_path("src/main.py", git_repo)


# =============================================================================
# UTF-8 BOM handling (llm-ext EDGE-2)
# =============================================================================


class TestBomHandling:
    """safe_read_text and safe_load_jsonc transparently handle UTF-8 BOM."""

    def test_safe_read_text_strips_leading_bom(self, tmp_path: Path) -> None:
        """A file with a leading UTF-8 BOM reads without the BOM char."""
        f = tmp_path / "bom.md"
        f.write_bytes(b"\xef\xbb\xbfhello\n")
        result = safe_read_text(f, 1024)
        assert result == "hello\n"
        assert "\ufeff" not in result

    def test_safe_load_jsonc_parses_json_with_bom(self, tmp_path: Path) -> None:
        """A JSON file with a leading UTF-8 BOM (common on Windows) parses."""
        f = tmp_path / "settings.json"
        f.write_bytes(b'\xef\xbb\xbf{"model": "opus"}\n')
        result = safe_load_jsonc(f, 1024)
        assert result == {"model": "opus"}

    def test_safe_load_jsonc_parses_jsonc_with_bom_and_comments(
        self, tmp_path: Path
    ) -> None:
        """BOM + JSONC comments + trailing commas all parse cleanly."""
        payload = b'\xef\xbb\xbf{\n  // a comment\n  "k": "v",\n}\n'
        f = tmp_path / "settings.json"
        f.write_bytes(payload)
        result = safe_load_jsonc(f, 1024)
        assert result == {"k": "v"}


# =============================================================================
# Symlink-escape classification (aegis MEDIUM-1 + llm-ext LOGIC-2)
# =============================================================================


class TestSymlinkEscapeClassification:
    """classify_folder_scope / classify_file_scope reject symlink escapes."""

    def test_folder_symlink_outside_repo_classifies_missing(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """A folder symlink to outside the repo classifies as 'missing'."""
        outside = tmp_path / "outside-dir"
        outside.mkdir()
        (outside / "leaked.md").write_text("secret\n", encoding="utf-8")
        link = git_repo / ".claude" / "agents"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside)
        scope = classify_folder_scope(link, git_repo)
        assert scope == "missing"

    def test_file_symlink_outside_repo_classifies_missing(
        self, git_repo: Path, tmp_path: Path
    ) -> None:
        """A file symlink to outside the repo classifies as 'missing'."""
        outside = tmp_path / "outside.txt"
        outside.write_text("secret\n", encoding="utf-8")
        link = git_repo / ".claude" / "settings.json"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside)
        scope = classify_file_scope(link, git_repo)
        assert scope == "missing"

    def test_normal_folder_inside_repo_still_classified_correctly(
        self, git_repo: Path
    ) -> None:
        """Regression guard — plain folders (no symlinks) still work."""
        (git_repo / ".claude" / "agents").mkdir(parents=True)
        (git_repo / ".claude" / "agents" / "x.md").write_text(
            "---\nname: x\n---\n", encoding="utf-8"
        )
        _git(git_repo, "add", ".claude/agents/x.md")
        _git(git_repo, "commit", "-m", "add agent", "--quiet")
        scope = classify_folder_scope(git_repo / ".claude" / "agents", git_repo)
        assert scope == "project"
