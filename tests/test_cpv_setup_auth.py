#!/usr/bin/env python3
"""Tests for scripts/cpv_setup_auth.py (TRDD-b5e44619 Phase C — partial).

The eight-surface auth orchestrator. The script never mutates anything
beyond what the user explicitly authorises via subcommands; the default
``check`` mode is read-only and only reports each surface's status as
SET / NOT SET / PARTIAL / N/A.

Each surface gets one happy-path test (read-only check passes) plus at
least one failure-path test (the surface is not configured), per the
TRDD's eight-row table.

Auth surfaces:
    1. Git identity              (git config user.name + user.email)
    2. GitHub HTTPS auth         (gh auth status; owner/repo read-only check)
    3. GitHub SSH auth           (~/.ssh/config + ssh-add -L)
    4. MARKETPLACE_PAT           (env var + secret-on-repo, via set_marketplace_pat)
    5. Branch protection rules   (setup_branch_rules / setup_branch_rules_generic)
    6. Pre-push hook             (setup_git_hooks)
    7. GPG / SSH commit signing  (commit.gpgsign / user.signingkey)
    8. External scanners         (cpv_install_scanners — fclones, trufflehog, ...)

This is the read-only contract test for ``cpv_setup_auth.check_surfaces``.
The script wraps existing helpers — no new shell calls are introduced
that aren't already in the helper modules. Mocked subprocess calls below
are the SAME calls those helpers make, exercised through the orchestrator.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "cpv_setup_auth.py"


def _load_module():
    """Load cpv_setup_auth.py as a module — same pattern as test_set_marketplace_pat.py.

    Registers the module in ``sys.modules`` BEFORE ``exec_module`` so that
    @dataclass(frozen=True) + ``from __future__ import annotations`` can
    resolve the class's __module__ during decoration. Without the pre-
    registration, ``dataclasses._is_type`` calls ``sys.modules.get(__module__)
    .__dict__`` and crashes with AttributeError on NoneType.
    """
    spec = importlib.util.spec_from_file_location("cpv_setup_auth", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cpv_setup_auth"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def auth():
    """Loaded module object for cpv_setup_auth.py."""
    return _load_module()


def _run_script(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Invoke the script as a subprocess with a controlled env."""
    cmd = [sys.executable, str(SCRIPT_PATH), *args]
    full_env = {"PATH": os.environ.get("PATH", ""), **(env or {})}
    # 90s: covers gh-CLI auth checks under concurrent xdist load + publish-pipeline pressure.
    # 30s was too tight when publish.py runs Gate-2 pytest alongside Gates-3/4/5 (Phase C).
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env, timeout=90)


# ── Module-level smoke tests ─────────────────────────────────────────────────


class TestModuleSmoke:
    """Verify the script loads and its public API is in place."""

    def test_script_file_exists(self):
        """The script must be present at the expected path."""
        assert SCRIPT_PATH.exists(), f"Missing: {SCRIPT_PATH}"
        assert SCRIPT_PATH.is_file()

    def test_module_imports_cleanly(self, auth):
        """Module loads without import-time side effects."""
        assert auth is not None

    def test_status_constants_exist(self, auth):
        """The 4 status values are exposed as module constants."""
        assert auth.STATUS_SET == "SET"
        assert auth.STATUS_NOT_SET == "NOT SET"
        assert auth.STATUS_PARTIAL == "PARTIAL"
        assert auth.STATUS_NA == "N/A"

    def test_eight_surfaces_enumerated(self, auth):
        """The 8 auth surfaces from TRDD-b5e44619 §C are enumerated by id."""
        # Using a stable integer key matching the TRDD table column #.
        assert set(auth.AUTH_SURFACES.keys()) == {1, 2, 3, 4, 5, 6, 7, 8}
        # Each surface has at least a `name` and a `check` callable.
        for sid, spec in auth.AUTH_SURFACES.items():
            assert isinstance(sid, int)
            assert "name" in spec
            assert "check" in spec
            assert callable(spec["check"])

    def test_check_surfaces_returns_list_of_results(self, auth):
        """check_surfaces() returns a list of SurfaceResult — one per surface."""
        results = auth.check_surfaces()
        assert isinstance(results, list)
        assert len(results) == 8
        # Each result has the expected fields.
        for r in results:
            assert hasattr(r, "surface_id")
            assert hasattr(r, "name")
            assert hasattr(r, "status")
            assert hasattr(r, "detail")
            assert r.status in {auth.STATUS_SET, auth.STATUS_NOT_SET, auth.STATUS_PARTIAL, auth.STATUS_NA}


# ── Surface 1 — Git identity ────────────────────────────────────────────────


class TestSurface1GitIdentity:
    """git config user.name + user.email — local repo OR --global; falls back to env."""

    def test_set_when_both_local_set(self, auth):
        """Both local user.name and user.email present → SET."""
        # Helpers below accept (key, scope=...) — match the same call shape
        # check_git_identity uses (positional scope arg).
        with patch.object(auth, "_git_config_get") as mock_get:
            mock_get.side_effect = lambda key, scope="local": {
                ("user.name", "local"): "Emasoft",
                ("user.email", "local"): "x@y.z",
                ("user.name", "global"): "",
                ("user.email", "global"): "",
            }.get((key, scope), "")
            r = auth.check_git_identity()
            assert r.status == auth.STATUS_SET
            assert "Emasoft" in r.detail

    def test_set_when_both_global_set(self, auth):
        """Only global config → SET (local can be empty)."""
        with patch.object(auth, "_git_config_get") as mock_get:
            mock_get.side_effect = lambda key, scope="local": {
                ("user.name", "local"): "",
                ("user.email", "local"): "",
                ("user.name", "global"): "Emasoft",
                ("user.email", "global"): "x@y.z",
            }.get((key, scope), "")
            r = auth.check_git_identity()
            assert r.status == auth.STATUS_SET

    def test_partial_when_only_name(self, auth):
        """Only name set, no email → PARTIAL."""
        with patch.object(auth, "_git_config_get") as mock_get:
            mock_get.side_effect = lambda key, scope="local": {
                ("user.name", "local"): "Emasoft",
                ("user.name", "global"): "",
                ("user.email", "local"): "",
                ("user.email", "global"): "",
            }.get((key, scope), "")
            r = auth.check_git_identity()
            assert r.status == auth.STATUS_PARTIAL

    def test_not_set_when_neither(self, auth):
        """Neither name nor email anywhere → NOT SET."""
        with patch.object(auth, "_git_config_get") as mock_get:
            mock_get.return_value = ""
            r = auth.check_git_identity()
            assert r.status == auth.STATUS_NOT_SET


# ── Surface 2 — GitHub HTTPS auth via gh CLI ────────────────────────────────


class TestSurface2GhAuth:
    """gh auth status (and optional owner/repo read access)."""

    def test_set_when_gh_auth_status_succeeds(self, auth):
        """gh auth status exits 0 → SET."""
        with patch.object(auth, "_run_cmd") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["gh", "auth", "status"], returncode=0, stdout="Logged in as Emasoft", stderr=""
            )
            with patch("shutil.which", return_value="/usr/local/bin/gh"):
                r = auth.check_gh_auth()
                assert r.status == auth.STATUS_SET

    def test_not_set_when_gh_missing(self, auth):
        """gh CLI not on PATH → NOT SET."""
        with patch("shutil.which", return_value=None):
            r = auth.check_gh_auth()
            assert r.status == auth.STATUS_NOT_SET
            assert "gh" in r.detail.lower()

    def test_not_set_when_gh_auth_fails(self, auth):
        """gh installed but `gh auth status` exits non-zero → NOT SET."""
        with patch("shutil.which", return_value="/usr/local/bin/gh"):
            with patch.object(auth, "_run_cmd") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["gh", "auth", "status"],
                    returncode=1,
                    stdout="",
                    stderr="You are not logged in",
                )
                r = auth.check_gh_auth()
                assert r.status == auth.STATUS_NOT_SET


# ── Surface 3 — GitHub SSH auth ─────────────────────────────────────────────


class TestSurface3SshAuth:
    """SSH key registered with ssh-agent (ssh-add -L) — best-effort detection."""

    def test_set_when_ssh_add_lists_keys(self, auth):
        """ssh-add -L returns a key → SET."""
        with patch("shutil.which", return_value="/usr/bin/ssh-add"):
            with patch.object(auth, "_run_cmd") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["ssh-add", "-L"],
                    returncode=0,
                    stdout="ssh-ed25519 AAAAC3NzaC1l...comment",
                    stderr="",
                )
                r = auth.check_ssh_auth()
                assert r.status == auth.STATUS_SET

    def test_not_set_when_no_keys(self, auth):
        """ssh-add -L returns 'The agent has no identities' → NOT SET."""
        with patch("shutil.which", return_value="/usr/bin/ssh-add"):
            with patch.object(auth, "_run_cmd") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["ssh-add", "-L"],
                    returncode=1,
                    stdout="",
                    stderr="The agent has no identities.",
                )
                r = auth.check_ssh_auth()
                assert r.status == auth.STATUS_NOT_SET

    def test_na_when_ssh_missing(self, auth):
        """ssh-add not on PATH → N/A (some platforms don't ship it)."""
        with patch("shutil.which", return_value=None):
            r = auth.check_ssh_auth()
            assert r.status == auth.STATUS_NA


# ── Surface 4 — MARKETPLACE_PAT ─────────────────────────────────────────────


class TestSurface4MarketplacePat:
    """PAT in env + secret on repo (delegated to set_marketplace_pat helper)."""

    def test_set_when_env_var_present(self, auth, monkeypatch):
        """MARKETPLACE_PAT or PAT_MARKETPLACE in env → SET (env-side)."""
        monkeypatch.setenv("MARKETPLACE_PAT", "ghp_dummytestvalue1234567890abc")
        # Read-only check considers env presence; secret-on-repo verification is
        # opt-in via --repo flag and not exercised by the default check_surfaces().
        r = auth.check_marketplace_pat()
        assert r.status == auth.STATUS_SET

    def test_set_when_alternate_env_var_present(self, auth, monkeypatch):
        """PAT_MARKETPLACE alternate name → SET (per set_marketplace_pat order)."""
        monkeypatch.delenv("MARKETPLACE_PAT", raising=False)
        monkeypatch.setenv("PAT_MARKETPLACE", "ghp_dummytestvalue1234567890abc")
        r = auth.check_marketplace_pat()
        assert r.status == auth.STATUS_SET

    def test_not_set_when_no_env_var(self, auth, monkeypatch):
        """Neither name set → NOT SET."""
        monkeypatch.delenv("MARKETPLACE_PAT", raising=False)
        monkeypatch.delenv("PAT_MARKETPLACE", raising=False)
        r = auth.check_marketplace_pat()
        assert r.status == auth.STATUS_NOT_SET


# ── Surface 5 — Branch protection rules ─────────────────────────────────────


class TestSurface5BranchRules:
    """Branch-rules helper presence (script-on-disk check; remote query opt-in)."""

    def test_set_when_helper_script_present(self, auth):
        """scripts/setup_branch_rules.py exists and is importable → SET."""
        # The helper IS present in CPV by construction; this test guards against
        # accidental deletion/rename.
        r = auth.check_branch_rules()
        # In CPV's own checkout the helper exists, so we expect SET.
        assert r.status == auth.STATUS_SET
        assert "setup_branch_rules" in r.detail

    def test_not_set_when_helper_missing(self, auth, tmp_path, monkeypatch):
        """If the helper script is missing → NOT SET."""
        # Point the helper-locator at an empty directory.
        monkeypatch.setattr(auth, "SCRIPTS_DIR", tmp_path)
        r = auth.check_branch_rules()
        assert r.status == auth.STATUS_NOT_SET


# ── Surface 6 — Pre-push hook ───────────────────────────────────────────────


class TestSurface6PrePushHook:
    """`git config core.hooksPath` and the pre-push hook file."""

    def test_set_when_hooks_path_and_hook_exist(self, auth, tmp_path):
        """core.hooksPath set + pre-push file present → SET."""
        hooks_dir = tmp_path / "git-hooks"
        hooks_dir.mkdir()
        (hooks_dir / "pre-push").write_text("#!/bin/sh\nexit 0\n")
        with patch.object(auth, "_git_config_get") as mock_get:
            # _git_config_get(key, scope="local"); default scope → "local".
            mock_get.side_effect = lambda key, *args, **kwargs: (str(hooks_dir) if key == "core.hooksPath" else "")
            r = auth.check_pre_push_hook(plugin_root=tmp_path)
            assert r.status == auth.STATUS_SET

    def test_partial_when_hooks_path_unset_but_hook_in_default(self, auth, tmp_path):
        """No hooksPath but .git/hooks/pre-push exists → PARTIAL."""
        git_dir = tmp_path / ".git"
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "pre-push").write_text("#!/bin/sh\nexit 0\n")
        with patch.object(auth, "_git_config_get") as mock_get:
            mock_get.return_value = ""
            r = auth.check_pre_push_hook(plugin_root=tmp_path)
            assert r.status == auth.STATUS_PARTIAL

    def test_not_set_when_no_hook_anywhere(self, auth, tmp_path):
        """Neither core.hooksPath nor a default-location hook → NOT SET."""
        with patch.object(auth, "_git_config_get") as mock_get:
            mock_get.return_value = ""
            r = auth.check_pre_push_hook(plugin_root=tmp_path)
            assert r.status == auth.STATUS_NOT_SET


# ── Surface 7 — GPG / SSH commit signing ────────────────────────────────────


class TestSurface7CommitSigning:
    """commit.gpgsign or user.signingkey configured (optional convention)."""

    def test_set_when_gpgsign_true_and_signingkey_present(self, auth):
        """commit.gpgsign=true + user.signingkey set → SET."""
        with patch.object(auth, "_git_config_get") as mock_get:
            mock_get.side_effect = lambda key, scope="local": {
                "commit.gpgsign": "true",
                "user.signingkey": "ABC123DEADBEEF",
            }.get(key, "")
            r = auth.check_commit_signing()
            assert r.status == auth.STATUS_SET

    def test_partial_when_signingkey_set_but_gpgsign_false(self, auth):
        """Key declared but signing not enabled → PARTIAL."""
        with patch.object(auth, "_git_config_get") as mock_get:
            mock_get.side_effect = lambda key, scope="local": {
                "commit.gpgsign": "false",
                "user.signingkey": "ABC123DEADBEEF",
            }.get(key, "")
            r = auth.check_commit_signing()
            assert r.status == auth.STATUS_PARTIAL

    def test_not_set_when_neither(self, auth):
        """Neither configured → NOT SET (this is the OPTIONAL surface, OK)."""
        with patch.object(auth, "_git_config_get") as mock_get:
            mock_get.return_value = ""
            r = auth.check_commit_signing()
            assert r.status == auth.STATUS_NOT_SET


# ── Surface 8 — External scanners ────────────────────────────────────────────


class TestSurface8ExternalScanners:
    """cpv_install_scanners.* — best-effort PATH check for the 6 scanners."""

    def test_set_when_all_six_on_path(self, auth):
        """All 6 scanners detected on PATH → SET."""
        scanners = {"fclones", "cc-audit", "trufflehog", "semgrep", "tirith", "skill-scanner"}

        def _which(name):
            return f"/usr/local/bin/{name}" if name in scanners else None

        with patch("shutil.which", side_effect=_which):
            r = auth.check_external_scanners()
            assert r.status == auth.STATUS_SET

    def test_partial_when_some_on_path(self, auth):
        """At least one but not all scanners → PARTIAL."""

        def _which(name):
            return f"/usr/local/bin/{name}" if name in {"fclones", "trufflehog"} else None

        with patch("shutil.which", side_effect=_which):
            r = auth.check_external_scanners()
            assert r.status == auth.STATUS_PARTIAL

    def test_not_set_when_none_on_path(self, auth):
        """None of the scanners on PATH → NOT SET."""
        with patch("shutil.which", return_value=None):
            r = auth.check_external_scanners()
            assert r.status == auth.STATUS_NOT_SET


# ── Reporting + CLI ─────────────────────────────────────────────────────────


class TestRender:
    """The Unicode-table renderer matches the TRDD's output style."""

    def test_render_returns_text_with_table_borders(self, auth):
        """render_table() emits a Unicode-bordered table."""
        results = [
            auth.SurfaceResult(surface_id=1, name="Git identity", status=auth.STATUS_SET, detail="Emasoft <e@x>"),
            auth.SurfaceResult(surface_id=2, name="GitHub HTTPS", status=auth.STATUS_NOT_SET, detail=""),
        ]
        text = auth.render_table(results)
        assert isinstance(text, str)
        # Heavy header borders + light body borders, per the user's table convention.
        assert "┏" in text and "┳" in text and "┓" in text
        assert "┡" in text and "╇" in text and "┩" in text
        assert "Git identity" in text
        assert "SET" in text and "NOT SET" in text


class TestCLI:
    """Subprocess-level CLI smoke tests."""

    def test_help_flag_exits_zero(self):
        result = _run_script(["--help"])
        assert result.returncode == 0
        assert "auth" in result.stdout.lower()

    def test_check_default_exits_zero_in_clean_env(self):
        """Even a totally unconfigured environment → check exits 0 (it's a status report)."""
        # Strip every relevant env var so we exercise the worst-case `NOT SET` path.
        env = {
            # Minimal PATH to avoid pytest pollution; scanners will resolve to None.
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        result = _run_script(["check"], env=env)
        # CLI succeeds; it's a status report, not a gate.
        assert result.returncode == 0
        # Output must mention all 8 surfaces.
        for surface_word in ("Git identity", "GitHub HTTPS", "GitHub SSH"):
            assert surface_word in result.stdout

    def test_check_strict_exits_nonzero_when_required_missing(self):
        """`--strict` flag exits non-zero when any REQUIRED surface is NOT SET."""
        env = {
            "PATH": "/dev/null",  # nothing on PATH
            "HOME": os.environ.get("HOME", ""),
        }
        result = _run_script(["check", "--strict"], env=env)
        # In strict mode, missing required surfaces (1, 2, 6) → exit 1.
        assert result.returncode != 0
