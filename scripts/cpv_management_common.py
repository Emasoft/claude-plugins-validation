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

import copy
import datetime
import json
import os
import platform
import shutil
import sys
import tarfile
import uuid
import zipfile
from pathlib import Path
from typing import IO, NoReturn

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


def unique_tmp_path(path: Path) -> Path:
    """Return a per-process-unique sibling temp path for an atomic write.

    A FIXED tmp name (``path.with_suffix(".tmp")``) lets two processes writing
    the SAME target file collide on the SAME tmp file — their writes interleave
    and the os.replace then promotes a corrupt mix to the target. Suffixing the
    tmp name with the PID and a random token gives every writer its own staging
    file, so concurrent writers of one target can no longer corrupt each other;
    os.replace stays atomic because the tmp is a sibling (same directory ->
    same filesystem). The name still ends in ``.tmp`` so existing cleanup globs
    and "no .tmp leftover" assertions keep working.
    """
    return path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")


def save_json_safe(path: Path, data: dict, dry_run: bool = False):
    """Atomically write JSON with backup. Cross-platform."""
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_file(path)

    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = unique_tmp_path(path)
    try:
        tmp.write_text(content, encoding="utf-8")
        # os.replace() (called by Path.replace()) is atomic on all platforms including Windows
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ── Archive extraction ────────────────────────────────────
#
# Resource limits (Codex adversarial review 2026-05-04, finding #3):
#
# Defaults are sized for LLM-development workloads — multi-shard
# safetensors checkpoints, GGUF models, HuggingFace datasets routinely
# hit the multi-GB / 100k-entry range and must extract cleanly. The
# numbers below are deliberately generous so legitimate ML artefacts
# never trip the gate.
#
# The PRIMARY zip-bomb defense is `max_compression_ratio` (200x):
#   * Model weights (random-looking floats):   ratio ≈ 1-2x
#   * Pre-compressed datasets (jsonl/parquet): ratio ≈ 1-3x
#   * Source code:                             ratio ≈ 5-10x
#   * Highly compressible text:                ratio ≈ 30-50x
#   * Pathological zip bombs:                  ratio ≈ 1,000-1,000,000x
# 200x sits well above any legitimate content yet catches every real bomb.
#
# Every limit is overridable via env var; set to a higher integer when
# you genuinely need more room. Unset / empty / non-integer → default.
DEFAULT_ARCHIVE_MAX_BYTES = 200 * 1024**3  # 200 GB total uncompressed
DEFAULT_ARCHIVE_MAX_PER_FILE_BYTES = 50 * 1024**3  # 50 GB per file
DEFAULT_ARCHIVE_MAX_ENTRIES = 100_000  # 100k entries
DEFAULT_ARCHIVE_MAX_RATIO = 200  # 200x compression
DEFAULT_ARCHIVE_MAX_NESTING = 32  # 32 path components


def _archive_limits() -> dict[str, int]:
    """Read archive extraction quotas from env vars (with LLM-friendly defaults).

    Override via:
      CPV_ARCHIVE_MAX_BYTES           — total uncompressed bytes
      CPV_ARCHIVE_MAX_PER_FILE_BYTES  — per-entry uncompressed bytes
      CPV_ARCHIVE_MAX_ENTRIES         — entry count
      CPV_ARCHIVE_MAX_RATIO           — uncompressed/compressed ratio
      CPV_ARCHIVE_MAX_NESTING         — path component depth
    """

    def _int_env(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
            return value if value > 0 else default
        except ValueError:
            return default

    return {
        "max_bytes": _int_env("CPV_ARCHIVE_MAX_BYTES", DEFAULT_ARCHIVE_MAX_BYTES),
        "max_per_file_bytes": _int_env("CPV_ARCHIVE_MAX_PER_FILE_BYTES", DEFAULT_ARCHIVE_MAX_PER_FILE_BYTES),
        "max_entries": _int_env("CPV_ARCHIVE_MAX_ENTRIES", DEFAULT_ARCHIVE_MAX_ENTRIES),
        "max_ratio": _int_env("CPV_ARCHIVE_MAX_RATIO", DEFAULT_ARCHIVE_MAX_RATIO),
        "max_nesting": _int_env("CPV_ARCHIVE_MAX_NESTING", DEFAULT_ARCHIVE_MAX_NESTING),
    }


def _abort_archive(dest: Path, archive: Path, reason: str) -> NoReturn:
    """Print the quota violation, clean up partial extraction, exit non-zero.

    Callers of `extract_archive` always pass a fresh dest (per-call tmp dir
    or staging tree they own), so wiping `dest` on abort is safe and avoids
    leaving a half-extracted tree pretending to be valid.
    """
    err(f"Archive '{archive.name}' refused: {reason}")
    if dest.exists():
        err(f"  Cleaning up partial extraction at {dest}")
        try:
            shutil.rmtree(dest, ignore_errors=True)
        except OSError:
            pass
    sys.exit(1)


def _path_nesting(member_path: str) -> int:
    """Count path components (handles both POSIX and Windows separators)."""
    cleaned = member_path.replace("\\", "/").strip("/")
    if not cleaned:
        return 0
    return cleaned.count("/") + 1


EXTRACT_CHUNK_BYTES = 1024 * 1024


def _stream_entry(
    src: IO[bytes],
    target: Path,
    entry_name: str,
    written: int,
    limits: dict[str, int],
) -> tuple[int, str | None]:
    """Copy one member to `target` in bounded chunks, counting the REAL bytes.

    An archive header is attacker-controlled, so the declared size is only a
    cheap preflight hint; this running count is what actually enforces the
    quota. Returns the new aggregate total and a quota-violation reason (None
    when the member fit). The overflowing chunk is never written.
    """
    per_file = 0
    with open(target, "wb") as out:
        while True:
            chunk = src.read(EXTRACT_CHUNK_BYTES)
            if not chunk:
                break
            per_file += len(chunk)
            written += len(chunk)
            if per_file > limits["max_per_file_bytes"]:
                return written, (
                    f"entry '{entry_name}' expands past {limits['max_per_file_bytes']:,} bytes "
                    f"(header under-declared its size); override with CPV_ARCHIVE_MAX_PER_FILE_BYTES"
                )
            if written > limits["max_bytes"]:
                return written, (
                    f"archive expands past {limits['max_bytes']:,} bytes while extracting "
                    f"'{entry_name}' (headers under-declared their sizes); "
                    f"override with CPV_ARCHIVE_MAX_BYTES"
                )
            out.write(chunk)
    return written, None


def _write_member(
    src: IO[bytes],
    target: Path,
    entry_name: str,
    written: int,
    limits: dict[str, int],
    dest: Path,
    archive: Path,
) -> int:
    """Stream one member into place, aborting (and removing the partial) on quota."""
    target.parent.mkdir(parents=True, exist_ok=True)
    written, quota_error = _stream_entry(src, target, entry_name, written, limits)
    if quota_error:
        target.unlink(missing_ok=True)
        _abort_archive(dest, archive, quota_error)
    return written


def extract_archive(archive_path: str, dest: Path):
    """Extract .tar.gz/.tgz/.zip/.tar.bz2/.tar.xz to dest directory.

    Enforces resource quotas (zip-bomb defense, sized for LLM-dev). See
    `_archive_limits` for the env-var overrides.
    """
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
    """Extract a zip archive with path traversal AND quota enforcement.

    Extracts members individually after validation to prevent TOCTOU issues.
    The declared-size quotas (entry count, total size, compression ratio,
    per-file size, nesting depth) run as a cheap preflight, but the central
    directory is attacker-controlled, so each member is then streamed in
    bounded chunks with a running byte counter that enforces the real quota
    on the bytes actually written.
    """
    limits = _archive_limits()
    archive_size = archive.stat().st_size
    written = 0

    with zipfile.ZipFile(archive, "r") as zf:
        infos = zf.infolist()

        # Preflight quotas that depend on the whole-archive aggregate.
        if len(infos) > limits["max_entries"]:
            _abort_archive(
                dest,
                archive,
                f"too many entries ({len(infos):,} > {limits['max_entries']:,}); override with CPV_ARCHIVE_MAX_ENTRIES",
            )
        total = sum(int(getattr(i, "file_size", 0)) for i in infos)
        if total > limits["max_bytes"]:
            _abort_archive(
                dest,
                archive,
                f"total uncompressed {total:,} bytes > {limits['max_bytes']:,}; override with CPV_ARCHIVE_MAX_BYTES",
            )
        if archive_size > 0 and total > limits["max_ratio"] * archive_size:
            _abort_archive(
                dest,
                archive,
                f"compression ratio {total // max(archive_size, 1)}x > "
                f"{limits['max_ratio']}x (likely a zip bomb); "
                f"override with CPV_ARCHIVE_MAX_RATIO",
            )

        # Append os.sep so /tmp/abc doesn't match /tmp/abcdef (traversal bypass)
        dest_resolved = str(dest.resolve()) + os.sep

        for info in infos:
            # Per-entry quotas
            if info.file_size > limits["max_per_file_bytes"]:
                _abort_archive(
                    dest,
                    archive,
                    f"entry '{info.filename}' size {info.file_size:,} bytes > "
                    f"{limits['max_per_file_bytes']:,}; "
                    f"override with CPV_ARCHIVE_MAX_PER_FILE_BYTES",
                )
            depth = _path_nesting(info.filename)
            if depth > limits["max_nesting"]:
                _abort_archive(
                    dest,
                    archive,
                    f"entry '{info.filename}' nests {depth} levels > "
                    f"{limits['max_nesting']}; override with CPV_ARCHIVE_MAX_NESTING",
                )
            # Path-traversal checks (unchanged)
            member_path = os.path.normpath(info.filename)
            if member_path.startswith("..") or os.path.isabs(member_path):
                # Route through _abort_archive, not a bare sys.exit: a refused
                # archive must not leave a half-extracted tree behind that a
                # later step could mistake for a valid extraction. The quota
                # aborts already did this; the traversal aborts did not.
                _abort_archive(dest, archive, f"path-traversal entry: {info.filename}")
            target = (dest / member_path).resolve()
            if not (str(target) + os.sep).startswith(dest_resolved):
                # Route through _abort_archive, not a bare sys.exit: a refused
                # archive must not leave a half-extracted tree behind that a
                # later step could mistake for a valid extraction. The quota
                # aborts already did this; the traversal aborts did not.
                _abort_archive(dest, archive, f"path-traversal entry: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            # zipfile stops reading a member at its DECLARED file_size, so a
            # header claiming 16 bytes hides the rest of the inflated stream
            # from the counter below. Read against our own cap instead (on a
            # copy, so the ZipFile's own records stay untouched) and let the
            # running count decide where extraction really stops.
            member = copy.copy(info)
            member.file_size = limits["max_per_file_bytes"] + 1
            try:
                with zf.open(member) as src:
                    written = _write_member(src, target, info.filename, written, limits, dest, archive)
            except zipfile.BadZipFile:
                # ZipExtFile validates the CRC once its internal `_left`
                # counter (seeded from our deliberately-lowered file_size)
                # hits zero — which only happens when the REAL inflated
                # stream is longer than our declared override, i.e. the
                # entry is already over max_per_file_bytes. The CRC check
                # then compares our truncated read against the CRC for the
                # FULL (longer) stream and always mismatches. That mismatch
                # IS the quota signal here, not corruption — translate it to
                # the same abort a caller sees from every other quota check.
                target.unlink(missing_ok=True)
                _abort_archive(
                    dest,
                    archive,
                    f"entry '{info.filename}' expands past {limits['max_per_file_bytes']:,} bytes "
                    f"(header under-declared its size); override with CPV_ARCHIVE_MAX_PER_FILE_BYTES",
                )


def _extract_tar(archive: Path, dest: Path, mode: str):
    """Extract a tar archive with security filtering AND quota enforcement.

    Quota preflight uses tarfile's getmembers() (which streams headers for
    compressed tars without decompressing the data) as a cheap early reject.
    But a tar header is attacker-controlled, same as a zip central directory,
    so a member under-reporting its `size` would let unbounded data pass the
    preflight while `extractall`/`extract` write it in full. Regular-file
    members are therefore streamed ourselves via `tf.extractfile()` in
    bounded chunks with a running byte counter (see `_stream_entry`), which
    is what actually enforces the quota; declared size is only the fast path
    for an honest archive. Non-file members (dirs/symlinks/hardlinks/etc.)
    carry no data blocks worth streaming and go through tarfile's own safe
    extraction (filter="data" on 3.12+, the manual traversal checks below on
    older Python).
    """
    limits = _archive_limits()
    archive_size = archive.stat().st_size
    written = 0

    with tarfile.open(archive, mode) as tf:  # type: ignore[call-overload]
        members = tf.getmembers()

        # Preflight aggregate quotas
        if len(members) > limits["max_entries"]:
            _abort_archive(
                dest,
                archive,
                f"too many entries ({len(members):,} > {limits['max_entries']:,}); "
                f"override with CPV_ARCHIVE_MAX_ENTRIES",
            )
        total = sum(int(getattr(m, "size", 0)) for m in members if m.isfile())
        if total > limits["max_bytes"]:
            _abort_archive(
                dest,
                archive,
                f"total uncompressed {total:,} bytes > {limits['max_bytes']:,}; override with CPV_ARCHIVE_MAX_BYTES",
            )
        if archive_size > 0 and total > limits["max_ratio"] * archive_size:
            _abort_archive(
                dest,
                archive,
                f"compression ratio {total // max(archive_size, 1)}x > "
                f"{limits['max_ratio']}x (likely a tar bomb); "
                f"override with CPV_ARCHIVE_MAX_RATIO",
            )

        # Per-entry preflight (size + nesting). Path-traversal is handled
        # below either by tarfile filter="data" (3.12+) or the manual loop.
        for member in members:
            if member.isfile() and member.size > limits["max_per_file_bytes"]:
                _abort_archive(
                    dest,
                    archive,
                    f"entry '{member.name}' size {member.size:,} bytes > "
                    f"{limits['max_per_file_bytes']:,}; "
                    f"override with CPV_ARCHIVE_MAX_PER_FILE_BYTES",
                )
            depth = _path_nesting(member.name)
            if depth > limits["max_nesting"]:
                _abort_archive(
                    dest,
                    archive,
                    f"entry '{member.name}' nests {depth} levels > "
                    f"{limits['max_nesting']}; override with CPV_ARCHIVE_MAX_NESTING",
                )

        # Append os.sep so /tmp/abc doesn't match /tmp/abcdef (path traversal bypass)
        dest_resolved = str(dest.resolve()) + os.sep

        for member in members:
            # Path-traversal checks apply to every member regardless of type
            # or Python version — this is defense-in-depth alongside 3.12+'s
            # own filter="data", and it is the ONLY traversal check on <3.12.
            member_path = os.path.normpath(member.name)
            if member_path.startswith("..") or os.path.isabs(member_path):
                _abort_archive(dest, archive, f"path-traversal entry: {member.name}")
            if PYTHON_VERSION < (3, 12):
                # Block symlinks pointing outside dest (3.12+'s filter="data"
                # covers this on the tf.extract() call below instead).
                if member.issym() or member.islnk():
                    link_target = os.path.normpath(os.path.join(os.path.dirname(member.name), member.linkname))
                    if link_target.startswith("..") or os.path.isabs(link_target):
                        _abort_archive(
                            dest,
                            archive,
                            f"symlink escaping archive: {member.name} -> {member.linkname}",
                        )
            target = (dest / member_path).resolve()
            if not (str(target) + os.sep).startswith(dest_resolved):
                _abort_archive(dest, archive, f"path-traversal entry: {member.name}")

            if member.isfile():
                # tarfile.extractfile() hands us the raw member stream without
                # truncating it to the declared `size` — read it ourselves in
                # bounded chunks so the running counter (not the header) is
                # what decides where extraction stops.
                try:
                    src = tf.extractfile(member)
                except (OSError, tarfile.TarError) as exc:
                    _abort_archive(dest, archive, f"cannot read entry '{member.name}': {exc}")
                if src is None:
                    continue
                with src:
                    written = _write_member(src, target, member.name, written, limits, dest, archive)
            elif PYTHON_VERSION >= (3, 12):
                # extract with filter="data" is safe against path traversal (Python 3.12+)
                # filter="data" raises a tarfile.FilterError subclass
                # (OutsideDestinationError / AbsoluteLinkError / SpecialFileError / …)
                # on a malicious entry. Catch it so an unsafe tar fails IDENTICALLY to
                # the ZIP path and the pre-3.12 manual loop — clean message + cleanup +
                # exit 1 — instead of a raw traceback that leaves a partial tree behind.
                try:
                    tf.extract(member, dest, filter="data")
                except tarfile.FilterError as exc:
                    _abort_archive(dest, archive, f"unsafe tar entry: {exc}")
            else:
                # Non-file member (dir/symlink/hardlink/etc.) on pre-3.12:
                # already traversal-checked above; no data blocks to stream.
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


# ── Marker-block helpers (Phase 5: README auto-maintenance) ───────────────


# Module-private regex helper. Imported into refresh_readme.py via
# `from cpv_management_common import _re_marker` for the --check path
# that needs to inspect the file directly without rewriting.
import re as _re_marker  # noqa: E402  (intentional: section-local import)


def replace_marker_block(
    file_path: Path,
    marker_id: str,
    new_content: str,
    *,
    create_if_missing: bool = False,
) -> tuple[bool, str]:
    """Replace text between `<!-- BEGIN AUTO-{marker_id} -->` and
    `<!-- END AUTO-{marker_id} -->` with `new_content`. Idempotent.

    The marker comments are preserved; only the body between them is
    rewritten. This pattern lets agents auto-refresh sections of a README
    without clobbering the user's surrounding prose.

    Returns (changed, status):
      * (True, "updated") — markers found, content differed, file rewritten.
      * (False, "unchanged") — markers found, content already matched.
      * (False, "missing") — markers not present and create_if_missing=False.
      * (True, "appended") — markers not present, create_if_missing=True,
        block appended to end of file.

    Raises FileNotFoundError if file_path does not exist (unless
    create_if_missing=True, in which case the file is created).
    """
    begin_marker = f"<!-- BEGIN AUTO-{marker_id} -->"
    end_marker = f"<!-- END AUTO-{marker_id} -->"
    new_block = f"{begin_marker}\n{new_content.rstrip()}\n{end_marker}\n"

    if not file_path.exists():
        if create_if_missing:
            file_path.write_text(new_block, encoding="utf-8")
            return True, "created"
        raise FileNotFoundError(file_path)

    text = file_path.read_text(encoding="utf-8")
    pattern = _re_marker.compile(
        f"{_re_marker.escape(begin_marker)}.*?{_re_marker.escape(end_marker)}\n?",
        flags=_re_marker.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        if not create_if_missing:
            return False, "missing"
        # Append the block at the end of the file with a leading blank line.
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + new_block
        file_path.write_text(text, encoding="utf-8")
        return True, "appended"

    # Compare existing body to the new body. Strip whitespace differences
    # so trivial reformats don't trigger a rewrite.
    existing = match.group(0)
    if existing.strip() == new_block.strip():
        return False, "unchanged"
    new_text = text[: match.start()] + new_block + text[match.end() :]
    file_path.write_text(new_text, encoding="utf-8")
    return True, "updated"


def detect_components(plugin_root: Path) -> dict[str, list[str]]:
    """Return a per-component-folder list of names found in plugin_root.

    Conservatively classifies files in known component dirs:
      agents/   → agent name (basename without .md)
      skills/   → skill name (each subdir with SKILL.md)
      commands/ → command name (basename without .md)
      hooks/    → ["hooks.json"] if present (hooks themselves are in JSON)

    Returns {component_dir: [name1, name2, ...]} sorted alphabetically.
    Empty / missing dirs are omitted from the dict.
    """
    out: dict[str, list[str]] = {}
    agents = plugin_root / "agents"
    if agents.is_dir():
        names = sorted(p.stem for p in agents.glob("*.md") if p.is_file())
        if names:
            out["agents"] = names
    skills = plugin_root / "skills"
    if skills.is_dir():
        names = sorted(p.name for p in skills.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())
        if names:
            out["skills"] = names
    commands = plugin_root / "commands"
    if commands.is_dir():
        names = sorted(p.stem for p in commands.glob("*.md") if p.is_file())
        if names:
            out["commands"] = names
    hooks_json = plugin_root / "hooks" / "hooks.json"
    if hooks_json.is_file():
        out["hooks"] = ["hooks.json"]
    mcp_json = plugin_root / ".mcp.json"
    if mcp_json.is_file():
        out["mcpServers"] = [".mcp.json"]
    return out


def render_components_table(components: dict[str, list[str]]) -> str:
    """Render the auto-detected components as a markdown table.

    Empty input → returns a single line ("(no components detected)").
    """
    if not components:
        return "_(no components detected — add files to agents/, skills/, commands/, hooks/, or .mcp.json)_"
    lines = [
        "| Component | Count | Names |",
        "|---|---:|---|",
    ]
    for comp_dir, names in sorted(components.items()):
        names_str = ", ".join(f"`{n}`" for n in names)
        lines.append(f"| `{comp_dir}/` | {len(names)} | {names_str} |")
    return "\n".join(lines)
