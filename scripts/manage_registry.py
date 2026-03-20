#!/usr/bin/env python3
"""Plugin registry operations for Claude Code plugins.

Lists and searches installed plugins with component detection:
- List all installed plugins with name, version, status, components
- Search by component type (commands, agents, skills, hooks, mcp, lsp, rules, output-styles)
- Search by free text (matches names, descriptions, components)

Usage:
    uv run scripts/manage_registry.py --list
    uv run scripts/manage_registry.py --search <query>
    uv run scripts/manage_registry.py --marketplace <name|owner/name>
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Tuple

from cpv_management_common import (
    BOLD,
    CYAN,
    GREEN,
    MARKETPLACES_DIR,
    NC,
    RED,
    SETTINGS_FILE,
    SETTINGS_TARGET,
    YELLOW,
    err,
    info,
)
from manage_plugin import read_plugin_meta

__all__ = [
    "do_list",
    "do_search",
    "do_list_marketplace_plugins",
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

    all_enabled = _load_enabled_plugins()
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

            # Check both user and project-local enabled status
            statuses = all_enabled.get(plugin_key, {"user": None, "local": None})
            # Effective status: local overrides user if set
            effective = statuses.get("local") if statuses.get("local") is not None else statuses.get("user")
            status = (
                f"{GREEN}enabled{NC}"
                if effective is True
                else f"{YELLOW}disabled{NC}"
                if effective is False
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
        if comp_type in ("hooks", "output-styles"):  # directory-based components
            if path.exists() and path.is_dir():
                result[comp_type] = 1
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

    all_enabled = _load_enabled_plugins()
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
                statuses = all_enabled.get(plugin_key, {"user": None, "local": None})
                effective = statuses.get("local") if statuses.get("local") is not None else statuses.get("user")
                matches.append((meta, mp_name, plug_dir, components, effective))

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

    for meta, mp_name, plug_dir, components, effective in matches:
        status = (
            f"{GREEN}enabled{NC}"
            if effective is True
            else f"{YELLOW}disabled{NC}"
            if effective is False
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


# ── List Marketplace Plugins ──────────────────────────────


def _resolve_marketplace_name(query: str) -> str:
    """Resolve a marketplace query to a marketplace directory name.

    Accepts:
      marketplace-name            → use as-is
      owner/marketplace-name      → strip owner/ prefix
    """
    if "/" in query:
        return query.split("/", 1)[1]
    return query


def _find_marketplace_json(mp_name: str) -> "Path | None":
    """Find marketplace.json for a given marketplace name.

    Checks: marketplaces/<name>/.claude-plugin/marketplace.json
            marketplaces/<name>/marketplace.json
    """
    mp_dir = MARKETPLACES_DIR / mp_name
    if not mp_dir.is_dir():
        return None
    for sub in [".claude-plugin/marketplace.json", "marketplace.json"]:
        candidate = mp_dir / sub
        if candidate.is_file():
            return candidate
    return None


def _get_marketplace_owner(mp_name: str) -> str:
    """Extract owner from marketplace registration in settings files."""
    for sf in [SETTINGS_FILE, SETTINGS_TARGET]:
        if not sf.exists():
            continue
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        entry = data.get("extraKnownMarketplaces", {}).get(mp_name, {})
        source = entry.get("source", {})
        src_type = source.get("source", "")
        if src_type == "github":
            repo = source.get("repo", "")
            if "/" in repo:
                return str(repo.split("/", 1)[0])
        elif src_type == "git":
            url = source.get("url", "")
            # Extract owner from https://github.com/Owner/repo.git
            parts = url.replace(".git", "").rstrip("/").split("/")
            if len(parts) >= 2:
                return str(parts[-2])
        elif src_type == "directory":
            path = source.get("path", "")
            # Try to extract owner from path structure
            if path:
                return Path(path).parent.name
    return ""


def _load_enabled_plugins() -> "dict[str, dict[str, bool | None]]":
    """Load enabledPlugins from all settings files.

    Returns {plugin_key: {"user": True/False/None, "local": True/False/None}}.
    """
    result: dict[str, dict[str, "bool | None"]] = {}

    # User-level: ~/.claude/settings.json
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for key, val in data.get("enabledPlugins", {}).items():
                result.setdefault(key, {"user": None, "local": None})
                result[key]["user"] = val
        except (json.JSONDecodeError, OSError):
            pass

    # Project-level: .claude/settings.local.json
    project_settings = Path.cwd() / ".claude" / "settings.local.json"
    if project_settings.exists():
        try:
            data = json.loads(project_settings.read_text(encoding="utf-8"))
            for key, val in data.get("enabledPlugins", {}).items():
                result.setdefault(key, {"user": None, "local": None})
                result[key]["local"] = val
        except (json.JSONDecodeError, OSError):
            pass

    return result


def do_list_marketplace_plugins(query: str):
    """List all plugins available in a marketplace with their enabled status."""
    mp_name = _resolve_marketplace_name(query)
    mj_path = _find_marketplace_json(mp_name)
    if not mj_path:
        err(f"Marketplace '{mp_name}' not found locally.")
        err(f"Checked: {MARKETPLACES_DIR / mp_name}")
        err("Is the marketplace registered? Run: /cpv-manage-marketplaces list")
        sys.exit(1)

    try:
        mj_data = json.loads(mj_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        err(f"Cannot read {mj_path}: {e}")
        sys.exit(1)

    mp_display_name = mj_data.get("name", mp_name)
    mp_version = mj_data.get("version", "?")
    mp_owner = _get_marketplace_owner(mp_name)
    plugins = mj_data.get("plugins", [])

    # Load enabled status from all settings files
    all_enabled = _load_enabled_plugins()

    # Header
    owner_str = f"{mp_owner}/" if mp_owner else ""
    print(f"\n{BOLD}Marketplace: {owner_str}{mp_display_name}{NC}  v{mp_version}")
    print(f"Plugins: {len(plugins)}")
    print()

    if not plugins:
        info("No plugins in this marketplace.")
        return

    # Table header
    hdr = f"  {'Plugin':<40} {'Version':<10} {'User':<10} {'Local':<10}"
    print(f"{BOLD}{hdr}{NC}")
    print(f"  {'─' * 40} {'─' * 10} {'─' * 10} {'─' * 10}")

    def _status_str(val: "bool | None") -> tuple[str, str]:
        """Return (colored_string, raw_text) for alignment."""
        if val is True:
            return f"{GREEN}enabled{NC}", "enabled"
        if val is False:
            return f"{RED}disabled{NC}", "disabled"
        return f"{YELLOW}--{NC}", "--"

    for p in sorted(plugins, key=lambda x: x.get("name", "")):
        name = p.get("name", "?")
        version = p.get("version", "?")
        plugin_key = f"{name}@{mp_name}"

        statuses = all_enabled.get(plugin_key, {"user": None, "local": None})
        user_colored, user_raw = _status_str(statuses.get("user"))
        local_colored, local_raw = _status_str(statuses.get("local"))

        # Pad based on raw text length, then insert colored version
        user_pad = " " * (10 - len(user_raw))
        local_pad = " " * (10 - len(local_raw))
        print(f"  {name:<40} {version:<10} {user_colored}{user_pad} {local_colored}{local_pad}")

    print()


# ── Main ──────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Plugin registry operations")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List all installed plugins")
    group.add_argument("--search", type=str, help="Search plugins by type or text")
    group.add_argument("--marketplace", type=str, help="List plugins in a marketplace (name or owner/name)")
    args = parser.parse_args()

    if args.list:
        do_list()
    elif args.search:
        do_search(args.search)
    elif args.marketplace:
        do_list_marketplace_plugins(args.marketplace)


if __name__ == "__main__":
    main()
