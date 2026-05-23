#!/usr/bin/env python3
"""Parallelism regression tests for ``validate_agents_directory`` (task #384).

The directory scanner was refactored from a serial ``for f in files`` loop
into a ``ProcessPoolExecutor``-backed ``parallel_scan``. These tests pin the
acceptance gate from ``/tmp/cpv-parallel-spec.md``: parallel and serial
paths produce IDENTICAL findings for the same multi-file fixture (same
severity, same message, same input order).

Why a separate file
-------------------
``ProcessPoolExecutor`` requires top-level pickleable callables and spawns
real worker processes, which is heavier than the unit tests in
``test_validate_agent.py``. Keeping the parallelism harness in its own
module:
  * lets ``test_validate_agent.py`` stay a fast in-process test file,
  * isolates the multi-file fixture setup the parity test needs,
  * matches the structure used by the shared harness's own tests
    (``test_cpv_parallel_runner.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Match the scripts/-on-sys.path convention used across the suite. The
# conftest.py at tests/ already does this, but we repeat it here so the
# module is importable when pytest collects it via the per-file CLI form
# this task uses (``uv run pytest tests/test_validate_agent_parallelism.py``).
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_agent import (  # noqa: E402
    AgentValidationReport,
    scan_one_agent,
    validate_agent,
    validate_agents_directory,
)

# ---------------------------------------------------------------------------
# Fixture helpers — write a directory of N realistic agent files.
# ---------------------------------------------------------------------------

# A small, valid agent. Body intentionally includes the structure the
# validator looks for (frontmatter + 2 example blocks + "You are" role
# definition) so the fixture produces a non-trivial result list per file
# instead of a single "missing X" complaint.
_VALID_AGENT = """\
---
name: {name}
description: Use when reviewing code in a {lang} project. Specialized in {lang}.
model: sonnet
tools:
  - Read
  - Grep
  - Glob
color: blue
---

# {title}

You are a code reviewer for {lang} projects.

## Capabilities

- Static analysis
- Security review
- Performance feedback

## Workflow

1. Read the files
2. Analyze
3. Report

<example>
user: Review src/{lang}_module.py
assistant: I will review src/{lang}_module.py.
<commentary>The agent reads the file and produces a structured review.</commentary>
</example>

<example>
user: Check tests for {lang}
assistant: I will analyze the test suite for {lang}.
<commentary>The agent inspects test coverage.</commentary>
</example>
"""


# A second-shape agent: minimal but invalid (missing description) so that
# the parity test exercises BOTH valid and invalid scoring paths through
# the parallel pool.
_INVALID_AGENT_NO_DESC = """\
---
name: {name}
model: sonnet
---

# {title}

You are a placeholder agent.

<example>
user: x
assistant: y
</example>

<example>
user: a
assistant: b
</example>
"""


def _write_agent_fixture_dir(root: Path, n_valid: int = 8, n_invalid: int = 4) -> Path:
    """Create a directory of N agent files with a mix of valid/invalid shapes.

    The mix matters: we want the parallel-vs-serial parity assertion to
    exercise both the success path (full validation pipeline produces many
    results) AND the failure path (early MAJOR/CRITICAL on missing fields).
    A pure-valid fixture would let a regression that only breaks the
    error path slip through.
    """
    agents_dir = root / "agents"
    agents_dir.mkdir()
    langs = ["python", "rust", "typescript", "go", "swift", "kotlin", "ruby", "elixir"]
    for i in range(n_valid):
        lang = langs[i % len(langs)]
        name = f"agent-valid-{i:02d}"
        (agents_dir / f"{name}.md").write_text(
            _VALID_AGENT.format(name=name, lang=lang, title=name.title()),
            encoding="utf-8",
        )
    for i in range(n_invalid):
        name = f"agent-invalid-{i:02d}"
        (agents_dir / f"{name}.md").write_text(
            _INVALID_AGENT_NO_DESC.format(name=name, title=name.title()),
            encoding="utf-8",
        )
    return agents_dir


def _report_signature(r: AgentValidationReport) -> tuple:
    """Reduce a report to its compare-safe shape.

    We compare on the ordered ``(level, message, file)`` tuple sequence —
    the validator's primary contract. Excluded:
      * ``line`` (always None for agent validators currently)
      * ``phase`` (not set by validate_agent)
      * dynamic absolute paths in ``agent_path`` (compared separately by
        filename only)

    Reducing to a hashable tuple lets pytest's ``==`` diff print readable
    side-by-sides on regression.
    """
    return tuple((res.level, res.message, res.file) for res in r.results)


# ---------------------------------------------------------------------------
# Parity test — the spec acceptance gate.
# ---------------------------------------------------------------------------


class TestParallelSerialParity:
    """Parallel ``validate_agents_directory`` must produce findings IDENTICAL
    to the prior serial baseline. Same severity, same message, same order."""

    def test_parallel_matches_serial_baseline(self, tmp_path):
        """For a 12-file fixture, parallel result == map(validate_agent, sorted(files))."""
        agents_dir = _write_agent_fixture_dir(tmp_path, n_valid=8, n_invalid=4)

        # Parallel path — exercises the production code (post-refactor).
        parallel_reports = validate_agents_directory(agents_dir)

        # Serial baseline — replicates the exact loop that lived in
        # validate_agents_directory before task #384. If the production
        # function ever drifts away from "sorted alphabetically", this
        # baseline assertion will catch it.
        sorted_files = sorted(
            p for p in agents_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"
        )
        serial_reports = [validate_agent(f) for f in sorted_files]

        # Same number of reports.
        assert len(parallel_reports) == len(serial_reports) == 12

        # Same input order — both must lex-sort by filename.
        parallel_names = [Path(r.agent_path).name for r in parallel_reports]
        serial_names = [Path(r.agent_path).name for r in serial_reports]
        assert parallel_names == serial_names

        # Same per-file finding signature — the actual parity check.
        for par, ser in zip(parallel_reports, serial_reports):
            assert _report_signature(par) == _report_signature(ser), (
                f"Parallel/serial divergence on {Path(par.agent_path).name}"
            )

    def test_input_order_is_alphabetical(self, tmp_path):
        """Output order is alphabetical regardless of filesystem iteration order.

        The harness preserves input order; ``validate_agents_directory`` sorts
        before submitting. This test pins that contract — a future refactor
        that drops the ``sorted(...)`` call would re-introduce filesystem-order
        flakiness on case-insensitive volumes (macOS HFS+, exFAT).
        """
        agents_dir = _write_agent_fixture_dir(tmp_path, n_valid=5, n_invalid=3)
        reports = validate_agents_directory(agents_dir)
        names = [Path(r.agent_path).name for r in reports]
        assert names == sorted(names), f"order broke: {names}"

    def test_single_file_directory_still_works(self, tmp_path):
        """One-file dirs are a degenerate case — make sure the pool overhead
        is acceptable AND the result equals the single ``validate_agent`` call.

        ``parallel_scan`` short-circuits when ``files`` is empty but NOT for
        one file; this asserts the one-file path produces the same result as
        a direct call. Catches regressions where the harness or aggregator
        mis-handles the single-element case (e.g. off-by-one indexing).
        """
        agents_dir = _write_agent_fixture_dir(tmp_path, n_valid=1, n_invalid=0)
        files = sorted(agents_dir.iterdir())
        assert len(files) == 1

        parallel_reports = validate_agents_directory(agents_dir)
        serial_report = validate_agent(files[0])

        assert len(parallel_reports) == 1
        assert _report_signature(parallel_reports[0]) == _report_signature(serial_report)

    def test_empty_directory_unchanged_behavior(self, tmp_path):
        """Empty dir hits the early-return path; harness must not be called.

        ``parallel_scan`` is well-behaved on empty input (returns []), but the
        validator's early-return code path is what callers (CI, scripts)
        depend on — an empty-dir scan returns ONE info-report, not zero
        reports. This pins the existing pre-refactor public contract.
        """
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()  # empty
        reports = validate_agents_directory(agents_dir)
        assert len(reports) == 1
        assert any("No agent files" in r.message for r in reports[0].results)


# ---------------------------------------------------------------------------
# Worker contract — ``scan_one_agent`` must satisfy the harness invariants.
# ---------------------------------------------------------------------------


class TestScanOneAgentContract:
    """``scan_one_agent`` is the pickleable worker the harness submits to
    the pool. These tests pin its contract independent of the dispatcher."""

    def test_returns_single_element_list_of_reports(self, tmp_path):
        """``scan_one_agent`` returns ``[report]`` — single element, never zero
        or more. The aggregator's ``assert len(sr.findings) == 1`` depends
        on this; if the worker ever returns 0 or 2 elements the directory
        scan crashes loudly during dev (which is the intent)."""
        agent_path = tmp_path / "test-agent.md"
        agent_path.write_text(
            _VALID_AGENT.format(name="test-agent", lang="python", title="Test"),
            encoding="utf-8",
        )
        result = scan_one_agent(agent_path)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], AgentValidationReport)
        assert result[0].agent_path == str(agent_path)

    def test_worker_is_pickleable(self):
        """The harness uses ``ProcessPoolExecutor.submit(_run_one, scan_one_agent, ...)``
        which requires both the shim AND ``scan_one_agent`` to pickle. A
        nested function or a class method would fail at submit time with
        ``PicklingError``. We verify by round-tripping through ``pickle``."""
        import pickle

        roundtripped = pickle.loads(pickle.dumps(scan_one_agent))
        # The pickled-then-unpickled object should still be the same callable
        # (by qualified name). Pickle stores callables by reference, not by
        # serializing the code, so identity-equality holds.
        assert roundtripped is scan_one_agent

    def test_worker_result_is_pickleable(self, tmp_path):
        """``AgentValidationReport`` (and its nested ``ValidationResult`` list)
        must pickle so the harness can ship it back from a worker process.
        Catches regressions where someone adds an unpickleable field
        (callable, open file handle, threading.Lock) to the report dataclass."""
        import pickle

        agent_path = tmp_path / "test-agent.md"
        agent_path.write_text(
            _VALID_AGENT.format(name="test-agent", lang="python", title="Test"),
            encoding="utf-8",
        )
        report = scan_one_agent(agent_path)[0]
        blob = pickle.dumps(report)
        restored = pickle.loads(blob)
        assert restored.agent_path == report.agent_path
        assert len(restored.results) == len(report.results)
        # Compare the actual signatures element-by-element so a regression in
        # ValidationResult fields surfaces here (not just at length-equality).
        assert _report_signature(restored) == _report_signature(report)
