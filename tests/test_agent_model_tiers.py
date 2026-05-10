#!/usr/bin/env python3
"""Tests for agent model-tier policy (TRDD-82e836dc).

Enforces the three-tier model assignment policy:

| Tier | When to use | Examples |
|---|---|---|
| haiku | Launching scripts; rendering menus and parsing integer/letter
|       | choices; routing to specialised work agents. NO analysis. |
|       | cpv-main-menu-agent, plugin-validator, skill-validation-agent,
|       | the four `*-menu` dispatchers. |
| sonnet | Mechanical info-retrieval / install / list / show tasks. |
|        | plugin-manager, plugin-creator. |
| opus / opus[1m] | Diagnosis, analysis, planning, reading reports,
|                  | applying fixes, deep semantic checks. |
|                  | plugin-fixer, marketplace-fixer, cache-optimizer-agent,
|                  | cpv-doctor-agent, plugin-diagnoser, semantic-validator. |

Tests in this file:

* Phase A (frontmatter-only downgrades) — always active.
* Phase B (menu/work split for the four opus agents) — auto-active once the
  `*-menu.md` files exist on disk. Until then the Phase B tests are skipped
  with a clear reason so the test file passes after Phase A and starts
  enforcing Phase B as soon as the splits land.
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

    The TRDD's reference implementation in §5 uses ``text.index("---", 3)``
    which only works for the simplest case (no leading newline before the
    fence and no `---` characters inside the YAML). The agent files in this
    repo all start with ``---\\n`` and the closing fence is on a line by
    itself, so we split on the literal newline-fence-newline boundary to
    avoid false positives.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise AssertionError(f"{path} missing frontmatter — first line is not '---'")
    # Find the closing fence on its own line.
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        # Tolerate trailing whitespace on the closing fence line.
        parts = text.split("\n---", 1)
        if len(parts) != 2:
            raise AssertionError(f"{path} missing closing frontmatter fence")
    head = parts[0]
    # Drop the leading "---" line.
    yaml_body = head.split("\n", 1)[1] if "\n" in head else ""
    data = yaml.safe_load(yaml_body) or {}
    if not isinstance(data, dict):
        raise AssertionError(f"{path} frontmatter is not a mapping: {type(data).__name__}")
    return data


def _agent_command_field(path: Path, field: str) -> object:
    """Return a field from a command's frontmatter (or raise KeyError)."""
    fm = _load_frontmatter(path)
    return fm[field]


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
    """Regression guard — cpv-main-menu-agent must stay on haiku.

    This is the gold-standard menu agent the four new `*-menu` dispatchers
    are modeled on. If this ever flips to sonnet/opus, the policy doc and
    this test file have drifted out of sync.
    """
    fm = _load_frontmatter(AGENTS_DIR / "cpv-main-menu-agent.md")
    assert fm["model"] == "haiku", (
        "cpv-main-menu-agent must stay on haiku — it is the canonical "
        "menu-rendering pattern the new *-menu agents inherit from."
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
    # opus[1m] is the 1M context window variant.
    assert fm["model"] in ("opus[1m]", "opus"), (
        "semantic-validator must declare `model: opus[1m]` (or opus fallback) "
        "per TRDD-82e836dc §3 — deep A-F grading requires opus."
    )


# ---------------------------------------------------------------------------
# Phase B — menu/work split (active once the *-menu files exist)
# ---------------------------------------------------------------------------

# Map of (work-agent file, menu-agent file, command file, command-name).
# The four splits in TRDD-82e836dc §4.
_SPLIT_MAP = [
    (
        "plugin-fixer.md",
        "plugin-fixer-menu.md",
        "cpv-fix-validation.md",
        "plugin-fixer-menu",
    ),
    (
        "marketplace-fixer.md",
        "marketplace-fixer-menu.md",
        "cpv-fix-marketplace-validation.md",
        "marketplace-fixer-menu",
    ),
    (
        "cache-optimizer-agent.md",
        "cache-optimizer-menu.md",
        "cpv-cache-optimize.md",
        "cache-optimizer-menu",
    ),
    (
        "cpv-doctor-agent.md",
        "cpv-doctor-menu.md",
        "cpv-doctor.md",
        "cpv-doctor-menu",
    ),
]


def _split_complete(work: str, menu: str) -> bool:
    """Return True iff both halves of a split exist on disk."""
    return (AGENTS_DIR / menu).exists() and (AGENTS_DIR / work).exists()


# Eligibility helpers — each returns True only when the corresponding split
# is on disk. pytest.mark.skipif consults these at collection time so the
# Phase B tests automatically activate once the menu agents land.
def _phase_b_ready(work: str, menu: str) -> bool:
    return _split_complete(work, menu)


@pytest.mark.parametrize("work,menu,cmd,_cmd_name", _SPLIT_MAP)
def test_menu_agent_is_haiku(work: str, menu: str, cmd: str, _cmd_name: str) -> None:
    """Each *-menu agent declares model: haiku."""
    if not _phase_b_ready(work, menu):
        pytest.skip(
            f"Phase B not yet shipped for {menu} — file does not exist. "
            f"Test will activate automatically once {menu} is created."
        )
    fm = _load_frontmatter(AGENTS_DIR / menu)
    assert fm["model"] == "haiku", (
        f"{menu} must declare `model: haiku` — menu agents are pure "
        f"dispatchers (numbered table + integer/letter parse + Agent dispatch)."
    )


@pytest.mark.parametrize("work,menu,cmd,_cmd_name", _SPLIT_MAP)
def test_work_agent_is_opus(work: str, menu: str, cmd: str, _cmd_name: str) -> None:
    """Each work-agent counterpart stays on opus."""
    if not _phase_b_ready(work, menu):
        pytest.skip(
            f"Phase B not yet shipped — {menu} doesn't exist yet. This test gates the {work} side of the split."
        )
    fm = _load_frontmatter(AGENTS_DIR / work)
    assert fm["model"] == "opus", (
        f"{work} must declare `model: opus` — it is the heavy-lifting "
        f"counterpart of {menu} and handles diagnosis/analysis/fixes."
    )


@pytest.mark.parametrize("work,menu,cmd,_cmd_name", _SPLIT_MAP)
def test_menu_agent_tool_surface_is_minimal(work: str, menu: str, cmd: str, _cmd_name: str) -> None:
    """Each *-menu agent declares tools: [Bash, Read, Agent] only."""
    if not _phase_b_ready(work, menu):
        pytest.skip(f"Phase B not yet shipped for {menu} — tool-surface guard skipped.")
    fm = _load_frontmatter(AGENTS_DIR / menu)
    declared_tools = fm.get("tools", [])
    # Tools may be a list of strings or a list of {name: ...} dicts.
    normalised: set[str] = set()
    for tool in declared_tools:
        if isinstance(tool, str):
            normalised.add(tool)
        elif isinstance(tool, dict) and "name" in tool:
            normalised.add(tool["name"])
        else:
            raise AssertionError(f"{menu} declares unparseable tool entry: {tool!r}")
    allowed = {"Bash", "Read", "Agent"}
    extras = normalised - allowed
    assert not extras, (
        f"{menu} declares forbidden tools: {sorted(extras)}. Menu agents "
        f"may only use Bash + Read + Agent (dispatch). Heavy tools belong "
        f"on the work agent ({work})."
    )


# Opus agents that MUST NOT carry a First Contact menu (they receive a
# structured context from the menu agent instead). cpv-doctor-agent.md is
# special-cased in TRDD §4 B.4: it keeps a POST-SCAN follow-up menu that
# requires scanner-output context, so we exclude its full body from the
# "no menu table" check, but we DO require its First Contact (top-level)
# menu to be gone — that's covered by a separate test below.
#
# Each entry is (work-agent, menu-agent) — these names DO NOT follow the
# naive `name + "-menu"` rule because cache-optimizer-agent splits into
# cache-optimizer-menu (NOT cache-optimizer-agent-menu).
_OPUS_AGENTS_NO_FIRST_CONTACT = [
    ("plugin-fixer.md", "plugin-fixer-menu.md"),
    ("marketplace-fixer.md", "marketplace-fixer-menu.md"),
    ("cache-optimizer-agent.md", "cache-optimizer-menu.md"),
]


@pytest.mark.parametrize("agent_name,menu_name", _OPUS_AGENTS_NO_FIRST_CONTACT)
def test_opus_work_agent_has_no_first_contact_menu(agent_name: str, menu_name: str) -> None:
    """Opus work agents must not contain First Contact / numbered-menu blocks.

    They are dispatched by the haiku menu agent which already made the
    choice. A leftover menu in the work agent risks the user being
    re-prompted after dispatch.

    We detect the menu via TWO concrete signals — the heavy table-drawing
    `┏━` row that every legacy menu uses, AND a markdown section header
    that begins with `## First Contact`. Bare prose mentions of "First
    Contact" inside the description / explanatory paragraphs are allowed
    because they document what was MOVED (e.g. "the menu agent handles
    First Contact menu rendering"). The fail is on the SECTION, not the
    phrase.
    """
    if not (AGENTS_DIR / menu_name).exists():
        pytest.skip(
            f"Phase B split not yet shipped for {agent_name} "
            f"({menu_name} missing) — menu-removal guard skipped until split lands."
        )
    body = (AGENTS_DIR / agent_name).read_text(encoding="utf-8")
    # Signal 1: heavy table-drawing characters the legacy menus use.
    assert "┏━" not in body, (
        f"{agent_name} still contains a Unicode-bordered menu table "
        f"(`┏━` found). First Contact menus belong on the haiku menu agent."
    )
    # Signal 2: an actual `## First Contact` section header (case-sensitive,
    # markdown-level-2 only). Inline prose mentions of the term are fine.
    has_first_contact_section = any(line.startswith("## First Contact") for line in body.splitlines())
    assert not has_first_contact_section, (
        f"{agent_name} still contains a `## First Contact` section. The "
        f"menu belongs on the haiku menu agent ({menu_name}). Bare prose "
        f"mentions of the term are allowed; only the section header is forbidden."
    )


def test_cpv_doctor_agent_first_contact_menu_removed() -> None:
    """cpv-doctor-agent loses its FIRST-contact menu (rows 1..22) but keeps the post-scan follow-up.

    Per TRDD §4 B.4, cpv-doctor-agent stays on opus (because the post-scan
    follow-up menu requires scanner-output context). Only the FIRST CONTACT
    pre-scan menu moves to the haiku dispatcher. This test gates only the
    pre-scan menu's absence — leaves the post-scan menu (rows 1..9 with
    severity-threshold actions) intact.
    """
    menu_path = AGENTS_DIR / "cpv-doctor-menu.md"
    if not menu_path.exists():
        pytest.skip(
            "Phase B split not yet shipped for cpv-doctor-agent.md "
            "(cpv-doctor-menu.md missing) — first-contact removal guard skipped."
        )
    body = (AGENTS_DIR / "cpv-doctor-agent.md").read_text(encoding="utf-8")
    # The pre-scan First Contact menu uses a 22-row table (rows 1..22 + A + 0).
    # The most reliable signature is the row "│ 22 │ Add a dependency to a plugin"
    # which only appears in the pre-scan First Contact menu. The post-scan
    # follow-up menu has only 9 numbered rows + A + 0.
    assert "│ 22 │ Add a dependency to a plugin" not in body, (
        "cpv-doctor-agent.md still contains the pre-scan First Contact "
        "menu (row 22 detected). Move that menu to cpv-doctor-menu.md."
    )
    assert "│ 17 │ Cache cleanup" not in body, (
        "cpv-doctor-agent.md still references row 17 of the pre-scan "
        "menu. The pre-scan menu must move to cpv-doctor-menu.md."
    )


@pytest.mark.parametrize("work,menu,cmd,cmd_name", _SPLIT_MAP)
def test_command_routes_to_menu_agent(work: str, menu: str, cmd: str, cmd_name: str) -> None:
    """Each command's `agent:` field points at the menu (not the work) agent."""
    if not _phase_b_ready(work, menu):
        pytest.skip(f"Phase B not yet shipped — {menu} missing. Command-routing guard skipped.")
    cmd_path = COMMANDS_DIR / cmd
    fm = _load_frontmatter(cmd_path)
    actual = fm.get("agent")
    assert actual == cmd_name, (
        f"{cmd}'s `agent:` field is {actual!r}, expected {cmd_name!r}. "
        f"After Phase B, the command must dispatch the haiku menu agent "
        f"(which then dispatches the opus {work.replace('.md', '')})."
    )


@pytest.mark.parametrize("work,menu,cmd,_cmd_name", _SPLIT_MAP)
def test_work_agent_skills_preserved(work: str, menu: str, cmd: str, _cmd_name: str) -> None:
    """The work agent retains the original skill-set (skills do the actual work).

    Per TRDD §4 cross-cutting requirement #3: skills go on WORK agent only.
    Menu agent has empty (or missing) `skills:` list. We assert here that
    the work agent still has at least one skill — it's where the heavy
    lifting reads the fix-validation / canonical-pipeline / etc. skills.
    """
    if not _phase_b_ready(work, menu):
        pytest.skip(f"Phase B not yet shipped for {work} — skills guard skipped.")
    fm = _load_frontmatter(AGENTS_DIR / work)
    skills = fm.get("skills", [])
    assert isinstance(skills, list) and skills, (
        f"{work} lost its `skills:` list during the split. Skills must "
        f"stay on the work agent — only the menu dispatcher is skill-less."
    )


@pytest.mark.parametrize("work,menu,cmd,_cmd_name", _SPLIT_MAP)
def test_menu_agent_has_no_skills(work: str, menu: str, cmd: str, _cmd_name: str) -> None:
    """The menu agent declares no skills (it just dispatches).

    Per TRDD §4 cross-cutting requirement #3: skills declared on both menu
    and work agent would be loaded twice. The menu agent has an empty skill
    list (or no `skills:` key at all).
    """
    if not _phase_b_ready(work, menu):
        pytest.skip(f"Phase B not yet shipped for {menu} — no-skills guard skipped.")
    fm = _load_frontmatter(AGENTS_DIR / menu)
    skills = fm.get("skills", [])
    assert not skills, (
        f"{menu} declares skills {skills!r}. Menu agents must NOT load "
        f"skills — that's the work agent's job (per TRDD §4 cross-cutting #3)."
    )
