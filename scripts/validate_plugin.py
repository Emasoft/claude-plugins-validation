#!/usr/bin/env python3
"""
Claude Code Plugin Validator

Comprehensive validation suite for Claude Code plugins.
Validates structure, manifest, hooks, skills, scripts, MCP servers, and
since v2.65.0 the whole-repo lint pass via `cpv_lint_engine.lint_repo`
(15 languages, gitignore-aware, uvx/bunx/docker fallback for tool
resolution — strict-by-default missing-tool detection).

Usage:
    uv run python scripts/validate_plugin.py /path/to/plugin
    uv run python scripts/validate_plugin.py --verbose
    uv run python scripts/validate_plugin.py --json
    uv run python scripts/validate_plugin.py --marketplace-only
    uv run python scripts/validate_plugin.py --skip-platform-checks windows

Flags:
    --marketplace-only: Skip plugin.json requirement for marketplace-only
                        distribution (strict=false). When using strict=false,
                        plugin.json should NOT exist (causes CLI issues).

    --skip-platform-checks: Skip platform-specific checks.
                        Valid platforms: windows, macos, linux
                        Use without args to skip all platform checks.
                        Example: --skip-platform-checks windows
                        Example: --skip-platform-checks (skips all)

Exit codes:
    0 - All checks passed (or only INFO/PASSED/WARNING/NIT)
    1 - CRITICAL issues found
    2 - MAJOR issues found
    3 - MINOR issues found
    4 - NIT issues found (--strict mode only)
"""

from __future__ import annotations

import argparse
import difflib
import glob as _glob
import json
import os
import platform
import re
import shlex
import sys
from pathlib import Path
from typing import Any, cast

import yaml
from cpv_lint_engine import lint_repo as run_lint_engine
from cpv_validation_common import (
    COLORS,
    ValidationReport,
    check_remote_execution_guard,
    is_vendored_path,
    load_cpv_config,
    save_report_and_print_summary,
    validate_component_name,
    validate_md_file_paths,
    validate_md_urls,
    validate_no_absolute_paths,
    validate_plugin_shipped_restrictions,
    validate_toc_embedding,
)
from detect_language import detect_languages
from detect_lockfiles import detect_lockfiles
from gitignore_filter import GitignoreFilter
from validate_hook import (
    validate_hooks as validate_hook_file,
)
from validate_mcp import validate_plugin_mcp
from validate_rules import validate_rules_directory

# Import comprehensive skill validator (190+ rules from AgentSkills OpenSpec, Nixtla, Meta-Skills)
from validate_skill_comprehensive import validate_skill as validate_skill_comprehensive

IS_WINDOWS = platform.system() == "Windows"

# Module-level gitignore filter — initialized in main(), used by scan functions
_gi: GitignoreFilter | None = None


# Plugin-name pattern (kebab-case) — mirrors cpv_validation_common.NAME_PATTERN but
# expressed here as a local regex so dependency + channel validators don't reach out.
_PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Identifier pattern for userConfig keys — Python-style identifier.
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Monitor `when` pattern — "always" or "on-skill-invoke:<kebab-skill-name>".
_MONITOR_WHEN_RE = re.compile(r"^always$|^on-skill-invoke:[a-z0-9-]+$")

# Minimal syntactic semver-range check for plugin dependencies.
# Accepts npm-semver-range idioms documented at plugin-dependencies.md:44-52:
#   ~2.1.0, ^2.0, ^2.0.0-0, >=1.4, =2.1.0, 1.2.3, x.y.z - a.b.c, "a || b".
# The regex targets a SINGLE range atom; logical OR is split and each side checked.
_SEMVER_ATOM_RE = re.compile(
    r"""^
    \s*                                           # leading space ok
    (?:                                           # range-kind prefix
        [~^]                                      #   ~ or ^
      | =
      | >=?|<=?                                   #   >, >=, <, <=
    )?
    \s*
    \d+(?:\.\d+){0,2}                             # MAJOR[.MINOR[.PATCH]]
    (?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?       # -prerelease
    (?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?      # +build
    \s*
    $
    """,
    re.VERBOSE,
)

# Hyphen range "x.y.z - a.b.c" (3 tokens separated by a bare dash and spaces).
_SEMVER_HYPHEN_RE = re.compile(
    r"^\s*\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.-]+)?\s+-\s+\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.-]+)?\s*$"
)


def _is_valid_semver_range(text: str) -> bool:
    """Return True when ``text`` parses as a syntactic semver range.

    Not a full npm-semver parser — we only guard against obviously-malformed
    strings (empty, spaces inside a single range token, non-ASCII). Valid
    ranges like ``~2.1.0``, ``^2.0``, ``^2.0.0-0``, ``>=1.4``, ``=2.1.0``,
    ``1.2.3``, ``x.y.z - a.b.c``, logical OR chains ``a || b`` all pass.
    """
    if not isinstance(text, str) or not text:
        return False
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    # Logical OR — each side must be a valid range on its own.
    if "||" in text:
        return all(_is_valid_semver_range(part) for part in text.split("||"))
    # Hyphen range ("x.y.z - a.b.c") — must be checked before the atom regex
    # since the atom regex does not allow internal whitespace.
    if _SEMVER_HYPHEN_RE.match(text):
        return True
    return bool(_SEMVER_ATOM_RE.match(text))


def _path_has_traversal(path: str) -> bool:
    """Return True when ``path`` contains a `..` path segment.

    Splits on both ``/`` and ``\\`` so Windows-style paths are caught too.
    """
    if not isinstance(path, str):
        return False
    parts = re.split(r"[\\/]+", path)
    return any(p == ".." for p in parts)


def _safe_load_marketplace_json(path: Path) -> dict[str, Any] | None:
    """Read+parse a ``marketplace.json`` file. Returns None on any error.

    Used by ``discover_hosting_marketplace`` so a malformed marketplace.json
    on the filesystem never crashes the plugin validator — it just falls
    back to the no-context INFO behaviour. Validation of the marketplace
    file itself is the marketplace-validator's job, not the plugin
    validator's.
    """
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def discover_hosting_marketplace(plugin_root: Path) -> dict[str, Any] | None:
    """Auto-discover the hosting marketplace.json for a plugin on disk.

    Returns the parsed marketplace.json dict (or ``None`` when no hosting
    marketplace is on the filesystem). The result is suitable to pass as the
    ``hosting_marketplace=`` kwarg on ``validate_manifest`` /
    ``validate_dependencies``.

    Discovery order (first match wins — Layout C beats Layout B beats cache):

      1. **Layout C — marketplace-in-plugin.** Plugin's own
         ``.claude-plugin/marketplace.json`` exists at ``plugin_root``.
      2. **Layout B — nested monorepo.** Walk up at most 3 parents looking
         for ``<parent>/.claude-plugin/marketplace.json``. (3 is enough to
         cover ``<mkt>/plugins/<name>/`` and one extra for safety while
         keeping the walk bounded.)
      3. **Cache layout.** ``~/.claude/plugins/cache/<mkt>/<plugin>/`` —
         the immediate parent's ``.claude-plugin/marketplace.json``. This
         is the dominant deployment shape after ``claude plugin install``.

    On a malformed marketplace.json the function returns None rather than
    raising — that surface is owned by ``validate_marketplace.py`` and the
    plugin validator must not crash on a sibling's bad JSON.
    """
    plugin_root = Path(plugin_root)

    # 1. Layout C — self-marketplace
    self_mkt = plugin_root / ".claude-plugin" / "marketplace.json"
    layout_c = _safe_load_marketplace_json(self_mkt)
    if layout_c is not None:
        return layout_c

    # 2. Layout B — walk up looking for a parent .claude-plugin/marketplace.json.
    #    Bound the walk to 3 levels so we don't scan the entire filesystem
    #    for an arbitrarily-deep nesting.
    seen: set[Path] = set()
    parent = plugin_root.parent
    for _ in range(3):
        if parent in seen or parent == parent.parent:
            break
        seen.add(parent)
        parent_mkt = parent / ".claude-plugin" / "marketplace.json"
        layout_b = _safe_load_marketplace_json(parent_mkt)
        if layout_b is not None:
            return layout_b
        parent = parent.parent

    # 3. Cache layout — ~/.claude/plugins/cache/<mkt>/<plugin>/.
    #    Already covered by step 2 when the parent has .claude-plugin/marketplace.json.
    #    Some cache layouts put marketplace.json directly at the cache-mkt root
    #    (no .claude-plugin/ wrapper). Try that fallback too.
    direct_parent_mkt = plugin_root.parent / "marketplace.json"
    return _safe_load_marketplace_json(direct_parent_mkt)


def validate_dependencies(
    manifest: dict[str, Any],
    report: ValidationReport,
    hosting_marketplace: dict[str, Any] | None = None,
) -> None:
    """Validate the ``dependencies`` array per plugin-dependencies.md:29-67.

    Each entry is either:
      * a bare string (plugin name only), or
      * a dict ``{name, version?, marketplace?}``.

    ``name`` is required and must match the plugin kebab-case name pattern.
    ``version`` is optional and must parse as a syntactic semver range.
    ``marketplace`` is optional and must also match the name pattern.
    Extra unknown sub-keys produce a MINOR finding so consumers notice.

    ``hosting_marketplace`` (TRDD-20108ab7, v2.22.3) is the parsed
    ``marketplace.json`` of the marketplace hosting the plugin under
    validation. When supplied, cross-marketplace dependency references are
    checked against the marketplace's ``allowCrossMarketplaceDependenciesOn``
    allowlist (per plugin-dependencies.md:54-79 — the canonical spec field
    name). The dict MUST contain a ``name`` key identifying the hosting
    marketplace; the allowlist is read from
    ``hosting_marketplace["allowCrossMarketplaceDependenciesOn"]`` (optional,
    defaults to empty allowlist). Pass ``None`` to skip cross-marketplace
    allowlist checks (e.g. when validating a plugin in isolation without
    marketplace context) — in that case an INFO is emitted per cross-dep.

    Backward-compat: an earlier CPV release used the non-spec name
    ``allowedDependencyMarketplaces``. Plugins that still ship that key
    are honoured as a fallback (with a NIT nudge to rename to the spec
    field).
    """
    if "dependencies" not in manifest:
        return
    deps = manifest["dependencies"]
    if not isinstance(deps, list):
        report.major(
            f"'dependencies' must be an array, got {type(deps).__name__} (plugin-dependencies.md:29)",
            ".claude-plugin/plugin.json",
        )
        return
    # Resolve hosting-marketplace context (TRDD-20108ab7).
    hosting_name: str | None = None
    hosting_allowlist: list[str] | None = None
    if isinstance(hosting_marketplace, dict):
        raw_name = hosting_marketplace.get("name")
        if isinstance(raw_name, str) and raw_name:
            hosting_name = raw_name
        # Spec name (plugin-dependencies.md): allowCrossMarketplaceDependenciesOn.
        # Fall back to the legacy name allowedDependencyMarketplaces only when
        # the spec name is absent — emit a NIT so authors rename to the spec.
        raw_allow = hosting_marketplace.get("allowCrossMarketplaceDependenciesOn")
        if raw_allow is None:
            raw_allow_legacy = hosting_marketplace.get("allowedDependencyMarketplaces")
            if raw_allow_legacy is not None:
                report.nit(
                    "marketplace.json uses legacy 'allowedDependencyMarketplaces' — "
                    "rename to the spec field 'allowCrossMarketplaceDependenciesOn' "
                    "(plugin-dependencies.md:54-79). Both names are honoured but the "
                    "legacy alias is removed in a future release.",
                    ".claude-plugin/marketplace.json",
                )
                raw_allow = raw_allow_legacy
        if isinstance(raw_allow, list):
            # Keep only string items — bad items are the marketplace validator's job.
            hosting_allowlist = [x for x in raw_allow if isinstance(x, str) and x]
    known_subkeys = {"name", "version", "marketplace"}
    for i, entry in enumerate(deps):
        if isinstance(entry, str):
            if not _PLUGIN_NAME_RE.match(entry):
                report.major(
                    f"'dependencies[{i}]' bare-string name '{entry}' is not a valid kebab-case plugin name",
                    ".claude-plugin/plugin.json",
                )
            else:
                # plugin-dependencies.md:9-11: "By default, a dependency tracks
                # the latest available version, so an upstream release can
                # change the dependency under your plugin without warning."
                # An unversioned bare-string dep is therefore a soft-WARNING
                # signal — install can break on the next upstream tag. Authors
                # who explicitly want auto-tracking can suppress with
                # `cpv: { allow_unversioned_dependencies: true }` in plugin.json.
                cpv_block = manifest.get("cpv") if isinstance(manifest, dict) else None
                allow_unversioned = isinstance(cpv_block, dict) and bool(
                    cpv_block.get("allow_unversioned_dependencies")
                )
                if not allow_unversioned:
                    report.warning(
                        f"'dependencies[{i}]' = '{entry}' has no version constraint "
                        f"— it auto-tracks the latest tag and the next upstream release "
                        f"can break this plugin without warning. Pin a semver range: "
                        f"{{'name': '{entry}', 'version': '~1.2.0'}} (plugin-dependencies.md:9-11). "
                        f"Suppress with `cpv.allow_unversioned_dependencies: true` if "
                        f"intentional.",
                        ".claude-plugin/plugin.json",
                    )
            continue
        if not isinstance(entry, dict):
            report.major(
                f"'dependencies[{i}]' must be a string or object, got {type(entry).__name__} "
                "(plugin-dependencies.md:29-50)",
                ".claude-plugin/plugin.json",
            )
            continue
        # name — required
        if "name" not in entry:
            report.major(
                f"'dependencies[{i}]' object missing required 'name' field (plugin-dependencies.md:46)",
                ".claude-plugin/plugin.json",
            )
        else:
            dep_name = entry["name"]
            if not isinstance(dep_name, str) or not _PLUGIN_NAME_RE.match(dep_name):
                report.major(
                    f"'dependencies[{i}].name' must be a kebab-case plugin name, got {dep_name!r}",
                    ".claude-plugin/plugin.json",
                )
        # version — optional; syntactic range check
        if "version" in entry:
            dep_version = entry["version"]
            if not isinstance(dep_version, str) or not _is_valid_semver_range(dep_version):
                report.major(
                    f"'dependencies[{i}].version' is not a valid semver range: {dep_version!r} "
                    "(plugin-dependencies.md:44-52)",
                    ".claude-plugin/plugin.json",
                )
        # marketplace — optional; must be a plugin-style kebab name
        if "marketplace" in entry:
            market = entry["marketplace"]
            if not isinstance(market, str) or not _PLUGIN_NAME_RE.match(market):
                report.major(
                    f"'dependencies[{i}].marketplace' must be a kebab-case marketplace name, got {market!r}",
                    ".claude-plugin/plugin.json",
                )
            else:
                # TRDD-20108ab7: cross-marketplace dependency resolution. When a
                # dep declares a DIFFERENT marketplace from the hosting one, the
                # target MUST appear in the hosting marketplace's
                # `allowedDependencyMarketplaces` list — otherwise the
                # dependency is blocked at install time.
                if hosting_marketplace is None:
                    # Validating in isolation — informational only.
                    report.info(
                        f"'dependencies[{i}].marketplace' = '{market}' is a cross-marketplace "
                        "reference; allowlist check skipped (no hosting marketplace context)",
                        ".claude-plugin/plugin.json",
                    )
                elif hosting_name is not None and market != hosting_name:
                    if hosting_allowlist is None or market not in hosting_allowlist:
                        allow_desc = sorted(hosting_allowlist) if hosting_allowlist is not None else "<none declared>"
                        report.major(
                            f"'dependencies[{i}].marketplace' = '{market}' is not in the hosting "
                            f"marketplace's allowCrossMarketplaceDependenciesOn allowlist "
                            f"({allow_desc}) — cross-marketplace dependency is blocked at install time "
                            "with a 'cross-marketplace' error (plugin-dependencies.md:54-79). Add "
                            f"'{market}' to the root marketplace.json's "
                            "allowCrossMarketplaceDependenciesOn array OR remove the marketplace field.",
                            ".claude-plugin/plugin.json",
                        )
                    else:
                        report.passed(
                            f"'dependencies[{i}].marketplace' = '{market}' allowlisted "
                            "for cross-marketplace resolution",
                            ".claude-plugin/plugin.json",
                        )
        # unknown sub-keys — MINOR so authors notice typos
        for extra in set(entry.keys()) - known_subkeys:
            report.minor(
                f"'dependencies[{i}].{extra}' is not a recognized dependency sub-field "
                "(recognized: name, version, marketplace)",
                ".claude-plugin/plugin.json",
            )
    if deps:
        report.passed(f"'dependencies' schema valid: {len(deps)} entry(ies)", ".claude-plugin/plugin.json")


# v2.1.121 — userConfig per-key `type` enum (5 values).
USER_CONFIG_TYPE_ENUM = frozenset({"string", "number", "boolean", "directory", "file"})


def validate_user_config_structure(manifest: dict[str, Any], report: ValidationReport) -> None:
    """Validate the ``userConfig`` root per plugins-reference.md (v2.1.121).

    Per-key fields:
      Required: type, title, description
      Optional: sensitive, required, default, multiple, min, max
    Type enum: string | number | boolean | directory | file

    Keys must be valid identifiers (CLAUDE_PLUGIN_OPTION_<KEY> env-var derivation).
    """
    if "userConfig" not in manifest:
        return
    uc = manifest["userConfig"]
    if not isinstance(uc, dict):
        # The inline validator in validate_manifest already emits a MAJOR for
        # non-dict userConfig — no need to duplicate it here.
        return
    # v2.1.121 spec — full sub-field set (9 fields total).
    known_sub = frozenset(
        {
            "type",
            "title",
            "description",
            "sensitive",
            "required",
            "default",
            "multiple",
            "min",
            "max",
        }
    )
    required_sub = frozenset({"type", "title", "description"})
    for key, entry in uc.items():
        if not isinstance(key, str) or not _IDENTIFIER_RE.match(key):
            report.major(
                f"'userConfig.{key}' key must be a valid identifier — needed for the "
                "CLAUDE_PLUGIN_OPTION_<KEY> env-var export",
                ".claude-plugin/plugin.json",
            )
            continue
        if not isinstance(entry, dict):
            # Inline validator already reports a MAJOR — do not duplicate.
            continue

        # v2.1.121 — required sub-fields.
        for req in required_sub:
            if req not in entry:
                report.major(
                    f"'userConfig.{key}' missing required sub-field '{req}' (spec requires type, title, description)",
                    ".claude-plugin/plugin.json",
                )

        # type — enum validation.
        if "type" in entry:
            t = entry["type"]
            if not isinstance(t, str):
                report.major(
                    f"'userConfig.{key}.type' must be a string, got {type(t).__name__}",
                    ".claude-plugin/plugin.json",
                )
            elif t not in USER_CONFIG_TYPE_ENUM:
                report.major(
                    f"'userConfig.{key}.type' = {t!r} is not a valid type "
                    f"(expected one of: {sorted(USER_CONFIG_TYPE_ENUM)})",
                    ".claude-plugin/plugin.json",
                )

        # title — must be a non-empty string.
        if "title" in entry:
            title = entry["title"]
            if not isinstance(title, str) or not title.strip():
                report.major(
                    f"'userConfig.{key}.title' must be a non-empty string",
                    ".claude-plugin/plugin.json",
                )

        # description — optional per spec; type-checked when present.
        if "description" in entry and not isinstance(entry["description"], str):
            report.major(
                f"'userConfig.{key}.description' must be a string, got {type(entry['description']).__name__}",
                ".claude-plugin/plugin.json",
            )

        # sensitive / required / multiple — boolean.
        for bool_field in ("sensitive", "required", "multiple"):
            if bool_field in entry and not isinstance(entry[bool_field], bool):
                report.major(
                    f"'userConfig.{key}.{bool_field}' must be a boolean, got {type(entry[bool_field]).__name__}",
                    ".claude-plugin/plugin.json",
                )

        # min / max — only meaningful for type: number.
        for num_field in ("min", "max"):
            if num_field in entry:
                v = entry[num_field]
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    report.major(
                        f"'userConfig.{key}.{num_field}' must be a number, got {type(v).__name__}",
                        ".claude-plugin/plugin.json",
                    )
                elif entry.get("type") not in (None, "number"):
                    report.minor(
                        f"'userConfig.{key}.{num_field}' set on non-number type "
                        f"({entry.get('type')!r}) — only meaningful for type: number",
                        ".claude-plugin/plugin.json",
                    )

        # multiple is only meaningful for type: string per spec.
        if entry.get("multiple") is True and entry.get("type") not in (None, "string"):
            report.minor(
                f"'userConfig.{key}.multiple' set on non-string type "
                f"({entry.get('type')!r}) — only meaningful for type: string",
                ".claude-plugin/plugin.json",
            )

        # Unknown sub-fields — MINOR so authors notice typos.
        for extra in set(entry.keys()) - known_sub:
            report.minor(
                f"'userConfig.{key}.{extra}' is not a recognized sub-field (recognized: {sorted(known_sub)})",
                ".claude-plugin/plugin.json",
            )


_PLUGIN_ROOT_DIR_PATTERN = re.compile(r"\$\{?CLAUDE_PLUGIN_ROOT\}?[/\\]+([A-Za-z0-9_.\-]+)[/\\]")


def _extract_referenced_dirs_from_text(text: str) -> set[str]:
    """Find folder names referenced as `${CLAUDE_PLUGIN_ROOT}/<dir>/...` in text.

    Returns the lowercase set of distinct first-level folder names found. Used to
    discover plugin-bundled folders that the manifest legitimately uses, so the
    "non-standard directory" warning doesn't false-positive on e.g. `mcp-server/`
    when `.mcp.json` has `"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/mcp-server/index.js"]`.
    """
    return {m.group(1).lower() for m in _PLUGIN_ROOT_DIR_PATTERN.finditer(text)}


def _walk_for_command_args(node: Any) -> list[str]:
    """Recursively collect string values from `command` / `args` keys in nested dicts/lists."""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("command", "url") and isinstance(v, str):
                out.append(v)
            elif k == "args" and isinstance(v, list):
                out.extend(s for s in v if isinstance(s, str))
            elif k == "env" and isinstance(v, dict):
                out.extend(s for s in v.values() if isinstance(s, str))
            else:
                out.extend(_walk_for_command_args(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(_walk_for_command_args(item))
    return out


def _collect_manifest_referenced_dirs(plugin_root: Path) -> set[str]:
    """Discover plugin-bundled folders referenced from the manifest.

    Scans .mcp.json, .lsp.json, hooks/hooks.json, monitors/monitors.json, and
    plugin.json's inline mcpServers/lspServers/hooks/monitors fields for
    `${CLAUDE_PLUGIN_ROOT}/<dirname>/...` patterns. Returns the lowercase set of
    distinct first-level folder names found. Failures (missing file, malformed
    JSON, etc.) are silently ignored — this is a hint generator, not a validator.
    """
    referenced: set[str] = set()

    def _safe_load(p: Path) -> Any:
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # Standard root-level config files
    for cfg_path in (
        plugin_root / ".mcp.json",
        plugin_root / ".lsp.json",
        plugin_root / "hooks" / "hooks.json",
        plugin_root / "monitors" / "monitors.json",
    ):
        data = _safe_load(cfg_path)
        if data is not None:
            for s in _walk_for_command_args(data):
                referenced |= _extract_referenced_dirs_from_text(s)

    # Inline plugin.json fields (mcpServers / lspServers / hooks / monitors)
    manifest = _safe_load(plugin_root / ".claude-plugin" / "plugin.json")
    if isinstance(manifest, dict):
        for field in ("mcpServers", "lspServers", "hooks", "monitors", "channels"):
            value = manifest.get(field)
            if value is None:
                continue
            # Inline object/array: walk it directly
            if isinstance(value, (dict, list)):
                for s in _walk_for_command_args(value):
                    referenced |= _extract_referenced_dirs_from_text(s)
            # Path string: also load the referenced file (if it exists) and walk it
            if isinstance(value, str):
                ref_path = value
                if ref_path.startswith("./"):
                    ref_path = ref_path[2:]
                ref_file = plugin_root / ref_path
                ref_data = _safe_load(ref_file)
                if ref_data is not None:
                    for s in _walk_for_command_args(ref_data):
                        referenced |= _extract_referenced_dirs_from_text(s)
            # Array of path strings (e.g. mcpServers: ["./a.json", "./b.json"])
            elif isinstance(value, list):
                for entry in value:
                    if not isinstance(entry, str):
                        continue
                    ref_path = entry[2:] if entry.startswith("./") else entry
                    ref_file = plugin_root / ref_path
                    ref_data = _safe_load(ref_file)
                    if ref_data is not None:
                        for s in _walk_for_command_args(ref_data):
                            referenced |= _extract_referenced_dirs_from_text(s)

    return referenced


def _mcp_server_keys(manifest: dict[str, Any], plugin_root: Path) -> set[str] | None:
    """Resolve the set of declared MCP server names.

    Returns ``None`` when the set cannot be determined (e.g. ``mcpServers``
    is a path string that cannot be loaded) so callers can skip cross-ref
    checks rather than emit false-positive MAJORs.
    """
    if "mcpServers" not in manifest:
        return set()
    mcp = manifest["mcpServers"]
    if isinstance(mcp, dict):
        # Inline object — either {name: config, ...} directly, or the MCP-standard
        # wrapper shape {"mcpServers": {name: config, ...}}.
        if "mcpServers" in mcp and isinstance(mcp["mcpServers"], dict):
            return set(mcp["mcpServers"].keys())
        return set(mcp.keys())
    if isinstance(mcp, str):
        mcp_path = (plugin_root / mcp.lstrip("./")).resolve()
        if not mcp_path.is_file():
            return None
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if isinstance(data, dict):
            if "mcpServers" in data and isinstance(data["mcpServers"], dict):
                return set(data["mcpServers"].keys())
            return set(data.keys())
        return None
    return None


def validate_channels_structure(manifest: dict[str, Any], plugin_root: Path, report: ValidationReport) -> None:
    """Validate the ``channels`` array per plugins-reference.md:438-455.

    Each entry is a dict with required ``server`` (string). ``server`` MUST
    match a key in the plugin's ``mcpServers``. ``mcpServers`` may be an
    inline dict or a path string pointing at an MCP config — when it's a path
    we try to resolve it from ``plugin_root``; if the file cannot be loaded
    we skip the cross-reference check rather than emit a false positive.

    The optional per-entry ``userConfig`` follows the same schema as the
    top-level one; we validate structure inline since the helper is scoped
    to the root manifest.
    """
    if "channels" not in manifest:
        return
    channels = manifest["channels"]
    if not isinstance(channels, list):
        report.major(
            f"'channels' must be an array, got {type(channels).__name__} (plugins-reference.md:438)",
            ".claude-plugin/plugin.json",
        )
        return
    mcp_keys = _mcp_server_keys(manifest, plugin_root)
    for i, entry in enumerate(channels):
        if not isinstance(entry, dict):
            report.major(
                f"'channels[{i}]' must be an object (plugins-reference.md:438-455)",
                ".claude-plugin/plugin.json",
            )
            continue
        # server — required + cross-reference
        if "server" not in entry:
            report.major(
                f"'channels[{i}]' missing required 'server' field (plugins-reference.md:438-455)",
                ".claude-plugin/plugin.json",
            )
        elif not isinstance(entry["server"], str):
            report.major(
                f"'channels[{i}].server' must be a string, got {type(entry['server']).__name__}",
                ".claude-plugin/plugin.json",
            )
        elif mcp_keys is not None and entry["server"] not in mcp_keys:
            # mcp_keys may be empty (no mcpServers declared) — still a MAJOR
            # because channels[].server MUST reference an existing MCP server.
            report.major(
                f"'channels[{i}].server' = '{entry['server']}' does not match any key in mcpServers "
                "(plugins-reference.md:438-455)",
                ".claude-plugin/plugin.json",
            )
        # per-channel userConfig — optional; reuse identifier + type checks.
        if "userConfig" in entry:
            cuc = entry["userConfig"]
            if not isinstance(cuc, dict):
                report.major(
                    f"'channels[{i}].userConfig' must be an object, got {type(cuc).__name__}",
                    ".claude-plugin/plugin.json",
                )
            else:
                for ck, cv in cuc.items():
                    if not isinstance(ck, str) or not _IDENTIFIER_RE.match(ck):
                        report.major(
                            f"'channels[{i}].userConfig.{ck}' key must be a valid identifier",
                            ".claude-plugin/plugin.json",
                        )
                    if isinstance(cv, dict):
                        if "description" in cv and not isinstance(cv["description"], str):
                            report.major(
                                f"'channels[{i}].userConfig.{ck}.description' must be a string",
                                ".claude-plugin/plugin.json",
                            )
                        if "sensitive" in cv and not isinstance(cv["sensitive"], bool):
                            report.major(
                                f"'channels[{i}].userConfig.{ck}.sensitive' must be a boolean",
                                ".claude-plugin/plugin.json",
                            )


def _read_skill_md_name(skill_md: Path) -> str | None:
    """Return the ``name`` frontmatter value of a SKILL.md file, or None.

    Fail-safe by design: any read or parse error yields None so skill
    discovery never crashes on a malformed file — the skill validator is
    the surface that reports the actual defect.
    """
    try:
        content = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    if isinstance(frontmatter, dict):
        name = frontmatter.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _discover_plugin_skills(plugin_root: Path) -> set[str]:
    """Return the set of skill names declared by this plugin.

    GAP-10 helper (v2.22.3): scans ``<plugin>/skills/<skill>/SKILL.md`` so
    the monitors validator can cross-reference ``on-skill-invoke:<skill>``
    targets against actually-declared skills.

    CC v2.1.142: when the plugin has no ``skills/`` subdirectory, a
    root-level ``SKILL.md`` is surfaced as a skill — its invocable name is
    the ``name`` frontmatter field, so it is included here too.
    """
    skills_dir = plugin_root / "skills"
    if not skills_dir.is_dir():
        root_skill_md = plugin_root / "SKILL.md"
        if root_skill_md.is_file():
            name = _read_skill_md_name(root_skill_md)
            if name:
                return {name}
        return set()
    discovered: set[str] = set()
    for entry in skills_dir.iterdir():
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            discovered.add(entry.name)
    return discovered


def _validate_monitors_array(
    entries: list[Any],
    source_label: str,
    report: ValidationReport,
    declared_skills: set[str] | None = None,
) -> None:
    """Shared per-entry validator for monitors arrays (inline or external file).

    ``declared_skills`` is the set of skill names declared by the hosting plugin.
    When supplied, ``on-skill-invoke:<name>`` targets are cross-referenced
    against the declared set; a MINOR is emitted when the referenced skill
    does not exist (GAP-10). Pass ``None`` to skip the check (e.g. when the
    caller cannot determine the plugin root).
    """
    seen: set[str] = set()
    known = {"name", "command", "description", "when"}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            report.major(
                f"monitors[{i}] must be an object (plugins-reference.md:268-318)",
                source_label,
            )
            continue
        # name — required + unique
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            report.major(
                f"monitors[{i}] missing required 'name' field (plugins-reference.md:302-318)",
                source_label,
            )
        elif name in seen:
            report.major(
                f"monitors[{i}] duplicate 'name' = '{name}' — monitor names must be unique",
                source_label,
            )
        else:
            seen.add(name)
        # command — required
        if not isinstance(entry.get("command"), str) or not entry.get("command"):
            report.major(
                f"monitors[{i}] missing required 'command' field (plugins-reference.md:302-318)",
                source_label,
            )
        # description — required
        if not isinstance(entry.get("description"), str) or not entry.get("description"):
            report.major(
                f"monitors[{i}] missing required 'description' field (plugins-reference.md:302-318)",
                source_label,
            )
        # when — optional; must match "always" or "on-skill-invoke:<name>"
        if "when" in entry:
            when_val = entry["when"]
            if not isinstance(when_val, str) or not _MONITOR_WHEN_RE.match(when_val):
                report.major(
                    f"monitors[{i}].when = {when_val!r} must match 'always' or "
                    "'on-skill-invoke:<skill-name>' (plugins-reference.md:302-318)",
                    source_label,
                )
            elif declared_skills is not None and isinstance(when_val, str) and when_val.startswith("on-skill-invoke:"):
                # GAP-10 (v2.22.3): cross-reference the skill name against
                # declared skills. Empty declared_skills means the plugin
                # has no skills/ directory at all — still report so authors
                # notice the dangling reference.
                target = when_val.split(":", 1)[1]
                if target and target not in declared_skills:
                    report.minor(
                        f"monitors[{i}].when references unknown skill "
                        f"'{target}' — no skills/{target}/SKILL.md found "
                        "(plugins-reference.md:314)",
                        source_label,
                    )
        # unknown keys — MINOR
        if isinstance(entry, dict):
            for extra in set(entry.keys()) - known:
                report.minor(
                    f"monitors[{i}].{extra} is not a recognized monitor field "
                    "(recognized: name, command, description, when)",
                    source_label,
                )


def validate_monitors_entries(manifest: dict[str, Any], plugin_root: Path, report: ValidationReport) -> None:
    """Validate the ``monitors`` entries per plugins-reference.md:268-318.

    ``monitors`` may be inline in plugin.json OR a path string pointing at a
    ``monitors.json`` file. Either shape is an array of dicts requiring
    ``name`` (unique), ``command``, and ``description``. Optional ``when``
    must match the ``always``/``on-skill-invoke:<name>`` grammar.
    """
    if "monitors" not in manifest:
        return
    monitors = manifest["monitors"]
    declared_skills = _discover_plugin_skills(plugin_root)
    if isinstance(monitors, list):
        _validate_monitors_array(monitors, ".claude-plugin/plugin.json", report, declared_skills)
        return
    if isinstance(monitors, str):
        # Path string — resolve relative to plugin_root and load.
        rel = monitors.lstrip("./")
        monitors_path = (plugin_root / rel).resolve()
        if not monitors_path.is_file():
            # Missing file is already flagged elsewhere (path validator);
            # we only check contents when the file actually exists.
            return
        try:
            data = json.loads(monitors_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            report.major(f"monitors file could not be parsed: {err}", monitors)
            return
        # monitors.json can be an array or {monitors: [...]} wrapper.
        if isinstance(data, list):
            _validate_monitors_array(data, monitors, report, declared_skills)
        elif isinstance(data, dict) and isinstance(data.get("monitors"), list):
            _validate_monitors_array(data["monitors"], monitors, report, declared_skills)
        else:
            report.major(
                f"monitors file must contain an array or {{'monitors': [...]}} wrapper, got {type(data).__name__}",
                monitors,
            )
        return
    report.major(
        f"'monitors' must be an array or path string, got {type(monitors).__name__} (plugins-reference.md:268-318)",
        ".claude-plugin/plugin.json",
    )


def validate_layout_c_consistency(
    plugin_root: Path,
    report: ValidationReport,
) -> None:
    """Validate Layout C (marketplace-in-plugin) cross-consistency.

    Layout C exists when ONE root holds BOTH `.claude-plugin/plugin.json`
    AND `.claude-plugin/marketplace.json`. The marketplace must list the
    plugin's own name (self-reference) and version must match across the
    two manifests.

    Per references/marketplace-layouts.md§"Layout C", the rules are:
      1. plugin.json.name MUST appear in marketplace.json.plugins[].name
      2. The self-referenced plugin entry MUST use source: "./" (relative).
      3. plugin.json.version MUST equal marketplace.json.plugins[<self>].version
         (when both are set).

    Severities are MAJOR for hard mismatches (would break install) and
    MINOR for soft drift (cosmetic / future-confusion).
    """
    plugin_path = plugin_root / ".claude-plugin" / "plugin.json"
    market_path = plugin_root / ".claude-plugin" / "marketplace.json"
    if not plugin_path.is_file() or not market_path.is_file():
        return  # Not Layout C — single-manifest plugins are unaffected.

    try:
        plugin_obj = json.loads(plugin_path.read_text(encoding="utf-8"))
        market_obj = json.loads(market_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return  # Per-manifest validators already report parse errors.

    plugin_name = plugin_obj.get("name") if isinstance(plugin_obj, dict) else None
    plugin_version = plugin_obj.get("version") if isinstance(plugin_obj, dict) else None
    if not plugin_name:
        return  # Per-manifest validator already flagged missing name.

    plugins_arr = market_obj.get("plugins") if isinstance(market_obj, dict) else None
    if not isinstance(plugins_arr, list):
        return

    self_entry = None
    for entry in plugins_arr:
        if isinstance(entry, dict) and entry.get("name") == plugin_name:
            self_entry = entry
            break

    if self_entry is None:
        report.major(
            f"Layout C: plugin.json declares name='{plugin_name}' but "
            f"marketplace.json's plugins[] does not list a self-reference "
            f"with that name. Add `{{name: '{plugin_name}', source: './'}}` "
            f"to marketplace.json's plugins array, or remove marketplace.json "
            f"if this is meant to be a plain plugin.",
            ".claude-plugin/marketplace.json",
        )
        return

    # Rule 2 — source must be "./" (relative)
    src = self_entry.get("source")
    src_ok = src == "./" or src == "." or (isinstance(src, str) and src.strip() in ("./", "."))
    if not src_ok:
        report.major(
            f"Layout C: marketplace.json's self-reference for plugin "
            f"'{plugin_name}' has source={src!r}; must be './' (relative) "
            f"so install resolves to the same repo. Other source types "
            f"would re-clone the repository.",
            ".claude-plugin/marketplace.json",
        )

    # Rule 3 — version consistency
    self_version = self_entry.get("version")
    if plugin_version and self_version and plugin_version != self_version:
        report.minor(
            f"Layout C: plugin.json version '{plugin_version}' differs from "
            f"marketplace.json plugins[{plugin_name}].version '{self_version}'. "
            f"Bump both together to keep installation metadata consistent.",
            ".claude-plugin/marketplace.json",
        )

    # v2.81.0 (TRDD-c0ee9543, Phase B / GAP-13) — also use the shared
    # diff helper so description / author / keywords / homepage drift
    # between the two manifests surfaces. The helper emits NIT for
    # those fields (cosmetic), MAJOR for name (already covered above
    # by the self-entry-presence check), MINOR for version (already
    # covered above by Rule 3 — the helper will not double-report
    # because we short-circuit via opt-out logic below).
    try:
        from cpv_upstream_plugin_json import diff_marketplace_vs_upstream  # noqa: PLC0415
    except ImportError:
        return  # Module not available — pre-Phase-B install; nothing to add.

    # Don't double-emit NAME-MISMATCH or VERSION-DRIFT — those map to
    # the rules above. We only forward metadata drift findings.
    drifts = diff_marketplace_vs_upstream(self_entry, plugin_obj if isinstance(plugin_obj, dict) else {})
    for drift in drifts:
        if drift.code == "RC-MKPL-METADATA-DRIFT":
            report.nit(drift.message, ".claude-plugin/marketplace.json")


def validate_manifest(
    plugin_root: Path,
    report: ValidationReport,
    marketplace_only: bool = False,
    hosting_marketplace: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Validate plugin.json manifest.

    Args:
        plugin_root: Path to the plugin directory
        report: ValidationReport to add results to
        marketplace_only: If True, skip plugin.json requirement
        hosting_marketplace: Parsed ``marketplace.json`` of the hosting
            marketplace (TRDD-20108ab7). Used to check cross-marketplace
            dependencies against the marketplace's
            ``allowedDependencyMarketplaces`` allowlist. ``None`` skips the
            cross-marketplace allowlist check (INFO emitted per cross-dep).

    Returns:
        The manifest dict if valid, None otherwise
    """
    manifest_path = plugin_root / ".claude-plugin" / "plugin.json"

    if not manifest_path.exists():
        if marketplace_only:
            msg = "plugin.json correctly absent (marketplace-only, strict=false)"
            report.passed(msg, ".claude-plugin/plugin.json")
            return None
        # GAP-27 (v2.22.3): plugin.json is OPTIONAL when components exist in default
        # directories per plugins-reference.md:374-385 — "If you include a manifest,
        # `name` is the only required field." Downgrade CRITICAL→MINOR when ANY of
        # the auto-discovered default directories has content. A plugin with
        # only commands/ is perfectly valid and the plugin name is derived from
        # the directory name per plugins-reference.md:341.
        default_component_dirs = (
            "commands",
            "skills",
            "agents",
            "hooks",
            "rules",
            "monitors",
            "output-styles",
        )
        has_components = any(
            (plugin_root / d).is_dir() and any((plugin_root / d).iterdir()) for d in default_component_dirs
        )
        if has_components:
            report.minor(
                "plugin.json not found — plugin is valid because components exist in "
                "default directories, but adding a manifest is recommended for "
                "discoverability and version control (plugins-reference.md:374-385)",
                ".claude-plugin/plugin.json",
            )
            return None
        report.critical(
            "plugin.json not found and no components in default directories "
            "(commands/, skills/, agents/, hooks/, rules/, monitors/, output-styles/)",
            ".claude-plugin/plugin.json",
        )
        return None

    if marketplace_only:
        report.major(
            "plugin.json EXISTS but should NOT for marketplace-only (strict=false). Remove .claude-plugin/plugin.json to fix CLI uninstall issues.",
            ".claude-plugin/plugin.json",
        )
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        report.critical(f"Invalid JSON in plugin.json: {e}", ".claude-plugin/plugin.json")
        return None

    report.passed("plugin.json is valid JSON", ".claude-plugin/plugin.json")

    # Required field: name (per Anthropic docs, ONLY 'name' is required)
    if "name" not in manifest:
        report.critical(
            "Missing required field 'name' in plugin.json",
            ".claude-plugin/plugin.json",
        )
    else:
        report.passed("Required field 'name' present", ".claude-plugin/plugin.json")

    # Recommended fields
    recommended_fields = ["version", "description"]
    for fld in recommended_fields:
        if fld not in manifest:
            report.minor(
                f"Missing recommended field '{fld}' in plugin.json",
                ".claude-plugin/plugin.json",
            )
        else:
            report.passed(
                f"Recommended field '{fld}' present",
                ".claude-plugin/plugin.json",
            )

    # Name validation — uses shared validate_component_name for uniform rules
    if "name" in manifest:
        name = manifest["name"]
        if isinstance(name, str):
            validate_component_name(name, "plugin", report)

    # Version validation — guard against non-string values (e.g. "version": 123)
    if "version" in manifest:
        version = manifest["version"]
        if not isinstance(version, str):
            report.major(
                f"Version must be a string, got {type(version).__name__}: {version}",
                ".claude-plugin/plugin.json",
            )
        elif not re.match(r"^\d+\.\d+\.\d+", version):
            report.major(
                f"Version must be semver format: {version}",
                ".claude-plugin/plugin.json",
            )

    # Check for unknown fields — warn but don't block, as custom fields
    # may be consumed by plugin scripts or external tooling.
    # Aligned with plugins-reference.md (v2.1.121).
    known_fields = {
        "name",
        "$schema",  # v2.1.120 — JSON-Schema link, ignored at load time
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "commands",
        "agents",
        "skills",
        "hooks",
        "mcpServers",
        "outputStyles",
        "themes",  # v2.1.118 — plugin-shipped theme JSON files under themes/
        "lspServers",
        "monitors",  # v2.1.105 — background monitor configs (monitors/monitors.json by default)
        "userConfig",  # User-configurable values prompted at enable time (v2.1.80)
        "channels",  # Channel declarations for message injection (v2.1.85)
        "dependencies",  # v2.1.110+ — plugin dependency declarations with semver ranges (see plugin-dependencies.md)
        # CPV-managed config block (TRDD-793ac32a strip-dev-parts). The
        # generator emits a `cpv.strip` block on every fresh scaffold, and
        # `cpv strip-dev-parts` reads it later. Allowlisted so CPV's own
        # creator output validates clean. Custom keys under `cpv.*` stay
        # under the same namespace per CPV ownership.
        "cpv",
        # v2.1.129 — preferred wrapper for opt-in/experimental features.
        # `themes` and `monitors` should now be declared under `experimental: { ... }`;
        # top-level placement still works but `claude plugin validate` warns.
        "experimental",
    }
    for key in manifest.keys():
        if key not in known_fields:
            report.warning(
                f"Unknown manifest field '{key}' — not part of the Claude Code plugin spec. If used by plugin scripts, consider documenting it.",
                ".claude-plugin/plugin.json",
            )

    # v2.1.129 — Recommend the `experimental: { themes, monitors }` wrapper.
    # Top-level `themes` and `monitors` are still honoured but `claude plugin
    # validate` emits a warning, so CPV mirrors that as a NIT (non-blocking
    # nudge) so authors discover the new shape without breaking existing files.
    experimental = manifest.get("experimental")
    for legacy_key in ("themes", "monitors"):
        # If author already nested the key under `experimental`, don't double-warn
        # on a top-level appearance — the CC loader prefers the nested copy.
        nested = isinstance(experimental, dict) and legacy_key in experimental
        if legacy_key in manifest and not nested:
            report.nit(
                f"'{legacy_key}' should be nested under 'experimental: {{ ... }}' "
                f"per v2.1.129. Top-level still works (claude plugin validate warns).",
                ".claude-plugin/plugin.json",
            )

    # When an `experimental` block is present, validate it's an object and only
    # contains recognised opt-in keys. Unknown keys inside `experimental` are
    # WARNINGs (the wrapper is a forward-compat surface, so we don't reject).
    if "experimental" in manifest:
        if not isinstance(experimental, dict):
            report.major(
                f"'experimental' must be an object, got {type(experimental).__name__} "
                "(plugins-reference.md / changelog v2.1.129)",
                ".claude-plugin/plugin.json",
            )
        else:
            known_experimental_keys = {"themes", "monitors"}
            for exp_key in experimental.keys():
                if exp_key not in known_experimental_keys:
                    report.warning(
                        f"Unknown 'experimental.{exp_key}' field — not part of the "
                        "Claude Code experimental opt-in surface (v2.1.129). "
                        f"Known keys: {sorted(known_experimental_keys)}.",
                        ".claude-plugin/plugin.json",
                    )

    # Validate repository field type — Claude Code requires a string URL, not an object
    if "repository" in manifest:
        repo_val = manifest["repository"]
        if not isinstance(repo_val, str):
            report.major(
                f'Field \'repository\' must be a string URL (e.g. "https://github.com/user/repo"), not {type(repo_val).__name__}. Claude Code rejects object format like {{"type":"git","url":"..."}}.',
                ".claude-plugin/plugin.json",
            )

    # Validate author field structure (plugins-reference.md:352 — object supports {name, email, url})
    if "author" in manifest:
        author = manifest["author"]
        if isinstance(author, str):
            report.passed("Author is a string (acceptable)", ".claude-plugin/plugin.json")
        elif isinstance(author, dict):
            if "name" not in author:
                report.major(
                    "'author' object missing required 'name' field",
                    ".claude-plugin/plugin.json",
                )
            elif not isinstance(author["name"], str):
                report.major(
                    "'author.name' must be a string",
                    ".claude-plugin/plugin.json",
                )
            else:
                report.passed("Author object has valid 'name' field", ".claude-plugin/plugin.json")
            # author.url (optional, v2.1.x — spec plugins-reference.md:352)
            if "url" in author and not isinstance(author["url"], str):
                report.major(
                    f"'author.url' must be a string, got {type(author['url']).__name__}",
                    ".claude-plugin/plugin.json",
                )
        else:
            report.major(
                f"'author' must be a string or object, got {type(author).__name__}",
                ".claude-plugin/plugin.json",
            )

    # Validate keywords field
    if "keywords" in manifest:
        kw = manifest["keywords"]
        if not isinstance(kw, list):
            report.major("'keywords' must be an array", ".claude-plugin/plugin.json")
        elif not all(isinstance(k, str) for k in kw):
            report.major("'keywords' must contain only strings", ".claude-plugin/plugin.json")
        else:
            report.passed(f"Keywords: {len(kw)} keyword(s)", ".claude-plugin/plugin.json")

    # Validate homepage and license field types
    for string_field in ("homepage", "license"):
        if string_field in manifest:
            val = manifest[string_field]
            if not isinstance(val, str):
                report.major(
                    f"'{string_field}' must be a string, got {type(val).__name__}",
                    ".claude-plugin/plugin.json",
                )

    # Validate component path fields start with ./
    # Also rejects `..` segments per plugins-reference.md:568-571 — paths escaping the
    # plugin root never resolve post-install because external files aren't copied to the cache.
    path_fields = [
        "commands",
        "agents",
        "skills",
        "hooks",
        "mcpServers",
        "outputStyles",
        "lspServers",
        "monitors",
    ]
    for key in path_fields:
        if key in manifest:
            value = manifest[key]
            if isinstance(value, str) and not value.startswith("./"):
                report.major(
                    f"Field '{key}' path must start with './': {value}",
                    ".claude-plugin/plugin.json",
                )
            if isinstance(value, str) and _path_has_traversal(value):
                report.major(
                    f"Field '{key}' contains path-traversal segment '..': {value} — "
                    "paths escaping the plugin root do not resolve post-install "
                    "(plugins-reference.md:568-571)",
                    ".claude-plugin/plugin.json",
                )
            elif isinstance(value, list):
                for i, path in enumerate(value):
                    if not isinstance(path, str):
                        report.major(
                            f"Field '{key}[{i}]' must be a string path, got {type(path).__name__}",
                            ".claude-plugin/plugin.json",
                        )
                    elif not path.startswith("./"):
                        report.major(
                            f"Field '{key}[{i}]' path must start with './': {path}",
                            ".claude-plugin/plugin.json",
                        )
                    elif _path_has_traversal(path):
                        report.major(
                            f"Field '{key}[{i}]' contains path-traversal segment '..': {path} — "
                            "paths escaping the plugin root do not resolve post-install "
                            "(plugins-reference.md:568-571)",
                            ".claude-plugin/plugin.json",
                        )
            elif isinstance(value, dict):
                # Inline configuration object - valid for hooks, mcpServers, lspServers
                if key in ("hooks", "mcpServers", "lspServers"):
                    report.passed(
                        f"Field '{key}' uses inline configuration object",
                        ".claude-plugin/plugin.json",
                    )
                else:
                    report.major(
                        f"Field '{key}' must be a string path or array, not an object",
                        ".claude-plugin/plugin.json",
                    )

    # Validate userConfig schema (v2.1.80): keys must be identifiers, each entry needs title + type.
    # Claude Code's runtime validator enforces 'title' as REQUIRED — issue #9 documented a v1.7.0
    # release of token-reporter that passed CPV but failed at install with:
    #   userConfig.<key>.title: Invalid input: expected string, received undefined
    # Issue #??? (2026-04-18): runtime ALSO enforces 'type' as REQUIRED via Zod .enum() — when
    # missing, Zod emits "userConfig.<key>.type: Invalid option: expected one of
    # \"string\"|\"number\"|\"boolean\"|\"directory\"|\"file\"". The docs-listed types
    # (integer/array/object) are NOT accepted by the runtime; the runtime accepts exactly 5.
    # Mirror the runtime schema strictly to catch this at validation time.
    USERCONFIG_VALID_TYPES = {"string", "number", "boolean", "directory", "file"}
    USERCONFIG_TYPE_TO_PYTHON: dict[str, tuple[type, ...]] = {
        "string": (str,),
        "number": (int, float),
        "boolean": (bool,),
        "directory": (str,),  # path string
        "file": (str,),  # path string
    }
    if "userConfig" in manifest:
        uc = manifest["userConfig"]
        if not isinstance(uc, dict):
            report.major(f"'userConfig' must be an object, got {type(uc).__name__}", ".claude-plugin/plugin.json")
        else:
            for key, entry in uc.items():
                if not isinstance(key, str) or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key):
                    report.major(f"'userConfig' key '{key}' must be a valid identifier", ".claude-plugin/plugin.json")
                if not isinstance(entry, dict):
                    report.major(
                        f"'userConfig.{key}' must be an object with 'title' and 'type'",
                        ".claude-plugin/plugin.json",
                    )
                    continue
                # title (REQUIRED — runtime rejects the manifest at install time without it)
                if "title" not in entry:
                    report.major(
                        f"'userConfig.{key}' missing required 'title' field — Claude Code runtime "
                        f"rejects this at install time",
                        ".claude-plugin/plugin.json",
                    )
                elif not isinstance(entry["title"], str):
                    report.major(
                        f"'userConfig.{key}.title' must be a string, got {type(entry['title']).__name__}",
                        ".claude-plugin/plugin.json",
                    )
                # description (recommended)
                if "description" not in entry:
                    report.minor(f"'userConfig.{key}' missing 'description' field", ".claude-plugin/plugin.json")
                elif not isinstance(entry["description"], str):
                    report.major(f"'userConfig.{key}.description' must be a string", ".claude-plugin/plugin.json")
                # type (REQUIRED — runtime rejects missing type with "Invalid option: expected one of ...")
                declared_type: str | None = None
                if "type" not in entry:
                    report.major(
                        f"'userConfig.{key}' missing required 'type' field — Claude Code runtime "
                        f"rejects this at install time with 'Invalid option: expected one of "
                        f'"string"|"number"|"boolean"|"directory"|"file"\'',
                        ".claude-plugin/plugin.json",
                    )
                elif not isinstance(entry["type"], str):
                    report.major(
                        f"'userConfig.{key}.type' must be a string, got {type(entry['type']).__name__}",
                        ".claude-plugin/plugin.json",
                    )
                elif entry["type"] not in USERCONFIG_VALID_TYPES:
                    report.major(
                        f"'userConfig.{key}.type' must be one of "
                        f"{sorted(USERCONFIG_VALID_TYPES)}, got {entry['type']!r} — "
                        f"Claude Code runtime rejects this at install time",
                        ".claude-plugin/plugin.json",
                    )
                else:
                    declared_type = entry["type"]
                # default (optional, but if both type and default present, types must match)
                if "default" in entry and declared_type is not None:
                    expected_py_types = USERCONFIG_TYPE_TO_PYTHON.get(declared_type, ())
                    default_value = entry["default"]
                    # bool is a subclass of int — exclude when checking number
                    is_match = isinstance(default_value, expected_py_types)
                    if declared_type == "number" and isinstance(default_value, bool):
                        is_match = False
                    if not is_match:
                        report.major(
                            f"'userConfig.{key}.default' type ({type(default_value).__name__}) "
                            f"does not match declared type ({declared_type})",
                            ".claude-plugin/plugin.json",
                        )
                # sensitive (optional, must be bool)
                if "sensitive" in entry and not isinstance(entry["sensitive"], bool):
                    report.major(f"'userConfig.{key}.sensitive' must be a boolean", ".claude-plugin/plugin.json")
            report.passed(f"'userConfig' schema valid: {len(uc)} config(s)", ".claude-plugin/plugin.json")

    # Validate channels schema (v2.1.85): server is required, must match mcpServers key
    if "channels" in manifest:
        ch = manifest["channels"]
        if not isinstance(ch, list):
            report.major(f"'channels' must be an array, got {type(ch).__name__}", ".claude-plugin/plugin.json")
        else:
            mcp_keys = set()
            if "mcpServers" in manifest and isinstance(manifest["mcpServers"], dict):
                mcp_keys = set(manifest["mcpServers"].keys())
            for i, entry in enumerate(ch):
                if not isinstance(entry, dict):
                    report.major(f"'channels[{i}]' must be an object", ".claude-plugin/plugin.json")
                    continue
                if "server" not in entry:
                    report.major(f"'channels[{i}]' missing required 'server' field", ".claude-plugin/plugin.json")
                elif not isinstance(entry["server"], str):
                    report.major(f"'channels[{i}].server' must be a string", ".claude-plugin/plugin.json")
                elif mcp_keys and entry["server"] not in mcp_keys:
                    report.major(
                        f"'channels[{i}].server' = '{entry['server']}' does not match any mcpServers key",
                        ".claude-plugin/plugin.json",
                    )
            if ch:
                report.passed(f"'channels' schema valid: {len(ch)} channel(s)", ".claude-plugin/plugin.json")

    # Validate lspServers required fields (command + extensionToLanguage) plus
    # optional-field type checks. GAP-65/66/67/68 (v2.22.3) tighten type-checks
    # for `settings`/`initializationOptions`/`workspaceFolder`/`restartOnCrash`/
    # `args`/`env` on the inline `lspServers` block so malformed values are
    # flagged here as MINOR without needing to invoke the full `validate_lsp`
    # helper (which operates on external `.lsp.json` files).
    if "lspServers" in manifest and isinstance(manifest["lspServers"], dict):
        for name, config in manifest["lspServers"].items():
            if isinstance(config, dict):
                if "command" not in config:
                    report.major(f"LSP server '{name}' missing required 'command' field", ".claude-plugin/plugin.json")
                if "extensionToLanguage" not in config:
                    report.major(
                        f"LSP server '{name}' missing required 'extensionToLanguage' field",
                        ".claude-plugin/plugin.json",
                    )
                elif isinstance(config["extensionToLanguage"], dict):
                    for ext in config["extensionToLanguage"]:
                        if not ext.startswith("."):
                            report.minor(
                                f"LSP server '{name}' extensionToLanguage key '{ext}' should start with '.'",
                                ".claude-plugin/plugin.json",
                            )
                # GAP-67 (v2.22.3): `args` must be a list of strings per
                # plugins-reference.md:243.
                if "args" in config:
                    args_val = config["args"]
                    if not isinstance(args_val, list):
                        report.minor(
                            f"LSP server '{name}' 'args' must be an array, got {type(args_val).__name__} "
                            "(plugins-reference.md:243)",
                            ".claude-plugin/plugin.json",
                        )
                    else:
                        for ai, arg in enumerate(args_val):
                            if not isinstance(arg, str):
                                report.minor(
                                    f"LSP server '{name}' args[{ai}] must be a string, got {type(arg).__name__}",
                                    ".claude-plugin/plugin.json",
                                )
                # GAP-68 (v2.22.3): `env` must be a dict with string values per
                # plugins-reference.md:245.
                if "env" in config:
                    env_val = config["env"]
                    if not isinstance(env_val, dict):
                        report.minor(
                            f"LSP server '{name}' 'env' must be an object, got {type(env_val).__name__} "
                            "(plugins-reference.md:245)",
                            ".claude-plugin/plugin.json",
                        )
                    else:
                        for env_key, env_value in env_val.items():
                            if not isinstance(env_value, str):
                                report.minor(
                                    f"LSP server '{name}' env[{env_key!r}] must be a string, "
                                    f"got {type(env_value).__name__}",
                                    ".claude-plugin/plugin.json",
                                )
                # GAP-65 (v2.22.3): `initializationOptions` must be an object.
                if "initializationOptions" in config:
                    init_val = config["initializationOptions"]
                    if not isinstance(init_val, dict):
                        report.minor(
                            f"LSP server '{name}' 'initializationOptions' must be an object, "
                            f"got {type(init_val).__name__} (plugins-reference.md:241-252)",
                            ".claude-plugin/plugin.json",
                        )
                # GAP-65 (v2.22.3): `settings` must be an object.
                if "settings" in config:
                    settings_val = config["settings"]
                    if not isinstance(settings_val, dict):
                        report.minor(
                            f"LSP server '{name}' 'settings' must be an object, "
                            f"got {type(settings_val).__name__} (plugins-reference.md:241-252)",
                            ".claude-plugin/plugin.json",
                        )
                # GAP-65 (v2.22.3): `workspaceFolder` must be a string.
                if "workspaceFolder" in config:
                    wf_val = config["workspaceFolder"]
                    if not isinstance(wf_val, str):
                        report.minor(
                            f"LSP server '{name}' 'workspaceFolder' must be a string, "
                            f"got {type(wf_val).__name__} (plugins-reference.md:241-252)",
                            ".claude-plugin/plugin.json",
                        )
                # GAP-66 (v2.22.3): `restartOnCrash` must be a boolean per
                # plugins-reference.md:251.
                if "restartOnCrash" in config:
                    roc_val = config["restartOnCrash"]
                    if not isinstance(roc_val, bool):
                        report.minor(
                            f"LSP server '{name}' 'restartOnCrash' must be a boolean, "
                            f"got {type(roc_val).__name__} (plugins-reference.md:251)",
                            ".claude-plugin/plugin.json",
                        )

    # Claude Code auto-discovers standard directories at the plugin root.
    # Empirically verified 2026-04-18:
    #   - For commands/skills/outputStyles: pointing the manifest field at the default
    #     directory (e.g. "skills": "./skills/") is accepted and works fine — the docs
    #     even endorse this for "include the default in your array to keep both" form.
    #     CPV downgrades this to a MINOR redundancy nudge (was previously CRITICAL —
    #     false positive).
    #   - For hooks: pointing at the default directory (`hooks: "./hooks/"`, the DIR
    #     not the file) IS rejected by CC's validator with `hooks: Invalid input`. CPV
    #     keeps CRITICAL for this case.
    #   - For agents: see the dedicated `agents`-folder check below — folder paths in
    #     `agents` are ALWAYS rejected by CC with `agents: Invalid input`.
    # See `skills/fix-validation/references/empirical-loading-bugs.md` for evidence.
    auto_discovered_defaults = {
        "commands": "./commands/",
        "agents": "./agents/",
        "skills": "./skills/",
        "hooks": "./hooks/",
        "outputStyles": "./output-styles/",
    }
    # Fields where pointing at the default DIRECTORY actually breaks plugin loading
    # (verified empirically — CC's validator rejects these with `Invalid input`).
    breaks_loading_when_default = {"hooks"}
    for key, default_path in auto_discovered_defaults.items():
        if key not in manifest:
            continue
        value = manifest[key]
        # String pointing to the default directory
        if isinstance(value, str):
            normalized = value.replace("\\", "/").rstrip("/") + "/"
            if normalized == default_path:
                if key in breaks_loading_when_default:
                    report.critical(
                        f"Field '{key}' points to '{default_path}' which Claude Code rejects "
                        f"with `{key}: Invalid input` — the plugin will not load. Remove it "
                        "from plugin.json — only non-standard paths need explicit declaration.",
                        ".claude-plugin/plugin.json",
                    )
                elif key == "agents":
                    # Agents-folder rejection is handled by the dedicated agents check
                    # below (which provides a richer error message). Skip here.
                    pass
                else:
                    # commands / skills / outputStyles: redundant but harmless.
                    report.minor(
                        f"Field '{key}' points to '{default_path}' which Claude Code "
                        "auto-discovers anyway. This declaration is redundant. Remove the "
                        "field from plugin.json (the default folder is scanned automatically).",
                        ".claude-plugin/plugin.json",
                    )
        # Array of files inside the default directory
        elif isinstance(value, list) and all(isinstance(p, str) and p.startswith(default_path) for p in value):
            if key in breaks_loading_when_default:
                report.critical(
                    f"Field '{key}' lists items inside '{default_path}' which Claude Code "
                    f"rejects with `{key}: Invalid input` — the plugin will not load. "
                    "Remove it from plugin.json.",
                    ".claude-plugin/plugin.json",
                )
            elif key == "agents":
                # Skip — the dedicated agents check below handles this.
                pass
            else:
                report.minor(
                    f"Field '{key}' lists items inside '{default_path}' which Claude Code "
                    "auto-discovers anyway. This is redundant. Remove the field from "
                    "plugin.json (or include only items OUTSIDE the default folder).",
                    ".claude-plugin/plugin.json",
                )

    # `agents` field empirical constraint (NOT in docs schema): Claude Code's manifest
    # validator rejects ANY folder path in the `agents` field with the cryptic message
    # "agents: Invalid input" — both string and array forms, default folder OR not.
    # Only `.md` file paths are accepted. The docs' own complete-schema example
    # ("./custom/agents/") would actually fail this check. The default folder ./agents/
    # is no exception — empirically `agents: "./agents/"` ALSO fails with `Invalid input`
    # (auto_discovered_defaults CRITICAL skips agents because this dedicated check
    # provides the richer message).
    # If a plugin author skips `claude plugin validate` and publishes with a folder path,
    # CC silently drops the agents at runtime — no error in --debug log, agents simply
    # don't appear. Pre-empt CC's cryptic error with a clear, actionable message.
    # Empirical evidence: TRDD-20260418 (cpv-agents-other-folder-test, cpv-agents-default-test).
    agents_value = manifest.get("agents")
    if agents_value is not None:
        agents_paths: list[str] = []
        if isinstance(agents_value, str):
            agents_paths = [agents_value]
        elif isinstance(agents_value, list):
            agents_paths = [p for p in agents_value if isinstance(p, str)]
        for path_str in agents_paths:
            normalized = path_str.replace("\\", "/")
            # A folder path either ends with "/" or has no .md extension on its last segment.
            looks_like_folder = normalized.endswith("/") or not normalized.lower().endswith(".md")
            if not looks_like_folder:
                continue
            normalized_with_slash = normalized.rstrip("/") + "/"
            is_default = normalized_with_slash == "./agents/"
            extra_default_note = (
                (
                    " (Note: the default ./agents/ folder is auto-discovered — just remove "
                    "the 'agents' field entirely from plugin.json.)"
                )
                if is_default
                else ""
            )
            report.major(
                f"Field 'agents' contains folder path '{path_str}' — Claude Code's manifest validator "
                f"REJECTS folder paths in the 'agents' field with the cryptic error 'agents: Invalid input' "
                f"(both string and array forms). Only '.md' file paths are accepted. If you skip validate "
                f"and publish, CC silently drops the agents at runtime with no error. "
                f"Fix: list specific .md files like ['./agents/reviewer.md', './agents/tester.md'] "
                f"instead of '{path_str}'.{extra_default_note} Note: the docs' own complete-schema example "
                f"('./custom/agents/') is incorrect — it would also be rejected.",
                ".claude-plugin/plugin.json",
            )

    # Check for duplicate hooks loading — Claude Code auto-discovers hooks/hooks.json,
    # so explicitly pointing to it in plugin.json triggers a runtime ERROR with a CASCADE:
    # not only does Claude Code log "Duplicate hooks file detected" at runtime, but the
    # error also disables the plugin's other capabilities such as MCP servers
    # (debug log: "Plugin not available for MCP: <plugin>@inline - error type: hook-load-failed").
    # `claude plugin validate` does NOT catch this, so CPV emits MAJOR to give the author
    # a chance to spot the silent partial-failure mode before publishing.
    # Empirical evidence: TRDD-20260418 (cpv-hooks-doublefire-test) — hook fires once
    # (CC dedupes), but plugin's MCP servers fail to load with "hook-load-failed".
    # Handles BOTH string form ("./hooks/hooks.json") AND array form (["./hooks/hooks.json"])
    # AND path normalization (./hooks/./hooks.json, hooks\\hooks.json on Windows, etc.).
    def _is_default_hooks_path(path: str) -> bool:
        """True if path resolves to the auto-discovered hooks/hooks.json default."""
        normalized = path.replace("\\", "/")
        # Collapse "./" and "//" path segments — common authoring slip-ups.
        # We don't follow symlinks; static path equivalence is sufficient for this check.
        parts = [p for p in normalized.split("/") if p and p != "."]
        return parts == ["hooks", "hooks.json"]

    hooks_value = manifest.get("hooks")
    hooks_paths_to_check: list[str] = []
    if isinstance(hooks_value, str):
        hooks_paths_to_check = [hooks_value]
    elif isinstance(hooks_value, list):
        hooks_paths_to_check = [p for p in hooks_value if isinstance(p, str)]
    for hooks_path in hooks_paths_to_check:
        if _is_default_hooks_path(hooks_path):
            report.major(
                f"Field 'hooks' contains '{hooks_path}' which resolves to the auto-discovered "
                "'hooks/hooks.json' default. At runtime this triggers 'Duplicate hooks file detected' "
                "AND the cascading 'hook-load-failed' error DISABLES this plugin's MCP servers "
                "(silent partial failure — `claude plugin validate` does not catch it). "
                "Fix: remove the 'hooks' field from plugin.json (the default file is loaded automatically), "
                "or point it at a NON-default path like './hooks/extra.json'.",
                ".claude-plugin/plugin.json",
            )
            break  # Only emit once even if listed in array — the message is the same.

    # v2.84.0 — Plugin.json key shadows the default component folder (CC v2.1.140).
    # When plugin.json sets one of {commands, agents, skills, outputStyles},
    # the default folder is silently ignored at runtime: only the items the
    # author explicitly listed are loaded. Files left in the default folder
    # but not listed never reach Claude Code. CC's own /doctor / `claude plugin
    # list` / /plugin views started warning about this in v2.1.140; CPV emits
    # the same warning so authors catch the shadowing pre-publish.
    #
    # Coverage rules: the explicit value is considered to cover the default
    # folder if it (a) IS the default folder path as a string, (b) is an
    # array containing the default folder path, or (c) is an array that
    # lists every loadable item inside the default folder.
    _DEFAULT_COMPONENT_FOLDERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        # (manifest_key, default_folder, file_extensions)
        ("commands", "commands", (".md",)),
        ("agents", "agents", (".md",)),
        ("outputStyles", "output-styles", (".md",)),
    )

    def _norm_path(p: str) -> str:
        """Canonicalize a plugin.json path to a relative POSIX path with no
        leading ``./`` and no trailing ``/``. Accepts both ``"./commands/"``
        and ``"commands"`` and normalizes them to ``"commands"``."""
        n = p.replace("\\", "/").strip().rstrip("/")
        while n.startswith("./"):
            n = n[2:]
        return n

    def _list_default_folder_files(folder: Path, exts: tuple[str, ...]) -> list[str]:
        """Return loadable items in ``folder`` as POSIX-style ``folder/name``
        strings (no leading ``./``), scanning only the top level."""
        if not folder.is_dir():
            return []
        items = sorted(
            p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts and not p.name.startswith(".")
        )
        return [f"{folder.name}/{n}" for n in items]

    def _list_default_skill_dirs(folder: Path) -> list[str]:
        """Return skill subdirs in ``./skills/`` that contain SKILL.md.
        Each returned path is normalized (no leading ``./``, no trailing ``/``)."""
        if not folder.is_dir():
            return []
        items: list[str] = []
        for sub in sorted(folder.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            # Skill folder is loadable if it contains SKILL.md (case-insensitive on macOS).
            for entry in sub.iterdir():
                if entry.is_file() and entry.name.lower() == "skill.md":
                    items.append(f"{folder.name}/{sub.name}")
                    break
        return items

    def _emit_shadow_warning(
        key: str,
        default_rel: str,
        shadowed: list[str],
    ) -> None:
        # Cap the listing to keep the message terminal-friendly.
        shown = shadowed if len(shadowed) <= 6 else shadowed[:6] + [f"... and {len(shadowed) - 6} more"]
        report.major(
            f"Field '{key}' is set in plugin.json — Claude Code v2.1.140+ silently ignores "
            f"the default '{default_rel}' folder when the matching key is declared. "
            f"{len(shadowed)} item(s) inside the default folder will NOT load at runtime: "
            f"{shown}. Fix: either remove the '{key}' field from plugin.json so the default "
            f"folder is auto-discovered, or add the missing entries to the explicit '{key}' "
            f"list. CC's /doctor, `claude plugin list`, and /plugin now surface this warning.",
            ".claude-plugin/plugin.json",
        )

    def _shadowed_items(value: Any, folder_name: str, default_contents: list[str]) -> list[str]:
        """Return default-folder items not reached by ``value``. Empty list
        means the explicit value already covers everything (no warning)."""
        if isinstance(value, str):
            covered = {_norm_path(value)}
        elif isinstance(value, list):
            covered = {_norm_path(p) for p in value if isinstance(p, str)}
        else:
            covered = set()
        # A bare-folder reference (e.g. "commands" or "./commands/") covers
        # all current AND future content in that folder.
        if folder_name in covered:
            return []
        return [item for item in default_contents if _norm_path(item) not in covered]

    for key, folder_name, exts in _DEFAULT_COMPONENT_FOLDERS:
        if key not in manifest:
            continue
        default_folder = plugin_root / folder_name
        default_contents = _list_default_folder_files(default_folder, exts)
        if not default_contents:
            continue
        shadowed = _shadowed_items(manifest[key], folder_name, default_contents)
        if shadowed:
            _emit_shadow_warning(key, f"./{folder_name}/", shadowed)

    # Skills are folder-based (./skills/<name>/SKILL.md). Same shadowing rule:
    # a 'skills' key in plugin.json suppresses auto-discovery of ./skills/.
    if "skills" in manifest:
        skills_folder = plugin_root / "skills"
        default_skills = _list_default_skill_dirs(skills_folder)
        if default_skills:
            shadowed = _shadowed_items(manifest["skills"], "skills", default_skills)
            if shadowed:
                _emit_shadow_warning("skills", "./skills/", shadowed)

    # v2.22.0 spec-parity helpers — dependencies, userConfig sub-fields, channels/mcp
    # cross-ref, and monitors entry shape. Each helper is a no-op when the corresponding
    # field is absent so unused manifests pay zero extra cost.
    # v2.22.3 (TRDD-20108ab7): dependencies receives hosting_marketplace context
    # so cross-marketplace refs can be checked against the allowlist.
    # v2.79+ (TRDD-20108ab7, 2026-05-10): when caller did NOT supply explicit
    # hosting_marketplace, attempt on-disk auto-discovery so end-users running
    # ``validate_plugin <path>`` with NO marketplace flag also get the
    # cross-marketplace enforcement (Layout C / Layout B / cache layout).
    # Explicit context always wins over auto-discovery (test:
    # test_validate_manifest_explicit_context_overrides_auto_discovery).
    effective_hosting = hosting_marketplace
    if effective_hosting is None and "dependencies" in manifest:
        # Only pay the discovery cost when the manifest actually has deps.
        # Manifests with no dependencies field never trigger the cross-mkt
        # path, so the parent-walk filesystem cost would be wasted.
        effective_hosting = discover_hosting_marketplace(plugin_root)
    validate_dependencies(manifest, report, hosting_marketplace=effective_hosting)
    validate_user_config_structure(manifest, report)
    validate_channels_structure(manifest, plugin_root, report)
    validate_monitors_entries(manifest, plugin_root, report)

    return cast(dict[str, Any], manifest)


def validate_structure(plugin_root: Path, report: ValidationReport, marketplace_only: bool = False) -> None:
    """Validate plugin directory structure.

    Args:
        plugin_root: Path to the plugin directory
        report: ValidationReport to add results to
        marketplace_only: If True, .claude-plugin directory is optional
    """
    claude_plugin_dir = plugin_root / ".claude-plugin"
    if not claude_plugin_dir.is_dir():
        if marketplace_only:
            msg = ".claude-plugin absent (marketplace-only, uses marketplace.json)"
            report.passed(msg)
        else:
            report.critical(".claude-plugin directory not found")
            return
    else:
        report.passed(".claude-plugin directory exists")

    # Components must be at root, NOT in .claude-plugin
    for component in ["commands", "agents", "skills", "hooks", "scripts", "schemas", "bin"]:
        wrong_path = plugin_root / ".claude-plugin" / component
        if wrong_path.exists():
            report.critical(f"{component}/ must be at plugin root, not in .claude-plugin/")

    # Common directories
    common_dirs = {
        "commands": "INFO",
        "agents": "INFO",
        "skills": "INFO",
        "hooks": "INFO",
        "scripts": "INFO",
        "docs": "INFO",
        "output-styles": "INFO",
        "bin": "INFO",  # plugins.md L192 — contents added to Bash PATH while plugin enabled
        "monitors": "INFO",  # plugins-reference.md — background monitor definitions
    }

    for d, level in common_dirs.items():
        if (plugin_root / d).is_dir():
            report.passed(f"{d}/ directory exists")
        else:
            if level == "INFO":
                report.info(f"Optional directory {d}/ not found")
            else:
                report.minor(f"Directory {d}/ not found")

    # Check for non-standard directories — warn but don't block, since users
    # may add folders like libs/, modules/, resources/ needed by scripts.
    # Also dynamically discover folders referenced from manifest fields
    # (.mcp.json, .lsp.json, hooks, monitors, plugin.json mcpServers/lspServers
    # commands+args) so e.g. `mcp-server/` referenced via
    # `${CLAUDE_PLUGIN_ROOT}/mcp-server/index.js` doesn't false-positive.
    known_dirs = {
        ".claude-plugin",
        ".git",
        ".jj",  # Jujutsu VCS metadata (v2.1.86)
        ".sl",  # Sapling VCS metadata (v2.1.86)
        ".github",
        "commands",
        "agents",
        "skills",
        "hooks",
        "scripts",
        "docs",
        "rules",
        "schemas",
        "bin",  # plugins.md L192 — executables on PATH while plugin enabled
        "monitors",  # plugins-reference.md — background monitor definitions (v2.1.105+)
        "servers",  # MCP server bundles per docs example: ${CLAUDE_PLUGIN_ROOT}/servers/db-server
        "templates",
        "tests",
        "test",  # singular variant
        # Common non-standard but legitimate dirs
        "lib",
        "libs",
        "modules",
        "resources",
        "assets",
        "data",
        "config",
        "configs",
        "examples",
        "samples",
        "references",
        # Developer tooling dirs
        "git-hooks",
        "fixtures",
        "vendor",
        "src",
        "dist",
        "build",
        "out",
        "target",
        "output-styles",
        "design",  # TRDD design docs (design/tasks/)
        "reports",  # v2.24.0 — mandated report output folder (gitignored; see cpv_validation_common.resolve_reports_dir())
        # Common dirs across many plugins (added v2.23.2 after empirical scan
        # of 160 installed plugins surfaced these as repeat false positives):
        "prompts",  # prompt templates (used by codex and most AI plugins)
        "demo",
        "demos",
        "eval",
        "evals",  # evaluation scripts (visualize, clean-viz)
        "node_modules",  # JavaScript dependencies — never publish, but common in dev caches
        "output",
        "outputs",
        "server",  # backend code (cc-plugin-viz, web-automation-suite)
        "public",  # public web assets (cc-plugin-viz)
        "static",  # static web assets
        "web",  # web frontend
        "shared",  # shared utilities
        "settings",  # plugin-managed settings (claude-code-settings)
        "guidances",  # AI guidance docs (claude-code-settings)
        "plugins",  # nested plugin defs (claude-code-settings)
        # Language source directories (plugins that ship native binaries often
        # bundle source for the platform-specific binaries in bin/):
        "rust",  # Rust source (perfect-skill-suggester, etc.)
        "go",  # Go source
        "python",  # Python source (less common when scripts/ exists, but seen)
        "node",  # Node.js source
        "ts",  # TypeScript source
        "js",  # JavaScript source
        "java",
        "kotlin",
        "swift",
        "ruby",
        "csharp",
        "cpp",
        "c",
    }
    referenced_dirs = _collect_manifest_referenced_dirs(plugin_root)

    # Submodule pattern: many plugins (especially Layout B nested ones) have a
    # subdirectory named after the plugin itself (e.g., `web-automation-suite/`
    # contains `web-automation-suite/`). Auto-allow this pattern. Read the plugin
    # name from .claude-plugin/plugin.json once.
    plugin_name_lower: str | None = None
    plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"
    if plugin_json_path.exists():
        try:
            pj = json.loads(plugin_json_path.read_text(encoding="utf-8"))
            if isinstance(pj, dict):
                pn = pj.get("name")
                if isinstance(pn, str):
                    plugin_name_lower = pn.lower()
        except (json.JSONDecodeError, OSError):
            pass

    # Also skip hidden dirs and _dev dirs
    for item in plugin_root.iterdir():
        if not item.is_dir():
            continue
        dirname = item.name
        if dirname.startswith(".") or dirname.endswith("_dev"):
            continue
        dirname_lower = dirname.lower()
        if dirname_lower in known_dirs:
            continue
        if dirname_lower in referenced_dirs:
            # Folder is legitimately used by the plugin's manifest (MCP, LSP, hooks,
            # or monitor commands reference `${CLAUDE_PLUGIN_ROOT}/<dirname>/...`).
            # No warning needed — its purpose is self-documented by the manifest.
            continue
        if plugin_name_lower and dirname_lower == plugin_name_lower:
            # Submodule pattern: subdirectory named after the plugin itself.
            # Common in Layout B nested marketplaces and dev-cached plugins.
            continue
        # Issue #16 category H: skip vendoring-conventional roots
        # (external/, vendor/, third_party/, node_modules/, etc.) AND any
        # directory listed as a submodule in .gitmodules. Also honor
        # `cpv.allow_root_dirs` allow-list in plugin.json for explicit
        # opt-out of edge-case directory names.
        if is_vendored_path(Path(dirname), plugin_root):
            continue
        cpv_cfg = load_cpv_config(plugin_root)
        allow_roots = cpv_cfg.get("allow_root_dirs", [])
        if isinstance(allow_roots, list) and dirname in allow_roots:
            continue
        # Severity: MAJOR (was WARNING). The user's directive: "NO DEVIATION
        # FROM THE STANDARD can be allowed unless you declare the custom
        # folder in plugin.json". An undeclared non-standard root folder
        # is the #1 source of "the plugin published but installs to
        # nothing" because the install pipeline only knows about the
        # standard component directories.
        report.major(
            f"[RC-NONSTD-DIR-001] Non-standard directory '{dirname}/' — not part "
            "of the plugin spec, and not declared in plugin.json's "
            "`cpv.allow_root_dirs`. Either move the contents under a standard "
            "component dir (skills/agents/commands/hooks/scripts/...) OR add "
            f"'{dirname}' to `cpv.allow_root_dirs` in .claude-plugin/plugin.json. "
            "Undeclared non-standard root dirs are the #1 cause of empty plugin "
            "installs because the install pipeline only loads from the standard "
            "directories."
        )

    # Validate plugin-shipped settings.json if present
    settings_path = plugin_root / "settings.json"
    if settings_path.exists():
        try:
            settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings_data, dict):
                report.major("settings.json: root must be a JSON object", "settings.json")
            else:
                # "agent" is the primary plugin-level setting; "extraKnownMarketplaces"
                # is the v2.1.80 inline-marketplace declaration validated separately below.
                # "subagentStatusLine" is the v2.1.x plugin-scoped override (plugins.md:278-288).
                # TRDD-e2b17a61: "strictKnownMarketplaces" added so it does not emit a
                # spurious "unrecognized key" MINOR — its actual scope violation
                # (admin-managed only) is reported as a MAJOR below.
                recognized_keys = {
                    "agent",
                    "extraKnownMarketplaces",
                    "strictKnownMarketplaces",
                    "subagentStatusLine",
                }
                has_unrecognized = False
                for key in settings_data:
                    if key not in recognized_keys:
                        has_unrecognized = True
                        report.minor(
                            f"settings.json: unrecognized key '{key}' — supported plugin settings: {', '.join(sorted(recognized_keys))}",
                            "settings.json",
                        )
                # Validate 'agent' value references a real agent file
                if "agent" in settings_data:
                    agent_val = settings_data["agent"]
                    if isinstance(agent_val, str) and agent_val:
                        agents_dir = plugin_root / "agents"
                        agent_file = agents_dir / f"{agent_val}.md"
                        if agents_dir.is_dir() and not agent_file.is_file():
                            report.minor(
                                f"settings.json 'agent' value '{agent_val}' does not match any agent file in agents/",
                                "settings.json",
                            )
                    elif not isinstance(agent_val, str):
                        report.major(
                            f"settings.json 'agent' must be a string, got {type(agent_val).__name__}", "settings.json"
                        )
                # TRDD-e2b17a61 — v2.1.80+: validate extraKnownMarketplaces /
                # strictKnownMarketplaces by delegating to the dedicated
                # settings-marketplace validator. Wiring fires for EITHER block so
                # authors get schema validation for whichever they ship. Results
                # merge into this plugin report so all findings land in a single
                # report.
                #
                # Open question 3 (TRDD-e2b17a61): both keys are scope-mismatched
                # when they live in a plugin-shipped settings.json:
                #   - extraKnownMarketplaces: USER/PROJECT-scope (silently ignored
                #     from plugins) → emit WARNING so the author knows the
                #     declaration is a no-op for end users.
                #   - strictKnownMarketplaces: ADMIN-MANAGED-only allowlist → emit
                #     MAJOR because the author may be relying on lockdown that
                #     will never fire.
                has_extra_kn_mp = "extraKnownMarketplaces" in settings_data
                has_strict_kn_mp = "strictKnownMarketplaces" in settings_data
                if has_extra_kn_mp or has_strict_kn_mp:
                    from validate_settings_marketplace import validate_settings_marketplace_file

                    sm_report = validate_settings_marketplace_file(settings_path)
                    report.merge(sm_report)

                    if has_extra_kn_mp:
                        report.warning(
                            "settings.json: 'extraKnownMarketplaces' is a USER/PROJECT-scope "
                            "key (settings.md). When shipped inside a plugin-shipped "
                            "settings.json it is silently ignored at runtime — Claude Code "
                            "only honours this block from user (~/.claude/settings.json) "
                            "or project (.claude/settings.json) scopes. Move the "
                            "declaration to your project README as installation guidance "
                            "instead of bundling it in the plugin.",
                            "settings.json",
                        )
                    if has_strict_kn_mp:
                        report.major(
                            "settings.json: 'strictKnownMarketplaces' is an "
                            "ADMIN-MANAGED-only key (cc_scope_rules.MANAGED_ONLY_KEYS, "
                            "managed-settings.md). Claude Code silently ignores this "
                            "block from any plugin-shipped settings.json — the author "
                            "is relying on lockdown enforcement that will NEVER fire. "
                            "Strict allowlists belong in /etc/claude-code/managed-settings.json "
                            "(Linux), /Library/Application Support/ClaudeCode/managed-settings.json "
                            "(macOS), or C:\\ProgramData\\ClaudeCode\\managed-settings.json (Windows).",
                            "settings.json",
                        )
                if not has_unrecognized:
                    report.passed("settings.json is valid", "settings.json")
                else:
                    report.passed("settings.json is parseable JSON", "settings.json")
        except json.JSONDecodeError as e:
            report.major(f"settings.json: JSON parse error: {e}", "settings.json")

    # Check that plugin has at least some actual content beyond just a manifest
    content_indicators = ["commands", "skills", "agents", "hooks", "scripts", "output-styles"]
    # CC v2.1.142: a root-level SKILL.md (with no skills/ subdir) is surfaced
    # as a skill, so it counts as plugin content on its own.
    file_indicators = [".mcp.json", ".lsp.json", "SKILL.md"]
    has_content = any((plugin_root / d).is_dir() for d in content_indicators) or any(
        (plugin_root / f).exists() for f in file_indicators
    )
    if not has_content:
        report.major(
            "Plugin has a manifest but no content — expected at least one of: "
            "commands/, skills/, agents/, hooks/, scripts/, .mcp.json, .lsp.json, "
            "or a root-level SKILL.md",
            ".claude-plugin/plugin.json",
        )

    # Check pyproject.toml for Python plugins
    has_py_scripts = (plugin_root / "scripts").is_dir() and any((plugin_root / "scripts").glob("*.py"))
    if has_py_scripts:
        if (plugin_root / "pyproject.toml").exists():
            report.passed("pyproject.toml exists")
        else:
            report.minor("pyproject.toml not found — recommended for Python plugins")
        if (plugin_root / ".python-version").exists():
            report.passed(".python-version exists")
        else:
            report.warning(".python-version not found — recommended for reproducible builds")


def validate_commands(plugin_root: Path, report: ValidationReport) -> None:
    """Validate command definitions."""
    commands_dir = plugin_root / "commands"

    if not commands_dir.is_dir():
        report.info("No commands/ directory found")
        return

    # Find all command files
    cmd_files = list(commands_dir.glob("*.md"))
    if not cmd_files:
        report.info("No command files (*.md) found in commands/")
        return

    report.info(f"Found {len(cmd_files)} command file(s)")

    for cmd_path in cmd_files:
        validate_command_file(cmd_path, report)


def validate_command_file(cmd_path: Path, report: ValidationReport) -> None:
    """Validate a single command file."""
    rel_path = f"commands/{cmd_path.name}"
    content = cmd_path.read_text(encoding="utf-8")

    # Check frontmatter
    if not content.startswith("---"):
        report.critical("No frontmatter in command file", rel_path)
        return

    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            report.critical("Malformed frontmatter (missing closing ---)", rel_path)
            return

        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        report.critical(f"Invalid YAML frontmatter: {e}", rel_path)
        return

    if not frontmatter:
        report.critical("Empty frontmatter", rel_path)
        return

    report.passed("Valid YAML frontmatter", rel_path)

    # Required fields
    if "name" not in frontmatter:
        report.critical("Missing 'name' in frontmatter", rel_path)
    else:
        expected_name = cmd_path.stem
        if frontmatter["name"] != expected_name:
            report.major(
                f"Command name '{frontmatter['name']}' doesn't match filename '{expected_name}'",
                rel_path,
            )

    if "description" not in frontmatter:
        report.major("Missing 'description' in frontmatter", rel_path)


def validate_agents(plugin_root: Path, report: ValidationReport) -> None:
    """Validate agent definitions."""
    agents_dir = plugin_root / "agents"

    if not agents_dir.is_dir():
        report.info("No agents/ directory found")
        return

    # Find all agent files
    agent_files = list(agents_dir.glob("*.md"))
    if not agent_files:
        report.info("No agent files (*.md) found in agents/")
        return

    report.info(f"Found {len(agent_files)} agent file(s)")

    for agent_path in agent_files:
        validate_agent_file(agent_path, report)


def validate_agent_file(agent_path: Path, report: ValidationReport) -> None:
    """Validate a single agent file."""
    rel_path = f"agents/{agent_path.name}"
    content = agent_path.read_text(encoding="utf-8")

    # Check frontmatter
    if not content.startswith("---"):
        report.critical("No frontmatter in agent file", rel_path)
        return

    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            report.critical("Malformed frontmatter (missing closing ---)", rel_path)
            return

        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        report.critical(f"Invalid YAML frontmatter: {e}", rel_path)
        return

    if not frontmatter:
        report.critical("Empty frontmatter", rel_path)
        return

    report.passed("Valid YAML frontmatter", rel_path)

    # Required fields for agents
    if "name" not in frontmatter:
        report.critical("Missing 'name' in frontmatter", rel_path)

    if "description" not in frontmatter:
        report.major("Missing 'description' in frontmatter", rel_path)

    # Plugin agents do NOT support hooks, mcpServers, or permissionMode (per official spec).
    # These are security restrictions — only project agents (.claude/agents/) can use them.
    # Uses the shared helper from cpv_validation_common so validate_agent.py and this
    # orchestrator call path emit identical messages.
    validate_plugin_shipped_restrictions(frontmatter, rel_path, report, is_plugin_shipped=True)

    # Validate TOC embedding — agent files must embed TOCs from referenced .md files
    validate_toc_embedding(content, agent_path, agent_path.parent, report)


def validate_hooks(plugin_root: Path, report: ValidationReport) -> None:
    """Validate hook configuration using comprehensive hook validator."""
    hooks_dir = plugin_root / "hooks"

    if not hooks_dir.is_dir():
        report.info("No hooks/ directory found")
        return

    hooks_json = hooks_dir / "hooks.json"
    if not hooks_json.exists():
        report.info("No hooks.json found")
        return

    # Use comprehensive hook validator
    hook_report = validate_hook_file(hooks_json, plugin_root)

    # Transfer all results to main report
    for result in hook_report.results:
        file_path = result.file
        if file_path:
            if file_path.startswith(str(plugin_root)):
                file_path = file_path[len(str(plugin_root)) + 1 :]
            if not file_path.startswith("hooks/"):
                file_path = f"hooks/{file_path}"
        else:
            file_path = "hooks/hooks.json"

        report.add(result.level, result.message, file_path, result.line)


def validate_mcp(plugin_root: Path, report: ValidationReport) -> None:
    """Validate MCP server configurations."""
    # Use comprehensive MCP validator
    mcp_report = validate_plugin_mcp(plugin_root)

    # Transfer all results to main report
    for result in mcp_report.results:
        report.add(result.level, result.message, result.file, result.line)


# TRDD-e3e74f69 telemetry hookup
def validate_telemetry(plugin_root: Path, report: ValidationReport) -> None:
    """Run the OTEL telemetry supply-chain sub-validator.

    Delegates to ``validate_telemetry.scan_plugin_for_telemetry`` and merges
    findings into the umbrella report. Catches the OTEL hazards introduced
    by ``monitoring-usage.md``: ``otelHeadersHelper`` in plugin settings
    (CRITICAL — periodic arbitrary code execution),
    ``OTEL_LOG_RAW_API_BODIES=1`` in plugin env (CRITICAL — full
    request/response exfil), prompt-exfil flags (MAJOR), endpoint hijack
    (MAJOR), and any plugin-shipped OTEL var (MINOR — telemetry config
    belongs in ``managed-settings.json``).

    The check stays silent when the plugin has no OTEL configuration at
    all — PASSED-only results from the standalone validator are dropped to
    avoid noise in the umbrella output for the 99% of plugins that don't
    ship telemetry config.
    """
    # PLC0415: import inside the function to avoid pulling validate_telemetry
    # at module import time. Multiple agents may add umbrella entries; this
    # keeps the import surface stable across merges.
    from validate_telemetry import scan_plugin_for_telemetry  # noqa: PLC0415

    tel_report = scan_plugin_for_telemetry(plugin_root)

    # Merge findings, filtering PASSED noise — the umbrella does not need
    # a separate "telemetry passed" line for every clean plugin.
    for result in tel_report.results:
        if result.level == "PASSED":
            continue
        report.add(result.level, result.message, result.file, result.line)


def _has_shebang(path: Path) -> bool:
    """Check if a file starts with a shebang (#!) line."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except Exception:
        return False


def validate_scripts(plugin_root: Path, report: ValidationReport) -> None:
    """Validate scripts/ structure — exec bits + shebangs ONLY.

    v2.64.0: the lint pieces that lived here (ruff / mypy / shellcheck /
    eslint / PSScriptAnalyzer / gofmt / cargo) moved to
    `cpv_lint_engine.lint_repo`, which is invoked by the main `validate()`
    flow as a separate REPO LINT phase. That gives us a single source of
    truth for linting and lets every linter resolve via uvx / bunx / npx /
    docker without polluting the host.

    What stays here: scripts/-specific structural checks that don't make
    sense at the whole-repo level — exec-bit verification on .sh/.bash
    files and shebang enforcement on script extensions.
    """
    scripts_dir = plugin_root / "scripts"

    if not scripts_dir.is_dir():
        report.info("No scripts/ directory found")
        return

    # --- Shell scripts (.sh, .bash) — exec-bit only; lint runs in REPO LINT ---
    sh_files = list(scripts_dir.glob("*.sh")) + list(scripts_dir.glob("*.bash"))
    for sh_file in sh_files:
        # os.access(..., X_OK) is unreliable on Windows (NTFS ACLs don't map
        # to POSIX exec bits), so skip the exec-bit check there. Users on
        # Windows won't be executing .sh scripts directly from PowerShell/cmd
        # anyway; the check is a Unix portability safeguard.
        if IS_WINDOWS:
            report.passed(
                f"Shell script present (exec bit not checked on Windows): {sh_file.name}",
                f"scripts/{sh_file.name}",
            )
        elif not os.access(sh_file, os.X_OK):
            report.major(f"Shell script not executable: {sh_file.name}", f"scripts/{sh_file.name}")
        else:
            report.passed(f"Shell script executable: {sh_file.name}", f"scripts/{sh_file.name}")

    # Check Python scripts with shebang are executable (Unix only)
    if not IS_WINDOWS:
        if scripts_dir.is_dir():
            for py_file in scripts_dir.glob("*.py"):
                if _has_shebang(py_file) and not os.access(py_file, os.X_OK):
                    report.warning(
                        f"scripts/{py_file.name} has shebang but is not executable — run: chmod +x scripts/{py_file.name}",
                        f"scripts/{py_file.name}",
                    )

    # Check shebangs on script files — scripts without shebangs may not run cross-platform
    shebang_extensions = {".py", ".sh", ".bash", ".rb", ".pl", ".php"}
    # __init__.py and _-prefixed files are module markers/internal modules — never need shebangs
    all_scripts = [
        f
        for f in scripts_dir.iterdir()
        if f.is_file()
        and f.suffix.lower() in shebang_extensions
        and f.name != "__init__.py"
        and not f.stem.startswith("_")
    ]
    scripts_missing_shebang = []
    for script in all_scripts:
        try:
            with open(script, errors="replace") as f:
                first_line = f.readline().rstrip("\n")
            if not first_line.startswith("#!"):
                scripts_missing_shebang.append(script.name)
        except (OSError, UnicodeDecodeError):
            pass
    if scripts_missing_shebang:
        report.minor(
            f"Scripts missing shebang (e.g. #!/usr/bin/env python3): {', '.join(sorted(scripts_missing_shebang))}. Without a shebang, scripts may not run correctly across platforms.",
            "scripts/",
        )


# =============================================================================
# Cross-Platform Compatibility Validation
# =============================================================================

# Script extensions and their platform availability
# Each entry: extension -> (language_name, available_platforms, notes)
SCRIPT_PLATFORM_MAP: dict[str, tuple[str, set[str], str]] = {
    ".sh": ("Bash/Shell", {"macos", "linux"}, "Not natively available on Windows"),
    ".bash": ("Bash", {"macos", "linux"}, "Not natively available on Windows"),
    ".zsh": ("Zsh", {"macos"}, "Not standard on Linux or Windows"),
    ".fish": ("Fish shell", set(), "Requires separate installation on all platforms"),
    ".ps1": ("PowerShell", {"windows"}, "Requires pwsh installation on macOS/Linux"),
    ".bat": ("Windows Batch", {"windows"}, "Not available on macOS or Linux"),
    ".cmd": ("Windows Batch", {"windows"}, "Not available on macOS or Linux"),
    ".nix": ("Nix", {"linux"}, "Not standard on macOS or Windows"),
}

# Cross-platform script languages (available everywhere with standard install)
CROSSPLATFORM_EXTENSIONS = {
    ".py",  # Python — widely available
    ".js",  # Node.js — widely available
    ".ts",  # TypeScript (via tsx/ts-node) — widely available
    ".mjs",  # ES module JavaScript
    ".cjs",  # CommonJS JavaScript
    ".rb",  # Ruby — often pre-installed on macOS
}

# Compiled binary extensions by platform
BINARY_PLATFORM_SUFFIXES: dict[str, str] = {
    # macOS
    "-darwin-arm64": "macOS ARM64 (Apple Silicon)",
    "-darwin-amd64": "macOS x86_64 (Intel)",
    "-darwin-x86_64": "macOS x86_64 (Intel)",
    "-darwin-universal": "macOS Universal",
    "-macos-arm64": "macOS ARM64 (Apple Silicon)",
    "-macos-amd64": "macOS x86_64 (Intel)",
    "-macos-x86_64": "macOS x86_64 (Intel)",
    # Linux
    "-linux-arm64": "Linux ARM64",
    "-linux-amd64": "Linux x86_64",
    "-linux-x86_64": "Linux x86_64",
    # Windows
    "-windows-arm64.exe": "Windows ARM64",
    "-windows-amd64.exe": "Windows x86_64",
    "-windows-x86_64.exe": "Windows x86_64",
}

# Minimum recommended platform set for compiled binaries
RECOMMENDED_PLATFORMS = {
    "macOS ARM64 (Apple Silicon)",
    "macOS x86_64 (Intel)",
    "Linux x86_64",
}

# Shebang interpreters that mark a file as an interpreted script rather than a compiled binary.
# Matches `#!/usr/bin/env python3`, `#!/bin/bash`, `#!/usr/bin/python3.12`, etc.
# `\b(name)[\d.]*` allows versioned interpreters like python3 / python3.12 / node18.
_SCRIPT_SHEBANG_RE = re.compile(r"^#!.*\b(python|bash|sh|node|deno|ruby|perl|pwsh|fish|zsh|tclsh)[\d.]*\b")


def _file_has_script_shebang(path: Path) -> bool:
    """Return True if the file starts with a shebang pointing at a known interpreter.

    Used to distinguish portable extensionless scripts (e.g. ``bin/my-tool``
    starting with ``#!/usr/bin/env python3``) from genuine compiled binaries
    that happen to lack an extension.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(256)
    except (OSError, PermissionError):
        return False
    if not head.startswith(b"#!"):
        return False
    try:
        first_line = head.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    except Exception:
        return False
    return bool(_SCRIPT_SHEBANG_RE.match(first_line))


def _is_python_venv(dirpath: Path) -> bool:
    """Detect Python virtual environments by structural markers, not name.

    A directory is a venv if it contains pyvenv.cfg (created by python -m venv
    and virtualenv). This catches venvs regardless of name (.venv, .windows_venv,
    .virtualenv, my_env, etc.).
    """
    # pyvenv.cfg is the canonical marker — always created by venv/virtualenv
    if (dirpath / "pyvenv.cfg").is_file():
        return True
    # Fallback: bin/activate (Unix) or Scripts/activate.bat (Windows)
    if (dirpath / "bin" / "activate").is_file():
        return True
    if (dirpath / "Scripts" / "activate.bat").is_file():
        return True
    return False


def validate_bin_executables(plugin_root: Path, report: ValidationReport) -> None:
    """Validate bin/ directory — executables added to Bash tool's PATH (v2.1.91).

    Files in bin/ are invokable as bare commands from the Bash tool while the
    plugin is enabled. Files that look like executables (no extension, or script
    extensions) must be executable. Data files, libraries, and configs are skipped.
    """
    bin_dir = plugin_root / "bin"
    if not bin_dir.is_dir():
        return

    bin_files = [f for f in bin_dir.iterdir() if f.is_file()]
    if not bin_files:
        report.info("bin/ directory exists but is empty")
        return

    # Extensions that indicate data/library files — skip executable check
    data_extensions = {
        ".dll",
        ".so",
        ".dylib",
        ".a",
        ".lib",
        ".o",
        ".obj",  # Libraries
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",  # Config
        ".txt",
        ".md",
        ".csv",
        ".log",  # Data/docs
        ".pem",
        ".crt",
        ".key",  # Certificates
        ".wasm",  # WebAssembly modules
    }
    # Extensions that indicate scripts — should be executable
    script_extensions = {".sh", ".bash", ".py", ".rb", ".pl", ".js", ".ts", ".ps1"}

    executable_count = 0
    for bin_file in bin_files:
        ext = bin_file.suffix.lower()
        if ext in data_extensions:
            continue  # Skip data/library files
        # Files with no extension or script extensions should be executable
        if ext == "" or ext in script_extensions:
            # os.access(..., X_OK) on Windows is unreliable: NTFS ACL checks
            # don't map to POSIX exec bits, and every file often reports as
            # executable. Skip the exec check on Windows to avoid false
            # positives/negatives; the user's chmod advice is Unix-only anyway.
            if IS_WINDOWS:
                executable_count += 1
                report.passed(
                    f"bin/{bin_file.name} present (exec bit not checked on Windows)",
                    f"bin/{bin_file.name}",
                )
            elif not os.access(bin_file, os.X_OK):
                report.minor(
                    f"bin/{bin_file.name} is not executable — if this is a command, run: chmod +x bin/{bin_file.name}",
                    f"bin/{bin_file.name}",
                )
            else:
                executable_count += 1
                report.passed(f"bin/{bin_file.name} is executable", f"bin/{bin_file.name}")

    if executable_count > 0:
        report.passed(f"bin/ directory: {executable_count} executable(s) found")


def validate_cross_platform(plugin_root: Path, report: ValidationReport) -> None:
    """Validate cross-platform compatibility of plugin scripts and binaries.

    Checks:
    1. Scripts using platform-specific languages get warnings
    2. Compiled source code without binaries or build script = MAJOR error
    3. Compiled binaries should cover all major platforms
    """
    # Collect all files across the entire plugin tree
    platform_specific_scripts: dict[str, list[str]] = {}  # ext -> [relative paths]
    compiled_source_files: dict[str, list[str]] = {}  # lang -> [relative paths]
    all_files: list[str] = []

    # Compiled language source extensions and their build system markers
    compiled_languages: dict[str, tuple[str, list[str]]] = {
        ".rs": ("Rust", ["Cargo.toml", "Cargo.lock"]),
        ".go": ("Go", ["go.mod", "go.sum"]),
        ".c": ("C", ["Makefile", "CMakeLists.txt", "meson.build"]),
        ".cpp": ("C++", ["Makefile", "CMakeLists.txt", "meson.build"]),
        ".cc": ("C++", ["Makefile", "CMakeLists.txt", "meson.build"]),
        ".cxx": ("C++", ["Makefile", "CMakeLists.txt", "meson.build"]),
        ".swift": ("Swift", ["Package.swift"]),
        ".zig": ("Zig", ["build.zig"]),
    }

    # Directories to always skip (build artifacts, caches, developer tooling)
    skip_dirs = {
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        ".eggs",
        "git-hooks",  # git hooks are developer tooling, not end-user components
        "tests",  # test fixtures may contain platform-specific scripts
        "fixtures",
    }

    # Use gitignore-aware walk to skip ignored files and directories
    for dirpath, dirnames, filenames in _gi.walk(plugin_root, skip_dirs=skip_dirs) if _gi else os.walk(plugin_root):
        if not _gi:
            # Fallback filtering when gitignore filter not initialized
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".") and d not in skip_dirs and not _is_python_venv(Path(dirpath) / d)
            ]
        rel_dir = Path(dirpath).relative_to(plugin_root)

        for filename in filenames:
            ext = Path(filename).suffix.lower()
            rel_path = str(rel_dir / filename) if str(rel_dir) != "." else filename
            all_files.append(rel_path)

            if ext in SCRIPT_PLATFORM_MAP:
                platform_specific_scripts.setdefault(ext, []).append(rel_path)

            if ext in compiled_languages:
                lang_name = compiled_languages[ext][0]
                compiled_source_files.setdefault(lang_name, []).append(rel_path)

    # --- 1. Report platform-specific interpreted scripts ---
    # When a script has a portable fallback in the same directory (same stem with a
    # cross-platform extension, e.g. install.sh + install.py + install.ps1), demote
    # the warning to INFO since the user has covered the gap. This avoids the surprise
    # of getting a warning for portable POSIX shell scripts that ship alongside Python
    # or PowerShell wrappers.
    def _has_portable_fallback(rel_path: str, all_paths: list[str]) -> bool:
        p = Path(rel_path)
        stem = p.stem
        parent = str(p.parent)
        fallback_extensions = {".py", ".js", ".ts", ".rb", ".ps1"}
        for other in all_paths:
            op = Path(other)
            if op == p:
                continue
            if str(op.parent) == parent and op.stem == stem and op.suffix.lower() in fallback_extensions:
                return True
        return False

    if platform_specific_scripts:
        for ext, paths in platform_specific_scripts.items():
            lang_name, platforms, note = SCRIPT_PLATFORM_MAP[ext]
            covered_paths = [p for p in paths if _has_portable_fallback(p, all_files)]
            uncovered_paths = [p for p in paths if p not in covered_paths]
            if covered_paths:
                report.info(
                    f"Found {len(covered_paths)} {lang_name} script(s) ({ext}) with portable fallback "
                    f"(.py/.ps1/etc.) in the same directory — cross-platform coverage already in place."
                )
            if uncovered_paths:
                if platforms:
                    platforms_str = ", ".join(sorted(platforms))
                    report.warning(
                        f"Found {len(uncovered_paths)} {lang_name} script(s) ({ext}) — only natively available on {platforms_str}. {note}. Consider providing cross-platform alternatives or documenting requirements.",
                    )
                else:
                    report.warning(
                        f"Found {len(uncovered_paths)} {lang_name} script(s) ({ext}) — {note}. Consider providing cross-platform alternatives.",
                    )
    else:
        has_scripts = any(
            any(f.endswith(ext) for ext in CROSSPLATFORM_EXTENSIONS)
            for _, _, files in (_gi.walk(plugin_root, skip_dirs=skip_dirs) if _gi else os.walk(plugin_root))
            for f in files
        )
        if has_scripts:
            report.passed("All scripts use cross-platform languages")

    # --- 2. Check compiled source code has binaries or build script ---
    if compiled_source_files:
        # Search for bin/ directories recursively, skip gitignored paths
        bin_dirs = list(_gi.rglob("bin") if _gi else plugin_root.rglob("bin"))
        has_bin = any(d.is_dir() and any(d.iterdir()) for d in bin_dirs)

        for lang_name, source_paths in compiled_source_files.items():
            # Find expected build system files for this language
            expected_build_files: set[str] = set()
            for ext, (ln, build_markers) in compiled_languages.items():
                if ln == lang_name:
                    expected_build_files.update(build_markers)

            # Check if build system files exist at plugin root
            has_build_system = any((plugin_root / bf).exists() for bf in expected_build_files)

            # Check for a generic build/install script
            has_build_script = any(
                (plugin_root / s).exists()
                for s in [
                    "build.sh",
                    "install.sh",
                    "setup.sh",
                    "compile.sh",
                    "build.py",
                    "install.py",
                    "setup.py",
                    "Makefile",
                    "justfile",
                    "Taskfile.yml",
                ]
            )

            if has_bin:
                report.info(f"Found {len(source_paths)} {lang_name} source file(s) with compiled binaries in bin/")
            elif has_build_system or has_build_script:
                report.warning(
                    f"Found {len(source_paths)} {lang_name} source file(s) with build system but no pre-compiled binaries in bin/. Users will need to compile before use."
                )
            else:
                report.major(
                    f"Found {len(source_paths)} {lang_name} source file(s) but no compiled binaries in bin/ and no build script (build.sh, install.sh, Makefile, etc.). Provide pre-compiled binaries or a build/install script."
                )

    # --- 3. Check compiled binaries platform coverage ---
    # Search for bin/ directories recursively, skip gitignored paths
    all_bin_dirs = []
    for d in _gi.rglob("bin") if _gi else plugin_root.rglob("bin"):
        if not d.is_dir():
            continue
        # Also skip venvs detected structurally
        rel_parts = d.relative_to(plugin_root).parts[:-1]
        if any(_is_python_venv(plugin_root / Path(*rel_parts[: i + 1])) for i in range(len(rel_parts))):
            continue
        all_bin_dirs.append(d)
    if not all_bin_dirs:
        return

    binary_files: list[str] = []
    detected_platforms: set[str] = set()
    base_names: set[str] = set()

    for bin_dir in all_bin_dirs:
        for item in bin_dir.rglob("*"):
            if not item.is_file():
                continue
            name = item.name
            rel_path = str(item.relative_to(plugin_root))

            for suffix, platform_name in BINARY_PLATFORM_SUFFIXES.items():
                if suffix in name.lower():
                    binary_files.append(rel_path)
                    detected_platforms.add(platform_name)
                    base = name[: name.lower().index(suffix.split("-")[0] + "-")]
                    if base.endswith("-"):
                        base = base[:-1]
                    base_names.add(base)
                    break
            else:
                if not item.suffix and os.access(item, os.X_OK):
                    # Skip portable interpreted scripts (Python/Bash/Node/etc.) — they have a
                    # shebang and run on every platform without compilation. Treating them as
                    # compiled binaries produces false-positive "missing platform suffix" warnings.
                    if _file_has_script_shebang(item):
                        continue
                    binary_files.append(rel_path)
                    base_names.add(name)
                elif item.suffix == ".exe":
                    binary_files.append(rel_path)
                    detected_platforms.add("Windows")
                    base_names.add(item.stem)
                elif item.suffix in {".dylib", ".so"}:
                    binary_files.append(rel_path)
                    if item.suffix == ".dylib":
                        detected_platforms.add("macOS")
                    else:
                        detected_platforms.add("Linux")

    if not binary_files:
        return

    report.info(f"Found {len(binary_files)} compiled binary file(s) for {len(base_names)} tool(s)")

    if detected_platforms:
        missing = RECOMMENDED_PLATFORMS - detected_platforms
        if missing:
            missing_str = ", ".join(sorted(missing))
            report.warning(
                f"Compiled binaries missing for: {missing_str}. Detected platforms: {', '.join(sorted(detected_platforms))}. Consider providing binaries for all major platforms."
            )
        else:
            report.passed(f"Compiled binaries cover recommended platforms: {', '.join(sorted(detected_platforms))}")
    else:
        report.warning(
            f"Found {len(binary_files)} binary file(s) without platform identifiers in filename. Use naming convention like 'tool-darwin-arm64', 'tool-linux-amd64', 'tool-windows-amd64.exe' for multi-platform support."
        )


def validate_manifest_skill_paths(plugin_root: Path, report: ValidationReport) -> bool:
    """Validate the optional ``skills`` path-list in plugin.json (CC v2.1.136+).

    Per CC v2.1.136 changelog: a ``skills`` entry in plugin.json HIDES the
    plugin's default ``skills/`` directory (auto-discovery is suppressed)
    and listing a file path that doesn't exist now shows an error in
    ``claude plugin validate``. CPV mirrors that behaviour:

    - When ``manifest["skills"]`` is absent or not a list, this function
      is a no-op and ``validate_skills`` continues with the default
      ``skills/`` directory walk.
    - When ``manifest["skills"]`` is a list, every entry is validated
      against the filesystem. Each entry may be either:
        - a folder path containing ``SKILL.md`` (e.g. ``skills/my-skill/``)
        - a direct ``SKILL.md`` file path (e.g. ``skills/my-skill/SKILL.md``)
      Missing paths emit MAJOR (not WARNING) — they break the plugin's
      skill discovery silently in CC < v2.1.136 and produce a hard error
      in ≥ v2.1.136.

    Returns ``True`` when the manifest declares a ``skills`` array (so
    the caller can suppress the default ``skills/`` directory walk),
    ``False`` otherwise. Mirrors the CC loader: a present ``skills`` field
    is authoritative — it does not augment the default discovery.
    """
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    if not plugin_json.is_file():
        return False
    try:
        manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False
    skills_field = manifest.get("skills")
    if skills_field is None:
        return False
    if not isinstance(skills_field, list):
        report.major(
            f"plugin.json::skills must be a list of paths (got "
            f"{type(skills_field).__name__}). CC v2.1.136+ rejects non-list "
            f"values and the field overrides the default skills/ directory.",
            ".claude-plugin/plugin.json",
        )
        return True  # field IS declared (just malformed) — suppress default walk
    for i, entry in enumerate(skills_field):
        if not isinstance(entry, str):
            report.major(
                f"plugin.json::skills[{i}] must be a string path (got "
                f"{type(entry).__name__}). CC v2.1.136+ rejects non-string entries.",
                ".claude-plugin/plugin.json",
            )
            continue
        # Resolve relative to plugin root; reject path-traversal.
        candidate = (plugin_root / entry).resolve()
        try:
            candidate.relative_to(plugin_root.resolve())
        except ValueError:
            report.major(
                f"plugin.json::skills[{i}] = {entry!r} escapes the plugin root "
                f"(resolved to {candidate}). Reject for security.",
                ".claude-plugin/plugin.json",
            )
            continue
        # Accept either a directory containing SKILL.md OR a direct SKILL.md.
        if candidate.is_dir():
            if not (candidate / "SKILL.md").is_file():
                report.major(
                    f"plugin.json::skills[{i}] = {entry!r} is a directory but "
                    f"contains no SKILL.md. CC v2.1.136+ shows this as an error "
                    f"in `claude plugin validate`.",
                    ".claude-plugin/plugin.json",
                )
        elif candidate.is_file():
            if candidate.name != "SKILL.md":
                report.major(
                    f"plugin.json::skills[{i}] = {entry!r} is a file but not a "
                    f"SKILL.md (got {candidate.name!r}). CC v2.1.136+ requires "
                    f"either a folder containing SKILL.md or a direct SKILL.md path.",
                    ".claude-plugin/plugin.json",
                )
        else:
            report.major(
                f"plugin.json::skills[{i}] = {entry!r} does not exist on disk. "
                f"CC v2.1.136+ shows this as an error instead of failing silently.",
                ".claude-plugin/plugin.json",
            )
    return True


def validate_skills(plugin_root: Path, report: ValidationReport, skip_platform_checks: list[str] | None = None) -> None:
    """Validate all skills in the plugin's skills/ directory.

    Args:
        plugin_root: Path to plugin root directory
        report: ValidationReport to add results to
        skip_platform_checks: List of platforms to skip checks for (e.g., ['windows'])

    CC v2.1.136+ semantics: when plugin.json declares a ``skills`` array,
    that list is AUTHORITATIVE and the default ``skills/`` directory walk
    is suppressed. ``validate_manifest_skill_paths`` runs first and
    returns True when it consumed the field — in that case this function
    early-returns so we don't double-validate (or validate skills the
    plugin author intentionally hid).
    """
    if validate_manifest_skill_paths(plugin_root, report):
        # plugin.json::skills is the authoritative source — every listed
        # path is the responsibility of the manifest validator above.
        return

    skills_dir = plugin_root / "skills"
    root_skill_md = plugin_root / "SKILL.md"

    if not skills_dir.is_dir():
        # CC v2.1.142: a plugin with a root-level SKILL.md and no skills/
        # subdirectory has that SKILL.md surfaced as a skill. Validate it with
        # the full skill validator, the same scrutiny a skills/<name>/ skill
        # gets — anything less would let a broken root-level skill ship.
        if root_skill_md.is_file():
            report.info("Root-level SKILL.md found — surfaced as a skill (CC v2.1.142)")
            # The skill's directory IS the plugin root, so the frontmatter
            # 'name' has no skills/<name>/ folder to be matched against.
            skill_report = validate_skill_comprehensive(
                plugin_root,
                strict_mode=True,
                strict_openspec=False,
                validate_pillars_flag=False,
                skip_platform_checks=skip_platform_checks,
                skip_dir_name_check=True,
            )
            for result in skill_report.results:
                report.add(result.level, result.message, result.file or "SKILL.md", result.line)
        else:
            report.info("No skills/ directory found")
        return

    # A skills/ directory exists: per CC v2.1.142 a root-level SKILL.md is
    # surfaced ONLY when the plugin has no skills/ subdir, so a SKILL.md left
    # at the plugin root alongside skills/ is dead weight that never loads.
    if root_skill_md.is_file():
        report.minor(
            "Root-level SKILL.md will NOT load: CC v2.1.142 surfaces a "
            "root-level SKILL.md as a skill only when the plugin has no "
            "skills/ subdirectory. Move it to skills/<name>/SKILL.md, or "
            "remove the skills/ directory so the root-level SKILL.md is "
            "surfaced instead.",
            "SKILL.md",
        )

    # Find all skill directories
    skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir()]

    if not skill_dirs:
        report.info("No skill directories found in skills/")
        return

    report.info(f"Found {len(skill_dirs)} skill(s) to validate")

    # Validate each skill using comprehensive validator (190+ rules)
    for skill_dir in sorted(skill_dirs):
        skill_name = skill_dir.name
        # Use comprehensive validator with all checks enabled
        skill_report = validate_skill_comprehensive(
            skill_dir,
            strict_mode=True,  # Enable Nixtla strict mode
            strict_openspec=False,  # Don't require OpenSpec 6-field whitelist for plugins
            validate_pillars_flag=skill_name.startswith(("lang-", "convert-")),  # Auto-enable for lang-*/convert-*
            skip_platform_checks=skip_platform_checks,
        )

        # Transfer results to main report with skill path prefix
        for result in skill_report.results:
            file_path = f"skills/{skill_name}/{result.file}" if result.file else f"skills/{skill_name}"
            report.add(result.level, result.message, file_path, result.line)


def validate_output_styles(plugin_root: Path, report: ValidationReport) -> None:
    """Validate output-styles/ directory — markdown files with YAML frontmatter.

    Output style files have frontmatter fields:
    - name (string, optional — defaults to filename)
    - description (string, optional — shown in /config picker)
    - keep-coding-instructions (boolean, optional — default false)
    """
    styles_dir = plugin_root / "output-styles"
    if not styles_dir.is_dir():
        return

    md_files = list(styles_dir.glob("*.md"))
    if not md_files:
        report.info("output-styles/ directory exists but has no .md files")
        return

    valid_fields = {"name", "description", "keep-coding-instructions"}

    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception as e:
            report.major(f"Cannot read output style: {e}", f"output-styles/{md_file.name}")
            continue

        # Parse frontmatter
        if not content.startswith("---"):
            report.minor(
                f"Output style '{md_file.name}' has no YAML frontmatter",
                f"output-styles/{md_file.name}",
            )
            continue

        parts = content.split("---", 2)
        if len(parts) < 3:
            report.minor(
                f"Output style '{md_file.name}' has malformed frontmatter (missing closing ---)",
                f"output-styles/{md_file.name}",
            )
            continue

        try:
            fm = yaml.safe_load(parts[1])
        except yaml.YAMLError as e:
            report.major(
                f"Output style '{md_file.name}' has invalid YAML: {e}",
                f"output-styles/{md_file.name}",
            )
            continue

        # If frontmatter exists but is not a mapping (e.g. a bare string or a
        # list), the file is malformed. Report it as a MAJOR finding before
        # normalizing to {} so downstream field checks don't spuriously claim
        # success on garbage frontmatter.
        if fm is not None and not isinstance(fm, dict):
            report.major(
                f"Output style '{md_file.name}': frontmatter must be a YAML mapping, got {type(fm).__name__}",
                f"output-styles/{md_file.name}",
            )
            fm = {}
        elif not isinstance(fm, dict):
            fm = {}

        # Validate fields
        for key in fm:
            if key not in valid_fields:
                report.warning(
                    f"Output style '{md_file.name}' has unknown field '{key}'",
                    f"output-styles/{md_file.name}",
                )

        if "keep-coding-instructions" in fm:
            val = fm["keep-coding-instructions"]
            if not isinstance(val, bool):
                report.major(
                    f"Output style '{md_file.name}': 'keep-coding-instructions' must be boolean, got {type(val).__name__}",
                    f"output-styles/{md_file.name}",
                )

        # Check body content exists
        body = parts[2].strip() if len(parts) > 2 else ""
        if not body:
            report.minor(
                f"Output style '{md_file.name}' has no body content (instructions)",
                f"output-styles/{md_file.name}",
            )
        else:
            report.passed(f"Output style '{md_file.name}' is valid", f"output-styles/{md_file.name}")

    report.passed(f"output-styles/: {len(md_files)} style(s) found")


def validate_readme(plugin_root: Path, report: ValidationReport) -> None:
    """Validate README.md exists and has recommended markers."""
    readme = plugin_root / "README.md"
    if readme.exists():
        report.passed("README.md found")
    else:
        report.minor("README.md not found")

    # Badge markers for automated badge updates (v2.26.0 — narrowed).
    #
    # Only fire the WARNING when the README ALREADY contains literal badge
    # markdown and lacks the automation markers. If the README has no
    # badges at all, the markers are unnecessary — nothing to regenerate.
    # This removes a false-positive WARNING from minimal READMEs and keeps
    # the check focused on its real purpose: flagging badges that cannot
    # be auto-updated by CI.
    if readme.exists():
        readme_content = readme.read_text(encoding="utf-8", errors="replace")
        has_markers = "<!--BADGES-START-->" in readme_content and "<!--BADGES-END-->" in readme_content
        # Detect literal markdown badges. Two common forms:
        # - Image-link form: [![alt](img)](href)
        # - Plain image form followed by shields.io/badge URL
        has_image_link_badge = bool(re.search(r"\[!\[[^\]]*\]\([^)]+\)\]\([^)]+\)", readme_content))
        has_shields_url = "shields.io" in readme_content or "img.shields.io" in readme_content
        has_badges = has_image_link_badge or has_shields_url
        if has_markers:
            report.passed("README.md has badge markers for automated updates", "README.md")
        elif has_badges:
            report.warning(
                "README.md has badge markdown but is missing the automation "
                "markers (<!--BADGES-START--> / <!--BADGES-END-->). CI cannot "
                "regenerate badges without the markers — wrap the badge block "
                "with those HTML comments so `scripts/update_badges.py` (or "
                "equivalent) can refresh versions/CI status automatically.",
                "README.md",
            )
        # else: no badges, no markers — nothing to flag. Silent pass.


def validate_license(plugin_root: Path, report: ValidationReport) -> None:
    """Validate LICENSE file exists."""
    for license_name in ["LICENSE", "LICENSE.md", "LICENSE.txt"]:
        if (plugin_root / license_name).exists():
            report.passed(f"{license_name} found")
            return

    report.minor("No LICENSE file found")


def validate_rules(plugin_root: Path, report: ValidationReport) -> None:
    """Validate rule files in the plugin's rules/ directory.

    Rules are plain markdown files loaded alongside CLAUDE.md into model context.
    Checks: UTF-8 encoding, optional frontmatter (paths field), token budget.
    """
    rules_dir = plugin_root / "rules"

    if not rules_dir.is_dir():
        report.info("No rules/ directory found")
        return

    # Use the dedicated rules validator
    rules_report = validate_rules_directory(rules_dir, plugin_root=plugin_root)

    # Transfer results to main report
    for result in rules_report.results:
        report.add(result.level, result.message, result.file, result.line)


def validate_no_local_paths(plugin_root: Path, report: ValidationReport) -> None:
    """Validate that plugin files don't contain hardcoded local or absolute paths.

    Uses the stricter absolute path validation from cpv_validation_common.py.

    In plugins, ALL paths should be:
    - Relative to plugin root (e.g., ./scripts/foo.py)
    - Using ${CLAUDE_PLUGIN_ROOT} for runtime resolution
    - Using ${HOME} or ~ for user home directory

    Checks for:
    - Current user's home path (CRITICAL) - auto-detected from system
    - Any absolute home directory paths (MAJOR)

    Excludes:
    - Cache directories (.mypy_cache, .ruff_cache, __pycache__)
    - Development folders (docs_dev/, scripts_dev/, etc.)
    - .git/ directory
    - Allowed system paths (/tmp/, /dev/, /proc/, /sys/)
    - Generic example usernames in documentation
    - Test directories (tests/) — contain intentional test fixture paths
    """
    # Use the strict absolute path validator which checks for:
    # - Current user's username (auto-detected) - CRITICAL
    # - ANY absolute paths that don't use env vars - MAJOR
    # We pass our local report since both have compatible interfaces
    validate_no_absolute_paths(plugin_root, report, skip_dirs={"tests"})  # type: ignore[arg-type]


# =============================================================================
# .gitignore Validation
# =============================================================================

# Patterns that a well-formed plugin .gitignore should include
# Each tuple: (pattern_to_search_for, description, severity)
# We check if the gitignore content covers these categories
EXPECTED_GITIGNORE_CATEGORIES: list[tuple[list[str], str, str]] = [
    # Cache/build artifacts
    (["__pycache__", "*.pyc"], "Python cache files (__pycache__ or *.pyc)", "warning"),
    (["node_modules"], "Node modules (node_modules/)", "warning"),
    ([".mypy_cache", ".ruff_cache", ".pytest_cache"], "Linter/type checker caches", "warning"),
    (["dist", "build", "*.egg-info"], "Build artifacts (dist/, build/, *.egg-info)", "warning"),
    # Temp/editor files
    ([".DS_Store", "Thumbs.db"], "OS metadata files (.DS_Store, Thumbs.db)", "warning"),
    (["*.swp", "*.swo", "*~", ".idea", ".vscode"], "Editor temp files", "warning"),
    # Environment/secrets
    ([".env", "*.env"], "Environment files (.env)", "major"),
    # Virtual environments
    ([".venv", "venv"], "Virtual environment directories", "major"),
    # Claude Code runtime directories
    ([".claude"], "Claude Code cache directory (.claude/)", "minor"),
    (["llm_externalizer_output"], "LLM Externalizer output directory", "warning"),
    ([".tldr"], "TLDR cache directory (.tldr/)", "warning"),
    # Agent/script reports — per ~/.claude/rules/agent-reports-location.md,
    # every plugin MUST have both `reports/` and `reports_dev/` explicitly
    # gitignored. Reports routinely contain private data (absolute paths,
    # source snippets, auth tokens in logs, PII in test fixtures), so both
    # entries MUST be present even if the folders do not yet exist — this
    # is defensive intent, not a filesystem reflection. The trailing slash
    # in each pattern disambiguates `reports/` from `reports_dev/` under
    # the validator's substring-match logic (line 2673). Added v2.25.0.
    (["reports/"], "Agent/script reports (reports/)", "major"),
    (["reports_dev/"], "Dev-only report scratch (reports_dev/)", "warning"),
]


def _check_stale_user_settings_local(report: ValidationReport) -> None:
    """Warn if ~/.claude/settings.local.json exists — it should not be at user level.

    settings.local.json only makes sense inside a project directory
    (<project>/.claude/settings.local.json). At ~/.claude/ it indicates a
    leftover from buggy tooling or running Claude Code from ~/ (invalid).
    """
    stale = Path.home() / ".claude" / "settings.local.json"
    if stale.exists():
        report.warning(
            "~/.claude/settings.local.json exists but should NOT be at user level. "
            "This file only makes sense inside project dirs (<project>/.claude/settings.local.json). "
            "Run /cpv-doctor --fix to delete it, or remove it manually.",
            "~/.claude/settings.local.json",
        )


def _category_has_matching_artifact(plugin_root: Path, patterns: list[str]) -> bool:
    """Return True iff ANY pattern in the category matches an existing
    file or directory inside the plugin.

    The gitignore-coverage check is GATED on this: we only flag missing
    coverage when the artifact actually exists in the plugin. A .gitignore
    pattern for a folder that does not exist in the plugin would be pure
    speculation — there is nothing to leak, so nothing to require.

    The gitignore bootstrap is performed lazily by agents at the point
    they are about to write a report (per
    ~/.claude/rules/agent-reports-location.md), not eagerly by CPV at
    validation time.

    Pattern matching:
    - Patterns with a trailing ``/`` are treated as directories.
    - Patterns containing ``*`` are passed through ``rglob`` (matches any
      file under the plugin tree — catches nested ``__pycache__`` etc.).
    - All other patterns are matched as either a file or a directory.
    """
    for raw in patterns:
        p = raw.strip()
        if p.endswith("/"):
            if (plugin_root / p.rstrip("/")).is_dir():
                return True
            continue
        if "*" in p:
            try:
                if next(plugin_root.rglob(p), None) is not None:
                    return True
            except OSError:
                pass
            continue
        target = plugin_root / p
        if target.is_dir() or target.is_file():
            return True
    return False


def validate_strip_gitmodules(plugin_root: Path, report: ValidationReport) -> None:
    """TRDD-793ac32a — validate `.gitmodules` URL allowlist.

    Plugins that use the strip-dev-parts pattern (tests/ → submodule)
    expose a `.gitmodules` URL surface that is normally trusted with no
    defense (PSS pattern). CPV adds:

      * URL-shape rules (no userinfo, no `..`, scheme in {https,ssh},
        no backslash/newline) → CRITICAL on violation (STRIP-G010)
      * Per-plugin allowlist via `cpv.strip.allowed_submodule_urls`
        → CRITICAL on alien URL (STRIP-G011)
      * Default rule when allowlist is absent: same owner as parent OR
        `Emasoft` (transitional shared-dev repos) → CRITICAL on alien
        owner (STRIP-G013)
      * Opt-out via `cpv.strip.require_url_allowlist=false` → WARNING
        for traceability (STRIP-G014)
      * Recorded `submodule_commit_sha` cross-check vs git index
        → CRITICAL on mismatch (STRIP-G015)

    No-op when `.gitmodules` is absent. **Fail-closed** when CPV's own
    `cpv_validate_gitmodules` module cannot be imported: emit CRITICAL
    with code RC-STRIP-GITMODULES-IMPORT-FAILED. A missing security
    validator is itself a security failure — refusing to validate is
    safer than silently passing the plugin (the engine ALSO runs the
    same check at strip time, but that is a separate execution
    path).
    """
    gm = plugin_root / ".gitmodules"
    if not gm.is_file():
        return
    try:
        import sys as _sys
        from pathlib import Path as _Path

        scripts_dir = str(_Path(__file__).resolve().parent)
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from cpv_validate_gitmodules import validate_gitmodules  # noqa: PLC0415
    except ImportError as e:
        # Engine helper missing — security validator unavailable.
        # FAIL-CLOSED: refuse to validate rather than silently pass.
        # A missing CRITICAL-tier check on a security-sensitive surface
        # (.gitmodules URL allowlist) must NEVER degrade to a soft warning
        # — that turns a security validator into a fail-open path that
        # an attacker can exploit by deleting / shadowing the helper.
        report.critical(
            "[RC-STRIP-GITMODULES-IMPORT-FAILED] .gitmodules present but "
            "cpv_validate_gitmodules.py is not installed/importable — "
            f"refusing to validate (import error: {e}). The .gitmodules URL "
            "allowlist is a CRITICAL-tier security check (TRDD-793ac32a) "
            "and CPV must not pass plugins through it silently. Reinstall "
            "CPV from a release that ships scripts/cpv_validate_gitmodules.py, "
            "or remove .gitmodules from the plugin if no submodule is needed."
        )
        return

    findings = validate_gitmodules(plugin_root)
    for f in findings:
        msg = f"[{f.code}] submodule={f.submodule_name!r} {f.message}"
        if f.severity == "CRITICAL":
            report.critical(msg)
        elif f.severity == "WARNING":
            report.warning(msg)
        else:
            report.minor(msg)
    if not findings:
        report.passed(".gitmodules URLs pass the strip-dev-parts allowlist (TRDD-793ac32a)")


def validate_gitignore(plugin_root: Path, report: ValidationReport) -> None:
    """Validate that the plugin has a .gitignore with essential patterns.

    Checks that cache files, build artifacts, temp files, secrets,
    and virtual environments are properly ignored — **but only for
    artifacts that actually exist in the plugin**. Missing coverage for
    a folder that does not exist is not a finding; the gitignore
    bootstrap rule (agent-reports-location.md) is lazy — agents add
    entries at the point they're about to write, not eagerly.
    """
    gitignore_path = plugin_root / ".gitignore"

    if not gitignore_path.exists():
        report.major(
            "No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin"
        )
        return

    try:
        content = gitignore_path.read_text(encoding="utf-8")
    except Exception as e:
        report.minor(f"Could not read .gitignore: {e}")
        return

    # Strip comments and empty lines for pattern matching
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    missing_categories: list[tuple[str, str]] = []

    for patterns, description, severity in EXPECTED_GITIGNORE_CATEGORIES:
        # Only flag if the gitignore misses this category AND the artifact
        # actually exists in the plugin. Don't speculate about future files.
        found_in_gitignore = any(any(p.lower() in line.lower() for line in lines) for p in patterns)
        if found_in_gitignore:
            continue
        if _category_has_matching_artifact(plugin_root, patterns):
            missing_categories.append((description, severity))

    if not missing_categories:
        report.passed(".gitignore covers all expected categories for artifacts present in the plugin")
    else:
        for description, severity in missing_categories:
            getattr(report, severity)(f".gitignore missing coverage for: {description}")

    # Check for common anti-patterns in .gitignore
    # Ignoring the entire plugin source is almost certainly wrong
    if "*.py" in lines or "*.js" in lines or "*.ts" in lines:
        report.major(
            ".gitignore ignores all source files (*.py, *.js, or *.ts) — this will exclude plugin code from distribution"
        )

    # Scan for actual venv directories by structure (any name, not just .venv/venv)
    # BUG FIX: previous substring match `dirname in line` falsely reported that a
    # venv named `venv/` was covered when the gitignore only contained `.venv/`,
    # because "venv" is a substring of ".venv". Use fnmatch against the normalised
    # pattern body so exact directory names are required (glob still supported).
    import fnmatch

    def _gitignore_covers(name: str, gitignore_lines: list[str]) -> bool:
        lower_name = name.lower()
        for raw in gitignore_lines:
            # Strip negation marker, leading slash, and trailing slash — gitignore
            # semantics: `/foo/` and `foo/` both mean "dir named foo". We don't
            # need full gitignore semantics here, just an exact/glob name check.
            pat = raw.strip()
            if pat.startswith("!"):
                pat = pat[1:]
            pat = pat.lstrip("/").rstrip("/")
            if not pat:
                continue
            if fnmatch.fnmatch(lower_name, pat.lower()):
                return True
        return False

    for item in plugin_root.iterdir():
        if item.is_dir() and _is_python_venv(item):
            dirname = item.name
            # Check if this specific directory is covered by .gitignore
            if not _gitignore_covers(dirname, lines):
                report.major(
                    f"Virtual environment '{dirname}/' detected (contains pyvenv.cfg) but not covered by .gitignore. Add '{dirname}/' to .gitignore."
                )

    # Check for bundled dependency directories that should be installed at runtime
    # in ${CLAUDE_PLUGIN_DATA} instead of shipped inside the plugin root.
    # ${CLAUDE_PLUGIN_ROOT} is wiped on every plugin update; ${CLAUDE_PLUGIN_DATA} persists.
    # Skip this check in development mode (.git present = source repo, not installed plugin).
    is_dev_mode = (plugin_root / ".git").exists()
    if not is_dev_mode:
        bundled_dep_dirs = {"node_modules", ".venv", "venv", "vendor", "__pypackages__"}
        for item in plugin_root.iterdir():
            if item.is_dir() and item.name.lower() in bundled_dep_dirs:
                report.warning(
                    f"Bundled dependency directory '{item.name}/' found inside plugin root. "
                    "This directory will be lost on every plugin update because ${{CLAUDE_PLUGIN_ROOT}} is replaced. "
                    "Use a SessionStart hook to install dependencies into ${{CLAUDE_PLUGIN_DATA}} instead — "
                    "see https://code.claude.com/docs/en/plugins-reference#persistent-data-directory",
                )

    # Check that Node.js plugins wire a SessionStart installer hook.
    # Plugins that ship `package.json`/`package-lock.json`/`pnpm-lock.yaml`/
    # `yarn.lock`/`bun.lock` need their `node_modules/` installed at
    # runtime — and the only durable place to install them is
    # ${CLAUDE_PLUGIN_DATA}, because ${CLAUDE_PLUGIN_ROOT} is wiped on
    # every plugin update.
    #
    # We narrow this advisory to Node.js because:
    #   - Python plugins typically run via `uv run`, which auto-provisions
    #     deps from pyproject.toml lazily (no SessionStart needed).
    #   - Rust/Go plugins typically `cargo build`/`go install` lazily.
    #   - Node.js is the only ecosystem where the dependency resolver
    #     refuses to run lazily — `require()` looks up `node_modules/`
    #     in the running process's directory tree, so the install MUST
    #     happen ahead of the first import.
    #
    # This rule fires in BOTH dev mode and packaged mode — the
    # missing-installer case is a design mistake, not a packaging
    # mistake, and the dev tree is the right place to catch it before
    # publish.
    node_manifests: tuple[str, ...] = (
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
    )
    matched_manifests: list[str] = [m for m in node_manifests if (plugin_root / m).is_file()]
    has_runtime_deps = bool(matched_manifests)

    if has_runtime_deps:
        # Look for a SessionStart hook in either of the two valid hook
        # locations. The hook command must mention an installer command
        # AND target ${CLAUDE_PLUGIN_DATA} for the install destination.
        hook_files = [
            plugin_root / "hooks" / "hooks.json",
            plugin_root / ".claude-plugin" / "hooks" / "hooks.json",
        ]
        installer_keywords = re.compile(
            r"(npm\s+(ci|install)|pnpm\s+install|yarn\s+install|bun\s+install|"
            r"pip\s+install|uv\s+(pip\s+install|sync)|cargo\s+(build|install)|"
            r"go\s+(install|build))",
            re.IGNORECASE,
        )
        plugin_data_token = "CLAUDE_PLUGIN_DATA"
        installer_found = False
        for hook_file in hook_files:
            if not hook_file.is_file():
                continue
            try:
                hook_content = hook_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Cheap textual check — full JSON parsing happens in validate_hook.
            # We just need to know whether the file mentions both an installer
            # command AND ${CLAUDE_PLUGIN_DATA}, anywhere inside a SessionStart
            # block.
            if (
                "SessionStart" in hook_content
                and plugin_data_token in hook_content
                and installer_keywords.search(hook_content)
            ):
                installer_found = True
                break

        if not installer_found:
            manifests_str = ", ".join(matched_manifests)
            report.warning(
                f"[RC-DATA-INSTALLER-001] Plugin declares runtime dependencies in "
                f"{manifests_str} but has no SessionStart hook installing them into "
                "${CLAUDE_PLUGIN_DATA}. Without one, the plugin either has to bundle "
                "node_modules/site-packages (which inflates the install + gets wiped on every "
                "plugin update because ${CLAUDE_PLUGIN_ROOT} is replaced wholesale), or it "
                "depends on the user having the tooling globally installed (fragile). The "
                "canonical pattern is a SessionStart hook that runs `npm ci --prefix "
                "$CLAUDE_PLUGIN_DATA` (or `uv pip install --target $CLAUDE_PLUGIN_DATA/...` "
                "for Python, etc.) on first session and is a no-op afterwards. See "
                "https://code.claude.com/docs/en/plugins-reference#persistent-data-directory."
            )

    # Check that no script / hook / config file references
    # ${CLAUDE_PLUGIN_ROOT}/<dep-dir>/ — that path is wiped on every update.
    # Mutable state belongs in ${CLAUDE_PLUGIN_DATA}/.
    #
    # Markdown files are EXCLUDED from this scan: they are documentation
    # that often quotes both correct and incorrect patterns side-by-side
    # (e.g. plugin-diagnoser.md has rule descriptions that LITERALLY
    # contain the bad pattern as the thing being detected). Quoting an
    # anti-pattern is fine; we only flag actual code that ships the
    # anti-pattern.
    plugin_root_dep_re = re.compile(
        r"\$\{?CLAUDE_PLUGIN_ROOT\}?/(node_modules|\.venv|venv|vendor|site-packages|target|__pypackages__)\b"
    )
    code_extensions = {".py", ".sh", ".js", ".ts", ".mjs", ".cjs", ".json", ".yml", ".yaml", ".toml"}
    scan_dirs = [
        plugin_root / "scripts",
        plugin_root / "hooks",
        plugin_root / "git-hooks",
        plugin_root,  # for top-level config files like .mcp.json
    ]
    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for f in scan_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in code_extensions:
                continue
            # Skip files inside node_modules / .venv / vendor / etc. (we don't
            # care about third-party code) and inside `_dev` working dirs.
            try:
                rel_parts = f.relative_to(plugin_root).parts
            except ValueError:
                continue
            # Skip third-party / build dirs (we don't audit code we don't own)
            # AND skip tests/ — test files often embed the very anti-patterns
            # they exist to detect, as fixtures. Same idea as why
            # validate_security skips test files for password / token regexes.
            if any(
                p
                in {
                    "node_modules",
                    ".venv",
                    "venv",
                    "vendor",
                    "__pypackages__",
                    "target",
                    "build",
                    "dist",
                    "_dev",
                    "tests",
                    "tests_dev",
                }
                or p.endswith("_dev")
                for p in rel_parts
            ):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in plugin_root_dep_re.finditer(text):
                rel = str(f.relative_to(plugin_root))
                line_no = text[: match.start()].count("\n") + 1
                report.major(
                    f"[RC-DATA-WRONG-ROOT-001] {rel}:{line_no} references "
                    f"${{CLAUDE_PLUGIN_ROOT}}/{match.group(1)}/ — that path is wiped on "
                    f"every plugin update. Use ${{CLAUDE_PLUGIN_DATA}}/{match.group(1)}/ "
                    "instead, and install via a SessionStart hook.",
                    file=rel,
                    line=line_no,
                )

    # Check that non-plugin artifacts that may exist are ignored
    # Look for actual artifacts in the tree that should be gitignored
    artifact_patterns = {
        "*.pyc": "Compiled Python files",
        ".DS_Store": "macOS metadata",
        "Thumbs.db": "Windows metadata",
    }
    for pattern_glob, desc in artifact_patterns.items():
        # Use gitignore-aware rglob — only find artifacts NOT covered by .gitignore
        if _gi:
            matches = [p for p in _gi.rglob(pattern_glob)]
        else:
            matches = list(plugin_root.rglob(pattern_glob))
        if matches:
            sample = matches[0].relative_to(plugin_root)
            report.warning(f"Found {len(matches)} {desc} file(s) (e.g. {sample}) that are not gitignored")


# Regex to find inline Python blocks inside YAML: `python3 -c "..."`  or `python -c "..."`
# Captures the Python code string passed to -c.
_YAML_INLINE_PYTHON_RE = re.compile(
    r'python3?\s+-c\s+"([^"]*(?:"[^"]*"[^"]*)*)"',
    re.DOTALL,
)

# Dangerous pattern: dict["key"] or dict['key'] inside an f-string.
# In YAML inline Python the shell strips the inner quotes, causing NameError.
# Matches: {expr["key"]}, {expr['key']}, {expr.method()["key"]} etc.
_FSTRING_DICT_BRACKET_RE = re.compile(
    r"""\{[^}]*\[["'][^"']+["']\][^}]*\}""",
)


def validate_workflow_inline_python(plugin_root: Path, report: ValidationReport) -> None:
    """Scan GitHub Actions workflow files for dangerous inline Python patterns.

    When a YAML workflow uses ``python3 -c "..."`` (double-quoted shell string),
    dict bracket access like source["repo"] inside f-strings will fail at
    runtime because the shell strips the inner double quotes before Python
    sees the code.  Python then interprets the bare word as an undefined
    variable name, causing NameError.

    This validator catches that pattern and reports it as MAJOR.
    """
    workflows_dir = plugin_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return

    yaml_files = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
    if not yaml_files:
        return

    found_any = False
    for yaml_path in yaml_files:
        try:
            content = yaml_path.read_text(encoding="utf-8")
        except Exception:
            continue

        rel_path = str(yaml_path.relative_to(plugin_root))

        # Find all inline Python blocks
        for match in _YAML_INLINE_PYTHON_RE.finditer(content):
            python_code = match.group(1)
            block_start_offset = match.start()

            # Search for f-strings with dict bracket access
            for bad_match in _FSTRING_DICT_BRACKET_RE.finditer(python_code):
                abs_offset = block_start_offset + bad_match.start()
                line_num = content[:abs_offset].count("\n") + 1
                snippet = bad_match.group(0)
                found_any = True
                report.major(
                    f"Inline Python uses dict bracket access in f-string: {snippet} -- shell quoting will strip inner quotes causing NameError. Extract value into a local variable first.",
                    rel_path,
                    line_num,
                )

    if not found_any and yaml_files:
        report.passed(f"No inline Python quoting issues in {len(yaml_files)} workflow file(s)")


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    colors = COLORS

    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    print("\n" + "=" * 60)
    print("Plugin Validation Report")
    print("=" * 60)

    print("\nSummary:")
    print(f"  {colors['CRITICAL']}CRITICAL: {counts['CRITICAL']}{colors['RESET']}")
    print(f"  {colors['MAJOR']}MAJOR:    {counts['MAJOR']}{colors['RESET']}")
    print(f"  {colors['MINOR']}MINOR:    {counts['MINOR']}{colors['RESET']}")
    print(f"  {colors['NIT']}NIT:      {counts['NIT']}{colors['RESET']}")
    print(f"  {colors['WARNING']}WARNING:  {counts['WARNING']}{colors['RESET']}")
    if verbose:
        print(f"  {colors['INFO']}INFO:     {counts['INFO']}{colors['RESET']}")
        print(f"  {colors['PASSED']}PASSED:   {counts['PASSED']}{colors['RESET']}")

    print("\nDetails:")
    for r in report.results:
        if r.level == "PASSED" and not verbose:
            continue
        if r.level == "INFO" and not verbose:
            continue

        color = colors[r.level]
        reset = colors["RESET"]
        file_info = f" ({r.file})" if r.file else ""
        line_info = f":{r.line}" if r.line else ""
        print(f"  {color}[{r.level}]{reset} {r.message}{file_info}{line_info}")

    print("\n" + "-" * 60)
    if report.exit_code == 0:
        print(f"{colors['PASSED']}✓ All checks passed{colors['RESET']}")
    elif report.exit_code == 1:
        print(f"{colors['CRITICAL']}✗ CRITICAL issues found - plugin will not work{colors['RESET']}")
    elif report.exit_code == 2:
        print(f"{colors['MAJOR']}✗ MAJOR issues found - significant problems{colors['RESET']}")
    else:
        print(f"{colors['MINOR']}! MINOR issues found - may affect UX{colors['RESET']}")

    # Machine-readable summary for CI/CD parsing
    print(
        f"SUMMARY: CRITICAL={counts['CRITICAL']} MAJOR={counts['MAJOR']} MINOR={counts['MINOR']} NIT={counts['NIT']} WARNING={counts['WARNING']}"
    )

    print()

    # If there are any fixable issues, point the user at the fixer agent/skill.
    from cpv_validation_common import _print_fixer_recommendation

    _print_fixer_recommendation(report, None)


def print_json(report: ValidationReport) -> None:
    """Print validation results as JSON."""
    output = {
        "exit_code": report.exit_code,
        "counts": {
            "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
            "major": sum(1 for r in report.results if r.level == "MAJOR"),
            "minor": sum(1 for r in report.results if r.level == "MINOR"),
            "nit": sum(1 for r in report.results if r.level == "NIT"),
            "warning": sum(1 for r in report.results if r.level == "WARNING"),
            "info": sum(1 for r in report.results if r.level == "INFO"),
            "passed": sum(1 for r in report.results if r.level == "PASSED"),
        },
        "results": [{"level": r.level, "message": r.message, "file": r.file, "line": r.line} for r in report.results],
    }
    print(json.dumps(output, indent=2))


def validate_md_content_references(plugin_root: Path, report: ValidationReport) -> None:
    """Validate file path references and URLs inside all .md files in the plugin.

    Scans commands/, agents/, skills/, README.md for:
    - Broken file path references (markdown links and backtick paths)
    - Dead URLs (HTTP HEAD check with sanitization)
    """
    # Collect all .md files to check (excluding tests/, _dev/ dirs, and CHANGELOG)
    md_files: list[Path] = []

    # README.md at root
    readme = plugin_root / "README.md"
    if readme.exists():
        md_files.append(readme)

    # Commands
    commands_dir = plugin_root / "commands"
    if commands_dir.is_dir():
        md_files.extend(commands_dir.glob("*.md"))

    # Agents
    agents_dir = plugin_root / "agents"
    if agents_dir.is_dir():
        md_files.extend(agents_dir.glob("*.md"))

    # Skills (SKILL.md + references/*.md). EXEMPT vendor-doc references
    # — those are canonical embedded copies fetched from code.claude.com,
    # and their cross-links use `/en/...` paths that target other Claude
    # docs, not files inside the plugin. We keep them byte-identical to
    # the upstream so doc updates produce clean diffs.
    VENDOR_DOC_NAMES = {"plugins-reference.md", "skills-reference.md"}
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    md_files.append(skill_md)
                refs_dir = skill_dir / "references"
                if refs_dir.is_dir():
                    md_files.extend(f for f in refs_dir.glob("*.md") if f.name not in VENDOR_DOC_NAMES)

    if not md_files:
        return

    report.info(f"Checking content references in {len(md_files)} .md file(s)")

    # Patterns to skip in path validation (common false positives)
    skip_patterns = {
        "node_modules/",
        "__pycache__/",
        ".git/",
        "<placeholder",
        "${",
        "$(",
    }

    # Shared URL cache across all files (avoid re-checking same URL)
    url_cache: dict[str, bool] = {}

    # Reference files (skills/*/references/*.md) are documentation about the USER's
    # plugin, not about this plugin. Backtick paths in those files describe the target
    # plugin structure, so they should not be validated as references to files in THIS
    # plugin. We pass a flag to downgrade plugin-internal backtick path errors to
    # WARNING in reference files.
    for md_file in sorted(md_files):
        # Reference files and command files describe the USER's plugin structure,
        # not this plugin. Backtick paths there are documentation examples.
        is_reference_doc = "/references/" in str(md_file) or "/commands/" in str(md_file)
        # Validate file path references
        validate_md_file_paths(
            md_file, plugin_root, report, skip_patterns=skip_patterns, is_reference_doc=is_reference_doc
        )
        # Validate URLs
        validate_md_urls(md_file, plugin_root, report, url_cache=url_cache)


def validate_pipeline_readiness(plugin_root: Path, report: ValidationReport) -> None:
    """Check that the plugin has CI/CD pipeline infrastructure."""
    # Pre-push hook
    hook_paths = [
        plugin_root / ".githooks" / "pre-push",
        plugin_root / "git-hooks" / "pre-push",
    ]
    if any(p.exists() for p in hook_paths):
        report.passed("Pre-push hook found")
    else:
        report.minor(
            "No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates"
        )

    # Publish script
    if (plugin_root / "scripts" / "publish.py").exists():
        report.passed("scripts/publish.py found")
    else:
        report.warning("No scripts/publish.py found — recommended for release automation")

    # Changelog config
    if (plugin_root / "cliff.toml").exists():
        report.passed("cliff.toml found (git-cliff changelog)")
    else:
        report.warning("No cliff.toml found — recommended for automated changelog generation")

    # GitHub workflows
    workflows_dir = plugin_root / ".github" / "workflows"
    if workflows_dir.is_dir() and list(workflows_dir.glob("*.yml")):
        report.passed("GitHub workflows found")
    else:
        report.minor("No .github/workflows/*.yml found — recommended for CI/CD automation")

    # Marketplace notification workflow
    if workflows_dir.is_dir():
        notify_names = ["notify-marketplace.yml", "notify.yml", "marketplace-notify.yml"]
        if any((workflows_dir / n).exists() for n in notify_names):
            report.passed("Marketplace notification workflow found")
        else:
            report.warning("No notify-marketplace.yml workflow — plugin updates won't auto-notify marketplaces")


# Regex matching `scripts/<name>.py` references in workflow / hook / template
# files. Captures the script name only (no leading `scripts/` for cleaner
# error messages).
#   - The lookbehind `(?<![\w./])` blocks matches inside paths like
#     `prefix/scripts/x.py` from being conflated with the project's scripts/.
#   - The lookahead `(?![\w.])` blocks matches like `scripts/x.py.bak.gz` —
#     a trailing `.` means the `.py` is part of a longer extension chain
#     (backup, archive, .pyc-derivative), not an actual script reference.
_SCRIPT_REF_RE = re.compile(r"(?<![\w./])scripts/([A-Za-z_][A-Za-z0-9_]*\.py)(?![\w.])")


def _collect_script_refs(text: str, source_label: str) -> list[tuple[str, int, str]]:
    """Yield (script_name, line_no, line_excerpt) for every scripts/*.py
    reference found in ``text``. Used by ``validate_pipeline_script_refs``.
    """
    refs: list[tuple[str, int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in _SCRIPT_REF_RE.finditer(line):
            script_name = match.group(1)
            excerpt = line.strip()
            if len(excerpt) > 120:
                excerpt = excerpt[:117] + "..."
            refs.append((script_name, line_no, excerpt))
    _ = source_label  # kept for caller-side diagnostics
    return refs


def validate_pipeline_script_refs(plugin_root: Path, report: ValidationReport) -> None:
    """Detect dangling `scripts/<name>.py` references in pipeline surface area.

    Why this exists: every time a script in `scripts/` is renamed or removed,
    multiple consumers silently break — `.github/workflows/*.yml`, the locally
    installed `.git/hooks/pre-push`, the published `setup_plugin_pipeline.py`
    template, and the `plugin-validation-skill` reference hooks all hardcode
    `scripts/<name>.py` paths. The v2.65.0 lint consolidation triggered exactly
    this regression — `lint_files.py` was removed but CI + the local hook still
    invoked it, breaking every push until a follow-up patch.

    This validator scans every place a stale reference could hide and emits
    MAJOR for each missing target. Catching dangling references at PR / release
    time is the only durable fix; the alternative is rediscovering the bug
    every time a script gets renamed.
    """
    scripts_dir = plugin_root / "scripts"
    if not scripts_dir.is_dir():
        return  # plugin without a scripts/ folder — nothing to check

    # Files that may legitimately hardcode `scripts/<name>.py` paths.
    targets: list[tuple[Path, str]] = []

    # GitHub workflows.
    workflows_dir = plugin_root / ".github" / "workflows"
    if workflows_dir.is_dir():
        for wf in sorted(workflows_dir.glob("*.yml")):
            targets.append((wf, f".github/workflows/{wf.name}"))
        for wf in sorted(workflows_dir.glob("*.yaml")):
            targets.append((wf, f".github/workflows/{wf.name}"))

    # Locally-installed git hook (only present in dev checkouts; absent in
    # cache installs because .git/ isn't shipped, so this is naturally a
    # no-op for end users).
    installed_hook = plugin_root / ".git" / "hooks" / "pre-push"
    if installed_hook.is_file():
        targets.append((installed_hook, ".git/hooks/pre-push"))

    # Git-tracked source-of-truth hook templates under git-hooks/. These
    # are the canonical templates that setup_git_hooks.py copies into
    # .git/hooks/, so a stale ref here propagates to every fresh install.
    # The v2.65.0 lint_files.py-removal regression slipped through because
    # this directory was NOT scanned by the validator — the installed
    # copy under .git/hooks/ had been hand-patched, so .git/hooks/pre-push
    # passed validation while git-hooks/pre-push (the source) still had
    # the dangling reference.
    git_hooks_dir = plugin_root / "git-hooks"
    if git_hooks_dir.is_dir():
        for hook_name in (
            "pre-push",
            "pre-commit",
            "post-rewrite",
            "post-merge",
            "commit-msg",
        ):
            tracked_hook = git_hooks_dir / hook_name
            if tracked_hook.is_file():
                targets.append((tracked_hook, f"git-hooks/{hook_name}"))

    # Plugin-validation-skill reference hooks (template that gets copied into
    # plugins by setup_plugin_pipeline).
    pvs_hook = plugin_root / "skills" / "plugin-validation-skill" / "references" / "pre-push-hook.py"
    if pvs_hook.is_file():
        targets.append((pvs_hook, "skills/plugin-validation-skill/references/pre-push-hook.py"))

    # The pipeline-template generator itself — its embedded PRE_PUSH_HOOK
    # string is the source-of-truth for newly-scaffolded plugins.
    pipeline_gen = plugin_root / "scripts" / "setup_plugin_pipeline.py"
    if pipeline_gen.is_file():
        targets.append((pipeline_gen, "scripts/setup_plugin_pipeline.py"))

    if not targets:
        return

    # Build the set of scripts that actually exist on disk.
    existing_scripts = {p.name for p in scripts_dir.glob("*.py")}

    for path, label in targets:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for script_name, line_no, excerpt in _collect_script_refs(text, label):
            if script_name in existing_scripts:
                continue
            report.major(
                f"Dangling reference to scripts/{script_name} in {label}:{line_no} — "
                f"the script does not exist. Update the reference or restore the file. "
                f"Line: {excerpt}",
                file=label,
                line=line_no,
            )


# ── RC-WORKFLOW-PATH-BROKEN (issue #21 ask #2) ────────────────────────────────
# Path-shape heuristic: a token is "path-like" if it starts with one of these
# prefixes or carries a trailing ".sh"/".py" extension. We DELIBERATELY keep
# this list narrow — broadening it (e.g. matching every `*` or every relative
# segment) starts catching glob-formatted matrix variables, makefile vars,
# bash arrays, etc. The narrow prefix list catches the documented symptom
# (post-migration .sh references that no longer exist) without false-positives
# on legitimate workflow constructs.
_WORKFLOW_PATH_PREFIXES: tuple[str, ...] = (
    "scripts/",
    "tests/",
    ".github/",
    ".githooks/",
    "git-hooks/",
    "./",
)

# Glob meta-characters in the same shell sense Python's glob module uses.
_WORKFLOW_GLOB_CHARS: frozenset[str] = frozenset({"*", "?", "[", "]"})

# Trailing shell control operators that frequently glue onto path-like
# tokens because shlex.split does NOT consume them as token separators —
# they are shell metacharacters, not whitespace. Without stripping them,
# `for h in scripts/hooks/*.py; do` produces the token
# `scripts/hooks/*.py;` (with the semicolon attached), which globs to
# zero matches and triggers a spurious MAJOR. Symmetric set for leading
# operators (case branches, leading pipes); the sets must remain narrow
# so we don't accidentally strip a leading dot or path separator.
_TRAILING_SHELL_OPS: str = ";)&|<>"
_LEADING_SHELL_OPS: str = "(&|"


def _strip_shell_ops(token: str) -> str:
    """Remove trailing/leading shell control operators (``;``, ``)``,
    ``&``, ``|``, ``<``, ``>``, ``(``) that shlex.split leaves glued onto
    path-like tokens. ``str.rstrip`` / ``lstrip`` take a *set* of
    characters, so this collapses runs of mixed operators in one pass
    (e.g. ``scripts/foo.sh;)`` → ``scripts/foo.sh``).
    """
    return token.lstrip(_LEADING_SHELL_OPS).rstrip(_TRAILING_SHELL_OPS)


def _looks_like_workflow_path(token: str) -> bool:
    """True iff ``token`` is a candidate path argument extracted from a
    workflow ``run:`` body.

    Excludes flag tokens (``-x``), URLs (``http://...``, ``https://...``),
    env-var refs (``${{ matrix.x }}``, ``$FOO``, ``${HOME}``), tokens
    that *contain* a shell variable reference anywhere (``./$h``,
    ``path/${VAR}/x.sh``), bare binaries (``shellcheck``, ``bash``), and
    KEY=VALUE assignments.
    """
    if not token:
        return False
    # Flags: -x, --foo, ---bar (anything starting with `-`).
    if token.startswith("-"):
        return False
    # URLs.
    lowered = token.lower()
    if (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("git+ssh://")
        or lowered.startswith("ssh://")
    ):
        return False
    # Shell variable references and GitHub Actions expressions. ${{ ... }}
    # is the GHA expression form; $FOO and ${FOO} are POSIX shell. We
    # exclude the token when ``$`` appears ANYWHERE in it — not just at
    # the start. shlex.split with posix=True strips the surrounding
    # quotes from `./"$h"`, leaving the bare string `./$h` which would
    # otherwise pass the path-prefix check below and be reported as a
    # missing literal. Any token containing ``$`` is dynamic at runtime
    # and cannot be statically validated against the filesystem, so the
    # honest answer is "not a literal path".
    if "$" in token:
        return False
    # Backticked command substitutions are not paths. ($-anchored
    # substitutions like $(...) are already excluded by the $-anywhere
    # rule above.)
    if token.startswith("`"):
        return False
    # KEY=VALUE shell assignments — the token isn't a path even when the value
    # part *contains* one (the assignment as a whole is a single token).
    if "=" in token and "/" not in token.split("=", 1)[0]:
        return False
    # Path-shape heuristic — must start with one of the known repo prefixes
    # OR end in a recognised extension. Avoids flagging bare command names
    # like ``shellcheck`` or ``bash``.
    if any(token.startswith(p) for p in _WORKFLOW_PATH_PREFIXES):
        return True
    if token.endswith((".sh", ".py", ".yml", ".yaml", ".toml", ".json")):
        # An unprefixed extension hit ('foo.sh') is too aggressive — only
        # flag when the token also contains a path separator. ``echo done.sh``
        # would otherwise emit a false positive.
        if "/" in token:
            return True
    return False


def _is_workflow_glob(token: str) -> bool:
    """Treat ``token`` as a glob iff it contains shell wildcards. Anything
    else is a literal path. Mirrors Python's ``glob`` module which treats
    ``*``, ``?`` and ``[…]`` as wildcards."""
    return any(ch in _WORKFLOW_GLOB_CHARS for ch in token)


def _scan_workflow_run_body(body: str, body_start_line: int) -> list[tuple[str, int]]:
    """Yield (token, absolute_line_no) tuples for every path-like token
    found in a workflow ``run:`` body.

    The body may be multi-line (``run: |`` literal block scalar). Each
    line is shlex-tokenised independently with ``posix=True`` so quoted
    strings collapse to single tokens. Tokeniser failures (unbalanced
    quotes from ``run: |`` heredoc bodies, half-written EOF blocks, etc.)
    fall back to whitespace-splitting that line — better than skipping
    the entire file.
    """
    results: list[tuple[str, int]] = []
    for offset, line in enumerate(body.splitlines()):
        # Comments and empty lines: nothing to extract.
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError:
            tokens = line.split()
        for raw_token in tokens:
            # Strip shell control operators that shlex.split does not treat
            # as token separators (`;`, `)`, `&`, `|`, `<`, `>` trailing;
            # `(`, `&`, `|` leading). Without this step the for-loop
            # syntax `for h in scripts/hooks/*.py; do` produces the token
            # `scripts/hooks/*.py;` — a glob that matches zero files and
            # triggers a spurious MAJOR. The strip is safe: pathnames
            # ending in those characters are not legal in POSIX command
            # arguments without explicit quoting (which shlex would have
            # consumed before we see the token here).
            token = _strip_shell_ops(raw_token)
            if _looks_like_workflow_path(token):
                results.append((token, body_start_line + offset))
    return results


def _collect_run_blocks(content: str) -> list[tuple[str, int]]:
    """Extract every ``run:`` body from a workflow YAML as a (body, line_no)
    list. Falls back to a regex pass when ``yaml.safe_load`` fails — better
    than giving up because of a single malformed step.

    We use a hybrid approach: PyYAML for structural extraction, then
    re-locate the body in the raw source so we can attach correct line
    numbers (PyYAML strips them). The line number returned is the line
    of the first content line of the body, NOT the line of the ``run:``
    key — the user wants citations like ``ci.yml:42`` to point at the
    offending command, not at the block-header line above it.
    """
    blocks: list[tuple[str, int]] = []

    # ── Structural pass via yaml.safe_load ────────────────────────────
    try:
        doc = yaml.safe_load(content)
    except Exception:
        doc = None

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "run" and isinstance(v, str):
                    body_start = _locate_run_body(content, v)
                    blocks.append((v, body_start))
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    if doc is not None:
        _walk(doc)
        if blocks:
            return blocks

    # ── Regex fallback ─────────────────────────────────────────────────
    # When PyYAML can't parse (e.g. tab indentation, unsupported tag), or
    # the document parsed but contained zero ``run:`` keys (some workflows
    # use only ``uses:`` actions), fall back to a regex that finds every
    # ``run:`` line and grabs either the inline value or the literal-block
    # body that follows.
    pattern = re.compile(r"^([ \t]*)run:[ \t]*(\|[+-]?|>[+-]?)?[ \t]*(.*)$", re.MULTILINE)
    for m in pattern.finditer(content):
        indent = m.group(1)
        block_marker = (m.group(2) or "").strip()
        inline_value = m.group(3)
        # Line number of the body's first physical line (NOT the run: line
        # itself — the diagnostic message should point at the offending
        # command). For inline ``run: foo`` that's the same line; for
        # block ``run: |`` it's the next line.
        line_at_run_key = content[: m.start()].count("\n") + 1
        if block_marker.startswith("|") or block_marker.startswith(">"):
            # Block scalar — collect indented continuation lines.
            lines = content.splitlines()
            start_idx = line_at_run_key  # 1-based: line AFTER the run: line
            collected: list[str] = []
            for idx in range(start_idx, len(lines)):
                line = lines[idx]
                if not line.strip():
                    collected.append("")
                    continue
                # Block ends when indentation regresses to or below the
                # ``run:`` line's indentation.
                line_indent = line[: len(line) - len(line.lstrip())]
                if len(line_indent) <= len(indent):
                    break
                collected.append(line)
            body = "\n".join(collected).rstrip("\n")
            blocks.append((body, start_idx + 1))  # 1-based body line
        else:
            blocks.append((inline_value or "", line_at_run_key))

    return blocks


def _locate_run_body(content: str, body: str) -> int:
    """Best-effort 1-based line number of a ``run:`` body inside the raw
    YAML source. Used when PyYAML stripped the line metadata.

    Strategy: search for the first non-empty line of ``body`` as a
    substring. Falls back to line 1 if not found.
    """
    first_line = next((line for line in body.splitlines() if line.strip()), body)
    if not first_line:
        return 1
    idx = content.find(first_line.strip())
    if idx < 0:
        return 1
    return content[:idx].count("\n") + 1


def validate_workflow_path_broken(plugin_root: Path, report: ValidationReport) -> None:
    """Detect broken literal paths and zero-match globs in workflow ``run:``
    bodies — issue #21 ask #2 (RC-WORKFLOW-PATH-BROKEN, MAJOR).

    Symptom this rule catches: a canonical-pipeline migration that
    consolidates several scripts/*.sh helpers into publish.py but leaves
    the workflow YAML still invoking the old shellcheck-on-globs lines:

        run: shellcheck scripts/dispatch.sh scripts/detectors/*.sh \\
                        scripts/hooks/*.sh scripts/lib/*.sh .githooks/pre-push

    After consolidation, ``scripts/detectors/`` no longer exists, so
    ``scripts/detectors/*.sh`` matches zero files and the workflow
    silently passes (shellcheck reports zero issues on zero files). The
    plugin then ships with NO shellcheck coverage, even though CI says
    "green."

    This validator detects the symptom by:
      1. Walking every ``.github/workflows/*.yml``/``*.yaml`` file.
      2. Extracting every ``run:`` body (multi-line block scalars too).
      3. shlex-tokenising each line and selecting "path-like" tokens
         via ``_looks_like_workflow_path``.
      4. For literals: ``(plugin_root / token).exists()`` → MAJOR if
         missing.
      5. For globs (token contains ``*``/``?``/``[``): ``glob.glob`` from
         the plugin root → MAJOR if zero matches.

    Severity is MAJOR (not CRITICAL): the workflow still runs, but the
    intended check is silently no-op'd. MAJOR means publish.py blocks the
    release until the dangling reference is fixed. Severity NOT CRITICAL
    because there is no security loss — only a lost lint/test signal.

    Skipped when:
      - The plugin has no ``.github/workflows/`` directory.
      - The token is a flag (``-x``), URL, env-var ref, or KEY=VALUE
        assignment (handled by ``_looks_like_workflow_path``).
    """
    workflows_dir = plugin_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return

    yaml_files: list[Path] = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    if not yaml_files:
        return

    plugin_root_str = str(plugin_root)
    found_any = False

    for yaml_path in yaml_files:
        try:
            content = yaml_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_path = str(yaml_path.relative_to(plugin_root))

        run_blocks = _collect_run_blocks(content)
        for body, body_start_line in run_blocks:
            for token, line_no in _scan_workflow_run_body(body, body_start_line):
                if _is_workflow_glob(token):
                    # Resolve the glob from the plugin root. Use
                    # ``recursive=False`` so ``*`` does NOT cross directory
                    # boundaries (matches shell glob semantics, which is
                    # what the workflow author wrote). ``**/*.sh`` would
                    # need recursive=True, but the heuristic above only
                    # accepts tokens with ``*`` not ``**``-style — and
                    # even if it did, shell globs default non-recursive
                    # unless the user enables ``shopt -s globstar``.
                    abs_pattern = str(Path(plugin_root_str) / token)
                    matches = _glob.glob(abs_pattern)
                    if not matches:
                        found_any = True
                        report.major(
                            f"[RC-WORKFLOW-PATH-BROKEN] {rel_path}:{line_no} — "
                            f"glob '{token}' matches zero files in the plugin tree. "
                            "If a canonical-pipeline migration consolidated the "
                            "matched files into publish.py, remove the dangling "
                            "glob from the workflow body; otherwise restore the "
                            "missing files.",
                            file=rel_path,
                            line=line_no,
                        )
                else:
                    target = plugin_root / token
                    if not target.exists():
                        found_any = True
                        report.major(
                            f"[RC-WORKFLOW-PATH-BROKEN] {rel_path}:{line_no} — "
                            f"literal path '{token}' does not exist on disk. "
                            "Update the workflow to point at the new canonical "
                            "entry-point (e.g. publish.py / cpv_lint_engine), or "
                            "restore the missing file.",
                            file=rel_path,
                            line=line_no,
                        )

    if not found_any and yaml_files:
        report.passed(
            f"All workflow run: paths/globs resolve in {len(yaml_files)} workflow file(s) (RC-WORKFLOW-PATH-BROKEN)"
        )


# Files generated by `generate_plugin_repo.gen_*` that are pure
# infrastructure (publish pipeline, retry helper, pre-push hook, CI / release
# / notify workflows, changelog config, mega-linter config). Plugins are NOT
# expected to customise these — their job is to stay in lockstep with the
# canonical CPV templates so every plugin gets the same security gates,
# idempotent publish pipeline, cross-platform Python, etc.
#
# When any of these drifts from the canonical content, the validator emits a
# WARNING (not blocking a publish, but visible in CI). The plugin-fixer agent
# picks the WARNING up and offers `/cpv-upgrade-plugin` to migrate.
_CANONICAL_PIPELINE_FILES: tuple[tuple[str, str], ...] = (
    ("scripts/publish.py", "gen_publish_py"),
    ("scripts/cpv_network_resilience.py", "gen_cpv_network_resilience_py"),
    ("git-hooks/pre-push", "gen_pre_push_hook"),
    (".github/workflows/ci.yml", "gen_ci_yml"),
    (".github/workflows/release.yml", "gen_release_yml"),
    (".github/workflows/notify-marketplace.yml", "gen_notify_marketplace_yml"),
    ("cliff.toml", "gen_cliff_toml"),
    (".mega-linter.yml", "gen_mega_linter_yml"),
    (".markdownlint.json", "gen_markdownlint_json"),
)


def validate_canonical_pipeline_drift(plugin_root: Path, report: ValidationReport) -> None:
    """Emit a WARNING for every canonical pipeline file that drifts from the
    latest CPV template.

    Each file in ``_CANONICAL_PIPELINE_FILES`` is generated from a deterministic
    `gen_*(p: PluginParams)` function in `generate_plugin_repo`. We re-run the
    generator with the plugin's own manifest params and byte-compare the
    rendered string against the file on disk.

    Plugins that opted into a specific older standard, or that intentionally
    customised one of these files, will see the WARNING — and that is desired
    behaviour: the WARNING tells them `/cpv-upgrade-plugin` will sync them to
    the latest standard (idempotent publish.py, sanitized inputs, pathlib-only
    Python, no bash hook constructs, validate_pipeline_script_refs, etc.).

    Skipped when scanning CPV itself — the canonical templates ARE CPV's own
    files, so any change CPV makes to the templates would self-warn.

    Skipped silently when:
      - The file is missing (validate_pipeline_readiness already flags missing
        publish.py / cliff.toml / workflows; emitting a drift warning on top
        would be noise).
      - `generate_plugin_repo` cannot be imported (e.g. the plugin under test
        is on an old CPV checkout that lacks one of the gen_* helpers).
      - The plugin's manifest cannot be read (other validators already warn).
    """
    # CPV self-scan: skip — the templates ARE CPV's own files.
    try:
        from validate_security import is_cpv_self_scan

        if is_cpv_self_scan(plugin_root):
            return
    except Exception:
        # Best-effort import; if validate_security is unavailable, fall through
        # to the manifest-name heuristic below.
        plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
        if plugin_json.is_file():
            try:
                manifest_data = json.loads(plugin_json.read_text(encoding="utf-8"))
                if isinstance(manifest_data, dict) and manifest_data.get("name") == "claude-plugins-validation":
                    return
            except Exception:
                pass

    # Read the plugin's manifest so we can populate template params.
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    if not plugin_json.is_file():
        return  # validate_required_files already flags this
    try:
        manifest_data = json.loads(plugin_json.read_text(encoding="utf-8"))
    except Exception:
        return

    # Import the generator and the params helper.
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import generate_plugin_repo as gen_module
        from standardize_plugin import _params_from_manifest
    except Exception:
        return

    try:
        params = _params_from_manifest(manifest_data)
    except Exception:
        return

    # Per-file emission with embedded unified diff.
    #
    # Issue #21 ask #3: instead of one consolidated warning naming six files,
    # emit one warning per drifted file containing the unified diff hunks
    # (with @@ line markers) so the reader can immediately see WHICH lines
    # drifted, not just WHICH files. Severity stays WARNING — escalation to
    # MAJOR is the job of validate_workflow_path_refs (issue #21 ask #2),
    # which targets a NARROWER subset (broken paths/globs in workflow run:
    # bodies), not whole-file template drift.
    for rel_path, gen_func_name in _CANONICAL_PIPELINE_FILES:
        target = plugin_root / rel_path
        if not target.is_file():
            continue
        try:
            actual_content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        gen_func = getattr(gen_module, gen_func_name, None)
        if gen_func is None:
            continue
        try:
            # Some gen_* are unparameterized; introspect the signature instead
            # of guessing.
            import inspect

            sig = inspect.signature(gen_func)
            expected_content = gen_func(params) if sig.parameters else gen_func()
        except Exception:
            continue
        if actual_content == expected_content:
            continue

        # Build a unified diff. Cap at ±10 hunks per file or 200 diff lines
        # total per emission so the message stays readable. The diff is
        # produced with `lineterm=""` per Python docs — every yielded hunk
        # line already contains its own newline, so no double-newlines and
        # no trailing-LF noise.
        diff_iter = difflib.unified_diff(
            expected_content.splitlines(),
            actual_content.splitlines(),
            fromfile=f"canonical/{rel_path}",
            tofile=f"plugin/{rel_path}",
            lineterm="",
            n=3,
        )
        diff_lines: list[str] = []
        hunk_count = 0
        max_hunks = 10
        max_diff_lines = 200
        truncated = False
        for hunk_line in diff_iter:
            if hunk_line.startswith("@@"):
                hunk_count += 1
                if hunk_count > max_hunks:
                    truncated = True
                    break
            if len(diff_lines) >= max_diff_lines:
                truncated = True
                break
            diff_lines.append(hunk_line)

        diff_body = "\n".join(diff_lines)
        if truncated:
            diff_body += (
                f"\n... (diff truncated at {max_hunks} hunks / "
                f"{max_diff_lines} lines — full diff: "
                f"`diff -u <canonical> {rel_path}`)"
            )

        # v2.86.0: reword to handle the case where the plugin's pipeline is
        # AT or ABOVE canon (issue #22 case — the plugin had extra hardening
        # CPV is now adopting). The blanket "migrate to the latest standard"
        # phrasing previously suggested regressing such plugins. Now we
        # describe the drift neutrally and let the maintainer judge whether
        # to migrate. Files that match canon-hardening checkpoints (SHA-pin
        # comments, atomic-push pattern, env-sanitization comments) get a
        # softer phrasing.
        already_hardened = any(
            marker in diff_body
            for marker in (
                "git push --atomic",
                "SHA-pin",
                "actionlint",
                "commitlint-github-action",
                "wagoid/commitlint",
                "rhysd/actionlint",
            )
        )
        if already_hardened:
            recommendation = (
                "This file appears to already include canon-level hardening "
                "(SHA-pinned actions, atomic push, actionlint/commitlint, "
                "etc.). Review the unified diff and decide whether the "
                "remaining deltas are intentional. If your version is "
                "STRICTLY above canon, consider opening an upstream PR to "
                "narrow this gap; if you want CPV to ignore it for this "
                "plugin, add the file path to "
                "`cpv.allow_pipeline_drift` in plugin.json."
            )
        else:
            recommendation = (
                "Run `/cpv-upgrade-plugin` (or `uvx cpv-remote-validate "
                "standardize <plugin> --fix --force-templates`) to migrate "
                "to the latest standard (canon now bundles idempotent "
                "publish.py, atomic push, SHA-pinned actions, actionlint + "
                "commitlint gates, macOS matrix, env-sanitized run blocks)."
            )
        report.warning(
            f"[RC-PIPELINE-DRIFT-001] Plugin pipeline differs from the "
            f"canonical CPV standard in {rel_path}. {recommendation}\n"
            f"Unified diff (canonical → plugin):\n{diff_body}",
            file=rel_path,
        )


# Legacy pipeline scripts that older `generate_plugin_repo` versions emitted
# but that publish.py now subsumes. Plugins upgraded to the canonical pipeline
# should NOT keep these around — invoking them bypasses the 14 publish gates
# (security scans, gh-auth precheck, integrity manifest, idempotency, etc.).
#
# Each entry: (relative-path, replaced-by, why-it's-legacy).
# Severity emitted by `validate_legacy_pipeline_scripts`: MINOR — informational,
# does not block publish; the fixer agent moves these to scripts_dev/ on the
# `/cpv-upgrade-plugin` path.
_LEGACY_PIPELINE_SCRIPTS: tuple[tuple[str, str, str], ...] = (
    (
        "scripts/bump_version.py",
        "scripts/publish.py --patch / --minor / --major",
        "publish.py owns version bumping — Gate 7 reads the remote tag and calls bump_semver() idempotently",
    ),
    (
        "scripts/release.sh",
        "scripts/publish.py",
        "publish.py is the canonical 14-gate release pipeline; .sh blocks Windows users",
    ),
    (
        "scripts/release.py",
        "scripts/publish.py",
        "publish.py is the canonical 14-gate release pipeline",
    ),
    (
        "scripts/publish.sh",
        "scripts/publish.py",
        "publish.py replaces publish.sh; .sh blocks Windows users",
    ),
    (
        "scripts/lint.sh",
        ".github/workflows/ci.yml + publish.py Gate 4 (lint)",
        "linting runs in CI on every push and inside publish.py Gate 4 — lint.sh is a pre-CPV-pipeline artefact",
    ),
    (
        "scripts/setup-hooks.sh",
        "scripts/setup-hooks.py",
        "setup-hooks.py is cross-platform Python; .sh blocks Windows users",
    ),
    (
        "scripts/compute_hashes.py",
        "scripts/publish.py Gate 8 (integrity manifest)",
        "publish.py Gate 8 generates and signs the integrity manifest; "
        "third-party plugins should NOT ship a hash computer",
    ),
    (
        "scripts/verify_hashes.py",
        "scripts/publish.py Gate 8 verification",
        "publish.py verifies hashes during the release; downstream verifiers live in CPV's _plugin_verify_hashes.py",
    ),
    (
        "scripts/changelog.py",
        "scripts/publish.py Gate 9 (git-cliff)",
        "publish.py Gate 9 invokes git-cliff with the cliff.toml emitted by the canonical pipeline",
    ),
    (
        "scripts/generate_changelog.py",
        "scripts/publish.py Gate 9",
        "publish.py Gate 9 generates CHANGELOG.md",
    ),
    (
        "scripts/check_version.py",
        "scripts/publish.py Gate 7",
        "publish.py Gate 7 validates version consistency across plugin.json, marketplace.json, pyproject.toml, etc.",
    ),
    (
        "scripts/install.sh",
        "Documentation in README + claude plugin install",
        "users install via `claude plugin install` — install.sh is a pre-pipeline artefact",
    ),
)


def validate_legacy_pipeline_scripts(plugin_root: Path, report: ValidationReport) -> None:
    """Emit a MINOR finding for every known-legacy pipeline script that
    survives in the plugin's `scripts/` folder.

    Older versions of `generate_plugin_repo.py` shipped helpers
    (bump_version.py, release.sh, lint.sh, etc.) that have since been
    subsumed by publish.py's 14-gate pipeline. Plugins migrated via
    `/cpv-upgrade-plugin` MUST have these removed — leaving them around
    invites users to invoke the legacy entry-point and skip the canonical
    gates (security scans, gh-auth precheck, integrity manifest,
    idempotent commit/tag/push, etc.).

    Severity is MINOR (not MAJOR) so the finding is informational and
    does not block publishing — the fixer's `--upgrade` flow moves the
    files to `scripts_dev/` (preservation guardrail) instead of deleting
    them, then the user can decide whether to delete after verifying.

    Skipped on CPV self-scan (CPV is the canonical source — the listed
    files don't exist at CPV root anyway, but the early-return keeps the
    rule cheap on every CPV-self lint pass).
    """
    # Skip CPV self-scan.
    try:
        from validate_security import is_cpv_self_scan

        if is_cpv_self_scan(plugin_root):
            return
    except Exception:
        plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
        if plugin_json.is_file():
            try:
                manifest_data = json.loads(plugin_json.read_text(encoding="utf-8"))
                if isinstance(manifest_data, dict) and manifest_data.get("name") == "claude-plugins-validation":
                    return
            except Exception:
                pass

    for rel_path, replaced_by, reason in _LEGACY_PIPELINE_SCRIPTS:
        target = plugin_root / rel_path
        if not target.is_file():
            continue
        report.minor(
            f"[RC-LEGACY-PIPELINE-001] Legacy pipeline script `{rel_path}` is "
            f"obsoleted by `{replaced_by}` — {reason}. The fixer can move it "
            f"to scripts_dev/ via `/cpv-upgrade-plugin` (preservation guardrail: "
            f"the legacy file is moved, not deleted, so the user can review "
            f"before final removal).",
            rel_path,
        )


_PEP723_BLOCK_RE = re.compile(
    r"^# /// script\s*\n(?P<body>(?:^#.*\n)*?)^# ///\s*$",
    re.MULTILINE,
)
_PEP723_DEPS_RE = re.compile(
    r"^#\s*dependencies\s*=\s*\[(?P<deps>.*?)\]",
    re.MULTILINE | re.DOTALL,
)
_PYTHON_STDLIB_PREFIXES: tuple[str, ...] = (
    # Conservative subset — anything else is treated as needing a venv.
    "argparse",
    "ast",
    "asyncio",
    "base64",
    "bisect",
    "collections",
    "concurrent",
    "contextlib",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "difflib",
    "enum",
    "errno",
    "fnmatch",
    "functools",
    "glob",
    "gzip",
    "hashlib",
    "heapq",
    "hmac",
    "html",
    "http",
    "importlib",
    "inspect",
    "io",
    "ipaddress",
    "itertools",
    "json",
    "logging",
    "math",
    "mimetypes",
    "multiprocessing",
    "operator",
    "os",
    "pathlib",
    "pickle",
    "platform",
    "pprint",
    "queue",
    "random",
    "re",
    "secrets",
    "select",
    "shlex",
    "shutil",
    "signal",
    "socket",
    "sqlite3",
    "ssl",
    "stat",
    "string",
    "struct",
    "subprocess",
    "sys",
    "tempfile",
    "textwrap",
    "threading",
    "time",
    "tomllib",
    "traceback",
    "types",
    "typing",
    "unicodedata",
    "unittest",
    "urllib",
    "uuid",
    "venv",
    "warnings",
    "weakref",
    "xml",
    "zipfile",
    "zlib",
)


def _pep723_has_runtime_deps(body: str) -> bool:
    """True when a PEP 723 metadata block declares ≥ 1 non-stdlib dependency.

    Body is the inline-comment block between `# /// script` and `# ///`. We
    parse the `dependencies = [ ... ]` list and check each entry's leading
    package-name token against the conservative stdlib prefix list. An empty
    list (`dependencies = []`) is fine — no `uv run` needed because the
    script imports nothing extra.
    """
    deps_match = _PEP723_DEPS_RE.search(body)
    if not deps_match:
        return False
    deps_str = deps_match.group("deps")
    # Strip per-line comment leaders and quotes; collect package-name tokens.
    cleaned = re.sub(r"^\s*#\s?", "", deps_str, flags=re.MULTILINE)
    for raw in cleaned.split(","):
        token = raw.strip().strip("\"'")
        if not token:
            continue
        # Slice off version/extra markers (e.g. "ruamel.yaml>=0.18", "pkg[opt]>=1").
        pkg = re.split(r"[<>=!~\[;]", token, maxsplit=1)[0].strip()
        if not pkg:
            continue
        # Top-level module name (e.g. "ruamel.yaml" → "ruamel" — close enough).
        head = pkg.split(".")[0].lower().replace("-", "_")
        if head not in _PYTHON_STDLIB_PREFIXES:
            return True
    return False


def validate_pep723_invocations(plugin_root: Path, report: ValidationReport) -> None:
    """Emit MAJOR for `python <script.py>` invocations of PEP 723 scripts.

    Background (reported 2026-05-09): plugin-creator scaffolded scripts that
    declare runtime dependencies via a PEP 723 inline-script metadata block
    (``# /// script ... # ///``), but the generated invocations in commands /
    agents / skills / hooks / README used bare ``python <script>`` /
    ``python3 <script>`` instead of ``uv run <script>``. Bare ``python`` ignores
    the inline metadata block, so the script ImportErrors on the first
    non-stdlib import the moment a user runs it. The plugin "looks valid" to
    every static check yet is broken at runtime for anyone whose Python env
    lacks the listed deps.

    Detection:
      1. Walk ``scripts/*.py`` for the regex
         ``^# /// script\\s*\\n(?:^#.*\\n)*?^# ///\\s*$``.
      2. For each script with a non-empty ``dependencies`` list (i.e. NOT
         ``dependencies = []``) AND at least one non-stdlib package, record
         the relative path + basename.
      3. Walk every ``commands/*.md``, ``agents/*.md``, ``skills/**/SKILL.md``,
         ``skills/**/references/*.md``, ``hooks/hooks.json``, ``.mcp.json``,
         ``.lsp.json``, and the plugin's ``README.md`` for invocations
         matching ``\\bpython3?\\s+[^\\n]*<script-basename>``.
      4. Flag every bare-python invocation as MAJOR
         ``[RC-PEP723-INVOCATION-001]``. Use the FIX hint to point at
         ``uv run <script>`` (or ``uv run --with <deps> python <script>`` if
         the plugin author insists on the explicit-deps form).

    Severity is MAJOR — silent runtime breakage for end users is much worse
    than the build-time noise of a wrong invocation pattern. The fixer's
    cpv-codemod already supports a ``python-to-uv-run`` transform; the
    upgrade flow chains it after the validator's report.
    """
    scripts_dir = plugin_root / "scripts"
    if not scripts_dir.is_dir():
        return

    pep723_scripts: list[tuple[str, str]] = []  # [(rel_path, basename)]
    for py_file in sorted(scripts_dir.glob("*.py")):
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _PEP723_BLOCK_RE.search(text)
        if not match:
            continue
        if not _pep723_has_runtime_deps(match.group("body")):
            continue
        rel = str(py_file.relative_to(plugin_root))
        pep723_scripts.append((rel, py_file.name))

    if not pep723_scripts:
        return

    # Where to look for invocations. Skip ``scripts_dev/`` (gitignored dev
    # scratch — not shipped) and the script files themselves.
    candidate_files: list[Path] = []
    for sub in ("commands", "agents", "skills", "hooks"):
        d = plugin_root / sub
        if d.is_dir():
            candidate_files.extend(p for p in d.rglob("*.md") if p.is_file())
            candidate_files.extend(p for p in d.rglob("*.json") if p.is_file())
    for top_file in (".mcp.json", ".lsp.json", "README.md"):
        f = plugin_root / top_file
        if f.is_file():
            candidate_files.append(f)

    # Build one regex per script — match `python` or `python3` followed by
    # optional flags + any path that ends with the script's basename.
    bare_python_patterns = {
        basename: re.compile(
            rf"\bpython3?\b(?!\s+(?:-c|-m)\b)(?:\s+-[A-Za-z]+)*\s+\S*{re.escape(basename)}\b",
        )
        for _rel, basename in pep723_scripts
    }
    # `uv run python <script>` is acceptable — uv's environment satisfies
    # PEP 723 deps. Detect the prefix to avoid false positives.
    uv_prefix = re.compile(r"\b(?:uvx?|pipx)\s+(?:run\s+)?(?:--[a-z\-]+\s+\S+\s+)*", re.IGNORECASE)

    for cand in sorted(set(candidate_files)):
        try:
            content = cand.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_cand = str(cand.relative_to(plugin_root))
        for line_no, line in enumerate(content.splitlines(), start=1):
            for basename, pat in bare_python_patterns.items():
                m = pat.search(line)
                if not m:
                    continue
                # Skip if a uv/uvx/pipx prefix immediately precedes the python token.
                pre = line[: m.start()]
                if uv_prefix.search(pre[-100:]):  # 100-char lookback
                    continue
                report.major(
                    f"[RC-PEP723-INVOCATION-001] Bare `python {basename}` "
                    f"invocation in {rel_cand}:{line_no} — `scripts/{basename}` "
                    f"declares PEP 723 inline runtime deps that bare python "
                    f"ignores. Replace with `uv run scripts/{basename}` (or "
                    f"`uv run --with <deps> python scripts/{basename}` if the "
                    f"plugin author wants explicit deps). The cpv-codemod "
                    f"`python-to-uv-run` transform applies the fix in bulk.",
                    rel_cand,
                    line_no,
                )


def validate_workflow_best_practices(plugin_root: Path, report: ValidationReport) -> None:
    """Check GitHub workflow files for common anti-patterns."""
    workflows_dir = plugin_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return
    for wf in workflows_dir.glob("*.yml"):
        try:
            content = wf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(wf.relative_to(plugin_root))
        # Check for uv pip install --system (should use uvx)
        if "uv pip install --system" in content:
            report.nit(f"{rel}: uses 'uv pip install --system' — prefer 'uvx' for reproducible installs", rel)
        # Check for unpinned actions/checkout
        if "actions/checkout@" not in content and "actions/checkout" in content:
            report.nit(f"{rel}: uses 'actions/checkout' without version pin — pin to '@v4' or similar", rel)


# =============================================================================
# Submodule + Language + Lockfile Detection (TRDD-79638eb6)
# =============================================================================


def is_plugin_in_submodule(plugin_root: Path) -> Path | None:
    """Detect if plugin_root is registered as a git submodule of a parent repo.

    Walks up the parent chain from plugin_root looking for any ancestor
    directory that contains a .gitmodules file AND references this plugin's
    relative path as a submodule target.

    Why this matters: when a plugin lives inside a parent repo as a submodule,
    the parent repo's CI will not run the plugin's own workflows automatically
    — the plugin must be released/validated independently. Users are often
    surprised by this.

    Args:
        plugin_root: Absolute path to the plugin directory.

    Returns:
        Path to the parent repo root if the plugin is a submodule, else None.
        The returned path is the directory containing .gitmodules that lists
        this plugin.
    """
    try:
        plugin_abs = plugin_root.resolve()
    except OSError:
        return None

    # Walk up the parent chain. Stop at filesystem root.
    current = plugin_abs.parent
    while True:
        gitmodules = current / ".gitmodules"
        if gitmodules.is_file():
            try:
                content = gitmodules.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            # Collect every "path = <relative>" entry in the .gitmodules file.
            # A submodule section looks like:
            #     [submodule "some-name"]
            #         path = some/rel/path
            #         url = https://...
            submodule_paths: list[str] = []
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("path") and "=" in stripped:
                    _, _, val = stripped.partition("=")
                    submodule_paths.append(val.strip())

            # Compute plugin_abs relative to the candidate parent dir, in POSIX form.
            try:
                rel = plugin_abs.relative_to(current)
            except ValueError:
                rel = None

            if rel is not None:
                rel_posix = rel.as_posix()
                if rel_posix in submodule_paths:
                    return current

        # Stop at filesystem root — current.parent == current means we've bottomed out.
        if current.parent == current:
            return None
        current = current.parent


def validate_submodule_containment(plugin_root: Path, report: ValidationReport) -> None:
    """Emit INFO when the plugin lives inside a parent repo as a git submodule.

    Parent repos do not run their submodules' CI — users need to know they
    must trigger the plugin's own release pipeline independently.
    """
    parent = is_plugin_in_submodule(plugin_root)
    if parent is None:
        return
    try:
        parent_display = str(parent)
    except Exception:
        parent_display = "<parent>"
    report.info(
        f"Plugin is a submodule of {parent_display}. Parent repo CI will not run this plugin's pipeline automatically."
    )


def validate_project_languages(plugin_root: Path, report: ValidationReport) -> dict[str, Path]:
    """Detect and report which languages the plugin uses.

    Emits a single INFO line listing all detected languages. The caller can
    use the returned dict to pick which linters/toolchains to invoke.

    Returns:
        Mapping of language -> marker file path (may be empty).
    """
    langs = detect_languages(plugin_root)
    if not langs:
        report.info("No language markers detected (pyproject.toml, package.json, Cargo.toml, etc.)")
        return langs
    names = sorted(langs.keys())
    # Build a concise one-line summary for the report
    summary = ", ".join(f"{name} ({langs[name].name})" for name in names)
    report.info(f"Detected project languages: {summary}")
    return langs


def validate_lockfiles(plugin_root: Path, report: ValidationReport, detected_languages: dict[str, Path]) -> None:
    """Scan for known lockfiles and flag orphan lockfiles or gitignored lockfiles.

    Emits:
        - NIT: lockfile present but its language was not detected (orphan).
               Usually means a config file was removed but the lockfile was left
               behind, or the plugin inherited a lockfile from a parent repo.
        - WARNING: lockfile present but listed in .gitignore. This defeats the
                   purpose of a lockfile — CI will reinstall with unpinned deps
                   and drift from whatever the developer tested.

    Args:
        plugin_root: Plugin directory.
        report: Where to record findings.
        detected_languages: Output from detect_languages() — used to determine
            whether each lockfile has a matching detected language.
    """
    lockfiles = detect_lockfiles(plugin_root)
    if not lockfiles:
        return

    # Parse the .gitignore so we can detect lockfiles that are being filtered
    # out before they reach CI. Use the project-local filter to pick up nested
    # rules as well (it handles walking up to find parent .gitignores).
    gitignore_path = plugin_root / ".gitignore"
    ignored_patterns: list[str] = []
    if gitignore_path.is_file():
        try:
            gi_content = gitignore_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            gi_content = ""
        for line in gi_content.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                ignored_patterns.append(s)

    for lockfile_name, language in sorted(lockfiles.items()):
        rel = lockfile_name
        # Orphan check: lockfile present but no manifest for its language.
        # "js" and "ts" share the same lockfiles — a TypeScript project with
        # only .ts files would still register "ts" (not "js") but its
        # package-lock.json is not orphaned. Treat "js" lockfiles as matched
        # when either "js" or "ts" is detected.
        matched = False
        if language in detected_languages:
            matched = True
        elif language == "js" and "ts" in detected_languages:
            matched = True
        if not matched:
            report.nit(
                f"Lockfile {lockfile_name} present but no {language} project detected — "
                "orphan lockfile (leftover from a removed toolchain?)",
                rel,
            )
            # Still check gitignore status below — both can fire for the same lockfile.

        # Gitignore check: an ignored lockfile will not ship to CI.
        # Use a conservative substring / exact match — we only compare the
        # filename against each active .gitignore entry. Patterns like
        # "*.lock" or "lockfiles/" also match.
        if _lockfile_is_gitignored(lockfile_name, ignored_patterns):
            report.warning(
                f"Lockfile {lockfile_name} is listed in .gitignore — CI will install "
                "with unpinned deps and drift from tested versions",
                rel,
            )


def _lockfile_is_gitignored(lockfile_name: str, patterns: list[str]) -> bool:
    """Cheap check: does any active .gitignore pattern match this lockfile name?

    Uses exact match, substring match, and trivial wildcard expansion. This
    intentionally mirrors the common .gitignore patterns a user would write
    for a lockfile (`uv.lock`, `*.lock`, `/Cargo.lock`) rather than
    implementing the full gitignore grammar.
    """
    import fnmatch

    lower_name = lockfile_name.lower()
    for pat in patterns:
        # Strip leading slash — anchored pattern, still a basename match
        candidate = pat.lstrip("/")
        if not candidate:
            continue
        # Direct exact match
        if candidate == lockfile_name:
            return True
        # Case-insensitive direct match
        if candidate.lower() == lower_name:
            return True
        # fnmatch glob support (covers *.lock, *lock*, etc.)
        if fnmatch.fnmatch(lockfile_name, candidate):
            return True
        if fnmatch.fnmatch(lower_name, candidate.lower()):
            return True
    return False


def _find_plugin_candidates(root: Path, max_depth: int = 3) -> list[Path]:
    """Scan ``root`` up to ``max_depth`` levels deep for plugin folders.

    A folder counts as a plugin candidate when it has either:
    - ``.claude-plugin/plugin.json`` (CPV-preferred layout), or
    - ``plugin.json`` at the folder root (auto-discovery legacy layout).

    Skips common no-go directories (node_modules, .git, .venv, __pycache__,
    dist, build, _dev suffixed folders, cache) so we don't flood the hint
    with irrelevant hits.
    """
    skip_names = {
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        "target",
        ".idea",
        ".vscode",
        "tmp",
        "vendor",
        "cache",
    }
    candidates: list[Path] = []

    def _walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(d.iterdir())
        except (OSError, PermissionError):
            return
        # Is this folder itself a plugin?
        if (d / ".claude-plugin" / "plugin.json").is_file() or (d / "plugin.json").is_file():
            if d != root:
                candidates.append(d)
            return  # don't descend further once a plugin root is hit
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name in skip_names or entry.name.endswith("_dev"):
                continue
            _walk(entry, depth + 1)

    _walk(root, 0)
    return candidates


def _classify_path(path: Path) -> str:
    """Return a short human-readable classification for a non-plugin path.

    Helps the user understand WHY the path they passed is not a plugin root,
    and what to do next. Used by the "no plugin found" error to give
    targeted guidance (different messages for marketplaces, skills,
    project ``.claude/`` configs, cache directories, etc.).
    """
    name = path.name
    parent_name = path.parent.name if path.parent != path else ""
    # Marketplace folder
    if (path / ".claude-plugin" / "marketplace.json").is_file() or (path / "marketplace.json").is_file():
        return "marketplace"
    # Standalone skill folder (has SKILL.md but NO plugin.json). Easy to confuse
    # with a plugin because both live in "plugin-like" directories.
    if (path / "SKILL.md").is_file() and not (path / ".claude-plugin" / "plugin.json").is_file():
        # Distinguish a skill nested INSIDE a plugin's skills/<name>/ from a truly
        # standalone skill by checking ancestors within 3 levels for plugin.json.
        ancestor: Path | None = path.parent
        for _ in range(3):
            if ancestor is None:
                break
            if (ancestor / ".claude-plugin" / "plugin.json").is_file():
                return "skill_inside_plugin"
            if ancestor.parent == ancestor:
                break
            ancestor = ancestor.parent
        return "standalone_skill"
    # Project-scoped Claude config (.claude/ in a project root)
    if name == ".claude" or (path / "settings.json").is_file() and (path / "plugins").is_dir():
        return "claude_project_config"
    # Global Claude plugin cache
    try:
        if ".claude" in path.parts and "cache" in path.parts:
            return "plugin_cache"
    except ValueError:
        pass
    # Home/projects parent
    if parent_name in {"projects", "Code", "code", "workspace", "dev"}:
        return "dev_parent"
    return "unknown"


def _format_no_plugin_found_hint(plugin_root: Path) -> str:
    """Compose the multi-line error emitted when ``plugin_root`` is not a plugin.

    The output has three parts:
    1. A classified explanation of what the path looks like (marketplace,
       ``.claude/`` project config, cache, etc.).
    2. A list of plugin candidates found within 3 levels, ranked by proximity.
    3. A reminder of how to pass the right path.
    """
    lines = [f"Error: No Claude Code plugin found at {plugin_root}"]
    classification = _classify_path(plugin_root)
    if classification == "marketplace":
        lines.append(
            "  → This path looks like a MARKETPLACE (has marketplace.json), not a plugin. "
            "Use `validate_marketplace.py` for marketplaces, or pick a plugin subfolder for `validate_plugin.py`."
        )
    elif classification == "standalone_skill":
        lines.append(
            "  → This path looks like a STANDALONE SKILL (has SKILL.md, no plugin.json). "
            "Skills and plugins are different things — skills are single folders dropped into "
            "`~/.claude/skills/` (user scope) or `<project>/.claude/skills/` (project/local scope), "
            "and do NOT need a marketplace or plugin.json. If you want to validate a skill, use "
            "`validate_skill.py` or the `skill-validation-agent`. If you meant to scaffold this as a "
            "full plugin, you need to wrap it in a plugin folder with `.claude-plugin/plugin.json` first."
        )
    elif classification == "skill_inside_plugin":
        lines.append(
            "  → This path is a SKILL INSIDE A PLUGIN (has SKILL.md; a parent folder has plugin.json). "
            "`validate_plugin.py` wants the PLUGIN root, not the skill. Use `validate_skill.py` to "
            "validate this skill on its own, or move up to the plugin root (the ancestor folder with "
            "`.claude-plugin/plugin.json`) to validate the whole plugin."
        )
    elif classification == "claude_project_config":
        lines.append(
            "  → This path looks like a project-scoped `.claude/` config directory. That holds INSTALLED "
            "plugin metadata (`.claude/plugins/cache/`), NOT plugin sources. Point to the source folder you "
            "maintain (the one with `.claude-plugin/plugin.json`)."
        )
    elif classification == "plugin_cache":
        lines.append(
            "  → This path looks like the global Claude plugin cache (~/.claude/plugins/cache/). That is a "
            "read-only copy created at install time, not a source. Point to the plugin's source repo/folder."
        )
    elif classification == "dev_parent":
        lines.append(
            "  → This path looks like a dev parent folder (projects/, Code/, workspace/, dev/). "
            "It is not the plugin itself — the plugin lives in a subfolder."
        )
    candidates = _find_plugin_candidates(plugin_root, max_depth=3)
    if candidates:
        lines.append("")
        if len(candidates) == 1:
            c = candidates[0]
            rel = c.relative_to(plugin_root) if c.is_relative_to(plugin_root) else c
            lines.append(f"  Did you mean: {rel}   (full path: {c})")
            lines.append(f"  Try:  uv run python scripts/validate_plugin.py {c}")
        else:
            lines.append(f"  Found {len(candidates)} plugin candidate(s) under this path:")
            for c in candidates[:10]:
                rel = c.relative_to(plugin_root) if c.is_relative_to(plugin_root) else c
                lines.append(f"    - {rel}   (full path: {c})")
            if len(candidates) > 10:
                lines.append(f"    ... and {len(candidates) - 10} more")
            lines.append("  Pass one of the above paths to validate a specific plugin.")
    else:
        lines.append("")
        lines.append(
            "  No plugin folders were found within 3 levels. Expected layout: "
            "`<plugin-root>/.claude-plugin/plugin.json`. Check the path and try again, "
            "or run the scaffolder (`generate_plugin_repo.py`) to create a new plugin here."
        )
    return "\n".join(lines)


def main() -> int:
    """Main entry point.

    First action: verify CPV's own source has not been tampered with
    by checking each validator file's SHA256 against the GitHub
    canonical manifest for the running plugin version. Exits with
    code 2 on mismatch — a tampered validator cannot be trusted.
    """
    from _plugin_verify_hashes import verify_self_integrity  # noqa: PLC0415

    verify_self_integrity(quiet=True)

    check_remote_execution_guard()

    from cpv_validation_common import launcher_epilog

    parser = argparse.ArgumentParser(
        description="Validate a Claude Code plugin against all validation rules.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("This is the main entry point. It orchestrates all 17 sub-validators.\n\n" + launcher_epilog("plugin")),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all results including passed checks",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--marketplace-only",
        action="store_true",
        help="Skip plugin.json requirement (for strict=false marketplace distribution)",
    )
    parser.add_argument(
        "--skip-platform-checks",
        nargs="*",
        metavar="PLATFORM",
        help="Skip platform-specific checks (e.g., --skip-platform-checks windows). Valid platforms: windows, macos, linux. Use without args to skip all.",
    )
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block validation")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color codes in output")
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )
    # TRDD-20108ab7 (2026-05-10): explicit hosting-marketplace override.
    # When passed, the plugin's cross-marketplace dep allowlist is checked
    # against THIS marketplace.json instead of the auto-discovered one.
    # Useful for CI where the plugin lives outside its production marketplace
    # tree (e.g. a worktree, a packed tarball, or a freshly cloned PR).
    parser.add_argument(
        "--marketplace-context",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "Path to a marketplace.json (or its containing directory) that "
            "should be treated as the plugin's hosting marketplace for "
            "cross-marketplace dependency-allowlist enforcement. Overrides "
            "auto-discovery. See plugin-dependencies.md:54-79."
        ),
    )
    parser.add_argument("path", nargs="?", help="Plugin root path (default: parent of scripts/)")
    args = parser.parse_args()

    # Disable ANSI colors when --no-color is passed or stdout is not a TTY.
    # Use the set_color_enabled() helper instead of mutating COLORS — direct
    # mutation is shared-state pollution that flares under pytest-xdist
    # parallel workers (one worker's --no-color clobbers COLORS for every
    # subsequent colorize() call by any other test in the same process).
    if args.no_color or not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        import cpv_validation_common

        cpv_validation_common.set_color_enabled(False)

    # Determine plugin root — always resolve to absolute path so relative_to() works
    if args.path:
        plugin_root = Path(args.path).resolve()
    else:
        plugin_root = Path(__file__).resolve().parent.parent

    if not plugin_root.is_dir():
        # Typo-tolerant hint: scan the parent for similarly-named folders
        # that DO exist, so a mistyped path gets a "did you mean" suggestion.
        msg = [f"Error: {plugin_root} is not a directory (or does not exist)"]
        parent = plugin_root.parent
        if parent.is_dir() and parent != plugin_root:
            target_name = plugin_root.name.lower()
            try:
                siblings = [d for d in parent.iterdir() if d.is_dir() and not d.name.startswith(".")]
            except (OSError, PermissionError):
                siblings = []
            near = [d for d in siblings if target_name in d.name.lower() or d.name.lower() in target_name]
            if near:
                msg.append("  Did you mean one of these?")
                for d in near[:5]:
                    msg.append(f"    - {d}")
            elif siblings:
                msg.append(f"  Parent {parent} exists. Plugin folders I can see there:")
                for d in siblings[:8]:
                    has_plugin = (d / ".claude-plugin" / "plugin.json").is_file() or (d / "plugin.json").is_file()
                    marker = "  ← plugin" if has_plugin else ""
                    msg.append(f"    - {d.name}{marker}")
        print("\n".join(msg), file=sys.stderr)
        return 1

    # Auto-resolve plugin cache directories that contain version subdirectories
    # e.g. ~/.claude/plugins/cache/marketplace/plugin-name/{1.0.0, 1.1.7}
    if not (plugin_root / ".claude-plugin").is_dir():
        version_dirs = sorted(
            [d for d in plugin_root.iterdir() if d.is_dir() and re.match(r"\d+\.\d+", d.name)],
            key=lambda d: d.name,
            reverse=True,
        )
        if version_dirs and (version_dirs[0] / ".claude-plugin").is_dir():
            plugin_root = version_dirs[0]
            print(f"Auto-resolved to latest version: {plugin_root.name}", file=sys.stderr)
        elif not args.marketplace_only:
            # No .claude-plugin/ at this path — scan for nearby candidates + explain
            # what kind of folder this looks like, so the agent/user can correct course.
            print(_format_no_plugin_found_hint(plugin_root), file=sys.stderr)
            return 1

    # Marketplace short-circuit: if the path has marketplace.json but NO plugin.json,
    # this is a marketplace folder, not a plugin. Bail with a targeted error so we
    # don't emit dozens of false positives ("Non-standard directory") for the
    # plugin subfolders that ARE the marketplace's content.
    has_marketplace = (plugin_root / ".claude-plugin" / "marketplace.json").is_file() or (
        plugin_root / "marketplace.json"
    ).is_file()
    has_plugin_manifest = (plugin_root / ".claude-plugin" / "plugin.json").is_file()
    if has_marketplace and not has_plugin_manifest and not args.marketplace_only:
        print(
            f"Error: {plugin_root} is a MARKETPLACE folder (has marketplace.json), not a plugin.\n"
            f"  Use validate_marketplace.py to validate marketplaces, or pass a plugin\n"
            f"  subfolder to validate_plugin.py.",
            file=sys.stderr,
        )
        return 1

    # Phase 0 plugin-shape detection — refuse to "validate as plugin" something
    # that clearly isn't a plugin. Real incident: CPV agents wrapped a SKILL
    # (which has SKILL.md at root + a `references/` folder + relative-path
    # references) into a plugin manifest + marketplace + publish pipeline,
    # and the published artifact installed to nothing because the underlying
    # content was a skill, not a plugin. This guard fails fast with a clear
    # message telling the user to call the right validator OR convert the
    # content to a plugin first.
    if not has_plugin_manifest and not has_marketplace and not args.marketplace_only:
        skill_md_at_root = (plugin_root / "SKILL.md").is_file()
        agents_only = (
            (plugin_root / "agents").is_dir()
            and not (plugin_root / "skills").is_dir()
            and not (plugin_root / "commands").is_dir()
            and not (plugin_root / "hooks").is_dir()
        )
        commands_only = (
            (plugin_root / "commands").is_dir()
            and not (plugin_root / "skills").is_dir()
            and not (plugin_root / "agents").is_dir()
            and not (plugin_root / "hooks").is_dir()
        )
        if skill_md_at_root:
            print(
                f"Error: {plugin_root} contains SKILL.md at root — it is a SKILL, not a plugin.\n"
                f"  This is the most common mis-classification that produces empty plugin\n"
                f"  installs. Either:\n"
                f"  (a) wrap this skill INTO a new plugin: place its content under\n"
                f"      <new-plugin>/skills/<skill-name>/SKILL.md, then add\n"
                f"      <new-plugin>/.claude-plugin/plugin.json;\n"
                f"  (b) ADD this skill to an existing plugin's skills/ folder;\n"
                f"  (c) validate as a skill: `cpv-remote-validate skill {plugin_root}`.",
                file=sys.stderr,
            )
            return 1
        if agents_only:
            print(
                f"Error: {plugin_root} only has agents/ — it is a single agent, not a plugin.\n"
                f"  Wrap into a plugin (add .claude-plugin/plugin.json + at least one\n"
                f"  component) or add the agent to an existing plugin's agents/ folder.",
                file=sys.stderr,
            )
            return 1
        if commands_only:
            print(
                f"Error: {plugin_root} only has commands/ — it is a loose commands folder,\n"
                f"  not a plugin. Wrap into a plugin or add to an existing plugin's commands/.",
                file=sys.stderr,
            )
            return 1
        # Generic missing-manifest case (no recognised standalone shape):
        print(
            f"Error: {plugin_root} has no .claude-plugin/plugin.json and no recognised\n"
            f"  standalone Claude Code shape (no SKILL.md, no agents/, no commands/).\n"
            f"  CPV refuses to validate this as a plugin — wrapping arbitrary directories\n"
            f"  into plugin manifests has historically produced empty installs.\n"
            f"  Add .claude-plugin/plugin.json (and at least one component dir) or pass\n"
            f"  a different path.",
            file=sys.stderr,
        )
        return 1

    # Initialize gitignore filter — all scan functions use this to skip ignored files
    global _gi  # noqa: PLW0603
    _gi = GitignoreFilter(plugin_root)

    # Run validation
    report = ValidationReport()
    marketplace_only = args.marketplace_only
    skip_platform_checks = args.skip_platform_checks

    # TRDD-20108ab7 (2026-05-10): resolve --marketplace-context (if any) so
    # validate_manifest sees the explicit hosting marketplace and skips
    # auto-discovery. Malformed/missing marketplace.json at the override path
    # falls through to auto-discovery rather than crashing.
    explicit_hosting: dict[str, Any] | None = None
    if args.marketplace_context:
        ctx_path = Path(args.marketplace_context).resolve()
        if ctx_path.is_dir():
            # Try Layout C location first, then bare marketplace.json at root.
            for cand in (
                ctx_path / ".claude-plugin" / "marketplace.json",
                ctx_path / "marketplace.json",
            ):
                explicit_hosting = _safe_load_marketplace_json(cand)
                if explicit_hosting is not None:
                    break
        else:
            explicit_hosting = _safe_load_marketplace_json(ctx_path)
        if explicit_hosting is None:
            print(
                f"Warning: --marketplace-context {args.marketplace_context!r} "
                "did not resolve to a readable marketplace.json — auto-discovery "
                "will be used instead.",
                file=sys.stderr,
            )

    validate_manifest(plugin_root, report, marketplace_only, hosting_marketplace=explicit_hosting)
    validate_structure(plugin_root, report, marketplace_only)
    # v2.32.0 — Layout C cross-validation (marketplace-in-plugin)
    validate_layout_c_consistency(plugin_root, report)
    validate_commands(plugin_root, report)
    validate_agents(plugin_root, report)
    validate_hooks(plugin_root, report)
    validate_mcp(plugin_root, report)
    # TRDD-e3e74f69 telemetry hookup — OTEL supply-chain audit on every plugin
    validate_telemetry(plugin_root, report)
    validate_scripts(plugin_root, report)
    # v2.64.0 — single source of truth for repo-wide linting.
    # Replaces the inline lint pieces of validate_scripts (Python ruff/mypy,
    # shell shellcheck, JS eslint, PowerShell PSSA, Go vet, Rust cargo) AND
    # the standalone scripts/lint_files.py orchestrator. Strict-by-default:
    # any missing linter for a detected language fails the run with MAJOR.
    print(f"\n{COLORS['BOLD']}═══ [REPO LINT] (15 languages, gitignore-filtered) ═══{COLORS['RESET']}")
    run_lint_engine(plugin_root, report, strict_missing_tools=True)
    validate_bin_executables(plugin_root, report)
    validate_skills(plugin_root, report, skip_platform_checks)
    validate_rules(plugin_root, report)
    validate_output_styles(plugin_root, report)
    validate_readme(plugin_root, report)
    validate_license(plugin_root, report)
    validate_no_local_paths(plugin_root, report)
    validate_gitignore(plugin_root, report)
    validate_strip_gitmodules(plugin_root, report)
    validate_cross_platform(plugin_root, report)
    # Check for stale ~/.claude/settings.local.json — should not exist at user level
    _check_stale_user_settings_local(report)
    validate_md_content_references(plugin_root, report)
    validate_workflow_inline_python(plugin_root, report)
    validate_pipeline_readiness(plugin_root, report)
    validate_pipeline_script_refs(plugin_root, report)
    validate_workflow_path_broken(plugin_root, report)
    validate_canonical_pipeline_drift(plugin_root, report)
    validate_legacy_pipeline_scripts(plugin_root, report)
    validate_pep723_invocations(plugin_root, report)
    validate_workflow_best_practices(plugin_root, report)
    # Submodule + language + lockfile detection (TRDD-79638eb6)
    validate_submodule_containment(plugin_root, report)
    detected_languages = validate_project_languages(plugin_root, report)
    validate_lockfiles(plugin_root, report, detected_languages)

    # Output
    if args.json:
        print_json(report)
    else:
        if args.report:
            save_report_and_print_summary(
                report, Path(args.report), "Plugin Validation", print_results, args.verbose, plugin_path=args.path
            )
        else:
            print_results(report, args.verbose)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
