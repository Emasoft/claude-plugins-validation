#!/usr/bin/env python3
"""Two-sided regression lock for issue #161 — the ordinary English word
``function`` followed by a parenthesised markdown code span must NOT fire
``SHELL_EXEC`` as though it were the JavaScript ``Function()`` constructor.

Root cause: every catalog pattern is compiled with ``re.IGNORECASE``
(``cpv_skillaudit_native._compiled_rules``) — there is no per-pattern flag field
in ``scripts/rules/skillaudit_patterns.json``. The SHELL_EXEC pattern
``(?:\\bnew\\s+)?\\bFunction\\s*\\(\\s*[`'"]`` therefore matched lowercase prose
whenever a markdown backtick (or a quote) happened to open the parenthetical:

    the helper function (`run_all`) is defined below

``Function`` matched the English word, ``\\s*\\(\\s*`` matched " (", and the
character class matched the backtick. In a doc the finding demotes to NIT — and a
NIT blocks ``--strict``, so this hard-blocked the reporter's publish.

Note the reporter's stated root cause was WRONG: they believed the pattern was a
bare ``Function\\s*\\(``. It already required a quote/backtick — the trigger is the
markdown backtick, which their simplified repro ("The real function (see below)")
does not contain, so that repro does not fire.

Fix: the identifier is pinned CASE-SENSITIVE with a scoped inline flag —
``(?-i:Function)`` — which is re2-compatible (verified). The JS constructor is
capitalised by definition, so no real threat is lost.

Both sides run the REAL scanner — never a reimplementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DOC = "skills/helper/SKILL.md"  # a markdown surface, where the FP was reported
JS = "scripts/helper.js"  # a real executable surface


def _shell_exec(path: str, doc: str) -> int:
    """Count non-suppressed SHELL_EXEC findings the REAL scanner reports."""
    from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]

    return sum(
        1
        for f in scan_content(doc, path)
        if f.get("suppressed") is not True and f.get("ruleId") == "SHELL_EXEC"
    )


# ────────────────────────────────────────────────────────────────────────
# FP side — lowercase English prose must not fire.
# ────────────────────────────────────────────────────────────────────────


class TestFunctionProseDoesNotFire:
    def test_backtick_code_span_parenthetical(self) -> None:
        """The exact #161 shape: prose "function (`ident`)" in a markdown doc."""
        doc = "Call the helper function (`run_all`) before the gate.\n"
        assert _shell_exec(DOC, doc) == 0

    def test_double_quoted_parenthetical(self) -> None:
        """Prose where the parenthetical opens with a double quote."""
        doc = 'The entry function ("main") is resolved at import time.\n'
        assert _shell_exec(DOC, doc) == 0

    def test_single_quoted_parenthetical(self) -> None:
        """Prose where the parenthetical opens with a single quote."""
        doc = "Every function ('including the wrappers') is scanned.\n"
        assert _shell_exec(DOC, doc) == 0

    def test_prose_in_a_js_file_comment(self) -> None:
        """The same prose inside a JS comment is prose, not a constructor call."""
        doc = "// the exported function (`handler`) is bound at startup\n"
        assert _shell_exec(JS, doc) == 0


# ────────────────────────────────────────────────────────────────────────
# FN side — the real JavaScript Function() constructor still fires.
# ────────────────────────────────────────────────────────────────────────


class TestRealFunctionConstructorStillFires:
    def test_new_function_double_quote(self) -> None:
        doc = 'const f = new Function("return process.env");\n'
        assert _shell_exec(JS, doc) >= 1

    def test_bare_function_double_quote(self) -> None:
        doc = 'const f = Function("return this")();\n'
        assert _shell_exec(JS, doc) >= 1

    def test_function_template_literal(self) -> None:
        doc = "const f = Function(`return ${payload}`);\n"
        assert _shell_exec(JS, doc) >= 1

    def test_new_function_single_quote(self) -> None:
        doc = "const f = new Function('return globalThis');\n"
        assert _shell_exec(JS, doc) >= 1

    def test_new_function_with_space_before_paren(self) -> None:
        """The `\\s*` between the identifier and `(` is preserved."""
        doc = 'const f = new Function ("return 1");\n'
        assert _shell_exec(JS, doc) >= 1

    def test_real_constructor_inside_a_js_fence_in_a_doc(self) -> None:
        """A doc that ships an executable JS fence still fires — the fix is
        case-sensitivity, not a doc carve-out."""
        doc = '```js\nconst f = new Function("return process.env.TOKEN");\n```\n'
        assert _shell_exec(DOC, doc) >= 1


# ────────────────────────────────────────────────────────────────────────
# Fail-safe side — the CAPITALISED form stays firing even in prose.
#
# Pinning the identifier case-sensitively clears the lowercase English word,
# which is the whole FP. A capital-F ``Function("…")`` is byte-identical to the
# JS constructor call, so it is genuinely ambiguous — and an ambiguous shape must
# keep firing (never suppress; a doc that *documents* the constructor should show
# it inside a fence, or reword). This is asserted, not tolerated: it locks the
# boundary so a future "fix" cannot widen the clear into an FN.
# ────────────────────────────────────────────────────────────────────────


class TestCapitalisedFormStaysFailSafe:
    def test_sentence_initial_capitalised_function_still_fires(self) -> None:
        doc = 'Function ("dispatch") names are resolved at import time.\n'
        assert _shell_exec(DOC, doc) >= 1

    def test_capitalised_backtick_form_still_fires(self) -> None:
        doc = "Function (`dispatch`) is the constructor being described.\n"
        assert _shell_exec(DOC, doc) >= 1
