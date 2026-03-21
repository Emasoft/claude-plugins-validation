#!/usr/bin/env python3
"""Health check for Claude Code plugin installation.

Validates the entire plugin ecosystem:
- Claude CLI authentication status
- settings.json and settings.local.json integrity
- Marketplace manifest validation (structure, reserved names, plugin entries)
- Per-plugin validation (delegates to CPV's validate_plugin.py in verbose mode)
- Claude CLI's own `claude plugin validate` on each marketplace
- Orphaned entries detection (settings referencing missing plugins/marketplaces)

Usage:
    uv run scripts/manage_doctor.py [--verbose] [--fix]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

from cpv_management_common import (
    BOLD,
    CACHE_DIR,
    CLAUDE_DIR,
    CYAN,
    GREEN,
    KNOWN_MARKETPLACES_FILE,
    MARKETPLACES_DIR,
    NC,
    RED,
    SETTINGS_FILE,
    SETTINGS_TARGET,
    YELLOW,
    err,
    info,
    load_json_safe,
    load_jsonc,
    ok,
    save_json_safe,
    warn,
)
from manage_plugin import _portable_path, _run_cpv_validation, read_plugin_meta

__all__ = [
    "do_doctor",
    "_run_claude_validate",
]



# read_plugin_meta, _portable_path, _run_cpv_validation imported from manage_plugin


# ── Claude CLI validate helper ───────────────────────────────────────


def _run_claude_validate(target_path: Path) -> Tuple[List[str], List[str]]:
    """Run `claude plugin validate <path>` and parse findings. Returns (errors, warnings).
    Silently returns empty lists if the claude CLI is not available."""
    errors: List[str] = []
    warnings: List[str] = []
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return errors, warnings
    # Strip env vars that prevent claude from running inside another claude instance
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
    }
    try:
        result = subprocess.run(
            [claude_bin, "plugin", "validate", str(target_path)],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        warnings.append("claude plugin validate: timed out or failed to run")
        return errors, warnings
    # Parse output: lines starting with "  ❯ " are findings
    # in_errors tracks whether we're in the errors section vs warnings section
    in_errors = False
    for line in (result.stdout + result.stderr).splitlines():
        stripped = line.strip()
        if "Found" in stripped and "error" in stripped:
            in_errors = True
        elif "Found" in stripped and "warning" in stripped:
            in_errors = False
        elif stripped.startswith("❯"):
            finding = stripped.lstrip("❯").strip()
            prefixed = f"claude validate: {finding}"
            if in_errors:
                errors.append(prefixed)
            else:
                warnings.append(prefixed)
    # If exit code non-zero but we found no parsed errors, add a generic one
    if result.returncode != 0 and not errors:
        errors.append(f"claude plugin validate exited with code {result.returncode}")
    return errors, warnings


def _check_orphaned_settings(settings: dict, fix: bool = False, settings_path: Path | None = None) -> int:
    """Check for orphaned marketplace and plugin entries in settings.

    When fix=True, removes orphaned entries and saves the file.
    Returns the number of issues found.
    """
    issues = 0
    changed = False

    # 1. Orphaned marketplace registrations (directory sources pointing to missing paths)
    ekm = settings.get("extraKnownMarketplaces", {})
    orphaned_mkts: list[str] = []
    for mp_name, mp_cfg in list(ekm.items()):
        source = mp_cfg.get("source", {})
        if source.get("source") == "directory":
            mp_path = Path(source.get("path", ""))
            if not mp_path.exists():
                print()
                warn(f"Orphaned marketplace '{mp_name}' — directory not found: {mp_path}")
                orphaned_mkts.append(mp_name)
                issues += 1

    # 2. Orphaned enabledPlugins (plugin or marketplace doesn't exist)
    ep = settings.get("enabledPlugins", {})
    orphaned_plugins: list[str] = []
    for pkey, _val in list(ep.items()):
        if "@" not in pkey:
            continue
        pname, mpname = pkey.split("@", 1)
        plug_in_marketplace = MARKETPLACES_DIR / mpname / "plugins" / pname
        plug_in_cache = CACHE_DIR / mpname / pname
        if not plug_in_marketplace.exists() and not plug_in_cache.exists():
            mp_exists = (MARKETPLACES_DIR / mpname).exists() or (CACHE_DIR / mpname).exists()
            if not mp_exists:
                print()
                warn(f"Orphaned enabledPlugins entry: '{pkey}' — marketplace '{mpname}' not found")
                orphaned_plugins.append(pkey)
                issues += 1

    # 3. Fix if requested
    if fix and (orphaned_mkts or orphaned_plugins):
        for mp_name in orphaned_mkts:
            ekm.pop(mp_name, None)
            ok(f"  Removed orphaned marketplace: {mp_name}")
            changed = True
        for pkey in orphaned_plugins:
            ep.pop(pkey, None)
            ok(f"  Removed orphaned plugin entry: {pkey}")
            changed = True
        if changed and settings_path:
            save_json_safe(settings_path, settings)
            ok(f"  Saved cleaned {settings_path.name}")

    # 4. Check ~/.claude/settings.local.json — this file should NOT exist at user level.
    # settings.local.json only makes sense inside a project dir (<project>/.claude/settings.local.json).
    # At ~/.claude/ it would only apply if Claude Code were launched from ~/ which is invalid.
    user_local = CLAUDE_DIR / "settings.local.json"
    if user_local.exists():
        print()
        warn("~/.claude/settings.local.json exists — this file should NOT exist at user level (only inside project dirs)")
        issues += 1
        if fix:
            user_local.unlink()
            ok("  Deleted stale ~/.claude/settings.local.json")

    # 5. Check known_marketplaces.json for orphaned entries (directories that don't exist)
    if KNOWN_MARKETPLACES_FILE.exists():
        try:
            km_data = json.loads(KNOWN_MARKETPLACES_FILE.read_text(encoding="utf-8"))
            orphaned_km: list[str] = []
            for km_name, km_cfg in list(km_data.items()):
                source = km_cfg.get("source", {})
                if source.get("source") == "directory":
                    km_path = Path(source.get("path", ""))
                    if not km_path.exists():
                        print()
                        warn(f"Orphaned known_marketplaces entry: '{km_name}' — directory not found: {km_path}")
                        orphaned_km.append(km_name)
                        issues += 1
                else:
                    # Check that the install location exists
                    install_loc = km_cfg.get("installLocation", "")
                    if install_loc and not Path(install_loc).exists():
                        print()
                        warn(f"Orphaned known_marketplaces entry: '{km_name}' — install location missing: {install_loc}")
                        orphaned_km.append(km_name)
                        issues += 1
            if fix and orphaned_km:
                for km_name in orphaned_km:
                    km_data.pop(km_name, None)
                    ok(f"  Removed orphaned known_marketplace: {km_name}")
                save_json_safe(KNOWN_MARKETPLACES_FILE, km_data)
                ok("  Saved cleaned known_marketplaces.json")
        except (json.JSONDecodeError, OSError):
            pass

    return issues


# ── Doctor command ───────────────────────────────────────────────────


def do_doctor(verbose: bool = False, fix: bool = False):
    """Check overall health of local plugin installation."""
    print(f"{BOLD}Plugin installation health check{NC}")
    print()

    issues = 0

    # 1. Check Claude directory exists
    if not CLAUDE_DIR.exists():
        info(f"Claude directory not found at {CLAUDE_DIR}")
        info("No plugins have been installed yet. This is normal for a fresh setup.")
        return
    ok(f"Claude directory: {CLAUDE_DIR}")

    # 2. Check Claude CLI authentication
    claude_bin = shutil.which("claude")
    if claude_bin:
        try:
            result = subprocess.run(
                [claude_bin, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "CLAUDECODE": ""},
            )
            if result.returncode == 0:
                # Extract key info from auth status output
                auth_out = result.stdout.strip()
                if auth_out:
                    ok("Claude CLI authenticated")
                    if verbose:
                        for line in auth_out.splitlines()[:3]:
                            info(f"  {line.strip()}")
                else:
                    ok("Claude CLI: auth status OK")
            else:
                warn(
                    "Claude CLI: not authenticated — marketplace/remote commands will fail"
                )
                issues += 1
        except (subprocess.TimeoutExpired, OSError):
            info("Claude CLI: auth status check skipped (timeout or error)")
    else:
        info("Claude CLI not found on PATH — marketplace/remote commands unavailable")

    # 3. Check settings files
    for label, path in [
        ("settings.json", SETTINGS_FILE),
    ]:
        if path.exists():
            try:
                data = load_jsonc(path)
                ok(f"{label}: valid")
                ekm = data.get("extraKnownMarketplaces", {})
                if ekm:
                    info(f"  {len(ekm)} marketplace(s) registered")
                ep = data.get("enabledPlugins", {})
                if ep:
                    enabled = sum(1 for v in ep.values() if v)
                    disabled = sum(1 for v in ep.values() if not v)
                    info(f"  {enabled} plugin(s) enabled, {disabled} disabled")
            except Exception as e:
                err(f"{label}: CORRUPT — {e}")
                issues += 1
        else:
            info(f"{label}: not present (this is OK)")

    # Load settings once for all checks below (before MARKETPLACES_DIR check
    # so orphaned entries are detected even when no marketplaces directory exists)
    settings = load_json_safe(SETTINGS_TARGET)

    # 4. Check marketplaces directory
    if not MARKETPLACES_DIR.exists():
        info("No local marketplaces directory yet.")
        # Still check for orphaned settings entries even without marketplace directory
        issues += _check_orphaned_settings(settings, fix=fix, settings_path=SETTINGS_TARGET)
        print()
        if issues == 0:
            ok("All checks passed — installation is healthy")
        else:
            warn(f"{issues} issue(s) found")
        return

    # 5. Validate each marketplace
    for mp_dir in sorted(MARKETPLACES_DIR.iterdir()):
        if not mp_dir.is_dir():
            continue
        mp_name = mp_dir.name
        mp_json = mp_dir / ".claude-plugin" / "marketplace.json"

        print()
        print(f"  {BOLD}Marketplace: {mp_name}{NC}")

        if not mp_json.exists():
            err("  Missing marketplace.json")
            issues += 1
            continue

        try:
            mj = json.loads(mp_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            err(f"  marketplace.json: CORRUPT — {e}")
            issues += 1
            continue

        # Validate marketplace.json required structure
        mj_valid = True
        if not isinstance(mj, dict):
            err("  marketplace.json: must be a JSON object")
            issues += 1
            continue
        if not mj.get("name"):
            err("  marketplace.json: missing 'name' field")
            issues += 1
            mj_valid = False
        else:
            mp_json_name = mj["name"]
            # Reserved names per Anthropic spec
            reserved_names = {
                "claude-code-marketplace",
                "claude-code-plugins",
                "claude-plugins-official",
                "anthropic-marketplace",
                "anthropic-plugins",
                "agent-skills",
                "life-sciences",
            }
            if mp_json_name in reserved_names:
                err(
                    f"  marketplace.json: name '{mp_json_name}' is reserved by Anthropic"
                )
                issues += 1
                mj_valid = False
            # Impersonation patterns (contains "official" + "anthropic"/"claude")
            lower_name = mp_json_name.lower()
            if "official" in lower_name and (
                "anthropic" in lower_name or "claude" in lower_name
            ):
                err(
                    f"  marketplace.json: name '{mp_json_name}' impersonates an official marketplace"
                )
                issues += 1
                mj_valid = False
            # Kebab-case: lowercase letters, digits, hyphens only
            if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", mp_json_name):
                warn(
                    f"  marketplace.json: name '{mp_json_name}' should be kebab-case "
                    "(lowercase, hyphens, no spaces)"
                )
                issues += 1
        if not isinstance(mj.get("owner"), dict) or not mj["owner"].get("name"):
            err(
                '  marketplace.json: missing or invalid \'owner\' field (must be {"name": "..."})'
            )
            issues += 1
            mj_valid = False
        # metadata is optional per the Anthropic spec, but description is recommended
        if not isinstance(mj.get("plugins"), list):
            err("  marketplace.json: 'plugins' must be an array")
            issues += 1
            mj_valid = False
        elif len(mj.get("plugins", [])) == 0:
            warn("  marketplace.json: marketplace has no plugins defined")
            issues += 1
        if not (mj.get("metadata", {}) or {}).get("description"):
            warn(
                "  marketplace.json: no marketplace description provided — "
                "add metadata.description to help users understand your marketplace"
            )
            issues += 1
        # Claude CLI only recognizes these root keys — others cause validation errors
        recognized_root_keys = {"name", "owner", "metadata", "plugins"}
        for key in mj:
            if key not in recognized_root_keys:
                warn(
                    f"  marketplace.json: unrecognized root key '{key}' — "
                    "Claude CLI will reject this (only name, owner, metadata, plugins are allowed)"
                )
                issues += 1
        if mj_valid:
            ok("  marketplace.json: valid")

        # Validate individual plugin entries in marketplace.json
        seen_plugin_names = set()
        valid_source_types = {"github", "url", "git-subdir", "npm", "pip"}
        # Required fields per source type (Anthropic spec)
        source_required_fields = {
            "github": ["repo"],
            "url": ["url"],
            "git-subdir": ["url", "path"],
            "npm": ["package"],
            "pip": ["package"],
        }
        for p_entry in mj.get("plugins", []):
            if not isinstance(p_entry, dict):
                warn("  marketplace.json: invalid plugin entry (not an object)")
                issues += 1
                continue
            p_name_display = p_entry.get("name", "?")
            if not p_entry.get("name"):
                warn("  marketplace.json: plugin entry missing 'name'")
                issues += 1
            else:
                # Duplicate name check
                if p_entry["name"] in seen_plugin_names:
                    err(
                        f"  marketplace.json: duplicate plugin name '{p_entry['name']}'"
                    )
                    issues += 1
                seen_plugin_names.add(p_entry["name"])
                # Kebab-case check for plugin names
                if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", p_entry["name"]):
                    warn(
                        f"  marketplace.json: plugin name '{p_entry['name']}' should be kebab-case"
                    )
                    issues += 1
            p_src = p_entry.get("source")
            if not p_src:
                warn(f"  marketplace.json: plugin '{p_name_display}' missing 'source'")
                issues += 1
            elif isinstance(p_src, str):
                # Relative path source — must start with "./" and not contain ".."
                if not p_src.startswith("./"):
                    warn(
                        f"  marketplace.json: plugin '{p_name_display}' relative source "
                        f"'{p_src}' must start with './'"
                    )
                    issues += 1
                if ".." in p_src:
                    err(
                        f"  marketplace.json: plugin '{p_name_display}' source path "
                        f"contains '..' — paths must not climb above the marketplace root"
                    )
                    issues += 1
            elif isinstance(p_src, dict):
                src_type = p_src.get("source", "")
                if not src_type:
                    warn(
                        f"  marketplace.json: plugin '{p_name_display}' source object "
                        "missing 'source' type field"
                    )
                    issues += 1
                elif src_type not in valid_source_types:
                    warn(
                        f"  marketplace.json: plugin '{p_name_display}' unknown source type "
                        f"'{src_type}' — must be one of: {', '.join(sorted(valid_source_types))}"
                    )
                    issues += 1
                else:
                    # Validate required fields for this source type
                    for req_field in source_required_fields.get(src_type, []):
                        if not p_src.get(req_field):
                            warn(
                                f"  marketplace.json: plugin '{p_name_display}' source type "
                                f"'{src_type}' requires '{req_field}' field"
                            )
                            issues += 1

        # Check if registered in extraKnownMarketplaces (informational only)
        # Marketplaces can also be loaded via '/plugin marketplace add' which stores them
        # internally — absence from extraKnownMarketplaces is NOT an error
        ekm = settings.get("extraKnownMarketplaces", {})
        if mp_name in ekm:
            registered_path = ekm[mp_name].get("source", {}).get("path", "")
            actual_path = _portable_path(mp_dir)
            if registered_path and registered_path != actual_path:
                warn(
                    f"  Path mismatch in settings: registered='{registered_path}' actual='{actual_path}'"
                )
                issues += 1
            else:
                ok("  Registered in settings")
        else:
            info(
                "  Not in extraKnownMarketplaces (may be loaded via '/plugin marketplace add')"
            )

        # Run Claude CLI built-in marketplace validation
        cv_errors, cv_warnings = _run_claude_validate(mp_dir)
        for cv_e in cv_errors:
            err(f"  {cv_e}")
            issues += 1
        for cv_w in cv_warnings:
            warn(f"  {cv_w}")
            issues += 1
        if not cv_errors and not cv_warnings and shutil.which("claude"):
            ok("  claude plugin validate: passed")

        # Determine where plugins live based on marketplace.json source paths
        # Not all marketplaces use a plugins/ directory — some use ./ or other structures
        plugins_dir = mp_dir / "plugins"
        if not plugins_dir.exists():
            # Check if plugins are at the marketplace root (source: "./" pattern)
            has_root_plugins = any(
                isinstance(p.get("source"), str)
                and (
                    p.get("source") == "./"
                    or not p.get("source", "").startswith("./plugins/")
                )
                for p in mj.get("plugins", [])
                if isinstance(p, dict)
            )
            if has_root_plugins:
                # Plugins are at root or other locations — scan from marketplace root
                plugins_dir = mp_dir
            else:
                continue

        declared_plugins = {
            p.get("name") for p in mj.get("plugins", []) if isinstance(p, dict)
        }

        # Resolve actual plugin directories from marketplace.json source paths
        # rather than blindly scanning all subdirectories (avoids false positives
        # from .git, .claude-plugin, and other non-plugin directories)
        resolved_plugin_dirs = []
        for p_entry in mj.get("plugins", []):
            if not isinstance(p_entry, dict):
                continue
            p_src = p_entry.get("source")
            if isinstance(p_src, str):
                # Relative path source — resolve to actual directory
                resolved = (mp_dir / p_src).resolve()
                if resolved.is_dir():
                    resolved_plugin_dirs.append(resolved)
            # Object sources (github, npm, etc.) are fetched externally — skip

        # Deduplicate resolved dirs (multiple plugins can share a source like "./")
        seen_dirs = set()
        unique_plugin_dirs = []
        for d in sorted(resolved_plugin_dirs, key=lambda d: d.name):
            rd = d.resolve()
            if rd not in seen_dirs:
                seen_dirs.add(rd)
                unique_plugin_dirs.append(d)

        for plug_dir in unique_plugin_dirs:
            if not plug_dir.is_dir():
                continue
            # Skip marketplace root — plugins with source: "./" use strict:false
            # and define everything inline in marketplace.json
            if plug_dir.resolve() == mp_dir.resolve():
                continue

            pj = plug_dir / ".claude-plugin" / "plugin.json"
            if not pj.exists():
                # Plugin dir exists but has no manifest — may be a stub for external source
                # or an incomplete plugin. Only warn, don't count as issue.
                continue

            meta = read_plugin_meta(plug_dir)
            plugin_key = f"{meta['name']}@{mp_name}"

            # Use CPV's modular validator via shared helper from manage_plugin
            v_errors, v_warnings, _valid = _run_cpv_validation(plug_dir, quiet=True)

            status_parts = []
            if v_errors:
                status_parts.append(f"{RED}{len(v_errors)} error(s){NC}")
                issues += len(v_errors)
            if v_warnings:
                status_parts.append(f"{YELLOW}{len(v_warnings)} warning(s){NC}")
            if not v_errors and not v_warnings:
                status_parts.append(f"{GREEN}clean{NC}")

            # Check enabled status
            enabled = settings.get("enabledPlugins", {}).get(plugin_key)
            en_str = (
                f"{GREEN}enabled{NC}"
                if enabled
                else f"{YELLOW}disabled{NC}"
                if enabled is False
                else f"{CYAN}managed by Claude Code{NC}"
            )

            print(
                f"    {meta['name']} v{meta['version']}  [{en_str}]  [{', '.join(status_parts)}]"
            )

            # Show full validation details in verbose mode
            if verbose and (v_errors or v_warnings):
                for ve in v_errors:
                    print(f"      {RED}ERROR:{NC} {ve}")
                for vw in v_warnings:
                    print(f"      {YELLOW}WARN:{NC}  {vw}")
                print()  # Blank line separator for readability between plugins

            # Check if declared in marketplace.json
            if meta["name"] not in declared_plugins:
                warn(
                    "    Not listed in marketplace.json — may not be discovered by Claude Code"
                )
                issues += 1

    # 6. Check for orphaned entries in settings
    issues += _check_orphaned_settings(settings, fix=fix, settings_path=SETTINGS_TARGET)

    # Summary
    print()
    if issues == 0:
        ok("All checks passed — installation is healthy")
    else:
        warn(f"{issues} issue(s) found")
    scripts_dir = Path(__file__).resolve().parent
    print()
    print(f"  {BOLD}Next steps:{NC}")
    print(f"    List plugins:    uv run {scripts_dir / 'manage_registry.py'} --list")
    print(f"    Validate one:    uv run {scripts_dir / 'validate_plugin.py'} <path>")
    if not verbose:
        print(f"    Verbose doctor:  uv run {scripts_dir / 'manage_doctor.py'} --verbose")
    print()


def main():
    parser = argparse.ArgumentParser(description="Plugin health check")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed validation")
    parser.add_argument("--fix", action="store_true", help="Auto-fix orphaned entries in settings files")
    args = parser.parse_args()
    do_doctor(verbose=args.verbose, fix=args.fix)


if __name__ == "__main__":
    main()
