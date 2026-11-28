#!/usr/bin/env python3
"""Issue #295: Homebrew keg-only formula paths under <prefix>/opt/ are fixed system locations.

A keg-only formula (e.g. node@22) is linked only under <prefix>/opt/<formula>,
never into <prefix>/bin, so `/opt/homebrew/opt/node@22/bin/node` is the sole
install location and has no plugin-relative form. It must be downgraded to INFO
like `/opt/homebrew/bin/node`. Controls pin that the prefix match stays exact.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    scan_file_for_absolute_paths,
)

# `level` is the field name on a result; a wrong attribute would make every
# absence-assertion below pass vacuously (see test_bare_system_binary_dir.py).
_BLOCKING_LEVELS = {"CRITICAL", "MAJOR", "MINOR", "NIT"}


def _scan(tmp_path: Path, name: str, content: str) -> ValidationReport:
    target = tmp_path / name
    target.write_text(content, encoding="utf-8")
    report = ValidationReport()
    scan_file_for_absolute_paths(target, report, tmp_path)
    return report


def _blocking(report: ValidationReport) -> list[tuple[str, str]]:
    return [(r.level.upper(), r.message) for r in report.results if r.level.upper() in _BLOCKING_LEVELS]


def test_apple_silicon_keg_only_path_does_not_block(tmp_path: Path) -> None:
    """/opt/homebrew/opt/node@22/bin/node in a .mjs candidate list gives no blocking finding."""
    content = 'export const POSIX_CANDIDATES = ["/opt/homebrew/opt/node@22/bin/node", "/opt/homebrew/bin/node", "/usr/local/bin/node"];\n'
    report = _scan(tmp_path, "probe.mjs", content)
    assert not _blocking(report), _blocking(report)


def test_intel_keg_only_path_does_not_block(tmp_path: Path) -> None:
    """/usr/local/opt/python@3.12/bin/python3 in a .mjs candidate list gives no blocking finding."""
    content = 'export const POSIX_CANDIDATES = ["/usr/local/opt/python@3.12/bin/python3", "/usr/local/bin/python3"];\n'
    report = _scan(tmp_path, "probe.mjs", content)
    assert not _blocking(report), _blocking(report)


def test_home_path_still_fires(tmp_path: Path) -> None:
    """Control: a /Users/<name> home path in a .mjs file still blocks."""
    report = _scan(tmp_path, "probe.mjs", 'export const P = "/Users/alice/project/x";\n')
    assert _blocking(report), "a home path was cleared"


def test_confusable_homebrew_prefix_still_minor(tmp_path: Path) -> None:
    """Control: /opt/homebrewfoo/bin/node is not under /opt/homebrew/ and still draws a MINOR."""
    report = _scan(tmp_path, "probe.mjs", 'export const P = "/opt/homebrewfoo/bin/node";\n')
    levels = [lvl for lvl, _ in _blocking(report)]
    assert "MINOR" in levels, _blocking(report)
