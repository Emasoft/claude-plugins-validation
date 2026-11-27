"""A slash right after a dot is a relative path, so the system absolute path rule skips it."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_validation_common import (  # noqa: E402
    ABSOLUTE_PATH_PATTERNS,
    ValidationReport,
    scan_file_for_absolute_paths,
)


def _system_path_matches(text: str) -> list[str]:
    """Return the group(1) texts that the system absolute path entry matches in text."""
    (pattern,) = [regex for regex, label in ABSOLUTE_PATH_PATTERNS if label == "system absolute path"]
    return [match.group(1) for match in pattern.finditer(text)]


def test_dot_dot_relative_import_is_not_matched() -> None:
    """A dynamic import of a parent-relative module path matches nothing."""
    assert _system_path_matches('await import("../lib/pillar/stdio.ts")') == []


def test_dot_slash_relative_path_is_not_matched() -> None:
    """A dot-slash relative path matches nothing."""
    assert _system_path_matches("./lib/x.ts") == []


def test_bare_lib_path_is_matched() -> None:
    """A bare system path after a space is matched whole."""
    assert _system_path_matches("cat /lib/x") == ["/lib/x"]


def test_quoted_usr_path_is_matched() -> None:
    """A system path inside double quotes is matched without the quotes."""
    assert _system_path_matches('"/usr/lib/x"') == ["/usr/lib/x"]


def test_env_assignment_path_is_matched() -> None:
    """A system path after an equals sign is matched whole."""
    assert _system_path_matches("PATH=/usr/local/bin") == ["/usr/local/bin"]


def test_shebang_path_is_not_matched() -> None:
    """A shebang line names an interpreter path and matches nothing."""
    assert _system_path_matches("#!/usr/bin/env node") == []


def test_scan_ignores_dot_relative_import_and_flags_system_path(tmp_path: Path) -> None:
    """Through the real scanner, a parent-relative import raises no finding while a system path still does."""
    relative = tmp_path / "relative.ts"
    relative.write_text('await import("../lib/pillar/stdio.ts")\n', encoding="utf-8")
    system = tmp_path / "system.ts"
    system.write_text("cat /lib/x\n", encoding="utf-8")
    assert scan_file_for_absolute_paths(relative, ValidationReport(), "relative.ts") == 0
    system_report = ValidationReport()
    assert scan_file_for_absolute_paths(system, system_report, "system.ts") >= 1
    assert any("/lib/x" in result.message for result in system_report.results)


def test_glued_dot_path_is_not_matched() -> None:
    """A system path glued directly after a dot is part of a relative token, so it matches nothing."""
    assert _system_path_matches("x./usr/lib/x") == []


def test_path_before_sentence_period_is_matched() -> None:
    """The regex keeps a sentence period inside the match; trimming it happens in absolute_path_match_text."""
    assert _system_path_matches("see /usr/lib.") == ["/usr/lib."]
