#!/usr/bin/env python3
"""Issue #248: RC-164 on a Markdown chmod of the READER's project path.

A shell-fence ``chmod +x .claude/hooks/check.sh`` in a skill reference tells the
reader to mark a file in THEIR project executable. It must stop firing, while
every shape that could aim the same chmod at the plugin tree keeps firing.
"""

from __future__ import annotations

import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_inplugin_write_guard as guard  # noqa: E402

_CHMOD = "chmod"
_DOC = "skills/x/references/guide.md"


def _flag_lines(content: str, rel_path: str, plugin_root: Path) -> list[int]:
    """1-based line numbers the guard flags."""
    return [wf.line_no for wf in guard.inplugin_script_write_findings(content, rel_path, plugin_root)]


def _make_plugin(tmp_path: Path) -> Path:
    """A plugin shipping top-level ``scripts/`` and ``hooks/`` (not ``.claude/``)."""
    root = tmp_path / "myplugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "myplugin", "version": "0.1.0"}')
    (root / "scripts").mkdir()
    (root / "scripts" / "existing.sh").write_text("#!/bin/sh\necho shipped\n")
    (root / "hooks").mkdir()
    (root / "hooks" / "run.sh").write_text("#!/bin/sh\n")
    return root


def _fence(*body: str, lang: str = "bash") -> str:
    return "\n".join([f"```{lang}", *body, "```", ""])


class TestSampleStopsFiring:
    """The issue's sample no longer raises RC-164."""

    @pytest.mark.parametrize("target", [".claude/hooks/check.sh", "./.claude/hooks/check.sh", "project/bin/x.sh"])
    def test_reader_project_path_not_flagged(self, tmp_path: Path, target: str) -> None:
        """A plain shell-fence chmod of an unshipped first segment is not flagged."""
        root = _make_plugin(tmp_path)
        assert _flag_lines(_fence(f"{_CHMOD} +x {target}"), _DOC, root) == []

    def test_plain_mkdir_of_other_path_keeps_narrowing(self, tmp_path: Path) -> None:
        """A plain block with an unrelated mkdir still narrows the foreign chmod."""
        root = _make_plugin(tmp_path)
        doc = _fence("mkdir -p build", f"{_CHMOD} +x .claude/hooks/check.sh")
        assert _flag_lines(doc, _DOC, root) == []


class TestWrappersStillFire:
    """Any sign the fence may change directory or assemble commands keeps the finding."""

    @pytest.mark.parametrize(
        "pre",
        [
            "cd hooks",
            "CD hooks",
            "pushd hooks",
            # `popd` is absent on purpose: with an empty stack the cwd tracker
            # already makes the target unplaceable, so today it is silent too.
            "chdir hooks",
            "builtin cd hooks",
            "env -C hooks true",
            "env --chdir=hooks true",
            "sudo -D hooks true",
            "sudo --chdir hooks true",
            'eval "c""d hooks"',
            'c""d hooks',
            "c\\d hooks",
            "x=$(pwd)",
            "x=`pwd`",
            "(cd hooks)",
            "${CD} hooks",
            "exec bash",
            "source env.sh",
            ". env.sh",
            "alias ls=cd",
            "f() { :; }",
            "function f",
            "cat <<EOF",
            "hooks",
            "setopt autocd",
            "c{d,} hooks",
            "# now cd into hooks",
            "ls; cd hooks",
        ],
    )
    def test_same_fence_wrapper_keeps_finding(self, tmp_path: Path, pre: str) -> None:
        """A directory-changing / assembling line in the fence keeps RC-164."""
        root = _make_plugin(tmp_path)
        doc = _fence(pre, f"{_CHMOD} +x .claude/hooks/check.sh")
        assert _flag_lines(doc, _DOC, root) == [3]

    def test_risky_line_after_chmod_keeps_finding(self, tmp_path: Path) -> None:
        """The flag is per fence, so a risky line AFTER the chmod also counts."""
        root = _make_plugin(tmp_path)
        doc = _fence(f"{_CHMOD} +x .claude/hooks/check.sh", "cd hooks")
        assert _flag_lines(doc, _DOC, root) == [2]

    @pytest.mark.parametrize(
        "line",
        [
            "env -C hooks chmod +x .claude/hooks/check.sh",
            "sudo -D hooks chmod +x .claude/hooks/check.sh",
            "find hooks -execdir chmod +x .claude/hooks/check.sh",
            "chroot hooks chmod +x .claude/hooks/check.sh",
            # A plain first word: only the command-position gate keeps this one.
            "ls chmod +x .claude/hooks/check.sh",
        ],
    )
    def test_same_line_wrapper_keeps_finding(self, tmp_path: Path, line: str) -> None:
        """chmod not in command position at line start keeps RC-164."""
        root = _make_plugin(tmp_path)
        assert _flag_lines(_fence(line), _DOC, root) == [2]

    def test_prose_cd_keeps_finding(self, tmp_path: Path) -> None:
        """A directory change described in prose keeps every chmod in the file."""
        root = _make_plugin(tmp_path)
        doc = "First cd into the hooks folder.\n\n" + _fence(f"{_CHMOD} +x .claude/hooks/check.sh")
        assert _flag_lines(doc, _DOC, root) == [4]

    def test_untagged_fence_unchanged(self, tmp_path: Path) -> None:
        """Only shell-tagged fences are narrowed."""
        root = _make_plugin(tmp_path)
        assert _flag_lines(_fence(f"{_CHMOD} +x .claude/hooks/check.sh", lang=""), _DOC, root) == [2]

    def test_second_operand_keeps_finding(self, tmp_path: Path) -> None:
        """A second chmod operand would ride along unjudged, so the line keeps RC-164."""
        root = _make_plugin(tmp_path)
        doc = _fence(f"{_CHMOD} +x .claude/hooks/check.sh scripts/existing.sh")
        assert _flag_lines(doc, _DOC, root) == [2]

    def test_earlier_fence_cd_keeps_finding(self, tmp_path: Path) -> None:
        """A cd in an EARLIER fence moves the later chmod into the plugin (file-wide flag)."""
        root = _make_plugin(tmp_path)
        (root / "hooks" / "foo").mkdir()
        (root / "hooks" / "foo" / "x.sh").write_text("#!/bin/sh\n")
        doc = _fence("cd hooks") + "\n" + _fence(f"{_CHMOD} +x foo/x.sh")
        assert _flag_lines(doc, _DOC, root) == [6]

    @pytest.mark.parametrize("wrapper", ["env -C hooks true", "sudo --chdir hooks true", "pushd hooks"])
    def test_earlier_fence_wrapper_keeps_finding(self, tmp_path: Path, wrapper: str) -> None:
        """A directory-changing wrapper in another fence keeps every chmod in the file."""
        root = _make_plugin(tmp_path)
        doc = _fence(wrapper) + "\n" + _fence(f"{_CHMOD} +x foo/x.sh")
        assert _flag_lines(doc, _DOC, root) == [6]

    @pytest.mark.parametrize(
        "mention",
        [
            "Run these from the plugin directory.",
            "Paths are relative to ${CLAUDE_PLUGIN_ROOT}.",
            "The PLUGIN_ROOT holds the scripts.",
            "Open a shell in the plugin root first.",
            "Work in the plugin dir.",
        ],
    )
    def test_plugin_root_mention_keeps_finding(self, tmp_path: Path, mention: str) -> None:
        """Any mention of the plugin root anywhere disables the narrowing for the file."""
        root = _make_plugin(tmp_path)
        doc = mention + "\n\n" + _fence(f"{_CHMOD} +x foo/x.sh")
        assert _flag_lines(doc, _DOC, root) == [4]

    def test_plugin_root_mention_after_chmod_keeps_finding(self, tmp_path: Path) -> None:
        """The mention counts even when it appears after the chmod."""
        root = _make_plugin(tmp_path)
        doc = _fence(f"{_CHMOD} +x foo/x.sh") + "\nSee `$CLAUDE_PLUGIN_ROOT` for details.\n"
        assert _flag_lines(doc, _DOC, root) == [2]


class TestTargetShapeStillFires:
    """Shipped, non-literal or created first segments keep the finding."""

    @pytest.mark.parametrize(
        "target",
        [
            "scripts/new.sh",
            "./scripts/new.sh",
            "Scripts/new.sh",
            "HOOKS/x.sh",
            "$CLAUDE_PLUGIN_ROOT/scripts/new.sh",
            '"${CLAUDE_PLUGIN_ROOT}"/scripts/new.sh',
            "'.claude/hooks/check.sh'",
            ".claude/../scripts/new.sh",
            "./../myplugin/scripts/new.sh",
        ],
    )
    def test_target_keeps_finding(self, tmp_path: Path, target: str) -> None:
        """A shipped (case-folded) segment, an anchor, quote residue or `..` fires."""
        root = _make_plugin(tmp_path)
        assert _flag_lines(_fence(f"{_CHMOD} +x {target}"), _DOC, root) == [2]

    @pytest.mark.parametrize("ship_form,doc_form", [("NFC", "NFD"), ("NFD", "NFC")])
    def test_decomposed_accent_target_fires(self, tmp_path: Path, ship_form: str, doc_form: str) -> None:
        """A shipped accented name fires whichever normalization form the doc uses."""
        root = _make_plugin(tmp_path)
        (root / unicodedata.normalize(ship_form, "café")).mkdir()
        target = unicodedata.normalize(doc_form, "café") + "/x.sh"
        assert _flag_lines(_fence(f"{_CHMOD} +x {target}"), _DOC, root) == [2]

    def test_segment_comparison_normalizes(self) -> None:
        """NFC + casefold on both sides: NFD / upper-case spellings match the shipped name."""
        shipped = frozenset({guard._norm_segment(unicodedata.normalize("NFD", "Café"))})
        assert guard._md_segment_shipped(unicodedata.normalize("NFC", "CAFÉ"), shipped)
        assert not guard._md_segment_shipped("cafe", shipped)

    @pytest.mark.parametrize(
        "creator",
        [
            "mkdir -p .claude/hooks",
            "touch .claude/hooks/check.sh",
            "echo hi > .claude/hooks/check.sh",
            "cp a .claude/hooks/check.sh",
            "git clone https://example.invalid/r .claude",
            "curl -o .claude/hooks/check.sh https://example.invalid/x",
            "tar -xf a.tar -C .claude",
        ],
    )
    def test_runtime_created_segment_keeps_finding(self, tmp_path: Path, creator: str) -> None:
        """A first segment the same file creates or writes keeps RC-164 (any fence)."""
        root = _make_plugin(tmp_path)
        doc = _fence(creator, lang="text") + "\n" + _fence(f"{_CHMOD} +x .claude/hooks/check.sh")
        flagged = _flag_lines(doc, _DOC, root)
        assert flagged and flagged[-1] == 6

    def test_quoted_anchor_now_judged_in_shell_script(self, tmp_path: Path) -> None:
        """Part 1: a quote wrapping only the anchor no longer hides the chmod."""
        root = _make_plugin(tmp_path)
        line = f'{_CHMOD} +x "${{CLAUDE_PLUGIN_ROOT}}"/scripts/new.sh\n'
        assert _flag_lines(line, "install.sh", root) == [1]

    def test_existing_quoted_whole_token_unchanged(self, tmp_path: Path) -> None:
        """Part 1 control: a fully quoted in-tree target still fires exactly as before."""
        root = _make_plugin(tmp_path)
        assert _flag_lines(f'{_CHMOD} +x "scripts/existing.sh"\n', "install.sh", root) == [1]

    @pytest.mark.parametrize(
        "tok",
        [
            "'$CLAUDE_PLUGIN_ROOT'/scripts/new.sh",
            "'a\\b'/x.sh",
            "'`pwd`'/x.sh",
        ],
    )
    def test_single_quoted_special_stays_rejected(self, tmp_path: Path, tok: str) -> None:
        """Inside single quotes `$`, backtick and `\\` are literal: the word is not reinterpreted."""
        root = _make_plugin(tmp_path)
        assert _flag_lines(f"{_CHMOD} +x {tok}\n", "install.sh", root) == []

    @pytest.mark.parametrize("tok", ["'scripts'/new.sh", "'${CLAUDE_PLUGIN_ROOT}/scripts/new.sh'"])
    def test_single_quoted_plain_segment_still_judged(self, tmp_path: Path, tok: str) -> None:
        """Control: a plain single-quoted span is judged, and a FULLY single-quoted
        word keeps the verdict it had before #248 (no relaxation)."""
        root = _make_plugin(tmp_path)
        assert _flag_lines(f"{_CHMOD} +x {tok}\n", "install.sh", root) == [1]

    @pytest.mark.parametrize(
        "line",
        [
            'r = run("echo x > .git/hooks/post-commit && chmod +x .git/hooks/post-commit")',
            "r = run('chmod +x .git/hooks/post-commit')",
        ],
    )
    def test_word_ending_inside_outer_string_unchanged(self, tmp_path: Path, line: str) -> None:
        """A chmod word glued to the close of an outer string keeps its pre-#248 verdict."""
        root = _make_plugin(tmp_path)
        assert _flag_lines(line + "\n", "tests/test_x.txt", root) == []

    @pytest.mark.parametrize("tok", ["a:b/x.sh", "C:/x.sh", "a=b/x.sh", "a,b/x.sh", "a#b/x.sh"])
    def test_reader_project_check_rejects_non_literal(self, tmp_path: Path, tok: str) -> None:
        """Direct unit check: a target holding `:` `=` `,` `#` is never narrowed."""
        root = _make_plugin(tmp_path)
        scan = guard._MdChmodScan(frozenset(), False, frozenset())
        line = f"{_CHMOD} +x {tok}"
        assert guard._md_reader_project_chmod(line, tok, 1, scan, set(), root) is False
        ok = guard._md_reader_project_chmod(f"{_CHMOD} +x ab/x.sh", "ab/x.sh", 1, scan, set(), root)
        assert ok is True


class TestScopeOfTheNarrowing:
    """Non-Markdown files and git-tracked entries."""

    @pytest.mark.parametrize("rel", ["install.sh", "hooks/setup", "docs/guide.txt"])
    def test_non_markdown_unchanged(self, tmp_path: Path, rel: str) -> None:
        """The same relative chmod outside Markdown keeps firing."""
        root = _make_plugin(tmp_path)
        assert _flag_lines(f"{_CHMOD} +x .claude/hooks/check.sh\n", rel, root) == [1]

    def test_git_tracked_deleted_entry_counts_as_shipped(self, tmp_path: Path) -> None:
        """A tracked top-level entry missing on disk is still a shipped segment."""
        root = _make_plugin(tmp_path)
        (root / "tools").mkdir()
        (root / "tools" / "a.sh").write_text("#!/bin/sh\n")
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "PATH": "/usr/bin:/bin"}
        subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
        subprocess.run(["git", "-C", str(root), "add", "tools/a.sh"], check=True, env=env)
        (root / "tools" / "a.sh").unlink()
        (root / "tools").rmdir()
        assert _flag_lines(_fence(f"{_CHMOD} +x tools/a.sh"), _DOC, root) == [2]

    def test_inherited_git_env_is_dropped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A caller GIT_DIR must not redirect the ls-files read of the plugin repo."""
        root = _make_plugin(tmp_path)
        (root / "tools").mkdir()
        (root / "tools" / "a.sh").write_text("#!/bin/sh\n")
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "PATH": "/usr/bin:/bin"}
        subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
        subprocess.run(["git", "-C", str(root), "add", "tools/a.sh"], check=True, env=env)
        (root / "tools" / "a.sh").unlink()
        (root / "tools").rmdir()
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "not-a-repo"))
        assert _flag_lines(_fence(f"{_CHMOD} +x tools/a.sh"), _DOC, root) == [2]


def _big_doc(n_blocks: int, risky: bool) -> str:
    """``n_blocks`` fences of 10 lines each, one chmod per fence."""
    body: list[str] = []
    for i in range(n_blocks):
        body.append(f"Step {i} text.")
        body.append("```bash")
        body.extend(["ls -la"] * 6)
        if risky:
            body.append("cd hooks")
        body.append(f"{_CHMOD} +x .claude/hooks/c{i}.sh")
        body.append("```")
    return "\n".join(body) + "\n"


def _best_of_3(content: str, root: Path) -> float:
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        guard.inplugin_script_write_findings(content, _DOC, root)
        best = min(best, time.perf_counter() - t0)
    return best


class TestLinear:
    """The scan grows linearly with the file (the rejected design was quadratic)."""

    @pytest.mark.parametrize("risky", [False, True])
    def test_doubling_the_file_at_most_triples_time(self, tmp_path: Path, risky: bool) -> None:
        """Time at 2N chmod fences is at most ~3x time at N (best of 3)."""
        root = _make_plugin(tmp_path)
        small = _best_of_3(_big_doc(1000, risky), root)
        large = _best_of_3(_big_doc(2000, risky), root)
        assert large <= 3 * small + 0.05, (small, large)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
