"""Tests for the `submodule-build` publish.py VARIANT (Piece C1, issue #128).

Covers the profile-aware ``gen_publish_py(p, profile)`` generator and the
profile-appropriate comparison in ``validate_canonical_pipeline_drift``.

The PSS shape (``Emasoft/perfect-skill-suggester``): build sources live in a
git submodule (``rust/``) + pre-compiled binaries committed to ``bin/``. Such a
plugin needs a SUBMODULE-AWARE publish.py — the standard one ships STALE
binaries because its rebuild-decision glob only ever sees the ``160000``
gitlink, never the submodule's own files (the #128 concrete bug).

FN-SAFETY is the #1 concern; every assertion here is TWO-SIDED:

  * ``gen_publish_py(p, "submodule-build")`` CONTAINS the submodule behaviors
    (commit-before-gitlink, push-before-parent, the ``git -C`` source-change
    detection, the gitlink-tolerant preflight) AND
    ``gen_publish_py(p, "standard")`` does NOT — and the standard output is
    byte-identical to the implicit default (the standard regression guard).
  * Drift CLEARS for a CORRECT submodule-build plugin (publish.py == the
    variant) AND still WARNs for a submodule-build plugin carrying the STALE
    standard publish.py (the by-design divergence clears, but a genuinely wrong
    file of the same profile still flags).
  * Drift stays WARNING / non-blocking / non-suppressible — the profile is a
    SELECTOR (which canon to compare against), never a suppressor.
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


def _params():
    """A minimal PluginParams for generator byte-comparisons."""
    from generate_plugin_repo import PluginParams

    return PluginParams(
        name="pss-like",
        description="A submodule-build plugin",
        author="Emasoft",
        author_email="x@y",
        github_owner="Emasoft",
    )


def _mk_submodule_build_plugin(tmp_path: Path, name: str = "pss-like") -> tuple[Path, dict]:
    """Create a plugin tree that resolves to the `submodule-build` profile.

    Signature (#128): a build-source submodule registered in `.gitmodules`
    (NOT a strip-dev-parts dev submodule) + a committed non-hidden `bin/`
    artifact. The manifest ALSO declares ``cpv.pipeline_profile:
    "submodule-build"`` — the authoritative SELECTOR override (a realistic,
    explicit config for a PSS-shape plugin). The override pins the profile
    independently of detection ordering: the standard publish.py drives the
    remote ``cpv-remote-validate`` gate, and with no vendored validators present
    a pure shape-detection would classify the plugin as ``remote-validation``
    (checked first). The override keeps these tests focused on the VARIANT
    comparison rather than on detection precedence (which is exercised in
    ``test_pipeline_profile.py``). Returns ``(plugin_root, manifest_dict)``.
    """
    p = tmp_path / name
    (p / ".claude-plugin").mkdir(parents=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": "A submodule-build plugin",
        "author": {"name": "Emasoft", "email": "x@y"},
        "repository": f"https://github.com/Emasoft/{name}",
        "cpv": {"pipeline_profile": "submodule-build"},
    }
    (p / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
    (p / "scripts").mkdir()
    # build-source submodule (rust/) — NOT a dev submodule
    (p / ".gitmodules").write_text(
        '[submodule "rust"]\n\tpath = rust\n\turl = https://github.com/Emasoft/x-rust\n'
    )
    # a committed prebuilt binary artifact
    bin_dir = p / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").write_bytes(b"\x7fELF prebuilt binary")
    return p, manifest


def _drift_warnings(plugin_root: Path) -> list:
    """Run the drift detector; return its RC-PIPELINE-DRIFT-001 WARNINGs."""
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin_root, report)
    return [
        r
        for r in report.results
        if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message
    ]


def _publish_py_drift(warnings: list) -> list:
    """Subset of drift warnings that are about scripts/publish.py."""
    return [w for w in warnings if "scripts/publish.py" in w.message]


# ── generator: the variant carries the behaviors; standard does not ────────


def test_submodule_variant_contains_git_C_source_change_detection() -> None:
    """The #128 fix: the variant detects source changes via `git -C <submodule>`."""
    from cpv_pipeline_profile import PROFILE_STANDARD, PROFILE_SUBMODULE_BUILD
    from generate_plugin_repo import gen_publish_py

    p = _params()
    variant = gen_publish_py(p, PROFILE_SUBMODULE_BUILD)
    standard = gen_publish_py(p, PROFILE_STANDARD)

    # The variant runs the rebuild-decision diff INSIDE the submodule.
    assert "git -C" in variant
    assert "submodule_source_changed" in variant
    # The standard body does NOT carry the `git -C` source-change detection —
    # this is precisely why a submodule-build plugin must NOT use it (it would
    # ship stale binaries by only ever seeing the 160000 gitlink).
    assert "git -C" not in standard
    assert "submodule_source_changed" not in standard


def test_submodule_variant_does_not_rely_on_parent_rs_glob_for_rebuild() -> None:
    """The variant's rebuild decision is the `git -C` diff, NOT a parent *.rs glob."""
    from cpv_pipeline_profile import PROFILE_SUBMODULE_BUILD
    from generate_plugin_repo import gen_publish_py

    variant = gen_publish_py(_params(), PROFILE_SUBMODULE_BUILD)
    # The source-change helper must contain the in-submodule diff form.
    assert "git" in variant and "-C" in variant
    assert 'diff", "--name-only"' in variant
    # And it must NOT decide rebuilds off a bare parent-repo `*.rs` glob.
    assert "*.rs" not in variant


def test_submodule_variant_commits_before_gitlink_and_pushes_before_parent() -> None:
    """The variant carries submodule-commit-before-gitlink + push-before-parent."""
    from cpv_pipeline_profile import PROFILE_STANDARD, PROFILE_SUBMODULE_BUILD
    from generate_plugin_repo import gen_publish_py

    variant = gen_publish_py(_params(), PROFILE_SUBMODULE_BUILD)
    standard = gen_publish_py(_params(), PROFILE_STANDARD)

    for marker in (
        "submodule_commit_before_gitlink",
        "ensure_submodule_pushed",
        "submodule_clean_tree_ok",
        "submodule_gitlink_moved",
        "BEGIN submodule-build profile section",
        "END submodule-build profile section",
    ):
        assert marker in variant, f"variant missing {marker!r}"
        assert marker not in standard, f"standard unexpectedly carries {marker!r}"


def test_submodule_variant_is_strictly_additive_over_standard() -> None:
    """The variant is the standard body PLUS an appended section (additive)."""
    from cpv_pipeline_profile import PROFILE_STANDARD, PROFILE_SUBMODULE_BUILD
    from generate_plugin_repo import gen_publish_py

    p = _params()
    standard = gen_publish_py(p, PROFILE_STANDARD)
    variant = gen_publish_py(p, PROFILE_SUBMODULE_BUILD)
    assert variant.startswith(standard)
    assert len(variant) > len(standard)


def test_both_bodies_compile_as_valid_python() -> None:
    """Both the standard and submodule-build publish.py bodies parse + compile."""
    from cpv_pipeline_profile import PROFILE_STANDARD, PROFILE_SUBMODULE_BUILD
    from generate_plugin_repo import gen_publish_py

    p = _params()
    compile(gen_publish_py(p, PROFILE_STANDARD), "<standard-publish.py>", "exec")
    compile(gen_publish_py(p, PROFILE_SUBMODULE_BUILD), "<submodule-publish.py>", "exec")


# ── standard byte-identity regression guard ────────────────────────────────


def test_standard_profile_byte_identical_to_default() -> None:
    """`gen_publish_py(p, "standard")` == the implicit default (no regression)."""
    from cpv_pipeline_profile import PROFILE_STANDARD
    from generate_plugin_repo import gen_publish_py

    p = _params()
    assert gen_publish_py(p, PROFILE_STANDARD) == gen_publish_py(p)


def test_remote_validation_profile_byte_identical_to_standard() -> None:
    """remote-validation reuses the standard publish.py body (no variant)."""
    from cpv_pipeline_profile import PROFILE_REMOTE_VALIDATION
    from generate_plugin_repo import gen_publish_py

    p = _params()
    assert gen_publish_py(p, PROFILE_REMOTE_VALIDATION) == gen_publish_py(p)


def test_unknown_profile_fails_safe_to_standard_body() -> None:
    """An unrecognized profile value yields the standard body (fail-safe)."""
    from generate_plugin_repo import gen_publish_py

    p = _params()
    assert gen_publish_py(p, "totally-unknown-profile") == gen_publish_py(p)


def test_standard_publish_py_still_pins_cpv_ref() -> None:
    """The standard ref-pin survives the profile parameter (root-cause #2)."""
    from cpv_pipeline_profile import PROFILE_STANDARD, PROFILE_SUBMODULE_BUILD
    from generate_plugin_repo import gen_publish_py

    p = _params()
    assert "claude-plugins-validation@" in gen_publish_py(p, PROFILE_STANDARD)
    # The pin is preserved in the variant too (it is the standard body + suffix).
    assert "claude-plugins-validation@" in gen_publish_py(p, PROFILE_SUBMODULE_BUILD)


# ── drift: clears for a correct submodule-build plugin ─────────────────────


def test_drift_clears_for_correct_submodule_build_plugin(tmp_path: Path) -> None:
    """A submodule-build plugin whose publish.py == the variant has NO publish.py drift."""
    from generate_plugin_repo import gen_publish_py
    from standardize_plugin import _params_from_manifest

    plugin, manifest = _mk_submodule_build_plugin(tmp_path)
    # Write the CORRECT (submodule-aware) publish.py — exactly what the detector
    # expects for this profile, built from the same params it uses.
    params = _params_from_manifest(manifest)
    (plugin / "scripts" / "publish.py").write_text(
        gen_publish_py(params, "submodule-build"), encoding="utf-8"
    )

    warnings = _drift_warnings(plugin)
    assert _publish_py_drift(warnings) == [], (
        "a correct submodule-build publish.py must NOT drift against its own "
        "(submodule-aware) canon"
    )


def test_drift_warns_for_submodule_build_plugin_with_stale_standard_publish(
    tmp_path: Path,
) -> None:
    """A submodule-build plugin still carrying the STALE standard publish.py WARNs."""
    from generate_plugin_repo import gen_publish_py
    from standardize_plugin import _params_from_manifest

    plugin, manifest = _mk_submodule_build_plugin(tmp_path)
    # Write the WRONG (standard, NOT submodule-aware) publish.py — behind its
    # own profile canon. This is the genuinely-broken-but-same-profile case.
    params = _params_from_manifest(manifest)
    (plugin / "scripts" / "publish.py").write_text(
        gen_publish_py(params, "standard"), encoding="utf-8"
    )

    publish_drift = _publish_py_drift(_drift_warnings(plugin))
    assert publish_drift, (
        "a submodule-build plugin carrying the stale STANDARD publish.py must "
        "still WARN (it is behind its submodule-aware canon)"
    )
    # The message must be the neutral by-design one (no downgrade), NOT a
    # `--force-templates` migrate-and-clobber instruction.
    msg = publish_drift[0].message
    assert "BY DESIGN" in msg
    assert "submodule-build" in msg


def test_drift_two_sided_correct_clears_and_stale_warns(tmp_path: Path) -> None:
    """Same profile, two plugins: the correct one clears, the stale one WARNs."""
    from generate_plugin_repo import gen_publish_py
    from standardize_plugin import _params_from_manifest

    # Correct plugin.
    good, gm = _mk_submodule_build_plugin(tmp_path, name="good-pss")
    (good / "scripts" / "publish.py").write_text(
        gen_publish_py(_params_from_manifest(gm), "submodule-build"), encoding="utf-8"
    )
    # Stale plugin.
    bad, bm = _mk_submodule_build_plugin(tmp_path, name="bad-pss")
    (bad / "scripts" / "publish.py").write_text(
        gen_publish_py(_params_from_manifest(bm), "standard"), encoding="utf-8"
    )

    assert _publish_py_drift(_drift_warnings(good)) == []
    assert _publish_py_drift(_drift_warnings(bad))


# ── drift stays WARNING / non-blocking / non-suppressible ──────────────────


def test_submodule_build_drift_is_warning_not_blocking(tmp_path: Path) -> None:
    """The publish.py drift on a stale submodule-build plugin is a WARNING (non-blocking)."""
    from cpv_validation_common import ValidationReport
    from generate_plugin_repo import gen_publish_py
    from standardize_plugin import _params_from_manifest
    from validate_plugin import validate_canonical_pipeline_drift

    plugin, manifest = _mk_submodule_build_plugin(tmp_path)
    (plugin / "scripts" / "publish.py").write_text(
        gen_publish_py(_params_from_manifest(manifest), "standard"), encoding="utf-8"
    )

    report = ValidationReport()
    validate_canonical_pipeline_drift(plugin, report)
    drift = [r for r in report.results if "RC-PIPELINE-DRIFT-001" in r.message]
    assert drift, "expected a pipeline-drift finding"
    # Every drift finding is a WARNING — never CRITICAL/MAJOR/MINOR (non-blocking).
    assert all(r.level == "WARNING" for r in drift)


def test_profile_is_selector_not_suppressor_for_submodule_build(tmp_path: Path) -> None:
    """A submodule-build plugin whose publish.py is BOTH stale AND mismatched still WARNs.

    The profile selects the variant to compare against; it cannot silence a
    finding. A publish.py that matches NEITHER the standard nor the variant
    (here: the stale standard, which differs from the submodule-aware canon)
    still produces a WARNING.
    """
    from generate_plugin_repo import gen_publish_py
    from standardize_plugin import _params_from_manifest

    plugin, manifest = _mk_submodule_build_plugin(tmp_path)
    (plugin / "scripts" / "publish.py").write_text(
        gen_publish_py(_params_from_manifest(manifest), "standard"), encoding="utf-8"
    )
    assert _publish_py_drift(_drift_warnings(plugin)), (
        "the submodule-build SELECTOR must not suppress a genuinely-divergent "
        "publish.py finding"
    )
