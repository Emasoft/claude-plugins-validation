#!/usr/bin/env python3
"""Tests for the v2.89.4 ``cpv-format-menu`` fork-skill (TRDD-3ce2f864).

``cpv-format-menu`` was originally invoked by the four CPV menu-
orchestrator commands (``cpv-doctor``, ``cpv-fix-validation``,
``cpv-fix-marketplace-validation``, ``cpv-cache-optimize``) to render
menus via a forked haiku subagent. Per TRDD-c50531c2 (v2.90.0 menu
unification) those four orchestrator commands were DELETED — every
workflow is now routed through ``/cpv-main-menu``.

The skill file itself still exists (a follow-up wave will either delete
it or re-wire it through ``cpv-main-menu-skill``). Until then this file
pins only the structural invariants that hold regardless of whether the
skill has any active loader:

- The skill file exists at the canonical path.
- Its frontmatter declares ``name``, ``context: fork``, ``model: haiku``,
  ``agent: general-purpose``, ``user-invocable: false``.
- Its ``allowed-tools`` are minimal (Bash + Read, no mutation tools).

The "loaders mentioned in description" check (``LOADER_COMMANDS``) is
DROPPED in v2.90.0 because the four named commands no longer exist —
asserting their names in the description is now misleading.

The fork-skill exists because ``model: haiku`` on a slash-command or skill
frontmatter only takes effect "for the rest of the current turn" while
keeping the inherited conversation history (per the Claude Code skills
docs). A multi-turn orchestrator on opus with a 1M-token context cannot
safely degrade mid-turn to haiku — the override silently fails.
``context: fork`` creates a fresh subagent with no inherited history, so
``model: haiku`` actually takes effect for the render step alone.
"""

from __future__ import annotations

from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = PLUGIN_ROOT / "skills" / "cpv-format-menu" / "SKILL.md"


def _load_frontmatter(path: Path) -> dict:
    """Parse the YAML frontmatter block from a markdown file.

    Splits on the literal newline-fence-newline boundary so ``---``
    characters inside the YAML body never cause a false split.
    """
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{path} missing frontmatter — first line is not '---'"
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        parts = text.split("\n---", 1)
    assert len(parts) == 2, f"{path} missing closing frontmatter fence"
    head = parts[0]
    yaml_body = head.split("\n", 1)[1] if "\n" in head else ""
    data = yaml.safe_load(yaml_body) or {}
    assert isinstance(data, dict), f"{path} frontmatter is not a mapping: {type(data).__name__}"
    return data


def test_cpv_format_menu_skill_file_exists() -> None:
    """The skill file MUST exist at ``skills/cpv-format-menu/SKILL.md``.

    TRDD-3ce2f864 §"New skill" mandates this exact path. The four
    orchestrators invoke it as ``claude-plugins-validation:cpv-format-menu``
    via the Skill tool — that fully-qualified name resolves through the
    plugin's standard skills/ folder convention.
    """
    assert SKILL_PATH.is_file(), (
        f"cpv-format-menu skill is missing at {SKILL_PATH}. Per "
        f"TRDD-3ce2f864 §'New skill' it must exist with frontmatter "
        f"declaring `context: fork`, `model: haiku`, "
        f"`agent: general-purpose`, and `user-invocable: false`."
    )


def test_cpv_format_menu_name_field() -> None:
    """Frontmatter ``name`` MUST be exactly ``cpv-format-menu``.

    The plugin-validator's skill-name-matches-folder rule enforces that the
    frontmatter name matches the skill folder name. Both must be
    ``cpv-format-menu`` so the Skill tool invocation
    ``claude-plugins-validation:cpv-format-menu`` resolves correctly.
    """
    fm = _load_frontmatter(SKILL_PATH)
    assert fm.get("name") == "cpv-format-menu", (
        f"cpv-format-menu skill frontmatter `name` must be exactly "
        f"'cpv-format-menu' (matches the folder name). Current name: "
        f"{fm.get('name')!r}."
    )


def test_cpv_format_menu_context_is_fork() -> None:
    """Frontmatter MUST declare ``context: fork``.

    Without ``context: fork`` the skill inherits the parent session's
    conversation history. When the parent is opus with a 1M-token context,
    the ``model: haiku`` override silently degrades — the exact bug
    TRDD-3ce2f864 fixes.
    """
    fm = _load_frontmatter(SKILL_PATH)
    assert fm.get("context") == "fork", (
        f"cpv-format-menu skill must declare `context: fork`. Without it, "
        f"the skill inherits parent conversation history and the haiku "
        f"model override silently degrades. Current context: "
        f"{fm.get('context')!r}."
    )


def test_cpv_format_menu_model_is_haiku() -> None:
    """Frontmatter MUST declare ``model: haiku``.

    This is the actual model that runs the menu render step. Menu rendering
    is bounded text manipulation — haiku is the cheapest tier that can do
    it, and per TRDD-3ce2f864 it's the whole reason this skill exists.
    """
    fm = _load_frontmatter(SKILL_PATH)
    assert fm.get("model") == "haiku", (
        f"cpv-format-menu skill must declare `model: haiku`. Per "
        f"TRDD-3ce2f864 this is THE fork-skill that lets menu rendering "
        f"actually run on haiku. Current model: {fm.get('model')!r}."
    )


def test_cpv_format_menu_agent_is_general_purpose() -> None:
    """Frontmatter MUST declare ``agent: general-purpose``.

    The fork needs to know which subagent type to spawn. ``general-purpose``
    is the canonical bootstrap subagent for context-fork skills that don't
    need specialised tooling — and ``cpv-format-menu`` only needs Bash + Read,
    which every general-purpose subagent has.
    """
    fm = _load_frontmatter(SKILL_PATH)
    assert fm.get("agent") == "general-purpose", (
        f"cpv-format-menu skill must declare `agent: general-purpose` so "
        f"the fork knows which subagent type to spawn. Current agent: "
        f"{fm.get('agent')!r}."
    )


def test_cpv_format_menu_is_not_user_invocable() -> None:
    """Frontmatter MUST declare ``user-invocable: false``.

    The skill is a private rendering helper for the four orchestrator
    commands. Users have no reason to invoke it directly (they would have
    to construct the JSON spec by hand). Per the plugin's
    non-user-invocable convention, it must be hidden from the
    user-facing skill picker.
    """
    fm = _load_frontmatter(SKILL_PATH)
    assert fm.get("user-invocable") is False, (
        f"cpv-format-menu skill must declare `user-invocable: false`. "
        f"It is loaded only by the four orchestrator commands; users "
        f"never invoke it directly. Current value: "
        f"{fm.get('user-invocable')!r}."
    )


# v2.90.0 (TRDD-c50531c2): test_cpv_format_menu_description_references_all_orchestrators
# was removed because the four orchestrator commands it pinned
# (cpv-doctor, cpv-fix-validation, cpv-fix-marketplace-validation,
# cpv-cache-optimize) were DELETED in v2.90.0. The skill has no active
# loader during the menu-unification transition, so a "Loaded by X" check
# that names dead commands would be misleading rather than helpful.


def test_cpv_format_menu_allowed_tools_minimal() -> None:
    """``allowed-tools`` MUST include Bash AND Read but nothing fancy.

    The skill body runs ``cat <spec_path>`` + ``python3 format_menu.py``
    + emits stdout. Bash for the shell pipeline; Read as a fallback for
    inspecting the spec file. Anything beyond that (Edit, Write, Glob,
    Grep, Skill, Agent) would let a misuse leak — the fork is supposed
    to be a single-pass renderer with zero side-effects.
    """
    fm = _load_frontmatter(SKILL_PATH)
    tools = fm.get("allowed-tools", "")
    # Tools can be a string or a list per the plugin spec; normalise.
    if isinstance(tools, list):
        tools_str = ", ".join(tools)
    else:
        tools_str = str(tools)
    assert "Bash" in tools_str, (
        f"cpv-format-menu skill must allow Bash (to run format_menu.py). "
        f"Current allowed-tools: {tools_str!r}."
    )
    assert "Read" in tools_str, (
        f"cpv-format-menu skill must allow Read (to inspect the spec "
        f"file as a fallback). Current allowed-tools: {tools_str!r}."
    )
    # Negative checks — the renderer must NOT be able to mutate files
    # or escalate beyond a single render pass.
    for forbidden in ("Edit", "Write", "Agent"):
        assert forbidden not in tools_str, (
            f"cpv-format-menu skill must NOT allow {forbidden}. It is a "
            f"single-pass renderer with zero side-effects. Current "
            f"allowed-tools: {tools_str!r}."
        )
