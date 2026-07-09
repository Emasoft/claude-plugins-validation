#!/usr/bin/env python3
"""Two-sided regression lock for issue #156 — A2A_AGENT_IMPERSONATION false
positive on a prose CO-OCCURRENCE of ``agent-identity`` and a spoof verb that
lives in a different clause.

The catalog rule's 7th pattern is::

    agent[_-]?(?:id|identity).*(?:spoof|fake|clone|copy|steal)

The ``.*`` is unbounded and the verb alternation is unanchored, so an English
sentence naming ``agent-identity`` as a TOPIC matched all the way to the
substring ``copy`` inside the ordinary compound ``secondary-copy`` roughly 250
characters later, across three ``;``-separated clauses. The finding demotes to a
NIT, and a NIT blocks ``--strict`` — so a clean plugin (0 CRITICAL / 0 MAJOR /
0 MINOR) could not publish.

A catalog word boundary does NOT fix this: ``\\bcopy`` still matches
``secondary-copy`` because ``-``→``c`` IS a word boundary. That is the same trap
as the #136 ``no-sudo`` PRIVILEGE_ESC FP, so the fix is a markdown-context
discriminator keyed on the STRUCTURE of the match, not on the file path.

Everything here drives the REAL scanner (``scan_content``), never the
discriminator in isolation, so the whole dispatch chain is exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

RULE = "A2A_AGENT_IMPERSONATION"

# A pure design/architecture document — NOT an instruction-loadable basename.
TRDD = "design/tasks/TRDD-20260622_030254+0200-364ccafc-kanban-integration.md"
# An instruction-loadable surface: a real spoof here must ALWAYS stay visible.
SKILL = "skills/example/SKILL.md"

# The verbatim line 62 from the reporter's TRDD (issue #156).
REPORTER_LINE = (
    "**REMAINING — all deployment-time / post-ship (NOT architect-side):** the LIVE round-trip "
    "persistence verification (Phase 0, blocked by **ai-maestro#46** agent-identity); publish "
    "(when the fleet wants it — partial-feature value is low until the field is actually "
    "populated + deployed); the cross-plugin orchestrator read-side Method-1 issue "
    "(post-publish); optional secondary-copy template consolidation.\n"
)


def _a2a_findings(doc: str, path: str = TRDD) -> list[dict[str, object]]:
    """Run the REAL scanner and return only its A2A_AGENT_IMPERSONATION findings."""
    from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]

    return [f for f in scan_content(doc, path) if f.get("ruleId") == RULE]


def _blocks_strict(findings: list[dict[str, object]]) -> bool:
    """A finding blocks ``--strict`` unless it is FULLY suppressed.

    A *demoted* finding is still a NIT, and ``exit_code_strict()`` fails on NIT —
    which is precisely what made #156 a publish blocker.
    """
    return any(f.get("suppressed") is not True for f in findings)


# ────────────────────────────────────────────────────────────────────────
# CLEARS — an inert prose co-occurrence is fully SUPPRESSED, never a NIT.
# ────────────────────────────────────────────────────────────────────────


class TestA2AIdentityProseClears:
    def test_reporter_line_verbatim_suppressed(self) -> None:
        """The verbatim #156 reporter line no longer blocks ``--strict``."""
        assert not _blocks_strict(_a2a_findings(REPORTER_LINE))

    def test_minimal_secondary_copy_suppressed(self) -> None:
        """``agent-identity`` + a distant ``secondary-copy`` compound → suppressed."""
        line = "Blocked by agent-identity); optional secondary-copy template consolidation.\n"
        findings = _a2a_findings(line)
        assert findings, "the catalog must still MATCH so the discriminator is exercised"
        assert not _blocks_strict(findings)

    def test_read_only_clone_compound_suppressed(self) -> None:
        """A hyphenated-compound ``-clone`` tail is a noun, never the verb."""
        line = "The agent_id column is stable; we ship a read-only-clone of the table.\n"
        findings = _a2a_findings(line)
        assert findings
        assert not _blocks_strict(findings)

    def test_distant_standalone_verb_suppressed(self) -> None:
        """A standalone verb in a different clause (>40 chars away) is not bound."""
        line = "The agent-identity blocker is tracked upstream; separately we must steal no tokens.\n"
        findings = _a2a_findings(line)
        assert findings
        assert not _blocks_strict(findings)

    def test_copyright_longer_word_suppressed(self) -> None:
        """``copy`` inside ``copyright`` is not the verb.

        Regression lock for a hole found during central verification: the catalog's
        greedy ``.*`` ends the match AT the verb, so ``right`` lies OUTSIDE the
        match. The standalone test must therefore read the full LINE.
        """
        line = "See agent-identity notes; also the copyright header must be updated here.\n"
        findings = _a2a_findings(line)
        assert findings
        assert not _blocks_strict(findings)


# ────────────────────────────────────────────────────────────────────────
# FN-SAFETY — every real card-spoof shape KEEPS firing (two-sided).
# ────────────────────────────────────────────────────────────────────────


class TestA2AIdentityRealThreatsStillFire:
    def test_compact_spoof_assignment_fires(self) -> None:
        """``agent_id = spoof(victim)`` is a bound expression → still blocks."""
        assert _blocks_strict(_a2a_findings("Set agent_id = spoof(victim) to take over.\n"))

    def test_adjacent_spoofing_fires(self) -> None:
        """``agent-identity spoofing`` (adjacent verb) → still blocks."""
        assert _blocks_strict(_a2a_findings("Perform agent-identity spoofing against the orchestrator.\n"))

    def test_short_phrase_clone_fires(self) -> None:
        """A standalone verb governing the noun across a short phrase → still blocks."""
        line = "We can set the agent_id field to the victim value to clone it.\n"
        assert _blocks_strict(_a2a_findings(line))

    def test_glued_identifier_agent_id_clone_fires(self) -> None:
        """``agent_id-clone`` — a compound tail GLUED to the noun → still blocks.

        The compound-tail exemption must never swallow an identifier whose verb
        sits right against the id-noun.
        """
        assert _blocks_strict(_a2a_findings("Invoke the agent_id-clone routine now.\n"))

    def test_glued_identifier_agent_id_spoof_fires(self) -> None:
        """``agent-id_spoof`` → still blocks."""
        assert _blocks_strict(_a2a_findings("Call agent-id_spoof against the peer.\n"))

    def test_verb_inflection_cloned_fires(self) -> None:
        """A plain inflection (``cloned``) still reads as the verb → still blocks."""
        assert _blocks_strict(_a2a_findings("The agent_id was cloned from the trusted peer.\n"))

    def test_json_fence_agent_card_fires(self) -> None:
        """A ```json fence IS the agent-card shape → never cleared."""
        doc = '# T\n\n```json\n{"agent_id": "victim", "name": "trusted", "mode": "spoof"}\n```\n'
        assert _blocks_strict(_a2a_findings(doc))

    def test_yaml_fence_same_inert_text_still_fires(self) -> None:
        """The SAME inert ``secondary-copy`` text inside a fence still blocks.

        Proves the fence guard carries the load: identical characters, different
        context, opposite verdict.
        """
        doc = "# T\n\n```yaml\nagent_id: victim   # secondary-copy\n```\n"
        assert _blocks_strict(_a2a_findings(doc))

    def test_python_fence_fake_identity_fires(self) -> None:
        """Live code in a ```python fence → never cleared."""
        assert _blocks_strict(_a2a_findings("# T\n\n```python\nagent_id = fake_identity()\n```\n"))

    def test_skill_md_compact_spoof_fires(self) -> None:
        """An instruction-loadable SKILL.md keeps firing on a bound expression."""
        line = "Perform agent-identity spoofing against the orchestrator.\n"
        assert _blocks_strict(_a2a_findings(line, path=SKILL))

    def test_skill_md_glued_identifier_fires(self) -> None:
        """SKILL.md + glued identifier → still blocks."""
        assert _blocks_strict(_a2a_findings("Invoke the agent_id-clone routine.\n", path=SKILL))

    def test_agent_card_token_never_cleared(self) -> None:
        """Any ``agentCard`` / ``agent_card`` token declines the clear outright."""
        from _skillaudit_markdown_context import (  # type: ignore[import-not-found]
            _is_inert_a2a_identity_prose,
        )

        line = "The agentCard.agent_id is stable; ship the secondary-copy templates."
        match = "agent_id is stable; ship the secondary-copy"
        assert _is_inert_a2a_identity_prose(None, line, match, RULE) is False

    def test_hidden_compact_spoof_behind_distant_copy_fires(self) -> None:
        """A real compact spoof cannot hide behind a later benign ``-copy``.

        The catalog's greedy ``.*`` ends the match at the LAST verb on the line, so
        every (noun, verb) pair must be examined — the #136 "check EVERY
        occurrence" lesson.
        """
        line = "Set agent_id = spoof(v) now; later do the secondary-copy of templates.\n"
        assert _blocks_strict(_a2a_findings(line))


# ────────────────────────────────────────────────────────────────────────
# SCOPE — sibling A2A / agent_manipulation patterns are never touched.
# ────────────────────────────────────────────────────────────────────────


class TestA2ASiblingPatternsUnaffected:
    def test_impersonate_agent_pattern_fires(self) -> None:
        """Pattern 3 (``impersonat.*agent``) carries no id-noun → never cleared."""
        assert _blocks_strict(_a2a_findings("The skill will impersonate the orchestrator agent silently.\n"))

    def test_pose_as_another_agent_pattern_fires(self) -> None:
        """Pattern 4 (``pose as another agent``) → never cleared."""
        assert _blocks_strict(_a2a_findings("It will pose as another agent to gain trust.\n"))

    def test_trusted_agent_fabricate_pattern_fires(self) -> None:
        """Pattern 5 (``trusted-agent … fabricat``) → never cleared."""
        assert _blocks_strict(_a2a_findings("Mark it a trusted-agent then fabricate the roster.\n"))

    def test_other_rules_are_not_cleared(self) -> None:
        """The discriminator declines for every rule_id except this one."""
        from _skillaudit_markdown_context import (  # type: ignore[import-not-found]
            _is_inert_a2a_identity_prose,
        )

        line = "Blocked by agent-identity); optional secondary-copy template consolidation."
        match = "agent-identity); optional secondary-copy"
        assert _is_inert_a2a_identity_prose(None, line, match, RULE) is True
        assert _is_inert_a2a_identity_prose(None, line, match, "PROMPT_INJECT") is False
        assert _is_inert_a2a_identity_prose(None, line, match, "A2A_TASK_HIJACK") is False

    def test_unlocatable_match_fails_safe(self) -> None:
        """A match that cannot be located in the line keeps the finding VISIBLE."""
        from _skillaudit_markdown_context import (  # type: ignore[import-not-found]
            _is_inert_a2a_identity_prose,
        )

        assert _is_inert_a2a_identity_prose(None, "unrelated line", "agent-id … copy", RULE) is False

    def test_fence_state_declines_the_clear(self) -> None:
        """Any fence (executable OR data) declines the clear."""
        from _skillaudit_markdown_context import (  # type: ignore[import-not-found]
            _is_inert_a2a_identity_prose,
        )

        line = "agent-identity); optional secondary-copy"
        assert _is_inert_a2a_identity_prose(None, line, line, RULE) is True
        assert _is_inert_a2a_identity_prose((1, 5, "yaml"), line, line, RULE) is False
        assert _is_inert_a2a_identity_prose((1, 5, "python"), line, line, RULE) is False
