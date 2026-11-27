#!/usr/bin/env python3
"""Issue #262 half A (Markdown): RC-39 must not report a prose line whose only
persistence-looking inline-code spans are launchd REMOVALS, and must keep every
line with an install span, an odd backtick count, or persistence in the prose.
Non-Markdown files keep the old behaviour."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["CPV_SCAN_CACHE"] = "0"

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_security as vsec  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

PLIST = "~/Library/LaunchAgents/com.x.plist"


def _rc39(tmp_path: Path, name: str, content: str) -> list[str]:
    (tmp_path / ".claude-plugin").mkdir(exist_ok=True)
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(content)
    report = ValidationReport()
    vsec.check_phase2e_extras(tmp_path, report)
    return [r.message for r in report.results if "RC-39" in r.message]


CLEARED = [
    f"- `rm -f {PLIST}`",
    f"Remove it: `launchctl bootout gui/501/com.x` then `rm -f {PLIST}`",
    f"- Remove the agent: `launchctl bootout gui/501/com.x` then `rm -f {PLIST}`",
    f"Run `launchctl unload -w {PLIST}` and then `rm {PLIST}` to uninstall.",
]


@pytest.mark.parametrize("line", CLEARED)
@pytest.mark.parametrize("ext", ["md", "mdx", "markdown"])
def test_markdown_inline_code_removal_not_reported(tmp_path: Path, line: str, ext: str) -> None:
    """A Markdown line whose matching inline-code spans are pure launchd removals is not RC-39."""
    assert _rc39(tmp_path, f"docs/removal.{ext}", f"# Notes\n\n{line}\n") == []


STILL_FLAGGED = [
    # install span on the same line as a removal span
    f"`rm -f {PLIST}` and `launchctl bootstrap gui/501 {PLIST}`",
    f"`launchctl bootstrap gui/501 {PLIST}` and `rm -f {PLIST}`",
    # install shapes into LaunchAgents
    "`cp x.plist ~/Library/LaunchAgents/`",
    f"`rm -f {PLIST}` then `cp x.plist ~/Library/LaunchAgents/`",
    f"`rm -f {PLIST}` then `cat new > {PLIST}`",
    f"`rm -f {PLIST}` then `echo x | tee {PLIST}`",
    f"`rm -f {PLIST}` then `ln -s x {PLIST}`",
    f"`rm -f {PLIST}` then `launchctl load {PLIST}`",
    f"`rm -f {PLIST}` then `launchctl enable gui/501/com.x` and `launchctl bootstrap gui/501 {PLIST}`",
    # a chained install INSIDE one span
    f"`rm -f {PLIST} && cp x.plist {PLIST}`",
    # persistence in the prose outside the spans
    f"Copy the plist into ~/Library/LaunchAgents/ and load it, or `rm -f {PLIST}`",
    f"`rm -f {PLIST}` then run launchctl load {PLIST} by hand",
    # odd backtick count (the reporter's line 196 shape)
    f"  2>/dev/null || true` + `rm -f {PLIST}`,",
    f"`rm -f {PLIST}` and `launchctl bootout gui/501/com.x",
    # a span that is only a path is not a removal
    f"Next file: `{PLIST}` then write it",
]


@pytest.mark.parametrize("line", STILL_FLAGGED)
def test_markdown_install_or_ambiguous_line_still_reported(tmp_path: Path, line: str) -> None:
    """An install span, odd backtick count or persistence prose keeps RC-39 on a Markdown line."""
    assert len(_rc39(tmp_path, "docs/removal.md", f"# Notes\n\n{line}\n")) >= 1


def test_markdown_reporter_fixture_lines(tmp_path: Path) -> None:
    """The reporter docs/removal.md fixture: the odd-backtick line 3 stays flagged."""
    content = (
        "# Notes\n\n"
        "  2>/dev/null || true` + `rm -f ~/Library/LaunchAgents/com.emasoft.claude-account-rotator.plist`,\n"
        "- REMOVE: stub install (step 3), plist render (step 4), `launchctl bootstrap` (step 5), and the\n"
    )
    found = _rc39(tmp_path, "docs/removal.md", content)
    assert any("line 3:" in m for m in found)


def test_markdown_removal_span_does_not_hide_install_on_next_line(tmp_path: Path) -> None:
    """Clearing line 3 must not clear the install span on line 4."""
    content = f"# Notes\n\n- `rm -f {PLIST}`\n- `cp x.plist ~/Library/LaunchAgents/`\n"
    found = _rc39(tmp_path, "docs/removal.md", content)
    assert any("line 4:" in m for m in found)
    assert not any("line 3:" in m for m in found)


def test_shell_file_with_backticked_removal_unchanged(tmp_path: Path) -> None:
    """A non-Markdown file never gets the inline-code clearing (backticks there are command substitution)."""
    found = _rc39(tmp_path, "run.sh", f"#!/bin/sh\necho `rm -f {PLIST}`\n")
    assert len(found) >= 1


def test_shell_install_control_unchanged(tmp_path: Path) -> None:
    """The ctrl/install.sh shell installer is still reported."""
    script = (
        "#!/bin/sh\n"
        'cat > "$HOME/Library/LaunchAgents/x.plist" <<PL\nx\nPL\n'
        'launchctl bootstrap gui/501 "$HOME/Library/LaunchAgents/x.plist"\n'
    )
    assert len(_rc39(tmp_path, "ctrl/install.sh", script)) >= 1
