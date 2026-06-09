#!/usr/bin/env python3
"""AST-based Python taint analyzer (RC-73/74/75).

Tracks how external/untrusted data ("taint sources") flows through
assignments and reaches dangerous operations ("taint sinks") within a
single Python module. Catches the canonical injection chain
`os.environ.get(...) → exec(...)` and its multi-hop variants.

RC-73: direct source-to-sink (1 hop)
RC-74: transitive propagation (2+ hops via intermediate assignments)
RC-75: sanitizer recognition (clears taint — emitted as INFO, not finding)

Scope: single-file analysis. Cross-file taint is intentionally NOT
implemented — it requires whole-program type inference and is out of
proportion to the threat. Per-file analysis catches the dangerous cases
in plugin code (most plugin scripts are <300 LOC, single-file).

Coverage: Python only. JS/TS taint requires a real parser.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

_LOG = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Source / sink / sanitizer vocabulary
# -----------------------------------------------------------------------------

# Each source is a tuple of name parts: ('os', 'environ', 'get') matches
# os.environ.get(...) and os.environ['X']. Bare-call sources are length-1.
TAINT_SOURCES: frozenset[tuple[str, ...]] = frozenset(
    {
        ("os", "environ", "get"),
        ("os", "getenv"),
        ("os", "environ"),  # subscript access
        ("sys", "argv"),
        ("sys", "stdin", "read"),
        ("sys", "stdin", "readline"),
        ("input",),
        ("subprocess", "check_output"),
        ("socket", "recv"),
        ("requests", "get"),  # response.text/.json() are downstream
    }
)

# Sinks consume taint dangerously. Some are conditional (subprocess.run
# is only a sink with shell=True — handled in _is_sink_call).
TAINT_SINKS_DIRECT: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "compile",
    }
)

TAINT_SINKS_QUALIFIED: frozenset[tuple[str, ...]] = frozenset(
    {
        ("os", "system"),
        ("os", "popen"),
        ("subprocess", "run"),  # only when shell=True
        ("subprocess", "call"),  # only when shell=True
        ("subprocess", "Popen"),  # only when shell=True
        ("subprocess", "check_call"),  # only when shell=True
        ("subprocess", "getoutput"),
        ("subprocess", "getstatusoutput"),
        ("pickle", "loads"),
        ("yaml", "load"),  # yaml.safe_load is the sanitizer
        ("marshal", "loads"),
    }
)

# Sanitizers clear taint when the tainted value passes through them.
SANITIZERS_QUALIFIED: frozenset[tuple[str, ...]] = frozenset(
    {
        ("shlex", "quote"),
        ("shlex", "split"),
        ("re", "escape"),
        ("html", "escape"),
        ("urllib", "parse", "quote"),
        ("urllib", "parse", "quote_plus"),
        ("json", "loads"),
        ("yaml", "safe_load"),
        ("ast", "literal_eval"),
    }
)

SANITIZERS_BARE: frozenset[str] = frozenset(
    {
        "int",
        "float",
        "bool",
    }
)

# STRUCTURED-DATA parsers: a subset of SANITIZERS_QUALIFIED whose output can be
# a RAW attacker string (``json.loads('"rm -rf /"')`` → ``"rm -rf /"``). They
# neutralize INJECTION sinks (the common pattern accesses sub-fields) but NOT
# code-EXEC sinks — ``exec(json.loads(untrusted))`` runs attacker code. So when
# one of these sanitizes a tainted value we retain an exec-only taint instead of
# fully clearing. The escaper/coercer sanitizers (shlex.*, re.escape, html.escape,
# urllib.quote*, int/float/bool) genuinely neutralize and DO fully clear. (audit MAJOR #10)
SANITIZERS_STRUCTURED_PARSER: frozenset[tuple[str, ...]] = frozenset(
    {
        ("json", "loads"),
        ("yaml", "safe_load"),
        ("ast", "literal_eval"),
    }
)

# EXEC-class sinks execute / deserialize their argument as code, so a value
# that was only sanitized-for-injection (SANITIZERS_STRUCTURED_PARSER) is still
# dangerous here. (audit MAJOR #10)
TAINT_SINKS_EXEC_QUALIFIED: frozenset[tuple[str, ...]] = frozenset(
    {
        ("pickle", "loads"),
        ("marshal", "loads"),
        ("yaml", "load"),
    }
)

# str methods that PRESERVE taint (a tainted string stays tainted through them).
# Used to extract tainted Names from non-Name sink args without descending into
# arbitrary (possibly-sanitizing) function calls. (audit MINOR #11c)
_STR_PASSTHROUGH_METHODS: frozenset[str] = frozenset(
    {
        "strip",
        "lstrip",
        "rstrip",
        "lower",
        "upper",
        "title",
        "capitalize",
        "swapcase",
        "format",
        "format_map",
        "join",
        "replace",
        "encode",
        "decode",
        "expandtabs",
        "removeprefix",
        "removesuffix",
        "zfill",
    }
)


# -----------------------------------------------------------------------------
# Finding type
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaintFinding:
    """One source-to-sink path discovered in a single file."""

    rule_id: str  # "RC-73" (1-hop) or "RC-74" (transitive)
    source: str  # human description of the source
    sink: str  # human description of the sink
    var_name: str  # the variable carrying the taint at the sink
    hop_count: int  # 1 for direct, 2+ for transitive
    line: int  # line of the SINK


# -----------------------------------------------------------------------------
# Helpers — turn AST nodes into the (a, b, c) tuples we match against
# -----------------------------------------------------------------------------


def _attribute_chain(node: ast.AST) -> tuple[str, ...] | None:
    """Convert an ast.Attribute / ast.Name chain to a tuple of names.

    a.b.c → ('a','b','c'); foo → ('foo',); a()[0].b → None (not pure attribute).
    """
    parts: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return tuple(reversed(parts))
    return None


def _is_source_call(call: ast.Call) -> str | None:
    """Return a human description if `call` is a taint source, else None.

    ``input`` is matched by the TAINT_SOURCES membership test above —
    ``("input",)`` is a member — so no separate ``input`` branch is needed.
    """
    chain = _attribute_chain(call.func)
    if chain and chain in TAINT_SOURCES:
        return ".".join(chain) + "(...)"
    return None


def _is_source_subscript(node: ast.Subscript) -> str | None:
    """e.g. os.environ['FOO'] or sys.argv[1]."""
    chain = _attribute_chain(node.value)
    if chain in (("os", "environ"), ("sys", "argv")):
        return ".".join(chain) + "[...]"
    return None


def _resolve_sink_target(value: ast.expr, state: _TaintState | None) -> tuple[str, ...] | None:
    """If ``value`` (the RHS of an assignment) denotes a KNOWN exec/shell sink,
    return its normalised sink tuple, else None.

    Recognises the three SIMPLE alias shapes the RT5 fix targets:
      * ``eval`` / ``exec`` / ``compile``      → ``("eval",)`` … (a direct builtin
        sink), but ONLY when that bare name has NOT been locally rebound — a scope
        that did ``def eval(...)`` / ``eval = x`` / ``import ... as eval`` shadows
        the builtin, so ``e = eval`` there is a non-sink alias and must not fire.
      * a dotted attribute chain in ``TAINT_SINKS_QUALIFIED`` (``os.system``,
        ``subprocess.run``, …) → that chain tuple.
      * an existing sink alias (``t = s`` where ``s = os.system``) → alias-of-alias.

    Resolution is FN-SAFE in BOTH directions: it returns a tuple ONLY for a
    callee provably bound to a vocabulary sink, so a non-sink RHS (``g = print``)
    yields None and creates no alias / no spurious finding.
    """
    # eval / exec / compile — direct builtin sinks, unless locally shadowed.
    if isinstance(value, ast.Name):
        if value.id in TAINT_SINKS_DIRECT and (state is None or value.id not in state.shadowed_builtins):
            return (value.id,)
        # alias-of-alias: t = s  where  s = os.system
        if state is not None:
            existing = state.lookup_sink_alias(value.id)
            if existing is not None:
                return existing
        return None
    # os.system / subprocess.run / pickle.loads / … — qualified sinks.
    chain = _attribute_chain(value)
    if chain is not None and chain in TAINT_SINKS_QUALIFIED:
        return chain
    return None


def _qualified_sink_desc(chain: tuple[str, ...], call: ast.Call) -> str | None:
    """Human description for a qualified-sink CALL named by ``chain``.

    ``subprocess.{run,call,Popen,check_call}`` is a sink ONLY with ``shell=True``;
    this preserves that gate for both the direct dotted form and the alias form
    (``run = subprocess.run; run(x, shell=True)``) — an alias never relaxes the
    shell=True requirement."""
    if chain[:1] == ("subprocess",) and chain[1:] in (("run",), ("call",), ("Popen",), ("check_call",)):
        for kw in call.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                return ".".join(chain) + "(..., shell=True)"
        return None
    return ".".join(chain) + "(...)"


def _is_sink_call(call: ast.Call, state: _TaintState | None = None) -> str | None:
    """Return a human description if `call` is a taint sink, else None.

    ``state`` carries the per-scope sink-alias map (RT5). When the callee is a
    bare ``ast.Name`` that resolves to a registered alias of a known sink
    (``e = eval; e(x)`` / ``s = os.system; s(x)``), the call is treated as that
    underlying sink — the alias does not let an obfuscated sink evade detection,
    yet a non-sink name (never an alias) still returns None (FN-safe + no FP)."""
    # Direct bare-name sinks: exec(), eval(), compile() — unless locally shadowed.
    if isinstance(call.func, ast.Name):
        name = call.func.id
        if name in TAINT_SINKS_DIRECT and (state is None or name not in state.shadowed_builtins):
            return f"{name}(...)"
        # Bare-name ALIAS of a known sink (the RT5 hole): e = eval; e(x).
        if state is not None:
            aliased = state.lookup_sink_alias(name)
            if aliased is not None:
                if len(aliased) == 1:  # alias of a direct builtin sink
                    return f"{aliased[0]}(...)"
                return _qualified_sink_desc(aliased, call)
        return None
    chain = _attribute_chain(call.func)
    if chain is None:
        return None
    if chain in TAINT_SINKS_QUALIFIED:
        return _qualified_sink_desc(chain, call)
    return None


def _is_sanitizer_call(call: ast.Call) -> bool:
    chain = _attribute_chain(call.func)
    if chain and chain in SANITIZERS_QUALIFIED:
        return True
    if isinstance(call.func, ast.Name) and call.func.id in SANITIZERS_BARE:
        return True
    return False


def _is_structured_parser_sanitizer(call: ast.Call) -> bool:
    """True iff ``call`` is json.loads / yaml.safe_load / ast.literal_eval —
    a sanitizer whose output can still be exec'd. (audit MAJOR #10)"""
    chain = _attribute_chain(call.func)
    return chain is not None and chain in SANITIZERS_STRUCTURED_PARSER


def _tainted_arg_source(call: ast.Call, state: _TaintState) -> tuple[str, int] | None:
    """If any positional arg of ``call`` is a tainted Name or a direct taint
    source, return its ``(source_desc, hops)``. Used to carry taint THROUGH a
    structured-data parser as exec-risk. (audit MAJOR #10)"""
    for arg in call.args:
        if isinstance(arg, ast.Name):
            t = state.lookup(arg.id)
            if t:
                return t
        elif isinstance(arg, ast.Call):
            s = _is_source_call(arg)
            if s:
                return (s, 1)
        elif isinstance(arg, ast.Subscript):
            s = _is_source_subscript(arg)
            if s:
                return (s, 1)
        elif isinstance(arg, ast.Attribute):
            chain = _attribute_chain(arg)
            if chain and chain in TAINT_SOURCES:
                return (".".join(chain), 1)
    return None


def _is_exec_class_sink(call: ast.Call, state: _TaintState | None = None) -> bool:
    """True iff ``call`` executes/deserializes its argument as code — exec/eval/
    compile (bare) or pickle.loads / marshal.loads / yaml.load. (audit MAJOR #10)

    Alias-aware (RT5): a bare-name call resolving to a sink alias is exec-class
    iff the UNDERLYING sink is exec-class (``e = eval; e(x)`` is; ``s = os.system;
    s(x)`` is not). A locally-shadowed builtin name (``def eval``) is not."""
    if isinstance(call.func, ast.Name):
        name = call.func.id
        if name in TAINT_SINKS_DIRECT and (state is None or name not in state.shadowed_builtins):
            return True
        if state is not None:
            aliased = state.lookup_sink_alias(name)
            if aliased is not None:
                # Direct-builtin alias (("eval",)) → exec-class; qualified alias →
                # exec-class only if the chain is in the exec-qualified set.
                return aliased[0] in TAINT_SINKS_DIRECT or aliased in TAINT_SINKS_EXEC_QUALIFIED
        return False
    chain = _attribute_chain(call.func)
    return chain is not None and chain in TAINT_SINKS_EXEC_QUALIFIED


def _passthrough_tainted_names(arg: ast.expr) -> list[str]:
    """Names reachable from ``arg`` through TAINT-PRESERVING shapes only —
    attribute access, subscript, string concat (BinOp), f-strings, and the str
    passthrough methods in ``_STR_PASSTHROUGH_METHODS`` (``user.strip()``).

    Deliberately does NOT descend into arbitrary function calls — those may
    sanitize, and the taint findings are blocking (RC-73=MAJOR / RC-74=MINOR),
    so guessing through unknown calls would create blocking false positives.
    (audit MINOR #11c; the unsound #11a path is intentionally not taken.)
    """
    found: list[str] = []

    def _walk(node: ast.AST) -> None:
        if isinstance(node, ast.Name):
            found.append(node.id)
        elif isinstance(node, ast.Attribute):
            _walk(node.value)
        elif isinstance(node, ast.Subscript):
            _walk(node.value)
        elif isinstance(node, ast.BinOp):
            _walk(node.left)
            _walk(node.right)
        elif isinstance(node, ast.JoinedStr):  # f-string
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    _walk(part.value)
        elif isinstance(node, ast.Call):
            # ONLY str passthrough methods (x.strip()); never arbitrary calls.
            if isinstance(node.func, ast.Attribute) and node.func.attr in _STR_PASSTHROUGH_METHODS:
                _walk(node.func.value)

    _walk(arg)
    return found


# -----------------------------------------------------------------------------
# Main analyzer
# -----------------------------------------------------------------------------


@dataclass
class _TaintState:
    """Per-scope mapping of variable name → (source_desc, hop_count).

    ``tainted`` holds FULL taint (dangerous for any sink). ``exec_risk`` holds
    values that were sanitized-for-injection by a structured-data parser but are
    still dangerous if EXEC'd (audit MAJOR #10) — they trigger ONLY exec-class
    sinks. A name lives in at most one of the two dicts.

    ``sink_aliases`` resolves SIMPLE intra-scope aliases of a known exec/shell
    sink (RT5 — sink-obfuscation FN-hole). ``e = eval`` / ``s = os.system`` /
    ``run = subprocess.run`` make a bare ``ast.Name`` call (``e(x)``) a true sink
    that ``_attribute_chain`` cannot see (the callee is ``Name('e')``, not
    ``eval``). Each entry is a normalised sink tuple — ``("eval",)`` for a direct
    builtin sink, ``("subprocess", "run")`` for a qualified one — so ``_is_sink_call``
    re-points the alias to the underlying sink before the membership check.

    ``shadowed_builtins`` records bare sink names (``eval``/``exec``/``compile``)
    that have been REBOUND locally (``def eval(...)``, ``eval = something``, an
    ``import``). A subsequent ``e = eval`` then aliases the LOCAL ``eval``, NOT the
    builtin sink, so it must NOT create a sink alias — this is what keeps a
    non-sink alias from producing a spurious finding (the "preserve" half of the
    RT5 fix). A name is in at most one of ``sink_aliases`` / ``tainted`` /
    ``exec_risk`` at a time; reassignment clears the others.
    """

    tainted: dict[str, tuple[str, int]] = field(default_factory=dict)
    exec_risk: dict[str, tuple[str, int]] = field(default_factory=dict)
    sink_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    shadowed_builtins: set[str] = field(default_factory=set)

    def mark(self, name: str, source: str, hops: int) -> None:
        self.tainted[name] = (source, hops)
        self.exec_risk.pop(name, None)  # full taint supersedes exec-only
        self.sink_aliases.pop(name, None)  # data taint supersedes a stale alias

    def clear(self, name: str) -> None:
        self.tainted.pop(name, None)
        self.exec_risk.pop(name, None)
        self.sink_aliases.pop(name, None)

    def lookup(self, name: str) -> tuple[str, int] | None:
        return self.tainted.get(name)

    def mark_exec_risk(self, name: str, source: str, hops: int) -> None:
        """Injection taint cleared, but the value can still be exec'd."""
        self.exec_risk[name] = (source, hops)
        self.tainted.pop(name, None)
        self.sink_aliases.pop(name, None)

    def lookup_exec_risk(self, name: str) -> tuple[str, int] | None:
        return self.exec_risk.get(name)

    def mark_sink_alias(self, name: str, sink: tuple[str, ...]) -> None:
        """``name`` is now an alias of the known sink ``sink`` (e.g. ``("eval",)``
        or ``("os", "system")``). Supersedes any prior data-taint on ``name`` —
        the value IS the callable, not attacker data."""
        self.sink_aliases[name] = sink
        self.tainted.pop(name, None)
        self.exec_risk.pop(name, None)

    def lookup_sink_alias(self, name: str) -> tuple[str, ...] | None:
        return self.sink_aliases.get(name)


def analyze_module(tree: ast.Module) -> list[TaintFinding]:
    """Run taint analysis on a parsed Python module and return findings.

    For each function body and the module-level body, tracks variable
    taint linearly through statements. Loops/conditionals are unrolled
    via a single pass — sound for forward-only flow but deliberately
    over-approximates (no joins).
    """
    findings: list[TaintFinding] = []

    def analyze_block(body: list[ast.stmt], scope_state: _TaintState) -> None:
        for stmt in body:
            # SHADOWING (RT5): a ``def eval``/``class eval``/``import … as eval``
            # rebinds a builtin sink name in THIS scope, so a later ``e = eval``
            # aliases the local binding, not the builtin sink. Record it before
            # the alias logic in _analyze_stmt could (mis)resolve it.
            _record_sink_name_shadows(stmt, scope_state)
            _analyze_stmt(stmt, scope_state, findings)
            # Recurse into nested function/class definitions
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = _TaintState()
                # Function parameters are themselves untrusted in a defensive
                # sense; mark them as low-confidence taint sources so a
                # bare `exec(arg)` inside the function still warns.
                for arg in stmt.args.args:
                    inner.mark(arg.arg, f"function parameter '{arg.arg}'", 1)
                    # A parameter named like a builtin sink (``def f(eval): ...``)
                    # shadows that sink inside the body — ``eval`` there is the
                    # tainted param, so ``e = eval`` is data passthrough, not a
                    # sink alias. (RT5 no-FP)
                    if arg.arg in TAINT_SINKS_DIRECT:
                        inner.shadowed_builtins.add(arg.arg)
                analyze_block(stmt.body, inner)
            elif isinstance(stmt, ast.ClassDef):
                analyze_block(stmt.body, _TaintState())
            # Conditional / loop branches share the same scope (over-approx)
            for branch_attr in ("body", "orelse", "finalbody", "handlers"):
                if hasattr(stmt, branch_attr):
                    branch = getattr(stmt, branch_attr)
                    if isinstance(branch, list):
                        nested = [n for n in branch if isinstance(n, ast.stmt)]
                        if nested and not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            analyze_block(nested, scope_state)
                        # Handlers contain ExceptHandler with their own body
                        if branch_attr == "handlers" and isinstance(branch, list):
                            for h in branch:
                                if isinstance(h, ast.ExceptHandler):
                                    analyze_block(list(h.body), scope_state)

    analyze_block(list(tree.body), _TaintState())
    return findings


# Statement fields that hold NESTED STATEMENT blocks. Sinks inside these
# are found when ``analyze_block`` recurses into them, NOT by the
# enclosing statement's own-expression walk — otherwise every nested sink
# is inspected once per enclosing level (O(depth) redundant work AND
# duplicate findings). (audit MINOR #5)
_NESTED_STMT_FIELDS: frozenset[str] = frozenset({"body", "orelse", "finalbody", "handlers"})


def _own_calls(stmt: ast.stmt) -> Iterable[ast.Call]:
    """Yield every ``ast.Call`` reachable from ``stmt``'s OWN expressions,
    WITHOUT descending into nested statement blocks (if/for/while/try
    bodies, function/class bodies, except handlers).

    This is the expression-level half of ``ast.walk(stmt)``: it finds
    sinks nested arbitrarily deep inside THIS statement's expressions
    (``foo(bar(os.system(x)))``) exactly once, while leaving sinks that
    live in child statements to ``analyze_block``'s recursion. The result
    is each sink inspected exactly once with taint state still flowing
    correctly through branches (the recursion shares ``scope_state``).
    """
    stack: list[ast.AST] = [stmt]
    first = True
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            yield node
        for field_name, value in ast.iter_fields(node):
            # Do NOT cross into nested statement blocks — recursion owns
            # them. Only skip these on the ROOT statement; deeper nodes are
            # all expressions/operators and never carry these field names
            # as statement lists, but guarding the root is what matters.
            if first and field_name in _NESTED_STMT_FIELDS:
                continue
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        stack.append(item)
            elif isinstance(value, ast.AST):
                stack.append(value)
        first = False


def _record_sink_name_shadows(stmt: ast.stmt, state: _TaintState) -> None:
    """Record any builtin-sink name (``eval``/``exec``/``compile``) that ``stmt``
    REBINDS to a local definition or import, so a later ``e = <that name>`` is NOT
    treated as a builtin-sink alias (RT5 no-FP).

    Covers ``def eval``/``class eval`` and ``import x as eval`` /
    ``from m import f as eval`` / ``import eval`` (whatever a plugin imports as
    ``eval`` is its own object, not the builtin code-exec sink). Assignment-based
    shadowing (``eval = x``) is handled inside ``_process_assignment``.
    """
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if stmt.name in TAINT_SINKS_DIRECT:
            state.shadowed_builtins.add(stmt.name)
        return
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        for alias in stmt.names:
            bound = alias.asname or alias.name.split(".")[0]
            if bound in TAINT_SINKS_DIRECT:
                state.shadowed_builtins.add(bound)


def _analyze_stmt(
    stmt: ast.stmt,
    state: _TaintState,
    findings: list[TaintFinding],
) -> None:
    # Assignments — propagate or clear taint
    if isinstance(stmt, ast.Assign):
        for target in stmt.targets:
            _process_assignment(target, stmt.value, state)
    elif isinstance(stmt, ast.AugAssign):
        # ``target OP= value`` is ``target = target OP value`` — taint is the
        # UNION. Never CLEAR the target (it keeps its own prior taint); only ADD
        # any taint the value contributes. (audit MINOR #11b)
        _process_augassign(stmt.target, stmt.value, state)
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        _process_assignment(stmt.target, stmt.value, state)

    # Inspect every Call in this statement's OWN expressions for sinks.
    # Nested-statement-block sinks are handled by analyze_block recursion,
    # so each sink is checked exactly once (audit MINOR #5).
    #
    # RT6 — precompute the set of Call nodes whose VALUE is itself immediately
    # invoked, i.e. the inner ``g`` of ``g(...)``. This is exactly the
    # ``getattr(obj, tainted)(...)`` gadget shape: the dynamic-getattr Call is the
    # ``.func`` of an enclosing Call. ``_check_dynamic_getattr`` consults this to
    # keep that gadget firing on an ordinary object while clearing the benign
    # value-returning ``getattr(self._real, name)`` reflection (result not called).
    invoked_call_ids = {id(n.func) for n in _own_calls(stmt) if isinstance(n.func, ast.Call)}
    for node in _own_calls(stmt):
        sink_desc = _is_sink_call(node, state)
        if sink_desc:
            _check_sink_args(node, sink_desc, state, findings)
        _check_dynamic_getattr(node, state, findings, result_called=id(node) in invoked_call_ids)


def _process_assignment(
    target: ast.expr,
    value: ast.expr,
    state: _TaintState,
) -> None:
    """Update state based on `target = value`."""
    target_names = _assigned_names(target)
    if not target_names:
        return

    # SINK-ALIAS tracking (RT5). Resolve the RHS to a known exec/shell sink BEFORE
    # the source/sanitizer/data interpretations: a sink CALLABLE (``s = os.system``,
    # ``e = eval``) is none of those, and binding the target to it lets a later
    # bare-name call (``s(x)``) be recognised as the underlying sink. Also track
    # SHADOWING — rebinding a builtin sink name to a non-sink (``def eval``-style
    # ``eval = x``) means a subsequent ``e = eval`` aliases the LOCAL ``eval``, not
    # the builtin, so it must NOT become a sink alias (preserves no-FP).
    resolved_sink = _resolve_sink_target(value, state)
    for name in target_names:
        if name in TAINT_SINKS_DIRECT and resolved_sink != (name,):
            # ``eval``/``exec``/``compile`` rebound to anything other than itself
            # → the builtin sink is shadowed in this scope from here on.
            state.shadowed_builtins.add(name)
    if resolved_sink is not None:
        for name in target_names:
            state.mark_sink_alias(name, resolved_sink)
        return
    # RHS is NOT a sink callable — drop any stale alias on the targets (handled by
    # mark/clear below), so a reassigned alias name stops being treated as a sink.

    # Sanitizer call → clears taint, EXCEPT a structured-data parser whose input
    # traces to a taint source: its result can be a raw attacker string that is
    # still dangerous if EXEC'd, so retain an exec-only taint. (audit MAJOR #10)
    if isinstance(value, ast.Call) and _is_sanitizer_call(value):
        src_hops = _tainted_arg_source(value, state)
        if _is_structured_parser_sanitizer(value) and src_hops is not None:
            src, hops = src_hops
            for name in target_names:
                state.mark_exec_risk(name, src, hops)
        else:
            for name in target_names:
                state.clear(name)
        return

    # Source call → marks taint with hop count = 1
    if isinstance(value, ast.Call):
        source = _is_source_call(value)
        if source:
            for name in target_names:
                state.mark(name, source, 1)
            return

    # Source subscript: x = os.environ['FOO']
    if isinstance(value, ast.Subscript):
        source = _is_source_subscript(value)
        if source:
            for name in target_names:
                state.mark(name, source, 1)
            return

    # Source attribute access: x = sys.argv (no call, no subscript)
    if isinstance(value, ast.Attribute):
        chain = _attribute_chain(value)
        if chain and chain in TAINT_SOURCES:
            for name in target_names:
                state.mark(name, ".".join(chain), 1)
            return

    # Pass-through: y = x  →  y inherits x's taint with +1 hop
    if isinstance(value, ast.Name):
        upstream = state.lookup(value.id)
        if upstream:
            src, hops = upstream
            for name in target_names:
                state.mark(name, src, hops + 1)
            return
        # Otherwise the assignment clears whatever was there
        for name in target_names:
            state.clear(name)
        return

    # Default: not a recognized source — clear any prior taint on the target
    for name in target_names:
        state.clear(name)


def _process_augassign(target: ast.expr, value: ast.expr, state: _TaintState) -> None:
    """Handle ``target OP= value`` as ``target = target OP value``.

    Taint is the UNION: the target KEEPS its own prior taint and additionally
    gains any taint the value contributes. The plain-assignment path would
    instead overwrite (and CLEAR the target when value is untainted), which lost
    taint on ``s += untrusted_part`` and on ``cmd += x``. (audit MINOR #11b)
    """
    target_names = _assigned_names(target)
    if not target_names:
        return

    # Taint contributed by ``value`` (Name passthrough +1 hop, or a direct source).
    contributed: tuple[str, int] | None = None
    if isinstance(value, ast.Name):
        up = state.lookup(value.id)
        if up:
            contributed = (up[0], up[1] + 1)
    elif isinstance(value, ast.Call):
        s = _is_source_call(value)
        if s:
            contributed = (s, 1)
    elif isinstance(value, ast.Subscript):
        s = _is_source_subscript(value)
        if s:
            contributed = (s, 1)

    if contributed is None:
        return  # value adds no taint — preserve the target's existing taint
    for name in target_names:
        existing = state.lookup(name)
        # Keep whichever taint is "closer" (lower hop count) — a direct source on
        # either side wins over a multi-hop one.
        if existing is not None and existing[1] <= contributed[1]:
            continue
        state.mark(name, contributed[0], contributed[1])


def _assigned_names(target: ast.expr) -> list[str]:
    """Extract bound names from an assignment target."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for elt in target.elts:
            if isinstance(elt, ast.Name):
                names.append(elt.id)
        return names
    return []


def _check_sink_args(
    call: ast.Call,
    sink_desc: str,
    state: _TaintState,
    findings: list[TaintFinding],
) -> None:
    """For each argument to a sink call, see if it carries a tainted variable.

    Inspects not just bare ``ast.Name`` args but tainted Names reachable through
    taint-preserving shapes (``exec(user.strip())``, ``os.system(a + b)``,
    f-strings) via ``_passthrough_tainted_names`` (audit MINOR #11c). For
    EXEC-class sinks, also flags values that were sanitized-for-injection but
    remain exec-dangerous (audit MAJOR #10).
    """
    exec_sink = _is_exec_class_sink(call, state)
    seen: set[str] = set()
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        for name in _passthrough_tainted_names(arg):
            if name in seen:
                continue
            taint = state.lookup(name)
            if taint is None and exec_sink:
                taint = state.lookup_exec_risk(name)
            if taint:
                seen.add(name)
                src, hops = taint
                rule = "RC-73" if hops == 1 else "RC-74"
                findings.append(
                    TaintFinding(
                        rule_id=rule,
                        source=src,
                        sink=sink_desc,
                        var_name=name,
                        hop_count=hops,
                        line=call.lineno,
                    )
                )


# Dynamic-attribute sinks (AST7, SkillSpector port — TRDD-de582146 / proposal
# TRDD-b0c85371). ``getattr``/``setattr``/``delattr`` are sinks ONLY through their
# SECOND argument (the attribute NAME). A tainted attribute name is dynamic
# dispatch controlled by untrusted input — the classic ``getattr(os, user_input)``
# → ``os.system`` gadget. A LITERAL name (``getattr(o, "method", default)``) — the
# overwhelmingly common, benign shape — never fires, because the taint must reach
# arg[1] as a non-constant tainted value. This is why it lives in the taint engine,
# not as a regex: a bare ``getattr\(`` pattern would false-positive on every
# defensive attribute lookup.
_DYNAMIC_ATTR_BUILTINS: frozenset[str] = frozenset({"getattr", "setattr", "delattr"})

# Modules whose attributes are CAPABILITIES, not data: pulling an arbitrary
# attribute out of one of these by an attacker-controlled name hands the caller
# a code-execution / process-control / deserialization primitive even before it
# is invoked (``getattr(os, x)`` → ``os.system``; ``getattr(importlib, x)`` →
# ``import_module``; ``getattr(builtins, x)`` → ``eval``/``exec``). For these the
# dynamic-getattr IS the gadget regardless of whether the result is immediately
# called. A tainted attr name on a non-dangerous object (``getattr(self._real,
# name)`` — the ubiquitous ``__getattr__`` proxy idiom) returns a plain VALUE and
# is benign UNLESS that value is then invoked (handled separately by the
# ``result_called`` gate). The set mirrors the high-value reflection targets a
# ``getattr`` gadget reaches; ``__builtins__`` covers the dict-or-module dunder
# form. (RT6 — FP-prone benign reflection vs. the real getattr-on-module gadget.)
_DANGEROUS_ATTR_OBJECTS: frozenset[str] = frozenset(
    {
        "os",
        "subprocess",
        "sys",
        "builtins",
        "__builtins__",
        "importlib",
        "pickle",
        "marshal",
        "ctypes",
        "shutil",
        "socket",
        "commands",
        "pty",
        "platform",
    }
)


def _getattr_object_is_dangerous(obj_arg: ast.expr) -> bool:
    """True iff arg[0] of a getattr/setattr/delattr names a CAPABILITY module
    (``os``, ``subprocess``, ``importlib``, ``builtins``, …) — directly
    (``getattr(os, x)``) or as the head of a dotted chain
    (``getattr(os.path, x)``, ``getattr(sys.modules['os'], x)`` resolves through
    the subscript to ``sys``). A tainted attr name on such an object is dynamic
    dispatch into a dangerous namespace and fires unconditionally."""
    if isinstance(obj_arg, ast.Name):
        return obj_arg.id in _DANGEROUS_ATTR_OBJECTS
    chain = _attribute_chain(obj_arg)
    if chain is not None:
        return chain[0] in _DANGEROUS_ATTR_OBJECTS
    # ``sys.modules[...]`` / ``importlib.import_module(...)`` — the head still
    # resolves to a dangerous module through a subscript or call.
    cur: ast.AST = obj_arg
    while isinstance(cur, (ast.Subscript, ast.Call)):
        cur = cur.value if isinstance(cur, ast.Subscript) else cur.func
    head = _attribute_chain(cur)
    return head is not None and head[0] in _DANGEROUS_ATTR_OBJECTS


def _check_dynamic_getattr(
    call: ast.Call,
    state: _TaintState,
    findings: list[TaintFinding],
    *,
    result_called: bool,
) -> None:
    """Flag ``getattr/setattr/delattr(obj, <tainted name>)`` — dynamic attribute
    access whose attribute NAME (arg[1]) is attacker-controlled.

    FP-safety (RT6). The bare-reflection idiom ``getattr(self._real, name)`` (a
    ``__getattr__`` stream/proxy delegate; ``getattr(report, level)`` dynamic
    method *lookup* that returns a value to the caller) is BENIGN: the result is
    a plain value, not executed, and the object is not a capability module. It
    used to fire RC-73/74 on every such proxy in every plugin — a genuine false
    positive. The sink now fires ONLY when the dynamic dispatch can actually
    execute / write into a dangerous namespace, i.e. when EITHER:

      * the object (arg[0]) is a capability module — ``getattr(os, attr)`` hands
        out ``os.system`` even if the result is bound, not called; OR
      * the getattr RESULT is IMMEDIATELY INVOKED — ``getattr(obj, tainted)(...)``
        — the classic gadget where the tainted name selects the method run.

    ``setattr``/``delattr`` are attribute WRITE/DELETE primitives: they return no
    usable value, so the benign "reflective read returns data" idiom does not
    apply to them. A tainted attribute *name* being written/deleted is inherently
    rare and suspicious, so they keep firing on ANY tainted name (the
    ``result_called`` gate is a no-op for them — setattr/delattr are never the
    func of an enclosing call in the value sense). This asymmetry is what removes
    the ubiquitous benign-``getattr`` FP without weakening write/delete coverage.

    FN-safety: a real ``getattr(os, user_input)()`` / ``getattr(__builtins__,
    x)()`` gadget still fires (dangerous object AND/OR result called); only the
    provably-benign value-returning reflection on an ordinary object clears.
    """
    if not (isinstance(call.func, ast.Name) and call.func.id in _DYNAMIC_ATTR_BUILTINS):
        return
    if len(call.args) < 2:
        return
    name_arg = call.args[1]
    # A literal attribute name cannot be attacker-controlled — the benign,
    # ubiquitous shape (getattr(o, "x", default)). Never fires.
    if isinstance(name_arg, ast.Constant):
        return
    builtin = call.func.id
    # getattr-specific FP gate: clear benign value-returning reflection on an
    # ordinary object (result not invoked). setattr/delattr are unaffected — they
    # have no returned value to use as data, so they stay on the original
    # any-tainted-name contract.
    if builtin == "getattr" and not result_called and not _getattr_object_is_dangerous(call.args[0]):
        return
    for nm in _passthrough_tainted_names(name_arg):
        taint = state.lookup(nm)
        if taint:
            src, hops = taint
            findings.append(
                TaintFinding(
                    rule_id="RC-73" if hops == 1 else "RC-74",
                    source=src,
                    sink=f"{builtin}(obj, <tainted attr name>)",
                    var_name=nm,
                    hop_count=hops,
                    line=call.lineno,
                )
            )
            return


# -----------------------------------------------------------------------------
# Public entry
# -----------------------------------------------------------------------------


def analyze_file(file_path: Path) -> list[TaintFinding]:
    """Parse and analyze a single .py file. Returns [] on read/parse errors.

    A read or parse failure is NOT silent: it is logged (audit MINOR #12) so a
    file that evades the taint layer leaves a trace. The empty-list return is the
    safe direction — the regex catalog still text-scans the file — but the log
    lets an operator see that this file's taint pass was skipped.
    """
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        _LOG.info("taint_engine: cannot read %s (%s) — taint pass skipped", file_path, exc)
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        _LOG.info("taint_engine: cannot parse %s (%s) — taint pass skipped", file_path, exc)
        return []
    return analyze_module(tree)


def iter_python_files(root: Path) -> Iterable[Path]:
    """Yield every .py file under root, skipping standard ignore dirs."""
    skip_dirs = {
        "node_modules",
        ".venv",
        ".git",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "vendor",
        "target",
    }
    for p in root.rglob("*.py"):
        parts = p.relative_to(root).parts
        if any(part in skip_dirs or part.endswith("_dev") for part in parts[:-1]):
            continue
        yield p


def analyze_plugin(plugin_path: Path) -> dict[Path, list[TaintFinding]]:
    """Run the taint analyzer over every .py file in a plugin tree."""
    out: dict[Path, list[TaintFinding]] = {}
    for f in iter_python_files(plugin_path):
        findings = analyze_file(f)
        if findings:
            out[f] = findings
    return out
