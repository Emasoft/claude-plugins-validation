"""Tests for Phase F of TRDD-c0ee9543 — agent pre-completion gate.

The plugin-creator and plugin-fixer agents MUST refuse to declare DONE on
a plugin/marketplace combo that fails the new Phase A + Phase B
cross-validation. (v2.90.0 TRDD-c50531c2: the cpv-upgrade-plugin slash
command was deleted along with 22 other redundant commands; the migration
flow now goes through the plugin-fixer agent directly, dispatched from
the cpv-main-menu top-level "Diagnose" category.)

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


class TestCpvUpgradePluginCommandDeletedInV290:
    """Phase F.3 — v2.90.0 TRDD-c50531c2: the `cpv-upgrade-plugin` slash
    command was deleted along with 22 other redundant user-facing commands.
    The migration flow now goes through `agents/plugin-fixer.md`
    directly, reached from the cpv-main-menu "Diagnose & Upgrade" leaf.

    These tests pin the new architectural location of the migration gate.
    """

    def test_cpv_upgrade_plugin_command_file_is_deleted(self):
        """The slash command file MUST stay deleted (TRDD-c50531c2 contract)."""
        path = REPO_ROOT / "commands" / "cpv-upgrade-plugin.md"
        assert not path.exists(), (
            f"{path} still exists. TRDD-c50531c2 (v2.90.0) deleted the "
            "cpv-upgrade-plugin slash command — the migration flow now goes "
            "through plugin-fixer agent dispatched from cpv-main-menu's "
            "'Diagnose & Upgrade' leaf. Re-creating the command would be a "
            "regression."
        )

    def test_plugin_fixer_agent_documents_marketplace_cross_check(self):
        """The migration gate moved from cpv-upgrade-plugin.md into the
        plugin-fixer agent (which is what the deleted command dispatched).
        """
        text = (REPO_ROOT / "agents" / "plugin-fixer.md").read_text(encoding="utf-8")
        assert "validate_marketplace.py" in text
        assert "RC-MKPL-NAME-MISMATCH" in text
        assert "TRDD-c0ee9543" in text

    def test_plugin_fixer_agent_references_ai_maestro_incident(self):
        """The migration gate's motivation citation moved into plugin-fixer.md."""
        text = (REPO_ROOT / "agents" / "plugin-fixer.md").read_text(encoding="utf-8")
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
        """The marketplace-upstream-drift recipe must remain reachable from the skill.

        v2.90.0 (TRDD-c50531c2): SKILL.md was trimmed to a thin overview
        (~66 lines). Per-error detail moved into `references/marketplace-error-index.md`
        and `references/plugin-error-index.md`, both of which the SKILL.md
        overview points at via the standard progressive-disclosure pattern.
        The drift-recipe file itself is referenced from those indices and
        from the agents that load this skill.
        """
        skill_text = (REPO_ROOT / "skills/fix-validation/SKILL.md").read_text(encoding="utf-8")
        mkt_index = (REPO_ROOT / "skills/fix-validation/references/marketplace-error-index.md").read_text(
            encoding="utf-8"
        )
        plugin_index = (REPO_ROOT / "skills/fix-validation/references/plugin-error-index.md").read_text(
            encoding="utf-8"
        )
        # SKILL.md must still mention its references/ folder so the
        # progressive-disclosure loader pulls them in.
        assert "references" in skill_text.lower() or "reference" in skill_text.lower(), (
            "fix-validation SKILL.md must reference its references/ folder "
            "so plugin-fixer can drill into per-error fix guides."
        )
        # The RC-MKPL detail lives in the per-error indices (where agents
        # actually drill down to).
        combined = mkt_index + plugin_index
        assert "marketplace-upstream-drift.md" in combined, (
            "fix-validation references/ must cite marketplace-upstream-drift.md "
            "in at least one error index."
        )
        assert "RC-MKPL-NAME-MISMATCH" in combined, (
            "RC-MKPL-NAME-MISMATCH must remain documented in fix-validation references."
        )
        assert "RC-MKPL-UNKNOWN-FIELD" in combined, (
            "RC-MKPL-UNKNOWN-FIELD must remain documented in fix-validation references."
        )

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


class TestMainMenuV290TopLevelCategories:
    """v2.90.0 (TRDD-c50531c2): canonical 8-category top-level menu replaces
    the prior Doctor-row design.

    Per TRDD-c50531c2 the 8 categories are:
        1. Validate
        2. Fix
        3. Optimize for Cache
        4. Diagnose
        5. Update
        6. Create
        7. Publish & Migrate
        8. Manage

    The former §3.7 "Doctor (deep diagnostic) — 22-option menu" was
    deleted; its options were absorbed into §3.1 (Validate) and §3.5
    (Manage), and the row was removed from the top-level table.
    """

    EXPECTED_CATEGORIES = (
        "Validate",
        "Fix",
        "Optimize for Cache",
        "Diagnose",
        "Update",
        "Create",
        "Publish & Migrate",
        "Manage",
    )

    def test_menu_tree_top_level_has_8_canonical_categories(self):
        text = (REPO_ROOT / "skills/cpv-main-menu-skill/references/menu-tree.md").read_text(encoding="utf-8")
        for category in self.EXPECTED_CATEGORIES:
            assert category in text, (
                f"v2.90.0 top-level menu must list '{category}' as one of the 8 "
                f"canonical categories per TRDD-c50531c2 §3.0."
            )

    def test_menu_tree_no_longer_has_doctor_deep_diagnostic_section(self):
        """The former §3.7 'Doctor (deep diagnostic)' heading MUST stay deleted."""
        text = (REPO_ROOT / "skills/cpv-main-menu-skill/references/menu-tree.md").read_text(encoding="utf-8")
        assert "### 3.7 Doctor (deep diagnostic)" not in text, (
            "The §3.7 'Doctor (deep diagnostic) — 22-option menu' section was "
            "deleted in v2.90.0 (TRDD-c50531c2). Its options moved to §3.1 and §3.5."
        )

    def test_menu_tree_publish_migrate_routes_to_old_github_setup(self):
        """The §3.7 anchor is now reused for the renamed 'GitHub setup' sub-menu
        that the new top-level 'Publish & Migrate' row routes to."""
        text = (REPO_ROOT / "skills/cpv-main-menu-skill/references/menu-tree.md").read_text(encoding="utf-8")
        # §3.7 is now the GitHub setup / Publish & Migrate sub-menu.
        assert "### 3.7 GitHub setup" in text, (
            "§3.7 was repurposed in v2.90.0 — it is now the 'GitHub setup' "
            "sub-menu reached via the new top-level 'Publish & Migrate' row."
        )
        # §3.8 Deep semantic analysis is unchanged (still reached via Diagnose).
        assert "### 3.8 Deep semantic analysis" in text


class TestPublishGate0RejectsBypass:
    """Phase B.bypass — publish.py Gate 0 must reject CPV_SKIP_UPSTREAM_CROSS_CHECK.

    v2.86.0 (issue #22): the bypass-guard now uses prefix-pattern matching
    instead of a fixed forbidden allowlist. CPV_SKIP_UPSTREAM_CROSS_CHECK
    is matched implicitly by the ``CPV_SKIP_`` prefix entry.
    """

    def test_publish_py_rejects_upstream_bypass(self):
        text = (REPO_ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")
        # The prefix-pattern bypass-guard catches every CPV_SKIP_* env var,
        # including CPV_SKIP_UPSTREAM_CROSS_CHECK. Look for the prefix entry
        # and the exemption block that documents the two reads-only escape
        # hatches (CPV_SKIP_GITHUB_INTEGRITY / CPV_SKIP_GH_AUTH_CHECK).
        assert '"CPV_SKIP_"' in text, "CPV_SKIP_ prefix entry missing from bypass-guard"
        # And the docstring should still mention the upstream-cross-check
        # case for greppability.
        assert "CPV_SKIP_UPSTREAM_CROSS_CHECK" in text
