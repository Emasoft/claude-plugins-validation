"""Issue #237 / task G — plugin-declared cpv.* opt-out keys are INERT and warned once.

Owner policy: "in no case an user should be able to customize the validation
rules"; "exclude paths or url allowlist must also be not configurable by the user".

A full in-process ``validate_plugin.main()`` run over a git fixture must:
  * emit exactly ONE WARNING per inert key present (no duplicate from the older
    RC-DEPRECATED-OPTOUT list or from validate_no_absolute_paths), and none when
    the keys are absent;
  * produce the same findings with and without the keys (no effect), with the
    findings each key used to suppress still present.

The fixture carries a `.gitmodules` whose cross-owner URL the plugin allowlists
(`cpv.strip.allowed_submodule_urls`) or exempts (`require_url_allowlist=false`):
the STRIP-G013 CRITICAL must fire identically with and without those keys. Each
key is also checked ALONE (parametrized), against the same plugin without it.

The CPV self-integrity check is marked verified IN-PROCESS by monkeypatching the
module state (as the in-process suite does), never via the skip variable.
"""

from __future__ import annotations

import collections
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _plugin_verify_hashes as pvh  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402
import validate_plugin as vp  # noqa: E402

INERT_BLOCK = {
    "exclude_paths": ["hidden/"],
    "allow_root_dirs": ["weird"],
    "allow_orchestrator_traversal": "skills/x",
    "strip": {
        "allowed_submodule_urls": ["https://github.com/someone/*"],
        "require_url_allowlist": False,
    },
}
INERT_NAMES = (
    "exclude_paths",
    "allow_root_dirs",
    "allow_orchestrator_traversal",
    "strip.allowed_submodule_urls",
    "strip.require_url_allowlist",
)

_SKILL_MD = """\
---
name: x
description: A test skill that does a thing. Use when you need to test the thing in a controlled environment for plugin validation purposes.
---
# X skill

## Steps

1. Do the thing.
"""

_FILES = {
    "skills/x/SKILL.md": _SKILL_MD,
    "hidden/run.sh": "cp x /lib/secret\n",
    "weird/data.txt": "data\n",
    "LICENSE": "MIT License\n\nCopyright (c) 2026\n",
    "README.md": "# inert-keys\n\nTest plugin.\n",
    ".gitignore": "__pycache__/\n*.pyc\n.venv/\n",
    ".gitmodules": '[submodule "x"]\n\tpath = libs/x\n\turl = https://github.com/someone/x.git\n',
}


def _build(root: Path, cpv_block: dict | None) -> Path:
    manifest: dict = {"name": "inert-keys", "version": "1.0.0", "description": "Inert cpv keys fixture."}
    if cpv_block is not None:
        manifest["cpv"] = cpv_block
    files = dict(_FILES)
    files[".claude-plugin/plugin.json"] = json.dumps(manifest, indent=2) + "\n"
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "--", *files], cwd=root, check=True, capture_output=True)
    return root


def _run(root: Path, monkeypatch, capsys) -> list[dict]:
    monkeypatch.setattr(pvh, "_VERIFIED_THIS_PROCESS", True)
    monkeypatch.setenv("CPV_ORCHESTRATOR_PARALLEL", "0")
    monkeypatch.setattr(sys, "argv", ["validate_plugin.py", str(root), "--json"])
    cvc._load_cpv_config_cached.cache_clear()
    vp.main()
    return json.loads(capsys.readouterr().out)["results"]


def _key_warning(r: dict, name: str) -> bool:
    return r["level"] == "WARNING" and f"cpv.{name} is ignored" in r["message"]


def _is_any_key_warning(r: dict) -> bool:
    return any(_key_warning(r, n) for n in INERT_NAMES)


def _findings(results: list[dict], root: Path) -> collections.Counter:
    return collections.Counter(
        (r["level"], r["message"].replace(str(root), "<ROOT>"), r.get("file"))
        for r in results
        if r["level"] not in ("PASSED", "INFO") and not _is_any_key_warning(r)
    )


def test_each_inert_key_warned_exactly_once_and_has_no_effect(tmp_path, monkeypatch, capsys) -> None:
    with_keys = _build(tmp_path / "with" / "inert-keys", INERT_BLOCK)
    without = _build(tmp_path / "without" / "inert-keys", None)
    res_with = _run(with_keys, monkeypatch, capsys)
    res_without = _run(without, monkeypatch, capsys)

    for name in INERT_NAMES:
        assert sum(1 for r in res_with if _key_warning(r, name)) == 1, name
        assert not any(_key_warning(r, name) for r in res_without), name
    # No second warning for the same key from the older opt-out list.
    for name in ("allow_root_dirs", "allow_orchestrator_traversal"):
        assert not any("RC-DEPRECATED-OPTOUT" in r["message"] and name in r["message"] for r in res_with)

    # No effect: identical findings, and what the keys used to suppress still fires.
    assert _findings(res_with, with_keys) == _findings(res_without, without)
    assert any("Absolute path found" in r["message"] and r.get("file") == "hidden/run.sh" for r in res_with)
    assert any(r["level"] in ("MAJOR", "MINOR") and "weird" in r["message"] for r in res_with)


_SINGLE_KEY_BLOCKS = {
    "exclude_paths": {"exclude_paths": ["hidden/"]},
    "allow_root_dirs": {"allow_root_dirs": ["weird"]},
    "allow_orchestrator_traversal": {"allow_orchestrator_traversal": "skills/x"},
    "strip.allowed_submodule_urls": {"strip": {"allowed_submodule_urls": ["https://github.com/someone/*"]}},
    "strip.require_url_allowlist": {"strip": {"require_url_allowlist": False}},
}


@pytest.mark.parametrize("name", INERT_NAMES)
def test_single_inert_key_has_no_effect(tmp_path, monkeypatch, capsys, name: str) -> None:
    """Each key ALONE: warned exactly once, and every finding equals the keyless run."""
    with_key = _build(tmp_path / "with" / "inert-keys", _SINGLE_KEY_BLOCKS[name])
    without = _build(tmp_path / "without" / "inert-keys", None)
    res_with = _run(with_key, monkeypatch, capsys)
    res_without = _run(without, monkeypatch, capsys)
    assert sum(1 for r in res_with if _key_warning(r, name)) == 1
    assert sum(1 for r in res_with if _is_any_key_warning(r)) == 1
    assert _findings(res_with, with_key) == _findings(res_without, without)
    assert any("STRIP-G013" in r["message"] and r["level"] == "CRITICAL" for r in res_with)


def test_warn_inert_cpv_keys_ignores_lookalikes(tmp_path) -> None:
    """Only the exact CPV-defined key paths warn; a top-level `allowed_submodule_urls` does not."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "cpv": {"allowed_submodule_urls": [], "strip": {"extract": []}}}),
        encoding="utf-8",
    )
    cvc._load_cpv_config_cached.cache_clear()
    report = cvc.ValidationReport()
    cvc.warn_inert_cpv_keys(tmp_path, report)
    assert [r for r in report.results if r.level == "WARNING"] == []
