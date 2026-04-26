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


def _parse_inline_list(raw: str) -> list[Any]:
    """Parse ``[a, b, c]`` flow-sequence syntax (no nesting)."""
    inner = raw.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        raise YAMLError(f"expected inline list, got: {raw!r}")
    body = inner[1:-1].strip()
    if not body:
        return []
    if "[" in body or "]" in body or "{" in body or "}" in body:
        raise YAMLError(f"nested flow sequences not supported: {raw!r}")
    return [_coerce_scalar(item) for item in body.split(",")]


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
        # Strip trailing comment from inline value (only when the ``#`` is
        # preceded by whitespace, to avoid eating ``#`` inside strings).
        cmt = re.search(r"\s+#", rest)
        if cmt:
            rest = rest[: cmt.start()].rstrip()

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
            joined = ("\n".join(block_lines)) if not folded else (" ".join(s for s in block_lines if s != ""))
            if chomp_strip:
                joined = joined.rstrip("\n")
            elif chomp_keep:
                if not joined.endswith("\n"):
                    joined += "\n"
            else:  # default: clip — single trailing newline (YAML 1.2 default)
                if joined and not joined.endswith("\n"):
                    joined += "\n"
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
                items.append(_coerce_scalar(ls[2:]))
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
