"""Issue #239 half A: `gh secret list` must be pinned to the origin repo with --repo."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish  # noqa: E402
from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", "git@github.com:acme/widget.git"], check=True)
    return root


def _fake_gh(tmp_path: Path, stdout: str, exit_code: int) -> tuple[Path, Path]:
    """Write a fake gh that records argv to a file and prints `stdout`."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "argv.txt"
    gh = bindir / "gh"
    gh.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\nprintf '%b' {stdout!r}\nexit {exit_code}\n")
    gh.chmod(0o755)
    return gh, log


def _generated_helper():
    """Exec the generated publish.py `_gh_secret_exists` + `_current_repo_slug` helpers."""
    src = gen_publish_py(PluginParams(name="t239", description="d", author="Emasoft", author_email="t@e.com"))
    tree = ast.parse(src)
    wanted = [
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_gh_secret_exists", "_current_repo_slug")
    ]
    assert {n.name for n in wanted} == {"_gh_secret_exists", "_current_repo_slug"}
    ns: dict = {"Path": Path, "shutil": shutil, "subprocess": subprocess, "re": __import__("re")}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), "<generated>", "exec"), ns)  # noqa: S102
    return src, ns["_gh_secret_exists"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _make_repo(tmp_path)


def test_publish_passes_repo_and_finds_secret(repo, tmp_path):
    """publish._gh_secret_exists passes --repo acme/widget and returns True when listed."""
    gh, log = _fake_gh(tmp_path, "OTHER\nMARKETPLACE_PAT\n", 0)
    assert publish._gh_secret_exists(repo, "MARKETPLACE_PAT", gh_bin=str(gh)) is True
    assert "--repo acme/widget" in log.read_text()


def test_publish_absent_secret_is_false(repo, tmp_path):
    """publish._gh_secret_exists returns False when gh answers without the secret."""
    gh, _ = _fake_gh(tmp_path, "OTHER\n", 0)
    assert publish._gh_secret_exists(repo, "MARKETPLACE_PAT", gh_bin=str(gh)) is False


def test_publish_gh_failure_is_none(repo, tmp_path):
    """publish._gh_secret_exists returns None (cannot-check) when gh exits non-zero."""
    gh, _ = _fake_gh(tmp_path, "", 1)
    assert publish._gh_secret_exists(repo, "MARKETPLACE_PAT", gh_bin=str(gh)) is None


def test_publish_no_origin_is_none(tmp_path):
    """With no origin remote there is no slug, so the helper cannot check and returns None."""
    root = tmp_path / "bare"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    gh, log = _fake_gh(tmp_path, "MARKETPLACE_PAT\n", 0)
    assert publish._gh_secret_exists(root, "MARKETPLACE_PAT", gh_bin=str(gh)) is None
    assert not log.exists()


def test_generated_helper_compiles_and_is_three_valued(repo, tmp_path, monkeypatch):
    """The generated helper has --repo, compiles, and returns True/False/None like publish.py."""
    src, helper = _generated_helper()
    compile(src, "<generated publish.py>", "exec")
    assert '"--repo", slug' in src
    gh, log = _fake_gh(tmp_path, "MARKETPLACE_PAT\tUpdated\n", 0)
    monkeypatch.setenv("PATH", f"{gh.parent}{os.pathsep}{os.environ['PATH']}")
    assert helper(repo, "MARKETPLACE_PAT") is True
    assert "--repo acme/widget" in log.read_text()
    assert helper(repo, "NOPE") is False
    (gh.parent / "gh").write_text("#!/bin/sh\nexit 1\n")
    assert helper(repo, "MARKETPLACE_PAT") is None


def test_generated_caller_blocks_on_none():
    """The generated marketplace stage exits on a None (unverified) secret state."""
    src, _ = _generated_helper()
    assert 'pat_state = _gh_secret_exists(root, "MARKETPLACE_PAT")' in src
    assert "if pat_state is None:" in src
    assert src.index("if pat_state is None:") < src.index("if not pat_state:")
