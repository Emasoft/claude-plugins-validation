#!/usr/bin/env python3
"""Regression locks for the template-generator promotion in
`scripts/_skillaudit_python_context.py`.

When a SHELL_EXEC / CMD_INJECTION pattern matches inside a multi-line
string literal that lives inside a function whose body is dominated by
template-string generation (returns `str`, body's source range is
≥50% multi-line literals), the classifier promotes the verdict from
`safe_doc` (demoted to NIT under the execution-class rules) to
`safe_literal` (fully suppressed). The matched text is generated CODE
that will be written to disk and validated when the produced file is
scanned in its own right — flagging it on the template author file is
a double-count FP.

This file pins the behavior on synthetic fixtures so a future refactor
of the classifier cannot silently regress it (which would re-introduce
the 27 NITs on `scripts/generate_plugin_repo.py` that v2.102.x masked
via hash-anchored suppression — see TRDD-a4260cc6 / v2.101.0 design
notes for the original context-classifier intent).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _skillaudit_python_context as ctx  # noqa: E402

# --- helpers ----------------------------------------------------------------


def _classify(source: str, target_line_1based: int, rule_id: str = "SHELL_EXEC") -> str:
    """Run classify() on `source`, using the 1-based `target_line_1based`."""
    lines = source.splitlines()
    line_text = lines[target_line_1based - 1] if 0 < target_line_1based <= len(lines) else ""
    return ctx.classify(
        file_path="fixture.py",
        source=source,
        line_idx=target_line_1based - 1,
        match=line_text.strip(),
        rule_id=rule_id,
    )


# --- positive cases: template generators SHOULD be promoted to safe_literal --


class TestTemplateGeneratorPromotion:
    """When the enclosing function is a template generator, the matched
    SHELL_EXEC / CMD_INJECTION pattern inside the generated template is
    promoted to safe_literal (fully suppressed)."""

    def test_gen_publish_py_subprocess_run_inside_triple_quoted_returns_safe_literal(self) -> None:
        """`def gen_publish_py(p) -> str: return '''...subprocess.run(...)...'''` → safe_literal."""
        source = '''\
def gen_publish_py(p) -> str:
    return """#!/usr/bin/env python3
import subprocess
def publish():
    subprocess.run(["git", "push"], check=True)
    return 0
"""
'''
        # The `subprocess.run` line is line 5 (1-based).
        verdict = _classify(source, 5, "SHELL_EXEC")
        assert verdict == "safe_literal", (
            f"expected safe_literal for SHELL_EXEC inside template-generator "
            f"return value; got {verdict!r}"
        )

    def test_gen_ci_yml_returning_yaml_template_promotes_shell_exec(self) -> None:
        """Non-Python templates (YAML, TOML, JSON) generated via a
        `-> str` function also count as template-generator output."""
        source = '''\
def gen_ci_yml(p) -> str:
    return """name: CI
jobs:
  test:
    steps:
      - run: subprocess.run(["pytest", "-q"], check=True)
"""
'''
        verdict = _classify(source, 6, "SHELL_EXEC")
        assert verdict == "safe_literal"

    def test_high_literal_ratio_without_annotation_still_promotes(self) -> None:
        """A legacy generator with no return annotation but ≥85% literal
        body still qualifies (catches pre-typed generators in old code)."""
        source = '''\
def gen_legacy_publish(p):
    return """#!/usr/bin/env python3
import subprocess
import sys
import os
from pathlib import Path

def publish():
    subprocess.run(["git", "push"], check=True)
    subprocess.run(["gh", "release", "create"], check=True)
    return 0

if __name__ == "__main__":
    sys.exit(publish())
"""
'''
        # subprocess.run is at line 9; literal_ratio should be > 0.85
        verdict = _classify(source, 9, "SHELL_EXEC")
        assert verdict == "safe_literal"

    def test_cmd_injection_inside_template_generator_also_promotes(self) -> None:
        """CMD_INJECTION (the sibling execution-class rule) gets the
        same promotion treatment as SHELL_EXEC."""
        source = '''\
def gen_install_doc(p) -> str:
    return """## Install

Run this:

    curl -LsSf https://astral.sh/uv/install.sh | sh

That installs uv.
"""
'''
        # The `| sh` is on line 5
        verdict = _classify(source, 5, "CMD_INJECTION")
        assert verdict == "safe_literal"


# --- negative cases: non-generators MUST stay safe_doc / suspect / unknown ---


class TestTemplateGeneratorDoesNotOverSuppress:
    """The promotion is gated tightly — non-template functions that
    happen to contain a multi-line string literal must STILL fall
    through to safe_doc (or higher severity)."""

    def test_function_with_just_a_docstring_does_not_promote(self) -> None:
        """A normal function with a docstring + real code is NOT a
        template generator — its body is mostly code, not literal."""
        source = '''\
def normal_function(x: int) -> int:
    """This is a docstring.

    It spans multiple lines.
    The function returns 2x.
    """
    # Real work below — the docstring is the only literal here.
    a = x * 2
    b = a + 1
    c = b - 1
    d = c * 1
    e = d + 0
    f = e * 1
    g = f + 0
    h = g * 1
    return h
'''
        # No SHELL_EXEC match here, but if there were one outside the
        # docstring it would NOT be promoted — verify with a synthetic
        # match on line 8 (real code).
        # (The classifier returns 'unknown' because the line is real
        # code with no shell-reaching call — no promotion needed.)
        verdict = _classify(source, 8, "SHELL_EXEC")
        assert verdict != "safe_literal", (
            f"function with real code body must NOT be classified as a "
            f"template generator; got {verdict!r}"
        )

    def test_function_returning_str_but_with_real_code_does_not_promote(self) -> None:
        """A function with `-> str` return annotation but real
        executable body (not dominated by literals) does NOT qualify.
        Tests the 50% literal_ratio threshold."""
        source = '''\
def format_args(args: list[str]) -> str:
    """Format args."""
    result = ""
    for arg in args:
        if arg:
            result += arg + " "
        else:
            result += "(empty) "
        result = result.strip()
    return result
'''
        # subprocess.run not present, but the function's literal_ratio
        # would be ~10% (just docstring) — below 50% threshold.
        # Manual probe of the helper:
        import ast as _ast

        tree = _ast.parse(source)
        # Use line 5 (inside the for loop) — real code.
        assert not ctx._enclosing_function_is_template_generator(tree, 5)

    def test_shell_exec_in_normal_docstring_stays_safe_doc(self) -> None:
        """A `subprocess.run` mention inside a docstring of a non-
        template-generator function stays `safe_doc` (demoted to NIT
        for SHELL_EXEC) — preserves the original conservative behavior."""
        source = '''\
def process_data(items: list[str]) -> int:
    """Process the items.

    Example usage::

        subprocess.run(["my-tool", "--in", path], check=True)

    Returns the count.
    """
    count = 0
    for item in items:
        count += 1
    return count
'''
        verdict = _classify(source, 6, "SHELL_EXEC")
        # Function returns int, not str → not a template generator.
        # Should stay safe_doc.
        assert verdict == "safe_doc", (
            f"function with docstring example but not a template "
            f"generator must stay safe_doc; got {verdict!r}"
        )

    def test_real_subprocess_run_outside_string_is_unaffected(self) -> None:
        """The classifier path for REAL subprocess.run calls (not
        inside string literals) is unchanged — they continue to be
        classified by their argv shape (safe_literal for list-form,
        suspect for shell=True+f-string, etc.)."""
        source = '''\
import subprocess

def runner(cmd: list[str]) -> int:
    result = subprocess.run(cmd, check=True)
    return result.returncode
'''
        # Line 4: subprocess.run(cmd, check=True) — real code, safe_literal
        verdict = _classify(source, 4, "SHELL_EXEC")
        assert verdict == "safe_literal", (
            f"real subprocess.run with list-arg should be safe_literal; "
            f"got {verdict!r}"
        )

    def test_template_generator_only_promotes_execution_class_rules(self) -> None:
        """Other rule_ids (SQL_INJECTION, INSECURE_CRYPTO, etc.) are
        NOT promoted by the template-generator heuristic — only
        SHELL_EXEC and CMD_INJECTION qualify, since they are the two
        rules with the "execution-class → demote safe_doc" policy."""
        source = '''\
def gen_template(p) -> str:
    return """import hashlib
h = hashlib.md5(data).hexdigest()
"""
'''
        # Line 3 has the md5 inside the template. INSECURE_CRYPTO is
        # NOT in the {SHELL_EXEC, CMD_INJECTION} set → no promotion.
        verdict = _classify(source, 3, "INSECURE_CRYPTO")
        # safe_doc (or unknown) — but specifically NOT safe_literal
        # via the new template-generator path. (Other paths may still
        # return safe_literal, but only via their own logic — this
        # test pins that the template-generator promotion did not
        # extend silently to non-execution rules.)
        assert verdict in {"safe_doc", "unknown"}, (
            f"INSECURE_CRYPTO inside template should NOT be promoted "
            f"by template-generator path; got {verdict!r}"
        )


# --- the helper itself ------------------------------------------------------


class TestEnclosingFunctionIsTemplateGenerator:
    """Direct unit tests on the helper, independent of `classify()`."""

    def test_returns_false_when_line_is_outside_any_function(self) -> None:
        import ast as _ast

        source = "x = 1\ny = 2\nz = 3\n"
        tree = _ast.parse(source)
        assert not ctx._enclosing_function_is_template_generator(tree, 2)

    def test_returns_true_for_str_returning_function_with_dominant_literal(self) -> None:
        import ast as _ast

        source = '''\
def gen(p) -> str:
    return """line1
line2
line3
line4
line5
"""
'''
        tree = _ast.parse(source)
        # Any line inside the function (lines 1..7) should qualify
        # because the function returns str and the literal dominates.
        assert ctx._enclosing_function_is_template_generator(tree, 3)

    def test_returns_false_for_int_returning_function_with_docstring_only(self) -> None:
        import ast as _ast

        source = '''\
def compute(x: int) -> int:
    """A computation."""
    return x * 2
'''
        tree = _ast.parse(source)
        # 1 line of docstring out of 3 function lines = ratio ~0.33.
        # Annotation is `int`, not `str`. Should be False.
        assert not ctx._enclosing_function_is_template_generator(tree, 2)

    def test_deepest_nested_function_wins(self) -> None:
        """When `line` is inside a nested function, the helper picks
        the DEEPEST enclosing FunctionDef (smallest span), not the
        outermost one. Nested helpers inside a template generator
        are evaluated in their own right."""
        import ast as _ast

        source = '''\
def gen(p) -> str:
    def helper(x: int) -> int:
        return x * 2
    return """body
line2
"""
'''
        tree = _ast.parse(source)
        # Line 3 is inside `helper`, a (helper, int) function — should
        # NOT qualify as template generator.
        assert not ctx._enclosing_function_is_template_generator(tree, 3)
        # Line 5 is inside `gen` only (helper ends at line 3); template
        # generator IS the enclosing function.
        # (The literal is lines 4-6, that's 3 of 6 function lines = 50% —
        # plus annotation `str` → qualifies.)
        assert ctx._enclosing_function_is_template_generator(tree, 5)

    def test_pure_literal_ratio_threshold_without_annotation(self) -> None:
        """Functions WITHOUT return annotation qualify only when ≥85%
        of their body is multi-line string literal."""
        import ast as _ast

        # 8-line function, 5 lines of literal = 5/8 = 62.5% (below 85%).
        source_borderline = '''\
def legacy_gen(p):
    name = p.name
    desc = p.desc
    return """code
line2
line3
line4
line5
"""
'''
        tree = _ast.parse(source_borderline)
        # Without annotation, threshold is 85% — body is mostly code,
        # not literal — should NOT promote.
        # (literal lines: range(4, 10) = 6 lines / 9 total = 66% < 85%)
        # Actually 6 ≥ 85% would need ratio > 0.85; 6/9 = 0.67 < 0.85.
        assert not ctx._enclosing_function_is_template_generator(tree, 4)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
