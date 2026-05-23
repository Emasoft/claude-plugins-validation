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
- Its frontmatter declares ``name``, ``context: fork``,
  ``agent: general-purpose``, ``user-invocable: false`` and pins NO concrete
  ``model:`` (it inherits the session model — v2.102.0 cache-warm policy).
- It declares NO ``allowed-tools`` field. Per the fleet-wide "all tools
  allowed" policy (every CPV skill and agent may call all tools, no limit),
  an absent field grants the full tool surface; an empty ``[]`` would mean
  the opposite (no tools) and a non-empty list would re-introduce a limit.

The "loaders mentioned in description" check (``LOADER_COMMANDS``) is
DROPPED in v2.90.0 because the four named commands no longer exist —
asserting their names in the description is now misleading.

The fork-skill exists for context isolation: ``context: fork`` creates a
fresh subagent with no inherited conversation history, so a long parent
session never bloats the menu-rendering turn. As of v2.102.0 it pins NO
``model:`` — a ``model:`` frontmatter fragments the prompt cache (CA-04),
so the render inherits the session model instead. (Pre-v2.102.0 the skill
pinned ``model: haiku``; that pin was removed for cache-warmth.)
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
        f"declaring `context: fork`, `agent: general-purpose`, and "
        f"`user-invocable: false` (no concrete `model:` since v2.102.0)."
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
    conversation history, so a long parent session bloats the render turn.
    ``context: fork`` keeps the render isolated from parent history.
    """
    fm = _load_frontmatter(SKILL_PATH)
    assert fm.get("context") == "fork", (
        f"cpv-format-menu skill must declare `context: fork`. Without it, "
        f"the skill inherits parent conversation history and a long parent "
        f"session bloats the render turn. Current context: "
        f"{fm.get('context')!r}."
    )


def test_cpv_format_menu_has_no_model_pin() -> None:
    """Frontmatter MUST NOT pin a concrete ``model:`` (v2.102.0 cache-warm policy).

    Pre-v2.102.0 this skill declared ``model: haiku`` to render menus on the
    cheapest tier. That pin was removed because a ``model:`` frontmatter forces
    an in-line model switch that fragments the prompt cache (CPV's own CA-04
    rule). The render now inherits the session model; ``context: fork`` still
    isolates it from the parent's conversation history. ``model: inherit`` is
    the only acceptable explicit value.
    """
    fm = _load_frontmatter(SKILL_PATH)
    model = fm.get("model")
    assert model is None or str(model).strip().lower() == "inherit", (
        f"cpv-format-menu must NOT pin a concrete model (v2.102.0 — it used to be "
        f"`model: haiku`, which fragments the cache per CA-04). Omit the field or use "
        f"`model: inherit`. Current model: {model!r}."
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


def test_cpv_format_menu_has_no_allowed_tools_field() -> None:
    """``allowed-tools`` MUST be ABSENT — cpv-format-menu inherits all tools.

    Per the user's fleet-wide policy (every CPV skill and agent may call all
    tools, no limit), this skill declares NO ``allowed-tools`` field, because an
    absent field means "all tools allowed". Two regressions are pinned out:

    - An empty ``allowed-tools: []`` would mean the OPPOSITE (no tools, chat-
      only) — wrong here.
    - A non-empty list would re-introduce the very restriction the policy
      removed.

    Either form is a regression against the policy, so this test asserts the
    field's ABSENCE. (Historically this test enforced a Bash+Read-only
    lockdown; that security-minimal stance was explicitly overridden by the
    "all tools allowed in every CPV skill and agent" directive.)
    """
    fm = _load_frontmatter(SKILL_PATH)
    assert "allowed-tools" not in fm, (
        f"cpv-format-menu must NOT declare an `allowed-tools` field — per the "
        f"fleet-wide 'all tools allowed' policy, an absent field grants the "
        f"full tool surface. Found allowed-tools: {fm.get('allowed-tools')!r}."
    )
