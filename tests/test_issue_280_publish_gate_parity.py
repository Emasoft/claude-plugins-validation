#!/usr/bin/env python3
"""Tests for RC-PUBLISH-GATE-PARITY-001 (issue #280).

A local scripts/publish.py gate that never runs mypy while CI does must be a
MAJOR finding, regardless of cpv.pipeline.intentional_divergence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import validate_publish_gate_parity  # noqa: E402

RULE = "RC-PUBLISH-GATE-PARITY-001"
CI_MYPY = (
    "name: Release\non: push\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: uv run mypy scripts/\n"
)
PUBLISH_NO_MYPY = "#!/usr/bin/env python3\nprint('publish')\n"


def _plugin(tmp_path: Path, *, workflow: str | None, publish: str | None, manifest_extra: dict | None = None) -> Path:
    """Build a minimal plugin dir with an optional release workflow and publish.py."""
    plugin = tmp_path / "p"
    (plugin / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": "p", "version": "1.0.0", "description": "d"}
    manifest.update(manifest_extra or {})
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
    if workflow is not None:
        (plugin / ".github" / "workflows").mkdir(parents=True)
        (plugin / ".github" / "workflows" / "release.yml").write_text(workflow)
    if publish is not None:
        (plugin / "scripts").mkdir()
        (plugin / "scripts" / "publish.py").write_text(publish)
    return plugin


def _majors(plugin: Path) -> list[str]:
    report = ValidationReport()
    validate_publish_gate_parity(plugin, report)
    return [r.message for r in report.results if r.level == "MAJOR"]


def test_ci_mypy_publish_without_mypy_is_major(tmp_path):
    """CI runs mypy directly and publish.py does not: exactly one MAJOR with the rule id."""
    majors = _majors(_plugin(tmp_path, workflow=CI_MYPY, publish=PUBLISH_NO_MYPY))
    assert len(majors) == 1
    assert RULE in majors[0]


def test_publish_running_mypy_passes(tmp_path):
    """publish.py invoking mypy on a real line produces no finding."""
    publish = PUBLISH_NO_MYPY + 'subprocess.run(["uv", "run", "mypy", "scripts/"])\n'
    assert _majors(_plugin(tmp_path, workflow=CI_MYPY, publish=publish)) == []


def test_publish_running_ci_preflight_passes(tmp_path):
    """publish.py delegating to cpv-remote-validate ci-preflight covers mypy."""
    publish = PUBLISH_NO_MYPY + 'subprocess.run(["cpv-remote-validate", "ci-preflight", "."])\n'
    assert _majors(_plugin(tmp_path, workflow=CI_MYPY, publish=publish)) == []


def test_mypy_only_in_publish_comment_is_major(tmp_path):
    """A mypy mention inside a publish.py comment runs nothing, so it still fires."""
    publish = PUBLISH_NO_MYPY + "# TODO: run mypy here someday\n"
    majors = _majors(_plugin(tmp_path, workflow=CI_MYPY, publish=publish))
    assert len(majors) == 1
    assert RULE in majors[0]


def test_mypy_only_in_workflow_comment_is_silent(tmp_path):
    """CI mentioning mypy only in a YAML comment (no Mega-Linter) means CI does not run it."""
    workflow = CI_MYPY.replace("- run: uv run mypy scripts/", "# - run: uv run mypy scripts/\n      - run: echo hi")
    assert _majors(_plugin(tmp_path, workflow=workflow, publish=PUBLISH_NO_MYPY)) == []


def test_no_publish_py_is_silent(tmp_path):
    """A plugin without scripts/publish.py draws no finding."""
    assert _majors(_plugin(tmp_path, workflow=CI_MYPY, publish=None)) == []


def test_intentional_divergence_does_not_hide_gap(tmp_path):
    """Declaring publish.py in cpv.pipeline.intentional_divergence still yields the MAJOR."""
    extra = {"cpv": {"pipeline": {"intentional_divergence": ["scripts/publish.py"]}}}
    majors = _majors(_plugin(tmp_path, workflow=CI_MYPY, publish=PUBLISH_NO_MYPY, manifest_extra=extra))
    assert len(majors) == 1
    assert RULE in majors[0]
