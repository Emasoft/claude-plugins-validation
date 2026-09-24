#!/usr/bin/env python3
"""CC v2.1.258–2.1.281 spec sync, wave-3 follow-ups (two-sided).

- Hooks declared INLINE in plugin.json now run through validate_hook's checks.
- The Git LFS warning is one aggregated finding.
- Traversal edge cases (`plugins/..`, `...`) and a minimal valid options block.
- The marketplace scaffolder and standardizer refuse every spec-reserved name.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import pytest  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from generate_marketplace_repo import validate_name  # noqa: E402
from standardize_marketplace import validate_marketplace_json  # noqa: E402
from validate_marketplace import validate_local_path, validate_plugin_source  # noqa: E402
from validate_plugin import (  # noqa: E402
    check_lfs_shipped_files,
    validate_manifest,
    validate_user_config_structure,
)

PERMREQ_NEEDLE = "does not support 'agent' hooks"
UNQUOTED_NEEDLE = "unquoted"


def _plugin(tmp_path: Path, manifest: dict[str, Any]) -> Path:
    (tmp_path / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    base = {"name": "demo", "version": "1.0.0", "description": "d"}
    base.update(manifest)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps(base), encoding="utf-8")
    return tmp_path


def _levels(report: ValidationReport, needle: str) -> list[str]:
    return [r.level for r in report.results if needle in r.message]


# ---------------------------------------------------------------- inline plugin.json hooks


def test_inline_permission_request_agent_hook_is_major(tmp_path: Path) -> None:
    hooks = {"PermissionRequest": [{"hooks": [{"type": "agent", "prompt": "decide"}]}]}
    report = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"hooks": hooks}), report)
    assert "MAJOR" in _levels(report, PERMREQ_NEEDLE)


def test_inline_unquoted_plugin_root_is_warning(tmp_path: Path) -> None:
    hooks = {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "sh ${CLAUDE_PLUGIN_ROOT}/x.sh"}]}
        ]
    }
    report = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"hooks": hooks}), report)
    assert "WARNING" in _levels(report, UNQUOTED_NEEDLE)


def test_inline_wrapped_document_shape_is_also_checked(tmp_path: Path) -> None:
    """The {"hooks": {...}} hooks.json shape inline must be checked too."""
    hooks = {"hooks": {"PermissionRequest": [{"hooks": [{"type": "agent", "prompt": "decide"}]}]}}
    report = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"hooks": hooks}), report)
    assert "MAJOR" in _levels(report, PERMREQ_NEEDLE)


def test_clean_inline_hooks_draw_neither(tmp_path: Path) -> None:
    """Control: a quoted command hook and a prompt hook on PermissionRequest."""
    hooks = {
        "PermissionRequest": [{"hooks": [{"type": "prompt", "prompt": "decide"}]}],
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": 'sh "${CLAUDE_PLUGIN_ROOT}/x.sh"'}],
            }
        ],
    }
    report = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"hooks": hooks}), report)
    assert _levels(report, PERMREQ_NEEDLE) == []
    assert _levels(report, UNQUOTED_NEEDLE) == []


def test_hooks_path_string_is_not_treated_as_inline(tmp_path: Path) -> None:
    report = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"hooks": "./hooks/extra.json"}), report)
    assert not [r for r in report.results if r.message.startswith("(inline hooks)")]


# ---------------------------------------------------------------- LFS aggregation


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_three_lfs_files_give_exactly_one_warning(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
    for i in range(3):
        (tmp_path / f"f{i}.bin").write_bytes(b"x")
    _git(tmp_path, "add", ".gitattributes", "f0.bin", "f1.bin", "f2.bin")
    report = ValidationReport()
    check_lfs_shipped_files(tmp_path, report)
    lfs = [r for r in report.results if "LFS" in r.message]
    assert len(lfs) == 1 and lfs[0].level == "WARNING"
    assert "3 shipped file(s)" in lfs[0].message and "runtime" in lfs[0].message


def test_no_lfs_files_no_warning(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    report = ValidationReport()
    check_lfs_shipped_files(tmp_path, report)
    assert report.results == []


# ---------------------------------------------------------------- traversal edge cases


def test_trailing_dotdot_segment_stays_critical(tmp_path: Path) -> None:
    results = validate_plugin_source({"source": "./plugins/.."}, "p", tmp_path, "m.json")
    results += validate_local_path("./plugins/..", "p", tmp_path, "m.json")
    assert "CRITICAL" in [r.level for r in results if "traversal" in r.message]


def test_triple_dot_name_is_not_traversal(tmp_path: Path) -> None:
    (tmp_path / "plugins" / "..." / ".claude-plugin").mkdir(parents=True)
    results = validate_plugin_source({"source": "./plugins/..."}, "p", tmp_path, "m.json")
    results += validate_local_path("./plugins/...", "p", tmp_path, "m.json")
    assert not [r for r in results if "traversal" in r.message]


# ---------------------------------------------------------------- minimal valid options


def test_minimal_valid_options_block_is_clean() -> None:
    field = {"type": "string", "title": "T", "description": "D", "options": ["a", "b"], "default": "a"}
    report = ValidationReport()
    validate_user_config_structure({"userConfig": {"k": field}}, report)
    assert not [r for r in report.results if r.level in ("MAJOR", "MINOR")]


# ---------------------------------------------------------------- reserved-name drift


@pytest.mark.parametrize("name", ["npm", "gh", "claude-tag-plugins", "claude-code-plugins"])
def test_generator_rejects_spec_reserved_names(name: str) -> None:
    assert validate_name(name) is not None


def test_generator_accepts_ordinary_name() -> None:
    assert validate_name("my-tools") is None


@pytest.mark.parametrize("name", ["npm", "claude-tag-plugins"])
def test_standardize_rejects_spec_reserved_names(tmp_path: Path, name: str) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    doc = {"name": name, "owner": {"name": "o"}, "plugins": []}
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(json.dumps(doc), encoding="utf-8")
    _, findings = validate_marketplace_json(tmp_path)
    assert any("reserved" in f.message for f in findings)


def test_standardize_accepts_ordinary_name(tmp_path: Path) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    doc = {"name": "my-tools", "owner": {"name": "o"}, "plugins": []}
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(json.dumps(doc), encoding="utf-8")
    _, findings = validate_marketplace_json(tmp_path)
    assert not any("reserved" in f.message for f in findings)
