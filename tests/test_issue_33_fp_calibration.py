#!/usr/bin/env python3
"""Regression locks for issue #33 — FP calibration on ai-maestro-janitor v0.5.0.

Issue #33 reported that v2.99.3's SkillAudit regex catalog over-fires
on safe constructs:
- Hardcoded-argv ``subprocess.run`` calls flagged as CMD_INJECTION
- Markdown prose mentions of ``curl``/``sudo``/etc. flagged as CRITICAL
- JSON ``description`` field text flagged as TIME_BOMB / CMD_INJECTION
- Every ``subprocess.run`` flagged at publish-blocking MINOR severity
- Hashlib.sha1 used for cache-key identity flagged as INSECURE_CRYPTO

v2.100.0 (TRDD-a4260cc6) introduced per-file-type context classifiers
(Python AST, JSON schema, markdown fence/prose, YAML workflow) that
provide the missing context. This test runs the full ``scan_content``
+ ``scan_path`` pipeline on a curated copy of the ai-maestro-janitor
v0.5.0 source tree and asserts:

* Zero CRITICAL findings (the v2.99.3 CRITICAL count was 9 → 0).
* Zero MAJOR findings (the v2.99.3 MAJOR count was 6 → 0).

Findings at NIT level (the demoted bucket) are EXPECTED — per the
iron rule "better safe than sorry, agents triage". The downstream
security agents read the demoted findings and confirm or deny.

The fixture is checked in at
``tests/fixtures/issue_33_no_fp_janitor/`` (git-cloned shallow checkout
at v0.5.0).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
FIXTURE_DIR = REPO / "tests" / "fixtures" / "issue_33_no_fp_janitor"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ────────────────────────────────────────────────────────────────────────
# Per-finding spot checks (these are the specific FP categories #33
# called out — each spot check is a regression lock).
# ────────────────────────────────────────────────────────────────────────


class TestHardcodedSubprocessArgvDoesNotTriggerCmdInjection:
    """`subprocess.run(["git-cliff", "--version"], ...)` → safe_literal, no finding."""

    def test_python_literal_argv_subprocess_run_suppresses(self) -> None:
        from cpv_skillaudit_native import scan_content

        src = (
            "import subprocess\n"
            "def check_git_cliff():\n"
            "    result = subprocess.run([\"git-cliff\", \"--version\"], "
            "capture_output=True, text=True)\n"
            "    return result.returncode == 0\n"
        )
        findings = scan_content(src, "scripts/check.py")
        actionable = [
            f for f in findings
            if not f.get("suppressed") and f.get("ruleId") in {"CMD_INJECTION", "SHELL_EXEC"}
        ]
        assert actionable == [], (
            f"hardcoded-literal-argv subprocess.run must produce zero "
            f"actionable CMD_INJECTION/SHELL_EXEC findings; got: {actionable}"
        )


class TestMarkdownProseDoesNotTriggerCmdInjection:
    """README prose mentioning shell commands → safe_doc, no actionable finding."""

    def test_markdown_prose_with_inline_code_suppresses_cmd_injection(self) -> None:
        from cpv_skillaudit_native import scan_content

        md = (
            "# Plugin Documentation\n\n"
            "Re-run `/janitor-arm` if no drift lines surface after an explicit resume.\n"
        )
        findings = scan_content(md, "README.md")
        actionable_exec = [
            f for f in findings
            if not f.get("suppressed")
            and f.get("severity") in ("critical", "high", "medium")
            and f.get("ruleId") in {"CMD_INJECTION", "SHELL_EXEC", "REVERSE_SHELL"}
        ]
        assert actionable_exec == [], (
            f"markdown prose with inline-code spans must not produce "
            f"actionable CRITICAL/MAJOR/MINOR CMD_INJECTION; got: {actionable_exec}"
        )


class TestJsonDescriptionFieldDoesNotTriggerCmdInjection:
    """plugin.json `"description": "...re-shells \\`git ls-files\\`..."` → safe_schema."""

    def test_json_description_with_shell_keyword_suppresses(self) -> None:
        from cpv_skillaudit_native import scan_content

        json_text = (
            "{\n"
            '  "userConfig": {\n'
            '    "tracked_ignored_interval": {\n'
            '      "title": "Tracked-Ignored Detector Cadence (seconds)",\n'
            '      "description": "The detector is HEAD-cached: it only re-shells '
            "`git ls-files` when HEAD has moved.\",\n"
            '      "type": "number",\n'
            '      "default": 3600\n'
            "    }\n"
            "  }\n"
            "}\n"
        )
        findings = scan_content(json_text, ".claude-plugin/plugin.json")
        actionable = [
            f for f in findings
            if not f.get("suppressed") and f.get("severity") in ("critical", "high")
        ]
        assert actionable == [], (
            f"JSON description field text must not produce CRITICAL/MAJOR "
            f"findings; got: {actionable}"
        )


class TestJsonDescriptionPollingCadenceDoesNotTriggerTimeBomb:
    """plugin.json polling-interval `"description"` → safe_schema, no TIME_BOMB."""

    def test_polling_cadence_description_suppresses_time_bomb(self) -> None:
        from cpv_skillaudit_native import scan_content

        json_text = (
            "{\n"
            '  "version_check_interval": {\n'
            '    "description": "Minimum seconds between checks against api.github.com '
            "for a newer plugin release. Default: 300 (5 min — runs every heartbeat "
            "by default).\",\n"
            '    "default": 300\n'
            "  }\n"
            "}\n"
        )
        findings = scan_content(json_text, ".claude-plugin/plugin.json")
        actionable = [
            f for f in findings
            if not f.get("suppressed") and f.get("ruleId") == "TIME_BOMB"
        ]
        assert actionable == [], (
            f"TIME_BOMB must not fire on JSON polling-interval description; "
            f"got: {actionable}"
        )


class TestHashlibSha1ForIdentityDoesNotTriggerInsecureCrypto:
    """`digest = hashlib.sha1(...).hexdigest()[:12]` → safe_literal (identity context)."""

    def test_sha1_hexdigest_truncated_for_session_id(self) -> None:
        from cpv_skillaudit_native import scan_content

        src = (
            "import hashlib\n"
            "import socket\n"
            "from datetime import datetime\n"
            "def session_id():\n"
            "    host = socket.gethostname().split('.')[0]\n"
            "    today = datetime.now().astimezone().strftime('%Y-%m-%d')\n"
            "    digest = hashlib.sha1(f'{host}@{today}'.encode('utf-8')).hexdigest()\n"
            "    return digest[:12]\n"
        )
        findings = scan_content(src, "scripts/detectors/trdd-reminder.py")
        actionable = [
            f for f in findings
            if not f.get("suppressed") and f.get("ruleId") == "INSECURE_CRYPTO"
        ]
        assert actionable == [], (
            f"hashlib.sha1 + hexdigest + slice → identity usage, must not "
            f"trigger INSECURE_CRYPTO; got: {actionable}"
        )


class TestCiSudoAptGetInstallDoesNotTriggerCriticalPrivilegeEsc:
    """`run: sudo apt-get install -y shellcheck` → known-safe CI, demote not critical."""

    def test_sudo_apt_get_in_workflow_run_block_demotes(self) -> None:
        from cpv_skillaudit_native import scan_content

        yml = (
            "name: CI\n"
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Install shellcheck\n"
            "        run: sudo apt-get update && sudo apt-get install -y shellcheck\n"
        )
        findings = scan_content(yml, ".github/workflows/ci.yml")
        critical = [
            f for f in findings
            if f.get("severity") == "critical" and f.get("ruleId") == "PRIVILEGE_ESC"
        ]
        assert critical == [], (
            f"known-safe CI sudo apt-get install must not produce CRITICAL "
            f"PRIVILEGE_ESC findings; got: {critical}"
        )


# ────────────────────────────────────────────────────────────────────────
# Full-fixture smoke test — the empirical issue #33 reproducer.
# ────────────────────────────────────────────────────────────────────────


class TestFullFixtureSmoke:
    """End-to-end: scan ai-maestro-janitor v0.5.0 source tree, assert
    zero CRITICAL + zero MAJOR.

    The fixture lives under ``tests/fixtures/issue_33_no_fp_janitor/``.
    It is committed as a shallow git clone at the v0.5.0 tag.
    """

    def test_fixture_directory_exists(self) -> None:
        if not FIXTURE_DIR.is_dir():
            import pytest
            pytest.skip(
                f"calibration fixture missing: {FIXTURE_DIR}. "
                "Re-clone with: git clone --depth 1 --branch v0.5.0 "
                "https://github.com/Emasoft/ai-maestro-janitor.git "
                "tests/fixtures/issue_33_no_fp_janitor. "
                "The fixture is gitignored to keep the CPV repo size small; "
                "local devs and the dev-machine CI can re-clone it on demand."
            )
        assert FIXTURE_DIR.is_dir()

    def test_fixture_full_scan_produces_zero_critical_and_zero_major(self) -> None:
        from cpv_skillaudit_native import scan_path

        if not FIXTURE_DIR.is_dir():
            import pytest
            pytest.skip("calibration fixture not present")

        findings, _files_scanned = scan_path(FIXTURE_DIR)
        # The plugin's own tests/ tree contains intentional test
        # fixtures (e.g. ``hashlib.sha1`` calls used to verify the
        # purge logic). In validate_plugin.py these are filtered via
        # the test-file eligibility check; the raw scan_path does
        # NOT apply that filter, so we apply it here to align with
        # the validate_plugin behavior the issue #33 acceptance
        # criterion targets.
        def _in_tests(f: dict) -> bool:
            file_path = (f.get("file") or "").replace("\\", "/")
            return file_path.startswith("tests/") or "/tests/" in file_path

        actionable = [f for f in findings if not f.get("suppressed") and not _in_tests(f)]
        critical = [f for f in actionable if f.get("severity") == "critical"]
        major = [f for f in actionable if f.get("severity") == "high"]

        assert critical == [], (
            f"ai-maestro-janitor v0.5.0 must produce ZERO CRITICAL findings "
            f"(was 9 in v2.99.3, target 0 per issue #33 acceptance); got "
            f"{len(critical)}: " + ", ".join(
                f"{f.get('ruleId')}@{f.get('file')}:{f.get('line')}"
                for f in critical[:5]
            )
        )

        assert major == [], (
            f"ai-maestro-janitor v0.5.0 must produce ZERO MAJOR findings "
            f"(was 6 in v2.99.3, target 0 per issue #33 acceptance); got "
            f"{len(major)}: " + ", ".join(
                f"{f.get('ruleId')}@{f.get('file')}:{f.get('line')}"
                for f in major[:5]
            )
        )
