"""Tests for the shared parallel-scan harness (task #384).

ProcessPoolExecutor needs pickleable callables AT MODULE SCOPE — no closures,
no lambdas, no test-class methods. Every scan_func passed to ``parallel_scan``
in this file is a top-level function defined here, by design.

We exercise a REAL ProcessPoolExecutor (no mock) because the contract IS the
concurrency primitive — mocking it would test nothing. Tests are kept short
(N ≤ 100 files) and the per-file work is trivial so the suite finishes in
seconds even with process-startup overhead.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from scripts.cpv_parallel_runner import (
    ScanResult,
    parallel_scan,
    parallel_scan_aggregated,
)


# ---------------------------------------------------------------------------
# Top-level scan callables — MUST be pickleable, hence module-scope.
# Each one models a different validator-shaped behavior.
# ---------------------------------------------------------------------------


def scan_return_path_name(path: Path) -> list:
    """Trivial scan: returns one 'finding' containing the file's name.
    Used to verify input-order preservation and basic plumbing."""
    return [{"name": path.name}]


def scan_return_index_finding(path: Path) -> list:
    """Returns a finding whose value equals the trailing integer in the
    filename. Lets tests assert order by checking the integer sequence
    in the result list."""
    # Filenames are crafted by the test as e.g. "file_007.txt".
    stem = path.stem  # "file_007"
    n_str = stem.split("_")[-1]
    return [{"idx": int(n_str), "path": str(path)}]


def scan_always_raise(path: Path) -> list:
    """Raises ``RuntimeError`` for every file. Used to test error capture /
    re-raise behavior."""
    raise RuntimeError(f"intentional failure on {path.name}")


def scan_raise_for_even_index(path: Path) -> list:
    """Raises for files whose trailing index is even; returns normally
    otherwise. Mixed success/failure to verify per-file isolation."""
    stem = path.stem
    n = int(stem.split("_")[-1])
    if n % 2 == 0:
        raise ValueError(f"even index {n}")
    return [{"idx": n}]


def scan_sleep_then_return(path: Path) -> list:
    """Sleeps for 5 seconds before returning. Used to test ``timeout_per_file``
    with a short timeout (we don't want to actually wait 5s; the timeout
    fires first)."""
    time.sleep(5)
    return [{"name": path.name}]


def scan_returns_empty(path: Path) -> list:
    """Returns no findings — the common 'this file is clean' validator
    return value. Verifies that empty findings list ≠ error."""
    return []


def scan_returns_multiple(path: Path) -> list:
    """Returns three findings — verifies the harness doesn't flatten or
    truncate the inner list."""
    return [
        {"id": 1, "path": str(path)},
        {"id": 2, "path": str(path)},
        {"id": 3, "path": str(path)},
    ]


def _make_unpickleable_scan():
    """Returns a closure (NOT pickleable). Used to verify that the harness
    surfaces ProcessPoolExecutor's pickling error clearly when a caller
    violates the top-level-only contract."""
    captured = "i-am-a-closure"

    def inner(path: Path) -> list:
        return [{"closure_value": captured, "path": str(path)}]

    return inner


# Aggregator for parallel_scan_aggregated tests — also must be top-level.
def aggregate_count_findings(results: list[ScanResult]) -> int:
    """Sums findings across all ScanResults (ignoring errored ones)."""
    return sum(len(r.findings) for r in results)


def aggregate_to_dict(results: list[ScanResult]) -> dict:
    """Builds {path-name → findings-list} mapping. Used to verify the
    aggregator sees ALL results, in order."""
    return {r.file_path.name: r.findings for r in results}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_files(tmp_path: Path) -> list[Path]:
    """Create 10 small files numbered file_000.txt..file_009.txt."""
    paths: list[Path] = []
    for i in range(10):
        p = tmp_path / f"file_{i:03d}.txt"
        p.write_text(f"content of file {i}", encoding="utf-8")
        paths.append(p)
    return paths


@pytest.fixture
def many_tmp_files(tmp_path: Path) -> list[Path]:
    """Create 100 small files — exercises larger-scale order preservation
    and basic throughput sanity."""
    paths: list[Path] = []
    for i in range(100):
        p = tmp_path / f"file_{i:03d}.txt"
        p.write_text(f"content {i}", encoding="utf-8")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_returns_scan_result_per_file(tmp_files):
    """Every input file produces exactly one ScanResult; no error; findings
    populated."""
    results = parallel_scan(tmp_files, scan_return_path_name)
    assert len(results) == len(tmp_files)
    for r, p in zip(results, tmp_files):
        assert isinstance(r, ScanResult)
        assert r.file_path == p
        assert r.error is None
        assert r.findings == [{"name": p.name}]


def test_input_order_preserved_with_default_workers(tmp_files):
    """Output ScanResults are in the SAME order as the input files,
    regardless of which worker finished first. This is the core
    spec invariant."""
    results = parallel_scan(tmp_files, scan_return_index_finding)
    assert [r.findings[0]["idx"] for r in results] == list(range(10))


def test_input_order_preserved_with_large_input(many_tmp_files):
    """Order preservation at 100-file scale (forces multiple workers
    to actually race for completion order)."""
    results = parallel_scan(many_tmp_files, scan_return_index_finding)
    assert len(results) == 100
    assert [r.findings[0]["idx"] for r in results] == list(range(100))
    # And the file_path field also lines up — not just the embedded idx.
    for r, p in zip(results, many_tmp_files):
        assert r.file_path == p


def test_empty_input_returns_empty_list_without_spawning_pool():
    """No files → no work, no pool. The exact behavior we want is just an
    empty list — no exception, no hang."""
    assert parallel_scan([], scan_return_path_name) == []


def test_single_file_input(tmp_path):
    """One file is the smallest non-empty case — exercises the boundary
    where pool overhead dwarfs scan cost. Result must still be a 1-element
    list with correct content."""
    p = tmp_path / "lonely.txt"
    p.write_text("alone", encoding="utf-8")
    results = parallel_scan([p], scan_return_path_name)
    assert len(results) == 1
    assert results[0].file_path == p
    assert results[0].findings == [{"name": "lonely.txt"}]
    assert results[0].error is None


def test_error_captured_when_on_error_collect(tmp_files):
    """``on_error='collect'`` (default): worker exceptions DO NOT propagate;
    they land in ``ScanResult.error`` as a stringified ``Class: message``."""
    results = parallel_scan(tmp_files, scan_always_raise)
    assert len(results) == len(tmp_files)
    for r in results:
        assert r.findings == []
        assert r.error is not None
        assert r.error.startswith("RuntimeError:")
        assert "intentional failure" in r.error


def test_error_raised_when_on_error_raise(tmp_files):
    """``on_error='raise'`` re-raises the worker exception verbatim
    (same exception class, same message)."""
    with pytest.raises(RuntimeError, match="intentional failure"):
        parallel_scan(tmp_files, scan_always_raise, on_error="raise")


def test_mixed_success_and_failure_per_file_isolation(tmp_files):
    """If one file's scan raises, OTHER files' results are unaffected.
    Verifies that the worker pool doesn't poison sibling results."""
    results = parallel_scan(tmp_files, scan_raise_for_even_index)
    assert len(results) == 10
    for i, r in enumerate(results):
        if i % 2 == 0:
            assert r.findings == []
            assert r.error is not None
            assert r.error.startswith("ValueError:")
        else:
            assert r.error is None
            assert r.findings == [{"idx": i}]


def test_timeout_per_file_collects_as_error(tmp_path):
    """A scan that sleeps 5s but has a 0.5s timeout → ``ScanResult.error``
    is set to a TimeoutError message; findings empty; no crash."""
    p = tmp_path / "slow.txt"
    p.write_text("slow", encoding="utf-8")
    results = parallel_scan(
        [p],
        scan_sleep_then_return,
        timeout_per_file=0.5,
        n_workers=1,
    )
    assert len(results) == 1
    assert results[0].findings == []
    assert results[0].error is not None
    assert "TimeoutError" in results[0].error
    assert "0.5" in results[0].error


def test_timeout_per_file_raises_when_on_error_raise(tmp_path):
    """``on_error='raise'`` + a timeout → ``TimeoutError`` propagated."""
    p = tmp_path / "slow.txt"
    p.write_text("slow", encoding="utf-8")
    with pytest.raises(TimeoutError, match="0.5"):
        parallel_scan(
            [p],
            scan_sleep_then_return,
            timeout_per_file=0.5,
            on_error="raise",
            n_workers=1,
        )


def test_n_workers_override_respected(tmp_files):
    """Explicit ``n_workers=1`` still works (no pool deadlock) and produces
    the same correct output as the default."""
    results = parallel_scan(tmp_files, scan_return_index_finding, n_workers=1)
    assert [r.findings[0]["idx"] for r in results] == list(range(10))


def test_n_workers_default_uses_cpu_count(monkeypatch, tmp_files):
    """When ``n_workers is None``, the harness consults ``os.cpu_count()``.
    Patch it to return a known value and verify the pool isn't crashing
    on an unexpected default."""
    # The actual worker count is internal to ProcessPoolExecutor; we don't
    # peek inside it. The contract is: no crash with default config + correct
    # output. Forcing cpu_count to 2 exercises a known multi-worker path
    # that's portable across CI machines.
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    results = parallel_scan(tmp_files, scan_return_index_finding)
    assert [r.findings[0]["idx"] for r in results] == list(range(10))


def test_chunk_size_greater_than_one_batches_files(many_tmp_files):
    """``chunk_size=10`` → 100 files arrive as 10 batches; order is still
    preserved; per-file findings are still individually returned."""
    results = parallel_scan(
        many_tmp_files, scan_return_index_finding, chunk_size=10
    )
    assert len(results) == 100
    assert [r.findings[0]["idx"] for r in results] == list(range(100))
    for r, p in zip(results, many_tmp_files):
        assert r.file_path == p
        assert r.error is None


def test_chunk_size_greater_than_one_with_per_file_errors(tmp_files):
    """Batched mode still isolates per-file errors within a batch — a
    raising file in batch position 0 doesn't kill positions 1..N."""
    results = parallel_scan(
        tmp_files, scan_raise_for_even_index, chunk_size=4
    )
    assert len(results) == 10
    for i, r in enumerate(results):
        if i % 2 == 0:
            assert r.error is not None
            assert r.error.startswith("ValueError:")
        else:
            assert r.error is None
            assert r.findings == [{"idx": i}]


def test_chunk_size_zero_or_negative_rejected():
    """Sub-1 chunk_size is a programming error — ``ValueError`` at call time,
    not a silent infinite loop."""
    with pytest.raises(ValueError, match="chunk_size"):
        parallel_scan([Path("x")], scan_return_path_name, chunk_size=0)
    with pytest.raises(ValueError, match="chunk_size"):
        parallel_scan([Path("x")], scan_return_path_name, chunk_size=-1)


def test_invalid_on_error_rejected():
    """Unknown ``on_error`` value is a programming error → ``ValueError``."""
    with pytest.raises(ValueError, match="on_error"):
        parallel_scan(
            [Path("x")], scan_return_path_name, on_error="ignore"
        )


def test_unpickleable_callable_raises_clear_error(tmp_files):
    """Closures aren't pickleable — ProcessPoolExecutor raises a pickling
    error at submit/result time. Verify the error surfaces rather than
    hanging or being silently swallowed."""
    closure = _make_unpickleable_scan()
    # Pickling errors from ProcessPoolExecutor come through as either
    # ``PicklingError`` (from pickle) or ``AttributeError`` (from the
    # un-importable local function) depending on Python version. Both are
    # acceptable; the key invariant is "raises SOMETHING within a few
    # seconds, doesn't hang".
    with pytest.raises(Exception) as excinfo:
        parallel_scan(tmp_files, closure, on_error="raise", n_workers=1)
    # Make sure it's actually a serialization-related failure, not a logic
    # bug pretending to be one.
    msg = str(excinfo.value) + type(excinfo.value).__name__
    assert (
        "pickle" in msg.lower()
        or "local object" in msg.lower()
        or "attribute" in msg.lower()
    )


def test_empty_findings_list_is_not_an_error(tmp_files):
    """A file that returns ``[]`` (i.e. "clean") is a SUCCESS, not an
    error. ``ScanResult.findings == []`` and ``error is None``."""
    results = parallel_scan(tmp_files, scan_returns_empty)
    for r in results:
        assert r.findings == []
        assert r.error is None


def test_findings_list_preserved_intact(tmp_files):
    """A scan returning 3 findings produces 3 findings in the ScanResult
    — the harness doesn't flatten, dedupe, or truncate."""
    results = parallel_scan(tmp_files, scan_returns_multiple)
    for r, p in zip(results, tmp_files):
        assert r.error is None
        assert len(r.findings) == 3
        assert all(f["path"] == str(p) for f in r.findings)
        assert [f["id"] for f in r.findings] == [1, 2, 3]


def test_parallel_scan_aggregated_end_to_end(tmp_files):
    """``parallel_scan_aggregated`` runs the scan and applies the aggregator
    to the resulting ScanResult list. Verifies both the count aggregator
    and a dict aggregator that depends on input order."""
    total = parallel_scan_aggregated(
        tmp_files, scan_returns_multiple, aggregate_count_findings
    )
    assert total == 10 * 3  # 10 files * 3 findings each

    mapping = parallel_scan_aggregated(
        tmp_files, scan_return_path_name, aggregate_to_dict
    )
    assert list(mapping.keys()) == [p.name for p in tmp_files]
    for p in tmp_files:
        assert mapping[p.name] == [{"name": p.name}]


def test_parallel_scan_aggregated_passes_through_kwargs(tmp_files):
    """``parallel_scan_aggregated`` forwards kwargs to ``parallel_scan``
    (on_error, n_workers, chunk_size, timeout_per_file). Demonstrate by
    triggering on_error='raise' through the aggregated entry point."""
    with pytest.raises(RuntimeError, match="intentional failure"):
        parallel_scan_aggregated(
            tmp_files,
            scan_always_raise,
            aggregate_count_findings,
            on_error="raise",
        )


def test_results_are_frozen_dataclass_instances(tmp_files):
    """Sanity check: ``ScanResult`` is frozen — mutating returned results
    raises. Important because validators may pass these between threads
    or hash them; we don't want surprise mutation."""
    results = parallel_scan(tmp_files, scan_return_path_name)
    assert results
    r = results[0]
    with pytest.raises(Exception):  # FrozenInstanceError, subclass of AttributeError
        r.findings = []  # type: ignore[misc]
    with pytest.raises(Exception):
        r.error = "tampered"  # type: ignore[misc]
