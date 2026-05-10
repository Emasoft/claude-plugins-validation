#!/usr/bin/env python3
"""DEPRECATED — renamed to scripts/_plugin_compute_hashes.py in v2.51.0.

Removed in v2.53.0. See TRDD-bbff5bc5 (publish.py auth standard).

This module is a thin re-export shim that forwards every public name
to `_plugin_compute_hashes`. A one-shot DeprecationWarning is emitted
on first import per process.

The new `_plugin_compute_hashes.py` writes BOTH `.plugin-self-hashes.json`
(canonical) AND `.cpv-self-hashes.json` (legacy compat copy) on every
invocation, so existing publish pipelines that invoke this script via
its old path continue to produce both files.
"""

from __future__ import annotations

import sys as _sys
import warnings as _w
from pathlib import Path as _Path

_w.warn(
    "scripts/compute_cpv_self_hashes.py is renamed to scripts/_plugin_compute_hashes.py "
    "(TRDD-bbff5bc5). Update your invocation to use the new path — the legacy name "
    "is removed in v2.53.0.",
    DeprecationWarning,
    stacklevel=2,
)

# Make the new sibling module importable when this shim is loaded by
# absolute path (e.g. publish.py invokes `python scripts/compute_cpv_self_hashes.py`)
# — sys.path may not contain scripts/ in that case.
_SCRIPTS_DIR = _Path(__file__).parent
if str(_SCRIPTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS_DIR))

from _plugin_compute_hashes import (  # noqa: F401,E402  (re-export)
    MANIFEST_NAME_LEGACY,
    MANIFEST_NAME_NEW,
    MANIFEST_VERSION,
    compute_manifest,
    is_self_scan_eligible,
    main,
    sha256_of_file,
    write_manifest,
)

# Backwards-compat constant (some test fixtures reference MANIFEST_NAME).
MANIFEST_NAME = MANIFEST_NAME_LEGACY

__all__ = [
    "MANIFEST_NAME",
    "MANIFEST_NAME_LEGACY",
    "MANIFEST_NAME_NEW",
    "MANIFEST_VERSION",
    "compute_manifest",
    "is_self_scan_eligible",
    "main",
    "sha256_of_file",
    "write_manifest",
]


if __name__ == "__main__":
    _sys.exit(main())
