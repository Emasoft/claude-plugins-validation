#!/usr/bin/env python3
"""Canonical-pipeline PROFILE model (TRDD-e9f13df1, issues #130 / #118-d2).

CPV's canonical-pipeline drift detector historically assumed exactly ONE
"standard vendored" pipeline shape: every plugin vendors the CPV validator
scripts and ships the standard `gen_*` workflows, and any byte-difference is
"drift" the maintainer is told to migrate away from with `--force-templates`.

That single-shape assumption mis-fires on plugins whose architecture
LEGITIMATELY diverges from the standard shape:

  * **remote-validation** (#130, CAA) — the plugin de-vendored ALL local CPV
    validator scripts; validation is ONLY the remote `uvx … cpv-remote-validate
    … --strict` gate, run identically in publish.py / hooks / CI. The ABSENCE
    of the vendored validators is the WHOLE POINT — not a gap.
  * **submodule-build** (#128, PSS) — build sources live in a git submodule
    (e.g. `rust/`), pre-compiled binaries are committed to `bin/`, and a
    submodule-aware publish.py drives an N-file synchronized version bump.
  * **binary-release** (#115, janitor) — ships compiled binaries as RELEASE
    ASSETS via a build MATRIX + a shared stage script + a CI smoke job +
    `SHA256SUMS`.

This module resolves the plugin's PRIMARY profile so the drift detector can
compare each pipeline file against the *profile-appropriate* canon, and so
`validate_pipeline_readiness` stops reporting intentionally-absent vendored
validators as "missing".

CRITICAL SECURITY INVARIANT — the profile is a **SELECTOR, never a SUPPRESSOR**
(TRDD-02e1672b). The removed `cpv.allow_pipeline_drift` key was a suppressor: a
malicious author could list every drifted file and self-approve. A profile
selector cannot silence anything — declaring `remote-validation` HOLDS the
plugin to the remote-validation canon, which still enforces SHA-pins,
least-privilege permissions, the notify chain, version consistency, and atomic
push. This module therefore ONLY decides WHICH canon a file is compared
against; it never decides WHETHER a finding fires.

Detection is best-effort and side-effect-free: ANY error, exception, or
ambiguity falls back to `standard` (the conservative choice — current
behavior, no suppression). The manifest key `cpv.pipeline_profile` is an
authoritative OVERRIDE of detection, but it too is only a selector: a declared
profile is still held to that profile's full canon.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# ── The profile vocabulary ────────────────────────────────────────────────
# Exactly one PRIMARY profile per plugin. `cron-daemon` is an orthogonal trait
# (heartbeat/daemon runtime) and is NOT a value here — it only relaxes
# test-gate expectations elsewhere and never changes the pipeline file set.
PROFILE_STANDARD = "standard"
PROFILE_REMOTE_VALIDATION = "remote-validation"
PROFILE_SUBMODULE_BUILD = "submodule-build"
PROFILE_BINARY_RELEASE = "binary-release"

#: The set of recognized profile values. A `cpv.pipeline_profile` override
#: outside this set is ignored (detection runs instead) — an unknown string is
#: never honored, so a typo can never silently disable the standard canon.
KNOWN_PROFILES: frozenset[str] = frozenset(
    {
        PROFILE_STANDARD,
        PROFILE_REMOTE_VALIDATION,
        PROFILE_SUBMODULE_BUILD,
        PROFILE_BINARY_RELEASE,
    }
)

# ── Detection signatures ──────────────────────────────────────────────────
# Vendored CPV validator scripts. Their PRESENCE means the plugin runs CPV
# locally (standard / submodule-build / binary-release); their ABSENCE
# combined with a remote-gate invocation means remote-validation. This list is
# the set of scripts a `standardize --force-templates` would (re-)vendor — the
# exact thing a de-vendored plugin's invariant forbids (issue #118 defect 3).
_VENDORED_VALIDATOR_SCRIPTS: tuple[str, ...] = (
    "validate_plugin.py",
    "cpv_lint_engine.py",
    "cpv_network_resilience.py",
    "cpv_validation_common.py",
    "lint_files.py",
)

# Token that proves a file drives the REMOTE CPV gate. Matches both the CLI
# entry-point (`cpv-remote-validate`) and the module name (`cpv_remote_validate`
# / `remote_validation`). Used on publish.py and the workflow YAML.
_REMOTE_GATE_RE = re.compile(r"cpv[-_]remote[-_]validate|remote_validation")

# A committed `bin/` directory holding prebuilt artifacts. We treat any
# non-empty, non-hidden regular file under `bin/` as a candidate prebuilt
# artifact (a script shim there is still a shipped runtime binary; the
# submodule-build signal additionally requires a build-source submodule, so a
# lone `bin/foo.sh` shim never trips the profile on its own).
_BIN_DIR = "bin"

# Conventional names for a build-source submodule path. The strip-dev-parts
# feature already models a per-plugin `tests/` → submodule; that one is a DEV
# submodule, not a BUILD-SOURCE submodule, so it must NOT trip submodule-build.
# We treat a submodule whose path is (or contains a leading segment of) one of
# these as a build-source submodule.
_BUILD_SOURCE_SUBMODULE_HINTS: frozenset[str] = frozenset(
    {"rust", "src", "go", "cpp", "c", "native", "core", "lib", "crate", "crates"}
)

# Submodule path segments that mark a DEV/test submodule (strip-dev-parts) —
# these are explicitly NOT build-source submodules.
_DEV_SUBMODULE_HINTS: frozenset[str] = frozenset(
    {"tests", "test", "dev", "docs", "doc", "examples", "samples", "fixtures"}
)


def _read_text_safe(path: Path) -> str:
    """Read a file as text, returning "" on any error. Side-effect-free."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _load_manifest(plugin_root: Path) -> dict[str, object]:
    """Load `.claude-plugin/plugin.json` as a dict, or {} on any error."""
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def manifest_profile_override(plugin_root: Path) -> str | None:
    """Return the `cpv.pipeline_profile` manifest override, or None.

    Returns the declared profile string ONLY when it is a recognized value in
    ``KNOWN_PROFILES``. An unknown / malformed / non-string value returns None
    so detection runs instead — a typo can never silently disable the standard
    canon, and the override can never be a value that exists outside the
    enforced-canon set.

    This is a SELECTOR override: it chooses WHICH canon to compare against. It
    never suppresses a finding (the chosen canon is still fully enforced).
    """
    manifest = _load_manifest(plugin_root)
    cpv_block = manifest.get("cpv")
    if not isinstance(cpv_block, dict):
        return None
    declared = cpv_block.get("pipeline_profile")
    if isinstance(declared, str) and declared in KNOWN_PROFILES:
        return declared
    return None


def _gitmodules_submodule_paths(plugin_root: Path) -> list[str]:
    """Every `path = <rel>` entry in the plugin's own `.gitmodules`.

    Reads ONLY the plugin's own `.gitmodules` (not a parent repo's) — a
    build-source submodule is registered by the plugin itself.
    """
    gm = plugin_root / ".gitmodules"
    if not gm.is_file():
        return []
    paths: list[str] = []
    for line in _read_text_safe(gm).splitlines():
        m = re.match(r"^\s*path\s*=\s*(.+?)\s*$", line)
        if m:
            paths.append(m.group(1).strip().rstrip("/"))
    return paths


def has_build_source_submodule(plugin_root: Path) -> bool:
    """True iff the plugin registers a BUILD-SOURCE submodule in `.gitmodules`.

    Distinguishes a build-source submodule (e.g. `rust/`, `src/`) from a
    strip-dev-parts DEV submodule (e.g. `dev/tests/`). The leading path segment
    must be a build-source hint AND must not be a dev hint. A submodule whose
    leading segment is a dev hint (tests/dev/docs) never counts.
    """
    for sub_path in _gitmodules_submodule_paths(plugin_root):
        if not sub_path:
            continue
        head = sub_path.split("/", 1)[0].lower()
        if head in _DEV_SUBMODULE_HINTS:
            continue
        if head in _BUILD_SOURCE_SUBMODULE_HINTS:
            return True
    return False


def has_committed_bin_artifacts(plugin_root: Path) -> bool:
    """True iff a non-empty `bin/` directory with prebuilt artifacts exists.

    A prebuilt artifact is any regular, non-hidden file directly under `bin/`.
    Hidden files (`.DS_Store`, `.gitkeep`) are ignored so an empty-but-tracked
    `bin/` never trips the signal. Side-effect-free.
    """
    bin_dir = plugin_root / _BIN_DIR
    if not bin_dir.is_dir():
        return False
    try:
        for entry in bin_dir.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.is_file():
                return True
    except OSError:
        return False
    return False


def _workflow_yaml_files(plugin_root: Path) -> list[Path]:
    """Every `.github/workflows/*.yml|*.yaml` file (sorted, deterministic)."""
    wf_dir = plugin_root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    files: list[Path] = []
    try:
        files.extend(sorted(wf_dir.glob("*.yml")))
        files.extend(sorted(wf_dir.glob("*.yaml")))
    except OSError:
        return []
    return files


def _publish_py_text(plugin_root: Path) -> str:
    """The plugin's `scripts/publish.py` content, or "" if absent."""
    return _read_text_safe(plugin_root / "scripts" / "publish.py")


def invokes_remote_gate(plugin_root: Path) -> bool:
    """True iff publish.py OR any workflow invokes the remote CPV gate.

    Matches `cpv-remote-validate` / `cpv_remote_validate` / `remote_validation`
    in scripts/publish.py or any `.github/workflows/*.yml`. This is the
    positive signal that the plugin drives the REMOTE gate rather than (or in
    addition to) a vendored validator.
    """
    if _REMOTE_GATE_RE.search(_publish_py_text(plugin_root)):
        return True
    for wf in _workflow_yaml_files(plugin_root):
        if _REMOTE_GATE_RE.search(_read_text_safe(wf)):
            return True
    return False


def vendored_validators_present(plugin_root: Path) -> bool:
    """True iff ANY vendored CPV validator script exists under scripts/.

    The PRESENCE of even one of these means the plugin runs CPV locally — it is
    NOT a de-vendored remote-validation plugin. Their absence (combined with a
    remote-gate invocation) is the remote-validation signature.
    """
    scripts_dir = plugin_root / "scripts"
    if not scripts_dir.is_dir():
        return False
    for name in _VENDORED_VALIDATOR_SCRIPTS:
        if (scripts_dir / name).is_file():
            return True
    return False


def is_remote_validation_shape(plugin_root: Path) -> bool:
    """True iff the plugin matches the remote-validation (de-vendored) shape.

    Signature (#130): a remote-gate invocation in publish.py / workflows AND
    the ABSENCE of every vendored CPV validator script. The de-vendoring is the
    defining trait — a plugin that both invokes the remote gate AND keeps a
    vendored validator is `standard` (it has a local validator), not
    remote-validation.
    """
    return invokes_remote_gate(plugin_root) and not vendored_validators_present(plugin_root)


# Tokens that mark a release workflow producing BINARY ASSETS. We require ALL
# THREE shapes to co-occur in a single workflow file so a vanilla release.yml
# (which has none of them) never trips the profile, and a workflow that merely
# uploads a non-binary asset does not either:
#   1. a build MATRIX (matrix over targets/platforms),
#   2. a `gh release upload` (or `softprops/action-gh-release`) of assets,
#   3. a `SHA256SUMS` checksum step.
_BINARY_MATRIX_RE = re.compile(r"\bmatrix\s*:", re.IGNORECASE)
_BINARY_TARGET_RE = re.compile(
    r"\btargets?\s*:|\bplatform\s*:|\bgoos\b|\bgoarch\b|--target\b|"
    r"x86_64-|aarch64-|apple-darwin|unknown-linux|pc-windows",
    re.IGNORECASE,
)
_RELEASE_UPLOAD_RE = re.compile(
    r"gh\s+release\s+upload|softprops/action-gh-release|svenstaro/upload-release-action",
    re.IGNORECASE,
)
_SHA256SUMS_RE = re.compile(r"SHA256SUMS", re.IGNORECASE)


def is_binary_release_shape(plugin_root: Path) -> bool:
    """True iff a release workflow builds + attaches BINARY release assets.

    Signature (#115): a single `.github/workflows/*.yml` containing a build
    MATRIX over targets/platforms, a `gh release upload` (or equivalent) of
    assets, AND a `SHA256SUMS` checksum step. All three must co-occur in the
    SAME workflow file so a standard release.yml never matches.
    """
    for wf in _workflow_yaml_files(plugin_root):
        text = _read_text_safe(wf)
        if not text:
            continue
        if (
            _BINARY_MATRIX_RE.search(text)
            and _BINARY_TARGET_RE.search(text)
            and _RELEASE_UPLOAD_RE.search(text)
            and _SHA256SUMS_RE.search(text)
        ):
            return True
    return False


def is_submodule_build_shape(plugin_root: Path) -> bool:
    """True iff the plugin builds from a submodule + ships committed binaries.

    Signature (#128): a build-source submodule registered in `.gitmodules`
    (NOT a strip-dev-parts dev submodule) AND a committed `bin/` with prebuilt
    artifacts. Both are required so neither a lone dev submodule nor a lone
    `bin/` shim trips the profile.
    """
    return has_build_source_submodule(plugin_root) and has_committed_bin_artifacts(plugin_root)


def detect_pipeline_profile(plugin_root: Path) -> str:
    """Detect the plugin's PRIMARY pipeline profile by SHAPE (no manifest).

    First-match-wins ordering, most-specific first:
      1. remote-validation — de-vendored + remote gate (the strongest signal;
         a de-vendored plugin cannot also be submodule/binary in the
         vendored-canon sense, so this is checked first).
      2. submodule-build — build-source submodule + committed bin/.
      3. binary-release — matrix build + release-asset upload + SHA256SUMS.
      4. standard — everything else (the conservative default).

    Best-effort and side-effect-free: any exception → `standard`.
    """
    try:
        if is_remote_validation_shape(plugin_root):
            return PROFILE_REMOTE_VALIDATION
        if is_submodule_build_shape(plugin_root):
            return PROFILE_SUBMODULE_BUILD
        if is_binary_release_shape(plugin_root):
            return PROFILE_BINARY_RELEASE
    except Exception:  # noqa: BLE001 — detection is advisory; any failure must fall back to the conservative `standard`, never crash the run
        return PROFILE_STANDARD
    return PROFILE_STANDARD


def resolve_pipeline_profile(plugin_root: Path) -> str:
    """Resolve the plugin's pipeline profile (manifest override → detection).

    1. If `plugin.json` → `cpv.pipeline_profile` is a known value, RETURN it
       (authoritative SELECTOR override — but the chosen canon stays fully
       enforced; this is not a suppressor).
    2. Else detect by shape via :func:`detect_pipeline_profile`.
    3. Any error / ambiguity → `standard` (fail SAFE — current behavior, no
       suppression).

    Returns one of the values in :data:`KNOWN_PROFILES`.
    """
    try:
        override = manifest_profile_override(plugin_root)
        if override is not None:
            return override
        return detect_pipeline_profile(plugin_root)
    except Exception:  # noqa: BLE001 — resolution must never crash a validation run; fall back to the conservative `standard`
        return PROFILE_STANDARD
