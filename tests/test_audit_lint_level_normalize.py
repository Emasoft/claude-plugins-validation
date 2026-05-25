"""Audit fix #5: ``_replay_results_into_report`` normalizes cached levels.

A cross-release or corrupted lint cache stores ``ValidationResult.to_dict()``
entries; the replay helper used to trust the cached ``level`` string verbatim
(the in-code comment falsely claimed ``ValidationReport.add()`` would raise on
an invalid level — it does not, because ``Level`` is a ``Literal`` erased at
runtime). An unknown level therefore flowed straight through and was silently
mis-bucketed by the exact-string-equality exit-code logic.

These two-sided tests pin both directions of the fix:
  - a VALID cached level replays unchanged (behavior preserved), and
  - an INVALID cached level is normalized to a known ``Level`` value
    instead of being kept as an unknown string.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_lint_engine import _replay_results_into_report  # noqa: E402
from cpv_validation_common import (  # noqa: E402
    Level,
    ValidationReport,
    normalize_level,
)

# The exact set of legal Level values (mirrors the Literal in cpv_validation_common).
KNOWN_LEVELS: frozenset[str] = frozenset(
    {"CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING", "INFO", "PASSED"}
)


def _replay_single(entry: dict[str, object]) -> ValidationReport:
    """Replay one serialised cache entry and hand back the populated report."""
    report = ValidationReport()
    _replay_results_into_report([entry], report)
    return report


# --- normalize_level contract (the dependency the fix relies on) -------------


def test_normalize_level_passes_valid_levels_through_unchanged() -> None:
    """normalize_level is identity on every legal Level value."""
    for level in KNOWN_LEVELS:
        assert normalize_level(level) == level


def test_normalize_level_defaults_unknown_to_info() -> None:
    """normalize_level collapses unknown/garbled levels to the INFO default."""
    assert normalize_level("BOGUS") == "INFO"
    assert normalize_level("CRITICAL ") == "INFO"  # trailing space is NOT a Level
    assert normalize_level("") == "INFO"


# --- side 1: valid cached level replays unchanged (behavior preserved) -------


def test_replay_keeps_valid_major_level_blocking() -> None:
    """A cached MAJOR finding replays as MAJOR (still a blocking severity)."""
    entry = {"level": "MAJOR", "message": "real blocking finding"}
    report = _replay_single(entry)

    assert len(report.results) == 1
    replayed = report.results[0]
    assert replayed.level == "MAJOR"
    assert replayed.message == "real blocking finding"


def test_replay_preserves_all_known_levels() -> None:
    """Every legal cached level survives the round-trip verbatim."""
    serialised = [{"level": lvl, "message": f"msg-{lvl}"} for lvl in sorted(KNOWN_LEVELS)]
    report = ValidationReport()
    _replay_results_into_report(serialised, report)

    replayed_levels = [r.level for r in report.results]
    assert replayed_levels == sorted(KNOWN_LEVELS)


def test_replay_preserves_optional_fields() -> None:
    """Replay carries file/line/phase/fixable/category/suggestion across."""
    entry = {
        "level": "MINOR",
        "message": "with metadata",
        "file": "src/foo.py",
        "line": 42,
        "phase": "lint",
        "fixable": True,
        "fix_id": "RUF100",
        "category": "ruff",
        "suggestion": "remove unused noqa",
    }
    report = _replay_single(entry)

    assert len(report.results) == 1
    r = report.results[0]
    assert r.level == "MINOR"
    assert r.file == "src/foo.py"
    assert r.line == 42
    assert r.phase == "lint"
    assert r.fixable is True
    assert r.fix_id == "RUF100"
    assert r.category == "ruff"
    assert r.suggestion == "remove unused noqa"


# --- side 2: invalid cached level is normalized, never kept verbatim ---------


@pytest.mark.parametrize(
    "bad_level",
    ["CRITICAL ", "BOGUS", "error", "Critical", "majorr", " MINOR"],
)
def test_replay_normalizes_invalid_level_to_known_value(bad_level: str) -> None:
    """An invalid/garbled cached level is normalized to a known Level."""
    entry = {"level": bad_level, "message": "from a corrupt cache"}
    report = _replay_single(entry)

    assert len(report.results) == 1
    replayed_level = report.results[0].level
    # The whole point of the fix: never a raw unknown string flowing through.
    assert replayed_level in KNOWN_LEVELS
    assert replayed_level != bad_level


def test_replay_trailing_space_critical_becomes_info_not_blocking() -> None:
    """A trailing-space 'CRITICAL ' is downgraded to INFO, not a fake CRITICAL.

    This is the concrete corruption the audit called out: exact-string-equality
    exit-code logic would have treated 'CRITICAL ' as a non-matching (and thus
    silently non-blocking) level. After normalization it is the explicit,
    non-blocking INFO default.
    """
    entry = {"level": "CRITICAL ", "message": "trailing-space corruption"}
    report = _replay_single(entry)

    assert report.results[0].level == "INFO"


def test_replay_lowercase_valid_level_is_uppercased() -> None:
    """A lowercase-but-otherwise-valid cached level normalizes to its Level."""
    entry = {"level": "critical", "message": "case drift across releases"}
    report = _replay_single(entry)

    assert report.results[0].level == "CRITICAL"


# --- existing guard rails stay intact ----------------------------------------


def test_replay_skips_non_dict_and_non_str_entries() -> None:
    """Malformed entries (non-dict, missing/non-str level or message) are dropped."""
    serialised: list[object] = [
        "not a dict",
        {"message": "missing level"},
        {"level": "MAJOR"},  # missing message
        {"level": 7, "message": "non-str level"},
        {"level": "MAJOR", "message": 99},  # non-str message
        {"level": "NIT", "message": "the only valid one"},
    ]
    report = ValidationReport()
    _replay_results_into_report(serialised, report)  # type: ignore[arg-type]

    assert len(report.results) == 1
    assert report.results[0].level == "NIT"
    assert report.results[0].message == "the only valid one"


def test_known_levels_match_level_literal() -> None:
    """The test's KNOWN_LEVELS set matches the Level Literal it guards against."""
    import typing

    literal_values = frozenset(typing.get_args(Level))
    assert literal_values == KNOWN_LEVELS
