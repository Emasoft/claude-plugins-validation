"""Tests for scripts/cpv_fix_ledger.py — the compact by-file findings ledger.

All fixtures are in-memory JSON shaped exactly like
``remote_validation.py plugin . --strict --json`` output
(``{"results": [ValidationResult.to_dict(), ...]}``). The validator is NEVER
invoked — these tests exercise the pure transform + its CLI in isolation.

Every assertion is two-sided: the positive case (a mech finding lands in
``mech``, a blocking finding is marked blocking) is paired with its negative
(a non-fixable finding lands in ``intel``, an advisory warning is not blocking).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_fix_ledger as ledger  # noqa: E402


def _result(
    level: str,
    message: str,
    *,
    file: Any = "MISSING",
    line: Any = "MISSING",
    fixable: bool | None = None,
    fix_id: str | None = None,
    category: str | None = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    """Build one result dict, omitting keys the real serializer omits."""
    r: dict[str, Any] = {"level": level, "message": message}
    if file != "MISSING":
        r["file"] = file
    if line != "MISSING":
        r["line"] = line
    if fixable:  # the serializer only emits ``fixable`` when true
        r["fixable"] = True
        if fix_id is not None:
            r["fix_id"] = fix_id
    if category:
        r["category"] = category
    if suggestion is not None:
        r["suggestion"] = suggestion
    return r


def _wrap(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap results in the standard findings-JSON envelope."""
    return {"exit_code": 2, "counts": {}, "results": results, "security_gates": {}}


# --------------------------------------------------------------------------
# MECH / INTEL split by ``fixable``
# --------------------------------------------------------------------------


def test_fixable_finding_lands_in_mech() -> None:
    """A finding with fixable:true is bucketed under mech, not intel."""
    out = ledger.build_ledger(
        _wrap([_result("MAJOR", "bad manifest", file="a.json", line=3, fixable=True, fix_id="fix_0")])
    )
    assert "a.json" in out["mech"]
    assert out["intel"] == {}
    assert out["summary"]["mech"] == 1
    assert out["summary"]["intel"] == 0


def test_non_fixable_finding_lands_in_intel() -> None:
    """A finding without fixable is bucketed under intel, not mech."""
    out = ledger.build_ledger(_wrap([_result("MAJOR", "needs judgement", file="a.json", line=3)]))
    assert "a.json" in out["intel"]
    assert out["mech"] == {}
    assert out["summary"]["intel"] == 1
    assert out["summary"]["mech"] == 0


def test_mech_entry_carries_fix_id_not_blocking() -> None:
    """Mech entries expose fix_id and omit the blocking flag."""
    out = ledger.build_ledger(
        _wrap([_result("MINOR", "m", file="f", line=1, fixable=True, fix_id="fix_7", category="skill")])
    )
    entry = out["mech"]["f"][0]
    assert entry == {"line": 1, "level": "MINOR", "category": "skill", "fix_id": "fix_7", "suggestion": "m"}
    assert "blocking" not in entry


def test_intel_entry_carries_blocking_not_fix_id() -> None:
    """Intel entries expose the blocking flag and omit fix_id."""
    out = ledger.build_ledger(_wrap([_result("MINOR", "m", file="f", line=1, category="skill")]))
    entry = out["intel"]["f"][0]
    assert set(entry) == {"line", "level", "category", "blocking", "suggestion"}
    assert "fix_id" not in entry


# --------------------------------------------------------------------------
# Grouping by file + line sort
# --------------------------------------------------------------------------


def test_same_file_findings_grouped_together() -> None:
    """Two findings in the same file share one per-file list."""
    out = ledger.build_ledger(
        _wrap(
            [
                _result("MAJOR", "one", file="same.py", line=10),
                _result("MINOR", "two", file="same.py", line=20),
            ]
        )
    )
    assert list(out["intel"]) == ["same.py"]
    assert len(out["intel"]["same.py"]) == 2


def test_different_files_get_separate_buckets() -> None:
    """Findings in different files get distinct bucket keys."""
    out = ledger.build_ledger(
        _wrap([_result("MAJOR", "one", file="a.py", line=1), _result("MAJOR", "two", file="b.py", line=1)])
    )
    assert set(out["intel"]) == {"a.py", "b.py"}


def test_findings_sorted_by_line_within_file() -> None:
    """A file's findings are sorted ascending by line, line-less entries last."""
    out = ledger.build_ledger(
        _wrap(
            [
                _result("MINOR", "line42", file="f", line=42),
                _result("MINOR", "none", file="f", line=None),
                _result("MINOR", "line5", file="f", line=5),
                _result("MINOR", "line10", file="f", line=10),
            ]
        )
    )
    lines = [e["line"] for e in out["intel"]["f"]]
    assert lines == [5, 10, 42, None]


# --------------------------------------------------------------------------
# Blocking classification — severity levels
# --------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["CRITICAL", "MAJOR", "MINOR", "NIT"])
def test_severity_levels_are_blocking(level: str) -> None:
    """CRITICAL/MAJOR/MINOR/NIT are always blocking under --strict."""
    out = ledger.build_ledger(_wrap([_result(level, "x", file="f", line=1)]))
    assert out["intel"]["f"][0]["blocking"] is True
    assert out["summary"]["blocking"] == 1


def test_info_and_passed_are_excluded() -> None:
    """INFO and PASSED are not findings — excluded from the ledger and counts."""
    out = ledger.build_ledger(
        _wrap(
            [
                _result("PASSED", "ok", file="f", line=1),
                _result("INFO", "fyi", file="f", line=2),
                _result("MAJOR", "real", file="f", line=3),
            ]
        )
    )
    assert out["summary"]["total"] == 1
    assert len(out["intel"]["f"]) == 1
    assert out["intel"]["f"][0]["suggestion"] == "real"


# --------------------------------------------------------------------------
# Blocking classification — WARNING (the discriminating case)
# --------------------------------------------------------------------------


def test_known_advisory_warning_is_not_blocking() -> None:
    """A truly-advisory WARNING (from the doc list) is marked blocking:false."""
    msg = "Found 3 Bash/Shell script(s) — not natively available on Windows"
    out = ledger.build_ledger(_wrap([_result("WARNING", msg, file="f", line=1)]))
    assert out["intel"]["f"][0]["blocking"] is False
    assert out["summary"]["warning"] == 1
    assert out["summary"]["blocking"] == 0


def test_known_publish_blocking_warning_is_blocking() -> None:
    """A publish-blocker WARNING (from the doc list) is marked blocking:true."""
    msg = "Version mismatch: plugin.json=1.0.0 pyproject.toml=1.0.1"
    out = ledger.build_ledger(_wrap([_result("WARNING", msg, file="plugin.json", line=1)]))
    assert out["intel"]["plugin.json"][0]["blocking"] is True
    assert out["summary"]["blocking"] == 1


def test_unknown_warning_defaults_to_blocking() -> None:
    """An unrecognized WARNING defaults to blocking (FN-safe: never advisory)."""
    out = ledger.build_ledger(_wrap([_result("WARNING", "some novel unclassified warning", file="f", line=1)]))
    assert out["intel"]["f"][0]["blocking"] is True


def test_warning_is_blocking_helper_two_sided() -> None:
    """warning_is_blocking: advisory→False, blocker→True, unknown→True."""
    assert ledger.warning_is_blocking("Optional metadata missing (homepage, keywords)") is False
    assert ledger.warning_is_blocking("MARKETPLACE_PAT not configured") is True
    assert ledger.warning_is_blocking("totally unknown warning text") is True


def test_blocker_marker_wins_over_advisory_marker() -> None:
    """A message matching both markers stays blocking (FN-safe precedence)."""
    # Contains an advisory phrase AND a blocker phrase; blocker must win.
    msg = "Optional metadata missing; also: no pre-push hook installed"
    assert ledger.warning_is_blocking(msg) is True


# --------------------------------------------------------------------------
# file == null / absent bucketing
# --------------------------------------------------------------------------


def test_null_file_is_bucketed_under_no_file() -> None:
    """A finding with file:null is bucketed under '<no-file>'."""
    out = ledger.build_ledger(_wrap([_result("MAJOR", "global", file=None, line=None)]))
    assert ledger._NO_FILE in out["intel"]
    assert list(out["intel"]) == ["<no-file>"]


def test_absent_file_is_bucketed_under_no_file() -> None:
    """A finding with no file key is bucketed under '<no-file>'."""
    out = ledger.build_ledger(_wrap([_result("MAJOR", "global", line=None)]))
    assert ledger._NO_FILE in out["intel"]


# --------------------------------------------------------------------------
# suggestion fallback + defaults
# --------------------------------------------------------------------------


def test_missing_suggestion_falls_back_to_message() -> None:
    """A finding without a suggestion uses its message as the suggestion."""
    out = ledger.build_ledger(_wrap([_result("MAJOR", "the message", file="f", line=1)]))
    assert out["intel"]["f"][0]["suggestion"] == "the message"


def test_present_suggestion_is_used_over_message() -> None:
    """A finding with a suggestion keeps the suggestion, not the message."""
    out = ledger.build_ledger(
        _wrap([_result("MAJOR", "the message", file="f", line=1, suggestion="do this instead")])
    )
    assert out["intel"]["f"][0]["suggestion"] == "do this instead"


def test_absent_category_defaults_to_empty_string() -> None:
    """A finding without a category yields category '' in the entry."""
    out = ledger.build_ledger(_wrap([_result("MAJOR", "m", file="f", line=1)]))
    assert out["intel"]["f"][0]["category"] == ""


def test_stringified_line_is_coerced_to_int() -> None:
    """A line value given as a numeric string is coerced to int."""
    out = ledger.build_ledger(_wrap([_result("MAJOR", "m", file="f", line="17")]))
    assert out["intel"]["f"][0]["line"] == 17


# --------------------------------------------------------------------------
# Empty / malformed input
# --------------------------------------------------------------------------


def test_empty_results_yields_empty_zeroed_ledger() -> None:
    """No results → empty mech/intel and a fully-zeroed summary."""
    out = ledger.build_ledger(_wrap([]))
    assert out["mech"] == {}
    assert out["intel"] == {}
    assert out["summary"] == {
        "critical": 0,
        "major": 0,
        "minor": 0,
        "nit": 0,
        "warning": 0,
        "total": 0,
        "mech": 0,
        "intel": 0,
        "blocking": 0,
    }


def test_missing_results_key_yields_empty_ledger() -> None:
    """A dict with no 'results' key is treated as empty, not a crash."""
    out = ledger.build_ledger({"exit_code": 0, "counts": {}})
    assert out["summary"]["total"] == 0


def test_bare_list_input_is_accepted() -> None:
    """A bare list of result dicts (no envelope) is accepted."""
    out = ledger.build_ledger([_result("MAJOR", "m", file="f", line=1)])
    assert out["summary"]["total"] == 1


def test_non_json_object_input_yields_empty_ledger() -> None:
    """A scalar / unexpected input yields an empty ledger."""
    assert ledger.build_ledger(None)["summary"]["total"] == 0
    assert ledger.build_ledger("nonsense")["summary"]["total"] == 0


# --------------------------------------------------------------------------
# Summary aggregation
# --------------------------------------------------------------------------


def test_summary_counts_by_level_and_totals() -> None:
    """Per-level counts, total, mech/intel split, and blocking count all agree."""
    out = ledger.build_ledger(
        _wrap(
            [
                _result("CRITICAL", "c", file="a", line=1),
                _result("MAJOR", "m", file="a", line=2, fixable=True, fix_id="fix_0"),
                _result("MINOR", "mi", file="b", line=1),
                _result("NIT", "n", file="b", line=2),
                _result("WARNING", "language detection: x", file="b", line=3),  # advisory
                _result("PASSED", "ok", file="c", line=1),  # excluded
            ]
        )
    )
    s = out["summary"]
    assert (s["critical"], s["major"], s["minor"], s["nit"], s["warning"]) == (1, 1, 1, 1, 1)
    assert s["total"] == 5
    assert s["mech"] == 1
    assert s["intel"] == 4
    assert s["mech"] + s["intel"] == s["total"]
    # blocking = critical + major + minor + nit (4), advisory warning excluded
    assert s["blocking"] == 4


# --------------------------------------------------------------------------
# Text rendering
# --------------------------------------------------------------------------


def test_render_text_has_sections_and_per_file_headers() -> None:
    """The text view has MECH+INTEL sections and a '<file> (n)' header per file."""
    out = ledger.build_ledger(
        _wrap(
            [
                _result("MAJOR", "fixme", file="a.py", line=10, fixable=True, fix_id="fix_0", category="manifest"),
                _result("MINOR", "judge me", file="b.py", line=5, category="skill"),
            ]
        )
    )
    text = ledger.render_text(out)
    assert "## MECH" in text
    assert "## INTEL" in text
    assert "a.py (1)" in text
    assert "b.py (1)" in text
    assert "L10 MAJOR [manifest]" in text
    assert "L5 MINOR [skill]" in text


def test_render_text_marks_warning_blocking_state() -> None:
    """INTEL warnings are annotated BLOCKING or advisory in the text view."""
    out = ledger.build_ledger(
        _wrap(
            [
                _result("WARNING", "version mismatch: a vs b", file="x", line=1),  # blocking
                _result("WARNING", "orphan lockfile detected", file="y", line=2),  # advisory
            ]
        )
    )
    text = ledger.render_text(out)
    assert "WARNING [] BLOCKING" in text
    assert "WARNING [] advisory" in text


def test_render_text_truncates_long_suggestion() -> None:
    """A long suggestion is flattened and truncated with an ellipsis."""
    long_msg = "x " * 200  # >100 chars, embedded whitespace/newlines collapse
    out = ledger.build_ledger(_wrap([_result("MAJOR", long_msg, file="f", line=1)]))
    text = ledger.render_text(out)
    assert "…" in text
    # No single rendered line should be absurdly long (truncation worked).
    assert max(len(line) for line in text.splitlines()) < 160


def test_render_text_line_less_finding_shows_L_question() -> None:
    """A finding with no line renders as 'L?' in the text view."""
    out = ledger.build_ledger(_wrap([_result("MAJOR", "global", file="f", line=None)]))
    assert "L? MAJOR" in ledger.render_text(out)


# --------------------------------------------------------------------------
# CLI round-trip
# --------------------------------------------------------------------------


def test_cli_build_writes_ledger_and_text(tmp_path: Path) -> None:
    """`build --json --out --text` writes both artifacts and returns 0."""
    findings = tmp_path / "findings.json"
    findings.write_text(
        json.dumps(
            _wrap(
                [
                    _result("MAJOR", "fix me", file="a.py", line=3, fixable=True, fix_id="fix_0"),
                    _result("WARNING", "language detection: python", file="b.py", line=1),
                ]
            )
        ),
        encoding="utf-8",
    )
    out_json = tmp_path / "ledger.json"
    out_txt = tmp_path / "ledger.txt"

    rc = ledger.main(["build", "--json", str(findings), "--out", str(out_json), "--text", str(out_txt)])
    assert rc == 0

    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["summary"]["total"] == 2
    assert data["summary"]["mech"] == 1
    assert data["summary"]["intel"] == 1
    assert "a.py" in data["mech"]
    assert data["intel"]["b.py"][0]["blocking"] is False  # advisory warning
    assert out_txt.read_text(encoding="utf-8").startswith("# fix-ledger")


def test_cli_build_creates_missing_output_dirs(tmp_path: Path) -> None:
    """The build sub-command creates nested output directories as needed."""
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps(_wrap([])), encoding="utf-8")
    out_json = tmp_path / "nested" / "deep" / "ledger.json"

    rc = ledger.main(["build", "--json", str(findings), "--out", str(out_json)])
    assert rc == 0
    assert out_json.exists()


def test_cli_requires_subcommand() -> None:
    """Invoking with no sub-command exits non-zero (argparse enforces it)."""
    with pytest.raises(SystemExit):
        ledger.main([])


# --------------------------------------------------------------------------
# Token-economy gate — the ledger the loop re-reads each iteration is a small
# fraction of the raw findings surface it replaces, WITHOUT losing findings
# (compression, not truncation). This is the measured P1 win (TRDD-GVMOKJBB);
# the assertion is a conservative ceiling so the win cannot silently regress.
# --------------------------------------------------------------------------


def test_ledger_text_far_smaller_but_lossless() -> None:
    """The compact ledger text is well under half the raw findings surface it replaces (measured ~22%) yet preserves every file and the full finding count — compression, not truncation (TRDD-GVMOKJBB)."""
    verbose = (
        "the component declares a value that does not match the documented Claude Code plugin "
        "spec for this position; at load time the runtime silently ignores or mis-handles it, "
        "which usually indicates an authoring mistake to correct before publishing."
    )
    paths = ["skills/a/SKILL.md", "agents/b.md", "commands/c.md", "scripts/d.py"]
    results = [
        _result(
            ["MAJOR", "MINOR", "NIT", "WARNING"][k],
            f"{fi}.{k} {verbose}",
            file=f,
            line=10 + k * 7,
            category="cat",
            suggestion=f"Correct the value at {f}:{10 + k * 7} to the documented form; see the plugin spec.",
        )
        for fi, f in enumerate(paths)
        for k in range(4)
    ]
    findings = _wrap(results)
    raw_bytes = len(json.dumps(findings))
    built = ledger.build_ledger(findings)
    text = ledger.render_text(built)
    # SMALLER: the loop re-reads this every iteration instead of the full findings/report.
    assert len(text) < raw_bytes * 0.5, f"ledger {len(text)}B not < 50% of findings {raw_bytes}B"
    # LOSSLESS (the paired negative): every file and the full count survive the compaction.
    assert built["summary"]["total"] == len(results)
    for f in paths:
        assert f in text
