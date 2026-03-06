#!/usr/bin/env python3
"""
claude-plugin-install — Install, validate, and manage Claude Code plugins.

Wraps plugins into local marketplaces and registers them using the official
extraKnownMarketplaces mechanism in settings.local.json. Includes deep
validation of hooks schemas, frontmatter, scripts, and MCP configs.

Cross-platform: works on macOS, Linux, and Windows.
Requires: Python 3.8+, no external dependencies.
"""

import argparse
import datetime
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

IS_WINDOWS = platform.system() == "Windows"
PYTHON_VERSION = sys.version_info
TOOL_VERSION = "1.2.0"


# ── Paths ─────────────────────────────────────────────────


def _get_claude_dir() -> Path:
    """Get the Claude config directory, cross-platform.

    Claude Code uses ~/.claude on all platforms, including Windows.
    On Windows this resolves to %USERPROFILE%\\.claude (e.g. C:\\Users\\you\\.claude).
    """
    return Path.home() / ".claude"


CLAUDE_DIR = _get_claude_dir()
PLUGINS_DIR = CLAUDE_DIR / "plugins"
MARKETPLACES_DIR = PLUGINS_DIR / "marketplaces"
CACHE_DIR = PLUGINS_DIR / "cache"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"
SETTINGS_LOCAL_FILE = CLAUDE_DIR / "settings.local.json"
INSTALLED_FILE = PLUGINS_DIR / "installed_plugins.json"

# We write marketplace registration to settings.local.json (user-level,
# not committed to repos) so we never interfere with a project's
# settings.json or a hand-crafted global settings.json.
SETTINGS_TARGET = SETTINGS_LOCAL_FILE


# ── Colors ────────────────────────────────────────────────


def _enable_ansi_windows():
    """Enable ANSI escape codes on Windows 10+ terminals."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def supports_color():
    if IS_WINDOWS:
        _enable_ansi_windows()
        if os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM"):
            return True
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


C = supports_color()
RED = "\033[0;31m" if C else ""
GREEN = "\033[0;32m" if C else ""
YELLOW = "\033[1;33m" if C else ""
CYAN = "\033[0;36m" if C else ""
BOLD = "\033[1m" if C else ""
NC = "\033[0m" if C else ""


def ok(msg: str):
    print(f"{GREEN}✔{NC} {msg}")


def info(msg: str):
    print(f"{CYAN}ℹ{NC} {msg}")


def warn(msg: str):
    print(f"{YELLOW}⚠{NC} {msg}")


def err(msg: str):
    print(f"{RED}✖{NC} {msg}")


# ── JSONC parser ──────────────────────────────────────────


def strip_jsonc_comments(text: str) -> str:
    """Strip // and /* */ comments from JSONC text, respecting strings."""
    result = []
    i = 0
    in_string = False
    n = len(text)

    while i < n:
        ch = text[i]

        if in_string:
            result.append(ch)
            if ch == "\\":
                # Consume the escaped character too, so \" doesn't toggle in_string
                i += 1
                if i < n:
                    result.append(text[i])
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue

        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue

        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            if i + 1 < n:
                i += 2  # skip */
            else:
                i = n  # unterminated block comment — skip to end
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ] (common JSONC pattern).
    Respects JSON string boundaries to avoid corrupting string values."""
    result = []
    in_string = False
    i = 0
    while i < len(text):
        c = text[i]
        if in_string:
            result.append(c)
            if c == "\\" and i + 1 < len(text):
                i += 1
                result.append(text[i])
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
            result.append(c)
        elif c == ",":
            # Look ahead: if only whitespace then } or ], skip the comma
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
            result.append(c)
        else:
            result.append(c)
        i += 1
    return "".join(result)


def load_jsonc(path: Path) -> dict:
    """Load a JSONC file (JSON with comments and trailing commas)."""
    text = path.read_text(encoding="utf-8")
    cleaned = strip_trailing_commas(strip_jsonc_comments(text))
    result: dict = json.loads(cleaned)
    return result


# ── Safe JSON file operations ─────────────────────────────


def backup_file(path: Path):
    """Create a timestamped backup of a file before modifying it."""
    if not path.exists():
        return
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.parent / f"{path.name}.{ts}.bak"
    shutil.copy2(path, backup)
    # Restrict backup permissions to owner-only on non-Windows
    if not IS_WINDOWS:
        try:
            backup.chmod(0o600)
        except OSError:
            pass

    # Keep only the 5 most recent backups
    pattern = f"{path.name}.*.bak"
    backups = sorted(path.parent.glob(pattern), key=lambda p: p.stat().st_mtime)
    for old in backups[:-5]:
        old.unlink(missing_ok=True)


def load_json_safe(path: Path) -> dict:
    """Load a JSON/JSONC file safely, returning {} if missing or corrupt."""
    if not path.exists():
        return {}
    try:
        return load_jsonc(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        warn(f"Could not parse {path}: {e}")
        warn("The file may be corrupt. A backup will be created before any changes.")
        return {}


def save_json_safe(path: Path, data: dict, dry_run: bool = False):
    """Atomically write JSON with backup. Cross-platform."""
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_file(path)

    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        # os.replace() (called by Path.replace()) is atomic on all platforms including Windows
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ── Archive extraction ────────────────────────────────────


def extract_archive(archive_path: str, dest: Path):
    """Extract .tar.gz/.tgz/.zip/.tar.bz2/.tar.xz to dest directory."""
    archive = Path(archive_path)
    if not archive.exists():
        err(f"File not found: {archive}")
        sys.exit(1)

    name = archive.name.lower()

    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        _extract_tar(archive, dest, "r:gz")
    elif name.endswith(".tar.bz2"):
        _extract_tar(archive, dest, "r:bz2")
    elif name.endswith(".tar.xz"):
        _extract_tar(archive, dest, "r:xz")
    elif name.endswith(".tar"):
        _extract_tar(archive, dest, "r:")
    elif name.endswith(".zip"):
        _extract_zip(archive, dest)
    else:
        err(f"Unsupported archive format: {archive.suffix}")
        err("Supported: .tar.gz, .tgz, .tar.bz2, .tar.xz, .tar, .zip")
        sys.exit(1)


def _extract_zip(archive: Path, dest: Path):
    """Extract a zip archive with path traversal prevention.
    Extracts members individually after validation to prevent TOCTOU issues."""
    with zipfile.ZipFile(archive, "r") as zf:
        # Append os.sep so /tmp/abc doesn't match /tmp/abcdef (path traversal bypass)
        dest_resolved = str(dest.resolve()) + os.sep
        for info in zf.infolist():
            member_path = os.path.normpath(info.filename)
            if member_path.startswith("..") or os.path.isabs(member_path):
                err(f"Refusing to extract path-traversal entry: {info.filename}")
                sys.exit(1)
            # Check that the resolved path stays within dest
            target = (dest / member_path).resolve()
            if not (str(target) + os.sep).startswith(dest_resolved):
                err(f"Refusing to extract path-traversal entry: {info.filename}")
                sys.exit(1)
            # Extract each member individually right after validation
            zf.extract(info, dest)


def _extract_tar(archive: Path, dest: Path, mode: str):
    """Extract a tar archive with security filtering."""
    with tarfile.open(name=str(archive), mode=mode) as tf:  # type: ignore[call-overload]
        if PYTHON_VERSION >= (3, 12):
            tf.extractall(dest, filter="data")
        else:
            # Manual path-traversal and symlink prevention for older Python
            # Append os.sep so /tmp/abc doesn't match /tmp/abcdef (path traversal bypass)
            dest_resolved = str(dest.resolve()) + os.sep
            for member in tf.getmembers():
                member_path = os.path.normpath(member.name)
                if member_path.startswith("..") or os.path.isabs(member_path):
                    err(f"Refusing to extract path-traversal entry: {member.name}")
                    sys.exit(1)
                # Block symlinks pointing outside dest
                if member.issym() or member.islnk():
                    link_target = os.path.normpath(os.path.join(os.path.dirname(member.name), member.linkname))
                    if link_target.startswith("..") or os.path.isabs(link_target):
                        err(f"Refusing to extract symlink escaping archive: {member.name} -> {member.linkname}")
                        sys.exit(1)
                # Verify resolved path stays within dest
                target = (dest / member_path).resolve()
                if not (str(target) + os.sep).startswith(dest_resolved):
                    err(f"Refusing to extract path-traversal entry: {member.name}")
                    sys.exit(1)
                # Extract each member individually right after validation
                tf.extract(member, dest)


# ── Gitignore handling ────────────────────────────────────


def _parse_gitignore_patterns(gitignore_path: Path) -> List[str]:
    """Parse a .gitignore file and return a list of patterns."""
    if not gitignore_path.exists():
        return []
    patterns = []
    for line in gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _gitignore_pattern_to_re(pattern: str) -> Tuple[Optional[re.Pattern], bool]:
    """Convert a single gitignore pattern to a compiled regex.
    Returns (regex, is_negation). Returns (None, _) for unparseable patterns."""
    negation = False
    if pattern.startswith("!"):
        negation = True
        pattern = pattern[1:]

    # Remove trailing spaces (unless escaped)
    pattern = pattern.rstrip()
    if not pattern:
        return None, False

    # If pattern contains a slash (not trailing), it's relative to base
    anchored = "/" in pattern.rstrip("/")

    # Trailing slash means directory only — we handle by appending to match
    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern.rstrip("/")

    # Convert gitignore glob to regex
    parts = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # ** pattern
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    parts.append("(?:.*/)?")  # **/  matches zero or more dirs
                    i += 3
                    continue
                else:
                    parts.append(".*")  # trailing ** matches everything
                    i += 2
                    continue
            else:
                parts.append("[^/]*")  # single * matches within one dir
        elif c == "?":
            parts.append("[^/]")
        elif c == "[":
            # Character class — pass through until ]
            j = i + 1
            if j < len(pattern) and pattern[j] == "!":
                j += 1
            if j < len(pattern) and pattern[j] == "]":
                j += 1
            while j < len(pattern) and pattern[j] != "]":
                j += 1
            parts.append(pattern[i : j + 1].replace("!", "^", 1) if "!" in pattern[i : j + 1] else pattern[i : j + 1])
            i = j + 1
            continue
        else:
            parts.append(re.escape(c))
        i += 1

    regex_str = "".join(parts)

    if anchored:
        # Anchored pattern: must match from the root
        regex_str = "^" + regex_str
    else:
        # Unanchored: can match at any directory level
        regex_str = "(?:^|/)" + regex_str

    # Both dir-only and general patterns match the entry and everything inside it.
    # For dir-only patterns (e.g. "node_modules/"), the trailing "/" was stripped above
    # and the pattern matches the directory itself plus all its contents.
    regex_str += "(?:/.*)?$"

    try:
        return re.compile(regex_str), negation
    except re.error:
        return None, False


def _is_git_metadata(rel_str: str) -> bool:
    """Check if a relative path is a git metadata file/directory."""
    # Normalize to forward slashes for consistent matching
    norm = rel_str.replace("\\", "/")
    if norm == ".git" or norm.startswith(".git/"):
        return True
    # Also exclude .gitignore and .gitattributes from installed plugins
    basename = norm.rsplit("/", 1)[-1] if "/" in norm else norm
    if basename in (".gitignore", ".gitattributes", ".gitmodules", ".gitkeep"):
        return True
    return False


def _build_gitignore_matcher(plugin_dir: Path) -> Callable[[Path], bool]:
    """Build a function that returns True if a path should be ignored.
    Uses `git check-ignore` if inside a git repo, otherwise parses .gitignore manually.
    Always ignores .git/ directory and git metadata files regardless."""
    gitignore_path = plugin_dir / ".gitignore"

    # Try git check-ignore first (most accurate, handles nested gitignores)
    has_git = (plugin_dir / ".git").exists()
    if has_git:
        try:
            # Verify git is available
            subprocess.run(["git", "--version"], capture_output=True, check=True)
            use_git = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            use_git = False
    else:
        use_git = False

    if use_git:
        # Pre-compute ignored files via batch git check-ignore for performance
        ignored_paths: set = set()
        try:
            # Collect all relative paths
            all_paths = []
            for item in plugin_dir.rglob("*"):
                rel = str(item.relative_to(plugin_dir))
                if not _is_git_metadata(rel):
                    all_paths.append(rel)
            if all_paths:
                # Batch check: pass all paths via stdin
                result = subprocess.run(
                    ["git", "check-ignore", "--stdin"],
                    cwd=str(plugin_dir),
                    input="\n".join(all_paths),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                # Each line of stdout is a path that is ignored
                for line in result.stdout.splitlines():
                    stripped = line.strip()
                    if stripped:
                        ignored_paths.add(stripped)
        except (subprocess.TimeoutExpired, OSError):
            pass  # Fall through — ignored_paths stays empty, nothing extra ignored

        def _is_ignored_git(path: Path) -> bool:
            rel = path.relative_to(plugin_dir)
            rel_str = str(rel)
            if _is_git_metadata(rel_str):
                return True
            # Check against pre-computed set
            return rel_str in ignored_paths

        return _is_ignored_git

    # Fallback: parse .gitignore manually
    if not gitignore_path.exists() and not has_git:
        # No .gitignore and no .git — only filter git metadata
        def _is_ignored_minimal(path: Path) -> bool:
            rel_str = str(path.relative_to(plugin_dir))
            return _is_git_metadata(rel_str)

        return _is_ignored_minimal

    patterns = _parse_gitignore_patterns(gitignore_path)
    compiled = []
    for pat in patterns:
        regex, neg = _gitignore_pattern_to_re(pat)
        if regex:
            compiled.append((regex, neg))

    def _is_ignored_manual(path: Path) -> bool:
        rel = path.relative_to(plugin_dir)
        rel_str = str(rel).replace("\\", "/")
        if _is_git_metadata(rel_str):
            return True
        # Add trailing slash for directories so dir-only patterns work
        if path.is_dir():
            check_str = rel_str + "/"
        else:
            check_str = rel_str
        ignored = False
        for regex, is_negation in compiled:
            if regex.search(check_str) or regex.search("/" + check_str):
                ignored = not is_negation
        return ignored

    return _is_ignored_manual


def _copy_plugin_from_dir(source_dir: Path, dest: Path, ignore_fn: Optional[Callable[[Path], bool]] = None):
    """Copy a plugin directory to dest, skipping files matched by ignore_fn.
    The .git directory and git metadata files are always excluded.
    Empty directories (after filtering) are not created."""
    copied_any = False
    for item in sorted(source_dir.iterdir()):
        # Always skip .git directory and git metadata files
        if item.name in (".git", ".gitignore", ".gitattributes", ".gitmodules", ".gitkeep"):
            continue
        if ignore_fn and ignore_fn(item):
            continue
        # Skip symlinks to prevent symlink attacks (archives already filter them)
        if item.is_symlink():
            continue
        dest_item = dest / item.name
        if item.is_dir():
            _copy_plugin_from_dir(item, dest_item, ignore_fn)
            # Only count as copied if the subdirectory was actually created
            if dest_item.exists():
                if not copied_any:
                    dest.mkdir(parents=True, exist_ok=True)
                copied_any = True
        elif item.is_file():
            if not copied_any:
                dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_item)
            copied_any = True


def find_plugin_root(search_dir: Path) -> Optional[Path]:
    """Find the plugin root directory (parent of .claude-plugin/plugin.json).
    Skips directories that also contain marketplace.json."""
    for pj in search_dir.rglob(".claude-plugin/plugin.json"):
        if (pj.parent / "marketplace.json").exists():
            continue
        return pj.parent.parent
    return None


# ── Plugin metadata ───────────────────────────────────────


def read_plugin_meta(plugin_root: Path) -> dict:
    """Read plugin.json and return metadata with defaults."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        meta = json.loads(pj.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    return {
        "name": meta.get("name") or plugin_root.name,
        "version": meta.get("version", "1.0.0"),
        "description": meta.get("description", ""),
    }


def _detect_plugin_origin_refs(plugin_root: Path) -> List[str]:
    """Detect marketplace, repository, or GitHub references inside the plugin.

    Returns a list of human-readable strings describing each reference found,
    e.g. 'plugin.json "marketplace": "official-plugins"'
    """
    refs: List[str] = []

    # Check plugin.json for origin-related fields
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if pj.exists():
        try:
            meta = json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

        # Fields that reference a marketplace or origin
        for field in ("marketplace", "registry", "source", "origin"):
            val = meta.get(field)
            if isinstance(val, str) and val.strip():
                refs.append(f'plugin.json "{field}": "{val}"')
            elif isinstance(val, dict):
                refs.append(f'plugin.json "{field}": {json.dumps(val)}')

        # Fields that reference a repository or homepage
        for field in ("repository", "homepage", "url", "bugs"):
            val = meta.get(field)
            if isinstance(val, str) and val.strip():
                refs.append(f'plugin.json "{field}": "{val}"')
            elif isinstance(val, dict):
                # e.g. "repository": {"type": "git", "url": "https://..."}
                url = val.get("url", "")
                if url:
                    refs.append(f'plugin.json "{field}.url": "{url}"')

        # Check author field for URLs
        author = meta.get("author")
        if isinstance(author, dict):
            url = author.get("url", "")
            if url:
                refs.append(f'plugin.json "author.url": "{url}"')
        elif isinstance(author, str) and ("github.com" in author or "http" in author):
            refs.append(f'plugin.json "author": "{author}"')

    # Check marketplace.json if bundled inside the plugin
    bundled_mj = plugin_root / ".claude-plugin" / "marketplace.json"
    if bundled_mj.exists():
        try:
            mj = json.loads(bundled_mj.read_text(encoding="utf-8"))
            mp_name = mj.get("name", "")
            if mp_name:
                refs.append(f'marketplace.json "name": "{mp_name}"')
            mp_url = mj.get("url", "") or mj.get("repository", "")
            if mp_url:
                refs.append(f'marketplace.json "url": "{mp_url}"')
        except Exception:
            pass

    return refs


# ── File permissions (cross-platform) ────────────────────


def _has_shebang(path: Path) -> bool:
    """Check if a file starts with a shebang line."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except Exception:
        return False


def _is_executable(path: Path) -> bool:
    """Check if a file is executable, cross-platform."""
    if IS_WINDOWS:
        return _has_shebang(path)
    return os.access(path, os.X_OK)


def _make_executable(path: Path):
    """Make a file executable. No-op on Windows."""
    if IS_WINDOWS:
        return
    try:
        current = path.stat().st_mode
        path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


SCRIPT_EXTENSIONS = {".py", ".sh", ".js", ".ts", ".rb", ".pl", ".mjs", ".cjs"}
WINDOWS_SCRIPT_EXTENSIONS = {".cmd", ".bat", ".ps1"}
ALL_SCRIPT_EXTENSIONS = SCRIPT_EXTENSIONS | WINDOWS_SCRIPT_EXTENSIONS


def _find_all_scripts(plugin_dir: Path) -> List[Path]:
    """Find all script files in a plugin directory."""
    scripts = []
    for f in plugin_dir.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix in ALL_SCRIPT_EXTENSIONS:
            scripts.append(f)
        elif f.parent.name == "scripts" and "." not in f.name:
            scripts.append(f)
    return scripts


def _fix_permissions(plugin_dir: Path):
    """Make all script files executable (Unix) or verify shebangs (Windows)."""
    for f in _find_all_scripts(plugin_dir):
        _make_executable(f)


def _portable_path(p: Path) -> str:
    """Convert a path to forward slashes for JSON storage.
    Claude Code (Node.js) expects forward slashes even on Windows."""
    return str(p).replace("\\", "/")


# ── Plugin validation ─────────────────────────────────────

# Known hook events (case-sensitive)
VALID_HOOK_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "UserPromptSubmit",
    "Notification",
    "Stop",
    "SubagentStop",
    "SubagentStart",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "Setup",
    "ConfigChange",
    "TeammateIdle",
    "TaskCompleted",
    "WorktreeCreate",
    "WorktreeRemove",
    "InstructionsLoaded",
}

TOOL_MATCHER_EVENTS = {"PreToolUse", "PermissionRequest", "PostToolUse", "PostToolUseFailure"}
KNOWN_TOOL_MATCHERS = {
    "Task",
    "Bash",
    "Glob",
    "Grep",
    "Read",
    "Edit",
    "MultiEdit",
    "Write",
    "WebFetch",
    "WebSearch",
    "Notebook",
    "NotebookEdit",
    "Skill",
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "EnterWorktree",
    "TaskCreate",
    "TaskUpdate",
    "TaskList",
    "TaskGet",
    "TaskStop",
    "ToolSearch",
    "TodoRead",
    "TodoWrite",
    "LSP",
    "Agent",
}
NOTIFICATION_MATCHERS = {"permission_prompt", "idle_prompt", "auth_success", "elicitation_dialog"}
SESSION_START_MATCHERS = {"startup", "resume", "clear", "compact"}
PRECOMPACT_MATCHERS = {"manual", "auto"}
NO_MATCHER_EVENTS = {
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "SubagentStart",
    "SessionEnd",
    "InstructionsLoaded",
    "WorktreeCreate",
    "WorktreeRemove",
    "TeammateIdle",
    "TaskCompleted",
}
VALID_HOOK_TYPES = {"command", "http", "prompt", "agent"}
COMPONENT_PATH_FIELDS = {
    "commands": ("string", "array"),
    "agents": ("string", "array"),
    "hooks": ("string", "object"),
    "mcpServers": ("string", "object"),
}


def _check_type(value, expected_types):
    type_map = {"string": str, "array": list, "object": dict, "boolean": bool, "number": (int, float)}
    for t in expected_types:
        if isinstance(value, type_map.get(t, type(None))):
            return None
    return f"expected {' or '.join(expected_types)}, got {type(value).__name__}"


def _fuzzy_match_event(wrong_name: str) -> Optional[str]:
    lower = wrong_name.lower()
    for valid in VALID_HOOK_EVENTS:
        if valid.lower() == lower:
            return valid
    for valid in VALID_HOOK_EVENTS:
        if len(wrong_name) == len(valid):
            diff = sum(1 for a, b in zip(wrong_name, valid) if a != b)
            if diff <= 2:
                return valid
        if lower in valid.lower() or valid.lower() in lower:
            return valid
    return None


def _validate_matcher(matcher: str, event_name: str, path: str) -> list:
    warnings: list[str] = []
    if not matcher or matcher == "*":
        return warnings

    if event_name in NO_MATCHER_EVENTS:
        warnings.append(
            f"{path}: '{event_name}' does not use matchers — the matcher '{matcher}' will be ignored. Remove it or omit the matcher field."
        )
        return warnings

    if event_name in TOOL_MATCHER_EVENTS:
        for part in [p.strip() for p in matcher.split("|")]:
            clean = re.sub(r"[.*+?^$()\\]", "", part)
            if clean and clean not in KNOWN_TOOL_MATCHERS and not part.startswith("mcp__"):
                close = [t for t in KNOWN_TOOL_MATCHERS if t.lower() == clean.lower()]
                if close:
                    warnings.append(
                        f"{path}: matcher '{part}' — did you mean '{close[0]}'? (matchers are case-sensitive)"
                    )
                else:
                    warnings.append(
                        f"{path}: matcher '{part}' doesn't match any known tool. Known tools: {', '.join(sorted(KNOWN_TOOL_MATCHERS))}. MCP tools use pattern: mcp__<server>__<tool>"
                    )
    elif event_name == "Notification":
        for part in [p.strip() for p in matcher.split("|")]:
            clean = re.sub(r"[.*+?^$()\\]", "", part)
            if clean and clean not in NOTIFICATION_MATCHERS:
                warnings.append(
                    f"{path}: Notification matcher '{part}' — known types: {', '.join(sorted(NOTIFICATION_MATCHERS))}"
                )
    elif event_name == "SessionStart":
        for part in [p.strip() for p in matcher.split("|")]:
            clean = re.sub(r"[.*+?^$()\\]", "", part)
            if clean and clean not in SESSION_START_MATCHERS:
                warnings.append(
                    f"{path}: SessionStart matcher '{part}' — known values: {', '.join(sorted(SESSION_START_MATCHERS))}"
                )
    elif event_name == "PreCompact":
        for part in [p.strip() for p in matcher.split("|")]:
            clean = re.sub(r"[.*+?^$()\\]", "", part)
            if clean and clean not in PRECOMPACT_MATCHERS:
                warnings.append(
                    f"{path}: PreCompact matcher '{part}' — known values: {', '.join(sorted(PRECOMPACT_MATCHERS))}"
                )

    return warnings


def _validate_bash_command(cmd: str, path: str, plugin_root: Optional[Path] = None):
    """Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    stripped = cmd.strip()
    if not stripped:
        return errors, warnings

    expanded = re.sub(r"\$\{[^}]+\}", "/expanded", stripped)
    expanded = re.sub(r"\$\w+", "/expanded", expanded)
    expanded_tokens = expanded.split()
    first_token = expanded_tokens[0] if expanded_tokens else ""

    # ── Script without interpreter ──
    script_interpreters = {
        ".py": ("python3", "python", "uv run", "uvx"),
        ".js": ("node", "npx", "pnpm dlx", "bunx", "bun"),
        ".ts": ("ts-node", "tsx", "npx ts-node", "npx tsx", "bunx", "bun", "pnpm dlx tsx"),
        ".sh": ("bash", "sh", "zsh"),
        ".rb": ("ruby",),
        ".pl": ("perl",),
    }

    for ext, interpreters in script_interpreters.items():
        if first_token.endswith(ext):
            has_shebang = False
            if plugin_root:
                resolved = cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
                resolved = resolved.replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))
                sp = Path(resolved.split()[0])
                if sp.exists():
                    has_shebang = _has_shebang(sp)

            if not has_shebang:
                note = (
                    " — on Windows, scripts always need an explicit interpreter"
                    if IS_WINDOWS
                    else f" or add a shebang line (e.g. #!/usr/bin/env {interpreters[0]})"
                )
                warnings.append(
                    f"{path}: command runs '{first_token}' without an interpreter. Add one of: {' / '.join(interpreters)} (e.g. '{interpreters[0]} {stripped}'){note}"
                )
            break

    # ── Tilde expansion ──
    if stripped.startswith("~/"):
        warnings.append(
            f"{path}: command starts with '~/' — tilde expansion may not work in hook commands. Use $HOME/ instead."
        )

    # ── cd without follow-up ──
    if stripped.startswith("cd ") and "&&" not in stripped and ";" not in stripped:
        warnings.append(
            f"{path}: 'cd' alone has no effect — each hook runs in a fresh shell. Combine: 'cd /dir && your-command'"
        )

    # ── Windows backslash paths ──
    if IS_WINDOWS and "\\" in cmd and "${CLAUDE_PLUGIN_ROOT}" not in cmd:
        warnings.append(f"{path}: use forward slashes for cross-platform compatibility")

    # ── Verify referenced script exists ──
    if plugin_root and ("${CLAUDE_PLUGIN_ROOT}" in cmd or "$CLAUDE_PLUGIN_ROOT" in cmd):
        resolved = cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
        resolved = resolved.replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))
        tokens = resolved.split()
        if not tokens:
            return errors, warnings

        interpreters_set = {
            "python3",
            "python",
            "node",
            "bash",
            "sh",
            "zsh",
            "ruby",
            "perl",
            "ts-node",
            "tsx",
            "npx",
            "bunx",
            "bun",
            "pnpm",
            "uvx",
            "uv",
            "deno",
        }
        script_path = None
        if tokens[0] in interpreters_set or Path(tokens[0]).name in interpreters_set:
            for t in tokens[1:]:
                if not t.startswith("-"):
                    script_path = t
                    break
        else:
            script_path = tokens[0]

        if script_path and script_path.startswith(str(plugin_root)):
            sp = Path(script_path)
            if not sp.exists():
                prefix = str(plugin_root)
                # Strip plugin_root prefix for cleaner error message
                for sep in ("/", os.sep):
                    full_prefix = prefix + sep
                    if script_path.startswith(full_prefix):
                        rel = script_path[len(full_prefix) :]
                        break
                else:
                    rel = script_path
                errors.append(f"Hook command references '{rel}' but this file does not exist in the plugin")

    return errors, warnings


def _validate_hooks_structure(hooks_data: dict, source_file: str, plugin_root: Optional[Path] = None):
    errors = []
    warnings = []

    if "hooks" in hooks_data and isinstance(hooks_data.get("hooks"), dict):
        hooks_obj = hooks_data["hooks"]
    else:
        hooks_obj = hooks_data

    for event_name, event_value in hooks_obj.items():
        # Skip known metadata keys and $-prefixed keys like $schema
        if event_name in ("hooks", "description", "version", "metadata") or event_name.startswith("$"):
            continue

        if event_name not in VALID_HOOK_EVENTS:
            suggestion = _fuzzy_match_event(event_name)
            if suggestion:
                errors.append(
                    f"{source_file}: unknown hook event '{event_name}' — did you mean '{suggestion}'? (event names are case-sensitive)"
                )
            else:
                errors.append(
                    f"{source_file}: unknown hook event '{event_name}'. Valid events: {', '.join(sorted(VALID_HOOK_EVENTS))}"
                )

        if not isinstance(event_value, list):
            errors.append(
                f'{source_file}: \'{event_name}\' must be an array of matcher groups, got {type(event_value).__name__}. Correct: "{event_name}": [{{"hooks": [...]}}]'
            )
            continue

        for gi, group in enumerate(event_value):
            gpath = f"{source_file}: {event_name}[{gi}]"

            if not isinstance(group, dict):
                errors.append(
                    f'{gpath}: each matcher group must be an object, got {type(group).__name__}. Correct: {{"matcher": "...", "hooks": [...]}}'
                )
                continue

            matcher = group.get("matcher")
            if matcher is not None and not isinstance(matcher, str):
                errors.append(f"{gpath}.matcher: must be a string (regex), got {type(matcher).__name__}")
            elif isinstance(matcher, str) and event_name in VALID_HOOK_EVENTS:
                warnings.extend(_validate_matcher(matcher, event_name, gpath))

            inner_hooks = group.get("hooks")
            if inner_hooks is None:
                errors.append(
                    f'{gpath}: missing \'hooks\' array. Each matcher group needs: {{"hooks": [{{"type": "command", "command": "..."}}]}}'
                )
                continue

            if not isinstance(inner_hooks, list):
                errors.append(
                    f'{gpath}.hooks: must be an array of hook handlers, got {type(inner_hooks).__name__}. Correct: "hooks": [{{"type": "command", "command": "..."}}]'
                )
                continue

            for hi, handler in enumerate(inner_hooks):
                hpath = f"{gpath}.hooks[{hi}]"

                if not isinstance(handler, dict):
                    errors.append(f"{hpath}: each hook handler must be an object, got {type(handler).__name__}")
                    continue

                htype = handler.get("type")
                if not htype:
                    errors.append(
                        f"{hpath}: missing 'type' field. Must be one of: {', '.join(sorted(VALID_HOOK_TYPES))}"
                    )
                elif htype not in VALID_HOOK_TYPES:
                    errors.append(
                        f"{hpath}: invalid type '{htype}'. Must be one of: {', '.join(sorted(VALID_HOOK_TYPES))}"
                    )
                else:
                    if htype == "command":
                        cmd = handler.get("command")
                        if not cmd:
                            errors.append(f"{hpath}: type 'command' requires a 'command' field")
                        elif not isinstance(cmd, str):
                            errors.append(f"{hpath}.command: must be a string, got {type(cmd).__name__}")
                        else:
                            if "${CLAUDE_PLUGIN_ROOT}" not in cmd and "/" in cmd and not cmd.startswith("jq"):
                                warnings.append(
                                    f"{hpath}.command: uses a path without ${{CLAUDE_PLUGIN_ROOT}} — may not work after installation"
                                )
                            cmd_errors, cmd_warnings = _validate_bash_command(cmd, hpath, plugin_root)
                            errors.extend(cmd_errors)
                            warnings.extend(cmd_warnings)
                    elif htype == "http":
                        url = handler.get("url")
                        if not url:
                            errors.append(f"{hpath}: type 'http' requires a 'url' field")
                        elif not isinstance(url, str):
                            errors.append(f"{hpath}.url: must be a string")
                    elif htype == "prompt":
                        prompt = handler.get("prompt")
                        if not prompt:
                            errors.append(f"{hpath}: type 'prompt' requires a 'prompt' field")
                        elif not isinstance(prompt, str):
                            errors.append(f"{hpath}.prompt: must be a string")

                timeout = handler.get("timeout")
                if timeout is not None and not isinstance(timeout, (int, float)):
                    warnings.append(f"{hpath}.timeout: should be a number (seconds)")

    return errors, warnings


def _parse_simple_frontmatter(text: str) -> Optional[Tuple[Dict[str, str], str]]:
    """Parse YAML-like frontmatter from markdown. Returns (key_values, body) or None.

    This is a simple parser — no YAML library needed. Handles:
    - Simple key: value pairs
    - Multi-line values (indented continuation)
    - Nested objects (hooks:) detected by key presence
    Returns lowercase keys mapped to raw string values.
    """
    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    fm_text = parts[1].strip()
    body = parts[2]
    if not fm_text:
        return ({}, body)

    kv = {}
    for line in fm_text.splitlines():
        if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
            key, val = line.split(":", 1)
            kv[key.strip().lower()] = val.strip()

    return (kv, body)


# ── Known frontmatter fields per component type ──────────

COMMAND_KNOWN_FIELDS = {"description", "model"}

AGENT_KNOWN_FIELDS = {
    "name",
    "description",
    "tools",
    "disallowedtools",
    "model",
    "permissionmode",
    "maxturns",
    "skills",
    "mcpservers",
    "hooks",
    "memory",
    "background",
    "isolation",
    "color",
}
AGENT_REQUIRED_FIELDS = {"name", "description"}
AGENT_BOOLEAN_FIELDS = {"background"}
AGENT_VALID_MODELS = {"haiku", "sonnet", "opus", "inherit"}
AGENT_VALID_PERMISSION_MODES = {"default", "acceptedits", "dontask", "bypasspermissions", "plan"}
AGENT_VALID_MEMORY_SCOPES = {"user", "project", "local"}
AGENT_VALID_ISOLATION = {"worktree"}

SKILL_KNOWN_FIELDS = {
    "name",
    "description",
    "argument-hint",
    "disable-model-invocation",
    "user-invocable",
    "allowed-tools",
    "model",
    "context",
    "agent",
    "hooks",
}
SKILL_BOOLEAN_FIELDS = {"disable-model-invocation", "user-invocable"}
SKILL_MAX_LINES = 500
SKILL_MAX_CHARS = 5000


def _validate_markdown_frontmatter(
    md_path: Path, component_type: str, rel_prefix: str = ""
) -> Tuple[List[str], List[str]]:
    """Validate YAML frontmatter in agent/command/skill markdown files.
    Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return errors, warnings

    name = md_path.name
    if rel_prefix:
        label = f"{rel_prefix}/{name}"
    else:
        label = f"{component_type}s/{name}"

    parsed = _parse_simple_frontmatter(text)

    # ── No frontmatter ──
    if parsed is None:
        # Detect unclosed frontmatter (starts with --- but no closing ---)
        if text.startswith("---") and text.count("---") < 2:
            warnings.append(f"{label}: frontmatter '---' block is not properly closed")
            return errors, warnings
        if component_type == "agent":
            warnings.append(
                f"{label}: missing YAML frontmatter. Agents should start with:\n           ---\n           name: my-agent\n           description: What this agent does\n           ---"
            )
        elif component_type == "skill":
            warnings.append(
                f"{label}: missing YAML frontmatter. Skills should start with:\n           ---\n           description: What this skill does and when to use it\n           ---"
            )
        return errors, warnings

    fm, _body = parsed

    # ── Unclosed frontmatter ──
    # (already handled by _parse_simple_frontmatter returning None for bad split)

    if not fm:
        warnings.append(f"{label}: frontmatter is empty")
        return errors, warnings

    # ══════════════════════════════════════════════════════════
    # Component-specific validation
    # ══════════════════════════════════════════════════════════

    if component_type == "agent":
        # ── Required fields ──
        for req in ("name", "description"):
            if req not in fm:
                if req == "description":
                    warnings.append(
                        f"{label}: missing 'description' in frontmatter — Claude Code uses this to decide when to invoke the agent"
                    )
                else:
                    warnings.append(f"{label}: missing '{req}' in frontmatter")

        # ── Name validation ──
        agent_name = fm.get("name", "")
        if agent_name and not re.match(r"^[a-z][a-z0-9-]*$", agent_name):
            warnings.append(f"{label}: name '{agent_name}' should be lowercase letters, numbers, and hyphens only")

        # ── Unknown fields ──
        for key in fm:
            if key not in AGENT_KNOWN_FIELDS:
                warnings.append(
                    f"{label}: unknown frontmatter field '{key}'. Known fields: {', '.join(sorted(AGENT_KNOWN_FIELDS))}"
                )

        # ── Model validation ──
        model_val = fm.get("model", "").lower()
        if model_val and model_val not in AGENT_VALID_MODELS:
            warnings.append(f"{label}: model '{fm['model']}' — known values: {', '.join(sorted(AGENT_VALID_MODELS))}")

        # ── Boolean fields ──
        for bf in AGENT_BOOLEAN_FIELDS:
            if bf in fm:
                val = fm[bf].lower()
                if val not in ("true", "false"):
                    errors.append(f"{label}: '{bf}' must be true or false, got '{fm[bf]}'")

        # ── permissionMode ──
        pm = fm.get("permissionmode", "")
        if pm and pm.lower() not in AGENT_VALID_PERMISSION_MODES:
            warnings.append(
                f"{label}: permissionMode '{pm}' — known values: default, acceptEdits, dontAsk, bypassPermissions, plan"
            )

        # ── maxTurns ──
        mt = fm.get("maxturns", "")
        if mt:
            try:
                int(mt)
            except ValueError:
                errors.append(f"{label}: maxTurns must be an integer, got '{mt}'")

        # ── memory ──
        mem = fm.get("memory", "")
        if mem and mem.lower() not in AGENT_VALID_MEMORY_SCOPES:
            warnings.append(f"{label}: memory '{mem}' — known scopes: {', '.join(sorted(AGENT_VALID_MEMORY_SCOPES))}")

        # ── isolation ──
        iso = fm.get("isolation", "")
        if iso and iso.lower() not in AGENT_VALID_ISOLATION:
            warnings.append(f"{label}: isolation '{iso}' — only 'worktree' is supported")

    elif component_type == "command":
        # ── Recommended field ──
        if fm and "description" not in fm:
            warnings.append(
                f"{label}: frontmatter present but no 'description' — add one so it shows in autocomplete when users type '/'"
            )

        # ── Unknown fields ──
        for key in fm:
            if key not in COMMAND_KNOWN_FIELDS:
                warnings.append(
                    f"{label}: unknown frontmatter field '{key}'. Command fields: {', '.join(sorted(COMMAND_KNOWN_FIELDS))}"
                )

        # ── Model validation ──
        model_val = fm.get("model", "").lower()
        if model_val and model_val not in AGENT_VALID_MODELS:
            warnings.append(f"{label}: model '{fm['model']}' — known values: {', '.join(sorted(AGENT_VALID_MODELS))}")

    elif component_type == "skill":
        # ── Recommended field ──
        if "description" not in fm:
            warnings.append(
                f"{label}: missing 'description' — skills without description now appear, but Claude uses description for auto-discovery. Recommended to add one."
            )
        else:
            desc = fm["description"]
            if desc and len(desc) > 200:
                warnings.append(f"{label}: description is {len(desc)} chars (max 200 recommended)")

        # ── Name field validation ──
        skill_name = fm.get("name", "")
        if skill_name:
            if len(skill_name) > 64:
                warnings.append(f"{label}: name is {len(skill_name)} chars (max 64)")
            if not re.match(r"^[a-z0-9][a-z0-9-]*$", skill_name):
                warnings.append(f"{label}: name '{skill_name}' should be lowercase letters, numbers, and hyphens only")

        # ── Unknown fields ──
        for key in fm:
            if key not in SKILL_KNOWN_FIELDS:
                warnings.append(
                    f"{label}: unknown frontmatter field '{key}'. Known fields: {', '.join(sorted(SKILL_KNOWN_FIELDS))}"
                )

        # ── Boolean fields ──
        for bf in SKILL_BOOLEAN_FIELDS:
            if bf in fm:
                val = fm[bf].lower()
                if val not in ("true", "false"):
                    errors.append(f"{label}: '{bf}' must be true or false, got '{fm[bf]}'")

        # ── Context/agent fields ──
        context_val = fm.get("context", "")
        if context_val and context_val != "fork":
            warnings.append(f"{label}: context '{context_val}' — only 'fork' is supported")

        agent_val = fm.get("agent", "")
        if agent_val and "context" not in fm:
            warnings.append(f"{label}: 'agent' field has no effect without 'context: fork'")

        # ── Size limits (critical for progressive discovery) ──
        # Measure body only (excluding frontmatter) since Claude Code loads the body for discovery
        total_lines = len(_body.splitlines())
        total_chars = len(_body)

        if total_lines > SKILL_MAX_LINES:
            warnings.append(
                f"{label}: {total_lines} lines exceeds the {SKILL_MAX_LINES}-line limit. Progressive discovery won't work for this skill."
            )
        if total_chars > SKILL_MAX_CHARS:
            warnings.append(
                f"{label}: {total_chars} chars exceeds the {SKILL_MAX_CHARS}-char limit. Progressive discovery won't work for this skill."
            )
        elif total_lines > SKILL_MAX_LINES * 0.8 or total_chars > SKILL_MAX_CHARS * 0.8:
            warnings.append(
                f"{label}: {total_lines} lines / {total_chars} chars — approaching the limit ({SKILL_MAX_LINES} lines / {SKILL_MAX_CHARS} chars). Consider trimming to stay within progressive discovery limits."
            )

    return errors, warnings


_SKILL_AUDIT_MAX_FILES = 200
_SKILL_AUDIT_MAX_DEPTH = 6


def _run_skill_audit(plugin_root: Path) -> Tuple[List[str], List[str]]:
    """Run skill-audit on a plugin directory if available. Returns (errors, warnings)."""
    errors: List[str] = []
    warnings: List[str] = []
    # Check if skill-audit is on PATH
    skill_audit_bin = shutil.which("skill-audit")
    if not skill_audit_bin:
        return errors, warnings
    # Safeguard: skip directories that are too large or deeply nested
    # to avoid hanging on massive folder trees (e.g. ~/.claude/ or node_modules)
    file_count = 0
    for item in plugin_root.rglob("*"):
        file_count += 1
        if file_count > _SKILL_AUDIT_MAX_FILES:
            warnings.append(
                f"skill-audit: skipped — directory has >{_SKILL_AUDIT_MAX_FILES} files (too large for security scan)"
            )
            return errors, warnings
        # Check nesting depth relative to plugin root
        try:
            depth = len(item.relative_to(plugin_root).parts)
            if depth > _SKILL_AUDIT_MAX_DEPTH:
                warnings.append(f"skill-audit: skipped — directory nesting exceeds {_SKILL_AUDIT_MAX_DEPTH} levels")
                return errors, warnings
        except ValueError:
            pass
    try:
        result = subprocess.run(
            [skill_audit_bin, "--format", "json", str(plugin_root)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        warnings.append("skill-audit: timed out or failed to run")
        return errors, warnings

    if result.returncode == 2:
        # Tool execution error — not a finding, skip silently
        return errors, warnings

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return errors, warnings

    for finding in data.get("findings", []):
        severity = finding.get("severity", "info")
        msg = finding.get("message", "unknown issue")
        scanner = finding.get("scanner", "")
        file_path = finding.get("file", "")
        line = finding.get("line")
        # Make file path relative to plugin root for readability
        try:
            rel = Path(file_path).relative_to(plugin_root)
        except (ValueError, TypeError):
            rel = file_path
        location = f"{rel}:{line}" if line else str(rel)
        text = f"skill-audit ({scanner}): {msg} [{location}]"
        if severity == "error":
            errors.append(text)
        else:
            warnings.append(text)

    return errors, warnings


def validate_plugin(
    plugin_root: Path, ignore_fn: Optional[Callable[[Path], bool]] = None, run_security_audit: bool = True
):
    """Validate a plugin directory. Returns (errors, warnings).
    If ignore_fn is provided, files/dirs matched by it are skipped during validation.
    If run_security_audit is False, skip the skill-audit external tool check."""
    errors: list[str] = []
    warnings: list[str] = []

    # ── 1. Manifest ──────────────────────────────────────────

    pj_path = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj_path.exists():
        errors.append("Missing .claude-plugin/plugin.json manifest")
        return errors, warnings

    try:
        manifest = json.loads(pj_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"plugin.json: JSON parse error: {e}")
        return errors, warnings

    if not isinstance(manifest, dict):
        errors.append("plugin.json: must be a JSON object")
        return errors, warnings

    # ── 2. Required and recommended fields ───────────────────

    name = manifest.get("name")
    if not name:
        errors.append("plugin.json: 'name' field is required")
    elif not isinstance(name, str):
        errors.append(f"plugin.json: 'name' must be a string, got {type(name).__name__}")
    elif not re.match(r"^[a-z][a-z0-9_-]*$", name):
        warnings.append(f"plugin.json: name '{name}' should be kebab-case (lowercase, hyphens/underscores, no spaces)")

    version = manifest.get("version")
    if not version:
        warnings.append("plugin.json: 'version' field is recommended (e.g. '1.0.0')")
    elif not isinstance(version, str):
        warnings.append(f"plugin.json: 'version' should be a string, got {type(version).__name__}")
    elif not re.match(r"^\d+\.\d+\.\d+", version):
        warnings.append(f"plugin.json: version '{version}' doesn't follow semver (x.y.z)")

    if not manifest.get("description"):
        warnings.append("plugin.json: 'description' field is recommended")

    # author and repository accept both string and dict per npm conventions
    for field, expected_types in [
        ("author", (str, dict)),
        ("keywords", (list,)),
        ("homepage", (str,)),
        ("repository", (str, dict)),
        ("license", (str,)),
    ]:
        val = manifest.get(field)
        if val is not None and not isinstance(val, expected_types):
            type_names = "/".join(t.__name__ for t in expected_types)
            warnings.append(f"plugin.json: '{field}' should be {type_names}, got {type(val).__name__}")

    if isinstance(manifest.get("keywords"), list):
        for kw in manifest["keywords"]:
            if not isinstance(kw, str):
                warnings.append(f"plugin.json: keywords must be strings, found {type(kw).__name__}")
                break

    # ── 3. Component path fields ─────────────────────────────

    for field, allowed_types in COMPONENT_PATH_FIELDS.items():
        val = manifest.get(field)
        if val is None:
            continue
        type_err = _check_type(val, allowed_types)
        if type_err:
            errors.append(f"plugin.json: '{field}' — {type_err}")
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if not isinstance(item, str):
                    errors.append(f"plugin.json: '{field}[{i}]' must be a string, got {type(item).__name__}")
            for item in val:
                if isinstance(item, str) and not item.startswith("./"):
                    warnings.append(f"plugin.json: '{field}' path '{item}' should start with './'")
        elif isinstance(val, str) and not val.startswith("./") and field in ("commands", "agents", "hooks"):
            warnings.append(f"plugin.json: '{field}' path '{val}' should start with './'")

    # ── 4. Directory structure ───────────────────────────────

    claude_plugin_dir = plugin_root / ".claude-plugin"
    for component in ("commands", "agents", "hooks", "skills", "scripts"):
        if (claude_plugin_dir / component).exists():
            errors.append(
                f"'{component}/' is inside .claude-plugin/ — move it to the plugin root. Only plugin.json belongs in .claude-plugin/"
            )

    # ── 5. Hooks deep validation ─────────────────────────────

    hooks_json = plugin_root / "hooks" / "hooks.json"
    if hooks_json.exists():
        try:
            hooks_data = json.loads(hooks_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"hooks/hooks.json: JSON parse error: {e}")
            hooks_data = None
        if hooks_data is not None:
            if not isinstance(hooks_data, dict):
                errors.append("hooks/hooks.json: must be a JSON object")
            else:
                h_e, h_w = _validate_hooks_structure(hooks_data, "hooks/hooks.json", plugin_root)
                errors.extend(h_e)
                warnings.extend(h_w)

    inline_hooks = manifest.get("hooks")
    if isinstance(inline_hooks, dict):
        h_e, h_w = _validate_hooks_structure(inline_hooks, "plugin.json (inline hooks)", plugin_root)
        errors.extend(h_e)
        warnings.extend(h_w)
    elif isinstance(inline_hooks, str):
        hook_file = plugin_root / inline_hooks.lstrip("./")
        if not hook_file.exists():
            errors.append(f"plugin.json: hooks path '{inline_hooks}' does not exist")
        else:
            try:
                hd = json.loads(hook_file.read_text(encoding="utf-8"))
                h_e, h_w = _validate_hooks_structure(hd, inline_hooks, plugin_root)
                errors.extend(h_e)
                warnings.extend(h_w)
            except json.JSONDecodeError as e:
                errors.append(f"{inline_hooks}: JSON parse error: {e}")

    # ── 6. MCP configuration ─────────────────────────────────

    mcp_json = plugin_root / ".mcp.json"
    if mcp_json.exists():
        try:
            mcp_data = json.loads(mcp_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f".mcp.json: JSON parse error: {e}")
            mcp_data = None
        if mcp_data is not None and isinstance(mcp_data, dict):
            servers = mcp_data.get("mcpServers", mcp_data)
            for srv_name, srv_config in servers.items():
                if srv_name == "mcpServers":
                    continue
                if not isinstance(srv_config, dict):
                    errors.append(f".mcp.json: server '{srv_name}' must be an object")
                elif not srv_config.get("command") and not srv_config.get("url"):
                    warnings.append(f".mcp.json: server '{srv_name}' has no 'command' or 'url'")

    inline_mcp = manifest.get("mcpServers")
    if isinstance(inline_mcp, dict):
        for srv_name, srv_config in inline_mcp.items():
            if not isinstance(srv_config, dict):
                errors.append(f"plugin.json mcpServers: '{srv_name}' must be an object")
            elif not srv_config.get("command") and not srv_config.get("url"):
                warnings.append(f"plugin.json mcpServers: '{srv_name}' has no 'command' or 'url'")

    # ── 7. Script permissions and existence ──────────────────

    non_executable = []
    missing_shebang = []
    for script in _find_all_scripts(plugin_root):
        if ignore_fn and ignore_fn(script):
            continue
        rel = str(script.relative_to(plugin_root))
        if not _is_executable(script):
            non_executable.append(rel)
        # Shebang check — critical for cross-platform
        if script.suffix in (".py", ".sh", ".rb", ".pl") and not _has_shebang(script):
            missing_shebang.append(rel)

    if non_executable:
        fix_note = "auto-fixed during install" if not IS_WINDOWS else "ensure shebangs are present"
        warnings.append(
            f"Scripts not executable ({fix_note}): "
            + ", ".join(non_executable[:5])
            + (f" +{len(non_executable) - 5} more" if len(non_executable) > 5 else "")
        )

    if missing_shebang:
        warnings.append(
            "Scripts missing shebang (e.g. #!/usr/bin/env python3): "
            + ", ".join(missing_shebang[:5])
            + (f" +{len(missing_shebang) - 5} more" if len(missing_shebang) > 5 else "")
            + ". Without a shebang, scripts may not run correctly across platforms."
        )

    # ── 8. Content directories and frontmatter ───────────────

    commands_dir = plugin_root / "commands"
    if commands_dir.exists() and commands_dir.is_dir():
        cmd_files = [f for f in commands_dir.rglob("*.md") if not (ignore_fn and ignore_fn(f))]
        if not cmd_files:
            warnings.append("commands/ directory exists but contains no .md files")
        else:
            for md in cmd_files:
                cmd_e, cmd_w = _validate_markdown_frontmatter(md, "command")
                errors.extend(cmd_e)
                warnings.extend(cmd_w)

    skills_dir = plugin_root / "skills"
    if skills_dir.exists() and skills_dir.is_dir():
        skill_mds = [f for f in skills_dir.rglob("SKILL.md") if not (ignore_fn and ignore_fn(f))]
        if not skill_mds:
            warnings.append("skills/ directory exists but contains no SKILL.md files")
        else:
            for md in skill_mds:
                # Build a relative path like "skills/code-review/SKILL.md"
                rel = str(md.relative_to(plugin_root))
                sk_e, sk_w = _validate_markdown_frontmatter(
                    md, "skill", rel_prefix=str(md.parent.relative_to(plugin_root))
                )
                errors.extend(sk_e)
                warnings.extend(sk_w)

    agents_dir = plugin_root / "agents"
    if agents_dir.exists() and agents_dir.is_dir():
        agent_mds = [f for f in agents_dir.rglob("*.md") if not (ignore_fn and ignore_fn(f))]
        if not agent_mds:
            warnings.append("agents/ directory exists but contains no .md files")
        else:
            for md in agent_mds:
                ag_e, ag_w = _validate_markdown_frontmatter(md, "agent")
                errors.extend(ag_e)
                warnings.extend(ag_w)

    # ── 9. LSP configuration (.lsp.json) ────────────────────

    lsp_json = plugin_root / ".lsp.json"
    if lsp_json.exists():
        try:
            lsp_data = json.loads(lsp_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f".lsp.json: JSON parse error: {e}")
            lsp_data = None

        if lsp_data is not None and isinstance(lsp_data, dict):
            for lang, cfg in lsp_data.items():
                if not isinstance(cfg, dict):
                    errors.append(f".lsp.json: '{lang}' must be an object")
                    continue
                if not cfg.get("command"):
                    errors.append(f".lsp.json: '{lang}' is missing required 'command' field")
                if not cfg.get("extensionToLanguage"):
                    warnings.append(f".lsp.json: '{lang}' has no 'extensionToLanguage' mapping")

    if isinstance(manifest.get("lspServers"), dict):
        # Inline LSP in plugin.json — same checks
        for lang, cfg in manifest["lspServers"].items():
            if isinstance(cfg, dict) and not cfg.get("command"):
                errors.append(f"plugin.json lspServers: '{lang}' is missing required 'command' field")

    # ── 10. Plugin settings.json ─────────────────────────────

    plugin_settings = plugin_root / "settings.json"
    if plugin_settings.exists():
        try:
            ps_data = json.loads(plugin_settings.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f"settings.json: JSON parse error: {e}")
            ps_data = None

        if ps_data is not None and isinstance(ps_data, dict):
            supported_keys = {"agent"}
            for key in ps_data:
                if key not in supported_keys:
                    warnings.append(
                        f"settings.json: key '{key}' is not a recognized plugin setting. Currently supported: {', '.join(sorted(supported_keys))}"
                    )

    # ── 11. Check plugin has actual content ──────────────────

    has_content = any(
        (plugin_root / d).exists()
        for d in ("commands", "skills", "agents", "hooks", "scripts", ".mcp.json", ".lsp.json")
    )
    if not has_content:
        warnings.append("Plugin has a manifest but no commands, skills, agents, hooks, MCP, or LSP config")

    # ── 12. Security audit via skill-audit (if available) ────
    if run_security_audit:
        sa_errors, sa_warnings = _run_skill_audit(plugin_root)
        errors.extend(sa_errors)
        warnings.extend(sa_warnings)

    return errors, warnings


def print_validation_report(errors, warnings, _plugin_name):
    if errors:
        print()
        for e in errors:
            print(f"  {RED}✖ ERROR:{NC} {e}")
    if warnings:
        print()
        for w in warnings:
            print(f"  {YELLOW}⚠ WARN:{NC}  {w}")

    if not errors and not warnings:
        ok("Validation passed — no issues found")
    elif not errors:
        ok(f"Validation passed with {len(warnings)} warning(s)")
    else:
        err(f"Validation failed — {len(errors)} error(s), {len(warnings)} warning(s)")

    return len(errors) == 0


# ── Install ───────────────────────────────────────────────


def do_install(
    source_path: str, marketplace_name: Optional[str], force: bool = False, dry_run: bool = False, quiet: bool = False
):
    if dry_run and not quiet:
        info("DRY RUN — no files will be modified")

    source = Path(source_path)
    if not source.exists():
        err(f"Not found: {source_path}")
        sys.exit(1)

    is_directory = source.is_dir()
    ignore_fn = None  # gitignore filter, only used for directory installs
    tmp_cleanup = None  # temp directory to clean up after archive extraction

    # ── Resolve plugin_root from source ──────────────────────

    if is_directory:
        if not quiet:
            info(f"Installing from directory: {source}")
        plugin_root = source if (source / ".claude-plugin" / "plugin.json").exists() else find_plugin_root(source)
        if not plugin_root:
            err("No plugin found in directory.")
            err("Expected: <dir>/.claude-plugin/plugin.json")
            sys.exit(1)
        # Build gitignore matcher for filtering
        ignore_fn = _build_gitignore_matcher(plugin_root)
    else:
        if not quiet:
            info("Extracting archive...")
        tmp_cleanup = tempfile.mkdtemp()
        tmp = Path(tmp_cleanup)
        try:
            extract_archive(source_path, tmp)
        except Exception:
            shutil.rmtree(tmp_cleanup, ignore_errors=True)
            raise
        plugin_root = find_plugin_root(tmp)
        if not plugin_root:
            err("No plugin found in archive.")
            err("Expected: <dir>/.claude-plugin/plugin.json")
            if not quiet:
                print("\nArchive contents:")
                for f in sorted(tmp.rglob("*")):
                    if f.is_file():
                        print(f"  {f.relative_to(tmp)}")
            shutil.rmtree(tmp_cleanup, ignore_errors=True)
            sys.exit(1)

    # ── From here on, plugin_root is resolved ────────────────

    try:
        meta = read_plugin_meta(plugin_root)
        plugin_name = meta["name"]
        plugin_version = meta["version"]
        plugin_desc = meta["description"]

        if not quiet:
            ok(f"Found plugin: {BOLD}{plugin_name}{NC} v{plugin_version}")
            if plugin_desc:
                info(f"  {plugin_desc}")

        if not quiet:
            info("Validating plugin...")
        v_errors, v_warnings = validate_plugin(plugin_root, ignore_fn=ignore_fn)
        if not quiet:
            valid = print_validation_report(v_errors, v_warnings, plugin_name)
        else:
            valid = len(v_errors) == 0
        if not valid:
            if not force:
                err("Plugin has validation errors. Fix them or use --force to install anyway.")
                sys.exit(1)
            else:
                if not quiet:
                    warn("Installing despite validation errors (--force)")
        if not quiet:
            print()

        if not marketplace_name:
            err("Marketplace name is required.")
            err("Usage: claude-plugin-install <source> <marketplace>")
            err(f"Example: claude-plugin-install {source_path} my-marketplace")
            sys.exit(1)

        plugin_key = f"{plugin_name}@{marketplace_name}"
        mp_dir = MARKETPLACES_DIR / marketplace_name

        # Warn if the same plugin already exists in OTHER marketplaces
        if not quiet and MARKETPLACES_DIR.exists():
            other_locations = []
            for other_mp in MARKETPLACES_DIR.iterdir():
                if not other_mp.is_dir() or other_mp.name == marketplace_name:
                    continue
                other_plug = other_mp / "plugins" / plugin_name
                if other_plug.exists():
                    other_locations.append(f"{plugin_name}@{other_mp.name}")
            if other_locations:
                print()
                warn(f"Plugin '{plugin_name}' is also installed in other marketplace(s):")
                for loc in other_locations:
                    print(f"    {YELLOW}• {loc}{NC}")
                print("  This may cause conflicts. Consider removing the duplicate(s):")
                for loc in other_locations:
                    print(f"    {sys.argv[0]} --uninstall {loc}")
                print()

        # If marketplace already exists, ask for confirmation (quiet auto-confirms)
        if mp_dir.exists() and not force and not dry_run:
            if quiet:
                pass  # quiet mode auto-confirms
            else:
                warn(f"Marketplace '{marketplace_name}' already exists at {mp_dir}")
                answer = input("  Install into existing marketplace? [y/N] ").strip().lower()
                if answer not in ("y", "yes"):
                    info("Aborted.")
                    return

        if not quiet:
            info(f"Marketplace: {marketplace_name}")

        dest_plugin_dir = mp_dir / "plugins" / plugin_name
        if dest_plugin_dir.exists():
            if force or dry_run or quiet:
                if not quiet:
                    info(
                        f"Updating '{plugin_name}' in marketplace '{marketplace_name}'"
                        + (" (--force)" if force else " (dry run)" if dry_run else "")
                    )
            else:
                warn(f"Plugin '{plugin_name}' already exists in marketplace '{marketplace_name}'")
                answer = input("  Overwrite? [y/N] ").strip().lower()
                if answer not in ("y", "yes"):
                    info("Aborted.")
                    return
            if not dry_run:
                shutil.rmtree(dest_plugin_dir)

        if dry_run:
            if not quiet:
                ok(f"Would copy plugin to {dest_plugin_dir}")
                ok(f"Would register marketplace in {SETTINGS_TARGET.name}")
                ok(f"Would enable plugin as {plugin_key}")
                print(f"\n  {CYAN}Run without --dry-run to install.{NC}")
            return

        # ── Copy plugin to marketplace ───────────────────────
        if is_directory:
            # Directory install: respect .gitignore and exclude .git
            _copy_plugin_from_dir(plugin_root, dest_plugin_dir, ignore_fn)
            if not dest_plugin_dir.exists():
                err("No files to install — all plugin files are gitignored.")
                sys.exit(1)
        else:
            # Archive install: straight copy (archives shouldn't contain .git)
            dest_plugin_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(plugin_root, dest_plugin_dir)
        _fix_permissions(dest_plugin_dir)
        if not quiet:
            if is_directory:
                ok("Plugin copied to marketplace (respecting .gitignore)")
            else:
                ok("Plugin copied to marketplace")

    finally:
        # Clean up temp directory for archive installs
        if tmp_cleanup is not None:
            shutil.rmtree(tmp_cleanup, ignore_errors=True)

    # Generate/update marketplace.json
    mp_json_dir = mp_dir / ".claude-plugin"
    mp_json_dir.mkdir(parents=True, exist_ok=True)
    mp_json_path = mp_json_dir / "marketplace.json"

    if mp_json_path.exists():
        mj = load_json_safe(mp_json_path)
        plugins_list = mj.setdefault("plugins", [])
        plugins_list[:] = [p for p in plugins_list if p.get("name") != plugin_name]
    else:
        mj = {
            "name": marketplace_name,
            "version": "1.0.0",
            "owner": {
                "name": "local",
            },
            "metadata": {
                "description": "Local plugin marketplace (auto-generated by claude-plugin-install)",
            },
        }
        plugins_list = []
        mj["plugins"] = plugins_list

    # Ensure 'owner' is a valid object (older marketplace.json files may lack it or have it corrupted)
    if not isinstance(mj.get("owner"), dict) or "name" not in mj["owner"]:
        mj["owner"] = {"name": "local"}
    # Ensure 'metadata' exists
    if not isinstance(mj.get("metadata"), dict):
        mj["metadata"] = {"description": "Local plugin marketplace (auto-generated by claude-plugin-install)"}

    plugins_list.append(
        {
            "name": plugin_name,
            "description": plugin_desc,
            "version": plugin_version,
            "source": f"./plugins/{plugin_name}",
        }
    )

    save_json_safe(mp_json_path, mj, dry_run=dry_run)
    if not quiet:
        ok("Marketplace manifest updated")

    settings = load_json_safe(SETTINGS_TARGET)
    ekm = settings.setdefault("extraKnownMarketplaces", {})
    ekm[marketplace_name] = {
        "source": {
            "source": "directory",
            "path": _portable_path(mp_dir),
        }
    }
    ep = settings.setdefault("enabledPlugins", {})
    ep[plugin_key] = True
    save_json_safe(SETTINGS_TARGET, settings, dry_run=dry_run)
    if not quiet:
        ok(f"Registered in {SETTINGS_TARGET.name}")

    installed = load_json_safe(INSTALLED_FILE)
    if "version" not in installed:
        installed = {"version": 1, "plugins": installed}
    # Guard against corrupt file where "plugins" is not a dict
    if not isinstance(installed.get("plugins"), dict):
        installed["plugins"] = {}
    plugins_map = installed["plugins"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    plugins_map[plugin_key] = {
        "version": plugin_version,
        "installedAt": now,
        "lastUpdated": now,
        "installPath": _portable_path(dest_plugin_dir),
        "isLocal": True,
    }
    save_json_safe(INSTALLED_FILE, installed, dry_run=dry_run)
    if not quiet:
        ok("Plugin registered in installed_plugins.json")

        script = sys.argv[0]
        print()
        print(f"{GREEN}{'═' * 60}{NC}")
        print(f"{GREEN}  {BOLD}{plugin_name}{NC}{GREEN} installed successfully!{NC}")
        print(f"{GREEN}{'═' * 60}{NC}")
        print()
        print(f"  Plugin key:    {BOLD}{plugin_key}{NC}")
        print(f"  Location:      {dest_plugin_dir}")
        print(f"  Marketplace:   {marketplace_name}")
        print(f"  Settings:      {SETTINGS_TARGET}")
        print()
        print(f"  The plugin is {GREEN}enabled{NC} by default — no action needed.")
        print(f"  {BOLD}Run /reload-plugins or restart Claude Code for changes to take effect.{NC}")
        print()

        # Warn if the plugin references a different marketplace or repository
        origin_refs = _detect_plugin_origin_refs(dest_plugin_dir)
        if origin_refs:
            print(f"  {YELLOW}{BOLD}NOTE:{NC}{YELLOW} This plugin contains references to an origin")
            print(f"  marketplace or repository that differs from '{marketplace_name}':{NC}")
            for ref in origin_refs:
                print(f"    {YELLOW}• {ref}{NC}")
            print()

        print(f"  {BOLD}Manage with this script:{NC}")
        print(f"    Update:      {script} --update <source> {marketplace_name}")
        print(f"    Uninstall:   {script} --uninstall {plugin_key}")
        print(f"    Disable:     {script} --disable {plugin_key}")
        print(f"    Enable:      {script} --enable {plugin_key}")
        print(f"    List all:    {script} --list")
        print(f"    Health:      {script} --doctor")
        print()
        print(f"  {BOLD}Manage with Claude CLI:{NC}")
        print(f"    Uninstall:   claude plugin uninstall {plugin_key}")
        print(f"    Update:      claude plugin update {plugin_key}")
        print(f"    Disable:     claude plugin disable {plugin_key}")
        print(f"    Enable:      claude plugin enable {plugin_key}")
        print("    List:        claude plugin list")
        print()


# ── Uninstall ─────────────────────────────────────────────


def do_uninstall(plugin_key: str, quiet: bool = False, dry_run: bool = False):
    if "@" not in plugin_key:
        err("Format: --uninstall <plugin-name>@<marketplace-name>")
        sys.exit(1)

    plugin_name, marketplace_name = plugin_key.split("@", 1)
    mp_dir = MARKETPLACES_DIR / marketplace_name
    plug_dir = mp_dir / "plugins" / plugin_name

    if dry_run:
        if not quiet:
            info(f"DRY RUN — would uninstall {plugin_key}")
            ok(f"Would remove {plug_dir}")
            ok(f"Would update settings in {SETTINGS_TARGET.name}")
        return

    if not quiet:
        info(f"Uninstalling {plugin_name} from marketplace {marketplace_name}...")

    if plug_dir.exists():
        try:
            shutil.rmtree(plug_dir)
        except OSError as e:
            err(f"Failed to remove plugin directory: {e}")
            sys.exit(1)
        if not quiet:
            ok("Removed plugin directory")
    else:
        warn(f"Plugin directory not found: {plug_dir}")

    mp_json = mp_dir / ".claude-plugin" / "marketplace.json"
    if mp_json.exists():
        mj = load_json_safe(mp_json)
        mj["plugins"] = [p for p in mj.get("plugins", []) if p.get("name") != plugin_name]
        save_json_safe(mp_json, mj)

    plugins_parent = mp_dir / "plugins"
    remaining = [d for d in (plugins_parent.iterdir() if plugins_parent.exists() else []) if d.is_dir()]

    # Load settings once, make all modifications, save once
    settings = load_json_safe(SETTINGS_TARGET)
    if not remaining:
        if not quiet:
            info(f"Marketplace '{marketplace_name}' is now empty, removing...")
        shutil.rmtree(mp_dir, ignore_errors=True)
        settings.get("extraKnownMarketplaces", {}).pop(marketplace_name, None)
        if not quiet:
            ok(f"Removed empty marketplace '{marketplace_name}'")
    settings.get("enabledPlugins", {}).pop(plugin_key, None)
    save_json_safe(SETTINGS_TARGET, settings)

    installed = load_json_safe(INSTALLED_FILE)
    if "version" not in installed:
        installed = {"version": 1, "plugins": installed}
    # Guard against corrupt file where "plugins" is not a dict
    if not isinstance(installed.get("plugins"), dict):
        installed["plugins"] = {}
    plugins_map = installed["plugins"]
    plugins_map.pop(plugin_key, None)
    save_json_safe(INSTALLED_FILE, installed)

    # Clean up Claude Code's plugin cache — Claude Code does NOT auto-invalidate stale cache
    # Check the expected path: cache/<marketplace>/<plugin>/
    cache_mp = CACHE_DIR / marketplace_name
    if cache_mp.exists():
        cache_plug = cache_mp / plugin_name
        if cache_plug.exists():
            shutil.rmtree(cache_plug, ignore_errors=True)
            if not quiet:
                ok("Removed cached plugin data")
        # If no other plugins cached in this marketplace, remove the marketplace cache dir
        remaining_cached = [d for d in cache_mp.iterdir() if d.is_dir()]
        if not remaining_cached:
            shutil.rmtree(cache_mp, ignore_errors=True)
    # Also check for orphaned cache dirs: cache/<plugin>/ (without marketplace prefix)
    # Claude Code sometimes caches to the wrong path
    orphan_cache = CACHE_DIR / plugin_name
    if orphan_cache.exists() and orphan_cache != cache_mp:
        shutil.rmtree(orphan_cache, ignore_errors=True)
        if not quiet:
            ok(f"Removed orphaned cache at {orphan_cache}")

    if not quiet:
        ok(f"Uninstalled {plugin_key}")
        print("  Run /reload-plugins or restart Claude Code for changes to take effect.")


# ── Enable / Disable ─────────────────────────────────────


def do_enable(plugin_key: str, quiet: bool = False, dry_run: bool = False):
    if "@" not in plugin_key:
        err("Format: --enable <plugin-name>@<marketplace-name>")
        sys.exit(1)

    plugin_name, marketplace_name = plugin_key.split("@", 1)
    plug_dir = MARKETPLACES_DIR / marketplace_name / "plugins" / plugin_name
    if not plug_dir.exists():
        err(f"Plugin not found: {plug_dir}")
        sys.exit(1)

    settings = load_json_safe(SETTINGS_TARGET)
    ep = settings.setdefault("enabledPlugins", {})
    if ep.get(plugin_key) is True:
        if not quiet:
            info(f"{plugin_key} is already enabled.")
        return

    if dry_run:
        if not quiet:
            ok(f"Would enable {plugin_key}")
        return

    ep[plugin_key] = True
    save_json_safe(SETTINGS_TARGET, settings)
    if not quiet:
        ok(f"Enabled {plugin_key}")
        print("  Run /reload-plugins or restart Claude Code for changes to take effect.")


def do_disable(plugin_key: str, quiet: bool = False, dry_run: bool = False):
    if "@" not in plugin_key:
        err("Format: --disable <plugin-name>@<marketplace-name>")
        sys.exit(1)

    plugin_name, marketplace_name = plugin_key.split("@", 1)
    plug_dir = MARKETPLACES_DIR / marketplace_name / "plugins" / plugin_name
    if not plug_dir.exists():
        err(f"Plugin not found: {plug_dir}")
        sys.exit(1)

    settings = load_json_safe(SETTINGS_TARGET)
    ep = settings.setdefault("enabledPlugins", {})
    if ep.get(plugin_key) is False:
        if not quiet:
            info(f"{plugin_key} is already disabled.")
        return

    if dry_run:
        if not quiet:
            ok(f"Would disable {plugin_key}")
        return

    ep[plugin_key] = False
    save_json_safe(SETTINGS_TARGET, settings)
    if not quiet:
        ok(f"Disabled {plugin_key}")
        print("  Run /reload-plugins or restart Claude Code for changes to take effect.")


# ── Update ───────────────────────────────────────────────


def do_update(
    source_path: str, marketplace_name: Optional[str], force: bool = False, dry_run: bool = False, quiet: bool = False
):  # noqa: ARG001 (force accepted from argparse but update always forces reinstall)
    """Update a plugin by uninstalling the old version and reinstalling from a new source."""
    # Resolve the source to find the plugin name
    source = Path(source_path)
    if not source.exists():
        err(f"Not found: {source_path}")
        sys.exit(1)

    # Extract plugin name from the new source
    tmp_cleanup = None
    if source.is_dir():
        plugin_root = source if (source / ".claude-plugin" / "plugin.json").exists() else find_plugin_root(source)
        if not plugin_root:
            err("No plugin found in directory.")
            err("Expected: <dir>/.claude-plugin/plugin.json")
            sys.exit(1)
    else:
        tmp_cleanup = tempfile.mkdtemp()
        tmp = Path(tmp_cleanup)
        try:
            extract_archive(source_path, tmp)
        except Exception:
            shutil.rmtree(tmp_cleanup, ignore_errors=True)
            raise
        plugin_root = find_plugin_root(tmp)
        if not plugin_root:
            err("No plugin found in archive.")
            err("Expected: <dir>/.claude-plugin/plugin.json")
            shutil.rmtree(tmp_cleanup, ignore_errors=True)
            sys.exit(1)

    try:
        meta = read_plugin_meta(plugin_root)
        plugin_name = meta["name"]
    finally:
        if tmp_cleanup:
            shutil.rmtree(tmp_cleanup, ignore_errors=True)

    if not marketplace_name:
        err("Marketplace name is required for update.")
        err("Usage: claude-plugin-install --update <source> <marketplace>")
        sys.exit(1)

    plugin_key = f"{plugin_name}@{marketplace_name}"
    plug_dir = MARKETPLACES_DIR / marketplace_name / "plugins" / plugin_name

    if not plug_dir.exists():
        err(f"Plugin not installed: {plugin_key}")
        err("Use a normal install instead of --update for new plugins.")
        sys.exit(1)

    old_meta = read_plugin_meta(plug_dir)
    old_version = old_meta.get("version", "unknown")

    if not quiet:
        info(f"Updating {BOLD}{plugin_name}{NC} in marketplace '{marketplace_name}'")
        info(f"  Old version: {old_version}  →  New version: {meta['version']}")

    if dry_run:
        if not quiet:
            ok("Would uninstall old version and reinstall from new source.")
            print(f"\n  {CYAN}Run without --dry-run to update.{NC}")
        return

    # Uninstall old version (always quiet during update — the install will print the success banner)
    do_uninstall(
        plugin_key, quiet=True
    )  # must delete cache — Claude Code doesn't auto-invalidate stale cache on restart

    # Reinstall from the new source (always force — we just uninstalled, no overwrite prompt needed)
    do_install(
        source_path, marketplace_name, force=True, dry_run=False, quiet=quiet
    )  # always force — we just uninstalled

    if not quiet:
        info(f"Updated from v{old_version} → v{meta['version']}")


# ── List ──────────────────────────────────────────────────


def do_list():
    if not MARKETPLACES_DIR.exists():
        info("No local marketplaces found. Nothing installed yet.")
        return

    print(f"{BOLD}Locally installed plugins:{NC}")
    print()

    settings = load_json_safe(SETTINGS_TARGET)
    found = False
    for mp_dir in sorted(MARKETPLACES_DIR.iterdir()):
        if not mp_dir.is_dir():
            continue
        plugins_dir = mp_dir / "plugins"
        if not plugins_dir.exists():
            continue

        mp_name = mp_dir.name
        for plug_dir in sorted(plugins_dir.iterdir()):
            if not plug_dir.is_dir():
                continue
            if not (plug_dir / ".claude-plugin" / "plugin.json").exists():
                continue

            meta = read_plugin_meta(plug_dir)
            plugin_key = f"{meta['name']}@{mp_name}"

            enabled = settings.get("enabledPlugins", {}).get(plugin_key, None)
            status = f"{GREEN}enabled{NC}" if enabled else f"{YELLOW}disabled{NC}" if enabled is False else ""

            components = []
            for comp, glob_pat, label in [
                ("commands", "*.md", "command"),
                ("agents", "*.md", "agent"),
                ("skills", "SKILL.md", "skill"),
            ]:
                comp_dir = plug_dir / comp
                if comp_dir.exists():
                    count = len(list(comp_dir.rglob(glob_pat)))
                    if count:
                        components.append(f"{count} {label}{'s' if count != 1 else ''}")
            if (plug_dir / "hooks").exists():
                components.append("hooks")
            if (plug_dir / ".mcp.json").exists():
                components.append("MCP")

            comp_str = f"  [{', '.join(components)}]" if components else ""

            print(f"  {GREEN}{meta['name']}{NC}@{mp_name}  v{meta['version']}  {status}{comp_str}")
            if meta["description"]:
                print(f"    {meta['description']}")
            print(f"    {CYAN}{plug_dir}{NC}")
            found = True

    if not found:
        info("No plugins installed by this tool yet.")
    print()


# ── Validate ──────────────────────────────────────────────


def do_validate(source_path: str):
    p = Path(source_path)
    tmpdir = None  # Track if we created a temp dir
    ignore_fn = None  # gitignore filter for directory validation

    # Handle plugin@marketplace syntax for installed plugins
    if "@" in source_path and not p.exists():
        plugin_name, marketplace_name = source_path.split("@", 1)
        plug_dir = MARKETPLACES_DIR / marketplace_name / "plugins" / plugin_name
        if plug_dir.exists() and (plug_dir / ".claude-plugin" / "plugin.json").exists():
            info(f"Validating installed plugin: {source_path}")
            plugin_root = plug_dir
        else:
            err(f"Installed plugin not found: {source_path}")
            err(f"Expected at: {plug_dir}")
            sys.exit(1)
    elif p.is_dir():
        info(f"Validating plugin directory: {p}")
        found_root = p if (p / ".claude-plugin" / "plugin.json").exists() else find_plugin_root(p)
        if not found_root:
            err("No plugin found in directory. Expected: .claude-plugin/plugin.json")
            sys.exit(1)
        plugin_root = found_root
        # Build gitignore matcher for directory validation
        ignore_fn = _build_gitignore_matcher(plugin_root)
    elif p.is_file():
        info("Extracting archive for validation...")
        tmpdir = tempfile.mkdtemp()
        extract_archive(source_path, Path(tmpdir))
        found_archive_root = find_plugin_root(Path(tmpdir))
        if not found_archive_root:
            err("No plugin found in archive. Expected: <dir>/.claude-plugin/plugin.json")
            print("\nArchive contents:")
            for f in sorted(Path(tmpdir).rglob("*")):
                if f.is_file():
                    print(f"  {f.relative_to(Path(tmpdir))}")
            shutil.rmtree(tmpdir, ignore_errors=True)
            sys.exit(1)
        plugin_root = found_archive_root
    else:
        err(f"Not found: {source_path}")
        sys.exit(1)

    meta = read_plugin_meta(plugin_root)
    ok(f"Found plugin: {BOLD}{meta['name']}{NC} v{meta['version']}")
    if meta["description"]:
        info(f"  {meta['description']}")

    print()
    info("Running validation checks...")
    v_errors, v_warnings = validate_plugin(plugin_root, ignore_fn=ignore_fn)
    valid = print_validation_report(v_errors, v_warnings, meta["name"])

    # Cleanup temp dir if we created one
    if tmpdir is not None:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    if valid:
        print(f"  {GREEN}Plugin is ready to install.{NC}")
    else:
        print(f"  {RED}Plugin has errors that should be fixed before installing.{NC}")
        print("  Use --force to install anyway.")

    sys.exit(0 if valid else 1)


# ── Doctor ───────────────────────────────────────────────


def do_doctor(verbose: bool = False):
    """Check overall health of local plugin installation."""
    print(f"{BOLD}Plugin installation health check{NC}")
    print()

    issues = 0

    # 1. Check Claude directory exists
    if not CLAUDE_DIR.exists():
        info(f"Claude directory not found at {CLAUDE_DIR}")
        info("No plugins have been installed yet. This is normal for a fresh setup.")
        return
    ok(f"Claude directory: {CLAUDE_DIR}")

    # 2. Check settings files
    for label, path in [("settings.json", SETTINGS_FILE), ("settings.local.json", SETTINGS_LOCAL_FILE)]:
        if path.exists():
            try:
                data = load_jsonc(path)
                ok(f"{label}: valid")
                ekm = data.get("extraKnownMarketplaces", {})
                if ekm:
                    info(f"  {len(ekm)} marketplace(s) registered")
                ep = data.get("enabledPlugins", {})
                if ep:
                    enabled = sum(1 for v in ep.values() if v)
                    disabled = sum(1 for v in ep.values() if not v)
                    info(f"  {enabled} plugin(s) enabled, {disabled} disabled")
            except Exception as e:
                err(f"{label}: CORRUPT — {e}")
                issues += 1
        else:
            info(f"{label}: not present (this is OK)")

    # 3. Check marketplaces directory
    if not MARKETPLACES_DIR.exists():
        info("No local marketplaces directory yet.")
        print()
        return

    # Load settings once for all checks below
    settings = load_json_safe(SETTINGS_TARGET)

    # 4. Validate each marketplace
    for mp_dir in sorted(MARKETPLACES_DIR.iterdir()):
        if not mp_dir.is_dir():
            continue
        mp_name = mp_dir.name
        mp_json = mp_dir / ".claude-plugin" / "marketplace.json"

        print()
        print(f"  {BOLD}Marketplace: {mp_name}{NC}")

        if not mp_json.exists():
            err("  Missing marketplace.json")
            issues += 1
            continue

        try:
            mj = json.loads(mp_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            err(f"  marketplace.json: CORRUPT — {e}")
            issues += 1
            continue

        # Validate marketplace.json required structure
        mj_valid = True
        if not isinstance(mj, dict):
            err("  marketplace.json: must be a JSON object")
            issues += 1
            continue
        if not mj.get("name"):
            err("  marketplace.json: missing 'name' field")
            issues += 1
            mj_valid = False
        if not isinstance(mj.get("owner"), dict) or not mj["owner"].get("name"):
            err('  marketplace.json: missing or invalid \'owner\' field (must be {"name": "..."})')
            issues += 1
            mj_valid = False
        # metadata is optional per the Anthropic spec
        if not isinstance(mj.get("plugins"), list):
            err("  marketplace.json: 'plugins' must be an array")
            issues += 1
            mj_valid = False
        if mj_valid:
            ok("  marketplace.json: valid")

        # Validate individual plugin entries in marketplace.json
        for p_entry in mj.get("plugins", []):
            if not isinstance(p_entry, dict):
                warn("  marketplace.json: invalid plugin entry (not an object)")
                issues += 1
                continue
            if not p_entry.get("name"):
                warn("  marketplace.json: plugin entry missing 'name'")
                issues += 1
            p_src = p_entry.get("source")
            p_name_display = p_entry.get("name", "?")
            if not p_src:
                warn(f"  marketplace.json: plugin '{p_name_display}' missing 'source'")
                issues += 1
            elif isinstance(p_src, dict):
                src_type = p_src.get("source", "")
                if not src_type:
                    # Object sources (github, url, npm, pip, git-subdir) require a "source" type field
                    warn(f"  marketplace.json: plugin '{p_name_display}' source object missing 'source' type field")
                issues += 1
            # String sources like "./plugins/name" are valid relative paths per the Anthropic spec

        # Check if registered in extraKnownMarketplaces (informational only)
        # Marketplaces can also be loaded via '/plugin marketplace add' which stores them
        # internally — absence from extraKnownMarketplaces is NOT an error
        ekm = settings.get("extraKnownMarketplaces", {})
        if mp_name in ekm:
            registered_path = ekm[mp_name].get("source", {}).get("path", "")
            actual_path = _portable_path(mp_dir)
            if registered_path and registered_path != actual_path:
                warn(f"  Path mismatch in settings: registered='{registered_path}' actual='{actual_path}'")
                issues += 1
            else:
                ok("  Registered in settings")
        else:
            info("  Not in extraKnownMarketplaces (may be loaded via '/plugin marketplace add')")

        # Determine where plugins live based on marketplace.json source paths
        # Not all marketplaces use a plugins/ directory — some use ./ or other structures
        plugins_dir = mp_dir / "plugins"
        if not plugins_dir.exists():
            # Check if plugins are at the marketplace root (source: "./" pattern)
            has_root_plugins = any(
                isinstance(p.get("source"), str)
                and (p.get("source") == "./" or not p.get("source", "").startswith("./plugins/"))
                for p in mj.get("plugins", [])
                if isinstance(p, dict)
            )
            if has_root_plugins:
                # Plugins are at root or other locations — scan from marketplace root
                plugins_dir = mp_dir
            else:
                continue

        declared_plugins = {p.get("name") for p in mj.get("plugins", []) if isinstance(p, dict)}

        # Resolve actual plugin directories from marketplace.json source paths
        # rather than blindly scanning all subdirectories (avoids false positives
        # from .git, .claude-plugin, and other non-plugin directories)
        resolved_plugin_dirs = []
        for p_entry in mj.get("plugins", []):
            if not isinstance(p_entry, dict):
                continue
            p_src = p_entry.get("source")
            if isinstance(p_src, str):
                # Relative path source — resolve to actual directory
                resolved = (mp_dir / p_src).resolve()
                if resolved.is_dir():
                    resolved_plugin_dirs.append(resolved)
            # Object sources (github, npm, etc.) are fetched externally — skip

        # Deduplicate resolved dirs (multiple plugins can share a source like "./")
        seen_dirs = set()
        unique_plugin_dirs = []
        for d in sorted(resolved_plugin_dirs, key=lambda d: d.name):
            rd = d.resolve()
            if rd not in seen_dirs:
                seen_dirs.add(rd)
                unique_plugin_dirs.append(d)

        for plug_dir in unique_plugin_dirs:
            if not plug_dir.is_dir():
                continue
            # Skip marketplace root — plugins with source: "./" use strict:false
            # and define everything inline in marketplace.json
            if plug_dir.resolve() == mp_dir.resolve():
                continue

            pj = plug_dir / ".claude-plugin" / "plugin.json"
            if not pj.exists():
                # Plugin dir exists but has no manifest — may be a stub for external source
                # or an incomplete plugin. Only warn, don't count as issue.
                continue

            meta = read_plugin_meta(plug_dir)
            plugin_key = f"{meta['name']}@{mp_name}"

            # Quick validation — only run skill-audit in verbose mode (too slow for 200+ plugins)
            v_errors, v_warnings = validate_plugin(plug_dir, run_security_audit=verbose)
            status_parts = []
            if v_errors:
                status_parts.append(f"{RED}{len(v_errors)} error(s){NC}")
                issues += len(v_errors)
            if v_warnings:
                status_parts.append(f"{YELLOW}{len(v_warnings)} warning(s){NC}")
            if not v_errors and not v_warnings:
                status_parts.append(f"{GREEN}clean{NC}")

            # Check enabled status
            enabled = settings.get("enabledPlugins", {}).get(plugin_key)
            en_str = (
                f"{GREEN}enabled{NC}"
                if enabled
                else f"{YELLOW}disabled{NC}"
                if enabled is False
                else f"{CYAN}managed by Claude Code{NC}"
            )

            print(f"    {meta['name']} v{meta['version']}  [{en_str}]  [{', '.join(status_parts)}]")

            # Show full validation details in verbose mode
            if verbose and (v_errors or v_warnings):
                for ve in v_errors:
                    print(f"      {RED}ERROR:{NC} {ve}")
                for vw in v_warnings:
                    print(f"      {YELLOW}WARN:{NC}  {vw}")
                print()  # Blank line separator for readability between plugins

            # Check if declared in marketplace.json
            if meta["name"] not in declared_plugins:
                warn("    Not listed in marketplace.json — may not be discovered by Claude Code")
                issues += 1

    # 5. Check for orphaned entries in settings
    ekm = settings.get("extraKnownMarketplaces", {})
    for mp_name, mp_cfg in ekm.items():
        source = mp_cfg.get("source", {})
        if source.get("source") == "directory":
            mp_path = Path(source.get("path", ""))
            if not mp_path.exists():
                print()
                warn(f"Orphaned marketplace in settings: '{mp_name}' points to non-existent path: {mp_path}")
                issues += 1

    ep = settings.get("enabledPlugins", {})
    for pkey, enabled in ep.items():
        if "@" in pkey:
            pname, mpname = pkey.split("@", 1)
            # Check both marketplace dir and cache — GitHub-sourced plugins live in cache only
            plug_in_marketplace = MARKETPLACES_DIR / mpname / "plugins" / pname
            plug_in_cache = CACHE_DIR / mpname / pname
            if not plug_in_marketplace.exists() and not plug_in_cache.exists() and enabled:
                # Also check if the marketplace itself exists (could be managed externally)
                mp_exists = (MARKETPLACES_DIR / mpname).exists() or (CACHE_DIR / mpname).exists()
                if not mp_exists:
                    print()
                    warn(f"Orphaned entry in enabledPlugins: '{pkey}' — marketplace '{mpname}' not found")
                    issues += 1

    # Summary
    print()
    if issues == 0:
        ok("All checks passed — installation is healthy")
    else:
        warn(f"{issues} issue(s) found")
    print()


# ── Main ──────────────────────────────────────────────────

HELP_EPILOG = f"""\
{BOLD}install (default){NC}
  claude-plugin-install <source> <marketplace> [options]

  Install a plugin from an archive file (.tar.gz, .tgz, .zip, .tar.bz2,
  .tar.xz, .tar) or directly from a plugin directory. The source must
  contain a directory with a .claude-plugin/plugin.json manifest inside it.

  When installing from a directory, .git/ is always excluded and the
  plugin's .gitignore is respected (matching files are not copied).
  Validation also skips gitignored files.

  The plugin is copied into a local marketplace directory at:
    ~/.claude/plugins/marketplaces/<marketplace>/plugins/<name>/

  The marketplace is registered in settings.local.json so Claude Code
  discovers it on next restart. The marketplace name is required.

  Multiple plugins can share the same marketplace name to group them:
    claude-plugin-install plugin-a.tar.gz my-tools
    claude-plugin-install plugin-b.zip    my-tools

{BOLD}--validate <path>{NC}
  Validate a plugin without installing it. Accepts:
    - Archive file:       --validate my-plugin.tar.gz
    - Local directory:    --validate ./my-plugin/
    - Installed plugin:   --validate my-plugin@local-my-plugin

  Runs 30+ checks including:
    • plugin.json schema (name, version, component paths)
    • hooks.json deep validation (events, matchers, handler types)
    • Bash command analysis (missing interpreters, tilde, cd traps)
    • Agent/command/skill frontmatter (required fields, valid values)
    • SKILL.md size limits (500 lines / 5000 chars for discovery)
    • Script permissions and shebangs (cross-platform)
    • MCP and LSP configuration
    • Hook-referenced file existence

  Exit code: 0 = no errors (warnings OK), 1 = errors found.

{BOLD}--update <source> <marketplace>{NC}
  Update an installed plugin from a new archive or directory. The old
  version is fully uninstalled first, then the new version is installed
  into the same marketplace. The plugin must already be installed.

    claude-plugin-install --update my-plugin-v2.tar.gz my-marketplace
    claude-plugin-install --update ./my-plugin/ my-marketplace

{BOLD}--uninstall <plugin>@<marketplace>{NC}
  Remove an installed plugin and clean up settings. If the marketplace
  has no remaining plugins, it is also removed.

    claude-plugin-install --uninstall token-reporter@local-token-reporter

{BOLD}--enable <plugin>@<marketplace>{NC}
  Enable a previously disabled plugin. Plugins are enabled by default
  after installation.

    claude-plugin-install --enable my-plugin@my-marketplace

{BOLD}--disable <plugin>@<marketplace>{NC}
  Disable a plugin without removing its files. The plugin remains
  installed but will not be loaded by Claude Code.

    claude-plugin-install --disable my-plugin@my-marketplace

{BOLD}--list{NC}
  Show all plugins installed by this tool, with version, enabled/disabled
  status, and component summary (commands, agents, skills, hooks, MCP).

{BOLD}--doctor{NC}
  Health check for the entire plugin installation:
    • Validates settings.json and settings.local.json
    • Checks each marketplace is registered and its path matches
    • Runs validation on every installed plugin
    • Detects orphaned entries in settings (deleted plugins, missing paths)
    • Reports enabled/disabled status

{BOLD}options:{NC}
  -f, --force     Install even if validation fails (errors become warnings).
                  Also skips the overwrite confirmation prompt.
  -n, --dry-run   Show exactly what would happen without writing any files.
                  Useful for previewing before a real install.
  -q, --quiet     Suppress all non-error output and auto-confirm all prompts.
                  Useful for scripted/automated installs.
  -v, --verbose   Show full validation details for each plugin (use with --doctor).
  --version       Print version number and exit.

{BOLD}examples:{NC}
  # Basic install from archive
  claude-plugin-install my-plugin.tar.gz my-marketplace

  # Install from a local directory (respects .gitignore, excludes .git)
  claude-plugin-install ./my-plugin/ my-marketplace

  # Install into a shared marketplace
  claude-plugin-install my-plugin.tar.gz shared-tools

  # Update a plugin from a new version
  claude-plugin-install --update my-plugin-v2.tar.gz my-marketplace

  # Update a plugin from a local directory
  claude-plugin-install --update ./my-plugin/ my-marketplace

  # Overwrite an existing plugin (skip confirmation)
  claude-plugin-install my-plugin.tar.gz my-marketplace --force

  # Validate before distributing
  claude-plugin-install --validate ./my-plugin/

  # Re-validate an installed plugin after manual edits
  claude-plugin-install --validate my-plugin@local-my-plugin

  # Check everything is healthy
  claude-plugin-install --doctor

  # Health check with full validation details
  claude-plugin-install --doctor --verbose

  # Preview an install
  claude-plugin-install --dry-run my-plugin.tar.gz my-marketplace

  # Silent install (no output, auto-confirm)
  claude-plugin-install my-plugin.tar.gz my-marketplace --quiet

  # Disable a plugin (keep files, stop loading)
  claude-plugin-install --disable my-plugin@my-marketplace

  # Re-enable a disabled plugin
  claude-plugin-install --enable my-plugin@my-marketplace

  # Remove a plugin
  claude-plugin-install --uninstall my-plugin@local-my-plugin

{BOLD}plugin structure:{NC}
  my-plugin/
  ├── .claude-plugin/
  │   └── plugin.json        # manifest (name, version, description)
  ├── commands/               # slash commands (*.md)
  ├── agents/                 # subagent definitions (*.md)
  ├── skills/                 # skills (*/SKILL.md)
  │   └── my-skill/
  │       └── SKILL.md
  ├── hooks/
  │   └── hooks.json          # lifecycle hooks
  ├── scripts/                # supporting scripts
  ├── .mcp.json               # MCP server configuration
  ├── .lsp.json               # LSP server configuration
  └── settings.json           # plugin settings overrides

{BOLD}files modified:{NC}
  ~/.claude/settings.local.json           marketplace registration
  ~/.claude/plugins/marketplaces/         plugin files
  ~/.claude/plugins/installed_plugins.json  install tracking + backups
"""


def main():
    parser = argparse.ArgumentParser(
        prog="claude-plugin-install",
        description=(
            "Install, validate, and manage Claude Code plugins.\nCross-platform: macOS, Linux, and Windows. Python 3.8+, no dependencies."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "archive", nargs="?", help="Plugin source: archive (.tar.gz, .tgz, .zip, etc.) or directory path"
    )
    group.add_argument("--uninstall", metavar="NAME@MARKETPLACE", help="Remove a plugin and clean up settings")
    group.add_argument(
        "--validate", metavar="PATH", help="Validate an archive, directory, or installed plugin (name@marketplace)"
    )
    group.add_argument("--list", action="store_true", help="Show all plugins installed by this tool")
    group.add_argument("--doctor", action="store_true", help="Run health checks on all installed plugins and settings")
    group.add_argument(
        "--update",
        nargs=2,
        metavar=("SOURCE", "MARKETPLACE"),
        help="Update a plugin from a new archive or directory (uninstalls old, reinstalls)",
    )
    group.add_argument("--enable", metavar="NAME@MARKETPLACE", help="Enable a disabled plugin")
    group.add_argument("--disable", metavar="NAME@MARKETPLACE", help="Disable an installed plugin without removing it")

    parser.add_argument(
        "marketplace", nargs="?", default=None, help="Marketplace name to install into (required for install)"
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="Install despite validation errors; skip overwrite prompt"
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="Preview what would happen without writing any files"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress non-error output and auto-confirm all prompts"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show full validation details (use with --doctor)")

    args = parser.parse_args()

    if args.list:
        do_list()
    elif args.uninstall:
        do_uninstall(args.uninstall, quiet=args.quiet, dry_run=args.dry_run)
    elif args.update:
        do_update(args.update[0], args.update[1], force=args.force, dry_run=args.dry_run, quiet=args.quiet)
    elif args.enable:
        do_enable(args.enable, quiet=args.quiet, dry_run=args.dry_run)
    elif args.disable:
        do_disable(args.disable, quiet=args.quiet, dry_run=args.dry_run)
    elif args.validate:
        do_validate(args.validate)
    elif args.doctor:
        do_doctor(verbose=args.verbose)
    elif args.archive:
        do_install(args.archive, args.marketplace, force=args.force, dry_run=args.dry_run, quiet=args.quiet)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
