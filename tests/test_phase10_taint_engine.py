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
# Augmented-assign attribute source (regression: `x += sys.argv` FN)
# -----------------------------------------------------------------------------


class TestAugAssignAttributeSource:
    """`_process_augassign` must recognize a bare ast.Attribute taint source
    (e.g. `cmd += sys.argv`) exactly as the plain-assign path does — else the
    augmented-assign path silently drops a source `x = sys.argv` would catch."""

    def test_augassign_attribute_source_propagates(self) -> None:
        # `cmd += sys.argv` gains sys.argv taint → os.system(cmd) is a sink.
        src = "import sys, os\ncmd = 'p'\ncmd += sys.argv\nos.system(cmd)\n"
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" and "os.system" in f.sink for f in findings)

    def test_augassign_matches_plain_assign_for_attribute_source(self) -> None:
        # Consistency: the plain-assign and augassign forms must agree.
        plain = _analyze("import sys, os\ncmd = sys.argv\nos.system(cmd)\n")
        aug = _analyze("import sys, os\ncmd = 'p'\ncmd += sys.argv\nos.system(cmd)\n")
        assert plain and aug  # both forms must flag the same sys.argv → os.system flow

    def test_augassign_benign_attribute_no_false_positive(self) -> None:
        # A non-source attribute (`config.value` ∉ TAINT_SOURCES) adds no taint.
        src = "import os\ncmd = 'p'\ncmd += config.value\nos.system(cmd)\n"
        findings = _analyze(src)
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
        src = "import os\nx = os.environ.get('A')\nx = 'safe constant'\nexec(x)\n"
        # Source is overwritten by a non-source non-Name expression → no taint
        findings = _analyze(src)
        assert findings == []


# -----------------------------------------------------------------------------
# RC-75 — sanitizer recognition
# -----------------------------------------------------------------------------


class TestRC75Sanitizer:
    def test_shlex_quote_clears(self) -> None:
        src = "import os, shlex\nx = os.environ.get('A')\ny = shlex.quote(x)\nimport os as os2\nos2.system(y)\n"
        # shlex.quote sanitizes; the os.system call gets a clean string
        findings = _analyze(src)
        assert findings == []

    def test_int_cast_clears(self) -> None:
        src = "import os\nraw = os.environ.get('PORT')\nn = int(raw)\nexec(n)\n"
        # int() returns an int; exec(int) would crash anyway. No taint.
        findings = _analyze(src)
        assert findings == []

    def test_re_escape_clears(self) -> None:
        src = "import os, re\nx = os.environ.get('A')\ny = re.escape(x)\nexec(y)\n"
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
        src = "import shlex\ndef run(cmd):\n    safe = shlex.quote(cmd)\n    import os\n    os.system(safe)\n"
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
        src = "import os\ntainted = os.environ.get('A')\nclean = 'safe'\nexec(clean)\n"
        findings = _analyze(src)
        assert findings == []


# -----------------------------------------------------------------------------
# Multi-statement / control flow
# -----------------------------------------------------------------------------


class TestControlFlow:
    def test_inside_if_branch(self) -> None:
        src = "import os\nif True:\n    x = os.environ.get('A')\n    exec(x)\n"
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" for f in findings)

    def test_inside_for_loop(self) -> None:
        src = "import os\nfor i in range(3):\n    cmd = os.environ.get('CMD')\n    exec(cmd)\n"
        findings = _analyze(src)
        assert any(f.rule_id == "RC-73" for f in findings)

    def test_inside_try_except(self) -> None:
        src = "import os\ntry:\n    x = os.environ.get('X')\n    exec(x)\nexcept Exception:\n    pass\n"
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
        (tmp_path / "a.py").write_text("import os\nx = os.environ.get('A')\nexec(x)\n")
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

    def test_iter_python_files_skips_gitignored_dir(self, tmp_path: Path) -> None:
        """Issue #112: a gitignored dir's .py is not scanned, a tracked .py is."""
        # GitignoreFilter reads <root>/.gitignore directly; no .git dir needed.
        (tmp_path / ".gitignore").write_text("/INPUT_DEV/\n")
        (tmp_path / "shipped.py").write_text("")
        (tmp_path / "INPUT_DEV").mkdir()
        (tmp_path / "INPUT_DEV" / "scratch.py").write_text("")
        files = list(iter_python_files(tmp_path))
        # FP that must CLEAR: the gitignored scratch file is absent.
        assert (tmp_path / "INPUT_DEV" / "scratch.py") not in files
        # Real surface that must STILL be scanned: the tracked shipped file.
        assert tmp_path / "shipped.py" in files

    def test_iter_python_files_skips_uppercase_dev_suffix(self, tmp_path: Path) -> None:
        """Issue #112: the _dev-suffix skip is case-insensitive (FOO_DEV), even untracked."""
        (tmp_path / "a.py").write_text("")
        (tmp_path / "FOO_DEV").mkdir()
        (tmp_path / "FOO_DEV" / "b.py").write_text("")
        files = list(iter_python_files(tmp_path))
        assert tmp_path / "a.py" in files
        assert (tmp_path / "FOO_DEV" / "b.py") not in files


# -----------------------------------------------------------------------------
# Sink line attribution
# -----------------------------------------------------------------------------


class TestLineAttribution:
    def test_line_points_at_sink(self) -> None:
        src = (
            "import os\n"  # 1
            "x = os.environ.get('A')\n"  # 2 (source)
            "y = x\n"  # 3 (hop)
            "exec(y)\n"  # 4 (sink)
        )
        findings = _analyze(src)
        assert findings
        assert findings[0].line == 4


# -----------------------------------------------------------------------------
# RC-73 — yaml.load(..., Loader=<safe loader/subclass>) carve-out (issue #75)
#
# yaml.load with a SafeLoader (or a local subclass that does NOT re-add a
# python/object constructor) is as safe as yaml.safe_load. The carve-out MUST
# clear those, but every unsafe-loader path — bare yaml.load(x), an explicit
# Loader=yaml.Loader|FullLoader|UnsafeLoader, or a SafeLoader subclass that
# re-enables python/object construction, or an unresolvable Loader/tag — MUST
# still fire. MUST CLEAR = no yaml.load(...) finding; MUST FIRE = an RC-73/74
# finding on the yaml.load(...) sink.
# -----------------------------------------------------------------------------


def _yaml_load_fired(findings: list[TaintFinding]) -> bool:
    return any(f.rule_id in ("RC-73", "RC-74") and f.sink == "yaml.load(...)" for f in findings)


def _yaml_load_cleared(findings: list[TaintFinding]) -> bool:
    return not any(f.sink == "yaml.load(...)" for f in findings)


class TestRC73YamlSafeLoader:
    def test_1_reporter_dup_loader_clears(self) -> None:
        # Reporter's exact shape: SafeLoader subclass + module-level
        # add_constructor of the BENIGN mapping tag → safe.
        src = (
            "import yaml\n"
            "class _DupLoader(yaml.SafeLoader):\n"
            "    pass\n"
            "def _cm(loader, node):\n"
            "    return list(loader.construct_pairs(node))\n"
            "_DupLoader.add_constructor(\n"
            "    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _cm,\n"
            ")\n"
            "def parse(raw_text):\n"
            "    return yaml.load(raw_text, Loader=_DupLoader)\n"
        )
        assert _yaml_load_cleared(_analyze(src))

    def test_2_bare_load_fires(self) -> None:
        src = "import yaml\ndef parse(raw_text):\n    return yaml.load(raw_text)\n"
        assert _yaml_load_fired(_analyze(src))

    def test_3_loader_yaml_loader_fires(self) -> None:
        src = "import yaml\ndef parse(raw_text):\n    return yaml.load(raw_text, Loader=yaml.Loader)\n"
        assert _yaml_load_fired(_analyze(src))

    def test_4_subclass_readds_python_object_multi_fires(self) -> None:
        src = (
            "import yaml\n"
            "class _Evil(yaml.SafeLoader):\n"
            "    pass\n"
            "_Evil.add_multi_constructor('tag:yaml.org,2002:python/object/apply:', lambda l, s, n: None)\n"
            "def parse(raw_text):\n"
            "    return yaml.load(raw_text, Loader=_Evil)\n"
        )
        assert _yaml_load_fired(_analyze(src))

    def test_5_subclass_readds_python_object_single_fires(self) -> None:
        src = (
            "import yaml\n"
            "class _Evil(yaml.SafeLoader):\n"
            "    pass\n"
            "_Evil.add_constructor('tag:yaml.org,2002:python/object/apply:os.system', lambda l, n: None)\n"
            "def parse(raw_text):\n"
            "    return yaml.load(raw_text, Loader=_Evil)\n"
        )
        assert _yaml_load_fired(_analyze(src))

    def test_6_loader_full_loader_fires(self) -> None:
        src = "import yaml\ndef parse(raw_text):\n    return yaml.load(raw_text, Loader=yaml.FullLoader)\n"
        assert _yaml_load_fired(_analyze(src))

    def test_7_plain_safe_subclass_no_ctor_clears(self) -> None:
        src = (
            "import yaml\n"
            "class _S(yaml.SafeLoader):\n"
            "    pass\n"
            "def parse(raw_text):\n"
            "    return yaml.load(raw_text, Loader=_S)\n"
        )
        assert _yaml_load_cleared(_analyze(src))

    def test_8_unresolvable_loader_fires(self) -> None:
        # MysteryLoader imported from elsewhere — cannot prove it is safe.
        src = (
            "import yaml\n"
            "from mystery import MysteryLoader\n"
            "def parse(raw_text):\n"
            "    return yaml.load(raw_text, Loader=MysteryLoader)\n"
        )
        assert _yaml_load_fired(_analyze(src))

    def test_9_subclass_readds_python_object_shortform_fires(self) -> None:
        src = (
            "import yaml\n"
            "class _Evil(yaml.SafeLoader):\n"
            "    pass\n"
            "_Evil.add_multi_constructor('!!python/object/apply:', lambda l, s, n: None)\n"
            "def parse(raw_text):\n"
            "    return yaml.load(raw_text, Loader=_Evil)\n"
        )
        assert _yaml_load_fired(_analyze(src))

    def test_10_subclass_benign_literal_ctor_clears(self) -> None:
        src = (
            "import yaml\n"
            "class _S(yaml.SafeLoader):\n"
            "    pass\n"
            "_S.add_constructor('tag:yaml.org,2002:map', lambda l, n: None)\n"
            "def parse(raw_text):\n"
            "    return yaml.load(raw_text, Loader=_S)\n"
        )
        assert _yaml_load_cleared(_analyze(src))

    def test_11_subclass_unresolvable_tag_var_fires(self) -> None:
        # add_constructor's tag is a variable we cannot resolve → conservative fire.
        src = (
            "import yaml\n"
            "SOME_TAG = compute_tag()\n"
            "class _S(yaml.SafeLoader):\n"
            "    pass\n"
            "_S.add_constructor(SOME_TAG, lambda l, n: None)\n"
            "def parse(raw_text):\n"
            "    return yaml.load(raw_text, Loader=_S)\n"
        )
        assert _yaml_load_fired(_analyze(src))

    def test_12_loader_safeloader_directly_clears(self) -> None:
        src = "import yaml\ndef parse(raw_text):\n    return yaml.load(raw_text, Loader=yaml.SafeLoader)\n"
        assert _yaml_load_cleared(_analyze(src))

    def test_13_loader_unsafe_loader_fires(self) -> None:
        src = "import yaml\ndef parse(raw_text):\n    return yaml.load(raw_text, Loader=yaml.UnsafeLoader)\n"
        assert _yaml_load_fired(_analyze(src))
