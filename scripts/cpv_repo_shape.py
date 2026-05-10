#!/usr/bin/env python3
"""Auto-detect repo shape and configuration for the universal publish pipeline.

TRDD-9065109a Phase B (zero-config publish pipeline). The classifier in this
module returns one of seven RepoShape kinds based on signals already present
in the project tree (no env-vars, no per-plugin customization):

    single-plugin           — `.claude-plugin/plugin.json` only
    marketplace-hub         — `.claude-plugin/marketplace.json` only
    nested-monorepo         — both, plus `plugins/<name>/.claude-plugin/`
    marketplace-in-plugin   — both at root, single self-entry source `./`
    workspace-multi-git     — multiple subfolders, each with its own .git/ AND
                              .claude-plugin/ (e.g. dev workspace with
                              several plugin repos cloned side-by-side)
    submodule-bundle        — plugin.json + .gitmodules referencing binaries
                              built from a tracked external source repo
                              (perfect-skill-suggester case)
    unknown                 — none of the above; not a CPV-managed repo

`extract_config_from_tree(root, shape)` then pulls every value publish.py
needs out of the tree:
    - plugin name / version (from plugin.json)
    - GitHub remote owner / repo (from `git remote get-url origin`)
    - marketplace owner / repo (from notify-marketplace.yml env vars)
    - submodule paths (from .gitmodules)
    - workspace children (when shape is workspace-multi-git)

Why a separate module instead of extending publish.py:
  - `publish.py` is already 2700+ lines; a focused classifier module is
    easier to test and re-use.
  - Other entry points (cpv-doctor, plugin-creator, plugin-fixer) need the
    same detection logic. Promoting it out of publish.py is the cheapest
    way to share without circular imports.
  - Per the TRDD: "ONE identical publish.py byte-for-byte across every
    plugin." This module is the auto-detection layer that makes that
    promise hold.

Backward compatibility: this module DOES NOT replace `publish.detect_layout`.
The legacy A/B/none classifier stays in publish.py for the existing
14-gate pipeline. `cpv_repo_shape` extends the taxonomy with Layout C,
workspace-multi-git, and submodule-bundle so future zero-config callers
have a single classifier to consult. Both classifiers must agree on
overlapping cases (Layout A → single-plugin, Layout B from the marketplace
root → nested-monorepo) — `TestBackwardCompatWithDetectLayout` enforces
this.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# -----------------------------------------------------------------------------
# Public dataclasses
# -----------------------------------------------------------------------------

# Allowed shape kinds — keep this list aligned with the TRDD §B detection
# table. New kinds are additive; existing kinds must NEVER be renamed
# without a coordinated migration of all callers (publish.py, plugin-creator,
# cpv-doctor, etc.).
SHAPE_KINDS = (
    "single-plugin",
    "marketplace-hub",
    "nested-monorepo",
    "marketplace-in-plugin",
    "workspace-multi-git",
    "submodule-bundle",
    "unknown",
)


@dataclass(frozen=True)
class RepoShape:
    """Result of `detect_repo_shape()`.

    `kind` is one of SHAPE_KINDS. `root` is the absolute path the detection
    was run against. The other fields are populated only when relevant to
    the kind:

      - children — workspace-multi-git: list of subfolder paths that each
        host their own plugin repo (sorted alphabetically for stability).
      - submodule_paths — submodule-bundle: list of paths from .gitmodules
        (sorted alphabetically).
      - has_plugin_json / has_marketplace_json — convenience flags so
        callers don't have to re-stat the manifests after the classifier
        already touched them.
    """

    kind: str
    root: Path
    has_plugin_json: bool = False
    has_marketplace_json: bool = False
    children: list[Path] = field(default_factory=list)
    submodule_paths: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in SHAPE_KINDS:
            raise ValueError(f"unknown shape kind: {self.kind!r} (allowed: {SHAPE_KINDS})")


@dataclass(frozen=True)
class RepoConfig:
    """Result of `extract_config_from_tree()`.

    Every field is auto-detected from the tree. None means "signal not
    present" — never a fallback default. publish.py is responsible for
    deciding whether a None field is a fatal config error (e.g. missing
    plugin name) or a benign one (e.g. missing marketplace dispatch on
    a plain single-plugin repo).
    """

    shape: RepoShape
    plugin_name: str | None = None
    plugin_version: str | None = None
    marketplace_name: str | None = None
    github_owner: str | None = None
    github_repo: str | None = None
    marketplace_owner: str | None = None
    marketplace_repo: str | None = None
    submodule_paths: list[str] = field(default_factory=list)
    children: list[tuple[Path, RepoShape]] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Detection helpers
# -----------------------------------------------------------------------------


def _read_plugin_json(root: Path) -> dict | None:
    """Read .claude-plugin/plugin.json if present, return parsed dict or None.

    Returns None on any read or parse failure — never raises. publish.py is
    the right place to validate the manifest contents; this module is purely
    descriptive.
    """
    pj = root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_marketplace_json(root: Path) -> dict | None:
    """Read .claude-plugin/marketplace.json if present, return parsed dict or None."""
    mp = root / ".claude-plugin" / "marketplace.json"
    if not mp.is_file():
        return None
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _list_workspace_plugin_dirs(root: Path) -> list[Path]:
    """Find every immediate subdirectory that has its own .git/ AND
    .claude-plugin/ — i.e. plugin repos cloned side-by-side in a workspace.

    Returns a sorted list (alphabetical). Sort order matters for the
    interactive picker so users see a deterministic numbered list.
    """
    children: list[Path] = []
    if not root.is_dir():
        return children
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        # Must have ITS OWN git repo (not a subdir of a parent git tree)
        # AND its own .claude-plugin manifest.
        has_git = (entry / ".git").exists()
        has_cpd = (entry / ".claude-plugin").is_dir()
        if has_git and has_cpd:
            children.append(entry)
    return sorted(children)


def _read_gitmodules_paths(root: Path) -> list[str]:
    """Parse .gitmodules and return the list of submodule paths.

    Pure-text parser (no third-party dep). The .gitmodules format is INI-ish:

        [submodule "name"]
            path = some/path
            url = https://...

    We only need the `path = ...` values. Empty list when .gitmodules is
    absent or unreadable.
    """
    gm = root / ".gitmodules"
    if not gm.is_file():
        return []
    try:
        text = gm.read_text(encoding="utf-8")
    except OSError:
        return []
    paths: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("path") and "=" in stripped:
            _, value = stripped.split("=", 1)
            value = value.strip()
            if value:
                paths.append(value)
    return sorted(paths)


def _is_self_marketplace_in_plugin(root: Path, mp_data: dict, pj_data: dict) -> bool:
    """Layout C check: marketplace.json has exactly one plugin entry, and
    that entry's source points at the current directory ("./" or ".").

    Defensive: malformed manifest (missing `plugins`, wrong types) returns
    False so the caller falls through to nested-monorepo detection.
    """
    plugins = mp_data.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1:
        return False
    entry = plugins[0]
    if not isinstance(entry, dict):
        return False
    src = entry.get("source")
    plugin_name = pj_data.get("name")
    entry_name = entry.get("name")
    # Name must match plugin.json's name — otherwise it's not the self-entry.
    if entry_name != plugin_name:
        return False
    # Accept both legacy string-source and modern object-source:
    #   "source": "./"
    #   "source": {"source": "relative-path", "path": "./"}
    if isinstance(src, str):
        return src.strip() in ("./", ".", "")
    if isinstance(src, dict):
        path_val = src.get("path")
        if isinstance(path_val, str):
            return path_val.strip() in ("./", ".", "")
    return False


def _has_nested_plugin_dirs(root: Path) -> bool:
    """Check whether `root/plugins/<name>/.claude-plugin/plugin.json` exists
    for at least one direct child of `plugins/`.

    Used to disambiguate Layout B (nested-monorepo) from Layout C
    (marketplace-in-plugin) when both manifests live at the root.
    """
    plugins_dir = root / "plugins"
    if not plugins_dir.is_dir():
        return False
    for entry in plugins_dir.iterdir():
        if not entry.is_dir():
            continue
        if (entry / ".claude-plugin" / "plugin.json").is_file():
            return True
    return False


# -----------------------------------------------------------------------------
# Public API: detect_repo_shape()
# -----------------------------------------------------------------------------


def detect_repo_shape(root: Path) -> RepoShape:
    """Classify the repo at `root` as one of the seven SHAPE_KINDS.

    Detection priority (first match wins):
      1. plugin.json + .gitmodules       → submodule-bundle
      2. marketplace.json AND plugin.json
            with self-entry at "./"      → marketplace-in-plugin (Layout C)
            with plugins/<n>/ subdirs    → nested-monorepo (Layout B)
      3. marketplace.json only           → marketplace-hub
      4. plugin.json only                → single-plugin (Layout A)
      5. multiple subdirs each with
         their own .git/ + .claude-plugin → workspace-multi-git
      6. otherwise                       → unknown

    The classifier is read-only — never mutates the tree, never spawns
    subprocesses (except for git remote lookups in extract_config_from_tree).
    """
    root = root.resolve()
    pj_data = _read_plugin_json(root)
    mp_data = _read_marketplace_json(root)
    has_pj = pj_data is not None
    has_mp = mp_data is not None
    sub_paths = _read_gitmodules_paths(root)

    # Step 1: Layout-aware classification when marketplace.json is at root.
    # The layout tells the publish pipeline whether to register with a
    # marketplace, dispatch to one, or treat the repo as a hub. Submodule
    # presence does NOT override layout — it's exposed via submodule_paths
    # on the returned shape so callers can layer the extra verify gates
    # on top.
    if pj_data is not None and mp_data is not None and _is_self_marketplace_in_plugin(root, mp_data, pj_data):
        return RepoShape(
            kind="marketplace-in-plugin",
            root=root,
            has_plugin_json=True,
            has_marketplace_json=True,
            submodule_paths=sub_paths,
        )

    if has_mp and _has_nested_plugin_dirs(root):
        return RepoShape(
            kind="nested-monorepo",
            root=root,
            has_plugin_json=has_pj,
            has_marketplace_json=True,
            submodule_paths=sub_paths,
        )

    if has_mp and not has_pj:
        return RepoShape(
            kind="marketplace-hub",
            root=root,
            has_plugin_json=False,
            has_marketplace_json=True,
            submodule_paths=sub_paths,
        )

    # Marketplace.json + plugin.json BOTH present, no Layout B/C signal →
    # the plugin.json belongs to the hub itself (uncommon but valid) →
    # marketplace-hub.
    if has_mp and has_pj:
        return RepoShape(
            kind="marketplace-hub",
            root=root,
            has_plugin_json=True,
            has_marketplace_json=True,
            submodule_paths=sub_paths,
        )

    # Step 2: No marketplace at root → single-plugin world.
    # Submodule-bundle is a single-plugin specialization: same publish
    # pipeline, plus the per-submodule reachability gates.
    if has_pj and sub_paths:
        return RepoShape(
            kind="submodule-bundle",
            root=root,
            has_plugin_json=True,
            has_marketplace_json=False,
            submodule_paths=sub_paths,
        )

    if has_pj:
        return RepoShape(
            kind="single-plugin",
            root=root,
            has_plugin_json=True,
            has_marketplace_json=False,
        )

    # Step 3: No manifests at root → workspace check.
    children = _list_workspace_plugin_dirs(root)
    if len(children) >= 2:
        return RepoShape(
            kind="workspace-multi-git",
            root=root,
            children=children,
        )

    return RepoShape(kind="unknown", root=root)


# -----------------------------------------------------------------------------
# Public API: extract_config_from_tree()
# -----------------------------------------------------------------------------

# Regex shared with publish.py's _parse_owner_repo_from_remote — kept in
# sync so any URL the legacy parser handles, the new parser handles too.
_GITHUB_URL_RE = re.compile(
    r"""
    ^                                  # anchored at start
    (?:                                # one of:
        git@github\.com:               #   ssh shorthand: git@github.com:owner/repo
      | https?://github\.com/          #   https://github.com/owner/repo
      | ssh://git@github\.com/         #   ssh://git@github.com/owner/repo
    )
    (?P<owner>[^/]+)                   # owner segment
    /
    (?P<repo>[^/.]+?)                  # repo segment (lazy: stop before .git)
    (?:\.git)?                         # optional .git suffix
    /?                                 # optional trailing slash
    $
    """,
    re.VERBOSE,
)


def parse_owner_repo_from_remote(url: str) -> tuple[str, str] | None:
    """Parse a GitHub remote URL into (owner, repo). Returns None for
    non-GitHub URLs and for unparseable input.

    Mirrors publish._parse_owner_repo_from_remote so callers that import
    cpv_repo_shape don't have to also import publish.py just for URL parsing.
    """
    if not url:
        return None
    m = _GITHUB_URL_RE.match(url.strip())
    if not m:
        return None
    return m.group("owner"), m.group("repo")


def _git_remote_url(root: Path) -> str | None:
    """Return the URL of `origin` for the git repo at `root`, or None.

    Uses subprocess (no GitPython dep). Timeouts at 10 s — `git remote
    get-url` is a local read, so anything slower is broken state.
    """
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url or None


_NOTIFY_OWNER_RE = re.compile(
    r"^\s*MARKETPLACE_OWNER:\s*['\"]?([^'\"\s]+)['\"]?\s*$",
    re.MULTILINE,
)
_NOTIFY_REPO_RE = re.compile(
    r"^\s*MARKETPLACE_REPO:\s*['\"]?([^'\"\s]+)['\"]?\s*$",
    re.MULTILINE,
)


def _parse_notify_workflow(root: Path) -> tuple[str | None, str | None]:
    """Read .github/workflows/notify-marketplace.yml and return
    (MARKETPLACE_OWNER, MARKETPLACE_REPO) or (None, None).

    Mirrors publish._parse_notify_workflow — kept in sync so a fix landed
    in publish.py propagates to the new classifier with no extra work.
    """
    wf = root / ".github" / "workflows" / "notify-marketplace.yml"
    if not wf.is_file():
        return None, None
    try:
        text = wf.read_text(encoding="utf-8")
    except OSError:
        return None, None
    m_owner = _NOTIFY_OWNER_RE.search(text)
    m_repo = _NOTIFY_REPO_RE.search(text)
    return (
        m_owner.group(1) if m_owner else None,
        m_repo.group(1) if m_repo else None,
    )


def extract_config_from_tree(root: Path, shape: RepoShape) -> RepoConfig:
    """Auto-detect every value publish.py needs out of the tree.

    Returns a RepoConfig with None for any signal that's absent. Callers
    decide what's fatal; this function never raises on missing data.
    """
    root = root.resolve()

    plugin_name: str | None = None
    plugin_version: str | None = None
    if shape.has_plugin_json:
        pj = _read_plugin_json(root)
        if pj is not None:
            name_val = pj.get("name")
            ver_val = pj.get("version")
            if isinstance(name_val, str):
                plugin_name = name_val
            if isinstance(ver_val, str):
                plugin_version = ver_val

    marketplace_name: str | None = None
    if shape.has_marketplace_json:
        mp = _read_marketplace_json(root)
        if mp is not None:
            mp_name = mp.get("name")
            if isinstance(mp_name, str):
                marketplace_name = mp_name

    # GitHub origin owner/repo — None when origin is missing or non-GitHub.
    github_owner: str | None = None
    github_repo: str | None = None
    remote_url = _git_remote_url(root)
    if remote_url:
        parsed = parse_owner_repo_from_remote(remote_url)
        if parsed is not None:
            github_owner, github_repo = parsed

    # Marketplace owner/repo from notify-marketplace.yml.
    mkt_owner, mkt_repo = _parse_notify_workflow(root)

    # Workspace children: detect each child's own shape so the picker can
    # show "[1] plugin-a (single-plugin)" style entries.
    children: list[tuple[Path, RepoShape]] = []
    if shape.kind == "workspace-multi-git":
        for child_path in shape.children:
            child_shape = detect_repo_shape(child_path)
            children.append((child_path, child_shape))

    return RepoConfig(
        shape=shape,
        plugin_name=plugin_name,
        plugin_version=plugin_version,
        marketplace_name=marketplace_name,
        github_owner=github_owner,
        github_repo=github_repo,
        marketplace_owner=mkt_owner,
        marketplace_repo=mkt_repo,
        submodule_paths=list(shape.submodule_paths),
        children=children,
    )


# -----------------------------------------------------------------------------
# Public API: pick_workspace_child()
# -----------------------------------------------------------------------------


def pick_workspace_child(
    shape: RepoShape,
    *,
    input_fn: Callable[[str], str] = input,
) -> Path | None:
    """Interactive picker for workspace-multi-git shapes.

    Prints a numbered table of children and asks the user to pick one.
    Returns the selected child path, or None when:
      - the user picks 0 (cancel)
      - the input is malformed (not an int, out of range)

    `input_fn` is injectable so tests can drive the picker without mocking
    stdin. Keeps the function pure-stdlib + dependency-free.

    Raises ValueError if called on a non-workspace shape — that's a
    programming error, not a user error.
    """
    if shape.kind != "workspace-multi-git":
        raise ValueError(f"pick_workspace_child requires shape.kind == 'workspace-multi-git', got {shape.kind!r}")
    children = list(shape.children)
    if not children:
        return None

    # Print the menu so the human can see the choices. Even when input_fn
    # is injected for tests, the print serves as documentation of what
    # the user would have seen on a real run.
    print(f"\nWorkspace at {shape.root} contains {len(children)} plugin(s):")
    for idx, child in enumerate(children, start=1):
        print(f"  [{idx}] {child.name}")
    print("  [0] cancel")

    raw = input_fn("Pick: ").strip()
    try:
        choice = int(raw)
    except ValueError:
        return None
    if choice == 0:
        return None
    if 1 <= choice <= len(children):
        return children[choice - 1]
    return None


__all__ = [
    "SHAPE_KINDS",
    "RepoShape",
    "RepoConfig",
    "detect_repo_shape",
    "extract_config_from_tree",
    "pick_workspace_child",
    "parse_owner_repo_from_remote",
]


# -----------------------------------------------------------------------------
# CLI entry point — `python scripts/cpv_repo_shape.py [<path>]`
# -----------------------------------------------------------------------------


def _format_config_for_human(cfg: "RepoConfig") -> str:
    """Pretty-print a RepoConfig in a stable layout for the CLI.

    Format is intentionally plain-text (no JSON, no colors) so it grep-pipes
    cleanly. Keys missing from the config are emitted with `<not-detected>`
    as the value — that way a user inspecting the output can see which
    detection signals failed.
    """
    lines = [
        f"shape:                 {cfg.shape.kind}",
        f"root:                  {cfg.shape.root}",
        f"plugin_name:           {cfg.plugin_name or '<not-detected>'}",
        f"plugin_version:        {cfg.plugin_version or '<not-detected>'}",
        f"marketplace_name:      {cfg.marketplace_name or '<not-detected>'}",
        f"github_owner:          {cfg.github_owner or '<not-detected>'}",
        f"github_repo:           {cfg.github_repo or '<not-detected>'}",
        f"marketplace_owner:     {cfg.marketplace_owner or '<not-detected>'}",
        f"marketplace_repo:      {cfg.marketplace_repo or '<not-detected>'}",
        f"submodule_paths:       {cfg.submodule_paths or '<none>'}",
    ]
    if cfg.children:
        lines.append("children:")
        for child_path, child_shape in cfg.children:
            lines.append(f"  - {child_path.name} ({child_shape.kind})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: `python scripts/cpv_repo_shape.py [<path>]`.

    Prints the detected shape + auto-extracted config for the given path
    (default: cwd). Useful for users debugging "why does publish.py think
    my repo is X?" without having to drop into a Python REPL.
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(
            "Usage: python scripts/cpv_repo_shape.py [<path>]\n"
            "\n"
            "Detects the repo shape (one of: " + ", ".join(SHAPE_KINDS) + ")\n"
            "and prints the auto-detected config for the given directory\n"
            "(default: current working directory).\n"
        )
        return 0
    target = Path(args[0]).resolve() if args else Path.cwd()
    if not target.is_dir():
        print(f"ERROR: not a directory: {target}", file=sys.stderr)
        return 1
    shape = detect_repo_shape(target)
    cfg = extract_config_from_tree(target, shape)
    print(_format_config_for_human(cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
