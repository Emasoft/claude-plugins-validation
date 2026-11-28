"""Issue #296: ci-preflight cspell runs on the tracked files only; shfmt selects what Mega-Linter's BASH_SHFMT selects."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from cpv_ci_preflight import _CSPELL_LINTER_ID, PreflightResult, _gate_cspell, _gate_shfmt, _shfmt_shell_paths

# Mega-Linter v8.8.0, megalinter/descriptors/bash.megalinter-descriptor.yml. BASH_SHFMT has no
# file filter of its own, so it inherits the BASH descriptor's:
#   file_extensions: ["", ".sh", ".bash", ".dash", ".ksh"]
#   file_contains_regex_extensions: [""]
#   file_contains_regex: ["^#!/usr/bin/env bash", "^#!/bin/bash", "^#!/bin/sh"]
# and `filter_files` in megalinter/utils.py applies that content regex only to extensionless files.
# So `#!/usr/bin/env sh` (the canon git-hooks/pre-push) is NOT selected, while `#!/bin/bash` is.
CANON_PRE_PUSH = "#!/usr/bin/env sh\nset -eu\necho ok\n"
BASH_EXTENSIONLESS = "#!/bin/bash\necho ok\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=60,
    )


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _shell_repo(root: Path) -> None:
    """Real git repo: tracked scripts, plus an untracked gitignored `scripts_dev/y.sh`."""
    _git(root, "init", "-q")
    _write(root, ".gitignore", "scripts_dev/\n")
    _write(root, "scripts/x.sh", "#!/bin/sh\necho ok\n")
    _write(root, "scripts/z.ksh", "echo ok\n")
    _write(root, "git-hooks/pre-push", CANON_PRE_PUSH)
    _write(root, "bin/tool", BASH_EXTENSIONLESS)
    _git(root, "add", ".gitignore", "scripts", "git-hooks", "bin")
    _git(root, "commit", "-q", "-m", "init")
    _write(root, "scripts_dev/y.sh", "#!/bin/sh\necho ok\n")


def test_shfmt_excludes_extensionless_env_sh_hook(tmp_path: Path) -> None:
    """An extensionless `#!/usr/bin/env sh` git-hooks/pre-push is not selected: no BASH_SHFMT content regex matches it."""
    _shell_repo(tmp_path)
    assert "git-hooks/pre-push" not in _shfmt_shell_paths(tmp_path)


def test_shfmt_includes_tracked_sh_script(tmp_path: Path) -> None:
    """A tracked `scripts/x.sh` is selected, as are `.ksh` and an extensionless `#!/bin/bash` file."""
    _shell_repo(tmp_path)
    selected = _shfmt_shell_paths(tmp_path)
    assert "scripts/x.sh" in selected
    assert "scripts/z.ksh" in selected
    assert "bin/tool" in selected


def test_shfmt_excludes_untracked_gitignored_script(tmp_path: Path) -> None:
    """An untracked gitignored `scripts_dev/y.sh` is not selected: CI's clean checkout never has it."""
    _shell_repo(tmp_path)
    assert "scripts_dev/y.sh" not in _shfmt_shell_paths(tmp_path)


@pytest.mark.skipif(shutil.which("shfmt") is None, reason="shfmt binary not installed")
def test_real_shfmt_gate_ignores_unformatted_ignored_and_env_sh_files(tmp_path: Path) -> None:
    """The shfmt gate PASSes when only an unselected env-sh hook and an ignored script are badly formatted."""
    _shell_repo(tmp_path)
    ugly = "#!/usr/bin/env sh\nif true; then\n        echo   ok\nfi\n"
    _write(tmp_path, "git-hooks/pre-push", ugly)
    _write(tmp_path, "scripts_dev/y.sh", ugly)
    result = PreflightResult(plugin_path=tmp_path)
    _gate_shfmt(result, {"BASH_SHFMT"})
    assert result.exit_code == 0, [f.message for f in result.findings]


def _cspell_repo(root: Path) -> None:
    """Real git repo with a tracked cspell config and a gitignored scratch file holding an unknown word."""
    _git(root, "init", "-q")
    _write(root, ".gitignore", "scripts_dev/\n")
    _write(root, "cspell.json", '{"version": "0.2", "language": "en"}\n')
    _write(root, "README.md", "Hello world.\n")
    _git(root, "add", ".gitignore", "cspell.json", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _write(root, "scripts_dev/scratch.txt", "zzqxplorfwibble\n")


def test_cspell_runs_in_a_snapshot_without_ignored_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cspell executable is started with a cwd that holds the tracked cspell.json and no scripts_dev."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _cspell_repo(repo)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "cwd.txt"
    # A real executable standing in for cspell: it records where it was started and what is there.
    fake = bin_dir / "cspell"
    fake.write_text(
        f'#!/bin/sh\n{{ pwd -P; ls -A; }} > "{record}"\nexit 0\n',
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = PreflightResult(plugin_path=repo)
    _gate_cspell(result, {_CSPELL_LINTER_ID})

    assert result.exit_code == 0, [f.message for f in result.findings]
    lines = record.read_text(encoding="utf-8").splitlines()
    assert Path(lines[0]).resolve() != repo.resolve()
    assert "cspell.json" in lines[1:]
    assert "scripts_dev" not in lines[1:]


@pytest.mark.skipif(shutil.which("cspell") is None, reason="cspell binary not installed")
def test_real_cspell_gate_passes_despite_unknown_word_in_ignored_scratch(tmp_path: Path) -> None:
    """With real cspell, an unknown word in a gitignored file fails a run in place but not the gate."""
    _cspell_repo(tmp_path)
    in_place = subprocess.run(
        ["cspell", "lint", "--no-progress", "--no-summary", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert in_place.returncode != 0, "control: cspell must see the scratch file when run in place"

    result = PreflightResult(plugin_path=tmp_path)
    _gate_cspell(result, {_CSPELL_LINTER_ID})
    assert result.exit_code == 0, [f.message for f in result.findings]
