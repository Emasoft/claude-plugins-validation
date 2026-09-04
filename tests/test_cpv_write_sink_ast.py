"""Two-sided tests for scripts/cpv_write_sink_ast.py (TRDD-ETDWX70R census)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_write_sink_ast import (  # noqa: E402
    _dedupe_cache_roots,
    _run_census,
    classify_python_write_sinks,
    classify_shell_write_sinks,
)


def _one(sinks):
    assert len(sinks) == 1, sinks
    return sinks[0]


def test_literal_open_write_mode_is_literal():
    """open("x.py", "w") is a LITERAL script destination."""
    s = _one(classify_python_write_sinks('open("x.py", "w")\n'))
    assert s.dest_class == "LITERAL"
    assert s.is_script_dest is True


def test_open_read_mode_is_not_a_sink():
    """open("x.py") with no mode (default read) yields no write sink."""
    assert classify_python_write_sinks('open("x.py")\n') == []


def test_open_binary_read_mode_is_not_a_sink():
    """open("x.py", "rb") is a read, not a write."""
    assert classify_python_write_sinks('open("x.py", "rb")\n') == []


def test_path_write_text_literal_script():
    """Path("x.py").write_text(...) is LITERAL + script."""
    s = _one(classify_python_write_sinks('Path("x.py").write_text("hi")\n'))
    assert s.dest_class == "LITERAL"
    assert s.is_script_dest is True


def test_file_anchored_parent_one_hop():
    """Path(__file__).parent / "gen.py" is FILE_ANCHORED, parent_hops=1."""
    s = _one(classify_python_write_sinks('Path(Path(__file__).parent / "gen.py").write_text("x")\n'))
    assert s.dest_class == "FILE_ANCHORED"
    assert s.parent_hops == 1
    assert s.is_script_dest is True


def test_file_anchored_parent_three_hops():
    """Three chained .parent hops above __file__ are counted."""
    s = _one(
        classify_python_write_sinks(
            'Path(Path(__file__).parent.parent.parent / "out.sh").write_text("x")\n'
        )
    )
    assert s.dest_class == "FILE_ANCHORED"
    assert s.parent_hops == 3


def test_file_anchored_join_dirname_unknown_leaf():
    """os.path.join(os.path.dirname(__file__), name) is FILE_ANCHORED, unknown leaf."""
    s = _one(
        classify_python_write_sinks(
            'open(os.path.join(os.path.dirname(__file__), name), "w")\n'
        )
    )
    assert s.dest_class == "FILE_ANCHORED"
    assert s.unknown_leaf is True
    assert s.is_script_dest is None


def test_env_anchored_data_plus_literal_script():
    """os.environ["CLAUDE_PLUGIN_DATA"] + "/hook.sh" is ENV_ANCHORED + script."""
    s = _one(
        classify_python_write_sinks(
            'open(os.environ["CLAUDE_PLUGIN_DATA"] + "/hook.sh", "w")\n'
        )
    )
    assert s.dest_class == "ENV_ANCHORED"
    assert s.is_script_dest is True


def test_unrelated_getenv_plus_literal_script_is_assembled_unknown():
    """os.getenv("HOME") + "/x.py" is ASSEMBLED_UNKNOWN (HOME is not an anchor)."""
    s = _one(classify_python_write_sinks('open(os.getenv("HOME") + "/x.py", "w")\n'))
    assert s.dest_class == "ASSEMBLED_UNKNOWN"


def test_function_call_result_is_assembled_unknown():
    """dst = build_path(cfg); open(dst, "w") is ASSEMBLED_UNKNOWN."""
    src = 'dst = build_path(cfg)\nopen(dst, "w")\n'
    s = _one(classify_python_write_sinks(src))
    assert s.dest_class == "ASSEMBLED_UNKNOWN"


def test_variable_resolution_through_two_assignments():
    """A variable resolved through two hops of assignment reaches its literal."""
    src = 'a = "base.py"\nb = a\nopen(b, "w")\n'
    s = _one(classify_python_write_sinks(src))
    assert s.dest_class == "LITERAL"
    assert s.is_script_dest is True


def test_function_local_shadows_module_global():
    """A function-local assignment of the same name wins inside that function."""
    src = (
        'x = "module.py"\n'
        "def f():\n"
        '    x = "local.sh"\n'
        '    open(x, "w")\n'
    )
    s = _one(classify_python_write_sinks(src))
    assert s.dest_class == "LITERAL"
    # Local assignment (walked after module scope) should win.
    assert s.dest_text == "x"


def test_augassign_marks_name_unknown():
    """A variable rebound via += cannot be resolved (AugAssign -> UNKNOWN)."""
    src = 'x = "a.py"\nx += "b"\nopen(x, "w")\n'
    s = _one(classify_python_write_sinks(src))
    assert s.dest_class == "ASSEMBLED_UNKNOWN"


def test_fstring_with_file_anchor():
    """An f-string embedding __file__ classifies FILE_ANCHORED."""
    s = _one(classify_python_write_sinks('open(f"{__file__}/../out.py", "w")\n'))
    assert s.dest_class == "FILE_ANCHORED"


def test_shutil_copy_is_copy_idiom():
    """shutil.copy(src, dst) is flagged copy_idiom=True."""
    s = _one(classify_python_write_sinks('shutil.copy(src, "dst.py")\n'))
    assert s.copy_idiom is True
    assert s.sink == "shutil.copy"


def test_os_open_write_flags_is_a_sink():
    """os.open(p, os.O_WRONLY|os.O_CREAT) is a write sink."""
    s = _one(classify_python_write_sinks('os.open(p, os.O_WRONLY | os.O_CREAT)\n'))
    assert s.sink == "os.open"


def test_os_open_read_only_flags_is_not_a_sink():
    """os.open(p, os.O_RDONLY) is a read, not a write."""
    assert classify_python_write_sinks("os.open(p, os.O_RDONLY)\n") == []


def test_syntax_error_yields_empty_list():
    """Unparseable source yields [] rather than raising."""
    assert classify_python_write_sinks("def f(:\n") == []


def test_shell_env_redirect_is_env_anchored():
    """echo x > "$CLAUDE_PLUGIN_DATA/h.sh" is ENV_ANCHORED."""
    s = _one(classify_shell_write_sinks('echo x > "$CLAUDE_PLUGIN_DATA/h.sh"\n'))
    assert s.dest_class == "ENV_ANCHORED"
    assert s.is_script_dest is True


def test_shell_bash_source_heredoc_is_file_anchored():
    """A SCRIPT_DIR derived from BASH_SOURCE, used in a heredoc redirect, is FILE_ANCHORED."""
    src = (
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'cat > "$SCRIPT_DIR/gen.py" <<EOF\n'
        "print(1)\n"
        "EOF\n"
    )
    sinks = classify_shell_write_sinks(src)
    heredoc_sinks = [s for s in sinks if s.sink == "shell.heredoc"]
    assert len(heredoc_sinks) == 1
    assert heredoc_sinks[0].dest_class == "FILE_ANCHORED"


def test_shell_unknown_var_redirect_is_assembled_unknown():
    """> "$OUT/x.py" with an unrecognized var is ASSEMBLED_UNKNOWN."""
    s = _one(classify_shell_write_sinks('> "$OUT/x.py"\n'))
    assert s.dest_class == "ASSEMBLED_UNKNOWN"


def test_shell_dev_null_redirect_is_skipped():
    """> /dev/null is not a real write sink."""
    assert classify_shell_write_sinks("cmd > /dev/null\n") == []


def test_shell_stderr_redirect_is_skipped():
    """2> err.log style fd-redirects are excluded from the plain '>' match."""
    # `2>` is excluded by the write-pattern's "not preceded by a digit" rule.
    assert classify_shell_write_sinks("cmd 2> err.log\n") == []


def test_shell_comment_line_is_skipped():
    """A '#'-prefixed line is never scanned as a write sink."""
    assert classify_shell_write_sinks('# > "$CLAUDE_PLUGIN_DATA/x"\n') == []


def test_shell_copy_primitive_is_copy_idiom():
    """cp a.sh "$CLAUDE_PLUGIN_ROOT/b.sh" is copy_idiom=True."""
    s = _one(classify_shell_write_sinks('cp a.sh "$CLAUDE_PLUGIN_ROOT/b.sh"\n'))
    assert s.copy_idiom is True


def test_dedupe_cache_picks_highest_version(tmp_path):
    """--dedupe-cache keeps only the highest-version dir per marketplace/plugin."""
    base = tmp_path / "cache" / "mkt" / "plugin"
    for v in ("1.2.0", "1.10.0", "1.9.3"):
        (base / v).mkdir(parents=True)
    picked = _dedupe_cache_roots([tmp_path / "cache"])
    assert len(picked) == 1
    assert picked[0].name == "1.10.0"


def test_census_walk_and_json_output(tmp_path, capsys):
    """The census CLI walks a tmp tree and writes a JSON report of the expected shape."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "gen.py").write_text('open("out.py", "w")\n', encoding="utf-8")
    out_json = tmp_path / "report.json"

    rc = _run_census(["census", str(plugin), "--json", str(out_json), "--top", "5"])
    assert rc == 0

    captured = capsys.readouterr()
    assert "Files scanned: 1" in captured.out

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert len(payload) == 1
    entry = payload[0]
    assert entry["dest_class"] == "LITERAL"
    assert entry["sink"] == "open"
    assert "file" in entry
