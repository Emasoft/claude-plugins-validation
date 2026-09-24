"""CC v2.1.258–v2.1.281 settings sync: new keys, no-effect keys, attribution:false.

Every acceptance test has a sibling that proves the same code path still
rejects what it should, so no assertion passes vacuously.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cc_scope_rules  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402
from validate_local_scope import validate_settings_local_json  # noqa: E402
from validate_project_scope import validate_settings_json_project_scope  # noqa: E402

NEW_KEYS = [
    "bashEditDiffEnabled",
    "bashOutputMaxChars",
    "copyOnSelect",
    "gatewayInternalNetworks",
    "maxEffortLevel",
    "syncClaudeAiPlugins",
    "taskOutputMaxChars",
]


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_key_is_known(key: str) -> None:
    """Each settings-reference.md row added in this window is a known key."""
    assert key in cc_scope_rules.KNOWN_SETTINGS_KEYS


@pytest.mark.parametrize("typo", ["bashOutputMaxChar", "maxEffortLevels", "taskOutputMaxChar", "syncClaudeAIPlugins"])
def test_near_miss_typo_is_still_unknown(typo: str) -> None:
    """Positive control: a near-miss spelling is NOT excused by the additions."""
    assert typo not in cc_scope_rules.KNOWN_SETTINGS_KEYS


def test_gateway_internal_networks_is_managed_only() -> None:
    """settings-reference.md: 'Scope: Managed. Read only from a source on the machine'."""
    assert "gatewayInternalNetworks" in cc_scope_rules.MANAGED_ONLY_KEYS
    # Control: an any-file key from the same window is NOT managed-only.
    assert "maxEffortLevel" not in cc_scope_rules.MANAGED_ONLY_KEYS


def _project(tmp_path: Path, data: dict) -> cvc.ValidationReport:
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    return report


def _local(tmp_path: Path, data: dict) -> cvc.ValidationReport:
    f = tmp_path / "settings.local.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_local_json(f, report)
    return report


def _hits(report: cvc.ValidationReport, needle: str) -> list[tuple[str, str]]:
    return [(r.level, r.message) for r in report.results if needle in r.message]


def test_gateway_internal_networks_in_project_settings_is_flagged(tmp_path: Path) -> None:
    """A project value is ignored by CC, so the managed-only rule reports it."""
    hits = _hits(_project(tmp_path, {"gatewayInternalNetworks": ["203.0.113.0/24"]}), "gatewayInternalNetworks")
    assert any(level == "MAJOR" for level, _ in hits)


@pytest.mark.parametrize(("key", "since"), [("taskOutputMaxChars", "v2.1.277"), ("keybindingFlavor", "v2.1.261")])
@pytest.mark.parametrize("scope", ["project", "local"])
def test_no_effect_key_emits_info_only(tmp_path: Path, key: str, since: str, scope: str) -> None:
    """A no-effect key draws exactly an INFO naming the version — never a blocking level."""
    report = (_project if scope == "project" else _local)(tmp_path, {key: 1000})
    hits = _hits(report, key)
    assert hits, f"{key} drew no finding in {scope} scope"
    assert all(level == "INFO" for level, _ in hits), hits
    assert any(since in msg for _, msg in hits)


def test_live_key_draws_no_no_effect_info(tmp_path: Path) -> None:
    """Control: a still-live key from the same window gets no 'no effect' note."""
    assert not _hits(_project(tmp_path, {"bashOutputMaxChars": 100000}), "no effect")


def test_attribution_false_warns_in_project_scope(tmp_path: Path) -> None:
    """The shared file: older CLIs skip it wholesale, so a WARNING (non-blocking)."""
    hits = _hits(_project(tmp_path, {"attribution": False}), "attribution")
    assert [level for level, _ in hits] == ["WARNING"], hits


def test_attribution_false_is_silent_in_local_scope(tmp_path: Path) -> None:
    """settings.local.json is not shared across collaborators' CLIs — no warning."""
    assert not _hits(_local(tmp_path, {"attribution": False}), "attribution")


def test_attribution_object_form_is_silent(tmp_path: Path) -> None:
    """The documented object form draws nothing — the rule keys on the boolean only."""
    data = {"attribution": {"commit": "", "pr": "", "sessionUrl": False}}
    assert not _hits(_project(tmp_path, data), "attribution")


def test_attribution_false_draws_no_blocking_finding(tmp_path: Path) -> None:
    """The boolean is valid since v2.1.281: nothing about it may block --strict."""
    hits = _hits(_project(tmp_path, {"attribution": False}), "attribution")
    assert not [h for h in hits if h[0] in {"CRITICAL", "MAJOR", "MINOR", "NIT"}]
