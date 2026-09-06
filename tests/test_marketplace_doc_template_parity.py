"""Doc-internal consistency checks for the cpv-setup-github-marketplace skill.

These assertions deliberately do NOT read anything under ``templates/`` — that
tree can be mid-edit by a concurrent worker, and a test built on a half-written
file would fail unreliably and look like a doc bug. Instead this checks that
the skill's own reference docs agree with THEMSELVES: the canonical marker
spelling is used everywhere, the old spelling and the retired script name are
gone, the documented workflow stages README.md, and the --check CI gate is
documented.
"""

from __future__ import annotations

from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent / "skills" / "cpv-setup-github-marketplace"


def _all_markdown_files() -> list[Path]:
    return sorted(SKILL_ROOT.rglob("*.md"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_skill_root_exists() -> None:
    """Sanity check: the skill directory this whole test file is about is real."""
    assert SKILL_ROOT.is_dir(), f"missing skill dir: {SKILL_ROOT}"
    assert _all_markdown_files(), "expected at least one markdown file under the skill"


def test_canonical_markers_are_used() -> None:
    """The canonical marker pair must appear somewhere in the skill's docs."""
    combined = "\n".join(_read(p) for p in _all_markdown_files())
    assert "<!-- PLUGIN-VERSIONS-START -->" in combined
    assert "<!-- PLUGIN-VERSIONS-END -->" in combined


def test_old_marker_spelling_is_gone() -> None:
    """The retired PLUGINS_TABLE_START/END spelling must not survive anywhere."""
    for path in _all_markdown_files():
        text = _read(path)
        assert "PLUGINS_TABLE_START" not in text, f"stale marker in {path}"
        assert "PLUGINS_TABLE_END" not in text, f"stale marker in {path}"


def test_generate_readme_script_is_not_documented() -> None:
    """The doc-only generate-readme.py generator must no longer be taught."""
    for path in _all_markdown_files():
        text = _read(path)
        assert "generate-readme.py" not in text, f"stale generator reference in {path}"
        assert "generate_readme.py" not in text, f"stale generator reference in {path}"


def test_render_readme_table_is_documented() -> None:
    """The shipped render_readme_table.py script must be named in the docs."""
    combined = "\n".join(_read(p) for p in _all_markdown_files())
    assert "render_readme_table.py" in combined


def test_workflow_stages_readme_md() -> None:
    """The Update Versions workflow doc must stage README.md alongside marketplace.json.

    This is the exact omission the original bug report was about: only
    marketplace.json was ever staged, so README.md silently went stale.
    """
    workflow_doc = SKILL_ROOT / "references" / "workflow-templates.md"
    text = _read(workflow_doc)
    assert "git add .claude-plugin/marketplace.json README.md" in text
    # And the omission itself must not still be present anywhere in the file.
    assert "git add .claude-plugin/marketplace.json\n" not in text


def test_workflow_regenerates_readme_before_change_check() -> None:
    """render_readme_table.py must run before the 'Check for changes' step.

    Otherwise the diff-check step would run against a stale README and never
    see a table that needs regenerating.
    """
    workflow_doc = SKILL_ROOT / "references" / "workflow-templates.md"
    text = _read(workflow_doc)
    regen_pos = text.find("render_readme_table.py")
    check_pos = text.find("Check for changes")
    assert regen_pos != -1, "render_readme_table.py step not found"
    assert check_pos != -1, "'Check for changes' step not found"
    assert regen_pos < check_pos, "README regeneration must precede the change check"


def test_check_gate_is_documented() -> None:
    """The --check CI gate must be documented in the validate.yml workflow section."""
    workflow_doc = SKILL_ROOT / "references" / "workflow-templates.md"
    text = _read(workflow_doc)
    assert "render_readme_table.py --check" in text
    assert "Verify README table is up to date" in text


def test_skill_md_references_render_readme_table() -> None:
    """SKILL.md's own script list must name the current script, not the retired one."""
    text = _read(SKILL_ROOT / "SKILL.md")
    assert "render_readme_table.py" in text
    assert "generate-readme.py" not in text
