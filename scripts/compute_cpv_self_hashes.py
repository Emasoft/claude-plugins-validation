#!/usr/bin/env python3
"""Compute SHA256 hashes of every CPV file eligible for self-scan exclusion.

The CPV security validator skips its own pattern-defining source files
(validator scripts, fix-validation references, security tests) when
scanning the CPV plugin itself. Without integrity protection, any file
named like a CPV file would be skipped — name-based detection is
spoofable.

This script computes SHA256 hashes of every file that would be skipped
in CPV self-scan mode and writes them to `.cpv-self-hashes.json`. The
validator then verifies each candidate file's hash against the manifest
before skipping. Hash mismatch → file gets scanned normally.

Run before every commit / push. The publish.py pipeline calls this as
part of its pre-push gate.

Usage:
    uv run python scripts/compute_cpv_self_hashes.py [<plugin_root>]

Default plugin root is the parent of `scripts/`. Writes the manifest
to `<plugin_root>/.cpv-self-hashes.json`. Exit code 0 on success.
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

MANIFEST_NAME = ".cpv-self-hashes.json"
MANIFEST_VERSION = 1


def is_self_scan_eligible(rel_path: str) -> bool:
    """Mirror of `cpv_self_scan_skip` minus the runtime active-flag check.

    Used to enumerate which files NEED a hash entry in the manifest. Must
    stay in sync with `validate_security.cpv_self_scan_skip`.
    """
    if is_validator_script(rel_path):
        return True
    if is_security_fix_reference(rel_path):
        return True
    file_normalized = rel_path.lower().replace("\\", "/").lstrip("/")
    if file_normalized.startswith("tests/"):
        basename = file_normalized.rsplit("/", 1)[-1]
        if basename.startswith(
            ("test_validate_security", "test_phase", "test_fp_reduction")
        ):
            return True
    if "/semantic-validation-skill/references/" in ("/" + file_normalized):
        return True
    return False


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_manifest(plugin_root: Path) -> dict[str, object]:
    """Walk plugin_root, hash every self-scan-eligible file, return manifest dict."""
    files: dict[str, str] = {}

    # Skip these dirs entirely — never useful to hash venvs, build artifacts,
    # cache, git internals.
    skip_dirs = {
        ".git", ".venv", "venv", "__pycache__", "node_modules",
        "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "reports", "reports_dev", "downloads_dev", "libs_dev", "builds_dev",
        "samples_dev", "scripts_dev", "tests_dev", "examples_dev", "docs_dev",
    }

    for path in plugin_root.rglob("*"):
        if not path.is_file():
            continue
        # Filter out anything inside a skipped directory.
        rel = path.relative_to(plugin_root)
        if any(part in skip_dirs for part in rel.parts):
            continue
        rel_path = str(rel)
        if not is_self_scan_eligible(rel_path):
            continue
        # Never hash the manifest itself.
        if rel.name == MANIFEST_NAME:
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


def write_manifest(plugin_root: Path, manifest: dict[str, object]) -> Path:
    """Write the manifest atomically (tmp + rename)."""
    out_path = plugin_root / MANIFEST_NAME
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    payload = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(out_path)
    return out_path


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
    out_path = write_manifest(plugin_root, manifest)
    files_block = manifest["files"]
    file_count = len(files_block) if isinstance(files_block, dict) else 0
    print(f"Wrote {out_path} ({file_count} hashes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
