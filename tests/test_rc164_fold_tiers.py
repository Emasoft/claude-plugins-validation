#!/usr/bin/env python3
"""Two-sided matrix for the RC-164 AST fold + three-tier verdict (TRDD-ETDWX70R).

Every BLOCK case ships a MINIMAL-MUTATION sibling (the anchor hoisted out of
scope, the tail made literal-and-non-script, the destination moved out of tree,
the write turned into a verbatim copy) so each test proves the GATE and not an
incidental difference. The whole suite runs through the REAL public entry point
``inplugin_script_write_findings``; the tier-flip and fold-disabled mutations
prove the assertions are not vacuous.
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
from cpv_write_sink_ast import collect_ast_write_sinks  # noqa: E402

SELF = "scripts/x.py"
SELF_SH = "scripts/install.sh"


# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def root(tmp_path: Path) -> Path:
    plugin = tmp_path / "myplugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "myplugin", "version": "0.1.0"}'
    )
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "existing.py").write_text("print('shipped')\n")
    return plugin


def tiers(content: str, plugin_root: Path, rel: str = SELF) -> list[str]:
    """The TIER of every finding, in emission order."""
    return [f.tier for f in guard.inplugin_script_write_findings(content, rel, plugin_root)]


def findings(content: str, plugin_root: Path, rel: str = SELF):
    return guard.inplugin_script_write_findings(content, rel, plugin_root)


# ════════════════════════════════════════════════════════════════════════
# T1 — the fold places a script INSIDE the tree → critical
# ════════════════════════════════════════════════════════════════════════


class TestT1FileAnchoredCritical:
    def test_file_anchor_parent_literal_script(self, root: Path) -> None:
        """`Path(__file__).parent / "gen.py"` from scripts/x.py → critical."""
        src = 'Path(Path(__file__).parent / "gen.py").write_text(body)\n'
        assert tiers(src, root) == ["critical"]

    def test_parents_two_escapes_the_tree(self, root: Path) -> None:
        """`.parents[2]` from scripts/x.py lands OUTSIDE → no finding.

        FILE_ANCHORED is not INSIDE: the minimal mutation of the case above.
        """
        src = 'Path(Path(__file__).parents[2] / "gen.py").write_text(body)\n'
        assert tiers(src, root) == []

    def test_fstring_literal_suffix_in_tail(self, root: Path) -> None:
        """`f"{stem}.py"` — the stem moved into a variable buys nothing."""
        src = 'out = Path(__file__).parent / f"{stem}.py"\nout.write_text(body)\n'
        assert tiers(src, root) == ["critical"]

    def test_home_data_dir_literal_folds(self, root: Path) -> None:
        """`Path.home()/".claude/plugins/data/<slug>/hook.py"` → critical."""
        src = 'Path.home().joinpath(".claude/plugins/data/myplugin/hook.py").write_text(b)\n'
        assert tiers(src, root) == ["critical"]

    def test_home_outside_the_data_sandbox_is_not_in_tree(self, root: Path) -> None:
        """The same `~` anchor OUTSIDE the data sandbox → no finding."""
        src = 'Path.home().joinpath(".config/hook.py").write_text(b)\n'
        assert tiers(src, root) == []

    def test_var_write_text_dead_script_gate(self, root: Path) -> None:
        """`VAR.write_text(...)` with VAR bound to an in-tree script path.

        The regex capture was the VARIABLE NAME, so the script-gate was dead.
        """
        src = 'VAR = Path(__file__).parent / "hook.py"\nVAR.write_text(body)\n'
        assert tiers(src, root) == ["critical"]

    def test_out_of_tree_literal_is_silent(self, root: Path) -> None:
        """A project-output destination stays ALLOWED (the codegen quirk)."""
        src = 'Path("/tmp/userproject/out.py").write_text(body)\n'
        assert tiers(src, root) == []


class TestT1MethodSuffixSources:
    """Every documented T1 suffix SOURCE must reach the same critical verdict."""

    @pytest.mark.parametrize(
        "src",
        [
            '(Path(__file__).parent / n).with_suffix(".py").write_text(b)\n',
            'p = Path(__file__).parent / n\next = ".py"\np.with_suffix(ext).write_text(b)\n',
            'open(str(Path(__file__).parent / n).replace(".txt", ".py"), "w")\n',
            'open(b"scripts/gen.py", "wb")\n',
            '(Path(__file__).parent / n).with_name("gen.py").write_text(b)\n',
            'EXT = ".sh"\nopen(os.path.join(os.path.dirname(__file__), n) + EXT, "w")\n',
        ],
        ids=["with_suffix", "with_suffix_var", "str_replace", "bytes", "with_name", "join_plus_var"],
    )
    def test_method_suffix_is_critical(self, root: Path, src: str) -> None:
        assert tiers(src, root) == ["critical"], src

    def test_non_script_replacement_is_not_critical(self, root: Path) -> None:
        """The minimal mutation: a NON-script literal replacement → not T1."""
        src = 'open(str(Path(__file__).parent / n).replace(".txt", ".log"), "w")\n'
        assert "critical" not in tiers(src, root)

    def test_with_name_on_unknown_slashless_base_is_info_not_critical(self, root: Path) -> None:
        """``param.with_name("gen.py")`` keeps the unknown receiver as prefix → T3, never T1."""
        src = "def f(p, b):\n    p.with_name('gen.py').write_text(b)\n"
        assert tiers(src, root) == ["info"], src
        src = "Path(cfg['dir']).with_name('gen.py').write_text(b)\n"
        assert tiers(src, root) == ["info"], src

    def test_with_name_on_in_tree_base_still_critical(self, root: Path) -> None:
        """Positive control for the slash-less fix: a foldable receiver stays T1."""
        src = "(Path(__file__).parent / 'x').with_name('gen.py').write_text(b)\n"
        assert tiers(src, root) == ["critical"], src

    def test_str_format_keeps_literal_suffix_as_tail(self, root: Path) -> None:
        """``"{}.py".format(stem)`` — the template's trailing ``.py`` IS the tail → T1."""
        src = '(Path(__file__).parent / "{}.py".format(stem)).write_text(b)\n'
        assert tiers(src, root) == ["critical"], src

    def test_str_format_leading_field_is_not_an_in_tree_prefix(self, root: Path) -> None:
        """``"{}/gen.py".format(d)`` — the field is UNKNOWN, so the prefix never folds in-tree."""
        src = 'open("{}/gen.py".format(d), "w")\n'
        assert tiers(src, root) == ["info"], src

    def test_str_format_all_unknown_yields_nothing(self, root: Path) -> None:
        """Minimal mutation: no literal suffix after the last field → no script evidence."""
        src = 'open("{}/{}".format(d, n), "w")\n'
        assert tiers(src, root) == [], src

    def test_str_format_on_a_bound_template_name_is_critical(self, root: Path) -> None:
        """A NAME bound to the template is the same defect one binding away → still T1."""
        src = 'TPL = "{}.py"\n(Path(__file__).parent / TPL.format(stem)).write_text(b)\n'
        assert tiers(src, root) == ["critical"], src

    def test_str_format_bound_template_leading_field_is_info(self, root: Path) -> None:
        """The bound-template twin of the out-of-tree FP: prefix stays unknown."""
        src = 'TPL = "{}/gen.py"\nopen(TPL.format(d), "w")\n'
        assert tiers(src, root) == ["info"], src

    def test_format_map_reads_the_template_too(self, root: Path) -> None:
        """``format_map`` carries the shape in the same literal → same verdict."""
        src = '(Path(__file__).parent / "{k}.py".format_map(m)).write_text(b)\n'
        assert tiers(src, root) == ["critical"], src

    def test_with_name_on_bare_relative_literal_is_critical(self, root: Path) -> None:
        """The ``with_name`` ELSE-branch: a bare relative receiver folds in-tree → T1."""
        src = 'Path("gen").with_name("gen.py").write_text(b)\n'
        assert tiers(src, root) == ["critical"], src


# ════════════════════════════════════════════════════════════════════════
# T2 — in-plugin PREFIX, unresolved tail → blocking major
# ════════════════════════════════════════════════════════════════════════


class TestT2Unresolved:
    def test_root_anchor_unresolved_tail_is_major(self, root: Path) -> None:
        src = 'out = Path(__file__).parent / cfg["name"]\nout.write_text(body)\n'
        assert tiers(src, root) == ["major"]

    def test_tier_flip_literal_script_tail_becomes_critical(self, root: Path) -> None:
        """Mutation of the case above — proves the TIER logic, not just the fold."""
        src = 'out = Path(__file__).parent / "gen.py"\nout.write_text(body)\n'
        assert tiers(src, root) == ["critical"]

    def test_data_anchor_unresolved_tail_is_also_major(self, root: Path) -> None:
        """DATA is NOT downgraded to warning: a non-blocking DATA tier re-opens
        the #152 staged-daemon hole (the C1 fold scans the in-tree source and is
        sound only if the staged file is a verbatim copy)."""
        src = (
            'out = Path(os.environ["CLAUDE_PLUGIN_DATA"]) / cfg["name"]\n'
            "out.write_text(body)\n"
        )
        assert tiers(src, root) == ["major"]

    def test_data_anchor_literal_non_script_tail_is_silent(self, root: Path) -> None:
        """`DATA / f"{sid}.json"` — a literal NON-script suffix → no finding."""
        src = 'out = Path(os.environ["CLAUDE_PLUGIN_DATA"]) / f"{sid}.json"\nout.write_text(b)\n'
        assert tiers(src, root) == []

    def test_literal_extensionless_tail_keeps_todays_verdict(self, root: Path) -> None:
        """A LITERAL extensionless tail ("daemon") is NOT T2 — shebang/chmod
        evidence is what makes it a script, and there is none here."""
        src = '(Path(__file__).parent / "daemon").write_text("hello")\n'
        assert tiers(src, root) == []

    def test_regex_path_residual_var_in_tail_is_major(self, root: Path) -> None:
        """Shell: `> "$CLAUDE_PLUGIN_DATA/$name"` — today silent, now T2."""
        src = 'echo "$body" > "$CLAUDE_PLUGIN_DATA/$name"\n'
        assert tiers(src, root, SELF_SH) == ["major"]

    def test_regex_path_residual_var_under_foreign_prefix_is_silent(self, root: Path) -> None:
        """Minimal mutation: a NON-plugin prefix → still no finding."""
        src = 'echo "$body" > "$OUT_DIR/$name"\n'
        assert tiers(src, root, SELF_SH) == []


# ════════════════════════════════════════════════════════════════════════
# T3 — unplaceable prefix + script tail → ONE aggregate info per file
# ════════════════════════════════════════════════════════════════════════


class TestT3Aggregate:
    def test_three_sites_yield_one_info_carrying_the_count(self, root: Path) -> None:
        src = (
            'open(build_path(cfg) + ".py", "w")\n'
            'open(build_path(cfg) + ".py", "w")\n'
            'open(build_path(cfg) + ".py", "w")\n'
        )
        out = findings(src, root)
        assert [f.tier for f in out] == ["info"]
        assert "3 script write" in out[0].message
        assert out[0].line_no == 1
        assert "1, 2, 3" in out[0].message

    def test_unknown_prefix_with_unknown_tail_is_silent(self, root: Path) -> None:
        """The FP guard: 5412 such sinks across the census corpus — an INFO on
        those would flood. Minimal mutation of the case above (no `.py` tail)."""
        src = 'open(build_path(cfg), "w")\n' * 3
        assert tiers(src, root) == []


# ════════════════════════════════════════════════════════════════════════
# Copy predicate (verbatim ⇔ the write argument IS a read call)
# ════════════════════════════════════════════════════════════════════════


class TestCopyPredicate:
    def test_direct_read_is_a_copy(self, root: Path) -> None:
        src = 'dst = Path(__file__).parent / "gen.py"\ndst.write_text(src.read_text())\n'
        assert tiers(src, root) == []

    def test_transformed_read_is_not_a_copy(self, root: Path) -> None:
        """`write_text(src.read_text() + payload)` — the advisor-found FN in the
        old regex carve-out (which matched the two method names on one line)."""
        src = 'dst = Path(__file__).parent / "gen.py"\ndst.write_text(src.read_text() + "x")\n'
        assert tiers(src, root) == ["critical"]

    def test_read_bound_to_a_name_is_still_a_copy(self, root: Path) -> None:
        src = (
            "blob = src.read_bytes()\n"
            'dst = Path(__file__).parent / "gen.py"\n'
            "dst.write_bytes(blob)\n"
        )
        assert tiers(src, root) == []

    def test_augassign_poisons_the_copy_binding(self, root: Path) -> None:
        """The copy predicate and the renderer share ONE binding map, so an
        AugAssign makes the name UNKNOWN in both: this is a GENERATE."""
        src = (
            "blob = src.read_bytes()\n"
            'blob += b"x"\n'
            'dst = Path(__file__).parent / "gen.py"\n'
            "dst.write_bytes(blob)\n"
        )
        assert tiers(src, root) == ["critical"]

    def test_shutil_copy_stays_a_copy(self, root: Path) -> None:
        src = 'shutil.copy2(src, Path(__file__).parent / "gen.py")\n'
        assert tiers(src, root) == []


# ════════════════════════════════════════════════════════════════════════
# Shebang + chmod evidence — a SCRIPT regardless of suffix
# ════════════════════════════════════════════════════════════════════════


class TestScriptEvidence:
    def test_shebang_body_makes_an_extensionless_dest_a_script(self, root: Path) -> None:
        src = '(Path(__file__).parent / "daemon").write_text("#!/bin/sh\\necho hi\\n")\n'
        assert tiers(src, root) == ["critical"]

    def test_non_shebang_body_to_the_same_path_is_silent(self, root: Path) -> None:
        src = '(Path(__file__).parent / "daemon").write_text("hello")\n'
        assert tiers(src, root) == []

    def test_unknown_prefix_with_shebang_body_is_info(self, root: Path) -> None:
        src = 'base = compute()\n(base / "daemon").write_text("#!/bin/sh\\n")\n'
        assert tiers(src, root) == ["info"]

    def test_os_chmod_exec_on_an_in_tree_path_is_critical(self, root: Path) -> None:
        src = 'os.chmod(Path(__file__).parent / "daemon", 0o755)\n'
        assert tiers(src, root) == ["critical"]

    def test_os_chmod_without_exec_bits_is_silent(self, root: Path) -> None:
        src = 'os.chmod(Path(__file__).parent / "daemon", 0o644)\n'
        assert tiers(src, root) == []


# ════════════════════════════════════════════════════════════════════════
# Shell self-location fold
# ════════════════════════════════════════════════════════════════════════


class TestShellSelfFold:
    def test_heredoc_dirname_dollar_zero_is_critical(self, root: Path) -> None:
        src = 'cat > "$(dirname "$0")/gen.sh" <<EOF\necho hi\nEOF\n'
        assert tiers(src, root, SELF_SH) == ["critical"]

    def test_redirect_escaping_the_tree_is_silent(self, root: Path) -> None:
        """The minimal mutation: `../../` walks out of the plugin tree."""
        src = 'echo x > "$(dirname "$0")/../../out.sh"\n'
        assert tiers(src, root, SELF_SH) == []

    @pytest.mark.parametrize(
        "anchor",
        [
            '"$(cd "$(dirname "$0")" && pwd)"',
            '"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
            '"$(dirname "${BASH_SOURCE[0]}")"',
        ],
        ids=["cd_pwd", "cd_pwd_bash_source", "dirname_bash_source"],
    )
    def test_every_self_dir_form_folds(self, root: Path, anchor: str) -> None:
        src = f"echo x > {anchor[:-1]}/gen.sh\"\n"
        assert tiers(src, root, SELF_SH) == ["critical"]

    def test_realpath_self_rewrites_this_script(self, root: Path) -> None:
        src = 'echo x > "$(realpath "$0")"\n'
        assert tiers(src, root, SELF_SH) == ["critical"]

    def test_bare_dollar_zero_rewrites_this_script(self, root: Path) -> None:
        src = 'echo x > "$0"\n'
        assert tiers(src, root, SELF_SH) == ["critical"]


# ════════════════════════════════════════════════════════════════════════
# Binding scope rules (the census instrument's own bugs)
# ════════════════════════════════════════════════════════════════════════


class TestBindingScope:
    def test_parameter_shadows_a_module_anchor(self, root: Path) -> None:
        """(i) a PARAMETER named `root` must not resolve to the module value —
        the false FILE_ANCHORED hit the census inspection found."""
        src = (
            "root = Path(__file__).parent\n"
            "def f(root):\n"
            '    (root / "x.py").write_text(b)\n'
        )
        assert tiers(src, root) == ["info"]

    def test_same_body_without_the_parameter_is_critical(self, root: Path) -> None:
        """(i) the minimal mutation — `root` is no longer a parameter."""
        src = (
            "root = Path(__file__).parent\n"
            "def f():\n"
            '    (root / "x.py").write_text(b)\n'
        )
        assert tiers(src, root) == ["critical"]

    def test_a_local_in_one_function_never_reaches_another(self, root: Path) -> None:
        """(ii) the VERIFIED real shape: `root` is local to g, param of f."""
        src = (
            "def g():\n"
            "    root = Path(__file__).resolve().parent.parent\n"
            "    return root\n"
            "def f(root):\n"
            '    (root / "scripts" / "impl.py").write_text(b)\n'
        )
        assert tiers(src, root) == ["info"]

    def test_a_call_result_is_never_followed(self, root: Path) -> None:
        """(iii) `h()` is a Call — never followed."""
        src = (
            "def h():\n"
            "    return Path(__file__).parent\n"
            '(h() / "x.py").write_text(b)\n'
        )
        assert tiers(src, root) == ["info"]

    def test_global_promotion_before_the_writer_def_resolves(self, root: Path) -> None:
        """(iv) `global root` promotes setup()'s assignment to module scope."""
        src = (
            "def setup():\n"
            "    global root\n"
            "    root = Path(__file__).parent\n"
            "def run():\n"
            '    (root / "x.py").write_text(b)\n'
        )
        assert tiers(src, root) == ["critical"]

    def test_global_promotion_after_the_writer_def_does_not(self, root: Path) -> None:
        """(iv) `run` defined BEFORE `setup` → the promotion is not yet visible."""
        src = (
            "def run():\n"
            '    (root / "x.py").write_text(b)\n'
            "def setup():\n"
            "    global root\n"
            "    root = Path(__file__).parent\n"
        )
        assert tiers(src, root) == ["info"]

    def test_a_parameter_beats_a_global_promotion(self, root: Path) -> None:
        """(iv) a param is UNKNOWN always, even with a promoted global present."""
        src = (
            "def setup():\n"
            "    global root\n"
            "    root = Path(__file__).parent\n"
            "def f(root):\n"
            '    (root / "x.py").write_text(b)\n'
        )
        assert tiers(src, root) == ["info"]

    def test_no_global_declaration_keeps_the_binding_local(self, root: Path) -> None:
        """(iv) without `global`, setup()'s `root` never leaves setup."""
        src = (
            "def setup():\n"
            "    root = Path(__file__).parent\n"
            "def run():\n"
            '    (root / "x.py").write_text(b)\n'
        )
        assert tiers(src, root) == ["info"]

    def test_a_for_target_shadows_an_outer_anchor(self, root: Path) -> None:
        src = (
            "root = Path(__file__).parent\n"
            "for root in candidates:\n"
            '    (root / "x.py").write_text(b)\n'
        )
        assert tiers(src, root) == ["info"]


# ════════════════════════════════════════════════════════════════════════
# Line attribution + dispatch + fold-disabled mutation proof
# ════════════════════════════════════════════════════════════════════════


class TestLineAndDispatch:
    def test_line_no_is_the_write_call_line(self, root: Path) -> None:
        """`line_no` is the line carrying `.write_text(`, not the enclosing
        statement's first line (the census mis-attributed a multi-line call)."""
        src = 'p = Path(__file__).parent / "gen.py"\n(\n    p\n).write_text(\n    body\n)\n'
        out = findings(src, root)
        assert [f.line_no for f in out] == [4]
        assert src.split("\n")[3].strip().startswith(").write_text(")

    def test_parsable_py_reports_once_not_twice(self, root: Path) -> None:
        """A `.py` that parses uses the AST path ONLY — no regex double-report."""
        src = 'Path("$CLAUDE_PLUGIN_DATA/scripts/daemon.py").write_text(gen)\n'
        assert tiers(src, root) == ["critical"]

    def test_shell_surface_inside_a_parsable_py_is_still_scanned(self, root: Path) -> None:
        """A `.py` reaching the AST path keeps its SHELL surface scanned.

        The AST walk cannot see a shell command inside a string, so dispatching
        `.py` to the AST path ALONE would drop every heredoc / `sed -i` / `>`
        redirect / `chmod +x` a Python file drives through `os.system` — a
        straight false negative against the pre-TRDD behaviour.
        """
        cases = [
            # A heredoc generating an in-plugin script.
            'SETUP = """\ncat > scripts/gen.sh <<EOF\necho hi\nEOF\n"""\nos.system(SETUP)\n',
            # A redirect generating an in-plugin script.
            'SETUP = """\necho "$body" > scripts/gen.sh\n"""\nos.system(SETUP)\n',
            # `chmod +x` marking an in-plugin path runnable.
            'SETUP = """\nchmod +x scripts/existing.py\n"""\nos.system(SETUP)\n',
            # An in-place edit of a shipped in-plugin script.
            "SETUP = \"\"\"\nsed -i 's/a/b/' scripts/existing.py\n\"\"\"\nos.system(SETUP)\n",
        ]
        for src in cases:
            assert "critical" in tiers(src, root), src

    def test_python_primitives_are_not_double_reported(self, root: Path) -> None:
        """The AST path owns the PYTHON writes — the regex path must not re-emit
        them (that is the whole point of the AST-only dispatch)."""
        src = 'Path("scripts/generated.py").write_text(body)\n'
        out = findings(src, root)
        assert [f.tier for f in out] == ["critical"], out
        assert [f.line_no for f in out] == [1]

    def test_unparsable_py_falls_back_to_the_regex_path(self, root: Path) -> None:
        """A SyntaxError must not silence the file (fail-closed, RC-70 idiom)."""
        src = 'def f(:\nPath("$CLAUDE_PLUGIN_DATA/scripts/daemon.py").write_text(gen)\n'
        assert collect_ast_write_sinks(src, SELF) is None
        assert tiers(src, root) == ["critical"]

    def test_symlinked_plugin_root_gives_the_same_verdicts(
        self, root: Path, tmp_path: Path
    ) -> None:
        """The #227 alias pattern: judge through the symlink, not only the real
        path."""
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        block = 'Path(Path(__file__).parent / "gen.py").write_text(body)\n'
        allow = 'Path(Path(__file__).parents[2] / "gen.py").write_text(body)\n'
        assert tiers(block, alias) == ["critical"]
        assert tiers(allow, alias) == []

    def test_fold_disabled_drops_every_critical(self, root: Path) -> None:
        """MUTATION PROOF: with `self_path=None` the fold cannot place a
        `__file__` anchor, so the critical cases below all stop being critical —
        i.e. the assertions above are earned by the fold, not incidental."""
        cases = [
            'Path(Path(__file__).parent / "gen.py").write_text(body)\n',
            'out = Path(__file__).parent / f"{stem}.py"\nout.write_text(body)\n',
            '(Path(__file__).parent / n).with_name("gen.py").write_text(b)\n',
        ]
        for src in cases:
            assert tiers(src, root) == ["critical"], src
            sinks = collect_ast_write_sinks(src, None)
            assert sinks is not None
            unfolded = guard._ast_path_findings(sinks, SELF, root)
            assert "critical" not in [f.tier for f in unfolded], src


# ════════════════════════════════════════════════════════════════════════
# Markdown fence bounding for the regex-path name lookup
# ════════════════════════════════════════════════════════════════════════


class TestMarkdownFenceBinding:
    def test_name_resolved_within_the_same_fence(self, root: Path) -> None:
        src = (
            "Intro text.\n\n"
            "```python\n"
            'VAR = Path("$CLAUDE_PLUGIN_DATA/scripts/hook.py")\n'
            "VAR.write_text(code)\n"
            "```\n"
        )
        assert tiers(src, root, "docs/guide.md") == ["critical"]

    def test_name_from_a_different_fence_does_not_leak(self, root: Path) -> None:
        """The same VAR can be rebound across fences — the lookup is bounded."""
        src = (
            "```python\n"
            'VAR = Path("$CLAUDE_PLUGIN_DATA/scripts/hook.py")\n'
            "```\n\n"
            "```python\n"
            "VAR.write_text(code)\n"
            "```\n"
        )
        assert tiers(src, root, "docs/guide.md") == []


# ════════════════════════════════════════════════════════════════════════
# validate_security emit — the tier reaches the report at the right severity
# ════════════════════════════════════════════════════════════════════════


def _rc164(plugin_root: Path) -> list[tuple[str, str]]:
    report = ValidationReport()
    vsec.check_phase2e_extras(plugin_root, report)
    return [(r.level, r.message) for r in report.results if "RC-164" in r.message]


class TestSeverityEmit:
    def test_major_tier_emits_a_blocking_major(self, root: Path) -> None:
        (root / "scripts" / "gen.py").write_text(
            'out = Path(__file__).parent / cfg["name"]\nout.write_text(body)\n'
        )
        rows = _rc164(root)
        assert rows, "expected an RC-164 finding"
        assert all(level == "MAJOR" for level, _ in rows), rows
        assert all("[UNRESOLVED]" in msg for _, msg in rows), rows

    def test_info_tier_emits_a_non_blocking_info(self, root: Path) -> None:
        (root / "scripts" / "gen.py").write_text('open(build_path(cfg) + ".py", "w")\n')
        rows = _rc164(root)
        assert rows, "expected an RC-164 finding"
        assert all(level == "INFO" for level, _ in rows), rows

    def test_critical_tier_still_emits_critical(self, root: Path) -> None:
        (root / "scripts" / "gen.py").write_text(
            'Path("$CLAUDE_PLUGIN_DATA/scripts/daemon.py").write_text(gen)\n'
        )
        rows = _rc164(root)
        assert rows, "expected an RC-164 finding"
        assert all(level == "CRITICAL" for level, _ in rows), rows


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
