#!/usr/bin/env python3
"""Render menus, summaries, breakdowns, and status tables for the /cpv-* orchestrators.

Four CLI modes:

    python3 scripts/format_menu.py menu          <json>
    python3 scripts/format_menu.py summary       <json>
    python3 scripts/format_menu.py breakdown     <json>
    python3 scripts/format_menu.py status_table  <json>

Owns ALL Unicode-bordered table rendering for the main-session
orchestrator command bodies (cpv-doctor, cpv-fix-validation,
cpv-fix-marketplace-validation, cpv-cache-optimize) AND for any
sub-script or agent that needs a properly aligned table — security
findings, doctor recipe breakdowns, plugin-creator/upgrade build
status reports, health-check outputs, etc. The bodies themselves
never embed Unicode tables — they hand the rows to this helper.

**breakdown** mode renders a matrix table: rows are categories (e.g.
recipe names, security rules, file paths) and columns are severities
(CRITICAL / MAJOR / MINOR / NIT / WARNING). Each row shows the
per-severity count + a row total; a final TOTAL row sums each
column. Use for: doctor recipe findings broken down by recipe,
security-finding tables broken down by RC-id, any "where do the
findings come from?" report.

**status_table** mode renders a build/audit/health table: rows are
components (e.g. plugin.json, commands/, skills/<name>, etc.) and
each row has a STATUS column (✓ implemented / ✗ missing / ⚠ buggy /
◐ partial / ○ pending / ⊝ skipped) plus a Notes column. Use for:
plugin-creator/upgrade build reports, doctor quick-health-check
output, anything that's "checklist of items + per-item status".

Why a helper instead of inline Python in the command body:

- Cell widths must use **display columns**, not codepoint count.
  Box-drawing characters and east-asian chars take 1 or 2 columns
  each; ``len("┏━━┓") == 4`` but the visual width is 4 columns too
  (each char is 1 column). Emoji and wide-Asian glyphs misalign
  ``len()``-padded cells.
- The "drop disabled rows + renumber" logic is shared by 4 command
  bodies; centralising it makes the behaviour identical and
  testable.
- The orchestrator can run a single bash one-liner instead of a
  multi-line python heredoc, keeping the body readable.

JSON shapes — see ``design/tasks/TRDD-81e7fa34-...`` § Phase A.

Exit codes:

- 0  success — table printed on stdout, action_id map (menu mode
     only) printed on stderr as JSON.
- 2  malformed JSON on stdin/argv.
- 3  JSON shape error (missing required keys, wrong types).
"""

from __future__ import annotations

import json
import sys
import unicodedata
from typing import Any

# ---------------------------------------------------------------------------
# Display-width oracle
# ---------------------------------------------------------------------------


def _char_display_width(ch: str) -> int:
    """Return the display column count for ``ch`` in a typical monospace terminal.

    Heuristic — what actually renders in iTerm2 / Terminal.app / GNOME
    Terminal / Windows Terminal on monospace fonts:

    - **0 cols**: control chars (< 0x20, 0x7F), combining marks
      (Mn/Mc/Me), format chars (Cf — BiDi marks, soft hyphen, ZWJ).
    - **2 cols**: explicit East-Asian Wide (``W``) or Full-width
      (``F``); explicit emoji codepoints in U+1F000+ ranges;
      characters followed by U+FE0F emoji-presentation variation
      selector (caller must combine adjacent chars to detect this —
      not handled at the per-char level here).
    - **1 col**: everything else — including most of the BMP
      "Miscellaneous Symbols" (U+2600..U+26FF) and "Dingbats"
      (U+2700..U+27BF) ranges. ✓ (U+2713), ✗ (U+2717), ⚠ (U+26A0),
      ○ (U+25CB), ◐ (U+25D0), ⊝ (U+229D), • (U+2022) all render as
      **1 column** in standard monospace fonts. An earlier version
      of this helper marked the whole U+2600..U+27BF range as 2 col,
      which broke alignment of every menu containing those glyphs.

    Pure stdlib (``unicodedata.east_asian_width``) — no `wcwidth`
    dependency.
    """
    if ord(ch) < 0x20 or ord(ch) == 0x7F:
        return 0
    if unicodedata.category(ch) in ("Mn", "Mc", "Me", "Cf"):
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 2
    code = ord(ch)
    # Real emoji codepoints (post-BMP) are 2 cols. The BMP Misc
    # Symbols / Dingbats ranges (U+2600..U+27BF) are NOT treated as
    # 2 cols — monospace terminals render them as 1 col, and the
    # CPV status tables use ✓/✗/⚠/○/◐/⊝ for status glyphs and
    # depend on 1-col rendering for alignment.
    if (
        0x1F300 <= code <= 0x1FAFF  # Misc Symbols & Pictographs, Symbols & Pict Ext-A
        or 0x1F000 <= code <= 0x1F2FF  # Mahjong, Domino, Playing Cards
        or 0x1F600 <= code <= 0x1F64F  # Emoticons
    ):
        return 2
    return 1


def display_width(text: str) -> int:
    """Total display columns occupied by ``text`` when rendered in a monospace terminal."""
    return sum(_char_display_width(ch) for ch in text)


def pad(text: str, width: int, align: str = "left") -> str:
    """Pad ``text`` to ``width`` display columns with spaces.

    If ``text`` is already wider than ``width`` (rare for menu cells
    after the caller computed the max width), returns it unchanged
    rather than truncating — truncation in a menu would silently hide
    user-facing information.
    """
    pad_amount = width - display_width(text)
    if pad_amount <= 0:
        return text
    if align == "right":
        return " " * pad_amount + text
    if align == "center":
        left = pad_amount // 2
        right = pad_amount - left
        return " " * left + text + " " * right
    return text + " " * pad_amount


# ---------------------------------------------------------------------------
# Menu mode
# ---------------------------------------------------------------------------


_STATIC_KEYS = frozenset({"0", "A"})  # always keep literal — Exit and Ask/Other


def _validate_menu_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise SystemExit(("menu payload must be a JSON object", 3))
    if "header" not in payload or not isinstance(payload["header"], str):
        raise SystemExit(("menu payload missing 'header' (string)", 3))
    if "rows" not in payload or not isinstance(payload["rows"], list):
        raise SystemExit(("menu payload missing 'rows' (list)", 3))
    for i, row in enumerate(payload["rows"]):
        if not isinstance(row, dict):
            raise SystemExit((f"row {i} must be an object", 3))
        if "key" not in row or not isinstance(row["key"], str):
            raise SystemExit((f"row {i} missing 'key' (string)", 3))
        if "label" not in row or not isinstance(row["label"], str):
            raise SystemExit((f"row {i} missing 'label' (string)", 3))


def render_menu(payload: dict) -> tuple[str, dict[str, str]]:
    """Return (table_string, key_to_action_id_map).

    The map is what the orchestrator uses to translate the user's
    typed key back to the original ``action_id`` (or ``key`` if no
    explicit action_id was provided).
    """
    header_label = payload["header"]
    rows_in = payload["rows"]
    footer = payload.get("footer", "Type a number to choose:")
    renumber = payload.get("renumber", True)

    # 1. Drop disabled rows.
    rows_live = [r for r in rows_in if not r.get("disabled", False)]

    # 2. Build the rendered key for each row.
    #    Static keys (0, A) keep their literal. Numeric keys may be
    #    renumbered into a contiguous 1..N sequence if ``renumber``
    #    is true (default). The original key is preserved as the
    #    action_id when no explicit one is supplied.
    #    The action_id map (rendered_key -> action_id) is returned
    #    so the caller can route user input correctly even after
    #    renumbering.
    action_map: dict[str, str] = {}
    rendered_rows: list[tuple[str, str]] = []  # (rendered_key, label)
    next_num = 1
    for r in rows_live:
        action_id = r.get("action_id", r["key"])
        if r["key"] in _STATIC_KEYS or not renumber:
            rendered_key = r["key"]
        else:
            rendered_key = str(next_num)
            next_num += 1
        action_map[rendered_key] = action_id
        rendered_rows.append((rendered_key, r["label"]))

    # 3. Compute column widths from DISPLAY width.
    key_header = "#"
    width_key = max(display_width(key_header), *(display_width(k) for k, _ in rendered_rows)) if rendered_rows else display_width(key_header)
    width_label = max(display_width(header_label), *(display_width(lbl) for _, lbl in rendered_rows)) if rendered_rows else display_width(header_label)

    # 4. Render the Unicode-bordered table.
    pad_l, pad_r = 1, 1  # one space of breathing room each side of every cell
    bar_key = "━" * (width_key + pad_l + pad_r)
    bar_label = "━" * (width_label + pad_l + pad_r)
    sep_key = "─" * (width_key + pad_l + pad_r)
    sep_label = "─" * (width_label + pad_l + pad_r)

    lines = [
        f"┏{bar_key}┳{bar_label}┓",
        f"┃{' ' * pad_l}{pad(key_header, width_key, 'right')}{' ' * pad_r}"
        f"┃{' ' * pad_l}{pad(header_label, width_label)}{' ' * pad_r}┃",
        f"┡{'━' * (width_key + pad_l + pad_r)}╇{'━' * (width_label + pad_l + pad_r)}┩".replace("━", "━"),
    ]
    for k, label in rendered_rows:
        lines.append(
            f"│{' ' * pad_l}{pad(k, width_key, 'right')}{' ' * pad_r}"
            f"│{' ' * pad_l}{pad(label, width_label)}{' ' * pad_r}│"
        )
    lines.append(f"└{sep_key}┴{sep_label}┘")
    if footer:
        lines.append(footer)
    return "\n".join(lines), action_map


# ---------------------------------------------------------------------------
# Summary mode
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = ("CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING")

# ANSI colour codes for severity tiers. Used only when stdout is a TTY.
_SEVERITY_COLOR = {
    "CRITICAL": "\x1b[91m",  # bright red
    "MAJOR": "\x1b[93m",  # bright yellow
    "MINOR": "\x1b[94m",  # bright blue
    "NIT": "\x1b[96m",  # bright cyan
    "WARNING": "\x1b[95m",  # bright magenta
}
_VERDICT_COLOR = {"VALID": "\x1b[92m", "INVALID": "\x1b[91m"}
_RESET = "\x1b[0m"


def _validate_summary_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise SystemExit(("summary payload must be a JSON object", 3))
    if "counts" not in payload or not isinstance(payload["counts"], dict):
        raise SystemExit(("summary payload missing 'counts' (object)", 3))
    for sev in payload["counts"]:
        if sev.upper() not in _SEVERITY_ORDER:
            raise SystemExit((f"unknown severity in counts: {sev}", 3))
        value = payload["counts"][sev]
        if not isinstance(value, int) or value < 0:
            raise SystemExit((f"count for {sev} must be a non-negative int, got {value!r}", 3))


def render_summary(payload: dict, use_color: bool) -> str:
    """Render a 2-column severity summary table + verdict footer + report-path line."""
    title = payload.get("title", "Findings summary")
    counts = {sev.upper(): payload["counts"].get(sev.lower(), payload["counts"].get(sev.upper(), 0)) for sev in _SEVERITY_ORDER}
    verdict = (payload.get("verdict") or "").upper()
    report_path = payload.get("report_path", "")

    col_sev_header = "Severity"
    col_count_header = "Count"
    width_sev = max(display_width(col_sev_header), *(display_width(s) for s in _SEVERITY_ORDER))
    width_count = max(display_width(col_count_header), *(display_width(str(counts[s])) for s in _SEVERITY_ORDER))

    pad_l, pad_r = 1, 1
    bar_sev = "━" * (width_sev + pad_l + pad_r)
    bar_count = "━" * (width_count + pad_l + pad_r)
    sep_sev = "─" * (width_sev + pad_l + pad_r)
    sep_count = "─" * (width_count + pad_l + pad_r)

    def _color(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if use_color else text

    lines = [
        f"{title}",
        f"┏{bar_sev}┳{bar_count}┓",
        f"┃{' ' * pad_l}{pad(col_sev_header, width_sev)}{' ' * pad_r}"
        f"┃{' ' * pad_l}{pad(col_count_header, width_count, 'right')}{' ' * pad_r}┃",
        f"┡{bar_sev}╇{bar_count}┩",
    ]
    for sev in _SEVERITY_ORDER:
        count = counts[sev]
        sev_cell = pad(sev, width_sev)
        count_cell = pad(str(count), width_count, "right")
        if use_color and count > 0:
            sev_cell = _color(sev, _SEVERITY_COLOR[sev]) + " " * (width_sev - display_width(sev))
            count_cell = _color(pad(str(count), width_count, "right"), _SEVERITY_COLOR[sev])
        lines.append(
            f"│{' ' * pad_l}{sev_cell}{' ' * pad_r}│{' ' * pad_l}{count_cell}{' ' * pad_r}│"
        )
    lines.append(f"└{sep_sev}┴{sep_count}┘")
    if verdict:
        v_color = _VERDICT_COLOR.get(verdict, "")
        lines.append(f"Verdict: {_color(verdict, v_color)}")
    if report_path:
        lines.append(f"Report: {report_path}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Breakdown mode — per-category × per-severity matrix with totals
# ---------------------------------------------------------------------------


def _validate_breakdown_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise SystemExit(("breakdown payload must be a JSON object", 3))
    if "rows" not in payload or not isinstance(payload["rows"], list):
        raise SystemExit(("breakdown payload missing 'rows' (list)", 3))
    columns = payload.get("columns")
    if columns is not None and not isinstance(columns, list):
        raise SystemExit(("'columns' must be a list of strings if present", 3))
    for i, row in enumerate(payload["rows"]):
        if not isinstance(row, dict):
            raise SystemExit((f"breakdown row {i} must be an object", 3))
        if "label" not in row or not isinstance(row["label"], str):
            raise SystemExit((f"breakdown row {i} missing 'label' (string)", 3))
        if "counts" not in row or not isinstance(row["counts"], dict):
            raise SystemExit((f"breakdown row {i} missing 'counts' (object)", 3))


def render_breakdown(payload: dict, use_color: bool) -> str:
    """Render a category × severity matrix table with row totals + totals row.

    Input shape:
        {
          "title": "Findings by recipe",
          "row_header": "Recipe / Category",
          "columns": ["CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING"],  # optional, defaults to _SEVERITY_ORDER
          "rows": [
            {"label": "Schema validation",  "counts": {"CRITICAL": 0, "MAJOR": 0, "MINOR": 2, "NIT": 0, "WARNING": 1}},
            {"label": "D1 Shape detection", "counts": {"CRITICAL": 0, ...}},
            ...
          ],
          "totals_row": true,            # default true
          "verdict": "VALID",            # optional footer line
          "report_path": "/abs/path"     # optional footer line
        }

    Each row's per-severity count is padded to a fixed column width. A
    Total column sums the row's counts. A final TOTAL row sums every
    column. The borders use the same Unicode set as the other modes.
    """
    title = payload.get("title", "Findings breakdown")
    row_header = payload.get("row_header", "Category")
    columns = payload.get("columns") or list(_SEVERITY_ORDER)
    rows = payload["rows"]
    include_total_col = payload.get("total_column", True)
    include_total_row = payload.get("totals_row", True)
    verdict = (payload.get("verdict") or "").upper()
    report_path = payload.get("report_path", "")

    # Normalize column header casing for matching purposes.
    columns_upper = [c.upper() for c in columns]
    total_col_label = "Total"

    # Compute per-row, per-cell normalized counts.
    matrix: list[tuple[str, list[int], int]] = []  # (label, [counts in column order], row_total)
    column_totals = [0] * len(columns)
    grand_total = 0
    for row in rows:
        # Counts dict may use any casing; normalize keys to upper.
        normalized = {k.upper(): int(v) for k, v in row["counts"].items() if isinstance(v, int)}
        row_counts = [normalized.get(col, 0) for col in columns_upper]
        row_total = sum(row_counts)
        matrix.append((row["label"], row_counts, row_total))
        for i, c in enumerate(row_counts):
            column_totals[i] += c
        grand_total += row_total

    # Optionally append a "TOTAL" row at the bottom.
    if include_total_row and matrix:
        matrix.append(("TOTAL", column_totals, grand_total))

    # Compute column widths from display width.
    pad_l, pad_r = 1, 1
    width_label = max(display_width(row_header), *(display_width(lbl) for lbl, _, _ in matrix)) if matrix else display_width(row_header)
    col_widths = []
    for i, col in enumerate(columns):
        col_max = max(display_width(col), *(display_width(str(row_counts[i])) for _, row_counts, _ in matrix)) if matrix else display_width(col)
        col_widths.append(col_max)
    width_total = max(display_width(total_col_label), *(display_width(str(t)) for _, _, t in matrix)) if (include_total_col and matrix) else 0

    # Render.
    def _cell_bar(w: int, ch: str) -> str:
        return ch * (w + pad_l + pad_r)

    def _row_line(label: str, cells: list[str], total_cell: str | None, *, heavy: bool = False) -> str:
        v = "┃" if heavy else "│"
        label_part = f"{v}{' ' * pad_l}{pad(label, width_label)}{' ' * pad_r}"
        cell_parts = "".join(
            f"{v}{' ' * pad_l}{pad(cells[i], col_widths[i], 'right')}{' ' * pad_r}"
            for i in range(len(cells))
        )
        total_part = ""
        if include_total_col and total_cell is not None:
            total_part = f"{v}{' ' * pad_l}{pad(total_cell, width_total, 'right')}{' ' * pad_r}"
        return label_part + cell_parts + total_part + v

    top_segments = [_cell_bar(width_label, "━")] + [_cell_bar(w, "━") for w in col_widths]
    if include_total_col:
        top_segments.append(_cell_bar(width_total, "━"))
    top = "┏" + "┳".join(top_segments) + "┓"
    sep = "┡" + "╇".join(top_segments) + "┩"
    bot_segments = [_cell_bar(width_label, "─")] + [_cell_bar(w, "─") for w in col_widths]
    if include_total_col:
        bot_segments.append(_cell_bar(width_total, "─"))
    bot = "└" + "┴".join(bot_segments) + "┘"

    header_cells: list[str] = [str(c) for c in columns]
    header_total = total_col_label if include_total_col else None
    lines = [title, top, _row_line(row_header, header_cells, header_total, heavy=True), sep]

    last_data_idx = len(matrix) - (1 if include_total_row and matrix else 0) - 1
    for idx, (label, row_counts, row_total) in enumerate(matrix):
        cells = [str(c) for c in row_counts]
        # Color: per-cell tinted by severity column when count > 0 and use_color.
        if use_color:
            for i, count in enumerate(row_counts):
                if count > 0 and columns_upper[i] in _SEVERITY_COLOR:
                    color = _SEVERITY_COLOR[columns_upper[i]]
                    cells[i] = f"{color}{pad(str(count), col_widths[i], 'right')}{_RESET}"
                    # When colored, pre-formatted; pad() in _row_line still pads more but
                    # display_width of ANSI escapes is 0, so it computes correctly.
            if label == "TOTAL":
                label = f"\x1b[1m{label}{_RESET}"  # bold the TOTAL label
        total_cell = str(row_total) if include_total_col else None
        # Insert a thin separator line above the TOTAL row to visually divide it.
        if include_total_row and idx == last_data_idx + 1 and matrix:
            mid_segments = [_cell_bar(width_label, "─")] + [_cell_bar(w, "─") for w in col_widths]
            if include_total_col:
                mid_segments.append(_cell_bar(width_total, "─"))
            lines.append("├" + "┼".join(mid_segments) + "┤")
        lines.append(_row_line(label, cells, total_cell))
    lines.append(bot)

    if verdict:
        v_color = _VERDICT_COLOR.get(verdict, "")
        v_str = f"{v_color}{verdict}{_RESET}" if use_color and v_color else verdict
        lines.append(f"Verdict: {v_str}")
    if report_path:
        lines.append(f"Report: {report_path}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Status-table mode — implemented / missing / buggy build report
# ---------------------------------------------------------------------------

# Glyph + ANSI color per status. ANSI suppressed when use_color=False.
_STATUS_GLYPH = {
    "ok": "✓",
    "implemented": "✓",
    "missing": "✗",
    "buggy": "⚠",
    "partial": "◐",
    "pending": "○",
    "skipped": "⊝",
    "info": "•",
}
_STATUS_COLOR = {
    "ok": "\x1b[92m",
    "implemented": "\x1b[92m",
    "missing": "\x1b[91m",
    "buggy": "\x1b[93m",
    "partial": "\x1b[93m",
    "pending": "\x1b[96m",
    "skipped": "\x1b[90m",
    "info": "\x1b[94m",
}
# Per-status display label (column 2 contents). Always uppercase for scan-ability.
_STATUS_LABEL = {
    "ok": "OK",
    "implemented": "OK",
    "missing": "MISSING",
    "buggy": "BUGGY",
    "partial": "PARTIAL",
    "pending": "PENDING",
    "skipped": "SKIPPED",
    "info": "INFO",
}


def _validate_status_table_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise SystemExit(("status_table payload must be a JSON object", 3))
    if "rows" not in payload or not isinstance(payload["rows"], list):
        raise SystemExit(("status_table payload missing 'rows' (list)", 3))
    for i, row in enumerate(payload["rows"]):
        if not isinstance(row, dict):
            raise SystemExit((f"status_table row {i} must be an object", 3))
        if "label" not in row or not isinstance(row["label"], str):
            raise SystemExit((f"status_table row {i} missing 'label' (string)", 3))
        if "status" not in row or not isinstance(row["status"], str):
            raise SystemExit((f"status_table row {i} missing 'status' (string)", 3))
        if row["status"].lower() not in _STATUS_GLYPH:
            raise SystemExit((f"status_table row {i} has unknown status {row['status']!r}; allowed: {sorted(_STATUS_GLYPH)}", 3))


def render_status_table(payload: dict, use_color: bool) -> str:
    """Render a 3-column status table: Component | Status | Notes.

    Input shape:
        {
          "title": "Plugin build status",          # optional
          "row_header": "Component",                # optional, default "Component"
          "rows": [
            {"label": "plugin.json",   "status": "ok",      "notes": "valid manifest"},
            {"label": "commands/",     "status": "missing", "notes": "directory not created"},
            {"label": "skills/cache",  "status": "buggy",   "notes": "name missing from frontmatter"},
            {"label": "agents/diag",   "status": "partial", "notes": "frontmatter only, body TODO"}
          ],
          "summary_row": true,                      # optional, default true — append "TOTALS"
          "footer": "...optional footer line..."
        }

    Status values (case-insensitive): ok / implemented / missing /
    buggy / partial / pending / skipped / info. Each maps to a glyph +
    coloured label (✓/✗/⚠/◐/○/⊝/•). The "summary_row" footer counts
    each status into a one-line summary like "3 OK, 1 MISSING, 1 BUGGY".
    """
    title = payload.get("title", "Status")
    row_header = payload.get("row_header", "Component")
    rows = payload["rows"]
    include_summary_row = payload.get("summary_row", True)
    footer_line = payload.get("footer", "")

    status_col_header = "Status"
    notes_col_header = "Notes"

    # Normalize statuses + glyph.
    normalized: list[tuple[str, str, str, str]] = []  # (label, status_key, glyph_str, notes)
    counts: dict[str, int] = {}
    for r in rows:
        skey = r["status"].lower()
        glyph = _STATUS_GLYPH[skey]
        status_text = f"{glyph} {_STATUS_LABEL[skey]}"
        notes = str(r.get("notes", ""))
        normalized.append((r["label"], skey, status_text, notes))
        counts[skey] = counts.get(skey, 0) + 1

    # Compute column widths from DISPLAY width.
    pad_l, pad_r = 1, 1
    width_label = max(display_width(row_header), *(display_width(lbl) for lbl, _, _, _ in normalized)) if normalized else display_width(row_header)
    width_status = max(display_width(status_col_header), *(display_width(s) for _, _, s, _ in normalized)) if normalized else display_width(status_col_header)
    width_notes = max(display_width(notes_col_header), *(display_width(n) for _, _, _, n in normalized)) if normalized else display_width(notes_col_header)

    def _bar(w: int, ch: str) -> str:
        return ch * (w + pad_l + pad_r)

    def _color(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if use_color else text

    top = f"┏{_bar(width_label, '━')}┳{_bar(width_status, '━')}┳{_bar(width_notes, '━')}┓"
    sep = f"┡{_bar(width_label, '━')}╇{_bar(width_status, '━')}╇{_bar(width_notes, '━')}┩"
    bot = f"└{_bar(width_label, '─')}┴{_bar(width_status, '─')}┴{_bar(width_notes, '─')}┘"

    lines = [
        title,
        top,
        f"┃{' ' * pad_l}{pad(row_header, width_label)}{' ' * pad_r}"
        f"┃{' ' * pad_l}{pad(status_col_header, width_status)}{' ' * pad_r}"
        f"┃{' ' * pad_l}{pad(notes_col_header, width_notes)}{' ' * pad_r}┃",
        sep,
    ]
    for label, skey, status_text, notes in normalized:
        label_cell = pad(label, width_label)
        status_padded = pad(status_text, width_status)
        notes_cell = pad(notes, width_notes)
        if use_color:
            status_padded = _color(status_padded, _STATUS_COLOR[skey])
        lines.append(
            f"│{' ' * pad_l}{label_cell}{' ' * pad_r}"
            f"│{' ' * pad_l}{status_padded}{' ' * pad_r}"
            f"│{' ' * pad_l}{notes_cell}{' ' * pad_r}│"
        )
    lines.append(bot)

    if include_summary_row and counts:
        # Stable order: ok, implemented, missing, buggy, partial, pending, skipped, info.
        order = ("ok", "implemented", "missing", "buggy", "partial", "pending", "skipped", "info")
        seen: set[str] = set()
        parts: list[str] = []
        for k in order:
            if k in counts and _STATUS_LABEL[k] not in seen:
                seen.add(_STATUS_LABEL[k])
                label_text = _STATUS_LABEL[k]
                if use_color:
                    label_text = _color(label_text, _STATUS_COLOR[k])
                parts.append(f"{counts[k]} {label_text}")
        if parts:
            lines.append("Summary: " + ", ".join(parts))
    if footer_line:
        lines.append(footer_line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _load_payload(arg: str) -> dict:
    if arg == "-":
        raw = sys.stdin.read()
    else:
        raw = arg
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"format_menu: invalid JSON — {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print("format_menu: payload must be a JSON object", file=sys.stderr)
        sys.exit(2)
    return data


def main(argv: list[str]) -> int:
    valid_modes = ("menu", "summary", "breakdown", "status_table")
    if len(argv) < 3 or argv[1] not in valid_modes:
        print(f"usage: format_menu.py {{{'|'.join(valid_modes)}}} <json-or-->", file=sys.stderr)
        return 2
    mode = argv[1]
    payload = _load_payload(argv[2])
    import os  # noqa: PLC0415

    try:
        if mode == "menu":
            _validate_menu_payload(payload)
            table, action_map = render_menu(payload)
            print(table)
            print(json.dumps({"action_map": action_map}), file=sys.stderr)
        else:
            use_color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
            if mode == "summary":
                _validate_summary_payload(payload)
                print(render_summary(payload, use_color=use_color))
            elif mode == "breakdown":
                _validate_breakdown_payload(payload)
                print(render_breakdown(payload, use_color=use_color))
            else:  # status_table
                _validate_status_table_payload(payload)
                print(render_status_table(payload, use_color=use_color))
    except SystemExit as exc:
        if isinstance(exc.code, tuple) and len(exc.code) == 2:
            msg, code = exc.code
            print(f"format_menu: {msg}", file=sys.stderr)
            return int(code)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
