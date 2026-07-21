#!/usr/bin/env python3
"""Canonical-pipeline PROFILE model (TRDD-e9f13df1, issues #130 / #118-d2).

CPV's cpv-canonical-pipeline drift detector historically assumed exactly ONE
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


def resolve_intentional_divergence(plugin_root: Path) -> frozenset[str]:
    """Return the repo-relative paths a plugin declares as INTENTIONALLY divergent.

    Read-only resolution of the OPTIONAL manifest key
    ``cpv.pipeline.intentional_divergence`` (issue #144Ba) — a list of
    repo-relative file paths the maintainer has deliberately customized away
    from the canonical template and does NOT want the upgrade flow to "fix".

    Shape in `.claude-plugin/plugin.json`::

        { "cpv": { "pipeline": { "intentional_divergence": ["cliff.toml", ".markdownlint.json"] } } }

    For each listed file the drift detector still EMITS an auditable
    informational note that the file is intentionally divergent (so the
    divergence is never invisible), but DROPS the "run `--force-templates` /
    `/cpv-upgrade-plugin`" recommendation — force-templating a deliberately
    customized shared-canon file would REGRESS it (the #144/#145 incident).
    The standardizer (Agent C2) reuses this reader to SKIP force-overwriting a
    declared-divergent file.

    SECURITY: this is a NUDGE selector, NOT a finding suppressor. It only
    suppresses the *upgrade recommendation* on a drifted file — the WARNING/
    note is still produced (visible + auditable), and it has no effect on the
    security scanner or any other validation. (The drift WARNING is advisory
    and non-blocking to begin with, so dropping its nudge cannot un-block a
    `--strict` run.)

    Resolution is best-effort + side-effect-free: ANY missing/malformed key
    yields the empty set (the conservative, no-behavior-change default).
    Non-string list elements are ignored individually; a non-list value yields
    the empty set. Paths are normalized to forward slashes (matching the
    ``_CANONICAL_PIPELINE_FILES`` rel-path spelling) so a Windows-authored
    manifest still matches.
    """
    manifest = _load_manifest(plugin_root)
    cpv_block = manifest.get("cpv")
    if not isinstance(cpv_block, dict):
        return frozenset()
    pipeline_block = cpv_block.get("pipeline")
    if not isinstance(pipeline_block, dict):
        return frozenset()
    declared = pipeline_block.get("intentional_divergence")
    if not isinstance(declared, list):
        return frozenset()
    # Keep only non-empty string entries; normalize separators to `/` so the
    # comparison against `_CANONICAL_PIPELINE_FILES` rel-paths is OS-agnostic.
    return frozenset(entry.replace("\\", "/").strip() for entry in declared if isinstance(entry, str) and entry.strip())


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


# ── binary-release CANONICAL-SHAPE recognition (#115 / Piece C2a) ───────────────
# A binary-release plugin's release workflow is inherently language/toolchain-
# specific (`cargo` vs `go build` vs `cmake`), so no single generated template
# can byte-match it. Recognition is therefore STRUCTURAL: a binary-release
# release workflow is CANONICAL iff it satisfies ALL FOUR invariants below,
# modelled on the janitor `memgrep-release.yml` reference shape:
#   1. SHA-PINNED third-party actions — every non-`actions/`-org `uses:` is
#      pinned to a 40-hex commit SHA (a floating `@v1`/`@main` on a third-party
#      action is a miss). `actions/`-org uses may use a major tag.
#   2. LEAST-PRIVILEGE split — the build job carries `permissions: contents:
#      read` (NO write) and EXACTLY ONE job carries `contents: write`.
#   3. A CHECKSUM step — produces or verifies `SHA256SUMS` (or per-asset
#      `.sha256`).
#   4. A build MATRIX over targets.
#
# This is a SELECTOR, never a SUPPRESSOR (TRDD-02e1672b): a CANONICAL workflow
# clears the "missing standard release.yml" drift flag (the standard byte-
# compare can never pass for a toolchain-specific build), but a DEFICIENT
# binary-release workflow — one missing any of the four — still WARNs, naming
# the missing requirement(s). Declaring/​detecting `binary-release` HOLDS the
# plugin to the binary-release canon; it can never silence a real finding.

# Human-readable identifiers for the four structural requirements. These exact
# strings are what `is_binary_release_canonical_shape` returns in its
# `missing_requirements` list and what the validators surface in the WARNING, so
# the reader knows precisely which invariant to add.
BR_REQ_SHA_PINNED_ACTIONS = "SHA-pinned third-party actions"
BR_REQ_LEAST_PRIVILEGE = "least-privilege permissions split (build job contents:read, exactly one job contents:write)"
BR_REQ_CHECKSUM = "a checksum step (SHA256SUMS or per-asset .sha256)"
BR_REQ_BUILD_MATRIX = "a build matrix over targets"

#: The four requirements, in a stable order (used for deterministic output).
BINARY_RELEASE_CANONICAL_REQUIREMENTS: tuple[str, ...] = (
    BR_REQ_SHA_PINNED_ACTIONS,
    BR_REQ_LEAST_PRIVILEGE,
    BR_REQ_CHECKSUM,
    BR_REQ_BUILD_MATRIX,
)

# A `uses:` line and its action reference. We capture the whole ref token and
# inspect it in Python (no regex lookbehind/lookahead — re2-safe). The ref may
# be quoted; the value-extractor strips surrounding quotes.
_USES_LINE_RE = re.compile(r"^\s*(?:-\s*)?uses\s*:\s*(?P<ref>\S+)", re.IGNORECASE | re.MULTILINE)
# A 40-hex git commit SHA after the `@` of a `uses:` ref (the canonical pin).
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
# The two `contents:` access lines we key the least-privilege split on. Each is
# strictly line-anchored (block-mapping style) and re2-safe.
_CONTENTS_WRITE_RE = re.compile(r"^\s*contents\s*:\s*write\b", re.IGNORECASE | re.MULTILINE)
_CONTENTS_READ_RE = re.compile(r"^\s*contents\s*:\s*read\b", re.IGNORECASE | re.MULTILINE)
# A per-asset `.sha256` checksum file token (the alternative to a combined
# SHA256SUMS). Word-anchored so it does not match a `.sha256sum` substring twice.
_PER_ASSET_SHA256_RE = re.compile(r"\.sha256\b", re.IGNORECASE)


def _uses_actions_are_sha_pinned(workflow_text: str) -> bool:
    """True iff every non-``actions/``-org ``uses:`` reference is SHA-pinned.

    For each ``uses: <owner>/<action>[/<sub>]@<ref>`` line:
      * a first-party ``actions/<x>@<tag>`` (GitHub's own org) may pin to a major
        tag and is always accepted;
      * any OTHER (third-party) action MUST pin to a 40-hex commit SHA after the
        ``@`` — a floating ``@v1`` / ``@main`` / ``@<branch>`` is a MISS.

    A LOCAL action (``uses: ./...`` — no ``@``, runs the repo's own checked-out
    code) and a ``docker://`` reference are not third-party registry pins and are
    accepted as-is. A workflow with NO ``uses:`` lines vacuously satisfies this
    requirement (there is no third-party action to pin). Side-effect-free; never
    raises.
    """
    for m in _USES_LINE_RE.finditer(workflow_text):
        ref = m.group("ref").strip().strip("'\"")
        # Local composite action or a Dockerfile-relative action: no registry
        # pin applies.
        if ref.startswith("./") or ref.startswith("../") or ref.startswith("docker://"):
            continue
        owner = ref.split("/", 1)[0].lower()
        # GitHub's own first-party org — a major tag is the accepted convention.
        if owner == "actions":
            continue
        # Third-party action: it MUST be SHA-pinned. A ref with no `@` (a bare
        # `owner/action`) is unpinned → a miss.
        if "@" not in ref:
            return False
        pin = ref.rsplit("@", 1)[1]
        if not _SHA40_RE.match(pin):
            return False
    return True


def _has_least_privilege_split(workflow_text: str) -> bool:
    """True iff the workflow has a least-privilege permissions split.

    Requires BOTH:
      * at least one ``contents: read`` permission line (the build job's
        read-only default), AND
      * EXACTLY ONE ``contents: write`` permission line (the single release job
        that needs write to upload assets).

    A workflow with zero ``contents: write`` (no job can write a release) or
    with two-or-more ``contents: write`` (write is over-broadly granted) FAILS
    this requirement. The check is line-level (it counts the access declarations
    that actually appear), so a top-level ``permissions: {}`` default plus
    per-job ``contents: read`` / a single ``contents: write`` matches the
    canonical memgrep-release shape. Side-effect-free; never raises.
    """
    write_count = len(_CONTENTS_WRITE_RE.findall(workflow_text))
    has_read = bool(_CONTENTS_READ_RE.search(workflow_text))
    return has_read and write_count == 1


def _has_checksum_step(workflow_text: str) -> bool:
    """True iff the workflow produces/verifies a ``SHA256SUMS`` or ``.sha256``."""
    return bool(_SHA256SUMS_RE.search(workflow_text) or _PER_ASSET_SHA256_RE.search(workflow_text))


def _has_build_matrix_over_targets(workflow_text: str) -> bool:
    """True iff the workflow has a build ``matrix`` over targets/platforms.

    Reuses the same matrix + target co-occurrence that ``is_binary_release_shape``
    keys on, so the canonical-shape recognition and the profile detection agree.
    """
    return bool(_BINARY_MATRIX_RE.search(workflow_text) and _BINARY_TARGET_RE.search(workflow_text))


def is_binary_release_canonical_shape(workflow_text: str) -> tuple[bool, list[str]]:
    """Recognize a CANONICAL binary-release release workflow, structurally.

    Returns ``(is_canonical, missing_requirements)``. ``is_canonical`` is True
    iff the workflow satisfies ALL FOUR structural invariants (SHA-pinned
    third-party actions, a least-privilege permissions split, a checksum step,
    and a build matrix over targets). ``missing_requirements`` lists exactly the
    requirements (from :data:`BINARY_RELEASE_CANONICAL_REQUIREMENTS`) that are
    NOT met — so an empty list ⟺ canonical.

    This is the SELECTOR (TRDD-02e1672b): the validators clear the false
    "missing standard release.yml" drift flag when this returns canonical, but a
    DEFICIENT workflow (a non-empty missing list) still WARNs, naming each
    missing requirement. It NEVER suppresses a real finding — a binary-release
    plugin is HELD to this canon. Best-effort + side-effect-free: any error
    treats every requirement as unmet (the conservative direction — a workflow
    we cannot parse is NOT recognized as canonical, so it keeps warning).
    """
    if not workflow_text:
        return (False, list(BINARY_RELEASE_CANONICAL_REQUIREMENTS))
    try:
        missing: list[str] = []
        if not _uses_actions_are_sha_pinned(workflow_text):
            missing.append(BR_REQ_SHA_PINNED_ACTIONS)
        if not _has_least_privilege_split(workflow_text):
            missing.append(BR_REQ_LEAST_PRIVILEGE)
        if not _has_checksum_step(workflow_text):
            missing.append(BR_REQ_CHECKSUM)
        if not _has_build_matrix_over_targets(workflow_text):
            missing.append(BR_REQ_BUILD_MATRIX)
        return (not missing, missing)
    except Exception:  # noqa: BLE001 — recognition is advisory; any failure treats the workflow as NON-canonical (keeps warning), never crashes
        return (False, list(BINARY_RELEASE_CANONICAL_REQUIREMENTS))


def binary_release_release_workflow(plugin_root: Path) -> Path | None:
    """Return the binary-release release workflow file, or None.

    The workflow that builds + attaches binary assets — the first
    ``.github/workflows/*.yml`` whose text satisfies
    :func:`is_binary_release_shape`'s co-occurrence (matrix + target + release
    upload + SHA256SUMS). Used by the validators to pick WHICH workflow to hold
    to the binary-release canon. Side-effect-free; returns None on any error or
    if no such workflow exists.
    """
    try:
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
                return wf
    except Exception:  # noqa: BLE001 — advisory selection; any failure → no workflow identified
        return None
    return None


def binary_release_canonical_status(plugin_root: Path) -> tuple[bool, list[str]]:
    """Recognize the plugin's binary-release release workflow as canonical or not.

    Convenience wrapper over :func:`binary_release_release_workflow` +
    :func:`is_binary_release_canonical_shape`. Returns
    ``(is_canonical, missing_requirements)`` for the plugin's binary-release
    release workflow. If NO binary-release workflow is present (the plugin
    doesn't actually have the shape), returns ``(False, [])`` — there is nothing
    to recognize and nothing missing to warn about; the standard drift path
    governs. Side-effect-free.
    """
    wf = binary_release_release_workflow(plugin_root)
    if wf is None:
        return (False, [])
    return is_binary_release_canonical_shape(_read_text_safe(wf))


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


# ── "untested-until-release" advisory heuristic (#115 part-5) ──────────────────
# A NON-BLOCKING WARNING surface (the validator wires these helpers to
# ``report.warning``). It flags a workflow that BUILDS/STAGES a compiled binary
# artifact in a step reachable ONLY from tag/release triggers (no push/PR path),
# so the build/stage breakage is invisible until a tag is cut. Born from the
# janitor v0.7.0 incident: a tag-only staging step copied from the wrong
# ``target/`` dir, passed actionlint+zizmor+CPV statically, and failed on all
# four platforms at release because no push/PR job ever exercised it.
#
# THE MAKE-OR-BREAK PRECISION GUARD: the STANDARD canonical ``release.yml``
# (``gen_release_yml``) is ALSO tag-triggered and ALSO runs
# ``gh release upload validation-report.txt sbom.spdx.json SHA256SUMS`` — it
# literally contains the strings ``gh release upload`` AND ``SHA256SUMS``. So
# keying on the upload / checksum alone would fire on EVERY canon plugin
# (catastrophic FP). The discriminator is a compiled-artifact BUILD/STAGE
# (a build MATRIX over targets, OR a compile step, OR a stage step that moves
# COMPILED artifacts), which the standard release.yml does NOT have (it stages
# plain text reports). Verified empirically against the real ``gen_release_yml``
# / ``gen_ci_yml`` output: every regex below returns no match on them.

# Trigger classification ─ which `on:` events are RELEASE-class vs CI-class.
#
# We classify by SHAPE of the `on:` mapping. `release-class` is a tag/release
# event that fires only when a version is cut: a `release:` event, a `push:`
# restricted to `tags:` with NO `branches:`, and `workflow_dispatch:` (a manual
# button — never a normal commit). `ci-class` is any event a regular push/PR
# exercises: a `push:` with `branches:` (or a bare `push`/`push:` with neither
# tags nor branches → fires on every branch push), `pull_request`,
# `pull_request_target`, `merge_group`, and `schedule`.
#
# A workflow is "release-only" iff it has ≥1 release-class trigger AND 0
# ci-class triggers — so its steps are NEVER exercised by a normal push/PR.
# Capture the `on:` mapping body WITHOUT a lookahead (re2-safe): match the
# same-line value (`inline` — e.g. ` [push, pull_request]` in flow style), then
# zero-or-more continuation lines that are blank or whitespace-indented (the
# block body), which naturally stops at the first column-0 non-space line (the
# next top-level key) because such a line matches neither alternative.
_ON_BLOCK_RE = re.compile(
    r"^on\s*:(?P<inline>[^\n]*)\n(?P<body>(?:[ \t][^\n]*\n|[ \t]*\n)*)",
    re.IGNORECASE | re.MULTILINE,
)
# Each trigger keyword is matched either as a block-mapping key (`^\s*push:`)
# OR as a flow-style list member (`on: [push, pull_request]` → `[push` / `,
# pull_request`). The `(?:^\s*|[\[,]\s*)` prefix is re2-safe (no lookbehind):
# the keyword must start a YAML line OR follow a `[`/`,` flow delimiter, so the
# word `push` inside `# a comment about push` or a step name never matches.
# The `[` is escaped inside the class (`[\[,]`) so it is an unambiguous
# two-member class, not a nested set.
_TRIGGER_RELEASE_EVENT_RE = re.compile(r"(?:^\s*|[\[,]\s*)release\b", re.IGNORECASE | re.MULTILINE)
_TRIGGER_WORKFLOW_DISPATCH_RE = re.compile(
    r"(?:^\s*|[\[,]\s*)workflow_dispatch\b", re.IGNORECASE | re.MULTILINE
)
_TRIGGER_PUSH_RE = re.compile(r"(?:^\s*|[\[,]\s*)push\b", re.IGNORECASE | re.MULTILINE)
_TRIGGER_PR_RE = re.compile(r"(?:^\s*|[\[,]\s*)pull_request(?:_target)?\b", re.IGNORECASE | re.MULTILINE)
_TRIGGER_MERGE_GROUP_RE = re.compile(r"(?:^\s*|[\[,]\s*)merge_group\b", re.IGNORECASE | re.MULTILINE)
_TRIGGER_SCHEDULE_RE = re.compile(r"(?:^\s*|[\[,]\s*)schedule\b", re.IGNORECASE | re.MULTILINE)
# Sub-keys WITHIN the `on:` block that qualify a `push:` event. These are only
# meaningful in block-mapping style (a flow-style `on: [push]` has neither), so
# they stay strictly line-anchored.
_ON_HAS_BRANCHES_RE = re.compile(r"^\s*branches(?:-ignore)?\s*:", re.IGNORECASE | re.MULTILINE)
_ON_HAS_TAGS_RE = re.compile(r"^\s*tags(?:-ignore)?\s*:", re.IGNORECASE | re.MULTILINE)

# Compiled-artifact BUILD signals — a compile step that turns source into a
# binary. Two regexes, both verified to NOT match `gen_release_yml`/`gen_ci_yml`:
#
# 1. _COMPILE_VERB_RE — VERB-BEARING tokens that are unambiguous build commands
#    anywhere they appear (a two-word command like `cargo build` / `docker
#    build` / `npm run build` is never English prose). Safe un-anchored.
# 2. _COMPILE_CMD_RE — BARE single-word build tools (`make`, `cmake`, `gcc`,
#    `clang`, `rustc`, …) that DO collide with English ("an attacker can make
#    Dependabot …"). These fire ONLY at a shell COMMAND POSITION — start of a
#    YAML line, after `run:`, or after a `&&`/`||`/`;`/`|`/`$(` shell separator
#    — so a bare `make`/`gcc` in a comment or a step NAME never matches. The
#    command-position prefix `(?:^|run:\s*|[|&;]\s*|\$\(\s*)` is re2-safe (no
#    lookbehind).
_COMPILE_VERB_RE = re.compile(
    r"\b(?:cargo|go)\s+build\b"
    r"|\bgo\s+install\b"
    r"|\bpyinstaller\b"
    r"|\bnuitka\b"
    r"|\bzig\s+build\b"
    r"|\bcmake\s+--build\b"
    r"|\bdocker\s+build\b|\bdocker\s+buildx\s+build\b"
    r"|\bnpm\s+run\s+build\b|\byarn\s+build\b|\bpnpm\s+run\s+build\b"
    r"|\b(?:\./)?gradlew?\b[^\n]*?\b(?:build|assemble|shadowjar)\b"
    r"|\bmvn\b[^\n]*?\b(?:package|install|verify)\b",
    re.IGNORECASE,
)
_COMPILE_CMD_RE = re.compile(
    r"(?:^|run:\s*|[|&;]\s*|\$\(\s*)"  # shell command position only (re2-safe)
    r"(?:cmake|make|rustc|gcc|g\+\+|clang\+\+|clang)\b",
    re.IGNORECASE | re.MULTILINE,
)
# Compiled-artifact STAGE signals — a step that copies/moves COMPILED artifacts
# into a release-staging dir, OR a `stage.sh`-style script that feeds the upload.
# Keys on compiled-output dir paths and binary extensions, NOT on text files.
_STAGE_STEP_RE = re.compile(
    r"\btarget/(?:release|debug)\b"  # cargo output dir
    r"|[\w./-]*\bdist/bin\b"
    r"|[\w./-]*\bbuild/(?:bin|lib|release)\b"
    r"|\.(?:so|dylib|dll|exe|a|o|wasm)\b"  # compiled binary/object extensions
    r"|\b[\w-]*stage(?:[-_]?bin(?:aries)?)?\.sh\b"  # stage.sh / stage-binaries.sh
    r"|\bcargo\s+install\b",
    re.IGNORECASE,
)


def classify_workflow_triggers(text: str) -> tuple[bool, bool]:
    """Classify a workflow's ``on:`` triggers into (has_release_class, has_ci_class).

    release-class = a tag/release/manual event that fires only when a version is
    cut (``release:``, ``push:`` restricted to ``tags:`` with no ``branches:``,
    ``workflow_dispatch``). ci-class = an event a normal push/PR exercises
    (``push:`` with ``branches:`` or a bare/unqualified ``push``,
    ``pull_request``/``pull_request_target``, ``merge_group``, ``schedule``).

    Best-effort + side-effect-free: an unparseable ``on:`` block (or a missing
    one) yields ``(False, False)`` — never raises.
    """
    has_release = False
    has_ci = False
    try:
        m = _ON_BLOCK_RE.search(text)
        # The block we classify is the `on:` mapping body: the same-line value
        # (``inline`` — the flow-style ``[push, pull_request]`` lives here) plus
        # the indented continuation lines (``body``). If we cannot isolate it,
        # classify over the whole file as a conservative fallback.
        on_block = (m.group("inline") + "\n" + m.group("body")) if m else text

        if _TRIGGER_RELEASE_EVENT_RE.search(on_block):
            has_release = True
        if _TRIGGER_WORKFLOW_DISPATCH_RE.search(on_block):
            has_release = True

        if _TRIGGER_PR_RE.search(on_block):
            has_ci = True
        if _TRIGGER_MERGE_GROUP_RE.search(on_block):
            has_ci = True
        if _TRIGGER_SCHEDULE_RE.search(on_block):
            has_ci = True

        if _TRIGGER_PUSH_RE.search(on_block):
            # A `push:` event is release-class ONLY when it is restricted to
            # tags with no branches. A `push:` with `branches:` is ci-class; a
            # bare `push` / `push:` with neither tags nor branches fires on
            # every branch push → ci-class too (conservative: when unsure, treat
            # a push as ci so a real CI build still counts as smoke coverage).
            has_tags = bool(_ON_HAS_TAGS_RE.search(on_block))
            has_branches = bool(_ON_HAS_BRANCHES_RE.search(on_block))
            if has_tags and not has_branches:
                has_release = True
            else:
                has_ci = True
    except Exception:  # noqa: BLE001 — trigger classification is advisory; any failure → (False, False)
        return (False, False)
    return (has_release, has_ci)


def workflow_has_compiled_artifact_build(text: str) -> bool:
    """True iff the workflow text contains a compiled-artifact BUILD or STAGE step.

    The discriminator that separates a real binary-build/stage workflow from the
    standard canonical ``release.yml`` (which only uploads text reports). Fires on:
      * a build MATRIX over targets/platforms (``is_binary_release_shape``'s
        matrix+target co-occurrence), OR
      * a compile step (``cargo build`` / ``go build`` / ``make <t>`` / ``cmake
        --build`` / ``pyinstaller`` / ``zig build`` / ``docker build`` / …), OR
      * a stage step that moves COMPILED artifacts (``target/release`` /
        ``dist/bin`` / ``*.so``/``*.dylib``/``*.exe`` / a ``stage.sh``-style
        script feeding the upload).

    Each signal is verified to NOT match ``gen_release_yml`` / ``gen_ci_yml``.
    Best-effort + side-effect-free.
    """
    if not text:
        return False
    try:
        if _BINARY_MATRIX_RE.search(text) and _BINARY_TARGET_RE.search(text):
            return True
        if _COMPILE_VERB_RE.search(text):
            return True
        if _COMPILE_CMD_RE.search(text):
            return True
        if _STAGE_STEP_RE.search(text):
            return True
    except Exception:  # noqa: BLE001 — signal scan is advisory; any failure → not a build/stage
        return False
    return False


def repo_has_ci_build_smoke(plugin_root: Path) -> bool:
    """True iff ANY ci-class-triggered workflow in the repo builds/stages a binary.

    The MITIGATION signal: if a push/PR-triggered workflow already exercises a
    compile/build/stage step (or invokes the shared stage script), then the
    binary build/stage is NOT untested-until-release — a broken step fails CI on
    the next push, long before a tag is cut. Best-effort + side-effect-free.
    """
    for wf in _workflow_yaml_files(plugin_root):
        text = _read_text_safe(wf)
        if not text:
            continue
        _has_release, has_ci = classify_workflow_triggers(text)
        if has_ci and workflow_has_compiled_artifact_build(text):
            return True
    return False


def untested_until_release_workflows(plugin_root: Path) -> list[Path]:
    """Every release-only workflow that builds/stages a binary with NO CI smoke.

    Returns the sorted list of ``.github/workflows/*.yml`` files that:
      1. are RELEASE-ONLY (≥1 release-class trigger AND 0 ci-class triggers),
      2. contain a compiled-artifact BUILD or STAGE step, and
      3. have NO sibling ci-class workflow in the repo that builds/stages a
         binary (the mitigation).

    The standard canonical ``release.yml`` is release-only and uploads
    ``SHA256SUMS``, but has NO build/stage step → it is NEVER returned (the
    catastrophic-FP guard). Best-effort + side-effect-free: any error → ``[]``.
    """
    try:
        wfs = _workflow_yaml_files(plugin_root)
        if not wfs:
            return []
        # Compute the repo-wide CI-smoke mitigation ONCE.
        if repo_has_ci_build_smoke(plugin_root):
            return []
        offenders: list[Path] = []
        for wf in wfs:
            text = _read_text_safe(wf)
            if not text:
                continue
            has_release, has_ci = classify_workflow_triggers(text)
            if has_release and not has_ci and workflow_has_compiled_artifact_build(text):
                offenders.append(wf)
        return sorted(offenders)
    except Exception:  # noqa: BLE001 — the whole heuristic is advisory; any failure → no findings
        return []
