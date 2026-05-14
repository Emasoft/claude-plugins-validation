#!/usr/bin/env python3
"""Tests for v2.1.141 env-var allowlist additions.

Per the v2.1.141 changelog:
* ``CLAUDE_CODE_PLUGIN_PREFER_HTTPS`` — clone GitHub plugin sources over HTTPS
  instead of SSH, for environments without a GitHub SSH key
* ``ANTHROPIC_WORKSPACE_ID`` — workload identity federation; scopes the minted
  token to a specific workspace when the federation rule covers more than one

Both must be recognized by ``is_valid_plugin_env_var`` so plugin READMEs,
``env`` blocks in settings.json, and hook substitutions do not flag them
as "unknown env var".
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import VALID_PLUGIN_ENV_VARS, is_valid_plugin_env_var  # noqa: E402


def test_CLAUDE_CODE_PLUGIN_PREFER_HTTPS_in_allowlist():
    """CLAUDE_CODE_PLUGIN_PREFER_HTTPS is recognized by the env-var allowlist."""
    assert "CLAUDE_CODE_PLUGIN_PREFER_HTTPS" in VALID_PLUGIN_ENV_VARS
    assert is_valid_plugin_env_var("CLAUDE_CODE_PLUGIN_PREFER_HTTPS")


def test_ANTHROPIC_WORKSPACE_ID_in_allowlist():
    """ANTHROPIC_WORKSPACE_ID is recognized by the env-var allowlist."""
    assert "ANTHROPIC_WORKSPACE_ID" in VALID_PLUGIN_ENV_VARS
    assert is_valid_plugin_env_var("ANTHROPIC_WORKSPACE_ID")


def test_other_v2_1_141_neighbors_still_in_allowlist():
    """Sanity check: pre-existing neighboring entries were not accidentally dropped."""
    # These bracketed my insertions in cpv_validation_common.py; if any are
    # missing the bracket-edit corrupted the set literal.
    assert "CLAUDE_CODE_ENABLE_FEEDBACK_SURVEY_FOR_OTEL" in VALID_PLUGIN_ENV_VARS
    assert "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES" in VALID_PLUGIN_ENV_VARS


def test_unrelated_env_var_still_unrecognized():
    """Sanity check: the allowlist did not become permissive — a random var still fails."""
    assert not is_valid_plugin_env_var("BOGUS_VAR_THAT_DOES_NOT_EXIST_12345")
