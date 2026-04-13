#!/usr/bin/env python3
"""Shared utilities for Claude Code plugin management operations.

Provides common infrastructure used by all manage_*.py modules:
- Claude Code directory paths and settings file locations
- JSONC parsing (JSON with comments and trailing commas)
- Safe atomic JSON file I/O with timestamped backups
- Archive extraction with path traversal prevention
- Cross-platform support (macOS, Linux, Windows)
- Colored terminal output helpers
"""

import datetime
import json
import os
import platform
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

__all__ = [
    "IS_WINDOWS",
    "PYTHON_VERSION",
    "TOOL_VERSION",
    "CLAUDE_DIR",
    "PLUGINS_DIR",
    "MARKETPLACES_DIR",
    "CACHE_DIR",
    "SETTINGS_FILE",
    "INSTALLED_FILE",
    "KNOWN_MARKETPLACES_FILE",
    "SETTINGS_TARGET",
    "C",
    "RED",
    "GREEN",
    "YELLOW",
    "CYAN",
    "BOLD",
    "NC",
    "ok",
    "info",
    "warn",
    "err",
    "strip_jsonc_comments",
    "strip_trailing_commas",
    "load_jsonc",
    "backup_file",
    "load_json_safe",
    "save_json_safe",
    "extract_archive",
    "_validate_safe_name",
]

IS_WINDOWS = platform.system() == "Windows"
PYTHON_VERSION = sys.version_info
TOOL_VERSION = "2.1.0"


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
INSTALLED_FILE = PLUGINS_DIR / "installed_plugins.json"
# Claude Code internal marketplace registry — must be cleaned on uninstall/doctor
KNOWN_MARKETPLACES_FILE = PLUGINS_DIR / "known_marketplaces.json"

# All plugin/marketplace operations write to ~/.claude/settings.json (user-level).
# ~/.claude/settings.local.json is NOT used — it only applies if Claude Code
# is launched from ~/ which is not a valid project directory.
SETTINGS_TARGET = SETTINGS_FILE


# ── Colors ────────────────────────────────────────────────


def _enable_ansi_windows():
    """Enable ANSI escape codes on Windows 10+ terminals."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]
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
    """Create a timestamped backup of a file before modifying it.
    Backups are stored in ~/.claude/backups/ to match Claude Code 2.1.47+ convention."""
    if not path.exists():
        return
    # Use ~/.claude/backups/ directory (aligned with Claude Code 2.1.47+)
    backup_dir = CLAUDE_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = backup_dir / f"{path.name}.{ts}.bak"
    shutil.copy2(path, backup)
    # Restrict backup permissions to owner-only on non-Windows
    if not IS_WINDOWS:
        try:
            backup.chmod(0o600)
        except OSError:
            pass

    # Keep only the 5 most recent backups per original filename
    pattern = f"{path.name}.*.bak"

    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    backups = sorted(backup_dir.glob(pattern), key=_safe_mtime)
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
        err(f"Unsupported archive format: {''.join(archive.suffixes) or archive.name}")
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
    with tarfile.open(archive, mode) as tf:  # type: ignore[call-overload]
        if PYTHON_VERSION >= (3, 12):
            # extractall with filter="data" is safe against path traversal (Python 3.12+)
            # NOTE: filter kwarg only works on extractall(), NOT on extract()
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


# ── Name validation ───────────────────────────────────────


def _validate_safe_name(name: str, label: str) -> str:
    """Validate that a plugin or marketplace name is safe for use in file paths.
    Rejects path traversal, path separators, and control characters."""
    if not name:
        err(f"Empty {label} name.")
        sys.exit(1)
    if ".." in name or "/" in name or "\\" in name or "\0" in name:
        err(f"Invalid {label} name: '{name}' — must not contain path separators or '..'")
        sys.exit(1)
    if name.startswith(".") or name.startswith("-"):
        err(f"Invalid {label} name: '{name}' — must not start with '.' or '-'")
        sys.exit(1)
    return name
