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
import codecs
import io
import re
import tokenize
from typing import Final, Literal

from _skillaudit_markdown_context import _is_charset_detection_vocab  # type: ignore[import-not-found]

ContextVerdict = Literal["safe_literal", "safe_doc", "code_fence_neutral", "suspect", "unknown"]

# r03 FP iter (2026-05-28) — env-var names whose presence as a string
# literal in the surrounding scope means a dynamic ``os.environ[var] = …``
# could be setting a runtime-hijack var → keep visible.
_PY_ENV_HIJACK_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    r"['\"](?:LD_PRELOAD|LD_LIBRARY_PATH|DYLD_INSERT_LIBRARIES|DYLD_LIBRARY_PATH|"
    r"NODE_OPTIONS|PYTHONPATH|PYTHONSTARTUP|PYTHONHOME|PERL5OPT|PERL5LIB|RUBYOPT|"
    r"RUBYLIB|BASH_ENV|GIT_SSH_COMMAND|GCONV_PATH|CLASSPATH|PATH|IFS)['\"]"
)
# A dynamic ``os.environ[<identifier>] =`` assignment (variable key, not a
# string literal).
_PY_ENV_DYNAMIC_KEY_RE: Final[re.Pattern[str]] = re.compile(r"os\.environ\s*\[\s*[A-Za-z_]\w*\s*\]\s*=")
_PY_ENV_READ_RE: Final[re.Pattern[str]] = re.compile(r"os\.environ\.get\s*\(|os\.environ\s*\[\s*[A-Za-z_]")


def _is_env_read_modify_write(lines: list[str], line_idx: int) -> bool:
    """True iff an ENV_INJECTION match is a READ-MODIFY-WRITE of an env var
    via a DYNAMIC (variable) key — ``os.environ[var] = <transform of the
    existing value>`` — with NO runtime-hijack-var name appearing as a
    string literal anywhere in the ±8-line window.

    This is the canonical benign shape (e.g. stripping a host from
    ``NO_PROXY``): the value is derived from the env's own current value,
    not attacker input, and the key is provably not a hijack var (none is
    named nearby). A literal hijack-var key, or a hijack-var literal in
    context, keeps the finding visible (iron rule)."""
    if not (0 <= line_idx < len(lines)):
        return False
    if not _PY_ENV_DYNAMIC_KEY_RE.search(lines[line_idx]):
        return False
    lo = max(0, line_idx - 8)
    hi = min(len(lines), line_idx + 8)
    window = "\n".join(lines[lo:hi])
    if _PY_ENV_HIJACK_LITERAL_RE.search(window):
        return False
    # Require a same-window env READ (the "read" half of read-modify-write).
    # The write line itself (``os.environ[k] = …``) matches the second
    # alternative of ``_PY_ENV_READ_RE`` (``os.environ[<ident>``), so it would
    # self-satisfy this check for EVERY dynamic-key write — including a fully
    # attacker-controlled ``os.environ[user_key] = user_value`` with no real
    # read anywhere. Exclude the matched write line so the "read" half must
    # come from a genuinely different line (the canonical shape reads the env
    # value on a prior line, e.g. ``v = os.environ.get('NO_PROXY')``).
    read_window = "\n".join(ln for i, ln in enumerate(lines[lo:hi], start=lo) if i != line_idx)
    return bool(_PY_ENV_READ_RE.search(read_window))


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

# Rules whose ENTIRE concern is the call's INJECTION SURFACE. A fixed,
# non-interpolated argv (``_classify_call`` → ``safe_literal``) genuinely
# precludes injection, so a benign call SHAPE may suppress exactly these.
# EVERY OTHER rule fires on the call ARGUMENT'S CONTENT, which this very
# call EXECUTES — a literal ``os.system('bash -i >& /dev/tcp/…')`` is a
# reverse shell, a literal ``os.system('cat ~/.ssh/id_rsa | nc …')`` is
# credential exfiltration — regardless of having no injection surface. The
# call-shape verdict must NEVER suppress those (security invariant: "never
# silently suppress malicious code"; a literal payload is still executed).
# Deliberately SMALL — a rule omitted here merely stays VISIBLE (over-flag),
# never hidden.
_CALL_SHAPE_SUPPRESSIBLE_RULES: Final[frozenset[str]] = frozenset({"CMD_INJECTION", "SHELL_EXEC"})

# Config shapes whose occurrence INSIDE a string/bytes literal is PROVABLY data,
# never a live config — so suppression carries ZERO false-negative risk. Issue
# #65/#67: ``"…verify=False / --insecure…"`` is a TLS-bypass SIGNATURE / description
# string in a security plugin's pattern library, NOT a live ``verify=False`` kwarg
# (that exists only as real code, never inside a quoted string). Deliberately does
# NOT include CMD_INJECTION / SHELL_EXEC — those are FLOW-SENSITIVE (a string CAN be
# a payload that reaches a sink: ``f"…"`` → ``shell=True``), so the existing
# AST-flow logic owns them (data string → demote-visible, sink-flowing → keep).
_LITERAL_DATA_SUPPRESSIBLE_RULES: Final[frozenset[str]] = frozenset({"INSECURE_TLS"})

# Content-THREAT rules that fire on the PRESENCE of a fixed substring (a header
# name, a CLI-command name, a privilege keyword) whose live form IS that string fed
# to an exec / shell / subprocess sink — so an in-string occurrence is suppressible
# ONLY when the literal does NOT flow to such a sink. Issue #65:
#   * PRIVILEGE_ESC fires on ``sudo\s`` / ``setuid`` / ``chmod +s``; a benign install
#     help-hint (``HINT = "sudo usermod -aG docker $USER"``) is inert text, but the
#     IDENTICAL ``sudo `` substring inside ``os.system("sudo …")`` /
#     ``subprocess.run("sudo …", shell=True)`` IS a live escalation.
#   * TOKEN_STEAL fires on ``Authorization:\s*Bearer`` etc.; a benign git-config
#     literal (``GIT_CONFIG_VALUE_0 = "AUTHORIZATION: bearer {token}"``) is data, but
#     ``os.system("curl -H 'Authorization: Bearer …' …")`` flows to a shell sink and
#     stays a live exfil.
#   * CLAUDE_CLI_TOKEN_THEFT fires on ``\bclaude\s+setup-token\b``; a benign
#     ``print("Run `claude setup-token`")`` help string is data, but
#     ``subprocess.run("claude setup-token | nc …", shell=True)`` is a live theft.
# The discriminator is therefore SINK-AWARE for ALL of them
# (``_string_literal_match_is_inert_no_sink`` → ``_path_literal_feeds_fs_or_exec_sink``):
# suppress the inert data string, keep the sink-flowing one visible. The "slightest
# possibility the string is executed, not merely compared" → keep it. NEVER add a
# flow-sensitive INJECTION rule (CMD_INJECTION / SHELL_EXEC) here — those are owned
# end-to-end by the call-shape AST logic, which already demotes a data string and
# keeps a sink-flowing one. (``_string_literal_match_is_inert_no_sink`` checks only a
# DIRECT-argument sink flow; an INJECTION literal bound to a variable that then reaches
# a sink — ``cmd = "curl … | sh"; subprocess.run(cmd, shell=True)`` — would slip through
# this set as a false-negative. Issue #100 class B routes the single-line eval/exec
# finding-MESSAGE FP through the dedicated, INDIRECTION-AWARE
# ``_eval_exec_message_string_is_inert`` helper instead, which closes that hole.)
_SINK_GUARDED_LITERAL_SUPPRESSIBLE_RULES: Final[frozenset[str]] = frozenset(
    {"PRIVILEGE_ESC", "TOKEN_STEAL", "CLAUDE_CLI_TOKEN_THEFT"}
)

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

# ALWAYS-shell string sinks — the subset of ``_SHELL_CALL_FQNAMES`` that has NO
# non-shell mode: each routes its argument string through ``/bin/sh -c``
# UNCONDITIONALLY and takes NO ``shell=`` kwarg. The "shell= absent → Python
# passes argv straight to ``execve``, no shell" guarantee that
# ``_classify_call`` relies on is TRUE for ``subprocess.run([...])`` /
# ``Popen([...])`` / ``os.execv(...)`` (argv-form, no shell) but FALSE for these
# — so the leniency must NOT be applied to them. Only a PURE-literal arg
# (``os.system("clear")``) is provably inert; ANY non-pure-literal arg (a bare
# ``Name`` reassembled via ``.join``/concat on a prior line, a ``Call`` returning
# the built string, a ``Subscript``/``Attribute``) is a live shell execution and
# MUST stay ``suspect`` — reaching parity with the already-correct
# ``subprocess.run(<Name>, shell=True)`` path. (RT2/RT4 FN-holes — never-suppress.)
# Derived from the named always-shell set above (excluding the dynamic
# eval/exec/compile members, which have their own ``_DYNAMIC_EXEC_FQNAMES``
# handling), plus the two ``subprocess.get*output`` helpers, which are likewise
# implemented as ``/bin/sh -c`` wrappers and take no ``shell=`` kwarg.
_ALWAYS_SHELL_STRING_SINKS: Final[frozenset[str]] = (_STRING_CMD_EXEC_FQNAMES - _DYNAMIC_EXEC_FQNAMES) | frozenset(
    {"subprocess.getoutput", "subprocess.getstatusoutput"}
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

    ``ast.walk`` is breadth-first top-down, so an OUTER call is yielded
    before the INNER call it wraps. When both cover ``line`` with the SAME
    line span (the common single-line ``open(subprocess.run(...))`` shape),
    a strict ``<`` would keep the first-yielded OUTER call — the opposite
    of "deepest". Use ``<=`` so the later-yielded, more-deeply-nested call
    replaces it on a tie. (audit LOW #123: the docstring promised the inner
    call but ``<`` returned the outer one.)
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
            if span <= best_span:
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


def _python_comment_start_pos(source_line: str) -> int:
    """Return the column index of the first ``#`` that starts a comment on
    ``source_line``, ignoring ``#`` characters inside string literals.

    Returns ``-1`` if the line has no inline comment.

    Simple state machine — handles single-line single- and double-quoted
    strings (with backslash escapes). Triple-quoted strings spanning
    multiple lines are NOT tracked here (a separate AST pass handles
    matches inside multi-line strings via ``_is_inside_multiline_string_literal``).
    """
    in_string = False
    quote_char: str | None = None
    i = 0
    n = len(source_line)
    while i < n:
        c = source_line[i]
        if in_string:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == quote_char:
                in_string = False
                quote_char = None
        else:
            if c == "#":
                return i
            if c == '"' or c == "'":
                in_string = True
                quote_char = c
        i += 1
    return -1


def _match_in_python_inline_comment(source_line: str, match: str) -> bool:
    """True iff ``match`` (as a literal substring) appears INSIDE the
    inline-comment portion of ``source_line``.

    Used to certify execution-class rule matches inside a Python ``#``
    inline-comment as safe_literal: a comment is provably non-executable,
    so a CMD_INJECTION / REGEX_DOS / TOOL_SHADOW / etc. match in the
    comment text is documentation, not code.

    Iron-rule preserved: prose-vector rules (PROMPT_INJECT, DATA_EXFIL,
    HARDCODED_SECRET, INVISIBLE_UNICODE_RAW, etc.) stay flagged even in
    a comment — the caller checks ``_rule_is_prose_vector`` separately.
    """
    if not match or not source_line:
        return False
    comment_start = _python_comment_start_pos(source_line)
    if comment_start < 0:
        return False
    # Decide on the FIRST occurrence of ``match``:
    #   * before ``comment_start`` → the match is in CODE. The rule would
    #     fire on that code-side occurrence regardless of any later
    #     comment-side copy, so keep the finding visible (return False).
    #   * at/after ``comment_start`` → the match is in the inline comment,
    #     which is provably non-executable → safe (return True).
    # The first occurrence is therefore decisive: a single ``find`` (no
    # loop) is all that's needed, because the two cases are exhaustive
    # for any non-negative offset.
    occurrence = source_line.find(match)
    if occurrence < 0:
        return False
    return occurrence >= comment_start


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
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
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
    vocabulary) and the line carries no hard runtime data-grab indicator.

    Comparison is CASE-INSENSITIVE so ALL-CAPS module constants like
    ``SYSTEM_PROMPT`` / ``CLAUDE_CODE_SYSTEM_PROMPT`` (the standard Python
    shape for top-level config strings — e.g. the Anthropic OAuth-token
    helper's required `system` field literal) match the same vocabulary
    list as snake-case API params and dict keys. The OPPOSITE-direction
    test — a real runtime data-grab pattern like ``get_tools()`` or
    ``tool_results[i]`` — stays case-sensitive because those are
    fixed-identifier APIs, not domain vocabulary.
    """
    match_lower = match.lower()
    line_lower = line.lower()
    if not any(name in match_lower or name in line_lower for name in _API_FIELD_NAMES_PY):
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
_LOOPBACK_HOSTS_PY: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})


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


# ── Issue #198 — SSRF_ADVANCED static-DESTINATION discriminator (Python) ──
# SSRF_PATTERN has forgiven a fully static URL literal since issue #41: a
# destination fixed at author time cannot be attacker-influenced, which is
# the whole premise of SSRF. SSRF_ADVANCED never got the same carve-out, so
# the canonical-pipeline template's own version probe
#
#     req = urllib.request.Request(  # nosec B310 - fixed https constant, never user input
#         CANON_LATEST_URL, headers={"User-Agent": "cpv-publish-canon-version"}
#     )
#
# drew a publish-blocking MAJOR in every plugin that adopted the canon:
# catalog pattern 0 matches ``request(`` plus ANY later ``\binput\b`` on the
# line — here the word "input" inside the nosec justification comment.
#
# _ssrf_url_is_static_literal_py canNOT be reused verbatim (measured, not
# assumed): it locates the string literal CONTAINING the match, and an
# SSRF_ADVANCED match is a call fragment rather than a URL substring, so it
# returns False on every shape here — the FP and the real threats alike, i.e.
# it would have cleared nothing. The equivalent test at this rule's
# granularity is on the DESTINATION: every argument of the flagged call (or
# the value of the flagged assignment) must be a provably static literal. An
# f-string, a concatenation, a nested call, an attribute, a subscript, or a
# variable that is not a single-assignment module-level literal all keep the
# finding VISIBLE.

# The user-input identifiers catalog patterns 0/3/7 key on. The carve-out is
# scoped to matches carrying one of these. The other SSRF_ADVANCED patterns
# identify a threat by URL/technique CONTENT (decimal/hex-encoded loopback,
# IPv6-mapped loopback, metadata hosts, ``curl $(...)``, redirect following),
# and there a static literal is exactly what must keep firing.
_SSRF_ADV_USER_INPUT_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\breq\."
    r"|\binput\b|\bparam\b|\bquery\b"
    r"|user(?:Input|Data|Query|Id|Body|Param|Token|Provided)\b"
    r"|\buser_(?:input|data|query)\b",
    re.IGNORECASE,
)

# Any of these inside the match means the match was earned by CONTENT, not by
# an incidental user-input word — never clear it, however static the args are.
_SSRF_ADV_HARD_SIGNAL_RE: Final[re.Pattern[str]] = re.compile(
    r"169\.254|metadata|instance-data"
    r"|0177\.0\.0\.1|0x7f|2130706433|017700000001|\[0:0:0:0:0:"
    r"|\bredirect\b|\bfollow\b|\blocation\b"
    r"|\$\{|\$\(",
    re.IGNORECASE,
)


def _expr_is_static_literal_py(node: ast.expr, static_names: frozenset[str]) -> bool:
    """True iff ``node`` evaluates to a value fixed at author time.

    Strict whitelist: a constant, a container of constants, a unary op on
    one, or a name proven to be a single-assignment module-level literal.
    Everything else — f-string, concatenation, call, attribute, subscript,
    comprehension, lambda, star-arg, walrus — can differ per invocation and
    is therefore a destination the caller may be able to influence.
    """
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in static_names
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_expr_is_static_literal_py(e, static_names) for e in node.elts)
    if isinstance(node, ast.Dict):
        # A ``**spread`` entry has a None key — dynamic by construction.
        if any(k is None for k in node.keys):
            return False
        return all(_expr_is_static_literal_py(k, static_names) for k in node.keys if k is not None) and all(
            _expr_is_static_literal_py(v, static_names) for v in node.values
        )
    if isinstance(node, ast.UnaryOp):
        return _expr_is_static_literal_py(node.operand, static_names)
    return False


def _module_static_literal_names_py(tree: ast.AST) -> frozenset[str]:
    """Module-level names bound EXACTLY ONCE, to a pure literal.

    A name rebound anywhere else — a second assignment, a ``global`` write, a
    loop / with / except target, a function parameter, an import alias, a def
    or class of the same name — is excluded, because its value at call time
    is not the module-level literal. That guard is what stops the carve-out
    from clearing

        CANON_LATEST_URL = "https://..."     # looks static ...
        def set_url(u):
            global CANON_LATEST_URL
            CANON_LATEST_URL = u             # ... but is not

    The module-level literal itself is required to be a PURE literal (an
    empty ``static_names`` is passed down), so no resolution ordering between
    constants can be exploited.
    """
    binding_counts: dict[str, int] = {}
    rebound_elsewhere: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            binding_counts[node.id] = binding_counts.get(node.id, 0) + 1
        elif isinstance(node, ast.arg):
            rebound_elsewhere.add(node.arg)
        elif isinstance(node, ast.alias):
            rebound_elsewhere.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rebound_elsewhere.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            rebound_elsewhere.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            rebound_elsewhere.update(node.names)

    candidates: set[str] = set()
    for stmt in getattr(tree, "body", []):
        # AnnAssign.value is Optional (a bare ``X: str`` annotation binds
        # nothing), so the union is the honest type for both branches.
        value: ast.expr | None
        if isinstance(stmt, ast.Assign):
            targets: list[ast.expr] = list(stmt.targets)
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
            value = stmt.value
        else:
            continue
        if value is None or not _expr_is_static_literal_py(value, frozenset()):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                candidates.add(target.id)

    return frozenset(n for n in candidates if binding_counts.get(n, 0) == 1 and n not in rebound_elsewhere)


def _ssrf_advanced_destination_is_static_py(tree: ast.AST, line: int, match: str) -> bool:
    """True iff the SSRF_ADVANCED match on ``line`` has a provably static
    destination — the SSRF_PATTERN static-literal carve-out (issue #41)
    applied at SSRF_ADVANCED's granularity (issue #198)."""
    if not _SSRF_ADV_USER_INPUT_TOKEN_RE.search(match):
        return False
    if _SSRF_ADV_HARD_SIGNAL_RE.search(match):
        return False

    static_names = _module_static_literal_names_py(tree)

    # Assignment shape (catalog pattern 3): ``url = <static>``.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and getattr(node, "lineno", None) == line:
            value = node.value
            if value is not None and _expr_is_static_literal_py(value, static_names):
                return True

    # Call shape (catalog patterns 0/7). EVERY call whose source range covers
    # the line must have provably static arguments. Checking only the
    # innermost call would clear ``Request(get_url())`` — the inner call has
    # no args at all — and checking only the outermost would miss a dynamic
    # sibling call sharing the physical line.
    saw_call = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        saw_call = True
        for arg in node.args:
            if not _expr_is_static_literal_py(arg, static_names):
                return False
        for kw in node.keywords:
            # ``**spread`` has kw.arg None — dynamic by construction.
            if kw.arg is None or not _expr_is_static_literal_py(kw.value, static_names):
                return False
    return saw_call


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


# Issue #125 class 5 — the SUPPLY_CHAIN pattern ``npm install\s+(?!--|@)\S+.*&&``
# fires on a printed install-instruction HINT
# (``_log("Install with: npm install -g dev-browser && dev-browser install")``).
# The command is a STRING-LITERAL argument to a print/log sink — it is shown to
# the user, never executed. A printed string cannot install anything. This is a
# GENERAL printed-help-string discriminator, NOT a publish.py path exemption.
#
# A print / log sink head immediately preceding a quoted string the match sits
# inside. Conservative: the sink name must be one of the recognised
# output/log/warn functions; an arbitrary call is NOT a print sink.
_PRINT_LOG_SINK_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:print"
    r"|_log|log_\w+|\w*_log"
    r"|logging\.\w+|logger\.\w+|log\.\w+"
    r"|sys\.stderr\.write|sys\.stdout\.write"
    r"|click\.echo|click\.secho"
    r"|warnings\.warn"
    r"|echo|secho|print_\w+)\s*\(",
    re.IGNORECASE,
)
# An execution token anywhere on the line voids the carve-out — a real shell
# spawn of the install command keeps firing.
_PY_EXEC_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\bsubprocess\.|os\.system|os\.popen|\bPopen\s*\(|\bcheck_output\s*\("
    r"|\brun\s*\(|\bcall\s*\(|shell\s*=\s*True|os\.exec\w*\s*\(|\bcommands\.getoutput",
)


def _is_command_in_print_string_arg(line: str, match: str) -> bool:
    """True iff a SUPPLY_CHAIN ``match`` (an install command) sits inside a
    STRING LITERAL that is an argument to a print / log sink, AND the line has
    NO execution token.

    A printed/logged install hint (``print("run: npm install x && y")`` /
    ``_log("Install with: …")``) is documentation text, never executed.

    FN-safe (BOTH conditions required):
      1. the matched command is inside a double/single-quoted string on the line
         (so it is string DATA, not a bare command), AND a print/log sink head
         appears on the line; AND
      2. the line carries NO execution token (``subprocess`` / ``os.system`` /
         ``Popen(`` / ``run(`` / ``shell=True`` / …) — a real
         ``subprocess.run("npm install x && y", shell=True)`` keeps firing.
    """
    if not match:
        return False
    if not _PRINT_LOG_SINK_RE.search(line):
        return False
    if _PY_EXEC_TOKEN_RE.search(line):
        return False
    # The matched command must sit inside a quoted string literal on the line
    # (string DATA passed to the print sink), not be a bare token.
    return _match_inside_quoted_string_py(line, match)


# A lightweight same-line check: ``match`` substring is enclosed by a pair of
# matching quotes on ``line``. Used by the print-string discriminator (the AST
# token check is heavier and runs later for other carve-outs).
def _match_inside_quoted_string_py(line: str, match: str) -> bool:
    """True iff ``match`` appears inside a single/double-quoted string span on
    ``line`` (a best-effort same-line scan, quote-pair aware)."""
    if not match or match not in line:
        return False
    for quote in ('"', "'"):
        depth_open = -1
        i = 0
        n = len(line)
        spans: list[tuple[int, int]] = []
        while i < n:
            if line[i] == quote:
                if depth_open == -1:
                    depth_open = i
                else:
                    spans.append((depth_open, i))
                    depth_open = -1
            i += 1
        start = 0
        while True:
            pos = line.find(match, start)
            if pos == -1:
                break
            end = pos + len(match)
            if any(s < pos and end <= e for s, e in spans):
                return True
            start = pos + 1
    return False


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


def _re_compile_wrapper_names(tree: ast.AST) -> frozenset[str]:
    """Return the names of local functions that are PROVABLY thin
    ``re.compile`` wrappers — body is exactly ``return re.compile(<first
    param>, …)`` (an optional leading docstring is allowed). A string
    argument to such a function is a regex pattern literal, identical to a
    direct ``re.compile(...)`` argument.

    Security plugins routinely define ``def _re(p): return re.compile(p,
    re.M)`` / ``def _re_i(p): return re.compile(p, re.I|re.M)`` and pass
    every attack-pattern regex through them (ai-maestro-janitor's
    ``scripts/lib/*_patterns.py``). The strict single-return shape is what
    keeps this safe: a function that ALSO does anything with the param —
    exec it, open it, send it — has more than one body statement, so it
    cannot masquerade as a compile wrapper and its argument stays visible.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = list(node.args.posonlyargs) + list(node.args.args)
        if not params:
            continue
        first_param = params[0].arg
        body = list(node.body)
        # Allow (and skip) a single leading docstring.
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)  # type: ignore[attr-defined]
        ):
            body = body[1:]
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            continue
        ret = body[0].value
        if not isinstance(ret, ast.Call):
            continue
        f = ret.func
        if not (
            isinstance(f, ast.Attribute)
            and f.attr == "compile"
            and isinstance(f.value, ast.Name)
            and f.value.id == "re"
        ):
            continue
        if ret.args and isinstance(ret.args[0], ast.Name) and ret.args[0].id == first_param:
            names.add(node.name)
    return frozenset(names)


def _is_re_pattern_call_or_wrapper(node: ast.AST, wrapper_names: frozenset[str]) -> bool:
    """True iff ``node`` is a stdlib ``re.<func>(...)`` call OR a call to a
    local function proven (by :func:`_re_compile_wrapper_names`) to be a thin
    ``re.compile`` wrapper. Used to treat ``_re_i(r"…")`` exactly like
    ``re.compile(r"…")`` for regex-pattern-literal suppression."""
    if _is_re_module_pattern_call(node):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in wrapper_names


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
        # The pattern Constant must live inside the sink's positional args OR a
        # keyword-arg value. ``subprocess.run``/``Popen`` accept the command as
        # ``args=`` by keyword and ``os.system``/``os.popen`` accept it as
        # ``command=`` — ``subprocess.run(args=re.compile(r"…").pattern,
        # shell=True)`` executes the pattern just like the positional form, so a
        # positional-only scan was a false-negative (an executed pattern stayed
        # suppressed). Mirrors the args+kwargs scan of
        # ``_path_literal_feeds_fs_or_exec_sink`` / ``_module_container_name_flows_to_sink``.
        in_args = any(sub is target for arg in node.args for sub in ast.walk(arg))
        in_kwargs = any(sub is target for kw in node.keywords for sub in ast.walk(kw.value))
        if not (in_args or in_kwargs):
            continue
        if qn in _STRING_CMD_EXEC_FQNAMES:
            return True
        if qn in _SHELL_CALL_FQNAMES and _shell_kwarg_is_true(node):
            return True
    return False


# ──────────────────────────────────────────────────────────────────────
# Issue #57 Fix A — absolute-path linter data-vs-sink discriminator.
# A sensitive path literal (`/etc/passwd`, `/etc/hosts`, …) sitting in an
# inert pure-literal container (a detector's pattern list, a test-input
# fixture dict) is NOT a live finding; one that flows into a filesystem /
# exec / network sink IS. The distinction is computed intrinsically from
# the AST — never self-declared by the scanned plugin.
# ──────────────────────────────────────────────────────────────────────

# Filesystem / network sinks that CONSUME a path string as a live operation
# (open/read/write/delete the path, or send it over the network).
_FS_NET_SINK_FQNAMES: Final[frozenset[str]] = frozenset(
    {
        "open",
        "io.open",
        "os.open",
        "os.fdopen",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.removedirs",
        "os.rename",
        "os.replace",
        "os.truncate",
        "os.chmod",
        "os.chown",
        "os.mkdir",
        "os.makedirs",
        "os.scandir",
        "os.listdir",
        "os.stat",
        "os.lstat",
        "os.readlink",
        "os.symlink",
        "os.link",
        "os.access",
        "os.walk",
        "os.chdir",
        "pathlib.Path",
        "Path",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copytree",
        "shutil.move",
        "shutil.rmtree",
        "shutil.copyfileobj",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "requests.head",
        "requests.patch",
        "requests.request",
        "httpx.get",
        "httpx.post",
        "httpx.request",
        "urllib.request.urlopen",
        "urlopen",
    }
)


def _path_literal_feeds_fs_or_exec_sink(tree: ast.AST, target: ast.Constant) -> bool:
    """True iff the path-string Constant ``target`` is consumed by a live
    filesystem / exec / network sink — modeled on
    ``_re_literal_feeds_exec_sink`` but with the FS/network sink set added.

    A sensitive path literal is normally inert (a detector pattern or test
    fixture), but the moment its string flows into ``open(...)`` /
    ``subprocess.run(...)`` / ``os.remove(...)`` / ``requests.get(...)`` it
    IS a live operation, so the data-vs-sink suppression must NOT apply.
    Conservative: ANY subprocess/os-exec call carrying the path keeps it
    visible (a path argument is plausibly executed or is a binary path —
    either way not inert data worth suppressing). (issue #57 Fix A)
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        qn = _node_qualname(node.func)
        if qn is None:
            continue
        in_args = any(sub is target for arg in node.args for sub in ast.walk(arg))
        in_kwargs = any(sub is target for kw in node.keywords for sub in ast.walk(kw.value))
        if not (in_args or in_kwargs):
            continue
        if qn in _STRING_CMD_EXEC_FQNAMES or qn in _FS_NET_SINK_FQNAMES or qn in _SHELL_CALL_FQNAMES:
            return True
    return False


def _outermost_pure_literal_container(tree: ast.AST, target: ast.AST) -> ast.AST | None:
    """Return the outermost List/Tuple/Set/Dict that contains ``target``
    through an unbroken chain of PURE-literal containers, or ``None`` if
    ``target`` is not inside any pure container. A container is pure iff
    every element is a Constant or a nested pure container (no Name / Call /
    f-string / unpacking)."""

    def _is_pure(n: ast.AST) -> bool:
        if isinstance(n, ast.Constant):
            return True
        if isinstance(n, (ast.List, ast.Tuple, ast.Set)):
            return all(_is_pure(e) for e in n.elts)
        if isinstance(n, ast.Dict):
            return (
                all((k is None or _is_pure(k)) and _is_pure(v) for k, v in zip(n.keys, n.values, strict=False))
                and None not in n.keys
            )  # **unpack → impure
        return False

    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    outer: ast.AST | None = None
    cur: ast.AST = target
    while True:
        parent = parents.get(id(cur))
        if isinstance(parent, (ast.List, ast.Tuple, ast.Set, ast.Dict)) and _is_pure(parent):
            outer = parent
            cur = parent
            continue
        break
    return outer


def _match_inside_python_comment(source: str, line: int, match: str) -> bool:
    """True iff ``match`` appears inside a Python ``#`` COMMENT on ``line``.

    A comment is never executed, opened, or fed to a sink, so an absolute path
    inside one is inert documentation — the dominant residual shape in a
    security plugin's detector libraries, whose regexes are annotated with the
    attack-target paths they match (``# Restrict to /tmp/, /var/tmp/,
    /dev/shm/``). Uses :mod:`tokenize` so a ``#`` inside a string literal is
    NOT mistaken for a comment. Conservative: returns ``False`` on any
    tokenize error, or if the path ALSO appears in the code portion preceding
    the comment on the same line (then it is a real code path that the comment
    merely repeats). (issue #57 Fix A — comment extension)
    """
    if not source or not match:
        return False
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return False
    src_lines = source.splitlines()
    for tok in toks:
        # A Python COMMENT token is always single-line (it ends at the newline).
        if tok.type != tokenize.COMMENT or tok.start[0] != line:
            continue
        if match not in tok.string:
            continue
        col = tok.start[1]
        code_part = src_lines[line - 1][:col] if 0 <= line - 1 < len(src_lines) else ""
        if match in code_part:
            return False  # real code path; the trailing comment merely repeats it
        return True
    return False


def _match_inside_string_literal_token(source: str, line: int, match: str) -> bool:
    """True iff ``match`` appears inside a Python STRING / BYTES / F-STRING literal
    on (or spanning) the 1-based ``line``.

    Issue #65/#67: a code-EXECUTION shape (``new Function(``, ``verify=False``,
    ``shell=True``) matched INSIDE a string/bytes literal is that literal's DATA —
    a detector SIGNATURE (``b"return(function("``) or a DESCRIPTION string
    (``"TLS bypass: verify=False"``) in a security plugin's pattern library, not a
    live sink. The real sink, if any, is matched separately at the call site, so
    suppressing the in-string match drops noise without dropping a true positive.

    Column-precise via :mod:`tokenize` (a STRING token, not a substring scan) so
    ``os.system("x"); evil(real)`` only matches the in-string half. Conservative:
    returns ``False`` on any tokenize error.
    """
    if not source or not match:
        return False
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return False
    fstr_mid = getattr(tokenize, "FSTRING_MIDDLE", None)
    for tok in toks:
        is_string_tok = tok.type == tokenize.STRING or (fstr_mid is not None and tok.type == fstr_mid)
        if not is_string_tok:
            continue
        if tok.start[0] <= line <= tok.end[0] and match in tok.string:
            return True
    return False


def _string_literal_match_is_inert_no_sink(tree: ast.AST, line: int, source: str, match: str) -> bool:
    """True iff ``match`` on the 1-based ``line`` sits inside a string Constant
    that does NOT flow to a filesystem / exec / shell / subprocess sink.

    Issue #65 — the sink-aware discriminator for content-THREAT rules whose live
    form IS a string fed to a sink (PRIVILEGE_ESC: ``sudo`` / ``setuid`` /
    ``chmod +s``). A benign install help-hint string (``HINT = "sudo usermod -aG
    docker $USER"``) is inert text; the IDENTICAL ``sudo `` substring inside
    ``os.system("sudo ...")`` / ``subprocess.run(["sudo", x])`` / ``subprocess.run(
    "sudo ...", shell=True)`` IS a live escalation. This helper suppresses ONLY the
    former: it locates the covering string Constant and refuses (returns False) the
    moment that Constant flows into any fs/exec/net/shell sink (reusing the existing
    flow analysis ``_path_literal_feeds_fs_or_exec_sink``, which is conservative —
    ANY subprocess/os-exec/open/requests call carrying the literal keeps it visible).

    Conservative defaults to False (keep visible) on: an f-string match (a JoinedStr
    has no single covering string Constant to reason about — an interpolated command
    is potentially dynamic), no covering Constant, or any sink flow.
    """
    if not source:
        return False
    lines = source.splitlines()
    if not (0 <= line - 1 < len(lines)) or (match and match not in lines[line - 1]):
        return False
    # An f-string (JoinedStr) covering the line is dynamic-shaped text, not a fixed
    # inert literal — do not certify it here (keep visible / let other paths handle).
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            fstart = getattr(node, "lineno", None)
            fend = getattr(node, "end_lineno", None)
            if fstart is not None and fend is not None and fstart <= line <= fend:
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
    # The decisive guard: a literal feeding a live fs/exec/shell/subprocess sink is
    # NOT inert — keep it visible (the real ``os.system("sudo …")`` keeps firing).
    if _path_literal_feeds_fs_or_exec_sink(tree, target):
        return False
    return True


def _eval_exec_message_string_is_inert(tree: ast.AST, line: int, source: str, match: str) -> bool:
    """Issue #100 (class B). True iff ``match`` (an ``eval(`` / ``exec(`` / shell-exec
    token) on the 1-based ``line`` sits inside a SINGLE-LINE string Constant that is a
    scanner's inert finding-MESSAGE / detector doc (``msg = "Python eval() detected"``)
    and CANNOT execute — neither directly nor through a variable.

    This is a STRICTER sibling of ``_string_literal_match_is_inert_no_sink``, built
    specifically because the flow-sensitive INJECTION rules (SHELL_EXEC / CMD_INJECTION)
    must NOT be added to ``_SINK_GUARDED_LITERAL_SUPPRESSIBLE_RULES``: that helper checks
    only a DIRECT-argument sink flow, so a literal bound to a variable that then reaches
    a sink (``cmd = "curl … | sh"; subprocess.run(cmd, shell=True)``) would slip through
    as a false-negative. The three extra guards here close every such hole:

    * MULTI-LINE / triple-quoted strings are REFUSED — a multi-line data string could be
      an exploit-payload template and must stay at ``safe_doc`` (demote-visible), which a
      later dispatch branch already does. Only a one-line message string qualifies.
    * The covering Constant must NOT directly feed an exec/shell/fs sink
      (``_path_literal_feeds_fs_or_exec_sink``).
    * If the Constant is the RHS of a simple ``NAME = "…"`` assignment, that NAME must
      NEVER reach an exec/shell/fs sink anywhere in the module (INDIRECTION-aware walk,
      modeled on ``_module_container_name_flows_to_sink``). A name reaching a sink keeps
      the finding visible — the real injection through ``cmd`` STILL fires.

    Conservative defaults to False (keep visible) on: an f-string (JoinedStr), a
    multi-line Constant, no covering Constant, a direct sink flow, or a variable that
    reaches a sink. A REAL bare ``eval(x)`` / ``os.system(x)`` CALL is not inside a string
    Constant at all → no ``target`` → False → STILL fires."""
    if not source:
        return False
    lines = source.splitlines()
    if not (0 <= line - 1 < len(lines)) or (match and match not in lines[line - 1]):
        return False
    # An f-string (JoinedStr) covering the line is dynamic-shaped — never certify.
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            fstart = getattr(node, "lineno", None)
            fend = getattr(node, "end_lineno", None)
            if fstart is not None and fend is not None and fstart <= line <= fend:
                return False
    # A TRIPLE-QUOTED string is a data / template block (``cmd = """os.system(…)"""``)
    # the iron rule keeps visible — even on one physical line — because it could be a
    # payload the variable later carries to ``exec``. A finding MESSAGE is a normal
    # ``"…"`` string. Refuse any triple-quoted token covering the line; the later
    # ``safe_doc`` branch demotes those (visible NIT) instead of suppressing. Tokenize-
    # based (a ``#`` or quote inside a string is not mis-read); refuse on any tokenize error.
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type != tokenize.STRING or not (tok.start[0] <= line <= tok.end[0]):
                continue
            body = tok.string.lstrip("rbuRBUfF")
            if body[:3] in ('"""', "'''"):
                return False
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return False
    # Locate the covering string Constant; require it to be SINGLE-LINE (a multi-line
    # data string could be a payload template → must stay at safe_doc, not suppress).
    target: ast.Constant | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if (
                start is not None
                and end is not None
                and start == end == line
                and (not match or match in node.value)
            ):
                target = node
                break
    if target is None:
        return False
    # Direct sink flow → not inert (a literal handed straight to exec/shell/fs fires).
    if _path_literal_feeds_fs_or_exec_sink(tree, target):
        return False
    # Indirection guard: if the Constant is the RHS of ``NAME = "…"`` (simple or
    # annotated), refuse the moment that NAME reaches a sink. Closes the
    # ``cmd = "… | sh"; subprocess.run(cmd, shell=True)`` false-negative.
    sinks = _STRING_CMD_EXEC_FQNAMES | _FS_NET_SINK_FQNAMES | _SHELL_CALL_FQNAMES
    assigned_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.value is target:
            for tgt in node.targets:
                for sub in ast.walk(tgt):
                    if isinstance(sub, ast.Name):
                        assigned_names.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and node.value is target and isinstance(node.target, ast.Name):
            assigned_names.add(node.target.id)
    if assigned_names:
        for node in ast.walk(tree):
            # (a) the assigned NAME (or an expression containing it) is a sink argument.
            if isinstance(node, ast.Call) and _node_qualname(node.func) in sinks:
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    for sub in ast.walk(arg):
                        if isinstance(sub, ast.Name) and sub.id in assigned_names:
                            return False
            # (b) ``for x in NAME:`` whose body contains any sink call.
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and node.iter.id in assigned_names:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and _node_qualname(sub.func) in sinks:
                        return False
    return True


def abs_path_const_is_inert_py_data(
    source: str, line: int, matched_text: str, is_test_file: bool, tree: ast.AST | None = None
) -> bool:
    """Issue #57 Fix A entry point. True iff the absolute-path token
    ``matched_text`` on ``line`` of Python ``source`` is INERT data — a
    string Constant reachable through pure-literal containers from a
    module-level assignment (ANY file) or, in a TEST file, sitting inside a
    pure-literal container (a test-input fixture) — AND it does NOT feed a
    live fs/exec/network sink. Defaults to False (keep visible) on any
    uncertainty, syntax error, or sink flow.
    """
    if tree is None:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return False
    # A path inside a `#` COMMENT is inert documentation — comments are never
    # executed or opened, and (unlike a prose-vector match) a hardcoded path in
    # a comment has zero portability impact. Checked FIRST: a comment has no
    # string Constant, so the covering-Constant search below would short-circuit
    # to False before any clause runs. (issue #57 Fix A — comment extension)
    if _match_inside_python_comment(source, line, matched_text):
        return True
    # Locate the covering string Constant that carries the matched path.
    needle = matched_text.rstrip("/.")
    target: ast.Constant | None = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        if matched_text in node.value or needle in node.value:
            target = node
            break
    if target is None:
        return False
    # Sink guard — a path feeding a live sink is never inert (keep visible).
    if _path_literal_feeds_fs_or_exec_sink(tree, target):
        return False
    # A sensitive path inside a regex PATTERN literal (``re.compile(r"…")`` or
    # a local ``_re()`` compile-wrapper arg) is a detector's matching
    # vocabulary, not a path the plugin opens — the dominant shape in a
    # security plugin's ``*_patterns.py`` libraries. ``_match_inside_re_
    # pattern_literal`` carries its own exec-sink guard, so a pattern that
    # feeds exec still stays visible.
    if _match_inside_re_pattern_literal(tree, line, source, matched_text):
        return True
    # Module-level pure-data container assignment qualifies in ANY file —
    # UNLESS the container's bound name is later opened / exec'd (directly, by
    # iteration, or via subscript), which makes the embedded paths live.
    if _node_is_in_module_level_pure_data_assign(tree, target) and not _module_container_name_flows_to_sink(
        tree, target
    ):
        return True
    # In a TEST file, a path inside a pure-literal container is inert
    # test-input data (assigned locally or passed to a non-sink helper).
    if is_test_file and _outermost_pure_literal_container(tree, target) is not None:
        return True
    # A sensitive path inside a BARE-STRING STATEMENT — a module / class /
    # function DOCSTRING, or any string literal evaluated-and-discarded as a
    # statement — is pure documentation. Python never assigns or opens a bare
    # string, and the sink guard above already proved it reaches no live sink.
    # This is the dominant residual shape in a security plugin's pattern
    # libraries, whose module / rule docstrings cite attack-target paths
    # (``/etc/passwd``, ``/etc/restic/pw``) by name to explain what each
    # detector matches. (issue #57 Fix A — docstring/description extension)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and node.value is target:
            return True
    # A sensitive path inside a METADATA / DESCRIPTION field string — a
    # ``name=`` / ``description=`` / ``message=`` constructor kwarg, or a Dict
    # value under such a key — is the human-readable doc of what a detector
    # MATCHES, never a path the plugin opens. Reuses the skillaudit
    # metadata-field discriminator; double sink-guarded (the fs/exec/net guard
    # above + the helper's own exec guard). (issue #57 Fix A — description ext.)
    if _match_inside_metadata_field_string(tree, line, source, matched_text):
        return True
    return False


# LLM prompt-template constant detection (r01 anthropics FP iter1, 2026-05-27).
# Security-tool plugins routinely declare module-level constants like
# ``SECURITY_REVIEW_PROMPT = """..."""`` or ``CLAUDE_CODE_SYSTEM_PROMPT =
# """..."""`` that contain LLM prompts describing vulnerability categories.
# Those prompts MENTION exploit patterns by name (e.g. "Look for `os.system`
# calls with user input"), and the skillaudit regex catches the mention.
#
# The naming convention is what makes this iron-rule-safe: a malicious
# author hiding an exploit in ``EXPLOIT_PAYLOAD = """..."""`` is not
# matching this allowlist (no _PROMPT/_TEMPLATE/_INSTRUCTIONS/etc. suffix).
# Real exec sinks that consume these strings (os.system(EXPLOIT_PAYLOAD))
# are flagged by separate, still-visible rules.
_LLM_PROMPT_CONSTANT_SUFFIXES: Final[tuple[str, ...]] = (
    "_PROMPT",
    "_PROMPTS",
    "_TEMPLATE",
    "_TEMPLATES",
    "_INSTRUCTIONS",
    "_INSTRUCTION",
    "_MESSAGE",
    "_MESSAGES",
    "_GUIDANCE",
    "_REVIEW_PROMPT",
    "_SYSTEM_PROMPT",
    "_USER_PROMPT",
    "_ASSISTANT_PROMPT",
    "_RUBRIC",
    "_CHECKLIST",
    # r01 anthropic FP iter1: security-tool reminder strings — assigned
    # to module-level constants like ``_UNSAFE_YAML_LOAD_REMINDER =
    # """⚠️ Security Warning: yaml.load() ... use yaml.safe_load() ..."""``
    # that the security-guidance plugin shows to the user. The string
    # CONTAINS the dangerous patterns (because it's documenting them)
    # — the scanner re-fires on the documented patterns. Same iron-rule
    # logic as prompt templates: inert data displayed to the user, not
    # executable code.
    "_REMINDER",
    "_REMINDERS",
    "_WARNING",
    "_WARNINGS",
    "_NOTICE",
    "_NOTICES",
    "_NOTE",
    "_NOTES",
    "_HINT",
    "_HINTS",
    "_DESCRIPTION",
    "_DOCSTRING",
    "_HELP",
    "_HELP_TEXT",
    "_USAGE",
    "_BANNER",
    "_BODY",
    "_TEXT",
    "_CONTENT",
    "_CONTEXT",
    "_DETAILS",
    # NOTE: ``_EXAMPLE`` / ``_EXAMPLES`` deliberately NOT included —
    # ``EXAMPLE = """..."""`` is too generic. A var named ``EXAMPLE``
    # holding a triple-quoted string could be an exploit string the
    # author plans to use later (test fixture for a vuln scanner, etc.).
    # Iron-rule preserved: data strings stay at safe_doc → demote.
    # r01 anthropic FP iter1: LLM agent system / user / assistant
    # message variants. Common shapes:
    #   AGENTIC_INVESTIGATE_SYSTEM = """..."""
    #   SECURITY_REVIEW_TASK = """..."""
    #   AUDIT_QUERY = """..."""
    # The string contents describe vulnerability categories to look
    # for — they MENTION dangerous code patterns as examples, which
    # the scanner re-fires on.
    "_SYSTEM",
    "_USER",
    "_ASSISTANT",
    "_QUERY",
    "_TASK",
    "_TASKS",
    "_REVIEW",
    "_AUDIT",
    "_INVESTIGATE",
    "_INVESTIGATION",
    "_ANALYSIS",
    "_ANALYZE",
    "_CHECK",
    "_CHECKS",
)


def _is_inside_llm_prompt_template_constant(tree: ast.AST, line: int) -> bool:
    """True iff ``line`` falls inside a string-Constant value of an
    ``<id> = <multi-line-string>`` assignment whose target identifier
    matches a known LLM prompt-template naming convention (``*_PROMPT``,
    ``*_TEMPLATE``, ``*_INSTRUCTIONS``, ``*_RUBRIC``, etc.) — both
    module-level (``ALL_CAPS``) and local (``snake_case``).

    The naming-suffix gate is what makes this iron-rule-safe: an attacker
    hiding exploit code in a string would not match
    ``EXPLOIT_PAYLOAD = (triple-quote)..(triple-quote)`` against any of
    the suffixes; the exec sink that consumes the string is flagged
    separately.

    Detection walks every Assign / AnnAssign anywhere in the tree (not
    just module-level) — security-guidance plugins routinely build
    prompts inside helper functions (e.g. ``analyze_code_security``
    declares ``diff_instruction = (triple-quote)..(triple-quote)``
    locally before splicing it into the API call). Case-insensitive
    suffix matching catches both module-level CAPS constants and
    function-local snake_case variables.
    """

    def _is_prompt_name(name: str) -> bool:
        if not name:
            return False
        if not name.replace("_", "").isalnum():
            return False
        upper = name.upper()
        # Match either the explicit suffix (``XXX_PROMPT``,
        # ``XXX_INSTRUCTION``) OR the bare word (``prompt``,
        # ``instructions``) — a function-local variable named exactly
        # ``prompt = """..."""`` is the most common security-tool
        # shape and would be missed by suffix-only matching.
        for suf in _LLM_PROMPT_CONSTANT_SUFFIXES:
            if upper.endswith(suf):
                return True
            if upper == suf.lstrip("_"):
                return True
        return False

    def _value_spans_line(value: ast.expr | None) -> bool:
        if value is None:
            return False
        start = getattr(value, "lineno", None)
        end = getattr(value, "end_lineno", None)
        if start is None or end is None:
            return False
        return bool(start <= line <= end)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if not any(isinstance(t, ast.Name) and _is_prompt_name(t.id) for t in node.targets):
                continue
            if _value_spans_line(node.value):
                return True
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if not (isinstance(target, ast.Name) and _is_prompt_name(target.id)):
                continue
            if _value_spans_line(node.value):
                return True
    return False


# Pattern-catalog detection (r01 anthropics FP iteration, 2026-05-27).
# Security-tool plugins ship rule-detection dictionaries — each dict entry
# describes a vulnerability category and contains the regex/substrings that
# DETECT it. The skillaudit scanner reads those strings and re-fires its own
# rules on them. To suppress those self-references safely, we require the
# enclosing Dict to carry at least one CATALOG-SHAPE key — a key that ONLY
# appears in detection catalogs, not in general configuration dicts.
_PATTERN_CATALOG_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "regex",
        "regexes",
        "pattern",
        "patterns",
        "substrings",
        "matches",
        "match_pattern",
        "match_patterns",
        "rule",
        "rules",
        "signature",
        "signatures",
        "trigger",
        "triggers",
    }
)


def _match_inside_pattern_catalog(tree: ast.AST, line: int, match: str) -> bool:
    """True iff ``line`` sits inside a Dict literal whose key set proves
    it is a SECURITY DETECTION CATALOG.

    Detection logic:

    * Walk the AST for Dict nodes covering ``line``.
    * A Dict qualifies as a pattern catalog when it has AT LEAST ONE key
      in ``_PATTERN_CATALOG_REQUIRED_KEYS`` (regex / patterns / substrings /
      etc.) — generic keys like ``name`` / ``description`` are too common
      to qualify alone.
    * If the line falls anywhere inside such a Dict → True. The matched
      substring can span multiple Constants (e.g. a RESOURCE_ABUSE
      match like ``child_process.exec", "execSync(`` that crosses two
      adjacent list elements is still detection data, not exec code).

    A security-detection-catalog dict is INERT DATA — the strings are
    fed to a regex engine for MATCHING, never executed or rendered as
    instructions. Real exploit code lives outside the catalog (in the
    scanner that consumes it), where it is still scanned normally.

    NOTE: this is a sibling of ``_match_inside_re_pattern_literal`` —
    that one catches ``re.compile(r"…")`` lone-literal calls; this one
    catches ``[{"regex": "…", "name": "…"}, ...]`` dict-of-dicts shape
    that's idiomatic for security-pattern registries (skillaudit's own
    catalog, the security-guidance plugin's ``SECURITY_PATTERNS``, etc.).
    """
    del match  # match-span is allowed to cross multiple constants; only line position matters

    def _dict_keys_str(d: ast.Dict) -> set[str]:
        out: set[str] = set()
        for k in d.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.add(k.value)
        return out

    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        keys = _dict_keys_str(node)
        if not (keys & _PATTERN_CATALOG_REQUIRED_KEYS):
            continue
        # The Dict is a pattern-catalog entry. Any content on a line
        # inside it is detection metadata (string literal value, the
        # `:` separator, the `,` separator, even the `"` quotation
        # marks). The match is inert.
        return True
    return False


# Known file-magic byte signatures. A bytes literal whose head matches
# one of these is unambiguously the start of a known file format, not
# obfuscated code. The OBFUSCATION rule pattern
# ``\\\\x[0-9a-fA-F]{2}\\\\x[0-9a-fA-F]{2}\\\\x[0-9a-fA-F]{2}`` fires on
# any 3-byte hex-escape sequence, which falsely matches every embedded
# binary file (PNG header, JPEG header, PDF magic, ELF, Mach-O, ZIP, …).
_FILE_MAGIC_HEXES: Final[tuple[str, ...]] = (
    "\\x89PNG",  # PNG (8950 4E 47 0D 0A 1A 0A)
    "\\xff\\xd8",  # JPEG SOI
    "\\xff\\xd9",  # JPEG EOI
    "GIF8",  # GIF87a / GIF89a
    "%PDF",  # PDF
    "PK\\x03",  # ZIP / docx / xlsx / jar
    "\\x7fELF",  # ELF
    "\\xcf\\xfa\\xed\\xfe",  # Mach-O 64
    "\\xca\\xfe\\xba\\xbe",  # Mach-O fat
    "MZ",  # PE / DOS executable header
    "\\x1f\\x8b",  # gzip
    "BZh",  # bzip2
    "\\xfd7zXZ",  # xz
    "\\x00asm",  # WebAssembly
    "ID3",  # MP3 ID3
    "RIFF",  # WAV / AVI / WebP
    "\\x47",  # MPEG TS (loose; combined with other checks would be stronger)
)

# Issue #100 (class C) — the canonical Unicode byte-order marks. The OBFUSCATION
# pattern ``\\xNN\\xNN\\xNN`` matches the 3-byte UTF-8 BOM ``\\xef\\xbb\\xbf`` (and
# the printable-UTF-8 carve-out misses it: ``b"\\xef\\xbb\\xbf".decode("utf-8")`` is
# the zero-width ``"\\ufeff"``, which ``str.isprintable()`` reports False). A BOM in a
# BOM-detection routine (``data.startswith(b"\\xef\\xbb\\xbf")``) is an encoding
# CONSTANT, not obfuscated machine code.
_CANONICAL_BOMS: Final[frozenset[bytes]] = frozenset(
    {
        codecs.BOM_UTF8,  # b"\xef\xbb\xbf"
        codecs.BOM_UTF16_LE,  # b"\xff\xfe"
        codecs.BOM_UTF16_BE,  # b"\xfe\xff"
        codecs.BOM_UTF32_LE,  # b"\xff\xfe\x00\x00"
        codecs.BOM_UTF32_BE,  # b"\x00\x00\xfe\xff"
    }
)


def _obfuscation_bytes_literal_is_canonical_bom(tree: ast.AST, line: int) -> bool:
    """True iff a bytes Constant on the 1-based ``line`` is EXACTLY a canonical
    encoding BOM (UTF-8 / UTF-16 / UTF-32).

    Issue #100 (class C) — a BOM is a recognized encoding constant in a BOM-detection
    routine, not obfuscated code. The match is EXACT-value (``node.value in
    _CANONICAL_BOMS``), NOT a prefix check, so a shellcode literal whose head happens
    to be a BOM (``b"\\xef\\xbb\\xbf\\x90\\x90..."``) is NOT cleared — its value is not
    a canonical BOM and OBFUSCATION STILL fires. Defaults False on no covering bytes
    Constant (keep visible)."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, bytes)):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is not None and end is not None and start <= line <= end:
            if node.value in _CANONICAL_BOMS:
                return True
    return False


def _bytes_literal_is_file_magic(source_line: str) -> bool:
    """True iff ``source_line`` contains a Python bytes literal whose
    first ~8 bytes match a known file-format magic header.

    Used to certify OBFUSCATION matches on hex-escape sequences inside
    bytes literals as safe_literal: a `b"\\x89PNG\\r\\n..."` literal is
    a fixture image header, not obfuscated code.
    """
    # Look for bytes literal: b"..." or b'...'
    for m in re.finditer(r"\bb[rR]?[\"']", source_line):
        rest = source_line[m.end() :]
        # Look at the first ~16 chars of the bytes literal content
        head = rest[:32]
        if any(head.startswith(magic) or head.lstrip()[: len(magic)] == magic for magic in _FILE_MAGIC_HEXES):
            return True
    return False


def _line_is_bytes_continuation_in_file_magic_tuple(lines: list[str], line_idx: int) -> bool:
    """True iff ``lines[line_idx]`` is a bytes-literal line AND a nearby
    line (within ±10) is a bytes literal that starts with a known
    file-format magic header.

    Multi-line file-fixture tuples (PNG / JPEG / PDF / etc.) typically
    span 4-10 lines: the first `b"\\x89PNG\\r\\n..."` is the magic
    header; continuation lines `b"\\x08\\x06\\x00..."` carry the
    payload bytes. The continuation lines also fire OBFUSCATION's
    hex-escape pattern but are NOT obfuscation — they're the rest of
    the fixture image. Suppress them by anchoring on the magic line.
    """
    if not (0 <= line_idx < len(lines)):
        return False
    line = lines[line_idx]
    # Current line must itself be a bytes literal continuation.
    if not re.search(r"\bb[rR]?[\"']", line):
        return False
    # A magic-marker bytes literal somewhere nearby is enough.
    lo = max(0, line_idx - 10)
    hi = min(len(lines) - 1, line_idx + 10)
    for i in range(lo, hi + 1):
        if _bytes_literal_is_file_magic(lines[i]):
            return True
    return False


# Sinks that DECODE / DEOBFUSCATE / EXECUTE a bytes value — feeding a bytes literal
# into one of these is the live deobfuscate-then-run shape, so the printable-UTF-8
# carve-out must NOT apply when the literal flows here. Combined with the exec set
# (``_STRING_CMD_EXEC_FQNAMES``: eval / exec / compile / __import__ / os.system / …).
_BYTES_DECODE_SINK_FQNAMES: Final[frozenset[str]] = frozenset(
    {
        "base64.b64decode",
        "base64.standard_b64decode",
        "base64.urlsafe_b64decode",
        "base64.b32decode",
        "base64.b16decode",
        "base64.a85decode",
        "base64.b85decode",
        "base64.decodebytes",
        "codecs.decode",
        "binascii.unhexlify",
        "binascii.a2b_base64",
        "binascii.a2b_hex",
        "zlib.decompress",
        "gzip.decompress",
        "bz2.decompress",
        "lzma.decompress",
        "marshal.loads",
        "pickle.loads",
    }
)


def _bytes_const_decodes_to_printable_text(value: bytes) -> bool:
    """True iff ``value`` is valid UTF-8 whose every character is printable or a
    common whitespace (space / tab / newline / carriage-return).

    Content-based proof of inertness for the OBFUSCATION ``\\xNN\\xNN\\xNN`` rule:
    an em-dash byte run (``b"\\xe2\\x80\\x94"`` → ``"—"``) is human-readable text,
    never an obfuscated machine-code payload. Real shellcode (``b"\\x90\\x901\\xc0"``)
    is NOT valid printable UTF-8 (x86 opcodes are not legal UTF-8 continuation
    sequences) → returns False → stays visible. Empty bytes → False (nothing to
    certify)."""
    if not value:
        return False
    try:
        decoded = value.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return False
    return all(ch.isprintable() or ch in " \t\n\r" for ch in decoded)


def _obfuscation_bytes_literal_is_printable_text(tree: ast.AST, line: int, source: str) -> bool:
    """True iff the bytes Constant carrying the OBFUSCATION ``\\xNN`` hex-escape run on
    the 1-based ``line`` decodes to printable UTF-8 text AND does NOT flow into a
    decode / deobfuscate / exec sink.

    Issue #65 — extends the file-magic carve-out: an em-dash / unicode-punctuation
    byte literal (``MSG = b"received \\xe2\\x80\\x94 close"``) trips the 3-hex-escape
    OBFUSCATION pattern but is plain text, not obfuscated code. The discriminator is
    CONTENT-based (the bytes must decode to printable UTF-8 — a strict proof that
    rules out shellcode) AND sink-aware (a literal fed to ``base64.b64decode`` /
    ``exec`` / ``codecs.decode`` / a shell stays visible). Both must hold; defaults
    to False (keep visible) on no covering bytes Constant, a non-printable decode,
    or any decode/exec sink flow."""
    if not source:
        return False
    lines = source.splitlines()
    if not (0 <= line - 1 < len(lines)):
        return False
    # Locate the covering bytes Constant on the line.
    target: ast.Constant | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is not None and end is not None and start <= line <= end:
                target = node
                break
    if target is None:
        return False
    if not _bytes_const_decodes_to_printable_text(target.value):  # type: ignore[arg-type]
        return False
    # Sink guard — a printable-looking bytes literal that is nonetheless fed to a
    # decode/deobfuscate/exec sink is part of a live pipeline; keep it visible.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        qn = _node_qualname(node.func)
        if qn is None:
            continue
        in_args = any(sub is target for arg in node.args for sub in ast.walk(arg))
        in_kwargs = any(sub is target for kw in node.keywords for sub in ast.walk(kw.value))
        if not (in_args or in_kwargs):
            continue
        if qn in _BYTES_DECODE_SINK_FQNAMES or qn in _STRING_CMD_EXEC_FQNAMES or qn in _SHELL_CALL_FQNAMES:
            return False
    return True


# Known runtime-hijack env vars. Setting any of these IS dangerous
# even with a literal value because the dynamic linker / interpreter
# reads them on every subprocess spawn.
_ENV_HIJACK_VARS: Final[frozenset[str]] = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "NODE_OPTIONS",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "PERL5OPT",
        "PERL5LIB",
        "RUBYOPT",
        "RUBYLIB",
        "BASH_ENV",
        "ENV",
        "GIT_SSH_COMMAND",
        "GCONV_PATH",
        "IFS",
        "PATH",
        "CLASSPATH",
    }
)

_ENV_LITERAL_SET_RE: Final[re.Pattern[str]] = re.compile(
    r"""os\.environ\s*\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]\s*=\s*['"]([^'"]*)['"]\s*$""",
)


def _is_safe_env_literal_set(source_line: str) -> bool:
    """True iff ``source_line`` is an ``os.environ["LITERAL_KEY"] =
    "literal_value"`` assignment AND the key is NOT a known runtime-
    hijack env var.

    A literal-key/literal-value assignment is configuration setup with
    zero injection surface (no dynamic input reaches a subprocess env).
    Real env injection requires either an attacker-controlled key/value
    OR a hijack-var key (which IS dangerous even with a literal value).
    """
    stripped = source_line.strip()
    # Skip lines with f-strings or BinOp on the value (dynamic input).
    m = _ENV_LITERAL_SET_RE.match(stripped)
    if m is None:
        return False
    key = m.group(1)
    if key in _ENV_HIJACK_VARS:
        return False
    # Key starts with one of the hijack-var prefixes?
    for hv in _ENV_HIJACK_VARS:
        if key.startswith(hv + "_") or key == hv:
            return False
    return True


# Issue #75 (class 4) — build-OUTPUT / download-CACHE directory env vars.
# Redirecting any of these only changes WHERE build artifacts / caches are
# written; NONE is consulted as a code-load, library-search, or executable-
# search path at runtime, so setting it — even to an attacker value — cannot
# cause code execution. Positive allowlist: a runtime-hijack var can NEVER
# appear here (asserted by tests; allowlist ∩ _ENV_HIJACK_VARS == ∅).
# Deliberately EXCLUDES CARGO_HOME/GOPATH (have bin/), TMPDIR/TMP/TEMP
# (executable-drop surface), and generic XDG_* — those stay VISIBLE.
_ENV_BUILD_OUTPUT_VARS: Final[frozenset[str]] = frozenset(
    {
        "CARGO_TARGET_DIR",
        "GOCACHE",
        "CCACHE_DIR",
        "PIP_CACHE_DIR",
        "UV_CACHE_DIR",
        "YARN_CACHE_FOLDER",
        "npm_config_cache",
        "NPM_CONFIG_CACHE",
    }
)

# os.environ["KEY"] = <rhs>  — KEY allows lowercase (npm_config_cache); RHS may be
# a literal OR a call that OPENS on this line (multi-line value, the janitor shape).
_ENV_BUILD_SET_RE: Final[re.Pattern[str]] = re.compile(
    r"""os\.environ\s*\[\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]\s*\]\s*=\s*(.+?)\s*$"""
)

# Controlled value shapes only: a string literal, or a path/tempfile constructor.
# Anything else (bare name, attacker var, interpolating f-string) does NOT match,
# so the finding stays visible.
_SAFE_BUILD_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"""^(?:(?:r|R|b|B|u|U)?['"]|str\s*\(|Path\s*\(|(?:os\.path|os|tempfile|pathlib)\.)"""
)


def _is_safe_build_env_set(source_line: str) -> bool:
    """True iff ``source_line`` is ``os.environ["<BUILD_OUTPUT_VAR>"] = <controlled
    path/literal>`` for a var in ``_ENV_BUILD_OUTPUT_VARS`` (build-output / cache
    dirs only — never a code-load / library / exec search path). FN-safe: the
    allowlist is positive and excludes every runtime-hijack var, so a hijack-var
    assignment can never reach this suppressor regardless of value shape."""
    m = _ENV_BUILD_SET_RE.search(source_line)
    if m is None:
        return False
    if m.group(1) not in _ENV_BUILD_OUTPUT_VARS:  # positive allowlist; hijack vars excluded
        return False
    rhs = m.group(2).strip()
    if rhs[:2].lower() in ("f'", 'f"'):  # an interpolating f-string is NOT controlled
        return False
    return bool(_SAFE_BUILD_VALUE_RE.match(rhs))


def _line_has_quoted_string(source_line: str) -> bool:
    """True iff ``source_line`` contains a Python string literal
    (single, double, triple-single, triple-double, or any prefix
    variant like r"..." / b"..." / f"...").

    Conservative — only checks for the opening quote, not full balance.
    Used for CRED_ENV_SAFE in-string suppression where ANY string
    literal on the line means the match is documentation prose.

    A simple presence check is enough: even continuation lines of a
    multi-line tuple (``"foo"\\n"bar"``) start with a quote, so any
    line that has at least one `"` or `'` qualifies.
    """
    return bool(re.search(r"[\"']", source_line))


_PYTHON_DEF_RE: Final[re.Pattern[str]] = re.compile(r"^\s*(?:async\s+)?def\s+[A-Za-z_][\w]*\s*\(")


def _line_is_python_function_def(source_line: str) -> bool:
    """True iff ``source_line`` starts with a Python ``def`` / ``async def``
    statement (a function definition, not a call).

    r05 ananddtyagi FP iter1 (2026-05-27): SSRF_ADVANCED pattern fires on
    ``def process_user_request(request):`` because the catalog regex
    matches ``\\brequest\\(`` on the parameter name. A `def` line is the
    OPPOSITE of an outbound network call — flag it as safe_literal so the
    rule's intent (outbound HTTP with user input) isn't degraded by
    matching function definitions whose param name happens to be
    `request`.
    """
    return bool(_PYTHON_DEF_RE.match(source_line))


def _line_is_re_module_call(source_line: str) -> bool:
    """True iff ``source_line`` contains a Python ``re.<func>(...)`` call
    (re.compile / re.search / re.match / re.fullmatch / re.split / re.sub
    / re.subn / re.findall / re.finditer).

    Used to certify REGEX_DOS / catastrophic-backtracking matches that
    SPAN the call boundary (the matched substring contains the call's
    opening paren + the regex source string + part of the pattern) as
    safe_literal: the regex string IS the pattern being audited; the
    code that calls re.<func> is doing pattern-COMPILATION, never
    exec. Real exec lives in the code that consumes the compiled
    pattern (re.search().group() fed to exec(), etc.), separately
    visible.

    Conservative: only matches when the line clearly has a re.<func>(
    call shape with optional string-prefix on the regex argument.
    """
    return bool(
        re.search(
            r"\bre\.(?:compile|search|match|fullmatch|split|sub|subn|findall|finditer)\s*\(",
            source_line,
        )
    )


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

    # Local thin ``re.compile`` wrappers (``def _re(p): return
    # re.compile(p, …)``) are treated exactly like ``re.<func>`` calls, so a
    # string argument to ``_re_i(r"…")`` counts as a regex pattern literal.
    wrapper_names = _re_compile_wrapper_names(tree)

    # Shape 1 — target inside the first arg of an re.<func>(...) call.
    for node in ast.walk(tree):
        if _is_re_pattern_call_or_wrapper(node, wrapper_names):
            args = node.args  # type: ignore[attr-defined]
            if args and any(sub is target for sub in ast.walk(args[0])):
                return True

    # r05 ananddtyagi FP iter1 (2026-05-27) — Shape 3: target is an
    # element of a List/Tuple/Set literal assigned to a variable, and
    # that variable is later passed as the FIRST positional arg to a
    # ``re.<func>(...)`` call. Idiomatic Python regex-pattern-list shape:
    #   ``file_patterns = [r'...', r'...']``
    #   ``for p in file_patterns: re.findall(p, content)``
    # The List elements are inert pattern data fed to the regex engine.
    target_variable_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # Only consider plain ``var = [...]`` / ``var = (...)`` / ``var = {...}``
        if not isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
            continue
        # Is `target` an element of this list/tuple/set?
        if not any(elt is target or any(sub is target for sub in ast.walk(elt)) for elt in node.value.elts):
            continue
        # Grab the variable names this list is assigned to
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                target_variable_names.add(tgt.id)
            elif isinstance(tgt, ast.Tuple):
                for sub in tgt.elts:
                    if isinstance(sub, ast.Name):
                        target_variable_names.add(sub.id)
    if target_variable_names:
        # Search the same tree for ``re.<func>(var, ...)`` where ``var`` is
        # in target_variable_names. Also accept ``for p in var: re.<func>(p)``.
        for node in ast.walk(tree):
            if not _is_re_pattern_call_or_wrapper(node, wrapper_names):
                continue
            args = node.args  # type: ignore[attr-defined]
            if not args:
                continue
            first = args[0]
            # Direct: re.findall(var, ...)
            if isinstance(first, ast.Name) and first.id in target_variable_names:
                return True
            # Iteration: ``re.findall(p, ...)`` where `p` is the loop var
            # over our variable. Walk For statements to find p ← var.
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            if not isinstance(node.iter, ast.Name) or node.iter.id not in target_variable_names:
                continue
            loop_var: str | None = None
            if isinstance(node.target, ast.Name):
                loop_var = node.target.id
            if loop_var is None:
                continue
            # Inside the loop body, look for re.<func>(loop_var, ...)
            for body_node in ast.walk(node):
                if not _is_re_pattern_call_or_wrapper(body_node, wrapper_names):
                    continue
                body_args = body_node.args  # type: ignore[attr-defined]
                if body_args and isinstance(body_args[0], ast.Name) and body_args[0].id == loop_var:
                    return True

    # Shape 2 — target in the pure-literal iter of a comprehension whose
    # elt compiles a regex.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            continue
        if not _is_re_pattern_call_or_wrapper(node.elt, wrapper_names):
            continue
        for gen in node.generators:
            if any(sub is target for sub in ast.walk(gen.iter)):
                return True

    return False


# Metadata / description field NAMES whose string value is human-readable
# documentation of a detection rule (or any record), never an executable
# action. A SECURITY plugin's *_patterns.py rule catalogs name and describe
# every rule in their own attack vocabulary.
_METADATA_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "id",
        "rule_id",
        "ruleid",
        "name",
        "title",
        "label",
        "description",
        "desc",
        "summary",
        "severity",
        "category",
        "owasp",
        "owasp_asi",
        "cwe",
        "cwe_id",
        "references",
        "reference",
        "remediation",
        "mitigation",
        "message",
        "note",
        "notes",
        "reason",
        "rationale",
        "explanation",
        "help",
        "hint",
        "doc",
        "docs",
        "tags",
        "tag",
    }
)


def _match_is_identifier_fragment(tree: ast.AST, source: str, line: int, match: str) -> bool:
    """True iff every occurrence of ``match`` on ``line`` is a FRAGMENT of a
    larger Python identifier (``SETUID`` inside ``_SETUID_CHMOD``). A name is
    not an action; ``os.setuid(0)`` (standalone) stays visible.

    Two guards keep this from over-firing:
    * the match must be a CODE identifier, NOT text inside a string literal —
      a word inside an f-string / docstring is documentation, handled by the
      safe_doc / slug paths, never suppressed here;
    * "embedded" is START/END-word-aware: a match is a fragment only if a
      WORD-char border CONTINUES one of its WORD-char ends into a larger
      identifier. ``SECRET_KEY =`` (left end ``S`` preceded by ``_`` in
      ``_WP_DEFAULT_SECRET_KEY``) is embedded; ``yaml.load(`` (right end ``(``,
      not a word char) is a real call and stays visible.

    Returns False if any occurrence is standalone, or the token does not appear
    on the line — defaults to KEEP."""
    if not match:
        return False
    lines = source.splitlines()
    if not (0 <= line - 1 < len(lines)):
        return False
    # The match must be a CODE identifier — if it falls inside a string
    # literal (a plain string Constant OR an f-string / JoinedStr span), it is
    # documentation text, not a name fragment, and is handled by the safe_doc /
    # slug paths.
    for node in ast.walk(tree):
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        if isinstance(node, ast.JoinedStr):
            return False
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and match in node.value:
            return False

    def _is_word(ch: str) -> bool:
        return bool(ch) and (ch.isalnum() or ch == "_")

    starts_word = _is_word(match[:1])
    ends_word = _is_word(match[-1:])
    text = lines[line - 1]
    found = False
    for m in re.finditer(re.escape(match), text):
        found = True
        before = text[m.start() - 1] if m.start() > 0 else ""
        after = text[m.end()] if m.end() < len(text) else ""
        # A WORD-char border extends a WORD-char END of the match into a larger
        # identifier (``_SECRET_KEY`` → ``_WP_DEFAULT_SECRET_KEY``;
        # ``_scan_dockerfile_setuid``). ``yaml.load(`` ends in ``(`` (not a word
        # char) so the argument after it does NOT extend it → stays visible;
        # ``os.setuid(0)`` is standalone (``.``/``(`` borders) → stays visible.
        left_embed = starts_word and _is_word(before)
        right_embed = ends_word and _is_word(after)
        if not (left_embed or right_embed):
            return False
    return found


def _match_inside_metadata_field_string(tree: ast.AST, line: int, source: str, match: str) -> bool:
    """True iff ``match`` sits inside a string Constant that is the VALUE of a
    metadata/description field — a ``name=``/``description=``/``id=`` keyword
    argument of a constructor call, or a Dict entry with such a key. These are
    human-readable docs of what a rule DETECTS, never an executable action.
    Exec-sink guarded: a description string that also flows into a shell/exec
    sink stays visible."""
    if not source:
        return False
    lines = source.splitlines()
    if not (0 <= line - 1 < len(lines)) or (match and match not in lines[line - 1]):
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
    if _re_literal_feeds_exec_sink(tree, target):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in _METADATA_FIELD_NAMES:
            if any(sub is target for sub in ast.walk(node.value)):
                return True
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=False):
                if (
                    isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                    and k.value in _METADATA_FIELD_NAMES
                    and any(sub is target for sub in ast.walk(v))
                ):
                    return True
    return False


# A slug string is a SINGLE identifier-like token (kebab / snake / dotted
# case), no whitespace and no shell / URL metacharacters — a rule-ID
# reference, an env-var name, a capability name, a registry key. The leading
# char must be alphanumeric (so a ``/etc/passwd`` path or a ``http://`` URL
# does NOT qualify), and slashes/colons are excluded (no paths, no URLs).
_SLUG_STRING_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][\w.\-]*$")


def _match_inside_slug_string(tree: ast.AST, line: int, source: str, match: str) -> bool:
    """True iff ``match`` sits inside a string Constant whose ENTIRE value is a
    single identifier-like slug (a rule-ID reference / env-var name /
    capability name / registry key). A slug is a NAME, never a command or
    payload. Exec/fs/net-sink guarded — a slug that flows into a live sink
    (``subprocess.run([slug])``) stays visible."""
    if not source:
        return False
    lines = source.splitlines()
    if not (0 <= line - 1 < len(lines)) or (match and match not in lines[line - 1]):
        return False
    # An f-string (JoinedStr) is formatted documentation text, not a slug
    # reference — a single word inside one is not a rule-ID / env-var name.
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            fstart = getattr(node, "lineno", None)
            fend = getattr(node, "end_lineno", None)
            if fstart is not None and fend is not None and fstart <= line <= fend:
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
    if not _SLUG_STRING_RE.match(str(target.value).strip()):
        return False
    if _path_literal_feeds_fs_or_exec_sink(tree, target):
        return False
    return True


# Invisible / zero-width / bidi / format characters — the vocabulary a
# unicode-smuggling DETECTOR necessarily contains to recognise them. Written
# as explicit \u escapes (never literal invisibles) so this source stays
# readable and does not trip CPV's own invisible-unicode self-scan.
_INVISIBLE_CODEPOINTS: Final[frozenset[int]] = frozenset(
    {
        0x200B,
        0x200C,
        0x200D,
        0x200E,
        0x200F,  # zero-width + LRM/RLM
        0x2060,
        0x2061,
        0x2062,
        0x2063,
        0x2064,  # word-joiner + invisible math
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,  # bidi embedding/override
        0x2066,
        0x2067,
        0x2068,
        0x2069,  # bidi isolates
        0xFEFF,
        0x00AD,
        0x180E,
        0x061C,  # BOM, soft-hyphen, MVS, ALM
    }
)


def _has_invisible_char(text: str) -> bool:
    """True iff ``text`` contains any invisible / zero-width / bidi / format
    character from :data:`_INVISIBLE_CODEPOINTS`."""
    return any(ord(c) in _INVISIBLE_CODEPOINTS for c in text)


def _strip_invisible_chars(text: str) -> str:
    """Return ``text`` with every invisible / format character removed."""
    return "".join(c for c in text if ord(c) not in _INVISIBLE_CODEPOINTS)


def _invisible_unicode_is_detector_vocab(tree: ast.AST, line: int, source: str) -> bool:
    """True iff the invisible / bidi / zero-width characters on ``line`` form a
    DETECTOR's charset vocabulary — they sit inside a regex pattern literal
    (``re.compile`` / ``_re()`` char-class) OR a string Constant composed
    ONLY of invisible / format / control characters
    (``_ZERO_WIDTH = "\\u200b\\u200c…"``). Invisible chars interspersed in
    VISIBLE text (a smuggling payload) are NOT detector vocabulary and stay
    visible."""
    lines = source.splitlines()
    if not (0 <= line - 1 < len(lines)):
        return False
    if not _has_invisible_char(lines[line - 1]):
        return False
    target: ast.Constant | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is not None and end is not None and start <= line <= end and _has_invisible_char(node.value):
                target = node
                break
    if target is None:
        return False
    # (a) inside a regex pattern literal call (stdlib re.<func> or _re wrapper).
    wrapper_names = _re_compile_wrapper_names(tree)
    for node in ast.walk(tree):
        if _is_re_pattern_call_or_wrapper(node, wrapper_names):
            args = node.args  # type: ignore[attr-defined]
            if args and any(sub is target for sub in ast.walk(args[0])):
                return True
    # (b) pure charset — the Constant is ONLY invisible/format chars (after
    # removing them nothing visible remains). A payload mixes invisibles into
    # visible text, so its stripped form is non-empty → stays visible.
    if _strip_invisible_chars(str(target.value)).strip() == "":
        return True
    return False


def _subprocess_match_is_annotation_only(tree: ast.AST, line: int, match: str) -> bool:
    """Issue #124 (reopened) class 7 — True iff a ``SHELL_EXEC`` match whose text
    is ``subprocess.Popen`` / ``subprocess.run`` sits in TYPE-ANNOTATION position
    on ``line``, not in a CALL.

    The reporter's shapes (PSS ``pss_reindex.py``) are pure type hints, not
    invocations::

        p2: subprocess.Popen[bytes] | None = None
        running: tuple[subprocess.Popen[bytes] | None, ...] = (p2, p3)

    There the ``subprocess.Popen`` attribute is the ``.value`` of a ``Subscript``
    (``subprocess.Popen[bytes]``) that lives inside an annotation — never the
    ``.func`` of a ``Call``. The real call form ``subprocess.Popen(args, …)`` is
    the ``.func`` of a ``Call`` and is classified by the call-shape dispatch
    (list-form argv → non-blocking ``info``; ``shell=True`` → fires), so this
    helper deliberately suppresses ONLY the annotation use.

    Decision (conservative — must be PROVEN an annotation, never a call):
      * the match text is a ``subprocess.Popen`` / ``subprocess.run`` qualname,
      * an ``ast.Attribute`` matching that qualname covers ``line``,
      * that Attribute is NOT (directly or via an enclosing ``Subscript`` chain)
        the ``.func`` of any ``ast.Call``,
      * that Attribute IS reached through an annotation slot — an
        ``AnnAssign.annotation``, a function arg / return annotation, or the
        value/slice of a ``Subscript`` that is itself in such an annotation.
    Any uncertainty → False (the rule keeps firing).
    """
    m = (match or "").strip()
    if not (m.startswith("subprocess.Popen") or m.startswith("subprocess.run")):
        return False

    # Collect every Attribute node on `line` whose qualname is the exec target.
    targets: list[ast.Attribute] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if getattr(node, "lineno", None) != line:
            continue
        if _node_qualname(node) not in ("subprocess.Popen", "subprocess.run"):
            continue
        targets.append(node)
    if not targets:
        return False

    # Build child→parent links once so we can walk a target upward.
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node

    def _is_call_func(attr: ast.Attribute) -> bool:
        """True iff ``attr`` is (directly, or via a Subscript value chain) the
        callee of a Call — i.e. ``subprocess.Popen(...)`` or
        ``subprocess.Popen[...](...)`` invocation."""
        cur: ast.AST = attr
        while True:
            par = parent.get(id(cur))
            if par is None:
                return False
            if isinstance(par, ast.Call) and par.func is cur:
                return True
            # A Subscript whose .value is `cur` could still be called
            # (`Foo[bar](...)`), so keep climbing through Subscript values only.
            if isinstance(par, ast.Subscript) and par.value is cur:
                cur = par
                continue
            return False

    def _is_in_annotation(attr: ast.Attribute) -> bool:
        """True iff ``attr`` is reached through a type-annotation slot."""
        cur: ast.AST = attr
        while True:
            par = parent.get(id(cur))
            if par is None:
                return False
            if isinstance(par, ast.AnnAssign) and par.annotation is cur:
                return True
            if isinstance(par, ast.arg) and getattr(par, "annotation", None) is cur:
                return True
            if isinstance(par, (ast.FunctionDef, ast.AsyncFunctionDef)) and getattr(par, "returns", None) is cur:
                return True
            # Climb through annotation-internal nodes (Subscript, BinOp for
            # `X | None`, Tuple for `tuple[...]`, Index/Attribute) toward the slot.
            if isinstance(par, (ast.Subscript, ast.BinOp, ast.Tuple, ast.List, ast.Index, ast.Attribute, ast.Starred)):
                cur = par
                continue
            return False

    # EVERY target on the line must be a proven annotation use AND none a call.
    # If any target is a call (or unprovable as an annotation), do not suppress —
    # the line carries a real invocation and must stay visible.
    for attr in targets:
        if _is_call_func(attr):
            return False
        if not _is_in_annotation(attr):
            return False
    return True


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
    # r03 FP iter (2026-05-28) — INDIRECT_PROMPT_INJECT charset-ENCODING
    # vocabulary (``zero-width char``, ``unicode characters``, ``hidden
    # character``) is documentation about character encoding, benign in a
    # comment / docstring / code alike. Runs BEFORE the comment fast-path
    # (which would only DEMOTE a prose-vector rule to safe_doc, keeping the
    # FP visible). The injection variants (instruction / injection /
    # payload) of the same catalog pattern are NOT matched here and stay
    # visible (iron rule).
    if rule_id == "INDIRECT_PROMPT_INJECT" and _is_charset_detection_vocab(match):
        return "safe_literal"

    # Cheap fast-path: full-line comment detection without parse (parse is
    # ~50× slower). Issue #40: a Python ``#`` comment is NEVER executed, so
    # an execution-class rule matched inside one is a provable non-threat →
    # suppress (safe_literal). A prose-vector rule (prompt-injection / exfil
    # instruction / hardcoded secret / invisible unicode) stays VISIBLE
    # (safe_doc → demote) — a careless agent or grep-loader could surface it.
    lines = source.split("\n")
    if 0 <= line_idx < len(lines) and _line_is_full_comment(lines[line_idx]):
        return "safe_doc" if _rule_is_prose_vector(rule_id) else "safe_literal"

    # r01 anthropic FP iter1 (2026-05-27): extend the comment carve-out to
    # INLINE comments — when the match position falls AFTER the ``#`` of an
    # inline comment, the match is in the comment text (not the code
    # before it). Same iron-rule split as the full-line case: execution
    # rules suppress, prose-vector rules stay visible.
    #
    # Catches FPs like:
    #   re.compile(r"\\(.*\\)+"),  # nested quantifier: (a+)*  (a*b)*
    #     ^^^^ code (the actual pattern)   ^^^^^^^ inline comment (doc)
    # where the REGEX_DOS rule fires on ``(a+)*`` from the COMMENT, not
    # from the re.compile() pattern argument.
    #
    # IMPORTANT split between rule classes:
    #
    # * Execution-class rules (CMD_INJECTION, REGEX_DOS, TOOL_SHADOW,
    #   PATH_TRAVERSAL, …) inside an inline comment → safe_literal
    #   (a comment is provably non-executable).
    # * Prose-vector rules (PROMPT_INJECT / DATA_EXFIL /
    #   INVISIBLE_UNICODE_RAW / …) → DEFER (fall through to existing
    #   heuristic chain). These have OTHER more specific safe-literal
    #   heuristics downstream (e.g. synthetic-secret detection for
    #   ``"sk-" + "a" * 24`` in test files via SECRET_*-specific
    #   logic) that must run first. Returning safe_doc here would
    #   shadow those heuristics.
    # * Per-vendor SECRET_* rules → DEFER (same reason — synthetic
    #   secret detection in test files needs to run).
    if (
        0 <= line_idx < len(lines)
        and not _rule_is_prose_vector(rule_id)
        and _match_in_python_inline_comment(lines[line_idx], match)
    ):
        return "safe_literal"

    # Issue #65/#67 — a code-execution / config shape (new Function(, verify=False,
    # shell=True) matched INSIDE a string / bytes literal is the literal's DATA
    # (a detector signature, a description string in a security plugin's pattern
    # library), not a live sink. Suppress for the code-shape rules only; the real
    # sink, if any, is matched separately at the call site. Prose-vector / secret
    # rules are excluded — a payload string or a real key in a literal stays visible.
    if (
        rule_id in _LITERAL_DATA_SUPPRESSIBLE_RULES
        and 0 <= line_idx < len(lines)
        and _match_inside_string_literal_token(source, line_idx + 1, match)
    ):
        return "safe_literal"

    # Issue #125 class 5 — SUPPLY_CHAIN on a printed install-instruction HINT
    # (``_log("Install with: npm install -g dev-browser && dev-browser install")``).
    # The command is a string-literal argument to a print/log sink — shown to the
    # user, never executed. General printed-help-string discriminator (NOT a
    # publish.py path exemption). FN-safe: a line with any execution token
    # (``subprocess.run(..., shell=True)`` / ``os.system`` / …) keeps firing.
    if (
        rule_id == "SUPPLY_CHAIN"
        and 0 <= line_idx < len(lines)
        and _is_command_in_print_string_arg(lines[line_idx], match)
    ):
        return "safe_literal"

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

    # Issue #65 — content-THREAT rule whose live form is a string fed to a sink
    # (PRIVILEGE_ESC: ``sudo`` / ``setuid`` / ``chmod +s``) matched inside a string
    # literal that does NOT flow to any fs/exec/shell/subprocess sink is an inert
    # install help-hint / doc string (``HINT = "sudo usermod -aG docker $USER"``),
    # not a live escalation. Sink-AWARE (unlike the zero-FN block above): the moment
    # the SAME literal flows into ``os.system("sudo …")`` / ``subprocess.run(["sudo",
    # x])`` / ``subprocess.run("sudo …", shell=True)``, ``_string_literal_match_is_
    # inert_no_sink`` returns False and the finding stays visible. Runs before the
    # call-shape dispatch, which (correctly) does NOT suppress content-threat rules
    # for a literal-argv shape — so this is the only path that can exonerate the
    # benign data string, and it does so only when proven sink-free.
    if rule_id in _SINK_GUARDED_LITERAL_SUPPRESSIBLE_RULES and _string_literal_match_is_inert_no_sink(
        tree, line, source, match
    ):
        return "safe_literal"

    # Issue #100 (class B) — SHELL_EXEC / CMD_INJECTION on an ``eval(`` / ``exec(`` /
    # ``os.system(`` token that is provably a SINGLE-LINE, sink-free, finding-MESSAGE
    # string (a security scanner's own detector message: ``msg = "Python eval()
    # detected"``). These flow-sensitive rules are deliberately NOT in the
    # ``_SINK_GUARDED_*`` set above (that helper is direct-arg-only, blind to variable
    # indirection); this dedicated helper is INDIRECTION-aware and refuses multi-line
    # strings, so a payload bound to a variable that reaches a sink
    # (``cmd = "curl…|sh"; subprocess.run(cmd, shell=True)``) and a multi-line exploit
    # template both STAY visible. A real bare ``eval(x)`` / ``os.system(x)`` CALL is not
    # inside a string Constant → keeps firing.
    if rule_id in _CALL_SHAPE_SUPPRESSIBLE_RULES and _eval_exec_message_string_is_inert(
        tree, line, source, match
    ):
        return "safe_literal"

    # r01 anthropic FP iteration (2026-05-27) — a match inside a security
    # pattern-catalog Dict literal is detection data, not exploit code.
    # The Dict must carry at least one catalog-shape key (regex / patterns
    # / substrings / signature / ...). Same iron-rule logic as Issue #42:
    # the strings in catalog dicts are inert data fed to a regex engine
    # for MATCHING, never executed. Real exploit-shape code lives in the
    # scanner that consumes the catalog, which is scanned separately.
    # Applies to ALL rules — a string in a `"substrings": [...]` list
    # cannot reach a shell whether it spells `exec(`, `rm -rf /`, or
    # `Ignore previous instructions`.
    if _match_inside_pattern_catalog(tree, line, match):
        return "safe_literal"

    # Janitor FP wave (2026-05-30) — a SECURITY plugin's detection-rule
    # catalogs (``scripts/lib/*_patterns.py``) name and describe every rule
    # in its own security vocabulary. Three intrinsic shapes:
    #
    #  A. The matched keyword is a FRAGMENT of a larger Python identifier
    #     (``_SETUID_CHMOD = _re(...)`` — ``SETUID`` is part of the variable
    #     name). A name is not an action; the action would be a CALL
    #     (``os.setuid(0)``) where the keyword is standalone, not embedded.
    #     Gated to NON-prose-vector rules.
    #  B. The matched keyword is inside a metadata/description string FIELD
    #     (``name="setuid/setgid chmod() after open"``, ``id="race-setuid-…"``)
    #     of a rule-definition constructor / dict — human-readable docs of
    #     what the rule DETECTS, never executed. Non-prose-vector → suppress;
    #     prose-vector → DEMOTE (a rule NAME/description in a NON-instruction-
    #     loadable ``.py`` catalog is never a publish-blocking CRITICAL, but a
    #     prompt-injection phrase stays VISIBLE at NIT per the iron rule).
    #     Exec-sink-guarded either way.
    #  C. Invisible/bidi/zero-width characters that are a detector's own
    #     charset vocabulary (regex char-class or a pure all-invisible string
    #     constant). The smuggling-payload shape (invisibles in visible text)
    #     stays visible.
    #  D. The matched keyword is inside a SLUG string — a single identifier-like
    #     token used as a rule-ID reference / env-var name / capability name
    #     (``rule_by_id["…ptrace…"]``, ``"LD_PRELOAD"``, ``== "race-setuid-…"``).
    #     A slug is a name, not a command. Sink-guarded.
    #
    # NON-prose-vector matches (A/B/D/module-data) are inert detection data →
    # SUPPRESS. PROSE-vector matches (DATA_EXFIL / INDIRECT_PROMPT_INJECT /
    # PROMPT_INJECT) that are the detector's own vocabulary — an exfil-domain
    # blocklist, a rule NAME/description, a slug — DEMOTE to NIT: they stay
    # VISIBLE (the agent reviews them) but never publish-block a security
    # plugin. A real injection / exfil instruction in INSTRUCTION-LOADABLE
    # prose (SKILL.md / agents) is a different surface (markdown classifier)
    # and stays at full severity.
    _is_prose = _rule_is_prose_vector(rule_id)
    if not _is_prose and _match_is_identifier_fragment(tree, source, line, match):
        return "safe_literal"
    if not _is_prose and _match_inside_slug_string(tree, line, source, match):
        return "safe_literal"
    if _match_inside_metadata_field_string(tree, line, source, match):
        return "safe_literal" if not _is_prose else "code_fence_neutral"
    if _is_prose and (
        _match_inside_module_data_literal(tree, line, source, match)
        or _match_inside_slug_string(tree, line, source, match)
    ):
        return "code_fence_neutral"
    if rule_id in ("INVISIBLE_UNICODE_RAW", "INDIRECT_PROMPT_INJECT") and _invisible_unicode_is_detector_vocab(
        tree, line, source
    ):
        return "safe_literal"

    # r03 trailofbits FP iteration (2026-05-27) — REGEX_DOS pattern
    # matched against a Python ``re.<func>(...)`` call line. The line's
    # matched span typically crosses the call boundary
    # (e.g. ``re.search(r"(\\d+)+", x)`` matches ``(r"(\\d+)`` which
    # includes the call's opening paren). Since this isn't entirely
    # inside the string Constant, ``_match_inside_re_pattern_literal``
    # doesn't catch it. But: the regex pattern being CATALOGUED is the
    # author's own catastrophic-backtracking detection vocabulary
    # (e.g. the `_REDOS_SHAPES = [re.compile(...)]` from the security-
    # guidance plugin's extensibility.py), not a live exec sink.
    # Suppress for REGEX_DOS specifically — other rules still fire normally.
    if rule_id == "REGEX_DOS" and 0 <= line_idx < len(lines) and _line_is_re_module_call(lines[line_idx]):
        return "safe_literal"

    # r03 trailofbits FP iteration (2026-05-27) — OBFUSCATION pattern
    # matches ``\\x..\\x..\\x..`` hex-escape sequences. These often
    # appear as embedded binary fixtures (PNG headers, JPEG markers,
    # PDF magic, ELF, etc.). A bytes literal whose head matches a
    # known file-format magic is unambiguously NOT obfuscated code —
    # it's a fixture image / test asset / file template.
    if (
        rule_id == "OBFUSCATION"
        and 0 <= line_idx < len(lines)
        and (
            _bytes_literal_is_file_magic(lines[line_idx])
            or _line_is_bytes_continuation_in_file_magic_tuple(lines, line_idx)
        )
    ):
        return "safe_literal"

    # Issue #65 — OBFUSCATION on a ``\\xNN\\xNN\\xNN`` hex-escape run inside a bytes
    # literal that DECODES TO PRINTABLE UTF-8 text (em-dash / unicode-punctuation
    # ``b"received \\xe2\\x80\\x94 close"`` → ``"received — close"``) AND does not flow
    # to a decode/deobfuscate/exec sink is plain text, not obfuscated code. The
    # printable-UTF-8 decode is a CONTENT proof that rules out shellcode (x86 opcodes
    # are not valid UTF-8); the sink guard keeps a literal that IS fed to
    # ``base64.b64decode`` / ``exec`` / a shell visible. FN-safe: a real obfuscated
    # payload neither decodes to printable text nor (per issue #65 evidence) fires
    # OBFUSCATION on a ``\\xNN`` run at all — it fires the decode-sink rules instead.
    if rule_id == "OBFUSCATION" and _obfuscation_bytes_literal_is_printable_text(tree, line, source):
        return "safe_literal"

    # Issue #100 (class C) — OBFUSCATION on the 3-byte UTF-8 BOM ``\\xef\\xbb\\xbf``
    # (or a UTF-16 / UTF-32 BOM) inside a bytes literal — a BOM-detection routine's
    # encoding CONSTANT (``data.startswith(b"\\xef\\xbb\\xbf")``), not obfuscated code.
    # EXACT-value match (not prefix): a BOM-PREFIXED shellcode literal
    # (``b"\\xef\\xbb\\xbf\\x90\\x90…"``) has a value not in ``_CANONICAL_BOMS`` and
    # STILL fires. The printable-UTF-8 carve-out above misses the BOM because the
    # zero-width ``"\\ufeff"`` is not ``str.isprintable()``.
    if rule_id == "OBFUSCATION" and _obfuscation_bytes_literal_is_canonical_bom(tree, line):
        return "safe_literal"

    # r03 trailofbits FP iteration (2026-05-27) — ENV_INJECTION pattern
    # ``os.environ[".*"] = `` matches every Python env-var assignment,
    # including benign ``os.environ["MY_DEBUG_FLAG"] = "1"`` config-setup
    # calls. Real injection happens when the KEY or VALUE is attacker-
    # controlled OR the KEY is a runtime-hijack var (LD_PRELOAD,
    # NODE_OPTIONS, PYTHONPATH, PYTHONSTARTUP, BASH_ENV, GIT_SSH_COMMAND).
    # Suppress when BOTH key and value are pure literals AND the key is
    # NOT in the hijack-var list.
    if rule_id == "ENV_INJECTION" and 0 <= line_idx < len(lines) and _is_safe_env_literal_set(lines[line_idx]):
        return "safe_literal"

    # Issue #75 (class 4) — ENV_INJECTION on a build-OUTPUT / download-CACHE dir
    # var (CARGO_TARGET_DIR, GOCACHE, *_CACHE_DIR, …) set to a controlled path —
    # standard build hygiene (e.g. redirect cargo's target/ to a tmp dir), not
    # injection. The allowlist excludes every runtime-hijack var, so LD_PRELOAD /
    # PATH / PYTHONPATH / NODE_OPTIONS / DYLD_* keep firing regardless of value.
    if rule_id == "ENV_INJECTION" and 0 <= line_idx < len(lines) and _is_safe_build_env_set(lines[line_idx]):
        return "safe_literal"

    # r01 FP iter (2026-05-28) — ENV_INJECTION read-modify-write of an env
    # var via a dynamic key (``os.environ[var] = <transform of existing
    # value>``) with no hijack-var literal nearby (e.g. stripping a host
    # from NO_PROXY). The value is the env's own value, not attacker input.
    if rule_id == "ENV_INJECTION" and 0 <= line_idx < len(lines) and _is_env_read_modify_write(lines, line_idx):
        return "safe_literal"

    # r03 trailofbits FP iteration (2026-05-27) — CRED_ENV_SAFE is the
    # "Credential reference (documentation)" rule by name. Inside ANY
    # Python string literal it's just text the program prints to the
    # user (e.g. a tip message like ``"Add OPENAI_API_KEY to .env"``).
    # Real credential reads happen in code (open, readFile) which fire
    # CRED_ENV_READ — not on a string literal mention. Iron-rule
    # preserved: HARDCODED_SECRET / SECRET_OPENAI_KEY / API_KEY_LEAK
    # rules fire on the actual key payload, not on the word ``.env``.
    if rule_id == "CRED_ENV_SAFE" and 0 <= line_idx < len(lines) and _line_has_quoted_string(lines[line_idx]):
        return "safe_literal"

    # Issue #124 (reopened) class 7 — SHELL_EXEC matched on a ``subprocess.Popen``
    # / ``subprocess.run`` token that is in TYPE-ANNOTATION position
    # (``p2: subprocess.Popen[bytes] | None = None``), not a call. The substring
    # appears in the annotation's Subscript value, never as a Call func, so it is
    # provably not an exec site. Runs before the call-shape dispatch (which only
    # handles actual Calls and would otherwise fall through to firing on the bare
    # token). FN-safe: a real ``subprocess.Popen(args, …)`` invocation IS a Call
    # func → not suppressed here → classified by the call-shape dispatch below
    # (list-form argv → non-blocking info; ``shell=True`` → fires).
    if rule_id == "SHELL_EXEC" and _subprocess_match_is_annotation_only(tree, line, match):
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
                # A benign call SHAPE (``safe_literal`` = literal/fixed argv,
                # no injection surface) only exonerates INJECTION-class rules
                # (CMD_INJECTION / SHELL_EXEC). A content-threat rule
                # (REVERSE_SHELL / PRIVILEGE_ESC / CRED_* / DATA_EXFIL / …)
                # fires on the literal CONTENT this call EXECUTES — a
                # hardcoded ``os.system('bash -i >& /dev/tcp/…')`` is a
                # reverse shell whether or not its argv is a literal. NEVER
                # let the call-shape verdict suppress those: fall through to
                # the heuristic chain so the threat stays visible. A
                # ``suspect`` shape (shell=True + injection) still applies to
                # every rule — it can only escalate, never hide.
                if verdict == "safe_literal" and rule_id not in _CALL_SHAPE_SUPPRESSIBLE_RULES:
                    pass  # fall through — content threat must stay visible
                else:
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

    # Issue #60 — DESERIALIZATION FP: ``yaml.load(raw, Loader=L)`` where ``L``
    # is (or transitively subclasses) a PyYAML SAFE loader
    # (``SafeLoader`` / ``CSafeLoader`` / ``BaseLoader`` / ``CBaseLoader``)
    # disables arbitrary object construction exactly as ``yaml.safe_load`` does:
    #
    #     class _DupLoader(yaml.SafeLoader): ...   # safe base
    #     tree = yaml.load(raw_text, Loader=_DupLoader)   # safe
    #
    # The class hierarchy is resolved from the AST (intrinsic) — an unresolved
    # Loader, or ``Loader=yaml.Loader`` / ``FullLoader`` / ``UnsafeLoader``,
    # stays suspect.
    if rule_id == "DESERIALIZATION" and _yaml_load_uses_safe_loader_subclass(tree, line):
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
    # Issue #57 Fix B: a security plugin's pattern catalog legitimately trips
    # execution-class rules BEYOND CMD_INJECTION/SUPPLY_CHAIN (code_execution,
    # privilege_escalation, path_traversal, reconnaissance, …). Suppress ANY
    # non-prose-vector rule whose match is an inert string in a module-level
    # pure-literal container (the catalog DEFINITION). Prose-vector rules
    # (PROMPT_INJECT / DATA_EXFIL / SECRET_* / …) stay VISIBLE — an injected
    # instruction or secret embedded in a data literal is still a delivery
    # vector. A sink that CONSUMES the catalog string
    # (e.g. os.system(PATTERNS["x"])) is a different match on a different line
    # and stays flagged — _match_inside_module_data_literal only matches the
    # pure-literal definition, never a Call argument.
    if not _rule_is_prose_vector(rule_id) and _match_inside_module_data_literal(tree, line, source, match):
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
        # r05 ananddtyagi FP iter1 (2026-05-27) — SSRF_ADVANCED fires on
        # ``def process_user_request(request):`` — a function DEFINITION,
        # not a call. The catalog pattern ``\brequest\(`` matches the
        # parameter name ``request)`` after ``def …_request(``. A function
        # def is the OPPOSITE of an outbound network call.
        if _line_is_python_function_def(source.splitlines()[line_idx]):
            return "safe_literal"
        # Issue #198 — the static-DESTINATION carve-out SSRF_PATTERN has had
        # since issue #41, applied here too. A destination fixed at author
        # time has no input path, so it is not attacker-controlled under
        # EITHER rule id; forgiving it under one and not the other was an
        # oversight, and it hard-blocked every plugin adopting canon v5.3.0.
        # Scoped to the user-input patterns and refused whenever any argument
        # is dynamic, so an f-string / concatenated / variable destination
        # keeps firing exactly as before.
        if _ssrf_advanced_destination_is_static_py(tree, line, match):
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
        _ssrf_url_is_static_literal_py(line_text, match) or _ssrf_url_is_loopback_with_literal_host_py(line_text, match)
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
        # r01 anthropic FP iter1 (2026-05-27) — LLM prompt-template
        # promotion: when the match is inside a multi-line string
        # assigned to a module-level ALL-CAPS constant whose name
        # follows the prompt-template naming convention
        # (``*_PROMPT``, ``*_TEMPLATE``, ``*_INSTRUCTIONS``,
        # ``*_MESSAGE``, ``*_GUIDANCE``, ``*_REVIEW_PROMPT``, etc.),
        # the string is documentation prose fed to an LLM, not
        # executable code. The shape is idiomatic for security-tool
        # plugins (the security-guidance plugin's
        # ``CLAUDE_CODE_SYSTEM_PROMPT`` / ``SECURITY_REVIEW_PROMPT``)
        # that ship LLM prompts describing vulnerability categories
        # with example patterns inside the prose.
        #
        # Iron rule preserved: prose-vector rules (PROMPT_INJECT,
        # DATA_EXFIL, HARDCODED_SECRET, INVISIBLE_UNICODE_RAW,
        # BASE64_DECODE_THREAT, ...) stay at safe_doc (visible NIT) —
        # those rules detect threats whose delivery vector IS prose,
        # so the string content matters even when it's a prompt.
        # Generic data strings (lowercase identifiers, plain ALL-CAPS
        # without _PROMPT suffix like ``CMD = """..."""``) also stay
        # at safe_doc — they could be used elsewhere.
        if rule_id not in {
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
        } and _is_inside_llm_prompt_template_constant(tree, line):
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

        # ALWAYS-shell string sinks (os.system / os.popen / subprocess.getoutput
        # / subprocess.getstatusoutput / commands.getoutput /
        # commands.getstatusoutput / pty.spawn / asyncio.create_subprocess_shell)
        # have NO non-shell mode — the argument string ALWAYS goes through
        # ``/bin/sh -c``. None of them takes a ``shell=`` kwarg, so the
        # ``_shell_kwarg_is_possibly_true`` test below is always False for them
        # and they would otherwise fall into the "Python guarantees no shell"
        # execve branch — which is WRONG for these sinks. A pure-literal arg is
        # the ONLY provably-inert case (``os.system("clear")``); ANY other arg
        # shape — a bare ``Name`` reassembled from fragments on a prior line, a
        # ``Call`` returning the built string, a string-concat ``BinOp`` of
        # ``Name`` operands, a ``Subscript``/``Attribute`` — is a real shell
        # execution whose CONTENT is attacker-assembled at runtime and cannot be
        # proven inert. The clear must NOT depend on whether the reassembly
        # expression sits literally at the sink or one line up in a variable —
        # that is an attacker-controllable structural signal (FN-unsafe). So
        # force ``suspect`` for every non-pure-literal arg here, BEFORE the
        # shell-kwarg test, bringing these to parity with the already-correct
        # ``subprocess.run(<Name>, shell=True)`` path. FN-safe: additive only —
        # the pure-literal arg still clears, and the argv-form sinks
        # (subprocess.run/Popen/call/check_*/os.execv*/create_subprocess_exec/
        # os.spawnv*), which truly bypass the shell, are NOT in this set and are
        # unaffected. (RT2/RT4 always-shell variable-arg FN-holes.)
        if qualname in _ALWAYS_SHELL_STRING_SINKS:
            if _arg_is_pure_literal(first):
                return "safe_literal"
            return "suspect"

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
    covering_calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and _node_qualname(n.func) in _WEAK_HASH_FQNAMES
        and getattr(n, "lineno", None) is not None
        and getattr(n, "end_lineno", None) is not None
        and n.lineno <= line <= n.end_lineno  # type: ignore[operator]
    ]
    if not covering_calls:
        return False

    # r03 trailofbits FP iter1 (2026-05-27) — ``usedforsecurity=False``
    # kwarg is Python 3.9+ explicit declaration "this hash is NOT for
    # security purposes" (the official upstream-supported way to mark
    # a non-security md5 / sha1 usage that's also FIPS-aware). The
    # weak-hash call carrying that kwarg is provably identity-only
    # regardless of slicing / LHS-naming context.
    for call in covering_calls:
        for kw in call.keywords:
            if kw.arg == "usedforsecurity" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                return True

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

    # (4) MULTI-LINE / chained shape the line-based checks (1)/(2) miss — walk
    #     up to the enclosing Assign/AnnAssign and inspect its VALUE subtree for
    #     a truncating slice (``[:N]``) or a ``.hexdigest()`` chain to an
    #     identity-named target, regardless of how many lines the call spans:
    #         sig = hashlib.sha1(
    #             ",".join(...).encode()
    #         ).hexdigest()[:8]
    #     The security-sensitive-target override is preserved (a multi-line
    #     ``password_digest = hashlib.sha1(...)[:8]`` stays VISIBLE).
    for stmt in ast.walk(tree):
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        value = stmt.value
        if value is None:
            continue
        if not any(
            isinstance(n, ast.Call)
            and _node_qualname(n.func) in _WEAK_HASH_FQNAMES
            and getattr(n, "lineno", 0) <= line <= getattr(n, "end_lineno", 0)
            for n in ast.walk(value)
        ):
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        tgt_names = [t.id for t in targets if isinstance(t, ast.Name)]
        # security-sensitive target → NEVER identity usage (audit MAJOR #8).
        if any(any(stem in tn.lower() for stem in _SECURITY_TARGET_STEMS) for tn in tgt_names):
            return False
        # truncating slice anywhere in the value subtree → identity lookup key.
        if any(isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice) for n in ast.walk(value)):
            return True
        # .hexdigest() chain assigned to an identity-named target.
        if any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "hexdigest"
            for n in ast.walk(value)
        ) and any(tn in _IDENTITY_TARGET_NAMES for tn in tgt_names):
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


def _deepest_enclosing_function(tree: ast.AST, line: int) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the deepest ``FunctionDef`` / ``AsyncFunctionDef`` whose source
    range covers ``line``, or ``None`` when ``line`` is at module level.

    "Deepest" (smallest span) so a nested helper inside a larger function is
    resolved to the helper, matching the scope a name lookup on ``line`` would
    actually see. Mirrors the covering-FunctionDef walk used by
    ``_enclosing_function_is_template_generator``.
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
    return enclosing


def _function_locally_binds(func: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """True iff ``func`` introduces a LOCAL binding for ``name`` in its own
    scope — a parameter, an ``import name`` / ``from m import name`` (incl.
    ``as name`` aliases), or an assignment target.

    A local binding shadows any module-level binding of the same name, so the
    caller must NOT treat a module-level assignment as the source of ``name``
    when this returns True. The walk descends through statements but does NOT
    enter nested ``FunctionDef`` / ``AsyncFunctionDef`` / ``ClassDef`` bodies —
    those introduce their own scopes whose bindings are not ``func``'s locals.
    """
    # Parameters.
    args = func.args
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
        if arg.arg == name:
            return True
    if args.vararg is not None and args.vararg.arg == name:
        return True
    if args.kwarg is not None and args.kwarg.arg == name:
        return True

    def _walk_scope(node: ast.AST) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # nested scope — its bindings are not func's locals
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    if bound == name:
                        return True
            elif isinstance(child, ast.Assign):
                for tgt in child.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == name:
                        return True
            elif isinstance(child, ast.AnnAssign):
                if isinstance(child.target, ast.Name) and child.target.id == name:
                    return True
            if _walk_scope(child):
                return True
        return False

    return _walk_scope(func)


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

    # 2. Search the IN-SCOPE assignments of that name for a YAML(...)
    #    constructor call. Python LEGB: a binding is in scope for the
    #    matched ``.load`` line iff it lives in the load's enclosing
    #    function body (local) OR at module level (global). An assignment
    #    inside a SIBLING function / class is NOT in scope and must be
    #    ignored — otherwise an unrelated ``yaml = YAML(typ="rt")`` in one
    #    function would suppress a genuinely-unsafe PyYAML ``yaml.load()``
    #    that merely reuses the name elsewhere (audit MED #36; the
    #    docstring already promises "same enclosing scope").
    enclosing_func = _deepest_enclosing_function(tree, line)
    enclosing_func_nodes = frozenset(map(id, ast.walk(enclosing_func))) if enclosing_func is not None else frozenset()
    module_body_ids = frozenset(map(id, tree.body)) if isinstance(tree, ast.Module) else frozenset()
    # A LOCAL binding of ``receiver_name`` inside the enclosing function
    # (``import yaml`` / ``from m import yaml`` / ``yaml = …`` / a ``yaml``
    # parameter) SHADOWS any module-level binding of the same name. When the
    # function shadows it, only the function's own assignments count — the
    # module-level ruamel instance is invisible to this ``.load`` (this is
    # exactly the case (A) FN: a local ``import yaml`` is PyYAML, not the
    # global ruamel instance). With no local binding, the function closes
    # over the module-level (global) binding, so module-level assigns count.
    local_shadows = enclosing_func is not None and _function_locally_binds(enclosing_func, receiver_name)

    def _assign_in_scope(assign_node: ast.AST) -> bool:
        # Local scope: the assignment is somewhere inside the enclosing
        # function's lexical region (its body, including nested defs — a
        # conservative accept that keeps the legitimate ruamel FP suppressed
        # without widening to sibling scopes).
        if id(assign_node) in enclosing_func_nodes:
            return True
        # Global scope: a module-level statement (direct child of Module),
        # but ONLY when the enclosing function does not shadow the name.
        if local_shadows:
            return False
        return id(assign_node) in module_body_ids

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if not _assign_in_scope(node):
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


# PyYAML loaders that disable arbitrary object construction (issue #60). A
# ``yaml.load(..., Loader=L)`` with one of these — or a subclass — is as safe
# as ``yaml.safe_load``. ``Loader`` / ``FullLoader`` / ``UnsafeLoader`` /
# ``CLoader`` / ``CFullLoader`` are deliberately EXCLUDED (they construct
# arbitrary Python objects), so they stay suspect.
_YAML_SAFE_LOADER_NAMES: Final[frozenset[str]] = frozenset({"SafeLoader", "CSafeLoader", "BaseLoader", "CBaseLoader"})


def _base_is_safe_loader(node: ast.expr) -> bool:
    """True iff an AST node names a PyYAML safe loader — ``yaml.SafeLoader``
    (Attribute) or a bare ``SafeLoader`` (Name), incl. the C/Base variants."""
    if isinstance(node, ast.Attribute):
        return node.attr in _YAML_SAFE_LOADER_NAMES
    if isinstance(node, ast.Name):
        return node.id in _YAML_SAFE_LOADER_NAMES
    return False


def _classdef_subclasses_safe_loader(tree: ast.AST, class_name: str, _depth: int = 0) -> bool:
    """True iff the ``ClassDef`` named ``class_name`` in ``tree`` transitively
    subclasses a PyYAML safe loader. Recurses through LOCAL base classes (a
    base that is itself a class defined in this module); bounded depth guards
    against cycles / pathological inheritance graphs."""
    if _depth > 10:
        return False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for base in node.bases:
            if _base_is_safe_loader(base):
                return True
            if isinstance(base, ast.Name) and _classdef_subclasses_safe_loader(tree, base.id, _depth + 1):
                return True
        return False  # found the class but no safe base
    return False


def _yaml_load_uses_safe_loader_subclass(tree: ast.AST, line: int) -> bool:
    """True iff ``line`` calls ``*.load(..., Loader=L)`` where ``L`` is (or
    transitively subclasses) a PyYAML safe loader — so the load disables
    arbitrary object construction exactly as ``yaml.safe_load`` does and the
    DESERIALIZATION match is a false positive (issue #60):

        class _DupLoader(yaml.SafeLoader): ...
        yaml.load(raw, Loader=_DupLoader)     # safe

    Conservative: a ``Loader=`` that does not resolve to a known-safe loader
    (or its subclass) — including ``yaml.Loader`` / ``FullLoader`` /
    ``UnsafeLoader``, or an unresolved name — keeps the finding visible.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None or not (start <= line <= end):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "load"):
            continue
        for kw in node.keywords:
            if kw.arg != "Loader":
                continue
            val = kw.value
            if _base_is_safe_loader(val):
                return True  # Loader=yaml.SafeLoader / SafeLoader directly
            if isinstance(val, ast.Name) and _classdef_subclasses_safe_loader(tree, val.id):
                return True  # Loader=<local class subclassing a safe loader>
            return False  # Loader= present but not safe → keep visible
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


def _match_inside_module_data_literal(tree: ast.AST, line: int, source: str, match: str) -> bool:
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
    if not _node_is_in_module_level_pure_data_assign(tree, target_const):
        return False
    # Recheck 2026-05-31 (security-bypass HIGH): a module-level container is
    # inert data ONLY if its bound name does NOT flow to a live exec / fs /
    # net sink. ``PAYLOADS = ['bash -i >& /dev/tcp/…']; os.system(PAYLOADS[0])``
    # parks a reverse shell in a module literal but EXECUTES it via subscript —
    # the SAME content-threat the v2.114.0 ``_classify_call`` hardening keeps
    # visible on the DIRECT-literal path. The sink-flow guard already existed
    # (wired into the abs-path / SECRET_* branch) but was never called here, so
    # the indirect container-subscript path silently suppressed REVERSE_SHELL /
    # CRED_* / PRIVILEGE_ESC / EXFIL to info. Wire it in — this fixes BOTH
    # callers (the non-prose-vector ``safe_literal`` suppressor AND the
    # prose-vector ``code_fence_neutral`` demoter).
    return not _module_container_name_flows_to_sink(tree, target_const)


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
            # A ``None`` key marks a ``**spread`` entry (``{**other, …}``).
            # The spread value can be ANY object (a non-literal variable,
            # a call result), so a dict that contains one is NOT a pure
            # literal — reject it on the spot. (Previously ``if k is not
            # None`` SKIPPED the spread entry, wrongly certifying
            # ``{**other, '/p': 'x'}`` as pure-literal; audit MED #37.)
            for k, v in zip(n.keys, n.values, strict=False):
                if k is None:  # **spread → not a pure literal
                    return False
                if not (_is_pure_literal_data(k) and _is_pure_literal_data(v)):
                    return False
            return True
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
        # See through a container-constructor call wrapping a single literal
        # container — ``frozenset({...})`` / ``set({...})`` / ``tuple([...])``
        # / ``list([...])`` / ``dict({...})`` are idiomatic module-level
        # constant-data declarations (e.g. a detector's ``_SAFE = frozenset({
        # "/usr/share/git-core/templates", ...})``). Same data-declaration
        # intent and same risk profile as a bare ``{...}`` literal, which this
        # function already accepts. (issue #57 Fix A — constructor-call ext.)
        if (
            isinstance(val, ast.Call)
            and isinstance(val.func, ast.Name)
            and val.func.id in {"frozenset", "set", "tuple", "list", "dict"}
            and len(val.args) == 1
            and not val.keywords
            and isinstance(val.args[0], (ast.List, ast.Tuple, ast.Set, ast.Dict))
        ):
            val = val.args[0]
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


def _module_container_name_flows_to_sink(tree: ast.AST, target: ast.AST) -> bool:
    """Defense-in-depth guard for the module-level pure-data-container
    suppression. A module-level ``NAME = {…paths…}`` / ``frozenset({…})`` is
    normally inert data — but if ``NAME`` is then OPENED / EXEC'd, directly
    (``open(NAME)``), by iteration (``for p in NAME: open(p)``), or via a
    subscript / conversion (``open(list(NAME)[0])``), the paths are LIVE and
    the suppression must NOT apply. Returns ``True`` iff the container's bound
    name reaches a live fs/exec/net sink, so the caller keeps the finding
    visible. Conservative (over-keeps-visible on ambiguity). Closes the
    pre-existing indirect-via-variable hole for module-level containers.
    (issue #57 Fix A — container-name sink guard)
    """
    if not isinstance(tree, ast.Module):
        return False

    def _contains(n: ast.AST, t: ast.AST) -> bool:
        if n is t:
            return True
        return any(_contains(c, t) for c in ast.iter_child_nodes(n))

    # Mirror the constructor-unwrap of _node_is_in_module_level_pure_data_assign
    # so we collect the SAME assigns' bound names.
    names: set[str] = set()
    for stmt in tree.body:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        val = stmt.value
        if val is None:
            continue
        if (
            isinstance(val, ast.Call)
            and isinstance(val.func, ast.Name)
            and val.func.id in {"frozenset", "set", "tuple", "list", "dict"}
            and len(val.args) == 1
            and not val.keywords
            and isinstance(val.args[0], (ast.List, ast.Tuple, ast.Set, ast.Dict))
        ):
            val = val.args[0]
        if not isinstance(val, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            continue
        if not _contains(val, target):
            continue
        bound = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        # Collect EVERY bound Name in each target subtree — not just a top-level
        # ``Name``. A destructuring target (``(A, B) = ([...], [...])`` /
        # ``[A, *B] = (...)``) binds names INSIDE a Tuple/List/Starred, which a
        # top-level-only check missed → the sink guard saw no bound name and
        # failed open, so ``os.system(A[0])`` after such an unpack silently
        # suppressed the reverse shell. Walking the target subtree binds A and B
        # conservatively (over-keeps-visible: any of them reaching a sink keeps
        # the finding). ``_node_is_in_module_level_pure_data_assign`` already
        # matches the same destructuring assign (its value is the pure outer
        # container), so both functions now agree on which assigns qualify.
        for tnode in bound:
            for sub in ast.walk(tnode):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
    if not names:
        return False

    sinks = _STRING_CMD_EXEC_FQNAMES | _FS_NET_SINK_FQNAMES | _SHELL_CALL_FQNAMES

    def _name_in_call_args(call: ast.Call) -> bool:
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Name) and sub.id in names:
                    return True
        return False

    for node in ast.walk(tree):
        # (a) NAME (or an expression containing it) is an argument to a sink.
        if isinstance(node, ast.Call) and _node_qualname(node.func) in sinks and _name_in_call_args(node):
            return True
        # (b) `for x in NAME:` whose body contains any sink call.
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and node.iter.id in names:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and _node_qualname(sub.func) in sinks:
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
