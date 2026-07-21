"""Regression guards for the batch-b17 audit fixes (agent .md doc-correctness).

This module pins three doc-correctness fixes made to agent definition files
during the 2026-05-31 full-audit fix pass. All three are pure-Markdown fixes
(no Python behavior changed), so the guards assert against the agent files'
text — each guard checks BOTH that the original bug-shape is gone AND that the
corrected statement is present, so a future edit that re-introduces the bug
fails here.

Findings covered:
  * cpv-marketplace-fixer-agent.md — a hardcoded "10 fix iterations" cap in the
    completion gate contradicted the file's own "NO hardcoded iteration cap"
    rule (line ~102) and the project's no-hardcoded-iteration-caps feedback
    rule. The pre-existing guard in test_batch_fix_v291.py did NOT match the
    "after N fix iterations" phrasing, so it gave false assurance — this
    module adds a guard for that exact phrasing.
  * cpv-doctor-agent.md — the frontmatter description and the <context>
    `source:` line named a non-existent "/cpv-doctor" slash-command
    orchestrator; the real dispatcher is /cpv-main-menu (Diagnose category),
    which the agent body itself already states correctly.
  * cpv-plugin-manager-agent.md — the CRITICAL rule's blanket "never call manage_*.py
    directly" was factually wrong for manage_plugin.py (no launcher alias, no
    isolation guard), making the install/uninstall flow — and Example 1 —
    impossible if taken literally.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AGENTS = REPO / "agents"


def _read(name: str) -> str:
    return (AGENTS / name).read_text(encoding="utf-8")


# --- cpv-marketplace-fixer-agent: no hardcoded iteration cap in the completion gate ----


def test_marketplace_fixer_completion_gate_has_no_numeric_iteration_cap() -> None:
    """The completion gate must not re-introduce an 'after N fix iterations' cap."""
    text = _read("cpv-marketplace-fixer-agent.md")
    # Original bug shape: "If after 10 fix iterations findings remain, ..."
    # Generalised so any '<digits> fix iteration(s)' or 'after N iterations'
    # cap phrasing in the gate is caught (the blind spot of the older guard).
    bug = re.compile(
        r"after\s+\d+\s+(fix\s+)?iterations?",
        re.IGNORECASE,
    )
    assert not bug.search(text), (
        "cpv-marketplace-fixer-agent.md re-introduced a hardcoded numeric iteration cap "
        "in its completion gate — only oscillation (identical consecutive "
        "finding sets) may terminate the loop. See no-hardcoded-iteration-caps."
    )


def test_marketplace_fixer_completion_gate_states_no_cap_and_oscillation() -> None:
    """The corrected gate must state NO hardcoded cap and oscillation-as-terminator."""
    text = _read("cpv-marketplace-fixer-agent.md")
    assert "NO hardcoded iteration cap" in text, (
        "cpv-marketplace-fixer-agent.md lost the explicit 'NO hardcoded iteration cap' "
        "statement — the loop's only termination condition is oscillation."
    )
    # The completion-gate paragraph specifically must tie [BLOCKED] to
    # oscillation, not to a fixed iteration count.
    assert re.search(
        r"oscillates.*\[BLOCKED\]|\[BLOCKED\].*oscillat",
        text,
        re.IGNORECASE | re.DOTALL,
    ), "cpv-marketplace-fixer-agent.md completion gate must escalate [BLOCKED] on oscillation."


# --- cpv-doctor-agent: dispatcher is /cpv-main-menu, not a fake /cpv-doctor --


def test_cpv_doctor_agent_does_not_name_nonexistent_cpv_doctor_command() -> None:
    """No reference to a standalone '/cpv-doctor' slash command (it does not exist)."""
    text = _read("cpv-doctor-agent.md")
    # '/cpv-doctor' as a standalone token (NOT '/cpv-doctor-agent' and NOT the
    # 'cpv-doctor-recipes.md' reference path). A trailing '-' means it is part
    # of a longer identifier and is allowed.
    bad = re.findall(r"/cpv-doctor(?![-\w])", text)
    assert not bad, (
        "cpv-doctor-agent.md references a non-existent '/cpv-doctor' slash "
        "command — the dispatcher is /cpv-main-menu (Diagnose category). "
        f"Offending matches: {bad}"
    )
    # Sanity: the command file genuinely does not exist.
    assert not (REPO / "commands" / "cpv-doctor.md").exists(), (
        "commands/cpv-doctor.md unexpectedly exists — re-evaluate this guard."
    )


def test_cpv_doctor_agent_names_cpv_main_menu_dispatcher() -> None:
    """The corrected description + <context> source name /cpv-main-menu."""
    text = _read("cpv-doctor-agent.md")
    assert "/cpv-main-menu main-session orchestrator" in text, (
        "cpv-doctor-agent.md description must name /cpv-main-menu as dispatcher."
    )
    assert "source: /cpv-main-menu main-session menu" in text, (
        "cpv-doctor-agent.md <context> 'source:' must name /cpv-main-menu."
    )
    # The real dispatcher command file must exist.
    assert (REPO / "commands" / "cpv-main-menu.md").exists(), (
        "commands/cpv-main-menu.md is missing — the dispatcher reference is dead."
    )


# --- cpv-plugin-manager-agent: manage_plugin.py is the documented direct-call exception -


def test_plugin_manager_critical_rule_carves_out_manage_plugin() -> None:
    """The CRITICAL rule must not forbid the direct manage_plugin.py call it relies on."""
    text = _read("cpv-plugin-manager-agent.md")
    # The CRITICAL rule must explicitly note manage_plugin.py is the direct-call
    # exception (it has no launcher alias / isolation guard).
    crit_para = next(
        (ln for ln in text.splitlines() if ln.startswith("**CRITICAL**")),
        "",
    )
    assert "manage_plugin.py" in crit_para, "the CRITICAL paragraph itself must mention the manage_plugin.py exception."
    assert re.search(r"NO launcher alias", crit_para, re.IGNORECASE), (
        "the CRITICAL paragraph must state manage_plugin.py has NO launcher alias."
    )


def test_plugin_manager_install_example_uses_manage_plugin_directly() -> None:
    """Example 1 (install) must keep its direct manage_plugin.py call (now consistent)."""
    text = _read("cpv-plugin-manager-agent.md")
    assert "manage_plugin.py" in text, "cpv-plugin-manager-agent.md lost the manage_plugin.py install example."


def test_remote_validation_has_no_manage_plugin_alias_supports_the_carveout() -> None:
    """Ground-truth: the launcher has no manage_plugin alias, so the carve-out is required."""
    launcher = (REPO / "scripts" / "remote_validation.py").read_text(encoding="utf-8")
    # Extract the _ALIASES dict block and confirm 'manage_plugin' is not a value
    # target nor a key (other than as a substring of unrelated names).
    assert re.search(r'"manage_plugin"\s*:', launcher) is None, (
        "remote_validation.py now defines a 'manage_plugin' alias — the "
        "cpv-plugin-manager-agent.md carve-out should be re-evaluated."
    )
    # manage_plugin.py must still have no isolation guard (it is called directly).
    mgr = (REPO / "scripts" / "manage_plugin.py").read_text(encoding="utf-8")
    assert "remote location" not in mgr, (
        "manage_plugin.py grew a 'remote location' isolation guard — it can no "
        "longer be called directly, so the carve-out must be revisited."
    )
