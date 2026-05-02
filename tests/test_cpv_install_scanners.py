"""Tests for the silent autoinstall helpers in cpv_install_scanners.

These tests do NOT exercise real installs — that would require root,
network, and an absent state. Instead they verify the cascade logic by
mocking ``shutil.which`` and ``subprocess.run`` so we can assert exactly
which install commands fire (and in which order) under each platform/state
combination.

The contract being tested:
  * If the binary is already on PATH, no install is attempted.
  * If the opt-out env var is set, no install is attempted.
  * Per-platform cascade respects the documented order
    (macOS: brew → cargo; Linux: snap → pacman/apk → cargo; Windows:
     GitHub release → cargo).
  * The runner never raises on subprocess failure — every helper returns
    True/False.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_install_scanners as cis  # noqa: E402


# ── _opt_out helper ──────────────────────────────────────────────────


class TestOptOut:
    def test_unset_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CPV_NO_FCLONES_INSTALL", raising=False)
        assert cis._opt_out("CPV_NO_FCLONES_INSTALL") is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", "on", "ON"])
    def test_truthy_values_return_true(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("CPV_NO_FCLONES_INSTALL", value)
        assert cis._opt_out("CPV_NO_FCLONES_INSTALL") is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
    def test_falsy_values_return_false(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("CPV_NO_FCLONES_INSTALL", value)
        assert cis._opt_out("CPV_NO_FCLONES_INSTALL") is False


# ── _silent_run helper ──────────────────────────────────────────────


class TestSilentRun:
    def test_returns_true_on_zero_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a: Any, **kw: Any) -> mock.Mock:
            m = mock.Mock()
            m.returncode = 0
            return m

        monkeypatch.setattr(cis.subprocess, "run", fake_run)
        assert cis._silent_run(["echo", "hi"]) is True

    def test_returns_false_on_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a: Any, **kw: Any) -> mock.Mock:
            m = mock.Mock()
            m.returncode = 1
            return m

        monkeypatch.setattr(cis.subprocess, "run", fake_run)
        assert cis._silent_run(["false"]) is False

    def test_returns_false_on_filenotfound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("no such binary")

        monkeypatch.setattr(cis.subprocess, "run", fake_run)
        assert cis._silent_run(["/no/such/binary"]) is False

    def test_returns_false_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*a: Any, **kw: Any) -> None:
            raise cis.subprocess.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(cis.subprocess, "run", fake_run)
        assert cis._silent_run(["sleep", "1000"]) is False

    def test_captures_output_silently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_kwargs: dict[str, Any] = {}

        def fake_run(*a: Any, **kw: Any) -> mock.Mock:
            captured_kwargs.update(kw)
            m = mock.Mock()
            m.returncode = 0
            return m

        monkeypatch.setattr(cis.subprocess, "run", fake_run)
        cis._silent_run(["echo", "noisy"])
        assert captured_kwargs.get("capture_output") is True


# ── _ensure_local_bin_on_path ──────────────────────────────────────


class TestEnsureLocalBinOnPath:
    def test_prepends_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        cis._ensure_local_bin_on_path()
        assert str(cis._local_bin_dir()) in cis.os.environ["PATH"].split(cis.os.pathsep)

    def test_idempotent_when_already_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bin_dir = str(cis._local_bin_dir())
        monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin")
        original = cis.os.environ["PATH"]
        cis._ensure_local_bin_on_path()
        # Still on PATH (no duplicate prepend)
        assert cis.os.environ["PATH"].count(bin_dir) == original.count(bin_dir)


# ── ensure_fclones cascade ──────────────────────────────────────────


class TestEnsureFclones:
    def test_returns_true_when_already_on_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cis.shutil, "which", lambda name: "/usr/bin/fclones")
        # No subprocess should be invoked.
        called = []
        monkeypatch.setattr(cis, "_silent_run", lambda *a, **kw: called.append(a))
        assert cis.ensure_fclones() is True
        assert called == []

    def test_opt_out_skips_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cis.shutil, "which", lambda name: None)
        monkeypatch.setenv("CPV_NO_FCLONES_INSTALL", "1")
        called = []
        monkeypatch.setattr(cis, "_silent_run", lambda *a, **kw: called.append(a))
        assert cis.ensure_fclones() is False
        assert called == []

    def test_macos_tries_brew_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CPV_NO_FCLONES_INSTALL", raising=False)
        monkeypatch.setattr(cis.platform, "system", lambda: "Darwin")
        which_calls: list[str] = []

        def fake_which(name: str) -> str | None:
            which_calls.append(name)
            if name == "fclones":
                # Not installed for first three probes (initial,
                # post-brew, post-cargo); installed after that.
                if which_calls.count("fclones") <= 1:
                    return None
                return "/opt/homebrew/bin/fclones"
            if name == "brew":
                return "/opt/homebrew/bin/brew"
            return None

        monkeypatch.setattr(cis.shutil, "which", fake_which)
        run_calls: list[list[str]] = []

        def fake_run(argv: list[str], *a: Any, **kw: Any) -> bool:
            run_calls.append(argv)
            return True  # brew install succeeds

        monkeypatch.setattr(cis, "_silent_run", fake_run)
        assert cis.ensure_fclones() is True
        # brew was the first thing tried
        assert run_calls[0] == ["brew", "install", "fclones"]

    def test_linux_tries_snap_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CPV_NO_FCLONES_INSTALL", raising=False)
        monkeypatch.setattr(cis.platform, "system", lambda: "Linux")

        which_lookup = {"fclones": [None, "/snap/bin/fclones"], "snap": "/usr/bin/snap"}
        which_calls: dict[str, int] = {}

        def fake_which(name: str) -> str | None:
            count = which_calls.get(name, 0)
            which_calls[name] = count + 1
            if name in which_lookup:
                value = which_lookup[name]
                if isinstance(value, list):
                    return value[count] if count < len(value) else value[-1]
                return value
            return None

        monkeypatch.setattr(cis.shutil, "which", fake_which)
        run_calls: list[list[str]] = []

        def fake_run(argv: list[str], *a: Any, **kw: Any) -> bool:
            run_calls.append(argv)
            return True

        monkeypatch.setattr(cis, "_silent_run", fake_run)
        assert cis.ensure_fclones() is True
        assert run_calls[0] == ["snap", "install", "fclones"]

    def test_windows_attempts_release_download(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CPV_NO_FCLONES_INSTALL", raising=False)
        monkeypatch.setattr(cis.platform, "system", lambda: "Windows")
        monkeypatch.setattr(cis.shutil, "which", lambda name: None)

        download_called = {"flag": False}

        def fake_download() -> bool:
            download_called["flag"] = True
            return False  # download fails → cascade to cargo

        monkeypatch.setattr(cis, "_download_fclones_github_release", fake_download)
        monkeypatch.setattr(cis, "_install_fclones_via_cargo", lambda: None)
        # Final probe: still not installed
        cis.ensure_fclones()
        assert download_called["flag"] is True


# ── ensure_cc_audit / trufflehog / semgrep / tirith / cisco ────────


class TestEnsureCcAudit:
    def test_returns_true_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cis.shutil, "which", lambda name: "/usr/bin/cc-audit")
        assert cis.ensure_cc_audit() is True

    def test_opt_out_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cis.shutil, "which", lambda name: None)
        monkeypatch.setenv("CPV_NO_CC_AUDIT_INSTALL", "1")
        assert cis.ensure_cc_audit() is False

    def test_invokes_npm_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CPV_NO_CC_AUDIT_INSTALL", raising=False)
        which_calls: list[str] = []

        def fake_which(name: str) -> str | None:
            which_calls.append(name)
            if name == "npm":
                return "/usr/bin/npm"
            if name == "cc-audit" and which_calls.count("cc-audit") == 1:
                return None
            if name == "cc-audit":
                return "/usr/local/bin/cc-audit"
            return None

        monkeypatch.setattr(cis.shutil, "which", fake_which)
        run_calls: list[list[str]] = []
        monkeypatch.setattr(cis, "_silent_run", lambda argv, **kw: run_calls.append(argv) or True)
        cis.ensure_cc_audit()
        assert run_calls[0] == ["npm", "install", "-g", "@cc-audit/cc-audit"]


class TestEnsureCiscoSkillScanner:
    def test_returns_true_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cis.shutil, "which", lambda name: "/usr/local/bin/skill-scanner")
        assert cis.ensure_cisco_skill_scanner() is True

    def test_opt_out_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cis.shutil, "which", lambda name: None)
        monkeypatch.setenv("CPV_NO_CISCO_INSTALL", "1")
        assert cis.ensure_cisco_skill_scanner() is False

    def test_invokes_uv_tool_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CPV_NO_CISCO_INSTALL", raising=False)
        which_calls: list[str] = []

        def fake_which(name: str) -> str | None:
            which_calls.append(name)
            if name == "uv":
                return "/usr/local/bin/uv"
            return None

        monkeypatch.setattr(cis.shutil, "which", fake_which)
        run_calls: list[list[str]] = []
        monkeypatch.setattr(cis, "_silent_run", lambda argv, **kw: run_calls.append(argv) or True)
        cis.ensure_cisco_skill_scanner()
        assert run_calls[0] == ["uv", "tool", "install", "cisco-ai-skill-scanner"]


# ── install_all_scanners batch helper ──────────────────────────────


class TestInstallAllScanners:
    def test_returns_dict_with_all_six_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mark every scanner as already installed; no cascades to run.
        monkeypatch.setattr(cis.shutil, "which", lambda name: f"/usr/bin/{name}")
        statuses = cis.install_all_scanners()
        assert set(statuses.keys()) == {
            "fclones",
            "cc-audit",
            "trufflehog",
            "semgrep",
            "tirith",
            "skill-scanner",
        }
        assert all(statuses.values()), f"all should be available; got {statuses}"

    def test_returns_false_when_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No scanner on PATH; every opt-out set so no cascades fire.
        monkeypatch.setattr(cis.shutil, "which", lambda name: None)
        for var in (
            "CPV_NO_FCLONES_INSTALL",
            "CPV_NO_CC_AUDIT_INSTALL",
            "CPV_NO_TRUFFLEHOG_INSTALL",
            "CPV_NO_SEMGREP_INSTALL",
            "CPV_NO_TIRITH_INSTALL",
            "CPV_NO_CISCO_INSTALL",
        ):
            monkeypatch.setenv(var, "1")
        statuses = cis.install_all_scanners()
        assert all(v is False for v in statuses.values()), statuses
