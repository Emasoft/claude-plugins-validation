#!/usr/bin/env python3
"""Hardlink-backed staging trees for the v2.48 universal scan pipeline.

This module is the substrate that the dedup pipeline (``cpv_dedup``) and
the marketplace bulk scanner (Phase 4) both build on. Every CPV scan flows
through this module:

  1. ``stage_target(target)`` clones the target into a tmpdir using
     hardlinks. Hardlinks share inodes with the source — they're zero-copy
     and zero-additional-disk-bytes when same-fs.
  2. The dedup step (``cpv_dedup.apply_dedup``) deletes non-canonical
     hardlinks from staging. Because hardlinks share inodes, this is SAFE
     w.r.t. the original target — only the staging directory entry is
     gone; the inode persists with hardlink count >= 1.
  3. Scanners walk the deduped staging tree.
  4. ``cleanup_staging(stage_root)`` runs in a finally block to remove
     the tmpdir.

Key design decisions:
  * **Hardlinks > symlinks**: scanners handle symlinks inconsistently
    (some follow, some skip, controlled by per-tool flags). Hardlinks are
    transparent — every scanner sees a regular file at the staging path.
  * **Cross-fs fallback**: `os.link()` raises EXDEV when source and dest
    are on different filesystems. We fall back to file copies (slow but
    safe) for trees < 100 MiB; for larger trees we fall back to symlinks
    + skip dedup (the staging path still works for scanners; only the
    dedup-by-deletion is skipped).
  * **Same-target idempotence**: stage_target builds a fresh tmpdir on
    every call. We never reuse a stale stage tree — that's how scanner
    output gets contaminated.
  * **Best-effort cleanup**: cleanup_staging never raises. If a scanner
    leaves an open file descriptor or the tmpdir got an fchmod(0o000),
    the next scheduled OS tmpdir cleaner handles it.

Pairs with `cpv_dedup.py` (dedup logic) and `cpv_install_scanners.py`
(silent fclones autoinstall).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

__all__ = [
    "StageMode",
    "StageResult",
    "MarketplaceStageResult",
    "IngestResult",
    "stage_target",
    "stage_marketplace",
    "ingest_github_url",
    "ingest_archive",
    "looks_like_github_url",
    "looks_like_archive",
    "hardlink_tree",
    "cleanup_staging",
]


# Above this size threshold we fall back to symlinks instead of copies on
# cross-fs. Hardlinks remain the preferred path; copies are an acceptable
# fallback for small trees but not for marketplace-scale corpora.
_MAX_CROSS_FS_COPY_BYTES = 100 * 1024 * 1024  # 100 MiB


# ── Public types ────────────────────────────────────────────────────


class StageMode:
    """Sentinel constants describing how the staging tree was built.

    Inspecting ``StageResult.mode`` lets the dedup pipeline decide whether
    deletions are safe (``HARDLINK``, ``COPY``) or must be skipped
    (``SYMLINK``).
    """

    HARDLINK = "hardlink"  # same-fs hardlinks; deletion-safe
    COPY = "copy"  # cross-fs file copies; deletion-safe
    SYMLINK = "symlink"  # cross-fs symlinks; dedup-by-deletion DISABLED


class StageResult:
    """Outcome of a stage_target call.

    Attributes:
        stage_root: The created staging directory (always under the system
            tmpdir, prefixed ``cpv-stage-``). Caller is responsible for
            calling ``cleanup_staging(stage_root)`` in a finally block.
        target_in_stage: Where the input target now lives inside the staging
            tree. For a single-target stage this is ``stage_root / <basename>``;
            scanners should be invoked on THIS path, not on the original.
        mode: One of the ``StageMode`` constants. Tells the dedup pipeline
            whether deletion is safe.
        files_staged: Count of regular files that landed in the staging
            tree (excludes directories, symlinks-to-elsewhere, sockets).
        bytes_staged: Total content size of staged files. Approximate;
            with hardlinks the disk usage is essentially zero (we report
            content-bytes for sizing reports).
        skipped_reasons: Human-readable list of files/dirs we couldn't
            stage (permission denied, broken symlink, etc.). Always a list
            (possibly empty), never None.
    """

    def __init__(
        self,
        *,
        stage_root: Path,
        target_in_stage: Path,
        mode: str,
        files_staged: int = 0,
        bytes_staged: int = 0,
        skipped_reasons: list[str] | None = None,
    ) -> None:
        self.stage_root = stage_root
        self.target_in_stage = target_in_stage
        self.mode = mode
        self.files_staged = files_staged
        self.bytes_staged = bytes_staged
        self.skipped_reasons = list(skipped_reasons) if skipped_reasons else []

    @property
    def supports_deletion(self) -> bool:
        """True if the staging mode safely supports dedup-by-deletion."""
        return self.mode in (StageMode.HARDLINK, StageMode.COPY)


# ── Public API ──────────────────────────────────────────────────────


def stage_target(
    target: Path,
    *,
    stage_name: str | None = None,
    fall_back_to_copy_under_bytes: int = _MAX_CROSS_FS_COPY_BYTES,
) -> StageResult:
    """Build a hardlinked (or fallback-copied / symlinked) staging tree.

    The staging tree always lives under ``$TMPDIR/cpv-stage-<random>/``.
    The target is placed at ``stage_root/<basename>`` (or ``stage_name`` if
    overridden). Scanners must be invoked on ``StageResult.target_in_stage``,
    not on the original ``target``.

    Cross-fs fallback strategy:
      1. Try `os.link()` per file (HARDLINK mode). Fastest, zero-copy.
      2. If hardlinks fail at the FIRST file with EXDEV (cross-fs), measure
         the target's total content size:
           * If ≤ ``fall_back_to_copy_under_bytes`` (default 100 MiB):
             COPY mode (file copies). Slow but deletion-safe.
           * Else: SYMLINK mode. Dedup-by-deletion is DISABLED for this
             scan (staging entries point at the cache; deleting one would
             remove the path but leave the canonical accessible — bucketing
             still works because the dedup_map snapshot was taken first).
      3. If hardlinks fail mid-tree (one file works, the next EXDEV's),
         the partial hardlinks are left in place and we fall back to
         COPY for the remaining files. The mode is COPY in that case.

    Args:
        target: The directory or single file to stage. Must exist.
        stage_name: Optional override for the basename inside the staging
            tree. Defaults to ``target.name``. Used by the marketplace
            bulk scanner to give each plugin a deterministic subdir
            (``stage/<plugin-name>/``).
        fall_back_to_copy_under_bytes: Tunable knob for the cross-fs
            fallback decision. Default 100 MiB. Set to 0 to force the
            symlink fallback regardless of size (rare; tests).

    Returns:
        StageResult. Caller MUST call ``cleanup_staging(result.stage_root)``
        in a finally block. The staging tree is otherwise stranded under
        the OS tmpdir until the next OS-level cleanup pass.

    Raises:
        FileNotFoundError: When ``target`` doesn't exist. CPV's main entry
            already validates this; this re-raise is defensive.
    """
    if not target.exists():
        raise FileNotFoundError(f"stage_target: {target} does not exist")

    stage_root = Path(tempfile.mkdtemp(prefix="cpv-stage-"))
    target_basename = stage_name or target.name
    target_in_stage = stage_root / target_basename

    # Try HARDLINK first. If the very first link raises EXDEV, switch to
    # COPY/SYMLINK mode based on the target's total size.
    try:
        files, bytes_, skipped = hardlink_tree(target, target_in_stage)
        return StageResult(
            stage_root=stage_root,
            target_in_stage=target_in_stage,
            mode=StageMode.HARDLINK,
            files_staged=files,
            bytes_staged=bytes_,
            skipped_reasons=skipped,
        )
    except OSError as exc:
        # EXDEV (errno 18 on Linux, 17 on macOS) — cross-filesystem link.
        # Bail to COPY or SYMLINK based on size budget.
        if exc.errno not in {18, 17}:  # not cross-fs; re-raise
            cleanup_staging(stage_root)
            raise

    # Reached only on cross-fs detection. Compute size budget and pick mode.
    total_bytes = _measure_tree_bytes(target)
    if total_bytes <= fall_back_to_copy_under_bytes:
        files, bytes_, skipped = _copy_tree(target, target_in_stage)
        mode = StageMode.COPY
    else:
        files, bytes_, skipped = _symlink_tree(target, target_in_stage)
        mode = StageMode.SYMLINK
    return StageResult(
        stage_root=stage_root,
        target_in_stage=target_in_stage,
        mode=mode,
        files_staged=files,
        bytes_staged=bytes_,
        skipped_reasons=skipped,
    )


def hardlink_tree(src: Path, dst: Path) -> tuple[int, int, list[str]]:
    """Hardlink every file under ``src`` to a mirrored path under ``dst``.

    For directories we just recreate the structure; only regular files get
    hardlinked. Symlinks in the source are preserved as symlinks (not
    dereferenced).

    Returns:
        ``(files_linked, bytes_linked, skipped_reasons)``. ``bytes_linked``
        is the sum of source-file content sizes (hardlinks add zero on-disk
        bytes; this number is for reporting parity with COPY mode).

    Raises:
        OSError: When the FIRST `os.link()` fails with EXDEV (cross-fs).
            The caller (``stage_target``) catches this and falls back to
            copy/symlink mode. Subsequent EXDEV mid-tree is recorded in
            ``skipped_reasons`` (NOT raised) — partial hardlinks remain.
    """
    return _walk_and_apply(
        src,
        dst,
        on_file=lambda s, d: _hardlink_file(s, d, raise_first_exdev=True),
    )


def cleanup_staging(stage_root: Path) -> None:
    """Best-effort recursive removal of the staging tree.

    Never raises. Logged exceptions go nowhere — staging is ephemeral
    and the OS will sweep it up eventually.
    """
    if stage_root is None:
        return
    try:
        shutil.rmtree(stage_root, ignore_errors=True)
    except (OSError, RuntimeError):
        pass


# ── Internal helpers ──────────────────────────────────────────────


def _walk_and_apply(
    src: Path,
    dst: Path,
    *,
    on_file: Callable[[Path, Path], int],
) -> tuple[int, int, list[str]]:
    """Walk src, mirror its directory shape to dst, apply `on_file` per file.

    Returns (files, bytes, skipped_reasons). The on_file callback returns
    the bytes copied/linked for accounting; 0 means "skipped".
    """
    files = 0
    bytes_ = 0
    skipped: list[str] = []

    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            n = on_file(src, dst)
        except OSError as exc:
            skipped.append(f"{src}: {exc!r}")
            return 0, 0, skipped
        return (1 if n > 0 else 0), n, skipped

    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, names in os.walk(src, followlinks=False):
        rel_root = Path(root).relative_to(src)
        out_root = dst / rel_root
        out_root.mkdir(parents=True, exist_ok=True)
        for subdir in list(dirs):
            (out_root / subdir).mkdir(parents=True, exist_ok=True)
        for name in names:
            src_file = Path(root) / name
            dst_file = out_root / name
            try:
                n = on_file(src_file, dst_file)
                if n > 0:
                    files += 1
                    bytes_ += n
            except OSError as exc:
                skipped.append(f"{src_file}: {exc!r}")
    return files, bytes_, skipped


def _hardlink_file(s: Path, d: Path, *, raise_first_exdev: bool) -> int:
    """Hardlink (or symlink-passthrough) a single file. Returns content size.

    Symlinks in the source are recreated as symlinks pointing at the same
    target — we don't dereference them. Sockets/fifos/devices are skipped
    silently (return 0).
    """
    try:
        st = s.lstat()
    except OSError:
        return 0
    mode = st.st_mode
    if os.path.stat.S_ISLNK(mode):  # type: ignore[attr-defined]
        try:
            target = os.readlink(s)
            os.symlink(target, d)
        except OSError:
            return 0
        return 0  # symlinks contribute zero content bytes
    if os.path.stat.S_ISREG(mode):  # type: ignore[attr-defined]
        try:
            os.link(s, d)
            return st.st_size
        except OSError as exc:
            if raise_first_exdev and exc.errno in {17, 18}:
                raise  # cross-fs detected; caller will switch mode
            raise
    return 0  # not regular file, not symlink — skip


def _measure_tree_bytes(src: Path) -> int:
    """Sum content bytes of regular files under ``src``. Used for cross-fs sizing."""
    if src.is_file():
        try:
            return src.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, names in os.walk(src, followlinks=False):
        for name in names:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


def _copy_tree(src: Path, dst: Path) -> tuple[int, int, list[str]]:
    """File-copy fallback. Used when cross-fs and tree size <= budget."""
    return _walk_and_apply(src, dst, on_file=_copy_file)


def _copy_file(s: Path, d: Path) -> int:
    try:
        shutil.copy2(s, d, follow_symlinks=False)
    except OSError as exc:
        raise OSError(f"copy {s} -> {d}: {exc}") from exc
    try:
        return d.lstat().st_size
    except OSError:
        return 0


def _symlink_tree(src: Path, dst: Path) -> tuple[int, int, list[str]]:
    """Symlink fallback. Used when cross-fs AND tree size > budget.

    Each staging entry points at the original. Dedup-by-deletion MUST be
    skipped in this mode (caller checks ``StageResult.supports_deletion``).
    """
    return _walk_and_apply(src, dst, on_file=_symlink_file)


def _symlink_file(s: Path, d: Path) -> int:
    try:
        os.symlink(s, d)
    except OSError as exc:
        raise OSError(f"symlink {s} -> {d}: {exc}") from exc
    return 0  # symlinks contribute zero content bytes


# ── Marketplace staging (Phase 4 — many plugins under one stage root) ──


class MarketplaceStageResult:
    """Outcome of a marketplace-wide staging operation.

    Attributes:
        stage_root: Tmpdir containing per-plugin subdirs. Caller must call
            ``cleanup_staging(stage_root)`` in a finally block.
        plugin_paths: Dict mapping ``<plugin-name> → staged-plugin-path``.
            The staged path is ``stage_root/<safe-plugin-name>/`` — scanners
            run on these paths.
        original_paths: Dict mapping ``<plugin-name> → original-source-path``
            (the cache path or wherever the plugin came from). Used by the
            bucketing layer to attribute findings back to the user's view of
            the plugin and by the per-plugin scanner pass (cc-audit/tirith)
            which needs the real plugin layout.
        mode: Worst-case StageMode across all plugins (HARDLINK if all are
            hardlinked; COPY/SYMLINK if any plugin fell back). Determines
            whether dedup-by-deletion is safe corpus-wide.
        files_staged: Total files across all plugin subtrees.
        bytes_staged: Total content bytes across all plugin subtrees.
        skipped_reasons: List of "<plugin-name>: <reason>" lines for plugins
            that could not be staged at all (resolution failure, clone
            timeout, etc.). NOT individual file-level skips inside a
            successfully staged plugin.
    """

    def __init__(
        self,
        *,
        stage_root: Path,
        plugin_paths: dict[str, Path] | None = None,
        original_paths: dict[str, Path] | None = None,
        mode: str = StageMode.HARDLINK,
        files_staged: int = 0,
        bytes_staged: int = 0,
        skipped_reasons: list[str] | None = None,
    ) -> None:
        self.stage_root = stage_root
        self.plugin_paths = dict(plugin_paths) if plugin_paths else {}
        self.original_paths = dict(original_paths) if original_paths else {}
        self.mode = mode
        self.files_staged = files_staged
        self.bytes_staged = bytes_staged
        self.skipped_reasons = list(skipped_reasons) if skipped_reasons else []

    @property
    def supports_deletion(self) -> bool:
        """True iff dedup-by-deletion is safe corpus-wide."""
        return self.mode in (StageMode.HARDLINK, StageMode.COPY)


def stage_marketplace(
    plugin_dirs: list[Path],
    *,
    name_resolver: Callable[[Path], str] | None = None,
    fall_back_to_copy_under_bytes: int = _MAX_CROSS_FS_COPY_BYTES,
) -> MarketplaceStageResult:
    """Stage every plugin in ``plugin_dirs`` under one shared tmpdir.

    Builds ``$TMPDIR/cpv-stage-mp-<random>/<safe-plugin-name>/`` per plugin.
    Each plugin's subdir is hardlinked from its original (zero-copy on
    same-fs) so the dedup pipeline can safely delete duplicate hardlinks
    without touching the source.

    Args:
        plugin_dirs: Original plugin directory paths (typically resolved
            from ``~/.claude/plugins/cache/<marketplace>/<plugin>/<latest>/``
            or freshly cloned tmpdirs from Phase 6's URL ingestion).
        name_resolver: Optional callable taking a plugin Path and returning
            the safe basename to use under the stage root. Defaults to using
            ``plugin_dir.name`` plus a numeric suffix on collision so two
            different plugin paths whose ``.name`` happens to match (e.g.
            two different versions of the same plugin) don't clobber each
            other in staging.
        fall_back_to_copy_under_bytes: Per-plugin cross-fs fallback budget
            (forwarded to each ``stage_target`` call internally — but here
            we open ONE tmpdir for the whole marketplace, so the helper is
            called inline rather than via ``stage_target``).

    Returns:
        MarketplaceStageResult. Caller MUST call
        ``cleanup_staging(result.stage_root)`` in a finally block. The mode
        is the worst-case across all plugins (HARDLINK if all clean, COPY
        if any fell back to file-copy, SYMLINK if any fell back to symlinks
        which disables dedup-by-deletion corpus-wide).

    Raises:
        ValueError: When ``plugin_dirs`` is empty (no work to do).
    """
    if not plugin_dirs:
        raise ValueError("stage_marketplace: plugin_dirs is empty")

    stage_root = Path(tempfile.mkdtemp(prefix="cpv-stage-mp-"))
    plugin_paths: dict[str, Path] = {}
    original_paths: dict[str, Path] = {}
    skipped_reasons: list[str] = []
    total_files = 0
    total_bytes = 0
    overall_mode = StageMode.HARDLINK
    used_names: set[str] = set()

    def _resolve_name(p: Path) -> str:
        base = name_resolver(p) if name_resolver is not None else p.name
        # Sanitize: replace path separators / unsafe chars with underscores.
        base = "".join(c if c.isalnum() or c in "._-" else "_" for c in base)
        if not base:
            base = "plugin"
        # Disambiguate collisions deterministically.
        candidate = base
        i = 2
        while candidate in used_names:
            candidate = f"{base}_{i}"
            i += 1
        used_names.add(candidate)
        return candidate

    for plugin_dir in plugin_dirs:
        if not plugin_dir.exists():
            skipped_reasons.append(f"{plugin_dir}: source does not exist")
            continue
        safe_name = _resolve_name(plugin_dir)
        target_in_stage = stage_root / safe_name
        try:
            files, bytes_, _per_plugin_skipped = hardlink_tree(plugin_dir, target_in_stage)
            mode = StageMode.HARDLINK
        except OSError as exc:
            if exc.errno not in {17, 18}:  # not cross-fs — record + skip plugin
                skipped_reasons.append(f"{plugin_dir}: {exc!r}")
                continue
            # Cross-fs: fall back to copy or symlink based on plugin tree size.
            cleanup_staging(target_in_stage)  # remove any partial state
            tree_bytes = _measure_tree_bytes(plugin_dir)
            if tree_bytes <= fall_back_to_copy_under_bytes:
                files, bytes_, _ = _copy_tree(plugin_dir, target_in_stage)
                mode = StageMode.COPY
            else:
                files, bytes_, _ = _symlink_tree(plugin_dir, target_in_stage)
                mode = StageMode.SYMLINK
        plugin_paths[safe_name] = target_in_stage
        original_paths[safe_name] = plugin_dir
        total_files += files
        total_bytes += bytes_
        # Worst-case mode: SYMLINK > COPY > HARDLINK (in restrictiveness order).
        if mode == StageMode.SYMLINK:
            overall_mode = StageMode.SYMLINK
        elif mode == StageMode.COPY and overall_mode == StageMode.HARDLINK:
            overall_mode = StageMode.COPY

    return MarketplaceStageResult(
        stage_root=stage_root,
        plugin_paths=plugin_paths,
        original_paths=original_paths,
        mode=overall_mode,
        files_staged=total_files,
        bytes_staged=total_bytes,
        skipped_reasons=skipped_reasons,
    )


# ── URL / Archive ingestion (Phase 6 — scan-before-install) ───────


_GITHUB_URL_RE = (
    "https://github.com/",
    "http://github.com/",
)
_GITHUB_SHORTHAND_PREFIX = "github:"
_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".tar",
)


class IngestResult:
    """Outcome of a URL or archive ingestion.

    Attributes:
        tmpdir: The freshly-created tmpdir holding the cloned/extracted
            content. The caller MUST remove this with ``cleanup_staging``
            in a finally block. Using a separate name from staging's
            stage_root makes it clear this is the ingestion-stage tmpdir,
            not the dedup-stage tmpdir built later by ``stage_target``.
        target: The path inside ``tmpdir`` that should be passed to the
            scan pipeline. For a single-plugin GitHub URL this is the
            cloned repo root; for an archive it's the extracted root.
        source_kind: One of ``"github-url"`` or ``"archive"``. Helps
            downstream reporting describe where the content came from.
        source_spec: The raw input string (URL or archive path) for
            audit-trail messages.
    """

    def __init__(
        self,
        *,
        tmpdir: Path,
        target: Path,
        source_kind: str,
        source_spec: str,
    ) -> None:
        self.tmpdir = tmpdir
        self.target = target
        self.source_kind = source_kind
        self.source_spec = source_spec


def looks_like_github_url(spec: str) -> bool:
    """True if ``spec`` should be treated as a GitHub URL or shorthand.

    Recognized shapes:
      * ``https://github.com/owner/repo`` (with or without trailing slash)
      * ``http://github.com/owner/repo``  (auto-upgraded to https on clone)
      * ``github:owner/repo``             (shorthand)
    Plain ``owner/repo`` is intentionally NOT recognized — it would
    collide with relative paths like ``my-plugins/p1`` and produce false
    positives. Users must add the ``github:`` prefix to disambiguate.
    """
    if not isinstance(spec, str) or not spec:
        return False
    if spec.startswith(_GITHUB_SHORTHAND_PREFIX):
        return True
    return any(spec.startswith(prefix) for prefix in _GITHUB_URL_RE)


def looks_like_archive(spec: str) -> bool:
    """True if ``spec`` looks like a path to a supported archive file.

    Match is case-insensitive on the suffix. We do NOT actually read the
    file here — caller verifies existence/permissions via ``ingest_archive``.
    """
    if not isinstance(spec, str) or not spec:
        return False
    lower = spec.lower()
    return any(lower.endswith(s) for s in _ARCHIVE_SUFFIXES)


def _normalize_github_spec(spec: str) -> str:
    """Convert any accepted GitHub spec form to ``owner/repo`` for ``gh repo clone``."""
    s = spec.strip()
    if s.startswith(_GITHUB_SHORTHAND_PREFIX):
        s = s[len(_GITHUB_SHORTHAND_PREFIX) :]
    for prefix in _GITHUB_URL_RE:
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    s = s.rstrip("/")
    # Strip any trailing path components (e.g. /tree/main/sub/dir) — gh
    # repo clone takes owner/repo only. We deliberately NOT support
    # subdir clones here; a follow-up could use git sparse-checkout.
    parts = s.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return s


def ingest_github_url(spec: str, *, depth: int = 1, timeout_seconds: int = 120) -> IngestResult:
    """Clone a GitHub URL into a fresh tmpdir for downstream scanning.

    Returns an ``IngestResult`` whose ``target`` points at the cloned repo
    root (suitable for passing into ``stage_target`` or ``validate_security``).
    Caller MUST call ``cleanup_staging(result.tmpdir)`` in a finally block.

    Args:
        spec: A GitHub URL (https://github.com/owner/repo) or shorthand
            (github:owner/repo). Validated by ``looks_like_github_url``.
        depth: Clone depth. Default 1 (shallow). Set higher only when the
            scan needs git history (rare — CPV's scanners are content-based).
        timeout_seconds: Max wall-clock seconds for the gh clone.

    Raises:
        ValueError: When ``spec`` is not a recognized GitHub URL/shorthand.
        RuntimeError: When ``gh`` is not on PATH or the clone fails. The
            tmpdir is cleaned up before the exception is re-raised.
    """
    if not looks_like_github_url(spec):
        raise ValueError(f"ingest_github_url: not a GitHub URL: {spec!r}")
    gh_bin = shutil.which("gh")
    if not gh_bin:
        raise RuntimeError("ingest_github_url: 'gh' CLI not on PATH. Install: https://cli.github.com/")
    repo_name = _normalize_github_spec(spec)
    tmpdir = Path(tempfile.mkdtemp(prefix="cpv-ingest-gh-"))
    target = tmpdir / "repo"
    try:
        result = subprocess.run(
            [gh_bin, "repo", "clone", repo_name, str(target), "--", "--depth", str(depth), "--quiet"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ingest_github_url: clone of {repo_name!r} failed "
                f"(exit {result.returncode}): "
                f"{(result.stderr or '').strip()[:300]}"
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        cleanup_staging(tmpdir)
        raise RuntimeError(f"ingest_github_url: clone of {repo_name!r} failed: {exc!r}") from exc
    except RuntimeError:
        cleanup_staging(tmpdir)
        raise
    return IngestResult(
        tmpdir=tmpdir,
        target=target,
        source_kind="github-url",
        source_spec=spec,
    )


def ingest_archive(archive_path: Path | str) -> IngestResult:
    """Extract a local archive into a fresh tmpdir for downstream scanning.

    Wraps ``cpv_management_common.extract_archive`` (which has built-in
    path-traversal and symlink-escape protections) but converts its
    ``sys.exit`` failure mode into a proper exception so the caller can
    cleanup the tmpdir.

    Args:
        archive_path: Path to a ``.zip``, ``.tar.gz``, ``.tgz``,
            ``.tar.bz2``, ``.tbz2``, ``.tar.xz``, ``.txz``, or ``.tar``
            file. Existence verified before extraction.

    Returns:
        ``IngestResult`` with ``target`` pointing at the extracted root.
        Caller MUST call ``cleanup_staging(result.tmpdir)`` in a finally.

    Raises:
        FileNotFoundError: When ``archive_path`` does not exist.
        ValueError: When the suffix isn't a supported archive format.
        RuntimeError: When extraction fails (re-wraps any internal error
            from ``extract_archive``). The tmpdir is cleaned up before
            the exception is re-raised.
    """
    archive = Path(archive_path)
    if not archive.exists():
        raise FileNotFoundError(f"ingest_archive: {archive} not found")
    if not looks_like_archive(str(archive)):
        raise ValueError(
            f"ingest_archive: unsupported archive format: {archive.name!r}; "
            f"supported suffixes: {', '.join(_ARCHIVE_SUFFIXES)}"
        )
    tmpdir = Path(tempfile.mkdtemp(prefix="cpv-ingest-ar-"))
    target = tmpdir / "extracted"
    target.mkdir()
    try:
        # Local import keeps cpv_management_common's heavy dependency graph
        # out of the cold path (no ingestion → no import cost).
        from cpv_management_common import extract_archive  # noqa: PLC0415

        try:
            extract_archive(str(archive), target)
        except SystemExit as exc:
            raise RuntimeError(
                f"ingest_archive: extract failed for {archive!r} (exit {exc.code}). See stderr for details."
            ) from exc
    except Exception:
        cleanup_staging(tmpdir)
        raise
    return IngestResult(
        tmpdir=tmpdir,
        target=target,
        source_kind="archive",
        source_spec=str(archive),
    )
