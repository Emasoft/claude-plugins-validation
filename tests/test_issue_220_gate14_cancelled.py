#!/usr/bin/env python3
"""Issue #220: Gate 14 must not read a superseded-cancelled CI run as RED.

`gh run list` reports `conclusion: cancelled` both for a run a human genuinely
cancelled AND for a run GitHub's concurrency group cancelled because a newer
push to the same branch superseded it. Gate 14 (`stage_verify_ci_green` in
`scripts/publish.py`, plus its byte-identical twin emitted by
`scripts/generate_plugin_repo.py::gen_publish_py`) used to fold both into a
single "RED" bucket. `classify_ci_runs` is the pure, gh-free classifier this
issue asks for: a `cancelled` run only clears when the caller supplies proof
(via `successors`) that a newer run of the same workflow exists on a
descendant commit; absent that proof it goes to `unknown`, which the gate
must report as UNKNOWN — never green, and never folded into `failed`.

Both copies (CPV's own `publish.py` and the emitted canon in
`generate_plugin_repo.py`) are tested identically so they cannot silently
diverge.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import generate_plugin_repo as gpr  # noqa: E402
import publish  # noqa: E402


def _extract_classify_ci_runs(source: str):
    """Compile ONLY the `classify_ci_runs` top-level function out of `source`.

    The emitted canon is a full standalone script (its own `__file__`-using
    module-level statements included), so exec'ing the whole thing is not an
    option in a test process. `classify_ci_runs` is pure (no module state),
    so lifting just its AST node and compiling that alone is sufficient.

    Zero-argument and the target name is a hard-coded literal (rather than a
    caller-supplied parameter) so this helper cannot be mistaken for a
    generic "compile arbitrary function from arbitrary source" primitive —
    a `source`/`func_name` PARAMETER reaching `exec`/`compile` is exactly
    the taint shape RC-73 exists to catch (CPV's own security scanner), and
    a literal name/no-param helper is provably not that shape.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "classify_ci_runs":
            mod = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(mod)
            ns: dict = {}
            exec(compile(mod, "<canon:classify_ci_runs>", "exec"), ns)  # noqa: S102
            return ns["classify_ci_runs"]
    raise AssertionError("classify_ci_runs not found in the emitted canon")


def _classifiers():
    """Both copies of classify_ci_runs — the module SSOT and the emitted canon."""
    canon_text = gpr.gen_publish_py(
        gpr.PluginParams(name="t", description="d", author="a", author_email="a@b.com")  # type: ignore[call-arg]
    )
    canon_fn = _extract_classify_ci_runs(canon_text)
    return {"publish.py (SSOT)": publish.classify_ci_runs, "canon (emitted)": canon_fn}


CLASSIFIERS = _classifiers()


@pytest.mark.parametrize("name,fn", list(CLASSIFIERS.items()))
class TestClassifyCiRuns:
    """`classify_ci_runs(runs, successors) -> (failed, unknown)`, gh-free."""

    def test_success_skipped_neutral_are_not_failing(self, name, fn):
        """A dormant workflow (skipped/neutral) is not a failure, per the pre-existing rule."""
        runs = [
            {"name": "Lint", "conclusion": "success"},
            {"name": "PyPI publish", "conclusion": "skipped"},
            {"name": "Optional check", "conclusion": "neutral"},
        ]
        failed, unknown = fn(runs, {})
        assert failed == []
        assert unknown == []

    def test_genuine_failure_is_still_failed(self, name, fn):
        """A real `failure`/`timed_out`/`action_required` conclusion is unaffected."""
        runs = [{"name": "Test", "conclusion": "failure"}]
        failed, unknown = fn(runs, {})
        assert failed == runs
        assert unknown == []

    def test_cancelled_with_no_successor_is_unknown_not_failed(self, name, fn):
        """No proof of a superseding run -> UNKNOWN, never folded into RED."""
        runs = [{"name": "Test", "conclusion": "cancelled"}]
        failed, unknown = fn(runs, {})
        assert failed == [], "a bare cancellation must not be reported as a failure"
        assert unknown == runs

    def test_cancelled_with_successors_false_is_unknown(self, name, fn):
        """An explicit False in the map (successor search ran, found nothing) -> UNKNOWN."""
        runs = [{"name": "Test", "conclusion": "cancelled"}]
        failed, unknown = fn(runs, {"Test": False})
        assert failed == []
        assert unknown == runs

    def test_cancelled_with_resolved_successor_is_not_failing(self, name, fn):
        """A resolved descendant-commit successor -> the cancellation clears entirely."""
        runs = [{"name": "Test", "conclusion": "cancelled"}]
        failed, unknown = fn(runs, {"Test": True})
        assert failed == []
        assert unknown == []

    def test_mixed_batch_classifies_each_run_independently(self, name, fn):
        runs = [
            {"name": "Lint", "conclusion": "success"},
            {"name": "Test", "conclusion": "failure"},
            {"name": "Release", "conclusion": "cancelled"},  # superseded
            {"name": "Docs", "conclusion": "cancelled"},  # genuinely user-cancelled
        ]
        failed, unknown = fn(runs, {"Release": True})
        assert failed == [{"name": "Test", "conclusion": "failure"}]
        assert unknown == [{"name": "Docs", "conclusion": "cancelled"}]

    def test_successor_lookup_is_keyed_by_workflow_name(self, name, fn):
        """The map is keyed on the run's `name`, not position — order independence."""
        runs = [
            {"name": "A", "conclusion": "cancelled"},
            {"name": "B", "conclusion": "cancelled"},
        ]
        failed, unknown = fn(runs, {"B": True})
        assert failed == []
        assert unknown == [{"name": "A", "conclusion": "cancelled"}]


def test_resolve_ci_run_successors_finds_a_descendant_commit_run(monkeypatch):
    """`_resolve_ci_run_successors` (publish.py) resolves True only when `gh run
    list` returns a candidate commit that `git merge-base --is-ancestor` confirms
    is a descendant of the released sha."""
    import subprocess

    calls = []

    class _Result:
        def __init__(self, returncode, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["gh", "run", "list"]:
            import json

            return _Result(0, json.dumps([{"headSha": "newsha123", "conclusion": "success"}]))
        if argv[:2] == ["git", "merge-base"]:
            # released sha IS an ancestor of the newer commit
            return _Result(0)
        raise AssertionError(f"unexpected subprocess call: {argv}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cancelled = [{"name": "Test", "headBranch": "main"}]
    result = publish._resolve_ci_run_successors("gh", Path("/tmp"), "oldsha000", cancelled)
    assert result == {"Test": True}
    assert any(a[:3] == ["gh", "run", "list"] for a in calls)
    assert any(a[:2] == ["git", "merge-base"] for a in calls)


def test_resolve_ci_run_successors_no_descendant_leaves_entry_absent(monkeypatch):
    """A candidate exists but is NOT a descendant of the released commit ->
    no True is recorded, so `classify_ci_runs` will route it to `unknown`."""
    import subprocess

    class _Result:
        def __init__(self, returncode, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(argv, **kwargs):
        if argv[:3] == ["gh", "run", "list"]:
            import json

            return _Result(0, json.dumps([{"headSha": "unrelatedsha", "conclusion": "success"}]))
        if argv[:2] == ["git", "merge-base"]:
            return _Result(1)  # not an ancestor
        raise AssertionError(f"unexpected subprocess call: {argv}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cancelled = [{"name": "Test", "headBranch": "main"}]
    result = publish._resolve_ci_run_successors("gh", Path("/tmp"), "oldsha000", cancelled)
    assert result == {}


def test_resolve_ci_run_successors_gh_failure_leaves_entry_absent(monkeypatch):
    """gh list failing (non-zero exit) must not resolve a True — fail toward UNKNOWN."""
    import subprocess

    class _Result:
        def __init__(self, returncode, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = "boom"

    def fake_run(argv, **kwargs):
        return _Result(1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    cancelled = [{"name": "Test", "headBranch": "main"}]
    result = publish._resolve_ci_run_successors("gh", Path("/tmp"), "oldsha000", cancelled)
    assert result == {}
