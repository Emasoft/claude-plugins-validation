#!/usr/bin/env python3
"""
CC spec-drift sync (Claude Code v2.1.205-212) — two-sided regression locks.

Six allowlist-widening / stale-warning-removal fixes (E1-E6), each verified
two-sided: the newly-accepted value/alias is now accepted (or no longer
warned), AND a positive control proves the same code path still rejects a
bogus sibling / still fires for a genuinely-unknown value.

E1 — `best` model alias                (cpv_validation_common._SHORT_MODEL_RE)
E2 — reserved marketplace names        (validate_marketplace.RESERVED_MARKETPLACE_NAMES)
E3 — `Setup` is a current hook event   (validate_hook + validate_hook_output — stale WARNING removed)
E4 — `MessageDisplay` is matcher-less  (validate_hook.EVENTS_WITHOUT_MATCHERS)
E5 — Notification matcher values       (validate_hook.COMMON_NOTIFICATION_TYPES)
E6 — StopFailure matcher values        (validate_hook.STOPFAILURE_ERRORS)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import VALID_MODELS, ValidationReport, is_valid_model
from validate_hook import validate_event_name, validate_matcher
from validate_hook_output import validate_output_payload
from validate_marketplace import validate_marketplace_name


# ---------------------------------------------------------------------------
# E1 — `best` model alias
# ---------------------------------------------------------------------------
class TestE1BestModelAlias:
    """`best` (model-config.md, v2.1.205) is a first-class model alias."""

    def test_best_alias_accepted(self) -> None:
        """is_valid_model must accept the `best` alias."""
        assert is_valid_model("best") is True

    def test_best_alias_with_1m_suffix_accepted(self) -> None:
        """is_valid_model must accept `best[1m]` (the [1m] context variant)."""
        assert is_valid_model("best[1m]") is True

    def test_best_alias_case_insensitive(self) -> None:
        """The short-alias regex is case-insensitive, so `BEST` is accepted too."""
        assert is_valid_model("BEST") is True

    def test_best_is_not_in_base_model_set(self) -> None:
        """`best` is an ALIAS, not a base model — like default/opusplan it stays
        out of VALID_MODELS (regex-only)."""
        assert "best" not in VALID_MODELS

    # positive controls — the widened regex must not accept bogus siblings
    def test_bogus_best_prefix_still_rejected(self) -> None:
        """A bogus sibling like `bestest` / `besty` must STILL be rejected."""
        assert is_valid_model("bestest") is False
        assert is_valid_model("besty") is False

    def test_unrelated_garbage_still_rejected(self) -> None:
        """A genuinely-unknown model value must still be rejected."""
        assert is_valid_model("gpt-4") is False
        assert is_valid_model("") is False


# ---------------------------------------------------------------------------
# E2 — reserved marketplace names (v2.1.205)
# ---------------------------------------------------------------------------
class TestE2ReservedMarketplaceNames:
    """`first-party-plugins` and `healthcare` were reserved in v2.1.205."""

    def test_first_party_plugins_is_reserved(self) -> None:
        """`first-party-plugins` must now be flagged CRITICAL as reserved."""
        results = validate_marketplace_name("first-party-plugins", "test.json")
        assert any(r.level == "CRITICAL" and "reserved" in r.message for r in results)

    def test_healthcare_is_reserved(self) -> None:
        """`healthcare` must now be flagged CRITICAL as reserved."""
        results = validate_marketplace_name("healthcare", "test.json")
        assert any(r.level == "CRITICAL" and "reserved" in r.message for r in results)

    # positive controls — the reserved check must not over-fire
    def test_prior_reserved_name_still_reserved(self) -> None:
        """A pre-existing reserved name must STILL be flagged (no regression)."""
        results = validate_marketplace_name("claude-code-marketplace", "test.json")
        assert any(r.level == "CRITICAL" and "reserved" in r.message for r in results)

    def test_ordinary_community_name_not_reserved(self) -> None:
        """An ordinary community marketplace name must NOT be flagged reserved."""
        results = validate_marketplace_name("my-cool-plugins", "test.json")
        assert not any(r.level == "CRITICAL" and "reserved" in r.message for r in results)


# ---------------------------------------------------------------------------
# E3 — `Setup` is a current hook event (stale deprecation WARNING removed)
# ---------------------------------------------------------------------------
_STALE_SETUP_MARKERS = (
    "legacy or deprecated",
    "not in the current official spec",
    "legacy event not listed in hooks.md",
    "best-effort",
)


class TestE3SetupIsCurrentEvent:
    """`Setup` (hooks.md ### Setup) must no longer draw a stale deprecation WARNING."""

    def test_setup_event_name_no_deprecation_warning(self) -> None:
        """validate_event_name('Setup') is valid and emits NO deprecation warning."""
        report = ValidationReport()
        assert validate_event_name("Setup", report) is True
        for r in report.results:
            for marker in _STALE_SETUP_MARKERS:
                assert marker not in r.message, f"stale Setup warning fired: {r.message!r}"

    def test_setup_output_payload_no_deprecation_warning(self) -> None:
        """validate_output_payload('Setup', {}) emits NO stale legacy/best-effort warning."""
        report = validate_output_payload("Setup", {})
        for r in report.results:
            for marker in _STALE_SETUP_MARKERS:
                assert marker not in r.message, f"stale Setup output warning fired: {r.message!r}"

    # positive controls — the unknown-event rejection path must still fire
    def test_unknown_event_name_still_rejected(self) -> None:
        """A genuinely-unknown event name must STILL be rejected CRITICAL."""
        report = ValidationReport()
        assert validate_event_name("TotallyBogusEvent", report) is False
        assert any(r.level == "CRITICAL" for r in report.results)

    def test_unknown_output_event_still_rejected(self) -> None:
        """validate_output_payload on an unknown event must STILL flag it (MAJOR)."""
        report = validate_output_payload("TotallyBogusEvent", {})
        assert any(r.level == "MAJOR" and "Unknown hook event" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# E4 — `MessageDisplay` is matcher-less
# ---------------------------------------------------------------------------
class TestE4MessageDisplayMatcherless:
    """A matcher on MessageDisplay is ignored (hooks.md no-matcher table)."""

    def test_message_display_matcher_gets_ignored_info(self) -> None:
        """A matcher on MessageDisplay must draw the 'matchers are ignored' INFO."""
        report = ValidationReport()
        assert validate_matcher("Bash", "MessageDisplay", report) is True
        assert any("matchers are ignored" in r.message for r in report.results if r.level == "INFO")

    # positive control — a matcher-supporting event must NOT get that INFO
    def test_matcher_supporting_event_not_ignored(self) -> None:
        """PreToolUse DOES support matchers — it must NOT get the 'ignored' INFO."""
        report = ValidationReport()
        validate_matcher("Bash", "PreToolUse", report)
        assert not any("matchers are ignored" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# E5 — Notification matcher values
# ---------------------------------------------------------------------------
class TestE5NotificationMatcherValues:
    """`elicitation_complete` / `elicitation_response` are valid Notification values."""

    def test_elicitation_complete_no_unknown_info(self) -> None:
        """`elicitation_complete` must NOT draw the spurious 'is not a known' INFO."""
        report = ValidationReport()
        validate_matcher("elicitation_complete", "Notification", report)
        assert not any("is not a known" in r.message for r in report.results)

    def test_elicitation_response_no_unknown_info(self) -> None:
        """`elicitation_response` must NOT draw the spurious 'is not a known' INFO."""
        report = ValidationReport()
        validate_matcher("elicitation_response", "Notification", report)
        assert not any("is not a known" in r.message for r in report.results)

    # positive control — a genuinely-unknown value must still be hinted
    def test_bogus_notification_value_still_hinted(self) -> None:
        """A genuinely-unknown Notification value must STILL draw the INFO hint."""
        report = ValidationReport()
        validate_matcher("not_a_real_notification", "Notification", report)
        assert any("is not a known" in r.message for r in report.results if r.level == "INFO")


# ---------------------------------------------------------------------------
# E6 — StopFailure matcher values
# ---------------------------------------------------------------------------
class TestE6StopFailureMatcherValues:
    """`overloaded` / `oauth_org_not_allowed` / `model_not_found` are valid StopFailure values."""

    def test_new_stopfailure_values_no_unknown_info(self) -> None:
        """The 3 newly-recognised StopFailure values must NOT draw a spurious INFO."""
        for value in ("overloaded", "oauth_org_not_allowed", "model_not_found"):
            report = ValidationReport()
            validate_matcher(value, "StopFailure", report)
            assert not any("is not a known" in r.message for r in report.results), (
                f"{value} spuriously flagged unknown"
            )

    def test_prior_stopfailure_value_still_accepted(self) -> None:
        """A pre-existing StopFailure value must STILL be accepted (no regression)."""
        report = ValidationReport()
        validate_matcher("rate_limit", "StopFailure", report)
        assert not any("is not a known" in r.message for r in report.results)

    # positive control — a genuinely-unknown value must still be hinted
    def test_bogus_stopfailure_value_still_hinted(self) -> None:
        """A genuinely-unknown StopFailure error must STILL draw the INFO hint."""
        report = ValidationReport()
        validate_matcher("not_a_real_error", "StopFailure", report)
        assert any("is not a known" in r.message for r in report.results if r.level == "INFO")
