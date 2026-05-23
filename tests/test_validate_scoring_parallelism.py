"""Task #384 — parity regression tests for parallel scoring validation.

These tests pin the contract that ``run_all_validators`` produces the
SAME per-key ``ValidationResult`` sequence regardless of whether the
top-level scoring tasks run serially (``CPV_SCORING_PARALLEL=0``) or via
the shared ``parallel_scan`` harness (default). The parallel harness uses
``ProcessPoolExecutor`` per spec, so any silent regression to
"results-by-completion-order" would surface here as a mismatch on the
first run.

Coverage parallels A6's test_validate_hook_parallelism.py and A8's
test_validate_cache_parallelism.py:
  1. Source-of-truth pin — validate_scoring.py imports ``parallel_scan``
     from the shared harness AND exposes a top-level
     ``scan_one_scoring_unit`` callable.
  2. Top-level pickleability — ``scan_one_scoring_unit`` works on real
     ``_ScoringWorkUnit``s round-tripped through pickle.
  3. Parity at small cardinality — minimal valid plugin (manifest +
     README only).
  4. Parity at higher cardinality — multi-file fixture exercising every
     validator branch together (agents, skills, commands, hooks, mcp).
  5. Order preservation under load — bigger fixture with mixed file
     counts.
  6. Worker exception surfaces as per-key WARNING (no crash).
  7. Env-var switch parsing (0/false/no/off → serial; everything else
     including unset → parallel).
  8. Empty / no-finding plugin still produces the canonical key set.
  9. Public API unchanged — ``run_all_validators`` / ``compute_quality_score``
     signatures preserved.
 10. ``_build_scoring_work_units`` order matches the legacy serial loop.
"""

from __future__ import annotations

import inspect
import json
import os
import pickle
import sys
from pathlib import Path

import pytest

# tests/conftest.py adds scripts/ to sys.path; this is a defensive duplicate.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_scoring  # noqa: E402
from validate_scoring import (  # noqa: E402
    _build_scoring_work_units,
    _scoring_parallel_enabled,
    _ScoringWorkUnit,
    compute_quality_score,
    run_all_validators,
    scan_one_scoring_unit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_base_plugin(tmp_path: Path, name: str = "test-plugin") -> Path:
    """Create a minimal valid plugin scaffold.

    Mirrors test_validate_scoring.TestRunAllValidatorsAdditional._make_base_plugin
    so the parity tests below see the same shape the existing functional
    tests exercise.
    """
    plugin = tmp_path / name
    plugin.mkdir()
    claude_plugin = plugin / ".claude-plugin"
    claude_plugin.mkdir()
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": "A plugin for testing scoring parallelism",
        "author": {"name": "Tester", "email": "tester@example.com"},
    }
    (claude_plugin / "plugin.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (plugin / "README.md").write_text(
        "# Test Plugin\n\nDescription.\n\n## Installation\n\nInstall it.\n\n## Usage\n\nUse it.\n",
        encoding="utf-8",
    )
    return plugin


def _run_scoring(plugin_root: Path, *, parallel: bool) -> dict:
    """Run run_all_validators once with CPV_SCORING_PARALLEL forced on/off.

    The env-var is restored on exit so xdist workers can't bleed state.
    """
    prev = os.environ.get("CPV_SCORING_PARALLEL")
    os.environ["CPV_SCORING_PARALLEL"] = "1" if parallel else "0"
    try:
        return run_all_validators(plugin_root)
    finally:
        if prev is None:
            os.environ.pop("CPV_SCORING_PARALLEL", None)
        else:
            os.environ["CPV_SCORING_PARALLEL"] = prev


def _result_sig(r) -> tuple:
    """Tuple form of a ValidationResult suitable for sequence-equality
    comparison. Includes level, message, file, line.
    """
    return (r.level, r.message, r.file, r.line)


def _reports_sig(reports: dict) -> dict[str, list[tuple]]:
    """Convert a ``reports`` dict into a comparable per-key signature dict.

    Each key maps to the ordered list of ``_result_sig`` tuples for that
    report. We compare ``signature dicts`` rather than raw reports because
    ValidationReport's dataclass-equality includes object identity of
    fixable_issues / valid_items / failed_items which scoring doesn't use
    but might gain in the future.
    """
    return {k: [_result_sig(r) for r in v.results] for k, v in reports.items()}


# ---------------------------------------------------------------------------
# 1. Source-of-truth pins
# ---------------------------------------------------------------------------


def test_validate_scoring_imports_parallel_scan_harness():
    """validate_scoring.py MUST import ``parallel_scan`` from the shared
    ``cpv_parallel_runner`` module. If someone reverts to a private
    executor or removes the harness wiring, this is the first signal.
    """
    src = inspect.getsource(validate_scoring)
    assert "from cpv_parallel_runner import parallel_scan" in src, (
        "validate_scoring.py no longer imports parallel_scan from the shared "
        "harness — task #384 parallelism wiring has been reverted."
    )


def test_validate_scoring_exposes_scan_one_scoring_unit_top_level():
    """``scan_one_scoring_unit`` MUST be a TOP-LEVEL callable in
    validate_scoring.py (not a closure, not a nested function).
    ProcessPoolExecutor requires pickleable callables; closures fail at
    submit time.
    """
    assert callable(scan_one_scoring_unit), "scan_one_scoring_unit is not callable"
    # A top-level function's __qualname__ equals its __name__. A nested
    # function would have a qualname like
    # 'enclosing_func.<locals>.scan_one_scoring_unit'.
    assert scan_one_scoring_unit.__qualname__ == "scan_one_scoring_unit", (
        f"scan_one_scoring_unit is not top-level "
        f"(qualname={scan_one_scoring_unit.__qualname__!r}); "
        "ProcessPoolExecutor will reject the unpickleable closure."
    )
    assert scan_one_scoring_unit.__module__ == "validate_scoring"


def test_run_all_validators_parallel_uses_parallel_scan():
    """The body of ``_run_all_validators_parallel`` must call ``parallel_scan``
    (the shared harness function). Catches a regression where someone
    replaces the harness call with an inline loop or with a different
    executor.
    """
    src = inspect.getsource(validate_scoring._run_all_validators_parallel)
    assert "parallel_scan(units, scan_one_scoring_unit)" in src, (
        "_run_all_validators_parallel no longer dispatches via parallel_scan — "
        "task #384 parallel branch has been bypassed."
    )


def test_run_all_validators_respects_env_var():
    """``run_all_validators`` must consult ``_scoring_parallel_enabled``
    so the user escape hatch ``CPV_SCORING_PARALLEL=0`` works.
    """
    src = inspect.getsource(validate_scoring.run_all_validators)
    assert "_scoring_parallel_enabled()" in src, (
        "run_all_validators no longer respects CPV_SCORING_PARALLEL — "
        "the user escape hatch is gone."
    )


# ---------------------------------------------------------------------------
# 2. Top-level pickleability
# ---------------------------------------------------------------------------


def test_scoring_work_unit_round_trips_through_pickle(tmp_path: Path):
    """A real-shape ``_ScoringWorkUnit`` must survive a pickle round-trip.
    If anyone adds a non-pickleable field (a Path, a Callable, an open
    file handle, …), ProcessPoolExecutor rejects the submit and the
    parallel branch fails.
    """
    unit = _ScoringWorkUnit(
        kind="security",
        target_key="security",
        plugin_root_str=str(tmp_path),
    )
    blob = pickle.dumps(unit)
    restored = pickle.loads(blob)
    assert restored == unit


def test_scan_one_scoring_unit_returns_list_per_harness_contract(tmp_path: Path):
    """``scan_one_scoring_unit`` MUST return a list per the parallel_scan
    harness contract (``scan_func: Callable[[Any], list]``). Returning a
    bare tuple would land in ``ScanResult.findings`` unmodified, and the
    parent's ``payload[0]`` unwrap would then misbehave (it would grab
    the first character of the target_key string instead of the tuple).
    """
    plugin = _make_base_plugin(tmp_path)
    unit = _ScoringWorkUnit(
        kind="plugin",
        target_key="plugin",
        plugin_root_str=str(plugin),
    )
    out = scan_one_scoring_unit(unit)
    assert isinstance(out, list), f"Expected list, got {type(out).__name__}"
    assert len(out) == 1, f"Expected single-element list, got len={len(out)}"
    key, report = out[0]
    assert key == "plugin"
    assert hasattr(report, "results"), "Second element must be a ValidationReport"


def test_scan_one_scoring_unit_unknown_kind_returns_empty_report(tmp_path: Path):
    """An unknown kind on a work unit returns an empty report rather than
    raising. This is intentional defense — a future enum drift surfaces
    as missing findings, which the parity tests catch, rather than
    crashing the whole validator.
    """
    plugin = _make_base_plugin(tmp_path)
    unit = _ScoringWorkUnit(
        kind="bogus_kind",
        target_key="bogus",
        plugin_root_str=str(plugin),
    )
    out = scan_one_scoring_unit(unit)
    assert len(out) == 1
    key, report = out[0]
    assert key == "bogus"
    assert report.results == []


# ---------------------------------------------------------------------------
# 3. Parity — small fixture (minimal valid plugin)
# ---------------------------------------------------------------------------


def test_parity_minimal_plugin(tmp_path: Path):
    """Minimal valid plugin → serial and parallel must produce the same
    per-key ValidationResult sequences. Smallest possible parity case —
    exercises the round-trip plumbing without any optional sub-validators.
    """
    plugin = _make_base_plugin(tmp_path)

    serial_reports = _run_scoring(plugin, parallel=False)
    parallel_reports = _run_scoring(plugin, parallel=True)

    s_sig = _reports_sig(serial_reports)
    p_sig = _reports_sig(parallel_reports)

    # Same key set
    assert set(s_sig.keys()) == set(p_sig.keys()), (
        f"Key sets differ.\n"
        f"Serial keys:   {sorted(s_sig.keys())}\n"
        f"Parallel keys: {sorted(p_sig.keys())}"
    )

    # Same per-key result sequences
    for k in s_sig:
        assert s_sig[k] == p_sig[k], (
            f"Per-key parity broken for {k!r}.\n"
            f"Serial:   {s_sig[k]}\n"
            f"Parallel: {p_sig[k]}"
        )


# ---------------------------------------------------------------------------
# 4. Parity — multi-rule fixture (exercises every validator branch)
# ---------------------------------------------------------------------------


def _seed_multi_kind_plugin(plugin: Path) -> None:
    """Plant fixtures that engage every conditional branch in run_all_validators.

    Layout:
      hooks/hooks.json     → enables 'hooks' validator
      .mcp.json            → enables 'mcp' validator
      agents/a.md          → enables 'agents' validator
      skills/s/SKILL.md    → enables 'skills' validator
      commands/c.md        → enables 'commands' validator
    """
    hooks_dir = plugin / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "event": "PreToolUse",
                        "matcher": "Bash",
                        "command": "echo 'check'",
                    }
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (plugin / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "test-server": {
                        "command": "node",
                        "args": ["server.js"],
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (plugin / "agents").mkdir()
    (plugin / "agents" / "a.md").write_text(
        "---\nname: a\ndescription: An agent that does stuff\nmodel: sonnet\n---\n\n"
        "# A\n\nBody text.\n",
        encoding="utf-8",
    )

    (plugin / "skills" / "s").mkdir(parents=True)
    (plugin / "skills" / "s" / "SKILL.md").write_text(
        "---\nname: s\ndescription: A skill for tests\n---\n\n# S\n\nBody.\n",
        encoding="utf-8",
    )

    (plugin / "commands").mkdir()
    (plugin / "commands" / "c.md").write_text(
        "---\nname: c\ndescription: A command for tests\n---\n\n# /c\n\nBody.\n",
        encoding="utf-8",
    )


def test_parity_multi_kind_fixture(tmp_path: Path):
    """Engages every conditional branch in run_all_validators. Asserts
    per-key ValidationResult sequences are identical between serial and
    parallel runs. Pinning ALL seven keys — plugin, security, hooks,
    mcp, agents, skills, commands.
    """
    plugin = _make_base_plugin(tmp_path)
    _seed_multi_kind_plugin(plugin)

    serial_reports = _run_scoring(plugin, parallel=False)
    parallel_reports = _run_scoring(plugin, parallel=True)

    # Sanity: every expected key is present in both modes
    expected_keys = {"plugin", "security", "hooks", "mcp", "agents", "skills", "commands"}
    assert expected_keys.issubset(serial_reports.keys()), (
        f"Serial run missing keys: {expected_keys - serial_reports.keys()}"
    )
    assert expected_keys.issubset(parallel_reports.keys()), (
        f"Parallel run missing keys: {expected_keys - parallel_reports.keys()}"
    )

    s_sig = _reports_sig(serial_reports)
    p_sig = _reports_sig(parallel_reports)

    # Per-key sequence equality
    for key in expected_keys:
        s_seq = s_sig[key]
        p_seq = p_sig[key]
        if s_seq != p_seq:
            first_diff = next(
                (i for i in range(min(len(s_seq), len(p_seq))) if s_seq[i] != p_seq[i]),
                min(len(s_seq), len(p_seq)),
            )
            lo = max(0, first_diff - 2)
            hi = min(max(len(s_seq), len(p_seq)), first_diff + 3)
            lines = [f"Result sequences diverge for key {key!r}."]
            lines.append(f"  Serial   len={len(s_seq)}")
            lines.append(f"  Parallel len={len(p_seq)}")
            lines.append(f"  First divergent index: {first_diff}")
            lines.append(f"  Slice [{lo}:{hi}]:")
            for j in range(lo, hi):
                s_e = s_seq[j] if j < len(s_seq) else "<MISSING>"
                p_e = p_seq[j] if j < len(p_seq) else "<MISSING>"
                mark = " <-- DIVERGE" if s_e != p_e else ""
                lines.append(f"    [{j}]:")
                lines.append(f"      serial:   {s_e}{mark}")
                lines.append(f"      parallel: {p_e}")
            pytest.fail("\n".join(lines))


# ---------------------------------------------------------------------------
# 5. Parity — higher cardinality (multiple files per kind)
# ---------------------------------------------------------------------------


def test_parity_multi_file_components(tmp_path: Path):
    """4 agents + 4 commands + 4 skills, asserting parallel per-file
    merge order matches serial per-file merge order. Pins the by-key
    grouping in _run_all_validators_parallel.
    """
    plugin = _make_base_plugin(tmp_path)
    (plugin / "agents").mkdir()
    (plugin / "commands").mkdir()
    (plugin / "skills").mkdir()

    for i in range(4):
        (plugin / "agents" / f"a{i:02d}.md").write_text(
            f"---\nname: a{i:02d}\ndescription: agent {i:02d} for tests\n"
            "model: sonnet\n---\n\n# a\n\nBody.\n",
            encoding="utf-8",
        )
        (plugin / "commands" / f"c{i:02d}.md").write_text(
            f"---\nname: c{i:02d}\ndescription: command {i:02d} for tests\n"
            "---\n\n# /c\n\nBody.\n",
            encoding="utf-8",
        )
        (plugin / "skills" / f"s{i:02d}").mkdir()
        (plugin / "skills" / f"s{i:02d}" / "SKILL.md").write_text(
            f"---\nname: s{i:02d}\ndescription: skill {i:02d} for tests\n"
            "---\n\n# s\n\nBody.\n",
            encoding="utf-8",
        )

    serial_reports = _run_scoring(plugin, parallel=False)
    parallel_reports = _run_scoring(plugin, parallel=True)

    s_sig = _reports_sig(serial_reports)
    p_sig = _reports_sig(parallel_reports)

    # Both modes must produce the same keys
    assert set(s_sig.keys()) == set(p_sig.keys())

    for key in ("agents", "skills", "commands"):
        assert s_sig[key] == p_sig[key], (
            f"Per-file merge order broken for key {key!r}.\n"
            f"Serial   len={len(s_sig[key])}\n"
            f"Parallel len={len(p_sig[key])}\n"
            f"  First serial item: {s_sig[key][:1]}\n"
            f"  First parallel:    {p_sig[key][:1]}"
        )


# ---------------------------------------------------------------------------
# 6. Worker exception surfaces as per-key WARNING (no crash)
# ---------------------------------------------------------------------------


def test_worker_exception_becomes_warning(tmp_path: Path, monkeypatch):
    """Patch ``parallel_scan`` in the validate_scoring namespace so it
    returns a synthetic error ScanResult for every unit. The merge code
    path must catch the error and add one per-unit WARNING — NEVER raise
    out of ``run_all_validators``.

    Mirrors A6's test_worker_exception_becomes_warning approach: we
    patch the in-process binding of ``parallel_scan``, not the worker
    body itself, because the harness pickles the function object at
    submit time and a parent-process mock has no effect on real workers.
    Patching the validator's import keeps the test in-process while
    still exercising the same merge code that handles a real worker
    crash.
    """
    plugin = _make_base_plugin(tmp_path)

    from cpv_parallel_runner import ScanResult

    def fake_parallel_scan(units, fn, **kwargs):  # noqa: ARG001
        return [
            ScanResult(
                file_path=u,
                findings=[],
                error="RuntimeError: synthetic scoring-worker crash",
            )
            for u in units
        ]

    monkeypatch.setattr(validate_scoring, "parallel_scan", fake_parallel_scan)
    monkeypatch.setenv("CPV_SCORING_PARALLEL", "1")

    reports = run_all_validators(plugin)

    # At minimum the always-built plugin + security units should produce
    # a WARNING under their target keys. ``run_all_validators`` must NOT
    # have raised.
    assert "plugin" in reports
    assert "security" in reports

    for key in ("plugin", "security"):
        warnings = [r for r in reports[key].results if r.level == "WARNING"]
        assert warnings, (
            f"Expected at least one worker-error WARNING under {key!r}, "
            f"got: {reports[key].results}"
        )
        for w in warnings:
            assert "synthetic scoring-worker crash" in w.message
            assert "RuntimeError" in w.message


# ---------------------------------------------------------------------------
# 7. Env-var switch parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("False", False),
        ("no", False),
        ("NO", False),
        ("off", False),
        ("OFF", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        # Any unrecognised value → default = parallel
        ("banana", True),
        ("", True),  # explicitly set to empty → still parallel
    ],
)
def test_scoring_parallel_enabled_env_var_parsing(monkeypatch, value, expected):
    """``_scoring_parallel_enabled`` must recognise the documented disable
    values (0/false/no/off, case-insensitive) AND default to parallel
    for any other value. Consistency with A6's CPV_HOOK_PARALLEL and
    A8's CPV_CACHE_PARALLEL parsers.
    """
    monkeypatch.setenv("CPV_SCORING_PARALLEL", value)
    assert _scoring_parallel_enabled() is expected


def test_scoring_parallel_enabled_unset_defaults_to_parallel(monkeypatch):
    """When CPV_SCORING_PARALLEL is unset entirely (most common case),
    the function returns True so the parallel path is the default.
    """
    monkeypatch.delenv("CPV_SCORING_PARALLEL", raising=False)
    assert _scoring_parallel_enabled() is True


# ---------------------------------------------------------------------------
# 8. compute_quality_score parity (end-to-end smoke)
# ---------------------------------------------------------------------------


def test_compute_quality_score_status_parity(tmp_path: Path):
    """compute_quality_score must produce the same status and the same
    overall_score under serial and parallel execution. End-to-end smoke
    that the parallel branch doesn't break the user-facing score.
    """
    plugin = _make_base_plugin(tmp_path)

    prev = os.environ.get("CPV_SCORING_PARALLEL")
    try:
        os.environ["CPV_SCORING_PARALLEL"] = "0"
        r_serial = compute_quality_score(plugin)

        os.environ["CPV_SCORING_PARALLEL"] = "1"
        r_parallel = compute_quality_score(plugin)
    finally:
        if prev is None:
            os.environ.pop("CPV_SCORING_PARALLEL", None)
        else:
            os.environ["CPV_SCORING_PARALLEL"] = prev

    # Status must match
    assert r_serial.status == r_parallel.status, (
        f"Status differs: serial={r_serial.status}, parallel={r_parallel.status}"
    )

    # Overall score must match (allowing for tiny float jitter).
    assert abs(r_serial.overall_score - r_parallel.overall_score) < 0.001, (
        f"Overall score drift: serial={r_serial.overall_score}, "
        f"parallel={r_parallel.overall_score}"
    )

    # Same set of category names + same per-category scores.
    s_cats = {n: c.score for n, c in r_serial.category_scores.items()}
    p_cats = {n: c.score for n, c in r_parallel.category_scores.items()}
    assert s_cats.keys() == p_cats.keys()
    for name in s_cats:
        assert abs(s_cats[name] - p_cats[name]) < 0.001, (
            f"Category {name!r} score drift: "
            f"serial={s_cats[name]}, parallel={p_cats[name]}"
        )


# ---------------------------------------------------------------------------
# 9. Public API unchanged
# ---------------------------------------------------------------------------


def test_public_run_all_validators_signature_unchanged():
    """``run_all_validators(plugin_path)`` is the documented public API.
    The refactor preserves that signature. Catches a future refactor
    that silently changes the arg order or adds required params.
    """
    sig = inspect.signature(validate_scoring.run_all_validators)
    params = list(sig.parameters)
    assert params == ["plugin_path"], (
        f"run_all_validators signature changed: {params}"
    )


def test_public_compute_quality_score_signature_unchanged():
    sig = inspect.signature(validate_scoring.compute_quality_score)
    assert list(sig.parameters) == ["plugin_path"]


# ---------------------------------------------------------------------------
# 10. _build_scoring_work_units order matches serial loop order
# ---------------------------------------------------------------------------


def test_build_scoring_work_units_visits_in_serial_order(tmp_path: Path):
    """``_build_scoring_work_units`` MUST visit tasks in the same order
    the legacy serial loop did: plugin → security → hooks → mcp →
    agents → skills → commands. If this drifts, the parallel branch's
    merge loop emits findings in a different sequence than the serial
    branch and the parity tests above blow up — but this test catches
    the issue earlier and points directly at the work-unit builder.
    """
    plugin = _make_base_plugin(tmp_path)
    _seed_multi_kind_plugin(plugin)

    units = _build_scoring_work_units(plugin)

    kinds_in_order = [u.kind for u in units]

    # plugin and security are always first two.
    assert kinds_in_order[0] == "plugin"
    assert kinds_in_order[1] == "security"

    # hooks (if present) precedes mcp (if present).
    if "hooks" in kinds_in_order:
        i_hooks = kinds_in_order.index("hooks")
        if "mcp" in kinds_in_order:
            i_mcp = kinds_in_order.index("mcp")
            assert i_hooks < i_mcp, (
                f"hooks must precede mcp; got order: {kinds_in_order}"
            )

    # mcp (if present) precedes agents (if present).
    if "mcp" in kinds_in_order and "agent" in kinds_in_order:
        i_mcp = kinds_in_order.index("mcp")
        i_first_agent = kinds_in_order.index("agent")
        assert i_mcp < i_first_agent, (
            f"mcp must precede agents; got order: {kinds_in_order}"
        )

    # agents block precedes skills block precedes commands block.
    if "agent" in kinds_in_order and "skill" in kinds_in_order:
        last_agent = max(i for i, k in enumerate(kinds_in_order) if k == "agent")
        first_skill = next(i for i, k in enumerate(kinds_in_order) if k == "skill")
        assert last_agent < first_skill, (
            f"agents must precede skills; got order: {kinds_in_order}"
        )
    if "skill" in kinds_in_order and "command" in kinds_in_order:
        last_skill = max(i for i, k in enumerate(kinds_in_order) if k == "skill")
        first_cmd = next(i for i, k in enumerate(kinds_in_order) if k == "command")
        assert last_skill < first_cmd, (
            f"skills must precede commands; got order: {kinds_in_order}"
        )


def test_build_scoring_work_units_empty_optional_branches(tmp_path: Path):
    """A minimal plugin with no hooks/mcp/agents/skills/commands builds
    EXACTLY two work units: plugin + security. Pins the optional-branch
    guards in the work-unit builder.
    """
    plugin = _make_base_plugin(tmp_path)
    units = _build_scoring_work_units(plugin)
    kinds = [u.kind for u in units]
    assert kinds == ["plugin", "security"], (
        f"Minimal plugin should yield exactly [plugin, security]; got {kinds}"
    )
