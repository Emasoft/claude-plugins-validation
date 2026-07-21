#!/usr/bin/env python3
"""Compute SHA256 hashes of every CPV file eligible for self-scan exclusion.

The CPV security validator skips its own pattern-defining source files
(validator scripts, cpv-fix-validation references, security tests) when
scanning the CPV plugin itself. Without integrity protection, any file
named like a CPV file would be skipped — name-based detection is
spoofable.

This script computes SHA256 hashes of every file that would be skipped
in CPV self-scan mode and writes them to BOTH `.plugin-self-hashes.json`
(canonical, per TRDD-bbff5bc5) AND `.cpv-self-hashes.json` (legacy,
shipped for one release for backward compat with v2.50.x cached
clients). Both files contain bytes-identical content.

The validator then verifies each candidate file's hash against the
manifest before skipping. Hash mismatch → file gets scanned normally.

Run before every commit / push. The publish.py pipeline calls this as
part of Gate 9.

Usage:
    uv run python scripts/_plugin_compute_hashes.py [<plugin_root>]

Default plugin root is the parent of `scripts/`. Writes BOTH manifests
to `<plugin_root>/`. Exit code 0 on success.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Re-use the validator's own classification helpers so this script
# stays in lockstep with what cpv_self_scan_skip() actually skips.
SCRIPTS_DIR = Path(__file__).parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from validate_security import (  # noqa: E402
    is_security_fix_reference,
    is_validator_script,
)

MANIFEST_NAME_NEW = ".plugin-self-hashes.json"  # canonical (TRDD-bbff5bc5)
MANIFEST_NAME_LEGACY = ".cpv-self-hashes.json"  # removed in v2.53.0
MANIFEST_VERSION = 1  # schema v1; v2 lands in v2.53.0

# Set of basenames the manifest writer must NEVER hash itself.
_MANIFEST_BASENAMES = frozenset({MANIFEST_NAME_NEW, MANIFEST_NAME_LEGACY})


def is_self_scan_eligible(rel_path: str) -> bool:
    """Mirror of `cpv_self_scan_skip` minus the runtime active-flag check.

    Used to enumerate which files NEED a hash entry in the manifest. Must
    stay in sync with `validate_security._is_self_scan_eligible`.
    """
    if is_validator_script(rel_path):
        return True
    if is_security_fix_reference(rel_path):
        return True
    file_normalized = rel_path.lower().replace("\\", "/")
    basename = file_normalized.rsplit("/", 1)[-1] if "/" in file_normalized else file_normalized
    if basename.startswith("test_") and basename.endswith(".py"):
        return True
    if "/tests/fixtures/" in file_normalized or file_normalized.startswith("tests/fixtures/"):
        return True
    # v2.99.1 — CPV rule catalogs (e.g. scripts/rules/skillaudit_patterns.json
    # — moved into scripts/ in v2.99.3 so the file ships in the wheel under
    # hatchling's packages=["scripts"], issue #32).
    # MUST stay in lockstep with validate_security._is_self_scan_eligible.
    if ("/rules/" in file_normalized or file_normalized.startswith("rules/")) and basename.endswith(".json"):
        return True
    # v2.99.1 — root-level documentation files (README, CHANGELOG, etc.).
    # Mirror validate_security._is_self_scan_eligible.
    if basename in {
        "readme.md",
        "changelog.md",
        "shiplog.md",
        "contributing.md",
        "security.md",
        "code_of_conduct.md",
        "support.md",
    } and "/" not in file_normalized.lstrip("./"):
        return True
    # v2.99.1 — root-level references/ + design/audits/.
    if file_normalized.startswith("references/") and basename.endswith(".md"):
        return True
    if file_normalized.startswith("design/audits/") and basename.endswith(".md"):
        return True
    # v2.99.1 — CPV's own .github/workflows.
    if file_normalized.startswith(".github/workflows/") and (basename.endswith(".yml") or basename.endswith(".yaml")):
        return True
    if "/cpv-semantic-validation-skill/references/" in file_normalized:
        return True
    if (
        ("/skills/" in file_normalized or file_normalized.startswith("skills/"))
        and "/references/" in file_normalized
        and basename.endswith((".md", ".py", ".sh", ".js", ".ts", ".json", ".yaml", ".yml"))
    ):
        return True
    if ("/agents/" in file_normalized or file_normalized.startswith("agents/")) and basename.endswith(".md"):
        return True
    if ("/commands/" in file_normalized or file_normalized.startswith("commands/")) and basename.endswith(".md"):
        return True
    if ("/skills/" in file_normalized or file_normalized.startswith("skills/")) and basename.endswith(".md"):
        return True
    if "/templates/" in file_normalized or file_normalized.startswith("templates/"):
        return True
    if "/design/tasks/" in file_normalized and basename.startswith("trdd-"):
        return True
    if "/docs_dev/" in file_normalized:
        return True
    return False


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_tracked_files(plugin_root: Path) -> set[str] | None:
    """Return the set of git-tracked files (relative paths) for plugin_root.

    The manifest MUST only include files that exist in the published git
    repo — otherwise CI's fresh checkout will hash a smaller fileset than
    the local manifest expects, and `verify_self_integrity` flags the
    delta as a tampered/deleted file. Local-only files (`.DS_Store`,
    `.idea/*`, `.vscode/*`, build artifacts that escape `skip_dirs`)
    silently land in the manifest when developer-machine state diverges
    from the gitignore.

    Returns None if `git ls-files` is unavailable — in which case we
    fall back to the directory walk + skip_dirs heuristic.
    """
    import subprocess

    try:
        # `-z` is mandatory, not cosmetic:
        #   * It disables `core.quotePath`, so a tracked file with a
        #     non-ASCII name (e.g. `café.md`) is emitted as raw bytes
        #     instead of the default octal-escaped, double-quote-wrapped
        #     form (`"caf\303\251.md"`). A quoted name would not match the
        #     real file in `compute_manifest` (`is_file()` → False) and the
        #     shipped file would be silently LEFT UNHASHED — exactly the
        #     "one unhashed shipped file is a poisoning vector" failure the
        #     exhaustive manifest exists to prevent — while
        #     `_detect_added_files` would simultaneously raise a spurious
        #     integrity CRITICAL for the same file.
        #   * NUL termination also makes a filename containing a newline
        #     parse correctly; the previous `.splitlines()` split such a
        #     path into two bogus entries.
        # Read raw bytes (no text=True) and decode UTF-8 so the result is
        # locale-independent — git stores paths as bytes and the on-disk
        # names round-trip through pathlib as UTF-8.
        result = subprocess.run(
            ["git", "-C", str(plugin_root), "ls-files", "-z"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return None
        decoded = result.stdout.decode("utf-8", errors="surrogateescape")
        return {entry for entry in decoded.split("\0") if entry}
    except (OSError, subprocess.SubprocessError):
        return None


# Directories that never ship (build artifacts, caches, dev-scratch). Used
# ONLY by the non-git walk fallback — `git ls-files` already excludes
# gitignored dirs. Single source of truth for both the manifest builder and
# verify_self_integrity's added-file detection.
_SHIPPED_WALK_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "reports",
        "reports_dev",
        "downloads_dev",
        "libs_dev",
        "builds_dev",
        "samples_dev",
        "scripts_dev",
        "tests_dev",
        "examples_dev",
        "docs_dev",
        # CPV/plugin RUNTIME-STATE dirs created during a scan — never shipped,
        # always gitignored. On a fresh (non-git) INSTALL the FS-walk fallback
        # would otherwise hash these and verify_self_integrity would flag them as
        # "added" files, aborting EVERY scan on a clean install (GitHub issue #66:
        # `.in_use/` PID-lock files). `git ls-files` already excludes them on a
        # source checkout; this list fixes the installed (non-git) case.
        ".in_use",
        ".trashcan",
        ".rechecker",
        ".audit",
        ".cpv-cache",
        ".scan-cache",
    }
)

# Runtime / OS cruft that can appear inside an install dir but is never a
# shipped (git-tracked) file. Excluded from the non-git walk so the manifest
# never hashes them AND verify_self_integrity's added-file detection never
# false-flags a developer's `.DS_Store` or a stray `.pyc` as an inoculated
# file. The git path needs no such list — `git ls-files` excludes them.
#
# `.orphaned_at` (GitHub issue #70-C): the Claude Code plugin-cache HOST — NOT
# CPV — drops this marker into a `<cache>/<plugin>/<version>/` dir once a newer
# version supersedes it. When the orphaned CPV version then runs its own
# self-integrity check, the FS-walk fallback would see `.orphaned_at` (absent
# from the canonical manifest) and abort with a CRITICAL "added/inoculated
# file". It is a host-generated, never-executed timestamp marker — CPV neither
# writes, reads, nor loads it — so skipping this one exact basename cannot
# weaken tamper-detection of CPV's executable surface: any added .py / skill /
# agent / hook is still caught (proved by the scoped-fix test). Same family as
# the closed #66 `.in_use` PID-lock fix.
_RUNTIME_CRUFT_BASENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", ".orphaned_at"})
_RUNTIME_CRUFT_SUFFIXES = (".pyc", ".pyo", ".pyd", ".swp", ".swo", ".swn", ".orig", ".bak", ".tmp")


def _is_runtime_cruft(rel_path: str) -> bool:
    """True for OS/editor/runtime files that are never part of the shipped set."""
    name = rel_path.rsplit("/", 1)[-1]
    if name in _RUNTIME_CRUFT_BASENAMES:
        return True
    if name.endswith("~"):  # editor backup files (foo.py~)
        return True
    if name == ".coverage" or name.startswith(".coverage."):
        return True
    return rel_path.endswith(_RUNTIME_CRUFT_SUFFIXES)


def enumerate_shipped_files(plugin_root: Path) -> set[str]:
    """Return the relative paths of every shipped (git-tracked) file.

    SINGLE SOURCE OF TRUTH for "what ships" (TRDD-b8c6d04f). Used by BOTH
    `compute_manifest` (hash each entry) AND
    `_plugin_verify_hashes.verify_self_integrity` (added-file detection:
    any on-disk file NOT in the manifest is an added/inoculated file).
    Because both sides enumerate identically, runtime cruft / caches /
    gitignored files are excluded symmetrically and never false-flag.

    Dual path, mirroring how a plugin is shipped:
      * `git ls-files` when plugin_root is a git repo — the exact shipped set.
      * a directory walk minus `_SHIPPED_WALK_SKIP_DIRS` and runtime cruft
        when it is not (uvx / tarball / sparse install with no `.git`).

    The two manifest files are always excluded — they cannot contain their
    own hash (chicken-and-egg), so they are neither hashed nor flagged.
    """
    out: set[str] = set()
    tracked = _git_tracked_files(plugin_root)
    if tracked is not None:
        for rel_path in tracked:
            if Path(rel_path).name in _MANIFEST_BASENAMES:
                continue
            out.add(rel_path)
        return out
    # Fallback: not a git repo. Walk the tree, excluding never-shipped dirs
    # and runtime cruft. No eligibility filter — exhaustive by design.
    for path in plugin_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(plugin_root)
        if any(part in _SHIPPED_WALK_SKIP_DIRS for part in rel.parts):
            continue
        rel_path = str(rel).replace("\\", "/")
        if rel.name in _MANIFEST_BASENAMES:
            continue
        if _is_runtime_cruft(rel_path):
            continue
        out.add(rel_path)
    return out


def compute_manifest(plugin_root: Path) -> dict[str, object]:
    """Hash EVERY shipped (git-tracked) file under plugin_root.

    SECURITY (TRDD-b8c6d04f): the manifest is EXHAUSTIVE over the shipped
    fileset. One unhashed shipped file is a poisoning vector, so every
    git-tracked file is hashed — minus the two manifest files themselves,
    which cannot contain their own hash (chicken-and-egg; their integrity
    comes from the GitHub-anchored manifest comparison, not a self-hash).
    Gitignored files are not part of the plugin and are excluded.

    `verify_self_integrity` rejects any tracked file added / modified /
    deleted relative to this manifest, and `cpv_self_scan_skip` skips a file
    ONLY when its SHA256 matches an entry here.

    `git ls-files` is the source of truth for "what ships". Falls back to a
    directory walk (minus build/cache/dev-scratch dirs) only when the target
    is not a git repo.
    """
    files: dict[str, str] = {}
    # Hash every shipped file. enumerate_shipped_files is the single source of
    # truth (git-tracked when a repo, else walk minus skip-dirs/cruft) shared
    # with verify_self_integrity's added-file detection, so the manifest and the
    # verifier agree exactly on the shipped set.
    for rel_path in sorted(enumerate_shipped_files(plugin_root)):
        path = plugin_root / rel_path
        if not path.is_file():
            # Tracked but absent locally (e.g. sparse checkout) — nothing to
            # hash here; verify_self_integrity flags genuine deletions.
            continue
        try:
            digest = sha256_of_file(path)
        except (OSError, PermissionError):
            continue
        files[rel_path] = f"sha256:{digest}"

    return {
        "version": MANIFEST_VERSION,
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": (
            "Exhaustive SHA256 manifest of every shipped (git-tracked) file in "
            "the plugin. verify_self_integrity rejects any tracked file added, "
            "modified, or deleted relative to this manifest; CPV self-scan skips "
            "a file ONLY when its hash matches an entry here. One unhashed "
            "shipped file would be a poisoning vector, so the manifest is "
            "exhaustive (TRDD-b8c6d04f)."
        ),
        "files": dict(sorted(files.items())),
    }


def _atomic_write(out_path: Path, payload: str) -> None:
    """Write payload to out_path atomically and durably (tmp + fsync + rename).

    fsync the tmp file's contents to disk BEFORE the rename so a power loss
    between the buffered write and the rename cannot leave an empty/partial
    file renamed into place. The manifest is written right before a
    commit/push gate, so durability matters — this mirrors the same
    flush()+os.fsync()+os.replace() guarantee that cpv_scanner_cache.put()
    already provides for the result cache.
    """
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    # Use a real fd so flush()+fsync() push the bytes to the platter before
    # the atomic rename. os.replace() is atomic on POSIX and NTFS alike.
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, out_path)


def write_manifest(plugin_root: Path, manifest: dict[str, object]) -> tuple[Path, Path]:
    """Write the manifest atomically to BOTH the new and legacy filenames.

    Per TRDD-bbff5bc5 §2.2, both files ship in v2.51.0 with bytes-identical
    content so v2.50.x cached clients (which only know the legacy name)
    keep working until v2.53.0 deletes the legacy file.

    Returns (new_path, legacy_path). Both are atomically replaced via tmp+rename.
    """
    payload = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    new_path = plugin_root / MANIFEST_NAME_NEW
    legacy_path = plugin_root / MANIFEST_NAME_LEGACY
    _atomic_write(new_path, payload)
    _atomic_write(legacy_path, payload)
    return new_path, legacy_path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args:
        plugin_root = Path(args[0]).resolve()
    else:
        plugin_root = SCRIPTS_DIR.parent.resolve()

    if not plugin_root.is_dir():
        print(f"ERROR: plugin root not found: {plugin_root}", file=sys.stderr)
        return 1

    manifest = compute_manifest(plugin_root)
    new_path, legacy_path = write_manifest(plugin_root, manifest)
    files_block = manifest["files"]
    file_count = len(files_block) if isinstance(files_block, dict) else 0
    print(f"Wrote {new_path} ({file_count} hashes)")
    print(f"Wrote {legacy_path} ({file_count} hashes, compat copy)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
