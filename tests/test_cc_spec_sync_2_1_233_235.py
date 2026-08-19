"""CC spec-drift sync for the v2.1.233 → v2.1.235 window.

Every assertion is two-sided where a rejection sibling exists: each "now
accepted / now detected" case is paired with a control proving the same code
path still rejects what it must reject.

The window's plugin-spec surface, verified against the RAW docs
(``tools-reference.md`` / ``settings.md`` / ``sub-agents.md``) by mechanical
set-diff rather than the changelog summary, per the recorded spec-drift method:

* **v2.1.224 ``ListAgents``** — in the tools-reference table but missing from
  ``VALID_TOOLS`` (surfaced by this window's set-diff, owed since 2.1.224).
* **v2.1.233 todo/task-tool model gating** — TodoWrite + TaskCreate/Get/List/
  Update are not provided on Opus 4.8 / Sonnet 5 / Fable 5 / Mythos 5+ unless
  the user opts in (``CLAUDE_CODE_ENABLE_TODO_TOOLS=1``); validate_agent now
  says so (WARNING for TodoWrite — disabled by default everywhere — and INFO
  for the four Task tools, still default-on for older models).
* **``SlashCommand`` / ``MCPSearch``** — no longer rows of the tools-reference
  table; demoted to the legacy-emits-WARNING bucket (kept in ``VALID_TOOLS``,
  mirroring MultiEdit/Notebook/TodoRead — not removed, because removal is only
  proven for tools the doc explicitly retired, per the TeamCreate precedent).
* **``CANONICAL_TOOLS`` backfill** — Artifact, ListAgents, ReportFindings,
  SendUserFile, Workflow (current tools whose permission globs would otherwise
  be mis-scanned as content) plus the retained-legacy names the set's own
  docstring promises to keep (MCPSearch, MultiEdit, Notebook, SlashCommand,
  TodoRead).
* **v2.1.235 ``spellcheck`` settings key** — now a row of settings.md's
  Available-settings table, plus the 59-key accumulated backfill the same
  set-diff surfaced (theme, verbose, fastMode, autoCompactEnabled, …).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cc_scope_rules  # noqa: E402
import cpv_tool_permission_match as tpm  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402
import validate_agent as va  # noqa: E402


def _agent_report_for_tools(tools: str) -> va.AgentValidationReport:
    """Run validate_tools_field on a minimal frontmatter carrying ``tools``."""
    report = va.AgentValidationReport()
    va.validate_tools_field({"tools": tools}, "agent.md", report)
    return report


def _messages(report: va.AgentValidationReport, level: str) -> list[str]:
    return [r.message for r in report.results if r.level == level]


class TestListAgentsTool:
    """v2.1.224 ListAgents is a current tools-reference row."""

    def test_listagents_valid(self) -> None:
        """ListAgents is accepted as a valid agent tool."""
        assert "ListAgents" in cvc.VALID_TOOLS

    def test_listagents_in_canonical(self) -> None:
        """ListAgents is recognised for permission-glob suppression."""
        assert "ListAgents" in tpm.CANONICAL_TOOLS

    def test_control_hallucinated_tool_still_rejected(self) -> None:
        """Control: a made-up tool name is still not valid."""
        assert "ListSessions" not in cvc.VALID_TOOLS
        assert "ListSessions" not in tpm.CANONICAL_TOOLS


class TestTodoToolModelGating:
    """v2.1.233 — todo/task tools absent on new models unless opted in."""

    def test_task_tools_stay_valid(self) -> None:
        """Availability is model-conditional, not removal: the tools stay VALID."""
        for t in ("TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TodoWrite"):
            assert t in cvc.VALID_TOOLS, t

    def test_todowrite_emits_warning(self) -> None:
        """TodoWrite (disabled by default in favor of Task tools) draws a WARNING."""
        report = _agent_report_for_tools("Read, TodoWrite")
        assert any("TodoWrite" in m and "CLAUDE_CODE_ENABLE_TODO_TOOLS" in m for m in _messages(report, "WARNING"))

    def test_taskcreate_emits_info_not_warning(self) -> None:
        """TaskCreate is still default-on for older models → INFO, not WARNING."""
        report = _agent_report_for_tools("Read, TaskCreate")
        assert any("TaskCreate" in m and "v2.1.233" in m for m in _messages(report, "INFO"))
        assert not any("TaskCreate" in m for m in _messages(report, "WARNING"))

    def test_control_ungated_tool_draws_no_gating_note(self) -> None:
        """Control: TaskStop/TaskOutput are NOT in the 2.1.233 gated set."""
        report = _agent_report_for_tools("Read, TaskStop")
        assert not any("TaskStop" in m and "v2.1.233" in m for m in _messages(report, "INFO"))


class TestSlashCommandMcpSearchLegacy:
    """SlashCommand/MCPSearch dropped from the tools-reference table."""

    def test_kept_in_valid_tools(self) -> None:
        """Not removed (runtime removal unproven) — kept, like MultiEdit."""
        assert "SlashCommand" in cvc.VALID_TOOLS
        assert "MCPSearch" in cvc.VALID_TOOLS

    def test_emit_legacy_warning(self) -> None:
        """Both now draw the not-in-current-spec WARNING."""
        for tool in ("SlashCommand", "MCPSearch"):
            report = _agent_report_for_tools(f"Read, {tool}")
            assert any(tool in m and "tools-reference" in m for m in _messages(report, "WARNING")), tool

    def test_control_current_tool_draws_no_legacy_warning(self) -> None:
        """Control: a current tool (Skill) draws no legacy warning."""
        report = _agent_report_for_tools("Read, Skill")
        assert not _messages(report, "WARNING")


class TestCanonicalToolsBackfill:
    """CANONICAL_TOOLS covers every current tools-reference row + retained legacy."""

    def test_current_tools_present(self) -> None:
        for t in ("Artifact", "ListAgents", "ReportFindings", "SendUserFile", "Workflow"):
            assert t in tpm.CANONICAL_TOOLS, t

    def test_retained_legacy_present(self) -> None:
        """The set's docstring promises legacy names stay recognised."""
        for t in ("MCPSearch", "MultiEdit", "Notebook", "SlashCommand", "TodoRead", "TeamCreate", "TeamDelete"):
            assert t in tpm.CANONICAL_TOOLS, t

    def test_valid_tools_minus_alias_subset_of_canonical(self) -> None:
        """Structural invariant: everything VALID (except the Task alias, which
        the alias map owns) is recognisable for permission-glob suppression."""
        missing = (cvc.VALID_TOOLS - {"Task"}) - tpm.CANONICAL_TOOLS
        assert not missing, f"VALID_TOOLS entries unknown to CANONICAL_TOOLS: {sorted(missing)}"

    def test_control_task_alias_not_duplicated(self) -> None:
        """Control: Task stays an alias (TOOL_ALIASES), not a canonical entry."""
        assert "Task" not in tpm.CANONICAL_TOOLS
        assert tpm.TOOL_ALIASES["Task"] == "Agent"


class TestSettingsKeysBackfill:
    """v2.1.235 spellcheck + the Available-settings table backfill."""

    def test_spellcheck_known(self) -> None:
        assert "spellcheck" in cc_scope_rules.KNOWN_SETTINGS_KEYS

    def test_backfilled_sample_known(self) -> None:
        for k in (
            "theme",
            "verbose",
            "fastMode",
            "autoCompactEnabled",
            "fallbackModel",
            "availableModels",
            "autoUpdatesChannel",
            "defaultShell",
            "voice",
            "sshConfigs",
        ):
            assert k in cc_scope_rules.KNOWN_SETTINGS_KEYS, k

    def test_control_nested_subkey_still_unknown(self) -> None:
        """Control: nested sub-keys stay out — the set is top-level only."""
        for k in ("strictAllowlist", "bwrapPath", "socatPath", "enabled"):
            assert k not in cc_scope_rules.KNOWN_SETTINGS_KEYS, k

    def test_control_typo_still_unknown(self) -> None:
        """Control: a plausible typo is still flagged as unknown."""
        assert "spellCheck" not in cc_scope_rules.KNOWN_SETTINGS_KEYS
        assert "fastmode" not in cc_scope_rules.KNOWN_SETTINGS_KEYS
