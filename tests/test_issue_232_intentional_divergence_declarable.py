"""#232 — cpv.pipeline.intentional_divergence is declarable.

The filer's corrected report: a top-level ``cpv`` manifest key was rejected as
unknown under ``--strict``, so the documented mechanism for sparing a file from
``--force-templates`` overwrites could not be declared at all. The key is now
admitted (manifest known-keys set), and this test pins that admission so a
refactor of the manifest-keys set cannot silently regress it (the probe that
verified the fix was one-off; this makes it permanent).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"

FIXTURE_MANIFEST = {
    "name": "i232-pin",
    "version": "1.0.0",
    "description": "pin",
    "cpv": {
        "pipeline": {
            "intentional_divergence": ["scripts/publish.py"],
        }
    },
}


def test_top_level_cpv_block_emits_no_unknown_key_finding(tmp_path: Path) -> None:
    """A manifest whose ONLY unusual field is a top-level cpv pipeline block
    draws no unknown-manifest-key finding through the real validator."""
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(FIXTURE_MANIFEST), encoding="utf-8"
    )
    (plugin / "skills" / "dummy").mkdir(parents=True)
    (plugin / "skills" / "dummy" / "SKILL.md").write_text(
        "---\nname: dummy\ndescription: fill component set\n---\nbody\n",
        encoding="utf-8",
    )
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "remote_validation.py"),
        "plugin",
        str(plugin),
        "--strict",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
        env={
            "PATH": __import__("os").environ["PATH"],
            "PLUGIN_SKIP_GITHUB_INTEGRITY": "1",
            "CLAUDE_PRIVATE_USERNAMES": __import__("os").environ.get("USER", ""),
            "CPV_SCAN_CACHE": "0",
        },
    )
    output = result.stdout + result.stderr
    assert "Unknown manifest field" not in output, (
        f"the top-level cpv key drew an unknown-field finding — #232 regressed\n{output[:1500]}"
    )
