"""Tests for scripts/setup_branch_rules.py.

Verifies ruleset construction, bypass_actor merging, dry-run output, and CLI
argument handling. No network calls — gh subprocess is not mocked; instead
the pure functions are tested directly.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

setup_branch_rules = importlib.import_module("scripts.setup_branch_rules")


# ── parse_repo_slug ─────────────────────────────────────────────────

class TestParseRepoSlug:
    """Verifies the OWNER/REPO slug parser."""

    def test_parses_valid_slug(self):
        """Valid OWNER/REPO slug returns a (owner, repo) tuple."""
        assert setup_branch_rules.parse_repo_slug("foo/bar") == ("foo", "bar")

    def test_rejects_missing_slash(self):
        """Slug without a slash raises SystemExit."""
        with pytest.raises(SystemExit):
            setup_branch_rules.parse_repo_slug("foo")

    def test_rejects_empty_owner(self):
        """Empty owner side raises SystemExit."""
        with pytest.raises(SystemExit):
            setup_branch_rules.parse_repo_slug("/bar")

    def test_rejects_empty_repo(self):
        """Empty repo side raises SystemExit."""
        with pytest.raises(SystemExit):
            setup_branch_rules.parse_repo_slug("foo/")


# ── BypassActor dataclass ─────────────────────────────────────────

class TestBypassActor:
    """Verifies the BypassActor dataclass serialization."""

    def test_to_dict_has_expected_keys(self):
        """BypassActor.to_dict returns all three required fields."""
        actor = setup_branch_rules.BypassActor(
            actor_id=29110,
            actor_type="Integration",
            bypass_mode="always",
        )
        d = actor.to_dict()
        assert d == {
            "actor_id": 29110,
            "actor_type": "Integration",
            "bypass_mode": "always",
        }

    def test_deploy_key_has_null_actor_id(self):
        """DeployKey actors allow actor_id=None (per GitHub API shape)."""
        actor = setup_branch_rules.BypassActor(
            actor_id=None,
            actor_type="DeployKey",
            bypass_mode="always",
        )
        assert actor.to_dict()["actor_id"] is None


# ── build_default_bypass_actors ─────────────────────────────────────

class TestBuildDefaultBypassActors:
    """Verifies default bypass_actors seed for new rulesets."""

    def test_includes_admin_role(self):
        """Default seed includes RepositoryRole id 5 (maintain)."""
        actors = setup_branch_rules.build_default_bypass_actors()
        role_ids = {a.actor_id for a in actors if a.actor_type == "RepositoryRole"}
        assert 5 in role_ids

    def test_includes_dependabot(self):
        """Default seed includes dependabot app_id 29110."""
        actors = setup_branch_rules.build_default_bypass_actors()
        app_ids = {a.actor_id for a in actors if a.actor_type == "Integration"}
        assert 29110 in app_ids

    def test_includes_github_actions(self):
        """Default seed includes github-actions app_id 15368."""
        actors = setup_branch_rules.build_default_bypass_actors()
        app_ids = {a.actor_id for a in actors if a.actor_type == "Integration"}
        assert 15368 in app_ids

    def test_all_defaults_are_always_bypass(self):
        """Every default actor uses bypass_mode='always' for friction-free bot merges."""
        actors = setup_branch_rules.build_default_bypass_actors()
        for a in actors:
            assert a.bypass_mode == "always"


# ── merge_bypass_actors ─────────────────────────────────────────────

class TestMergeBypassActors:
    """Verifies preservation + deduplication behavior for bypass_actor merge."""

    def test_preserves_existing_entries(self):
        """Existing bypass_actors survive the merge."""
        existing = [
            setup_branch_rules.BypassActor(999, "Integration", "always"),
        ]
        additions = [
            setup_branch_rules.BypassActor(29110, "Integration", "always"),
        ]
        merged = setup_branch_rules.merge_bypass_actors(existing, additions)
        app_ids = {a.actor_id for a in merged}
        assert 999 in app_ids
        assert 29110 in app_ids

    def test_deduplicates_by_actor_type_and_id(self):
        """Collisions on (actor_type, actor_id) drop the addition."""
        existing = [
            setup_branch_rules.BypassActor(29110, "Integration", "pull_request"),
        ]
        additions = [
            setup_branch_rules.BypassActor(29110, "Integration", "always"),
        ]
        merged = setup_branch_rules.merge_bypass_actors(existing, additions)
        assert len(merged) == 1
        # Existing entry wins — user-configured bypass_mode is preserved
        assert merged[0].bypass_mode == "pull_request"

    def test_handles_deploy_key_with_null_actor_id(self):
        """DeployKey entries with actor_id=None deduplicate correctly."""
        existing = [
            setup_branch_rules.BypassActor(None, "DeployKey", "always"),
        ]
        additions = [
            setup_branch_rules.BypassActor(None, "DeployKey", "always"),
        ]
        merged = setup_branch_rules.merge_bypass_actors(existing, additions)
        assert len(merged) == 1

    def test_different_actor_types_do_not_collide(self):
        """Same actor_id across different actor_types stays separate."""
        existing = [
            setup_branch_rules.BypassActor(5, "RepositoryRole", "always"),
        ]
        additions = [
            setup_branch_rules.BypassActor(5, "Integration", "always"),
        ]
        merged = setup_branch_rules.merge_bypass_actors(existing, additions)
        assert len(merged) == 2


# ── build_ruleset ───────────────────────────────────────────────────

class TestBuildRuleset:
    """Verifies the ruleset payload shape for GitHub Rulesets API."""

    def test_has_correct_name(self):
        """Ruleset is always named 'cpv-branch-rules' for idempotent identification."""
        ruleset = setup_branch_rules.build_ruleset([], [])
        assert ruleset["name"] == "cpv-branch-rules"

    def test_targets_default_branch(self):
        """Ruleset applies to the default branch via ~DEFAULT_BRANCH pseudo-ref."""
        ruleset = setup_branch_rules.build_ruleset([], [])
        assert ruleset["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]

    def test_blocks_deletion(self):
        """Ruleset rules include 'deletion' to prevent default-branch removal."""
        ruleset = setup_branch_rules.build_ruleset([], [])
        rule_types = {r["type"] for r in ruleset["rules"]}
        assert "deletion" in rule_types

    def test_blocks_force_push(self):
        """Ruleset rules include 'non_fast_forward' to prevent force-push."""
        ruleset = setup_branch_rules.build_ruleset([], [])
        rule_types = {r["type"] for r in ruleset["rules"]}
        assert "non_fast_forward" in rule_types

    def test_requires_pull_request(self):
        """Ruleset rules include 'pull_request' so every change goes via PR."""
        ruleset = setup_branch_rules.build_ruleset([], [])
        pr_rules = [r for r in ruleset["rules"] if r["type"] == "pull_request"]
        assert len(pr_rules) == 1

    def test_pull_request_review_count_is_zero(self):
        """No manual approval required — bots and admins can merge without review."""
        ruleset = setup_branch_rules.build_ruleset([], [])
        pr_rule = next(r for r in ruleset["rules"] if r["type"] == "pull_request")
        assert pr_rule["parameters"]["required_approving_review_count"] == 0

    def test_required_status_checks_contexts_included(self):
        """Each provided check context appears in required_status_checks."""
        contexts = ["CI / Lint", "CI / Validate", "CI / Test"]
        ruleset = setup_branch_rules.build_ruleset(contexts, [])
        status_rule = next(
            r for r in ruleset["rules"] if r["type"] == "required_status_checks"
        )
        ruleset_contexts = [
            c["context"] for c in status_rule["parameters"]["required_status_checks"]
        ]
        assert ruleset_contexts == contexts

    def test_strict_policy_disabled_for_automerge(self):
        """strict_required_status_checks_policy is false so auto-merge works without rebase loops."""
        ruleset = setup_branch_rules.build_ruleset([], [])
        status_rule = next(
            r for r in ruleset["rules"] if r["type"] == "required_status_checks"
        )
        assert status_rule["parameters"]["strict_required_status_checks_policy"] is False

    def test_bypass_actors_are_serialized(self):
        """BypassActor objects are converted to dicts in the final ruleset payload."""
        actors = [
            setup_branch_rules.BypassActor(29110, "Integration", "always"),
        ]
        ruleset = setup_branch_rules.build_ruleset([], actors)
        assert ruleset["bypass_actors"] == [
            {"actor_id": 29110, "actor_type": "Integration", "bypass_mode": "always"}
        ]

    def test_enforcement_active(self):
        """Ruleset is created in 'active' enforcement mode (not 'evaluate')."""
        ruleset = setup_branch_rules.build_ruleset([], [])
        assert ruleset["enforcement"] == "active"


# ── CLI integration ────────────────────────────────────────────────

class TestRepoTypeDetection:
    """Verifies plugin/marketplace auto-detection and default-selection logic."""

    def test_default_plugin_contexts_has_three_checks(self):
        """Plugin default requires the three parallel jobs from the consolidated CI."""
        ctxs = setup_branch_rules.default_check_contexts_for("plugin")
        assert ctxs == ["CI / Lint", "CI / Validate", "CI / Test"]

    def test_default_marketplace_context_has_single_check(self):
        """Marketplace default requires the single validate job from Marketplace Validation workflow."""
        ctxs = setup_branch_rules.default_check_contexts_for("marketplace")
        assert ctxs == ["Marketplace Validation / Validate"]

    def test_unknown_type_falls_back_to_plugin(self):
        """Unknown repo type falls back to plugin defaults (the common case)."""
        ctxs = setup_branch_rules.default_check_contexts_for("unknown")
        assert ctxs == ["CI / Lint", "CI / Validate", "CI / Test"]

    def test_returned_list_is_a_copy_not_the_module_constant(self):
        """default_check_contexts_for returns a fresh list so mutating it does not bleed into defaults."""
        ctxs = setup_branch_rules.default_check_contexts_for("plugin")
        ctxs.append("CI / Something")
        assert "CI / Something" not in setup_branch_rules.DEFAULT_PLUGIN_CHECK_CONTEXTS

    def test_detect_repo_type_is_callable(self):
        """detect_repo_type is exposed as a module-level callable (used by main)."""
        assert callable(setup_branch_rules.detect_repo_type)

    def test_plugin_and_marketplace_constants_are_disjoint(self):
        """Plugin and marketplace check-context constants have no overlap."""
        plugin = set(setup_branch_rules.DEFAULT_PLUGIN_CHECK_CONTEXTS)
        marketplace = set(setup_branch_rules.DEFAULT_MARKETPLACE_CHECK_CONTEXTS)
        assert plugin.isdisjoint(marketplace)


class TestCli:
    """Verifies CLI argument parsing and help output without network calls."""

    def test_help_shows_usage(self, capsys):
        """--help prints usage and exits cleanly (exit 0)."""
        with mock.patch.object(sys, "argv", ["setup_branch_rules.py", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                setup_branch_rules.parse_args()
            assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Target repo slug" in out

    def test_repo_slug_is_required(self):
        """CLI requires a positional repo slug."""
        with mock.patch.object(sys, "argv", ["setup_branch_rules.py"]):
            with pytest.raises(SystemExit):
                setup_branch_rules.parse_args()

    def test_dry_run_flag_parsed(self):
        """--dry-run flag is recognized and sets args.dry_run True."""
        with mock.patch.object(
            sys, "argv", ["setup_branch_rules.py", "Emasoft/test", "--dry-run"]
        ):
            args = setup_branch_rules.parse_args()
        assert args.dry_run is True

    def test_add_bypass_app_id_accepts_multiple(self):
        """--add-bypass-app-id can be passed multiple times."""
        with mock.patch.object(
            sys, "argv",
            ["setup_branch_rules.py", "Emasoft/test",
             "--add-bypass-app-id", "15368",
             "--add-bypass-app-id", "29110"],
        ):
            args = setup_branch_rules.parse_args()
        assert args.add_bypass_app_id == [15368, 29110]

    def test_check_context_repeatable(self):
        """--check-context can be passed multiple times to override defaults."""
        with mock.patch.object(
            sys, "argv",
            ["setup_branch_rules.py", "Emasoft/test",
             "--check-context", "X / A",
             "--check-context", "X / B"],
        ):
            args = setup_branch_rules.parse_args()
        assert args.check_context == ["X / A", "X / B"]

    def test_reset_bypass_flag(self):
        """--reset-bypass flag sets args.reset_bypass True."""
        with mock.patch.object(
            sys, "argv", ["setup_branch_rules.py", "Emasoft/test", "--reset-bypass"]
        ):
            args = setup_branch_rules.parse_args()
        assert args.reset_bypass is True
