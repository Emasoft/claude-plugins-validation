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
    """print_gates prints all gates (0-13) with descriptions."""
    publish.print_gates()
    out = capsys.readouterr().out
    for i in range(14):
        assert f"Gate {i}:" in out
    assert "WARNING is the only severity" in out


# ── Pipeline order + git-cliff wiring ───────────────────────────────────────
# Pin the cornerstone contract: lint must precede tests, tests must precede
# validate, validate must precede bump, bump must precede changelog, and the
# changelog must be generated via `git-cliff --bump --unreleased --tag`. The
# template's pipeline is covered by TestPublishPyPipelineOrder in
# test_generate_plugin_repo.py — these tests cover CPV's OWN publish.py.


class TestCpvPublishPipelineOrder:
    """Pin the order of gates in CPV's own publish.py main() pipeline."""

    def test_gate_2_is_lint(self):
        """Gate 2 must be lint+typecheck (runs before tests on Gate 3)."""
        label, desc = publish.GATES[2]
        assert label == "Gate 2"
        assert "lint" in desc.lower()
        assert "typecheck" in desc.lower() or "mypy" in desc.lower()

    def test_gate_3_is_tests(self):
        """Gate 3 must be tests (runs after lint on Gate 2)."""
        label, desc = publish.GATES[3]
        assert label == "Gate 3"
        assert "test" in desc.lower() or "pytest" in desc.lower()

    def test_gate_4_is_plugin_validate(self):
        """Gate 4 must be plugin validation (runs after tests on Gate 3)."""
        label, desc = publish.GATES[4]
        assert label == "Gate 4"
        assert "validat" in desc.lower()

    def test_gate_8_is_bump(self):
        """Gate 8 must be the bump stage."""
        label, desc = publish.GATES[8]
        assert label == "Gate 8"
        assert "bump" in desc.lower()

    def test_gate_9_is_changelog_with_git_cliff_bump_unreleased(self):
        """Gate 9 must be the changelog stage, explicitly using git-cliff --bump --unreleased."""
        label, desc = publish.GATES[9]
        assert label == "Gate 9"
        assert "git-cliff" in desc.lower()
        assert "--bump" in desc
        assert "--unreleased" in desc
        assert "--tag" in desc

    def test_gates_10_to_13_run_commit_tag_push_release(self):
        """Gates 10-13 must run commit → tag → push → github release in that order."""
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
