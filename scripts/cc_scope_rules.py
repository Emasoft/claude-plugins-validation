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
- Bounded I/O helpers (``safe_read_text``, ``safe_load_jsonc``,
  ``safe_parse_frontmatter``) that cap memory usage on adversarial inputs
  and scrub secrets from error messages before they reach the report.
- Sandbox helpers (``resolve_within``, ``gitignore_covers_path``,
  ``list_tracked_files_under``) used by the orchestrators to keep file
  walks contained inside the validated project and batched to one git
  call per folder.

This module's resource and security invariants are load-bearing for the
scope validators' ability to process untrusted project input. See the
aegis security audit in ``docs_dev/aegis-security-audit-20260414.md`` for
the threat model and the rationale for each bound.

References:

- https://code.claude.com/docs/en/settings.md
- https://code.claude.com/docs/en/mcp.md
- https://code.claude.com/docs/en/permissions.md
- TRDD-2be75e88 — design/tasks/TRDD-2be75e88-...-scope-validators.md
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml

if TYPE_CHECKING:
    from cpv_validation_common import ValidationReport

__all__ = [
    "PROJECT_REJECTED_KEYS",
    "PROJECT_REJECTED_NESTED_KEYS",
    "MANAGED_ONLY_KEYS",
    "MANAGED_ONLY_NESTED_KEYS",
    "GLOBAL_CONFIG_KEYS",
    "PLUGIN_ONLY_KEYS",
    "KNOWN_SETTINGS_KEYS",
    "SECRET_VALUE_PATTERNS",
    "SECRET_KEY_NAME_PATTERN",
    "SECRET_ENV_VAR_NAMES",
    "ABSOLUTE_HOME_PATH_PATTERNS",
    "CLAUDE_VAR_PREFIXES",
    "MAX_SETTINGS_JSON_BYTES",
    "MAX_MCP_JSON_BYTES",
    "MAX_MARKDOWN_BYTES",
    "MAX_CLAUDE_MD_BYTES",
    "MAX_HOME_CLAUDE_JSON_BYTES",
    "MAX_GITIGNORE_BYTES",
    "MAX_FRONTMATTER_BYTES",
    "MAX_YAML_ALIASES",
    "MAX_FILES_PER_FOLDER",
    "MAX_CLAUDE_MD_IMPORT_DEPTH",
    "Scope",
    "is_secret_value",
    "looks_like_secret_key_name",
    "contains_absolute_home_path",
    "uses_claude_var_expansion",
    "redact_home_path",
    "find_git_root",
    "is_git_tracked",
    "is_git_ignored",
    "classify_folder_scope",
    "classify_file_scope",
    "resolve_within",
    "gitignore_covers_path",
    "list_tracked_files_under",
    "safe_read_text",
    "safe_load_jsonc",
    "safe_parse_frontmatter",
    "extract_at_path_imports",
    "validate_claude_md_imports",
    "ENABLED_PLUGIN_RE",
    "resolve_plugin_cache_dir",
]


# =============================================================================
# Resource limits (aegis HIGH-1, HIGH-2, MEDIUM-5, LOW-2)
# =============================================================================

# Size caps for file reads, chosen to comfortably fit all real-world content
# while rejecting adversarial inputs. These are applied BEFORE ``read_text``
# or JSONC parsing so the validator can never be OOM'd by a hostile project.
MAX_SETTINGS_JSON_BYTES: int = 1 * 1024 * 1024        # 1 MB — settings.json / settings.local.json
MAX_MCP_JSON_BYTES: int = 1 * 1024 * 1024             # 1 MB — .mcp.json
MAX_MARKDOWN_BYTES: int = 256 * 1024                  # 256 KB — individual agent/skill/command/rule .md
MAX_CLAUDE_MD_BYTES: int = 2 * 1024 * 1024            # 2 MB — CLAUDE.md / CLAUDE.local.md
MAX_HOME_CLAUDE_JSON_BYTES: int = 16 * 1024 * 1024    # 16 MB — ~/.claude.json (many projects)
MAX_GITIGNORE_BYTES: int = 1 * 1024 * 1024            # 1 MB — .gitignore

# Frontmatter size cap + alias count limit close the YAML "billion laughs"
# bomb surface on adversarial SKILL.md / agent .md files.
MAX_FRONTMATTER_BYTES: int = 64 * 1024                # 64 KB
MAX_YAML_ALIASES: int = 100                           # anchors + references combined

# Per-folder file count cap on rglob walks — prevents a hostile project from
# forcing an unbounded tree walk via millions of .md files or directory symlinks.
MAX_FILES_PER_FOLDER: int = 10_000

# CLAUDE.md / CLAUDE.local.md ``@path/to/file.md`` imports are recursively loaded
# by Claude Code up to a maximum depth of 5 (per memory.md L95-107). Deeper
# chains are silently truncated — we flag them as MAJOR so the author knows the
# import will not fire. A file importing itself (depth increment with the same
# path in the chain) is a circular import and also flagged.
MAX_CLAUDE_MD_IMPORT_DEPTH: int = 5


# =============================================================================
# Git binary resolution (aegis LOW-4)
# =============================================================================

# Pin the ``git`` binary at import time so later PATH changes cannot substitute
# a malicious shim. ``None`` means git is unavailable — classifier helpers
# return False/"no-git" gracefully in that case.
GIT_BIN: str | None = shutil.which("git")


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
        "forceLoginMethod",  # memory.md L272 — admin authentication enforcement
        "forceLoginOrgUUID",  # memory.md L272 — organization lock
        "forceRemoteSettingsRefresh",
        "pluginTrustMessage",
        "strictKnownMarketplaces",
    }
)

# Per permission-modes.md: admin kill-switches nested under ``permissions``.
# Silently ignored outside managed settings — the value "disable" only binds
# when the key appears in a managed-settings file (or server-managed settings).
# Placing these in project settings has no effect; CPV emits MAJOR so users
# move them to the correct scope.
MANAGED_ONLY_NESTED_KEYS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("permissions", "disableAutoMode"),  # permission-modes.md L154
        ("permissions", "disableBypassPermissionsMode"),  # permission-modes.md L258
        ("autoMode", "environment"),  # server-managed-settings.md L85-94 — trusted-infra list
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

# Per plugins-reference.md: these top-level keys are recognized ONLY inside
# a plugin package's ``plugin.json``. Placing them in any settings file
# (``settings.json`` / ``settings.local.json`` / ``~/.claude.json``) is a
# CRITICAL error — Claude Code silently drops the block, so the author's
# declaration of LSP / background monitors never takes effect. The correct
# home for these is the owning plugin's manifest.
PLUGIN_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "lspServers",
        "monitors",
    }
)

# Per settings.md + memory.md + channels.md + scheduled-tasks.md +
# monitoring-usage.md: settings keys that are VALID in one or more settings
# scopes (not plugin-only, not project-rejected). This set is used for gentle
# typo detection — unknown keys produce an INFO when validation is verbose,
# not a MAJOR. Claude Code silently ignores truly unknown keys at runtime, so
# this is a UX aid, not a hard gate. The union includes project/local/user/
# managed-valid keys; per-scope restrictions are enforced separately via
# MANAGED_ONLY_KEYS / GLOBAL_CONFIG_KEYS / PLUGIN_ONLY_KEYS.
KNOWN_SETTINGS_KEYS: frozenset[str] = frozenset(
    {
        # Core
        "$schema",
        "apiKeyHelper",
        "awsAuthRefresh",
        "awsCredentialExport",
        "env",
        "hooks",
        "includeCoAuthoredBy",  # deprecated
        "mcpServers",
        "model",
        "otelHeadersHelper",  # monitoring-usage.md — admin-managed script for OTEL headers
        "outputStyle",  # claude-directory.md L116/L612 — active output style
        "permissions",
        "sandbox",
        "statusLine",
        "fileSuggestion",
        "subagentStatusLine",  # v2.1.x — plugin-shipped settings key per plugins.md L278
        # Cleanup / retention
        "cleanupPeriodDays",
        # Memory / CLAUDE.md
        "autoMemoryEnabled",  # memory.md L308-313
        "autoMemoryDirectory",
        "autoMode",
        "useAutoModeDuringPlan",
        "claudeMdExcludes",  # memory.md L285-294
        # Skill shell-exec control (skills.md L414)
        "disableSkillShellExecution",
        # UI
        "tui",  # v2.1.110 — flicker-free rendering mode
        "autoScrollEnabled",  # v2.1.110 — fullscreen auto-scroll toggle
        "showTurnDuration",
        "terminalProgressBarEnabled",
        "editorMode",
        "teammateMode",
        "autoConnectIde",
        "autoInstallIdeExtension",
        # Git hint overrides
        "includeGitInstructions",
        "respectGitignore",
        "awaySummaryEnabled",
        # Plugin / marketplace configuration
        "extraKnownMarketplaces",
        "enableAllProjectMcpServers",
        "enabledMcpjsonServers",
        "disabledMcpjsonServers",
        "enabledPlugins",
        "disabledPlugins",
        "pluginConfigs",
        # Managed-only (kept here for typo detection; semantics enforced elsewhere)
        "allowedChannelPlugins",
        "allowedMcpServers",
        "deniedMcpServers",
        "allowManagedHooksOnly",
        "allowManagedMcpServersOnly",
        "allowManagedPermissionRulesOnly",
        "blockedMarketplaces",
        "channelsEnabled",
        "forceLoginMethod",
        "forceLoginOrgUUID",
        "forceRemoteSettingsRefresh",
        "pluginTrustMessage",
        "strictKnownMarketplaces",
        # Plugin-only (kept here for typo detection; emits CRITICAL when misplaced)
        "lspServers",
        "monitors",
        # v2.22.2 batch — keys present across changelog versions that CPV
        # was flagging as unknown in legitimate settings files.
        "attribution",  # v2.0.62 — replaces includeCoAuthoredBy; commit/PR by-line config
        "effortLevel",  # env-vars.md L87 — effort level setting (CLAUDE_CODE_EFFORT_LEVEL overrides)
        "alwaysThinkingEnabled",  # v2.1.47
        "companyAnnouncements",  # v2.0.32
        "spinnerTipsEnabled",  # v1.0.112
        "spinnerTipsOverride",  # v2.1.45 — tips array + excludeDefault
        "spinnerVerbs",  # v2.1.23 + v2.1.46 — custom spinner verbs
        "plansDirectory",  # v2.1.9
        "refreshInterval",  # v2.1.97 — statusline re-run interval
        "feedbackSurveyRate",  # v2.1.76 — enterprise session-quality survey rate
        "modelOverrides",  # v2.1.73 — Bedrock inference profile ARN mapping
        "showThinkingSummaries",  # v2.1.89
        "showClearContextOnPlanAccept",  # v2.1.81
        "disableDeepLinkRegistration",  # v2.1.83
        "keychainFallback",  # sensitive-credential keychain fallback
        "allowManagedDomainsOnly",  # v2.1.69 — managed domain allowlist
        "language",  # v2.1.0 — e.g. "japanese"
        "disallowAllHooks",  # v2.1.49
        "disableAllHooks",  # v2.1.49 — companion toggle
        "voiceEnabled",  # v2.1.79
        "worktree",  # v2.1.76 — top-level object (sparsePaths, etc.)
    }
)


# =============================================================================
# Secret and absolute-path detection
# =============================================================================

# Known secret formats. Each regex is anchored so a value matches only if the
# entire string looks like a secret. These are intentionally conservative to
# avoid false positives on ordinary strings.
# Per env-vars.md: these env vars ARE secrets by definition. A literal value
# in a settings file's ``env`` block (or MCP server ``env`` map) is an
# unconditional CRITICAL leak — it doesn't matter if the format looks
# "secret-ish" to ``SECRET_VALUE_PATTERNS``. Only ``${VAR}``-expansion
# references are acceptable. Names drawn from env-vars.md L13, L14, L36,
# L45, L64, L110, L112, L188, and Bedrock/Foundry references.
SECRET_ENV_VAR_NAMES: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_FOUNDRY_API_KEY",
        "ANTHROPIC_CUSTOM_HEADERS",  # may carry auth tokens in header form
        "AWS_BEARER_TOKEN_BEDROCK",
        "CLAUDE_CODE_CLIENT_CERT",
        "CLAUDE_CODE_CLIENT_KEY",
        "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
        "MCP_CLIENT_SECRET",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "GL_TOKEN",
        "BITBUCKET_TOKEN",
    }
)


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
# Case-insensitive on all platforms — macOS and Windows filesystems are
# case-insensitive by default, so ``/users/alice/`` and ``/Users/alice/`` are
# the same path and the detection must not be bypassable by changing case.
ABSOLUTE_HOME_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|\s|[\"'=])/Users/[^/\s\"']+/", re.IGNORECASE),
    re.compile(r"(?:^|\s|[\"'=])/home/[^/\s\"']+/", re.IGNORECASE),
    re.compile(r"(?:^|\s|[\"'=])/root/", re.IGNORECASE),
    re.compile(
        r"(?:^|\s|[\"'=])C:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s\"']+[\\/]",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|\s|[\"'=])~[/\\]"),
)

# Redaction pattern — same branches as the detection regexes, but captures
# only the username portion so we can replace it with ``<REDACTED>`` in
# finding messages before they reach the report file.
_HOME_PATH_REDACT_PATTERN: re.Pattern[str] = re.compile(
    r"(?P<prefix>/Users/|/home/|C:[\\/]Users[\\/]|C:[\\/]Documents and Settings[\\/])"
    r"(?P<user>[^/\\\s\"']+)"
    r"(?P<suffix>[/\\])",
    re.IGNORECASE,
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


def redact_home_path(text: str) -> str:
    """Replace the username segment of absolute home paths with ``<REDACTED>``.

    This is the sanitisation layer between raw user input from a settings
    file and the finding messages that the validator writes to its report.
    Matches ``/Users/alice/...``, ``/home/bob/...``,
    ``C:\\Users\\Alice\\...``, and the localised Windows variant.
    Non-matches are returned unchanged.
    """
    if not isinstance(text, str) or not text:
        return text
    return _HOME_PATH_REDACT_PATTERN.sub(
        lambda m: f"{m.group('prefix')}<REDACTED>{m.group('suffix')}",
        text,
    )


# =============================================================================
# Git-tracking classifier
# =============================================================================

Scope = Literal["project", "local", "missing", "no-git"]


def _run_git(
    args: list[str], cwd: Path, *, timeout: int = 10
) -> subprocess.CompletedProcess[str] | None:
    """Run a pinned-binary git command. Returns None on any exec failure.

    This is the only place that calls ``subprocess.run`` for git — every
    caller goes through it so binary pinning and timeout are uniform.
    """
    if GIT_BIN is None:
        return None
    try:
        return subprocess.run(
            [GIT_BIN, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def find_git_root(path: Path, *, boundary: Path | None = None) -> Path | None:
    """Walk up from ``path`` to find the nearest ``.git`` directory.

    Returns the repo root Path or None when no parent has ``.git``.
    The walk is bounded to 50 levels as a sanity limit. When ``boundary``
    is provided, the walk does NOT look above ``boundary`` — used by the
    orchestrators to prevent symlinked parents from exposing unrelated git
    roots far outside the validator's nominal working set
    (aegis MEDIUM-6).
    """
    try:
        current = path.resolve()
    except OSError:
        return None
    boundary_resolved: Path | None = None
    if boundary is not None:
        try:
            boundary_resolved = boundary.resolve()
        except OSError:
            boundary_resolved = None
    for _ in range(50):
        if (current / ".git").exists():
            return current
        if boundary_resolved is not None and current == boundary_resolved:
            return None
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
    result = _run_git(["ls-files", "--error-unmatch", "--", str(rel)], repo_root)
    return result is not None and result.returncode == 0


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
    result = _run_git(["check-ignore", "-q", "--", str(rel)], repo_root)
    return result is not None and result.returncode == 0


def classify_folder_scope(folder: Path, repo_root: Path | None = None) -> Scope:
    """Classify a folder as project / local / missing / no-git.

    Semantics per TRDD-2be75e88 section 3.4:

    - ``missing``: the folder does not exist, OR is a symlink that
      resolves outside ``repo_root`` (safer to treat symlink-escape as
      "missing" so neither scope validator follows it — see aegis
      MEDIUM-1 and llm-ext LOGIC-2)
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
    # Symlink-escape guard: a folder that resolves outside the repo root
    # (e.g., .claude/agents -> /tmp/malicious) must not be walked. We
    # classify it as "missing" so both scope validators skip it silently.
    if resolve_within(folder, repo_root) is None:
        return "missing"
    if is_git_ignored(folder, repo_root):
        return "local"
    rel = _relative_to_root(folder, repo_root)
    if rel is None:
        return "no-git"
    result = _run_git(["ls-files", "--", f"{rel}/"], repo_root)
    if result is None:
        return "local"
    if result.returncode == 0 and result.stdout.strip():
        return "project"
    return "local"


def classify_file_scope(file: Path, repo_root: Path | None = None) -> Scope:
    """Classify a single file as project / local / missing / no-git.

    - ``missing``: the file does not exist, OR resolves outside
      ``repo_root`` via a symlink (treated as missing so neither
      validator touches it)
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
    # Symlink-escape guard — same rationale as classify_folder_scope.
    if resolve_within(file, repo_root) is None:
        return "missing"
    if is_git_tracked(file, repo_root):
        return "project"
    return "local"


# =============================================================================
# Sandbox helpers — containment + batched git queries
# =============================================================================


def resolve_within(path: Path, root: Path) -> Path | None:
    """Resolve ``path`` and verify it stays under ``root``.

    Returns the fully resolved Path on success, or None when:

    - ``path`` does not exist / cannot be stat'd
    - ``path`` resolves outside ``root`` (symlink escape — aegis MEDIUM-1)

    The resolved root is computed once via ``os.path.realpath`` (which
    accepts non-existent paths on Python 3.6+) and compared segment-wise
    via ``relative_to``. If ``relative_to`` raises ``ValueError``, the
    path is not contained.
    """
    try:
        resolved = Path(os.path.realpath(str(path)))
        resolved_root = Path(os.path.realpath(str(root)))
    except OSError:
        return None
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved


def gitignore_covers_path(rel_path: str, repo_root: Path) -> bool:
    """Return True when ``rel_path`` would be gitignored inside ``repo_root``.

    Unlike ``is_git_ignored``, this accepts a plain relative string (no Path
    resolution) and works on hypothetical files that do not exist on disk.
    Used by the orchestrators to verify that common local-scope files
    (``settings.local.json``, ``CLAUDE.local.md``) are covered by the
    project's ``.gitignore`` rules.
    """
    result = _run_git(["check-ignore", "-q", "--", rel_path], repo_root)
    return result is not None and result.returncode == 0


def list_tracked_files_under(folder: Path, repo_root: Path) -> set[Path] | None:
    """Return the set of tracked file Paths under ``folder`` in one git call.

    Returns None when git is unavailable. Returns an empty set when the
    folder has no tracked files. Batches the "is this file tracked?" check
    into a single ``git ls-files -z -- <folder>/`` invocation, replacing N
    per-file subprocess calls with one (aegis LOW-3).

    Paths in the returned set are absolute and resolved (via
    ``repo_root / rel_path``) so callers can compare against file system
    Path objects directly.
    """
    rel = _relative_to_root(folder, repo_root)
    if rel is None:
        return None
    result = _run_git(["ls-files", "-z", "--", f"{rel}/"], repo_root)
    if result is None or result.returncode != 0:
        return None
    tracked: set[Path] = set()
    repo_root_resolved = repo_root.resolve()
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        tracked.add((repo_root_resolved / entry).resolve())
    return tracked


# =============================================================================
# Claude Code plugin-cache layout
# =============================================================================

# ``settings[.local].json.enabledPlugins`` keys have the form
# ``<plugin>@<marketplace>``. Both components use a strict name charset — no
# slashes, no ``..``, no leading/trailing dots — so once the regex matches,
# Path component injection into the cache-dir resolver is contained.
ENABLED_PLUGIN_RE: re.Pattern[str] = re.compile(
    r"^(?P<plugin>[A-Za-z0-9_.\-]+)@(?P<marketplace>[A-Za-z0-9_.\-]+)$"
)


def resolve_plugin_cache_dir(
    plugin_name: str,
    marketplace: str,
    *,
    report: "ValidationReport | None" = None,
    scope_label: str = "enabledPlugins",
) -> Path | None:
    """Find ``~/.claude/plugins/cache/<marketplace>/<plugin>/<highest-version>/``.

    Picks the highest-semver subdirectory. Falls back to lexicographic sort
    when mixed version formats (e.g. ``v2.0-alpha`` next to ``v1.5``) make
    the tuple-compare raise ``TypeError`` — in that case an INFO is emitted
    via ``report`` when supplied, so the operator knows the pick is not
    deterministic.

    The returned path is confined to the ``~/.claude/plugins/cache/`` tree
    via ``resolve_within``; a symlink that escapes the cache base returns
    ``None`` instead. This defends against post-compromise enumeration.
    """
    cache_base = Path.home() / ".claude" / "plugins" / "cache"
    base = cache_base / marketplace / plugin_name
    if not base.is_dir():
        return None
    versions = [d for d in base.iterdir() if d.is_dir()]
    if not versions:
        picked = base
    else:
        def _version_key(p: Path) -> tuple:
            # ``str.lstrip`` treats its argument as a set of characters, so
            # ``vv1.0.0`` would become ``1.0.0`` (both leading v's stripped)
            # — wrong for semver sort. ``removeprefix`` strips exactly one
            # ``v``, matching the canonical ``v<MAJOR>.<MINOR>.<PATCH>``
            # plugin-cache layout.
            parts = p.name.removeprefix("v").split(".")
            return tuple(int(x) if x.isdigit() else x for x in parts)
        try:
            versions.sort(key=_version_key, reverse=True)
        except TypeError:
            if report is not None:
                report.info(
                    f"[{scope_label} {marketplace}/{plugin_name}] version sort "
                    "fell back to lexicographic — mixed version formats "
                    "(e.g. ``v2.0-alpha`` next to ``v1.5``) make the highest-"
                    "pick non-deterministic. Review the cache directory.",
                    scope_label,
                )
            versions.sort(reverse=True)
        picked = versions[0]
    confined = resolve_within(picked, cache_base)
    if confined is None and report is not None:
        report.warning(
            f"[{scope_label} {marketplace}/{plugin_name}] cache dir escapes "
            f"``~/.claude/plugins/cache/`` via symlink — skipping",
            scope_label,
        )
    return confined


# =============================================================================
# Bounded I/O — size-capped reads + safe parsers
# =============================================================================


class OversizedFileError(Exception):
    """Raised by ``safe_read_text`` when a file exceeds its size cap."""


def safe_read_text(path: Path, max_bytes: int) -> str:
    """Read a text file bounded to ``max_bytes``.

    Raises:
        OversizedFileError: file is larger than ``max_bytes``.
        OSError: on stat or read failure.
        UnicodeDecodeError: on non-UTF-8 content.

    The size check happens BEFORE the read so a hostile project cannot
    make the validator OOM by pointing it at a multi-GB file. A leading
    UTF-8 BOM (``\\ufeff``), if present, is stripped transparently using
    ``encoding="utf-8-sig"`` — fixes Windows-edited settings files that
    otherwise fail to parse (llm-ext EDGE-2).
    """
    st = path.stat()
    if st.st_size > max_bytes:
        raise OversizedFileError(
            f"file exceeds {max_bytes} byte cap ({st.st_size} bytes)"
        )
    return path.read_text(encoding="utf-8-sig")


def _count_yaml_alias_markers(text: str) -> int:
    """Count YAML anchor (``&name``) + alias (``*name``) markers in ``text``.

    Used by ``safe_parse_frontmatter`` as a cheap pre-check against YAML
    billion-laughs bombs. Anchors and references add up because a bomb
    works by referencing few anchors many times.
    """
    anchors = len(re.findall(r"(?m)(?:^|\s)&[A-Za-z0-9_-]+", text))
    aliases = len(re.findall(r"(?m)(?:^|\s)\*[A-Za-z0-9_-]+", text))
    return anchors + aliases


def safe_parse_frontmatter(content: str) -> tuple[dict[str, object] | None, str]:
    """Parse YAML frontmatter from a markdown document, bounded.

    Returns ``(frontmatter_dict_or_None, body)``. The dict is None when:

    - The document has no ``---`` fence
    - The frontmatter exceeds ``MAX_FRONTMATTER_BYTES``
    - The frontmatter contains more than ``MAX_YAML_ALIASES`` anchor/alias
      markers (billion-laughs heuristic)
    - ``yaml.safe_load`` raises
    - ``yaml.safe_load`` returns a non-mapping

    The body is always returned (minus the fence) so callers can still
    scan it even when the frontmatter is rejected.
    """
    if not content.startswith("---"):
        return None, content
    lines = content.splitlines()
    if len(lines) < 2:
        return None, content
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, content
    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    if len(fm_text.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        return None, body
    if _count_yaml_alias_markers(fm_text) > MAX_YAML_ALIASES:
        return None, body
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None, body
    if not isinstance(data, dict):
        return None, body
    return data, body


def safe_load_jsonc(path: Path, max_bytes: int) -> object:
    """Load a JSONC file bounded to ``max_bytes``.

    Unlike ``cpv_management_common.load_jsonc``, this helper:

    1. Size-caps the read via ``safe_read_text`` (aegis HIGH-2).
    2. Strips a leading UTF-8 BOM if present (``encoding="utf-8-sig"``) so
       files edited on Windows parse successfully (llm-ext EDGE-2).
    3. Strips JSONC comments and trailing commas via the existing
       ``cpv_management_common`` helpers — parse behaviour is otherwise
       identical.

    Raises:
        OversizedFileError: on oversize input.
        OSError / json.JSONDecodeError / UnicodeDecodeError: on parse
        failure — callers are expected to catch these and convert them to
        sanitised findings (see aegis MEDIUM-4).
    """
    text = safe_read_text(path, max_bytes)
    # Defer the strip-helpers import so cc_scope_rules does not have a
    # hard module-load dependency on cpv_management_common's full graph.
    import json as _json

    from cpv_management_common import strip_jsonc_comments, strip_trailing_commas

    cleaned = strip_trailing_commas(strip_jsonc_comments(text))
    return _json.loads(cleaned)


def read_finding(
    report: ValidationReport | None,
    path: Path,
    max_bytes: int,
    *,
    file_label: str,
) -> str | None:
    """Bounded-read helper that funnels errors through the report.

    Reads ``path`` via ``safe_read_text`` and returns the content on
    success. On failure it adds a sanitised finding to ``report`` (using
    the exception *type* name only, never the exception message, to
    avoid leaking file contents per aegis MEDIUM-4) and returns None.
    When ``report`` is None, the function silently returns None — used
    by pure helpers that cannot emit findings directly.
    """
    try:
        return safe_read_text(path, max_bytes)
    except OversizedFileError:
        if report is not None:
            report.major(
                f"{file_label}: exceeds {max_bytes} byte size cap — skipping",
                file_label,
            )
        return None
    except (OSError, UnicodeDecodeError) as exc:
        if report is not None:
            report.critical(
                f"{file_label}: read failed ({type(exc).__name__})",
                file_label,
            )
        return None


# =============================================================================
# CLAUDE.md / CLAUDE.local.md ``@path`` import validator (memory.md L95-107)
# =============================================================================

# An import token is `@` followed by a non-empty, non-whitespace path. We anchor
# the `@` to the start of a word (start-of-line or preceded by whitespace) so
# that "email@domain.com" in prose is NOT interpreted as an import marker. The
# path itself is a run of non-whitespace characters terminated by whitespace or
# end-of-line. Trailing punctuation (.,;:!?) is stripped in a follow-up pass so
# tokens like "see @notes.md." don't include the terminal period as part of the
# path.
_AT_IMPORT_RE: re.Pattern[str] = re.compile(r"(?:^|(?<=\s))@(\S+)")
_AT_IMPORT_TRAILING_PUNCT: str = ".,;:!?)"

# Fenced code blocks (``` ... ```) and inline code (`...`) are skipped so that
# instructions/examples inside Markdown code DO NOT trigger imports — this
# matches Claude Code's actual loader behaviour (memory.md L98-101).
_FENCED_CODE_BLOCK_RE: re.Pattern[str] = re.compile(
    r"^\s*```.*?^\s*```",
    re.DOTALL | re.MULTILINE,
)
_INLINE_CODE_RE: re.Pattern[str] = re.compile(r"`[^`\n]*`")


def _strip_code_spans(text: str) -> str:
    """Remove fenced code blocks and inline code spans from markdown ``text``.

    Returns the same text with each match replaced by an equal-length run of
    spaces so that line/column positions of the remaining content are
    preserved. Callers that scan for ``@path`` import tokens use the stripped
    version to avoid false positives inside code examples.
    """
    def _blank(match: re.Match[str]) -> str:
        span = match.group(0)
        # Preserve newlines so line numbers remain stable for callers that
        # would want to report line positions (none currently, but cheap).
        return "".join("\n" if c == "\n" else " " for c in span)

    stripped = _FENCED_CODE_BLOCK_RE.sub(_blank, text)
    stripped = _INLINE_CODE_RE.sub(_blank, stripped)
    return stripped


def extract_at_path_imports(content: str) -> list[str]:
    """Return every ``@path`` import token found in ``content``.

    Per memory.md L95-107: ``@path/to/file.md`` in CLAUDE.md (or a nested
    imported file) triggers a recursive load. Tokens inside fenced code
    blocks or inline code spans are NOT imports and are excluded.

    The returned list preserves source order and MAY contain duplicates
    (the caller deduplicates when needed for cycle detection).
    """
    stripped = _strip_code_spans(content)
    out: list[str] = []
    for m in _AT_IMPORT_RE.finditer(stripped):
        raw = m.group(1)
        # Strip trailing punctuation so "see @notes.md." does not import
        # the literal path "notes.md." — memory.md's loader treats trailing
        # sentence punctuation as prose, not part of the path.
        path = raw.rstrip(_AT_IMPORT_TRAILING_PUNCT)
        if path:
            out.append(path)
    return out


def validate_claude_md_imports(
    source: Path,
    repo_root: Path,
    report: ValidationReport,
    rel_label: str,
    *,
    max_bytes: int,
) -> None:
    """Recursively validate ``@path`` imports starting from ``source``.

    Per memory.md L95-107, CLAUDE.md (and every file it imports) may contain
    ``@path/to/file.md`` import tokens that Claude Code resolves relative to
    the containing file. This helper walks the import graph from ``source``
    and emits findings for:

    - CRITICAL: an absolute path (leading ``/``) that resolves outside
      ``repo_root`` — e.g. an import that points at a host-level system
      file would leak its contents into Claude's context.
    - MAJOR: a path whose ``..`` segments escape ``repo_root``.
    - MAJOR: a path that does not exist on disk (dead import).
    - MAJOR: recursion depth exceeds ``MAX_CLAUDE_MD_IMPORT_DEPTH`` (5).
    - MAJOR: a circular import (A imports B imports A).

    An INFO line is emitted at the end summarising ``N imports from M
    files``. The walk is size-capped per read (via ``safe_read_text``) so
    a hostile imported file cannot OOM the validator.

    Args:
        source: path to the starting file (CLAUDE.md / CLAUDE.local.md /
                an already-imported file).
        repo_root: root below which imports are considered "inside" the
                   project; any resolved target outside it is flagged.
        report: findings sink.
        rel_label: display label for the starting file (used as the
                   ``file`` field on every finding emitted from this
                   walk).
        max_bytes: per-file size cap applied to every imported file read.
    """
    visited: set[Path] = set()
    import_count = 0
    file_count = 0

    def _walk(current: Path, chain: tuple[Path, ...], depth: int) -> None:
        nonlocal import_count, file_count
        try:
            content = safe_read_text(current, max_bytes)
        except (OversizedFileError, OSError, UnicodeDecodeError):
            # Read errors for the starting file are reported by the caller;
            # for imported files the caller already emitted a MAJOR on the
            # "does not exist" check or a follow-up.
            return
        file_count += 1
        for raw_path in extract_at_path_imports(content):
            import_count += 1
            # CRITICAL: absolute path outside repo root is a security leak.
            if raw_path.startswith("/"):
                try:
                    abs_target = Path(raw_path).resolve()
                except (OSError, ValueError):
                    abs_target = Path(raw_path)
                try:
                    abs_target.relative_to(repo_root.resolve())
                    # Absolute path that DOES resolve inside the repo is
                    # unusual (authors typically use relative paths) but is
                    # not a security leak — skip without a finding here.
                    # Fall through to the general resolve-and-check logic
                    # so "file does not exist" / depth checks still apply.
                    target = abs_target
                except ValueError:
                    report.critical(
                        f"{rel_label}: import '@{raw_path}' points outside the "
                        "project root (absolute path) — this would leak host "
                        "files into Claude's context.",
                        rel_label,
                    )
                    continue
            else:
                # Relative path — resolve from the CONTAINING file's dir.
                candidate = (current.parent / raw_path).resolve()
                try:
                    candidate.relative_to(repo_root.resolve())
                except ValueError:
                    report.major(
                        f"{rel_label}: import '@{raw_path}' uses '..' "
                        "segments that escape the project root — the target "
                        "lives outside the repo and will not load.",
                        rel_label,
                    )
                    continue
                target = candidate

            if not target.exists():
                report.major(
                    f"{rel_label}: import '@{raw_path}' points to a file that "
                    "does not exist — dead imports are silently ignored by "
                    "Claude Code.",
                    rel_label,
                )
                continue

            if target in chain:
                report.major(
                    f"{rel_label}: circular import detected — '@{raw_path}' "
                    "is already in the current import chain and will be "
                    "skipped by Claude Code's loader.",
                    rel_label,
                )
                continue

            if depth + 1 > MAX_CLAUDE_MD_IMPORT_DEPTH:
                report.major(
                    f"{rel_label}: import '@{raw_path}' exceeds the maximum "
                    f"recursion depth of {MAX_CLAUDE_MD_IMPORT_DEPTH} — "
                    "deeper chains are silently truncated by Claude Code.",
                    rel_label,
                )
                continue

            if target in visited:
                continue
            visited.add(target)
            _walk(target, chain + (target,), depth + 1)

    _walk(source, (source.resolve(),), 0)

    if import_count > 0:
        report.info(
            f"{rel_label}: {import_count} import(s) resolved from {file_count} "
            "file(s) (including the starting file).",
            rel_label,
        )
