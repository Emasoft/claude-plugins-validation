#!/usr/bin/env python3
"""Regression locks for v2.99.1 SkillAudit calibration + pre-install scan.

v2.99.1 added three things on top of v2.99.0's native skillaudit port:

1. **Mandatory in validate_plugin pipeline** — Check 27 now runs inside
   ``validate_plugin.py`` (not only in ``validate_security.py``). Every
   `cpv-remote-validate plugin <path>` call exercises it.

2. **Three-way confidence classifier** — placeholder-driven matches are
   suppressed, doc-context matches are DEMOTED to WARNING (never
   silently dropped — per the user's "better safe than sorry"
   principle: keep findings visible; have the security agents triage).
   Demoted findings carry a ⚠ marker in the report.

3. **Threat-category prefix in messages** — ``[skillaudit:<category>
   <rule_id>] ...`` so reviewers see the threat domain immediately
   (skillaudit ships 21 categories CPV didn't have before).

4. **`/cpv-pre-install-scan`** — a new top-level slash command + backing
   script that scans an untrusted target in a sandboxed tmp dir BEFORE
   it lands in ``~/.claude/plugins/cache/``. Iron rule preserved.

5. **Word-boundary patches to CMD_INJECTION patterns** — `id` no longer
   matches as substring of `validation`, `ls` no longer matches inside
   `skills`, etc. Patches live in CPV's fork of the rules catalog and
   are documented inline.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
COMMANDS_DIR = REPO / "commands"
RULES_PATH = REPO / "scripts" / "rules" / "skillaudit_patterns.json"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ────────────────────────────────────────────────────────────────────────
# Three-way confidence classifier
# ────────────────────────────────────────────────────────────────────────


class TestConfidenceClassifier:
    def test_classifier_exports_three_way_api(self) -> None:
        import cpv_skillaudit_native as native

        assert hasattr(native, "_confidence"), (
            "v2.99.1 introduced a three-way confidence classifier "
            "(suppress / demote / keep). It must remain on the module."
        )

    def test_placeholder_is_suppressed(self) -> None:
        import cpv_skillaudit_native as native

        lines = ["Set OPENAI_API_KEY=YOUR_API_KEY in your .env"]
        cb_map, cb_ranges = native._build_code_block_map(lines)
        verdict = native._confidence(
            lines,
            0,
            "OPENAI_API_KEY=YOUR_API_KEY",
            "CRED_ENV_READ",
            cb_map,
            cb_ranges,
        )
        assert verdict == "suppress"

    def test_substring_shell_token_is_demoted(self) -> None:
        import cpv_skillaudit_native as native

        # `ls` as substring of `skills`
        lines = ["See `skills/foo/SKILL.md` for the spec."]
        cb_map, cb_ranges = native._build_code_block_map(lines)
        verdict = native._confidence(
            lines,
            0,
            "ls",
            "CMD_INJECTION",
            cb_map,
            cb_ranges,
        )
        assert verdict == "demote", (
            "shell-keyword substrings of longer identifiers must DEMOTE, "
            "not silently suppress — agents triage the ambiguity"
        )

    def test_real_shell_token_not_demoted(self) -> None:
        import cpv_skillaudit_native as native

        # `ls` as a real shell command with word boundaries
        lines = ["Run `ls -t` to list files."]
        cb_map, cb_ranges = native._build_code_block_map(lines)
        verdict = native._confidence(
            lines,
            0,
            "ls",
            "CMD_INJECTION",
            cb_map,
            cb_ranges,
        )
        assert verdict == "keep", "real word-bounded shell keyword must KEEP — better safe than sorry"

    def test_md_table_demotes_injection_rules(self) -> None:
        import cpv_skillaudit_native as native

        lines = ["| 1 | `skills/foo/SKILL.md` | `skills/foo/` | `skill_dir` |"]
        cb_map, cb_ranges = native._build_code_block_map(lines)
        verdict = native._confidence(
            lines,
            0,
            "`skills/foo/`",
            "CMD_INJECTION",
            cb_map,
            cb_ranges,
        )
        assert verdict == "demote"

    def test_github_actions_ssti_suppressed(self) -> None:
        # v2.106.0 (issue #40 root cause A): a GitHub Actions ``${{ … }}``
        # expression is GitHub's sandboxed context-expression syntax,
        # categorically NOT a Jinja2 / Mako / ERB server-side template.
        # The v2.99.1 behaviour merely DEMOTED it (still publish-blocking
        # under --strict); v2.106.0 SUPPRESSES it (the ``$`` prefix is a
        # reliable discriminator — Jinja is bare ``{{ }}``). GHA *script
        # injection* is a separate concern handled by the workflow
        # validators / zizmor, not the Jinja-SSTI rule.
        import cpv_skillaudit_native as native

        lines = ["  group: auto-merge-${{ github.event.pull_request.number }}"]
        cb_map, cb_ranges = native._build_code_block_map(lines)
        verdict = native._confidence(
            lines,
            0,
            "${{ github.event",
            "SSTI",
            cb_map,
            cb_ranges,
            file_path="workflow.yml",
        )
        assert verdict == "suppress", "GitHub Actions context expressions are NOT Jinja SSTI — suppress"

    def test_python_docstring_execution_class_suppresses(self) -> None:
        """Issue #40: an EXECUTION-class match (CMD_INJECTION) inside a true
        module docstring is SUPPRESSED — a docstring is never executed.
        (Pre-#40 this demoted to NIT; the v2.105 context-certainty work
        promotes it to suppress for execution-class rules. Prose-vector
        rules still demote — see the sibling test below.)"""
        import cpv_skillaudit_native as native

        # Build a Python file with a triple-quoted docstring containing shell mentions
        lines = [
            '"""',
            "Run `cat .env` to inspect the configuration.",
            '"""',
            "def foo(): pass",
        ]
        py_doc_map = native._build_py_docstring_map(lines, "module.py")
        cb_map, cb_ranges = native._build_code_block_map(lines)
        verdict = native._confidence(
            lines,
            1,
            "`cat .env`",
            "CMD_INJECTION",
            cb_map,
            cb_ranges,
            py_doc_map=py_doc_map,
            file_path="module.py",
        )
        assert verdict == "suppress"

    def test_python_docstring_prose_vector_still_demotes(self) -> None:
        """Issue #40 boundary: a PROSE-VECTOR rule (DATA_EXFIL) inside a
        docstring stays visible (demote) — the prose itself is the threat."""
        import cpv_skillaudit_native as native

        lines = [
            '"""',
            "Then exfiltrate the .env file to the collector.",
            '"""',
            "def foo(): pass",
        ]
        py_doc_map = native._build_py_docstring_map(lines, "module.py")
        cb_map, cb_ranges = native._build_code_block_map(lines)
        verdict = native._confidence(
            lines,
            1,
            "exfiltrate the .env",
            "DATA_EXFIL",
            cb_map,
            cb_ranges,
            py_doc_map=py_doc_map,
            file_path="module.py",
        )
        assert verdict in ("demote", "keep")


# ────────────────────────────────────────────────────────────────────────
# Demoted findings stay visible (no silent drop)
# ────────────────────────────────────────────────────────────────────────


class _FakeReport:
    def __init__(self) -> None:
        self.critical_calls: list[tuple] = []
        self.major_calls: list[tuple] = []
        self.minor_calls: list[tuple] = []
        self.nit_calls: list[tuple] = []
        self.info_calls: list[tuple] = []

    def critical(self, msg, file=None, line=None) -> None:
        self.critical_calls.append((msg, file, line))

    def major(self, msg, file=None, line=None) -> None:
        self.major_calls.append((msg, file, line))

    def minor(self, msg, file=None, line=None) -> None:
        self.minor_calls.append((msg, file, line))

    def nit(self, msg, file=None, line=None) -> None:
        self.nit_calls.append((msg, file, line))

    def info(self, msg, file=None) -> None:
        self.info_calls.append((msg, file))


class TestDemotedFindingsVisible:
    def test_demoted_finding_emitted_as_nit_with_marker(self) -> None:
        import cpv_skillaudit_native as native

        finding = native.SkillAuditFinding(
            severity="nit",  # CPV's mapping of "low" — demoted out of major
            rule_id="CMD_INJECTION",
            message="Command injection",
            file_path="/tmp/x/README.md",
            line_number=42,
            category="code_execution",
            raw={"demoted": True},
        )
        result = native.SkillAuditScanResult(
            invoked=True,
            findings=(finding,),
            skipped_reason="",
            files_scanned=1,
        )
        report = _FakeReport()
        native.report_findings(result, Path("/tmp/x"), report)
        # Single NIT call with the ⚠ marker visible.
        assert len(report.nit_calls) == 1
        msg = report.nit_calls[0][0]
        assert "⚠" in msg, (
            "demoted findings MUST carry the ⚠ marker so reviewers / downstream agents see they need disambiguation"
        )
        assert "demoted" in msg.lower()

    def test_category_prefix_in_message(self) -> None:
        import cpv_skillaudit_native as native

        finding = native.SkillAuditFinding(
            severity="critical",
            rule_id="DATA_EXFIL",
            message="Data exfiltration attempt",
            file_path="/tmp/x/skills/evil/SKILL.md",
            line_number=1,
            category="data_exfiltration",
            raw={"demoted": False},
        )
        result = native.SkillAuditScanResult(
            invoked=True,
            findings=(finding,),
            skipped_reason="",
            files_scanned=1,
        )
        report = _FakeReport()
        native.report_findings(result, Path("/tmp/x"), report)
        assert len(report.critical_calls) == 1
        msg = report.critical_calls[0][0]
        # Must include category prefix for at-a-glance reviewer experience.
        assert "[skillaudit:data_exfiltration DATA_EXFIL]" in msg


# ────────────────────────────────────────────────────────────────────────
# Mandatory in validate_plugin pipeline
# ────────────────────────────────────────────────────────────────────────


class TestValidatePluginPipelineHookup:
    body = (SCRIPTS_DIR / "validate_plugin.py").read_text(encoding="utf-8")

    def test_run_skillaudit_native_helper_present(self) -> None:
        assert "_run_skillaudit_native" in self.body, (
            "v2.99.1 wires the native skillaudit scan into validate_plugin's "
            "main pipeline — the _run_skillaudit_native helper MUST stay"
        )

    def test_helper_and_telemetry_both_dispatched_from_main(self) -> None:
        """Both ``_run_skillaudit_native`` and ``validate_telemetry`` are
        invoked from ``main()`` — either as direct calls or via the
        parallel-validator dispatch table (task #384 / A10 refactor).

        Pre-A10 layout was flat ``main()`` calls in source order:
            ``validate_telemetry(plugin_root, report)``
            ``_run_skillaudit_native(plugin_root, report)``

        Post-A10 the parallel orchestrator dispatches independent
        validators via a ``parallel_tasks`` list; ``_run_skillaudit_native``
        intentionally runs SERIALLY BEFORE the parallel batch because it
        mutates ``_set_cpv_self_scan`` module-state that downstream parallel
        validators read (race-free invariant — see the comment above
        ``_run_skillaudit_native(plugin_root, report)`` in validate_plugin.py).
        Source order is therefore reversed (skillaudit first, telemetry
        in the parallel_tasks list below), but BOTH still execute from
        ``main()`` in the security-audit phase — the test's real
        invariant.
        """
        sa_flat = "_run_skillaudit_native(plugin_root, report)"  # pre-#180 flat call
        sa_wrapped = '_serial_phase("skillaudit_native", _run_skillaudit_native'  # #180 progress-marker wrapper
        sa_present = sa_flat in self.body or sa_wrapped in self.body
        tel_present = (
            "validate_telemetry(plugin_root, report)" in self.body  # legacy flat-call shape
            or '("validate_telemetry", validate_telemetry,' in self.body  # post-A10 dispatch tuple
        )
        assert sa_present, "main() must directly call _run_skillaudit_native (serial-before-parallel mutex contract)"
        assert tel_present, "main() must dispatch validate_telemetry (flat or via parallel_tasks)"

        # The mutex contract this test exists for is about ORDER, not spelling:
        # skillaudit writes the self-scan module state that validators in the
        # parallel batch read, so its call must precede the batch. Asserting
        # that directly means a future re-spelling cannot quietly break it.
        sa_at = self.body.find(sa_flat if sa_flat in self.body else sa_wrapped)
        batch_at = self.body.find("parallel_tasks: list[")
        assert sa_at != -1 and batch_at != -1
        assert sa_at < batch_at, "skillaudit must run BEFORE the parallel batch is built (mutex contract)"

    def test_helper_documents_iron_rule(self) -> None:
        # The helper body must mention MANDATORY and the iron-rule
        # CRITICAL-on-missing-catalog behavior.
        assert "MANDATORY" in self.body
        # No env-var bypass.
        for bad in (
            "CPV_NO_SKILLAUDIT",
            "CPV_SKIP_SKILLAUDIT",
            "SKILLAUDIT_SKIP",
            "PLUGIN_SKIP_SKILLAUDIT",
        ):
            assert bad not in self.body, f"validate_plugin must not honor {bad}"


# ────────────────────────────────────────────────────────────────────────
# CMD_INJECTION word-boundary patches
# ────────────────────────────────────────────────────────────────────────


class TestCmdInjectionWordBoundaries:
    def test_backtick_pattern_uses_word_boundary(self) -> None:
        """Backtick CMD_INJECTION patterns must have ``\\b`` word boundaries
        around the shell-keyword alternation so they only match the bare
        command, not when the keyword is a substring of a longer identifier.

        Real backtick command substitution `` `whoami` `` IN A .sh FILE
        IS execution and MUST stay flagged (iron rule). Markdown context
        is handled separately by the markdown classifier's
        ``_match_falls_inside_inline_code`` discriminator.

        (r01 anthropic FP iter1 (2026-05-27): the earlier
        ``\\s+\\S`` tightening was reverted because it lost real
        backtick-substitution detection in `.sh` files — bare
        `` `whoami` `` IS execution there. The `$(cat)` no-args FP is
        handled by the SEPARATE `\\$\\(...\\)` patterns which DO require
        ``\\s+\\S``.)
        """
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        cmd_inj = next(r for r in data["rules"] if r["id"] == "CMD_INJECTION")
        backtick_pat = next((p for p in cmd_inj["patterns"] if p.startswith("`")), None)
        assert backtick_pat is not None
        # Must contain \b around the shell-keyword alternation.
        assert r"\b(?:curl" in backtick_pat or r"\b(?:cat" in backtick_pat
        assert r")\b" in backtick_pat

    def test_dollar_paren_pattern_requires_argument(self) -> None:
        """``$(...)`` CMD_INJECTION patterns must require a non-empty argument
        after the binary name. The Claude Code hook stdin idiom
        ``input=$(cat)`` (no args) is NOT command injection — it's the
        documented input pattern. Only ``$(cat $USER_INPUT)`` /
        ``$(curl url)`` /  ``$(cat /tmp/$VAR)`` patterns are real.
        """
        data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        cmd_inj = next(r for r in data["rules"] if r["id"] == "CMD_INJECTION")
        dp_pats = [p for p in cmd_inj["patterns"] if p.startswith(r"\$\(")]
        assert dp_pats, "CMD_INJECTION must have at least one \\$\\( pattern"
        for pat in dp_pats:
            assert r"\s+\S" in pat, (
                f"$(...) CMD_INJECTION pattern must require \\s+\\S after the "
                f"binary name (got: {pat!r}). Bare $(cat) (Claude Code hook "
                f"stdin idiom) is not injection."
            )


# ────────────────────────────────────────────────────────────────────────
# /cpv-pre-install-scan command
# ────────────────────────────────────────────────────────────────────────


class TestPreInstallScanCommand:
    def test_command_file_exists(self) -> None:
        assert (COMMANDS_DIR / "cpv-pre-install-scan.md").is_file()

    def test_command_documents_iron_rule(self) -> None:
        body = (COMMANDS_DIR / "cpv-pre-install-scan.md").read_text(encoding="utf-8")
        assert "MANDATORY" in body
        assert "iron rule" in body.lower() or "IRON RULE" in body

    def test_command_invokes_backing_script(self) -> None:
        body = (COMMANDS_DIR / "cpv-pre-install-scan.md").read_text(encoding="utf-8")
        assert "cpv_pre_install_scan.py" in body

    def test_backing_script_exists_and_is_executable(self) -> None:
        script = SCRIPTS_DIR / "cpv_pre_install_scan.py"
        assert script.is_file()
        import os as _os

        assert _os.access(script, _os.X_OK), "pre-install scanner must be executable"

    def test_backing_script_documents_sandbox(self) -> None:
        body = (SCRIPTS_DIR / "cpv_pre_install_scan.py").read_text(encoding="utf-8")
        assert "sandbox" in body.lower()
        # Must use tempfile.mkdtemp, never write to plugins/cache.
        assert "tempfile.mkdtemp" in body
        # No CODE that writes to ~/.claude/plugins/cache — strip docstring
        # mentions (which legitimately describe what the scanner does NOT do).
        # Look for actual filesystem-write or fs-touch calls referencing the path.
        write_pat = re.compile(
            r"(?:open|copy|copyfile|copytree|copy2|move|mkdir|symlink|write_text|write_bytes|writelines|\.write\()"
            r".*\.claude/plugins/cache",
            re.IGNORECASE,
        )
        assert not write_pat.search(body), (
            "scanner must not WRITE to ~/.claude/plugins/cache — mentioning it "
            "in a docstring is fine, but no fs-write call may target that path"
        )

    def test_backing_script_runs_skillaudit_native(self) -> None:
        body = (SCRIPTS_DIR / "cpv_pre_install_scan.py").read_text(encoding="utf-8")
        assert "run_skillaudit_scan" in body or "cpv_skillaudit_native" in body

    def test_backing_script_never_executes_target_code(self) -> None:
        body = (SCRIPTS_DIR / "cpv_pre_install_scan.py").read_text(encoding="utf-8")
        # Strip docstrings + comments — only inspect actual code.
        code_lines: list[str] = []
        in_triple = False
        for line in body.splitlines():
            stripped = line.strip()
            if in_triple:
                if '"""' in stripped or "'''" in stripped:
                    in_triple = False
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    continue
                in_triple = True
                continue
            no_comment = re.sub(r"#.*$", "", line)
            code_lines.append(no_comment)
        code_only = "\n".join(code_lines)

        # No `npm install` of the target, no `python <target>` execution,
        # no `bash <target>/install.sh`. The scan must be purely static.
        for forbidden in (
            r"\bnpm\s+install\s+",
            r"\bpython\s+\$\{?\s*target",
            r"\bbash\s+\$\{?\s*target",
        ):
            assert not re.search(forbidden, code_only), (
                f"pre-install scanner must NOT do '{forbidden}' — scan must be static"
            )


# ────────────────────────────────────────────────────────────────────────
# Rules + supplementary suppression frozensets stay populated
# ────────────────────────────────────────────────────────────────────────


class TestSuppressionTables:
    def test_md_table_suppressed_rules_populated(self) -> None:
        import cpv_skillaudit_native as native

        assert isinstance(native._MD_TABLE_SUPPRESSED_RULES, frozenset)
        # Must cover at least the historic CRED_ENV_READ entries plus
        # the v2.99.1 broadening.
        assert "CMD_INJECTION" in native._MD_TABLE_SUPPRESSED_RULES
        assert "SHELL_EXEC" in native._MD_TABLE_SUPPRESSED_RULES
        assert "CRED_ENV_READ" in native._MD_TABLE_SUPPRESSED_RULES

    def test_data_lang_fences_includes_yaml_json(self) -> None:
        import cpv_skillaudit_native as native

        for lang in ("json", "yaml", "yml", "toml"):
            assert lang in native._DATA_LANG_FENCES

    def test_short_shell_tokens_includes_ls_id_cat(self) -> None:
        import cpv_skillaudit_native as native

        for tok in ("ls", "id", "cat", "nc"):
            assert tok in native._SHORT_SHELL_TOKENS
