#!/usr/bin/env python3
"""Compute SHA256 hashes of every CPV file eligible for self-scan exclusion.

The CPV security validator skips its own pattern-defining source files
(validator scripts, fix-validation references, security tests) when
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
    if "/semantic-validation-skill/references/" in file_normalized:
        return True
    if "/skills/" in file_normalized and "/references/" in file_normalized and basename.endswith(".md"):
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
        result = subprocess.run(
            ["git", "-C", str(plugin_root), "ls-files"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return None
        # ls-files emits POSIX-separated paths, one per line.
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        return None


def compute_manifest(plugin_root: Path) -> dict[str, object]:
    """Walk plugin_root, hash every self-scan-eligible file, return manifest dict.

    Honours `git ls-files` so untracked / gitignored files (`.DS_Store`,
    macOS metadata, IDE state) never enter the manifest. The published
    manifest must only describe files that exist in the public repo, or
    a clean clone (CI's fresh checkout, or any user pulling the plugin)
    will fail integrity verification on the missing-locally delta.
    """
    files: dict[str, str] = {}

    # Skip these dirs entirely — never useful to hash venvs, build artifacts,
    # cache, git internals.
    skip_dirs = {
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
    }

    tracked = _git_tracked_files(plugin_root)

    for path in plugin_root.rglob("*"):
        if not path.is_file():
            continue
        # Filter out anything inside a skipped directory.
        rel = path.relative_to(plugin_root)
        if any(part in skip_dirs for part in rel.parts):
            continue
        rel_path = str(rel).replace("\\", "/")
        if not is_self_scan_eligible(rel_path):
            continue
        # Never hash either manifest file itself.
        if rel.name in _MANIFEST_BASENAMES:
            continue
        # If git ls-files succeeded, only include tracked files. This is
        # the contract: the published manifest describes the published
        # source. Untracked local files (`.DS_Store`, IDE droppings)
        # never enter the manifest, so they can never cause an
        # integrity-mismatch FP on a clean checkout.
        if tracked is not None and rel_path not in tracked:
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
            "Hash manifest of files the CPV security validator skips during "
            "self-scan mode. The validator verifies each file's actual SHA256 "
            "against this manifest before skipping. Hash mismatch → the file "
            "gets scanned normally, defeating name-only spoofing."
        ),
        "files": dict(sorted(files.items())),
    }


def _atomic_write(out_path: Path, payload: str) -> None:
    """Write payload to out_path atomically (tmp + rename, same dir)."""
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(out_path)


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
