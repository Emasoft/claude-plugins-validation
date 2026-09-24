"""WP12 — pin the CC 2.1.281 Opus 5.5 / Fable 5.1 per-Mtok rates, and the
family-fallback + estimate-disclosure behavior added alongside them.

Sources (bundled claude-api skill, "Current Models" table + prompt-caching.md):
  - Opus 5.5:  "$4 / $20 per MTok", cache reads "$0.20/MTok"
  - Fable 5.1: "$10/$50 per MTok", cache reads "$0.25/MTok"
  - shared/prompt-caching.md: "Cache writes cost 1.25x for 5-minute TTL"
    -> Opus 5.5 cache_write = 4.0 * 1.25 = 5.0
    -> Fable 5.1 cache_write = 10.0 * 1.25 = 12.5

If MODEL_PRICING ever disagrees with these four numbers per model, that is a
finding to report, not a reason to edit this test to match the code.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_token_cost as ctc  # noqa: E402


class TestOpus55AndFable51PricingPinned:
    """Pin all four USD-per-Mtok numbers for claude-opus-5-5 and claude-fable-5-1."""

    def test_opus_5_5_all_four_rates(self) -> None:
        """claude-opus-5-5 = input 4.00 / output 20.00 / cache_write 5.00 / cache_read 0.20."""
        p = ctc.MODEL_PRICING["claude-opus-5-5"]
        assert p["input"] == 4.00
        assert p["output"] == 20.00
        assert p["cache_write"] == 5.00
        assert p["cache_read"] == 0.20

    def test_fable_5_1_all_four_rates(self) -> None:
        """claude-fable-5-1 = input 10.00 / output 50.00 / cache_write 12.50 / cache_read 0.25."""
        p = ctc.MODEL_PRICING["claude-fable-5-1"]
        assert p["input"] == 10.00
        assert p["output"] == 50.00
        assert p["cache_write"] == 12.50
        assert p["cache_read"] == 0.25

    def test_get_pricing_returns_the_pinned_rows(self) -> None:
        """get_pricing resolves both ids to the exact pinned dict objects."""
        assert ctc.get_pricing("claude-opus-5-5") is ctc.MODEL_PRICING["claude-opus-5-5"]
        assert ctc.get_pricing("claude-fable-5-1") is ctc.MODEL_PRICING["claude-fable-5-1"]


class TestFamilyFallbackResolvesToNewestRow:
    """An unlisted id in a known family resolves to that family's newest row, not a legacy bucket."""

    def test_unknown_opus_id_resolves_to_opus_5_5(self) -> None:
        """claude-opus-9 (no listed row) falls back to the newest Opus row (5.5), not the retired 4.1 bucket."""
        assert ctc.get_pricing("claude-opus-9") is ctc.MODEL_PRICING["claude-opus-5-5"]

    def test_unknown_sonnet_id_resolves_to_newest_sonnet(self) -> None:
        """claude-sonnet-9 (no listed row, no 5-x substring match) falls back to the newest Sonnet row."""
        assert ctc.get_pricing("claude-sonnet-9") is ctc.MODEL_PRICING["claude-sonnet-5"]

    def test_unknown_fable_id_resolves_to_fable_5_1(self) -> None:
        """An unrecognised Fable id still resolves to Fable 5.1, never DEFAULT."""
        assert ctc.get_pricing("claude-fable-9") is ctc.MODEL_PRICING["claude-fable-5-1"]

    def test_known_row_is_not_misreported_as_a_fallback(self) -> None:
        """A model id that exactly matches (or dated-suffix-matches) a row is NOT a fallback."""
        assert ctc.pricing_is_estimate("claude-opus-5-5") is False
        assert ctc.pricing_is_estimate("claude-opus-5-5-20260901") is False

    def test_family_fallback_is_flagged_as_an_estimate(self) -> None:
        """pricing_is_estimate is True exactly when get_pricing had to guess the family's newest row."""
        assert ctc.pricing_is_estimate("claude-opus-9") is True
        assert ctc.pricing_is_estimate("claude-sonnet-9") is True

    def test_default_pricing_is_also_flagged_as_an_estimate(self) -> None:
        """An id in no known family (or an empty id) is DEFAULT pricing, also an estimate."""
        assert ctc.pricing_is_estimate("gpt-4o-mini") is True
        assert ctc.pricing_is_estimate("") is True


class TestFormatCostLineDisclosesEstimates:
    """format_cost_line appends an estimate marker only for a fallback-priced model."""

    def _usage(self) -> ctc.TokenUsage:
        u = ctc.TokenUsage()
        u.input_tokens = 1000
        u.output_tokens = 500
        return u

    def test_unlisted_model_gets_the_estimate_marker(self) -> None:
        """A family-fallback model's cost line ends with the disclosure suffix."""
        line = ctc.format_cost_line(self._usage(), model="claude-opus-9")
        assert line.endswith("(estimated: unlisted model)")

    def test_listed_model_has_no_estimate_marker(self) -> None:
        """A specifically-priced model's cost line carries no disclosure suffix."""
        line = ctc.format_cost_line(self._usage(), model="claude-opus-5-5")
        assert "(estimated: unlisted model)" not in line

    def test_empty_model_gets_the_unknown_marker(self) -> None:
        """An empty model id gets '(estimated: model unknown)', not 'unlisted model'."""
        line = ctc.format_cost_line(self._usage(), model="")
        assert line.endswith("(estimated: model unknown)")

    def test_literal_unknown_model_gets_the_unknown_marker(self) -> None:
        """The literal id 'unknown' also gets '(estimated: model unknown)'."""
        line = ctc.format_cost_line(self._usage(), model="unknown")
        assert line.endswith("(estimated: model unknown)")


class TestSpecificMatchRequiresRealBoundary:
    """A row key must be followed by a real boundary, not any digit/character, to count as specific."""

    def test_exact_id_is_specific(self) -> None:
        """The bare row key is an exact, non-estimate match."""
        assert ctc.pricing_is_estimate("claude-opus-5-5") is False
        assert ctc.get_pricing("claude-opus-5-5") is ctc.MODEL_PRICING["claude-opus-5-5"]

    def test_dated_suffix_is_specific(self) -> None:
        """A row key plus a '-' and an 8-digit date suffix is a specific match."""
        assert ctc.pricing_is_estimate("claude-opus-5-5-20260901") is False
        assert ctc.get_pricing("claude-opus-5-5-20260901") is ctc.MODEL_PRICING["claude-opus-5-5"]

    def test_bracketed_context_window_tag_is_specific(self) -> None:
        """A row key plus a bracketed context-window tag ('[1m]') is a specific match."""
        assert ctc.pricing_is_estimate("claude-sonnet-5[1m]") is False
        assert ctc.get_pricing("claude-sonnet-5[1m]") is ctc.MODEL_PRICING["claude-sonnet-5"]

    def test_at_versioned_tag_is_specific(self) -> None:
        """A row key plus an '@'-versioned tag is a specific match."""
        assert ctc.pricing_is_estimate("claude-opus-5-5@20260901") is False
        assert ctc.get_pricing("claude-opus-5-5@20260901") is ctc.MODEL_PRICING["claude-opus-5-5"]

    def test_bedrock_provider_prefix_and_version_suffix_is_specific(self) -> None:
        """A leading provider prefix plus a Bedrock '-v1:0' suffix around a row key is specific."""
        model_id = "us.anthropic.claude-opus-5-5-v1:0"
        assert ctc.pricing_is_estimate(model_id) is False
        assert ctc.get_pricing(model_id) is ctc.MODEL_PRICING["claude-opus-5-5"]

    def test_extra_digit_after_row_key_is_not_specific(self) -> None:
        """claude-opus-4-10 is a DIFFERENT model than claude-opus-4-1 and must not inherit its price."""
        assert ctc.pricing_is_estimate("claude-opus-4-10") is True
        # Falls back to the newest Opus row (family fallback), not the 4.1 row it contains.
        assert ctc.get_pricing("claude-opus-4-10") is ctc.MODEL_PRICING["claude-opus-5-5"]

    def test_extra_digit_after_shorter_row_key_is_not_specific(self) -> None:
        """claude-sonnet-5-1 is a DIFFERENT model than claude-sonnet-5 and must not inherit its price."""
        assert ctc.pricing_is_estimate("claude-sonnet-5-1") is True
        assert ctc.get_pricing("claude-sonnet-5-1") is ctc.MODEL_PRICING["claude-sonnet-5"]
