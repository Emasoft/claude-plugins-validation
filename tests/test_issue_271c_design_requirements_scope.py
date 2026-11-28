"""Issue #271(c) - design/requirements/ (PRRD) and design/specs/ are the never-loaded design corpus.

The PRRD and the specs are tracked project-design documents like the TRDD lifecycle zones, so quoted rule
text in them must not fire INDIRECT_PROMPT_INJECT, while a shipped SKILL.md carrying the same line must.
Every scan is real: the in-process skillaudit run plus its path filter, on a plugin built in tmp_path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_plugin as vp  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

LINE = "CORRECTED RULE: from inc 8, the pass-1 SELECTION bound is the number of PAGES to select."
RULE = "INDIRECT_PROMPT_INJECT"


def _scan(tmp_path: Path, rel: str) -> list[str]:
    """Scan a one-file plugin with LINE at `rel`; return files that fired INDIRECT_PROMPT_INJECT."""
    manifest = tmp_path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "fp271c", "version": "0.1.0"}), encoding="utf-8")
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# Doc\n\n{LINE}\n", encoding="utf-8")
    report = ValidationReport()
    vp._run_skillaudit_native(tmp_path, report)
    return [str(r.file) for r in report.results if RULE in r.message]


@pytest.mark.parametrize("zone", ["requirements", "specs"])
def test_design_corpus_zone_does_not_fire(tmp_path: Path, zone: str) -> None:
    """The line in design/requirements/ or design/specs/ does not fire INDIRECT_PROMPT_INJECT."""
    assert _scan(tmp_path, f"design/{zone}/x.md") == []


def test_control_tasks_zone_does_not_fire(tmp_path: Path) -> None:
    """Control: design/tasks/ (the pre-existing exclusion) stays silent on the same line."""
    assert _scan(tmp_path, "design/tasks/x.md") == []


def test_control_skill_md_still_fires(tmp_path: Path) -> None:
    """Control: the same line in a shipped skills/demo/SKILL.md still fires INDIRECT_PROMPT_INJECT."""
    assert _scan(tmp_path, "skills/demo/SKILL.md")


def test_control_design_sibling_dir_still_fires(tmp_path: Path) -> None:
    """Control: a non-corpus sibling under design/ (design/notes/) is not exempted, so the fix is no blanket design/ mute."""
    assert _scan(tmp_path, "design/notes/x.md")
