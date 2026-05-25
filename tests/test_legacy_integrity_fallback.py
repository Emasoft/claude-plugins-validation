"""Tests for the cpv_integrity / compute_cpv_self_hashes deprecation shims.

TRDD-bbff5bc5 §6.3: for ONE release (v2.51.0 → v2.52.0 inclusive), the old
module names re-export from the new canonical modules with a per-process
DeprecationWarning. v2.53.0 deletes the shims.

This test file pins the warning text + the re-export list so a downstream
contributor can't accidentally drop a public name during the cleanup
sprint without seeing this test fail.
"""

from __future__ import annotations

import importlib
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _fresh_import(modname: str):
    """Force a fresh import (so the module-level DeprecationWarning fires)."""
    if modname in sys.modules:
        del sys.modules[modname]
    return importlib.import_module(modname)


def test_cpv_integrity_shim_emits_deprecation_warning():
    """Importing `cpv_integrity` MUST print a DeprecationWarning that names
    the new module and references the (deferred) v2.53.0 removal target."""
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _fresh_import("cpv_integrity")
    msgs = [str(w.message) for w in captured if issubclass(w.category, DeprecationWarning)]
    assert any("TRDD-bbff5bc5" in m for m in msgs), f"DeprecationWarning missing TRDD reference; got: {msgs}"
    assert any("_plugin_verify_hashes" in m for m in msgs), f"DeprecationWarning doesn't name new module; got: {msgs}"
    assert any("v2.53.0" in m for m in msgs), f"DeprecationWarning doesn't mention removal release; got: {msgs}"


def test_compute_cpv_self_hashes_shim_emits_deprecation_warning():
    """Importing `compute_cpv_self_hashes` MUST print a DeprecationWarning."""
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        _fresh_import("compute_cpv_self_hashes")
    msgs = [str(w.message) for w in captured if issubclass(w.category, DeprecationWarning)]
    assert any("TRDD-bbff5bc5" in m for m in msgs), msgs
    assert any("_plugin_compute_hashes" in m for m in msgs), msgs
    assert any("v2.53.0" in m for m in msgs), msgs


def test_cpv_integrity_shim_re_exports_full_api():
    """The shim must re-export every public name the validators import.
    A regression here would break `from cpv_integrity import …` in
    downstream tooling for one full release before the shim is removed."""
    cpv_integrity = _fresh_import("cpv_integrity")
    expected_names = {
        "verify_self_integrity",
        "fetch_canonical_manifest",
        "main",
        "MANIFEST_FILE",
        "REPO_OWNER",
        "REPO_NAME",
        "REPO_RAW_TAG_URL",
        "REPO_RAW_MAIN_URL",
        "CACHE_DIR",
        "CACHE_TTL",
        "HTTP_TIMEOUT_SEC",
        "USER_AGENT",
    }
    missing = expected_names - set(dir(cpv_integrity))
    assert not missing, f"Shim is missing re-exports: {missing}"


def test_compute_cpv_self_hashes_shim_re_exports_full_api():
    """The compute shim must re-export every public name the test fixtures
    and publish.py reach for."""
    compute_shim = _fresh_import("compute_cpv_self_hashes")
    expected_names = {
        "compute_manifest",
        "write_manifest",
        "is_self_scan_eligible",
        "sha256_of_file",
        "main",
        "MANIFEST_NAME",
        "MANIFEST_NAME_NEW",
        "MANIFEST_NAME_LEGACY",
        "MANIFEST_VERSION",
    }
    missing = expected_names - set(dir(compute_shim))
    assert not missing, f"Shim is missing re-exports: {missing}"


def test_cpv_integrity_shim_verify_self_integrity_still_callable(monkeypatch):
    """Smoke: the shim's verify_self_integrity is callable AND returns the
    same value as the canonical module (single source of truth)."""
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    cpv_integrity = _fresh_import("cpv_integrity")
    canonical = _fresh_import("_plugin_verify_hashes")
    canonical._VERIFIED_THIS_PROCESS = False
    a = cpv_integrity.verify_self_integrity(quiet=True)
    canonical._VERIFIED_THIS_PROCESS = False
    b = canonical.verify_self_integrity(quiet=True)
    assert a == b is True
