"""Regression tests: hidden plugin-root ancestor must not drop the xref scan.

CPV 5.25.0 CI reported ``Reference to non-existent skill '20260421'`` for
``reports_dev/validate_skill/20260421_...`` (word-prefix false positive), and
local strict validation missed it because the release worktree lives under
``.claude/worktrees/`` -- every file was filtered out by a hidden ancestor of
the plugin root. Real files in tmp_path, no mocks.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_xref import (  # noqa: E402
    SKILL_REF_PATTERN,
    CrossReferenceValidationReport,
    validate_skill_refs,
    validate_subagent_type_matching,
)


def _hidden_rooted_plugin(tmp_path: Path) -> Path:
    """Create a minimal plugin whose root sits under a hidden ancestor dir."""
    plugin = tmp_path / ".hidden" / "p"
    (plugin / "agents").mkdir(parents=True)
    (plugin / "skills").mkdir()
    return plugin


def test_skill_ref_under_hidden_plugin_root_is_reported(tmp_path: Path) -> None:
    """A missing skill is reported even when the plugin root has a hidden ancestor."""
    plugin = _hidden_rooted_plugin(tmp_path)
    (plugin / "agents" / "a.md").write_text("# a\nSee skills/missing-x for details.\n", encoding="utf-8")
    report = CrossReferenceValidationReport()
    validate_skill_refs(plugin, report, set())
    majors = [r.message for r in report.results if r.level == "MAJOR"]
    assert any("non-existent skill 'missing-x'" in m for m in majors)


def test_dispatch_ref_under_hidden_plugin_root_is_reported(tmp_path: Path) -> None:
    """A ghost subagent_type is reported even when the plugin root has a hidden ancestor."""
    plugin = _hidden_rooted_plugin(tmp_path)
    (plugin / "agents" / "a.md").write_text('# a\nsubagent_type: "ghost-agent"\n', encoding="utf-8")
    report = CrossReferenceValidationReport()
    validate_subagent_type_matching(plugin, report, {"a"}, plugin_name="p")
    assert any("ghost-agent" in r.message for r in report.results if r.level == "CRITICAL")


def test_hidden_dir_inside_plugin_is_still_skipped(tmp_path: Path) -> None:
    """A hidden directory INSIDE the plugin is still skipped, a visible sibling is not."""
    plugin = _hidden_rooted_plugin(tmp_path)
    (plugin / "agents" / ".cache").mkdir()
    (plugin / "agents" / "sub").mkdir()
    (plugin / "agents" / ".cache" / "x.md").write_text('subagent_type: "ghost-hidden"\n', encoding="utf-8")
    (plugin / "agents" / "sub" / "y.md").write_text('subagent_type: "ghost-visible"\n', encoding="utf-8")
    report = CrossReferenceValidationReport()
    validate_subagent_type_matching(plugin, report, set(), plugin_name="p")
    messages = [r.message for r in report.results if r.level == "CRITICAL"]
    assert any("ghost-visible" in m for m in messages)
    assert not any("ghost-hidden" in m for m in messages)


def test_word_prefixed_skill_dir_is_not_a_skill_ref() -> None:
    """``reports_dev/validate_skill/20260421_...`` yields no skill reference."""
    text = "Report: reports_dev/validate_skill/20260421_1-x.md"
    assert SKILL_REF_PATTERN.findall(text) == []


def test_dot_slash_skill_ref_still_extracted() -> None:
    """``./skills/foo`` still extracts ``foo``."""
    assert SKILL_REF_PATTERN.findall("run ./skills/foo now") == ["foo"]


def test_plugin_root_var_skill_ref_still_extracted() -> None:
    """``${CLAUDE_PLUGIN_ROOT}/skills/foo`` still extracts ``foo``."""
    assert SKILL_REF_PATTERN.findall("${CLAUDE_PLUGIN_ROOT}/skills/foo/run.sh") == ["foo"]


def test_markdown_link_skill_ref_still_extracted() -> None:
    """``(skills/foo/SKILL.md)`` still extracts ``foo``."""
    assert SKILL_REF_PATTERN.findall("[doc](skills/foo/SKILL.md)") == ["foo"]


def test_bare_skill_ref_at_line_start_still_extracted() -> None:
    """A bare ``skills/foo`` at the start of the text still extracts ``foo``."""
    assert SKILL_REF_PATTERN.findall("skills/foo is the entry") == ["foo"]


def test_hyphen_prefixed_skill_dir_is_not_a_skill_ref() -> None:
    """``my-skill/x`` and ``foo-skills/bar`` are other directories, not skill refs."""
    assert SKILL_REF_PATTERN.findall("see my-skill/20260421 and foo-skills/bar") == []


def test_space_prefixed_skills_ref_still_extracted() -> None:
    """A ``skills/bar`` preceded by a space still extracts ``bar``."""
    assert SKILL_REF_PATTERN.findall("see skills/bar now") == ["bar"]
