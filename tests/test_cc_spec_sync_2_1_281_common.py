"""CC spec sync v2.1.258–2.1.281 — shared constants, agents, skills (WP3).

Every accept/relax assertion is paired with a control proving the same code
path still rejects what it should, so no assertion passes vacuously.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_tool_permission_match as tpm  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402
from validate_agent import (  # noqa: E402
    PLUGIN_SHIPPED_AGENT_ALLOWED_FIELDS,
    AgentValidationReport,
    validate_agent,
    validate_omit_claude_md_field,
    validate_tools_field,
)


def _levels(report: AgentValidationReport, level: str) -> list[str]:
    return [r.message for r in report.results if r.level == level]


# 1. SubagentHandback tool -------------------------------------------------------


def test_subagent_handback_is_a_valid_and_canonical_tool() -> None:
    assert "SubagentHandback" in cvc.VALID_TOOLS
    assert "SubagentHandback" in tpm.CANONICAL_TOOLS


def test_subagent_handback_draws_no_unknown_tool_note_but_bogus_does() -> None:
    ok = AgentValidationReport()
    validate_tools_field({"tools": ["Read", "SubagentHandback"]}, "a.md", ok)
    assert not any("SubagentHandback" in m for m in _levels(ok, "INFO"))
    bad = AgentValidationReport()
    validate_tools_field({"tools": ["Read", "SubagentHandbak"]}, "a.md", bad)
    assert any("SubagentHandbak" in m for m in _levels(bad, "INFO"))


# 2. Built-in slash commands -----------------------------------------------------


def test_new_builtin_commands_present() -> None:
    for name in ("output-style", "skill-doctor", "update-config", "workflow-authoring", "design"):
        assert name in cvc.BUILTIN_SLASH_COMMANDS, name


def test_non_builtin_name_not_in_builtin_set() -> None:
    assert "my-plugin-deploy" not in cvc.BUILTIN_SLASH_COMMANDS


# 3. Segment-aware traversal helper ---------------------------------------------


def test_path_has_traversal_segment_semantics() -> None:
    assert not cvc.path_has_traversal("./plugins/..tools")
    assert not cvc.path_has_traversal("a/b..c/d")
    assert cvc.path_has_traversal("../x")
    assert cvc.path_has_traversal("a/../b")
    assert cvc.path_has_traversal("a\\..\\b")
    assert cvc.path_has_traversal("..")
    assert not cvc.path_has_traversal(None)
    assert not cvc.path_has_traversal(42)


# 4. PermissionRequest disallows agent hooks -------------------------------------


def test_permission_request_disallows_agent_hooks() -> None:
    allowed = cvc.hook_types_allowed_for_event("PermissionRequest")
    assert "agent" not in allowed
    assert allowed == frozenset({"command", "http", "mcp_tool", "prompt"})


def test_tier1_events_still_allow_agent_hooks() -> None:
    # Control: the exclusion is scoped to PermissionRequest only.
    for ev in ("PreToolUse", "Stop", "PermissionDenied"):
        assert "agent" in cvc.hook_types_allowed_for_event(ev), ev


# 5. omitClaudeMd + plugin-shipped fields ----------------------------------------


def test_omit_claude_md_bool_passes_non_bool_majors() -> None:
    ok = AgentValidationReport()
    validate_omit_claude_md_field({"omitClaudeMd": True}, "a.md", ok)
    assert not _levels(ok, "MAJOR")
    bad = AgentValidationReport()
    validate_omit_claude_md_field({"omitClaudeMd": "sometimes"}, "a.md", bad)
    assert any("omitClaudeMd" in m for m in _levels(bad, "MAJOR"))


def test_plugin_shipped_set_matches_doc_l72() -> None:
    doc_l72 = {
        "name",
        "description",
        "model",
        "effort",
        "maxTurns",
        "tools",
        "disallowedTools",
        "skills",
        "memory",
        "background",
        "omitClaudeMd",
        "isolation",
        "color",
        "experimental",
    }
    assert doc_l72 <= PLUGIN_SHIPPED_AGENT_ALLOWED_FIELDS


def _plugin_agent(tmp_path: Path, body: str) -> Path:
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "plug", "version": "1.0.0"}')
    (root / "agents").mkdir()
    p = root / "agents" / "a.md"
    p.write_text(body)
    return p


def test_plugin_agent_with_omit_claude_md_is_clean(tmp_path: Path) -> None:
    p = _plugin_agent(tmp_path, "---\nname: a\ndescription: An agent.\nomitClaudeMd: true\n---\n\n# A\n\nBody.\n")
    report = validate_agent(p)
    hits = [
        r.message for r in report.results if r.level in ("WARNING", "MINOR", "MAJOR") and "omitClaudeMd" in r.message
    ]
    assert not hits, hits


def test_plugin_agent_with_user_invocable_still_minors(tmp_path: Path) -> None:
    p = _plugin_agent(tmp_path, "---\nname: a\ndescription: An agent.\nuser-invocable: true\n---\n\n# A\n\nBody.\n")
    report = validate_agent(p)
    assert any("'user-invocable'" in m and "plugin-shipped" in m for m in _levels(report, "MINOR"))


# 6. TaskOutput removed / Todo gating wording ------------------------------------


def test_task_output_warning_says_removed_and_stays_warning() -> None:
    r = AgentValidationReport()
    validate_tools_field({"tools": ["Read", "TaskOutput"]}, "a.md", r)
    assert any("TaskOutput" in m and "v2.1.277" in m for m in _levels(r, "WARNING"))
    assert not any("TaskOutput" in m for m in _levels(r, "MINOR") + _levels(r, "MAJOR"))


def test_todo_gating_uses_positive_model_list() -> None:
    r = AgentValidationReport()
    validate_tools_field({"tools": ["Read", "TaskCreate"]}, "a.md", r)
    assert any("Opus 4.0–4.7" in m for m in _levels(r, "INFO"))
    control = AgentValidationReport()
    validate_tools_field({"tools": ["Read", "TaskStop"]}, "a.md", control)
    assert not any("Opus 4.0–4.7" in m for m in _levels(control, "INFO"))


# 7. Curated env vars --------------------------------------------------------------


def test_curated_env_vars_added_and_skipped_ones_absent() -> None:
    for v in (
        "CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH",
        "CLAUDE_CODE_MCP_STARTUP_WAIT_MS",
        "CLAUDE_CODE_WEBFETCH_DEADLINE_MS",
        "CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS",
    ):
        assert v in cvc.VALID_PLUGIN_ENV_VARS, v
    assert "CLAUDE_CODE_AUTO_MODE_SERVER" not in cvc.VALID_PLUGIN_ENV_VARS


# 8. Model ids -------------------------------------------------------------------


def test_new_model_ids_valid_and_bogus_rejected() -> None:
    for m in ("claude-opus-5-5", "claude-fable-5-1", "claude-opus-5-5[1m]"):
        assert cvc.is_valid_model(m), m
    for m in ("claude-gpt-5-5", "claude-opus", "opus-5-5"):
        assert not cvc.is_valid_model(m), m
