#!/usr/bin/env python3
"""AST literal-vs-assembled write-sink census (TRDD-ETDWX70R prerequisite).

A MEASUREMENT + CLASSIFICATION library, not a finding emitter. For every
file-WRITE destination it finds, it classifies the destination expression as
``LITERAL`` (every component is a string constant), ``ENV_ANCHORED`` (rooted
in ``CLAUDE_PLUGIN_ROOT``/``CLAUDE_PLUGIN_DATA``), ``FILE_ANCHORED`` (rooted in
``__file__``/``sys.argv[0]``/etc.), or ``ASSEMBLED_UNKNOWN`` (none of the
above — a fully computed path). This is the instrument the TRDD's 2026-08-29
decision names as the prerequisite for measuring the strict-flip false-positive
risk: it turns "how many writes would the strict flip newly block or reject"
from a guess into a number.

Stdlib only (this repo forbids third-party imports in scanner modules).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, NamedTuple

# Reuse the RC-164 write-pattern tables so the shell/script classification
# vocabulary cannot drift between the two modules. (No import cycle: the guard
# imports THIS module's collector lazily, inside its scan function.)
from cpv_inplugin_write_guard import (
    _COPY_PRIMITIVE_PATTERNS,
    _HEREDOC_REDIRECT_PATTERNS,
    _SCRIPT_EXTENSIONS,
    _SHELL_WRITE_PATTERNS,
    _body_starts_with_shebang,
)

_MAX_RESOLVE_DEPTH: Final[int] = 12

_ENV_ANCHOR_KEYS: Final[frozenset[str]] = frozenset(
    {"CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"}
)
_ENV_STR_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\$\{?(CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA)\}?"
)
_DATA_HOME_LITERAL: Final[str] = "~/.claude/plugins/data/"

_FILE_ANCHOR_NAMES: Final[frozenset[str]] = frozenset({"__file__"})


@dataclass(frozen=True)
class WriteSink:
    """One classified file-write destination."""

    line: int
    sink: str
    dest_text: str
    dest_class: str  # LITERAL | ENV_ANCHORED | FILE_ANCHORED | ASSEMBLED_UNKNOWN
    anchors: tuple[str, ...]
    unknown_leaf: bool
    parent_hops: int
    is_script_dest: bool | None
    copy_idiom: bool


# ────────────────────────────────────────────────────────────────────────
# Component resolution — walk an AST expr into a list of components, each
# either a literal string, an "anchor" token, or None (unknown).
# ────────────────────────────────────────────────────────────────────────


class _Component:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: str | None = None) -> None:
        # kind: "literal" | "env_anchor" | "file_anchor" | "unknown"
        self.kind = kind
        self.value = value


def _is_env_subscript_or_getenv(node: ast.AST) -> str | None:
    """Return the env-var NAME if ``node`` reads one of the two anchor vars."""
    # os.environ["X"] / os.environ.get("X", ...)
    if isinstance(node, ast.Subscript):
        val = node.value
        if _is_os_environ(val):
            key = _const_str(node.slice)
            if key in _ENV_ANCHOR_KEYS:
                return key
        return None
    if isinstance(node, ast.Call):
        func = node.func
        # os.environ.get("X", ...) / os.getenv("X", ...)
        if isinstance(func, ast.Attribute) and func.attr == "get" and _is_os_environ(func.value):
            if node.args:
                key = _const_str(node.args[0])
                if key in _ENV_ANCHOR_KEYS:
                    return key
        if isinstance(func, ast.Attribute) and func.attr == "getenv":
            if node.args:
                key = _const_str(node.args[0])
                if key in _ENV_ANCHOR_KEYS:
                    return key
        if isinstance(func, ast.Name) and func.id == "getenv":
            if node.args:
                key = _const_str(node.args[0])
                if key in _ENV_ANCHOR_KEYS:
                    return key
    return None


def _is_os_environ(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os"


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_file_anchor_expr(node: ast.AST) -> bool:
    """True iff ``node`` (possibly wrapped) is a __file__-family anchor."""
    n = node
    while True:
        if isinstance(n, ast.Name) and n.id in _FILE_ANCHOR_NAMES:
            return True
        if isinstance(n, ast.Attribute) and n.attr in (
            "resolve",
            "absolute",
            "expanduser",
        ):
            n = n.value
            continue
        if isinstance(n, ast.Call):
            func = n.func
            if isinstance(func, ast.Attribute) and func.attr in (
                "resolve",
                "absolute",
                "expanduser",
            ):
                n = func.value
                continue
            if isinstance(func, ast.Name) and func.id in ("Path", "str", "PurePath"):
                if n.args:
                    n = n.args[0]
                    continue
                return False
            if isinstance(func, ast.Attribute) and func.attr == "getfile":
                # inspect.getfile(...)
                return isinstance(func.value, ast.Name) and func.value.id == "inspect"
            if isinstance(func, ast.Attribute) and func.attr == "abspath":
                if isinstance(func.value, ast.Name) and func.value.id == "os" and n.args:
                    n = n.args[0]
                    continue
            return False
        return False


def _sys_argv0(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "argv"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "sys"
        and _const_int(node.slice) == 0
    )


def _const_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _resolve_expr(
    node: ast.AST,
    assigns: dict[str, ast.AST],
    depth: int,
    visited: set[int],
) -> list[_Component]:
    """Resolve ``node`` into an ordered list of components.

    ``depth`` bounds recursion; ``visited`` (id() set) prevents a Name-cycle.
    """
    if depth > _MAX_RESOLVE_DEPTH:
        return [_Component("unknown")]

    # Constants
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
        if _ENV_STR_TOKEN_RE.search(text) or _DATA_HOME_LITERAL in text:
            key_match = _ENV_STR_TOKEN_RE.search(text)
            return [_Component("env_anchor", key_match.group(1) if key_match else "PLUGIN_DATA_HOME")]
        return [_Component("literal", text)]

    # A ``.parent`` attribute hop (at ANY nesting depth) — resolve the base
    # and prepend a hop marker so the anchor propagates through it.
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        inner = _resolve_expr(node.value, assigns, depth + 1, visited)
        return [_Component("dirname_hop")] + inner

    # __file__ / sys.argv[0] / inspect.getfile(...) anchors
    if _is_file_anchor_expr(node):
        return [_Component("file_anchor", "__file__")]
    if _sys_argv0(node):
        return [_Component("file_anchor", "sys.argv[0]")]

    # os.environ[...] / os.getenv(...) as the WHOLE expr (env anchor or unknown)
    env_key = _is_env_subscript_or_getenv(node)
    if env_key is not None:
        return [_Component("env_anchor", env_key)]
    # An os.environ/getenv read of a DIFFERENT key is an unknown component
    if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
        return [_Component("unknown")]
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("get", "getenv") and (
            _is_os_environ(func.value)
        ):
            return [_Component("unknown")]
        if isinstance(func, ast.Name) and func.id == "getenv":
            return [_Component("unknown")]

    # Name resolution via tracked assignments
    if isinstance(node, ast.Name):
        if id(node) in visited:
            return [_Component("unknown")]
        target = assigns.get(node.id)
        if target is None:
            return [_Component("unknown")]
        visited = visited | {id(node)}
        return _resolve_expr(target, assigns, depth + 1, visited)

    # BinOp(+) on strings
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _resolve_expr(node.left, assigns, depth + 1, visited) + _resolve_expr(
            node.right, assigns, depth + 1, visited
        )

    # Path(...) / str(...) / os.fspath(...) — transparent wrapper
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("Path", "str", "PurePath", "os"):
            if node.args:
                return _resolve_expr(node.args[0], assigns, depth + 1, visited)
            return [_Component("unknown")]
        if isinstance(func, ast.Attribute) and func.attr == "fspath" and (
            isinstance(func.value, ast.Name) and func.value.id == "os"
        ):
            if node.args:
                return _resolve_expr(node.args[0], assigns, depth + 1, visited)
            return [_Component("unknown")]
        # .resolve()/.absolute()/.expanduser() transparent (handled by file-anchor
        # check above for __file__ chains; here for a non-file base too)
        if isinstance(func, ast.Attribute) and func.attr in (
            "resolve",
            "absolute",
            "expanduser",
        ):
            return _resolve_expr(func.value, assigns, depth + 1, visited)
        # os.path.join(...)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "join"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "path"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "os"
        ):
            out: list[_Component] = []
            for a in node.args:
                out.extend(_resolve_expr(a, assigns, depth + 1, visited))
            return out or [_Component("unknown")]
        # os.path.dirname(X) — a parent-hop wrapper; treat as anchor-transparent
        # + a synthetic hop tracked by the caller via string "..DIRNAME.." marker.
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "dirname"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "path"
        ):
            if node.args:
                inner = _resolve_expr(node.args[0], assigns, depth + 1, visited)
                return [_Component("dirname_hop")] + inner
            return [_Component("unknown")]
        # <expr>.joinpath(...) / <PathExpr> / x
        if isinstance(func, ast.Attribute) and func.attr == "joinpath":
            out = _resolve_expr(func.value, assigns, depth + 1, visited)
            for a in node.args:
                out.extend(_resolve_expr(a, assigns, depth + 1, visited))
            return out
        if isinstance(func, ast.Attribute) and func.attr in (
            "with_suffix",
            "with_name",
            "with_stem",
        ):
            out = _resolve_expr(func.value, assigns, depth + 1, visited)
            if node.args:
                out = out + _resolve_expr(node.args[0], assigns, depth + 1, visited)
            return out
        # str % (...) formatting or "{}".format(...)
        if isinstance(func, ast.Attribute) and func.attr == "format":
            out = _resolve_expr(func.value, assigns, depth + 1, visited)
            for a in node.args:
                out.extend(_resolve_expr(a, assigns, depth + 1, visited))
            return out
        return [_Component("unknown")]

    # PurePath/Path(...) / x  (BinOp Div)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _resolve_expr(node.left, assigns, depth + 1, visited) + _resolve_expr(
            node.right, assigns, depth + 1, visited
        )

    # % formatting
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        out = _resolve_expr(node.left, assigns, depth + 1, visited)
        rhs = node.right
        if isinstance(rhs, ast.Tuple):
            for e in rhs.elts:
                out.extend(_resolve_expr(e, assigns, depth + 1, visited))
        else:
            out.extend(_resolve_expr(rhs, assigns, depth + 1, visited))
        return out

    # f-string
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.append(_Component("literal", v.value))
            elif isinstance(v, ast.FormattedValue):
                out.extend(_resolve_expr(v.value, assigns, depth + 1, visited))
        return out or [_Component("unknown")]

    if isinstance(node, ast.Attribute) and node.attr in (
        "resolve",
        "absolute",
        "expanduser",
    ):
        return _resolve_expr(node.value, assigns, depth + 1, visited)

    return [_Component("unknown")]


def _classify_components(components: list[_Component]) -> tuple[str, tuple[str, ...], bool, int, str | None]:
    """Return (dest_class, anchors, unknown_leaf, parent_hops, leaf_literal)."""
    anchors: list[str] = []
    has_env = False
    has_file = False
    has_unknown = False
    parent_hops = 0
    leaf_literal: str | None = None
    for c in components:
        if c.kind == "env_anchor":
            has_env = True
            if c.value:
                anchors.append(c.value)
        elif c.kind == "file_anchor":
            has_file = True
            anchors.append(c.value or "__file__")
        elif c.kind == "literal":
            leaf_literal = c.value
        elif c.kind == "dirname_hop":
            parent_hops += 1
        elif c.kind == "unknown":
            has_unknown = True

    if has_env:
        dest_class = "ENV_ANCHORED"
    elif has_file:
        dest_class = "FILE_ANCHORED"
    elif not has_unknown and components:
        dest_class = "LITERAL"
    else:
        dest_class = "ASSEMBLED_UNKNOWN"

    unknown_leaf = (dest_class in ("ENV_ANCHORED", "FILE_ANCHORED")) and has_unknown
    return dest_class, tuple(anchors), unknown_leaf, parent_hops, leaf_literal


def _leaf_suffix(text: str) -> str:
    stripped = text.strip().strip("'\"")
    return Path(stripped).suffix.lower()


def _classify_dest_expr(node: ast.AST, assigns: dict[str, ast.AST]) -> WriteSink | None:
    """Classify a destination AST expr; caller fills in line/sink/dest_text."""
    # ``.parent`` hops at ANY nesting depth are stripped+counted inside
    # ``_resolve_expr`` itself, so the raw ``node`` is resolved directly.
    components = _resolve_expr(node, assigns, 0, set())
    dest_class, anchors, unknown_leaf, parent_hops, leaf_literal = _classify_components(components)

    is_script_dest: bool | None
    if leaf_literal is not None:
        is_script_dest = _leaf_suffix(leaf_literal) in _SCRIPT_EXTENSIONS
    else:
        is_script_dest = None

    return WriteSink(
        line=0,
        sink="",
        dest_text="",
        dest_class=dest_class,
        anchors=anchors,
        unknown_leaf=unknown_leaf,
        parent_hops=parent_hops,
        is_script_dest=is_script_dest,
        copy_idiom=False,
    )


# ────────────────────────────────────────────────────────────────────────
# AST walk — find write-sink Call/Attribute nodes
# ────────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────
# Scoped binding model (TRDD-ETDWX70R)
#
# The first census instrument used ONE flat name→RHS map over the whole tree,
# which leaked bindings across function scopes and let a function PARAMETER
# resolve to an unrelated module value (a pytest fixture param ``root`` resolved
# to a ``__file__``-derived module binding, turning a ``tmp_path`` write into a
# false FILE_ANCHORED hit). The model below is scoped:
#
#   * a function's PARAMETERS (args / posonly / kwonly / vararg / kwarg) and its
#     comprehension / ``for`` / ``with`` / ``except`` / unpacking / import-as
#     targets SHADOW any outer binding as UNKNOWN — always, even when a
#     global-promoted binding of the same name exists;
#   * bindings are PER FUNCTION — a local in ``g`` never reaches ``f``;
#   * ``global NAME`` promotes that function's assignments to NAME into MODULE
#     scope, recorded at the promoting function's ``def`` line; ``nonlocal``
#     binds in the enclosing function;
#   * an outer (module / global-promoted) binding is visible only when its
#     defining statement lies BEFORE the writer's own function ``def``;
#   * ``AugAssign`` poisons the name (no single RHS) → UNKNOWN, in the render
#     path AND the copy path, so ``s = src.read_bytes(); s += b"x"`` is not a
#     verbatim copy.
# ────────────────────────────────────────────────────────────────────────

# A binding entry: (lineno, name, rhs-or-None). ``None`` poisons the name.
_Binding = tuple[int, str, "ast.AST | None"]


class _FuncScope:
    __slots__ = ("node", "parent", "params", "shadowed", "locals", "globals_", "nonlocals_")

    def __init__(self, node: ast.AST, parent: "_FuncScope | None") -> None:
        self.node = node
        self.parent = parent
        self.params: set[str] = set()
        self.shadowed: set[str] = set()
        self.locals: list[_Binding] = []
        self.globals_: set[str] = set()
        self.nonlocals_: set[str] = set()


_FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _declared_scope_names(body: list[ast.stmt]) -> tuple[set[str], set[str]]:
    """``global`` / ``nonlocal`` names declared directly in ``body`` (not in a
    nested function, which owns its own declarations)."""
    g: set[str] = set()
    nl: set[str] = set()

    def walk(stmts: list[ast.stmt]) -> None:
        for st in stmts:
            if isinstance(st, _FUNC_NODES):
                continue
            if isinstance(st, ast.Global):
                g.update(st.names)
            elif isinstance(st, ast.Nonlocal):
                nl.update(st.names)
            for child in ast.iter_child_nodes(st):
                if isinstance(child, ast.stmt) and not isinstance(child, _FUNC_NODES):
                    walk([child])
                elif isinstance(child, ast.Global):
                    g.update(child.names)
                elif isinstance(child, ast.Nonlocal):
                    nl.update(child.names)

    walk(body)
    return g, nl


def _shadow_targets(target: ast.AST, out: set[str]) -> None:
    """Collect every Name bound by an assignment/loop/with/except TARGET that we
    cannot resolve to a single RHS (tuple/list unpacking, starred, attribute or
    subscript targets contribute nothing)."""
    if isinstance(target, ast.Name):
        out.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for e in target.elts:
            _shadow_targets(e, out)
    elif isinstance(target, ast.Starred):
        _shadow_targets(target.value, out)


class _ScopeIndex:
    """Per-node scope + binding tables for one parsed module."""

    def __init__(self, tree: ast.AST) -> None:
        self.module: list[_Binding] = []
        self.scope_by_id: dict[int, _FuncScope | None] = {}
        self._visit(tree, None)
        self.module.sort(key=lambda b: b[0])

    # ── construction ────────────────────────────────────────────────
    def _record(self, scope: _FuncScope | None, lineno: int, name: str, rhs: ast.AST | None) -> None:
        if scope is None:
            self.module.append((lineno, name, rhs))
            return
        if name in scope.globals_:
            # A ``global NAME`` assignment is a MODULE binding, and its
            # "defining statement" for the ordering rule is the promoting
            # function's ``def`` (a caller can only see it once that def exists).
            self.module.append((getattr(scope.node, "lineno", lineno), name, rhs))
            return
        if name in scope.nonlocals_ and scope.parent is not None:
            scope.parent.locals.append((lineno, name, rhs))
            return
        scope.locals.append((lineno, name, rhs))

    def _visit(self, node: ast.AST, scope: _FuncScope | None) -> None:
        self.scope_by_id[id(node)] = scope

        if isinstance(node, _FUNC_NODES):
            inner = _FuncScope(node, scope)
            a = node.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs]:
                inner.params.add(arg.arg)
            if a.vararg is not None:
                inner.params.add(a.vararg.arg)
            if a.kwarg is not None:
                inner.params.add(a.kwarg.arg)
            # Decorators + defaults are evaluated in the ENCLOSING scope.
            for d in getattr(node, "decorator_list", []):
                self._visit(d, scope)
            for d in [*a.defaults, *[k for k in a.kw_defaults if k is not None]]:
                self._visit(d, scope)
            if isinstance(node, ast.Lambda):
                # A lambda body is a single EXPRESSION; its params still shadow.
                self._visit(node.body, inner)
                return
            inner.globals_, inner.nonlocals_ = _declared_scope_names(node.body)
            for st in node.body:
                self._visit(st, inner)
            return

        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._record(scope, node.lineno, target.id, node.value)
                else:
                    names: set[str] = set()
                    _shadow_targets(target, names)
                    for n in names:
                        self._record(scope, node.lineno, n, None)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                self._record(scope, node.lineno, node.target.id, node.value)
        elif isinstance(node, ast.AugAssign):
            # No single RHS — poison the name in BOTH the render and copy paths.
            if isinstance(node.target, ast.Name):
                self._record(scope, node.lineno, node.target.id, None)
        elif isinstance(node, ast.NamedExpr):
            if isinstance(node.target, ast.Name):
                self._record(scope, node.lineno, node.target.id, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            names = set()
            _shadow_targets(node.target, names)
            self._mark_shadow(scope, names, getattr(node, "lineno", 0))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            names = set()
            for item in node.items:
                if item.optional_vars is not None:
                    _shadow_targets(item.optional_vars, names)
            self._mark_shadow(scope, names, node.lineno)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                self._mark_shadow(scope, {node.name}, node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {(al.asname or al.name.split(".")[0]) for al in node.names}
            self._mark_shadow(scope, names, node.lineno)

        for child in ast.iter_child_nodes(node):
            self._visit(child, scope)

    def _mark_shadow(self, scope: _FuncScope | None, names: set[str], lineno: int) -> None:
        if scope is None:
            for n in names:
                self.module.append((lineno, n, None))
            return
        scope.shadowed.update(names)

    # ── resolution ──────────────────────────────────────────────────
    def bindings_for(self, node: ast.AST) -> dict[str, ast.AST]:
        """The name→RHS map visible at ``node`` under the rules above."""
        scope = self.scope_by_id.get(id(node))
        chain: list[_FuncScope] = []
        s = scope
        while s is not None:
            chain.append(s)
            s = s.parent
        own_anchor = getattr(node, "lineno", 0) or 0
        outer_anchor = getattr(chain[0].node, "lineno", own_anchor) if chain else own_anchor

        out: dict[str, ast.AST] = {}

        def apply(entries: list[_Binding], anchor: int) -> None:
            for ln, name, rhs in entries:
                if ln > anchor:
                    continue
                if rhs is None:
                    out.pop(name, None)
                else:
                    out[name] = rhs

        apply(self.module, outer_anchor if chain else own_anchor)
        for sc in reversed(chain):
            apply(sc.locals, own_anchor if sc is chain[0] else outer_anchor)
            for n in sc.params | sc.shadowed:
                out.pop(n, None)
        return out


def _mode_is_write(mode_arg: ast.AST | None) -> bool | None:
    """True/False/None (unknown -> treat as write) for an ``open`` mode arg."""
    if mode_arg is None:
        return False  # default mode 'r' -> read
    text = _const_str(mode_arg)
    if text is None:
        return True  # non-literal mode -> treat as write (conservative)
    return any(c in text for c in "wax+")


def _flags_is_write(flags_text: str | None) -> bool:
    if flags_text is None:
        return True
    return any(tok in flags_text for tok in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"))


def _call_line(node: ast.Call) -> int:
    """The line carrying the CALL itself.

    For ``x.write_text(...)`` that is the line holding ``.write_text(`` — i.e.
    the attribute's END line, not the start of the (possibly multi-line)
    receiver expression, and never the enclosing statement's line. The first
    census instrument reported the enclosing statement and mis-attributed a
    multi-line ``write_text`` to the next statement's line.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return getattr(func, "end_lineno", None) or getattr(func, "lineno", 0) or 0
    return getattr(func, "lineno", None) or getattr(node, "lineno", 0) or 0


def classify_python_write_sinks(source: str) -> list[WriteSink]:
    """Classify every file-write destination in ``source`` (a Python file)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return []

    index = _ScopeIndex(tree)
    sinks: list[WriteSink] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        line = _call_line(node)
        assigns = index.bindings_for(node)

        # open(dst, mode) / io.open(dst, mode)
        is_open = (isinstance(func, ast.Name) and func.id == "open") or (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and isinstance(func.value, ast.Name)
            and func.value.id == "io"
        )
        if is_open and node.args:
            dst_node = node.args[0]
            mode_node = None
            if len(node.args) > 1:
                mode_node = node.args[1]
            else:
                for kw in node.keywords:
                    if kw.arg == "mode":
                        mode_node = kw.value
            if _mode_is_write(mode_node):
                sinks.append(_finish_sink(dst_node, assigns, line, "open"))
            continue

        # Path(...).write_text/write_bytes(...) or <expr>.write_text/write_bytes(...)
        if isinstance(func, ast.Attribute) and func.attr in ("write_text", "write_bytes"):
            sinks.append(_finish_sink(func.value, assigns, line, func.attr))
            continue

        # os.open(dst, flags)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
        ):
            if node.args:
                flags_text = ast.unparse(node.args[1]) if len(node.args) > 1 else None
                if _flags_is_write(flags_text):
                    sinks.append(_finish_sink(node.args[0], assigns, line, "os.open"))
            continue

        # shutil.copy/copy2/copyfile/copytree/move(src, dst)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "shutil":
            if func.attr in ("copy", "copy2", "copyfile", "copytree", "move") and len(node.args) >= 2:
                sink = _finish_sink(node.args[1], assigns, line, f"shutil.{func.attr}")
                sinks.append(WriteSink(**{**asdict(sink), "copy_idiom": True}))
            continue

        # os.rename/os.replace(src, dst)
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
            and func.attr in ("rename", "replace")
            and len(node.args) >= 2
        ):
            sink = _finish_sink(node.args[1], assigns, line, f"os.{func.attr}")
            sinks.append(WriteSink(**{**asdict(sink), "copy_idiom": True}))
            continue

    return sinks


def _finish_sink(dst_node: ast.AST, assigns: dict[str, ast.AST], line: int, sink_name: str) -> WriteSink:
    classified = _classify_dest_expr(dst_node, assigns)
    assert classified is not None
    try:
        dest_text = ast.unparse(dst_node)
    except Exception:
        dest_text = "<unparse-error>"
    return WriteSink(
        line=line,
        sink=sink_name,
        dest_text=dest_text,
        dest_class=classified.dest_class,
        anchors=classified.anchors,
        unknown_leaf=classified.unknown_leaf,
        parent_hops=classified.parent_hops,
        is_script_dest=classified.is_script_dest,
        copy_idiom=False,
    )


# ────────────────────────────────────────────────────────────────────────
# Shell (regex, per-line) classification
# ────────────────────────────────────────────────────────────────────────

_SHELL_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"^\s*#")
_FD_REDIRECT_SUPPRESS_RE: Final[re.Pattern[str]] = re.compile(r"^(?:[12]?>&[12]|/dev/null|/dev/.*)$")

_BASH_SOURCE_ANCHOR_RE: Final[re.Pattern[str]] = re.compile(
    r'\$\(\s*dirname\s+"?\$0"?\s*\)|\$\{?BASH_SOURCE(?:\[0\])?\}?|\$0\b'
)


def _shell_dest_class(token: str, file_anchor_vars: set[str], env_anchor_vars: set[str]) -> tuple[str, tuple[str, ...]]:
    has_env = "$CLAUDE_PLUGIN_ROOT" in token or "${CLAUDE_PLUGIN_ROOT}" in token or "$CLAUDE_PLUGIN_DATA" in token or "${CLAUDE_PLUGIN_DATA}" in token or _DATA_HOME_LITERAL in token
    if not has_env:
        for v in env_anchor_vars:
            if f"${v}" in token or f"${{{v}}}" in token:
                has_env = True
                break
    if has_env:
        return "ENV_ANCHORED", ("CLAUDE_PLUGIN_ROOT",)

    has_file = bool(_BASH_SOURCE_ANCHOR_RE.search(token))
    if not has_file:
        for v in file_anchor_vars:
            if f"${v}" in token or f"${{{v}}}" in token:
                has_file = True
                break
    if has_file:
        return "FILE_ANCHORED", ("BASH_SOURCE",)

    if "$" in token or "`" in token or "~" in token:
        return "ASSEMBLED_UNKNOWN", ()

    return "LITERAL", ()


_SHELL_CP_INSTALL_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|[\s;&|])(?:cp|install)\s+(.+)$")

_SCRIPT_DIR_ASSIGN_RE: Final[re.Pattern[str]] = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?\$\(.*(?:dirname\s+"?\$0"?|dirname\s+"?\$\{?BASH_SOURCE|BASH_SOURCE).*\)"?'
)
_ENV_VAR_ASSIGN_RE: Final[re.Pattern[str]] = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?\$\{?(CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA)\}?'
)


def classify_shell_write_sinks(source: str) -> list[WriteSink]:
    """Classify every file-write destination in ``source`` (a shell script)."""
    lines = source.splitlines()
    file_anchor_vars: set[str] = set()
    env_anchor_vars: set[str] = set()
    sinks: list[WriteSink] = []

    for idx, raw_line in enumerate(lines, start=1):
        if _SHELL_COMMENT_RE.match(raw_line):
            continue

        m = _SCRIPT_DIR_ASSIGN_RE.match(raw_line)
        if m:
            file_anchor_vars.add(m.group(1))
        m2 = _ENV_VAR_ASSIGN_RE.match(raw_line)
        if m2:
            env_anchor_vars.add(m2.group(1))

        copy_idiom = any(p.search(raw_line) for p in _COPY_PRIMITIVE_PATTERNS)

        cp_match = _SHELL_CP_INSTALL_RE.search(raw_line)
        if cp_match:
            tokens = [t for t in cp_match.group(1).split() if not t.startswith("-")]
            if tokens:
                dest_tok = tokens[-1]
                dest_class, anchors = _shell_dest_class(dest_tok, file_anchor_vars, env_anchor_vars)
                sinks.append(
                    WriteSink(
                        line=idx,
                        sink="shell.copy",
                        dest_text=dest_tok,
                        dest_class=dest_class,
                        anchors=anchors,
                        unknown_leaf=False,
                        parent_hops=dest_tok.count("/.."),
                        is_script_dest=None,
                        copy_idiom=True,
                    )
                )

        for pat, sink_name in (
            (_SHELL_WRITE_PATTERNS[0], "shell.redirect"),
            (_SHELL_WRITE_PATTERNS[1], "shell.tee"),
            (_SHELL_WRITE_PATTERNS[2], "shell.sed_i"),
        ):
            for match in pat.finditer(raw_line):
                token = match.group(1)
                if _FD_REDIRECT_SUPPRESS_RE.match(token):
                    continue
                dest_class, anchors = _shell_dest_class(token, file_anchor_vars, env_anchor_vars)
                parent_hops = token.count("/..") + len(re.findall(r"\bdirname\(", token))
                is_script_dest = None
                if dest_class == "LITERAL" or (dest_class != "ASSEMBLED_UNKNOWN"):
                    leaf = token.strip("'\"")
                    suffix = Path(leaf).suffix.lower()
                    if suffix:
                        is_script_dest = suffix in _SCRIPT_EXTENSIONS
                sinks.append(
                    WriteSink(
                        line=idx,
                        sink=sink_name,
                        dest_text=token,
                        dest_class=dest_class,
                        anchors=anchors,
                        unknown_leaf=False,
                        parent_hops=parent_hops,
                        is_script_dest=is_script_dest,
                        copy_idiom=copy_idiom,
                    )
                )

        for pat in _HEREDOC_REDIRECT_PATTERNS:
            for match in pat.finditer(raw_line):
                token = match.group(1)
                if _FD_REDIRECT_SUPPRESS_RE.match(token):
                    continue
                dest_class, anchors = _shell_dest_class(token, file_anchor_vars, env_anchor_vars)
                parent_hops = token.count("/..") + len(re.findall(r"\bdirname\(", token))
                is_script_dest = None
                leaf = token.strip("'\"")
                suffix = Path(leaf).suffix.lower()
                if suffix:
                    is_script_dest = suffix in _SCRIPT_EXTENSIONS
                sinks.append(
                    WriteSink(
                        line=idx,
                        sink="shell.heredoc",
                        dest_text=token,
                        dest_class=dest_class,
                        anchors=anchors,
                        unknown_leaf=False,
                        parent_hops=parent_hops,
                        is_script_dest=is_script_dest,
                        copy_idiom=copy_idiom,
                    )
                )

    return sinks


_PY_SUFFIXES: Final[frozenset[str]] = frozenset({".py", ".pyw"})
_SHELL_SUFFIXES: Final[frozenset[str]] = frozenset({".sh", ".bash", ".zsh", ".ksh"})


def classify_file(path: Path) -> list[WriteSink] | None:
    """Dispatch ``path`` to the python or shell classifier by suffix.

    Returns ``None`` when a ``.py`` file could not be parsed (so the census can
    report a real syntax-error count instead of a hardcoded zero)."""
    suffix = path.suffix.lower()
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if suffix in _PY_SUFFIXES:
        try:
            ast.parse(source)
        except (SyntaxError, ValueError, RecursionError):
            return None
        return classify_python_write_sinks(source)
    if suffix in _SHELL_SUFFIXES:
        return classify_shell_write_sinks(source)
    return []


# ────────────────────────────────────────────────────────────────────────
# RC-164 destination RENDERER (TRDD-ETDWX70R)
#
# The census classifier answers "which anchor family is this?"; the RC-164 fold
# needs the destination as a foldable path STRING plus the trailing LITERAL
# fragment the script-suffix gate reads. Rendering is deliberately total: any
# node we cannot model becomes a DISTINCT residual variable ``$__UNK_<n>``, so
# ``cpv_persistence_target._RESIDUAL_VAR_RE`` rejects it and the fold can never
# silently place an unmodelled expression inside the plugin tree.
#
# ``__file__`` renders as the plugin-root-RELATIVE ``self_path``; the fold
# resolves a bare relative path against the plugin root, so the result is
# exactly ``<plugin_root>/<self_path>``. There is NO ``${__SELF__}`` token —
# a token nobody folds would become an unresolved var and demote every
# ``__file__`` write.
# ────────────────────────────────────────────────────────────────────────

_UNK_PREFIX: Final[str] = "$__UNK_"
_UNK_RE: Final[re.Pattern[str]] = re.compile(r"\$__UNK_\d+")
_MAX_RENDER_DEPTH: Final[int] = 16
_PCT_CONV_RE: Final[re.Pattern[str]] = re.compile(r"%[-#0 +]*\d*(?:\.\d+)?[sdifgeExXorc]")
# ``"{}.py".format(stem)`` is the ``%``-format of str.format: the SHAPE lives in
# the template literal, not in the args. Joining template+args and taking the
# last ARG's tail loses the literal suffix (``.py`` → tail None → T2 instead of
# T1) and keeps a bogus ``{}`` in the rendered prefix (``{}/gen.py`` folds as a
# bare relative path → blocking T2 FP). TRDD-ETDWX70R advisor finding.
# ponytail: naive on ``{{``/``}}`` escapes and on nested specs like ``{x:{w}}`` —
# it may spend an extra unknown on the PREFIX, which only ever makes a fold less
# certain, never more. The TAIL (the half the suffix gate reads) stays correct
# because the real trailing field is still the last match. Parse properly with
# ``string.Formatter().parse`` if a prefix false-negative is ever measured.
_FMT_FIELD_RE: Final[re.Pattern[str]] = re.compile(r"\{[^{}]*\}")


class _R(NamedTuple):
    """A rendered expression: its path text + its TRAILING literal fragment."""

    text: str
    tail: str | None


class _Ctx:
    """Unknown-token counter — each unmodelled node gets a DISTINCT name so two
    unknowns are never accidentally equal."""

    __slots__ = ("n",)

    def __init__(self) -> None:
        self.n = 0

    def unk(self) -> str:
        token = f"{_UNK_PREFIX}{self.n}"
        self.n += 1
        return token


@dataclass(frozen=True)
class Rendered:
    """A destination expression rendered for the RC-164 fold.

    ``prefix`` is the text up to and including the LAST ``/`` (so the tail's own
    component is excluded); ``literal_tail`` is the trailing literal fragment of
    the last component (``".py"`` for ``name + ".py"``, ``"gen.py"`` for a whole
    literal) or ``None`` when the last component carries no literal fragment.
    """

    prefix: str
    literal_tail: str | None
    unknown: bool
    has_unknown_prefix: bool

    @property
    def foldable(self) -> str:
        """The path string handed to the fold: prefix + the literal tail (the
        unknown STEM of the last component is deliberately dropped, which is why
        moving the stem into a variable buys an attacker nothing)."""
        return self.prefix + (self.literal_tail or "")


@dataclass(frozen=True)
class AstWriteSink:
    """One write sink recovered from a parsed Python module."""

    line_no: int
    sink: str
    dest_text: str
    rendered: Rendered
    copy_idiom: bool
    script_evidence: str | None  # "shebang" | "chmod" | None


def _is_os_path(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _env_render(key: str | None, ctx: _Ctx) -> _R:
    if key is None:
        return _R(ctx.unk(), None)
    # HOME renders as the literal ``~`` (NOT ``$HOME``, which the fold rejects
    # as non-sandbox) so ``Path.home()/".claude/plugins/data/<slug>/x.py"``
    # folds through _PLUGIN_DATA_LITERAL_RE.
    if key == "HOME":
        return _R("~", None)
    return _R("${" + key + "}", None)


def _render(
    node: ast.AST,
    bindings: dict[str, ast.AST],
    self_path: str | None,
    ctx: _Ctx,
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
) -> _R:
    """Render ``node`` to (path text, trailing literal fragment)."""
    if depth > _MAX_RENDER_DEPTH:
        return _R(ctx.unk(), None)

    def sub(n: ast.AST) -> _R:
        return _render(n, bindings, self_path, ctx, depth + 1, seen)

    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, str):
            return _R(value, value)
        if isinstance(value, (bytes, bytearray)):
            text = bytes(value).decode("ascii", errors="replace")
            return _R(text, text)
        return _R(ctx.unk(), None)

    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return _R(self_path, self_path) if self_path else _R(ctx.unk(), None)
        if node.id in seen:
            return _R(ctx.unk(), None)
        rhs = bindings.get(node.id)
        if rhs is None:
            return _R(ctx.unk(), None)
        return _render(rhs, bindings, self_path, ctx, depth + 1, seen | {node.id})

    # ``sys.argv[0]`` is the ENTRY script, not this file → UNKNOWN.
    if _sys_argv0(node):
        return _R(ctx.unk(), None)

    if isinstance(node, ast.Subscript):
        if _is_os_environ(node.value):
            return _env_render(_const_str(node.slice), ctx)
        base_attr = node.value
        if isinstance(base_attr, ast.Attribute) and base_attr.attr == "parents":
            n = _const_int(node.slice)
            base = sub(base_attr.value)
            if n is not None and 0 <= n < 64:
                return _R(base.text + "/.." * (n + 1), None)
        return _R(ctx.unk(), None)

    if isinstance(node, ast.Attribute):
        if node.attr == "parent":
            return _R(sub(node.value).text + "/..", None)
        if node.attr in ("resolve", "absolute", "expanduser"):
            return sub(node.value)
        return _R(ctx.unk(), None)

    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(_R(v.value, v.value))
            elif isinstance(v, ast.FormattedValue):
                parts.append(sub(v.value))
            else:
                parts.append(_R(ctx.unk(), None))
        if not parts:
            return _R("", None)
        return _R("".join(p.text for p in parts), parts[-1].tail)

    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Div):
            left, right = sub(node.left), sub(node.right)
            return _R(left.text + "/" + right.text, right.tail)
        if isinstance(node.op, ast.Add):
            left, right = sub(node.left), sub(node.right)
            return _R(left.text + right.text, right.tail)
        if isinstance(node.op, ast.Mod):
            if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
                lit = node.left.value
                text = _PCT_CONV_RE.sub(lambda _: ctx.unk(), lit)
                matches = list(_PCT_CONV_RE.finditer(lit))
                tail = lit[matches[-1].end() :] if matches else lit
                return _R(text, tail or None)
            left, right = sub(node.left), sub(node.right)
            return _R(left.text + right.text, right.tail)
        return _R(ctx.unk(), None)

    if isinstance(node, ast.Call):
        func = node.func
        args = node.args
        if isinstance(func, ast.Name):
            if func.id in ("Path", "PurePath", "PosixPath", "WindowsPath", "str"):
                return sub(args[0]) if args else _R(ctx.unk(), None)
            if func.id == "getenv":
                return _env_render(_const_str(args[0]) if args else None, ctx)
            return _R(ctx.unk(), None)
        if isinstance(func, ast.Attribute):
            attr, recv = func.attr, func.value
            if attr == "get" and _is_os_environ(recv):
                return _env_render(_const_str(args[0]) if args else None, ctx)
            if attr == "getenv" and isinstance(recv, ast.Name) and recv.id == "os":
                return _env_render(_const_str(args[0]) if args else None, ctx)
            if attr == "home":
                return _R("~", None)
            if attr == "fspath" and isinstance(recv, ast.Name) and recv.id == "os":
                return sub(args[0]) if args else _R(ctx.unk(), None)
            if _is_os_path(recv):
                if attr in ("expanduser", "abspath", "normpath", "realpath"):
                    return sub(args[0]) if args else _R(ctx.unk(), None)
                if attr == "dirname":
                    return _R(sub(args[0]).text + "/..", None) if args else _R(ctx.unk(), None)
                if attr == "join":
                    parts = [sub(a) for a in args]
                    if not parts:
                        return _R(ctx.unk(), None)
                    return _R("/".join(p.text for p in parts), parts[-1].tail)
                return _R(ctx.unk(), None)
            if attr in ("resolve", "absolute", "expanduser", "as_posix"):
                return sub(recv)
            if attr == "joinpath":
                parts = [sub(recv)] + [sub(a) for a in args]
                return _R("/".join(p.text for p in parts), parts[-1].tail)
            if attr == "with_suffix" and args:
                base, suffix = sub(recv), sub(args[0])
                return _R(base.text + suffix.text, suffix.tail)
            if attr == "with_name" and args:
                base, name = sub(recv), sub(args[0])
                if "/" in base.text:
                    head = base.text.rsplit("/", 1)[0] + "/"
                elif _UNK_RE.search(base.text):
                    # A slash-less UNKNOWN receiver (a parameter, a computed
                    # Path) must keep its residual marker as the PREFIX —
                    # dropping it rendered ``param.with_name("gen.py")`` as the
                    # bare literal ``gen.py``, which folds to <root>/gen.py and
                    # fired a CRITICAL on a plain codegen idiom (advisor
                    # pre-commit finding, TRDD-ETDWX70R). Unknown base → T3.
                    head = base.text + "/"
                else:
                    head = ""
                return _R(head + name.text, name.tail)
            if attr == "with_stem" and args:
                # A new stem keeps the RECEIVER's suffix — so the receiver's own
                # trailing literal stays the suffix-gate's input.
                return sub(recv)
            if attr == "replace" and len(args) == 2:
                # ``str.replace(old, new)`` — the literal REPLACEMENT becomes the
                # tail. (``Path.replace(target)`` takes ONE arg and is excluded.)
                base, repl = sub(recv), sub(args[1])
                return _R(base.text + repl.text, repl.tail)
            if attr == "sub" and isinstance(recv, ast.Name) and recv.id == "re" and len(args) >= 3:
                subject, repl = sub(args[2]), sub(args[1])
                return _R(subject.text + repl.text, repl.tail)
            if attr in ("format", "format_map"):
                base = sub(recv)
                # ``text == tail`` holds exactly when the receiver rendered to ONE
                # known literal — a str Constant, or a NAME bound to one, since
                # ``sub`` resolves bindings transitively. Testing the rendered
                # value rather than ``isinstance(recv, ast.Constant)`` is what
                # keeps ``TPL = "{}.py"; TPL.format(s)`` from slipping through as
                # a syntactic near-miss of the same defect.
                if base.tail is not None and base.tail == base.text:
                    # Mirror the ``%`` branch: the template literal carries the
                    # shape, so fields become unknowns and the tail is whatever
                    # literal text trails the LAST field.
                    lit = base.text
                    text = _FMT_FIELD_RE.sub(lambda _: ctx.unk(), lit)
                    matches = list(_FMT_FIELD_RE.finditer(lit))
                    tail = lit[matches[-1].end() :] if matches else lit
                    return _R(text, tail or None)
                if attr == "format":
                    # ``format_map`` takes a MAPPING, not positional pieces, so
                    # only ``format`` may fall back to the concatenation path.
                    parts = [base] + [sub(a) for a in args]
                    return _R("".join(p.text for p in parts), parts[-1].tail)
        return _R(ctx.unk(), None)

    return _R(ctx.unk(), None)


def render_destination(
    node: ast.AST, bindings: dict[str, ast.AST], self_path: str | None
) -> Rendered:
    """Render a write DESTINATION expression for the RC-164 fold."""
    ctx = _Ctx()
    r = _render(node, bindings, self_path, ctx)
    text = r.text
    sep = text.rfind("/")
    prefix = text[: sep + 1] if sep >= 0 else ""
    last = text[sep + 1 :]
    literal_tail: str | None = None
    if r.tail:
        frag = r.tail.rsplit("/", 1)[-1]
        if frag and last.endswith(frag):
            literal_tail = frag
    # With an EMPTY prefix the whole path root is the last component's stem, so
    # an unknown stem there means the destination has no placeable root at all
    # (``build_path(cfg) + ".py"``). Folding it would resolve the bare ``.py``
    # relative to the plugin root and claim an in-tree hit the expression never
    # supports — so the empty-prefix case counts as an UNKNOWN prefix.
    stem = last[: len(last) - len(literal_tail)] if literal_tail else last
    has_unknown_prefix = bool(_UNK_RE.search(prefix)) or (
        prefix == "" and bool(_UNK_RE.search(stem))
    )
    return Rendered(
        prefix=prefix,
        literal_tail=literal_tail,
        unknown=bool(_UNK_RE.search(text)),
        has_unknown_prefix=has_unknown_prefix,
    )


# ── copy predicate + written-content head ──────────────────────────────

_READ_METHODS: Final[frozenset[str]] = frozenset({"read_text", "read_bytes"})


def _is_verbatim_read(
    node: ast.AST | None,
    bindings: dict[str, ast.AST],
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """True iff the written CONTENT is, verbatim, a file READ.

    A Name is followed through the SAME binding map the renderer uses, so an
    ``AugAssign`` (``s = src.read_bytes(); s += b"x"``) poisons the name and the
    write is NOT a copy. ``dst.write_text(src.read_text() + payload)`` is a
    BinOp, not a read call, so it is not a copy either.
    """
    if node is None or depth > 8:
        return False
    if isinstance(node, ast.Name):
        if node.id in seen:
            return False
        rhs = bindings.get(node.id)
        return _is_verbatim_read(rhs, bindings, depth + 1, seen | {node.id})
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in _READ_METHODS:
                return True
            if func.attr == "read" and isinstance(func.value, ast.Call):
                inner = func.value.func
                if isinstance(inner, ast.Name) and inner.id == "open":
                    return True
                if isinstance(inner, ast.Attribute) and inner.attr == "open":
                    return True
    return False


def _literal_head(
    node: ast.AST | None,
    bindings: dict[str, ast.AST],
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
) -> str | None:
    """The statically-known HEAD text of a written content expression."""
    if node is None or depth > 8:
        return None
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, str):
            return value
        if isinstance(value, (bytes, bytearray)):
            return bytes(value).decode("utf-8", errors="replace")
        return None
    if isinstance(node, ast.Name):
        if node.id in seen:
            return None
        return _literal_head(bindings.get(node.id), bindings, depth + 1, seen | {node.id})
    if isinstance(node, ast.JoinedStr):
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                return v.value
            return None
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _literal_head(node.left, bindings, depth + 1, seen)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "format":
            return _literal_head(func.value, bindings, depth + 1, seen)
        if isinstance(func, ast.Attribute) and func.attr == "dedent" and node.args:
            return _literal_head(node.args[0], bindings, depth + 1, seen)
    return None


_EXEC_BITS: Final[int] = 0o111


def _chmod_mode_is_exec(node: ast.AST | None) -> bool:
    """True when a chmod mode grants an execute bit (an UNKNOWN mode fails
    safe to True — an unmodelled mode must not silently clear the gate)."""
    value = _const_int(node) if node is not None else None
    if value is None:
        return True
    return bool(value & _EXEC_BITS)


def collect_ast_write_sinks(source: str, self_path: str | None) -> list[AstWriteSink] | None:
    """Every write sink in ``source``, rendered for the RC-164 fold.

    Returns ``None`` when ``source`` does not parse (the caller then falls back
    to the regex path, fail-closed — the RC-70 idiom).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None

    index = _ScopeIndex(tree)
    out: list[AstWriteSink] = []

    def emit(
        call: ast.Call,
        dst: ast.AST,
        sink_name: str,
        *,
        copy_idiom: bool = False,
        content: ast.AST | None = None,
        evidence: str | None = None,
    ) -> None:
        bindings = index.bindings_for(call)
        if evidence is None and content is not None:
            head = _literal_head(content, bindings)
            # A written body opening with a shebang makes the destination a
            # SCRIPT whatever its suffix — the AST twin of the heredoc
            # shebang gate.
            if head is not None and _head_is_shebang(head):
                evidence = "shebang"
        if not copy_idiom and content is not None and _is_verbatim_read(content, bindings):
            copy_idiom = True
        try:
            dest_text = ast.unparse(dst)
        except Exception:  # pragma: no cover - defensive
            dest_text = "<unparse-error>"
        out.append(
            AstWriteSink(
                line_no=_call_line(call),
                sink=sink_name,
                dest_text=dest_text,
                rendered=render_destination(dst, bindings, self_path),
                copy_idiom=copy_idiom,
                script_evidence=evidence,
            )
        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        is_open = (isinstance(func, ast.Name) and func.id == "open") or (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and isinstance(func.value, ast.Name)
            and func.value.id == "io"
        )
        if is_open and node.args:
            mode_node = node.args[1] if len(node.args) > 1 else None
            if mode_node is None:
                for kw in node.keywords:
                    if kw.arg == "mode":
                        mode_node = kw.value
            if _mode_is_write(mode_node):
                emit(node, node.args[0], "open")
            continue

        if isinstance(func, ast.Attribute) and func.attr in ("write_text", "write_bytes"):
            emit(
                node,
                func.value,
                func.attr,
                content=node.args[0] if node.args else None,
            )
            continue

        if (
            isinstance(func, ast.Attribute)
            and func.attr == "open"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
            and node.args
        ):
            flags_text = ast.unparse(node.args[1]) if len(node.args) > 1 else None
            if _flags_is_write(flags_text):
                emit(node, node.args[0], "os.open")
            continue

        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "shutil"
            and func.attr in ("copy", "copy2", "copyfile", "copytree", "move")
            and len(node.args) >= 2
        ):
            emit(node, node.args[1], f"shutil.{func.attr}", copy_idiom=True)
            continue

        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
            and func.attr in ("rename", "replace")
            and len(node.args) >= 2
        ):
            emit(node, node.args[1], f"os.{func.attr}", copy_idiom=True)
            continue

        # chmod — marking an in-plugin path executable makes it a runnable
        # script regardless of suffix (the AST twin of the `chmod +x` regex).
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
            and func.attr == "chmod"
            and node.args
        ):
            if _chmod_mode_is_exec(node.args[1] if len(node.args) > 1 else None):
                emit(node, node.args[0], "os.chmod", evidence="chmod")
            continue
        if isinstance(func, ast.Attribute) and func.attr == "chmod" and not isinstance(
            func.value, ast.Name
        ):
            if _chmod_mode_is_exec(node.args[0] if node.args else None):
                emit(node, func.value, "Path.chmod", evidence="chmod")
            continue

    return out


def _head_is_shebang(head: str) -> bool:
    """True iff the first non-blank line of ``head`` is a shebang.

    Delegates to the guard's own predicate so the shebang vocabulary has ONE
    source — a second copy would drift."""
    return _body_starts_with_shebang(head)


# ────────────────────────────────────────────────────────────────────────
# Census CLI
# ────────────────────────────────────────────────────────────────────────

_WALK_SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".git",
        "target",
        "__pycache__",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        "site-packages",
    }
)


def _iter_candidate_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in __import__("os").walk(root):
        dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP_DIRS]
        for name in filenames:
            suffix = Path(name).suffix.lower()
            if suffix in _PY_SUFFIXES or suffix in _SHELL_SUFFIXES:
                out.append(Path(dirpath) / name)
    return out


def _dedupe_cache_roots(roots: list[Path]) -> list[Path]:
    """Keep only the highest-version dir per <marketplace>/<plugin> pair.

    Expects a ``<cache>/<marketplace>/<plugin>/<version>/`` layout — collapses
    each ``root/<marketplace>/<plugin>/`` to its highest-version child.
    """
    out: list[Path] = []
    for cache_root in roots:
        if not cache_root.is_dir():
            continue
        for marketplace_dir in sorted(p for p in cache_root.iterdir() if p.is_dir()):
            for plugin_dir in sorted(p for p in marketplace_dir.iterdir() if p.is_dir()):
                versions = [p for p in plugin_dir.iterdir() if p.is_dir()]
                if not versions:
                    continue

                def _version_key(p: Path) -> tuple[int, tuple[int, ...], str]:
                    parts = p.name.split(".")
                    nums: list[int] = []
                    for part in parts:
                        if part.isdigit():
                            nums.append(int(part))
                        else:
                            break
                    if nums:
                        return (0, tuple(nums), p.name)
                    return (1, (), p.name)

                versions.sort(key=_version_key)
                out.append(versions[-1])
    return out


def _relpath(path: Path, roots: list[Path]) -> str:
    for r in roots:
        try:
            return str(path.relative_to(r))
        except ValueError:
            continue
    return str(path)


def _run_census(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cpv_write_sink_ast.py census")
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--dedupe-cache", action="store_true")
    parser.add_argument("--json")
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args(argv)

    input_roots = [Path(r) for r in args.roots]
    scan_roots = _dedupe_cache_roots(input_roots) if args.dedupe_cache else input_roots
    scan_roots = [r for r in scan_roots if r.exists()]

    files = []
    for r in scan_roots:
        files.extend(_iter_candidate_files(r))

    all_sinks: list[tuple[Path, WriteSink]] = []
    syntax_errors = 0
    for f in files:
        sinks = classify_file(f)
        if sinks is None:
            syntax_errors += 1
            continue
        all_sinks.extend((f, s) for s in sinks)

    # Table: dest_class x is_script_dest x copy/non-copy
    table: dict[tuple[str, str, str], int] = {}
    for _, s in all_sinks:
        script_key = "True" if s.is_script_dest else ("False" if s.is_script_dest is False else "None")
        copy_key = "copy" if s.copy_idiom else "non-copy"
        key = (s.dest_class, script_key, copy_key)
        table[key] = table.get(key, 0) + 1

    print(f"Deduped plugin roots scanned: {len(scan_roots)}")
    print(f"Files scanned: {len(files)}  (syntax errors: {syntax_errors})")
    print()
    print(f"{'dest_class':<18} {'script_dest':<12} {'copy?':<10} count")
    for (dest_class, script_key, copy_key), count in sorted(table.items()):
        print(f"{dest_class:<18} {script_key:<12} {copy_key:<10} {count}")
    print()

    def _top_list(pred, label: str) -> None:
        matches = [(f, s) for f, s in all_sinks if pred(s)]
        print(f"TOP {args.top} {label} (count={len(matches)}):")
        for f, s in matches[: args.top]:
            print(f"{_relpath(f, scan_roots)}:{s.line}  {s.sink}  {s.dest_text[:80]}")
        print()

    _top_list(
        lambda s: s.dest_class == "ASSEMBLED_UNKNOWN"
        and s.is_script_dest in (True, None)
        and not s.copy_idiom,
        "ASSEMBLED_UNKNOWN x script/unknown x non-copy",
    )
    _top_list(
        lambda s: s.dest_class == "FILE_ANCHORED" and s.is_script_dest is True and not s.copy_idiom,
        "FILE_ANCHORED x script x non-copy",
    )
    _top_list(
        lambda s: s.parent_hops >= 2,
        "parent_hops >= 2",
    )

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {"file": str(f), **asdict(s)} for f, s in all_sinks
        ]
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"JSON written: {out_path}")

    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] != "census":
        print("usage: cpv_write_sink_ast.py census <root>... [--dedupe-cache] [--json OUT] [--top N]")
        return 2
    return _run_census(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
