#!/usr/bin/env python3
"""Regression-lock tests for the TRDD-71e68ab5 (v2.91.0) batch-fix landing.

These tests pin the behavioural commitments made when the parallel-shard
batch-fix protocol shipped. Each test should be obvious from its name;
the docstring explains the lesson learned that motivated it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Plugin-fixer stale claim regression
# ---------------------------------------------------------------------------


class TestPluginFixerNoSubagentClaim:
    """``plugin-fixer.md`` must NOT claim subagents can spawn subagents.

    Per the Anthropic subagent spec (https://code.claude.com/docs/en/sub-agents),
    a subagent cannot spawn other subagents — the Agent tool has no effect
    inside a subagent definition. The pre-v2.91.0 plugin-fixer body claimed
    the opposite, leading to silent runtime no-ops the user reported.
    """

    body = (REPO / "agents" / "plugin-fixer.md").read_text()

    def test_no_parallel_subagents_allowed_claim(self) -> None:
        assert "parallel subagents are allowed" not in self.body, (
            "plugin-fixer.md must NOT claim parallel subagents are allowed — "
            "subagents cannot spawn subagents per the Anthropic spec. "
            "This stale guidance was removed in TRDD-71e68ab5."
        )

    def test_recommends_batch_fix_for_large_batches(self) -> None:
        assert "/cpv-batch-fix" in self.body, (
            "plugin-fixer.md must point users at /cpv-batch-fix for very large batches — TRDD-71e68ab5."
        )

    def test_documents_cannot_spawn_subagents(self) -> None:
        # Allow several phrasings, but at least one must explicitly state
        # the constraint (catches sneaking the misinformation back in).
        assert re.search(
            r"CANNOT spawn (other )?agents|cannot spawn (other )?(sub)?agents|subagents cannot spawn",
            self.body,
            re.IGNORECASE,
        ), "plugin-fixer.md must explicitly document that subagents cannot spawn subagents (TRDD-71e68ab5)."


# ---------------------------------------------------------------------------
# Doctor maxTurns + batch handoff
# ---------------------------------------------------------------------------


class TestCpvDoctorMaxTurns:
    """The doctor needs enough turn budget to diagnose big plugins.

    The pre-v2.91.0 maxTurns of 30 was below what's needed to run validator
    + D1..D8 recipes + handoff recommendation. v2.91.0 bumped to 100.
    """

    doctor = REPO / "agents" / "cpv-doctor-agent.md"

    def test_max_turns_is_at_least_100(self) -> None:
        text = self.doctor.read_text()
        front_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        assert front_match is not None
        frontmatter = yaml.safe_load(front_match.group(1))
        assert frontmatter["maxTurns"] >= 100, (
            f"cpv-doctor-agent.md maxTurns must be >= 100 to cover validator + "
            f"D1..D8 + handoff, got {frontmatter['maxTurns']}"
        )

    def test_doctor_body_recommends_batch_for_big_plugins(self) -> None:
        body = self.doctor.read_text()
        assert "/cpv-batch-fix" in body, (
            "cpv-doctor-agent must point at /cpv-batch-fix when findings exceed the single-agent context budget."
        )
        assert re.search(r"recommend-batch-fix", body), (
            "Doctor must emit the recommend-batch-fix token so the orchestrator "
            "knows to render the batch-fix recommendation."
        )


# ---------------------------------------------------------------------------
# batch-fix-protocol skill exists + Loaded-by reference
# ---------------------------------------------------------------------------


class TestBatchFixProtocolSkill:
    """The skill must exist, be ``user-invocable: false``, and be Loaded-by-plugin-fixer."""

    skill = REPO / "skills" / "batch-fix-protocol" / "SKILL.md"

    def test_skill_file_exists(self) -> None:
        assert self.skill.is_file(), f"batch-fix-protocol skill missing at {self.skill}"

    def test_skill_is_not_user_invocable(self) -> None:
        text = self.skill.read_text()
        front_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        assert front_match is not None
        meta = yaml.safe_load(front_match.group(1))
        assert meta.get("user-invocable") is False, (
            "batch-fix-protocol must be user-invocable: false (it's a reference "
            "loaded by plugin-fixer + the slash command, not directly invoked)."
        )

    def test_skill_reachable_from_plugin_fixer(self) -> None:
        """v2.93.0 (TRDD-478d9687) removed per-agent preload lists in favour of
        the universal the-skills-menu. plugin-fixer no longer declares
        batch-fix-protocol directly; instead it loads it on demand via the
        Skill tool. The skill MUST appear in the the-skills-menu catalog so the
        agent knows it exists.
        """
        index_body = (REPO / "skills" / "the-skills-menu" / "SKILL.md").read_text()
        catalog_body = (REPO / "skills" / "the-skills-menu" / "references" / "skills-catalog.md").read_text()
        combined = index_body + "\n" + catalog_body
        assert "batch-fix-protocol" in combined, (
            "batch-fix-protocol must appear in the the-skills-menu catalog so "
            "plugin-fixer can pick it at runtime for batch_shard mode."
        )

    def test_skill_documents_three_json_shapes(self) -> None:
        """The skill must document the index.json, shard manifest, and status JSON shapes."""
        body = self.skill.read_text()
        assert "index.json" in body
        assert "shard-K.json" in body or "shard manifest" in body.lower()
        assert "shard-K.status.json" in body or "status JSON" in body.lower() or "shard status" in body.lower()


# ---------------------------------------------------------------------------
# Slash command frontmatter
# ---------------------------------------------------------------------------


class TestCpvBatchFixCommand:
    """``/cpv-batch-fix`` command sanity."""

    cmd = REPO / "commands" / "cpv-batch-fix.md"

    def test_command_file_exists(self) -> None:
        assert self.cmd.is_file(), f"cpv-batch-fix command missing at {self.cmd}"

    def test_command_is_user_invocable(self) -> None:
        text = self.cmd.read_text()
        front_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        assert front_match is not None
        meta = yaml.safe_load(front_match.group(1))
        assert meta.get("user-invocable") is True, "cpv-batch-fix is user-invocable — the doctor recommends it by name."

    def test_command_body_invokes_planner_and_aggregator(self) -> None:
        body = self.cmd.read_text()
        assert "cpv_batch_planner.py" in body
        assert "cpv_batch_aggregator.py" in body

    def test_command_body_documents_parallel_dispatch(self) -> None:
        body = self.cmd.read_text()
        # The command must explicitly explain that multiple Agent calls in
        # one message run in parallel (per the Claude Code spec).
        assert "parallel" in body.lower()
        assert "main session" in body.lower()


# ---------------------------------------------------------------------------
# No hardcoded iteration caps anywhere we deliberately removed them
# ---------------------------------------------------------------------------


class TestNoHardcodedIterationCaps:
    """User feedback (2026-05-19): no hardcoded iteration ceilings in fixer loops.

    The rationale: a 300-finding plugin legitimately needs 20+ iterations to
    fully converge. The only stop condition is convergence-to-zero or
    oscillation (finding set repeats).
    """

    forbidden = re.compile(
        r"(max(imum)? [0-9]+ iter|iteration cap is [0-9]+|"
        r"\b[0-9]+ iter(ation)?s? cap|capped at ~?[0-9]+ minutes?|"
        r"if iterations? reach [0-9]+)",
        re.IGNORECASE,
    )

    files = [
        REPO / "agents" / "plugin-fixer.md",
        REPO / "agents" / "marketplace-fixer.md",
        REPO / "skills" / "fix-validation" / "references" / "iterative-fix-loop.md",
    ]

    def test_no_hardcoded_iteration_caps(self) -> None:
        offenders: list[str] = []
        for f in self.files:
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if self.forbidden.search(line):
                    offenders.append(f"{f.relative_to(REPO)}:{i}: {line.strip()}")
        assert not offenders, (
            "Hardcoded iteration/time caps re-introduced — per user feedback "
            "the agent decides termination dynamically (convergence or "
            "oscillation), not via magic numbers:\n" + "\n".join(offenders)
        )


# ---------------------------------------------------------------------------
# Planner + aggregator scripts exist + execute
# ---------------------------------------------------------------------------


class TestBatchScripts:
    def test_planner_script_exists(self) -> None:
        assert (REPO / "scripts" / "cpv_batch_planner.py").is_file()

    def test_aggregator_script_exists(self) -> None:
        assert (REPO / "scripts" / "cpv_batch_aggregator.py").is_file()

    def test_planner_help_runs(self) -> None:
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "cpv_batch_planner.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0
        assert "shard" in result.stdout.lower()

    def test_aggregator_help_runs(self) -> None:
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "cpv_batch_aggregator.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0
        assert "shard" in result.stdout.lower() or "session" in result.stdout.lower()
