#!/usr/bin/env python3
"""JSON / JSONC schema context classifier for SkillAudit (TRDD-a4260cc6).

A regex match in a ``.json`` file is structurally different from a match
in a ``.py`` file: JSON is data, never code. The match represents one of
three cases:

1. **The match is inside a value at a SAFE_KEY path** — keys like
   ``description``, ``title``, ``keywords``, ``homepage``, ``author``,
   ``readme``, ``license``, ``$comment``, etc. exist for UI display and
   are never executed by anyone. Matches here are documentation.
2. **The match is inside a value at a DANGEROUS_KEY path** — keys like
   ``hooks.*.hooks.*.command``, ``mcpServers.*.command``,
   ``mcpServers.*.args``, ``mcpServers.*.env.*`` literally flow into
   ``subprocess.run`` / ``exec`` at plugin-load time. Matches here ARE
   exploit candidates.
3. **The match is at an unrecognised path** — defer to the heuristic
   chain.

The classifier parses the JSON, locates the path covering the matched
line, then runs the path through the SAFE/DANGEROUS allowlists.

Iron rule: any parse failure → ``"unknown"`` so the existing heuristic
chain handles it. The classifier NEVER suppresses on doubt.
"""

from __future__ import annotations

import json
from typing import Final, Literal

ContextVerdict = Literal["safe_literal", "safe_doc", "safe_schema", "suspect", "unknown"]

# Keys whose values are UI metadata / human documentation. The same key
# names appear across plugin.json, package.json, OpenAPI specs,
# JSON-Schema dialects, and a handful of CI configs — we accept all of
# them as "the value here is a human-readable string the framework
# never executes".
#
# The check is suffix-based: any path segment ending with one of these
# names is SAFE_KEY. This is intentionally generous — over-permissive
# SAFE_KEY is the FP risk we accept, and the self-smoke gate in
# publish.py catches the converse direction.
_SAFE_KEY_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "description",
        "displayName",
        "title",
        "summary",
        "longDescription",
        "keywords",
        "tags",
        "categories",
        "homepage",
        "repository",
        "bugs",
        "documentation",
        "url",
        "uri",
        "license",
        "licenses",
        "spdx",
        "author",
        "authors",
        "maintainers",
        "contributors",
        "funding",
        "name",
        "engines",
        "readme",
        "changelog",
        "default",  # JSON-Schema property defaults — string defaults can carry shell-like content as the default VALUE
        "$comment",
        "$id",
        "$schema",
        "version",
        "argument-hint",  # claude-plugin command arg hint
        "model",  # claude-plugin agent model selector
        "type",  # JSON-Schema "type" key — value is "string"/"number"/etc.
        "format",  # JSON-Schema format keyword
        "enum",  # JSON-Schema enum keyword (list of allowed values)
        "examples",  # JSON-Schema examples list
        "label",  # UI label for plugin userConfig
        "placeholder",  # UI placeholder string
        "help",  # UI help text
        "hint",  # UI hint text
        "tooltip",  # UI tooltip
        "message",  # error messages, hint messages, etc.
        "error",  # error message strings
        "warning",  # warning message strings
        "info",  # info message strings
        "note",  # note / annotation
        "explanation",  # human-readable explanation
        "rationale",  # design rationale string
        "purpose",  # JSON-Schema-like purpose string
        "subject",
        "header",
        "footer",
        "caption",
        "alt",
        "aria-label",
    }
)

# Keys whose values DO flow into runtime execution. A match inside one of
# these paths is a candidate exploit. We do NOT downgrade these to
# SUSPECT here — the caller's heuristic chain decides if the regex match
# is a real injection. We just return "suspect" so the existing
# severity is preserved.
_DANGEROUS_KEY_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "command",
        "args",
        "argv",
        "entrypoint",
        "cmd",
        "exec",
        "run",
        "script",
        "shell",
        "interpreter",
        "binary",
        "bin",
        "executable",
        "env",
        "environment",  # env vars piped into the subprocess
        "preInstall",
        "postInstall",
        "install",  # npm-style lifecycle hooks
        "preStart",
        "postStart",
        "start",
        "preStop",
        "postStop",
        "stop",
        "preBuild",
        "postBuild",
        "build",
    }
)


# r08 sangrokjung FP iter1 (2026-05-28) — Claude Code permission glob
# pattern recognition. Strings inside `permissions.allow[]` /
# `permissions.ask[]` / `permissions.deny[]` arrays in settings.json /
# settings.local.json / settings.local.template.json are tool-permission
# glob patterns matched by Claude Code's permission engine. They are
# NOT regex compiled by a vulnerable engine, NOT chmod/crontab/rm
# INVOCATIONS — they're DECLARATIONS of which Bash/Read/Write/etc.
# tool calls are allowed/denied. Matching them as REGEX_DOS, FS_WRITE,
# PRIVILEGE_ESC, CMD_INJECTION is wholly false-positive.
import re

_CLAUDE_CODE_TOOL_GLOB_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:Bash|Read|Write|Edit|MultiEdit|NotebookEdit|Task|Glob|Grep|"
    r"WebFetch|WebSearch|TodoWrite|TodoRead|Agent|Skill|Plan|ExitPlanMode|"
    r"EnterPlanMode|EnterWorktree|ExitWorktree|TaskCreate|TaskList|"
    r"TaskGet|TaskOutput|TaskUpdate|TaskStop|TeamCreate|TeamDelete|"
    r"CronCreate|CronList|CronDelete|ScheduleWakeup|ReadMcpResourceTool|"
    r"ListMcpResourcesTool|SendMessage|LSP|Wait|Sleep|Find|"
    r"Mkdir|Cp|Mv|Rm|Touch|Tee|Cat|Echo|Ls)"
    r"\([^)]*\)\*?$"
)


def _is_claude_code_permission_glob(path_segments: tuple[str, ...], value: str) -> bool:
    """True iff a JSON string value is a Claude Code tool-permission glob
    pattern (in ``settings.json`` etc.).

    Recognized shape: path includes ``permissions.{allow|ask|deny}[N]``
    AND value matches ``^(Bash|Read|Write|Edit|...)\\([^)]*\\)\\*?$``.

    Example permission strings (all suppressed when matched):
      - ``"Bash(rm -rf *)*"`` — deny-list glob for rm -rf
      - ``"Read(<sensitive-path>)"`` — deny-list glob for reading sensitive files
      - ``"Write(*>~/.bashrc)*"`` — deny-list glob for shell-rc writes
      - ``"Bash(python3 -c *import os*)*"`` — deny-list glob
    """
    named = [seg for seg in path_segments if not seg.startswith("[")]
    # Find 'permissions' followed by allow/ask/deny in the path
    for i in range(len(named) - 1):
        if named[i] == "permissions" and named[i + 1] in ("allow", "ask", "deny"):
            return bool(_CLAUDE_CODE_TOOL_GLOB_RE.match(value.strip()))
    return False


def _classify_key(path_segments: tuple[str, ...]) -> Literal["safe_schema", "suspect", "unknown"]:
    """Map a JSON path (sequence of key names) to a verdict.

    * Last segment matches SAFE_KEY → ``"safe_schema"``.
    * Any segment matches DANGEROUS_KEY → ``"suspect"``.
    * Neither → ``"unknown"``.

    Arrays index segments are ``"[<n>]"`` strings and are ignored for
    matching purposes (we look at named keys only).
    """
    named = tuple(seg for seg in path_segments if not seg.startswith("["))

    # DANGEROUS wins over SAFE — if a description nested inside a command
    # path appears (rare but possible), the dangerous outer key takes
    # precedence. This is the iron-rule preservation: keep the finding
    # visible.
    for seg in named:
        if seg in _DANGEROUS_KEY_SUFFIXES:
            return "suspect"

    if named and named[-1] in _SAFE_KEY_SUFFIXES:
        return "safe_schema"

    return "unknown"


def _walk_with_lines(
    obj: object,
    source: str,
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], int, int]]:
    """Walk a parsed JSON tree, returning ``(path, start_line, end_line)``
    tuples for every string value.

    The line range is recovered by searching the source text for the
    exact JSON-encoded value. Not perfect (two identical strings would
    collide) but adequate for the SkillAudit use case where the matcher
    points at one specific line.
    """
    out: list[tuple[tuple[str, ...], int, int]] = []
    if isinstance(obj, str):
        # Find the encoded form in source. A JSON string can be written with raw
        # non-ASCII chars (``"é"``) OR ``\uXXXX`` escapes — try the raw form
        # first (the common case), then the ASCII-escaped form. Without this a
        # value containing non-ASCII lost its line/path and the covering
        # DANGEROUS-key context (hooks[].command) was dropped. (audit MINOR #13)
        idx = -1
        encoded = ""
        for candidate in (json.dumps(obj, ensure_ascii=False), json.dumps(obj)):
            idx = source.find(candidate)
            if idx >= 0:
                encoded = candidate
                break
        if idx >= 0:
            start_line = source.count("\n", 0, idx) + 1
            end_line = source.count("\n", 0, idx + len(encoded)) + 1
            out.append((path, start_line, end_line))
    elif isinstance(obj, dict):
        for key, val in obj.items():
            out.extend(_walk_with_lines(val, source, path + (key,)))
    elif isinstance(obj, list):
        for i, val in enumerate(obj):
            out.extend(_walk_with_lines(val, source, path + (f"[{i}]",)))
    return out


def classify(
    file_path: str,
    source: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> ContextVerdict:
    """Classify a SkillAudit match in a JSON / JSONC file.

    See module docstring for the SAFE_KEY / DANGEROUS_KEY split.
    """
    # Strip JSONC // line comments before parsing. Comments are a
    # common cause of parse failures in plugin.json files that include
    # ``// ...`` for human readers.
    cleaned = _strip_jsonc_comments(source)

    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return "unknown"

    line = line_idx + 1  # 1-based

    # Find the deepest path covering this line.
    best_path: tuple[str, ...] = ()
    best_span = float("inf")
    for path, start_line, end_line in _walk_with_lines(parsed, cleaned):
        if start_line <= line <= end_line:
            span = end_line - start_line
            if span < best_span:
                best_path = path
                best_span = span

    if not best_path:
        return "unknown"

    # r08 sangrokjung FP iter1 (2026-05-28) — Claude Code permission glob
    # check. settings.json's permissions.allow/ask/deny arrays contain
    # glob-pattern strings (Bash(rm -rf *), Read(<sensitive-path>), etc.)
    # that are DECLARATIONS of which tool calls are allowed/denied, NOT
    # invocations. Scanning them as REGEX_DOS/FS_WRITE/PRIVILEGE_ESC/
    # CMD_INJECTION is provably false. Walk the parsed structure to find
    # the value at best_path and check shape.
    value_at_path = _resolve_value(parsed, best_path)
    if isinstance(value_at_path, str) and _is_claude_code_permission_glob(best_path, value_at_path):
        return "safe_schema"

    return _classify_key(best_path)


def _resolve_value(parsed: object, path: tuple[str, ...]) -> object:
    """Walk ``parsed`` following ``path`` segments. ``"[N]"`` segments
    index into lists, named segments index into dicts. Returns the value
    at the path, or ``None`` if any step fails.
    """
    cur: object = parsed
    for seg in path:
        if seg.startswith("[") and seg.endswith("]"):
            try:
                idx = int(seg[1:-1])
            except ValueError:
                return None
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or seg not in cur:
                return None
            cur = cur[seg]
    return cur


def _strip_jsonc_comments(source: str) -> str:
    """Remove ``// …`` line comments and ``/* … */`` block comments.

    Strings containing ``//`` are preserved (we track the in-string
    flag explicitly). The result is a valid JSON document iff the input
    was valid JSONC.
    """
    out: list[str] = []
    i = 0
    n = len(source)
    in_string = False
    string_quote = '"'
    while i < n:
        ch = source[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if ch == string_quote:
                in_string = False
            i += 1
            continue
        # JSON / JSONC permit ONLY double-quoted strings. Tracking single
        # quotes as string openers can only DESYNC ``in_string`` on a
        # stray apostrophe outside strings/comments (e.g. in a JSON5-ish
        # file) — never help. (audit NIT #10)
        if ch == '"':
            in_string = True
            string_quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            # Skip to next newline (preserve the newline).
            j = source.find("\n", i)
            if j < 0:
                break
            i = j
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            # Skip to closing */ (preserve any newlines inside the block
            # so line numbers stay aligned for downstream consumers).
            j = source.find("*/", i + 2)
            if j < 0:
                break
            for k in range(i, j + 2):
                if source[k] == "\n":
                    out.append("\n")
            i = j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)
