"""The dead-link phase needed an aggregate bound, not just a per-URL one (#180).

Every request was already bounded (8s, bounded retries) — but `validate_md_urls`
runs ONCE PER MARKDOWN FILE and its per-host semaphores are scoped to a single
call, so nothing paced the whole sweep and the phase grew with (files x URLs).
A field report measured it running to the CI job's own 25-30 min cap while
producing no output at all. That is the same "bounded per item, unbounded in
aggregate" shape issue #162 fixed for REPO LINT; it was simply still open here.

Two properties matter equally and are tested as a pair throughout:

* the budget FIRES, so the phase cannot approach the job timeout; and
* an unchecked URL is reported as SKIPPED, never as DEAD. Inventing a dead-link
  warning for a URL nobody contacted would be a worse failure than the silence
  the budget exists to prevent, and it would also make the budget unsafe to
  raise or lower.

The network boundary is faked so the timing is deterministic — the code under
test is the budget logic, not urllib.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.cpv_validation_common import (  # noqa: E402
    ValidationReport,
    url_check_phase_timeout,
    validate_md_urls,
)

_LIVE = "https://cpv-budget-test.invalid/alive"
_DEAD = "https://cpv-budget-test.invalid/dead"


def _fake_urlopen(req, *_a, **_kw):  # noqa: ANN001, ANN002, ANN003
    """Alive for one URL, 404 for the other. No real network, no waiting."""
    import urllib.error

    url = req.full_url if hasattr(req, "full_url") else str(req)
    if url.endswith("/dead"):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    class _R:
        status = 200

        def __enter__(self) -> "_R":
            return self

        def __exit__(self, *a: object) -> None:
            return None

    return _R()


def _warnings(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "WARNING"]


def _run(tmp_path: Path, body: str, **kw: object) -> tuple[ValidationReport, list[str]]:
    md = tmp_path / "doc.md"
    md.write_text(body, encoding="utf-8")
    report = ValidationReport()
    skipped: list[str] = []
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        validate_md_urls(md, tmp_path, report, timeout=1.0, skipped=skipped, **kw)  # type: ignore[arg-type]
    return report, skipped


# --------------------------------------------------------------------------
# The budget resolver — mirrors the lint engine's _phase_timeout contract
# --------------------------------------------------------------------------


def test_default_budget_is_well_under_a_ci_job_cap(monkeypatch) -> None:
    """It must fire BEFORE the job is killed, or it buys nothing."""
    monkeypatch.delenv("PLUGIN_URL_CHECK_PHASE_TIMEOUT", raising=False)
    budget = url_check_phase_timeout()
    assert 0 < budget <= 900, f"budget {budget}s is not comfortably under a 25-30 min job cap"


def test_budget_env_override_is_honoured(monkeypatch) -> None:
    monkeypatch.setenv("PLUGIN_URL_CHECK_PHASE_TIMEOUT", "42")
    assert url_check_phase_timeout() == 42.0


def test_budget_ignores_non_positive_and_junk_overrides(monkeypatch) -> None:
    """LOAD-BEARING: a `0` that won would skip every URL on every run — the
    guard must never be disableable, or shortenable, by a typo."""
    default = _default_budget(monkeypatch)
    for bad in ("0", "-5", "", "   ", "soon"):
        monkeypatch.setenv("PLUGIN_URL_CHECK_PHASE_TIMEOUT", bad)
        assert url_check_phase_timeout() == default, f"{bad!r} changed the budget"


def _default_budget(monkeypatch) -> float:
    monkeypatch.delenv("PLUGIN_URL_CHECK_PHASE_TIMEOUT", raising=False)
    return url_check_phase_timeout()


# --------------------------------------------------------------------------
# Budget exhausted → skipped, and specifically NOT reported dead
# --------------------------------------------------------------------------


def test_expired_budget_skips_instead_of_checking(tmp_path: Path) -> None:
    report, skipped = _run(tmp_path, f"See [dead]({_DEAD}).\n", deadline=time.monotonic() - 1)
    assert skipped == [_DEAD]
    assert _warnings(report) == [], f"a skipped URL produced findings: {_warnings(report)}"


def test_expired_budget_never_reports_a_url_as_dead(tmp_path: Path) -> None:
    """LOAD-BEARING: the URL genuinely 404s, but we never contacted it — calling
    it dead would be a fabricated finding."""
    report, _ = _run(tmp_path, f"See [dead]({_DEAD}).\n", deadline=time.monotonic() - 1)
    assert not any("Dead URL" in m for m in _warnings(report))


def test_expired_budget_does_not_poison_the_shared_cache(tmp_path: Path) -> None:
    """A skipped URL must not be cached as alive, or a later file would trust a
    check that never ran."""
    md = tmp_path / "doc.md"
    md.write_text(f"See [dead]({_DEAD}).\n", encoding="utf-8")
    cache: dict[str, bool] = {}
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        validate_md_urls(md, tmp_path, ValidationReport(), url_cache=cache, deadline=time.monotonic() - 1)
    assert cache == {}, f"a URL that was never contacted got cached: {cache}"


# --------------------------------------------------------------------------
# The other side: a live budget must not change behaviour at all
# --------------------------------------------------------------------------


def test_future_deadline_still_reports_a_real_dead_link(tmp_path: Path) -> None:
    """LOAD-BEARING: if the budget suppressed findings in the normal case, it
    would be a detection regression dressed up as a reliability fix."""
    report, skipped = _run(tmp_path, f"See [dead]({_DEAD}).\n", deadline=time.monotonic() + 300)
    assert skipped == []
    assert any("Dead URL" in m for m in _warnings(report)), _warnings(report)


def test_future_deadline_leaves_a_live_link_alone(tmp_path: Path) -> None:
    report, skipped = _run(tmp_path, f"See [ok]({_LIVE}).\n", deadline=time.monotonic() + 300)
    assert skipped == []
    assert _warnings(report) == []


def test_no_deadline_preserves_legacy_behaviour(tmp_path: Path) -> None:
    """Every existing caller passes no deadline and must be unaffected."""
    report, skipped = _run(tmp_path, f"See [dead]({_DEAD}).\n")
    assert skipped == []
    assert any("Dead URL" in m for m in _warnings(report))


# --------------------------------------------------------------------------
# Wiring — the phase is the per-file loop, so the bound must live there
# --------------------------------------------------------------------------


def test_validate_plugin_bounds_the_whole_phase() -> None:
    """One deadline must span every file: a per-call bound would reset on each
    file and leave the aggregate exactly as unbounded as before."""
    src = (Path(__file__).resolve().parents[1] / "scripts" / "validate_plugin.py").read_text(encoding="utf-8")
    assert "url_check_phase_timeout()" in src, "validate_plugin never resolves the phase budget"
    loop = src.split("for md_file in sorted(md_files):", 1)[1].split("\ndef ", 1)[0]
    assert "deadline=url_deadline" in loop, "the per-file loop does not pass the shared deadline"
    assert "url_deadline = time.monotonic()" in src, "the deadline is computed inside the loop, not once"


def _validate_block() -> str:
    from scripts.generate_plugin_repo import PluginParams, gen_cpv_validate_run_block

    return gen_cpv_validate_run_block(
        PluginParams(
            name="d",
            description="x",
            author="A",
            author_email="a@b.c",
            license="MIT",
            python_version="3.12",
            github_owner="o",
            marketplace="m",
            version="0.1.0",
        ),
        "report.txt",
    )


def test_validate_step_streams_its_output() -> None:
    """With `> file` + a trailing `cat`, a hung run and a healthy one are
    byte-identical in the log for the whole window, and a job killed at its cap
    never reaches the `cat` — so the log shows nothing at all about what was
    running. That is the reporter's core complaint."""
    block = _validate_block()
    assert 'tee "report.txt"' in block, "the validate step no longer streams its output"
    assert "> \"report.txt\" 2>&1" not in block, "the blind redirect is back"


def test_validate_step_reads_the_validators_exit_code_not_tees() -> None:
    """LOAD-BEARING: after a pipeline, `$?` is tee's status. Reading it would
    report success for every failed validation — a fail-OPEN gate."""
    block = _validate_block()
    assert "PIPESTATUS[0]" in block, "the exit code is not taken from the validator"


def test_validate_step_quotes_exit_code_everywhere() -> None:
    """shellcheck cannot infer numeric-ness through PIPESTATUS, so an unquoted
    expansion trips SC2086 — and the generated Lint job runs actionlint, which
    would turn every scaffolded plugin's CI red."""
    block = _validate_block()
    for bad in ("[ $exit_code ", "exit $exit_code\n"):
        assert bad not in block, f"unquoted expansion {bad!r} would trip SC2086 in the generated CI"


def test_budget_overrun_is_reported_once_and_never_blocks() -> None:
    """A budget overrun is one fact about the run, and it must stay advisory —
    an unchecked link must not fail a plugin that may have none broken."""
    src = (Path(__file__).resolve().parents[1] / "scripts" / "validate_plugin.py").read_text(encoding="utf-8")
    tail = src.split("if url_skipped:", 1)[1].split("\ndef ", 1)[0]
    assert "report.warning(" in tail, "the overrun is not reported at WARNING severity"
    assert "report.major(" not in tail and "report.minor(" not in tail, "a budget overrun must not block"


class TestDeadlineRecheckedPerAttempt:
    """The entry check gates task START; everything after it could overrun.

    #180's budget was verified as one deadline spanning every file, which is
    the load-bearing property. But `_check_one` consulted it exactly once, at
    entry — so a task admitted just under the wire could still spend ~55s on a
    fully-timing-out strict host (5 attempts x 8s + backoff), with up to 16
    started tasks draining 2-at-a-time behind the per-host semaphore. The 300s
    phase budget could therefore overshoot by minutes.

    A retry is a NEW request, so declining to start one is exactly the decision
    the entry check already makes. The FIRST attempt stays exempt: a task
    admitted under the budget must get one real request, or a URL could be
    reported SKIPPED having never been tried at all.
    """

    @staticmethod
    def _attempt_loop() -> str:
        src = (
            Path(__file__).resolve().parent.parent / "scripts" / "cpv_validation_common.py"
        ).read_text(encoding="utf-8")
        i = src.index("for attempt in range(host_attempts")
        return src[i : i + 2000]

    def test_deadline_is_rechecked_inside_the_attempt_loop(self) -> None:
        assert "attempt > 0 and deadline is not None" in self._attempt_loop(), (
            "no per-attempt budget re-check — the retry ladder can outrun the phase budget"
        )

    def test_deadline_is_rechecked_after_acquiring_the_semaphore(self) -> None:
        loop = self._attempt_loop()
        after_sem = loop[loop.index("with sem:") :]
        head = after_sem[: after_sem.index("_one_request")]
        assert "deadline is not None and time.monotonic() >= deadline" in head, (
            "no re-check after the semaphore acquire — a queued task can wake up past the deadline"
        )

    def test_first_attempt_is_not_gated_by_the_recheck(self) -> None:
        """Otherwise a URL admitted under budget could be SKIPPED untried."""
        loop = self._attempt_loop()
        recheck = loop[loop.index("if attempt > 0 and deadline") :]
        assert recheck.startswith("if attempt > 0 and deadline is not None")

    def test_recheck_reports_skipped_never_dead(self) -> None:
        """A URL we declined to contact must never be called dead."""
        loop = self._attempt_loop()
        recheck = loop[loop.index("if attempt > 0 and deadline") :]
        ret = recheck[: recheck.index("with sem:")]
        assert "True, _URL_CHECK_SKIPPED" in ret, "must report alive+skipped, not dead"
