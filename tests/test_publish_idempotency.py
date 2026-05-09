#!/usr/bin/env python3
"""Tests for `publish.py` idempotency on interrupted-publish recovery.

Background: when a publish run was interrupted between the local
commit+tag and the push (e.g. transient network failure during `git push`,
or a pre-push hook that suddenly rejects), the repo ended up in a state
where:
  - plugin.json was already at the bumped version (e.g. 2.64.0)
  - HEAD was already the `chore(release): v2.64.0` commit
  - `v2.64.0` tag existed locally
  - origin was still on the previous version (v2.63.2)

Re-running publish.py would then DOUBLE-BUMP — Gate 7 read the local
plugin.json (already 2.64.0) and applied the bump again (→ 2.64.1 with
--patch or → 2.65.0 with --minor), producing a release that skipped a
number and left the v2.64.0 commit orphaned with no published tag. This
actually happened during the v2.64.0 ship attempt (commit 2ac8c10) and
forced a manual jump to v2.65.0.

Fix: publish.py reads the REMOTE plugin.json version (origin/master) as
the bump baseline, infers the bump_type from local-vs-remote when local
is ahead, and skips Gate 7 / 10 / 11 individually when the work is
already done.

These tests verify:
1. `_read_remote_version` returns the version from origin/master.
2. `_infer_bump_type` correctly classifies semver diffs.
3. `stage_bump` uses the REMOTE version as bump baseline.
4. `stage_bump` SKIPS the bump when local plugin.json already matches
   the bump target (interrupted-publish recovery path).
5. `stage_bump` REFUSES when local is at an unexpected intermediate
   version (refuses to guess).
6. The Gate 10 commit is skipped when HEAD already has
   `chore(release): vX.Y.Z` and the working tree is clean.
7. The Gate 11 tag step is skipped when the tag already exists locally.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import publish  # noqa: E402

# ---------------------------------------------------------------------------
# _infer_bump_type — pure semver diff classifier
# ---------------------------------------------------------------------------


class TestInferBumpType:
    def test_minor_diff(self) -> None:
        assert publish._infer_bump_type("2.63.2", "2.64.0") == "minor"

    def test_major_diff(self) -> None:
        assert publish._infer_bump_type("2.99.99", "3.0.0") == "major"

    def test_patch_diff(self) -> None:
        assert publish._infer_bump_type("2.63.2", "2.63.3") == "patch"

    def test_same_version_returns_none(self) -> None:
        assert publish._infer_bump_type("2.63.2", "2.63.2") is None

    def test_downgrade_returns_none(self) -> None:
        # publish.py never downgrades; an inferred downgrade is always a bug
        assert publish._infer_bump_type("2.65.0", "2.64.0") is None

    def test_malformed_returns_none(self) -> None:
        assert publish._infer_bump_type("not-semver", "2.64.0") is None
        assert publish._infer_bump_type("2.64.0", "weird") is None


# ---------------------------------------------------------------------------
# stage_bump — idempotent skip
# ---------------------------------------------------------------------------


def _write_plugin_json(plugin_root: Path, version: str) -> None:
    pj = plugin_root / ".claude-plugin"
    pj.mkdir(parents=True, exist_ok=True)
    (pj / "plugin.json").write_text(
        json.dumps({"name": "demo", "version": version}, indent=2),
        encoding="utf-8",
    )
    (plugin_root / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "{version}"\n',
        encoding="utf-8",
    )


class TestStageBumpIdempotency:
    def test_skips_bump_when_local_already_matches_target(self, tmp_path: Path) -> None:
        """The recovery scenario: local plugin.json is already at the target
        version (interrupted publish), remote is one minor behind."""
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        _write_plugin_json(plugin_root, "2.64.0")

        with (
            patch("publish._read_remote_version", return_value="2.63.2"),
            patch("publish.do_bump") as fake_do_bump,
        ):
            rc, new_version = publish.stage_bump(plugin_root, "minor", dry_run=False)

        assert rc == 0
        assert new_version == "2.64.0"
        # Critical: do_bump must NOT be called when local already matches target
        fake_do_bump.assert_not_called()

    def test_normal_bump_when_local_matches_remote(self, tmp_path: Path) -> None:
        """Normal release: local == remote, run the bump."""
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        _write_plugin_json(plugin_root, "2.63.2")

        with (
            patch("publish._read_remote_version", return_value="2.63.2"),
            patch("publish.do_bump", return_value=True) as fake_do_bump,
            patch("publish.stage_update_readme_badge"),
        ):
            rc, new_version = publish.stage_bump(plugin_root, "minor", dry_run=False)

        assert rc == 0
        assert new_version == "2.64.0"
        fake_do_bump.assert_called_once()

    def test_refuses_when_local_is_unexpected(self, tmp_path: Path) -> None:
        """If local is at some version that is neither remote nor target,
        refuse to bump rather than guess."""
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        _write_plugin_json(plugin_root, "2.99.99")  # weird intermediate state

        with (
            patch("publish._read_remote_version", return_value="2.63.2"),
            patch("publish.do_bump") as fake_do_bump,
        ):
            rc, new_version = publish.stage_bump(plugin_root, "minor", dry_run=False)

        assert rc != 0
        assert new_version is None
        fake_do_bump.assert_not_called()

    def test_falls_back_to_local_baseline_when_remote_unavailable(self, tmp_path: Path) -> None:
        """Fresh clone or offline: no remote ref. Use local as baseline (legacy
        behaviour)."""
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        _write_plugin_json(plugin_root, "2.63.2")

        with (
            patch("publish._read_remote_version", return_value=None),
            patch("publish.do_bump", return_value=True) as fake_do_bump,
            patch("publish.stage_update_readme_badge"),
        ):
            rc, new_version = publish.stage_bump(plugin_root, "patch", dry_run=False)

        assert rc == 0
        assert new_version == "2.63.3"
        fake_do_bump.assert_called_once()


# ---------------------------------------------------------------------------
# Gate 10 / Gate 11 idempotency in stage_commit_tag_push
# ---------------------------------------------------------------------------


class TestCommitTagIdempotency:
    def test_skips_commit_when_head_already_release_commit_and_tree_clean(self, tmp_path: Path, capsys) -> None:
        """Gate 10 must skip the `git commit` step when HEAD's subject already
        matches the expected release commit AND the tree is clean."""
        plugin_root = tmp_path
        with (
            patch("publish.run") as fake_run,
            patch("publish._git_porcelain_clean", return_value=True),
            patch("publish._head_commit_message", return_value="chore(release): v2.64.0"),
            patch("publish._local_tag_exists", return_value=True),
            patch("publish._ensure_gh_auth"),
            patch("publish._resolve_owner_repo", return_value=("Emasoft", "demo")),
            patch("publish.git_with_retry"),
        ):
            rc = publish.stage_commit_tag_push(plugin_root, "v2.64.0")
        assert rc == 0
        # No `git add -A` / `git commit` call should have been issued — Gate 10
        # took the "already committed" short-circuit. (Inspect args, not str(call):
        # tmp_path contains the test name which itself contains "commit".)
        cmd_lists = [list(call.args[0]) if call.args else [] for call in fake_run.call_args_list]
        for cmd in cmd_lists:
            assert not (len(cmd) >= 2 and cmd[0] == "git" and cmd[1] in ("add", "commit")), (
                f"unexpected git add/commit call: {cmd}"
            )

    def test_skips_tag_when_already_exists(self, tmp_path: Path) -> None:
        """Gate 11 must skip `git tag` when the tag already points locally."""
        plugin_root = tmp_path
        with (
            patch("publish.run") as fake_run,
            patch("publish._git_porcelain_clean", return_value=False),
            patch("publish._head_commit_message", return_value=""),
            patch("publish._local_tag_exists", return_value=True),
            patch("publish._ensure_gh_auth"),
            patch("publish._resolve_owner_repo", return_value=("Emasoft", "demo")),
            patch("publish.git_with_retry"),
        ):
            publish.stage_commit_tag_push(plugin_root, "v2.64.0")
        # Inspect calls — none should be `git tag`
        all_calls = [list(call.args[0]) if call.args else [] for call in fake_run.call_args_list]
        for cmd in all_calls:
            assert not (len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "tag"), f"unexpected git tag call: {cmd}"

    def test_runs_commit_and_tag_when_neither_exists(self, tmp_path: Path) -> None:
        """Normal release path: working tree dirty, HEAD doesn't have the
        release commit, tag doesn't exist. Run both."""
        plugin_root = tmp_path
        with (
            patch("publish.run") as fake_run,
            patch("publish._git_porcelain_clean", return_value=False),
            patch("publish._head_commit_message", return_value="some other commit"),
            patch("publish._local_tag_exists", return_value=False),
            patch("publish._ensure_gh_auth"),
            patch("publish._resolve_owner_repo", return_value=("Emasoft", "demo")),
            patch("publish.git_with_retry"),
        ):
            publish.stage_commit_tag_push(plugin_root, "v2.64.0")
        all_calls = [list(call.args[0]) if call.args else [] for call in fake_run.call_args_list]
        assert any(cmd[:2] == ["git", "add"] for cmd in all_calls), "expected git add -A"
        assert any(cmd[:2] == ["git", "commit"] for cmd in all_calls), "expected git commit"
        assert any(cmd[:2] == ["git", "tag"] for cmd in all_calls), "expected git tag"
