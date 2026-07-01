#!/usr/bin/env python3
"""
Claude Plugins Validation - Cross-Reference Validator

Validates cross-references between plugin components:
1. Agent Task() calls must reference existing agents in agents/ directory
2. Subagent_type must match actual agent filenames
3. Version synchronization between plugin.json, marketplace entry, and README
4. Breaking references detection for commands calling non-existent agents
5. Skills referenced in code should exist in skills/ directory
6. Hook scripts referenced in hooks.json must exist

Usage:
    uv run python scripts/validate_xref.py /path/to/plugin
    uv run python scripts/validate_xref.py /path/to/plugin --verbose
    uv run python scripts/validate_xref.py /path/to/plugin --json

Exit codes:
    0 - All checks passed (or only INFO/PASSED)
    1 - CRITICAL issues found
    2 - MAJOR issues found
    3 - MINOR issues found
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from cpv_parallel_runner import ScanResult, parallel_scan
from cpv_validation_common import (
    COLORS,
    ValidationReport,
    print_report_summary,
    print_results_by_level,
    save_report_and_print_summary,
    should_skip_directory,
)

# =============================================================================
# Regex Patterns for Cross-Reference Detection
# =============================================================================

# Pattern to find Task tool invocations with subagent_type parameter
# Matches patterns like: subagent_type: "my-agent" or subagent_type="my-agent"
# RETAINED for backward compatibility with code paths that still use it;
# new code should use _extract_dispatch_refs() (per TRDD-25b9be90 Phase 1).
SUBAGENT_TYPE_PATTERN = re.compile(
    r'subagent_type\s*[=:]\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# Pattern to find agent references in markdown (e.g., "spawn agent-name agent")
AGENT_SPAWN_PATTERN = re.compile(
    r'(?:spawn|invoke|call|use)\s+(?:the\s+)?["\']?([a-z][a-z0-9-]*)["\']?\s+agent',
    re.IGNORECASE,
)

# Issue #110: a real agent name is kebab/snake-cased (browser-agent, cpv-spark,
# caa-fix-agent) or carries a digit. A bare single English word adjacent to
# "agent(s)" — "explicit", "specific", "single", "new" — is prose, not a
# dispatch target. This gates ONLY the advisory prose WARNING below (which by
# construction fires only on a candidate that is NOT a known agent); the
# structural ghost-dispatch path (subagent_type:, RC_GHOST_DISPATCH_*) stays
# CRITICAL and is unaffected.
_LOOKS_LIKE_AGENT_IDENTIFIER = re.compile(r"[-0-9]")

# Pattern to find skill references in code and markdown.
#
# Issue #27 (v2.97.0): the previous form ``[a-z][a-z0-9-]*`` allowed a
# trailing hyphen because the boundary char (``#``, ``.`` , ``)``,
# whitespace, etc.) ends the capture but the dash is kept. Body text
# like ``skills/amvcp-wf-#anchor`` produced phantom skill name
# ``amvcp-wf-`` that no plugin can ship. The new form requires the
# capture (when longer than 1 char) to end in ``[a-z0-9]`` so a
# trailing hyphen cannot leak through:
#   [a-z]                          — first char must be a letter
#   (?:[a-z0-9-]*[a-z0-9])?        — optional tail ending in non-hyphen
# Single-letter names like ``a`` still match (per the optional group).
# Issue #44: the `(?<![A-Za-z0-9~]/)` lookbehind rejects matches where
# ``skills/`` sits inside an absolute path or URL segment — concretely,
# ``<alphanumeric-or-tilde>/skills/<name>`` is a path component (e.g.
# ``/mnt/skills/user/``, ``~/.pi/agent/skills/vercel-deploy/``,
# ``https://example.com/skills/foo``), not an intra-plugin reference, so
# ``user`` / ``vercel-deploy`` / ``foo`` MUST NOT be looked up as skill
# names. Intra-plugin shapes remain matched: bare ``skills/foo``, relative
# ``./skills/foo`` and ``../skills/foo``, bracketed ``(skills/foo)``,
# variable-expanded ``${CLAUDE_PLUGIN_ROOT}/skills/foo`` (closing ``}``
# isn't alphanumeric), and ``[label](skills/foo/SKILL.md)`` (opening
# paren isn't alphanumeric). The lookbehind needs two characters of
# context to fire, so it never strips an in-bounds match when the file
# starts on ``skills/...``.
SKILL_REF_PATTERN = re.compile(
    r"(?<![A-Za-z0-9~]/)(?:skill|skills)/([a-z](?:[a-z0-9-]*[a-z0-9])?)",
    re.IGNORECASE,
)

# Pattern to extract version from files.
# The ``(?![\d.])`` boundary after the captured triple stops a 4+-segment
# version (``1.2.3.4``) from being truncated to ``1.2.3`` — without it,
# README "1.2.3.4" and plugin.json "1.2.3" would be recorded as equal and
# validate_version_sync would falsely report agreement. Matches the
# quote-anchored pyproject extractor below.
VERSION_PATTERN = re.compile(
    r'(?:version|VERSION)\s*[=:]\s*["\']?(\d+\.\d+\.\d+)(?![\d.])',
    re.IGNORECASE,
)

# Pattern to find hook script references in hooks.json
# Only matches CLAUDE_PLUGIN_ROOT paths (CLAUDE_PLUGIN_DATA paths are persistent external state, not resolvable)
HOOK_SCRIPT_PATTERN = re.compile(
    r'\$\{CLAUDE_PLUGIN_ROOT\}/([^"\'}\s]+)',
)


# =============================================================================
# Ghost-agent dispatch detection (per TRDD-25b9be90)
# =============================================================================

# Built-in Claude Code agents — always resolvable, no per-plugin file required.
# Verified 2026-05-19 against the harness tool listing. The CC v2.1.140 resolver
# is case- and separator-insensitive, so these are compared via _normalize_subagent_type.
BUILTIN_AGENTS: frozenset[str] = frozenset(
    {
        "general-purpose",  # universal catch-all agent
        "explore",  # fast read-only search agent
        "plan",  # software architect planning agent
        "statusline-setup",  # built-in agent for status line config
        # Agent-tool forked subagent (gated by CLAUDE_CODE_FORK_SUBAGENT): the
        # `fork` subagent_type inherits the parent conversation instead of a
        # named agent — it has NO agents/fork.md BY DESIGN, so a dispatch to it
        # is NOT a ghost dispatch. (sub-agents.md "Fork the current conversation".)
        "fork",
    }
)

# Finding codes for ghost-agent dispatch (per TRDD-25b9be90).
RC_GHOST_DISPATCH_UNRESOLVED = "RC-GHOST-DISPATCH-001"  # CRITICAL — silent-failure class
RC_GHOST_DISPATCH_DYNAMIC = "RC-GHOST-DISPATCH-002"  # MINOR — variable / template, cannot statically verify
RC_GHOST_DISPATCH_CROSS_PLUGIN = "RC-GHOST-DISPATCH-003"  # NIT — namespaced to a different plugin

# Three extractors covering all four documented dispatch forms (TRDD-25b9be90 §Design):
#
#   _DISPATCH_QUOTED_VALUE — captures any value between matching quotes,
#     for bare-key forms:
#       subagent_type: "agent" / subagent_type: 'Code Reviewer' /
#       subagent_type="agent-name"
#     Allows spaces inside quotes (needed for the v2.1.140 case/separator-
#     insensitive matcher, which accepts ``"Code Reviewer"`` → ``code-reviewer``).
#
#   _DISPATCH_JSON_QUOTED_VALUE — captures any value between matching quotes,
#     for JSON-object key form:
#       "subagent_type": "agent" / 'subagent_type': 'agent'
#
#   _DISPATCH_UNQUOTED_VALUE — captures Python-identifier-like names (no
#     spaces), for unquoted forms in both YAML and Python kwarg contexts:
#       subagent_type: agent-name (YAML bare — literal)
#       subagent_type=variable (Python kwarg unquoted — dynamic per
#                                _classify_dispatch logic)
#
# Each match yields ``(quote_char_or_empty, name)``; _classify_dispatch
# converts that into ``(kind, name)`` where ``kind`` is ``literal`` or
# ``dynamic``.
_DISPATCH_QUOTED_VALUE = re.compile(
    r"""(?<![\w-])subagent_type\s*[=:]\s*(["'])([^"'\n]+?)\1""",
)
_DISPATCH_JSON_QUOTED_VALUE = re.compile(
    r"""["']subagent_type["']\s*:\s*(["'])([^"'\n]+?)\1""",
)
_DISPATCH_UNQUOTED_VALUE = re.compile(
    r"""(?<![\w-])subagent_type\s*([=:])\s*([a-zA-Z_][\w:.-]*)(?![\w:.-])""",
)

# Languages whose fenced code blocks are example output / shell commands
# / log captures, NOT directives. Bodies marked with these languages are
# stripped before regex scanning so that example output doesn't false-positive.
# Per TRDD-25b9be90:
#   - text/output/console/log: example output captures
#   - bash/shell/sh: shell-command examples (path arguments like
#     ./skills/foo/ are shell paths, not skill invocations)
_NOISE_FENCE_LANGS = ("text", "output", "console", "log", "bash", "shell", "sh")

# Plugin-structural directory names that show up as `skills/<dir>` captures in
# prose like "skills/agents/commands" but are NOT actual skill invocations.
# Lifted to module scope (task #384) so the parallel worker function below
# can reference it without rebuilding the set on every per-file call — and
# so worker processes (which re-import this module) see the same set.
_SKILL_REF_PLUGIN_DIRS: frozenset[str] = frozenset(
    {
        "agents",
        "commands",
        "skills",
        "hooks",
        "mcp",
        "monitors",
        "output-styles",
        "outputstyles",
        "lsp",
        "scripts",
        "tests",
        "references",
        "examples",
        "templates",
        "name",
        "ext",
        "file",
        "node",
        "my-skill",  # common prose / template placeholders
        "agent",  # singular — prose-like "skills/agent/SKILL.md" template placeholder
        "command",
        "skill",
        "hook",  # singular forms used in prose
        "rust",
        "python",
        "javascript",
        "typescript",
        "java",
        "go",
        "ruby",  # language names sometimes appear as "skills/python"
    }
)


def _strip_noise(content: str) -> str:
    """Strip non-directive regions from a file body before regex scanning.

    Removes:
    * YAML frontmatter (between leading ``---`` markers) — that's metadata,
      not executable code.
    * Fenced code blocks whose info-string is one of ``text``, ``output``,
      ``console``, or ``log`` — those are example output / log captures
      that the agent will quote verbatim, not directives the agent will
      execute.
    * HTML comments (``<!-- ... -->``) — explanatory prose hidden from
      rendered output, not directives.

    Replaces stripped regions with spaces of the same length so that
    downstream byte-offset arithmetic (if added later) stays accurate.

    Args:
        content: Raw markdown content.

    Returns:
        Content with noise regions blanked out.
    """
    # Strip leading frontmatter. Blank every non-newline char to a space but
    # KEEP the newlines, so the line numbers (and any future byte offsets) of the
    # body stay correct — the old code collapsed the whole block to one line of
    # spaces, contradicting this function's own newline-preserving contract.
    # (audit NIT doc #7)
    # CRLF-tolerant: a Windows-authored file starts with "---\r\n", so gating
    # on the literal "---\n" (LF only) would skip the strip and scan
    # subagent_type:/skills:... metadata as if it were body. \r is blanked to a
    # space by the [^\n] substitution, so the \n stays and line counts hold.
    if re.match(r"^---\r?\n", content):
        m = re.match(r"^---\r?\n.*?\r?\n---\r?\n", content, re.DOTALL)
        if m:
            content = re.sub(r"[^\n]", " ", m.group(0)) + content[m.end() :]

    def _blank(m: re.Match[str]) -> str:
        # Preserve newlines so line numbers stay correct.
        return re.sub(r"[^\n]", " ", m.group(0))

    # Strip fenced code blocks marked text/output/console/log
    fence_alt = "|".join(_NOISE_FENCE_LANGS)
    content = re.sub(
        rf"^```(?:{fence_alt})\b.*?^```",
        _blank,
        content,
        flags=re.DOTALL | re.MULTILINE | re.IGNORECASE,
    )

    # Strip HTML comments
    content = re.sub(r"<!--.*?-->", _blank, content, flags=re.DOTALL)

    # Blank lines containing Unicode box-drawing chars (U+2500-U+257F). These are
    # ASCII-art example boxes — e.g. a rendered sample finding
    # `| skills/foo/SKILL.md:7 |` drawn with box chars — NOT real intra-plugin
    # references (issue #58). Markdown tables use the ASCII pipe `|` (U+007C),
    # which is OUTSIDE this range, so real table rows are untouched; box-drawing
    # chars are impossible in a kebab-case skill name, so no genuine reference is
    # blanked. The only tradeoff — a real reference sharing a line with box art is
    # skipped — is the safe direction (under-flag xref, never invent a broken-ref).
    content = re.sub(r"(?m)^.*[─-╿].*$", _blank, content)

    return content


def _classify_dispatch(
    quote_present: bool,
    name: str,
    separator: str,
) -> tuple[str, str] | None:
    """Classify a captured dispatch match.

    Args:
        quote_present: True if the value was wrapped in matching quotes.
        name: Captured agent-name token (already stripped of quotes).
        separator: ``=`` (Python kwarg context) or ``:`` (YAML / JSON context).

    Returns:
        ``(kind, name)`` tuple where ``kind`` is one of:
        * ``"literal"`` — quoted string OR unquoted kebab/namespaced identifier
        * ``"dynamic"`` — unquoted Python-identifier value in a ``=`` context
    """
    if quote_present:
        return ("literal", name)
    # Unquoted value
    if "-" in name or ":" in name or "." in name:
        # Kebab-case or namespaced literal — YAML bare string
        return ("literal", name)
    if separator == "=":
        # Python kwarg with unquoted variable name → dynamic dispatch
        return ("dynamic", name)
    # YAML bare with single-word value → literal (e.g. `subagent_type: foo`)
    return ("literal", name)


def _extract_dispatch_refs(content: str) -> list[tuple[str, str]]:
    """Extract all subagent_type dispatch references from a file body.

    Applies the three-pattern extractor set (quoted bare-key, quoted JSON-key,
    unquoted bare-key) after stripping non-directive regions via
    :func:`_strip_noise`. Together the three patterns cover all four
    documented dispatch forms (YAML quoted, YAML bare, Python kwarg
    quoted/unquoted, JSON-object).

    Args:
        content: Raw markdown / code content.

    Returns:
        List of ``(kind, name)`` tuples where ``kind`` is ``"literal"`` or
        ``"dynamic"``. Duplicates within the same file are de-duplicated.
    """
    stripped = _strip_noise(content)
    refs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # Quoted bare-key forms (covers YAML/Python quoted, with spaces allowed).
    for match in _DISPATCH_QUOTED_VALUE.finditer(stripped):
        quoted_ref: tuple[str, str] = ("literal", match.group(2))
        if quoted_ref not in seen:
            refs.append(quoted_ref)
            seen.add(quoted_ref)

    # JSON-object quoted-key form (e.g. {"subagent_type": "agent-name"}).
    for match in _DISPATCH_JSON_QUOTED_VALUE.finditer(stripped):
        json_ref: tuple[str, str] = ("literal", match.group(2))
        if json_ref not in seen:
            refs.append(json_ref)
            seen.add(json_ref)

    # Unquoted bare-key forms — distinguishes YAML bare (literal) from
    # Python kwarg with unquoted variable (dynamic).
    for match in _DISPATCH_UNQUOTED_VALUE.finditer(stripped):
        classified = _classify_dispatch(False, match.group(2), match.group(1))
        if classified is not None and classified not in seen:
            refs.append(classified)
            seen.add(classified)

    return refs


# =============================================================================
# Per-file parallel workers (task #384)
#
# Each validator that does a `for file in files: ...` loop over per-file
# CPU-bound regex work splits that work into two halves:
#
#   1. EXTRACTION (per-file, parallelizable) — read the file, strip noise,
#      run the regex extractors. Returns pickleable tuples of raw refs.
#      This is what the worker functions below do.
#
#   2. RESOLUTION (cross-file, serial) — walk the per-file extraction
#      results in input order, call _resolve_dispatch_ref / lookup the
#      pre-built available_agents / available_skills sets, and emit
#      findings to the report.
#
# The harness (cpv_parallel_runner.parallel_scan) handles the parallelism;
# these workers stay context-free (no validator state, no closures) so they
# can be pickled across the ProcessPoolExecutor boundary.
#
# ``CPV_XREF_PARALLEL=0`` environment variable disables the parallel path
# and runs the validator serially — useful for debugging, CI environments
# that pin worker counts, and any tooling that needs deterministic stdout
# ordering across runs (parallel + processes can interleave any prints we
# might add inside workers in the future).
# =============================================================================


def _xref_parallel_enabled() -> bool:
    """Return True iff the parallel scan path should run.

    Honors the ``CPV_XREF_PARALLEL`` escape hatch — when set to ``0`` /
    ``false`` / ``no`` (case-insensitive) the validator falls back to the
    serial loop. Default is parallel-on so the common case benefits from
    the speedup without per-call configuration.
    """
    val = os.environ.get("CPV_XREF_PARALLEL", "").strip().lower()
    if val in {"0", "false", "no", "off"}:
        return False
    return True


def _xref_extract_dispatch_worker(path: Path) -> list[tuple[str, str]]:
    """Per-file parallel worker for dispatch-ref extraction.

    Reads ``path``, applies noise stripping, runs the dispatch-ref
    extractor. Returns ``[(kind, name), ...]`` tuples where ``kind`` is
    ``"literal"`` or ``"dynamic"``. Empty list if the file is unreadable
    or contains no dispatch refs.

    NOTE: Read errors are swallowed here and reported as an empty result.
    The serial join layer can't tell "no refs found" from "couldn't read"
    just from the worker return, but that matches the existing serial
    behavior (which also silently skips unreadable files via
    ``read_text(errors="ignore")``). Any exception OTHER than read errors
    propagates up to ``parallel_scan`` which captures it in
    ``ScanResult.error`` for the join layer to surface as a WARNING.
    """
    try:
        content = path.read_text(errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []
    return _extract_dispatch_refs(content)


def _xref_extract_command_worker(path: Path) -> list[tuple[str, str]]:
    """Per-file parallel worker for command-file ref extraction.

    Commands trigger TWO kinds of extraction:
      * Dispatch refs (subagent_type ...): returned as ``("dispatch", "<kind>:<name>")``
        where ``<kind>`` is ``literal`` or ``dynamic``.
      * Prose "spawn <agent> agent" matches: returned as ``("spawn", "<name>")``.

    Tagging the kind in the return tuple lets the serial join layer
    distinguish the two extraction families without re-running the regex.
    Returns ``[]`` if the file is unreadable.

    The encoded ``"<kind>:<name>"`` packing for dispatch refs keeps the
    return type a uniform ``list[tuple[str, str]]`` — important because
    ``ScanResult.findings`` is a single list and we want to preserve the
    extraction order across both ref families (dispatch refs first, then
    spawn refs, matching the original serial code's order).
    """
    try:
        content = path.read_text(errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []

    out: list[tuple[str, str]] = []
    # Dispatch refs (same extractor as the dispatch worker)
    for kind, name in _extract_dispatch_refs(content):
        out.append(("dispatch", f"{kind}:{name}"))

    # Spawn-prose refs — share the noise-stripped body with dispatch to
    # match the original serial behavior (see validate_command_agent_refs).
    stripped = _strip_noise(content)
    for spawn_ref in AGENT_SPAWN_PATTERN.findall(stripped):
        out.append(("spawn", spawn_ref))

    return out


def _xref_extract_skill_refs_worker(path: Path) -> list[str]:
    """Per-file parallel worker for skill-ref extraction.

    Reads ``path``, applies noise stripping, runs ``SKILL_REF_PATTERN``,
    filters out plugin-structural directory names and trailing-hyphen
    captures (the belt-and-suspenders defense from issue #27).

    Returns a list of skill-name strings IN THE ORDER they appeared in
    the file. The serial join layer de-duplicates per its own existing
    logic (``set(filtered_matches)``); we keep duplicates here so the
    de-dup happens at the same point in the pipeline as before.
    """
    try:
        content = path.read_text(errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []

    stripped = _strip_noise(content)
    matches = SKILL_REF_PATTERN.findall(stripped)

    # Apply the same two filters as the serial code:
    # 1. Plugin-structural directory names ("agents", "commands", etc.)
    # 2. Belt-and-suspenders against trailing-hyphen captures (issue #27)
    return [m for m in matches if m.lower() not in _SKILL_REF_PLUGIN_DIRS and not m.endswith("-")]


def _get_plugin_name(plugin_root: Path) -> str | None:
    """Read the plugin's manifest name from ``.claude-plugin/plugin.json``.

    The name is used to distinguish in-plugin namespaced references
    (``<my-plugin>:<agent>``) from cross-plugin references (``<other>:<agent>``).

    Args:
        plugin_root: Root path of the plugin.

    Returns:
        Plugin name from the manifest, or ``None`` if not found.
    """
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    if not plugin_json.exists():
        return None
    try:
        manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
        name = manifest.get("name")
        return name if isinstance(name, str) else None
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_dispatch_ref(
    name: str,
    available_agents: set[str],
    *,
    plugin_name: str | None = None,
    user_scope_agents: set[str] | None = None,
) -> tuple[str, str | None]:
    """Resolve a literal dispatch reference against built-ins, in-plugin
    agents, and (optionally) user-scope agents.

    Args:
        name: The captured reference token (may be bare ``agent-name`` or
            namespaced ``plugin:agent``).
        available_agents: Set of in-plugin agent names from ``agents/``.
        plugin_name: Name of the current plugin (for namespace matching).
            When the reference is ``<plugin_name>:<agent>``, it's treated
            as same-plugin and looked up in ``available_agents``.
        user_scope_agents: Optional set of agent names from
            ``~/.claude/agents/`` (used when auditing user-scope, not
            plugin-scope). When ``None``, user-scope is not checked.

    Returns:
        ``(status, canonical)`` where ``status`` is one of:

        * ``"ok"`` — resolves cleanly to a built-in, an in-plugin agent,
          or (when ``user_scope_agents`` is provided) a user-scope agent.
        * ``"ok-fuzzy"`` — resolves via the v2.1.140 case/separator-
          insensitive matcher (works at runtime but isn't canonical).
        * ``"cross_plugin"`` — namespaced to a different plugin, cannot
          statically verify (NIT).
        * ``"ghost"`` — unresolved (CRITICAL).

        ``canonical`` is the canonical agent filename when ``status``
        is ``"ok-fuzzy"``, else ``None``.
    """
    # Built-in agent check (case/separator-insensitive per v2.1.140).
    normalized = _normalize_subagent_type(name)
    if normalized in {_normalize_subagent_type(a) for a in BUILTIN_AGENTS}:
        return ("ok", None)

    # Namespaced reference: <namespace>:<agent>
    if ":" in name:
        ns, _, agent_part = name.partition(":")
        if plugin_name and ns == plugin_name:
            # Same-plugin namespaced reference — check in-plugin agents
            if agent_part in available_agents:
                return ("ok", None)
            canonical = {_normalize_subagent_type(a): a for a in available_agents}.get(
                _normalize_subagent_type(agent_part)
            )
            if canonical is not None:
                return ("ok-fuzzy", canonical)
            return ("ghost", None)
        # Different namespace — cross-plugin, cannot statically verify
        return ("cross_plugin", None)

    # Bare reference — exact match in same plugin's agents/
    if name in available_agents:
        return ("ok", None)

    # v2.1.140 case/separator-insensitive match against same plugin
    normalized_to_canonical = {_normalize_subagent_type(a): a for a in available_agents}
    canonical = normalized_to_canonical.get(normalized)
    if canonical is not None:
        return ("ok-fuzzy", canonical)

    # User-scope agents (only when explicitly provided)
    if user_scope_agents is not None:
        if name in user_scope_agents:
            return ("ok", None)
        canonical = {_normalize_subagent_type(a): a for a in user_scope_agents}.get(normalized)
        if canonical is not None:
            return ("ok-fuzzy", canonical)

    return ("ghost", None)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class CrossReferenceValidationReport(ValidationReport):
    """Validation report for cross-references, extends base ValidationReport.

    Attributes:
        plugin_path: Path to the plugin being validated
        agent_refs: Dict mapping source files to their agent references
        skill_refs: Dict mapping source files to their skill references
        version_sources: Dict mapping file names to versions found
        hook_script_refs: List of hook script paths referenced
    """

    plugin_path: str = ""
    agent_refs: dict[str, list[str]] = field(default_factory=dict)
    skill_refs: dict[str, list[str]] = field(default_factory=dict)
    version_sources: dict[str, str] = field(default_factory=dict)
    hook_script_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        base = super().to_dict()
        base["plugin_path"] = self.plugin_path
        base["agent_refs"] = self.agent_refs
        base["skill_refs"] = self.skill_refs
        base["version_sources"] = self.version_sources
        base["hook_script_refs"] = self.hook_script_refs
        return base


# =============================================================================
# Helper Functions
# =============================================================================


def get_available_agents(plugin_root: Path) -> set[str]:
    """Get set of available agent names from agents/ directory.

    Args:
        plugin_root: Root path of the plugin

    Returns:
        Set of agent names (without .md extension)
    """
    agents_dir = plugin_root / "agents"
    if not agents_dir.exists():
        return set()

    agents = set()
    for agent_file in agents_dir.glob("*.md"):
        # Extract agent name from filename (remove .md extension)
        agent_name = agent_file.stem
        agents.add(agent_name)
    return agents


def get_available_skills(plugin_root: Path) -> set[str]:
    """Get set of available skill names from skills/ directory.

    Args:
        plugin_root: Root path of the plugin

    Returns:
        Set of skill names (directory names in skills/)
    """
    skills_dir = plugin_root / "skills"
    if not skills_dir.exists():
        return set()

    skills = set()
    for skill_dir in skills_dir.iterdir():
        if skill_dir.is_dir() and not skill_dir.name.startswith("."):
            skills.add(skill_dir.name)
    return skills


def should_skip_dir(path: Path) -> bool:
    """Check if directory should be skipped during scanning.

    Args:
        path: Path to check

    Returns:
        True if directory should be skipped
    """
    return should_skip_directory(path.name) or path.name.startswith(".")


def parse_yaml_frontmatter(content: str) -> dict[str, Any] | None:
    """Parse YAML frontmatter from markdown content.

    Args:
        content: Markdown file content

    Returns:
        Parsed frontmatter dict or None if not found/invalid
    """
    # Line-based opener/closer scan (same approach validate_rules.py adopted).
    # The old content.split("---", 2) split on the FIRST two "---" anywhere,
    # corrupting frontmatter whose VALUE contains "---" (e.g.
    # `description: "use --- here"`) — it would treat the value's "---" as the
    # closing fence (audit n7). Matching whole "---" lines (CRLF-tolerant via
    # .strip()) avoids that.
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return None

    closing_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing_idx = idx
            break
    if closing_idx is None:
        return None

    fm_text = "\n".join(lines[1:closing_idx])
    try:
        frontmatter = yaml.safe_load(fm_text)
        return frontmatter if isinstance(frontmatter, dict) else None
    except yaml.YAMLError:
        return None


# =============================================================================
# Rule 1: Agent Task() calls must reference existing agents
# =============================================================================


def validate_agent_task_refs(
    plugin_root: Path,
    report: CrossReferenceValidationReport,
    available_agents: set[str],
    *,
    plugin_name: str | None = None,
) -> None:
    """Validate that Task() calls reference existing agents.

    Parses agent .md files for Task tool references with subagent_type
    and verifies the referenced agents exist in agents/ directory.

    Per TRDD-25b9be90, an unresolved reference is CRITICAL (silent-failure
    class — at runtime the call no-ops and the agent skill thinks it spawned
    a worker but nothing happens). Dynamic dispatch (``subagent_type=var``)
    is MINOR with code ``RC-GHOST-DISPATCH-002``. Cross-plugin namespaced
    references are NIT with code ``RC-GHOST-DISPATCH-003``.

    Args:
        plugin_root: Root path of the plugin
        report: Validation report to add results to
        available_agents: Set of available agent names
        plugin_name: Name of the current plugin (for namespaced reference
            resolution). Read from ``.claude-plugin/plugin.json`` if not
            provided.
    """
    agents_dir = plugin_root / "agents"
    if not agents_dir.exists():
        report.info("No agents/ directory found - skipping Task() reference check")
        return

    if plugin_name is None:
        plugin_name = _get_plugin_name(plugin_root)

    # Collect agent files in deterministic order so parallel + serial paths
    # produce identical finding ordering. ``glob`` order varies by platform
    # (filesystem-dependent on macOS/Linux); ``sorted`` pins it.
    agent_files = sorted(agents_dir.glob("*.md"))

    # Task #384: extract per-file refs in parallel, then resolve serially.
    # The CPV_XREF_PARALLEL=0 escape hatch routes through the same code path
    # via parallel_scan with a synthetic single-result list — easier than
    # duplicating the resolve/emit loop for the serial fallback.
    if _xref_parallel_enabled() and len(agent_files) > 1:
        scan_results = parallel_scan(agent_files, _xref_extract_dispatch_worker)
    else:
        scan_results = [
            ScanResult(
                file_path=f,
                findings=_xref_extract_dispatch_worker(f),
                error=None,
            )
            for f in agent_files
        ]

    for result in scan_results:
        rel_path = str(result.file_path.relative_to(plugin_root))

        if result.error is not None:
            # A worker raised — surface as MINOR (matches the old serial
            # "Could not read agent file" path, which was also MINOR).
            report.minor(
                f"Could not read agent file: {result.error}",
                rel_path,
            )
            continue

        refs = result.findings
        if refs:
            report.agent_refs[rel_path] = [name for _, name in refs]

        for kind, ref_agent in refs:
            if kind == "dynamic":
                report.minor(
                    f"[{RC_GHOST_DISPATCH_DYNAMIC}] Task() uses dynamic subagent_type "
                    f"'{ref_agent}' (variable reference — cannot statically verify)",
                    rel_path,
                )
                continue

            status, canonical = _resolve_dispatch_ref(ref_agent, available_agents, plugin_name=plugin_name)
            if status == "ok":
                report.passed(
                    f"Task() reference to '{ref_agent}' is valid",
                    rel_path,
                )
            elif status == "ok-fuzzy":
                report.nit(
                    f"Task() reference '{ref_agent}' resolves to "
                    f"agents/{canonical}.md via the v2.1.140 case/separator-insensitive "
                    f"matcher. Use the canonical kebab-case form '{canonical}' for clarity.",
                    rel_path,
                )
            elif status == "cross_plugin":
                report.nit(
                    f"[{RC_GHOST_DISPATCH_CROSS_PLUGIN}] Task() references cross-plugin "
                    f"agent '{ref_agent}' — cannot statically verify; ensure the target "
                    f"plugin is installed at runtime",
                    rel_path,
                )
            else:  # ghost
                report.critical(
                    f"[{RC_GHOST_DISPATCH_UNRESOLVED}] Task() references non-existent "
                    f"agent '{ref_agent}' — runtime will silently no-op",
                    rel_path,
                )


# =============================================================================
# Rule 2: Subagent_type must match actual agent filenames
# =============================================================================


def _normalize_subagent_type(name: str) -> str:
    """Normalize a subagent_type reference per CC v2.1.140 matching rules.

    CC v2.1.140 accepts case- and separator-insensitive values:
    ``"Code Reviewer"`` resolves to ``code-reviewer``. We mirror the same
    rule so CPV doesn't flag legal-but-non-canonical spellings as MAJOR.

    Rules:
    * Lowercase the string
    * Replace whitespace and underscores with hyphens
    * Collapse consecutive hyphens
    """
    s = name.strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s


def validate_subagent_type_matching(
    plugin_root: Path,
    report: CrossReferenceValidationReport,
    available_agents: set[str],
    *,
    plugin_name: str | None = None,
) -> None:
    """Validate subagent_type values match actual agent filenames.

    Scans all markdown files for subagent_type references (using the
    four-variant extractor — handles YAML quoted, YAML bare, Python kwarg,
    and JSON-object forms) and verifies each NAME exists in ``agents/``.

    Per TRDD-25b9be90, an unresolved literal is CRITICAL ``RC-GHOST-DISPATCH-001``
    (silent failure class), dynamic dispatch is MINOR ``RC-GHOST-DISPATCH-002``,
    and cross-plugin namespaced references are NIT ``RC-GHOST-DISPATCH-003``.
    Per CC v2.1.140 the resolver accepts case- and separator-insensitive
    values; CPV accepts those too with a NIT recommending the canonical form.

    Args:
        plugin_root: Root path of the plugin
        report: Validation report to add results to
        available_agents: Set of available agent names
        plugin_name: Name of the current plugin (for namespaced reference
            resolution). Read from ``.claude-plugin/plugin.json`` if not
            provided.
    """
    if plugin_name is None:
        plugin_name = _get_plugin_name(plugin_root)

    # Only scan executable plugin content. Skipping documentation /
    # dev-artifact / report directories (design/, docs_dev/, reports/,
    # reports_dev/, samples_dev/, examples*/, tests/, scripts_dev/, etc.)
    # so that TRDD bodies and audit reports describing the very pattern
    # this validator detects don't false-positive on themselves.
    executable_dirs = ("agents", "commands", "skills")
    md_files: list[Path] = []
    for ed in executable_dirs:
        ed_path = plugin_root / ed
        if ed_path.is_dir():
            md_files.extend(ed_path.rglob("*.md"))

    # Filter hidden/cache dirs BEFORE handing the list to the parallel
    # scanner. This way the worker count + ordering match what the
    # serial path used to see, and we don't waste worker time on files
    # we'll discard. Sorting pins per-platform glob order so parallel
    # output stays deterministic.
    md_files = sorted(md for md in md_files if not any(should_skip_dir(p) for p in md.parents))

    # Task #384: per-file extraction in parallel, resolution serial.
    if _xref_parallel_enabled() and len(md_files) > 1:
        scan_results = parallel_scan(md_files, _xref_extract_dispatch_worker)
    else:
        scan_results = [
            ScanResult(
                file_path=f,
                findings=_xref_extract_dispatch_worker(f),
                error=None,
            )
            for f in md_files
        ]

    for result in scan_results:
        rel_path = str(result.file_path.relative_to(plugin_root))

        if result.error is not None:
            # Worker raised — surface as WARNING per task #384 contract.
            # The original serial code silently skipped on read errors, so
            # WARNING is strictly more informative without breaking
            # backward compatibility on exit codes (WARNING never blocks).
            report.warning(
                f"Could not extract dispatch refs from {rel_path}: {result.error}",
                rel_path,
            )
            continue

        refs = result.findings

        for kind, ref_agent in refs:
            if kind == "dynamic":
                report.minor(
                    f"[{RC_GHOST_DISPATCH_DYNAMIC}] subagent_type uses dynamic value "
                    f"'{ref_agent}' (variable reference — cannot statically verify)",
                    rel_path,
                )
                continue

            status, canonical = _resolve_dispatch_ref(ref_agent, available_agents, plugin_name=plugin_name)
            if status == "ok":
                # Canonical spelling or built-in — silent pass.
                continue
            if status == "ok-fuzzy":
                report.nit(
                    f"subagent_type '{ref_agent}' resolves to "
                    f"agents/{canonical}.md via the v2.1.140 case/separator-"
                    f"insensitive matcher. Use the canonical kebab-case form "
                    f"'{canonical}' for clarity.",
                    rel_path,
                )
            elif status == "cross_plugin":
                report.nit(
                    f"[{RC_GHOST_DISPATCH_CROSS_PLUGIN}] subagent_type references "
                    f"cross-plugin agent '{ref_agent}' — cannot statically verify; "
                    f"ensure the target plugin is installed at runtime",
                    rel_path,
                )
            else:  # ghost
                report.critical(
                    f"[{RC_GHOST_DISPATCH_UNRESOLVED}] subagent_type '{ref_agent}' "
                    f"has no matching agents/{ref_agent}.md — runtime will silently no-op",
                    rel_path,
                )


# =============================================================================
# Rule 3: Version synchronization
# =============================================================================


def validate_version_sync(
    plugin_root: Path,
    report: CrossReferenceValidationReport,
) -> None:
    """Validate version consistency across plugin files.

    Checks version in:
    - .claude-plugin/plugin.json (version field)
    - marketplace entry (if parent is a marketplace)
    - README.md (if version is mentioned)

    Args:
        plugin_root: Root path of the plugin
        report: Validation report to add results to
    """
    versions_found: dict[str, str] = {}

    # Check plugin.json
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    if plugin_json.exists():
        try:
            manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
            if "version" in manifest:
                versions_found["plugin.json"] = manifest["version"]
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, KeyError, TypeError):
            # Narrow, fail-fast except: a genuine programming error (e.g. an
            # AttributeError introduced by a refactor) propagates instead of
            # silently degrading to "version source absent".
            pass

    # Check README.md for version mentions
    readme_path = plugin_root / "README.md"
    if readme_path.exists():
        try:
            content = readme_path.read_text(errors="ignore")
            # Look for version patterns like "Version: 1.0.0" or "version = 1.0.0"
            match = VERSION_PATTERN.search(content)
            if match:
                versions_found["README.md"] = match.group(1)
        except (OSError, UnicodeDecodeError):
            pass

    # Check marketplace.json in parent directory (if plugin is in a marketplace)
    marketplace_json = plugin_root.parent / "marketplace.json"
    if marketplace_json.exists():
        try:
            marketplace = json.loads(marketplace_json.read_text(encoding="utf-8"))
            plugins = marketplace.get("plugins", [])
            plugin_name = plugin_root.name
            for plugin_entry in plugins:
                if plugin_entry.get("name") == plugin_name:
                    if "version" in plugin_entry:
                        versions_found["marketplace.json"] = plugin_entry["version"]
                    break
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, KeyError, TypeError, AttributeError):
            # AttributeError tolerated here: marketplace entries are arbitrary
            # JSON, so plugin_entry may legitimately not be a dict (.get fails).
            pass

    # Check pyproject.toml for version (Python plugins)
    pyproject = plugin_root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            match = re.search(r'version\s*=\s*["\'](\d+\.\d+\.\d+)["\']', content)
            if match:
                versions_found["pyproject.toml"] = match.group(1)
        except (OSError, UnicodeDecodeError):
            pass

    # Check SKILL.md files for version in frontmatter
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for skill_subdir in skills_dir.iterdir():
            if not skill_subdir.is_dir():
                continue
            skill_md = skill_subdir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
                # Use the line-based frontmatter parser rather than
                # content.find("---", 3): the latter stops at the FIRST "---"
                # substring after the opener, so a "---" inside a value (e.g.
                # description: "a --- b") truncates the block and a real
                # version: line after it is silently dropped — masking a genuine
                # version-sync mismatch. parse_yaml_frontmatter matches whole
                # "---" delimiter lines only.
                frontmatter = parse_yaml_frontmatter(content)
                if frontmatter is not None and "version" in frontmatter:
                    ver = str(frontmatter["version"]).strip().strip("'\"")
                    if ver:
                        versions_found[f"skills/{skill_subdir.name}/SKILL.md"] = ver
            except (OSError, UnicodeDecodeError):
                pass

    report.version_sources = versions_found

    if len(versions_found) < 2:
        report.info(f"Only {len(versions_found)} version source(s) found - sync check skipped")
        return

    # Check for version mismatches
    unique_versions = set(versions_found.values())
    if len(unique_versions) == 1:
        version = list(unique_versions)[0]
        report.passed(f"All {len(versions_found)} version sources agree: {version}")
    else:
        version_list = ", ".join(f"{src}={ver}" for src, ver in versions_found.items())
        report.major(f"Version mismatch detected: {version_list}")


# =============================================================================
# Rule 4: Breaking references for commands calling non-existent agents
# =============================================================================


def validate_command_agent_refs(
    plugin_root: Path,
    report: CrossReferenceValidationReport,
    available_agents: set[str],
    *,
    plugin_name: str | None = None,
) -> None:
    """Validate that commands do not reference non-existent agents.

    Scans command .md files for agent references (subagent_type literals
    via the TRDD-25b9be90 four-variant extractor, plus spawn/invoke prose
    patterns) and verifies the referenced agents exist.

    Per TRDD-25b9be90, an unresolved literal is CRITICAL ``RC-GHOST-DISPATCH-001``,
    dynamic dispatch is MINOR ``RC-GHOST-DISPATCH-002``, and cross-plugin
    namespaced references are NIT ``RC-GHOST-DISPATCH-003``.

    Args:
        plugin_root: Root path of the plugin
        report: Validation report to add results to
        available_agents: Set of available agent names
        plugin_name: Name of the current plugin (for namespaced reference
            resolution). Read from ``.claude-plugin/plugin.json`` if not
            provided.
    """
    commands_dir = plugin_root / "commands"
    if not commands_dir.exists():
        report.info("No commands/ directory found - skipping command agent ref check")
        return

    if plugin_name is None:
        plugin_name = _get_plugin_name(plugin_root)

    cmd_files = sorted(commands_dir.glob("*.md"))

    # Task #384: per-file extraction parallel, resolution serial. The
    # command worker emits a tagged stream of (kind, payload) tuples
    # interleaving dispatch refs and spawn refs in the order they
    # appeared in the file. The join loop below splits them back out
    # so the existing report-emission order (dispatch findings first
    # per file, then spawn findings) is preserved.
    if _xref_parallel_enabled() and len(cmd_files) > 1:
        scan_results = parallel_scan(cmd_files, _xref_extract_command_worker)
    else:
        scan_results = [
            ScanResult(
                file_path=f,
                findings=_xref_extract_command_worker(f),
                error=None,
            )
            for f in cmd_files
        ]

    # Precompute the fuzzy-match index outside the loop — it depends only
    # on the available_agents / BUILTIN_AGENTS sets, not per-file content.
    # Building it once instead of N times shaves a per-file allocation.
    _available_normalized = {_normalize_subagent_type(a) for a in available_agents}
    _builtin_normalized = {_normalize_subagent_type(a) for a in BUILTIN_AGENTS}

    for result in scan_results:
        rel_path = str(result.file_path.relative_to(plugin_root))

        if result.error is not None:
            # Worker raised — surface as MINOR (matches the old serial
            # "Could not read command file" path).
            report.minor(
                f"Could not read command file: {result.error}",
                rel_path,
            )
            continue

        # Split the tagged stream back into the two ref families. We keep
        # extraction order within each family by appending — the worker
        # already emits dispatch refs before spawn refs (per the original
        # serial code's order).
        dispatch_refs: list[tuple[str, str]] = []
        spawn_refs: list[str] = []
        for tag, payload in result.findings:
            if tag == "dispatch":
                # payload is encoded as "<kind>:<name>" by the worker
                kind, _, name = payload.partition(":")
                dispatch_refs.append((kind, name))
            elif tag == "spawn":
                spawn_refs.append(payload)
            # Unknown tags are silently ignored — keeps the join layer
            # forward-compatible if the worker grows new categories.

        # Subagent_type references via the four-variant extractor (TRDD-25b9be90)
        for kind, ref_agent in dispatch_refs:
            if kind == "dynamic":
                report.minor(
                    f"[{RC_GHOST_DISPATCH_DYNAMIC}] Command uses dynamic subagent_type "
                    f"'{ref_agent}' (variable reference — cannot statically verify)",
                    rel_path,
                )
                continue

            status, canonical = _resolve_dispatch_ref(ref_agent, available_agents, plugin_name=plugin_name)
            if status == "ok":
                report.passed(
                    f"Command reference to agent '{ref_agent}' is valid",
                    rel_path,
                )
            elif status == "ok-fuzzy":
                report.nit(
                    f"Command reference '{ref_agent}' resolves to "
                    f"agents/{canonical}.md via the v2.1.140 case/separator-insensitive "
                    f"matcher. Use the canonical kebab-case form '{canonical}' for clarity.",
                    rel_path,
                )
            elif status == "cross_plugin":
                report.nit(
                    f"[{RC_GHOST_DISPATCH_CROSS_PLUGIN}] Command references cross-plugin "
                    f"agent '{ref_agent}' — cannot statically verify; ensure the target "
                    f"plugin is installed at runtime",
                    rel_path,
                )
            else:  # ghost
                report.critical(
                    f"[{RC_GHOST_DISPATCH_UNRESOLVED}] Command references non-existent "
                    f"agent '{ref_agent}' — runtime will silently no-op (BREAKING)",
                    rel_path,
                )

        # Spawn/invoke prose patterns (heuristic — keeps the existing behavior
        # but uses the corrected BUILTIN_AGENTS set instead of the old wrong list
        # which contained model names + scout/oracle as if they were built-ins).
        for ref_agent in spawn_refs:
            ref_agent_normalized = _normalize_subagent_type(ref_agent)
            # (doc #9) The normalized-vs-raw `in available_agents` check was
            # redundant — `_available_normalized` (normalized forms of the same
            # set) fully covers it on the next line.
            if ref_agent_normalized in _available_normalized:
                continue  # v2.1.140 fuzzy match
            if ref_agent_normalized in _builtin_normalized:
                continue
            # (issue #110) A bare single-word candidate ("explicit", "specific",
            # "single", "new") adjacent to "agent" is ordinary prose, not a
            # dispatch target — a real agent name is kebab/snake-cased or carries
            # a digit. Skip the advisory WARNING for such words. A hyphenated
            # unknown agent ("evil-exfil-agent") still warns; the CRITICAL
            # subagent_type: ghost-dispatch path above is untouched.
            if not _LOOKS_LIKE_AGENT_IDENTIFIER.search(ref_agent):
                continue
            # (doc #6) This is a PROSE heuristic — it fires on innocuous English
            # like "use the browser agent" / "we use the X agent". It is NOT a
            # structural dispatch error (the ghost-dispatch path above stays
            # CRITICAL), so per the calibration rule it is advisory WARNING, not
            # a blocking MAJOR.
            report.warning(
                f"Command prose mentions a possible agent name '{ref_agent}' that is not a known agent "
                "(heuristic — ignore if this is ordinary prose, not a dispatch)",
                rel_path,
            )


# =============================================================================
# Rule 5: Skills referenced in code should exist
# =============================================================================


def validate_skill_refs(
    plugin_root: Path,
    report: CrossReferenceValidationReport,
    available_skills: set[str],
) -> None:
    """Validate that skill references point to existing skills.

    Scans executable plugin content (agents/, commands/, skills/) for
    ``skills/<name>`` path references and verifies each name exists in
    the plugin's ``skills/`` directory.

    Per TRDD-25b9be90 Phase 5 (scope narrowing): we no longer scan
    ``scripts/`` or other top-level files, because Python comments and
    docstrings commonly contain prose like ``"skills/agents/commands"``
    that mention the plugin's directory layout — those aren't skill
    references and were generating ~225 false positives per CPV self-scan.

    Args:
        plugin_root: Root path of the plugin
        report: Validation report to add results to
        available_skills: Set of available skill names
    """
    # Compare case-insensitively on BOTH sides. The reference is lowercased
    # below (skill_name.lower()); available_skills is built from raw directory
    # names, so a skill dir "MySkill" would never match a lowercased ref and
    # produce a false "non-existent skill" MAJOR. Lowercasing the lookup set
    # closes that asymmetry. (Skill dirs are conventionally kebab-case-lower,
    # so this rarely bites — but the asymmetry was a real soundness gap.)
    available_skills_lower = {s.lower() for s in available_skills}
    # Scope (TRDD-25b9be90 Phase 5): top-level executable bodies only —
    # agent bodies, command bodies, and skill BODIES (skills/<name>/SKILL.md).
    # Excludes references/ subdirs because those are documentation containing
    # example skill names like ``deploy`` / ``skill-a`` / ``codebase-visualizer``
    # that aren't actual invocations.
    file_paths: list[Path] = []
    agents_dir = plugin_root / "agents"
    if agents_dir.is_dir():
        file_paths.extend(agents_dir.glob("*.md"))
    commands_dir = plugin_root / "commands"
    if commands_dir.is_dir():
        file_paths.extend(commands_dir.glob("*.md"))
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        # Only the top-level SKILL.md per skill — skip references/, scripts/, etc.
        for skill_subdir in skills_dir.iterdir():
            if not skill_subdir.is_dir():
                continue
            skill_md = skill_subdir / "SKILL.md"
            if skill_md.is_file():
                file_paths.append(skill_md)

    # Filter hidden/cache dirs BEFORE parallel dispatch and sort for
    # deterministic order across platforms. (task #384)
    file_paths = sorted(fp for fp in file_paths if not any(should_skip_dir(p) for p in fp.parents))

    # Task #384: per-file extraction parallel, serial join below. The
    # worker handles read + _strip_noise + regex + the _plugin_dirs and
    # trailing-hyphen filters (matching the old serial code exactly).
    if _xref_parallel_enabled() and len(file_paths) > 1:
        scan_results = parallel_scan(file_paths, _xref_extract_skill_refs_worker)
    else:
        scan_results = [
            ScanResult(
                file_path=f,
                findings=_xref_extract_skill_refs_worker(f),
                error=None,
            )
            for f in file_paths
        ]

    for result in scan_results:
        rel_path = str(result.file_path.relative_to(plugin_root))

        if result.error is not None:
            # The serial code silently skipped read errors; WARNING here
            # is strictly more informative without changing exit codes.
            report.warning(
                f"Could not extract skill refs from {rel_path}: {result.error}",
                rel_path,
            )
            continue

        filtered_matches = result.findings
        if filtered_matches:
            report.skill_refs[rel_path] = list(set(filtered_matches))

        for skill_name in set(filtered_matches):
            skill_name_lower = skill_name.lower()
            if skill_name_lower not in available_skills_lower:
                report.major(
                    f"Reference to non-existent skill '{skill_name}'",
                    rel_path,
                )
            else:
                report.passed(
                    f"Skill reference '{skill_name}' is valid",
                    rel_path,
                )


# =============================================================================
# Rule 6: Hook scripts referenced in hooks.json must exist
# =============================================================================


def validate_hook_script_refs(
    plugin_root: Path,
    report: CrossReferenceValidationReport,
) -> None:
    """Validate that hook script references in hooks.json exist.

    Parses hooks/hooks.json (and any hooks referenced in plugin.json)
    and verifies that all script paths exist.

    Args:
        plugin_root: Root path of the plugin
        report: Validation report to add results to
    """
    hooks_files: list[Path] = []

    # Check default hooks/hooks.json
    default_hooks = plugin_root / "hooks" / "hooks.json"
    if default_hooks.exists():
        hooks_files.append(default_hooks)

    # Check for hooks referenced in plugin.json
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    if plugin_json.exists():
        try:
            manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
            if "hooks" in manifest:
                hooks_val = manifest["hooks"]
                if isinstance(hooks_val, str):
                    # Path to hooks file. Strip a single optional "./" PREFIX
                    # only — str.lstrip("./") would treat "./" as a char set and
                    # mangle hidden-dir targets (".config/run.sh" -> "config/run.sh").
                    hooks_rel = hooks_val[2:] if hooks_val.startswith("./") else hooks_val
                    hooks_path = plugin_root / hooks_rel
                    if hooks_path.exists() and hooks_path not in hooks_files:
                        hooks_files.append(hooks_path)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass

    if not hooks_files:
        report.info("No hooks configuration found - skipping hook script check")
        return

    for hooks_file in hooks_files:
        try:
            hooks_content = hooks_file.read_text(encoding="utf-8")
            hooks_config = json.loads(hooks_content)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            report.minor(f"Could not parse hooks file: {type(e).__name__}", str(hooks_file.relative_to(plugin_root)))
            continue

        rel_hooks_path = str(hooks_file.relative_to(plugin_root))

        # Extract all script paths from hooks config
        script_paths = extract_script_paths_from_hooks(hooks_config)
        report.hook_script_refs.extend(script_paths)

        for script_path in script_paths:
            # Resolve the path relative to plugin root.
            # Scripts use ${CLAUDE_PLUGIN_ROOT} which maps to plugin_root.
            # Strip a single optional "./" PREFIX only — str.lstrip("./")
            # treats "./" as a char set and would mangle hidden-dir targets
            # (".config/run.sh" -> "config/run.sh") into a false CRITICAL.
            script_rel = script_path[2:] if script_path.startswith("./") else script_path
            resolved_path = plugin_root / script_rel

            if not resolved_path.exists():
                report.critical(
                    f"Hook references non-existent script: {script_path}",
                    rel_hooks_path,
                )
            else:
                # Check if script is executable (for shell scripts)
                if resolved_path.suffix in {".sh", ".bash"}:
                    import os

                    if not os.access(resolved_path, os.X_OK):
                        report.minor(
                            f"Hook script is not executable: {script_path}",
                            rel_hooks_path,
                        )
                    else:
                        report.passed(
                            f"Hook script exists and is executable: {script_path}",
                            rel_hooks_path,
                        )
                else:
                    report.passed(
                        f"Hook script exists: {script_path}",
                        rel_hooks_path,
                    )


def extract_script_paths_from_hooks(hooks_config: dict[str, Any]) -> list[str]:
    """Extract all script paths from hooks configuration.

    Args:
        hooks_config: Parsed hooks.json content

    Returns:
        List of script paths referenced in the hooks
    """
    script_paths: list[str] = []

    def extract_from_value(value: Any) -> None:
        """Recursively extract script paths from a value."""
        if isinstance(value, str):
            # Check for ${CLAUDE_PLUGIN_ROOT} paths
            matches = HOOK_SCRIPT_PATTERN.findall(value)
            script_paths.extend(matches)
        elif isinstance(value, dict):
            # Recurse into EVERY value. A 'command' string is reached here via
            # the str branch above, so there is no separate 'command' special
            # case — a dedicated one would scan the command string a second time
            # and append every hook path twice (the trailing dict.fromkeys dedup
            # hid it, but the redundant work and the duplicate-then-collapse were
            # real). The recursion is the single source of truth.
            for v in value.values():
                extract_from_value(v)
        elif isinstance(value, list):
            for item in value:
                extract_from_value(item)

    extract_from_value(hooks_config)
    # Order-preserving de-dup: list(set(...)) randomized finding order run-to-run
    # (a missing-script CRITICAL could appear at a nondeterministic position).
    # dict.fromkeys keeps first-seen order while still collapsing duplicates
    # (audit n5).
    return list(dict.fromkeys(script_paths))


# =============================================================================
# Main Validation Function
# =============================================================================


def validate_cross_references(plugin_path: str | Path) -> CrossReferenceValidationReport:
    """Validate all cross-references in a plugin.

    Args:
        plugin_path: Path to the plugin directory

    Returns:
        CrossReferenceValidationReport with all validation results
    """
    plugin_root = Path(plugin_path).resolve()
    report = CrossReferenceValidationReport()
    report.plugin_path = str(plugin_root)

    # Verify plugin directory exists
    if not plugin_root.exists():
        report.critical(f"Plugin directory does not exist: {plugin_root}")
        return report

    if not plugin_root.is_dir():
        report.critical(f"Plugin path is not a directory: {plugin_root}")
        return report

    # Get available components
    available_agents = get_available_agents(plugin_root)
    available_skills = get_available_skills(plugin_root)
    plugin_name = _get_plugin_name(plugin_root)

    report.info(f"Found {len(available_agents)} agent(s) in agents/")
    report.info(f"Found {len(available_skills)} skill(s) in skills/")

    # Run all validation rules
    # Rule 1: Agent Task() calls
    validate_agent_task_refs(plugin_root, report, available_agents, plugin_name=plugin_name)

    # Rule 2: Subagent_type matching
    validate_subagent_type_matching(plugin_root, report, available_agents, plugin_name=plugin_name)

    # Rule 3: Version synchronization
    validate_version_sync(plugin_root, report)

    # Rule 4: Command agent references
    validate_command_agent_refs(plugin_root, report, available_agents, plugin_name=plugin_name)

    # Rule 5: Skill references
    validate_skill_refs(plugin_root, report, available_skills)

    # Rule 6: Hook script references
    validate_hook_script_refs(plugin_root, report)

    return report


# =============================================================================
# CLI Entry Point
# =============================================================================


def main() -> int:
    """CLI entry point for cross-reference validation.

    Returns:
        Exit code (0=OK, 1=CRITICAL, 2=MAJOR, 3=MINOR)
    """
    from cpv_validation_common import launcher_epilog

    parser = argparse.ArgumentParser(
        description="Validate cross-references between Claude Code plugin components",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Checks: agent Task() refs, command-agent refs, version sync, hook script paths.

Exit codes:
    0 - All checks passed
    1 - CRITICAL issues found
    2 - MAJOR issues found
    3 - MINOR issues found

"""
        + launcher_epilog("xref"),
    )
    parser.add_argument(
        "plugin_path",
        type=str,
        help="Path to the plugin directory to validate",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all results including PASSED and INFO",
    )
    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Save detailed report to file, print only summary to stdout",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode: NIT issues also cause non-zero exit",
    )

    args = parser.parse_args()

    # Resolve to absolute path so relative_to() works correctly
    plugin_path = Path(args.plugin_path).resolve()

    # Verify this is a plugin directory
    if not plugin_path.is_dir():
        print(f"Error: {plugin_path} is not a directory", file=sys.stderr)
        return 1
    if not (plugin_path / ".claude-plugin").is_dir():
        print(
            f"Error: No Claude Code plugin found at {plugin_path}\nExpected a .claude-plugin/ directory.",
            file=sys.stderr,
        )
        return 1

    # Run validation
    report = validate_cross_references(plugin_path)

    # Output results
    if args.json:
        print(report.to_json())
    elif args.report:

        def _print_full(report, verbose=False):
            print_report_summary(report, "Cross-Reference Validation Report")
            print_results_by_level(report, verbose=verbose)

        save_report_and_print_summary(
            report,
            Path(args.report),
            "Cross-Reference Validation",
            _print_full,
            args.verbose,
            plugin_path=args.plugin_path,
        )
    else:
        print_report_summary(report, "Cross-Reference Validation Report")
        print_results_by_level(report, verbose=args.verbose)

        # Show cross-reference summary
        if args.verbose:
            print(f"\n{COLORS['BOLD']}Cross-Reference Summary:{COLORS['RESET']}")
            if report.agent_refs:
                print(f"  Agent references found in {len(report.agent_refs)} file(s)")
            if report.skill_refs:
                print(f"  Skill references found in {len(report.skill_refs)} file(s)")
            if report.version_sources:
                print(f"  Version sources: {', '.join(report.version_sources.keys())}")
            if report.hook_script_refs:
                print(f"  Hook scripts referenced: {len(report.hook_script_refs)}")

    return report.exit_code_strict() if args.strict else report.exit_code


if __name__ == "__main__":
    sys.exit(main())
