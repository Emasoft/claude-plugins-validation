#!/usr/bin/env python3
"""CC docs-drift sync (2026-09-28) — five allowlist-widening FP reductions.

Two-sided regression locks for the 2026-09-28 docs-drift scout findings
(reports_dev/cc-spec-drift-scout-20260928.md). Each acceptance test is paired
with a positive control proving the same code path still rejects a bogus
sibling — the allowlist widened, the detector did not disable.

F1  — Notification matcher values: elicitation_url_dialog, quota_auto_resume_fired,
      quota_auto_resume_stale, quota_auto_resume_disabled (hooks.md ~L2255-2270)
      → validate_hook.COMMON_NOTIFICATION_TYPES.
F2  — Reserved marketplace names: anthropic-plugin-directory, claude-plugin-directory
      (marketplace-reference.md "Reserved names", line ~42)
      → validate_marketplace.RESERVED_MARKETPLACE_NAMES (+ standardize union parity).
F3  — Marketplace top-level field: forceRemoveDeletedPlugins boolean
      (marketplace-reference.md "Top-level fields", line ~68)
      → validate_marketplace.OPTIONAL_MARKETPLACE_TOP_LEVEL_FIELDS.
F15 — Plugin manifest field: workflows (manifest-reference.md "Fields" table)
      → validate_marketplace.OPTIONAL_PLUGIN_FIELDS (accepts it at the
      marketplace-entry level; validate_plugin.py already path-checks it).
F5  — Built-in agent types: claude, claude-code-guide (sub-agents.md "Other"
      built-ins table) → cpv_validation_common.BUILTIN_AGENT_TYPES +
      validate_xref.BUILTIN_AGENTS (ghost-dispatch FP direction).
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import BUILTIN_AGENT_TYPES, ValidationReport
from validate_hook import validate_matcher
from validate_marketplace import (
    _KNOWN_MARKETPLACE_ENTRY_FIELDS,
    OPTIONAL_MARKETPLACE_TOP_LEVEL_FIELDS,
    OPTIONAL_PLUGIN_FIELDS,
    RESERVED_MARKETPLACE_NAMES,
    validate_marketplace_name,
)
from validate_xref import BUILTIN_AGENTS, _resolve_dispatch_ref


# ---------------------------------------------------------------------------
# F1 — Notification matcher values
# ---------------------------------------------------------------------------
class TestF1NotificationMatcherValues:
    """The four documented Notification values no longer draw a spurious INFO."""

    def test_elicitation_url_dialog_accepted(self) -> None:
        report = ValidationReport()
        validate_matcher("elicitation_url_dialog", "Notification", report)
        assert not any("is not a known" in r.message for r in report.results)

    def test_quota_auto_resume_fired_accepted(self) -> None:
        report = ValidationReport()
        validate_matcher("quota_auto_resume_fired", "Notification", report)
        assert not any("is not a known" in r.message for r in report.results)

    def test_quota_auto_resume_stale_accepted(self) -> None:
        report = ValidationReport()
        validate_matcher("quota_auto_resume_stale", "Notification", report)
        assert not any("is not a known" in r.message for r in report.results)

    def test_quota_auto_resume_disabled_accepted(self) -> None:
        report = ValidationReport()
        validate_matcher("quota_auto_resume_disabled", "Notification", report)
        assert not any("is not a known" in r.message for r in report.results)

    def test_preexisting_notification_values_retained(self) -> None:
        """The 8 previously-known values were not dropped by the addition."""
        from validate_hook import COMMON_NOTIFICATION_TYPES

        for name in (
            "permission_prompt",
            "idle_prompt",
            "auth_success",
            "elicitation_dialog",
            "elicitation_complete",
            "elicitation_response",
            "agent_needs_input",
            "agent_completed",
        ):
            assert name in COMMON_NOTIFICATION_TYPES, name

    # positive control — a bogus sibling of the quota_auto_resume_ family
    def test_bogus_quota_sibling_still_unknown(self) -> None:
        """`quota_auto_resume_bogus` is NOT a doc value and must still draw the INFO."""
        report = ValidationReport()
        validate_matcher("quota_auto_resume_bogus", "Notification", report)
        assert any("is not a known" in r.message for r in report.results if r.level == "INFO")


# ---------------------------------------------------------------------------
# F2 — reserved marketplace names
# ---------------------------------------------------------------------------
class TestF2ReservedMarketplaceNames:
    """The two plugin-directory names are reserved at CRITICAL."""

    def test_anthropic_plugin_directory_reserved(self) -> None:
        assert "anthropic-plugin-directory" in RESERVED_MARKETPLACE_NAMES
        results = validate_marketplace_name("anthropic-plugin-directory", "marketplace.json")
        assert any(r.level == "CRITICAL" and "reserved" in r.message for r in results)

    def test_claude_plugin_directory_reserved(self) -> None:
        assert "claude-plugin-directory" in RESERVED_MARKETPLACE_NAMES
        results = validate_marketplace_name("claude-plugin-directory", "marketplace.json")
        assert any(r.level == "CRITICAL" and "reserved" in r.message for r in results)

    def test_preexisting_reserved_names_retained(self) -> None:
        for name in ("claude-tag-plugins", "first-party-plugins", "healthcare"):
            assert name in RESERVED_MARKETPLACE_NAMES, name

    # positive controls — a genuinely-unreserved name is clean of the reserved
    # error AND the impersonation net did not silently widen
    def test_unreserved_community_name_still_allowed(self) -> None:
        results = validate_marketplace_name("emasoft-plugins", "marketplace.json")
        assert not any("reserved and cannot be used" in r.message for r in results)

    def test_bogus_directory_sibling_not_reserved(self) -> None:
        """`my-plugin-directory` is a common community name — must NOT be reserved."""
        assert "my-plugin-directory" not in RESERVED_MARKETPLACE_NAMES
        results = validate_marketplace_name("my-plugin-directory", "marketplace.json")
        assert not any("reserved" in r.message for r in results)

    def test_standardize_union_covers_the_new_names(self) -> None:
        """standardize_marketplace unions the spec set — it must not lose names (parity)."""
        from standardize_marketplace import RESERVED_MARKETPLACE_NAMES as SM_RESERVED

        missing = set(RESERVED_MARKETPLACE_NAMES) - set(SM_RESERVED)
        assert missing == set(), f"standardize lost spec-reserved names: {sorted(missing)}"


# ---------------------------------------------------------------------------
# F3 — marketplace top-level field forceRemoveDeletedPlugins
# ---------------------------------------------------------------------------
class TestF3ForceRemoveDeletedPlugins:
    """forceRemoveDeletedPlugins is a documented top-level field."""

    def test_field_in_top_level_allowlist(self) -> None:
        assert "forceRemoveDeletedPlugins" in OPTIONAL_MARKETPLACE_TOP_LEVEL_FIELDS

    def test_preexisting_top_level_fields_retained(self) -> None:
        for name in ("$schema", "metadata", "renames", "owner", "plugins", "name"):
            assert name in OPTIONAL_MARKETPLACE_TOP_LEVEL_FIELDS, name

    # positive control — a typo'd sibling stays outside the allowlist
    def test_bogus_top_level_field_not_accepted(self) -> None:
        assert "forceRemoveDeletedPlugin" not in OPTIONAL_MARKETPLACE_TOP_LEVEL_FIELDS
        assert "forceRemovePlugins" not in OPTIONAL_MARKETPLACE_TOP_LEVEL_FIELDS


# ---------------------------------------------------------------------------
# F15 — plugin manifest field workflows
# ---------------------------------------------------------------------------
class TestF15WorkflowsManifestField:
    """`workflows` is a documented plugin.json / marketplace-entry field."""

    def test_workflows_in_entry_allowlist(self) -> None:
        assert "workflows" in OPTIONAL_PLUGIN_FIELDS
        assert "workflows" in _KNOWN_MARKETPLACE_ENTRY_FIELDS

    def test_workflows_entry_draws_no_unknown_field_major(self) -> None:
        """A marketplace entry declaring `workflows` must not MAJOR as unknown."""
        from validate_marketplace import _validate_known_entry_fields

        plugin = {"name": "p", "source": "./p", "workflows": "./workflows"}
        results = _validate_known_entry_fields(plugin, "p", "marketplace.json")
        assert not any("RC-MKPL-UNKNOWN-FIELD" in r.message for r in results)

    # positive control — a typo'd sibling still MAJORs through the same path
    def test_bogus_entry_field_still_major(self) -> None:
        from validate_marketplace import _validate_known_entry_fields

        plugin = {"name": "p", "source": "./p", "workflowsDir": "./workflows"}
        results = _validate_known_entry_fields(plugin, "p", "marketplace.json")
        assert any("RC-MKPL-UNKNOWN-FIELD" in r.message for r in results)


# ---------------------------------------------------------------------------
# F5 — built-in agent types
# ---------------------------------------------------------------------------
class TestF5BuiltinAgentTypes:
    """`claude` and `claude-code-guide` are dispatchable built-ins (sub-agents.md)."""

    def test_claude_in_builtin_agent_types(self) -> None:
        assert "claude" in BUILTIN_AGENT_TYPES

    def test_claude_code_guide_in_builtin_agent_types(self) -> None:
        assert "claude-code-guide" in BUILTIN_AGENT_TYPES

    def test_display_form_retained(self) -> None:
        """The existing display form is kept — existing consumers rely on it."""
        assert "Claude Code Guide" in BUILTIN_AGENT_TYPES

    def test_preexisting_builtins_retained(self) -> None:
        for name in ("Explore", "Plan", "general-purpose", "statusline-setup", "fork"):
            assert name in BUILTIN_AGENT_TYPES, name

    def test_xref_builtin_agents_cover_both(self) -> None:
        assert "claude" in BUILTIN_AGENTS
        assert "claude-code-guide" in BUILTIN_AGENTS

    def test_dispatch_to_claude_resolves_not_ghost(self) -> None:
        """A dispatch to the catch-all `claude` built-in is not a ghost dispatch."""
        status, _ = _resolve_dispatch_ref("claude", set())
        assert status == "ok"

    def test_dispatch_to_claude_code_guide_resolves_not_ghost(self) -> None:
        status, _ = _resolve_dispatch_ref("claude-code-guide", set())
        assert status == "ok"

    # positive controls — near-miss siblings of the new names stay ghosts
    def test_bogus_agent_type_still_ghost(self) -> None:
        """`subagent_typo` and near-misses of the new builtins must still ghost."""
        assert _resolve_dispatch_ref("subagent_typo", set())[0] == "ghost"
        assert _resolve_dispatch_ref("claude-guide", set())[0] == "ghost"
        assert _resolve_dispatch_ref("claude-code", set())[0] == "ghost"
