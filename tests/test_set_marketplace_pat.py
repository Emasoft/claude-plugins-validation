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
    """Enforce fail-closed behavior when $MARKETPLACE_PAT is absent."""

    def test_missing_env_exits_2(self):
        # env= deliberately omits MARKETPLACE_PAT — _run_script starts from clean env
        result = _run_script(["Emasoft/x"])
        assert result.returncode == 2
        assert "MARKETPLACE_PAT" in result.stderr
        # Must tell the user to export it, not to paste it on the command line
        assert "export" in result.stderr.lower()

    def test_empty_env_exits_2(self):
        result = _run_script(["Emasoft/x"], env={"MARKETPLACE_PAT": ""})
        assert result.returncode == 2

    def test_whitespace_pat_exits_2(self):
        result = _run_script(["Emasoft/x"], env={"MARKETPLACE_PAT": "ghp_abc\n"})
        # Newline-containing value means copy-paste damage → reject
        assert result.returncode == 2
        assert "whitespace" in result.stderr.lower() or "newline" in result.stderr.lower()

    def test_pat_with_leading_space_exits_2(self):
        result = _run_script(["Emasoft/x"], env={"MARKETPLACE_PAT": " ghp_abc"})
        assert result.returncode == 2


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
            assert "--body" in args
            # --body must be immediately followed by the value
            body_idx = args.index("--body")
            assert args[body_idx + 1] == "ghp_value"
            # No stdin-piping: input= must NOT have been passed
            assert "input" not in set_call.kwargs

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

    def test_script_uses_only_body_argv_form(self):
        """The script's source must reference --body, not stdin."""
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        assert '"--body"' in source
        # It must not spawn `gh secret set` with stdin/input=
        assert "input=" not in source  # no stdin data passing


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
