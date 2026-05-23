"""Task #384 / Agent A10 — parity regression tests for parallel rules validation.

These tests pin the contract that ``validate_rules_directory`` produces the
SAME ``ValidationResult`` sequence regardless of whether the per-file
scans run serially (``CPV_RULES_PARALLEL=0``) or via the shared
``parallel_scan`` harness (default). The parallel harness uses
``ProcessPoolExecutor`` per spec, so any silent regression to
"results-by-completion-order" would surface here as a mismatch on the
first run.

Coverage parallels A8's test_validate_cache_parallelism.py:
  1. Source-of-truth pin — validate_rules.py imports ``parallel_scan``
     from the shared harness AND exposes a top-level
     ``_scan_one_rule_file`` callable.
  2. Top-level pickleability — ``_scan_one_rule_file`` works on real
     ``_RuleWorkUnit``s round-tripped through pickle.
  3. Parity at small cardinality — single rule file.
  4. Parity at higher cardinality — multi-file fixture exercising
     valid + secret + private-path + bad-frontmatter findings together.
  5. Order preservation under load — 50-file fixture with mixed
     findings, asserts byte-identical sequences.
  6. Worker exception surfaces as per-file WARNING (no crash).
  7. Env-var switch parsing (0/false/no/off → serial; everything else
     including unset → parallel).
  8. Empty / no-finding plugin still gets the PASSED line in both modes.
  9. Public API unchanged — ``validate_rule_file``,
     ``validate_rules_directory`` signatures untouched.
 10. Single-file fixture forces the serial branch (``len(units) > 1``
     gate), and the result is still bit-identical.
"""

from __future__ import annotations

import inspect
import os
import pickle
import sys
from pathlib import Path

import pytest

# tests/conftest.py adds scripts/ to sys.path; this is a defensive duplicate.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_rules  # noqa: E402
from validate_rules import (  # noqa: E402
    _rules_parallel_enabled,
    _RuleWorkUnit,
    _scan_one_rule_file,
    validate_rule_file,
    validate_rules_directory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rules_dir(tmp_path: Path, n_files: int = 1) -> Path:
    """Create a minimal rules/ scaffold with ``n_files`` clean rule files."""
    rules = tmp_path / "rules"
    rules.mkdir()
    for i in range(n_files):
        (rules / f"rule-{i:03d}.md").write_text(
            f"---\npaths:\n  - src/**\n---\n\n# Rule {i}\n\nBody {i}.\n",
            encoding="utf-8",
        )
    return rules


def _run_scan(rules_dir: Path, *, parallel: bool, plugin_root: Path | None = None):
    """Run validate_rules_directory once with CPV_RULES_PARALLEL forced on/off.

    The env-var is restored on exit so xdist workers can't bleed state.
    """
    prev = os.environ.get("CPV_RULES_PARALLEL")
    os.environ["CPV_RULES_PARALLEL"] = "1" if parallel else "0"
    try:
        return validate_rules_directory(rules_dir, plugin_root=plugin_root)
    finally:
        if prev is None:
            os.environ.pop("CPV_RULES_PARALLEL", None)
        else:
            os.environ["CPV_RULES_PARALLEL"] = prev


def _result_sig(r) -> tuple:
    """Tuple form of a ValidationResult suitable for sequence-equality
    comparison. Includes level, message, file, line — not the optional
    metadata which the rules validator never sets and which therefore
    can't drift.
    """
    return (r.level, r.message, r.file, r.line)


# ---------------------------------------------------------------------------
# 1. Source-of-truth pins
# ---------------------------------------------------------------------------


def test_validate_rules_imports_parallel_scan_harness():
    """validate_rules.py MUST import ``parallel_scan`` from the shared
    ``cpv_parallel_runner`` module. If someone reverts to a private
    executor or removes the harness wiring, this is the first signal.
    """
    src = inspect.getsource(validate_rules)
    assert "from cpv_parallel_runner import" in src and "parallel_scan" in src, (
        "validate_rules.py no longer imports parallel_scan from the shared "
        "harness — task #384 parallelism wiring has been reverted."
    )


def test_validate_rules_exposes_scan_one_rule_file_top_level():
    """``_scan_one_rule_file`` MUST be a TOP-LEVEL callable in
    validate_rules.py (not a closure, not a nested function).
    ProcessPoolExecutor requires pickleable callables; closures fail at
    submit time.
    """
    assert callable(_scan_one_rule_file), "_scan_one_rule_file is not callable"
    # A top-level function's __qualname__ equals its __name__. A nested
    # function would have a qualname like
    # 'enclosing_func.<locals>._scan_one_rule_file'.
    assert _scan_one_rule_file.__qualname__ == "_scan_one_rule_file", (
        f"_scan_one_rule_file is not top-level "
        f"(qualname={_scan_one_rule_file.__qualname__!r}); "
        "ProcessPoolExecutor will reject the unpickleable closure."
    )
    assert _scan_one_rule_file.__module__ == "validate_rules"


def test_validate_rules_directory_uses_parallel_scan():
    """The body of ``validate_rules_directory`` must call ``parallel_scan``
    (the shared harness function). Catches a regression where someone
    replaces the harness call with an inline loop or with a different
    executor.
    """
    src = inspect.getsource(validate_rules.validate_rules_directory)
    assert "parallel_scan(" in src, (
        "validate_rules_directory no longer dispatches via parallel_scan "
        "— task #384 parallel branch has been bypassed."
    )
    # Sanity: env-var switch is still present so users can disable.
    assert "_rules_parallel_enabled()" in src, (
        "validate_rules_directory no longer respects CPV_RULES_PARALLEL — "
        "the user escape hatch is gone."
    )


# ---------------------------------------------------------------------------
# 2. Top-level pickleability
# ---------------------------------------------------------------------------


def test_rule_work_unit_round_trips_through_pickle(tmp_path: Path):
    """A real-shape ``_RuleWorkUnit`` must survive a pickle round-trip.
    If anyone adds a non-pickleable field (a Path, a Callable, an open
    file handle, …), ProcessPoolExecutor rejects the submit and the
    parallel branch fails. This test catches that at unit-test time
    before any plugin author sees the regression.
    """
    unit = _RuleWorkUnit(
        file_path_str=str(tmp_path / "some-rule.md"),
        rel_path="rules/some-rule.md",
    )
    blob = pickle.dumps(unit)
    restored = pickle.loads(blob)
    assert restored == unit


def test_scan_one_rule_file_round_trips_simple_rule(tmp_path: Path):
    """``_scan_one_rule_file`` on a clean rule file returns a
    ``(content, results)`` tuple where content is the file body and
    results has the PASSED line. Smoke-checks the worker's dispatch.
    """
    rules = _make_rules_dir(tmp_path, 1)
    unit = _RuleWorkUnit(
        file_path_str=str(rules / "rule-000.md"),
        rel_path="rules/rule-000.md",
    )
    content, results = _scan_one_rule_file(unit)
    assert isinstance(content, str)
    assert content, "worker should return the file body"
    assert results, "worker should return at least one result (PASSED line)"
    # The PASSED line for a successful rule file mentions the rel_path.
    passed_lines = [r for r in results if r.level == "PASSED"]
    assert passed_lines, f"expected PASSED, got: {[(r.level, r.message) for r in results]}"
    # And the whole tuple pickles cleanly so the harness can transmit it
    # across the worker-result pipe.
    pickle.dumps((content, results))


# ---------------------------------------------------------------------------
# 3. Parity — small fixture (single rule file with frontmatter typo)
# ---------------------------------------------------------------------------


def test_parity_single_file_serial_branch_taken(tmp_path: Path):
    """With ONE rule file, ``len(units) > 1`` is False so the serial
    fallback branch runs even when CPV_RULES_PARALLEL=1. Both paths
    must produce the same ValidationResult sequence — this pins that
    the serial fallback inside the parallel-enabled mode stays
    bit-identical to the explicit CPV_RULES_PARALLEL=0 path.
    """
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "only.md").write_text(
        "---\npath: src/**\n---\n\n# Lone rule\n", encoding="utf-8"
    )
    r_serial = _run_scan(rules, parallel=False, plugin_root=tmp_path)
    r_parallel = _run_scan(rules, parallel=True, plugin_root=tmp_path)

    s_seq = [_result_sig(r) for r in r_serial.results]
    p_seq = [_result_sig(r) for r in r_parallel.results]
    assert s_seq == p_seq, (
        f"Single-file parity broken — sequences differ.\n"
        f"Serial:   {s_seq}\nParallel: {p_seq}"
    )
    assert r_serial.exit_code == r_parallel.exit_code
    # Sanity: the typo'd `path:` (vs the recognised `paths:`) fires the
    # unknown-frontmatter-field MINOR.
    assert any(
        r.level == "MINOR" and "path" in r.message and "Unknown" in r.message
        for r in r_serial.results
    )


# ---------------------------------------------------------------------------
# 4. Parity — multi-rule fixture (exercises every finding category)
# ---------------------------------------------------------------------------


def _seed_multi_finding_rules(rules: Path) -> None:
    """Plant fixtures that fire AT LEAST one finding for every category:
      * clean.md         → PASSED only
      * with-paths.md    → PASSED + paths frontmatter accepted
      * absolute.md      → MAJOR (absolute glob)
      * escape.md        → MAJOR (.. escape)
      * unknown-key.md   → MINOR (unknown frontmatter field)
      * empty.md         → MINOR (empty file)
      * bad-yaml.md      → MAJOR (invalid YAML)
      * non-list-paths.md → MAJOR (paths not a list)
    """
    (rules / "clean.md").write_text("# Clean rule\n\nNothing fancy.\n", encoding="utf-8")
    (rules / "with-paths.md").write_text(
        "---\npaths:\n  - src/**/*.py\n  - tests/*.py\n---\n\n# OK\n",
        encoding="utf-8",
    )
    (rules / "absolute.md").write_text(
        "---\npaths:\n  - /etc/passwd\n---\n\n# nope\n",
        encoding="utf-8",
    )
    (rules / "escape.md").write_text(
        "---\npaths:\n  - ../escape/**\n---\n\n# nope\n",
        encoding="utf-8",
    )
    (rules / "unknown-key.md").write_text(
        "---\npath: src/**\n---\n\n# typo of paths:\n",
        encoding="utf-8",
    )
    (rules / "empty.md").write_text("", encoding="utf-8")
    (rules / "bad-yaml.md").write_text(
        "---\npaths:\n  - [unclosed\n---\n\n# bad\n", encoding="utf-8"
    )
    (rules / "non-list-paths.md").write_text(
        "---\npaths: src/**\n---\n\n# scalar instead of list\n",
        encoding="utf-8",
    )


def test_parity_multi_finding_fixture(tmp_path: Path):
    """Fires AT LEAST one finding in every category. Asserts the
    ValidationResult sequence is byte-identical between serial and
    parallel runs. This is the strict invariant — finding order, file
    traversal order all must match.
    """
    rules = tmp_path / "rules"
    rules.mkdir()
    _seed_multi_finding_rules(rules)

    r_serial = _run_scan(rules, parallel=False, plugin_root=tmp_path)
    r_parallel = _run_scan(rules, parallel=True, plugin_root=tmp_path)

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

    # Smoke-check the fixture actually exercises each finding category.
    levels = {r.level for r in r_serial.results}
    assert "MAJOR" in levels, "fixture must produce at least one MAJOR"
    assert "MINOR" in levels, "fixture must produce at least one MINOR"
    assert "PASSED" in levels, "fixture must produce at least one PASSED"


# ---------------------------------------------------------------------------
# 5. Parity — higher cardinality (order preservation under load)
# ---------------------------------------------------------------------------


def test_parity_many_files_order_preservation(tmp_path: Path):
    """50 rule files with deterministic content. The parallel harness
    promises input-order preservation; this test catches a regression
    to "results-by-completion-order" which would scramble the sequence
    on a process pool with N workers.
    """
    rules = tmp_path / "rules"
    rules.mkdir()
    # Mix: half clean, half with a unique unknown frontmatter key so each
    # file produces a recognisable MINOR finding with a per-file marker.
    # If the harness loses input order, the MINOR messages will appear in
    # the wrong order relative to the file names embedded in `r.file`.
    for i in range(50):
        if i % 2 == 0:
            (rules / f"r{i:03d}.md").write_text(
                f"# Clean rule {i:03d}\n\nBody {i}.\n", encoding="utf-8"
            )
        else:
            (rules / f"r{i:03d}.md").write_text(
                f"---\nbogus_key_{i:03d}: yes\n---\n\n# Rule {i:03d}\n",
                encoding="utf-8",
            )

    r_serial = _run_scan(rules, parallel=False, plugin_root=tmp_path)
    r_parallel = _run_scan(rules, parallel=True, plugin_root=tmp_path)

    s_seq = [_result_sig(r) for r in r_serial.results]
    p_seq = [_result_sig(r) for r in r_parallel.results]
    assert s_seq == p_seq, (
        f"High-cardinality parity broken.\n"
        f"Serial len:   {len(s_seq)}\nParallel len: {len(p_seq)}"
    )
    assert r_serial.exit_code == r_parallel.exit_code
    # And the count matches the fixture: 50 files → 50 PASSED, plus 25
    # MINORs from the odd-index files (unknown frontmatter), plus the
    # token-budget summary line, plus the "Found N rule file(s)" info.
    # Don't over-pin the exact count — just the parity.


# ---------------------------------------------------------------------------
# 6. Worker exception surfaces as per-file WARNING
# ---------------------------------------------------------------------------


def test_worker_exception_becomes_warning(tmp_path: Path, monkeypatch):
    """Patch ``parallel_scan`` in the validate_rules namespace so it
    returns a synthetic error ScanResult for every unit. The replay code
    path must catch the error and add one per-file WARNING — NEVER raise
    out of ``validate_rules_directory``.

    Mirrors A8's test_worker_exception_becomes_warning approach: we
    patch the in-process binding of ``parallel_scan``, not the worker
    body itself, because the harness pickles the function object at
    submit time and a parent-process mock has no effect on real workers.
    """
    rules = _make_rules_dir(tmp_path, n_files=3)

    from cpv_parallel_runner import ScanResult

    def fake_parallel_scan(units, fn, **kwargs):  # noqa: ARG001
        return [
            ScanResult(
                file_path=u,
                findings=("", []),  # tuple in scan_func shape, but error overrides
                error="RuntimeError: synthetic rules-worker crash",
            )
            for u in units
        ]

    monkeypatch.setattr(validate_rules, "parallel_scan", fake_parallel_scan)
    monkeypatch.setenv("CPV_RULES_PARALLEL", "1")

    report = validate_rules_directory(rules, plugin_root=tmp_path)

    warnings = [r for r in report.results if r.level == "WARNING"]
    # 3 files → 3 worker-error WARNINGS. (The token-budget summary at the
    # end can produce one more WARNING in some scenarios; we don't depend
    # on its presence here — just check at least 3.)
    worker_err_warnings = [
        w for w in warnings if "synthetic rules-worker crash" in w.message
    ]
    assert len(worker_err_warnings) == 3, (
        f"Expected 3 worker-error WARNINGs, got: "
        f"{[(w.level, w.message) for w in warnings]}"
    )
    for w in worker_err_warnings:
        assert "RuntimeError" in w.message

    # No CRITICAL synthesized — worker errors are non-blocking.
    assert not any(r.level == "CRITICAL" for r in report.results), (
        f"Worker error must NOT produce CRITICAL — got: "
        f"{[(r.level, r.message) for r in report.results if r.level == 'CRITICAL']}"
    )


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
def test_rules_parallel_enabled_env_var_parsing(monkeypatch, value, expected):
    """``_rules_parallel_enabled`` must recognise the documented disable
    values (0/false/no/off, case-insensitive) AND default to parallel
    for any other value. Consistency with A8's CPV_CACHE_PARALLEL and
    A9's CPV_XREF_PARALLEL parsers.
    """
    monkeypatch.setenv("CPV_RULES_PARALLEL", value)
    assert _rules_parallel_enabled() is expected


def test_rules_parallel_enabled_unset_defaults_to_parallel(monkeypatch):
    """When CPV_RULES_PARALLEL is unset entirely (most common case),
    the function returns True so the parallel path is the default.
    """
    monkeypatch.delenv("CPV_RULES_PARALLEL", raising=False)
    assert _rules_parallel_enabled() is True


# ---------------------------------------------------------------------------
# 8. Empty / clean rules dir still produces the PASSED token-budget line
# ---------------------------------------------------------------------------


def test_parity_clean_rules_emits_token_budget_passed(tmp_path: Path):
    """A rules dir with no findings still gets the
    "Total rules content: ... within budget" PASSED line — in BOTH
    serial and parallel modes.
    """
    rules = _make_rules_dir(tmp_path, n_files=2)
    r_serial = _run_scan(rules, parallel=False, plugin_root=tmp_path)
    r_parallel = _run_scan(rules, parallel=True, plugin_root=tmp_path)

    s_seq = [_result_sig(r) for r in r_serial.results]
    p_seq = [_result_sig(r) for r in r_parallel.results]
    assert s_seq == p_seq, (
        f"Clean-rules parity broken.\nSerial: {s_seq}\nParallel: {p_seq}"
    )
    # The token-budget PASSED line is unique to the directory-level scan.
    token_passed = [
        r for r in r_serial.results
        if r.level == "PASSED" and "Total rules content" in r.message
    ]
    assert token_passed, f"missing token-budget PASSED line: {r_serial.results}"
    assert r_serial.exit_code == 0
    assert r_parallel.exit_code == 0


def test_empty_rules_dir_short_circuits_both_paths(tmp_path: Path):
    """A rules/ dir with NO .md files at all should short-circuit
    BEFORE the parallel/serial decision — both paths emit only the
    "No rule files (*.md) found in rules/" info line.
    """
    rules = tmp_path / "rules"
    rules.mkdir()
    r_serial = _run_scan(rules, parallel=False, plugin_root=tmp_path)
    r_parallel = _run_scan(rules, parallel=True, plugin_root=tmp_path)

    s_seq = [_result_sig(r) for r in r_serial.results]
    p_seq = [_result_sig(r) for r in r_parallel.results]
    assert s_seq == p_seq
    assert any("No rule files" in r.message for r in r_serial.results)


# ---------------------------------------------------------------------------
# 9. Public API unchanged
# ---------------------------------------------------------------------------


def test_public_validate_rule_file_signature_unchanged():
    """``validate_rule_file(rule_path, report, rel_path)`` is the
    documented public API for the per-file scanner. The refactor
    preserves that signature. Catches a future refactor that silently
    changes the arg order.
    """
    sig = inspect.signature(validate_rule_file)
    params = list(sig.parameters)
    assert params == ["rule_path", "report", "rel_path"], (
        f"validate_rule_file signature changed: {params}"
    )


def test_public_validate_rules_directory_signature_unchanged():
    """``validate_rules_directory(rules_dir, report=None, plugin_root=None)``
    is the documented public API for the directory-level scan. The
    refactor preserves that signature.
    """
    sig = inspect.signature(validate_rules_directory)
    params = list(sig.parameters)
    assert params == ["rules_dir", "report", "plugin_root"], (
        f"validate_rules_directory signature changed: {params}"
    )


def test_validate_rule_file_returns_content_string(tmp_path: Path):
    """The legacy ``validate_rule_file`` returns the file's content
    string (for token counting). Public API consumers may rely on that
    — verify the wrapper still honors the str return contract.
    """
    rule_file = tmp_path / "r.md"
    rule_file.write_text("# Hello\n\nBody text.\n", encoding="utf-8")
    from cpv_validation_common import ValidationReport

    report = ValidationReport()
    content = validate_rule_file(rule_file, report, "rules/r.md")
    assert isinstance(content, str)
    assert "Hello" in content
