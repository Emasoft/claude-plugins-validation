"""Tests for cpv_validate_plugin_folder.py.

Covers shape detection, mode selection (including the deliberate ``cache``
exclusion), the remote-vs-local classifier, target resolution, the
worst-wins severity ordering, the remote plugin-or-skill gate, and the
sandbox cleanup contract. Also carries regression cases for the GitHub /
GitLab URL normalisation in ``cpv_pre_install_scan.py`` that
``cpv_validate_plugin_folder`` reuses for its own remote-fetch path.

No real validator subprocess is ever run here — ``run_mode`` is always
monkeypatched — so the suite is fast and hermetic (no network, no clone).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_pre_install_scan  # noqa: E402
import cpv_validate_plugin_folder as folder  # noqa: E402
from cpv_pre_install_scan import _normalize_github_url  # noqa: E402
from cpv_validation_common import (  # noqa: E402
    EXIT_CRITICAL,
    EXIT_MAJOR,
    EXIT_MINOR,
    EXIT_NIT,
    EXIT_OK,
)

# ---------------------------------------------------------------------------
# detect_shape
# ---------------------------------------------------------------------------


class TestDetectShape:
    """detect_shape must tell apart the four folder shapes it classifies."""

    def test_plugin_shape_from_manifest(self, tmp_path: Path) -> None:
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text("{}")
        assert folder.detect_shape(tmp_path) == "plugin"

    def test_marketplace_shape_from_manifest(self, tmp_path: Path) -> None:
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "marketplace.json").write_text("{}")
        assert folder.detect_shape(tmp_path) == "marketplace"

    def test_skill_shape_from_root_skill_md(self, tmp_path: Path) -> None:
        (tmp_path / "SKILL.md").write_text("# skill")
        assert folder.detect_shape(tmp_path) == "skill"

    def test_unknown_shape_for_empty_dir(self, tmp_path: Path) -> None:
        assert folder.detect_shape(tmp_path) == "unknown"


# ---------------------------------------------------------------------------
# modes_for
# ---------------------------------------------------------------------------


class TestModesFor:
    """modes_for maps a shape to its scan set, and NEVER includes cache."""

    @pytest.mark.parametrize(
        ("shape", "expected"),
        [
            ("plugin", ["plugin", "security"]),
            ("marketplace", ["marketplace", "security"]),
            ("skill", ["skill", "security"]),
            ("unknown", ["security"]),
        ],
    )
    def test_modes_for_shape(self, shape: str, expected: list[str]) -> None:
        assert folder.modes_for(shape) == expected

    @pytest.mark.parametrize("shape", ["plugin", "marketplace", "skill", "unknown"])
    def test_cache_mode_never_included(self, shape: str) -> None:
        """cache mode CRITICALs any folder without plugin.json, and plugin
        mode already runs the CA-01..07 audit in-process — this is a
        deliberate design decision (see modes_for's own docstring), pinned
        here so a later change does not "helpfully" re-add it."""
        assert "cache" not in folder.modes_for(shape)


# ---------------------------------------------------------------------------
# is_remote_spec
# ---------------------------------------------------------------------------


class TestIsRemoteSpec:
    """is_remote_spec must recognise remote shapes and defer to a local path."""

    def test_github_https_url_is_remote(self) -> None:
        assert folder.is_remote_spec("https://github.com/o/r") is True

    def test_gitlab_https_url_is_remote(self) -> None:
        assert folder.is_remote_spec("https://gitlab.com/g/s/r") is True

    def test_owner_repo_shorthand_is_remote(self) -> None:
        assert folder.is_remote_spec("owner/repo") is True

    def test_existing_local_dir_wins_over_remote_shape(self, tmp_path: Path) -> None:
        """The local-path-wins rule: an EXISTING dir is never mistaken for a
        remote spec, no matter what its name looks like."""
        local_dir = tmp_path / "owner-repo-lookalike"
        local_dir.mkdir()
        assert folder.is_remote_spec(str(local_dir)) is False

    def test_plain_word_is_not_remote(self) -> None:
        assert folder.is_remote_spec("hello") is False

    def test_absolute_path_is_not_remote(self) -> None:
        assert folder.is_remote_spec("/nonexistent/absolute/path") is False


# ---------------------------------------------------------------------------
# resolve_target
# ---------------------------------------------------------------------------


class TestResolveTarget:
    """resolve_target: explicit arg > $CLAUDE_PROJECT_DIR > cwd."""

    def test_explicit_arg_resolved(self, tmp_path: Path) -> None:
        target = tmp_path / "sub"
        target.mkdir()
        assert folder.resolve_target(str(target)) == target.resolve()

    def test_env_var_used_when_no_explicit_arg(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        assert folder.resolve_target(None) == tmp_path.resolve()

    def test_cwd_used_when_no_arg_and_no_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert folder.resolve_target(None) == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Severity aggregation
# ---------------------------------------------------------------------------


class TestSeverityRank:
    """_SEVERITY_RANK orders CRITICAL worst, OK best — opposite of the raw
    exit-code vocabulary (1 == most severe), so a plain max() over the codes
    would pick the wrong worst-mode. worst-wins in main() relies on this."""

    def test_worst_wins_ordering(self) -> None:
        rank = folder._SEVERITY_RANK
        assert (
            rank[EXIT_CRITICAL] > rank[EXIT_MAJOR] > rank[EXIT_MINOR] > rank[EXIT_NIT] > rank[EXIT_OK]
        )


# ---------------------------------------------------------------------------
# The remote plugin-or-skill gate + sandbox cleanup
# ---------------------------------------------------------------------------


def _write_plugin_manifest(root: Path) -> None:
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True, exist_ok=True)
    (cp / "plugin.json").write_text("{}")


class TestRemoteGate:
    """A cloned remote target that is neither a plugin nor a skill is
    rejected before any validator runs; a remote target that IS a plugin
    proceeds normally."""

    def test_remote_fetch_not_plugin_or_skill_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fetched = tmp_path / "fetched"
        fetched.mkdir()

        def fake_fetch(spec: str, sandbox: Path) -> tuple[Path, str]:
            return fetched, spec

        monkeypatch.setattr(cpv_pre_install_scan, "_fetch_target", fake_fetch)
        monkeypatch.setattr(sys, "argv", ["cpv-validate-plugin-folder", "https://github.com/o/r"])
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        exit_code = folder.main()

        assert exit_code == EXIT_CRITICAL
        assert "not a plugin or skill" in capsys.readouterr().err

    def test_remote_fetch_that_is_a_plugin_does_not_hit_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fetched = tmp_path / "fetched"
        _write_plugin_manifest(fetched)

        def fake_fetch(spec: str, sandbox: Path) -> tuple[Path, str]:
            return fetched, spec

        def fake_run_mode(mode: str, target: Path, out: Path) -> tuple[int, str]:
            return 0, ""

        monkeypatch.setattr(cpv_pre_install_scan, "_fetch_target", fake_fetch)
        monkeypatch.setattr(folder, "run_mode", fake_run_mode)
        monkeypatch.setattr(sys, "argv", ["cpv-validate-plugin-folder", "https://github.com/o/r"])
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        exit_code = folder.main()

        assert exit_code == EXIT_OK
        assert "not a plugin or skill" not in capsys.readouterr().err


class TestSandboxCleanup:
    """The temp sandbox is deleted on EVERY exit path — the gate-failure
    early return AND the ordinary success path."""

    def test_sandbox_removed_after_gate_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sandbox_dir = tmp_path / "sandbox-gate"
        sandbox_dir.mkdir()

        def fake_mkdtemp(*args: object, **kwargs: object) -> str:
            return str(sandbox_dir)

        def fake_fetch(spec: str, sandbox: Path) -> tuple[Path, str]:
            return sandbox_dir, spec

        monkeypatch.setattr(tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(cpv_pre_install_scan, "_fetch_target", fake_fetch)
        monkeypatch.setattr(sys, "argv", ["cpv-validate-plugin-folder", "https://github.com/o/r"])
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        exit_code = folder.main()

        assert exit_code == EXIT_CRITICAL
        assert not sandbox_dir.exists()

    def test_sandbox_removed_after_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sandbox_dir = tmp_path / "sandbox-ok"
        _write_plugin_manifest(sandbox_dir)

        def fake_mkdtemp(*args: object, **kwargs: object) -> str:
            return str(sandbox_dir)

        def fake_fetch(spec: str, sandbox: Path) -> tuple[Path, str]:
            return sandbox_dir, spec

        def fake_run_mode(mode: str, target: Path, out: Path) -> tuple[int, str]:
            return 0, ""

        monkeypatch.setattr(tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(cpv_pre_install_scan, "_fetch_target", fake_fetch)
        monkeypatch.setattr(folder, "run_mode", fake_run_mode)
        monkeypatch.setattr(sys, "argv", ["cpv-validate-plugin-folder", "https://github.com/o/r"])
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        exit_code = folder.main()

        assert exit_code == EXIT_OK
        assert not sandbox_dir.exists()


# ---------------------------------------------------------------------------
# Regression: GitHub / GitLab URL normalisation (cpv_pre_install_scan.py)
# ---------------------------------------------------------------------------


class TestNormalizeGithubUrl:
    """_normalize_github_url must turn every supported spec shape into a
    clone URL, and must NOT mangle a GitLab subgroup path (the case an
    earlier single-regex version got wrong)."""

    def test_owner_repo_shorthand_maps_to_github_clone_url(self) -> None:
        assert _normalize_github_url("Emasoft/cpv") == "https://github.com/Emasoft/cpv.git"

    def test_github_tree_url_maps_to_bare_clone_url(self) -> None:
        assert (
            _normalize_github_url("https://github.com/E/cpv/tree/main/skills")
            == "https://github.com/E/cpv.git"
        )

    def test_gitlab_simple_repo_maps_to_clone_url(self) -> None:
        assert (
            _normalize_github_url("https://gitlab.com/owner/repo")
            == "https://gitlab.com/owner/repo.git"
        )

    def test_gitlab_subgroup_repo_is_last_segment_not_second(self) -> None:
        """The repo is the LAST segment of a subgroup path — an earlier
        single-regex version wrongly returned .../group/sub.git here."""
        assert (
            _normalize_github_url("https://gitlab.com/group/sub/repo")
            == "https://gitlab.com/group/sub/repo.git"
        )

    def test_gitlab_web_view_tree_segment_is_stripped(self) -> None:
        assert (
            _normalize_github_url("https://gitlab.com/g/s/repo/-/tree/main")
            == "https://gitlab.com/g/s/repo.git"
        )

    def test_unrelated_host_url_is_unchanged(self) -> None:
        assert _normalize_github_url("https://example.com/x/y") == "https://example.com/x/y"
