"""Tests for the prompt-cache audit validator (CA-01..CA-06).

Each rule has a positive test (fires on the documented breakage pattern)
and a negative test (does NOT fire on benign-but-similar code). FP guards
matter even more for CA-* than for security rules — a noisy cache check
will be ignored, defeating the point.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_validation_common import RULE_REGISTRY, ValidationReport  # noqa: E402
from validate_cache import (  # noqa: E402
    _DYNAMIC_PLACEHOLDER,
    _DYNAMIC_SHELL_CMD,
    _strip_fences_for_dynamic_check,
    scan_hook_for_fork_unsafe,
    scan_hook_for_prefix_mutation,
    scan_hook_for_tool_mutation,
    scan_hook_for_unbounded_output,
    scan_plugin_for_cache,
    scan_skill_for_model_override,
)

# -----------------------------------------------------------------------------
# Schema registration
# -----------------------------------------------------------------------------


class TestSchemaRegistration:
    """All 6 CA rules must be registered in the central schema registry."""

    def test_all_six_ca_rules_registered(self) -> None:
        ca_ids = {r.rule_id for r in RULE_REGISTRY if r.rule_id.startswith("CA-")}
        assert ca_ids == {"CA-01", "CA-02", "CA-03", "CA-04", "CA-05", "CA-06"}

    def test_ca_rules_have_descriptions(self) -> None:
        for rule in RULE_REGISTRY:
            if not rule.rule_id.startswith("CA-"):
                continue
            assert rule.description, f"{rule.rule_id} missing description"
            assert rule.references, f"{rule.rule_id} must cite cache-audit"


# -----------------------------------------------------------------------------
# Helpers — fence stripper, regex sanity
# -----------------------------------------------------------------------------


class TestFenceStripper:
    def test_strips_triple_backtick_block(self) -> None:
        text = "before\n```bash\n$(date)\n```\nafter"
        out = _strip_fences_for_dynamic_check(text)
        assert "$(date)" not in out
        assert "before" in out and "after" in out

    def test_strips_inline_backticks(self) -> None:
        text = "Use `$(date)` to get the date."
        out = _strip_fences_for_dynamic_check(text)
        assert "$(date)" not in out
        assert "Use" in out and "to get the date" in out

    def test_keeps_dynamic_marker_in_prose(self) -> None:
        text = "Today is $(date) and the prompt prefix sees this every session."
        out = _strip_fences_for_dynamic_check(text)
        assert "$(date)" in out


# -----------------------------------------------------------------------------
# CA-01 — static prefix scanner
# -----------------------------------------------------------------------------


class TestCA01:
    def test_dynamic_placeholder_in_claude_md_fires(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "CLAUDE.md").write_text("# My plugin\n\nCurrent time: {{TIMESTAMP}}\nMore prose.\n")
        report = scan_plugin_for_cache(plugin)
        ca01 = [r for r in report.results if "CA-01" in r.message]
        assert ca01, "CA-01 should fire for {{TIMESTAMP}} in CLAUDE.md"
        assert all(r.level == "MAJOR" for r in ca01)

    def test_shell_subst_in_agent_system_prompt_fires(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "agents").mkdir()
        (plugin / "agents" / "writer.md").write_text(
            "---\nname: writer\ndescription: writes\n---\n\nIt is currently $(date +%Y-%m-%d).\n"
        )
        report = scan_plugin_for_cache(plugin)
        ca01 = [r for r in report.results if "CA-01" in r.message]
        assert ca01, "CA-01 should fire for $(date) in agent system prompt"

    def test_static_option_placeholder_does_not_fire(self, tmp_path: Path) -> None:
        """${CLAUDE_PLUGIN_OPTION_*} placeholders are stable per session — not dynamic."""
        plugin = _make_plugin(tmp_path)
        (plugin / "CLAUDE.md").write_text("API endpoint: ${CLAUDE_PLUGIN_OPTION_API_URL}\n")
        report = scan_plugin_for_cache(plugin)
        ca01 = [r for r in report.results if "CA-01" in r.message]
        assert ca01 == [], f"option placeholders must not fire CA-01: {ca01}"

    def test_dollar_date_inside_fenced_block_does_not_fire(self, tmp_path: Path) -> None:
        """$(date) inside ```bash ... ``` is documentation, not active substitution."""
        plugin = _make_plugin(tmp_path)
        (plugin / "CLAUDE.md").write_text(
            "## Examples\n\n```bash\nTIMESTAMP=$(date +%s)\n```\n\nThat shell snippet runs at hook time, not at prompt time.\n"
        )
        report = scan_plugin_for_cache(plugin)
        ca01 = [r for r in report.results if "CA-01" in r.message]
        assert ca01 == []

    def test_dollar_date_inside_inline_backticks_does_not_fire(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "CLAUDE.md").write_text("Use `$(date)` to inject the current date in your hook output.\n")
        report = scan_plugin_for_cache(plugin)
        ca01 = [r for r in report.results if "CA-01" in r.message]
        assert ca01 == []

    def test_claude_project_dir_placeholder_is_static(self) -> None:
        """{{CLAUDE_PROJECT_DIR}} resolves to a stable path; do NOT match in regex."""
        text = "{{CLAUDE_PROJECT_DIR}} is fine"
        assert _DYNAMIC_PLACEHOLDER.search(text) is None

    def test_timestamp_placeholder_matches(self) -> None:
        for token in ("{{TIMESTAMP}}", "{{ DATE }}", "{{ now }}", "{{UUID}}", "{{SESSION_ID}}"):
            assert _DYNAMIC_PLACEHOLDER.search(token), f"should match {token}"


# -----------------------------------------------------------------------------
# CA-02 — hooks must not write to cached prefix files
# -----------------------------------------------------------------------------


class TestCA02:
    def test_session_start_hook_writing_claude_md_fires(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "init.sh"
        script.write_text("#!/bin/bash\necho 'Today: $(date)' >> ~/.claude/CLAUDE.md\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_prefix_mutation(script, "SessionStart", report, plugin)
        ca02 = [r for r in report.results if "CA-02" in r.message]
        assert ca02, "CA-02 must fire when SessionStart writes to CLAUDE.md"
        assert all(r.level == "MAJOR" for r in ca02)

    def test_user_prompt_submit_hook_appending_settings_fires(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "augment.sh"
        script.write_text("#!/bin/bash\nsed -i '' 's/x/y/' .claude/settings.json\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_prefix_mutation(script, "UserPromptSubmit", report, plugin)
        ca02 = [r for r in report.results if "CA-02" in r.message]
        assert ca02

    def test_stop_hook_writing_claude_md_does_not_fire(self, tmp_path: Path) -> None:
        """Stop hooks run AFTER the turn — touching CLAUDE.md doesn't bust this turn's cache."""
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "save.sh"
        script.write_text("#!/bin/bash\necho 'logged' >> ~/.claude/CLAUDE.md\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_prefix_mutation(script, "Stop", report, plugin)
        ca02 = [r for r in report.results if "CA-02" in r.message]
        assert ca02 == []

    def test_hook_writing_to_user_data_dir_does_not_fire(self, tmp_path: Path) -> None:
        """Plugin data files under .claude/data/ are not part of the cached prefix."""
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "store.sh"
        script.write_text("#!/bin/bash\necho 'cache' >> ${CLAUDE_PLUGIN_DATA}/state.json\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_prefix_mutation(script, "SessionStart", report, plugin)
        ca02 = [r for r in report.results if "CA-02" in r.message]
        assert ca02 == []

    def test_comment_only_writes_do_not_fire(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "documented.sh"
        script.write_text(
            "#!/bin/bash\n# echo 'breaks cache' >> ~/.claude/CLAUDE.md  # don't do this\necho '{\"continue\": true}'\n"
        )
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_prefix_mutation(script, "SessionStart", report, plugin)
        ca02 = [r for r in report.results if "CA-02" in r.message]
        assert ca02 == []


# -----------------------------------------------------------------------------
# CA-03 — hooks must not toggle tool allow/deny lists
# -----------------------------------------------------------------------------


class TestCA03:
    def test_hook_writing_allow_list_to_settings_fires(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "toggle.sh"
        script.write_text('#!/bin/bash\necho \'{"permissions":{"allow":["Bash"]}}\' > .claude/settings.json\n')
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_tool_mutation(script, "SessionStart", report, plugin)
        ca03 = [r for r in report.results if "CA-03" in r.message]
        assert ca03, "CA-03 must fire when settings.json allow list is rewritten"

    def test_hook_logging_to_unrelated_file_does_not_fire(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "log.sh"
        script.write_text("#!/bin/bash\necho 'allow=$ALLOW' >> /tmp/log.txt\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_tool_mutation(script, "SessionStart", report, plugin)
        ca03 = [r for r in report.results if "CA-03" in r.message]
        assert ca03 == [], "writing 'allow' to /tmp/log.txt is not a tool-list mutation"


# -----------------------------------------------------------------------------
# CA-04 — skills with `model:` frontmatter
# -----------------------------------------------------------------------------


class TestCA04:
    def test_skill_with_model_field_fires(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "skills" / "my-skill").mkdir(parents=True)
        (plugin / "skills" / "my-skill" / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: x\nmodel: opus\n---\n\nbody\n"
        )
        report = scan_plugin_for_cache(plugin)
        ca04 = [r for r in report.results if "CA-04" in r.message]
        assert ca04
        assert all(r.level == "MINOR" for r in ca04)
        assert "opus" in ca04[0].message

    def test_skill_without_model_field_does_not_fire(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "skills" / "fine-skill").mkdir(parents=True)
        (plugin / "skills" / "fine-skill" / "SKILL.md").write_text(
            "---\nname: fine-skill\ndescription: x\n---\n\nbody\n"
        )
        report = scan_plugin_for_cache(plugin)
        ca04 = [r for r in report.results if "CA-04" in r.message]
        assert ca04 == []

    def test_agent_with_model_field_does_not_fire_ca04(self, tmp_path: Path) -> None:
        """Agents have their own conversation — model: there is fine."""
        plugin = _make_plugin(tmp_path)
        (plugin / "agents").mkdir()
        (plugin / "agents" / "writer.md").write_text("---\nname: writer\ndescription: x\nmodel: opus\n---\n\nbody\n")
        # scan_skill_for_model_override is only called on SKILL.md files; agent
        # frontmatter is not in scope. The full plugin scan must NOT raise CA-04.
        report = scan_plugin_for_cache(plugin)
        ca04 = [r for r in report.results if "CA-04" in r.message]
        assert ca04 == []

    def test_skill_model_in_body_does_not_fire(self, tmp_path: Path) -> None:
        """A skill body that mentions models in prose must not trigger."""
        plugin = _make_plugin(tmp_path)
        (plugin / "skills" / "doc-skill").mkdir(parents=True)
        (plugin / "skills" / "doc-skill" / "SKILL.md").write_text(
            "---\nname: doc-skill\ndescription: x\n---\n\nUse the model: opus when you need long context.\n"
        )
        report = ValidationReport()
        scan_skill_for_model_override(plugin / "skills" / "doc-skill" / "SKILL.md", report, plugin)
        ca04 = [r for r in report.results if "CA-04" in r.message]
        assert ca04 == []


# -----------------------------------------------------------------------------
# CA-05 — unbounded git/find/cat output in hook scripts
# -----------------------------------------------------------------------------


class TestCA05:
    def test_unbounded_git_status_fires(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "ctx.sh"
        script.write_text("#!/bin/bash\ngit status\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_unbounded_output(script, "SessionStart", report, plugin)
        ca05 = [r for r in report.results if "CA-05" in r.message]
        assert ca05

    def test_bounded_git_status_short_does_not_fire(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "ctx.sh"
        script.write_text("#!/bin/bash\ngit status --short\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_unbounded_output(script, "SessionStart", report, plugin)
        ca05 = [r for r in report.results if "CA-05" in r.message]
        assert ca05 == []

    def test_git_log_with_n_does_not_fire(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "log.sh"
        script.write_text("#!/bin/bash\ngit log -n 5 --oneline\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_unbounded_output(script, "SessionStart", report, plugin)
        assert [r for r in report.results if "CA-05" in r.message] == []

    def test_find_without_maxdepth_or_head_fires(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "scan.sh"
        script.write_text("#!/bin/bash\nfind / -name '*.log'\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_unbounded_output(script, "SessionStart", report, plugin)
        assert [r for r in report.results if "CA-05" in r.message]

    def test_post_tool_use_hook_does_not_fire(self, tmp_path: Path) -> None:
        """CA-05 is about cached-prefix bloat — only prefix-affecting events count."""
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "post.sh"
        script.write_text("#!/bin/bash\ngit status\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_unbounded_output(script, "PostToolUse", report, plugin)
        assert [r for r in report.results if "CA-05" in r.message] == []


# -----------------------------------------------------------------------------
# CA-06 — fork-safety on PreCompact / SubagentStart
# -----------------------------------------------------------------------------


class TestCA06:
    def test_pre_compact_writing_claude_md_warns(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "compact.sh"
        script.write_text("#!/bin/bash\necho 'summary' > ~/.claude/CLAUDE.md\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_fork_unsafe(script, "PreCompact", report, plugin)
        ca06 = [r for r in report.results if "CA-06" in r.message]
        assert ca06
        assert all(r.level == "WARNING" for r in ca06)

    def test_user_prompt_submit_does_not_fire_ca06(self, tmp_path: Path) -> None:
        """CA-06 only applies to fork-affecting events."""
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "init.sh"
        script.write_text("#!/bin/bash\necho 'x' > ~/.claude/CLAUDE.md\n")
        script.chmod(0o755)
        report = ValidationReport()
        scan_hook_for_fork_unsafe(script, "UserPromptSubmit", report, plugin)
        ca06 = [r for r in report.results if "CA-06" in r.message]
        assert ca06 == []


# -----------------------------------------------------------------------------
# Plugin-level integration
# -----------------------------------------------------------------------------


class TestPluginIntegration:
    def test_clean_plugin_yields_passed(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "CLAUDE.md").write_text("# my plugin\n\nstatic instructions.\n")
        report = scan_plugin_for_cache(plugin)
        assert any(r.level == "PASSED" for r in report.results)
        assert all(r.level not in ("CRITICAL", "MAJOR", "MINOR") for r in report.results)

    def test_missing_plugin_json_critical(self, tmp_path: Path) -> None:
        report = scan_plugin_for_cache(tmp_path / "nope")
        assert any(r.level == "CRITICAL" for r in report.results)

    def test_full_pipeline_with_hooks_json_finds_violation(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        (plugin / "hooks").mkdir()
        script = plugin / "hooks" / "init.sh"
        script.write_text("#!/bin/bash\necho 'x' >> ~/.claude/CLAUDE.md\n")
        script.chmod(0o755)
        # Wire it as a SessionStart hook
        (plugin / "hooks" / "hooks.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/init.sh"},
                                ],
                            }
                        ]
                    }
                }
            )
        )
        report = scan_plugin_for_cache(plugin)
        assert any("CA-02" in r.message for r in report.results)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _make_plugin(tmp_path: Path, name: str = "test-plugin") -> Path:
    """Create a minimal plugin scaffold (`.claude-plugin/plugin.json`)."""
    plugin = tmp_path / name
    plugin.mkdir()
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0", "description": "x"})
    )
    return plugin


# -----------------------------------------------------------------------------
# Regex sanity for direct exports
# -----------------------------------------------------------------------------


class TestRegexSanity:
    def test_dynamic_shell_cmd_matches_date(self) -> None:
        assert _DYNAMIC_SHELL_CMD.search("$(date +%s)")

    def test_dynamic_shell_cmd_matches_git_status(self) -> None:
        assert _DYNAMIC_SHELL_CMD.search("$(git status)")

    def test_dynamic_shell_cmd_does_not_match_static_call(self) -> None:
        # $(echo "hi") is dynamic in shell semantics but produces stable output;
        # CA-01 is intentionally narrow and only flags well-known dynamic tools.
        assert _DYNAMIC_SHELL_CMD.search("$(echo 'hi')") is None
