"""The release workflow runs validation and the test suite CONCURRENTLY, sharded.

Both CPV's own `.github/workflows/release.yml` and the canon it emits
(`generate_plugin_repo.gen_release_yml`) used to be a SINGLE job that ran
validation and then the whole suite SERIALLY (`pytest tests/ -v`) before cutting
the release. Measured on CPV: 839s of wall clock, of which 577s was the serial
suite — and `publish.py`'s Gate 14 waits for every workflow on the released
commit, so that time is paid by every publish.

Validation and the suite are independent gates on the same commit, so they now
run side by side, and the suite is sharded with the same pytest-split matrix
`ci.yml` already uses.

Two properties are load-bearing and easy to lose in a later edit:

* The shards stay SERIAL (no ``-n``). Sharding buys wall clock; it must never
  buy it by giving up the order-dependent serial-pollution catch that running
  each shard serially preserves (TRDD-K7P2XR4Q).
* The release job is FAIL-CLOSED on both gates. A release is precisely the
  thing that must not proceed on a red gate, so it `needs` both and carries no
  ``if: always()`` escape.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_plugin_repo import (  # noqa: E402
    TEST_SHARD_COUNT,
    PluginParams,
    gen_release_yml,
)

OWN_RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _params() -> PluginParams:
    return PluginParams(
        name="sample-plugin",
        description="d",
        author="Emasoft",
        author_email="713559+Emasoft@users.noreply.github.com",
        license="MIT",
        python_version="3.12",
        github_owner="Emasoft",
        marketplace="emasoft-plugins",
        version="0.1.0",
        language="python",
        self_marketplace=False,
        strip_dev=False,
        marketplace_owner="Emasoft",
        cpv_ref="",
        cpv_source="git",
        force_notify=False,
    )


def _own() -> dict:
    return yaml.safe_load(OWN_RELEASE_YML.read_text())


def _canon() -> dict:
    return yaml.safe_load(gen_release_yml(_params()))


def _steps(parsed: dict) -> list[dict]:
    return [step for job in parsed["jobs"].values() for step in job["steps"]]


def _pytest_commands(parsed: dict) -> list[str]:
    return [
        step["run"]
        for step in _steps(parsed)
        if "run" in step and "pytest" in step["run"]
    ]


def _runs_validation(step: dict) -> bool:
    """True when this step runs CPV validation.

    BOTH spellings are required. CPV's own workflow invokes
    ``validate_plugin.py`` directly; the emitted canon shells out to
    ``cpv-remote-validate`` so a scaffolded plugin does not vendor the
    validator. A detector that knows only the first one silently reports "this
    job does not validate" for every generated plugin, which makes the
    assertions below pass without testing anything — measured: with only the
    ``validate_plugin`` spelling, the canon's own single-job baseline (which
    demonstrably validated AND released) was scored as not validating at all.
    """
    run = step.get("run", "")
    return "validate_plugin" in run or "cpv-remote-validate" in run


# ---------------------------------------------------------------------------
# The suite is sharded, not serial-whole
# ---------------------------------------------------------------------------


def test_own_release_workflow_shards_the_suite() -> None:
    """CPV's own release.yml runs the suite as a pytest-split matrix."""
    parsed = _own()
    shard_jobs = [j for j in parsed["jobs"].values() if "strategy" in j]
    assert shard_jobs, "release.yml has no matrix job — the suite is not sharded"
    groups = shard_jobs[0]["strategy"]["matrix"]["group"]
    assert len(groups) >= 2, f"a 1-way split is not a split: {groups}"

    commands = _pytest_commands(parsed)
    assert commands, "no pytest step at all in release.yml"
    for cmd in commands:
        assert "--splits" in cmd and "--group" in cmd, (
            f"pytest step is not sharded, so it runs the whole suite serially: {cmd}"
        )


def test_canon_release_workflow_shards_the_suite() -> None:
    """The emitted canon shards too — the higher-impact instance.

    Fixing only CPV's own tree would leave every scaffolded plugin inheriting
    the unsharded release path.
    """
    parsed = _canon()
    shard_jobs = [j for j in parsed["jobs"].values() if "strategy" in j]
    assert shard_jobs, "emitted release.yml has no matrix job"
    groups = shard_jobs[0]["strategy"]["matrix"]["group"]
    assert groups == list(range(1, TEST_SHARD_COUNT + 1)), (
        f"matrix groups {groups} do not match TEST_SHARD_COUNT={TEST_SHARD_COUNT}; "
        "the matrix and pyproject's pytest-split requirement would desync"
    )

    commands = _pytest_commands(parsed)
    assert commands, "no pytest step at all in the emitted release.yml"
    for cmd in commands:
        assert f"--splits {TEST_SHARD_COUNT}" in cmd, f"shard count desync: {cmd}"


# ---------------------------------------------------------------------------
# Sharding must not buy wall clock with coverage
# ---------------------------------------------------------------------------


def test_shards_stay_serial_in_both_own_and_canon() -> None:
    """No shard uses xdist.

    `-n auto` inside a shard would re-order tests within it and destroy the
    serial-pollution catch that is the whole reason each shard runs serially.
    """
    for label, parsed in (("own", _own()), ("canon", _canon())):
        for cmd in _pytest_commands(parsed):
            # Judge the pytest INVOCATION lines, not the whole `run:` block. The
            # block is shell, and ` -n ` is also POSIX test's string-is-non-empty
            # operator — the guard around the test-discovery `find` (issue #215)
            # uses it, and a whole-block substring match read that as xdist.
            for line in cmd.splitlines():
                if "pytest" not in line:
                    continue
                assert " -n " not in line and "--dist" not in line, (
                    f"{label} release shard runs under xdist, losing the "
                    f"order-dependent pollution catch: {line.strip()}"
                )


# ---------------------------------------------------------------------------
# The release job is fail-closed on BOTH gates
# ---------------------------------------------------------------------------


def test_release_job_needs_both_gates_and_has_no_always_escape() -> None:
    """The job that cuts the release depends on validation AND every shard."""
    for label, parsed in (("own", _own()), ("canon", _canon())):
        jobs = parsed["jobs"]
        releasing = [
            (name, job)
            for name, job in jobs.items()
            if any("gh release" in s.get("run", "") for s in job["steps"])
        ]
        assert len(releasing) == 1, f"{label}: expected one releasing job, got {releasing}"
        name, job = releasing[0]

        needs = job.get("needs") or []
        assert len(needs) >= 2, f"{label}: releasing job {name!r} needs only {needs}"

        # Every named dependency must resolve to a real job, or `needs` is a
        # no-op that reads as a gate.
        for dep in needs:
            assert dep in jobs, f"{label}: {name!r} needs unknown job {dep!r}"

        # A validation gate and a test gate, not two of the same thing.
        dep_steps = [s for dep in needs for s in jobs[dep]["steps"]]
        assert any("pytest" in s.get("run", "") for s in dep_steps), (
            f"{label}: releasing job does not depend on any job that runs tests"
        )
        assert any(_runs_validation(s) for s in dep_steps), (
            f"{label}: releasing job does not depend on any job that validates"
        )

        assert "always()" not in str(job.get("if", "")), (
            f"{label}: releasing job carries an `if: always()` escape, so a red "
            "gate would not stop the release"
        )


def test_validation_report_is_handed_over_as_an_artifact() -> None:
    """The report is a release asset, so the split must not lose it.

    Validation moved out of the releasing job, so the report it produces has to
    reach that job some other way. Anything else means either a missing release
    asset or the releasing job re-running the validator to regenerate a file it
    already had.
    """
    for label, parsed in (("own", _own()), ("canon", _canon())):
        steps = _steps(parsed)
        uploads = [s for s in steps if "upload-artifact" in s.get("uses", "")]
        downloads = [s for s in steps if "download-artifact" in s.get("uses", "")]
        assert uploads, f"{label}: validation report is never uploaded"
        assert downloads, f"{label}: validation report is never downloaded"
        assert uploads[0]["with"]["name"] == downloads[0]["with"]["name"], (
            f"{label}: artifact name mismatch between upload and download — the "
            "download would silently produce nothing"
        )
        # if-no-files-found: error is what turns "validation produced nothing"
        # into a failure instead of an empty release asset.
        assert uploads[0]["with"].get("if-no-files-found") == "error", (
            f"{label}: a missing validation report would upload silently"
        )


def test_no_job_both_validates_and_releases() -> None:
    """Validation and release are separate jobs — that separation IS the speedup."""
    for label, parsed in (("own", _own()), ("canon", _canon())):
        saw_validation = False
        for name, job in parsed["jobs"].items():
            validates = any(_runs_validation(s) for s in job["steps"])
            saw_validation = saw_validation or validates
            releases = any("gh release" in s.get("run", "") for s in job["steps"])
            assert not (validates and releases), (
                f"{label}: job {name!r} both validates and releases, so they are "
                "serialised again"
            )
        # Guard the guard: if no job is recognised as validating, the loop above
        # is vacuously true and would keep passing through any regression.
        assert saw_validation, (
            f"{label}: no step was recognised as running validation — the "
            "assertion above proved nothing"
        )
