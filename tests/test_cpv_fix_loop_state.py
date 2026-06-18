#!/usr/bin/env python3
"""Two-sided regression lock for the plugin-fixer oscillation detector.

TRDD-933592ac / B-cycle. ``scripts/cpv_fix_loop_state.py`` replaces the old
single-step oscillation guard (``signature(N) == signature(N-1)``) — which
missed multi-step cycles and let the TOC-embed catch-22 loop forever until the
agent exhausted its context — with a full-history detector that flags a repeat
against ANY prior iteration.

The contract is two-sided:
  * REAL cycle (a finding multiset recurs — incl. a 2-cycle A→B→A the old guard
    missed) MUST be reported CYCLE (exit 2), pointing at the first occurrence.
  * Genuine progress (any finding cleared / count dropped / message changed)
    MUST be reported PROGRESS — a progressing loop never falsely terminates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_fix_loop_state as loop  # noqa: E402


def _f(sev: str, file: str, msg: str) -> dict[str, str]:
    return {"severity": sev, "file": file, "message": msg}


# --------------------------------------------------------------------------- #
# compute_signature                                                           #
# --------------------------------------------------------------------------- #
class TestComputeSignature:
    def test_same_findings_same_signature(self) -> None:
        """Identical finding multisets hash to the identical signature (stable)."""
        a = [_f("minor", "SKILL.md", "0/13 TOC headings embedded")]
        b = [_f("minor", "SKILL.md", "0/13 TOC headings embedded")]
        assert loop.compute_signature(a) == loop.compute_signature(b)

    def test_order_independent(self) -> None:
        """The validator may emit findings in any order — the signature must not depend on it."""
        x = _f("major", "a.md", "m1")
        y = _f("minor", "b.md", "m2")
        assert loop.compute_signature([x, y]) == loop.compute_signature([y, x])

    def test_empty_is_canonical(self) -> None:
        """An empty finding-set has one canonical signature (drives CONVERGED)."""
        assert loop.compute_signature([]) == loop.compute_signature([])

    def test_message_change_changes_signature(self) -> None:
        """Progress on one finding (0/13 → 1/13) changes the signature — it is NOT 'no change'."""
        before = [_f("minor", "SKILL.md", "0/13 TOC headings embedded")]
        after = [_f("minor", "SKILL.md", "1/13 TOC headings embedded")]
        assert loop.compute_signature(before) != loop.compute_signature(after)

    def test_multiset_not_set_clearing_one_duplicate_changes_signature(self) -> None:
        """Two identical-keyed findings vs one: clearing a duplicate is real progress.

        A *set* signature would alias {dup, dup} with {dup}; the multiset
        signature must differ so the loop sees the cleared finding as progress.
        """
        two = [_f("minor", "x.md", "same"), _f("minor", "x.md", "same")]
        one = [_f("minor", "x.md", "same")]
        assert loop.compute_signature(two) != loop.compute_signature(one)

    def test_distinct_files_distinct_signature(self) -> None:
        """Same rule/message on two different files are two distinct findings."""
        a = [_f("minor", "one.md", "0/5 TOC headings embedded")]
        b = [_f("minor", "two.md", "0/5 TOC headings embedded")]
        assert loop.compute_signature(a) != loop.compute_signature(b)


# --------------------------------------------------------------------------- #
# select_findings                                                            #
# --------------------------------------------------------------------------- #
class TestSelectFindings:
    def test_report_dict_findings_key(self) -> None:
        """Extracts findings from a CPV report dict with a 'findings' list."""
        payload = {"findings": [{"severity": "MINOR", "file": "a.md", "message": "m"}], "summary": {}}
        got = loop.select_findings(payload)
        assert got == [{"severity": "minor", "file": "a.md", "message": "m"}]

    def test_bare_list(self) -> None:
        """Accepts a bare JSON list of finding objects."""
        payload = [{"severity": "MAJOR", "path": "b.md", "msg": "x"}]
        got = loop.select_findings(payload)
        assert got == [{"severity": "major", "file": "b.md", "message": "x"}]

    def test_excludes_info_and_suppressed(self) -> None:
        """info / suppressed severities never count toward the loop set."""
        payload = [
            {"severity": "info", "file": "a", "message": "i"},
            {"severity": "suppressed", "file": "b", "message": "s"},
            {"severity": "nit", "file": "c", "message": "n"},
        ]
        got = loop.select_findings(payload)
        assert got == [{"severity": "nit", "file": "c", "message": "n"}]

    def test_warning_excluded_by_default_included_on_flag(self) -> None:
        """WARNING is opt-in: excluded by default, counted with include_warnings."""
        payload = [{"severity": "warning", "file": "a", "message": "w"}]
        assert loop.select_findings(payload) == []
        assert loop.select_findings(payload, include_warnings=True) == [
            {"severity": "warning", "file": "a", "message": "w"}
        ]

    def test_field_name_fallbacks(self) -> None:
        """severity/sev/level, file/path/location, message/text/title all resolve."""
        payload = [{"level": "critical", "location": "z.md", "title": "boom"}]
        assert loop.select_findings(payload) == [{"severity": "critical", "file": "z.md", "message": "boom"}]

    def test_non_dict_entries_ignored(self) -> None:
        """Stray non-dict list entries are ignored, never guessed at."""
        payload = ["junk", 7, {"severity": "minor", "file": "a", "message": "m"}]
        assert loop.select_findings(payload) == [{"severity": "minor", "file": "a", "message": "m"}]


# --------------------------------------------------------------------------- #
# record — convergence / progress / cycle                                     #
# --------------------------------------------------------------------------- #
class TestRecordVerdicts:
    def test_empty_findings_converged_exit0(self, tmp_path: Path) -> None:
        """No findings → CONVERGED, exit 0."""
        line, code = loop.record(tmp_path / "s.json", [])
        assert line.startswith("CONVERGED iterations=1")
        assert code == 0

    def test_new_signature_progress_exit0(self, tmp_path: Path) -> None:
        """A first non-empty finding-set → PROGRESS, exit 0."""
        line, code = loop.record(tmp_path / "s.json", [_f("minor", "a.md", "m")])
        assert line.startswith("PROGRESS iterations=1 findings=1")
        assert code == 0

    def test_single_step_cycle_detected(self, tmp_path: Path) -> None:
        """An immediate repeat (N == N-1) is a CYCLE pointing at iteration 1."""
        state = tmp_path / "s.json"
        fset = [_f("major", "a.md", "same")]
        loop.record(state, fset)
        line, code = loop.record(state, fset)
        assert line.startswith("CYCLE iterations=2 repeat_of=1")
        assert code == loop._CYCLE_EXIT == 2

    def test_two_cycle_A_B_A_detected_the_bug_the_old_guard_missed(self, tmp_path: Path) -> None:
        """The TOC catch-22: A→B→A. Old single-step guard compared n=3 to n=2 (=B) and MISSED it.

        Full-history detection flags iteration 3's A as a repeat of iteration 1.
        """
        state = tmp_path / "s.json"
        a = [_f("minor", "SKILL.md", "0/13 TOC headings embedded")]
        b = [_f("major", "SKILL.md", "SKILL.md body exceeds the token cap")]
        l1, c1 = loop.record(state, a)
        l2, c2 = loop.record(state, b)
        l3, c3 = loop.record(state, a)  # back to iter-1 state
        assert l1.startswith("PROGRESS iterations=1")
        assert l2.startswith("PROGRESS iterations=2")  # B != A → old guard would keep looping
        assert l3.startswith("CYCLE iterations=3 repeat_of=1")
        assert (c1, c2, c3) == (0, 0, 2)

    def test_three_cycle_A_B_C_A_detected(self, tmp_path: Path) -> None:
        """A longer cycle A→B→C→A is caught when A recurs at iteration 4."""
        state = tmp_path / "s.json"
        a = [_f("minor", "a.md", "A")]
        b = [_f("minor", "b.md", "B")]
        c = [_f("minor", "c.md", "C")]
        loop.record(state, a)
        loop.record(state, b)
        loop.record(state, c)
        line, code = loop.record(state, a)
        assert line.startswith("CYCLE iterations=4 repeat_of=1")
        assert code == 2

    def test_progressing_loop_never_cycles_then_converges(self, tmp_path: Path) -> None:
        """FN-safety: a strictly-shrinking loop (3→2→1→0) is all PROGRESS then CONVERGED, never CYCLE."""
        state = tmp_path / "s.json"
        sets = [
            [_f("minor", "a", "1"), _f("minor", "b", "2"), _f("minor", "c", "3")],
            [_f("minor", "a", "1"), _f("minor", "b", "2")],
            [_f("minor", "a", "1")],
            [],
        ]
        verdicts = [loop.record(state, s)[0].split()[0] for s in sets]
        assert verdicts == ["PROGRESS", "PROGRESS", "PROGRESS", "CONVERGED"]

    def test_cascade_count_rise_is_progress_not_cycle(self, tmp_path: Path) -> None:
        """A fix that exposes NEW findings (count rises) is still PROGRESS — the set is new."""
        state = tmp_path / "s.json"
        loop.record(state, [_f("major", "a", "1")])
        line, code = loop.record(state, [_f("minor", "a", "2"), _f("minor", "b", "3")])
        assert line.startswith("PROGRESS iterations=2 findings=2")
        assert code == 0

    def test_state_persists_across_calls(self, tmp_path: Path) -> None:
        """Each record() reloads the on-disk history — termination survives a degrading context."""
        state = tmp_path / "s.json"
        a = [_f("minor", "a.md", "x")]
        loop.record(state, a)
        loop.record(state, [_f("major", "b.md", "y")])
        # A fresh process (no in-memory history) still sees iteration 1's A and flags the repeat.
        line, code = loop.record(state, a)
        assert line.startswith("CYCLE iterations=3 repeat_of=1")
        assert code == 2

    def test_corrupt_state_file_recovers(self, tmp_path: Path) -> None:
        """A half-written/corrupt state file must not crash the loop — it restarts cleanly."""
        state = tmp_path / "s.json"
        state.write_text("{ this is not json", encoding="utf-8")
        line, code = loop.record(state, [_f("minor", "a", "m")])
        assert line.startswith("PROGRESS iterations=1")
        assert code == 0

    def test_converged_after_cycle_history_is_recorded(self, tmp_path: Path) -> None:
        """The state file accumulates every iteration with its verdict for the fix-log/summary."""
        state = tmp_path / "s.json"
        loop.record(state, [_f("minor", "a", "m")])
        loop.record(state, [_f("minor", "a", "m")])  # cycle
        data = json.loads(state.read_text(encoding="utf-8"))
        assert [it["verdict"] for it in data["iterations"]] == ["PROGRESS", "CYCLE"]
        assert data["iterations"][1]["repeat_of"] == 1


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
class TestCli:
    def _write(self, p: Path, findings: list[dict[str, str]]) -> Path:
        p.write_text(json.dumps({"findings": findings}), encoding="utf-8")
        return p

    def test_reset_then_record_cycle_exit_code(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """reset clears history; two identical records via the CLI yield exit 2 on the repeat."""
        state = tmp_path / "s.json"
        findings = self._write(tmp_path / "f.json", [{"severity": "major", "file": "a.md", "message": "m"}])
        assert loop.main(["reset", "--state", str(state)]) == 0
        assert loop.main(["record", "--state", str(state), "--findings", str(findings)]) == 0
        code = loop.main(["record", "--state", str(state), "--findings", str(findings)])
        out = capsys.readouterr().out
        assert code == 2
        assert "CYCLE" in out

    def test_record_converged_via_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """An empty findings report prints CONVERGED and exits 0."""
        state = tmp_path / "s.json"
        findings = self._write(tmp_path / "f.json", [])
        code = loop.main(["record", "--state", str(state), "--findings", str(findings)])
        assert code == 0
        assert "CONVERGED" in capsys.readouterr().out

    def test_summary_lists_iterations(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """summary prints one row per recorded iteration."""
        state = tmp_path / "s.json"
        findings = self._write(tmp_path / "f.json", [{"severity": "minor", "file": "a", "message": "m"}])
        loop.main(["record", "--state", str(state), "--findings", str(findings)])
        loop.main(["summary", "--state", str(state)])
        out = capsys.readouterr().out
        assert "PROGRESS" in out and "iter" in out

    def test_missing_findings_file_errors_cleanly(self, tmp_path: Path) -> None:
        """A missing findings JSON exits 1 (error) without a traceback."""
        state = tmp_path / "s.json"
        code = loop.main(["record", "--state", str(state), "--findings", str(tmp_path / "nope.json")])
        assert code == loop._ERROR_EXIT == 1
