#!/usr/bin/env python3
"""Body-vs-frontmatter tool-consistency matching (TRDD-94e06820, Phase 1).

A skill / agent / command that DECLARES an ``allowed-tools`` (skills, commands)
or ``tools`` (agents) field promises the runtime it will only use the listed
tools. If the BODY instructs the model to use a tool the field does NOT grant,
that tool call fails at runtime and the component fails **silently**. This
module detects that contradiction.

Two violation classes (both CRITICAL when the usage is a real instruction):

1. Empty declaration (``[]`` / ``""`` = no tools) + body uses any tool.
2. Non-empty declaration + body uses a tool / MCP server not granted by it.

When the field is ABSENT, all tools are allowed → the caller skips this check.

Phase 1 scope: tool-NAME level (built-in tools) + MCP level. Specifier-level
matching (does a body ``Bash(npm install)`` match a declared ``Bash(git:*)``
scope; does a body file path match a declared ``Read(~/x/**)`` glob) is Phase 2
and deliberately deferred — reliably extracting a literal specifier from a
prose/markdown body is FP-prone, while the tool-NAME + MCP level already catches
the silent-failure bug.

Severity (Balanced FP policy, per user direction): a usage inside a fenced code
block is an intended instruction → CRITICAL; a usage in plain prose is most
likely documentation → non-blocking WARNING. An empty ``[]`` declaration plus
ANY usage is always CRITICAL (an explicit "no tools" component has no reason to
invoke one).

The canonical tool list is baked in from
https://code.claude.com/docs/en/tools-reference.md (fetched 2026-05-22).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Protocol

# Canonical built-in tool identifiers (PascalCase), verbatim from
# tools-reference.md. Used to recognise tool-invocation syntax in a body and to
# resolve declared rule tool-names.
CANONICAL_TOOLS: frozenset[str] = frozenset(
    {
        "Agent",
        "AskUserQuestion",
        "Bash",
        "CronCreate",
        "CronDelete",
        "CronList",
        "Edit",
        "EnterPlanMode",
        "EnterWorktree",
        "ExitPlanMode",
        "ExitWorktree",
        "Glob",
        "Grep",
        "ListMcpResourcesTool",
        "LSP",
        "Monitor",
        "NotebookEdit",
        "PowerShell",
        "PushNotification",
        "Read",
        "ReadMcpResourceTool",
        "RemoteTrigger",
        "ScheduleWakeup",
        "SendMessage",
        "ShareOnboardingGuide",
        "Skill",
        "TaskCreate",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
        "TaskUpdate",
        "TeamCreate",
        "TeamDelete",
        "TodoWrite",
        "ToolSearch",
        "WaitForMcpServers",
        "WebFetch",
        "WebSearch",
        "Write",
    }
)

# Historical / soft aliases — each side satisfies the other. ``Task`` was
# renamed to ``Agent`` in v2.1.63 but still works as an alias.
TOOL_ALIASES: dict[str, str] = {"Task": "Agent"}

# Documented cross-tool grants (tools-reference.md): declaring the KEY tool also
# grants the VALUE tools. ONLY the explicitly-documented grants are encoded —
# being conservative here avoids false-negatives (missing a real violation):
#   * "An Edit(...) allow rule also grants read access to the same path" → Read.
#   * "Monitor uses the same permission rules as Bash" → a Bash rule grants Monitor.
# The path-grammar grouping (Read/Grep/Glob/LSP share a specifier FORMAT) is NOT
# a grant and is intentionally absent: an allow-list that names only ``Read`` does
# not make ``Grep`` available.
TOOL_GROUP_GRANTS: dict[str, frozenset[str]] = {
    "Edit": frozenset({"Read"}),
    "Bash": frozenset({"Monitor"}),
}

# Tool-invocation syntax in a body: a canonical tool name (or the Task alias)
# immediately followed by ``(`` — e.g. ``Read(...)``, ``Skill({...})``,
# ``Bash(...)``. NO whitespace is allowed before ``(`` so prose like "Read (the
# file)" does not match — real tool calls are written ``Read(``. The negative
# lookbehind rejects a preceding word char / ``.`` (``xRead(`` substrings,
# ``obj.Read(`` method calls) AND ``]`` / ``:`` / ``>`` / ``-`` so markdown links
# (``[Read](url)``), namespaced calls in code fences (``ns::Read(...)``,
# ``foo:Read(``), blockquote/HTML punctuation, and hyphenated identifiers don't
# produce a false tool-usage match.
_TOOL_NAMES_FOR_RE = sorted(CANONICAL_TOOLS | set(TOOL_ALIASES), key=len, reverse=True)
_TOOL_CALL_RE = re.compile(
    r"(?<![\w.\]:>-])(" + "|".join(re.escape(t) for t in _TOOL_NAMES_FOR_RE) + r")\(",
)

# MCP tool reference: ``mcp__<server>__<tool>``. Server/tool segments may contain
# hyphens and underscores (real example:
# ``mcp__plugin_llm-externalizer_llm-externalizer__chat``). Because the underscore
# is a word character, a SINGLE greedy word-or-hyphen run after the ``mcp__``
# prefix already captures the whole multi-segment name in one pass (the double
# underscores between segments are themselves word chars). The single-run shape
# is also ReDoS-safe: any pattern that splits the segments out as separate
# repeating subpatterns ends up with overlapping quantifiers, because the
# underscore matches both the segment body class and the segment separator. On
# an adversarial all-underscore input from an untrusted plugin body, that split
# shape backtracks pathologically; the single-run shape does not, because there
# is only one repetition with no ambiguity about where each character belongs.
# The distinctive ``mcp__`` prefix keeps accidental prose matches rare, so a
# trailing ``(`` is not required.
_MCP_USAGE_RE = re.compile(r"(?<![\w-])(mcp__[\w-]+)")

# Fence delimiters for the self-contained fence tracker. Per CommonMark a fence
# delimiter may be indented 0-3 spaces; 4+ spaces is an indented code block, not
# a fence, so the indent is capped at 3 spaces (spaces only — a leading tab makes
# it an indented code block too).
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


class _ReportLike(Protocol):
    """Structural type for any CPV validator report (skill / command / agent).

    All four report classes expose ``critical`` and ``warning`` with a
    ``(message, file, line)`` positional signature; the comprehensive report's
    extra ``category`` kwarg defaults to ``None`` so the positional call works
    uniformly.
    """

    def critical(self, message: str, file: str | None = ..., line: int | None = ...) -> None: ...

    def warning(self, message: str, file: str | None = ..., line: int | None = ...) -> None: ...


@dataclass(frozen=True)
class BodyUsage:
    """A single tool/MCP usage found in a component body."""

    name: str  # canonical built-in tool name, or full mcp__server__tool
    is_mcp: bool
    line: int  # 1-based line number in the body
    in_fence: bool  # True when inside a fenced code block (intended instruction)


@dataclass(frozen=True)
class ConsistencyFinding:
    """One body-vs-frontmatter violation."""

    severity: str  # "CRITICAL" | "WARNING"
    message: str
    line: int


def resolve_alias(tool: str) -> str:
    """Map an alias to its canonical name (e.g. ``Task`` → ``Agent``)."""
    return TOOL_ALIASES.get(tool, tool)


def parse_declared_tools(value: object) -> list[str] | None:
    """Normalise a frontmatter ``allowed-tools`` / ``tools`` value to a rule list.

    Returns ``None`` when the field is ABSENT (caller must skip the check).
    Returns ``[]`` when the field is present but empty (no tools allowed).
    Otherwise returns the list of rule strings (e.g. ``["Read", "Bash(git:*)"]``),
    splitting CSV / space-delimited strings while respecting parenthesised scopes
    such as ``Bash(git:*,gh:*)``.
    """
    if value is None:
        return None
    if isinstance(value, list):
        # A YAML list item may itself be a CSV/space-delimited string (unusual but
        # valid, e.g. `- Read, Bash(git:*)`), so re-split each item through the
        # same parser and flatten — otherwise the whole item becomes one bogus
        # rule (`"Read, Bash(git:*)"`) that never matches a canonical tool name.
        rules: list[str] = []
        for item in value:
            rules.extend(_split_rules_string(str(item)))
        return rules
    if isinstance(value, str):
        return _split_rules_string(value)
    # Any other type (int/bool/dict) — treat as "present but unparseable"; the
    # caller's own type validation reports the malformed field. Return [] so the
    # consistency check does not crash, and any body usage is flagged.
    return []


def _split_rules_string(tools: str) -> list[str]:
    """Split a CSV / space-delimited rules string, respecting ``(...)`` scopes.

    Rules are separated by a top-level comma (Nixtla) OR whitespace (OpenSpec);
    commas and spaces INSIDE a ``(...)`` scope are preserved. Splitting on BOTH
    delimiters handles every form, including the mixed
    ``"Read Bash(git:*,gh:*)"`` (space-separated tools, comma inside a scope):

    * ``"Read, Bash(git:*,gh:*), Grep"`` → ``["Read", "Bash(git:*,gh:*)", "Grep"]``
    * ``"Bash(jq:*) Read"``               → ``["Bash(jq:*)", "Read"]``
    * ``"Read Bash(git:*,gh:*)"``         → ``["Read", "Bash(git:*,gh:*)"]``
    """
    tools = tools.strip()
    if not tools:
        return []
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in tools:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            if depth > 0:
                depth -= 1
                current.append(ch)
            # else: stray top-level ')' (malformed input) — drop it. The field's
            # own type/format validator reports the malformed value separately;
            # keeping it would corrupt the rule name (`Read)` → never matches).
        elif depth == 0 and (ch == "," or ch.isspace()):
            _append_rule(current, parts)
            current = []
        else:
            current.append(ch)
    _append_rule(current, parts)
    return parts


def _append_rule(chars: list[str], parts: list[str]) -> None:
    """Normalise a collected rule token and append it to ``parts`` if non-empty.

    Strips surrounding whitespace, then surrounding quotes (a token like ``"Read"``
    whose quotes survived YAML — e.g. from a CSV string ``'"Read", "Write"'`` —
    normalises to ``Read``), then any whitespace the quotes were hiding.
    """
    token = "".join(chars).strip().strip("'\"").strip()
    if token:
        parts.append(token)


def _rule_base_name(rule: str) -> str:
    """Extract the tool name from a rule, dropping any ``(specifier)``."""
    return rule.split("(", 1)[0].strip()


def granted_builtin_tools(rules: list[str]) -> set[str]:
    """Compute the set of built-in tool names a declared rule list grants.

    Expands aliases (declared ``Task`` grants ``Agent``) and the documented
    cross-tool grants (``Edit`` → ``Read``, ``Bash`` → ``Monitor``). MCP rules
    are ignored here (see :func:`declared_mcp_patterns`).
    """
    granted: set[str] = set()
    for rule in rules:
        base = _rule_base_name(rule)
        if not base or base.startswith("mcp__"):
            continue
        canonical = resolve_alias(base)
        granted.add(canonical)
        granted |= TOOL_GROUP_GRANTS.get(canonical, frozenset())
    return granted


def declared_mcp_patterns(rules: list[str]) -> list[str]:
    """Return the declared MCP rule patterns (those whose base starts ``mcp__``)."""
    patterns: list[str] = []
    for rule in rules:
        base = _rule_base_name(rule)
        if base.startswith("mcp__"):
            patterns.append(base)
    return patterns


def mcp_usage_allowed(usage: str, patterns: list[str]) -> bool:
    """Is an ``mcp__server__tool`` body usage granted by any declared pattern?

    Matches: exact equality, ``fnmatch`` glob (``mcp__github__*``), or a bare
    server prefix (``mcp__github`` grants every ``mcp__github__<tool>``).
    """
    for pattern in patterns:
        if pattern == usage:
            return True
        if ("*" in pattern or "?" in pattern or "[" in pattern) and fnmatch.fnmatchcase(usage, pattern):
            return True
        if usage.startswith(pattern + "__"):
            return True
    return False


def _fence_line_flags(body: str) -> list[bool]:
    """Return a per-line bool: True when the (0-based) line is inside a fence.

    A fence opens on a line whose first non-space run is ``` ``` ``` or ``~~~``
    (length ≥ 3) and closes on the next line with a matching-or-longer run of the
    same character. The fence delimiter lines themselves are treated as inside
    the fence (a tool call never appears on a bare delimiter line, so this is
    harmless and keeps the tracker simple).
    """
    lines = body.splitlines()
    flags = [False] * len(lines)
    fence_char: str | None = None
    fence_len = 0
    for i, line in enumerate(lines):
        m = _FENCE_RE.match(line)
        if fence_char is None:
            if m:
                run = m.group(1)
                fence_char = run[0]
                fence_len = len(run)
                flags[i] = True
        else:
            flags[i] = True
            if m:
                run = m.group(1)
                # A closing fence must match the opener's char, be at least as
                # long, AND carry no info string (CommonMark). The info-string
                # check stops a ```lang line that is CONTENT inside the fence
                # from prematurely closing it.
                if run[0] == fence_char and len(run) >= fence_len and line[m.end() :].strip() == "":
                    fence_char = None
                    fence_len = 0
    return flags


def detect_body_usages(body: str) -> list[BodyUsage]:
    """Find every built-in tool-call and MCP usage in a component body.

    De-duplicates by (name, line) so a line invoking the same tool twice yields
    one usage. Multiple distinct tools on one line each yield a usage.

    ``body`` is always the parsed component body string (``parse_frontmatter``
    returns ``""`` for a missing body, never ``None``); an empty string yields no
    usages. A non-str body is a caller-contract violation and is left to raise
    (fail-fast) rather than silently coerced.
    """
    fence_flags = _fence_line_flags(body)
    lines = body.splitlines()
    seen: set[tuple[str, int]] = set()
    usages: list[BodyUsage] = []
    for idx, line in enumerate(lines):
        in_fence = fence_flags[idx] if idx < len(fence_flags) else False
        for m in _TOOL_CALL_RE.finditer(line):
            canonical = resolve_alias(m.group(1))
            key = (canonical, idx)
            if key in seen:
                continue
            seen.add(key)
            usages.append(BodyUsage(name=canonical, is_mcp=False, line=idx + 1, in_fence=in_fence))
        for m in _MCP_USAGE_RE.finditer(line):
            name = m.group(1)
            key = (name, idx)
            if key in seen:
                continue
            seen.add(key)
            usages.append(BodyUsage(name=name, is_mcp=True, line=idx + 1, in_fence=in_fence))
    return usages


def check_body_tool_consistency(
    declared_value: object,
    body: str,
    *,
    field_name: str = "allowed-tools",
) -> list[ConsistencyFinding]:
    """Compute body-vs-frontmatter tool-consistency findings.

    ``declared_value`` is the raw frontmatter value (str / list / None). ``None``
    (field absent) → no findings (all tools allowed). Returns a list of
    :class:`ConsistencyFinding`; the caller emits them on its report object.
    """
    rules = parse_declared_tools(declared_value)
    if rules is None:
        return []  # field absent → all tools allowed, nothing to check

    is_empty = len(rules) == 0
    granted = granted_builtin_tools(rules)
    mcp_patterns = declared_mcp_patterns(rules)

    findings: list[ConsistencyFinding] = []
    for usage in detect_body_usages(body):
        if usage.is_mcp:
            if not is_empty and mcp_usage_allowed(usage.name, mcp_patterns):
                continue
            allowed = False
        else:
            allowed = (not is_empty) and (usage.name in granted)
            if allowed:
                continue

        # Severity (Balanced): empty-[] + any usage → CRITICAL; a usage inside a
        # fenced code block is an intended instruction → CRITICAL; a usage in
        # plain prose is most likely documentation → WARNING.
        critical = is_empty or usage.in_fence
        findings.append(_build_finding(usage, field_name, is_empty, critical))
    return findings


def _build_finding(usage: BodyUsage, field_name: str, is_empty: bool, critical: bool) -> ConsistencyFinding:
    """Render the message + severity for one undeclared usage."""
    kind = "MCP tool" if usage.is_mcp else "tool"
    if is_empty:
        why = f"but '{field_name}' is empty ([] = no tools allowed)"
    else:
        why = f"but the '{field_name}' field does not grant it"
    fix = (
        f"Add '{usage.name}' to '{field_name}'"
        if not usage.is_mcp
        else f"Add '{usage.name}' (or 'mcp__<server>' / 'mcp__<server>__*') to '{field_name}'"
    )
    if critical:
        verb = "invokes"
        tail = (
            "— the call will fail at runtime (silent failure). "
            f"{fix}, or remove the '{field_name}' field to allow all tools."
        )
        severity = "CRITICAL"
    else:
        verb = "mentions"
        tail = (
            f"(in prose). If this is documentation, ignore it; if the body actually uses '{usage.name}', "
            f"{fix.lower()} (or remove the '{field_name}' field) to avoid a silent runtime failure."
        )
        severity = "WARNING"
    message = f"body {verb} the {kind} '{usage.name}' (line {usage.line}) {why} {tail}"
    return ConsistencyFinding(severity=severity, message=message, line=usage.line)


def validate_body_tool_consistency(
    declared_value: object,
    body: str,
    report: _ReportLike,
    *,
    filename: str,
    field_name: str = "allowed-tools",
) -> None:
    """Run the consistency check and emit findings on ``report``.

    ``report`` must expose ``.critical(message, file, line)`` and
    ``.warning(message, file, line)`` (every CPV validator report does; the
    comprehensive report's extra ``category`` kwarg defaults to ``None``).
    """
    for finding in check_body_tool_consistency(declared_value, body, field_name=field_name):
        emit = report.critical if finding.severity == "CRITICAL" else report.warning
        emit(finding.message, filename, finding.line)
