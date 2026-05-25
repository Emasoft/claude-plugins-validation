#!/usr/bin/env python3
"""Regression lock: --no-color / non-TTY must suppress ALL ANSI (v2.107.1).

Bug (pre-v2.107.1): ``set_color_enabled(False)`` only affected ``colorize()``
and ``format_result()``; the ~100 direct ``COLORS['LEVEL']`` reads scattered
across the validators (summary headers, ``[REPO LINT]`` banners, per-validator
output) still emitted raw ``\\033[`` codes. So
``validate_plugin.py . --no-color > report.txt`` produced a report full of ANSI
escapes — which (a) was unreadable as a release asset and (b) tripped the
encoding control-character check when the Release workflow scanned its own
captured ``validation-report.txt`` (MINOR ``raw control characters (0x1b)`` →
exit 3 → every release's Release job failed).

The fix makes ``COLORS`` a flag-aware ``Mapping`` (``_ColorMap``) so every read
honors the per-process ``_COLOR_ENABLED`` flag — no call-site changes, no raw
table mutation (pytest-xdist-safe). These tests are TWO-SIDED: they pin both
that disabling truly blanks every read AND that enabling still yields the real
ANSI codes (interactive color was not broken).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_validation_common as cvc  # noqa: E402

_ALL_KEYS = (
    "CRITICAL",
    "MAJOR",
    "MINOR",
    "NIT",
    "WARNING",
    "INFO",
    "PASSED",
    "RESET",
    "BOLD",
    "DIM",
)


@pytest.fixture(autouse=True)
def _restore_color_flag() -> Iterator[None]:
    """Save/restore the process-global flag so a test that disables color
    cannot pollute sibling tests sharing the same xdist worker process."""
    saved = cvc._COLOR_ENABLED
    try:
        yield
    finally:
        cvc.set_color_enabled(saved)


class TestColorsReadsRespectFlag:
    """Every direct COLORS[...] read must honor _COLOR_ENABLED — the bug."""

    def test_disabled_direct_reads_return_empty(self) -> None:
        """Disabled: every direct COLORS read is '' (the leak the fix closes)."""
        cvc.set_color_enabled(False)
        for key in _ALL_KEYS:
            assert cvc.COLORS[key] == "", (
                f"COLORS[{key!r}] must be '' when colors disabled "
                "(direct reads used to leak ANSI under --no-color)"
            )

    def test_enabled_direct_reads_return_ansi(self) -> None:
        """Enabled: direct COLORS reads still return the real ANSI codes."""
        cvc.set_color_enabled(True)
        assert cvc.COLORS["CRITICAL"] == "\033[91m"
        assert cvc.COLORS["RESET"] == "\033[0m"
        assert cvc.COLORS["BOLD"] == "\033[1m"

    def test_disabled_then_enabled_round_trip(self) -> None:
        """Toggling the flag flips every read live (no captured-at-import value)."""
        cvc.set_color_enabled(False)
        assert cvc.COLORS["CRITICAL"] == ""
        cvc.set_color_enabled(True)
        assert cvc.COLORS["CRITICAL"] == "\033[91m"


class TestColorsMappingProtocol:
    """COLORS must stay dict-compatible for the ~100 existing call sites."""

    def test_get_with_default(self) -> None:
        """.get() returns the code for known keys, the default for unknown."""
        cvc.set_color_enabled(True)
        assert cvc.COLORS.get("CRITICAL", "x") == "\033[91m"
        assert cvc.COLORS.get("NOPE", "fallback") == "fallback"

    def test_membership(self) -> None:
        """`in` works via the Mapping ABC (used by completeness tests)."""
        assert "CRITICAL" in cvc.COLORS
        assert "MAJOR_DARK" not in cvc.COLORS

    def test_unknown_key_raises_keyerror(self) -> None:
        """Subscripting an unknown key raises KeyError, like the old dict."""
        cvc.set_color_enabled(True)
        with pytest.raises(KeyError):
            _ = cvc.COLORS["NONEXISTENT_LEVEL"]

    def test_iter_and_len(self) -> None:
        """iter()/len() expose exactly the 10 raw color entries."""
        keys = set(cvc.COLORS)
        assert {"CRITICAL", "RESET", "BOLD"} <= keys
        assert len(cvc.COLORS) == len(_ALL_KEYS)


class TestColorizeHelpersRespectFlag:
    """colorize() / format_result() (the helpers) also honor the flag."""

    def test_colorize_disabled_is_plain(self) -> None:
        """colorize() returns the bare text when colors are disabled."""
        cvc.set_color_enabled(False)
        assert cvc.colorize("msg", "CRITICAL") == "msg"

    def test_colorize_enabled_wraps(self) -> None:
        """colorize() wraps text in the level color + RESET when enabled."""
        cvc.set_color_enabled(True)
        assert cvc.colorize("msg", "CRITICAL") == "\033[91mmsg\033[0m"
