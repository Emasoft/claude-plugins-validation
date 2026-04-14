"""Tests for scripts/setup_branch_rules_generic.py.

Verifies that the project-agnostic ruleset installer:
  - parses OWNER/REPO slugs
  - serializes BypassActor entries correctly
  - seeds only the admin role by default (no hardcoded GitHub App IDs —
    those cause HTTP 422 when the app isn't installed on the owner)
  - builds a ruleset payload that GitHub's API will actually accept
    (no automatic_copilot_code_review_enabled, no do_not_enforce_on_create)
  - merges + deduplicates bypass_actor lists
  - enforces --check as a required CLI argument (a ruleset with zero
    required status checks is functionally useless)
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

generic = importlib.import_module("scripts.setup_branch_rules_generic")


# ── parse_repo_slug ─────────────────────────────────────────────────

class TestParseRepoSlug:
    """Verifies the OWNER/REPO slug parser."""

    def test_parses_valid_slug(self):
        """Valid OWNER/REPO returns a tuple."""
        assert generic.parse_repo_slug("foo/bar") == ("foo", "bar")

    def test_rejects_missing_slash(self):
        """Missing slash raises SystemExit."""
        with pytest.raises(SystemExit):
            generic.parse_repo_slug("foo")

    def test_rejects_empty_owner(self):
        """Empty owner raises SystemExit."""
        with pytest.raises(SystemExit):
            generic.parse_repo_slug("/bar")

    def test_rejects_empty_repo(self):
        """Empty repo raises SystemExit."""
        with pytest.raises(SystemExit):
            generic.parse_repo_slug("foo/")


# ── Default bypass actors ─────────────────────────────────────────

class TestBuildDefaultBypassActors:
    """Verifies the admin-only default seed."""

    def test_includes_admin_role(self):
        """Default seed includes RepositoryRole id 5 (maintain)."""
        actors = generic.build_default_bypass_actors()
        role_ids = {a.actor_id for a in actors if a.actor_type == "RepositoryRole"}
        assert 5 in role_ids

    def test_contains_no_hardcoded_integrations(self):
        """Default seed MUST NOT contain any Integration app_ids.

        Hardcoding an app_id that the target owner doesn't have installed
        causes HTTP 422 'Actor X must be part of the ruleset source or owner
        organization'. The generic script leaves integrations to --add-bypass-app-id.
        """
        actors = generic.build_default_bypass_actors()
        integration_actors = [a for a in actors if a.actor_type == "Integration"]
        assert integration_actors == []

    def test_all_defaults_are_always_bypass(self):
        """Every default actor uses bypass_mode='always'."""
        actors = generic.build_default_bypass_actors()
        for a in actors:
            assert a.bypass_mode == "always"


# ── Merge bypass actors ─────────────────────────────────────────────

class TestMergeBypassActors:
    """Verifies the preserve-existing semantics."""

    def test_preserves_existing_entries(self):
        """Existing actors survive the merge."""
        existing = [generic.BypassActor(999, "Integration", "always")]
        additions = [generic.BypassActor(5, "RepositoryRole", "always")]
        merged = generic.merge_bypass_actors(existing, additions)
        ids = {(a.actor_type, a.actor_id) for a in merged}
        assert ("Integration", 999) in ids
        assert ("RepositoryRole", 5) in ids

    def test_dedupes_by_actor_type_and_id(self):
        """Collisions on (actor_type, actor_id) drop the addition."""
        existing = [generic.BypassActor(29110, "Integration", "pull_request")]
        additions = [generic.BypassActor(29110, "Integration", "always")]
        merged = generic.merge_bypass_actors(existing, additions)
        assert len(merged) == 1
        # Existing entry wins — user-downgraded bypass_mode preserved
        assert merged[0].bypass_mode == "pull_request"


# ── build_ruleset ───────────────────────────────────────────────────

class TestBuildRuleset:
    """Verifies the ruleset payload shape."""

    def test_uses_custom_ruleset_name(self):
        """--ruleset-name is honored (not hardcoded to 'branch-rules')."""
        ruleset = generic.build_ruleset("my-project-rules", ["CI / test"], [])
        assert ruleset["name"] == "my-project-rules"

    def test_targets_default_branch(self):
        """Rule applies to the default branch via ~DEFAULT_BRANCH."""
        ruleset = generic.build_ruleset("x", ["CI / test"], [])
        assert ruleset["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]

    def test_includes_deletion_block(self):
        """Rules include 'deletion' so the default branch can't be removed."""
        ruleset = generic.build_ruleset("x", ["CI / test"], [])
        types = {r["type"] for r in ruleset["rules"]}
        assert "deletion" in types

    def test_includes_non_fast_forward_block(self):
        """Rules include 'non_fast_forward' (blocks force-push)."""
        ruleset = generic.build_ruleset("x", ["CI / test"], [])
        types = {r["type"] for r in ruleset["rules"]}
        assert "non_fast_forward" in types

    def test_pull_request_review_count_zero(self):
        """No manual approval required — bots & admins can merge without review."""
        ruleset = generic.build_ruleset("x", ["CI / test"], [])
        pr = next(r for r in ruleset["rules"] if r["type"] == "pull_request")
        assert pr["parameters"]["required_approving_review_count"] == 0

    def test_pull_request_rule_omits_copilot_field(self):
        """pull_request parameters must NOT include automatic_copilot_code_review_enabled.

        That field causes 'Unexpected parameter' HTTP 422 on user-owned repos.
        """
        ruleset = generic.build_ruleset("x", ["CI / test"], [])
        pr = next(r for r in ruleset["rules"] if r["type"] == "pull_request")
        assert "automatic_copilot_code_review_enabled" not in pr["parameters"]

    def test_required_status_checks_includes_all_contexts(self):
        """Every --check context appears in required_status_checks."""
        contexts = ["CI / build", "CI / test", "CI / lint"]
        ruleset = generic.build_ruleset("x", contexts, [])
        status_rule = next(
            r for r in ruleset["rules"] if r["type"] == "required_status_checks"
        )
        ruleset_contexts = [
            c["context"] for c in status_rule["parameters"]["required_status_checks"]
        ]
        assert ruleset_contexts == contexts

    def test_required_status_checks_strict_false_for_automerge(self):
        """strict policy off so auto-merge doesn't force a rebase loop."""
        ruleset = generic.build_ruleset("x", ["CI / test"], [])
        status = next(
            r for r in ruleset["rules"] if r["type"] == "required_status_checks"
        )
        assert status["parameters"]["strict_required_status_checks_policy"] is False

    def test_required_status_checks_omits_do_not_enforce_on_create(self):
        """required_status_checks must NOT include do_not_enforce_on_create.

        That field is rejected as unexpected on user-owned repos.
        """
        ruleset = generic.build_ruleset("x", ["CI / test"], [])
        status = next(
            r for r in ruleset["rules"] if r["type"] == "required_status_checks"
        )
        assert "do_not_enforce_on_create" not in status["parameters"]

    def test_enforcement_is_active(self):
        """Ruleset is created in 'active' mode (not 'evaluate')."""
        ruleset = generic.build_ruleset("x", ["CI / test"], [])
        assert ruleset["enforcement"] == "active"


# ── CLI ────────────────────────────────────────────────────────────

class TestCli:
    """Verifies CLI parsing and required-arg enforcement."""

    def test_help_prints_cleanly(self, capsys):
        """--help exits 0 and shows usage + examples."""
        with mock.patch.object(sys, "argv", ["branch-rules-install", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                generic.parse_args()
            assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Target repo slug" in out
        assert "--check" in out

    def test_repo_is_required(self):
        """Missing positional repo raises SystemExit."""
        with mock.patch.object(sys, "argv", ["branch-rules-install"]):
            with pytest.raises(SystemExit):
                generic.parse_args()

    def test_check_is_repeatable(self):
        """--check can be passed multiple times."""
        with mock.patch.object(
            sys, "argv",
            ["branch-rules-install", "Emasoft/test",
             "--check", "CI / build",
             "--check", "CI / test"],
        ):
            args = generic.parse_args()
        assert args.check_contexts == ["CI / build", "CI / test"]

    def test_ruleset_name_defaults_to_branch_rules(self):
        """Default --ruleset-name is 'branch-rules'."""
        with mock.patch.object(
            sys, "argv",
            ["branch-rules-install", "Emasoft/test", "--check", "CI / test"],
        ):
            args = generic.parse_args()
        assert args.ruleset_name == "branch-rules"

    def test_ruleset_name_overridable(self):
        """--ruleset-name can be set to a custom value."""
        with mock.patch.object(
            sys, "argv",
            ["branch-rules-install", "Emasoft/test",
             "--check", "CI / test",
             "--ruleset-name", "my-custom-rules"],
        ):
            args = generic.parse_args()
        assert args.ruleset_name == "my-custom-rules"

    def test_add_bypass_app_id_repeatable(self):
        """--add-bypass-app-id accepts multiple integer values."""
        with mock.patch.object(
            sys, "argv",
            ["branch-rules-install", "Emasoft/test", "--check", "CI / test",
             "--add-bypass-app-id", "29110",
             "--add-bypass-app-id", "852577"],
        ):
            args = generic.parse_args()
        assert args.add_bypass_app_id == [29110, 852577]

    def test_dry_run_flag_parsed(self):
        """--dry-run sets args.dry_run=True."""
        with mock.patch.object(
            sys, "argv",
            ["branch-rules-install", "Emasoft/test",
             "--check", "CI / test", "--dry-run"],
        ):
            args = generic.parse_args()
        assert args.dry_run is True
