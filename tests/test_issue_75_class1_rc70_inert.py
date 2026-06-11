#!/usr/bin/env python3
"""Two-sided regression tests for GitHub issue #75 — class 1.

`validate_security` RC-70 ("generic obfuscation with proximity-to-exec",
CRITICAL/`evasion`) fired on a security-scanner plugin's OWN Python test
fixtures, where the decoder/sink tokens live inside `str` constants that are
only ever passed as data to the detector under test — never executed.

The fix (issue #75 class 1) is a narrow, Python-AST, provably-inert carve-out
on the RC-70 emit site in `check_phase2e_extras`: for a `.py` file, suppress
RC-70 on a line whose RC-70 *decoder* token is a substring of a string literal
(no real decode `ast.Call` anchored on that line). A token inside a `str`
constant cannot execute, so the whole decode-then-exec chain is inert.

The carve-out is FN-safe and is NOT a `tests/`-skip:

* A REAL `base64.b64decode(blob)` near an `exec` — even one whose argument is a
  string constant — is an `ast.Call` and STILL fires, anywhere in the plugin
  including `tests/` (the RT-hole the reporter's "skip tests/" ask would open).
* The carve-out is Python-only. JS/TS `atob`+`eval` matches are left exactly as
  before (no JS parser; conservative — no regression).
* On a parse failure the helper returns the empty set (default-visible), so a
  deliberately-broken `.py` file cannot be used to dodge RC-70.

Every case below was reproduced on the shipped validator and encodes BOTH
sides of the contract: the benign shape stops firing while the genuinely-live
sibling keeps firing.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_validation_common as cvc  # noqa: E402
import validate_security as vs  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the skillaudit result cache so a same-version classifier change is
    never masked by a cached verdict (the cache is keyed on content/catalog/
    version/ext, NOT on this carve-out's code)."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _count_rc70(report: cvc.ValidationReport) -> int:
    """Number of findings whose message mentions RC-70."""
    return sum(1 for r in report.results if "RC-70" in (r.message or ""))


def _mkplugin(root: Path, files: dict[str, str]) -> Path:
    """Materialise a minimal plugin tree with a `.claude-plugin/plugin.json`
    so the phase-level `_iter_scannable_files` walk picks it up."""
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    meta = root / ".claude-plugin"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "plugin.json").write_text('{"name":"p","version":"1.0.0","description":"t"}')
    return root


def _phase2e_rc70(files: dict[str, str]) -> int:
    """Run `check_phase2e_extras` over a fresh plugin tree and return the RC-70
    finding count (the real emit path, incl. the test-path demotion)."""
    with tempfile.TemporaryDirectory() as d:
        plugin = _mkplugin(Path(d), files)
        report = cvc.ValidationReport()
        vs.check_phase2e_extras(plugin, report)
        return _count_rc70(report)


# ---------------------------------------------------------------------------
# MUST-SUPPRESS — benign string-literal decoders in a tests/ .py file
# ---------------------------------------------------------------------------


class TestRc70InertSuppressed:
    def test_single_line_string_literal_not_flagged(self) -> None:
        """Decoder+sink inside ONE python string literal (dict value) is inert."""
        body = (
            "def _hits(text):\n"
            "    return text.count('base64')\n"
            "\n"
            "def test_fixture():\n"
            '    payload = {"content": "import base64\\nresult = eval(base64.b64decode(blob))\\n"}\n'
            '    assert _hits(payload["content"]) == 1\n'
        )
        assert _phase2e_rc70({"tests/test_detector_fixtures.py": body}) == 0

    def test_adjacent_string_concat_not_flagged(self) -> None:
        """Decoder fragment + `exec(...)` fragment as adjacent string lines is inert."""
        body = (
            "def _hits(text):\n"
            "    return text.count('base64')\n"
            "\n"
            "def test_fixture():\n"
            "    sample = (\n"
            "        \"decoded = base64.b64decode('QQ==')\\n\"\n"
            '        "exec(decoded)\\n"\n'
            "    )\n"
            "    assert _hits(sample) == 1\n"
        )
        assert _phase2e_rc70({"tests/test_detector_fixtures.py": body}) == 0

    def test_token_inside_comment_not_flagged(self) -> None:
        """A decoder+sink token inside a `#` comment is not an ast.Call — inert."""
        body = (
            "def run():\n"
            "    # example: eval(base64.b64decode(x))\n"
            "    return 1\n"
        )
        assert _phase2e_rc70({"tests/test_comment_fixture.py": body}) == 0


# ---------------------------------------------------------------------------
# MUST-FIRE — the RT-hole stays closed (real threats keep blocking)
# ---------------------------------------------------------------------------


class TestRc70RealThreatStillFires:
    def test_live_pipeline_in_tests_dir_still_fires(self) -> None:
        """A real `b64decode(blob)` -> `exec(decoded)` on code lines in tests/ fires."""
        body = (
            'blob = "cHJpbnQoMSk="\n'
            "def run():\n"
            "    decoded = base64.b64decode(blob)\n"
            "    exec(decoded)\n"
        )
        assert _phase2e_rc70({"tests/test_actually_malicious.py": body}) >= 1

    def test_same_line_real_call_string_arg_still_fires(self) -> None:
        """`exec(base64.b64decode("..."))` is a REAL decode call — still fires."""
        body = "def run():\n    exec(base64.b64decode(\"cHJpbnQoMSk=\"))\n"
        assert _phase2e_rc70({"tests/test_inline.py": body}) >= 1


# ---------------------------------------------------------------------------
# Carve-out scope guards — Python-only, default-SAFE
# ---------------------------------------------------------------------------


class TestRc70CarveoutScope:
    def test_javascript_unchanged_still_fires(self) -> None:
        """A `.js` file with `eval(atob("..."))` is NOT covered by the .py carve-out."""
        body = 'const code = eval(atob("cHJpbnQoMSk="));\n'
        assert _phase2e_rc70({"hook.js": body}) >= 1

    def test_python_parse_failure_keeps_firing(self) -> None:
        """A `.py` file with a real decode->exec PLUS a syntax error keeps RC-70."""
        body = (
            'blob = "cHJpbnQoMSk="\n'
            "def run():\n"
            "    decoded = base64.b64decode(blob)\n"
            "    exec(decoded)\n"
            "def broken(:\n"  # deliberate syntax error -> ast.parse raises
        )
        assert _phase2e_rc70({"tests/test_broken.py": body}) >= 1


# ---------------------------------------------------------------------------
# Direct unit tests of the discriminator helper
# ---------------------------------------------------------------------------


class TestRc70InertHelper:
    def test_non_python_returns_empty(self) -> None:
        """Non-.py path => empty set (suppress nothing)."""
        assert (
            vs._rc70_python_inert_decoder_lines("hook.js", 'eval(atob("x"));\n')
            == frozenset()
        )

    def test_parse_failure_returns_empty(self) -> None:
        """A syntax error => empty set (default-visible)."""
        assert (
            vs._rc70_python_inert_decoder_lines("tests/x.py", "def f(:\n    pass\n")
            == frozenset()
        )

    def test_string_literal_decoder_line_is_inert(self) -> None:
        """A decoder token only inside a string literal => that line is inert."""
        content = 's = "result = eval(base64.b64decode(blob))"\n'
        inert = vs._rc70_python_inert_decoder_lines("tests/x.py", content)
        assert 1 in inert

    def test_real_decoder_line_is_not_inert(self) -> None:
        """A real `base64.b64decode(...)` ast.Call => that line is NOT inert."""
        content = "x = base64.b64decode(blob)\n"
        inert = vs._rc70_python_inert_decoder_lines("tests/x.py", content)
        assert 1 not in inert

    def test_real_decoder_with_string_arg_is_not_inert(self) -> None:
        """A real decode call whose arg is a string constant is still a live call."""
        content = "x = base64.b64decode('QQ==')\n"
        inert = vs._rc70_python_inert_decoder_lines("tests/x.py", content)
        assert 1 not in inert
