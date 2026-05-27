#!/usr/bin/env python3
"""Shell context classifier for SkillAudit (issue #41 follow-up).

Currently detects ONE precise FP shape: a match inside a HEREDOC body
whose opener is a PRINT command (``cat`` / ``echo`` / ``printf`` / ``tee``).
Heredocs fed to print commands are user-facing help text written to
stdout/stderr/a file, NOT executed shell — so an install hint like
``npm install -g dev-browser`` printed inside ``cat >&2 <<EOF ... EOF``
is documentation, not a supply-chain action.

The classifier returns one of the standard verdict strings the
``_context_classifier_verdict`` dispatcher in ``cpv_skillaudit_native.py``
already understands:

* ``safe_doc`` — DEMOTE execution-class rules (CMD_INJECTION /
  SHELL_EXEC / SUPPLY_CHAIN / TIME_BOMB) to NIT so the agent layer can
  still triage, and KEEP intent-class rules (PROMPT_INJECT / DATA_EXFIL /
  URL_SUSPICIOUS / …) visible per the iron rule (prose CAN carry intent).
* ``""`` (unknown) — fall through to the existing heuristic chain when
  the match isn't inside a print heredoc.

Conservatively narrow on purpose: install hints inside a real ``bash -c``,
inside a function body that's actually executed, or in any non-print
context KEEP firing. The "is it inside a printed heredoc?" question is
answered by a simple opener / closer walk — no full shell parser needed.

Future extensions (shellcheck-style flow analysis, command-substitution
detection, exec-vs-print disambiguation for ``tee``) can layer on top of
this minimal start without changing the dispatcher contract.
"""

from __future__ import annotations

import re
from typing import Final

# Print commands that consume a heredoc and emit it as text (no exec).
# ``tee`` writes to a file, which is also non-exec content. We DO NOT
# include ``bash``/``sh``/``zsh`` (those *execute* the heredoc) nor
# ``eval``/``source``/``.`` (also exec). Anything not on this list keeps
# the match flagged (the conservative side of the gate).
#
# The regex requires the print command at the start of the (possibly
# indented) line, then any chars that aren't ``<`` (so things like
# ``cat >&2``, ``echo -n``, ``printf '%s\\n'`` all match), then ``<<-?``
# with the heredoc delimiter. The optional ``-`` is the indent-stripping
# heredoc variant (``<<-EOF`` lets the closer be tab-indented). The
# delimiter may be quoted (``<<'EOF'`` / ``<<"END"``) — quoting only
# changes whether ``$``-expansion happens inside the body, not whether
# the body is executed, so it's irrelevant to the print-vs-exec gate.
_PRINT_HEREDOC_OPEN_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:cat|echo|printf|tee)\b[^<]*<<-?(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1"
)


# r01 anthropic FP iter1 (2026-05-27) — CROSS_TOOL_ACCESS shell-script
# field-name discriminator. Mirrors the Python classifier's
# ``_is_api_field_name_match_py`` heuristic for shell scripts:
# ``SYSTEM_PROMPT`` / ``CONTEXT_WINDOW`` / etc. as a bash variable
# name (or env var, or `$VAR` expansion) is LLM-API domain vocabulary
# being USED, not a runtime data-grab on another tool's output.
_API_FIELD_NAMES_SHELL: Final[frozenset[str]] = frozenset(
    {
        "system_prompt",
        "system_message",
        "context_window",
        "full_context",
        "conversation_history",
        "message_history",
        "chat_history",
    }
)
# Real runtime data-grab vocabulary that would override the field-name
# heuristic: a shell script using these would be reading agent tool
# outputs / messages at runtime, which IS suspicious.
_RETRIEVAL_GRAB_RE_SHELL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:get_tools|list_tools|available_tools|call_tool|invoke_tool|use_tool)\b"
    r"|\bprevious_tool_output\b"
    r"|\btool_results?\s*\[",
    re.IGNORECASE,
)


def _is_api_field_name_match_shell(line: str, match: str) -> bool:
    """True iff a CROSS_TOOL_ACCESS match in a shell script is an
    LLM-API field NAME (used as a bash variable / env var / arg name),
    AND the surrounding line carries no runtime data-grab indicator.

    Idiomatic safe shapes:
      * ``SYSTEM_PROMPT=$(awk ...)`` — extract value from input file
      * ``$SYSTEM_PROMPT`` / ``"$SYSTEM_PROMPT"`` — use the extracted value
      * ``--system-prompt "$value"`` — CLI flag for an LLM client
      * ``export ANTHROPIC_SYSTEM_PROMPT=...`` — env var setup
    All of these are LEGITIMATE uses of the domain vocabulary in
    validation / setup / launcher scripts.
    """
    match_lower = match.lower()
    line_lower = line.lower()
    if not any(name in match_lower or name in line_lower for name in _API_FIELD_NAMES_SHELL):
        return False
    if _RETRIEVAL_GRAB_RE_SHELL.search(line):
        return False
    return True


def classify(
    file_path: str,
    content: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> str:
    """Return ``"safe_doc"`` iff ``line_idx`` is inside an OPEN print
    heredoc (opener has been seen on a previous line, closer has not yet
    been encountered). Empty string otherwise — caller falls through.

    Algorithm: scan lines [0, line_idx) once, maintaining a stack of
    currently-open print-heredoc delimiters. A line opening a new print
    heredoc pushes its delimiter; a line whose ``strip()`` equals the
    top-of-stack delimiter pops it. If the stack is non-empty after the
    scan, the match line is inside a printed heredoc → ``safe_doc``.

    r01 anthropic FP iter1 (2026-05-27) — also returns ``"safe_literal"``
    for CROSS_TOOL_ACCESS matches that are LLM-API field-name
    vocabulary in a bash variable / env var / CLI-flag context.
    """
    if not file_path:
        return ""
    fp = file_path.lower()
    if not (
        fp.endswith(".sh")
        or fp.endswith(".bash")
        or fp.endswith(".zsh")
        or fp.endswith(".fish")
    ):
        return ""
    lines = content.split("\n")
    if line_idx < 0 or line_idx >= len(lines):
        return ""

    # CROSS_TOOL_ACCESS field-name pre-check (cheap, no per-line scan).
    line_text = lines[line_idx]
    if rule_id == "CROSS_TOOL_ACCESS" and _is_api_field_name_match_shell(line_text, match):
        return "safe_literal"

    if line_idx == 0:
        return ""
    # `_` underscores below used by intent — pylint-style noqa not needed.
    open_delimiters: list[str] = []
    for i in range(line_idx):
        line = lines[i]
        # If we're inside an open heredoc, ONLY check for the closer on
        # this line — non-closer lines inside a heredoc are body content,
        # never new openers (a heredoc body is data, not commands).
        if open_delimiters and line.strip() == open_delimiters[-1]:
            open_delimiters.pop()
            continue
        if open_delimiters:
            continue
        m = _PRINT_HEREDOC_OPEN_RE.match(line)
        if m:
            open_delimiters.append(m.group(2))
    return "safe_doc" if open_delimiters else ""
