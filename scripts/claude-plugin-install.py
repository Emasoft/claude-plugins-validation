#!/usr/bin/env python3
"""
claude-plugin-install — Install, validate, and manage Claude Code plugins.

Wraps plugins into local marketplaces and registers them in Claude Code's
runtime registry (known_marketplaces.json) and settings.json. Includes deep
validation of hooks schemas, frontmatter, scripts, and MCP configs.

Cross-platform: works on macOS, Linux, and Windows.
Requires: Python 3.12+, no external dependencies.
"""

import argparse
import datetime
import json
import os
import platform
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal

IS_WINDOWS = platform.system() == "Windows"
PYTHON_VERSION = sys.version_info
TOOL_VERSION = "1.7.9"


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
SETTINGS_FILE = CLAUDE_DIR / "settings.json"
INSTALLED_FILE = PLUGINS_DIR / "installed_plugins.json"

# This is the file Claude Code ACTUALLY reads at runtime to discover marketplaces.
# extraKnownMarketplaces in settings.json is only processed during the interactive
# trust dialog — it does NOT get read by plugin commands or at session start.
# See: https://gist.github.com/alexey-pelykh/566a4e5160b305db703d543312a1e686
KNOWN_MARKETPLACES_FILE = PLUGINS_DIR / "known_marketplaces.json"

# enabledPlugins MUST go in ~/.claude/settings.json (the global user one).
# Writing it to settings.local.json has no effect unless the key also exists
# in settings.json — a known Claude Code bug.
# See: https://github.com/anthropics/claude-code/issues/27247
# See: https://github.com/anthropics/claude-code/issues/17832
ENABLED_PLUGINS_FILE = SETTINGS_FILE


# ── Colors ────────────────────────────────────────────────


def _enable_ansi_windows():
    """Enable ANSI escape codes on Windows 10+ terminals."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]  # Windows-only attribute
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
    """Remove trailing commas before } or ] (common JSONC pattern)."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def load_jsonc(path: Path) -> dict[str, Any]:
    """Load a JSONC file (JSON with comments and trailing commas)."""
    text = path.read_text(encoding="utf-8")
    cleaned = strip_trailing_commas(strip_jsonc_comments(text))
    result: dict[str, Any] = json.loads(cleaned)
    return result


# ── Safe JSON file operations ─────────────────────────────


def backup_file(path: Path):
    """Create a timestamped backup of a file before modifying it."""
    if not path.exists():
        return
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.parent / f"{path.name}.{ts}.bak"
    shutil.copy2(path, backup)

    # Keep only the 5 most recent backups
    pattern = f"{path.name}.*.bak"
    backups = sorted(path.parent.glob(pattern), key=lambda p: p.stat().st_mtime)
    for old in backups[:-5]:
        old.unlink(missing_ok=True)


def load_json_safe(path: Path) -> dict[str, Any]:
    """Load a JSON/JSONC file safely, returning {} if missing or corrupt."""
    if not path.exists():
        return {}
    try:
        return load_jsonc(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        warn(f"Could not parse {path}: {e}")
        warn("The file may be corrupt. A backup will be created before any changes.")
        try:
            result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return result
        except Exception:
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
        if IS_WINDOWS and path.exists():
            # Windows: Path.replace() fails if target exists
            path.unlink()
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
    """Extract a zip archive with path traversal prevention."""
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            member_path = os.path.normpath(info.filename)
            if member_path.startswith("..") or os.path.isabs(member_path):
                err(f"Refusing to extract path-traversal entry: {info.filename}")
                sys.exit(1)
            # Check that the resolved path stays within dest
            target = (dest / member_path).resolve()
            if not str(target).startswith(str(dest.resolve())):
                err(f"Refusing to extract path-traversal entry: {info.filename}")
                sys.exit(1)
        zf.extractall(dest)


def _extract_tar(archive: Path, dest: Path, mode: Literal["r:gz", "r:bz2", "r:xz", "r:"]) -> None:
    """Extract a tar archive with security filtering."""
    with tarfile.open(str(archive), mode) as tf:
        if PYTHON_VERSION >= (3, 12):
            tf.extractall(dest, filter="data")
        else:
            # Manual path-traversal and symlink prevention for older Python
            dest_resolved = str(dest.resolve())
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
                if not str(target).startswith(dest_resolved):
                    err(f"Refusing to extract path-traversal entry: {member.name}")
                    sys.exit(1)
            tf.extractall(dest)


def find_plugin_root(search_dir: Path) -> Path | None:
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


def _find_all_scripts(plugin_dir: Path) -> list[Path]:
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
    "TodoRead",
    "TodoWrite",
}
NOTIFICATION_MATCHERS = {"permission_prompt", "idle_prompt", "auth_success", "elicitation_dialog"}
SESSION_START_MATCHERS = {"startup", "resume", "clear", "compact"}
PRECOMPACT_MATCHERS = {"manual", "auto"}
NO_MATCHER_EVENTS = {"UserPromptSubmit", "Stop", "SubagentStop", "SubagentStart", "SessionEnd"}
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


def _fuzzy_match_event(wrong_name: str) -> str | None:
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


def _validate_matcher(matcher: str, event_name: str, path: str) -> list[str]:
    warnings: list[str] = []
    if not matcher or matcher == "*":
        return warnings

    if event_name in NO_MATCHER_EVENTS:
        warnings.append(
            f"{path}: '{event_name}' does not use matchers — "
            f"the matcher '{matcher}' will be ignored. Remove it or omit the matcher field."
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
                        f"{path}: matcher '{part}' doesn't match any known tool. "
                        f"Known tools: {', '.join(sorted(KNOWN_TOOL_MATCHERS))}. "
                        f"MCP tools use pattern: mcp__<server>__<tool>"
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


def _validate_bash_command(cmd: str, path: str, plugin_root: Path | None = None) -> tuple[list[str], list[str]]:
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
                    f"{path}: command runs '{first_token}' without an interpreter. "
                    f"Add one of: {' / '.join(interpreters)} "
                    f"(e.g. '{interpreters[0]} {stripped}'){note}"
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


def _validate_hooks_structure(hooks_data: dict, source_file: str, plugin_root: Path | None = None):
    errors = []
    warnings = []

    if "hooks" in hooks_data and isinstance(hooks_data.get("hooks"), dict):
        hooks_obj = hooks_data["hooks"]
    else:
        hooks_obj = hooks_data

    for event_name, event_value in hooks_obj.items():
        if event_name in ("hooks", "description"):
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
                f"{source_file}: '{event_name}' must be an array of matcher groups, "
                f"got {type(event_value).__name__}. "
                f'Correct: "{event_name}": [{{"hooks": [...]}}]'
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


def _parse_simple_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
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
) -> tuple[list[str], list[str]]:
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
                f"{label}: missing YAML frontmatter. Agents should start with:\n"
                f"           ---\n"
                f"           name: my-agent\n"
                f"           description: What this agent does\n"
                f"           ---"
            )
        elif component_type == "skill":
            warnings.append(
                f"{label}: missing YAML frontmatter. Skills should start with:\n"
                f"           ---\n"
                f"           description: What this skill does and when to use it\n"
                f"           ---"
            )
        return errors, warnings

    fm, _body = parsed
    del _body  # only frontmatter is validated

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
                        f"{label}: missing 'description' in frontmatter — "
                        f"Claude Code uses this to decide when to invoke the agent"
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
                f"{label}: frontmatter present but no 'description' — "
                f"add one so it shows in autocomplete when users type '/'"
            )

        # ── Unknown fields ──
        for key in fm:
            if key not in COMMAND_KNOWN_FIELDS:
                warnings.append(
                    f"{label}: unknown frontmatter field '{key}'. "
                    f"Command fields: {', '.join(sorted(COMMAND_KNOWN_FIELDS))}"
                )

        # ── Model validation ──
        model_val = fm.get("model", "").lower()
        if model_val and model_val not in AGENT_VALID_MODELS:
            warnings.append(f"{label}: model '{fm['model']}' — known values: {', '.join(sorted(AGENT_VALID_MODELS))}")

    elif component_type == "skill":
        # ── Recommended field ──
        if "description" not in fm:
            warnings.append(
                f"{label}: missing 'description' — Claude uses this for auto-discovery. "
                f"Without it, Claude falls back to the first paragraph of the content."
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
        total_lines = len(text.splitlines())
        total_chars = len(text)

        if total_lines > SKILL_MAX_LINES:
            warnings.append(
                f"{label}: {total_lines} lines exceeds the {SKILL_MAX_LINES}-line limit. "
                f"Progressive discovery won't work for this skill."
            )
        if total_chars > SKILL_MAX_CHARS:
            warnings.append(
                f"{label}: {total_chars} chars exceeds the {SKILL_MAX_CHARS}-char limit. "
                f"Progressive discovery won't work for this skill."
            )
        elif total_lines > SKILL_MAX_LINES * 0.8 or total_chars > SKILL_MAX_CHARS * 0.8:
            warnings.append(
                f"{label}: {total_lines} lines / {total_chars} chars — "
                f"approaching the limit ({SKILL_MAX_LINES} lines / {SKILL_MAX_CHARS} chars). "
                f"Consider trimming to stay within progressive discovery limits."
            )

    return errors, warnings


def validate_plugin(plugin_root: Path) -> tuple[list[str], list[str]]:
    """Validate a plugin directory. Returns (errors, warnings)."""
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

    for field, expected_type in [
        ("author", dict),
        ("keywords", list),
        ("homepage", str),
        ("repository", str),
        ("license", str),
    ]:
        val = manifest.get(field)
        if val is not None and not isinstance(val, expected_type):
            warnings.append(f"plugin.json: '{field}' should be {expected_type.__name__}, got {type(val).__name__}")

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
        cmd_files = list(commands_dir.rglob("*.md"))
        if not cmd_files:
            warnings.append("commands/ directory exists but contains no .md files")
        else:
            for md in cmd_files:
                fm_errs, fm_warns = _validate_markdown_frontmatter(md, "command")
                errors.extend(fm_errs)
                warnings.extend(fm_warns)

    skills_dir = plugin_root / "skills"
    if skills_dir.exists() and skills_dir.is_dir():
        skill_mds = list(skills_dir.rglob("SKILL.md"))
        if not skill_mds:
            warnings.append("skills/ directory exists but contains no SKILL.md files")
        else:
            for md in skill_mds:
                # Build a relative path like "skills/code-review/SKILL.md"
                rel = str(md.relative_to(plugin_root))
                fm_errs, fm_warns = _validate_markdown_frontmatter(
                    md, "skill", rel_prefix=str(md.parent.relative_to(plugin_root))
                )
                errors.extend(fm_errs)
                warnings.extend(fm_warns)

    agents_dir = plugin_root / "agents"
    if agents_dir.exists() and agents_dir.is_dir():
        agent_mds = list(agents_dir.rglob("*.md"))
        if not agent_mds:
            warnings.append("agents/ directory exists but contains no .md files")
        else:
            for md in agent_mds:
                fm_errs, fm_warns = _validate_markdown_frontmatter(md, "agent")
                errors.extend(fm_errs)
                warnings.extend(fm_warns)

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
                        f"settings.json: key '{key}' is not a recognized plugin setting. "
                        f"Currently supported: {', '.join(sorted(supported_keys))}"
                    )

    # ── 11. Check plugin has actual content ──────────────────

    has_content = any(
        (plugin_root / d).exists()
        for d in ("commands", "skills", "agents", "hooks", "scripts", ".mcp.json", ".lsp.json")
    )
    if not has_content:
        warnings.append("Plugin has a manifest but no commands, skills, agents, hooks, MCP, or LSP config")

    return errors, warnings


def print_validation_report(errors, warnings, _plugin_name):
    del _plugin_name  # reserved for future use in report header
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


def do_install(archive_path: str, marketplace_name: str | None, force: bool = False, dry_run: bool = False):
    if dry_run:
        info("DRY RUN — no files will be modified")

    info("Extracting archive...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        extract_archive(archive_path, tmp)

        plugin_root = find_plugin_root(tmp)
        if not plugin_root:
            err("No plugin found in archive.")
            err("Expected: <dir>/.claude-plugin/plugin.json")
            print("\nArchive contents:")
            for f in sorted(tmp.rglob("*")):
                if f.is_file():
                    print(f"  {f.relative_to(tmp)}")
            sys.exit(1)

        meta = read_plugin_meta(plugin_root)
        plugin_name = meta["name"]
        plugin_version = meta["version"]
        plugin_desc = meta["description"]

        ok(f"Found plugin: {BOLD}{plugin_name}{NC} v{plugin_version}")
        if plugin_desc:
            info(f"  {plugin_desc}")

        info("Validating plugin...")
        v_errors, v_warnings = validate_plugin(plugin_root)
        valid = print_validation_report(v_errors, v_warnings, plugin_name)
        if not valid:
            if not force:
                err("Plugin has validation errors. Fix them or use --force to install anyway.")
                sys.exit(1)
            else:
                warn("Installing despite validation errors (--force)")
        print()

        if not marketplace_name:
            marketplace_name = f"local-{plugin_name}"

        plugin_key = f"{plugin_name}@{marketplace_name}"
        mp_dir = MARKETPLACES_DIR / marketplace_name

        info(f"Marketplace: {marketplace_name}")

        dest_plugin_dir = mp_dir / "plugins" / plugin_name
        if dest_plugin_dir.exists():
            if force or dry_run:
                info(
                    f"Updating '{plugin_name}' in marketplace '{marketplace_name}'"
                    + (" (--force)" if force else " (dry run)")
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
            ok(f"Would copy plugin to {dest_plugin_dir}")
            ok("Would register marketplace in known_marketplaces.json")
            ok(f"Would enable plugin as {plugin_key} in settings.json")
            print(f"\n  {CYAN}Run without --dry-run to install.{NC}")
            return

        dest_plugin_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(plugin_root, dest_plugin_dir)
        _fix_permissions(dest_plugin_dir)
        ok("Plugin copied to marketplace")

    # Generate/update marketplace.json
    mp_json_dir = mp_dir / ".claude-plugin"
    mp_json_dir.mkdir(parents=True, exist_ok=True)
    mp_json_path = mp_json_dir / "marketplace.json"

    if mp_json_path.exists():
        mj = load_json_safe(mp_json_path)
        # Ensure owner exists (required by Claude Code schema)
        if "owner" not in mj:
            mj["owner"] = {"name": "local"}
        plugins_list = mj.setdefault("plugins", [])
        plugins_list[:] = [p for p in plugins_list if p.get("name") != plugin_name]
    else:
        mj = {
            "name": marketplace_name,
            "owner": {"name": "local"},
            "description": "Local plugin marketplace (auto-generated by claude-plugin-install)",
            "version": "1.0.0",
        }
        plugins_list = []
        mj["plugins"] = plugins_list

    plugins_list.append(
        {
            "name": plugin_name,
            "description": plugin_desc,
            "version": plugin_version,
            "source": f"./plugins/{plugin_name}",
        }
    )

    save_json_safe(mp_json_path, mj)
    ok("Marketplace manifest updated")

    # ── Register in known_marketplaces.json (what Claude Code actually reads) ──
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    known_mp = load_json_safe(KNOWN_MARKETPLACES_FILE)
    known_mp[marketplace_name] = {
        "source": {
            "source": "directory",
            "path": _portable_path(mp_dir),
        },
        "installLocation": _portable_path(mp_dir),
        "lastUpdated": now,
    }
    save_json_safe(KNOWN_MARKETPLACES_FILE, known_mp)
    ok("Registered in known_marketplaces.json (Claude Code runtime registry)")

    # ── Enable plugin in settings.json (what Claude Code reads for enabledPlugins) ──
    settings = load_json_safe(ENABLED_PLUGINS_FILE)
    ep = settings.setdefault("enabledPlugins", {})
    ep[plugin_key] = True
    save_json_safe(ENABLED_PLUGINS_FILE, settings)
    ok(f"Plugin enabled in {ENABLED_PLUGINS_FILE.name}")

    installed = load_json_safe(INSTALLED_FILE)
    if "version" not in installed:
        installed = {"version": 1, "plugins": installed}
    plugins_map = installed.setdefault("plugins", {})
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    plugins_map[plugin_key] = {
        "version": plugin_version,
        "installedAt": now,
        "lastUpdated": now,
        "installPath": _portable_path(dest_plugin_dir),
        "isLocal": True,
    }
    save_json_safe(INSTALLED_FILE, installed)
    ok("Plugin registered in installed_plugins.json")

    print()
    print(f"{GREEN}{'═' * 55}{NC}")
    print(f"{GREEN}  {BOLD}{plugin_name}{NC}{GREEN} installed successfully!{NC}")
    print(f"{GREEN}{'═' * 55}{NC}")
    print()
    print(f"  Plugin key:    {BOLD}{plugin_key}{NC}")
    print(f"  Location:      {dest_plugin_dir}")
    print(f"  Marketplace:   {marketplace_name}")
    print(f"  Enabled in:    {ENABLED_PLUGINS_FILE}")
    print()
    print("  Restart Claude Code for changes to take effect.")
    print()
    print("  Verify:      claude plugin list")
    print(f"  Uninstall:   {sys.argv[0]} --uninstall {plugin_key}")
    print(f"  List all:    {sys.argv[0]} --list")
    print()


# ── Uninstall ─────────────────────────────────────────────


def do_uninstall(plugin_key: str):
    if "@" not in plugin_key:
        err("Format: --uninstall <plugin-name>@<marketplace-name>")
        sys.exit(1)

    plugin_name, marketplace_name = plugin_key.split("@", 1)
    mp_dir = MARKETPLACES_DIR / marketplace_name
    plug_dir = mp_dir / "plugins" / plugin_name

    info(f"Uninstalling {plugin_name} from marketplace {marketplace_name}...")

    if plug_dir.exists():
        shutil.rmtree(plug_dir)
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

    if not remaining:
        info(f"Marketplace '{marketplace_name}' is now empty, removing...")
        shutil.rmtree(mp_dir, ignore_errors=True)

        # Remove from known_marketplaces.json (Claude Code runtime registry)
        known_mp = load_json_safe(KNOWN_MARKETPLACES_FILE)
        known_mp.pop(marketplace_name, None)
        save_json_safe(KNOWN_MARKETPLACES_FILE, known_mp)
        ok(f"Removed empty marketplace '{marketplace_name}'")

    # Remove from enabledPlugins in settings.json
    settings = load_json_safe(ENABLED_PLUGINS_FILE)
    settings.get("enabledPlugins", {}).pop(plugin_key, None)
    save_json_safe(ENABLED_PLUGINS_FILE, settings)

    installed = load_json_safe(INSTALLED_FILE)
    plugins_map = installed.get("plugins", installed)
    plugins_map.pop(plugin_key, None)
    save_json_safe(INSTALLED_FILE, installed)

    ok(f"Uninstalled {plugin_key}")
    print("  Restart Claude Code for changes to take effect.")


# ── List ──────────────────────────────────────────────────


def do_list():
    if not MARKETPLACES_DIR.exists():
        info("No local marketplaces found. Nothing installed yet.")
        return

    print(f"{BOLD}Locally installed plugins:{NC}")
    print()

    settings = load_json_safe(ENABLED_PLUGINS_FILE)
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


def do_validate(source_path: str) -> None:
    p = Path(source_path)
    tmpdir: str | None = None  # Track if we created a temp dir
    plugin_root: Path | None = None

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
        plugin_root = p if (p / ".claude-plugin" / "plugin.json").exists() else find_plugin_root(p)
        if plugin_root is None:
            err("No plugin found in directory. Expected: .claude-plugin/plugin.json")
            sys.exit(1)
    elif p.is_file():
        info("Extracting archive for validation...")
        tmpdir = tempfile.mkdtemp()
        extract_archive(source_path, Path(tmpdir))
        plugin_root = find_plugin_root(Path(tmpdir))
        if plugin_root is None:
            err("No plugin found in archive. Expected: <dir>/.claude-plugin/plugin.json")
            print("\nArchive contents:")
            for f in sorted(Path(tmpdir).rglob("*")):
                if f.is_file():
                    print(f"  {f.relative_to(Path(tmpdir))}")
            shutil.rmtree(tmpdir, ignore_errors=True)
            sys.exit(1)
    else:
        err(f"Not found: {source_path}")
        sys.exit(1)

    assert plugin_root is not None  # All None paths call sys.exit above
    meta = read_plugin_meta(plugin_root)
    ok(f"Found plugin: {BOLD}{meta['name']}{NC} v{meta['version']}")
    if meta["description"]:
        info(f"  {meta['description']}")

    print()
    info("Running validation checks...")
    v_errors, v_warnings = validate_plugin(plugin_root)
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


def do_doctor():
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

    # 2. Check settings.json
    if SETTINGS_FILE.exists():
        try:
            data = load_jsonc(SETTINGS_FILE)
            ok("settings.json: valid")
            ep = data.get("enabledPlugins", {})
            if ep:
                enabled = sum(1 for v in ep.values() if v)
                disabled = sum(1 for v in ep.values() if not v)
                info(f"  {enabled} plugin(s) enabled, {disabled} disabled")
        except Exception as e:
            err(f"settings.json: CORRUPT — {e}")
            issues += 1
    else:
        info("settings.json: not present (this is OK)")

    # 2b. Check known_marketplaces.json (the file Claude Code actually reads)
    if KNOWN_MARKETPLACES_FILE.exists():
        try:
            km_data = load_jsonc(KNOWN_MARKETPLACES_FILE)
            ok(f"known_marketplaces.json: valid ({len(km_data)} marketplace(s))")
        except Exception as e:
            err(f"known_marketplaces.json: CORRUPT — {e}")
            issues += 1
    else:
        warn("known_marketplaces.json: not present — Claude Code won't discover any marketplaces")
        issues += 1

    # 3. Check marketplaces directory
    if not MARKETPLACES_DIR.exists():
        info("No local marketplaces directory yet.")
        print()
        return

    # Load settings once for all checks below
    settings = load_json_safe(ENABLED_PLUGINS_FILE)

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
            ok("  marketplace.json: valid")
        except json.JSONDecodeError as e:
            err(f"  marketplace.json: CORRUPT — {e}")
            issues += 1
            continue

        # Check if registered in known_marketplaces.json (what Claude Code reads)
        known_mp = load_json_safe(KNOWN_MARKETPLACES_FILE)
        if mp_name in known_mp:
            km_path = known_mp[mp_name].get("source", {}).get("path", "")
            if not km_path:
                km_path = known_mp[mp_name].get("installLocation", "")
            actual_path = _portable_path(mp_dir)
            if km_path and km_path != actual_path:
                warn(f"  Path mismatch in known_marketplaces.json: registered='{km_path}' actual='{actual_path}'")
                issues += 1
            else:
                ok("  Registered in known_marketplaces.json")
        else:
            warn("  NOT in known_marketplaces.json — Claude Code won't discover this marketplace")
            issues += 1

        # Check each plugin in marketplace
        plugins_dir = mp_dir / "plugins"
        if not plugins_dir.exists():
            warn("  No plugins/ directory")
            continue

        declared_plugins = {p.get("name") for p in mj.get("plugins", []) if isinstance(p, dict)}

        for plug_dir in sorted(plugins_dir.iterdir()):
            if not plug_dir.is_dir():
                continue

            pj = plug_dir / ".claude-plugin" / "plugin.json"
            if not pj.exists():
                warn(f"  {plug_dir.name}: missing plugin.json")
                issues += 1
                continue

            meta = read_plugin_meta(plug_dir)
            plugin_key = f"{meta['name']}@{mp_name}"

            # Quick validation
            v_errors, v_warnings = validate_plugin(plug_dir)
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
                else f"{YELLOW}not in enabledPlugins{NC}"
            )

            print(f"    {meta['name']} v{meta['version']}  [{en_str}]  [{', '.join(status_parts)}]")

            # Check if declared in marketplace.json
            if meta["name"] not in declared_plugins:
                warn("    Not listed in marketplace.json — may not be discovered by Claude Code")
                issues += 1

    # 5. Check for orphaned entries
    # 5a. Check known_marketplaces.json
    known_mp = load_json_safe(KNOWN_MARKETPLACES_FILE)
    for mp_name, mp_cfg in known_mp.items():
        source = mp_cfg.get("source", {})
        mp_path_str = source.get("path", "") or mp_cfg.get("installLocation", "")
        if mp_path_str:
            mp_path = Path(mp_path_str)
            if not mp_path.exists():
                print()
                warn(f"Orphaned in known_marketplaces.json: '{mp_name}' points to non-existent path: {mp_path}")
                issues += 1

    # 5b. Check enabledPlugins in settings.json

    ep = settings.get("enabledPlugins", {})
    for pkey, enabled in ep.items():
        if "@" in pkey:
            pname, mpname = pkey.split("@", 1)
            plug_path = MARKETPLACES_DIR / mpname / "plugins" / pname
            if not plug_path.exists() and enabled:
                print()
                warn(f"Orphaned entry in enabledPlugins: '{pkey}' — plugin directory does not exist")
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
  claude-plugin-install <archive> [marketplace] [options]

  Install a plugin from an archive file (.tar.gz, .tgz, .zip, .tar.bz2,
  .tar.xz, .tar). The archive must contain a directory with a
  .claude-plugin/plugin.json manifest inside it.

  The plugin is copied into a local marketplace directory at:
    ~/.claude/plugins/marketplaces/<marketplace>/plugins/<name>/

  The marketplace is registered in known_marketplaces.json (the file Claude
  Code actually reads at runtime) and the plugin is enabled in settings.json.
  auto-generated as "local-<plugin-name>".

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

{BOLD}--uninstall <plugin>@<marketplace>{NC}
  Remove an installed plugin and clean up settings. If the marketplace
  has no remaining plugins, it is also removed.

    claude-plugin-install --uninstall token-reporter@local-token-reporter

{BOLD}--list{NC}
  Show all plugins installed by this tool, with version, enabled/disabled
  status, and component summary (commands, agents, skills, hooks, MCP).

{BOLD}--doctor{NC}
  Health check for the entire plugin installation:
    • Validates settings.json and known_marketplaces.json
    • Checks each marketplace is registered and its path matches
    • Runs validation on every installed plugin
    • Detects orphaned entries in settings (deleted plugins, missing paths)
    • Reports enabled/disabled status

{BOLD}options:{NC}
  -f, --force     Install even if validation fails (errors become warnings).
                  Also skips the overwrite confirmation prompt.
  -n, --dry-run   Show exactly what would happen without writing any files.
                  Useful for previewing before a real install.
  --version       Print version number and exit.

{BOLD}examples:{NC}
  # Basic install
  claude-plugin-install my-plugin.tar.gz

  # Install into a shared marketplace
  claude-plugin-install my-plugin.tar.gz shared-tools

  # Update an existing plugin (skip confirmation)
  claude-plugin-install my-plugin.tar.gz --force

  # Validate before distributing
  claude-plugin-install --validate ./my-plugin/

  # Re-validate an installed plugin after manual edits
  claude-plugin-install --validate my-plugin@local-my-plugin

  # Check everything is healthy
  claude-plugin-install --doctor

  # Preview an install
  claude-plugin-install --dry-run my-plugin.tar.gz

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
  ~/.claude/plugins/known_marketplaces.json   runtime marketplace registry (what Claude Code reads)
  ~/.claude/settings.json                     enabledPlugins (what Claude Code reads for plugin state)
  ~/.claude/plugins/marketplaces/             plugin files
  ~/.claude/plugins/installed_plugins.json    install tracking + backups
"""


def main():
    parser = argparse.ArgumentParser(
        prog="claude-plugin-install",
        description=(
            "Install, validate, and manage Claude Code plugins.\n"
            "Cross-platform: macOS, Linux, and Windows. Python 3.12+, no dependencies."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=HELP_EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("archive", nargs="?", help="Plugin archive to install (.tar.gz, .tgz, .zip, .tar.bz2, .tar.xz)")
    group.add_argument("--uninstall", metavar="NAME@MARKETPLACE", help="Remove a plugin and clean up settings")
    group.add_argument(
        "--validate", metavar="PATH", help="Validate an archive, directory, or installed plugin (name@marketplace)"
    )
    group.add_argument("--list", action="store_true", help="Show all plugins installed by this tool")
    group.add_argument("--doctor", action="store_true", help="Run health checks on all installed plugins and settings")

    parser.add_argument(
        "marketplace", nargs="?", default=None, help="Marketplace name to install into (default: local-<plugin-name>)"
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="Install despite validation errors; skip overwrite prompt"
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="Preview what would happen without writing any files"
    )

    args = parser.parse_args()

    if args.list:
        do_list()
    elif args.uninstall:
        do_uninstall(args.uninstall)
    elif args.validate:
        do_validate(args.validate)
    elif args.doctor:
        do_doctor()
    elif args.archive:
        do_install(args.archive, args.marketplace, force=args.force, dry_run=args.dry_run)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
