#!/usr/bin/env python3
"""CPV parallel-scan harness (task #384).

Validators that scan many files (validate_security, validate_skill,
validate_cache, validate_xref, …) historically iterate serially:

    for file in files:
        scan_one_file(file)

That makes a 200-file plugin's lint loop a 200×(per-file-cost) wall-clock cost.
This harness wraps the per-file callable in a ``ProcessPoolExecutor`` so the
loop becomes parallel-by-CPU-count without changing the per-file logic.

## Contract

* ``scan_func`` MUST be a TOP-LEVEL importable function (closures and
  lambdas are NOT pickleable by ``ProcessPoolExecutor``).
* ``scan_func`` takes exactly ONE arg (a ``Path``). For validators that need
  extra context, capture it in module-level globals OR refactor the
  validator to be context-free at the per-file level.
* Findings returned by ``scan_func`` MUST be pickleable (dicts of primitives
  or simple dataclasses — NOT report objects holding open file handles).
* Result order is PRESERVED (input order in == output order out).
* Exceptions raised by ``scan_func`` are CAPTURED into ``ScanResult.error``
  by default (``on_error="collect"``); the harness does NOT crash.

This module is INTENTIONALLY minimal. It owns concurrency primitives and
nothing else; validators retain their domain logic.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ScanResult:
    """One file's scan outcome. ``findings`` is empty when ``error`` is set."""

    file_path: Path
    findings: list
    error: str | None = None


def parallel_scan(
    files: Sequence[Path],
    scan_func: Callable[[Path], list],
    *,
    n_workers: int | None = None,
    chunk_size: int = 1,
    on_error: str = "collect",
    timeout_per_file: float | None = None,
) -> list[ScanResult]:
    """Run ``scan_func(file)`` for each file in parallel.

    NOT YET IMPLEMENTED — Agent 1 owns the implementation.
    """
    raise NotImplementedError("parallel_scan — Agent 1 owns the implementation per /tmp/cpv-parallel-spec.md")


def parallel_scan_aggregated(
    files: Sequence[Path],
    scan_func: Callable[[Path], list],
    aggregator: Callable[[list[ScanResult]], T],
    **kwargs,
) -> T:
    """Convenience: ``aggregator(parallel_scan(files, scan_func, **kwargs))``.

    NOT YET IMPLEMENTED — Agent 1 owns the implementation.
    """
    raise NotImplementedError("parallel_scan_aggregated — Agent 1 owns the implementation")
