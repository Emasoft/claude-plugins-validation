#!/usr/bin/env python3
"""
CC spec-drift sync (Claude Code v2.1.217-218) — two-sided regression locks.

Allowlist-widening / FP-reduction adds carrying the spec forward from the
v2.1.216 sweep to v2.1.218. Every new value is verified two-sided: the
newly-accepted value is now accepted (or no longer flagged), AND a positive
control proves the same code path still rejects a bogus sibling.

S1 — emojiCompletionEnabled setting        (v2.1.217) KNOWN_SETTINGS_KEYS
S2 — subagent-concurrency env vars         (v2.1.217) VALID_PLUGIN_ENV_VARS
S3 — `background` skill frontmatter key     (v2.1.218) SKILL_FRONTMATTER_FIELDS
S4 — frontmatter boolean widening           (v2.1.218) is_accepted_frontmatter_bool
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cc_scope_rules import KNOWN_SETTINGS_KEYS
from cpv_validation_common import (
    SKILL_FRONTMATTER_FIELDS,
    VALID_PLUGIN_ENV_VARS,
    is_accepted_frontmatter_bool,
    is_known_skill_frontmatter_key,
    is_valid_plugin_env_var,
)


# ---------------------------------------------------------------------------
# S1 — emojiCompletionEnabled setting (v2.1.217)
# ---------------------------------------------------------------------------
class TestS1EmojiCompletionSetting:
    """`emojiCompletionEnabled` (settings, v2.1.217) is a known settings key."""

    def test_emoji_completion_in_known_settings(self) -> None:
        """emojiCompletionEnabled must be in KNOWN_SETTINGS_KEYS."""
        assert "emojiCompletionEnabled" in KNOWN_SETTINGS_KEYS

    def test_bogus_setting_key_rejected(self) -> None:
        """Positive control: a made-up sibling is not in the reference set."""
        assert "emojiCompletionEnabledXYZ" not in KNOWN_SETTINGS_KEYS
        assert "emojiCompletion" not in KNOWN_SETTINGS_KEYS


# ---------------------------------------------------------------------------
# S2 — subagent-concurrency env vars (v2.1.217)
# ---------------------------------------------------------------------------
class TestS2SubagentConcurrencyEnvVars:
    """The two subagent-concurrency env vars (v2.1.217) are valid plugin env vars."""

    def test_max_concurrent_subagents_valid(self) -> None:
        """CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS is accepted (in-set and via helper)."""
        assert "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS" in VALID_PLUGIN_ENV_VARS
        assert is_valid_plugin_env_var("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS")

    def test_max_subagent_spawn_depth_valid(self) -> None:
        """CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH is accepted (in-set and via helper)."""
        assert "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH" in VALID_PLUGIN_ENV_VARS
        assert is_valid_plugin_env_var("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH")

    def test_bogus_subagent_env_var_rejected(self) -> None:
        """Positive control: an unknown CLAUDE_CODE_* sibling still fails the helper."""
        assert not is_valid_plugin_env_var("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS_XYZ")
        assert not is_valid_plugin_env_var("CLAUDE_CODE_MAX_SUBAGENTS_DEPTH")


# ---------------------------------------------------------------------------
# S3 — `background` skill frontmatter key (v2.1.218)
# ---------------------------------------------------------------------------
class TestS3BackgroundSkillFrontmatterKey:
    """`background` (v2.1.218) is a recognized skill frontmatter key."""

    def test_background_in_skill_fields(self) -> None:
        """background must be in SKILL_FRONTMATTER_FIELDS and pass the recognizer."""
        assert "background" in SKILL_FRONTMATTER_FIELDS
        assert is_known_skill_frontmatter_key("background")

    def test_bogus_skill_key_rejected(self) -> None:
        """Positive control: a typo of background still fails the recognizer."""
        assert not is_known_skill_frontmatter_key("backgroundd")
        assert not is_known_skill_frontmatter_key("bg")


# ---------------------------------------------------------------------------
# S4 — frontmatter boolean widening (v2.1.218)
# ---------------------------------------------------------------------------
class TestS4FrontmatterBooleanWidening:
    """CC v2.1.218 accepts yes/no/on/off/1/0 alongside true/false in frontmatter booleans."""

    def test_python_booleans_accepted(self) -> None:
        """True/False (already coerced by yaml.safe_load) are accepted."""
        assert is_accepted_frontmatter_bool(True)
        assert is_accepted_frontmatter_bool(False)

    def test_yaml_bool_strings_accepted(self) -> None:
        """Every documented literal reaching a validator as a string is accepted (any case)."""
        for value in ("true", "false", "yes", "no", "on", "off", "1", "0", "TRUE", "Yes", "OFF"):
            assert is_accepted_frontmatter_bool(value), f"{value!r} should be accepted"

    def test_int_one_zero_accepted(self) -> None:
        """The integer forms 1/0 (which yaml.safe_load leaves as int, not bool) are accepted."""
        assert is_accepted_frontmatter_bool(1)
        assert is_accepted_frontmatter_bool(0)

    def test_whitespace_padded_string_accepted(self) -> None:
        """A padded string boolean is accepted (the helper strips)."""
        assert is_accepted_frontmatter_bool("  yes  ")

    def test_genuine_non_booleans_rejected(self) -> None:
        """Positive control: genuine non-booleans still fail (never widen into junk)."""
        for value in (2, -1, "maybe", "truthy", "", [1], {"a": 1}, 2.5, None):
            assert not is_accepted_frontmatter_bool(value), f"{value!r} should be rejected"
