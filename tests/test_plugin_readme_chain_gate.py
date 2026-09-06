"""Tests for the plugin<->marketplace README chain gate in ``validate_pipeline_readiness``.

Two checks, both non-blocking WARNING advisories:

1. A plugin with a publish pipeline (``scripts/publish.py``) whose workflows
   never perform a ``plugin-updated`` ``repository_dispatch`` — the
   marketplace has no signal to refresh its catalog entry / README table on
   release. Scoped to plugins that already have a publish pipeline: a plugin
   with no release automation at all draws no finding here.
2. A plugin with a publish pipeline whose README carries no
   ``version-X.Y.Z-blue`` shields.io badge — publish.py's canon
   ``stage_update_badges`` step (see ``generate_plugin_repo.py``) has nothing
   to rewrite on release.

Real temp plugin directories (real ``plugin.json``, real workflow YAML, real
README) run through the real ``validate_pipeline_readiness`` function — no
mocking.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import validate_pipeline_readiness  # noqa: E402

_DISPATCHING_WORKFLOW = """\
name: Notify Marketplace
on:
  push:
    tags:
      - 'v*'
jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger marketplace update
        uses: peter-evans/repository-dispatch@5fc4efd1a4797ddb68ffd0714a238564e4cc0e6f
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}
          repository: owner/marketplace
          event-type: plugin-updated
          client-payload: '{"plugin": "test-plugin"}'
"""

_STUB_NOTIFY_WORKFLOW = """\
name: Notify Marketplace
on:
  push:
    tags:
      - 'v*'
jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Placeholder
        run: echo "TODO wire this up"
"""

_BADGE_README = """\
# test-plugin

![version](https://img.shields.io/badge/version-1.2.3-blue)

A test plugin.
"""

_NO_BADGE_README = """\
# test-plugin

A test plugin with no badges.
"""


def _make_plugin(
    tmp_path: Path,
    *,
    has_publish_py: bool = False,
    readme: str | None = None,
    workflow_content: dict[str, str] | None = None,
) -> Path:
    """Build a minimal real plugin directory on disk."""
    plugin = tmp_path / "test-plugin"
    plugin.mkdir()
    cp = plugin / ".claude-plugin"
    cp.mkdir()
    (cp / "plugin.json").write_text(
        json.dumps(
            {
                "name": "test-plugin",
                "version": "1.0.0",
                "description": "Test",
                "author": {"name": "A", "email": "a@b.c"},
            }
        )
    )
    (plugin / "commands").mkdir()
    if has_publish_py:
        scripts_dir = plugin / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "publish.py").write_text("#!/usr/bin/env python3\n# publish\n")
    if readme is not None:
        (plugin / "README.md").write_text(readme)
    if workflow_content:
        wf_dir = plugin / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        for name, content in workflow_content.items():
            (wf_dir / name).write_text(content)
    return plugin


def _messages(report: ValidationReport, level: str) -> list[str]:
    return [r.message for r in report.results if r.level == level]


class TestNotifyMarketplaceDispatchContent:
    """The `plugin-updated` repository_dispatch content-verification check."""

    def test_no_publish_pipeline_no_workflows_draws_no_dispatch_finding(self, tmp_path):
        """A plugin with neither a publish pipeline nor any workflow is not nagged about dispatch."""
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        assert not any(
            "plugin-updated" in m for m in _messages(report, "WARNING") + _messages(report, "PASSED")
        )

    def test_no_publish_pipeline_but_stub_workflow_draws_no_dispatch_finding(self, tmp_path):
        """Positive control for scoping: a stub notify workflow with NO publish pipeline is not nagged
        about the dispatch content (only the filename-existence check, unaffected, may fire)."""
        plugin = _make_plugin(
            tmp_path,
            workflow_content={"notify-marketplace.yml": _STUB_NOTIFY_WORKFLOW},
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        assert not any("plugin-updated" in m for m in _messages(report, "WARNING"))

    def test_publish_pipeline_with_dispatching_workflow_passes(self, tmp_path):
        """A publish-pipeline plugin whose workflow performs a real `plugin-updated` dispatch clears."""
        plugin = _make_plugin(
            tmp_path,
            has_publish_py=True,
            workflow_content={"notify-marketplace.yml": _DISPATCHING_WORKFLOW},
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        passed = _messages(report, "PASSED")
        assert any("plugin-updated" in m for m in passed)
        assert not any("does not notify its marketplace" in m for m in _messages(report, "WARNING"))

    def test_publish_pipeline_with_stub_notify_workflow_warns(self, tmp_path):
        """A publish-pipeline plugin with a NAMED but non-dispatching notify workflow (a stub) still
        draws the content-verification WARNING — the filename alone is not enough."""
        plugin = _make_plugin(
            tmp_path,
            has_publish_py=True,
            workflow_content={"notify-marketplace.yml": _STUB_NOTIFY_WORKFLOW},
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        warnings = _messages(report, "WARNING")
        assert any("does not notify its marketplace" in m for m in warnings)
        assert any("notify-marketplace.yml" in m for m in warnings)

    def test_publish_pipeline_with_no_workflows_at_all_draws_no_dispatch_finding(self, tmp_path):
        """Scoping check: a publish-pipeline plugin with NO .github/workflows directory at all draws
        no dispatch-content finding (there is nothing to inspect, matches the pre-existing filename
        check's own gate on `workflows_dir.is_dir()`)."""
        plugin = _make_plugin(tmp_path, has_publish_py=True)
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        assert not any("plugin-updated" in m for m in _messages(report, "WARNING") + _messages(report, "PASSED"))

    def test_warning_wording_is_a_capability_gap_not_an_overclaim(self, tmp_path):
        """The warning must not overclaim that the marketplace README WILL go stale — a marketplace
        may poll on a schedule instead, so the wording states a capability gap."""
        plugin = _make_plugin(
            tmp_path,
            has_publish_py=True,
            workflow_content={"ci.yml": "name: CI\non:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"},
        )
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        warnings = _messages(report, "WARNING")
        matching = [m for m in warnings if "does not notify its marketplace" in m]
        assert matching
        assert "will stay stale" not in matching[0]
        assert "unless the marketplace polls" in matching[0]


class TestReadmeVersionBadge:
    """The `version-X.Y.Z-blue` shields.io badge check."""

    def test_no_publish_pipeline_no_badge_draws_no_finding(self, tmp_path):
        """A plugin with no publish pipeline at all is not nagged about a missing version badge."""
        plugin = _make_plugin(tmp_path, readme=_NO_BADGE_README)
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        assert not any(
            "version badge" in m or "version-X.Y.Z-blue" in m
            for m in _messages(report, "WARNING") + _messages(report, "PASSED")
        )

    def test_publish_pipeline_with_version_badge_passes(self, tmp_path):
        """A publish-pipeline plugin whose README carries a real version badge clears."""
        plugin = _make_plugin(tmp_path, has_publish_py=True, readme=_BADGE_README)
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        assert any("version badge" in m for m in _messages(report, "PASSED"))
        assert not any("version-X.Y.Z-blue" in m for m in _messages(report, "WARNING"))

    def test_publish_pipeline_with_no_badge_warns(self, tmp_path):
        """A publish-pipeline plugin whose README has no version badge draws the WARNING."""
        plugin = _make_plugin(tmp_path, has_publish_py=True, readme=_NO_BADGE_README)
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        warnings = _messages(report, "WARNING")
        assert any("version-X.Y.Z-blue" in m for m in warnings)
        assert any("stage_update_badges" in m for m in warnings)

    def test_publish_pipeline_with_missing_readme_warns(self, tmp_path):
        """A publish-pipeline plugin with no README.md at all also draws the missing-badge WARNING
        (there is nowhere for a badge to live)."""
        plugin = _make_plugin(tmp_path, has_publish_py=True)
        report = ValidationReport()
        validate_pipeline_readiness(plugin, report)
        assert any("version-X.Y.Z-blue" in m for m in _messages(report, "WARNING"))
