#!/usr/bin/env python3
"""Tests for the v2.84.0 plugin.json default-folder shadow warning (CC v2.1.140).

Per the v2.1.140 changelog:
    Plugins now warn when a default component folder (e.g. ``commands/``)
    is silently ignored because ``plugin.json`` sets the matching key.

When ``plugin.json`` declares one of ``commands``, ``agents``, ``skills``,
``outputStyles``, the default folder is silently bypassed at runtime. Files
left in the default folder but not listed never reach Claude Code. CPV emits
a MAJOR warning so authors catch the shadowing pre-publish.

Coverage:
* Partial array → MAJOR listing the shadowed files.
* Full array (every default-folder item explicitly listed) → no warning.
* Bare default-folder reference ("commands") in array → no warning.
* String pointing AT the default folder → no warning.
* Key set but pointing entirely outside the default folder → MAJOR listing
  ALL default-folder items as shadowed.
* skills key shadows skill subdirectories the same way.
* Missing default folder → no warning (nothing to shadow).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import validate_manifest  # noqa: E402


def _make_plugin(
    tmp_path: Path,
    manifest: dict,
    *,
    commands: list[str] | None = None,
    agents: list[str] | None = None,
    skills: list[str] | None = None,
    output_styles: list[str] | None = None,
) -> Path:
    """Lay down a plugin tree with the named manifest and optional default-folder content."""
    root = tmp_path / "plugin"
    root.mkdir(parents=True, exist_ok=True)
    cp_dir = root / ".claude-plugin"
    cp_dir.mkdir(exist_ok=True)
    (cp_dir / "plugin.json").write_text(json.dumps(manifest, indent=2))
    if commands:
        d = root / "commands"
        d.mkdir(exist_ok=True)
        for n in commands:
            (d / n).write_text("# placeholder")
    if agents:
        d = root / "agents"
        d.mkdir(exist_ok=True)
        for n in agents:
            (d / n).write_text("---\nname: x\n---\nbody")
    if skills:
        d = root / "skills"
        d.mkdir(exist_ok=True)
        for n in skills:
            sd = d / n
            sd.mkdir(exist_ok=True)
            (sd / "SKILL.md").write_text("---\nname: " + n + "\ndescription: test\n---\nbody")
    if output_styles:
        d = root / "output-styles"
        d.mkdir(exist_ok=True)
        for n in output_styles:
            (d / n).write_text("style")
    return root


def _has_shadow_finding(report: ValidationReport, key: str) -> bool:
    needle = f"Field '{key}' is set in plugin.json"
    return any(f.level == "MAJOR" and needle in f.message and "silently ignores" in f.message for f in report.results)


# ---------------------------------------------------------------------------
# commands shadow detection
# ---------------------------------------------------------------------------


def test_commands_partial_array_shadows_remaining(tmp_path):
    """commands: ['./commands/foo.md'] when bar.md also exists → MAJOR shadow."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "commands": ["./commands/foo.md"]},
        commands=["foo.md", "bar.md"],
    )
    report = ValidationReport()
    validate_manifest(root, report)
    assert _has_shadow_finding(report, "commands"), [(f.level, f.message) for f in report.results]
    # The shadowed file must be named in the message.
    assert any("bar.md" in f.message for f in report.results)


def test_commands_full_array_no_shadow(tmp_path):
    """commands: ['./commands/foo.md', './commands/bar.md'] covers both → no warning."""
    root = _make_plugin(
        tmp_path,
        {
            "name": "test-plugin",
            "version": "0.1.0",
            "commands": ["./commands/foo.md", "./commands/bar.md"],
        },
        commands=["foo.md", "bar.md"],
    )
    report = ValidationReport()
    validate_manifest(root, report)
    assert not _has_shadow_finding(report, "commands")


def test_commands_bare_folder_reference_no_shadow(tmp_path):
    """commands: ['./commands/'] covers all current AND future content → no warning."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "commands": ["./commands/"]},
        commands=["foo.md", "bar.md"],
    )
    report = ValidationReport()
    validate_manifest(root, report)
    assert not _has_shadow_finding(report, "commands")


def test_commands_string_form_pointing_at_default_no_shadow(tmp_path):
    """commands: './commands/' (string form) covers all → no warning."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "commands": "./commands/"},
        commands=["foo.md", "bar.md"],
    )
    report = ValidationReport()
    validate_manifest(root, report)
    assert not _has_shadow_finding(report, "commands")


def test_commands_entirely_external_shadows_all(tmp_path):
    """commands: ['./other/x.md'] when default folder has content → MAJOR with all items."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "commands": ["./other/x.md"]},
        commands=["foo.md", "bar.md"],
    )
    # plugin_root/other/x.md must exist to keep validate_manifest from complaining
    # about a separate "missing file" issue — but the shadow check doesn't care
    # whether the external path resolves, only whether default content is covered.
    (root / "other").mkdir(exist_ok=True)
    (root / "other" / "x.md").write_text("# external")
    report = ValidationReport()
    validate_manifest(root, report)
    assert _has_shadow_finding(report, "commands")


def test_commands_default_folder_missing_no_warning(tmp_path):
    """commands: ['./other/x.md'] and no ./commands/ folder → no shadow (nothing to shadow)."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "commands": ["./other/x.md"]},
    )
    (root / "other").mkdir(exist_ok=True)
    (root / "other" / "x.md").write_text("# external")
    report = ValidationReport()
    validate_manifest(root, report)
    assert not _has_shadow_finding(report, "commands")


def test_commands_default_folder_empty_no_warning(tmp_path):
    """./commands/ exists but is empty → no shadow (nothing to shadow)."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "commands": ["./other/x.md"]},
    )
    (root / "commands").mkdir(exist_ok=True)  # empty folder
    (root / "other").mkdir(exist_ok=True)
    (root / "other" / "x.md").write_text("# external")
    report = ValidationReport()
    validate_manifest(root, report)
    assert not _has_shadow_finding(report, "commands")


def test_commands_key_absent_no_warning(tmp_path):
    """No 'commands' key → auto-discovery applies, no shadow warning ever."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0"},
        commands=["foo.md", "bar.md"],
    )
    report = ValidationReport()
    validate_manifest(root, report)
    assert not _has_shadow_finding(report, "commands")


# ---------------------------------------------------------------------------
# agents shadow detection
# ---------------------------------------------------------------------------


def test_agents_partial_shadows_remaining(tmp_path):
    """Same shadow logic for agents/."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "agents": ["./agents/reviewer.md"]},
        agents=["reviewer.md", "tester.md"],
    )
    report = ValidationReport()
    validate_manifest(root, report)
    assert _has_shadow_finding(report, "agents")
    assert any("tester.md" in f.message for f in report.results)


# ---------------------------------------------------------------------------
# outputStyles shadow detection
# ---------------------------------------------------------------------------


def test_outputStyles_partial_shadows_remaining(tmp_path):
    """Same shadow logic for outputStyles/ → output-styles/ folder."""
    root = _make_plugin(
        tmp_path,
        {
            "name": "test-plugin",
            "version": "0.1.0",
            "outputStyles": ["./output-styles/dense.md"],
        },
        output_styles=["dense.md", "verbose.md"],
    )
    report = ValidationReport()
    validate_manifest(root, report)
    assert _has_shadow_finding(report, "outputStyles")
    assert any("verbose.md" in f.message for f in report.results)


# ---------------------------------------------------------------------------
# skills shadow detection (folder-based)
# ---------------------------------------------------------------------------


def test_skills_partial_shadows_remaining_subdirs(tmp_path):
    """skills: ['./skills/alpha'] when beta also has SKILL.md → MAJOR shadow."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "skills": ["./skills/alpha"]},
        skills=["alpha", "beta"],
    )
    report = ValidationReport()
    validate_manifest(root, report)
    assert _has_shadow_finding(report, "skills")
    assert any("beta" in f.message for f in report.results)


def test_skills_bare_folder_reference_no_shadow(tmp_path):
    """skills: 'skills' (bare folder ref) covers all subdirs → no warning."""
    root = _make_plugin(
        tmp_path,
        {"name": "test-plugin", "version": "0.1.0", "skills": "./skills/"},
        skills=["alpha", "beta"],
    )
    report = ValidationReport()
    validate_manifest(root, report)
    assert not _has_shadow_finding(report, "skills")
