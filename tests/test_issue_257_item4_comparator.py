"""Issue 257 item 4: notify-marketplace.yml drift must use the file's own owner/repo."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

NOTIFY = ".github/workflows/notify-marketplace.yml"


def _plugin(tmp_path: Path, owner: str, repo: str) -> Path:
    """Plugin whose notify-marketplace.yml is the canonical render for owner/repo."""
    from generate_plugin_repo import gen_notify_marketplace_yml
    from standardize_plugin import _params_from_manifest

    root = tmp_path / "demo"
    (root / ".claude-plugin").mkdir(parents=True)
    manifest = {
        "name": "demo",
        "version": "0.1.0",
        "description": "d",
        "author": {"name": "T", "email": "t@example.com"},
        "repository": "https://github.com/Acme/demo",
    }
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
    params = _params_from_manifest(manifest)
    params.marketplace_owner = owner
    params.marketplace = repo
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / NOTIFY).write_text(gen_notify_marketplace_yml(params))
    return root


def _drift(root: Path) -> list[str]:
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    report = ValidationReport()
    validate_canonical_pipeline_drift(root, report)
    return [r.message for r in report.results if "RC-PIPELINE-DRIFT-001" in r.message and NOTIFY in r.message]


def test_non_default_marketplace_owner_and_repo_is_not_drift(tmp_path: Path) -> None:
    """A byte-identical file rendered for a real owner/marketplace reports no drift."""
    assert _drift(_plugin(tmp_path, "AcmeMarket", "acme-marketplace")) == []


def test_tampered_secret_name_still_drifts_with_custom_values(tmp_path: Path) -> None:
    """Custom owner/repo must not free any other byte: a renamed secret still fires."""
    root = _plugin(tmp_path, "AcmeMarket", "acme-marketplace")
    target = root / NOTIFY
    original = target.read_text()
    assert "MARKETPLACE_PAT" in original
    target.write_text(original.replace("MARKETPLACE_PAT", "OTHER_PAT"))
    assert len(_drift(root)) == 1


@pytest.mark.parametrize("bad", ["evil$(id)", "a;b", "a/b", "x" * 101])
def test_malformed_owner_value_still_drifts(tmp_path: Path, bad: str) -> None:
    """An owner that is not a plain GitHub name is never adopted: the file is flagged."""
    assert len(_drift(_plugin(tmp_path, bad, "acme-marketplace"))) == 1


def test_malformed_repo_value_still_drifts(tmp_path: Path) -> None:
    """A repo value that is not a plain GitHub name is never adopted: the file is flagged."""
    assert len(_drift(_plugin(tmp_path, "AcmeMarket", "repo$(id)"))) == 1


def test_detector_failure_does_not_skip_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A detector crash must fail toward flagging, never skip the drift check."""
    import standardize_plugin

    def boom(_root: Path) -> dict[str, str | None]:
        raise ValueError("boom")

    monkeypatch.setattr(standardize_plugin, "_detect_existing_notify_marketplace", boom)
    assert len(_drift(_plugin(tmp_path, "AcmeMarket", "acme-marketplace"))) == 1
