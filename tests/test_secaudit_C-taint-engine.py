#!/usr/bin/env python3
"""Two-sided red-team regression tests — group C-taint-engine (RT5).

Finding: RT5-skillaudit-sink-obfuscation-alias-eval (CRITICAL fn-hole) against
``scripts/cpv_taint_engine.py``.

Root cause (pre-fix): ``_is_sink_call`` resolved a call's callee ONLY through
``_attribute_chain`` — a bare ``ast.Name`` (``exec``/``eval``/``compile``) or a
dotted ``ast.Attribute`` chain (``os.system``). Aliasing a sink to a local name
(``e = eval; e(tainted)`` / ``s = os.system; s(tainted)`` /
``run = subprocess.run; run(tainted, shell=True)``) made the callee a plain
``Name('e')`` that matched NO sink vocabulary, so the tainted source→sink path
was silently dropped — a live ``os.environ.get()`` → aliased-``os.system``
escalation produced ZERO findings (full taint-layer bypass).

Fix: per-scope sink-alias resolution in ``_TaintState.sink_aliases`` populated by
``_resolve_sink_target`` during ``_process_assignment``; ``_is_sink_call`` /
``_is_exec_class_sink`` re-point a bare-name alias to its underlying sink before
the membership check. FN-safe in both directions: it resolves an alias ONLY when
the RHS is provably bound to a vocabulary sink, and respects local SHADOWING
(``def eval`` / ``eval = x`` / ``import … as eval`` / a parameter named ``eval``),
so a non-sink alias creates no spurious finding.

EVERY test is TWO-SIDED: the obfuscated MALICIOUS shape MUST now fire (a
source→sink taint finding), and the matching BENIGN shape the discriminator must
preserve MUST still clear (zero findings). A one-sided assertion would not prove
the fix is FN-safe.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_taint_engine import TaintFinding, analyze_module  # noqa: E402


def _analyze(src: str) -> list[TaintFinding]:
    return analyze_module(ast.parse(src))


def _fires(src: str) -> bool:
    """True iff the taint engine reports at least one source→sink finding."""
    return len(_analyze(src)) > 0


# A tainted source bound to ``x`` reused across the malicious fixtures.
_SRC = 'import os\nx = os.environ.get("CMD")\n'


# -----------------------------------------------------------------------------
# Baseline — direct (un-aliased) sinks fire. Anchors the fixtures as "valid":
# the SAME payload via the direct sink is a known CRITICAL, so a 0 on the
# aliased form below is genuinely the FN-hole, not an inert fixture.
# -----------------------------------------------------------------------------


class TestBaselineDirectSinksFire:
    """Un-aliased sinks fire — confirms the payloads are real source→sink paths."""

    def test_direct_eval_fires(self) -> None:
        assert _fires(_SRC + "eval(x)\n")

    def test_direct_exec_fires(self) -> None:
        assert _fires("x = input()\nexec(x)\n")

    def test_direct_os_system_fires(self) -> None:
        assert _fires(_SRC + "os.system(x)\n")

    def test_direct_subprocess_run_shell_fires(self) -> None:
        assert _fires(_SRC + "import subprocess\nsubprocess.run(x, shell=True)\n")


# -----------------------------------------------------------------------------
# Alias of a DIRECT builtin sink — e = eval / ex = exec.
# -----------------------------------------------------------------------------


class TestAliasDirectBuiltinSink:
    """``e = eval; e(tainted)`` must fire; a non-sink/untainted alias must not."""

    def test_alias_eval_malicious_fires(self) -> None:
        # MALICIOUS: aliased eval executes an env-controlled expression.
        findings = _analyze(_SRC + "e = eval\ne(x)\n")
        assert len(findings) == 1
        assert "eval" in findings[0].sink
        assert findings[0].var_name == "x"

    def test_alias_exec_malicious_fires(self) -> None:
        findings = _analyze("x = input()\nex = exec\nex(x)\n")
        assert any("exec" in f.sink for f in findings)

    def test_alias_compile_malicious_fires(self) -> None:
        findings = _analyze(_SRC + "c = compile\nc(x, '<s>', 'exec')\n")
        assert any("compile" in f.sink for f in findings)

    def test_non_sink_alias_benign_clears(self) -> None:
        # BENIGN (preserve): aliasing a NON-sink (print) must not invent a sink.
        assert not _fires("x = input()\ng = print\ng(x)\n")

    def test_alias_with_untainted_arg_benign_clears(self) -> None:
        # BENIGN (preserve): the alias is real but the arg is a literal, no taint.
        assert not _fires("e = eval\ne('1 + 1')\n")


# -----------------------------------------------------------------------------
# Alias of a QUALIFIED sink — s = os.system / run = subprocess.run.
# -----------------------------------------------------------------------------


class TestAliasQualifiedSink:
    """``s = os.system; s(tainted)`` must fire; the shell=True gate is preserved."""

    def test_alias_os_system_malicious_fires(self) -> None:
        findings = _analyze(_SRC + "s = os.system\ns(x)\n")
        assert len(findings) == 1
        assert "os.system" in findings[0].sink

    def test_alias_os_popen_malicious_fires(self) -> None:
        assert _fires(_SRC + "p = os.popen\np(x)\n")

    def test_alias_subprocess_run_shell_true_fires(self) -> None:
        findings = _analyze(_SRC + "import subprocess\nrun = subprocess.run\nrun(x, shell=True)\n")
        assert any("subprocess.run" in f.sink and "shell=True" in f.sink for f in findings)

    def test_alias_subprocess_run_without_shell_stays_silent(self) -> None:
        # BENIGN/preserve: subprocess.run is a sink ONLY with shell=True — the
        # alias must NOT relax that gate (run(x) with no shell= is not a shell sink).
        assert not _fires(_SRC + "import subprocess\nrun = subprocess.run\nrun(x)\n")

    def test_non_sink_attribute_alias_benign_clears(self) -> None:
        # BENIGN (preserve): aliasing a non-sink attribute (os.getcwd) is inert.
        assert not _fires(_SRC + "g = os.getcwd\ng(x)\n")


# -----------------------------------------------------------------------------
# Alias-of-alias — t = s where s = os.system.
# -----------------------------------------------------------------------------


class TestAliasOfAlias:
    """A second-level alias still resolves to the underlying sink."""

    def test_alias_of_alias_malicious_fires(self) -> None:
        findings = _analyze(_SRC + "s = os.system\nt = s\nt(x)\n")
        assert any("os.system" in f.sink for f in findings)

    def test_alias_of_non_sink_benign_clears(self) -> None:
        # BENIGN (preserve): chaining aliases of a non-sink stays inert.
        assert not _fires(_SRC + "a = os.getcwd\nb = a\nb(x)\n")


# -----------------------------------------------------------------------------
# Aliased EXEC-class sink + structured-parser exec-risk path (audit MAJOR #10).
# An exec-class alias must still pick up json.loads-sanitized exec-risk taint;
# a non-exec-class alias (os.system) must NOT (matching direct-call behaviour).
# -----------------------------------------------------------------------------


class TestAliasExecClassRisk:
    """Aliasing must not change a sink's exec-class status either way."""

    def test_alias_eval_picks_up_exec_risk(self) -> None:
        # MALICIOUS: eval(json.loads(env)) is exec-class; aliased eval must match.
        src = 'import os, json\nx = os.environ.get("X")\nd = json.loads(x)\ne = eval\ne(d)\n'
        assert _fires(src)

    def test_alias_os_system_does_not_pick_up_exec_risk(self) -> None:
        # BENIGN/consistent: json.loads clears INJECTION taint; os.system is NOT
        # exec-class, so the sanitized value is not re-flagged — same as the
        # direct os.system(json.loads(env)) case (no over-report via alias).
        src = 'import os, json\nx = os.environ.get("X")\nd = json.loads(x)\ns = os.system\ns(d)\n'
        assert not _fires(src)
        # Parity check: the direct form is likewise silent.
        direct = 'import os, json\nx = os.environ.get("X")\nd = json.loads(x)\nos.system(d)\n'
        assert not _fires(direct)


# -----------------------------------------------------------------------------
# SHADOWING — the no-FP backbone. A locally-rebound builtin-sink name means a
# later ``e = <name>`` aliases the LOCAL object, not the builtin sink.
# Each test pairs the benign shadow (must clear) with the unshadowed malicious
# control (must fire) so the suppression is proven SCOPED, not blanket.
# -----------------------------------------------------------------------------


class TestShadowingPreservesNoFalsePositive:
    """A rebound ``eval``/``exec`` name is not the builtin sink — no spurious hit."""

    def test_def_shadow_clears_but_unshadowed_fires(self) -> None:
        shadowed = _SRC + "def eval(z):\n    return z\ne = eval\ne(x)\n"
        assert not _fires(shadowed)  # BENIGN: e aliases the local def eval
        assert _fires(_SRC + "e = eval\ne(x)\n")  # CONTROL: builtin eval → fires

    def test_assign_shadow_clears(self) -> None:
        # eval rebound to str → e = eval is a str alias, not a code-exec sink.
        assert not _fires(_SRC + "eval = str\ne = eval\ne(x)\n")

    def test_import_as_shadow_clears(self) -> None:
        # `from shlex import quote as eval` rebinds eval to a sanitizer.
        assert not _fires(_SRC + "from shlex import quote as eval\ne = eval\ne(x)\n")

    def test_class_shadow_clears(self) -> None:
        assert not _fires(_SRC + "class compile:\n    pass\nc = compile\nc(x, '<s>', 'exec')\n")

    def test_parameter_named_like_sink_clears_but_module_fires(self) -> None:
        # Inside the function, `eval` is the tainted PARAMETER, so `e = eval` is
        # data passthrough — exec is never reached via a builtin sink here.
        shadowed = 'import os\ndef f(eval):\n    x = os.environ.get("CMD")\n    e = eval\n    e(x)\n'
        assert not _fires(shadowed)
        # CONTROL: same body at module scope (no param shadow) DOES fire.
        assert _fires(_SRC + "e = eval\ne(x)\n")


# -----------------------------------------------------------------------------
# Reassignment — an alias name later rebound to data stops being a sink, and a
# data variable later rebound to a sink starts being one.
# -----------------------------------------------------------------------------


class TestAliasReassignment:
    """The alias map tracks the LATEST binding of a name (linear flow)."""

    def test_alias_then_overwritten_with_data_clears(self) -> None:
        # s aliases os.system, then is overwritten by a plain string → s(x) is
        # no longer a recognised sink (the string is not callable as os.system).
        assert not _fires(_SRC + 's = os.system\ns = "harmless"\ns(x)\n')

    def test_data_name_then_rebound_to_sink_fires(self) -> None:
        # s starts as data, then becomes an os.system alias → the later s(x) fires.
        src = 'import os\ns = "init"\nx = os.environ.get("CMD")\ns = os.system\ns(x)\n'
        assert _fires(src)


# -----------------------------------------------------------------------------
# End-to-end decisive pair — the report's "same payload, opposite verdict" shape,
# at the analyze_module level: a privilege-escalation command reaching an aliased
# os.system fires exactly as the direct form does.
# -----------------------------------------------------------------------------


class TestDecisivePair:
    """The report's headline: aliased sink with a live payload is no longer inert."""

    def test_aliased_sudo_disable_apparmor_fires(self) -> None:
        malicious = (
            "import os\n"
            'cmd = os.environ.get("BOOT_CMD")\n'
            "elevate = os.system\n"
            "elevate(cmd)\n"  # at runtime: os.system(<env>) — root escalation vector
        )
        findings = _analyze(malicious)
        assert len(findings) >= 1
        assert any("os.system" in f.sink for f in findings)

    def test_benign_hint_alias_never_called_clears(self) -> None:
        # The de-noising the suppressor exists for: an os.system reference that is
        # NEVER invoked on tainted data must stay silent.
        benign = (
            "import os\n"
            "elevate = os.system\n"
            'note = "elevate runs os.system; do not call on user input"\n'
        )
        assert not _fires(benign)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
