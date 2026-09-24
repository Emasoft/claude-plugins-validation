"""CC 2.1.281 sync — the doctor reads `claude plugin validate --json`.

The old helper scraped CC's human-readable output and returned two empty lists
when the CLI was missing, timed out, or printed something it could not parse,
so "could not check" was indistinguishable from "checked, clean". These tests
drive the REAL subprocess path through a fake `claude` executable on PATH.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from manage_doctor import _run_claude_validate  # noqa: E402


def _fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str, stderr: str = "", code: int = 0) -> Path:
    """Put an executable `claude` on PATH that prints fixed output and records its argv."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "out.txt").write_text(stdout)
    (tmp_path / "err.txt").write_text(stderr)
    script = bindir / "claude"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" > "{tmp_path}/argv.txt"\n'
        f'cat "{tmp_path}/out.txt"\n'
        f'cat "{tmp_path}/err.txt" >&2\n'
        f"exit {code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    return tmp_path / "argv.txt"


@pytest.mark.skipif(sys.platform == "win32", reason="shell-script fake CLI")
class TestDoctorJson:
    def test_findings_mapped_from_json(self, tmp_path, monkeypatch):
        """errors/warnings from manifest and contents blocks are mapped; --json is passed."""
        payload = {
            "success": False,
            "manifest": {"file": "/p/.claude-plugin/plugin.json", "errors": [], "warnings": [
                {"path": "author", "message": "No author information provided", "code": None}]},
            "contents": [{"file": "/p/.mcp.json", "errors": [
                {"path": "mcpServers.x.type", "message": 'server has a "url" but no "type"', "code": None}],
                "warnings": []}],
        }
        argv = _fake_claude(tmp_path, monkeypatch, json.dumps(payload), code=1)
        errors, warnings, unknown = _run_claude_validate(tmp_path)
        assert unknown is None
        assert errors == ['claude validate: .mcp.json:mcpServers.x.type: server has a "url" but no "type"']
        assert warnings == ["claude validate: plugin.json:author: No author information provided"]
        assert "--json" in argv.read_text().split()

    def test_clean_json_is_clean(self, tmp_path, monkeypatch):
        """Control: a clean JSON result is clean (no UNKNOWN), so the UNKNOWN path is not blanket."""
        _fake_claude(tmp_path, monkeypatch, json.dumps({"success": True, "manifest": {}, "contents": []}))
        assert _run_claude_validate(tmp_path) == ([], [], None)

    def test_missing_cli_is_unknown(self, tmp_path, monkeypatch):
        """No claude on PATH → UNKNOWN with a reason, never an empty clean result."""
        monkeypatch.setenv("PATH", str(tmp_path))
        errors, warnings, unknown = _run_claude_validate(tmp_path)
        assert (errors, warnings) == ([], [])
        assert unknown and "not found" in unknown

    def test_malformed_json_is_unknown(self, tmp_path, monkeypatch):
        """Unparseable output → UNKNOWN (a rewording of CC's output must not read as clean)."""
        _fake_claude(tmp_path, monkeypatch, "Validating plugin...\n✔ passed\n")
        errors, warnings, unknown = _run_claude_validate(tmp_path)
        assert (errors, warnings) == ([], [])
        assert unknown and "without JSON" in unknown

    def test_old_cli_rejecting_json_is_unknown(self, tmp_path, monkeypatch):
        """An older CLI that rejects --json (stderr + exit 1) → UNKNOWN naming the reason."""
        _fake_claude(tmp_path, monkeypatch, "", stderr="error: unknown option '--json'", code=1)
        errors, warnings, unknown = _run_claude_validate(tmp_path)
        assert (errors, warnings) == ([], [])
        assert unknown and "unknown option" in unknown and "exited 1" in unknown

    def test_failure_without_detail_is_an_error(self, tmp_path, monkeypatch):
        """success:false with no listed error still yields one error — a failed validate is never clean."""
        _fake_claude(tmp_path, monkeypatch, json.dumps({"success": False, "manifest": {}, "contents": []}), code=1)
        errors, _, unknown = _run_claude_validate(tmp_path)
        assert unknown is None
        assert len(errors) == 1 and "reported failure" in errors[0]
