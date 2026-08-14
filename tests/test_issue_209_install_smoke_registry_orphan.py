"""Issue #209 — Gate 15 must assert its own registry cleanup, not assume it.

`claude plugin uninstall --scope local` does not always drop the local-scope
record from `installed_plugins.json`. When it does not, the smoke temp dir is
deleted moments later and the record is left pointing at a path that no longer
exists. Nothing reported it, which is why it took a manual read of the file to
notice — so the fix is a post-uninstall assertion, and these tests pin both
directions of it.

Every "reports an orphan" case is paired with a control proving the same code
path stays silent when the uninstall did its job: a check that fired on a clean
run would be noise, and noise is how a real signal gets ignored.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish  # noqa: E402

TARGET = "my-plugin@my-marketplace"


def _registry(tmp_path: Path, records: list[dict[str, object]], key: str = TARGET) -> Path:
    path = tmp_path / "installed_plugins.json"
    path.write_text(
        json.dumps({"version": 1, "plugins": {key: records}}),
        encoding="utf-8",
    )
    return path


def _smoke_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cpv-install-smoke-abc123"
    d.mkdir()
    return d


# ─────────────────────────────────────────────────────────────────────────────
# The orphan is reported
# ─────────────────────────────────────────────────────────────────────────────
def test_a_surviving_local_record_is_reported(tmp_path: Path) -> None:
    smoke = _smoke_dir(tmp_path)
    reg = _registry(tmp_path, [{"scope": "local", "projectPath": str(smoke), "version": "1.0.0"}])
    assert publish._smoke_records_still_registered(TARGET, smoke, reg) == [str(smoke)]


def test_the_path_compare_is_resolved_not_literal(tmp_path: Path) -> None:
    """macOS records `/private/var/...` for a `/var/...` mktemp dir.

    A raw string compare finds nothing on exactly the platform this was
    reported from, so the resolved compare is the load-bearing part rather
    than a detail.
    """
    smoke = _smoke_dir(tmp_path)
    # Reach the same directory through a symlinked parent — the registry value
    # and the smoke dir are different strings for one directory.
    link_parent = tmp_path / "link"
    link_parent.symlink_to(tmp_path, target_is_directory=True)
    via_link = link_parent / smoke.name
    reg = _registry(tmp_path, [{"scope": "local", "projectPath": str(via_link)}])
    assert publish._smoke_records_still_registered(TARGET, smoke, reg) == [str(via_link)]


# ─────────────────────────────────────────────────────────────────────────────
# Controls — the check must stay silent on a clean run
# ─────────────────────────────────────────────────────────────────────────────
def test_a_clean_uninstall_reports_nothing(tmp_path: Path) -> None:
    smoke = _smoke_dir(tmp_path)
    assert publish._smoke_records_still_registered(TARGET, smoke, _registry(tmp_path, [])) == []


def test_a_user_scope_record_is_not_an_orphan(tmp_path: Path) -> None:
    """The author almost certainly has the plugin installed at USER scope.

    Reporting that as leftover smoke state would tell them to clean up their
    real installation.
    """
    smoke = _smoke_dir(tmp_path)
    reg = _registry(tmp_path, [{"scope": "user", "installPath": "/somewhere/else", "version": "1.0.0"}])
    assert publish._smoke_records_still_registered(TARGET, smoke, reg) == []


def test_another_projects_local_record_is_not_an_orphan(tmp_path: Path) -> None:
    smoke = _smoke_dir(tmp_path)
    other = tmp_path / "some-real-project"
    other.mkdir()
    reg = _registry(tmp_path, [{"scope": "local", "projectPath": str(other)}])
    assert publish._smoke_records_still_registered(TARGET, smoke, reg) == []


def test_another_plugins_smoke_record_is_not_this_runs_to_report(tmp_path: Path) -> None:
    """Scoped to this plugin's key: a stranger's leftover is not this run's signal."""
    smoke = _smoke_dir(tmp_path)
    reg = _registry(
        tmp_path,
        [{"scope": "local", "projectPath": str(smoke)}],
        key="someone-else@their-marketplace",
    )
    assert publish._smoke_records_still_registered(TARGET, smoke, reg) == []


# ─────────────────────────────────────────────────────────────────────────────
# Cannot-check is never reported as clean
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda p: None, id="missing-file"),
        pytest.param(lambda p: p.write_text("{not json", encoding="utf-8"), id="malformed"),
        pytest.param(lambda p: p.write_text("[]", encoding="utf-8"), id="not-an-object"),
        pytest.param(lambda p: p.write_text('{"version": 1}', encoding="utf-8"), id="no-plugins-key"),
    ],
)
def test_an_unreadable_registry_is_unknown_not_clean(tmp_path: Path, write) -> None:
    smoke = _smoke_dir(tmp_path)
    reg = tmp_path / "installed_plugins.json"
    write(reg)
    assert publish._smoke_records_still_registered(TARGET, smoke, reg) is None


# ─────────────────────────────────────────────────────────────────────────────
# The reporter never raises and never changes the verdict
# ─────────────────────────────────────────────────────────────────────────────
def test_the_reporter_is_silent_on_a_clean_run(tmp_path: Path, capsys, monkeypatch) -> None:
    smoke = _smoke_dir(tmp_path)
    monkeypatch.setattr(publish, "_INSTALLED_PLUGINS_REGISTRY", _registry(tmp_path, []))
    publish._report_smoke_registry_orphan(TARGET, smoke)
    assert capsys.readouterr().out == ""


def test_the_reporter_names_the_orphan(tmp_path: Path, capsys, monkeypatch) -> None:
    smoke = _smoke_dir(tmp_path)
    monkeypatch.setattr(
        publish,
        "_INSTALLED_PLUGINS_REGISTRY",
        _registry(tmp_path, [{"scope": "local", "projectPath": str(smoke)}]),
    )
    publish._report_smoke_registry_orphan(TARGET, smoke)
    out = capsys.readouterr().out
    assert str(smoke) in out
    assert "#209" in out


def test_the_reporter_says_unverified_when_it_cannot_read(tmp_path: Path, capsys, monkeypatch) -> None:
    smoke = _smoke_dir(tmp_path)
    monkeypatch.setattr(publish, "_INSTALLED_PLUGINS_REGISTRY", tmp_path / "does-not-exist.json")
    publish._report_smoke_registry_orphan(TARGET, smoke)
    assert "UNVERIFIED" in capsys.readouterr().out


def test_the_check_never_writes_to_the_registry(tmp_path: Path) -> None:
    """READ-ONLY: the registry belongs to Claude Code, not to a publish pipeline."""
    smoke = _smoke_dir(tmp_path)
    reg = _registry(tmp_path, [{"scope": "local", "projectPath": str(smoke)}])
    before = reg.read_bytes()
    publish._smoke_records_still_registered(TARGET, smoke, reg)
    assert reg.read_bytes() == before
