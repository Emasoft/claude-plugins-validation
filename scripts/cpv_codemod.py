#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""cpv-codemod — deterministic mechanical fixes for CPV findings.

Addresses GitHub issue #17. The cpv-plugin-fixer-agent agent is excellent for
judgment-required fixes but burns enormous tokens on line-local
mechanical transforms (backtick → markdown link, TOC stubs, etc.).
This CLI applies the inverse of CPV's detection regexes — read-only
audit becomes read-write fix at zero LLM cost.

Safety contract:
  * Dry-run is the default. ``--apply`` is opt-in and always pairs with
    a per-file backup under ``.cpv-codemod-backup/<timestamp>/``.
  * Every change is shown as a unified diff before write.
  * Idempotent: running the codemod twice produces no further changes.
  * Skips ``external/``, ``vendor/``, ``third_party/``, ``node_modules/``,
    and any path listed in ``.gitmodules`` — vendored content stays put.
  * Never invokes git. The maintainer reviews diffs and commits.

Subcommands:
  * ``backtick-to-link`` — ``\\`path/file.md\\``` in prose →
    ``[file](path/file.md)`` (issue #16 category C)
  * ``add-toc`` — prepend ``## Table of Contents`` block built from
    existing ``##`` headings (issue #16 category D)
  * ``wrap-placeholder-paths`` — wrap unresolved prose paths in
    ``<...>`` template-exempt brackets
  * ``add-standard-sections`` — insert missing ``## Overview`` /
    ``## Examples`` / ``## Output`` headings
  * ``dedup-trailing-blanks`` — collapse ``\\n\\n\\n+`` → ``\\n\\n``
  * ``external-skip-list`` — auto-add ``external/``, vendored paths to
    the plugin's CPV exclusion list in ``.claude-plugin/plugin.json``
  * ``all`` — run every applicable subcommand in safe order
  * ``apply`` — read a CPV ``--strict --json`` findings report, select the
    ``fixable: true`` entries, and dispatch each by ``fix_id`` to its
    deterministic transform (Phase 2, TRDD-GVMOKJBB). ``fixable``/``fix_id``
    are the SSOT the validators set at finding-build time; the fix ledger's
    MECH bucket is exactly this set. Only the ``chmod-exec`` fix_id is wired
    today (chmod +x a file that has a shebang). Dry-run by default; ``--apply``
    performs the change with the same per-file backup + vendored-skip guards.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

# ── Vendored-path skip list (matches issue #16 category F) ────────────────────
VENDORED_DIR_NAMES = frozenset(
    {
        "external",
        "vendor",
        "vendored",
        "third_party",
        "third-party",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".git",
        "__pycache__",
    }
)

# Single source of truth for the backup directory name. Used both to CREATE
# backups (_backup_dir) and to EXCLUDE the backup tree from the markdown walk
# (_walk_markdown) — otherwise a second --apply run would descend into the
# mirrored .md files under a prior run's backup and re-process them, breaking
# the documented idempotency contract.
_BACKUP_DIR_NAME = ".cpv-codemod-backup"


def _read_gitmodules(plugin_root: Path) -> set[str]:
    """Return submodule paths declared in .gitmodules (relative to plugin_root)."""
    gm = plugin_root / ".gitmodules"
    if not gm.is_file():
        return set()
    paths: set[str] = set()
    for line in gm.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*path\s*=\s*(.+?)\s*$", line)
        if m:
            paths.add(m.group(1).strip().rstrip("/"))
    return paths


def _is_vendored(rel_path: Path, submodule_paths: set[str]) -> bool:
    """True if this path lives under a vendored / submodule subtree."""
    parts = rel_path.parts
    for part in parts:
        if part in VENDORED_DIR_NAMES:
            return True
    rel_str = str(rel_path).rstrip("/")
    for sm in submodule_paths:
        if rel_str == sm or rel_str.startswith(sm + "/"):
            return True
    return False


def _file_has_shebang(path: Path) -> bool:
    """True iff ``path`` begins with a ``#!`` shebang.

    Mirrors ``validate_plugin._has_shebang`` byte-for-byte (reads the first two
    bytes) so the ``chmod-exec`` transform's precondition is identical to the
    one the validator uses when it EMITS the ``fix_id="chmod-exec"`` finding —
    the transform can never disagree with the finding about whether a shebang
    is present.
    """
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def _walk_markdown(plugin_root: Path) -> Iterable[Path]:
    """Yield every .md file under plugin_root, skipping vendored subtrees.

    Also skips this tool's own backup tree (``.cpv-codemod-backup/``): the
    backup mirrors the source layout, so the mirrored ``.md`` files would
    otherwise be re-walked (and re-backed-up) on a later ``--apply`` run,
    violating the idempotency contract. Matching on a path *component*
    (not the file basename) is required because backed-up files keep their
    original names (e.g. ``SKILL.md``), so a basename check never fires.
    """
    submodules = _read_gitmodules(plugin_root)
    for path in sorted(plugin_root.rglob("*.md")):
        rel = path.relative_to(plugin_root)
        if _BACKUP_DIR_NAME in rel.parts:
            continue
        if _is_vendored(rel, submodules):
            continue
        yield path


# ── Backup helpers ────────────────────────────────────────────────────────────
def _backup_dir(plugin_root: Path) -> Path:
    """Per-run backup directory under .cpv-codemod-backup/<timestamp>/."""
    ts = datetime.now(tz=timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S%z")
    return plugin_root / _BACKUP_DIR_NAME / ts


def _backup_file(file_path: Path, plugin_root: Path, backup_root: Path) -> None:
    """Mirror file_path's relative location under backup_root before mutation."""
    rel = file_path.relative_to(plugin_root)
    dst = backup_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file_path, dst)


def _print_diff(file_path: Path, before: str, after: str) -> None:
    """Print a unified diff for the user to review."""
    if before == after:
        return
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{file_path} (before)",
        tofile=f"{file_path} (after)",
        n=2,
    )
    sys.stdout.writelines(diff)
    sys.stdout.write("\n")


# ── Subcommand: backtick-to-link ──────────────────────────────────────────────
# Match `path/to/file.md` (or any common doc/code extension) in prose.
# Skip:
#   - inside fenced code blocks (```...```)
#   - inside indented code blocks (4-space indent)
#   - npm package shapes: @scope/name, name@version, id/version (issue #16 C)
#   - already-linked: [text](path) followed by no opening bracket
#   - bare CLI/variable tokens (no slash AND no extension)
_BACKTICK_PATH_RE = re.compile(r"`([^`\n]+\.(?:md|py|js|ts|json|yaml|yml|toml|sh|html|css))`")
_NPM_PACKAGE_RE = re.compile(
    r"^("
    r"@[a-z0-9][\w.-]*/[a-z0-9][\w.-]*(@[\w.-]+)?"
    r"|[a-z0-9][\w.-]*@[\w.-]+"
    r"|[a-z0-9][\w.-]*/\d+\.\d+"
    r")$",
    re.IGNORECASE,
)


def _apply_backtick_to_link(text: str) -> str:
    """Convert ``path/to/file.md`` in prose to ``[file](path/to/file.md)``.

    Skips fenced code blocks and npm-package shapes (issue #16 C).
    Idempotent — already-linked refs are left alone because the regex
    only matches bare backtick-wrapped paths, not ``[label](path)``.
    """
    out_lines: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        # Track fenced code blocks (``` or ~~~)
        for marker in ("```", "~~~"):
            if stripped.startswith(marker):
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif fence_marker == marker:
                    in_fence = False
                    fence_marker = ""
                break
        if in_fence:
            out_lines.append(line)
            continue
        # Indented code blocks (4 leading spaces, no list marker)
        if line.startswith("    ") and not line.lstrip().startswith(("- ", "* ", "1.", "2.")):
            out_lines.append(line)
            continue

        def _replace(m: re.Match[str]) -> str:
            path_str = m.group(1).strip()
            # Skip npm-package shapes and version specs
            if _NPM_PACKAGE_RE.match(path_str):
                return m.group(0)
            # Skip absolute URLs (rare but possible in backticks)
            if path_str.startswith(("http://", "https://", "ftp://", "git@")):
                return m.group(0)
            # Strip leading ./ for cleaner labels
            label_source = path_str.lstrip("./")
            label = Path(label_source).stem or label_source
            return f"[{label}]({path_str})"

        out_lines.append(_BACKTICK_PATH_RE.sub(_replace, line))
    return "".join(out_lines)


# ── Subcommand: add-toc ───────────────────────────────────────────────────────
_HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$")


def _slugify_heading(text: str) -> str:
    """GitHub-style heading slug: lowercase, drop punctuation, dashes for spaces."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")


def _has_toc(text: str) -> bool:
    """True if the file already has a Table of Contents header in the prologue."""
    head = text[:1000].lower()
    return "## table of contents" in head or "## toc" in head


def _apply_add_toc(text: str, min_lines: int = 50) -> str:
    """Insert a `## Table of Contents` block built from existing ## headings.

    Skips files shorter than ``min_lines`` (issue #16 D — short technique
    files don't benefit from a TOC).
    Idempotent — files that already have a TOC are left alone.
    """
    if _has_toc(text):
        return text
    lines = text.splitlines(keepends=True)
    if len(lines) < min_lines:
        return text
    headings: list[tuple[int, str, str]] = []
    in_fence = False
    fence_marker = ""
    for line in lines:
        stripped = line.lstrip()
        for marker in ("```", "~~~"):
            if stripped.startswith(marker):
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif fence_marker == marker:
                    in_fence = False
                    fence_marker = ""
                break
        if in_fence:
            continue
        m = _HEADING_RE.match(line.rstrip("\n"))
        if m:
            level = len(m.group(1))
            title = m.group(2)
            headings.append((level, title, _slugify_heading(title)))
    if len(headings) < 3:
        # Not worth a TOC for fewer than 3 sub-headings.
        return text

    toc_lines = ["## Table of Contents", ""]
    for level, title, slug in headings:
        indent = "  " * (level - 2)
        toc_lines.append(f"{indent}- [{title}](#{slug})")
    toc_lines.extend(["", ""])
    toc_block = "\n".join(toc_lines)

    # Insert after the H1 line (first '# ' heading) if present, else at top.
    # The scan tracks fenced code blocks so a '# comment' line inside a
    # leading code fence is never mistaken for the document H1 — otherwise
    # the TOC block would be inserted in the MIDDLE of that fence, corrupting
    # it. The heading-collection loop above is already fence-aware; this loop
    # must match it.
    out = list(lines)
    insert_at = 0
    scan_in_fence = False
    scan_fence_marker = ""
    for i, line in enumerate(out):
        stripped = line.lstrip()
        is_fence_toggle = False
        for marker in ("```", "~~~"):
            if stripped.startswith(marker):
                if not scan_in_fence:
                    scan_in_fence = True
                    scan_fence_marker = marker
                elif scan_fence_marker == marker:
                    scan_in_fence = False
                    scan_fence_marker = ""
                is_fence_toggle = True
                break
        if scan_in_fence or is_fence_toggle:
            continue
        if line.startswith("# ") and not line.startswith("## "):
            insert_at = i + 1
            # Skip the blank line right after the H1 too.
            if insert_at < len(out) and out[insert_at].strip() == "":
                insert_at += 1
            break
    out.insert(insert_at, toc_block + "\n")
    return "".join(out)


# ── Subcommand: dedup-trailing-blanks ─────────────────────────────────────────
_TRIPLE_BLANK_RE = re.compile(r"\n{3,}")


def _apply_dedup_blanks(text: str) -> str:
    """Collapse runs of 3+ newlines into exactly 2."""
    return _TRIPLE_BLANK_RE.sub("\n\n", text)


# ── Subcommand: wrap-placeholder-paths ────────────────────────────────────────
# Detect prose backtick paths whose target doesn't exist on disk relative to
# the plugin root, AND that look like a placeholder (e.g. contains UPPER_CASE
# tokens or ${VAR}). Wrap them in <...> to mark them as template-exempt.
_PLACEHOLDER_TOKEN_RE = re.compile(r"\$\{[A-Z_]+\}|<[A-Z_]+>|[A-Z][A-Z_]{2,}")


def _apply_wrap_placeholder_paths(text: str, plugin_root: Path, file_path: Path) -> str:
    """Wrap unresolved prose paths that look like placeholders in <...>."""
    file_dir = file_path.parent

    def _replace(m: re.Match[str]) -> str:
        path_str = m.group(1).strip()
        # Skip if it's already a placeholder shape
        if path_str.startswith("<") and path_str.endswith(">"):
            return m.group(0)
        if not _PLACEHOLDER_TOKEN_RE.search(path_str):
            return m.group(0)
        # Resolve relative to file_dir and to plugin_root
        candidates = [file_dir / path_str, plugin_root / path_str]
        if any(c.exists() for c in candidates):
            return m.group(0)
        return f"`<{path_str}>`"

    return _BACKTICK_PATH_RE.sub(_replace, text)


# ── Subcommand: add-standard-sections ─────────────────────────────────────────
_STANDARD_SECTIONS = ("## Overview", "## Examples", "## Output")


def _apply_add_standard_sections(text: str) -> str:
    """Insert missing standard SKILL.md sections at the end of the file."""
    additions: list[str] = []
    for heading in _STANDARD_SECTIONS:
        if heading not in text:
            stub = f"\n{heading}\n\nTODO — describe.\n"
            additions.append(stub)
    if not additions:
        return text
    if not text.endswith("\n"):
        text += "\n"
    return text + "".join(additions)


# ── Subcommand: external-skip-list ────────────────────────────────────────────
@dataclass(frozen=True)
class SkipListResult:
    """Structured outcome of ``_apply_external_skip_list``.

    ``ok`` is the success signal the exit code is derived from — a clean
    no-op (manifest missing, no vendored dirs, already excluded) is still
    ``ok=True`` (exit 0); only a genuine inability to proceed is ``ok=False``.
    Deriving the exit code from this flag — rather than substring-matching
    ``summary`` — means the human-readable text can be reworded without
    silently flipping the CLI's exit status (finding #9).

    ``changed`` is True only when ``apply`` was set AND the manifest was
    actually rewritten; in dry-run it is always False even when a write
    WOULD have happened (see ``would_change``).
    """

    changed: bool
    ok: bool
    summary: str
    would_change: bool = False


def _apply_external_skip_list(plugin_root: Path, *, apply: bool) -> SkipListResult:
    """Add detected vendored dirs to plugin.json's cpv exclusion list.

    Honors the module-wide dry-run-by-default contract (finding #1): in
    dry-run (``apply=False``) the manifest is NEVER written — instead the
    diff is printed and ``would_change`` reports what an ``--apply`` run
    would do. In apply mode the manifest is backed up (mirroring the
    markdown transforms' backup behavior) BEFORE it is rewritten.
    """
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        # No manifest to update is a clean no-op, not a failure.
        return SkipListResult(changed=False, ok=True, summary=f"No .claude-plugin/plugin.json under {plugin_root}")
    # A malformed or unreadable manifest is a genuine inability to proceed
    # (ok=False → exit 1), NOT a crash (finding #64). Surfacing it as a
    # structured failure — rather than letting JSONDecodeError/OSError
    # propagate as a traceback — keeps the `all` run from aborting after
    # it has already rewritten the markdown files.
    try:
        raw = manifest.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return SkipListResult(changed=False, ok=False, summary=f"Cannot read .claude-plugin/plugin.json: {exc}")
    detected: set[str] = set()
    submodules = _read_gitmodules(plugin_root)
    detected.update(submodules)
    for child in plugin_root.iterdir():
        if child.is_dir() and child.name in VENDORED_DIR_NAMES:
            detected.add(child.name)
    if not detected:
        return SkipListResult(changed=False, ok=True, summary="No vendored directories detected")
    cpv_block = data.setdefault("cpv", {})
    existing = set(cpv_block.get("exclude_paths", []))
    new = sorted(existing | detected)
    if new == sorted(existing):
        return SkipListResult(changed=False, ok=True, summary=f"All {len(detected)} vendored paths already excluded")
    cpv_block["exclude_paths"] = new
    new_raw = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if new_raw == raw:
        return SkipListResult(changed=False, ok=True, summary="No change after re-serialization")
    added = len(detected - existing)
    # Always show the diff so the user can review it (dry-run AND apply).
    _print_diff(manifest.relative_to(plugin_root), raw, new_raw)
    if not apply:
        return SkipListResult(
            changed=False,
            ok=True,
            would_change=True,
            summary=f"Would add {added} vendored path(s) to cpv.exclude_paths (dry-run — re-run with --apply to write)",
        )
    backup_root = _backup_dir(plugin_root)
    _backup_file(manifest, plugin_root, backup_root)
    manifest.write_text(new_raw, encoding="utf-8")
    return SkipListResult(changed=True, ok=True, summary=f"Added {added} vendored path(s) to cpv.exclude_paths")


# ── fix_id-dispatched apply mode (Phase 2, TRDD-GVMOKJBB) ─────────────────────
# ``fixable=True`` + a stable ``fix_id`` are the SSOT a validator sets AT
# FINDING-BUILD TIME to mean "this finding is mechanically auto-fixable" (the
# fix ledger's MECH bucket is exactly this set). ``apply --json`` reads a CPV
# ``--strict --json`` report, selects those entries, and dispatches each by
# ``fix_id`` to a deterministic transform. The dry-run / --apply / per-file
# backup / vendored-skip contract is identical to the content transforms above.


@dataclass(frozen=True)
class FixOutcome:
    """One fixable finding's ``apply`` result — one printed line's worth.

    ``changed`` is True when a fix WAS applied (``--apply``) OR WOULD be applied
    (dry-run) — the finding is genuinely actionable now. Every skip (already
    fixed, vendored, missing file, no shebang, outside the tree, chmod error) is
    ``changed=False``. ``detail`` is the human-readable reason.
    """

    fix_id: str
    file: str
    changed: bool
    detail: str


def _apply_fix_chmod_exec(
    result: dict[str, Any],
    plugin_root: Path,
    backup_root: Path,
    submodules: set[str],
    *,
    apply: bool,
) -> FixOutcome:
    """Make a shebang-carrying script executable (the ``chmod-exec`` fix_id).

    Deterministic + idempotent: acts only on a file that (a) resolves INSIDE the
    plugin tree, (b) is not vendored, (c) exists, (d) begins with a shebang, and
    (e) is not already executable for the current user. Any failed guard is a
    no-op skip (fail-safe — never chmod a look-alike). ``mode | 0o111`` mirrors
    the finding's own remediation advice (``chmod +x``); a second run finds the
    file already executable and skips, so ``apply`` is idempotent.
    """
    fix_id = "chmod-exec"
    rel = result.get("file")
    if not rel or not isinstance(rel, str):
        return FixOutcome(fix_id, str(rel), False, "skip (finding has no file)")
    rel_path = Path(rel)
    # Resolve the finding's plugin-relative (or absolute) ``file`` to an on-disk
    # path AND to a path relative to the plugin root (for the vendored check +
    # the backup mirror). A target outside the plugin tree is skipped — ``apply``
    # never touches a file outside the scanned plugin.
    target = rel_path if rel_path.is_absolute() else plugin_root / rel_path
    try:
        rel_for_tree = target.resolve().relative_to(plugin_root.resolve())
    except (ValueError, OSError):
        return FixOutcome(fix_id, rel, False, "skip (outside plugin root)")
    if _is_vendored(rel_for_tree, submodules):
        return FixOutcome(fix_id, rel, False, "skip (vendored)")
    if not target.is_file():
        return FixOutcome(fix_id, rel, False, "skip (file not found)")
    if not _file_has_shebang(target):
        # The transform's core guard: never chmod a file lacking a shebang. This
        # is exactly why only the shebang-GATED validator finding is tagged
        # chmod-exec — a non-shebang look-alike is left untouched.
        return FixOutcome(fix_id, rel, False, "skip (no shebang)")
    if os.access(target, os.X_OK):
        # Idempotency: the finding's condition is ``not os.access(.., X_OK)``, so
        # an already-executable file is nothing to fix (a 2nd run is a no-op).
        return FixOutcome(fix_id, rel, False, "skip (already executable)")
    if not apply:
        return FixOutcome(fix_id, rel, True, "would chmod +x (dry-run)")
    try:
        mode = target.stat().st_mode
        # Back up FIRST (copy2 preserves the pre-chmod mode → the original
        # non-executable permission stays restorable), mirroring the content
        # transforms' backup-before-write discipline.
        _backup_file(target, plugin_root, backup_root)
        os.chmod(target, mode | 0o111)
    except OSError as exc:
        return FixOutcome(fix_id, rel, False, f"skip (chmod failed: {exc})")
    return FixOutcome(fix_id, rel, True, "chmod +x applied")


class _FixHandler(Protocol):
    """Call signature every ``fix_id`` handler in the dispatch table honors."""

    def __call__(
        self,
        result: dict[str, Any],
        plugin_root: Path,
        backup_root: Path,
        submodules: set[str],
        *,
        apply: bool,
    ) -> FixOutcome: ...


# fix_id → handler. A finding whose fix_id is absent here is a fixable finding
# with no wired transform yet: ``apply`` reports it and skips it (never crashes,
# never guesses). Add a transform above, register it here, tag the finding at
# its validator build site — the three stay in lockstep.
_FIX_ID_DISPATCH: dict[str, _FixHandler] = {
    "chmod-exec": _apply_fix_chmod_exec,
}


def _extract_apply_results(data: Any) -> list[Any]:
    """Locate the results list in a findings JSON (wrapper or bare list).

    Accepts the standard ``{"results": [...]}`` wrapper ``remote_validation.py``
    emits, or a bare list of result dicts. Anything else yields an empty list
    (an empty run — never a crash). Mirrors ``cpv_fix_ledger._extract_results``.
    """
    if isinstance(data, dict):
        results = data.get("results")
        return results if isinstance(results, list) else []
    if isinstance(data, list):
        return data
    return []


# Detail string for a fixable finding whose fix_id has no wired transform. A
# module constant so the per-line emitter and the summary tally agree by
# construction (never a fragile duplicated literal).
_NO_TRANSFORM_DETAIL = "skip (no transform registered)"


def _apply_one_finding(
    r: Any,
    plugin_root: Path,
    backup_root: Path,
    submodules: set[str],
    *,
    apply: bool,
) -> FixOutcome | None:
    """Dispatch ONE report entry. ``None`` ⇒ the entry is not a fixable finding.

    A fixable entry whose ``fix_id`` has no registered transform yields a
    ``_NO_TRANSFORM_DETAIL`` skip (reported, never guessed). Extracting this
    keeps ``_run_apply``'s loop flat.
    """
    if not (isinstance(r, dict) and r.get("fixable")):
        return None  # NON-fixable / INFO / PASSED — apply never touches them.
    fix_id = r.get("fix_id")
    handler = _FIX_ID_DISPATCH.get(fix_id) if isinstance(fix_id, str) else None
    if handler is None:
        label = fix_id if isinstance(fix_id, str) and fix_id else "<no-fix_id>"
        return FixOutcome(label, str(r.get("file")), False, _NO_TRANSFORM_DETAIL)
    return handler(r, plugin_root, backup_root, submodules, apply=apply)


def _run_apply(plugin_root: Path, findings_path: Path, *, apply: bool) -> int:
    """Apply the ``fixable: true`` findings from a CPV ``--strict --json`` report.

    Selects fixable entries, dispatches each by ``fix_id``, prints one line per
    fixable finding. Dry-run by default (``--apply`` writes). Returns 0 on a
    processed report, 1 on a read/parse error (never a traceback). NON-fixable
    findings are ignored entirely — ``apply`` only ever acts on the MECH set.
    """
    try:
        data = json.loads(findings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"error: cannot read findings JSON {findings_path}: {exc}", file=sys.stderr)
        return 1

    submodules = _read_gitmodules(plugin_root)
    backup_root = _backup_dir(plugin_root)
    fixable_seen = changed = unregistered = 0
    for r in _extract_apply_results(data):
        outcome = _apply_one_finding(r, plugin_root, backup_root, submodules, apply=apply)
        if outcome is None:
            continue
        fixable_seen += 1
        if outcome.changed:
            changed += 1
        if outcome.detail == _NO_TRANSFORM_DETAIL:
            unregistered += 1
        print(f"[apply] {outcome.fix_id} {outcome.file}: {outcome.detail}")

    mode = "applied" if apply else "would apply"
    tail = f" ({unregistered} with no registered transform)" if unregistered else ""
    print(f"\n[apply] {mode} {changed} fix(es) across {fixable_seen} fixable finding(s){tail}")
    if apply and changed and backup_root.exists():
        print(f"[apply] backup → {backup_root.relative_to(plugin_root)}/")
    return 0


# ── Orchestrator ──────────────────────────────────────────────────────────────
def _process_file(
    file_path: Path,
    plugin_root: Path,
    transform: str,
    apply: bool,
    backup_root: Path,
    min_toc_lines: int,
) -> bool:
    """Run a single transform on a single file. Returns True if it changed."""
    before = file_path.read_text(encoding="utf-8")
    if transform == "backtick-to-link":
        after = _apply_backtick_to_link(before)
    elif transform == "add-toc":
        after = _apply_add_toc(before, min_lines=min_toc_lines)
    elif transform == "dedup-trailing-blanks":
        after = _apply_dedup_blanks(before)
    elif transform == "wrap-placeholder-paths":
        after = _apply_wrap_placeholder_paths(before, plugin_root, file_path)
    elif transform == "add-standard-sections":
        # Only apply to SKILL.md files at the root of a skill folder
        if file_path.name != "SKILL.md":
            return False
        after = _apply_add_standard_sections(before)
    else:
        return False
    if before == after:
        return False
    _print_diff(file_path.relative_to(plugin_root), before, after)
    if apply:
        _backup_file(file_path, plugin_root, backup_root)
        file_path.write_text(after, encoding="utf-8")
    return True


def _run_subcommand(
    transform: str,
    plugin_root: Path,
    apply: bool,
    min_toc_lines: int,
) -> int:
    """Run one subcommand across the plugin tree. Returns exit code."""
    if transform == "external-skip-list":
        result = _apply_external_skip_list(plugin_root, apply=apply)
        print(f"[{transform}] {result.summary}")
        # Exit code comes from the structured `ok` flag — never from
        # scraping `summary` (finding #9). Reword the text freely.
        return 0 if result.ok else 1
    backup_root = _backup_dir(plugin_root)
    files_touched = 0
    # The backup tree (.cpv-codemod-backup/) is excluded inside
    # _walk_markdown — by path component, not basename — so no extra
    # guard is needed here.
    for md_path in _walk_markdown(plugin_root):
        if _process_file(
            md_path,
            plugin_root,
            transform,
            apply,
            backup_root,
            min_toc_lines,
        ):
            files_touched += 1
    mode = "applied" if apply else "would change"
    print(f"\n[{transform}] {mode} {files_touched} file(s)")
    if apply and files_touched and backup_root.exists():
        rel_backup = backup_root.relative_to(plugin_root)
        print(f"[{transform}] backup → {rel_backup}/")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpv-codemod",
        description="Deterministic mechanical fixes for CPV findings (issue #17).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  cpv-codemod backtick-to-link --plugin ./my-plugin              # dry-run
  cpv-codemod backtick-to-link --plugin ./my-plugin --apply      # apply with backup
  cpv-codemod add-toc --plugin ./my-plugin --apply --min-lines 80
  cpv-codemod all --plugin ./my-plugin --apply
  cpv-codemod apply --json findings.json                         # dry-run the fixable set
  cpv-codemod apply --json findings.json --apply                 # chmod-exec etc. with backup

Safety contract:
  * Dry-run is the DEFAULT. --apply is required to write any file.
  * Every --apply run backs up touched files to
    .cpv-codemod-backup/<timestamp>/<rel-path>.
  * Vendored subtrees (external/, vendor/, third_party/, node_modules/, and
    any path listed in .gitmodules) are SKIPPED.
  * Idempotent — running twice produces no further changes.
""",
    )
    parser.add_argument(
        "subcommand",
        choices=[
            "backtick-to-link",
            "add-toc",
            "wrap-placeholder-paths",
            "add-standard-sections",
            "dedup-trailing-blanks",
            "external-skip-list",
            "all",
            "apply",
        ],
    )
    parser.add_argument(
        # Required for every subcommand EXCEPT `apply` (which defaults it to the
        # CWD — see main()). Kept optional at the argparse layer so `apply` can
        # run as `apply --json findings.json` from the plugin root; the
        # requiredness for the transform subcommands is enforced in main().
        "--plugin",
        required=False,
        default=None,
        type=Path,
        help="Path to the plugin root (required for every subcommand except `apply`, which defaults to CWD)",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry-run with diff preview)")
    parser.add_argument(
        "--min-lines", type=int, default=50, help="add-toc: minimum file line count to receive a TOC (default 50)"
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="apply: path to a CPV `--strict --json` findings report; applies its fixable:true entries by fix_id",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --plugin is required for every subcommand EXCEPT `apply`, where the
    # findings report already carries plugin-root-relative file paths, so
    # running `apply` from the plugin root needs no --plugin (defaults to CWD).
    # parser.error() exits 2 — identical to the old required=True behavior.
    if args.plugin is not None:
        plugin_root = args.plugin.expanduser().resolve()
    elif args.subcommand == "apply":
        plugin_root = Path.cwd().resolve()
    else:
        parser.error("--plugin is required")
    if not plugin_root.is_dir():
        print(f"error: plugin root not a directory: {plugin_root}", file=sys.stderr)
        return 2

    if args.subcommand == "apply":
        if args.json is None:
            parser.error("apply requires --json <findings.json>")
        return _run_apply(plugin_root, args.json.expanduser().resolve(), apply=args.apply)

    if args.subcommand == "all":
        # Every applicable subcommand in safe order: content transforms
        # first, then external-skip-list which rewrites the manifest.
        # add-standard-sections is included here too — _process_file
        # self-skips every file except SKILL.md, so running it over the
        # whole tree is a no-op everywhere else (finding #62).
        subs = [
            "backtick-to-link",
            "add-toc",
            "dedup-trailing-blanks",
            "wrap-placeholder-paths",
            "add-standard-sections",
            "external-skip-list",
        ]
        # Propagate the worst per-subcommand exit code instead of discarding
        # it (finding #63): a single failing subcommand must fail `all`.
        worst = 0
        for sub in subs:
            print(f"\n══════════════════════ {sub} ══════════════════════")
            rc = _run_subcommand(sub, plugin_root, args.apply, args.min_lines)
            worst = max(worst, rc)
        return worst
    return _run_subcommand(args.subcommand, plugin_root, args.apply, args.min_lines)


if __name__ == "__main__":
    sys.exit(main())
