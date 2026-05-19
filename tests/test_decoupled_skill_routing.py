#!/usr/bin/env python3
"""Regression-locks for TRDD-14cc93a6 (v2.91.1 decoupled-routing pattern).

Pins:

* `plugin-fixer.md` declares Phase 0.5 — situation triage + routing table
  that names the safe-ceiling derivation, the four core situations, and
  the `[BATCH_REQUIRED]` exit path.
* `cpv-doctor-agent.md` declares Phase 0 — runtime skill routing and
  emits the `recommend-batch-fix` token when findings exceed safe-ceiling.
* `cpv-main-menu-skill`'s Fix-leaf (§3.2.1) auto-triages and routes to
  either `plugin-fixer` (small plugin) or `/cpv-batch-fix` (big plugin).
* The orphan-detection test recognises agent BODIES (not just frontmatter
  ``skills:`` lists) as a valid loader path.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).parent.parent


class TestPluginFixerPhase0Triage:
    """Plugin-fixer must triage before entering any fix loop."""

    body = (REPO / "agents" / "plugin-fixer.md").read_text(encoding="utf-8")

    def test_phase_0_triage_section_present(self) -> None:
        assert re.search(r"## Phase 0\.5 — MANDATORY situation triage", self.body), (
            "plugin-fixer must declare Phase 0.5 triage section (TRDD-14cc93a6)"
        )

    def test_documents_safe_ceiling_derivation(self) -> None:
        # The body must explain how to compute the safe-ceiling from the
        # declared model — opus/sonnet bare → 30-40, [1m] variants → 100-150.
        # Wider context-window models may differ.
        assert "safe-ceiling" in self.body.lower()
        assert "opus[1m]" in self.body or "sonnet[1m]" in self.body
        assert "200K" in self.body  # documenting the default opus window
        assert "1M" in self.body    # documenting the extended-context variants

    def test_routing_table_lists_four_core_situations(self) -> None:
        # The routing table must explicitly mention: zero findings,
        # within safe-ceiling, above safe-ceiling, batch_shard mode.
        assert "total_findings == 0" in self.body
        assert "safe-ceiling" in self.body
        assert "BATCH_REQUIRED" in self.body
        assert "batch_shard" in self.body

    def test_batch_required_exit_documented(self) -> None:
        """The `[BATCH_REQUIRED]` exit path is the critical one — pin it."""
        # The line must reference both the exit token AND /cpv-batch-fix
        bracket_section = re.search(
            r"`\[BATCH_REQUIRED\][^`]+/cpv-batch-fix[^`]+`",
            self.body,
        )
        assert bracket_section is not None, (
            "plugin-fixer must document the literal [BATCH_REQUIRED] exit "
            "format with /cpv-batch-fix in the same line for the orchestrator "
            "to parse"
        )

    def test_documents_skill_tool_invocation_pattern(self) -> None:
        """Body must demonstrate Skill tool calls for fix-validation + batch-fix-protocol."""
        # Use the fully-qualified marker (catches a regression where someone
        # writes the call without the plugin prefix).
        assert 'skill: "claude-plugins-validation:fix-validation"' in self.body
        assert 'skill: "claude-plugins-validation:batch-fix-protocol"' in self.body


class TestDoctorPhase0Routing:
    """The doctor's runtime routing section + recommend-batch-fix token."""

    body = (REPO / "agents" / "cpv-doctor-agent.md").read_text(encoding="utf-8")

    def test_phase_0_runtime_routing_section_present(self) -> None:
        assert re.search(r"## Phase 0 — Runtime skill routing", self.body)

    def test_documents_skill_global_library_principle(self) -> None:
        """Body must explicitly state skills are a global library (not ACL'd)."""
        assert "global library" in self.body.lower()
        assert "pre-loading hint" in self.body.lower() or "preloading hint" in self.body.lower()
        assert "NOT an access control list" in self.body or "not an access control" in self.body.lower()

    def test_emits_recommend_batch_fix_token_for_big_plugins(self) -> None:
        assert "recommend-batch-fix" in self.body, (
            "Doctor must emit the `recommend-batch-fix` token when findings "
            "exceed plugin-fixer's safe-ceiling (TRDD-14cc93a6 + v2.91.0)"
        )

    def test_routing_table_documented(self) -> None:
        """The doctor's routing table must mention the validator-skill invocation."""
        assert 'skill: "claude-plugins-validation:plugin-validation-skill"' in self.body


class TestMenuFixLeafAutoRoutes:
    """The menu-tree §3.2.1 Fix-plugin-findings leaf must auto-route."""

    menu_tree = (
        REPO
        / "skills"
        / "cpv-main-menu-skill"
        / "references"
        / "menu-tree.md"
    ).read_text(encoding="utf-8")

    def test_fix_leaf_has_triage_step(self) -> None:
        """The Fix-leaf must run a quick-triage validation before dispatch."""
        # The §3.2.1 execution must invoke validate_plugin --json and count findings.
        # Find the §3.2.1 section
        section = re.search(
            r"#### 3\.2\.1 Fix plugin findings(.*?)#### 3\.2\.2",
            self.menu_tree,
            re.DOTALL,
        )
        assert section is not None
        text = section.group(1)
        assert "Quick-triage" in text or "quick-triage" in text or "Quick triage" in text
        assert "validate_plugin.py --json" in text or "validate_plugin --json" in text
        assert "counts." in text  # references the JSON counts field

    def test_fix_leaf_routes_to_batch_for_big_plugins(self) -> None:
        section = re.search(
            r"#### 3\.2\.1 Fix plugin findings(.*?)#### 3\.2\.2",
            self.menu_tree,
            re.DOTALL,
        )
        assert section is not None
        text = section.group(1)
        assert "/cpv-batch-fix" in text
        # Must mention the threshold + the "too many for single-agent" reasoning
        assert "single-agent" in text or "context exhaust" in text or "too many" in text

    def test_fix_leaf_handles_already_clean(self) -> None:
        section = re.search(
            r"#### 3\.2\.1 Fix plugin findings(.*?)#### 3\.2\.2",
            self.menu_tree,
            re.DOTALL,
        )
        assert section is not None
        text = section.group(1)
        # When zero findings, must short-circuit (not dispatch any agent)
        assert "already clean" in text.lower() or "total_findings == 0" in text


class TestSkillsAreGloballyInvocable:
    """The decoupled-routing principle: agents invoke skills via Skill tool, not via frontmatter ACL."""

    def test_plugin_fixer_body_uses_skill_tool_calls(self) -> None:
        """plugin-fixer must demonstrate at least 2 fully-qualified Skill calls in its body."""
        body = (REPO / "agents" / "plugin-fixer.md").read_text(encoding="utf-8")
        matches = re.findall(
            r'skill:\s*"claude-plugins-validation:([a-z0-9_-]+)"',
            body,
        )
        # Body must contain at least 2 distinct skill invocations (e.g.
        # fix-validation + batch-fix-protocol)
        assert len(set(matches)) >= 2, (
            f"plugin-fixer body must demonstrate >=2 fully-qualified Skill calls "
            f"(TRDD-14cc93a6 decoupled routing). Found: {set(matches)}"
        )

    def test_orphan_detection_test_scans_agent_bodies(self) -> None:
        """The extended orphan-detection scan must look in agent bodies (Path 3)."""
        consolidation_test = (
            REPO / "tests" / "test_consolidation_v211.py"
        ).read_text(encoding="utf-8")
        # The test must include scanning agent bodies for the
        # fully-qualified Skill invocation pattern.
        assert "Path 3" in consolidation_test or "agent BODIES" in consolidation_test
        # And it must include the loop over AGENTS_DIR with the skill_invocation_re
        assert "AGENTS_DIR.glob" in consolidation_test
        # The TRDD reference must be present so future readers know why
        assert "14cc93a6" in consolidation_test or "TRDD-14cc93a6" in consolidation_test
