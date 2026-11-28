#!/usr/bin/env python3
"""Static check that a Claude Code mod's hooks module exports `register` by name (TRDD-775XK7HE).

Claude Code calls the hooks module's exported `register(on, options)` on load
(mods reference chapter, Files table; types index.d.ts "export function
register(on, options)"), and `claude plugin validate` refuses a module whose
`register` export it cannot see. CPV has no JS/TS parser and must stay offline
and deterministic, so this module is a small LEXICAL scanner over the module
source: it never executes, imports or evaluates the code, and it never opens a
sibling file (native does not follow re-exports either: fixtures C05a, C06a).

Every rejection rule below is ORACLE-DERIVED (2.1.332): observed behaviour of
`claude plugin validate` 2.1.332 on the 137 fixtures of the TRDD-775XK7HE
evidence matrix, with no doc sentence behind it. Only "a hooks module exports
`register`" is doc-derived.

Fail-open contract: whenever the scanner cannot be sure (an unterminated
comment or string, unbalanced brackets, a syntax form outside the proven set)
the verdict is UNDECIDED, which the caller reports as INFO. A MAJOR verdict is
only ever produced by a form the matrix proved native rejects.

Pure functions, no I/O, stdlib only.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

VerdictCode = Literal["OK", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "UNDECIDED"]

TS_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts"})
JSX_SUFFIXES = frozenset({".jsx", ".tsx"})

# Native's own limit (troubleshooting chapter: "code nested too deep to scan:
# more than 2000 scopes"); deeper input is UNDECIDED, never a verdict.
_MAX_DEPTH = 2000

# Spec B.2 rule 5: after these keywords a `/` starts a regex literal.
_REGEX_KEYWORDS = frozenset(
    "return typeof instanceof in of new delete void throw case do else yield await".split()
)
# JS line terminators and the other invisible characters the lexer handles,
# built with chr() so the source never holds a raw U+2028/U+2029 (a raw one
# counts as a line break for some editors and tools).
_LS, _PS = chr(0x2028), chr(0x2029)
_BOM = chr(0xFEFF)
_ZWNJ_ZWJ = chr(0x200C) + chr(0x200D)
_LINE_TERMINATORS = "\n" + _LS + _PS
_OPENERS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = frozenset(_OPENERS.values())
# A declarator ends at a new line starting with one of these (spec B.2).
_BOUNDARY_KEYWORDS = frozenset({"export", "import", "const", "let", "var", "function", "class", "async"})
_TS_BOUNDARY_KEYWORDS = frozenset({"type", "interface", "enum", "declare", "namespace", "abstract"})
# Words that are not a plain identifier reference: an initializer starting
# with one of them is outside the proven R4 set (UNVERIFIED -> UNDECIDED).
_NON_REFERENCE_WORDS = frozenset(
    {
        "new", "await", "typeof", "void", "delete", "this", "super", "true", "false", "undefined",
        "import", "yield", "NaN", "Infinity", "arguments", "function", "class", "async", "null",
    }
)
_RESERVED_LOCALS = frozenset({"default", "this", "super", "null", "true", "false", "import", "export"})
# Proven R4 initializer classes (C08 num, Z01 str, Z02 null, Z03 array,
# X12 object, Z15 class expression, Y17 bare identifier, Z04 call).
_R4_PROVEN_INITS = frozenset({"num", "str", "null", "array", "object", "class", "ident", "call"})
# JSX guard (spec B.2 step 4): a raw `export ... register` on one line.
_RAW_EXPORT_REGISTER = re.compile(r"\bexport\b[^\n;]*\bregister\b")
# Marker prefixed to a string value that held a backslash escape: its decoded
# value is not trusted, so it never compares equal to "register".
_ESCAPED_STRING_MARK = "\x00escaped:"


@dataclass(frozen=True)
class RegisterVerdict:
    """The scanner's answer for one hooks module.

    code: OK, a rejection code R1-R7 (native reason table of TRDD-775XK7HE),
    or UNDECIDED. line: 1-based source line of the offending export when
    known. local: the local name an export list binds (R6, R7). detail: the
    accepted form for OK, the reason for UNDECIDED.
    """

    code: VerdictCode
    line: int | None
    local: str | None
    detail: str


@dataclass(frozen=True)
class Tok:
    """One lexical token. depth is the bracket depth BEFORE the token; nl is
    True when a line terminator separates it from the previous token."""

    kind: str  # ident | num | str | tmpl | regex | punct
    value: str
    line: int
    depth: int
    nl: bool


class _Undecided(Exception):
    """The scanner cannot decide; the message is the INFO detail."""


class _TooDeep(_Undecided):
    """More than 2000 nested scopes: a native ERROR ("code nested too deep to
    scan", fixture p44), unlike every other _Undecided, which is only a limit
    of this scanner. The message equals the plain depth limit, so the
    `register` verdict stays UNDECIDED with an unchanged detail."""

    def __init__(self, message: str, line: int) -> None:
        super().__init__(message)
        self.line = line


@dataclass
class _RegisterExport:
    form: str  # function | const-fn | const-r4 | const-unverified | letvar-r4 | letvar-unverified
    #            | enum | class | spec-local | spec-from | spec-from-str | star-as | unknown
    line: int
    local: str | None = None
    local_is_str: bool = False


class _Lexer:
    """Spec B.2 lexer: drops comments, keeps decoded strings, folds templates
    to one opaque token, tells regex from division by the previous token."""

    def __init__(self, src: str, jsx: bool, max_depth: int = _MAX_DEPTH) -> None:
        self.max_depth = max_depth
        # Open `{` that start a function body or block (see _opens_scope): native
        # counts only these toward its 2000-scope limit, never parens, arrays or
        # object literals (probes d-objlit, e-paren, e-array of TRDD-29QP66H2).
        self.scopes_open = 0
        self.s = src
        self.n = len(src)
        self.i = 0
        self.line = 1
        self.jsx = jsx
        self.idents: Counter[str] = Counter()
        self.match: dict[int, int] = {}
        self.outer_depth = 0

    @staticmethod
    def _opens_scope(toks: list[Tok], stack: list[tuple[str, int, bool]], in_template: bool) -> bool:
        """True when the `{` about to be pushed starts a function body or a
        block, the only braces native counts toward its 2000-scope limit
        (probes d-brace, e-arrowbody, e-while, e-if, e-objfn: register body +
        1999 inner scopes passes, +2000 errors; d-objlit: 2500 nested object
        literals pass). Decided by the previous token, so an object literal
        (`= {`, `: {`, `return {`, `({`) is never counted: an UNDER-count only
        ever fails open, an over-count would be a false finding."""
        prev = toks[-1] if toks else None
        if prev is None:
            return not in_template  # a `{` opening the file is a bare block
        if prev.kind == "punct":
            if prev.value in (")", "=>", ";", "}"):
                return True
            # `{{`: a block inside a block; inside an object literal it is invalid code.
            return prev.value == "{" and bool(stack) and stack[-1][2]
        return prev.kind == "ident" and prev.value in ("else", "try", "finally", "do")

    def run(self, in_template: bool) -> list[Tok]:
        """Lex until EOF (outer level) or the `}` closing a template `${`."""
        s, n = self.s, self.n
        toks: list[Tok] = []
        stack: list[tuple[str, int, bool]] = []
        nl = True

        def emit(kind: str, value: str, line: int) -> None:
            nonlocal nl
            toks.append(Tok(kind, value, line, len(stack), nl))
            nl = False
            if kind == "ident":
                self.idents[value] += 1

        while self.i < n:
            c = s[self.i]
            nxt = s[self.i + 1] if self.i + 1 < n else ""
            if c == "\r":
                if nxt != "\n":
                    self.line += 1
                    nl = True
                self.i += 1
                continue
            if c in _LINE_TERMINATORS:
                self.line += 1
                nl = True
                self.i += 1
                continue
            if c.isspace() or c == _BOM:
                self.i += 1
                continue
            if c == "/" and nxt == "/":
                while self.i < n and s[self.i] not in _LINE_TERMINATORS and s[self.i] != "\r":
                    self.i += 1
                continue
            if c == "/" and nxt == "*":
                end = s.find("*/", self.i + 2)
                if end < 0:
                    raise _Undecided(f"unterminated block comment at line {self.line}")
                body = s[self.i : end]
                breaks = sum(body.count(t) for t in _LINE_TERMINATORS) + body.count("\r") - body.count("\r\n")
                if breaks:
                    self.line += breaks
                    nl = True
                self.i = end + 2
                continue
            if c in "'\"":
                start_line = self.line
                value = self._string(c)
                if value is None:  # JSX text apostrophe (W-tsx): lone quote punct
                    emit("punct", c, start_line)
                    self.i += 1
                else:
                    emit("str", value, start_line)
                continue
            if c == "`":
                start_line = self.line
                self._template()
                emit("tmpl", "`", start_line)
                continue
            if c.isdigit() or (c == "." and nxt.isdigit()):
                j = self.i + 1
                while j < n and (
                    s[j].isalnum() or s[j] in "_." or (s[j] in "+-" and s[j - 1] in "eE" and not s[self.i : j].lower().startswith("0x"))
                ):
                    j += 1
                emit("num", s[self.i : j], self.line)
                self.i = j
                continue
            if c.isalpha() or c in "_$" or (ord(c) > 127 and ("a" + c).isidentifier()):
                j = self.i + 1
                while j < n and (s[j].isalnum() or s[j] in "_$" + _ZWNJ_ZWJ or (ord(s[j]) > 127 and ("a" + s[j]).isidentifier())):
                    j += 1
                if j < n and s[j] == "\\":
                    raise _Undecided(f"identifier with a unicode escape at line {self.line}")
                emit("ident", s[self.i : j], self.line)
                self.i = j
                continue
            if c == "\\":
                raise _Undecided(f"stray backslash at line {self.line}")
            if c == "/":
                if self.jsx and nxt == ">":
                    emit("punct", "/>", self.line)
                    self.i += 2
                    continue
                prev = toks[-1] if toks else None
                if (
                    prev is None
                    or (prev.kind == "punct" and prev.value not in (")", "]"))
                    or (prev.kind == "ident" and prev.value in _REGEX_KEYWORDS)
                ):
                    start_line = self.line
                    self._regex()
                    emit("regex", "/", start_line)
                    continue
            if self.jsx and c == "<" and nxt == "/":
                emit("punct", "</", self.line)
                self.i += 2
                continue
            if in_template and c == "}" and not stack:
                self.i += 1
                return toks
            three = s[self.i : self.i + 3]
            two = s[self.i : self.i + 2]
            if three in ("===", "!==", "..."):
                emit("punct", three, self.line)
                self.i += 3
                continue
            if two in ("=>", "==", "!=", "<=", ">=", "?."):
                emit("punct", two, self.line)
                self.i += 2
                continue
            if c in _OPENERS:
                is_scope = c == "{" and self._opens_scope(toks, stack, in_template)
                if is_scope and self.scopes_open >= _MAX_DEPTH:
                    raise _TooDeep(f"more than {_MAX_DEPTH} nested scopes", self.line)
                if self.outer_depth + len(stack) >= self.max_depth:
                    raise _Undecided(f"more than {_MAX_DEPTH} nested scopes")
                emit("punct", c, self.line)
                stack.append((c, len(toks) - 1, is_scope))
                self.scopes_open += is_scope
            elif c in _CLOSERS:
                if not stack or _OPENERS[stack[-1][0]] != c:
                    raise _Undecided(f"unbalanced '{c}' at line {self.line}")
                emit("punct", c, self.line)
                _, open_index, was_scope = stack.pop()
                self.scopes_open -= was_scope
                if not in_template:
                    self.match[open_index] = len(toks) - 1
            else:
                emit("punct", c, self.line)
            self.i += 1
        if in_template:
            raise _Undecided("unterminated template substitution")
        if stack:
            raise _Undecided(f"unclosed '{stack[-1][0]}' at end of file")
        return toks

    def _string(self, quote: str) -> str | None:
        """Scan a quoted string at self.i. Returns its decoded value, or None
        for a JSX-text lone quote (newline before the close, .jsx/.tsx only)."""
        s, n = self.s, self.n
        start, start_line = self.i, self.line
        j = start + 1
        buf: list[str] = []
        escaped = False
        while j < n:
            ch = s[j]
            if ch == "\\":
                if j + 1 >= n:
                    break
                after = s[j + 1]
                if after == "\r" and s[j + 2 : j + 3] == "\n":
                    self.line += 1
                    j += 3
                    continue
                if after in _LINE_TERMINATORS or after == "\r":
                    self.line += 1
                    j += 2
                    continue
                escaped = True
                buf.append(after)
                j += 2
                continue
            if ch == quote:
                self.i = j + 1
                value = "".join(buf)
                return _ESCAPED_STRING_MARK + value if escaped else value
            if ch in _LINE_TERMINATORS or ch == "\r":
                if self.jsx:
                    self.line = start_line
                    return None
                raise _Undecided(f"unterminated string at line {start_line}")
            buf.append(ch)
            j += 1
        if self.jsx:
            self.line = start_line
            return None
        raise _Undecided(f"unterminated string at line {start_line}")

    def _template(self) -> None:
        """Scan a template literal at self.i, recursing into `${...}`; the
        inner tokens only feed the identifier counter."""
        s, n = self.s, self.n
        start_line = self.line
        j = self.i + 1
        while j < n:
            ch = s[j]
            if ch == "\\":
                if s[j + 1 : j + 2] in ("\n", _LS, _PS):
                    self.line += 1
                j += 2
                continue
            if ch == "`":
                self.i = j + 1
                return
            if ch == "$" and s[j + 1 : j + 2] == "{":
                self.i = j + 2
                self.outer_depth += 1
                self.run(in_template=True)
                self.outer_depth -= 1
                j = self.i
                continue
            if ch in _LINE_TERMINATORS:
                self.line += 1
            j += 1
        raise _Undecided(f"unterminated template literal at line {start_line}")

    def _regex(self) -> None:
        """Scan a regex literal at self.i: honours `\\` and `[...]` classes."""
        s, n = self.s, self.n
        j = self.i + 1
        in_class = False
        while j < n:
            ch = s[j]
            if ch in _LINE_TERMINATORS or ch == "\r":
                break
            if ch == "\\":
                j += 2
                continue
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                j += 1
                while j < n and (s[j].isalnum() or s[j] in "_$"):
                    j += 1
                self.i = j
                return
            j += 1
        raise _Undecided(f"unterminated regular expression at line {self.line}")


class _Parser:
    """Spec B.2 parser over the depth-0 token stream: a declarations table
    and the exports named `register`."""

    def __init__(self, toks: list[Tok], match: dict[int, int], idents: Counter[str], ts: bool) -> None:
        self.t = toks
        self.match = match
        self.idents = idents
        self.ts = ts
        self.decls: dict[str, str] = {}
        self.exports: list[_RegisterExport] = []
        self.star_line: int | None = None
        self.ts_export_assign = False

    # -- token helpers -------------------------------------------------
    def tok(self, j: int) -> Tok | None:
        return self.t[j] if 0 <= j < len(self.t) else None

    def is_p(self, j: int, value: str) -> bool:
        tk = self.tok(j)
        return tk is not None and tk.kind == "punct" and tk.value == value

    def is_id(self, j: int, value: str | None = None) -> bool:
        tk = self.tok(j)
        return tk is not None and tk.kind == "ident" and (value is None or tk.value == value)

    def is_opener(self, j: int) -> bool:
        tk = self.tok(j)
        return tk is not None and tk.kind == "punct" and tk.value in _OPENERS

    def close_of(self, j: int) -> int:
        if j not in self.match:
            raise _Undecided("bracket the scanner could not match")
        return self.match[j]

    def is_decl_end(self, j: int) -> bool:
        tk = self.tok(j)
        if tk is None:
            return True
        if tk.kind == "punct" and tk.value in (",", ";"):
            return True
        if not (tk.nl and tk.kind == "ident"):
            return False
        if tk.value in _BOUNDARY_KEYWORDS:
            return True
        # TS declarations on a new line end the declarator too, else a
        # `type T = Pick<A, B>` after an object literal splits at its comma
        # (rest-in-pid 1.0.4/1.0.5, natively accepted).
        return self.ts and tk.value in _TS_BOUNDARY_KEYWORDS and self.is_id(j + 1)

    def ends(self, j: int, stop: int | None) -> bool:
        return j == stop if stop is not None else self.is_decl_end(j)

    def scan_decl_end(self, j: int) -> int:
        while not self.is_decl_end(j):
            if self.is_opener(j):
                j = self.close_of(j) + 1
                continue
            if self.ts and self.is_p(j, "<") and self.is_id(j - 1):
                # TS type arguments of a call (`new Map<string, X>()`): their
                # commas are not declarator separators. Found on 6 of the 42
                # installed mods (strays, mod-logic, ...), all natively accepted.
                after = self.type_args_end(j)
                if after is not None:
                    j = after
                    continue
            j += 1
        return j

    def type_args_end(self, j: int) -> int | None:
        """Index of the `(` after a balanced `<...>` starting at j, or None
        when the `<` does not open a call type-argument list (a comparison)."""
        angle = 0
        while True:
            tk = self.tok(j)
            if tk is None or self.is_p(j, ";") or (tk.nl and tk.kind == "ident" and tk.value in _BOUNDARY_KEYWORDS):
                return None
            if self.is_opener(j):
                j = self.close_of(j) + 1
                continue
            if self.is_p(j, "<"):
                angle += 1
            elif self.is_p(j, ">"):
                angle -= 1
                if angle == 0:
                    return j + 1 if self.is_p(j + 1, "(") else None
            j += 1

    def declare(self, name: str, kind: str) -> None:
        old = self.decls.get(name)
        self.decls[name] = kind if old is None or old == kind else "unknown"

    def stmt_start(self, j: int) -> bool:
        prev = self.tok(j - 1)
        cur = self.t[j]
        if prev is None:
            return True
        if prev.kind == "punct" and prev.value in (";", "}"):
            return True
        if cur.nl:
            return not (prev.kind == "punct" and prev.value not in (")", "]", "}"))
        return False

    # -- statements ----------------------------------------------------
    def parse(self) -> None:
        j = 0
        while j < len(self.t):
            tk = self.t[j]
            if self.is_opener(j):
                j = self.close_of(j) + 1
                continue
            if tk.kind == "ident":
                prev = self.tok(j - 1)
                after_dot = prev is not None and prev.kind == "punct" and prev.value in (".", "?.")
                if tk.value == "export" and not after_dot:
                    j = self.parse_export(j)
                    continue
                if not after_dot and self.stmt_start(j):
                    v = tk.value
                    if v in ("const", "let", "var") and (self.is_id(j + 1) or self.is_opener(j + 1)):
                        j = self.parse_declarators(j, exported=False)
                        continue
                    if v == "function" or (v == "async" and self.is_id(j + 1, "function")):
                        j = self.parse_function(j, exported=False)
                        continue
                    if v == "class" and self.is_id(j + 1):
                        j = self.parse_class(j, exported=False)
                        continue
                    if v == "import" and not (self.is_p(j + 1, "(") or self.is_p(j + 1, ".")):
                        j = self.parse_import(j)
                        continue
                    if self.ts and v in ("enum", "namespace", "module", "type", "interface", "declare", "abstract"):
                        self.mark_ts_declaration(j)
            j += 1

    def mark_ts_declaration(self, j: int) -> None:
        """A non-exported TS-only declaration binds a name the scanner does
        not classify: an export list aliasing it is UNDECIDED, never R6/R7."""
        k = j + 1
        while self.is_id(k) and self.t[k].value in (
            "const", "let", "var", "function", "class", "abstract", "async", "enum", "namespace", "module", "type",
            "interface",
        ):
            k += 1
        if self.is_id(k) and not self.t[k].nl:
            self.declare(self.t[k].value, "unknown")

    def add_export(self, name: str, form: str, line: int, local: str | None = None, local_is_str: bool = False) -> None:
        if name == "register":
            self.exports.append(_RegisterExport(form, line, local, local_is_str))

    def parse_export(self, j: int) -> int:
        k = j + 1
        tk = self.tok(k)
        line = self.t[j].line
        if tk is None:
            raise _Undecided(f"bare `export` at line {line}")
        if self.is_p(k, "*"):
            if self.is_id(k + 1, "as"):
                name = self.tok(k + 2)
                if name is None or name.kind not in ("ident", "str"):
                    raise _Undecided(f"`export * as` without a name at line {line}")
                # R3 (X13): `export * as register from` is never followed.
                self.add_export(name.value, "star-as", name.line)
                return k + 3
            if self.star_line is None:
                self.star_line = tk.line
            return k + 1
        if self.is_p(k, "{"):
            return self.parse_export_list(k, type_only=False)
        if self.ts and self.is_p(k, "="):
            self.ts_export_assign = True
            return k + 1
        if tk.kind != "ident":
            raise _Undecided(f"export form not recognised at line {line}")
        v = tk.value
        if v == "default":
            # C04, X08, X18: a default export never counts as `register`.
            return k + 1
        if v in ("const", "let", "var"):
            if self.ts and v == "const" and self.is_id(k + 1, "enum"):
                name = self.tok(k + 2)
                if name is None or name.kind != "ident":
                    raise _Undecided(f"`export const enum` without a name at line {line}")
                self.add_export(name.value, "unknown", name.line)
                return k + 3
            return self.parse_declarators(k, exported=True)
        if v == "function" or (v == "async" and self.is_id(k + 1, "function")):
            return self.parse_function(k, exported=True)
        if v == "class":
            return self.parse_class(k, exported=True)
        if self.ts:
            if v == "type":
                if self.is_p(k + 1, "{"):
                    # X09: `export type { register }` exports nothing at runtime.
                    return self.parse_export_list(k + 1, type_only=True)
                # C07: `export type register = ...` (and `export type *`) erase.
                return k + 1
            if v in ("interface", "declare"):
                # X10, X11, Z24: erased by the TS compile native runs first.
                return k + 1
            if v in ("enum", "namespace", "module"):
                name = self.tok(k + 1)
                if name is None or name.kind != "ident":
                    raise _Undecided(f"`export {v}` without a name at line {line}")
                # Z22, Z23: compiled to `export var register;` -> R4.
                self.add_export(name.value, "enum", name.line)
                self.declare(name.value, "unknown")
                return k + 2
            if v == "abstract" and self.is_id(k + 1, "class"):
                # Z25: an abstract class is a class -> R5.
                return self.parse_class(k + 1, exported=True)
            if v == "import":
                name = self.tok(k + 1)
                if name is not None and name.kind == "ident" and self.is_p(k + 2, "="):
                    self.add_export(name.value, "unknown", name.line)
                    self.declare(name.value, "unknown")
                    return k + 2
            if v == "as" and self.is_id(k + 1, "namespace"):
                return k + 2
        raise _Undecided(f"export form not recognised at line {line}")

    def parse_export_list(self, brace: int, type_only: bool) -> int:
        close = self.close_of(brace)
        inner_depth = self.t[brace].depth + 1
        specs: list[list[Tok]] = [[]]
        for tk in self.t[brace + 1 : close]:
            if tk.depth != inner_depth or tk.kind not in ("ident", "str", "punct"):
                raise _Undecided(f"export list not recognised at line {tk.line}")
            if tk.kind == "punct":
                if tk.value != ",":
                    raise _Undecided(f"export list not recognised at line {tk.line}")
                specs.append([])
            else:
                specs[-1].append(tk)
        from_clause = self.is_id(close + 1, "from")
        for spec in specs:
            if not spec:
                continue
            spec_type = False
            if (
                len(spec) >= 2
                and spec[0].kind == "ident"
                and spec[0].value == "type"
                and not (len(spec) == 3 and spec[1].kind == "ident" and spec[1].value == "as")
            ):
                spec_type = True
                spec = spec[1:]
            if len(spec) == 1:
                local, exported = spec[0], spec[0]
            elif len(spec) == 3 and spec[1].kind == "ident" and spec[1].value == "as":
                local, exported = spec[0], spec[2]
            else:
                raise _Undecided(f"export specifier not recognised at line {spec[0].line}")
            if exported.value != "register":
                continue
            if type_only or spec_type:
                if not self.ts:
                    raise _Undecided(f"`type` export specifier in a JavaScript module at line {exported.line}")
                # X09, Z21: a type-only specifier is erased.
                continue
            if from_clause:
                form = "spec-from-str" if local.kind == "str" else "spec-from"
            else:
                form = "spec-local"
            self.add_export("register", form, exported.line, local.value, local.kind == "str")
        return close + 1

    def parse_function(self, k: int, exported: bool) -> int:
        p = k + 1 if self.t[k].value == "function" else k + 2
        if self.is_p(p, "*"):
            p += 1
        name = self.tok(p)
        if name is None or name.kind != "ident":
            raise _Undecided(f"function declaration without a name at line {self.t[k].line}")
        self.declare(name.value, "function")
        if exported:
            self.add_export(name.value, "function", name.line)
        return p + 1

    def parse_class(self, k: int, exported: bool) -> int:
        name = self.tok(k + 1)
        if name is None or name.kind != "ident":
            raise _Undecided(f"class declaration without a name at line {self.t[k].line}")
        self.declare(name.value, "class")
        if exported:
            self.add_export(name.value, "class", name.line)
        return k + 2

    def parse_import(self, k: int) -> int:
        """Bind every local an import statement creates (Y08 -> R6)."""
        p = k + 1
        type_import = (
            self.ts and self.is_id(p, "type") and not (self.is_id(p + 1, "from") or self.is_p(p + 1, ","))
        )
        if type_import:
            p += 1
        if self.is_id(p) and self.is_p(p + 1, "="):
            self.declare(self.t[p].value, "unknown")  # TS `import x = require(...)`
            return p + 2
        kind = "unknown" if type_import else "import"
        while True:
            tk = self.tok(p)
            if tk is None or tk.kind == "str" or self.is_p(p, ";") or self.is_id(p, "from"):
                return p + 1 if tk is not None else p
            if self.is_p(p, "{"):
                close = self.close_of(p)
                spec: list[Tok] = []
                for inner in [*self.t[p + 1 : close], None]:
                    if inner is None or (inner.kind == "punct" and inner.value == ","):
                        if spec:
                            spec_kind = "unknown" if (len(spec) > 1 and spec[0].value == "type") else kind
                            self.declare(spec[-1].value, spec_kind)
                        spec = []
                    elif inner.kind in ("ident", "str"):
                        spec.append(inner)
                p = close + 1
                continue
            if tk.kind == "ident" and tk.value != "as":
                self.declare(tk.value, kind)
            p += 1

    def parse_declarators(self, k: int, exported: bool) -> int:
        keyword = self.t[k].value
        p = k + 1
        while True:
            tk = self.tok(p)
            if tk is None:
                raise _Undecided(f"`{keyword}` without a declarator at line {self.t[k].line}")
            if tk.kind == "ident":
                names = [tk.value]
                simple = True
                name_line = tk.line
                p += 1
            elif self.is_p(p, "{") or self.is_p(p, "["):
                close = self.close_of(p)
                names = [x.value for x in self.t[p + 1 : close] if x.kind == "ident"]
                simple = False
                name_line = tk.line
                p = close + 1
            else:
                raise _Undecided(f"declarator not recognised at line {tk.line}")
            if self.ts and self.is_p(p, "!"):
                p += 1
            if self.is_p(p, ":"):
                if not self.ts:
                    raise _Undecided(f"type annotation in a JavaScript module at line {name_line}")
                # X15, Z27: the annotation is erased; skip to the bare `=`.
                p = self.skip_type(p + 1, "eq", None)
            init: str | None = None
            if self.is_p(p, "="):
                init, after = self.classify(p + 1, None)
                p = self.scan_decl_end(after)
            elif not self.is_decl_end(p):
                raise _Undecided(f"declarator not recognised at line {name_line}")
            self.record_declarator(keyword, names, simple, init, exported, name_line)
            if self.is_p(p, ","):
                p += 1
                continue
            return p

    def record_declarator(
        self, keyword: str, names: list[str], simple: bool, init: str | None, exported: bool, line: int
    ) -> None:
        if not simple:
            for name in names:
                self.declare(name, "unknown")
                if exported:
                    self.add_export(name, "unknown", line)  # destructuring export: UNVERIFIED
            return
        name = names[0]
        if keyword == "const":
            if init == "fn":
                kind = "const-fn"
            elif init is None:
                kind = "unknown"
            else:
                kind = "const-other:" + ("numeric" if init == "num" else init)
            self.declare(name, kind)
            if exported:
                if init == "fn":
                    form = "const-fn"  # C02, Y01, Y02, Z05-Z07, Z14, Z18-Z20, Z26, Z27
                elif init in _R4_PROVEN_INITS:
                    form = "const-r4"  # C08, X12, Y17, Z01-Z04, Z15
                else:
                    form = "const-unverified"
                self.add_export(name, form, line)
            return
        self.declare(name, keyword)
        if exported:
            # X04, Y06, V01, V02, V03, V05, Z08, Z09: `export let|var register`
            # with a function, arrow, numeric or no initializer is R4.
            form = "letvar-r4" if init in (None, "fn", "num") else "letvar-unverified"
            self.add_export(name, form, line)

    # -- initializer classification ------------------------------------
    def skip_type(self, j: int, mode: str, stop: int | None) -> int:
        """Skip a TS type. mode "eq": stop at a bare `=` (annotation);
        "arrow": stop at `=>` (arrow return type); "end": stop at the
        declarator end only (after `as` / `satisfies`)."""
        angle = 0
        while True:
            if stop is not None and j >= stop:
                return j
            tk = self.tok(j)
            if tk is None:
                return j
            if angle == 0:
                if mode == "eq" and self.is_p(j, "="):
                    return j
                if mode == "arrow" and self.is_p(j, "=>"):
                    return j
                if stop is None and self.is_decl_end(j):
                    return j
            if self.is_opener(j):
                j = self.close_of(j) + 1
                continue
            if self.is_p(j, "<"):
                angle += 1
            elif self.is_p(j, ">"):
                angle -= 1
                if angle < 0:
                    raise _Undecided(f"type not recognised at line {tk.line}")
            j += 1

    def skip_angles(self, j: int) -> int:
        angle = 0
        while True:
            tk = self.tok(j)
            if tk is None or self.is_p(j, ";"):
                raise _Undecided("type parameter list not recognised")
            if self.is_opener(j):
                j = self.close_of(j) + 1
                continue
            if self.is_p(j, "<"):
                angle += 1
            elif self.is_p(j, ">"):
                angle -= 1
                if angle == 0:
                    return j + 1
            j += 1

    def arrow_after_params(self, open_paren: int) -> int | None:
        """Index after `=>` when `(params)` [`: type`] `=>` starts at open_paren."""
        r = self.close_of(open_paren) + 1
        if self.ts and self.is_p(r, ":"):
            r = self.skip_type(r + 1, "arrow", None)
        return r + 1 if self.is_p(r, "=>") else None

    def classify(self, p: int, stop: int | None) -> tuple[str, int]:
        """Classify the initializer at p: "fn" or a proven R4 class, else
        "unverified". Returns (class, index after the classified prefix)."""
        tk = self.tok(p)
        if tk is None or (stop is not None and p >= stop):
            raise _Undecided("empty initializer")
        if self.is_p(p, "("):
            after_arrow = self.arrow_after_params(p)
            if after_arrow is not None:
                return "fn", after_arrow  # C02, Z20
            close = self.close_of(p)
            # Z05: unwrap `( expr )` — the inner expression must span the parens.
            inner, inner_end = self.classify(p + 1, close)
            if inner == "fn":
                inner_depth = self.t[p].depth + 1
                if any(x.kind == "punct" and x.value == "," and x.depth == inner_depth for x in self.t[inner_end:close]):
                    return "unverified", close + 1
            elif inner_end != close:
                return "unverified", close + 1
            q = close + 1
            if self.ends(q, stop):
                return inner, q
            if self.ts and inner == "fn" and (self.is_id(q, "as") or self.is_id(q, "satisfies")):
                return "fn", self.skip_type(q + 1, "end", stop)  # Z18, Z26
            return "unverified", q
        if self.ts and self.is_p(p, "<"):
            q = self.skip_angles(p)
            if self.is_p(q, "("):
                after_arrow = self.arrow_after_params(q)
                if after_arrow is not None:
                    return "fn", after_arrow  # Z19
            return "unverified", p + 1
        if tk.kind == "ident":
            v = tk.value
            if v == "function":
                return "fn", p + 1  # Y01, Z27
            if v == "async":
                if self.is_id(p + 1, "function") and not self.t[p + 1].nl:
                    return "fn", p + 2  # Z06
                if self.is_p(p + 1, "("):
                    after_arrow = self.arrow_after_params(p + 1)
                    if after_arrow is not None:
                        return "fn", after_arrow  # Y02
                if self.is_id(p + 1) and self.is_p(p + 2, "=>"):
                    return "fn", p + 3
            if self.is_p(p + 1, "=>") and v not in _NON_REFERENCE_WORDS:
                return "fn", p + 2  # Z07
            if v == "class":
                q = p + 1
                if self.is_id(q) and self.t[q].value != "extends":
                    q += 1
                if self.is_p(q, "{"):
                    q = self.close_of(q) + 1
                    if self.ends(q, stop):
                        return "class", q  # Z15
                return "unverified", p + 1
            if v == "null":
                return ("null", p + 1) if self.ends(p + 1, stop) else ("unverified", p + 1)  # Z02
            if v in _NON_REFERENCE_WORDS:
                return "unverified", p + 1
            if self.ends(p + 1, stop):
                return "ident", p + 1  # Y17
            if self.is_p(p + 1, "("):
                return "call", self.close_of(p + 1) + 1  # Z04
            return "unverified", p + 1
        if tk.kind in ("num", "str"):
            if self.ends(p + 1, stop):
                return tk.kind, p + 1  # C08, Z01
            return "unverified", p + 1
        if self.is_p(p, "[") or self.is_p(p, "{"):
            q = self.close_of(p) + 1
            if self.ends(q, stop):
                return ("array" if tk.value == "[" else "object"), q  # Z03, X12
            return "unverified", q
        return "unverified", p + 1

    # -- verdict -------------------------------------------------------
    def verdict(self) -> RegisterVerdict:
        exports = self.exports
        if len(exports) > 1:
            return RegisterVerdict("UNDECIDED", exports[1].line, None, "more than one `register` export")
        if not exports:
            if self.ts_export_assign:
                return RegisterVerdict("UNDECIDED", None, None, "TS `export =` assignment")
            if any(
                tk.kind == "ident" and tk.value == "export" and tk.depth > 0 and not self.is_p(i - 1, ".")
                for i, tk in enumerate(self.t)
            ):
                return RegisterVerdict("UNDECIDED", None, None, "`export` inside a block (namespace or module)")
            if self.star_line is not None:
                # C06a, C06b: `export *` is not followed even when the target exports register.
                return RegisterVerdict("R3", self.star_line, None, "only `export *`")
            # C04, C07, C09-C11, X01, X02, X08-X11, X16-X18, Z21, Z24, W.
            return RegisterVerdict("R1", None, None, "no export named register")
        e = exports[0]
        if e.form == "function":
            return RegisterVerdict("OK", e.line, None, "function declaration")  # C01, X03, Y07
        if e.form == "const-fn":
            return RegisterVerdict("OK", e.line, None, "const function")
        if e.form == "spec-local":
            return self.local_verdict(e)
        if e.form == "spec-from":
            return RegisterVerdict("R2", e.line, e.local, "re-export")  # C05a, C05b, Z13, Z17, V04, V06
        if e.form == "star-as":
            return RegisterVerdict("R3", e.line, None, "`export * as register`")  # X13
        if e.form in ("const-r4", "letvar-r4", "enum"):
            return RegisterVerdict("R4", e.line, None, "not a const function")
        if e.form == "class":
            return RegisterVerdict("R5", e.line, None, "class")  # X06, Z25
        details = {
            "spec-from-str": "re-export with a string local name",
            "const-unverified": "a const initializer outside the proven set",
            "letvar-unverified": "a let/var initializer outside the proven set",
            "unknown": "an export form outside the proven set",
        }
        return RegisterVerdict("UNDECIDED", e.line, e.local, details[e.form])

    def local_verdict(self, e: _RegisterExport) -> RegisterVerdict:
        local = e.local or ""
        if e.local_is_str or local in _RESERVED_LOCALS:
            return RegisterVerdict("UNDECIDED", e.line, local, "export list with a string or reserved local")
        kind = self.decls.get(local)
        if kind in ("function", "const-fn"):
            return RegisterVerdict("OK", e.line, local, f"export list alias of `{local}`")  # C03, X05, X07, Y03, Z11
        if kind in ("let", "var", "class", "import", "const-other:numeric"):
            return RegisterVerdict("R6", e.line, local, f"alias of a {kind} binding")  # Y04, Z10, Y05, Y08, Y14
        if kind is None and self.idents[local] == 1:
            # Z16: the local appears nowhere else at all, so nothing declares it.
            return RegisterVerdict("R7", e.line, local, "undeclared export list local")
        return RegisterVerdict("UNDECIDED", e.line, local, f"export list local `{local}` not classified")


def _strip_prelude(source: str) -> str:
    """Drop a BOM and blank a `#!` line (keeping its length, so line numbers hold)."""
    src = source[1:] if source.startswith(_BOM) else source
    if src.startswith("#!"):
        newline = src.find("\n")
        src = "" if newline < 0 else " " * newline + src[newline:]
    return src


def scan_register_export(source: str, suffix: str) -> RegisterVerdict:
    """Decide statically whether a hooks module source exports `register`
    the way `claude plugin validate` 2.1.332 accepts. suffix selects TS
    erasure (.ts .tsx .mts .cts) and JSX lexing (.jsx .tsx)."""
    ts = suffix in TS_SUFFIXES
    jsx = suffix in JSX_SUFFIXES
    src = _strip_prelude(source)
    try:
        lexer = _Lexer(src, jsx)
        toks = lexer.run(in_template=False)
        parser = _Parser(toks, lexer.match, lexer.idents, ts)
        parser.parse()
        verdict = parser.verdict()
    except _Undecided as exc:
        return RegisterVerdict("UNDECIDED", None, None, str(exc))
    except RecursionError:
        # Fail-open, not a hidden bug: deeply nested parentheses exceed
        # Python's recursion limit before native's 2000-scope limit.
        return RegisterVerdict("UNDECIDED", None, None, "nesting too deep for the scanner")
    if verdict.code in ("R1", "R3") and jsx and _RAW_EXPORT_REGISTER.search(source):
        # JSX guard (spec B.2 step 4): a lenient JSX-text quote or a `/*` in
        # JSX text can swallow a real export, so absence is not trusted in
        # .jsx/.tsx when the raw text shows `export ... register`. The .js/.ts
        # decoys X01, X02, X16, X17 stay MAJOR because the guard is JSX-only.
        return RegisterVerdict("UNDECIDED", None, None, "JSX module whose raw text mentions `export ... register`")
    return verdict


# ---------------------------------------------------------------------------
# Module-body rules C2, C3, C4, C9 (TRDD-29QP66H2, mods gap list batch 1) and
# C1, C5, C6, C8 (batch 2; C7, the types contract, is further below)
#
# `claude plugin validate` 2.1.332 reads a hooks module far beyond its
# `register` export. This scan mirrors the errors of it that the token stream
# decides (C1-C5, C9) and collects the $.state references (C6) and the Client
# modules (C8) the caller checks against files on disk, with the same contract
# as the register scan: pure, never executes, never opens a sibling file, and
# an UNDER-detection (fail open) is always preferred to a false finding. Every
# rule is ORACLE-DERIVED: the fixture ids (p06 ...) are those of
# reports_dev/mods-gap; the batch-1 probe names (t-*, g-*, i-*, j-*, d-*, e-*,
# u-*) are those of reports_dev/5290/probe, the batch-2 probe names (e-*, d-*,
# d2-*, s-*, t-*, c-*) those of reports_dev/mods-expert/batch2/final-20261128/probe;
# all ran on 2.1.332.
# ---------------------------------------------------------------------------

BodyCode = Literal["C1", "C2", "C3", "C3-undecided", "C4-specifier", "C4-dynamic", "C5", "C9"]

# Hooks of these events stand on the "anthropic" telemetry stream unless a
# matcher names `to: "collector"` (mods reference, Telemetry; p06 vs p07).
_TELEMETRY_EVENTS = frozenset({"telemetry.log", "telemetry.mark"})
# These events stream, so their hook is an async generator (p14, p34).
_STREAMING_EVENTS = frozenset({"turn.step", "process.spawn"})
# C1: every event name of Claude Code 2.1.332, the 171 string literals of `EventName` in the types that
# release writes (.claude-plugin/types/claude-code/index.d.ts, first line "Written by Claude Code 2.1.332"),
# read with the TypeScript checker; one fixture hooking all 171 passes native validate (probe e-all-events).
# PINNED TO 2.1.332: a later release that adds an event makes this table refuse it until it is regenerated
# (the TypeScript checker over `EventName`, script in reports_dev/mods-expert/batch2) and diffed; the CI
# cross-check against native validate is what notices.
_EVENT_NAMES = frozenset(
    """
    agent.call agent.limits agent.list agent.offer agent.register agent.report agent.spawn
    attribution.text audio.play audio.speak classic.ConfigChange classic.CwdChanged
    classic.DirectoryAdded classic.Elicitation classic.ElicitationResult classic.FileChanged
    classic.InstructionsLoaded classic.MessageDisplay classic.Notification classic.PermissionDenied
    classic.PermissionRequest classic.PostCompact classic.PostModelSwitch classic.PostToolBatch
    classic.PostToolUse classic.PostToolUseFailure classic.PreCompact classic.PreModelSwitch
    classic.PreSkillShellCommand classic.PreToolUse classic.SessionEnd classic.SessionStart
    classic.Setup classic.Stop classic.StopFailure classic.SubagentStart classic.SubagentStop
    classic.TaskCompleted classic.TaskCreated classic.TeammateIdle classic.UserPromptExpansion
    classic.UserPromptSubmit classic.WorktreeCreate classic.WorktreeRemove clock.after clock.every
    clock.now clock.sleep command.describe command.list command.register command.run config.describe
    config.list config.set engine.create env.get env.set fs.ancestors fs.exists fs.list fs.read fs.stat
    fs.write http.fetch mcp.call mcp.connect model.classify model.complete model.fork plugin.register
    process.hookEnv process.run process.spawn prompt.attachment prompt.autocomplete prompt.change
    prompt.compose prompt.context prompt.edit prompt.fill prompt.mention prompt.paint prompt.read
    prompt.section prompt.submit prompt.suggest session.append session.attach session.authorize
    session.compact session.cwd session.detach session.end session.id session.identity session.info
    session.machine session.measure session.messages session.model session.receive session.redact
    session.repo session.root session.send session.start session.stop session.surface session.surfaces
    session.turns session.usage session.version settings.read skill.prompt state.get state.set
    store.delete store.get store.keys store.set telemetry.log telemetry.mark theme.clear theme.set
    tool.call tool.check tool.describe tool.judge tool.list tool.offer tool.register tool.run turn.abort
    turn.complete turn.start turn.step ui.blit ui.cancelNotify ui.close ui.copy ui.fault ui.focus
    ui.input ui.invalidate ui.log ui.message ui.notice ui.notify ui.offer ui.open ui.panes ui.place
    ui.pointer ui.press ui.render ui.resolve ui.scroll ui.select ui.selection ui.status ui.tip ui.toast
    ui.view vcs.detect vcs.diff vcs.isolate vcs.list vcs.release vcs.status wasm.compile
    """.split()
)
# The namespaces those names start with. Native refuses an unknown name only under one of these heads, or a
# name with no dot at all (probes e-tool-cal, e-classic-bogus, e-ui-ask, e-plugin-name, e-two-dots, e-dotless,
# e-head-only, e-core-head-types); a name under any other head passes (e-unknown-head, e-case-head,
# e-dep-namespace: a namespace another plugin adds), so the scan never judges it.
_EVENT_NAMESPACES = frozenset(name.split(".", 1)[0] for name in _EVENT_NAMES)
_NOT_A_PATTERN = 'is not an event name or a pattern ("*", "<prefix>.*", or "!" before either)'
# C5: words a `$` may follow in code; after any other word on the same line it is prose (JSX text).
_KEYWORDS_BEFORE_VALUE = frozenset(
    "return typeof instanceof in of new delete void throw case do else yield await extends".split()
)


def _event_problem(pattern: str) -> str | None:
    """The native C1 sentence for the first argument of on(...), or None when native accepts it or the
    form is unprobed. Probes: e-empty, e-trailing-space, e-star-call, e-glob-double (not a pattern),
    e-neg-star, e-double-neg, e-neg-unknown ("!tool.cal" reports "tool.cal"), e-all-globs, e-all-negations."""
    if pattern == "" or any(ch.isspace() for ch in pattern):
        return f'"{pattern}" {_NOT_A_PATTERN}'
    if pattern.startswith("!!"):
        return f'"{pattern}" is not a pattern: "!" negates once'
    negated = pattern.startswith("!")
    body = pattern[1:] if negated else pattern
    if body == "*":
        return f'"{pattern}" is not a pattern: it selects no event' if negated else None
    if "*" in body:
        if body.endswith(".*") and body.count("*") == 1:
            return None  # `<prefix>.*`: every core prefix passes (e-all-globs), an unknown one too (e-unknown-head-glob)
        return f'"{pattern}" {_NOT_A_PATTERN}'
    if "!" in body or body in _EVENT_NAMES:
        return None
    if "." in body and body.split(".", 1)[0] not in _EVENT_NAMESPACES:
        return None
    return f'"{body}" is not an event'


_SCOPE_LIMIT_MESSAGE = (
    f"code nested too deep to scan: more than {_MAX_DEPTH} scopes (functions, blocks, loops) open inside one another"
)
# The body scan never feeds the recursive parser, so its lexer may go far past
# the register scan's total bracket depth: native's limit counts scopes only.
_BODY_MAX_BRACKET_DEPTH = _MAX_DEPTH * 50


@dataclass(frozen=True)
class BodyFinding:
    """One module-body rule hit. line: 1-based. detail: the event name (C2, C3), the identifier
    (C3-undecided), the import specifier (C4-specifier) or the native sentence (C1, C5, C9)."""

    code: BodyCode
    line: int
    detail: str


@dataclass(frozen=True)
class RelativeImport:
    """A relative import of the hooks module; the caller resolves it on disk."""

    spec: str
    line: int


@dataclass(frozen=True)
class BodyScan:
    """Findings plus the relative imports to resolve. Empty when the source
    could not be lexed (the register scan already reports that as INFO)."""

    findings: tuple[BodyFinding, ...]
    relative_imports: tuple[RelativeImport, ...]
    state_refs: tuple[StateRef, ...] = ()
    client_modules: tuple[ClientModule, ...] = ()


@dataclass(frozen=True)
class StateRef:
    """A `$.state` reference whose plugin and key are string literals (C6); the caller compares it
    with the `interface PluginState` of the manifest's types contract."""

    plugin: str
    key: str
    line: int


@dataclass(frozen=True)
class ClientModule:
    """The string-literal `module` of a Client element (C8); the caller resolves it on disk."""

    spec: str
    line: int


def _is_relative_specifier(spec: str) -> bool:
    return spec in (".", "..") or spec.startswith(("./", "../"))


class _BodyScanner:
    """Walks the depth-0 and call-site tokens of a hooks module."""

    def __init__(self, toks: list[Tok], match: dict[int, int], ts: bool, jsx: bool = False) -> None:
        self.t = toks
        self.ts = ts
        self.p = _Parser(toks, match, Counter(), ts)
        self.findings: list[BodyFinding] = []
        self.relative: list[RelativeImport] = []
        # Top-level function bindings: name -> "asyncgen" | "other" | "unknown".
        self.top_fns: dict[str, str] = {}
        # C6 state references and C8 Client modules, resolved by the caller against files on disk.
        self.state_refs: list[StateRef] = []
        self.clients: list[ClientModule] = []
        self.jsx = jsx
        self._jsx_text: set[int] | None = None

    def run(self) -> None:
        self.collect_top_functions()
        for j, tk in enumerate(self.t):
            if tk.kind != "ident":
                continue
            prev = self.p.tok(j - 1)
            if prev is not None and (
                (prev.kind == "punct" and prev.value in (".", "?."))
                or (prev.kind == "ident" and prev.value == "function")
            ):
                continue  # `emitter.on(...)`, `x.import(...)`, `function on(...)` are not the forms native reads
            if tk.value == "import":
                self.on_import(j)
            elif tk.value == "export" and tk.depth == 0:
                self.on_export(j)
            elif tk.value == "on":
                self.on_call(j)
        # Batch 2 passes. An _Undecided inside one of them drops only that pass: it must never erase
        # the C2-C4 findings above, which a whole-scan _Undecided would.
        for batch2_pass in (self.check_registrations, self.collect_state_refs, self.collect_clients):
            try:
                batch2_pass()
            except (_Undecided, RecursionError):
                continue

    # -- C4: imports ---------------------------------------------------
    def add_specifier(self, spec: Tok) -> None:
        value = spec.value
        if value.startswith(_ESCAPED_STRING_MARK) or value == "claude-code":
            return  # an escaped specifier is not trusted; "claude-code" is the one allowed package
        if _is_relative_specifier(value):
            self.relative.append(RelativeImport(value, spec.line))
        else:
            self.findings.append(BodyFinding("C4-specifier", spec.line, value))

    def on_import(self, j: int) -> None:
        nxt = self.p.tok(j + 1)
        if nxt is None:
            return
        if nxt.kind == "punct" and nxt.value == "(":
            # p47 (and probes i-dyn-nonliteral, i-dyn-claude-code): native refuses every import(),
            # whatever its target and wherever it stands. In a TypeScript module an `import("x")` in
            # TYPE position (`$: import("claude-code").EngineInterface`) is erased and accepted
            # (found on 2 natively accepted catalogue mods), so there only a value position counts:
            # after `await` or `return`, or at the start of a statement.
            prev = self.p.tok(j - 1)
            value_position = prev is None or (
                (prev.kind == "ident" and prev.value in ("await", "return"))
                or (prev.kind == "punct" and prev.value in (";", "{", "}"))
            )
            if not self.ts or value_position:
                self.findings.append(BodyFinding("C4-dynamic", self.t[j].line, ""))
            return
        if self.t[j].depth != 0 or (nxt.kind == "punct" and nxt.value == "."):
            return  # `import.meta`, or not an import declaration
        p = self.p
        k = j + 1
        if self.ts and p.is_id(k, "type") and not (p.is_id(k + 1, "from") or p.is_p(k + 1, ",")):
            return  # `import type ...` is erased by the TS compile (probes i-npm-type-ts, j-ts-type-ns)
        first = p.tok(k)
        if first is None:
            return
        if first.kind == "str":
            self.add_specifier(first)  # `import "x"`
            return
        if p.is_id(k) and p.is_p(k + 1, "="):
            return  # TS `import x = require("x")` passes natively (probe i-require-ts)
        all_inline_type = self.ts and p.is_p(k, "{") and self.only_inline_types(k)
        m = k
        while True:
            tk = p.tok(m)
            if tk is None or p.is_p(m, ";"):
                return
            if p.is_opener(m):
                close = p.match.get(m)
                if close is None:
                    return
                m = close + 1
                continue
            if tk.kind == "ident" and tk.value == "from":
                spec = p.tok(m + 1)
                if spec is not None and spec.kind == "str":
                    if not all_inline_type:  # `import { type A, type B } from` is erased (probe i-npm-typeinline-ts)
                        self.add_specifier(spec)
                    return
            if m > k and tk.nl and tk.kind == "ident" and tk.value in _BOUNDARY_KEYWORDS:
                return  # a new statement began before any `from`
            m += 1

    def only_inline_types(self, brace: int) -> bool:
        """`{ type A, type B }`: every specifier carries the `type` modifier."""
        close = self.p.match.get(brace)
        if close is None or close == brace + 1:
            return False
        entries = self.split(brace + 1, close, self.t[brace].depth + 1)
        return all(
            e - s >= 2 and self.t[s].kind == "ident" and self.t[s].value == "type" and self.t[s + 1].value != "as"
            for s, e in entries
        )

    def on_export(self, j: int) -> None:
        """`export ... from "x"` is an import for native (probes i-export-from-npm, i-export-star-npm);
        `export type ... from` is erased (probe j-ts-export-type-from)."""
        p = self.p
        k = j + 1
        if p.is_p(k, "*"):
            m = k + 1
            if p.is_id(m, "as"):
                m += 2
        elif p.is_p(k, "{"):
            close = p.match.get(k)
            if close is None:
                return
            m = close + 1
        else:
            return
        spec = p.tok(m + 1)
        if p.is_id(m, "from") and spec is not None and spec.kind == "str":
            self.add_specifier(spec)

    # -- C2, C3: on("<event>", ...) ------------------------------------
    def split(self, start: int, stop: int, depth: int) -> list[tuple[int, int]]:
        """Comma-separated, non-empty token ranges [s, e) between start and stop at bracket depth `depth`."""
        out: list[tuple[int, int]] = []
        s = start
        for i in range(start, stop):
            tk = self.t[i]
            if tk.depth == depth and tk.kind == "punct" and tk.value == ",":
                if i > s:
                    out.append((s, i))
                s = i + 1
        if stop > s:
            out.append((s, stop))
        return out

    def on_call(self, j: int) -> None:
        p = self.p
        event = p.tok(j + 2)
        close = p.match.get(j + 1)
        if not p.is_p(j + 1, "(") or close is None or event is None or event.kind != "str":
            return
        if event.value not in _TELEMETRY_EVENTS and event.value not in _STREAMING_EVENTS:
            return
        args = self.split(j + 2, close, self.t[j + 1].depth + 1)
        if not args or args[0] != (j + 2, j + 3):
            return
        if event.value in _TELEMETRY_EVENTS:
            self.check_telemetry(self.t[j].line, event.value, args)
        else:
            self.check_streaming(self.t[j].line, event.value, args)

    def check_telemetry(self, line: int, event: str, args: list[tuple[int, int]]) -> None:
        """C2, native (p06): `its hooks stand on the telemetry stream "anthropic" (a matcher names it,
        or a telemetry hook names no "to" and so stands on every stream) ... name the collector on each
        telemetry hook: on("telemetry.log", { to: "collector" }, hook)`. p07 passes."""
        if len(args) == 2:
            if self.t[args[1][0]].value != "{":  # `on(event, { ... })` has no hook: another native message
                self.findings.append(BodyFinding("C2", line, event))
        elif len(args) >= 3:
            s, e = args[1]
            if self.t[s].value == "{" and self.p.match.get(s) == e - 1 and self.matcher_on_anthropic(s):
                self.findings.append(BodyFinding("C2", line, event))

    def matcher_on_anthropic(self, brace: int) -> bool:
        """True when the matcher object literal stands on the anthropic stream: no `to` key, or
        `to` naming "anthropic" (probes u-matcher-x, t-to-anthropic, t2-to-both, u-to-both-reversed).
        A spread, shorthand, method, computed key or non-literal `to` has its own native message: False."""
        t = self.t
        has_to = False
        for s, e in self.split(brace + 1, self.p.match[brace], t[brace].depth + 1):
            key, colon = t[s], t[s + 1] if s + 1 < e else None
            if key.kind not in ("ident", "str") or colon is None or colon.kind != "punct" or colon.value != ":":
                return False
            if key.value != "to":
                continue
            has_to = True
            value = t[s + 2 : e]
            if len(value) == 1 and value[0].kind == "str":
                names = [value[0].value]
            elif value and value[0].value == "[" and self.p.match.get(s + 2) == e - 1:
                inner = value[1:-1]
                if any(x.kind != "str" and not (x.kind == "punct" and x.value == ",") for x in inner):
                    return False
                names = [x.value for x in inner if x.kind == "str"]
            else:
                return False
            if "anthropic" in names:
                return True
        return not has_to

    def check_streaming(self, line: int, event: str, args: list[tuple[int, int]]) -> None:
        """C3, native (p14, p34): `the hook on "turn.step" is not an async generator: turn.step streams,
        so it takes async function* ($, e, next) { ... }`. Native also resolves a hook NAME to a function
        declared at the top of the file or a const bound to one (probes g-ident-nongen, g2-const-fn-expr)."""
        if len(args) < 2:
            return
        s, e = args[-1]
        kind = self.classify_function(s, e)
        if isinstance(kind, tuple):
            resolved = self.top_fns.get(kind[1], "unknown")
            if resolved == "unknown":
                self.findings.append(BodyFinding("C3-undecided", line, kind[1]))
                return
            kind = resolved
        if kind == "other":
            self.findings.append(BodyFinding("C3", line, event))

    def classify_function(self, s: int, e: int) -> str | tuple[str, str] | None:
        """Return "asyncgen", "other" (a function literal that is not an async generator), ("name", id)
        for a bare identifier, None for anything else (a member, a call, a parenthesised form)."""
        t, p = self.t, self.p
        first = t[s]
        if first.kind == "ident" and first.value == "async" and p.is_id(s + 1, "function"):
            return ("asyncgen" if p.is_p(s + 2, "*") else "other") if self.ends_with_body(e) else None
        if first.kind == "ident" and first.value == "function":
            return "other" if self.ends_with_body(e) else None  # `function` and `function*`: both refused by native
        if self.is_arrow(s, e):
            return "other"
        if e - s == 1 and first.kind == "ident":
            return ("name", first.value)
        return None

    def ends_with_body(self, e: int) -> bool:
        """The range ends with a `}`: a function literal, not `function () {}.bind(x)`."""
        return self.p.is_p(e - 1, "}")

    def is_arrow(self, s: int, e: int) -> bool:
        """`x => ...`, `async x => ...`, `(...) => ...`, `async (...) => ...`, with a TS return type allowed."""
        t, p = self.t, self.p
        i = s
        if p.is_id(i, "async") and not p.is_p(i + 1, "=>"):
            i += 1
        if i >= e:
            return False
        if t[i].kind == "ident" and p.is_p(i + 1, "=>"):
            return True
        close = p.match.get(i) if p.is_p(i, "(") else None
        if close is None:
            return False
        if p.is_p(close + 1, "=>"):
            return True
        if self.ts and p.is_p(close + 1, ":"):
            m = close + 2
            while m < e:
                if p.is_opener(m):
                    inner = p.match.get(m)
                    if inner is None:
                        return False
                    m = inner + 1
                    continue
                if p.is_p(m, "=>") and t[m].depth == t[s].depth:
                    return True
                m += 1
        return False

    def bind(self, name: str, kind: str) -> None:
        old = self.top_fns.get(name)
        self.top_fns[name] = kind if old in (None, kind) else "unknown"

    def collect_top_functions(self) -> None:
        """Bind every top-level `function name` declaration and `const name = <function literal>`;
        a `let` or `var`, a local or an imported name stays unbound (probes g2-let-asyncgen,
        g2-local-decl, g2-import-ident)."""
        p = self.p
        for j, tk in enumerate(self.t):
            if tk.depth != 0 or tk.kind != "ident" or tk.value not in ("function", "const"):
                continue
            prev = p.tok(j - 1)
            if prev is not None and prev.kind == "punct" and prev.value in (".", "?."):
                continue
            is_async = tk.value == "function" and prev is not None and prev.kind == "ident" and prev.value == "async"
            b = j - 1 if is_async else j
            while p.is_id(b - 1, "export") or p.is_id(b - 1, "default"):
                b -= 1
            if not p.stmt_start(b):
                continue  # a function EXPRESSION, whose name binds nowhere outside itself
            if tk.value == "function":
                k = j + 2 if p.is_p(j + 1, "*") else j + 1
                name = p.tok(k)
                if name is not None and name.kind == "ident":
                    self.bind(name.value, "asyncgen" if is_async and p.is_p(j + 1, "*") else "other")
            elif p.is_id(j + 1) and (p.is_p(j + 2, "=") or (self.ts and p.is_p(j + 2, ":"))):
                try:
                    # `const stepped: Hook<"turn.step"> = async function* ...` (TS): skip the annotation.
                    eq = p.skip_type(j + 3, "eq", None) if p.is_p(j + 2, ":") else j + 2
                except _Undecided:
                    continue
                end = p.scan_decl_end(eq + 1) if p.is_p(eq, "=") else eq
                kind = self.classify_function(eq + 1, end) if end > eq + 1 else None
                self.bind(self.t[j + 1].value, kind if isinstance(kind, str) else "unknown")

    # -- C1, C5: register, its on(...) calls and their hooks ------------
    # Native 2.1.332 (probes e-* and d-* of reports_dev/mods-expert batch 2) reads `on` as the first
    # parameter of `register` whatever its name (e-param-hook) and never a module-level `on`
    # (e-top-level, e-other-name-module-on); it reads `$` as the first parameter of each hook whatever
    # its name (d-other-param-alias) and never a module-level `$` (d-module-dollar). Native also follows
    # `on` and `$` into helpers they are passed to (e-helper-forward, d-helper-alias); this scan does not,
    # an under-detection, never a false finding.
    def params(self, k: int) -> tuple[str | None, int] | None:
        """(first parameter name or None, index of the closing paren) of the parameter list at k."""
        p = self.p
        close = p.match.get(k) if p.is_p(k, "(") else None
        if close is None:
            return None
        first = p.tok(k + 1)
        named = first is not None and first.kind == "ident" and any(p.is_p(k + 2, v) for v in (",", ")", ":", "=", "?"))
        return (first.value if named and first is not None else None), close

    def function_body(self, m: int, arrow: bool, stop: int | None) -> tuple[int, int] | None:
        """Token range [lo, hi) of the body after a parameter list ending at m - 1: `=> { ... }`, `=> expr`
        (up to stop, the end of the argument), or `{ ... }`; a TS return-type annotation is skipped."""
        p = self.p
        if self.ts and p.is_p(m, ":"):
            m += 1
            while p.tok(m) is not None and not p.is_p(m, "=>" if arrow else "{"):
                if p.is_p(m, ";"):
                    return None
                if p.is_opener(m):
                    close = p.match.get(m)
                    if close is None:
                        return None
                    m = close + 1
                    continue
                m += 1
        if arrow:
            if not p.is_p(m, "=>"):
                return None
            m += 1
            if not p.is_p(m, "{"):
                return (m, stop) if stop is not None and stop > m else None
        if not p.is_p(m, "{"):
            return None
        close = p.match.get(m)
        return None if close is None else (m + 1, close)

    def function_literal(self, s: int, stop: int | None) -> tuple[str | None, int, int] | None:
        """(first parameter, body lo, body hi) of the function literal at s: `[async] function [*] [name]
        (...) {...}`, `[async] (...) => ...` or `[async] x => ...`; None for any other form."""
        p = self.p
        k = s
        if p.is_id(k, "async") and not p.is_p(k + 1, "=>"):
            k += 1
        if p.is_id(k, "function"):
            k += 1
            if p.is_p(k, "*"):
                k += 1
            if p.is_id(k):
                k += 1
            got = self.params(k)
            body = None if got is None else self.function_body(got[1] + 1, False, stop)
            return None if got is None or body is None else (got[0], body[0], body[1])
        if p.is_id(k) and p.is_p(k + 1, "=>"):
            body = self.function_body(k + 1, True, stop)
            return None if body is None else (self.t[k].value, body[0], body[1])
        got = self.params(k)
        body = None if got is None else self.function_body(got[1] + 1, True, stop)
        return None if got is None or body is None else (got[0], body[0], body[1])

    def top_function(self, j: int) -> tuple[str | None, int, int] | None:
        """The function a depth-0 binding at j names: `function NAME (...)` (j at NAME) or
        `const|let|var NAME [: T] = <function literal>`."""
        p = self.p
        if p.is_id(j - 1, "function"):
            got = self.params(j + 1)
            body = None if got is None else self.function_body(got[1] + 1, False, None)
            return None if got is None or body is None else (got[0], body[0], body[1])
        if not any(p.is_id(j - 1, kw) for kw in ("const", "let", "var")):
            return None
        eq = j + 1
        if self.ts and p.is_p(eq, ":"):
            eq = p.skip_type(j + 2, "eq", None)
        return self.function_literal(eq + 1, None) if p.is_p(eq, "=") else None

    def find_register(self) -> tuple[str, int, int] | None:
        """(first parameter, body lo, body hi) of the one depth-0 `register` function, or None."""
        found: list[tuple[str | None, int, int] | None] = []
        for j, tk in enumerate(self.t):
            if tk.depth == 0 and tk.kind == "ident" and tk.value == "register" and not self.p.is_p(j - 1, "."):
                if self.p.is_id(j - 1, "function") or any(self.p.is_id(j - 1, kw) for kw in ("const", "let", "var")):
                    found.append(self.top_function(j))
        if len(found) != 1 or found[0] is None or found[0][0] is None:
            return None
        name, lo, hi = found[0]
        return (name, lo, hi) if name is not None else None

    def named_functions(self) -> dict[str, tuple[str | None, int, int] | None]:
        """Top-level `function NAME` and `const NAME = <function literal>` bindings (None when repeated)."""
        out: dict[str, tuple[str | None, int, int] | None] = {}
        for j, tk in enumerate(self.t):
            if tk.depth != 0 or tk.kind != "ident" or self.p.is_p(j - 1, ".") or self.p.is_p(j - 1, "?."):
                continue
            if self.p.is_id(j - 1, "function") or any(self.p.is_id(j - 1, kw) for kw in ("const", "let", "var")):
                try:
                    got = self.top_function(j)
                except _Undecided:
                    got = None
                out[tk.value] = None if tk.value in out else got
        return out

    def enclosing_opener(self, j: int) -> int | None:
        """Index of the innermost bracket that contains token j."""
        d = self.t[j].depth
        for i in range(j - 1, -1, -1):
            tk = self.t[i]
            if tk.depth < d - 1:
                return None
            if tk.depth == d - 1 and tk.kind == "punct" and tk.value in _OPENERS:
                return i
        return None

    def rebinds(self, name: str, lo: int, hi: int) -> bool:
        """True when `name` is declared again inside [lo, hi): `const|let|var|function|class name`,
        `name =>`, a destructuring declaration, or a parameter of a nested function or catch clause.
        Native refuses that shadowing with its own message (probe e-shadow-inner); the scan then decides nothing."""
        p, t = self.p, self.t
        for j in range(lo, hi):
            tk = t[j]
            if tk.kind != "ident" or tk.value != name or p.is_p(j - 1, ".") or p.is_p(j - 1, "?."):
                continue
            if any(p.is_id(j - 1, kw) for kw in ("const", "let", "var", "function", "class")) or p.is_p(j + 1, "=>"):
                return True
            o = self.enclosing_opener(j)
            if o is None:
                continue
            if t[o].value == "(":
                close = p.match.get(o)
                if close is not None and (p.is_p(close + 1, "=>") or (self.ts and p.is_p(close + 1, ":"))):
                    return True
                if p.is_id(o - 1, "function") or p.is_id(o - 1, "catch") or (p.is_id(o - 1) and p.is_id(o - 2, "function")):
                    return True
                if close is not None and p.is_id(o - 1) and p.is_p(close + 1, "{"):
                    return True  # method shorthand `name(on) { ... }`
            elif any(p.is_id(o - 1, kw) for kw in ("const", "let", "var")):
                return True
        return False

    def check_registrations(self) -> None:
        """C1 on the event of every `on(...)` inside register, C5 on the `$` of every hook it registers."""
        reg = self.find_register()
        if reg is None:
            return
        on_name, lo, hi = reg
        if self.rebinds(on_name, lo, hi):
            return
        p, t = self.p, self.t
        named: dict[str, tuple[str | None, int, int] | None] | None = None
        seen: set[tuple[int, str]] = set()
        for j in range(lo, hi):
            tk = t[j]
            if tk.kind != "ident" or tk.value != on_name or not p.is_p(j + 1, "("):
                continue
            if p.is_p(j - 1, ".") or p.is_p(j - 1, "?.") or p.is_id(j - 1, "function"):
                continue
            close = p.match.get(j + 1)
            if close is None:
                continue
            args = self.split(j + 2, close, t[j + 1].depth + 1)
            if len(args) < 2:
                continue  # `on(event)` alone has its own native message (probe e-no-hook)
            s, e = args[0]
            if e - s == 1 and t[s].kind == "str" and not t[s].value.startswith(_ESCAPED_STRING_MARK):
                problem = _event_problem(t[s].value)
                if problem is not None:
                    self.findings.append(BodyFinding("C1", tk.line, problem))
            hs, he = args[-1]
            hook = self.function_literal(hs, he)
            if hook is not None and hook[2] > he:
                continue
            if hook is None and he - hs == 1 and t[hs].kind == "ident":
                named = self.named_functions() if named is None else named
                hook = named.get(t[hs].value)
            if hook is None or hook[0] is None or self.rebinds(hook[0], hook[1], hook[2]):
                continue
            for m in range(hook[1], hook[2]):
                use = t[m]
                if use.kind != "ident" or use.value != hook[0] or p.is_p(m - 1, ".") or p.is_p(m - 1, "?."):
                    continue
                problem = None if self.in_jsx_text(m) else self.dollar_problem(m)
                if problem is not None and (use.line, problem) not in seen:
                    seen.add((use.line, problem))
                    self.findings.append(BodyFinding("C5", use.line, problem))

    def dollar_problem(self, j: int) -> str | None:
        """The native C5 sentence for the use of a hook's `$` at token j, or None when native accepts it
        (`$.noun.method(...)`, `$.plugin.name`, `$.plugin.root`, `h($)`, `($).fs...`) or the form is unprobed."""
        p, t = self.p, self.t
        prev, nxt = p.tok(j - 1), p.tok(j + 1)
        if prev is not None and prev.kind == "ident" and prev.value not in _KEYWORDS_BEFORE_VALUE and not self.t[j].nl:
            return None  # `word $` is no code: JSX text or prose (catalogue dev slice, context-lens)
        if nxt is not None and nxt.kind == "punct" and nxt.value in ("[", "?."):
            return "a computed or optional member access on $"  # d-computed-root, d-optional-root (p10)
        if self.ts and prev is not None and prev.kind == "ident" and prev.value == "typeof":
            return None  # `typeof $` may stand in a TS type, which the compile erases
        if p.is_p(j + 1, "."):
            noun = p.tok(j + 2)
            if noun is None or noun.kind != "ident":
                return None
            if noun.value == "plugin":
                if p.is_p(j + 3, ".") and p.is_id(j + 4) and t[j + 4].value in ("name", "root"):
                    return None
                return "$.plugin is used other than as $.plugin.name or $.plugin.root"  # d-plugin-noun
            if self.ts and (p.is_p(j + 3, "!") or p.is_id(j + 3, "as") or p.is_id(j + 3, "satisfies")):
                return None
            if not p.is_p(j + 3, "."):
                # d-alias-noun (p09), d-optional-noun, d-computed-method, d-pass-noun, d-await-noun
                return f"$.{noun.value} is used as a value (a noun of $ bound, passed or read)"
            method, call = p.tok(j + 4), p.tok(j + 5)
            if method is None or method.kind != "ident" or p.is_p(j + 5, "("):
                return None
            if call is not None and (
                call.kind == "tmpl"
                or (self.ts and ((call.kind == "punct" and call.value in ("<", "!")) or (call.kind == "ident" and call.value in ("as", "satisfies"))))
            ):
                return None
            # d-method-ref, d-optional-call, d-bind, d-noun-method-member
            return f"$.{noun.value}.{method.value} is used as a value (an event of $ bound, passed or read) instead of called"
        if prev is None or nxt is None:
            return None
        if prev.kind == "punct" and prev.value == "(" and nxt.kind == "punct" and nxt.value == ")":
            return None  # `h($)` (p31) and `($).fs.read()` (d-paren-root) pass
        if nxt.kind == "ident" and nxt.value in ("as", "satisfies"):
            return None
        binary = ("===", "!==", "==", "!=")
        if (nxt.kind == "punct" and nxt.value in binary) or (prev.kind == "punct" and prev.value in binary) or (
            prev.kind == "ident" and prev.value in ("in", "instanceof")
        ):
            return "$ itself is used in a BinaryExpression (bound, passed, spread, returned or read)"  # d-in-root, d-eq-root
        if prev.kind == "punct" and prev.value == "=":
            o = self.enclosing_opener(j)
            if o is not None and t[o].value == "(":
                return None  # a default parameter or a parenthesised assignment: unprobed
            if self.declares(j - 1):
                return "$ itself is bound to a name (bound, passed, spread, returned or read)"  # d-alias-bare, d-destructure
            return "$ itself is assigned (bound, passed, spread, returned or read)"  # d-let-bare
        if prev.kind == "ident" and prev.value == "return":
            return "$ itself is returned (bound, passed, spread, returned or read)"  # d-return-root
        if prev.kind == "punct" and prev.value == "...":
            return "$ itself is spread (bound, passed, spread, returned or read)"  # d-root-spread
        if not self.ts and prev.kind == "ident" and prev.value == "typeof":
            return "$ itself is used in a UnaryExpression (bound, passed, spread, returned or read)"  # d-typeof-root
        o = self.enclosing_opener(j)
        if o is None or not (nxt.kind == "punct" and nxt.value in (",", "}", "]")):
            return None
        if t[o].value == "{" and prev.kind == "punct" and prev.value == ":":
            key, before = p.tok(j - 2), p.tok(j - 3)
            if key is not None and key.kind in ("ident", "str") and before is not None and before.kind == "punct" and before.value in ("{", ","):
                return "$ itself is put in an object (bound, passed, spread, returned or read)"  # d-obj-root
        if t[o].value == "[" and prev.kind == "punct" and prev.value in ("[", ","):
            before = p.tok(o - 1)
            if before is None or (before.kind == "punct" and before.value not in (")", "]")) or (
                before.kind == "ident" and before.value == "return"
            ):
                return "$ itself is put in an array (bound, passed, spread, returned or read)"  # d-arr-root
        return None

    def declares(self, eq: int) -> bool:
        """True when the `=` at eq ends the binding of a `const|let|var` declaration."""
        d = self.t[eq].depth
        for i in range(eq - 1, max(eq - 64, -1), -1):
            tk = self.t[i]
            if tk.depth < d or (tk.depth == d and tk.kind == "punct" and tk.value in (";", "=", ",")):
                return False
            if tk.depth == d and tk.kind == "ident" and tk.value in ("const", "let", "var"):
                return True
        return False

    # -- C6: $.state references with literal plugin and key --------------
    def claude_code_imports(self) -> dict[str, str]:
        """Local name -> imported name for `import { atom, update as up } from "claude-code"`."""
        p, t, out = self.p, self.t, {}
        for j, tk in enumerate(t):
            if tk.depth != 0 or tk.kind != "ident" or tk.value != "import" or p.is_p(j - 1, "."):
                continue
            k = j + 1
            if p.is_id(k) and p.is_p(k + 1, ","):
                k += 2
            close = p.match.get(k) if p.is_p(k, "{") else None
            spec = None if close is None else p.tok(close + 2)
            if close is None or not p.is_id(close + 1, "from") or spec is None or spec.kind != "str" or spec.value != "claude-code":
                continue
            for s, e in self.split(k + 1, close, t[k].depth + 1):
                if e - s == 1 and t[s].kind == "ident":
                    out[t[s].value] = t[s].value
                elif e - s == 3 and t[s].kind == "ident" and p.is_id(s + 1, "as") and t[s + 2].kind == "ident":
                    out[t[s + 2].value] = t[s].value
        return out

    def ref_literal(self, s: int, e: int) -> tuple[str, str] | None:
        """(plugin, key) of the argument [s, e) when it is `{ plugin: "p", key: "k" [, id: x] }`,
        optionally followed by TS `as const`; None for any other shape."""
        p, t = self.p, self.t
        close = p.match.get(s) if p.is_p(s, "{") else None
        if close is None or not (close == e - 1 or (close == e - 3 and p.is_id(e - 2, "as") and p.is_id(e - 1, "const"))):
            return None
        fields: dict[str, tuple[int, int]] = {}
        for a, b in self.split(s + 1, close, t[s].depth + 1):
            key = t[a].value if t[a].kind in ("ident", "str") else None
            if b - a < 3 or not p.is_p(a + 1, ":") or key not in ("plugin", "key", "id") or key in fields:
                return None
            fields[key] = (a + 2, b)
        if "plugin" not in fields or "key" not in fields:
            return None
        values = []
        for name in ("plugin", "key"):
            a, b = fields[name]
            if b - a != 1 or t[a].kind != "str" or t[a].value.startswith(_ESCAPED_STRING_MARK):
                return None
            values.append(t[a].value)
        return values[0], values[1]

    def collect_state_refs(self) -> None:
        """C6, native (p11b, p12d, probes s-*): every reference whose plugin and key are string literals,
        given to `$.state.get|set` or to `atom`/`memberOf` (first argument) or `read`/`update` (second
        argument) imported from "claude-code", written there or in a top-level const, is listed."""
        p, t = self.p, self.t
        imports = self.claude_code_imports()
        consts: dict[str, tuple[str, str] | None] = {}
        for j, tk in enumerate(t):
            if tk.depth == 0 and tk.kind == "ident" and tk.value == "const" and p.is_id(j + 1) and p.is_p(j + 2, "="):
                end = p.scan_decl_end(j + 3)
                name = t[j + 1].value
                consts[name] = None if name in consts else self.ref_literal(j + 3, end)
        for j, tk in enumerate(t):
            if tk.kind != "ident" or not p.is_p(j + 1, "("):
                continue
            member = p.is_p(j - 1, ".") or p.is_p(j - 1, "?.")
            if member and tk.value in ("get", "set") and p.is_id(j - 2, "state") and p.is_p(j - 3, "."):
                index = 0
            elif not member and not p.is_id(j - 1, "function") and imports.get(tk.value) in ("atom", "memberOf"):
                index = 0
            elif not member and not p.is_id(j - 1, "function") and imports.get(tk.value) in ("read", "update"):
                index = 1
            else:
                continue
            close = p.match.get(j + 1)
            args = [] if close is None else self.split(j + 2, close, t[j + 1].depth + 1)
            if len(args) <= index:
                continue
            s, e = args[index]
            ref = self.ref_literal(s, e)
            if ref is None and e - s == 1 and t[s].kind == "ident":
                ref = consts.get(t[s].value)
            if ref is not None:
                self.state_refs.append(StateRef(ref[0], ref[1], tk.line))

    # -- C8: the module of each Client element ---------------------------
    def collect_clients(self) -> None:
        """C8, native (p56, p57, probes c-*): native reads the `module` of every `Client({ ... })` call,
        bare or as a member (c-member, c-local-fn), and of every JSX `<Client module="...">` (c-jsx-*)."""
        p, t = self.p, self.t
        for j, tk in enumerate(t):
            if tk.kind != "ident" or tk.value != "Client":
                continue
            if p.is_p(j + 1, "(") and not (p.is_id(j - 1, "new") or p.is_id(j - 1, "function")):
                close = p.match.get(j + 1)
                args = [] if close is None else self.split(j + 2, close, t[j + 1].depth + 1)
                if not args or not p.is_p(args[0][0], "{") or p.match.get(args[0][0]) != args[0][1] - 1:
                    continue
                o = args[0][0]
                for a, b in self.split(o + 1, args[0][1] - 1, t[o].depth + 1):
                    if b - a == 3 and t[a].kind in ("ident", "str") and t[a].value == "module" and p.is_p(a + 1, ":"):
                        self.add_client(t[a + 2])
            elif self.jsx and p.is_p(j - 1, "<"):
                m = j + 1
                while m < len(t) and t[m].depth == tk.depth and not (p.is_p(m, ">") or p.is_p(m, "/>")):
                    if p.is_opener(m):
                        m = p.match.get(m, m) + 1
                        continue
                    if p.is_id(m, "module") and p.is_p(m + 1, "=") and m + 2 < len(t):
                        self.add_client(t[m + 2])
                    m += 1

    def add_client(self, value: Tok) -> None:
        if value.kind == "str" and not value.value.startswith(_ESCAPED_STRING_MARK):
            self.clients.append(ClientModule(value.value, value.line))

    # -- JSX text: words between tags are no code -------------------------
    def in_jsx_text(self, j: int) -> bool:
        """True when token j is JSX text (a child of an element that is neither `{...}` nor an element)."""
        if not self.jsx:
            return False
        if self._jsx_text is None:
            mask: set[int] = set()
            i = 0
            while i < len(self.t):
                i = self.jsx_element(i, mask) if self.jsx_opens(i, in_children=False) else i + 1
            self._jsx_text = mask
        return j in self._jsx_text

    def jsx_opens(self, i: int, in_children: bool) -> bool:
        """`<` that opens an element or fragment: followed by a name or `>`, and, outside JSX children,
        standing where an expression starts (never `a < b` or TS `f<T>()`)."""
        p = self.p
        nxt = p.tok(i + 1)
        if not p.is_p(i, "<") or nxt is None or not (nxt.kind == "ident" or (nxt.kind == "punct" and nxt.value == ">")):
            return False
        if in_children:
            return True
        prev = p.tok(i - 1)
        if prev is None:
            return True
        if prev.kind == "punct":
            return prev.value in ("(", ",", "=", ":", "?", "[", "{", "=>", "&&", "||", "??", "!")
        return prev.kind == "ident" and prev.value in ("return", "yield", "default", "case", "else", "do", "await")

    def jsx_element(self, i: int, mask: set[int]) -> int:
        """Skip the element whose `<` is at i, adding its text children to mask; returns the index after it."""
        p, t, n = self.p, self.t, len(self.t)
        d = t[i].depth
        m = i + 1
        if not p.is_p(m, ">"):
            while m < n and not (t[m].depth == d and t[m].kind == "punct" and t[m].value in (">", "/>")):
                if t[m].depth < d:
                    return m
                m = p.match.get(m, m) + 1 if p.is_opener(m) else m + 1
            if m >= n or t[m].value == "/>":
                return m + 1
        m += 1
        while m < n:
            tk = t[m]
            if tk.depth < d:
                return m
            if tk.depth == d and tk.kind == "punct" and tk.value == "</":
                while m < n and not (t[m].depth == d and t[m].kind == "punct" and t[m].value == ">"):
                    m += 1
                return m + 1
            if tk.depth == d and self.jsx_opens(m, in_children=True):
                m = self.jsx_element(m, mask)
                continue
            if tk.kind == "punct" and tk.value == "{":
                m = p.match.get(m, m) + 1
                continue
            mask.add(m)
            m += 1
        return m


def scan_module_body(source: str, suffix: str) -> BodyScan:
    """Statically apply the module-body rules C1-C5, C9 to a hooks module source and collect the
    $.state references (C6) and Client modules (C8) the caller checks against files on disk.
    suffix selects TS erasure (.ts .tsx .mts .cts) and JSX lexing (.jsx .tsx), as for the register scan."""
    ts = suffix in TS_SUFFIXES
    jsx = suffix in JSX_SUFFIXES
    try:
        lexer = _Lexer(_strip_prelude(source), jsx, max_depth=_BODY_MAX_BRACKET_DEPTH)
        toks = lexer.run(in_template=False)
        scanner = _BodyScanner(toks, lexer.match, ts, jsx)
        scanner.run()
    except _TooDeep as exc:
        # C9, native (p44): `code nested too deep to scan: more than 2000 scopes (functions, blocks,
        # loops) open inside one another`; the boundary was probed on blocks, arrow bodies, while, if
        # and object methods (register body + 1999 passes, + 2000 errors).
        return BodyScan((BodyFinding("C9", exc.line, _SCOPE_LIMIT_MESSAGE),), ())
    except (_Undecided, RecursionError):
        return BodyScan((), ())
    return BodyScan(
        tuple(scanner.findings), tuple(scanner.relative), tuple(scanner.state_refs), tuple(scanner.clients)
    )


# ---------------------------------------------------------------------------
# The types contract (TRDD-29QP66H2 batch 2: C6 declarations, C7 exports)
#
# The manifest `types` names a .d.ts contract. Native 2.1.332 reads it token by
# token, never as TypeScript (probe t-export-multiline: `export` and `type` on
# two lines pass). Same contract as the module scan: pure, never executes,
# never opens another file, and anything unread decides nothing.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TypesContract:
    """What a types contract holds. exports: (line, the token after it) for every top-level `export`
    not followed by `type` or `interface` (C7). state: plugin -> the keys `interface PluginState`
    declares for it, or None when an entry could not be read. state_known: False when a PluginState
    lies outside `declare module "claude-code"`, extends another type, or the file did not lex; C6
    then decides nothing."""

    exports: tuple[tuple[int, str], ...]
    state: tuple[tuple[str, frozenset[str] | None], ...]
    state_known: bool


def _type_members(t: list[Tok], match: dict[int, int], o: int) -> list[tuple[str | None, int, int]] | None:
    """Members of the object type `{ ... }` at token o, as (key, value start, value end). An index
    signature `[k: T]: V` gives key None (native counts it as no key: probe s-index-sig). None when a
    member is not a plain `[readonly] key[?]: T` or method `key(...)` (a mapped or computed key, an
    accessor, an escaped string key, a line the reader cannot place)."""
    close = match.get(o)
    if close is None:
        return None
    d = t[o].depth + 1

    def member_head(i: int) -> tuple[str | None, int] | None:
        """(key, index after the key and its `?`) when a member starts at i."""
        if t[i].kind == "punct" and t[i].value == "[":
            end = match.get(i)
            if end is None or not (i + 2 < close and t[i + 1].kind == "ident" and t[i + 2].value == ":"):
                return None
            return (None, end + 1) if t[end + 1].value == ":" else None
        if t[i].kind == "ident" and t[i].value == "readonly" and t[i + 1].kind in ("ident", "str"):
            i += 1
        key = t[i]
        if key.kind not in ("ident", "str", "num") or key.value.startswith(_ESCAPED_STRING_MARK):
            return None
        k = i + 1
        if t[k].kind == "punct" and t[k].value == "?":
            k += 1
        if t[k].kind == "punct" and t[k].value in (":", "(", "<"):
            return key.value, k
        return None

    out: list[tuple[str | None, int, int]] = []
    i = o + 1
    while i < close:
        if t[i].kind == "punct" and t[i].value in (";", ","):
            i += 1
            continue
        head = member_head(i)
        if head is None:
            return None
        key, k = head
        start = k + 1 if t[k].value == ":" else k
        m = start
        while m < close:
            tk = t[m]
            if tk.depth == d and tk.kind == "punct" and tk.value in (";", ","):
                break
            if tk.depth == d and tk.nl and m > start:
                if tk.kind in ("ident", "str") or (tk.kind == "punct" and tk.value == "["):
                    if member_head(m) is not None:
                        break
                    if tk.kind != "punct" and not (t[m - 1].kind == "punct" and t[m - 1].value in ("|", "&", "=>", ":", "?", "<", ",", "=")):
                        return None  # a new line the reader cannot place: decide nothing
            if tk.kind == "punct" and tk.value in _OPENERS:
                end = match.get(m)
                if end is None:
                    return None
                m = end + 1
                continue
            m += 1
        out.append((key, start, m))
        i = m
    return out


def scan_types_contract(source: str) -> TypesContract:
    """Read a mod's types contract (.d.ts) lexically: the C7 exports and the C6 PluginState keys."""
    try:
        lexer = _Lexer(_strip_prelude(source), False, max_depth=_BODY_MAX_BRACKET_DEPTH)
        t = lexer.run(in_template=False)
    except (_Undecided, RecursionError):
        return TypesContract((), (), False)
    match = lexer.match
    p = _Parser(t, match, Counter(), True)
    exports: list[tuple[int, str]] = []
    state: dict[str, frozenset[str] | None] = {}
    known = True
    for j, tk in enumerate(t):
        if tk.kind != "ident" or p.is_p(j - 1, "."):
            continue
        if tk.value == "export" and tk.depth == 0:
            nxt = p.tok(j + 1)
            if nxt is None or not (nxt.kind == "ident" and nxt.value in ("type", "interface")):
                # C7, native (p12, p15, probes t-*): `export` at the top level is followed by `type` or `interface`
                exports.append((tk.line, "" if nxt is None else nxt.value))
        if tk.value != "interface" or not p.is_id(j + 1, "PluginState"):
            continue
        holder = None
        for i in range(j - 1, -1, -1):
            if t[i].depth == tk.depth - 1 and t[i].kind == "punct" and t[i].value == "{":
                holder = i
                break
        in_module = (
            holder is not None
            and holder >= 3
            and p.is_id(holder - 3, "declare")
            and p.is_id(holder - 2, "module")
            and t[holder - 1].kind == "str"
            and t[holder - 1].value == "claude-code"
            and t[holder].depth == 0
        )
        members = _type_members(t, match, j + 2) if in_module and p.is_p(j + 2, "{") else None
        if members is None:
            known = False
            continue
        for plugin, s, e in members:
            if plugin is None:
                continue
            keys = None
            if t[s].kind == "punct" and t[s].value == "{" and match.get(s) == e - 1:
                inner = _type_members(t, match, s)
                keys = None if inner is None else frozenset(k for k, _, _ in inner if k is not None)
            old = state.get(plugin, frozenset())
            state[plugin] = None if keys is None or old is None else old | keys
    return TypesContract(tuple(exports), tuple(sorted(state.items())), known)


def undeclared_state_refs(refs: tuple[StateRef, ...], plugin_name: str, contract_source: str | None) -> list[StateRef] | None:
    """C6, native (p11b, p12d, probes s-*): the references of this plugin (other plugins' are never
    checked, probe s-other-plugin) whose key its types contract does not declare, one per plugin.key.
    contract_source None means the manifest names no `types`: every reference is undeclared (s-atom-no-types).
    None when the contract cannot be read (C6 then decides nothing); a plugin entry the reader could
    not parse decides nothing for its own references."""
    own = [r for r in refs if r.plugin == plugin_name]
    if not own:
        return []
    declared: dict[str, frozenset[str] | None] = {}
    if contract_source is not None:
        contract = scan_types_contract(contract_source)
        if not contract.state_known:
            return None
        declared = dict(contract.state)
    out: list[StateRef] = []
    seen: set[str] = set()
    for ref in own:
        keys = declared.get(ref.plugin, frozenset())
        if keys is not None and ref.key not in keys and ref.key not in seen:
            seen.add(ref.key)
            out.append(ref)
    return out
