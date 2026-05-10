#!/usr/bin/env python3
"""Hash-manifest format v2 helpers.

TRDD-9065109a Phase G — extends the existing v1 manifest schema (used by
`scripts/_plugin_compute_hashes.py` and verified by
`scripts/_plugin_verify_hashes.py`) with three optional blocks:

  - `format`     — explicit format tag, value `"cpv-hash-manifest-v2"`.
                   Lets verifiers branch on schema without sniffing keys.
  - `git`        — `{tag, sha, remote}` of the manifest's source commit.
                   Useful for reproducing the manifest from any clone.
  - `submodules` — `{path: {url, sha, purpose}}` for submodule-bundle
                   plugins (perfect-skill-suggester case): captures the
                   exact source-of-truth commit each bundled binary was
                   built from.

Backward compatibility contract:
  - Existing v1 manifests STAY VALID. The verifier accepts both v1 and v2
    via `normalize_to_files_dict()`.
  - The current writer at `scripts/_plugin_compute_hashes.py` keeps emitting
    v1 by default. v2 is opt-in: callers that have git/submodule info pass
    it to `build_v2_manifest()` and write the result themselves.
  - When `git` and `submodules` are both absent, `build_v2_manifest()`
    still returns a v2 dict (with `version: 2` + `format` tag), but the
    inner shape is otherwise indistinguishable from v1. This means a
    plugin that opts into v2 without using the new fields gets the
    schema-tag promotion for free.

Why a separate module instead of editing `_plugin_compute_hashes.py`:
  - The legacy module is widely vendored across CPV-generated plugins.
    Touching it forces every downstream plugin to refresh, which is
    exactly the migration cost this TRDD is trying to amortize.
  - Keeping v2 in its own module lets us ship the schema spec NOW while
    the writer migration happens at its own pace (Phase H of the TRDD).
  - Pure functions are easier to reason about and easier to extract into
    `cpv.hash_manifest` later when the package layout lands.
"""

from __future__ import annotations

from typing import Any, Mapping

# Schema tag — exact string a v2 manifest carries in its `format` field.
# Bumping this constant is a breaking change; downstream verifiers branch
# on it, so any new value needs a coordinated rollout.
V2_FORMAT_TAG = "cpv-hash-manifest-v2"

# Supported manifest versions. `normalize_to_files_dict` accepts any of
# these and rejects everything else, so an unknown version (e.g. v3
# released by a future plugin) fails loudly instead of silently misreading
# the file as v2.
SUPPORTED_VERSIONS = frozenset({1, 2})


def build_v2_manifest(
    v1_manifest: Mapping[str, Any],
    *,
    git: Mapping[str, Any] | None = None,
    submodules: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Promote a v1 manifest dict to v2.

    The v1 fields (`computed_at`, `purpose`, `files`) carry across
    verbatim. `version` flips to 2 and `format` is set to V2_FORMAT_TAG.
    Optional `git` / `submodules` blocks are added only when provided —
    omitting them yields a minimal v2 manifest that is otherwise
    indistinguishable from v1.

    Pure function — does NOT mutate `v1_manifest`. Returns a fresh dict
    so callers can serialize without worrying about aliasing.
    """
    out: dict[str, Any] = {
        "version": 2,
        "format": V2_FORMAT_TAG,
    }

    # Carry across the v1 fields. Use .get() so a malformed v1 (missing
    # `computed_at` or `purpose`) doesn't crash here — the verifier will
    # catch the structural issue when it tries to read the manifest.
    if "computed_at" in v1_manifest:
        out["computed_at"] = v1_manifest["computed_at"]
    if "computed_by" in v1_manifest:
        out["computed_by"] = v1_manifest["computed_by"]
    if "purpose" in v1_manifest:
        out["purpose"] = v1_manifest["purpose"]
    out["files"] = dict(v1_manifest.get("files", {}))

    # Optional blocks: only emitted when provided. Defensive copies so the
    # caller's dicts can't be mutated through the returned manifest.
    if git is not None:
        out["git"] = dict(git)
    if submodules is not None:
        out["submodules"] = {key: dict(value) for key, value in submodules.items()}

    return out


def normalize_to_files_dict(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Read a v1 OR v2 manifest and return its `files` map.

    Verifiers call this to get the raw {path: sha256-string} mapping
    without caring which schema version produced the file. Raises:
      - KeyError when `version` is missing entirely
      - ValueError when `version` is present but unsupported

    The files dict is returned as a fresh `dict[str, str]` (copied) so
    the caller can mutate it freely.
    """
    if "version" not in manifest:
        raise KeyError("manifest missing required field 'version'")
    version = manifest["version"]
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"unsupported manifest version: {version!r} (supported: {sorted(SUPPORTED_VERSIONS)})")
    files = manifest.get("files", {})
    if not isinstance(files, Mapping):
        raise ValueError(f"manifest 'files' must be an object, got {type(files).__name__}")
    # Materialize to a plain dict[str, str] so the caller can mutate
    # without affecting the parsed-on-disk view.
    return {str(k): str(v) for k, v in files.items()}


def detect_format_version(manifest: Mapping[str, Any]) -> int | None:
    """Return the manifest's `version` field if present, else None.

    Convenience for callers that want to branch on schema before deciding
    whether to call `normalize_to_files_dict()` (which raises). Returning
    None for a malformed manifest avoids a bare KeyError leaking out of
    a quick "what version is this?" check.
    """
    return manifest.get("version") if isinstance(manifest, Mapping) else None


__all__ = [
    "SUPPORTED_VERSIONS",
    "V2_FORMAT_TAG",
    "build_v2_manifest",
    "detect_format_version",
    "normalize_to_files_dict",
]
