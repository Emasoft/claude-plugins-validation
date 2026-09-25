"""w9-followups #4 — a freshly generated plugin failed its own `--strict` gate.

``generate_plugin_repo.gen_the_skills_menu_skill`` emitted a Resources section
with the markdown link

    [cpv-the-skills-menu-create](../cpv-the-skills-menu-create/SKILL.md)

pointing at a sibling skill INSIDE this (freshly scaffolded) plugin — but that
skill lives in the separate `claude-plugins-validation` plugin and is never
bundled here (the very next sentence said so). Two checks fired on the exact
same dangling relative path:

* ``validate_documentation.validate_broken_links`` → MAJOR "Broken internal
  link: ..."
* ``cpv_validation_common.validate_md_file_paths`` (the cross-reference
  sweep) → MINOR "Broken file reference: ..."

Reproduced end-to-end before the fix (see the w9-followups report): a fresh
scaffold validated with `remote_validation.py plugin <tmp> --strict` scored
`MAJOR=1 MINOR=1`, both against this one link.

Fixed by dropping the markdown-link syntax entirely — the Resources section
now names the sibling skill and its plugin in plain prose, which is not a
path reference either checker resolves.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_validation_common as common  # noqa: E402
import generate_plugin_repo as gen  # noqa: E402
import validate_documentation as docs  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _scaffold(tmp_path: Path, name: str = "w9-skillsmenu-sample") -> Path:
    params = gen.PluginParams(
        name=name, description="x", author="A", author_email="a@a.a", github_owner="Emasoft",
    )
    target = tmp_path / name
    target.mkdir()
    gen.generate_plugin_repo(target, params)
    return target


def test_emitted_skill_no_longer_carries_a_markdown_link_to_the_sibling_skill() -> None:
    """The fix is dropping the LINK syntax — the plain-prose mention stays."""
    params = gen.PluginParams(
        name="w9-skillsmenu-sample", description="x", author="A", author_email="a@a.a",
    )
    text = gen.gen_the_skills_menu_skill(params)
    assert "](../cpv-the-skills-menu-create" not in text
    # The information is still there, just not as a resolvable relative link.
    assert "cpv-the-skills-menu-create" in text
    assert "claude-plugins-validation" in text


def test_fresh_scaffold_has_no_broken_internal_link(tmp_path: Path) -> None:
    target = _scaffold(tmp_path)
    report = docs.DocumentationValidationReport()
    docs.validate_broken_links(target, report)
    broken = [r for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR")]
    assert broken == [], f"fresh scaffold has broken links: {broken}"


def test_fresh_scaffold_skills_menu_has_no_broken_file_reference(tmp_path: Path) -> None:
    target = _scaffold(tmp_path)
    skill_md = target / "skills" / "cpv-the-skills-menu" / "SKILL.md"
    assert skill_md.is_file()
    report = ValidationReport()
    common.validate_md_file_paths(skill_md, target, report)
    broken = [r for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR")]
    assert broken == [], f"skills menu SKILL.md has a broken file reference: {broken}"
