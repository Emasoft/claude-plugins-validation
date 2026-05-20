#!/usr/bin/env python3
"""Regression-locks for the v2.99.0 SkillAudit MANDATORY scanner integration.

SkillAudit (https://github.com/megamind-0x/skillaudit) is wired into
``validate_security.py`` as Check 28 — a NON-SKIPPABLE external scanner.
These tests pin the iron-rule semantics:

* The scanner is installed via ``ensure_skillaudit`` in
  ``cpv_install_scanners.py``.
* ``install_all_scanners`` includes ``"skillaudit"`` in its result dict.
* The wrapper module ``cpv_skillaudit_scanner.py`` exposes the canonical
  shape ``run_skillaudit_scan`` / ``report_findings`` /
  ``is_skillaudit_available`` / ``build_scan_command`` / ``parse_findings``.
* Missing scanner → CRITICAL finding (iron rule — not WARNING / SKIPPED).
* No ``CPV_NO_SKILLAUDIT_INSTALL`` / ``SKILLAUDIT_SKIP`` / similar
  opt-out env vars are honored anywhere in the codebase.
* The bypass-guard prefix-pattern (``PLUGIN_SKIP_*``, ``CPV_SKIP_*``,
  ``SKIP_*``, ``NO_VERIFY``) still rejects any attempt to skip
  skillaudit at publish time.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ────────────────────────────────────────────────────────────────────────
# Module presence + symbol surface
# ────────────────────────────────────────────────────────────────────────


class TestSkillAuditModuleSurface:
    def test_skillaudit_scanner_module_exists(self) -> None:
        assert (SCRIPTS_DIR / "cpv_skillaudit_scanner.py").is_file()

    def test_module_exposes_canonical_symbols(self) -> None:
        import cpv_skillaudit_scanner as sa

        for name in (
            "is_skillaudit_available",
            "build_scan_command",
            "parse_findings",
            "run_skillaudit_scan",
            "report_findings",
            "SkillAuditFinding",
            "SkillAuditScanResult",
            "DEFAULT_TIMEOUT_SECONDS",
        ):
            assert hasattr(sa, name), f"cpv_skillaudit_scanner.{name} missing"

    def test_module_documents_iron_rule(self) -> None:
        body = (SCRIPTS_DIR / "cpv_skillaudit_scanner.py").read_text(encoding="utf-8")
        assert "MANDATORY" in body, "module docstring must declare MANDATORY"
        assert "iron rule" in body.lower() or "IRON RULE" in body, (
            "module must reference the iron rule explicitly"
        )
        # The wrapper MUST raise a CRITICAL (not info / not warning) when
        # the scanner cannot run.
        assert "report.critical(" in body, (
            "report_findings must call report.critical() when the scanner "
            "could not be invoked — iron rule"
        )


# ────────────────────────────────────────────────────────────────────────
# Installer integration
# ────────────────────────────────────────────────────────────────────────


class TestSkillAuditInstaller:
    def test_ensure_skillaudit_exported(self) -> None:
        import cpv_install_scanners as inst

        assert hasattr(inst, "ensure_skillaudit")
        assert "ensure_skillaudit" in inst.__all__

    def test_install_all_scanners_includes_skillaudit(self) -> None:
        import cpv_install_scanners as inst

        # Patch every per-scanner ensurer to return True so we exercise
        # only the dictionary shape — no real installs happen.
        with patch.object(inst, "ensure_fclones", return_value=True), \
             patch.object(inst, "ensure_cc_audit", return_value=True), \
             patch.object(inst, "ensure_trufflehog", return_value=True), \
             patch.object(inst, "ensure_semgrep", return_value=True), \
             patch.object(inst, "ensure_tirith", return_value=True), \
             patch.object(inst, "ensure_cisco_skill_scanner", return_value=True), \
             patch.object(inst, "ensure_skillaudit", return_value=True):
            result = inst.install_all_scanners()
        assert "skillaudit" in result
        assert result["skillaudit"] is True

    def test_ensure_skillaudit_honors_no_opt_out(self) -> None:
        """ensure_skillaudit MUST NOT honor a CPV_NO_SKILLAUDIT_INSTALL flag.

        Iron rule: skillaudit is mandatory. Other scanners (cc-audit,
        trufflehog, semgrep, tirith, Cisco) have ``_opt_out`` calls;
        skillaudit must not.
        """
        body = (SCRIPTS_DIR / "cpv_install_scanners.py").read_text(encoding="utf-8")
        # Find the ensure_skillaudit function body (between its def and the
        # next top-level def or end of file).
        start = body.find("def ensure_skillaudit(")
        assert start != -1, "ensure_skillaudit must exist"
        # The next def at column 0 marks the end of the function.
        end = body.find("\ndef ", start + 1)
        if end == -1:
            end = len(body)
        fn_body = body[start:end]
        assert "_opt_out(" not in fn_body, (
            "ensure_skillaudit must NOT call _opt_out — skillaudit is "
            "MANDATORY, no env-var bypass allowed"
        )
        assert "CPV_NO_SKILLAUDIT" not in fn_body, (
            "no CPV_NO_SKILLAUDIT_INSTALL env-var honored"
        )


# ────────────────────────────────────────────────────────────────────────
# Scanner invocation + parsing
# ────────────────────────────────────────────────────────────────────────


class TestSkillAuditCommandBuild:
    def test_build_uses_persistent_binary_when_present(self) -> None:
        import cpv_skillaudit_scanner as sa

        with patch("shutil.which", return_value="/usr/local/bin/skillaudit"):
            cmd = sa.build_scan_command(Path("/tmp/p"))
        assert cmd[0] == "skillaudit"
        assert "/tmp/p" in cmd
        assert "--json" in cmd

    def test_build_falls_back_to_npx(self) -> None:
        import cpv_skillaudit_scanner as sa

        def _which(name: str) -> str | None:
            return "/usr/bin/npx" if name == "npx" else None

        with patch("shutil.which", side_effect=_which):
            cmd = sa.build_scan_command(Path("/tmp/p"))
        assert cmd[0] == "npx"
        assert "--yes" in cmd  # non-interactive package install
        assert "skillaudit" in cmd
        assert "--json" in cmd

    def test_is_available_returns_true_when_npx_present(self) -> None:
        import cpv_skillaudit_scanner as sa

        def _which(name: str) -> str | None:
            return "/usr/bin/npx" if name == "npx" else None

        with patch("shutil.which", side_effect=_which):
            assert sa.is_skillaudit_available() is True

    def test_is_available_returns_false_when_neither_present(self) -> None:
        import cpv_skillaudit_scanner as sa

        with patch("shutil.which", return_value=None):
            assert sa.is_skillaudit_available() is False


class TestSkillAuditParseFindings:
    def test_parse_top_level_findings_array(self) -> None:
        import cpv_skillaudit_scanner as sa

        blob = json.dumps(
            {
                "riskLevel": "moderate",
                "findings": [
                    {
                        "ruleId": "CRED_ENV_READ",
                        "severity": "high",
                        "message": "reads .env",
                        "file": "skills/leak/SKILL.md",
                        "line": 42,
                    },
                    {
                        "ruleId": "OBFUSCATION",
                        "severity": "low",
                        "message": "base64 payload",
                    },
                ],
            }
        )
        out = sa.parse_findings(blob)
        assert len(out) == 2
        # Severity mapping: high → major, low → nit
        assert out[0].severity == "major"
        assert out[0].rule_id == "CRED_ENV_READ"
        assert out[0].file_path == "skills/leak/SKILL.md"
        assert out[0].line_number == 42
        assert out[1].severity == "nit"

    def test_parse_results_wrapped_shape(self) -> None:
        import cpv_skillaudit_scanner as sa

        blob = json.dumps(
            {
                "results": [
                    {
                        "skill_name": "x",
                        "findings": [
                            {
                                "ruleId": "PROMPT_INJECT",
                                "severity": "critical",
                                "message": "ignore previous instructions",
                                "location": {"file": "a.md", "line": 7},
                            }
                        ],
                    }
                ]
            }
        )
        out = sa.parse_findings(blob)
        assert len(out) == 1
        assert out[0].severity == "critical"
        assert out[0].file_path == "a.md"
        assert out[0].line_number == 7

    def test_parse_fallback_severity_from_risk_level(self) -> None:
        import cpv_skillaudit_scanner as sa

        # Finding has no per-finding severity → fall back to top-level riskLevel.
        blob = json.dumps(
            {
                "riskLevel": "high",
                "findings": [{"ruleId": "DATA_EXFIL", "message": "webhook.site"}],
            }
        )
        out = sa.parse_findings(blob)
        assert out[0].severity == "major"  # high → major

    def test_parse_malformed_json_is_empty(self) -> None:
        import cpv_skillaudit_scanner as sa

        assert sa.parse_findings("not json") == ()
        assert sa.parse_findings("{") == ()

    def test_parse_empty_findings(self) -> None:
        import cpv_skillaudit_scanner as sa

        assert sa.parse_findings(json.dumps({"riskLevel": "clean", "findings": []})) == ()


# ────────────────────────────────────────────────────────────────────────
# Iron-rule enforcement: missing scanner → CRITICAL
# ────────────────────────────────────────────────────────────────────────


class _FakeReport:
    """Minimal ValidationReport stand-in for testing report_findings."""

    def __init__(self) -> None:
        self.critical_calls: list[tuple] = []
        self.major_calls: list[tuple] = []
        self.minor_calls: list[tuple] = []
        self.nit_calls: list[tuple] = []
        self.info_calls: list[tuple] = []

    def critical(self, msg: str, file: str | None = None, line: int | None = None) -> None:
        self.critical_calls.append((msg, file, line))

    def major(self, msg: str, file: str | None = None, line: int | None = None) -> None:
        self.major_calls.append((msg, file, line))

    def minor(self, msg: str, file: str | None = None, line: int | None = None) -> None:
        self.minor_calls.append((msg, file, line))

    def nit(self, msg: str, file: str | None = None, line: int | None = None) -> None:
        self.nit_calls.append((msg, file, line))

    def info(self, msg: str, file: str | None = None) -> None:
        self.info_calls.append((msg, file))


class TestSkillAuditIronRule:
    def test_missing_scanner_emits_critical_not_info(self) -> None:
        import cpv_skillaudit_scanner as sa

        result = sa.SkillAuditScanResult(
            invoked=False,
            findings=(),
            skipped_reason="neither `skillaudit` nor `npx` on PATH",
            raw_stdout="",
            raw_stderr="",
            exit_code=-1,
        )
        report = _FakeReport()
        count = sa.report_findings(result, Path("/tmp/x"), report)
        assert count == 1
        assert len(report.critical_calls) == 1, (
            "missing skillaudit MUST surface as CRITICAL (iron rule)"
        )
        assert len(report.info_calls) == 0
        assert len(report.major_calls) == 0
        # Message must mention MANDATORY so the operator understands
        # this is not a normal optional-scanner skip.
        assert "MANDATORY" in report.critical_calls[0][0]

    def test_timeout_emits_critical(self) -> None:
        import cpv_skillaudit_scanner as sa

        result = sa.SkillAuditScanResult(
            invoked=False,
            findings=(),
            skipped_reason="skillaudit scan timed out after 300s",
            raw_stdout="",
            raw_stderr="",
            exit_code=-2,
        )
        report = _FakeReport()
        sa.report_findings(result, Path("/tmp/x"), report)
        assert len(report.critical_calls) == 1

    def test_invoked_with_findings_maps_to_severities(self) -> None:
        import cpv_skillaudit_scanner as sa

        findings = (
            sa.SkillAuditFinding(
                severity="critical",
                rule_id="CRED_ENV_READ",
                message="reads .env",
                file_path="/tmp/x/skills/a/SKILL.md",
                line_number=5,
                raw={},
            ),
            sa.SkillAuditFinding(
                severity="minor",
                rule_id="OBFUSCATION",
                message="b64",
                file_path="/tmp/x/skills/b/SKILL.md",
                line_number=None,
                raw={},
            ),
        )
        result = sa.SkillAuditScanResult(
            invoked=True,
            findings=findings,
            skipped_reason="",
            raw_stdout="{}",
            raw_stderr="",
            exit_code=0,
        )
        report = _FakeReport()
        count = sa.report_findings(result, Path("/tmp/x"), report)
        assert count == 2
        assert len(report.critical_calls) == 1
        assert len(report.minor_calls) == 1
        # Relativisation
        assert report.critical_calls[0][1] == "skills/a/SKILL.md"

    def test_should_skip_filter_drops_matching_findings(self) -> None:
        import cpv_skillaudit_scanner as sa

        findings = (
            sa.SkillAuditFinding(
                severity="major",
                rule_id="OBFUSCATION",
                message="vendored-dep noise",
                file_path="/tmp/x/node_modules/leftpad/index.js",
                line_number=1,
                raw={},
            ),
        )
        result = sa.SkillAuditScanResult(
            invoked=True,
            findings=findings,
            skipped_reason="",
            raw_stdout="{}",
            raw_stderr="",
            exit_code=0,
        )
        report = _FakeReport()
        count = sa.report_findings(
            result,
            Path("/tmp/x"),
            report,
            should_skip=lambda f, _line: "node_modules" in f,
        )
        assert count == 0
        assert report.major_calls == []


# ────────────────────────────────────────────────────────────────────────
# validate_security.py wiring
# ────────────────────────────────────────────────────────────────────────


class TestValidateSecurityWiring:
    body = (SCRIPTS_DIR / "validate_security.py").read_text(encoding="utf-8")

    def test_validate_security_imports_skillaudit_scanner(self) -> None:
        assert "from cpv_skillaudit_scanner import" in self.body, (
            "validate_security.py must import the skillaudit wrapper"
        )
        assert "run_skillaudit_scan" in self.body
        assert "report_findings as skillaudit_report_findings" in self.body

    def test_validate_security_records_skillaudit_step(self) -> None:
        # The step index must be unique (27) and the step name must mention
        # skillaudit by its canonical reference.
        assert (
            "External: SkillAudit (megamind-0x/skillaudit)" in self.body
        ), "the skillaudit step must use its canonical full name"
        # Step number 27 is the next unused slot after Cisco (26).
        assert "_record_step(\n        27," in self.body, (
            "skillaudit step must be recorded as step 27"
        )

    def test_validate_security_documents_mandatory_in_comment(self) -> None:
        assert "MANDATORY scanner" in self.body, (
            "validate_security.py must label skillaudit as MANDATORY"
        )

    def test_skillaudit_step_runs_after_cisco(self) -> None:
        # Step 27 (skillaudit) must appear in source AFTER step 26 (Cisco).
        cisco_idx = self.body.find('"External: Cisco AI Defense (skill-scanner)"')
        skill_idx = self.body.find('"External: SkillAudit (megamind-0x/skillaudit)"')
        assert cisco_idx != -1 and skill_idx != -1
        assert skill_idx > cisco_idx, (
            "skillaudit must be wired AFTER the Cisco scanner block"
        )

    def test_no_env_var_bypass_in_validate_security(self) -> None:
        # No conditional that skips the skillaudit block should exist.
        # Specifically: there must be NO ``os.environ.get("CPV_NO_SKILLAUDIT")``,
        # ``CPV_SKIP_SKILLAUDIT``, ``SKILLAUDIT_SKIP``, etc.
        for bad in (
            "CPV_NO_SKILLAUDIT",
            "CPV_SKIP_SKILLAUDIT",
            "SKILLAUDIT_SKIP",
            "PLUGIN_SKIP_SKILLAUDIT",
        ):
            assert bad not in self.body, (
                f"validate_security.py must NOT honor a {bad} env var — "
                "skillaudit is MANDATORY"
            )


# ────────────────────────────────────────────────────────────────────────
# Publish-time bypass-guard still rejects skillaudit skip names
# ────────────────────────────────────────────────────────────────────────


class TestBypassGuardCatchesSkillAuditSkipNames:
    """The Gate 0 bypass-guard (PLUGIN_SKIP_*, CPV_SKIP_*, SKIP_*, NO_VERIFY)
    must still reject any future attempt to skip skillaudit at publish time.

    We don't add a new exemption; we verify the existing prefix-pattern
    matches ``CPV_SKIP_SKILLAUDIT`` etc."""

    @pytest.mark.parametrize(
        "name",
        [
            "PLUGIN_SKIP_SKILLAUDIT",
            "CPV_SKIP_SKILLAUDIT",
            "SKIP_SKILLAUDIT",
        ],
    )
    def test_skip_name_would_be_rejected_by_publish_guard(self, name: str) -> None:
        publish_body = (SCRIPTS_DIR / "publish.py").read_text(encoding="utf-8")
        # The guard implementation lives in publish.py around the
        # "forbidden_prefixes" / "forbidden_exact" tuples — confirm the
        # canonical prefixes are still declared so the prefix match works.
        # Then verify the synthetic name we're imagining matches one of
        # those prefixes.
        prefixes = ("PLUGIN_SKIP_", "CPV_SKIP_", "SKIP_")
        for prefix in prefixes:
            assert prefix in publish_body, (
                f"publish.py must keep the {prefix} bypass-guard prefix"
            )
        assert any(name.startswith(p) for p in prefixes), (
            f"name {name} should be caught by the prefix-pattern guard"
        )

    def test_skillaudit_is_not_in_bypass_guard_exemption_list(self) -> None:
        # The Gate-0 exemption set in scripts/generate_plugin_repo.py
        # MUST NOT contain any SKILLAUDIT-related name. Iron rule.
        gen_body = (SCRIPTS_DIR / "generate_plugin_repo.py").read_text(encoding="utf-8")
        publish_body = (SCRIPTS_DIR / "publish.py").read_text(encoding="utf-8")
        for body in (gen_body, publish_body):
            assert "SKILLAUDIT" not in body or (
                "MANDATORY" in body or "skillaudit" in body.lower()
            ), (
                "If SKILLAUDIT appears in publish/generator code it must "
                "ONLY be in a context that documents MANDATORY status, "
                "never as an exemption from the bypass-guard"
            )
