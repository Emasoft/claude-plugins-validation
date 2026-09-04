"""CC spec-drift sync v2.1.258-260: CLAUDE_CODE_SUBAGENT_MODEL(_FORCE) env vars + Fable 5.1 full model ID."""

from scripts.cpv_validation_common import VALID_PLUGIN_ENV_VARS, is_valid_model


def test_claude_code_subagent_model_is_known():
    """CLAUDE_CODE_SUBAGENT_MODEL (the default subagent/teammate model) is a recognized plugin env var."""
    assert "CLAUDE_CODE_SUBAGENT_MODEL" in VALID_PLUGIN_ENV_VARS


def test_claude_code_subagent_model_force_is_known():
    """CLAUDE_CODE_SUBAGENT_MODEL_FORCE (CC v2.1.257, forces one model onto every subagent) is a recognized plugin env var."""
    assert "CLAUDE_CODE_SUBAGENT_MODEL_FORCE" in VALID_PLUGIN_ENV_VARS


def test_unknown_sibling_env_var_still_unrecognized():
    """A plausible-looking but non-existent sibling env var is NOT recognized (positive control)."""
    assert "CLAUDE_CODE_SUBAGENT_MODEL_OVERRIDE" not in VALID_PLUGIN_ENV_VARS


def test_fable_5_1_full_model_id_is_valid():
    """claude-fable-5-1 (Fable 5.1's full model ID, CC v2.1.257) is accepted by is_valid_model."""
    assert is_valid_model("claude-fable-5-1") is True


def test_fable_5_1_with_1m_suffix_is_valid():
    """claude-fable-5-1[1m] (Fable 5.1 with the 1M context window) is accepted by is_valid_model."""
    assert is_valid_model("claude-fable-5-1[1m]") is True


def test_hallucinated_family_still_rejected():
    """A hallucinated model family (e.g. claude-gpt-5-1) is still rejected (positive control)."""
    assert is_valid_model("claude-gpt-5-1") is False
