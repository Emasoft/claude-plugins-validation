#!/usr/bin/env python3
"""Tests for the Claude Code v2.1.144 changelog catch-up (review).

The v2.1.144 release is dominated by Claude Code-side bug fixes
(rendering, startup hangs, timeout behaviour, MCP pagination, session
title sourcing, etc.) with **zero new plugin schema/key/env-var
additions**. Every plugin-relevant item is either:

* already enforced by CPV from a prior catch-up (v2.84.0 / v2.87.0 /
  v2.88.0 covering v2.1.139..v2.1.143), or
* a CC-side behavioural fix that needs no validator change.

This file documents the review and pins regressions for the small
number of items that do touch the plugin surface, so future CPV
changes cannot silently break the v2.1.144-correct behaviour.

## Item-by-item review

| v2.1.144 item                                                | Plugin-validator footprint | Status |
|--------------------------------------------------------------|----------------------------|--------|
| ``/resume`` for background sessions                          | None — CC built-in         | n/a    |
| Elapsed duration in subagent notifications                   | None — UI only             | n/a    |
| ``/plugin`` panes show last-updated date                     | None — metadata display    | n/a    |
| ``/model`` per-session by default                            | None — CC built-in         | n/a    |
| Usage credits rename (``/extra-usage`` → ``/usage-credits``) | None — CC built-in         | n/a    |
| 15s timeout for side-channel API calls                       | None — CC internal         | n/a    |
| MCP paginated tools/list responses fully read                | None — CC bug fix          | n/a    |
| MCP SVG MIME fallback to disk                                | None — CC bug fix          | n/a    |
| Plugin enabled-but-not-cached error                          | None — CC fix              | n/a    |
| Plugins enabled by project ``.claude/settings.json``         | Plugin enable from project | Locked |
| Skill-dir build no longer triggers reloads on non-.md files  | Plugins ship non-.md       | Locked |
| Marketplace add/update respects ``CLAUDE_CODE_PLUGIN_PREFER_HTTPS`` | Env var          | v2.88.0 |
| ``/plugin`` returns to Installed list                        | None — UI behaviour        | n/a    |
| ``/doctor`` exec-form example for missing ``command`` field  | None — CC msg              | n/a    |
| Skill-listing truncation removed from startup notification   | None — UI                  | n/a    |
| Session title not from plugin monitor output                 | None — CC fix              | n/a    |
| Background-job daemon respawn on macOS Full Disk Access      | None — CC fix              | n/a    |
| Stop-hook block cap (``CLAUDE_CODE_STOP_HOOK_BLOCK_CAP``)    | Env var                    | v2.88.0 |

Two items earn regression-pinning tests here:

* **Plugins enabled via project-scope settings** — CPV's local-scope and
  project-scope validators must continue to recognise plugins enabled
  through ``$PROJECT/.claude/settings.json``'s ``enabledPlugins``
  field, not just ``~/.claude/settings.json``. The v2.1.144 user-facing
  install hint is contingent on this being a supported pattern.
* **Skills containing non-.md files** — CPV must NOT reject a skill
  directory because it contains non-.md files (Python scripts, image
  assets, .DS_Store, etc.). The v2.1.144 fix prevents CC from reloading
  on non-.md changes, but only if non-.md files are an accepted shape;
  CPV's validator already supports this (its own plugin ships skills
  with .py files under references/), and this test pins that behaviour.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))


class TestPluginsEnabledViaProjectScope:
    """Item: plugins enabled by ``$PROJECT/.claude/settings.json`` are valid.

    The v2.1.144 install hint ("plugins enabled only by a project's
    ``.claude/settings.json`` now show an actionable claude plugin
    install hint") confirms project-scope plugin enable is a first-class
    pattern. CPV's local-scope validator must recognise the
    ``enabledPlugins`` key in project settings.
    """

    def test_project_settings_enabled_plugins_key_recognised(self) -> None:
        """``enabledPlugins`` in ``$PROJECT/.claude/settings.json`` is a known
        settings key — CPV must not flag it as unknown."""
        from cc_scope_rules import KNOWN_SETTINGS_KEYS

        # The enabledPlugins key is documented and accepted at every scope.
        assert "enabledPlugins" in KNOWN_SETTINGS_KEYS, (
            "enabledPlugins must remain in the allow-list — v2.1.144 confirms project-scope plugin enable is supported"
        )

    def test_project_scope_settings_with_enabled_plugins_validates(self, tmp_path: Path) -> None:
        """A ``$PROJECT/.claude/settings.json`` with an ``enabledPlugins``
        mapping does not produce structural CRITICALs."""
        from cpv_validation_common import ValidationReport
        from validate_local_scope import validate_settings_local_json

        project_root = tmp_path / "demo-project"
        (project_root / ".claude").mkdir(parents=True)
        settings_path = project_root / ".claude" / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "enabledPlugins": {
                        "claude-menu-system@emasoft-plugins": True,
                        "ai-maestro-janitor@ai-maestro-plugins": True,
                    }
                }
            )
        )
        report = ValidationReport()
        validate_settings_local_json(settings_path, report)
        # The settings file is well-formed — no CRITICAL just from the key.
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL" and "enabledPlugins" in r.message]
        assert not critical_msgs, (
            f"enabledPlugins key in project settings should not produce CRITICAL, got: {critical_msgs}"
        )


class TestSkillsWithNonMarkdownFiles:
    """Item: skill directories may contain non-.md files (.py, assets, etc.).

    CC v2.1.144 fixed a bug where builds inside a skill directory caused
    skill reloads on every non-.md file change. The fix implies non-.md
    files in skill dirs are a legitimate, supported shape — they should
    not be flagged as suspicious by CPV.
    """

    def test_skill_with_python_helper_in_references_validates(self, tmp_path: Path) -> None:
        """A skill with a ``references/helper.py`` does not produce CRITICAL
        findings for the .py file being present."""
        from validate_skill_comprehensive import validate_skill

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Test skill with a Python helper "
            "in references/ — exercises the v2.1.144 non-.md-files-allowed shape.\n---\n\n"
            "# My Skill\n\nA test skill demonstrating non-.md content under references/.\n"
        )
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "helper.py").write_text(
            '"""Test helper — exists to confirm non-.md files in skill dirs are accepted."""\n'
        )

        report = validate_skill(skill_dir)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL" and "helper.py" in r.message]
        assert not critical_msgs, f"Non-.md file under references/ should not produce CRITICAL, got: {critical_msgs}"

    def test_skill_with_image_asset_validates(self, tmp_path: Path) -> None:
        """A skill with a binary asset under ``assets/`` validates cleanly."""
        from validate_skill_comprehensive import validate_skill

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        skill_dir = skills_dir / "asset-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: asset-skill\ndescription: Test skill with a PNG asset "
            "under assets/ — exercises the v2.1.144 non-.md-files-allowed shape.\n---\n\n"
            "# Asset Skill\n\nA test skill with binary content.\n"
        )
        (skill_dir / "assets").mkdir()
        # Fake PNG signature followed by minimal content
        (skill_dir / "assets" / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        report = validate_skill(skill_dir)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL" and "icon.png" in r.message]
        assert not critical_msgs, f"Binary asset under assets/ should not produce CRITICAL, got: {critical_msgs}"


class TestPreviouslyHandledEnvVars:
    """Regression-lock that the v2.1.141/143 env vars stay in the allow-list.

    v2.1.144 didn't add env vars, but it relies on the ones added in
    earlier catch-ups (HTTPS preference, stop-hook block cap). Lock them
    so future CPV refactors can't silently regress.
    """

    def test_claude_code_plugin_prefer_https_recognised(self) -> None:
        """``CLAUDE_CODE_PLUGIN_PREFER_HTTPS`` (v2.1.141) must stay in the
        env-var allow-list — v2.1.144 marketplace add/update uses it."""
        from cpv_validation_common import VALID_PLUGIN_ENV_VARS, is_valid_plugin_env_var

        assert "CLAUDE_CODE_PLUGIN_PREFER_HTTPS" in VALID_PLUGIN_ENV_VARS
        assert is_valid_plugin_env_var("CLAUDE_CODE_PLUGIN_PREFER_HTTPS")

    def test_claude_code_stop_hook_block_cap_recognised(self) -> None:
        """``CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`` (v2.1.143) must stay in the
        env-var allow-list — v2.1.144 keeps the 8-block default behaviour."""
        from cpv_validation_common import VALID_PLUGIN_ENV_VARS, is_valid_plugin_env_var

        assert "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP" in VALID_PLUGIN_ENV_VARS
        assert is_valid_plugin_env_var("CLAUDE_CODE_STOP_HOOK_BLOCK_CAP")

    def test_claude_code_powershell_respect_execution_policy_recognised(self) -> None:
        """``CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY`` (v2.1.143) must
        stay in the env-var allow-list."""
        from cpv_validation_common import VALID_PLUGIN_ENV_VARS, is_valid_plugin_env_var

        assert "CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY" in VALID_PLUGIN_ENV_VARS
        assert is_valid_plugin_env_var("CLAUDE_CODE_POWERSHELL_RESPECT_EXECUTION_POLICY")
