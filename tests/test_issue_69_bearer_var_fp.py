"""Regression tests for issue #69 — TOKEN_STEAL on a variable Bearer value.

A plugin documenting its OWN auth contract writes
``Authorization: Bearer $AID_AUTH`` (a shell-VARIABLE reference, not a
literal token) in its instruction-loadable SKILL.md. TOKEN_STEAL demoted it
to a NIT, which blocks ``--strict``.

Root cause: ``_is_bearer_token_placeholder`` lowercased the Bearer value
before the "short uppercase identifier" heuristic ran, so that heuristic
(which checks ``[A-Z]``) was DEAD CODE — it never matched an uppercase
shell-var / constant name like ``$AID_AUTH`` / ``YOUR_API_KEY``.

Fix: the uppercase-name heuristic now runs against the ORIGINAL-case value,
gated on a ``$``/``{`` variable marker OR an embedded ``_`` so a contiguous
uppercase SECRET blob (a contiguous all-caps key) is NOT cleared.

TWO-SIDED: a variable / constant-name Bearer value clears; a LITERAL token
(JWT) and a contiguous uppercase secret blob still demote (stay visible).
The credential is the variable's VALUE, which is never present in the doc —
so a variable reference is provably-inert documentation, not a leak.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _skillaudit_markdown_context import _is_bearer_token_placeholder  # noqa: E402
from cpv_skillaudit_native import scan_content  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _token_steal_actionable(content: str) -> list[dict]:
    """Non-suppressed TOKEN_STEAL findings (suppressed = cleared)."""
    return [
        f
        for f in scan_content(content, "skills/governance/SKILL.md")
        if f.get("ruleId") == "TOKEN_STEAL" and not f.get("suppressed") and not f.get("_skillaudit_sentinel")
    ]


class TestBearerVariablePlaceholderCleared:
    """A variable / constant-name Bearer value documents the auth FORMAT,
    not a literal token — TOKEN_STEAL must clear."""

    def test_shell_variable_bearer_cleared(self) -> None:
        """`Authorization: Bearer $AID_AUTH` (the issue #69 case) clears."""
        src = "The agent sends Authorization: Bearer $AID_AUTH to the governance server.\n"
        assert not _token_steal_actionable(src), "a shell-variable Bearer value must clear TOKEN_STEAL"

    def test_braced_variable_bearer_cleared(self) -> None:
        """`Authorization: Bearer ${API_KEY}` clears."""
        src = "Set header Authorization: Bearer ${API_KEY} when calling the API.\n"
        assert not _token_steal_actionable(src)

    def test_constant_name_bearer_cleared(self) -> None:
        """`Authorization: Bearer YOUR_API_KEY` (underscore constant name) clears."""
        src = "Put Authorization: Bearer YOUR_API_KEY in every request.\n"
        assert not _token_steal_actionable(src)


class TestRealBearerTokenStillFires:
    """A LITERAL token or a contiguous uppercase secret blob must stay
    visible (demoted, not suppressed) — FN-safety."""

    def test_literal_jwt_still_fires(self) -> None:
        """A real JWT Bearer value (mixed-case, 32+ chars) still surfaces."""
        src = "Send Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 to the API.\n"
        assert _token_steal_actionable(src), "a literal JWT Bearer token must still surface for triage"

    def test_uppercase_secret_blob_still_fires(self) -> None:
        """A contiguous uppercase blob with no `$`/`_` (e.g. an AWS key id)
        is NOT treated as a variable name — it still surfaces."""
        src = "Send Authorization: Bearer QWERTYUIOPASDFGHJKLZ to the endpoint.\n"
        assert _token_steal_actionable(src), "a contiguous uppercase secret blob must still surface"


class TestBearerPlaceholderUnit:
    """Unit-level checks of the recogniser itself."""

    def test_recognises_shell_variable(self) -> None:
        assert _is_bearer_token_placeholder("Authorization: Bearer $AID_AUTH", "Authorization: Bearer")

    def test_recognises_underscore_constant(self) -> None:
        assert _is_bearer_token_placeholder("Authorization: Bearer MY_TOKEN", "Authorization: Bearer")

    def test_rejects_literal_jwt(self) -> None:
        line = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9aaaa"
        assert not _is_bearer_token_placeholder(line, "Authorization: Bearer")

    def test_rejects_uppercase_secret_blob(self) -> None:
        # Contiguous uppercase, no `$`/`{`/`_` → a possible key, not a name.
        assert not _is_bearer_token_placeholder("Authorization: Bearer QWERTYUIOPASDFGHJKLZ", "Authorization: Bearer")
