#!/usr/bin/env python3
"""Tests for agent model policy.

**v2.102.0 policy reversal — NO model pinning.** Earlier revisions used a
per-agent model tier (TRDD-82e836dc: haiku for script-launchers, sonnet for
info-retrieval, opus for analysis). That tiering was REMOVED: a `model:`
frontmatter field forces an in-line model switch that fragments the prompt
cache (each model keeps a separate cache — CPV's own CA-04 cache rule flags
exactly this). Every CPV agent now OMITS `model:` and inherits the session
model, keeping the cache warm. `model: inherit` is the only acceptable
explicit value (it documents intent without forcing a switch).

The architectural invariants from the menu-unification line of TRDDs are
still enforced here because they are independent of the model field:

* **TRDD-bcbceeed** (v2.89.0): the four `*-menu` haiku dispatcher subagents
  were deleted — subagents cannot spawn other subagents per the Anthropic
  spec (https://code.claude.com/docs/en/sub-agents), so a menu-subagent
  dispatch chain is a documented no-op. They must stay deleted.
* **TRDD-c50531c2** (v2.90.0 — menu unification): every workflow routes
  through `/cpv-main-menu`; the opus work agents carry no First Contact menu
  (the slash-command body already made the choice before dispatch).
* **TRDD-4de479a0 → Phase 4**: the `cpv-format-menu` fork-skill +
  `scripts/format_menu.py` were REMOVED (safe-deleted into `.trashcan/`).
  Menus now render via the externalised `claude-menu-system` plugin's
  Stop-hook emitter, brokered through the `scripts/cpv_menu.py` bridge.
  The hook prints via the `systemMessage` JSON field — zero cache cost,
  no subagent fork, no transcript entry.
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


def _pins_concrete_model(fm: dict) -> bool:
    """True iff the frontmatter pins a CONCRETE model (not absent, not `inherit`)."""
    if "model" not in fm:
        return False
    return str(fm["model"]).strip().lower() != "inherit"


# ---------------------------------------------------------------------------
# v2.102.0 no-pinning policy (always active)
# ---------------------------------------------------------------------------


def test_no_agent_pins_a_model() -> None:
    """No CPV agent may pin a concrete `model:` — every agent inherits the session model.

    A `model:` frontmatter forces an in-line model switch that fragments the
    prompt cache (CA-04). The historical haiku/sonnet/opus tiering was removed
    in v2.102.0 in favour of cache-warm inheritance. Absence of the field — or
    an explicit `model: inherit` — is the only acceptable state.
    """
    offenders = []
    for agent_md in sorted(AGENTS_DIR.glob("*.md")):
        fm = _load_frontmatter(agent_md)
        if _pins_concrete_model(fm):
            offenders.append(f"{agent_md.name}: model={fm['model']!r}")
    assert not offenders, (
        "These agents still pin a concrete `model:` (forbidden since v2.102.0 — it "
        "fragments the prompt cache per CA-04; omit the field or use `model: inherit`):\n  " + "\n  ".join(offenders)
    )


def test_every_agent_file_parses_and_keeps_name_description() -> None:
    """Removing `model:` must not have broken any agent's frontmatter."""
    for agent_md in sorted(AGENTS_DIR.glob("*.md")):
        fm = _load_frontmatter(agent_md)
        assert "name" in fm, f"{agent_md.name} lost its `name:` field"
        assert "description" in fm, f"{agent_md.name} lost its `description:` field"


# ---------------------------------------------------------------------------
# Architectural invariants (menu unification) — independent of the model field
# ---------------------------------------------------------------------------

# (work-agent file, slash-command file, work-agent name). These four slash
# commands were main-session menu orchestrators per v2.89.0 (now folded into
# /cpv-main-menu by v2.90.0); the work agents still exist and do the heavy
# lifting.
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
def test_work_agent_has_no_model_field(work: str, cmd: str, _work_name: str) -> None:
    """Each work-agent inherits the session model (no concrete `model:` pin) — v2.102.0.

    These agents used to declare `model: opus` (TRDD-82e836dc). The pin was
    removed in v2.102.0 because it fragments the prompt cache (CA-04); they now
    inherit whatever model the session runs on.
    """
    fm = _load_frontmatter(AGENTS_DIR / work)
    assert not _pins_concrete_model(fm), (
        f"{work} must NOT pin a concrete model (v2.102.0 cache-warm policy — it was "
        f"`model: opus` under TRDD-82e836dc). Counterpart of the {cmd} orchestrator. "
        f"Current model: {fm.get('model')!r}."
    )


@pytest.mark.parametrize("work", ["plugin-fixer.md", "marketplace-fixer.md", "cache-optimizer-agent.md"])
def test_work_agent_has_no_first_contact_menu(work: str) -> None:
    """Work agents must not contain First Contact / numbered-menu blocks.

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


# ---------------------------------------------------------------------------
# cpv_menu.py bridge — claude-menu-system Stop-hook emit (TRDD-4de479a0 Phase 4)
# ---------------------------------------------------------------------------


def test_cpv_menu_bridge_exists_and_no_legacy_format_menu() -> None:
    """`scripts/cpv_menu.py` is the sole CPV-side menu bridge (TRDD-4de479a0).

    Phase 4 removed `scripts/format_menu.py` and the `cpv-format-menu`
    fork-skill (safe-deleted). Menus now go through `cpv_menu.write_menu()`,
    which writes a claude-menu-system spec and ends the orchestrator's turn;
    the bundled Stop / SubagentStop / StopFailure hook (`menu_emit.py`)
    prints the rendered menu via the hook JSON `systemMessage` field —
    zero token cost, no transcript entry, no subagent fork.

    This test pins the new invariant: the bridge exists, and the legacy
    `format_menu.py` script does not.
    """
    bridge = PLUGIN_ROOT / "scripts" / "cpv_menu.py"
    assert bridge.is_file(), f"{bridge} is the sole CPV-side menu bridge after TRDD-4de479a0 Phase 4 — it must exist."
    legacy_script = PLUGIN_ROOT / "scripts" / "format_menu.py"
    assert not legacy_script.exists(), (
        f"{legacy_script} was safe-deleted in TRDD-4de479a0 Phase 4 and "
        f"must not be re-introduced. Use cpv_menu.write_menu() instead."
    )
    legacy_skill = PLUGIN_ROOT / "skills" / "cpv-format-menu" / "SKILL.md"
    assert not legacy_skill.exists(), (
        f"{legacy_skill} fork-skill was safe-deleted in TRDD-4de479a0 "
        f"Phase 4. Menus render via the claude-menu-system Stop hook, "
        f"not via a CPV-side fork skill."
    )
