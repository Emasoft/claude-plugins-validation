#!/usr/bin/env python3
"""Python AST context classifier for SkillAudit (TRDD-a4260cc6).

Given a Python source file plus a line index that a SkillAudit regex
matched against, classify the surrounding AST shape so the matcher can
distinguish exploit-shaped calls (``subprocess.run(f"curl {host}",
shell=True)``) from benign-shaped calls
(``subprocess.run(["git-cliff", "--version"], capture_output=True)``).

The four verdicts mirror the contract in
``cpv_skillaudit_native._confidence`` — see the docstring at the top of
this module's ``classify`` function for the full mapping.

Contract:

* No exception ever leaks out of ``classify`` — partial parse failures
  return ``"unknown"`` so the existing heuristic chain takes over.
* No state crosses calls — each call is independent.
* The classifier MUST be conservative: when in doubt, return
  ``"unknown"`` (which falls through to "keep"). The iron rule —
  "better safe than sorry" — applies.
"""

from __future__ import annotations

import ast
import re
from typing import Final, Literal

ContextVerdict = Literal["safe_literal", "safe_doc", "suspect", "unknown"]

# Modules + attributes whose calls reach a shell. Every plugin uses at
# least one of these.
_SHELL_CALL_FQNAMES: Final[frozenset[str]] = frozenset(
    {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "os.system",
        "os.popen",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "commands.getoutput",
        "commands.getstatusoutput",
        "pty.spawn",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
    }
)

# eval / exec are dangerous regardless of argument shape; the classifier
# treats them as SUSPECT unless the arg is a literal Constant and the
# context is a unit test (handled by the test-file heuristic in
# _confidence).
_DYNAMIC_EXEC_FQNAMES: Final[frozenset[str]] = frozenset({"eval", "exec", "compile", "__import__"})

# Sinks that execute a STRING command directly (no ``shell=True`` kwarg needed) —
# feeding a regex-pattern string into one of these executes it. Used by the
# re-pattern-literal suppressor to refuse suppression when the pattern is
# consumed by an exec sink on the same statement. (audit MAJOR #9)
_STRING_CMD_EXEC_FQNAMES: Final[frozenset[str]] = (
    frozenset(
        {
            "os.system",
            "os.popen",
            "commands.getoutput",
            "commands.getstatusoutput",
            "pty.spawn",
            "asyncio.create_subprocess_shell",
        }
    )
    | _DYNAMIC_EXEC_FQNAMES
)

# Hash functions flagged by INSECURE_CRYPTO. The matcher fires on the
# function reference itself; the AST classifier then checks the call
# context — these are commonly used for non-cryptographic identity
# (cache keys, session IDs, file dedupe) where weak-hash != security
# defect. The shape ``hashlib.<weak>(...).hexdigest()`` followed by
# slicing or assignment to an identity-named target signals identity
# usage, not crypto.
_WEAK_HASH_FQNAMES: Final[frozenset[str]] = frozenset({"hashlib.md5", "hashlib.sha1"})
_IDENTITY_TARGET_NAMES: Final[frozenset[str]] = frozenset(
    {
        "digest",
        "hash",
        "key",
        "cache_key",
        "cachekey",
        "tick",
        "tick_key",
        "session_id",
        "sessionid",
        "sid",
        "id",
        "identifier",
        "fingerprint",
        "checksum",
        "etag",
        "signature",
        "sig",
        "name",
        "uid",
        "guid",
        "session",
        "entries_hash",
        "content_hash",
        "file_hash",
        "path_hash",
    }
)

# Substrings that mark an assignment target as security-sensitive. Weak-hashing
# (md5/sha1) a value destined for one of these is the exact INSECURE_CRYPTO
# threat, never benign "identity usage" — so it must stay visible regardless of
# .hexdigest()/slice shape. Substring match: ``user_password``, ``access_token``,
# ``secret_hash`` all qualify. (audit MAJOR #8) A plain tuple of stems (not a
# compiled regex) is all that's needed here.
_SECURITY_TARGET_STEMS: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "credential",
    "apikey",
    "api_key",
    "privatekey",
    "private_key",
    "authkey",
    "auth_key",
)


def _node_qualname(node: ast.AST) -> str | None:
    """Resolve ``a.b.c.method`` into a dotted string. Returns None on unknown shapes."""
    if isinstance(node, ast.Attribute):
        prefix = _node_qualname(node.value)
        if prefix is None:
            return None
        return f"{prefix}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return None


def _arg_is_pure_literal(arg: ast.expr) -> bool:
    """True iff arg is a literal string/bytes constant.

    Constants like ``"git-cliff"``, ``b"foo"``, ``""`` are pure literals.
    Numbers / None / True / False are also literals but never reach a
    shell argv usefully — we still accept them (they'd be coerced to
    strings).
    """
    return isinstance(arg, ast.Constant) and not isinstance(arg.value, type(...))


# Names and attribute-access chains whose results coerce a typed object
# into a string without shell expansion. Calls to these are treated as
# safe argv elements.
_SAFE_COERCION_FUNCS: Final[frozenset[str]] = frozenset({"str", "int", "float", "bool", "bytes", "Path"})
_SAFE_COERCION_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "as_posix",
        "absolute",
        "resolve",
        "parent",
        "name",
        "stem",
        "suffix",
        "fspath",  # os.fspath / pathlib.PurePath.fspath
        "expanduser",  # Path.expanduser
        "joinpath",  # Path.joinpath returns a Path
        "with_name",
        "with_suffix",
        "relative_to",
    }
)


# Patterns that signal a real injection-shape argv element. If ANY arg
# element matches one of these, the call gets "suspect" — these are
# the shapes the user's issue #33 listed as real risk:
#   * JoinedStr  (f-strings  f"curl {host}")
#   * BinOp where one operand is non-Constant  ("rm -rf " + path)
#   * Call to .format() / .join() / .replace() on a string
def _arg_is_exploit_shape(arg: ast.expr) -> bool:
    """True iff ``arg`` looks like a user-input-flowing-into-shell shape.

    The exploit shape this detector targets is the **literal-string +
    variable** concatenation pattern that's the classic shell-injection
    vehicle:

        subprocess.run("rm -rf " + user_path, shell=True)

    where a constant string prefix is joined with a non-constant value
    and then executed via shell. The detector flags this; everything
    else stays safe by default.

    Variable + variable (``cmd + file_paths``) is NOT flagged — it's
    most often list-concat producing argv, never string-concat into
    a shell. Variable + List literal (``cmd + ["--foo"]``) is also
    not flagged. F-strings ARE flagged. ``.format`` / ``.join`` calls
    are flagged.
    """
    if isinstance(arg, ast.JoinedStr):
        return True
    if isinstance(arg, ast.BinOp):
        # Phase 6 FP-iteration (Emasoft/emasoft-plugins):
        #   * If EITHER operand is a List/Tuple literal, this is
        #     list/tuple concatenation, not string concat into a shell.
        #   * If NEITHER operand is a Constant string, the BinOp is
        #     ambiguous (could be list-concat or arithmetic) but is
        #     NOT the literal-prefix-into-shell shape that the rule
        #     targets. Default to safe.
        #   * The classic exploit shape requires AT LEAST ONE operand
        #     to be a literal string Constant (the attacker-readable
        #     prefix) AND the other to be non-Constant (the injection
        #     point).
        if isinstance(arg.left, (ast.List, ast.Tuple)) or isinstance(arg.right, (ast.List, ast.Tuple)):
            return False
        left_is_literal_str = isinstance(arg.left, ast.Constant) and isinstance(arg.left.value, str)
        right_is_literal_str = isinstance(arg.right, ast.Constant) and isinstance(arg.right.value, str)
        left_is_const = isinstance(arg.left, ast.Constant)
        right_is_const = isinstance(arg.right, ast.Constant)
        # Exactly the dangerous shape: ONE side is a literal string
        # and the OTHER side is non-Constant.
        if left_is_literal_str and not right_is_const:
            return True
        if right_is_literal_str and not left_is_const:
            return True
        # Every other BinOp shape is benign (var+var, list+list,
        # const+const, etc.).
        return False
    if isinstance(arg, ast.Call):
        if isinstance(arg.func, ast.Attribute):
            if arg.func.attr in {"format", "join", "format_map"}:
                return True
    return False


def _arg_is_known_safe(arg: ast.expr) -> bool:
    """True iff ``arg`` is a known-safe argv element.

    Safe shapes (the user's issue #33 binary applied properly):

    * ``Constant`` — pure literal.
    * ``Starred(value=Name)`` — ``*args`` from caller's argv.
    * ``Name`` — bare variable reference. Not an exploit-shape per se;
      the matcher at THIS site has no signal. Suppress.
    * ``Subscript`` — ``d["key"]`` / ``argv[1]``. Same logic as Name.
    * ``Attribute`` on a Name — ``self.cmd``. Same logic.
    * ``Call`` to a known-safe coercion (``str(x)``, ``Path(x).as_posix()``,
      ``os.fspath(x)``).

    Unsafe shapes (return False so caller falls through to exploit-shape
    detection):

    * ``JoinedStr`` (f-string)
    * ``BinOp`` with non-Constant operand
    * ``Call`` to ``.format()`` / ``.join()`` / etc.
    """
    if isinstance(arg, ast.Constant):
        return True
    if isinstance(arg, ast.Starred):
        return True  # *args — splat from caller; not an injection signal here
    if isinstance(arg, ast.Name):
        return True  # bare var — no signal at this site
    if isinstance(arg, (ast.Subscript, ast.Attribute)):
        return True  # d["k"], self.cmd, obj.field
    if isinstance(arg, ast.Call):
        # Calls to known-safe coercion functions.
        if isinstance(arg.func, ast.Name) and arg.func.id in _SAFE_COERCION_FUNCS:
            return True
        if isinstance(arg.func, ast.Attribute):
            if arg.func.attr in _SAFE_COERCION_ATTRS:
                return True
            # Reject explicit format-string call shape.
            if arg.func.attr in {"format", "join", "format_map", "replace"}:
                return False
        # Other calls: conservatively treat as safe (the call returns a
        # typed value; the FP cost of treating them as suspect is much
        # higher than the TP gain).
        return True
    return False


def _container_is_all_safe(node: ast.expr) -> bool:
    """True iff ``node`` is a List/Tuple/Set whose every element is
    a safe argv element shape.

    The argv elements of ``subprocess.run([...])`` / ``Popen([...])``
    do NOT get shell-expanded — each element is passed verbatim as one
    argv to the child process. So even f-strings inside the list are
    safe (the f-string evaluates to a typed Python string; the
    subprocess receives that string as one argv with no shell
    interpretation). The exploit shape is ``shell=True`` PLUS an
    f-string / BinOp concat as the WHOLE first arg — that case is
    handled separately in the caller via ``_shell_kwarg_is_true``
    + ``_arg_is_exploit_shape`` at the top level.

    Allowed element shapes:

    * ``Constant`` — pure literal.
    * ``Starred`` — ``*args`` from caller's argv.
    * ``Name`` — bare variable reference.
    * ``Subscript`` / ``Attribute`` — ``d["k"]`` / ``self.cmd``.
    * ``Call`` — assumed to return a typed string value.
    * ``JoinedStr`` — f-string. Safe inside a list arg because list
      elements are not shell-expanded.
    * ``BinOp`` — string concat. Same reasoning as JoinedStr.

    ``["a", "b", "c"]`` → True
    ``["git", *args]`` → True
    ``[str(path), "log"]`` → True
    ``["git-cliff", "--tag", f"v{ver}", "--unreleased"]`` → True
       (f-string element is safe because list args don't shell-expand)
    """
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return False
    # Inside a list arg, ANY element is safe at the call site (no
    # shell expansion of list args). The only injection-shape risk for
    # list-form subprocess calls is shell=True (handled upstream).
    return True


def _container_is_all_literals(node: ast.expr) -> bool:
    """Legacy strict check — kept for tests that pin literal-only behavior.

    Use ``_container_is_all_safe`` for the FP-aware classifier; this
    helper remains for unit-test introspection.
    """
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return False
    return all(_arg_is_pure_literal(elt) for elt in node.elts)


def _shell_kwarg_is_true(call: ast.Call) -> bool:
    """True iff the call has ``shell=True`` (literal True constant)."""
    for kw in call.keywords:
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _shell_kwarg_is_possibly_true(call: ast.Call) -> bool:
    """True iff a ``shell=`` kwarg is present whose value is NOT a literal
    ``False``/falsey constant — i.e. it could be truthy at runtime.

    The "Python guarantees no shell" reasoning only holds when the call
    provably does NOT route through a shell. A literal ``shell=True``
    obviously routes through one; but so can ``shell=use_shell`` where
    ``use_shell`` is a variable/attribute/call that evaluates to a truthy
    value at runtime. Static analysis can't prove such an indirection is
    falsey, so we must NOT take the safe branch for it. (audit MAJOR #2)

    Returns False only when:
      * there is no ``shell=`` kwarg at all, OR
      * ``shell=`` is bound to a literal constant that is falsey
        (``False``, ``0``, ``None``, ``""``).

    Returns True for every other ``shell=`` value (``True``, a ``Name``,
    an ``Attribute``, a ``Call``, an f-string, etc.).

    ``**kwargs`` splat: a bare ``**opts`` keyword (``kw.arg is None``)
    could itself carry ``shell=True`` at runtime, so it is treated as a
    possibly-true shell kwarg too — fail-safe over fail-open. The
    splat-only case is handled separately by ``_shell_signal_only_via_splat``
    (issue #45) so the dispatcher can apply the same convention other
    linters use (Bandit B603 / ruff S603): a list/Name first arg with
    only a ``**kwargs`` signal is not flagged.
    """
    for kw in call.keywords:
        if kw.arg is None:
            # ``**opts`` — could expand to shell=True; do not certify safe.
            return True
        if kw.arg != "shell":
            continue
        val = kw.value
        if isinstance(val, ast.Constant):
            # Literal constant — safe ONLY if it is falsey.
            return bool(val.value)
        # Non-literal expression (Name / Attribute / Call / BinOp / …):
        # cannot prove it is falsey → treat as possibly-true.
        return True
    return False


def _shell_signal_only_via_splat(call: ast.Call) -> bool:
    """True iff the call's ONLY ``shell=possibly-true`` signal is a
    ``**kwargs`` splat — there is no explicit ``shell=`` keyword.

    Used by ``_classify_call`` (issue #45) to distinguish the routine
    ``subprocess.run(cmd, **kw)`` shape (bandit B603 / ruff S603 don't
    flag this — the analyser can't prove ``**kw`` carries ``shell=True``
    so blanket-flagging produces noise) from the genuinely dangerous
    explicit ``shell=True`` / ``shell=use_shell`` shapes (which DO
    deserve the SHELL_EXEC / CMD_INJECTION finding regardless of
    first-arg shape).
    """
    has_splat = False
    for kw in call.keywords:
        if kw.arg is None:
            has_splat = True
            continue
        if kw.arg == "shell":
            # Explicit shell= — this branch is handled by
            # _shell_kwarg_is_possibly_true; never claim "only via splat".
            return False
    return has_splat


def _first_arg_is_argv_safe_shape_py(arg: ast.expr) -> bool:
    """True iff ``arg`` matches a conventional argv-list shape — the
    routine pattern that other linters (Bandit / ruff / Semgrep) do NOT
    flag because, by Python convention, the value is a ``list[str]`` not
    a shell-injectable string.

    Safe shapes:
      * ``Name`` — bare variable. Convention: holds a list of strings
        (e.g. ``cmd``, ``argv``, ``args``). Bandit B603 does not flag.
      * ``Subscript`` — ``d["cmd"]`` / ``argv[1:]``. Same convention.
      * ``Attribute`` — ``self.cmd`` / ``config.argv``. Same convention.
      * ``List`` / ``Tuple`` literal whose elements are ALL known-safe
        (Constant, Starred Name, Name, Subscript, Attribute, safe Call)
        AND no element is an exploit shape (f-string, str-concat).

    Unsafe shapes that fall through (the dispatcher then evaluates the
    rest of the chain — e.g. ``_arg_is_pure_literal`` for string-form,
    ``_arg_is_exploit_shape`` for injection-vehicle detection):
      * ``Constant`` (string-form ``subprocess.run("rm -rf /tmp", …)``).
      * ``BinOp`` (string concatenation).
      * ``JoinedStr`` (f-string).
      * ``Call`` to ``.format`` / ``.join`` (string-building chain).
    """
    if isinstance(arg, (ast.Name, ast.Subscript, ast.Attribute)):
        return True
    if isinstance(arg, (ast.List, ast.Tuple)):
        if not arg.elts:
            return False
        for elt in arg.elts:
            if isinstance(elt, ast.JoinedStr):
                return False
            if _arg_is_exploit_shape(elt):
                return False
            if not _arg_is_known_safe(elt):
                return False
        return True
    return False


def _is_inside_string_literal(tree: ast.AST, line: int) -> bool:
    """True iff the 1-based line falls inside any string-Constant node
    (docstring, multiline string, raw string used as data, etc.).

    Kept for back-compat with older callers; do NOT use as the primary
    "this match is documentation" check — every shell-call site has
    single-line string Constants on its argument line, so this function
    would shadow real call classification. Use
    ``_is_inside_multiline_string_literal`` instead, which only returns
    True when the Constant SPANS the matched line (i.e. is actually a
    docstring / multi-line data string).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is not None and end is not None and start <= line <= end:
                return True
    return False


def _is_inside_multiline_string_literal(tree: ast.AST, line: int) -> bool:
    """True iff ``line`` falls inside a multi-line string Constant
    or a multi-line f-string (JoinedStr).

    Distinguishes a real docstring / triple-quoted data string (which
    spans multiple lines) from a single-line literal that happens to
    appear on the same line as the matched substring (every shell-call
    arg line has at least one of these).

    A single-line constant where ``start == end == line`` is NOT
    considered "inside a string literal" by this helper — it returns
    False so the caller can classify by the surrounding Call instead.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is None or end is None:
                continue
            # Multi-line literal that spans the matched line.
            if start < line <= end:
                return True
            if start == line and end > line:
                return True
        elif isinstance(node, ast.JoinedStr):
            # f-string with at least one newline → triple-quoted; treat
            # the body as documentation/data string for our purposes.
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is None or end is None:
                continue
            if start < line <= end:
                return True
            if start == line and end > line:
                return True
    return False


def _enclosing_function_is_template_generator(tree: ast.AST, line: int) -> bool:
    """True iff ``line`` falls inside a FunctionDef whose body is dominated
    by template-string generation (returns a string-typed payload built
    from multi-line Constant / JoinedStr / BinOp-of-strings).

    Template-generator functions exist only to emit code or config as
    data — the matched ``subprocess.run`` / ``os.system`` / etc. inside
    the generated string is generated CODE that will be written to disk
    and validated separately when CPV scans the produced file. The
    generator file itself does NOT execute the inner pattern, so flagging
    it there is a false positive (same `subprocess.run([...])` shape gets
    reviewed twice — once in the template author file, once in the
    generated file).

    A function qualifies when EITHER:

      1. It has return annotation ``-> str`` AND ≥50% of its body's
         source range is covered by multi-line string literals.
      2. ≥85% of its body's source range is covered by multi-line
         string literals (regardless of annotation — annotation-free
         legacy generators still pass).

    The 50/85 ratios are conservative: a function that's mostly Python
    code with a single docstring + the matched line outside that
    docstring fails BOTH thresholds and is correctly NOT treated as a
    template generator.

    Walks every covering FunctionDef / AsyncFunctionDef and picks the
    DEEPEST one (smallest span); inner nested helpers inside a larger
    template generator are evaluated in their own right.
    """
    enclosing: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    best_span: float = float("inf")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        span = end - start
        if span < best_span:
            enclosing = node
            best_span = span
    if enclosing is None:
        return False

    func_start = enclosing.lineno or 0
    func_end = enclosing.end_lineno or 0
    func_total_lines = func_end - func_start + 1
    if func_total_lines <= 0:
        return False

    # Compute the union of source-line ranges covered by multi-line
    # string Constants and JoinedStr nodes inside this function body.
    # Using set-based union avoids double-counting overlapping nodes
    # (a JoinedStr that contains a Constant inside it would otherwise
    # be counted twice).
    literal_lines: set[int] = set()
    for n in ast.walk(enclosing):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            s = getattr(n, "lineno", None)
            e = getattr(n, "end_lineno", None)
            if s is not None and e is not None and e > s:
                literal_lines.update(range(s, e + 1))
        elif isinstance(n, ast.JoinedStr):
            s = getattr(n, "lineno", None)
            e = getattr(n, "end_lineno", None)
            if s is not None and e is not None and e > s:
                literal_lines.update(range(s, e + 1))
    literal_ratio = len(literal_lines) / func_total_lines

    ret = enclosing.returns
    returns_str = isinstance(ret, ast.Name) and ret.id == "str"

    return (returns_str and literal_ratio >= 0.50) or literal_ratio >= 0.85


def _find_enclosing_call(tree: ast.AST, line: int) -> ast.Call | None:
    """Return the deepest ``ast.Call`` node whose source range covers ``line``.

    "Deepest" so nested calls (``open(subprocess.run(...))``) resolve to
    the inner one — that's the one whose argv shape matters.
    """
    best: ast.Call | None = None
    best_span = float("inf")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None:
            continue
        if start <= line <= end:
            span = end - start
            if span < best_span:
                best = node
                best_span = span
    return best


def _find_enclosing_shell_call(tree: ast.AST, line: int) -> ast.Call | None:
    """Return a shell-reaching ``ast.Call`` covering ``line`` if one exists.

    Issue #39 fix: when a CMD_INJECTION pattern matches inside an
    f-string that wraps a ``subprocess.run([...])``, the OUTERMOST
    enclosing call is e.g. ``lines.append(f"... {subprocess.run([...]).stdout} ...")``.
    Both calls span exactly the matched line so
    ``_find_enclosing_call`` picks the first one ast.walk yields
    (top-down BFS = the outer call). The outer call's qualname is
    ``lines.append`` which is not in ``_SHELL_CALL_FQNAMES`` — so the
    classifier falls through to "unknown" → "keep" instead of
    recognising the SAFE inner ``subprocess.run([...])`` call.

    This helper finds the deepest covering Call whose qualname is in
    ``_SHELL_CALL_FQNAMES``, so the classifier can verdict on THAT
    call's argv shape (typically the safe list-form). Returns None
    when no shell-reaching call covers the line.
    """
    best: ast.Call | None = None
    best_span = float("inf")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        qn = _node_qualname(node.func)
        if qn not in _SHELL_CALL_FQNAMES and qn not in _DYNAMIC_EXEC_FQNAMES:
            continue
        span = end - start
        if span < best_span:
            best = node
            best_span = span
    return best


def _line_is_full_comment(source_line: str) -> bool:
    """True iff the stripped source line begins with ``#``."""
    return source_line.lstrip().startswith("#")


# ── Issue #40 — execution-class vs prose-vector split for comment/docstring ──
# A Python ``#`` comment and a docstring are NEVER executed. So a rule that
# REQUIRES code execution to be a threat (CMD_INJECTION, PATH_TRAVERSAL, …)
# matched inside one is a provable non-threat → suppress. But a rule whose
# THREAT IS THE PROSE ITSELF (prompt-injection / exfil instruction /
# hardcoded secret / invisible unicode) stays VISIBLE even in a comment —
# a careless agent or a grep-based loader could still surface that text.
_PROSE_VECTOR_RULES: Final[frozenset[str]] = frozenset(
    {
        "PROMPT_INJECT",
        "INDIRECT_PROMPT_INJECT",
        "DATA_EXFIL",
        "DATA_EXFIL_TO_NETWORK",
        "EXFIL_TO_CHAT",
        "URL_SUSPICIOUS",
        "HARDCODED_SECRET",
        "INVISIBLE_UNICODE_RAW",
        "BASE64_DECODE_THREAT",
        "HEX_DECODE_THREAT",
        "UNICODE_ESCAPE_DECODE_THREAT",
        "CHARCODE_DECODE_THREAT",
    }
)


def _rule_is_prose_vector(rule_id: str) -> bool:
    """True iff the rule's threat is the prose itself (stays visible in a
    comment/docstring). SECRET_* prefixes are treated as prose-vector too."""
    return rule_id in _PROSE_VECTOR_RULES or rule_id.startswith("SECRET_")


def _is_inside_docstring(tree: ast.AST, line: int) -> bool:
    """True iff the 1-based ``line`` falls inside a true DOCSTRING node — the
    string-literal first statement of a Module / FunctionDef / AsyncFunctionDef
    / ClassDef. A data string assigned to a variable is NOT a docstring and is
    excluded (it could be passed to a shell, per the user's "strings stay
    visible" rule)."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            start = getattr(first.value, "lineno", None)
            end = getattr(first.value, "end_lineno", None)
            if start is not None and end is not None and start <= line <= end:
                return True
    return False


# ── Issue #41 — CROSS_TOOL_ACCESS field-name discriminator (Python) ──
# Same principle as the TS classifier: the field-name patterns
# (system_prompt / context_window / …) are LLM-client domain vocabulary,
# distinct from the runtime data-grab patterns (get_tools / tool_results[ /
# previous_tool_output). Python uses these as function params, dict keys,
# CLI flags — all benign.
_API_FIELD_NAMES_PY: Final[frozenset[str]] = frozenset(
    {
        "system_prompt",
        "system_message",
        "context_window",
        "full_context",
        "conversation_history",
        "message_history",
        "chat_history",
    }
)
_RETRIEVAL_GRAB_RE_PY: Final[re.Pattern[str]] = re.compile(
    r"\b(?:get_tools|list_tools|available_tools|call_tool|invoke_tool|use_tool)\b"
    r"|\bprevious_tool_output\b"
    r"|\btool_results?\s*\["
    r"|\bget[_]?(?:all|previous|recent)\w*(?:message|response|output)",
    re.IGNORECASE,
)


def _is_api_field_name_match_py(line: str, match: str) -> bool:
    """True iff a CROSS_TOOL_ACCESS match is an LLM-API field NAME (domain
    vocabulary) and the line carries no hard runtime data-grab indicator."""
    if not any(name in match or name in line for name in _API_FIELD_NAMES_PY):
        return False
    if _RETRIEVAL_GRAB_RE_PY.search(line):
        return False
    return True


# ── Issue #41 — SSRF static-literal discriminator (Python) ──
# A localhost / metadata URL that is a fully static string literal (no
# f-string ``{…}`` interpolation, no ``+`` concatenation) has a fixed
# author-time destination → not attacker-controlled → not SSRF.
def _ssrf_url_is_static_literal_py(line: str, match: str) -> bool:
    idx = line.find(match)
    if idx < 0:
        return False
    # Find the enclosing string literal: nearest quote to the left of the URL
    # and nearest to the right. We do NOT break on punctuation while scanning
    # left — string CONTENT legitimately contains commas / parens
    # (``help="API base, e.g. http://localhost:1234"``), and breaking on them
    # mis-classifies the help text as "not in a literal".
    open_pos = max(line.rfind('"', 0, idx), line.rfind("'", 0, idx))
    if open_pos < 0:
        return False
    quote = line[open_pos]
    close_pos = line.find(quote, idx + len(match))
    if close_pos < 0:
        return False
    literal_body = line[open_pos + 1 : close_pos]
    # f-string interpolation inside the literal → dynamic.
    if "{" in literal_body and "}" in literal_body:
        prefix = line[max(0, open_pos - 2) : open_pos].lower()
        if "f" in prefix:
            return False
    after = line[close_pos + 1 :].lstrip()
    before = line[:open_pos].rstrip()
    if after.startswith("+") or before.endswith("+"):
        return False
    return True


# ── Issue #41 — SSRF loopback-host discriminator (Python, follow-up) ──
# Even when the URL string IS f-string interpolated, a loopback HOST
# literal cannot redirect outside the local machine, so the URL is not
# server-side-request-forgery regardless of the interpolated port / path.
# The dynamic-host case ``f"http://{host}:{port}"`` (host is not a literal)
# stays flagged. Concrete affected shapes from issue #41 follow-up on
# ai-maestro-webdesign:
#     url = f"http://localhost:{args.port}"
#     return server, f"http://127.0.0.1:{port}"
_LOOPBACK_HOSTS_PY: Final[frozenset[str]] = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
)


def _ssrf_url_is_loopback_with_literal_host_py(line: str, match: str) -> bool:
    """Return True iff the URL containing ``match`` has a literal loopback
    HOST, even if other URL components (port, path, query) are f-string
    interpolated. The host literal cannot escape the local machine, so the
    request is not server-side-request-forgery."""
    idx = line.find(match)
    if idx < 0:
        return False
    # Find the enclosing string literal: nearest matching quote on each side.
    open_pos = max(line.rfind('"', 0, idx), line.rfind("'", 0, idx))
    if open_pos < 0:
        return False
    quote = line[open_pos]
    close_pos = line.find(quote, idx + len(match))
    if close_pos < 0:
        return False
    literal_body = line[open_pos + 1 : close_pos]
    scheme_idx = literal_body.find("://")
    if scheme_idx < 0:
        return False
    # Authority = everything from after `://` to the first '/', '?', '#'
    # or end of literal. RFC 3986 says path/query/fragment all delimit it.
    tail = literal_body[scheme_idx + 3 :]
    end_in_tail = len(tail)
    for delim in "/?#":
        i = tail.find(delim)
        if 0 <= i < end_in_tail:
            end_in_tail = i
    authority = tail[:end_in_tail]
    # Strip userinfo (``user:pass@host``) — host is to the right of '@'.
    if "@" in authority:
        authority = authority.rsplit("@", 1)[-1]
    # Separate host from port: IPv6 hosts are bracketed (``[::1]:8080``);
    # IPv4 / hostnames split on the last ':' (if any).
    if authority.startswith("["):
        end_bracket = authority.find("]")
        host = authority[: end_bracket + 1] if end_bracket > 0 else authority
    elif ":" in authority:
        host = authority.rsplit(":", 1)[0]
    else:
        host = authority
    # Host MUST be a fully literal token — any f-string ``{...}`` placeholder
    # in the host portion means the host itself is dynamic, which can resolve
    # to any external destination → genuine SSRF risk.
    if "{" in host or "}" in host:
        return False
    return host in _LOOPBACK_HOSTS_PY


# ── Issue #41 — TOOL_SHADOW monkeypatch-in-test discriminator (Python) ──
# pytest's ``monkeypatch`` fixture is standard test scaffolding; the
# TOOL_SHADOW rule fires on the substring ``monkey?patch``. In a test file
# it is never tool-shadowing. The dangerous TOOL_SHADOW shapes
# (``__proto__ =``, ``override … tool``, ``Proxy(``) are different patterns
# that still fire.
def _is_pytest_monkeypatch(line: str, match: str) -> bool:
    m = (match or "").lower()
    return "monkeypatch" in m or "monkeypatch" in line.lower()


# ── Issue #41 — OBFUSCATION html.unescape discriminator (Python) ──
# ``html.unescape(...)`` is HTML-entity DECODING (rendering text readable),
# the opposite of obfuscation. The OBFUSCATION rule fires on the substring
# ``unescape(``. The bare/global ``unescape(`` (deprecated JS-style) stays
# flagged; only the qualified stdlib ``html.unescape`` is softened.
def _is_html_unescape(line: str, match: str) -> bool:
    return "html.unescape" in line


# Issue #42 — a string COMPILED as a regular expression is inert data: it
# describes a match, it is never executed. A scanner / validator that
# ships a pattern catalog (CPV's own ``cpv_skillaudit_*.py``,
# ``scan-for-prompt-injection.py`` — and any plugin doing input
# validation) has regex literals like ``r"curl.*\|.*sh"`` /
# ``r"eval\("`` / a sensitive-system-path matcher whose dangerous-looking
# substrings are the scanner's own vocabulary, not a live threat.
# Recognised by the two real shapes: a literal passed (directly or in a
# pure-literal container fed through a comprehension) to ``re.<func>(...)``.
_RE_PATTERN_FUNCS: Final[frozenset[str]] = frozenset(
    {"compile", "match", "search", "fullmatch", "sub", "subn", "split", "findall", "finditer"}
)


def _is_re_module_pattern_call(node: ast.AST) -> bool:
    """True iff ``node`` is a ``re.<func>(...)`` call on the stdlib ``re``
    module (the receiver must literally be the name ``re`` — third-party
    ``regex.compile`` and aliased imports are intentionally excluded so the
    discriminator stays conservative)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _RE_PATTERN_FUNCS
        and isinstance(func.value, ast.Name)
        and func.value.id == "re"
    )


def _re_literal_feeds_exec_sink(tree: ast.AST, target: ast.Constant) -> bool:
    """True iff the regex-pattern Constant ``target`` is consumed by a dangerous
    exec sink — ``os.system``/``eval``/``exec``/… directly, or ``subprocess.*``/
    ``asyncio.create_subprocess_*`` with ``shell=True``.

    A regex pattern is normally inert (suppressible), but the moment its string
    is fed into a shell/exec sink (``subprocess.run(re.compile(r"…").pattern,
    shell=True)``) it IS executed, so the suppression must not apply. (audit
    MAJOR #9)
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        qn = _node_qualname(node.func)
        if qn is None:
            continue
        # The pattern Constant must live inside one of the sink's positional args.
        if not any(sub is target for arg in node.args for sub in ast.walk(arg)):
            continue
        if qn in _STRING_CMD_EXEC_FQNAMES:
            return True
        if qn in _SHELL_CALL_FQNAMES and _shell_kwarg_is_true(node):
            return True
    return False


def _match_inside_re_pattern_literal(tree: ast.AST, line: int, source: str, match: str) -> bool:
    """True iff ``match`` sits inside a string Constant that is a regex
    PATTERN literal (issue #42).

    Two precise shapes are accepted (no looser "an re.compile is somewhere
    in this statement" heuristic, which would over-match):

    1. The Constant is within the FIRST positional argument subtree of a
       ``re.<func>(...)`` call — e.g. ``re.compile(r"curl.*\\|.*sh")`` or
       ``re.match("a" "b", x)``.
    2. The Constant is an element of a pure-literal container (List / Tuple
       / Set, possibly nested) that is the ``.iter`` of a comprehension
       whose ``.elt`` is a ``re.<func>(...)`` call — e.g.
       ``tuple(re.compile(p) for p in (r"readFile", r"fs\\.read", …))``.

    A regex pattern is never executed, so suppressing a dangerous-looking
    substring inside it is safe: any actual misuse would live in the CODE
    that consumes the compiled pattern (a ``.sub`` into an ``exec`` sink,
    etc.), which is separate, still-visible code.
    """
    if not source:
        return False
    lines = source.splitlines()
    if not (0 <= line - 1 < len(lines)):
        return False
    if match and match not in lines[line - 1]:
        return False

    target: ast.Constant | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is not None and end is not None and start <= line <= end and (not match or match in node.value):
                target = node
                break
    if target is None:
        return False

    # The pattern is inert ONLY if it is not consumed by an exec sink on the
    # same statement. ``subprocess.run(re.compile(r"…").pattern, shell=True)``
    # executes the pattern string, so it must stay visible. (audit MAJOR #9)
    if _re_literal_feeds_exec_sink(tree, target):
        return False

    # Shape 1 — target inside the first arg of an re.<func>(...) call.
    for node in ast.walk(tree):
        if _is_re_module_pattern_call(node):
            args = node.args  # type: ignore[attr-defined]
            if args and any(sub is target for sub in ast.walk(args[0])):
                return True

    # Shape 2 — target in the pure-literal iter of a comprehension whose
    # elt compiles a regex.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            continue
        if not _is_re_module_pattern_call(node.elt):
            continue
        for gen in node.generators:
            if any(sub is target for sub in ast.walk(gen.iter)):
                return True

    return False


def classify(
    file_path: str,
    source: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> ContextVerdict:
    """Classify a SkillAudit match in a Python source file.

    Args:
        file_path: absolute or repo-relative path. Used only for the
            ``.py`` extension check and (eventually) for test-file
            heuristics. Pass an empty string when calling from a
            content-only scanner — the function still returns a
            best-effort verdict.
        source: full file contents, as a single string.
        line_idx: zero-based line index where the regex match landed.
        match: the literal substring the regex captured.
        rule_id: the SkillAudit rule id (``CMD_INJECTION``,
            ``SHELL_EXEC``, ``INSECURE_CRYPTO``, ``TIME_BOMB``, etc.).

    Returns:
        One of:
          * ``"safe_literal"`` — surrounded by an all-literal shell call;
            zero injection surface. Caller should ``suppress``.
          * ``"safe_doc"`` — inside a docstring / data string / full-line
            comment. Caller should ``suppress``.
          * ``"suspect"`` — surrounded by a non-literal-argv call OR
            ``shell=True`` with non-literal argument. Caller should
            ``keep`` at declared severity.
          * ``"unknown"`` — could not classify (parse failure, no
            enclosing call, match outside any AST node). Caller falls
            through to the existing heuristic chain.
    """
    # Cheap fast-path: full-line comment detection without parse (parse is
    # ~50× slower). Issue #40: a Python ``#`` comment is NEVER executed, so
    # an execution-class rule matched inside one is a provable non-threat →
    # suppress (safe_literal). A prose-vector rule (prompt-injection / exfil
    # instruction / hardcoded secret / invisible unicode) stays VISIBLE
    # (safe_doc → demote) — a careless agent or grep-loader could surface it.
    lines = source.splitlines()
    if 0 <= line_idx < len(lines) and _line_is_full_comment(lines[line_idx]):
        return "safe_doc" if _rule_is_prose_vector(rule_id) else "safe_literal"

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Source isn't valid Python — could be a .py file with macros, a
        # partial snippet, or a stale parse. Iron rule: fall through.
        return "unknown"

    line = line_idx + 1  # ast uses 1-based line numbers

    # Issue #42 — a match inside a regex PATTERN literal (compiled via
    # ``re.<func>(...)``) is the scanner's own detection vocabulary, not a
    # live threat. Applies to ALL rules: a pattern string is inert data
    # whether it looks like a shell command, a prompt-injection phrase, or
    # a secret format — ``re`` compiles it for MATCHING, never executes it.
    # Any real misuse lives in the code that CONSUMES the compiled pattern
    # (a ``.sub`` result fed to ``exec``, etc.), which is separate,
    # still-visible code. Runs before the call-shape checks: a regex line's
    # enclosing call is ``re.compile``, never a shell call, so this cannot
    # shadow a real ``os.system(...)`` / ``subprocess(... shell=True)``.
    if _match_inside_re_pattern_literal(tree, line, source, match):
        return "safe_literal"

    # PRIMARY PATH: find the enclosing Call. A line that contains a
    # shell-reaching call SHAPE must be classified by that shape — the
    # call-site verdict takes precedence over any "this line happens
    # to contain a string Constant" verdict. (Previously the
    # string-literal check ran first and shadowed every realistic
    # call-site classification because shell calls always carry literal
    # string args.)
    # Rule-specific INSECURE_CRYPTO pre-check — works even when the
    # matched line contains a nested call chain (the outermost call may
    # be ``.hexdigest()`` whose qualname is None; the weak-hash call is
    # nested inside). Walk every covering Call.
    if rule_id == "INSECURE_CRYPTO":
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is None or end is None or not (start <= line <= end):
                continue
            qn = _node_qualname(node.func)
            if qn in _WEAK_HASH_FQNAMES:
                if _weak_hash_is_identity_usage(source, line_idx):
                    return "safe_literal"
                break  # weak-hash call found but not identity context; let the regex match stand

    # Issue #39 — for CMD_INJECTION / SHELL_EXEC rules, prefer the
    # INNER shell-reaching call when one exists. This fixes the FP
    # shape where the matched line is an f-string that wraps a
    # subprocess.run([...]) call, e.g.
    #
    #     lines.append(f"Generated: {subprocess.run(['date', '+%Y%m%d'],
    #                                              capture_output=True).stdout.strip()}")
    #
    # The OUTERMOST call (lines.append) spans the matched line, but
    # its qualname is not in _SHELL_CALL_FQNAMES — so the original
    # _find_enclosing_call returned None for classifier purposes, and
    # the safe inner subprocess.run([...]) was never classified.
    # _find_enclosing_shell_call picks the deepest shell-reaching call
    # specifically; if it returns one, classify by THAT call's argv
    # shape (typically safe_literal for list-form argv).
    if rule_id in {"CMD_INJECTION", "SHELL_EXEC"}:
        inner = _find_enclosing_shell_call(tree, line)
        if inner is not None:
            qn = _node_qualname(inner.func)
            if qn is not None:
                v = _classify_call(inner, qn)
                if v is not None:
                    return v

    call = _find_enclosing_call(tree, line)
    if call is not None:
        qualname = _node_qualname(call.func)
        if qualname is not None:
            verdict = _classify_call(call, qualname)
            if verdict is not None:
                return verdict
        # Enclosing call exists but its qualname or shape doesn't fit
        # the known shell/exec patterns — fall through to the
        # string-literal / unknown branch.

    # Issue #39 — DESERIALIZATION FP: ruamel.yaml YAML(typ="rt") /
    # YAML(typ="safe") instance loaders are safe-by-default; the
    # match on yaml.load( is being conflated with PyYAML's
    # yaml.load() which IS unsafe. Detect the shape:
    #
    #     yaml = YAML(typ="rt")   # or "safe", or default-call YAML()
    #     yaml.indent(...)        # OR yaml.preserve_quotes = True (rt marker)
    #     ...
    #     data = yaml.load(f)
    #
    # When the local variable `yaml` is assigned from a YAML(...) Call
    # in the SAME function/module (and not reassigned to pyyaml), the
    # load is via ruamel.yaml's instance API → safe.
    if rule_id == "DESERIALIZATION" and _is_ruamel_yaml_safe_load(tree, source, line):
        return "safe_literal"

    # Issue #39 — CRED_ENV_READ FP: matched substring `credentials.json`
    # is part of a Path literal pointing at the user's OWN credential
    # store (e.g. `Path.home() / ".claude" / ".credentials.json"`).
    # That's a self-config read by a statusline / diagnostic script,
    # not credential theft. Detect:
    #
    #     <var> = Path.home() / "<dotdir>" / ".credentials.json"
    #
    # AST shape: line is an Assign whose value is a BinOp chain of
    # Path division operators with at least one Constant containing
    # ".credentials.json" AND a Path.home() Call upstream in the chain.
    if rule_id == "CRED_ENV_READ" and _is_self_credentials_path(tree, line, match):
        return "safe_literal"

    # Issue #39 — CMD_INJECTION FP: the matched substring (`| sh`,
    # `| bash`, `; curl`, etc.) is inside a pure-string Constant that
    # lives in a module-level pure-literal data structure (List of
    # Tuples of strings). Example from publish.py:
    #
    #     REQUIRED_TOOLS: list[tuple[str, str]] = [
    #         ("uvx", "curl -LsSf https://astral.sh/uv/install.sh | sh"),
    #         ...
    #     ]
    #
    # The string is data the program shows the user as an install
    # hint — never passed to a shell. AST shape: match position is
    # inside a Constant string that is reachable via List/Tuple/Set/
    # Dict containers from a module-level Assign/AnnAssign target.
    # Issue #41 — SUPPLY_CHAIN shares this FP shape: the matched substring
    # (``curl … | sh``, ``npm install … &&``) is an install-HINT string in a
    # module-level data structure (e.g. publish.py's
    # ``REQUIRED_TOOLS = [("uvx", "curl -LsSf https://astral.sh/uv/install.sh | sh"), …]``).
    # It is text the program SHOWS the user, never executed.
    if rule_id in {"CMD_INJECTION", "SUPPLY_CHAIN"} and _match_inside_module_data_literal(
        tree, line, source, match
    ):
        return "safe_literal"

    # Issue #39 — SECRET_* FP: synthetic test-fixture secret in a
    # Python test file. The fixture line constructs a fake key like
    #     secret = "sk-" + "a" * 24
    # and the matched substring is the obvious-fake constructed
    # constant (or its sample value in a trailing comment).
    if rule_id.startswith("SECRET_") and _is_python_test_file(file_path):
        if _is_synthetic_secret_construction(tree, line, source):
            return "safe_literal"
        if _is_obvious_fake_secret_string(match):
            return "safe_literal"

    # SECONDARY PATH 0: SSRF_ADVANCED false-positive on
    # ``<urly_name> = <pure_internal_call>(<args>)`` where the
    # right-hand side is a Call to a private/internal function
    # (starts with underscore) and the args are simple Names /
    # Subscripts. The matcher's regex matches "url =" PLUS any text
    # containing "user/input/req" — but the RHS Call shape is a
    # sanitiser / extractor, not a SSRF surface.
    if rule_id == "SSRF_ADVANCED":
        if _line_is_safe_internal_assignment(tree, line, source.splitlines()[line_idx]):
            return "safe_literal"

    line_text = lines[line_idx] if 0 <= line_idx < len(lines) else ""

    # Issue #41 — CROSS_TOOL_ACCESS FP: LLM-client API field name used as
    # Python function param / dict key / CLI flag (system_prompt=…,
    # context_window: int, "--system-prompt"). Domain vocabulary, not a
    # runtime data-grab (those use the retrieval patterns → different match).
    if rule_id == "CROSS_TOOL_ACCESS" and _is_api_field_name_match_py(line_text, match):
        return "safe_literal"

    # Issue #41 — SSRF_PATTERN FP: static localhost/metadata literal (e.g. a
    # test assertion ``== "http://localhost:1234/v1"`` or a config default).
    # Static destination → not attacker-controlled → not SSRF. Dynamic
    # (f-string / concatenation) stays visible — UNLESS the dynamic
    # interpolation only affects port/path on a loopback HOST literal
    # (issue #41 follow-up: a loopback host can't reach an external
    # destination regardless of how the port/path is computed).
    if rule_id == "SSRF_PATTERN" and (
        _ssrf_url_is_static_literal_py(line_text, match)
        or _ssrf_url_is_loopback_with_literal_host_py(line_text, match)
    ):
        return "safe_literal"

    # Issue #41 — TOOL_SHADOW FP: pytest ``monkeypatch`` fixture in a test
    # file. Standard scaffolding, never tool-shadowing. The dangerous
    # TOOL_SHADOW shapes (__proto__=, override…tool, Proxy() ) are separate
    # patterns that still fire.
    if rule_id == "TOOL_SHADOW" and _is_python_test_file(file_path) and _is_pytest_monkeypatch(line_text, match):
        return "safe_literal"

    # Issue #41 — OBFUSCATION FP: ``html.unescape(...)`` is HTML-entity
    # DECODING (the opposite of obfuscation). Bare global ``unescape(``
    # stays flagged.
    if rule_id == "OBFUSCATION" and _is_html_unescape(line_text, match):
        return "safe_literal"

    # Issue #40 — execution-class rule matched inside a true DOCSTRING
    # (module/class/function ``__doc__``). A docstring is never executed, so
    # an execution-class match is a provable non-threat → suppress. A
    # prose-vector rule stays visible (safe_doc → demote). Data strings
    # assigned to variables are NOT docstrings (handled by the multiline
    # path below as safe_doc → demote, since a data string could be used).
    if _is_inside_docstring(tree, line) and not _rule_is_prose_vector(rule_id):
        return "safe_literal"

    # SECONDARY PATH: match is inside a triple-quoted string literal
    # (docstring or multi-line data string)? → safe_doc.
    # Use the multi-line filter so single-line literals (which appear
    # on EVERY call site as args) don't shadow real call classification.
    if _is_inside_multiline_string_literal(tree, line):
        # Template-generator promotion: when the enclosing function
        # exists ONLY to return a string template (Python script
        # template, YAML/TOML/JSON template, etc.), the matched
        # ``subprocess.run`` / ``os.system`` / etc. inside the
        # template is GENERATED CODE — it will land in the produced
        # file and be validated there. Flagging it in the template
        # author file is a false positive that the SHELL_EXEC /
        # CMD_INJECTION rules surface as a NIT under the default
        # "safe_doc → demote for execution-class rules" policy.
        # Promote to ``safe_literal`` so the dispatcher SUPPRESSES it
        # at source instead of relying on hash-anchored manifest
        # suppression (which breaks on every line-shift edit).
        if rule_id in {"SHELL_EXEC", "CMD_INJECTION"} and _enclosing_function_is_template_generator(tree, line):
            return "safe_literal"
        return "safe_doc"

    return "unknown"


def _classify_call(call: ast.Call, qualname: str) -> ContextVerdict | None:
    """Classify a known shell/exec call by its argument shape.

    Returns ``None`` when the qualname is not a recognised
    shell-reaching / dynamic-exec function — the caller falls through.
    """

    # Dynamic exec / eval / compile — always SUSPECT unless argument is
    # a literal Constant. Even a literal-only exec is dangerous in most
    # contexts (think: stored payloads), but the existing rules already
    # handle that; for context-classifier purposes we treat literal-only
    # eval as safe_literal to dampen the most common test-fixture FP.
    if qualname in _DYNAMIC_EXEC_FQNAMES:
        if call.args and _arg_is_pure_literal(call.args[0]):
            return "safe_literal"
        return "suspect"

    # Shell-reaching calls — the central category.
    if qualname in _SHELL_CALL_FQNAMES:
        # subprocess.run/Popen/etc. shape — v2.100.0 FP-aware:
        #   safe_literal: first arg is a List/Tuple of all-safe elements
        #                 (Constant, Starred, Name, Subscript, Attribute,
        #                 or Call to known-safe coercion) AND no element
        #                 is an exploit shape (f-string, BinOp concat,
        #                 .format/.join Call), AND shell= is absent or
        #                 False.
        #   safe_literal: first arg is a single literal string (e.g.
        #                 os.system("clear")), AND shell= is absent or
        #                 a literal False. These get coerced to argv with
        #                 no expansion, so they're safe too.
        #   suspect:      first arg is exploit-shaped (f-string, BinOp
        #                 concat, .format/.join Call) OR shell= is
        #                 possibly-true (literal True, a non-literal value
        #                 like ``shell=use_shell``, or a ``**opts`` splat)
        #                 with a non-pure-literal first arg. (audit MAJOR #2)
        if not call.args:
            # Unusual: subprocess.run() with no positional arg. Suspect.
            return "suspect"

        first = call.args[0]

        if _shell_kwarg_is_possibly_true(call):
            # shell=True (or a non-literal shell= that could be truthy at
            # runtime, e.g. ``shell=use_shell``, or a ``**opts`` splat) is
            # the dangerous case: the args ARE routed through a shell, so
            # metacharacter interpretation applies. Only safe if the first
            # arg is a pure literal string (no attacker-controlled bytes
            # reach the shell). Any other first-arg shape stays suspect.
            #
            # CRITICAL (audit MAJOR #2): a bare ``shell=use_shell`` where
            # ``use_shell`` is truthy must NOT fall through to the "Python
            # guarantees no shell" branch below — static analysis cannot
            # prove the indirection is falsey, so we conservatively treat
            # it as a real shell call. ``_shell_kwarg_is_possibly_true``
            # returns False ONLY for an absent kwarg or a literal-falsey
            # ``shell=False``/``shell=0``/``shell=None``/``shell=""``.
            if _arg_is_pure_literal(first):
                return "safe_literal"
            # Issue #45 — when the ONLY shell-possibly-true signal is a
            # ``**kwargs`` splat (no explicit ``shell=`` keyword) AND the
            # first arg is a conventional argv shape (List/Tuple of
            # safe-shaped elements, or a bare Name/Subscript/Attribute
            # holding a list-of-strings by Python convention), suppress.
            # This matches Bandit B603 / ruff S603 behaviour: they don't
            # flag ``subprocess.run(cmd, **kwargs)`` either, because the
            # analyser cannot prove ``**kwargs`` carries ``shell=True``
            # AND a list-form argv with literal-or-safe elements cannot
            # carry shell metacharacters even if it did. Explicit
            # ``shell=True`` / ``shell=use_shell`` stays in the suspect
            # branch above.
            if _shell_signal_only_via_splat(call) and _first_arg_is_argv_safe_shape_py(first):
                return "safe_literal"
            return "suspect"

        # shell= absent / literal-False — Python guarantee: subprocess.run /
        # subprocess.Popen / subprocess.call WITHOUT shell=True passes
        # the args to ``execve``-family syscalls, NOT to a shell. There
        # is no shell metachar interpretation; semicolons, pipes,
        # dollar-signs, ``rm -rf /``, and even an absolute path like
        # ``> ETC_PASSWD`` would all be passed as raw bytes in argv.
        # Command injection in the "shell interprets attacker input"
        # sense IS NOT POSSIBLE in this branch — the rule's threat
        # model doesn't apply.
        #
        # We still flag the obvious exploit shape (f-string / literal
        # string + variable concat) because some downstream tooling
        # (e.g. shell=True introduced LATER, or a shell-style wrapper)
        # might consume the same string. Conservative: if the first
        # arg LOOKS like the canonical injection vehicle, keep the
        # finding; otherwise it's safe.
        if _arg_is_exploit_shape(first):
            return "suspect"
        # No exploit shape + shell != True → safe by Python semantics.
        return "safe_literal"

    # Weak-hash usage — INSECURE_CRYPTO. ``hashlib.md5`` / ``hashlib.sha1``
    # are also widely used for non-crypto identity (cache keys, dedupe
    # signatures, session IDs). Detect identity context: result is sliced,
    # OR assigned to a name in ``_IDENTITY_TARGET_NAMES``, OR chained
    # through ``.hexdigest()`` (truncated digests are by definition
    # non-cryptographic).
    if qualname in _WEAK_HASH_FQNAMES:
        # Walk up the AST surrounding this call by examining its
        # parent expression via ast.iter_fields is not directly
        # available; instead use _identity_hash_shape which inspects
        # the line's siblings/ancestors via the source-text helpers.
        # Here we report the call as safe iff it's part of a
        # .hexdigest() chain — most common identity pattern.
        # The caller has the full source; we re-walk locally.
        return None  # delegated to dispatcher (see _weak_hash_is_identity)

    # Calls outside the shell-reaching set don't fit this classifier.
    # Fall through to the heuristic chain.
    return None


def _weak_hash_is_identity_usage(source: str, line_idx: int) -> bool:
    """True iff the matched ``hashlib.<weak>(...)`` call is used for
    identity (cache key, session ID, dedupe signature), not for
    cryptographic security.

    Identity is certified by any of:

    * The digest is sliced (``[:N]``) — a truncated hash is a lookup key.
    * The result is chained through ``.hexdigest()`` AND assigned to a name in
      ``_IDENTITY_TARGET_NAMES`` (``cache_key``, ``etag``, …).
    * The weak-hash call is directly assigned to an identity name (AST shapes
      the line regex misses: ``AnnAssign`` / ``AugAssign`` / ``NamedExpr`` /
      tuple-unpack).

    A SECURITY-sensitive target (``*password*`` / ``*secret*`` / ``*token*`` /
    ``*credential*`` / ``*_key``) ALWAYS overrides → returns ``False`` (stays
    visible): weak-hashing such a value is the exact INSECURE_CRYPTO threat.

    A bare ``.hexdigest()`` with no slice and no identity target is NOT
    sufficient on its own — that was the false-negative the audit found
    (``password_digest = hashlib.md5(pw).hexdigest()`` was being suppressed).
    ``hashlib.md5(password.encode()).digest()`` (no hexdigest, no slice, no
    identity target) stays flagged too. (audit MAJOR #8)
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    line = line_idx + 1

    # The matched line must be covered by a weak-hash call, else nothing to do.
    if not any(
        isinstance(n, ast.Call)
        and _node_qualname(n.func) in _WEAK_HASH_FQNAMES
        and getattr(n, "lineno", None) is not None
        and getattr(n, "end_lineno", None) is not None
        and n.lineno <= line <= n.end_lineno  # type: ignore[operator]
        for n in ast.walk(tree)
    ):
        return False

    line_text = source.splitlines()[line_idx]
    # Simple LHS assignment target — captures the last component of a dotted
    # target (``self.cache_key`` → ``cache_key``). Good enough for the
    # security-override and the hexdigest-identity gate; the AST pass below
    # covers compound shapes (tuple-unpack / AnnAssign / NamedExpr).
    lhs_match = re.match(r"\s*(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\s*(?::[^=]+)?\s*=(?!=)", line_text)
    lhs_name = lhs_match.group(1) if lhs_match else ""

    # (override) security-sensitive target → NEVER identity usage.
    if lhs_name:
        lhs_lower = lhs_name.lower()
        if any(stem in lhs_lower for stem in _SECURITY_TARGET_STEMS):
            return False

    # (1) sliced digest anywhere on the line → truncated lookup key.
    if re.search(r"\)\s*\[\s*:\s*\d+\s*\]", line_text):
        return True
    # (2) hexdigest chain assigned to an identity-named target. Bare hexdigest
    #     WITHOUT an identity target is no longer sufficient (audit MAJOR #8).
    if ".hexdigest()" in line_text and lhs_name in _IDENTITY_TARGET_NAMES:
        return True

    # (3) weak-hash call directly assigned to an identity name — AST shapes the
    #     line regex misses (AnnAssign / AugAssign / NamedExpr / tuple-unpack).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        qual = _node_qualname(node.func)
        if qual not in _WEAK_HASH_FQNAMES:
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        for parent in ast.walk(tree):
            for _field, value in ast.iter_fields(parent):
                children = value if isinstance(value, list) else [value]
                for child in children:
                    if child is not node:
                        continue
                    if isinstance(parent, ast.Assign):
                        for tgt in parent.targets:
                            if isinstance(tgt, ast.Name) and tgt.id in _IDENTITY_TARGET_NAMES:
                                return True
                            if isinstance(tgt, ast.Tuple):
                                for elt in tgt.elts:
                                    if isinstance(elt, ast.Name) and elt.id in _IDENTITY_TARGET_NAMES:
                                        return True
                    if isinstance(parent, ast.AnnAssign):
                        if isinstance(parent.target, ast.Name) and parent.target.id in _IDENTITY_TARGET_NAMES:
                            return True
                    if isinstance(parent, ast.AugAssign):
                        if isinstance(parent.target, ast.Name) and parent.target.id in _IDENTITY_TARGET_NAMES:
                            return True
                    if isinstance(parent, ast.NamedExpr):
                        if isinstance(parent.target, ast.Name) and parent.target.id in _IDENTITY_TARGET_NAMES:
                            return True
    return False


def _line_is_safe_internal_assignment(tree: ast.AST, line: int, line_text: str) -> bool:
    """True iff ``line`` is an assignment of the form
    ``<name> = <internal_callable>(<simple_args>)`` where
    ``<internal_callable>`` is module-private (leading underscore) AND
    the args are pure-shape (Name / Subscript / Attribute / Constant).

    Matches the SSRF_ADVANCED FP shape

        clean_url = _strip_userinfo(url)

    where the regex matches the URL-keyword + "user" substring but
    the actual code is a sanitiser call. The matcher should not flag
    these — they are the OPPOSITE of a SSRF surface (sanitisation step).
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if getattr(node, "lineno", None) != line:
            continue
        val = node.value
        if not isinstance(val, ast.Call):
            continue
        # Function name must look "internal" — Name starting with _, or
        # an Attribute access on a Name.
        func = val.func
        if isinstance(func, ast.Name) and func.id.startswith("_"):
            # Args must be simple (no exploit shape).
            for a in val.args:
                if _arg_is_exploit_shape(a):
                    return False
            return True
        if isinstance(func, ast.Attribute):
            # foo._private(...) or self._private(...).
            attr_chain = func.attr
            if attr_chain.startswith("_"):
                for a in val.args:
                    if _arg_is_exploit_shape(a):
                        return False
                return True
    # Belt-and-suspenders: a textual heuristic for lines where the AST
    # walk didn't find an Assign (shadowed by surrounding scope).
    # If the line LOOKS like "<name> = _func_name(...)", trust it.
    if re.match(r"^\s*[A-Za-z_]\w*\s*=\s*[A-Za-z_]\w*\._?[A-Za-z_]+\s*\(", line_text):
        return False  # method call on object — needs deeper analysis
    if re.match(r"^\s*[A-Za-z_]\w*\s*=\s*_[A-Za-z_]+\s*\([^)f]*\)\s*$", line_text):
        return True
    return False


# ────────────────────────────────────────────────────────────────────────
# Issue #39 helpers (TRDD: closes #39)
# ────────────────────────────────────────────────────────────────────────

# The set of yaml-loader instance APIs that ARE safe by design.
#   ruamel.yaml.YAML(typ="rt")     — round-trip loader, never executes constructors
#   ruamel.yaml.YAML(typ="safe")   — explicit safe loader
#   ruamel.yaml.YAML()             — default is "rt"
# All three load via the instance method ``yaml.load(stream)``, not via
# the module-level ``yaml.load(stream)`` of PyYAML. The DESERIALIZATION
# rule's regex matches both call shapes — we need AST to distinguish.
_RUAMEL_SAFE_TYPES: Final[frozenset[str]] = frozenset({"rt", "safe", "base"})


def _is_ruamel_yaml_safe_load(tree: ast.AST, source: str, line: int) -> bool:
    """True iff ``line`` calls ``<var>.load(...)`` where ``<var>`` was
    assigned from ``YAML(typ="rt"|"safe"|"base")`` (or ``YAML()``) or
    from ``ruamel.yaml.YAML(...)`` in the same enclosing scope.

    Identifies the ruamel.yaml round-trip loader FP shape (issue #39):

        from ruamel.yaml import YAML
        yaml = YAML(typ="rt")
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            data = yaml.load(f)   # <-- matched by rule, but safe

    Conservative: returns True ONLY when an explicit ``YAML(...)`` call
    is found assigning the same variable name used in the matched
    ``.load(...)``. A plain ``import yaml; yaml.load(...)`` is PyYAML
    and stays suspect.
    """
    # 1. Find the Call on the matched line: <recv>.load(...).
    receiver_name: str | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "load":
            recv = func.value
            if isinstance(recv, ast.Name):
                receiver_name = recv.id
                break
    if receiver_name is None:
        return False

    # 2. Search the module for an assignment of that name to a
    #    YAML(...) constructor call.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        # Get the assignment target name.
        target_name: str | None = None
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == receiver_name:
                    target_name = tgt.id
                    break
        else:  # AnnAssign
            if isinstance(node.target, ast.Name) and node.target.id == receiver_name:
                target_name = node.target.id
        if target_name is None:
            continue
        val = node.value
        if not isinstance(val, ast.Call):
            continue
        # Accept `YAML(...)` (bare name), `ruamel.yaml.YAML(...)`,
        # `ryaml.YAML(...)` (any alias).
        f = val.func
        is_yaml_ctor = False
        if isinstance(f, ast.Name) and f.id == "YAML":
            is_yaml_ctor = True
        elif isinstance(f, ast.Attribute) and f.attr == "YAML":
            is_yaml_ctor = True
        if not is_yaml_ctor:
            continue
        # If no `typ=` kwarg, default is "rt" → safe.
        # If `typ=` is given, must be in _RUAMEL_SAFE_TYPES.
        typ_kw = None
        for kw in val.keywords:
            if kw.arg == "typ":
                typ_kw = kw.value
                break
        if typ_kw is None:
            return True  # default constructor is "rt" → safe
        if isinstance(typ_kw, ast.Constant) and typ_kw.value in _RUAMEL_SAFE_TYPES:
            return True
        # Other typ values ("unsafe", "full") → not safe; let the rule fire.
        return False
    return False


# Paths that, when used as a Path-literal in source, indicate the
# program reading its OWN credential file (not stealing someone
# else's). These are the canonical locations a statusline / diagnostic
# script reads. Exact basename match.
_SELF_CREDENTIAL_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        ".credentials.json",
        "credentials.json",
        "auth.json",
        ".auth.json",
        "token.json",
        ".token.json",
        ".npmrc",
        ".pypirc",
        ".gitconfig",
        ".netrc",
    }
)


def _is_self_credentials_path(tree: ast.AST, line: int, match: str) -> bool:
    """True iff the match `credentials.json` sits inside a Path literal
    that points at the program's OWN credentials store, not at an
    arbitrary external one.

    AST shape recognised: an Assign/AnnAssign on ``line`` whose value
    is a chained Path division (``Path.home() / "..." / ".credentials.json"``)
    where the last Constant element is ``.credentials.json``. Direct
    Constant ``"~/.claude/.credentials.json"`` also counts.

    Conservative: only the canonical basenames in
    ``_SELF_CREDENTIAL_BASENAMES`` qualify. A line referencing a
    third-party credential store like ``/etc/aws/credentials.json``
    where the path is hardcoded by the attacker would NOT match this
    helper (its basename is on the list, but the AST shape needs a
    Path.home()/cwd anchor — see _path_chain_has_home_anchor).
    """
    if "credentials.json" not in match and "credentials.json" not in (match or ""):
        # Light pre-filter; the dispatcher already matched the rule
        # before calling us. We don't gate on match-text — we gate on
        # AST shape.
        pass

    # Walk the assignment on this line.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        n_line = getattr(node, "lineno", None)
        if n_line != line:
            continue
        val = getattr(node, "value", None)
        if val is None:
            continue
        if _path_chain_has_home_anchor_and_safe_basename(val):
            return True
    # Also handle the case where the line is not an assignment but a
    # bare expression / function call argument that ends in a Path
    # chain (rare in practice).
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp):
            continue
        n_line = getattr(node, "lineno", None)
        e_line = getattr(node, "end_lineno", None)
        if n_line is None or e_line is None or not (n_line <= line <= e_line):
            continue
        if _path_chain_has_home_anchor_and_safe_basename(node):
            return True
    return False


def _path_chain_has_home_anchor_and_safe_basename(node: ast.AST) -> bool:
    """True iff ``node`` is a Path-division chain
    (``Path.home() / ... / "<basename>"``) whose terminal element is a
    Constant in ``_SELF_CREDENTIAL_BASENAMES`` AND whose root is a
    ``Path.home()`` / ``Path.cwd()`` / ``os.path.expanduser(...)`` Call.

    The chain may have any number of intermediate Constant string
    elements (``Path.home() / ".claude" / ".credentials.json"``). We
    walk the BinOp tree looking for: at least one Path.home() / cwd /
    expanduser anchor AND at least one Constant whose value is in
    the safe basename set, AND no anchor-resetting component.

    A component RESETS the pathlib anchor (so the path escapes home) when it is
    an absolute Constant (``Path.home() / "/etc" / ".credentials.json"`` ==
    ``/etc/.credentials.json``) OR a non-Constant variable (``Path.home() /
    user_dir / ".credentials.json"`` where ``user_dir`` may be absolute). If any
    such component is present we do NOT certify the path safe — the finding stays
    visible for the agent to triage. (audit MINOR #14)
    """
    has_home_anchor = False
    has_safe_basename = False
    has_unsafe_component = False

    def visit(n: ast.AST) -> None:
        nonlocal has_home_anchor, has_safe_basename, has_unsafe_component
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            base = n.value.rsplit("/", 1)[-1]
            if base in _SELF_CREDENTIAL_BASENAMES:
                has_safe_basename = True
            # An absolute-path Constant resets the anchor → escapes home.
            if n.value.startswith("/"):
                has_unsafe_component = True
            return
        if isinstance(n, ast.Call):
            f = n.func
            # Path.home() / Path.cwd()
            if isinstance(f, ast.Attribute) and f.attr in {"home", "cwd"}:
                if isinstance(f.value, ast.Name) and f.value.id == "Path":
                    has_home_anchor = True
                else:
                    has_unsafe_component = True
            # os.path.expanduser(...) / Path(...).expanduser()
            elif isinstance(f, ast.Attribute) and f.attr == "expanduser":
                has_home_anchor = True
            else:
                # Any other call returns an unknown (possibly absolute) value.
                has_unsafe_component = True
            return
        if isinstance(n, ast.BinOp):
            visit(n.left)
            visit(n.right)
            return
        # Name / Subscript / Attribute / etc.: a variable component that may
        # resolve to an absolute path and reset the anchor, escaping home.
        has_unsafe_component = True

    visit(node)
    return has_home_anchor and has_safe_basename and not has_unsafe_component


def _match_inside_module_data_literal(
    tree: ast.AST, line: int, source: str, match: str
) -> bool:
    """True iff ``match`` text on ``line`` is inside a pure-string
    Constant that is reachable via pure-literal containers
    (List / Tuple / Set / Dict / nested) from a module-level
    Assign / AnnAssign target.

    Catches the publish.py FP:

        REQUIRED_TOOLS: list[tuple[str, str]] = [
            ("uvx", "curl -LsSf https://astral.sh/uv/install.sh | sh"),
            ...
        ]

    A `| sh` substring inside the constant is data — never executed.

    Conservative: ALL elements in the container chain must be
    pure-string / pure-numeric Constants OR nested pure-literal
    containers. Any Name / Call / variable injection breaks the
    "pure data" property and the helper returns False.
    """
    if not source:
        return False
    lines = source.splitlines()
    if not (0 <= line - 1 < len(lines)):
        return False
    line_text = lines[line - 1]
    if match and match not in line_text:
        return False

    # Find the deepest Constant string that covers `line` AND contains
    # `match` in its `.value`. Strings inside containers may span one
    # or many lines; we accept any covering Constant.
    target_const: ast.Constant | None = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        if not match or match in node.value:
            target_const = node
            break
    if target_const is None:
        return False

    # Walk the AST and check whether `target_const` is a descendant of
    # a module-level Assign/AnnAssign whose value is a pure-literal
    # container tree.
    return _node_is_in_module_level_pure_data_assign(tree, target_const)


def _node_is_in_module_level_pure_data_assign(tree: ast.AST, target: ast.AST) -> bool:
    """True iff ``target`` is reachable via pure-literal containers
    from the value of a Module-level Assign / AnnAssign.

    REQUIRES the assign's value to be a container (List / Tuple / Set
    / Dict) — a bare Constant value (single triple-quoted string
    assigned to a CONSTANT_NAME) does NOT qualify, because a
    triple-quoted documentation string is the canonical safe_doc
    shape that the multi-line-string-literal check already handles.
    Treating it as safe_literal here would short-circuit the
    safe_doc path and break callers that depend on the historical
    "documentation string with embedded subprocess.run example" →
    safe_doc verdict.
    """

    def _is_pure_literal_data(n: ast.AST) -> bool:
        if isinstance(n, ast.Constant):
            return True
        if isinstance(n, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_pure_literal_data(e) for e in n.elts)
        if isinstance(n, ast.Dict):
            return all(
                _is_pure_literal_data(k) and _is_pure_literal_data(v)
                for k, v in zip(n.keys, n.values, strict=False)
                if k is not None  # **kwargs unpacking → not pure
            )
        return False

    def _contains_target(n: ast.AST, t: ast.AST) -> bool:
        if n is t:
            return True
        for child in ast.iter_child_nodes(n):
            if _contains_target(child, t):
                return True
        return False

    # Module body only — class/function-scope assignments don't
    # qualify (they're runtime data, but the literal-shape guarantee
    # only holds when the module-level binding is the single source).
    if not isinstance(tree, ast.Module):
        return False
    for stmt in tree.body:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        val = stmt.value
        if val is None:
            continue
        # Iron rule: only CONTAINER literals qualify, not bare
        # Constant strings. A `CONSTANT = """..."""` triple-quoted
        # string is documentation; the safe_doc multi-line check
        # owns that case.
        if not isinstance(val, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            continue
        if not _is_pure_literal_data(val):
            continue
        if _contains_target(val, target):
            return True
    return False


# Path / basename markers that identify a Python test file. Used by
# the SECRET_* heuristic to scope synthetic-secret-fixture recognition.
def _is_python_test_file(file_path: str) -> bool:
    """True iff ``file_path`` looks like a Python test or fixture
    file. Accepts basenames starting with ``test_`` and ending with
    ``.py``; ``conftest.py``; ``*_test.py``; or any path containing a
    ``tests``/``test``/``__tests__``/``fixtures``/``__mocks__`` dir."""
    fp = file_path.replace("\\", "/").lower()
    if not fp:
        return False
    parts = fp.split("/")
    base = parts[-1]
    if not base.endswith(".py"):
        return False
    if base.startswith("test_") or base.endswith("_test.py") or base == "conftest.py":
        return True
    for d in ("tests", "test", "__tests__", "fixtures", "__fixtures__", "__mocks__", "mocks"):
        if d in parts:
            return True
    return False


_OBVIOUS_FAKE_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        r"\bsk-(?:proj-)?([A-Za-z0-9])\1{15,}\b",
        r"\bsk-(?:proj-)?(?:0123456789|1234567890|abcdef|deadbeef|test|fake|dummy|sample|example)",
        r"\bsk-(?:proj-)?(?:[0-9]{1,5}[a-f]{1,5}){2,}\b",
    )
)


def _is_obvious_fake_secret_string(match: str) -> bool:
    """True iff ``match`` is a synthetic test-fixture secret —
    ``sk-aaaa…``, ``sk-1234567890``, ``sk-deadbeef…``, etc. — that
    a human can immediately recognise as test data.

    Mirrors ``_skillaudit_typescript_context._FAKE_SECRET_PATTERNS``
    so Python and TS test files share the same synthetic-secret
    grammar. The classifier only applies this in Python test files
    (gated by ``_is_python_test_file`` in the caller).
    """
    if not match:
        return False
    return any(p.search(match) for p in _OBVIOUS_FAKE_SECRET_PATTERNS)


def _is_synthetic_secret_construction(tree: ast.AST, line: int, source: str) -> bool:
    """True iff ``line`` is an assignment whose RHS constructs a
    synthetic-looking secret from string-concat / string-multiply
    operations involving only literal strings.

    Recognises shapes like:

        secret = "sk-" + "a" * 24
        token  = "sk-test-" + "1" * 30
        key    = "sk-proj-" + "deadbeef" * 4

    These are the canonical Python test-fixture patterns for "build a
    string that LOOKS like a real secret to feed to the scanner". The
    classifier walks the BinOp chain and confirms every operand is
    pure-literal (Constant string or Constant int) — any variable
    reference disqualifies (real secrets read from env or config
    would have a Name/Attribute node somewhere in the RHS).
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if getattr(node, "lineno", None) != line:
            continue
        val = getattr(node, "value", None)
        if val is None:
            continue
        if _expr_is_pure_string_arithmetic(val):
            return True
    return False


def _expr_is_pure_string_arithmetic(node: ast.AST) -> bool:
    """True iff ``node`` is a tree of BinOp / Constant / safe coercion
    operations that produces a string at runtime AND contains no
    variable / function-call inputs other than `str(...)` /
    `bytes(...)` coercions of literal constants.

    Used to distinguish synthetic secret-fixture construction
    (``"sk-" + "a" * 24``) from real env-driven secret assembly
    (``"sk-" + os.environ["X"]`` — would have a Subscript node).
    """
    if isinstance(node, ast.Constant):
        # Strings, ints, bytes all qualify.
        return isinstance(node.value, (str, int, bytes))
    if isinstance(node, ast.BinOp):
        return _expr_is_pure_string_arithmetic(node.left) and _expr_is_pure_string_arithmetic(node.right)
    if isinstance(node, ast.JoinedStr):
        # f-string with only pure-literal interior counts.
        return all(_expr_is_pure_string_arithmetic(v) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return _expr_is_pure_string_arithmetic(node.value)
    if isinstance(node, ast.Call):
        # Only safe coercions of pure-literal args.
        f = node.func
        if isinstance(f, ast.Name) and f.id in {"str", "bytes", "int", "float", "repr"}:
            return all(_expr_is_pure_string_arithmetic(a) for a in node.args)
        return False
    return False
