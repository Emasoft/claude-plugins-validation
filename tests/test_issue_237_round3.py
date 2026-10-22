"""Task G round 3 — coordinator review items (issues #237 / #257).

Every precision change is tested on both sides: the hardened shape is scanned
(fail toward flagging) and the legitimate shape keeps its old result.
"""

from __future__ import annotations

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


def _scan_one(tmp_path: Path, text: str) -> tuple[int, int]:
    path = tmp_path / "run.md"
    path.write_text(f"see {text} here\n", encoding="utf-8")
    return (
        cvc.scan_file_for_absolute_paths(path, cvc.ValidationReport(), "run.md"),
        cvc.scan_file_for_private_info(path, cvc.ValidationReport(), "run.md"),
    )


# ── item 1: security-doc placeholders ────────────────────────────────────────


@pytest.mark.parametrize("login", ["victim", "attacker"])
def test_security_doc_placeholders_not_flagged(tmp_path: Path, login: str) -> None:
    assert _scan_one(tmp_path, "/" + "Users/" + login + "/.ssh/id_rsa") == (0, 0)


def test_real_login_beside_placeholders_still_flagged(tmp_path: Path) -> None:
    abs_hits, private_hits = _scan_one(tmp_path, "/" + "Users/alice/.ssh/id_rsa")
    assert abs_hits >= 1
    assert private_hits >= 1


# ── item 3: load_strip_config drops the inert allowlist keys ──────────────────


def test_load_strip_config_drops_inert_allowlist_keys(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / ".claude-plugin").mkdir(parents=True)
    strip = {"extract": [], "keep_dev_configs": False, "allowed_submodule_urls": ["x"], "require_url_allowlist": False}
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "p", "cpv": {"strip": strip}}), "utf-8")
    assert cvc.load_strip_config(root) == {"extract": [], "keep_dev_configs": False}


# ── item 4: the negative git-index lookup is cached ───────────────────────────


def test_negative_git_index_lookup_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = cvc._probe_git_index_path

    def _counting(root_str: str):
        calls.append(root_str)
        return real(root_str)

    monkeypatch.setattr(cvc, "_probe_git_index_path", _counting)
    root = str(_plugin(tmp_path / "p"))
    assert cvc._git_index_path(root) is None
    assert cvc._git_index_path(root) is None
    assert calls == [root]
    # A negative still skips nothing: a non-git vendored path is scanned.
    assert not cvc.is_vendored_path(Path("node_modules/x.md"), Path(root))


# ── item 5: root-dir exemption at the RC-NONSTD-DIR-001 call site only ────────


def _nonstd(root: Path, dirname: str) -> bool:
    import validate_plugin as vp

    report = cvc.ValidationReport()
    vp.validate_structure(root, report)
    return any("RC-NONSTD-DIR-001" in r.message and f"'{dirname}/'" in r.message for r in report.results)


@pytest.mark.parametrize("vdir", ["vendor", "third_party"])
def test_tracked_vendor_dir_exempt_but_scanned(tmp_path: Path, vdir: str) -> None:
    root = _plugin(tmp_path / "p")
    _write(root, f"{vdir}/lib.md", f"cat {_LEAK}\n")
    _git(root, "init", "-q")
    _git(root, "add", ".claude-plugin/plugin.json", f"{vdir}/lib.md")
    assert not _nonstd(root, vdir)
    report = cvc.ValidationReport()
    cvc.validate_no_absolute_paths(root, report, respect_gitignore=False)
    assert any((r.file or "").startswith(vdir) for r in report.results if "Absolute path" in r.message)


def test_tracked_weird_dir_is_nonstandard(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "p")
    _write(root, "weird/x.md", "x\n")
    _git(root, "init", "-q")
    _git(root, "add", ".claude-plugin/plugin.json", "weird/x.md")
    assert _nonstd(root, "weird")


def test_gitmodules_entry_without_gitlink_not_exempt(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "p")
    _write(root, ".gitmodules", '[submodule "extlib"]\n\tpath = extlib\n\turl = https://github.com/o/extlib.git\n')
    _write(root, "extlib/x.md", "x\n")
    _git(root, "init", "-q")
    _git(root, "add", ".claude-plugin/plugin.json", ".gitmodules", "extlib/x.md")
    assert _nonstd(root, "extlib")


def test_index_gitlink_dir_is_exempt(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "p")
    (root / "extlib").mkdir()
    _git(root, "init", "-q")
    _git(root, "add", ".claude-plugin/plugin.json")
    _git(root, "update-index", "--add", "--cacheinfo", "160000," + "1" * 40 + ",extlib")
    assert cvc._is_vendored_root_dir("extlib", root)
    assert not _nonstd(root, "extlib")


# ── item 7: build/dependency dirs pruned only when untracked ──────────────────


@pytest.mark.parametrize("respect_gitignore", [True, False])
@pytest.mark.parametrize("track", [True, False])
@pytest.mark.parametrize("sdir", ["dist", "target"])
def test_skip_dir_pruned_only_when_untracked(tmp_path: Path, track: bool, respect_gitignore: bool, sdir: str) -> None:
    root = _plugin(tmp_path / "p")
    _write(root, f"{sdir}/out.md", f"cat {_LEAK}\n")
    _git(root, "init", "-q")
    _git(root, "add", ".claude-plugin/plugin.json")
    if track:
        _git(root, "add", f"{sdir}/out.md")
    abs_report = cvc.ValidationReport()
    cvc.validate_no_absolute_paths(root, abs_report, respect_gitignore=respect_gitignore)
    private_report = cvc.ValidationReport()
    cvc.scan_directory_for_private_info(root, private_report, respect_gitignore=respect_gitignore)
    abs_hit = any((r.file or "").startswith(sdir) for r in abs_report.results)
    private_hit = any((r.file or "").startswith(sdir) for r in private_report.results)
    assert abs_hit is track
    assert private_hit is track


def test_skip_dir_in_non_git_tree_is_scanned(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "p")
    _write(root, "dist/out.md", f"cat {_LEAK}\n")
    report = cvc.ValidationReport()
    cvc.validate_no_absolute_paths(root, report, respect_gitignore=False)
    assert any((r.file or "").startswith("dist") for r in report.results)


# ── item 10: only login-illegal characters make a username "regex" ───────────


@pytest.mark.parametrize("text", ["C:\\Users\\John (Work)\\x", "C:\\Users\\John(Work)\\x"])
def test_parenthesised_windows_login_flagged(tmp_path: Path, text: str) -> None:
    assert _scan_one(tmp_path, text)[0] >= 1


@pytest.mark.parametrize("pattern", ["/home/[^/]+/", "/home/^me/x"])
def test_regex_username_segment_still_skipped(tmp_path: Path, pattern: str) -> None:
    assert _scan_one(tmp_path, pattern)[0] == 0


# ── item 11: a repo-configured fsmonitor hook is never executed ───────────────


def test_fsmonitor_hook_never_runs(tmp_path: Path) -> None:
    root = _plugin(tmp_path / "p")
    _write(root, "node_modules/x.md", "x\n")
    marker = tmp_path / "marker"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(root, "init", "-q")
    _git(root, "add", ".claude-plugin/plugin.json")
    _git(root, "config", "core.fsmonitor", str(hook))
    cvc.is_vendored_path(Path("node_modules/x.md"), root)
    # `status` is the git command that does consult core.fsmonitor; _git_run must
    # neutralise the hook for every command it runs.
    cvc._git_run(str(root), "status", "--porcelain", timeout=30)
    assert not marker.exists()
