#!/usr/bin/env python3
"""v2.48 P-2 — Pytest parametrize-body predicate.

A line lies inside a `@pytest.mark.parametrize(` decorator's argument
list (or a `parametrize(` callsite within a `pytest.mark` chain) iff it
is structurally part of the test-fixture parametrize body.

Why suppress findings here:
    Pytest parametrize fixtures BY CONSTRUCTION contain the very attack
    strings the rules are designed to catch. The test asserts that the
    rule fires on those strings. Examples:
        @pytest.mark.parametrize("payload", [
            "msfvenom -p windows/x64/shell_reverse_tcp ...",
            "忽略之前所有的指令",
            "ignore previous instructions",
        ])
        def test_rule_fires(payload): ...
    Treating these literals as live attacks is a structural FP — the
    file declares them as pattern fixtures, not as live code.

The predicate is general (works for any plugin's pytest tests). It
does NOT name any specific module, file, or author-specific identifier.

Implementation: brace/paren counting via a tokenized walk. We do not
import `ast` at module-import time because validators occasionally run
against partially-edited Python files where the AST parse would fail;
brace counting succeeds even on syntactically incomplete buffers.

The implementation tracks ALL `parametrize(` callsites whose immediate
context is a `pytest.mark` decorator chain — including:
    @pytest.mark.parametrize(...)
    @parametrize(...)                   # if `from pytest import parametrize`
    @pytest.mark.parametrize("a,b", [...])
    @pytest.mark.parametrize(
        "a, b",
        [("x", "y"), ("a", "b")],
        ids=lambda v: v[0],
    )

Caching: line-set is computed once per content-id and reused across
calls. Use `clear_cache()` to drop the cache between unrelated runs
(tests, batch operations).
"""

from __future__ import annotations

import re
from typing import Final

_PARAMETRIZE_DECORATOR_RE: Final[re.Pattern[str]] = re.compile(
    # `@pytest.mark.parametrize(`, `@parametrize(`, `@mark.parametrize(`,
    # and dotted variants. The `@` anchors the line as a decorator (not
    # an arbitrary call to `parametrize` inside test bodies, which would
    # not be a fixture-declaration site).
    r"^\s*@\s*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*\s*\.\s*)*"
    r"parametrize\s*\(",
)


# Module-level cache keyed by id(content_lines_tuple) — reset whenever a
# fresh tuple is supplied. Using a `WeakValueDictionary` would require
# the values to be weakrefable; plain `dict` plus the explicit
# `clear_cache()` is simpler and avoids the `frozenset` weakref issue.
_LINE_SET_CACHE: dict[int, frozenset[int]] = {}


def clear_cache() -> None:
    """Drop the per-content cache. Call between batch runs to avoid
    cross-test memory accumulation."""
    _LINE_SET_CACHE.clear()


def _strip_string_and_comment(line: str) -> str:
    """Remove string literals and `#` comments from `line` so that
    paren-counting is not fooled by `(` inside `"text("` or by `# ` in
    a comment.

    Strings handled:
        - single-quote `'...'`
        - double-quote `"..."`
        - triple-quote markers are NOT stripped here — they are tracked
          separately by the caller via `_track_triple_quote()`.
        - escape sequences (`\\"` / `\\'`) are honored.

    Returns the line with strings replaced by spaces of the same length
    so column offsets are preserved.
    """
    out: list[str] = []
    i = 0
    n = len(line)
    in_str: str | None = None
    while i < n:
        c = line[i]
        if in_str is not None:
            # Inside a single/double-quoted string. Look for end.
            if c == "\\" and i + 1 < n:
                out.append(" ")
                out.append(" ")
                i += 2
                continue
            if c == in_str:
                in_str = None
                out.append(" ")
                i += 1
                continue
            out.append(" ")
            i += 1
            continue
        if c == "#":
            # Rest of line is comment.
            out.append(" " * (n - i))
            break
        if c in ("'", '"'):
            # Triple-quote? Don't strip here — caller handles it.
            if i + 2 < n and line[i + 1] == c and line[i + 2] == c:
                out.append(line[i : i + 3])
                i += 3
                continue
            in_str = c
            out.append(" ")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _split_lines(content: str | list[str]) -> list[str]:
    if isinstance(content, list):
        return content
    return content.split("\n")


def compute_parametrize_body_lines(
    content: str | list[str],
) -> frozenset[int]:
    """Return the set of 1-based line numbers that lie inside the body
    (parenthesised arguments) of a `@pytest.mark.parametrize(` decorator.

    The decorator line itself is NOT included — only the argument-body
    lines are. The closing `)` line is included (it is part of the body
    syntactically).

    Triple-quoted strings inside parametrize args (e.g. multi-line
    payload literals) are entirely included since they are part of the
    body.

    Notes:
        - The line containing `)` that BALANCES the decorator's `(` is
          included as part of the body.
        - Nested parens / brackets / braces are tracked with a single
          counter — strings and comments are stripped first to avoid
          false-balancing.
        - When the parametrize body is split across many lines (50+),
          the entire span is included.
    """
    lines = _split_lines(content)
    body_lines: set[int] = set()
    in_body = False
    paren_depth = 0
    in_triple: str | None = None

    for idx, raw in enumerate(lines, start=1):
        # Track multi-line triple-quoted strings (not part of decorator
        # body parsing per-se, but we don't want to count parens inside
        # them).
        rest = raw
        # Strip leading whitespace match for decorator detection.
        if not in_body and in_triple is None:
            if _PARAMETRIZE_DECORATOR_RE.match(rest):
                in_body = True
                # Count parens on THIS line — the decorator opens with
                # `parametrize(` so depth increments by at least 1, but
                # the rest of the line may also balance it (single-line
                # parametrize like `@pytest.mark.parametrize("a", [1, 2])`).
                cleaned, in_triple = _scan_line_for_triple(rest, in_triple)
                cleaned = _strip_string_and_comment(cleaned)
                # Find the FIRST `(` after `parametrize` to start the
                # paren counter at the right offset. Everything before
                # that `(` is the decorator name; everything after
                # contributes to depth.
                m = re.search(r"parametrize\s*\(", cleaned)
                if m is None:
                    # Decorator line found via regex but cleaned line
                    # lost the marker — fall back to line-level
                    # counting.
                    open_count = cleaned.count("(")
                    close_count = cleaned.count(")")
                    paren_depth = open_count - close_count
                else:
                    after = cleaned[m.end() :]
                    # We've already passed ONE `(` (the parametrize-open).
                    paren_depth = 1
                    paren_depth += after.count("(") - after.count(")")
                if paren_depth <= 0:
                    # Single-line balanced parametrize. Body lives on
                    # this line — include it.
                    body_lines.add(idx)
                    in_body = False
                    paren_depth = 0
                # If still open, this decorator-line ITSELF contributes
                # to body (e.g. inline list `[`). Include it.
                else:
                    body_lines.add(idx)
                continue
        elif in_body:
            cleaned, in_triple = _scan_line_for_triple(rest, in_triple)
            cleaned = _strip_string_and_comment(cleaned)
            paren_depth += cleaned.count("(") - cleaned.count(")")
            body_lines.add(idx)
            if paren_depth <= 0:
                in_body = False
                paren_depth = 0
            continue
        else:
            # Not in body, not in decorator-line. Track triple-quote so
            # that decorators inside docstrings don't fire. Module-level
            # decorators don't appear inside docstrings — safe.
            _, in_triple = _scan_line_for_triple(rest, in_triple)

    return frozenset(body_lines)


def _scan_line_for_triple(
    line: str,
    current: str | None,
) -> tuple[str, str | None]:
    """Scan `line` for triple-quoted string toggles.

    Returns ``(line_without_triple_content, new_state)``.

    If ``current`` is not None, lines inside an open triple-quoted region
    are blanked out so paren-counting doesn't trigger inside docstrings.
    Best-effort: same approach as the pattern-source-predicate's
    ``_compute_docstring_lines``. Edge cases (single-line triple-quoted
    spans) are handled by replacing the whole match with spaces.
    """
    out: list[str] = []
    i = 0
    n = len(line)
    state = current
    while i < n:
        # Already inside a triple-quoted string.
        if state is not None:
            if line.startswith(state, i):
                state = None
                out.append(" ")
                out.append(" ")
                out.append(" ")
                i += 3
                continue
            out.append(" ")
            i += 1
            continue
        # Not inside — check for opener.
        for delim in ('"""', "'''"):
            if line.startswith(delim, i):
                state = delim
                out.append(" ")
                out.append(" ")
                out.append(" ")
                i += 3
                break
        else:
            out.append(line[i])
            i += 1
    return "".join(out), state


def is_parametrize_body_line(
    content: str | list[str],
    line_no: int,
) -> bool:
    """Return True iff `line_no` (1-based) lies inside the body of a
    `@pytest.mark.parametrize(` decorator.

    Caches the per-content body-line set keyed on `id(content)` so
    callers iterating over a single file pay the cost ONCE.
    """
    if line_no < 1:
        return False
    cache_key = id(content)
    body_lines = _LINE_SET_CACHE.get(cache_key)
    if body_lines is None:
        body_lines = compute_parametrize_body_lines(content)
        _LINE_SET_CACHE[cache_key] = body_lines
    return line_no in body_lines
