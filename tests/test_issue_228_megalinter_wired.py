"""Issue #228 — "CI's Mega-Linter WILL enforce it" only when a workflow runs Mega-Linter.

`.mega-linter.yml` enabling a linter is not proof CI runs it: a repo can keep the
config after dropping the workflow step (the reporter's case — no workflow ran
Mega-Linter at all), and the skip line then names a backstop that does not
exist, at exactly the moment the operator decides whether the skip matters.

What is pinned here:

* the detector recognises every Mega-Linter invocation shape and ignores
  comments, report-upload steps and unrelated `uses:` lines;
* LOAD-BEARING FN guard: CPV's own generated ci.yml is detected as wired, so a
  freshly scaffolded plugin keeps the "WILL enforce" wording;
* two-sided wording in the preflight (jscpd + every Mega-Linter probe);
* the generated publish.py cannot import CPV, so it carries the regex as a
  literal — rendered from the SAME constant (parity test) and guarding every
  Mega-Linter-backed "WILL enforce" line behind the wired check.
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
    name="issue-228-sample", description="x", author="A", author_email="a@a.a",
    github_owner="Emasoft",
)

# One workflow body per invocation shape the detector must recognise.
WIRED_FORMS = {
    "oxsecurity-action": "      - uses: oxsecurity/megalinter@e08c2b05e3dbc40af4c23f41172ef1e068a7d651 # v8\n",
    "flavor-action": "      - uses: oxsecurity/megalinter/flavors/python@v8\n",
    "legacy-action": "      - uses: megalinter/megalinter@v5\n",
    "legacy-nvuillam": "      - uses: nvuillam/mega-linter@v4\n",
    "docker-uses": "      - uses: docker://ghcr.io/oxsecurity/megalinter-python:v8\n",
    "docker-run": "      - run: docker run --rm -v $PWD:/tmp/lint ghcr.io/oxsecurity/megalinter:v8\n",
    "container-image": "    container:\n      image: oxsecurity/megalinter:v8\n",
    "runner-cli": "      - run: npx mega-linter-runner --flavor python\n",
    "reusable-workflow": "    uses: my-org/ci/.github/workflows/megalinter.yml@main\n",
    "quoted-uses": '      - uses: "oxsecurity/megalinter@v8"\n',
}

# Workflows that mention Mega-Linter WITHOUT running it.
NOT_WIRED_FORMS = {
    "comment-only": "      # Mega-Linter used to run here: oxsecurity/megalinter@v8\n",
    "trailing-comment": "      - run: echo hi # npx mega-linter-runner\n",
    "upload-step": (
        "      - name: Upload Mega-Linter reports\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n          path: megalinter-reports/\n"
    ),
    "unrelated-action": "      - uses: rhysd/actionlint@v1.7.12\n",
}


def _workflow(root: Path, body: str, name: str = "ci.yml") -> None:
    wf = root / ".github" / "workflows" / name
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text("name: CI\non: [push]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n" + body,
                  encoding="utf-8")


def _enable(root: Path, *ids: str) -> None:
    (root / ".mega-linter.yml").write_text(
        "ENABLE_LINTERS:\n" + "".join(f"  - {i}\n" for i in ids), encoding="utf-8"
    )


# ── the detector ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("form", sorted(WIRED_FORMS))
def test_every_invocation_shape_is_wired(tmp_path: Path, form: str) -> None:
    _workflow(tmp_path, WIRED_FORMS[form])
    assert pf._megalinter_workflow_wired(tmp_path) is True


@pytest.mark.parametrize("form", sorted(NOT_WIRED_FORMS))
def test_mentions_without_invocation_are_not_wired(tmp_path: Path, form: str) -> None:
    _workflow(tmp_path, NOT_WIRED_FORMS[form])
    assert pf._megalinter_workflow_wired(tmp_path) is False


def test_no_workflows_dir_is_not_wired(tmp_path: Path) -> None:
    assert pf._megalinter_workflow_wired(tmp_path) is False


def test_yaml_extension_is_scanned(tmp_path: Path) -> None:
    _workflow(tmp_path, WIRED_FORMS["oxsecurity-action"], name="lint.yaml")
    assert pf._megalinter_workflow_wired(tmp_path) is True


def test_cpv_generated_ci_yml_is_wired(tmp_path: Path) -> None:
    """LOAD-BEARING FN guard: a scaffolded plugin's own ci.yml runs Mega-Linter."""
    wf = tmp_path / ".github" / "workflows" / "ci.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(gen.gen_ci_yml(_PARAMS), encoding="utf-8")
    assert pf._megalinter_workflow_wired(tmp_path) is True


def test_fully_scaffolded_plugin_is_wired(tmp_path: Path) -> None:
    target = tmp_path / "issue-228-sample"
    target.mkdir()
    gen.generate_plugin_repo(target, _PARAMS)
    assert pf._megalinter_workflow_wired(target) is True


def test_pattern_is_re2_safe() -> None:
    re2 = pytest.importorskip("re2")
    assert re2.compile(pf.MEGALINTER_WORKFLOW_PATTERN).search(WIRED_FORMS["flavor-action"])


# ── preflight wording, two-sided ────────────────────────────────────────────


def _probe_absent_tool(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    _enable(root, "REPOSITORY_CHECKOV")
    monkeypatch.setattr(pf.shutil, "which", lambda _name: None)
    result = pf.PreflightResult(plugin_path=root)
    pf._gate_megalinter_tool(
        result, gate="checkov", linter_id="REPOSITORY_CHECKOV", tool_name="checkov",
        install_hint="pip install checkov", build_argv=lambda b, _r: [b],
        enabled=pf._megalinter_enabled_linters(root),
    )
    (finding,) = result.findings
    assert finding.severity == "WARNING"  # same severity either way
    return finding.message


def test_config_without_workflow_says_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _probe_absent_tool(tmp_path, monkeypatch)
    assert "WILL enforce" not in msg
    assert "NOT run" in msg and "Mega-Linter workflow" in msg


@pytest.mark.parametrize("form", sorted(WIRED_FORMS))
def test_config_with_wired_workflow_keeps_will_enforce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    _workflow(tmp_path, WIRED_FORMS[form])
    msg = _probe_absent_tool(tmp_path, monkeypatch)
    assert "CI's Mega-Linter WILL enforce it" in msg
    assert "NOT run" not in msg


def test_probe_run_failure_wording_is_conditional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 'could not run' branch made the same unconditional claim."""
    _enable(tmp_path, "REPOSITORY_CHECKOV")
    monkeypatch.setattr(pf.shutil, "which", lambda name: f"/bin/{name}")

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("no exec")

    monkeypatch.setattr(pf.subprocess, "run", _boom)

    def _run() -> str:
        result = pf.PreflightResult(plugin_path=tmp_path)
        pf._gate_megalinter_tool(
            result, gate="checkov", linter_id="REPOSITORY_CHECKOV", tool_name="checkov",
            install_hint="x", build_argv=lambda b, _r: [b],
            enabled=pf._megalinter_enabled_linters(tmp_path),
        )
        return result.findings[0].message

    assert "still enforces" not in _run()
    _workflow(tmp_path, WIRED_FORMS["oxsecurity-action"])
    assert "CI's Mega-Linter still enforces REPOSITORY_CHECKOV" in _run()


def _jscpd_message(root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(pf, "_resolve_jscpd_cmd", lambda: None)
    result = pf.PreflightResult(plugin_path=root)
    pf._gate_jscpd(result)
    (finding,) = result.findings
    assert finding.severity == "WARNING"
    return finding.message


def test_jscpd_without_workflow_says_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _jscpd_message(tmp_path, monkeypatch)
    assert "WILL enforce" not in msg and "NOT run" in msg


def test_jscpd_with_workflow_keeps_will_enforce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workflow(tmp_path, WIRED_FORMS["oxsecurity-action"])
    msg = _jscpd_message(tmp_path, monkeypatch)
    assert "CI's Mega-Linter WILL enforce it (.jscpd.json threshold)" in msg


# ── the generated publish.py ────────────────────────────────────────────────


def _emitted_tree(profile: str) -> ast.Module:
    return ast.parse(gen.gen_publish_py(_PARAMS, profile=profile))


@pytest.mark.parametrize("profile", sorted(gen.KNOWN_PROFILES))
def test_emitted_pattern_equals_the_preflight_constant(profile: str) -> None:
    """Single source: the literal in publish.py IS MEGALINTER_WORKFLOW_PATTERN."""
    literals = [
        ast.literal_eval(node.value.args[0])
        for node in ast.walk(_emitted_tree(profile))
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "_MEGALINTER_WORKFLOW_RE" for t in node.targets)
    ]
    assert literals == [pf.MEGALINTER_WORKFLOW_PATTERN]


def _emitted_helper() -> object:
    """Execute ONLY the emitted regex + helper, not the whole publish.py."""
    tree = _emitted_tree(gen.PROFILE_STANDARD)
    keep = [
        n for n in tree.body
        if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_MEGALINTER_WORKFLOW_RE" for t in n.targets))
        or (isinstance(n, ast.FunctionDef) and n.name == "_megalinter_workflow_wired")
    ]
    assert len(keep) == 2
    ns: dict[str, object] = {"re": __import__("re"), "Path": Path}
    exec(  # noqa: S102 - our own generated code, two statements
        compile(ast.Module(body=keep, type_ignores=[]), "publish.py", "exec"), ns
    )
    return ns["_megalinter_workflow_wired"]


@pytest.mark.parametrize("form", sorted(WIRED_FORMS) + sorted(NOT_WIRED_FORMS))
def test_emitted_helper_agrees_with_preflight(tmp_path: Path, form: str) -> None:
    _workflow(tmp_path, {**WIRED_FORMS, **NOT_WIRED_FORMS}[form])
    emitted = _emitted_helper()
    assert emitted(tmp_path) is pf._megalinter_workflow_wired(tmp_path)  # type: ignore[operator]


_WIRED_GUARD_NAMES = {
    "_ml_wired",  # Mega-Linter-backed gates (jscpd, cspell/checkov/etc — #228)
    "_al_wired",  # actionlint (w9-followups #3b, #228 follow-up)
    "_mypy_wired",  # mypy (w9-followups #3b, #228 follow-up)
}


def _will_enforce_calls_outside_wired_guard(tree: ast.Module) -> list[str]:
    """Every "WILL enforce" cprint not under `if <one of _WIRED_GUARD_NAMES>:`."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "cprint"):
            continue
        text = ast.unparse(node)
        if "WILL enforce" not in text:
            continue
        cur: ast.AST | None = node
        guarded = False
        while cur is not None:
            parent = parents.get(cur)
            if (
                isinstance(parent, ast.If)
                and ast.unparse(parent.test) in _WIRED_GUARD_NAMES
                and cur in parent.body
            ):
                guarded = True
                break
            cur = parent
        if not guarded:
            bad.append(text)
    return bad


def test_generated_will_enforce_lines_are_guarded() -> None:
    tree = _emitted_tree(gen.PROFILE_STANDARD)
    will = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and "WILL enforce" in ast.unparse(n)]
    assert len(will) >= 5, "anchor drift: the WILL-enforce lines were not found"
    assert _will_enforce_calls_outside_wired_guard(tree) == []
