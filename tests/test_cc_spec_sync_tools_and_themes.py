#!/usr/bin/env python3
"""CC spec-sync: two documented surfaces CPV had drifted away from.

Both are FALSE POSITIVES — CPV flagged something Claude Code documents as valid:

1. ``VALID_TOOLS`` was missing ``ReportFindings`` (tools-reference, v2.1.196) and
   ``SendUserFile``. An agent declaring either in ``tools:`` was flagged as
   referencing an unknown tool.

2. ``validate_plugin.known_dirs`` was missing ``themes`` — a DEFAULT component
   directory in the plugins-reference "Directory | Purpose" table ("Color theme
   definitions"), a peer of ``skills/`` / ``agents/`` / ``commands/`` /
   ``output-styles/`` / ``bin/``. A plugin shipping ``themes/`` drew a
   publish-blocking ``RC-NONSTD-DIR-001`` MAJOR.

Every assertion runs through the REAL validator, and every "this is now accepted"
assertion is paired with a POSITIVE CONTROL proving the same code path still
rejects a genuinely bogus value — otherwise an "accepted" test passes vacuously.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# ────────────────────────────────────────────────────────────────────────
# 1. Tools — through the REAL agent tools-field validator.
# ────────────────────────────────────────────────────────────────────────


def _tool_findings(tools: str) -> list[str]:
    """Run the REAL validate_tools_field and return the 'unknown tool' messages."""
    from validate_agent import (  # type: ignore[import-not-found]
        AgentValidationReport,
        validate_tools_field,
    )

    report = AgentValidationReport()
    validate_tools_field({"tools": tools}, "x.md", report)
    return [
        str(getattr(r, "message", r)) for r in report.results if "Unknown tool" in str(getattr(r, "message", r))
    ]


class TestNewlyDocumentedToolsAreAccepted:
    def test_report_findings_accepted(self) -> None:
        assert not [m for m in _tool_findings("ReportFindings") if "ReportFindings" in m]

    def test_send_user_file_accepted(self) -> None:
        assert not [m for m in _tool_findings("SendUserFile") if "SendUserFile" in m]

    def test_both_alongside_ordinary_tools(self) -> None:
        found = _tool_findings("Read, Grep, ReportFindings, SendUserFile")
        assert not [m for m in found if "ReportFindings" in m or "SendUserFile" in m]

    def test_positive_control_bogus_tool_still_flagged(self) -> None:
        """The same code path must still reject a tool that does not exist.

        Without this, the three assertions above would pass even if the
        validator had stopped checking tools altogether.
        """
        assert [m for m in _tool_findings("NotARealToolXYZ") if "NotARealToolXYZ" in m]


def test_valid_tools_contains_the_two_documented_tools() -> None:
    from cpv_validation_common import VALID_TOOLS  # type: ignore[import-not-found]

    assert {"ReportFindings", "SendUserFile"} <= set(VALID_TOOLS)


# ────────────────────────────────────────────────────────────────────────
# 2. themes/ — through the REAL structure validator.
# ────────────────────────────────────────────────────────────────────────


def _structure_messages(tmp_path: Path, dirname: str) -> list[str]:
    """Build a minimal plugin carrying `dirname/` and run the real validator."""
    from cpv_validation_common import ValidationReport  # type: ignore[import-not-found]
    from validate_plugin import validate_structure  # type: ignore[import-not-found]

    root = tmp_path / f"plugin-{dirname}"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "version": "1.0.0", "description": "d"}), encoding="utf-8"
    )
    (root / dirname).mkdir()
    (root / dirname / "sample.json").write_text("{}", encoding="utf-8")

    report = ValidationReport()
    validate_structure(root, report)
    return [str(getattr(r, "message", r)) for r in report.results]


class TestThemesIsAKnownComponentDir:
    def test_themes_dir_no_longer_flagged(self, tmp_path: Path) -> None:
        flagged = [m for m in _structure_messages(tmp_path, "themes") if "RC-NONSTD-DIR-001" in m]
        assert flagged == [], flagged

    def test_positive_control_unknown_dir_still_flagged(self, tmp_path: Path) -> None:
        """A genuinely non-standard root dir must STILL raise the MAJOR.

        This is what proves the test above measured a real clear rather than a
        validator that stopped checking directories.
        """
        flagged = [m for m in _structure_messages(tmp_path, "zzbogusdir") if "RC-NONSTD-DIR-001" in m]
        assert flagged, "RC-NONSTD-DIR-001 must still fire on a genuinely unknown dir"

    def test_every_documented_default_dir_is_known(self) -> None:
        """The full plugins-reference 'Directory | Purpose' set, pinned.

        `known_dirs` is a local inside validate_structure, so it is read from the
        source rather than imported.
        """
        import re

        src = (SCRIPTS / "validate_plugin.py").read_text(encoding="utf-8")
        start = src.index("known_dirs = {")
        # Brace-match to the close: a plain `src.index("}")` stops at the first
        # brace, which truncates the set and silently drops later entries.
        depth = 0
        end = start
        for pos in range(start, len(src)):
            if src[pos] == "{":
                depth += 1
            elif src[pos] == "}":
                depth -= 1
                if depth == 0:
                    end = pos
                    break
        assert end > start, "could not locate the end of the known_dirs set"
        known = set(re.findall(r'"([^"]+)"', src[start : end + 1]))
        documented = {"skills", "commands", "agents", "output-styles", "themes", "bin"}
        assert documented <= known, sorted(documented - known)
