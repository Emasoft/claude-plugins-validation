"""Border-alignment test for the cpv-main-menu menu tables.

Every box-drawing table in `skills/cpv-main-menu-skill/references/menu-tree.md`
must have aligned borders. The user's UI ONLY renders correctly when each
row's character columns line up under the column separators on the top
border line.

`len()` is the WRONG width measure for Unicode strings — emoji, CJK,
and many BMP arrows render as 2 columns wide in monospace terminals. We
use `unicodedata.east_asian_width()` to correctly classify each char.

This test is preventive: any future menu edit that breaks border
alignment will fail this test before reaching the user's terminal.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MENU_TREE = REPO_ROOT / "skills" / "cpv-main-menu-skill" / "references" / "menu-tree.md"


# Unicode box-drawing characters that we recognise as table delimiters.
# Order matters: BOX_TOP_HORIZONTAL must come before BOX_HORIZONTAL_LIGHT
# in any membership check that uses startswith / startswith.
BOX_TOP_LEFT = "┏"
BOX_TOP_RIGHT = "┓"
BOX_BOTTOM_LEFT = "└"
BOX_BOTTOM_RIGHT = "┘"
BOX_HEAVY_T_BOTTOM = "┳"  # heavy top-border T
BOX_LIGHT_T_TOP = "┴"  # light bottom-border T
BOX_HEAVY_PIPE = "┃"  # heavy vertical (header rows)
BOX_LIGHT_PIPE = "│"  # light vertical (body rows)


def display_width(text: str) -> int:
    """Sum of east-asian display widths in a Western (non-CJK) terminal.

    F (Fullwidth) and W (Wide) characters count as 2 — emoji, CJK ideographs,
    and the like. A (Ambiguous) chars count as 1 because the user's terminal
    is non-CJK; this includes ALL box-drawing chars (U+2500-U+257F), which
    is critical: if we counted box chars as 2 the test would be useless.

    Everything else counts as 1.
    """
    total = 0
    for ch in text:
        eaw = unicodedata.east_asian_width(ch)
        total += 2 if eaw in ("F", "W") else 1
    return total


def find_tables(text: str) -> list[list[str]]:
    """Split menu-tree.md into individual box-drawing tables.

    A table starts at a line that begins with `┏` (heavy top-left corner)
    and ends at the next line that begins with `└` (light bottom-left).
    Returns a list of tables, each a list of its constituent lines.
    """
    tables: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(BOX_TOP_LEFT):
            current = [stripped]
            continue
        if current is not None:
            current.append(stripped)
            if stripped.startswith(BOX_BOTTOM_LEFT):
                tables.append(current)
                current = None
    return tables


def test_menu_tree_file_exists():
    assert MENU_TREE.is_file(), f"menu-tree.md not found at {MENU_TREE}"


def test_every_table_has_consistent_row_widths():
    """For each table, every line must have the same display width.

    This catches the most common drift: a row whose content overflows or
    underfills the column, breaking the right-edge border.
    """
    text = MENU_TREE.read_text(encoding="utf-8")
    tables = find_tables(text)
    assert tables, "No box-drawing tables found in menu-tree.md"

    failures: list[str] = []
    for idx, table in enumerate(tables, start=1):
        widths = [display_width(line) for line in table]
        if len(set(widths)) > 1:
            ref = widths[0]
            offenders = [f"  line {i + 1}: width={w} text={table[i]!r}" for i, w in enumerate(widths) if w != ref]
            failures.append(
                f"Table #{idx} (starts: {table[0][:60]!r}) has inconsistent "
                f"row widths. Reference (top border) = {ref}. Offenders:\n" + "\n".join(offenders)
            )

    assert not failures, "Border-alignment failures:\n\n" + "\n\n".join(failures)


def test_unicode_wide_chars_in_menu_tables():
    """Diagnostic-only: list every East-Asian-F/W char in any table cell.

    Doesn't fail. Surfaces emoji and CJK ideographs that take 2 columns,
    so reviewers can spot them when designing new tables.
    """
    text = MENU_TREE.read_text(encoding="utf-8")
    tables = find_tables(text)
    found: dict[str, int] = {}
    for table in tables:
        for line in table:
            for ch in line:
                if unicodedata.east_asian_width(ch) in ("F", "W"):
                    found[ch] = found.get(ch, 0) + 1
    if found:
        msg = "\n".join(
            f"  {ch!r} (U+{ord(ch):04X}, eaw={unicodedata.east_asian_width(ch)}): {count} occurrences"
            for ch, count in sorted(found.items(), key=lambda kv: -kv[1])
        )
        print(f"\n[diagnostic] East-Asian wide chars in menu tables:\n{msg}")
