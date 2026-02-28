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
