"""Two-sided tests for the publish-gate stale-CPV-ref block (TRDD-35BN0TEI).

A plugin migrated by an OLD CPV pins
``git+https://github.com/Emasoft/claude-plugins-validation@main`` in its
``.github/workflows/*.yml``. CPV's default branch is ``master``, so ``@main``
404s on the runner (``Git operation failed / Updating ... (main)``) and the
workflow red-CIs forever. ``validate_workflow_cpv_ref`` enforces the CIP-6 rule
INSIDE ``validate_plugin`` (the ``--strict`` gate ``publish.py`` Gate 3 runs), so
a stale ref BLOCKS the publish instead of red-CIing post-push.

These tests prove the gate is TWO-SIDED: it FIRES MAJOR on a non-resolvable ref
(``@main`` / ``@develop`` / ``@HEAD`` / a branch) and stays SILENT on a resolvable
one (``@master`` / ``@v<semver>`` / a SHA / no git pin at all / the PyPI form).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import (  # noqa: E402
    _cpv_workflow_ref_is_valid,
    validate_workflow_cpv_ref,
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _run(workflows: dict[str, str]) -> ValidationReport:
    """Build a temp plugin with the given ``.github/workflows`` files, validate."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wf_dir = root / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        for name, body in workflows.items():
            (wf_dir / name).write_text(body, encoding="utf-8")
        report = ValidationReport()
        validate_workflow_cpv_ref(root, report)
        # Detach results from the temp dir so assertions outlive the context.
        out = ValidationReport()
        out.results = list(report.results)
        return out


def _majors(report: ValidationReport) -> list:
    return [r for r in report.results if r.level == "MAJOR"]


def _gitref(ref: str, *, dot_git: bool = False, with_form: bool = False) -> str:
    """A one-line workflow body pinning the CPV git ref ``@ref``."""
    suffix = ".git" if dot_git else ""
    url = f"git+https://github.com/Emasoft/claude-plugins-validation{suffix}@{ref}"
    if with_form:
        run = f"uvx --from {url} --with pyyaml cpv-remote-validate plugin . --strict"
    else:
        run = f"uvx --from {url} cpv-remote-validate plugin . --strict"
    return f"name: CI\non: push\njobs:\n  v:\n    steps:\n      - run: {run}\n"


# --------------------------------------------------------------------------- #
# FIRES — a non-resolvable ref must be a blocking MAJOR
# --------------------------------------------------------------------------- #


def test_main_ref_fires_major() -> None:
    """The classic `@main` pin (CPV's default branch is `master`) FIRES MAJOR."""
    rep = _run({"ci.yml": _gitref("main")})
    majors = _majors(rep)
    assert len(majors) == 1
    assert "@main" in majors[0].message
    assert "ci.yml" in (majors[0].file or "")


def test_develop_ref_fires_major() -> None:
    """A `@develop` branch pin does not resolve → MAJOR."""
    assert len(_majors(_run({"ci.yml": _gitref("develop")}))) == 1


def test_head_ref_fires_major() -> None:
    """A `@HEAD` pin does not resolve on the runner → MAJOR."""
    assert len(_majors(_run({"ci.yml": _gitref("HEAD")}))) == 1


def test_branch_with_slash_fires_major() -> None:
    """A `@feature/x` branch pin → MAJOR (the ref captures up to whitespace)."""
    assert len(_majors(_run({"ci.yml": _gitref("feature/x")}))) == 1


def test_dot_git_main_form_fires_major() -> None:
    """The `…claude-plugins-validation.git@main` form is recognised + FIRES."""
    assert len(_majors(_run({"ci.yml": _gitref("main", dot_git=True)}))) == 1


def test_with_form_main_fires_major() -> None:
    """The `uvx --from git+…@main --with pyyaml` form FIRES (matches the real error)."""
    assert len(_majors(_run({"release.yml": _gitref("main", with_form=True)}))) == 1


def test_only_release_yml_fires_when_ci_is_pinned_master() -> None:
    """A `@master` ci.yml + a `@main` release.yml → exactly ONE MAJOR (release.yml)."""
    rep = _run({"ci.yml": _gitref("master"), "release.yml": _gitref("main")})
    majors = _majors(rep)
    assert len(majors) == 1
    assert "release.yml" in (majors[0].file or "")


def test_line_number_points_at_the_pin() -> None:
    """The reported line number is the line carrying the stale pin."""
    body = "name: CI\non: push\njobs:\n  v:\n    steps:\n      - run: " + _gitref("main").splitlines()[-1].split("- run: ", 1)[1] + "\n"
    rep = _run({"ci.yml": body})
    majors = _majors(rep)
    assert len(majors) == 1
    assert majors[0].line == body[: body.index("@main")].count("\n") + 1


# --------------------------------------------------------------------------- #
# PASSES — a resolvable ref (or no pin) must produce ZERO MAJOR
# --------------------------------------------------------------------------- #


def test_master_ref_passes() -> None:
    """`@master` is CPV's default branch → resolves → ZERO MAJOR."""
    assert _majors(_run({"ci.yml": _gitref("master")})) == []


def test_semver_tag_passes() -> None:
    """A `@v<semver>` tag is a stable, resolvable pin → ZERO MAJOR."""
    assert _majors(_run({"ci.yml": _gitref("v2.147.1")})) == []


def test_prerelease_tag_passes() -> None:
    """A `@v…-rc.1` pre-release tag resolves → ZERO MAJOR."""
    assert _majors(_run({"ci.yml": _gitref("v2.147.1-rc.1")})) == []


def test_short_sha_passes() -> None:
    """A 7-hex abbreviated commit SHA resolves → ZERO MAJOR."""
    assert _majors(_run({"ci.yml": _gitref("abc1234")})) == []


def test_full_sha_passes() -> None:
    """A 40-hex full commit SHA resolves → ZERO MAJOR."""
    assert _majors(_run({"ci.yml": _gitref("0" * 40)})) == []


def test_non_git_from_form_passes() -> None:
    """A `--from <name>` with no `git+...@` pin doesn't match the rule → ZERO MAJOR."""
    body = (
        "name: CI\non: push\njobs:\n  v:\n    steps:\n"
        "      - run: uvx --from claude-plugins-validation cpv-remote-validate plugin . --strict\n"
    )
    rep = _run({"ci.yml": body})
    assert _majors(rep) == []


def test_no_cpv_ref_at_all_passes() -> None:
    """A workflow with no CPV pin (e.g. local-script invocation) → ZERO MAJOR."""
    body = "name: CI\non: push\njobs:\n  v:\n    steps:\n      - run: python3 scripts/validate_plugin.py .\n"
    assert _majors(_run({"ci.yml": body})) == []


def test_no_workflows_dir_is_noop() -> None:
    """No `.github/workflows` dir → no crash, no findings."""
    with tempfile.TemporaryDirectory() as td:
        report = ValidationReport()
        validate_workflow_cpv_ref(Path(td), report)
        assert report.results == []


# --------------------------------------------------------------------------- #
# The shared rule helper — exhaustive two-sided unit coverage
# --------------------------------------------------------------------------- #


def test_ref_validity_rule_two_sided() -> None:
    """`_cpv_workflow_ref_is_valid` matches the CIP-6 rule exactly (both sides)."""
    valid = ["master", "v2.147.1", "v2.147.1-rc.1", "v10.0.0+build.5", "abc1234", "0" * 40, "deadbeef"]
    invalid = ["main", "develop", "HEAD", "feature/x", "v2.147", "vabc", "master2", "", "latest"]
    for r in valid:
        assert _cpv_workflow_ref_is_valid(r) is True, r
    for r in invalid:
        assert _cpv_workflow_ref_is_valid(r) is False, r


# --------------------------------------------------------------------------- #
# Registration — the gate is wired into the dispatch list (regression guard)
# --------------------------------------------------------------------------- #


def test_validator_is_registered_in_dispatch_list() -> None:
    """The gate must be in validate_plugin's dispatch list, else it never runs."""
    src = (SCRIPTS / "validate_plugin.py").read_text(encoding="utf-8")
    assert '"validate_workflow_cpv_ref", validate_workflow_cpv_ref' in src
