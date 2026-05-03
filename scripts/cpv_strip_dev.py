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
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────────

# Whitelist regex for `cpv.strip.extract[].src` paths.
# Lowercase + alnum + hyphen + underscore + slash, no `..`, no leading `/`.
_SAFE_SRC_RE = re.compile(r"^[a-z][a-z0-9_-]*(/[a-z][a-z0-9_-]*)*/?$")

# Reserved paths that may NEVER be extracted (would brick the plugin).
_RESERVED_SRCS: frozenset[str] = frozenset({
    ".git", ".gitmodules", ".claude-plugin", "scripts",
    "agents", "commands", "skills", "hooks", "templates",
})

# Default extraction targets (per TRDD-793ac32a §4.2).
DEFAULT_EXTRACT_TARGETS: tuple[str, ...] = ("tests/", "design/", "git-hooks/")

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
    src: str                     # "tests/" — relative to plugin_root
    submodule: str               # "Emasoft/cpv-tests" — owner/repo
    submodule_path: str          # "dev/tests/" — where it lands in plugin_root
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
        raise StripError("STRIP-E003", (
            f"src '{src}' does not match safe-name pattern "
            f"(lowercase + alnum + hyphen + underscore + slash; no `..` or `/` prefix)"
        ))
    if src.rstrip("/") in _RESERVED_SRCS:
        raise StripError("STRIP-E006", (
            f"src '{src}' is a reserved path (would brick the runtime plugin); "
            f"reserved set = {sorted(_RESERVED_SRCS)}"
        ))

    repo_resolved = plugin_root.resolve()
    raw_candidate = plugin_root / src   # NOT resolved — keeps symlinks intact
    candidate = raw_candidate.resolve()

    # Strict subpath check (resolved form must stay inside resolved root).
    try:
        candidate.relative_to(repo_resolved)
    except ValueError as e:
        raise StripError("STRIP-E001", (
            f"src '{src}' resolves to '{candidate}' which is OUTSIDE the "
            f"plugin root '{repo_resolved}'"
        )) from e

    # Symlink check — walk the UNRESOLVED path's ancestors AND the leaf.
    # We catch:
    #   (a) the leaf itself being a symlink (e.g. tests/ -> real-tests/)
    #   (b) any intermediate dir being a symlink (would let an attacker
    #       redirect `dev/tests` via a symlinked `dev/` dir)
    # plugin_root itself is NOT checked because the user owns it.
    cursor = raw_candidate
    while True:
        if cursor.is_symlink():
            raise StripError("STRIP-E002", (
                f"src '{src}' traverses a symlink at '{cursor}'. "
                f"Symlinks are rejected for safety (the symlink target is not "
                f"part of the plugin's working tree)."
            ))
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
        capture_output=True, text=True, timeout=10, check=False,
    )
    if res.returncode != 0 or res.stdout.strip() != "true":
        raise StripError("STRIP-W001", (
            f"plugin_root '{plugin_root}' is not a git working tree. "
            f"`cpv strip-dev-parts` requires a git repo (it commits the "
            f".gitmodules + content removal atomically)."
        ))

    # 2. Working tree must be clean.
    res = subprocess.run(
        ["git", "-C", str(plugin_root), "status", "--porcelain"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    porcelain = (res.stdout or "").strip()
    if porcelain:
        raise StripError("STRIP-W002", (
            f"plugin_root '{plugin_root}' has uncommitted changes:\n{porcelain[:600]}\n"
            f"Commit or stash them before running cpv strip-dev-parts."
        ))

    # 3. Refuse to operate inside a linked git worktree.
    cd_res = subprocess.run(
        ["git", "-C", str(plugin_root), "rev-parse", "--git-dir"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    cmn_res = subprocess.run(
        ["git", "-C", str(plugin_root), "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if cd_res.returncode == 0 and cmn_res.returncode == 0:
        gd = cd_res.stdout.strip()
        gcd = cmn_res.stdout.strip()
        if gd != gcd:
            raise StripError("STRIP-W003", (
                f"plugin_root '{plugin_root}' is a linked git worktree "
                f"(git-dir={gd!r}, git-common-dir={gcd!r}). cpv strip-dev-parts "
                f"must run from the main checkout."
            ))

    # 4. Stashes present → could lose work.
    res = subprocess.run(
        ["git", "-C", str(plugin_root), "stash", "list"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if res.returncode == 0 and res.stdout.strip():
        raise StripError("STRIP-W004", (
            f"plugin_root has {len(res.stdout.splitlines())} stash entries. "
            f"Pop or drop them before running cpv strip-dev-parts — the "
            f"strip rewrites paths and could lose stashed work."
        ))

    # 5. Untracked files inside extraction targets.
    if not test_mode:
        for t in targets:
            res = subprocess.run(
                ["git", "-C", str(plugin_root), "ls-files",
                 "--others", "--exclude-standard", "--", t.src],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                raise StripError("STRIP-W005", (
                    f"untracked files inside extraction target '{t.src}':\n"
                    f"{res.stdout.strip()[:400]}\n"
                    f"Add+commit OR delete them before running cpv strip-dev-parts."
                ))

    # 6. Unmerged paths (in-progress merge).
    res = subprocess.run(
        ["git", "-C", str(plugin_root), "ls-files", "-u"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if res.returncode == 0 and res.stdout.strip():
        raise StripError("STRIP-W006", (
            "plugin_root has unmerged paths (in-progress merge). "
            "Resolve before running cpv strip-dev-parts."
        ))

    # 7. HEAD detached.
    res = subprocess.run(
        ["git", "-C", str(plugin_root), "symbolic-ref", "HEAD"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if res.returncode != 0:
        raise StripError("STRIP-W007", (
            "plugin_root has detached HEAD. cpv strip-dev-parts must "
            "run from a branch (the strip commits + needs to push to "
            "the branch's remote tracking ref)."
        ))


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
    Returns 0 (INIT) if state is missing or unrecognised."""
    raw = state.get("state")
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
    plugin.json's cpv.strip.extract[]."""
    # Bare folder name like "tests/" → submodule "Emasoft/<plugin>-tests"
    bare = src.rstrip("/").split("/")[-1]
    return ExtractTarget(
        src=src.rstrip("/") + "/",
        submodule=f"{plugin_owner}/{plugin_name}-{bare}",
        submodule_path=f"dev/{bare}/",
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
        raise StripError("STRIP-E007", (
            f"plugin.json not found at {pj_path}. "
            f"cpv strip-dev-parts requires a Claude Code plugin."
        ))
    pj = json.loads(pj_path.read_text(encoding="utf-8"))
    plugin_name = str(pj.get("name", "")) or "plugin"
    repo_field = pj.get("repository") or pj.get("homepage") or ""
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", str(repo_field))
    plugin_owner = m.group(1) if m else "unknown-owner"

    cpv_block = pj.get("cpv", {}) if isinstance(pj.get("cpv"), dict) else {}
    strip = cpv_block.get("strip", {}) if isinstance(cpv_block.get("strip"), dict) else {}

    # Targets — explicit list from CLI overrides the plugin.json list.
    if explicit_targets:
        targets = [
            normalise_target(s, plugin_owner, plugin_name)
            for s in explicit_targets
        ]
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
                    targets.append(ExtractTarget(
                        src=src.rstrip("/") + "/",
                        submodule=submodule,
                        submodule_path=str(entry.get("submodule_path") or f"dev/{bare}/"),
                        submodule_commit_sha=str(entry.get("submodule_commit_sha", "")),
                    ))
            elif isinstance(entry, str):
                targets.append(normalise_target(entry, plugin_owner, plugin_name))

    if not targets:
        # Apply defaults if nothing configured AND no explicit list.
        targets = [
            normalise_target(s, plugin_owner, plugin_name)
            for s in DEFAULT_EXTRACT_TARGETS
        ]

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
        raise StripError("STRIP-G003", (
            "gh CLI not installed; required for repo creation. "
            "Install via `brew install gh`."
        ))
    res = subprocess.run(
        [gh_bin, "repo", "view", submodule, "--json", "name,defaultBranchRef,isEmpty"],
        capture_output=True, text=True, timeout=20, check=False,
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
        lines.append(
            f"    - src={t.src!r:20s} → submodule={t.submodule!r} "
            f"path={t.submodule_path!r}"
        )
    lines.append("Steps that would execute (in order):")
    for i, t in enumerate(plan.targets, start=1):
        lines.append(
            f"  [{i}] gh repo create {t.submodule} --private  "
            f"(if it doesn't already exist + is empty)"
        )
        lines.append(
            f"      git clone --no-local <plugin> /tmp/cpv-strip-{uuid.uuid4().hex[:8]}/extract"
        )
        lines.append(
            f"      git filter-repo --force --subdirectory-filter {t.src} --refs main"
        )
        lines.append(f"      git push -u origin main  # to {t.url}")
        lines.append(f"      git submodule add {t.url} {t.submodule_path}")
    lines.append("  [N+1] git commit -m 'chore: extract dev parts to submodules (cpv strip-dev-parts)'")
    return "\n".join(lines)


# ── CLI entry ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Subset of operations supported here: `--dry-run`, `--check`,
    plan summary. The full `--auto` extraction flow is implemented in
    Sprint 2 rc3 once the engine + security helpers are battle-tested
    in tests. This is per the phased rollout documented in the sprint
    plan (rc1 = engine + tests; CLI extraction = rc3).
    """
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print("\nUsage:")
        print("  cpv_strip_dev.py <plugin-path> --dry-run "
              "[--extract <src>...]")
        print("  cpv_strip_dev.py <plugin-path> --check")
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
                f"FAIL: dev parts still in MAIN repo: "
                f"{[t.src for t in offending]}",
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
            print(f"\nNOTE: working tree is NOT in a state where the plan "
                  f"could execute: {e}", file=sys.stderr)
        return 0

    # Non-dry-run path: rc3 work. Stop here for rc1 to keep the engine
    # sandboxed until tests cover it end-to-end.
    print(
        "ERROR: live execution (--auto) is not enabled in cpv_strip_dev "
        "rc1. Use --dry-run to preview. The full extraction flow lands "
        "in Sprint 2 rc3 (commands/cpv-strip-dev-parts.md).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
