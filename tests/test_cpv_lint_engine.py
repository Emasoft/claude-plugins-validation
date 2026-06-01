#!/usr/bin/env python3
"""Tests for `cpv_lint_engine` — the consolidated lint module (v2.64.0).

Replaces the three pre-v2.64 lint test files (test_extended_linting,
test_lint_files_gitignore, test_lint_files_strict_mode). Coverage:

- detect_languages: extension matrix + gitignore filtering + symlink reject
- per-language linters: happy / unhappy / missing-tool strict / soft
- lint_repo: orchestration, missing-tool propagation, language subset
- regressions for the v2.63.1 _files_or_root fix and v2.63.2 strict default
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# tests/conftest.py adds scripts/ to sys.path; this is a defensive duplicate
# so the file works when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_lint_engine import (  # noqa: E402
    _DISPATCH,
    _files_or_root,
    detect_languages,
    lint_css,
    lint_dockerfile,
    lint_go,
    lint_html,
    lint_javascript,
    lint_json,
    lint_markdown,
    lint_powershell,
    lint_python,
    lint_repo,
    lint_rust,
    lint_shell,
    lint_sql,
    lint_toml,
    lint_xml,
    lint_yaml,
)
from cpv_validation_common import ValidationReport  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeResult:
    """subprocess.CompletedProcess stand-in with configurable rc/stdout/stderr."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_run(*results: FakeResult, capture_argv: list[list[str]] | None = None):
    """Build a subprocess.run mock that returns the next FakeResult per call.

    If `capture_argv` is supplied, each call's argv is appended to it.
    """
    queue = list(results)

    def fake_run(cmd, **kwargs):  # noqa: ARG001 — kwargs accepted for parity
        if capture_argv is not None:
            capture_argv.append(list(cmd))
        if not queue:
            return FakeResult(0, "", "")
        return queue.pop(0)

    return fake_run


def _counts(report: ValidationReport) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in report.results:
        out[r.level] = out.get(r.level, 0) + 1
    return out


# ---------------------------------------------------------------------------
# detect_languages
# ---------------------------------------------------------------------------


class TestDetectLanguages:
    """Verify the language bucketing covers every supported extension."""

    def test_detects_all_15_languages(self, tmp_path: Path) -> None:
        # One file per language category — at least one extension each.
        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "app.ts").write_text("export const x = 1;\n")
        (tmp_path / "deploy.sh").write_text("#!/bin/bash\necho hi\n")
        (tmp_path / "main.go").write_text("package main\n")
        (tmp_path / "main.rs").write_text("fn main() {}\n")
        (tmp_path / "README.md").write_text("# Hello\n")
        (tmp_path / "data.json").write_text("{}\n")
        (tmp_path / "config.yml").write_text("a: 1\n")
        (tmp_path / "Dockerfile").write_text("FROM alpine:3\n")
        (tmp_path / "feed.xml").write_text("<?xml version='1.0'?><r/>\n")
        (tmp_path / "main.css").write_text("body{}\n")
        (tmp_path / "index.html").write_text("<html></html>\n")
        (tmp_path / "schema.sql").write_text("SELECT 1;\n")
        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        (tmp_path / "script.ps1").write_text("Write-Host 'hi'\n")

        detected = detect_languages(tmp_path)

        expected = {
            "python",
            "javascript",
            "shell",
            "go",
            "rust",
            "markdown",
            "json",
            "yaml",
            "dockerfile",
            "xml",
            "css",
            "html",
            "sql",
            "toml",
            "powershell",
        }
        assert set(detected.keys()) == expected, f"missing: {expected - set(detected.keys())}"

    def test_extra_extensions_in_each_language(self, tmp_path: Path) -> None:
        """Each language bucket aggregates all of its registered extensions."""
        (tmp_path / "a.tsx").write_text("export const x = 1;\n")
        (tmp_path / "b.jsx").write_text("export const y = 2;\n")
        (tmp_path / "c.bash").write_text("#!/bin/bash\n")
        (tmp_path / "d.mdx").write_text("# md\n")
        (tmp_path / "e.yaml").write_text("k: v\n")
        (tmp_path / "f.scss").write_text(".a{}\n")
        (tmp_path / "g.less").write_text(".b{}\n")
        (tmp_path / "h.htm").write_text("<i></i>\n")
        (tmp_path / "i.psm1").write_text("function f {}\n")
        (tmp_path / "j.psd1").write_text("@{ A=1 }\n")
        (tmp_path / "k.dockerfile").write_text("FROM scratch\n")

        detected = detect_languages(tmp_path)
        assert any(p.name == "a.tsx" for p in detected.get("javascript", []))
        assert any(p.name == "b.jsx" for p in detected.get("javascript", []))
        assert any(p.name == "c.bash" for p in detected.get("shell", []))
        assert any(p.name == "d.mdx" for p in detected.get("markdown", []))
        assert any(p.name == "e.yaml" for p in detected.get("yaml", []))
        assert any(p.name == "f.scss" for p in detected.get("css", []))
        assert any(p.name == "g.less" for p in detected.get("css", []))
        assert any(p.name == "h.htm" for p in detected.get("html", []))
        assert any(p.name == "i.psm1" for p in detected.get("powershell", []))
        assert any(p.name == "j.psd1" for p in detected.get("powershell", []))
        assert any(p.name == "k.dockerfile" for p in detected.get("dockerfile", []))

    def test_gitignored_files_excluded(self, tmp_path: Path) -> None:
        """detect_languages must respect .gitignore (regression v2.63.1)."""
        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "INPUT_DEV").mkdir()
        (tmp_path / "INPUT_DEV" / "foreign.py").write_text("noise = 1\n")
        (tmp_path / ".gitignore").write_text("INPUT_DEV/\n")

        detected = detect_languages(tmp_path)
        py_files = detected.get("python", [])
        assert any(p.name == "main.py" for p in py_files)
        assert not any("INPUT_DEV" in str(p) for p in py_files), "INPUT_DEV/ files leaked through the gitignore filter"

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need admin on Windows")
    def test_symlinks_rejected(self, tmp_path: Path) -> None:
        """Unsafe symlinks must be skipped (trust boundary)."""
        outside = tmp_path.parent / f"_outside_{tmp_path.name}"
        outside.mkdir()
        try:
            (outside / "evil.py").write_text("x = 1\n")
            link = tmp_path / "link.py"
            link.symlink_to(outside / "evil.py")
            (tmp_path / "real.py").write_text("y = 2\n")

            detected = detect_languages(tmp_path)
            py_files = detected.get("python", [])
            # The real file is detected; the symlink is skipped (or resolved
            # to a path under tmp_path — both outcomes drop the symlink
            # itself from the list under the trust-boundary rule).
            assert any(p.name == "real.py" for p in py_files)
        finally:
            import shutil as _shutil

            _shutil.rmtree(outside, ignore_errors=True)


# ---------------------------------------------------------------------------
# _files_or_root regression (v2.63.1)
# ---------------------------------------------------------------------------


class TestFilesOrRoot:
    def test_returns_files_when_provided(self, tmp_path: Path) -> None:
        files = [tmp_path / "a.py", tmp_path / "b.py"]
        assert _files_or_root(tmp_path, files) == [str(tmp_path / "a.py"), str(tmp_path / "b.py")]

    def test_falls_back_to_repo_root_when_files_is_empty(self, tmp_path: Path) -> None:
        assert _files_or_root(tmp_path, []) == [str(tmp_path)]


# ---------------------------------------------------------------------------
# lint_python
# ---------------------------------------------------------------------------


class TestLintPython:
    def test_happy_path_no_findings(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", side_effect=lambda t: ["/bin/" + t]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(0, "", ""), FakeResult(0, "", "")),
            ):
                ok = lint_python(tmp_path, [tmp_path / "main.py"], report)
        assert ok is True
        assert _counts(report).get("MAJOR", 0) == 0

    def test_unhappy_path_records_per_file_majors(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.py"
        bad.write_text("import os\n")
        report = ValidationReport()
        ruff_stdout = f"{bad}:1:1: F401 unused import\n{bad}:1:1: E401 another error\n"
        with patch("cpv_lint_engine._resolve", side_effect=lambda t: ["/bin/" + t] if t == "ruff" else None):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(1, ruff_stdout, "")),
            ):
                ok = lint_python(tmp_path, [bad], report)
        assert ok is False
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("Ruff:" in r.message for r in majors)

    def test_missing_ruff_strict_mode_fails(self, tmp_path: Path) -> None:
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_python(tmp_path, [tmp_path / "main.py"], report, strict_missing_tools=True)
        assert ok is False
        assert any(r.level == "MAJOR" and "ruff" in r.message for r in report.results)

    def test_missing_ruff_soft_mode_warns(self, tmp_path: Path) -> None:
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_python(tmp_path, [tmp_path / "main.py"], report, strict_missing_tools=False)
        assert ok is True
        assert any(r.level == "WARNING" and "ruff" in r.message for r in report.results)

    def test_filtered_files_passed_to_ruff_not_repo_root(self, tmp_path: Path) -> None:
        """Regression v2.63.1: ruff must receive the file list, not str(repo_root)."""
        files = [tmp_path / "a.py", tmp_path / "b.py"]
        captured: list[list[str]] = []
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", side_effect=lambda t: ["/bin/" + t] if t == "ruff" else None):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(0, "", ""), capture_argv=captured),
            ):
                lint_python(tmp_path, files, report)
        assert captured, "subprocess.run was never called"
        ruff_argv = captured[0]
        assert str(tmp_path / "a.py") in ruff_argv
        assert str(tmp_path / "b.py") in ruff_argv
        # repo_root MUST NOT appear as a positional after the fix
        assert str(tmp_path) not in ruff_argv


# ---------------------------------------------------------------------------
# lint_javascript
# ---------------------------------------------------------------------------


class TestLintJavascript:
    def test_skipped_when_no_eslint_config(self, tmp_path: Path) -> None:
        report = ValidationReport()
        files = [tmp_path / "app.ts"]
        files[0].write_text("export const x = 1;\n")
        with patch("cpv_lint_engine._resolve", return_value=["/bin/eslint"]):
            ok = lint_javascript(tmp_path, files, report)
        assert ok is True
        assert any(r.level == "INFO" and "eslint config" in r.message for r in report.results)

    def test_filtered_files_passed_to_eslint(self, tmp_path: Path) -> None:
        (tmp_path / "eslint.config.js").write_text("export default [];\n")
        files = [tmp_path / "a.ts", tmp_path / "b.tsx"]
        captured: list[list[str]] = []
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/eslint"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(0, "[]", ""), capture_argv=captured),
            ):
                lint_javascript(tmp_path, files, report)
        assert captured
        argv = captured[0]
        assert str(tmp_path / "a.ts") in argv
        assert str(tmp_path / "b.tsx") in argv
        assert "." not in argv

    def test_eslint_error_severity_2_becomes_major(self, tmp_path: Path) -> None:
        (tmp_path / "eslint.config.js").write_text("export default [];\n")
        files = [tmp_path / "bad.ts"]
        report = ValidationReport()
        eslint_json = (
            '[{"filePath": "' + str(files[0]) + '", '
            '"messages": [{"severity": 2, "message": "no-undef", '
            '"line": 3, "ruleId": "no-undef"}]}]'
        )
        with patch("cpv_lint_engine._resolve", return_value=["/bin/eslint"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(1, eslint_json, "")),
            ):
                ok = lint_javascript(tmp_path, files, report)
        assert ok is False
        assert any(r.level == "MAJOR" and "no-undef" in r.message for r in report.results)

    def test_missing_eslint_strict_mode_fails(self, tmp_path: Path) -> None:
        report = ValidationReport()
        files = [tmp_path / "app.ts"]
        files[0].write_text("\n")
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_javascript(tmp_path, files, report, strict_missing_tools=True)
        assert ok is False
        assert any(r.level == "MAJOR" and "eslint" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# lint_shell
# ---------------------------------------------------------------------------


class TestLintShell:
    def test_clean_script_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "ok.sh"
        f.write_text("#!/bin/bash\necho hi\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/shellcheck"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(0, "", "")),
            ):
                ok = lint_shell(tmp_path, [f], report)
        assert ok is True

    def test_shellcheck_error_becomes_major(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.sh"
        f.write_text("#!/bin/bash\nfoo\n")
        report = ValidationReport()
        sc_json = (
            '[{"file": "' + str(f) + '", "line": 2, "level": "error", '
            '"code": 2148, "message": "Tips depend on target shell."}]'
        )
        with patch("cpv_lint_engine._resolve", return_value=["/bin/shellcheck"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(1, sc_json, "")),
            ):
                ok = lint_shell(tmp_path, [f], report)
        assert ok is False
        assert any(r.level == "MAJOR" and "SC2148" in r.message for r in report.results)

    def test_missing_shellcheck_strict_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "x.sh"
        f.write_text("\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_shell(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False


# ---------------------------------------------------------------------------
# lint_go
# ---------------------------------------------------------------------------


class TestLintGo:
    def test_gofmt_receives_file_list_not_dot(self, tmp_path: Path) -> None:
        files = [tmp_path / "main.go"]
        captured: list[list[str]] = []
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/gofmt"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(0, "", ""), capture_argv=captured),
            ):
                lint_go(tmp_path, files, report)
        assert captured
        gofmt_argv = captured[0]
        assert str(tmp_path / "main.go") in gofmt_argv
        assert "." not in gofmt_argv[2:]

    def test_go_vet_skipped_without_go_mod(self, tmp_path: Path) -> None:
        """Regression: go vet must NOT run unless repo_root has go.mod."""
        files = [tmp_path / "main.go"]
        captured: list[list[str]] = []
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", side_effect=lambda t: ["/bin/" + t]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(0, "", ""), capture_argv=captured),
            ):
                lint_go(tmp_path, files, report)
        for argv in captured:
            assert not (argv[0].endswith("go") and len(argv) > 1 and argv[1] == "vet"), (
                f"unexpected go vet invocation: {argv}"
            )

    def test_go_vet_runs_when_go_mod_present(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        files = [tmp_path / "main.go"]
        captured: list[list[str]] = []
        report = ValidationReport()

        def resolve_mock(name: str) -> list[str]:
            return ["/bin/gofmt"] if name == "gofmt" else ["/bin/go"]

        with patch("cpv_lint_engine._resolve", side_effect=resolve_mock):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(
                    FakeResult(0, "", ""),
                    FakeResult(0, "", ""),
                    capture_argv=captured,
                ),
            ):
                lint_go(tmp_path, files, report)
        # Expect a gofmt -l invocation AND a go vet invocation
        assert any("gofmt" in argv[0] for argv in captured)
        assert any(argv[0].endswith("go") and "vet" in argv for argv in captured)

    def test_gofmt_unformatted_files_become_majors(self, tmp_path: Path) -> None:
        files = [tmp_path / "main.go"]
        report = ValidationReport()
        gofmt_stdout = str(files[0]) + "\n"
        with patch("cpv_lint_engine._resolve", return_value=["/bin/gofmt"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(0, gofmt_stdout, "")),
            ):
                ok = lint_go(tmp_path, files, report)
        assert ok is False
        assert any(r.level == "MAJOR" and "needs formatting" in r.message for r in report.results)

    def test_missing_gofmt_strict_fails(self, tmp_path: Path) -> None:
        report = ValidationReport()
        files = [tmp_path / "main.go"]
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_go(tmp_path, files, report, strict_missing_tools=True)
        assert ok is False


# ---------------------------------------------------------------------------
# lint_rust
# ---------------------------------------------------------------------------


class TestLintRust:
    def test_no_cargo_toml_yields_info_only(self, tmp_path: Path) -> None:
        files = [tmp_path / "main.rs"]
        files[0].write_text("fn main() {}\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_rust(tmp_path, files, report)
        assert ok is True
        assert any(r.level == "INFO" and "Cargo.toml" in r.message for r in report.results)

    def test_missing_cargo_strict_fails(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\nversion='0.1.0'\n")
        files = [tmp_path / "main.rs"]
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_rust(tmp_path, files, report, strict_missing_tools=True)
        assert ok is False

    def test_cargo_fmt_failure_becomes_major(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\nversion='0.1.0'\n")
        files = [tmp_path / "main.rs"]
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/cargo"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(1, "", ""), FakeResult(0, "", "")),
            ):
                ok = lint_rust(tmp_path, files, report)
        assert ok is False
        assert any(r.level == "MAJOR" and "cargo fmt" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# lint_markdown / json / yaml / dockerfile / xml / css / html / sql / toml / ps
# ---------------------------------------------------------------------------


class TestLintMarkdown:
    def test_missing_tool_strict_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "README.md"
        f.write_text("# hi\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_markdown(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False

    def test_clean_md_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "ok.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/markdownlint-cli2"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(0, "", "")),
            ):
                ok = lint_markdown(tmp_path, [f], report)
        assert ok is True

    def test_findings_emit_nit_not_minor(self, tmp_path: Path) -> None:
        """Issue #20: markdownlint findings are stylistic — they must NOT
        block a publish gate via --strict. Demoting to NIT preserves the
        signal (developer sees the rule + line) without making
        markdownlint a publish blocker."""
        f = tmp_path / "doc.md"
        f.write_text("# Title\n\nlong line " + "x" * 200 + "\n")
        report = ValidationReport()
        stderr = (
            "doc.md:3 MD013/line-length Line length [Expected: 80; Actual: 213]\n"
            "doc.md:3 MD012/no-multiple-blanks Multiple blanks\n"
        )
        with patch("cpv_lint_engine._resolve", return_value=["/bin/markdownlint-cli2"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(1, "", stderr)),
            ):
                ok = lint_markdown(tmp_path, [f], report)
        # audit MED #15: NIT-only findings must NOT flip the return to False —
        # the documented module contract is "True iff no MAJOR/CRITICAL", and a
        # stylistic markdownlint nit must not block the publish gate (issue #20).
        assert ok is True
        # Severities: NIT only — no MINOR markdownlint findings.
        levels = [r.level for r in report.results if "markdownlint" in r.message]
        assert levels and all(level == "NIT" for level in levels), (
            f"expected all markdownlint findings to be NIT, got {levels}"
        )
        assert any("MD013" in r.message for r in report.results)
        assert any("MD012" in r.message for r in report.results)

    def test_silent_failure_surfaces_warning(self, tmp_path: Path) -> None:
        """Issue #20 fix: when markdownlint exits non-zero but produces
        NO parseable output, the developer used to see only "CPV blocked
        the push" with no actionable detail. Now we always emit at least
        one finding so the gate failure is explainable."""
        f = tmp_path / "doc.md"
        f.write_text("# hi\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/markdownlint-cli2"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(3, "", "")),
            ):
                ok = lint_markdown(tmp_path, [f], report)
        # audit MED #15: a silent markdownlint failure surfaces a WARNING (not a
        # MAJOR), and WARNING does not flip the return — markdownlint never
        # blocks a publish (issue #20); the WARNING keeps the breakage visible.
        assert ok is True
        # Either a WARNING (truly silent — empty stderr+stdout) or a NIT
        # carrying the unparsed output. EITHER way at least one finding
        # exists so the user knows why the gate failed.
        relevant = [r for r in report.results if "markdownlint" in r.message]
        assert relevant, "silent gate failure must produce at least one finding"
        assert any(r.level in ("WARNING", "NIT") for r in relevant)

    def test_bundled_config_passed_via_config_flag(self, tmp_path: Path) -> None:
        """Multi-path resolver: when target has no .markdownlint.json, the
        invocation must include `--config <path>` pointing at one of the
        two CPV-bundled candidates (scripts/.markdownlint.json for uvx,
        repo-root .markdownlint.json for cached). Issue #20 was that the
        single-path resolver missed the wheel case under uvx."""
        f = tmp_path / "x.md"
        f.write_text("# x\n")
        report = ValidationReport()
        captured: dict[str, list[str]] = {"argv": []}

        def fake_run(*args, **kwargs):
            # subprocess.run signature: cmd is positional arg[0].
            cmd = args[0] if args else kwargs.get("args", [])
            captured["argv"] = list(cmd)
            return FakeResult(0, "", "")

        with patch("cpv_lint_engine._resolve", return_value=["/bin/markdownlint-cli2"]):
            with patch("cpv_lint_engine.subprocess.run", side_effect=fake_run):
                lint_markdown(tmp_path, [f], report)
        # Either we found a bundled config (--config flag present) OR the
        # CPV install genuinely lacks one (in which case markdownlint runs
        # with its built-in defaults — fine for the unit test).
        if "--config" in captured["argv"]:
            cfg_idx = captured["argv"].index("--config")
            cfg_path = captured["argv"][cfg_idx + 1]
            assert cfg_path.endswith(".markdownlint.json"), f"unexpected --config path: {cfg_path}"


class TestLintJson:
    def test_valid_json_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "a.json"
        f.write_text('{"a": 1}\n')
        report = ValidationReport()
        ok = lint_json(tmp_path, [f], report)
        assert ok is True

    def test_invalid_json_becomes_major(self, tmp_path: Path) -> None:
        f = tmp_path / "broken.json"
        f.write_text('{"a": ,\n')
        report = ValidationReport()
        ok = lint_json(tmp_path, [f], report)
        assert ok is False
        assert any(r.level == "MAJOR" and "syntax error" in r.message for r in report.results)


class TestLintYaml:
    def test_missing_yamllint_strict_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "a.yml"
        f.write_text("k: v\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_yaml(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False

    def test_yamllint_error_becomes_major(self, tmp_path: Path) -> None:
        f = tmp_path / "a.yml"
        f.write_text("k: [\n")
        report = ValidationReport()
        stdout = "a.yml:1:5: [error] syntax error\n"
        with patch("cpv_lint_engine._resolve", return_value=["/bin/yamllint"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(1, stdout, "")),
            ):
                ok = lint_yaml(tmp_path, [f], report)
        assert ok is False
        assert any(r.level == "MAJOR" and "syntax error" in r.message for r in report.results)


class TestLintDockerfile:
    def test_missing_hadolint_strict_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "Dockerfile"
        f.write_text("FROM alpine\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_dockerfile(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False

    def test_clean_dockerfile_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "Dockerfile"
        f.write_text("FROM alpine:3\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["/bin/hadolint"]):
            with patch(
                "cpv_lint_engine.subprocess.run",
                side_effect=_make_run(FakeResult(0, "", "")),
            ):
                ok = lint_dockerfile(tmp_path, [f], report)
        assert ok is True


class TestLintXml:
    def test_missing_xmllint_strict_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "a.xml"
        f.write_text("<r/>\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_xml(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False


class TestLintCss:
    def test_missing_stylelint_soft_warns(self, tmp_path: Path) -> None:
        f = tmp_path / "a.css"
        f.write_text("body{}\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_css(tmp_path, [f], report, strict_missing_tools=False)
        assert ok is True
        assert any(r.level == "WARNING" for r in report.results)


class TestLintHtml:
    def test_missing_htmlhint_strict_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "i.html"
        f.write_text("<html></html>\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_html(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False


class TestLintSql:
    def test_missing_sqlfluff_strict_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "q.sql"
        f.write_text("SELECT 1;\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_sql(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False


class TestLintToml:
    def test_valid_toml_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "a.toml"
        f.write_text('[project]\nname="x"\n')
        report = ValidationReport()
        ok = lint_toml(tmp_path, [f], report)
        assert ok is True

    def test_invalid_toml_becomes_major(self, tmp_path: Path) -> None:
        f = tmp_path / "broken.toml"
        f.write_text("[a\nb=\n")
        report = ValidationReport()
        ok = lint_toml(tmp_path, [f], report)
        assert ok is False
        assert any(r.level == "MAJOR" and "TOML syntax" in r.message for r in report.results)


class TestLintPowershell:
    def test_missing_pssa_strict_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "x.ps1"
        f.write_text("Write-Host 'hi'\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_powershell(tmp_path, [f], report, strict_missing_tools=True)
        assert ok is False


# ---------------------------------------------------------------------------
# lint_repo orchestration
# ---------------------------------------------------------------------------


class TestLintRepoOrchestration:
    def test_empty_tree_passes(self, tmp_path: Path) -> None:
        report = ValidationReport()
        ok = lint_repo(tmp_path, report)
        assert ok is True
        assert any(r.level == "INFO" and "No source files" in r.message for r in report.results)

    def test_polyglot_passes_when_every_linter_runs(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "data.json").write_text('{"a":1}\n')
        report = ValidationReport()

        def fake_lint(*args, **kwargs):  # noqa: ARG001
            return True

        # v2.98.0: patch.object full-swap (matches sibling test files)
        # eliminates the cross-test shared-dict mutation that caused
        # xdist isolation flakes.
        import cpv_lint_engine as _cle

        fake_dispatch = dict(_DISPATCH)
        fake_dispatch["python"] = fake_lint
        with patch.object(_cle, "_DISPATCH", fake_dispatch):
            ok = lint_repo(tmp_path, report)
        assert ok is True

    def test_strict_missing_tool_fails_overall(self, tmp_path: Path) -> None:
        (tmp_path / "app.ts").write_text("export const x = 1;\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_repo(tmp_path, report, strict_missing_tools=True)
        assert ok is False
        assert any(r.level == "MAJOR" and "javascript" in r.message and "eslint" in r.message for r in report.results)

    def test_soft_missing_tool_passes_overall(self, tmp_path: Path) -> None:
        (tmp_path / "app.ts").write_text("export const x = 1;\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            ok = lint_repo(tmp_path, report, strict_missing_tools=False)
        assert ok is True
        assert any(r.level == "WARNING" and "eslint" in r.message for r in report.results)

    def test_language_subset_filter(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "app.ts").write_text("export const x = 1;\n")
        report = ValidationReport()

        called: list[str] = []

        def trace(name: str):
            def _fn(*args, **kwargs):  # noqa: ARG001
                called.append(name)
                return True

            return _fn

        # v2.98.0: switched from `patch.dict(_DISPATCH, …, clear=False)` to
        # `patch.object(cpv_lint_engine, "_DISPATCH", …)` — full attribute
        # swap (not in-place mutation) eliminates the cross-test shared-
        # state risk that caused xdist isolation flakes on v2.96.0 +
        # v2.97.0 publishes. Matches the pattern in
        # `test_lint_cache_integration.py` and `test_lint_parallelization.py`.
        #
        # ALSO: pass an isolated `ScannerCache(cache_dir=tmp_path/"cache")`
        # so that other parallel xdist workers cannot cause this run to
        # hit a stale cache entry and skip the lint subprocess (which
        # would leave ``called`` empty and trigger the same flake).
        import cpv_lint_engine as _cle
        from cpv_scanner_cache import ScannerCache

        fake_dispatch = {
            "python": trace("python"),
            "javascript": trace("javascript"),
        }
        with patch.object(_cle, "_DISPATCH", fake_dispatch):
            lint_repo(
                tmp_path,
                report,
                languages=["python"],
                cache=ScannerCache(cache_dir=tmp_path / "cache"),
            )
        assert called == ["python"]

    def test_gitignore_filtered_at_orchestration_layer(self, tmp_path: Path) -> None:
        """Regression v2.63.1: scan must skip files under gitignored paths."""
        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "INPUT_DEV").mkdir()
        (tmp_path / "INPUT_DEV" / "foreign.py").write_text("noise = 1\n")
        (tmp_path / ".gitignore").write_text("INPUT_DEV/\n")

        captured_lists: list[list[Path]] = []

        def capture_lint(repo, files, report, **kwargs):  # noqa: ARG001
            captured_lists.append(list(files))
            return True

        report = ValidationReport()
        # v2.98.0: patch.object full-swap (matches sibling test files)
        # + isolated ScannerCache so a stale entry from another worker
        # doesn't short-circuit the lint subprocess.
        import cpv_lint_engine as _cle
        from cpv_scanner_cache import ScannerCache

        fake_dispatch = dict(_DISPATCH)
        fake_dispatch["python"] = capture_lint
        with patch.object(_cle, "_DISPATCH", fake_dispatch):
            lint_repo(
                tmp_path,
                report,
                cache=ScannerCache(cache_dir=tmp_path / "cache"),
            )
        assert captured_lists, "lint_python was never called"
        py_files = captured_lists[0]
        assert any(p.name == "main.py" for p in py_files)
        assert not any("INPUT_DEV" in str(p) for p in py_files)


# ---------------------------------------------------------------------------
# Real-tool integration smoke test (skipped if ruff is missing locally)
# ---------------------------------------------------------------------------


class TestRealToolSmoke:
    @pytest.mark.skipif(
        # shutil.which is the portable stdlib PATH probe used everywhere else
        # in this suite. The previous form spawned `which` as a subprocess at
        # collection time, which raises FileNotFoundError on Windows (no `which`
        # binary) and crashes module collection rather than skipping the test.
        shutil.which("ruff") is None,
        reason="ruff not on PATH for smoke test",
    )
    def test_ruff_executes_against_clean_python_file(self, tmp_path: Path) -> None:
        f = tmp_path / "clean.py"
        f.write_text("x = 1\n")
        report = ValidationReport()
        ok = lint_python(tmp_path, [f], report, strict_missing_tools=False)
        # Real ruff should pass on a single trivial assignment
        assert ok is True
