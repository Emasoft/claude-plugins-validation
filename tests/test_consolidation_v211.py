#!/usr/bin/env python3
"""Tests for command consolidation (v2.8.0 → v2.90.0).

v2.90.0 (TRDD-c50531c2 — menu unification) is the latest re-shape:
- Only ONE user-visible slash command remains: `/cpv-main-menu`.
- The 23 user-facing slash commands and 14 component-creation commands
  were either deleted (the 23) or converted to `user-invocable: false`
  skills (the 14). All workflows are now routed through the main menu.

Validates that:
- Exactly 1 command exists in commands/ (`cpv-main-menu.md`).
- That command runs INLINE in the main session (no subagent fork).
- The 23 deleted user-facing commands are no longer in commands/.
- The 14 commands-turned-skills no longer exist in commands/.
- Old obsolete commands (from earlier waves) are also still gone.
- Archived commands are in scripts_dev/commands_archive/ (local only).
- Canonical-pipeline skill has correct frontmatter.
- Every skill is loaded by at least one agent OR slash-command body
  — with explicit exemptions for skills awaiting wiring waves.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = PROJECT_ROOT / "commands"
SKILLS_DIR = PROJECT_ROOT / "skills"
ARCHIVE_DIR = PROJECT_ROOT / "scripts_dev" / "commands_archive"


def _parse_frontmatter(path: Path) -> dict | None:
    """Parse YAML frontmatter from a markdown file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    result: dict = yaml.safe_load(parts[1])
    return result


# --- Direct-script commands (no agent field) ---
#
# v2.90.0 (TRDD-c50531c2 — menu unification): ALL direct-script commands
# were deleted. The 23 user-facing slash commands either disappear (their
# functionality lives in agents already dispatched via /cpv-main-menu) or
# converted into `user-invocable: false` skills (the 14 component-creation
# commands). The set is therefore empty.

DIRECT_SCRIPT_COMMANDS: list[str] = []

# --- Agent commands (commands with an `agent:` field) ---
#
# Post-de-fork: NO command delegates to an agent. `/cpv-main-menu` now runs
# INLINE in the main session (its frontmatter carries no `agent:`/`context:`),
# and `cpv-main-menu-agent` was deleted. The fork was a stalled-migration
# leftover the project's own CA-07 rule flags — it cold-re-primed the prompt
# cache for zero benefit, since the claude-menu-system Stop hook already renders
# the menu. Deliberate design change, not drift. The set is therefore empty,
# like DIRECT_SCRIPT_COMMANDS above.

AGENT_COMMANDS: dict[str, str] = {}

# --- Commands deleted in v2.90.0 (TRDD-c50531c2 menu unification) ---
#
# 23 user-facing slash commands were deleted because their functionality is
# now routed through `/cpv-main-menu` → existing agents (cpv-plugin-validator-agent,
# cpv-plugin-fixer-agent, cpv-plugin-creator-agent, cpv-plugin-manager-agent, cpv-cache-optimizer-agent,
# cpv-doctor-agent, cpv-semantic-validator-agent, cpv-marketplace-fixer-agent, etc.).
V290_DELETED_USER_COMMANDS = [
    "cpv-validate-plugin",
    "cpv-validate-skill",
    "cpv-validate-local-scope",
    "cpv-validate-project-scope",
    "cpv-validate-cache",
    "cpv-validate-github-plugin",
    "cpv-validate-github-marketplace",
    "cpv-validate-settings-marketplace",
    "cpv-validate-telemetry",
    "cpv-fix-validation",
    "cpv-fix-marketplace-validation",
    "cpv-semantic-validation",
    "cpv-create",
    "cpv-list-plugins",
    "cpv-manage",
    "cpv-diagnose-plugin",
    "cpv-validate",
    "cpv-doctor",
    "cpv-cache-optimize",
    "cpv-upgrade-plugin",
    "cpv-setup-branch-rules",
    "cpv-setup-branch-rules-generic",
    "cpv-migrate-marketplace",
]

# --- Commands converted to `user-invocable: false` skills in v2.90.0 ---
#
# 14 component-creation / utility commands were converted into skills loaded
# only by agents (or by the main-menu skill's references). The cpv-XXX file
# in commands/ is GONE; the skill at skills/<new-name>/SKILL.md is its
# replacement. Mapping: command (old) → skill (new).
V290_COMMAND_TO_SKILL = {
    "cpv-add-component": "cpv-add-component-to-plugin",
    "cpv-add-dependency": "cpv-add-dependency",
    "cpv-bump-version": "cpv-bump-version",
    "cpv-codemod": "cpv-deterministic-codemod",
    "cpv-create-agent": "cpv-scaffold-agent",
    "cpv-create-command": "cpv-scaffold-command",
    "cpv-create-hook": "cpv-add-hook",
    "cpv-create-mcp": "cpv-register-mcp",
    "cpv-create-skill": "cpv-scaffold-skill",
    "cpv-link-plugin": "cpv-link-plugin-marketplace",
    "cpv-pack-components": "cpv-pack-components",
    "cpv-refresh-readme": "cpv-refresh-readme",
    "cpv-strip-dev-parts": "cpv-strip-dev-submodules",
    "cpv-version": "cpv-show-version",
}


class TestCommandCount:
    """Verify total command count after the v2.90.0 menu unification."""

    def test_total_command_count(self):
        """commands/ directory must contain ONLY the documented entry points.

        Per TRDD-c50531c2 (v2.90.0), the 23 user-facing slash commands
        and the 14 component-creation commands were either deleted or
        converted to ``user-invocable: false`` skills. Only ``cpv-main-menu.md``
        remains as the discovery surface.

        Per TRDD-71e68ab5 (v2.91.0), ``cpv-batch-fix.md`` was added as a
        SECOND direct-entry slash command — but only because it is a
        power-user surface the doctor recommends by exact name when it
        finds 100+ findings. Burying it behind 2-3 menu clicks would make
        the doctor's recommendation user-hostile.

        Per TRDD-9dd64dbf (v2.95.0), ``cpv-the-skills-menu-create.md`` was
        added as a THIRD direct-entry slash command. It is the public
        face of the universal migrator — users of OTHER plugins need a
        bare slash command to retrofit their plugin to cpv-the-skills-menu
        method, so menu navigation is not acceptable here.

        Per TRDD-84525d4a (v2.99.1), ``cpv-pre-install-scan.md`` was
        added as a FOURTH direct-entry slash command. The pre-install
        gate must be invokable BEFORE the user opens any menu — the
        whole point is to scan untrusted plugins before they touch
        the cache. Menu navigation defeats the pre-install timing
        guarantee, so a bare slash command is the only honest UX.

        Any fifth command requires a new TRDD documenting why menu
        unification is broken for that case.
        """
        allowed = {
            "cpv-main-menu.md",
            "cpv-batch-fix.md",
            "cpv-the-skills-menu-create.md",
            "cpv-pre-install-scan.md",
            # TRDD-3dcbb37c (v2.101.0) — Batch skills family. Each one is a
            # bare slash command because the user wants to fan out fleet-wide
            # operations directly (marketplace URL / list / @listfile inputs),
            # not navigate through menu layers per plugin.
            "cpv-batch-validate.md",
            "cpv-batch-security-audit.md",
            "cpv-batch-caching-audit.md",
            "cpv-batch-caching-optimize.md",
            # Phase 3 same-turn variants (TRDD-3dcbb37c §3) — single-pass
            # read + scan + verify-FPs + fix in ONE per-plugin agent turn.
            "cpv-batch-validate-and-fix.md",
            "cpv-batch-full-scan-and-fix.md",
            # Phase 4 scope-aware doctor batch (TRDD-a175f78d) — LOCAL-only
            # per-project diagnostic (user/project/local/full).
            "cpv-batch-scope-diagnose.md",
            "cpv-batch-scope-fix.md",
            "cpv-batch-scope-diagnose-and-fix.md",
            # Direct free-form entry to the general-purpose cpv-agent worker
            # (user directive 2026-07-23) — `/cpv-agent <request>` reaches the
            # worker directly, the same target as the menu's `A — Ask the agent`
            # row, so a bare slash command is the honest UX.
            "cpv-agent.md",
        }
        md_files = list(COMMANDS_DIR.glob("*.md"))
        actual = {f.name for f in md_files}
        unexpected = actual - allowed
        missing = allowed - actual
        assert not unexpected and not missing, (
            f"commands/ must contain exactly {sorted(allowed)} (TRDD-c50531c2 + TRDD-71e68ab5 + TRDD-9dd64dbf). "
            f"Unexpected: {sorted(unexpected)}. Missing: {sorted(missing)}."
        )

    def test_only_remaining_command_is_cpv_main_menu_or_batch_fix(self):
        """The four surviving commands MUST be in the allowlist."""
        allowed = {
            "cpv-main-menu.md",
            "cpv-batch-fix.md",
            "cpv-the-skills-menu-create.md",
            "cpv-pre-install-scan.md",
            # TRDD-3dcbb37c (v2.101.0) — Batch skills family. Each one is a
            # bare slash command because the user wants to fan out fleet-wide
            # operations directly (marketplace URL / list / @listfile inputs),
            # not navigate through menu layers per plugin.
            "cpv-batch-validate.md",
            "cpv-batch-security-audit.md",
            "cpv-batch-caching-audit.md",
            "cpv-batch-caching-optimize.md",
            # Phase 3 same-turn variants (TRDD-3dcbb37c §3) — single-pass
            # read + scan + verify-FPs + fix in ONE per-plugin agent turn.
            "cpv-batch-validate-and-fix.md",
            "cpv-batch-full-scan-and-fix.md",
            # Phase 4 scope-aware doctor batch (TRDD-a175f78d) — LOCAL-only.
            "cpv-batch-scope-diagnose.md",
            "cpv-batch-scope-fix.md",
            "cpv-batch-scope-diagnose-and-fix.md",
            # Direct free-form entry to the general-purpose cpv-agent worker
            # (user directive 2026-07-23) — user-facing `/cpv-agent <request>`.
            "cpv-agent.md",
        }
        md_files = list(COMMANDS_DIR.glob("*.md"))
        for f in md_files:
            assert f.name in allowed, f"Unexpected command file {f.name}; allowed: {sorted(allowed)}"


class TestDirectScriptCommands:
    """Verify direct-script commands have all been removed (v2.90.0).

    Pre-v2.90.0 the project shipped many direct-script (`agent: <absent>`)
    slash commands. Per TRDD-c50531c2 they were all deleted in favor of
    routing through `/cpv-main-menu` → existing agents.
    """

    def test_no_direct_script_commands_remain(self):
        """The DIRECT_SCRIPT_COMMANDS allowlist is empty in v2.90.0."""
        assert DIRECT_SCRIPT_COMMANDS == [], (
            "Per TRDD-c50531c2 (v2.90.0) every direct-script slash command "
            "was deleted. DIRECT_SCRIPT_COMMANDS must remain empty so a "
            "future regression that re-introduces one is caught here."
        )


class TestAgentCommands:
    """Verify the single surviving agent command (v2.90.0)."""

    def test_all_agent_commands_exist(self):
        """The single agent command (`cpv-main-menu`) must exist."""
        for name in AGENT_COMMANDS:
            assert (COMMANDS_DIR / f"{name}.md").is_file(), f"{name}.md missing"

    def test_agent_commands_have_correct_agent(self):
        """Each agent command must delegate to the correct agent."""
        for name, expected_agent in AGENT_COMMANDS.items():
            fm = _parse_frontmatter(COMMANDS_DIR / f"{name}.md")
            assert fm is not None, f"{name}.md has no frontmatter"
            assert fm.get("agent") == expected_agent, (
                f"{name}.md: expected agent: {expected_agent}, got {fm.get('agent')}"
            )


class TestObsoleteCommandsRemoved:
    """Verify obsolete commands no longer exist in commands/."""

    def test_old_individual_validators_removed(self):
        """Individual validator commands should be consolidated into cpv-validate."""
        for name in [
            "cpv-validate-hooks",
            "cpv-validate-agents",
            "cpv-validate-command",
            "cpv-validate-security",
            "cpv-validate-scoring",
            "cpv-validate-marketplace",
            "cpv-validate-enterprise",
            "cpv-validate-mcp",
            "cpv-validate-lsp",
            "cpv-validate-documentation",
            "cpv-validate-encoding",
            "cpv-validate-rules",
            "cpv-validate-xref",
        ]:
            assert not (COMMANDS_DIR / f"{name}.md").exists(), f"{name}.md should be archived"

    def test_old_management_commands_removed(self):
        """Individual management commands should be consolidated into cpv-manage."""
        for name in [
            "cpv-install-plugin-from-local-mp",
            "cpv-uninstall-plugin-from-local-mp",
            "cpv-update-plugin",
            "cpv-manage-remote-plugins",
            "cpv-enable-plugin",
            "cpv-disable-plugin",
            "cpv-list-mp-plugins",
            "cpv-search-plugins",
            "cpv-manage-marketplaces",
        ]:
            assert not (COMMANDS_DIR / f"{name}.md").exists(), f"{name}.md should be archived"

    def test_old_creation_commands_removed(self):
        """Individual creation commands should be consolidated into cpv-create."""
        for name in [
            "cpv-create-local-plugin",
            "cpv-create-local-marketplace",
            "cpv-publish-a-plugin-as-github-repo",
            "cpv-create-a-github-marketplace",
            "cpv-publish-a-plugin-to-a-github-marketplace",
            "cpv-standardize",
        ]:
            assert not (COMMANDS_DIR / f"{name}.md").exists(), f"{name}.md should be archived"

    def test_v290_deleted_user_commands_are_gone(self):
        """The 23 user-facing slash commands deleted in v2.90.0 (TRDD-c50531c2)
        must NOT exist in commands/.

        Their functionality is now routed through `/cpv-main-menu` → the
        existing agent for the workflow (cpv-plugin-validator-agent, cpv-plugin-fixer-agent,
        cpv-plugin-creator-agent, cpv-plugin-manager-agent, cpv-cache-optimizer-agent,
        cpv-doctor-agent, cpv-semantic-validator-agent, cpv-marketplace-fixer-agent, etc.).
        """
        for name in V290_DELETED_USER_COMMANDS:
            assert not (COMMANDS_DIR / f"{name}.md").exists(), (
                f"{name}.md must NOT exist — deleted in v2.90.0 per "
                f"TRDD-c50531c2 (functionality routed via /cpv-main-menu)."
            )

    def test_v290_command_to_skill_conversions_are_gone_from_commands(self):
        """The 14 commands converted to `user-invocable: false` skills in
        v2.90.0 (TRDD-c50531c2) must NOT exist in commands/.

        For each entry, the cpv-XXX file is GONE; the skill at
        skills/<new-name>/SKILL.md is its replacement.
        """
        for cmd_name, skill_name in V290_COMMAND_TO_SKILL.items():
            assert not (COMMANDS_DIR / f"{cmd_name}.md").exists(), (
                f"{cmd_name}.md must NOT exist — converted to "
                f"skills/{skill_name}/SKILL.md in v2.90.0 per TRDD-c50531c2."
            )
            assert (SKILLS_DIR / skill_name / "SKILL.md").is_file(), (
                f"skills/{skill_name}/SKILL.md must exist — it is the "
                f"replacement for the deleted {cmd_name}.md command "
                f"(v2.90.0 per TRDD-c50531c2)."
            )


class TestArchivedCommands:
    """Verify archived commands are in scripts_dev/commands_archive/ (local only).

    scripts_dev/ is gitignored — these tests only run locally where the
    archive exists. In CI (clean clone), they are skipped automatically.
    """

    def test_archive_directory_exists(self):
        """scripts_dev/commands_archive/ must exist locally (skipped in CI)."""
        if not ARCHIVE_DIR.is_dir():
            pytest.skip("scripts_dev/commands_archive/ not present (gitignored, CI environment)")
        assert ARCHIVE_DIR.is_dir()

    def test_archived_commands_count(self):
        """Archive should contain the 28 moved commands (skipped in CI)."""
        if not ARCHIVE_DIR.is_dir():
            pytest.skip("scripts_dev/commands_archive/ not present (gitignored, CI environment)")
        archived = list(ARCHIVE_DIR.glob("*.md"))
        assert len(archived) >= 25, f"Expected 25+ archived commands, found {len(archived)}"


class TestCanonicalPipelineSkill:
    """Verify the cpv-canonical-pipeline skill."""

    def test_skill_directory_exists(self):
        """skills/cpv-canonical-pipeline/ directory must exist."""
        assert (SKILLS_DIR / "cpv-canonical-pipeline").is_dir()

    def test_skill_md_has_name_field(self):
        """cpv-canonical-pipeline/SKILL.md frontmatter must have name: cpv-canonical-pipeline."""
        fm = _parse_frontmatter(SKILLS_DIR / "cpv-canonical-pipeline" / "SKILL.md")
        assert fm is not None
        assert fm.get("name") == "cpv-canonical-pipeline"


AGENTS_DIR = PROJECT_ROOT / "agents"


class TestSkillAgentArchitecture:
    """Enforce the agent-first architecture invariants.

    All skills must be non-user-invocable and loaded only by agents.
    Every "Loaded by X agent" claim in a skill description must be
    backed by an actual agent that lists the skill in its frontmatter.
    """

    def test_all_skills_are_non_user_invocable(self):
        """Every SKILL.md must have user-invocable: false.

        Exceptions (user-invocable: true): ``cpv-the-skills-menu`` is the
        universal agent-facing router (the agent counterpart to the human
        /cpv-main-menu — see agents/cpv.md); ``cpv-the-skills-menu-create`` is the
        universal migrator that converts arbitrary plugins to cpv-the-skills-menu
        method. Both MUST stay user-invocable so authors/agents can trigger
        them directly (the migration target of -create is the OTHER plugin,
        not this one). The batch family below is user-invocable for the same
        direct-invocation reason.
        """
        user_invocable_exemptions = {
            # The universal agent-facing router — the agent counterpart to the
            # human-facing /cpv-main-menu. Flipped to user-invocable so any
            # Claude can be told "read the CPV skills menu and use whatever you
            # need" and have it auto-trigger and route the request. Companion:
            # agents/cpv.md. Deliberate evolution of v2.90.0 menu-unification
            # (TRDD-c50531c2), NOT drift — do NOT revert to user-invocable: false.
            "cpv-the-skills-menu",
            "cpv-the-skills-menu-create",
            # TRDD-3dcbb37c (v2.101.0) — Batch-skills family. Users invoke
            # these directly (`/cpv-batch-validate Emasoft/emasoft-plugins`),
            # so the SKILL.md MUST be user-invocable. The companion
            # `commands/cpv-batch-*.md` orchestrator body is the actual entry
            # point; the SKILL.md is the discoverability surface for
            # cpv-the-skills-menu and natural-language invocation.
            "cpv-batch-validate",
            "cpv-batch-security-audit",
            "cpv-batch-caching-audit",
            "cpv-batch-caching-optimize",
            "cpv-batch-fix",
            # Phase 3 same-turn variants.
            "cpv-batch-validate-and-fix",
            "cpv-batch-full-scan-and-fix",
            # Phase 4 scope-aware doctor batch.
            "cpv-batch-scope-diagnose",
            "cpv-batch-scope-fix",
            "cpv-batch-scope-diagnose-and-fix",
            # EXPERIMENTAL agent-architecture generators (user directive 2026-07-22:
            # "create those 2 skills", invoked as `/cpv-create-...`). Deliberately
            # user-invocable — the user explicitly asked for direct slash-command
            # invocation, the sanctioned override of the single-visible-command
            # convention (they are wired into cpv-the-skills-menu, not orphans). NOT drift.
            "cpv-create-mono-agent",
            "cpv-create-micro-agents-workflow",
        }
        for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
            fm = _parse_frontmatter(skill_md)
            assert fm is not None, f"{skill_md} has no frontmatter"
            skill_name = skill_md.parent.name
            if skill_name in user_invocable_exemptions:
                assert fm.get("user-invocable") is True, f"{skill_name}: must be user-invocable: true (migration tool)"
                continue
            assert fm.get("user-invocable") is False, (
                f"{skill_name}: user-invocable must be false, got {fm.get('user-invocable')!r}"
            )

    def test_all_agents_declare_skills_list(self):
        """Every agent must have a skills: list in its frontmatter.

        v2.89.0 (TRDD-bcbceeed): the four `*-menu` haiku dispatcher subagents
        previously exempted from this rule were deleted entirely (subagents
        cannot spawn other subagents per the current Claude Code spec — the
        slash-command body is now the menu orchestrator). The exemption set
        is therefore empty; every remaining agent must declare a non-empty
        skills list.
        """
        for agent_md in AGENTS_DIR.glob("*.md"):
            fm = _parse_frontmatter(agent_md)
            assert fm is not None, f"{agent_md} has no frontmatter"
            skills = fm.get("skills")
            assert isinstance(skills, list) and skills, (
                f"{agent_md.name}: skills must be a non-empty list, got {skills!r}"
            )

    def test_every_skill_is_loaded_by_at_least_one_agent(self):
        """Each skill must be referenced by at least one loader — any of:

        1. An agent's frontmatter ``skills:`` list (the traditional path).
        2. A slash-command body via the Skill tool with the fully-qualified
           ``claude-plugins-validation:<skill-name>`` form (the v2.89.4
           context-fork pattern introduced by TRDD-3ce2f864).
        3. An AGENT body via the same fully-qualified Skill invocation
           (TRDD-14cc93a6 v2.91.1 decoupled-routing pattern — agents pick
           skills dynamically at runtime via the Skill tool).
        4. ANOTHER skill's body via the same fully-qualified invocation
           (used by routing skills to fan out to sub-skills).
        """
        # Skills that are intentionally orphaned during a multi-wave TRDD
        # landing. Each entry MUST cite the TRDD that authored it and the
        # subsequent wave that wires it. Remove the entry the moment the
        # wiring wave lands.
        pending_wave_b_wiring = {
            # TRDD-962fdc55 Wave 7-A creates this skill; Wave 7-B (queued
            # behind TRDD-c0ee9543) wires cpv-plugin-creator-agent / cpv-plugin-fixer-agent /
            # cpv-marketplace-fixer-agent to load it.
            "cpv-marketplace-authoring-contract",
            # (cpv-format-menu was safe-deleted in TRDD-4de479a0 Phase 4 —
            # menu rendering moved to the externalised claude-menu-system
            # Stop hook via scripts/cpv_menu.py; no orphan to allowlist.)
            # TRDD-c50531c2 (v2.90.0 menu unification) created these 14
            # skills as replacements for the deleted commands of the same
            # role. The orchestrator wiring (which agent loads which skill)
            # is a follow-up wave — until then these are intentionally
            # orphaned. Each maps 1:1 to a former cpv-XXX command:
            #   cpv-add-component-to-plugin ← cpv-add-component
            #   cpv-add-dependency           ← cpv-add-dependency
            #   cpv-add-hook                 ← cpv-create-hook
            #   cpv-bump-version             ← cpv-bump-version
            #   cpv-deterministic-codemod    ← cpv-codemod
            #   cpv-link-plugin-marketplace  ← cpv-link-plugin
            #   cpv-pack-components          ← cpv-pack-components
            #   cpv-refresh-readme           ← cpv-refresh-readme
            #   cpv-register-mcp             ← cpv-create-mcp
            #   cpv-scaffold-agent           ← cpv-create-agent
            #   cpv-scaffold-command         ← cpv-create-command
            #   cpv-scaffold-skill           ← cpv-create-skill
            #   cpv-show-version             ← cpv-version
            #   cpv-strip-dev-submodules     ← cpv-strip-dev-parts
            "cpv-add-component-to-plugin",
            "cpv-add-dependency",
            "cpv-add-hook",
            "cpv-bump-version",
            "cpv-deterministic-codemod",
            "cpv-link-plugin-marketplace",
            "cpv-pack-components",
            "cpv-refresh-readme",
            "cpv-register-mcp",
            "cpv-scaffold-agent",
            "cpv-scaffold-command",
            "cpv-scaffold-skill",
            "cpv-show-version",
            "cpv-strip-dev-submodules",
            # TRDD-3dcbb37c (v2.101.0) — Batch-skills family. These are
            # ``user-invocable: true`` slash-command-equivalent skills the
            # user triggers directly (no agent loader). The matching
            # ``commands/cpv-batch-*.md`` files contain the orchestrator
            # bodies; the SKILL.md is the discoverability surface for
            # ``cpv-the-skills-menu`` and natural-language invocation.
            "cpv-batch-validate",
            "cpv-batch-security-audit",
            "cpv-batch-caching-audit",
            "cpv-batch-caching-optimize",
            "cpv-batch-fix",
            # Phase 3 same-turn variants.
            "cpv-batch-validate-and-fix",
            "cpv-batch-full-scan-and-fix",
            # Phase 4 scope-aware doctor batch.
            "cpv-batch-scope-diagnose",
            "cpv-batch-scope-fix",
            "cpv-batch-scope-diagnose-and-fix",
        }
        # Gather all skill names declared by agents.
        loaded = set()
        for agent_md in AGENTS_DIR.glob("*.md"):
            fm = _parse_frontmatter(agent_md) or {}
            for s in fm.get("skills", []) or []:
                loaded.add(s)
        # ALSO gather skills invoked by slash-command bodies via the Skill
        # tool. Historically this caught the v2.89.4 ``cpv-format-menu``
        # fork-skill (TRDD-3ce2f864), loaded only by the four (now-deleted)
        # orchestrator commands — that skill itself was safe-deleted in
        # TRDD-4de479a0 Phase 4 alongside the renderer it wrapped, but
        # other command-loaded skills (e.g. the batch family) still rely
        # on this code path. The fully-qualified form
        # ``skill: "claude-plugins-validation:<name>"`` is the unambiguous
        # marker — bare prose mentions ("this skill", "fork-skill:")
        # don't false-match.
        import re as _re

        # Universal regex matching `skill: "claude-plugins-validation:<name>"`
        # OR `Skill({skill: "claude-plugins-validation:<name>"})` patterns.
        # Bare prose mentions (e.g. "use the cpv-fix-validation skill") don't
        # false-match because the fully-qualified form requires the plugin
        # prefix + quotes.
        skill_invocation_re = _re.compile(
            r'skill:\s*"claude-plugins-validation:([a-z0-9_-]+)"',
        )

        # Path 2: slash-command bodies (v2.89.4 fork-skill pattern)
        commands_dir = SKILLS_DIR.parent / "commands"
        if commands_dir.is_dir():
            for cmd_md in commands_dir.glob("*.md"):
                body = cmd_md.read_text(encoding="utf-8")
                for m in skill_invocation_re.finditer(body):
                    loaded.add(m.group(1))

        # Path 3: agent BODIES (v2.91.1 decoupled-routing pattern,
        # TRDD-14cc93a6). Agents dynamically pick skills at runtime; a
        # fully-qualified Skill invocation in the body counts as loading
        # the same way an entry in `skills:` does.
        for agent_md in AGENTS_DIR.glob("*.md"):
            body = agent_md.read_text(encoding="utf-8")
            for m in skill_invocation_re.finditer(body):
                loaded.add(m.group(1))

        # Path 4: other skills' bodies AND their reference files
        # (cross-references between skills, e.g. a routing skill that fans
        # out to sub-skills, or a catalog skill whose references/ folder
        # holds the per-skill invocation list — TRDD-478d9687 universal
        # cpv-the-skills-menu pattern).
        for sk_dir in SKILLS_DIR.iterdir():
            if not sk_dir.is_dir():
                continue
            # Scan SKILL.md
            sk_md = sk_dir / "SKILL.md"
            if sk_md.exists():
                body = sk_md.read_text(encoding="utf-8")
                for m in skill_invocation_re.finditer(body):
                    loaded.add(m.group(1))
            # Scan every reference .md file inside the skill (recurse one level)
            ref_dir = sk_dir / "references"
            if ref_dir.is_dir():
                for ref_md in ref_dir.rglob("*.md"):
                    body = ref_md.read_text(encoding="utf-8")
                    for m in skill_invocation_re.finditer(body):
                        loaded.add(m.group(1))
        # Every skill directory must appear in some loader's list/body.
        for skill_dir in SKILLS_DIR.iterdir():
            if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
                continue
            if skill_dir.name in pending_wave_b_wiring:
                continue
            assert skill_dir.name in loaded, (
                f"Skill {skill_dir.name} is not loaded by any agent OR "
                f"slash-command — orphaned skill. Either add it to an "
                f"agent's `skills:` list, OR invoke it from a command "
                f'body via `Skill({{skill: "claude-plugins-validation:'
                f'{skill_dir.name}", ...}})` (the v2.89.4 context-fork '
                f"pattern from TRDD-3ce2f864)."
            )

    def test_loaded_by_claims_match_actual_agents(self):
        """Each 'Loaded by X agent' claim in a skill must map to an agent that loads it."""
        import re

        loaded_by_pattern = re.compile(r"Loaded by ([a-z0-9, -]+?) agents?\b", re.IGNORECASE)
        # Build reverse index: skill -> set(agents that load it)
        skill_to_agents: dict[str, set[str]] = {}
        for agent_md in AGENTS_DIR.glob("*.md"):
            fm = _parse_frontmatter(agent_md) or {}
            agent_name = fm.get("name") or agent_md.stem
            for s in fm.get("skills", []) or []:
                skill_to_agents.setdefault(s, set()).add(agent_name)
        # For each skill, check its description claim
        for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
            fm = _parse_frontmatter(skill_md)
            if not fm:
                continue
            desc = fm.get("description", "")
            if isinstance(desc, list):
                desc = " ".join(desc)
            match = loaded_by_pattern.search(desc or "")
            if not match:
                continue
            claimed_raw = match.group(1)
            claimed = {
                c.strip().removesuffix(" and").strip()
                for c in claimed_raw.replace(",", " ").replace(" and ", " ").split()
                if c.strip()
            }
            skill_name = skill_md.parent.name
            actual = skill_to_agents.get(skill_name, set())
            missing = claimed - actual
            assert not missing, (
                f"{skill_name}: description claims 'Loaded by {claimed_raw}' "
                f"but these agents do not list the skill: {sorted(missing)}. "
                f"Actual loaders: {sorted(actual)}"
            )
