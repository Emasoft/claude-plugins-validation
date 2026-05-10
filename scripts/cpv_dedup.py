#!/usr/bin/env python3
"""fclones-backed pre-scan deduplication for CPV.

This module is the brains of v2.48's "tree-scan-once" speedup. The dedup step
runs as the FIRST thing inside every CPV security scan, BEFORE any scanner
touches a file. It identifies duplicate files (cross-plugin shared READMEs,
vendored libs, identical SKILL.md templates, copy-pasted boilerplate) by
content hash, then deletes all but one canonical member from a hardlinked
staging tree. The scanners then walk only canonical files — paying scan cost
N times less, where N is the duplication factor of the corpus.

The dedup is content-aware (SHA hashes via fclones), not path-aware: two
files in different plugin subtrees that share identical bytes are dedup'd
together. After scanning, every finding emitted on a canonical file is
propagated to every original member's path so the per-plugin report still
sees its copy of the issue (no information loss — only scan-time savings).

Architecture notes:
  * fclones is invoked ONCE per scan (read-only `fclones group --format
    json`); we never use `fclones link/move/remove/dedupe` because we
    manage deletion ourselves to control the audit trail.
  * The dedup_map snapshot is taken BEFORE any deletion so bucketing can
    propagate findings back to ALL original members (even members whose
    staging hardlink was just deleted).
  * Hardlinks (not symlinks) are the dedup substrate: deleting a hardlink
    only decrements the inode's link count; the cache copy survives.
  * Graceful degradation: if fclones isn't installed (and the user has
    opted out of autoinstall via `CPV_NO_FCLONES_INSTALL=1`), every helper
    in this module is a no-op that returns empty/False — the scan proceeds
    against the un-deduped staging tree at full cost.

This module pairs with `cpv_staging.py` (which builds the hardlinked
staging tree and handles cross-fs fallbacks) and `cpv_install_scanners.py`
(which provides silent autoinstall of fclones at first scan).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

__all__ = [
    "DedupResult",
    "is_fclones_available",
    "run_fclones",
    "parse_dedup_groups",
    "apply_dedup",
    "bucket_canonical_to_members",
]


# ── Public types ────────────────────────────────────────────────────


@dataclass
class DedupResult:
    """Outcome of a dedup pass.

    Attributes:
        attempted: True if fclones was invoked. False when fclones isn't
            installed or the user opted out via env var.
        succeeded: True if fclones ran cleanly AND we built a dedup_map.
            Distinguishes "fclones missing" (attempted=False, succeeded=False)
            from "fclones ran but found no groups" (attempted=True,
            succeeded=True, dedup_map={}).
        dedup_map: ``{canonical_path: [all_member_paths]}``. Each list
            includes the canonical itself as its first element. Empty when
            no duplicate groups were found OR fclones didn't run.
        files_removed: Count of non-canonical hardlinks deleted from the
            staging tree by ``apply_dedup``. Zero before ``apply_dedup`` is
            called (the result of ``run_fclones`` alone has files_removed=0).
        bytes_saved: Total bytes reclaimed from the staging tree (sum of the
            file sizes of deleted hardlinks). Approximate: only counts the
            staging tree, not the underlying inode bytes (those persist as
            long as the cache hardlink count stays >= 1).
        fclones_elapsed_seconds: Wall-clock time fclones spent grouping the
            tree. Used by the scan-step table to surface dedup cost.
        skipped_reason: Human-readable reason when ``attempted`` is False.
            E.g. "fclones not on PATH; CPV_NO_FCLONES_INSTALL=1".
    """

    attempted: bool = False
    succeeded: bool = False
    dedup_map: dict[Path, list[Path]] = field(default_factory=dict)
    files_removed: int = 0
    bytes_saved: int = 0
    fclones_elapsed_seconds: float = 0.0
    skipped_reason: str = ""


# ── Probes ──────────────────────────────────────────────────────────


def is_fclones_available() -> bool:
    """True iff the ``fclones`` binary is on PATH for the current process."""
    return shutil.which("fclones") is not None


# ── fclones invocation ─────────────────────────────────────────────


_FCLONES_TIMEOUT_SECONDS = 600


def run_fclones(stage_root: Path) -> DedupResult:
    """Invoke ``fclones group --format json`` on ``stage_root``.

    Returns a DedupResult with ``dedup_map`` populated. The dedup map is the
    canonical input to ``apply_dedup`` (which performs the deletions) and to
    ``bucket_canonical_to_members`` (which propagates findings post-scan).

    No deletion happens here — this function is read-only with respect to
    the staging tree. ``run_fclones`` is safe to call even if you don't plan
    to call ``apply_dedup`` (e.g. for stats-only reports).

    Args:
        stage_root: Path to the staging tree to dedup. Must exist; otherwise
            DedupResult.skipped_reason describes the failure.

    Returns:
        A DedupResult. Inspect ``attempted`` + ``succeeded`` to distinguish:
          * (False, False) — fclones not installed / opted out / stage_root
            missing
          * (True,  False) — fclones invoked but failed (timeout, parse
            error, non-zero exit). ``skipped_reason`` describes the failure.
          * (True,  True ) — dedup_map is authoritative; safe to apply.
    """
    if not is_fclones_available():
        return DedupResult(
            attempted=False,
            succeeded=False,
            skipped_reason="fclones not on PATH; install via `cpv-doctor --install-scanners`",
        )

    if not stage_root.is_dir():
        return DedupResult(
            attempted=False,
            succeeded=False,
            skipped_reason=f"stage_root {stage_root} is not a directory",
        )

    import time

    start = time.perf_counter()
    try:
        result = subprocess.run(
            [
                "fclones",
                "group",
                str(stage_root),
                "--format",
                "json",
                "--hidden",  # include dotfiles (scanners may flag .env etc.)
                "--no-ignore",  # CPV must see EVERY file the scanners would see
            ],
            capture_output=True,
            text=True,
            timeout=_FCLONES_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return DedupResult(
            attempted=True,
            succeeded=False,
            fclones_elapsed_seconds=time.perf_counter() - start,
            skipped_reason=f"fclones invocation failed: {exc!r}",
        )
    elapsed = time.perf_counter() - start

    if result.returncode != 0:
        return DedupResult(
            attempted=True,
            succeeded=False,
            fclones_elapsed_seconds=elapsed,
            skipped_reason=(f"fclones exited {result.returncode}: {(result.stderr or '').strip()[:200]}"),
        )

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return DedupResult(
            attempted=True,
            succeeded=False,
            fclones_elapsed_seconds=elapsed,
            skipped_reason=f"fclones JSON parse failed: {exc}",
        )

    dedup_map = parse_dedup_groups(payload)
    return DedupResult(
        attempted=True,
        succeeded=True,
        dedup_map=dedup_map,
        fclones_elapsed_seconds=elapsed,
    )


# ── JSON parsing ─────────────────────────────────────────────────────


def parse_dedup_groups(payload: dict | list) -> dict[Path, list[Path]]:
    """Normalize fclones JSON output into a canonical dedup map.

    fclones emits one of these shapes depending on version:

    * Modern (``--format json`` since 0.30+):
        ``{"groups": [{"file_len": N, "file_hash": "...", "files": [{"path": "..."}, ...]}, ...]}``

    * Older (some 0.2x builds): array-of-arrays directly:
        ``[[ "/path/a", "/path/b" ], ...]``

    * Single-group output without the ``groups`` wrapper:
        ``{"files": [{"path": ...}, ...]}``

    This parser tolerates all three. The canonical (= first member of each
    group) is determined by lexicographic path order so two consecutive
    runs on the same tree produce identical bucketing — important for
    deterministic CI and report diffs.

    Returns:
        ``{canonical_path: [all_member_paths_including_canonical]}``. Groups
        of size 1 are skipped (no duplicates to dedup). Members of size > 1
        are sorted lexicographically; the first one is the canonical.
    """
    raw_groups: list[Iterable] = []
    if isinstance(payload, dict):
        groups = payload.get("groups")
        if isinstance(groups, list):
            raw_groups = groups
        elif "files" in payload:
            # Single-group flat shape.
            raw_groups = [payload]
    elif isinstance(payload, list):
        raw_groups = payload

    dedup_map: dict[Path, list[Path]] = {}
    for group in raw_groups:
        members = _extract_group_members(group)
        if len(members) <= 1:
            continue
        members.sort()
        canonical = members[0]
        dedup_map[canonical] = members
    return dedup_map


def _extract_group_members(group: object) -> list[Path]:
    """Return the list of Path members in one fclones group.

    Each ``files[i]`` may be either a string path or a dict with a ``path``
    key (modern format adds metadata like inode/mtime). We accept both.
    """
    if isinstance(group, dict):
        raw = group.get("files") or []
    elif isinstance(group, list):
        raw = group
    else:
        return []

    members: list[Path] = []
    for entry in raw:
        path: str | None = None
        if isinstance(entry, str):
            path = entry
        elif isinstance(entry, dict):
            path = entry.get("path") or entry.get("file") or entry.get("name")
        if isinstance(path, str) and path:
            members.append(Path(path))
    return members


# ── Apply dedup (delete non-canonical hardlinks) ───────────────────


def apply_dedup(
    dedup_map: dict[Path, list[Path]],
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Delete all non-canonical members in ``dedup_map`` from disk.

    Safe-by-design: the caller is expected to have built the staging tree
    via hardlinks (see ``cpv_staging.hardlink_tree``). Deleting a hardlink
    only removes the directory entry — the underlying inode survives as
    long as another hardlink points at it (typically the cache copy).

    Args:
        dedup_map: Output of ``parse_dedup_groups``. Each value is a list
            ``[canonical, dup_1, dup_2, ...]``.
        dry_run: When True, computes ``files_removed`` and ``bytes_saved``
            stats without actually deleting anything. Used by tests and
            by ``cpv-doctor --plan-dedup`` to preview a dedup operation.

    Returns:
        ``(files_removed, bytes_saved)``. Both zero when ``dedup_map`` is
        empty. Files that fail to delete (already gone, permission denied,
        directory entry vanished) are silently ignored — the dedup count
        reflects the actual disk state, not the intent.
    """
    files_removed = 0
    bytes_saved = 0
    for _canonical, members in dedup_map.items():
        for victim in members[1:]:  # skip index 0 (the canonical)
            try:
                size = victim.stat().st_size if victim.is_file() else 0
            except OSError:
                size = 0
            if dry_run:
                files_removed += 1
                bytes_saved += size
                continue
            try:
                victim.unlink()
                files_removed += 1
                bytes_saved += size
            except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
                # Best-effort: another worker may have already removed it,
                # or it might be a directory entry we shouldn't touch. Either
                # way, the intent (dedup) succeeds at the next layer (the
                # bucketing step uses the dedup_map snapshot, not on-disk
                # state).
                pass
    return files_removed, bytes_saved


# ── Finding bucketing (post-scan) ──────────────────────────────────


def bucket_canonical_to_members(
    finding_paths: Iterable[Path],
    dedup_map: dict[Path, list[Path]],
) -> dict[Path, list[Path]]:
    """Map each finding path to the list of original members it represents.

    After the scanners have run on the deduped staging tree, every finding
    references a canonical path (the surviving copy). This helper expands
    that path back into the full set of original member paths so the
    per-plugin report shows the finding under EVERY plugin that originally
    contained a copy.

    For a finding on a path that's NOT a canonical with duplicates, the
    output is ``{path: [path]}`` — a no-op identity mapping. Callers can
    treat the output uniformly (always iterate over the value list).

    Args:
        finding_paths: Iterable of paths from scanner findings (typically
            absolute paths under the staging root).
        dedup_map: Output of ``parse_dedup_groups``.

    Returns:
        ``{finding_path: [original_paths_to_emit_for]}``. The list contains
        the input path itself for non-canonical inputs (or canonicals
        without duplicates); for canonicals with duplicates, it contains
        every member of the group.
    """
    expanded: dict[Path, list[Path]] = {}
    for path in finding_paths:
        members = dedup_map.get(path)
        if members:
            expanded[path] = list(members)
        else:
            expanded[path] = [path]
    return expanded


# ── Convenience CLI for spot-checks ────────────────────────────────


def _cli_main(argv: list[str]) -> int:
    """Tiny CLI for `python -m cpv_dedup <stage_root>`.

    Prints a one-line summary plus the dedup map (one group per line)
    so a developer can spot-check what fclones found before wiring the
    dedup into a real scan.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="cpv-dedup",
        description="Run fclones on a staging tree and print the dedup map.",
    )
    parser.add_argument("stage_root", type=Path)
    parser.add_argument("--apply", action="store_true", help="Actually delete duplicates (default: dry-run).")
    args = parser.parse_args(argv)

    result = run_fclones(args.stage_root)
    if not result.attempted:
        print(f"[skip] {result.skipped_reason}")
        return 1
    if not result.succeeded:
        print(f"[fail] {result.skipped_reason}")
        return 2

    print(f"fclones found {len(result.dedup_map)} duplicate group(s) in {result.fclones_elapsed_seconds:.2f}s")
    for canonical, members in sorted(result.dedup_map.items()):
        print(f"  canonical={canonical}  dup_count={len(members) - 1}")

    if result.dedup_map:
        files, bytes_ = apply_dedup(result.dedup_map, dry_run=not args.apply)
        verb = "would remove" if not args.apply else "removed"
        print(f"{verb} {files} file(s) ({bytes_} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(_cli_main(sys.argv[1:]))


# Suppress "unused import" warning when os is only used in tests/CLI futures
_ = os
