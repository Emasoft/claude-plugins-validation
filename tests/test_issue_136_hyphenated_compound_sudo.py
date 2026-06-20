#!/usr/bin/env python3
"""Two-sided regression lock for issue #136 — PRIVILEGE_ESC false positive on the
``sudo`` substring INSIDE a hyphenated compound (``no-sudo`` / ``non-sudo`` /
``passwordless-sudo`` / ``without-sudo`` / ``agentless-sudo`` …).

The PRIVILEGE_ESC catalog pattern ``sudo\\s`` has NO leading word boundary, so it
matches the substring "sudo " inside a ``<word>-sudo`` compound. The reporter's
line in ``docs/ROLE_BOUNDARIES.md`` —
``Added the AID+portfolio-token / no-sudo / immutable-identity facts (R26/R28/R32).``
— fired PRIVILEGE_ESC with ``match='sudo '`` and (in markdown) ``severity=low,
demoted=true``, and a demoted NIT BLOCKS ``--strict``. ``no-sudo`` is a POLICY /
NEGATION token (documenting that agents have NO sudo — the OPPOSITE of an attack),
not a command invocation. This cannot be fixed in the catalog (``\\bsudo`` STILL
matches ``no-sudo`` because the ``o`` -> ``-`` -> ``s`` transition is a word
boundary); it must be a markdown context discriminator.

``_is_hyphenated_compound_sudo`` drives a ``safe_literal`` (full SUPPRESS) verdict
ONLY when EVERY ``sudo`` occurrence the catalog ``sudo\\s`` pattern can match on
the line is a ``<word>-sudo`` compound. A real ``sudo <command>`` (``sudo bash`` /
``sudo -i`` / ``sudo su`` / ``sudo rm`` / ``sudo chmod``) has the ``sudo`` token
at line-start or after whitespace — NEVER as the tail of a ``<word>-sudo`` compound
— so it keeps its EXACT baseline verdict in both prose and a ``` ```bash ``` fence,
and a mixed ``no-sudo container … run sudo bash`` line keeps the real escalation
visible (FN-safe).

Every case is verified through the REAL scanner
(``cpv_skillaudit_native.scan_content``) AND the classifier
(``import _skillaudit_markdown_context as ctx``).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _priv_findings(doc: str) -> list[dict[str, object]]:
    """Run the REAL scanner and return only its PRIVILEGE_ESC findings."""
    from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]

    return [f for f in scan_content(doc, "docs/x.md") if f.get("ruleId") == "PRIVILEGE_ESC"]


def _bash_fence(code_line: str) -> str:
    return "# Doc\n\n```bash\n" + code_line + "\n```\n"


# ────────────────────────────────────────────────────────────────────────
# CLEARS — a ``<word>-sudo`` policy/negation token is fully SUPPRESSED.
# A demoted NIT blocks ``--strict``; full suppression is what un-blocks it.
# ────────────────────────────────────────────────────────────────────────


class TestHyphenatedCompoundSudoClears:
    def test_reporter_slash_list_line_suppressed(self) -> None:
        """The verbatim #136 reporter line clears (no longer a blocking NIT)."""
        line = "Added the AID+portfolio-token / no-sudo / immutable-identity facts (R26/R28/R32).\n"
        findings = _priv_findings(line)
        # Either no PRIVILEGE_ESC finding survives, or it is fully suppressed —
        # in both outcomes it cannot block --strict.
        assert all(f.get("suppressed") is True for f in findings)
        assert not any(f.get("demoted") is True and f.get("suppressed") is False for f in findings)

    def test_no_sudo_prose_suppressed(self) -> None:
        """``no-sudo policy`` in prose → suppressed, not a blocking NIT."""
        findings = _priv_findings("The agent runs with no-sudo policy enforced.\n")
        assert findings, "the catalog must still MATCH so the discriminator is exercised"
        assert all(f.get("suppressed") is True for f in findings)

    def test_non_sudo_prose_suppressed(self) -> None:
        """``non-sudo`` compound → suppressed."""
        findings = _priv_findings("This is a non-sudo execution environment.\n")
        assert findings
        assert all(f.get("suppressed") is True for f in findings)

    def test_passwordless_sudo_prose_suppressed(self) -> None:
        """``passwordless-sudo`` compound → suppressed."""
        findings = _priv_findings("We use passwordless-sudo for the CI runner.\n")
        assert findings
        assert all(f.get("suppressed") is True for f in findings)

    def test_without_sudo_prose_suppressed(self) -> None:
        """``without-sudo`` compound → suppressed."""
        findings = _priv_findings("Run the tool in a without-sudo container.\n")
        assert findings
        assert all(f.get("suppressed") is True for f in findings)

    def test_agentless_sudo_prose_suppressed(self) -> None:
        """``agentless-sudo`` compound → suppressed."""
        findings = _priv_findings("This follows an agentless-sudo design.\n")
        assert findings
        assert all(f.get("suppressed") is True for f in findings)

    def test_compound_in_bash_fence_suppressed(self) -> None:
        """A ``<word>-sudo`` compound is inert in a ``` ```bash ``` fence too."""
        findings = _priv_findings("```bash\n# enforce no-sudo policy here\n```\n")
        assert findings
        assert all(f.get("suppressed") is True for f in findings)

    def test_classify_reporter_line_is_safe_literal(self) -> None:
        """At the classifier level the reporter line resolves to ``safe_literal``."""
        import _skillaudit_markdown_context as ctx

        line = "Added the AID+portfolio-token / no-sudo / immutable-identity facts (R26/R28/R32).\n"
        assert ctx.classify("docs/x.md", line, 0, "sudo ", "PRIVILEGE_ESC") == "safe_literal"


# ────────────────────────────────────────────────────────────────────────
# STILL FIRES — a real ``sudo <command>`` keeps its EXACT baseline verdict.
# The fix must be a no-op on every genuine escalation.
# ────────────────────────────────────────────────────────────────────────


class TestRealSudoStillFires:
    def test_sudo_bash_fence_not_newly_suppressed(self) -> None:
        """``sudo bash`` in a bash fence → stays a finding, NOT suppressed (baseline)."""
        findings = _priv_findings(_bash_fence("sudo bash"))
        assert findings
        assert any(f.get("suppressed") is False for f in findings)

    def test_sudo_dash_i_fence_not_newly_suppressed(self) -> None:
        """``sudo -i`` in a bash fence → stays a finding, NOT suppressed."""
        findings = _priv_findings(_bash_fence("sudo -i"))
        assert findings
        assert any(f.get("suppressed") is False for f in findings)

    def test_sudo_su_fence_not_newly_suppressed(self) -> None:
        """``sudo su`` in a bash fence → stays a finding, NOT suppressed."""
        findings = _priv_findings(_bash_fence("sudo su"))
        assert findings
        assert any(f.get("suppressed") is False for f in findings)

    def test_sudo_rm_fence_not_newly_suppressed(self) -> None:
        """``sudo rm -rf /tmp/x`` in a bash fence → stays a finding, NOT suppressed."""
        findings = _priv_findings(_bash_fence("sudo rm -rf /tmp/x"))
        assert findings
        assert any(f.get("suppressed") is False for f in findings)

    def test_sudo_chmod_fence_not_newly_suppressed(self) -> None:
        """``sudo chmod +s /bin/sh`` in a bash fence → stays a finding, NOT suppressed."""
        findings = _priv_findings(_bash_fence("sudo chmod +s /bin/sh"))
        assert findings
        assert any(f.get("suppressed") is False for f in findings)

    def test_classify_real_sudo_fence_not_safe_literal(self) -> None:
        """A real ``sudo bash`` in a fence is NEVER routed to ``safe_literal``."""
        import _skillaudit_markdown_context as ctx

        assert ctx.classify("docs/x.md", _bash_fence("sudo bash"), 3, "sudo ", "PRIVILEGE_ESC") != "safe_literal"
        assert ctx.classify("docs/x.md", _bash_fence("sudo -i"), 3, "sudo ", "PRIVILEGE_ESC") != "safe_literal"
        assert ctx.classify("docs/x.md", _bash_fence("sudo rm -rf /tmp/x"), 3, "sudo ", "PRIVILEGE_ESC") != "safe_literal"

    def test_mixed_compound_and_real_sudo_keeps_real_visible(self) -> None:
        """A line/block with BOTH a ``no-sudo`` compound AND a real ``sudo -i`` keeps
        the real escalation visible (FN-safe — the discriminator declines)."""
        doc = "```bash\n# no-sudo policy comment\nsudo -i\n```\n"
        findings = _priv_findings(doc)
        # The real `sudo -i` finding must survive un-suppressed.
        assert any(f.get("suppressed") is False for f in findings), findings


# ────────────────────────────────────────────────────────────────────────
# Helper-level scoping — only PRIVILEGE_ESC, only the ``sudo`` token,
# and only when EVERY ``sudo`` on the line is a ``<word>-sudo`` compound.
# ────────────────────────────────────────────────────────────────────────


class TestHyphenatedCompoundSudoScoping:
    def test_helper_clears_compound(self) -> None:
        import _skillaudit_markdown_context as ctx

        assert ctx._is_hyphenated_compound_sudo("with no-sudo policy", "sudo ", "PRIVILEGE_ESC") is True
        assert ctx._is_hyphenated_compound_sudo("a passwordless-sudo runner", "sudo ", "PRIVILEGE_ESC") is True

    def test_helper_declines_real_sudo(self) -> None:
        import _skillaudit_markdown_context as ctx

        assert ctx._is_hyphenated_compound_sudo("sudo bash", "sudo ", "PRIVILEGE_ESC") is False
        assert ctx._is_hyphenated_compound_sudo("Run sudo -i now", "sudo ", "PRIVILEGE_ESC") is False

    def test_helper_declines_mixed_line(self) -> None:
        """One compound + one real ``sudo`` → declines (the real one must stay visible)."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_hyphenated_compound_sudo("no-sudo container but sudo -i forbidden", "sudo ", "PRIVILEGE_ESC") is False

    def test_helper_declines_non_word_prefix(self) -> None:
        """``sudo`` glued to a non-hyphenated word (``thesudo``) is NOT a compound — declines."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_hyphenated_compound_sudo("thesudo bash", "sudo ", "PRIVILEGE_ESC") is False

    def test_helper_declines_other_rule(self) -> None:
        """A non-PRIVILEGE_ESC rule is never suppressed by this branch."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_hyphenated_compound_sudo("with no-sudo policy", "sudo ", "SHELL_EXEC") is False

    def test_helper_declines_non_sudo_match(self) -> None:
        """A PRIVILEGE_ESC match that is NOT the sudo token (``chmod +s``) → declines."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_hyphenated_compound_sudo("no-sudo and chmod +s here", "chmod +s", "PRIVILEGE_ESC") is False

    def test_helper_declines_no_sudo_token(self) -> None:
        """No ``sudo\\s`` occurrence on the line at all → declines."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_hyphenated_compound_sudo("nothing escalates here", "sudo ", "PRIVILEGE_ESC") is False


# ────────────────────────────────────────────────────────────────────────
# CASE-INSENSITIVITY — the catalog ``sudo\s`` matches case-insensitively, so the
# token-finder MUST too. This is load-bearing for BOTH a CAPITALISED-compound FP
# (``No-Sudo`` at a sentence start / heading) AND a latent FN hole: on a mixed
# line ``no-sudo … SUDO bash`` a case-sensitive finder would MISS the capitalised
# real escalation, wrongly conclude "every sudo is a compound", and SUPPRESS the
# real ``SUDO bash``. Both are guarded here.
# ────────────────────────────────────────────────────────────────────────


class TestCaseInsensitiveCompoundSudo:
    def test_capitalised_compound_suppressed(self) -> None:
        """``No-Sudo`` / ``NON-SUDO`` / ``Passwordless-Sudo`` (caps) → suppressed."""
        for token in ("No-Sudo", "NON-SUDO", "Passwordless-Sudo", "without-Sudo"):
            findings = _priv_findings(f"Policy fact: AID / {token} / immutable-identity.\n")
            assert findings, f"catalog must still MATCH {token!r} so the discriminator runs"
            assert all(f.get("suppressed") is True for f in findings), f"{token!r} not suppressed"

    def test_capitalised_real_sudo_still_fires(self) -> None:
        """A capitalised real escalation (``SUDO -i`` / ``Sudo su``) stays visible."""
        for code in ("SUDO -i", "Sudo su"):
            findings = _priv_findings(_bash_fence(code))
            assert findings
            assert any(f.get("suppressed") is False for f in findings), f"{code!r} wrongly suppressed"

    def test_fn_hole_lowercase_compound_plus_capitalised_real_sudo_stays_visible(self) -> None:
        """The FN hole: a lower-case compound AND a CAPITALISED real ``SUDO bash``
        on the same line — the real escalation MUST remain visible (not suppressed)."""
        for doc in (
            "The no-sudo policy notwithstanding, it runs SUDO bash now.\n",
            "AID / No-Sudo / immutable; yet sudo -i executes.\n",
            "passwordless-sudo design, but Sudo su is invoked.\n",
        ):
            findings = _priv_findings(doc)
            assert findings
            assert any(f.get("suppressed") is False for f in findings), f"real sudo suppressed in: {doc!r}"
