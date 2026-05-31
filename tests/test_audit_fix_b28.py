"""Regression tests for the full-audit batch-28 fixes.

Covers the five code-level findings fixed in the batch that owns:
  scripts/cpv_setup_auth.py, scripts/cpv_skill_scanner.py,
  scripts/cpv_validate_gitmodules.py, scripts/detect_language.py,
  scripts/manage_plugin.py, scripts/setup_branch_rules_generic.py,
  scripts/smart_exec.py

Each finding gets the corrected-behavior assertion plus a guard that would
have caught the original bug (and, for the security fix, the benign-stays-clean
side too). All filesystem/subprocess interaction is local or monkeypatched —
nothing here touches real GitHub or global git state.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_setup_auth as auth  # noqa: E402
import cpv_validate_gitmodules as cvg  # noqa: E402
import detect_language as dl  # noqa: E402
import setup_branch_rules_generic as sbrg  # noqa: E402
import smart_exec as se  # noqa: E402

# ── Finding 72: check_pre_push_hook honours a GLOBAL core.hooksPath ──────────


def test_pre_push_hook_detects_global_hookspath(tmp_path, monkeypatch):
    """A core.hooksPath set in GLOBAL scope must be detected (was local-only)."""
    hooks_dir = tmp_path / "global-hooks"
    hooks_dir.mkdir()
    (hooks_dir / "pre-push").write_text("#!/bin/sh\nexit 0\n")

    def fake_get(key: str, scope: str = "local") -> str:
        # Simulate: nothing local, but core.hooksPath set globally.
        if key == "core.hooksPath" and scope == "global":
            return str(hooks_dir)
        return ""

    monkeypatch.setattr(auth, "_git_config_get", fake_get)
    result = auth.check_pre_push_hook(plugin_root=tmp_path)
    assert result.status == auth.STATUS_SET
    assert str(hooks_dir) in result.detail


def test_pre_push_hook_guard_local_only_lookup_would_miss_global(tmp_path, monkeypatch):
    """Guard: had the check stayed local-only, the global hook would read as NOT SET.

    We assert that when the value is ONLY reachable via the global scope, the
    fixed code still finds it — i.e. the function does not ignore global scope.
    """
    hooks_dir = tmp_path / "gh"
    hooks_dir.mkdir()
    (hooks_dir / "pre-push").write_text("#!/bin/sh\n")
    seen_scopes: list[str] = []

    def fake_get(key: str, scope: str = "local") -> str:
        seen_scopes.append(scope)
        if key == "core.hooksPath" and scope == "global":
            return str(hooks_dir)
        return ""

    monkeypatch.setattr(auth, "_git_config_get", fake_get)
    result = auth.check_pre_push_hook(plugin_root=tmp_path)
    # The fixed code must have queried the global scope at all.
    assert "global" in seen_scopes
    assert result.status == auth.STATUS_SET


def test_pre_push_hook_local_overrides_global(tmp_path, monkeypatch):
    """Local core.hooksPath takes precedence over global (git semantics)."""
    local_dir = tmp_path / "local-hooks"
    local_dir.mkdir()
    (local_dir / "pre-push").write_text("#!/bin/sh\n")
    global_dir = tmp_path / "global-hooks"
    global_dir.mkdir()
    (global_dir / "pre-push").write_text("#!/bin/sh\n")

    def fake_get(key: str, scope: str = "local") -> str:
        if key == "core.hooksPath" and scope == "local":
            return str(local_dir)
        if key == "core.hooksPath" and scope == "global":
            return str(global_dir)
        return ""

    monkeypatch.setattr(auth, "_git_config_get", fake_get)
    result = auth.check_pre_push_hook(plugin_root=tmp_path)
    assert result.status == auth.STATUS_SET
    assert str(local_dir) in result.detail
    assert str(global_dir) not in result.detail


# ── Finding 74: gitmodules SSH `git@` userinfo accepted; creds still rejected ─


def test_gitmodules_accepts_ssh_git_user():
    """ssh:// and git+ssh:// with the canonical password-less git@ user now pass."""
    ok1, _ = cvg._validate_url_shape("ssh://git@github.com/owner/repo.git")
    ok2, _ = cvg._validate_url_shape("git+ssh://git@github.com/owner/repo.git")
    assert ok1 is True
    assert ok2 is True


def test_gitmodules_rejects_embedded_credentials():
    """Two-sided: a password/token in the URL is STILL rejected (security)."""
    for bad in (
        "ssh://git:secrettoken@github.com/o/r.git",
        "https://x-access-token:TOKEN@github.com/o/r.git",
        "https://attacker@github.com/Emasoft/x.git",
        "ssh://attacker@evil.com/x/y.git",
    ):
        ok, reason = cvg._validate_url_shape(bad)
        assert ok is False, f"expected rejection for {bad}"
        assert "user" in reason.lower()


def test_gitmodules_guard_only_git_user_allowed():
    """Guard: only the literal `git` SSH user is allowlisted — `gituser@` is not."""
    ok, _ = cvg._validate_url_shape("ssh://gituser@github.com/o/r.git")
    assert ok is False
    # And the allowed user constant is exactly "git".
    assert cvg._ALLOWED_SSH_USER == "git"


def test_gitmodules_allowed_schemes_now_usable_end_to_end():
    """The allowlisted ssh/git+ssh schemes are reachable (not dead) after the fix."""
    assert {"ssh", "git+ssh"} <= cvg._ALLOWED_SCHEMES
    # Both schemes' canonical form passes the full shape check.
    assert cvg._validate_url_shape("ssh://git@github.com/o/r.git")[0] is True
    assert cvg._validate_url_shape("git+ssh://git@github.com/o/r.git")[0] is True


# ── Finding 75: manage_plugin valid-set drops the unreachable exit 5 ──────────


def test_manage_plugin_valid_set_excludes_unreachable_five(monkeypatch):
    """_run_cpv_validation maps returncodes 0/3/4 -> valid, 1/2/5 -> not valid.

    validate_plugin.py never emits 5 (WARNING has no exit code), so 5 must NOT
    be treated as a passing/valid code. CRITICAL(1)/MAJOR(2) block install.
    """
    import manage_plugin as mp

    class _Result:
        def __init__(self, rc: int) -> None:
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    captured: dict[str, int] = {}

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return _Result(captured["rc"])

    monkeypatch.setattr(mp.subprocess, "run", fake_run)
    # Make the validate script appear present so the subprocess path runs.
    monkeypatch.setattr(mp.Path, "exists", lambda self: True)
    outcomes: dict[int, bool] = {}
    for rc in (0, 1, 2, 3, 4, 5):
        captured["rc"] = rc
        _, _, valid = mp._run_cpv_validation(Path("/tmp/x"), quiet=True)
        outcomes[rc] = valid

    assert outcomes[0] is True
    assert outcomes[3] is True
    assert outcomes[4] is True
    assert outcomes[1] is False
    assert outcomes[2] is False
    # The crux: a stray exit 5 is NOT silently treated as valid anymore.
    assert outcomes[5] is False


# ── Finding 153: detect_language budget counts only non-skipped source files ──


def test_detect_language_skips_vendored_without_charging_budget(tmp_path):
    """Real source past a huge vendored tree is found; budget is for source only."""
    nm = tmp_path / "node_modules" / "deep"
    nm.mkdir(parents=True)
    for i in range(50):
        (nm / f"v{i}.ts").write_text("x\n")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.ts").write_text("export const a = 1;\n")
    # A tiny limit that the vendored files would blow if they were counted.
    found = dl._has_any_source_file(tmp_path, ".ts", limit=5)
    assert found is not None
    assert found.name == "app.ts"


def test_detect_language_guard_order_independent(tmp_path, monkeypatch):
    """Guard: even if vendored files are yielded FIRST, the source is still found.

    This deterministically reproduces the original count-before-skip bug by
    forcing rglob to emit all skipped files before the real source.
    """
    nm = tmp_path / "node_modules"
    nm.mkdir()
    vendored = [nm / f"v{i}.ts" for i in range(100)]
    for p in vendored:
        p.write_text("x\n")
    real = tmp_path / "real.ts"
    real.write_text("export const a = 1;\n")

    # Force worst-case ordering: every skipped file before the one real file.
    ordered = [*vendored, real]
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: iter(ordered))

    # limit smaller than the vendored count: the OLD code would return None here.
    found = dl._has_any_source_file(tmp_path, ".ts", limit=10)
    assert found is not None
    assert found.name == "real.ts"


def test_detect_language_only_vendored_returns_none(tmp_path):
    """A tree of purely vendored source returns None (runaway guard intact)."""
    nm = tmp_path / ".venv" / "lib"
    nm.mkdir(parents=True)
    for i in range(20):
        (nm / f"g{i}.ts").write_text("x\n")
    assert dl._has_any_source_file(tmp_path, ".ts", limit=500) is None


# ── Finding 156: smart_exec 'direct' removed from PRIORITY['native'] ──────────


def test_smart_exec_native_priority_has_no_dead_direct_entry():
    """'direct' is unreachable inside the PRIORITY loop and must be absent."""
    assert "direct" not in se.PRIORITY["native"]
    # The other native executors stay.
    assert "docker" in se.PRIORITY["native"]
    assert "npx" in se.PRIORITY["native"]


def test_smart_exec_choose_best_still_prefers_direct_on_path(monkeypatch):
    """The direct path is preserved in choose_best (early-return), not lost."""
    spec = se.ToolSpec("mytool", "native", package="mytool", command="mytool")
    # Pretend the tool is already on PATH.
    monkeypatch.setattr(se, "have", lambda cmd: cmd == "mytool")
    argv, executor = se.choose_best(spec, ["--version"], {})
    assert executor == "direct"
    assert argv == ["mytool", "--version"]


def test_smart_exec_choose_best_falls_through_when_not_on_path(monkeypatch):
    """When the native tool is NOT on PATH, choose_best skips 'direct' cleanly.

    Guard: with 'direct' gone from PRIORITY['native'], a tool that is not on
    PATH and has no working executor raises RuntimeError rather than silently
    selecting an unreachable 'direct'.
    """
    spec = se.ToolSpec("nope-tool", "native", package="nope-tool", command="nope-tool")
    monkeypatch.setattr(se, "have", lambda cmd: False)
    try:
        se.choose_best(spec, [], {})
    except RuntimeError as exc:
        assert "No suitable executor" in str(exc)
    else:  # pragma: no cover - must raise
        raise AssertionError("expected RuntimeError when no executor is available")


# ── Finding 78: setup_branch_rules_generic.list_installed_apps multi-page ─────


def test_flatten_installations_handles_concatenated_pages():
    """The multi-page `gh api --paginate` stream (concatenated objects) flattens.

    Guard: the original `--jq '.installations'` produced one JSON array per page;
    json.loads() on the concatenation raised JSONDecodeError and a bare except
    silently dropped EVERY page. The new stream parser must recover all pages.
    """
    import json

    # Two object-pages back-to-back, exactly as gh --paginate (no --jq) emits.
    page1 = json.dumps({"total_count": 4, "installations": [{"app_id": 1}, {"app_id": 2}]})
    page2 = json.dumps({"total_count": 4, "installations": [{"app_id": 3}, {"app_id": 4}]})
    stream = page1 + "\n" + page2 + "\n"

    # Confirm the stream is NOT a single valid JSON document (the original trap).
    try:
        json.loads(stream)
        raise AssertionError("stream unexpectedly parsed as one document")
    except json.JSONDecodeError:
        pass

    apps = sbrg._flatten_installations_stream(stream)
    assert [a["app_id"] for a in apps] == [1, 2, 3, 4]


def test_flatten_installations_accepts_single_array_body():
    """A single bare array body (single-page / older shape) is also accepted."""
    import json

    stream = json.dumps([{"app_id": 9}, {"app_id": 10}])
    apps = sbrg._flatten_installations_stream(stream)
    assert [a["app_id"] for a in apps] == [9, 10]


def test_flatten_installations_empty_and_garbage_safe():
    """Empty / whitespace / trailing-garbage input never raises and never loops."""
    assert sbrg._flatten_installations_stream("") == []
    assert sbrg._flatten_installations_stream("   \n  ") == []
    # Valid first doc then garbage: take the valid doc, stop at the garbage.
    apps = sbrg._flatten_installations_stream('{"installations": [{"app_id": 1}]}\nnot-json')
    assert [a["app_id"] for a in apps] == [1]


def test_list_installed_apps_recovers_all_pages(monkeypatch):
    """End-to-end: >1 page of installs is fully recovered and deduplicated."""
    import json

    user_stream = (
        json.dumps({"total_count": 3, "installations": [{"app_id": 1}, {"app_id": 2}]})
        + "\n"
        + json.dumps({"total_count": 3, "installations": [{"app_id": 3}]})
        + "\n"
    )
    # Org page repeats app_id 3 (should be deduped) and adds 4.
    org_stream = json.dumps({"total_count": 2, "installations": [{"app_id": 3}, {"app_id": 4}]})

    class _R:
        def __init__(self, out: str) -> None:
            self.returncode = 0
            self.stdout = out
            self.stderr = ""

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        # No --jq should be passed anymore (that was the bug).
        assert "--jq" not in cmd, "list_installed_apps must not use --jq with --paginate"
        if "/user/installations" in cmd:
            return _R(user_stream)
        if "/orgs/acme/installations" in cmd:
            return _R(org_stream)
        return _R("")

    monkeypatch.setattr(sbrg, "run", fake_run)
    apps = sbrg.list_installed_apps("acme")
    ids = sorted(a["app_id"] for a in apps)
    assert ids == [1, 2, 3, 4]  # all pages, deduped (3 not doubled)
