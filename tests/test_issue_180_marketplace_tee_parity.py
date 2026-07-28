"""The marketplace lineage must carry the same #180 diagnosability as plugins.

`generate_marketplace_repo.py` was already FAIL-CLOSED — its shared
`_cpv_verdict_shell` helper does the RC-8 classification, so there was no gate
hole here. But its three validator call sites still used the blind
`> file 2>&1` + `$?` shape the plugin lineage moved off in v3.22.0, and the
helper opened with a `cat` of the report: a job killed at its timeout never
reaches that `cat`, so the log showed nothing at all about what was running.

Two traps this lineage inherits, both already paid for once on the plugin side:

* `$?` after a pipeline is `tee`'s status. GitHub's default `run:` shell is
  `bash -e {0}` WITHOUT `-o pipefail`, so reading it would report success for
  every failed validation — strictly worse than the hang it replaces.
* shellcheck can infer numeric-ness through `exit_code=$?` but NOT through
  `PIPESTATUS`, so an unquoted expansion trips SC2086 — which the emitted
  Lint job's actionlint turns into red CI for every scaffolded marketplace.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_marketplace_repo import _cpv_verdict_shell, _validate_workflow  # noqa: E402


@pytest.fixture(scope="module")
def workflow() -> str:
    return _validate_workflow()


def _run_blocks(text: str) -> list[str]:
    """Every `run:` body in the workflow that invokes the CPV validator."""
    blocks = [chunk for chunk in text.split("run: |") if "cpv-remote-validate" in chunk]
    return [re.split(r"\n\s*-\s+name:", chunk)[0] for chunk in blocks]


def test_there_are_blocks_to_check(workflow: str) -> None:
    """LOAD-BEARING: if the scoping stops matching, every test below would
    pass over an empty list and prove nothing."""
    assert len(_run_blocks(workflow)) == 3, "expected the marketplace, per-plugin and pipeline gates"


def test_no_block_uses_the_blind_redirect(workflow: str) -> None:
    for block in _run_blocks(workflow):
        assert "| tee " in block, "validator output is not streamed"
        assert not re.search(r">\s*\"\$RUNNER_TEMP/[\w.-]+\"\s+2>&1", block), "still uses the blind redirect"


def test_no_block_reads_tees_exit_status(workflow: str) -> None:
    """The trap that would green every failed validation."""
    for block in _run_blocks(workflow):
        assert "exit_code=$?" not in block
        assert "rc=$?" not in block
        assert "PIPESTATUS[0]" in block


def test_exit_expansions_are_quoted(workflow: str) -> None:
    """Unquoted → SC2086 → actionlint → red CI on every scaffolded marketplace."""
    for block in _run_blocks(workflow):
        assert "[ $exit_code " not in block
        assert "[ $rc " not in block
        assert not re.search(r"\bexit \$exit_code\s*$", block, re.MULTILINE)


def test_every_validate_step_is_unbuffered(workflow: str) -> None:
    """Keeps the phase banners at their true timestamps — the thing that says
    WHICH phase a hung run died in."""
    assert workflow.count('PYTHONUNBUFFERED: "1"') == 3


def test_helper_no_longer_cats_the_report() -> None:
    """The report is streamed by `tee` now; a `cat` would duplicate it, and a
    trailing one is unreachable in exactly the run you need it for."""
    shell = _cpv_verdict_shell(
        label="Demo",
        exit_var="exit_code",
        report_path="$RUNNER_TEMP/demo.txt",
        marker="SUMMARY: CRITICAL=",
        indent="",
        pass_echo="ok",
        findings_action='exit "$exit_code"',
        infra_action="exit 1",
    )
    assert not shell.startswith("cat ")
    assert "\ncat " not in shell


def test_helper_stays_fail_closed() -> None:
    """UNCHANGED CONTRACT — this lineage was already fail-closed and must
    remain so: exit 0 passes, 1-4 WITH the marker is a verdict, anything else
    (127 command-not-found, 137 OOM kill) is an explicit infra failure."""
    shell = _cpv_verdict_shell(
        label="Demo",
        exit_var="exit_code",
        report_path="$RUNNER_TEMP/demo.txt",
        marker="SUMMARY: CRITICAL=",
        indent="",
        pass_echo="ok",
        findings_action='exit "$exit_code"',
        infra_action="exit 1",
    )
    assert 'grep -q "SUMMARY: CRITICAL="' in shell, "no proof the validator actually ran"
    assert "FAILED TO RUN" in shell, "no explicit non-verdict branch"
    assert re.search(r"else\n\s*echo[\s\S]*?\n\s*exit 1", shell), "infra branch does not fail"


def test_per_plugin_loop_still_counts_failures(workflow: str) -> None:
    """Inside the Layout B loop a failure must be COUNTED, not `exit`ed, so
    one broken nested plugin cannot hide the others — including an infra
    crash, which is not a pass."""
    assert workflow.count("failed=$((failed + 1))") == 2
