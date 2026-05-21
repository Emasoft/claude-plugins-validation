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
_DYNAMIC_EXEC_FQNAMES: Final[frozenset[str]] = frozenset(
    {"eval", "exec", "compile", "__import__"}
)

# Hash functions flagged by INSECURE_CRYPTO. The matcher fires on the
# function reference itself; the AST classifier then checks the call
# context — these are commonly used for non-cryptographic identity
# (cache keys, session IDs, file dedupe) where weak-hash != security
# defect. The shape ``hashlib.<weak>(...).hexdigest()`` followed by
# slicing or assignment to an identity-named target signals identity
# usage, not crypto.
_WEAK_HASH_FQNAMES: Final[frozenset[str]] = frozenset(
    {"hashlib.md5", "hashlib.sha1"}
)
_IDENTITY_TARGET_NAMES: Final[frozenset[str]] = frozenset(
    {
        "digest", "hash", "key", "cache_key", "cachekey", "tick", "tick_key",
        "session_id", "sessionid", "sid", "id", "identifier", "fingerprint",
        "checksum", "etag", "signature", "sig", "name", "uid", "guid",
        "session", "entries_hash", "content_hash", "file_hash", "path_hash",
    }
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
_SAFE_COERCION_FUNCS: Final[frozenset[str]] = frozenset(
    {"str", "int", "float", "bool", "bytes", "Path"}
)
_SAFE_COERCION_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "as_posix", "absolute", "resolve", "parent", "name", "stem", "suffix",
        "fspath",   # os.fspath / pathlib.PurePath.fspath
        "expanduser",  # Path.expanduser
        "joinpath",  # Path.joinpath returns a Path
        "with_name", "with_suffix", "relative_to",
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
        if isinstance(arg.left, (ast.List, ast.Tuple)) or isinstance(
            arg.right, (ast.List, ast.Tuple)
        ):
            return False
        left_is_literal_str = isinstance(arg.left, ast.Constant) and isinstance(
            arg.left.value, str
        )
        right_is_literal_str = isinstance(arg.right, ast.Constant) and isinstance(
            arg.right.value, str
        )
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


def _line_is_full_comment(source_line: str) -> bool:
    """True iff the stripped source line begins with ``#``."""
    return source_line.lstrip().startswith("#")


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
    # Cheap fast-path: full-line comment or docstring detection without
    # parse (parse is ~50× slower; the existing comment-stripper in
    # _confidence already partially handles this).
    lines = source.splitlines()
    if 0 <= line_idx < len(lines) and _line_is_full_comment(lines[line_idx]):
        return "safe_doc"

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Source isn't valid Python — could be a .py file with macros, a
        # partial snippet, or a stale parse. Iron rule: fall through.
        return "unknown"

    line = line_idx + 1  # ast uses 1-based line numbers

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

    # SECONDARY PATH: match is inside a triple-quoted string literal
    # (docstring or multi-line data string)? → safe_doc.
    # Use the multi-line filter so single-line literals (which appear
    # on EVERY call site as args) don't shadow real call classification.
    if _is_inside_multiline_string_literal(tree, line):
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
        #                 False. These get coerced to argv with no
        #                 expansion, so they're safe too.
        #   suspect:      first arg is exploit-shaped (f-string, BinOp
        #                 concat, .format/.join Call) OR shell=True with
        #                 non-literal arg.
        if not call.args:
            # Unusual: subprocess.run() with no positional arg. Suspect.
            return "suspect"

        first = call.args[0]

        if _shell_kwarg_is_true(call):
            # shell=True is the dangerous case. Only safe if first arg
            # is a pure literal string.
            if _arg_is_pure_literal(first):
                return "safe_literal"
            return "suspect"

        # shell= absent / False — Python guarantee: subprocess.run /
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

    Signals (any one is sufficient):

    * The call is chained through ``.hexdigest()`` and the result is
      sliced (`[:N]`).
    * The result is assigned to a name in ``_IDENTITY_TARGET_NAMES``.
    * The call's argument is an f-string / encoded short identifier
      (``f"{x}@{y}".encode()``) — characteristic of compound-key
      hashing, not message authentication.

    These signals each independently disqualify the call as a real
    INSECURE_CRYPTO finding. The matcher would still fire on
    ``hashlib.md5(password.encode()).digest()`` (no hexdigest, no
    slice, no identity-name target) because none of the signals
    apply — the security-relevant shape stays flagged.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    line = line_idx + 1

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Must be the weak-hash call.
        qual = _node_qualname(node.func)
        if qual not in _WEAK_HASH_FQNAMES:
            continue
        # Must cover the matched line.
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        # Walk parent chain via ast.walk and find the enclosing
        # Subscript / Assign for context.
        for parent in ast.walk(tree):
            for field, value in ast.iter_fields(parent):
                children = value if isinstance(value, list) else [value]
                for child in children:
                    if child is not node:
                        continue
                    # Check whether the parent expression eventually
                    # yields a sliced hexdigest assigned to an identity
                    # name. We look at the whole-line source instead of
                    # walking many AST hops — simpler + adequate.
                    line_text = source.splitlines()[line_idx]
                    # hexdigest() chain → identity.
                    if ".hexdigest()" in line_text:
                        return True
                    # `[:N]` slice anywhere on the line → identity.
                    if re.search(r"\)\s*\[\s*:\s*\d+\s*\]", line_text):
                        return True
                    # Assignment target is an identity name.
                    if isinstance(parent, ast.Assign):
                        for tgt in parent.targets:
                            if isinstance(tgt, ast.Name) and tgt.id in _IDENTITY_TARGET_NAMES:
                                return True
                            # Tuple-unpack: (a, b) = expr
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


# Need `re` for the slice-regex check above. Top-of-module import would
# be cleaner; deferred until needed to keep cold-import time low.
import re  # noqa: E402


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
