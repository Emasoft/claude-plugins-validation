"""Gate 3d (publish secret scan) and the git-accuracy of the external post-filter.

Two things are under test and they are deliberately in one file, because each is
the other's control:

1. ``publish.stage_secret_scan`` ARMS the SHA-verified self-scan exemption and
   DISARMS it in a ``finally``. Without the arming, CPV's own detector regexes
   read as credentials and CPV can never publish itself ("scanning the
   scanner"). Without the disarm, the flag — a module GLOBAL — leaks into any
   later scan in the same process.

2. ``validate_security._external_finding_is_gitignored`` never suppresses a
   git-TRACKED path. ``.gitignore`` does not untrack an already-tracked file, so
   such a file still ships; suppressing it would be a scan-evasion vector
   (``git add payload`` + ``.gitignore payload`` → invisible to every external
   scanner).

Every suppression assertion here ships with a positive control, because an
assertion that "nothing was reported" passes vacuously against a filter that
reports nothing at all.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import publish  # noqa: E402
import validate_security as vs  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

PLUGIN_ROOT = SCRIPTS.parent


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture()
def _clear_tracked_cache():
    """The tracked-path cache is keyed by root and lives for the process."""
    vs._GIT_TRACKED_CACHE.clear()
    yield
    vs._GIT_TRACKED_CACHE.clear()


@pytest.fixture()
def ignored_repo(tmp_path: Path) -> Path:
    """A git repo with `corpus/` gitignored and one file inside it, untracked."""
    root = tmp_path / "repo"
    (root / "corpus").mkdir(parents=True)
    _git("init", "-q", ".", cwd=root)
    (root / ".gitignore").write_text("corpus/\n", encoding="utf-8")
    (root / "corpus" / "leak.py").write_text("TOKEN = 'x'\n", encoding="utf-8")
    _git("add", ".gitignore", cwd=root)
    _git("commit", "-qm", "base", cwd=root)
    return root


# ---------------------------------------------------------------- _git_tracked_relpaths


def test_tracked_relpaths_lists_tracked_files(ignored_repo: Path, _clear_tracked_cache):
    assert ".gitignore" in vs._git_tracked_relpaths(ignored_repo)


def test_tracked_relpaths_excludes_untracked_gitignored_file(
    ignored_repo: Path, _clear_tracked_cache
):
    assert "corpus/leak.py" not in vs._git_tracked_relpaths(ignored_repo)


def test_tracked_relpaths_is_empty_outside_a_git_repo(tmp_path: Path, _clear_tracked_cache):
    """Fail-open: an empty set REFUSES no suppression, so it changes no verdict."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert vs._git_tracked_relpaths(plain) == frozenset()


# ------------------------------------------------- _external_finding_is_gitignored


def test_untracked_gitignored_finding_is_suppressed(ignored_repo: Path, _clear_tracked_cache):
    """Issue #67: a gitignored+untracked corpus is not shipped, so it is noise."""
    gi = vs.get_gitignore_filter(ignored_repo)
    assert vs._external_finding_is_gitignored("corpus/leak.py", gi) is True


def test_tracked_gitignored_finding_is_NEVER_suppressed(
    ignored_repo: Path, _clear_tracked_cache
):
    """The evasion control: `git add -f` a gitignored file and it still ships.

    This is the assertion that fails if the tracked check is removed — the
    sibling test above keeps passing, which is exactly why both are needed.
    """
    _git("add", "-f", "corpus/leak.py", cwd=ignored_repo)
    _git("commit", "-qm", "track", cwd=ignored_repo)
    vs._GIT_TRACKED_CACHE.clear()
    gi = vs.get_gitignore_filter(ignored_repo)
    assert vs._external_finding_is_gitignored("corpus/leak.py", gi) is False


def test_non_gitignored_tracked_finding_is_not_suppressed(
    ignored_repo: Path, _clear_tracked_cache
):
    gi = vs.get_gitignore_filter(ignored_repo)
    assert vs._external_finding_is_gitignored(".gitignore", gi) is False


def test_absolute_finding_path_is_normalised(ignored_repo: Path, _clear_tracked_cache):
    """External scanners hand back absolute paths; the verdict must not change."""
    gi = vs.get_gitignore_filter(ignored_repo)
    absolute = str(ignored_repo / "corpus" / "leak.py")
    assert vs._external_finding_is_gitignored(absolute, gi) is True


def test_path_outside_the_plugin_root_is_not_suppressed(
    ignored_repo: Path, _clear_tracked_cache
):
    gi = vs.get_gitignore_filter(ignored_repo)
    assert vs._external_finding_is_gitignored("/etc/hosts", gi) is False


# ------------------------------------------------------------- Gate 3d arming/disarming


def test_gate_arms_self_scan_for_cpv_and_disarms_after(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, object] = {}

    def _spy(plugin_path, report):
        seen["active"] = vs._CPV_SELF_SCAN_ACTIVE
        seen["root"] = vs._CPV_SELF_PLUGIN_ROOT
        return 0

    monkeypatch.setattr(publish, "stage_secret_scan", publish.stage_secret_scan)
    monkeypatch.setattr(vs, "check_trufflehog", _spy)
    rc = publish.stage_secret_scan(PLUGIN_ROOT)

    assert rc == 0
    assert seen["active"] is True, "the exemption must be ARMED while the scan runs"
    assert seen["root"] == PLUGIN_ROOT.resolve()
    assert vs._CPV_SELF_SCAN_ACTIVE is False, "the module-global flag must be disarmed"
    assert vs._CPV_SELF_PLUGIN_ROOT is None


def test_gate_does_not_arm_self_scan_for_a_foreign_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The positive control for the test above: a third-party tree gets NO exemption."""
    foreign = tmp_path / "other"
    (foreign / ".claude-plugin").mkdir(parents=True)
    (foreign / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"other","version":"0.1.0","description":"d"}\n', encoding="utf-8"
    )
    seen: dict[str, object] = {}

    def _spy(plugin_path, report):
        seen["active"] = vs._CPV_SELF_SCAN_ACTIVE
        return 0

    monkeypatch.setattr(vs, "check_trufflehog", _spy)
    publish.stage_secret_scan(foreign)
    assert seen["active"] is False


def test_gate_disarms_even_when_the_scan_raises(monkeypatch: pytest.MonkeyPatch):
    """A left-armed flag would let the NEXT plugin's scan read stale state."""

    def _boom(plugin_path, report):
        raise RuntimeError("scanner exploded")

    monkeypatch.setattr(vs, "check_trufflehog", _boom)
    with pytest.raises(RuntimeError):
        publish.stage_secret_scan(PLUGIN_ROOT)
    assert vs._CPV_SELF_SCAN_ACTIVE is False
    assert vs._CPV_SELF_PLUGIN_ROOT is None


# ------------------------------------------------------------------ Gate 3d verdicts


def test_gate_blocks_on_a_blocking_finding(monkeypatch: pytest.MonkeyPatch):
    def _finding(plugin_path, report: ValidationReport):
        report.major("trufflehog UNVERIFIED secret: detector=Slack")
        return 1

    monkeypatch.setattr(vs, "check_trufflehog", _finding)
    assert publish.stage_secret_scan(PLUGIN_ROOT) == 1


def test_gate_passes_on_a_clean_scan(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vs, "check_trufflehog", lambda plugin_path, report: 0)
    assert publish.stage_secret_scan(PLUGIN_ROOT) == 0


def test_gate_names_the_fixer_and_does_not_redact(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Redaction is the fixer agent's job; a gate that can silence itself is not a gate."""

    def _finding(plugin_path, report: ValidationReport):
        report.major("trufflehog UNVERIFIED secret: detector=Slack")
        return 1

    monkeypatch.setattr(vs, "check_trufflehog", _finding)
    publish.stage_secret_scan(PLUGIN_ROOT)
    err = capsys.readouterr().err
    assert "cpv-plugin-leaks-preventer-agent" in err
    assert "ROTATED" in err


def test_gate_installs_trufflehog_when_absent(monkeypatch: pytest.MonkeyPatch):
    """trufflehog is a CPV dependency, so the gate installs it rather than nagging."""
    import cpv_install_scanners

    calls: list[str] = []
    monkeypatch.setattr(publish.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        cpv_install_scanners, "ensure_trufflehog", lambda: calls.append("install") or False
    )
    publish.stage_secret_scan(PLUGIN_ROOT)
    assert calls == ["install"], "the gate must attempt the install before blocking"


def test_gate_blocks_when_trufflehog_cannot_be_installed(monkeypatch: pytest.MonkeyPatch):
    """`check_trufflehog` reports a missing binary as a non-blocking WARNING.

    That is right for a general validate run and wrong for a release gate: it
    would publish having scanned nothing. A failed install must block instead.
    """
    import cpv_install_scanners

    monkeypatch.setattr(publish.shutil, "which", lambda name: None)
    monkeypatch.setattr(cpv_install_scanners, "ensure_trufflehog", lambda: False)
    assert publish.stage_secret_scan(PLUGIN_ROOT) == 1


def test_gate_blocks_when_the_installer_itself_raises(monkeypatch: pytest.MonkeyPatch):
    """An installer crash is still 'we never scanned' — it must not fall through."""
    import cpv_install_scanners

    def _boom():
        raise RuntimeError("brew exploded")

    monkeypatch.setattr(publish.shutil, "which", lambda name: None)
    monkeypatch.setattr(cpv_install_scanners, "ensure_trufflehog", _boom)
    assert publish.stage_secret_scan(PLUGIN_ROOT) == 1


def test_gate_runs_when_trufflehog_is_present(monkeypatch: pytest.MonkeyPatch):
    """Positive control: the probe must not block when the binary IS there."""
    monkeypatch.setattr(publish.shutil, "which", lambda name: "/usr/local/bin/trufflehog")
    monkeypatch.setattr(vs, "check_trufflehog", lambda plugin_path, report: 0)
    assert publish.stage_secret_scan(PLUGIN_ROOT) == 0


def test_gate_refuses_when_the_scanner_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Cannot-check is not clean — a gate that skips must not report a pass."""
    empty = tmp_path / "noscripts"
    empty.mkdir()
    real_import = __import__

    def _blocked(name, *args, **kwargs):
        if name == "validate_security":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _blocked)
    assert publish.stage_secret_scan(empty) == 1


# ------------------------------------------------------------- exclude-path anchoring


def test_exclude_args_anchor_on_absolute_paths(ignored_repo: Path, _clear_tracked_cache):
    """trufflehog matches --exclude-paths regexes against ABSOLUTE paths.

    A plugin-relative anchor (`^corpus`) matches nothing while looking correct,
    which is how this was wrong the first time.
    """
    gi = vs.get_gitignore_filter(ignored_repo)
    args = vs._trufflehog_exclude_args(ignored_repo, gi)
    if not args:
        pytest.skip("no exclude file produced for this tree")
    assert args[0] in ("-x", "--exclude-paths")
    body = Path(args[1]).read_text(encoding="utf-8")
    # The patterns are re.escape()d, so compare against the escaped root — a raw
    # comparison fails on any path containing a regex metacharacter (a macOS
    # tmpdir contains hyphens), which says nothing about the anchoring.
    escaped_root = re.escape(str(ignored_repo.resolve()))
    assert escaped_root in body, "patterns must anchor on the absolute root"
    assert f"^{escaped_root}/corpus" in body


# ----------------------------------------------------- the EMITTED canon gate


def _emitted_publish_py() -> str:
    from generate_plugin_repo import PluginParams, gen_publish_py

    return gen_publish_py(
        PluginParams(
            name="demo-plugin",
            description="d",
            author="a",
            author_email="a@b.c",
            license="MIT",
            python_version="3.12",
            github_owner="o",
            marketplace="m",
        )
    )


def test_emitted_publish_py_carries_a_release_path_secret_gate():
    """The pre-push hook scans FEATURE branches only.

    A default-branch / tag push is gated on publish.py ancestry instead, so
    without this gate the one path that reaches users is never scanned.
    """
    body = _emitted_publish_py()
    assert "[G3s] Secret scan" in body
    assert "trufflehog" in body


def test_emitted_gate_installs_trufflehog_rather_than_demanding_it():
    body = _emitted_publish_py()
    assert '"brew", "install", "trufflehog"' in body
    assert "github.com/trufflesecurity/trufflehog/v3@latest" in body


def test_emitted_gate_blocks_when_the_install_fails():
    """Cannot-check is not clean: an unscanned release must not publish."""
    body = _emitted_publish_py()
    assert "could not be installed" in body
    assert "UNKNOWN is not clean" in body


def test_emitted_gate_asks_for_every_result_bucket():
    """Without the widened set an expired/revoked credential is invisible (#219)."""
    assert "filtered_unverified" in _emitted_publish_py()


def test_emitted_gate_excludes_only_untracked_ignored_paths():
    """A TRACKED+gitignored file still ships, so it must stay scanned."""
    body = _emitted_publish_py()
    assert '"--others", "--ignored", "--exclude-standard", "--directory"' in body


def test_emitted_gate_runs_between_validation_and_tests():
    body = _emitted_publish_py()
    assert body.index("[G3] Valid") < body.index("[G3s] Secret") < body.index("[G4] Running")


def test_emitted_publish_py_parses():
    import ast

    ast.parse(_emitted_publish_py())
