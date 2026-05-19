#!/usr/bin/env python3
"""Tests for the Claude Code v2.1.145 changelog catch-up (TRDD-31de95b7).

v2.1.145 (May 19, 2026) shipped a CC-side validator change plus an
infinite-loop fix that imply two new CPV checks:

* **Item 1 — `claude plugin validate` flags `skills:` file entries.**
  The official validator previously accepted a `skills:` entry that
  pointed at a `SKILL.md` file (instead of the parent directory).
  v2.1.145 flags this and suggests the parent directory. CPV mirrors
  the behaviour with a **MINOR** (the entry still works at runtime
  per v2.1.142's root-level SKILL.md surfacing, but the official
  validator now objects).
* **Item 2 — `context: fork` skill self-recursion.** CC v2.1.145
  fixed an infinite loop where a `context: fork` skill could
  re-invoke itself via `Skill()` instead of running. The runtime
  bug is fixed but the antipattern remains almost always
  unintentional. CPV emits a **MINOR** so authors restructure into
  helpers / external skills.

Plus a backfill from v2.1.142:

* **Item 3 — `CLAUDE_CODE_OPUS_4_6_FAST_MODE_OVERRIDE` env var.**
  v2.1.142 added this opt-in to pin fast mode to Opus 4.6 (default
  since v2.1.142 is Opus 4.7). The v2.87.0 catch-up TRDD missed it;
  plugin docs referencing the name were producing false-positive
  unknown-env-var findings.

The remaining v2.1.145 items are regression-pinned (no validator
change, but the behaviour must not silently regress):

* **Item 4 — Stop/SubagentStop hook input gained `background_tasks`
  and `session_crons` fields.** CPV does not parse hook script bodies
  (only their config), so the new stdin fields don't reach the
  validator's surface. Regression-pin only: a hook config remains
  valid regardless of which input fields the script reads.
* **Item 5 — Status line JSON input now includes GitHub repo + PR
  info.** The statusline command is just a Bash script reading stdin;
  CPV validates the command spec, not its stdout. Regression-pin
  only: a statusline spec stays valid.
* **Item 6 — Read tool partial-view notice.** CC-internal behaviour;
  no validator footprint.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    VALID_PLUGIN_ENV_VARS,
    ValidationReport,
    is_valid_plugin_env_var,
)

# =============================================================================
# Item 3 — CLAUDE_CODE_OPUS_4_6_FAST_MODE_OVERRIDE (v2.1.142 backfill)
# =============================================================================


class TestOpus46FastModeOverrideEnvVar:
    """v2.1.142 backfill — env var was missed in the v2.87.0 catch-up."""

    def test_opus_4_6_fast_mode_override_recognised_in_set(self) -> None:
        """``CLAUDE_CODE_OPUS_4_6_FAST_MODE_OVERRIDE`` is in the canonical
        allowlist so docs/settings referencing it do not flag as unknown."""
        assert "CLAUDE_CODE_OPUS_4_6_FAST_MODE_OVERRIDE" in VALID_PLUGIN_ENV_VARS

    def test_opus_4_6_fast_mode_override_helper_accepts(self) -> None:
        """The dynamic helper ``is_valid_plugin_env_var`` agrees."""
        assert is_valid_plugin_env_var("CLAUDE_CODE_OPUS_4_6_FAST_MODE_OVERRIDE")

    def test_negative_control_unknown_env_var_still_rejected(self) -> None:
        """A clearly unrelated env var must still be unknown — proves the
        previous two assertions aren't accidentally matching everything."""
        assert not is_valid_plugin_env_var("CLAUDE_CODE_OPUS_4_5_NONEXISTENT_KNOB")


# =============================================================================
# Item 1 — `skills:` file-entry MINOR demotion
# =============================================================================


class TestSkillsFieldFileEntryDemoted:
    """v2.1.145: `claude plugin validate` flags `skills:` file entries."""

    def _make_plugin(self, tmp_path: Path, skills_value: object) -> Path:
        """Build a minimal plugin at ``tmp_path`` with the given ``skills`` value."""
        plugin = tmp_path / "demo-plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / "skills" / "helper").mkdir(parents=True)
        (plugin / "skills" / "helper" / "SKILL.md").write_text(
            "---\nname: helper\ndescription: Demo helper skill\n---\n\n# helper\n\nBody.\n"
        )
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "demo-plugin",
                    "version": "0.1.0",
                    "description": "test fixture",
                    "skills": skills_value,
                }
            )
        )
        return plugin

    def test_file_entry_pointing_at_SKILL_md_emits_minor(self, tmp_path: Path) -> None:
        """A `skills:` entry pointing at a literal SKILL.md file (rather than
        its parent directory) emits a MINOR with a parent-directory hint."""
        from validate_plugin import validate_manifest_skill_paths

        plugin = self._make_plugin(tmp_path, ["skills/helper/SKILL.md"])
        report = ValidationReport()

        validate_manifest_skill_paths(plugin, report)

        minors = [r for r in report.results if r.level == "MINOR"]
        flagged = [m for m in minors if "points at a file" in m.message]
        assert flagged, f"Expected MINOR with 'points at a file', got: {[m.message for m in minors]}"
        # Parent-directory hint must include the parent path.
        assert any("skills/helper" in m.message for m in flagged), (
            "MINOR must name the parent directory in the hint"
        )

    def test_directory_entry_passes_silently(self, tmp_path: Path) -> None:
        """A `skills:` entry pointing at the directory does NOT emit the
        v2.1.145 MINOR — only file entries do."""
        from validate_plugin import validate_manifest_skill_paths

        plugin = self._make_plugin(tmp_path, ["skills/helper"])
        report = ValidationReport()

        validate_manifest_skill_paths(plugin, report)

        flagged = [r for r in report.results if "points at a file" in r.message]
        assert flagged == [], (
            f"Directory entries must not trip the v2.1.145 file-entry MINOR; got: "
            f"{[r.message for r in flagged]}"
        )

    def test_non_skillmd_file_entry_still_major(self, tmp_path: Path) -> None:
        """A `skills:` entry pointing at a non-SKILL.md file remains MAJOR
        — the v2.1.145 demotion only covers the SKILL.md case (file works
        at runtime per v2.1.142 root-level SKILL.md surfacing)."""
        from validate_plugin import validate_manifest_skill_paths

        plugin = self._make_plugin(tmp_path, ["skills/helper/SKILL.md"])
        # Add an arbitrary stray file
        (plugin / "skills" / "helper" / "README.md").write_text("# readme")
        # Override the manifest to point at the stray file
        manifest_path = plugin / ".claude-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["skills"] = ["skills/helper/README.md"]
        manifest_path.write_text(json.dumps(manifest))

        report = ValidationReport()
        validate_manifest_skill_paths(plugin, report)

        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("is a file but not a" in m.message for m in majors), (
            f"Non-SKILL.md file entry must still emit MAJOR; got: {[m.message for m in majors]}"
        )


# =============================================================================
# Item 2 — context: fork self-recursion detection
# =============================================================================


class TestContextForkSelfRecursionDetected:
    """v2.1.145: detect a `context: fork` skill that invokes itself."""

    def _make_skill(self, tmp_path: Path, body: str, context: str = "fork") -> Path:
        """Build a minimal skill directory at ``tmp_path``."""
        skill = tmp_path / "demo-skill"
        skill.mkdir(parents=True)
        # Plug a plugin manifest in so the namespace qualifier resolves
        (tmp_path / ".claude-plugin").mkdir(exist_ok=True)
        (tmp_path / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "my-plugin", "version": "0.1.0", "description": "demo"})
        )
        if context:
            frontmatter = (
                f"---\nname: demo-skill\ndescription: An actionable test fixture skill\n"
                f"context: {context}\nmodel: haiku\n---\n\n"
            )
        else:
            frontmatter = "---\nname: demo-skill\ndescription: A demo skill\n---\n\n"
        (skill / "SKILL.md").write_text(frontmatter + body)
        return skill

    def test_bare_self_invocation_emits_minor(self, tmp_path: Path) -> None:
        """A `context: fork` skill that calls ``Skill({skill: "<self>"})``
        triggers a MINOR with the v2.1.145 changelog reference."""
        from validate_skill_comprehensive import validate_skill

        body = (
            "# Demo skill\n\n"
            "## Steps\n\n"
            "1. Run the helper:\n\n"
            '   `Skill({skill: "demo-skill"})`\n'
            "2. Return the result.\n"
        )
        skill = self._make_skill(tmp_path, body)
        report = validate_skill(skill, skip_dir_name_check=True)

        minors = [r for r in report.results if r.level == "MINOR"]
        flagged = [m for m in minors if "self-recursion" in m.message]
        assert flagged, (
            f"Expected MINOR with 'self-recursion' for bare invocation; got: "
            f"{[m.message for m in minors]}"
        )

    def test_qualified_self_invocation_emits_minor(self, tmp_path: Path) -> None:
        """Fully-qualified self-invocation ``Skill({skill: "<plugin>:<self>"})``
        also triggers the MINOR."""
        from validate_skill_comprehensive import validate_skill

        body = (
            "# Demo skill\n\n"
            "## Steps\n\n"
            "1. Recurse via the plugin namespace:\n\n"
            '   `Skill({skill: "my-plugin:demo-skill"})`\n'
            "2. Return.\n"
        )
        skill = self._make_skill(tmp_path, body)
        report = validate_skill(skill, skip_dir_name_check=True)

        flagged = [r for r in report.results if "self-recursion" in r.message]
        assert flagged, "Qualified `<plugin>:<self>` invocation must trigger the MINOR"

    def test_non_fork_skill_with_self_invocation_no_minor(self, tmp_path: Path) -> None:
        """A skill WITHOUT `context: fork` calling itself does not produce
        the v2.1.145 MINOR — the antipattern is specifically the fork +
        self-invocation combination (only the fork case had the infinite
        loop bug)."""
        from validate_skill_comprehensive import validate_skill

        body = (
            "# Demo skill\n\n"
            "## Steps\n\n"
            "1. Implementation detail:\n\n"
            '   `Skill({skill: "demo-skill"})`\n'
        )
        skill = self._make_skill(tmp_path, body, context="")
        report = validate_skill(skill, skip_dir_name_check=True)

        flagged = [r for r in report.results if "self-recursion" in r.message]
        assert flagged == [], (
            "Non-fork skills must not trigger the v2.1.145 MINOR — only fork+self does"
        )

    def test_fork_skill_invoking_OTHER_skill_no_minor(self, tmp_path: Path) -> None:
        """A `context: fork` skill calling a DIFFERENT skill is fine — only
        self-invocation is the antipattern."""
        from validate_skill_comprehensive import validate_skill

        body = (
            "# Demo skill\n\n"
            "## Steps\n\n"
            "1. Delegate to helper:\n\n"
            '   `Skill({skill: "other-helper"})`\n'
            "2. Return.\n"
        )
        skill = self._make_skill(tmp_path, body)
        report = validate_skill(skill, skip_dir_name_check=True)

        flagged = [r for r in report.results if "self-recursion" in r.message]
        assert flagged == [], (
            "Fork skills calling OTHER skills must not trigger the MINOR — only self"
        )


# =============================================================================
# Items 4-6 — regression pins (no validator change)
# =============================================================================


class TestHookInputBackgroundTasksAndSessionCrons:
    """Item 4 regression pin — Stop/SubagentStop hook input now includes
    ``background_tasks`` and ``session_crons`` fields.

    CPV validates hook CONFIG (the hooks.json registration) not hook
    SCRIPT bodies (what the script reads from stdin). The new input
    fields are read by the script if and only if its author chooses
    to read them — there is nothing for CPV to validate or reject.

    Regression pin: a Stop hook config remains valid regardless of
    whether the underlying script reads the new fields. This protects
    against a future overzealous validator change that tries to
    enforce hook script body content.
    """

    def test_stop_hook_config_still_valid(self, tmp_path: Path) -> None:
        """A minimal Stop hook entry validates cleanly — the new stdin
        schema fields don't reach hook-config validation."""
        from validate_hook import validate_hooks

        hooks_path = tmp_path / "hooks.json"
        hooks_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": "echo 'turn ended'"}
                                ],
                            }
                        ]
                    }
                }
            )
        )
        report = validate_hooks(hooks_path)

        # Stop hook config must not trip CRITICAL findings (matcher field
        # is optional on Stop, command is well-formed). Some MAJORs about
        # script-non-existence are acceptable since the command refers
        # to a literal echo rather than a real script — only fail on
        # CRITICAL.
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert criticals == [], (
            f"Stop hook config must not trip CRITICAL; got: "
            f"{[(r.level, r.message) for r in criticals]}"
        )

    def test_background_tasks_and_session_crons_not_modelled_in_hook_output(self) -> None:
        """``background_tasks`` and ``session_crons`` are hook INPUT fields
        (CC reads them into the hook's stdin), not OUTPUT fields the hook
        emits. They must not appear in ``UNIVERSAL_OUTPUT_FIELDS`` — a
        future change adding them there would be a misclassification."""
        from validate_hook_output import UNIVERSAL_OUTPUT_FIELDS

        assert "background_tasks" not in UNIVERSAL_OUTPUT_FIELDS, (
            "background_tasks is a stdin INPUT field, not an OUTPUT field; "
            "must not appear in UNIVERSAL_OUTPUT_FIELDS"
        )
        assert "session_crons" not in UNIVERSAL_OUTPUT_FIELDS, (
            "session_crons is a stdin INPUT field, not an OUTPUT field; "
            "must not appear in UNIVERSAL_OUTPUT_FIELDS"
        )


class TestStatusLineInputGithubFields:
    """Item 5 regression pin — statusline JSON input now includes GitHub
    repo and PR information.

    The statusline command is registered under ``statusLine`` in
    settings.json with a ``command`` field; CPV checks the command spec
    (type, path, shape) but does not parse the script's stdin. The new
    GitHub/PR fields land in stdin for the author's script to read or
    ignore — no CPV impact.

    Regression pin: a settings.json with a statusline command stays
    valid regardless of the input-schema additions.
    """

    def test_statusline_is_known_settings_key(self) -> None:
        """``statusLine`` is in CPV's recognized settings.json keys — the
        validator must accept it as a known top-level field regardless of
        what fields its piped-in JSON now includes."""
        from cc_scope_rules import KNOWN_SETTINGS_KEYS

        assert "statusLine" in KNOWN_SETTINGS_KEYS, (
            "statusLine must remain in KNOWN_SETTINGS_KEYS — the v2.1.145 "
            "GitHub/PR input-schema additions don't reach settings.json validation"
        )

    def test_github_pr_input_fields_not_modelled_as_settings(self) -> None:
        """No CPV constant should model statusline INPUT fields like
        ``github_repo``, ``pr_number``, etc. as settings keys — those
        are stdin fields the author's script can read."""
        from cc_scope_rules import KNOWN_SETTINGS_KEYS

        leak_candidates = [
            k for k in KNOWN_SETTINGS_KEYS
            if k.lower() in {"github_repo", "pr_number", "pr_title", "github_pr"}
        ]
        assert leak_candidates == [], (
            f"Statusline stdin fields leaked into KNOWN_SETTINGS_KEYS: {leak_candidates}"
        )


class TestReadToolPartialViewBehaviour:
    """Item 6 regression pin — Read tool returns a 'PARTIAL view' notice
    instead of a hard error for over-token-budget whole-file reads.

    Pure CC-internal behaviour. No plugin file format, no plugin
    permission rule, no plugin env var is touched.

    Regression pin: nothing in CPV currently models the Read tool's
    error vs. partial-view behaviour; if a future CPV change tries to
    add such modelling, this test will at least surface the
    'no-behaviour-modelled' contract.
    """

    def test_read_tool_partial_view_not_modelled(self) -> None:
        """CPV has no constant or rule about Read-tool partial views.
        This test pins that — adding one would be a deliberate design
        decision, not an accidental side-effect."""
        from cpv_validation_common import VALID_PLUGIN_ENV_VARS

        # No env var, no setting, no constant references the partial-view
        # behaviour. Spot-check the env-var set.
        partial_view_names = [n for n in VALID_PLUGIN_ENV_VARS if "PARTIAL" in n.upper()]
        assert partial_view_names == [], (
            f"No env var should model Read-tool partial views; found: {partial_view_names}"
        )
