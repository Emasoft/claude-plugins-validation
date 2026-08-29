"""Tests for the opt-in AI triage of SkillAudit residual findings
(scripts/cpv_ai_triage.py).

No mocking of the parsing logic: `parse_stdout` is tested against the real
`key=value` stdout shapes `llm-ext scan security` produces (verified this
session), including the refusal shape whose exit code is 0 even though the
job never ran — the single most important regression this suite guards.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_ai_triage as ait  # noqa: E402

# ---------------------------------------------------------------------------
# Real stdout fixtures, verified this session against the live `llm-ext`
# `scan security` contract.
# ---------------------------------------------------------------------------

REFUSAL_STDOUT = """\
budget_gate=refused
reason=estimated cost exceeds budget
est_cost_usd=$1.2345
items_skipped_over_budget=all
json=
report=
"""

SUCCESS_STDOUT = """\
job_id=job-abc123
items=2
threat=1
not_threat=1
uncertain=0
deduped=0
skipped_too_big=0
spent=$0.0421
json=/tmp/cpv-ai-triage-xyz/result.json
report=/tmp/cpv-ai-triage-xyz/report.md
"""


class _FakeFinding:
    """Minimal stand-in for a SkillAuditFinding — only the fields this module reads."""

    def __init__(self, rule_id: str, file_path: str, line_number: int | None) -> None:
        self.rule_id = rule_id
        self.file_path = file_path
        self.line_number = line_number


class _FakeReport:
    """Minimal ValidationReport stand-in that records every info() call."""

    def __init__(self) -> None:
        self.info_calls: list[tuple[str, Any]] = []

    def info(self, message: str, file: str | None = None) -> None:
        self.info_calls.append((message, file))


def test_parse_stdout_on_real_refusal_shape() -> None:
    """parse_stdout reads every field of a real budget-refused stdout payload."""
    fields = ait.parse_stdout(REFUSAL_STDOUT)
    assert fields["budget_gate"] == "refused"
    assert fields["reason"] == "estimated cost exceeds budget"
    assert fields["est_cost_usd"] == "$1.2345"
    assert fields["items_skipped_over_budget"] == "all"
    assert fields["json"] == ""
    assert fields["report"] == ""


def test_parse_stdout_on_real_success_shape() -> None:
    """parse_stdout reads every field of a real completed-job stdout payload."""
    fields = ait.parse_stdout(SUCCESS_STDOUT)
    assert fields["job_id"] == "job-abc123"
    assert fields["items"] == "2"
    assert fields["threat"] == "1"
    assert fields["not_threat"] == "1"
    assert fields["uncertain"] == "0"
    assert fields["spent"] == "$0.0421"
    assert fields["json"] == "/tmp/cpv-ai-triage-xyz/result.json"
    assert fields["report"] == "/tmp/cpv-ai-triage-xyz/report.md"


def test_refusal_payload_yields_invoked_false_with_reason_carried_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exit-0-does-not-mean-success regression guard: a refused job must
    report invoked=False with the printed reason/cost carried into skipped_reason,
    never invoked=True with an empty verdict set."""
    monkeypatch.setenv(ait.AI_TRIAGE_BUDGET_ENV, "5.0")
    monkeypatch.setattr(ait.shutil, "which", lambda name: "/usr/bin/llm-ext")

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode=0, stdout=REFUSAL_STDOUT, stderr="")

    monkeypatch.setattr(ait.subprocess, "run", fake_run)

    findings = (_FakeFinding("TOOL_SHADOW", "/plugin/skills/x/SKILL.md", 10),)
    result = ait.run_ai_triage(Path("/plugin"), findings)

    assert result.invoked is False
    assert result.verdicts == ()
    assert "reason=estimated cost exceeds budget" in result.skipped_reason
    assert "est_cost_usd=$1.2345" in result.skipped_reason


def test_residual_findings_keeps_only_the_five_rule_ids_and_drops_others() -> None:
    """residual_findings filters to exactly the 5 residual rule ids, dropping any other."""
    findings = (
        _FakeFinding("INSECURE_CRYPTO", "a.py", 1),
        _FakeFinding("TOOL_SHADOW", "b.py", 2),
        _FakeFinding("SSRF_ADVANCED", "c.py", 3),
        _FakeFinding("ENV_INJECTION", "d.py", 4),
        _FakeFinding("RESOURCE_ABUSE", "e.py", 5),
        _FakeFinding("PROMPT_INJECT", "f.py", 6),
        _FakeFinding("CMD_INJECTION", "g.py", 7),
    )
    kept = ait.residual_findings(findings)
    assert {f.rule_id for f in kept} == ait.RESIDUAL_RULE_IDS
    assert len(kept) == 5


def test_build_targets_produces_documented_item_shape_with_stable_id() -> None:
    """build_targets emits the verified {id, category, file_path, line, context_lines} shape."""
    plugin_path = Path("/plugin")
    findings = (_FakeFinding("TOOL_SHADOW", "/plugin/skills/x/SKILL.md", 42),)
    targets = ait.build_targets(findings, plugin_path)

    assert len(targets) == 1
    item = targets[0]
    assert item["id"] == "TOOL_SHADOW:skills/x/SKILL.md:42"
    assert item["category"] == "TOOL_SHADOW"
    assert item["file_path"] == "skills/x/SKILL.md"
    assert item["line"] == 42
    assert item["context_lines"] == 10

    # Same finding, same call, same id — the join key must be deterministic.
    targets_again = ait.build_targets(findings, plugin_path)
    assert targets_again[0]["id"] == item["id"]


def test_opt_out_with_no_budget_env_never_spawns_a_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """With CPV_AI_TRIAGE_BUDGET_USD unset, invoked=False and subprocess.run is never called."""
    monkeypatch.delenv(ait.AI_TRIAGE_BUDGET_ENV, raising=False)

    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess.run must not be called when the budget env is unset")

    monkeypatch.setattr(ait.subprocess, "run", fail_if_called)

    findings = (_FakeFinding("TOOL_SHADOW", "/plugin/skills/x/SKILL.md", 1),)
    result = ait.run_ai_triage(Path("/plugin"), findings)

    assert result.invoked is False
    assert "not set" in result.skipped_reason
    assert result.verdicts == ()


def test_budget_usd_treats_zero_negative_and_garbage_as_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """budget_usd() returns None for unset/blank/zero/negative/unparseable values."""
    for value in ("", "0", "-1", "not-a-number"):
        monkeypatch.setenv(ait.AI_TRIAGE_BUDGET_ENV, value)
        assert ait.budget_usd() is None
    monkeypatch.setenv(ait.AI_TRIAGE_BUDGET_ENV, "2.5")
    assert ait.budget_usd() == pytest.approx(2.5)


def test_success_payload_with_threat_and_uncertain_items_parses_into_two_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: a real success JSON with one threat + one uncertain item
    parses into two TriageVerdicts carrying the right verdicts — proves the
    parsing path can actually produce a positive result, not just clear FPs."""
    monkeypatch.setenv(ait.AI_TRIAGE_BUDGET_ENV, "5.0")
    monkeypatch.setattr(ait.shutil, "which", lambda name: "/usr/bin/llm-ext")

    result_json = tmp_path / "result.json"
    result_json.write_text(
        json.dumps(
            {
                "job_id": "job-1",
                "summary": {"budget_usd_spent": 0.05},
                "items": [
                    {
                        "id": "TOOL_SHADOW:skills/x/SKILL.md:10",
                        "category": "TOOL_SHADOW",
                        "verdict": "threat",
                        "confidence": 0.91,
                        "reason": "shadows the Bash tool",
                        "injection_observed": True,
                        "file_path": "skills/x/SKILL.md",
                        "line": 10,
                    },
                    {
                        "id": "ENV_INJECTION:hooks/y.sh:3",
                        "category": "ENV_INJECTION",
                        "verdict": "uncertain",
                        "confidence": 0.4,
                        "reason": "ambiguous env write",
                        "injection_observed": False,
                        "file_path": "hooks/y.sh",
                        "line": 3,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    stdout = (
        "job_id=job-1\n"
        "items=2\n"
        "threat=1\n"
        "not_threat=0\n"
        "uncertain=1\n"
        "deduped=0\n"
        "skipped_too_big=0\n"
        "spent=$0.05\n"
        f"json={result_json}\n"
        "report=/tmp/report.md\n"
    )

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(ait.subprocess, "run", fake_run)

    findings = (
        _FakeFinding("TOOL_SHADOW", "/plugin/skills/x/SKILL.md", 10),
        _FakeFinding("ENV_INJECTION", "/plugin/hooks/y.sh", 3),
    )
    result = ait.run_ai_triage(Path("/plugin"), findings)

    assert result.invoked is True
    assert len(result.verdicts) == 2
    verdicts_by_rule = {v.rule_id: v for v in result.verdicts}
    assert verdicts_by_rule["TOOL_SHADOW"].verdict == "threat"
    assert verdicts_by_rule["TOOL_SHADOW"].injection_observed is True
    assert verdicts_by_rule["ENV_INJECTION"].verdict == "uncertain"
    assert verdicts_by_rule["ENV_INJECTION"].injection_observed is False


def test_uncertain_verdict_is_never_folded_into_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """report_verdicts emits the literal 'uncertain' verdict, never silently drops it."""
    monkeypatch.setenv(ait.AI_TRIAGE_BUDGET_ENV, "5.0")
    monkeypatch.setattr(ait.shutil, "which", lambda name: "/usr/bin/llm-ext")

    result_json = tmp_path / "result.json"
    result_json.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": "SSRF_ADVANCED:skills/a/SKILL.md:5",
                        "category": "SSRF_ADVANCED",
                        "verdict": "uncertain",
                        "confidence": 0.5,
                        "reason": "cannot determine target host at analysis time",
                        "injection_observed": False,
                        "file_path": "skills/a/SKILL.md",
                        "line": 5,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    stdout = f"threat=0\nnot_threat=0\nuncertain=1\njson={result_json}\nreport=/tmp/r.md\n"

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(ait.subprocess, "run", fake_run)

    findings = (_FakeFinding("SSRF_ADVANCED", "/plugin/skills/a/SKILL.md", 5),)
    result = ait.run_ai_triage(Path("/plugin"), findings)
    assert result.verdicts[0].verdict == "uncertain"

    report = _FakeReport()
    count = ait.report_verdicts(result, report)
    assert count == 1
    joined = "\n".join(msg for msg, _file in report.info_calls)
    assert "uncertain" in joined


def test_all_items_unparseable_is_reported_unusable_never_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A judge returning items we can parse NONE of must NOT read as a clean triage.

    Without the guard this returns invoked=True with zero verdicts, which
    renders identically to 'triaged, nothing to say' — the cannot-check-reads-
    as-clean failure this whole module is built to avoid.
    """
    monkeypatch.setenv(ait.AI_TRIAGE_BUDGET_ENV, "5.0")
    monkeypatch.setattr(ait.shutil, "which", lambda name: "/usr/bin/llm-ext")

    result_json = tmp_path / "result.json"
    # Items present, but every one is a non-dict the parser must skip.
    result_json.write_text(json.dumps({"items": ["garbage", 42, None]}), encoding="utf-8")
    stdout = f"job_id=j\nitems=3\njson={result_json}\nreport=/tmp/r.md\n"

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(ait.subprocess, "run", fake_run)

    findings = (_FakeFinding("SSRF_ADVANCED", "/plugin/skills/a/SKILL.md", 5),)
    result = ait.run_ai_triage(Path("/plugin"), findings)

    assert result.invoked is False
    assert "none could be parsed" in result.skipped_reason
    assert "not clean" in result.skipped_reason


def test_category_rubrics_are_sent_for_every_triaged_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The TRDD's premise is per-category adjudication, so the rubrics must
    actually reach llm-ext; without them the judge falls back to an unstated
    default and the semantic distinction is never drawn."""
    monkeypatch.setenv(ait.AI_TRIAGE_BUDGET_ENV, "5.0")
    monkeypatch.setattr(ait.shutil, "which", lambda name: "/usr/bin/llm-ext")

    result_json = tmp_path / "result.json"
    result_json.write_text(json.dumps({"items": []}), encoding="utf-8")
    stdout = f"job_id=j\nitems=0\njson={result_json}\nreport=/tmp/r.md\n"
    seen: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(ait.subprocess, "run", fake_run)

    findings = (
        _FakeFinding("INSECURE_CRYPTO", "/plugin/a.py", 1),
        _FakeFinding("SSRF_ADVANCED", "/plugin/b.py", 2),
    )
    ait.run_ai_triage(Path("/plugin"), findings)

    argv = seen["argv"]
    assert "--category_rubrics" in argv
    rubrics = json.loads(argv[argv.index("--category_rubrics") + 1])
    assert set(rubrics) == {"INSECURE_CRYPTO", "SSRF_ADVANCED"}
    # Each rubric must name the BENIGN shape too, not just the threat —
    # a threat-only rubric biases the judge toward 'threat'.
    assert "NOT a threat" in rubrics["INSECURE_CRYPTO"]
    assert "NOT a threat" in rubrics["SSRF_ADVANCED"]


def test_all_items_malformed_is_not_reported_as_a_completed_triage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A json payload whose every item is unparseable must yield invoked=False.

    The regression this guards: returning invoked=True with an empty verdict
    tuple would render as a triage that ran and found nothing to say, which is
    indistinguishable from a clean result. A moved output schema must surface
    as "could not check", never as "checked".
    """
    monkeypatch.setenv(ait.AI_TRIAGE_BUDGET_ENV, "5.0")
    monkeypatch.setattr(ait.shutil, "which", lambda name: "/usr/bin/llm-ext")

    result_json = tmp_path / "result.json"
    result_json.write_text(
        json.dumps(
            {
                "job_id": "job-2",
                "summary": {"budget_usd_spent": 0.0},
                # Every item lacks the required "id" -> each raises KeyError.
                "items": [{"category": "TOOL_SHADOW"}, {"category": "ENV_INJECTION"}],
            }
        ),
        encoding="utf-8",
    )
    stdout = f"job_id=job-2\nitems=2\njson={result_json}\nreport=/tmp/r.md\n"

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(ait.subprocess, "run", fake_run)

    findings = (_FakeFinding("TOOL_SHADOW", "/plugin/skills/x/SKILL.md", 10),)
    result = ait.run_ai_triage(Path("/plugin"), findings)

    assert result.invoked is False
    assert "none could be parsed" in result.skipped_reason
    assert result.verdicts == ()


def test_one_malformed_item_among_good_ones_still_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control for the guard above: a SINGLE bad item must not sink an
    otherwise-good triage. Without this, the guard could be 'satisfied' by
    rejecting every payload containing any malformed item."""
    monkeypatch.setenv(ait.AI_TRIAGE_BUDGET_ENV, "5.0")
    monkeypatch.setattr(ait.shutil, "which", lambda name: "/usr/bin/llm-ext")

    result_json = tmp_path / "result.json"
    result_json.write_text(
        json.dumps(
            {
                "job_id": "job-3",
                "summary": {"budget_usd_spent": 0.01},
                "items": [
                    {"category": "TOOL_SHADOW"},  # malformed: no id
                    {
                        "id": "ENV_INJECTION:hooks/y.sh:3",
                        "category": "ENV_INJECTION",
                        "verdict": "not_threat",
                        "confidence": 0.8,
                        "reason": "build var read",
                        "injection_observed": False,
                        "file_path": "hooks/y.sh",
                        "line": 3,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    stdout = f"job_id=job-3\nitems=2\njson={result_json}\nreport=/tmp/r.md\n"

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(ait.subprocess, "run", fake_run)

    findings = (_FakeFinding("ENV_INJECTION", "/plugin/hooks/y.sh", 3),)
    result = ait.run_ai_triage(Path("/plugin"), findings)

    assert result.invoked is True
    assert len(result.verdicts) == 1
    assert result.verdicts[0].verdict == "not_threat"


def test_invoked_triage_cannot_change_the_verdict() -> None:
    """The INVOKED path must not move the exit code — in EITHER direction.

    Every other test here exercises the opt-OUT path, where `report_verdicts`
    emits nothing, so none of them can see this. Two symmetric risks live on
    the invoked path:

    * DEMOTION — a `not_threat` verdict clearing a real finding. Prevented by
      construction: `report_verdicts` only ever calls `report.info(...)`.
    * PROMOTION — the triage's own output BLOCKING a clean plugin. If INFO were
      a blocking tier, merely setting `CPV_AI_TRIAGE_BUDGET_USD` would flip a
      clean plugin to INVALID: a security-gate change nobody asked for.

    This asserts through the REAL `ValidationReport` rather than by reading
    `exit_code`, so it also pins the tier `report.info()` actually writes and
    the blocking sets `exit_code`/`exit_code_strict` actually consult. A
    refactor of any of those three would break this test — which is the point,
    because none of them is obviously coupled to this module.
    """
    from cpv_validation_common import ValidationReport  # noqa: PLC0415

    report = ValidationReport()
    before = (report.exit_code, report.exit_code_strict())
    assert before == (0, 0), "precondition: a fresh report must be clean"

    emitted = ait.report_verdicts(
        ait.TriageResult(
            invoked=True,
            verdicts=(
                ait.TriageVerdict("X", "TOOL_SHADOW", "a.py", 1, "not_threat", 0.9, "why", False),
                ait.TriageVerdict("Y", "SSRF_ADVANCED", "b.py", 2, "threat", 0.9, "why", True),
            ),
        ),
        report,
    )

    assert emitted == 2, "both verdicts should be reported, including the threat one"
    assert {r.level for r in report.results} == {"INFO"}, "triage must emit INFO and nothing else"
    assert (report.exit_code, report.exit_code_strict()) == before, (
        "the AI triage changed the verdict — it must be verdict-neutral in both directions"
    )
