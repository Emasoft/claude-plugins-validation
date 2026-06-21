"""Tests for the CI-parity preflight subsystem (TRDD-8eee537a, Phase 1).

The #137-143 family root cause: fixer/upgrade agents declare DONE on
``validate_plugin --strict``, which does NOT run the jscpd / actionlint / mypy /
``uv sync --extra dev`` gates the adopting plugin's GitHub-CI ``ci.yml`` Lint job
runs, so a locally-clean upgrade still red-CIs. Phase 1 builds the LOCAL
preflight that mirrors those gates.

Three pieces are tested here:

* ``cpv_ci_parity_checks.check_ci_parity`` — the FIVE static defect detectors
  (CIP-1..CIP-5). Every check is two-sided: a DEFECTIVE-fixture tree FIRES it,
  and a CLEAN / canon-fixture tree PASSES it (and a plugin that simply does not
  use the relevant workflow never draws a false finding).
* ``cpv_ci_preflight.run_ci_preflight`` — the orchestrator. The degrade-not-block
  contract: a tool being ABSENT (mocked) ALWAYS yields a WARNING and NEVER a
  non-zero exit; only a REAL CI gate failure (a CIP MAJOR/MINOR static defect,
  or a tool that ran and found a real problem) exits non-zero.
* The ``ci-preflight`` subcommand in ``remote_validation.py`` — it is wired (the
  alias maps to the module) and exits per the preflight result.

The full CPV repo itself is exercised as the canonical CLEAN tree: CPV ships
``ci.yml`` (no Mega-Linter, no ``validate.yml``, no inverted env, ``uv sync``
without ``--extra dev``, no ``[no-redef]``-without-``misc`` shim) so EVERY CIP
check must PASS on it — the real-world no-false-fire guard.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_ci_preflight  # noqa: E402
from cpv_ci_parity_checks import ParityFinding, check_ci_parity  # noqa: E402
from cpv_ci_preflight import PreflightResult, run_ci_preflight  # noqa: E402

CPV_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_plugin(tmp_path: Path) -> Path:
    """Lay down a minimal valid plugin tree with a plugin.json + scripts/."""
    root = tmp_path / "plug"
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True)
    (cp / "plugin.json").write_text(
        json.dumps(
            {"name": "plug", "version": "0.1.0", "description": "t", "author": "X"}, indent=2
        ),
        encoding="utf-8",
    )
    (root / "scripts").mkdir()
    return root


def _write_workflow(root: Path, name: str, body: str) -> Path:
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    path = wf / name
    path.write_text(body, encoding="utf-8")
    return path


def _ids(findings: list[ParityFinding]) -> set[str]:
    return {f.check_id for f in findings}


# A clean canon ci.yml (matches CPV's own shape: bare `uv sync`, no Mega-Linter,
# no inverted env). Used as the PASS sibling for every static check.
_CLEAN_CI_YML = """\
name: CI
on:
  push:
    branches: [master]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv sync
      - run: uv run ruff check scripts/
"""


# ===========================================================================
# CIP-1 — inverted CLAUDE_PRIVATE_USERNAMES (#140)
# ===========================================================================


def test_cip1_fires_on_inverted_private_usernames(tmp_path: Path) -> None:
    """A workflow setting CLAUDE_PRIVATE_USERNAMES to the repo owner FIRES CIP-1."""
    root = _make_plugin(tmp_path)
    _write_workflow(
        root,
        "ci.yml",
        "name: CI\njobs:\n  v:\n    steps:\n      - run: cpv-remote-validate plugin .\n"
        "        env:\n          CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}\n",
    )
    findings = check_ci_parity(root)
    assert "CIP-1" in _ids(findings)
    cip1 = [f for f in findings if f.check_id == "CIP-1"][0]
    assert cip1.severity == "MAJOR"
    assert cip1.file == ".github/workflows/ci.yml"


def test_cip1_passes_on_skip_only_env(tmp_path: Path) -> None:
    """A workflow with only PLUGIN_SKIP_GITHUB_INTEGRITY (no inverted env) PASSES."""
    root = _make_plugin(tmp_path)
    _write_workflow(
        root,
        "ci.yml",
        "name: CI\njobs:\n  v:\n    steps:\n      - run: cpv-remote-validate plugin .\n"
        "        env:\n          PLUGIN_SKIP_GITHUB_INTEGRITY: '1'\n",
    )
    assert "CIP-1" not in _ids(check_ci_parity(root))


def test_cip1_passes_on_local_whoami_idiom(tmp_path: Path) -> None:
    """The LOCAL `CLAUDE_PRIVATE_USERNAMES="$(whoami)"` scan idiom is NOT a defect."""
    root = _make_plugin(tmp_path)
    _write_workflow(
        root,
        "ci.yml",
        'name: CI\njobs:\n  v:\n    steps:\n      - run: CLAUDE_PRIVATE_USERNAMES="$(whoami)" '
        "cpv-remote-validate plugin .\n",
    )
    assert "CIP-1" not in _ids(check_ci_parity(root))


# ===========================================================================
# CIP-2 — import-fallback shim missing `misc` (#142 Defect-1)
# ===========================================================================

_SHIM_BAD = """\
try:
    from cpv_network_resilience import gh_with_retry
except ImportError:
    def gh_with_retry(cmd, **kwargs):  # type: ignore[no-redef]
        return None
"""

_SHIM_GOOD = """\
try:
    from cpv_network_resilience import gh_with_retry
except ImportError:
    def gh_with_retry(cmd, **kwargs):  # type: ignore[no-redef, misc]
        return None
"""


def test_cip2_fires_on_no_redef_without_misc(tmp_path: Path) -> None:
    """A fallback shim with `[no-redef]` but no `misc` FIRES CIP-2."""
    root = _make_plugin(tmp_path)
    (root / "scripts" / "publish.py").write_text(_SHIM_BAD, encoding="utf-8")
    findings = check_ci_parity(root)
    assert "CIP-2" in _ids(findings)
    cip2 = [f for f in findings if f.check_id == "CIP-2"][0]
    assert cip2.severity == "MINOR"
    assert cip2.file == "scripts/publish.py"


def test_cip2_passes_on_no_redef_with_misc(tmp_path: Path) -> None:
    """A fallback shim with `[no-redef, misc]` PASSES CIP-2."""
    root = _make_plugin(tmp_path)
    (root / "scripts" / "publish.py").write_text(_SHIM_GOOD, encoding="utf-8")
    assert "CIP-2" not in _ids(check_ci_parity(root))


def test_cip2_passes_on_unconditional_no_redef(tmp_path: Path) -> None:
    """A `[no-redef]` NOT inside an `except ImportError` fallback is left alone."""
    root = _make_plugin(tmp_path)
    (root / "scripts" / "x.py").write_text(
        "def foo():  # type: ignore[no-redef]\n    return 1\n", encoding="utf-8"
    )
    assert "CIP-2" not in _ids(check_ci_parity(root))


def test_cip2_passes_when_no_scripts_dir(tmp_path: Path) -> None:
    """A plugin with no scripts/ dir cannot draw a CIP-2 finding."""
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "version": "0.1.0", "description": "t", "author": "X"}),
        encoding="utf-8",
    )
    assert "CIP-2" not in _ids(check_ci_parity(root))


# ===========================================================================
# CIP-3 — `uv sync --extra dev` without a declared dev extra (#142 Defect-2)
# ===========================================================================

_CI_WITH_DEV = (
    "name: CI\njobs:\n  lint:\n    steps:\n      - run: uv sync --extra dev\n"
    "      - run: uv run mypy scripts/\n"
)


def test_cip3_fires_when_dev_extra_requested_but_undeclared(tmp_path: Path) -> None:
    """A workflow running `uv sync --extra dev` with no dev extra FIRES CIP-3."""
    root = _make_plugin(tmp_path)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "plug"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    _write_workflow(root, "ci.yml", _CI_WITH_DEV)
    findings = check_ci_parity(root)
    assert "CIP-3" in _ids(findings)
    assert [f for f in findings if f.check_id == "CIP-3"][0].severity == "MAJOR"


def test_cip3_passes_when_dev_extra_declared(tmp_path: Path) -> None:
    """The same workflow PASSES when pyproject declares the dev extra."""
    root = _make_plugin(tmp_path)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "plug"\nversion = "0.1.0"\n\n'
        '[project.optional-dependencies]\ndev = ["pytest", "ruff", "mypy"]\n',
        encoding="utf-8",
    )
    _write_workflow(root, "ci.yml", _CI_WITH_DEV)
    assert "CIP-3" not in _ids(check_ci_parity(root))


def test_cip3_passes_on_bare_uv_sync(tmp_path: Path) -> None:
    """A workflow running a bare `uv sync` (no `--extra dev`) never FIRES CIP-3.

    This is CPV's own shape — the `--extra dev` token is the trigger.
    """
    root = _make_plugin(tmp_path)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "plug"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    _write_workflow(root, "ci.yml", _CLEAN_CI_YML)
    assert "CIP-3" not in _ids(check_ci_parity(root))


def test_cip3_passes_when_no_pyproject(tmp_path: Path) -> None:
    """No pyproject.toml → undeterminable → CIP-3 does not fire (degrade-not-block)."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _CI_WITH_DEV)
    assert "CIP-3" not in _ids(check_ci_parity(root))


# ===========================================================================
# CIP-4 — superseded validate.yml alongside ci.yml (#142 Defect-4)
# ===========================================================================

_CPV_VALIDATE_YML = (
    "name: Validate Plugin\njobs:\n  validate:\n    steps:\n"
    "      - run: cpv-remote-validate plugin . --strict\n"
)


def test_cip4_fires_on_cpv_validate_yml_with_ci_yml(tmp_path: Path) -> None:
    """A CPV-shipped validate.yml alongside ci.yml FIRES CIP-4."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _CLEAN_CI_YML)
    _write_workflow(root, "validate.yml", _CPV_VALIDATE_YML)
    findings = check_ci_parity(root)
    assert "CIP-4" in _ids(findings)
    cip4 = [f for f in findings if f.check_id == "CIP-4"][0]
    assert cip4.severity == "MAJOR"
    assert cip4.file == ".github/workflows/validate.yml"


def test_cip4_passes_validate_yml_alone(tmp_path: Path) -> None:
    """A validate.yml with NO ci.yml is the plugin's only gate — never flagged."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "validate.yml", _CPV_VALIDATE_YML)
    assert "CIP-4" not in _ids(check_ci_parity(root))


def test_cip4_passes_on_non_cpv_validate_yml(tmp_path: Path) -> None:
    """A user's own validate.yml (no CPV markers) is NEVER flagged even with ci.yml."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _CLEAN_CI_YML)
    _write_workflow(
        root,
        "validate.yml",
        "name: Validate Schemas\njobs:\n  v:\n    steps:\n      - run: ajv validate -s s.json\n",
    )
    assert "CIP-4" not in _ids(check_ci_parity(root))


# ===========================================================================
# CIP-5 — COPYPASTE_JSCPD enabled but no .jscpd.json (#143)
# ===========================================================================

_CI_WITH_JSCPD = (
    "name: CI\njobs:\n  lint:\n    steps:\n      - uses: oxsecurity/megalinter@v7\n"
    "        env:\n          ENABLE_LINTERS: COPYPASTE_JSCPD\n"
    '          COPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"\n'
)


def test_cip5_fires_when_jscpd_enabled_without_config(tmp_path: Path) -> None:
    """ci.yml enabling COPYPASTE_JSCPD with no .jscpd.json FIRES CIP-5."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _CI_WITH_JSCPD)
    findings = check_ci_parity(root)
    assert "CIP-5" in _ids(findings)
    assert [f for f in findings if f.check_id == "CIP-5"][0].severity == "MINOR"


def test_cip5_passes_when_jscpd_config_present(tmp_path: Path) -> None:
    """The same workflow PASSES when a .jscpd.json exists."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _CI_WITH_JSCPD)
    (root / ".jscpd.json").write_text('{"threshold": 5}', encoding="utf-8")
    assert "CIP-5" not in _ids(check_ci_parity(root))


def test_cip5_passes_when_jscpd_not_enabled(tmp_path: Path) -> None:
    """A ci.yml with no Mega-Linter/COPYPASTE_JSCPD never FIRES CIP-5 even with no config.

    This is CPV's own shape — its CI runs no Mega-Linter.
    """
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _CLEAN_CI_YML)
    assert "CIP-5" not in _ids(check_ci_parity(root))


# ===========================================================================
# Combined / canon-clean
# ===========================================================================


def test_clean_canon_tree_has_zero_findings(tmp_path: Path) -> None:
    """A fully clean canon tree (good shim, declared dev extra, ci.yml only,
    jscpd config present) draws NO CIP findings."""
    root = _make_plugin(tmp_path)
    (root / "scripts" / "publish.py").write_text(_SHIM_GOOD, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "plug"\nversion = "0.1.0"\n\n'
        '[project.optional-dependencies]\ndev = ["pytest", "ruff", "mypy"]\n',
        encoding="utf-8",
    )
    _write_workflow(root, "ci.yml", _CI_WITH_DEV)
    (root / ".jscpd.json").write_text('{"threshold": 5}', encoding="utf-8")
    assert check_ci_parity(root) == []


def test_check_ci_parity_on_cpv_repo_is_clean() -> None:
    """CPV's OWN tree must pass all five static checks (the real no-false-fire guard)."""
    assert check_ci_parity(CPV_ROOT) == []


def test_no_workflows_dir_is_clean(tmp_path: Path) -> None:
    """A plugin with no .github/workflows/ draws no workflow-based findings."""
    root = _make_plugin(tmp_path)
    assert check_ci_parity(root) == []


# ===========================================================================
# Preflight orchestration — degrade-to-WARNING on tool absence
# ===========================================================================


def _force_tools_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every external tool resolve as ABSENT (shutil.which → None)."""
    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", lambda _name: None)


def test_preflight_degrades_to_warning_when_all_tools_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every tool absent → only WARNINGs (+PASS no-ops), exit 0. NEVER a FAIL."""
    root = _make_plugin(tmp_path)
    # A workflow that asks for the dev extra + jscpd, so the gates have something
    # to (try to) run — but with all tools mocked absent they must WARN, not FAIL.
    (root / "pyproject.toml").write_text(
        '[project]\nname = "plug"\nversion = "0.1.0"\n\n'
        '[project.optional-dependencies]\ndev = ["pytest"]\n',
        encoding="utf-8",
    )
    # A real .py in scripts/ so the mypy gate has something to check — an empty
    # scripts/ would short-circuit mypy to PASS before the tool-absence check.
    (root / "scripts" / "x.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    _write_workflow(root, "ci.yml", _CI_WITH_DEV)
    _force_tools_absent(monkeypatch)

    result = run_ci_preflight(root)
    assert result.exit_code == 0
    assert not result.fails  # tool-absence is NEVER a FAIL
    gates_with_warning = {f.gate for f in result.warnings}
    # jscpd, actionlint, mypy, uv-sync-dev must each WARN when their tool is gone.
    assert {"jscpd", "actionlint", "mypy", "uv-sync-dev"} <= gates_with_warning


def test_preflight_jscpd_absent_is_warning_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When jscpd/npx is absent, the jscpd gate is a WARNING, never a FAIL."""
    root = _make_plugin(tmp_path)
    _force_tools_absent(monkeypatch)
    result = run_ci_preflight(root)
    jscpd = [f for f in result.findings if f.gate == "jscpd"]
    assert len(jscpd) == 1
    assert jscpd[0].severity == "WARNING"
    assert result.exit_code == 0


def test_preflight_fails_on_static_cip_defect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real static CIP defect FAILs the preflight (non-zero exit) even with tools absent.

    Tool-absence WARNINGs don't block, but a CIP MAJOR (inverted env) is a real
    CI gate failure — exit must be non-zero.
    """
    root = _make_plugin(tmp_path)
    _write_workflow(
        root,
        "ci.yml",
        "name: CI\njobs:\n  v:\n    steps:\n      - run: cpv-remote-validate plugin .\n"
        "        env:\n          CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}\n",
    )
    _force_tools_absent(monkeypatch)
    result = run_ci_preflight(root)
    assert result.exit_code != 0
    fail_gates = {f.gate for f in result.fails}
    assert "CIP-1" in fail_gates


def test_preflight_clean_tree_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean tree with all tools absent → exit 0 (only WARNINGs/PASSes)."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _CLEAN_CI_YML)
    _force_tools_absent(monkeypatch)
    result = run_ci_preflight(root)
    assert result.exit_code == 0
    assert not result.fails


def test_preflight_empty_scripts_dir_is_not_a_mypy_fail(tmp_path: Path) -> None:
    """An empty scripts/ (no .py files) must NOT be a mypy FAIL.

    mypy exits non-zero with "no .py files in directory" for an empty dir; the
    preflight must treat that as PASS (nothing to type-check), not a CI failure.
    This runs mypy for real (no mock) so the short-circuit is exercised even
    when mypy IS installed on the box.
    """
    root = _make_plugin(tmp_path)  # creates an EMPTY scripts/ dir
    _write_workflow(root, "ci.yml", _CLEAN_CI_YML)
    result = run_ci_preflight(root)
    mypy = [f for f in result.findings if f.gate == "mypy"]
    assert len(mypy) == 1
    assert mypy[0].severity == "PASS"
    # The empty-scripts mypy no-op must never push the verdict to non-zero.
    assert not any(f.severity == "FAIL" and f.gate == "mypy" for f in result.findings)


def test_preflight_result_strict_threaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The strict flag is threaded into the result (interface symmetry)."""
    root = _make_plugin(tmp_path)
    _force_tools_absent(monkeypatch)
    result = run_ci_preflight(root, strict=True)
    assert result.strict is True
    assert isinstance(result, PreflightResult)


def test_preflight_static_cip_warning_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CIP finding whose severity is WARNING maps to a non-blocking WARNING.

    (Guards the severity-mapping: only MAJOR/MINOR CIP findings become FAIL.)
    """
    root = _make_plugin(tmp_path)
    _force_tools_absent(monkeypatch)
    # Monkeypatch check_ci_parity to return a single WARNING-severity finding.
    monkeypatch.setattr(
        cpv_ci_preflight,
        "check_ci_parity",
        lambda _p: [ParityFinding("CIP-9", "WARNING", "advisory", "x.yml")],
    )
    result = run_ci_preflight(root)
    assert result.exit_code == 0
    cip9 = [f for f in result.findings if f.gate == "CIP-9"]
    assert len(cip9) == 1
    assert cip9[0].severity == "WARNING"


# ===========================================================================
# Subcommand wiring (remote_validation.py ci-preflight)
# ===========================================================================


def test_remote_validation_aliases_ci_preflight() -> None:
    """The `ci-preflight` alias maps to the cpv_ci_preflight module."""
    import remote_validation

    assert remote_validation._ALIASES["ci-preflight"] == "cpv_ci_preflight"
    assert remote_validation._ALIASES["cpv_ci_preflight"] == "cpv_ci_preflight"
    assert "ci-preflight" in remote_validation._COMMANDS


def test_remote_validation_existing_aliases_unchanged() -> None:
    """The pre-existing subcommands stay wired byte-identically."""
    import remote_validation

    for name, module in (
        ("plugin", "validate_plugin"),
        ("security", "validate_security"),
        ("skill", "validate_skill_comprehensive"),
        ("marketplace", "validate_marketplace"),
    ):
        assert remote_validation._ALIASES[name] == module


def test_ci_preflight_main_exit_zero_on_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`cpv_ci_preflight.main()` exits 0 on a clean tree (tools mocked absent)."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _CLEAN_CI_YML)
    _force_tools_absent(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["ci-preflight", str(root)])
    rc = cpv_ci_preflight.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "PARITY-CLEAN" in out


def test_ci_preflight_main_nonzero_on_defect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main()` exits non-zero when a static CIP defect is present."""
    root = _make_plugin(tmp_path)
    _write_workflow(
        root,
        "ci.yml",
        "name: CI\njobs:\n  v:\n    steps:\n      - run: cpv-remote-validate plugin .\n"
        "        env:\n          CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}\n",
    )
    _force_tools_absent(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["ci-preflight", str(root)])
    rc = cpv_ci_preflight.main()
    out = capsys.readouterr().out
    assert rc != 0
    assert "CI WOULD FAIL" in out


def test_ci_preflight_main_missing_target(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main()` errors out (SystemExit) when no target path is given."""
    monkeypatch.setattr(sys, "argv", ["ci-preflight"])
    with pytest.raises(SystemExit):
        cpv_ci_preflight.main()


def test_ci_preflight_main_nonexistent_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main()` returns 1 for a path that does not exist."""
    missing = tmp_path / "nope"
    monkeypatch.setattr(sys, "argv", ["ci-preflight", str(missing)])
    rc = cpv_ci_preflight.main()
    err = capsys.readouterr().err
    assert rc == 1
    assert "does not exist" in err


def test_ci_preflight_main_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main --report FILE` writes the report to disk."""
    root = _make_plugin(tmp_path)
    _write_workflow(root, "ci.yml", _CLEAN_CI_YML)
    _force_tools_absent(monkeypatch)
    report = tmp_path / "preflight.txt"
    monkeypatch.setattr(sys, "argv", ["ci-preflight", str(root), "--report", str(report)])
    rc = cpv_ci_preflight.main()
    assert rc == 0
    assert report.is_file()
    assert "CI-PARITY PREFLIGHT" in report.read_text(encoding="utf-8")
