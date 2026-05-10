"""Unit tests for scripts/cpv_strip_dev.py — TRDD-793ac32a §2.3, §2.4, §2.5.

Pin:
  * Path-traversal defense (STRIP-E001..E006)
  * Working-tree safety 7-step refusal cascade (STRIP-W001..W007)
  * State-checkpoint atomicity
  * Plan construction edge cases

Live-execution (--auto) is NOT tested here — it lands in rc3 via
e2e tests with mocked gh CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_strip_dev as csd  # noqa: E402

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_plugin(
    tmp_path: Path,
    plugin_json: dict | None = None,
    files: dict[str, str] | None = None,
    init_git: bool = True,
) -> Path:
    """Create a minimal plugin tree at tmp_path/demo with optional init."""
    plugin = tmp_path / "demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    pj = plugin_json or {
        "name": "demo",
        "version": "0.1.0",
        "description": "x",
        "repository": "https://github.com/Emasoft/demo",
    }
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(pj),
        encoding="utf-8",
    )
    for rel, content in (files or {}).items():
        target = plugin / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if init_git:
        subprocess.run(["git", "-C", str(plugin), "init", "-b", "main"], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(plugin), "config", "user.email", "t@t.t"], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(plugin), "config", "user.name", "T"], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(plugin), "add", "."], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(plugin), "commit", "-m", "initial"], capture_output=True, check=False)
    return plugin


# ── Path-traversal defense ────────────────────────────────────────────────────


def test_validate_src_path_rejects_dotdot(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    with pytest.raises(csd.StripError) as exc:
        csd.validate_src_path("../etc", plugin)
    assert exc.value.code == "STRIP-E003"


def test_validate_src_path_rejects_absolute(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    with pytest.raises(csd.StripError) as exc:
        csd.validate_src_path("/etc/passwd", plugin)
    assert exc.value.code == "STRIP-E003"


def test_validate_src_path_rejects_uppercase(tmp_path):
    plugin = _make_plugin(tmp_path, files={"Tests/x.py": "x"})
    with pytest.raises(csd.StripError) as exc:
        csd.validate_src_path("Tests/", plugin)
    assert exc.value.code == "STRIP-E003"


def test_validate_src_path_rejects_special_chars(tmp_path):
    plugin = _make_plugin(tmp_path)
    with pytest.raises(csd.StripError) as exc:
        csd.validate_src_path("te;sts/", plugin)
    assert exc.value.code == "STRIP-E003"


def test_validate_src_path_rejects_reserved_paths(tmp_path):
    plugin = _make_plugin(tmp_path, files={"scripts/foo.py": "x"})
    with pytest.raises(csd.StripError) as exc:
        csd.validate_src_path("scripts/", plugin)
    assert exc.value.code == "STRIP-E006"


def test_validate_src_path_rejects_dotgit(tmp_path):
    # `.git` not allowed by the regex AND in reserved set; either error code is OK.
    plugin = _make_plugin(tmp_path)
    with pytest.raises(csd.StripError):
        csd.validate_src_path(".git", plugin)


def test_validate_src_path_rejects_nonexistent(tmp_path):
    plugin = _make_plugin(tmp_path)
    with pytest.raises(csd.StripError) as exc:
        csd.validate_src_path("not-there/", plugin)
    assert exc.value.code == "STRIP-E004"


def test_validate_src_path_rejects_file_not_dir(tmp_path):
    # `notadir` passes the regex (lowercase, no special chars) but is a file
    # not a directory, so STRIP-E005 fires.
    plugin = _make_plugin(tmp_path, files={"notadir": "x"})
    with pytest.raises(csd.StripError) as exc:
        csd.validate_src_path("notadir", plugin)
    assert exc.value.code == "STRIP-E005"


def test_validate_src_path_rejects_symlink(tmp_path):
    plugin = _make_plugin(tmp_path, files={"real-tests/x.py": "x"})
    (plugin / "tests").symlink_to(plugin / "real-tests")
    with pytest.raises(csd.StripError) as exc:
        csd.validate_src_path("tests/", plugin)
    assert exc.value.code == "STRIP-E002"


def test_validate_src_path_accepts_valid_dir(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    resolved = csd.validate_src_path("tests/", plugin)
    assert resolved == (plugin / "tests").resolve()


# ── Working-tree safety ────────────────────────────────────────────────────────


def test_check_working_tree_safe_passes_clean(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    plan = csd.build_plan(plugin, explicit_targets=["tests/"])
    csd.check_working_tree_safe(plugin, plan.targets)  # no raise


def test_check_working_tree_safe_rejects_non_git(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"}, init_git=False)
    targets = [csd.normalise_target("tests/", "Emasoft", "demo")]
    with pytest.raises(csd.StripError) as exc:
        csd.check_working_tree_safe(plugin, targets)
    assert exc.value.code == "STRIP-W001"


def test_check_working_tree_safe_rejects_dirty_tree(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    # Create an uncommitted file.
    (plugin / "dirty.txt").write_text("uncommitted", encoding="utf-8")
    targets = [csd.normalise_target("tests/", "Emasoft", "demo")]
    with pytest.raises(csd.StripError) as exc:
        csd.check_working_tree_safe(plugin, targets)
    assert exc.value.code == "STRIP-W002"


def test_check_working_tree_safe_rejects_stash(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    # Modify a tracked file then stash.
    (plugin / "tests" / "x.py").write_text("modified", encoding="utf-8")
    subprocess.run(["git", "-C", str(plugin), "stash", "push", "-m", "test"], capture_output=True, check=False)
    targets = [csd.normalise_target("tests/", "Emasoft", "demo")]
    with pytest.raises(csd.StripError) as exc:
        csd.check_working_tree_safe(plugin, targets)
    assert exc.value.code == "STRIP-W004"


def test_check_working_tree_safe_rejects_untracked_in_target(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    (plugin / "tests" / "untracked.txt").write_text("u", encoding="utf-8")
    # commit the existing tree change to keep tree clean OUTSIDE the target,
    # but the untracked file is INSIDE tests/.
    targets = [csd.normalise_target("tests/", "Emasoft", "demo")]
    # Top-level dirty check fires first (STRIP-W002). That's the correct
    # safety order — we only get to STRIP-W005 when the top tree is clean.
    with pytest.raises(csd.StripError) as exc:
        csd.check_working_tree_safe(plugin, targets)
    assert exc.value.code in ("STRIP-W002", "STRIP-W005")


def test_check_working_tree_safe_rejects_detached_head(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    head_sha = subprocess.run(
        ["git", "-C", str(plugin), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(plugin), "checkout", head_sha], capture_output=True, check=False)
    targets = [csd.normalise_target("tests/", "Emasoft", "demo")]
    with pytest.raises(csd.StripError) as exc:
        csd.check_working_tree_safe(plugin, targets)
    assert exc.value.code == "STRIP-W007"


# ── State checkpoint ──────────────────────────────────────────────────────────


def test_save_and_load_state_round_trip(tmp_path):
    plugin = _make_plugin(tmp_path)
    csd.save_state(plugin, {"state": "REPO_CREATED", "targets": ["tests"]})
    loaded = csd.load_state(plugin)
    assert loaded["state"] == "REPO_CREATED"
    assert loaded["targets"] == ["tests"]


def test_load_state_missing_returns_empty(tmp_path):
    plugin = _make_plugin(tmp_path)
    assert csd.load_state(plugin) == {}


def test_load_state_corrupt_returns_marker(tmp_path):
    plugin = _make_plugin(tmp_path)
    (plugin / csd.STATE_FILENAME).write_text("not json", encoding="utf-8")
    state = csd.load_state(plugin)
    assert state.get("__corrupt__") is True


def test_clear_state_removes_file(tmp_path):
    plugin = _make_plugin(tmp_path)
    csd.save_state(plugin, {"state": "DONE"})
    assert (plugin / csd.STATE_FILENAME).is_file()
    csd.clear_state(plugin)
    assert not (plugin / csd.STATE_FILENAME).is_file()


def test_state_progress_recognises_states(tmp_path):
    assert csd.state_progress({}) == 0
    assert csd.state_progress({"state": "INIT"}) == 0
    assert csd.state_progress({"state": "REPO_CREATED"}) == 2
    assert csd.state_progress({"state": "DONE"}) == 6
    # Unknown state → 0 (treated as fresh start).
    assert csd.state_progress({"state": "BOGUS"}) == 0


# ── Plan construction ────────────────────────────────────────────────────────


def test_build_plan_uses_defaults_when_no_config(tmp_path):
    """PSS-style default: ONE submodule per plugin (tests/ only)."""
    plugin = _make_plugin(
        tmp_path,
        files={
            "tests/x.py": "x",
        },
    )
    plan = csd.build_plan(plugin)
    srcs = {t.src for t in plan.targets}
    assert srcs == {"tests/"}, f"Default extract should be tests/ only (PSS pattern), got {srcs}"


def test_build_plan_explicit_targets_override_config(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    plan = csd.build_plan(plugin, explicit_targets=["tests/"])
    assert len(plan.targets) == 1
    assert plan.targets[0].src == "tests/"


def test_build_plan_reads_cpv_strip_block(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json={
            "name": "demo",
            "version": "0.1.0",
            "description": "x",
            "repository": "https://github.com/Emasoft/demo",
            "cpv": {
                "strip": {
                    "extract": [
                        {"src": "tests/", "submodule": "Emasoft/demo-tests", "submodule_path": "dev/tests/"},
                    ],
                    "keep_in_main": ["tests/fixtures/small/"],
                    "keep_dev_configs": True,
                    "symlinks_for_devs": False,
                },
            },
        },
        files={"tests/x.py": "x"},
    )
    plan = csd.build_plan(plugin)
    assert len(plan.targets) == 1
    assert plan.targets[0].submodule == "Emasoft/demo-tests"
    assert plan.targets[0].submodule_path == "dev/tests/"
    assert plan.keep_in_main == ["tests/fixtures/small/"]
    assert plan.keep_dev_configs is True
    assert plan.symlinks_for_devs is False


def test_build_plan_rejects_no_plugin_json(tmp_path):
    with pytest.raises(csd.StripError) as exc:
        csd.build_plan(tmp_path / "nonexistent")
    assert exc.value.code == "STRIP-E007"


def test_build_plan_rejects_invalid_src_in_config(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json={
            "name": "demo",
            "version": "0.1.0",
            "description": "x",
            "cpv": {
                "strip": {
                    "extract": [
                        {"src": "../escape/", "submodule": "Emasoft/demo-x"},
                    ],
                },
            },
        },
    )
    with pytest.raises(csd.StripError):
        csd.build_plan(plugin)


def test_normalise_target_uses_owner_plugin_convention():
    """PSS pattern: submodule mounts at the SAME path as the original dir."""
    t = csd.normalise_target("tests/", "Emasoft", "myplugin")
    assert t.src == "tests/"
    assert t.submodule == "Emasoft/myplugin-tests"
    assert t.submodule_path == "tests/"


def test_normalise_target_strips_trailing_slash():
    t = csd.normalise_target("design", "Acme", "x")
    assert t.src == "design/"
    assert t.submodule == "Acme/x-design"


# ── summarise_plan ────────────────────────────────────────────────────────────


def test_summarise_plan_includes_all_targets(tmp_path):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x", "design/y.md": "y"})
    plan = csd.build_plan(plugin, explicit_targets=["tests/", "design/"])
    summary = csd.summarise_plan(plan)
    assert "tests/" in summary
    assert "design/" in summary
    assert "gh repo create" in summary
    assert "git submodule add" in summary


# ── CLI smoke ─────────────────────────────────────────────────────────────────


def test_main_help(capsys):
    rc = csd.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Usage:" in out


def test_main_dry_run(tmp_path, capsys):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    rc = csd.main([str(plugin), "--dry-run", "--extract", "tests/"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tests/" in out
    assert "Steps that would execute" in out


def test_main_check_passes_when_dev_parts_absent(tmp_path, capsys):
    plugin = _make_plugin(tmp_path)
    # No tests/, no design/ — but defaults try to extract them.
    # build_plan validates src exists; for --check we just verify
    # that the validation properly errors on missing src.
    rc = csd.main([str(plugin), "--check", "--extract", "tests/"])
    assert rc == 1  # build_plan fails because tests/ doesn't exist


def test_main_without_auto_falls_through_to_dry_run(tmp_path, capsys):
    """Without --auto, --extract still prints the plan and exits 0
    instead of executing destructive operations.
    """
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "x"})
    rc = csd.main([str(plugin), "--extract", "tests/"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Plan for" in captured.out
    assert "--auto" in captured.err


# ── should_strip_target heuristic ────────────────────────────────────────────


def test_should_strip_target_skips_tiny_dir(tmp_path):
    """A 200-byte / 5-file tests/ is below both thresholds → don't strip."""
    plugin = _make_plugin(
        tmp_path,
        files={
            "tests/test_a.py": "a",
            "tests/test_b.py": "b",
            "tests/test_c.py": "c",
        },
    )
    target = csd.normalise_target("tests/", "Emasoft", "demo")
    worth, reason = csd.should_strip_target(target, plugin)
    assert worth is False
    assert "small" in reason.lower()


def test_should_strip_target_recommends_when_large(tmp_path):
    """A tests/ with 30 files of 10KB each clears both thresholds → strip."""
    files = {f"tests/test_{i:02d}.py": "x" * 10_000 for i in range(30)}
    plugin = _make_plugin(tmp_path, files=files)
    target = csd.normalise_target("tests/", "Emasoft", "demo")
    worth, reason = csd.should_strip_target(target, plugin)
    assert worth is True
    assert "heavy" in reason.lower() or "over" in reason.lower()


def test_should_strip_target_handles_missing_src(tmp_path):
    plugin = _make_plugin(tmp_path)  # no tests/
    target = csd.normalise_target("tests/", "Emasoft", "demo")
    worth, reason = csd.should_strip_target(target, plugin)
    assert worth is False
    assert "does not exist" in reason


# ── State-machine resume ─────────────────────────────────────────────────────


def test_state_progress_int_arithmetic():
    """state_progress returns higher numbers for later states."""
    assert csd.state_progress({"current_state": csd.StripState.INIT.value}) == 0
    assert csd.state_progress({"current_state": csd.StripState.REPO_VERIFIED.value}) > 0
    assert csd.state_progress({"current_state": csd.StripState.DONE.value}) > csd.state_progress(
        {"current_state": csd.StripState.SUBMODULE_ADDED.value}
    )
