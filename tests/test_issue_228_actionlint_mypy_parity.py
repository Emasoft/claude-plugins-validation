"""w9-followups #3b (#228 follow-up) — the GENERATED publish.py carries its
actionlint/mypy "CI still enforces it" claim rendered from the SAME constants
as ``cpv_ci_preflight`` (``ACTIONLINT_WORKFLOW_PATTERN`` /
``MYPY_WORKFLOW_RUN_PATTERN``), exactly the way ``MEGALINTER_WORKFLOW_PATTERN``
is rendered (issue #228 — see ``test_issue_228_megalinter_wired.py``), so the
local gate and the preflight can never drift apart.

Pinned here:

* the emitted regex literals equal the preflight's own module constants;
* the emitted ``_actionlint_workflow_wired`` / ``_mypy_workflow_wired``
  helpers agree with the preflight's own functions across every shape;
* LOAD-BEARING FN guard: CPV's own generated ci.yml is detected as wired for
  BOTH tools, so a freshly scaffolded plugin keeps today's "WILL enforce"
  wording rather than regressing to the new "NOT run" wording.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_ci_preflight as pf  # noqa: E402
import generate_plugin_repo as gen  # noqa: E402

_PARAMS = gen.PluginParams(
    name="issue-228b-sample", description="x", author="A", author_email="a@a.a",
    github_owner="Emasoft",
)

ACTIONLINT_WIRED_FORMS = {
    "action": "      - uses: rhysd/actionlint@914e7df21a07ef503a81201c76d2b11c789d3fca # v1.7.12\n",
    "docker": "      - run: docker run --rm docker://rhysd/actionlint:latest\n",
    "run-bare": "      - run: actionlint\n",
}
ACTIONLINT_NOT_WIRED_FORMS = {
    "comment-only": "      # actionlint runs elsewhere: rhysd/actionlint\n",
    "unrelated-action": "      - uses: actions/checkout@v4\n",
}

# mypy is wired via a direct workflow run: (the Mega-Linter+PYTHON_MYPY path
# needs a SECOND file, exercised separately below).
MYPY_WIRED_FORMS = {
    "run-bare": "      - run: uv run mypy scripts/ --ignore-missing-imports\n",
}
MYPY_NOT_WIRED_FORMS = {
    "comment-only": "      # we also run mypy separately\n",
    "unrelated-action": "      - uses: actions/checkout@v4\n",
}


def _workflow(root: Path, body: str, name: str = "ci.yml") -> None:
    wf = root / ".github" / "workflows" / name
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text(
        "name: CI\non: [push]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n" + body,
        encoding="utf-8",
    )


def _mega_linter(root: Path, linters: list[str]) -> None:
    body = "APPLY_FIXES: none\nENABLE_LINTERS:\n" + "".join(f"  - {x}\n" for x in linters)
    (root / ".mega-linter.yml").write_text(body, encoding="utf-8")


def _mega_linter_raw(root: Path, body: str) -> None:
    (root / ".mega-linter.yml").write_text(body, encoding="utf-8")


# ── the preflight functions directly ────────────────────────────────────────


@pytest.mark.parametrize("form", sorted(ACTIONLINT_WIRED_FORMS))
def test_actionlint_wired_forms(tmp_path: Path, form: str) -> None:
    _workflow(tmp_path, ACTIONLINT_WIRED_FORMS[form])
    assert pf._actionlint_workflow_wired(tmp_path) is True


@pytest.mark.parametrize("form", sorted(ACTIONLINT_NOT_WIRED_FORMS))
def test_actionlint_not_wired_forms(tmp_path: Path, form: str) -> None:
    _workflow(tmp_path, ACTIONLINT_NOT_WIRED_FORMS[form])
    assert pf._actionlint_workflow_wired(tmp_path) is False


@pytest.mark.parametrize("form", sorted(MYPY_WIRED_FORMS))
def test_mypy_wired_forms(tmp_path: Path, form: str) -> None:
    _workflow(tmp_path, MYPY_WIRED_FORMS[form])
    assert pf._mypy_workflow_wired(tmp_path) is True


@pytest.mark.parametrize("form", sorted(MYPY_NOT_WIRED_FORMS))
def test_mypy_not_wired_forms(tmp_path: Path, form: str) -> None:
    _workflow(tmp_path, MYPY_NOT_WIRED_FORMS[form])
    assert pf._mypy_workflow_wired(tmp_path) is False


def test_mypy_wired_via_megalinter_python_mypy(tmp_path: Path) -> None:
    _workflow(tmp_path, "      - uses: oxsecurity/megalinter@e08c2b05e3dbc40af4c23f41172ef1e068a7d651 # v8\n")
    _mega_linter(tmp_path, ["PYTHON_MYPY"])
    assert pf._mypy_workflow_wired(tmp_path) is True


def test_mypy_not_wired_via_megalinter_without_python_mypy(tmp_path: Path) -> None:
    _workflow(tmp_path, "      - uses: oxsecurity/megalinter@e08c2b05e3dbc40af4c23f41172ef1e068a7d651 # v8\n")
    _mega_linter(tmp_path, ["PYTHON_RUFF"])
    assert pf._mypy_workflow_wired(tmp_path) is False


# ── Mega-Linter's own default-runs-everything semantics (no ENABLE key) ──────
# Mega-Linter's documented default with NO ENABLE_LINTERS/ENABLE key at all is
# to run every linter it supports, PYTHON_MYPY included — so "PYTHON_MYPY not
# explicitly listed" must NOT read as "not wired" when there is no list to
# begin with. DISABLE_LINTERS/DISABLE still wins over that default.

_MEGALINTER_STEP = (
    "      - uses: oxsecurity/megalinter@e08c2b05e3dbc40af4c23f41172ef1e068a7d651 # v8\n"
)


def test_mypy_wired_via_megalinter_no_enable_key_at_all(tmp_path: Path) -> None:
    _workflow(tmp_path, _MEGALINTER_STEP)
    _mega_linter_raw(tmp_path, "APPLY_FIXES: none\n")
    assert pf._mypy_workflow_wired(tmp_path) is True


def test_mypy_wired_when_no_mega_linter_yml_but_workflow_invokes(tmp_path: Path) -> None:
    # No .mega-linter.yml file at all: Mega-Linter is wired but its config is
    # absent, which is the same "no explicit ENABLE list" default-run case.
    _workflow(tmp_path, _MEGALINTER_STEP)
    assert pf._mypy_workflow_wired(tmp_path) is True


def test_mypy_not_wired_via_megalinter_disable_linters_python_mypy(tmp_path: Path) -> None:
    _workflow(tmp_path, _MEGALINTER_STEP)
    _mega_linter_raw(
        tmp_path,
        "APPLY_FIXES: none\nDISABLE_LINTERS:\n  - PYTHON_MYPY\n",
    )
    assert pf._mypy_workflow_wired(tmp_path) is False


def test_mypy_not_wired_via_megalinter_disable_whole_python_language(tmp_path: Path) -> None:
    _workflow(tmp_path, _MEGALINTER_STEP)
    _mega_linter_raw(
        tmp_path,
        "APPLY_FIXES: none\nDISABLE_LINTERS:\n  - PYTHON\n",
    )
    assert pf._mypy_workflow_wired(tmp_path) is False


def test_mypy_still_wired_with_explicit_enable_and_no_matching_disable(tmp_path: Path) -> None:
    _workflow(tmp_path, _MEGALINTER_STEP)
    _mega_linter_raw(
        tmp_path,
        "APPLY_FIXES: none\nENABLE_LINTERS:\n  - PYTHON_MYPY\nDISABLE_LINTERS:\n  - JAVASCRIPT_ES\n",
    )
    assert pf._mypy_workflow_wired(tmp_path) is True


NO_ENABLE_KEY_FORMS = {
    "no-enable-key-at-all": "APPLY_FIXES: none\n",
    "disable-python-mypy": "APPLY_FIXES: none\nDISABLE_LINTERS:\n  - PYTHON_MYPY\n",
    "disable-python-language": "APPLY_FIXES: none\nDISABLE_LINTERS:\n  - PYTHON\n",
    "enable-and-non-matching-disable": (
        "APPLY_FIXES: none\nENABLE_LINTERS:\n  - PYTHON_MYPY\n"
        "DISABLE_LINTERS:\n  - JAVASCRIPT_ES\n"
    ),
}


@pytest.mark.parametrize("form", sorted(NO_ENABLE_KEY_FORMS))
def test_emitted_mypy_helper_agrees_with_preflight_for_megalinter_forms(
    tmp_path: Path, form: str
) -> None:
    _workflow(tmp_path, _MEGALINTER_STEP)
    _mega_linter_raw(tmp_path, NO_ENABLE_KEY_FORMS[form])
    emitted = _emitted_helper("_mypy_workflow_wired")
    assert emitted(tmp_path) is pf._mypy_workflow_wired(tmp_path)  # type: ignore[operator]


# ── the GENERATED publish.py ─────────────────────────────────────────────────


def _emitted_tree(profile: str) -> ast.Module:
    return ast.parse(gen.gen_publish_py(_PARAMS, profile=profile))


@pytest.mark.parametrize("profile", sorted(gen.KNOWN_PROFILES))
def test_emitted_actionlint_pattern_equals_the_preflight_constant(profile: str) -> None:
    literals = [
        ast.literal_eval(node.value.args[0])
        for node in ast.walk(_emitted_tree(profile))
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "_ACTIONLINT_WORKFLOW_RE" for t in node.targets)
    ]
    assert literals == [pf.ACTIONLINT_WORKFLOW_PATTERN]


@pytest.mark.parametrize("profile", sorted(gen.KNOWN_PROFILES))
def test_emitted_mypy_pattern_equals_the_preflight_constant(profile: str) -> None:
    literals = [
        ast.literal_eval(node.value.args[0])
        for node in ast.walk(_emitted_tree(profile))
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "_MYPY_WORKFLOW_RUN_RE" for t in node.targets)
    ]
    assert literals == [pf.MYPY_WORKFLOW_RUN_PATTERN]


def _emitted_helper(name: str) -> object:
    """Execute ONLY the emitted regex + helper(s) it needs, not the whole publish.py."""
    tree = _emitted_tree(gen.PROFILE_STANDARD)
    wanted_assigns = {"_ACTIONLINT_WORKFLOW_RE", "_MYPY_WORKFLOW_RUN_RE", "_MEGALINTER_WORKFLOW_RE"}
    wanted_funcs = {"_actionlint_workflow_wired", "_mypy_workflow_wired", "_megalinter_workflow_wired"}
    keep = [
        n for n in tree.body
        if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in wanted_assigns for t in n.targets))
        or (isinstance(n, ast.FunctionDef) and n.name in wanted_funcs)
    ]
    assert len(keep) == 6
    ns: dict[str, object] = {"re": __import__("re"), "Path": Path}
    exec(  # noqa: S102 - our own generated code, six statements
        compile(ast.Module(body=keep, type_ignores=[]), "publish.py", "exec"), ns
    )
    return ns[name]


@pytest.mark.parametrize("form", sorted(ACTIONLINT_WIRED_FORMS) + sorted(ACTIONLINT_NOT_WIRED_FORMS))
def test_emitted_actionlint_helper_agrees_with_preflight(tmp_path: Path, form: str) -> None:
    _workflow(tmp_path, {**ACTIONLINT_WIRED_FORMS, **ACTIONLINT_NOT_WIRED_FORMS}[form])
    emitted = _emitted_helper("_actionlint_workflow_wired")
    assert emitted(tmp_path) is pf._actionlint_workflow_wired(tmp_path)  # type: ignore[operator]


@pytest.mark.parametrize("form", sorted(MYPY_WIRED_FORMS) + sorted(MYPY_NOT_WIRED_FORMS))
def test_emitted_mypy_helper_agrees_with_preflight(tmp_path: Path, form: str) -> None:
    _workflow(tmp_path, {**MYPY_WIRED_FORMS, **MYPY_NOT_WIRED_FORMS}[form])
    emitted = _emitted_helper("_mypy_workflow_wired")
    assert emitted(tmp_path) is pf._mypy_workflow_wired(tmp_path)  # type: ignore[operator]


# ── load-bearing FN guard: CPV's own generated ci.yml is detected as wired ──


def _scaffold(tmp_path: Path, name: str = "issue-228b-scaffold") -> Path:
    params = gen.PluginParams(
        name=name, description="x", author="A", author_email="a@a.a", github_owner="Emasoft",
    )
    target = tmp_path / name
    target.mkdir()
    gen.generate_plugin_repo(target, params)
    return target


def test_cpv_generated_ci_yml_is_actionlint_wired(tmp_path: Path) -> None:
    target = _scaffold(tmp_path)
    assert pf._actionlint_workflow_wired(target) is True


def test_cpv_generated_ci_yml_is_mypy_wired(tmp_path: Path) -> None:
    target = _scaffold(tmp_path)
    assert pf._mypy_workflow_wired(target) is True


def test_no_leftover_placeholders_in_emitted_publish_py(tmp_path: Path) -> None:
    target = _scaffold(tmp_path)
    text = (target / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert "__CPV_ACTIONLINT_WORKFLOW_PATTERN__" not in text
    assert "__CPV_MYPY_WORKFLOW_RUN_PATTERN__" not in text
    compile(text, "publish.py", "exec")
