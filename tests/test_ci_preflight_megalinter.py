"""Tests for the Mega-Linter sub-linter parity probes (F4, TRDD-HZSI0BZ6).

MODE 2 of the #137-143 recurrence: CI's Mega-Linter container enforces a set of
sub-linters (checkov / trivy / cspell / bandit / shellcheck / shfmt) declared in
``.mega-linter.yml``'s ``ENABLE_LINTERS``, but ``validate_plugin --strict`` and
the jscpd/actionlint/mypy/uv-sync gates NEVER reproduce them locally — so an
agent declares DONE on a clean ``ci-preflight``, publishes, and CI fails on a
gate the agent could not see. These probes add LOCAL visibility for those
linters, following the EXACT degrade-gracefully contract of the other gates.

The contract under test, per probe:

* The plugin's ``.mega-linter.yml`` does NOT enable the linter (or has no config
  at all) → clean PASS "linter not enabled, skipped" (never forces a tool on a
  plugin that did not opt in; never changes the default-enabled Mega-Linter set).
* linter enabled, tool on PATH, ran clean → PASS.
* linter enabled, tool on PATH, found a real error → FAIL (surfaces the first
  error line; the only path that contributes a non-zero exit).
* linter enabled, tool ABSENT on PATH → non-blocking WARNING (a dev/agent box
  without checkov must NOT be false-blocked — the #129 / #143 discipline).

The matrix (enabled+present+clean / enabled+present+error / enabled+absent /
not-enabled) is exercised fully for cspell AND checkov, plus the YAML-parser
units, the shell-script-list probes (shellcheck/shfmt), and the orchestrator
integration (no-config skip + never-block).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_ci_preflight  # noqa: E402
from cpv_ci_preflight import PreflightResult, run_ci_preflight  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture builders + subprocess mock
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


def _write_mega_linter(root: Path, linters: list[str]) -> None:
    """Write a `.mega-linter.yml` enabling the given linter ids (block-list form)."""
    lines = ["APPLY_FIXES: none", "ENABLE_LINTERS:"]
    lines += [f"  - {lid}" for lid in linters]
    (root / ".mega-linter.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")


class _FakeProc:
    """Stand-in for subprocess.CompletedProcess with the fields the gates read."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_tool(
    monkeypatch: pytest.MonkeyPatch,
    *,
    present: set[str],
    run_result: dict[str, _FakeProc] | None = None,
) -> None:
    """Mock which()/subprocess.run for the preflight module.

    `present` is the set of tool names that resolve on PATH (everything else →
    None). `run_result` maps a tool name to the _FakeProc its invocation returns
    (default: a clean exit-0 proc). A subprocess.run for a tool NOT in `present`
    must never happen (which() returned None), so a missing mapping defaults to
    a clean proc only for present tools.
    """
    run_result = run_result or {}

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    def fake_run(argv: list[str], **_kw: object) -> _FakeProc:
        # argv[0] is the resolved tool path `/usr/bin/<name>`; key on the name.
        name = Path(argv[0]).name
        return run_result.get(name, _FakeProc(0))

    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", fake_which)
    monkeypatch.setattr(cpv_ci_preflight.subprocess, "run", fake_run)


def _finding(result: PreflightResult, gate: str):  # type: ignore[no-untyped-def]
    matches = [f for f in result.findings if f.gate == gate]
    assert len(matches) == 1, f"expected exactly one {gate} finding, got {len(matches)}"
    return matches[0]


# ===========================================================================
# YAML enabled-linter parser
# ===========================================================================


def test_parse_block_sequence_form() -> None:
    """The block-sequence `ENABLE_LINTERS:` + `  - ID` form is parsed."""
    text = (
        "APPLY_FIXES: none\n"
        "ENABLE_LINTERS:\n"
        "  - PYTHON_BANDIT\n"
        "  - REPOSITORY_CHECKOV\n"
        "  - SPELL_CSPELL\n"
        "FILTER_REGEX_EXCLUDE: 'x'\n"
    )
    assert cpv_ci_preflight._parse_enabled_linters(text) == {
        "PYTHON_BANDIT",
        "REPOSITORY_CHECKOV",
        "SPELL_CSPELL",
    }


def test_parse_inline_flow_form() -> None:
    """The inline-flow `ENABLE_LINTERS: [A, B]` form is parsed."""
    text = "ENABLE_LINTERS: [PYTHON_BANDIT, REPOSITORY_TRIVY]\n"
    assert cpv_ci_preflight._parse_enabled_linters(text) == {
        "PYTHON_BANDIT",
        "REPOSITORY_TRIVY",
    }


def test_parse_enable_alias_key() -> None:
    """The `ENABLE:` key (alias) is honoured as well as `ENABLE_LINTERS:`."""
    text = "ENABLE:\n  - BASH_SHELLCHECK\n"
    assert cpv_ci_preflight._parse_enabled_linters(text) == {"BASH_SHELLCHECK"}


def test_parse_ignores_commented_out_linter() -> None:
    """A commented-out block item is NOT counted as enabled."""
    text = "ENABLE_LINTERS:\n  - PYTHON_BANDIT\n  # - REPOSITORY_CHECKOV\n"
    enabled = cpv_ci_preflight._parse_enabled_linters(text)
    assert "PYTHON_BANDIT" in enabled
    assert "REPOSITORY_CHECKOV" not in enabled


def test_parse_block_ends_at_next_top_level_key() -> None:
    """A following top-level key ends the block (does not absorb its value)."""
    text = (
        "ENABLE_LINTERS:\n"
        "  - PYTHON_BANDIT\n"
        "PYTHON_RUFF_ARGUMENTS: \"--select=E\"\n"
        "  - REPOSITORY_TRIVY\n"  # orphan after the key — must NOT be picked up
    )
    enabled = cpv_ci_preflight._parse_enabled_linters(text)
    assert enabled == {"PYTHON_BANDIT"}


def test_parse_no_enable_key_is_empty() -> None:
    """A config without any enable key yields the empty set."""
    assert cpv_ci_preflight._parse_enabled_linters("APPLY_FIXES: none\n") == set()


def test_enabled_linters_none_when_no_config(tmp_path: Path) -> None:
    """`_megalinter_enabled_linters` returns None when `.mega-linter.yml` is absent."""
    root = _make_plugin(tmp_path)
    assert cpv_ci_preflight._megalinter_enabled_linters(root) is None


def test_enabled_linters_real_cpv_default_set(tmp_path: Path) -> None:
    """The exact CPV-generated default set parses to all 12 enabled linters."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(
        root,
        [
            "PYTHON_RUFF",
            "PYTHON_MYPY",
            "PYTHON_BANDIT",
            "BASH_SHELLCHECK",
            "BASH_SHFMT",
            "JSON_JSONLINT",
            "YAML_YAMLLINT",
            "MARKDOWN_MARKDOWNLINT",
            "SPELL_CSPELL",
            "COPYPASTE_JSCPD",
            "REPOSITORY_CHECKOV",
            "REPOSITORY_TRIVY",
        ],
    )
    enabled = cpv_ci_preflight._megalinter_enabled_linters(root)
    assert enabled is not None
    for lid in ("PYTHON_BANDIT", "SPELL_CSPELL", "REPOSITORY_CHECKOV", "REPOSITORY_TRIVY"):
        assert lid in enabled


# ===========================================================================
# cspell probe — full matrix
# ===========================================================================


def test_cspell_enabled_present_clean_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cspell enabled + on PATH + exit 0 → PASS."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])
    _patch_tool(monkeypatch, present={"cspell"}, run_result={"cspell": _FakeProc(0)})
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "cspell")
    assert f.severity == "PASS"


def test_cspell_enabled_present_error_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cspell enabled + on PATH + non-zero exit → FAIL with the first error line."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])
    _patch_tool(
        monkeypatch,
        present={"cspell"},
        run_result={"cspell": _FakeProc(1, stdout="README.md:3:1 - Unknown word (teh)\n")},
    )
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "cspell")
    assert f.severity == "FAIL"
    assert "Unknown word (teh)" in f.message


def test_cspell_enabled_absent_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cspell enabled but NOT on PATH → non-blocking WARNING (never FAIL)."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])
    _patch_tool(monkeypatch, present=set())
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "cspell")
    assert f.severity == "WARNING"


def test_cspell_not_enabled_passes_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cspell NOT in ENABLE_LINTERS → clean PASS "skipped" (tool never invoked)."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["PYTHON_RUFF"])  # cspell absent from the list
    # cspell IS present on PATH — but must NOT be run because it's not enabled.
    calls: list[str] = []

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    def fake_run(argv: list[str], **_kw: object) -> _FakeProc:
        calls.append(Path(argv[0]).name)
        return _FakeProc(1)  # would FAIL if ever called

    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", fake_which)
    monkeypatch.setattr(cpv_ci_preflight.subprocess, "run", fake_run)
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "cspell")
    assert f.severity == "PASS"
    assert "not enabled" in f.message
    assert "cspell" not in calls  # the not-enabled probe never invokes the tool


# ===========================================================================
# checkov probe — full matrix
# ===========================================================================


def test_checkov_enabled_present_clean_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """checkov enabled + on PATH + exit 0 → PASS."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["REPOSITORY_CHECKOV"])
    _patch_tool(monkeypatch, present={"checkov"}, run_result={"checkov": _FakeProc(0)})
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "checkov")
    assert f.severity == "PASS"


def test_checkov_enabled_present_error_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """checkov enabled + on PATH + non-zero exit → FAIL with the first error line."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["REPOSITORY_CHECKOV"])
    _patch_tool(
        monkeypatch,
        present={"checkov"},
        run_result={
            "checkov": _FakeProc(
                1, stdout="Check: CKV_DOCKER_2: \"Ensure HEALTHCHECK\"\n\tFAILED\n"
            )
        },
    )
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "checkov")
    assert f.severity == "FAIL"
    assert "CKV_DOCKER_2" in f.message


def test_checkov_enabled_absent_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """checkov enabled but NOT on PATH → non-blocking WARNING."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["REPOSITORY_CHECKOV"])
    _patch_tool(monkeypatch, present=set())
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "checkov")
    assert f.severity == "WARNING"


def test_checkov_not_enabled_passes_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """checkov NOT in ENABLE_LINTERS → clean PASS "skipped"."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])  # checkov absent
    _patch_tool(monkeypatch, present={"checkov"})  # present but must not run
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "checkov")
    assert f.severity == "PASS"
    assert "not enabled" in f.message


# ===========================================================================
# trivy / bandit probes — spot the enabled+error and not-enabled paths
# ===========================================================================


def test_trivy_enabled_error_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """trivy enabled + non-zero exit → FAIL."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["REPOSITORY_TRIVY"])
    _patch_tool(
        monkeypatch,
        present={"trivy"},
        run_result={"trivy": _FakeProc(1, stdout="Dockerfile (dockerfile)\nHIGH: DS002\n")},
    )
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    assert _finding(result, "trivy").severity == "FAIL"


def test_bandit_not_enabled_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """bandit NOT enabled → clean PASS "skipped"."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["REPOSITORY_TRIVY"])
    _patch_tool(monkeypatch, present={"bandit"})
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "bandit")
    assert f.severity == "PASS"
    assert "not enabled" in f.message


def test_bandit_enabled_clean_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """bandit enabled + on PATH + exit 0 → PASS (argv targets scripts/)."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["PYTHON_BANDIT"])
    captured: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    def fake_run(argv: list[str], **_kw: object) -> _FakeProc:
        captured.append(argv)
        return _FakeProc(0)

    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", fake_which)
    monkeypatch.setattr(cpv_ci_preflight.subprocess, "run", fake_run)
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    assert _finding(result, "bandit").severity == "PASS"
    # The bandit invocation recurses scripts/.
    bandit_calls = [a for a in captured if Path(a[0]).name == "bandit"]
    assert bandit_calls and "scripts/" in bandit_calls[0]


# ===========================================================================
# shellcheck / shfmt — file-list probes
# ===========================================================================


def test_shellcheck_enabled_no_shell_files_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """shellcheck enabled but the plugin ships no *.sh → clean PASS (nothing to lint)."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["BASH_SHELLCHECK"])
    _patch_tool(monkeypatch, present={"shellcheck"})
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "shellcheck")
    assert f.severity == "PASS"
    assert "No *.sh" in f.message


def test_shellcheck_enabled_present_error_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """shellcheck enabled + a *.sh present + non-zero exit → FAIL."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["BASH_SHELLCHECK"])
    (root / "scripts" / "x.sh").write_text("#!/bin/sh\necho $UNQUOTED\n", encoding="utf-8")
    # The gate surfaces the FIRST non-empty output line (terse report contract),
    # so put the diagnostic on the first line to assert that surfacing.
    _patch_tool(
        monkeypatch,
        present={"shellcheck"},
        run_result={"shellcheck": _FakeProc(1, stdout="x.sh:2:6: note: SC2086 Double quote\n")},
    )
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "shellcheck")
    assert f.severity == "FAIL"
    assert "SC2086" in f.message


def test_shellcheck_enabled_absent_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """shellcheck enabled + a *.sh present but tool absent → WARNING."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["BASH_SHELLCHECK"])
    (root / "scripts" / "x.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    _patch_tool(monkeypatch, present=set())
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    assert _finding(result, "shellcheck").severity == "WARNING"


def test_shfmt_not_enabled_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """shfmt NOT enabled → clean PASS "skipped"."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["BASH_SHELLCHECK"])
    (root / "scripts" / "x.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    _patch_tool(monkeypatch, present={"shfmt"})
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    f = _finding(result, "shfmt")
    assert f.severity == "PASS"
    assert "not enabled" in f.message


# ===========================================================================
# Orchestration — no-config skip + never-block contract
# ===========================================================================


def test_megalinter_no_config_all_probes_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `.mega-linter.yml` → every Mega-Linter probe is a clean PASS "skipped"."""
    root = _make_plugin(tmp_path)
    # All tools present — but with no config NONE may run.
    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fail_run(argv: list[str], **_kw: object) -> _FakeProc:
        raise AssertionError(f"no probe should run without a config: {argv}")

    monkeypatch.setattr(cpv_ci_preflight.subprocess, "run", fail_run)
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    for gate in ("cspell", "checkov", "trivy", "bandit", "shellcheck", "shfmt"):
        f = _finding(result, gate)
        assert f.severity == "PASS"
        assert "not enabled" in f.message


def test_megalinter_error_makes_preflight_exit_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An enabled+present+error Mega-Linter probe drives a non-zero preflight exit."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["REPOSITORY_CHECKOV"])
    # Mock jscpd/actionlint/mypy/uv as absent (WARNING/PASS), checkov as failing.
    monkeypatch.setattr(
        cpv_ci_preflight.shutil,
        "which",
        lambda name: "/usr/bin/checkov" if name == "checkov" else None,
    )
    monkeypatch.setattr(
        cpv_ci_preflight.subprocess,
        "run",
        lambda argv, **_kw: _FakeProc(1, stdout="CKV_DOCKER_2 FAILED\n"),
    )
    result = run_ci_preflight(root)
    assert result.exit_code == 1
    assert any(f.gate == "checkov" and f.severity == "FAIL" for f in result.fails)


def test_megalinter_absent_tools_never_block_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every Mega-Linter linter enabled but every tool absent → exit 0 (never block)."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(
        root,
        ["SPELL_CSPELL", "REPOSITORY_CHECKOV", "REPOSITORY_TRIVY", "PYTHON_BANDIT", "BASH_SHELLCHECK", "BASH_SHFMT"],
    )
    (root / "scripts" / "x.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    # Everything absent — and no static CIP defect (no workflows).
    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", lambda _name: None)
    result = run_ci_preflight(root)
    # Tool-absent on enabled linters → WARNING, never FAIL → exit 0.
    assert result.exit_code == 0
    warn_gates = {f.gate for f in result.warnings}
    assert {"cspell", "checkov", "trivy", "bandit", "shellcheck", "shfmt"} <= warn_gates


def test_megalinter_run_timeout_degrades_to_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Mega-Linter probe whose subprocess times out → WARNING, not FAIL."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["REPOSITORY_CHECKOV"])

    def timeout_run(argv: list[str], **_kw: object) -> _FakeProc:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cpv_ci_preflight.subprocess, "run", timeout_run)
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    assert _finding(result, "checkov").severity == "WARNING"


def test_megalinter_unreadable_config_skips_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present `.mega-linter.yml` with no enable key → every probe skips (PASS)."""
    root = _make_plugin(tmp_path)
    (root / ".mega-linter.yml").write_text("APPLY_FIXES: none\n", encoding="utf-8")
    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fail_run(argv: list[str], **_kw: object) -> _FakeProc:
        raise AssertionError("no probe should run with no enabled linters")

    monkeypatch.setattr(cpv_ci_preflight.subprocess, "run", fail_run)
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_megalinter(result)
    for gate in ("cspell", "checkov", "trivy", "bandit", "shellcheck", "shfmt"):
        assert _finding(result, gate).severity == "PASS"


# ---------------------------------------------------------------------------
# Regression: a probe MUST NOT false-block the canonical generated plugin
# (central-verify dogfood, TRDD-HZSI0BZ6). A freshly-scaffolded plugin IS
# CI-green, yet its generated publish.py emits ~50 LOW-severity B404/B603
# "subprocess call" bandit findings. A bare `bandit -r` exits non-zero on those
# → it would FALSE-BLOCK a provably-CI-green plugin (the degrade-gracefully
# contract's cardinal sin). `_argv_bandit` filters to MEDIUM+ severity (`-ll`).
# ---------------------------------------------------------------------------


def test_argv_bandit_filters_to_medium_severity() -> None:
    """`_argv_bandit` passes `-ll` (MEDIUM+ severity) so the LOW subprocess noise
    every canonical generated publish.py produces does not false-block a publish."""
    argv = cpv_ci_preflight._argv_bandit("/usr/bin/bandit", Path("/x"))
    assert "-ll" in argv, f"bandit probe must filter to MEDIUM+ severity (-ll): {argv}"


def test_generated_plugin_megalinter_probes_do_not_false_block(tmp_path: Path) -> None:
    """The Mega-Linter probes produce NO FAIL on a freshly-scaffolded plugin
    (CI-green by construction). On a bare runner every tool is absent → WARNING →
    no FAIL; on a dev box with the tool present it catches a probe miscalibration
    (the bandit-on-LOW false-block this regression locks)."""
    import generate_plugin_repo as gen

    params = gen.PluginParams(
        name="ml-clean-sample",
        description="x",
        author="A",
        author_email="a@a.a",
        github_owner="Emasoft",
    )
    target = tmp_path / "ml-clean-sample"
    target.mkdir(parents=True)
    gen.generate_plugin_repo(target, params)
    result = PreflightResult(plugin_path=target)
    cpv_ci_preflight._gate_megalinter(result)
    fails = [f for f in result.findings if f.severity == "FAIL"]
    assert fails == [], (
        "Mega-Linter probes false-blocked a canonical generated plugin "
        f"(probe miscalibration): {[(f.gate, f.message[:80]) for f in fails]}"
    )
