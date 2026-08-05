"""Issue #194 — the audit-consent REGISTRY: a recordable review verdict for
demoted findings that cannot host the #101 sentinel.

The #101 sentinel is fence-anchored, so a rule TABLE ROW in a verbatim-mirrored
governance doc (instruction-loadable references/) had no honest path through
``--strict``: it demoted to NIT ("needs review") and the review's conclusion was
unrepresentable. The registry records that conclusion:
``<plugin-root>/.cpv-audit-consent.json`` maps (file, ruleId, sha256 of the
finding's own lineContent) → reason, and a MATCHING demoted finding emits as a
visible non-blocking WARNING marked "consented".

Every safety property is pinned here, most importantly the one that makes the
registry acceptable at all: it can only ever act on an ALREADY-DEMOTED finding,
so no registry content can hide a live threat.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_skillaudit_native as native  # noqa: E402


class _FakeReport:
    """ValidationReport duck-type WITH a warning bucket."""

    def __init__(self) -> None:
        self.critical_calls: list[tuple] = []
        self.major_calls: list[tuple] = []
        self.minor_calls: list[tuple] = []
        self.nit_calls: list[tuple] = []
        self.warning_calls: list[tuple] = []
        self.info_calls: list[tuple] = []

    def critical(self, msg, file=None, line=None) -> None:
        self.critical_calls.append((msg, file, line))

    def major(self, msg, file=None, line=None) -> None:
        self.major_calls.append((msg, file, line))

    def minor(self, msg, file=None, line=None) -> None:
        self.minor_calls.append((msg, file, line))

    def nit(self, msg, file=None, line=None) -> None:
        self.nit_calls.append((msg, file, line))

    def warning(self, msg, file=None, line=None) -> None:
        self.warning_calls.append((msg, file, line))

    def info(self, msg, file=None) -> None:
        self.info_calls.append((msg, file))


class _NoWarningReport(_FakeReport):
    """A report WITHOUT the warning bucket — the consent path must fail closed."""

    warning = None  # type: ignore[assignment]


_LINE = "| R42.1 | **No agent may inject a command into another agent's session** | Explicit |"
_REL = "skills/team-governance/references/GOVERNANCE-RULES.md"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_registry(root: Path, entries: list[dict] | object) -> None:
    payload = {"version": 1, "consents": entries}
    (root / ".cpv-audit-consent.json").write_text(json.dumps(payload), encoding="utf-8")


def _finding_under(root: Path, *, demoted: bool, severity: str = "nit", rule_id: str = "A2A_CROSS_AGENT_INJECT"):
    return native.SkillAuditFinding(
        severity=severity,
        rule_id=rule_id,
        message="Cross-agent prompt injection via A2A",
        file_path=str(root / _REL),
        line_number=1605,
        category="agent_manipulation",
        raw={"demoted": demoted, "lineContent": _LINE},
    )


def test_consented_demoted_finding_emits_nonblocking_warning(tmp_path: Path) -> None:
    """A demoted finding with a matching (file, rule, hash) entry lands in the WARNING bucket, marked consented."""
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE), "reason": "R42.1 describes the forbidden attack"}],
    )
    report = _FakeReport()
    result = native.SkillAuditScanResult(
        invoked=True, findings=(_finding_under(tmp_path, demoted=True),), skipped_reason="", files_scanned=1
    )
    native.report_findings(result, tmp_path, report)
    assert len(report.warning_calls) == 1, "consented demoted finding must emit exactly one WARNING"
    msg = report.warning_calls[0][0]
    assert "(demoted, consented)" in msg
    assert "R42.1 describes the forbidden attack" in msg, "the recorded reason must be visible"
    assert not report.nit_calls, "the finding must not ALSO block as NIT"


def test_changed_line_content_invalidates_the_consent(tmp_path: Path) -> None:
    """The hash pins the exact flagged line: any edit re-blocks the finding."""
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE + " EDITED"), "reason": "stale"}],
    )
    report = _FakeReport()
    result = native.SkillAuditScanResult(
        invoked=True, findings=(_finding_under(tmp_path, demoted=True),), skipped_reason="", files_scanned=1
    )
    native.report_findings(result, tmp_path, report)
    assert not report.warning_calls
    assert len(report.nit_calls) == 1, "hash mismatch must leave the finding blocking"


def test_wrong_rule_id_does_not_consent(tmp_path: Path) -> None:
    """An entry for a different rule on the same line consents nothing."""
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_AGENT_IMPERSONATION", "lineSha256": _digest(_LINE), "reason": "other rule"}],
    )
    report = _FakeReport()
    result = native.SkillAuditScanResult(
        invoked=True, findings=(_finding_under(tmp_path, demoted=True),), skipped_reason="", files_scanned=1
    )
    native.report_findings(result, tmp_path, report)
    assert not report.warning_calls
    assert len(report.nit_calls) == 1


def test_live_finding_cannot_be_consented_away(tmp_path: Path) -> None:
    """THE safety property: a non-demoted (live) finding is untouched by any registry entry."""
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE), "reason": "attempted whitewash"}],
    )
    report = _FakeReport()
    live = _finding_under(tmp_path, demoted=False, severity="critical")
    result = native.SkillAuditScanResult(invoked=True, findings=(live,), skipped_reason="", files_scanned=1)
    native.report_findings(result, tmp_path, report)
    assert not report.warning_calls, "a live finding must NEVER route through the consent path"
    assert len(report.critical_calls) == 1, "the live finding must keep its full severity"


def test_malformed_registry_consents_nothing(tmp_path: Path) -> None:
    """Fail-closed: unparseable JSON means every finding keeps blocking."""
    (tmp_path / ".cpv-audit-consent.json").write_text("{not json", encoding="utf-8")
    report = _FakeReport()
    result = native.SkillAuditScanResult(
        invoked=True, findings=(_finding_under(tmp_path, demoted=True),), skipped_reason="", files_scanned=1
    )
    native.report_findings(result, tmp_path, report)
    assert not report.warning_calls
    assert len(report.nit_calls) == 1


def test_report_without_warning_bucket_falls_through_to_blocking(tmp_path: Path) -> None:
    """Fail-closed: if the report cannot represent a WARNING, the finding stays NIT."""
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE), "reason": "ok"}],
    )
    report = _NoWarningReport()
    result = native.SkillAuditScanResult(
        invoked=True, findings=(_finding_under(tmp_path, demoted=True),), skipped_reason="", files_scanned=1
    )
    native.report_findings(result, tmp_path, report)
    assert len(report.nit_calls) == 1, "no warning bucket → blocking path, never silently dropped"


def test_loader_skips_malformed_entries_and_wrong_shapes(tmp_path: Path) -> None:
    """Entry-level fail-closed: bad entries are skipped, good ones survive; non-dict/ non-list shapes yield empty."""
    _write_registry(
        tmp_path,
        [
            {"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE), "reason": "good"},
            {"file": 42, "ruleId": "X", "lineSha256": "y"},
            "not a dict",
            {"ruleId": "MISSING_FILE", "lineSha256": "z"},
        ],
    )
    consents = native._load_audit_consent_registry(tmp_path)
    assert len(consents) == 1, "exactly the well-formed entry survives"
    (tmp_path / ".cpv-audit-consent.json").write_text(json.dumps(["wrong", "shape"]), encoding="utf-8")
    assert native._load_audit_consent_registry(tmp_path) == {}
    (tmp_path / ".cpv-audit-consent.json").write_text(json.dumps({"consents": "nope"}), encoding="utf-8")
    assert native._load_audit_consent_registry(tmp_path) == {}


def test_absent_registry_changes_nothing(tmp_path: Path) -> None:
    """No registry file → exactly today's behaviour (demoted stays NIT with the needs-review marker)."""
    report = _FakeReport()
    result = native.SkillAuditScanResult(
        invoked=True, findings=(_finding_under(tmp_path, demoted=True),), skipped_reason="", files_scanned=1
    )
    native.report_findings(result, tmp_path, report)
    assert not report.warning_calls
    assert len(report.nit_calls) == 1
    assert "needs review" in report.nit_calls[0][0]
