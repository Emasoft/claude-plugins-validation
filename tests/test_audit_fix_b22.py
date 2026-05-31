"""Regression tests for full-audit batch B22 fixes.

Covers the audit findings assigned to:
  - scripts/validate_agent.py  (findings 83, 157)
  - scripts/validate_rules.py  (findings 92, 164)
  - scripts/validate_xref.py   (findings 94, 95)

Each test asserts the CORRECTED behavior and embeds a guard that would have
failed against the pre-fix code:

  * F83  — validate_task_tool_prohibition must flag a context:fork agent that
           OMITS 'tools' (it inherits Task → infinite-recursion hazard), while
           leaving non-fork agents and fork agents with safe explicit tools clean.
  * F157 — validate_frontmatter_exists must still emit exactly one CRITICAL on
           malformed YAML; the removed dead-code branch never altered behavior.
  * F92  — the validate_rules.py CLI pre-check must accept a rules/ tree whose
           .md files live ONLY in subdirectories (rglob, matching the validator).
  * F164 — TOKEN_RATIO_KANA must be the reciprocal (within rounding) of the
           chars/token figure its comment now documents.
  * F94  — extract_script_paths_from_hooks must not double-extract the 'command'
           field (no duplicate-then-collapse), and must still find every path.
  * F95  — validate_version_sync must read a SKILL.md version even when a "---"
           substring appears inside a frontmatter value before the version line.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("CPV_SCAN_CACHE", "0")

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_agent import (  # noqa: E402
    AgentValidationReport,
    validate_frontmatter_exists,
    validate_task_tool_prohibition,
)
from validate_rules import TOKEN_RATIO_KANA, validate_rules_directory  # noqa: E402
from validate_xref import (  # noqa: E402
    CrossReferenceValidationReport,
    extract_script_paths_from_hooks,
    parse_yaml_frontmatter,
    validate_version_sync,
)


def _levels(report: object, level: str) -> list[str]:
    return [r.message for r in report.results if r.level == level]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Finding 83 — fork agent that omits 'tools' inherits Task (recursion hazard)
# ---------------------------------------------------------------------------
class TestForkInheritsTask:
    def test_fork_without_tools_field_is_flagged(self) -> None:
        """A context:fork agent with NO 'tools' field inherits Task → MAJOR.

        Pre-fix this returned early (tools is None) and emitted nothing, hiding
        the exact infinite-recursion case the check exists to catch.
        """
        report = AgentValidationReport()
        validate_task_tool_prohibition({"context": "fork"}, "agent.md", report)
        majors = _levels(report, "MAJOR")
        assert len(majors) == 1
        assert "infinite recursion" in majors[0]
        assert "inherits Task" in majors[0]

    def test_fork_with_explicit_task_still_flagged(self) -> None:
        """The original explicit-Task case stays flagged (no regression)."""
        report = AgentValidationReport()
        validate_task_tool_prohibition({"context": "fork", "tools": ["Task"]}, "agent.md", report)
        majors = _levels(report, "MAJOR")
        assert len(majors) == 1
        assert "infinite recursion" in majors[0]

    def test_fork_with_safe_tools_stays_clean(self) -> None:
        """A fork agent that explicitly lists safe tools (no Task) is clean."""
        report = AgentValidationReport()
        validate_task_tool_prohibition({"context": "fork", "tools": ["Read", "Bash"]}, "agent.md", report)
        assert _levels(report, "MAJOR") == []

    def test_non_fork_agent_without_tools_stays_clean(self) -> None:
        """A non-fork agent (no context:fork) has no recursion restriction even
        when it omits 'tools' — the fix must not over-broaden to non-subagents."""
        report = AgentValidationReport()
        validate_task_tool_prohibition({}, "agent.md", report)
        assert _levels(report, "MAJOR") == []

    def test_non_fork_agent_with_task_stays_clean(self) -> None:
        """A non-fork agent may legitimately hold Task (it is the orchestrator)."""
        report = AgentValidationReport()
        validate_task_tool_prohibition({"tools": "Task"}, "agent.md", report)
        assert _levels(report, "MAJOR") == []


# ---------------------------------------------------------------------------
# Finding 157 — removed dead-code branch must not change behavior
# ---------------------------------------------------------------------------
class TestFrontmatterExistsDeadCodeRemoval:
    def test_malformed_yaml_emits_single_critical(self) -> None:
        """Malformed YAML still produces exactly one 'Malformed YAML' CRITICAL
        and returns None — the only path that yields frontmatter is None."""
        report = AgentValidationReport()
        result = validate_frontmatter_exists("---\n: : [ : :\n---\nbody\n", report, "a.md")
        assert result is None
        crit = _levels(report, "CRITICAL")
        assert len(crit) == 1
        assert "Malformed YAML frontmatter" in crit[0]

    def test_valid_frontmatter_returns_dict(self) -> None:
        """A well-formed agent still parses to its frontmatter dict."""
        report = AgentValidationReport()
        result = validate_frontmatter_exists("---\nname: ok\n---\nbody\n", report, "a.md")
        assert result == {"name": "ok"}
        assert _levels(report, "CRITICAL") == []

    def test_no_frontmatter_emits_critical(self) -> None:
        """Content without an opening '---' still reports the missing-frontmatter CRITICAL."""
        report = AgentValidationReport()
        result = validate_frontmatter_exists("just a body, no frontmatter\n", report, "a.md")
        assert result is None
        assert any("No YAML frontmatter" in m for m in _levels(report, "CRITICAL"))


# ---------------------------------------------------------------------------
# Finding 92 — rules/ pre-check / validator must both be recursive
# ---------------------------------------------------------------------------
class TestRulesRecursiveDiscovery:
    def test_validator_finds_rules_in_subdirectory(self, tmp_path: Path) -> None:
        """validate_rules_directory (rglob) discovers a rule file nested in a
        subdirectory — the case the non-recursive CLI pre-check used to reject
        before being switched to rglob too.
        """
        rules_dir = tmp_path / "rules"
        nested = rules_dir / "subcat"
        nested.mkdir(parents=True)
        (nested / "deep-rule.md").write_text(
            "# Deep rule\n\nUse absolute imports for shared modules.\n",
            encoding="utf-8",
        )
        report = validate_rules_directory(rules_dir, plugin_root=tmp_path)
        infos = " ".join(r.message for r in report.results if r.level == "INFO")
        # The validator must have FOUND the nested file (1 rule file), proving
        # the recursive contract the CLI pre-check now matches.
        assert "Found 1 rule file" in infos
        assert "No rule file" not in infos


# ---------------------------------------------------------------------------
# Finding 164 — TOKEN_RATIO_KANA matches its documented chars/token figure
# ---------------------------------------------------------------------------
class TestKanaRatioConsistency:
    def test_kana_ratio_is_reciprocal_of_documented_chars_per_token(self) -> None:
        """0.7 tokens/char ⇔ ~1.43 chars/token (the value the comment now states).

        Pre-fix the comment claimed ~1.5 chars/token, whose reciprocal is 0.667,
        contradicting the 0.7 constant. Guard the relationship so the two cannot
        silently drift apart again.
        """
        chars_per_token = 1.0 / TOKEN_RATIO_KANA
        assert abs(chars_per_token - 1.43) < 0.01
        # And confirm 0.7 is the conservative (denser) side of the ~1.5 empirical
        # figure — it must overcount, i.e. tokens/char above the 1/1.5 reciprocal.
        assert TOKEN_RATIO_KANA > 1.0 / 1.5


# ---------------------------------------------------------------------------
# Finding 94 — hook command paths extracted exactly once
# ---------------------------------------------------------------------------
class TestHookScriptNoDoubleExtraction:
    def test_command_path_extracted_once(self) -> None:
        """A single 'command' hook yields its script path exactly once."""
        hooks = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"}]}
                ]
            }
        }
        result = extract_script_paths_from_hooks(hooks)
        assert result == ["scripts/foo.py"]

    def test_distinct_commands_all_found(self) -> None:
        """Removing the special-case must not drop any path: two distinct
        command scripts are both still extracted via the generic recursion."""
        hooks = {
            "a": {"command": "${CLAUDE_PLUGIN_ROOT}/scripts/a.py"},
            "b": {"command": "${CLAUDE_PLUGIN_ROOT}/scripts/b.py"},
        }
        assert sorted(extract_script_paths_from_hooks(hooks)) == ["scripts/a.py", "scripts/b.py"]

    def test_command_and_args_both_extracted(self) -> None:
        """A path in a sibling key of 'command' is reached by the recursion too."""
        hooks = {
            "h": {
                "command": "${CLAUDE_PLUGIN_ROOT}/scripts/cmd.py",
                "extra": "${CLAUDE_PLUGIN_ROOT}/scripts/extra.py",
            }
        }
        assert sorted(extract_script_paths_from_hooks(hooks)) == ["scripts/cmd.py", "scripts/extra.py"]


# ---------------------------------------------------------------------------
# Finding 95 — version sync uses the robust line-based frontmatter parser
# ---------------------------------------------------------------------------
class TestVersionSyncRobustFrontmatter:
    def _make_plugin(self, root: Path, plugin_version: str, skill_frontmatter: str) -> None:
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "demo", "version": "%s"}' % plugin_version, encoding="utf-8"
        )
        skill_dir = root / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(skill_frontmatter, encoding="utf-8")

    def test_version_read_despite_dash_in_value(self, tmp_path: Path) -> None:
        """A '---' inside a frontmatter VALUE before the version line must not
        truncate parsing: the SKILL.md version is still picked up and a true
        mismatch against plugin.json is reported.

        Pre-fix content.find('---', 3) stopped at the value's '---', dropping
        the version source entirely (mismatch silently hidden).
        """
        skill_md = (
            "---\n"
            'description: "use --- as a separator in docs"\n'
            "version: 2.0.0\n"
            "---\n"
            "Body.\n"
        )
        root = tmp_path / "demo"
        self._make_plugin(root, plugin_version="1.0.0", skill_frontmatter=skill_md)

        # Sanity: the robust parser sees the version despite the embedded '---'.
        assert parse_yaml_frontmatter(skill_md)["version"] == "2.0.0"

        report = CrossReferenceValidationReport()
        validate_version_sync(root, report)
        assert report.version_sources.get("skills/demo-skill/SKILL.md") == "2.0.0"
        assert report.version_sources.get("plugin.json") == "1.0.0"
        majors = _levels(report, "MAJOR")
        assert any("Version mismatch" in m for m in majors)

    def test_versions_agree_when_consistent(self, tmp_path: Path) -> None:
        """When plugin.json and SKILL.md agree, the sync check passes (no false
        MAJOR introduced by the parser swap)."""
        skill_md = "---\nname: demo-skill\nversion: 1.0.0\n---\nBody.\n"
        root = tmp_path / "demo"
        self._make_plugin(root, plugin_version="1.0.0", skill_frontmatter=skill_md)
        report = CrossReferenceValidationReport()
        validate_version_sync(root, report)
        assert report.version_sources.get("skills/demo-skill/SKILL.md") == "1.0.0"
        assert _levels(report, "MAJOR") == []
        assert any("version sources agree" in m for m in _levels(report, "PASSED"))
