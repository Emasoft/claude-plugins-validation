#!/usr/bin/env python3
"""Regression locks for scripts/_skillaudit_python_context.py (TRDD-a4260cc6).

The classifier returns suppress/demote/keep verdicts based on AST shape
around a regex match. These tests pin every documented verdict on every
input shape we care about, plus the "iron rule" falls-through-to-unknown
behavior for parse failures.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ────────────────────────────────────────────────────────────────────────
# Helper: locate the 0-based line index of a substring inside source
# ────────────────────────────────────────────────────────────────────────


def _line_idx_of(src: str, needle: str) -> int:
    """Return the 0-based line index where ``needle`` first appears.

    The classifier's ``line_idx`` parameter is 0-based; the AST uses
    1-based line numbers internally. Tests express positions in the
    classifier's contract (0-based) and trust the classifier to convert.
    """
    offset = src.index(needle)
    return src.count("\n", 0, offset)


# ────────────────────────────────────────────────────────────────────────
# TestSafeLiteral — all-literal shell calls return "safe_literal"
# ────────────────────────────────────────────────────────────────────────


class TestSafeLiteral:
    def test_subprocess_run_with_literal_argv(self) -> None:
        """subprocess.run with an all-Constant argv list is safe_literal."""
        import _skillaudit_python_context as ctx

        src = 'import subprocess\nresult = subprocess.run(["git-cliff", "--version"], capture_output=True)'
        idx = _line_idx_of(src, "git-cliff")
        verdict = ctx.classify("scripts/test.py", src, idx, "git-cliff", "CMD_INJECTION")
        assert verdict == "safe_literal"

    def test_subprocess_popen_with_literal_argv(self) -> None:
        """subprocess.Popen with an all-literal argv list is safe_literal."""
        import _skillaudit_python_context as ctx

        src = 'import subprocess\np = subprocess.Popen(["ls", "-la"])'
        idx = _line_idx_of(src, '"ls"')
        verdict = ctx.classify("scripts/test.py", src, idx, "ls", "CMD_INJECTION")
        assert verdict == "safe_literal"

    def test_os_system_with_single_literal_string(self) -> None:
        """os.system with a single literal string and no shell=True is safe_literal."""
        import _skillaudit_python_context as ctx

        src = 'import os\nos.system("clear")'
        idx = _line_idx_of(src, "clear")
        verdict = ctx.classify("scripts/test.py", src, idx, "clear", "SHELL_EXEC")
        assert verdict == "safe_literal"

    def test_subprocess_check_output_with_literal_argv(self) -> None:
        """subprocess.check_output with all-literal argv and text=True is safe_literal."""
        import _skillaudit_python_context as ctx

        src = 'import subprocess\nout = subprocess.check_output(["git", "log"], text=True)'
        idx = _line_idx_of(src, '"git"')
        verdict = ctx.classify("scripts/test.py", src, idx, "git", "CMD_INJECTION")
        assert verdict == "safe_literal"

    def test_subprocess_run_with_explicit_shell_false(self) -> None:
        """subprocess.run with shell=False explicit and literal argv is safe_literal."""
        import _skillaudit_python_context as ctx

        src = 'import subprocess\nsubprocess.run(["echo", "hello"], shell=False)'
        idx = _line_idx_of(src, '"echo"')
        verdict = ctx.classify("scripts/test.py", src, idx, "echo", "CMD_INJECTION")
        assert verdict == "safe_literal"


# ────────────────────────────────────────────────────────────────────────
# TestSuspect — shell calls with non-literal args return "suspect"
# ────────────────────────────────────────────────────────────────────────


class TestSuspect:
    def test_subprocess_run_fstring_with_shell_true(self) -> None:
        """subprocess.run with an f-string first arg and shell=True is suspect."""
        import _skillaudit_python_context as ctx

        src = 'import subprocess\nurl = "http://x"\nsubprocess.run(f"curl {url}", shell=True)'
        idx = _line_idx_of(src, "curl")
        verdict = ctx.classify("scripts/test.py", src, idx, "curl", "CMD_INJECTION")
        assert verdict == "suspect"

    def test_subprocess_run_variable_in_argv_list(self) -> None:
        """subprocess.run with a bare Name in argv is safe_literal at this site.

        Per TRDD-a4260cc6 design + issue #33 acceptance: the matcher
        only flags the EXPLICIT exploit shape (f-string / BinOp concat /
        .format / .join). A bare variable reference is invisible to the
        regex matcher at the call site — the actual injection (if any)
        happens at the data-flow source, which downstream agents catch.
        Calibration target is "0 NIT" on real plugins, so bare-Name
        elements are SUPPRESSED (not demoted) at the call site.
        """
        import _skillaudit_python_context as ctx

        src = 'import subprocess\ncmd = "git"\narg = "log"\nsubprocess.run([cmd, arg], shell=False)'
        idx = _line_idx_of(src, "[cmd, arg]")
        verdict = ctx.classify("scripts/test.py", src, idx, "subprocess.run", "CMD_INJECTION")
        assert verdict == "safe_literal"

    def test_subprocess_run_binop_concat_with_shell_true(self) -> None:
        """subprocess.run with string concatenation and shell=True is suspect."""
        import _skillaudit_python_context as ctx

        src = 'import subprocess\npath = "/tmp"\nsubprocess.run("rm -rf " + path, shell=True)'
        idx = _line_idx_of(src, "rm -rf")
        verdict = ctx.classify("scripts/test.py", src, idx, "rm -rf", "CMD_INJECTION")
        assert verdict == "suspect"

    def test_os_system_with_fstring(self) -> None:
        """os.system with an f-string argument is suspect."""
        import _skillaudit_python_context as ctx

        src = 'import os\nx = "user"\nos.system(f"sudo {x}")'
        idx = _line_idx_of(src, "sudo")
        verdict = ctx.classify("scripts/test.py", src, idx, "sudo", "SHELL_EXEC")
        assert verdict == "suspect"

    def test_subprocess_run_fstring_element_inside_argv(self) -> None:
        """subprocess.run with an f-string element inside an argv list is safe_literal.

        v2.100.0 refinement: list-form argv elements are NEVER shell-expanded
        by subprocess (they pass through as one argv each, verbatim). An
        f-string inside the list evaluates to a typed Python string before
        the list is constructed; the subprocess receives that string as a
        single argv with no shell interpretation. The only injection-shape
        risk for list-form subprocess calls is ``shell=True`` PLUS an
        f-string / BinOp concat as the WHOLE first arg — that case is
        tested separately in TestSuspect.
        """
        import _skillaudit_python_context as ctx

        src = 'import subprocess\nbranch = "main"\nsubprocess.run([f"git-{branch}", "log"])'
        idx = _line_idx_of(src, "git-")
        verdict = ctx.classify("scripts/test.py", src, idx, "git-", "CMD_INJECTION")
        assert verdict == "safe_literal"


# ────────────────────────────────────────────────────────────────────────
# TestSafeDoc — matches inside strings/comments return "safe_doc"
# ────────────────────────────────────────────────────────────────────────


class TestSafeDoc:
    def test_match_inside_module_docstring(self) -> None:
        """A match inside a triple-quoted module docstring is safe_doc."""
        import _skillaudit_python_context as ctx

        src = '"""Example usage:\n\n    subprocess.run(["danger", "command"])\n"""\nimport os\n'
        idx = _line_idx_of(src, "danger")
        verdict = ctx.classify("scripts/test.py", src, idx, "danger", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_match_inside_triple_quoted_data_string(self) -> None:
        """A match inside a triple-quoted string assigned to a variable is safe_doc."""
        import _skillaudit_python_context as ctx

        src = 'EXAMPLE = """\nsubprocess.run(["evil", "cmd"], shell=True)\n"""\n'
        idx = _line_idx_of(src, "evil")
        verdict = ctx.classify("scripts/test.py", src, idx, "evil", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_match_inside_full_line_comment(self) -> None:
        """A match inside a full-line `#` comment is safe_doc (fast path)."""
        import _skillaudit_python_context as ctx

        src = 'import subprocess\n# subprocess.run(["dangerous", "x"], shell=True)\nprint("hi")\n'
        idx = _line_idx_of(src, "dangerous")
        verdict = ctx.classify("scripts/test.py", src, idx, "dangerous", "CMD_INJECTION")
        assert verdict == "safe_doc"

    def test_match_inside_triple_quoted_fstring_constant_portion(self) -> None:
        """A match inside the constant portion of a multi-line f-string is safe_doc.

        The classifier detects multi-line JoinedStr (f-string) nodes —
        these are documentation / data strings, not executable code.
        Single-line f-strings would shadow real call classification at
        their argv lines, so the helper deliberately excludes them.
        """
        import _skillaudit_python_context as ctx

        # Genuine multi-line f-string spanning 4 lines.
        src = (
            'name = "x"\n'
            'DOC = f"""\n'
            'leadingtext_marker_here {name}\n'
            'trailing\n'
            '"""\n'
        )
        idx = _line_idx_of(src, "marker_here")
        verdict = ctx.classify("scripts/test.py", src, idx, "marker_here", "CMD_INJECTION")
        assert verdict == "safe_doc"


# ────────────────────────────────────────────────────────────────────────
# TestUnknown — classifier falls through when it cannot decide
# ────────────────────────────────────────────────────────────────────────


class TestUnknown:
    def test_syntax_invalid_source(self) -> None:
        """Syntax-invalid Python source returns unknown (iron-rule fall-through)."""
        import _skillaudit_python_context as ctx

        src = "def broken(:\n    subprocess.run([\n"
        verdict = ctx.classify("scripts/test.py", src, 1, "subprocess.run", "CMD_INJECTION")
        assert verdict == "unknown"

    def test_match_on_blank_top_level_line(self) -> None:
        """A match landing on a blank line outside any call returns unknown."""
        import _skillaudit_python_context as ctx

        # Line 1 (0-based) is blank — no enclosing call.
        src = "import os\n\nimport sys\n"
        verdict = ctx.classify("scripts/test.py", src, 1, "noop", "CMD_INJECTION")
        assert verdict == "unknown"

    def test_match_inside_non_shell_call(self) -> None:
        """A match inside a non-shell call (json.loads) returns unknown."""
        import _skillaudit_python_context as ctx

        src = 'import json\ndata = "{}"\nresult = json.loads(data)\n'
        idx = _line_idx_of(src, "json.loads")
        verdict = ctx.classify("scripts/test.py", src, idx, "json.loads", "CMD_INJECTION")
        assert verdict == "unknown"

    def test_match_on_bare_attribute_access(self) -> None:
        """A match on a bare attribute access (no enclosing Call) returns unknown."""
        import _skillaudit_python_context as ctx

        src = "import subprocess\npipe_value = subprocess.PIPE\n"
        idx = _line_idx_of(src, "subprocess.PIPE")
        verdict = ctx.classify("scripts/test.py", src, idx, "subprocess.PIPE", "CMD_INJECTION")
        assert verdict == "unknown"

    def test_empty_source(self) -> None:
        """Empty source (no lines, no AST nodes) returns unknown."""
        import _skillaudit_python_context as ctx

        verdict = ctx.classify("scripts/test.py", "", 0, "anything", "CMD_INJECTION")
        assert verdict == "unknown"


# ────────────────────────────────────────────────────────────────────────
# TestEvalExec — dynamic exec / eval / compile branch
# ────────────────────────────────────────────────────────────────────────


class TestEvalExec:
    def test_eval_with_literal_arg(self) -> None:
        """eval with a pure-literal first argument is safe_literal."""
        import _skillaudit_python_context as ctx

        src = 'result = eval("1+1")\n'
        idx = _line_idx_of(src, "1+1")
        verdict = ctx.classify("scripts/test.py", src, idx, "1+1", "DYNAMIC_EVAL")
        assert verdict == "safe_literal"

    def test_eval_with_variable_arg(self) -> None:
        """eval with a Name/variable first argument is suspect."""
        import _skillaudit_python_context as ctx

        src = 'user_input = "1+1"\nresult = eval(user_input)\n'
        idx = _line_idx_of(src, "eval(user_input)")
        verdict = ctx.classify("scripts/test.py", src, idx, "eval", "DYNAMIC_EVAL")
        assert verdict == "suspect"

    def test_exec_with_fstring_arg(self) -> None:
        """exec with an f-string first argument is suspect."""
        import _skillaudit_python_context as ctx

        src = 'x = 1\nexec(f"print({x})")\n'
        idx = _line_idx_of(src, "exec(")
        verdict = ctx.classify("scripts/test.py", src, idx, "exec", "DYNAMIC_EVAL")
        assert verdict == "suspect"


# ────────────────────────────────────────────────────────────────────────
# TestModuleSurface — exports + FQName membership
# ────────────────────────────────────────────────────────────────────────


class TestModuleSurface:
    def test_module_exports_classify_and_verdict_type(self) -> None:
        """The module exports the public ``classify`` function and ``ContextVerdict`` type alias."""
        import _skillaudit_python_context as ctx

        assert hasattr(ctx, "classify"), "classify() is the public entry point"
        assert callable(ctx.classify)
        assert hasattr(ctx, "ContextVerdict"), "ContextVerdict type alias must be exported"

    def test_shell_call_fqnames_contains_canonical_entries(self) -> None:
        """_SHELL_CALL_FQNAMES contains the canonical subprocess.run and os.system entries."""
        import _skillaudit_python_context as ctx

        assert "subprocess.run" in ctx._SHELL_CALL_FQNAMES
        assert "os.system" in ctx._SHELL_CALL_FQNAMES

    def test_dynamic_exec_fqnames_contains_eval_exec_compile(self) -> None:
        """_DYNAMIC_EXEC_FQNAMES contains eval, exec, and compile."""
        import _skillaudit_python_context as ctx

        assert "eval" in ctx._DYNAMIC_EXEC_FQNAMES
        assert "exec" in ctx._DYNAMIC_EXEC_FQNAMES
        assert "compile" in ctx._DYNAMIC_EXEC_FQNAMES
