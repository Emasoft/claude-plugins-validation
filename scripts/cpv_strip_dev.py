#!/usr/bin/env python3
"""TRDD-793ac32a — strip-dev-parts engine.

Moves dev-only artefacts (tests/, design/, git-hooks/, …) from a
plugin's MAIN repo into a per-plugin git submodule pointing at a fresh
GitHub repo. Claude Code's shallow-clone install does NOT recurse into
submodules, so the submodule content does not ship to end users —
saving ~12 MB per CPV-style install.

Pattern verified empirically against PSS (`perfect-skill-suggester`):
PSS's `rust/` submodule is 1.2 MB pointer in cache vs. gigabytes in
dev. This module generalises the pattern to N submodules per plugin.

This file is the **engine** (pure functions). The CLI surface lives in
`commands/cpv-strip-dev-parts.md`. The end-to-end command flow is:

    cpv strip-dev-parts <plugin>          # interactive
    cpv strip-dev-parts <plugin> --auto   # standard rules, no prompts
    cpv strip-dev-parts <plugin> --dry-run
    cpv strip-dev-parts <plugin> --restore

Security model is documented in:
  * `cpv_validate_gitmodules.py` — `.gitmodules` URL allowlist
  * §2.3-§2.6 of TRDD-793ac32a (path traversal, working-tree safety,
    GH repo creation safety, history preservation)

Idempotent state machine (per TRDD-793ac32a §2.5):

    INIT → REPO_VERIFIED → REPO_CREATED → CONTENT_PUSHED →
    SUBMODULE_ADDED → COMMITTED → DONE

State checkpointed at `<plugin_root>/.cpv-strip-state.json` so a
crashed run can resume from the last successful step.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# Network-resilience helpers — wrap gh CLI / git push in retry-on-transient
# loops. Imported via sibling lookup so cpv_strip_dev stays usable when
# called as a script (sys.path may not include the scripts/ folder).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cpv_network_resilience import gh_with_retry, git_with_retry  # noqa: E402

# ── Constants ──────────────────────────────────────────────────────────────────

# Whitelist regex for `cpv.strip.extract[].src` paths.
# Lowercase + alnum + hyphen + underscore + slash, no `..`, no leading `/`.
_SAFE_SRC_RE = re.compile(r"^[a-z][a-z0-9_-]*(/[a-z][a-z0-9_-]*)*/?$")

# Reserved paths that may NEVER be extracted (would brick the plugin).
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

# Default extraction targets (per TRDD-793ac32a §4.2).
# PSS-style default: ONE submodule per plugin. tests/ is typically the
# heaviest dev folder. Plugins with additional heavy dev folders can opt
# in by adding more entries to cpv.strip.extract[] in plugin.json.
DEFAULT_EXTRACT_TARGETS: tuple[str, ...] = ("tests/",)

# State checkpoint filename at plugin root.
STATE_FILENAME: str = ".cpv-strip-state.json"


class StripState(str, Enum):
    """Idempotent state-machine states (per TRDD-793ac32a §2.5)."""

    INIT = "INIT"
    REPO_VERIFIED = "REPO_VERIFIED"
    REPO_CREATED = "REPO_CREATED"
    CONTENT_PUSHED = "CONTENT_PUSHED"
    SUBMODULE_ADDED = "SUBMODULE_ADDED"
    COMMITTED = "COMMITTED"
    DONE = "DONE"


# Ordered transitions; index in this tuple = "progress score".
_STATE_ORDER: tuple[StripState, ...] = (
    StripState.INIT,
    StripState.REPO_VERIFIED,
    StripState.REPO_CREATED,
    StripState.CONTENT_PUSHED,
    StripState.SUBMODULE_ADDED,
    StripState.COMMITTED,
    StripState.DONE,
)


# ── Exceptions ─────────────────────────────────────────────────────────────────


class StripError(RuntimeError):
    """Base class for all strip-dev-parts engine errors.

    Carries a stable error code (STRIP-Wxxx for working-tree safety,
    STRIP-Exxx for path errors, STRIP-Gxxx for gh-repo errors,
    STRIP-Hxxx for history errors).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class ExtractTarget:
    """A single `cpv.strip.extract[]` entry, normalised."""

    src: str  # "tests/" — relative to plugin_root
    submodule: str  # "Emasoft/cpv-tests" — owner/repo
    submodule_path: str  # "dev/tests/" — where it lands in plugin_root
    submodule_commit_sha: str = ""  # empty if not yet pinned

    @property
    def url(self) -> str:
        return f"https://github.com/{self.submodule}.git"


@dataclass
class StripPlan:
    """Plan for a strip operation. Built by `build_plan`, consumed by `apply_plan`."""

    plugin_root: Path
    targets: list[ExtractTarget]
    keep_in_main: list[str] = field(default_factory=list)
    keep_dev_configs: bool = False
    symlinks_for_devs: bool = True
    history_preserve: bool = True


# ── Path-traversal defense (STRIP-E001..E006) ─────────────────────────────────


def validate_src_path(src: str, plugin_root: Path) -> Path:
    """Validate an extract-source path. Raises `StripError` on rejection.

    Returns the resolved Path on success. Per TRDD-793ac32a §2.3, ALL
    of these checks must succeed before any write/move happens.
    """
    if not src:
        raise StripError("STRIP-E003", "src path is empty")
    if not _SAFE_SRC_RE.match(src):
        raise StripError(
            "STRIP-E003",
            (
                f"src '{src}' does not match safe-name pattern "
                f"(lowercase + alnum + hyphen + underscore + slash; no `..` or `/` prefix)"
            ),
        )
    if src.rstrip("/") in _RESERVED_SRCS:
        raise StripError(
            "STRIP-E006",
            (
                f"src '{src}' is a reserved path (would brick the runtime plugin); "
                f"reserved set = {sorted(_RESERVED_SRCS)}"
            ),
        )

    repo_resolved = plugin_root.resolve()
    raw_candidate = plugin_root / src  # NOT resolved — keeps symlinks intact
    candidate = raw_candidate.resolve()

    # Strict subpath check (resolved form must stay inside resolved root).
    try:
        candidate.relative_to(repo_resolved)
    except ValueError as e:
        raise StripError(
            "STRIP-E001", (f"src '{src}' resolves to '{candidate}' which is OUTSIDE the plugin root '{repo_resolved}'")
        ) from e

    # Symlink check — walk the UNRESOLVED path's ancestors AND the leaf.
    # We catch:
    #   (a) the leaf itself being a symlink (e.g. tests/ -> real-tests/)
    #   (b) any intermediate dir being a symlink (would let an attacker
    #       redirect `dev/tests` via a symlinked `dev/` dir)
    # plugin_root itself is NOT checked because the user owns it.
    cursor = raw_candidate
    while True:
        if cursor.is_symlink():
            raise StripError(
                "STRIP-E002",
                (
                    f"src '{src}' traverses a symlink at '{cursor}'. "
                    f"Symlinks are rejected for safety (the symlink target is not "
                    f"part of the plugin's working tree)."
                ),
            )
        if cursor == plugin_root or cursor.parent == cursor:
            break
        cursor = cursor.parent

    if not candidate.exists():
        raise StripError("STRIP-E004", f"src '{src}' does not exist in plugin")
    if not candidate.is_dir():
        raise StripError("STRIP-E005", f"src '{src}' is not a directory")
    return candidate


# ── Working-tree safety (STRIP-W001..W007) ────────────────────────────────────


def check_working_tree_safe(
    plugin_root: Path,
    targets: list[ExtractTarget],
    *,
    test_mode: bool = False,
) -> None:
    """7-step refusal cascade per TRDD-793ac32a §2.4.

    Fail-closed (first failure raises). `test_mode=True` skips ONLY
    check (5) — must be set explicitly via env var by the test harness;
    NEVER documented for users.
    """
    # 1. Is this a git working tree at all?
    res = subprocess.run(
        ["git", "-C", str(plugin_root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if res.returncode != 0 or res.stdout.strip() != "true":
        raise StripError(
            "STRIP-W001",
            (
                f"plugin_root '{plugin_root}' is not a git working tree. "
                f"`cpv strip-dev-parts` requires a git repo (it commits the "
                f".gitmodules + content removal atomically)."
            ),
        )

    # 2. Working tree must be clean.
    res = subprocess.run(
        ["git", "-C", str(plugin_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    porcelain = (res.stdout or "").strip()
    if porcelain:
        raise StripError(
            "STRIP-W002",
            (
                f"plugin_root '{plugin_root}' has uncommitted changes:\n{porcelain[:600]}\n"
                f"Commit or stash them before running cpv strip-dev-parts."
            ),
        )

    # 3. Refuse to operate inside a linked git worktree.
    cd_res = subprocess.run(
        ["git", "-C", str(plugin_root), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    cmn_res = subprocess.run(
        ["git", "-C", str(plugin_root), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if cd_res.returncode == 0 and cmn_res.returncode == 0:
        gd = cd_res.stdout.strip()
        gcd = cmn_res.stdout.strip()
        if gd != gcd:
            raise StripError(
                "STRIP-W003",
                (
                    f"plugin_root '{plugin_root}' is a linked git worktree "
                    f"(git-dir={gd!r}, git-common-dir={gcd!r}). cpv strip-dev-parts "
                    f"must run from the main checkout."
                ),
            )

    # 4. Stashes present → could lose work.
    res = subprocess.run(
        ["git", "-C", str(plugin_root), "stash", "list"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if res.returncode == 0 and res.stdout.strip():
        raise StripError(
            "STRIP-W004",
            (
                f"plugin_root has {len(res.stdout.splitlines())} stash entries. "
                f"Pop or drop them before running cpv strip-dev-parts — the "
                f"strip rewrites paths and could lose stashed work."
            ),
        )

    # 5. Untracked files inside extraction targets.
    if not test_mode:
        for t in targets:
            res = subprocess.run(
                ["git", "-C", str(plugin_root), "ls-files", "--others", "--exclude-standard", "--", t.src],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                raise StripError(
                    "STRIP-W005",
                    (
                        f"untracked files inside extraction target '{t.src}':\n"
                        f"{res.stdout.strip()[:400]}\n"
                        f"Add+commit OR delete them before running cpv strip-dev-parts."
                    ),
                )

    # 6. Unmerged paths (in-progress merge).
    res = subprocess.run(
        ["git", "-C", str(plugin_root), "ls-files", "-u"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if res.returncode == 0 and res.stdout.strip():
        raise StripError(
            "STRIP-W006",
            ("plugin_root has unmerged paths (in-progress merge). Resolve before running cpv strip-dev-parts."),
        )

    # 7. HEAD detached.
    res = subprocess.run(
        ["git", "-C", str(plugin_root), "symbolic-ref", "HEAD"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if res.returncode != 0:
        raise StripError(
            "STRIP-W007",
            (
                "plugin_root has detached HEAD. cpv strip-dev-parts must "
                "run from a branch (the strip commits + needs to push to "
                "the branch's remote tracking ref)."
            ),
        )


# ── State checkpointing ───────────────────────────────────────────────────────


def load_state(plugin_root: Path) -> dict[str, object]:
    """Load `.cpv-strip-state.json` or return {}."""
    p = plugin_root / STATE_FILENAME
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        # Corrupt state file — caller decides whether to reset.
        return {"__corrupt__": True}


def save_state(plugin_root: Path, state: dict[str, object]) -> None:
    """Atomically write `.cpv-strip-state.json` (tmp + rename)."""
    p = plugin_root / STATE_FILENAME
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def clear_state(plugin_root: Path) -> None:
    """Remove `.cpv-strip-state.json` (after DONE)."""
    p = plugin_root / STATE_FILENAME
    if p.is_file():
        p.unlink()


def state_progress(state: dict[str, object]) -> int:
    """Return the int index in _STATE_ORDER for the saved state.
    Returns 0 (INIT) if state is missing or unrecognised.

    Honours both legacy `state` and canonical `current_state` keys so
    older state files keep deserialising. New writes always use
    `current_state` (TRDD-793ac32a §2.5 canonical name).
    """
    raw = state.get("current_state") or state.get("state")
    if not isinstance(raw, str):
        return 0
    try:
        return _STATE_ORDER.index(StripState(raw))
    except ValueError:
        return 0


# ── Plan construction ─────────────────────────────────────────────────────────


def normalise_target(src: str, plugin_owner: str, plugin_name: str) -> ExtractTarget:
    """Build an `ExtractTarget` from a raw src + the parent plugin's
    owner/name. Used when no `submodule` is explicitly declared in
    plugin.json's cpv.strip.extract[].

    PSS pattern: the submodule mounts at the SAME path the original dir
    occupied. After strip, `tests/` keeps being `tests/` from the dev's
    perspective (just backed by a submodule). All references to the
    folder in CI, scripts, README continue to work unchanged. End-user
    cache installs get just the .gitmodules pointer (no recurse).
    """
    bare = src.rstrip("/").split("/")[-1]
    return ExtractTarget(
        src=src.rstrip("/") + "/",
        submodule=f"{plugin_owner}/{plugin_name}-{bare}",
        submodule_path=src.rstrip("/") + "/",
    )


def build_plan(
    plugin_root: Path,
    *,
    explicit_targets: list[str] | None = None,
) -> StripPlan:
    """Build a StripPlan from plugin.json + optional CLI overrides.

    Reads `cpv.strip.extract[]` from plugin.json. CLI `--extract <src>`
    flags add ad-hoc targets that are normalised via `normalise_target`.
    Validates each src via `validate_src_path` — raises StripError on
    any rejected path.
    """
    pj_path = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj_path.is_file():
        raise StripError(
            "STRIP-E007", (f"plugin.json not found at {pj_path}. cpv strip-dev-parts requires a Claude Code plugin.")
        )
    pj = json.loads(pj_path.read_text(encoding="utf-8"))
    plugin_name = str(pj.get("name", "")) or "plugin"
    repo_field = pj.get("repository") or pj.get("homepage") or ""
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", str(repo_field))
    plugin_owner = m.group(1) if m else "unknown-owner"

    cpv_block = pj.get("cpv", {}) if isinstance(pj.get("cpv"), dict) else {}
    strip = cpv_block.get("strip", {}) if isinstance(cpv_block.get("strip"), dict) else {}

    # Targets — explicit list from CLI overrides the plugin.json list.
    if explicit_targets:
        targets = [normalise_target(s, plugin_owner, plugin_name) for s in explicit_targets]
    else:
        raw_targets = strip.get("extract", [])
        if not isinstance(raw_targets, list):
            raw_targets = []
        targets = []
        for entry in raw_targets:
            if isinstance(entry, dict):
                src = str(entry.get("src", ""))
                submodule = str(entry.get("submodule", ""))
                if not src:
                    continue
                if not submodule:
                    targets.append(normalise_target(src, plugin_owner, plugin_name))
                else:
                    bare = src.rstrip("/").split("/")[-1]
                    targets.append(
                        ExtractTarget(
                            src=src.rstrip("/") + "/",
                            submodule=submodule,
                            submodule_path=str(entry.get("submodule_path") or f"dev/{bare}/"),
                            submodule_commit_sha=str(entry.get("submodule_commit_sha", "")),
                        )
                    )
            elif isinstance(entry, str):
                targets.append(normalise_target(entry, plugin_owner, plugin_name))

    if not targets:
        # Apply defaults if nothing configured AND no explicit list.
        targets = [normalise_target(s, plugin_owner, plugin_name) for s in DEFAULT_EXTRACT_TARGETS]

    # Validate every src path before returning.
    for t in targets:
        validate_src_path(t.src, plugin_root)

    keep_in_main = strip.get("keep_in_main", [])
    if not isinstance(keep_in_main, list):
        keep_in_main = []
    keep_dev_configs = bool(strip.get("keep_dev_configs", False))
    symlinks_for_devs = bool(strip.get("symlinks_for_devs", True))

    return StripPlan(
        plugin_root=plugin_root,
        targets=targets,
        keep_in_main=[str(p) for p in keep_in_main if isinstance(p, str)],
        keep_dev_configs=keep_dev_configs,
        symlinks_for_devs=symlinks_for_devs,
    )


# ── GH repo creation safety (STRIP-G001..G002) ───────────────────────────────


def gh_repo_exists_and_populated(submodule: str) -> tuple[bool, bool]:
    """Return (exists, populated). populated=True iff repo has commits
    beyond a default README.

    Used as the pre-create check per TRDD-793ac32a §2.5. If the repo
    exists AND is populated, abort STRIP-G001 (race / squat). If exists
    AND empty (or just default README), re-use it.
    """
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        raise StripError(
            "STRIP-G003", ("gh CLI not installed; required for repo creation. Install via `brew install gh`.")
        )
    res = subprocess.run(
        [gh_bin, "repo", "view", submodule, "--json", "name,defaultBranchRef,isEmpty"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if res.returncode != 0:
        # 404 or other non-zero → repo doesn't exist or no perm.
        return False, False
    try:
        info = json.loads(res.stdout)
    except json.JSONDecodeError:
        return True, True  # exists but unreadable — treat as populated for safety
    # `isEmpty` is True iff repo has no commits OR only the auto-init README.
    is_empty = bool(info.get("isEmpty", False))
    return True, not is_empty


# ── Public convenience: dry-run summary ───────────────────────────────────────


def summarise_plan(plan: StripPlan) -> str:
    """Return a multi-line human-readable summary of what `apply_plan` would do.

    Used by `--dry-run` mode to show the user EXACTLY what's about to
    happen before any GH repo is created or any commit is made.
    """
    lines = [
        f"Plan for {plan.plugin_root}:",
        f"  history_preserve: {plan.history_preserve}",
        f"  symlinks_for_devs: {plan.symlinks_for_devs}",
        f"  keep_dev_configs: {plan.keep_dev_configs}",
        f"  keep_in_main ({len(plan.keep_in_main)}):",
    ]
    for kp in plan.keep_in_main:
        lines.append(f"    - {kp}")
    lines.append(f"  extract targets ({len(plan.targets)}):")
    for t in plan.targets:
        lines.append(f"    - src={t.src!r:20s} → submodule={t.submodule!r} path={t.submodule_path!r}")
        # Heuristic recommendation — surface but never auto-skip targets.
        # The user's explicit cpv.strip.extract[] config wins; this is just
        # advice when the dry-run plan looks like a waste of effort.
        worth, reason = should_strip_target(t, plan.plugin_root)
        marker = "✓" if worth else "⚠"
        lines.append(f"      {marker} {reason}")
    lines.append("Steps that would execute (in order):")
    for i, t in enumerate(plan.targets, start=1):
        lines.append(f"  [{i}] gh repo create {t.submodule} --private  (if it doesn't already exist + is empty)")
        lines.append(f"      git clone --no-local <plugin> /tmp/cpv-strip-{uuid.uuid4().hex[:8]}/extract")
        lines.append(f"      git filter-repo --force --subdirectory-filter {t.src} --refs main")
        lines.append(f"      git push -u origin main  # to {t.url}")
        lines.append(f"      git submodule add {t.url} {t.submodule_path}")
    lines.append("  [N+1] git commit -m 'chore: extract dev parts to submodules (cpv strip-dev-parts)'")
    return "\n".join(lines)


# ── Needs-strip heuristic (TRDD-793ac32a §2.7) ───────────────────────────────


# Thresholds chosen empirically:
#  - 256 KB: typical CPV plugin's tests/ exceeds this when fixtures or
#    snapshot files appear; smaller tests/ gives < 1 MB savings on the
#    cache install — not worth the operational overhead of a separate repo.
#  - 20 files: fewer files than this means the dev folder is a stub or
#    pure docs — also not worth stripping.
NEEDS_STRIP_BYTES_MIN: int = 256 * 1024
NEEDS_STRIP_FILES_MIN: int = 20


def should_strip_target(
    target: ExtractTarget,
    plugin_root: Path,
) -> tuple[bool, str]:
    """Return (worth-stripping, reason).

    Heuristic: a target is worth stripping ONLY if BOTH thresholds are
    crossed (size AND file count). This avoids creating throwaway repos
    for plugins whose tests/ is just a stub or only contains a couple of
    smoke tests.

    Reason string is always populated for surfacing to the user via
    --dry-run output / --check report.
    """
    src_dir = plugin_root / target.src
    if not src_dir.is_dir():
        return False, f"source path {target.src!r} does not exist (cannot strip nothing)"

    total_bytes = 0
    total_files = 0
    for path in src_dir.rglob("*"):
        if path.is_file():
            try:
                total_bytes += path.stat().st_size
                total_files += 1
            except OSError:
                continue

    size_kb = total_bytes / 1024
    if total_bytes < NEEDS_STRIP_BYTES_MIN and total_files < NEEDS_STRIP_FILES_MIN:
        return False, (
            f"{target.src!r} is small ({size_kb:.1f} KB, {total_files} files) — "
            f"under both {NEEDS_STRIP_BYTES_MIN // 1024} KB and {NEEDS_STRIP_FILES_MIN} files. "
            f"Skip stripping (savings not worth the operational cost of a separate repo)."
        )
    return True, (
        f"{target.src!r} is heavy enough to strip ({size_kb:.1f} KB, "
        f"{total_files} files) — over the {NEEDS_STRIP_BYTES_MIN // 1024} KB / "
        f"{NEEDS_STRIP_FILES_MIN}-file threshold."
    )


# ── Live execution ───────────────────────────────────────────────────────────


def _ensure_repo_exists(target: ExtractTarget, plugin_name: str) -> None:
    """Verify or create the target's GitHub repo. Idempotent.

    On a fresh repo: `gh repo create --private` (retry-wrapped).
    On an existing-but-empty repo: re-use. On an existing-AND-populated
    repo: if `target.submodule_commit_sha` is pinned in plugin.json,
    verify the remote HEAD matches; otherwise abort STRIP-G001 to refuse
    silent overwrite of squatter content.
    """
    exists, populated = gh_repo_exists_and_populated(target.submodule)
    if exists and populated:
        # SHA-pin check: only safe to reuse if the pin matches HEAD.
        if target.submodule_commit_sha:
            head_sha = _gh_remote_head_sha(target.submodule)
            if head_sha and head_sha == target.submodule_commit_sha:
                print(
                    f"  [reuse] {target.submodule} exists, populated, and HEAD "
                    f"matches pinned SHA {head_sha[:8]}; reusing."
                )
                return
            raise StripError(
                "STRIP-G001",
                f"{target.submodule} exists and is populated, but HEAD ({head_sha or 'unknown'}) "
                f"does not match pinned cpv.strip.extract[].submodule_commit_sha "
                f"({target.submodule_commit_sha[:8]}). Refusing to overwrite. "
                f"Either bump the pin OR delete the remote repo and rerun.",
            )
        raise StripError(
            "STRIP-G001",
            f"{target.submodule} exists and is populated. Refusing to silently "
            f"overwrite — either pin the expected SHA via "
            f"cpv.strip.extract[].submodule_commit_sha (and run `cpv strip-dev-parts "
            f"--auto` again to verify) OR delete the remote repo and rerun.",
        )
    if exists and not populated:
        print(f"  [reuse] {target.submodule} exists and is empty; reusing.")
        return
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        raise StripError("STRIP-G003", "gh CLI not installed")
    print(f"  [create] gh repo create {target.submodule} --private")
    gh_with_retry(
        [
            gh_bin,
            "repo",
            "create",
            target.submodule,
            "--private",
            "--description",
            f"Dev artefacts extracted from {plugin_name} (TRDD-793ac32a)",
        ],
        check=True,
    )


def _gh_remote_head_sha(submodule: str) -> str | None:
    """Return the remote HEAD commit SHA via `gh api`, or None on failure.

    Used by _ensure_repo_exists to verify a SHA pin matches before re-using
    an existing populated repo.
    """
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        return None
    res = subprocess.run(
        [gh_bin, "api", f"repos/{submodule}/commits", "--jq", ".[0].sha"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if res.returncode != 0:
        return None
    sha = (res.stdout or "").strip()
    return sha if sha else None


def _filter_and_push(target: ExtractTarget, plugin_root: Path, tmp_root: Path) -> None:
    """Clone main repo to tmpdir, filter-repo to keep only target.src,
    push to target.url. Uses --no-local so filter-repo refuses to operate
    on the original repo. Push is retry-wrapped against transient hiccups.
    """
    clone = tmp_root / "extract"
    print(f"  [clone] git clone --no-local {plugin_root} {clone}")
    git_with_retry(
        ["git", "clone", "--no-local", str(plugin_root), str(clone)],
        check=True,
        capture_output=True,
    )
    print(f"  [filter] git filter-repo --subdirectory-filter {target.src}")
    # filter-repo is a single-shot operation, not network-dependent. No retry.
    # 30-min timeout caps a corrupt-object-DB hang; capture_output drains
    # stderr to avoid pipe-buffer deadlock on multi-GB repos that emit
    # >64 KB of progress output.
    subprocess.run(
        ["git", "-C", str(clone), "filter-repo", "--force", "--subdirectory-filter", target.src.rstrip("/")],
        check=True,
        capture_output=True,
        timeout=1800,
    )
    # filter-repo deletes 'origin' for safety. Re-add it pointing at the new repo.
    print(f"  [remote] origin → {target.url}")
    subprocess.run(
        ["git", "-C", str(clone), "remote", "add", "origin", target.url],
        check=True,
        capture_output=True,
        timeout=30,
    )
    # Detect default branch name in the cloned repo (could be main or master).
    branch_res = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    branch = branch_res.stdout.strip() or "main"
    print(f"  [push] git push -u origin {branch} (force: first push to fresh repo)")
    git_with_retry(
        ["git", "-C", str(clone), "push", "-u", "origin", branch, "--force"],
        check=True,
        capture_output=True,
    )


def _replace_with_submodule(target: ExtractTarget, plugin_root: Path) -> None:
    """In the MAIN repo: remove target.src and add it back as a submodule
    at target.submodule_path.

    If target.submodule_path equals target.src, the directory is replaced
    in place (PSS pattern: same path, just becomes a submodule mount).
    """
    src_dir = plugin_root / target.src
    sub_path = target.submodule_path.rstrip("/")
    print(f"  [git rm] {target.src}")
    subprocess.run(
        ["git", "-C", str(plugin_root), "rm", "-rf", target.src],
        check=True,
        capture_output=True,
        timeout=60,
    )
    # `git rm -rf` removes the directory entry but if untracked files
    # remained inside (gitignored), they may persist. The working-tree
    # safety check (STRIP-W005) already rejected untracked-in-target,
    # so this is just defensive cleanup.
    if src_dir.exists():
        shutil.rmtree(src_dir)
    print(f"  [submodule add] {target.url} {sub_path}")
    subprocess.run(
        ["git", "-C", str(plugin_root), "submodule", "add", "--force", target.url, sub_path],
        check=True,
        capture_output=True,
        timeout=300,
    )


def apply_plan(plan: StripPlan) -> None:
    """Execute the plan with idempotent state-machine recovery.

    Per TRDD-793ac32a §2.5, each transition is checkpointed to
    `.cpv-strip-state.json` BEFORE being attempted, so a crashed run can
    resume from the last successful state. Re-running this function with
    a saved state skips work that's already done:

        INIT → REPO_VERIFIED → CONTENT_PUSHED → SUBMODULE_ADDED → COMMITTED → DONE

    State is per-target. The state file tracks `current_target_index` and
    `current_state` so the loop knows where to pick up.
    """
    plugin_name = plan.plugin_root.name
    state = load_state(plan.plugin_root) or {}
    raw_idx = state.get("current_target_index", 0) if state else 0
    saved_idx = int(raw_idx) if isinstance(raw_idx, (int, str)) and str(raw_idx).isdigit() else 0
    raw_state = state.get("current_state") if state else None
    saved_state = str(raw_state) if isinstance(raw_state, str) else StripState.INIT.value

    tmp_dirs: list[Path] = []
    try:
        for idx, target in enumerate(plan.targets):
            # Resume: skip targets already fully committed in a prior run.
            if idx < saved_idx:
                print(f"\n=== {target.src} → {target.submodule} ===  [SKIP — already committed in prior run]")
                continue
            print(f"\n=== {target.src} → {target.submodule} ===")

            # Reset state markers when moving past saved target.
            cur_state = saved_state if idx == saved_idx else StripState.INIT.value
            saved_state = StripState.INIT.value  # only the resumed target uses the saved value
            saved_idx = idx  # keep idx aligned for next iter

            # Step A: ensure repo exists (idempotent).
            if state_progress({"current_state": cur_state}) < state_progress(
                {"current_state": StripState.REPO_VERIFIED.value}
            ):
                save_state(plan.plugin_root, {"current_target_index": idx, "current_state": StripState.INIT.value})
                _ensure_repo_exists(target, plugin_name)
                save_state(
                    plan.plugin_root, {"current_target_index": idx, "current_state": StripState.REPO_VERIFIED.value}
                )
                cur_state = StripState.REPO_VERIFIED.value

            # Step B: clone + filter-repo + push.
            if state_progress({"current_state": cur_state}) < state_progress(
                {"current_state": StripState.CONTENT_PUSHED.value}
            ):
                tmp = Path(tempfile.mkdtemp(prefix=f"cpv-strip-{uuid.uuid4().hex[:8]}-"))
                tmp_dirs.append(tmp)
                _filter_and_push(target, plan.plugin_root, tmp)
                save_state(
                    plan.plugin_root, {"current_target_index": idx, "current_state": StripState.CONTENT_PUSHED.value}
                )
                cur_state = StripState.CONTENT_PUSHED.value

            # Step C: git rm + submodule add.
            if state_progress({"current_state": cur_state}) < state_progress(
                {"current_state": StripState.SUBMODULE_ADDED.value}
            ):
                _replace_with_submodule(target, plan.plugin_root)
                save_state(
                    plan.plugin_root, {"current_target_index": idx, "current_state": StripState.SUBMODULE_ADDED.value}
                )

        # Step D: final commit (only if there are submodule changes staged).
        commit_msg = "chore: extract dev parts to submodules (cpv strip-dev-parts)"
        print(f"\n[commit] git commit -m '{commit_msg}'")
        diff_check = subprocess.run(
            ["git", "-C", str(plan.plugin_root), "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if not diff_check.stdout.strip():
            print("  (nothing staged — skipping commit)")
        else:
            subprocess.run(
                ["git", "-C", str(plan.plugin_root), "commit", "-m", commit_msg],
                check=True,
                timeout=60,
            )

        save_state(
            plan.plugin_root,
            {
                "current_target_index": len(plan.targets),
                "current_state": StripState.DONE.value,
            },
        )
        print("\n✓ Strip complete. Push the parent commit to make it visible.")
        # Clear state on full success so the next run starts fresh.
        clear_state(plan.plugin_root)
    finally:
        for tmp in tmp_dirs:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)


# ── CLI entry ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Modes: `--dry-run` (preview), `--check` (CI gate), `--auto`
    (live execution: creates GitHub repos, rewrites history).
    """
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print("\nUsage:")
        print("  cpv_strip_dev.py <plugin-path> --dry-run [--extract <src>...]")
        print("  cpv_strip_dev.py <plugin-path> --check")
        print("  cpv_strip_dev.py <plugin-path> --auto [--extract <src>...]")
        return 0

    plugin_root = Path(args[0]).resolve()
    if not plugin_root.is_dir():
        print(f"ERROR: plugin root not found: {plugin_root}", file=sys.stderr)
        return 1

    flags = args[1:]
    dry_run = "--dry-run" in flags
    check = "--check" in flags
    explicit_targets: list[str] = []
    i = 0
    while i < len(flags):
        if flags[i] == "--extract" and i + 1 < len(flags):
            explicit_targets.append(flags[i + 1])
            i += 2
            continue
        i += 1

    try:
        plan = build_plan(plugin_root, explicit_targets=explicit_targets or None)
    except StripError as e:
        print(f"FAILED to build plan: {e}", file=sys.stderr)
        return 1

    if check:
        # `--check` mode: exit 1 if dev parts still in MAIN repo, else 0.
        offending = [t for t in plan.targets if (plugin_root / t.src).is_dir()]
        if offending:
            print(
                f"FAIL: dev parts still in MAIN repo: {[t.src for t in offending]}",
                file=sys.stderr,
            )
            return 1
        print("OK: no dev parts in MAIN repo (all extracted to submodules).")
        return 0

    if dry_run:
        print(summarise_plan(plan))
        try:
            check_working_tree_safe(plugin_root, plan.targets)
        except StripError as e:
            print(f"\nNOTE: working tree is NOT in a state where the plan could execute: {e}", file=sys.stderr)
        return 0

    # Live execution path. Requires --auto (no interactive mode in this RC).
    if "--auto" not in flags:
        print(summarise_plan(plan))
        print(
            "\nNOTE: pass --auto to actually run this. Without --auto the "
            "command stays in dry-run-only mode (no GitHub repos created, "
            "no git history rewritten).",
            file=sys.stderr,
        )
        return 0

    try:
        check_working_tree_safe(plugin_root, plan.targets)
    except StripError as e:
        print(f"ABORT: working tree not safe for live execution: {e}", file=sys.stderr)
        return 1

    try:
        apply_plan(plan)
    except StripError as e:
        print(f"FAILED to apply plan: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"FAILED: subprocess error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
