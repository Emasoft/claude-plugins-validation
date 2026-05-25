#!/usr/bin/env python3
"""DEPRECATED — renamed to scripts/_plugin_verify_hashes.py in v2.51.0.

Compat shim — STILL SHIPPED at v2.105.0; removal originally planned for
v2.53.0 was deferred and has not yet happened. See TRDD-bbff5bc5
(publish.py auth standard). Re-exports every public name from
`_plugin_verify_hashes`.

This module is a thin re-export shim that forwards every public name
to `_plugin_verify_hashes`. A one-shot DeprecationWarning is emitted
on first import per process.

Why a shim instead of a hard redirect: external tooling (e.g. tests
using `subprocess.run([..., "scripts/cpv_integrity.py"])` or imports
written before the rename landed) keeps working, giving downstream
code time to migrate before the eventual removal.
"""

from __future__ import annotations

import warnings as _w

_w.warn(
    "scripts/cpv_integrity.py is renamed to scripts/_plugin_verify_hashes.py "
    "(TRDD-bbff5bc5). Update your import to `from _plugin_verify_hashes "
    "import …` — the legacy name is deprecated and will be removed in a "
    "future release (removal originally planned for v2.53.0 was deferred).",
    DeprecationWarning,
    stacklevel=2,
)

from _plugin_verify_hashes import (  # noqa: F401,E402  (re-export)
    CACHE_DIR,
    CACHE_TTL,
    HTTP_TIMEOUT_SEC,
    MANIFEST_FILE,
    REPO_NAME,
    REPO_OWNER,
    REPO_RAW_MAIN_URL,
    REPO_RAW_TAG_URL,
    USER_AGENT,
    fetch_canonical_manifest,
    main,
    verify_self_integrity,
)

# Explicit re-export list — silences Pyright "not accessed" diagnostics
# AND tells `from cpv_integrity import *` what to forward.
__all__ = [
    "CACHE_DIR",
    "CACHE_TTL",
    "HTTP_TIMEOUT_SEC",
    "MANIFEST_FILE",
    "REPO_NAME",
    "REPO_OWNER",
    "REPO_RAW_MAIN_URL",
    "REPO_RAW_TAG_URL",
    "USER_AGENT",
    "fetch_canonical_manifest",
    "main",
    "verify_self_integrity",
]


if __name__ == "__main__":
    import sys

    sys.exit(main())
