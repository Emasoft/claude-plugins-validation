"""Tests for GitHub issues #286 and #272 — generator canon: notify-marketplace checkout and ruff vs the shell hook."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    gen_notify_marketplace_yml,
    gen_pre_push_hook,
    gen_ruff_toml,
)

CHECKOUT_PIN = "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10"


def _params(**overrides: object) -> PluginParams:
    """A PluginParams with sensible defaults, accepting field overrides."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


def _assert_checkout_first(workflow_text: str) -> None:
    """Assert the notify job's first step is the pinned checkout, before Get plugin info."""
    steps = yaml.safe_load(workflow_text)["jobs"]["notify"]["steps"]
    first = steps[0]
    assert str(first.get("uses", "")).startswith(CHECKOUT_PIN)
    assert first["with"]["persist-credentials"] is False
    names = [s.get("name") for s in steps]
    assert names.index("Get plugin info") > 0


def test_notify_workflow_checks_out_before_reading_plugin_json() -> None:
    """The notify job checks out the repo before it reads plugin.json (#286)."""
    _assert_checkout_first(gen_notify_marketplace_yml(_params()))


def test_ruff_toml_does_not_include_shell_hook() -> None:
    """ruff is not pointed at the shell pre-push hook (#272)."""
    assert "extend-include" not in tomllib.loads(gen_ruff_toml(_params()))
    first_line = gen_pre_push_hook(_params()).splitlines()[0]
    assert first_line.startswith("#!")
    assert "python" not in first_line
