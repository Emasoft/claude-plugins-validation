"""Tests for CIP-6 — the stale / invalid CPV-ref CI-parity static check (TRDD-HZSI0BZ6).

The dominant downstream CI-green failure mode: a ``.github/workflows/*.yml``
pins ``claude-plugins-validation`` at a git ref that does not resolve. CPV's
default branch is ``master``, so an old ``@main`` pin (from a plugin migrated by
a pre-#139 CPV and never re-published) fails ``uvx --from git+…@main`` with
``Git operation failed / Updating … (main)`` and red-CIs forever.

CIP-6 FIRES **MAJOR** when the pinned ``<ref>`` is NOT one of {``master``, a
``v<semver>`` tag, a 7-40 char hex commit SHA}. Every test is two-sided: a
DEFECTIVE-ref tree FIRES it, and a valid-ref / no-pin / no-workflows tree PASSES.
The check is STATIC + OFFLINE (it never hits the network) and re2-safe.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cpv_ci_parity_checks import ParityFinding, check_ci_parity  # noqa: E402

CPV_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_plugin(tmp_path: Path) -> Path:
    """Lay down a minimal valid plugin tree with a plugin.json + scripts/."""
    root = tmp_path / "plug"
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True)
    (cp / "plugin.json").write_text(
        json.dumps(
            {"name": "plug", "version": "0.1.0", "description": "t", "author": "X"}, indent=2
        ),
        encoding="utf-8",
    )
    (root / "scripts").mkdir()
    return root


def _write_workflow(root: Path, name: str, body: str) -> Path:
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    path = wf / name
    path.write_text(body, encoding="utf-8")
    return path


def _ids(findings: list[ParityFinding]) -> set[str]:
    return {f.check_id for f in findings}


def _ci_with_cpv_ref(ref: str) -> str:
    """A ci.yml whose Validate step pins CPV at ``@<ref>`` (the uvx-from-git form)."""
    return (
        "name: CI\n"
        "jobs:\n"
        "  validate:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - run: >-\n"
        "          uvx --from git+https://github.com/Emasoft/"
        f"claude-plugins-validation@{ref}\n"
        "          --with pyyaml cpv-remote-validate plugin . --strict\n"
    )


# ===========================================================================
# CIP-6 FIRES on a stale / invalid ref
# ===========================================================================


def test_cip6_fires_on_main_ref(tmp_path: Path) -> None:
    """The classic `@main` pin (CPV's default branch is `master`) FIRES CIP-6."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("main"))
    findings = check_ci_parity(root)
    assert "CIP-6" in _ids(findings)
    cip6 = [f for f in findings if f.check_id == "CIP-6"][0]
    assert cip6.severity == "MAJOR"
    assert cip6.file == ".github/workflows/ci.yml"
    # The message names the bad ref and the fix.
    assert "@main" in cip6.message
    assert "master" in cip6.message
    assert "standardize --fix" in cip6.message


def test_cip6_fires_on_develop_ref(tmp_path: Path) -> None:
    """A `@develop` branch pin FIRES CIP-6."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("develop"))
    assert "CIP-6" in _ids(check_ci_parity(root))


def test_cip6_fires_on_head_ref(tmp_path: Path) -> None:
    """A `@HEAD` pin FIRES CIP-6 (HEAD is 4 chars — not a 7+ hex SHA)."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("HEAD"))
    assert "CIP-6" in _ids(check_ci_parity(root))


def test_cip6_fires_on_feature_branch_ref(tmp_path: Path) -> None:
    """A `@feature-x` branch pin FIRES CIP-6."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("feature-x"))
    assert "CIP-6" in _ids(check_ci_parity(root))


def test_cip6_fires_on_branchy_ref_path(tmp_path: Path) -> None:
    """A `@refs/heads/main`-style ref (carries a `/`) FIRES CIP-6."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("refs/heads/main"))
    assert "CIP-6" in _ids(check_ci_parity(root))


def test_cip6_fires_on_dot_git_at_form(tmp_path: Path) -> None:
    """The `…claude-plugins-validation.git@<ref>` pin form is recognised + FIRES on `@main`."""
    root = _make_plugin(tmp_path)
    _write_workflow(
        root,
        "release.yml",
        "name: Release\njobs:\n  rel:\n    steps:\n      - run: >-\n"
        "          pip install "
        "git+https://github.com/Emasoft/claude-plugins-validation.git@main\n",
    )
    findings = check_ci_parity(root)
    assert "CIP-6" in _ids(findings)
    assert [f for f in findings if f.check_id == "CIP-6"][0].file == ".github/workflows/release.yml"


def test_cip6_fires_on_short_branch_with_nonhex(tmp_path: Path) -> None:
    """A 7-char branch name containing a non-hex letter is NOT a SHA → FIRES."""
    root = _make_plugin(tmp_path)
    # `staging` is 7 chars but has g/t/n/s (non-hex) → not a commit SHA.
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("staging"))
    assert "CIP-6" in _ids(check_ci_parity(root))


# ===========================================================================
# CIP-6 PASSES on a valid ref
# ===========================================================================


def test_cip6_passes_on_master_ref(tmp_path: Path) -> None:
    """`@master` (CPV's default branch) PASSES CIP-6."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("master"))
    assert "CIP-6" not in _ids(check_ci_parity(root))


def test_cip6_passes_on_semver_tag(tmp_path: Path) -> None:
    """A `@v2.146.0` release-tag pin PASSES CIP-6."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("v2.146.0"))
    assert "CIP-6" not in _ids(check_ci_parity(root))


def test_cip6_passes_on_semver_prerelease_tag(tmp_path: Path) -> None:
    """A `@v2.146.0-rc.1` prerelease-tag pin PASSES CIP-6."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("v2.146.0-rc.1"))
    assert "CIP-6" not in _ids(check_ci_parity(root))


def test_cip6_passes_on_short_sha(tmp_path: Path) -> None:
    """A `@fe71945` short commit SHA (7 hex) PASSES CIP-6."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("fe71945"))
    assert "CIP-6" not in _ids(check_ci_parity(root))


def test_cip6_passes_on_full_sha(tmp_path: Path) -> None:
    """A full 40-char commit SHA PASSES CIP-6."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("a" * 40))
    assert "CIP-6" not in _ids(check_ci_parity(root))


def test_cip6_passes_on_pypi_wheel_pin(tmp_path: Path) -> None:
    """The `claude-plugins-validation==<ver>` PyPI-wheel form has no git ref → PASS.

    The `==` selector (the #137 wheel path) carries no git ref to validate, so
    CIP-6 must NOT match it (its pin regex requires `@`, not `==`).
    """
    root = _make_plugin(tmp_path)
    _write_workflow(
        root,
        "ci.yml",
        "name: CI\njobs:\n  v:\n    steps:\n      - run: >-\n"
        "          uvx --from claude-plugins-validation==2.146.0 "
        "cpv-remote-validate plugin . --strict\n",
    )
    assert "CIP-6" not in _ids(check_ci_parity(root))


# ===========================================================================
# CIP-6 does not fire when there is nothing to check
# ===========================================================================


def test_cip6_passes_when_no_cpv_pin(tmp_path: Path) -> None:
    """A workflow that does not pin CPV at all draws no CIP-6 finding."""
    root = _make_plugin(tmp_path)
    _write_workflow(
        root,
        "ci.yml",
        "name: CI\njobs:\n  lint:\n    steps:\n      - run: uv run ruff check scripts/\n",
    )
    assert "CIP-6" not in _ids(check_ci_parity(root))


def test_cip6_passes_when_no_workflows_dir(tmp_path: Path) -> None:
    """A plugin with no .github/workflows/ draws no CIP-6 finding."""
    root = _make_plugin(tmp_path)
    assert "CIP-6" not in _ids(check_ci_parity(root))


def test_cip6_clean_on_cpv_repo() -> None:
    """CPV's OWN tree draws no CIP-6 finding (it does not uvx-pin itself in CI)."""
    assert "CIP-6" not in _ids(check_ci_parity(CPV_ROOT))


# ===========================================================================
# Mixed: one good ref + one bad ref in the same tree
# ===========================================================================


def test_cip6_fires_only_on_the_bad_ref_across_files(tmp_path: Path) -> None:
    """With a `@master` ci.yml and a `@main` release.yml, CIP-6 fires ONCE — on release.yml."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("master"))
    _write_workflow(root, "release.yml", _ci_with_cpv_ref("main"))
    findings = [f for f in check_ci_parity(root) if f.check_id == "CIP-6"]
    assert len(findings) == 1
    assert findings[0].file == ".github/workflows/release.yml"


def test_cip6_fires_per_bad_pin_in_one_file(tmp_path: Path) -> None:
    """Two stale pins in one workflow yield two CIP-6 findings (one per occurrence)."""
    root = _make_plugin(tmp_path)
    body = (
        "name: CI\njobs:\n  a:\n    steps:\n"
        "      - run: uvx --from git+https://github.com/Emasoft/"
        "claude-plugins-validation@main cpv-remote-validate plugin .\n"
        "  b:\n    steps:\n"
        "      - run: pip install "
        "git+https://github.com/Emasoft/claude-plugins-validation.git@develop\n"
    )
    _write_workflow(root, "ci.yml", body)
    findings = [f for f in check_ci_parity(root) if f.check_id == "CIP-6"]
    assert len(findings) == 2
    assert all(f.severity == "MAJOR" for f in findings)


# ===========================================================================
# Other CIP checks are unaffected by a clean CIP-6 path
# ===========================================================================


def test_cip6_addition_does_not_disturb_other_checks(tmp_path: Path) -> None:
    """A tree with a `@main` pin fires CIP-6 but no other CIP check on an
    otherwise-clean workflow (CIP-6 is independent of CIP-1..5)."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _ci_with_cpv_ref("main"))
    ids = _ids(check_ci_parity(root))
    assert "CIP-6" in ids
    assert ids == {"CIP-6"}
