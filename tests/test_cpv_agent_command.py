"""Tests for the ``/cpv-agent`` direct-entry slash command (user directive 2026-07-23).

The command adds a user-facing slash surface on top of the already-dispatchable
``claude-plugins-validation:cpv-agent`` agent. These tests pin the invariants that
make ``/cpv-agent <request>`` work AND keep it clean under CPV's own validator:

- the command file exists and is ``user-invocable: true`` (the field that grants
  the user slash surface — there is no "agent-invocable" field; agent dispatch is
  implicit from the file living in ``agents/``);
- its body dispatches to the ``claude-plugins-validation:cpv-agent`` subagent
  (body-dispatch, matching the batch-command precedent — NOT a legacy ``agent:``
  frontmatter field);
- the agent it targets actually exists;
- ``validate_command`` reports zero CRITICAL/MAJOR findings on it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_validation_common import parse_frontmatter  # noqa: E402
from validate_command import validate_command  # noqa: E402

COMMAND_FILE = REPO_ROOT / "commands" / "cpv-agent.md"
AGENT_FILE = REPO_ROOT / "agents" / "cpv-agent.md"


def _frontmatter() -> dict:
    fm, _body, _end = parse_frontmatter(COMMAND_FILE.read_text(encoding="utf-8"))
    assert fm is not None, "commands/cpv-agent.md has no parseable YAML frontmatter"
    return fm


def test_command_file_exists() -> None:
    """The /cpv-agent command file must exist for the user slash surface to work."""
    assert COMMAND_FILE.is_file(), f"{COMMAND_FILE} is missing — /cpv-agent is unreachable."


def test_command_is_user_invocable() -> None:
    """`user-invocable: true` is the field that grants the user-facing slash surface."""
    assert _frontmatter().get("user-invocable") is True, (
        "commands/cpv-agent.md MUST declare `user-invocable: true` so the user can "
        "type /cpv-agent. There is no 'agent-invocable' field; agent dispatch is "
        "implicit from agents/cpv-agent.md existing."
    )


def test_command_name_matches_stem() -> None:
    """The `name` field must equal the file stem so the slash command resolves."""
    assert _frontmatter().get("name") == "cpv-agent"


def test_command_body_dispatches_to_the_agent() -> None:
    """The body must dispatch to the cpv-agent subagent (body-dispatch, no `agent:` field)."""
    body = COMMAND_FILE.read_text(encoding="utf-8")
    assert "claude-plugins-validation:cpv-agent" in body, (
        "The command body must dispatch to the `claude-plugins-validation:cpv-agent` "
        "subagent — that is what makes /cpv-agent reach the worker."
    )
    assert "$ARGUMENTS" in body, "The command must forward the user's request via $ARGUMENTS."


def test_command_does_not_use_legacy_agent_field() -> None:
    """Dispatch is via the body, not a legacy `agent:` frontmatter field.

    CC does not reliably auto-forward on the `agent:` field, and CPV's v2.90.0
    consolidation asserts no command delegates via frontmatter, so /cpv-agent
    uses the same body-dispatch pattern as the batch commands.
    """
    assert "agent" not in _frontmatter(), (
        "commands/cpv-agent.md must NOT use a legacy `agent:` frontmatter field; "
        "dispatch is done by the body via the Agent tool."
    )


def test_target_agent_exists() -> None:
    """The agent the command dispatches to must exist."""
    assert AGENT_FILE.is_file(), (
        f"{AGENT_FILE} is missing — /cpv-agent would dispatch to a nonexistent subagent."
    )


def test_validate_command_reports_no_blocking_findings() -> None:
    """CPV's own command validator must find zero CRITICAL/MAJOR on the command."""
    report = validate_command(COMMAND_FILE)
    critical = [r for r in report.results if r.level == "CRITICAL"]
    major = [r for r in report.results if r.level == "MAJOR"]
    assert not critical and not major, (
        f"validate_command flagged /cpv-agent. CRITICAL={[r.message for r in critical]} "
        f"MAJOR={[r.message for r in major]}"
    )
