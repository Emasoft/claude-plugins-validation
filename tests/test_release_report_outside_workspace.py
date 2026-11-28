"""The release validate step must not write its report inside the tree it validates.

The integrity check finds added files by a disk walk as well as `git ls-files`
(TRDD-DDL1C1T2), so a `validation-report.txt` `tee`d into the checkout while the
validator runs is reported as an added/inoculated file and the validator exits 2
(the red v5.29.0 Release run). The report belongs in $RUNNER_TEMP.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

from generate_plugin_repo import PluginParams, gen_release_yml  # noqa: E402

_BARE_WRITE = re.compile(r"(?:tee\s+(?:-a\s+)?|>>?\s*)\"?validation-report\.txt")


def _params() -> PluginParams:
    return PluginParams(
        name="demo-plugin",
        description="A demo plugin",
        author="Demo Author",
        author_email="demo@example.com",
        version="0.1.0",
    )


def test_generated_release_workflow_does_not_write_report_to_bare_path() -> None:
    """The scaffolded release.yml never tees or redirects validation-report.txt to a bare relative path."""
    text = gen_release_yml(_params())
    assert not _BARE_WRITE.search(text), "generated release.yml writes the report inside the workspace"
    assert "$RUNNER_TEMP/validation-report.txt" in text


def test_own_release_workflow_does_not_write_report_to_bare_path() -> None:
    """CPV's own .github/workflows/release.yml never tees or redirects validation-report.txt to a bare relative path."""
    text = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert not _BARE_WRITE.search(text), "release.yml writes the report inside the workspace"
    assert "$RUNNER_TEMP/validation-report.txt" in text
