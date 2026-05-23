"""Helper worker module for test_skillaudit_native_parallelism.

This module exists at the file level (not nested in the test class)
because ``ProcessPoolExecutor`` pickles callables by qualified name,
NOT by value. A function defined inside a test method, a closure, or
a lambda cannot cross the pool boundary — pickle can't locate it on
the worker side. By living in its own module file with a stable
module-level qualified name, the wrapper IS pickleable and the
worker subprocess can import and call it.

Behaviour: a thin wrapper around the real
``_scan_one_file_skillaudit`` worker that raises a synthetic
``RuntimeError`` on a file whose basename matches the value of
``CPV_SKILLAUDIT_TEST_FAIL_BASENAME``. All other files are scanned
normally by delegating to the real worker. This lets the
error-isolation test trigger ONE deterministic per-file failure
across a multi-file fixture without polluting the production worker
with test hooks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the production scripts/ folder importable when this module is
# re-loaded in a worker process (the worker has a fresh sys.path).
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def failing_worker(file_path: Path) -> list:
    """Per-file worker that raises on a specific basename.

    The basename to fail on is read from
    ``CPV_SKILLAUDIT_TEST_FAIL_BASENAME``. When the file's basename
    matches, this function raises ``RuntimeError`` with a recognisable
    message so the test can assert on it. All other files are
    delegated to the real production worker.
    """
    fail_basename = os.environ.get("CPV_SKILLAUDIT_TEST_FAIL_BASENAME", "")
    if fail_basename and file_path.name == fail_basename:
        raise RuntimeError(f"synthetic test failure on {fail_basename}")

    from cpv_skillaudit_native import _scan_one_file_skillaudit

    return _scan_one_file_skillaudit(file_path)
