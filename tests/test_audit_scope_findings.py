#!/usr/bin/env python3
"""Regression tests for audit findings #3, #8, #14 (scope domain).

Audit report:
``reports/audit/20260525_105207+0200-batch-menu-cli-content.md``.

* #3  MINOR — ``cpv-batch-doctor`` is a dead cross-reference in
  ``cpv_scope_doctor_input.URL_REJECTED_MESSAGE`` AND in
  ``commands/cpv-batch-scope-diagnose.md``. It must point only at
  commands that actually exist on disk.
* #14 NIT — ``resolve_scope_inputs`` docstring advertised a
  non-existent ``allow_url`` parameter; the real keyword is
  ``default_to_pwd``.
* #8  MINOR — ``classify_folder_scope`` returned ``"local"`` when the
  git ``ls-files`` probe itself failed (transient git-unavailable),
  conflating "git could not classify" with "untracked". It must return
  the ``"no-git"`` sentinel instead. Every consumer treats ``"no-git"``
  and ``"local"`` identically downstream, so the change is
  behaviour-preserving for validators while reporting the cause
  accurately.

Each behaviour-affecting finding is covered two-sided: the bad value is
provably gone AND the corrected value is provably present / honoured.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cc_scope_rules  # noqa: E402
import cpv_scope_doctor_input  # noqa: E402
from cc_scope_rules import classify_folder_scope  # noqa: E402
from cpv_scope_doctor_input import (  # noqa: E402
    URL_REJECTED_MESSAGE,
    resolve_scope_inputs,
)

REPO_ROOT = SCRIPTS_DIR.parent
COMMANDS_DIR = REPO_ROOT / "commands"
SCOPE_DIAGNOSE_CMD = COMMANDS_DIR / "cpv-batch-scope-diagnose.md"

# The dead reference the audit flagged, and the live replacements.
DEAD_COMMAND_REF = "cpv-batch-doctor"
LIVE_COMMAND_REFS = ("cpv-batch-validate", "cpv-batch-security-audit")


# =============================================================================
# #3 — dead cpv-batch-doctor cross-reference (py error string + command md)
# =============================================================================


class TestFinding3DeadCommandReference:
    """The URL-rejection text and command point only at real commands."""

    def test_error_message_does_not_mention_dead_command(self) -> None:
        """URL_REJECTED_MESSAGE no longer names the non-existent command."""
        assert DEAD_COMMAND_REF not in URL_REJECTED_MESSAGE

    def test_error_message_names_real_commands(self) -> None:
        """URL_REJECTED_MESSAGE names commands that exist on disk."""
        for ref in LIVE_COMMAND_REFS:
            assert ref in URL_REJECTED_MESSAGE
            assert (COMMANDS_DIR / f"{ref}.md").is_file()

    def test_command_md_does_not_mention_dead_command(self) -> None:
        """cpv-batch-scope-diagnose.md no longer names the dead command."""
        text = SCOPE_DIAGNOSE_CMD.read_text(encoding="utf-8")
        assert DEAD_COMMAND_REF not in text

    def test_command_md_names_real_commands(self) -> None:
        """cpv-batch-scope-diagnose.md references real commands."""
        text = SCOPE_DIAGNOSE_CMD.read_text(encoding="utf-8")
        for ref in LIVE_COMMAND_REFS:
            assert ref in text
            assert (COMMANDS_DIR / f"{ref}.md").is_file()

    def test_no_dead_command_file_exists(self) -> None:
        """The dead command genuinely has no backing file (was a phantom)."""
        assert not (COMMANDS_DIR / f"{DEAD_COMMAND_REF}.md").exists()


# =============================================================================
# #14 — stale resolve_scope_inputs docstring signature
# =============================================================================


class TestFinding14DocstringSignature:
    """The docstring matches the real signature (no phantom allow_url)."""

    def test_docstring_does_not_claim_allow_url_param(self) -> None:
        """The docstring no longer advertises a non-existent allow_url kw."""
        doc = inspect.getdoc(resolve_scope_inputs) or ""
        assert "allow_url=False" not in doc

    def test_docstring_mentions_real_keyword(self) -> None:
        """The docstring advertises the real default_to_pwd keyword."""
        doc = inspect.getdoc(resolve_scope_inputs) or ""
        assert "default_to_pwd" in doc

    def test_real_signature_has_default_to_pwd_not_allow_url(self) -> None:
        """The live signature exposes default_to_pwd and not allow_url."""
        params = inspect.signature(resolve_scope_inputs).parameters
        assert "default_to_pwd" in params
        assert "allow_url" not in params

    def test_module_docstring_signature_matches(self) -> None:
        """Module docstring advertises the corrected signature too."""
        mod_doc = cpv_scope_doctor_input.__doc__ or ""
        assert "resolve_scope_inputs(input_spec, *, default_to_pwd=True)" in mod_doc
        assert "resolve_scope_inputs(input_spec, *, allow_url=False)" not in mod_doc

    def test_default_to_pwd_true_resolves_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With default_to_pwd=True, empty input resolves to $PWD.

        $PWD must be a recognisable local shape for the underlying
        resolver to accept it, so the tmp cwd is given a minimal
        plugin manifest.
        """
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "x", "version": "0.0.1"}\n', encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        resolved = resolve_scope_inputs(None, default_to_pwd=True)
        assert len(resolved) == 1
        assert Path(resolved[0].abs_path).resolve() == tmp_path.resolve()

    def test_default_to_pwd_false_raises_on_empty(self) -> None:
        """With default_to_pwd=False, empty input raises (no $PWD fallback)."""
        with pytest.raises(cpv_scope_doctor_input.InputResolutionError):
            resolve_scope_inputs(None, default_to_pwd=False)


# =============================================================================
# #8 — git-unavailable must map to no-git, never to local
# =============================================================================


class TestFinding8GitFailureSentinel:
    """A transient git-probe failure returns no-git, not local."""

    def test_git_probe_failure_returns_no_git(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the ls-files probe returns None, scope is 'no-git'.

        The transient-failure branch is reached by forcing the ls-files
        ``_run_git`` call to return None *after* find_git_root has already
        succeeded — exactly the "git vanished mid-run / timeout" case the
        audit describes. ``classify_folder_scope`` is the unit under test;
        ``_run_git`` is its git-subprocess seam, so stubbing it is legal.
        """
        folder = tmp_path / "ext"
        folder.mkdir()
        # repo_root supplied so find_git_root is skipped — we are past the
        # no-.git-ancestor check and exercising the probe-failure branch.
        monkeypatch.setattr(cc_scope_rules, "resolve_within", lambda p, r: p)
        monkeypatch.setattr(cc_scope_rules, "is_git_ignored", lambda p, r: False)
        monkeypatch.setattr(cc_scope_rules, "_relative_to_root", lambda p, r: Path("ext"))
        monkeypatch.setattr(cc_scope_rules, "_run_git", lambda *a, **k: None)

        scope = classify_folder_scope(folder, tmp_path)
        assert scope == "no-git"

    def test_git_probe_failure_is_not_local(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The probe-failure branch must NOT be the old 'local' value."""
        folder = tmp_path / "ext"
        folder.mkdir()
        monkeypatch.setattr(cc_scope_rules, "resolve_within", lambda p, r: p)
        monkeypatch.setattr(cc_scope_rules, "is_git_ignored", lambda p, r: False)
        monkeypatch.setattr(cc_scope_rules, "_relative_to_root", lambda p, r: Path("ext"))
        monkeypatch.setattr(cc_scope_rules, "_run_git", lambda *a, **k: None)

        scope = classify_folder_scope(folder, tmp_path)
        assert scope != "local"

    def test_real_untracked_folder_still_local(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A genuine untracked folder (git WORKS, returns empty) stays local.

        This is the other side of the discriminator: only a *failed* probe
        (None) becomes no-git; a *successful* probe that lists nothing is
        still 'local'. We simulate a working git that finds zero tracked
        files under the folder.
        """
        folder = tmp_path / "ext"
        folder.mkdir()
        ok = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="", stderr="")
        monkeypatch.setattr(cc_scope_rules, "resolve_within", lambda p, r: p)
        monkeypatch.setattr(cc_scope_rules, "is_git_ignored", lambda p, r: False)
        monkeypatch.setattr(cc_scope_rules, "_relative_to_root", lambda p, r: Path("ext"))
        monkeypatch.setattr(cc_scope_rules, "_run_git", lambda *a, **k: ok)

        scope = classify_folder_scope(folder, tmp_path)
        assert scope == "local"

    def test_real_tracked_folder_still_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A folder with tracked files (git WORKS, lists output) stays project."""
        folder = tmp_path / "ext"
        folder.mkdir()
        ok = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="ext/a.md\n", stderr="")
        monkeypatch.setattr(cc_scope_rules, "resolve_within", lambda p, r: p)
        monkeypatch.setattr(cc_scope_rules, "is_git_ignored", lambda p, r: False)
        monkeypatch.setattr(cc_scope_rules, "_relative_to_root", lambda p, r: Path("ext"))
        monkeypatch.setattr(cc_scope_rules, "_run_git", lambda *a, **k: ok)

        scope = classify_folder_scope(folder, tmp_path)
        assert scope == "project"

    def test_no_git_is_a_legal_scope_value(self) -> None:
        """'no-git' is part of the Scope vocabulary (not an invented value)."""
        # The Scope Literal is the source of truth for legal return values.
        legal = set(cc_scope_rules.Scope.__args__)  # type: ignore[attr-defined]
        assert "no-git" in legal
        assert {"project", "local", "missing"} <= legal
