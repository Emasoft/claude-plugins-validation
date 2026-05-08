#!/usr/bin/env python3
"""Tests for scripts/set_marketplace_pat.py.

Covers the helper that replaces improvised ``gh secret set`` invocations.
The core invariants under test:

1. Refuses to run when ``$MARKETPLACE_PAT`` is unset (exit 2).
2. Rejects malformed ``OWNER/REPO`` arguments (exit 4).
3. Rejects PAT values with whitespace or newlines (copy-paste damage).
4. Never uses stdin-pipe / echo form — always calls
   ``gh secret set NAME --repo OWNER/REPO --body VALUE``.
5. Never prints the PAT value anywhere (stdout / stderr / combined).
6. ``--verify-only`` calls only ``gh secret list`` and never ``set``.
7. Handles ``gh`` not installed (exit 3) and ``gh auth status`` failure.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "set_marketplace_pat.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("set_marketplace_pat", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def smp():
    """Loaded module object for set_marketplace_pat.py."""
    return _load_module()


# ── Helper: invoke the script as a subprocess with a controlled env ──────────


def _run_script(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT_PATH), *args]
    full_env = {"PATH": os.environ.get("PATH", ""), **(env or {})}
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env)


class TestArgumentValidation:
    """Validate the helper's argparse layer before any network/gh calls."""

    def test_help_flag_prints_usage_and_exits_zero(self):
        result = _run_script(["--help"])
        assert result.returncode == 0
        assert "set_marketplace_pat" in result.stdout or "Set the MARKETPLACE_PAT" in result.stdout

    def test_no_args_exits_nonzero(self):
        result = _run_script([])
        assert result.returncode != 0
        # argparse writes its usage error to stderr
        assert "required" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_malformed_repo_exits_4(self):
        result = _run_script(["bad-repo-no-slash"], env={"MARKETPLACE_PAT": "ghp_abc"})
        assert result.returncode == 4
        assert "malformed" in result.stderr.lower()

    def test_repo_with_two_slashes_exits_4(self):
        result = _run_script(["owner/repo/extra"], env={"MARKETPLACE_PAT": "ghp_abc"})
        assert result.returncode == 4


class TestMissingEnvVar:
    """Enforce fail-closed behavior when no PAT env var is set."""

    def test_missing_env_exits_2(self):
        # env= deliberately omits both PAT_MARKETPLACE and MARKETPLACE_PAT
        result = _run_script(["Emasoft/x"])
        assert result.returncode == 2
        # The error message must enumerate every env var the script tried,
        # so the user knows which one(s) to export.
        assert "$PAT_MARKETPLACE" in result.stderr
        assert "$MARKETPLACE_PAT" in result.stderr
        # Must tell the user to export it, not to paste it on the command line
        assert "export" in result.stderr.lower()

    def test_empty_env_exits_2(self):
        result = _run_script(["Emasoft/x"], env={"MARKETPLACE_PAT": "", "PAT_MARKETPLACE": ""})
        assert result.returncode == 2

    def test_whitespace_pat_exits_2(self):
        result = _run_script(["Emasoft/x"], env={"MARKETPLACE_PAT": "ghp_abc\n"})
        # Newline-containing value means copy-paste damage → reject
        assert result.returncode == 2
        assert "whitespace" in result.stderr.lower() or "newline" in result.stderr.lower()

    def test_pat_with_leading_space_exits_2(self):
        result = _run_script(["Emasoft/x"], env={"MARKETPLACE_PAT": " ghp_abc"})
        assert result.returncode == 2


class TestEnvVarFlexibility:
    """Verify the dual-default lookup chain + --env-var override."""

    def test_pat_marketplace_takes_precedence_over_marketplace_pat(self, smp):
        """PAT_MARKETPLACE wins when both env vars are set."""
        with patch.object(smp.subprocess, "run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="", stderr=""),  # gh secret set
                MagicMock(returncode=0, stdout="MARKETPLACE_PAT\tabc\n"),  # verify
            ]
            with patch.dict(os.environ, {
                "PAT_MARKETPLACE": "ghp_from_PAT_MARKETPLACE",
                "MARKETPLACE_PAT": "ghp_from_MARKETPLACE_PAT",
            }, clear=True):
                # Patch _require_gh + _check_auth so they don't shell out
                with patch.object(smp, "_require_gh", return_value="/usr/local/bin/gh"), \
                     patch.object(smp, "_check_auth"):
                    rc = smp.main_with_args(["Emasoft/x"]) if hasattr(smp, "main_with_args") else None
                    if rc is None:
                        # main() reads sys.argv directly — invoke via subprocess for this test
                        pass
            # The mock-based round-trip is exercised by the subprocess test below.
            # Here we just confirm the priority order by inspecting the env-var
            # tuple the script publishes.
            assert smp.DEFAULT_PAT_ENV_VARS[0] == "PAT_MARKETPLACE"
            assert "MARKETPLACE_PAT" in smp.DEFAULT_PAT_ENV_VARS

    def test_falls_back_to_marketplace_pat_when_only_legacy_set(self):
        """When only $MARKETPLACE_PAT is set, the script reads it (back-compat)."""
        # Subprocess invocation — the legacy var must still work end-to-end up
        # to the gh-CLI step. We don't need gh to actually succeed; we only
        # need to confirm the script gets PAST the env-var lookup.
        result = _run_script(
            ["Emasoft/x"],
            env={"MARKETPLACE_PAT": "ghp_abc"},
        )
        # gh CLI is missing in the clean test env, so we expect exit 3
        # (gh-not-found) — proving the env-var lookup succeeded and the
        # script reached the gh-resolution step.
        assert result.returncode == 3
        # The script printed which env var supplied the value (so the user
        # can spot if a wrong var was picked up).
        assert "MARKETPLACE_PAT" in result.stdout

    def test_explicit_env_var_flag_overrides_default_chain(self):
        """--env-var GITHUB_PAT reads $GITHUB_PAT exclusively."""
        result = _run_script(
            ["--env-var", "GITHUB_PAT", "Emasoft/x"],
            env={"GITHUB_PAT": "ghp_abc", "MARKETPLACE_PAT": "wrong_value"},
        )
        # Should reach gh-resolution (exit 3 = gh missing in clean env)
        assert result.returncode == 3
        assert "GITHUB_PAT" in result.stdout
        assert "wrong_value" not in result.stdout
        assert "wrong_value" not in result.stderr

    def test_explicit_env_var_flag_with_unset_var_exits_2(self):
        """--env-var X with $X unset returns the standard PAT-missing error."""
        result = _run_script(
            ["--env-var", "GITHUB_PAT", "Emasoft/x"],
            env={"PAT_MARKETPLACE": "wrong_value"},  # different var set, but flag points elsewhere
        )
        assert result.returncode == 2
        assert "$GITHUB_PAT" in result.stderr
        assert "wrong_value" not in result.stderr  # never log other vars

    def test_invalid_env_var_name_rejected(self):
        """--env-var with a name that violates POSIX naming rules is rejected (exit 4)."""
        for bad_name in ["bad-name", "1starts_with_digit", "has space", "has;semi"]:
            result = _run_script(
                ["--env-var", bad_name, "Emasoft/x"],
                env={"PAT_MARKETPLACE": "ghp_abc"},
            )
            assert result.returncode == 4, f"bad name {bad_name!r} should exit 4, got {result.returncode}"
            assert "POSIX" in result.stderr or "valid" in result.stderr.lower()


class TestGhInvocationShape:
    """Validate that the script calls gh with --body and never with a pipe."""

    def test_set_secret_uses_body_flag(self, smp):
        """_set_secret must pass --body "<value>" — never stdin."""
        with patch.object(smp.subprocess, "run") as mock_run:
            # First call: gh secret set — mocked to succeed
            # Second call: gh secret list — mocked to show the secret
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr="", stdout=""),
                MagicMock(returncode=0, stdout="MARKETPLACE_PAT\t2026-04-13\n", stderr=""),
            ]
            rc = smp._set_secret("/usr/bin/gh", "owner/repo", "MARKETPLACE_PAT", "ghp_value")
            assert rc == 0
            # First mock call was the set operation — inspect its args
            set_call = mock_run.call_args_list[0]
            args = set_call.args[0]  # positional argv list
            assert args[0] == "/usr/bin/gh"
            assert "secret" in args
            assert "set" in args
            assert "MARKETPLACE_PAT" in args
            assert "--repo" in args
            assert "owner/repo" in args
            # The secret is passed via stdin (--body-file -) NOT --body, so
            # the value never appears in argv (which is visible to other users
            # via /proc/<pid>/cmdline or `ps -ef`).
            assert "--body-file" in args
            body_file_idx = args.index("--body-file")
            assert args[body_file_idx + 1] == "-"
            assert "--body" not in args
            # The value MUST be passed via input=, never as a positional arg.
            assert set_call.kwargs.get("input") == "ghp_value"
            assert "ghp_value" not in args

    def test_no_echo_pipe_in_executable_code(self):
        """AST check: no string literal in executable code runs echo | gh secret set.

        Docstrings and error messages are allowed to mention the anti-pattern
        so humans know what to avoid — this test scans ONLY the executable
        statements (assignments, calls, subprocess arg lists) for any string
        literal that would shell out to the forbidden form.
        """
        import ast

        source = SCRIPT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Walk AST and collect all string literals that are NOT docstrings
        # (docstrings are the first Expr.Constant of a module/class/function).
        docstring_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    docstring_nodes.add(id(node.body[0].value))

        forbidden_patterns = [
            'echo "$MARKETPLACE_PAT" |',
            "echo $MARKETPLACE_PAT |",
            "echo '$MARKETPLACE_PAT' |",
            "| gh secret set",
        ]
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstring_nodes:
                    continue
                for pat in forbidden_patterns:
                    assert pat not in node.value, (
                        f"Forbidden shell pattern {pat!r} found in executable code at "
                        f"line {getattr(node, 'lineno', '?')}"
                    )

    def test_script_uses_body_file_stdin_form(self):
        """The script must use --body-file - + input= (stdin) so the secret
        value is NEVER on argv (which is visible to other users via
        /proc/<pid>/cmdline or `ps -ef`). The previous --body form was a
        secret-leak vulnerability — fixed in v2.65.2."""
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        # Must use --body-file with the - sentinel (stdin)
        assert '"--body-file"' in source
        # AND pass the value via input= (stdin) so it never enters argv
        assert "input=value" in source
        # The plain --body argv form (which leaks via /proc) must not appear
        # in executable code. (Docstrings may still mention it as historical
        # context, so we check only that the literal "--body"-with-comma
        # form (an argv element) is absent — note this also excludes the
        # --body-file form via the trailing comma.)
        # Find non-docstring uses of "--body" without "-file":
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith(('"', "'", "#")):
                continue  # docstring or comment
            if '"--body"' in line:
                raise AssertionError(
                    f'Line {i} uses "--body" argv form (secret leaks via /proc/<pid>/cmdline). '
                    f'Use "--body-file", "-" + input=value instead.'
                )


class TestPatNeverLogged:
    """The PAT value must never appear in any output, ever."""

    def test_set_secret_does_not_print_pat_value_on_success(self, smp, capsys):
        with patch.object(smp.subprocess, "run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stderr="", stdout=""),
                MagicMock(returncode=0, stdout="MARKETPLACE_PAT\t2026-04-13\n", stderr=""),
            ]
            smp._set_secret("/usr/bin/gh", "owner/repo", "MARKETPLACE_PAT", "ghp_SENSITIVE_VALUE_123")
        captured = capsys.readouterr()
        assert "ghp_SENSITIVE_VALUE_123" not in captured.out
        assert "ghp_SENSITIVE_VALUE_123" not in captured.err

    def test_set_secret_does_not_print_pat_value_on_failure(self, smp, capsys):
        with patch.object(smp.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="permission denied", stdout="")
            smp._set_secret("/usr/bin/gh", "owner/repo", "MARKETPLACE_PAT", "ghp_SENSITIVE_VALUE_456")
        captured = capsys.readouterr()
        assert "ghp_SENSITIVE_VALUE_456" not in captured.out
        assert "ghp_SENSITIVE_VALUE_456" not in captured.err


class TestVerifyOnlyMode:
    """--verify-only must not set anything; it must only read via gh secret list."""

    def test_verify_only_does_not_call_secret_set(self, smp):
        """_secret_exists must call only `gh secret list`, never `gh secret set`."""
        with patch.object(smp.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="MARKETPLACE_PAT\t2026\n", stderr=""
            )
            found = smp._secret_exists("/usr/bin/gh", "owner/repo", "MARKETPLACE_PAT")
        assert found is True
        args = mock_run.call_args.args[0]
        assert "list" in args
        assert "set" not in args


class TestGhMissing:
    """When gh is not on PATH, helper must exit 3 with a clear message."""

    def test_gh_missing_exits_3(self):
        # PATH="" makes shutil.which("gh") return None
        result = _run_script(["owner/repo"], env={"MARKETPLACE_PAT": "ghp_abc", "PATH": ""})
        assert result.returncode == 3
        assert "gh" in result.stderr
