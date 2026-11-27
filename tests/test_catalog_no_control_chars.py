"""Catalog regression (TRDD-1STNHQ21): a single backslash before `b` inside a JSON
string decodes to U+0008 (backspace), so the pattern can never match ordinary
text. No catalog pattern may contain a control character, and the seven
patterns that used to carry one must match their intended words again, scoped
to the reconnaissance COMMAND shape (command position) so identifiers, method
calls, imports and SDK idioms stay silent.

Real `scan_content` calls on real temp files; no mocks.
"""

from __future__ import annotations

import json
from pathlib import Path

import cpv_skillaudit_native as native
import pytest

_CATALOG = Path(native.__file__).resolve().parent / "rules" / "skillaudit_patterns.json"


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the scan cache so every call really scans."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def test_no_catalog_pattern_contains_a_control_character() -> None:
    """Every decoded catalog pattern has no character below U+0020."""
    data = json.loads(_CATALOG.read_text(encoding="utf-8"))
    bad = [
        (rule["id"], pattern)
        for rule in data["rules"]
        for pattern in rule.get("patterns", [])
        if isinstance(pattern, str) and any(ord(ch) < 0x20 for ch in pattern)
    ]
    assert not bad, f"patterns with a control character: {bad!r}"


_SH = "x.sh"
_PY = "x.py"
_TS = "x.ts"
_MD = "x.md"
_JS = "x.js"

# Samples are Python strings written to temp files and scanned; nothing is ever
# executed. \x27 is a single quote and \x60 a backtick (kept out of the source
# so the file is safe to pass through a shell).

# Reconnaissance COMMAND shapes (TRDD-1STNHQ21 scoping): command position only.
_POSITIVES = [
    ("ENV_RECON", _SH, "whoami"),
    ("ENV_RECON", _SH, "  whoami 2>/dev/null"),
    ("ENV_RECON", _SH, "echo $(whoami)"),
    ("ENV_RECON", _SH, "id=\x60whoami\x60"),
    ("ENV_RECON", _SH, "cd /tmp; whoami"),
    ("ENV_RECON", _SH, "true && whoami"),
    ("ENV_RECON", _SH, "false || whoami"),
    ("ENV_RECON", _SH, "echo ok | whoami"),
    ("ENV_RECON", _SH, "if [ \"$(whoami)\" = root ]; then"),
    ("ENV_RECON", _SH, "sudo whoami"),
    ("ENV_RECON", _SH, "exec whoami"),
    ("ENV_RECON", _SH, "whoami | tee /tmp/out"),
    ("ENV_RECON", _SH, "$ whoami"),
    ("ENV_RECON", _SH, "bash -c \x27whoami\x27"),
    ("ENV_RECON", _SH, "cmd.exe /c whoami"),
    ("ENV_RECON", _SH, "whoami.exe /all"),
    ("ENV_RECON", _PY, "subprocess.check_output([\"whoami\"])"),
    ("ENV_RECON", _PY, "os.system(\"whoami\")"),
    ("ENV_RECON", _PY, "subprocess.run(\"whoami\", shell=True)"),
    ("ENV_RECON", "x.yml", "      - run: whoami"),
    ("ENV_RECON", _SH, "net user"),
    ("ENV_RECON", _SH, "net user admin /add"),
    ("ENV_RECON", _SH, "cd /tmp; net user"),
    ("PERSISTENCE", _SH, "cp x ~/.config/autostart/"),
    ("PERSISTENCE", _SH, "cp x /etc/xdg/autostart/"),
    ("PERSISTENCE", _SH, "cp x ${XDG_CONFIG_HOME}/autostart/x.desktop"),
    ("CROSS_TOOL_ACCESS", _SH, "tools = list_tools()"),
    ("CROSS_TOOL_ACCESS", _SH, "const tools = get_tools();"),
    ("CROSS_TOOL_ACCESS", _SH, "PREV=$(get_tools | jq .results)"),
    ("CROSS_TOOL_ACCESS", _SH, "node -e \x27get_tools()\x27"),
    ("CROSS_TOOL_ACCESS", _SH, "x = available_tools()"),
    ("CROSS_TOOL_ACCESS", _SH, "call_tool(\"x\")"),
    ("CROSS_TOOL_ACCESS", _SH, "result = call_tool()"),
    ("CROSS_TOOL_ACCESS", _SH, "invoke_tool(\"x\")"),
    ("CROSS_TOOL_ACCESS", _SH, "return use_tool(\"x\")"),
]

# False-positive shapes measured on the plugin cache: identifiers, method calls,
# imports, SDK idioms, subcommands of other CLIs, capability probes, prose.
_NEGATIVES = [
    ("ENV_RECON", _SH, "whoamix"),
    ("ENV_RECON", _SH, "xwhoami"),
    ("ENV_RECON", _SH, "ifconfigx"),
    ("ENV_RECON", _SH, "systeminfox"),
    ("ENV_RECON", _SH, "net users"),
    ("ENV_RECON", _MD, "allowed-tools: \"Bash(tailscale:*), Bash(ifconfig:*), Read\""),
    ("ENV_RECON", _SH, "ALLOW=\x27^(ls|pwd|echo|date|whoami)\x27"),
    ("ENV_RECON", _TS, "exec(\x27npm whoami\x27).then(() => {});"),
    ("ENV_RECON", _PY, "user = api.whoami()"),
    ("ENV_RECON", _PY, "from huggingface_hub import whoami"),
    ("ENV_RECON", _PY, "user = whoami()"),
    ("ENV_RECON", _PY, "def whoami():"),
    ("ENV_RECON", _SH, "clawhub whoami"),
    ("ENV_RECON", _SH, "aimaestro-governance.sh whoami | jq ."),
    ("ENV_RECON", _PY, "cmd = [cli, \"whoami\"]"),
    ("ENV_RECON", _PY, "path = shutil.which(\"whoami\")"),
    ("ENV_RECON", _PY, "path = shutil.which(\"ifconfig\")"),
    ("ENV_RECON", _PY, "print(whoami)"),
    ("ENV_RECON", _PY, "f(whoami, x)"),
    ("ENV_RECON", _PY, "run(ifconfig, x)"),
    ("ENV_RECON", _TS, "export interface SystemInfo {"),
    ("ENV_RECON", _TS, "export function detectSystemInfo(): SystemInfo {"),
    ("ENV_RECON", _SH, "if (( RAM_GB == 0 )) && command -v systeminfo >/dev/null 2>&1; then"),
    ("ENV_RECON", _MD, "Any unauthenticated internet user can access the collection."),
    ("ENV_RECON", _PY, "msg = \"any internet user can self-register\""),
    ("ENV_RECON", _MD, "The ip address of the host is not collected."),
    ("PERSISTENCE", _SH, "autostarter"),
    ("PERSISTENCE", _JS, "this.props.autostart = false;"),
    ("PERSISTENCE", _MD, "- list of start-up files: .config\\/(?:systemd\\/user|autostart)\\/ entries"),
    ("CROSS_TOOL_ACCESS", _SH, "list_toolsx"),
    ("CROSS_TOOL_ACCESS", _SH, "call_toolx"),
    ("CROSS_TOOL_ACCESS", _PY, "    response = await self.session.list_tools()"),
    ("CROSS_TOOL_ACCESS", _PY, "tools = await connection.list_tools()"),
    ("CROSS_TOOL_ACCESS", _PY, "result = await connection.call_tool(tool_name, tool_input)"),
    ("CROSS_TOOL_ACCESS", _PY, "names = {t.name for t in server.mcp._tool_manager.list_tools()}"),
    ("CROSS_TOOL_ACCESS", _PY, "async def call_tool(self, tool_name: str, arguments: dict) -> Any:"),
    ("CROSS_TOOL_ACCESS", _PY, "async def list_tools(self) -> list[dict[str, Any]]:"),
    ("CROSS_TOOL_ACCESS", _PY, "@app.list_tools()"),
    ("CROSS_TOOL_ACCESS", _PY, "@app.call_tool()"),
    ("CROSS_TOOL_ACCESS", _PY, "\x27call_tool\x27: handle_call_tool,"),
    ("CROSS_TOOL_ACCESS", _PY, "ALLOWED = {\x27ping\x27, \x27list_tools\x27, \x27call_tool\x27}"),
    ("CROSS_TOOL_ACCESS", _MD, "Handlers are registered with \x60@app.list_tools\x60 and \x60@app.call_tool\x60."),
]


def _scan(tmp_path: Path, name: str, line: str) -> list[dict]:
    """Scan one real temp file holding `line` on its second line; return findings."""
    target = tmp_path / "scripts" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8")
    return native.scan_content(target.read_text(encoding="utf-8"), str(target))


@pytest.mark.parametrize(("rule_id", "name", "line"), _POSITIVES)
def test_scoped_pattern_matches_command_shape(tmp_path: Path, rule_id: str, name: str, line: str) -> None:
    """Each scoped pattern yields its ruleId, unsuppressed, on a reconnaissance command shape."""
    findings = _scan(tmp_path, name, line)
    hits = [f for f in findings if f["ruleId"] == rule_id and not f.get("suppressed")]
    seen = [(f["ruleId"], f["severity"], bool(f.get("suppressed"))) for f in findings]
    assert hits, f"{line!r} -> {seen}"


@pytest.mark.parametrize(("rule_id", "name", "line"), _NEGATIVES)
def test_scoped_pattern_rejects_non_command_shape(tmp_path: Path, rule_id: str, name: str, line: str) -> None:
    """Identifiers, method calls, imports, SDK idioms, probes and prose yield no finding for that rule."""
    findings = _scan(tmp_path, name, line)
    bad = [(f["ruleId"], f["severity"], f.get("match")) for f in findings if f["ruleId"] == rule_id]
    assert not bad, f"{line!r} -> {bad}"


# TRDD-1STNHQ21 option b: reconnaissance commands that legitimate environment detectors run,
# with no declaration path to suppress a finding, so the catalog deliberately has no pattern for them.
_NOT_DETECTED_BY_DESIGN = [
    ("ENV_RECON", _SH, "ifconfig"),
    ("ENV_RECON", _SH, "ifconfig -a"),
    ("ENV_RECON", _SH, "sudo ifconfig en0"),
    ("ENV_RECON", _SH, "echo $(ifconfig)"),
    ("ENV_RECON", _SH, "ip addr show"),
    ("ENV_RECON", _SH, "systeminfo"),
    ("ENV_RECON", _SH, "systeminfo | findstr Host"),
    ("ENV_RECON", _SH, "cmd /c systeminfo"),
]


@pytest.mark.parametrize(("rule_id", "name", "line"), _NOT_DETECTED_BY_DESIGN)
def test_ifconfig_and_systeminfo_not_detected_by_design(tmp_path: Path, rule_id: str, name: str, line: str) -> None:
    """ifconfig, ip addr and systeminfo yield no ENV_RECON finding (TRDD-1STNHQ21 option b)."""
    findings = _scan(tmp_path, name, line)
    bad = [(f["ruleId"], f["severity"], f.get("match")) for f in findings if f["ruleId"] == rule_id]
    assert not bad, f"{line!r} -> {bad}"
