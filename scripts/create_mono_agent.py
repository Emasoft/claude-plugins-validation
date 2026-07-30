#!/usr/bin/env python3
"""Generate a PLUGIN-WIDE **ALL-IN-ONE agent**: one agent that PRELOADS every
non-meta skill of a plugin BY NAME and routes to them from its own body.

SUPERSESSION — this script no longer INLINES anything (TRDD-XUNZQ70I,
``design/specs/agent-closure-and-variants.md`` §1.1, §5). It used to concatenate
every non-meta skill BODY into one agent, and that construction is now FORBIDDEN:

    A skill's content is NEVER copied into an agent. An agent REFERENCES skills by
    name in its ``skills:`` frontmatter and nowhere else.

The reason is single-source-of-truth. A skill has to stay INDEPENDENT so it can be
shared by many agents and edited, fixed, or updated ONCE; an inlined copy is a
second source that silently rots the moment the original changes, and with N
agents inlining it there are N stale copies and no signal that any drifted.

Nothing is lost by referencing instead of copying, which is the part that makes
the old design pointless rather than merely risky: ``skills:`` frontmatter IS the
preload mechanism — it injects each named skill's FULL content into every
invocation's cached prefix. So the ALL-IN-ONE agent is still ready from turn 1
with its whole skill set in context; the copy only ever added a maintenance
liability on top of a preload that was already happening.

This is a DELIBERATE BREAKING CHANGE to published behaviour (hence a MAJOR bump),
and there is exactly ONE version of the mechanism — no inlining path is retained
behind a compatibility flag.

Scope is unchanged: this generator is PLUGIN-WIDE (every non-meta skill the plugin
ships). To convert ONE EXISTING AGENT into any of the three architectures — using
that agent's own skill closure and its own routing structure — use
``convert_agent.py <agent.md> --to all-in-one|one-for-all|plugin-omni``.

Usage:
    create_mono_agent.py <plugin-path> [--name NAME] [--include-all] [--force]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Reuse the shared authoring conventions so every scaffolder agrees on what a
# valid component name is and how a plugin root is recognised.
from add_component import _load_json_object, _validate_name

# The variant renderer, the mandatory companion skill, and the routing row type all
# live in convert_agent — ONE definition of what a generated variant looks like,
# whether it came from one agent's closure or from a whole plugin's skill set.
from convert_agent import (
    COMPANION_SKILL_NAME,
    RoutedSkill,
    ensure_companion_skill,
    render_variant_agent,
)
from cpv_agent_closure import skill_blocks_preloading

# Skills that are pure routing / menu / meta scaffolding rather than a
# capability — excluded by default. An ALL-IN-ONE agent is all capability, no
# menu: routing through a catalog is what the PLUGIN-OMNI architecture is for, and
# an agent must never list the skill that generates it. Matched as a SUBSTRING of
# the skill directory name so this works on any target plugin AND on CPV's own
# tree. `--include-all` overrides to literally every skill.
_META_SKILL_SUBSTRINGS = (
    "the-skills-menu",
    "main-menu",
    "semantic-validation",
    "create-mono-agent",
    "create-micro-agents-workflow",
)


def _is_meta_skill(name: str) -> bool:
    return any(sub in name for sub in _META_SKILL_SUBSTRINGS)


def _plugin_slug(plugin: Path) -> str:
    """The plugin's manifest name, falling back to the directory name."""
    data = _load_json_object(plugin / ".claude-plugin" / "plugin.json")
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return plugin.name


def _default_agent_name(plugin: Path) -> str:
    """``<sanitised-slug>-all-in-one``, guaranteed to satisfy the kebab-case rule.

    "mono" survives only as this script's and its skill's historical NAME (renaming
    a published skill is a separate change); it is never used to DESCRIBE the
    architecture, which is ALL-IN-ONE.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", _plugin_slug(plugin).lower()).strip("-")
    return f"{slug}-all-in-one" if slug else "all-in-one-agent"


def select_skills(plugin: Path, *, include_all: bool) -> tuple[list[str], list[tuple[str, str]]]:
    """``(preloadable skill names, [(excluded name, reason)])`` for one plugin.

    Every exclusion carries its reason, and none is silent: a skill that quietly
    vanished from the list would be indistinguishable from one the plugin never
    shipped.

    The un-preloadable filter is what keeps the generated agent VALID. A skill with
    ``disable-model-invocation: true`` (or a bundled user-only ``verify`` /
    ``code-review``) CANNOT be preloaded at all — Claude Code drops such a preload
    silently and only logs it to the debug log — so listing one is a MAJOR (AC5) and
    an agent that never receives that content.
    """
    included: list[str] = []
    excluded: list[tuple[str, str]] = []
    for skill_md in sorted((plugin / "skills").glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        if skill_name == COMPANION_SKILL_NAME:
            # Added unconditionally further down; listing it twice would be a
            # duplicate entry, not a second skill.
            continue
        if not include_all and _is_meta_skill(skill_name):
            excluded.append((skill_name, "meta/router skill (pass --include-all to list it anyway)"))
            continue
        reason = skill_blocks_preloading(skill_name, skill_md)
        if reason is not None:
            excluded.append((skill_name, f"cannot be preloaded: {reason} (AC5)"))
            continue
        included.append(skill_name)
    return included, excluded


def build_mono_agent(
    plugin: Path, name: str, *, include_all: bool
) -> tuple[str, list[str], list[tuple[str, str]]]:
    """Build the ALL-IN-ONE agent ``.md`` text plus ``(included, excluded)``.

    Pure apart from READING the plugin's skill files, so a test can assert on the
    produced text without writing anything. Writing the mandatory companion skill
    to disk is ``create()``'s job, not this function's.
    """
    slug = _plugin_slug(plugin)
    included, excluded = select_skills(plugin, include_all=include_all)
    skills = [*included, COMPANION_SKILL_NAME]

    notes = [
        "This agent declares no `tools:`, so it inherits every session tool — including `Skill`, "
        "which every architecture needs in order to reach its skills.",
        f"Scope: every non-meta skill the `{slug}` plugin ships at generation time. Add a skill to "
        "`skills:` when the plugin gains one; nothing here has to be regenerated for a skill EDIT, "
        "because no skill content is copied in.",
    ]
    by_reason: dict[str, list[str]] = {}
    for skill_name, reason in excluded:
        by_reason.setdefault(reason, []).append(skill_name)
    for reason, names in by_reason.items():
        notes.append(f"NOT preloaded ({reason}): {', '.join(f'`{n}`' for n in names)}.")

    text = render_variant_agent(
        mode="all-in-one",
        agent_name=name,
        scope_label=f"the `{slug}` plugin",
        source_label=None,
        tools=None,
        disallowed=None,
        skills=skills,
        # A plugin-wide generator has no source agent, so there is NO ordering to
        # read: the spec's instruction is to emit a flat "choose by intent" table
        # rather than invent a sequence.
        routed=[RoutedSkill(name=n, branch="", when="") for n in included],
        notes=notes,
    )
    return text, included, excluded


def create(plugin: Path, name: str, *, include_all: bool, force: bool) -> int:
    if not (plugin / ".claude-plugin" / "plugin.json").is_file():
        print(
            f"  [create-mono-agent] {plugin}: not a plugin root (missing .claude-plugin/plugin.json)",
            file=sys.stderr,
        )
        return 1
    _validate_name(name, "agent")

    included, _ = select_skills(plugin, include_all=include_all)
    if not included:
        print(
            f"  [create-mono-agent] {plugin}: no preloadable skills found (skills/ empty, all meta, or "
            f"all un-preloadable) — an agent listing only the companion skill would be an empty shell",
            file=sys.stderr,
        )
        return 1

    agent_md = plugin / "agents" / f"{name}.md"
    if agent_md.is_file() and not force:
        print(
            f"  [create-mono-agent] {agent_md} already exists. Pass --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    # The companion skill must exist BEFORE the agent that preloads it: an
    # unresolvable preload is a MAJOR, so a generator that named it without
    # shipping it would emit an agent that fails CPV's own validator.
    companion_path, companion_created, companion_error = ensure_companion_skill(plugin)
    if companion_error:
        print(f"  [create-mono-agent] {companion_error}", file=sys.stderr)
        return 1

    text, included, excluded = build_mono_agent(plugin, name, include_all=include_all)
    agent_md.parent.mkdir(parents=True, exist_ok=True)
    agent_md.write_text(text, encoding="utf-8")
    print(
        f"  [create-mono-agent] created {agent_md.relative_to(plugin)} preloading {len(included)} skill(s) "
        f"BY NAME (no skill content is copied)"
    )
    if companion_created:
        print(f"  [create-mono-agent] created {companion_path.relative_to(plugin)} (mandatory companion)")
    for i, (skill_name, reason) in enumerate(excluded, start=1):
        print(f"  [create-mono-agent] excluded {i}. {skill_name}: {reason}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("plugin_path", type=Path, help="Plugin root (containing .claude-plugin/plugin.json)")
    p.add_argument("--name", default="", help="Agent name (default: <plugin-slug>-all-in-one)")
    p.add_argument(
        "--include-all",
        action="store_true",
        help="List LITERALLY every skill, including meta/router skills (default: skip meta).",
    )
    p.add_argument("--force", action="store_true", help="Overwrite an existing agent of the same name.")
    args = p.parse_args()

    plugin = args.plugin_path.resolve()
    name = args.name or _default_agent_name(plugin)
    return create(plugin, name, include_all=args.include_all, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
