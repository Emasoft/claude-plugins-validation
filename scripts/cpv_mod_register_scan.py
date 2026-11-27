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

    def __init__(self, src: str, jsx: bool) -> None:
        self.s = src
        self.n = len(src)
        self.i = 0
        self.line = 1
        self.jsx = jsx
        self.idents: Counter[str] = Counter()
        self.match: dict[int, int] = {}
        self.outer_depth = 0

    def run(self, in_template: bool) -> list[Tok]:
        """Lex until EOF (outer level) or the `}` closing a template `${`."""
        s, n = self.s, self.n
        toks: list[Tok] = []
        stack: list[tuple[str, int]] = []
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
                if self.outer_depth + len(stack) >= _MAX_DEPTH:
                    raise _Undecided(f"more than {_MAX_DEPTH} nested scopes")
                emit("punct", c, self.line)
                stack.append((c, len(toks) - 1))
            elif c in _CLOSERS:
                if not stack or _OPENERS[stack[-1][0]] != c:
                    raise _Undecided(f"unbalanced '{c}' at line {self.line}")
                emit("punct", c, self.line)
                _, open_index = stack.pop()
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


def scan_register_export(source: str, suffix: str) -> RegisterVerdict:
    """Decide statically whether a hooks module source exports `register`
    the way `claude plugin validate` 2.1.332 accepts. suffix selects TS
    erasure (.ts .tsx .mts .cts) and JSX lexing (.jsx .tsx)."""
    ts = suffix in TS_SUFFIXES
    jsx = suffix in JSX_SUFFIXES
    src = source[1:] if source.startswith(_BOM) else source
    if src.startswith("#!"):
        newline = src.find("\n")
        src = "" if newline < 0 else " " * newline + src[newline:]
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
