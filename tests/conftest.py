"""Shared pytest fixtures for validation tests."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))


# ----------------------------------------------------------------------------
# TRDD-fa70f9b8 — suite-pollution defence.
#
# Two tests historically failed only when run via the full `pytest tests/`
# directory glob (NOT in isolation, NOT with explicit-file lists):
#   - tests/test_validate_security.py::TestMainCLI::test_main_verbose_text_output
#   - tests/test_phase4_minor_observability.py::TestCheckPhase4All::test_phase4_fires_on_real_file
#
# The TRDD identified the suspected polluters as module-level globals on
# `validate_security` (`_CPV_SELF_SCAN_*`, `_CLASSIFIER_*`) plus the two
# `functools.lru_cache`-wrapped helpers in `cpv_validation_common`
# (`_read_gitmodules_paths`, `_load_cpv_config_cached`). Any earlier test
# that called `validate_security()` against the real CPV plugin (or directly
# poked `_set_cpv_self_scan(True, ...)` / `_set_classifier_active(True, ...)`)
# could leak that state into later tests that bypass the orchestrator and
# call the lower-level phase checkers directly.
#
# This autouse fixture defensively resets every suspected polluter BEFORE
# each test. The cost is microseconds; the upside is deterministic per-test
# isolation that closes the Heisenbug regardless of pytest collection order
# or worker-id assignment under `-n auto --dist=worksteal`.
#
# DO NOT remove without a corresponding rewrite of the production globals
# into context-managed state (see TRDD-fa70f9b8 §"Why we can't easily fix it").
# ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _trdd_fa70f9b8_reset_global_state():
    """Reset suspected suite-pollution globals BEFORE every test.

    Hardens the suite against TRDD-fa70f9b8: previous tests that activate
    self-scan / classifier or fill the cpv-config / gitmodules lru_caches
    used to leak state into the next test, producing a Heisenbug whose
    symptom (test passes alone, fails in directory mode) defied bisection.

    The reset is idempotent and side-effect-free when the globals are
    already at their default values.
    """
    # Only attempt the reset when the validator modules are importable in
    # this test process. They WILL be importable in every test under
    # tests/ because conftest.py adds scripts/ to sys.path above, but the
    # try/except keeps the fixture safe if the test layout ever changes.
    try:
        import validate_security as _vs

        _vs._set_cpv_self_scan(False, plugin_root=None, notice_report=None)
        _vs._set_classifier_active(False)
    except ImportError:
        pass

    try:
        from cpv_validation_common import (
            _load_cpv_config_cached,
            _read_gitmodules_paths,
        )

        _read_gitmodules_paths.cache_clear()
        _load_cpv_config_cached.cache_clear()
    except ImportError:
        pass

    # TRDD-a0ab2363: validate_plugin._gi is a module-global GitignoreFilter set
    # fresh per run inside main() (production-correct, process exits after). A
    # test that runs validate_plugin.main() in-process leaves _gi pointing at
    # THAT plugin_root; a later test calling validate_cross_platform()/the
    # binary checks directly then rglobs the stale (deleted) root and finds no
    # bin/ files, silently dropping its expected findings. Only bites under
    # serial collection (CI) — parallel xdist masks it. Reset to the default.
    try:
        import validate_plugin as _vp

        _vp._gi = None
    except ImportError:
        pass

    yield

    # Post-test reset — guards against tests that activate state and rely
    # on a "clean exit" but forget to reset (or raise mid-test).
    try:
        import validate_security as _vs

        _vs._set_cpv_self_scan(False, plugin_root=None, notice_report=None)
        _vs._set_classifier_active(False)
    except ImportError:
        pass

    try:
        from cpv_validation_common import (
            _load_cpv_config_cached,
            _read_gitmodules_paths,
        )

        _read_gitmodules_paths.cache_clear()
        _load_cpv_config_cached.cache_clear()
    except ImportError:
        pass

    try:
        import validate_plugin as _vp

        _vp._gi = None
    except ImportError:
        pass


# ----------------------------------------------------------------------------
# TRDD-21ES7XEX defect B — repo-wide guard against a real tirith host install.
#
# scripts/validate_security.py::_resolve_tirith_runner falls back to a real
# brew/npm/cargo install of the `tirith` scanner whenever CPV_NO_TIRITH_INSTALL
# is unset. tests/test_tirith_integration.py has its own local autouse fixture
# for that file, but `check_tirith_scanner`/`_resolve_tirith_runner` are also
# reachable indirectly through `validate_security()` (the top-level security
# orchestrator many other test files call directly, e.g.
# tests/test_validate_security.py) and through subprocess invocations of
# validate_plugin.py / remote_validation.py, none of which set the env var.
# A test suite must never mutate the developer's machine, so the guard is set
# HERE, repo-wide, rather than re-added to every one of those call sites —
# this is the narrowest scope that still covers every caller. Setting the
# env var is idempotent with the per-file fixture in test_tirith_integration.py
# (monkeypatch.setenv to the same value twice is a no-op), and any test that
# genuinely needs the opposite can still monkeypatch.delenv it locally within
# its own test body (fixtures run before the test body, so a later delenv in
# the test always wins for that test).
# ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _trdd_21es7xex_no_real_tirith_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force CPV_NO_TIRITH_INSTALL=1 for every test in the suite.

    See the module comment above for why this must be repo-wide rather than
    scoped to tests/test_tirith_integration.py alone.
    """
    monkeypatch.setenv("CPV_NO_TIRITH_INSTALL", "1")


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files.

    Yields a Path object to the temp directory.
    Directory is cleaned up after the test completes.
    """
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


@pytest.fixture
def valid_plugin_json():
    """Return a valid plugin.json structure.

    Contains all required fields for a minimal valid plugin manifest.
    """
    return {
        "name": "test-plugin",
        "version": "1.0.0",
        "description": "Test plugin for validation",
        "author": {"name": "Test Author", "email": "test@example.com"},
    }


@pytest.fixture
def valid_plugin_dir(temp_dir, valid_plugin_json):
    """Create a minimal valid plugin directory structure.

    Creates:
    - .claude-plugin/plugin.json with valid manifest
    - README.md with basic content

    Returns the path to the plugin directory.
    """
    plugin_dir = temp_dir / "test-plugin"
    plugin_dir.mkdir()

    # Create .claude-plugin directory with plugin.json
    claude_plugin = plugin_dir / ".claude-plugin"
    claude_plugin.mkdir()
    (claude_plugin / "plugin.json").write_text(json.dumps(valid_plugin_json, indent=2))

    # Create README.md
    (plugin_dir / "README.md").write_text(
        "# Test Plugin\n\nA test plugin for validation.\n\n"
        "## Installation\n\nRun `claude plugin install test-plugin`\n\n"
        "## Usage\n\nJust use it.\n"
    )

    return plugin_dir


@pytest.fixture
def valid_agent_frontmatter():
    """Return valid agent YAML frontmatter content."""
    return """---
name: test-agent
description: A test agent for validation
model: sonnet
tools:
  - Read
  - Write
  - Bash
---

# Test Agent

This is a test agent for validation purposes.

## Instructions

Follow the test instructions.
"""


@pytest.fixture
def valid_skill_frontmatter():
    """Return valid skill YAML frontmatter content."""
    return """---
name: test-skill
description: A test skill for validation
triggers:
  - when user asks about testing
  - when user mentions validation
---

# Test Skill

This skill teaches how to test things.

## When to use

Use this skill when you need to test validation.
"""


@pytest.fixture
def invalid_plugin_json():
    """Return an invalid plugin.json structure (missing required fields)."""
    return {
        "version": "1.0.0",
        # Missing: name (required)
        # Missing: description (required)
    }


@pytest.fixture
def fixtures_dir():
    """Return path to the fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def valid_fixture_plugin(fixtures_dir):
    """Return path to the valid_plugin fixture."""
    return fixtures_dir / "valid_plugin"


@pytest.fixture
def invalid_fixture_plugin(fixtures_dir):
    """Return path to the invalid_plugin fixture."""
    return fixtures_dir / "invalid_plugin"
