#!/usr/bin/env python3
"""Issue #97 — third-person role-def must not be reported as a missing role-def.

The role-definition check in ``validate_agent.validate_body_content`` used a bare
``"you are" not in body_text.lower()`` substring test. An agent whose role is
written in the THIRD person (a ``## Identity`` section, or a "The <Name> Agent is
a …" sentence) has no ``"you are"`` and was therefore warned that the role
definition was "missing" — a false positive, because a substantive role-def WAS
present.

These tests drive the REAL ``validate_agent(Path)`` end-to-end on real agent
files written to disk (NO mocks). Two-sided:

* (a) FP fixture: ``## Identity`` + "The X Agent is a … agent" → there is NO
  finding claiming the role-def is missing; instead a non-blocking INFO advisory
  recommends second person.
* (b) Real fixture: an agent body with NO role definition at all (no "you are",
  no ``## Identity`` heading, no "is a … agent"/"Acts as …" sentence) → the
  WARNING about a missing role definition STILL fires.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_agent import validate_agent  # noqa: E402

# The exact substring the role-def WARNING messages share. Both the
# second-person-only and the "neither form" warnings contain this phrase, so a
# match means the validator claimed a role definition is absent / should be
# added.
_MISSING_ROLEDEF_MARKER = "should include a role definition"


def _write_agent(tmp_path: Path, name: str, body: str) -> Path:
    """Write a minimal-but-valid agent .md file and return its path."""
    agent_path = tmp_path / f"{name}.md"
    agent_path.write_text(body, encoding="utf-8")
    return agent_path


def _messages_for_level(report, level: str) -> list[str]:
    return [r.message for r in report.results if r.level == level]


# ---------------------------------------------------------------------------
# (a) FALSE-POSITIVE side: a third-person role-def must NOT be flagged absent
# ---------------------------------------------------------------------------

THIRD_PERSON_AGENT = """\
---
name: doc-writer
description: Use this agent to transform technical specifications into clear markdown documentation for the project when asked.
---
## Identity

The Documentation Writer Agent is a specialized LOCAL HELPER AGENT that transforms
technical requirements, specifications, and architectural decisions into clear,
comprehensive markdown documentation. It owns the docs/ tree and keeps it current.

## Workflow
1. Read the spec.
2. Draft the documentation.
3. Review and refine for clarity.
"""


def test_third_person_roledef_is_not_reported_missing(tmp_path: Path) -> None:
    """A `## Identity` + 'The X Agent is a … agent' body is NOT warned as missing a role-def."""
    agent_path = _write_agent(tmp_path, "doc-writer", THIRD_PERSON_AGENT)
    report = validate_agent(agent_path)

    # No finding (at ANY severity) may claim the role definition is missing.
    offending = [r for r in report.results if _MISSING_ROLEDEF_MARKER in r.message]
    assert offending == [], (
        "third-person role-def falsely reported as missing: "
        f"{[(r.level, r.message) for r in offending]}"
    )


def test_third_person_roledef_emits_nonblocking_info_advisory(tmp_path: Path) -> None:
    """A third-person role-def emits a non-blocking INFO advising second person."""
    agent_path = _write_agent(tmp_path, "doc-writer", THIRD_PERSON_AGENT)
    report = validate_agent(agent_path)

    infos = _messages_for_level(report, "INFO")
    assert any("third person" in m for m in infos), (
        f"expected a third-person INFO advisory; INFO messages were: {infos}"
    )
    # INFO never blocks (even in --strict): exit_code_strict must not flag it.
    assert report.exit_code_strict() == 0, (
        "third-person role-def advisory must be non-blocking"
    )


# A `## Identity` heading alone (without an explicit '... agent ...' sentence) is
# also a recognized third-person identity form and must not be reported missing.
IDENTITY_HEADING_ONLY_AGENT = """\
---
name: builder
description: Use this agent when you need to compile the project and surface the build outcome to the user clearly.
---
## Identity

Acts as the build coordinator. Owns the compilation pipeline and reports the
final pass/fail status with the relevant log excerpts.

## Workflow
1. Run the build.
2. Capture and summarize the result.
"""


def test_identity_heading_form_is_not_reported_missing(tmp_path: Path) -> None:
    """An `## Identity` heading + 'Acts as the …' body is not warned as missing a role-def."""
    agent_path = _write_agent(tmp_path, "builder", IDENTITY_HEADING_ONLY_AGENT)
    report = validate_agent(agent_path)

    offending = [r for r in report.results if _MISSING_ROLEDEF_MARKER in r.message]
    assert offending == [], (
        "identity-heading role-def falsely reported as missing: "
        f"{[(r.level, r.message) for r in offending]}"
    )


# ---------------------------------------------------------------------------
# Sanity: second-person role-def still PASSES (happy path unchanged)
# ---------------------------------------------------------------------------

SECOND_PERSON_AGENT = """\
---
name: reviewer
description: Use this agent when the user asks for a focused code review of recently modified files in the repo.
---
You are a meticulous code reviewer. You read the diff, flag correctness bugs,
and report findings concisely to the user.

## Workflow
1. Read the diff.
2. Report findings.
"""


def test_second_person_roledef_passes(tmp_path: Path) -> None:
    """A 'You are …' body produces a PASSED role-def check and no missing-role warning."""
    agent_path = _write_agent(tmp_path, "reviewer", SECOND_PERSON_AGENT)
    report = validate_agent(agent_path)

    passed = _messages_for_level(report, "PASSED")
    assert any("Role definition present" in m for m in passed), (
        f"expected a PASSED role-def check; PASSED messages were: {passed}"
    )
    offending = [r for r in report.results if _MISSING_ROLEDEF_MARKER in r.message]
    assert offending == [], (
        "second-person role-def falsely reported as missing: "
        f"{[(r.level, r.message) for r in offending]}"
    )


# ---------------------------------------------------------------------------
# (b) REAL side: an agent with NO role definition is STILL flagged
# ---------------------------------------------------------------------------

NO_ROLEDEF_AGENT = """\
---
name: no-role
description: Use this agent when you need to run a sequence of generic build steps and report the outcome to the user.
---
## Workflow
1. Run the build.
2. Run the tests.
3. Report pass/fail.

Always use absolute paths. Capture stdout to a file. Return a one-line summary.
"""


def test_agent_with_no_roledef_is_still_warned(tmp_path: Path) -> None:
    """An agent with no 'you are', no `## Identity`, no '… agent' sentence is STILL warned."""
    agent_path = _write_agent(tmp_path, "no-role", NO_ROLEDEF_AGENT)
    report = validate_agent(agent_path)

    warnings = _messages_for_level(report, "WARNING")
    assert any(_MISSING_ROLEDEF_MARKER in m for m in warnings), (
        "a genuinely role-def-less agent must still be warned; "
        f"WARNING messages were: {warnings}"
    )
    # And it must NOT have been falsely PASSED or reduced to a third-person INFO.
    passed = _messages_for_level(report, "PASSED")
    assert not any("Role definition present" in m for m in passed), (
        "a role-def-less agent must not be reported as having a role definition"
    )
    infos = _messages_for_level(report, "INFO")
    assert not any("third person" in m for m in infos), (
        "a role-def-less agent must not be reduced to a third-person advisory"
    )
