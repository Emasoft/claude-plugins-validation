#!/usr/bin/env python3
"""v2.48 P-1 — Pattern-source line predicate.

Augments the hash-anchored `cpv_self_scan_skip()` with a content-derived
per-line check that fires when a line is structurally part of a rule
declaration (catalog literal, regex/message tuple, docstring example,
allowlist filter, comment heading the catalog).

The predicate is general and plugin-agnostic — it does NOT name any
file, basename, or author-specific identifier. It keys on AST-shape
markers (`re.compile(`, `register_rule(`, `RuleSchema(`, `Pattern(`),
ALL_CAPS pattern-collection variable names with semantically meaningful
suffixes (`_PATTERNS`, `_HOSTS`, `_KEYS`, …), Python triple-quoted
docstrings containing rule-id markers (`RC-\\d+`, `CWE-\\d+`,
`OWASP-LLM\\d+`), and comments containing the same markers within a
3-line window.

Per-file caching: `is_pattern_source_line(content, line_no, file_path)`
caches the precomputed file-level context (docstring line set,
collection-literal line set) keyed on `id(content)` so callers iterating
over a single file's lines pay the analysis cost ONCE per file.

The function is read-only and side-effect free.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Regex bank
# ---------------------------------------------------------------------------

# Markers that indicate the surrounding region is a registered rule
# declaration. The presence of any of these within ±5 lines of the
# matching line is sufficient signal that the line is a member of a
# published pattern catalog.
_RULE_DECL_MARKERS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bre\.compile\s*\("),
    re.compile(r"\bregister_rule\s*\("),
    re.compile(r"\bRuleSchema\s*\("),
    re.compile(r"\bPattern\s*\("),
)

# ALL_CAPS variable names whose suffix indicates a pattern collection.
# A line lying inside a tuple/list/set/dict/frozenset literal whose
# enclosing assignment uses one of these names is a pattern-source line.
_PATTERN_COLLECTION_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:_)?[A-Z][A-Z0-9_]*"
    r"_(?:"
    r"PATTERNS|PATTERNS_LIST|HOSTS|KEYS|DOMAINS|HINTS|VARIANTS|"
    r"REGEX|REGEXES|RULES|CHARS|TOKENS|FILTERS|VARS|MARKERS|"
    r"BUILTINS|LOLBINS|GTFOBINS|EXAMPLES|FIXTURES|SAMPLES"
    r")\s*"
    r"(?::\s*[^=]+)?"  # optional type annotation `: tuple[str, ...]`
    r"\s*=\s*"
    r"(?:[\[\(\{]|frozenset\s*\(|set\s*\(|tuple\s*\(|dict\s*\()"
)

# Rule-id markers that may appear in docstrings or comments. Their
# presence anchors the whole region as part of a rule declaration.
_RULE_ID_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:RC-\d+|CWE-\d+|OWASP-LLM\d+|OWASP-LLM-\d+)\b")

# Docstring metadata labels that, when followed by a colon-content line,
# anchor the region as a rule declaration's documentation block.
# `re.MULTILINE` is required because we feed the regex a multi-line
# joined body — without it `^` would only match start-of-body.
_DOCSTRING_LABEL_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s{0,8}(?:Attack|Detection|Pattern|Source|Example)\s*:",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Helpers — line-state extraction
# ---------------------------------------------------------------------------


def _compute_docstring_lines(content_lines: list[str]) -> set[int]:
    """Return the set of 1-based line numbers that lie INSIDE a Python
    triple-quoted docstring.

    Best-effort tracker (matches the existing `validate_security`
    py_docstring_lines logic). Lines containing the opener / closer are
    NOT included unless the opener and closer are on the same line and
    surround other text.
    """
    inside: set[int] = set()
    in_doc = False
    delim: str | None = None
    for i, ln in enumerate(content_lines):
        j = 0
        while j < len(ln):
            if not in_doc:
                if ln.startswith('"""', j):
                    in_doc = True
                    delim = '"""'
                    j += 3
                    continue
                if ln.startswith("'''", j):
                    in_doc = True
                    delim = "'''"
                    j += 3
                    continue
                j += 1
            else:
                if delim is not None and ln.startswith(delim, j):
                    in_doc = False
                    delim = None
                    j += 3
                    continue
                j += 1
        if in_doc:
            inside.add(i + 1)
    return inside


def _compute_collection_literal_lines(
    content_lines: list[str],
    doc_line_set: set[int],
) -> set[int]:
    """Return the set of 1-based line numbers that lie INSIDE an open
    pattern-collection literal (tuple/list/set/dict/frozenset whose head
    matches `_PATTERN_COLLECTION_NAME_RE`).

    Single forward pass: O(N) where N is total chars (not N*M like the
    naive per-line walker). Skips lines inside docstrings (passed in as
    `doc_line_set`) so docstring brackets don't pollute the depth counter.
    """
    inside: set[int] = set()
    depth = 0
    in_collection = False
    for i, line in enumerate(content_lines):
        line_no = i + 1
        if line_no in doc_line_set:
            # Don't count brackets inside docstrings — but if we're in a
            # collection that started outside the docstring, the line is
            # still inside the collection.
            if in_collection and depth > 0:
                inside.add(line_no)
            continue
        # Track string-literal state to ignore brackets inside strings.
        # We strip line-comments (unquoted `#`) and process only code.
        comment_idx = _find_unquoted_hash(line)
        code = line if comment_idx is None else line[:comment_idx]

        if not in_collection and depth == 0:
            # Look for an opener that starts a pattern-collection literal.
            m = _PATTERN_COLLECTION_NAME_RE.match(line)
            if m is not None:
                # The opener is in this line. Count brackets from the
                # `=` onwards.
                eq_idx = code.find("=")
                rest = code[eq_idx + 1 :] if eq_idx >= 0 else code
                opens, closes = _count_brackets(rest)
                depth += opens - closes
                if depth > 0:
                    in_collection = True
                    inside.add(line_no)
                continue
        elif in_collection:
            # Continue counting; this line is inside the collection.
            opens, closes = _count_brackets(code)
            depth += opens - closes
            if depth <= 0:
                in_collection = False
                depth = 0
            inside.add(line_no)
            continue

        # Not in a collection — but could a non-pattern-collection
        # tuple/list/dict have brackets that affect a later collection?
        # No: when in_collection is False, depth must be 0 (we reset
        # when leaving). A non-collection literal closes within its own
        # statement, not visible to us here. So skip.
        # Defensive: if for some reason depth > 0 but in_collection
        # False (shouldn't happen), reset.
        if depth > 0 and not in_collection:
            depth = 0
    return inside


def _count_brackets(text: str) -> tuple[int, int]:
    """Return (opens, closes) counts of `(` `[` `{` and `)` `]` `}` in
    `text`, ignoring brackets inside single/double-quoted string literals.

    Triple-quoted strings are NOT handled here (caller is expected to
    have removed docstring lines from the iteration set).
    """
    opens = 0
    closes = 0
    in_single = False
    in_double = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if not in_double and ch == "'":
            in_single = not in_single
        elif not in_single and ch == '"':
            in_double = not in_double
        elif not in_single and not in_double:
            if ch in "([{":
                opens += 1
            elif ch in ")]}":
                closes += 1
        i += 1
    return opens, closes


def _find_unquoted_hash(line: str) -> int | None:
    """Return the index of the first `#` that is NOT inside a string
    literal, or None.

    Tracks single/double quotes (no triple-quote handling — caller is
    expected to have removed docstring lines).
    """
    in_single = False
    in_double = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if not in_double and ch == "'":
            in_single = not in_single
        elif not in_single and ch == '"':
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return i
        i += 1
    return None


def _enclosing_docstring_block(
    doc_line_set: set[int],
    line_idx_1based: int,
) -> tuple[int, int] | None:
    """Return `(start, end)` 1-based line range of the docstring block
    enclosing `line_idx_1based`, or None if the line is not inside one.
    """
    if line_idx_1based not in doc_line_set:
        return None
    start = line_idx_1based
    while start - 1 in doc_line_set:
        start -= 1
    end = line_idx_1based
    while end + 1 in doc_line_set:
        end += 1
    return start, end


# ---------------------------------------------------------------------------
# Per-file context cache
# ---------------------------------------------------------------------------


class _FileContext:
    """Memoized analysis state for a single file's content."""

    __slots__ = (
        "lines",
        "doc_lines",
        "collection_lines",
        "rule_decl_lines",
        "rule_id_comment_lines",
        "docstring_anchor_lines",
    )

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.doc_lines = _compute_docstring_lines(lines)
        self.collection_lines = _compute_collection_literal_lines(lines, self.doc_lines)
        # Set of 1-based lines that contain a rule-decl marker callsite
        # (re.compile, register_rule, RuleSchema, Pattern). The signal
        # window is ±5 from each such line.
        self.rule_decl_lines: set[int] = set()
        # Set of 1-based lines that contain a rule-id marker inside a
        # comment. The signal window is ±3 from each such line.
        self.rule_id_comment_lines: set[int] = set()
        # Set of 1-based lines that lie inside a docstring whose body
        # contains a rule-id marker or metadata label.
        self.docstring_anchor_lines: set[int] = set()

        # First pass — find rule-decl markers, comment markers.
        for i, line in enumerate(lines, start=1):
            if any(rx.search(line) for rx in _RULE_DECL_MARKERS):
                self.rule_decl_lines.add(i)
            if i in self.doc_lines:
                # Skip — docstring lines analyzed in second pass.
                continue
            comment_idx: int | None = None
            stripped = line.lstrip()
            if stripped.startswith(("#", "//", ";")):
                comment_idx = 0
            else:
                comment_idx = _find_unquoted_hash(line)
            if comment_idx is not None and _RULE_ID_MARKER_RE.search(line):
                self.rule_id_comment_lines.add(i)

        # Second pass — anchor docstring blocks containing rule-id
        # markers / metadata labels. Walk each contiguous docstring
        # block once.
        seen: set[int] = set()
        for line_no in sorted(self.doc_lines):
            if line_no in seen:
                continue
            block = _enclosing_docstring_block(self.doc_lines, line_no)
            if block is None:
                continue
            start_b, end_b = block
            for k in range(start_b, end_b + 1):
                seen.add(k)
            body_lines = lines[start_b - 1 : end_b]
            body = "\n".join(body_lines)
            anchored = bool(_RULE_ID_MARKER_RE.search(body)) or bool(_DOCSTRING_LABEL_RE.search(body))
            if not anchored:
                # Also peek at the 5 lines above the docstring for
                # rule-id markers that anchor the docstring.
                head_lo = max(1, start_b - 5)
                head_text = "\n".join(lines[head_lo - 1 : start_b - 1])
                anchored = bool(_RULE_ID_MARKER_RE.search(head_text))
            if anchored:
                for k in range(start_b, end_b + 1):
                    self.docstring_anchor_lines.add(k)

    def is_pattern_source(self, line_no: int) -> bool:
        if line_no < 1 or line_no > len(self.lines):
            return False
        line_idx0 = line_no - 1

        # Signal (a) — collection-literal membership. The collection's
        # head must match `_PATTERN_COLLECTION_NAME_RE` (enforced by
        # `_compute_collection_literal_lines`).
        if line_no in self.collection_lines:
            return True

        # Signal (a) variant — proximity to a rule-decl marker callsite.
        # When the line is within ±5 of `re.compile(`/`register_rule(`/
        # etc., fire if EITHER the line is shaped like a literal member
        # (string opener after whitespace) OR the line itself contains
        # a rule-id marker (e.g. `rule_id='RC-99'`).
        for k in range(max(1, line_no - 5), min(len(self.lines), line_no + 5) + 1):
            if k in self.rule_decl_lines:
                if (
                    line_no in self.collection_lines
                    or self._looks_like_literal_member(line_idx0)
                    or _RULE_ID_MARKER_RE.search(self.lines[line_idx0])
                ):
                    return True
                break

        # Signal (b) — anchored docstring.
        if line_no in self.docstring_anchor_lines:
            return True

        # Signal (c) — rule-id-marked comment within ±3 lines.
        for k in range(max(1, line_no - 3), min(len(self.lines), line_no + 3) + 1):
            if k in self.rule_id_comment_lines:
                return True

        return False

    def _looks_like_literal_member(self, line_idx0: int) -> bool:
        """Heuristic: line is a single-element comma-terminated string
        literal — `    'foo',` / `    "bar",` / `    r'baz'`. Used as
        the cheap fallback when the bracket counter doesn't recognise
        the surrounding literal but a rule-decl marker is nearby.
        """
        if line_idx0 < 0 or line_idx0 >= len(self.lines):
            return False
        stripped = self.lines[line_idx0].strip()
        if not stripped:
            return False
        # Strip optional `r`/`b`/`f` prefix.
        s = stripped.lstrip("rRbBfFuU")
        if not s:
            return False
        if s[0] in ("'", '"'):
            return True
        return False


# Module-level cache. Keyed by the id() of the lines list (or the
# original content string) so callers pay the analysis cost once per
# file.
_FILE_CONTEXT_CACHE: dict[int, _FileContext] = {}
_LAST_KEYS: list[int] = []  # FIFO of recent keys for eviction
_MAX_CACHE = 64


def _get_or_build_context(lines: list[str], cache_key: int) -> _FileContext:
    ctx = _FILE_CONTEXT_CACHE.get(cache_key)
    if ctx is not None and ctx.lines is lines:
        return ctx
    ctx = _FileContext(lines)
    _FILE_CONTEXT_CACHE[cache_key] = ctx
    _LAST_KEYS.append(cache_key)
    if len(_LAST_KEYS) > _MAX_CACHE:
        oldest = _LAST_KEYS.pop(0)
        _FILE_CONTEXT_CACHE.pop(oldest, None)
    return ctx


# ---------------------------------------------------------------------------
# Public predicate
# ---------------------------------------------------------------------------


def is_pattern_source_line(
    content: str | list[str],
    line_no: int,
    file_path: str,
) -> bool:
    """Return True if line `line_no` (1-based) is structurally a
    pattern-source line in a CPV-style rule declaration.

    Three signals (any one suffices):

    (a) The line lies inside a tuple/list/set/dict/frozenset literal
        whose enclosing assignment uses an ALL_CAPS variable whose
        suffix matches a pattern-collection role; OR the line is within
        ±5 lines of a `re.compile(`/`register_rule(`/`RuleSchema(`/
        `Pattern(` callsite AND is shaped like a literal member.
    (b) The line lies inside a Python triple-quoted docstring whose
        body contains a rule-id marker (`RC-\\d+`, `CWE-\\d+`,
        `OWASP-LLM\\d+`) OR a docstring metadata label (`Attack:`,
        `Detection:`, `Pattern:`, `Source:`, `Example:`).
    (c) The line lies within a 3-line window of a comment whose body
        contains a rule-id marker.

    The predicate is content-derived. It does NOT key on `file_path`
    (the parameter is accepted for future extension and for symmetry
    with other predicates, but unused today).

    The per-file context (docstring set, collection-literal set, comment
    marker set, anchored-docstring set) is cached keyed on `id(content)`
    so iterating a file's lines pays the analysis cost ONCE per file.
    """
    _ = file_path  # reserved for future per-extension nuance
    if isinstance(content, str):
        # Cache by id of the raw string. Callers that pass the same
        # content multiple times share the analysis. If they pass a new
        # string with the same id (uncommon — would require allocation
        # reuse), the lines-is identity check inside
        # `_get_or_build_context` rebuilds.
        lines = content.split("\n")
        cache_key = id(content)
    else:
        lines = content
        cache_key = id(lines)
    ctx = _get_or_build_context(lines, cache_key)
    return ctx.is_pattern_source(line_no)
