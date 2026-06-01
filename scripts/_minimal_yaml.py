"""Minimal YAML parser for Claude plugin/skill frontmatter.

A stdlib-only fallback used when ``pyyaml`` is unavailable. Supports the
narrow subset of YAML actually used in Claude Code skill, agent, and plugin
frontmatter:

- ``key: scalar`` (string / bool / int / null)
- ``key: 'quoted'`` and ``key: "quoted"``
- ``key:`` followed by ``  - item`` lines (block list)
- ``key: [a, b, c]`` (inline list)
- ``key: >`` (folded scalar) and ``key: |`` (literal scalar)
- ``# comment`` lines (skipped)

For anything outside this subset (anchors, references, nested mappings,
multi-document streams, complex flow sequences) the parser raises
:class:`YAMLError` so the caller can prompt the user to install ``pyyaml``.

This is NOT a general-purpose YAML parser. It exists solely so that
``validate_skill.py`` and any other CPV script can be invoked from a host
venv that lacks ``pyyaml`` (issue #14).
"""

from __future__ import annotations

import re
from typing import Any


class YAMLError(Exception):
    """Raised when the input cannot be parsed by the minimal parser.

    Mirrors :class:`yaml.YAMLError` so callers can use a single
    ``except YAMLError`` clause regardless of which parser is loaded.
    """


_BOOL_TRUE = {"true", "True", "TRUE", "yes", "Yes", "YES", "on", "On", "ON"}
_BOOL_FALSE = {"false", "False", "FALSE", "no", "No", "NO", "off", "Off", "OFF"}
_NULL = {"null", "Null", "NULL", "~", ""}
_INT_RE = re.compile(r"^-?\d+$")


def _coerce_scalar(raw: str) -> Any:
    """Convert a bare YAML scalar token to a Python value."""
    s = raw.strip()
    # Quoted strings — preserve as-is, strip outer quotes
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    if s in _NULL:
        return None
    if _INT_RE.match(s):
        return int(s)
    return s


def _strip_comment(value: str) -> str:
    """Strip a trailing ``# comment`` from a scalar token, honoring quotes.

    A YAML comment starts at the first ``#`` that is BOTH outside any quoted
    token AND preceded by whitespace (a ``#`` at column 0 of the value region
    also begins a comment, matching pyyaml — ``key: # c`` yields a null value).
    A ``#`` inside ``'...'`` / ``"..."`` is ordinary text, and a ``#`` glued to
    preceding non-space text (``foo#c``) is NOT a comment.

    This replaces the previous ``re.search(r"\\s+#", value)`` strip, which was
    quote-blind: it ate the ``#`` inside a quoted scalar (``desc: 'a # b'``
    became ``"'a"``) and never ran on block-list items at all (so a ``tags:``
    item with a trailing comment kept the comment text). Both diverged from
    pyyaml; routing every scalar through this one helper is the single source
    of truth that keeps the fallback parser matching pyyaml on the supported
    subset.
    """
    quote: str | None = None
    prev_ws = True  # column 0 counts as preceded-by-whitespace for a leading '#'
    for idx, ch in enumerate(value):
        if quote is not None:
            if ch == quote:
                quote = None
            prev_ws = False
            continue
        if ch in ("'", '"'):
            quote = ch
            prev_ws = False
            continue
        if ch == "#" and prev_ws:
            return value[:idx].rstrip()
        prev_ws = ch in (" ", "\t")
    return value.rstrip()


def _split_inline_items(body: str, raw: str) -> list[str]:
    """Split an inline-list body on top-level commas, honoring quotes.

    A comma inside a ``'...'`` or ``"..."`` quoted token does NOT separate
    items, so ``a, "b,c"`` yields ``['a', '"b,c"']`` (two items) rather than
    naively splitting into three. An unquoted nested-flow character
    (``[ ] { }``) raises :class:`YAMLError` — nested flow sequences are
    outside the supported subset — but the same character inside a quoted
    token is treated as ordinary text.
    """
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in body:
        if quote is not None:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            continue
        if ch in ("[", "]", "{", "}"):
            raise YAMLError(f"nested flow sequences not supported: {raw!r}")
        if ch == ",":
            items.append("".join(current))
            current = []
            continue
        current.append(ch)
    if quote is not None:
        raise YAMLError(f"unterminated quote in inline list: {raw!r}")
    items.append("".join(current))
    return items


def _parse_inline_list(raw: str) -> list[Any]:
    """Parse ``[a, b, c]`` flow-sequence syntax (no nesting).

    Commas inside quoted tokens are preserved, so ``[a, "b,c"]`` parses to
    two items, matching pyyaml on the supported subset.
    """
    inner = raw.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        raise YAMLError(f"expected inline list, got: {raw!r}")
    body = inner[1:-1].strip()
    if not body:
        return []
    return [_coerce_scalar(item) for item in _split_inline_items(body, raw)]


def _split_trailing_blanks(block_lines: list[str]) -> tuple[list[str], int]:
    """Split block-scalar lines into ``(content_lines, trailing_blank_count)``.

    ``content_lines`` runs through the LAST non-empty line (interior blanks are
    kept); ``trailing_blank_count`` is the number of empty lines after it. An
    all-blank block yields ``([], len(block_lines))``. Used by the literal
    (``|``) path so chomping (strip / clip / keep) can be applied to the
    trailing-blank count rather than baked into the joined body — matching how
    the folded (``>``) path already consumes ``_fold_block_lines``'s trailing
    count.
    """
    last_content = -1
    for idx, ln in enumerate(block_lines):
        if ln != "":
            last_content = idx
    if last_content == -1:
        return [], len(block_lines)
    return block_lines[: last_content + 1], len(block_lines) - (last_content + 1)


def _fold_block_lines(block_lines: list[str]) -> tuple[str, int]:
    """Fold a ``>`` (folded) block scalar's lines, YAML-1.2 semantics.

    Consecutive non-empty lines are joined with a single space, but a run of
    ``k`` blank lines between content (or at the start) becomes ``k`` literal
    newlines rather than being dropped. The previous implementation discarded
    blank lines entirely (``" ".join(... if s != "")``), which ran the words on
    either side of a blank line together — diverging from ``pyyaml`` (verified:
    ``>`` with a blank line yields ``"a b\\nc"`` in pyyaml, not ``"a b c"``).

    Returns ``(body, trailing_blank_count)`` *before* chomping. The caller
    applies strip / clip / keep chomping using ``trailing_blank_count`` so the
    result matches pyyaml across every blank-line / chomp combination.
    """
    parts: list[str] = []
    prev_content = False
    pending = 0  # consecutive blank lines since the last content line (or start)
    for ln in block_lines:
        if ln == "":
            pending += 1
            continue
        if not prev_content:
            # Leading blank lines become that many newlines before the content.
            if pending:
                parts.append("\n" * pending)
        else:
            # A run of blanks between content lines folds to that many
            # newlines; zero blanks fold to a single joining space.
            parts.append("\n" * pending if pending else " ")
        parts.append(ln)
        prev_content = True
        pending = 0
    return "".join(parts), pending


def safe_load(text: str) -> dict[str, Any] | None:
    """Parse a YAML frontmatter document into a Python ``dict``.

    Returns ``None`` for an empty / whitespace-only document, matching
    :func:`yaml.safe_load`. Raises :class:`YAMLError` on any input the
    minimal parser cannot handle.
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    result: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip blank lines and comments
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        # Document markers — we accept and skip a leading ``---`` because callers
        # may hand us frontmatter with markers stripped or kept.
        if stripped == "---" or stripped == "...":
            i += 1
            continue

        # Top-level entries must have zero indent and a ``key:`` form
        if line.startswith((" ", "\t")):
            raise YAMLError(f"unexpected indented line at top level: {line!r}")

        if ":" not in stripped:
            raise YAMLError(f"expected ``key: value`` form, got: {line!r}")

        key, sep, rest = stripped.partition(":")
        if not sep:
            raise YAMLError(f"missing ``:`` separator: {line!r}")
        key = key.strip()
        rest = rest.lstrip()
        # Strip a trailing ``# comment`` from the inline value, honoring quotes
        # (a ``#`` inside ``'...'``/``"..."`` is data, not a comment).
        rest = _strip_comment(rest)

        # Block scalar: ``key: >`` (folded) or ``key: |`` (literal)
        if rest in (">", "|", ">-", "|-", ">+", "|+"):
            folded = rest.startswith(">")
            chomp_strip = rest.endswith("-")
            chomp_keep = rest.endswith("+")
            block_lines: list[str] = []
            i += 1
            block_indent: int | None = None
            while i < len(lines):
                blk = lines[i]
                if not blk.strip():
                    block_lines.append("")
                    i += 1
                    continue
                cur_indent = len(blk) - len(blk.lstrip(" "))
                if cur_indent == 0:
                    break
                if block_indent is None:
                    block_indent = cur_indent
                if cur_indent < block_indent:
                    break
                block_lines.append(blk[block_indent:])
                i += 1
            if folded:
                # Folded (``>``): blank lines fold to newlines (NOT dropped).
                # Chomping needs the trailing-blank count, so handle it here
                # rather than via the shared endswith("\n") logic below.
                joined, trailing_blanks = _fold_block_lines(block_lines)
                if chomp_strip:
                    joined = joined.rstrip("\n")
                elif chomp_keep:
                    # Keep: trailing blanks become newlines; a non-empty body
                    # also gets the block's own final line break (+1).
                    joined = ("\n" * trailing_blanks) if not joined else (joined + "\n" * (trailing_blanks + 1))
                else:  # clip — single trailing newline (YAML 1.2 default)
                    if joined and not joined.endswith("\n"):
                        joined += "\n"
            else:
                # Literal (``|``): preserve every interior line break verbatim,
                # but handle TRAILING blank lines via the chomp indicator — not
                # by joining them into the body. The previous ``"\n".join(...)``
                # folded trailing blanks into the body string, which conflated
                # content newlines with trailing-blank newlines and diverged from
                # pyyaml for ``|`` (clip must collapse N trailing blanks to one
                # ``\n``) and ``|+`` (keep emits the body's own final ``\n`` plus
                # one ``\n`` per trailing blank). Splitting first matches pyyaml
                # across every blank-line / chomp combination (verified).
                content_lines, trailing_blanks = _split_trailing_blanks(block_lines)
                body = "\n".join(content_lines)
                if chomp_strip:  # strip: drop ALL trailing newlines
                    joined = body
                elif chomp_keep:  # keep: body's final ``\n`` + one per trailing blank
                    joined = ("\n" * trailing_blanks) if not body else (body + "\n" * (trailing_blanks + 1))
                else:  # clip — exactly one trailing newline (YAML 1.2 default)
                    joined = (body + "\n") if body else ""
            result[key] = joined
            continue

        # Block list: ``key:`` followed by indented ``- item`` lines.
        # When nothing indented follows, the value is a null scalar — matching
        # pyyaml's behavior for bare ``key:`` lines.
        if rest == "":
            items: list[Any] = []
            j = i + 1
            saw_list = False
            while j < len(lines):
                lst = lines[j]
                if not lst.strip():
                    j += 1
                    continue
                if not lst.startswith((" ", "\t")):
                    break
                ls = lst.strip()
                if not ls.startswith("- "):
                    raise YAMLError(f"expected ``- item`` continuation for {key!r}, got: {lst!r}")
                # A block-list item carries the same trailing-comment semantics
                # as an inline scalar (``- a  # note`` -> ``a``); strip it before
                # coercion so the result matches pyyaml.
                items.append(_coerce_scalar(_strip_comment(ls[2:])))
                saw_list = True
                j += 1
            result[key] = items if saw_list else None
            i = j
            continue

        # Inline list: ``key: [a, b, c]``
        if rest.startswith("["):
            result[key] = _parse_inline_list(rest)
            i += 1
            continue

        # Plain scalar value
        result[key] = _coerce_scalar(rest)
        i += 1

    return result
