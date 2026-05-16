#!/usr/bin/env python3
"""Tests for agent model-tier policy.

Two TRDDs are active here:

* **TRDD-82e836dc** (Phase A only — frontmatter-only downgrades): each
  agent's `model:` field is set to the cheapest tier that still does its
  job. These tests are always active.
* **TRDD-bcbceeed** (v2.89.0 — menu-orchestrator architecture fix):
  Phase B of TRDD-82e836dc introduced four `*-menu` haiku dispatcher
  *subagents* (`cpv-doctor-menu`, `plugin-fixer-menu`,
  `marketplace-fixer-menu`, `cache-optimizer-menu`) whose job was to spawn
  the matching opus work agent via the Agent tool. That design was
  invalidated by the current Anthropic spec: per
  https://code.claude.com/docs/en/sub-agents, *"subagents cannot spawn
  other subagents, so `Agent(agent_type)` has no effect in subagent
  definitions"*. The four menu subagents were therefore deleted; the
  slash-command body itself is now the menu orchestrator (runs in the
  main session with `model: haiku` on the slash-command frontmatter,
  dispatches the opus work agent via the Agent tool — which works only
  from the main session). The Phase B tests in this file enforce the
  v2.89.0 architecture: (a) the four `*-menu` files MUST NOT exist,
  (b) the four slash commands carry `model: haiku` and NO `agent:`
  field, (c) the four opus work agents stay on opus and contain no
  user-facing First Contact menu (that menu lives in the slash-command
  body now).

Tier policy:

| Tier | When to use | Examples |
|---|---|---|
| haiku | Launching scripts; rendering menus and parsing integer/letter
|       | choices; routing to specialised work agents. NO analysis. |
|       | cpv-main-menu-agent, plugin-validator, skill-validation-agent. |
| sonnet | Mechanical info-retrieval / install / list / show tasks. |
|        | plugin-manager, plugin-creator. |
| opus / opus[1m] | Diagnosis, analysis, planning, reading reports,
|                  | applying fixes, deep semantic checks. |
|                  | plugin-fixer, marketplace-fixer, cache-optimizer-agent,
|                  | cpv-doctor-agent, plugin-diagnoser, semantic-validator. |
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PLUGIN_ROOT / "agents"
COMMANDS_DIR = PLUGIN_ROOT / "commands"


def _load_frontmatter(path: Path) -> dict:
    """Parse the YAML frontmatter block from a markdown file.

    Splits on the literal newline-fence-newline boundary to avoid false
    positives on `---` characters inside the YAML body.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise AssertionError(f"{path} missing frontmatter — first line is not '---'")
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        parts = text.split("\n---", 1)
        if len(parts) != 2:
            raise AssertionError(f"{path} missing closing frontmatter fence")
    head = parts[0]
    yaml_body = head.split("\n", 1)[1] if "\n" in head else ""
    data = yaml.safe_load(yaml_body) or {}
    if not isinstance(data, dict):
        raise AssertionError(f"{path} frontmatter is not a mapping: {type(data).__name__}")
    return data


# ---------------------------------------------------------------------------
# Phase A — frontmatter-only downgrades (always active)
# ---------------------------------------------------------------------------


def test_plugin_validator_is_haiku() -> None:
    """plugin-validator runs only validation scripts: haiku is sufficient."""
    fm = _load_frontmatter(AGENTS_DIR / "plugin-validator.md")
    assert fm["model"] == "haiku", (
        "plugin-validator must declare `model: haiku` per TRDD-82e836dc §3 — "
        "the agent is a script-launcher with no analysis duties."
    )


def test_skill_validation_agent_is_haiku() -> None:
    """skill-validation-agent runs only validation scripts: haiku is sufficient."""
    fm = _load_frontmatter(AGENTS_DIR / "skill-validation-agent.md")
    assert fm["model"] == "haiku", (
        "skill-validation-agent must declare `model: haiku` per TRDD-82e836dc §3 — "
        "the agent is a script-launcher with no analysis duties."
    )


def test_cpv_main_menu_agent_stays_haiku() -> None:
    """Regression guard — cpv-main-menu-agent must stay on haiku."""
    fm = _load_frontmatter(AGENTS_DIR / "cpv-main-menu-agent.md")
    assert fm["model"] == "haiku", (
        "cpv-main-menu-agent must stay on haiku — it is the canonical "
        "menu-rendering pattern. (Note: per TRDD-bcbceeed this agent is on "
        "the deprecation track too; follow-up TRDD will migrate it to the "
        "slash-command body pattern.)"
    )


def test_plugin_manager_stays_sonnet() -> None:
    """Regression guard — plugin-manager stays on sonnet (info-retrieval tier)."""
    fm = _load_frontmatter(AGENTS_DIR / "plugin-manager.md")
    assert fm["model"] == "sonnet", (
        "plugin-manager must declare `model: sonnet` per TRDD-82e836dc §3 — "
        "mechanical install/list/show tasks need sonnet, not opus."
    )


def test_plugin_creator_stays_sonnet() -> None:
    """Regression guard — plugin-creator stays on sonnet (template-wizard tier)."""
    fm = _load_frontmatter(AGENTS_DIR / "plugin-creator.md")
    assert fm["model"] == "sonnet", (
        "plugin-creator must declare `model: sonnet` per TRDD-82e836dc §3 — "
        "wizard-driven scaffolding doesn't need opus (escalation is leaf-level)."
    )


def test_plugin_diagnoser_stays_opus() -> None:
    """Regression guard — plugin-diagnoser stays on opus (analysis tier)."""
    fm = _load_frontmatter(AGENTS_DIR / "plugin-diagnoser.md")
    assert fm["model"] == "opus", (
        "plugin-diagnoser must declare `model: opus` per TRDD-82e836dc §3 — deep diagnosis requires opus."
    )


def test_semantic_validator_stays_opus_1m() -> None:
    """Regression guard — semantic-validator stays on opus[1m] (deep semantic tier)."""
    fm = _load_frontmatter(AGENTS_DIR / "semantic-validator.md")
    assert fm["model"] in ("opus[1m]", "opus"), (
        "semantic-validator must declare `model: opus[1m]` (or opus fallback) "
        "per TRDD-82e836dc §3 — deep A-F grading requires opus."
    )


# ---------------------------------------------------------------------------
# Phase B (v2.89.0 / TRDD-bcbceeed) — main-session menu orchestrator pattern
# ---------------------------------------------------------------------------

# Map of (work-agent file, slash-command file, work-agent name). These four
# slash commands are main-session menu orchestrators per v2.89.0.
_MAIN_SESSION_MENUS = [
    ("plugin-fixer.md", "cpv-fix-validation.md", "plugin-fixer"),
    ("marketplace-fixer.md", "cpv-fix-marketplace-validation.md", "marketplace-fixer"),
    ("cache-optimizer-agent.md", "cpv-cache-optimize.md", "cache-optimizer-agent"),
    ("cpv-doctor-agent.md", "cpv-doctor.md", "cpv-doctor-agent"),
]

# Menu subagent files that were deleted in v2.89.0. They must stay gone:
# they are subagents that try to spawn other subagents (a documented no-op).
_DELETED_MENU_AGENTS = [
    "cpv-doctor-menu.md",
    "plugin-fixer-menu.md",
    "marketplace-fixer-menu.md",
    "cache-optimizer-menu.md",
]


@pytest.mark.parametrize("menu_filename", _DELETED_MENU_AGENTS)
def test_menu_subagent_is_deleted(menu_filename: str) -> None:
    """The four `*-menu` subagent files must stay deleted (v2.89.0).

    Per TRDD-bcbceeed: subagents cannot spawn other subagents per the
    current Anthropic spec, so the menu-subagent layer is replaced by a
    main-session orchestrator living in the slash-command body. Re-creating
    any of these files would re-introduce a broken dispatch chain.
    """
    path = AGENTS_DIR / menu_filename
    assert not path.exists(), (
        f"{menu_filename} was re-introduced. Per TRDD-bcbceeed (v2.89.0) "
        f"this file MUST stay deleted — subagents cannot spawn other "
        f"subagents, so the menu-subagent dispatch chain is broken by "
        f"spec. The slash-command body is now the menu orchestrator."
    )


@pytest.mark.parametrize("work,cmd,_work_name", _MAIN_SESSION_MENUS)
def test_orchestrator_command_is_haiku(work: str, cmd: str, _work_name: str) -> None:
    """Each menu-orchestrator slash command declares model: haiku."""
    fm = _load_frontmatter(COMMANDS_DIR / cmd)
    assert fm.get("model") == "haiku", (
        f"{cmd} must declare `model: haiku` in its frontmatter so the "
        f"first menu-render turn runs on haiku regardless of session model "
        f"(per TRDD-bcbceeed). Current model: {fm.get('model')!r}."
    )


@pytest.mark.parametrize("work,cmd,_work_name", _MAIN_SESSION_MENUS)
def test_orchestrator_command_has_no_agent_field(work: str, cmd: str, _work_name: str) -> None:
    """Each menu-orchestrator command must NOT declare an `agent:` field.

    Per TRDD-bcbceeed: the slash-command body is the orchestrator and runs
    in the main session. An `agent:` field would dispatch to a subagent —
    which then could not spawn the opus work agent (subagents can't spawn
    subagents). Therefore the field must be absent.
    """
    fm = _load_frontmatter(COMMANDS_DIR / cmd)
    assert "agent" not in fm, (
        f"{cmd} declares `agent: {fm.get('agent')!r}`. Per TRDD-bcbceeed "
        f"this field must be removed — the slash-command body itself "
        f"orchestrates the menu in the main session. Without removal, the "
        f"subagent-can't-spawn-subagent constraint silently breaks dispatch."
    )


@pytest.mark.parametrize("work,cmd,_work_name", _MAIN_SESSION_MENUS)
def test_orchestrator_command_body_invokes_format_menu(work: str, cmd: str, _work_name: str) -> None:
    """Each menu-orchestrator command body MUST call ``scripts/format_menu.py``
    rather than embedding hardcoded Unicode tables.

    Per TRDD-bcbceeed (v2.89.0): the menu presets are part of the slash-command
    body — no external menu-agent file.

    Per TRDD-81e7fa34 (v2.89.3): the body MUST hand its rows to the
    ``scripts/format_menu.py`` renderer (rather than embed a literal
    Unicode table). The renderer drops disabled rows, renumbers the
    rest, and pads cells by DISPLAY width (Unicode-correct alignment).
    Hardcoded ``┏━`` tables are forbidden because they re-introduce the
    ``len()``-vs-display-width alignment bug + the "menu shows greyed
    empty rows" defect from the v2.89.0 end-user feedback.
    """
    body = (COMMANDS_DIR / cmd).read_text(encoding="utf-8")
    assert "scripts/format_menu.py" in body, (
        f"{cmd} body does not invoke `scripts/format_menu.py`. Per "
        f"TRDD-81e7fa34 (v2.89.3) the menu and summary rendering MUST go "
        f"through the helper so cell widths use display columns and "
        f"disabled rows are dropped + renumbered."
    )
    assert ' menu "' in body or " menu '" in body or "format_menu.py menu" in body, (
        f"{cmd} body must call `format_menu.py menu` for at least one menu "
        f"render."
    )


@pytest.mark.parametrize("work,cmd,work_name", _MAIN_SESSION_MENUS)
def test_orchestrator_command_body_references_work_agent(work: str, cmd: str, work_name: str) -> None:
    """Each menu-orchestrator command body references the opus work agent.

    The body's Step 4 dispatch block must contain `subagent_type: <work_name>`
    so the main session knows which subagent to spawn.
    """
    body = (COMMANDS_DIR / cmd).read_text(encoding="utf-8")
    expected = f"subagent_type: {work_name}"
    assert expected in body, (
        f"{cmd} body does not reference `{expected}`. Per TRDD-bcbceeed "
        f"the body must dispatch the opus work agent ({work_name}) via "
        f"the Agent tool from the main session."
    )


@pytest.mark.parametrize("work,cmd,_work_name", _MAIN_SESSION_MENUS)
def test_orchestrator_command_documents_haiku_banner(work: str, cmd: str, _work_name: str) -> None:
    """Each menu-orchestrator command body documents the haiku-session banner.

    The body must instruct the orchestrator to print a banner suggesting
    `/model haiku` for cheaper menu navigation, so the user can opt into
    haiku-everywhere with one keystroke.
    """
    body = (COMMANDS_DIR / cmd).read_text(encoding="utf-8")
    assert "/model haiku" in body, (
        f"{cmd} body does not mention the `/model haiku` opt-in banner. "
        f"Per TRDD-bcbceeed the orchestrator must surface this suggestion "
        f"so users on Opus can opt into haiku-everywhere with one keystroke."
    )


@pytest.mark.parametrize("work,cmd,_work_name", _MAIN_SESSION_MENUS)
def test_work_agent_stays_opus(work: str, cmd: str, _work_name: str) -> None:
    """Each work-agent counterpart stays on opus."""
    fm = _load_frontmatter(AGENTS_DIR / work)
    assert fm["model"] == "opus", (
        f"{work} must declare `model: opus` — it is the heavy-lifting "
        f"counterpart of the {cmd} main-session orchestrator and handles "
        f"diagnosis/analysis/fixes."
    )


@pytest.mark.parametrize("work", ["plugin-fixer.md", "marketplace-fixer.md", "cache-optimizer-agent.md"])
def test_opus_work_agent_has_no_first_contact_menu(work: str) -> None:
    """Opus work agents must not contain First Contact / numbered-menu blocks.

    They are dispatched by the slash-command body which already made the
    choice. A leftover menu in the work agent risks the user being
    re-prompted after dispatch.

    Detected via TWO concrete signals — the heavy table-drawing `┏━` row
    that every legacy menu uses, AND a markdown section header that begins
    with `## First Contact`. Bare prose mentions are allowed (they
    document what was MOVED). The fail is on the SECTION, not the phrase.

    cpv-doctor-agent.md is special-cased separately because it keeps a
    POST-SCAN follow-up menu that requires scanner-output context.
    """
    body = (AGENTS_DIR / work).read_text(encoding="utf-8")
    assert "┏━" not in body, (
        f"{work} still contains a Unicode-bordered menu table (`┏━` found). "
        f"First Contact menus belong on the slash-command body."
    )
    has_first_contact_section = any(line.startswith("## First Contact") for line in body.splitlines())
    assert not has_first_contact_section, (
        f"{work} still contains a `## First Contact` section. The menu "
        f"belongs on the slash-command body. Bare prose mentions of the "
        f"term are allowed; only the section header is forbidden."
    )
