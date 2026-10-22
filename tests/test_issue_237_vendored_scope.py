"""Task G review items 4b-4e, 4j, 4k — scope of the tracked-only vendored skip.

Owner policy: "everything that is git tracked must be scanned, no exceptions".
Every case is two-sided where a skip still exists: the hardened shape is scanned,
and the legitimate untracked shape is still skipped.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_validation_common as cvc  # noqa: E402

# Built at runtime so this file never carries a literal home path itself.
_LEAK = "/" + "/".join(("Users", "alicedev", "secret", "notes.txt"))


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _plugin(root: Path) -> Path:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "p"}), encoding="utf-8")
    return root


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _abs_findings(root: Path) -> list[str]:
    report = cvc.ValidationReport()
    cvc.validate_no_absolute_paths(root, report, respect_gitignore=False)
    return [r.file or "" for r in report.results if r.level in ("MAJOR", "MINOR") and "Absolute path" in r.message]


# ── 4b: a plugin inside an unrelated parent repo ──────────────────────────────


def test_plugin_inside_unrelated_parent_repo_scans_vendored_dir(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init", "-q")
    _write(parent, "README.md", "x\n")
    _git(parent, "add", "README.md")
    plug = _plugin(parent / "plug")
    _write(plug, "external/x.md", f"see {_LEAK}\n")
    # plugin.json is NOT tracked by the parent: the parent index proves nothing.
    assert "external/x.md" in _abs_findings(plug)
    assert not cvc.is_vendored_path(Path("external/x.md"), plug)


def test_plugin_tracked_inside_parent_repo_uses_its_index(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    _git(parent, "init", "-q")
    plug = _plugin(parent / "plug")
    _git(parent, "add", "plug/.claude-plugin/plugin.json")
    _write(plug, "external/x.md", f"see {_LEAK}\n")
    # Accepted repo (manifest tracked): untracked external/ content is skipped.
    assert cvc.is_vendored_path(Path("external/x.md"), plug)
    _git(parent, "add", "plug/external/x.md")
    assert not cvc.is_vendored_path(Path("external/x.md"), plug)


# ── 4c: the git environment cannot be steered ────────────────────────────────


def test_git_index_file_env_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _plugin(tmp_path / "p")
    _git(root, "init", "-q")
    _write(root, "external/x.md", "x\n")
    _git(root, "add", "external/x.md")
    empty = tmp_path / "empty.idx"
    _git(root, "read-tree", "--empty", f"--index-output={empty}")
    monkeypatch.setenv("GIT_INDEX_FILE", str(empty))
    assert not cvc.is_vendored_path(Path("external/x.md"), root)


# ── 4d: absolute and parent-relative paths are never vendored ────────────────


@pytest.mark.parametrize("rel", ["../external/x", "/abs/node_modules/x", "external/../../node_modules/x"])
def test_abs_and_dotdot_never_vendored(tmp_path: Path, rel: str) -> None:
    root = _plugin(tmp_path / "p")
    _git(root, "init", "-q")
    assert not cvc.is_vendored_path(Path(rel), root)
    assert not cvc.is_untracked_in_git(Path(rel), root)
    assert cvc.is_untracked_in_git(Path("research"), root)  # control: untracked, relative
    assert cvc.is_vendored_path(Path("node_modules/x"), root)  # control: untracked, relative


def test_toc_reference_reanchored_on_plugin_root(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "p")
    _git(root, "init", "-q")
    skill = root / "skills" / "s"
    skill.mkdir(parents=True)
    # A reference inside the skill dir to an untracked vendored subtree is skipped;
    # one that climbs out with `..` is not; outside any plugin nothing is skipped.
    assert cvc._ref_is_vendored("node_modules/pkg/x.md", skill)
    assert not cvc._ref_is_vendored("../../node_modules/pkg/x.md", skill)
    # An absolute reference into the plugin's own vendored dir is still scanned.
    assert not cvc._ref_is_vendored(str(root / "node_modules" / "pkg" / "x.md"), skill)
    assert not cvc._ref_is_vendored("node_modules/x.md", tmp_path)


# ── 4e: no security scanner consults the vendored skip ───────────────────────


def test_security_entry_points_never_call_is_vendored_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("validate_security.py", "cpv_skillaudit_native.py"):
        src = (SCRIPTS_DIR / name).read_text(encoding="utf-8")
        assert "is_vendored_path" not in src and "_ref_is_vendored" not in src, name
    for fn in (cvc.scan_file_for_private_info, cvc.scan_directory_for_private_info):
        src = inspect.getsource(fn)
        assert "is_vendored_path" not in src and "_ref_is_vendored" not in src, fn.__name__

    def _boom(*_a, **_k):
        raise AssertionError("a security scanner called is_vendored_path")

    monkeypatch.setattr(cvc, "is_vendored_path", _boom)
    root = _plugin(tmp_path / "p")
    _git(root, "init", "-q")
    _write(root, "external/leak.md", f"path {_LEAK}\n")  # untracked vendored dir
    report = cvc.ValidationReport()
    cvc.scan_directory_for_private_info(root, report, respect_gitignore=False)
    assert any(r.level in ("CRITICAL", "MAJOR") and (r.file or "").startswith("external/") for r in report.results)


# ── 4j: only the username segment decides "regex" ────────────────────────────


@pytest.mark.parametrize("tail", ["projects/*", "report(1).pdf"])
def test_regex_looking_tail_still_flagged(tmp_path: Path, tail: str) -> None:
    path = tmp_path / "run.sh"
    path.write_text("cat /" + "Users/alice/" + tail + "\n", encoding="utf-8")
    assert cvc.scan_file_for_absolute_paths(path, cvc.ValidationReport(), "run.sh") >= 1
    assert cvc.scan_file_for_private_info(path, cvc.ValidationReport(), "run.sh") >= 1


@pytest.mark.parametrize("pattern", ["/home/[^/]+/", "/home/[^/\\s]+/"])
def test_regex_username_segment_not_flagged(tmp_path: Path, pattern: str) -> None:
    path = tmp_path / "run.py"
    path.write_text(f'PAT = r"{pattern}"\n', encoding="utf-8")
    assert cvc.scan_file_for_absolute_paths(path, cvc.ValidationReport(), "run.py") == 0
    assert cvc.scan_file_for_private_info(path, cvc.ValidationReport(), "run.py") == 0


# ── 4k: CI/system homes: absolute-path rule flags, private-info rule does not ─


@pytest.mark.parametrize("home", ["/home/runner/work/x/notes", "/root/.config/x/notes"])
def test_system_homes(tmp_path: Path, home: str) -> None:
    path = tmp_path / "run.sh"
    path.write_text(f"cat {home}\n", encoding="utf-8")
    assert cvc.scan_file_for_absolute_paths(path, cvc.ValidationReport(), "run.sh") >= 1
    assert cvc.scan_file_for_private_info(path, cvc.ValidationReport(), "run.sh") == 0


# ── Windows homes and `<name>` placeholders reach the username checks ────────
# 4j stopped the regex skip from swallowing `C:\Users\...`, so the username is
# extracted across `\` too and placeholders are skipped; real logins still flag.


@pytest.mark.parametrize("text", ["/Users/<name>/notes", "C:\\Users\\username\\notes", "C:\\Users\\<name>\\notes"])
def test_home_placeholders_not_flagged(tmp_path: Path, text: str) -> None:
    path = tmp_path / "run.md"
    path.write_text(f"see {text} here\n", encoding="utf-8")
    assert cvc.scan_file_for_absolute_paths(path, cvc.ValidationReport(), "run.md") == 0


@pytest.mark.parametrize("text", ["/Users/alice/notes", "C:\\Users\\alice\\notes"])
def test_real_home_logins_flagged(tmp_path: Path, text: str) -> None:
    path = tmp_path / "run.md"
    path.write_text(f"see {text} here\n", encoding="utf-8")
    assert cvc.scan_file_for_absolute_paths(path, cvc.ValidationReport(), "run.md") >= 1


# ── 4a: a .gitignore'd root dir only skips RC-NONSTD-DIR-001 when git proves it untracked ─


@pytest.mark.parametrize("track", [True, False])
def test_gitignored_root_dir_skips_only_untracked(tmp_path: Path, track: bool) -> None:
    import validate_plugin as vp

    root = _plugin(tmp_path / "p")
    _write(root, ".gitignore", "research/\n")
    _write(root, "research/n.md", "x")
    _git(root, "init", "-q")
    _git(root, "add", ".claude-plugin/plugin.json", ".gitignore")
    if track:
        _git(root, "add", "-f", "research/n.md")
    report = cvc.ValidationReport()
    vp.validate_structure(root, report)
    hits = [r for r in report.results if "RC-NONSTD-DIR-001" in r.message and "'research/'" in r.message]
    assert bool(hits) is track
