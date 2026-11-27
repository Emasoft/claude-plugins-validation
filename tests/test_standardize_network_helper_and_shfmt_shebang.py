"""Tests for the network-helper standard file and the shfmt shebang file selection."""

from __future__ import annotations

import json
import os
from pathlib import Path

from cpv_ci_preflight import _shfmt_shell_paths
from standardize_plugin import audit_standard_files, fix_missing_files

HELPER_REL = "scripts/cpv_network_resilience.py"


def test_audit_reports_network_helper_missing_when_publish_present(tmp_path: Path) -> None:
    """publish.py without its retry helper makes the standard-files audit report the helper MISSING."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "publish.py").write_text("# publish\n", encoding="utf-8")

    helper = [item for item in audit_standard_files(tmp_path) if item.name == HELPER_REL]

    assert len(helper) == 1
    assert helper[0].status == "MISSING"


def test_fix_creates_network_helper_when_missing(tmp_path: Path) -> None:
    """--fix writes scripts/cpv_network_resilience.py when the audit reports it MISSING."""
    (tmp_path / ".claude-plugin").mkdir()
    manifest = {"name": "demo-plugin", "version": "0.1.0", "description": "Demo plugin"}
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "publish.py").write_text("# publish\n", encoding="utf-8")
    missing = [item for item in audit_standard_files(tmp_path) if item.name == HELPER_REL]

    created = fix_missing_files(tmp_path, missing)

    assert (tmp_path / HELPER_REL).is_file()
    assert str(tmp_path / HELPER_REL) in created


def test_shfmt_selection_includes_extensionless_sh_shebang(tmp_path: Path) -> None:
    """An extensionless file whose first line is #!/bin/sh is selected for shfmt."""
    (tmp_path / "git-hooks").mkdir()
    (tmp_path / "git-hooks" / "pre-push").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

    assert "git-hooks/pre-push" in _shfmt_shell_paths(tmp_path)


def test_shfmt_selection_includes_env_bash_shebang(tmp_path: Path) -> None:
    """An extensionless file whose shebang is #!/usr/bin/env bash is selected for shfmt."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "tool").write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")

    assert "bin/tool" in _shfmt_shell_paths(tmp_path)


def test_shfmt_selection_excludes_extensionless_non_shell_files(tmp_path: Path) -> None:
    """An extensionless python script and a plain text file are not selected for shfmt."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "helper").write_text("#!/usr/bin/env python3\nprint(1)\n", encoding="utf-8")
    (tmp_path / "NOTES").write_text("plain text\n", encoding="utf-8")
    selected = _shfmt_shell_paths(tmp_path)

    assert "bin/helper" not in selected
    assert "NOTES" not in selected


def test_remote_validation_plugin_does_not_get_network_helper(tmp_path: Path) -> None:
    """A remote-validation plugin is de-vendored: the audit omits the helper and --fix does not create it."""
    (tmp_path / ".claude-plugin").mkdir()
    manifest = {
        "name": "demo-plugin",
        "version": "0.1.0",
        "description": "Demo plugin",
        "cpv": {"pipeline_profile": "remote-validation"},
    }
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "publish.py").write_text("# publish\n", encoding="utf-8")

    audit = audit_standard_files(tmp_path)
    fix_missing_files(tmp_path, [item for item in audit if item.status == "MISSING"])

    assert all(item.name != HELPER_REL for item in audit)
    assert not (tmp_path / HELPER_REL).exists()


def test_shfmt_selection_excludes_zsh_shebang(tmp_path: Path) -> None:
    """An extensionless zsh script is not selected: the gate mirrors the bash-family BASH_SHFMT linter."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "ztool").write_text("#!/usr/bin/env zsh\nprint -r -- ok\n", encoding="utf-8")

    assert "bin/ztool" not in _shfmt_shell_paths(tmp_path)


def test_shfmt_selection_skips_fifo_without_blocking(tmp_path: Path) -> None:
    """A named pipe with no extension is skipped instead of opened (opening it would block the gate)."""
    os.mkfifo(tmp_path / "pipe")
    (tmp_path / "hook").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")

    selected = _shfmt_shell_paths(tmp_path)

    assert "pipe" not in selected
    assert "hook" in selected
