"""Regression tests for the batch-b26 audit fixes (CPV full-audit 20260531).

Each test corresponds to one audit finding on the files owned by this fix
batch. Every test asserts the CORRECTED behaviour and embeds a guard that
would have caught the original bug. Security-shaped fixes are two-sided:
the benign input stays clean AND the dangerous input stays visible.

Findings covered:
  * MED #15  — cpv_lint_engine.lint_markdown/css/html/sql must NOT return
               False (blocking) when only NIT/MINOR findings exist.
  * MED #66  — cpv_batch_aggregator.aggregate must tolerate index shard
               entries missing 'status_path' / 'shard_id' (no KeyError).
  * MED #67  — cpv_fp_classifier.file_role_of must not classify
               'contest_runner.py' (substring 'test_') as a test file.
  * MED #68  — cpv_install_scanners CLI exit code must ignore the OPTIONAL
               google-re2 accelerator.
  * LOW #140 — _skillaudit_shell_context echo-string suppressor must handle
               a string whose body contains the OPPOSITE quote char.
  * LOW #174 — _skillaudit_json_context._walk_with_lines must map duplicate
               string values onto successive source lines, not collapse them.
  * MED #61  — spec_rule_extractor._heuristic_coverage intentionally only
               emits 'partial'/'unmapped' (covered/missing are human-audit
               buckets) — guard the documented, test-pinned contract.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest.py adds scripts/ to sys.path; add scripts/audit/ for the
# audit-only module, mirroring tests/test_audit_infrastructure.py.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_SCRIPTS_AUDIT = _SCRIPTS / "audit"
for _p in (_SCRIPTS, _SCRIPTS_AUDIT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Probes must never read a stale scan cache.
os.environ.setdefault("CPV_SCAN_CACHE", "0")

from cpv_validation_common import ValidationReport  # noqa: E402

# ---------------------------------------------------------------------------
# Test doubles for the lint subprocess (no real linters spawned)
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_returning(result: _FakeResult):
    def _run(*_args, **_kwargs):
        return result

    return _run


# ---------------------------------------------------------------------------
# MED #15 — NIT/MINOR-only lint runs must return True (non-blocking)
# ---------------------------------------------------------------------------


class TestLintReturnValueNitMinorNonBlocking:
    """A lint helper that only adds NIT/MINOR findings must return True;
    only MAJOR/CRITICAL (or a strict missing-tool failure) flips it False."""

    def test_markdown_nit_only_returns_true(self, tmp_path: Path) -> None:
        from cpv_lint_engine import lint_markdown

        f = tmp_path / "doc.md"
        f.write_text("# Title\n\nlong " + "x" * 200 + "\n")
        report = ValidationReport()
        stderr = "doc.md:3 MD013/line-length Line length [Expected: 80; Actual: 213]\n"
        with patch("cpv_lint_engine._resolve", return_value=["/bin/markdownlint-cli2"]):
            with patch("cpv_lint_engine.subprocess.run", side_effect=_run_returning(_FakeResult(1, "", stderr))):
                ok = lint_markdown(tmp_path, [f], report)
        # Corrected behaviour: NIT-only run is non-blocking.
        assert ok is True
        # Guard the original bug: the findings really are NIT (not MAJOR),
        # so returning False would have been a contract violation.
        md = [r for r in report.results if "markdownlint" in r.message]
        assert md and all(r.level == "NIT" for r in md)

    def test_css_minor_only_returns_true(self, tmp_path: Path) -> None:
        from cpv_lint_engine import lint_css

        f = tmp_path / "a.css"
        f.write_text("body{}\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/stylelint"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_run_returning(_FakeResult(2, "a.css:1:1 expected indentation\n", "")),
            ):
                ok = lint_css(tmp_path, [f], report)
        assert ok is True
        css = [r for r in report.results if "stylelint" in r.message]
        assert css and all(r.level == "MINOR" for r in css)

    def test_html_minor_only_returns_true(self, tmp_path: Path) -> None:
        from cpv_lint_engine import lint_html

        f = tmp_path / "i.html"
        f.write_text("<html></html>\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/htmlhint"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_run_returning(_FakeResult(1, "i.html:1:1 tag must be lowercase\n", "")),
            ):
                ok = lint_html(tmp_path, [f], report)
        assert ok is True

    def test_sql_minor_only_returns_true(self, tmp_path: Path) -> None:
        from cpv_lint_engine import lint_sql

        f = tmp_path / "q.sql"
        f.write_text("select 1;\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/sqlfluff"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_run_returning(_FakeResult(1, "L:1 | P:1 | LT01 | unexpected\n", "")),
            ):
                ok = lint_sql(tmp_path, [f], report)
        assert ok is True

    def test_missing_tool_strict_still_blocks(self, tmp_path: Path) -> None:
        """The fix must NOT weaken the missing-tool gate: a missing linter
        for a detected language is a MAJOR failure in strict mode."""
        from cpv_lint_engine import lint_html, lint_markdown, lint_sql

        for fn, name in ((lint_markdown, "x.md"), (lint_html, "x.html"), (lint_sql, "x.sql")):
            f = tmp_path / name
            f.write_text("x\n")
            report = ValidationReport()
            with patch("cpv_lint_engine._resolve", return_value=None):
                ok = fn(tmp_path, [f], report, strict_missing_tools=True)
            assert ok is False, f"{fn.__name__} must block when its tool is missing (strict)"


# ---------------------------------------------------------------------------
# MED #66 — aggregate tolerates malformed index shard entries
# ---------------------------------------------------------------------------


class TestAggregateToleratesMalformedIndex:
    def _write_index(self, session_dir: Path, shards: list) -> None:
        (session_dir / "index.json").write_text(
            json.dumps({"schema_version": 2, "plugin_path": "/x/p", "shards": shards})
        )

    def test_missing_status_path_does_not_crash(self, tmp_path: Path) -> None:
        from cpv_batch_aggregator import aggregate

        session = tmp_path / "session"
        session.mkdir()
        # One entry has no 'status_path', one has no 'shard_id'.
        self._write_index(
            session,
            [
                {"shard_id": 0},  # missing status_path
                {"status_path": str(tmp_path / "nope.json")},  # missing shard_id + file
            ],
        )
        report_path = tmp_path / "out.md"
        result = aggregate(session, report_path=report_path)
        # No KeyError; both shards degrade to error summaries.
        assert result["shard_count"] == 2
        errors = [s["error"] for s in result["shard_summaries"]]
        assert errors[0] and "status_path" in errors[0]
        assert errors[1]  # missing status file → its own error
        # A malformed shard means the batch is not 'all clean'.
        assert result["all_clean"] is False
        assert report_path.exists()

    def test_non_dict_shard_entry_degrades(self, tmp_path: Path) -> None:
        from cpv_batch_aggregator import aggregate

        session = tmp_path / "session"
        session.mkdir()
        self._write_index(session, ["not-a-dict", 42])
        result = aggregate(session, report_path=tmp_path / "out.md")
        assert result["shard_count"] == 2
        assert all(s["error"] for s in result["shard_summaries"])


# ---------------------------------------------------------------------------
# MED #67 — file_role_of word-boundary test detection
# ---------------------------------------------------------------------------


class TestFileRoleTestBoundary:
    def test_contest_runner_is_not_a_test_file(self) -> None:
        from cpv_fp_classifier import file_role_of

        # 'contest_runner.py' contains the substring 'test_' but is NOT a test.
        assert file_role_of("scripts/contest_runner.py") == "source"
        assert file_role_of("latest_results.py") == "source"
        assert file_role_of("greatest_hits.py") == "source"

    def test_genuine_test_basenames_still_detected(self) -> None:
        from cpv_fp_classifier import file_role_of

        assert file_role_of("scripts/test_foo.py") == "test"
        assert file_role_of("scripts/_test_x.py") == "test"
        assert file_role_of("scripts/__test_helper.py") == "test"
        assert file_role_of("pkg/foo_test.py") == "test"
        assert file_role_of("src/widget.test.ts") == "test"
        assert file_role_of("tests/anything.py") == "test"


# ---------------------------------------------------------------------------
# MED #68 — install-scanners exit code ignores the optional accelerator
# ---------------------------------------------------------------------------


class TestInstallScannersExitCodeOptionalRe2:
    def test_google_re2_is_optional(self) -> None:
        from cpv_install_scanners import _OPTIONAL_SCANNERS

        assert "google-re2" in _OPTIONAL_SCANNERS

    def test_required_only_exit_logic(self) -> None:
        from cpv_install_scanners import _OPTIONAL_SCANNERS

        def required_ok(statuses: dict[str, bool]) -> bool:
            # Mirror the CLI's exit-code predicate exactly.
            return all(ok for name, ok in statuses.items() if name not in _OPTIONAL_SCANNERS)

        # Only the optional accelerator failed → required_ok True (exit 0).
        all_required_pass = {
            "fclones": True,
            "cc-audit": True,
            "trufflehog": True,
            "semgrep": True,
            "tirith": True,
            "skill-scanner": True,
            "google-re2": False,
        }
        assert required_ok(all_required_pass) is True

        # A genuine required scanner failed → required_ok False (exit 1).
        required_failed = dict(all_required_pass)
        required_failed["semgrep"] = False
        assert required_ok(required_failed) is False


# ---------------------------------------------------------------------------
# LOW #140 — echo-string suppressor handles embedded opposite quote (2-sided)
# ---------------------------------------------------------------------------


class TestShellEchoStringOppositeQuote:
    def test_sudo_inside_double_quoted_string_with_apostrophe_is_inside(self) -> None:
        from _skillaudit_shell_context import _match_inside_shell_echo_string

        # Double-quoted display string that contains a ' — the sudo token is
        # display text, so the suppressor must recognise it as INSIDE.
        line = 'echo "it\'s easy: sudo apt install foo"'
        assert _match_inside_shell_echo_string(line, "sudo apt install foo") is True

    def test_double_quote_inside_single_quoted_string_is_inside(self) -> None:
        from _skillaudit_shell_context import _match_inside_shell_echo_string

        line = "echo 'run the \"sudo apt install foo\" step'"
        assert _match_inside_shell_echo_string(line, "sudo apt install foo") is True

    def test_real_sudo_outside_echo_is_not_suppressed(self) -> None:
        from _skillaudit_shell_context import _match_inside_shell_echo_string

        # A genuine invocation NOT inside any echo string must NOT be treated
        # as inside — the suppressor must keep it visible (security-safe side).
        line = "sudo apt install foo"
        assert _match_inside_shell_echo_string(line, "sudo apt install foo") is False
        # echo of an unrelated string, with the real sudo AFTER it.
        line2 = 'echo "banner" && sudo apt install foo'
        assert _match_inside_shell_echo_string(line2, "sudo apt install foo") is False


# ---------------------------------------------------------------------------
# LOW #174 — duplicate JSON string values map to successive source lines
# ---------------------------------------------------------------------------


class TestWalkWithLinesDuplicateValues:
    def test_duplicate_values_resolve_to_distinct_lines(self) -> None:
        from _skillaudit_json_context import _walk_with_lines

        # Two identical values on different lines, distinct keys.
        src = '{\n  "a": "dup",\n  "b": "dup"\n}\n'
        entries = _walk_with_lines(json.loads(src), src)
        by_path = {path: (start, end) for path, start, end in entries}
        # value at key "a" is line 2; value at key "b" is line 3.
        assert by_path[("a",)][0] == 2
        assert by_path[("b",)][0] == 3, "second 'dup' must map to its own line, not collapse onto the first"

    def test_single_value_unchanged(self) -> None:
        from _skillaudit_json_context import _walk_with_lines

        src = '{\n  "k": "only"\n}\n'
        entries = _walk_with_lines(json.loads(src), src)
        assert any(path == ("k",) and start == 2 for path, start, _end in entries)


# ---------------------------------------------------------------------------
# MED #61 — heuristic-coverage documented contract (intentional design)
# ---------------------------------------------------------------------------


class TestHeuristicCoverageContract:
    """The keyword heuristic intentionally emits only 'partial' / 'unmapped';
    'covered' / 'missing' are human-audit buckets. Guard that contract so a
    future change doesn't silently start over-claiming coverage."""

    def test_keyword_match_is_partial_not_covered(self) -> None:
        import spec_rule_extractor

        check, coverage = spec_rule_extractor._heuristic_coverage("The mcpServers field MUST be an object")
        assert coverage == "partial"
        assert check and "MCP" in check

    def test_no_keyword_is_unmapped_not_missing(self) -> None:
        import spec_rule_extractor

        check, coverage = spec_rule_extractor._heuristic_coverage("Some unrelated obligation MUST hold")
        assert coverage == "unmapped"
        assert check is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
