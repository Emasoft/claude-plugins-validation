"""Regression tests for the three integrity/common audit NITs (#8, #9, #10).

Source: reports/audit/20260525_101621+0200-common-integrity-caches.md

- #8  `_plugin_compute_hashes._atomic_write` now fsyncs the tmp file before
      the atomic rename (durability parity with cpv_scanner_cache.put).
- #9  `_minimal_yaml._parse_inline_list` now splits inline-list bodies on
      top-level commas only, honoring quotes (`[a, "b,c"]` -> 2 items).
- #10 The `cpv_integrity` / `compute_cpv_self_hashes` deprecation shims no
      longer claim a false past-tense "Removed in v2.53.0" in their
      docstrings, while still re-exporting their full public API.

Every NIT gets a TWO-SIDED test: the fix produces the corrected behavior
AND the previously-working behavior still holds (no regression).
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import _minimal_yaml  # noqa: E402
import _plugin_compute_hashes  # noqa: E402
from _minimal_yaml import YAMLError, safe_load  # noqa: E402

# ---------------------------------------------------------------------------
# #8 — manifest _atomic_write fsyncs before rename
# ---------------------------------------------------------------------------


def test_atomic_write_produces_correct_content(tmp_path: Path) -> None:
    """The fsync-hardened writer must still write exactly the payload bytes."""
    out = tmp_path / "manifest.json"
    payload = json.dumps({"version": 1, "files": {"a": "deadbeef"}}, indent=2) + "\n"
    _plugin_compute_hashes._atomic_write(out, payload)
    assert out.read_text(encoding="utf-8") == payload


def test_atomic_write_leaves_no_tmp_leftover(tmp_path: Path) -> None:
    """After a successful write the .tmp sidecar must be gone (renamed away)."""
    out = tmp_path / "manifest.json"
    _plugin_compute_hashes._atomic_write(out, "payload\n")
    tmp_sidecar = out.with_suffix(out.suffix + ".tmp")
    assert not tmp_sidecar.exists(), "atomic rename should consume the tmp file"
    # Only the final file remains in the directory.
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]


def test_atomic_write_uses_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The durability fix MUST route through os.fsync before the rename.

    We assert the fsync path actually runs (vs. the old write_text+replace that
    skipped it). Power-loss itself is untestable; running the fsync code path
    without error, on a real fd, is the observable proxy.
    """
    fsync_calls: list[int] = []
    real_fsync = _plugin_compute_hashes.os.fsync

    def _spy_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(_plugin_compute_hashes.os, "fsync", _spy_fsync)
    out = tmp_path / "manifest.json"
    _plugin_compute_hashes._atomic_write(out, "durable\n")
    assert fsync_calls, "_atomic_write must call os.fsync before renaming the tmp file"
    assert out.read_text(encoding="utf-8") == "durable\n"


def test_atomic_write_overwrites_existing_file(tmp_path: Path) -> None:
    """Rewriting an existing manifest must replace it atomically with new content."""
    out = tmp_path / "manifest.json"
    _plugin_compute_hashes._atomic_write(out, "old\n")
    _plugin_compute_hashes._atomic_write(out, "new\n")
    assert out.read_text(encoding="utf-8") == "new\n"
    assert not out.with_suffix(out.suffix + ".tmp").exists()


def test_write_manifest_round_trips_through_atomic_write(tmp_path: Path) -> None:
    """End-to-end: write_manifest emits valid JSON to both files via the fsync writer."""
    manifest = _plugin_compute_hashes.compute_manifest(REPO_ROOT)
    new_path, legacy_path = _plugin_compute_hashes.write_manifest(tmp_path, manifest)
    for p in (new_path, legacy_path):
        loaded = json.loads(p.read_text(encoding="utf-8"))
        assert loaded["version"] == _plugin_compute_hashes.MANIFEST_VERSION
        assert isinstance(loaded["files"], dict)
        assert not p.with_suffix(p.suffix + ".tmp").exists()
    # Both copies are byte-identical (the compat-copy invariant).
    assert new_path.read_text(encoding="utf-8") == legacy_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# #9 — inline-list parser honors quotes around commas
# ---------------------------------------------------------------------------


def test_inline_list_quoted_comma_is_one_item() -> None:
    """`[a, "b,c"]` must parse to exactly TWO items, not three (the NIT)."""
    assert _minimal_yaml._parse_inline_list('[a, "b,c"]') == ["a", "b,c"]


def test_inline_list_single_quoted_comma_is_one_item() -> None:
    """Single-quoted tokens get the same comma protection as double-quoted."""
    assert _minimal_yaml._parse_inline_list("[a, 'b,c', d]") == ["a", "b,c", "d"]


def test_inline_list_plain_still_splits_on_every_comma() -> None:
    """No-regression: an unquoted `[a, b, c]` still yields THREE items."""
    assert _minimal_yaml._parse_inline_list("[a, b, c]") == ["a", "b", "c"]


def test_inline_list_empty_returns_empty() -> None:
    """No-regression: an empty inline list returns []."""
    assert _minimal_yaml._parse_inline_list("[]") == []


def test_inline_list_nested_unquoted_still_raises() -> None:
    """No-regression: an UNquoted nested flow sequence still raises YAMLError."""
    with pytest.raises(YAMLError):
        _minimal_yaml._parse_inline_list("[a, [b]]")


def test_inline_list_unterminated_quote_raises() -> None:
    """A dangling quote must fail fast rather than silently swallow the rest."""
    with pytest.raises(YAMLError):
        _minimal_yaml._parse_inline_list('[a, "b]')


def test_safe_load_inline_list_with_quoted_comma() -> None:
    """Through the public safe_load entry point, quoted commas survive too."""
    out = safe_load('blocked-by: [TRDD-aaa, "x,y"]')
    assert out == {"blocked-by": ["TRDD-aaa", "x,y"]}


def test_safe_load_inline_list_matches_pyyaml_on_quoted_comma() -> None:
    """The fixed parser must agree with pyyaml for the quoted-comma case."""
    real_yaml = pytest.importorskip("yaml")
    doc = 'tags: [a, "b,c", d]'
    assert safe_load(doc) == real_yaml.safe_load(doc)


# ---------------------------------------------------------------------------
# #10 — deprecation shims: docstring truth + intact re-exports
# ---------------------------------------------------------------------------


def _fresh_import(modname: str):
    if modname in sys.modules:
        del sys.modules[modname]
    return importlib.import_module(modname)


@pytest.mark.parametrize("modname", ["cpv_integrity", "compute_cpv_self_hashes"])
def test_shim_docstring_drops_false_removed_claim(modname: str) -> None:
    """The shim docstrings must NOT claim a past-tense removal that never happened.

    Pre-fix both said `Removed in v2.53.0.` (past tense) while the files still
    ship at v2.105.0. The corrected docstring may still REFERENCE v2.53.0 as a
    deferred/originally-planned removal, but must not assert it already happened.
    """
    mod = _fresh_import(modname)
    doc = inspect.getdoc(mod) or ""
    assert "Removed in v2.53.0" not in doc, (
        f"{modname} docstring still makes the false past-tense removal claim"
    )
    # The shim is honest that it is still a live compat shim.
    assert "shim" in doc.lower(), f"{modname} docstring should describe itself as a compat shim"


def test_cpv_integrity_shim_re_exports_full_api() -> None:
    """No-regression: the verify shim still re-exports its full public API."""
    mod = _fresh_import("cpv_integrity")
    expected = {
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
    missing = expected - set(dir(mod))
    assert not missing, f"cpv_integrity shim lost re-exports: {missing}"
    # The headline callable must still be callable.
    assert callable(mod.verify_self_integrity)
    assert callable(mod.main)


def test_compute_cpv_self_hashes_shim_re_exports_full_api() -> None:
    """No-regression: the compute shim still re-exports its full public API."""
    mod = _fresh_import("compute_cpv_self_hashes")
    expected = {
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
    missing = expected - set(dir(mod))
    assert not missing, f"compute_cpv_self_hashes shim lost re-exports: {missing}"
    assert callable(mod.compute_manifest)
    assert callable(mod.write_manifest)
    assert callable(mod.is_self_scan_eligible)
