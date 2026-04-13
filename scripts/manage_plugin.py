#!/usr/bin/env python3
"""Plugin lifecycle management for Claude Code plugins.

Handles installation, uninstallation, updating, enabling, and disabling
of plugins in local marketplaces. Uses gitignore-aware file copying,
archive extraction with path traversal prevention, and atomic settings
updates with timestamped backups.

Usage:
    uv run scripts/manage_plugin.py <source> <marketplace> [--force] [--dry-run] [--quiet]
    uv run scripts/manage_plugin.py --uninstall <plugin>@<marketplace> [--dry-run]
    uv run scripts/manage_plugin.py --update <source> <marketplace> [--force] [--dry-run]
    uv run scripts/manage_plugin.py --enable <plugin> [--scope user|local]
    uv run scripts/manage_plugin.py --disable <plugin> [--scope user|local]
    uv run scripts/manage_plugin.py --version
"""

import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from cpv_management_common import (
    BOLD,
    CACHE_DIR,
    CYAN,
    GREEN,
    INSTALLED_FILE,
    IS_WINDOWS,
    KNOWN_MARKETPLACES_FILE,
    MARKETPLACES_DIR,
    NC,
    SETTINGS_TARGET,
    TOOL_VERSION,
    YELLOW,
    _validate_safe_name,
    err,
    extract_archive,
    info,
    load_json_safe,
    ok,
    save_json_safe,
    warn,
)

__all__ = [
    "find_plugin_root",
    "MultiplePluginsFoundError",
    "read_plugin_meta",
    "do_install",
    "do_uninstall",
    "do_update",
    "do_enable",
    "do_disable",
    "_portable_path",
    "_load_installed_plugins",
    "_detect_plugin_origin_refs",
]


class MultiplePluginsFoundError(Exception):
    """Raised when a directory contains more than one plugin.json (monorepo).

    The user must pass --plugin-dir <path> to select which one to operate on.
    """

    def __init__(self, plugin_roots: List[Path]):
        self.plugin_roots = plugin_roots
        joined = "\n  ".join(str(p) for p in plugin_roots)
        super().__init__(f"Multiple plugins found in the source directory:\n  {joined}")


# ── Gitignore handling ────────────────────────────────────


def _parse_gitignore_patterns(gitignore_path: Path) -> List[str]:
    """Parse a .gitignore file and return a list of patterns."""
    if not gitignore_path.exists():
        return []
    patterns = []
    for line in gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
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
    pattern = pattern.rstrip()
    if not pattern:
        return None, False
    anchored = "/" in pattern.rstrip("/")
    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern.rstrip("/")
    parts = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                if i + 2 < len(pattern) and pattern[i + 2] == "/":
                    parts.append("(?:.*/)?")
                    i += 3
                    continue
                else:
                    parts.append(".*")
                    i += 2
                    continue
            else:
                parts.append("[^/]*")
        elif c == "?":
            parts.append("[^/]")
        elif c == "[":
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
        regex_str = "^" + regex_str
    else:
        regex_str = "(?:^|/)" + regex_str
    regex_str += "(?:/.*)?$"
    try:
        return re.compile(regex_str), negation
    except re.error:
        return None, False


def _is_git_metadata(rel_str: str) -> bool:
    """Check if a relative path is a git metadata file/directory."""
    norm = rel_str.replace("\\", "/")
    if norm == ".git" or norm.startswith(".git/"):
        return True
    basename = norm.rsplit("/", 1)[-1] if "/" in norm else norm
    if basename in (".gitignore", ".gitattributes", ".gitmodules", ".gitkeep"):
        return True
    return False


def _build_gitignore_matcher(plugin_dir: Path) -> Callable[[Path], bool]:
    """Build a function that returns True if a path should be ignored.
    Uses `git check-ignore` if inside a git repo, otherwise parses .gitignore manually."""
    gitignore_path = plugin_dir / ".gitignore"
    has_git = (plugin_dir / ".git").exists()
    if has_git:
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
            use_git = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            use_git = False
    else:
        use_git = False

    if use_git:
        ignored_paths: set = set()
        try:
            all_paths = []
            for item in plugin_dir.rglob("*"):
                rel = str(item.relative_to(plugin_dir))
                if not _is_git_metadata(rel):
                    all_paths.append(rel)
            if all_paths:
                result = subprocess.run(
                    ["git", "check-ignore", "--stdin"],
                    cwd=str(plugin_dir),
                    input="\n".join(all_paths),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                for line in result.stdout.splitlines():
                    stripped = line.strip()
                    if stripped:
                        ignored_paths.add(stripped)
        except (subprocess.TimeoutExpired, OSError):
            pass

        def _is_ignored_git(path: Path) -> bool:
            rel = path.relative_to(plugin_dir)
            # Normalize to forward slashes — git check-ignore outputs forward slashes even on Windows
            rel_str = str(rel).replace("\\", "/")
            if _is_git_metadata(rel_str):
                return True
            return rel_str in ignored_paths

        return _is_ignored_git

    if not gitignore_path.exists() and not has_git:

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


# ── File operations ───────────────────────────────────────


def _copy_plugin_from_dir(
    source_dir: Path,
    dest: Path,
    ignore_fn: Optional[Callable[[Path], bool]] = None,
    follow_symlinks: bool = False,
    skipped_symlinks: Optional[List[Path]] = None,
):
    """Copy a plugin directory to dest, skipping files matched by ignore_fn.

    When follow_symlinks is False (default), symlinks are skipped and each
    one is appended to skipped_symlinks (if provided) so the caller can
    surface a warning after the copy loop. When follow_symlinks is True,
    symlinks are resolved and their target contents are copied — this is
    opt-in because following symlinks can escape the plugin directory and
    pull in unintended files.
    """
    copied_any = False
    for item in sorted(source_dir.iterdir()):
        if item.name in (".git", ".gitignore", ".gitattributes", ".gitmodules", ".gitkeep"):
            continue
        if ignore_fn and ignore_fn(item):
            continue
        if item.is_symlink():
            if not follow_symlinks:
                if skipped_symlinks is not None:
                    skipped_symlinks.append(item)
                continue
            # Follow: resolve to the real target and copy its contents. We
            # intentionally do NOT preserve the symlink — we inline the
            # target so downstream consumers see a self-contained tree.
            target = item.resolve()
            if not target.exists():
                if skipped_symlinks is not None:
                    skipped_symlinks.append(item)
                continue
            dest_item = dest / item.name
            if target.is_dir():
                _copy_plugin_from_dir(target, dest_item, ignore_fn, follow_symlinks, skipped_symlinks)
                if dest_item.exists():
                    if not copied_any:
                        dest.mkdir(parents=True, exist_ok=True)
                    copied_any = True
            elif target.is_file():
                if not copied_any:
                    dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, dest_item)
                copied_any = True
            continue
        dest_item = dest / item.name
        if item.is_dir():
            _copy_plugin_from_dir(item, dest_item, ignore_fn, follow_symlinks, skipped_symlinks)
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

    Skips directories that also contain marketplace.json.

    Returns the single plugin root when exactly one is found, or None when
    none are found. When multiple plugin.json files exist under search_dir
    (monorepo layout), raises MultiplePluginsFoundError so the caller can
    ask the user to disambiguate via --plugin-dir — returning the first
    match would silently ignore sibling plugins.
    """
    matches: List[Path] = []
    for pj in search_dir.rglob(".claude-plugin/plugin.json"):
        if (pj.parent / "marketplace.json").exists():
            continue
        matches.append(pj.parent.parent)
    if not matches:
        return None
    if len(matches) > 1:
        raise MultiplePluginsFoundError(sorted(matches))
    return matches[0]


# ── Plugin metadata ───────────────────────────────────────


def read_plugin_meta(plugin_root: Path) -> dict:
    """Read plugin.json and return metadata with defaults."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        meta = json.loads(pj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        warn(f"Could not read plugin.json: {e}")
        meta = {}
    return {
        "name": meta.get("name") or plugin_root.name,
        "version": meta.get("version", "1.0.0"),
        "description": meta.get("description", ""),
    }


def _detect_plugin_origin_refs(plugin_root: Path) -> List[str]:
    """Detect marketplace, repository, or GitHub references inside the plugin."""
    refs: List[str] = []
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if pj.exists():
        try:
            meta = json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        for field in ("marketplace", "registry", "source", "origin"):
            val = meta.get(field)
            if isinstance(val, str) and val.strip():
                refs.append(f'plugin.json "{field}": "{val}"')
            elif isinstance(val, dict):
                refs.append(f'plugin.json "{field}": {json.dumps(val)}')
        for field in ("repository", "homepage", "url", "bugs"):
            val = meta.get(field)
            if isinstance(val, str) and val.strip():
                refs.append(f'plugin.json "{field}": "{val}"')
            elif isinstance(val, dict):
                url = val.get("url", "")
                if url:
                    refs.append(f'plugin.json "{field}.url": "{url}"')
        author = meta.get("author")
        if isinstance(author, dict):
            url = author.get("url", "")
            if url:
                refs.append(f'plugin.json "author.url": "{url}"')
        elif isinstance(author, str) and ("github.com" in author or "http" in author):
            refs.append(f'plugin.json "author": "{author}"')
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
    """Convert a path to forward slashes for JSON storage."""
    return str(p).replace("\\", "/")


def _load_installed_plugins() -> dict:
    """Load installed_plugins.json and migrate v1->v2 format if needed."""
    installed = load_json_safe(INSTALLED_FILE)
    file_version = installed.get("version", 1)
    if "version" not in installed:
        installed = {"version": 2, "plugins": installed}
    if file_version < 2:
        installed["version"] = 2
        old_plugins = installed.get("plugins", {})
        if isinstance(old_plugins, dict):
            for pk, pv in old_plugins.items():
                if isinstance(pv, dict):
                    pv.setdefault("scope", "user")
                    old_plugins[pk] = [pv]
    if not isinstance(installed.get("plugins"), dict):
        installed["plugins"] = {}
    return installed


# ── Validation bridge ─────────────────────────────────────


def _run_cpv_validation(plugin_root: Path, quiet: bool = False) -> Tuple[List[str], List[str], bool]:
    """Run CPV's validate_plugin.py via subprocess. Returns (errors, warnings, valid).

    Exit codes: 0=pass, 1=CRITICAL, 2=MAJOR, 3=MINOR, 4=NIT, 5+=WARNING.
    Only CRITICAL and MAJOR (exit 1-2) block installation.
    """
    scripts_dir = Path(__file__).resolve().parent
    validate_script = scripts_dir / "validate_plugin.py"
    if not validate_script.exists():
        if not quiet:
            warn("CPV validation script not found — skipping validation")
        return [], [], True

    try:
        result = subprocess.run(
            [sys.executable, str(validate_script), str(plugin_root)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        if not quiet:
            warn("Validation timed out after 120s — skipping")
        return [], ["Validation timeout"], True
    output = result.stdout + result.stderr
    v_errors = [line for line in output.splitlines() if "CRITICAL" in line or "MAJOR" in line]
    v_warnings = [line for line in output.splitlines() if "MINOR" in line or "NIT" in line or "WARNING" in line]
    # Only CRITICAL (exit 1) and MAJOR (exit 2) block installation
    valid = result.returncode in (0, 3, 4, 5)
    return v_errors, v_warnings, valid


# ── Lifecycle: Install ────────────────────────────────────


def _resolve_plugin_root(search_dir: Path, plugin_dir: Optional[str], context_label: str) -> Path:
    """Pick the plugin root from search_dir, respecting an explicit --plugin-dir.

    - If plugin_dir is given, that path (absolute or relative to search_dir) is
      used verbatim and must contain .claude-plugin/plugin.json.
    - Otherwise, if search_dir itself is a plugin root, it is returned.
    - Otherwise, find_plugin_root() scans the tree. On multiple matches, this
      prints all candidates and exits — silently picking the first one hid
      sibling plugins in monorepos.
    """
    if plugin_dir:
        candidate = Path(plugin_dir)
        if not candidate.is_absolute():
            candidate = (search_dir / candidate).resolve()
        if not (candidate / ".claude-plugin" / "plugin.json").exists():
            err(f"--plugin-dir points to '{candidate}' but no .claude-plugin/plugin.json there.")
            sys.exit(1)
        return candidate

    if (search_dir / ".claude-plugin" / "plugin.json").exists():
        return search_dir

    try:
        root = find_plugin_root(search_dir)
    except MultiplePluginsFoundError as exc:
        err(f"Multiple plugins found in {context_label}:")
        for p in exc.plugin_roots:
            err(f"  - {p}")
        err("Pass --plugin-dir <path> to choose one of the above.")
        sys.exit(1)
    if not root:
        err(f"No plugin found in {context_label}.")
        err("Expected: <dir>/.claude-plugin/plugin.json")
        sys.exit(1)
    return root


def do_install(
    source_path: str,
    marketplace_name: Optional[str],
    force: bool = False,
    dry_run: bool = False,
    quiet: bool = False,
    plugin_dir: Optional[str] = None,
    follow_symlinks: bool = False,
    dev_link: bool = False,
):
    if dry_run and not quiet:
        info("DRY RUN — no files will be modified")

    source = Path(source_path)
    if not source.exists():
        err(f"Not found: {source_path}")
        sys.exit(1)

    is_directory = source.is_dir()
    ignore_fn = None
    tmp_cleanup = None

    if is_directory:
        if not quiet:
            info(f"Installing from directory: {source}")
        plugin_root = _resolve_plugin_root(source, plugin_dir, "directory")
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
        try:
            plugin_root = _resolve_plugin_root(tmp, plugin_dir, "archive")
        except SystemExit:
            if not quiet:
                print("\nArchive contents:")
                for f in sorted(tmp.rglob("*")):
                    if f.is_file():
                        print(f"  {f.relative_to(tmp)}")
            shutil.rmtree(tmp_cleanup, ignore_errors=True)
            raise

    try:
        meta = read_plugin_meta(plugin_root)
        plugin_name = meta["name"]
        plugin_version = meta["version"]
        plugin_desc = meta["description"]

        if not quiet:
            ok(f"Found plugin: {BOLD}{plugin_name}{NC} v{plugin_version}")
            if plugin_desc:
                info(f"  {plugin_desc}")

        # Use CPV's modular validator instead of CPM's monolithic one
        if not quiet:
            info("Validating plugin...")
        v_errors, v_warnings, valid = _run_cpv_validation(plugin_root, quiet=quiet)
        if not quiet:
            if v_errors:
                for ve in v_errors:
                    err(ve)
            if v_warnings:
                for vw in v_warnings:
                    warn(vw)
            if valid:
                ok("Validation passed")
            else:
                err(f"Validation found {len(v_errors)} error(s), {len(v_warnings)} warning(s)")
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
            err("Usage: manage_plugin.py <source> <marketplace>")
            err(f"Example: manage_plugin.py {source_path} my-marketplace")
            sys.exit(1)

        _validate_safe_name(marketplace_name, "marketplace")
        _validate_safe_name(plugin_name, "plugin")
        plugin_key = f"{plugin_name}@{marketplace_name}"
        mp_dir = MARKETPLACES_DIR / marketplace_name

        # Warn if the same plugin exists in OTHER marketplaces
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
                    print(f"    manage_plugin.py --uninstall {loc}")
                print()

        # If marketplace already exists, ask for confirmation
        if mp_dir.exists() and not force and not dry_run:
            if quiet:
                pass
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
                try:
                    shutil.rmtree(dest_plugin_dir)
                except OSError as e:
                    err(f"Failed to remove existing plugin directory: {e}")
                    sys.exit(1)

        if dry_run:
            if not quiet:
                ok(f"Would copy plugin to {dest_plugin_dir}")
                ok(f"Would register marketplace in {SETTINGS_TARGET.name}")
                ok(f"Would enable plugin as {plugin_key}")
                print(f"\n  {CYAN}Run without --dry-run to install.{NC}")
            return

        # Copy plugin to marketplace (or symlink in --dev-link mode)
        skipped_symlinks: List[Path] = []
        if dev_link:
            if not is_directory:
                err("--dev-link requires a directory source, not an archive.")
                sys.exit(1)
            dest_plugin_dir.parent.mkdir(parents=True, exist_ok=True)
            # Create directory symlink pointing at the live source
            try:
                os.symlink(plugin_root.resolve(), dest_plugin_dir, target_is_directory=True)
            except OSError as e:
                err(f"Failed to create dev-link symlink: {e}")
                if os.name == "nt":
                    err("On Windows, dev-link requires Developer Mode or admin privileges.")
                sys.exit(1)
            # Sentinel file — marks this as a dev-link so uninstall/update handle it specially
            sentinel = mp_dir / "plugins" / f".cpv-devlink-{plugin_name}.json"
            sentinel.write_text(
                json.dumps(
                    {
                        "source_path": str(plugin_root.resolve()),
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "installer_version": TOOL_VERSION,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            if not quiet:
                ok(f"Dev-linked plugin via symlink -> {plugin_root.resolve()}")
        elif is_directory:
            _copy_plugin_from_dir(
                plugin_root,
                dest_plugin_dir,
                ignore_fn,
                follow_symlinks=follow_symlinks,
                skipped_symlinks=skipped_symlinks,
            )
            if not dest_plugin_dir.exists():
                err("No files to install — all plugin files are gitignored.")
                sys.exit(1)
        else:
            dest_plugin_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(plugin_root, dest_plugin_dir, symlinks=not follow_symlinks)
        if not dev_link:
            _fix_permissions(dest_plugin_dir)
        if not quiet:
            if is_directory:
                ok("Plugin copied to marketplace (respecting .gitignore)")
            else:
                ok("Plugin copied to marketplace")
            if skipped_symlinks and not follow_symlinks:
                warn(
                    f"Skipped {len(skipped_symlinks)} symlink(s) during copy — "
                    "symlinks are skipped by default, pass --follow-symlinks to include them:"
                )
                for link in skipped_symlinks:
                    print(f"    {YELLOW}• {link}{NC}")

    finally:
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
            "owner": {"name": "local"},
            "metadata": {
                "description": "Local plugin marketplace (auto-generated by cpv manage_plugin)",
            },
        }
        plugins_list = []
        mj["plugins"] = plugins_list

    if not isinstance(mj.get("owner"), dict) or "name" not in mj["owner"]:
        mj["owner"] = {"name": "local"}
    if not isinstance(mj.get("metadata"), dict):
        mj["metadata"] = {"description": "Local plugin marketplace (auto-generated by cpv manage_plugin)"}

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
    ekm[marketplace_name] = {"source": {"source": "directory", "path": _portable_path(mp_dir)}}
    ep = settings.setdefault("enabledPlugins", {})
    ep[plugin_key] = True
    save_json_safe(SETTINGS_TARGET, settings, dry_run=dry_run)
    if not quiet:
        ok(f"Registered in {SETTINGS_TARGET.name}")

    installed = _load_installed_plugins()
    plugins_map = installed["plugins"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    plugins_map[plugin_key] = [
        {
            "scope": "user",
            "version": plugin_version,
            "installedAt": now,
            "lastUpdated": now,
            "installPath": _portable_path(dest_plugin_dir),
        }
    ]
    save_json_safe(INSTALLED_FILE, installed, dry_run=dry_run)
    if not quiet:
        ok("Plugin registered in installed_plugins.json")

        print()
        print(f"{GREEN}{'=' * 60}{NC}")
        print(f"{GREEN}  {BOLD}{plugin_name}{NC}{GREEN} installed successfully!{NC}")
        print(f"{GREEN}{'=' * 60}{NC}")
        print()
        print(f"  Plugin key:    {BOLD}{plugin_key}{NC}")
        print(f"  Location:      {dest_plugin_dir}")
        print(f"  Marketplace:   {marketplace_name}")
        print(f"  Settings:      {SETTINGS_TARGET}")
        print()
        print(f"  The plugin is {GREEN}enabled{NC} by default — no action needed.")
        print(f"  {BOLD}Run /reload-plugins or restart Claude Code for changes to take effect.{NC}")
        print()

        origin_refs = _detect_plugin_origin_refs(dest_plugin_dir)
        if origin_refs:
            print(f"  {YELLOW}{BOLD}NOTE:{NC}{YELLOW} This plugin contains references to an origin")
            print(f"  marketplace or repository that differs from '{marketplace_name}':{NC}")
            for ref in origin_refs:
                print(f"    {YELLOW}• {ref}{NC}")
            print()


# ── Lifecycle: Uninstall ──────────────────────────────────


def do_uninstall(plugin_key: str, quiet: bool = False, dry_run: bool = False):
    if "@" not in plugin_key:
        err("Format: --uninstall <plugin-name>@<marketplace-name>")
        sys.exit(1)

    plugin_name, marketplace_name = plugin_key.split("@", 1)
    _validate_safe_name(plugin_name, "plugin")
    _validate_safe_name(marketplace_name, "marketplace")
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

    # Check for --dev-link sentinel; if present, unlink only (don't rm the live source)
    sentinel = mp_dir / "plugins" / f".cpv-devlink-{plugin_name}.json"
    is_devlink = sentinel.exists() or (plug_dir.exists() and plug_dir.is_symlink())

    if plug_dir.exists() or plug_dir.is_symlink():
        try:
            if is_devlink:
                # Unlink the symlink only — preserve the live source tree
                if plug_dir.is_symlink():
                    plug_dir.unlink()
                if sentinel.exists():
                    sentinel.unlink()
                if not quiet:
                    ok("Unlinked dev-link symlink (source tree preserved)")
            else:
                shutil.rmtree(plug_dir)
                if not quiet:
                    ok("Removed plugin directory")
        except OSError as e:
            err(f"Failed to remove plugin directory: {e}")
            sys.exit(1)
    else:
        warn(f"Plugin directory not found: {plug_dir}")

    mp_json = mp_dir / ".claude-plugin" / "marketplace.json"
    if mp_json.exists():
        mj = load_json_safe(mp_json)
        mj["plugins"] = [p for p in mj.get("plugins", []) if p.get("name") != plugin_name]
        save_json_safe(mp_json, mj)

    plugins_parent = mp_dir / "plugins"
    remaining = [d for d in (plugins_parent.iterdir() if plugins_parent.exists() else []) if d.is_dir()]

    # Clean ALL settings files that might reference this plugin
    settings_files_to_clean = [SETTINGS_TARGET]
    # Also check ~/.claude/settings.local.json (legacy or stale)
    user_local = SETTINGS_TARGET.parent / "settings.local.json"
    if user_local.exists() and user_local != SETTINGS_TARGET:
        settings_files_to_clean.append(user_local)
    # Also check project-level .claude/settings.local.json (--scope local entries)
    project_local = Path.cwd() / ".claude" / "settings.local.json"
    if project_local.exists():
        settings_files_to_clean.append(project_local)

    for sf in settings_files_to_clean:
        if not sf.exists():
            continue
        s = load_json_safe(sf)
        changed = False
        if not remaining:
            if s.get("extraKnownMarketplaces", {}).pop(marketplace_name, None) is not None:
                changed = True
        if s.get("enabledPlugins", {}).pop(plugin_key, None) is not None:
            changed = True
        if changed:
            save_json_safe(sf, s)

    if not remaining:
        if not quiet:
            info(f"Marketplace '{marketplace_name}' is now empty, removing...")
        shutil.rmtree(mp_dir, ignore_errors=True)
        # Also clean Claude Code's internal marketplace registry
        if KNOWN_MARKETPLACES_FILE.exists():
            km = load_json_safe(KNOWN_MARKETPLACES_FILE)
            if km.pop(marketplace_name, None) is not None:
                save_json_safe(KNOWN_MARKETPLACES_FILE, km)
                if not quiet:
                    ok(f"Removed '{marketplace_name}' from known_marketplaces.json")
        if not quiet:
            ok(f"Removed empty marketplace '{marketplace_name}'")

    installed = _load_installed_plugins()
    plugins_map = installed["plugins"]
    plugins_map.pop(plugin_key, None)
    save_json_safe(INSTALLED_FILE, installed)

    # Clean up Claude Code's plugin cache
    cache_mp = CACHE_DIR / marketplace_name
    if cache_mp.exists():
        cache_plug = cache_mp / plugin_name
        if cache_plug.exists():
            shutil.rmtree(cache_plug, ignore_errors=True)
            if not quiet:
                ok("Removed cached plugin data")
        remaining_cached = [d for d in cache_mp.iterdir() if d.is_dir()]
        if not remaining_cached:
            shutil.rmtree(cache_mp, ignore_errors=True)
    orphan_cache = CACHE_DIR / plugin_name
    if orphan_cache.exists() and orphan_cache != cache_mp:
        shutil.rmtree(orphan_cache, ignore_errors=True)
        if not quiet:
            ok(f"Removed orphaned cache at {orphan_cache}")

    if not quiet:
        ok(f"Uninstalled {plugin_key}")
        print("  Run /reload-plugins or restart Claude Code for changes to take effect.")


# ── Lifecycle: Enable / Disable ───────────────────────────


def _resolve_settings_file(scope: str) -> Path:
    """Return the settings file for the given scope.

    'user' (default) → ~/.claude/settings.json
    'local'          → <project>/.claude/settings.local.json
    """
    if scope == "local":
        project_claude = Path.cwd() / ".claude"
        # Validate we're in a project directory, not a random folder
        if not project_claude.exists() and not (Path.cwd() / ".git").exists():
            err("Not a project directory (no .claude/ or .git/ found).")
            err("Run from a project root or use --scope user instead.")
            sys.exit(1)
        return project_claude / "settings.local.json"
    return SETTINGS_TARGET


def _collect_all_plugin_keys() -> dict[str, list[str]]:
    """Scan user settings + project settings for plugin keys.

    Checks ~/.claude/settings.json and <project>/.claude/settings.local.json.
    """
    result: dict[str, list[str]] = {}
    files_to_check = [SETTINGS_TARGET]
    project_settings = Path.cwd() / ".claude" / "settings.local.json"
    if project_settings.exists():
        files_to_check.append(project_settings)
    for sf in files_to_check:
        if not sf.exists():
            continue
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for key in data.get("enabledPlugins", {}):
            result.setdefault(key, []).append(str(sf))
    return result


def _resolve_plugin_key(query: str) -> str:
    """Resolve a plugin query to a full plugin_name@marketplace key.

    Accepts:
      plugin-name                         → find unique match across settings
      plugin-name@marketplace-name        → use as-is
      plugin-name@owner/marketplace-name  → strip owner/ prefix for key

    Raises SystemExit on ambiguity or not found.
    """
    # Case 3: owner/marketplace format — strip owner, key is name@marketplace
    if "@" in query and "/" in query.split("@", 1)[1]:
        plugin_name, owner_marketplace = query.split("@", 1)
        marketplace_name = owner_marketplace.split("/", 1)[1]
        return f"{plugin_name}@{marketplace_name}"

    # Case 2: already has @marketplace
    if "@" in query:
        return query

    # Case 1: bare plugin name — search for unique match
    all_keys = _collect_all_plugin_keys()
    matches = [k for k in all_keys if k.split("@", 1)[0] == query]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        err(f"Ambiguous plugin name '{query}'. Matches found:")
        for m in sorted(matches):
            print(f"  - {m}")
        err("Specify the full key: --enable/--disable <name>@<marketplace>")
        sys.exit(1)

    # Not found in settings — maybe it's on disk but never toggled yet
    disk_matches: list[str] = []
    if MARKETPLACES_DIR.exists():
        for mkt_dir in MARKETPLACES_DIR.iterdir():
            if not mkt_dir.is_dir():
                continue
            plug_dir = mkt_dir / "plugins" / query
            if plug_dir.is_dir():
                disk_matches.append(f"{query}@{mkt_dir.name}")
            # Also check cache dir
        cache_dir = MARKETPLACES_DIR.parent / "cache"
        if cache_dir.exists():
            for mkt_dir in cache_dir.iterdir():
                if not mkt_dir.is_dir():
                    continue
                plug_dir = mkt_dir / query
                if plug_dir.is_dir():
                    disk_matches.append(f"{query}@{mkt_dir.name}")

    if len(disk_matches) == 1:
        return disk_matches[0]
    if len(disk_matches) > 1:
        err(f"Ambiguous plugin name '{query}'. Found in multiple marketplaces:")
        for m in sorted(disk_matches):
            print(f"  - {m}")
        err("Specify the full key: --enable/--disable <name>@<marketplace>")
        sys.exit(1)

    err(f"Plugin '{query}' not found in any settings file or marketplace.")
    sys.exit(1)


def _verify_plugin_installed(plugin_key: str) -> bool:
    """Check if the plugin is installed (exists in enabledPlugins or on disk)."""
    all_keys = _collect_all_plugin_keys()
    if plugin_key in all_keys:
        return True
    # Check on disk (cache or plugins dir)
    plugin_name, marketplace_name = plugin_key.split("@", 1)
    plug_dir = MARKETPLACES_DIR / marketplace_name / "plugins" / plugin_name
    if plug_dir.is_dir():
        return True
    cache_dir = MARKETPLACES_DIR.parent / "cache" / marketplace_name / plugin_name
    if cache_dir.is_dir():
        return True
    return False


def do_enable(plugin_key: str, quiet: bool = False, dry_run: bool = False, scope: str = "user"):
    """Enable a plugin. Scope: 'user' → ~/.claude/settings.json, 'local' → project .claude/settings.local.json."""
    plugin_key = _resolve_plugin_key(plugin_key)
    if not _verify_plugin_installed(plugin_key):
        err(f"Plugin '{plugin_key}' is not installed. Install it first.")
        sys.exit(1)

    target = _resolve_settings_file(scope)
    if scope == "local":
        target.parent.mkdir(parents=True, exist_ok=True)

    settings = load_json_safe(target)
    ep = settings.setdefault("enabledPlugins", {})
    if ep.get(plugin_key) is True:
        if not quiet:
            info(f"{plugin_key} is already enabled in {target.name}.")
        return

    if dry_run:
        if not quiet:
            ok(f"Would enable {plugin_key} in {target}")
            if scope == "local":
                ok(f"Would remove {plugin_key} from user-level settings (so local takes effect)")
        return

    ep[plugin_key] = True
    save_json_safe(target, settings)
    if not quiet:
        ok(f"Enabled {plugin_key} in {target}")

    # Cascading: when enabling locally, REMOVE (not disable) at user level
    # so the local setting takes precedence. User True overrides local.
    if scope == "local":
        user_settings = load_json_safe(SETTINGS_TARGET)
        user_ep = user_settings.get("enabledPlugins", {})
        if plugin_key in user_ep:
            del user_ep[plugin_key]
            save_json_safe(SETTINGS_TARGET, user_settings)
            if not quiet:
                info(f"Removed {plugin_key} from user-level settings (local scope takes effect)")

    if not quiet:
        print("  Run /reload-plugins or restart Claude Code for changes to take effect.")


def do_disable(plugin_key: str, quiet: bool = False, dry_run: bool = False, scope: str = "user"):
    """Disable a plugin. Scope: 'user' → ~/.claude/settings.json, 'local' → project .claude/settings.local.json."""
    plugin_key = _resolve_plugin_key(plugin_key)
    if not _verify_plugin_installed(plugin_key):
        err(f"Plugin '{plugin_key}' is not installed. Nothing to disable.")
        sys.exit(1)

    target = _resolve_settings_file(scope)
    if scope == "local":
        target.parent.mkdir(parents=True, exist_ok=True)

    settings = load_json_safe(target)
    ep = settings.setdefault("enabledPlugins", {})
    if ep.get(plugin_key) is False:
        if not quiet:
            info(f"{plugin_key} is already disabled in {target.name}.")
        return

    if dry_run:
        if not quiet:
            ok(f"Would disable {plugin_key} in {target}")
        return

    ep[plugin_key] = False
    save_json_safe(target, settings)
    if not quiet:
        ok(f"Disabled {plugin_key} in {target}")
        print("  Run /reload-plugins or restart Claude Code for changes to take effect.")


# ── Lifecycle: Update ─────────────────────────────────────


def do_update(
    source_path: str,
    marketplace_name: Optional[str],
    force: bool = False,
    dry_run: bool = False,
    quiet: bool = False,
    plugin_dir: Optional[str] = None,
    follow_symlinks: bool = False,
):
    """Update a plugin by uninstalling the old version and reinstalling from a new source."""
    source = Path(source_path)
    if not source.exists():
        err(f"Not found: {source_path}")
        sys.exit(1)

    tmp_cleanup = None
    if source.is_dir():
        plugin_root = _resolve_plugin_root(source, plugin_dir, "directory")
    else:
        tmp_cleanup = tempfile.mkdtemp()
        tmp = Path(tmp_cleanup)
        try:
            extract_archive(source_path, tmp)
        except Exception:
            shutil.rmtree(tmp_cleanup, ignore_errors=True)
            raise
        try:
            plugin_root = _resolve_plugin_root(tmp, plugin_dir, "archive")
        except SystemExit:
            shutil.rmtree(tmp_cleanup, ignore_errors=True)
            raise

    try:
        meta = read_plugin_meta(plugin_root)
        plugin_name = meta["name"]
    finally:
        if tmp_cleanup:
            shutil.rmtree(tmp_cleanup, ignore_errors=True)

    if not marketplace_name:
        err("Marketplace name is required for update.")
        err("Usage: manage_plugin.py --update <source> <marketplace>")
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
        info(f"  Old version: {old_version}  ->  New version: {meta['version']}")

    if dry_run:
        if not quiet:
            ok("Would uninstall old version and reinstall from new source.")
            print(f"\n  {CYAN}Run without --dry-run to update.{NC}")
        return

    do_uninstall(plugin_key, quiet=True)
    do_install(
        source_path,
        marketplace_name,
        force=True,
        dry_run=False,
        quiet=quiet,
        plugin_dir=plugin_dir,
        follow_symlinks=follow_symlinks,
    )

    if not quiet:
        info(f"Updated from v{old_version} -> v{meta['version']}")


# ── Link plugin to an existing marketplace.json ────────────


def do_link_plugin(
    marketplace_path: str,
    plugin_spec: str,
    dry_run: bool = False,
    quiet: bool = False,
) -> None:
    """Append a plugin entry to an existing marketplace.json.

    plugin_spec forms:
      - ./path/to/plugin  (relative local path)
      - /abs/path         (absolute local path -> converted to relative)
      - owner/repo        (GitHub source)
    """
    mkt_root = Path(marketplace_path).resolve()
    mkt_json = mkt_root / ".claude-plugin" / "marketplace.json"
    if not mkt_json.exists():
        mkt_json_alt = mkt_root / "marketplace.json"
        if mkt_json_alt.exists():
            mkt_json = mkt_json_alt
        else:
            err(f"marketplace.json not found at {mkt_json}")
            sys.exit(1)

    mj = load_json_safe(mkt_json)
    plugins_list = mj.setdefault("plugins", [])

    # Resolve plugin_spec → source entry
    entry: dict
    if "/" in plugin_spec and not plugin_spec.startswith(("./", "/", "../")):
        # owner/repo form
        if plugin_spec.count("/") != 1:
            err(f"Invalid github spec '{plugin_spec}' — expected owner/repo")
            sys.exit(1)
        _owner, repo_name = plugin_spec.split("/", 1)
        entry = {
            "name": repo_name,
            "source": {"source": "github", "repo": plugin_spec},
        }
    else:
        # Local path form
        src = Path(plugin_spec).resolve()
        if not src.exists():
            err(f"Local plugin path not found: {src}")
            sys.exit(1)
        plug_json = src / ".claude-plugin" / "plugin.json"
        if not plug_json.exists():
            err(f"plugin.json not found at {plug_json}")
            sys.exit(1)
        pmeta = load_json_safe(plug_json)
        pname = pmeta.get("name")
        if not pname:
            err(f"plugin.json at {plug_json} has no 'name' field")
            sys.exit(1)
        try:
            rel = src.relative_to(mkt_root)
            rel_str = f"./{rel}"
        except ValueError:
            rel_str = str(src)
        entry = {
            "name": pname,
            "description": pmeta.get("description", ""),
            "version": pmeta.get("version", ""),
            "source": rel_str,
        }

    existing_names = {p.get("name") for p in plugins_list}
    if entry["name"] in existing_names:
        warn(f"Plugin '{entry['name']}' already in marketplace — replacing entry")
        plugins_list[:] = [p for p in plugins_list if p.get("name") != entry["name"]]
    plugins_list.append(entry)

    if dry_run:
        if not quiet:
            info(f"DRY RUN — would append: {json.dumps(entry, indent=2)}")
        return

    save_json_safe(mkt_json, mj, dry_run=False)
    if not quiet:
        ok(f"Linked '{entry['name']}' into marketplace at {mkt_json}")


# ── CLI entry point ───────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Plugin lifecycle management")
    parser.add_argument("source", nargs="?", help="Source directory or archive")
    parser.add_argument("marketplace", nargs="?", help="Marketplace name")
    parser.add_argument("--uninstall", type=str, help="Uninstall plugin (name@marketplace)")
    parser.add_argument("--update", action="store_true", help="Update instead of install")
    parser.add_argument("--enable", type=str, help="Enable plugin (name, name@marketplace, or name@owner/marketplace)")
    parser.add_argument(
        "--disable", type=str, help="Disable plugin (name, name@marketplace, or name@owner/marketplace)"
    )
    parser.add_argument(
        "--scope",
        choices=["user", "local"],
        default="user",
        help="'user' (default) = ~/.claude/settings.json, 'local' = <project>/.claude/settings.local.json",
    )
    parser.add_argument("--force", "-f", action="store_true", help="Force install despite errors")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview without changes")
    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")
    parser.add_argument(
        "--plugin-dir",
        type=str,
        default=None,
        help="In a monorepo with multiple plugin.json files, pick this path (absolute or relative to source)",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow symlinks and copy their target contents (default: skip symlinks with a warning)",
    )
    parser.add_argument(
        "--dev-link",
        action="store_true",
        help="Create a symlink from the marketplace dir to the live source (dev mode, reflects edits)",
    )
    parser.add_argument(
        "--link-plugin",
        nargs=2,
        metavar=("MARKETPLACE_PATH", "PLUGIN_SPEC"),
        help="Append a plugin entry to an existing marketplace.json (PLUGIN_SPEC is ./path OR owner/repo)",
    )
    parser.add_argument("--version", action="store_true", help="Show version")
    args = parser.parse_args()

    if args.version:
        print(f"manage_plugin.py v{TOOL_VERSION}")
        return
    if args.link_plugin:
        mkt_path_arg, plug_spec = args.link_plugin
        do_link_plugin(mkt_path_arg, plug_spec, dry_run=args.dry_run, quiet=args.quiet)
        return
    if args.uninstall:
        do_uninstall(args.uninstall, quiet=args.quiet, dry_run=args.dry_run)
    elif args.enable:
        do_enable(args.enable, quiet=args.quiet, dry_run=args.dry_run, scope=args.scope)
    elif args.disable:
        do_disable(args.disable, quiet=args.quiet, dry_run=args.dry_run, scope=args.scope)
    elif args.update:
        if not args.source:
            err("Source path required for update")
            sys.exit(1)
        do_update(
            args.source,
            args.marketplace,
            force=args.force,
            dry_run=args.dry_run,
            quiet=args.quiet,
            plugin_dir=args.plugin_dir,
            follow_symlinks=args.follow_symlinks,
        )
    elif args.source:
        do_install(
            args.source,
            args.marketplace,
            force=args.force,
            dry_run=args.dry_run,
            quiet=args.quiet,
            plugin_dir=args.plugin_dir,
            follow_symlinks=args.follow_symlinks,
            dev_link=args.dev_link,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
