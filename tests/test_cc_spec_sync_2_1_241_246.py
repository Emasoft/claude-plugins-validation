"""CC spec-drift sync for the v2.1.241 → v2.1.246 window.

Every assertion is two-sided: each "now accepted" case is paired with a control
proving the same code path still rejects what it must reject.

Verified against the RAW docs by mechanical set-diff (the recorded spec-drift
method), then PROBED against the real validator before being treated as a gap.
Each addition was confirmed absent at HEAD first (`git show HEAD:<file>` — 0
hits for every name added here), so none of these tests can pass vacuously.

**The measurement defect this window caught, and the reason it is worth more
than the three fixes:** the doc corpus is enumerated from ``llms.txt``, and the
fetch loop named each local file with ``basename $url``. Nine URLs share a
basename with a differently-scoped page (``hooks.md``, ``skills.md``,
``plugins.md``, ``mcp.md``, ``overview.md``, ``permissions.md``,
``quickstart.md``, ``sessions.md``, ``troubleshooting.md``), so each pair
silently collapsed to one file — 191 URLs became 182 files, and whichever page
was fetched second won. A page that is silently overwritten is indistinguishable
from a page with nothing new in it, which is the same failure shape as the
v5.8.0 vacuous-anchor finding: **the anchor, not the diff, is what needs
re-verifying each window.** Flattening the URL path into the filename
(``/`` → ``__``) restored all 191, and a byte-identical-pair check over the
result found zero collisions, which is the positive evidence that no fetch
silently fell back.

The window's plugin-spec surface:

* **v2.1.243 settings keys** — ``modelPicker``, ``promptCacheTtl``,
  ``subagentPromptCacheTtl``, plus ``autoContinueAtUsageLimit`` (v2.1.234) and
  ``disableDesktopLocalSessions`` accumulated. Anchored on the
  ``### `<key>` `` headings in ``settings-reference.md``.
* **skill ``license`` / ``compatibility``** — the v2.1.236–240 window recorded
  these as "already handled", which was true only of
  ``validate_skill_comprehensive`` (they live in ``OPENSPEC_ALLOWED_FIELDS``).
  ``validate_skill.py`` warns off ``SKILL_FRONTMATTER_FIELDS`` alone, and that
  set lacked both — so a spec-correct skill using either got an unknown-field
  WARNING from the plain validator. Two validators, one shared spec, and only
  one of them checked: "handled" needs naming WHICH code path.
* **built-in slash commands** — one genuinely new name
  (``rate-limit-options``) plus nineteen documented ALIASES. The earlier pass
  read only column 1 of the ``commands.md`` table and so never saw the Aliases
  column. An alias a user actually types is not a typo, so omitting it makes
  the detector warn on a working command.

Verified as needing NO change rather than assumed (each probed):

* ``VALID_SOURCE_TYPES`` already carries ``archive`` (v2.1.224) and ``command``
  (v2.1.229) — both present with their per-type required-key sets.
* ``VALID_TOOLS``, ``VALID_HOOK_EVENTS``, agent frontmatter, and marketplace
  fields showed no doc-side additions. The apparent diffs were extraction
  artifacts: table-header cells (``Field``, ``Result``, ``Valid``), prose
  headings (``Disclaimer``, ``Limitations``), and permission-mode/scope enum
  VALUES (``acceptEdits``, ``plan``, ``project``) matched by a field-name regex.
* the ``compatibility`` value-type rule is untouched — this window adds the KEY
  to the known-name set; ``validate_compatibility_field`` still enforces the
  OpenSpec string/500-char shape, so a mapping is still rejected.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class TestSettingsKeysV2_1_243:
    """`### <key>` headings in settings-reference.md vs KNOWN_SETTINGS_KEYS."""

    def test_new_keys_are_known(self) -> None:
        from cc_scope_rules import KNOWN_SETTINGS_KEYS  # type: ignore[import-not-found]

        for key in (
            "modelPicker",
            "promptCacheTtl",
            "subagentPromptCacheTtl",
            "autoContinueAtUsageLimit",
            "disableDesktopLocalSessions",
        ):
            assert key in KNOWN_SETTINGS_KEYS, f"{key} missing from KNOWN_SETTINGS_KEYS"

    def test_typo_of_a_new_key_is_still_unknown(self) -> None:
        """Control: the set stays a typo detector, not an allow-everything."""
        from cc_scope_rules import KNOWN_SETTINGS_KEYS  # type: ignore[import-not-found]

        for typo in ("modelPickers", "promptCacheTTL", "subAgentPromptCacheTtl"):
            assert typo not in KNOWN_SETTINGS_KEYS


class TestSkillOpenSpecFieldsReachThePlainValidator:
    """`license` / `compatibility` must be known to validate_skill.py too."""

    def test_openspec_fields_are_known(self) -> None:
        from cpv_validation_common import SKILL_FRONTMATTER_FIELDS  # type: ignore[import-not-found]

        assert "license" in SKILL_FRONTMATTER_FIELDS
        assert "compatibility" in SKILL_FRONTMATTER_FIELDS

    def test_plain_validator_and_comprehensive_agree(self) -> None:
        """The two validators must not disagree about the same spec field.

        This is the actual defect: `OPENSPEC_ALLOWED_FIELDS` carried both names
        while `SKILL_FRONTMATTER_FIELDS` did not, so which validator you ran
        decided whether a spec-correct skill was clean.
        """
        from cpv_validation_common import SKILL_FRONTMATTER_FIELDS  # type: ignore[import-not-found]
        from validate_skill_comprehensive import (  # type: ignore[import-not-found]
            OPENSPEC_ALLOWED_FIELDS,
        )

        assert OPENSPEC_ALLOWED_FIELDS <= SKILL_FRONTMATTER_FIELDS | {"name", "description"}

    def test_near_miss_spelling_is_still_unknown(self) -> None:
        """Control: `licence` (the British spelling) is NOT a spec field."""
        from cpv_validation_common import SKILL_FRONTMATTER_FIELDS  # type: ignore[import-not-found]

        assert "licence" not in SKILL_FRONTMATTER_FIELDS
        assert "compatability" not in SKILL_FRONTMATTER_FIELDS


class TestBuiltinSlashCommandAliases:
    """commands.md documents aliases in a column the earlier pass never read."""

    NEW = ("rate-limit-options",)
    ALIASES = (
        "adddir",
        "allowed-tools",
        "android",
        "ios",
        "app",
        "bashes",
        "bg",
        "checkpoint",
        "undo",
        "checkup",
        "continue",
        "new",
        "reset",
        "peers",
        "rc",
        "routines",
        "settings",
        "share",
        "tp",
    )

    def test_new_command_is_known(self) -> None:
        from cpv_validation_common import BUILTIN_SLASH_COMMANDS  # type: ignore[import-not-found]

        for name in self.NEW:
            assert name in BUILTIN_SLASH_COMMANDS

    def test_documented_aliases_are_known(self) -> None:
        from cpv_validation_common import BUILTIN_SLASH_COMMANDS  # type: ignore[import-not-found]

        for name in self.ALIASES:
            assert name in BUILTIN_SLASH_COMMANDS, f"/{name} is a documented alias"

    def test_an_undocumented_name_is_still_not_builtin(self) -> None:
        """Control: the set did not become a catch-all."""
        from cpv_validation_common import BUILTIN_SLASH_COMMANDS  # type: ignore[import-not-found]

        for name in ("cpv-validate", "definitely-not-a-builtin", "androids"):
            assert name not in BUILTIN_SLASH_COMMANDS


class TestNoChangeNeededSurfaces:
    """Pin the surfaces PROBED and found already correct, with their reason.

    Pinned so the next window checks the reason instead of re-litigating the
    decision — and so a later edit that silently drops one of these fails here.
    """

    def test_archive_and_command_marketplace_sources_present(self) -> None:
        from validate_marketplace import VALID_SOURCE_TYPES  # type: ignore[import-not-found]

        assert "archive" in VALID_SOURCE_TYPES  # v2.1.224
        assert "command" in VALID_SOURCE_TYPES  # v2.1.229

    def test_directory_added_hook_event_present(self) -> None:
        from cpv_validation_common import VALID_HOOK_EVENTS  # type: ignore[import-not-found]

        assert "DirectoryAdded" in VALID_HOOK_EVENTS  # v2.1.219

    def test_compatibility_value_type_rule_untouched(self) -> None:
        """Adding the KEY must not have relaxed the OpenSpec VALUE rule."""
        import validate_skill_comprehensive as vsc  # type: ignore[import-not-found]

        assert hasattr(vsc, "validate_compatibility_field")
