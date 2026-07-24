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
    csd.save_state(plugin, {"state": "CONTENT_PUSHED", "targets": ["tests"]})
    loaded = csd.load_state(plugin)
    assert loaded["state"] == "CONTENT_PUSHED"
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
    # New _STATE_ORDER (REPO_CREATED removed): INIT=0, REPO_VERIFIED=1,
    # CONTENT_PUSHED=2, REFERENCE_RECORDED=3, COMMITTED=4, DONE=5.
    assert csd.state_progress({"state": "REPO_VERIFIED"}) == 1
    assert csd.state_progress({"state": "CONTENT_PUSHED"}) == 2
    assert csd.state_progress({"state": "DONE"}) == 5
    # The removed dead state is now unknown → 0 (treated as fresh start).
    assert csd.state_progress({"state": "REPO_CREATED"}) == 0
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
    # Clone-by-URL model: the preview records a reference + removes the dir,
    # and NEVER runs `git submodule add` / writes `.gitmodules`.
    assert "git rm" in summary
    assert "cpv.strip.extract" in summary
    assert "NO .gitmodules" in summary
    assert "git submodule add" not in summary


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
        {"current_state": csd.StripState.REFERENCE_RECORDED.value}
    )


# ── Clone-by-URL model: record schema, recording, restore ────────────────────
#
# The retarget replaces `git submodule add` (which SHIPS content because
# Claude Code recurses submodules at install) with a clone-by-URL model:
# the extracted dir is removed and a `{path, url, sha}` reference is recorded
# in cpv.strip.extract — NO .gitmodules is ever written.

_FAKE_SHA = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"  # 40-hex, matches _SHA40_RE


def _strip_manifest(records: list[dict]) -> dict:
    """A plugin.json dict carrying `cpv.strip.extract` = the given entries."""
    return {
        "name": "demo",
        "version": "0.1.0",
        "description": "x",
        "repository": "https://github.com/Emasoft/demo",
        "cpv": {"strip": {"extract": records, "require_url_allowlist": True}},
    }


def _make_source_repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    """Create a local git repo standing in for an extracted source repo.

    Returns (repo_path, head_sha). Restore clones this path (validation of
    the https-github URL shape is exercised separately in parse tests).
    """
    repo = tmp_path / "src-repo"
    repo.mkdir()
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "s@s.s"], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "S"], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "content"], capture_output=True, check=False)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, sha


# ── validate_extract_record / parse_extract_records — (c) reject malformed ────


def test_validate_extract_record_accepts_valid(tmp_path):
    plugin = _make_plugin(tmp_path)
    rec = csd.validate_extract_record(
        {"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA}, plugin
    )
    assert rec == csd.ExtractRecord(path="tests", url="https://github.com/Emasoft/demo-tests.git", sha=_FAKE_SHA)


def test_validate_extract_record_rejects_bad_sha(tmp_path):
    plugin = _make_plugin(tmp_path)
    with pytest.raises(csd.StripError) as exc:
        csd.validate_extract_record(
            {"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": "deadbeef"}, plugin
        )
    assert exc.value.code == "STRIP-R004"


def test_validate_extract_record_rejects_non_github_url(tmp_path):
    plugin = _make_plugin(tmp_path)
    with pytest.raises(csd.StripError) as exc:
        csd.validate_extract_record(
            {"path": "tests", "url": "https://evil.example.com/x/y.git", "sha": _FAKE_SHA}, plugin
        )
    assert exc.value.code == "STRIP-R005"


def test_validate_extract_record_rejects_url_traversal(tmp_path):
    plugin = _make_plugin(tmp_path)
    with pytest.raises(csd.StripError) as exc:
        csd.validate_extract_record(
            {"path": "tests", "url": "https://github.com/Emasoft/../evil.git", "sha": _FAKE_SHA}, plugin
        )
    assert exc.value.code == "STRIP-R005"


def test_validate_extract_record_rejects_userinfo_url(tmp_path):
    plugin = _make_plugin(tmp_path)
    with pytest.raises(csd.StripError) as exc:
        csd.validate_extract_record(
            {"path": "tests", "url": "https://evil@github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA}, plugin
        )
    assert exc.value.code == "STRIP-R005"


def test_validate_extract_record_rejects_reserved_path(tmp_path):
    plugin = _make_plugin(tmp_path)
    with pytest.raises(csd.StripError):
        csd.validate_extract_record(
            {"path": "scripts", "url": "https://github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA}, plugin
        )


def test_validate_extract_record_rejects_missing_path(tmp_path):
    plugin = _make_plugin(tmp_path)
    with pytest.raises(csd.StripError) as exc:
        csd.validate_extract_record({"url": "https://github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA}, plugin)
    assert exc.value.code == "STRIP-R002"


def test_parse_extract_records_reads_records_ignores_declarations(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json=_strip_manifest(
            [
                {"src": "design/", "submodule": "Emasoft/demo-design"},  # declaration → ignored
                {"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA},
            ]
        ),
    )
    records = csd.parse_extract_records(plugin)
    assert len(records) == 1
    assert records[0].path == "tests"


def test_parse_extract_records_rejects_malformed(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json=_strip_manifest(
            [{"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": "not-a-sha"}]
        ),
    )
    with pytest.raises(csd.StripError):
        csd.parse_extract_records(plugin)


# ── _record_extract_reference — upsert (drop declaration, keep other keys) ────


def test_record_extract_reference_upserts_and_preserves_keys(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json=_strip_manifest(
            [{"src": "tests/", "submodule": "Emasoft/demo-tests", "submodule_path": "tests/"}]
        ),
        files={"tests/x.py": "x"},
    )
    target = csd.normalise_target("tests/", "Emasoft", "demo")
    csd._record_extract_reference(plugin, target, _FAKE_SHA)
    pj = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
    extract = pj["cpv"]["strip"]["extract"]
    # The declaration is replaced by exactly one record.
    assert extract == [{"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA}]
    # Sibling strip keys are preserved (upsert never clobbers the block).
    assert pj["cpv"]["strip"]["require_url_allowlist"] is True


def test_record_extract_reference_is_idempotent(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json=_strip_manifest([{"src": "tests/", "submodule": "Emasoft/demo-tests", "submodule_path": "tests/"}]),
        files={"tests/x.py": "x"},
    )
    target = csd.normalise_target("tests/", "Emasoft", "demo")
    csd._record_extract_reference(plugin, target, _FAKE_SHA)
    csd._record_extract_reference(plugin, target, _FAKE_SHA)  # resume re-run
    pj = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
    assert len(pj["cpv"]["strip"]["extract"]) == 1


# ── apply_plan (network mocked) — (a) records written, NO .gitmodules ─────────


def test_apply_plan_writes_records_removes_dir_no_gitmodules(tmp_path, monkeypatch):
    plugin = _make_plugin(tmp_path, files={"tests/x.py": "print('x')\n"})
    plan = csd.build_plan(plugin, explicit_targets=["tests/"])
    # Mock the two network-touching steps (repo create + filter/push).
    monkeypatch.setattr(csd, "_ensure_repo_exists", lambda target, name: None)
    monkeypatch.setattr(csd, "_filter_and_push", lambda target, root, tmp: _FAKE_SHA)

    csd.apply_plan(plan)

    # (a) records written under cpv.strip.extract, no .gitmodules ever created.
    pj = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())
    assert pj["cpv"]["strip"]["extract"] == [
        {"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA}
    ]
    assert not (plugin / ".gitmodules").exists()
    # The extracted dir is removed from the tree entirely.
    assert not (plugin / "tests").exists()
    # A commit landed and the state file was cleared on success (clean tree).
    assert not (plugin / csd.STATE_FILENAME).exists()
    status = subprocess.run(
        ["git", "-C", str(plugin), "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert status == ""


# ── restore — (b) re-clone content from recorded url+sha ──────────────────────


def test_restore_record_clones_and_strips_git(tmp_path):
    src_repo, sha = _make_source_repo(tmp_path, {"test_a.py": "A\n", "sub/test_b.py": "B\n"})
    plugin = _make_plugin(tmp_path)  # no tests/ dir
    record = csd.ExtractRecord(path="tests", url=str(src_repo), sha=sha)
    csd._restore_record(record, plugin)
    # Content is re-materialised at the recorded path.
    assert (plugin / "tests" / "test_a.py").read_text() == "A\n"
    assert (plugin / "tests" / "sub" / "test_b.py").read_text() == "B\n"
    # The nested .git is removed → plain tree content, never an accidental gitlink.
    assert not (plugin / "tests" / ".git").exists()


def test_restore_record_refuses_nonempty_dest(tmp_path):
    src_repo, sha = _make_source_repo(tmp_path, {"a.py": "A"})
    plugin = _make_plugin(tmp_path, files={"tests/existing.py": "keep"})
    record = csd.ExtractRecord(path="tests", url=str(src_repo), sha=sha)
    with pytest.raises(csd.StripError) as exc:
        csd._restore_record(record, plugin)
    assert exc.value.code == "STRIP-R020"


def test_run_restore_calls_restore_for_each_record(tmp_path, monkeypatch):
    plugin = _make_plugin(
        tmp_path,
        plugin_json=_strip_manifest(
            [{"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA}]
        ),
    )
    called: list[str] = []
    monkeypatch.setattr(csd, "_restore_record", lambda rec, root: called.append(rec.path))
    rc = csd.run_restore(plugin)
    assert rc == 0
    assert called == ["tests"]


def test_run_restore_noop_when_no_records(tmp_path):
    plugin = _make_plugin(tmp_path)  # no cpv.strip block at all
    assert csd.run_restore(plugin) == 0


def test_run_restore_aborts_on_malformed_record(tmp_path):
    plugin = _make_plugin(
        tmp_path,
        plugin_json=_strip_manifest([{"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": "x"}]),
    )
    assert csd.run_restore(plugin) == 1


def test_restore_round_trip_via_run_restore(tmp_path, monkeypatch):
    """End-to-end (b): a recorded reference restores its content."""
    src_repo, sha = _make_source_repo(tmp_path, {"test_a.py": "A\n"})
    plugin = _make_plugin(
        tmp_path,
        plugin_json=_strip_manifest(
            [{"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA}]
        ),
    )
    # Redirect the (validated) record at the local source repo so the real
    # clone path runs offline; the loop + success reporting are exercised.
    monkeypatch.setattr(
        csd, "parse_extract_records", lambda root: [csd.ExtractRecord(path="tests", url=str(src_repo), sha=sha)]
    )
    assert csd.run_restore(plugin) == 0
    assert (plugin / "tests" / "test_a.py").read_text() == "A\n"
    assert not (plugin / "tests" / ".git").exists()


# ── (d) strip-dev'd plugin passes the .gitmodules URL-allowlist validator ─────


def test_stripped_plugin_passes_gitmodules_validator(tmp_path):
    """A strip-dev'd plugin (cpv.strip.extract records, NO .gitmodules) passes
    the URL-allowlist validator that validate_plugin + pre-push invoke — it is
    a no-op without a .gitmodules file, so nothing is rejected."""
    import cpv_validate_gitmodules as cvg  # noqa: PLC0415

    plugin = _make_plugin(
        tmp_path,
        plugin_json=_strip_manifest(
            [{"path": "tests", "url": "https://github.com/Emasoft/demo-tests.git", "sha": _FAKE_SHA}]
        ),
    )
    assert not (plugin / ".gitmodules").exists()
    assert cvg.parse_gitmodules_urls(plugin) == []
    assert cvg.validate_gitmodules(plugin) == []
