"""Task G review — .ruff.toml and .bandit.yml are canonical-pipeline drift files.

They carry the lint/security rule set that .mega-linter.yml points at, so a
plugin editing them (an extra bandit skip, a ruff ignore) or deleting them is
reported like any other canonical-file drift. Two-sided: the canonical render
reports nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FILES = (".ruff.toml", ".bandit.yml")


def _plugin(tmp_path: Path) -> Path:
    from generate_plugin_repo import gen_bandit_yml, gen_mega_linter_yml, gen_ruff_toml
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
    (root / ".mega-linter.yml").write_text(gen_mega_linter_yml(params))
    (root / ".ruff.toml").write_text(gen_ruff_toml(params))
    (root / ".bandit.yml").write_text(gen_bandit_yml(params))
    return root


def _drift(root: Path, rel: str) -> list[str]:
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_canonical_pipeline_drift

    report = ValidationReport()
    validate_canonical_pipeline_drift(root, report)
    return [
        r.message
        for r in report.results
        if r.level == "WARNING" and "RC-PIPELINE-DRIFT-001" in r.message and rel in r.message
    ]


@pytest.mark.parametrize("rel", FILES)
def test_canonical_render_is_not_drift(tmp_path: Path, rel: str) -> None:
    assert _drift(_plugin(tmp_path), rel) == []


def test_edited_bandit_extra_skip_is_drift(tmp_path: Path) -> None:
    root = _plugin(tmp_path)
    target = root / ".bandit.yml"
    target.write_text(target.read_text() + "skips: [B602]\n")
    assert len(_drift(root, ".bandit.yml")) == 1


def test_edited_ruff_ignore_is_drift(tmp_path: Path) -> None:
    root = _plugin(tmp_path)
    target = root / ".ruff.toml"
    target.write_text(target.read_text() + '\n[lint.per-file-ignores]\n"*" = ["S"]\n')
    assert len(_drift(root, ".ruff.toml")) == 1


@pytest.mark.parametrize("rel", FILES)
def test_deleted_rule_config_is_drift(tmp_path: Path, rel: str) -> None:
    root = _plugin(tmp_path)
    (root / rel).unlink()
    found = _drift(root, rel)
    assert len(found) == 1 and "missing" in found[0]


def test_missing_rule_config_without_megalinter_is_silent(tmp_path: Path) -> None:
    """No canonical .mega-linter.yml: nothing points at the configs, no drift."""
    root = _plugin(tmp_path)
    (root / ".mega-linter.yml").unlink()
    (root / ".bandit.yml").unlink()
    assert _drift(root, ".bandit.yml") == []
