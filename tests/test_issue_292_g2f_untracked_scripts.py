#!/usr/bin/env python3
"""Issue #292: canon publish.py gate G2f must shellcheck only git-TRACKED scripts.

A gitignored, untracked broken script (e.g. under scripts_dev/) is invisible to CI
and every other gate, and must not block a push. A tracked broken script must still
fail the gate, even when it lives under an ignored directory.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402

CLEAN_SH = "#!/bin/sh\necho hello\n"
# SC2086 (unquoted variable) is a shellcheck finding under the default severity.
BROKEN_SH = "#!/bin/sh\necho $x\n"


def _load_generated_publish(tmp_path: Path) -> ModuleType:
    """Write the generated publish.py to disk and import it (the main guard keeps import side-effect free)."""
    params = PluginParams(
        name="p", description="d", author="a", author_email="a@example.com", license="MIT",
        python_version="3.12", github_owner="o", marketplace="m", version="0.1.0",
    )
    path = tmp_path / "generated_publish.py"
    path.write_text(gen_publish_py(params), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("generated_publish_292", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, check=True, capture_output=True, timeout=60,
    )


def _candidates(repo: Path) -> list[Path]:
    return sorted(list(repo.rglob("*.sh")) + list(repo.rglob("*.bash")))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / ".gitignore").write_text("scripts_dev/\n")
    (r / "scripts").mkdir()
    (r / "scripts" / "ok.sh").write_text(CLEAN_SH)
    (r / "scripts_dev").mkdir()
    (r / "scripts_dev" / "scratch.sh").write_text(BROKEN_SH)  # ignored + untracked
    (r / "new_untracked.bash").write_text(BROKEN_SH)  # untracked, not ignored
    _git(r, "add", ".gitignore", "scripts/ok.sh")
    _git(r, "commit", "-q", "-m", "init")
    return r


def test_ignored_and_untracked_scripts_are_dropped(tmp_path: Path, repo: Path) -> None:
    """Only the tracked clean script survives; the ignored and the untracked broken ones are excluded."""
    mod = _load_generated_publish(tmp_path)
    kept = mod._git_tracked_only(repo, _candidates(repo))
    assert kept == [repo / "scripts" / "ok.sh"]


def test_tracked_script_under_ignored_dir_is_never_skipped(tmp_path: Path, repo: Path) -> None:
    """A force-added script inside an ignored directory is tracked, so it is still linted."""
    _git(repo, "add", "-f", "scripts_dev/scratch.sh")
    _git(repo, "commit", "-q", "-m", "track scratch")
    mod = _load_generated_publish(tmp_path)
    kept = mod._git_tracked_only(repo, _candidates(repo))
    assert repo / "scripts_dev" / "scratch.sh" in kept


def test_not_a_git_repo_returns_none_so_gate_blocks(tmp_path: Path) -> None:
    """git ls-files failing yields None (the gate then BLOCKS) instead of a guess."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.sh").write_text(CLEAN_SH)
    mod = _load_generated_publish(tmp_path)
    assert mod._git_tracked_only(plain, _candidates(plain)) is None


def test_gate_source_filters_before_shellcheck_and_blocks_on_git_failure(tmp_path: Path) -> None:
    """The G2f block must call the helper before shellcheck and return 1 when it yields None."""
    src = _load_generated_publish(tmp_path).__dict__["run_gate"].__code__.co_names
    assert "_git_tracked_only" in src
    text = (tmp_path / "generated_publish.py").read_text(encoding="utf-8")
    g2f = text.split("[G2f]")[1].split("[G3]")[0]
    assert g2f.index("_git_tracked_only") < g2f.index('"shellcheck"')
    assert "is None:" in g2f and "return 1" in g2f.split("is None:")[1].split("if not _shell_scripts")[0]


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_verdict_follows_tracked_population(tmp_path: Path, repo: Path) -> None:
    """Red-green: shellcheck on the filtered set passes with only untracked junk, fails once a broken script is tracked."""
    mod = _load_generated_publish(tmp_path)

    def verdict() -> int:
        kept = mod._git_tracked_only(repo, _candidates(repo))
        return subprocess.run(["shellcheck", *map(str, kept)], cwd=repo, capture_output=True, timeout=60).returncode

    assert verdict() == 0
    (repo / "scripts" / "bad.sh").write_text(BROKEN_SH)
    _git(repo, "add", "scripts/bad.sh")
    _git(repo, "commit", "-q", "-m", "add broken")
    assert verdict() != 0
