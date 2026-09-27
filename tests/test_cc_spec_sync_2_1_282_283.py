"""CC v2.1.282–283 settings sync: maxProseWidth + three managed-only keys.

Every "now known" assertion is paired with a near-miss control proving the
typo detector still fires, so no acceptance test passes vacuously.
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
from validate_project_scope import validate_settings_json_project_scope  # noqa: E402

NEW_ANY_FILE_KEYS = ["maxProseWidth"]
NEW_MANAGED_ONLY_KEYS = [
    "allowClaudeInChromeWithManagedMcp",
    "availableModelsMatch",
    "deniedModels",
]


@pytest.mark.parametrize("key", NEW_ANY_FILE_KEYS + NEW_MANAGED_ONLY_KEYS)
def test_new_key_is_known(key: str) -> None:
    """Each v2.1.282–283 changelog key is a known settings key (no unknown-key hint)."""
    assert key in cc_scope_rules.KNOWN_SETTINGS_KEYS


@pytest.mark.parametrize(
    "typo",
    ["maxProseWidt", "allowClaudeInChromeWithManagedMcp2", "availableModelsMatching", "deniedModel"],
)
def test_near_miss_typo_is_still_unknown(typo: str) -> None:
    """Positive control: a near-miss spelling is NOT excused by the additions."""
    assert typo not in cc_scope_rules.KNOWN_SETTINGS_KEYS


@pytest.mark.parametrize("key", NEW_MANAGED_ONLY_KEYS)
def test_managed_only_keys_are_managed_only(key: str) -> None:
    """The three v2.1.282–283 managed keys belong in MANAGED_ONLY_KEYS."""
    assert key in cc_scope_rules.MANAGED_ONLY_KEYS
    # Control: the cosmetic UI key from v2.1.282 is NOT managed-only.
    assert "maxProseWidth" not in cc_scope_rules.MANAGED_ONLY_KEYS


@pytest.mark.parametrize("key", NEW_MANAGED_ONLY_KEYS)
def test_managed_only_key_in_project_settings_is_flagged(tmp_path: Path, key: str) -> None:
    """A project-scope value of a managed-only key is ignored by CC — MAJOR."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({key: True}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    hits = [r for r in report.results if key in r.message]
    assert hits, f"{key} drew no finding in project scope"
    assert any(r.level == "MAJOR" for r in hits), [(r.level, r.message) for r in hits]


def test_max_prose_width_in_project_settings_is_clean(tmp_path: Path) -> None:
    """maxProseWidth is a valid any-file key: no blocking finding, no unknown-key hint."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"$schema": "https://json.schemastore.org/claude-code-settings.json", "maxProseWidth": 120}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    blocking = [r for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT")]
    assert not blocking, [(r.level, r.message) for r in blocking]
    assert not [r for r in report.results if "maxProseWidth" in r.message and "Unknown" in r.message]
