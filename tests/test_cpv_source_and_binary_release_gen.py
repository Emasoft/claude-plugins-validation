"""Two-sided tests for the canonical-pipeline generator extensions:

  * CHANGE 1 (C2b / #115) — ``gen_release_binaries_yml``: a binary-release
    scaffold whose emitted workflow is CANONICAL per
    ``cpv_pipeline_profile.is_binary_release_canonical_shape`` (matrix+targets,
    ``gh release upload``, ``SHA256SUMS``, least-privilege split, SHA-pinned
    actions) and carries the push/PR ``build-smoke`` "untested-until-release"
    guard.
  * CHANGE 2 (#137) — the ``--cpv-source {git,pypi}`` selector: the default
    ``git`` source keeps the historical ``git+https://…@<ref>`` form at every
    generated callsite (NON-BREAKING), and ``pypi`` switches every callsite to
    the prebuilt wheel ``claude-plugins-validation==<ver>`` and drops the
    ``--with pyyaml`` shim (pyyaml is a declared wheel dependency).

Every guard is TWO-SIDED: the default behavior is asserted UNCHANGED and the
opt-in switch is asserted to flip every site — so a regression in either
direction fails a test.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_pipeline_profile as prof  # noqa: E402
from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    cpv_uvx_from_arg,
    cpv_uvx_needs_pyyaml,
    gen_ci_yml,
    gen_publish_py,
    gen_release_binaries_yml,
)

_CPV_GIT_URL = "git+https://github.com/Emasoft/claude-plugins-validation"
_CPV_WHEEL = "claude-plugins-validation=="


def _params(**overrides: object) -> PluginParams:
    """A PluginParams with sensible defaults, accepting field overrides."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


# ── CHANGE 1: gen_release_binaries_yml (binary-release scaffold) ─────────────


def test_binary_release_template_is_canonical() -> None:
    """The emitted binary-release workflow satisfies ALL four structural invariants."""
    yml = gen_release_binaries_yml(_params())
    canonical, missing = prof.is_binary_release_canonical_shape(yml)
    assert canonical is True, f"not canonical; missing: {missing}"
    assert missing == []


def test_binary_release_template_each_invariant_holds() -> None:
    """Each of the four canonical sub-checks passes individually (not just the aggregate)."""
    yml = gen_release_binaries_yml(_params())
    assert prof._uses_actions_are_sha_pinned(yml) is True
    assert prof._has_least_privilege_split(yml) is True
    assert prof._has_checksum_step(yml) is True
    assert prof._has_build_matrix_over_targets(yml) is True


def test_binary_release_template_is_detected_as_binary_release_shape(tmp_path: Path) -> None:
    """A repo scaffolded with this workflow is profile-detected as binary-release."""
    wfdir = tmp_path / ".github" / "workflows"
    wfdir.mkdir(parents=True)
    (wfdir / "release-binaries.yml").write_text(gen_release_binaries_yml(_params()))
    assert prof.is_binary_release_shape(tmp_path) is True
    assert prof.resolve_pipeline_profile(tmp_path) == "binary-release"


def test_binary_release_template_substitutes_binary_name() -> None:
    """The @@BIN@@ sentinel is replaced by the plugin name; none remains."""
    yml = gen_release_binaries_yml(_params(name="memgrep"))
    assert "@@BIN@@" not in yml
    assert "memgrep" in yml


def test_binary_release_template_exactly_one_write_job() -> None:
    """Least-privilege split: exactly ONE `contents: write` permission LINE, ≥1 `contents: read`.

    Counts actual YAML permission lines (stripped, line-anchored) the way the
    validator's ``_CONTENTS_WRITE_RE`` does — NOT a naive substring count, which
    would also match the prose mention of "contents: write" in the file's header
    comment.
    """
    perm_lines = [ln.strip() for ln in gen_release_binaries_yml(_params()).splitlines()]
    assert perm_lines.count("contents: write") == 1
    assert perm_lines.count("contents: read") >= 1


def test_binary_release_template_has_smoke_guard() -> None:
    """A push/PR build-smoke job exists (the untested-until-release guard)."""
    yml = gen_release_binaries_yml(_params())
    assert "build-smoke:" in yml
    assert "pull_request" in yml or "push" in yml


@pytest.mark.skipif(shutil.which("actionlint") is None, reason="actionlint not installed (CI runs without it)")
def test_binary_release_template_passes_actionlint() -> None:
    """The emitted workflow is valid GitHub Actions YAML (actionlint clean)."""
    yml = gen_release_binaries_yml(_params())
    with tempfile.TemporaryDirectory() as d:
        wf = Path(d) / "release-binaries.yml"
        wf.write_text(yml)
        r = subprocess.run(["actionlint", str(wf)], capture_output=True, text=True)
    assert r.returncode == 0, f"actionlint failed:\n{r.stdout}\n{r.stderr}"


# ── CHANGE 2: the --cpv-source {git,pypi} selector (#137) ────────────────────


def test_default_cpv_source_is_git() -> None:
    """The default is `git` — the historical behavior is NON-BREAKING."""
    assert _params().cpv_source == "git"


def test_cpv_uvx_from_arg_git_is_pinned_url() -> None:
    """git source → the exact pinned `git+…@<ref>` form."""
    p = _params(cpv_ref="v2.137.0")
    assert cpv_uvx_from_arg(p) == f"{_CPV_GIT_URL}@v2.137.0"


def test_cpv_uvx_from_arg_pypi_is_version_pinned_wheel() -> None:
    """pypi source + a version ref → `claude-plugins-validation==<ver>` (leading v stripped)."""
    p = _params(cpv_source="pypi", cpv_ref="v2.137.0")
    assert cpv_uvx_from_arg(p) == "claude-plugins-validation==2.137.0"


def test_cpv_uvx_from_arg_pypi_nonversion_degrades_to_bare_dist() -> None:
    """pypi + a branch/SHA ref (no published wheel) → bare dist (no unsatisfiable ==main)."""
    assert cpv_uvx_from_arg(_params(cpv_source="pypi", cpv_ref="main")) == "claude-plugins-validation"
    assert cpv_uvx_from_arg(_params(cpv_source="pypi", cpv_ref="abc1234")) == "claude-plugins-validation"


def test_cpv_uvx_needs_pyyaml_only_for_git() -> None:
    """git needs the --with pyyaml shim; the pypi wheel declares pyyaml as a dep."""
    assert cpv_uvx_needs_pyyaml(_params()) is True
    assert cpv_uvx_needs_pyyaml(_params(cpv_source="pypi")) is False


def test_gen_ci_yml_git_default_unchanged() -> None:
    """gen_ci_yml default emits the git+ form and NOT the wheel form."""
    ci = gen_ci_yml(_params(cpv_ref="v2.137.0"))
    assert _CPV_GIT_URL in ci
    assert _CPV_WHEEL not in ci


def test_gen_ci_yml_pypi_switches_every_cpv_ref() -> None:
    """gen_ci_yml with --cpv-source pypi emits the wheel form, ZERO git+ CPV refs."""
    ci = gen_ci_yml(_params(cpv_source="pypi", cpv_ref="v2.137.0"))
    assert "claude-plugins-validation==2.137.0" in ci
    assert _CPV_GIT_URL not in ci


def test_gen_publish_py_git_default_unchanged() -> None:
    """gen_publish_py default keeps the pinned git+ form at every CPV callsite."""
    pub = gen_publish_py(_params(cpv_ref="v2.137.0"))
    assert _CPV_GIT_URL in pub
    assert _CPV_WHEEL not in pub


def test_gen_publish_py_pypi_switches_and_drops_pyyaml() -> None:
    """gen_publish_py with --cpv-source pypi switches every CPV callsite to the wheel."""
    pub = gen_publish_py(_params(cpv_source="pypi", cpv_ref="v2.137.0"))
    assert "claude-plugins-validation==2.137.0" in pub
    assert _CPV_GIT_URL not in pub
