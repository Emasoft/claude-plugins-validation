#!/usr/bin/env python3
"""CPV lean-plugin architecture diagnostic — the DETECTION front-end.

Detects when a Claude Code plugin ships files **not needed at runtime**
(build-only mass, dev-only dirs, runtime dependency trees that belong in
``${CLAUDE_PLUGIN_DATA}``, regenerable build caches) and **recommends** the
existing CPV strip-dev / lean-separation machinery.

This module is **ADVISORY ONLY**. It classifies, measures, and recommends —
it NEVER moves, deletes, or mutates the target plugin. The SEPARATE step that
actually performs the separation is the existing ``cpv_strip_dev.py`` engine
(CLI: ``cpv strip-dev-parts``). The recommendations emitted here are shaped to
match that engine's real ``cpv.strip.extract[]`` config schema and its
``_RESERVED_SRCS`` ship-always set, so a user can act on them directly.

Design:
  * Pure-function core (``diagnose``) + a thin CLI (``main``). Read-only.
  * Git-accurate tree walk — reuses ``gitignored_unshipped_paths`` /
    ``path_is_unshipped`` from ``cpv_validation_common`` so a
    gitignored+untracked file (already not shipped) is not double-flagged as
    shippable mass, while a TRACKED build cache (which DOES ship) is surfaced.
  * Runtime-essential resolution — parses the manifest component fields AND
    greps the manifest + ``hooks.json`` + ``.mcp.json`` + ``.lsp.json`` +
    ``monitors.json`` + skill/agent bodies for ``${CLAUDE_PLUGIN_ROOT}``
    references, marking every referenced path ship-always.

FN-SAFETY (the diagnostic must never produce a destructive recommendation):
  * A RUNTIME-ESSENTIAL path, anything in ``_RESERVED_SRCS``, and anything
    referenced via ``${CLAUDE_PLUGIN_ROOT}`` is NEVER classified strippable.
    When in doubt → ship-always.
  * ``bin/`` (compiled binaries) is ALWAYS runtime — even though the SOURCE
    that builds it is BUILD_SOURCE.
  * A ``scripts/foo.py`` referenced by a runtime component is runtime; a
    build-only script referenced by nothing-at-runtime is a strip candidate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# Sibling-script import: the engine is invoked as a standalone script
# (`python scripts/cpv_diagnose_architecture.py`), so the scripts/ dir may not
# be on sys.path. Insert it, then import the shared helpers. We import ONLY the
# pure helpers we need; importing the module does NOT call the remote-execution
# guard (that runs only inside the validators' own entry points), so this stays
# usable as a freestanding CLI.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cpv_validation_common import (  # noqa: E402
    _read_gitmodules_paths,
    gitignored_unshipped_paths,
    is_vendored_path,
    load_strip_config,
    path_is_unshipped,
)

# ── Categories ──────────────────────────────────────────────────────────────

CAT_RUNTIME = "RUNTIME_ESSENTIAL"
CAT_BUILD_SOURCE = "BUILD_SOURCE"
CAT_RUNTIME_DEP = "RUNTIME_DEP"
CAT_DEV_ONLY = "DEV_ONLY"
CAT_BUILD_CACHE = "BUILD_CACHE"
CAT_UNKNOWN = "UNKNOWN"

# ── Reserved / ship-always set (kept in lockstep with cpv_strip_dev._RESERVED_SRCS).
# A plugin component dir that may NEVER be classified strippable, regardless of
# what else we detect. Mirrors cpv_strip_dev._RESERVED_SRCS plus the additional
# manifest/runtime component dirs from the Anthropic plugins-reference.
_RESERVED_SRCS: frozenset[str] = frozenset(
    {
        ".git",
        ".gitmodules",
        ".claude-plugin",
        "scripts",
        "agents",
        "commands",
        "skills",
        "hooks",
        "templates",
    }
)

# Runtime-component top-level dirs/files per the Anthropic plugins-reference.
# A file that IS or lives under one of these is runtime-essential and is never
# a strip candidate. (`scripts/` is special-cased — see below — because a
# build-only script can legitimately be a strip candidate when nothing at
# runtime references it.)
_RUNTIME_COMPONENT_DIRS: frozenset[str] = frozenset(
    {
        ".claude-plugin",
        "skills",
        "commands",
        "agents",
        "output-styles",
        "themes",
        "hooks",
        "servers",
        "monitors",
        "bin",
        "templates",
    }
)

# Conventional small root files that always ship (and are not strip candidates).
_RUNTIME_ROOT_FILES: frozenset[str] = frozenset(
    {
        ".claude-plugin",
        ".mcp.json",
        ".lsp.json",
        "settings.json",
        "license",
        "license.md",
        "license.txt",
        "changelog.md",
        "readme.md",
        "readme",
        "readme.txt",
        ".gitignore",
        ".gitmodules",
        ".mega-linter.yml",
        ".markdownlint.json",
        "cliff.toml",
    }
)

# ── Build-only taxonomy pattern lists (cover ALL languages) ───────────────────

# 1. BUILD_SOURCE — source + manifests that only PRODUCE the bin/ binaries.
_BUILD_SOURCE_DIR_NAMES: frozenset[str] = frozenset(
    {
        "rust",
        "crates",
        "src",
        "sources",
        "cmd",
        "pkg",
    }
)
# Manifest/build files whose presence in a dir marks the dir as a build crate.
_BUILD_SOURCE_MANIFEST_FILES: frozenset[str] = frozenset(
    {
        "cargo.toml",
        "cargo.lock",
        "go.mod",
        "go.sum",
        "package.swift",
        "build.zig",
        "build.zig.zon",
        "makefile",
        "gnumakefile",
        "cmakelists.txt",
        "meson.build",
        "build",  # Bazel BUILD (lowercased)
        "build.bazel",
        "workspace",
        "workspace.bazel",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "configure",
        "configure.ac",
        "*.cabal",
        "stack.yaml",
    }
)
# Source-file extensions that, when dominant in a dir, mark it BUILD_SOURCE.
_BUILD_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".rs",
        ".go",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hpp",
        ".hh",
        ".zig",
        ".swift",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".cs",
        ".fs",
        ".hs",
    }
)

# 2. RUNTIME_DEP — dependency trees that belong in ${CLAUDE_PLUGIN_DATA}, not shipped.
_RUNTIME_DEP_DIR_NAMES: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "site-packages",
        ".bundle",
        ".pnp",
    }
)

# 3. DEV_ONLY — present for development, not needed to run.
_DEV_ONLY_DIR_NAMES: frozenset[str] = frozenset(
    {
        "tests",
        "test",
        "design",
        "docs",
        "doc",
        "examples",
        "example",
        "samples",
        "sample",
        "benchmarks",
        "benchmark",
        "bench",
        "fixtures",
    }
)
# .github is repo-needed but not install-needed → LOW priority (INFO only).
_DEV_ONLY_LOW_PRIORITY_DIRS: frozenset[str] = frozenset({".github"})

# 4. BUILD_CACHE — regenerable build output/caches that MUST be gitignored.
_BUILD_CACHE_DIR_NAMES: frozenset[str] = frozenset(
    {
        "target",  # Rust / JVM
        ".cargo",
        ".next",
        ".turbo",
        ".parcel-cache",
        "__pycache__",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".gradle",
        ".build",  # Swift
        "deriveddata",
    }
)
# A bare `dist`/`build`/`out` dir is BUILD_CACHE only when regenerable — these
# are treated as build-cache candidates by name (the remediation is to gitignore
# them; if the author hand-authors content there it is their call to keep them).
_BUILD_CACHE_AMBIGUOUS_DIRS: frozenset[str] = frozenset({"dist", "build", "out"})

# File-suffix patterns for individual BUILD_CACHE artifacts (when surfaced as
# large nested files). Object/native-build artifacts OUTSIDE bin/.
_BUILD_CACHE_FILE_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
    ".class",
    ".o",
    ".obj",
    ".a",
    ".log",
)
# egg-info is a dir suffix.
_BUILD_CACHE_DIR_SUFFIXES: tuple[str, ...] = (".egg-info",)

# ${CLAUDE_PLUGIN_ROOT} reference extraction — match ${CLAUDE_PLUGIN_ROOT}/<path>
# and $CLAUDE_PLUGIN_ROOT/<path>. The captured group is the referenced rel path.
_PLUGIN_ROOT_REF_RE = re.compile(
    r"\$\{?CLAUDE_PLUGIN_ROOT\}?/([A-Za-z0-9._\-/]+)"
)

# Files we scan for ${CLAUDE_PLUGIN_ROOT} references (config + component bodies).
_REF_CONFIG_FILES: tuple[str, ...] = (
    ".mcp.json",
    ".lsp.json",
)
_REF_HOOK_CANDIDATES: tuple[str, ...] = (
    "hooks/hooks.json",
    "hooks.json",
)
_REF_MONITOR_CANDIDATES: tuple[str, ...] = ("monitors/monitors.json",)

# Cap how much of any single file we read when grepping for refs (keeps the
# diagnostic fast + bounded on a giant minified blob).
_MAX_REF_SCAN_BYTES = 512_000


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class StripExtractEntry:
    """A concrete ``cpv.strip.extract[]`` recommendation (the engine's schema)."""

    src: str  # e.g. "rust/" — relative to plugin_root, trailing slash kept
    submodule: str  # e.g. "owner/<plugin>-rust" — owner/repo

    def to_dict(self) -> dict[str, str]:
        return {"src": self.src, "submodule": self.submodule}


@dataclass
class Finding:
    """One non-runtime path the diagnostic surfaces (advisory)."""

    path: str  # rel-posix, trailing slash for dirs
    category: str  # one of the CAT_* constants (never RUNTIME_ESSENTIAL here)
    bytes: int
    reason: str
    remediation: str
    strip_extract_entry: StripExtractEntry | None
    tracked: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "category": self.category,
            "bytes": self.bytes,
            "reason": self.reason,
            "remediation": self.remediation,
            "strip_extract_entry": (
                self.strip_extract_entry.to_dict()
                if self.strip_extract_entry is not None
                else None
            ),
            "tracked": self.tracked,
        }


@dataclass
class DiagnosisResult:
    """The full diagnosis — serialised to the documented JSON contract."""

    plugin_path: str
    total_tracked_bytes: int
    shipped_runtime_bytes: int
    strippable_bytes: int
    findings: list[Finding] = field(default_factory=list)
    strip_extract: list[StripExtractEntry] = field(default_factory=list)
    gitignore_add: list[str] = field(default_factory=list)
    claude_plugin_data: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "plugin_path": self.plugin_path,
            "total_tracked_bytes": self.total_tracked_bytes,
            "shipped_runtime_bytes": self.shipped_runtime_bytes,
            "strippable_bytes": self.strippable_bytes,
            "findings": [f.to_dict() for f in self.findings],
            "recommendations": {
                "strip_extract": [e.to_dict() for e in self.strip_extract],
                "gitignore_add": list(self.gitignore_add),
                "claude_plugin_data": list(self.claude_plugin_data),
            },
        }


# ── Helpers ─────────────────────────────────────────────────────────────────


def _dir_size_bytes(root: Path) -> int:
    """Sum of regular-file sizes under ``root`` (du-equivalent).

    Does NOT follow symlinks (``os.walk`` default ``followlinks=False``) and
    silently skips entries it cannot ``stat`` (broken symlinks, races).
    """
    total = 0
    if root.is_file():
        try:
            return root.stat().st_size
        except OSError:
            return 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            fp = Path(dirpath) / name
            try:
                if fp.is_symlink():
                    continue
                total += fp.stat().st_size
            except OSError:
                continue
    return total


def _resolve_plugin_owner_name(plugin_root: Path) -> tuple[str, str]:
    """Best-effort (owner, name) for shaping a submodule recommendation.

    Reads the manifest ``name`` and ``repository`` (``owner/repo`` from a
    GitHub URL). Falls back to ``owner`` / the dir name. Never raises.
    """
    name = plugin_root.name
    owner = "owner"
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            mname = raw.get("name")
            if isinstance(mname, str) and mname.strip():
                name = mname.strip()
            repo = raw.get("repository")
            repo_url = ""
            if isinstance(repo, str):
                repo_url = repo
            elif isinstance(repo, dict):
                url_val = repo.get("url")
                if isinstance(url_val, str):
                    repo_url = url_val
            m = re.search(r"github\.com[:/]+([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$", repo_url)
            if m:
                owner = m.group(1)
    return owner, name


def _read_text_capped(fp: Path) -> str:
    """Read a text file (capped, utf-8, errors-replaced). Empty on failure."""
    try:
        with fp.open("rb") as fh:
            data = fh.read(_MAX_REF_SCAN_BYTES)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def _collect_plugin_root_refs(plugin_root: Path) -> set[str]:
    """Every rel-path referenced via ``${CLAUDE_PLUGIN_ROOT}/...``.

    Scans the manifest, ``hooks.json``, ``.mcp.json``, ``.lsp.json``,
    ``monitors.json``, and every skill/agent body. Each captured ``<path>`` is
    normalised to a leading-segment form too (so a reference to
    ``scripts/foo/bar.py`` marks ``scripts/foo`` and ``scripts`` as referenced,
    keeping the whole referenced subtree ship-always).
    """
    refs: set[str] = set()
    files_to_scan: list[Path] = []

    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        files_to_scan.append(manifest)
    for rel in _REF_CONFIG_FILES:
        fp = plugin_root / rel
        if fp.is_file():
            files_to_scan.append(fp)
    for rel in _REF_HOOK_CANDIDATES:
        fp = plugin_root / rel
        if fp.is_file():
            files_to_scan.append(fp)
    for rel in _REF_MONITOR_CANDIDATES:
        fp = plugin_root / rel
        if fp.is_file():
            files_to_scan.append(fp)

    # Skill + agent bodies (markdown), which may reference helper scripts.
    for component_dir in ("skills", "agents", "commands"):
        base = plugin_root / component_dir
        if not base.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                if name.lower().endswith((".md", ".json")):
                    files_to_scan.append(Path(dirpath) / name)

    for fp in files_to_scan:
        text = _read_text_capped(fp)
        if "CLAUDE_PLUGIN_ROOT" not in text:
            continue
        for m in _PLUGIN_ROOT_REF_RE.finditer(text):
            raw = m.group(1).strip().rstrip("/")
            if not raw:
                continue
            # Normalise: mark the full path AND each ancestor segment so the
            # whole referenced subtree counts as ship-always.
            parts = [p for p in raw.split("/") if p and p != "."]
            for i in range(1, len(parts) + 1):
                refs.add("/".join(parts[:i]))
    return refs


def _is_referenced(rel_top: str, referenced_paths: set[str]) -> bool:
    """True iff ``rel_top`` (a top-level dir/file, no trailing slash) is, or
    contains, a ``${CLAUDE_PLUGIN_ROOT}``-referenced path."""
    if rel_top in referenced_paths:
        return True
    prefix = rel_top + "/"
    return any(ref == rel_top or ref.startswith(prefix) for ref in referenced_paths)


def _dir_dominant_extensions(root: Path, limit: int = 4000) -> set[str]:
    """Set of file extensions (lowercased, with dot) found under ``root``.

    Bounded by ``limit`` files scanned so a huge tree stays fast.
    """
    exts: set[str] = set()
    seen = 0
    for _dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext:
                exts.add(ext)
            seen += 1
            if seen >= limit:
                return exts
    return exts


def _dir_has_manifest(root: Path) -> bool:
    """True iff ``root`` directly contains a recognised build-manifest file."""
    try:
        for child in root.iterdir():
            if not child.is_file():
                continue
            low = child.name.lower()
            if low in _BUILD_SOURCE_MANIFEST_FILES:
                return True
            # glob entries (e.g. *.cabal)
            if low.endswith(".cabal"):
                return True
    except OSError:
        return False
    return False


def _classify_top_entry(
    name: str,
    full: Path,
    *,
    referenced_paths: set[str],
) -> str:
    """Classify a single top-level entry into a CAT_* constant.

    FN-safety order is deliberate:
      1. Reserved / runtime-component / runtime-root-file / ${CLAUDE_PLUGIN_ROOT}-
         referenced  → RUNTIME_ESSENTIAL (never strippable).
      2. Otherwise classify into a build-only category by name/content.
    """
    low = name.lower()
    rel_top = name  # top-level entries are single-segment rel-paths

    # 1. Ship-always guards (FN-safety) — in priority order.
    if rel_top in _RESERVED_SRCS:
        return CAT_RUNTIME
    if rel_top in _RUNTIME_COMPONENT_DIRS:
        return CAT_RUNTIME
    if low in _RUNTIME_ROOT_FILES:
        return CAT_RUNTIME
    if _is_referenced(rel_top, referenced_paths):
        return CAT_RUNTIME

    # 2. Build-only classification.
    is_dir = full.is_dir()

    if is_dir:
        # 2a. RUNTIME_DEP — installed dependency trees. NOTE: `vendor/` is
        # deliberately NOT here. Go-style vendoring ships on purpose (a
        # reproducible build artifact), and `vendor` is in VENDORED_DIR_NAMES, so
        # it falls through to the vendored-runtime guard rather than being
        # (wrongly, destructively) recommended for removal. Only the unambiguous
        # install-on-first-use trees are RUNTIME_DEP.
        if low in _RUNTIME_DEP_DIR_NAMES:
            return CAT_RUNTIME_DEP

        # 2b. BUILD_CACHE — regenerable caches.
        if low in _BUILD_CACHE_DIR_NAMES:
            return CAT_BUILD_CACHE
        if any(low.endswith(suf) for suf in _BUILD_CACHE_DIR_SUFFIXES):
            return CAT_BUILD_CACHE
        if low in _BUILD_CACHE_AMBIGUOUS_DIRS:
            return CAT_BUILD_CACHE

        # 2c. BUILD_SOURCE — a build crate / compiled-language source dir.
        if low in _BUILD_SOURCE_DIR_NAMES or _dir_has_manifest(full):
            return CAT_BUILD_SOURCE
        exts = _dir_dominant_extensions(full)
        # A dir whose files are predominantly compiled-language source is a
        # build crate. Require that it contains at least one such source file
        # and no markdown component bodies dominating it.
        if exts & _BUILD_SOURCE_EXTENSIONS:
            return CAT_BUILD_SOURCE

        # 2d. DEV_ONLY — dev folders.
        if low in _DEV_ONLY_DIR_NAMES:
            return CAT_DEV_ONLY
        if low in _DEV_ONLY_LOW_PRIORITY_DIRS:
            return CAT_DEV_ONLY

        return CAT_UNKNOWN

    # File entries at the top level.
    if any(low.endswith(suf) for suf in _BUILD_CACHE_FILE_SUFFIXES):
        return CAT_BUILD_CACHE
    # A top-level build manifest file (Cargo.toml, etc.) without a wrapping dir.
    if low in _BUILD_SOURCE_MANIFEST_FILES or low.endswith(".cabal"):
        return CAT_BUILD_SOURCE
    return CAT_UNKNOWN


def _remediation_for(category: str) -> str:
    """One-line remediation per category."""
    if category == CAT_BUILD_SOURCE:
        return (
            "Strip into a git submodule via `cpv strip-dev-parts` "
            "(add a cpv.strip.extract[] entry); bin/ keeps the compiled binary."
        )
    if category == CAT_RUNTIME_DEP:
        return (
            "Do not ship: install on first use into ${CLAUDE_PLUGIN_DATA} "
            "(a SessionStart/Setup hook, e.g. `npm install`) and gitignore the copy."
        )
    if category == CAT_DEV_ONLY:
        return (
            "Strip into a submodule via `cpv strip-dev-parts` "
            "(cpv.strip.extract[]) or gitignore if regenerable."
        )
    if category == CAT_BUILD_CACHE:
        return "Gitignore this regenerable build output (it must never be tracked or shipped)."
    return "Review: not recognised as a runtime component — confirm it is needed at install."


def _reason_for(category: str, *, tracked: bool) -> str:
    """One-line reason per category."""
    if category == CAT_BUILD_SOURCE:
        return "Compiled-language build source that only produces bin/ — not run directly at runtime."
    if category == CAT_RUNTIME_DEP:
        return "Installed dependency tree the Anthropic docs say to install at runtime, not ship."
    if category == CAT_DEV_ONLY:
        return "Development-only content, not needed to run the plugin."
    if category == CAT_BUILD_CACHE:
        if tracked:
            return "Regenerable build cache that is TRACKED (it ships) — must be gitignored."
        return "Regenerable build cache."
    return "Unrecognised top-level entry (not a known runtime component)."


# ── Core diagnosis ──────────────────────────────────────────────────────────


def diagnose(plugin_path: str | Path, *, threshold_mb: float = 1.0) -> DiagnosisResult:
    """Diagnose a plugin tree. Pure (read-only) — returns a DiagnosisResult.

    ``threshold_mb`` controls which UNKNOWN entries are surfaced as findings
    (a tiny unknown file is noise); BUILD_*/RUNTIME_DEP/DEV_ONLY findings are
    always surfaced regardless of size because their remediation matters even
    when small (a tracked build cache is invalid at any size).
    """
    plugin_root = Path(plugin_path).resolve()
    threshold_bytes = int(threshold_mb * 1_000_000)

    result = DiagnosisResult(
        plugin_path=str(plugin_root),
        total_tracked_bytes=0,
        shipped_runtime_bytes=0,
        strippable_bytes=0,
    )

    if not plugin_root.is_dir():
        # Non-existent / not-a-dir target → empty advisory result (exit 0).
        return result

    # Git-accuracy: paths that are gitignored AND untracked are already
    # not-shipped, so they are not counted as shippable mass or double-flagged.
    # None → not a git tree → the present tree IS the artifact (count all).
    unshipped = gitignored_unshipped_paths(plugin_root)

    referenced_paths = _collect_plugin_root_refs(plugin_root)
    owner, plugin_name = _resolve_plugin_owner_name(plugin_root)
    # A path the plugin ALREADY strips (declared in cpv.strip.extract[]) must
    # not be re-recommended — read the real config via the shared helper so we
    # stay in lockstep with cpv_strip_dev's own parsing. A RAW git submodule
    # (declared in .gitmodules but NOT via cpv.strip) is ALSO already separated:
    # Claude Code never clones submodules, so its content never ships. Union both
    # so neither is wrongly re-recommended for stripping.
    already_stripped = _already_stripped_srcs(plugin_root) | _gitmodules_submodule_paths(plugin_root)

    try:
        entries = sorted(plugin_root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return result

    for entry in entries:
        name = entry.name
        rel_posix = name
        is_dir = entry.is_dir()
        rel_for_finding = rel_posix + "/" if is_dir else rel_posix

        # Skip a top-level entry that is entirely gitignored+untracked — it does
        # not ship, so it is neither runtime mass nor a strip candidate. A
        # collapsed-dir entry from `git ls-files --directory` matches by prefix.
        if unshipped is not None and path_is_unshipped(rel_posix, unshipped):
            continue

        size = _dir_size_bytes(entry)
        result.total_tracked_bytes += size

        # Already declared in cpv.strip.extract[] — the heavy content lives in a
        # submodule Claude Code does not clone; what remains here is the small
        # pointer. Do not re-recommend stripping it.
        if rel_posix in already_stripped:
            result.shipped_runtime_bytes += size
            continue

        category = _classify_top_entry(
            name,
            entry,
            referenced_paths=referenced_paths,
        )

        if category == CAT_RUNTIME:
            result.shipped_runtime_bytes += size
            continue

        # For entries we could NOT positively classify into a build-only
        # category, fall back to treating a vendored / excluded subtree as
        # runtime. CRITICAL: this fallback applies ONLY to CAT_UNKNOWN — a
        # positively-classified RUNTIME_DEP (node_modules/, .venv/) or
        # BUILD_CACHE (dist/, build/, __pycache__/) MUST still be surfaced, even
        # though those names also appear in VENDORED_DIR_NAMES. The vendored
        # guard exists to avoid false-flagging an UNRECOGNISED vendored subtree
        # (external/, third_party/, a .gitmodules/cpv.exclude_paths path) as
        # mass — not to swallow the build-only categories the diagnostic is for.
        if category == CAT_UNKNOWN:
            if is_vendored_path(rel_posix, plugin_root):
                result.shipped_runtime_bytes += size
                continue
            if size < threshold_bytes:
                # A small unknown is noise; never claim it is strippable.
                result.shipped_runtime_bytes += size
                continue

        # A non-runtime finding.
        tracked = unshipped is None or not path_is_unshipped(rel_posix, unshipped)

        # `.github/` (and similar) are REPO-needed (CI) but NOT install-needed —
        # Claude Code's plugin install never ships them anyway. So they are a
        # low-priority INFO finding: surfaced for awareness, but NOT a strip
        # candidate (stripping CI would break the repo) and NOT counted as
        # recoverable install savings.
        low_priority = is_dir and name.lower() in _DEV_ONLY_LOW_PRIORITY_DIRS

        strip_entry: StripExtractEntry | None = None
        if not low_priority and category in (CAT_BUILD_SOURCE, CAT_DEV_ONLY):
            # Shape a concrete cpv.strip.extract[] recommendation. Submodule
            # name follows the PSS pattern: <plugin>-<dirslug>.
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "dev"
            strip_entry = StripExtractEntry(
                src=rel_posix + "/" if is_dir else rel_posix,
                submodule=f"{owner}/{plugin_name}-{slug}",
            )

        if low_priority:
            reason = "Repo-needed (CI) but not shipped to plugin installs — informational only."
            remediation = "No action needed: this is not part of the installed plugin artifact."
        else:
            reason = _reason_for(category, tracked=tracked)
            remediation = _remediation_for(category)

        finding = Finding(
            path=rel_for_finding,
            category=category,
            bytes=size,
            reason=reason,
            remediation=remediation,
            strip_extract_entry=strip_entry,
            tracked=tracked,
        )
        result.findings.append(finding)
        if not low_priority:
            result.strippable_bytes += size

        # Build the recommendation buckets.
        if strip_entry is not None:
            result.strip_extract.append(strip_entry)
        if category == CAT_RUNTIME_DEP:
            if rel_for_finding not in result.gitignore_add:
                result.gitignore_add.append(rel_for_finding)
            if rel_for_finding not in result.claude_plugin_data:
                result.claude_plugin_data.append(rel_for_finding)
        if category == CAT_BUILD_CACHE:
            if rel_for_finding not in result.gitignore_add:
                result.gitignore_add.append(rel_for_finding)

    # Stable ordering: largest finding first (human table), then by path.
    result.findings.sort(key=lambda f: (-f.bytes, f.path))
    return result


# ── Existing-config awareness ─────────────────────────────────────────────────


def _already_stripped_srcs(plugin_root: Path) -> set[str]:
    """The ``src`` paths already declared in the plugin's ``cpv.strip.extract``.

    A path already stripped should not be re-recommended; we read the real
    config block via the shared ``load_strip_config`` helper so the diagnostic
    stays in lockstep with the engine's parsing.
    """
    out: set[str] = set()
    strip = load_strip_config(plugin_root)
    extract = strip.get("extract")
    if isinstance(extract, list):
        for item in extract:
            if isinstance(item, dict):
                src = item.get("src")
                if isinstance(src, str) and src.strip():
                    out.add(src.strip().rstrip("/"))
    return out


def _gitmodules_submodule_paths(plugin_root: Path) -> set[str]:
    """Top-level segments of every git-submodule path declared in `.gitmodules`.

    A directory that is a git submodule is ALREADY separated: Claude Code's
    shallow-clone install never recurses submodules, so the submodule's content
    never ships — even when the maintainer never declared a `cpv.strip.extract[]`
    entry for it. Such a RAW submodule must NOT be re-recommended for stripping.

    We reuse `cpv_validation_common._read_gitmodules_paths` (the canonical,
    cached `.gitmodules` parser shared with `is_vendored_path`) so the diagnostic
    stays in lockstep, then reduce each declared `path = <p>` to its TOP-LEVEL
    POSIX segment — because the scan loop compares bare top-level entry names
    (e.g. `"rust"`, not `"rust/"` or a nested `"dev/tests"`) against the
    already-stripped set. A nested submodule (`path = dev/tests`) collapses to its
    top-level dir (`dev`), which is the granularity the loop iterates.
    """
    out: set[str] = set()
    for raw in _read_gitmodules_paths(str(plugin_root.resolve())):
        # `_read_gitmodules_paths` already strips a trailing slash; take the
        # first POSIX segment to match the loop's top-level comparison.
        top = PurePosixPath(raw).parts[0] if raw else ""
        if top:
            out.add(top)
    return out


# ── Rendering ─────────────────────────────────────────────────────────────────


def _fmt_mb(num_bytes: int) -> str:
    return f"{num_bytes / 1_000_000:.2f}"


def render_human(result: DiagnosisResult) -> str:
    """Numbered findings table + a summary line."""
    lines: list[str] = []
    lines.append(f"Plugin: {result.plugin_path}")
    lines.append(
        f"Total tracked: {_fmt_mb(result.total_tracked_bytes)} MB  |  "
        f"Runtime: {_fmt_mb(result.shipped_runtime_bytes)} MB  |  "
        f"Strippable: {_fmt_mb(result.strippable_bytes)} MB"
    )
    lines.append("")
    if not result.findings:
        lines.append("No non-runtime mass detected — this plugin is lean. (Advisory; nothing to do.)")
        return "\n".join(lines)

    lines.append("Non-runtime findings (ADVISORY — nothing is moved or deleted):")
    lines.append("")
    header = f"{'#':>2}  {'PATH':<28}  {'CATEGORY':<17}  {'SIZE(MB)':>9}  REMEDIATION"
    lines.append(header)
    lines.append("-" * len(header))
    for i, f in enumerate(result.findings, start=1):
        tracked_mark = "" if f.tracked else " (untracked)"
        lines.append(
            f"{i:>2}  {f.path:<28}  {f.category:<17}  {_fmt_mb(f.bytes):>9}  "
            f"{f.remediation}{tracked_mark}"
        )
    lines.append("")
    n = len(result.findings)
    lines.append(
        f"Potential install savings: {_fmt_mb(result.strippable_bytes)} MB across {n} paths"
    )
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Always exits 0 (advisory; never blocks)."""
    parser = argparse.ArgumentParser(
        prog="cpv_diagnose_architecture.py",
        description=(
            "ADVISORY lean-plugin architecture diagnostic. Detects build-only "
            "mass / runtime deps / dev dirs / build caches a plugin ships, and "
            "recommends the existing CPV strip-dev / ${CLAUDE_PLUGIN_DATA} "
            "separation. Read-only: never moves or deletes anything."
        ),
    )
    parser.add_argument("plugin_path", help="Path to the plugin directory to diagnose")
    parser.add_argument("--json", action="store_true", help="Emit the JSON contract to stdout (and nothing else)")
    parser.add_argument(
        "--threshold-mb",
        type=float,
        default=1.0,
        help="Size (MB) below which an UNKNOWN entry is treated as noise (default 1.0)",
    )
    args = parser.parse_args(argv)

    result = diagnose(args.plugin_path, threshold_mb=args.threshold_mb)

    if args.json:
        sys.stdout.write(json.dumps(result.to_json_dict(), indent=2))
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_human(result))
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
