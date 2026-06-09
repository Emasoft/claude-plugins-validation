#!/usr/bin/env python3
"""Security red-team regression tests for ``scripts/cpv_validation_common.py``.

Group **B-validation-common** of the 2026-06-09 red-team audit
(``reports/security-audit-redteam/``). Every test is **two-sided**: it asserts
that the malicious / evasion shape now FIRES (catches the threat) AND that the
benign case the discriminator legitimately suppresses STILL clears (no
regression / no new false positive).

Findings fixed here (governing contract: never-suppress, FN-safe — a fix only
ever makes the scanner catch MORE, never mutes a rule or relaxes a gate):

* **RT4-rot13-bypass** (CRITICAL) — ``OBFUSCATION_DECODER_PATTERNS`` only knew
  ``codecs.decode(...,'hex')``; a rot13 dropper (and charcode-reconstruction /
  marshal / pickle / ``bytes.fromhex`` decoders) decoded past RC-70 and shipped
  a plugin that passed the gate. The decoder list is expanded; RC-70 still
  requires a decoder AND an exec sink within proximity, so benign decode-only
  code stays clear.
* **RT5-skillaudit-sink-obfuscation-getattr-exec** (CRITICAL) — an exec sink
  reached via ``getattr(__builtins__,"ex"+"ec")`` / ``globals()[...]`` /
  ``__builtins__["exec"]`` / ``__import__("os").system`` carried no literal
  ``exec(`` / ``os.system`` token, so every textual sink list missed it.
  ``EXEC_SINK_PATTERNS`` gains the string-keyed-builtin / string-keyed-os shapes
  (no benign plugin use), scoped so a bare ``getattr(obj, "attr")`` stays clear.
* **RT5-skillaudit-sink-obfuscation-rc70-proximity-indirect** (HIGH) — RC-70's
  decode-then-exec backstop failed when the nearby sink was indirect. Because
  RC-70 reuses ``EXEC_SINK_PATTERNS``, the expanded sink set restores the
  backstop for the indirect case while keeping the AND-proximity gate.
* **G3-gate-banners-1** (HIGH) — Bucket A omitted ~11 execution-class RC rules
  (SSH backdoor, RCE deeplink, kernel-module, MCP cmd-injection, …) and several
  exfil rules, so real malware routed to the generic fixer instead of
  ``plugin-devitalizer``. They are now bucketed (additive; cannot mute). A
  drift-guard walks the phase pattern tables so a future execution rule cannot
  ship unbucketed.
* **G3-gate-banners-2** (MEDIUM) — the ``Private info leaked:`` CRITICAL carried
  no classifiable ID, so it could never route to Gate B. It now carries the
  stable ``[RC-135]`` prefix (keyed on a rule_id, never the free-text prose, so
  it is not attacker-reproducible) → Bucket B.
* **G3-gate-banners-3** (LOW) — drift-guard pinning the intentional, bounded
  intersection of Bucket A and ``UNCERTAIN_IN_DOCS_RULES`` to exactly
  {RC-76, RC-87, RC-93}, so a future unambiguous-execution rule added to the
  doc-demotion set (which would silence a real CRITICAL in a "sample" file) is
  caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports (mirrors the other tests/ files).
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    _SECURITY_GATE_BUCKETS,
    EXEC_SINK_PATTERNS,
    PHASE3_PATTERNS,
    PHASE4_PATTERNS,
    UNCERTAIN_IN_DOCS_RULES,
    ValidationReport,
    _classify_security_buckets,
    effective_severity,
    find_obfuscated_exec,
)


def _fires(content: str) -> bool:
    """True iff RC-70 (find_obfuscated_exec) reports a decode-near-exec finding."""
    return bool(find_obfuscated_exec(content, proximity_lines=3))


def _rc_report(rc_message: str, level: str = "critical") -> ValidationReport:
    """Build a report holding one in-process RC finding shaped ``RC-NN: <msg>``."""
    report = ValidationReport()
    getattr(report, level)(rc_message, "fixture.py", 1)
    return report


# =====================================================================
# RT4-rot13-bypass — decoder-list coverage (rot13 / charcode / marshal / …)
# =====================================================================


class TestRT4DecoderCoverage:
    """RC-70 must catch decode-then-exec for every common decoder, not just hex/base64."""

    @pytest.mark.parametrize(
        "name, payload",
        [
            (
                "rot13",
                'import codecs\nblob = "fbzr"\ncode = codecs.decode(blob, "rot13")\nexec(code)\n',
            ),
            (
                "codecs-zlib",
                'import codecs\nraw = codecs.decode(blob, "zlib")\nexec(raw)\n',
            ),
            (
                "charcode-genexpr",
                'code = "".join(chr(c) for c in [112, 114, 105, 110, 116])\nexec(code)\n',
            ),
            (
                "charcode-map",
                'code = "".join(map(chr, payload_ints))\nexec(code)\n',
            ),
            (
                "bytes-int-list-decode",
                'code = bytes([112, 114, 105, 110, 116]).decode()\nexec(code)\n',
            ),
            (
                "bytes-fromhex",
                'code = bytes.fromhex("7072696e74")\nexec(code)\n',
            ),
            (
                "binascii-unhexlify",
                'import binascii\ncode = binascii.unhexlify(blob)\nexec(code)\n',
            ),
            (
                "marshal-loads",
                'import marshal\nobj = marshal.loads(blob)\nexec(obj)\n',
            ),
            (
                "pickle-loads",
                'import pickle\nobj = pickle.loads(blob)\nexec(obj)\n',
            ),
        ],
    )
    def test_decoder_near_exec_fires(self, name: str, payload: str) -> None:
        """MALICIOUS: <decoder> within ±3 lines of an exec sink → RC-70 fires."""
        assert _fires(payload), f"{name} decode-then-exec must trip RC-70"

    @pytest.mark.parametrize(
        "name, payload",
        [
            # The exact discriminator each new decoder pattern exists to preserve:
            # a decoder with NO exec sink in proximity is legitimate data handling.
            (
                "rot13-no-sink",
                'import codecs\ngreeting = codecs.decode("uryyb", "rot13")\nprint(greeting)\n',
            ),
            (
                "base64-no-sink",
                "import base64\ndata = base64.b64decode(blob)\nopen('out.bin','wb').write(data)\n",
            ),
            (
                "charcode-no-sink",
                'label = "".join(chr(c) for c in [72, 105])\nprint(label)\n',
            ),
            (
                "marshal-no-sink",
                "import marshal\nconfig = marshal.loads(cached_bytes)\nreturn config\n",
            ),
            (
                "fromhex-no-sink",
                'mac = bytes.fromhex("aabbccddeeff")\nlog.debug(mac)\n',
            ),
        ],
    )
    def test_decoder_without_exec_stays_clear(self, name: str, payload: str) -> None:
        """BENIGN: a decoder with no exec sink nearby must NOT fire (FP suppression preserved)."""
        assert not _fires(payload), f"{name}: decode-only data handling must stay clear"

    def test_proximity_gate_still_bounds_distance(self) -> None:
        """BENIGN: decoder and exec sink >3 lines apart must NOT fire (proximity gate intact)."""
        far = (
            "import base64\n"
            "code = base64.b64decode(blob)\n"  # line 2
            "a = 1\n"
            "b = 2\n"
            "c = 3\n"
            "d = 4\n"
            "exec(code)\n"  # line 7 → 5 lines away
        )
        assert not _fires(far)


# =====================================================================
# RT5 — indirect exec-sink obfuscation (getattr/globals/__builtins__/__import__)
# =====================================================================


class TestRT5IndirectExecSink:
    """An exec sink reached by string-keyed builtin/os lookup must be recognised."""

    @pytest.mark.parametrize(
        "name, line",
        [
            ("getattr-builtins", 'getattr(__builtins__, "ex" + "ec")(code)'),
            ("getattr-builtins-plain", 'getattr(builtins, "exec")(code)'),
            ("getattr-os", 'getattr(os, "system")(cmd)'),
            ("globals-builtins", 'globals()["__builtins__"]["eval"](code)'),
            ("vars-builtins", 'vars()["__builtins__"]["exec"](code)'),
            ("builtins-subscript", '__builtins__["exec"](code)'),
            ("vars-of-builtins", 'vars(__builtins__)["eval"](code)'),
            ("import-os-system", '__import__("os").system(cmd)'),
            ("import-os-popen", "__import__('os').popen(cmd)"),
        ],
    )
    def test_indirect_sink_is_recognised(self, name: str, line: str) -> None:
        """MALICIOUS: the indirect-exec shape matches an EXEC_SINK pattern."""
        assert any(p.search(line) for p in EXEC_SINK_PATTERNS), f"{name} must be a recognised exec sink"

    @pytest.mark.parametrize(
        "name, line",
        [
            # The discriminator: a bare getattr/globals on a NON-builtins object is
            # ubiquitous and benign — it must NOT be treated as an exec sink.
            ("getattr-config", 'val = getattr(config, "timeout")'),
            ("getattr-self", 'handler = getattr(self, method_name)'),
            ("getattr-module-attr", 'fn = getattr(numpy, "array")'),
            ("globals-plain-key", 'thing = globals()["my_helper"]'),
            ("dict-index", 'value = settings["exec_path"]'),
            # getattr(os, <non-exec attr>) is benign data access — only the
            # SHELL-EXEC attribute names on os are a sink (see test below).
            ("getattr-os-environ", 'env = getattr(os, "environ")'),
            ("getattr-os-getcwd", 'cwd = getattr(os, "getcwd")()'),
        ],
    )
    def test_benign_getattr_is_not_a_sink(self, name: str, line: str) -> None:
        """BENIGN: ordinary getattr/globals/dict access on non-builtins must NOT match a sink."""
        assert not any(p.search(line) for p in EXEC_SINK_PATTERNS), f"{name} must not be an exec sink"

    def test_decode_near_indirect_sink_fires_rc70(self) -> None:
        """MALICIOUS (RT5-rc70-proximity-indirect): decode adjacent to getattr(__builtins__,exec) fires."""
        g8 = (
            "import base64\n"
            'blob = "AAAA"\n'
            "code = base64.b64decode(blob)\n"  # decoder line 3
            'getattr(__builtins__, "ex" + "ec")(code)\n'  # indirect sink line 4
        )
        assert _fires(g8), "decode adjacent to an INDIRECT exec sink must restore RC-70 backstop"

    def test_decode_near_globals_eval_fires_rc70(self) -> None:
        """MALICIOUS: decode adjacent to globals()[__builtins__][eval] fires RC-70."""
        g2 = (
            "import base64\n"
            'code = base64.b64decode("AAAA")\n'  # decoder line 2
            'globals()["__builtins__"]["ev" + "al"](code)\n'  # indirect sink line 3
        )
        assert _fires(g2)

    def test_baseline_literal_exec_still_fires(self) -> None:
        """CONTROL: the original literal base64-then-exec(...) baseline still fires (no regression)."""
        baseline = "import base64\ncode = base64.b64decode(blob)\nexec(code)\n"
        assert _fires(baseline)

    def test_decode_near_benign_getattr_stays_clear(self) -> None:
        """BENIGN: a decode adjacent to getattr(config, "attr") must NOT fire RC-70."""
        benign = (
            "import base64\n"
            "raw = base64.b64decode(token)\n"
            'timeout = getattr(config, "timeout")\n'
        )
        assert not _fires(benign)


# =====================================================================
# G3-gate-banners-1 — Bucket A coverage of execution / exfil RC rules
# =====================================================================


class TestG3BucketCoverage:
    """Execution & exfil RC rules route to the right gate; prompt-injection adds to C."""

    # Each rule maps to the EXACT bucket set the audit prescribed.
    EXEC_RULES_A = (
        "RC-40",  # SSH backdoor
        "RC-41",  # git-hook persistence
        "RC-42",  # docker-entrypoint mod
        "RC-48",  # MCP shell metacharacters
        "RC-69",  # AST eval/Function obfuscation
        "RC-79",  # workbench tampering
        "RC-80",  # embedded binary magic bytes
        "RC-81",  # hidden executable dotfile
        "RC-94",  # cursor:// RCE deeplink
        "RC-95",  # post-uninstall downloader
        "RC-98",  # firewall disable / kernel-module load
        "RC-22",  # clipboard exfil
        "RC-23",  # sendBeacon exfil
        "RC-25",  # image beacon
        "RC-53",  # MCP createMessage exfil
    )
    EXFIL_DUAL_AB = ("RC-32", "RC-58")  # also leak a secret → {A, B}
    INJECTION_C = ("RC-02", "RC-99", "RC-108")  # prompt-injection family → harden

    @pytest.mark.parametrize("rc", EXEC_RULES_A)
    def test_execution_rule_routes_to_bucket_a(self, rc: str) -> None:
        """MALICIOUS: an execution-class RC finding classifies into Bucket A (devitalize)."""
        present = _classify_security_buckets(_rc_report(f"{rc}: malicious execution threat (line 10)"))
        assert "A" in present, f"{rc} must route to Gate A so it reaches plugin-devitalizer"

    @pytest.mark.parametrize("rc", EXFIL_DUAL_AB)
    def test_secret_exfil_rule_is_dual_a_and_b(self, rc: str) -> None:
        """MALICIOUS: a secret-leaking exfil RC finding is dual-class {A, B}."""
        present = _classify_security_buckets(_rc_report(f"{rc}: exfiltrates repository secrets (line 10)"))
        assert present == {"A", "B"}, f"{rc} must fire BOTH Gate A (devitalize) and Gate B (redact)"

    @pytest.mark.parametrize("rc", INJECTION_C)
    def test_prompt_injection_rule_routes_to_bucket_c(self, rc: str) -> None:
        """MALICIOUS: a prompt-injection RC finding classifies into Bucket C (harden)."""
        present = _classify_security_buckets(_rc_report(f"{rc}: prompt injection payload (line 10)"))
        assert present == {"C"}, f"{rc} must route to Gate B harden (prompt-injection family)"

    def test_unmapped_structural_rule_stays_unclassified(self) -> None:
        """BENIGN: a non-security structural rule (RC-160 manifest drift) yields NO bucket.

        This is the discriminator the additive bucket map must preserve — only
        security findings trigger a gate; a structural INVALID does not over-fire
        a security banner.
        """
        present = _classify_security_buckets(_rc_report("RC-160: manifest drift detected (line 10)"))
        assert present == set(), "a structural rule must not trigger a security gate banner"

    def test_warning_level_finding_never_triggers_a_gate(self) -> None:
        """BENIGN: a Bucket-A rule emitted at WARNING is NOT classified (matches verdict gate).

        ``_classify_security_buckets`` walks CRITICAL/MAJOR/MINOR/NIT only — a
        WARNING-demoted finding does not fail the verdict, so it must not trigger
        a banner either. (This is also the seam G3-gate-banners-3 guards.)
        """
        report = ValidationReport()
        report.warning("RC-40: append to ~/.ssh/authorized_keys (permanent SSH backdoor)", "x.sh", 1)
        assert _classify_security_buckets(report) == set()

    def test_drift_guard_every_execution_exfil_rule_is_bucketed(self) -> None:
        """DRIFT GUARD: every CRITICAL/MAJOR phase rule matching an execution/exfil
        mechanism keyword MUST be present in ``_SECURITY_GATE_BUCKETS``.

        Walks PHASE3/PHASE4 pattern tables (the in-process RC scanner) so a FUTURE
        execution/exfil rule cannot ship without a bucket — which would silently
        route real malware to the generic fixer instead of plugin-devitalizer.
        Strictly additive: this can only DEMAND more banner coverage; it can never
        mute a signal. The keyword set is scoped to execution MECHANISMS and ACTIVE
        EXFIL sinks (NOT prose social-engineering / impersonation / authority
        prompts, which are a separate, deliberately-non-exhaustive class).
        """
        import re

        exec_exfil_kw = re.compile(
            r"authorized_keys|permanent ssh backdoor|\bbackdoor\b|kernel-module|kernel module|"
            r"insmod|modprobe|defender disable|/dev/tcp|reverse[ -]?shell|"
            r"command injection vector|shell metacharacter|shell substitution|deeplink that opens|"
            r"git-hook persistence|\.git/hooks|docker-entrypoint|dockerfile modification|"
            r"post-uninstall hook invokes|downloader/interpreter|binary magic bytes|"
            r"hidden dotfile with executable|workbench tampering|ast-level (?:eval|function) obfuscation|"
            r"clipboard-api exfil|clipboard read|navigator\.sendbeacon|silent exfil|image beacon|"
            r"createmessage exfiltration|sampling/createmessage|tojson\(secrets\)|secret value echoed|"
            r"cross-agent relay|downstream agent",
            re.IGNORECASE,
        )
        order = {"CRITICAL": 5, "MAJOR": 4, "MINOR": 3, "NIT": 2, "WARNING": 1, "INFO": 0}
        worst_sev: dict[str, str] = {}
        sample_msg: dict[str, str] = {}
        for table in (PHASE3_PATTERNS, PHASE4_PATTERNS):
            for rule_id, severity, _pattern, message in table:
                if rule_id not in worst_sev or order[severity] > order[worst_sev[rule_id]]:
                    worst_sev[rule_id] = severity
                if exec_exfil_kw.search(message):
                    sample_msg[rule_id] = message

        unbucketed = [
            rid
            for rid, msg in sample_msg.items()
            if worst_sev[rid] in ("CRITICAL", "MAJOR") and rid not in _SECURITY_GATE_BUCKETS
        ]
        assert not unbucketed, (
            "execution/exfil RC rules missing a security-gate bucket (would route real "
            f"malware to the generic fixer instead of plugin-devitalizer): {sorted(unbucketed)}"
        )
        # Sanity: the guard actually inspected the rules the audit named (non-empty).
        assert {"RC-40", "RC-48", "RC-98", "RC-22", "RC-32", "RC-58"} <= set(sample_msg)


# =====================================================================
# G3-gate-banners-2 — leaked-private-info is gate-classifiable to Bucket B
# =====================================================================


class TestG3PrivateInfoClassifiable:
    """The ``Private info leaked:`` CRITICAL routes to Gate B via the stable [RC-135] id."""

    def test_private_info_message_classifies_to_b(self) -> None:
        """MALICIOUS: a leaked-private-info finding (carrying [RC-135]) routes to Bucket B."""
        msg = (
            "[RC-135] Private info leaked: Known private username - found "
            "'/Users/realperson/secret' (replace with relative path or ${CLAUDE_PLUGIN_ROOT})"
        )
        assert _classify_security_buckets(_rc_report(msg)) == {"B"}

    def test_classification_is_keyed_on_rule_id_not_prose(self) -> None:
        """BENIGN/FN-SAFE: the SAME prose WITHOUT the [RC-135] id is NOT classified.

        The audit's hard requirement: do NOT add a substring match on the
        free-text "Private info leaked:" — a text-keyed trigger is
        attacker-reproducible. The gate must key on the stable rule_id only, so a
        message lacking the bracketed id yields no bucket.
        """
        prose_only = (
            "Private info leaked: Known private username - found '/Users/x/y' "
            "(replace with relative path)"
        )
        assert _classify_security_buckets(_rc_report(prose_only)) == set()

    def test_emitted_message_actually_carries_the_id(self) -> None:
        """The real scanner emits the [RC-135] prefix (end-to-end wiring, not just the test string).

        Drives ``scan_file_for_private_info`` on a file with a leaked private
        username and asserts the recorded finding both classifies to Bucket B and
        literally contains ``[RC-135]``.
        """
        from cpv_validation_common import scan_file_for_private_info

        tmp = Path(__file__).parent.parent / "reports" / "_secaudit_tmp_privinfo.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text('{"cache": "/Users/secretagent007/cache"}', encoding="utf-8")
        try:
            report = ValidationReport()
            count = scan_file_for_private_info(
                tmp, report, "config.json", additional_usernames={"secretagent007"}
            )
            assert count >= 1
            crit_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
            assert any("[RC-135]" in m for m in crit_msgs), "emitted finding must carry the [RC-135] id"
            assert _classify_security_buckets(report) == {"B"}
        finally:
            tmp.unlink(missing_ok=True)


# =====================================================================
# G3-gate-banners-3 — doc-demotion drift guard (bucket-A ∩ uncertain-in-docs)
# =====================================================================


class TestG3DocDemotionDriftGuard:
    """The Bucket-A rules that hard-demote to WARNING in docs are bounded to a safe set."""

    def test_bucket_a_intersect_uncertain_in_docs_is_exactly_the_safe_three(self) -> None:
        """DRIFT GUARD: only {RC-76, RC-87, RC-93} may be BOTH Bucket A AND doc-demoted.

        Hard-demoting a Bucket-A rule to WARNING in a doc/sample silences both the
        verdict and Gate A for it. That is acceptable ONLY for high-FP-in-prose
        signatures. If a future unambiguous-execution rule (e.g. RC-40 SSH
        backdoor, RC-48 MCP cmd-injection, RC-98 kernel-module) is added to
        ``UNCERTAIN_IN_DOCS_RULES``, this guard fails — preventing a real CRITICAL
        threat from vanishing merely because a file "looks like a sample".
        """
        bucket_a = {rid for rid, buckets in _SECURITY_GATE_BUCKETS.items() if "A" in buckets}
        intersection = bucket_a & UNCERTAIN_IN_DOCS_RULES
        assert intersection == {"RC-76", "RC-87", "RC-93"}, (
            "an execution-class Bucket-A rule was added to UNCERTAIN_IN_DOCS_RULES; a "
            "sample-file demotion would silence a real threat. Intersection now: "
            f"{sorted(intersection)}"
        )

    @pytest.mark.parametrize("rc", ["RC-40", "RC-48", "RC-98", "RC-94", "RC-79", "RC-69"])
    def test_unambiguous_execution_rules_keep_full_severity_in_docs(self, rc: str) -> None:
        """MALICIOUS: a real execution rule in a doc keeps a verdict-failing severity.

        These rules are NOT in ``UNCERTAIN_IN_DOCS_RULES``, so a doc context gives
        at most a ONE-tier demotion (CRITICAL→MAJOR), never the hard drop to
        WARNING that would clear the verdict. (An SSH backdoor in ``README.md`` is
        still a backdoor.)
        """
        demoted = effective_severity("critical", "README.md", rule_id=rc)
        assert demoted in ("critical", "major"), f"{rc} must not hard-demote to warning in docs"
        # And the demoted finding still routes to Gate A (devitalize).
        present = _classify_security_buckets(_rc_report(f"{rc}: execution threat (line 1)", level=demoted))
        assert "A" in present

    def test_uncertain_in_docs_rule_still_hard_demotes(self) -> None:
        """BENIGN/CONTROL: RC-93 (≥30 spaces, high doc-FP) DOES hard-demote to warning in docs.

        The legitimate FP suppression the demotion engine exists for must be
        preserved — this is the two-sided counterpart to the guard above.
        """
        assert effective_severity("critical", "README.md", rule_id="RC-93") == "warning"
        assert effective_severity("major", "docs/guide.md", rule_id="RC-76") == "warning"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
