"""Issue #285 (owner decision 2026-11-28, TRDD-DH3PAC5I) - system profile folders are not private home paths.

Only the Windows/macOS system profile names Default, Default User, Public, All Users and Shared
are exempt from the home-path findings. Every other name (bob, alice, jsmith2, realname, a name
that merely starts with a system name) stays a finding, exactly as before.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    scan_file_for_absolute_paths,
    scan_file_for_private_info,
)
from validate_security import scan_for_user_paths  # noqa: E402

SYSTEM_PROFILE_PATHS = [
    "/Users/Default/x",
    "/Users/Shared/x",
    "/Users/shared/x",
    "c:/Users/Default/x",
    "C:\\Users\\Public\\x",
    "C:\\Users\\PUBLIC\\x",
    "C:\\Users\\All Users\\x",
    "C:\\Users\\all users\\x",
    "C:\\Users\\Default User\\x",
]

STILL_FLAGGED_PATHS = [
    "/Users/jsmith2/x",
    "C:\\Users\\realname\\x",
    "/Users/bob/x",
    "/home/alice/x",
    "/Users/Defaultuser/x",
    "/Users/publicx/x",
    "C:\\Users\\Defaultuser\\x",
    "C:\\Users\\All Bob\\x",
    "C:\\Users\\Allusers\\x",
]


def _doc(tmp_path: Path, path_text: str) -> Path:
    doc = tmp_path / "README.md"
    doc.write_text(f"Example:\n\n```\n{path_text}\n```\n", encoding="utf-8")
    return doc


@pytest.mark.parametrize("path_text", SYSTEM_PROFILE_PATHS)
def test_system_profile_path_is_not_a_finding(tmp_path: Path, path_text: str) -> None:
    """A system profile folder (Default, Public, All Users, Shared) raises no absolute-path or private-path finding."""
    doc = _doc(tmp_path, path_text)
    assert scan_file_for_absolute_paths(doc, ValidationReport(), "README.md") == 0
    assert scan_file_for_private_info(doc, ValidationReport(), "README.md") == 0


@pytest.mark.parametrize("path_text", STILL_FLAGGED_PATHS)
def test_other_names_still_flagged(tmp_path: Path, path_text: str) -> None:
    """Any other name, including look-alikes of a system name, is still a home-path finding."""
    doc = _doc(tmp_path, path_text)
    report = ValidationReport()
    assert scan_file_for_absolute_paths(doc, report, "README.md") >= 1
    assert any(r.level == "MAJOR" for r in report.results)


@pytest.mark.parametrize("path_text", ["/Users/Default/x", "/Users/Shared/x", "C:\\Users\\Public\\x"])
def test_security_scan_skips_system_profile(path_text: str) -> None:
    """The RC-135 user-path scan in validate_security also skips system profiles."""
    report = ValidationReport()
    assert scan_for_user_paths(f'cfg = "{path_text}"\n', "hooks/run.sh", report) == 0


@pytest.mark.parametrize(
    "path_text", ["/Users/bob/x", "/home/alice/x", "/Users/Defaultuser/x", "C:\\Users\\jsmith2\\x"]
)
def test_security_scan_still_flags_other_names(path_text: str) -> None:
    """The RC-135 user-path scan still flags every non-system name."""
    report = ValidationReport()
    assert scan_for_user_paths(f'cfg = "{path_text}"\n', "hooks/run.sh", report) >= 1
