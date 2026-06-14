r"""Regression tests for issue #91 — REGEX_DOS false positive on a
dynamically-built **linear** ``new RegExp(...)``.

The catalog REGEX_DOS pattern ``RegExp\s*\(.*\+`` (the bare ``+``-concatenation
variant) fires on ANY dynamically-built regex purely because of string
concatenation — e.g.::

    new RegExp('\\S*/reports/code-auditor-agent/\\S*\\.' + ext + '\\b')

That assembled pattern is ``\S* … literal … \S* … literal`` — single-level
quantifiers, no nesting, no overlapping alternation → linear/polynomial worst
case, NOT catastrophic backtracking. It is flagged ONLY because of the ``+``.

The fix (``_skillaudit_typescript_context.classify`` →
``_new_regexp_is_provably_linear``) suppresses a ``new RegExp(...)`` /
``RegExp(...)`` REGEX_DOS match ONLY when the first argument is constant string
literals + ``+``-joined NON-user identifiers AND neither any literal part NOR
the assembled skeleton has a catastrophic shape (a group quantified by
``+``/``*``/``{…}`` whose body itself carries a quantifier or a ``|``
alternation).

SECURITY — every one of these MUST STILL FIRE (the worst outcome is missing a
real ReDoS, so the fix is rejected if any is suppressed):

  * ``new RegExp('(a+)+' + userInput)``     — nested quantifier
  * ``new RegExp('(\\w+)*' + x)``           — quantified unbounded char class
  * ``/(\\w+)*$/``                          — catastrophic REGEX LITERAL
  * ``new RegExp('(.*)*' + ext)``           — quantified ``.*`` group
  * ``new RegExp(userInput)`` / ``new RegExp(req.query.x)`` — USER-SOURCE arg
    (an attacker-controlled pattern is a ReDoS vector even with no visible
    nested quantifier)
  * ``new RegExp('(' + a + '+)+')``         — nesting assembled ACROSS the
    concatenation

Every assertion is reproduced through the REAL scanner (``scan_content``); the
helper-level checks pin the contract for the ``(a|a)*`` overlapping-alternation
shape (which the catalog does not currently fire on at all — the fix must NOT
be the reason it is benign-classified).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _skillaudit_typescript_context import (  # noqa: E402
    _new_regexp_is_provably_linear,
    _regex_has_catastrophic_shape,
)
from cpv_skillaudit_native import scan_content  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the skillaudit content cache so every scan runs fresh.

    The cache keys on (content_hash, catalog_hash, version, ext) — NOT the
    classifier code — so without this a same-version classifier change would be
    masked by a cache hit.
    """
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _redos_hits(content: str, file_path: str = "scripts/x.js") -> list[dict]:
    """ACTIONABLE (non-suppressed) REGEX_DOS findings for ``content``.

    A demoted (NIT) finding is NOT suppressed, so it still appears here — i.e.
    "still visible / still blocks ``--strict``".
    """
    return [f for f in scan_content(content, file_path) if f.get("ruleId") == "REGEX_DOS" and not f.get("suppressed")]


# ── The reporter's exact case + the minimised variant ──
_FP_REPORTER = r"""const re = new RegExp('\S*/reports/code-auditor-agent/\S*\.' + ext + '\b', 'g');"""
_FP_SIMPLE = r"""const re = new RegExp('\S*/reports/\S*\.' + ext + '\b', 'g');"""

# ── The HARD FN-warning shapes (each MUST still fire) ──
_FN_NESTED_CONCAT_USER = r"""const re = new RegExp('(a+)+' + userInput);"""
_FN_WSTAR_CONCAT = r"""const re = new RegExp('(\w+)*' + x);"""
_FN_LITERAL_WSTAR = r"""const re = /(\w+)*$/;"""
_FN_DOTSTAR_STAR_CONCAT = r"""const re = new RegExp('(.*)*' + ext);"""
_FN_USER_ARG_PLAIN = r"""const re = new RegExp(userInput);"""
_FN_REQ_QUERY = r"""const re = new RegExp(req.query.x);"""
_FN_ASSEMBLED_NESTING = r"""const re = new RegExp('(' + a + '+)+');"""
_FN_ALT_AA = r"""const re = new RegExp('(a|a)*');"""


# ============================================================================
# FP clears — the linear constant-concat RegExp is suppressed
# ============================================================================


class TestLinearRegExpFpClears:
    """The reporter's dynamically-built but LINEAR ``new RegExp(...)`` no
    longer raises an actionable REGEX_DOS."""

    def test_reporter_exact_case_no_redos(self) -> None:
        """The exact reported regex (``\\S* … + ext + …``) yields 0 REGEX_DOS."""
        assert not _redos_hits(_FP_REPORTER), f"linear concat RegExp must not fire REGEX_DOS: {_redos_hits(_FP_REPORTER)!r}"

    def test_minimised_variant_no_redos(self) -> None:
        """The minimised ``\\S*/reports/\\S*\\. + ext + \\b`` variant: 0 REGEX_DOS."""
        assert not _redos_hits(_FP_SIMPLE), f"linear concat RegExp must not fire REGEX_DOS: {_redos_hits(_FP_SIMPLE)!r}"

    def test_member_access_filler_linear_no_redos(self) -> None:
        """A linear regex with a member-access concat filler (``cfg.prefix``)
        is provably linear and suppressed."""
        line = r"""const re = new RegExp('^' + cfg.prefix + '_\d+$');"""
        assert not _redos_hits(line), f"linear member-access concat must not fire: {_redos_hits(line)!r}"


# ============================================================================
# FN-safety — every catastrophic / user-source shape MUST still fire
# ============================================================================


class TestCatastrophicAndUserSourceStillFire:
    """Each genuinely-dangerous shape keeps raising an actionable REGEX_DOS.
    The fix is rejected if any of these is suppressed."""

    def test_nested_quantifier_concat_user_fires(self) -> None:
        """``new RegExp('(a+)+' + userInput)`` — nested quantifier — fires."""
        assert _redos_hits(_FN_NESTED_CONCAT_USER), "nested-quantifier concat MUST fire REGEX_DOS"

    def test_quantified_word_class_concat_fires(self) -> None:
        """``new RegExp('(\\w+)*' + x)`` — quantified unbounded class — fires."""
        assert _redos_hits(_FN_WSTAR_CONCAT), "(\\w+)* concat MUST fire REGEX_DOS"

    def test_catastrophic_regex_literal_fires(self) -> None:
        """``/(\\w+)*$/`` — a catastrophic REGEX LITERAL (not a call) — fires."""
        assert _redos_hits(_FN_LITERAL_WSTAR), "catastrophic regex literal MUST fire REGEX_DOS"

    def test_quantified_dotstar_group_concat_fires(self) -> None:
        """``new RegExp('(.*)*' + ext)`` — quantified ``.*`` group — fires."""
        assert _redos_hits(_FN_DOTSTAR_STAR_CONCAT), "(.*)* concat MUST fire REGEX_DOS"

    def test_user_source_plain_arg_fires(self) -> None:
        """``new RegExp(userInput)`` — attacker-controlled pattern — fires even
        with no visible nested quantifier."""
        assert _redos_hits(_FN_USER_ARG_PLAIN), "user-source RegExp arg MUST fire REGEX_DOS"

    def test_user_source_req_query_fires(self) -> None:
        """``new RegExp(req.query.x)`` — request-derived pattern — fires."""
        assert _redos_hits(_FN_REQ_QUERY), "req.query RegExp arg MUST fire REGEX_DOS"

    def test_assembled_nesting_across_concat_fires(self) -> None:
        """``new RegExp('(' + a + '+)+')`` — the nesting is assembled across the
        ``+`` concatenation — MUST fire (the skeleton assembly catches it)."""
        assert _redos_hits(_FN_ASSEMBLED_NESTING), "concatenation-assembled nesting MUST fire REGEX_DOS"

    def test_literal_split_nesting_across_concat_fires(self) -> None:
        """A catastrophic shape split across two DIRECTLY-ADJACENT string
        literals — ``new RegExp("(a+)" + "+")`` assembles to ``(a+)+`` — MUST
        fire. FN-hole regression (central verification): the prior
        ``placeholder.join(parts)`` inserted a placeholder between EVERY literal
        pair, so literal-adjacent nesting was hidden and suppressed; the fix
        places a placeholder ONLY for an identifier gap, joining adjacent
        literals directly."""
        for js in (
            'const r = new RegExp("(a+)" + "+");',
            'const r = new RegExp("(a" + "+)+");',
            'const r = new RegExp("(.*" + ")*");',
        ):
            assert _redos_hits(js), f"literal-split nesting MUST fire REGEX_DOS: {js}"


# ============================================================================
# Helper-level contract — the fix never VOUCHES for a dangerous shape
# ============================================================================


class TestHelperDoesNotVouchForDangerousShapes:
    """``_new_regexp_is_provably_linear`` returns False (→ keep firing) for
    every dangerous shape, and the catastrophic-shape detector recognises the
    listed nested/overlapping forms. This pins the ``(a|a)*`` overlapping-
    alternation contract: the catalog does not fire on it today, so the test
    asserts the FIX is not the reason it is benign (the helper declines it),
    rather than asserting an unrelated catalog gap is closed."""

    @pytest.mark.parametrize(
        "line",
        [
            _FN_NESTED_CONCAT_USER,
            _FN_WSTAR_CONCAT,
            _FN_DOTSTAR_STAR_CONCAT,
            _FN_USER_ARG_PLAIN,
            _FN_REQ_QUERY,
            _FN_ASSEMBLED_NESTING,
            _FN_ALT_AA,
        ],
    )
    def test_helper_declines_dangerous_shapes(self, line: str) -> None:
        """The linear-suppression helper returns False for every dangerous
        ``new RegExp`` shape, so it never suppresses a real/unproven ReDoS."""
        assert _new_regexp_is_provably_linear(line) is False, f"helper must NOT vouch for: {line!r}"

    def test_helper_vouches_for_reporter_linear_case(self) -> None:
        """The helper DOES vouch for the reporter's linear concat regex."""
        assert _new_regexp_is_provably_linear(_FP_REPORTER) is True

    @pytest.mark.parametrize(
        "pattern",
        [r"(a+)+", r"(\w+)*", r"(\d+)*", r"(.*)*", r"(.+)*", r"(a|a)*", r"(a|ab)*", r"(?:a+)+"],
    )
    def test_catastrophic_detector_recognises_nested_and_alternation(self, pattern: str) -> None:
        """The catastrophic-shape detector flags every nested-quantifier and
        overlapping-alternation form."""
        assert _regex_has_catastrophic_shape(pattern) is True, f"must detect catastrophic shape: {pattern!r}"

    @pytest.mark.parametrize(
        "pattern",
        [r"\S*/reports/\S*\.", r"(abc)+", r"(?:foo)*", r"abc", r"[a-z]+_[0-9]+", r"(a)(b)(c)", r"(abc)?"],
    )
    def test_catastrophic_detector_passes_linear_shapes(self, pattern: str) -> None:
        """The detector does NOT flag linear shapes (a bare quantifier, a
        quantified group with no inner quantifier/alternation)."""
        assert _regex_has_catastrophic_shape(pattern) is False, f"must NOT flag linear shape: {pattern!r}"
