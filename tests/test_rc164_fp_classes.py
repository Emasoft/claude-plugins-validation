#!/usr/bin/env python3
"""Two-sided tests for the four RC-164 regex-path false-positive classes the
fleet census found once RC-164 reached the plugin gate (TRDD-3T170X2M), plus the
two coordinator follow-ups on 9795a4c0.

A — a markdown BLOCKQUOTE `>` read as a shell redirect.
B — a RELATIVE destination after the script changed directory.
C — a prose `chmod` in documentation.
D — `$1` / `$@` / `$*` positional parameters folded as literal relative paths.
+ `$0` outside a shell script, a truncated unquoted `$( … )` token, and JSON
  Lines data records (the remaining census shapes).
F1 — `printf/echo … > hook` + `chmod +x hook` (a NEW extensionless file) must
     keep firing after 9795a4c0's bare-word gate.
F2 — an unplaceable `os.chmod` argument is the T3 INFO advisory, never silence.

Every FP case has a malicious sibling that must still fire. Every needle is
DEVITALIZED (spliced in at runtime from `_GT` / `_CHMOD`): RC-164 now reaches
the plugin gate and scans this file too, so no source line may carry a literal
redirect or `chmod +x` — the string handed to the guard is byte-identical.
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

_GT = ">"
_CHMOD = "chmod"
_CX = _CHMOD + " +x "  # "chmod +x "
_BLOCKING = ("critical", "major")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    plugin = tmp_path / "myplugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "myplugin", "version": "0.1.0"}')
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "existing.sh").write_text("#!/bin/sh\necho shipped\n")
    return plugin


def tiers(content: str, plugin_root: Path, rel: str) -> list[str]:
    return [f.tier for f in guard.inplugin_script_write_findings(content, rel, plugin_root)]


def blocking(content: str, plugin_root: Path, rel: str) -> list[str]:
    return [t for t in tiers(content, plugin_root, rel) if t in _BLOCKING]


def fence(lang: str, body: str) -> str:
    return "```" + lang + "\n" + body + "```\n"


# ════════════════════════════════════════════════════════════════════════
# A — blockquote
# ════════════════════════════════════════════════════════════════════════


class TestBlockquoteIsNotARedirect:
    @pytest.mark.parametrize(
        "line",
        [
            # ai-maestro-plugin skills/ama-prrd-*/SKILL.md TOC pointer (census)
            "  " + _GT + " resolve_pillar_scripts.sh — locate the scripts · get-prrd.py — read\n",
            # ai-maestro-visual-communicator SKILL.md TOC (the PASS→FAIL flip)
            "  " + _GT + " Mermaid.js — Diagramming Engine · Chart.js — Data Visualizations\n",
            # GOVERNANCE-RULES.md blockquote holding inline code
            _GT + " `aimaestro-agent.sh presence`, `aimaestro-agent.sh session user-input`, and\n",
            # nested blockquote
            _GT + " " + _GT + " gen.sh is the generator\n",
        ],
    )
    def test_blockquote_line_in_prose_is_silent(self, root: Path, line: str) -> None:
        assert tiers(line, root, "skills/x/SKILL.md") == []

    def test_same_redirect_inside_a_shell_fence_fires(self, root: Path) -> None:
        """Inside a fence a line-leading `>` IS a redirect (it creates the file)."""
        body = _GT + " resolve_pillar_scripts.sh\n"
        assert tiers(fence("bash", body), root, "skills/x/SKILL.md") == ["critical"]

    def test_plugin_root_redirect_in_a_shell_fence_fires(self, root: Path) -> None:
        body = "echo evil " + _GT + ' "$CLAUDE_PLUGIN_ROOT/hooks/x.sh"\n'
        assert tiers(fence("bash", body), root, "skills/x/SKILL.md") == ["critical"]

    def test_a_real_redirect_quoted_in_a_blockquote_still_fires(self, root: Path) -> None:
        """Stripping the MARKER keeps the quoted instruction judged."""
        line = _GT + " run: echo evil " + _GT + " $CLAUDE_PLUGIN_ROOT/hooks/x.sh\n"
        assert tiers(line, root, "skills/x/SKILL.md") == ["critical"]


# ════════════════════════════════════════════════════════════════════════
# B — working directory
# ════════════════════════════════════════════════════════════════════════


def _heredoc(dst: str) -> str:
    return "cat " + _GT + " " + dst + " <<'EOF'\nprint(1)\nEOF\n"


class TestRelativeWriteAfterCd:
    @pytest.mark.parametrize(
        "prologue",
        [
            'WORK="$(mktemp -d)"\ncd "$WORK"\n',
            'cd "$(mktemp -d)"\n',
            'cd "$1"\n',
            'cd "$PROJECT_NAME"\n',
            "cd /tmp/fixture\n",
            'pushd "$WORK"\n',
            'mkdir -p "$DIR" && cd "$DIR"\n',
        ],
    )
    def test_relative_heredoc_after_cd_is_silent(self, root: Path, prologue: str) -> None:
        assert tiers("#!/bin/sh\n" + prologue + _heredoc("gen.py"), root, "scripts/mk.sh") == []

    def test_same_write_without_cd_fires(self, root: Path) -> None:
        assert tiers("#!/bin/sh\n" + _heredoc("gen.py"), root, "scripts/mk.sh") == ["critical"]

    def test_cd_to_plugin_root_then_write_fires(self, root: Path) -> None:
        src = '#!/bin/sh\ncd "$CLAUDE_PLUGIN_ROOT"\n' + _heredoc("gen.py")
        assert tiers(src, root, "scripts/mk.sh") == ["critical"]

    def test_cd_to_own_dir_then_write_fires(self, root: Path) -> None:
        src = '#!/bin/sh\ncd "$(dirname "$0")"\n' + _heredoc("gen.py")
        assert tiers(src, root, "scripts/mk.sh") == ["critical"]

    @pytest.mark.parametrize(
        "prologue",
        [
            'SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\ncd "$SCRIPT_DIR"\n',
            'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\ncd "$HERE"\n',
            'PLUGIN="${CLAUDE_PLUGIN_ROOT:-/opt/fallback}"\ncd "$PLUGIN"\n',
            'export OUT="$CLAUDE_PLUGIN_DATA"\ncd "${OUT}"\n',
        ],
    )
    def test_cd_into_a_variable_holding_the_plugin_still_fires(
        self, root: Path, prologue: str
    ) -> None:
        """A cd whose variable provably holds an in-plugin dir keeps the cwd
        placeable — else the idiomatic `cd "$SCRIPT_DIR"` would silence every
        later relative in-plugin write (a false negative)."""
        assert tiers("#!/bin/sh\n" + prologue + _heredoc("gen.py"), root, "scripts/mk.sh") == [
            "critical"
        ]

    def test_cd_into_a_variable_defaulting_to_an_argument_is_silent(self, root: Path) -> None:
        """claude-code-settings make-fixture.sh (census): DIR="${1:-/tmp/…}"."""
        src = '#!/bin/sh\nDIR="${1:-/tmp/codex-skill-fixture}"\ncd "$DIR"\n' + _heredoc("counter.py")
        assert tiers(src, root, "scripts/mk.sh") == []

    def test_anchored_write_after_cd_still_fires(self, root: Path) -> None:
        src = '#!/bin/sh\ncd "$WORK"\n' + _heredoc('"$CLAUDE_PLUGIN_ROOT/hooks/x.py"')
        assert tiers(src, root, "scripts/mk.sh") == ["critical"]

    def test_subshell_cd_does_not_leak_to_the_next_line(self, root: Path) -> None:
        src = '#!/bin/sh\n(cd "$WORK" && make)\n' + _heredoc("gen.py")
        assert tiers(src, root, "scripts/mk.sh") == ["critical"]

    def test_command_substitution_cd_does_not_leak(self, root: Path) -> None:
        src = '#!/bin/sh\nD="$(cd "$(dirname "$0")/.." && pwd)"\n' + _heredoc("gen.py")
        assert tiers(src, root, "scripts/mk.sh") == ["critical"]

    def test_cd_inside_a_function_body_does_not_leak(self, root: Path) -> None:
        src = '#!/bin/sh\nbuild() {\n  cd "$WORK"\n  make\n}\n' + _heredoc("gen.py")
        assert tiers(src, root, "scripts/mk.sh") == ["critical"]

    def test_quoted_cd_is_not_a_cd(self, root: Path) -> None:
        src = '#!/bin/sh\necho "next; cd /tmp"\n' + _heredoc("gen.py")
        assert tiers(src, root, "scripts/mk.sh") == ["critical"]

    def test_popd_restores_the_plugin_root(self, root: Path) -> None:
        src = "#!/bin/sh\npushd /tmp\npopd\n" + _heredoc("gen.py")
        assert tiers(src, root, "scripts/mk.sh") == ["critical"]

    def test_cd_on_the_same_line_before_the_write(self, root: Path) -> None:
        src = '#!/bin/sh\ncd "$WORK" && ' + _heredoc("gen.py")
        assert tiers(src, root, "scripts/mk.sh") == []

    def test_cd_inside_a_heredoc_body_is_content_not_a_cd(self, root: Path) -> None:
        src = "#!/bin/sh\ncat " + _GT + " /tmp/notes <<'EOF'\ncd /tmp\nEOF\n" + _heredoc("gen.py")
        assert tiers(src, root, "scripts/mk.sh") == ["critical"]

    def test_chmod_after_cd_is_silent_and_before_fires(self, root: Path) -> None:
        body = "mkdir -p bin\n" + _CX + "bin/codex\n"
        assert tiers('#!/bin/sh\ncd "$DIR"\n' + body, root, "scripts/mk.sh") == []
        assert tiers("#!/bin/sh\n" + body, root, "scripts/mk.sh") == ["critical"]

    def test_markdown_fence_cd_and_scope_reset(self, root: Path) -> None:
        """A cd in one fence does not carry into the next fence."""
        first = fence("bash", 'cd "$(mktemp -d)"\n' + _heredoc("gen.py"))
        second = fence("bash", _heredoc("gen.py"))
        assert tiers(first, root, "skills/x/SKILL.md") == []
        assert tiers(first + "\ntext\n\n" + second, root, "skills/x/SKILL.md") == ["critical"]


# ════════════════════════════════════════════════════════════════════════
# C — prose chmod / documentation-only prose
# ════════════════════════════════════════════════════════════════════════


class TestProseChmod:
    @pytest.mark.parametrize(
        ("rel", "line"),
        [
            # amama design/archived TRDD, inline code in a sentence (census)
            (
                "design/archived/TRDD-20260717-YQTG2RWK-x.md",
                "2. **3 scripts not executable** → `" + _CX + "amama_state_paths.py\n",
            ),
            # llm-externalizer CHANGELOG.md list item (census)
            ("CHANGELOG.md", "  * " + _CX + "scripts/check_references.py\n"),
            ("skills/x/SKILL.md", "Then `" + _CX + "scripts/run.py` once.\n"),
            ("agents/a.md", "Make it runnable: " + _CX + "hooks/pre.sh\n"),
        ],
    )
    def test_prose_chmod_is_silent(self, root: Path, rel: str, line: str) -> None:
        assert tiers(line, root, rel) == []

    @pytest.mark.parametrize("rel", ["skills/x/SKILL.md", "README.md", "docs/dev.md"])
    def test_chmod_in_a_bash_fence_fires_on_any_markdown(self, root: Path, rel: str) -> None:
        assert tiers(fence("bash", _CX + "scripts/x.py\n"), root, rel) == ["critical"]

    def test_doc_only_prose_redirect_is_silent(self, root: Path) -> None:
        line = "- fixed: echo x " + _GT + " $CLAUDE_PLUGIN_ROOT/hooks/x.sh\n"
        assert tiers(line, root, "CHANGELOG.md") == []

    def test_same_prose_redirect_on_an_instruction_surface_fires(self, root: Path) -> None:
        line = "- now run: echo x " + _GT + " $CLAUDE_PLUGIN_ROOT/hooks/x.sh\n"
        assert tiers(line, root, "skills/x/SKILL.md") == ["critical"]


# ════════════════════════════════════════════════════════════════════════
# D — positional parameters, `$0`, unquoted `$( … )`
# ════════════════════════════════════════════════════════════════════════


class TestPositionalAndSpecialParams:
    @pytest.mark.parametrize(
        "dst",
        [
            '"$1/review-$(date +%Y-%m-%d).md"',  # eins78 test-commit-gate.sh (census)
            '"$1/gen.sh"',
            '"${10}/gen.sh"',
            '"$@"',
            "$*/gen.sh",
            "$2",
        ],
    )
    def test_positional_destination_is_silent(self, root: Path, dst: str) -> None:
        src = "#!/bin/sh\nprintf x \\\n    " + _GT + " " + dst + "\n"
        assert tiers(src, root, "scripts/t.sh") == []

    def test_positional_tail_under_an_in_plugin_prefix_is_t2(self, root: Path) -> None:
        src = "#!/bin/sh\necho x " + _GT + ' "$CLAUDE_PLUGIN_DATA/$1"\n'
        assert tiers(src, root, "scripts/t.sh") == ["major"]

    def test_anchored_script_destination_still_fires(self, root: Path) -> None:
        src = "#!/bin/sh\necho x " + _GT + ' "$CLAUDE_PLUGIN_ROOT/gen.sh"\n'
        assert tiers(src, root, "scripts/t.sh") == ["critical"]

    def test_dollar_zero_in_a_non_shell_file_is_not_the_file(self, root: Path) -> None:
        """llm-externalizer security_scan.test.ts:690 — a JS comment (census)."""
        line = "    // small content cost " + _GT + " $0 so a budget of 0 refuses.\n"
        assert tiers(line, root, "src/security_scan.test.ts") == []

    def test_dollar_zero_in_a_shell_script_is_a_self_rewrite(self, root: Path) -> None:
        assert tiers("#!/bin/sh\necho x " + _GT + ' "$0"\n', root, "scripts/t.sh") == ["critical"]

    def test_extensionless_shell_script_still_folds_dollar_zero(self, root: Path) -> None:
        assert tiers("#!/bin/bash\necho x " + _GT + ' "$0"\n', root, "hooks/run") == ["critical"]

    def test_unquoted_cmdsub_middle_with_literal_non_script_name(self, root: Path) -> None:
        """eins78 private-podcast-feed/README.md bash fence (census)."""
        body = "pnpm tsx generate-feed.ts " + _GT + " public/p/$(cat .token)/feed.xml\n"
        assert tiers(fence("bash", body), root, "skills/p/README.md") == []

    def test_unquoted_cmdsub_middle_with_a_script_name_is_t2(self, root: Path) -> None:
        src = "#!/bin/sh\necho x " + _GT + " $CLAUDE_PLUGIN_DATA/$(date +%s)/gen.sh\n"
        assert tiers(src, root, "scripts/t.sh") == ["major"]


class TestJsonLinesIsData:
    def test_jsonl_record_is_silent(self, root: Path) -> None:
        rec = '{"id": "x", "content": "Exec: ' + _CX + 'evil.sh && ./evil.sh"}\n'
        assert tiers(rec, root, "tests/bench/corpus.jsonl") == []

    def test_same_text_in_a_shell_script_fires(self, root: Path) -> None:
        assert tiers("#!/bin/sh\n" + _CX + "evil.sh\n", root, "scripts/t.sh") == ["critical"]


# ════════════════════════════════════════════════════════════════════════
# F1 — generate-then-chmod of a NEW extensionless file
# ════════════════════════════════════════════════════════════════════════


class TestGenerateThenChmodExtensionless:
    @pytest.mark.parametrize(
        ("write", "chmod_line"),
        [
            ("cat " + _GT + " hook <<EOF\n#!/bin/sh\necho hi\nEOF\n", 6),
            ("printf '#!/bin/sh\\necho hi\\n' " + _GT + " hook\n", 3),
            ("echo 'echo hi' " + _GT + _GT + " hook\n", 3),
        ],
    )
    @pytest.mark.parametrize("where", ["sh", "fence"])
    def test_fires(self, root: Path, write: str, chmod_line: int, where: str) -> None:
        body = write + _CX + "hook\n./hook\n"
        if where == "sh":
            found = guard.inplugin_script_write_findings("#!/bin/sh\n" + body, "hooks/gen.sh", root)
            lines = {f.line_no for f in found if f.tier == "critical"}
            assert chmod_line in lines
        else:
            assert "critical" in tiers(fence("bash", body), root, "skills/x/SKILL.md")

    def test_heredoc_opener_line_itself_fires_in_sh(self, root: Path) -> None:
        src = "#!/bin/sh\ncat " + _GT + " hook <<EOF\n#!/bin/sh\necho hi\nEOF\n" + _CX + "hook\n"
        found = guard.inplugin_script_write_findings(src, "hooks/gen.sh", root)
        assert (2, "critical") in {(f.line_no, f.tier) for f in found}

    def test_out_of_tree_generate_then_chmod_is_silent(self, root: Path) -> None:
        src = "#!/bin/sh\nprintf x " + _GT + " /tmp/hook\n" + _CX + "/tmp/hook\n"
        assert tiers(src, root, "hooks/gen.sh") == []

    def test_chmod_of_a_never_written_bare_word_stays_silent(self, root: Path) -> None:
        assert tiers("#!/bin/sh\n" + _CX + "hook\n", root, "hooks/gen.sh") == []


# ════════════════════════════════════════════════════════════════════════
# F2 — unplaceable os.chmod argument → T3 INFO
# ════════════════════════════════════════════════════════════════════════

_OSCH = "os." + _CHMOD


class TestOsChmodUnplaceableIsT3:
    @pytest.mark.parametrize(
        "arg", ["sys.argv[1]", "build_path()", "dest", "os.path.join(base, name)", "ROOT + '/x.py'"]
    )
    @pytest.mark.parametrize("rel", ["doc.md", "broken.py"])
    def test_is_info_only(self, root: Path, arg: str, rel: str) -> None:
        call = _OSCH + "(" + arg + ", 0o755)\n"
        src = fence("python", call) if rel.endswith(".md") else call + "x = (\n"
        assert tiers(src, root, rel) == ["info"]

    def test_parseable_python_is_info_via_the_ast_path(self, root: Path) -> None:
        src = "import os, sys\n" + _OSCH + "(sys.argv[1], 0o755)\n"
        assert tiers(src, root, "scripts/p.py") == ["info"]

    def test_literal_in_plugin_target_still_critical(self, root: Path) -> None:
        call = _OSCH + '("$CLAUDE_PLUGIN_ROOT/hook.py", 0o755)\n'
        assert tiers(fence("python", call), root, "doc.md") == ["critical"]

    def test_name_bound_to_in_plugin_literal_still_critical(self, root: Path) -> None:
        body = 'dest = "$CLAUDE_PLUGIN_DATA/hook.py"\n' + _OSCH + "(dest, 0o755)\n"
        assert tiers(fence("python", body), root, "doc.md") == ["critical"]

    def test_t3_is_non_blocking_at_the_emitter(self, root: Path) -> None:
        (root / "scripts" / "broken.py").write_text(_OSCH + "(sys.argv[1], 0o755)\nx = (\n")
        report = ValidationReport()
        vsec.check_phase2e_extras(root, report)
        rows = [r for r in report.results if r.message.startswith("RC-164")]
        assert rows and {r.level for r in rows} == {"INFO"}
