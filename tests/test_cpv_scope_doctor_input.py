#!/usr/bin/env python3
"""Unit tests for ``scripts/cpv_scope_doctor_input.py`` (TRDD-a175f78d §1-2).

The scope-doctor resolver enforces TWO contracts:

1. URL inputs are CRITICAL errors — the doctor needs filesystem
   access to ``~/.claude/`` and a URL cannot reach it.
2. ``--scope`` is one of ``user`` / ``project`` / ``local`` /
   ``full`` (default).

These tests pin the contract exactly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_scope_doctor_input import (  # noqa: E402
    URL_REJECTED_MESSAGE,
    InputResolutionError,
    parse_scope_flag,
    resolve_scope_inputs,
)

# ----------------------- parse_scope_flag --------------------------------


class TestParseScopeFlag:
    def test_none_returns_full(self) -> None:
        assert parse_scope_flag(None) == "full"

    def test_full(self) -> None:
        assert parse_scope_flag("full") == "full"

    def test_user(self) -> None:
        assert parse_scope_flag("user") == "user"

    def test_project(self) -> None:
        assert parse_scope_flag("project") == "project"

    def test_local(self) -> None:
        assert parse_scope_flag("local") == "local"

    def test_uppercase_accepted_and_lowered(self) -> None:
        assert parse_scope_flag("FULL") == "full"

    def test_whitespace_stripped(self) -> None:
        assert parse_scope_flag("  user  ") == "user"

    def test_invalid_raises(self) -> None:
        with pytest.raises(InputResolutionError, match="Accepted: full, local, project, user"):
            parse_scope_flag("invalid")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(InputResolutionError, match="Accepted"):
            parse_scope_flag("")


# ----------------------- resolve_scope_inputs ----------------------------


def _make_plugin(root: Path, name: str = "demo-plugin") -> Path:
    plugin_dir = root / name
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0"}), encoding="utf-8"
    )
    return plugin_dir


class TestResolveScopeInputs:
    def test_single_local_path_resolves(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        result = resolve_scope_inputs(str(plugin))
        assert len(result) == 1
        assert result[0].abs_path == plugin.resolve()
        assert result[0].kind == "plugin"

    def test_url_string_raises_with_canonical_message(self) -> None:
        with pytest.raises(InputResolutionError) as ex:
            resolve_scope_inputs("https://github.com/owner/plugin")
        assert "cpv-batch-scope-* skills require LOCAL project paths" in str(ex.value)
        assert URL_REJECTED_MESSAGE.split("\n")[0] in str(ex.value)

    def test_owner_repo_shorthand_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Make sure the shorthand doesn't collide with a local path.
        monkeypatch.chdir(tmp_path)
        with pytest.raises(InputResolutionError):
            resolve_scope_inputs("Emasoft/emasoft-plugins")

    def test_list_containing_url_raises_immediately(self, tmp_path: Path) -> None:
        plug = _make_plugin(tmp_path)
        with pytest.raises(InputResolutionError):
            resolve_scope_inputs([str(plug), "https://github.com/owner/repo"])

    def test_default_to_pwd_when_input_is_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_plugin(tmp_path)
        monkeypatch.chdir(tmp_path / "demo-plugin")
        result = resolve_scope_inputs(None)
        assert len(result) == 1
        assert result[0].abs_path == (tmp_path / "demo-plugin").resolve()

    def test_default_to_pwd_disabled_raises(self) -> None:
        with pytest.raises(InputResolutionError, match="no input given"):
            resolve_scope_inputs(None, default_to_pwd=False)

    def test_empty_string_default_to_pwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_plugin(tmp_path)
        monkeypatch.chdir(tmp_path / "demo-plugin")
        result = resolve_scope_inputs("")
        assert len(result) == 1

    def test_multiple_local_paths_resolve(self, tmp_path: Path) -> None:
        p_a = _make_plugin(tmp_path, "plug-a")
        p_b = _make_plugin(tmp_path, "plug-b")
        result = resolve_scope_inputs([str(p_a), str(p_b)])
        assert {r.abs_path for r in result} == {p_a.resolve(), p_b.resolve()}


# ----------------------- URL_REJECTED_MESSAGE constant -------------------


class TestUrlRejectedMessage:
    def test_message_mentions_local_requirement(self) -> None:
        assert "LOCAL project paths" in URL_REJECTED_MESSAGE

    def test_message_mentions_claude_install(self) -> None:
        assert "~/.claude/" in URL_REJECTED_MESSAGE

    def test_message_points_to_alternatives(self) -> None:
        assert "cpv-batch-validate" in URL_REJECTED_MESSAGE
