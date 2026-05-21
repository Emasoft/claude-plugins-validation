#!/usr/bin/env python3
"""Markdown context classifier for SkillAudit (TRDD-a4260cc6).

Markdown is documentation. The matcher sees a backtick-quoted
``\\`/janitor-arm\\``, an inline-code ``\\`subprocess.run\\``, or a
prose paragraph mentioning ``curl https://…`` and over-fires because
the regex doesn't know that NONE of these are executable code.

This classifier knows:

* **Prose paragraph** (no fence, no inline-code span) → SAFE_DOC.
  Prose is rendered as HTML by markdown engines; no part of it is
  executed.
* **Inline-code span** (``\\`thing\\``) → SAFE_DOC. Inline code is
  styled-as-monospace text; never executed.
* **Fenced code block with executable language tag** (``\\`\\`\\`bash``,
  ``\\`\\`\\`sh``, ``\\`\\`\\`shell``, ``\\`\\`\\`zsh``, ``\\`\\`\\`console``,
  ``\\`\\`\\`bat``, ``\\`\\`\\`cmd``, ``\\`\\`\\`powershell``,
  ``\\`\\`\\`pwsh``) → CODE_FENCE_EXECUTABLE. Matches inside fall through
  to the caller (which can apply shell-context heuristics there). For
  the matcher's purposes this is treated as "unknown" so the heuristic
  chain runs.
* **Fenced code block with data language tag** (``\\`\\`\\`json``,
  ``\\`\\`\\`yaml``, ``\\`\\`\\`toml``, ``\\`\\`\\`ini``, ``\\`\\`\\`env``,
  ``\\`\\`\\`dotenv``, ``\\`\\`\\`xml``, ``\\`\\`\\`csv``, ``\\`\\`\\`html``,
  ``\\`\\`\\`css``) → SAFE_DOC. Data formats don't execute.
* **Fenced code block with non-executable code language tag**
  (``\\`\\`\\`python``, ``\\`\\`\\`js``, ``\\`\\`\\`ts``,
  ``\\`\\`\\`go``, ``\\`\\`\\`rust``, ``\\`\\`\\`java``, etc.) →
  CODE_FENCE_NEUTRAL. The match is in an example snippet; demote
  rather than drop (per the iron rule).
* **Fenced code block with NO language tag** → CODE_FENCE_NEUTRAL.
  The fence might be an example to copy-paste; demote.

Iron rule: failure to parse markdown structure returns ``"unknown"``.
"""

from __future__ import annotations

import re
from typing import Final, Literal

ContextVerdict = Literal["safe_doc", "code_fence_neutral", "unknown"]

_EXECUTABLE_LANGS: Final[frozenset[str]] = frozenset(
    {
        "bash", "sh", "shell", "zsh", "console", "terminal", "tty",
        "bat", "cmd", "batch",
        "powershell", "pwsh", "ps1",
        "fish", "csh", "ksh", "dash",
    }
)

_DATA_LANGS: Final[frozenset[str]] = frozenset(
    {
        "json", "jsonc", "yaml", "yml", "toml", "ini", "cfg", "conf",
        "env", "dotenv",
        "xml", "csv", "tsv",
        "html", "htm", "css", "scss", "sass", "less",
        "txt", "text", "plaintext",
        "markdown", "md",
        "diff", "patch",
    }
)

_FENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<fence>```+|~~~+)\s*(?P<lang>[A-Za-z0-9_+-]*)\s*$"
)


def _build_fence_map(source: str) -> list[tuple[int, int, str] | None]:
    """Return per-line list. Each entry is either:

    * ``None`` — the line is NOT inside any fenced block (it's prose,
      heading, list, table, etc.).
    * ``(start_line, end_line, lang)`` — the line is inside the fenced
      block bounded by these 1-based line numbers, with the language
      tag (or empty string if no tag).

    Fences are normalized: matching opener / closer must use the same
    delimiter and length. The fence-open line and fence-close line
    themselves are marked as ``None`` (they're not content).
    """
    lines = source.splitlines()
    result: list[tuple[int, int, str] | None] = [None] * len(lines)

    i = 0
    while i < len(lines):
        m = _FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fence = m.group("fence")
        lang = (m.group("lang") or "").lower()
        # Find matching closer.
        j = i + 1
        while j < len(lines):
            close = _FENCE_RE.match(lines[j])
            if close and close.group("fence") == fence and not (close.group("lang") or ""):
                break
            j += 1
        if j >= len(lines):
            # Unterminated fence — bail; treat the rest as prose so
            # we don't mark hundreds of innocent lines as inside-fence.
            i += 1
            continue
        # Lines (i+1 .. j-1) are inside the fence content. 1-based.
        for k in range(i + 1, j):
            result[k] = (i + 1 + 1, j - 1 + 1, lang)
        i = j + 1

    return result


def _line_has_only_inline_code(line: str) -> bool:
    """True iff stripping inline-code spans leaves only whitespace.

    Example lines that should be SAFE_DOC because they're literally
    nothing but a backtick span:
      ``\\`subprocess.run\\```` (a markdown table cell or a label)
    """
    stripped_text = re.sub(r"`[^`\n]+`", "", line).strip()
    return bool(line.strip()) and not stripped_text


def _line_text_outside_inline_code(line: str) -> str:
    """Return the line content with ``\\`…\\``` spans removed."""
    return re.sub(r"`[^`\n]+`", "", line)


def _match_falls_inside_inline_code(line: str, match: str) -> bool:
    """True iff ``match`` only appears within ``\\`…\\``` spans on this line.

    Catches the very common case where a README mentions
    ``re-run \\`/janitor-arm\\``` — the regex matched the ``/janitor-arm``
    text, but it's literally inside backticks in markdown.
    """
    if not match:
        return False
    # Find every backtick span on this line.
    span_re = re.compile(r"`([^`\n]+)`")
    inside_any = False
    inside_all = True
    for outer_match in re.finditer(re.escape(match), line):
        m_start, m_end = outer_match.span()
        in_span = False
        for span in span_re.finditer(line):
            s_start, s_end = span.span()
            if s_start <= m_start and m_end <= s_end:
                in_span = True
                break
        if in_span:
            inside_any = True
        else:
            inside_all = False
            break
    return inside_any and inside_all


def classify(
    file_path: str,
    source: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> ContextVerdict:
    """Classify a SkillAudit match in a markdown file.

    See module docstring for the per-context verdict matrix.
    """
    lines = source.splitlines()
    if not (0 <= line_idx < len(lines)):
        return "unknown"

    fence_map = _build_fence_map(source)
    line = lines[line_idx]
    fence_state = fence_map[line_idx]

    if fence_state is None:
        # Outside any fence — prose, list, heading, table.
        # Inline-code spans are also documentation.
        if _match_falls_inside_inline_code(line, match):
            return "safe_doc"
        if _line_has_only_inline_code(line):
            return "safe_doc"

        # The match is plain prose text outside any code span. For the
        # execution-class rules (CMD_INJECTION, SHELL_EXEC,
        # REVERSE_SHELL, PRIVILEGE_ESC, OBFUSCATION,
        # INTENT_DESTRUCTIVE_INTENT, INTENT_EXPLICIT_EXFILTRATION,
        # TIME_BOMB, etc.) prose is documentation, suppress. For all
        # rule ids treat prose as documentation — the matcher in
        # markdown is fundamentally text-on-text, not executable.
        return "safe_doc"

    # Inside a fenced block.
    _, _, lang = fence_state
    if lang in _DATA_LANGS:
        return "safe_doc"
    if lang in _EXECUTABLE_LANGS:
        # Match is inside a shell-fence. We can't (here) reach into
        # the shell-context classifier without recursive plumbing, so
        # return "unknown" — the existing heuristic chain handles
        # shell fences via _is_code_in_fenced_block + the bash-uplift
        # already in skillaudit.
        return "unknown"
    # Other languages (python, js, etc.) or no language → neutral.
    # CODE_FENCE_NEUTRAL maps to "demote" in the caller, so the
    # finding stays visible at NIT level for agent triage.
    return "code_fence_neutral"
