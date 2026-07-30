#!/usr/bin/env python3
"""`--force-templates` must never DELETE a plugin's own build/release machinery.

Measured motivation (a real perfect-skill-suggester clone, profile
`submodule-build`): `standardize --fix --force-templates` replaced its 1506-line
hand-written `scripts/publish.py` with canon's 2392-line one, and every Rust
release behavior went to ZERO — `_ensure_submodule_pushed` (2 -> 0), `CARGO_LOCK`
(6 -> 0), `CARGO_TOML` (4 -> 0), `rustup` (3 -> 0), the second crate (11 -> 0).
`gen_publish_py` DOES model `submodule-build`, but only the gitlink /
submodule-push mechanics — never the toolchain steps beside them.

The guard therefore declines a force-overwrite of the two RELEASE-MACHINERY files
when the plugin builds a compiled artifact. Both halves are tested here, and the
NEGATIVE half is the load-bearing one: a guard that fired too widely would
silently disable `--force-templates` for everyone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from standardize_plugin import (  # noqa: E402
    _PIPELINE_DRIFT_RC,
    _PROFILE_SENSITIVE_FORCE_FILES,
    _canon_incomplete_profiles,
    _force_template_skip_reason,
)

PUBLISH_REL = "scripts/publish.py"
RELEASE_REL = ".github/workflows/release.yml"
CLIFF_REL = "cliff.toml"

# Deliberately drift-"behind" content: canon carries a hardening marker the
# plugin lacks, so `_classify_drift_direction` says "behind" -> the pre-existing
# conditions would OVERWRITE. Any skip in these tests is therefore attributable
# to the new guard alone.
CANON = "name: x\ntimeout-minutes: 30\n"
PLUGIN = "name: x\n"


def _write(root: Path, rel: str, text: str) -> Path:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return f


def _make_compiled(root: Path) -> None:
    """Give the tree an intrinsic compiled component (in-tree source + build system)."""
    _write(root, "rust/src/lib.rs", "pub fn f() {}\n")
    _write(root, "rust/Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')


def _skip(root: Path, rel: str, profile: str, divergence: set[str] | None = None) -> str | None:
    f = root / rel
    return _force_template_skip_reason(f, rel, CANON, divergence or set(), root, profile)


# ---------------------------------------------------------------------------
# POSITIVE — the guard fires
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel", sorted(_PROFILE_SENSITIVE_FORCE_FILES))
@pytest.mark.parametrize("profile", sorted(_canon_incomplete_profiles()))
def test_release_machinery_file_is_skipped_on_a_canon_incomplete_profile(
    tmp_path: Path, rel: str, profile: str
) -> None:
    """Every (release-machinery file x canon-incomplete profile) pair is declined."""
    _write(tmp_path, rel, PLUGIN)
    line = _skip(tmp_path, rel, profile)
    assert line is not None, f"{rel} on profile {profile} must NOT be force-overwritten"
    assert rel in line
    assert profile in line, "the skip line must name the resolved profile"
    assert "would DELETE" in line, "the skip line must state what the overwrite would destroy"
    assert _PIPELINE_DRIFT_RC in line


def test_intrinsic_compiled_component_is_skipped_even_on_the_standard_profile(
    tmp_path: Path,
) -> None:
    """The intrinsic half: a plugin that never DECLARED a profile is still protected.

    This is the case nobody warned — an undeclared compiled plugin resolves to
    `standard`, so a profile-only guard would happily delete its release steps.
    """
    _write(tmp_path, PUBLISH_REL, PLUGIN)
    _make_compiled(tmp_path)
    line = _skip(tmp_path, PUBLISH_REL, "standard")
    assert line is not None, "an undeclared compiled plugin must still be protected"
    assert "ships a compiled component" in line


# ---------------------------------------------------------------------------
# NEGATIVE — the guard stays out of the way (the half that matters)
# ---------------------------------------------------------------------------


def test_standard_plugin_release_machinery_is_still_overwritten(tmp_path: Path) -> None:
    """A plain standard plugin keeps getting its publish.py refreshed."""
    _write(tmp_path, PUBLISH_REL, PLUGIN)
    assert _skip(tmp_path, PUBLISH_REL, "standard") is None


def test_remote_validation_profile_is_deliberately_not_guarded(tmp_path: Path) -> None:
    """`remote-validation` is modelled by the standard template, so a refresh is safe."""
    _write(tmp_path, PUBLISH_REL, PLUGIN)
    assert _skip(tmp_path, PUBLISH_REL, "remote-validation") is None


@pytest.mark.parametrize("profile", sorted(_canon_incomplete_profiles()))
def test_non_release_machinery_files_are_still_overwritten(tmp_path: Path, profile: str) -> None:
    """The guard is scoped to the two release-machinery files, not all of canon.

    cliff.toml is toolchain-agnostic: canon models it fully, so even a
    submodule-build plugin must keep receiving it.
    """
    _write(tmp_path, CLIFF_REL, PLUGIN)
    assert CLIFF_REL not in _PROFILE_SENSITIVE_FORCE_FILES
    assert _skip(tmp_path, CLIFF_REL, profile) is None


def test_byte_identical_file_is_not_reported_as_a_declined_refresh(tmp_path: Path) -> None:
    """A plugin already ON canon gets no noisy skip line."""
    f = _write(tmp_path, PUBLISH_REL, CANON)
    assert (
        _force_template_skip_reason(f, PUBLISH_REL, CANON, set(), tmp_path, "submodule-build")
        is None
    )


def test_absent_file_is_still_created(tmp_path: Path) -> None:
    """The guard must never block the CREATE path — a missing file must be written."""
    f = tmp_path / PUBLISH_REL  # never created
    assert (
        _force_template_skip_reason(f, PUBLISH_REL, CANON, set(), tmp_path, "submodule-build")
        is None
    )


def test_declared_divergence_keeps_its_own_message(tmp_path: Path) -> None:
    """An author's explicit declaration is reported as such, not as the profile guard."""
    _write(tmp_path, PUBLISH_REL, PLUGIN)
    line = _skip(tmp_path, PUBLISH_REL, "submodule-build", divergence={PUBLISH_REL})
    assert line is not None
    assert "marked intentional_divergence" in line
    assert "would DELETE" not in line


def test_guard_profile_set_matches_the_pipeline_profile_constants() -> None:
    """The guarded profiles are read from cpv_pipeline_profile, never hand-copied."""
    from cpv_pipeline_profile import (  # noqa: E402
        PROFILE_BINARY_RELEASE,
        PROFILE_REMOTE_VALIDATION,
        PROFILE_STANDARD,
        PROFILE_SUBMODULE_BUILD,
    )

    guarded = _canon_incomplete_profiles()
    assert guarded == {PROFILE_SUBMODULE_BUILD, PROFILE_BINARY_RELEASE}
    assert PROFILE_STANDARD not in guarded
    assert PROFILE_REMOTE_VALIDATION not in guarded


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
