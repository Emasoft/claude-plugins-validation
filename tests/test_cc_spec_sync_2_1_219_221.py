#!/usr/bin/env python3
"""
CC spec-drift sync (Claude Code v2.1.219-221) — two-sided regression locks.

Allowlist-widening / FP-reduction adds carrying the spec forward from the
v2.1.218 sweep to v2.1.221. Every new value is verified two-sided: the
newly-accepted value is now accepted, AND a positive control proves the same
code path still REJECTS a bogus sibling. A one-sided "it is accepted now" test
would also pass against a regex widened to `\\w+`, which is exactly the change
this sync must not make.

S1 — `DirectoryAdded` hook event            (v2.1.219) VALID_HOOK_EVENTS
S2 — `DirectoryAdded` TAKES matchers        (v2.1.219) EVENTS_WITHOUT_MATCHERS
S3 — `claude-fable-5` full model ID         (v2.1.170) _FULL_MODEL_ID_RE
S4 — model error strings name `fable`       (v2.1.170) validate_skill/command
S5 — `workflowSizeGuideline` setting        (v2.1.219) KNOWN_SETTINGS_KEYS
S6 — `strictAllowlist` stays OUT (nested)   (v2.1.219) KNOWN_SETTINGS_KEYS

Doc ground truth (fetched, not recalled): hooks.md event table lists 31 events
including `DirectoryAdded`; its matcher-table row gives matchers
`slash_command`, `register_repo_root`. The changelog entry for v2.1.219 spells
the sandbox setting `sandbox.network.strictAllowlist` — a NESTED key.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cc_scope_rules import KNOWN_SETTINGS_KEYS
from cpv_validation_common import VALID_HOOK_EVENTS, VALID_MODELS, is_valid_model
from validate_hook import EVENTS_WITHOUT_MATCHERS
from validate_hook_output import HOOK_OUTPUT_EVENT_FIELDS


# ---------------------------------------------------------------------------
# S1 — DirectoryAdded hook event (v2.1.219)
# ---------------------------------------------------------------------------
class TestS1DirectoryAddedEvent:
    """`DirectoryAdded` (hooks.md, v2.1.219) is a valid hook event."""

    def test_directory_added_is_valid_event(self) -> None:
        """DirectoryAdded must be in VALID_HOOK_EVENTS."""
        assert "DirectoryAdded" in VALID_HOOK_EVENTS

    def test_bogus_event_still_rejected(self) -> None:
        """Positive control: made-up siblings are still unknown events."""
        assert "DirectoryAddedXYZ" not in VALID_HOOK_EVENTS
        assert "DirectoryRemoved" not in VALID_HOOK_EVENTS
        assert "DirectoryChanged" not in VALID_HOOK_EVENTS

    def test_directory_added_has_an_output_schema(self) -> None:
        """Adding an event without its output schema is an incomplete change.

        `test_hook_output_event_fields_covers_all_events` enforces the same
        invariant repo-wide and is what CAUGHT this omission; this asserts the
        specific row so the failure names the event directly.
        """
        assert "DirectoryAdded" in HOOK_OUTPUT_EVENT_FIELDS

    def test_directory_added_output_schema_is_empty(self) -> None:
        """EMPTY on purpose — the docs give it no decision control.

        Its siblings CwdChanged/FileChanged carry `watchPaths`, so the tempting
        move is to hand this one fields too. The doc says output reaches the
        user through the COMMON `systemMessage`, not an event-specific key.
        """
        assert HOOK_OUTPUT_EVENT_FIELDS["DirectoryAdded"] == frozenset()

    def test_sibling_events_still_carry_their_fields(self) -> None:
        """Control: the empty set above is a fact about this event, not a broken map."""
        assert HOOK_OUTPUT_EVENT_FIELDS["CwdChanged"] == frozenset({"watchPaths"})
        assert HOOK_OUTPUT_EVENT_FIELDS["FileChanged"] == frozenset({"watchPaths"})


# ---------------------------------------------------------------------------
# S2 — DirectoryAdded TAKES matchers (v2.1.219)
# ---------------------------------------------------------------------------
class TestS2DirectoryAddedTakesMatchers:
    """DirectoryAdded accepts matchers, so it must NOT be matcher-less.

    hooks.md's matcher table gives it `slash_command` / `register_repo_root`.
    Listing it in EVENTS_WITHOUT_MATCHERS would make CPV flag a CORRECT hook
    config that scopes on how the directory was added.
    """

    def test_directory_added_not_in_matcherless_set(self) -> None:
        """DirectoryAdded must NOT be in EVENTS_WITHOUT_MATCHERS."""
        assert "DirectoryAdded" not in EVENTS_WITHOUT_MATCHERS

    def test_matcherless_set_is_non_vacuous(self) -> None:
        """Control: genuinely matcher-less events ARE in the set.

        Without this, the assertion above would also pass against an empty set.
        """
        assert "UserPromptSubmit" in EVENTS_WITHOUT_MATCHERS
        assert "Stop" in EVENTS_WITHOUT_MATCHERS


# ---------------------------------------------------------------------------
# S3 — claude-fable-5 full model ID
# ---------------------------------------------------------------------------
class TestS3FableFullModelId:
    """The full-ID spelling of Claude Fable 5 validates.

    VALID_MODELS already carried the short alias `fable`, so only the FULL-ID
    form (`claude-fable-5`) was being rejected.
    """

    def test_short_alias_was_already_valid(self) -> None:
        """Baseline: the short alias was never the gap."""
        assert "fable" in VALID_MODELS
        assert is_valid_model("fable")

    def test_fable_full_id_valid(self) -> None:
        """claude-fable-5 is accepted as a full model ID."""
        assert is_valid_model("claude-fable-5")

    def test_fable_full_id_with_1m_suffix_valid(self) -> None:
        """claude-fable-5[1m] is accepted (same optional suffix as every family)."""
        assert is_valid_model("claude-fable-5[1m]")

    def test_sibling_families_still_valid(self) -> None:
        """The pre-existing families did not regress."""
        assert is_valid_model("claude-opus-5")
        assert is_valid_model("claude-sonnet-4-6")
        assert is_valid_model("claude-haiku-4-5-20251001")

    def test_hallucinated_family_still_rejected(self) -> None:
        """Positive control — the load-bearing half.

        The family alternation is enumerated so a hallucinated model ID is
        caught. A regex widened to `claude-\\w+-\\d...` would pass every
        assertion above while silently accepting all of these.
        """
        assert not is_valid_model("claude-gpt-4")
        assert not is_valid_model("claude-bogus-5")
        assert not is_valid_model("claude-mythos-5")
        assert not is_valid_model("claude-fable")  # no version segment
        assert not is_valid_model("claude-fabel-5")  # misspelt family
        assert not is_valid_model("openai-fable-5")  # wrong vendor prefix


# ---------------------------------------------------------------------------
# S4 — model error strings name `fable`
# ---------------------------------------------------------------------------
class TestS4ModelErrorStringsNameFable:
    """The invalid-model messages must enumerate every accepted family.

    A message that omits a family the code accepts sends an author to change
    something that was already correct.

    FOUR validators emit this message, not two — `validate_skill`,
    `validate_command`, `validate_skill_comprehensive` and `validate_agent`.
    The first pass fixed only the two found by reading the change set; the
    other two surfaced from a repo-wide grep, which is why this test is keyed
    on an ENUMERATED list of every emitting source rather than the ones that
    happened to be noticed.
    """

    # Sources whose message spells the family list out literally.
    _LITERAL_LIST_SOURCES = (
        "validate_skill.py",
        "validate_command.py",
        "validate_skill_comprehensive.py",
    )
    # validate_agent interpolates VALID_MODELS, so it tracks the family set
    # automatically and only its full-ID example is hand-maintained.
    _ALL_SOURCES = (*_LITERAL_LIST_SOURCES, "validate_agent.py")

    def _window(self, name: str) -> str:
        src = (scripts_dir / name).read_text(encoding="utf-8")
        assert "Invalid 'model' value" in src, f"{name} no longer emits the message"
        idx = src.index("Invalid 'model' value")
        return src[idx : idx + 400]

    def test_every_literal_list_names_fable(self) -> None:
        """Each hand-written family list includes fable."""
        for name in self._LITERAL_LIST_SOURCES:
            assert "fable" in self._window(name), f"{name} omits fable"

    def test_agent_message_tracks_family_set_automatically(self) -> None:
        """validate_agent interpolates VALID_MODELS rather than hardcoding families."""
        assert "{VALID_MODELS}" in self._window("validate_agent.py")

    def test_no_emitter_cites_a_retired_example_id(self) -> None:
        """Every message cites a current full-ID example, not a stale one."""
        for name in self._ALL_SOURCES:
            window = self._window(name)
            assert "claude-opus-4-6" not in window, f"{name} cites a retired example id"
            assert "claude-opus-5" in window, f"{name} lacks a current example id"

    def test_emitter_list_is_complete(self) -> None:
        """Guard: no OTHER script emits this message unchecked.

        Without this, a fifth validator could be added with a stale list and
        every assertion above would still pass.
        """
        emitters = {
            p.name for p in scripts_dir.glob("*.py") if "Invalid 'model' value" in p.read_text(encoding="utf-8")
        }
        assert emitters == set(self._ALL_SOURCES), f"emitter set drifted: {emitters ^ set(self._ALL_SOURCES)}"


# ---------------------------------------------------------------------------
# S4b — the fixer's doc MIRRORS must not drift from the code
# ---------------------------------------------------------------------------
class TestFixerDocMirrorsMatchCode:
    """`hook-fixes.md` calls itself the mirror of two code constants.

    A fixer agent resolves a finding by reading that guide, so a stale mirror
    sends it to do the wrong thing — and this is not hypothetical: the
    matcher-less list had been missing `MessageDisplay` since v2.160.0, found
    only by auditing it here. Fixing instances and waiting for the next drift
    is what these tests replace.
    """

    _DOC = Path(__file__).parent.parent / "skills/cpv-fix-validation/references/hook-fixes.md"

    def _text(self) -> str:
        return self._DOC.read_text(encoding="utf-8")

    def test_event_list_mirror_matches_valid_hook_events(self) -> None:
        """The bulleted event list equals VALID_HOOK_EVENTS, both directions."""
        import re

        block = self._text().split("Valid event names are", 1)[1].split("3. **Wrong**", 1)[0]
        listed = set(re.findall(r"^\s*-\s+`([A-Za-z]+)`", block, re.M))
        assert listed == set(VALID_HOOK_EVENTS), (
            f"mirror drift — code-only: {sorted(set(VALID_HOOK_EVENTS) - listed)}, "
            f"doc-only: {sorted(listed - set(VALID_HOOK_EVENTS))}"
        )

    def test_event_list_mirror_is_non_vacuous(self) -> None:
        """Guard the guard: a scoping change that matched nothing would pass above."""
        import re

        block = self._text().split("Valid event names are", 1)[1].split("3. **Wrong**", 1)[0]
        assert len(re.findall(r"^\s*-\s+`([A-Za-z]+)`", block, re.M)) >= 25

    def test_matcherless_mirror_matches_code(self) -> None:
        """The 'do NOT support matchers' list equals EVENTS_WITHOUT_MATCHERS."""
        import re

        line = next(line for line in self._text().splitlines() if "do NOT support matchers" in line)
        listed = set(re.findall(r"`([A-Za-z]+)`", line)) - {
            "EVENTS_WITHOUT_MATCHERS",
            "validate_hook.py",
        }
        assert listed == set(EVENTS_WITHOUT_MATCHERS), (
            f"mirror drift — code-only: {sorted(set(EVENTS_WITHOUT_MATCHERS) - listed)}, "
            f"doc-only: {sorted(listed - set(EVENTS_WITHOUT_MATCHERS))}"
        )

    def test_directory_added_documented_as_taking_matchers(self) -> None:
        """The guide must not list DirectoryAdded as matcher-less.

        Its siblings are matcher-less, so the pattern invites the mistake.
        """
        line = next(line for line in self._text().splitlines() if "do NOT support matchers" in line)
        assert "DirectoryAdded" not in line
        assert "DirectoryAdded" in self._text()


# ---------------------------------------------------------------------------
# S5 — workflowSizeGuideline setting (v2.1.219)
# ---------------------------------------------------------------------------
class TestS5WorkflowSizeGuidelineSetting:
    """`workflowSizeGuideline` is a top-level settings key ("any settings file")."""

    def test_workflow_size_guideline_known(self) -> None:
        """workflowSizeGuideline must be in KNOWN_SETTINGS_KEYS."""
        assert "workflowSizeGuideline" in KNOWN_SETTINGS_KEYS

    def test_bogus_setting_key_rejected(self) -> None:
        """Positive control: made-up siblings are not in the reference set."""
        assert "workflowSizeGuidelineXYZ" not in KNOWN_SETTINGS_KEYS
        assert "workflowSize" not in KNOWN_SETTINGS_KEYS


# ---------------------------------------------------------------------------
# S6 — strictAllowlist stays OUT (it is nested, not top-level)
# ---------------------------------------------------------------------------
class TestS6StrictAllowlistIsNestedNotTopLevel:
    """`sandbox.network.strictAllowlist` must NOT be a top-level known key.

    KNOWN_SETTINGS_KEYS models TOP-LEVEL keys only; nested paths live in the
    *_NESTED_KEYS tuple sets. `sandbox` is already known and its sub-keys are
    tolerated, so no entry is owed — and adding a bare `strictAllowlist` would
    EXCUSE a genuine typo written at the top level. This test pins the decision
    so a future sync does not "helpfully" add it.
    """

    def test_strict_allowlist_not_top_level(self) -> None:
        """A bare strictAllowlist is not a top-level settings key."""
        assert "strictAllowlist" not in KNOWN_SETTINGS_KEYS

    def test_sandbox_parent_is_known(self) -> None:
        """Control: the parent key IS known, which is why no entry is owed."""
        assert "sandbox" in KNOWN_SETTINGS_KEYS
