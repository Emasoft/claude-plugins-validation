#!/usr/bin/env python3
"""Issue #200 — every linter must run FROM the tree it is validating.

REPORTED SYMPTOM: running the validator against a repo from a working
directory OUTSIDE that repo (a `/tmp` scratch dir — the normal case for an
agent or a CI wrapper) mass-reported false ``unresolved import`` MINORs,
capped at 20, while the identical invocation launched FROM the repo root
reported 0. Same tree, same pin, only the launch cwd differed.

ROOT CAUSE: `cpv_lint_engine` invoked pyright with no ``cwd``, so the child
inherited CPV's launch directory. With no ``--project``, pyright's project
root IS its cwd — so ``pyrightconfig.json``, and the ``extraPaths`` /
``venvPath`` it declares, were looked for in the scratch dir, never found, and
every first-party import came back unresolved.

THE SIBLING AUDIT found the same class in mypy — which discovers
``mypy.ini`` / ``.mypy.ini`` / ``pyproject.toml`` / ``setup.cfg`` in the
CURRENT DIRECTORY — and there it ran in the FALSE-NEGATIVE direction: from a
foreign cwd the target's own config was never applied, so REAL findings went
missing. Measured on the fixture this file builds: 0 findings from a foreign
cwd, 1 from the repo root. That asymmetry is why the fix is not cosmetic —
one tool invented findings, the other lost them, for the same reason.

TWO-SIDED / NON-VACUITY: the anchoring assertions below fail against the
pre-fix engine. The CONTROLS must pass in BOTH states, and are the half that
matters:

  * ``test_ast_scan_is_not_vacuous`` — a source scan that silently matched
    nothing would let every other AST assertion pass over an empty list.
  * ``test_markdownlint_still_runs_from_an_isolated_temp_cwd`` — markdownlint
    is the ONE deliberate exception (issue #84: `bunx`/`npx` walk UP from the
    cwd to resolve their package, so a broken ancestor Node project breaks it).
    A "fix" that anchored every linter uniformly, markdownlint included, would
    regress #84 while satisfying every other assertion here.
"""

from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# tests/conftest.py adds scripts/ to sys.path; defensive duplicate so this file
# works when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_lint_engine  # noqa: E402
from cpv_lint_engine import (  # noqa: E402
    lint_dockerfile,
    lint_markdown,
    lint_powershell,
    lint_python,
    lint_shell,
    lint_xml,
)
from cpv_validation_common import ValidationReport  # noqa: E402

ENGINE_SRC = Path(cpv_lint_engine.__file__)

# The engine had 17 `_run_linter` call sites when this test was written. The
# guard below only needs a floor high enough that an AST scan matching nothing
# (or nearly nothing) cannot pass silently.
_MIN_EXPECTED_CALL_SITES = 15


class FakeResult:
    """subprocess.CompletedProcess stand-in (same shape as the sibling suite)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CwdRecorder:
    """`_run_linter` stand-in that records the cwd each call was given."""

    def __init__(self, *results: FakeResult) -> None:
        self.calls: list[dict[str, object]] = []
        self._queue = list(results)

    def __call__(self, cmd, **kwargs):  # noqa: ANN001, ANN204
        self.calls.append({"cmd": list(cmd), "cwd": kwargs.get("cwd")})
        return self._queue.pop(0) if self._queue else FakeResult()


def _run_linter_call_sites() -> list[ast.Call]:
    """Every `_run_linter(...)` call in the engine's source."""
    tree = ast.parse(ENGINE_SRC.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_run_linter"
    ]


# ---------------------------------------------------------------------------
# Source-level invariant — a linter added later cannot inherit #200 by omission
# ---------------------------------------------------------------------------


class TestRunLinterCwdInvariant:
    def test_ast_scan_is_not_vacuous(self) -> None:
        """CONTROL (passes pre- and post-fix): the scan really finds the calls.

        Without this, a regex/AST shape that stopped matching would make
        `test_every_run_linter_call_passes_cwd` pass over an empty list — a
        green test asserting nothing, which is how this class of guard rots.
        """
        assert len(_run_linter_call_sites()) >= _MIN_EXPECTED_CALL_SITES

    def test_every_run_linter_call_passes_cwd(self) -> None:
        """Every linter spawn names the directory it runs in — no inheritance.

        A source check rather than a behavioural one because the failure mode
        is OMISSION: a new linter added without `cwd=` produces no error, no
        warning and no wrong answer on the maintainer's box (where cwd usually
        IS the repo) — it only misbehaves for the agent/CI caller that launches
        CPV from somewhere else.
        """
        missing = [call.lineno for call in _run_linter_call_sites() if "cwd" not in {kw.arg for kw in call.keywords}]
        assert missing == [], f"_run_linter call(s) with no cwd= at line(s) {missing} — see issue #200"


# ---------------------------------------------------------------------------
# Behavioural — the cwd handed to each linter is the tree under validation
# ---------------------------------------------------------------------------


class TestPythonToolchainAnchorsToTarget:
    """pyright / mypy / ruff run from `repo_root`, never from CPV's launch dir."""

    @staticmethod
    def _foreign_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        foreign = tmp_path / "foreign-launch-dir"
        foreign.mkdir()
        monkeypatch.chdir(foreign)

    def test_pyright_runs_from_the_validated_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        # Presence of this file is what routes lint_python to the pyright branch.
        (repo / "pyrightconfig.json").write_text('{"extraPaths": ["libs"]}\n', encoding="utf-8")
        src = repo / "scripts" / "main.py"
        src.write_text("x = 1\n", encoding="utf-8")
        self._foreign_cwd(tmp_path, monkeypatch)

        rec = CwdRecorder(FakeResult(0), FakeResult(0, '{"generalDiagnostics": []}'))
        with (
            patch("cpv_lint_engine._resolve", side_effect=lambda name: [name]),
            patch("cpv_lint_engine._run_linter", side_effect=rec),
        ):
            lint_python(repo, [src], ValidationReport())

        pyright_calls = [c for c in rec.calls if c["cmd"][0] == "pyright"]  # type: ignore[index]
        assert len(pyright_calls) == 1
        assert Path(str(pyright_calls[0]["cwd"])) == repo

    def test_mypy_runs_from_the_validated_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "mypy.ini").write_text("[mypy]\ndisallow_untyped_defs = True\n", encoding="utf-8")
        src = repo / "scripts" / "main.py"
        src.write_text("x = 1\n", encoding="utf-8")
        self._foreign_cwd(tmp_path, monkeypatch)

        rec = CwdRecorder(FakeResult(0), FakeResult(0))
        with (
            patch("cpv_lint_engine._resolve", side_effect=lambda name: [name]),
            patch("cpv_lint_engine._run_linter", side_effect=rec),
        ):
            lint_python(repo, [src], ValidationReport())

        mypy_calls = [c for c in rec.calls if c["cmd"][0] == "mypy"]  # type: ignore[index]
        assert len(mypy_calls) == 1
        assert Path(str(mypy_calls[0]["cwd"])) == repo

    def test_ruff_runs_from_the_validated_tree(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        src = repo / "scripts" / "main.py"
        src.write_text("x = 1\n", encoding="utf-8")
        self._foreign_cwd(tmp_path, monkeypatch)

        rec = CwdRecorder(FakeResult(0), FakeResult(0))
        with (
            patch("cpv_lint_engine._resolve", side_effect=lambda name: [name]),
            patch("cpv_lint_engine._run_linter", side_effect=rec),
        ):
            lint_python(repo, [src], ValidationReport())

        ruff_calls = [c for c in rec.calls if c["cmd"][0] == "ruff"]  # type: ignore[index]
        assert len(ruff_calls) == 1
        assert Path(str(ruff_calls[0]["cwd"])) == repo


class TestSiblingLintersAnchorToTarget:
    """The non-Python linters that were also spawning with an inherited cwd."""

    @pytest.mark.parametrize(
        ("lint_fn", "filename", "body"),
        [
            (lint_shell, "deploy.sh", "#!/bin/bash\nsource ./lib.sh\n"),
            (lint_dockerfile, "Dockerfile", "FROM alpine\n"),
            (lint_xml, "doc.xml", "<a/>\n"),
            (lint_powershell, "run.ps1", "Write-Host hi\n"),
        ],
        ids=["shellcheck", "hadolint", "xmllint", "psscriptanalyzer"],
    )
    def test_linter_runs_from_the_validated_tree(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        lint_fn,  # noqa: ANN001
        filename: str,
        body: str,
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        target = repo / filename
        target.write_text(body, encoding="utf-8")
        foreign = tmp_path / "foreign-launch-dir"
        foreign.mkdir()
        monkeypatch.chdir(foreign)

        rec = CwdRecorder(FakeResult(0))
        with (
            patch("cpv_lint_engine._resolve", side_effect=lambda name: [name]),
            patch("cpv_lint_engine._run_linter", side_effect=rec),
        ):
            lint_fn(repo, [target], ValidationReport())

        assert len(rec.calls) == 1
        assert Path(str(rec.calls[0]["cwd"])) == repo


class TestMarkdownlintExceptionPreserved:
    """CONTROL (passes pre- and post-fix) — issue #84 must survive issue #200."""

    def test_markdownlint_still_runs_from_an_isolated_temp_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        doc = repo / "README.md"
        doc.write_text("# Title\n", encoding="utf-8")
        foreign = tmp_path / "foreign-launch-dir"
        foreign.mkdir()
        monkeypatch.chdir(foreign)

        rec = CwdRecorder(FakeResult(0))
        with (
            patch("cpv_lint_engine._resolve", return_value=["markdownlint-cli2"]),
            patch("cpv_lint_engine._run_linter", side_effect=rec),
        ):
            lint_markdown(repo, [doc], ValidationReport())

        assert len(rec.calls) == 1
        cwd = Path(str(rec.calls[0]["cwd"]))
        # Deliberately NOT repo_root: `bunx`/`npx` resolve their package by
        # walking UP from the cwd, so an ancestor `package.json` with a broken
        # `node_modules` crashes markdownlint-cli2's ESM imports (issue #84).
        assert cwd != repo
        assert "cpv-mdlint-" in cwd.name


# ---------------------------------------------------------------------------
# End-to-end — the reported symptom itself, driven from a foreign cwd
# ---------------------------------------------------------------------------


class TestForeignCwdEndToEnd:
    """The mocked tests prove the wiring; these prove the OUTCOME.

    Skipped where the real tool is absent — a skipped test is honest, whereas
    routing through `uvx`/`npx` here would make the suite fetch packages.
    """

    @pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright not installed locally")
    def test_pyright_unresolved_import_fp_is_gone_from_a_foreign_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The issue's exact shape: a first-party import resolved only by config."""
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "libs").mkdir()
        (repo / "pyrightconfig.json").write_text(
            '{\n  "include": ["scripts"],\n  "extraPaths": ["libs"]\n}\n', encoding="utf-8"
        )
        (repo / "libs" / "mylib.py").write_text('def greet(n: str) -> str:\n    return f"hi {n}"\n', encoding="utf-8")
        src = repo / "scripts" / "main.py"
        src.write_text('from mylib import greet\n\n\ndef main() -> str:\n    return greet("world")\n', encoding="utf-8")

        foreign = tmp_path / "foreign-launch-dir"
        foreign.mkdir()
        monkeypatch.chdir(foreign)

        report = ValidationReport()
        lint_python(repo, [src], report)

        unresolved = [r.message for r in report.results if "could not be resolved" in r.message]
        assert unresolved == [], f"issue #200 regression — pyright ignored {repo}/pyrightconfig.json: {unresolved}"

    @pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not installed locally")
    def test_mypy_applies_the_targets_config_from_a_foreign_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The FALSE-NEGATIVE half: without the fix this finding was LOST.

        `mypy.ini` here turns on `disallow_untyped_defs`. Pre-fix, mypy ran
        from the launch dir, read no config, and reported nothing — the target
        looked clean because its own configuration was never applied.
        """
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "mypy.ini").write_text("[mypy]\ndisallow_untyped_defs = True\n", encoding="utf-8")
        src = repo / "scripts" / "m.py"
        src.write_text("def untyped(a, b):\n    return a + b\n", encoding="utf-8")

        foreign = tmp_path / "foreign-launch-dir"
        foreign.mkdir()
        monkeypatch.chdir(foreign)

        report = ValidationReport()
        lint_python(repo, [src], report)

        mypy_findings = [r.message for r in report.results if r.message.startswith("Mypy:")]
        assert any("no-untyped-def" in m for m in mypy_findings), (
            f"issue #200 regression — the target's mypy.ini was not applied: {mypy_findings}"
        )
