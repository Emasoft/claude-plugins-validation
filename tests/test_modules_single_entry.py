"""TRDD-775XK7HE follow-up: a hooks.json `modules` array with more than one entry gets one count MAJOR.

Claude Code 2.1.332 allows one hooks module per plugin and refuses a second entry, so a
plugin listing two or more entries gets one MAJOR naming the count. Real files in tmp_path,
no mocks; the module code is never executed.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_hook import HookValidationReport, validate_hooks_data  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_hook.py"
VALID = "export function register(on) {}\n"
COUNT_NEEDLE = "one hooks module per plugin"


def _write(root: Path, entries: list[str]) -> Path:
    """Write hooks/hooks.json listing `entries`, and a valid register module for each entry."""
    hooks = root / "hooks"
    hooks.mkdir(exist_ok=True)
    hooks_json = hooks / "hooks.json"
    hooks_json.write_text(json.dumps({"modules": entries}))
    for entry in entries:
        (hooks / entry).write_text(VALID, encoding="utf-8")
    return hooks_json


def _validate(root: Path, entries: list[str]) -> HookValidationReport:
    """Run validate_hooks_data over a hooks.json listing `entries`."""
    hooks_json = _write(root, entries)
    report = HookValidationReport(hook_path=str(hooks_json))
    return validate_hooks_data({"modules": entries}, root, report)


def _count_majors(report: HookValidationReport) -> list[str]:
    """MAJOR messages that carry the multi-entry count."""
    return [x.message for x in report.results if x.level == "MAJOR" and COUNT_NEEDLE in x.message]


def _all_majors(report: HookValidationReport) -> list[str]:
    """Every MAJOR message in the report."""
    return [x.message for x in report.results if x.level == "MAJOR"]


def test_one_entry_has_no_count_major(tmp_path):
    """A single module entry raises no count MAJOR."""
    assert _count_majors(_validate(tmp_path, ["./a.js"])) == []


def test_two_entries_one_count_major(tmp_path):
    """Two valid entries: exactly one count MAJOR naming 2 entries, and no other MAJOR."""
    report = _validate(tmp_path, ["./a.js", "./b.js"])
    count = _count_majors(report)
    assert len(count) == 1 and "2 entries" in count[0]
    assert _all_majors(report) == count


def test_three_entries_names_the_count(tmp_path):
    """Three valid entries: exactly one count MAJOR naming 3 entries."""
    count = _count_majors(_validate(tmp_path, ["./a.js", "./b.js", "./c.js"]))
    assert len(count) == 1 and "3 entries" in count[0]


def test_duplicate_entry_counts(tmp_path):
    """The same module listed twice still counts as two entries: exactly one count MAJOR."""
    assert len(_count_majors(_validate(tmp_path, ["./a.js", "./a.js"]))) == 1


def test_cli_two_entries_exits_2(tmp_path):
    """CLI: a hooks.json with two modules exits 2 (MAJOR)."""
    hooks_json = _write(tmp_path, ["./a.js", "./b.js"])
    cmd = [sys.executable, str(SCRIPT), str(hooks_json), "--plugin-root", str(tmp_path)]
    assert subprocess.run(cmd, capture_output=True, text=True, timeout=120).returncode == 2
