#!/usr/bin/env python3
"""Regression tests for batch-29 audit fixes.

Each test is two-sided: it asserts the corrected behaviour AND a guard that
would have caught the original bug.

Two findings required a source change in this batch:

- validate_command.py #86 — `count_frontmatter_markers` counted EVERY `---`
  line, so a markdown horizontal rule in the command BODY produced a false
  MINOR "Multiple --- markers". It now counts only frontmatter-region
  delimiters; the missing-delimiter CRITICAL still fires for an unclosed file.
- validate_encoding.py #160 — `check_json_unicode` is scoped to Unicode
  handling (Rule 3) and intentionally does NOT report a plain JSON *syntax*
  error, because the JSON configs the runtime loads each have a dedicated
  validator that already CRITICALs a malformed file (reporting here would
  double-report). The change corrected a false comment; these tests lock in the
  intended behaviour (Unicode error reported; plain syntax error not).

The remaining five findings were verified to be ALREADY FIXED in the current
tree (their fixes cite the same audit numbers in code comments) — these tests
prove the verified-correct behaviour holds:

- validate_cache.py #85  — CA-06 must require the prefix-file mention and a
  write op on the SAME line (file-level coincidence is a false positive).
- validate_ide_config.py #161 — the `.env` reference NIT must report the line
  of the `.env` token itself, not one line early when a newline precedes it.
- validate_hook_precedence.py #159 — the second `len == 1 and not has_unknown`
  guard was dead code; removing it must not change emitted findings, and the
  `len == 1 + unknowns` case must still emit a finding.
- validate_lsp.py #163 — an external LSP config referenced via a `lspServers`
  string/array must have its per-server fields validated (not just its names
  extracted), and a redundant `.lsp.json` default reference must be validated
  exactly once (no double-report).
- update_marketplace_metadata.py #84 — `--check-only` for a plugin absent from
  marketplace.json must report "plugin not yet in marketplace", not the wrong
  "checksum changed".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_cache as vc  # noqa: E402
import validate_hook_precedence as vhp  # noqa: E402
import validate_ide_config as vic  # noqa: E402
import validate_lsp as vl  # noqa: E402
from validate_command import (  # noqa: E402
    CommandValidationReport,
    count_frontmatter_markers,
    validate_file_format,
)
from validate_encoding import (  # noqa: E402
    EncodingValidationReport,
    check_json_unicode,
)

# ---------------------------------------------------------------------------
# #85 — CA-06 same-line co-occurrence
# ---------------------------------------------------------------------------


class TestCA06SameLine:
    """CA-06 must fire only when a prefix-file write happens on a single line."""

    def test_read_plus_unrelated_write_is_not_flagged(self, tmp_path: Path) -> None:
        """Reading CLAUDE.md on one line and writing an unrelated file on another must NOT trigger CA-06."""
        script = tmp_path / "fp.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "cat CLAUDE.md | head -5\n"
            'echo "session started" > /tmp/myplugin.log\n'
        )
        findings = vc._collect_hook_for_fork_unsafe(script, "PreCompact", tmp_path)
        assert findings == [], f"CA-06 false positive on file-level coincidence: {findings}"

    def test_single_line_write_to_prefix_is_flagged_with_line_number(self, tmp_path: Path) -> None:
        """A genuine single-line append to CLAUDE.md must trigger CA-06 and report the offending line."""
        script = tmp_path / "real.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "echo step1\n"
            'echo "extra context" >> CLAUDE.md\n'
        )
        findings = vc._collect_hook_for_fork_unsafe(script, "PreCompact", tmp_path)
        assert len(findings) == 1
        assert findings[0].level == "WARNING"
        assert "CA-06" in findings[0].message
        # Same-line discipline lets us report the precise line (line 3 here).
        assert findings[0].line == 3

    def test_commented_prefix_write_is_ignored(self, tmp_path: Path) -> None:
        """A commented-out write to CLAUDE.md must not trigger CA-06 (comment lines are skipped)."""
        script = tmp_path / "commented.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            '# echo "x" >> CLAUDE.md   (disabled)\n'
            "true\n"
        )
        findings = vc._collect_hook_for_fork_unsafe(script, "PreCompact", tmp_path)
        assert findings == []


# ---------------------------------------------------------------------------
# #161 — .env reference line number
# ---------------------------------------------------------------------------


class TestDotenvLineNumber:
    """The .env NIT must report the line of the `.env` token, not one line early."""

    def test_env_at_line_start_after_newline(self, tmp_path: Path) -> None:
        """`.env` beginning a line (preceded by a newline) must be reported on its own line, not the previous one."""
        cfg = tmp_path / "config.txt"
        # `.env` is on line 5; the leading bound of the regex consumes the \n
        # terminating line 4 — the fix anchors the line number on the token.
        cfg.write_text("line1\nline2\nline3\nline4\n.env\nline6\n")
        report = vc.ValidationReport()  # plain report is fine for NIT
        added = vic.scan_ide_config_for_env_refs(cfg, report, tmp_path)
        assert added == 1
        env_nits = [r for r in report.results if r.level == "NIT" and ".env" in r.message]
        assert len(env_nits) == 1
        assert env_nits[0].line == 5, f"expected .env on line 5, got {env_nits[0].line}"

    def test_env_with_same_line_delimiter_unchanged(self, tmp_path: Path) -> None:
        """`envFile: ".env"` on a single line keeps its correct line number after the fix."""
        cfg = tmp_path / "settings.json"
        cfg.write_text('{\n  "task": "build",\n  "envFile": ".env"\n}\n')
        report = vc.ValidationReport()
        vic.scan_ide_config_for_env_refs(cfg, report, tmp_path)
        env_nits = [r for r in report.results if r.level == "NIT" and ".env" in r.message]
        assert len(env_nits) == 1
        assert env_nits[0].line == 3, f"expected .env on line 3, got {env_nits[0].line}"


# ---------------------------------------------------------------------------
# #159 — dead second guard in detect_precedence_conflicts
# ---------------------------------------------------------------------------


def _hook_with_decision(decision: str | None) -> dict[str, object]:
    """Build a minimal hook dict the precedence detector can read."""
    if decision is None:
        # An exec command hook: no static permissionDecision -> "unknown".
        return {"type": "command", "command": "./run.sh"}
    return {
        "type": "command",
        "command": "./run.sh",
        "hookSpecificOutput": {"permissionDecision": decision},
    }


class TestPrecedenceDeadGuardRemoval:
    """Removing the dead `len==1 and not has_unknown` guard must not change findings."""

    def test_uniform_single_decision_group_emits_no_finding(self) -> None:
        """A PreToolUse group with one inline decision and no unknowns is uniform -> no finding."""
        groups = {
            ("PreToolUse", "Bash"): [
                _hook_with_decision("allow"),
                _hook_with_decision("allow"),
            ]
        }
        findings = vhp.detect_precedence_conflicts(groups)
        assert findings == [], f"uniform group must yield no finding, got {findings}"

    def test_single_decision_plus_unknown_still_emits_finding(self) -> None:
        """One inline decision PLUS an unknown exec hook must STILL emit a finding (the fall-through case)."""
        groups = {
            ("PreToolUse", "Bash"): [
                _hook_with_decision("allow"),
                _hook_with_decision(None),  # exec script -> unknown at runtime
            ]
        }
        findings = vhp.detect_precedence_conflicts(groups)
        assert len(findings) == 1
        assert findings[0].has_unknown_decisions is True
        assert findings[0].inline_decisions == frozenset({"allow"})

    def test_conflicting_decisions_emit_finding(self) -> None:
        """Two distinct inline decisions in one group must emit a conflict finding."""
        groups = {
            ("PreToolUse", "Bash"): [
                _hook_with_decision("allow"),
                _hook_with_decision("deny"),
            ]
        }
        findings = vhp.detect_precedence_conflicts(groups)
        assert len(findings) == 1
        assert findings[0].inline_decisions == frozenset({"allow", "deny"})


# ---------------------------------------------------------------------------
# #163 — external LSP config field validation
# ---------------------------------------------------------------------------


def _write_plugin(root: Path, plugin_json: dict[str, object]) -> None:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(plugin_json))


class TestLspExternalRefValidation:
    """External `lspServers` references must be field-validated, exactly once."""

    def test_external_string_ref_field_is_validated(self, tmp_path: Path) -> None:
        """An external config referenced by a `lspServers` string with a bad `command` must be flagged."""
        (tmp_path / "extras").mkdir()
        (tmp_path / "extras" / "lsp.json").write_text(
            json.dumps({"pyls": {"command": 12345, "args": ["--stdio"]}})
        )
        _write_plugin(
            tmp_path,
            {"name": "x", "version": "1.0.0", "lspServers": "./extras/lsp.json"},
        )
        report = vl.validate_plugin_lsp(tmp_path)
        cmd_findings = [
            r for r in report.results if "command" in r.message.lower() and r.level == "CRITICAL"
        ]
        assert cmd_findings, "external-ref LSP config bad 'command' field escaped validation"

    def test_external_array_ref_field_is_validated(self, tmp_path: Path) -> None:
        """An external config referenced by a `lspServers` array entry must be field-validated too."""
        (tmp_path / "extras").mkdir()
        (tmp_path / "extras" / "lsp.json").write_text(
            json.dumps({"gopls": {"command": False}})
        )
        _write_plugin(
            tmp_path,
            {"name": "x", "version": "1.0.0", "lspServers": ["./extras/lsp.json"]},
        )
        report = vl.validate_plugin_lsp(tmp_path)
        cmd_findings = [
            r for r in report.results if "command" in r.message.lower() and r.level == "CRITICAL"
        ]
        assert cmd_findings, "array-ref LSP config bad 'command' field escaped validation"

    def test_redundant_default_ref_validated_exactly_once(self, tmp_path: Path) -> None:
        """A redundant `lspServers: ".lsp.json"` must field-validate the file exactly once (no double-report)."""
        (tmp_path / ".lsp.json").write_text(
            json.dumps({"pyls": {"command": 999, "args": ["--stdio"]}})
        )
        _write_plugin(
            tmp_path,
            {"name": "x", "version": "1.0.0", "lspServers": ".lsp.json"},
        )
        report = vl.validate_plugin_lsp(tmp_path)
        cmd_findings = [r for r in report.results if "command" in r.message.lower()]
        assert len(cmd_findings) == 1, (
            f"redundant default ref must validate once, got {len(cmd_findings)}: "
            f"{[r.message for r in cmd_findings]}"
        )

    def test_valid_external_ref_has_no_command_finding(self, tmp_path: Path) -> None:
        """A well-formed external LSP config must NOT produce any command-field finding (benign side)."""
        (tmp_path / "extras").mkdir()
        (tmp_path / "extras" / "lsp.json").write_text(
            json.dumps({"pyls": {"command": "pyright-langserver", "args": ["--stdio"]}})
        )
        _write_plugin(
            tmp_path,
            {"name": "x", "version": "1.0.0", "lspServers": "./extras/lsp.json"},
        )
        report = vl.validate_plugin_lsp(tmp_path)
        bad = [
            r
            for r in report.results
            if "command" in r.message.lower() and r.level in ("CRITICAL", "MAJOR")
        ]
        assert bad == [], f"valid external LSP config produced spurious command findings: {bad}"


# ---------------------------------------------------------------------------
# #84 — check-only reason for brand-new plugin
# ---------------------------------------------------------------------------


class TestCheckOnlyReason:
    """`--check-only` must distinguish a brand-new plugin from a changed checksum."""

    def _run_check_only(self, tmp_path: Path) -> dict[str, Any]:
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                str(scripts_dir / "update_marketplace_metadata.py"),
                "--plugin-dir",
                str(tmp_path),
                "--marketplace",
                str(tmp_path / "marketplace.json"),
                "--check-only",
                "--json",
            ],
            capture_output=True,
            text=True,
            cwd=str(scripts_dir),
        )
        payload: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
        return payload

    def test_plugin_absent_reports_not_in_marketplace(self, tmp_path: Path) -> None:
        """A plugin not present in marketplace.json must report 'plugin not yet in marketplace', not 'checksum changed'."""
        _write_plugin(tmp_path, {"name": "newplug", "version": "1.0.0", "description": "x"})
        (tmp_path / "marketplace.json").write_text(
            json.dumps({"name": "m", "plugins": [{"name": "otherplug", "checksum": "deadbeef"}]})
        )
        payload = self._run_check_only(tmp_path)
        assert payload["needs_update"] is True
        assert payload["reason"] == "plugin not yet in marketplace", payload

    def test_plugin_present_with_different_checksum_reports_changed(self, tmp_path: Path) -> None:
        """A plugin already in marketplace.json with a stale checksum must report 'checksum changed'."""
        _write_plugin(tmp_path, {"name": "myplug", "version": "1.0.0", "description": "x"})
        # Pre-seed an entry for THIS plugin with a wrong checksum so the
        # name matches but the checksum differs -> genuine "checksum changed".
        (tmp_path / "marketplace.json").write_text(
            json.dumps({"name": "m", "plugins": [{"name": "myplug", "checksum": "0" * 64}]})
        )
        payload = self._run_check_only(tmp_path)
        assert payload["needs_update"] is True
        assert payload["reason"] == "checksum changed", payload


# ---------------------------------------------------------------------------
# #86 — body horizontal rules must not be counted as frontmatter markers
# ---------------------------------------------------------------------------


class TestFrontmatterMarkerCounting:
    """count_frontmatter_markers must count only frontmatter-region delimiters."""

    def test_body_horizontal_rules_not_counted(self) -> None:
        """A valid command whose body uses `---` horizontal rules counts exactly two markers."""
        content = (
            "---\n"
            "name: foo\n"
            "description: a command\n"
            "---\n"
            "\n"
            "# Heading\n"
            "\n"
            "Intro text.\n"
            "\n"
            "---\n"  # body horizontal rule (markdown thematic break) — NOT a marker
            "\n"
            "More text.\n"
            "\n"
            "---\n"  # second body horizontal rule
            "\n"
            "End.\n"
        )
        assert count_frontmatter_markers(content) == 2

    def test_valid_command_with_body_hr_emits_no_false_minor(self) -> None:
        """validate_file_format must NOT emit a 'Multiple ---' MINOR for body horizontal rules (audit #86)."""
        content = "---\nname: t\ndescription: d\n---\nBody\n\n---\n\nmore\n"
        report = CommandValidationReport()
        ok = validate_file_format(content, report, "cmd.md")
        assert ok is True
        assert not any(r.level == "MINOR" and "Multiple ---" in r.message for r in report.results)

    def test_clean_frontmatter_counts_two(self) -> None:
        """A minimal valid command (open + close, no body rules) counts exactly two markers."""
        assert count_frontmatter_markers("---\nname: x\ndescription: d\n---\nbody") == 2

    def test_no_markers_still_reports_missing_critical(self) -> None:
        """A file with no frontmatter delimiters at all still triggers the missing-frontmatter CRITICAL (guard)."""
        report = CommandValidationReport()
        ok = validate_file_format("No markers at all", report, "bad.md")
        assert ok is False
        assert any(
            r.level == "CRITICAL" and "Missing YAML frontmatter markers" in r.message
            for r in report.results
        )

    def test_unclosed_frontmatter_not_rescued_by_body_rule(self) -> None:
        """An unclosed frontmatter must not be rescued to >=2 by a body `---`; it stays < 2 -> CRITICAL (guard)."""
        content = "---\nname: x\ndescription: d\n\nbody with no close\n---\ntrailing\n"
        assert count_frontmatter_markers(content) < 2
        report = CommandValidationReport()
        ok = validate_file_format(content, report, "unclosed.md")
        assert ok is False
        assert any(
            r.level == "CRITICAL" and "Missing YAML frontmatter markers" in r.message
            for r in report.results
        )


# ---------------------------------------------------------------------------
# #160 — check_json_unicode is Unicode-scoped (report Unicode errors only)
# ---------------------------------------------------------------------------


class TestJsonUnicodeScope:
    """check_json_unicode reports Unicode errors but intentionally ignores plain syntax errors."""

    def test_valid_json_passes_clean(self) -> None:
        """Well-formed JSON produces no findings."""
        report = EncodingValidationReport()
        assert check_json_unicode('{"a": 1, "b": [2, 3]}', "data.json", report) is True
        assert report.results == []

    def test_plain_syntax_error_not_reported_here(self) -> None:
        """A plain JSON syntax error is NOT reported by the Unicode-scoped check (avoids double-report)."""
        report = EncodingValidationReport()
        # Trailing comma -> JSONDecodeError that does NOT mention unicode/utf. Per
        # Rule 3 scope this returns True with no finding; the config files that
        # matter are syntax-checked by their own dedicated validators.
        assert check_json_unicode('{ "a": 1, }', "fixtures/sample.json", report) is True
        assert report.results == []

    def test_non_json_files_are_ignored(self) -> None:
        """check_json_unicode is a no-op for non-.json files."""
        report = EncodingValidationReport()
        assert check_json_unicode("not json at all {{{", "readme.md", report) is True
        assert report.results == []

    def test_unicode_class_error_is_reported_as_major(
        self, monkeypatch: Any
    ) -> None:
        """When the decode error wording IS unicode-class, the check reports MAJOR (the discriminator).

        CPython's stdlib ``json`` never emits a "unicode"/"utf"-worded
        ``JSONDecodeError`` for a ``str`` input, so the only deterministic way to
        exercise the MAJOR branch is to drive ``json.loads`` to raise such an
        error. This tests the function's OWN classification logic (the
        unicode-vs-other branch), not the thing under test.
        """
        import validate_encoding as ve

        def _raise_unicode_error(_content: str) -> object:
            raise json.JSONDecodeError("Invalid \\uXXXX escape (unicode)", "{}", 1)

        monkeypatch.setattr(ve.json, "loads", _raise_unicode_error)
        report = EncodingValidationReport()
        result = check_json_unicode('{"broken": true}', "data.json", report)
        assert result is False
        assert any(
            r.level == "MAJOR" and "JSON Unicode error" in r.message for r in report.results
        )
        assert report.stats["unicode_issues"] >= 1

    def test_non_unicode_decode_error_is_not_reported(
        self, monkeypatch: Any
    ) -> None:
        """When the decode error wording is NOT unicode-class, the check stays silent (no double-report).

        This is the reachable real-world path: a plain syntax error. The function
        returns True and adds no finding, deferring to the dedicated per-config
        validators that already CRITICAL a malformed file.
        """
        import validate_encoding as ve

        def _raise_syntax_error(_content: str) -> object:
            raise json.JSONDecodeError("Expecting value", "{ bad }", 2)

        monkeypatch.setattr(ve.json, "loads", _raise_syntax_error)
        report = EncodingValidationReport()
        result = check_json_unicode('{"x": 1}', "data.json", report)
        assert result is True
        assert report.results == []
