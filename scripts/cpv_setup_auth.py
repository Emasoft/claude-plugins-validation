#!/usr/bin/env python3
"""cpv_setup_auth — read-only orchestrator for the eight CPV auth surfaces.

Implements the user-facing slice of TRDD-b5e44619 (universal-publish-and-auth)
Phase C — the eight-surface auth contract — without touching scripts/publish.py
(which is being heavily restructured under TRDD-9065109a in a parallel
worktree). Instead of duplicating helper code, this script delegates to the
canonical helpers that already exist:

    Surface 1  Git identity                  → ``git config user.name/email``
    Surface 2  GitHub HTTPS auth (gh CLI)    → ``gh auth status``
    Surface 3  GitHub SSH auth               → ``ssh-add -L`` (best-effort)
    Surface 4  MARKETPLACE_PAT secret        → env vars (PAT_MARKETPLACE,
                                               MARKETPLACE_PAT) + repo secret
                                               via scripts/set_marketplace_pat.py
    Surface 5  Branch protection rules       → scripts/setup_branch_rules.py
                                               or setup_branch_rules_generic.py
    Surface 6  Pre-push hook installation    → ``git config core.hooksPath``
                                               + scripts/setup_git_hooks.py
    Surface 7  GPG / SSH commit signing      → ``git config commit.gpgsign``
                                               + ``user.signingkey``
    Surface 8  External-scanner installation → scripts/cpv_install_scanners.py
                                               (fclones, cc-audit, trufflehog,
                                               semgrep, tirith, cisco)

Read-only by default. Each surface check returns a SurfaceResult with one of
four statuses: SET / NOT SET / PARTIAL / N/A. The orchestrator never invokes
``gh auth token`` (per TRDD-bbff5bc5 §4.1 — PAT non-leakage), never reads
secret values from disk, and never writes to disk.

Usage::

    uv run python scripts/cpv_setup_auth.py check          # status report (default)
    uv run python scripts/cpv_setup_auth.py check --strict # exit 1 if any required surface is NOT SET
    uv run python scripts/cpv_setup_auth.py --help

Required surfaces in --strict mode:
    1  Git identity   (publish.py needs it for commit author)
    2  GitHub HTTPS   (publish.py Gate 12 / 13 need it for push + release)
    6  Pre-push hook  (canonical-pipeline mandates it for quality gating)

Surfaces 3, 4, 5, 7, 8 are advisory — their absence is reported but does
not fail --strict mode.

Exit codes:
    0  CLI succeeded; status report rendered. In --strict mode, all required
       surfaces are SET.
    1  --strict mode: at least one required surface is NOT SET / PARTIAL.
    2  Bad usage / argparse error.

Cross-references:
    - TRDD-b5e44619 (this TRDD) §C — eight-surface auth table.
    - TRDD-bbff5bc5 §4.1 — gh auth token never invoked.
    - scripts/publish.py:_ensure_gh_auth — Gate 12 / 13 push-time auth check.
    - scripts/set_marketplace_pat.py — DEFAULT_PAT_ENV_VARS lookup order.
    - scripts/cpv_install_scanners.py — install_all_scanners() return shape.

Why this script instead of /cpv-setup-auth (slash command + agent)?
    The slash-command + agent + skill triple is described in the TRDD
    Phase C plan but commands/*.md and agents/*.md are owned by other agents
    in the wave-4 implementation contract. This script is the callable
    Python core they would dispatch to once the slash-command lands —
    landing it now means the agent work later is a thin wrapper instead of
    re-implementing the eight checks from scratch.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# ── Module constants ────────────────────────────────────────────────────────

# Status values — kept as bare strings (not Enum) so render_table() and
# downstream consumers can str-format without member-resolution overhead.
STATUS_SET: str = "SET"
STATUS_NOT_SET: str = "NOT SET"
STATUS_PARTIAL: str = "PARTIAL"
STATUS_NA: str = "N/A"

# Repo locations — derived from this script's own location so the helpers
# stay co-located with publish.py / set_marketplace_pat.py / etc.
SCRIPTS_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = SCRIPTS_DIR.parent

# The 6 external scanners cpv_install_scanners can install. Names match the
# binary names ``shutil.which`` will resolve to.
EXTERNAL_SCANNER_NAMES: tuple[str, ...] = (
    "fclones",
    "cc-audit",
    "trufflehog",
    "semgrep",
    "tirith",
    "cisco-skill-scanner",
)

# Required surfaces in --strict mode. These three are load-bearing for any
# real publish flow; the other five are advisory.
REQUIRED_SURFACE_IDS: frozenset[int] = frozenset({1, 2, 6})


# ── Result type ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SurfaceResult:
    """One row in the auth-surface report.

    ``surface_id`` matches the TRDD's table column #. ``status`` is one of
    the four module-level STATUS_* constants. ``detail`` is a short human-
    readable string (never contains secret values — only labels like
    "Emasoft <e@x>" for git identity or "MARKETPLACE_PAT (env)" for the PAT).
    """

    surface_id: int
    name: str
    status: str
    detail: str


# ── Subprocess helpers ──────────────────────────────────────────────────────


def _run_cmd(cmd: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    """Wrapper around subprocess.run for unit-testability.

    Tests patch this single sym so we don't have to mock ``subprocess.run``
    globally (which collides with other tests' mocks). All calls capture
    output, never check, never propagate stdout/stderr to the parent.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_config_get(key: str, scope: str = "local") -> str:
    """Return the value of a git config key, or empty string if unset.

    ``scope`` is one of ``"local"`` or ``"global"``. The local scope reads
    from the current working directory's ``.git/config``; missing
    .git/ silently returns "" rather than raising — auth checks must be
    informational, not fatal.
    """
    flag = "--global" if scope == "global" else "--local"
    try:
        result = _run_cmd(["git", "config", flag, "--get", key])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    # subprocess.CompletedProcess.stdout is typed Any in some contexts when
    # universal_newlines/text=True is passed via kwargs — coerce to str
    # explicitly to satisfy strict --no-any-return mypy mode.
    return str(result.stdout).strip()


# ── Surface 1: Git identity ─────────────────────────────────────────────────


def check_git_identity() -> SurfaceResult:
    """git config user.name + user.email — local OR global.

    SET      → both name AND email set in either local or global scope
    PARTIAL  → only one of {name, email} set anywhere
    NOT SET  → neither set anywhere
    """
    name_local = _git_config_get("user.name", "local")
    email_local = _git_config_get("user.email", "local")
    name_global = _git_config_get("user.name", "global")
    email_global = _git_config_get("user.email", "global")

    name = name_local or name_global
    email = email_local or email_global

    if name and email:
        return SurfaceResult(
            surface_id=1,
            name="Git identity",
            status=STATUS_SET,
            detail=f"{name} <{email}>",
        )
    if name or email:
        return SurfaceResult(
            surface_id=1,
            name="Git identity",
            status=STATUS_PARTIAL,
            detail=f"name={'set' if name else 'unset'} email={'set' if email else 'unset'}",
        )
    return SurfaceResult(
        surface_id=1,
        name="Git identity",
        status=STATUS_NOT_SET,
        detail="run: git config --global user.name <name>; git config --global user.email <email>",
    )


# ── Surface 2: gh CLI auth ──────────────────────────────────────────────────


def check_gh_auth() -> SurfaceResult:
    """gh auth status — read-only check.

    Per TRDD-bbff5bc5 §4.1, NEVER invokes ``gh auth token``. The check is
    binary: gh installed AND ``gh auth status`` exits 0, or NOT SET.
    Push-permission verification against a specific owner/repo is a separate
    helper (publish.py's ``_ensure_gh_auth(owner, repo)`` covers Gate 12);
    this surface answers the lower-bar question "is there a gh login at all?".
    """
    gh = shutil.which("gh")
    if gh is None:
        return SurfaceResult(
            surface_id=2,
            name="GitHub HTTPS auth (gh CLI)",
            status=STATUS_NOT_SET,
            detail="gh CLI not installed — see https://cli.github.com/",
        )
    try:
        result = _run_cmd([gh, "auth", "status"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return SurfaceResult(
            surface_id=2,
            name="GitHub HTTPS auth (gh CLI)",
            status=STATUS_NOT_SET,
            detail="gh auth status failed to run",
        )
    if result.returncode != 0:
        return SurfaceResult(
            surface_id=2,
            name="GitHub HTTPS auth (gh CLI)",
            status=STATUS_NOT_SET,
            detail="gh installed but not authenticated — run: gh auth login",
        )
    # Pull a short identity hint out of the output if we can.
    detail = "gh authenticated"
    for line in (result.stdout + result.stderr).splitlines():
        line = line.strip()
        if "account " in line and ("Logged in" in line or "Active" in line):
            detail = line.split("account ", 1)[1].split(" ", 1)[0]
            break
    return SurfaceResult(
        surface_id=2,
        name="GitHub HTTPS auth (gh CLI)",
        status=STATUS_SET,
        detail=detail,
    )


# ── Surface 3: SSH auth ─────────────────────────────────────────────────────


def check_ssh_auth() -> SurfaceResult:
    """ssh-add -L — best-effort detection of registered SSH keys.

    Some platforms / minimal Docker images don't ship ssh-add. In that case
    we report N/A rather than NOT SET — a missing tool isn't a misconfig.
    """
    ssh_add = shutil.which("ssh-add")
    if ssh_add is None:
        return SurfaceResult(
            surface_id=3,
            name="GitHub SSH auth (ssh-agent)",
            status=STATUS_NA,
            detail="ssh-add not on PATH (skipped)",
        )
    try:
        result = _run_cmd([ssh_add, "-L"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return SurfaceResult(
            surface_id=3,
            name="GitHub SSH auth (ssh-agent)",
            status=STATUS_NA,
            detail="ssh-add invocation failed (skipped)",
        )
    # ssh-add -L returns 1 with "The agent has no identities." if empty.
    if result.returncode != 0 or "no identities" in (result.stderr or "").lower():
        return SurfaceResult(
            surface_id=3,
            name="GitHub SSH auth (ssh-agent)",
            status=STATUS_NOT_SET,
            detail="ssh-agent has no keys — run: ssh-add ~/.ssh/id_ed25519",
        )
    if not result.stdout.strip():
        return SurfaceResult(
            surface_id=3,
            name="GitHub SSH auth (ssh-agent)",
            status=STATUS_NOT_SET,
            detail="ssh-agent returned no keys",
        )
    # Count the number of registered keys for the detail string.
    n_keys = sum(1 for line in result.stdout.splitlines() if line.strip())
    return SurfaceResult(
        surface_id=3,
        name="GitHub SSH auth (ssh-agent)",
        status=STATUS_SET,
        detail=f"{n_keys} key(s) registered",
    )


# ── Surface 4: MARKETPLACE_PAT ──────────────────────────────────────────────


def check_marketplace_pat() -> SurfaceResult:
    """MARKETPLACE_PAT — env-var presence check.

    Order matches set_marketplace_pat.DEFAULT_PAT_ENV_VARS:
    PAT_MARKETPLACE first (preferred per the doctor agent's Phase 6.5
    audit), then MARKETPLACE_PAT (legacy, kept for backward compat).

    Never reads or prints the value — we only check truthiness of the
    environment lookup. Repo-side secret verification (gh secret list) is
    opt-in via ``set_marketplace_pat.py --verify-only`` and intentionally
    NOT triggered by the read-only check.
    """
    # Same ordering / names as set_marketplace_pat.DEFAULT_PAT_ENV_VARS.
    candidates = ("PAT_MARKETPLACE", "MARKETPLACE_PAT")
    for env_name in candidates:
        value = os.environ.get(env_name)
        if value:
            return SurfaceResult(
                surface_id=4,
                name="MARKETPLACE_PAT (env)",
                status=STATUS_SET,
                detail=f"{env_name} is set ({len(value)} chars)",
            )
    return SurfaceResult(
        surface_id=4,
        name="MARKETPLACE_PAT (env)",
        status=STATUS_NOT_SET,
        detail="set PAT_MARKETPLACE or MARKETPLACE_PAT in env (gh PAT with 'repo' scope)",
    )


# ── Surface 5: Branch protection rules ──────────────────────────────────────


def check_branch_rules() -> SurfaceResult:
    """Branch-rules helper presence on disk.

    The helper script is what actually applies the rules — verifying its
    presence here means the publisher CAN apply them. Whether they HAVE been
    applied to a specific GitHub repo is a remote query (``gh api
    repos/<owner>/<repo>/rulesets``) that requires owner/repo input and is
    out of scope for the default check_surfaces() path.
    """
    candidates = (
        SCRIPTS_DIR / "setup_branch_rules.py",
        SCRIPTS_DIR / "setup_branch_rules_generic.py",
    )
    found = [p.name for p in candidates if p.is_file()]
    if found:
        return SurfaceResult(
            surface_id=5,
            name="Branch protection rules",
            status=STATUS_SET,
            detail=f"helpers available: {', '.join(found)}",
        )
    return SurfaceResult(
        surface_id=5,
        name="Branch protection rules",
        status=STATUS_NOT_SET,
        detail="setup_branch_rules helper not found in scripts/",
    )


# ── Surface 6: Pre-push hook ────────────────────────────────────────────────


def check_pre_push_hook(plugin_root: Path | None = None) -> SurfaceResult:
    """git config core.hooksPath + the pre-push hook file.

    SET      → core.hooksPath set AND <hooksPath>/pre-push exists
    PARTIAL  → no core.hooksPath but .git/hooks/pre-push exists (default
               location — works but is the un-managed path)
    NOT SET  → neither
    """
    root = plugin_root or REPO_ROOT
    hooks_path_str = _git_config_get("core.hooksPath")
    if hooks_path_str:
        # core.hooksPath may be relative to the repo root.
        hooks_path = Path(hooks_path_str)
        if not hooks_path.is_absolute():
            hooks_path = root / hooks_path
        pre_push = hooks_path / "pre-push"
        if pre_push.is_file():
            return SurfaceResult(
                surface_id=6,
                name="Pre-push hook",
                status=STATUS_SET,
                detail=f"core.hooksPath={hooks_path_str}",
            )
        return SurfaceResult(
            surface_id=6,
            name="Pre-push hook",
            status=STATUS_PARTIAL,
            detail=f"core.hooksPath={hooks_path_str} but pre-push file missing",
        )
    # Fall back to the default location — works but un-managed.
    default_pre_push = root / ".git" / "hooks" / "pre-push"
    if default_pre_push.is_file():
        return SurfaceResult(
            surface_id=6,
            name="Pre-push hook",
            status=STATUS_PARTIAL,
            detail="hook in .git/hooks/pre-push (run setup_git_hooks.py to manage)",
        )
    return SurfaceResult(
        surface_id=6,
        name="Pre-push hook",
        status=STATUS_NOT_SET,
        detail="run: uv run python scripts/setup_git_hooks.py",
    )


# ── Surface 7: Commit signing ───────────────────────────────────────────────


def check_commit_signing() -> SurfaceResult:
    """commit.gpgsign + user.signingkey — purely advisory.

    SET      → commit.gpgsign=true AND user.signingkey is set
    PARTIAL  → user.signingkey set but commit.gpgsign=false (or unset)
    NOT SET  → neither (perfectly OK — signing is optional convention)
    """
    # _git_config_get's default scope is "local"; we explicitly check both
    # because signing keys are usually configured globally.
    sign_local = _git_config_get("commit.gpgsign", "local").lower()
    sign_global = _git_config_get("commit.gpgsign", "global").lower()
    key_local = _git_config_get("user.signingkey", "local")
    key_global = _git_config_get("user.signingkey", "global")

    sign_enabled = (sign_local == "true") or (sign_global == "true")
    key_set = bool(key_local or key_global)

    if sign_enabled and key_set:
        return SurfaceResult(
            surface_id=7,
            name="Commit signing (GPG/SSH)",
            status=STATUS_SET,
            detail="commit.gpgsign=true + user.signingkey configured",
        )
    if key_set:
        return SurfaceResult(
            surface_id=7,
            name="Commit signing (GPG/SSH)",
            status=STATUS_PARTIAL,
            detail="signingkey set but commit.gpgsign != true",
        )
    return SurfaceResult(
        surface_id=7,
        name="Commit signing (GPG/SSH)",
        status=STATUS_NOT_SET,
        detail="optional — see https://docs.github.com/en/authentication/managing-commit-signature-verification",
    )


# ── Surface 8: External scanners ────────────────────────────────────────────


def check_external_scanners() -> SurfaceResult:
    """Best-effort PATH check for the 6 scanners cpv_install_scanners installs.

    SET      → all 6 on PATH
    PARTIAL  → 1-5 on PATH
    NOT SET  → none on PATH
    """
    found = [name for name in EXTERNAL_SCANNER_NAMES if shutil.which(name)]
    n_total = len(EXTERNAL_SCANNER_NAMES)
    n_found = len(found)
    if n_found == n_total:
        return SurfaceResult(
            surface_id=8,
            name="External scanners",
            status=STATUS_SET,
            detail=f"{n_found}/{n_total} scanners on PATH",
        )
    if n_found > 0:
        missing = [n for n in EXTERNAL_SCANNER_NAMES if n not in found]
        return SurfaceResult(
            surface_id=8,
            name="External scanners",
            status=STATUS_PARTIAL,
            detail=f"{n_found}/{n_total} on PATH; missing: {', '.join(missing)}",
        )
    return SurfaceResult(
        surface_id=8,
        name="External scanners",
        status=STATUS_NOT_SET,
        detail="run: uv run python scripts/manage_doctor.py --install-scanners",
    )


# ── Surface registry ────────────────────────────────────────────────────────


# Ordered registry — id → {name, check}. Tests assert this is exactly
# {1..8}. Adding a 9th surface in the future requires updating
# REQUIRED_SURFACE_IDS and the test_eight_surfaces_enumerated assertion.
AUTH_SURFACES: dict[int, dict[str, object]] = {
    1: {"name": "Git identity", "check": check_git_identity},
    2: {"name": "GitHub HTTPS auth (gh CLI)", "check": check_gh_auth},
    3: {"name": "GitHub SSH auth (ssh-agent)", "check": check_ssh_auth},
    4: {"name": "MARKETPLACE_PAT (env)", "check": check_marketplace_pat},
    5: {"name": "Branch protection rules", "check": check_branch_rules},
    6: {"name": "Pre-push hook", "check": check_pre_push_hook},
    7: {"name": "Commit signing (GPG/SSH)", "check": check_commit_signing},
    8: {"name": "External scanners", "check": check_external_scanners},
}


def check_surfaces() -> list[SurfaceResult]:
    """Run every surface check in TRDD order; return results.

    Each check is independent — failure in one does not abort the rest.
    Surfaces that raise unexpectedly are caught here so the report stays
    rendered even when a helper has an environment quirk.
    """
    results: list[SurfaceResult] = []
    for sid in sorted(AUTH_SURFACES.keys()):
        spec = AUTH_SURFACES[sid]
        check_fn: Callable[[], SurfaceResult] = spec["check"]  # type: ignore[assignment]
        try:
            results.append(check_fn())
        except Exception as exc:  # noqa: BLE001 — defensive catch-all for surface checks
            results.append(
                SurfaceResult(
                    surface_id=sid,
                    name=str(spec["name"]),
                    status=STATUS_NOT_SET,
                    detail=f"check raised {type(exc).__name__}: {exc}",
                )
            )
    return results


# ── Rendering ───────────────────────────────────────────────────────────────


def render_table(results: list[SurfaceResult]) -> str:
    """Render results as a Unicode-bordered table.

    Heavy borders (━ / ┃ / ┏) for the header row, light borders (─ / │ / ┌)
    for the body — same style as the user's preferred test-result tables.
    """
    headers = ("#", "Surface", "Status", "Detail")
    rows = [(str(r.surface_id), r.name, r.status, r.detail or "") for r in sorted(results, key=lambda x: x.surface_id)]
    # Compute column widths from headers + data.
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _fmt_row(cells: tuple[str, ...], left: str, sep: str, right: str) -> str:
        padded = [f" {cells[i].ljust(widths[i])} " for i in range(len(cells))]
        return left + sep.join(padded) + right

    def _hline_heavy() -> tuple[str, str, str]:
        return (
            "┏" + "┳".join("━" * (w + 2) for w in widths) + "┓",
            "┡" + "╇".join("━" * (w + 2) for w in widths) + "┩",
            "└" + "┴".join("─" * (w + 2) for w in widths) + "┘",
        )

    top, mid, bottom = _hline_heavy()
    lines = [
        top,
        _fmt_row(headers, "┃", "┃", "┃"),
        mid,
    ]
    for row in rows:
        lines.append(_fmt_row(row, "│", "│", "│"))
    lines.append(bottom)
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpv_setup_auth",
        description=(
            "Read-only orchestrator for the eight CPV auth surfaces. "
            "Reports SET / NOT SET / PARTIAL / N/A per surface. "
            "Never reads or prints secret values; never invokes 'gh auth token'."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=False)

    check_p = sub.add_parser(
        "check",
        help="Render the auth-surface status table (default).",
    )
    check_p.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit non-zero when any REQUIRED surface (1: git identity, 2: gh auth, "
            "6: pre-push hook) is NOT SET or PARTIAL. Other surfaces are advisory."
        ),
    )
    return parser


def main() -> int:
    """CLI entrypoint. Returns the exit code."""
    parser = _build_parser()
    args = parser.parse_args()

    # Default to `check` if no subcommand provided.
    command = getattr(args, "command", None) or "check"
    strict = bool(getattr(args, "strict", False))

    if command != "check":
        # argparse already raised on unknown subcommands; this guards against
        # adding a new subcommand without wiring it.
        parser.print_help(sys.stderr)
        return 2

    results = check_surfaces()
    print(render_table(results))

    if strict:
        unmet = [r for r in results if r.surface_id in REQUIRED_SURFACE_IDS and r.status != STATUS_SET]
        if unmet:
            names = ", ".join(f"#{r.surface_id} {r.name}" for r in unmet)
            print(
                f"\nstrict: required surface(s) not SET: {names}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
