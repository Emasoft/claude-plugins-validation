#!/usr/bin/env python3
"""Two-sided test matrix for the copy-only in-plugin-write guard (issue #152,
``scripts/cpv_inplugin_write_guard.py`` + RC-164 in ``validate_security.py``).

Every BLOCK and ALLOW case in the TRDD (TRDD-Z2HKVTUE) is covered, and each
BLOCK has a sibling ALLOW that is a MINIMAL MUTATION (the in-plugin script
destination swapped for an out-of-tree / non-script / copy / dynamic one) so a
test proves the GUARD'S GATE, not an incidental difference.

Two surfaces are exercised:

* the module-level ``inplugin_script_write_findings`` predicate (unit), and
* the ``validate_security.py`` RC-164 emit through ``check_phase2e_extras`` on a
  real tiny plugin tree under ``tmp_path`` (integration).

THE RULE: a plugin may COPY a shipped, already-scanned script into the plugin
DATA folder, but may NOT GENERATE / EDIT a script that lands INSIDE the plugin
tree (ROOT or DATA). The discriminator is the DESTINATION, not the act:
output written OUTSIDE the plugin is ALLOWED. Lenient fail-safe: a destination
that does not PROVABLY resolve in-tree PASSES.

The skillaudit result cache is keyed on content+catalog+version, NOT validator
code — bypass it so a same-version change is actually exercised.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["CPV_SCAN_CACHE"] = "0"

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_inplugin_write_guard as guard  # noqa: E402
import validate_security as vsec  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────


def _flag_lines(content: str, rel_path: str, plugin_root: Path) -> list[int]:
    """Return the 1-based line numbers the guard flags for ``content``."""
    return [wf.line_no for wf in guard.inplugin_script_write_findings(content, rel_path, plugin_root)]


def _make_plugin(tmp_path: Path) -> Path:
    """A minimal real plugin tree: ``.claude-plugin/plugin.json`` + ``scripts/``
    holding an existing shipped script (so an in-place EDIT resolves a real
    file)."""
    root = tmp_path / "myplugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "myplugin", "version": "0.1.0"}')
    (root / "scripts").mkdir()
    (root / "scripts" / "existing.py").write_text("#!/usr/bin/env python3\nprint('shipped')\n")
    (root / "scripts" / "existing.sh").write_text("#!/bin/sh\necho shipped\n")
    return root


def _rc164_findings(plugin_root: Path) -> list[str]:
    """Run check_phase2e_extras on the plugin tree and return every RC-164
    finding message (any severity — findings live in one ``results`` list)."""
    report = ValidationReport()
    vsec.check_phase2e_extras(plugin_root, report)
    return [r.message for r in report.results if "RC-164" in r.message]


def _rc164_levels(plugin_root: Path) -> list[str]:
    """The severity levels of the RC-164 findings (to assert CRITICAL)."""
    report = ValidationReport()
    vsec.check_phase2e_extras(plugin_root, report)
    return [r.level for r in report.results if "RC-164" in r.message]


def _write_install_file(root: Path, name: str, content: str) -> None:
    """Write an installer file at the plugin root (so the scanner sees it)."""
    (root / name).write_text(content)


# ════════════════════════════════════════════════════════════════════════
# BLOCK cases — a PROVABLE in-plugin SCRIPT generate / edit is flagged
# ════════════════════════════════════════════════════════════════════════


class TestBlockPythonWrites:
    """Python write primitives that generate an in-plugin script → flagged."""

    def test_open_w_py_into_data_literal(self, tmp_path: Path) -> None:
        """open(DATA/x.py, 'w') generating a .py into DATA → flagged."""
        root = _make_plugin(tmp_path)
        c = 'open("$CLAUDE_PLUGIN_DATA/scripts/daemon.py", "w").write(code)\n'
        assert _flag_lines(c, "install.py", root) == [1]

    def test_open_append_py_into_data(self, tmp_path: Path) -> None:
        """open(DATA/x.py, 'a') (append-mutate) of an in-plugin .py → flagged."""
        root = _make_plugin(tmp_path)
        c = 'open("$CLAUDE_PLUGIN_DATA/scripts/d.py", "a").write(extra)\n'
        assert _flag_lines(c, "install.py", root) == [1]

    def test_write_text_py_into_data(self, tmp_path: Path) -> None:
        """Path(DATA/x.py).write_text(code) → flagged."""
        root = _make_plugin(tmp_path)
        c = 'Path("$CLAUDE_PLUGIN_DATA/scripts/daemon.py").write_text(code)\n'
        assert _flag_lines(c, "install.py", root) == [1]

    def test_write_bytes_sh_into_root(self, tmp_path: Path) -> None:
        """Path(ROOT/x.sh).write_bytes(blob) into the plugin ROOT tree → flagged."""
        root = _make_plugin(tmp_path)
        c = 'Path("$CLAUDE_PLUGIN_ROOT/scripts/d.sh").write_bytes(blob)\n'
        assert _flag_lines(c, "install.py", root) == [1]

    def test_write_bytes_generated_not_a_copy(self, tmp_path: Path) -> None:
        """write_bytes fed GENERATED bytes (no ``.read_bytes()`` source) is a
        generate, not a copy → flagged (the copy carve-out is read-fed-only, so
        it must not over-allow a generated write)."""
        root = _make_plugin(tmp_path)
        c = 'Path("$CLAUDE_PLUGIN_DATA/scripts/d.py").write_bytes(generated_blob)\n'
        assert _flag_lines(c, "install.py", root) == [1]

    def test_os_open_py_into_data(self, tmp_path: Path) -> None:
        """os.open(DATA/x.py, O_WRONLY|O_CREAT) → flagged."""
        root = _make_plugin(tmp_path)
        c = 'fd = os.open("$CLAUDE_PLUGIN_DATA/scripts/d.py", os.O_WRONLY | os.O_CREAT)\n'
        assert _flag_lines(c, "install.py", root) == [1]

    def test_relative_in_tree_py(self, tmp_path: Path) -> None:
        """A plugin-root-RELATIVE script destination resolves in-tree → flagged."""
        root = _make_plugin(tmp_path)
        c = 'open("scripts/generated.py", "w").write(code)\n'
        assert _flag_lines(c, "install.py", root) == [1]


class TestBlockEditInPlace:
    """Editing an EXISTING in-plugin script in place → flagged."""

    def test_sed_inplace_edit_existing_sh(self, tmp_path: Path) -> None:
        """sed -i on an existing in-plugin .sh (edit in place) → flagged."""
        root = _make_plugin(tmp_path)
        c = "sed -i 's/old/new/' scripts/existing.sh\n"
        assert _flag_lines(c, "install.sh", root) == [1]

    def test_write_text_edit_existing_py(self, tmp_path: Path) -> None:
        """write_text overwriting an existing in-plugin .py → flagged."""
        root = _make_plugin(tmp_path)
        c = 'Path("scripts/existing.py").write_text(patched_source)\n'
        assert _flag_lines(c, "install.py", root) == [1]


class TestBlockShellWrites:
    """Shell redirect / tee / heredoc writes of an in-plugin script → flagged."""

    def test_redirect_sh_into_data(self, tmp_path: Path) -> None:
        """`> DATA/x.sh` redirect generating a .sh → flagged."""
        root = _make_plugin(tmp_path)
        c = 'echo "$body" > $CLAUDE_PLUGIN_DATA/scripts/d.sh\n'
        assert _flag_lines(c, "install.sh", root) == [1]

    def test_append_py_into_data(self, tmp_path: Path) -> None:
        """`>> DATA/x.py` append generating a .py → flagged."""
        root = _make_plugin(tmp_path)
        c = 'printf "%s" "$body" >> $CLAUDE_PLUGIN_DATA/scripts/d.py\n'
        assert _flag_lines(c, "install.sh", root) == [1]

    def test_tee_sh_into_data(self, tmp_path: Path) -> None:
        """`tee DATA/x.sh` generating a .sh → flagged."""
        root = _make_plugin(tmp_path)
        c = 'echo "$body" | tee $CLAUDE_PLUGIN_DATA/scripts/d.sh\n'
        assert _flag_lines(c, "install.sh", root) == [1]

    def test_heredoc_py_into_data(self, tmp_path: Path) -> None:
        """`cat > DATA/x.py <<EOF` heredoc generating a .py → flagged."""
        root = _make_plugin(tmp_path)
        c = "cat > $CLAUDE_PLUGIN_DATA/scripts/d.py <<EOF\nimport os\nEOF\n"
        assert _flag_lines(c, "install.sh", root) == [1]

    def test_heredoc_shebang_extensionless(self, tmp_path: Path) -> None:
        """A heredoc into an EXTENSION-LESS in-plugin file whose body starts
        with a shebang is a script → flagged (the shebang is the proof)."""
        root = _make_plugin(tmp_path)
        c = "cat > $CLAUDE_PLUGIN_DATA/run <<EOF\n#!/usr/bin/env python3\nprint(1)\nEOF\n"
        assert _flag_lines(c, "install.sh", root) == [1]

    def test_chmod_exec_inplugin_path(self, tmp_path: Path) -> None:
        """chmod +x on an existing in-plugin path marks it runnable → flagged."""
        root = _make_plugin(tmp_path)
        c = "chmod +x scripts/existing.py\n"
        assert _flag_lines(c, "install.sh", root) == [1]


class TestBlockCouplingDaemonGenerated:
    """The #152 fold-gap closure: a daemon staged by GENERATE (not copy) into
    ``data/<slug>/<rest>`` → flagged (what RUNS would not be what was SCANNED)."""

    def test_daemon_generated_into_data_slug_literal(self, tmp_path: Path) -> None:
        """A hard-coded ``~/.claude/plugins/data/<slug>/...`` GENERATE → flagged
        (the literal folds to the plugin root exactly like the env var)."""
        root = _make_plugin(tmp_path)
        c = 'Path("~/.claude/plugins/data/myplugin/scripts/daemon.py").write_text(generated)\n'
        assert _flag_lines(c, "install.py", root) == [1]

    def test_daemon_generated_home_env_data_literal(self, tmp_path: Path) -> None:
        """The ``$HOME``-form data literal GENERATE → flagged."""
        root = _make_plugin(tmp_path)
        c = 'open("$HOME/.claude/plugins/data/x/scripts/d.py", "w").write(src)\n'
        assert _flag_lines(c, "install.py", root) == [1]


# ════════════════════════════════════════════════════════════════════════
# ALLOW cases — each a minimal mutation of a BLOCK sibling
# ════════════════════════════════════════════════════════════════════════


class TestAllowCopy:
    """A verbatim COPY of an in-tree source into the plugin tree → ALLOWED."""

    def test_shutil_copyfile(self, tmp_path: Path) -> None:
        """shutil.copyfile(in_tree_src, data_dst) → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = 'shutil.copyfile("scripts/existing.py", "$CLAUDE_PLUGIN_DATA/scripts/existing.py")\n'
        assert _flag_lines(c, "install.py", root) == []

    def test_shutil_copy2(self, tmp_path: Path) -> None:
        """shutil.copy2 (preserves metadata) → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = 'shutil.copy2("scripts/existing.py", "$CLAUDE_PLUGIN_DATA/scripts/existing.py")\n'
        assert _flag_lines(c, "install.py", root) == []

    def test_shutil_copytree(self, tmp_path: Path) -> None:
        """shutil.copytree of the scripts dir → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = 'shutil.copytree("scripts", "$CLAUDE_PLUGIN_DATA/scripts")\n'
        assert _flag_lines(c, "install.py", root) == []

    def test_shell_cp(self, tmp_path: Path) -> None:
        """shell `cp SRC DATA/x.py` → NOT flagged (verbatim copy)."""
        root = _make_plugin(tmp_path)
        c = "cp scripts/existing.py $CLAUDE_PLUGIN_DATA/scripts/existing.py\n"
        assert _flag_lines(c, "install.sh", root) == []

    def test_shell_install(self, tmp_path: Path) -> None:
        """shell `install -m 755 SRC DATA/x.py` → NOT flagged (verbatim copy)."""
        root = _make_plugin(tmp_path)
        c = "install -m 755 scripts/existing.sh $CLAUDE_PLUGIN_DATA/scripts/existing.sh\n"
        assert _flag_lines(c, "install.sh", root) == []

    def test_write_bytes_read_bytes_copy(self, tmp_path: Path) -> None:
        """``dst.write_bytes(src.read_bytes())`` — bytes fed VERBATIM from a file
        read is a copy into DATA, the user's explicitly-allowed operation → NOT
        flagged."""
        root = _make_plugin(tmp_path)
        c = (
            'Path("$CLAUDE_PLUGIN_DATA/scripts/existing.py")'
            '.write_bytes(Path("scripts/existing.py").read_bytes())\n'
        )
        assert _flag_lines(c, "install.py", root) == []

    def test_write_text_read_text_copy(self, tmp_path: Path) -> None:
        """``dst.write_text(src.read_text())`` — text fed verbatim from a file
        read is a copy → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = (
            'Path("$CLAUDE_PLUGIN_DATA/scripts/existing.py")'
            '.write_text(Path("scripts/existing.py").read_text())\n'
        )
        assert _flag_lines(c, "install.py", root) == []


class TestAllowNonScript:
    """A NON-script write into DATA → ALLOWED (DATA is the writable home)."""

    def test_json_into_data(self, tmp_path: Path) -> None:
        """write_text a .json into DATA → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = 'Path("$CLAUDE_PLUGIN_DATA/state.json").write_text(json_data)\n'
        assert _flag_lines(c, "install.py", root) == []

    def test_cache_into_data(self, tmp_path: Path) -> None:
        """open a .cache into DATA → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = 'open("$CLAUDE_PLUGIN_DATA/index.cache", "wb").write(blob)\n'
        assert _flag_lines(c, "install.py", root) == []

    def test_log_redirect_into_data(self, tmp_path: Path) -> None:
        """`> DATA/x.log` redirect → NOT flagged (not a script)."""
        root = _make_plugin(tmp_path)
        c = 'echo "$line" > $CLAUDE_PLUGIN_DATA/run.log\n'
        assert _flag_lines(c, "install.sh", root) == []

    def test_fd_redirect_log(self, tmp_path: Path) -> None:
        """A `2>` fd-redirect to a .log → NOT flagged (not a write to a script,
        and the digit before `>` excludes the fd-redirect form)."""
        root = _make_plugin(tmp_path)
        c = "run_thing 2> $CLAUDE_PLUGIN_DATA/err.log\n"
        assert _flag_lines(c, "install.sh", root) == []


class TestAllowOutsideTree:
    """A SCRIPT written OUTSIDE the plugin tree → ALLOWED (the plugin's
    legitimate code-generation function — output goes to the user's project)."""

    def test_py_into_project_abs(self, tmp_path: Path) -> None:
        """Generate a .py into an absolute non-plugin path → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = 'Path("/tmp/userproject/out.py").write_text(generated)\n'
        assert _flag_lines(c, "gen.py", root) == []

    def test_py_into_home(self, tmp_path: Path) -> None:
        """Generate a .py into ``~/`` (out of tree) → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = 'open("~/out.py", "w").write(code)\n'
        assert _flag_lines(c, "gen.py", root) == []

    def test_sh_into_cwd_project(self, tmp_path: Path) -> None:
        """`> ./generated.sh` into the user's CWD project (a `./`-prefixed path
        that does not fold to the plugin root) → NOT flagged."""
        root = _make_plugin(tmp_path)
        # `./out.sh` is plugin-root-relative ONLY if run from the plugin; the
        # guard folds a bare relative path to the root, so to model a PROJECT
        # write we use a sibling absolute project dir.
        c = 'echo "$x" > /tmp/proj/generated.sh\n'
        assert _flag_lines(c, "gen.sh", root) == []


class TestAllowDynamic:
    """A dynamic / unresolvable destination → ALLOWED (lenient fail-safe)."""

    def test_open_w_dynamic_var(self, tmp_path: Path) -> None:
        """open(VAR, 'w') with a computed destination var → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = 'open(dest_path, "w").write(code)\n'
        assert _flag_lines(c, "gen.py", root) == []

    def test_write_text_unfoldable_var(self, tmp_path: Path) -> None:
        """A `$SOMEDIR`-anchored destination (not a plugin-root env) → NOT
        flagged (does not provably resolve in-tree)."""
        root = _make_plugin(tmp_path)
        c = 'open("$OUTPUT_DIR/x.py", "w").write(code)\n'
        assert _flag_lines(c, "gen.py", root) == []

    def test_path_join_computed(self, tmp_path: Path) -> None:
        """A computed `os.path.join(target, name)` destination is NEVER blocking.

        TRDD-ETDWX70R: the prefix is unresolvable but the tail IS a script, so
        this is now the advisory T3 tier (one INFO per file) rather than
        silence. It still never blocks — the point of the original assertion.
        """
        root = _make_plugin(tmp_path)
        c = 'open(os.path.join(target_dir, "x.py"), "w").write(code)\n'
        findings = guard.inplugin_script_write_findings(c, "gen.py", root)
        assert [f.tier for f in findings] == ["info"], findings
        assert not [f for f in findings if f.tier in ("critical", "major")]


class TestAllowMisc:
    """Non-write / read / comment lines → never flagged."""

    def test_read_only_open(self, tmp_path: Path) -> None:
        """open(in_tree.py, 'r') (READ) → NOT flagged (no write mode)."""
        root = _make_plugin(tmp_path)
        c = 'data = open("scripts/existing.py", "r").read()\n'
        assert _flag_lines(c, "gen.py", root) == []

    def test_comment_line_with_write(self, tmp_path: Path) -> None:
        """A `#`-comment mentioning a write primitive → NOT flagged."""
        root = _make_plugin(tmp_path)
        c = '# open("scripts/x.py", "w") would generate a script — we do not\n'
        assert _flag_lines(c, "gen.py", root) == []


# ════════════════════════════════════════════════════════════════════════
# Integration — RC-164 emit through validate_security.check_phase2e_extras
# ════════════════════════════════════════════════════════════════════════


class TestRC164Integration:
    """The guard wired into validate_security emits RC-164 on a real tree."""

    def test_rc164_fires_on_generated_daemon(self, tmp_path: Path) -> None:
        """A real installer GENERATING a daemon into DATA → RC-164 finding."""
        root = _make_plugin(tmp_path)
        _write_install_file(
            root,
            "install.py",
            'Path("$CLAUDE_PLUGIN_DATA/scripts/daemon.py").write_text(generated_source)\n',
        )
        msgs = _rc164_findings(root)
        assert any("RC-164" in m for m in msgs), msgs

    def test_rc164_severity_is_critical(self, tmp_path: Path) -> None:
        """RC-164 fires at CRITICAL severity on a real (non-test/doc) installer."""
        root = _make_plugin(tmp_path)
        _write_install_file(
            root,
            "install.py",
            'Path("$CLAUDE_PLUGIN_DATA/scripts/daemon.py").write_text(generated_source)\n',
        )
        levels = _rc164_levels(root)
        assert levels, "expected at least one RC-164 finding"
        assert all(lv == "CRITICAL" for lv in levels), levels

    def test_rc164_silent_on_copy_install(self, tmp_path: Path) -> None:
        """An installer that COPIES the shipped daemon → no RC-164 finding."""
        root = _make_plugin(tmp_path)
        _write_install_file(
            root,
            "install.py",
            'shutil.copyfile("scripts/existing.py", "$CLAUDE_PLUGIN_DATA/scripts/existing.py")\n',
        )
        assert _rc164_findings(root) == []

    def test_rc164_silent_on_clean_plugin(self, tmp_path: Path) -> None:
        """A plugin with no in-plugin script writes → no RC-164 finding."""
        root = _make_plugin(tmp_path)
        _write_install_file(root, "readme.md", "# My plugin\nNothing dangerous here.\n")
        assert _rc164_findings(root) == []

    def test_rc164_silent_on_project_codegen(self, tmp_path: Path) -> None:
        """A code-generation plugin writing scripts to a PROJECT path → no
        RC-164 (the destination is outside the plugin tree)."""
        root = _make_plugin(tmp_path)
        _write_install_file(
            root,
            "codegen.py",
            'Path("/tmp/userproject/generated.py").write_text(code)\n',
        )
        assert _rc164_findings(root) == []


# ════════════════════════════════════════════════════════════════════════
# Self-scan-clean — the guard's own *_PATTERNS literals do not self-flag
# ════════════════════════════════════════════════════════════════════════


class TestSelfScanClean:
    """The guard module's own write-primitive pattern literals are recognised
    as pattern-source (they live in ``*_PATTERNS`` collections), so a self-scan
    of the module itself produces no RC-164 false positive."""

    def test_module_patterns_are_uppercase_collections(self) -> None:
        """Every write-primitive regex bank is an ALL-CAPS ``*_PATTERNS``
        ``Final`` collection so ``is_pattern_source_line`` recognises it."""
        names = [
            "_PY_WRITE_PATTERNS",
            "_SHELL_WRITE_PATTERNS",
            "_HEREDOC_REDIRECT_PATTERNS",
            "_COPY_PRIMITIVE_PATTERNS",
            "_SHEBANG_BODY_PATTERNS",
            "_CHMOD_EXEC_PATTERNS",
        ]
        for n in names:
            assert hasattr(guard, n), n
            assert n.endswith("_PATTERNS")
            assert tuple(getattr(guard, n))  # non-empty collection

    def test_pattern_source_predicate_recognises_collection_lines(self) -> None:
        """The shared ``is_pattern_source_line`` predicate fires on a line that
        is a member of one of the guard's ``*_PATTERNS`` collections — this is
        the mechanism CPV's self-scan uses to skip the module's own literals."""
        from cpv_pattern_source_predicate import is_pattern_source_line

        src = (SCRIPTS_DIR / "cpv_inplugin_write_guard.py").read_text()
        lines = src.split("\n")
        # Find a `re.compile(` line inside the `_PY_WRITE_PATTERNS` collection
        # and assert the predicate treats it as pattern-source.
        in_collection = False
        checked = False
        for i, line in enumerate(lines, start=1):
            if "_PY_WRITE_PATTERNS" in line and line.lstrip().startswith("_PY_WRITE_PATTERNS"):
                in_collection = True
                continue
            if in_collection and "re.compile(" in line:
                assert is_pattern_source_line(src, i, "scripts/cpv_inplugin_write_guard.py"), (
                    f"line {i} not recognised as pattern-source: {line!r}"
                )
                checked = True
                break
        assert checked, "did not locate a re.compile line inside _PY_WRITE_PATTERNS"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
