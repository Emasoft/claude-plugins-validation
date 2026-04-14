#!/usr/bin/env python3
"""Claude Code scope rules and git-tracking classifier.

Shared by ``validate_project_scope.py`` and ``validate_local_scope.py``.
Contains:

- Taxonomy constants for settings.json keys (managed-only, global-config,
  project-rejected).
- Secret-value and absolute-home-path detectors for ``.claude/settings.json``
  and ``.mcp.json`` content checks.
- Git-tracking classifier helpers (``is_git_tracked``, ``is_git_ignored``,
  ``classify_folder_scope``, ``classify_file_scope``) used to decide whether
  an element is in project scope (tracked) or local scope (ignored /
  untracked).

References:

- https://code.claude.com/docs/en/settings.md
- https://code.claude.com/docs/en/mcp.md
- https://code.claude.com/docs/en/permissions.md
- TRDD-2be75e88 — design/tasks/TRDD-2be75e88-...-scope-validators.md
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Literal

__all__ = [
    "PROJECT_REJECTED_KEYS",
    "PROJECT_REJECTED_NESTED_KEYS",
    "MANAGED_ONLY_KEYS",
    "GLOBAL_CONFIG_KEYS",
    "SECRET_VALUE_PATTERNS",
    "SECRET_KEY_NAME_PATTERN",
    "ABSOLUTE_HOME_PATH_PATTERNS",
    "CLAUDE_VAR_PREFIXES",
    "Scope",
    "is_secret_value",
    "looks_like_secret_key_name",
    "contains_absolute_home_path",
    "uses_claude_var_expansion",
    "find_git_root",
    "is_git_tracked",
    "is_git_ignored",
    "classify_folder_scope",
    "classify_file_scope",
]


# =============================================================================
# Claude Code settings key taxonomy
# =============================================================================

# Per settings.md: these top-level keys are silently dropped when they appear
# in a git-tracked project settings.json (``.claude/settings.json``). They are
# still read from ``~/.claude/settings.json``, ``.claude/settings.local.json``,
# and managed settings. A CRITICAL finding means Claude Code will ignore the
# key, so the author's intent will NOT take effect.
PROJECT_REJECTED_KEYS: frozenset[str] = frozenset(
    {
        "autoMemoryDirectory",
        "autoMode",
        "useAutoModeDuringPlan",
    }
)

# Nested paths that Claude Code silently drops when set in project settings.
# Each entry is a dotted path tuple: ``("permissions", "skipDangerousModePermissionPrompt")``
# means ``settings.json -> permissions -> skipDangerousModePermissionPrompt``.
PROJECT_REJECTED_NESTED_KEYS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("permissions", "skipDangerousModePermissionPrompt"),
    }
)

# Per permissions.md + settings.md "Managed-only settings": these keys are
# silently ignored unless they appear in a managed settings file (macOS:
# ``/Library/Application Support/ClaudeCode/managed-settings.json``, Linux:
# ``/etc/claude-code/managed-settings.json``, Windows:
# ``C:\Program Files\ClaudeCode\managed-settings.json``).
MANAGED_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "allowedChannelPlugins",
        "allowedMcpServers",
        "deniedMcpServers",
        "allowManagedHooksOnly",
        "allowManagedMcpServersOnly",
        "allowManagedPermissionRulesOnly",
        "blockedMarketplaces",
        "channelsEnabled",
        "forceRemoteSettingsRefresh",
        "pluginTrustMessage",
        "strictKnownMarketplaces",
    }
)

# Per settings.md "Global config settings": these keys live in
# ``~/.claude.json`` only. Placing them in a settings.json file triggers a
# schema validation error.
GLOBAL_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "autoConnectIde",
        "autoInstallIdeExtension",
        "editorMode",
        "showTurnDuration",
        "terminalProgressBarEnabled",
        "teammateMode",
    }
)


# =============================================================================
# Secret and absolute-path detection
# =============================================================================

# Known secret formats. Each regex is anchored so a value matches only if the
# entire string looks like a secret. These are intentionally conservative to
# avoid false positives on ordinary strings.
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$"),               # Anthropic API key
    re.compile(r"^sk-[A-Za-z0-9_-]{32,}$"),                   # OpenAI-style
    re.compile(r"^ghp_[A-Za-z0-9]{30,}$"),                    # GitHub personal access token
    re.compile(r"^gho_[A-Za-z0-9]{30,}$"),                    # GitHub OAuth token
    re.compile(r"^ghs_[A-Za-z0-9]{30,}$"),                    # GitHub server-to-server
    re.compile(r"^github_pat_[A-Za-z0-9_]{20,}$"),            # GitHub fine-grained PAT
    re.compile(r"^AIza[A-Za-z0-9_-]{30,}$"),                  # Google API key
    re.compile(r"^xox[baprs]-[A-Za-z0-9-]{20,}$"),            # Slack tokens
    re.compile(r"^AKIA[A-Z0-9]{16}$"),                        # AWS access key ID
    re.compile(                                                # JWT (header.payload.signature)
        r"^eyJ[A-Za-z0-9_=-]+\.eyJ[A-Za-z0-9_=-]+\.[A-Za-z0-9_=.+/-]+$"
    ),
)

# Substring match for key names that typically hold secrets. Used to focus
# secret-value scanning on fields where it matters (env, headers, etc.).
SECRET_KEY_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|secret|token|password|auth[_-]?token|credential|bearer)"
)

# Absolute home directory path patterns. Applied to shared settings fields to
# catch machine-specific paths that will break for other team members.
ABSOLUTE_HOME_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|\s|[\"'=])/Users/[^/\s\"']+/"),
    re.compile(r"(?:^|\s|[\"'=])/home/[^/\s\"']+/"),
    re.compile(r"(?:^|\s|[\"'=])/root/"),
    re.compile(r"(?:^|\s|[\"'=])C:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s\"']+[\\/]", re.IGNORECASE),
    re.compile(r"(?:^|\s|[\"'=])~[/\\]"),
)

# Claude-Code-safe variable prefixes. Any field that starts with one of these
# is considered portable and should NOT be flagged for absolute-path issues.
CLAUDE_VAR_PREFIXES: tuple[str, ...] = (
    "$CLAUDE_PROJECT_DIR",
    "${CLAUDE_PROJECT_DIR}",
    "${CLAUDE_PLUGIN_ROOT}",
    "${CLAUDE_PLUGIN_DATA}",
    "${CLAUDE_SKILL_DIR}",
    "${CLAUDE_ENV_FILE}",
)


def is_secret_value(value: object) -> bool:
    """Return True when ``value`` looks like a literal credential.

    Returns False for ``${VAR}`` / ``${VAR:-default}`` expansions (the
    documented portable pattern), for empty strings, and for non-strings.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith("${") and stripped.endswith("}"):
        return False
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


def looks_like_secret_key_name(name: str) -> bool:
    """Return True when ``name`` looks like it holds a credential."""
    return bool(SECRET_KEY_NAME_PATTERN.search(name))


def uses_claude_var_expansion(value: str) -> bool:
    """Return True when ``value`` starts with a portable Claude variable."""
    stripped = value.lstrip("\"' ")
    for prefix in CLAUDE_VAR_PREFIXES:
        if stripped.startswith(prefix):
            return True
    return False


def contains_absolute_home_path(text: object) -> bool:
    """Return True when ``text`` contains an absolute home directory path.

    Non-strings return False. Values that start with a Claude variable
    expansion (e.g. ``$CLAUDE_PROJECT_DIR``) are considered safe and return
    False.
    """
    if not isinstance(text, str):
        return False
    if not text:
        return False
    if uses_claude_var_expansion(text):
        return False
    for pattern in ABSOLUTE_HOME_PATH_PATTERNS:
        if pattern.search(text):
            return True
    return False


# =============================================================================
# Git-tracking classifier
# =============================================================================

Scope = Literal["project", "local", "missing", "no-git"]


def find_git_root(path: Path) -> Path | None:
    """Walk up from ``path`` to find the nearest ``.git`` directory.

    Returns the repo root Path or None when no parent has ``.git``.
    The walk is bounded to 50 levels as a sanity limit.
    """
    try:
        current = path.resolve()
    except OSError:
        return None
    for _ in range(50):
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _relative_to_root(path: Path, repo_root: Path) -> Path | None:
    """Return the path relative to ``repo_root`` or None on failure."""
    try:
        return path.resolve().relative_to(repo_root.resolve())
    except (OSError, ValueError):
        return None


def is_git_tracked(path: Path, repo_root: Path | None = None) -> bool:
    """Return True when ``path`` is a file currently tracked by git.

    Returns False when:

    - ``path`` does not exist
    - The path is not inside any git repository (no ``.git`` ancestor)
    - The file is in ``.gitignore`` or otherwise untracked
    - The ``git`` binary is unavailable

    Uses ``git ls-files --error-unmatch`` which has the precise semantics of
    "is this path a tracked entry in the current index".
    """
    if not path.exists():
        return False
    if repo_root is None:
        repo_root = find_git_root(path)
        if repo_root is None:
            return False
    rel = _relative_to_root(path, repo_root)
    if rel is None:
        return False
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(rel)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def is_git_ignored(path: Path, repo_root: Path | None = None) -> bool:
    """Return True when ``path`` matches a ``.gitignore`` rule in ``repo_root``.

    Returns False when the path is not inside a git repo or when git is
    unavailable. An untracked-but-not-ignored file returns False.
    """
    if repo_root is None:
        repo_root = find_git_root(path)
        if repo_root is None:
            return False
    rel = _relative_to_root(path, repo_root)
    if rel is None:
        return False
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(rel)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def classify_folder_scope(folder: Path, repo_root: Path | None = None) -> Scope:
    """Classify a folder as project / local / missing / no-git.

    Semantics per TRDD-2be75e88 section 3.4:

    - ``missing``: the folder does not exist
    - ``no-git``: the folder has no ``.git`` ancestor
    - ``local``: the folder is git-ignored OR has zero tracked files
    - ``project``: the folder has at least one tracked file inside it
    """
    if not folder.exists() or not folder.is_dir():
        return "missing"
    if repo_root is None:
        repo_root = find_git_root(folder)
        if repo_root is None:
            return "no-git"
    if is_git_ignored(folder, repo_root):
        return "local"
    rel = _relative_to_root(folder, repo_root)
    if rel is None:
        return "no-git"
    try:
        result = subprocess.run(
            ["git", "ls-files", "--", f"{rel}/"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "local"
    if result.returncode == 0 and result.stdout.strip():
        return "project"
    return "local"


def classify_file_scope(file: Path, repo_root: Path | None = None) -> Scope:
    """Classify a single file as project / local / missing / no-git.

    - ``missing``: the file does not exist
    - ``no-git``: the file has no ``.git`` ancestor
    - ``project``: the file is tracked
    - ``local``: the file is ignored or otherwise untracked
    """
    if not file.exists() or not file.is_file():
        return "missing"
    if repo_root is None:
        repo_root = find_git_root(file)
        if repo_root is None:
            return "no-git"
    if is_git_tracked(file, repo_root):
        return "project"
    return "local"
