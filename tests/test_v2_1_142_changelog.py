#!/usr/bin/env python3
"""Tests for the Claude Code v2.1.142 changelog catch-up (TRDD-81250f5a).

Four changelog items touch plugin validation:

* Item A — "Plugins with a root-level ``SKILL.md`` and no ``skills/``
  subdirectory are now surfaced as a skill." CPV must auto-discover and
  fully validate that root-level SKILL.md.
* Item B — "Fixed plugins using ``skills: ["./"]`` showing a false 'path
  escapes plugin directory' error." CPV must NOT emit a path-escape error
  for ``"./"`` (it resolves to the plugin root, which is in-bounds).
* Item C — "configuring a prompt- or agent-type hook for
  ``SessionStart``/``Setup``/``SubagentStart`` now shows a clear error."
  CPV must reject prompt/agent hooks for all three events.
* Item D — "Fixed plugin advisories not naming every ``plugin.json`` key
  that shadows a default folder." CPV must name every shadowing key.

Items B/C/D were already enforced correctly before v2.1.142; the tests
here are regression locks. Item A is the genuinely-new behaviour.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import HookValidationReport, validate_single_hook  # noqa: E402
from validate_plugin import (  # noqa: E402
    _discover_plugin_skills,
    validate_manifest,
    validate_manifest_skill_paths,
    validate_skills,
    validate_structure,
)

# A well-formed skill: third-person description, specific gerund name, body
# with steps. Passes the comprehensive validator with no CRITICAL.
_GOOD_SKILL_MD = """---
name: extracting-pdf-tables
description: Use this skill when the user needs to extract tabular data from PDF documents. It parses page layouts, detects table boundaries, and emits clean CSV for downstream analysis.
---

# Extracting PDF tables

This skill extracts tables from PDF files.

## Steps

1. Open the PDF and locate the pages that contain tabular layouts.
2. Detect the column and row boundaries on each page.
3. Emit the reconstructed table as CSV output.
"""


def _make_plugin(
    tmp_path: Path,
    manifest: dict,
    *,
    dir_name: str = "test-plugin",
    root_skill_md: str | None = None,
    skills: dict[str, str] | None = None,
    commands: list[str] | None = None,
    agents: list[str] | None = None,
    output_styles: list[str] | None = None,
) -> Path:
    """Lay down a plugin tree with the named manifest and optional content."""
    root = tmp_path / dir_name
    root.mkdir(parents=True, exist_ok=True)
    cp = root / ".claude-plugin"
    cp.mkdir(exist_ok=True)
    (cp / "plugin.json").write_text(json.dumps(manifest, indent=2))
    if root_skill_md is not None:
        (root / "SKILL.md").write_text(root_skill_md)
    if skills:
        sdir = root / "skills"
        sdir.mkdir(exist_ok=True)
        for name, content in skills.items():
            d = sdir / name
            d.mkdir(exist_ok=True)
            (d / "SKILL.md").write_text(content)
    if commands:
        d = root / "commands"
        d.mkdir(exist_ok=True)
        for n in commands:
            (d / n).write_text("# placeholder command\n")
    if agents:
        d = root / "agents"
        d.mkdir(exist_ok=True)
        for n in agents:
            (d / n).write_text(f"---\nname: {n[:-3]}\ndescription: An agent.\n---\nbody\n")
    if output_styles:
        d = root / "output-styles"
        d.mkdir(exist_ok=True)
        for n in output_styles:
            (d / n).write_text("style\n")
    return root


def _find(report: ValidationReport, level: str, needle: str) -> list:
    """Return findings at `level` whose message contains `needle`."""
    return [f for f in report.results if f.level == level and needle in f.message]


def _has(report: ValidationReport, level: str, needle: str) -> bool:
    """True if any finding at `level` contains `needle`."""
    return bool(_find(report, level, needle))


def _has_shadow_finding(report: ValidationReport, key: str) -> bool:
    """True if a default-folder shadow MAJOR names `key`."""
    needle = f"Field '{key}' is set in plugin.json"
    return any(f.level == "MAJOR" and needle in f.message and "silently ignores" in f.message for f in report.results)


# ---------------------------------------------------------------------------
# Item A — root-level SKILL.md surfaced as a skill (CC v2.1.142, genuinely new)
# ---------------------------------------------------------------------------


def test_root_level_skill_md_is_surfaced_when_no_skills_dir(tmp_path):
    """A root-level SKILL.md with no skills/ subdir is surfaced and reported as a skill."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "description": "A test plugin."},
        root_skill_md=_GOOD_SKILL_MD,
    )
    report = ValidationReport()
    validate_skills(root, report)
    assert _has(report, "INFO", "Root-level SKILL.md found"), [(f.level, f.message) for f in report.results]
    # The stale "No skills/ directory found" message must NOT be emitted —
    # the plugin DOES have a skill, just at the root.
    assert not _has(report, "INFO", "No skills/ directory found")


def test_root_level_skill_md_defect_is_caught(tmp_path):
    """A defect in a root-level SKILL.md is caught — the comprehensive validator runs on it."""
    # description as an int → comprehensive validator emits a MAJOR.
    bad_skill = "---\nname: extracting-pdf-tables\ndescription: 123\n---\n\n# Body\n\ntext\n"
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "description": "A test plugin."},
        root_skill_md=bad_skill,
    )
    report = ValidationReport()
    validate_skills(root, report)
    assert _has(report, "MAJOR", "'description' must be a string"), [(f.level, f.message) for f in report.results]


def test_root_level_skill_md_good_skill_has_no_critical(tmp_path):
    """A well-formed root-level SKILL.md produces no CRITICAL findings."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "description": "A test plugin."},
        root_skill_md=_GOOD_SKILL_MD,
    )
    report = ValidationReport()
    validate_skills(root, report)
    criticals = [f for f in report.results if f.level == "CRITICAL"]
    assert criticals == [], [(f.level, f.message) for f in criticals]


def test_root_level_skill_md_no_directory_name_mismatch(tmp_path):
    """A root-level SKILL.md is NOT flagged for a name/directory mismatch (its dir IS the plugin root)."""
    # Plugin directory name deliberately differs from the skill's frontmatter name.
    root = _make_plugin(
        tmp_path,
        {"name": "unrelated-plugin-dir", "version": "0.1.0", "description": "A test plugin."},
        dir_name="unrelated-plugin-dir",
        root_skill_md=_GOOD_SKILL_MD,
    )
    report = ValidationReport()
    validate_skills(root, report)
    # skip_dir_name_check=True suppresses the would-be MAJOR.
    assert not _has(report, "MAJOR", "must match directory name"), [(f.level, f.message) for f in report.results]


def test_plugin_with_only_root_skill_md_is_not_flagged_no_content(tmp_path):
    """A plugin whose only content is a root-level SKILL.md is NOT flagged 'manifest but no content'."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "description": "A test plugin."},
        root_skill_md=_GOOD_SKILL_MD,
    )
    report = ValidationReport()
    validate_structure(root, report)
    assert not _has(report, "MAJOR", "manifest but no content"), [(f.level, f.message) for f in report.results]


def test_root_skill_md_alongside_skills_dir_emits_minor(tmp_path):
    """A root-level SKILL.md is dead weight when skills/ also exists — CPV emits a MINOR."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "description": "A test plugin."},
        root_skill_md=_GOOD_SKILL_MD,
        skills={"extracting-pdf-tables": _GOOD_SKILL_MD},
    )
    report = ValidationReport()
    validate_skills(root, report)
    assert _has(report, "MINOR", "Root-level SKILL.md will NOT load"), [(f.level, f.message) for f in report.results]


def test_discover_plugin_skills_includes_root_level_skill(tmp_path):
    """_discover_plugin_skills returns the root-level skill's frontmatter name when there is no skills/ dir."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "description": "A test plugin."},
        root_skill_md=_GOOD_SKILL_MD,
    )
    assert _discover_plugin_skills(root) == {"extracting-pdf-tables"}


def test_discover_plugin_skills_ignores_root_skill_when_skills_dir_exists(tmp_path):
    """When skills/ exists, _discover_plugin_skills uses skills/ subdirs and ignores the root SKILL.md."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "description": "A test plugin."},
        root_skill_md=_GOOD_SKILL_MD,
        skills={"managing-deps": _GOOD_SKILL_MD},
    )
    # skills/ subdir wins; the root-level SKILL.md is not surfaced.
    assert _discover_plugin_skills(root) == {"managing-deps"}


# ---------------------------------------------------------------------------
# Item B — skills: ["./"] is valid, no false path-escape error (CC v2.1.142)
# ---------------------------------------------------------------------------


def test_skills_self_pointing_path_does_not_escape_plugin_root(tmp_path):
    """plugin.json skills: ["./"] with a root-level SKILL.md emits no path-escape error."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "description": "A test plugin.", "skills": ["./"]},
        root_skill_md=_GOOD_SKILL_MD,
    )
    report = ValidationReport()
    declared = validate_manifest_skill_paths(root, report)
    assert declared is True
    # "./" resolves to the plugin root — in-bounds, no escape error.
    assert not _has(report, "MAJOR", "escapes the plugin root"), [(f.level, f.message) for f in report.results]
    assert not _find(report, "MAJOR", "skills[0]")


def test_skills_self_pointing_path_without_root_skill_md_emits_major(tmp_path):
    """plugin.json skills: ["./"] with NO root SKILL.md still flags the missing file (not an escape error)."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "description": "A test plugin.", "skills": ["./"]},
    )
    report = ValidationReport()
    validate_manifest_skill_paths(root, report)
    # The path-list check still works — it just reports the real defect
    # (no SKILL.md), never a bogus path-escape.
    assert _has(report, "MAJOR", "contains no SKILL.md"), [(f.level, f.message) for f in report.results]
    assert not _has(report, "MAJOR", "escapes the plugin root")


# ---------------------------------------------------------------------------
# Item C — prompt/agent hooks rejected for SessionStart/Setup/SubagentStart
# ---------------------------------------------------------------------------


def _has_type_rejection(report: HookValidationReport, event: str) -> bool:
    """True if a CRITICAL rejects the hook type for `event`."""
    for f in report.results:
        if f.level == "CRITICAL" and event in f.message and "only supports" in f.message:
            return True
    return False


def test_subagent_start_prompt_hook_rejected():
    """A prompt-type hook configured for SubagentStart is rejected with a CRITICAL."""
    report = HookValidationReport(hook_path="test-hook")
    validate_single_hook({"type": "prompt", "prompt": "Summarize the task"}, "SubagentStart", None, report)
    assert _has_type_rejection(report, "SubagentStart")


def test_subagent_start_agent_hook_rejected():
    """An agent-type hook configured for SubagentStart is rejected with a CRITICAL."""
    report = HookValidationReport(hook_path="test-hook")
    validate_single_hook({"type": "agent", "agent": "explore"}, "SubagentStart", None, report)
    assert _has_type_rejection(report, "SubagentStart")


def test_subagent_start_command_hook_accepted():
    """A command-type hook for SubagentStart is NOT rejected on hook-type grounds."""
    report = HookValidationReport(hook_path="test-hook")
    validate_single_hook({"type": "command", "command": "echo hi"}, "SubagentStart", None, report)
    assert not _has_type_rejection(report, "SubagentStart")


def test_setup_prompt_hook_rejected():
    """A prompt-type hook configured for Setup is rejected with a CRITICAL."""
    report = HookValidationReport(hook_path="test-hook")
    validate_single_hook({"type": "prompt", "prompt": "Prepare the session"}, "Setup", None, report)
    assert _has_type_rejection(report, "Setup")


def test_session_start_agent_hook_rejected():
    """An agent-type hook configured for SessionStart is rejected with a CRITICAL."""
    report = HookValidationReport(hook_path="test-hook")
    validate_single_hook({"type": "agent", "agent": "explore"}, "SessionStart", None, report)
    assert _has_type_rejection(report, "SessionStart")


# ---------------------------------------------------------------------------
# Item D — every shadowing plugin.json key is named (CC v2.1.142)
# ---------------------------------------------------------------------------


def test_shadow_advisory_names_every_shadowing_key(tmp_path):
    """When commands/agents/skills/outputStyles all shadow their default folders, each key gets its own MAJOR."""
    skill_md = "---\nname: keep\ndescription: A kept skill for the shadow test.\n---\nbody\n"
    root = _make_plugin(
        tmp_path,
        {
            "name": "test-plugin",
            "version": "0.1.0",
            "description": "A test plugin.",
            "commands": ["./commands/keep.md"],
            "agents": ["./agents/keep.md"],
            "outputStyles": ["./output-styles/keep.md"],
            "skills": ["skills/keep"],
        },
        commands=["keep.md", "extra.md"],
        agents=["keep.md", "extra.md"],
        output_styles=["keep.md", "extra.md"],
        skills={"keep": skill_md, "extra": skill_md},
    )
    report = ValidationReport()
    validate_manifest(root, report)
    # Every one of the four shadowing keys must be named in its own MAJOR.
    for key in ("commands", "agents", "outputStyles", "skills"):
        assert _has_shadow_finding(report, key), (
            f"missing shadow MAJOR for key {key!r}: {[(f.level, f.message) for f in report.results]}"
        )
