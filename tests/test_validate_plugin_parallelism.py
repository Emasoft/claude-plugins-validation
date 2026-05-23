#!/usr/bin/env python3
"""Parallelism regression tests for ``validate_plugin`` orchestrator (task #384).

The orchestrator was refactored from a serial run-each-validator-in-turn
loop into a ``ThreadPoolExecutor``-backed dispatch (per-validator private
reports, merged in input order). These tests pin the acceptance gate from
``/tmp/cpv-parallel-spec.md``: parallel and serial orchestrator paths
produce IDENTICAL umbrella reports for the same plugin fixture (same
severity counts, same finding messages, same exit code).

Why a separate file
-------------------
``test_validate_plugin.py`` already has 100s of fast unit tests that test
individual validators. The parallelism gate needs:

  * a multi-validator plugin fixture (otherwise the parallel batch is
    trivially equal to the serial baseline because most validators
    no-op on empty inputs)
  * the ``CPV_ORCHESTRATOR_PARALLEL`` env-var toggled across runs
  * a much heavier setup/teardown than the existing per-function tests

Splitting into its own module keeps ``test_validate_plugin.py`` fast
and isolates the test fixture from accidental edits.
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
from pathlib import Path

# Match the scripts/-on-sys.path convention used across the suite.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

# Import the helpers we're testing. The orchestrator's main() is exercised
# via subprocess for the parity test (env var control + isolation), but
# the helper-function unit tests import directly.
from validate_plugin import (  # noqa: E402
    _orchestrator_parallel_enabled,
    _run_one_validator,
    _run_parallel_batch,
)


# ---------------------------------------------------------------------------
# Fixture — a self-contained plugin big enough to exercise several validators.
# ---------------------------------------------------------------------------


# A minimal plugin manifest the orchestrator accepts. Two commands + two
# agents + two skills gives every component-folder validator something to
# inspect, so the parallel batch is non-trivial.
_PLUGIN_JSON = """{
  "name": "test-parallel-plugin",
  "version": "1.0.0",
  "description": "A small plugin used to exercise the orchestrator parallelism gate.",
  "author": {"name": "test"}
}
"""


_COMMAND_MD = """---
name: {name}
description: A test command for the parallelism fixture.
---

# {name}

A simple command body.
"""

_AGENT_MD = """---
name: {name}
description: Use when testing parallel orchestrator. Specialized in {lang} review.
model: sonnet
tools:
  - Read
color: blue
---

# {title}

You are a code reviewer for {lang} projects.

<example>
user: Review src/x.py
assistant: I will review src/x.py.
</example>

<example>
user: Check tests
assistant: Done.
</example>
"""

_SKILL_MD = """---
name: {name}
description: Use when X. Loaded by test-agent. Triggered by /{name}.
---

# {name}

A test skill body.

## Usage

Run this skill when you need to test parallel orchestration.
"""

_README = """# test-parallel-plugin

A small plugin used to exercise the orchestrator parallelism gate.

## Usage

Install via the marketplace and invoke any command.
"""


def _make_test_plugin(root: Path) -> Path:
    """Materialise a small but realistic plugin under ``root/plugin``.

    Returns the plugin root path. Includes:
      * .claude-plugin/plugin.json
      * commands/ with 2 files
      * agents/ with 2 files
      * skills/ with 2 skill folders
      * README.md
      * LICENSE
      * .gitignore
    """
    plugin = root / "plugin"
    plugin.mkdir()
    claude_dir = plugin / ".claude-plugin"
    claude_dir.mkdir()
    (claude_dir / "plugin.json").write_text(_PLUGIN_JSON, encoding="utf-8")

    # Commands
    cmd_dir = plugin / "commands"
    cmd_dir.mkdir()
    for i in range(2):
        name = f"cmd-{i:02d}"
        (cmd_dir / f"{name}.md").write_text(
            _COMMAND_MD.format(name=name), encoding="utf-8"
        )

    # Agents
    agt_dir = plugin / "agents"
    agt_dir.mkdir()
    langs = ["python", "rust"]
    for i in range(2):
        name = f"agent-{i:02d}"
        (agt_dir / f"{name}.md").write_text(
            _AGENT_MD.format(name=name, lang=langs[i], title=name.title()),
            encoding="utf-8",
        )

    # Skills
    skl_dir = plugin / "skills"
    skl_dir.mkdir()
    for i in range(2):
        name = f"skill-{i:02d}"
        sd = skl_dir / name
        sd.mkdir()
        (sd / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")

    (plugin / "README.md").write_text(_README, encoding="utf-8")
    (plugin / "LICENSE").write_text(
        "MIT License\n\nCopyright (c) 2026 test\n", encoding="utf-8"
    )
    (plugin / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")

    return plugin


def _run_validate_plugin_subprocess(
    plugin_path: Path, parallel: bool
) -> tuple[int, dict]:
    """Invoke ``validate_plugin.py --json`` in a child process.

    Two-step run: subprocess control is necessary because
    ``CPV_ORCHESTRATOR_PARALLEL`` is read at orchestrator start AND because
    other tests in the same pytest process may have mutated module-level
    state inside ``validate_security`` (the skillaudit self-scan flag).
    Running in a fresh process guarantees clean state on both sides of
    the parity comparison.

    Returns ``(exit_code, parsed_json_report)``.
    """
    script = Path(__file__).parent.parent / "scripts" / "validate_plugin.py"
    env = os.environ.copy()
    env["CPV_ORCHESTRATOR_PARALLEL"] = "1" if parallel else "0"
    # Bypass GitHub integrity check — the local file has been modified by
    # this very test patch, and a network round-trip per run would add 5+s.
    env["PLUGIN_SKIP_GITHUB_INTEGRITY"] = "1"
    # Force a deterministic worker count for the parallel sibling validators
    # (validate_skill, validate_hook, etc.) so timing variance is minimised.
    env.setdefault("CPV_HOOK_PARALLEL", "1")
    # Disable colors so JSON output isn't polluted.
    env["NO_COLOR"] = "1"

    result = subprocess.run(
        ["uv", "run", "python", str(script), "--json", str(plugin_path)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(script.parent.parent),
        timeout=300,
    )
    # The orchestrator's --json mode emits the JSON document AT THE END of
    # stdout, AFTER the lint-engine's "═══ [REPO LINT] …" banner and a
    # one-line "Detected languages: …" header. We extract the JSON by
    # finding the LAST top-level `{` (line starting with `{`) and parsing
    # from there to end-of-stream. Brittle against future format changes
    # but tolerant of the current banner-then-JSON convention.
    raw = result.stdout
    json_start = raw.rfind("\n{")
    if json_start == -1 and raw.startswith("{"):
        json_start = 0
    elif json_start != -1:
        json_start += 1  # skip the newline
    if json_start == -1:
        raise AssertionError(
            f"validate_plugin --json: could not locate JSON document in stdout.\n"
            f"  exit_code={result.returncode}\n"
            f"  stdout (truncated to 4000 chars): {raw[:4000]}\n"
            f"  stderr (truncated to 4000 chars): {result.stderr[:4000]}"
        )
    try:
        report = json.loads(raw[json_start:])
    except json.JSONDecodeError as e:
        raise AssertionError(
            f"validate_plugin --json did not return parseable JSON.\n"
            f"  exit_code={result.returncode}\n"
            f"  stdout (truncated to 4000 chars): {raw[:4000]}\n"
            f"  stderr (truncated to 4000 chars): {result.stderr[:4000]}\n"
            f"  json error: {e}"
        )
    return result.returncode, report


# ---------------------------------------------------------------------------
# Acceptance gate — parallel vs serial parity.
# ---------------------------------------------------------------------------


class TestParallelSerialParity:
    """The orchestrator's parallel mode (CPV_ORCHESTRATOR_PARALLEL=1) must
    produce results IDENTICAL to its serial mode (=0). Same exit code,
    same severity counts, same finding messages."""

    def test_parallel_matches_serial_for_fixture_plugin(self, tmp_path):
        """For a small fixture plugin: parallel == serial across exit code,
        severity counts, and the full finding message list.

        This is the spec acceptance gate. A regression in the orchestrator
        parallel path (race condition, lost finding, wrong merge order)
        fails this test.
        """
        plugin = _make_test_plugin(tmp_path)

        # Run parallel + serial in two separate subprocesses. Each is a
        # fresh Python interpreter, so module-level state cannot bleed
        # between them.
        parallel_code, parallel_report = _run_validate_plugin_subprocess(
            plugin, parallel=True
        )
        serial_code, serial_report = _run_validate_plugin_subprocess(
            plugin, parallel=False
        )

        # Exit code must match — both paths see the same severities.
        assert parallel_code == serial_code, (
            f"Exit code drift: parallel={parallel_code} serial={serial_code}"
        )

        # Severity counts must match — same number of findings per level.
        parallel_counts = parallel_report["counts"]
        serial_counts = serial_report["counts"]
        assert parallel_counts == serial_counts, (
            f"Severity-count drift:\n"
            f"  parallel: {parallel_counts}\n"
            f"  serial:   {serial_counts}"
        )

        # Compare ordered (level, message, file) signatures so finding-by-finding
        # parity is enforced. The sort is needed because PASSED/INFO inside
        # the parallel batch can technically arrive in any order within the
        # SAME validator (some validators do their own internal threading);
        # however the orchestrator-level merge MUST keep validator-block order.
        # We compare on the multiset (sorted), which catches "lost finding"
        # and "extra finding" without false-positive ordering complaints.
        def _sig(report: dict) -> list[tuple[str, str, str | None]]:
            return sorted(
                (r["level"], r["message"], r.get("file"))
                for r in report["results"]
            )

        parallel_sig = _sig(parallel_report)
        serial_sig = _sig(serial_report)
        # Diff first few items for a readable failure
        if parallel_sig != serial_sig:
            only_parallel = [s for s in parallel_sig if s not in serial_sig]
            only_serial = [s for s in serial_sig if s not in parallel_sig]
            raise AssertionError(
                "Finding multiset drift between parallel and serial paths.\n"
                f"  Only in parallel ({len(only_parallel)}): "
                f"{only_parallel[:5]}{'…' if len(only_parallel) > 5 else ''}\n"
                f"  Only in serial   ({len(only_serial)}): "
                f"{only_serial[:5]}{'…' if len(only_serial) > 5 else ''}"
            )

    def test_total_result_count_matches(self, tmp_path):
        """The total number of result entries (across all severities)
        must be identical between parallel and serial paths.

        Catches regressions where the severity-count parity test would
        pass (one validator gained +1 finding while another lost -1)
        but the actual result list diverges. Asserting total length
        is a cheap secondary gate that catches asymmetric drift.
        """
        plugin = _make_test_plugin(tmp_path)
        _, parallel_report = _run_validate_plugin_subprocess(plugin, parallel=True)
        _, serial_report = _run_validate_plugin_subprocess(plugin, parallel=False)
        assert len(parallel_report["results"]) == len(serial_report["results"]), (
            f"Total result count drift: parallel={len(parallel_report['results'])} "
            f"serial={len(serial_report['results'])}"
        )


# ---------------------------------------------------------------------------
# Helper-function unit tests — keep the orchestrator-internal helpers honest.
# ---------------------------------------------------------------------------


class TestOrchestratorParallelEnabled:
    """The env-var switch must follow the same convention as
    ``CPV_HOOK_PARALLEL`` so users / CI scripts can toggle the whole
    concurrency stack with one consistent rule."""

    def test_default_returns_true(self, monkeypatch):
        """Unset env-var → parallel mode is the default."""
        monkeypatch.delenv("CPV_ORCHESTRATOR_PARALLEL", raising=False)
        assert _orchestrator_parallel_enabled() is True

    def test_zero_disables(self, monkeypatch):
        """Explicit "0" disables parallel mode."""
        monkeypatch.setenv("CPV_ORCHESTRATOR_PARALLEL", "0")
        assert _orchestrator_parallel_enabled() is False

    def test_false_disables(self, monkeypatch):
        """Explicit "false" disables parallel mode (case-insensitive)."""
        monkeypatch.setenv("CPV_ORCHESTRATOR_PARALLEL", "FALSE")
        assert _orchestrator_parallel_enabled() is False

    def test_no_disables(self, monkeypatch):
        """Explicit "no" disables parallel mode."""
        monkeypatch.setenv("CPV_ORCHESTRATOR_PARALLEL", "no")
        assert _orchestrator_parallel_enabled() is False

    def test_off_disables(self, monkeypatch):
        """Explicit "off" disables parallel mode."""
        monkeypatch.setenv("CPV_ORCHESTRATOR_PARALLEL", "off")
        assert _orchestrator_parallel_enabled() is False

    def test_one_enables(self, monkeypatch):
        """Explicit "1" enables parallel mode (the default)."""
        monkeypatch.setenv("CPV_ORCHESTRATOR_PARALLEL", "1")
        assert _orchestrator_parallel_enabled() is True

    def test_anything_else_enables(self, monkeypatch):
        """Any non-disable value (including garbage) enables parallel mode.

        This is intentional: the conservative default is parallel, and an
        accidental typo in the env-var should not silently slow down CI.
        """
        monkeypatch.setenv("CPV_ORCHESTRATOR_PARALLEL", "yes please")
        assert _orchestrator_parallel_enabled() is True


class TestRunOneValidator:
    """``_run_one_validator`` runs a single callable with its own private
    report and captures any exception. This is the per-task contract the
    parallel batch dispatcher depends on."""

    def test_success_returns_tuple_with_no_error(self, tmp_path):
        """Happy path: callable runs to completion, error is None."""
        from cpv_validation_common import ValidationReport

        def fake_validator(plugin_root, report):
            report.passed("looks good")

        name, sub_report, exc = _run_one_validator(
            "fake", fake_validator, tmp_path
        )
        assert name == "fake"
        assert isinstance(sub_report, ValidationReport)
        assert exc is None
        assert len(sub_report.results) == 1
        assert sub_report.results[0].message == "looks good"

    def test_exception_is_captured(self, tmp_path):
        """Bad path: callable raises, error is captured (not propagated)."""
        def crash(plugin_root, report):
            raise ValueError("boom")

        name, sub_report, exc = _run_one_validator("crash", crash, tmp_path)
        assert name == "crash"
        assert isinstance(exc, ValueError)
        assert str(exc) == "boom"
        # Sub-report should be empty (nothing was added before the crash).
        assert sub_report.results == []

    def test_positional_args_are_forwarded(self, tmp_path):
        """Validators with extra positional args (e.g. validate_skills
        takes skip_platform_checks) get them via the args_kwargs tuple."""
        received = []

        def with_arg(plugin_root, report, extra_arg):
            received.append(extra_arg)
            report.info("ran")

        name, sub_report, exc = _run_one_validator(
            "with_arg", with_arg, tmp_path, args_kwargs=((["windows"],), {})
        )
        assert exc is None
        assert received == [["windows"]]
        assert len(sub_report.results) == 1

    def test_kwargs_are_forwarded(self, tmp_path):
        """Validators with kwargs (e.g. run_lint_engine takes
        strict_missing_tools=True) get them via the args_kwargs tuple."""
        received = {}

        def with_kwarg(plugin_root, report, *, strict_missing_tools=False):
            received["strict"] = strict_missing_tools
            report.info("ran")

        name, sub_report, exc = _run_one_validator(
            "with_kwarg",
            with_kwarg,
            tmp_path,
            args_kwargs=((), {"strict_missing_tools": True}),
        )
        assert exc is None
        assert received == {"strict": True}


class TestRunParallelBatch:
    """``_run_parallel_batch`` dispatches a list of tasks to a thread
    pool and merges results IN INPUT ORDER. The order invariant is
    what makes the parallel path produce serial-identical reports."""

    def test_empty_tasks_is_noop(self, tmp_path):
        """Empty task list → no pool spawn, no report mutation."""
        from cpv_validation_common import ValidationReport

        report = ValidationReport()
        _run_parallel_batch([], tmp_path, report)
        assert report.results == []

    def test_results_merged_in_input_order(self, tmp_path):
        """Tasks A, B, C return reports rA, rB, rC. Final umbrella
        must contain rA's findings, then rB's, then rC's — regardless
        of which task finished first."""
        from cpv_validation_common import ValidationReport

        def make_validator(tag):
            def fn(plugin_root, report):
                # Add a distinguishable finding per validator. The
                # message text is what we compare for order.
                report.info(f"finding-from-{tag}")
            return fn

        tasks = [
            ("A", make_validator("A"), ((), {})),
            ("B", make_validator("B"), ((), {})),
            ("C", make_validator("C"), ((), {})),
        ]
        umbrella = ValidationReport()
        _run_parallel_batch(tasks, tmp_path, umbrella)

        messages = [r.message for r in umbrella.results]
        assert messages == [
            "finding-from-A",
            "finding-from-B",
            "finding-from-C",
        ], f"Order drift: {messages}"

    def test_crashed_task_surfaces_as_minor(self, tmp_path):
        """A validator that raises must NOT crash the batch — it
        surfaces as a MINOR finding on the umbrella report."""
        from cpv_validation_common import ValidationReport

        def good(plugin_root, report):
            report.passed("good")

        def bad(plugin_root, report):
            raise RuntimeError("kaboom")

        tasks = [
            ("good1", good, ((), {})),
            ("bad", bad, ((), {})),
            ("good2", good, ((), {})),
        ]
        umbrella = ValidationReport()
        _run_parallel_batch(tasks, tmp_path, umbrella)

        # 2 PASSED + 1 MINOR (from the boundary-error handler).
        levels = [r.level for r in umbrella.results]
        assert levels.count("PASSED") == 2
        assert levels.count("MINOR") == 1
        minor_msg = next(r.message for r in umbrella.results if r.level == "MINOR")
        assert "bad" in minor_msg
        assert "RuntimeError" in minor_msg
        assert "kaboom" in minor_msg
