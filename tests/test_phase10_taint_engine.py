"""Tests for Phase 10 (RC-73/74/75) AST-based Python taint engine."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_taint_engine import (  # noqa: E402
    SANITIZERS_BARE,
    SANITIZERS_QUALIFIED,
    TAINT_SINKS_DIRECT,
    TAINT_SOURCES,
    TaintFinding,
    analyze_file,
    analyze_module,
    analyze_plugin,
    iter_python_files,
)


def _analyze(src: str) -> list[TaintFinding]:
    return analyze_module(ast.parse(src))


# -----------------------------------------------------------------------------
# Vocabulary sanity
# -----------------------------------------------------------------------------


class TestVocabulary:
    def test_minimum_source_count(self) -> None:
        assert len(TAINT_SOURCES) >= 8

    def test_minimum_sink_count(self) -> None:
        assert "exec" in TAINT_SINKS_DIRECT
        assert "eval" in TAINT_SINKS_DIRECT

    def test_minimum_sanitizer_count(self) -> None:
        assert ("shlex", "quote") in SANITIZERS_QUALIFIED
        assert "int" in SANITIZERS_BARE


# -----------------------------------------------------------------------------
# RC-73 — direct (1-hop) source-to-sink
# -----------------------------------------------------------------------------


class TestRC73Direct:
    def test_environ_to_exec(self) -> None:
        src = "import os\nx = os.environ.get('CMD')\nexec(x)\n"
        findings = _analyze(src)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "RC-73"
        assert f.hop_count == 1
        assert "environ" in f.source
        assert "exec" in f.sink

    def test_getenv_to_eval(self) -> None:
        src = "import os\nq = os.getenv('Q')\nresult = eval(q)\n"
        findings = _analyze(src)
        assert len(findings) == 1
        assert findings[0].rule_id == "RC-73"

    def test_environ_subscript_to_compile(self) -> None:
        src = "import os\ncode = os.environ['CODE']\ncompile(code, '<x>', 'exec')\n"
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" and "compile" in f.sink for f in findings)

    def test_input_to_exec(self) -> None:
        src = "x = input()\nexec(x)\n"
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" for f in findings)

    def test_argv_to_os_system(self) -> None:
        src = "import sys, os\ncmd = sys.argv\nos.system(cmd)\n"
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" and "os.system" in f.sink for f in findings)

    def test_subprocess_run_with_shell_true(self) -> None:
        src = "import os, subprocess\nx = os.environ.get('X')\nsubprocess.run(x, shell=True)\n"
        findings = _analyze(src)
        assert any("subprocess.run" in f.sink and "shell=True" in f.sink for f in findings)

    def test_subprocess_run_without_shell_silent(self) -> None:
        src = "import os, subprocess\nx = os.environ.get('X')\nsubprocess.run(x)\n"
        findings = _analyze(src)
        # subprocess.run without shell=True is NOT a sink
        assert not findings


# -----------------------------------------------------------------------------
# RC-74 — transitive (2+ hops) propagation
# -----------------------------------------------------------------------------


class TestRC74Transitive:
    def test_two_hop(self) -> None:
        src = "import os\nx = os.environ.get('A')\ny = x\nexec(y)\n"
        findings = _analyze(src)
        assert any(f.rule_id == "RC-74" and f.hop_count == 2 for f in findings)

    def test_three_hop(self) -> None:
        src = "import os\nx = os.environ.get('A')\ny = x\nz = y\neval(z)\n"
        findings = _analyze(src)
        assert any(f.rule_id == "RC-74" and f.hop_count == 3 for f in findings)

    def test_overwrite_clears_taint(self) -> None:
        src = (
            "import os\n"
            "x = os.environ.get('A')\n"
            "x = 'safe constant'\n"
            "exec(x)\n"
        )
        # Source is overwritten by a non-source non-Name expression → no taint
        findings = _analyze(src)
        assert findings == []


# -----------------------------------------------------------------------------
# RC-75 — sanitizer recognition
# -----------------------------------------------------------------------------


class TestRC75Sanitizer:
    def test_shlex_quote_clears(self) -> None:
        src = (
            "import os, shlex\n"
            "x = os.environ.get('A')\n"
            "y = shlex.quote(x)\n"
            "import os as os2\nos2.system(y)\n"
        )
        # shlex.quote sanitizes; the os.system call gets a clean string
        findings = _analyze(src)
        assert findings == []

    def test_int_cast_clears(self) -> None:
        src = (
            "import os\n"
            "raw = os.environ.get('PORT')\n"
            "n = int(raw)\n"
            "exec(n)\n"
        )
        # int() returns an int; exec(int) would crash anyway. No taint.
        findings = _analyze(src)
        assert findings == []

    def test_re_escape_clears(self) -> None:
        src = (
            "import os, re\n"
            "x = os.environ.get('A')\n"
            "y = re.escape(x)\n"
            "exec(y)\n"
        )
        findings = _analyze(src)
        assert findings == []


# -----------------------------------------------------------------------------
# Function parameters as taint sources (defensive)
# -----------------------------------------------------------------------------


class TestFunctionParams:
    def test_param_to_exec(self) -> None:
        src = "def run(cmd):\n    exec(cmd)\n"
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" and "parameter" in f.source for f in findings)

    def test_param_sanitized(self) -> None:
        src = (
            "import shlex\n"
            "def run(cmd):\n"
            "    safe = shlex.quote(cmd)\n"
            "    import os\n"
            "    os.system(safe)\n"
        )
        findings = _analyze(src)
        assert findings == []


# -----------------------------------------------------------------------------
# Negative cases — should NOT fire
# -----------------------------------------------------------------------------


class TestNegative:
    def test_no_source(self) -> None:
        src = "x = 'hello'\nexec(x)\n"
        # x is a constant, not a source. No taint.
        findings = _analyze(src)
        assert findings == []

    def test_no_sink(self) -> None:
        src = "import os\nx = os.environ.get('X')\nprint(x)\n"
        # print is not a sink
        findings = _analyze(src)
        assert findings == []

    def test_unrelated_var_at_sink(self) -> None:
        src = (
            "import os\n"
            "tainted = os.environ.get('A')\n"
            "clean = 'safe'\n"
            "exec(clean)\n"
        )
        findings = _analyze(src)
        assert findings == []


# -----------------------------------------------------------------------------
# Multi-statement / control flow
# -----------------------------------------------------------------------------


class TestControlFlow:
    def test_inside_if_branch(self) -> None:
        src = (
            "import os\n"
            "if True:\n"
            "    x = os.environ.get('A')\n"
            "    exec(x)\n"
        )
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" for f in findings)

    def test_inside_for_loop(self) -> None:
        src = (
            "import os\n"
            "for i in range(3):\n"
            "    cmd = os.environ.get('CMD')\n"
            "    exec(cmd)\n"
        )
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" for f in findings)

    def test_inside_try_except(self) -> None:
        src = (
            "import os\n"
            "try:\n"
            "    x = os.environ.get('X')\n"
            "    exec(x)\n"
            "except Exception:\n"
            "    pass\n"
        )
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" for f in findings)


# -----------------------------------------------------------------------------
# File / plugin level
# -----------------------------------------------------------------------------


class TestFileLevel:
    def test_analyze_file_returns_findings(self, tmp_path: Path) -> None:
        f = tmp_path / "vuln.py"
        f.write_text("import os\nx = os.environ.get('CMD')\nexec(x)\n")
        findings = analyze_file(f)
        assert any(fd.rule_id == "RC-73" for fd in findings)

    def test_analyze_file_syntax_error(self, tmp_path: Path) -> None:
        f = tmp_path / "broken.py"
        f.write_text("def x(:\n    pass\n")
        assert analyze_file(f) == []

    def test_analyze_file_missing(self, tmp_path: Path) -> None:
        assert analyze_file(tmp_path / "doesnt-exist.py") == []

    def test_analyze_plugin_collects(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text(
            "import os\nx = os.environ.get('A')\nexec(x)\n"
        )
        (tmp_path / "b.py").write_text("def hello():\n    return 1\n")
        result = analyze_plugin(tmp_path)
        assert len(result) == 1
        assert (tmp_path / "a.py") in result

    def test_iter_python_files_skips_node_modules(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "b.py").write_text("")
        files = list(iter_python_files(tmp_path))
        assert tmp_path / "a.py" in files
        assert (tmp_path / "node_modules" / "b.py") not in files

    def test_iter_python_files_skips_dev_folders(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("")
        (tmp_path / "scripts_dev").mkdir()
        (tmp_path / "scripts_dev" / "b.py").write_text("")
        files = list(iter_python_files(tmp_path))
        assert tmp_path / "a.py" in files
        assert (tmp_path / "scripts_dev" / "b.py") not in files


# -----------------------------------------------------------------------------
# Sink line attribution
# -----------------------------------------------------------------------------


class TestLineAttribution:
    def test_line_points_at_sink(self) -> None:
        src = (
            "import os\n"   # 1
            "x = os.environ.get('A')\n"  # 2 (source)
            "y = x\n"        # 3 (hop)
            "exec(y)\n"      # 4 (sink)
        )
        findings = _analyze(src)
        assert findings
        assert findings[0].line == 4
