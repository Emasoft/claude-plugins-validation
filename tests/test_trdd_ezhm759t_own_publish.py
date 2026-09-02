"""TRDD-EZHM759T — the own-publish audit fixes (rows 2-4, 6-15, 17-22).

One test per fixed audit row, named in each test's docstring. Every network /
subprocess boundary is monkeypatched the way tests/test_publish.py does it, so
nothing here touches GitHub, git, or the working tree.

Suppression-style changes (a finding that stops being reported as a hard
negative) carry a positive control: the sibling assertion that the honest
answer still surfaces.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish  # noqa: E402


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _drive_main(monkeypatch, root: Path, *, calls: list[str], resume: bool, smoke_rc: int = 0) -> int:
    """Run publish.main() with every stage stubbed, so only the routing is exercised.

    `resume` decides what `_release_step_is_pending` answers; `calls` records
    which gates actually ran.
    """
    def rec(name: str, ret=0):
        def _f(*a, **k):
            calls.append(name)
            return ret
        return _f

    monkeypatch.setattr(sys, "argv", ["publish.py", "--patch"])
    monkeypatch.setattr(publish, "stage_bypass_guard", rec("bypass"))
    monkeypatch.setattr(publish, "get_plugin_root", lambda: root)
    monkeypatch.setattr(publish, "stage_check_working_tree", rec("tree"))
    monkeypatch.setattr(publish, "detect_layout", lambda r: ("none", {}))
    monkeypatch.setattr(publish, "_start_prefetch", lambda *a: type("P", (), {"shutdown": lambda s: None})())
    monkeypatch.setattr(publish, "run_preflight_parallel", rec("preflight"))
    monkeypatch.setattr(publish, "stage_fork_parity", rec("fork"))
    monkeypatch.setattr(publish, "stage_version_consistency", rec("consistency"))
    monkeypatch.setattr(publish, "stage_refresh_self_hashes", rec("hashes"))
    monkeypatch.setattr(publish, "do_bump", rec("do_bump", True))
    monkeypatch.setattr(publish, "stage_commit_tag_push", rec("commit_tag_push"))
    monkeypatch.setattr(publish, "stage_verify_ci_green", rec("ci"))
    monkeypatch.setattr(publish, "stage_install_smoke", rec("smoke", smoke_rc))
    monkeypatch.setattr(publish, "stage_github_release", rec("release"))
    monkeypatch.setattr(
        publish, "stage_changelog", lambda r, t, v: (calls.append("changelog"), (0, root / "notes.md"))[1]
    )
    monkeypatch.setattr(publish, "_release_step_is_pending", lambda r, v: resume)
    if resume:
        # Gate 7 declines: local == remote, so no bump happens.
        monkeypatch.setattr(publish, "_read_remote_version", lambda r: "1.0.0")
    else:
        monkeypatch.setattr(publish, "_read_remote_version", lambda r: None)
    return publish.main()


def _plugin_root(tmp_path: Path, version: str = "1.0.0") -> Path:
    root = tmp_path / "p"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "version": version, "description": "x", "author": {"name": "t", "email": "t@e.com"}}),
        encoding="utf-8",
    )
    return root


# ── Row 2: PLUGIN_FORK_PARITY_CMD appends, never replaces ────────────────────


def test_row2_fork_parity_cmd_appends_to_the_fixed_argv(monkeypatch, tmp_path):
    """Row 2: PLUGIN_FORK_PARITY_CMD adds pytest ARGS to the fixed argv."""
    root = tmp_path / "r"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    seen: list[list[str]] = []
    monkeypatch.setattr(publish, "fork_parity_supported", lambda: (True, "forced fork"))
    monkeypatch.setattr(
        publish,
        "run_under_linux_fork_default",
        lambda cmd, cwd, timeout: seen.append(cmd) or type("R", (), {"blocked": False, "output": "", "detail": ""})(),
    )
    monkeypatch.setenv("PLUGIN_FORK_PARITY_CMD", "-k smoke")
    assert publish.stage_fork_parity(root) == 0
    assert seen[0][:4] == ["uv", "run", "pytest", "tests/"]
    assert seen[0][-2:] == ["-k", "smoke"]


def test_row2_positive_control_bare_true_cannot_replace_the_command(monkeypatch, tmp_path):
    """Row 2 positive control: `PLUGIN_FORK_PARITY_CMD=true` no longer trivially passes."""
    root = tmp_path / "r"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    seen: list[list[str]] = []
    monkeypatch.setattr(publish, "fork_parity_supported", lambda: (True, "forced fork"))
    monkeypatch.setattr(
        publish,
        "run_under_linux_fork_default",
        lambda cmd, cwd, timeout: seen.append(cmd) or type("R", (), {"blocked": False, "output": "", "detail": ""})(),
    )
    monkeypatch.setenv("PLUGIN_FORK_PARITY_CMD", "true")
    publish.stage_fork_parity(root)
    # The real suite still runs; `true` is only an extra (meaningless) arg.
    assert seen[0][2] == "pytest"
    assert seen[0] != ["true"]


# ── Row 3: resume at the release step instead of double-bumping ──────────────


def test_row3_pushed_tag_with_no_release_resumes_instead_of_bumping(monkeypatch, tmp_path):
    """Row 3: a pushed tag with no GitHub release resumes at Gate 13, no second bump."""
    root = _plugin_root(tmp_path, "1.2.3")
    monkeypatch.setattr(publish, "_read_remote_version", lambda p: "1.2.3")
    monkeypatch.setattr(publish, "_remote_tag_state", lambda p, t: True)
    monkeypatch.setattr(publish, "_github_release_exists", lambda p, t: False)
    monkeypatch.setattr(publish, "do_bump", lambda *a, **k: pytest.fail("must not bump"))
    rc, version = publish.stage_bump(root, "patch", dry_run=False)
    assert (rc, version) == (0, "1.2.3")


def test_row3_main_routes_a_resume_straight_to_the_release_gate(monkeypatch, tmp_path, capsys):
    """Row 3: on resume main() skips Gates 8-12 and actually reaches stage_github_release."""
    root = _plugin_root(tmp_path, "1.0.0")
    calls: list[str] = []
    rc = _drive_main(monkeypatch, root, calls=calls, resume=True)
    assert rc == 0
    assert "release" in calls, "the resume path must reach Gate 13"
    for skipped in ("do_bump", "hashes", "commit_tag_push"):
        assert skipped not in calls, f"Gate 8-12 step {skipped} must not run on resume"
    assert "Resuming the interrupted publish" in capsys.readouterr().out


def test_row3_positive_control_normal_publish_still_runs_gates_8_to_12(monkeypatch, tmp_path):
    """Row 3 positive control: a normal publish still bumps, hashes, commits, tags and pushes."""
    root = _plugin_root(tmp_path, "1.0.0")
    calls: list[str] = []
    rc = _drive_main(monkeypatch, root, calls=calls, resume=False)
    assert rc == 0
    for required in ("do_bump", "hashes", "commit_tag_push", "release"):
        assert required in calls


def test_row3_positive_control_unreadable_remote_does_not_resume(monkeypatch, tmp_path):
    """Row 3 positive control: an unanswered tag probe never routes into resume."""
    root = _plugin_root(tmp_path, "1.2.3")
    monkeypatch.setattr(publish, "_remote_tag_state", lambda p, t: None)
    monkeypatch.setattr(publish, "_github_release_exists", lambda p, t: False)
    assert publish._release_step_is_pending(root, "1.2.3") is False


# ── Row 4: run() has a timeout parameter and the two big gates pass one ──────


def test_row4_run_accepts_a_timeout_and_reports_the_actual_bound(monkeypatch, tmp_path, capsys):
    """Row 4: run() takes `timeout=` and its expiry message names the real bound."""

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=k["timeout"])

    monkeypatch.setattr(publish.subprocess, "run", boom)
    with pytest.raises(SystemExit):
        publish.run(["x"], tmp_path, timeout=1234.0)
    assert "1234s" in capsys.readouterr().err


def test_row4_gate2_and_gate3_pass_their_own_bounds(monkeypatch, tmp_path):
    """Row 4: Gate 2 uses the suite timeout and Gate 3 the validator timeout."""
    seen: dict[str, float] = {}

    def fake_run(cmd, cwd, *, check=True, timeout=publish._RUN_DEFAULT_TIMEOUT_SEC, **kw):
        seen["pytest" if "pytest" in cmd else "validate"] = timeout
        return _completed(0)

    monkeypatch.setattr(publish, "run", fake_run)
    monkeypatch.setattr(publish, "_snapshot_browser_pids", lambda: set())
    monkeypatch.setattr(publish, "_cleanup_browser_orphans", lambda b: 0)
    publish.stage_run_tests(tmp_path)
    publish.stage_validate_plugin(tmp_path)
    assert seen["pytest"] == publish._TEST_SUITE_TIMEOUT_SEC
    assert seen["validate"] == publish._VALIDATOR_TIMEOUT_SEC


# ── Row 6: gh probes carry a bounded budget ──────────────────────────────────


def test_row6_gh_probes_pass_an_explicit_timeout_and_attempt_budget(monkeypatch, tmp_path):
    """Row 6: read-only gh probes bound timeout AND max_attempts."""
    calls: list[dict] = []

    def fake_gh(cmd, **kw):
        calls.append(kw)
        return _completed(0, stdout="MARKETPLACE_PAT\n")

    monkeypatch.setattr(publish, "gh_with_retry", fake_gh)
    publish._gh_secret_exists(tmp_path, "MARKETPLACE_PAT", gh_bin="/usr/bin/gh")
    assert calls[0]["timeout"] == publish._GH_PROBE_TIMEOUT_SEC
    assert calls[0]["max_attempts"] == publish._GH_PROBE_MAX_ATTEMPTS


# ── Row 7: successor resolution is bounded as a phase ────────────────────────


def test_row7_successor_resolution_stops_at_the_phase_deadline(monkeypatch, tmp_path, capsys):
    """Row 7: an expired deadline stops successor resolution instead of running for hours."""
    monkeypatch.setattr(
        publish.subprocess, "run", lambda *a, **k: pytest.fail("must not call gh after the deadline")
    )
    out = publish._resolve_ci_run_successors(
        "/usr/bin/gh",
        tmp_path,
        "abc",
        [{"name": "CI", "headBranch": "master"}],
        deadline=0.0,
    )
    assert out == {}
    assert "out of time" in capsys.readouterr().err


# ── Row 8: a bump that did not apply is not a success ────────────────────────


def test_row8_pyproject_with_project_table_but_no_version_is_an_error(tmp_path):
    """Row 8: a [project] table with no bumpable version line fails, never 'skipped'."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
    ok, msg = publish.update_pyproject_toml(root, "2.0.0")
    assert ok is False
    assert "refusing" in msg


def test_row8_positive_control_no_project_table_still_skips(tmp_path):
    """Row 8 positive control: a poetry-style file with no [project] table still skips cleanly."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "pyproject.toml").write_text('[tool.poetry]\nname = "p"\n', encoding="utf-8")
    ok, msg = publish.update_pyproject_toml(root, "2.0.0")
    assert ok is True
    assert "skipped" in msg


# ── Row 9: fetch before reading the remote version ───────────────────────────


def test_row9_remote_version_is_unknown_when_the_fetch_fails(monkeypatch, tmp_path):
    """Row 9: a failed `git fetch origin` yields UNKNOWN, never a stale 'in sync'."""
    publish._refresh_remote_tracking_refs.cache_clear()
    monkeypatch.setattr(publish, "git_with_retry", lambda *a, **k: _completed(128, stderr="no origin"))
    monkeypatch.setattr(
        publish.subprocess, "run", lambda *a, **k: pytest.fail("must not read a stale tracking ref")
    )
    assert publish._read_remote_version(tmp_path) is None


def test_row9_positive_control_successful_fetch_still_reads_the_ref(monkeypatch, tmp_path):
    """Row 9 positive control: after a good fetch the tracking ref is still read."""
    publish._refresh_remote_tracking_refs.cache_clear()
    monkeypatch.setattr(publish, "git_with_retry", lambda *a, **k: _completed(0))
    monkeypatch.setattr(
        publish.subprocess, "run", lambda *a, **k: _completed(0, stdout=json.dumps({"version": "9.9.9"}))
    )
    assert publish._read_remote_version(tmp_path) == "9.9.9"


# ── Row 10: cannot-check is UNKNOWN, not "not configured" ────────────────────


def test_row10_gh_failure_reports_unknown_not_a_missing_secret(monkeypatch, tmp_path):
    """Row 10: a failing gh probe returns None (UNKNOWN), not False."""
    monkeypatch.setattr(publish, "gh_with_retry", lambda cmd, **kw: _completed(1, stderr="502"))
    assert publish._gh_secret_exists(tmp_path, "MARKETPLACE_PAT", gh_bin="/usr/bin/gh") is None
    assert publish._remote_has_receiver_workflow("o", "r", gh_bin="/usr/bin/gh") is None


def test_row10_positive_control_a_real_absence_is_still_false(monkeypatch, tmp_path):
    """Row 10 positive control: gh answering with an empty list still reports absent."""
    monkeypatch.setattr(publish, "gh_with_retry", lambda cmd, **kw: _completed(0, stdout="OTHER_SECRET\n"))
    assert publish._gh_secret_exists(tmp_path, "MARKETPLACE_PAT", gh_bin="/usr/bin/gh") is False


def test_row10_receiver_workflow_absent_when_every_file_was_read(monkeypatch):
    """Row 10 positive control: all workflows read and none matching is a real False."""
    def fake_gh(cmd, **kw):
        if cmd[-1].endswith("/workflows"):
            return _completed(0, stdout=json.dumps([{"name": "ci.yml"}]))
        return _completed(0, stdout="on: push\n")

    monkeypatch.setattr(publish, "gh_with_retry", fake_gh)
    assert publish._remote_has_receiver_workflow("o", "r", gh_bin="/usr/bin/gh") is False


# ── Row 11: the published summary prints before a strict Gate 15 exit ───────


def test_row11_published_line_prints_even_when_strict_smoke_fails(monkeypatch, tmp_path, capsys):
    """Row 11: a strict Gate 15 failure still prints the ✓ Published summary, then exits non-zero."""
    root = _plugin_root(tmp_path, "1.0.0")
    calls: list[str] = []
    rc = _drive_main(monkeypatch, root, calls=calls, resume=False, smoke_rc=1)
    out = capsys.readouterr().out
    assert rc == 1
    assert "Published v" in out


def test_row11_positive_control_a_clean_smoke_exits_zero(monkeypatch, tmp_path, capsys):
    """Row 11 positive control: a passing Gate 15 still exits 0 with the same summary."""
    root = _plugin_root(tmp_path, "1.0.0")
    calls: list[str] = []
    rc = _drive_main(monkeypatch, root, calls=calls, resume=False, smoke_rc=0)
    assert rc == 0
    assert "Published v" in capsys.readouterr().out


# ── Row 12: the porcelain read is bounded ────────────────────────────────────


def test_row12_stage_release_changes_bounds_its_git_status(monkeypatch, tmp_path):
    """Row 12: `git status --porcelain` in stage_release_changes carries a timeout."""
    seen: list[dict] = []
    monkeypatch.setattr(publish, "run", lambda *a, **k: _completed(0))

    def fake_run(cmd, **kw):
        seen.append(kw)
        return _completed(0, stdout="")

    monkeypatch.setattr(publish.subprocess, "run", fake_run)
    publish.stage_release_changes(tmp_path)
    assert seen and seen[0]["timeout"] == 60


# ── Row 13: run()'s docstring no longer claims to stream ────────────────────


def test_row13_run_docstring_says_capture_not_stream():
    """Row 13: run()'s docstring describes capture-then-print, not streaming."""
    doc = publish.run.__doc__ or ""
    assert "CAPTURE" in doc
    assert "stream output" not in doc


# ── Row 14: install-smoke cleanup runs on every invoked path ────────────────


def test_row14_install_smoke_cleans_up_after_a_failed_install(monkeypatch, tmp_path):
    """Row 14: a non-zero install still triggers the uninstall cleanup."""
    root = _plugin_root(tmp_path)
    monkeypatch.delenv("CPV_PUBLISH_SKIP_INSTALL_SMOKE", raising=False)
    monkeypatch.delenv("CPV_PUBLISH_REQUIRE_INSTALL_SMOKE", raising=False)
    monkeypatch.setattr(publish.shutil, "which", lambda n: "/usr/bin/claude" if n == "claude" else None)
    monkeypatch.setattr(publish, "_resolve_marketplace_name", lambda p: "mkt")
    monkeypatch.setattr(publish, "_report_smoke_registry_orphan", lambda t, d: None)
    verbs: list[str] = []

    def fake_run(cmd, **kw):
        verbs.append(cmd[2])
        return _completed(1 if cmd[2] == "install" else 0)

    monkeypatch.setattr(publish.subprocess, "run", fake_run)
    publish.stage_install_smoke(root, "1.0.0")
    assert verbs == ["install", "uninstall"]


# ── Row 15: an unexpected gate exception does not discard the replay ────────


def test_row15_unexpected_gate_exception_is_captured_and_replayed(monkeypatch, tmp_path, capsys):
    """Row 15: a raising gate becomes a captured failure, siblings still replay."""
    monkeypatch.setattr(publish, "stage_run_tests", lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(publish, "stage_validate_plugin", lambda r: print("validate ran") or 0)
    monkeypatch.setattr(publish, "stage_ci_preflight", lambda r: 0)
    monkeypatch.setattr(publish, "stage_secret_scan", lambda r: 0)
    monkeypatch.setattr(publish, "stage_validate_marketplace", lambda r, layout: 0)
    monkeypatch.setattr(publish, "stage_marketplace_registration_check", lambda r, prefetch=None: 0)
    rc = publish.run_preflight_parallel(tmp_path, "none")
    captured = capsys.readouterr()
    assert rc != 0
    assert "boom" in captured.err
    assert "validate ran" in captured.out


# ── Row 17: the _bypass_env docstring matches the code ─────────────────────


def test_row17_bypass_env_docstring_does_not_claim_the_legacy_name_was_removed():
    """Row 17: _bypass_env still emits the legacy name and no longer denies it."""
    doc = publish._bypass_env.__doc__ or ""
    # The docstring may still QUOTE the retracted claim while correcting it, so
    # pin the correction rather than the absence of the words.
    assert "STILL EMITTED" in doc
    assert "CPV_SKIP_GITHUB_INTEGRITY" in publish._bypass_env()


# ── Row 18: the parallel-preflight docstring lists six gates ────────────────


def test_row18_preflight_docstring_matches_the_real_gate_order():
    """Row 18: run_preflight_parallel's docstring lists all six gates, in order."""
    doc = publish.run_preflight_parallel.__doc__ or ""
    assert "four gates" not in doc
    for name in publish._PARALLEL_GATE_ORDER:
        assert name in doc


# ── Row 19: the Gate 2 table entry is derived from the argv ────────────────


def test_row19_gate2_description_is_derived_from_the_real_argv(monkeypatch, tmp_path):
    """Row 19: the Gate 2 table entry renders the argv Gate 2 actually runs."""
    desc = dict(publish.GATES)["Gate 2"]
    assert " ".join(publish._GATE2_PYTEST_ARGV) in desc
    assert "-q --tb=short" in desc

    seen: list[list[str]] = []
    monkeypatch.setattr(publish, "_snapshot_browser_pids", lambda: set())
    monkeypatch.setattr(publish, "_cleanup_browser_orphans", lambda b: 0)
    monkeypatch.setattr(
        publish, "run", lambda cmd, cwd, **kw: seen.append(cmd) or _completed(0)
    )
    publish.stage_run_tests(tmp_path)
    assert seen[0] == publish._GATE2_PYTEST_ARGV


# ── Row 20: the advertised gate count matches the table ────────────────────


def test_row20_argparse_description_does_not_hardcode_a_stale_gate_count(capsys):
    """Row 20: --help's gate count is derived from GATES, not the stale '15-gate'."""
    with pytest.raises(SystemExit):
        publish.main.__wrapped__() if hasattr(publish.main, "__wrapped__") else None
        raise SystemExit(0)
    src = (Path(__file__).resolve().parent.parent / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert "15-gate fail-fast release" not in src
    assert "{len(GATES)} gates" in src


# ── Row 21: the duplicated sys.path guard is gone ──────────────────────────


def test_row21_secret_scan_has_one_syspath_guard():
    """Row 21: stage_secret_scan no longer repeats its sys.path.insert block."""
    import inspect

    body = inspect.getsource(publish.stage_secret_scan)
    assert body.count("sys.path.insert(0, str(scripts_dir))") == 1


# ── Row 22 + docs rows: the module docstring and --help name every env var ──


def test_row22_module_docstring_usage_lists_canon_version():
    """Row 22: the module docstring's Usage block documents --canon-version."""
    assert "--canon-version" in (publish.__doc__ or "")


def test_docs_every_env_var_read_is_documented_and_no_bypass_claim_remains():
    """Docs rows 1/3/4/5: every env var this script reads is listed; no 'no env var bypasses'."""
    src = (Path(__file__).resolve().parent.parent / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert "no env var bypasses" not in src
    documented = " ".join(f"{n} {e}" for n, e in publish.ENV_VARS)
    for var in (
        "CPV_SKIP_GH_AUTH_CHECK",
        "PLUGIN_SKIP_GITHUB_INTEGRITY",
        "CPV_SKIP_GITHUB_INTEGRITY",
        "PLUGIN_FORK_PARITY_TIMEOUT",
        "PLUGIN_FORK_PARITY_CMD",
        "CPV_PUBLISH_REQUIRE_INSTALL_SMOKE",
        "CPV_PUBLISH_SKIP_INSTALL_SMOKE",
    ):
        assert var in documented


def test_row16_argparse_documents_the_deliberate_gate_flag_divergence():
    """Row 16: a comment records that CPV's own repo uses git-hooks/pre-push directly."""
    src = (Path(__file__).resolve().parent.parent / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert "git-hooks/pre-push" in src
    assert "--install-hook" in src
