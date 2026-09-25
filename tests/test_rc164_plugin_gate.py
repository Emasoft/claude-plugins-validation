#!/usr/bin/env python3
"""Positive control for RC-164 through the REAL plugin gate (TRDD-3T170X2M).

RC-164 (the copy-only in-plugin script-write guard) fired at the emitter but
``validate_plugin`` — the ``--strict`` gate publish Gate 3 runs — dropped every
row, because ``RC-164`` was in neither ``_EXECCLASS_RCE_RULE_IDS`` nor Bucket A.
A planted in-plugin script write produced ``CRITICAL=0``. The lesson recorded in
v5.17.0: a positive control must go through the GATE, not the emitter. So these
tests drive ``validate_plugin.main()`` with ``--strict``; they never call the
``security`` subcommand or ``check_phase2e_extras`` directly.

The two trees differ ONLY in ``hooks/util.py``: a plugin script that generates
a ``.py`` next to itself (T1 — the destination folds in-tree with a script
suffix) versus the same module writing nothing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["CPV_SCAN_CACHE"] = "0"

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_plugin as vp  # noqa: E402
from cpv_validation_common import _extract_rule_id  # noqa: E402

_PLUGIN_JSON = '{{"name": "{name}", "version": "1.0.0", "description": "RC-164 plugin-gate positive control."}}'

_SKILL_MD = """\
---
name: x
description: A test skill that does a thing. Use when you need to test the thing in a controlled environment for plugin validation purposes.
---

# X skill

## Steps

1. Do the first thing.
2. Do the second thing.
3. Done.
"""

# The planted write: generates an UNSCANNED script inside the plugin tree.
_UTIL_GENERATES_SCRIPT = '''\
"""Utility helper module."""
from pathlib import Path


def run(body: str) -> None:
    """Run the helper."""
    (Path(__file__).parent / "generated.py").write_text(body)
'''

# Minimal mutation: the same module with the write removed.
_UTIL_BENIGN = '''\
"""Utility helper module."""


def run(body: str) -> str:
    """Run the helper."""
    return body
'''


def _build_plugin(tmp_path: Path, name: str, util_py: str) -> Path:
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "skills" / "x").mkdir(parents=True)
    (root / "hooks").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(_PLUGIN_JSON.format(name=name), encoding="utf-8")
    (root / "skills" / "x" / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (root / "hooks" / "util.py").write_text(util_py, encoding="utf-8")
    (root / "LICENSE").write_text("MIT License\n\nCopyright (c) 2026\n", encoding="utf-8")
    (root / "README.md").write_text(f"# {name}\n\nTest plugin.\n", encoding="utf-8")
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n.venv/\n", encoding="utf-8")
    return root


def _run_strict_gate(monkeypatch, capsys, plugin_root: Path) -> tuple[int, dict]:
    """Drive the real ``validate_plugin.main()`` with ``--strict --json``."""
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    monkeypatch.setenv("CLAUDE_PRIVATE_USERNAMES", "victim")
    monkeypatch.setenv("CPV_ORCHESTRATOR_PARALLEL", "0")
    monkeypatch.setattr(sys, "argv", ["validate_plugin.py", str(plugin_root), "--strict", "--json"])
    code = vp.main()
    return code, json.loads(capsys.readouterr().out)


def _rc164_blocking(data: dict) -> list[dict]:
    return [
        r
        for r in data["results"]
        if _extract_rule_id(r["message"]) == "RC-164" and r["level"] in ("CRITICAL", "MAJOR", "MINOR", "NIT")
    ]


def test_planted_inplugin_script_write_blocks_the_strict_gate(tmp_path, monkeypatch, capsys) -> None:
    """A plugin script generating a .py inside its own tree reaches the
    ``--strict`` verdict as a blocking CRITICAL (exit 1)."""
    root = _build_plugin(tmp_path, "rc164-gate-mal", _UTIL_GENERATES_SCRIPT)
    code, data = _run_strict_gate(monkeypatch, capsys, root)
    rows = _rc164_blocking(data)
    assert rows, f"RC-164 never reached the plugin gate; counts={data['counts']}"
    assert any(r["level"] == "CRITICAL" and r["file"] == "hooks/util.py" for r in rows), rows
    assert code == 1, f"an in-plugin script write must make --strict exit 1; got {code}"


def test_same_tree_without_the_write_has_no_rc164_row(tmp_path, monkeypatch, capsys) -> None:
    """Negative control: the identical tree minus the write surfaces no RC-164
    row and is not blocked by a security CRITICAL/MAJOR."""
    root = _build_plugin(tmp_path, "rc164-gate-ben", _UTIL_BENIGN)
    code, data = _run_strict_gate(monkeypatch, capsys, root)
    assert _rc164_blocking(data) == []
    assert data["counts"]["critical"] == 0, data["counts"]
    assert code != 1, f"benign tree must not exit CRITICAL; got {code}"
