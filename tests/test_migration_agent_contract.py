"""Tests for the canonical-pipeline migration agent's exit contract (issue #21 ask #1).

These are regression tests for the plugin-fixer agent's body and the
related skills/commands. They guard the contract:

> Migration is NOT complete until (a) every BLOCKER/MAJOR check in
> references/canonical-pipeline-migration-checklist.md passes, AND
> (b) a real publish.py + gh run watch reports green CI on the resulting
> tag, AND (c) if the plugin is in a Layout-C marketplace OR registered in
> any external marketplace, the marketplace's own publish.py also reports
> green CI on its tag.

The tests are deliberately textual — they read agents/, skills/, and
commands/ markdown directly and assert the contract is encoded there.
The agent itself runs the contract; these tests just guarantee the
contract wording does not silently rot.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_FILE = REPO_ROOT / "agents" / "plugin-fixer.md"
COMMAND_FILE = REPO_ROOT / "commands" / "cpv-upgrade-plugin.md"
CANONICAL_PIPELINE_SKILL = REPO_ROOT / "skills" / "canonical-pipeline" / "SKILL.md"
FIX_VALIDATION_SKILL = REPO_ROOT / "skills" / "fix-validation" / "SKILL.md"
STANDARDIZE_PLUGIN_SKILL = REPO_ROOT / "skills" / "standardize-plugin" / "SKILL.md"
ITERATIVE_FIX_LOOP = REPO_ROOT / "skills" / "fix-validation" / "references" / "iterative-fix-loop.md"
CHECKLIST_FILE = REPO_ROOT / "references" / "canonical-pipeline-migration-checklist.md"


# ── Required-existence guards ─────────────────────────────────────────────────


def test_checklist_reference_file_exists() -> None:
    """The 82-check checklist file MUST exist — the agent body links to it."""
    assert CHECKLIST_FILE.exists(), (
        f"Checklist not found at {CHECKLIST_FILE} — agent migration contract "
        f"references this file by absolute path. Recreate it before the agent "
        f"will function."
    )


def test_plugin_fixer_agent_file_exists() -> None:
    """The migration agent file MUST exist — /cpv-upgrade-plugin dispatches it."""
    assert AGENT_FILE.exists(), f"plugin-fixer agent not found at {AGENT_FILE}"


# ── Contract: agent body references the checklist ────────────────────────────


def test_agent_references_canonical_pipeline_migration_checklist() -> None:
    """Agent body MUST explicitly reference references/canonical-pipeline-migration-checklist.md.

    Without this reference, the agent has no way to find the 82-check
    matrix, and the migration contract would silently revert to the old
    validator-only gate.
    """
    body = AGENT_FILE.read_text()
    assert "canonical-pipeline-migration-checklist.md" in body, (
        "agents/plugin-fixer.md must reference the 82-check checklist "
        "(references/canonical-pipeline-migration-checklist.md) — without "
        "this link the agent cannot locate the post-migration verification "
        "matrix and silently regresses to the legacy validator-only gate."
    )


def test_agent_has_pre_completion_verification_section() -> None:
    """Agent body MUST contain the new "Pre-completion verification (REQUIRED)" section."""
    body = AGENT_FILE.read_text()
    assert re.search(r"## Pre-completion verification \(REQUIRED\)", body), (
        "agents/plugin-fixer.md must contain a section titled "
        "'Pre-completion verification (REQUIRED)' that lists, in order, "
        "the bash commands the agent runs after the regular fix loop "
        "(run_all_checks → publish.py --print-gates → --dry-run → --patch "
        "→ gh run watch)."
    )


def test_agent_runs_all_82_checks_via_run_all_checks() -> None:
    """Agent body MUST invoke run_all_checks (the function defined in the checklist)."""
    body = AGENT_FILE.read_text()
    assert "run_all_checks" in body, (
        "agents/plugin-fixer.md must invoke run_all_checks (the bash "
        "function that executes the 82-check matrix from "
        "canonical-pipeline-migration-checklist.md and emits a "
        "Unicode-bordered Markdown table)."
    )


def test_agent_invokes_gh_run_watch() -> None:
    """Agent body MUST invoke `gh run watch --exit-status` on the resulting CI run."""
    body = AGENT_FILE.read_text()
    assert "gh run watch" in body, (
        "agents/plugin-fixer.md must invoke `gh run watch` to verify CI "
        "is green on the resulting tag — the user's exit contract is "
        "'CI passes on next push'."
    )
    assert "--exit-status" in body, (
        "agents/plugin-fixer.md must use `gh run watch --exit-status` "
        "(without the flag, the command exits 0 even if the run failed)."
    )


def test_agent_runs_real_publish_patch() -> None:
    """Agent body MUST invoke a real `publish.py --patch` (not just --dry-run)."""
    body = AGENT_FILE.read_text()
    assert "publish.py --patch" in body, (
        "agents/plugin-fixer.md must invoke `publish.py --patch` "
        "(or --minor/--major) to actually push the new tag — a "
        "--dry-run alone would not exercise the CI run."
    )


# ── Contract: agent does NOT silently --force-templates ───────────────────────


def test_agent_warns_about_force_templates_data_loss() -> None:
    """Agent body MUST explicitly warn that --force-templates loses customisations.

    The user explicitly asked: "agent DOES NOT silently --force-templates
    when checks fail. Instead: present the per-CHECK failure list and ask
    the user whether to (a) fix manually, (b) re-run with --force-templates
    (with explicit warning that hand-tuned customizations will be lost),
    or (c) abort."
    """
    body = AGENT_FILE.read_text()
    # The warning must be present, not just the flag.
    assert "--force-templates" in body, (
        "agents/plugin-fixer.md must mention the --force-templates option in the post-failure decision matrix."
    )
    # And it must be paired with a warning about overwriting customisations.
    has_warning = any(
        kw in body.lower() for kw in ("overwrit", "lost", "lose", "hand-tuned", "customisation", "customization")
    )
    assert has_warning, (
        "agents/plugin-fixer.md must explicitly warn that --force-templates "
        "will overwrite hand-tuned customisations to canonical files."
    )


def test_agent_does_not_silently_force_templates_on_failure() -> None:
    """Agent body MUST contain an explicit instruction to NOT auto-force-templates.

    Negative-pattern guard: ensure the agent body says something equivalent
    to 'do NOT silently --force-templates'.
    """
    body = AGENT_FILE.read_text()
    silent_phrases = [
        r"do not silently `?--force-templates`?",
        r"never silently `?--force-templates`?",
        r"do NOT silently `?--force-templates`?",
        r"never auto-pick",
    ]
    matched = any(re.search(p, body, re.IGNORECASE) for p in silent_phrases)
    assert matched, (
        "agents/plugin-fixer.md must explicitly forbid silent "
        "--force-templates on failure (must instead surface the "
        "per-CHECK failure list and ask the user)."
    )


# ── Contract: agent body has the new exit-contract wording ───────────────────


def test_agent_has_migration_exit_contract_section() -> None:
    """Agent body MUST contain a "Migration exit contract" section (or equivalent)."""
    body = AGENT_FILE.read_text()
    assert re.search(r"Migration exit contract|Migration is NOT complete", body), (
        "agents/plugin-fixer.md must contain a 'Migration exit contract' "
        "subsection (or equivalent wording 'Migration is NOT complete') in "
        "the Completion gate section that spells out the (a)+(b)+(c) "
        "triple condition."
    )


def test_agent_exit_contract_mentions_marketplace_layout() -> None:
    """Agent's migration exit contract MUST acknowledge marketplace/Layout-C cases."""
    body = AGENT_FILE.read_text()
    assert "Layout-C" in body or "Layout C" in body or "marketplace" in body.lower(), (
        "agents/plugin-fixer.md must mention Layout-C marketplace or the "
        "registered-upstream-marketplace case in the exit contract — "
        "without it, plugins in marketplaces would silently skip the "
        "marketplace publish + watch step."
    )


def test_agent_exit_contract_mentions_partial_state() -> None:
    """Agent body MUST return [PARTIAL] (not [DONE]) on contract failure."""
    body = AGENT_FILE.read_text()
    assert "[PARTIAL]" in body, (
        "agents/plugin-fixer.md must return [PARTIAL] when the migration "
        "exit contract fails (not [DONE], not [BLOCKED] alone) — this is "
        "the user-visible signal that a migration ran but did not reach "
        "the CI-green gate."
    )


# ── Contract: agent has Bash in tools ─────────────────────────────────────────


def test_agent_tools_include_bash() -> None:
    """Agent's tools: frontmatter MUST include Bash — needed for run_all_checks + gh."""
    body = AGENT_FILE.read_text()
    # Extract the YAML frontmatter (between first two '---' markers).
    fm_match = re.match(r"^---\n(.*?)\n---", body, re.DOTALL)
    assert fm_match, "agents/plugin-fixer.md must have YAML frontmatter"
    frontmatter = fm_match.group(1)
    # Either (a) tools: list contains Bash, or (b) tools: field is absent
    # (which means the agent inherits all tools, including Bash, from the
    # default surface). Per issue #21 ask #1 we want Bash to be EXPLICIT
    # so the agent's required surface is unambiguous.
    tools_section_match = re.search(r"^tools:\n((?:\s+-\s+\w+\n)+)", frontmatter, re.MULTILINE)
    assert tools_section_match, (
        "agents/plugin-fixer.md frontmatter must declare tools: explicitly. "
        "Without an explicit list, the migration contract's Bash + gh + "
        "AskUserQuestion requirements are implicit (and a future inheritance "
        "regression could remove them silently)."
    )
    tools_block = tools_section_match.group(1)
    assert "Bash" in tools_block, (
        "agents/plugin-fixer.md frontmatter must include Bash in the tools: "
        "list — needed for run_all_checks, publish.py, and gh run watch."
    )
    assert "AskUserQuestion" in tools_block, (
        "agents/plugin-fixer.md frontmatter must include AskUserQuestion "
        "in the tools: list — needed for the post-failure decision menu "
        "(fix manually / --force-templates / abort)."
    )


# ── Contract: agent file (formerly the command file) tells user about new contract + time ──
# v2.90.0 (TRDD-c50531c2): the cpv-upgrade-plugin slash command was deleted.
# The migration contract now lives entirely in agents/plugin-fixer.md, which
# is dispatched from cpv-main-menu's "Diagnose & Upgrade" top-level row.
# The COMMAND_FILE constant is kept above only as a regression marker —
# every assertion that used to target it now targets AGENT_FILE.


def test_deleted_cpv_upgrade_plugin_command_stays_deleted() -> None:
    """v2.90.0 regression guard — commands/cpv-upgrade-plugin.md MUST stay deleted.

    Re-creating it would split the migration entry point between the menu
    and a standalone command, breaking the unified menu architecture.
    """
    assert not COMMAND_FILE.exists(), (
        f"{COMMAND_FILE} was deleted in v2.90.0 (TRDD-c50531c2) and MUST stay "
        "deleted. The migration flow now goes through cpv-main-menu's "
        "'Diagnose & Upgrade' top-level row, which dispatches plugin-fixer."
    )


def test_agent_description_warns_about_total_time() -> None:
    """plugin-fixer.md description MUST tell user about the 10-15 min time budget
    (or reference the 82-check matrix, which implies the same)."""
    body = AGENT_FILE.read_text()
    has_time_hint = bool(re.search(r"10-15\s*min|10-15\s*minute|82-check", body, re.IGNORECASE))
    assert has_time_hint, (
        "agents/plugin-fixer.md must tell the user (via its description or "
        "body) that the migration contract runs the 82-check matrix AND a "
        "real publish + CI watch (total time 10-15 minutes) — without this "
        "hint the user might abort thinking the agent hung. v2.90.0 moved "
        "this responsibility from the deleted cpv-upgrade-plugin command."
    )


def test_agent_mentions_82_check_matrix() -> None:
    """plugin-fixer.md MUST mention the 82-check Pre-completion verification matrix."""
    body = AGENT_FILE.read_text()
    assert "82" in body, (
        "agents/plugin-fixer.md must reference '82' (the check count) so "
        "users know what they are signing up for. v2.90.0 moved this "
        "responsibility from the deleted cpv-upgrade-plugin command."
    )


def test_agent_mentions_real_publish_and_ci_watch() -> None:
    """plugin-fixer.md MUST mention the real-publish + gh run watch step."""
    body = AGENT_FILE.read_text()
    has_publish = "publish.py --patch" in body or "real publish" in body.lower()
    has_ci = "gh run watch" in body or "green CI" in body
    assert has_publish, (
        "agents/plugin-fixer.md must mention `publish.py --patch` or 'real "
        "publish' so users know the migration will push a new tag."
    )
    assert has_ci, (
        "agents/plugin-fixer.md must mention `gh run watch` or 'green CI' "
        "so users know CI must pass before [DONE]."
    )


# ── Contract: skills are kept consistent with the agent ──────────────────────


def test_canonical_pipeline_skill_links_checklist() -> None:
    """canonical-pipeline SKILL.md MUST link to the 82-check checklist."""
    body = CANONICAL_PIPELINE_SKILL.read_text()
    assert "canonical-pipeline-migration-checklist.md" in body, (
        "skills/canonical-pipeline/SKILL.md must link to the 82-check "
        "checklist — the skill is loaded by plugin-fixer for migration "
        "runs and must surface the same exit contract."
    )


def test_fix_validation_skill_links_checklist() -> None:
    """fix-validation SKILL.md MUST link to the 82-check checklist."""
    body = FIX_VALIDATION_SKILL.read_text()
    assert "canonical-pipeline-migration-checklist.md" in body, (
        "skills/fix-validation/SKILL.md must link to the 82-check checklist "
        "— the skill is loaded by plugin-fixer for migration runs and must "
        "surface the same exit contract."
    )


def test_iterative_fix_loop_describes_migration_extra_steps() -> None:
    """iterative-fix-loop.md MUST describe the migration-only extra steps (7c, 7d)."""
    body = ITERATIVE_FIX_LOOP.read_text()
    assert re.search(r"run_all_checks|82-check|Pre-completion verification", body), (
        "skills/fix-validation/references/iterative-fix-loop.md must "
        "describe the migration-only extra steps (7c run_all_checks, 7d "
        "real publish + gh run watch). Without this, a fixer agent that "
        "loaded only this reference would skip them and silently regress."
    )


def test_standardize_plugin_skill_mentions_82_check_matrix() -> None:
    """standardize-plugin SKILL.md MUST mention the 82-check matrix in its checklist."""
    body = STANDARDIZE_PLUGIN_SKILL.read_text()
    has_checklist_ref = (
        "canonical-pipeline-migration-checklist" in body or "82-check matrix" in body or "82 check" in body
    )
    assert has_checklist_ref, (
        "skills/standardize-plugin/SKILL.md must mention the 82-check "
        "matrix in its tick-box checklist — when this skill is invoked "
        "from a /cpv-upgrade-plugin path, the post-fix verification step "
        "must be visible."
    )


# ── Regression guard: agent doesn't claim DONE when run-all returns non-zero ──


def test_agent_completion_gate_blocks_on_run_all_non_zero() -> None:
    """The completion-gate wording MUST tie SUCCESS to run_all_checks exit 0.

    This is the load-bearing assertion: the tests above verify run_all_checks
    is mentioned, but this one verifies that the AGENT'S DONE condition
    actually ties to its exit code (not just its mere mention).
    """
    body = AGENT_FILE.read_text()
    # Look for at least one phrase that ties the SUCCESS / DONE / clean
    # outcome to the run_all_checks exit code being 0.
    success_tied_to_runall_patterns = [
        r"run_all_checks.{0,80}exit\s*0",
        r"exit\s*0.{0,80}run_all_checks",
        r"run_all_checks.{0,200}return SUCCESS",
        r"step 7c.{0,200}exit 0",
        r"step 7c.{0,200}returns? exit 0",
        r"run_all_checks.{0,200}returns? exit 0",
    ]
    matched = any(re.search(p, body, re.IGNORECASE | re.DOTALL) for p in success_tied_to_runall_patterns)
    assert matched, (
        "agents/plugin-fixer.md must tie the SUCCESS / [DONE] return "
        "condition to run_all_checks returning exit 0. Without this "
        "explicit tying, a future edit could mention run_all_checks for "
        "documentation purposes only and silently bypass it. Add wording "
        "like 'step 7c returns exit 0' or 'run_all_checks returns exit 0' "
        "next to the SUCCESS / [DONE] return."
    )


def test_run_all_script_section_in_checklist() -> None:
    """The checklist file MUST contain the '### run_all_checks' marker pair.

    The agent's bash script extracts the run-all function by `awk` between
    these two markers — without them, step 7c silently fails to load the
    function.
    """
    body = CHECKLIST_FILE.read_text()
    assert "### run_all_checks" in body, (
        "references/canonical-pipeline-migration-checklist.md must contain "
        "the marker '### run_all_checks' — the agent extracts the function "
        "definition between this marker and '### END_RUN_ALL'."
    )
    assert "### END_RUN_ALL" in body, (
        "references/canonical-pipeline-migration-checklist.md must contain "
        "the marker '### END_RUN_ALL' — without it, the agent's awk "
        "extraction falls off the end of the file."
    )


# ── Regression: checklist categories cover issue #21's bug class ─────────────


@pytest.mark.parametrize(
    "expected_category",
    [
        "workflow",  # Category 1 — workflow YAML integrity
        "Python source",  # Category 2 — Python source quality
        "Hooks",  # Category 3 — Hooks shape
        "publish.py",  # Category 4 — publish.py
        "plugin.json",  # Category 5 — plugin.json
        ".gitignore",  # Category 6
        "self-validate",  # Category 7 — CPV self-validate clean
        "Canonical-template parity",  # Category 8
        "Tests",  # Category 9
        "Git state",  # Category 10
        "Smoke-test publish",  # Category 11
        "Marketplace",  # Category 12
        "Notification",  # Category 13
        "hooks.json",  # Category 14 — hooks.json
        "MCP servers",  # Category 15
        "Docs",  # Category 16
    ],
)
def test_checklist_covers_all_16_categories(expected_category: str) -> None:
    """Each of the 16 categories called out in the agent's contract MUST be in the checklist."""
    body = CHECKLIST_FILE.read_text()
    assert expected_category.lower() in body.lower(), (
        f"references/canonical-pipeline-migration-checklist.md must mention "
        f"the '{expected_category}' category — the migration contract "
        f"depends on all 16 categories being present in the matrix."
    )
