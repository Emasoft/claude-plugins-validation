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
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import validate_plugin  # noqa: E402

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


def test_known_fields_admits_the_cpv_key() -> None:
    """STRUCTURAL pin (review round 9): the manifest validator's known_fields
    set itself contains "cpv" — wording-proof and instant, unlike a grep on
    human-facing message strings that any rewording silently voids."""
    # known_fields is a function-local; re-derive it by parsing the function
    # source, which keeps the pin honest without reaching into call state.
    src = (SCRIPTS_DIR / "validate_plugin.py").read_text(encoding="utf-8")
    fn_start = src.index("def validate_manifest(")
    # The set literal sits after the docstring inside validate_manifest; the
    # admission is a quoted "cpv" element of it — pinned at source level so a
    # refactor that renames or moves the set still must carry the key.
    set_start = src.index("known_fields = {", fn_start)
    set_src = src[set_start : src.index("}", set_start)]
    assert '"cpv"' in set_src, (
        "the known_fields set inside validate_manifest no longer admits 'cpv' — #232 regressed"
    )
    assert callable(validate_plugin.validate_manifest)


def test_genuinely_unknown_key_still_draws_the_finding(tmp_path: Path) -> None:
    """POSITIVE CONTROL (review round 9): the absence assertion above proves
    nothing if the unknown-key check itself is disabled for this invocation
    shape — a bogus key must still fire, closing the check-disabled hole."""
    bogus = dict(FIXTURE_MANIFEST)
    bogus["bogusKey123"] = True
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps(bogus), encoding="utf-8")
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
    # Augment the env (never replace it) so the child inherits HOME/uv state —
    # a replaced env can make the child die before the validator runs, which
    # the absence assertion would read as clean.
    env = {**os.environ, "PLUGIN_SKIP_GITHUB_INTEGRITY": "1", "CPV_SCAN_CACHE": "0"}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    output = result.stdout + result.stderr
    assert "Unknown manifest field" in output and "bogusKey123" in output, (
        "a genuinely unknown manifest key did NOT draw the unknown-field finding — "
        f"the unknown-key check is disabled or its wording drifted; the absence "
        f"assertion in the sibling test is vacuous\n{output[:1500]}"
    )


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
    env = {**os.environ, "PLUGIN_SKIP_GITHUB_INTEGRITY": "1", "CPV_SCAN_CACHE": "0"}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    output = result.stdout + result.stderr
    assert "Unknown manifest field" not in output, (
        f"the top-level cpv key drew an unknown-field finding — #232 regressed\n{output[:1500]}"
    )
