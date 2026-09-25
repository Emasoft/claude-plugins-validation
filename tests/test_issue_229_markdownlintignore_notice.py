#!/usr/bin/env python3
"""Issue #229 — CPV never told the author markdownlint-cli2 ignores
``.markdownlintignore``.

markdownlint-cli2 does not read the legacy ``.markdownlintignore`` file at
all; it reads the ``ignores`` array from ``.markdownlint-cli2.jsonc`` (or
``.yaml``/``.cjs``). A repo that only carries ``.markdownlintignore`` gets NO
exclusions applied by the tool CPV invokes, and nothing said so.

``lint_markdown`` now emits exactly one non-blocking WARNING naming both
files when ``.markdownlintignore`` exists at the repo root. It must fire
even when markdownlint-cli2 itself is not installed — the gap is about
CONFIG, not tool availability — so the check runs before the `_resolve`
early-return.

Two-sided:
  * file present  -> exactly one WARNING mentioning both `.markdownlintignore`
    and `ignores`.
  * file absent   -> no such warning.
  * WARNING is non-blocking under `exit_code_strict` (never a NIT).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# tests/conftest.py adds scripts/ to sys.path; defensive duplicate so the file
# works when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_lint_engine import lint_markdown  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _markdownlintignore_warnings(report: ValidationReport) -> list[str]:
    return [
        r.message
        for r in report.results
        if r.level == "WARNING" and ".markdownlintignore" in r.message
    ]


class TestMarkdownlintignoreNotice:
    """lint_markdown warns about a stale .markdownlintignore, non-blocking."""

    def test_file_present_emits_one_warning(self, tmp_path: Path) -> None:
        """A `.markdownlintignore` at repo root -> exactly one WARNING."""
        (tmp_path / ".markdownlintignore").write_text("downloads_dev/\n")
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        # markdownlint-cli2 unavailable — the notice must still fire, since
        # the gap is about config, not the tool being installed.
        with patch("cpv_lint_engine._resolve", return_value=None):
            lint_markdown(tmp_path, [f], report)
        warns = _markdownlintignore_warnings(report)
        assert len(warns) == 1, f"expected exactly one notice, got {warns}"
        assert ".markdownlintignore" in warns[0]
        assert "ignores" in warns[0]
        assert "markdownlint-cli2" in warns[0]

    def test_file_absent_emits_no_warning(self, tmp_path: Path) -> None:
        """No `.markdownlintignore` -> the notice never fires."""
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            lint_markdown(tmp_path, [f], report)
        assert _markdownlintignore_warnings(report) == []

    def test_warning_is_non_blocking(self, tmp_path: Path) -> None:
        """The notice is WARNING level, never NIT — it must not block --strict."""
        (tmp_path / ".markdownlintignore").write_text("scratch/\n")
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=None):
            lint_markdown(tmp_path, [f], report)
        nits_mentioning_ignore = [
            r
            for r in report.results
            if r.level == "NIT" and ".markdownlintignore" in r.message
        ]
        assert nits_mentioning_ignore == [], "must never surface as a NIT"
