"""Tests for the cpv-canonical-pipeline PROFILE model (TRDD-e9f13df1).

Covers ``scripts/cpv_pipeline_profile.py`` (profile resolution + detection
helpers) and the profile-aware + direction-aware rewiring of
``validate_canonical_pipeline_drift`` / ``validate_pipeline_readiness`` in
``validate_plugin.py``. Resolves issues #130 (remote-validation plugins) and
#118 defect 2 (direction-aware drift).

FN-SAFETY is the #1 concern and every guard here is TWO-SIDED:
  * A STANDARD plugin resolves ``standard`` and its drift is byte-identical to
    today — NO suppression (``test_standard_*``).
  * Mis-detection fails SAFE to ``standard`` → current behavior
    (``test_*_fail_safe_*``).
  * For each non-standard profile, a fixture (a) resolves to it, (b) has its
    by-design divergence recognized (no downgrade message), AND (c) a
    genuinely behind/broken file of the SAME profile still WARNs.
  * ``cpv.pipeline_profile`` is a SELECTOR, never a SUPPRESSOR — a declared
    profile is still held to that profile's canon (``test_override_is_selector
    _not_suppressor``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# ── fixtures ──────────────────────────────────────────────────────────────


def _mk_plugin(tmp_path: Path, name: str = "test-plugin", manifest_extra: dict | None = None) -> Path:
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


def _drift_warnings(plugin_root: Path) -> list:
    """Run the drift detector and return its RC-PIPELINE-DRIFT-001 WARNINGs."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)
    return [r for r in report.results if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message]


# ── resolve_pipeline_profile: manifest override ───────────────────────────


def test_manifest_override_returns_known_profile(tmp_path: Path) -> None:
    """A `cpv.pipeline_profile` set to a KNOWN value is the authoritative override."""
    from cpv_pipeline_profile import manifest_profile_override, resolve_pipeline_profile

    p = _mk_plugin(tmp_path, manifest_extra={"cpv": {"pipeline_profile": "remote-validation"}})
    assert manifest_profile_override(p) == "remote-validation"
    assert resolve_pipeline_profile(p) == "remote-validation"


def test_manifest_override_unknown_value_is_ignored(tmp_path: Path) -> None:
    """An unknown / typo profile value is IGNORED (detection runs instead).

    A typo can never silently disable the standard canon nor select a value
    outside the enforced set.
    """
    from cpv_pipeline_profile import manifest_profile_override, resolve_pipeline_profile

    p = _mk_plugin(tmp_path, manifest_extra={"cpv": {"pipeline_profile": "nonsense"}})
    assert manifest_profile_override(p) is None
    assert resolve_pipeline_profile(p) == "standard"


def test_manifest_override_non_string_is_ignored(tmp_path: Path) -> None:
    """A non-string `cpv.pipeline_profile` is ignored, not crashed on."""
    from cpv_pipeline_profile import manifest_profile_override

    p = _mk_plugin(tmp_path, manifest_extra={"cpv": {"pipeline_profile": ["remote-validation"]}})
    assert manifest_profile_override(p) is None


def test_no_manifest_falls_back_to_standard(tmp_path: Path) -> None:
    """A plugin without a manifest resolves to `standard` (fail safe)."""
    from cpv_pipeline_profile import resolve_pipeline_profile

    p = tmp_path / "no-manifest"
    p.mkdir()
    assert resolve_pipeline_profile(p) == "standard"


# ── detection: remote-validation (#130) ──────────────────────────────────


def _write_remote_validation_publish(plugin_root: Path) -> None:
    (plugin_root / "scripts" / "publish.py").write_text(
        "#!/usr/bin/env python3\n"
        "import subprocess\n"
        'subprocess.run(["uvx","--from","git+https://github.com/Emasoft/'
        'claude-plugins-validation","--with","pyyaml","cpv-remote-validate",'
        '"plugin",".","--strict"])\n'
    )


def test_remote_validation_shape_detected(tmp_path: Path) -> None:
    """publish.py invokes the remote gate AND no vendored validator → remote-validation."""
    from cpv_pipeline_profile import (
        invokes_remote_gate,
        is_remote_validation_shape,
        resolve_pipeline_profile,
        vendored_validators_present,
    )

    p = _mk_plugin(tmp_path, name="caa-like")
    _write_remote_validation_publish(p)
    assert invokes_remote_gate(p) is True
    assert vendored_validators_present(p) is False
    assert is_remote_validation_shape(p) is True
    assert resolve_pipeline_profile(p) == "remote-validation"


def test_remote_gate_but_vendored_validator_is_standard(tmp_path: Path) -> None:
    """A plugin that invokes the remote gate BUT keeps a vendored validator is
    `standard` — it has a local validator, so it is not de-vendored."""
    from cpv_pipeline_profile import is_remote_validation_shape, resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="hybrid")
    _write_remote_validation_publish(p)
    (p / "scripts" / "validate_plugin.py").write_text("# vendored validator\n")
    assert is_remote_validation_shape(p) is False
    assert resolve_pipeline_profile(p) == "standard"


def test_remote_validation_detected_from_workflow(tmp_path: Path) -> None:
    """The remote-gate invocation may live in a workflow, not only publish.py."""
    from cpv_pipeline_profile import resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="ci-remote")
    wf = p / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "jobs:\n  v:\n    steps:\n      - run: uvx --from git+https://github.com/"
        "Emasoft/claude-plugins-validation cpv-remote-validate plugin . --strict\n"
    )
    assert resolve_pipeline_profile(p) == "remote-validation"


# ── detection: submodule-build (#128) ─────────────────────────────────────


def _write_build_source_submodule(plugin_root: Path, path: str = "rust") -> None:
    (plugin_root / ".gitmodules").write_text(
        f'[submodule "{path}"]\n\tpath = {path}\n\turl = https://github.com/Emasoft/x-{path}\n'
    )


def _write_bin_artifact(plugin_root: Path) -> None:
    bin_dir = plugin_root / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").write_bytes(b"\x7fELF prebuilt binary")


def test_submodule_build_shape_detected(tmp_path: Path) -> None:
    """A build-source submodule + a committed bin/ artifact → submodule-build."""
    from cpv_pipeline_profile import (
        has_build_source_submodule,
        has_committed_bin_artifacts,
        is_submodule_build_shape,
        resolve_pipeline_profile,
    )

    p = _mk_plugin(tmp_path, name="pss-like")
    _write_build_source_submodule(p)
    _write_bin_artifact(p)
    assert has_build_source_submodule(p) is True
    assert has_committed_bin_artifacts(p) is True
    assert is_submodule_build_shape(p) is True
    assert resolve_pipeline_profile(p) == "submodule-build"


def test_dev_tests_submodule_is_not_submodule_build(tmp_path: Path) -> None:
    """A strip-dev-parts `dev/tests` submodule is NOT a build-source submodule."""
    from cpv_pipeline_profile import has_build_source_submodule, is_submodule_build_shape, resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="dev-sub")
    (p / ".gitmodules").write_text('[submodule "tests"]\n\tpath = dev/tests\n\turl = u\n')
    _write_bin_artifact(p)
    assert has_build_source_submodule(p) is False
    assert is_submodule_build_shape(p) is False
    assert resolve_pipeline_profile(p) == "standard"


def test_classify_submodules_partitions_build_source_and_other(tmp_path: Path) -> None:
    """classify_submodules() → (build_source, other): `rust` is build-source; a non-hinted
    source dir (`engine`) and a dev submodule (`docs`) land in `other` — both ship on
    install (CC recurses), so neither is silently dropped (issue #175 FN close)."""
    from cpv_pipeline_profile import classify_submodules, has_build_source_submodule

    p = _mk_plugin(tmp_path, name="multi-sub")
    (p / ".gitmodules").write_text(
        '[submodule "rust"]\n\tpath = rust\n\turl = https://github.com/Emasoft/x-rust\n'
        '[submodule "engine"]\n\tpath = engine\n\turl = https://github.com/Emasoft/x-engine\n'
        '[submodule "docs"]\n\tpath = docs\n\turl = https://github.com/Emasoft/x-docs\n'
    )
    build_source, other = classify_submodules(p)
    assert build_source == ["rust"]
    assert sorted(other) == ["docs", "engine"]
    # has_build_source_submodule delegates to classify_submodules — one classifier, same verdict.
    assert has_build_source_submodule(p) is True


def test_classify_submodules_no_gitmodules_is_empty(tmp_path: Path) -> None:
    """No .gitmodules → both partitions empty (side-effect-free, FN-safe default)."""
    from cpv_pipeline_profile import classify_submodules

    p = _mk_plugin(tmp_path, name="no-sub")
    build_source, other = classify_submodules(p)
    assert build_source == []
    assert other == []


def test_empty_bin_dir_does_not_trip_submodule_build(tmp_path: Path) -> None:
    """A bin/ holding only hidden files (.gitkeep) is not a prebuilt-artifact dir."""
    from cpv_pipeline_profile import has_committed_bin_artifacts, is_submodule_build_shape

    p = _mk_plugin(tmp_path, name="empty-bin")
    _write_build_source_submodule(p)
    (p / "bin").mkdir()
    (p / "bin" / ".gitkeep").write_text("")
    assert has_committed_bin_artifacts(p) is False
    assert is_submodule_build_shape(p) is False


def test_build_submodule_without_bin_is_standard(tmp_path: Path) -> None:
    """A build-source submodule WITHOUT a committed bin/ does not trip the profile."""
    from cpv_pipeline_profile import is_submodule_build_shape, resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="sub-no-bin")
    _write_build_source_submodule(p)
    assert is_submodule_build_shape(p) is False
    assert resolve_pipeline_profile(p) == "standard"


# ── detection: binary-release (#115) ──────────────────────────────────────


def _write_binary_release_workflow(plugin_root: Path) -> None:
    wf = plugin_root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "release-binaries.yml").write_text(
        "name: rel\njobs:\n  build:\n    strategy:\n      matrix:\n"
        "        target: [x86_64-unknown-linux-gnu, aarch64-apple-darwin]\n"
        "    steps:\n      - run: cargo build --target ${{ matrix.target }}\n"
        "      - run: sha256sum dist/* > SHA256SUMS\n"
        "      - run: gh release upload ${{ github.ref_name }} dist/*\n"
    )


def test_binary_release_shape_detected(tmp_path: Path) -> None:
    """A matrix build + release-asset upload + SHA256SUMS in one workflow → binary-release."""
    from cpv_pipeline_profile import is_binary_release_shape, resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="janitor-like")
    _write_binary_release_workflow(p)
    assert is_binary_release_shape(p) is True
    assert resolve_pipeline_profile(p) == "binary-release"


def test_plain_release_workflow_is_not_binary_release(tmp_path: Path) -> None:
    """A vanilla release.yml (no matrix/upload/SHA256SUMS) is NOT binary-release."""
    from cpv_pipeline_profile import is_binary_release_shape, resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="plain-rel")
    wf = p / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "release.yml").write_text("name: rel\njobs:\n  r:\n    steps:\n      - run: echo hi\n")
    assert is_binary_release_shape(p) is False
    assert resolve_pipeline_profile(p) == "standard"


def test_partial_binary_release_signals_do_not_trip(tmp_path: Path) -> None:
    """A workflow with a matrix but NO release upload or SHA256SUMS is not binary-release."""
    from cpv_pipeline_profile import is_binary_release_shape

    p = _mk_plugin(tmp_path, name="matrix-only")
    wf = p / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "jobs:\n  t:\n    strategy:\n      matrix:\n"
        "        target: [x86_64-unknown-linux-gnu]\n    steps:\n      - run: cargo test\n"
    )
    assert is_binary_release_shape(p) is False


# ── fail-safe ──────────────────────────────────────────────────────────────


def test_detection_fails_safe_on_unreadable_gitmodules(tmp_path: Path) -> None:
    """A malformed .gitmodules must not crash detection; falls back to standard."""
    from cpv_pipeline_profile import resolve_pipeline_profile

    p = _mk_plugin(tmp_path, name="bad-gm")
    (p / ".gitmodules").write_text("not a valid gitmodules \x00 file")
    # Must not raise; with no other signals, resolves to standard.
    assert resolve_pipeline_profile(p) == "standard"


# ── profile-aware drift: remote-validation (#130) ─────────────────────────


def test_remote_validation_publish_drift_is_by_design(tmp_path: Path) -> None:
    """For a remote-validation plugin, a drifted publish.py is recognized as a
    by-design profile divergence (NO migrate/downgrade message)."""
    p = _mk_plugin(tmp_path, name="rv-pub")
    _write_remote_validation_publish(p)  # makes it resolve remote-validation
    warns = [w for w in _drift_warnings(p) if w.file == "scripts/publish.py"]
    assert len(warns) == 1, "remote-validation publish.py must still emit a WARNING (no suppression)"
    msg = warns[0].message
    assert "BY DESIGN for the plugin's" in msg
    assert "`remote-validation` pipeline profile" in msg
    assert "Run `/cpv-upgrade-plugin`" not in msg, "must NOT tell a remote-validation plugin to downgrade"


def test_remote_validation_absent_vendored_helper_is_not_a_finding(tmp_path: Path) -> None:
    """A remote-validation plugin omits cpv_network_resilience.py by design — the
    absent file produces NO drift finding (a missing file is skipped)."""
    p = _mk_plugin(tmp_path, name="rv-no-helper")
    _write_remote_validation_publish(p)
    warns = [w for w in _drift_warnings(p) if w.file == "scripts/cpv_network_resilience.py"]
    assert not warns, "an intentionally-absent vendored helper must not produce a drift finding"


def test_remote_validation_standard_canon_file_still_warns_to_upgrade(tmp_path: Path) -> None:
    """A remote-validation plugin's NON-divergent file (notify-marketplace.yml,
    stale, no hardening) is NOT in the by-design set → still gets the migrate
    message. The profile recognizes only the files that actually diverge."""
    p = _mk_plugin(tmp_path, name="rv-stale-notify")
    _write_remote_validation_publish(p)
    wf = p / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "notify-marketplace.yml").write_text("# stale notify, no hardening\n")
    warns = [w for w in _drift_warnings(p) if w.file == ".github/workflows/notify-marketplace.yml"]
    assert len(warns) == 1
    assert "Run `/cpv-upgrade-plugin`" in warns[0].message
    assert "BY DESIGN" not in warns[0].message


# ── selector-not-suppressor (the security invariant) ──────────────────────


def test_override_is_selector_not_suppressor(tmp_path: Path) -> None:
    """A declared `remote-validation` profile must NOT silence a stale
    standard-canon file. The selector picks WHICH canon — it never suppresses
    a finding (TRDD-02e1672b)."""
    p = _mk_plugin(
        tmp_path,
        name="declared-rv",
        manifest_extra={"cpv": {"pipeline_profile": "remote-validation"}},
    )
    wf = p / ".github" / "workflows"
    wf.mkdir(parents=True)
    # notify-marketplace.yml is NOT in remote-validation's by-design set.
    (wf / "notify-marketplace.yml").write_text("# stale notify, way behind\n")
    warns = [w for w in _drift_warnings(p) if w.file == ".github/workflows/notify-marketplace.yml"]
    assert len(warns) == 1, "a declared profile must NOT suppress a stale non-divergent file"
    assert "Run `/cpv-upgrade-plugin`" in warns[0].message


# ── direction-aware drift (#118 defect 2) ─────────────────────────────────


def test_direction_classifier_states() -> None:
    """The diff-direction classifier returns the four documented states.

    Diff is unified_diff(expected=CANON, actual=PLUGIN): a hardening marker on a
    `+` line = plugin AHEAD; on a `-` line = plugin BEHIND.
    """
    from validate_plugin import _classify_drift_direction

    assert _classify_drift_direction(["@@", "-old", "+new"]) == "plain"
    assert _classify_drift_direction(["@@", "+  uses: a/b@" + "f" * 40]) == "ahead"
    assert _classify_drift_direction(["@@", "-timeout-minutes: 15"]) == "behind"
    assert _classify_drift_direction(["@@", "+SHA256SUMS", "-timeout-minutes: 15"]) == "mixed"
    # diff headers are ignored, never mistaken for +/- content lines.
    assert _classify_drift_direction(["--- canonical/x", "+++ plugin/x", "@@", "+normal"]) == "plain"


def test_ahead_of_canon_file_is_not_told_to_downgrade(tmp_path: Path) -> None:
    """A standard plugin whose release.yml is AHEAD of canon (extra SHA-pin /
    persist-credentials / MARKETPLACE_PAT) gets the upstream/accept message, NOT
    `--force-templates` (which would downgrade it). Issue #118 defect 2."""
    from generate_plugin_repo import gen_release_yml
    from standardize_plugin import _params_from_manifest

    p = _mk_plugin(tmp_path, name="ahead-rel")
    (p / "scripts" / "validate_plugin.py").write_text("# vendored\n")  # ensure standard profile
    params = _params_from_manifest(json.loads((p / ".claude-plugin" / "plugin.json").read_text()))
    canon = gen_release_yml(params)
    wf = p / ".github" / "workflows"
    wf.mkdir(parents=True)
    # Add hardening the canon template lacks → plugin is AHEAD.
    ahead = canon.replace(
        "permissions:",
        "permissions:\n  # extra hardening below\n  persist-credentials: false\n",
        1,
    ) + "\n# MARKETPLACE_PAT preflight guard (extra hardening)\n"
    (wf / "release.yml").write_text(ahead)
    warns = [w for w in _drift_warnings(p) if w.file == ".github/workflows/release.yml"]
    assert len(warns) == 1
    msg = warns[0].message
    assert "AHEAD of canon" in msg
    assert "Run `/cpv-upgrade-plugin`" not in msg, "an ahead-of-canon file must NOT be told to downgrade"


def test_stale_standard_file_still_recommends_migrate(tmp_path: Path) -> None:
    """A standard plugin's plain stale publish.py (no hardening signal) keeps
    today's EXACT migrate recommendation (FN-safety: no regression)."""
    p = _mk_plugin(tmp_path, name="stale-pub")
    (p / "scripts" / "validate_plugin.py").write_text("# vendored\n")  # standard profile
    (p / "scripts" / "publish.py").write_text("# stale publish.py, far behind canon\n")
    warns = [w for w in _drift_warnings(p) if w.file == "scripts/publish.py"]
    assert len(warns) == 1
    assert "Run `/cpv-upgrade-plugin`" in warns[0].message


def test_behind_canon_file_recommends_migrate(tmp_path: Path) -> None:
    """A standard file BEHIND canon (canon carries hardening it lacks) → migrate."""
    from generate_plugin_repo import gen_release_yml
    from standardize_plugin import _params_from_manifest

    p = _mk_plugin(tmp_path, name="behind-rel")
    (p / "scripts" / "validate_plugin.py").write_text("# vendored\n")
    params = _params_from_manifest(json.loads((p / ".claude-plugin" / "plugin.json").read_text()))
    canon = gen_release_yml(params)
    # Strip a hardening token so canon has it and the plugin lacks it → BEHIND.
    if "SHA256SUMS" in canon:
        behind = canon.replace("SHA256SUMS", "CHECKSUMS_RENAMED")
    else:  # pragma: no cover — current canon ships SHA256SUMS; defensive fallback
        behind = canon.replace("timeout-minutes:", "x-timeout-disabled:")
    wf = p / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "release.yml").write_text(behind)
    warns = [w for w in _drift_warnings(p) if w.file == ".github/workflows/release.yml"]
    assert len(warns) == 1
    assert "Run `/cpv-upgrade-plugin`" in warns[0].message


# ── FN-safety #1: standard plugin, byte-identical canon = zero drift ───────


def test_standard_byte_identical_canon_emits_zero_drift(tmp_path: Path) -> None:
    """A standard plugin whose pipeline files are byte-identical to canon emits
    ZERO drift — exactly today's behavior, no suppression introduced."""
    from generate_plugin_repo import gen_pre_push_hook, gen_publish_py
    from standardize_plugin import _params_from_manifest

    p = _mk_plugin(tmp_path, name="std-canon")
    (p / "scripts" / "validate_plugin.py").write_text("# vendored validator\n")
    params = _params_from_manifest(json.loads((p / ".claude-plugin" / "plugin.json").read_text()))
    (p / "scripts" / "publish.py").write_text(gen_publish_py(params))
    (p / "git-hooks").mkdir()
    (p / "git-hooks" / "pre-push").write_text(gen_pre_push_hook(params))
    assert _drift_warnings(p) == []


def test_standard_plugin_emits_no_profile_readiness_info(tmp_path: Path) -> None:
    """A standard plugin's pipeline-readiness output carries NO profile INFO line
    (the INFO is reserved for non-standard profiles)."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_pipeline_readiness

    p = _mk_plugin(tmp_path, name="std-info")
    (p / "scripts" / "validate_plugin.py").write_text("# vendored\n")
    report = ValidationReport()
    validate_pipeline_readiness(p, report)
    infos = [r for r in report.results if r.level == "INFO" and "pipeline profile" in r.message]
    assert not infos


def test_remote_validation_emits_profile_readiness_info(tmp_path: Path) -> None:
    """A remote-validation plugin's readiness output documents the detected
    profile (INFO) instead of treating absent validators as a gap."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_pipeline_readiness

    p = _mk_plugin(tmp_path, name="rv-info")
    _write_remote_validation_publish(p)
    report = ValidationReport()
    validate_pipeline_readiness(p, report)
    infos = [r for r in report.results if r.level == "INFO" and "pipeline profile" in r.message]
    assert len(infos) == 1
    assert "remote-validation" in infos[0].message


# ── submodule-build + binary-release drift recognition ────────────────────


def test_submodule_build_publish_drift_is_by_design(tmp_path: Path) -> None:
    """A submodule-build plugin's drifted publish.py is recognized as a by-design
    divergence (submodule-aware), not a downgrade target."""
    p = _mk_plugin(tmp_path, name="sb-pub")
    _write_build_source_submodule(p)
    _write_bin_artifact(p)
    (p / "scripts" / "publish.py").write_text("# submodule-aware publish.py, differs from standard\n")
    warns = [w for w in _drift_warnings(p) if w.file == "scripts/publish.py"]
    assert len(warns) == 1
    assert "BY DESIGN for the plugin's" in warns[0].message
    assert "`submodule-build` pipeline profile" in warns[0].message
    assert "Run `/cpv-upgrade-plugin`" not in warns[0].message


def test_binary_release_release_yml_drift_is_by_design(tmp_path: Path) -> None:
    """A binary-release plugin's release.yml (the matrix build) is recognized as
    a by-design divergence, not flagged for downgrade."""
    p = _mk_plugin(tmp_path, name="br-rel")
    _write_binary_release_workflow(p)
    # Also give it a release.yml (a _CANONICAL_PIPELINE_FILES entry) that diverges.
    (p / ".github" / "workflows" / "release.yml").write_text(
        "name: rel\njobs:\n  build:\n    strategy:\n      matrix:\n"
        "        target: [x86_64-unknown-linux-gnu]\n    steps:\n"
        "      - run: cargo build --target ${{ matrix.target }}\n"
        "      - run: sha256sum dist/* > SHA256SUMS\n"
        "      - run: gh release upload ${{ github.ref_name }} dist/*\n"
    )
    warns = [w for w in _drift_warnings(p) if w.file == ".github/workflows/release.yml"]
    assert len(warns) == 1
    assert "BY DESIGN for the plugin's" in warns[0].message
    assert "`binary-release` pipeline profile" in warns[0].message
    assert "Run `/cpv-upgrade-plugin`" not in warns[0].message
