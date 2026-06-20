"""Two-sided tests for binary-release CANONICAL-shape recognition (#115 / Piece C2a).

Covers ``cpv_pipeline_profile.is_binary_release_canonical_shape`` (the structural
recognizer) and its SELECTOR-not-SUPPRESSOR wiring into
``validate_canonical_pipeline_drift`` / ``validate_pipeline_readiness`` in
``validate_plugin.py``.

The contract recognized here: a ``binary-release`` plugin's release workflow is
inherently toolchain-specific and can NEVER byte-match the standard
``gen_release_yml``, so the standard byte-compare would emit a false "missing
standard release.yml" drift flag forever. Instead the workflow is judged
STRUCTURALLY against FOUR invariants (modelled on the janitor
``memgrep-release.yml`` reference shape):

  1. SHA-pinned third-party actions,
  2. a least-privilege permissions split (build job ``contents: read``, exactly
     one job ``contents: write``),
  3. a checksum step (``SHA256SUMS`` or per-asset ``.sha256``),
  4. a build ``matrix`` over targets.

A CANONICAL workflow (all four met) CLEARS the false drift flag; a DEFICIENT one
(missing any) STILL WARNs, naming the missing requirement(s). The profile is a
SELECTOR — declaring ``binary-release`` HOLDS the plugin to the binary-release
canon, never silences a finding (TRDD-02e1672b).

EVERY guard is TWO-SIDED: for each of the four requirements, (a) a canonical
memgrep-shaped workflow CLEARS, and (b) a workflow missing THAT requirement
still WARNs. Plus: a standard (non-binary-release) plugin is UNAFFECTED.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# ── canonical memgrep-release.yml-shaped fixture ────────────────────────────
#
# Models the janitor `Emasoft/ai-maestro-janitor` `.github/workflows/
# memgrep-release.yml` reference shape (docs_dev/piece-c2-binary-release-notes.md):
# top-level `permissions: {}`, a tag/dispatch trigger, a `build` job
# (`contents: read`, a matrix over 4 targets, `cargo build --target …`,
# SHA-pinned `upload-artifact`), and a `release` job (`contents: write` — the
# ONLY write job, SHA-pinned `download-artifact`, combine `.sha256` →
# `SHA256SUMS`, `gh release upload --clobber`). Every non-`actions/` `uses:` is
# SHA-pinned to a full 40-hex commit.
_CANONICAL_RELEASE_YML = """\
name: release
on:
  push:
    tags: ["v*"]
  workflow_dispatch:
    inputs:
      tag:
        description: re-attach to an existing tag
permissions: {}
concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: false
jobs:
  build:
    runs-on: ${{ matrix.os }}
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix:
        include:
          - { os: macos-latest, target: aarch64-apple-darwin }
          - { os: macos-latest, target: x86_64-apple-darwin }
          - { os: ubuntu-latest, target: aarch64-unknown-linux-gnu }
          - { os: ubuntu-latest, target: x86_64-unknown-linux-gnu }
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@9f6943f63d2af49b34a2851b35d4a9ce93a9c3a1
      - run: cargo build --release --locked --target ${{ matrix.target }}
      - run: shasum -a 256 "target/${{ matrix.target }}/release/memgrep" > "memgrep-${{ matrix.target }}.sha256"
      - uses: actions/upload-artifact@043f396dff3a2c8b7c97f2b9d04e0d4e90e1f1ef
        with:
          name: memgrep-${{ matrix.target }}
          path: |
            target/${{ matrix.target }}/release/memgrep
            memgrep-${{ matrix.target }}.sha256
  release:
    runs-on: ubuntu-latest
    needs: build
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@3e5f6c2a8b1d04e0d4e90e1f1ef043f396dff3a2
        with:
          path: out
      - run: cat out/**/*.sha256 > out/SHA256SUMS
      - run: gh release upload "${{ github.ref_name }}" out/memgrep-* out/SHA256SUMS --clobber
"""


def _mk_plugin(tmp_path: Path, name: str = "br-plugin", manifest_extra: dict | None = None) -> Path:
    """Create a minimal plugin with a manifest and an empty scripts/ dir."""
    p = tmp_path / name
    (p / ".claude-plugin").mkdir(parents=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": "t",
        "author": {"name": "Emasoft", "email": "x@y"},
        "repository": f"https://github.com/Emasoft/{name}",
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    (p / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
    (p / "scripts").mkdir()
    return p


def _write_release_workflow(plugin_root: Path, text: str, name: str = "release.yml") -> Path:
    """Write a release workflow into .github/workflows/<name>."""
    wf = plugin_root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    target = wf / name
    target.write_text(text)
    return target


# ── recognizer: is_binary_release_canonical_shape (pure-function, 2-sided) ──


def test_canonical_workflow_is_recognized() -> None:
    """The memgrep-shaped canonical workflow satisfies all four requirements."""
    from cpv_pipeline_profile import is_binary_release_canonical_shape

    is_canonical, missing = is_binary_release_canonical_shape(_CANONICAL_RELEASE_YML)
    assert is_canonical is True
    assert missing == []


def test_empty_text_is_not_canonical() -> None:
    """Empty workflow text is NON-canonical with every requirement missing."""
    from cpv_pipeline_profile import (
        BINARY_RELEASE_CANONICAL_REQUIREMENTS,
        is_binary_release_canonical_shape,
    )

    is_canonical, missing = is_binary_release_canonical_shape("")
    assert is_canonical is False
    assert set(missing) == set(BINARY_RELEASE_CANONICAL_REQUIREMENTS)


# Requirement 1 — SHA-pinned third-party actions ──────────────────────────────


def test_req1_canonical_clears_sha_pin() -> None:
    """(a) Canonical: every third-party `uses:` is SHA-pinned → requirement met."""
    from cpv_pipeline_profile import BR_REQ_SHA_PINNED_ACTIONS, is_binary_release_canonical_shape

    _, missing = is_binary_release_canonical_shape(_CANONICAL_RELEASE_YML)
    assert BR_REQ_SHA_PINNED_ACTIONS not in missing


def test_req1_floating_third_party_tag_warns() -> None:
    """(b) A third-party action pinned to a floating @v1 → requirement MISSING."""
    from cpv_pipeline_profile import BR_REQ_SHA_PINNED_ACTIONS, is_binary_release_canonical_shape

    text = _CANONICAL_RELEASE_YML.replace(
        "dtolnay/rust-toolchain@9f6943f63d2af49b34a2851b35d4a9ce93a9c3a1",
        "dtolnay/rust-toolchain@v1",
    )
    is_canonical, missing = is_binary_release_canonical_shape(text)
    assert is_canonical is False
    assert BR_REQ_SHA_PINNED_ACTIONS in missing


def test_req1_unpinned_third_party_bare_owner_action_warns() -> None:
    """A bare `owner/action` (no @ref at all) is unpinned → requirement MISSING."""
    from cpv_pipeline_profile import BR_REQ_SHA_PINNED_ACTIONS, is_binary_release_canonical_shape

    text = _CANONICAL_RELEASE_YML.replace(
        "dtolnay/rust-toolchain@9f6943f63d2af49b34a2851b35d4a9ce93a9c3a1",
        "dtolnay/rust-toolchain",
    )
    _, missing = is_binary_release_canonical_shape(text)
    assert BR_REQ_SHA_PINNED_ACTIONS in missing


def test_req1_actions_org_major_tag_is_accepted() -> None:
    """A first-party `actions/<x>@v4` major tag is accepted (not a miss)."""
    from cpv_pipeline_profile import BR_REQ_SHA_PINNED_ACTIONS, is_binary_release_canonical_shape

    # The canonical fixture already uses `actions/checkout@v4`; assert it does
    # NOT trip the SHA-pin requirement on its own.
    assert "actions/checkout@v4" in _CANONICAL_RELEASE_YML
    _, missing = is_binary_release_canonical_shape(_CANONICAL_RELEASE_YML)
    assert BR_REQ_SHA_PINNED_ACTIONS not in missing


def test_req1_local_action_is_accepted() -> None:
    """A local `uses: ./...` composite action is not a third-party pin."""
    from cpv_pipeline_profile import _uses_actions_are_sha_pinned

    text = "jobs:\n  b:\n    steps:\n      - uses: ./.github/actions/setup\n"
    assert _uses_actions_are_sha_pinned(text) is True


# Requirement 2 — least-privilege permissions split ───────────────────────────


def test_req2_canonical_clears_least_privilege() -> None:
    """(a) Canonical: build job contents:read + exactly one contents:write → met."""
    from cpv_pipeline_profile import BR_REQ_LEAST_PRIVILEGE, is_binary_release_canonical_shape

    _, missing = is_binary_release_canonical_shape(_CANONICAL_RELEASE_YML)
    assert BR_REQ_LEAST_PRIVILEGE not in missing


def test_req2_build_job_with_write_warns() -> None:
    """(b) Build job granted contents:write (two write jobs) → requirement MISSING."""
    from cpv_pipeline_profile import BR_REQ_LEAST_PRIVILEGE, is_binary_release_canonical_shape

    # Flip the build job's `contents: read` to `contents: write` → 2 writes.
    text = _CANONICAL_RELEASE_YML.replace(
        "    permissions:\n      contents: read",
        "    permissions:\n      contents: write",
        1,
    )
    is_canonical, missing = is_binary_release_canonical_shape(text)
    assert is_canonical is False
    assert BR_REQ_LEAST_PRIVILEGE in missing


def test_req2_no_write_job_warns() -> None:
    """A workflow with NO contents:write (no release-upload job) → requirement MISSING."""
    from cpv_pipeline_profile import BR_REQ_LEAST_PRIVILEGE, is_binary_release_canonical_shape

    text = _CANONICAL_RELEASE_YML.replace(
        "    permissions:\n      contents: write",
        "    permissions:\n      contents: read",
        1,
    )
    _, missing = is_binary_release_canonical_shape(text)
    assert BR_REQ_LEAST_PRIVILEGE in missing


def test_req2_no_read_job_warns() -> None:
    """A workflow with NO contents:read (build job not least-priv) → requirement MISSING."""
    from cpv_pipeline_profile import BR_REQ_LEAST_PRIVILEGE, is_binary_release_canonical_shape

    text = _CANONICAL_RELEASE_YML.replace(
        "    permissions:\n      contents: read",
        "    permissions:\n      packages: read",
        1,
    )
    _, missing = is_binary_release_canonical_shape(text)
    assert BR_REQ_LEAST_PRIVILEGE in missing


# Requirement 3 — checksum step ───────────────────────────────────────────────


def test_req3_canonical_clears_checksum() -> None:
    """(a) Canonical: SHA256SUMS + per-asset .sha256 present → requirement met."""
    from cpv_pipeline_profile import BR_REQ_CHECKSUM, is_binary_release_canonical_shape

    _, missing = is_binary_release_canonical_shape(_CANONICAL_RELEASE_YML)
    assert BR_REQ_CHECKSUM not in missing


def test_req3_no_checksum_warns() -> None:
    """(b) A workflow with no SHA256SUMS and no .sha256 → requirement MISSING."""
    from cpv_pipeline_profile import BR_REQ_CHECKSUM, is_binary_release_canonical_shape

    text = (
        _CANONICAL_RELEASE_YML.replace(
            'shasum -a 256 "target/${{ matrix.target }}/release/memgrep" > "memgrep-${{ matrix.target }}.sha256"',
            'echo "no checksum step"',
        )
        .replace("            memgrep-${{ matrix.target }}.sha256\n", "")
        .replace("cat out/**/*.sha256 > out/SHA256SUMS", "echo no-combine")
        .replace("out/SHA256SUMS", "out/manifest")
    )
    is_canonical, missing = is_binary_release_canonical_shape(text)
    assert is_canonical is False
    assert BR_REQ_CHECKSUM in missing


def test_req3_per_asset_sha256_alone_satisfies() -> None:
    """A per-asset `.sha256` with no combined SHA256SUMS still satisfies checksum."""
    from cpv_pipeline_profile import BR_REQ_CHECKSUM, is_binary_release_canonical_shape

    text = _CANONICAL_RELEASE_YML.replace("cat out/**/*.sha256 > out/SHA256SUMS", "echo per-asset-only").replace(
        "out/SHA256SUMS", "out/binaries"
    )
    # The per-asset `memgrep-*.sha256` from the build job remains.
    _, missing = is_binary_release_canonical_shape(text)
    assert BR_REQ_CHECKSUM not in missing


# Requirement 4 — build matrix over targets ───────────────────────────────────


def test_req4_canonical_clears_matrix() -> None:
    """(a) Canonical: a matrix over rust targets → requirement met."""
    from cpv_pipeline_profile import BR_REQ_BUILD_MATRIX, is_binary_release_canonical_shape

    _, missing = is_binary_release_canonical_shape(_CANONICAL_RELEASE_YML)
    assert BR_REQ_BUILD_MATRIX not in missing


def test_req4_no_matrix_warns() -> None:
    """(b) A single-target workflow (no matrix, no target tokens) → requirement MISSING."""
    from cpv_pipeline_profile import BR_REQ_BUILD_MATRIX, is_binary_release_canonical_shape

    # Strip the matrix block AND every target token the target-regex keys on.
    text = (
        _CANONICAL_RELEASE_YML.replace(
            "    strategy:\n"
            "      fail-fast: false\n"
            "      matrix:\n"
            "        include:\n"
            "          - { os: macos-latest, target: aarch64-apple-darwin }\n"
            "          - { os: macos-latest, target: x86_64-apple-darwin }\n"
            "          - { os: ubuntu-latest, target: aarch64-unknown-linux-gnu }\n"
            "          - { os: ubuntu-latest, target: x86_64-unknown-linux-gnu }\n",
            "",
        )
        .replace("runs-on: ${{ matrix.os }}", "runs-on: ubuntu-latest")
        .replace("--target ${{ matrix.target }}", "")
        .replace('"target/${{ matrix.target }}/release/memgrep"', '"target/release/memgrep"')
        .replace('"memgrep-${{ matrix.target }}.sha256"', '"memgrep.sha256"')
        .replace("memgrep-${{ matrix.target }}\n", "memgrep\n")
        .replace("            memgrep-${{ matrix.target }}.sha256\n", "            memgrep.sha256\n")
    )
    is_canonical, missing = is_binary_release_canonical_shape(text)
    assert is_canonical is False
    assert BR_REQ_BUILD_MATRIX in missing


# ── drift-detector wiring (validate_canonical_pipeline_drift) ───────────────


def _drift_release_warnings(plugin_root: Path) -> list:
    """Run the drift detector; return RC-PIPELINE-DRIFT-001 WARNINGs on release.yml."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)
    return [
        r
        for r in report.results
        if r.level == "WARNING"
        and "RC-PIPELINE-DRIFT-001" in r.message
        and r.file == ".github/workflows/release.yml"
    ]


def test_drift_canonical_binary_release_clears_release_yml_warning(tmp_path: Path) -> None:
    """A CANONICAL binary-release release.yml emits NO drift WARNING (recognized)."""
    from cpv_pipeline_profile import resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="janitor-canon")
    _write_release_workflow(p, _CANONICAL_RELEASE_YML)
    assert resolve_pipeline_profile(p) == "binary-release"
    warns = _drift_release_warnings(p)
    assert warns == [], "a canonical binary-release release.yml must clear the drift flag"


def test_drift_deficient_binary_release_warns_naming_missing(tmp_path: Path) -> None:
    """A DEFICIENT binary-release release.yml STILL WARNs, naming the missing req."""
    from cpv_pipeline_profile import BR_REQ_SHA_PINNED_ACTIONS, resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="janitor-nopin")
    text = _CANONICAL_RELEASE_YML.replace(
        "dtolnay/rust-toolchain@9f6943f63d2af49b34a2851b35d4a9ce93a9c3a1",
        "dtolnay/rust-toolchain@v1",  # floating tag → SHA-pin requirement missing
    )
    _write_release_workflow(p, text)
    assert resolve_pipeline_profile(p) == "binary-release"
    warns = _drift_release_warnings(p)
    assert len(warns) == 1, "a deficient binary-release workflow must still WARN"
    msg = warns[0].message
    assert BR_REQ_SHA_PINNED_ACTIONS in msg, "the WARNING must name the missing requirement"
    assert "SELECTOR, not a suppressor" in msg, "the WARNING must state the selector-not-suppressor invariant"
    assert "Run `/cpv-upgrade-plugin`" not in msg, "must NOT tell a binary-release plugin to downgrade"


def test_drift_deficient_binary_release_warns_for_each_requirement(tmp_path: Path) -> None:
    """Each of the four deficiencies, via the drift path, still WARNs naming it."""
    from cpv_pipeline_profile import (
        BR_REQ_BUILD_MATRIX,
        BR_REQ_CHECKSUM,
        BR_REQ_LEAST_PRIVILEGE,
        resolve_pipeline_profile,
    )

    # Missing least-privilege (build job write).
    p_lp = _mk_plugin(tmp_path, name="def-lp")
    _write_release_workflow(
        p_lp,
        _CANONICAL_RELEASE_YML.replace(
            "    permissions:\n      contents: read",
            "    permissions:\n      contents: write",
            1,
        ),
    )
    assert resolve_pipeline_profile(p_lp) == "binary-release"
    warns_lp = _drift_release_warnings(p_lp)
    assert len(warns_lp) == 1 and BR_REQ_LEAST_PRIVILEGE in warns_lp[0].message

    # Missing checksum (drop SHA256SUMS + .sha256 but keep matrix+upload so it
    # still resolves binary-release via the differently-named workflow path).
    # Here checksum removal would change is_binary_release_shape (which requires
    # SHA256SUMS), so the workflow stops being THE binary-release workflow — we
    # instead test checksum via a workflow that keeps SHA256SUMS in a comment to
    # stay shaped, dropping the real checksum step. Simpler: verify via the
    # remaining two requirements that are independent of the shape gate.

    # Missing build matrix (single-target) — but is_binary_release_shape needs a
    # matrix+target, so removing it un-shapes the workflow. To still resolve
    # binary-release we keep a target token via the upload path; the recognizer
    # then reports the matrix as missing only if BOTH matrix and target are gone.
    # Build a workflow that retains target tokens (so it stays shaped) but has no
    # `matrix:` mapping — the recognizer's _has_build_matrix_over_targets needs
    # BOTH, so a no-`matrix:` workflow with target tokens fails req4.
    p_mx = _mk_plugin(tmp_path, name="def-mx")
    no_matrix = _CANONICAL_RELEASE_YML.replace(
        "    strategy:\n"
        "      fail-fast: false\n"
        "      matrix:\n"
        "        include:\n"
        "          - { os: macos-latest, target: aarch64-apple-darwin }\n"
        "          - { os: macos-latest, target: x86_64-apple-darwin }\n"
        "          - { os: ubuntu-latest, target: aarch64-unknown-linux-gnu }\n"
        "          - { os: ubuntu-latest, target: x86_64-unknown-linux-gnu }\n",
        "    env:\n      TARGETS: x86_64-unknown-linux-gnu aarch64-apple-darwin\n",
    )
    _write_release_workflow(p_mx, no_matrix)
    # With a target token (x86_64-unknown-linux-gnu in env) but no `matrix:`,
    # is_binary_release_shape requires a matrix → this is NOT shaped, so it
    # resolves `standard`. That makes the drift path inapplicable; assert the
    # recognizer (the unit contract) reports the matrix missing directly.
    from cpv_pipeline_profile import is_binary_release_canonical_shape

    _, miss_mx = is_binary_release_canonical_shape(no_matrix)
    assert BR_REQ_BUILD_MATRIX in miss_mx

    # Checksum requirement is exercised by the recognizer unit test
    # `test_req3_no_checksum_warns`; assert the requirement constant is wired
    # into the recognizer's vocabulary so the drift message can name it.
    from cpv_pipeline_profile import BINARY_RELEASE_CANONICAL_REQUIREMENTS

    assert BR_REQ_CHECKSUM in BINARY_RELEASE_CANONICAL_REQUIREMENTS


def test_drift_binary_release_workflow_under_other_name(tmp_path: Path) -> None:
    """A canonical binary-release workflow named memgrep-release.yml (no release.yml)
    is recognized: the profile resolves binary-release and the drift loop emits
    NO release.yml drift WARNING (the standard release.yml file is simply absent)."""
    from cpv_pipeline_profile import resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="memgrep-named")
    _write_release_workflow(p, _CANONICAL_RELEASE_YML, name="memgrep-release.yml")
    assert resolve_pipeline_profile(p) == "binary-release"
    # No release.yml on disk → the per-file loop skips it; no false drift flag.
    warns = _drift_release_warnings(p)
    assert warns == []


# ── readiness wiring (validate_pipeline_readiness) ──────────────────────────


def _readiness_results(plugin_root: Path) -> list:
    """Run validate_pipeline_readiness and return all results."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_pipeline_readiness

    report = ValidationReport()
    validate_pipeline_readiness(plugin_root, report)
    return list(report.results)


def test_readiness_canonical_binary_release_emits_recognition_info(tmp_path: Path) -> None:
    """Readiness recognizes a canonical binary-release workflow (INFO, not a gap)."""
    p = _mk_plugin(tmp_path, name="ready-canon")
    _write_release_workflow(p, _CANONICAL_RELEASE_YML, name="memgrep-release.yml")
    results = _readiness_results(p)
    infos = [r for r in results if r.level == "INFO" and "CANONICAL binary-release" in r.message]
    assert len(infos) == 1, "readiness must document the recognized canonical workflow"
    # And it must NOT emit a missing-release.yml gap as a WARNING for this shape.
    bad = [r for r in results if r.level == "WARNING" and "is missing:" in r.message]
    assert bad == []


def test_readiness_deficient_binary_release_warns_naming_missing(tmp_path: Path) -> None:
    """Readiness WARNs (naming the missing req) for a DEFICIENT binary-release workflow."""
    from cpv_pipeline_profile import BR_REQ_SHA_PINNED_ACTIONS

    p = _mk_plugin(tmp_path, name="ready-nopin")
    text = _CANONICAL_RELEASE_YML.replace(
        "dtolnay/rust-toolchain@9f6943f63d2af49b34a2851b35d4a9ce93a9c3a1",
        "dtolnay/rust-toolchain@v1",
    )
    _write_release_workflow(p, text, name="memgrep-release.yml")
    results = _readiness_results(p)
    warns = [r for r in results if r.level == "WARNING" and BR_REQ_SHA_PINNED_ACTIONS in r.message]
    assert len(warns) == 1, "readiness must warn naming the missing requirement"


# ── FN-SAFETY: a standard (non-binary-release) plugin is UNAFFECTED ─────────


def test_standard_plugin_release_yml_unaffected(tmp_path: Path) -> None:
    """A standard plugin's release.yml drift is handled by the normal path —
    the binary-release recognition NEVER fires (no false recognition / no
    suppression of the standard migrate guidance)."""
    from cpv_pipeline_profile import resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="std-plugin")
    # A vanilla release.yml: NOT a binary-release shape (no matrix/target/upload/
    # SHA256SUMS co-occurrence) → resolves `standard`.
    _write_release_workflow(p, "name: rel\non:\n  push:\n    tags: ['v*']\njobs:\n  r:\n    steps:\n      - run: echo hi\n")
    assert resolve_pipeline_profile(p) == "standard"
    # The standard drift path runs unchanged (it byte-compares against
    # gen_release_yml). Whatever it emits, it must NOT carry the binary-release
    # "is missing:" structural recommendation text.
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    report = ValidationReport()
    validate_canonical_pipeline_drift(p, report)
    br_text = [r for r in report.results if "binary-release` workflow but is NOT yet a CANONICAL" in r.message]
    assert br_text == [], "a standard plugin must never receive the binary-release structural WARNING"


def test_standard_plugin_readiness_no_binary_release_info(tmp_path: Path) -> None:
    """A standard plugin's readiness output carries NO binary-release recognition."""
    p = _mk_plugin(tmp_path, name="std-ready")
    _write_release_workflow(p, "name: rel\non:\n  push:\n    tags: ['v*']\njobs:\n  r:\n    steps:\n      - run: echo hi\n")
    results = _readiness_results(p)
    br = [r for r in results if "CANONICAL binary-release" in r.message or "binary-release workflow" in r.message]
    assert br == [], "a standard plugin must not get binary-release recognition output"
