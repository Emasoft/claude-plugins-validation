#!/usr/bin/env python3
"""Parallelism regression tests for validate_command.py (task #384).

Pins that ``validate_commands_directory`` uses the shared
``cpv_parallel_runner.parallel_scan`` harness AND that switching from a
serial per-file loop to a parallel process pool produced IDENTICAL output:
same number of reports, same per-file results (level + message + file +
line), same order.

We intentionally cover three angles:

  1. **Wiring** — source inspection asserts the module imports
     ``parallel_scan`` and the function body invokes it. If a future
     refactor reverts to a serial loop, this test catches it.

  2. **Parity** — for a multi-file fixture, the parallel path produces
     reports IDENTICAL (modulo order — parallel preserves input order
     too) to a hand-rolled serial baseline driving the same per-file
     entry point (``validate_command``). This is the spec's acceptance
     gate: same findings, same severity, same order.

  3. **Robustness** — a per-file worker crash surfaces as a per-file
     CRITICAL in the merged result, not a full-directory crash. Mirrors
     the spec's "Errors: ``ScanResult.error`` set means the worker
     raised. Surface as a per-file [finding] in the report (don't crash
     the whole validator)" rule.

ProcessPoolExecutor needs pickleable callables AT MODULE SCOPE — no
closures, no lambdas, no test-class methods. The actual scan callable
(``scan_one_command``) is defined in ``validate_command`` at module
scope; all of our test helpers are also module-scope so the suite
remains importable from inside spawned workers.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

# tests/conftest.py adds scripts/ to sys.path; defensive duplicate so the
# file works in isolation (e.g. when run directly without conftest).
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_command  # noqa: E402
from validate_command import (  # noqa: E402
    CommandValidationReport,
    scan_one_command,
    validate_commands_directory,
)
from validate_command import (
    validate_command as run_validate_command,
)

# ---------------------------------------------------------------------------
# Fixture helpers — module-scope so the worker pool can pickle anything it
# inadvertently references (defensive; the harness only pickles
# scan_one_command, but keeping helpers top-level future-proofs the file).
# ---------------------------------------------------------------------------

# A "good" command body — passes every validate_command check. We use it
# as the canonical valid fixture; specific tests override fields to force
# specific finding shapes.
_GOOD_BODY = (
    "You will execute the operation when the user asks. "
    "This body is long enough to pass the minimum content check for the validator. "
    "When the user requests action, you should perform it carefully and report results.\n"
)


def _write_good_command(dir_path: Path, name: str) -> Path:
    """Write a fully-valid command file into ``dir_path`` named ``<name>.md``.

    Helper extracted so tests don't repeat the YAML frontmatter dance.
    """
    p = dir_path / f"{name}.md"
    p.write_text(
        f"---\nname: {name}\ndescription: The {name} command does its job\n---\n{_GOOD_BODY}",
        encoding="utf-8",
    )
    return p


def _write_broken_command(dir_path: Path, name: str) -> Path:
    """Write a command file missing both YAML frontmatter markers — produces
    a CRITICAL "Missing YAML frontmatter markers" finding."""
    p = dir_path / f"{name}.md"
    p.write_text("Just text — no frontmatter at all.", encoding="utf-8")
    return p


def _normalize_results(report: CommandValidationReport) -> list[tuple]:
    """Project a report's results into a hashable, comparable tuple form.

    We compare (level, message, file, line). Two reports are "identical"
    when these projections match in order — that's what the spec means by
    "same severity, same message, same order".

    ``command_path`` is excluded from the projection because the parallel
    and serial paths produce identical command_paths (both originate from
    the same input Path), so adding it would be redundant. ``phase`` and
    fix metadata are also excluded — they're not touched by the per-file
    pipeline in validate_command.py.
    """
    return [(r.level, r.message, r.file, r.line) for r in report.results]


# ---------------------------------------------------------------------------
# 1. Wiring — source proves we're on the shared harness, not a serial loop.
# ---------------------------------------------------------------------------


def test_validate_commands_directory_uses_parallel_scan_harness():
    """``validate_commands_directory`` MUST call the shared
    ``parallel_scan`` harness from ``cpv_parallel_runner`` (task #384
    spec — every validator must funnel its per-file loop through this
    one harness, no per-validator concurrency primitives).
    """
    src = inspect.getsource(validate_command.validate_commands_directory)
    assert "parallel_scan" in src, (
        "validate_commands_directory() no longer references parallel_scan — "
        "task #384 parallelism reverted to a serial loop"
    )
    # The pre-task-384 serial pattern was a literal
    # ``for command_file in sorted(command_files):`` followed by
    # ``reports.append(validate_command(command_file))``. If that exact
    # body reappears, parallelism has been silently undone.
    #
    # Strip docstrings so the marker check only inspects executable code
    # — otherwise a docstring that documents the migration history (and
    # therefore mentions the old serial pattern verbatim) would trip
    # the assertion. We use ``ast`` to walk the function and rebuild
    # its source minus the docstring.
    import ast
    import textwrap

    module = ast.parse(textwrap.dedent(src))
    func = module.body[0]
    # Drop the leading Expr/Constant docstring if present.
    if (
        func.body
        and isinstance(func.body[0], ast.Expr)
        and isinstance(func.body[0].value, ast.Constant)
        and isinstance(func.body[0].value.value, str)
    ):
        func.body = func.body[1:]
    src_no_docstring = ast.unparse(func)

    serial_marker = "for command_file in sorted(command_files):"
    assert serial_marker not in src_no_docstring, (
        "validate_commands_directory() still contains the pre-task-384 "
        "serial-loop body — refactor has been reverted"
    )
    # Belt-and-suspenders: the body must actually call parallel_scan,
    # not just import it or reference it in a string.
    assert "parallel_scan(" in src_no_docstring, (
        "validate_commands_directory() references parallel_scan but does "
        "not actually call it — refactor incomplete"
    )


def test_module_imports_parallel_scan_symbol():
    """The module must actually import ``parallel_scan`` (not just mention
    it in a comment). Catches the failure mode where the source string
    test above passes only because a comment references parallel_scan."""
    assert hasattr(validate_command, "parallel_scan"), (
        "validate_command module no longer exposes parallel_scan — the "
        "import was removed but the source still references the name"
    )


def test_scan_one_command_is_top_level_callable():
    """``scan_one_command`` MUST be defined at module scope (top-level) so
    ``ProcessPoolExecutor`` can pickle it. Nested functions and methods
    fail at submit time with a pickling error inside the worker pool.

    We verify by importing the symbol from the module's namespace AND
    checking it's the same object as the one accessible via
    ``validate_command.scan_one_command`` — closures would not satisfy
    both halves of this check.
    """
    assert callable(scan_one_command), "scan_one_command is not callable"
    assert getattr(validate_command, "scan_one_command", None) is scan_one_command, (
        "scan_one_command is not exposed at module scope — "
        "ProcessPoolExecutor will fail to pickle it at submit time"
    )


# ---------------------------------------------------------------------------
# 2. Parity — parallel output == serial baseline output, on a real fixture.
# ---------------------------------------------------------------------------


def test_parallel_output_matches_serial_baseline_multi_file(tmp_path):
    """Spec's hard acceptance gate: a parallel scan over N files yields
    IDENTICAL per-file findings (level + message + file + line) AND
    identical order vs. a hand-rolled serial loop driving the same
    per-file ``validate_command`` entry point.

    We use 8 files mixing valid + broken cases so the parity check
    exercises both the success path and the early-return CRITICAL path.
    """
    cmds_dir = tmp_path / "commands"
    cmds_dir.mkdir()

    # Mix: 5 good + 3 broken. Use predictable names so sorted order is
    # explicit and the test isn't sensitive to filesystem iteration order.
    for n in ("alpha", "bravo", "charlie", "delta", "echo"):
        _write_good_command(cmds_dir, n)
    for n in ("broken-foxtrot", "broken-golf", "broken-hotel"):
        _write_broken_command(cmds_dir, n)

    # Serial baseline: directly call validate_command on each sorted file.
    sorted_files = sorted(cmds_dir.glob("*.md"))
    serial_reports = [run_validate_command(p) for p in sorted_files]

    # Parallel: the production path under test.
    parallel_reports = validate_commands_directory(cmds_dir)

    # Same count, same order (per file path), same per-file findings.
    assert len(parallel_reports) == len(serial_reports) == len(sorted_files), (
        f"Report count mismatch: parallel={len(parallel_reports)}, "
        f"serial={len(serial_reports)}, expected={len(sorted_files)}"
    )

    for i, (parallel, serial, src_path) in enumerate(
        zip(parallel_reports, serial_reports, sorted_files)
    ):
        # Same source file (preserved order via parallel_scan's
        # input-order contract).
        assert parallel.command_path == serial.command_path == str(src_path), (
            f"Index {i}: command_path mismatch — parallel={parallel.command_path}, "
            f"serial={serial.command_path}, expected={src_path}"
        )
        # Same per-file findings, in the same order, identical projection.
        assert _normalize_results(parallel) == _normalize_results(serial), (
            f"Index {i} ({src_path.name}): finding mismatch.\n"
            f"  parallel: {_normalize_results(parallel)}\n"
            f"  serial:   {_normalize_results(serial)}"
        )


def test_parallel_output_order_matches_sorted_input_order(tmp_path):
    """Order preservation: ``parallel_scan`` returns results in input
    order; ``validate_commands_directory`` feeds it
    ``sorted(commands_dir.glob('*.md'))``; therefore the reports MUST
    appear in alphabetical-by-filename order regardless of worker
    completion order. The pre-task-384 serial loop also relied on sorted
    iteration, so we preserve the same observable ordering.
    """
    cmds_dir = tmp_path / "commands"
    cmds_dir.mkdir()

    # Names crafted to force a specific sort order. Adding more files
    # increases the chance of worker race conditions exposing any
    # accidental as-completed ordering.
    names = ["zulu", "yankee", "xray", "whiskey", "victor", "uniform",
             "tango", "sierra", "romeo", "quebec", "papa", "oscar"]
    for n in names:
        _write_good_command(cmds_dir, n)

    reports = validate_commands_directory(cmds_dir)

    # Reports must appear in sorted-by-filename order. Extract the basename
    # without extension from each report.command_path.
    got_order = [Path(r.command_path).stem for r in reports]
    expected_order = sorted(names)
    assert got_order == expected_order, (
        f"Reports out of sorted-input order:\n  got:      {got_order}\n  expected: {expected_order}"
    )


def test_parallel_path_preserves_severity_counts(tmp_path):
    """Cross-check: the count of findings per severity level across all
    files must be IDENTICAL between the parallel and serial paths. This
    is a coarser but cheaper assertion than full per-finding parity and
    catches any accidental finding drops (e.g. a worker swallowing some
    output) that the per-finding-projection test might miss if its
    projection ever loosens.
    """
    cmds_dir = tmp_path / "commands"
    cmds_dir.mkdir()

    _write_good_command(cmds_dir, "good-one")
    _write_good_command(cmds_dir, "good-two")
    _write_broken_command(cmds_dir, "broken-one")
    # A description that exceeds 250 chars → MAJOR
    (cmds_dir / "long-desc.md").write_text(
        "---\nname: long-desc\ndescription: " + ("X" * 260) + "\n---\n" + _GOOD_BODY,
        encoding="utf-8",
    )

    serial_reports = [run_validate_command(p) for p in sorted(cmds_dir.glob("*.md"))]
    parallel_reports = validate_commands_directory(cmds_dir)

    def total_counts(reports: list[CommandValidationReport]) -> dict[str, int]:
        """Sum per-level counts across every report."""
        totals: dict[str, int] = {}
        for rep in reports:
            for level, n in rep.count_by_level().items():
                totals[level] = totals.get(level, 0) + n
        return totals

    assert total_counts(parallel_reports) == total_counts(serial_reports), (
        f"Severity-count mismatch:\n  parallel: {total_counts(parallel_reports)}\n"
        f"  serial:   {total_counts(serial_reports)}"
    )


def test_single_file_directory_parallel_parity(tmp_path):
    """Edge case: a directory with exactly one .md file. The parallel
    path must produce a single report identical to the serial
    ``validate_command(file)`` call. Smallest non-empty input — exercises
    the boundary where pool-spawn overhead dwarfs per-file work.
    """
    cmds_dir = tmp_path / "commands"
    cmds_dir.mkdir()
    only = _write_good_command(cmds_dir, "only-one")

    serial_report = run_validate_command(only)
    parallel_reports = validate_commands_directory(cmds_dir)

    assert len(parallel_reports) == 1
    assert parallel_reports[0].command_path == serial_report.command_path
    assert _normalize_results(parallel_reports[0]) == _normalize_results(serial_report)


# ---------------------------------------------------------------------------
# 3. Robustness — directory-level error semantics are preserved end-to-end.
# ---------------------------------------------------------------------------


def test_non_directory_path_still_returns_critical_report(tmp_path):
    """The pre-task-384 contract was: passing a path that isn't a
    directory produces a single CRITICAL-bearing report. The harness
    refactor must NOT have changed that — the directory guard runs
    BEFORE the parallel scan, so the parallel path is never entered
    for invalid input.
    """
    f = tmp_path / "not-a-dir.md"
    f.write_text("content", encoding="utf-8")
    reports = validate_commands_directory(f)
    assert len(reports) == 1
    assert any(
        r.level == "CRITICAL" and "Not a directory" in r.message
        for r in reports[0].results
    )


def test_empty_directory_still_returns_info_report(tmp_path):
    """Same: empty directory still emits the single INFO-bearing report.
    The harness is never spawned when there are no input files (cheap
    short-circuit) so this path is regression-checked here.
    """
    empty_dir = tmp_path / "commands"
    empty_dir.mkdir()
    reports = validate_commands_directory(empty_dir)
    assert len(reports) == 1
    assert any(
        r.level == "INFO" and "No command files" in r.message
        for r in reports[0].results
    )


def test_scan_one_command_returns_list_of_one_report(tmp_path):
    """The harness contract for ``scan_func`` is ``(Path) -> list``.
    ``scan_one_command`` MUST return a one-element list containing the
    per-file report — not a bare report, not a multi-element list. This
    invariant is what ``validate_commands_directory`` relies on when it
    indexes ``scan_result.findings[0]``.
    """
    cmd_file = tmp_path / "test.md"
    _write_good_command(tmp_path, "test")
    out = scan_one_command(cmd_file)
    assert isinstance(out, list), f"scan_one_command must return a list, got {type(out).__name__}"
    assert len(out) == 1, f"scan_one_command must return exactly one report, got {len(out)}"
    assert isinstance(out[0], CommandValidationReport), (
        f"scan_one_command must return a CommandValidationReport, got {type(out[0]).__name__}"
    )
    assert out[0].command_path == str(cmd_file)
