#!/usr/bin/env python3
"""Add a new component (skill / agent / command / hook / mcp) to an
existing plugin via CLI — saves users from re-running the generator or
hand-editing scaffolds.

Usage:
    cpv add-component <plugin-path> --type skill --name <name> [--description X]
    cpv add-component <plugin-path> --type agent --name <name> [--description X]
    cpv add-component <plugin-path> --type command --name <name> [--description X] [--allowed-tools "Bash,Read"]
    cpv add-component <plugin-path> --type hook --event <Event> --command "<bash>"
    cpv add-component <plugin-path> --type mcp --name <name> --command "<bash>" [--http-url <url>]

Each subcommand writes minimal but valid stubs with frontmatter that
passes validate_plugin / validate_skill out of the box. Existing files
are NEVER overwritten unless `--force` is passed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VALID_TYPES = {"skill", "agent", "command", "hook", "mcp"}


# ── Templates ────────────────────────────────────────────────────────────────


def _skill_template(name: str, description: str) -> str:
    return f"""---
name: {name}
description: {description}
---

# {name}

## Overview

{description}

## When to use

- (describe when this skill should be invoked)

## Instructions

1. (step 1)
2. (step 2)

## Examples

```
(example invocation)
```
"""


def _agent_template(name: str, description: str, tools: str) -> str:
    tools_line = f"tools: {tools}\n" if tools else ""
    return f"""---
name: {name}
description: {description}
{tools_line}---

# {name}

You are {name}. {description}

## Instructions

(define agent behavior here)

## When invoked

(describe trigger conditions)
"""


def _command_template(name: str, description: str, allowed_tools: str) -> str:
    at = allowed_tools or "Bash"
    return f"""---
name: {name}
description: {description}
allowed-tools: {at}
user-invocable: true
---

# /{name}

{description}

## Usage

```bash
(describe how to invoke)
```
"""


# ── Per-type writers ─────────────────────────────────────────────────────────


def _register_in_the_skills_menu(plugin: Path, new_skill_name: str, description: str) -> bool:
    """Append `new_skill_name` to the plugin's the-skills-menu catalog if it exists.

    Per TRDD-9dd64dbf: when a plugin has adopted the-skills-menu method
    (signalled by `skills/the-skills-menu/SKILL.md` existing), every new
    operational skill MUST be listed in the catalog so agents can
    discover it at runtime. Skip silently when the catalog is absent
    (plugin hasn't adopted the method — also valid).

    The new row is appended to the table immediately below
    `## Plugin Skills`. The table-presence detection is forgiving — if
    no table is found, a fresh one is created.

    Returns True if the catalog was modified, False if no catalog exists
    or the new skill is already listed.
    """
    catalog = plugin / "skills" / "the-skills-menu" / "SKILL.md"
    if not catalog.is_file():
        return False
    # Never list the catalog itself or the migrator inside the catalog —
    # recursive self-reference is meaningless.
    if new_skill_name in ("the-skills-menu", "the-skills-menu-create"):
        return False
    content = catalog.read_text(encoding="utf-8")
    raw_desc = description.strip().splitlines()[0][:80] if description.strip() else "(describe the skill)"
    # Escape '|' so a description containing a pipe can't break out of the
    # Markdown table cell and corrupt the catalog table.
    short_desc = raw_desc.replace("|", "\\|")
    new_row = f"| _ | _ | `{new_skill_name}` — {short_desc} |\n"
    # Find the "## Plugin Skills" section and insert at the end of its
    # first markdown table (or just after the section heading if no
    # table exists yet).
    plugin_skills_idx = content.find("## Plugin Skills")
    if plugin_skills_idx < 0:
        # Catalog exists but has no Plugin Skills section — bail.
        return False
    # Search for the next "## " heading (end of Plugin Skills section).
    next_heading_idx = content.find("\n## ", plugin_skills_idx + 1)
    if next_heading_idx < 0:
        next_heading_idx = len(content)
    section = content[plugin_skills_idx:next_heading_idx]
    # Already listed → idempotent no-op. Scope the duplicate check to the
    # Plugin Skills SECTION only (audit LOW #143 — a whole-content search
    # falsely skips registration when the name merely appears in prose, a
    # heading, or another section). Match the BACKTICK-WRAPPED name as it
    # appears in a catalog row (`name`), not a bare substring — a bare
    # substring match falsely skips a new skill whose name is a substring of
    # an existing entry (e.g. "fix" when "fix-validation" is already listed).
    if f"`{new_skill_name}`".lower() in section.lower():
        return False
    # Find the last "| ... |" table row in the section. The trailing group is
    # ``[^\S\n]*$`` (spaces/tabs but NOT the newline) so ``.end()`` stops at
    # the row's last non-blank char — inserting ``"\n" + row`` then lands the
    # new row DIRECTLY below the last existing row. A ``\s*$`` group would
    # swallow the row's terminating newline, dropping the new row one blank
    # line down and severing it from the table (audit MEDIUM #60).
    table_rows = [m for m in re.finditer(r"^\|[^\n]*\|[^\S\n]*$", section, re.MULTILINE)]
    if table_rows:
        last_row_end_in_section = table_rows[-1].end()
        insert_at = plugin_skills_idx + last_row_end_in_section
        new_content = content[:insert_at] + "\n" + new_row.rstrip() + content[insert_at:]
    else:
        # No table in the section yet — append a fresh table with a header
        # row and the new entry.
        fresh_table = (
            "\n\n| # | Domain | Skills |\n"
            "|---|--------|--------|\n"
            f"| 1 | (uncategorised) | `{new_skill_name}` — {short_desc} |\n"
        )
        insert_at = next_heading_idx
        new_content = content[:insert_at] + fresh_table + content[insert_at:]
    catalog.write_text(new_content, encoding="utf-8")
    return True


# Component names become path segments (skills/<name>/, agents/<name>.md,
# commands/<name>.md), so they must be a bare kebab-case slug — no path
# separators, no "..". Mirrors cpv_pack_components._validate_name so the two
# authoring tools agree.
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _validate_name(name: str, kind: str) -> None:
    """Reject names that would escape the plugin root or build an unsafe path.

    Without this a ``--name ../../foo`` writes the component file OUTSIDE the
    plugin (after which ``relative_to(plugin)`` raises). Self-injection only
    (the user supplies their own --name), but a footgun the sibling packer
    already guards against — so we match it here.
    """
    if not _NAME_RE.match(name):
        raise SystemExit(f"{kind} name must match {_NAME_RE.pattern} (kebab-case, no '/' or '..'); got {name!r}")


def add_skill(plugin: Path, name: str, description: str, *, force: bool) -> int:
    _validate_name(name, "skill")
    skill_dir = plugin / "skills" / name
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file() and not force:
        print(f"  [add-skill] {skill_md} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 1
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md.write_text(_skill_template(name, description), encoding="utf-8")
    print(f"  [add-skill] created {skill_md.relative_to(plugin)}")
    # TRDD-9dd64dbf: if the plugin uses the-skills-menu method, also
    # register the new skill in the catalog so agents can discover it.
    if _register_in_the_skills_menu(plugin, name, description):
        print(f"  [add-skill] also registered '{name}' in skills/the-skills-menu/SKILL.md catalog")
    return 0


def add_agent(plugin: Path, name: str, description: str, tools: str, *, force: bool) -> int:
    _validate_name(name, "agent")
    agents_dir = plugin / "agents"
    agent_md = agents_dir / f"{name}.md"
    if agent_md.is_file() and not force:
        print(f"  [add-agent] {agent_md} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 1
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent_md.write_text(_agent_template(name, description, tools), encoding="utf-8")
    print(f"  [add-agent] created {agent_md.relative_to(plugin)}")
    return 0


def add_command(plugin: Path, name: str, description: str, allowed_tools: str, *, force: bool) -> int:
    _validate_name(name, "command")
    cmd_dir = plugin / "commands"
    cmd_md = cmd_dir / f"{name}.md"
    if cmd_md.is_file() and not force:
        print(f"  [add-command] {cmd_md} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 1
    cmd_dir.mkdir(parents=True, exist_ok=True)
    cmd_md.write_text(_command_template(name, description, allowed_tools), encoding="utf-8")
    print(f"  [add-command] created {cmd_md.relative_to(plugin)}")
    return 0


def _load_json_object(path: Path) -> dict:
    """Read an existing JSON config file that MUST hold a top-level object.

    Returns ``{}`` when the file is absent or empty. Mirrors
    ``add_dependencies._read_plugin_json`` so every add-* CLI fails the same,
    legible way instead of a raw traceback:

    * a malformed file raises ``SystemExit`` with the decoder error (without
      this, ``json.loads`` leaks a cryptic ``JSONDecodeError`` traceback);
    * a well-formed-but-non-object file (``[]`` / ``null`` / ``"str"`` /
      ``42``) raises ``SystemExit`` BEFORE the caller's ``data.setdefault(...)``
      would crash with an opaque ``AttributeError`` and (worse) leave the
      config un-mutated with no actionable message.
    """
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"  [add] {path} is malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"  [add] {path} top-level must be a JSON object, got {type(data).__name__}")
    return data


def add_hook(plugin: Path, event: str, command: str) -> int:
    """Append a new hook entry to hooks/hooks.json (creating the file
    if needed). Idempotent: skips if an identical entry already exists.
    """
    hooks_dir = plugin / "hooks"
    hooks_json = hooks_dir / "hooks.json"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    data = _load_json_object(hooks_json)

    events = data.setdefault("hooks", {})
    event_list = events.setdefault(event, [])

    new_entry = {"hooks": [{"type": "command", "command": command}]}
    # Check for an exact-match duplicate — idempotent re-runs.
    for existing in event_list:
        if existing == new_entry:
            print(f"  [add-hook] {event}: identical entry already present; skipping")
            return 0
    event_list.append(new_entry)

    hooks_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  [add-hook] {hooks_json.relative_to(plugin)}: appended {event} → {command!r}")
    return 0


def add_mcp(plugin: Path, name: str, command: str, http_url: str) -> int:
    """Add an entry to .mcp.json (creating it if needed). Idempotent."""
    mcp = plugin / ".mcp.json"
    # `setdefault("mcpServers", {})` below creates the key when missing, so an
    # empty {} from _load_json_object is equivalent to the old {"mcpServers": {}}
    # seed — but now a malformed/non-object .mcp.json fails legibly instead of
    # crashing on `.setdefault`.
    data = _load_json_object(mcp)

    servers = data.setdefault("mcpServers", {})
    if name in servers:
        print(f"  [add-mcp] server {name!r} already in .mcp.json; skipping")
        return 0

    if http_url:
        servers[name] = {"type": "http", "url": http_url}
    else:
        servers[name] = {"command": command} if command else {"command": "echo 'configure me'"}

    mcp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  [add-mcp] {mcp.relative_to(plugin)}: registered server {name!r}")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("plugin_path", type=Path, help="Plugin root (containing .claude-plugin/plugin.json)")
    p.add_argument("--type", required=True, choices=sorted(VALID_TYPES), help="Component type to add")
    p.add_argument("--name", default="", help="Component name (skills/agents/commands/mcp). Required for those types.")
    p.add_argument(
        "--description", default="(describe me)", help="One-line description for the new component (optional)."
    )
    p.add_argument("--tools", default="", help="Agent only: comma-separated `tools:` list.")
    p.add_argument("--allowed-tools", default="", help="Command only: `allowed-tools:` value (e.g. 'Bash(uv:*)').")
    p.add_argument("--event", default="", help="Hook only: event name (PreToolUse, Stop, SessionStart, ...).")
    p.add_argument("--command", default="", help="Hook/MCP only: shell command to run.")
    p.add_argument("--http-url", default="", help="MCP only: HTTP endpoint URL (creates an http-transport server).")
    p.add_argument(
        "--force", action="store_true", help="Overwrite an existing file of the same name (default: refuse)."
    )
    args = p.parse_args()

    plugin = args.plugin_path.resolve()
    if not (plugin / ".claude-plugin" / "plugin.json").is_file():
        print(f"  [add] {plugin}: not a plugin root (missing .claude-plugin/plugin.json)", file=sys.stderr)
        return 1

    if args.type == "skill":
        if not args.name:
            print("  [add-skill] --name is required", file=sys.stderr)
            return 1
        return add_skill(plugin, args.name, args.description, force=args.force)
    if args.type == "agent":
        if not args.name:
            print("  [add-agent] --name is required", file=sys.stderr)
            return 1
        return add_agent(plugin, args.name, args.description, args.tools, force=args.force)
    if args.type == "command":
        if not args.name:
            print("  [add-command] --name is required", file=sys.stderr)
            return 1
        return add_command(plugin, args.name, args.description, args.allowed_tools, force=args.force)
    if args.type == "hook":
        if not args.event or not args.command:
            print("  [add-hook] --event AND --command are required", file=sys.stderr)
            return 1
        return add_hook(plugin, args.event, args.command)
    if args.type == "mcp":
        if not args.name:
            print("  [add-mcp] --name is required", file=sys.stderr)
            return 1
        if not args.command and not args.http_url:
            print("  [add-mcp] --command OR --http-url is required", file=sys.stderr)
            return 1
        return add_mcp(plugin, args.name, args.command, args.http_url)
    return 1


if __name__ == "__main__":
    sys.exit(main())
