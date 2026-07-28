"""CPV's OWN release gate must be fail-closed — it was not, and nothing noticed.

CPV ships an RC-8 fail-closed handler to every plugin it scaffolds, and v3.22.1
fixed the canon reference docs that still taught the fail-open shape. Its own
`release.yml` had the same hole the whole time: it errored only on exit 1-4 and
fell through otherwise, so `command not found` (127) or an OOM kill (137) would
SILENTLY PASS and the release would proceed on a validation that never ran.

That is the third population from the fix-what-the-tool-emits lesson — the
tool's own tree — and the one easiest to forget precisely because the fix is
"already shipped" everywhere else.

CPV's verdict codes stop at 4 (`cpv_validation_common`: OK 0 / CRITICAL 1 /
MAJOR 2 / MINOR 3 / NIT 4), so any other code means the validator failed to run.
"""

from __future__ import annotations

import re
from pathlib import Path

_WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def _validate_step() -> str:
    text = _WF.read_text(encoding="utf-8")
    assert "validate_plugin.py" in text, "release.yml no longer runs the validator — test is stale"
    body = text.split("validate_plugin.py", 1)[1]
    # Stop at the next step so we only assert about this one.
    return re.split(r"\n\s{6}-\s+name:", body)[0]


def test_release_gate_requires_proof_the_validator_ran() -> None:
    """LOAD-BEARING: exit 1-4 alone is not proof of a verdict — the SUMMARY line
    is. Without it, an infra failure that happens to exit 1 reads as findings,
    and one that exits 127 reads as success."""
    step = _validate_step()
    assert "SUMMARY: CRITICAL=" in step, "the release gate does not require CPV's own SUMMARY marker"


def test_release_gate_has_an_explicit_non_verdict_branch() -> None:
    """Falling through after the 1-4 check is what made 127/137 pass silently."""
    step = _validate_step()
    assert "FAILED TO RUN" in step, "no explicit non-verdict branch — the gate falls through fail-open"
    assert re.search(r"FAILED TO RUN[\s\S]*?exit 1\b", step), "the non-verdict branch does not exit non-zero"


def test_release_gate_streams_and_reads_the_validators_status() -> None:
    """A job killed at its cap never reaches a trailing `cat`, so the log would
    show nothing at all about what was running (#180)."""
    step = _validate_step()
    assert "| tee validation-report.txt" in step, "the validate step no longer streams its output"
    assert "PIPESTATUS[0]" in step, "reads tee's status instead of the validator's"
    assert "exit_code=$?" not in step, "reads $? after a pipeline — that is tee's exit code"


def test_release_gate_quotes_exit_code() -> None:
    """shellcheck cannot infer numeric-ness through PIPESTATUS; unquoted trips
    SC2086, and actionlint runs in CI."""
    step = _validate_step()
    assert "[ $exit_code " not in step
    assert not re.search(r"\bexit \$exit_code\s*$", step, re.MULTILINE)


def test_release_gate_keeps_no_color() -> None:
    """The report is uploaded as a release asset; ANSI escapes in it made the
    validator flag its own output (v2.107.1). Removing --no-color would
    re-create that."""
    assert "--no-color" in _validate_step()
