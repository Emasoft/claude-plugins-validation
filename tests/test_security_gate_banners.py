#!/usr/bin/env python3
"""Tests for the two security-gate warning banners + bucket classification.

Spec: ``reports/leaks-preventer-design/20260608_041101+0200-spec.md`` §0–§3,
§8.1/§8.2/§8.3-#14.

The two banners are PURELY ADDITIVE informational text rendered on an
already-failing security verdict:

  * **Gate A** — execution / malicious-threat code the plugin SHIPS →
    recommends the EXISTING ``plugin-devitalizer`` agent.
  * **Gate B** — leaked secrets (Bucket B) and/or missing safeguards
    (Bucket C) → recommends the NEW ``plugin-leaks-preventer`` agent.

The contract these tests pin (do NOT let it regress):

  1. The banners NEVER mutate report counts, NEVER call ``report.add_*``,
     NEVER change ``exit_code`` / ``exit_code_strict``. They are text on a
     verdict that already failed.
  2. They are non-suppressable by construction — the ONLY trigger is "which
     bucket of findings is present"; no env var / flag / allow-list mutes the
     SIGNAL.
  3. Under ``--json`` stdout stays PURE JSON (the #70-A contract); the banner
     SIGNAL is exposed as the additive ``security_gates`` object instead.
  4. Every other (non-security) validator is unaffected — it keeps the
     existing generic fixer-recommendation block.

All tests are two-sided: each asserts the positive AND the negative.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    _SECURITY_GATE_BUCKETS,
    EXIT_CRITICAL,
    EXIT_OK,
    ValidationReport,
    _classify_security_buckets,
    _print_security_gate_banners,
    print_compact_summary,
)

# ── Exact banner string anchors (must match the spec §2.1 / §2.2 verbatim) ──
GATE_A_TITLE = "SECURITY GATE A — EXECUTABLE THREAT CODE MUST BE DEVITALIZED"
GATE_B_TITLE = "SECURITY GATE B — LEAKS & MISSING SAFEGUARDS MUST BE FIXED"
# NOTE: the Gate B *title* itself contains the words "MISSING SAFEGUARDS", so a
# bare "MISSING SAFEGUARDS" substring search is NOT a reliable sub-section
# probe — use the full sub-section HEADER lines below instead.
LEAKS_SUBSECTION = "LEAKED SECRETS / SENSITIVE DATA:"
SAFEGUARDS_SUBSECTION = "MISSING SAFEGUARDS / EXPOSED VULNERABILITIES:"
DEVITALIZER_AGENT = "plugin-devitalizer"
LEAKS_PREVENTER_AGENT = "plugin-leaks-preventer"

# Markdown section headers (report-file body).
GATE_A_MD_HEADER = "## Security Gate A"
GATE_B_MD_HEADER = "## Security Gate B"


def _capture(fn) -> str:
    """Run ``fn`` with stdout redirected to a StringIO; return captured text.

    Because stdout is a StringIO (non-TTY), the banner helper strips ANSI
    exactly as it would when piped/redirected — so the captured text is plain.
    """
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        fn()
    finally:
        sys.stdout = old
    return buf.getvalue()


def _skillaudit_finding(report: ValidationReport, level: str, category: str, rule_id: str, msg: str = "x") -> None:
    """Append a finding shaped exactly like the native-skillaudit adapter emits.

    ``cpv_skillaudit_native.report_findings`` formats messages as
    ``[skillaudit:<category> <ID>] <message>`` — the classifier extracts ``<ID>``
    from that bracket. Mirroring the real shape keeps these unit tests faithful
    to the production message format without needing the scanner to fire.
    """
    getattr(report, level)(f"[skillaudit:{category} {rule_id}] {msg}", "fixture.py", 3)


def _rc_finding(report: ValidationReport, level: str, rc: str, msg: str = "something") -> None:
    """Append a finding shaped like an in-process RC rule: ``RC-NN: <message>``."""
    getattr(report, level)(f"{rc}: {msg} (line 10)", "fixture.py", 10)


# =====================================================================
# §8.1 — Banner-firing tests (the warning fires for the right bucket)
# =====================================================================


class TestGateFiring:
    """Each gate fires for its bucket, in both terminal and report-body surfaces."""

    def test_gate_a_fires_on_execution_finding_terminal_and_body(self) -> None:
        """A Bucket-A finding renders Gate A (terminal + markdown body); Gate B absent."""
        report = ValidationReport()
        _skillaudit_finding(report, "critical", "code_execution", "CMD_INJECTION")

        # Bucket classification is exactly {A}.
        assert _classify_security_buckets(report) == {"A"}

        # Terminal surface (boxed ASCII via print_compact_summary).
        terminal = _capture(
            lambda: print_compact_summary(
                report, "Security Validation", Path("/tmp/report.md"), security_gates=True
            )
        )
        assert GATE_A_TITLE in terminal
        assert DEVITALIZER_AGENT in terminal
        assert GATE_B_TITLE not in terminal, "Gate B must not fire with only a Bucket-A finding"

        # Report-file body surface (markdown).
        body = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/report.md"), markdown=True))
        assert GATE_A_MD_HEADER in body
        assert DEVITALIZER_AGENT in body
        assert GATE_B_MD_HEADER not in body

    def test_gate_a_fires_on_rc_token(self) -> None:
        """An in-process Bucket-A RC token (RC-46) triggers Gate A and only Gate A."""
        report = ValidationReport()
        _rc_finding(report, "major", "RC-46", "detector signature meta")
        assert _classify_security_buckets(report) == {"A"}
        terminal = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/r.md")))
        assert GATE_A_TITLE in terminal
        assert GATE_B_TITLE not in terminal

    def test_gate_b_fires_on_secret_leak_only_leaks_subsection(self) -> None:
        """A Bucket-B secret finding renders Gate B with the LEAKS sub-section only."""
        report = ValidationReport()
        _skillaudit_finding(report, "critical", "credential_theft", "SECRET_GITHUB_TOKEN")
        assert _classify_security_buckets(report) == {"B"}

        terminal = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/r.md")))
        assert GATE_B_TITLE in terminal
        assert LEAKS_PREVENTER_AGENT in terminal
        assert LEAKS_SUBSECTION in terminal
        assert SAFEGUARDS_SUBSECTION not in terminal, "C sub-section must be absent with no Bucket-C finding"
        assert GATE_A_TITLE not in terminal

    def test_gate_b_fires_on_hardcoded_secret(self) -> None:
        """HARDCODED_SECRET (no native catalog entry, emitted by the secret detector) → Bucket B."""
        report = ValidationReport()
        _skillaudit_finding(report, "critical", "credential_theft", "HARDCODED_SECRET")
        assert _classify_security_buckets(report) == {"B"}
        terminal = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/r.md")))
        assert GATE_B_TITLE in terminal
        assert LEAKS_SUBSECTION in terminal
        assert SAFEGUARDS_SUBSECTION not in terminal

    def test_gate_b_fires_on_missing_safeguard_only_safeguards_subsection(self) -> None:
        """A Bucket-C finding (INSECURE_TLS) renders Gate B with the SAFEGUARDS sub-section only."""
        report = ValidationReport()
        _skillaudit_finding(report, "minor", "network", "INSECURE_TLS")
        assert _classify_security_buckets(report) == {"C"}

        terminal = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/r.md")))
        assert GATE_B_TITLE in terminal
        assert SAFEGUARDS_SUBSECTION in terminal
        assert LEAKS_SUBSECTION not in terminal, "B sub-section must be absent with no Bucket-B finding"
        assert GATE_A_TITLE not in terminal

    def test_gate_c_fires_on_rc61_sandbox_bypass(self) -> None:
        """RC-61 (dangerouslyDisableSandbox) is Bucket C → Gate B SAFEGUARDS sub-section."""
        report = ValidationReport()
        _rc_finding(report, "major", "RC-61", "dangerouslyDisableSandbox")
        assert _classify_security_buckets(report) == {"C"}
        body = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/r.md"), markdown=True))
        assert GATE_B_MD_HEADER in body
        assert GATE_A_MD_HEADER not in body

    def test_both_banners_fire_gate_a_before_gate_b(self) -> None:
        """One Bucket-A + one Bucket-B finding → both banners, Gate A printed BEFORE Gate B."""
        report = ValidationReport()
        _skillaudit_finding(report, "critical", "code_execution", "CMD_INJECTION")
        _skillaudit_finding(report, "critical", "credential_theft", "HARDCODED_SECRET")
        assert _classify_security_buckets(report) == {"A", "B"}

        terminal = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/r.md")))
        idx_a = terminal.index(GATE_A_TITLE)
        idx_b = terminal.index(GATE_B_TITLE)
        assert idx_a < idx_b, "Gate A (higher-severity class) must print before Gate B"
        # Gate B with B-only present → LEAKS sub-section, no SAFEGUARDS.
        assert LEAKS_SUBSECTION in terminal
        assert SAFEGUARDS_SUBSECTION not in terminal

        # Markdown body preserves the same ordering.
        body = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/r.md"), markdown=True))
        assert body.index(GATE_A_MD_HEADER) < body.index(GATE_B_MD_HEADER)

    def test_dual_class_deserialization_triggers_a_and_c(self) -> None:
        """DESERIALIZATION is dual-class {A,C}: Gate A fires AND Gate B's SAFEGUARDS sub-section."""
        report = ValidationReport()
        _skillaudit_finding(report, "major", "code_execution", "DESERIALIZATION")
        assert _classify_security_buckets(report) == {"A", "C"}

        terminal = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/r.md")))
        assert GATE_A_TITLE in terminal
        assert GATE_B_TITLE in terminal
        assert SAFEGUARDS_SUBSECTION in terminal
        # No Bucket-B finding present → the LEAKS sub-section stays hidden.
        assert LEAKS_SUBSECTION not in terminal

    def test_markdown_body_has_no_ansi(self) -> None:
        """The markdown report-body banners never contain ANSI escape codes."""
        report = ValidationReport()
        _skillaudit_finding(report, "critical", "code_execution", "CMD_INJECTION")
        _skillaudit_finding(report, "minor", "network", "INSECURE_TLS")
        body = _capture(lambda: _print_security_gate_banners(report, Path("/tmp/r.md"), markdown=True))
        assert "\033[" not in body
        assert GATE_A_MD_HEADER in body
        assert GATE_B_MD_HEADER in body
        assert DEVITALIZER_AGENT in body
        assert LEAKS_PREVENTER_AGENT in body


# =====================================================================
# §8.2 — Negative / additive-invariant tests (does NOT fire when it
#        shouldn't; exit code unchanged)
# =====================================================================


class TestNegativeAndAdditiveInvariant:
    """The banners stay silent when they should, and never touch the exit code."""

    def test_clean_scan_no_banner(self) -> None:
        """A clean report (only PASSED) → neither banner, and the report stays clean."""
        report = ValidationReport()
        report.passed("all good")
        assert report.exit_code == EXIT_OK
        assert _classify_security_buckets(report) == set()
        terminal = _capture(
            lambda: print_compact_summary(report, "Security Validation", Path("/tmp/r.md"), security_gates=True)
        )
        assert GATE_A_TITLE not in terminal
        assert GATE_B_TITLE not in terminal
        # And the generic fixer block also no-ops on a clean run.
        assert "TO FIX THESE ISSUES AUTOMATICALLY" not in terminal

    def test_warning_only_scan_no_banner(self) -> None:
        """A WARNING-only finding (even one whose ID is in a bucket) does NOT trigger a gate."""
        report = ValidationReport()
        # NET_SUSPICIOUS is Bucket C, but a WARNING never triggers (matches the
        # fixable_total gate semantics — WARNING/INFO are excluded).
        report.warning("[skillaudit:network NET_SUSPICIOUS] suspicious endpoint", "x.py", 1)
        assert _classify_security_buckets(report) == set()
        terminal = _capture(
            lambda: print_compact_summary(report, "Security Validation", Path("/tmp/r.md"), security_gates=True)
        )
        assert GATE_A_TITLE not in terminal
        assert GATE_B_TITLE not in terminal

    def test_structural_only_invalid_no_security_banner_but_fixer_prints(self) -> None:
        """A non-security validator (security_gates=False) with a structural CRITICAL:

        - shows NEITHER security banner (regression guard for every non-security
          validator), AND
        - STILL shows the existing generic fixer-recommendation block.
        """
        report = ValidationReport()
        # A manifest-shaped CRITICAL with no security ID → empty bucket set.
        report.critical("Manifest plugin.json missing required field 'name'", "plugin.json", 1)
        assert _classify_security_buckets(report) == set()

        # security_gates=False is how EVERY non-security validator calls it.
        terminal = _capture(
            lambda: print_compact_summary(report, "Plugin Validation", Path("/tmp/r.md"), security_gates=False)
        )
        assert GATE_A_TITLE not in terminal
        assert GATE_B_TITLE not in terminal
        # The existing fixer block MUST still appear for non-security validators.
        assert "TO FIX THESE ISSUES AUTOMATICALLY" in terminal

    def test_security_invalid_with_no_classifiable_finding_falls_back_to_fixer(self) -> None:
        """A security run that is INVALID on a non-classifiable finding (empty bucket set)
        renders no security banner but DOES fall back to the generic fixer block."""
        report = ValidationReport()
        # RC-160 (structural self-scan hash drift) is NOT in the bucket map.
        report.critical("RC-160: integrity manifest mismatch", "<self>", 0)
        assert _classify_security_buckets(report) == set()
        terminal = _capture(
            lambda: print_compact_summary(report, "Security Validation", Path("/tmp/r.md"), security_gates=True)
        )
        assert GATE_A_TITLE not in terminal
        assert GATE_B_TITLE not in terminal
        # security_gates=True but empty bucket set → fixer fallback still prints.
        assert "TO FIX THESE ISSUES AUTOMATICALLY" in terminal

    def test_exit_code_unchanged_by_banner_real_scan(self, tmp_path, monkeypatch) -> None:
        """The banner adds zero findings: validate_security.main()'s exit code equals what
        the counts alone yield (compared against a control where the banner is a no-op)."""
        from validate_security import main

        # PLUGIN_SKIP_GITHUB_INTEGRITY=1 — editing CPV's own scripts drifts the
        # integrity manifest, which would otherwise abort the scan before the
        # banner code runs (unrelated to this contract).
        monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")

        plugin_dir = tmp_path / "secret-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        # A fabricated-but-pattern-matching GitHub token → a Bucket-B CRITICAL.
        (plugin_dir / "config.py").write_text('TOKEN = "ghp_' + "A" * 40 + '"\n')

        # Control run with the banner monkeypatched to a no-op — the exit code
        # must be identical, proving the banner changed nothing.
        import cpv_validation_common as cvc

        monkeypatch.setattr(cvc, "_print_security_gate_banners", lambda *a, **k: None)
        monkeypatch.setattr("sys.argv", ["validate_security", str(plugin_dir), "--strict", "--json"])
        control_out = _capture(main)
        control_code = json.loads(control_out)["exit_code"]

        # Restore the real banner and run again — same plugin, same exit code.
        monkeypatch.undo()
        monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
        monkeypatch.setattr("sys.argv", ["validate_security", str(plugin_dir), "--strict", "--json"])
        real_out = _capture(main)
        real_data = json.loads(real_out)

        # The CRITICAL secret means INVALID, but the banner mutated nothing.
        assert real_data["counts"]["CRITICAL"] >= 1
        assert real_data["exit_code"] == control_code == EXIT_CRITICAL
        # And the banner SIGNAL is exposed (Bucket B).
        assert real_data["security_gates"]["B"] is True
        assert real_data["security_gates"]["leaks_preventer_recommended"] is True

    def test_json_purity_and_additive_security_gates_object(self, tmp_path, monkeypatch) -> None:
        """Under --json: stdout is pure parseable JSON (no banner ASCII — #70-A contract),
        and the additive security_gates object is present and correct for a Bucket-A finding."""
        from validate_security import main

        monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
        plugin_dir = tmp_path / "exec-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        # A command-injection shape → a Bucket-A finding on a real scan.
        (plugin_dir / "run.py").write_text("import subprocess\nsubprocess.run('echo ' + user, shell=True)\n")

        monkeypatch.setattr("sys.argv", ["validate_security", str(plugin_dir), "--strict", "--json"])
        out = _capture(main)

        # stdout must be a single pure JSON object — no Gate banner ASCII.
        assert GATE_A_TITLE not in out
        assert GATE_B_TITLE not in out
        data = json.loads(out)  # raises if not pure JSON

        gates = data["security_gates"]
        assert set(gates.keys()) == {"A", "B", "C", "devitalize_recommended", "leaks_preventer_recommended"}
        assert gates["A"] is True
        assert gates["devitalize_recommended"] is True
        assert all(isinstance(v, bool) for v in gates.values())

    def test_report_file_body_contains_markdown_banner(self, tmp_path, monkeypatch) -> None:
        """The non-json run writes the markdown banner into the report-file body, so an
        agent reading only the file still sees it."""
        from validate_security import main

        monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
        plugin_dir = tmp_path / "secret-file-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "config.py").write_text('GH = "ghp_' + "B" * 40 + '"\n')

        report_path = tmp_path / "sec-report.md"
        monkeypatch.setattr(
            "sys.argv",
            ["validate_security", str(plugin_dir), "--strict", "--report", str(report_path)],
        )
        main()
        body = report_path.read_text()
        # Gate B markdown section present in the FILE body (Bucket B leak), so
        # an agent reading ONLY the report file still sees the banner.
        assert GATE_B_MD_HEADER in body
        assert LEAKS_PREVENTER_AGENT in body
        # The banner's own dispatch instruction made it into the file.
        assert 'Agent(subagent_type: "plugin-leaks-preventer"' in body
        # NOTE: the markdown banner itself emits zero ANSI — that guarantee is
        # pinned conclusively by TestGateFiring::test_markdown_body_has_no_ansi
        # (isolated helper call). The whole report FILE legitimately carries
        # ANSI from the pre-existing print_report_summary / aggregated-findings
        # rendering (outside this banner's scope), so a whole-file ANSI
        # assertion here would test pre-existing behaviour, not the banner.

    def test_non_suppressability_no_color_and_bogus_env(self, tmp_path, monkeypatch) -> None:
        """No env var / --no-color path can mute the banner SIGNAL — only ANSI is stripped,
        the text always remains."""
        from validate_security import main

        monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
        # Bogus mute vectors that must have ZERO effect on the banner.
        monkeypatch.setenv("CPV_SKIP_SECURITY_GATES", "1")
        monkeypatch.setenv("CPV_NO_BANNER", "1")
        monkeypatch.setenv("CPV_SUPPRESS_GATE_A", "1")

        plugin_dir = tmp_path / "nosuppress-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "run.py").write_text("import subprocess\nsubprocess.run('echo ' + x, shell=True)\n")

        report_path = tmp_path / "r.md"
        # --no-color (if accepted) only strips ANSI; the banner text stays.
        argv = ["validate_security", str(plugin_dir), "--strict", "--report", str(report_path)]
        monkeypatch.setattr("sys.argv", argv)
        terminal = _capture(main)

        # The banner text is present despite every attempted mute vector.
        assert GATE_A_TITLE in terminal
        assert DEVITALIZER_AGENT in terminal
        # And the report file carries the markdown banner regardless.
        assert GATE_A_MD_HEADER in report_path.read_text()


# =====================================================================
# §8.3-#14 — Bucket-map integrity (guards against drift)
# =====================================================================


class TestBucketMapIntegrity:
    """The bucket map is well-formed and the §1 representatives resolve correctly."""

    def test_all_values_are_subset_of_abc(self) -> None:
        """Every bucket-map value is a non-empty frozenset ⊆ {A,B,C}."""
        assert _SECURITY_GATE_BUCKETS, "bucket map must not be empty"
        for ident, buckets in _SECURITY_GATE_BUCKETS.items():
            assert isinstance(buckets, frozenset), f"{ident}: value must be a frozenset"
            assert buckets, f"{ident}: bucket set must be non-empty"
            assert buckets <= {"A", "B", "C"}, f"{ident}: {buckets} not a subset of {{A,B,C}}"

    def test_dual_class_ids_have_at_least_two_buckets(self) -> None:
        """Every documented dual-class ID (§1.5) carries 2+ bucket letters."""
        dual_class = {
            "DESERIALIZATION": {"A", "C"},
            "LOG_INJECTION": {"A", "C"},
            "PROTOTYPE_POLLUTION": {"A", "C"},
            "CRED_ENV_READ": {"B", "C"},
            "A2A_DATA_LEAK": {"A", "B"},
            "CREDENTIAL_DISCOVERY": {"B", "C"},
        }
        for ident, expected in dual_class.items():
            assert ident in _SECURITY_GATE_BUCKETS, f"{ident} missing from bucket map"
            buckets = _SECURITY_GATE_BUCKETS[ident]
            assert len(buckets) >= 2, f"{ident} must be dual-class (got {buckets})"
            assert set(buckets) == expected, f"{ident}: expected {expected}, got {set(buckets)}"

    @pytest.mark.parametrize(
        ("ident", "expected"),
        [
            # One representative ID per §1 bucket, to catch drift if rules move.
            ("CMD_INJECTION", {"A"}),  # code_execution
            ("OBFUSCATION", {"A"}),  # obfuscation
            ("SUPPLY_CHAIN", {"A"}),  # supply_chain
            ("TIME_BOMB", {"A"}),  # evasion
            ("TOOL_POISONING", {"A"}),  # agent_manipulation (exec sub-class)
            ("CRYPTO_THEFT", {"A"}),  # crypto_theft
            ("DATA_EXFIL", {"A"}),  # data_exfiltration (active)
            ("RC-73", {"A"}),  # in-process taint
            ("RC-46", {"A"}),  # detector-signature meta
            ("HARDCODED_SECRET", {"B"}),  # secret literal
            ("SECRET_GITHUB_TOKEN", {"B"}),  # provider secret
            ("TOKEN_STEAL", {"B"}),  # credential_theft literal
            ("RC-135", {"B"}),  # leaked home-path
            ("INSECURE_TLS", {"C"}),  # network safeguard
            ("PATH_TRAVERSAL", {"C"}),  # filesystem safeguard
            ("SQL_INJECTION", {"C"}),  # injection safeguard
            ("XXE_INJECTION", {"C"}),  # XML-entity safeguard
            ("REGEX_DOS", {"C"}),  # denial_of_service safeguard
            ("PROMPT_INJECT", {"C"}),  # prompt_injection safeguard
            ("RC-61", {"C"}),  # sandbox-bypass safeguard
            ("RC-62", {"C"}),  # permission-bypass safeguard
        ],
    )
    def test_representative_ids_resolve_to_expected_bucket(self, ident: str, expected: set[str]) -> None:
        """Each §1 representative ID resolves to the bucket the spec specifies."""
        assert set(_SECURITY_GATE_BUCKETS[ident]) == expected

    def test_classifier_extracts_skillaudit_id_from_bracket_not_body(self) -> None:
        """The classifier reads the ID from the `[skillaudit:<cat> <ID>]` prefix, not from
        an ID-shaped word that merely appears in the human message body."""
        report = ValidationReport()
        # The bracket ID is a non-bucketed category; the body mentions
        # "CMD_INJECTION" as prose — that must NOT promote the finding to A.
        report.major("[skillaudit:authentication JWT_VULN] beware CMD_INJECTION style misuse", "a.py", 1)
        # JWT_VULN is Bucket C; the prose "CMD_INJECTION" must be ignored.
        assert _classify_security_buckets(report) == {"C"}

    def test_unmapped_id_triggers_no_bucket(self) -> None:
        """A finding whose ID is not in the map contributes to neither banner's trigger."""
        report = ValidationReport()
        report.critical("[skillaudit:authentication SOME_FUTURE_UNMAPPED_RULE] x", "a.py", 1)
        report.critical("RC-9999: a future structural rule", "b.py", 2)
        assert _classify_security_buckets(report) == set()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
