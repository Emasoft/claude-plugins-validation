#!/usr/bin/env python3
"""Generate a MONO-agent: one agent whose body is prefilled with ALL of a
plugin's (non-meta) skills concatenated into a single, always-loaded body.

EXPERIMENTAL (user directive 2026-07-22). The "prefill-everything" cache
optimization: the whole skill set enters the agent's cached context prefix
ONCE (a single cache-creation cost, then ~1/10-price cache-reads), so the
agent is ready from turn 1, never needs to dynamically load a skill (which
would break the prompt cache each time), and is nudged to actually USE its
skills (an agent that must fetch a skill sometimes skips it).

This is the OPPOSITE of create_micro_agents_workflow.py (the RLM approach,
which shrinks context instead of prefilling it).

Depends on the agent body having NO length cap — CPV removed the agent
body-length limit precisely to make this legal (see the PROJECT-scope memory
note `agents-have-no-body-limit`). Only SKILLS carry a size limit.

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

# Skills that are pure routing / menu / meta scaffolding rather than a
# capability — excluded from the mono body by default (a mono-agent is all
# capability, no menu, and must never inline itself → infinite prefill). Matched
# as a SUBSTRING of the skill directory name so this works on any target plugin
# AND on CPV's own tree. `--include-all` overrides to literally every skill.
_META_SKILL_SUBSTRINGS = (
    "the-skills-menu",
    "main-menu",
    "semantic-validation",
    "create-mono-agent",
    "create-micro-agents-workflow",
)


def _is_meta_skill(name: str) -> bool:
    return any(sub in name for sub in _META_SKILL_SUBSTRINGS)


_FENCE_RE = re.compile(r"^(```|~~~)")
_H1_RE = re.compile(r"^#(\s)")


def _demote_h1(md: str) -> str:
    """Demote every top-level ``# `` heading to ``## ``, OUTSIDE fenced code blocks.

    Each inlined skill body carries its own ``# <name>`` H1; concatenating many of
    them under the agent's own H1 produces "multiple top-level headings" (markdownlint
    MD025), and a NIT blocks ``--strict``. Demoting the skill H1s leaves the agent with
    exactly one H1 (its own). Fence-aware so a ``# comment`` inside a ``` code block is
    never mistaken for a heading; only single-``#`` headings are touched (H2-H6 keep
    their relative hierarchy, and nothing can overflow past H6).
    """
    out: list[str] = []
    in_fence = False
    marker = ""
    for line in md.splitlines():
        stripped = line.lstrip()
        if not in_fence and _FENCE_RE.match(stripped):
            in_fence, marker = True, stripped[:3]
            out.append(line)
        elif in_fence:
            if stripped.startswith(marker):
                in_fence = False
            out.append(line)
        elif _H1_RE.match(line):
            out.append("#" + line)  # `# X` -> `## X`
        else:
            out.append(line)
    return "\n".join(out)


def _strip_frontmatter(text: str) -> str:
    """Return the markdown body with a leading YAML ``---`` frontmatter block removed.

    A SKILL.md is ``---\\n<yaml>\\n---\\n<body>``; we inline only the body so the
    mono-agent does not carry dozens of stray YAML blocks. Text without a leading
    ``---`` is returned unchanged (defensive — a malformed skill still contributes
    its raw content rather than being silently dropped).
    """
    if not text.startswith("---"):
        return text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :]).lstrip("\n")
    return text  # no closing delimiter — treat the whole thing as body


def _plugin_slug(plugin: Path) -> str:
    """The plugin's manifest name, falling back to the directory name."""
    data = _load_json_object(plugin / ".claude-plugin" / "plugin.json")
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return plugin.name


def _default_agent_name(plugin: Path) -> str:
    """`<sanitised-slug>-mono-agent`, guaranteed to satisfy the kebab-case name rule."""
    slug = re.sub(r"[^a-z0-9]+", "-", _plugin_slug(plugin).lower()).strip("-")
    return f"{slug}-mono-agent" if slug else "mono-agent"


def build_mono_agent(
    plugin: Path, name: str, *, include_all: bool
) -> tuple[str, list[str], list[str]]:
    """Build the mono-agent ``.md`` text plus the (included, excluded) skill lists.

    Pure (no I/O beyond reading the plugin's skill files) so tests can assert on
    the produced text without writing anything.
    """
    slug = _plugin_slug(plugin)
    skills_dir = plugin / "skills"
    included: list[str] = []
    excluded: list[str] = []
    sections: list[str] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        if not include_all and _is_meta_skill(skill_name):
            excluded.append(skill_name)
            continue
        body = _demote_h1(_strip_frontmatter(skill_md.read_text(encoding="utf-8")).strip())
        included.append(skill_name)
        sections.append(f"## Skill: {skill_name}\n\n{body}")

    # One line, no colon, well under the 300-token description limit.
    description = (
        f"EXPERIMENTAL prefill-everything mega-agent for {slug} — all "
        f"{len(included)} non-meta skills concatenated into one always-loaded body. "
        f"Use when you want an agent ready from turn 1 with every skill already in "
        f"its cached context (no dynamic skill loading, one cache-creation then cheap "
        f"cache-reads)."
    )
    header = (
        f"---\nname: {name}\ndescription: {description}\n---\n\n"
        f"# {name}\n\n"
        f"You are the prefill-everything mega-agent for the **{slug}** plugin. Every "
        f"skill this plugin ships is inlined below, so you ALREADY have all of them in "
        f"context. Do NOT load skills dynamically (that would break the prompt cache) — "
        f"read the relevant `## Skill:` section below and follow its instructions "
        f"directly.\n\n"
        f"Generated by cpv-create-mono-agent from {len(included)} skill(s). EXPERIMENTAL: "
        f"this body is intentionally very large — the whole skill set is prefilled once "
        f"into the cached prefix, trading a single cache-creation cost for turn-1 "
        f"readiness and cheap cache-reads thereafter.\n"
    )
    text = header + "\n---\n\n" + "\n\n---\n\n".join(sections) + "\n"
    return text, included, excluded


def create(plugin: Path, name: str, *, include_all: bool, force: bool) -> int:
    if not (plugin / ".claude-plugin" / "plugin.json").is_file():
        print(
            f"  [create-mono-agent] {plugin}: not a plugin root (missing .claude-plugin/plugin.json)",
            file=sys.stderr,
        )
        return 1
    _validate_name(name, "agent")
    text, included, excluded = build_mono_agent(plugin, name, include_all=include_all)
    if not included:
        print(
            f"  [create-mono-agent] {plugin}: no skills found to inline (skills/ empty or all meta)",
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
    agent_md.parent.mkdir(parents=True, exist_ok=True)
    agent_md.write_text(text, encoding="utf-8")
    print(f"  [create-mono-agent] created {agent_md.relative_to(plugin)} from {len(included)} skill(s)")
    if excluded:
        print(f"  [create-mono-agent] excluded {len(excluded)} meta skill(s): {', '.join(excluded)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("plugin_path", type=Path, help="Plugin root (containing .claude-plugin/plugin.json)")
    p.add_argument("--name", default="", help="Agent name (default: <plugin-slug>-mono-agent)")
    p.add_argument(
        "--include-all",
        action="store_true",
        help="Inline LITERALLY every skill, including meta/router skills (default: skip meta).",
    )
    p.add_argument("--force", action="store_true", help="Overwrite an existing agent of the same name.")
    args = p.parse_args()

    plugin = args.plugin_path.resolve()
    name = args.name or _default_agent_name(plugin)
    return create(plugin, name, include_all=args.include_all, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
