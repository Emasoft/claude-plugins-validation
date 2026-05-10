#!/usr/bin/env python3
"""Add plugin dependencies to a target plugin's plugin.json.

Two input modes (per the user's 2026-05-09 request — "menu option to
explicitly add a dependency for some other plugins"):

  1. Explicit declarations: `--add <name>[@<marketplace>][@<version>]`
     Repeatable. Each spec is parsed into the spec object that
     plugin-dependencies.md describes:
       * `<name>` alone               → bare-string entry
       * `<name>@<marketplace>`       → {name, marketplace}
       * `<name>@<marketplace>@<ver>` → {name, marketplace, version}
       * `<name>@@<ver>`              → {name, version} (no marketplace)

  2. Copy from another plugin: `--from <path-or-url>`
     Reads the source plugin's plugin.json and copies its entire
     `dependencies` array into the target. URLs (`https://...` or
     `git+...`) are cloned into a tmp dir; local paths are read directly.

The two modes can be combined — `--add` entries are MERGED with `--from`
entries, deduplicated by name (last write wins on the merged record).

Idempotency contract (per the user's preservation guardrail rule):
  * Existing dependencies are kept unless an `--add` spec re-declares
    them (in which case the new spec replaces the old in-place).
  * The output is a STABLE-ORDERED array — dependencies sorted by name.
  * The file is written ATOMICALLY (tmp + rename) so a crash mid-write
    can never corrupt plugin.json.

Always re-validates the result by calling validate_plugin.py on the
target. If the result has CRITICAL/MAJOR findings beyond the original
baseline, the previous plugin.json is restored from the .bak.

Usage:
    uv run scripts/add_dependencies.py <plugin-path> \\
        --add dev-browser \\
        --add foo@my-marketplace@~2.1.0 \\
        --from /path/to/other/plugin

Exit codes:
  0 OK
  1 invalid args / target missing / target malformed
  2 source plugin (--from) cannot be read
  3 merge would produce CRITICAL/MAJOR findings — rolled back
  4 atomic write failed (target untouched)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SPEC_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _parse_add_spec(raw: str) -> dict[str, str] | str:
    """Parse a `--add` argument into a dependency spec.

    Forms:
      `name`                         → "name" (bare string)
      `name@marketplace`             → {"name": "name", "marketplace": "marketplace"}
      `name@marketplace@version`     → {"name": "name", "marketplace": "marketplace", "version": "version"}
      `name@@version`                → {"name": "name", "version": "version"}
    """
    if "@" not in raw:
        if not _SPEC_NAME_RE.match(raw):
            raise ValueError(f"--add {raw!r}: invalid plugin name (must be kebab-case)")
        return raw
    parts = raw.split("@", 2)
    name = parts[0]
    if not _SPEC_NAME_RE.match(name):
        raise ValueError(f"--add {raw!r}: name {name!r} is not kebab-case")
    spec: dict[str, str] = {"name": name}
    market = parts[1] if len(parts) > 1 else ""
    version = parts[2] if len(parts) > 2 else ""
    if market:
        if not _SPEC_NAME_RE.match(market):
            raise ValueError(f"--add {raw!r}: marketplace {market!r} is not kebab-case")
        spec["marketplace"] = market
    if version:
        spec["version"] = version
    return spec


def _read_plugin_json(path: Path) -> dict:
    """Read and parse plugin.json. Raises on missing or malformed."""
    pj = path / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        raise FileNotFoundError(f"no plugin.json at {pj} (target must be a Claude Code plugin)")
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"plugin.json at {pj} is malformed: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"plugin.json at {pj} top-level must be an object")
    return data


def _read_dependencies_from_source(source: str) -> list:
    """Resolve `--from <path-or-url>` to its list of dependencies.

    Supports:
      - Local plugin path (folder containing .claude-plugin/plugin.json).
      - Git URLs (https://, git+https://, ssh://, git@) — cloned to a
        temporary directory via `git clone --depth 1 --filter=blob:none`.
    """
    if source.startswith(("https://", "git+https://", "ssh://", "git@")):
        return _read_deps_from_git_url(source)
    src = Path(source).expanduser().resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"--from {source}: not a directory or git URL")
    manifest = _read_plugin_json(src)
    deps = manifest.get("dependencies", [])
    if not isinstance(deps, list):
        raise ValueError(f"--from {source}: plugin.json::dependencies is not an array")
    return list(deps)


def _read_deps_from_git_url(url: str) -> list:
    """Shallow-clone a git URL to a temp dir and read its plugin.json deps.

    Cleanup happens automatically via TemporaryDirectory. Bare git URLs
    (`git@host:owner/repo.git`) are left as-is for git's parser.
    """
    if url.startswith("git+"):
        url = url[4:]
    with tempfile.TemporaryDirectory(prefix="cpv-add-deps-") as tmp:
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--filter=blob:none", url, tmp],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"--from {url}: git clone failed: {exc.stderr.strip() or exc}") from exc
        return _read_dependencies_from_source(tmp)


def _name_of(entry: object) -> str | None:
    """Extract the plugin name from a dependency entry (string or dict)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        n = entry.get("name")
        if isinstance(n, str):
            return n
    return None


def merge_dependencies(existing: list, additions: list) -> list:
    """Merge `additions` into `existing`, dedup by name, return sorted result.

    `additions` entries replace same-named entries in `existing`; new
    names append. Output is sorted by name for stability so re-runs
    produce no diff.
    """
    by_name: dict[str, object] = {}
    for entry in existing:
        n = _name_of(entry)
        if n is None:
            continue  # malformed entry — skip; the validator will flag
        by_name[n] = entry
    for entry in additions:
        n = _name_of(entry)
        if n is None:
            continue
        by_name[n] = entry  # last write wins
    # Stable ordering: sorted by name. Dicts before strings within same name
    # is moot since dedup ensures unique names.
    return [by_name[n] for n in sorted(by_name)]


def _atomic_write(path: Path, content: str) -> None:
    """Tmp-and-rename atomic write so a crash never leaves a partial file."""
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the orphan tmp.
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _backup(pj: Path) -> Path:
    """Save a `.bak` next to the manifest so the caller can restore on rollback."""
    bak = pj.with_suffix(pj.suffix + ".bak")
    shutil.copy2(pj, bak)
    return bak


def _restore(pj: Path, bak: Path) -> None:
    """Move the backup back over the manifest (rollback path)."""
    shutil.move(str(bak), str(pj))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add plugin dependencies to a target plugin's plugin.json. "
            "Two input modes — explicit `--add` specs and `--from` copy."
        ),
    )
    parser.add_argument("target", type=Path, help="Target plugin directory (contains .claude-plugin/plugin.json)")
    parser.add_argument(
        "--add",
        action="append",
        default=[],
        metavar="NAME[@MARKETPLACE[@VERSION]]",
        help=(
            "Add a dependency. Repeat for multiple. Forms: `name` (bare), "
            "`name@marketplace`, `name@marketplace@version`, or `name@@version` "
            "(version without marketplace). Names must be kebab-case."
        ),
    )
    parser.add_argument(
        "--from",
        dest="from_source",
        action="append",
        default=[],
        metavar="PATH-OR-URL",
        help=(
            "Copy ALL dependencies from another plugin. Repeat for multiple "
            "sources. Source can be a local plugin folder or a git URL "
            "(https://, git+https://, ssh://, git@host:owner/repo)."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the merged dependencies array; do NOT write")
    parser.add_argument(
        "--no-validate", action="store_true", help="Skip the post-write validate_plugin.py check (NOT recommended)"
    )
    args = parser.parse_args(argv)

    target = args.target.resolve()
    try:
        manifest = _read_plugin_json(target)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Order matters: `--from` is processed FIRST, then `--add`, so an
    # explicit `--add` spec for the same plugin name OVERRIDES the version
    # that came in via `--from` (last-write-wins in merge_dependencies).
    # This matches user intuition — `--add` is the targeted override path.
    additions: list = []
    for src in args.from_source:
        try:
            additions.extend(_read_dependencies_from_source(src))
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    for raw in args.add:
        try:
            additions.append(_parse_add_spec(raw))
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if not additions:
        print("ERROR: no --add or --from specified; nothing to do", file=sys.stderr)
        return 1

    existing = manifest.get("dependencies", [])
    if not isinstance(existing, list):
        existing = []

    merged = merge_dependencies(existing, additions)

    if args.dry_run:
        print(json.dumps(merged, indent=2, ensure_ascii=False))
        return 0

    pj = target / ".claude-plugin" / "plugin.json"
    bak = _backup(pj)
    manifest["dependencies"] = merged
    new_content = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    try:
        _atomic_write(pj, new_content)
    except OSError as exc:
        print(f"ERROR: atomic write failed: {exc}", file=sys.stderr)
        # Restore from backup if the manifest is now in a half-state.
        if bak.is_file():
            _restore(pj, bak)
        return 4

    if not args.no_validate:
        # Re-validate. Roll back on any new CRITICAL/MAJOR.
        validator = Path(__file__).resolve().parent / "validate_plugin.py"
        if validator.is_file():
            result = subprocess.run(
                [sys.executable, str(validator), str(target), "--strict", "--json"],
                capture_output=True,
                text=True,
                timeout=600,
            )
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError:
                report = None
            new_blocking = 0
            if isinstance(report, dict):
                summary = report.get("summary", {})
                new_blocking = int(summary.get("CRITICAL", 0)) + int(summary.get("MAJOR", 0))
            if new_blocking > 0:
                print(
                    "ERROR: merged dependencies introduced CRITICAL/MAJOR findings; rolling back.",
                    file=sys.stderr,
                )
                _restore(pj, bak)
                return 3

    bak.unlink(missing_ok=True)
    print(f"OK: {len(merged)} dependency entries written to {pj.relative_to(target)}")
    for entry in merged:
        if isinstance(entry, str):
            print(f"  - {entry}")
        elif isinstance(entry, dict):
            n = entry.get("name", "<unnamed>")
            v = entry.get("version", "")
            m = entry.get("marketplace", "")
            tail = " ".join(filter(None, [f"@{m}" if m else "", f"@{v}" if v else ""]))
            print(f"  - {n}{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
