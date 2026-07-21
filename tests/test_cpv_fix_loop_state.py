#!/usr/bin/env python3
"""Two-sided regression lock for the cpv-plugin-fixer-agent oscillation detector.

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


def _set(count: int, tag: str) -> list[dict[str, str]]:
    """`count` distinct findings tagged so each iteration has a NEW signature but a
    FIXED count — models a CI-publish loop whose failing set churns (shifting test
    names) without ever reducing the count: PROGRESS-not-CYCLE, yet not converging."""
    return [_f("minor", f"f{i}.md", f"{tag}-{i}") for i in range(count)]


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
# record — STALLED (opt-in non-progress guard for the CI-publish loop)         #
# --------------------------------------------------------------------------- #
class TestStalled:
    def test_stall_window_zero_never_stalls(self, tmp_path: Path) -> None:
        """Default (stall_window=0): a long churning plateau NEVER stalls — the cheap
        inner validate→fix loop keeps its no-cap behaviour (CONVERGED/CYCLE/PROGRESS only)."""
        state = tmp_path / "s.json"
        verdicts = [loop.record(state, _set(2, f"t{i}"))[0].split()[0] for i in range(8)]
        assert set(verdicts) == {"PROGRESS"}

    def test_flat_count_distinct_sets_stalls_after_window(self, tmp_path: Path) -> None:
        """CI loop: count plateaus at 2 with a NEW signature each cycle (no exact CYCLE).
        With stall_window=3, the 4th non-improving iteration is STALLED, exit 3."""
        state = tmp_path / "s.json"
        out = [loop.record(state, _set(2, f"t{i}"), stall_window=3) for i in range(4)]
        verdicts = [o[0].split()[0] for o in out]
        codes = [o[1] for o in out]
        assert loop._STALLED_EXIT == 3
        assert verdicts == ["PROGRESS", "PROGRESS", "PROGRESS", "STALLED"]
        assert codes == [0, 0, 0, 3]
        assert "stall_streak=3" in out[3][0] and "best=2" in out[3][0]

    def test_strictly_improving_never_stalls(self, tmp_path: Path) -> None:
        """FN-safety: a strictly-shrinking count (5→4→3→2→1) never stalls even with a
        tight window — every iteration is a new best, so the streak resets to 0."""
        state = tmp_path / "s.json"
        verdicts = [loop.record(state, _set(c, f"t{c}"), stall_window=2)[0].split()[0] for c in (5, 4, 3, 2, 1)]
        assert set(verdicts) == {"PROGRESS"}

    def test_improvement_resets_the_streak(self, tmp_path: Path) -> None:
        """A new best mid-plateau resets the streak, delaying STALLED.
        counts 3,3,3,2,2,2,2 @ window 3 → STALLED only on the 7th iteration (not the 4th)."""
        state = tmp_path / "s.json"
        seq = [(3, "a0"), (3, "a1"), (3, "a2"), (2, "b0"), (2, "b1"), (2, "b2"), (2, "b3")]
        out = [loop.record(state, _set(c, t), stall_window=3) for c, t in seq]
        verdicts = [o[0].split()[0] for o in out]
        assert verdicts == ["PROGRESS"] * 6 + ["STALLED"]
        assert out[6][1] == 3 and "best=2" in out[6][0]

    def test_cycle_outranks_stalled(self, tmp_path: Path) -> None:
        """When an iteration is BOTH a stall point AND an exact repeat, CYCLE wins
        (stronger, more specific). A,B,A @ window 2 → iter3 is CYCLE, not STALLED."""
        state = tmp_path / "s.json"
        a, b = _set(2, "A"), _set(2, "B")
        loop.record(state, a, stall_window=2)
        loop.record(state, b, stall_window=2)
        line, code = loop.record(state, a, stall_window=2)  # exact repeat of iter1 AND streak would be 2
        assert line.startswith("CYCLE iterations=3 repeat_of=1")
        assert code == loop._CYCLE_EXIT == 2

    def test_converged_outranks_stalled(self, tmp_path: Path) -> None:
        """An empty set short-circuits to CONVERGED before the stall gate is checked."""
        state = tmp_path / "s.json"
        loop.record(state, _set(2, "A"), stall_window=2)
        loop.record(state, _set(2, "B"), stall_window=2)  # streak would be 1
        line, code = loop.record(state, [], stall_window=2)
        assert line.startswith("CONVERGED iterations=3")
        assert code == 0

    def test_stalled_iteration_recorded_in_state(self, tmp_path: Path) -> None:
        """A STALLED iteration persists verdict + stall_streak + best for the fix-log/summary."""
        state = tmp_path / "s.json"
        for i in range(4):
            loop.record(state, _set(1, f"t{i}"), stall_window=3)
        data = json.loads(state.read_text(encoding="utf-8"))
        last = data["iterations"][-1]
        assert last["verdict"] == "STALLED"
        assert last["stall_streak"] == 3 and last["best"] == 1


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

    def test_record_stall_window_exit3(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """The CLI --stall-window drives STALLED (exit 3) on a churning, non-improving loop."""
        state = tmp_path / "s.json"
        code = 0
        for i in range(4):
            f = self._write(
                tmp_path / f"f{i}.json",
                [
                    {"severity": "minor", "file": "a.md", "message": f"t{i}-0"},
                    {"severity": "minor", "file": "b.md", "message": f"t{i}-1"},
                ],
            )
            code = loop.main(["record", "--state", str(state), "--findings", str(f), "--stall-window", "3"])
        assert code == 3
        assert "STALLED" in capsys.readouterr().out

    def test_record_no_stall_window_does_not_exit3(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Without --stall-window the same churning loop NEVER returns STALLED (back-compat)."""
        state = tmp_path / "s.json"
        code = 0
        for i in range(6):
            f = self._write(
                tmp_path / f"f{i}.json",
                [
                    {"severity": "minor", "file": "a.md", "message": f"t{i}-0"},
                    {"severity": "minor", "file": "b.md", "message": f"t{i}-1"},
                ],
            )
            code = loop.main(["record", "--state", str(state), "--findings", str(f)])
        assert code == 0
        assert "STALLED" not in capsys.readouterr().out
