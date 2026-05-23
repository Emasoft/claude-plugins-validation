"""Regression guard for the cpv-main-menu menu-tree migration (TRDD-4de479a0).

Before TRDD-4de479a0 this file validated the alignment of inline Unicode
box-drawing tables (`┏━━━┓` / `┃ … ┃` / `└───┘`) embedded directly in
`menu-tree.md`. Phase 3 of the menu-migration replaced every one of those
inline tables with `cpv_menu.py` spec invocations — the claude-menu-system
Stop hook now renders and emits each menu via `systemMessage` at turn end,
so menu rows never enter the transcript / prompt cache and there is no
border-alignment risk at the source level.

The previous border-alignment assertion is therefore moot. We invert it:
the post-migration invariant is that `menu-tree.md` must contain NO inline
box-drawing tables at all — any re-introduction (e.g. someone hand-rendering
a table back into the file) would silently undo the migration's cache /
token benefits. This test catches that regression before it ships.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MENU_TREE = REPO_ROOT / "skills" / "cpv-main-menu-skill" / "references" / "menu-tree.md"

# Heavy top-left corner — the unambiguous opener for the box-drawing tables
# CPV used to hand-render. Light-style and other corner glyphs are NOT
# matched: those appear inside spec JSON (rendered output) and in unrelated
# documentation tables in adjacent files, but `┏` was the canonical opener
# CPV's hand-rendered menus always used.
BOX_TOP_LEFT = "┏"


def test_menu_tree_file_exists() -> None:
    """menu-tree.md is the canonical menu spec catalogue — it must exist."""
    assert MENU_TREE.is_file(), f"menu-tree.md not found at {MENU_TREE}"


def test_menu_tree_has_no_inline_box_drawing_tables() -> None:
    """Post-TRDD-4de479a0 invariant: zero hand-rendered inline tables.

    Every menu surface migrated to `cpv_menu.py` spec invocations + the
    claude-menu-system Stop-hook emitter. Any line opening with the heavy
    top-left corner `┏` indicates a re-introduced inline table — that
    would re-create the cache / token-cost regression the migration
    explicitly eliminated.
    """
    text = MENU_TREE.read_text(encoding="utf-8")
    offenders = [
        (i + 1, line)
        for i, line in enumerate(text.splitlines())
        if line.lstrip().startswith(BOX_TOP_LEFT)
    ]
    assert not offenders, (
        "menu-tree.md re-introduces inline box-drawing tables — these were "
        "all replaced by cpv_menu.py spec invocations in TRDD-4de479a0 "
        "Phase 3 (the CMS Stop hook emits via systemMessage at turn end, "
        "zero token cost). Build a CMS spec instead:\n\n"
        + "\n".join(f"  line {ln}: {text!r}" for ln, text in offenders)
    )
