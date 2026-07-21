#!/usr/bin/env python3
"""Architecture lock for the fixer/publish loop hardening (TRDD-933592ac).

The loop is a BEHAVIOUR owned by the agent prompts, backed by a deterministic
oscillation detector. These tests pin the four load-bearing facts so a future
edit cannot quietly regress them:

  1. The deterministic oscillation detector script exists and is wired into BOTH
     fixer agents (the old N-vs-N-1 single-step guard let the TOC catch-22 loop
     forever until context exhaustion).
  2. The loop CONTROL FLOW lives in the agent prompts, not the skill ref — the
     agent runs the loop from its own prompt (user directive 2026-06-18).
  3. The publish/upgrade flow loops until CI is green (publish → watch → fix
     cause → re-publish), not "publish once, [PARTIAL] on red".
  4. The corrupt-state guardrails (strict file-scope, content-preservation) and
     the TOC catch-22 plugin-side remediation (§8 Fix B merge) are present.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).parent.parent
PLUGIN_FIXER = REPO / "agents" / "cpv-plugin-fixer-agent.md"
MARKETPLACE_FIXER = REPO / "agents" / "cpv-marketplace-fixer-agent.md"
ITERATIVE_FIX_LOOP = REPO / "skills" / "cpv-fix-validation" / "references" / "iterative-fix-loop.md"
SKILL_FIXES = REPO / "skills" / "cpv-fix-validation" / "references" / "skill-fixes.md"
LOOP_STATE = REPO / "scripts" / "cpv_fix_loop_state.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class TestLoopStateScript:
    def test_script_exists(self) -> None:
        """The deterministic oscillation detector script ships."""
        assert LOOP_STATE.is_file(), "scripts/cpv_fix_loop_state.py must exist"

    def test_script_has_record_and_reset_subcommands(self) -> None:
        """It exposes the reset + record subcommands the agents call each loop."""
        body = _read(LOOP_STATE)
        assert '"reset"' in body and '"record"' in body
        # CYCLE/CONVERGED/PROGRESS are the three verdicts the agents branch on.
        for verdict in ("CONVERGED", "CYCLE", "PROGRESS"):
            assert verdict in body, f"loop-state script missing verdict {verdict}"

    def test_detects_any_prior_iteration_not_just_n_minus_1(self) -> None:
        """The whole point: a repeat is detected vs ANY prior iteration (multi-step cycle)."""
        body = _read(LOOP_STATE).lower()
        assert "any prior" in body or "every prior" in body or "multi-step" in body, (
            "loop-state must document full-history (not N-vs-N-1) cycle detection"
        )


class TestBothFixersWireTheScript:
    def test_plugin_fixer_references_loop_state(self) -> None:
        """cpv-plugin-fixer-agent.md wires cpv_fix_loop_state.py into its loop."""
        assert "cpv_fix_loop_state.py" in _read(PLUGIN_FIXER)

    def test_marketplace_fixer_references_loop_state(self) -> None:
        """cpv-marketplace-fixer-agent.md wires cpv_fix_loop_state.py into its loop."""
        assert "cpv_fix_loop_state.py" in _read(MARKETPLACE_FIXER)


class TestLoopBehaviourOwnedByAgents:
    def test_plugin_fixer_owns_loop_behaviour(self) -> None:
        """cpv-plugin-fixer-agent.md states the loop is ITS behaviour (self-contained, not a skill)."""
        body = _read(PLUGIN_FIXER).lower()
        assert "owns the loop" in body or "this agent's behaviour" in body or "this prompt" in body

    def test_skill_ref_defers_control_flow_to_the_agent(self) -> None:
        """iterative-fix-loop.md hands the CONTROL FLOW to the agent and keeps the DATA."""
        body = _read(ITERATIVE_FIX_LOOP).lower()
        assert "owned by the agent" in body, (
            "iterative-fix-loop.md must state the loop control flow is owned by the agent prompts"
        )
        assert "supporting data" in body

    def test_skill_ref_no_longer_holds_the_while_loop_pseudocode(self) -> None:
        """The behavioral while-loop pseudocode was moved out of the skill ref."""
        body = _read(ITERATIVE_FIX_LOOP)
        assert "while True:" not in body, (
            "the while-loop pseudocode is loop BEHAVIOUR — it belongs in the agent, not this skill ref"
        )


class TestPublishUntilCiGreen:
    def test_plugin_fixer_loops_until_ci_green(self) -> None:
        """Migration §7d publishes then LOOPS until CI is green, not publish-once-then-[PARTIAL]."""
        body = _read(PLUGIN_FIXER)
        assert "gh run watch" in body and "gh run rerun" in body
        assert re.search(r"LOOP UNTIL CI", body, re.IGNORECASE), "cpv-plugin-fixer-agent §7d must loop until CI is green"
        # A red run is a fix iteration: it must fix the CAUSE, never mute the check.
        assert "NEVER" in body and "force-templates" in body


class TestCorruptStateGuardrails:
    def test_strict_file_scope_guardrail(self) -> None:
        """G3: edit only files named in the current findings."""
        body = _read(PLUGIN_FIXER).lower()
        assert "strict file-scope" in body or "files named in the current findings" in body.replace("  ", " ")

    def test_content_preservation_guardrail(self) -> None:
        """G4: a fix must not silently delete content (the corrupt-state failure mode)."""
        body = _read(PLUGIN_FIXER).lower()
        assert "content-preservation" in body or "never silently delete" in body
        assert "git diff --numstat" in _read(PLUGIN_FIXER)


class TestTocCatch22Remediation:
    def test_fix_b_merge_is_mandatory_and_content_preserving(self) -> None:
        """§8 makes Fix-B merge the mandatory, content-preserving escape from the catch-22."""
        body = _read(SKILL_FIXES)
        assert "Fix B is MANDATORY" in body
        assert "content-preserving" in body.lower()
        # The exact amvcp bug — abbreviation fails the verbatim substring match.
        assert "VERBATIM" in body and "what-it-does" in body
