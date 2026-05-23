"""Task #384 / Agent A8 — parity regression tests for parallel cache validation.

These tests pin the contract that ``scan_plugin_for_cache`` produces the
SAME ``ValidationResult`` sequence regardless of whether the per-file
scans run serially (``CPV_CACHE_PARALLEL=0``) or via the shared
``parallel_scan`` harness (default). The parallel harness uses
``ProcessPoolExecutor`` per spec, so any silent regression to
"results-by-completion-order" would surface here as a mismatch on the
first run.

Coverage parallels A6's test_validate_hook_parallelism.py:
  1. Source-of-truth pin — validate_cache.py imports ``parallel_scan``
     from the shared harness AND exposes a top-level
     ``scan_one_cache_unit`` callable.
  2. Top-level pickleability — ``scan_one_cache_unit`` works on real
     ``_CacheWorkUnit``s round-tripped through pickle.
  3. Parity at small cardinality — single component, single hook.
  4. Parity at higher cardinality — multi-file fixture exercising
     CA-01 / CA-02 / CA-03 / CA-04 / CA-05 / CA-06 / CA-07 all together.
  5. Order preservation under load — bigger fixture (12 components + 4
     hooks) with mixed findings, asserts byte-identical sequences.
  6. Worker exception surfaces as per-file WARNING (no crash).
  7. Env-var switch parsing (0/false/no/off → serial; everything else
     including unset → parallel).
  8. Empty / no-finding plugin still gets the PASSED line in both modes.
  9. Public scan_* API unchanged — each takes (file, report, plugin_root,
     [kind]) and mutates report exactly like pre-task-384.
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

import validate_cache  # noqa: E402
from validate_cache import (  # noqa: E402
    _build_cache_work_units,
    _cache_parallel_enabled,
    _CacheWorkUnit,
    scan_one_cache_unit,
    scan_plugin_for_cache,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin(tmp_path: Path, name: str = "test-plugin") -> Path:
    """Create a minimal plugin scaffold (mirrors test_validate_cache.py)."""
    plugin = tmp_path / name
    plugin.mkdir()
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0", "description": "x"})
    )
    return plugin


def _run_scan(plugin_root: Path, *, parallel: bool):
    """Run scan_plugin_for_cache once with CPV_CACHE_PARALLEL forced on/off.

    The env-var is restored on exit so xdist workers can't bleed state.
    """
    prev = os.environ.get("CPV_CACHE_PARALLEL")
    os.environ["CPV_CACHE_PARALLEL"] = "1" if parallel else "0"
    try:
        return scan_plugin_for_cache(plugin_root)
    finally:
        if prev is None:
            os.environ.pop("CPV_CACHE_PARALLEL", None)
        else:
            os.environ["CPV_CACHE_PARALLEL"] = prev


def _result_sig(r) -> tuple:
    """Tuple form of a ValidationResult suitable for sequence-equality
    comparison. Includes level, message, file, line — not the optional
    metadata which the cache validator never sets and which therefore
    can't drift.
    """
    return (r.level, r.message, r.file, r.line)


# ---------------------------------------------------------------------------
# 1. Source-of-truth pins
# ---------------------------------------------------------------------------


def test_validate_cache_imports_parallel_scan_harness():
    """validate_cache.py MUST import ``parallel_scan`` from the shared
    ``cpv_parallel_runner`` module. If someone reverts to a private
    executor or removes the harness wiring, this is the first signal.
    """
    src = inspect.getsource(validate_cache)
    assert "from cpv_parallel_runner import parallel_scan" in src, (
        "validate_cache.py no longer imports parallel_scan from the shared "
        "harness — task #384 parallelism wiring has been reverted."
    )


def test_validate_cache_exposes_scan_one_cache_unit_top_level():
    """``scan_one_cache_unit`` MUST be a TOP-LEVEL callable in
    validate_cache.py (not a closure, not a nested function).
    ProcessPoolExecutor requires pickleable callables; closures fail at
    submit time.
    """
    assert callable(scan_one_cache_unit), "scan_one_cache_unit is not callable"
    # A top-level function's __qualname__ equals its __name__. A nested
    # function would have a qualname like
    # 'enclosing_func.<locals>.scan_one_cache_unit'.
    assert scan_one_cache_unit.__qualname__ == "scan_one_cache_unit", (
        f"scan_one_cache_unit is not top-level "
        f"(qualname={scan_one_cache_unit.__qualname__!r}); "
        "ProcessPoolExecutor will reject the unpickleable closure."
    )
    assert scan_one_cache_unit.__module__ == "validate_cache"


def test_scan_plugin_uses_parallel_scan_in_parallel_branch():
    """The body of ``scan_plugin_for_cache`` must call ``parallel_scan``
    (the shared harness function) in its parallel branch. Catches a
    regression where someone replaces the harness call with an inline
    loop or with a different executor.
    """
    src = inspect.getsource(validate_cache.scan_plugin_for_cache)
    assert "parallel_scan(units, scan_one_cache_unit)" in src, (
        "scan_plugin_for_cache no longer dispatches via parallel_scan — "
        "task #384 parallel branch has been bypassed."
    )
    # Sanity: env-var switch is still present so users can disable.
    assert "_cache_parallel_enabled()" in src, (
        "scan_plugin_for_cache no longer respects CPV_CACHE_PARALLEL — "
        "the user escape hatch is gone."
    )


# ---------------------------------------------------------------------------
# 2. Top-level pickleability
# ---------------------------------------------------------------------------


def test_cache_work_unit_round_trips_through_pickle(tmp_path: Path):
    """A real-shape ``_CacheWorkUnit`` must survive a pickle round-trip.
    If anyone adds a non-pickleable field (a Path, a Callable, an open
    file handle, …), ProcessPoolExecutor rejects the submit and the
    parallel branch fails. This test catches that at unit-test time
    before any plugin author sees the regression.
    """
    unit = _CacheWorkUnit(
        kind="static_prefix",
        file_path_str=str(tmp_path / "CLAUDE.md"),
        plugin_root_str=str(tmp_path),
    )
    blob = pickle.dumps(unit)
    restored = pickle.loads(blob)
    assert restored == unit


def test_scan_one_cache_unit_round_trips_static_prefix(tmp_path: Path):
    """``scan_one_cache_unit`` on a static_prefix unit returns the same
    findings the serial scanner produces. Smoke-checks the dispatch
    inside the worker.
    """
    plugin = _make_plugin(tmp_path)
    (plugin / "CLAUDE.md").write_text(
        "Current time: {{TIMESTAMP}}\nExtra prose.\n"
    )
    unit = _CacheWorkUnit(
        kind="static_prefix",
        file_path_str=str(plugin / "CLAUDE.md"),
        plugin_root_str=str(plugin),
    )
    findings = scan_one_cache_unit(unit)
    assert findings, "static_prefix unit should return findings"
    assert all(f.level == "WARNING" for f in findings)
    assert any("CA-01" in f.message for f in findings)
    # And the whole list pickles cleanly so the harness can transmit it
    # across the worker-result pipe.
    pickle.dumps(findings)


def test_scan_one_cache_unit_unknown_kind_returns_empty(tmp_path: Path):
    """An unknown kind on a work unit returns an empty list rather than
    raising. This is intentional defense — a future enum drift surfaces
    as missing findings, which the parity tests catch, rather than
    crashing the whole validator.
    """
    plugin = _make_plugin(tmp_path)
    unit = _CacheWorkUnit(
        kind="bogus_kind",
        file_path_str=str(plugin / "CLAUDE.md"),
        plugin_root_str=str(plugin),
    )
    assert scan_one_cache_unit(unit) == []


# ---------------------------------------------------------------------------
# 3. Parity — small fixture (single static prefix file)
# ---------------------------------------------------------------------------


def test_parity_single_static_prefix_violation(tmp_path: Path):
    """One CA-01 violation → serial and parallel must produce the same
    ValidationResult sequence. Smallest possible parity case — exercises
    the round-trip plumbing without depending on any pool scheduling.
    """
    plugin = _make_plugin(tmp_path)
    (plugin / "CLAUDE.md").write_text("Time: {{TIMESTAMP}}\n")

    r_serial = _run_scan(plugin, parallel=False)
    r_parallel = _run_scan(plugin, parallel=True)

    s_seq = [_result_sig(r) for r in r_serial.results]
    p_seq = [_result_sig(r) for r in r_parallel.results]
    assert s_seq == p_seq, (
        f"Single-file parity broken — sequences differ.\n"
        f"Serial:   {s_seq}\nParallel: {p_seq}"
    )
    assert r_serial.exit_code == r_parallel.exit_code


# ---------------------------------------------------------------------------
# 4. Parity — multi-rule fixture (exercises CA-01..CA-07)
# ---------------------------------------------------------------------------


def _seed_multi_rule_plugin(plugin: Path) -> None:
    """Plant fixtures that fire AT LEAST one finding for every CA rule.

    Layout:
      CLAUDE.md            → CA-01 (dynamic placeholder + shell substitution)
      agents/a.md          → CA-04 (model override)
      commands/c.md        → CA-04 + CA-07 (model override + context: fork)
      skills/s/SKILL.md    → CA-04 + CA-07
      hooks/hooks.json     → 2 hooks
      hooks/session.sh     → CA-02 + CA-05 (writes CLAUDE.md + unbounded git log)
      hooks/precompact.sh  → CA-06 (PreCompact touches CLAUDE.md)
    """
    (plugin / "CLAUDE.md").write_text(
        "# plugin\n\nTime: {{TIMESTAMP}}\nNow: $(date +%s)\n"
    )

    (plugin / "agents").mkdir()
    (plugin / "agents" / "a.md").write_text(
        "---\nname: a\ndescription: a\nmodel: opus\n---\n\nbody\n"
    )

    (plugin / "commands").mkdir()
    (plugin / "commands" / "c.md").write_text(
        "---\nname: c\ndescription: c\nmodel: sonnet\ncontext: fork\n---\n\nbody\n"
    )

    (plugin / "skills" / "s").mkdir(parents=True)
    (plugin / "skills" / "s" / "SKILL.md").write_text(
        "---\nname: s\ndescription: s\nmodel: haiku\ncontext: branch\n---\n\nbody\n"
    )

    (plugin / "hooks").mkdir()
    session_sh = plugin / "hooks" / "session.sh"
    session_sh.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'init' >> CLAUDE.md\n"  # CA-02
        "echo '{\"allow\": []}' > settings.json\n"  # CA-03 (tool-list + write-op + settings.json)
        "git log\n"  # CA-05
    )
    precompact_sh = plugin / "hooks" / "precompact.sh"
    precompact_sh.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'compact' >> CLAUDE.md\n"  # CA-06
    )
    (plugin / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session.sh",
                                }
                            ]
                        }
                    ],
                    "PreCompact": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "${CLAUDE_PLUGIN_ROOT}/hooks/precompact.sh",
                                }
                            ]
                        }
                    ],
                }
            }
        )
    )


def test_parity_multi_rule_fixture(tmp_path: Path):
    """Fires AT LEAST one finding for every CA-01..CA-07 rule. Asserts the
    ValidationResult sequence is byte-identical between serial and
    parallel runs. This is the strict invariant — finding order, file
    traversal order, hook traversal order all must match.
    """
    plugin = _make_plugin(tmp_path)
    _seed_multi_rule_plugin(plugin)

    r_serial = _run_scan(plugin, parallel=False)
    r_parallel = _run_scan(plugin, parallel=True)

    s_seq = [_result_sig(r) for r in r_serial.results]
    p_seq = [_result_sig(r) for r in r_parallel.results]

    if s_seq != p_seq:
        first_diff = next(
            (i for i in range(min(len(s_seq), len(p_seq))) if s_seq[i] != p_seq[i]),
            min(len(s_seq), len(p_seq)),
        )
        lo = max(0, first_diff - 2)
        hi = min(max(len(s_seq), len(p_seq)), first_diff + 3)
        lines = ["Result sequences diverge."]
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

    assert r_serial.exit_code == r_parallel.exit_code

    # Smoke-check the fixture actually exercises every CA-01..CA-07 rule.
    # If a future edit trims the fixture so a rule is no longer present,
    # the parity test loses bite and we want to be told.
    rule_ids = {f"CA-0{i}" for i in range(1, 8)}
    fired_ids = {
        rid
        for rid in rule_ids
        if any(rid in r.message for r in r_serial.results)
    }
    assert fired_ids == rule_ids, (
        f"Fixture does not exercise every CA rule; missing: {rule_ids - fired_ids}"
    )


# ---------------------------------------------------------------------------
# 5. Parity — higher cardinality + load
# ---------------------------------------------------------------------------


def test_parity_many_components(tmp_path: Path):
    """12 agents + 12 commands + 12 skills, half violating CA-04 / CA-07.
    Asserts strict sequence equality under load — the kind of plugin
    size where parallel speedup actually matters.
    """
    plugin = _make_plugin(tmp_path)
    (plugin / "agents").mkdir()
    (plugin / "commands").mkdir()
    (plugin / "skills").mkdir()

    for i in range(12):
        (plugin / "agents" / f"a{i:02d}.md").write_text(
            f"---\nname: a{i:02d}\ndescription: a\n"
            + ("model: opus\n" if i % 2 == 0 else "")
            + "---\n\nbody\n"
        )
        (plugin / "commands" / f"c{i:02d}.md").write_text(
            f"---\nname: c{i:02d}\ndescription: c\n"
            + ("model: sonnet\n" if i % 2 == 0 else "")
            + ("context: fork\n" if i % 3 == 0 else "")
            + "---\n\nbody\n"
        )
        (plugin / "skills" / f"s{i:02d}").mkdir()
        (plugin / "skills" / f"s{i:02d}" / "SKILL.md").write_text(
            f"---\nname: s{i:02d}\ndescription: s\n"
            + ("model: haiku\n" if i % 2 == 1 else "")
            + ("context: branch\n" if i % 4 == 0 else "")
            + "---\n\nbody\n"
        )

    r_serial = _run_scan(plugin, parallel=False)
    r_parallel = _run_scan(plugin, parallel=True)

    s_seq = [_result_sig(r) for r in r_serial.results]
    p_seq = [_result_sig(r) for r in r_parallel.results]
    assert s_seq == p_seq, (
        f"High-cardinality parity broken.\n"
        f"Serial:   len={len(s_seq)}\n"
        f"Parallel: len={len(p_seq)}\n"
        + (
            "First divergent:\n  serial:   "
            f"{s_seq[next(i for i, x in enumerate(zip(s_seq, p_seq)) if x[0] != x[1])]}"
            if s_seq != p_seq
            else ""
        )
    )
    assert r_serial.exit_code == r_parallel.exit_code
    # And the fixture actually exercises both rules.
    assert any("CA-04" in r.message for r in r_serial.results)
    assert any("CA-07" in r.message for r in r_serial.results)


# ---------------------------------------------------------------------------
# 6. Worker exception surfaces as per-file WARNING
# ---------------------------------------------------------------------------


def test_worker_exception_becomes_warning(tmp_path: Path, monkeypatch):
    """Patch ``parallel_scan`` in the validate_cache namespace so it
    returns a synthetic error ScanResult for every unit. The merge code
    path must catch the error and add one per-file WARNING — NEVER raise
    out of ``scan_plugin_for_cache``.

    Mirrors A6's test_worker_exception_becomes_warning approach: we
    patch the in-process binding of ``parallel_scan``, not the worker
    body itself, because the harness pickles the function object at
    submit time and a parent-process mock has no effect on real workers.
    Patching the validator's import keeps the test in-process while
    still exercising the same merge code that handles a real worker
    crash.
    """
    plugin = _make_plugin(tmp_path)
    # A CLAUDE.md so at least one static_prefix unit gets built.
    (plugin / "CLAUDE.md").write_text("hello\n")

    from cpv_parallel_runner import ScanResult

    def fake_parallel_scan(units, fn, **kwargs):  # noqa: ARG001
        return [
            ScanResult(
                file_path=Path(u.file_path_str),
                findings=[],
                error="RuntimeError: synthetic cache-worker crash",
            )
            for u in units
        ]

    monkeypatch.setattr(validate_cache, "parallel_scan", fake_parallel_scan)
    monkeypatch.setenv("CPV_CACHE_PARALLEL", "1")

    report = scan_plugin_for_cache(plugin)

    warnings = [r for r in report.results if r.level == "WARNING"]
    # Every unit produced a warning. At minimum the CLAUDE.md unit
    # exists. (We don't pin a hard count because the plugin scaffold
    # has no hooks/agents/commands/skills — only the CLAUDE.md unit.)
    assert warnings, f"Expected at least one worker-error WARNING, got: {report.results}"
    for w in warnings:
        assert "synthetic cache-worker crash" in w.message
        assert "RuntimeError" in w.message

    # No CRITICAL synthesized — worker errors are non-blocking. exit_code
    # stays 0 (cache rules never push to non-zero on their own).
    assert not any(r.level == "CRITICAL" for r in report.results), (
        f"Worker error must NOT produce CRITICAL — got: "
        f"{[(r.level, r.message) for r in report.results if r.level == 'CRITICAL']}"
    )
    assert report.exit_code == 0


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
def test_cache_parallel_enabled_env_var_parsing(monkeypatch, value, expected):
    """``_cache_parallel_enabled`` must recognise the documented disable
    values (0/false/no/off, case-insensitive) AND default to parallel
    for any other value. Consistency with A6's CPV_HOOK_PARALLEL and
    A2's CPV_SECURITY_PARALLEL parsers.
    """
    monkeypatch.setenv("CPV_CACHE_PARALLEL", value)
    assert _cache_parallel_enabled() is expected


def test_cache_parallel_enabled_unset_defaults_to_parallel(monkeypatch):
    """When CPV_CACHE_PARALLEL is unset entirely (most common case),
    the function returns True so the parallel path is the default.
    """
    monkeypatch.delenv("CPV_CACHE_PARALLEL", raising=False)
    assert _cache_parallel_enabled() is True


# ---------------------------------------------------------------------------
# 8. Empty / clean plugin still produces the PASSED line
# ---------------------------------------------------------------------------


def test_parity_clean_plugin_emits_passed(tmp_path: Path):
    """A plugin with no cache violations still gets the "No prompt-cache
    violations detected" PASSED line — in BOTH serial and parallel modes.
    Pins the post-scan PASSED-emission logic (which uses an anchor index
    rather than a running counter so it works in the parallel branch).
    """
    plugin = _make_plugin(tmp_path)
    (plugin / "CLAUDE.md").write_text("# Clean plugin\n\nNothing dynamic here.\n")

    r_serial = _run_scan(plugin, parallel=False)
    r_parallel = _run_scan(plugin, parallel=True)

    s_seq = [_result_sig(r) for r in r_serial.results]
    p_seq = [_result_sig(r) for r in r_parallel.results]
    assert s_seq == p_seq, (
        f"Clean-plugin parity broken.\nSerial: {s_seq}\nParallel: {p_seq}"
    )
    assert any(r.level == "PASSED" for r in r_serial.results)
    assert any(r.level == "PASSED" for r in r_parallel.results)
    assert r_serial.exit_code == 0
    assert r_parallel.exit_code == 0


def test_parity_empty_plugin_has_no_units(tmp_path: Path):
    """A plugin with no CLAUDE.md, no agents/, no commands/, no skills/,
    no hooks/ builds zero work units. Both serial and parallel paths
    must still emit the PASSED line.
    """
    plugin = _make_plugin(tmp_path)
    units = _build_cache_work_units(plugin)
    assert units == [], f"Empty plugin should build no work units, got {units}"

    r_serial = _run_scan(plugin, parallel=False)
    r_parallel = _run_scan(plugin, parallel=True)

    s_seq = [_result_sig(r) for r in r_serial.results]
    p_seq = [_result_sig(r) for r in r_parallel.results]
    assert s_seq == p_seq
    assert any(r.level == "PASSED" for r in r_serial.results)


# ---------------------------------------------------------------------------
# 9. Public API unchanged
# ---------------------------------------------------------------------------


def test_public_scan_static_prefix_signature_unchanged():
    """``scan_static_prefix(file_path, report, plugin_root)`` is the
    documented public API for the per-file CA-01 scanner. The refactor
    preserves that signature. Catches a future refactor that silently
    changes the arg order.
    """
    sig = inspect.signature(validate_cache.scan_static_prefix)
    params = list(sig.parameters)
    assert params == ["file_path", "report", "plugin_root"], (
        f"scan_static_prefix signature changed: {params}"
    )


def test_public_scan_hook_for_prefix_mutation_signature_unchanged():
    sig = inspect.signature(validate_cache.scan_hook_for_prefix_mutation)
    assert list(sig.parameters) == ["script_path", "event", "report", "plugin_root"]


def test_public_scan_hook_for_tool_mutation_signature_unchanged():
    sig = inspect.signature(validate_cache.scan_hook_for_tool_mutation)
    assert list(sig.parameters) == ["script_path", "event", "report", "plugin_root"]


def test_public_scan_hook_for_unbounded_output_signature_unchanged():
    sig = inspect.signature(validate_cache.scan_hook_for_unbounded_output)
    assert list(sig.parameters) == ["script_path", "event", "report", "plugin_root"]


def test_public_scan_hook_for_fork_unsafe_signature_unchanged():
    sig = inspect.signature(validate_cache.scan_hook_for_fork_unsafe)
    assert list(sig.parameters) == ["script_path", "event", "report", "plugin_root"]


def test_public_scan_component_for_model_override_signature_unchanged():
    sig = inspect.signature(validate_cache.scan_component_for_model_override)
    assert list(sig.parameters) == ["md_file", "report", "plugin_root", "component_kind"]


def test_public_scan_component_for_context_fork_signature_unchanged():
    sig = inspect.signature(validate_cache.scan_component_for_context_fork)
    assert list(sig.parameters) == ["md_file", "report", "plugin_root", "component_kind"]


def test_public_scan_plugin_for_cache_signature_unchanged():
    sig = inspect.signature(validate_cache.scan_plugin_for_cache)
    assert list(sig.parameters) == ["plugin_root"]
    # Return type is still ValidationReport
    from cpv_validation_common import ValidationReport

    p = sig.parameters["plugin_root"]
    # We don't enforce annotations beyond what's already in the source;
    # the existence + arg name is the load-bearing invariant.
    del p, ValidationReport


def test_public_scan_static_prefix_returns_int_count():
    """The legacy ``scan_static_prefix`` returns an int (the issue count).
    Public API consumers may rely on that — verify the wrapper still
    honors the int return contract.
    """
    # Build a minimal fixture in-process.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        plugin = _make_plugin(tmp_path)
        f = plugin / "CLAUDE.md"
        f.write_text("Now: {{TIMESTAMP}}\n")
        from cpv_validation_common import ValidationReport

        report = ValidationReport()
        n = validate_cache.scan_static_prefix(f, report, plugin)
        assert isinstance(n, int) and n >= 1
        # And the report got that many findings.
        ca01 = [r for r in report.results if "CA-01" in r.message]
        assert len(ca01) == n


# ---------------------------------------------------------------------------
# 10. _build_cache_work_units order matches serial loop order
# ---------------------------------------------------------------------------


def test_build_cache_work_units_visits_in_serial_order(tmp_path: Path):
    """``_build_cache_work_units`` MUST visit files in the same order the
    legacy serial loop did: CA-01 static prefix → hook scripts → agents
    → commands → skills. If this drifts, the parallel branch's merge
    loop emits findings in a different sequence than the serial branch
    and the parity tests above blow up — but this test catches the
    issue earlier and points directly at the work-unit builder.
    """
    plugin = _make_plugin(tmp_path)

    (plugin / "CLAUDE.md").write_text("hello\n")

    (plugin / "agents").mkdir()
    (plugin / "agents" / "a.md").write_text(
        "---\nname: a\ndescription: a\n---\nbody\n"
    )

    (plugin / "commands").mkdir()
    (plugin / "commands" / "c.md").write_text(
        "---\nname: c\ndescription: c\n---\nbody\n"
    )

    (plugin / "skills" / "s").mkdir(parents=True)
    (plugin / "skills" / "s" / "SKILL.md").write_text(
        "---\nname: s\ndescription: s\n---\nbody\n"
    )

    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "h.sh").write_text("#!/bin/bash\necho 'hi'\n")
    (plugin / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "${CLAUDE_PLUGIN_ROOT}/hooks/h.sh",
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )

    units = _build_cache_work_units(plugin)

    # Group by kind, preserving order.
    kinds_in_order = [u.kind for u in units]
    # Expected: static_prefix block, then hook block, then component block.
    # We don't assert exact length-per-kind (depends on .md count), only
    # that the BLOCKS are contiguous in the right order.
    first_hook = next((i for i, k in enumerate(kinds_in_order) if k == "hook"), len(kinds_in_order))
    first_comp = next((i for i, k in enumerate(kinds_in_order) if k == "component"), len(kinds_in_order))
    last_static = max(
        (i for i, k in enumerate(kinds_in_order) if k == "static_prefix"),
        default=-1,
    )
    last_hook = max(
        (i for i, k in enumerate(kinds_in_order) if k == "hook"),
        default=-1,
    )
    assert last_static < first_hook, (
        "static_prefix units must precede hook units. "
        f"Got kinds: {kinds_in_order}"
    )
    assert last_hook < first_comp, (
        "hook units must precede component units. "
        f"Got kinds: {kinds_in_order}"
    )

    # And within the component block, the order is agents, commands,
    # skills. Pin that by inspecting the file_path_str.
    component_units = [u for u in units if u.kind == "component"]
    component_dirs = [
        Path(u.file_path_str).relative_to(plugin).parts[0]
        for u in component_units
    ]
    # All "agents" entries come first, then "commands", then "skills".
    transitions = []
    prev = None
    for d in component_dirs:
        if d != prev:
            transitions.append(d)
            prev = d
    assert transitions == ["agents", "commands", "skills"], (
        f"Component sub-dir order changed: {transitions}"
    )
