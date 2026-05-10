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


def add_skill(plugin: Path, name: str, description: str, *, force: bool) -> int:
    skill_dir = plugin / "skills" / name
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_file() and not force:
        print(f"  [add-skill] {skill_md} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 1
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md.write_text(_skill_template(name, description), encoding="utf-8")
    print(f"  [add-skill] created {skill_md.relative_to(plugin)}")
    return 0


def add_agent(plugin: Path, name: str, description: str, tools: str, *, force: bool) -> int:
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
    cmd_dir = plugin / "commands"
    cmd_md = cmd_dir / f"{name}.md"
    if cmd_md.is_file() and not force:
        print(f"  [add-command] {cmd_md} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 1
    cmd_dir.mkdir(parents=True, exist_ok=True)
    cmd_md.write_text(_command_template(name, description, allowed_tools), encoding="utf-8")
    print(f"  [add-command] created {cmd_md.relative_to(plugin)}")
    return 0


def add_hook(plugin: Path, event: str, command: str) -> int:
    """Append a new hook entry to hooks/hooks.json (creating the file
    if needed). Idempotent: skips if an identical entry already exists.
    """
    hooks_dir = plugin / "hooks"
    hooks_json = hooks_dir / "hooks.json"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    if hooks_json.is_file():
        data = json.loads(hooks_json.read_text(encoding="utf-8") or "{}")
    else:
        data = {}

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
    if mcp.is_file():
        data = json.loads(mcp.read_text(encoding="utf-8") or "{}")
    else:
        data = {"mcpServers": {}}

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
