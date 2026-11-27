"""Gap-report items A1, A5, A7 (mods): plugin-layout false positives on mod-shaped plugins.

A1: a root `types/` dir (manifest `types: ./types/index.d.ts`) must not draw RC-NONSTD-DIR-001,
    while a random unknown dir still does.
A5: a manifest `hooks` file that exists counts as plugin content (no "manifest but no content"
    MAJOR); a manifest with nothing at all still gets it.
A7: a `hooks/*.test.ts` file is a test, never an "untested" hook component.
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
from validate_plugin import check_test_coverage, validate_structure  # noqa: E402


def _make_plugin(root: Path, manifest: dict[str, object], files: dict[str, str]) -> None:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _structure_messages(root: Path) -> list[str]:
    report = ValidationReport()
    validate_structure(root, report)
    return [r.message for r in report.results]


def test_a1_types_dir_not_flagged_nonstd(tmp_path: Path) -> None:
    """A root types/ dir named by the manifest `types` field draws no RC-NONSTD-DIR-001."""
    _make_plugin(
        tmp_path,
        {"name": "m", "version": "1.0.0", "description": "d", "types": "./types/index.d.ts"},
        {"types/index.d.ts": "export {};\n", "scripts/a.py": "x = 1\n"},
    )
    msgs = [m for m in _structure_messages(tmp_path) if "RC-NONSTD-DIR-001" in m]
    assert not any("'types/'" in m for m in msgs), msgs


def test_a1_unknown_dir_still_flagged(tmp_path: Path) -> None:
    """A random unknown dir still draws RC-NONSTD-DIR-001 (no blanket exemption)."""
    _make_plugin(
        tmp_path,
        {"name": "m", "version": "1.0.0", "description": "d"},
        {"foo/x.txt": "x\n", "scripts/a.py": "x = 1\n"},
    )
    msgs = [m for m in _structure_messages(tmp_path) if "RC-NONSTD-DIR-001" in m]
    assert any("'foo/'" in m for m in msgs), msgs


def test_a5_manifest_hooks_file_counts_as_content(tmp_path: Path) -> None:
    """Manifest `hooks` naming an existing file means the plugin has content."""
    _make_plugin(
        tmp_path,
        {"name": "m", "version": "1.0.0", "description": "d", "hooks": "./custom/hooks.json"},
        {"custom/hooks.json": json.dumps({"hooks": {}})},
    )
    assert not [m for m in _structure_messages(tmp_path) if "manifest but no content" in m]


def test_a5_empty_manifest_still_flagged(tmp_path: Path) -> None:
    """A manifest with nothing at all still gets the no-content MAJOR."""
    _make_plugin(tmp_path, {"name": "m", "version": "1.0.0", "description": "d"}, {})
    assert [m for m in _structure_messages(tmp_path) if "manifest but no content" in m]


def test_a5_missing_hooks_file_still_flagged(tmp_path: Path) -> None:
    """A manifest `hooks` naming a file that does not exist is not content."""
    _make_plugin(
        tmp_path,
        {"name": "m", "version": "1.0.0", "description": "d", "hooks": "./custom/hooks.json"},
        {},
    )
    assert [m for m in _structure_messages(tmp_path) if "manifest but no content" in m]


def test_a7_hook_test_file_not_listed_untested(tmp_path: Path) -> None:
    """hooks/register.test.ts is a test file, not an untested hook component."""
    _make_plugin(
        tmp_path,
        {"name": "m", "version": "1.0.0", "description": "d"},
        {
            "hooks/register.ts": "export {};\n",
            "hooks/register.test.ts": "import './register';\n",
            "hooks/other.ts": "export {};\n",
        },
    )
    report = ValidationReport()
    check_test_coverage(tmp_path, report)
    warnings = [r.message for r in report.results if "RC-TEST-COVERAGE" in r.message]
    assert warnings, "expected an advisory naming hooks/other.ts"
    assert "hooks/other.ts" in warnings[0]
    assert "register.test.ts" not in warnings[0], warnings
