#!/usr/bin/env python3
"""TRDD-793ac32a — strip-dev-parts engine (clone-by-URL model).

Moves dev-only artefacts (tests/, design/, git-hooks/, …) from a
plugin's MAIN repo into a separate per-plugin GitHub repo, then REMOVES
them from the plugin tree entirely and records the extracted content's
source-repo URL + pinned commit SHA in the plugin manifest
(`.claude-plugin/plugin.json` → `cpv.strip.extract[]`). **No `.gitmodules`
entry is ever written.**

WHY NOT A GIT SUBMODULE (the load-bearing correction): the feature was
originally built on `git submodule add`, on the premise that Claude
Code's install does NOT recurse into submodules, so a submodule pointer
would keep the dev content off end-user machines. That premise is
EMPIRICALLY FALSE — Claude Code recursively fetches submodule CONTENT at
install time (verified on PSS 3.10.8: the installed `rust/` submodule
shipped its full source tree). A submodule pointer therefore ships the
content anyway. So the mechanism is retargeted onto a clone-by-URL model:

  * The extracted directory is DELETED from the plugin tree (nothing is
    left behind — not a submodule mount, not a `.gitmodules` gitlink).
  * The manifest records only `{path, url, sha}` — plain data, never a
    git-submodule pointer. There is nothing for Claude Code's installer
    to recurse into, so the dev content genuinely does not ship.
  * The dev content lives in a separate repo the developer re-clones on
    demand via `--restore`, which re-materialises each recorded path
    from its `{url, sha}` (a `git clone` pinned to the SHA, with the
    nested `.git` removed so the restored files are plain tree content —
    never an accidental gitlink).

This file is the **engine** (pure functions). The CLI surface lives in
`commands/cpv-strip-dev-parts.md`. The end-to-end command flow is:

    cpv strip-dev-parts <plugin>          # dry-run preview (no --auto)
    cpv strip-dev-parts <plugin> --auto   # standard rules, live execution
    cpv strip-dev-parts <plugin> --auto --visibility public  # #175 compile-source mirror
    cpv strip-dev-parts <plugin> --dry-run [--force-extract]
    cpv strip-dev-parts <plugin> --check  # CI gate: dev parts gone?
    cpv strip-dev-parts <plugin> --restore  # re-clone dev parts back

`--visibility {public,private}` (default private) sets the created source
repo's visibility; a PUBLIC push is fail-closed secret-scanned first (the
#175 compile-source repo is cloned tokenlessly by the plugin's release CI, so
its full extracted history goes public). `--force-extract` bypasses the
size/file threshold for the compile-source/canon migration.

Security model is documented in:
  * `cpv_validate_gitmodules.py` — `.gitmodules` URL allowlist (still
    guards any hand-authored `.gitmodules`; strip-dev no longer creates
    one, so a strip-dev'd plugin passes that validator trivially)
  * §2.3-§2.6 of TRDD-793ac32a (path traversal, working-tree safety,
    GH repo creation safety, history preservation)

Idempotent state machine (per TRDD-793ac32a §2.5):

    INIT → REPO_VERIFIED → CONTENT_PUSHED →
    REFERENCE_RECORDED → COMMITTED → DONE

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

# A pushed/recorded commit id — 40 lowercase hex chars. The clone-by-URL
# model PINS restore to this exact SHA, so a short or malformed value is
# rejected fail-fast rather than silently cloning an unpinned HEAD.
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

# Shape of a recorded `cpv.strip.extract[].url`. The recorder only ever
# writes `https://github.com/<owner>/<repo>.git`, so restore requires that
# exact shape: HTTPS only (no ssh/file/http), github.com host with NO
# embedded userinfo (`user@`) or port (`:`), and a `..`-free path (the
# `..` guard is applied separately because the char class permits `.`).
# Restricting `git clone` to this shape closes option-injection (a URL that
# begins `-`) and host-substitution vectors on a possibly-tampered manifest.
_HTTPS_GITHUB_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*(?:\.git)?$")

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
    """Idempotent state-machine states (per TRDD-793ac32a §2.5).

    REFERENCE_RECORDED replaces the former SUBMODULE_ADDED: the step no
    longer runs `git submodule add`; it removes the extracted directory
    and records the `{path, url, sha}` reference in the manifest.
    """

    INIT = "INIT"
    REPO_VERIFIED = "REPO_VERIFIED"
    CONTENT_PUSHED = "CONTENT_PUSHED"
    REFERENCE_RECORDED = "REFERENCE_RECORDED"
    COMMITTED = "COMMITTED"
    DONE = "DONE"


# Ordered transitions; index in this tuple = "progress score".
# (Repo verification and creation happen together in the REPO_VERIFIED step —
# there is no separate REPO_CREATED state; it was declared but never set, so it
# was removed rather than left as a dead, misleading enum member.)
_STATE_ORDER: tuple[StripState, ...] = (
    StripState.INIT,
    StripState.REPO_VERIFIED,
    StripState.CONTENT_PUSHED,
    StripState.REFERENCE_RECORDED,
    StripState.COMMITTED,
    StripState.DONE,
)


# ── Exceptions ─────────────────────────────────────────────────────────────────


class StripError(RuntimeError):
    """Base class for all strip-dev-parts engine errors.

    Carries a stable error code (STRIP-Wxxx for working-tree safety,
    STRIP-Exxx for path errors, STRIP-Gxxx for gh-repo errors,
    STRIP-Hxxx for history errors, STRIP-Rxxx for record-schema errors,
    STRIP-Sxxx for the fail-closed secret-scan gate).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class ExtractTarget:
    """A single strip DECLARATION, normalised (the pre-strip input shape).

    Read from a `cpv.strip.extract[]` entry carrying `src` (see
    ``build_plan``). `submodule` names the separate source repo the content
    is pushed to; `submodule_path` is where the content lived and where
    ``--restore`` re-materialises it (the recorded `path`).
    """

    src: str  # "tests/" — relative to plugin_root
    submodule: str  # "Emasoft/cpv-tests" — owner/repo of the source repo
    submodule_path: str  # "dev/tests/" — recorded restore path in plugin_root
    submodule_commit_sha: str = ""  # optional pre-declared pin
    pushed_sha: str = ""  # 40-hex SHA captured after the extract push

    @property
    def url(self) -> str:
        return f"https://github.com/{self.submodule}.git"


@dataclass(frozen=True)
class ExtractRecord:
    """A single `cpv.strip.extract[]` RECORD (the post-strip output shape).

    Written by ``apply_plan`` and read by ``--restore``. Plain data — NOT a
    git-submodule pointer. `path` is where the content is re-cloned; `url`
    is the source repo (`https://github.com/<owner>/<repo>.git`); `sha` is
    the exact commit ``--restore`` checks out.
    """

    path: str  # "tests" — relative to plugin_root (no trailing slash)
    url: str  # "https://github.com/<owner>/<repo>.git"
    sha: str  # 40-hex commit id


def validate_extract_record(entry: object, plugin_root: Path) -> ExtractRecord:
    """Validate one raw `cpv.strip.extract[]` record dict. Fail-fast.

    The record schema is `{"path": <rel>, "url": <https github url>,
    "sha": <40-hex>}`. Every field is shape-checked because ``--restore``
    runs `git clone` against the recorded URL and `git checkout` against the
    recorded SHA — a malformed entry on a possibly-tampered manifest must be
    REJECTED (raise ``StripError``) rather than fed to git.
    """
    if not isinstance(entry, dict):
        raise StripError("STRIP-R001", f"cpv.strip.extract record must be an object, got {type(entry).__name__}")
    path = entry.get("path")
    url = entry.get("url")
    sha = entry.get("sha")
    if not isinstance(path, str) or not path.strip():
        raise StripError("STRIP-R002", f"cpv.strip.extract record has a missing/invalid 'path': {entry!r}")
    if not isinstance(url, str) or not url.strip():
        raise StripError("STRIP-R003", f"cpv.strip.extract record has a missing/invalid 'url': {entry!r}")
    if not isinstance(sha, str) or not _SHA40_RE.match(sha):
        raise StripError(
            "STRIP-R004",
            f"cpv.strip.extract record 'sha' must be a 40-char lowercase hex commit id: {entry!r}",
        )
    # Path safety: same name / reserved-set / subpath / ancestor-symlink guards
    # as an extract source, but the destination need not exist yet.
    validate_src_path(path, plugin_root, must_exist=False)
    # URL safety: HTTPS github shape, no traversal, no embedded userinfo/port.
    if ".." in url:
        raise StripError("STRIP-R005", f"cpv.strip.extract record 'url' contains path-traversal `..`: {url}")
    if not _HTTPS_GITHUB_URL_RE.match(url):
        raise StripError(
            "STRIP-R005",
            (
                f"cpv.strip.extract record 'url' must be an https://github.com/<owner>/<repo> URL "
                f"(no ssh, no embedded credentials, no port): {url}"
            ),
        )
    return ExtractRecord(path=path.rstrip("/"), url=url, sha=sha)


def parse_extract_records(plugin_root: Path) -> list[ExtractRecord]:
    """Return the validated `{path, url, sha}` records from plugin.json.

    A record is any `cpv.strip.extract[]` entry carrying a `url` key
    (pre-strip DECLARATIONS carry `src` and no `url`, so they are ignored
    here). Every record-shaped entry is validated — a malformed one raises
    ``StripError`` (fail-fast). Used by ``--restore``.
    """
    pj_path = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj_path.is_file():
        raise StripError("STRIP-E007", f"plugin.json not found at {pj_path}. cpv strip-dev-parts requires a plugin.")
    pj = json.loads(pj_path.read_text(encoding="utf-8"))
    cpv_block = pj.get("cpv", {}) if isinstance(pj.get("cpv"), dict) else {}
    strip = cpv_block.get("strip", {}) if isinstance(cpv_block.get("strip"), dict) else {}
    raw = strip.get("extract", [])
    if not isinstance(raw, list):
        return []
    records: list[ExtractRecord] = []
    for entry in raw:
        if isinstance(entry, dict) and "url" in entry:
            records.append(validate_extract_record(entry, plugin_root))
    return records


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


def validate_src_path(src: str, plugin_root: Path, *, must_exist: bool = True) -> Path:
    """Validate an extract-source path. Raises `StripError` on rejection.

    Returns the resolved Path on success. Per TRDD-793ac32a §2.3, ALL
    of these checks must succeed before any write/move happens.

    ``must_exist`` (default True) applies the existence + is-directory checks,
    correct for an EXTRACT SOURCE (which must already be in the working tree).
    Pass ``must_exist=False`` for a DESTINATION path (e.g. a submodule mount
    point that does not exist yet): the name / reserved-set / subpath /
    ancestor-symlink guards still run, but the path is not required to exist.
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

    if must_exist:
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
                f"content removal + the cpv.strip.extract manifest record atomically)."
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

    The extracted dir is pushed to `<owner>/<plugin>-<dir>` and the
    recorded restore path (`submodule_path`) is the SAME path the original
    dir occupied. After strip, the content is REMOVED from the plugin tree
    (nothing ships to end-user installs — no submodule mount, no
    `.gitmodules` pointer for Claude Code's installer to recurse into); a
    developer re-materialises `tests/` at `tests/` on demand via
    `--restore` (a clone-by-URL pinned to the recorded SHA).
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
    saw_records = False
    if explicit_targets:
        targets = [normalise_target(s, plugin_owner, plugin_name) for s in explicit_targets]
    else:
        raw_targets = strip.get("extract", [])
        if not isinstance(raw_targets, list):
            raw_targets = []
        targets = []
        for entry in raw_targets:
            if isinstance(entry, dict):
                # A post-strip RECORD ({path, url, sha}, no `src`) means this
                # path was already extracted — it is NOT a declaration to
                # re-strip. Note it so we do not fall back to DEFAULT targets
                # on an already-stripped plugin, and skip it.
                if "url" in entry and not entry.get("src"):
                    saw_records = True
                    continue
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

    if not targets and not saw_records:
        # Apply defaults ONLY on a fresh plugin: nothing configured, no
        # explicit list, and no prior extract records. When the manifest
        # already holds records (already stripped) we return an empty plan —
        # a clean no-op rather than trying to re-strip a now-absent dir.
        targets = [normalise_target(s, plugin_owner, plugin_name) for s in DEFAULT_EXTRACT_TARGETS]

    # Validate every src path before returning.
    for t in targets:
        validate_src_path(t.src, plugin_root)
        # submodule_path is where the extracted content gets re-mounted as a
        # git submodule — validate it the SAME way as src (name / reserved-set /
        # subpath / ancestor-symlink), but with must_exist=False because the
        # mount point does not exist yet. git itself refuses an OUT-of-tree
        # submodule path, but the IN-tree reserved-dir case (e.g. a crafted
        # plugin.json mounting a submodule over scripts/ or .claude-plugin/)
        # was previously unguarded because only t.src ran through this.
        validate_src_path(t.submodule_path, plugin_root, must_exist=False)

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


def summarise_plan(plan: StripPlan, *, visibility: str = "private", force_extract: bool = False) -> str:
    """Return a multi-line human-readable summary of what `apply_plan` would do.

    Used by `--dry-run` mode to show the user EXACTLY what's about to
    happen before any GH repo is created or any commit is made. ``visibility``
    and ``force_extract`` mirror the live-execution flags so the preview shows
    the actual `gh repo create` flag and the actual strip recommendation.
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
        worth, reason = should_strip_target(t, plan.plugin_root, force_extract=force_extract)
        marker = "✓" if worth else "⚠"
        lines.append(f"      {marker} {reason}")
    vis_flag = "--public" if visibility == "public" else "--private"
    lines.append("Steps that would execute (in order):")
    for i, t in enumerate(plan.targets, start=1):
        lines.append(f"  [{i}] gh repo create {t.submodule} {vis_flag}  (if it doesn't already exist + is empty)")
        lines.append(f"      git clone --no-local <plugin> /tmp/cpv-strip-{uuid.uuid4().hex[:8]}/extract")
        # Preview MUST mirror _filter_and_push exactly: the real command
        # rstrips the trailing slash and filters ALL refs (no --refs main),
        # then pushes the cloned repo's detected default branch with --force.
        lines.append(f"      git filter-repo --force --subdirectory-filter {t.src.rstrip('/')}")
        lines.append(f"      git push -u origin <default-branch> --force  # to {t.url}  → capture pushed SHA")
        # Clone-by-URL model: remove the dir and record a {path, url, sha}
        # reference in the manifest. NO `git submodule add`, NO .gitmodules.
        lines.append(f"      git rm -r {t.src}  →  record cpv.strip.extract[] {{path={t.submodule_path.rstrip('/')!r},")
        lines.append(f"          url={t.url!r}, sha=<pushed>}} in plugin.json  (NO .gitmodules written)")
    lines.append("  [N+1] git commit -m 'chore: extract dev parts to source repos (cpv strip-dev-parts)'")
    lines.append("Restore later with: cpv strip-dev-parts <plugin> --restore  (re-clones each recorded url@sha)")
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
    *,
    force_extract: bool = False,
) -> tuple[bool, str]:
    """Return (worth-stripping, reason).

    Heuristic: a target is worth stripping when it is heavy by EITHER
    measure — size OR file count. Equivalently, it is skipped ONLY when it
    is small by BOTH measures (under the byte threshold AND under the file
    threshold). This keeps a heavy-but-few-files tree (e.g. a 5 MB tests/
    of three big fixtures) strippable for the install-cache savings, while
    still skipping a tests/ that is a stub or a couple of smoke tests.

    ``force_extract`` bypasses the size/file threshold: the #175 compile-source
    / canon migration must extract a source dir regardless of size (a small
    Rust crate is below the threshold but MUST be extracted). The existence
    check still runs first — you cannot force-strip a directory that isn't
    there (fail-safe: never "strip nothing").

    Reason string is always populated for surfacing to the user via
    --dry-run output / --check report.
    """
    src_dir = plugin_root / target.src
    if not src_dir.is_dir():
        return False, f"source path {target.src!r} does not exist (cannot strip nothing)"

    if force_extract:
        return True, "forced extraction — size threshold bypassed for compile-source/canon migration"

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


def _ensure_repo_exists(target: ExtractTarget, plugin_name: str, visibility: str = "private") -> None:
    """Verify or create the target's GitHub repo. Idempotent.

    On a fresh repo: `gh repo create --public|--private` (retry-wrapped) per
    ``visibility`` (default ``private``). ``public`` is the #175 compile-source
    case — the source repo the plugin's release CI clones by URL tokenlessly —
    so it gets a "Source for …" description; ``private`` (the dev-artefacts
    case) keeps the TRDD-793ac32a wording.
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
    # Visibility drives BOTH the gh flag and the repo description. `public` is
    # the #175 compile-source mirror (release CI clones it by URL, tokenless);
    # `private` is the original dev-artefacts extraction.
    if visibility == "public":
        vis_flag = "--public"
        description = f"Source for {plugin_name}, built into the shipped binaries (extracted by CPV cpv strip-dev-parts)"
    else:
        vis_flag = "--private"
        description = f"Dev artefacts extracted from {plugin_name} (TRDD-793ac32a)"
    print(f"  [create] gh repo create {target.submodule} {vis_flag}")
    gh_with_retry(
        [
            gh_bin,
            "repo",
            "create",
            target.submodule,
            vis_flag,
            "--description",
            description,
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


def _parse_truffle_findings(stdout: str) -> list[str]:
    """Parse trufflehog `--json` GIT-mode output → short finding descriptors.

    trufflehog emits one JSON object per line. In git mode the location lives
    under ``SourceMetadata.Data.Git`` (not ``.Filesystem`` as in filesystem
    mode). Mirrors the parse shape of ``validate_security.check_trufflehog``.
    Returns one ``"<detector> in <file>@<commit8>"`` string per finding.
    """
    out: list[str] = []
    for raw in (stdout or "").splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            finding = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(finding, dict):
            continue
        detector = finding.get("DetectorName") or finding.get("detector") or "?"
        meta = finding.get("SourceMetadata", {}) or {}
        data = meta.get("Data", {}) if isinstance(meta, dict) else {}
        git = data.get("Git", {}) if isinstance(data, dict) else {}
        fname = git.get("file", "?") if isinstance(git, dict) else "?"
        commit = git.get("commit", "?") if isinstance(git, dict) else "?"
        out.append(f"{detector} in {fname}@{str(commit)[:8]}")
    return out


def _scan_clone_for_secrets(clone: Path, *, visibility: str) -> None:
    """Fail-closed secret scan of the filtered clone's FULL git HISTORY.

    WHY THIS GATE EXISTS: ``git filter-repo`` PRESERVES history, so the
    extracted subdirectory's every past commit rides along to the new repo. If
    that repo is PUBLIC (the #175 compile-source case — the plugin's release CI
    clones it by URL, tokenlessly), a secret buried in an old/deleted commit
    leaks to the world the instant we push. So a PUBLIC target is FAIL-CLOSED
    (BLOCK the push on any finding, or if the scan cannot be trusted), exactly
    mirroring the issue-#169 pre-push secret gate. A PRIVATE target is not the
    leak surface #175 introduces, so it WARNs and proceeds.

    Scans the /tmp clone's `.git` history via ``trufflehog git file://<clone>``
    (30-min timeout). Never runs a shell (args list) and never trusts a partial
    scan for a public push.
    """
    public = visibility == "public"
    truffle = shutil.which("trufflehog")
    if truffle is None:
        if public:
            raise StripError(
                "STRIP-S002",
                "trufflehog unavailable — refusing to push a PUBLIC repo unscanned (fail-closed)",
            )
        print("  [warn] trufflehog not installed — secret scan skipped for a PRIVATE repo")
        return
    print(f"  [secret-scan] trufflehog git file://{clone}")
    try:
        res = subprocess.run(
            [truffle, "git", f"file://{clone}", "--json", "--no-update", "--fail"],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        if public:
            raise StripError(
                "STRIP-S002",
                "trufflehog scan timed out after 1800s — refusing to push a PUBLIC repo unscanned (fail-closed)",
            ) from e
        print("  [warn] trufflehog scan timed out — proceeding for a PRIVATE repo")
        return
    findings = _parse_truffle_findings(res.stdout)
    if findings:
        if public:
            raise StripError(
                "STRIP-S001",
                (
                    f"trufflehog found {len(findings)} secret(s) in the extracted history "
                    f"(e.g. {findings[0]}); refusing to push — the target repo would be PUBLIC "
                    f"(#175 compile-source). Purge the secret(s) from history and retry."
                ),
            )
        print(f"  [warn] trufflehog found {len(findings)} secret(s) in a PRIVATE repo's history; proceeding")
        return
    # No parseable findings, but `--fail` exits 183 ONLY on findings; any OTHER
    # non-zero exit means the scan itself errored. For a PUBLIC push that is a
    # scan we cannot trust, so block (fail-closed). Private proceeds.
    if public and res.returncode not in (0, 183):
        raise StripError(
            "STRIP-S002",
            (
                f"trufflehog exited {res.returncode} without parseable findings — refusing to push "
                f"a PUBLIC repo on an untrusted scan (fail-closed). stderr: {(res.stderr or '')[:200]}"
            ),
        )


def _filter_and_push(target: ExtractTarget, plugin_root: Path, tmp_root: Path, *, visibility: str = "private") -> str:
    """Clone main repo to tmpdir, filter-repo to keep only target.src,
    push to target.url. Uses --no-local so filter-repo refuses to operate
    on the original repo. Push is retry-wrapped against transient hiccups.

    Before the push, ``visibility`` gates a fail-closed secret scan of the
    filtered clone's full history (see ``_scan_clone_for_secrets``): a PUBLIC
    push is BLOCKED on any finding; a PRIVATE push WARNs and proceeds.

    Returns the 40-hex commit SHA that was pushed (the filtered clone's
    HEAD) — the clone-by-URL model records this SHA so ``--restore`` pins
    the re-clone to the exact content that was extracted.
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
    # FAIL-CLOSED secret gate — runs on the filtered clone's full history BEFORE
    # the push. filter-repo preserves history, so an old secret would go PUBLIC
    # the moment we push a public mirror; block it here (mirrors issue #169).
    _scan_clone_for_secrets(clone, visibility=visibility)
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
    # Capture the pushed commit SHA — this is what the manifest records and
    # what --restore checks out. filter-repo rewrote history, so the clone's
    # HEAD is the exact commit now living at the remote branch tip.
    sha_res = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    pushed_sha = sha_res.stdout.strip()
    print(f"  [pushed] {branch} @ {pushed_sha[:12]}…")
    return pushed_sha


def _record_extract_reference(plugin_root: Path, target: ExtractTarget, sha: str) -> None:
    """Upsert a `{path, url, sha}` record into `cpv.strip.extract[]`.

    Read-modify-write of `.claude-plugin/plugin.json`, atomic (tmp + rename).
    Drops this target's pre-strip DECLARATION (the entry whose `src` matches)
    AND any stale record for the same `path`, then appends the fresh record —
    so a crash-resume re-run is idempotent. Writes NO `.gitmodules`.
    """
    pj_path = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj_path.is_file():
        raise StripError("STRIP-E007", f"plugin.json not found at {pj_path}; cannot record extract reference.")
    pj = json.loads(pj_path.read_text(encoding="utf-8"))
    if not isinstance(pj, dict):
        raise StripError("STRIP-R006", f"plugin.json at {pj_path} is not a JSON object; cannot record.")
    rec_path = target.submodule_path.rstrip("/")
    record = {"path": rec_path, "url": target.url, "sha": sha}

    cpv_block = pj.get("cpv")
    if not isinstance(cpv_block, dict):
        cpv_block = {}
        pj["cpv"] = cpv_block
    strip = cpv_block.get("strip")
    if not isinstance(strip, dict):
        strip = {}
        cpv_block["strip"] = strip
    extract = strip.get("extract")
    if not isinstance(extract, list):
        extract = []

    src_key = target.src.rstrip("/")
    kept: list[object] = []
    for e in extract:
        if isinstance(e, dict):
            # Drop a prior record for the same restore path.
            if e.get("path") == rec_path:
                continue
            # Drop this target's own declaration (matched by `src`).
            e_src = e.get("src")
            if isinstance(e_src, str) and e_src.rstrip("/") == src_key:
                continue
        kept.append(e)
    kept.append(record)
    strip["extract"] = kept

    tmp = pj_path.with_suffix(pj_path.suffix + ".tmp")
    tmp.write_text(json.dumps(pj, indent=2) + "\n", encoding="utf-8")
    tmp.replace(pj_path)


def _replace_with_url_reference(target: ExtractTarget, plugin_root: Path, sha: str) -> None:
    """In the MAIN repo: remove target.src and record a clone-by-URL
    reference in the manifest — NO git submodule, NO `.gitmodules`.

    The extracted directory is deleted from the plugin tree entirely (so it
    does not ship on install), and `{path, url, sha}` is recorded in
    `cpv.strip.extract[]` so ``--restore`` can re-clone it on demand.
    """
    src_dir = plugin_root / target.src
    print(f"  [git rm] {target.src}")
    subprocess.run(
        # --ignore-unmatch makes the rm idempotent: this step is only
        # checkpointed (CONTENT_PUSHED -> REFERENCE_RECORDED) AFTER the whole
        # function returns, so a crash BETWEEN this rm and the manifest write
        # below leaves the run in CONTENT_PUSHED. A resume re-enters here and
        # re-runs the rm on an already-removed path — without --ignore-unmatch
        # `git rm` would exit non-zero ("pathspec did not match") and check=True
        # would abort the plan mid-surgery. Content is already safe in the
        # source repo (pushed in the prior step), so a no-op rm is correct.
        ["git", "-C", str(plugin_root), "rm", "-rf", "--ignore-unmatch", target.src],
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
    print(f"  [record] cpv.strip.extract[] path={target.submodule_path.rstrip('/')} url={target.url} sha={sha[:12]}…")
    _record_extract_reference(plugin_root, target, sha)
    # Stage the manifest edit so it lands in the same commit as the removal.
    subprocess.run(
        ["git", "-C", str(plugin_root), "add", "--", ".claude-plugin/plugin.json"],
        check=True,
        capture_output=True,
        timeout=30,
    )


def apply_plan(plan: StripPlan, *, visibility: str = "private") -> None:
    """Execute the plan with idempotent state-machine recovery.

    Per TRDD-793ac32a §2.5, each transition is checkpointed to
    `.cpv-strip-state.json` BEFORE being attempted, so a crashed run can
    resume from the last successful state. Re-running this function with
    a saved state skips work that's already done:

        INIT → REPO_VERIFIED → CONTENT_PUSHED → REFERENCE_RECORDED → COMMITTED → DONE

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
                _ensure_repo_exists(target, plugin_name, visibility=visibility)
                save_state(
                    plan.plugin_root, {"current_target_index": idx, "current_state": StripState.REPO_VERIFIED.value}
                )
                cur_state = StripState.REPO_VERIFIED.value

            # Step B: clone + filter-repo + push. Capture the pushed SHA
            # (the exact commit --restore will pin to).
            if state_progress({"current_state": cur_state}) < state_progress(
                {"current_state": StripState.CONTENT_PUSHED.value}
            ):
                tmp = Path(tempfile.mkdtemp(prefix=f"cpv-strip-{uuid.uuid4().hex[:8]}-"))
                tmp_dirs.append(tmp)
                target.pushed_sha = _filter_and_push(target, plan.plugin_root, tmp, visibility=visibility)
                save_state(
                    plan.plugin_root, {"current_target_index": idx, "current_state": StripState.CONTENT_PUSHED.value}
                )
                cur_state = StripState.CONTENT_PUSHED.value

            # Step C: git rm + record the clone-by-URL reference (NO submodule).
            if state_progress({"current_state": cur_state}) < state_progress(
                {"current_state": StripState.REFERENCE_RECORDED.value}
            ):
                # Resolve the SHA: from this run's push, else (a resume where
                # Step B was already done) re-query the remote HEAD. Recording
                # without a valid pin would produce an un-restorable reference.
                sha = target.pushed_sha or (_gh_remote_head_sha(target.submodule) or "")
                if not _SHA40_RE.match(sha):
                    raise StripError(
                        "STRIP-R010",
                        (
                            f"could not determine the pushed commit SHA for {target.submodule} "
                            f"(got {sha!r}); refusing to record an un-restorable cpv.strip.extract entry."
                        ),
                    )
                _replace_with_url_reference(target, plan.plugin_root, sha)
                save_state(
                    plan.plugin_root,
                    {"current_target_index": idx, "current_state": StripState.REFERENCE_RECORDED.value},
                )

        # Step D: final commit (only if there are staged removals + manifest edits).
        commit_msg = "chore: extract dev parts to source repos (cpv strip-dev-parts)"
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
        print("  Dev content lives in the source repo(s); re-clone with `--restore`.")
        # Clear state on full success so the next run starts fresh.
        clear_state(plan.plugin_root)
    finally:
        for tmp in tmp_dirs:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)


# ── Restore (clone-by-URL inverse of a strip) ─────────────────────────────────


def _restore_record(record: ExtractRecord, plugin_root: Path) -> None:
    """Re-materialise one recorded `{path, url, sha}` into the plugin tree.

    Clones the source repo, checks out the pinned SHA, then REMOVES the
    nested `.git` so the restored files are plain tree content — never an
    accidental gitlink (the exact trap the clone-by-URL model exists to
    avoid). Refuses to clobber a non-empty destination (fail-fast).
    """
    dest = plugin_root / record.path
    if dest.exists() and any(dest.iterdir()):
        raise StripError(
            "STRIP-R020",
            (
                f"restore destination '{record.path}' already exists and is not empty. "
                f"Remove it first, or it may already be restored."
            ),
        )
    print(f"  [clone] {record.url} → {record.path} @ {record.sha[:12]}…")
    # `--` guards against option-injection; the URL shape was validated in
    # validate_extract_record (HTTPS github, no userinfo/port/traversal).
    git_with_retry(
        ["git", "clone", "--", record.url, str(dest)],
        check=True,
        capture_output=True,
    )
    # Pin to the exact extracted commit (detached — restore is a materialise,
    # not a branch checkout).
    subprocess.run(
        ["git", "-C", str(dest), "checkout", "--detach", record.sha],
        check=True,
        capture_output=True,
        timeout=120,
    )
    git_dir = dest / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir, ignore_errors=True)


def run_restore(plugin_root: Path) -> int:
    """`--restore`: re-clone every recorded dev part from its url@sha.

    Reads the validated `{path, url, sha}` records from plugin.json (a
    malformed record aborts fail-fast) and re-materialises each. Returns a
    process exit code.
    """
    try:
        records = parse_extract_records(plugin_root)
    except StripError as e:
        print(f"FAILED to read cpv.strip.extract records: {e}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"FAILED: plugin.json is not valid JSON: {e}", file=sys.stderr)
        return 1
    if not records:
        print("No cpv.strip.extract records to restore (plugin is not stripped, or nothing recorded).")
        return 0
    for record in records:
        try:
            _restore_record(record, plugin_root)
        except StripError as e:
            print(f"FAILED to restore '{record.path}': {e}", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as e:
            print(f"FAILED to restore '{record.path}': subprocess error: {e}", file=sys.stderr)
            return 1
    print(f"\n✓ Restore complete: {len(records)} dev part(s) re-cloned from their recorded source repo(s).")
    return 0


# ── CLI entry ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Modes: `--dry-run` (preview), `--check` (CI gate), `--auto`
    (live execution: creates GitHub repos, rewrites history, records the
    clone-by-URL references), `--restore` (re-clone recorded dev parts).
    """
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print("\nUsage:")
        print("  cpv_strip_dev.py <plugin-path> --dry-run [--extract <src>...] [--visibility {public,private}] [--force-extract]")
        print("  cpv_strip_dev.py <plugin-path> --check")
        print("  cpv_strip_dev.py <plugin-path> --auto [--extract <src>...] [--visibility {public,private}] [--force-extract]")
        print("  cpv_strip_dev.py <plugin-path> --restore")
        print("\nOptions:")
        print("  --visibility {public,private}  visibility of the created source repo (default: private).")
        print("                                 public is the #175 compile-source mirror; it fail-closed")
        print("                                 secret-scans the extracted history before pushing.")
        print("  --force-extract                bypass the size/file threshold (extract regardless of size).")
        return 0

    plugin_root = Path(args[0]).resolve()
    if not plugin_root.is_dir():
        print(f"ERROR: plugin root not found: {plugin_root}", file=sys.stderr)
        return 1

    flags = args[1:]
    dry_run = "--dry-run" in flags
    check = "--check" in flags

    # `--restore` re-clones the recorded dev parts; it reads the manifest
    # records directly and needs no strip plan.
    if "--restore" in flags:
        return run_restore(plugin_root)

    # Manual flag parsing (this module deliberately does NOT use argparse — see
    # the 4 test_main_* stdout assertions that pin the `--help`/dry-run output).
    # --visibility takes a value validated fail-fast against the 2-member set;
    # --force-extract is a boolean. Option-injection-safe: no user string ever
    # reaches a shell (every subprocess call is an args list).
    explicit_targets: list[str] = []
    visibility = "private"
    i = 0
    while i < len(flags):
        tok = flags[i]
        if tok == "--extract" and i + 1 < len(flags):
            explicit_targets.append(flags[i + 1])
            i += 2
            continue
        if tok == "--visibility":
            if i + 1 >= len(flags):
                print("ERROR: --visibility requires a value (public|private)", file=sys.stderr)
                return 1
            val = flags[i + 1]
            if val not in ("public", "private"):
                print(f"ERROR: --visibility must be one of public|private (got {val!r})", file=sys.stderr)
                return 1
            visibility = val
            i += 2
            continue
        i += 1
    force_extract = "--force-extract" in flags

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
        print("OK: no dev parts in MAIN repo (all extracted to source repos).")
        return 0

    if dry_run:
        print(summarise_plan(plan, visibility=visibility, force_extract=force_extract))
        try:
            check_working_tree_safe(plugin_root, plan.targets)
        except StripError as e:
            print(f"\nNOTE: working tree is NOT in a state where the plan could execute: {e}", file=sys.stderr)
        return 0

    # Live execution path. Requires --auto (no interactive mode in this RC).
    if "--auto" not in flags:
        print(summarise_plan(plan, visibility=visibility, force_extract=force_extract))
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
        apply_plan(plan, visibility=visibility)
    except StripError as e:
        print(f"FAILED to apply plan: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"FAILED: subprocess error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
