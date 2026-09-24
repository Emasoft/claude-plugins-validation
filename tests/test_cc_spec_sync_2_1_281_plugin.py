#!/usr/bin/env python3
"""CC v2.1.258–2.1.281 spec sync — plugin.json + marketplace.json (two-sided).

Every "now accepted" assertion is paired with a control proving the same code
path still rejects the genuinely wrong input.
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
from validate_marketplace import (  # noqa: E402
    validate_local_path,
    validate_marketplace_name,
    validate_plugin_entry,
    validate_plugin_source,
    validate_renames_block,
)
from validate_plugin import (  # noqa: E402
    _validate_monitors_array,
    check_lfs_shipped_files,
    validate_manifest,
    validate_undeclared_user_config_refs,
    validate_user_config_structure,
)


def _levels(report: ValidationReport, needle: str) -> list[str]:
    return [r.level for r in report.results if needle in r.message]


def _uc(**field: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"type": "string", "title": "Tone", "description": "Voice"}
    base.update(field)
    return {"userConfig": {"tone": base}}


# ---------------------------------------------------------------- userConfig.options


def test_options_valid_field_has_no_blocking_finding() -> None:
    report = ValidationReport()
    validate_user_config_structure(_uc(options=["neutral", "warm"], default="neutral"), report)
    assert not [r for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT")]
    assert "WARNING" in _levels(report, "v2.1.271")  # older CLIs can't load it


@pytest.mark.parametrize(
    ("field", "needle"),
    [
        ({"type": "number", "options": ["a"], "default": "a"}, "requires type 'string'"),
        ({"options": ["a"], "default": "a", "multiple": True}, "multiple: true"),
        ({"options": ["a"], "default": "a", "sensitive": True}, "sensitive: true"),
        ({"options": ["a", "b"], "default": "c"}, "must be one of its options"),
        ({"options": ["a"]}, "requires required: true"),
        ({"options": [], "required": True}, "non-empty array"),
        ({"options": ["x" * 65], "required": True}, "1 to 64 characters"),
        ({"options": [" a"], "required": True}, "starts or ends with a space"),
        ({"options": ["a​b"], "required": True}, "invisible"),
        ({"options": ["a b"], "required": True}, "invisible"),
        ({"options": ["Warm", "warm"], "required": True}, "duplicates"),
    ],
)
def test_options_rule_violation_is_major(field: dict[str, Any], needle: str) -> None:
    report = ValidationReport()
    validate_user_config_structure(_uc(**field), report)
    assert "MAJOR" in _levels(report, needle)


def test_options_without_default_but_required_is_clean() -> None:
    report = ValidationReport()
    validate_user_config_structure(_uc(options=["a"], required=True), report)
    assert not [r for r in report.results if r.level == "MAJOR"]


def test_unknown_userconfig_subfield_still_minor() -> None:
    report = ValidationReport()
    validate_user_config_structure(_uc(optionz=["a"]), report)
    assert "MINOR" in _levels(report, "optionz")


# ---------------------------------------------------------------- manifest fields


def _plugin(tmp_path: Path, manifest: dict[str, Any]) -> Path:
    (tmp_path / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    base = {"name": "demo", "version": "1.0.0", "description": "d"}
    base.update(manifest)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps(base), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "extra",
    [
        {"metadata": {"sku": "x"}},
        {"workflows": "./custom/workflows/"},
        {"privacyPolicyUrl": "https://example.com/p"},
        {"supportUrl": "https://example.com/s"},
        {"experimental": {"evals": "quality/evals"}},
    ],
)
def test_new_manifest_fields_not_unknown(tmp_path: Path, extra: dict[str, Any]) -> None:
    report = ValidationReport()
    validate_manifest(_plugin(tmp_path, extra), report)
    assert not [r for r in report.results if "Unknown manifest field" in r.message or "Unknown 'experimental" in r.message]


def test_really_unknown_manifest_field_still_warns(tmp_path: Path) -> None:
    report = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"supportUrll": "x"}), report)
    assert "WARNING" in _levels(report, "Unknown manifest field 'supportUrll'")


def test_metadata_non_object_warns(tmp_path: Path) -> None:
    report = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"metadata": "x"}), report)
    assert "WARNING" in _levels(report, "'metadata' must be an object")


def test_workflows_path_rules(tmp_path: Path) -> None:
    report = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"workflows": "custom/workflows"}), report)
    assert "MAJOR" in _levels(report, "Field 'workflows' path must start with './'")


def test_evals_traversal_and_shape(tmp_path: Path) -> None:
    r1 = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"experimental": {"evals": "../outside"}}), r1)
    assert "MAJOR" in _levels(r1, "experimental.evals")
    r2 = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"experimental": {"evals": 5}}), r2)
    assert "MAJOR" in _levels(r2, "experimental.evals")
    r3 = ValidationReport()
    validate_manifest(_plugin(tmp_path, {"experimental": {"evalz": "x"}}), r3)
    assert "WARNING" in _levels(r3, "experimental.evalz")


# ---------------------------------------------------------------- undeclared ${user_config}


def test_undeclared_user_config_ref_in_mcp_warns(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"s": {"command": "x", "env": {"K": "${user_config.api_key}"}}}}),
        encoding="utf-8",
    )
    report = ValidationReport()
    validate_undeclared_user_config_refs({"userConfig": {"endpoint": {}}}, tmp_path, report)
    assert _levels(report, "'api_key'") == ["WARNING"]


def test_declared_user_config_ref_is_silent(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"s": {"command": "x", "env": {"K": "${user_config.api_key}"}}}}),
        encoding="utf-8",
    )
    report = ValidationReport()
    validate_undeclared_user_config_refs({"userConfig": {"api_key": {}}}, tmp_path, report)
    assert not report.results


def test_inline_lsp_and_channel_declared(tmp_path: Path) -> None:
    manifest = {
        "lspServers": {"py": {"command": "pyls", "env": {"A": "${user_config.missing}", "B": "${user_config.bot}"}}},
        "channels": [{"server": "s", "userConfig": {"bot": {}}}],
    }
    report = ValidationReport()
    validate_undeclared_user_config_refs(manifest, tmp_path, report)
    assert _levels(report, "'missing'") == ["WARNING"]
    assert not _levels(report, "'bot'")


def test_markdown_is_never_scanned(tmp_path: Path) -> None:
    (tmp_path / "skills" / "s").mkdir(parents=True)
    (tmp_path / "skills" / "s" / "SKILL.md").write_text("Use ${user_config.api_key} in configs.", encoding="utf-8")
    report = ValidationReport()
    validate_undeclared_user_config_refs({}, tmp_path, report)
    assert not report.results


# ---------------------------------------------------------------- monitors quoting


def _monitor(cmd: str) -> list[dict[str, Any]]:
    return [{"name": "m", "command": cmd, "description": "d"}]


def test_monitor_unquoted_plugin_root_warns() -> None:
    report = ValidationReport()
    _validate_monitors_array(_monitor("${CLAUDE_PLUGIN_ROOT}/poll.sh"), "plugin.json", report)
    assert "WARNING" in _levels(report, "unquoted")


@pytest.mark.parametrize("cmd", ['"${CLAUDE_PLUGIN_ROOT}/poll.sh"', "'${CLAUDE_PLUGIN_ROOT}/poll.sh'", "./poll.sh"])
def test_monitor_quoted_or_absent_root_is_silent(cmd: str) -> None:
    report = ValidationReport()
    _validate_monitors_array(_monitor(cmd), "plugin.json", report)
    assert not _levels(report, "unquoted")


# ---------------------------------------------------------------- Git LFS


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_lfs_tracked_file_warns(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
    (tmp_path / "model.bin").write_bytes(b"x")
    (tmp_path / "run.sh").write_text("echo", encoding="utf-8")
    _git(tmp_path, "add", "-f", ".gitattributes", "model.bin", "run.sh")
    report = ValidationReport()
    check_lfs_shipped_files(tmp_path, report)
    assert _levels(report, "model.bin") == ["WARNING"]
    assert not _levels(report, "run.sh")


def test_lfs_rule_without_tracked_match_is_silent(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitattributes").write_text("*.bin filter=lfs\n", encoding="utf-8")
    (tmp_path / "run.sh").write_text("echo", encoding="utf-8")
    _git(tmp_path, "add", ".gitattributes", "run.sh")
    report = ValidationReport()
    check_lfs_shipped_files(tmp_path, report)
    assert not report.results


def test_lfs_non_git_tree_is_silent(tmp_path: Path) -> None:
    (tmp_path / "model.bin").write_bytes(b"x")
    report = ValidationReport()
    check_lfs_shipped_files(tmp_path, report)
    assert not report.results


# ---------------------------------------------------------------- `..` segment traversal


def test_dotdot_prefixed_dir_name_is_not_traversal(tmp_path: Path) -> None:
    (tmp_path / "plugins" / "..tools" / ".claude-plugin").mkdir(parents=True)
    results = validate_plugin_source({"source": "./plugins/..tools"}, "p", tmp_path, "m.json")
    results += validate_local_path("./plugins/..tools", "p", tmp_path, "m.json")
    assert not [r for r in results if "traversal" in r.message]


@pytest.mark.parametrize("src", ["../x", "./a/../b", "./a\\..\\b"])
def test_real_traversal_still_critical(tmp_path: Path, src: str) -> None:
    results = validate_plugin_source({"source": src}, "p", tmp_path, "m.json")
    results += validate_local_path(src, "p", tmp_path, "m.json")
    assert "CRITICAL" in [r.level for r in results if "traversal" in r.message]


# ---------------------------------------------------------------- marketplace renames / headers


def test_renames_valid_shape() -> None:
    assert validate_renames_block({"formatter": "code-formatter", "legacy": None}, "m.json") == []


@pytest.mark.parametrize("renames", [["x"], {"old": 5}, {"old": ""}])
def test_renames_bad_shape_is_major(renames: Any) -> None:
    assert [r.level for r in validate_renames_block(renames, "m.json")] == ["MAJOR"]


def _entry_results(tmp_path: Path, **extra: Any) -> list[str]:
    (tmp_path / "plugins" / "p" / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    entry = {"name": "p", "source": "./plugins/p", "description": "d"}
    entry.update(extra)
    return [r.message for r in validate_plugin_entry(entry, 0, tmp_path, "m.json")]


def test_entry_headers_accepted(tmp_path: Path) -> None:
    msgs = _entry_results(tmp_path, headers={"Authorization": "Bearer x"})
    assert not [m for m in msgs if "headers" in m]


def test_entry_headers_bad_shape_and_unknown_field_still_flagged(tmp_path: Path) -> None:
    assert [m for m in _entry_results(tmp_path, headers={"X": 1}) if "'headers' must be" in m]
    assert [m for m in _entry_results(tmp_path, headerz={"X": "y"}) if "headerz" in m]


# ---------------------------------------------------------------- reserved names


@pytest.mark.parametrize("name", ["claude-tag-plugins", "npm", "pip", "uv", "cargo", "github", "gh"])
def test_reserved_names_critical(name: str) -> None:
    assert "CRITICAL" in [r.level for r in validate_marketplace_name(name, "m.json")]


def test_package_manager_names_any_casing() -> None:
    assert any("refused by Claude Code" in r.message for r in validate_marketplace_name("GitHub", "m.json"))


@pytest.mark.parametrize("name", ["npmx", "github-tools", "my-gh", "uvx-tools"])
def test_near_miss_names_not_reserved(name: str) -> None:
    assert not [r for r in validate_marketplace_name(name, "m.json") if r.level == "CRITICAL"]
