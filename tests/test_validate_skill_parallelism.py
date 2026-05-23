#!/usr/bin/env python3
"""Parallel-scan integration tests for ``validate_skill.scan_one_skill``.

Task #384 — Agent A3 (skills).

Validates that:

1. ``scan_one_skill`` is a top-level pickleable callable.
2. Running it serially over N skill directories produces the SAME findings,
   in the SAME order, as feeding the same dirs into
   ``cpv_parallel_runner.parallel_scan`` (parity regression).
3. Findings are plain dicts (pickleable across process boundaries) —
   no validator-internal report objects leak through.
4. Order preservation: input-order in == output-order out, regardless
   of which worker process completes first.
5. Per-skill isolation: one broken skill doesn't poison the rest of
   the batch (the harness captures the worker exception into
   ``ScanResult.error``).

These tests exercise the REAL ``ProcessPoolExecutor`` (no mock) because
the contract under test IS the concurrency primitive. Per-test work is
trivial (well-formed minimal skills) so a 5-skill fixture finishes in
well under a second even with process-startup overhead.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts/ to sys.path the same way the other tests do.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_parallel_runner import parallel_scan  # noqa: E402
from validate_skill import scan_one_skill, validate_skill  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers — generate minimal valid skill dirs the validator accepts.
# ---------------------------------------------------------------------------


def _write_skill(skill_dir: Path, name: str, body_extra: str = "") -> None:
    """Create a minimal skill directory at ``skill_dir`` with name ``name``.

    The skill is valid (frontmatter parses, name matches dir, body has
    content) so the validator emits a deterministic set of findings.
    """
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f"name: {name}\n"
        "description: A test skill for parallel-scan parity regression\n"
        "---\n"
        "# Test Skill\n\n"
        "This is a test skill body.\n"
        f"{body_extra}"
    )
    (skill_dir / "SKILL.md").write_text(content)


def _write_broken_skill(skill_dir: Path) -> None:
    """Create a directory WITHOUT SKILL.md — the validator emits CRITICAL."""
    skill_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScanOneSkillIsPickleable:
    """``scan_one_skill`` must be a TOP-LEVEL importable function so
    ``ProcessPoolExecutor`` can pickle it for transmission to workers."""

    def test_scan_one_skill_is_callable_at_module_scope(self):
        """The function must be importable from validate_skill module."""
        import validate_skill

        assert hasattr(validate_skill, "scan_one_skill")
        assert callable(validate_skill.scan_one_skill)

    def test_scan_one_skill_returns_list_of_dicts(self, tmp_path):
        """Per-skill output must be ``list[dict]`` — pickleable primitives."""
        skill_dir = tmp_path / "demo-skill"
        _write_skill(skill_dir, "demo-skill")

        findings = scan_one_skill(skill_dir)
        assert isinstance(findings, list)
        assert all(isinstance(f, dict) for f in findings)
        # Every finding must have at least level + message.
        for f in findings:
            assert "level" in f
            assert "message" in f


class TestParallelVsSerialParity:
    """Running scan_one_skill via parallel_scan must produce IDENTICAL
    findings (same content, same order) compared to running it serially.
    This is the core regression contract of task #384."""

    def _build_fixture(self, tmp_path: Path, n: int = 5) -> list[Path]:
        """Create N distinct valid skill directories and return their paths."""
        dirs: list[Path] = []
        for i in range(n):
            d = tmp_path / f"skill-{i:03d}"
            _write_skill(d, f"skill-{i:03d}")
            dirs.append(d)
        return dirs

    def test_parallel_matches_serial_findings(self, tmp_path):
        """For a multi-skill fixture, parallel_scan produces the same
        per-skill findings (modulo input-order preservation) as a serial
        list comprehension over scan_one_skill."""
        skill_dirs = self._build_fixture(tmp_path, n=5)

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
            # Per-finding parity — same shape, same content, same order.
            assert result.findings == expected, (
                f"parity break on skill #{i}: parallel={result.findings} "
                f"serial={expected}"
            )

    def test_parallel_preserves_input_order(self, tmp_path):
        """Even if workers complete out of submission order, results MUST
        come back in input order. We embed the index in each skill name
        and verify the embedded index matches its slot position."""
        skill_dirs = self._build_fixture(tmp_path, n=8)
        results = parallel_scan(skill_dirs, scan_one_skill, n_workers=4)

        for i, r in enumerate(results):
            assert r.file_path == skill_dirs[i]
            # The skill name "skill-000", "skill-001", ... is embedded
            # via the 'name' field finding ("'name' field present: ...").
            name_findings = [
                f for f in r.findings if "'name' field present" in f.get("message", "")
            ]
            assert name_findings, f"no name finding on slot #{i}: {r.findings}"
            assert f"skill-{i:03d}" in name_findings[0]["message"]


class TestBrokenSkillIsolation:
    """A skill missing SKILL.md emits CRITICAL but does NOT crash the
    worker — it produces a normal finding list with one CRITICAL entry.
    The harness's ScanResult.error stays None for this case (the validator
    handled it gracefully)."""

    def test_missing_skill_md_emits_critical_not_error(self, tmp_path):
        """A skill dir with no SKILL.md returns a non-empty findings list
        containing a CRITICAL; ScanResult.error is None (validator
        gracefully handled the missing file, didn't raise)."""
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

        # Slot 1: broken skill, CRITICAL emitted via normal report path.
        assert results[1].error is None
        crit_1 = [f for f in results[1].findings if f["level"] == "CRITICAL"]
        assert crit_1
        assert any("SKILL.md not found" in f["message"] for f in crit_1)


class TestEmptyInputShortCircuit:
    """parallel_scan([]) returns [] without spawning a pool. Verify
    scan_one_skill is wired correctly even on a no-op call."""

    def test_empty_skill_list_returns_empty(self):
        """No skills in -> no findings out, no exception."""
        assert parallel_scan([], scan_one_skill) == []
