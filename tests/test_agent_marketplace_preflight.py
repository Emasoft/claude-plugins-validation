"""Tests for Phase F of TRDD-c0ee9543 — agent pre-completion gate.

The plugin-creator, plugin-fixer, and cpv-upgrade-plugin agents MUST refuse
to declare DONE on a plugin/marketplace combo that fails the new Phase A
+ Phase B cross-validation.

These are architectural assertions on agent prompts — we read the markdown
files and check the gate text is present + correctly worded.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestPluginCreatorPreflight:
    """Phase F.1 — plugin-creator MUST gate on marketplace cross-check."""

    def test_plugin_creator_runs_marketplace_cross_check_before_declaring_done(self):
        """Look for explicit cross-check invocation in plugin-creator.md."""
        text = (REPO_ROOT / "agents" / "plugin-creator.md").read_text(encoding="utf-8")
        assert "validate_marketplace.py" in text, "plugin-creator must reference validate_marketplace.py"
        # Phase F gate must explicitly cite the RC-MKPL-* MAJOR codes.
        assert "RC-MKPL-NAME-MISMATCH" in text, "plugin-creator must call out RC-MKPL-NAME-MISMATCH as a block-on code"
        assert "RC-MKPL-UNKNOWN-FIELD" in text, "plugin-creator must call out RC-MKPL-UNKNOWN-FIELD as a block-on code"
        assert "TRDD-c0ee9543" in text, "plugin-creator gate must cite TRDD-c0ee9543"

    def test_plugin_creator_distinguishes_user_blessed_vs_agent_introduced_drift(self):
        """Per §9 risk row — agent must not auto-add opt-out flags."""
        text = (REPO_ROOT / "agents" / "plugin-creator.md").read_text(encoding="utf-8")
        # Must explicitly call out the dual-mode (user-blessed vs agent-introduced).
        assert "_cpv_skip_upstream_check" in text
        assert (
            "user-blessed" in text.lower()
            or "user-confirmation" in text.lower()
            or "user-side declaration" in text.lower()
        )


class TestPluginFixerPreflight:
    """Phase F.2 — plugin-fixer MUST gate on marketplace cross-check."""

    def test_plugin_fixer_blocks_completion_on_unresolved_marketplace_drift(self):
        text = (REPO_ROOT / "agents" / "plugin-fixer.md").read_text(encoding="utf-8")
        assert "validate_marketplace.py" in text
        assert "RC-MKPL-NAME-MISMATCH" in text
        assert "RC-MKPL-UNKNOWN-FIELD" in text
        assert "TRDD-c0ee9543" in text

    def test_plugin_fixer_references_recipe_file(self):
        """plugin-fixer must point at the recipes file for the codes it gates on."""
        text = (REPO_ROOT / "agents" / "plugin-fixer.md").read_text(encoding="utf-8")
        assert "marketplace-upstream-drift.md" in text, "plugin-fixer must reference the new recipe file"


class TestCpvUpgradePluginPreflight:
    """Phase F.3 — cpv-upgrade-plugin command MUST document the new gate."""

    def test_cpv_upgrade_plugin_documents_marketplace_cross_check(self):
        text = (REPO_ROOT / "commands" / "cpv-upgrade-plugin.md").read_text(encoding="utf-8")
        assert "validate_marketplace.py" in text
        assert "RC-MKPL-NAME-MISMATCH" in text
        assert "TRDD-c0ee9543" in text

    def test_cpv_upgrade_plugin_references_ai_maestro_incident(self):
        """The command's gate must cite the 2026-05-11 incident as motivation."""
        text = (REPO_ROOT / "commands" / "cpv-upgrade-plugin.md").read_text(encoding="utf-8")
        # Citing the incident or its date is sufficient signal that the gate
        # was added for the right reason and won't be silently removed.
        assert "2026-05-11" in text or "ai-maestro-visual-communicator" in text


class TestFixValidationSkillIndexes:
    """Phase F.4 — fix-validation skill must list the new RC-MKPL-* codes."""

    def test_marketplace_error_index_has_rc_mkpl_section(self):
        text = (REPO_ROOT / "skills/fix-validation/references/marketplace-error-index.md").read_text(encoding="utf-8")
        assert "1.1 RC-MKPL-* upstream cross-validation codes" in text
        assert "RC-MKPL-NAME-MISMATCH" in text
        assert "RC-MKPL-UNKNOWN-FIELD" in text
        assert "RC-MKPL-UNKNOWN-SOURCE-FIELD" in text
        assert "RC-MKPL-VERSION-DRIFT" in text
        assert "RC-MKPL-METADATA-DRIFT" in text
        assert "RC-MKPL-UPSTREAM-UNREACHABLE" in text

    def test_plugin_error_index_has_rc_mkpl_section(self):
        text = (REPO_ROOT / "skills/fix-validation/references/plugin-error-index.md").read_text(encoding="utf-8")
        assert "20. validate_marketplace cross-validation rules" in text
        # Cross-link to the recipe file:
        assert "marketplace-upstream-drift.md" in text

    def test_skill_md_toc_parity(self):
        """SKILL.md must mirror every new heading verbatim (v2.80.0 rule)."""
        text = (REPO_ROOT / "skills/fix-validation/SKILL.md").read_text(encoding="utf-8")
        # New recipe file must be listed in Resources with the canonical
        # bullet-summary one-liner.
        assert "marketplace-upstream-drift.md" in text
        assert "RC-MKPL-NAME-MISMATCH" in text
        assert "RC-MKPL-UNKNOWN-FIELD" in text

    def test_marketplace_upstream_drift_recipe_exists(self):
        """The recipe file itself must exist and have the 8 documented §s."""
        path = REPO_ROOT / "skills/fix-validation/references/marketplace-upstream-drift.md"
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        for section_title in (
            "1. Name mismatch",
            "2. Version drift",
            "3. Unknown entry field",
            "4. Unknown source sub-field",
            "5. Source unreachable",
            "6. Description / author / keywords drift",
            "7. Per-batch bulk align",
            "8. Opt-out flags",
        ):
            assert section_title in text, f"Missing section: {section_title}"


class TestMainMenuRow7Doctor:
    """Phase E — main-menu row 7 must be Doctor (deep diagnostic)."""

    def test_main_menu_agent_has_row_7_doctor(self):
        text = (REPO_ROOT / "agents" / "cpv-main-menu-agent.md").read_text(encoding="utf-8")
        assert "Doctor (deep diagnostic)" in text
        # The 22-option doctor menu count must be referenced.
        assert "22-option" in text or "22 row" in text or "22-row" in text

    def test_menu_tree_has_section_3_7_doctor(self):
        text = (REPO_ROOT / "skills/cpv-main-menu-skill/references/menu-tree.md").read_text(encoding="utf-8")
        assert "### 3.7 Doctor (deep diagnostic)" in text
        # The renumbered ones must still exist
        assert "### 3.8 GitHub setup" in text
        assert "### 3.9 Deep semantic analysis" in text


class TestPublishGate0RejectsBypass:
    """Phase B.bypass — publish.py Gate 0 must reject CPV_SKIP_UPSTREAM_CROSS_CHECK."""

    def test_publish_py_rejects_upstream_bypass(self):
        text = (REPO_ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")
        assert "CPV_SKIP_UPSTREAM_CROSS_CHECK" in text
        # It must be listed in the `forbidden` block, not just mentioned.
        assert '"CPV_SKIP_UPSTREAM_CROSS_CHECK"' in text
