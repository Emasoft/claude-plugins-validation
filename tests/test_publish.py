"""Tests for scripts/publish.py — the 14-gate publish pipeline.

Coverage:
- stage_validate_plugin blocks on CRITICAL/MAJOR/MINOR/NIT (4 tests)
- stage_validate_plugin allows WARNING (exit 0) to pass through (1 test)
- detect_layout() + stage_marketplace_registration_check for Layout A/B/none
  - Layout A missing notify-marketplace.yml → aborts
  - Layout A missing MARKETPLACE_PAT → aborts
  - Layout A missing plugin in remote mkt → aborts
  - Layout A missing receiver workflow → aborts
  - Layout B not at marketplace root → aborts
  - No-marketplace mode → WARNING + proceed

All `gh`/`git`/`subprocess` calls are stubbed via monkeypatch so the tests
never hit GitHub or modify the working tree.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_plugin_root(tmp_path: Path, name: str = "my-plugin", version: str = "1.0.0") -> Path:
    """Create a minimal Layout A plugin root with plugin.json and a notify workflow."""
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": version, "description": "x", "author": {"name": "t", "email": "t@e.com"}}),
        encoding="utf-8",
    )
    return root


def _make_notify_workflow(plugin_root: Path, owner: str = "Alice", repo: str = "mkt") -> Path:
    """Write a minimal notify-marketplace.yml with the given MARKETPLACE_OWNER/REPO."""
    wf_dir = plugin_root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf = wf_dir / "notify-marketplace.yml"
    wf.write_text(
        f"""name: Notify Marketplace
on:
  push:
    branches: [main]
env:
  MARKETPLACE_OWNER: '{owner}'
  MARKETPLACE_REPO: '{repo}'
jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: x
        run: echo x
""",
        encoding="utf-8",
    )
    return wf


def _make_marketplace_root(tmp_path: Path, plugin_name: str = "nested-plugin") -> tuple[Path, Path]:
    """Create a Layout B marketplace root with one nested plugin. Returns (mkt_root, nested_plugin_root)."""
    mkt_root = tmp_path / "my-marketplace"
    (mkt_root / ".claude-plugin").mkdir(parents=True)
    (mkt_root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "my-marketplace",
                "owner": {"name": "Alice", "email": "a@e.com"},
                "plugins": [{"name": plugin_name, "source": f"./plugins/{plugin_name}"}],
            }
        ),
        encoding="utf-8",
    )
    nested = mkt_root / "plugins" / plugin_name
    (nested / ".claude-plugin").mkdir(parents=True)
    (nested / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {"name": plugin_name, "version": "1.0.0", "description": "x", "author": {"name": "t", "email": "t@e.com"}}
        ),
        encoding="utf-8",
    )
    return mkt_root, nested


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Build a fake CompletedProcess for monkeypatching subprocess.run."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ── stage_validate_plugin tests ──────────────────────────────────────────────


def test_publish_blocks_on_critical_finding(monkeypatch, tmp_path, capsys):
    """Gate 4 aborts on CRITICAL (exit 1) before any bump happens."""
    root = _make_plugin_root(tmp_path)

    def fake_run(cmd, cwd, *, check=True, **kwargs):
        # Any call to validate_plugin returns code 1 (CRITICAL)
        if any("validate_plugin.py" in str(part) for part in cmd):
            return _completed(returncode=1)
        return _completed(returncode=0)

    monkeypatch.setattr(publish, "run", fake_run)
    rc = publish.stage_validate_plugin(root)
    assert rc == 1
    err = capsys.readouterr().err
    assert "CRITICAL" in err
    assert "PUBLISH BLOCKED" in err


def test_publish_blocks_on_major_finding(monkeypatch, tmp_path, capsys):
    """Gate 4 aborts on MAJOR (exit 2)."""
    root = _make_plugin_root(tmp_path)

    def fake_run(cmd, cwd, *, check=True, **kwargs):
        if any("validate_plugin.py" in str(part) for part in cmd):
            return _completed(returncode=2)
        return _completed(returncode=0)

    monkeypatch.setattr(publish, "run", fake_run)
    rc = publish.stage_validate_plugin(root)
    assert rc == 2
    assert "MAJOR" in capsys.readouterr().err


def test_publish_blocks_on_minor_finding(monkeypatch, tmp_path, capsys):
    """Gate 4 aborts on MINOR (exit 3)."""
    root = _make_plugin_root(tmp_path)

    def fake_run(cmd, cwd, *, check=True, **kwargs):
        if any("validate_plugin.py" in str(part) for part in cmd):
            return _completed(returncode=3)
        return _completed(returncode=0)

    monkeypatch.setattr(publish, "run", fake_run)
    rc = publish.stage_validate_plugin(root)
    assert rc == 3
    assert "MINOR" in capsys.readouterr().err


def test_publish_blocks_on_nit_finding(monkeypatch, tmp_path, capsys):
    """Gate 4 aborts on NIT (exit 4) — NIT is still a hard block, not advisory."""
    root = _make_plugin_root(tmp_path)

    def fake_run(cmd, cwd, *, check=True, **kwargs):
        if any("validate_plugin.py" in str(part) for part in cmd):
            return _completed(returncode=4)
        return _completed(returncode=0)

    monkeypatch.setattr(publish, "run", fake_run)
    rc = publish.stage_validate_plugin(root)
    assert rc == 4
    assert "NIT" in capsys.readouterr().err


def test_publish_allows_warning_only(monkeypatch, tmp_path, capsys):
    """Gate 4 passes when validator returns 0 (WARNING is advisory only, not a block)."""
    root = _make_plugin_root(tmp_path)

    def fake_run(cmd, cwd, *, check=True, **kwargs):
        # WARNINGs still yield exit 0 under --strict
        return _completed(returncode=0, stdout="WARNING: advisory only")

    monkeypatch.setattr(publish, "run", fake_run)
    rc = publish.stage_validate_plugin(root)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Plugin validation passed" in out


# ── Marketplace-registration tests (Task 2) ──────────────────────────────────


def test_marketplace_registration_no_marketplace_mode(monkeypatch, tmp_path, capsys):
    """No notify workflow + no parent marketplace → WARNING mode, proceed (rc=0)."""
    root = _make_plugin_root(tmp_path)
    # No workflow, no parent marketplace — should emit WARNING and return 0
    rc = publish.stage_marketplace_registration_check(root)
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "setup-marketplace-auto-notification" in out


def test_marketplace_registration_layout_a_missing_notify_workflow(monkeypatch, tmp_path, capsys):
    """Layout A without notify-marketplace.yml is treated as no-marketplace mode → WARNING, not abort.

    (Layout detection requires notify-marketplace.yml to claim Layout A. Without it,
    and without a parent marketplace.json, the plugin is in 'none' mode.)
    """
    root = _make_plugin_root(tmp_path)
    # Do NOT create notify-marketplace.yml
    rc = publish.stage_marketplace_registration_check(root)
    assert rc == 0
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "no marketplace registration found" in out


def test_marketplace_registration_layout_a_notify_workflow_missing_env_vars(monkeypatch, tmp_path, capsys):
    """Layout A with notify-marketplace.yml but no MARKETPLACE_OWNER/REPO should abort."""
    root = _make_plugin_root(tmp_path)
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "notify-marketplace.yml").write_text("name: broken\non: push\njobs: {}\n", encoding="utf-8")
    # Must not hit gh CLI for this path — we fail before that
    rc = publish.stage_marketplace_registration_check(root)
    # Either layout = 'A' with None owner → aborts with error
    # OR layout = 'A' and owner/repo not found → aborts
    assert rc == 1
    err = capsys.readouterr().err
    assert "MARKETPLACE_OWNER" in err or "notify-marketplace.yml" in err


def test_marketplace_registration_layout_a_missing_pat_secret(monkeypatch, tmp_path, capsys):
    """Layout A: notify workflow present, but `gh secret list` returns nothing → abort."""
    root = _make_plugin_root(tmp_path)
    _make_notify_workflow(root, owner="Alice", repo="mkt")

    # Stub shutil.which to pretend gh is installed
    monkeypatch.setattr(publish.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    # Stub _gh_secret_exists to return False (no MARKETPLACE_PAT)
    monkeypatch.setattr(publish, "_gh_secret_exists", lambda pr, name, gh_bin=None: False)

    rc = publish.stage_marketplace_registration_check(root)
    assert rc == 1
    err = capsys.readouterr().err
    assert "MARKETPLACE_PAT" in err
    assert "setup-marketplace-auto-notification" in err


def test_marketplace_registration_layout_a_plugin_not_in_remote_mkt(monkeypatch, tmp_path, capsys):
    """Layout A: secret exists but plugin is missing from remote marketplace.json → abort."""
    root = _make_plugin_root(tmp_path, name="my-plugin")
    _make_notify_workflow(root, owner="Alice", repo="mkt")

    monkeypatch.setattr(publish.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(publish, "_gh_secret_exists", lambda pr, name, gh_bin=None: True)
    # Remote marketplace.json does NOT list 'my-plugin'
    monkeypatch.setattr(
        publish,
        "_fetch_remote_marketplace_json",
        lambda owner, repo, gh_bin=None: {
            "name": "mkt",
            "owner": {"name": "Alice"},
            "plugins": [{"name": "other-plugin", "source": {"source": "github", "repo": "Alice/other-plugin"}}],
        },
    )
    monkeypatch.setattr(publish, "_current_repo_slug", lambda pr: "Alice/my-plugin")

    rc = publish.stage_marketplace_registration_check(root)
    assert rc == 1
    err = capsys.readouterr().err
    assert "not registered" in err
    assert "my-plugin" in err


def test_marketplace_registration_layout_a_missing_receiver_workflow(monkeypatch, tmp_path, capsys):
    """Layout A: plugin is registered but remote has no repository_dispatch workflow → abort."""
    root = _make_plugin_root(tmp_path, name="my-plugin")
    _make_notify_workflow(root, owner="Alice", repo="mkt")

    monkeypatch.setattr(publish.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(publish, "_gh_secret_exists", lambda pr, name, gh_bin=None: True)
    monkeypatch.setattr(
        publish,
        "_fetch_remote_marketplace_json",
        lambda owner, repo, gh_bin=None: {
            "plugins": [{"name": "my-plugin", "source": {"source": "github", "repo": "Alice/my-plugin"}}],
        },
    )
    monkeypatch.setattr(publish, "_current_repo_slug", lambda pr: "Alice/my-plugin")
    # No receiver workflow in remote
    monkeypatch.setattr(publish, "_remote_has_receiver_workflow", lambda owner, repo, gh_bin=None: False)

    rc = publish.stage_marketplace_registration_check(root)
    assert rc == 1
    err = capsys.readouterr().err
    assert "repository_dispatch" in err


def test_marketplace_registration_layout_a_all_checks_pass(monkeypatch, tmp_path, capsys):
    """Layout A: all checks pass → rc=0."""
    root = _make_plugin_root(tmp_path, name="my-plugin")
    _make_notify_workflow(root, owner="Alice", repo="mkt")

    monkeypatch.setattr(publish.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(publish, "_gh_secret_exists", lambda pr, name, gh_bin=None: True)
    monkeypatch.setattr(
        publish,
        "_fetch_remote_marketplace_json",
        lambda owner, repo, gh_bin=None: {
            "plugins": [{"name": "my-plugin", "source": {"source": "github", "repo": "Alice/my-plugin"}}],
        },
    )
    monkeypatch.setattr(publish, "_current_repo_slug", lambda pr: "Alice/my-plugin")
    monkeypatch.setattr(publish, "_remote_has_receiver_workflow", lambda owner, repo, gh_bin=None: True)

    rc = publish.stage_marketplace_registration_check(root)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Layout A marketplace registration verified" in out


def test_marketplace_registration_layout_b_not_at_marketplace_root(monkeypatch, tmp_path, capsys):
    """Layout B: publish.py running inside plugins/<name>/ subfolder aborts.

    Bumping a nested plugin independently would break the atomic marketplace tag.
    """
    mkt_root, nested = _make_marketplace_root(tmp_path, plugin_name="nested-plugin")
    # publish.py is running at the nested plugin subfolder — should abort
    rc = publish.stage_marketplace_registration_check(nested)
    assert rc == 1
    err = capsys.readouterr().err
    assert "Layout B" in err
    assert "MARKETPLACE repo root" in err
    assert "marketplace-layouts.md" in err


def test_marketplace_registration_layout_b_at_marketplace_root_success(monkeypatch, tmp_path, capsys):
    """Layout B: publish.py running at marketplace root with registered plugin → rc=0.

    Note: when publish.py is invoked at the marketplace root, the marketplace
    itself is not a plugin — detect_layout returns 'none' and we land in
    the no-marketplace WARNING path. (Actual marketplace releases go through
    validate_marketplace.py at Gate 5; the nested plugins are validated
    individually by the marketplace's own CI.)
    """
    mkt_root, _ = _make_marketplace_root(tmp_path, plugin_name="nested-plugin")
    # Add a plugin.json at the marketplace root itself so detect_layout
    # sees this as a plugin-with-marketplace-root combo. We simulate the
    # Layout B scenario where the marketplace root IS the plugin repo being released.
    (mkt_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "my-marketplace",
                "version": "1.0.0",
                "description": "x",
                "author": {"name": "t", "email": "t@e.com"},
            }
        ),
        encoding="utf-8",
    )

    rc = publish.stage_marketplace_registration_check(mkt_root)
    # At the marketplace root with no notify workflow and no *parent* marketplace,
    # we land in no-marketplace WARNING mode (rc=0) — this is expected for the
    # marketplace repo itself, which uses validate_marketplace.py at Gate 5.
    assert rc == 0
    out = capsys.readouterr().out
    # Marketplace root without a parent marketplace = no-marketplace mode
    assert "WARNING" in out or "verified" in out


def test_detect_layout_identifies_layout_a(tmp_path):
    """detect_layout returns 'A' when plugin has a notify-marketplace.yml."""
    root = _make_plugin_root(tmp_path)
    _make_notify_workflow(root, owner="Alice", repo="mkt")
    layout, details = publish.detect_layout(root)
    assert layout == "A"
    assert details["mkt_owner"] == "Alice"
    assert details["mkt_repo"] == "mkt"


def test_detect_layout_identifies_layout_b(tmp_path):
    """detect_layout returns 'B' when plugin is nested under plugins/<name>/ of a marketplace repo."""
    mkt_root, nested = _make_marketplace_root(tmp_path, plugin_name="nested-plugin")
    layout, details = publish.detect_layout(nested)
    assert layout == "B"
    mkt_root_from_details = details["marketplace_root"]
    assert mkt_root_from_details is not None
    assert Path(mkt_root_from_details).resolve() == mkt_root.resolve()
    assert details["plugin_name"] == "nested-plugin"


def test_detect_layout_returns_none_for_standalone(tmp_path):
    """detect_layout returns 'none' when plugin has no marketplace wiring."""
    root = _make_plugin_root(tmp_path)
    layout, details = publish.detect_layout(root)
    assert layout == "none"


def test_parse_notify_workflow_extracts_owner_repo(tmp_path):
    """_parse_notify_workflow pulls MARKETPLACE_OWNER and MARKETPLACE_REPO from YAML."""
    root = _make_plugin_root(tmp_path)
    wf = _make_notify_workflow(root, owner="Bob", repo="my-market")
    owner, repo = publish._parse_notify_workflow(wf)
    assert owner == "Bob"
    assert repo == "my-market"


def test_plugin_in_remote_marketplace_matches_github_source():
    """_plugin_in_remote_marketplace returns True for a matching github source entry."""
    mkt_json = {
        "plugins": [
            {"name": "foo", "source": {"source": "github", "repo": "Alice/foo"}},
            {"name": "bar", "source": {"source": "github", "repo": "Alice/bar"}},
        ]
    }
    assert publish._plugin_in_remote_marketplace(mkt_json, "foo", "Alice/foo") is True
    assert publish._plugin_in_remote_marketplace(mkt_json, "bar", "Alice/bar") is True
    assert publish._plugin_in_remote_marketplace(mkt_json, "baz", "Alice/baz") is False


def test_plugin_in_remote_marketplace_rejects_wrong_repo():
    """Returns False if the plugin name matches but repo slug doesn't."""
    mkt_json = {"plugins": [{"name": "foo", "source": {"source": "github", "repo": "Alice/foo"}}]}
    assert publish._plugin_in_remote_marketplace(mkt_json, "foo", "Bob/foo") is False


# ── Bypass guard test ────────────────────────────────────────────────────────


def test_bypass_guard_rejects_forbidden_env_vars(monkeypatch, capsys):
    """Gate 0 aborts when any CPV_SKIP_* or SKIP_* env var is set."""
    monkeypatch.setenv("CPV_SKIP_TESTS", "1")
    rc = publish.stage_bypass_guard()
    assert rc == 1
    err = capsys.readouterr().err
    assert "Bypass attempt detected" in err
    assert "CPV_SKIP_TESTS" in err


def test_bypass_guard_passes_with_clean_env(monkeypatch):
    """Gate 0 passes when no bypass env vars are set."""
    for v in [
        "CPV_SKIP_TESTS",
        "CPV_SKIP_LINT",
        "CPV_SKIP_VALIDATE",
        "CPV_FORCE_PUBLISH",
        "CPV_BYPASS_CHECKS",
        "SKIP_TESTS",
        "SKIP_LINT",
        "SKIP_VALIDATE",
        "NO_VERIFY",
    ]:
        monkeypatch.delenv(v, raising=False)
    rc = publish.stage_bypass_guard()
    assert rc == 0


def test_print_gates_lists_all_14_gates(capsys):
    """print_gates prints all gates (0-13) with descriptions.

    v2.64.0: Gate 2 (lint) was retired; lint moved into validate_plugin.py
    via cpv_lint_engine. Gates 3-14 renumbered to 2-13. Total = 14.
    Issue #18 added the integrity-manifest gate (now Gate 8).
    """
    publish.print_gates()
    out = capsys.readouterr().out
    for i in range(14):
        assert f"Gate {i}:" in out
    assert "WARNING is the only severity" in out


def test_gate_8_is_integrity_manifest_refresh():
    """Gate 8 must regenerate .plugin-self-hashes.json (issue #18 regression).

    Before this gate existed, every release between manifest refreshes
    shipped a stale manifest and fresh marketplace installs hit
    `cpv_integrity` ABORT immediately. Pin the gate's existence and
    label so it can't silently regress.

    v2.64.0: lint gate retired, gates renumbered — was Gate 9, now Gate 8.
    """
    label, desc = publish.GATES[8]
    assert label == "Gate 8"
    assert "self-hashes" in desc.lower() or "integrity" in desc.lower()
    assert "issue #18" in desc.lower()


def test_stage_refresh_self_hashes_skips_when_script_missing(monkeypatch, tmp_path, capsys):
    """When the compute script is absent (non-CPV plugins), the gate skips
    with a YELLOW warning instead of failing — other plugins generated by
    plugin-creator don't ship the integrity manifest. Per TRDD-bbff5bc5,
    the gate checks for the NEW script name first then falls back to the
    legacy name; if neither is present, the warning mentions both."""
    # tmp_path has no scripts/_plugin_compute_hashes.py and no legacy fallback
    rc = publish.stage_refresh_self_hashes(tmp_path)
    assert rc == 0
    err = capsys.readouterr().err
    assert "_plugin_compute_hashes.py not found" in err
    assert "legacy" in err.lower()


def test_stage_refresh_self_hashes_runs_new_script_when_present(monkeypatch, tmp_path):
    """When the new script exists, the gate invokes it via uv run."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "_plugin_compute_hashes.py").write_text("# placeholder", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, *, check=True, **kwargs):
        calls.append(list(cmd))
        return _completed(returncode=0)

    monkeypatch.setattr(publish, "run", fake_run)
    rc = publish.stage_refresh_self_hashes(tmp_path)
    assert rc == 0
    assert any("_plugin_compute_hashes.py" in str(arg) for c in calls for arg in c)


def test_stage_refresh_self_hashes_falls_back_to_legacy_script(monkeypatch, tmp_path):
    """When only the legacy compute_cpv_self_hashes.py exists, the gate
    falls back to it — preserves backward compat for plugins that haven't
    migrated to the new script name yet (TRDD-bbff5bc5 §6.3, 1-release
    deprecation window)."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "compute_cpv_self_hashes.py").write_text("# placeholder", encoding="utf-8")
    # Note: NO _plugin_compute_hashes.py
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, *, check=True, **kwargs):
        calls.append(list(cmd))
        return _completed(returncode=0)

    monkeypatch.setattr(publish, "run", fake_run)
    rc = publish.stage_refresh_self_hashes(tmp_path)
    assert rc == 0
    assert any("compute_cpv_self_hashes.py" in str(arg) for c in calls for arg in c)


# ── Pipeline order + git-cliff wiring ───────────────────────────────────────
# Pin the cornerstone contract: tests must precede validate (which now owns
# repo-wide lint via cpv_lint_engine since v2.64.0), validate must precede
# bump, bump must precede changelog, and the changelog must be generated via
# `git-cliff --bump --unreleased --tag`. The template's pipeline is covered
# by TestPublishPyPipelineOrder in test_generate_plugin_repo.py — these
# tests cover CPV's OWN publish.py.


class TestCpvPublishPipelineOrder:
    """Pin the order of gates in CPV's own publish.py main() pipeline."""

    def test_gate_2_is_tests(self):
        """Gate 2 must be tests (was Gate 3 before v2.64.0).

        v2.64.0 retired the standalone lint gate; lint moved into
        validate_plugin.py via cpv_lint_engine, so tests slid up to Gate 2.
        """
        label, desc = publish.GATES[2]
        assert label == "Gate 2"
        assert "test" in desc.lower() or "pytest" in desc.lower()

    def test_gate_3_is_plugin_validate(self):
        """Gate 3 must be plugin validation (owns repo-wide lint since v2.64.0)."""
        label, desc = publish.GATES[3]
        assert label == "Gate 3"
        assert "validat" in desc.lower()

    def test_gate_7_is_bump(self):
        """Gate 7 must be the bump stage (was Gate 8 before v2.64.0)."""
        label, desc = publish.GATES[7]
        assert label == "Gate 7"
        assert "bump" in desc.lower()

    def test_gate_9_is_changelog_with_git_cliff_bump_unreleased(self):
        """Gate 9 must be the changelog stage, explicitly using git-cliff --bump --unreleased.

        v2.64.0 retired the lint gate; the changelog gate moved from slot 10
        to slot 9.
        """
        label, desc = publish.GATES[9]
        assert label == "Gate 9"
        assert "git-cliff" in desc.lower()
        assert "--bump" in desc
        assert "--unreleased" in desc
        assert "--tag" in desc

    def test_gates_10_to_13_run_commit_tag_push_release(self):
        """Gates 10-13 must run commit → tag → push → github release in that order.

        v2.64.0 retired the lint gate; commit/tag/push/release shifted from
        slots 11-14 down to 10-13.
        """
        assert "commit" in publish.GATES[10][1].lower()
        assert "tag" in publish.GATES[11][1].lower()
        assert "push" in publish.GATES[12][1].lower()
        assert "release" in publish.GATES[13][1].lower()


class TestCpvStageChangelogUsesBumpUnreleased:
    """Pin that stage_changelog uses the git-cliff --bump --unreleased --tag pattern."""

    def test_stage_changelog_source_references_bump_unreleased_tag(self):
        """The stage_changelog function body must contain --bump, --unreleased, and --tag."""
        import inspect
        src = inspect.getsource(publish.stage_changelog)
        assert '"--bump"' in src or "'--bump'" in src
        assert '"--unreleased"' in src or "'--unreleased'" in src
        assert '"--tag"' in src or "'--tag'" in src


class TestCpvDetectBumpType:
    """Pin the behavior of publish.detect_bump_type — the auto-bump entry point.

    detect_bump_type shells out to `git-cliff --bumped-version` and compares
    the predicted version against the current one. On any failure it must
    fall back to 'patch' so the cornerstone rule (every push is a bump) is
    never violated.
    """

    def _fake_run(self, stdout: str = "", returncode: int = 0):
        """Return a completed-process-like mock for subprocess.run."""
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")

    def test_falls_back_to_patch_when_git_cliff_missing(self, monkeypatch, tmp_path):
        """detect_bump_type returns 'patch' when git-cliff is not on PATH."""
        monkeypatch.setattr(publish.shutil, "which", lambda _name: None)
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.0.0"}')
        assert publish.detect_bump_type(tmp_path) == "patch"

    def test_falls_back_to_patch_when_current_version_missing(self, monkeypatch, tmp_path):
        """detect_bump_type returns 'patch' if plugin.json is missing."""
        monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/git-cliff")
        # No plugin.json at all
        assert publish.detect_bump_type(tmp_path) == "patch"

    def test_falls_back_to_patch_on_git_cliff_nonzero_exit(self, monkeypatch, tmp_path):
        """detect_bump_type returns 'patch' if git-cliff exits non-zero."""
        monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/git-cliff")
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.0.0"}')
        monkeypatch.setattr(publish.subprocess, "run",
                            lambda *a, **k: self._fake_run(returncode=1))
        assert publish.detect_bump_type(tmp_path) == "patch"

    def test_detects_minor_bump(self, monkeypatch, tmp_path):
        """When git-cliff reports a minor-version bump, detect_bump_type returns 'minor'."""
        monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/git-cliff")
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.2.3"}')
        monkeypatch.setattr(publish.subprocess, "run",
                            lambda *a, **k: self._fake_run(stdout="v1.3.0\n"))
        assert publish.detect_bump_type(tmp_path) == "minor"

    def test_detects_major_bump(self, monkeypatch, tmp_path):
        """When git-cliff reports a major bump, detect_bump_type returns 'major'."""
        monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/git-cliff")
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.2.3"}')
        monkeypatch.setattr(publish.subprocess, "run",
                            lambda *a, **k: self._fake_run(stdout="2.0.0"))
        assert publish.detect_bump_type(tmp_path) == "major"

    def test_detects_patch_bump(self, monkeypatch, tmp_path):
        """When git-cliff reports a patch-version bump, detect_bump_type returns 'patch'."""
        monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/git-cliff")
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.2.3"}')
        monkeypatch.setattr(publish.subprocess, "run",
                            lambda *a, **k: self._fake_run(stdout="v1.2.4"))
        assert publish.detect_bump_type(tmp_path) == "patch"

    def test_strips_v_prefix_and_whitespace(self, monkeypatch, tmp_path):
        """detect_bump_type handles both 'v1.2.3' and '1.2.3' outputs with trailing whitespace."""
        monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/git-cliff")
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.0.0"}')
        monkeypatch.setattr(publish.subprocess, "run",
                            lambda *a, **k: self._fake_run(stdout="  v1.1.0  \n\n"))
        assert publish.detect_bump_type(tmp_path) == "minor"

    def test_falls_back_to_patch_on_malformed_version(self, monkeypatch, tmp_path):
        """detect_bump_type returns 'patch' when git-cliff returns a non-semver string."""
        monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/git-cliff")
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.0.0"}')
        monkeypatch.setattr(publish.subprocess, "run",
                            lambda *a, **k: self._fake_run(stdout="not-a-version"))
        assert publish.detect_bump_type(tmp_path) == "patch"

    def test_equal_versions_returns_patch(self, monkeypatch, tmp_path):
        """When git-cliff returns the current version unchanged, detect_bump_type returns 'patch'."""
        monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/git-cliff")
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.2.3"}')
        monkeypatch.setattr(publish.subprocess, "run",
                            lambda *a, **k: self._fake_run(stdout="v1.2.3"))
        assert publish.detect_bump_type(tmp_path) == "patch"


# ── stage_check_working_tree tests (regression for porcelain leading-space bug) ──


class TestStageCheckWorkingTree:
    """Gate 1 must parse `git status --porcelain` correctly.

    Regression: an earlier version called `.strip()` on the whole stdout
    BEFORE slicing each line at index 3. For unstaged-only changes git
    emits ' M filename' (leading space because column 0 is empty), and
    the outer strip removed that space, shifting the slice to start one
    character into the filename — so 'uv.lock' became 'v.lock' and the
    auto-commit branch never fired. Tests below pin both code paths.
    """

    def test_clean_tree_passes(self, monkeypatch, tmp_path, capsys):
        """No dirty lines → exit 0, no auto-commit."""
        monkeypatch.setattr(publish, "run", lambda *a, **k: _completed(stdout=""))
        rc = publish.stage_check_working_tree(tmp_path)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Working tree clean" in out

    def test_unstaged_uv_lock_only_triggers_auto_commit(self, monkeypatch, tmp_path, capsys):
        """' M uv.lock' (leading space, unstaged) must auto-commit, not block."""
        calls: list[list[str]] = []

        def fake_run(cmd, cwd, *, check=True, **kwargs):
            calls.append(list(cmd))
            if cmd[:3] == ["git", "status", "--porcelain"]:
                # NOTE the leading space — this is what git emits for
                # unstaged-only changes. The bug fixed in v2.49.0 was
                # that .strip() removed this space, making line[3:]
                # produce 'v.lock' instead of 'uv.lock'.
                return _completed(stdout=" M uv.lock\n")
            return _completed(returncode=0)

        monkeypatch.setattr(publish, "run", fake_run)
        rc = publish.stage_check_working_tree(tmp_path)
        assert rc == 0
        # auto-commit branch must have run `git add uv.lock` + `git commit`
        assert ["git", "add", "uv.lock"] in calls
        assert any(c[:2] == ["git", "commit"] for c in calls)
        out = capsys.readouterr().out
        assert "Auto-committing uv.lock" in out

    def test_staged_uv_lock_only_triggers_auto_commit(self, monkeypatch, tmp_path, capsys):
        """'M  uv.lock' (M then space, staged) must also auto-commit."""
        calls: list[list[str]] = []

        def fake_run(cmd, cwd, *, check=True, **kwargs):
            calls.append(list(cmd))
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return _completed(stdout="M  uv.lock\n")
            return _completed(returncode=0)

        monkeypatch.setattr(publish, "run", fake_run)
        rc = publish.stage_check_working_tree(tmp_path)
        assert rc == 0
        assert ["git", "add", "uv.lock"] in calls

    def test_other_dirty_file_blocks(self, monkeypatch, tmp_path, capsys):
        """Any file other than uv.lock dirty → exit 1, no auto-commit."""
        calls: list[list[str]] = []

        def fake_run(cmd, cwd, *, check=True, **kwargs):
            calls.append(list(cmd))
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return _completed(stdout=" M scripts/publish.py\n")
            return _completed(returncode=0)

        monkeypatch.setattr(publish, "run", fake_run)
        rc = publish.stage_check_working_tree(tmp_path)
        assert rc == 1
        # MUST NOT have auto-committed
        assert ["git", "add", "uv.lock"] not in calls
        err = capsys.readouterr().err
        assert "Uncommitted changes" in err

    def test_uv_lock_plus_other_blocks(self, monkeypatch, tmp_path, capsys):
        """uv.lock alone auto-commits, but uv.lock + any other file must block."""
        calls: list[list[str]] = []

        def fake_run(cmd, cwd, *, check=True, **kwargs):
            calls.append(list(cmd))
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return _completed(stdout=" M uv.lock\n M README.md\n")
            return _completed(returncode=0)

        monkeypatch.setattr(publish, "run", fake_run)
        rc = publish.stage_check_working_tree(tmp_path)
        assert rc == 1
        assert ["git", "add", "uv.lock"] not in calls

    def test_short_porcelain_line_is_skipped_safely(self, monkeypatch, tmp_path):
        """Defensive: malformed short line (<4 chars) must not crash on slice."""

        def fake_run(cmd, cwd, *, check=True, **kwargs):
            if cmd[:3] == ["git", "status", "--porcelain"]:
                return _completed(stdout="??\n M uv.lock\n")
            return _completed(returncode=0)

        monkeypatch.setattr(publish, "run", fake_run)
        # The "??" line is filtered (len < 4), only " M uv.lock" counts → auto-commit.
        rc = publish.stage_check_working_tree(tmp_path)
        assert rc == 0


# ── gh-auth precheck (TRDD-bbff5bc5 §5) ─────────────────────────────────────
# Pin the four documented failure modes of `_ensure_gh_auth()` and the
# wire-up into Gates 13/14. Each test stubs `subprocess.run` so we don't
# touch the real `gh` CLI or the network.


class TestEnsureGhAuth:
    """gh-auth precheck — TRDD-bbff5bc5 §4.1."""

    @staticmethod
    def _stub_subprocess(monkeypatch, status_rc=0, status_out="",
                         status_err="", perm_rc=0, perm_out="true"):
        """Replace subprocess.run inside publish module to feed gh outputs."""
        def fake_run(cmd, **kwargs):
            if not cmd:
                return _completed(returncode=0)
            # Match `gh auth status` and `gh api repos/.../permissions.push`.
            if "auth" in cmd and "status" in cmd:
                return _completed(returncode=status_rc, stdout=status_out, stderr=status_err)
            if "api" in cmd and "permissions.push" in " ".join(str(c) for c in cmd):
                return _completed(returncode=perm_rc, stdout=perm_out)
            return _completed(returncode=0)
        monkeypatch.setattr(publish.subprocess, "run", fake_run)

    def test_happy_path(self, monkeypatch):
        """gh installed, authed, has push perm → silent return (no exit)."""
        monkeypatch.setattr(publish.shutil, "which",
                            lambda name: "/usr/bin/gh" if name == "gh" else None)
        self._stub_subprocess(monkeypatch, status_rc=0, perm_rc=0, perm_out="true")
        publish._ensure_gh_auth("Emasoft", "claude-plugins-validation")  # no exception

    def test_fails_when_gh_missing(self, monkeypatch, capsys):
        """gh not on PATH → exit 1 with `brew install gh` hint."""
        monkeypatch.setattr(publish.shutil, "which", lambda name: None)
        with pytest.raises(SystemExit) as exc:
            publish._ensure_gh_auth("Emasoft", "claude-plugins-validation")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "gh CLI not installed" in err
        assert "brew install gh" in err

    def test_fails_when_unauthed(self, monkeypatch, capsys):
        """gh installed but `gh auth status` exits non-zero → exit 1 with
        `gh auth login` hint."""
        monkeypatch.setattr(publish.shutil, "which",
                            lambda name: "/usr/bin/gh" if name == "gh" else None)
        self._stub_subprocess(monkeypatch, status_rc=1)
        with pytest.raises(SystemExit) as exc:
            publish._ensure_gh_auth("Emasoft", "claude-plugins-validation")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "gh CLI not authenticated" in err
        assert "gh auth login" in err

    def test_fails_when_no_push_perm(self, monkeypatch, capsys):
        """gh authed but no push perm on owner/repo → exit 1 with switch
        hint."""
        monkeypatch.setattr(publish.shutil, "which",
                            lambda name: "/usr/bin/gh" if name == "gh" else None)
        self._stub_subprocess(
            monkeypatch,
            status_rc=0,
            status_out="github.com\n  ✓ Logged in to github.com account other-user",
            perm_rc=0,
            perm_out="false",
        )
        with pytest.raises(SystemExit) as exc:
            publish._ensure_gh_auth("Emasoft", "claude-plugins-validation")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "no push permission" in err
        assert "gh auth switch" in err

    def test_no_token_in_subprocess_calls(self, monkeypatch):
        """TRDD-bbff5bc5 §2.8 R6: precheck MUST NEVER invoke `gh auth token`
        (PAT non-leakage). Capture every subprocess invocation and assert
        none contain the token subcommand."""
        monkeypatch.setattr(publish.shutil, "which",
                            lambda name: "/usr/bin/gh" if name == "gh" else None)
        seen_cmds: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            seen_cmds.append([str(c) for c in cmd])
            if "auth" in cmd and "status" in cmd:
                return _completed(returncode=0)
            if "api" in cmd:
                return _completed(returncode=0, stdout="true")
            return _completed(returncode=0)

        monkeypatch.setattr(publish.subprocess, "run", fake_run)
        publish._ensure_gh_auth("Emasoft", "claude-plugins-validation")
        # Assert: NO `gh auth token` invocation anywhere.
        for c in seen_cmds:
            joined = " ".join(c)
            assert "auth token" not in joined, (
                f"PAT leakage risk: precheck invoked `gh auth token`: {joined}"
            )

    def test_parse_owner_repo_from_remote_handles_all_url_shapes(self):
        """Pin _parse_owner_repo_from_remote against every git URL shape
        we expect (TRDD-bbff5bc5 — owner/repo derived once at gate top)."""
        cases = {
            "git@github.com:Emasoft/claude-plugins-validation.git":
                ("Emasoft", "claude-plugins-validation"),
            "https://github.com/Emasoft/claude-plugins-validation.git":
                ("Emasoft", "claude-plugins-validation"),
            "https://github.com/Emasoft/claude-plugins-validation":
                ("Emasoft", "claude-plugins-validation"),
            "git@gitlab.com:org/repo.git": ("org", "repo"),
        }
        for url, expected in cases.items():
            assert publish._parse_owner_repo_from_remote(url) == expected
        assert publish._parse_owner_repo_from_remote("not-a-url") is None
        assert publish._parse_owner_repo_from_remote("") is None


class TestOrphanReleaseCommitRecovery:
    """Regression tests for task #151 — orphan chore(release) commit when
    a previous publish was interrupted between Gate 10 (commit) and Gate
    12 (push), and the re-run finds HEAD already has the release commit
    AND a dirty tree (from re-running Gate 8 / Gate 9).

    Before the fix: Gate 10 created a SECOND `chore(release): v<tag>`
    commit on top of the existing one, and Gate 11's "tag already exists"
    short-circuit kept the tag pointing at the OLDER commit. Result: the
    GitHub release was built from a stale tag.

    After the fix: Gate 10 detects the bad state, undoes the local-only
    chore commit (`git reset --soft HEAD~1`), drops the local-only tag,
    and creates ONE consolidated commit + tag at the new HEAD.
    """

    def test_remote_tag_exists_helper_handles_present_tag(self, monkeypatch, tmp_path):
        """`_remote_tag_exists` returns True when ls-remote shows the tag."""
        def fake_run(cmd, **_):
            assert cmd[:3] == ["git", "ls-remote", "--tags"]
            return _completed(returncode=0, stdout="abcd1234\trefs/tags/v1.0.0\n")
        monkeypatch.setattr(publish.subprocess, "run", fake_run)
        assert publish._remote_tag_exists(tmp_path, "v1.0.0") is True

    def test_remote_tag_exists_helper_returns_false_for_missing_tag(self, monkeypatch, tmp_path):
        """`_remote_tag_exists` returns False when ls-remote returns empty."""
        def fake_run(cmd, **_):
            return _completed(returncode=0, stdout="")
        monkeypatch.setattr(publish.subprocess, "run", fake_run)
        assert publish._remote_tag_exists(tmp_path, "v9.9.9") is False

    def test_remote_tag_exists_returns_false_on_timeout(self, monkeypatch, tmp_path):
        """Conservative: timeout → False so the recovery branch is skipped
        when we can't confirm the tag is local-only."""
        def fake_run(cmd, **_):
            raise publish.subprocess.TimeoutExpired(cmd, timeout=30)
        monkeypatch.setattr(publish.subprocess, "run", fake_run)
        assert publish._remote_tag_exists(tmp_path, "v1.0.0") is False

    def test_remote_tag_exists_returns_false_on_nonzero_exit(self, monkeypatch, tmp_path):
        """Non-zero exit (not-a-git-repo, network error) → False conservatively."""
        def fake_run(cmd, **_):
            return _completed(returncode=128, stdout="", stderr="not a git repo")
        monkeypatch.setattr(publish.subprocess, "run", fake_run)
        assert publish._remote_tag_exists(tmp_path, "v1.0.0") is False

    def test_stage_commit_recovery_when_dirty_and_unpushed(self, monkeypatch, tmp_path):
        """Recovery branch: HEAD has chore(release) commit, tree dirty,
        tag local-only → reset --soft HEAD~1 + drop local tag + ONE
        consolidated commit, then re-tag at HEAD in Gate 11.

        Asserts on the exact git command sequence so a future refactor
        cannot silently introduce the duplicate-commit bug again.
        """
        tag_name = "v1.0.0"
        expected_subject = f"chore(release): {tag_name}"
        commands: list[list[str]] = []
        # Stateful: tag is local-only at start; recovery's `git tag -d`
        # deletes it, so subsequent _local_tag_exists checks must return
        # False so Gate 11 re-creates it on the consolidated HEAD.
        local_tag_state = {"exists": True}

        def fake_run(cmd, cwd=None, *, check=True, env=None):
            commands.append(list(cmd))
            if cmd[:3] == ["git", "tag", "-d"]:
                local_tag_state["exists"] = False
            return _completed(returncode=0)

        def fake_git_with_retry(cmd, cwd=None, env=None, capture_output=False):
            commands.append(list(cmd))
            return _completed(returncode=0)

        monkeypatch.setattr(publish, "run", fake_run)
        monkeypatch.setattr(publish, "git_with_retry", fake_git_with_retry)
        monkeypatch.setattr(publish, "_head_commit_message", lambda _root: expected_subject)
        monkeypatch.setattr(publish, "_git_porcelain_clean", lambda _root: False)
        monkeypatch.setattr(publish, "_remote_tag_exists", lambda _root, _tag: False)
        monkeypatch.setattr(publish, "_local_tag_exists", lambda _root, _tag: local_tag_state["exists"])
        monkeypatch.setattr(publish, "_resolve_owner_repo", lambda _root: ("Alice", "repo"))
        monkeypatch.setattr(publish, "_ensure_gh_auth", lambda _o, _r: None)

        rc = publish.stage_commit_tag_push(tmp_path, tag_name)
        assert rc == 0

        # Recovery sequence: reset → drop local tag → add → commit → tag → push branch → push tag
        assert ["git", "reset", "--soft", "HEAD~1"] in commands
        assert ["git", "tag", "-d", tag_name] in commands
        # Exactly ONE commit with the release subject (not two).
        commit_cmds = [c for c in commands if c[:2] == ["git", "commit"] and "-m" in c]
        assert len(commit_cmds) == 1, f"expected 1 commit, got {len(commit_cmds)}: {commit_cmds}"
        assert expected_subject in commit_cmds[0]
        # New annotated tag created on the consolidated HEAD.
        tag_cmds = [c for c in commands if c[:3] == ["git", "tag", "-a"]]
        assert len(tag_cmds) == 1
        assert tag_cmds[0][3] == tag_name

    def test_stage_commit_normal_path_when_clean_and_no_chore_head(self, monkeypatch, tmp_path):
        """Sanity: non-recovery path still works — clean tree, no prior
        chore commit → standard add + commit + tag + push."""
        tag_name = "v1.0.0"
        commands: list[list[str]] = []

        def fake_run(cmd, **_kw):
            commands.append(list(cmd))
            return _completed(returncode=0)

        monkeypatch.setattr(publish, "run", fake_run)
        monkeypatch.setattr(publish, "git_with_retry", lambda cmd, **_kw: commands.append(list(cmd)) or _completed(returncode=0))
        monkeypatch.setattr(publish, "_head_commit_message", lambda _root: "feat: unrelated")
        monkeypatch.setattr(publish, "_git_porcelain_clean", lambda _root: False)
        monkeypatch.setattr(publish, "_local_tag_exists", lambda _root, _tag: False)
        monkeypatch.setattr(publish, "_remote_tag_exists", lambda _root, _tag: False)
        monkeypatch.setattr(publish, "_resolve_owner_repo", lambda _root: ("Alice", "repo"))
        monkeypatch.setattr(publish, "_ensure_gh_auth", lambda _o, _r: None)

        rc = publish.stage_commit_tag_push(tmp_path, tag_name)
        assert rc == 0
        # Normal path: NO reset; ONE commit; ONE tag.
        assert ["git", "reset", "--soft", "HEAD~1"] not in commands
        commit_cmds = [c for c in commands if c[:2] == ["git", "commit"] and "-m" in c]
        assert len(commit_cmds) == 1


class TestSubmodulePushGate:
    """TRDD-793ac32a Sprint 2 — submodule push gate.

    Before the parent's `git push origin HEAD`, every submodule's
    currently-checked-out SHA (as recorded in the parent's index) must be
    reachable on the submodule's `origin` remote. If not, the parent
    push would create a broken install: anyone cloning the parent with
    `--recurse-submodules` would fail at submodule init because the
    gitlinked SHA only exists in the maintainer's local clone.

    These tests stub `subprocess.run` inside the publish module so we
    never spawn real git processes or hit a network. Each test builds the
    minimum on-disk state needed for the function under test:

      - test_no_gitmodules_noop:                no .gitmodules → no-op
      - test_submodule_reachable_passes:        one reachable submodule
      - test_submodule_unreachable_fails:       one unreachable submodule
      - test_multiple_submodules_one_unreachable: 3 submodules, one bad
      - test_actionable_message_includes_push_command: error UX shape
    """

    @staticmethod
    def _make_submodule_workdir(plugin_root: Path, sub_path: str) -> Path:
        """Create the on-disk shape `_ensure_submodules_pushed` probes:
        a `<plugin_root>/<sub_path>/.git` marker file. The function checks
        `(sub_root / ".git").exists()` to decide whether the submodule is
        initialized; we only need it to return True. Real git would have
        `.git` as a gitdir-pointer file, but a plain file works for the
        existence check."""
        sub_root = plugin_root / sub_path
        sub_root.mkdir(parents=True, exist_ok=True)
        (sub_root / ".git").write_text(
            "gitdir: ../.git/modules/" + sub_path, encoding="utf-8"
        )
        return sub_root

    @staticmethod
    def _stub_subprocess(monkeypatch, *, status_lines, branch_r_map):
        """Stub publish.subprocess.run for the two commands the gate uses.

        - `git submodule status` → returns one line per entry in
          ``status_lines`` (already including the leading status marker).
        - `git -C <sub> branch -r --contains <sha>` → consults
          ``branch_r_map`` keyed by ``(sub_path, sha)``. Value is the
          stdout the call should return; empty string ⇒ unreachable.

        Any other subprocess call returns rc=0 / empty so we don't have
        to enumerate every call site of subprocess.run inside publish.py.
        """
        def fake_run(cmd, **kwargs):
            cmd_list = list(cmd)
            if cmd_list[:2] == ["git", "submodule"] and "status" in cmd_list:
                return _completed(
                    returncode=0,
                    stdout="\n".join(status_lines) + ("\n" if status_lines else ""),
                )
            # Match `git -C <sub_root> branch -r --contains <sha>`.
            if (
                len(cmd_list) >= 7
                and cmd_list[0] == "git"
                and cmd_list[1] == "-C"
                and cmd_list[3:6] == ["branch", "-r", "--contains"]
            ):
                sub_root_str = cmd_list[2]
                sha = cmd_list[6]
                # Resolve sub_path from sub_root_str: it's the basename
                # under plugin_root, but for portability the test keys
                # branch_r_map by the trailing path component(s) the test
                # used in ``status_lines``. Look up by sha first; fall
                # back to the path tail.
                # Try (path_tail, sha) for every key prefix-matching sub_root_str.
                for (path_key, sha_key), stdout in branch_r_map.items():
                    if sha_key == sha and sub_root_str.endswith(path_key):
                        return _completed(returncode=0, stdout=stdout)
                # Default: unreachable.
                return _completed(returncode=0, stdout="")
            return _completed(returncode=0)
        monkeypatch.setattr(publish.subprocess, "run", fake_run)

    def test_no_gitmodules_noop(self, monkeypatch, tmp_path):
        """Plugin root has no .gitmodules → gate returns cleanly with no
        subprocess calls beyond returning early. No SystemExit."""
        # No .gitmodules file.
        called: list[list[str]] = []

        def fake_run(cmd, **_kw):
            called.append(list(cmd))
            return _completed(returncode=0)

        monkeypatch.setattr(publish.subprocess, "run", fake_run)
        publish._ensure_submodules_pushed(tmp_path)  # no exception
        # Early return: subprocess.run must NOT have been invoked.
        assert called == [], (
            f"Expected zero subprocess calls when .gitmodules absent, got: {called}"
        )

    def test_submodule_reachable_passes(self, monkeypatch, tmp_path):
        """One submodule, SHA reachable on origin → silent return."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "tests"]\n\tpath = tests\n\turl = https://github.com/x/cpv-tests.git\n',
            encoding="utf-8",
        )
        self._make_submodule_workdir(tmp_path, "tests")
        self._stub_subprocess(
            monkeypatch,
            status_lines=[" abcdef1234567890abcdef1234567890abcdef12 tests (heads/main)"],
            branch_r_map={
                ("tests", "abcdef1234567890abcdef1234567890abcdef12"):
                    "  origin/main\n",
            },
        )
        publish._ensure_submodules_pushed(tmp_path)  # no exception

    def test_submodule_unreachable_fails(self, monkeypatch, tmp_path, capsys):
        """One submodule, SHA NOT reachable on origin → exit 1 with an
        actionable error message naming the submodule path AND the exact
        push command."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "tests"]\n\tpath = tests\n\turl = https://github.com/x/cpv-tests.git\n',
            encoding="utf-8",
        )
        self._make_submodule_workdir(tmp_path, "tests")
        # `branch -r --contains <sha>` returns empty → unreachable.
        self._stub_subprocess(
            monkeypatch,
            status_lines=[" deadbeefdeadbeefdeadbeefdeadbeefdeadbeef tests (heads/main)"],
            branch_r_map={
                ("tests", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"): "",
            },
        )
        with pytest.raises(SystemExit) as exc:
            publish._ensure_submodules_pushed(tmp_path)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "tests" in err
        assert "deadbeef" in err  # short SHA in message
        assert "git push origin HEAD" in err

    def test_multiple_submodules_one_unreachable(self, monkeypatch, tmp_path, capsys):
        """3 submodules, one is unreachable → fails with the offending
        path named in the error, and the OTHER two submodules are NOT in
        the error block (they're reachable)."""
        gm = []
        for sub in ("tests", "design", "git-hooks"):
            gm.append(
                f'[submodule "{sub}"]\n\tpath = {sub}\n\turl = https://github.com/x/{sub}.git\n'
            )
            self._make_submodule_workdir(tmp_path, sub)
        (tmp_path / ".gitmodules").write_text("".join(gm), encoding="utf-8")

        sha_tests = "1111111111111111111111111111111111111111"
        sha_design = "2222222222222222222222222222222222222222"
        sha_hooks = "3333333333333333333333333333333333333333"
        self._stub_subprocess(
            monkeypatch,
            status_lines=[
                f" {sha_tests} tests (heads/main)",
                f" {sha_design} design (heads/main)",
                f" {sha_hooks} git-hooks (heads/main)",
            ],
            branch_r_map={
                ("tests", sha_tests): "  origin/main\n",
                # design is the bad one — empty stdout → unreachable.
                ("design", sha_design): "",
                ("git-hooks", sha_hooks): "  origin/main\n",
            },
        )
        with pytest.raises(SystemExit) as exc:
            publish._ensure_submodules_pushed(tmp_path)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        # The unreachable submodule must be named.
        assert "design" in err
        # And its short SHA must appear.
        assert "22222222" in err
        # The fix command for that path.
        assert "cd design && git push origin HEAD" in err
        # The two reachable submodules MUST NOT appear in any
        # bullet/line of the error block (i.e. must not be flagged
        # alongside the bad one).
        # We check the bullet form to be precise — the word "tests" or
        # "git-hooks" can appear as part of a heading or generic prose.
        assert "submodule 'tests'" not in err
        assert "submodule 'git-hooks'" not in err

    def test_actionable_message_includes_push_command(self, monkeypatch, tmp_path, capsys):
        """The failure message MUST contain `git push origin HEAD` for
        the offending submodule path. This is the contract the TRDD
        promises to maintainers."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "vendor/foo"]\n\tpath = vendor/foo\n\turl = https://example.com/foo.git\n',
            encoding="utf-8",
        )
        self._make_submodule_workdir(tmp_path, "vendor/foo")
        sha = "cafebabecafebabecafebabecafebabecafebabe"
        self._stub_subprocess(
            monkeypatch,
            status_lines=[f" {sha} vendor/foo (heads/main)"],
            branch_r_map={("vendor/foo", sha): ""},
        )
        with pytest.raises(SystemExit):
            publish._ensure_submodules_pushed(tmp_path)
        err = capsys.readouterr().err
        # Exact contract: the message includes the path + the push
        # command in a form a maintainer can paste into their shell.
        assert "cd vendor/foo && git push origin HEAD" in err

    def test_uninitialized_submodule_reported_as_unreachable(self, monkeypatch, tmp_path, capsys):
        """A submodule whose `.git` marker is absent (never `submodule
        update --init`'d) cannot be probed without side effects. The
        gate treats it as unreachable so the maintainer initializes it
        themselves before re-running publish."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "tests"]\n\tpath = tests\n\turl = https://github.com/x/cpv-tests.git\n',
            encoding="utf-8",
        )
        # NOTE: deliberately NOT calling _make_submodule_workdir, so
        # `<plugin_root>/tests/.git` does not exist.
        self._stub_subprocess(
            monkeypatch,
            status_lines=[" 5555555555555555555555555555555555555555 tests (heads/main)"],
            branch_r_map={},  # no entries — tests should be flagged unreachable
        )
        with pytest.raises(SystemExit) as exc:
            publish._ensure_submodules_pushed(tmp_path)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "tests" in err
        assert "git push origin HEAD" in err

    def test_empty_gitmodules_file_noop(self, monkeypatch, tmp_path):
        """`.gitmodules` exists but `git submodule status` returns no
        lines → gate is a clean no-op (degenerate but legal state)."""
        (tmp_path / ".gitmodules").write_text("", encoding="utf-8")
        self._stub_subprocess(
            monkeypatch,
            status_lines=[],
            branch_r_map={},
        )
        publish._ensure_submodules_pushed(tmp_path)  # no exception

    def test_submodule_status_failure_aborts(self, monkeypatch, tmp_path, capsys):
        """`git submodule status` exiting non-zero (corrupt config,
        not-a-git-repo, etc.) aborts publish with stderr message — we
        do NOT silently let publish proceed because that would also
        break submodule consumers."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "tests"]\n\tpath = tests\n\turl = https://github.com/x/cpv-tests.git\n',
            encoding="utf-8",
        )

        def fake_run(cmd, **_kw):
            if list(cmd)[:2] == ["git", "submodule"]:
                return _completed(returncode=128, stdout="", stderr="not a git repo")
            return _completed(returncode=0)

        monkeypatch.setattr(publish.subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc:
            publish._ensure_submodules_pushed(tmp_path)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "git submodule status" in err

    def test_submodule_status_timeout_aborts(self, monkeypatch, tmp_path, capsys):
        """`git submodule status` timing out aborts publish — we cannot
        verify reachability under a timeout, so we fail closed."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "tests"]\n\tpath = tests\n\turl = https://github.com/x/cpv-tests.git\n',
            encoding="utf-8",
        )

        def fake_run(cmd, **_kw):
            if list(cmd)[:2] == ["git", "submodule"]:
                raise publish.subprocess.TimeoutExpired(cmd, timeout=60)
            return _completed(returncode=0)

        monkeypatch.setattr(publish.subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc:
            publish._ensure_submodules_pushed(tmp_path)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "timed out" in err

    def test_branch_r_timeout_treated_as_unreachable(self, monkeypatch, tmp_path, capsys):
        """`git branch -r --contains` timing out per-submodule is treated
        as unreachable (conservative). The submodule is named in the
        error so the maintainer knows which one to inspect."""
        (tmp_path / ".gitmodules").write_text(
            '[submodule "tests"]\n\tpath = tests\n\turl = https://github.com/x/cpv-tests.git\n',
            encoding="utf-8",
        )
        self._make_submodule_workdir(tmp_path, "tests")

        def fake_run(cmd, **_kw):
            cmd_list = list(cmd)
            if cmd_list[:2] == ["git", "submodule"] and "status" in cmd_list:
                return _completed(
                    returncode=0,
                    stdout=" abcdef1234567890abcdef1234567890abcdef12 tests (heads/main)\n",
                )
            if (
                len(cmd_list) >= 7
                and cmd_list[0] == "git"
                and cmd_list[1] == "-C"
                and cmd_list[3:6] == ["branch", "-r", "--contains"]
            ):
                raise publish.subprocess.TimeoutExpired(cmd, timeout=60)
            return _completed(returncode=0)

        monkeypatch.setattr(publish.subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc:
            publish._ensure_submodules_pushed(tmp_path)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "tests" in err
        assert "git push origin HEAD" in err

    def test_gate_is_called_in_stage_commit_tag_push_after_gh_auth(self, monkeypatch, tmp_path):
        """Wire-up test: the gate runs BEFORE the parent push and AFTER
        gh-auth precheck. We capture the call order via a sentinel list
        so a future refactor that reorders these gates breaks this test."""
        tag_name = "v1.0.0"
        call_order: list[str] = []

        def fake_run(cmd, **_kw):
            return _completed(returncode=0)

        def fake_git_with_retry(cmd, **_kw):
            call_order.append("git_push:" + " ".join(cmd[1:]))
            return _completed(returncode=0)

        monkeypatch.setattr(publish, "run", fake_run)
        monkeypatch.setattr(publish, "git_with_retry", fake_git_with_retry)
        monkeypatch.setattr(publish, "_head_commit_message", lambda _root: "feat: x")
        monkeypatch.setattr(publish, "_git_porcelain_clean", lambda _root: False)
        monkeypatch.setattr(publish, "_local_tag_exists", lambda _root, _tag: False)
        monkeypatch.setattr(publish, "_remote_tag_exists", lambda _root, _tag: False)
        monkeypatch.setattr(publish, "_resolve_owner_repo", lambda _root: ("Alice", "repo"))
        monkeypatch.setattr(publish, "_ensure_gh_auth", lambda _o, _r: call_order.append("gh_auth"))
        monkeypatch.setattr(publish, "_ensure_submodules_pushed", lambda _root: call_order.append("submodule_gate"))

        rc = publish.stage_commit_tag_push(tmp_path, tag_name)
        assert rc == 0

        # Order MUST be: gh_auth → submodule_gate → first git push.
        gh_idx = call_order.index("gh_auth")
        sub_idx = call_order.index("submodule_gate")
        first_push_idx = next(i for i, name in enumerate(call_order) if name.startswith("git_push:"))
        assert gh_idx < sub_idx < first_push_idx, (
            f"Expected gh_auth → submodule_gate → push, got: {call_order}"
        )
