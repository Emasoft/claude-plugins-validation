"""Regression tests for issue #223 — ENV_INJECTION missing from the sentinel set.

CLAUDE.md documents the audit-consent sentinel (issue #101) as covering
``CMD_INJECTION/SHELL_EXEC/SUPPLY_CHAIN/ENV_INJECTION/PRIVILEGE_ESC/FS_WRITE/…``,
but ``_EXECUTION_CLASS_RULES`` — the set ``_context_classifier_verdict`` actually
checks — omitted ``ENV_INJECTION``, so a real env-var-poisoning finding with the
exact sentinel comment above it never demoted to WARNING, contradicting the
documented contract (a plugin author who followed the docs stayed blocked).

Every assertion is TWO-SIDED and verified end-to-end through the REAL scanner
(``scan_content``, cache off):

* the sentinel demotes the flagged ENV_INJECTION finding to WARNING (still
  present, non-blocking), while
* the SAME flagged code WITHOUT the sentinel stays at its blocking severity,
* ``ENV_INJECTION`` is present in ``_EXECUTION_CLASS_RULES``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_skillaudit_native import _EXECUTION_CLASS_RULES, scan_content  # noqa: E402

SENTINEL = "WARNING: the following code could be malicious. Audit it for safety before executing it!"

# A dynamic key + a file-sourced value — mirrors the issue's real reproducer
# (state.py mirroring CLAUDE_PLUGIN_OPTION_* from settings.json) and is not
# cleared by any of the existing literal-set / build-env / read-modify-write
# carve-outs in `_skillaudit_python_context.py`.
_PY_WITH_SENTINEL = (
    f"# {SENTINEL}\n"
    "import os\n"
    "def mirror(key: str, text: str) -> None:\n"
    "    os.environ[key] = text\n"
)
_PY_WITHOUT_SENTINEL = "import os\ndef mirror(key: str, text: str) -> None:\n    os.environ[key] = text\n"


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the skillaudit content cache so every scan runs fresh."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _severities(content: str, file_path: str, rule_id: str) -> set[str]:
    """The set of scanner-internal severities of non-suppressed findings for one rule_id."""
    return {
        str(f.get("severity", ""))
        for f in scan_content(content, file_path)
        if f.get("ruleId") == rule_id and not f.get("suppressed")
    }


def test_env_injection_sentinel_demotes_to_warning() -> None:
    """A comment sentinel above an ``os.environ[key] = text`` write demotes ENV_INJECTION to WARNING."""
    sevs = _severities(_PY_WITH_SENTINEL, "skills/x/state.py", "ENV_INJECTION")
    assert sevs == {"warning"}, f"the sentinel must demote ENV_INJECTION to WARNING, got: {sevs!r}"


def test_env_injection_no_sentinel_stays_blocking() -> None:
    """The SAME env-var write WITHOUT the sentinel keeps its blocking severity (positive control)."""
    sevs = _severities(_PY_WITHOUT_SENTINEL, "skills/x/state.py", "ENV_INJECTION")
    assert sevs, "the finding must fire without a sentinel"
    assert "warning" not in sevs, f"no sentinel must not demote to WARNING: {sevs!r}"


def test_env_injection_in_execution_class_rules() -> None:
    """``ENV_INJECTION`` is registered in ``_EXECUTION_CLASS_RULES`` (the sentinel-eligible set)."""
    assert "ENV_INJECTION" in _EXECUTION_CLASS_RULES
