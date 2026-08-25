"""CC spec-drift sync for the v2.1.236 → v2.1.240 window.

Every assertion is two-sided: each "now accepted" case is paired with a control
proving the same code path still rejects what it must reject.

The window's plugin-spec surface, verified against the RAW docs by mechanical
set-diff rather than the changelog summary (the recorded spec-drift method),
and then PROBED against the real validator before being treated as a gap:

* **v2.1.236 ``classifierContext``** — a new ``hookSpecificOutput`` field on
  ``PostToolUse`` (hooks.md:1939, example at :1973) carrying a note about this
  call's result to the auto-mode classifier rather than to Claude. CPV emitted
  a NIT — which blocks ``--strict`` — on a spec-correct hook.
* **v2.1.238 ``keybindingFlavor``** plus a 10-key accumulated backfill of
  ``KNOWN_SETTINGS_KEYS``. The authoritative "Available settings" table MOVED
  to ``settings-reference.md``; ``settings.md``, which the previous window's
  set-diff read, no longer carries a single key row, so that diff had silently
  become vacuous.

Verified as needing NO change rather than assumed (each probed):

* ``tools-reference.md`` col-1 vs ``VALID_TOOLS`` — no doc-side additions.
* skill ``license`` / ``compatibility`` — already in ``OPENSPEC_ALLOWED_FIELDS``;
  and the doc specifies ``compatibility`` as a **string** of up to 500 chars, so
  CPV rejecting a mapping is CORRECT, not a gap.
* ``metadata.pluginRoot`` (v2.1.239) — handled since GAP-34, bare-name sources
  included.
* the v2.1.239 UTF-8 BOM fix — ``parse_frontmatter`` already strips a BOM.
* ``DirectoryAdded`` / ``updatedInput`` — already present.

Deliberate omissions, pinned WITH their reason so the next sync checks the
reason instead of re-litigating the decision:

* the ``<name>@synced`` plugin identity (v2.1.239, plugins-reference.md:405) is
  NOT added to any reserved-marketplace-name set. It is a load-identity suffix
  for claude.ai-synced plugins, and no doc calls it a reserved marketplace
  name; inventing a gate the spec does not have is the v2.154.1 ruling.
* every changelog-only settings key stays OUT of ``KNOWN_SETTINGS_KEYS`` — it
  is a typo detector, and an entry at an unverifiable level excuses a genuine
  typo written there (the ``crossSessionInbound`` / ``sandbox.ripgrep``
  precedent).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cc_scope_rules  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402
import validate_marketplace as vm  # noqa: E402
import validate_skill_comprehensive as vsc  # noqa: E402
from validate_command import validate_command  # noqa: E402
from validate_hook_output import (  # noqa: E402
    HOOK_OUTPUT_EVENT_FIELDS,
    validate_output_payload,
)

DOC_BLOCKING_LEVELS = {"CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING"}

# Sentinel for "the key is absent", distinct from any value it could hold.
_ABSENT = object()


def _hso_findings(event_name: str, hso: dict[str, Any]) -> list[str]:
    """Blocking-tier messages from validating a hookSpecificOutput payload."""
    payload = {"hookSpecificOutput": dict(hso, hookEventName=event_name)}
    report = validate_output_payload(event_name, payload)
    return [r.message for r in report.results if r.level in DOC_BLOCKING_LEVELS]


class TestClassifierContext:
    """v2.1.236 — PostToolUse hookSpecificOutput gained ``classifierContext``."""

    def test_accepted_on_posttooluse(self) -> None:
        """A spec-correct classifierContext note draws no finding."""
        assert _hso_findings("PostToolUse", {"classifierContext": "staging, not production"}) == []

    def test_in_posttooluse_field_set(self) -> None:
        """The field is registered against PostToolUse, not bolted on elsewhere."""
        assert "classifierContext" in HOOK_OUTPUT_EVENT_FIELDS["PostToolUse"]

    def test_control_typo_still_rejected(self) -> None:
        """Control: a near-miss spelling is still an unknown field."""
        found = _hso_findings("PostToolUse", {"classifierContextt": "typo"})
        assert any("classifierContextt" in m for m in found)

    def test_control_wrong_event_still_rejected(self) -> None:
        """Control: the field is PostToolUse-only — PreToolUse still rejects it."""
        found = _hso_findings("PreToolUse", {"classifierContext": "wrong event"})
        assert any("classifierContext" in m for m in found)

    def test_control_sibling_fields_intact(self) -> None:
        """Control: adding the field did not drop PostToolUse's existing ones."""
        assert {"decision", "reason", "additionalContext", "updatedToolOutput"} <= HOOK_OUTPUT_EVENT_FIELDS[
            "PostToolUse"
        ]


class TestSettingsKeyBackfill:
    """v2.1.238 keybindingFlavor + the settings-reference.md backfill."""

    NEW_KEYS = (
        "diffTool",
        "enableWorkflows",
        "externalEditorContext",
        "keybindingFlavor",
        "permissionExplainerEnabled",
        "skipAutoPermissionPrompt",
        "skipDangerousModePermissionPrompt",
        "sshHostAllowlist",
        "syncClaudeAiSkills",
        "teammateDefaultModel",
        "terminalTitleFromRename",
    )

    def test_every_documented_key_known(self) -> None:
        """Each key is a verified row of settings-reference.md's table."""
        missing = [k for k in self.NEW_KEYS if k not in cc_scope_rules.KNOWN_SETTINGS_KEYS]
        assert missing == []

    def test_control_typos_still_unknown(self) -> None:
        """Control: near-miss spellings of the new keys are still typos.

        The set is a typo detector; if a widening made these clear too, it
        would have stopped detecting the thing it exists for.
        """
        for typo in ("keybindingFlavour", "diffTools", "sshHostAllowList", "syncClaudeAISkills"):
            assert typo not in cc_scope_rules.KNOWN_SETTINGS_KEYS

    def test_control_prior_window_keys_retained(self) -> None:
        """Control: the v2.1.233–235 backfill survived this edit."""
        assert {"spellcheck", "theme", "verbose", "fastMode"} <= set(cc_scope_rules.KNOWN_SETTINGS_KEYS)

    def test_control_nested_subkeys_still_excluded(self) -> None:
        """Control: this set stays TOP-LEVEL only."""
        for nested in ("strictAllowlist", "ripgrep", "enabled"):
            assert nested not in cc_scope_rules.KNOWN_SETTINGS_KEYS


class TestMarketplaceHeadersHelper:
    """v2.1.238 — a catalog entry's ``headersHelper`` mints HTTP headers.

    Before this window CPV emitted a publish-blocking MAJOR saying the field
    "will be ignored at install time". That is false in the dangerous
    direction — the field is honoured and RUNS A COMMAND — so CPV was telling
    authors to delete a working auth mechanism.
    """

    @staticmethod
    def _findings(value: Any) -> list[tuple[str, str]]:
        entry = {} if value is _ABSENT else {"headersHelper": value}
        return [(r.level, r.message) for r in vm._validate_headers_helper(entry, "p1", "marketplace.json")]

    def test_field_is_a_known_entry_field(self) -> None:
        """The unknown-field MAJOR no longer fires on a spec-real field."""
        assert "headersHelper" in vm.OPTIONAL_PLUGIN_FIELDS
        assert "headersHelper" in vm._KNOWN_MARKETPLACE_ENTRY_FIELDS

    def test_plain_command_is_clean(self) -> None:
        """A readable helper command draws nothing."""
        assert self._findings("${CLAUDE_PLUGIN_ROOT}/scripts/mint-headers.sh") == []

    def test_absent_is_clean(self) -> None:
        """Control: the check is opt-in — an entry without the field is untouched."""
        assert self._findings(_ABSENT) == []

    def test_non_string_is_major(self) -> None:
        """A non-string cannot be a command under any reading — MAJOR."""
        assert [lvl for lvl, _ in self._findings(123)] == ["MAJOR"]

    def test_blank_string_is_major(self) -> None:
        """A whitespace-only helper declares a command that cannot run — MAJOR."""
        assert [lvl for lvl, _ in self._findings("   ")] == ["MAJOR"]

    def test_control_character_warns_not_blocks(self) -> None:
        """Unreadable-shape findings are WARNING, never MAJOR.

        The readability rules are spec'd for `command` SOURCES, not for this
        changelog-only field; asserting MAJOR here would invent a gate Claude
        Code is not known to enforce.
        """
        assert [lvl for lvl, _ in self._findings("mint.sh\n--evil")] == ["WARNING"]

    def test_space_run_warns(self) -> None:
        """A long space run hides a command's tail past the prompt edge."""
        assert [lvl for lvl, _ in self._findings("mint.sh" + " " * 6 + "; curl evil")] == ["WARNING"]

    def test_overlong_warns(self) -> None:
        """Past the command length limit the user cannot read what they accept."""
        assert [lvl for lvl, _ in self._findings("x" * (vm._COMMAND_MAX_LEN + 1))] == ["WARNING"]

    def test_readability_predicates_are_shared_not_forked(self) -> None:
        """Control: the rules are REUSED from the command source, not re-derived.

        Two copies drift, and a drifted copy is how a validator accepts on one
        path what it rejects on another.
        """
        assert vm._COMMAND_MAX_LEN == 500
        assert vm._COMMAND_NON_PRINTABLE_ASCII_RE.search("\t") is not None
        assert vm._COMMAND_SPACE_RUN_RE.search("a    b") is not None

    def test_control_alwaysload_still_rejected(self) -> None:
        """Control: widening for headersHelper did not open the allowlist.

        `alwaysLoad` is an MCP *server* field (validate_mcp KNOWN_SERVER_FIELDS);
        a stale comment used to claim this marketplace allowlist carried it.
        """
        assert "alwaysLoad" not in vm._KNOWN_MARKETPLACE_ENTRY_FIELDS

    def test_control_unknown_entry_field_still_major(self) -> None:
        """Control: an invented entry field still draws the unknown-field MAJOR."""
        assert "audience" not in vm._KNOWN_MARKETPLACE_ENTRY_FIELDS


class TestBuiltinSlashCommandBackfill:
    """The false NEGATIVE this window's corpus sweep uncovered.

    `BUILTIN_SLASH_COMMANDS` held 53 names against commands.md's 108, so a
    plugin shipping `commands/plan.md` (or run/diff/export/hooks/vim/…) drew no
    collision WARNING. That is the FN direction: the bare `/name` form resolves
    to the built-in, so the plugin's own command silently never runs.

    Found only by DISPROVING a grep-miss — searching for
    `BUILTIN_COMMAND|RESERVED_COMMAND|builtin_commands` returned nothing and
    was briefly read as "CPV has no such check". The constant is named
    `BUILTIN_SLASH_COMMANDS`. A grep that misses is not an absence.
    """

    # A sample spanning the whole backfill, including the two the v2.1.236–240
    # changelog itself touched (`claude-api` gained `upgrade`; `list-agents`
    # gained teammate rows).
    BACKFILLED = (
        "plan",
        "run",
        "diff",
        "export",
        "hooks",
        "vim",
        "goal",
        "schedule",
        "insights",
        "simplify",
        "claude-api",
        "list-agents",
        "workflows",
        "artifacts",
        "dataviz",
    )

    def test_backfilled_names_known(self) -> None:
        """Each is a row of commands.md's built-in table."""
        missing = [c for c in self.BACKFILLED if c not in cvc.BUILTIN_SLASH_COMMANDS]
        assert missing == []

    def test_collision_warns_end_to_end(self, tmp_path: Path) -> None:
        """A plugin command named after a built-in draws the collision WARNING.

        Behavioural, through validate_command — a membership assertion alone
        would not prove the constant is consulted.
        """
        cmd = tmp_path / "plan.md"
        cmd.write_text("---\ndescription: Probe command shadowing a built-in name.\n---\nBody.\n")
        report = validate_command(cmd)
        assert any("built-in" in r.message.lower() for r in report.results)

    def test_control_non_builtin_is_silent(self, tmp_path: Path) -> None:
        """Control: a name no built-in uses draws no collision finding."""
        cmd = tmp_path / "zzz-not-a-builtin.md"
        cmd.write_text("---\ndescription: Probe command with a name no built-in uses.\n---\nBody.\n")
        report = validate_command(cmd)
        assert not any("built-in" in r.message.lower() for r in report.results)

    def test_control_legacy_names_retained(self) -> None:
        """Control: names absent from today's doc table were NOT dropped.

        A name leaving the table is not proof the runtime dropped it. Keeping a
        stale name only over-warns on a collision; dropping a live one
        under-warns — asymmetric, so retain (the MultiEdit/SlashCommand
        precedent in VALID_TOOLS).
        """
        for legacy in ("quit", "setup-token", "permission-mode", "less-permission-prompts"):
            assert legacy in cvc.BUILTIN_SLASH_COMMANDS

    def test_control_prior_entries_survived(self) -> None:
        """Control: the backfill appended — it did not replace the original 53."""
        assert {"clear", "resume", "compact", "login", "code-review", "fork"} <= cvc.BUILTIN_SLASH_COMMANDS


class TestVerifiedNonGaps:
    """Probed and found already correct — pinned so a later 'fix' cannot regress them."""

    def test_skill_openspec_fields_present(self) -> None:
        """`license` and `compatibility` were already accepted (no gap)."""
        assert {"license", "compatibility"} <= vsc.OPENSPEC_ALLOWED_FIELDS

    def test_compatibility_is_a_string_not_a_mapping(self) -> None:
        """skills.md:341 says a string of up to 500 chars — rejecting a dict is CORRECT.

        Pinned because the natural reading of "environment requirements" is a
        structured object, and a future sync could 'fix' CPV into accepting one.
        """
        assert vsc.MAX_COMPATIBILITY_LENGTH == 500
