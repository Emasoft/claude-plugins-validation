#!/usr/bin/env python3
"""CPV → claude-menu-system bridge (TRDD-4de479a0).

CPV menus are rendered AND emitted by the externalised `claude-menu-system`
plugin's `Stop` / `SubagentStop` hook. The hook prints the menu through the
hook JSON `systemMessage` field, so the menu is shown to the user but NEVER
enters the transcript/context — zero token cost regardless of menu size, and
no subagent fork (so no prompt-cache re-prime). This is strictly cheaper than
CPV's old `format_menu.py` inline rendering and the `cpv-format-menu`
`context: fork` skill, both removed in this migration.

## Fixed-key routing (no renumbering)

CPV menus use a STABLE key→action contract. Two namespaces that never collide:
numbers ``1..N`` index the alphabetically-sorted DYNAMIC list (the things that
vary), and letters are FIXED actions/navigation (``M``/``B``/``X`` reserved for
Main/Back/Exit). Every letter always maps to the same action. An item that
doesn't apply right now is simply omitted — its row is NOT printed at all (no
blank line, no placeholder); the displayed rows stay contiguous and no
surviving key is relettered.

Because keys are stable, the orchestrator routes the user's typed key from the
FIXED map documented in its own skill — it never needs to read back a rendered
action map. So this bridge sets `renumber: false` by default (claude-menu-system
otherwise renumbers the surviving rows after dropping disabled ones), and does
NOT persist any action-map sidecar.

This module is the thin CPV-side bridge. Two responsibilities:

1. ``resolve_cms_root()`` — locate the installed claude-menu-system. CPV has
   NO inline menu renderer anymore, so if claude-menu-system is missing this
   FAILS FAST with an install hint (no silent fallback — fail-fast rule).
2. ``write_menu(spec)`` — enforce the fixed-key convention (`renumber: false`),
   write the spec to a tempfile, invoke ``menu_write.py`` (which queues the
   menu for the hook to emit at turn end), and return the queue path.

The orchestrator MUST end its turn after calling this — the hook emits
post-turn. Never print the menu inline.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Canonical install location of the menu-system plugin. We ONLY glob this exact
# path — never anything outside the plugin cache — so a crafted spec can't make
# us resolve an arbitrary directory.
_CMS_MARKETPLACE = "emasoft-plugins"
_CMS_PLUGIN = "claude-menu-system"

_INSTALL_HINT = (
    "Install it:\n"
    "  claude plugin install Emasoft/claude-menu-system@emasoft-plugins\n"
    "  /reload-plugins"
)


class MenuSystemUnavailable(RuntimeError):
    """Raised when claude-menu-system is not installed.

    CPV renders every menu through that plugin's Stop-hook emitter and has no
    inline fallback renderer, so this is a hard, fail-fast error.
    """


def _default_cache_base() -> Path:
    return Path.home() / ".claude" / "plugins" / "cache" / _CMS_MARKETPLACE / _CMS_PLUGIN


def _version_key(version_dir: Path) -> tuple[int, object]:
    """Sort key that orders dotted numeric versions correctly (0.1.10 > 0.1.5).

    Falls back to a lexicographic tie-breaker for non-numeric version names so
    a stray directory never crashes resolution.
    """
    parts = version_dir.name.split(".")
    try:
        return (0, tuple(int(p) for p in parts))
    except ValueError:
        return (1, version_dir.name)


def resolve_cms_root(cache_base: Path | None = None) -> Path:
    """Return the newest installed claude-menu-system version directory.

    A version dir qualifies only if it ships ``scripts/menu_write.py`` (so a
    half-extracted install is skipped). Fails fast with an install hint if no
    usable version is present.

    Args:
        cache_base: override the canonical cache path (tests pass a temp dir).
    """
    base = cache_base if cache_base is not None else _default_cache_base()
    if not base.is_dir():
        raise MenuSystemUnavailable(
            f"claude-menu-system is not installed (looked in {base}). CPV renders "
            f"menus via that plugin's Stop-hook emitter and has no inline fallback.\n"
            + _INSTALL_HINT
        )
    usable = [d for d in base.iterdir() if d.is_dir() and (d / "scripts" / "menu_write.py").is_file()]
    if not usable:
        raise MenuSystemUnavailable(
            f"claude-menu-system is present at {base} but no version ships "
            f"scripts/menu_write.py. Reinstall:\n" + _INSTALL_HINT
        )
    return max(usable, key=_version_key)


def write_menu(spec: dict, *, cache_base: Path | None = None) -> Path:
    """Queue a menu spec for the claude-menu-system Stop hook to emit.

    Enforces CPV's fixed-key convention: ``renumber`` defaults to ``false`` so
    claude-menu-system keeps the caller's keys verbatim (it otherwise renumbers
    the rows that survive after dropping ``disabled`` ones). The caller owns the
    stable key→action_id contract and routes the user's reply from it directly.

    Args:
        spec: a claude-menu-system spec dict (``spec_version``, ``mode``,
            ``plugin``, ``slug``, plus the per-mode fields). ``renumber`` is set
            to ``false`` unless the caller already specified it.
        cache_base: test override for the CMS cache path.

    Returns the queue file path that ``menu_write.py`` allocated.

    Raises:
        MenuSystemUnavailable: claude-menu-system is not installed.
        RuntimeError: ``menu_write.py`` exited non-zero (invalid spec, etc.).
    """
    cms = resolve_cms_root(cache_base=cache_base)
    menu_write = cms / "scripts" / "menu_write.py"

    # Fixed-key convention: never renumber unless the caller explicitly opted in.
    spec_out = {**spec, "renumber": spec.get("renumber", False)}

    tf = tempfile.NamedTemporaryFile("w", suffix=".cpv-menu.json", delete=False, encoding="utf-8")
    try:
        json.dump(spec_out, tf)
        tf.flush()
        tf.close()
        proc = subprocess.run(
            [sys.executable, str(menu_write), tf.name],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(tf.name).unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(f"menu_write.py failed (exit {proc.returncode}): {proc.stderr.strip()}")

    return Path(proc.stdout.strip())


def _cli(argv: list[str]) -> int:
    """CLI for orchestrator command/agent bodies (invoked via Bash).

    Usage: ``cpv_menu.py <spec-path.json>``
    Prints the queue path on stdout. The menu is emitted by the claude-menu-system
    Stop hook at the end of THIS turn — end the turn after calling this; never
    print the menu inline. Route the user's next-turn reply from the fixed
    key→action map documented in your skill.
    """
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print("usage: cpv_menu.py <spec-path.json>", file=sys.stderr)
        return 2
    spec_path = Path(argv[1])
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cpv_menu: cannot read spec {spec_path}: {exc}", file=sys.stderr)
        return 2
    try:
        queue_path = write_menu(spec)
    except MenuSystemUnavailable as exc:
        print(f"cpv_menu: {exc}", file=sys.stderr)
        return 5
    except RuntimeError as exc:
        print(f"cpv_menu: {exc}", file=sys.stderr)
        return 3
    print(queue_path)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
