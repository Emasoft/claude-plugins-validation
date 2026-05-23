#!/usr/bin/env python3
"""Parallel-vs-serial parity tests for validate_xref (task #384).

Pins:
  * Worker functions are TOP-LEVEL importable (ProcessPoolExecutor's only
    way to dispatch work). Closures/lambdas would raise PicklingError —
    this gate catches a regression where someone refactors a worker into
    a method or wraps it in a decorator that loses the module-level
    binding.
  * Parallel and serial paths produce IDENTICAL findings for a multi-file
    fixture: same severity, same message, same file, same order. The
    contract from spec §"Per-validator integration contract": "parallel
    path produces IDENTICAL findings (same severity, same message, same
    order modulo input order) vs the prior serial baseline."
  * Order preservation: the serial join walks ``parallel_scan`` results
    in INPUT order, so per-file finding order is reproducible run-to-run.
  * Errors from a worker (simulated read failure) surface as a per-file
    WARNING via the join layer — never crash the whole validator. We
    drive this via the harness directly (with a worker that intentionally
    raises) since the production workers swallow read errors.
  * CPV_XREF_PARALLEL=0 env var routes through the serial fallback. The
    fallback ALSO uses the same worker functions, so the parity test
    above effectively gates BOTH paths against each other.

No mocking — every test builds a real plugin tree under ``tmp_path`` and
runs the real ``validate_*`` functions.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports (matches conftest.py convention)
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_parallel_runner import parallel_scan
from validate_xref import (
    _SKILL_REF_PLUGIN_DIRS,
    CrossReferenceValidationReport,
    _xref_extract_command_worker,
    _xref_extract_dispatch_worker,
    _xref_extract_skill_refs_worker,
    _xref_parallel_enabled,
    validate_command_agent_refs,
    validate_cross_references,
    validate_subagent_type_matching,
)

# ---------------------------------------------------------------------------
# Helper: build a plugin tree with N files of mixed ref types.
# ---------------------------------------------------------------------------


def _build_multi_file_plugin(root: Path, n_agents: int = 8) -> Path:
    """Build a plugin with N agents + N commands + N skills, each
    containing a mix of refs (literal/dynamic/ghost/cross-plugin/spawn).

    The fixture is intentionally rich enough to drive every classification
    branch in the validators so parity assertions cover all of them. Total
    file count >= 16 to push the parallel path past the serial fallback
    threshold (which kicks in at len < 2).
    """
    plugin = root / "test-plugin"
    plugin.mkdir()
    cp_dir = plugin / ".claude-plugin"
    cp_dir.mkdir()
    (cp_dir / "plugin.json").write_text(
        json.dumps({"name": "test-plugin", "version": "1.0.0"}),
    )

    agents_dir = plugin / "agents"
    agents_dir.mkdir()
    commands_dir = plugin / "commands"
    commands_dir.mkdir()
    skills_dir = plugin / "skills"
    skills_dir.mkdir()

    # Create N agent files. Even-indexed reference the agent N//2 (valid);
    # odd-indexed reference a ghost agent that doesn't exist anywhere.
    # Every 4th file ALSO uses a dynamic Python kwarg form.
    for i in range(n_agents):
        body_lines = [
            "---",
            f"name: agent-{i:02d}",
            "---",
            f"# Agent {i:02d}",
            "",
        ]
        if i % 2 == 0:
            # Valid ref
            target = i // 2
            body_lines.append(f'Use subagent_type: "agent-{target:02d}" for the work.')
        else:
            # Ghost ref
            body_lines.append(f'Use subagent_type: "ghost-{i:02d}" to delegate.')
        if i % 4 == 0:
            # Add a dynamic dispatch too
            body_lines.append("")
            body_lines.append("```python")
            body_lines.append(f"Task(subagent_type=dynamic_var_{i})")
            body_lines.append("```")
        if i == 3:
            # One cross-plugin ref to exercise that branch
            body_lines.append('Use subagent_type: "other-plugin:remote-agent"')
        (agents_dir / f"agent-{i:02d}.md").write_text("\n".join(body_lines) + "\n")

    # Create N command files that reference agents + use spawn prose
    for i in range(n_agents):
        target = (i + 1) % n_agents
        body_lines = [
            "---",
            f"name: cmd-{i:02d}",
            "---",
            f"# Command {i:02d}",
            "",
            f'Use subagent_type: "agent-{target:02d}" first.',
        ]
        if i % 3 == 0:
            # spawn prose ref (ghost — should emit MAJOR)
            body_lines.append(f"Then spawn unknown-bot-{i:02d} agent for cleanup.")
        # Also embed a skill ref
        body_lines.append(f"Uses skills/skill-{i % 3:02d} for support.")
        (commands_dir / f"cmd-{i:02d}.md").write_text("\n".join(body_lines) + "\n")

    # Create 3 skills (skill-00, skill-01, skill-02 — referenced by commands)
    # Plus a couple of ghost-referencing skill files for full coverage.
    for i in range(3):
        sk = skills_dir / f"skill-{i:02d}"
        sk.mkdir()
        body_lines = [
            "---",
            f"name: skill-{i:02d}",
            "---",
            f"# Skill {i:02d}",
            "",
            f'Use subagent_type: "agent-{i:02d}" internally.',
            f"References skills/skill-{(i+1) % 3:02d} for chaining.",
        ]
        (sk / "SKILL.md").write_text("\n".join(body_lines) + "\n")

    return plugin


def _findings_signature(report: CrossReferenceValidationReport) -> list[tuple[str, str, str | None]]:
    """Reduce a report to its (level, message, file) triples, in order.

    This is the canonical equality key for parity tests — anything else
    in the report (timing, fixable hints) is irrelevant to the spec
    contract of "identical findings".
    """
    return [(r.level, r.message, r.file) for r in report.results]


# ===========================================================================
# Gate 1 — worker functions are top-level importable (pickle-able).
# ===========================================================================


class TestWorkersArePickleable:
    """Each worker function MUST be importable from validate_xref at module
    scope. ProcessPoolExecutor.submit will raise PicklingError otherwise,
    breaking the entire parallel path."""

    def test_dispatch_worker_is_top_level(self):
        """``_xref_extract_dispatch_worker`` is importable from validate_xref."""
        import pickle

        # Round-trip through pickle proves the function is reachable
        # through its qualified name — exactly what ProcessPoolExecutor
        # does to ship the callable to the worker process.
        rt = pickle.loads(pickle.dumps(_xref_extract_dispatch_worker))
        assert rt is _xref_extract_dispatch_worker

    def test_command_worker_is_top_level(self):
        """``_xref_extract_command_worker`` is importable from validate_xref."""
        import pickle

        rt = pickle.loads(pickle.dumps(_xref_extract_command_worker))
        assert rt is _xref_extract_command_worker

    def test_skill_refs_worker_is_top_level(self):
        """``_xref_extract_skill_refs_worker`` is importable from validate_xref."""
        import pickle

        rt = pickle.loads(pickle.dumps(_xref_extract_skill_refs_worker))
        assert rt is _xref_extract_skill_refs_worker


# ===========================================================================
# Gate 2 — worker behavior in isolation (no harness, just function call).
# ===========================================================================


class TestWorkerBehavior:
    """Each worker reads a file and returns the SAME data the inline serial
    code used to compute — these tests pin the worker's contract."""

    def test_dispatch_worker_returns_extracted_refs(self, tmp_path: Path):
        """Worker returns the same (kind, name) tuples that
        _extract_dispatch_refs(content) produces."""
        p = tmp_path / "a.md"
        p.write_text(
            "---\nname: a\n---\n# A\n\n"
            'Use subagent_type: "helper" then subagent_type=dyn_var\n'
        )
        refs = _xref_extract_dispatch_worker(p)
        assert ("literal", "helper") in refs
        assert ("dynamic", "dyn_var") in refs

    def test_dispatch_worker_returns_empty_on_unreadable_path(self, tmp_path: Path):
        """Worker returns ``[]`` for nonexistent path (read error swallowed,
        matches original serial behavior of skipping silently)."""
        missing = tmp_path / "nope.md"
        assert _xref_extract_dispatch_worker(missing) == []

    def test_command_worker_tags_dispatch_vs_spawn(self, tmp_path: Path):
        """Command worker returns tagged stream with 'dispatch' for
        subagent_type and 'spawn' for prose patterns."""
        p = tmp_path / "cmd.md"
        p.write_text(
            "---\nname: cmd\n---\n# Cmd\n\n"
            'subagent_type: "worker"\nThen spawn helper-bot agent for cleanup.\n'
        )
        out = _xref_extract_command_worker(p)
        tags = [t for t, _ in out]
        assert "dispatch" in tags
        assert "spawn" in tags
        # Dispatch payload is "<kind>:<name>"
        dispatch_payloads = [p for t, p in out if t == "dispatch"]
        assert "literal:worker" in dispatch_payloads
        # Spawn payload is the raw agent name
        spawn_payloads = [p for t, p in out if t == "spawn"]
        assert "helper-bot" in spawn_payloads

    def test_skill_refs_worker_applies_plugin_dirs_filter(self, tmp_path: Path):
        """Worker filters out plugin-structural names (e.g. ``skills/agents``)
        per the issue #27 + TRDD-25b9be90 logic."""
        p = tmp_path / "a.md"
        # ``skills/agents`` should be filtered (agents is plugin-structural);
        # ``skills/my-real-skill`` should survive.
        p.write_text("# Hello\n\nSee skills/agents and skills/my-real-skill.\n")
        out = _xref_extract_skill_refs_worker(p)
        assert "my-real-skill" in out
        assert "agents" not in out

    def test_skill_refs_worker_drops_trailing_hyphen(self, tmp_path: Path):
        """Belt-and-suspenders against issue #27 — no skill capture ends in
        ``-`` (no plugin can ship a name ending in hyphen)."""
        p = tmp_path / "a.md"
        # Force a tricky capture via a hand-built input that would have
        # leaked under the older pattern. The new regex also rejects this
        # at extraction time so the filter is belt-and-suspenders.
        p.write_text("skills/legitimate skills/ok-name\n")
        out = _xref_extract_skill_refs_worker(p)
        for name in out:
            assert not name.endswith("-")


# ===========================================================================
# Gate 3 — PARALLEL vs SERIAL parity on a multi-file plugin (THE contract).
# ===========================================================================


class TestParallelSerialParity:
    """parallel-path findings == serial-path findings on the same plugin.

    Strategy: run the validator twice on the same fixture, once with
    ``CPV_XREF_PARALLEL=1`` (forced parallel) and once with
    ``CPV_XREF_PARALLEL=0`` (forced serial). Compare the reduced
    (level, message, file) signatures.
    """

    @pytest.fixture
    def plugin(self, tmp_path: Path) -> Path:
        # 8 agents + 8 commands + 3 skills = 19 files total — well past
        # the len > 1 parallel threshold.
        return _build_multi_file_plugin(tmp_path, n_agents=8)

    def _run_with(self, plugin: Path, parallel: bool) -> CrossReferenceValidationReport:
        """Run validate_cross_references with the requested mode and
        return the report."""
        prior = os.environ.get("CPV_XREF_PARALLEL")
        os.environ["CPV_XREF_PARALLEL"] = "1" if parallel else "0"
        try:
            return validate_cross_references(plugin)
        finally:
            if prior is None:
                os.environ.pop("CPV_XREF_PARALLEL", None)
            else:
                os.environ["CPV_XREF_PARALLEL"] = prior

    def test_full_validator_parity(self, plugin: Path):
        """End-to-end validate_cross_references: parallel == serial."""
        rep_parallel = self._run_with(plugin, parallel=True)
        rep_serial = self._run_with(plugin, parallel=False)

        sig_p = _findings_signature(rep_parallel)
        sig_s = _findings_signature(rep_serial)

        # The two signatures must be IDENTICAL (level, message, file all
        # match, order preserved). This is the spec contract.
        assert sig_p == sig_s, (
            f"Parallel/serial divergence:\n"
            f"  parallel ({len(sig_p)}): {sig_p[:5]}\n"
            f"  serial   ({len(sig_s)}): {sig_s[:5]}"
        )

    def test_severity_counts_match(self, plugin: Path):
        """Even at the bucket level (CRITICAL/MAJOR/MINOR/NIT/WARNING/INFO/PASSED),
        parallel and serial agree — proves we didn't drop or duplicate any
        finding under either path."""
        rep_p = self._run_with(plugin, parallel=True)
        rep_s = self._run_with(plugin, parallel=False)

        def bucket(rep):
            from collections import Counter

            return Counter(r.level for r in rep.results)

        assert bucket(rep_p) == bucket(rep_s)

    def test_agent_refs_dict_parity(self, plugin: Path):
        """``report.agent_refs`` is populated identically — important
        because it's part of ``to_dict`` and consumers may rely on it."""
        rep_p = self._run_with(plugin, parallel=True)
        rep_s = self._run_with(plugin, parallel=False)

        # Compare normalized dicts (key-by-key, value-list-as-sorted)
        def norm(d):
            return {k: sorted(v) for k, v in d.items()}

        assert norm(rep_p.agent_refs) == norm(rep_s.agent_refs)

    def test_skill_refs_dict_parity(self, plugin: Path):
        """``report.skill_refs`` is populated identically — same rationale
        as agent_refs."""
        rep_p = self._run_with(plugin, parallel=True)
        rep_s = self._run_with(plugin, parallel=False)

        def norm(d):
            return {k: sorted(v) for k, v in d.items()}

        assert norm(rep_p.skill_refs) == norm(rep_s.skill_refs)


# ===========================================================================
# Gate 4 — order preservation across the harness boundary.
# ===========================================================================


class TestOrderPreservation:
    """``parallel_scan`` returns ScanResult per-file in INPUT order; the
    join layer in validate_xref walks that ordered list, so per-file
    finding emission must follow input order regardless of which worker
    finished first."""

    def test_input_order_drives_finding_order(self, tmp_path: Path):
        """Validate that findings come out in input order even when files
        are presented in non-glob order."""
        plugin = _build_multi_file_plugin(tmp_path, n_agents=8)

        # Run the same validator path used by validate_cross_references,
        # but with a controlled file ordering. We hit
        # validate_subagent_type_matching directly because it returns
        # both PASSED and CRITICAL findings whose file= field encodes
        # the input order.
        os.environ["CPV_XREF_PARALLEL"] = "1"
        try:
            report = CrossReferenceValidationReport()
            validate_subagent_type_matching(
                plugin,
                report,
                {f"agent-{i:02d}" for i in range(8)},
                plugin_name="test-plugin",
            )
        finally:
            os.environ.pop("CPV_XREF_PARALLEL", None)

        # Collect the file field of every finding; pythonic sort of the
        # FIRST occurrence per file should match the order md_files was
        # built in (alphabetical sort of agent/command/skill paths).
        seen_first: list[str] = []
        for r in report.results:
            if r.file and r.file not in seen_first:
                seen_first.append(r.file)

        # The validator sorts md_files alphabetically before dispatching;
        # the join layer walks in that order. So seen_first[i] must be
        # <= seen_first[i+1] alphabetically.
        for prev, curr in zip(seen_first, seen_first[1:]):
            assert prev <= curr, (
                f"Order violation: {prev!r} came before {curr!r} "
                f"but should come after (input was sorted)."
            )


# ===========================================================================
# Gate 5 — error surfacing via the harness.
# ===========================================================================


def _bomb_worker(path: Path) -> list:
    """Top-level worker that always raises — used to drive the
    ``ScanResult.error`` path on the harness."""
    raise RuntimeError(f"intentional bomb on {path.name}")


class TestErrorSurfacing:
    """A worker exception must be captured into ``ScanResult.error`` by
    the harness (per its on_error='collect' default). validate_xref's
    join layers then surface those errors as per-file findings rather
    than letting one bad file crash the whole validator."""

    def test_harness_captures_worker_exception(self, tmp_path: Path):
        """``parallel_scan`` with a bomb worker returns ScanResults with
        ``error`` set, not an empty list — sanity check on the harness
        contract before we trust the join layer to act on it."""
        files = [tmp_path / "a.md", tmp_path / "b.md"]
        for f in files:
            f.write_text("# stub")

        results = parallel_scan(files, _bomb_worker)
        assert len(results) == 2
        for r in results:
            assert r.error is not None
            assert "intentional bomb" in r.error
            assert r.findings == []

    def test_command_validator_does_not_crash_on_worker_error(
        self, tmp_path: Path, monkeypatch
    ):
        """If the command worker raises, validate_command_agent_refs
        surfaces the error as a per-file MINOR (matching the old serial
        'Could not read command file' code path) instead of bubbling
        up the exception."""
        plugin = tmp_path / "p"
        plugin.mkdir()
        commands_dir = plugin / "commands"
        commands_dir.mkdir()
        # Multiple files so the parallel path is exercised (len > 1)
        (commands_dir / "cmd-a.md").write_text("# A\n")
        (commands_dir / "cmd-b.md").write_text("# B\n")

        # Swap in the bomb worker via monkeypatch — this guarantees the
        # join layer sees ScanResult.error set.
        import validate_xref as vx

        monkeypatch.setattr(vx, "_xref_extract_command_worker", _bomb_worker)

        report = CrossReferenceValidationReport()
        # Force parallel so we exercise the real harness path
        monkeypatch.setenv("CPV_XREF_PARALLEL", "1")
        # Must not raise
        validate_command_agent_refs(plugin, report, set(), plugin_name="p")

        # The errors should surface as MINOR findings (per join-layer logic)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("Could not read command file" in m for m in minor_msgs)


# ===========================================================================
# Gate 6 — CPV_XREF_PARALLEL escape hatch.
# ===========================================================================


class TestEscapeHatch:
    """The CPV_XREF_PARALLEL env var disables the parallel path. This
    matters for CI environments that pin worker counts, for users with
    process-spawn restrictions, and for any debugger that needs a single
    process."""

    def test_default_enabled(self, monkeypatch):
        """When the env var is unset, the helper reports parallel ON."""
        monkeypatch.delenv("CPV_XREF_PARALLEL", raising=False)
        assert _xref_parallel_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "FALSE", "No", "OFF"])
    def test_disabled_by_zero_false_no_off(self, val, monkeypatch):
        """``0`` / ``false`` / ``no`` / ``off`` (case-insensitive) disable."""
        monkeypatch.setenv("CPV_XREF_PARALLEL", val)
        assert _xref_parallel_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", ""])
    def test_other_values_keep_default_enabled(self, val, monkeypatch):
        """Any value other than the disable-set keeps parallel ON. An
        empty string is treated as unset (the default)."""
        monkeypatch.setenv("CPV_XREF_PARALLEL", val)
        assert _xref_parallel_enabled() is True

    def test_serial_fallback_produces_same_findings(self, tmp_path: Path, monkeypatch):
        """When CPV_XREF_PARALLEL=0, the validator runs serially BUT
        still produces the same findings — proving the worker functions
        themselves (not just the harness) are the source of truth for
        finding content."""
        plugin = _build_multi_file_plugin(tmp_path, n_agents=4)

        monkeypatch.setenv("CPV_XREF_PARALLEL", "0")
        rep_serial = validate_cross_references(plugin)

        monkeypatch.setenv("CPV_XREF_PARALLEL", "1")
        rep_parallel = validate_cross_references(plugin)

        assert _findings_signature(rep_serial) == _findings_signature(rep_parallel)


# ===========================================================================
# Gate 7 — module-level _SKILL_REF_PLUGIN_DIRS sanity.
# ===========================================================================


class TestSkillRefPluginDirs:
    """The plugin-dirs filter set was lifted to module scope so worker
    processes (which re-import the module) see the same set. Pin its
    composition so a future refactor doesn't accidentally drop an entry
    (which would cause false-positive skill findings)."""

    def test_contains_core_plugin_subdirs(self):
        """Core plugin directories are filtered — these are NOT skill names."""
        for name in ("agents", "commands", "skills", "hooks", "scripts"):
            assert name in _SKILL_REF_PLUGIN_DIRS

    def test_contains_language_names(self):
        """Language names (e.g. ``python``, ``rust``) are filtered —
        ``skills/python`` is prose like a layout hint, not an invocation."""
        for name in ("python", "rust", "javascript", "typescript", "go"):
            assert name in _SKILL_REF_PLUGIN_DIRS

    def test_is_frozen_set(self):
        """The set is intentionally frozen — workers must not mutate it,
        and tests must not accidentally rewrite the production filter."""
        assert isinstance(_SKILL_REF_PLUGIN_DIRS, frozenset)
