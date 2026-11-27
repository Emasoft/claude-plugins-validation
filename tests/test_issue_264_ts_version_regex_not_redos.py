r"""Regression tests for issue #264 — REGEX_DOS false positive on a TS/JS
dotted-number (version string) regex literal using ``*``.

``/^\d+(?:\.\d+)*$/`` repeats a group that starts with a fixed ``.`` literal
followed by digits, so iterations cannot overlap: linear time. The predicate
``_is_versionish_regex_quantifier`` only knew the ``+`` form. Nested quantifiers
around the same group (``((?:\.\d+)*)*``) and classic ``(a+)+`` shapes MUST
keep firing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_skillaudit_native import scan_content  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the skillaudit content cache so every scan runs fresh."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _redos_hits(content: str) -> list[dict]:
    """Actionable (non-suppressed) REGEX_DOS findings for ``content``."""
    return [
        f for f in scan_content(content, "scripts/x.js") if f.get("ruleId") == "REGEX_DOS" and not f.get("suppressed")
    ]


@pytest.mark.parametrize(
    "src",
    [
        r"const V = /^\d+(?:\.\d+)*$/;",
        r"const V = /^v?\d+(\.\d+)*$/;",
        r"const V = /^\d+(?:\.\d+)+$/;",
    ],
)
def test_dotted_number_group_is_not_redos(src: str) -> None:
    """A version-string regex (dotted-number group, ``*`` or ``+``) yields no REGEX_DOS."""
    assert not _redos_hits(src), f"version regex must not fire REGEX_DOS: {_redos_hits(src)!r}"


@pytest.mark.parametrize(
    "src",
    [
        r"const E = /^(a+)+$/;",
        r"const E = /^x(-[a-z-_]+)*$/;",
        r"const E = /^((?:\.\d+)*)*$/;",
        r"const E = /^(?:(\.\d+)*)+$/;",
    ],
)
def test_nested_or_non_version_quantifier_still_fires(src: str) -> None:
    """Nested quantifiers and non-dotted-number groups keep firing REGEX_DOS."""
    assert _redos_hits(src), f"must still fire REGEX_DOS: {src}"
