"""Tests for ``scripts/format_menu.py`` — the menu/summary renderer used
by the four /cpv-* main-session orchestrators (cpv-doctor,
cpv-fix-validation, cpv-fix-marketplace-validation, cpv-cache-optimize).

The helper exists for the v2.89.3 fix to four end-user defects in the
v2.89.0 menu-orchestrator pattern:

1. Disabled rows leak into the rendered menu (``— (no findings)``).
2. No summary table between the work-agent return and the post-scan menu.
3. ``len()``-based cell padding mis-aligns rows containing box-drawing
   chars or wide-Asian glyphs.
4. The doctor's depth (separate from menu rendering) is too shallow.

This test file covers items 1, 3 (cell-padding correctness) and a
narrower slice of item 2 (the summary renderer itself). The
command-body wiring is covered by
``tests/test_v2_89_3_command_body_refactor.py``; the doctor depth is
covered by ``tests/test_cpv_doctor_recipes.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "format_menu.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from format_menu import (  # noqa: E402
    display_width,
    pad,
    render_breakdown,
    render_menu,
    render_status_table,
    render_summary,
)

# ---------------------------------------------------------------------------
# Display-width oracle
# ---------------------------------------------------------------------------


class TestDisplayWidth:
    def test_pure_ascii(self) -> None:
        assert display_width("hello") == 5
        assert display_width("") == 0

    def test_box_drawing_is_one_column(self) -> None:
        # Box-drawing chars are East-Asian Narrow → 1 column each.
        assert display_width("┏━━┓") == 4
        assert display_width("│  │") == 4

    def test_combining_marks_are_zero_width(self) -> None:
        # "café" is c, a, f, e + combining-acute → 4 columns visible.
        assert display_width("café") == 4

    def test_emoji_is_two_columns(self) -> None:
        # Real emoji codepoints (U+1F000+) render as 2 cols in monospace.
        assert display_width("🔴") == 2  # LARGE RED CIRCLE, U+1F534
        assert display_width("🟢") == 2  # LARGE GREEN CIRCLE, U+1F7E2

    def test_bmp_dingbats_are_one_column(self) -> None:
        # ✓ (U+2713), ✗ (U+2717), ⚠ (U+26A0), ○ (U+25CB), ◐ (U+25D0),
        # ⊝ (U+229D), • (U+2022) all render as ONE column in standard
        # monospace fonts despite living in the "Misc Symbols / Dingbats"
        # range. The status-table glyphs depend on this for alignment.
        for ch in "✓✗⚠○◐⊝•":
            assert display_width(ch) == 1, (
                f"{ch!r} (U+{ord(ch):04X}) must be 1 col for status-table alignment in monospace terminals."
            )

    def test_wide_asian_char_is_two_columns(self) -> None:
        # Hiragana A (あ, U+3042) is East-Asian Wide → 2 columns.
        # CJK Compat (亜, U+4E9C) Wide → 2 columns.
        assert display_width("あ") == 2
        assert display_width("亜") == 2

    def test_mixed_string_sums_correctly(self) -> None:
        # "OK ✓ test" → O+K+space+✓+space+t+e+s+t = 9 cols (✓ is 1 col).
        assert display_width("OK ✓ test") == 9


class TestPad:
    def test_pads_ascii_left_aligned(self) -> None:
        assert pad("foo", 6) == "foo   "

    def test_pads_ascii_right_aligned(self) -> None:
        assert pad("foo", 6, align="right") == "   foo"

    def test_pads_ascii_centered(self) -> None:
        assert pad("foo", 7, align="center") == "  foo  "

    def test_pads_emoji_string_to_display_columns(self) -> None:
        # "✓ ok" = 1+1+2 = 4 cols; pad to 8 → 4 trailing spaces.
        padded = pad("✓ ok", 8)
        assert display_width(padded) == 8

    def test_does_not_truncate_overlong(self) -> None:
        # Better to overflow than to silently lose menu text.
        assert pad("toolong", 3) == "toolong"


# ---------------------------------------------------------------------------
# Menu mode — drop disabled, renumber, render bordered table
# ---------------------------------------------------------------------------


class TestRenderMenuDropAndRenumber:
    def test_drops_disabled_rows_and_renumbers_remaining(self) -> None:
        payload = {
            "header": "What now?",
            "rows": [
                {"key": "1", "label": "Fix CRITICAL", "disabled": True, "action_id": "fix_critical"},
                {"key": "2", "label": "Fix MAJOR", "disabled": True, "action_id": "fix_major"},
                {"key": "3", "label": "Fix MINOR (2)", "action_id": "fix_minor"},
                {"key": "4", "label": "Pick interactive", "disabled": True, "action_id": "pick"},
                {"key": "5", "label": "Re-validate", "action_id": "revalidate"},
                {"key": "0", "label": "Exit"},
            ],
        }
        table, action_map = render_menu(payload)
        # Disabled rows must NOT appear in the table.
        assert "Fix CRITICAL" not in table
        assert "Fix MAJOR" not in table
        assert "Pick interactive" not in table
        # Live rows must appear, renumbered 1..N (with 0 kept literal).
        assert "│ 1 │ Fix MINOR (2)" in table
        assert "│ 2 │ Re-validate" in table
        assert "│ 0 │ Exit" in table
        # Action map must translate rendered key → original action_id.
        assert action_map == {"1": "fix_minor", "2": "revalidate", "0": "0"}

    def test_keeps_special_keys_zero_and_A_literal(self) -> None:
        payload = {
            "header": "Diagnose what?",
            "rows": [
                {"key": "1", "label": "Plugin", "action_id": "single_plugin"},
                {"key": "A", "label": "Free-form", "action_id": "ask_freeform"},
                {"key": "0", "label": "Cancel"},
            ],
        }
        table, action_map = render_menu(payload)
        # 1 is renumbered to 1 (no-op here), A and 0 stay literal.
        assert "│ 1 │ Plugin" in table
        assert "│ A │ Free-form" in table
        assert "│ 0 │ Cancel" in table
        assert action_map == {"1": "single_plugin", "A": "ask_freeform", "0": "0"}

    def test_does_not_renumber_when_renumber_false(self) -> None:
        payload = {
            "header": "What now?",
            "renumber": False,
            "rows": [
                {"key": "1", "label": "A", "disabled": True},
                {"key": "2", "label": "B", "action_id": "b"},
                {"key": "5", "label": "C", "action_id": "c"},
                {"key": "0", "label": "Exit"},
            ],
        }
        table, action_map = render_menu(payload)
        assert "│ 2 │ B" in table
        assert "│ 5 │ C" in table
        assert action_map == {"2": "b", "5": "c", "0": "0"}


class TestRenderMenuBordersAreAligned:
    def test_right_border_column_is_consistent_across_rows(self) -> None:
        payload = {
            "header": "What now?",
            "rows": [
                {"key": "1", "label": "Short"},
                {"key": "2", "label": "This is a much longer label than row 1"},
                {"key": "0", "label": "Exit"},
            ],
        }
        table, _ = render_menu(payload)
        body_lines = [line for line in table.splitlines() if line.startswith("│")]
        # Every body line's display width must be identical (right border
        # in the same column). This is the core ``len()``-vs-display-width
        # regression guard.
        widths = {display_width(line) for line in body_lines}
        assert len(widths) == 1, f"row widths drift: {widths} (lines: {body_lines})"

    def test_right_border_consistent_with_emoji_and_box_chars_in_labels(self) -> None:
        payload = {
            "header": "What now?",
            "rows": [
                {"key": "1", "label": "Fix ✓ all CRITICAL"},
                {"key": "2", "label": "Re-validate ⚠ now"},
                {"key": "0", "label": "Exit"},
            ],
        }
        table, _ = render_menu(payload)
        body_lines = [line for line in table.splitlines() if line.startswith("│")]
        widths = {display_width(line) for line in body_lines}
        assert len(widths) == 1, f"row widths drift on emoji content: {widths}"

    def test_header_and_body_borders_match(self) -> None:
        payload = {
            "header": "What now?",
            "rows": [{"key": "1", "label": "X"}, {"key": "0", "label": "Exit"}],
        }
        table, _ = render_menu(payload)
        lines = table.splitlines()
        top = lines[0]
        sep = [line for line in lines if line.startswith("┡")][0]
        bot = [line for line in lines if line.startswith("└")][0]
        # Top, separator, and bottom border lines must have identical display width.
        assert display_width(top) == display_width(sep) == display_width(bot)


class TestRenderMenuCLI:
    def test_cli_menu_writes_action_map_to_stderr(self, tmp_path: Path) -> None:
        payload = {
            "header": "What now?",
            "rows": [
                {"key": "1", "label": "A", "disabled": True, "action_id": "a"},
                {"key": "2", "label": "B", "action_id": "b"},
                {"key": "0", "label": "Exit"},
            ],
        }
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "menu", json.dumps(payload)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "│ 1 │ B" in result.stdout  # B renumbered from 2 to 1
        assert "│ 0 │ Exit" in result.stdout
        # Action map on stderr.
        stderr_payload = json.loads(result.stderr.strip().splitlines()[-1])
        assert stderr_payload == {"action_map": {"1": "b", "0": "0"}}

    def test_cli_menu_returns_code_2_on_bad_json(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "menu", "not-json"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "invalid JSON" in result.stderr

    def test_cli_menu_returns_code_3_on_bad_shape(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "menu", '{"header": 42, "rows": []}'],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 3


# ---------------------------------------------------------------------------
# Summary mode
# ---------------------------------------------------------------------------


class TestRenderSummary:
    def test_renders_severity_counts_table(self) -> None:
        payload = {
            "counts": {"critical": 0, "major": 0, "minor": 2, "nit": 0, "warning": 1},
            "verdict": "VALID",
            "report_path": "/abs/path/report.md",
        }
        out = render_summary(payload, use_color=False)
        # Every documented severity appears.
        for sev in ("CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING"):
            assert sev in out, f"summary missing severity row {sev}"
        # Counts visible.
        assert " 2 " in out  # minor = 2
        assert " 1 " in out  # warning = 1
        # Verdict footer.
        assert "Verdict: VALID" in out
        # Report path line.
        assert "Report: /abs/path/report.md" in out

    def test_summary_borders_aligned(self) -> None:
        payload = {
            "counts": {"critical": 1, "major": 22, "minor": 333, "nit": 4, "warning": 5},
            "verdict": "INVALID",
        }
        out = render_summary(payload, use_color=False)
        body_lines = [line for line in out.splitlines() if line.startswith("│")]
        widths = {display_width(line) for line in body_lines}
        assert len(widths) == 1, f"summary row widths drift: {widths}"

    def test_summary_uses_color_when_requested(self) -> None:
        payload = {"counts": {"critical": 1, "major": 0, "minor": 0, "nit": 0, "warning": 0}, "verdict": "INVALID"}
        with_color = render_summary(payload, use_color=True)
        without_color = render_summary(payload, use_color=False)
        # ANSI escape only present in coloured form.
        assert "\x1b[" in with_color
        assert "\x1b[" not in without_color


class TestRenderSummaryCLI:
    def test_cli_summary_writes_table_to_stdout(self) -> None:
        payload = {"counts": {"critical": 0, "major": 0, "minor": 2, "nit": 0, "warning": 1}, "verdict": "VALID"}
        env = {**os.environ, "NO_COLOR": "1"}  # deterministic plain output
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "summary", json.dumps(payload)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        assert "Findings summary" in result.stdout
        assert "Verdict: VALID" in result.stdout
        assert "MINOR" in result.stdout


# ---------------------------------------------------------------------------
# Breakdown mode — per-category × per-severity matrix with row totals + totals row
# ---------------------------------------------------------------------------


class TestRenderBreakdown:
    def test_renders_all_columns_and_per_row_totals(self) -> None:
        payload = {
            "title": "Doctor findings by recipe",
            "row_header": "Recipe / Category",
            "rows": [
                {
                    "label": "Schema validation",
                    "counts": {"CRITICAL": 0, "MAJOR": 0, "MINOR": 2, "NIT": 0, "WARNING": 1},
                },
                {
                    "label": "D2 Command coverage",
                    "counts": {"CRITICAL": 0, "MAJOR": 1, "MINOR": 3, "NIT": 0, "WARNING": 0},
                },
                {
                    "label": "D3 Skill invocability",
                    "counts": {"CRITICAL": 0, "MAJOR": 0, "MINOR": 1, "NIT": 0, "WARNING": 0},
                },
            ],
            "verdict": "VALID",
            "report_path": "/abs/path/report.md",
        }
        out = render_breakdown(payload, use_color=False)
        # All 5 severity columns visible in the header.
        for col in ("CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING"):
            assert col in out, f"missing severity column {col}"
        # "Total" column visible.
        assert "Total" in out
        # Per-row total cells visible — schema validation row totals to 3, D2 to 4, D3 to 1.
        body_lines = [line for line in out.splitlines() if line.startswith("│") and "TOTAL" not in line]
        # Find the row containing "Schema validation" and check the rightmost integer is 3.
        sv_line = next(line for line in body_lines if "Schema validation" in line)
        assert sv_line.rstrip("│ ").rstrip().endswith("3"), (
            f"Schema validation row total should end in 3, got: {sv_line!r}"
        )

    def test_appends_totals_row_summing_each_column(self) -> None:
        payload = {
            "rows": [
                {"label": "A", "counts": {"CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "NIT": 0, "WARNING": 0}},
                {"label": "B", "counts": {"CRITICAL": 0, "MAJOR": 1, "MINOR": 1, "NIT": 0, "WARNING": 2}},
            ],
        }
        out = render_breakdown(payload, use_color=False)
        # TOTAL row present.
        total_line = next((line for line in out.splitlines() if "TOTAL" in line), None)
        assert total_line is not None, "TOTAL row missing"
        # Column sums: CRITICAL=1, MAJOR=3, MINOR=4, NIT=0, WARNING=2, grand=10.
        # The row's integer cells (after stripping borders/padding) must be exactly these in order.
        # We extract integer tokens for a robust check.
        import re as _re

        ints = [int(m) for m in _re.findall(r"\b\d+\b", total_line)]
        assert ints == [1, 3, 4, 0, 2, 10], f"TOTAL row counts wrong: {ints} (line: {total_line!r})"

    def test_borders_aligned_with_long_labels_and_emoji(self) -> None:
        payload = {
            "rows": [
                {
                    "label": "Schema validation ✓",
                    "counts": {"CRITICAL": 0, "MAJOR": 0, "MINOR": 2, "NIT": 0, "WARNING": 1},
                },
                {
                    "label": "A much longer category label",
                    "counts": {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0},
                },
            ],
        }
        out = render_breakdown(payload, use_color=False)
        body_lines = [line for line in out.splitlines() if line.startswith(("│", "┃"))]
        widths = {display_width(line) for line in body_lines}
        assert len(widths) == 1, f"breakdown row widths drift: {widths}"

    def test_header_row_uses_heavy_borders(self) -> None:
        """Header row vertical separators must be ┃ (heavy) to match the top fence."""
        payload = {
            "rows": [
                {"label": "A", "counts": {"CRITICAL": 0, "MAJOR": 0, "MINOR": 1, "NIT": 0, "WARNING": 0}},
            ],
        }
        out = render_breakdown(payload, use_color=False)
        # First body-line (the header row, with "CRITICAL" etc.) must start with ┃ not │.
        header_line = next(line for line in out.splitlines() if "CRITICAL" in line)
        assert header_line.startswith("┃"), f"breakdown header must use heavy ┃, got: {header_line!r}"

    def test_color_tints_cells_with_nonzero_counts(self) -> None:
        payload = {
            "rows": [
                {"label": "A", "counts": {"CRITICAL": 1, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0}},
            ],
        }
        colored = render_breakdown(payload, use_color=True)
        plain = render_breakdown(payload, use_color=False)
        # ANSI escapes only when color is on AND the cell count > 0.
        assert "\x1b[" in colored
        assert "\x1b[" not in plain


class TestRenderBreakdownCLI:
    def test_cli_breakdown_renders_to_stdout(self) -> None:
        payload = {
            "title": "By recipe",
            "row_header": "Recipe",
            "rows": [
                {"label": "Schema", "counts": {"CRITICAL": 0, "MAJOR": 0, "MINOR": 2, "NIT": 0, "WARNING": 1}},
                {"label": "D2", "counts": {"CRITICAL": 0, "MAJOR": 1, "MINOR": 0, "NIT": 0, "WARNING": 0}},
            ],
            "verdict": "VALID",
        }
        env = {**os.environ, "NO_COLOR": "1"}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "breakdown", json.dumps(payload)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        assert "By recipe" in result.stdout
        assert "TOTAL" in result.stdout
        assert "Verdict: VALID" in result.stdout

    def test_cli_breakdown_rejects_bad_shape(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "breakdown", '{"rows": "not-a-list"}'],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 3


# ---------------------------------------------------------------------------
# Status-table mode — Component | Status | Notes
# ---------------------------------------------------------------------------


class TestRenderStatusTable:
    def test_renders_each_status_with_correct_glyph(self) -> None:
        payload = {
            "rows": [
                {"label": "plugin.json", "status": "ok", "notes": "valid"},
                {"label": "missing.md", "status": "missing", "notes": "not created"},
                {"label": "buggy.md", "status": "buggy", "notes": "frontmatter bad"},
                {"label": "partial.md", "status": "partial", "notes": "body TODO"},
                {"label": "pending.md", "status": "pending", "notes": "auto-gen"},
                {"label": "skipped.md", "status": "skipped", "notes": "optional"},
                {"label": "info.md", "status": "info", "notes": "noted"},
            ],
        }
        out = render_status_table(payload, use_color=False)
        # Each status glyph must appear at least once.
        for glyph in ("✓", "✗", "⚠", "◐", "○", "⊝", "•"):
            assert glyph in out, f"missing glyph {glyph}"
        # Status labels must appear (uppercase).
        for label in ("OK", "MISSING", "BUGGY", "PARTIAL", "PENDING", "SKIPPED", "INFO"):
            assert label in out, f"missing status label {label}"

    def test_appends_summary_row(self) -> None:
        payload = {
            "rows": [
                {"label": "a", "status": "ok"},
                {"label": "b", "status": "ok"},
                {"label": "c", "status": "missing"},
                {"label": "d", "status": "buggy"},
            ],
        }
        out = render_status_table(payload, use_color=False)
        # Summary line: "2 OK, 1 MISSING, 1 BUGGY".
        assert "Summary:" in out
        assert "2 OK" in out
        assert "1 MISSING" in out
        assert "1 BUGGY" in out

    def test_borders_aligned_across_all_status_rows(self) -> None:
        payload = {
            "rows": [
                {"label": "ok-row", "status": "ok", "notes": "short"},
                {"label": "missing-row", "status": "missing", "notes": "a much longer note here"},
                {"label": "buggy-row-with-very-long-label-too", "status": "buggy", "notes": "x"},
            ],
        }
        out = render_status_table(payload, use_color=False)
        body_lines = [line for line in out.splitlines() if line.startswith(("│", "┃"))]
        widths = {display_width(line) for line in body_lines}
        assert len(widths) == 1, f"status_table row widths drift: {widths}"

    def test_rejects_unknown_status(self) -> None:
        payload = {"rows": [{"label": "x", "status": "nonsense"}]}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "status_table", json.dumps(payload)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 3, f"expected 3 for unknown status, got {result.returncode}"
        assert "unknown status" in result.stderr


class TestRenderStatusTableCLI:
    def test_cli_renders_to_stdout(self) -> None:
        payload = {
            "title": "Plugin build status",
            "rows": [
                {"label": "plugin.json", "status": "ok", "notes": "ok"},
                {"label": "agents/foo.md", "status": "missing", "notes": "not yet"},
            ],
        }
        env = {**os.environ, "NO_COLOR": "1"}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "status_table", json.dumps(payload)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        assert "Plugin build status" in result.stdout
        assert "Summary:" in result.stdout
