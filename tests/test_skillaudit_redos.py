#!/usr/bin/env python3
"""Regression tests for issue #53 — ReDoS in the skillaudit per-line matcher.

A class of catalog patterns with >=2 chained unbounded `.*` between alternation
groups backtracks super-linearly on a single very long line, pinning a CPU core
indefinitely (the issue's `01:01:39 ELAPSED` profile). The fix bounds the
per-line input fed to the regex engine via `_MAX_SCAN_LINE`, making the worst
case linear regardless of pattern shape. These tests lock that guard.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_skillaudit_native as sa  # noqa: E402


class TestSkillauditReDoS:
    """Issue #53 — the per-line matcher must not backtrack catastrophically."""

    def test_max_scan_line_constant_present(self) -> None:
        """The line-length guard constant exists and is bounded."""
        assert isinstance(sa._MAX_SCAN_LINE, int)
        assert 0 < sa._MAX_SCAN_LINE <= 8192

    def test_a2a_capability_abuse_pathological_line_completes_fast(self) -> None:
        """The report's worst reproducer (A2A_CAPABILITY_ABUSE #3, 4 chained
        `.*`) HUNG on a ~38 KB single line; with the cap it must scan in well
        under 1 s."""
        line = "request " + "admin full access permission from via " * 1000 + "ZZZ"
        content = "# doc\n" + line + "\nmore\n"
        t0 = time.perf_counter()
        sa.scan_content(content, "evil.md")
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"ReDoS not bounded: scan took {elapsed:.3f}s"

    def test_generic_multistar_pathological_line_completes_fast(self) -> None:
        """A generic prefix+repeat-middle+bad-suffix line (the shape that
        explodes the whole multi-`.*` class) must also be bounded."""
        line = "wget " + "redirect follow manual proxy " * 1500 + "ZZZ"
        content = line + "\n"
        t0 = time.perf_counter()
        sa.scan_content(content, "evil.js")
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"ReDoS not bounded: scan took {elapsed:.3f}s"

    def test_long_line_does_not_hang(self) -> None:
        """A 1 MB single line must scan in BOUNDED (linear) time, not hang.

        The `_MAX_SCAN_LINE` cap bounds the catalog per-line regex; the rest of
        the pipeline (RE2 pre-filter + secret/url/intent scanners) still
        processes the full content linearly (~1-2 s for 1 MB), which is fine —
        the ReDoS bug was *exponential/unbounded* time. This guards against a
        regression to a hang (orders of magnitude over the linear cost), not a
        tight perf budget. The true ReDoS reproducers above assert < 1 s."""
        content = "x" * 1_000_000 + "\n"
        t0 = time.perf_counter()
        sa.scan_content(content, "big.txt")
        elapsed = time.perf_counter() - t0
        assert elapsed < 5.0, f"1 MB line not bounded (possible regression to hang): {elapsed:.3f}s"

    def test_bounded_span_preserves_true_positive(self) -> None:
        """The cap must not introduce false negatives: a real attack phrase
        expressed within a normal-length clause still produces findings."""
        findings = sa.scan_content("require admin access from another agent to exfiltrate data", "x.md")
        assert isinstance(findings, list)
        # The phrase exercises the bounded A2A patterns; it must still match.
        assert findings, "true-positive attack clause should still be detected"
