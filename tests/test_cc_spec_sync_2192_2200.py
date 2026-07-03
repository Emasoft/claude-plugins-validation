#!/usr/bin/env python3
"""Two-sided tests for CC spec-sync v2.1.192-2.1.200 allowlist FP-fixes (TRDD-S9NKP4WQ).

Two additive, FN-safe allowlist-widenings (add newly-valid values, remove nothing):

  Fix 1 (v2.1.200): ``"manual"`` is now a valid ``permissions.defaultMode`` /
  agent ``permissionMode`` / ``--permission-mode`` value. It is added to the
  shared ``VALID_PERMISSION_MODES`` frozenset, so both the agent-frontmatter
  path and the settings-``defaultMode`` path stop false-positive-rejecting it.

  Fix 2 (v2.1.198): ``agent_needs_input`` / ``agent_completed`` are new
  Notification hook triggers, added to ``COMMON_NOTIFICATION_TYPES`` so a
  Notification matcher using them is recognized (no spurious unknown-type INFO).

Each fix is proved two-sided: the newly-valid value is NO LONGER flagged, AND a
bogus value STILL is (the allowlist widened, it did not go permissive).
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_agent import (  # noqa: E402
    AgentValidationReport,
    validate_permission_mode_field,
)
from validate_hook import validate_matcher  # noqa: E402
from validate_local_scope import _flag_permissions_default_mode_local  # noqa: E402


def _majors(report: ValidationReport | AgentValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "MAJOR"]


def _infos(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "INFO"]


# ===========================================================================
# Fix 1 (v2.1.200): permission mode "manual"
# ===========================================================================


def test_manual_permission_mode_accepted_agent() -> None:
    """Agent `permissionMode: manual` is accepted (no MAJOR) and records PASSED."""
    report = AgentValidationReport()
    validate_permission_mode_field({"permissionMode": "manual"}, "agent.md", report)
    assert _majors(report) == [], _majors(report)
    passed = [r.message for r in report.results if r.level == "PASSED"]
    assert any("'permissionMode' field valid" in m for m in passed)


def test_bogus_permission_mode_still_flagged_agent() -> None:
    """A bogus agent `permissionMode: banana` STILL reports MAJOR (allowlist not permissive)."""
    report = AgentValidationReport()
    validate_permission_mode_field({"permissionMode": "banana"}, "agent.md", report)
    assert any("Invalid 'permissionMode'" in m for m in _majors(report))


def test_manual_default_mode_accepted_settings() -> None:
    """Settings `permissions.defaultMode: manual` is accepted (no MAJOR)."""
    report = ValidationReport()
    _flag_permissions_default_mode_local(
        {"permissions": {"defaultMode": "manual"}}, report, "settings.json"
    )
    assert _majors(report) == [], _majors(report)


def test_bogus_default_mode_still_flagged_settings() -> None:
    """A bogus settings `permissions.defaultMode: banana` STILL reports MAJOR."""
    report = ValidationReport()
    _flag_permissions_default_mode_local(
        {"permissions": {"defaultMode": "banana"}}, report, "settings.json"
    )
    assert any("'permissions.defaultMode'" in m for m in _majors(report))


# ===========================================================================
# Fix 2 (v2.1.198): Notification triggers agent_needs_input / agent_completed
# ===========================================================================


def test_agent_completed_notification_recognized() -> None:
    """A Notification matcher `agent_completed` is a known trigger — no unknown-type INFO."""
    report = ValidationReport()
    assert validate_matcher("agent_completed", "Notification", report) is True
    assert not any("not a known type" in m for m in _infos(report))


def test_agent_needs_input_notification_recognized() -> None:
    """A Notification matcher `agent_needs_input` is a known trigger — no unknown-type INFO."""
    report = ValidationReport()
    assert validate_matcher("agent_needs_input", "Notification", report) is True
    assert not any("not a known type" in m for m in _infos(report))


def test_bogus_notification_type_still_flagged() -> None:
    """A bogus Notification matcher type STILL triggers the unknown-type INFO (unchanged)."""
    report = ValidationReport()
    assert validate_matcher("banana_trigger", "Notification", report) is True
    assert any("not a known type" in m and "known values" in m for m in _infos(report))


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
