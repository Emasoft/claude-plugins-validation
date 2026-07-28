"""The emitted publish.py test gate must be satisfiable by a real suite (issue #179).

The canonical `run()` helper hardcoded `timeout=300` and BOTH pytest call sites
inherited it. 300s is sized for a lint/scan invocation, not a test suite — so on
any plugin whose suite runs longer than five minutes, gate G4 could never pass.
A cap the suite cannot finish inside does not make the gate stricter, it makes
it *unsatisfiable*: the run asserts nothing about the code while still printing
red, and a timeout is indistinguishable from a hang. The reporter's suite (13,618
tests) reached 47% at the cap.

Two-sided throughout: every "the suite bound is wide" assertion is paired with
one proving the NARROW default still applies to every other call site. Widening
`run()`'s default for everyone would have been the easy fix and the wrong one —
a lint step that hangs should still fail fast at 300s.

The env-override tests execute the EMITTED function, not a copy of it, so they
fail if the generated semantics drift from the intent.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402


def _params() -> PluginParams:
    return PluginParams(
        name="my-test-plugin",
        description="A test plugin",
        author="Test Author",
        author_email="test@example.com",
        license="MIT",
        python_version="3.12",
        github_owner="test-owner",
        marketplace="test-marketplace",
        version="0.1.0",
    )


def _template() -> str:
    return gen_publish_py(_params())


def _exec_emitted_timeout_helper(monkeypatch_env: dict[str, str] | None = None) -> float:
    """Execute the EMITTED `_test_suite_timeout` — not a reimplementation of it.

    Compiles only the constants + the function out of the generated source, so
    no module-level import side effect of publish.py is triggered. A test that
    re-implemented the resolver would pass while the shipped code was broken.
    """
    import os

    tree = ast.parse(_template())
    wanted = {"_TEST_SUITE_TIMEOUT_ENV", "_DEFAULT_TEST_SUITE_TIMEOUT"}
    keep: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in wanted for t in node.targets
        ):
            keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_test_suite_timeout":
            keep.append(node)
    assert len(keep) == 3, f"emitted template is missing the suite-timeout helper: found {len(keep)}/3"

    ns: dict[str, Any] = {"os": os}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "<emitted-publish.py>", "exec"), ns)  # noqa: S102

    env = dict(os.environ)
    try:
        os.environ.pop(ns["_TEST_SUITE_TIMEOUT_ENV"], None)
        if monkeypatch_env:
            os.environ.update(monkeypatch_env)
        return float(ns["_test_suite_timeout"]())
    finally:
        os.environ.clear()
        os.environ.update(env)


# --------------------------------------------------------------------------
# run(): the generic bound stays narrow, but is now overridable
# --------------------------------------------------------------------------


def test_run_helper_accepts_a_timeout_argument() -> None:
    """Without a parameter, every caller is stuck with whatever run() hardcodes."""
    text = _template()
    sig = re.search(r"\ndef run\(\n(.*?)\n\) -> subprocess\.CompletedProcess", text, re.DOTALL)
    assert sig is not None, "run() signature not found in the emitted template"
    assert "timeout" in sig.group(1), "run() still offers no way to override its bound"


def test_run_helper_default_bound_is_still_300() -> None:
    """LOAD-BEARING (two-sided): widening the DEFAULT would slow every hung
    lint/scan step from 5 minutes to 30. Only the test suite needed a wider
    bound; every other call site must keep failing fast."""
    text = _template()
    sig = re.search(r"\ndef run\(\n(.*?)\n\) -> subprocess\.CompletedProcess", text, re.DOTALL)
    assert sig is not None
    assert re.search(r"timeout:\s*float\s*=\s*300\b", sig.group(1)), (
        "run()'s default bound is no longer 300s — unrelated call sites lost their fail-fast"
    )


def test_run_helper_passes_its_timeout_through_to_subprocess() -> None:
    """A parameter that is accepted and then ignored is worse than none."""
    text = _template()
    body = text.split("\ndef run(", 1)[1].split("\ndef get_repo_root", 1)[0]
    assert "timeout=timeout" in body, "run() accepts a timeout but does not pass it to subprocess.run"


def test_timeout_message_reports_the_actual_value() -> None:
    """A hardcoded '300s' starts lying the moment any caller overrides it —
    and a wrong number in a timeout message sends triage down the wrong path."""
    text = _template()
    body = text.split("\ndef run(", 1)[1].split("\ndef get_repo_root", 1)[0]
    assert "Command timed out after 300s" not in body, "the expiry message still hardcodes 300s"
    assert re.search(r"Command timed out after \{timeout[^}]*\}s", body), (
        "the expiry message does not interpolate the actual bound"
    )


# --------------------------------------------------------------------------
# The suite bound itself
# --------------------------------------------------------------------------


def test_suite_timeout_default_admits_a_real_suite() -> None:
    """The reporter's 13.6k-test suite needs ~850s; 300s could never finish it."""
    assert _exec_emitted_timeout_helper() >= 1800.0


def test_suite_timeout_env_override_is_honoured() -> None:
    """The next larger suite must not have to patch the template to be provable."""
    assert _exec_emitted_timeout_helper({"PLUGIN_TEST_SUITE_TIMEOUT": "2400"}) == 2400.0


def test_suite_timeout_ignores_a_non_positive_override() -> None:
    """LOAD-BEARING: a `0` that won meant every suite times out instantly — the
    exact unsatisfiable-gate defect this fixes, re-created by a typo."""
    for bad in ("0", "-1", ""):
        assert _exec_emitted_timeout_helper({"PLUGIN_TEST_SUITE_TIMEOUT": bad}) >= 1800.0, (
            f"a {bad!r} override shortened the bound"
        )


def test_suite_timeout_ignores_an_unparseable_override() -> None:
    """Degrade to the default, never to zero."""
    assert _exec_emitted_timeout_helper({"PLUGIN_TEST_SUITE_TIMEOUT": "soon"}) >= 1800.0


# --------------------------------------------------------------------------
# Both pytest call sites — a fix at one site only is not a fix
# --------------------------------------------------------------------------


def test_gate_pytest_site_uses_the_suite_bound() -> None:
    """G4's inline guard — the site the reporter actually hit."""
    text = _template()
    gate = text.split("[G4] Running tests", 1)[1].split("All gates passed", 1)[0]
    assert "_test_suite_timeout()" in gate, "the G4 pytest guard still uses run()'s narrow default"
    assert "timeout=300" not in gate, "the G4 pytest guard still hardcodes 300s"
    assert "timed out after 300s" not in gate, "the G4 timeout message still hardcodes 300s"


def test_stage_tests_site_uses_the_suite_bound() -> None:
    """The publish path's own pytest run — same defect, different site."""
    text = _template()
    stage = text.split("\ndef stage_tests(", 1)[1].split("\ndef stage_validate(", 1)[0]
    assert "pytest" in stage, "stage_tests no longer runs pytest — test is stale"
    assert "_test_suite_timeout()" in stage, "stage_tests still inherits run()'s narrow default"


def test_emitted_template_still_parses() -> None:
    """Cheap guard: a template edit that breaks syntax would otherwise only be
    caught downstream, in somebody else's repo."""
    ast.parse(_template())
