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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

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
    """Return a human description if `call` is a taint source, else None."""
    chain = _attribute_chain(call.func)
    if chain and chain in TAINT_SOURCES:
        return ".".join(chain) + "(...)"
    if chain and chain[:1] == ("input",) and len(chain) == 1:
        return "input(...)"
    return None


def _is_source_subscript(node: ast.Subscript) -> str | None:
    """e.g. os.environ['FOO'] or sys.argv[1]."""
    chain = _attribute_chain(node.value)
    if chain in (("os", "environ"), ("sys", "argv")):
        return ".".join(chain) + "[...]"
    return None


def _is_sink_call(call: ast.Call) -> str | None:
    """Return a human description if `call` is a taint sink, else None."""
    # Direct bare-name sinks: exec(), eval(), compile()
    if isinstance(call.func, ast.Name) and call.func.id in TAINT_SINKS_DIRECT:
        return f"{call.func.id}(...)"
    chain = _attribute_chain(call.func)
    if chain is None:
        return None
    if chain in TAINT_SINKS_QUALIFIED:
        # subprocess.* is only a sink when shell=True
        if chain[:1] == ("subprocess",) and chain[1:] in (("run",), ("call",), ("Popen",), ("check_call",)):
            for kw in call.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    return ".".join(chain) + "(..., shell=True)"
            return None
        return ".".join(chain) + "(...)"
    return None


def _is_sanitizer_call(call: ast.Call) -> bool:
    chain = _attribute_chain(call.func)
    if chain and chain in SANITIZERS_QUALIFIED:
        return True
    if isinstance(call.func, ast.Name) and call.func.id in SANITIZERS_BARE:
        return True
    return False


# -----------------------------------------------------------------------------
# Main analyzer
# -----------------------------------------------------------------------------


@dataclass
class _TaintState:
    """Per-scope mapping of variable name → (source_desc, hop_count)."""

    tainted: dict[str, tuple[str, int]] = field(default_factory=dict)

    def mark(self, name: str, source: str, hops: int) -> None:
        self.tainted[name] = (source, hops)

    def clear(self, name: str) -> None:
        self.tainted.pop(name, None)

    def lookup(self, name: str) -> tuple[str, int] | None:
        return self.tainted.get(name)


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
            _analyze_stmt(stmt, scope_state, findings)
            # Recurse into nested function/class definitions
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = _TaintState()
                # Function parameters are themselves untrusted in a defensive
                # sense; mark them as low-confidence taint sources so a
                # bare `exec(arg)` inside the function still warns.
                for arg in stmt.args.args:
                    inner.mark(arg.arg, f"function parameter '{arg.arg}'", 1)
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
        _process_assignment(stmt.target, stmt.value, state)
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        _process_assignment(stmt.target, stmt.value, state)

    # Walk every Call node in this statement looking for sinks
    for node in ast.walk(stmt):
        if isinstance(node, ast.Call):
            sink_desc = _is_sink_call(node)
            if sink_desc:
                _check_sink_args(node, sink_desc, state, findings)


def _process_assignment(
    target: ast.expr,
    value: ast.expr,
    state: _TaintState,
) -> None:
    """Update state based on `target = value`."""
    target_names = _assigned_names(target)
    if not target_names:
        return

    # Sanitizer call → clears taint
    if isinstance(value, ast.Call) and _is_sanitizer_call(value):
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
    """For each argument to a sink call, see if it's a tainted variable."""
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        if isinstance(arg, ast.Name):
            taint = state.lookup(arg.id)
            if taint:
                src, hops = taint
                rule = "RC-73" if hops == 1 else "RC-74"
                findings.append(
                    TaintFinding(
                        rule_id=rule,
                        source=src,
                        sink=sink_desc,
                        var_name=arg.id,
                        hop_count=hops,
                        line=call.lineno,
                    )
                )


# -----------------------------------------------------------------------------
# Public entry
# -----------------------------------------------------------------------------


def analyze_file(file_path: Path) -> list[TaintFinding]:
    """Parse and analyze a single .py file. Returns [] on syntax errors."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
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
