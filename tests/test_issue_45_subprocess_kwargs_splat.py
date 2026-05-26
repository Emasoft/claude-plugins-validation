#!/usr/bin/env python3
"""Regression lock for issue #45: list-form ``subprocess.run([...], **kwargs)``
must NOT emit SHELL_EXEC at MINOR severity.

Bug (v2.107.2 and earlier): the SHELL_EXEC / CMD_INJECTION discriminator
in ``_skillaudit_python_context.py`` treated ANY ``**kwargs`` splat as
"shell could be True via kwargs" — and then required the FIRST ARG to be
a pure literal string to suppress. A list-form first arg (the stdlib-
recommended shape) with a ``**kw`` splat thus stayed flagged as suspect,
producing noise on:

    subprocess.run(["docker", *args], **kw)       # inline list literal
    subprocess.run(cmd, **kwargs)                 # bare Name → list[str]

CPV's own ``scripts/publish.py`` ships the second shape verbatim
(retry-fallback) so every CPV-managed plugin inherited 2 spurious MINORs.

Fix (v2.107.3): two new discriminators wired into ``_classify_call``:

* ``_shell_signal_only_via_splat(call)`` — True iff the ONLY
  ``shell=possibly-true`` signal is the ``**kwargs`` splat (no explicit
  ``shell=`` keyword). Distinguishes the routine ``**kw`` shape from the
  genuinely dangerous explicit ``shell=True`` / ``shell=use_shell``.
* ``_first_arg_is_argv_safe_shape_py(arg)`` — True iff ``arg`` matches a
  conventional argv shape: ``Name`` / ``Subscript`` / ``Attribute`` (by
  Python convention holds a ``list[str]``), OR a ``List`` / ``Tuple``
  literal whose elements are all known-safe (Constant / Starred Name /
  Name / Subscript / Attribute / safe Call) with no f-string / no
  string-concat exploit element.

When BOTH fire, the dispatch returns ``safe_literal`` (suppress). This
matches Bandit B603 / ruff S603 behaviour. Explicit ``shell=True`` or
``shell=<non-literal>`` keeps the existing strict path (only safe with a
pure-literal-string first arg).

Two-sided coverage:
* POSITIVE — list-form / Name first arg + ``**kw`` is suppressed
  (the user's reported FPs).
* NEGATIVE — explicit ``shell=True``, string-form first arg, f-string
  first arg, BinOp concat first arg, list with f-string element all
  STILL flag. Security gate intact.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from _skillaudit_python_context import (  # noqa: E402
    _classify_call,
    _first_arg_is_argv_safe_shape_py,
    _shell_signal_only_via_splat,
)


def _parse_call(src: str) -> ast.Call:
    """Parse a one-liner Python expression and return its top-level Call
    node. Used to drive the AST-level discriminators directly."""
    tree = ast.parse(src, mode="eval")
    assert isinstance(tree, ast.Expression), src
    assert isinstance(tree.body, ast.Call), src
    return tree.body


# ── _shell_signal_only_via_splat ──────────────────────────────────────────


class TestShellSignalOnlyViaSplat:
    """Correctly distinguishes ``**kw`` from explicit ``shell=`` signals."""

    def test_only_kwargs_splat_returns_true(self) -> None:
        call = _parse_call("subprocess.run(cmd, **kw)")
        assert _shell_signal_only_via_splat(call) is True

    def test_kwargs_with_explicit_shell_returns_false(self) -> None:
        """An explicit ``shell=...`` keyword overrides — handled by the
        other discriminator (``_shell_kwarg_is_possibly_true``)."""
        call = _parse_call("subprocess.run(cmd, shell=False, **kw)")
        assert _shell_signal_only_via_splat(call) is False

    def test_explicit_shell_true_returns_false(self) -> None:
        call = _parse_call("subprocess.run(cmd, shell=True)")
        assert _shell_signal_only_via_splat(call) is False

    def test_no_splat_no_shell_returns_false(self) -> None:
        """No ``**kw`` AND no ``shell=`` — nothing to be "only via splat"."""
        call = _parse_call("subprocess.run(cmd)")
        assert _shell_signal_only_via_splat(call) is False

    def test_multiple_splats_still_only_via_splat(self) -> None:
        call = _parse_call("subprocess.run(cmd, **kw1, **kw2)")
        assert _shell_signal_only_via_splat(call) is True


# ── _first_arg_is_argv_safe_shape_py ──────────────────────────────────────


class TestFirstArgIsArgvSafeShape:
    """The shape gate accepts conventional argv shapes, rejects exploit
    shapes."""

    def test_list_of_constants_is_safe(self) -> None:
        call = _parse_call('subprocess.run(["docker", "ps", "-a"], **kw)')
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is True

    def test_list_with_starred_name_is_safe(self) -> None:
        """The user's exact shape: ``["docker", *args]``."""
        call = _parse_call('subprocess.run(["docker", *args], **kw)')
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is True

    def test_tuple_argv_is_safe(self) -> None:
        call = _parse_call('subprocess.run(("ls", "-la"), **kw)')
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is True

    def test_bare_name_is_safe(self) -> None:
        """``subprocess.run(cmd, **kwargs)`` — publish.py's exact shape."""
        call = _parse_call("subprocess.run(cmd, **kwargs)")
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is True

    def test_subscript_is_safe(self) -> None:
        call = _parse_call("subprocess.run(cmds[0], **kw)")
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is True

    def test_attribute_is_safe(self) -> None:
        call = _parse_call("subprocess.run(self.cmd, **kw)")
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is True

    def test_string_constant_is_not_safe_shape(self) -> None:
        """String-form is NOT an argv shape — falls through to the
        ``_arg_is_pure_literal`` check (which DOES suppress single
        literal strings via the existing branch)."""
        call = _parse_call('subprocess.run("clear", **kw)')
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is False

    def test_list_with_fstring_element_is_unsafe(self) -> None:
        """An f-string IN the argv list can carry attacker input AS the
        command (argv[0] = ``rm -rf /tmp/{user}``). Reject."""
        call = _parse_call('subprocess.run([f"rm -rf {user}"], **kw)')
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is False

    def test_list_with_concat_element_is_unsafe(self) -> None:
        """String concatenation IN argv is the classic injection shape."""
        call = _parse_call('subprocess.run(["rm -rf " + user], **kw)')
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is False

    def test_empty_list_is_not_safe(self) -> None:
        """An empty argv literal can't be exec'd anyway — reject to keep
        the safe-list gate well-formed."""
        call = _parse_call("subprocess.run([], **kw)")
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is False

    def test_binop_first_arg_is_unsafe(self) -> None:
        """``cmd + ["x"]`` ambiguous — already handled by exploit-shape
        BUT this top-level shape gate should reject (it's not a List
        literal we can validate)."""
        call = _parse_call('subprocess.run("rm -rf " + user, **kw)')
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is False

    def test_fstring_first_arg_is_unsafe(self) -> None:
        call = _parse_call('subprocess.run(f"rm -rf {user}", **kw)')
        assert _first_arg_is_argv_safe_shape_py(call.args[0]) is False


# ── End-to-end _classify_call dispatch ────────────────────────────────────


class TestClassifyCallSuppressesUserReportedFPs:
    """The exact shapes from issue #45 — all must classify as ``safe_literal``."""

    def test_inline_list_starred_kwargs_suppressed(self) -> None:
        """The user's docker wrapper case."""
        call = _parse_call(
            'subprocess.run(["docker", *args], capture_output=True, text=True, check=False, **kw)'
        )
        assert _classify_call(call, "subprocess.run") == "safe_literal"

    def test_bare_name_kwargs_suppressed(self) -> None:
        """CPV publish.py's retry-fallback shape."""
        call = _parse_call("subprocess.run(cmd, **kwargs)")
        assert _classify_call(call, "subprocess.run") == "safe_literal"

    def test_popen_with_list_and_kwargs_suppressed(self) -> None:
        """Same gate applies to other shell-reaching qualnames."""
        call = _parse_call('subprocess.Popen(["git", "log"], **kw)')
        assert _classify_call(call, "subprocess.Popen") == "safe_literal"


class TestClassifyCallStillFlagsRealThreats:
    """Security gate intact — the v2.107.3 fix must NOT silence genuine
    SHELL_EXEC / CMD_INJECTION exploit shapes."""

    def test_explicit_shell_true_with_concat_still_suspect(self) -> None:
        """The canonical injection shape — string concat + ``shell=True``."""
        call = _parse_call('subprocess.run("rm -rf " + user, shell=True)')
        assert _classify_call(call, "subprocess.run") == "suspect"

    def test_explicit_shell_true_with_fstring_still_suspect(self) -> None:
        call = _parse_call('subprocess.run(f"rm -rf {user}", shell=True)')
        assert _classify_call(call, "subprocess.run") == "suspect"

    def test_explicit_shell_use_shell_with_name_still_suspect(self) -> None:
        """``shell=<non-literal>`` — analyser can't prove falsey."""
        call = _parse_call("subprocess.run(cmd, shell=use_shell)")
        assert _classify_call(call, "subprocess.run") == "suspect"

    def test_list_with_fstring_element_and_kwargs_still_suspect(self) -> None:
        """List with f-string in argv[0] — if ``**kw`` carries
        ``shell=True``, this IS injection. Stays flagged."""
        call = _parse_call('subprocess.run([f"docker {user}"], **kw)')
        assert _classify_call(call, "subprocess.run") == "suspect"

    def test_string_form_with_kwargs_still_suspect(self) -> None:
        """``subprocess.run("string", **kw)`` — string concat hidden in
        the string-form first arg is the dangerous case when **kw might
        carry ``shell=True``. Stays flagged (no argv-safe-shape match)."""
        call = _parse_call('subprocess.run("rm -rf " + user, **kw)')
        assert _classify_call(call, "subprocess.run") == "suspect"

    def test_explicit_shell_true_with_literal_string_still_safe(self) -> None:
        """Existing behaviour preserved: ``shell=True`` with a pure
        literal string is still safe (no attacker bytes reach the
        shell)."""
        call = _parse_call('subprocess.run("clear", shell=True)')
        assert _classify_call(call, "subprocess.run") == "safe_literal"


class TestNoShellKwargBehaviourUnchanged:
    """When there is NO ``**kw`` and NO ``shell=``, the existing safe
    path was already correct — pin it so the v2.107.3 changes didn't
    accidentally regress it."""

    def test_plain_list_no_kwargs_no_shell_is_safe(self) -> None:
        call = _parse_call('subprocess.run(["docker", "ps"])')
        assert _classify_call(call, "subprocess.run") == "safe_literal"

    def test_plain_list_with_capture_output_is_safe(self) -> None:
        call = _parse_call('subprocess.run(["git", "log"], capture_output=True, text=True)')
        assert _classify_call(call, "subprocess.run") == "safe_literal"

    def test_explicit_shell_false_with_list_is_safe(self) -> None:
        call = _parse_call('subprocess.run(["ls"], shell=False)')
        assert _classify_call(call, "subprocess.run") == "safe_literal"
