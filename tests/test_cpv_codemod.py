"""Tests for scripts/cpv_codemod.py — the deterministic codemod CLI (issue #17).

Coverage:
- backtick-to-link: positive transforms + npm-package skip + fenced/indented
  code-block skip + idempotence
- add-toc: insertion above min_lines + skip below threshold + skip if already
  present + GitHub-style slug generation
- dedup-trailing-blanks: collapse triple+ newlines
- wrap-placeholder-paths: wrap unresolved placeholder-shaped paths
- add-standard-sections: insert missing standard headings in SKILL.md only
- external-skip-list: detect vendored subtrees + write to plugin.json
- vendored-subtree skip helper: external/, vendor/, .gitmodules paths
- dry-run vs --apply: dry-run never writes, --apply writes + backs up
- CLI integration: argparse choices, --min-lines flag, exit codes
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_codemod  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_plugin(tmp_path: Path, name: str = "test-plugin") -> Path:
    """Create a minimal plugin root with .claude-plugin/plugin.json."""
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0"}, indent=2),
        encoding="utf-8",
    )
    return root


# ── backtick-to-link ─────────────────────────────────────────────────────────


class TestBacktickToLink:
    """Convert ``path/to/file.md`` in prose to ``[file](path/to/file.md)``."""

    def test_simple_md_path_is_converted(self):
        text = "See `references/foo.md` for details.\n"
        out = cpv_codemod._apply_backtick_to_link(text)
        assert out == "See [foo](references/foo.md) for details.\n"

    def test_dot_slash_prefix_kept_in_link_target(self):
        text = "See `./foo.md` for details.\n"
        out = cpv_codemod._apply_backtick_to_link(text)
        assert "(./foo.md)" in out
        assert "[foo]" in out

    def test_npm_scoped_package_is_skipped(self):
        text = "Install `@google/design.md` somehow.\n"
        out = cpv_codemod._apply_backtick_to_link(text)
        # @scoped/name pattern must be left alone
        assert out == text

    def test_npm_versioned_package_is_skipped(self):
        text = "Use `react@18.3.1` for the demo.\n"
        out = cpv_codemod._apply_backtick_to_link(text)
        assert out == text

    def test_fenced_code_block_is_left_alone(self):
        text = "Some text.\n\n```\nSee `references/foo.md` here.\n```\n\nOutside: `references/bar.md` here.\n"
        out = cpv_codemod._apply_backtick_to_link(text)
        # Inside the fence: untouched
        assert "`references/foo.md`" in out
        # Outside the fence: converted
        assert "[bar](references/bar.md)" in out

    def test_indented_code_block_is_left_alone(self):
        text = "Prose: `references/x.md`\n\n    See `references/y.md` here\n\nMore prose: `references/z.md`\n"
        out = cpv_codemod._apply_backtick_to_link(text)
        assert "[x](references/x.md)" in out
        assert "`references/y.md`" in out  # indented, untouched
        assert "[z](references/z.md)" in out

    def test_idempotent(self):
        text = "See `references/foo.md` for details.\n"
        out1 = cpv_codemod._apply_backtick_to_link(text)
        out2 = cpv_codemod._apply_backtick_to_link(out1)
        assert out1 == out2

    def test_already_linked_paths_left_alone(self):
        text = "See [foo](references/foo.md) for details.\n"
        out = cpv_codemod._apply_backtick_to_link(text)
        assert out == text

    def test_url_in_backticks_skipped(self):
        text = "See `https://example.com/foo.md` for details.\n"
        out = cpv_codemod._apply_backtick_to_link(text)
        assert out == text

    def test_python_path_with_extension_converted(self):
        text = "Edit `scripts/validate.py` to fix.\n"
        out = cpv_codemod._apply_backtick_to_link(text)
        assert "[validate](scripts/validate.py)" in out


# ── add-toc ─────────────────────────────────────────────────────────────────


class TestAddToc:
    """Prepend `## Table of Contents` block from existing `##` headings."""

    def _long_doc(self, n_headings: int = 4, lines_per_section: int = 30) -> str:
        out = ["# Document Title", ""]
        for i in range(n_headings):
            out.append(f"## Section {i + 1}")
            out.extend([f"line {j}" for j in range(lines_per_section)])
            out.append("")
        return "\n".join(out)

    def test_long_file_gets_toc(self):
        text = self._long_doc(4, 30)
        out = cpv_codemod._apply_add_toc(text, min_lines=50)
        assert "## Table of Contents" in out
        assert "[Section 1](#section-1)" in out
        assert "[Section 4](#section-4)" in out

    def test_short_file_skipped(self):
        text = "# Title\n\n## Heading 1\n\n## Heading 2\n\nshort.\n"
        out = cpv_codemod._apply_add_toc(text, min_lines=50)
        assert "## Table of Contents" not in out

    def test_already_has_toc_skipped(self):
        text = (
            "# Title\n\n"
            "## Table of Contents\n\n"
            "- [A](#a)\n\n"
            "## A\n\n" + "\n".join(f"line {i}" for i in range(60)) + "\n"
        )
        out = cpv_codemod._apply_add_toc(text, min_lines=10)
        # Already has TOC: text unchanged (length and content)
        assert out == text

    def test_too_few_headings_skipped(self):
        text = "# Title\n\n## Single Section\n" + ("line\n" * 100)
        out = cpv_codemod._apply_add_toc(text, min_lines=10)
        # Only 1 heading; threshold for TOC insertion is 3
        assert "## Table of Contents" not in out

    def test_idempotent(self):
        text = self._long_doc(4, 30)
        out1 = cpv_codemod._apply_add_toc(text, min_lines=50)
        out2 = cpv_codemod._apply_add_toc(out1, min_lines=50)
        assert out1 == out2

    def test_slug_generation_strips_punctuation(self):
        slug = cpv_codemod._slugify_heading("Hello, World!")
        assert slug == "hello-world"

    def test_inserts_after_h1(self):
        text = self._long_doc(3, 30)
        out = cpv_codemod._apply_add_toc(text, min_lines=10)
        # H1 should still be the first non-empty line
        first_nonblank = next(line for line in out.splitlines() if line.strip())
        assert first_nonblank.startswith("# Document Title")
        # TOC should appear before the first ## Section
        toc_pos = out.find("## Table of Contents")
        first_section_pos = out.find("## Section 1")
        assert 0 < toc_pos < first_section_pos


# ── dedup-trailing-blanks ───────────────────────────────────────────────────


class TestDedupBlanks:
    def test_triple_newlines_collapsed(self):
        text = "para 1\n\n\n\npara 2\n"
        out = cpv_codemod._apply_dedup_blanks(text)
        assert out == "para 1\n\npara 2\n"

    def test_double_newlines_preserved(self):
        text = "para 1\n\npara 2\n"
        out = cpv_codemod._apply_dedup_blanks(text)
        assert out == text

    def test_idempotent(self):
        text = "a\n\n\n\nb\n\n\nc\n"
        out1 = cpv_codemod._apply_dedup_blanks(text)
        out2 = cpv_codemod._apply_dedup_blanks(out1)
        assert out1 == out2


# ── wrap-placeholder-paths ──────────────────────────────────────────────────


class TestWrapPlaceholderPaths:
    def test_unresolved_placeholder_wrapped(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        file_path = plugin_root / "doc.md"
        file_path.write_text("See `${VAR}/file.md` for details.\n", encoding="utf-8")
        out = cpv_codemod._apply_wrap_placeholder_paths(file_path.read_text(encoding="utf-8"), plugin_root, file_path)
        assert "`<${VAR}/file.md>`" in out

    def test_existing_path_not_wrapped(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        (plugin_root / "real.md").write_text("real", encoding="utf-8")
        file_path = plugin_root / "doc.md"
        file_path.write_text("See `real.md` for details.\n", encoding="utf-8")
        out = cpv_codemod._apply_wrap_placeholder_paths(file_path.read_text(encoding="utf-8"), plugin_root, file_path)
        assert "<real.md>" not in out

    def test_already_wrapped_skipped(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        file_path = plugin_root / "doc.md"
        text = "See `<MY_PLACEHOLDER.md>` for details.\n"
        file_path.write_text(text, encoding="utf-8")
        out = cpv_codemod._apply_wrap_placeholder_paths(text, plugin_root, file_path)
        assert out == text


# ── add-standard-sections ───────────────────────────────────────────────────


class TestAddStandardSections:
    def test_missing_sections_appended(self):
        text = "# My Skill\n\nA description.\n"
        out = cpv_codemod._apply_add_standard_sections(text)
        assert "## Overview" in out
        assert "## Examples" in out
        assert "## Output" in out

    def test_existing_sections_not_duplicated(self):
        text = "# My Skill\n\n## Overview\n\nyo.\n\n## Examples\n\nex.\n\n## Output\n\nout.\n"
        out = cpv_codemod._apply_add_standard_sections(text)
        # No new "## Overview" was appended (count must equal 1)
        assert out.count("## Overview") == 1
        assert out.count("## Examples") == 1
        assert out.count("## Output") == 1

    def test_partial_existing_sections_filled(self):
        text = "# My Skill\n\n## Overview\n\nyo.\n"
        out = cpv_codemod._apply_add_standard_sections(text)
        assert "## Overview" in out
        assert "## Examples" in out  # was missing, now appended
        assert "## Output" in out  # was missing, now appended


# ── external-skip-list ──────────────────────────────────────────────────────


class TestExternalSkipList:
    def test_vendored_dirs_added_to_plugin_json(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        (plugin_root / "external").mkdir()
        (plugin_root / "node_modules").mkdir()
        changed, _summary = cpv_codemod._apply_external_skip_list(plugin_root)
        assert changed
        manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        assert "external" in manifest["cpv"]["exclude_paths"]
        assert "node_modules" in manifest["cpv"]["exclude_paths"]

    def test_no_vendored_dirs_returns_false(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        (plugin_root / "src").mkdir()
        changed, summary = cpv_codemod._apply_external_skip_list(plugin_root)
        assert not changed
        assert "No vendored" in summary

    def test_already_excluded_paths_idempotent(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        (plugin_root / "external").mkdir()
        cpv_codemod._apply_external_skip_list(plugin_root)
        # Run again — should report no NEW additions
        changed, summary = cpv_codemod._apply_external_skip_list(plugin_root)
        assert not changed
        assert "already excluded" in summary

    def test_gitmodules_paths_included(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        (plugin_root / ".gitmodules").write_text(
            '[submodule "vendored/lib"]\n  path = vendored/lib\n  url = https://example.com/lib.git\n',
            encoding="utf-8",
        )
        changed, _summary = cpv_codemod._apply_external_skip_list(plugin_root)
        assert changed
        manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        assert "vendored/lib" in manifest["cpv"]["exclude_paths"]


# ── Vendored-subtree skip helper ────────────────────────────────────────────


class TestVendoredSkip:
    def test_external_dir_skipped(self):
        assert cpv_codemod._is_vendored(Path("external/lib/foo.md"), set())

    def test_node_modules_skipped(self):
        assert cpv_codemod._is_vendored(Path("node_modules/x/y.md"), set())

    def test_normal_path_not_skipped(self):
        assert not cpv_codemod._is_vendored(Path("skills/my-skill/SKILL.md"), set())

    def test_gitmodule_path_skipped(self):
        assert cpv_codemod._is_vendored(Path("vendored/lib/x.md"), {"vendored/lib"})

    def test_nested_vendored_dir_skipped(self):
        assert cpv_codemod._is_vendored(Path("skills/my-skill/node_modules/x.md"), set())


# ── End-to-end CLI tests ────────────────────────────────────────────────────


class TestCliEndToEnd:
    def test_dry_run_does_not_write(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        (plugin_root / "doc.md").write_text("See `references/foo.md` for details.\n", encoding="utf-8")
        rc = cpv_codemod.main(["backtick-to-link", "--plugin", str(plugin_root)])
        assert rc == 0
        # File MUST NOT have been written
        assert (plugin_root / "doc.md").read_text(encoding="utf-8") == "See `references/foo.md` for details.\n"
        # No backup directory created
        assert not (plugin_root / ".cpv-codemod-backup").exists()

    def test_apply_writes_file_and_creates_backup(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        original = "See `references/foo.md` for details.\n"
        (plugin_root / "doc.md").write_text(original, encoding="utf-8")
        rc = cpv_codemod.main(["backtick-to-link", "--plugin", str(plugin_root), "--apply"])
        assert rc == 0
        # File has been transformed
        new_text = (plugin_root / "doc.md").read_text(encoding="utf-8")
        assert "[foo](references/foo.md)" in new_text
        # Backup directory exists with the original
        backup_dir = plugin_root / ".cpv-codemod-backup"
        assert backup_dir.is_dir()
        backups = list(backup_dir.rglob("doc.md"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == original

    def test_unknown_subcommand_rejected(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        with pytest.raises(SystemExit):
            cpv_codemod.main(["nonsense", "--plugin", str(plugin_root)])

    def test_invalid_plugin_path_rejected(self, tmp_path):
        rc = cpv_codemod.main(["backtick-to-link", "--plugin", str(tmp_path / "missing")])
        assert rc == 2

    def test_min_lines_threshold_respected(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        short = "# Title\n\n## A\n\n## B\n\n## C\n\nshort doc.\n"
        (plugin_root / "short.md").write_text(short, encoding="utf-8")
        rc = cpv_codemod.main(
            [
                "add-toc",
                "--plugin",
                str(plugin_root),
                "--apply",
                "--min-lines",
                "200",
            ]
        )
        assert rc == 0
        # File too short for the 200-line threshold; unchanged
        assert (plugin_root / "short.md").read_text(encoding="utf-8") == short

    def test_vendored_files_skipped(self, tmp_path):
        plugin_root = _make_plugin(tmp_path)
        (plugin_root / "external").mkdir()
        (plugin_root / "external" / "lib.md").write_text("See `lib/foo.md` here.\n", encoding="utf-8")
        rc = cpv_codemod.main(["backtick-to-link", "--plugin", str(plugin_root), "--apply"])
        assert rc == 0
        # external/ is in the vendored skip list — file untouched
        assert (plugin_root / "external" / "lib.md").read_text(encoding="utf-8") == "See `lib/foo.md` here.\n"
