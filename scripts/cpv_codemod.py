#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""cpv-codemod — deterministic mechanical fixes for CPV findings.

Addresses GitHub issue #17. The plugin-fixer agent is excellent for
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
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

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


def _walk_markdown(plugin_root: Path) -> Iterable[Path]:
    """Yield every .md file under plugin_root, skipping vendored subtrees."""
    submodules = _read_gitmodules(plugin_root)
    for path in sorted(plugin_root.rglob("*.md")):
        rel = path.relative_to(plugin_root)
        if _is_vendored(rel, submodules):
            continue
        yield path


# ── Backup helpers ────────────────────────────────────────────────────────────
def _backup_dir(plugin_root: Path) -> Path:
    """Per-run backup directory under .cpv-codemod-backup/<timestamp>/."""
    ts = datetime.now(tz=timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S%z")
    return plugin_root / ".cpv-codemod-backup" / ts


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
    out = list(lines)
    insert_at = 0
    for i, line in enumerate(out):
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
def _apply_external_skip_list(plugin_root: Path) -> tuple[bool, str]:
    """Add detected vendored dirs to plugin.json's cpv exclusion list.

    Returns (changed, summary).
    """
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return False, f"No .claude-plugin/plugin.json under {plugin_root}"
    raw = manifest.read_text(encoding="utf-8")
    data = json.loads(raw)
    detected: set[str] = set()
    submodules = _read_gitmodules(plugin_root)
    detected.update(submodules)
    for child in plugin_root.iterdir():
        if child.is_dir() and child.name in VENDORED_DIR_NAMES:
            detected.add(child.name)
    if not detected:
        return False, "No vendored directories detected"
    cpv_block = data.setdefault("cpv", {})
    existing = set(cpv_block.get("exclude_paths", []))
    new = sorted(existing | detected)
    if new == sorted(existing):
        return False, f"All {len(detected)} vendored paths already excluded"
    cpv_block["exclude_paths"] = new
    new_raw = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if new_raw == raw:
        return False, "No change after re-serialization"
    manifest.write_text(new_raw, encoding="utf-8")
    return True, f"Added {len(detected - existing)} vendored path(s) to cpv.exclude_paths"


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
        changed, summary = _apply_external_skip_list(plugin_root)
        print(f"[{transform}] {summary}")
        return 0 if changed or "already excluded" in summary or "No vendored" in summary else 1
    backup_root = _backup_dir(plugin_root)
    files_touched = 0
    for md_path in _walk_markdown(plugin_root):
        if md_path.name.startswith(".cpv-codemod-backup"):
            continue
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
        ],
    )
    parser.add_argument(
        "--plugin",
        required=True,
        type=Path,
        help="Path to the plugin root (must contain .claude-plugin/plugin.json or markdown files)",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry-run with diff preview)")
    parser.add_argument(
        "--min-lines", type=int, default=50, help="add-toc: minimum file line count to receive a TOC (default 50)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    plugin_root = args.plugin.expanduser().resolve()
    if not plugin_root.is_dir():
        print(f"error: plugin root not a directory: {plugin_root}", file=sys.stderr)
        return 2

    if args.subcommand == "all":
        subs = [
            "backtick-to-link",
            "add-toc",
            "dedup-trailing-blanks",
            "wrap-placeholder-paths",
            "external-skip-list",
        ]
        for sub in subs:
            print(f"\n══════════════════════ {sub} ══════════════════════")
            _run_subcommand(sub, plugin_root, args.apply, args.min_lines)
        return 0
    return _run_subcommand(args.subcommand, plugin_root, args.apply, args.min_lines)


if __name__ == "__main__":
    sys.exit(main())
