#!/usr/bin/env python3
"""Parallel-scan integration tests for
``validate_skill_comprehensive.scan_one_skill``.

Task #384 — Agent A3 (skills).

Same contract as ``test_validate_skill_parallelism.py``, but for the
comprehensive validator. Because comprehensive runs 190+ checks per
skill, fixture skills here are kept minimal (no scripts/, no references/)
so the suite stays under a second even with a real ProcessPoolExecutor.

Validates that:

1. ``scan_one_skill`` is a top-level pickleable callable accepting one
   positional ``Path`` arg (keyword args have defaults — the parallel
   harness only passes the positional).
2. parallel vs serial parity: same dirs in, same findings out, same
   order.
3. Findings are plain dicts (pickleable across process boundaries).
4. Per-skill isolation: a broken skill produces a CRITICAL finding
   inside the normal return value, NOT a ScanResult.error.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts/ to sys.path the same way the other tests do.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_parallel_runner import parallel_scan  # noqa: E402
from validate_skill_comprehensive import scan_one_skill  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers — generate skill dirs the comprehensive validator accepts.
# ---------------------------------------------------------------------------


def _write_skill(skill_dir: Path, name: str) -> None:
    """Create a minimal skill directory the comprehensive validator can scan.

    Includes the "Use when ..." trigger phrase so strict-mode-friendly skills
    don't accumulate description-quality MAJORs that would dominate the
    finding list and slow the parity test pointlessly.
    """
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f"name: {name}\n"
        "description: A test skill. Use when validating parallel-scan parity.\n"
        "---\n"
        "# Test Skill\n\n"
        "## Overview\n\n"
        "This is a body for the test skill.\n\n"
        "## Instructions\n\n"
        "Run the parallel scan parity test.\n"
    )
    (skill_dir / "SKILL.md").write_text(content)


def _write_broken_skill(skill_dir: Path) -> None:
    """Create a directory WITHOUT SKILL.md so the validator emits CRITICAL."""
    skill_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScanOneSkillIsPickleable:
    """``scan_one_skill`` must be importable at module scope and return
    pickleable primitives so ProcessPoolExecutor can shuttle the data."""

    def test_scan_one_skill_is_callable_at_module_scope(self):
        """The function must be importable from the comprehensive validator."""
        import validate_skill_comprehensive

        assert hasattr(validate_skill_comprehensive, "scan_one_skill")
        assert callable(validate_skill_comprehensive.scan_one_skill)

    def test_scan_one_skill_returns_list_of_dicts(self, tmp_path):
        """Per-skill output must be ``list[dict]`` — pickleable primitives."""
        skill_dir = tmp_path / "demo-skill"
        _write_skill(skill_dir, "demo-skill")

        findings = scan_one_skill(skill_dir)
        assert isinstance(findings, list)
        assert all(isinstance(f, dict) for f in findings)
        for f in findings:
            assert "level" in f
            assert "message" in f


class TestParallelVsSerialParity:
    """Running scan_one_skill via parallel_scan produces IDENTICAL
    findings (same content, same order) compared to serial iteration.
    Core regression contract for task #384."""

    def _build_fixture(self, tmp_path: Path, n: int = 4) -> list[Path]:
        """Create N distinct valid skill directories and return their paths."""
        dirs: list[Path] = []
        for i in range(n):
            d = tmp_path / f"skill-{i:03d}"
            _write_skill(d, f"skill-{i:03d}")
            dirs.append(d)
        return dirs

    def test_parallel_matches_serial_findings(self, tmp_path):
        """For a multi-skill fixture, parallel and serial scan_one_skill
        produce the same per-skill findings in the same order."""
        skill_dirs = self._build_fixture(tmp_path, n=4)

        serial = [scan_one_skill(d) for d in skill_dirs]
        parallel = parallel_scan(skill_dirs, scan_one_skill, n_workers=2)

        assert len(parallel) == len(skill_dirs)
        for i, (result, expected) in enumerate(zip(parallel, serial)):
            assert result.error is None, (
                f"unexpected worker error on skill #{i}: {result.error}"
            )
            assert result.file_path == skill_dirs[i], (
                f"order broken at slot #{i}: got {result.file_path}, "
                f"expected {skill_dirs[i]}"
            )
            assert result.findings == expected, (
                f"parity break on skill #{i}: parallel={result.findings} "
                f"serial={expected}"
            )

    def test_parallel_preserves_input_order(self, tmp_path):
        """Input order in == output order out, regardless of worker
        completion order. Each skill's name is embedded in its findings
        as 'name' field present: skill-NNN."""
        skill_dirs = self._build_fixture(tmp_path, n=6)
        results = parallel_scan(skill_dirs, scan_one_skill, n_workers=3)

        for i, r in enumerate(results):
            assert r.file_path == skill_dirs[i]
            name_findings = [
                f for f in r.findings if "'name' field present" in f.get("message", "")
            ]
            assert name_findings, f"no name finding on slot #{i}: {r.findings}"
            assert f"skill-{i:03d}" in name_findings[0]["message"]


class TestBrokenSkillIsolation:
    """A broken skill (missing SKILL.md) emits a CRITICAL through the
    normal report path — the worker does NOT raise, so ScanResult.error
    stays None. The neighboring good skills' results are unaffected."""

    def test_missing_skill_md_emits_critical_not_error(self, tmp_path):
        """Missing SKILL.md is reported as CRITICAL in findings, not as
        a ScanResult.error. Sibling skills in the batch are unaffected."""
        good = tmp_path / "good-skill"
        _write_skill(good, "good-skill")
        broken = tmp_path / "broken-skill"
        _write_broken_skill(broken)

        results = parallel_scan([good, broken], scan_one_skill)
        assert len(results) == 2

        # Slot 0: clean skill, no critical.
        assert results[0].error is None
        crit_0 = [f for f in results[0].findings if f["level"] == "CRITICAL"]
        assert not crit_0

        # Slot 1: broken skill — comprehensive emits CRITICAL "SKILL.md not found"
        # via the normal report path; ScanResult.error stays None.
        assert results[1].error is None
        crit_1 = [f for f in results[1].findings if f["level"] == "CRITICAL"]
        assert crit_1
        assert any("SKILL.md not found" in f["message"] for f in crit_1)


class TestEmptyInputShortCircuit:
    """parallel_scan([]) is a no-op that doesn't spawn the pool. Verify
    scan_one_skill is wired correctly in that path."""

    def test_empty_skill_list_returns_empty(self):
        """No skills in -> no findings out, no exception."""
        assert parallel_scan([], scan_one_skill) == []
