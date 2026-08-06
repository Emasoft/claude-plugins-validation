"""Issue #194 — the audit-consent REGISTRY: a recordable review verdict for
demoted findings that cannot host the #101 sentinel.

The #101 sentinel is fence-anchored, so a rule TABLE ROW in a verbatim-mirrored
governance doc (instruction-loadable references/) had no honest path through
``--strict``: it demoted to NIT ("needs review") and the review's conclusion was
unrepresentable. The registry records that conclusion:
``<plugin-root>/.cpv-audit-consent.json`` maps (file, ruleId, sha256 of the
FULL stripped flagged line as read from disk) → reason, and a MATCHING demoted
finding emits as a visible non-blocking WARNING marked "consented".

The safety properties pinned here, in order of importance:
  1. only an ALREADY-DEMOTED finding can consent (a live threat is untouchable);
  2. the _INTENT_HARD_SIGNAL_RULES family can never consent, demoted or not
     (the same boundary the #101 sentinel enforces — no weaker parallel gate);
  3. the hash covers the FULL line read from disk, so an edit beyond the
     200-char ``lineContent`` truncation cannot inherit a consent;
  4. every failure mode (absent/malformed registry, missing file, missing
     warning bucket) falls through to the BLOCKING path, never a silent pass;
  5. the reason string cannot spoof report lines (whitespace-collapsed, capped).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

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
_LINE_NO = 1605


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_registry(root: Path, entries: list[dict] | object) -> None:
    payload = {"version": 1, "consents": entries}
    (root / ".cpv-audit-consent.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_flagged_file(root: Path, line: str = _LINE, line_no: int = _LINE_NO, rel: str = _REL) -> None:
    """Materialise the flagged file on disk — the consent hash is computed from
    the REAL line at report time, never from the finding's truncated field."""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    body = ["| pad |"] * (line_no - 1) + [line]
    target.write_text("\n".join(body) + "\n", encoding="utf-8")


def _finding_under(
    root: Path,
    *,
    demoted: bool,
    severity: str = "nit",
    rule_id: str = "A2A_CROSS_AGENT_INJECT",
    line: str = _LINE,
    line_no: int = _LINE_NO,
    rel: str = _REL,
):
    return native.SkillAuditFinding(
        severity=severity,
        rule_id=rule_id,
        message="Cross-agent prompt injection via A2A",
        file_path=str(root / rel),
        line_number=line_no,
        category="agent_manipulation",
        raw={"demoted": demoted, "lineContent": line.strip()[:200]},
    )


def _run(root: Path, finding, report) -> None:
    result = native.SkillAuditScanResult(
        invoked=True, findings=(finding,), skipped_reason="", files_scanned=1
    )
    native.report_findings(result, root, report)


def test_consented_demoted_finding_emits_nonblocking_warning(tmp_path: Path) -> None:
    """The #194 use case (an A2A rule-table row): a demoted finding with a matching (file, rule, full-line hash) entry lands in the WARNING bucket, marked consented."""
    _write_flagged_file(tmp_path)
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE), "reason": "R42.1 describes the forbidden attack"}],
    )
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True), report)
    assert len(report.warning_calls) == 1, "consented demoted finding must emit exactly one WARNING"
    msg = report.warning_calls[0][0]
    assert "(demoted, consented)" in msg
    assert "R42.1 describes the forbidden attack" in msg, "the recorded reason must be visible"
    assert not report.nit_calls, "the finding must not ALSO block as NIT"


@pytest.mark.parametrize(
    "protected_rule",
    ["PROMPT_INJECT", "INDIRECT_PROMPT_INJECT", "INTENT_EXFILTRATION_INTENT", "HARDCODED_SECRET"],
)
def test_protected_intent_family_can_never_be_consented(tmp_path: Path, protected_rule: str) -> None:
    """The sentinel-parity boundary: a hard-signal rule stays BLOCKING even demoted with a byte-perfect consent entry — no weaker parallel gate."""
    _write_flagged_file(tmp_path)
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": protected_rule, "lineSha256": _digest(_LINE), "reason": "self-declared FP"}],
    )
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True, rule_id=protected_rule), report)
    assert not report.warning_calls, f"{protected_rule} must never route through the consent path"
    assert len(report.nit_calls) == 1, f"{protected_rule} must keep blocking"


def test_protected_set_is_the_hard_signal_set() -> None:
    """The consent denylist IS _INTENT_HARD_SIGNAL_RULES — kept identical by construction, and the A2A use-case rules are provably outside it."""
    assert native._CONSENT_PROTECTED_RULES == native._INTENT_HARD_SIGNAL_RULES
    assert "A2A_CROSS_AGENT_INJECT" not in native._CONSENT_PROTECTED_RULES
    assert "A2A_AGENT_IMPERSONATION" not in native._CONSENT_PROTECTED_RULES


def test_edit_beyond_char_200_cannot_inherit_consent(tmp_path: Path) -> None:
    """The truncation hole is closed: a payload appended AFTER char 200 (invisible to lineContent) invalidates the consent because the FULL disk line is hashed."""
    long_line = "| R99 | " + "x" * 230 + " |"
    tampered = long_line + " <!-- appended payload -->"
    assert long_line.strip()[:200] == tampered.strip()[:200], (
        "precondition: the tamper is invisible to the truncated lineContent recipe"
    )
    _write_flagged_file(tmp_path, line=tampered)
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(long_line), "reason": "reviewed original"}],
    )
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True, line=tampered), report)
    assert not report.warning_calls, "the tampered long line must not inherit the consent"
    assert len(report.nit_calls) == 1
    # Two-sided control: the UNtampered long line consents normally.
    _write_flagged_file(tmp_path, line=long_line)
    report2 = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True, line=long_line), report2)
    assert len(report2.warning_calls) == 1, "the reviewed original long line must still consent"


def test_changed_line_content_invalidates_the_consent(tmp_path: Path) -> None:
    """The hash pins the exact flagged line: any edit re-blocks the finding."""
    _write_flagged_file(tmp_path)
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE + " EDITED"), "reason": "stale"}],
    )
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True), report)
    assert not report.warning_calls
    assert len(report.nit_calls) == 1, "hash mismatch must leave the finding blocking"


def test_wrong_rule_id_does_not_consent(tmp_path: Path) -> None:
    """An entry for a different rule on the same line consents nothing."""
    _write_flagged_file(tmp_path)
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_AGENT_IMPERSONATION", "lineSha256": _digest(_LINE), "reason": "other rule"}],
    )
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True), report)
    assert not report.warning_calls
    assert len(report.nit_calls) == 1


def test_live_finding_cannot_be_consented_away(tmp_path: Path) -> None:
    """THE safety property: a non-demoted (live) finding is untouched by any registry entry."""
    _write_flagged_file(tmp_path)
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE), "reason": "attempted whitewash"}],
    )
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=False, severity="critical"), report)
    assert not report.warning_calls, "a live finding must NEVER route through the consent path"
    assert len(report.critical_calls) == 1, "the live finding must keep its full severity"


def test_flagged_file_missing_on_disk_declines_consent(tmp_path: Path) -> None:
    """Fail-closed: if the flagged file cannot be read, the hash cannot be verified and the finding keeps blocking."""
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE), "reason": "ok"}],
    )
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True), report)
    assert not report.warning_calls
    assert len(report.nit_calls) == 1


def test_malformed_registry_consents_nothing(tmp_path: Path) -> None:
    """Fail-closed: unparseable JSON means every finding keeps blocking."""
    _write_flagged_file(tmp_path)
    (tmp_path / ".cpv-audit-consent.json").write_text("{not json", encoding="utf-8")
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True), report)
    assert not report.warning_calls
    assert len(report.nit_calls) == 1


def test_report_without_warning_bucket_falls_through_to_blocking(tmp_path: Path) -> None:
    """Fail-closed: if the report cannot represent a WARNING, the finding stays NIT."""
    _write_flagged_file(tmp_path)
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE), "reason": "ok"}],
    )
    report = _NoWarningReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True), report)
    assert len(report.nit_calls) == 1, "no warning bucket → blocking path, never silently dropped"


def test_reason_cannot_spoof_report_lines(tmp_path: Path) -> None:
    """The reason is whitespace-collapsed and capped, so an embedded newline cannot fabricate extra report lines."""
    _write_flagged_file(tmp_path)
    sneaky = "ok\n[PASSED] fabricated clean line\n" + "A" * 500
    _write_registry(
        tmp_path,
        [{"file": _REL, "ruleId": "A2A_CROSS_AGENT_INJECT", "lineSha256": _digest(_LINE), "reason": sneaky}],
    )
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True), report)
    assert len(report.warning_calls) == 1
    msg = report.warning_calls[0][0]
    assert "\n" not in msg, "a consent reason must never introduce a line break into the report"
    assert len(msg) < 600, "the reason must be capped, not interpolated unbounded"


def test_loader_skips_malformed_entries_and_wrong_shapes(tmp_path: Path) -> None:
    """Entry-level fail-closed: bad entries are skipped, good ones survive; non-dict / non-list shapes yield empty."""
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
    _write_flagged_file(tmp_path)
    report = _FakeReport()
    _run(tmp_path, _finding_under(tmp_path, demoted=True), report)
    assert not report.warning_calls
    assert len(report.nit_calls) == 1
    assert "needs review" in report.nit_calls[0][0]
