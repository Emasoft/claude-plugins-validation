#!/usr/bin/env python3
"""Plugin registry operations for Claude Code plugins.

Lists and searches installed plugins with component detection:
- List all installed plugins with name, version, status, components
- Search by component type (commands, agents, skills, hooks, mcp, lsp, rules, output-styles)
- Search by free text (matches names, descriptions, components)

Usage:
    uv run scripts/manage_registry.py --list
    uv run scripts/manage_registry.py --search <query>
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Tuple

from cpv_management_common import (
    MARKETPLACES_DIR,
    SETTINGS_TARGET,
    INSTALLED_FILE,
    info,
    ok,
    warn,
    err,
    load_json_safe,
    BOLD,
    NC,
    GREEN,
    YELLOW,
    CYAN,
    RED,
)
from manage_plugin import read_plugin_meta

__all__ = [
    "do_list",
    "do_search",
    "_detect_components",
    "_format_components",
    "_COMPONENT_TYPES",
    "_SPECIAL_COMPONENTS",
    "_ALL_COMPONENT_KEYWORDS",
]


# ── List ─────────────────────────────────────────────────


def do_list():
    if not MARKETPLACES_DIR.exists():
        info("No local marketplaces found. Nothing installed yet.")
        return

    print(f"{BOLD}Locally installed plugins:{NC}")
    print()

    settings = load_json_safe(SETTINGS_TARGET)
    found = False
    for mp_dir in sorted(MARKETPLACES_DIR.iterdir()):
        if not mp_dir.is_dir():
            continue
        plugins_dir = mp_dir / "plugins"
        if not plugins_dir.exists():
            continue

        mp_name = mp_dir.name
        for plug_dir in sorted(plugins_dir.iterdir()):
            if not plug_dir.is_dir():
                continue
            if not (plug_dir / ".claude-plugin" / "plugin.json").exists():
                continue

            meta = read_plugin_meta(plug_dir)
            plugin_key = f"{meta['name']}@{mp_name}"

            enabled = settings.get("enabledPlugins", {}).get(plugin_key, None)
            status = (
                f"{GREEN}enabled{NC}"
                if enabled
                else f"{YELLOW}disabled{NC}"
                if enabled is False
                else ""
            )

            components = _detect_components(plug_dir)
            comp_str = _format_components(components)

            print(
                f"  {GREEN}{meta['name']}{NC}@{mp_name}  v{meta['version']}  {status}{comp_str}"
            )
            if meta["description"]:
                print(f"    {meta['description']}")
            print(f"    {CYAN}{plug_dir}{NC}")
            found = True

    if not found:
        info("No plugins installed by this tool yet.")
    print()


# ── Search ────────────────────────────────────────────────

# Canonical component type names and their detection logic
_COMPONENT_TYPES: Dict[str, Tuple[str, str]] = {
    "commands": ("commands", "*.md"),
    "agents": ("agents", "*.md"),
    "skills": ("skills", "SKILL.md"),
    "rules": ("rules", "*.md"),
}
# Special components detected by file existence
_SPECIAL_COMPONENTS = {
    "hooks": "hooks",
    "mcp": ".mcp.json",
    "lsp": ".lsp.json",
    "output-styles": "output-styles",
}
# All searchable type keywords
_ALL_COMPONENT_KEYWORDS = set(_COMPONENT_TYPES) | set(_SPECIAL_COMPONENTS)


def _detect_components(plug_dir: Path) -> Dict[str, int]:
    """Detect all component types in a plugin directory. Returns {type: count}."""
    result: Dict[str, int] = {}
    for comp_type, (subdir, glob_pat) in _COMPONENT_TYPES.items():
        comp_dir = plug_dir / subdir
        if comp_dir.exists():
            count = len(list(comp_dir.rglob(glob_pat)))
            if count:
                result[comp_type] = count
    for comp_type, filename in _SPECIAL_COMPONENTS.items():
        path = plug_dir / filename
        if comp_type == "hooks":
            if path.exists() and path.is_dir():
                result["hooks"] = 1
        else:
            if path.exists() and path.is_file():
                result[comp_type] = 1
    return result


def _format_components(components: Dict[str, int]) -> str:
    """Format a components dict into a display string like '[2 commands, hooks, MCP]'."""
    parts = []
    for ctype, count in components.items():
        if ctype in _SPECIAL_COMPONENTS:
            parts.append(ctype.upper() if ctype in ("mcp", "lsp") else ctype)
        else:
            singular = ctype.rstrip("s")
            parts.append(f"{count} {singular if count == 1 else ctype}")
    return f"  [{', '.join(parts)}]" if parts else ""


def do_search(query: str):
    """Search installed plugins by name, description, or component type."""
    if not MARKETPLACES_DIR.exists():
        info("No local marketplaces found. Nothing installed yet.")
        return

    query_lower = query.lower().strip()
    # Check if query is a known component type keyword
    is_type_filter = query_lower in _ALL_COMPONENT_KEYWORDS
    # Also accept common aliases
    type_aliases = {
        "command": "commands",
        "agent": "agents",
        "skill": "skills",
        "rule": "rules",
        "hook": "hooks",
    }
    if query_lower in type_aliases:
        query_lower = type_aliases[query_lower]
        is_type_filter = True

    settings = load_json_safe(SETTINGS_TARGET)
    matches = []

    for mp_dir in sorted(MARKETPLACES_DIR.iterdir()):
        if not mp_dir.is_dir():
            continue
        plugins_dir = mp_dir / "plugins"
        if not plugins_dir.exists():
            continue

        mp_name = mp_dir.name
        for plug_dir in sorted(plugins_dir.iterdir()):
            if not plug_dir.is_dir():
                continue
            if not (plug_dir / ".claude-plugin" / "plugin.json").exists():
                continue

            meta = read_plugin_meta(plug_dir)
            components = _detect_components(plug_dir)
            plugin_key = f"{meta['name']}@{mp_name}"

            # Match logic: type filter OR text search
            matched = False
            if is_type_filter:
                matched = query_lower in components
            else:
                # Text search across name, description, and component types
                searchable = f"{meta['name']} {meta.get('description', '')} {' '.join(components.keys())}".lower()
                matched = query_lower in searchable

            if matched:
                enabled = settings.get("enabledPlugins", {}).get(plugin_key, None)
                matches.append((meta, mp_name, plug_dir, components, enabled))

    if not matches:
        if is_type_filter:
            info(f"No plugins found with component type: {query_lower}")
        else:
            info(f"No plugins matching: {query}")
        return

    label = (
        f"with {BOLD}{query_lower}{NC}"
        if is_type_filter
        else f"matching {BOLD}{query}{NC}"
    )
    print(f"{BOLD}Plugins {label}:{NC}  ({len(matches)} found)")
    print()

    for meta, mp_name, plug_dir, components, enabled in matches:
        plugin_key = f"{meta['name']}@{mp_name}"
        status = (
            f"{GREEN}enabled{NC}"
            if enabled
            else f"{YELLOW}disabled{NC}"
            if enabled is False
            else ""
        )
        comp_str = _format_components(components)

        print(
            f"  {GREEN}{meta['name']}{NC}@{mp_name}  v{meta['version']}  {status}{comp_str}"
        )
        if meta.get("description"):
            print(f"    {meta['description']}")
        print(f"    {CYAN}{plug_dir}{NC}")

    print()


# ── Main ──────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Plugin registry operations")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List all installed plugins")
    group.add_argument("--search", type=str, help="Search plugins by type or text")
    args = parser.parse_args()

    if args.list:
        do_list()
    elif args.search:
        do_search(args.search)


if __name__ == "__main__":
    main()
