#!/usr/bin/env python3
"""Two-sided regression tests for GitHub issue #58 — FP classes on a
pyright-canonical, pyright-clean security plugin (ai-maestro-janitor).

Four independent FP classes, each fixed in deterministic-detection style (no
plugin-self-exempt flags):

- Class 1: mypy run on a pyright-canonical project (cpv_lint_engine).
- Class 2: guarded ``sys.exit(main())`` inside ``if __name__ == "__main__":``
  mis-flagged as MODULE scope (validate_hook).
- Class 3a: ``skills/foo`` extracted from an ASCII-art example box →
  "non-existent skill" (validate_xref).
- Class 3c: bare ```SKILL.md``` prose → "uses backtick format" MINOR
  (cpv_validation_common).

Every fix is paired with a regression-preserve (keep) test so the narrowing
can never silently disable the rule's real purpose.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_lint_engine as cle  # noqa: E402
from cpv_lint_engine import _canonical_python_typechecker, _config_fingerprint  # noqa: E402
from cpv_validation_common import ValidationReport, validate_toc_embedding  # noqa: E402
from validate_hook import detect_module_scope_sys_exit  # noqa: E402
from validate_xref import _strip_noise  # noqa: E402

# ── Class 1: canonical type-checker detection (mypy vs pyright) ───────────────


class TestCanonicalTypechecker:
    """The type-checker is chosen from the target's OWN config, never both."""

    def test_pyrightconfig_json_wins(self, tmp_path):
        (tmp_path / "pyrightconfig.json").write_text("{}")
        assert _canonical_python_typechecker(tmp_path) == "pyright"

    def test_pyproject_tool_pyright(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pyright]\ninclude = ['scripts']\n")
        assert _canonical_python_typechecker(tmp_path) == "pyright"

    def test_mypy_ini(self, tmp_path):
        (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = true\n")
        assert _canonical_python_typechecker(tmp_path) == "mypy"

    def test_pyproject_tool_mypy(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
        assert _canonical_python_typechecker(tmp_path) == "mypy"

    def test_setup_cfg_mypy_section(self, tmp_path):
        (tmp_path / "setup.cfg").write_text("[mypy]\nstrict = True\n")
        assert _canonical_python_typechecker(tmp_path) == "mypy"

    def test_default_is_mypy(self, tmp_path):
        assert _canonical_python_typechecker(tmp_path) == "mypy"

    def test_pyrightconfig_overrides_tool_mypy(self, tmp_path):
        """Strongest signal: a dedicated pyrightconfig.json wins over [tool.mypy]."""
        (tmp_path / "pyrightconfig.json").write_text("{}")
        (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
        assert _canonical_python_typechecker(tmp_path) == "pyright"

    def test_tool_pyright_loses_to_mypy_ini(self, tmp_path):
        """Tie-break: a bare [tool.pyright] does NOT beat an explicit mypy.ini —
        only a dedicated pyrightconfig.json does."""
        (tmp_path / "pyproject.toml").write_text("[tool.pyright]\ninclude = ['x']\n")
        (tmp_path / "mypy.ini").write_text("[mypy]\n")
        assert _canonical_python_typechecker(tmp_path) == "mypy"

    def test_unparseable_pyproject_falls_back(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("this is { not valid toml ][\n")
        assert _canonical_python_typechecker(tmp_path) == "mypy"


class TestTypecheckerConfigCacheKey:
    """DERIVED: the lint cache must invalidate when the type-checker config changes."""

    def test_python_config_filenames_include_typecheckers(self):
        py = cle._LANG_CONFIG_FILENAMES["python"]
        assert "pyrightconfig.json" in py
        assert "mypy.ini" in py
        assert ".mypy.ini" in py

    def test_fingerprint_changes_when_pyrightconfig_added(self, tmp_path):
        base = _config_fingerprint("python", tmp_path)
        (tmp_path / "pyrightconfig.json").write_text("{}")
        assert _config_fingerprint("python", tmp_path) != base


def _fake_proc(returncode: int, stdout: str = "", stderr: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestLintPythonCheckerBranch:
    """lint_python runs the canonical checker only — pyright on a pyright repo,
    mypy on a mypy repo, never both."""

    def _setup_repo(self, tmp_path) -> Path:
        (tmp_path / "scripts").mkdir()
        f = tmp_path / "scripts" / "mod.py"
        f.write_text("x: int = 1\n")
        return f

    def test_pyright_project_runs_pyright_not_mypy(self, tmp_path, monkeypatch):
        (tmp_path / "pyrightconfig.json").write_text("{}")
        f = self._setup_repo(tmp_path)
        invoked: list[str] = []

        def fake_run(cmd, **kw):
            invoked.append(cmd[0])
            if "check" in cmd:  # ruff
                return _fake_proc(0)
            if "--outputjson" in cmd:  # pyright
                return _fake_proc(
                    1,
                    json.dumps(
                        {
                            "generalDiagnostics": [
                                {
                                    "file": str(f),
                                    "severity": "error",
                                    "message": "bad type\nsecond line",
                                    "range": {"start": {"line": 4}},
                                }
                            ]
                        }
                    ),
                )
            return _fake_proc(1, "scripts/mod.py:1: error: should-not-run")  # mypy

        monkeypatch.setattr(cle, "_resolve", lambda tool: [tool])
        monkeypatch.setattr(cle.subprocess, "run", fake_run)
        report = ValidationReport()
        cle.lint_python(tmp_path, [f], report, strict_missing_tools=False)
        msgs = [r.message for r in report.results]
        assert any(m.startswith("Pyright:") for m in msgs), msgs
        assert not any(m.startswith("Mypy:") for m in msgs), msgs
        assert "mypy" not in invoked, f"mypy must NOT run on a pyright project: {invoked}"
        # First line of message only; line number is 1-based (range line 4 -> :5).
        pyright_msg = next(m for m in msgs if m.startswith("Pyright:"))
        assert "second line" not in pyright_msg
        assert ":5" in pyright_msg

    def test_mypy_project_runs_mypy_not_pyright(self, tmp_path, monkeypatch):
        # No pyright config -> mypy is canonical (status quo).
        f = self._setup_repo(tmp_path)
        invoked: list[str] = []

        def fake_run(cmd, **kw):
            invoked.append(cmd[0])
            if "check" in cmd:  # ruff
                return _fake_proc(0)
            if "--outputjson" in cmd:  # pyright (should NOT run)
                return _fake_proc(1, "{}")
            return _fake_proc(1, "scripts/mod.py:1: error: bad type")  # mypy

        monkeypatch.setattr(cle, "_resolve", lambda tool: [tool])
        monkeypatch.setattr(cle.subprocess, "run", fake_run)
        report = ValidationReport()
        cle.lint_python(tmp_path, [f], report, strict_missing_tools=False)
        msgs = [r.message for r in report.results]
        assert any(m.startswith("Mypy:") for m in msgs), msgs
        assert not any(m.startswith("Pyright:") for m in msgs), msgs
        assert "pyright" not in invoked, f"pyright must NOT run on a mypy project: {invoked}"

    def test_pyright_project_missing_pyright_is_info_not_strict_fail(self, tmp_path, monkeypatch):
        (tmp_path / "pyrightconfig.json").write_text("{}")
        f = self._setup_repo(tmp_path)

        def fake_run(cmd, **kw):
            return _fake_proc(0)  # ruff passes

        # ruff resolves; pyright does NOT.
        monkeypatch.setattr(cle, "_resolve", lambda tool: [tool] if tool == "ruff" else None)
        monkeypatch.setattr(cle.subprocess, "run", fake_run)
        report = ValidationReport()
        ok = cle.lint_python(tmp_path, [f], report, strict_missing_tools=True)
        # Type-check absence is auxiliary — never a strict failure.
        assert ok is True
        assert any(r.level == "INFO" and "pyright not available" in r.message for r in report.results)


# ── Class 2: __main__-guarded sys.exit is NOT module-scope ────────────────────


class TestDunderMainGuard:
    def _exits(self, tmp_path, src: str) -> list[int]:
        p = tmp_path / "hook.py"
        p.write_text(src, encoding="utf-8")
        return detect_module_scope_sys_exit(p)

    def test_dunder_main_sys_exit_not_flagged(self, tmp_path):
        src = 'import sys\n\n\ndef main():\n    return 0\n\n\nif __name__ == "__main__":\n    sys.exit(main())\n'
        assert self._exits(tmp_path, src) == []

    def test_dunder_main_raise_systemexit_not_flagged(self, tmp_path):
        src = 'def main():\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
        assert self._exits(tmp_path, src) == []

    def test_dunder_main_reversed_operands_not_flagged(self, tmp_path):
        src = 'import sys\n\nif "__main__" == __name__:\n    sys.exit(0)\n'
        assert self._exits(tmp_path, src) == []

    def test_top_level_if_guard_still_flagged(self, tmp_path):
        """REGRESSION-PRESERVE: a non-__main__ if-guarded sys.exit STILL flags."""
        src = "import sys\n\nClient = None\nif Client is None:\n    sys.exit(1)\n"
        assert self._exits(tmp_path, src), "a non-dunder-main if-guarded sys.exit must still flag"

    def test_try_except_import_sys_exit_still_flagged(self, tmp_path):
        """REGRESSION-PRESERVE: the PSS try/except ImportError sys.exit STILL flags."""
        src = "import sys\n\ntry:\n    import nonexistent_dep\nexcept ImportError:\n    sys.exit(1)\n"
        assert self._exits(tmp_path, src), "the PSS import-fatal sys.exit must still flag"

    def test_bare_module_sys_exit_still_flagged(self, tmp_path):
        """REGRESSION-PRESERVE: a top-level (unguarded) sys.exit STILL flags."""
        src = "import sys\n\nsys.exit(1)\n"
        assert self._exits(tmp_path, src)


# ── Class 3a: skill refs inside ASCII-art example boxes are ignored ───────────


class TestBoxDrawingSkillRef:
    def test_box_drawing_line_blanked(self):
        """A `skills/foo` inside a box-drawing example line is blanked."""
        content = "# Doc\n\nNormal prose.\n│ skills/foo/SKILL.md:7 │\nmore text\n"
        out = _strip_noise(content)
        assert "skills/foo" not in out, "box-art skill ref should be blanked"
        # Newline-preserving contract: line count unchanged.
        assert out.count("\n") == content.count("\n")

    def test_normal_line_skill_ref_preserved(self):
        """REGRESSION-PRESERVE: a real skills/ ref on a NORMAL line is untouched."""
        content = "# Doc\n\nSee skills/real-skill/SKILL.md for details.\n"
        out = _strip_noise(content)
        assert "skills/real-skill" in out

    def test_markdown_table_pipe_not_blanked(self):
        """ASCII pipe `|` (U+007C) is NOT a box-drawing char — markdown table rows
        (which may legitimately mention skills/) are NOT blanked."""
        content = "# Doc\n\n| name | path |\n|------|------|\n| foo | skills/real/SKILL.md |\n"
        out = _strip_noise(content)
        assert "skills/real" in out, "markdown table rows must not be blanked"


# ── Class 3c: bare `SKILL.md` prose mention is not a backtick reference ────────


class TestBareBacktickFilename:
    def test_bare_skill_md_not_flagged(self, tmp_path):
        content = "# Skill\n\nWe scan every `SKILL.md` under skills/ for problems.\n"
        p = tmp_path / "SKILL.md"
        p.write_text(content, encoding="utf-8")
        report = ValidationReport()
        validate_toc_embedding(content, p, tmp_path, report)
        assert not any("backtick" in r.message.lower() for r in report.results), [r.message for r in report.results]

    def test_self_reference_not_flagged(self, tmp_path):
        sub = tmp_path / "skills" / "foo"
        sub.mkdir(parents=True)
        p = sub / "SKILL.md"
        content = "# Foo\n\nThis file `skills/foo/SKILL.md` documents itself.\n"
        p.write_text(content, encoding="utf-8")
        report = ValidationReport()
        validate_toc_embedding(content, p, tmp_path, report)
        assert not any("backtick" in r.message.lower() for r in report.results), [r.message for r in report.results]

    def test_dir_qualified_backtick_still_flagged(self, tmp_path):
        """REGRESSION-PRESERVE: a real cross-file backtick ref STILL fires the MINOR."""
        refs = tmp_path / "references"
        refs.mkdir()
        (refs / "guide.md").write_text("# Guide\n\nbody\n", encoding="utf-8")
        content = "# Skill\n\nSee `references/guide.md` for details.\n"
        p = tmp_path / "SKILL.md"
        p.write_text(content, encoding="utf-8")
        report = ValidationReport()
        validate_toc_embedding(content, p, tmp_path, report)
        assert any("backtick" in r.message.lower() for r in report.results), [r.message for r in report.results]
