"""Issue #203 — cpv-setup-branch-rules must target the RATIFIED baseline.

The reported defect had two halves, and the second is the dangerous one:

  1. The command hardcoded a ruleset named `cpv-branch-rules` and CREATED it
     beside the ratified `baseline-history-protect` + `baseline-pr-and-checks`
     pair instead of bringing that pair to spec.
  2. `fetch_legacy_protection_rulesets()` classified every non-CPV ruleset
     whose rules intersected {pull_request, required_status_checks,
     required_signatures, code_quality} as "legacy", and the caller printed
     `gh api --method DELETE .../rulesets/<id>` for each. `baseline-pr-and-checks`
     always intersects, so the DELETE was aimed at the COMPLIANT configuration;
     `baseline-history-protect` does not intersect and survived, leaving a repo
     that still LOOKS protected with its only merge gate deleted.

Every test here is two-sided: the ratified path is asserted to do the right
thing AND the wrong thing is asserted to be absent (no parallel ruleset, no
DELETE aimed at a baseline, no write at all when the repo could not be read).

NO NETWORK. `setup_branch_rules.run` — the single subprocess entry point — is
replaced by a recorded fake, so no `gh` call ever leaves the process and no
repo's settings can be touched by this suite.
"""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

sbr = importlib.import_module("scripts.setup_branch_rules")

HISTORY = sbr.BASELINE_HISTORY_PROTECT_NAME
PR_CHECKS = sbr.BASELINE_PR_AND_CHECKS_NAME
LEGACY = sbr.LEGACY_RULESET_NAME


# ── Fixtures: ruleset bodies shaped like the GitHub API returns them ────────


def _history_protect_body(ruleset_id: int = 101, *, rules: list[dict] | None = None) -> dict:
    return {
        "id": ruleset_id,
        "name": HISTORY,
        "target": "branch",
        "enforcement": "active",
        "rules": rules if rules is not None else [{"type": "deletion"}, {"type": "non_fast_forward"}],
        "bypass_actors": [],
    }


def _pr_and_checks_body(
    ruleset_id: int = 102,
    *,
    bypass_actors: list[dict] | None = None,
    contexts: list[str] | None = None,
) -> dict:
    return {
        "id": ruleset_id,
        "name": PR_CHECKS,
        "target": "branch",
        "enforcement": "active",
        "rules": [
            {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": c} for c in (contexts or ["Validate"])],
                },
            },
        ],
        "bypass_actors": (
            bypass_actors
            if bypass_actors is not None
            else [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
        ),
    }


def _tag_protect_body(ruleset_id: int = 103) -> dict:
    """A ratified baseline this file has never heard of, on a tag target."""
    return {
        "id": ruleset_id,
        "name": "baseline-tag-protect",
        "target": "tag",
        "enforcement": "active",
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        "bypass_actors": [],
    }


def _legacy_cpv_body(ruleset_id: int = 200, *, bypass_actors: list[dict] | None = None) -> dict:
    return {
        "id": ruleset_id,
        "name": LEGACY,
        "target": "branch",
        "enforcement": "active",
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
            {"type": "required_status_checks", "parameters": {"required_status_checks": []}},
        ],
        "bypass_actors": bypass_actors if bypass_actors is not None else [],
    }


def _hand_rolled_protection_body(ruleset_id: int = 300) -> dict:
    """A protection ruleset an owner configured by hand — not CPV's, not ratified."""
    return {
        "id": ruleset_id,
        "name": "org-review-policy",
        "target": "branch",
        "enforcement": "active",
        "rules": [{"type": "pull_request", "parameters": {"required_approving_review_count": 2}}],
        "bypass_actors": [{"actor_id": 777, "actor_type": "Integration", "bypass_mode": "always"}],
    }


class FakeGh:
    """A recorded stand-in for `setup_branch_rules.run`.

    Answers the handful of `gh` invocations the script makes from an in-memory
    repo state and records every write. `writes` is what the two-sided
    assertions read: a test proving "no parallel ruleset was created" is only
    meaningful if the create call would have been recorded had it happened.
    """

    def __init__(
        self,
        bodies: list[dict],
        *,
        list_returncode: int = 0,
        list_stdout: str | None = None,
        unreadable_ids: set[int] | None = None,
        repo_type: str = "plugin",
    ) -> None:
        self.bodies = {b["id"]: b for b in bodies}
        self.list_returncode = list_returncode
        self.list_stdout = list_stdout
        self.unreadable_ids = unreadable_ids or set()
        self.repo_type = repo_type
        self.writes: list[tuple[str, str, dict]] = []
        self.commands: list[list[str]] = []
        self._next_new_id = 900

    # -- helpers ----------------------------------------------------------
    def _ok(self, cmd: list[str], stdout: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    def _fail(self, cmd: list[str], code: int = 1, stderr: str = "boom") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, code, "", stderr)

    def _list_view(self) -> list[dict]:
        # GitHub's list view carries no `rules` array — mirror that so a test
        # cannot accidentally pass by reading rules the real API would not
        # have returned at that point.
        return [
            {"id": b["id"], "name": b["name"], "target": b["target"], "enforcement": b["enforcement"]}
            for b in self.bodies.values()
        ]

    # -- the run() replacement --------------------------------------------
    def __call__(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        input_data: str | None = None,
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(cmd))

        if cmd[:2] == ["gh", "--version"] or cmd[:3] == ["gh", "auth", "status"]:
            return self._ok(cmd, "ok")

        if "--method" in cmd:
            method = cmd[cmd.index("--method") + 1]
            endpoint = cmd[cmd.index("--method") + 2]
            payload = json.loads(input_data or "{}")
            self.writes.append((method, endpoint, payload))
            new_id = self._next_new_id
            self._next_new_id += 1
            return self._ok(cmd, json.dumps({"id": new_id}))

        target = cmd[2] if len(cmd) > 2 else ""

        if target.endswith("/contents/.claude-plugin/plugin.json"):
            return self._ok(cmd, "{}") if self.repo_type == "plugin" else self._fail(cmd, 404)
        if target.endswith("/contents/.claude-plugin/marketplace.json"):
            return self._ok(cmd, "{}") if self.repo_type == "marketplace" else self._fail(cmd, 404)
        if target.endswith("/commits/HEAD/check-runs"):
            return self._ok(cmd, "Lint\nValidate\nTest\n")
        if target.endswith("/rulesets"):
            if self.list_returncode != 0:
                return self._fail(cmd, self.list_returncode, "gh: 503 server error")
            if self.list_stdout is not None:
                return self._ok(cmd, self.list_stdout)
            return self._ok(cmd, json.dumps(self._list_view()))
        if "/rulesets/" in target:
            ruleset_id = int(target.rsplit("/", 1)[1])
            if ruleset_id in self.unreadable_ids:
                return self._fail(cmd, 1, "gh: 500")
            body = self.bodies.get(ruleset_id)
            if body is None:
                return self._fail(cmd, 404, "not found")
            return self._ok(cmd, json.dumps(body))

        raise AssertionError(f"unexpected gh invocation in test: {cmd}")


def _run_main(fake: FakeGh, argv: list[str], monkeypatch) -> int:
    monkeypatch.setattr(sbr, "run", fake)
    with mock.patch.object(sys, "argv", ["setup_branch_rules.py", *argv]):
        return sbr.main()


def _payload_names(fake: FakeGh) -> list[str]:
    return [str(payload.get("name", "")) for _, _, payload in fake.writes]


# ── The ratified payloads ──────────────────────────────────────────────────


class TestBaselineHistoryProtectPayload:
    """Verifies `baseline-history-protect` matches the ratified spec exactly."""

    def test_name_is_the_ratified_name(self):
        """The payload is named baseline-history-protect, never cpv-branch-rules."""
        payload = sbr.build_baseline_history_protect_ruleset()
        assert payload["name"] == "baseline-history-protect"
        assert payload["name"] != LEGACY

    def test_targets_default_branch_and_is_active(self):
        """Ratified target/enforcement/condition: branch, active, ~DEFAULT_BRANCH."""
        payload = sbr.build_baseline_history_protect_ruleset()
        assert payload["target"] == "branch"
        assert payload["enforcement"] == "active"
        assert payload["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
        assert payload["conditions"]["ref_name"]["exclude"] == []

    def test_rules_are_exactly_deletion_and_non_fast_forward(self):
        """History protection carries those two rules and nothing else."""
        payload = sbr.build_baseline_history_protect_ruleset()
        assert {r["type"] for r in payload["rules"]} == {"deletion", "non_fast_forward"}

    def test_never_carries_required_linear_history(self):
        """required_linear_history was removed by owner ruling and must never return.

        Asserted on the rendered JSON rather than the rule-type set so that a
        future re-introduction as a parameter, not a rule, fails here too.
        """
        rendered = json.dumps(sbr.build_baseline_history_protect_ruleset())
        assert "required_linear_history" not in rendered

    def test_bypass_actors_is_empty_nobody_bypasses(self):
        """Nobody bypasses history protection — not even an admin."""
        assert sbr.build_baseline_history_protect_ruleset()["bypass_actors"] == []


class TestBaselinePrAndChecksPayload:
    """Verifies `baseline-pr-and-checks` matches the ratified spec exactly."""

    def test_name_is_the_ratified_name(self):
        """The payload is named baseline-pr-and-checks, never cpv-branch-rules."""
        payload = sbr.build_baseline_pr_and_checks_ruleset(["Validate"])
        assert payload["name"] == "baseline-pr-and-checks"
        assert payload["name"] != LEGACY

    def test_targets_default_branch_and_is_active(self):
        """Ratified target/enforcement/condition: branch, active, ~DEFAULT_BRANCH."""
        payload = sbr.build_baseline_pr_and_checks_ruleset(["Validate"])
        assert payload["target"] == "branch"
        assert payload["enforcement"] == "active"
        assert payload["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]

    def test_admin_bypass_is_the_ratified_default(self):
        """Default bypass is exactly the admin repository role, always — what makes a scripted release possible."""
        payload = sbr.build_baseline_pr_and_checks_ruleset(["Validate"])
        assert payload["bypass_actors"] == [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        ]

    def test_pull_request_parameters_are_ratified(self):
        """PR params: 1 approval, dismiss-stale, no codeowner, no last-push, threads resolved."""
        payload = sbr.build_baseline_pr_and_checks_ruleset(["Validate"])
        params = next(r for r in payload["rules"] if r["type"] == "pull_request")["parameters"]
        assert params["required_approving_review_count"] == 1
        assert params["dismiss_stale_reviews_on_push"] is True
        assert params["require_code_owner_review"] is False
        assert params["require_last_push_approval"] is False
        assert params["required_review_thread_resolution"] is True

    def test_status_checks_are_strict_and_carry_the_detected_contexts(self):
        """strict policy is true and the auto-detected CI contexts are required."""
        payload = sbr.build_baseline_pr_and_checks_ruleset(["Lint", "Validate", "Test"])
        params = next(r for r in payload["rules"] if r["type"] == "required_status_checks")["parameters"]
        assert params["strict_required_status_checks_policy"] is True
        assert [c["context"] for c in params["required_status_checks"]] == ["Lint", "Validate", "Test"]

    def test_explicit_bypass_actors_override_the_ratified_default(self):
        """An explicit list is honoured — that is the opt-in deviation path."""
        actors = [sbr.BypassActor(4242, "Integration", "always")]
        payload = sbr.build_baseline_pr_and_checks_ruleset(["Validate"], actors)
        assert {a["actor_id"] for a in payload["bypass_actors"]} == {4242}


# ── Never nominate a baseline ruleset for deletion ─────────────────────────


class TestBaselineIsNeverNominatedForDeletion:
    """The #203 core: a `baseline-*` ruleset is never proposed for removal."""

    def test_baseline_pair_alone_nominates_nothing(self):
        """A compliant repo produces an EMPTY removal set (was: DELETE on baseline-pr-and-checks)."""
        rulesets = [_history_protect_body(), _pr_and_checks_body()]
        assert sbr.rulesets_superseded_by_baseline(rulesets) == []

    def test_unknown_baseline_name_is_also_protected(self):
        """A `baseline-*` name this file never heard of is protected by the prefix rule."""
        assert sbr.rulesets_superseded_by_baseline([_tag_protect_body()]) == []

    def test_hand_rolled_protection_ruleset_is_not_nominated(self):
        """An owner's own protection ruleset is left alone — removal needs a positive signal."""
        assert sbr.rulesets_superseded_by_baseline([_hand_rolled_protection_body()]) == []

    def test_legacy_cpv_ruleset_is_nominated(self):
        """Two-sided: CPV's own superseded ruleset IS returned, so the check is not vacuous."""
        superseded = sbr.rulesets_superseded_by_baseline([_legacy_cpv_body(), _pr_and_checks_body()])
        assert [rs["name"] for rs in superseded] == [LEGACY]

    def test_is_ratified_baseline_name_two_sided(self):
        """The name predicate accepts every baseline-* and rejects everything else."""
        assert sbr.is_ratified_baseline_name(HISTORY)
        assert sbr.is_ratified_baseline_name(PR_CHECKS)
        assert sbr.is_ratified_baseline_name("baseline-tag-protect")
        assert not sbr.is_ratified_baseline_name(LEGACY)
        assert not sbr.is_ratified_baseline_name("org-review-policy")
        assert not sbr.is_ratified_baseline_name(None)

    def test_adoption_set_and_removal_set_are_different_questions(self):
        """`baseline-pr-and-checks` is protection-shaped (adoptable) yet never removable.

        This is the exact conflation that caused #203: one set answered both
        questions, so being a place to learn bypass actors from also meant
        being a deletion candidate.
        """
        pr = _pr_and_checks_body()
        assert sbr.is_protection_shaped(pr) is True
        assert sbr.rulesets_superseded_by_baseline([pr]) == []


# ── Planning: update in place vs create ────────────────────────────────────


class TestPlanBaselineRulesets:
    """Verifies create-vs-update planning against the repo's current rulesets."""

    def test_baselined_repo_updates_both_in_place(self):
        """Both ratified rulesets are UPDATEd by id — no new ruleset is planned."""
        rulesets = [_history_protect_body(101), _pr_and_checks_body(102)]
        plans = sbr.plan_baseline_rulesets(rulesets, ["Validate"])
        assert [(p.name, p.action, p.existing_id) for p in plans] == [
            (HISTORY, "UPDATE", 101),
            (PR_CHECKS, "UPDATE", 102),
        ]

    def test_bare_repo_creates_the_pair(self):
        """A repo with no rulesets gets both ratified rulesets CREATEd."""
        plans = sbr.plan_baseline_rulesets([], ["Validate"])
        assert [(p.name, p.action, p.existing_id) for p in plans] == [
            (HISTORY, "CREATE", None),
            (PR_CHECKS, "CREATE", None),
        ]

    def test_half_baselined_repo_updates_one_and_creates_the_other(self):
        """A partial hand-fix leaves one of the pair — that one is updated, the other created."""
        plans = sbr.plan_baseline_rulesets([_pr_and_checks_body(102)], ["Validate"])
        assert [(p.name, p.action) for p in plans] == [(HISTORY, "CREATE"), (PR_CHECKS, "UPDATE")]

    def test_no_plan_ever_carries_the_legacy_name(self):
        """Planning cannot emit a `cpv-branch-rules` payload, on any repo shape."""
        for rulesets in ([], [_legacy_cpv_body()], [_history_protect_body(), _pr_and_checks_body()]):
            plans = sbr.plan_baseline_rulesets(rulesets, ["Validate"])
            assert LEGACY not in [p.payload["name"] for p in plans]

    def test_a_legacy_ruleset_present_does_not_become_an_update_target(self):
        """`cpv-branch-rules` is never mistaken for one of the ratified pair."""
        plans = sbr.plan_baseline_rulesets([_legacy_cpv_body(200)], ["Validate"])
        assert all(p.existing_id is None for p in plans)


# ── End-to-end through main(), with a recorded fake gh ─────────────────────


class TestMainRatifiedMode:
    """Drives main() against recorded repo states — the load-bearing tests."""

    def test_baselined_repo_updates_in_place_and_nominates_nothing(self, monkeypatch, capsys):
        """The reported scenario: a repo on the baseline is brought to spec, not forked.

        Asserts all three halves of the defect are gone: PUT (not POST), no
        `cpv-branch-rules` payload, and zero DELETE advice anywhere.
        """
        fake = FakeGh([_history_protect_body(101), _pr_and_checks_body(102), _tag_protect_body(103)])
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        captured = capsys.readouterr()

        assert rc == 0
        assert [(m, e) for m, e, _ in fake.writes] == [
            ("PUT", "repos/Emasoft/plugin/rulesets/101"),
            ("PUT", "repos/Emasoft/plugin/rulesets/102"),
        ]
        assert _payload_names(fake) == [HISTORY, PR_CHECKS]
        assert LEGACY not in _payload_names(fake)
        assert "DELETE" not in captured.out
        assert "DELETE" not in captured.err

    def test_bare_repo_creates_the_ratified_pair(self, monkeypatch, capsys):
        """Two-sided: with nothing present, both ratified rulesets are POSTed."""
        fake = FakeGh([])
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        capsys.readouterr()

        assert rc == 0
        assert [m for m, _, _ in fake.writes] == ["POST", "POST"]
        assert _payload_names(fake) == [HISTORY, PR_CHECKS]

    def test_plugin_repo_gets_the_three_ci_contexts(self, monkeypatch, capsys):
        """Check contexts are still auto-detected from the repo type."""
        fake = FakeGh([], repo_type="plugin")
        _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        capsys.readouterr()
        pr_payload = next(p for _, _, p in fake.writes if p["name"] == PR_CHECKS)
        params = next(r for r in pr_payload["rules"] if r["type"] == "required_status_checks")["parameters"]
        assert [c["context"] for c in params["required_status_checks"]] == ["Lint", "Validate", "Test"]

    def test_marketplace_repo_gets_the_single_context(self, monkeypatch, capsys):
        """Two-sided on detection: a marketplace requires only 'Validate'."""
        fake = FakeGh([], repo_type="marketplace")
        _run_main(fake, ["Emasoft/mkpl"], monkeypatch)
        capsys.readouterr()
        pr_payload = next(p for _, _, p in fake.writes if p["name"] == PR_CHECKS)
        params = next(r for r in pr_payload["rules"] if r["type"] == "required_status_checks")["parameters"]
        assert [c["context"] for c in params["required_status_checks"]] == ["Validate"]

    def test_stray_legacy_ruleset_is_the_only_thing_ever_nominated(self, monkeypatch, capsys):
        """A repo left in the post-#203 damaged state: only CPV's stray ruleset is named."""
        fake = FakeGh([_history_protect_body(101), _pr_and_checks_body(102), _legacy_cpv_body(200)])
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        err = capsys.readouterr().err

        assert rc == 0
        assert "rulesets/200" in err
        assert "rulesets/101" not in err
        assert "rulesets/102" not in err

    def test_dry_run_writes_nothing(self, monkeypatch, capsys):
        """--dry-run prints both payloads and performs zero API writes."""
        fake = FakeGh([_history_protect_body(101), _pr_and_checks_body(102)])
        rc = _run_main(fake, ["Emasoft/plugin", "--dry-run"], monkeypatch)
        out = capsys.readouterr().out

        assert rc == 0
        assert fake.writes == []
        assert HISTORY in out
        assert PR_CHECKS in out

    def test_duplicate_baseline_names_are_reported(self, monkeypatch, capsys):
        """GitHub allows duplicate names; the shadow copy is named, not silently ignored."""
        fake = FakeGh([_pr_and_checks_body(102), _pr_and_checks_body(104)])
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        err = capsys.readouterr().err

        assert rc == 0
        assert "2 rulesets are named" in err
        # Only the first match is written to; the duplicate is left untouched.
        assert [e for _, e, _ in fake.writes if e.endswith("/104")] == []


class TestMainFailsClosed:
    """An unreadable repo is UNKNOWN, never 'unprotected' — nothing is written."""

    def test_unreadable_ruleset_list_refuses_and_writes_nothing(self, monkeypatch, capsys):
        """gh failure on the list → exit 2, zero writes, zero deletion advice."""
        fake = FakeGh([], list_returncode=1)
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        captured = capsys.readouterr()

        assert rc == 2
        assert fake.writes == []
        assert "REFUSING" in captured.err
        assert "DELETE" not in captured.err

    def test_non_json_ruleset_list_refuses(self, monkeypatch, capsys):
        """A proxy banner instead of JSON is UNKNOWN, not an empty repo."""
        fake = FakeGh([], list_stdout="<html>gateway timeout</html>")
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        capsys.readouterr()
        assert rc == 2
        assert fake.writes == []

    def test_non_list_ruleset_response_refuses(self, monkeypatch, capsys):
        """A JSON object where a list was expected is UNKNOWN too."""
        fake = FakeGh([], list_stdout='{"message": "Not Found"}')
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        capsys.readouterr()
        assert rc == 2
        assert fake.writes == []

    def test_unreadable_existing_baseline_refuses_before_overwriting_it(self, monkeypatch, capsys):
        """We do not overwrite a ruleset we could not read — its contents would go unreported."""
        fake = FakeGh([_history_protect_body(101), _pr_and_checks_body(102)], unreadable_ids={102})
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        capsys.readouterr()
        assert rc == 2
        assert fake.writes == []

    def test_genuinely_empty_repo_is_not_treated_as_unknown(self, monkeypatch, capsys):
        """Two-sided control: an ACTUALLY empty ruleset list proceeds normally.

        Without this, a 'fix' that refused on every repo would satisfy every
        other assertion in this class while breaking the command outright.
        """
        fake = FakeGh([], list_stdout="[]")
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        capsys.readouterr()
        assert rc == 0
        assert [m for m, _, _ in fake.writes] == ["POST", "POST"]


class TestMainLegacyMode:
    """`--legacy-cpv-ruleset` still works, but never beside a ratified baseline."""

    def test_refuses_on_a_repo_that_already_carries_a_baseline(self, monkeypatch, capsys):
        """The §F violation is refused outright: no parallel ruleset is created."""
        fake = FakeGh([_history_protect_body(101), _pr_and_checks_body(102)])
        rc = _run_main(fake, ["Emasoft/plugin", "--legacy-cpv-ruleset"], monkeypatch)
        err = capsys.readouterr().err

        assert rc == 2
        assert fake.writes == []
        assert "REFUSING" in err

    def test_refuses_on_any_baseline_prefixed_ruleset(self, monkeypatch, capsys):
        """Even a baseline this file does not manage blocks legacy mode."""
        fake = FakeGh([_tag_protect_body(103)])
        rc = _run_main(fake, ["Emasoft/plugin", "--legacy-cpv-ruleset"], monkeypatch)
        capsys.readouterr()
        assert rc == 2
        assert fake.writes == []

    def test_creates_the_legacy_ruleset_on_a_non_fleet_repo(self, monkeypatch, capsys):
        """Two-sided: with no baseline present the legacy behaviour is preserved."""
        fake = FakeGh([])
        rc = _run_main(fake, ["Emasoft/plugin", "--legacy-cpv-ruleset"], monkeypatch)
        capsys.readouterr()

        assert rc == 0
        assert _payload_names(fake) == [LEGACY]

    def test_legacy_mode_never_prints_delete_advice(self, monkeypatch, capsys):
        """Adoption no longer implies removal: a hand-rolled ruleset is adopted from, not nominated."""
        fake = FakeGh([_hand_rolled_protection_body(300)])
        rc = _run_main(fake, ["Emasoft/plugin", "--legacy-cpv-ruleset"], monkeypatch)
        captured = capsys.readouterr()

        assert rc == 0
        assert "DELETE" not in captured.err
        assert "DELETE" not in captured.out
        # ...and the good behaviour is kept: its bypass actor was adopted.
        payload = fake.writes[0][2]
        assert 777 in {a["actor_id"] for a in payload["bypass_actors"]}


class TestRatifiedBypassActorHandling:
    """Bypass actors are pinned by default and only ever deviate on request."""

    def test_extra_actor_is_dropped_by_restore_and_named(self, monkeypatch, capsys):
        """Restoring to baseline drops a non-ratified actor — and says so out loud."""
        pr = _pr_and_checks_body(
            102,
            bypass_actors=[
                {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"},
                {"actor_id": 4242, "actor_type": "Integration", "bypass_mode": "always"},
            ],
        )
        fake = FakeGh([_history_protect_body(101), pr])
        rc = _run_main(fake, ["Emasoft/plugin"], monkeypatch)
        err = capsys.readouterr().err

        assert rc == 0
        payload = next(p for _, _, p in fake.writes if p["name"] == PR_CHECKS)
        assert {a["actor_id"] for a in payload["bypass_actors"]} == {5}
        assert "4242" in err

    def test_adopt_flag_keeps_the_extra_actor_and_warns_it_deviates(self, monkeypatch, capsys):
        """Two-sided: --adopt-bypass-actors keeps it, and the run says it is not exempt."""
        pr = _pr_and_checks_body(
            102,
            bypass_actors=[
                {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"},
                {"actor_id": 4242, "actor_type": "Integration", "bypass_mode": "always"},
            ],
        )
        fake = FakeGh([_history_protect_body(101), pr])
        rc = _run_main(fake, ["Emasoft/plugin", "--adopt-bypass-actors"], monkeypatch)
        err = capsys.readouterr().err

        assert rc == 0
        payload = next(p for _, _, p in fake.writes if p["name"] == PR_CHECKS)
        assert {a["actor_id"] for a in payload["bypass_actors"]} == {5, 4242}
        assert "DEVIATES" in err
        assert "not approval-exempt" in err.lower()

    def test_adopt_flag_never_weakens_history_protect(self, monkeypatch, capsys):
        """history-protect stays no-bypass even under --adopt-bypass-actors."""
        pr = _pr_and_checks_body(
            102,
            bypass_actors=[{"actor_id": 4242, "actor_type": "Integration", "bypass_mode": "always"}],
        )
        fake = FakeGh([_history_protect_body(101), pr])
        _run_main(fake, ["Emasoft/plugin", "--adopt-bypass-actors"], monkeypatch)
        capsys.readouterr()
        payload = next(p for _, _, p in fake.writes if p["name"] == HISTORY)
        assert payload["bypass_actors"] == []

    def test_add_bypass_app_id_is_reported_as_a_deviation(self, monkeypatch, capsys):
        """An explicitly added app id lands in the payload and is flagged as a deviation."""
        fake = FakeGh([])
        rc = _run_main(fake, ["Emasoft/plugin", "--add-bypass-app-id", "15368"], monkeypatch)
        err = capsys.readouterr().err

        assert rc == 0
        payload = next(p for _, _, p in fake.writes if p["name"] == PR_CHECKS)
        assert {a["actor_id"] for a in payload["bypass_actors"]} == {5, 15368}
        assert "DEVIATES" in err

    def test_clean_baseline_run_claims_no_deviation(self, monkeypatch, capsys):
        """Control: an already-compliant repo is not accused of deviating."""
        fake = FakeGh([_history_protect_body(101), _pr_and_checks_body(102)])
        _run_main(fake, ["Emasoft/plugin", "--adopt-bypass-actors"], monkeypatch)
        assert "DEVIATES" not in capsys.readouterr().err

    def test_non_ratified_bypass_actors_two_sided(self):
        """The extras filter keeps only what the ratified list does not carry."""
        actors = [
            sbr.BypassActor(5, "RepositoryRole", "always"),
            sbr.BypassActor(4242, "Integration", "always"),
        ]
        extras = sbr.non_ratified_bypass_actors(actors)
        assert [a.actor_id for a in extras] == [4242]


class TestDriftReporting:
    """A restore names what it changes rather than stripping it silently."""

    def test_reports_a_removed_rule(self):
        """An extra rule someone added is named before the restore drops it."""
        current = _history_protect_body(rules=[{"type": "deletion"}, {"type": "required_linear_history"}])
        notes = sbr.ruleset_drift_notes(current, sbr.build_baseline_history_protect_ruleset())
        assert "rule removed: required_linear_history" in notes
        assert "rule added: non_fast_forward" in notes

    def test_reports_an_enforcement_downgrade(self):
        """A ruleset switched to 'evaluate' is reported as returning to 'active'."""
        current = _history_protect_body()
        current["enforcement"] = "evaluate"
        notes = sbr.ruleset_drift_notes(current, sbr.build_baseline_history_protect_ruleset())
        assert any("enforcement" in n for n in notes)

    def test_identical_ruleset_reports_no_drift(self):
        """Two-sided: a compliant ruleset produces an empty note list."""
        ratified = sbr.build_baseline_history_protect_ruleset()
        current = dict(ratified, id=101)
        assert sbr.ruleset_drift_notes(current, ratified) == []


# ── Fail-closed reader unit tests ──────────────────────────────────────────


class TestRulesetReaderIsThreeState:
    """UNKNOWN and 'no rulesets' are distinct values, not the same empty list."""

    def test_gh_failure_is_unknown_not_empty(self, monkeypatch):
        """A gh non-zero exit yields None (UNKNOWN), never []."""
        monkeypatch.setattr(sbr, "run", FakeGh([], list_returncode=1))
        assert sbr.fetch_all_rulesets_or_unknown("o", "r") is None

    def test_empty_repo_is_empty_not_unknown(self, monkeypatch):
        """Two-sided: a genuinely empty list is [], which is NOT None."""
        monkeypatch.setattr(sbr, "run", FakeGh([], list_stdout="[]"))
        assert sbr.fetch_all_rulesets_or_unknown("o", "r") == []

    def test_require_raises_on_unknown(self, monkeypatch):
        """require_all_rulesets converts UNKNOWN into a refusal."""
        monkeypatch.setattr(sbr, "run", FakeGh([], list_returncode=1))
        with pytest.raises(sbr.RulesetReadError):
            sbr.require_all_rulesets("o", "r")

    def test_require_returns_the_list_when_readable(self, monkeypatch):
        """Two-sided: a readable repo returns its rulesets."""
        monkeypatch.setattr(sbr, "run", FakeGh([_pr_and_checks_body(102)]))
        assert [rs["name"] for rs in sbr.require_all_rulesets("o", "r")] == [PR_CHECKS]

    def test_require_full_ruleset_raises_when_unreadable(self, monkeypatch):
        """A ruleset body we cannot read is a refusal, not an empty dict."""
        monkeypatch.setattr(sbr, "run", FakeGh([_pr_and_checks_body(102)], unreadable_ids={102}))
        with pytest.raises(sbr.RulesetReadError):
            sbr.require_full_ruleset("o", "r", 102)

    def test_lossy_shim_keeps_its_documented_contract(self, monkeypatch):
        """`_fetch_all_rulesets` still returns [] on failure for its external callers."""
        monkeypatch.setattr(sbr, "run", FakeGh([], list_returncode=1))
        assert sbr._fetch_all_rulesets("o", "r") == []

    def test_no_decision_path_calls_the_lossy_shim(self):
        """Source-level lock: nothing in the module calls the []-on-failure reader.

        The shim exists only for external callers bound to the old name. A
        decision path calling it would silently reinstate the "unreadable repo
        looks unprotected" confusion, which no behavioural test can catch
        because the two return values are indistinguishable at the call site.
        """
        source = (REPO_ROOT / "scripts" / "setup_branch_rules.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert "_fetch_all_rulesets" not in called
        # Non-vacuity: the parse really does see this module's calls, and the
        # fail-closed reader really is the one being used.
        assert "require_all_rulesets" in called
        assert hasattr(sbr, "_fetch_all_rulesets")


# ── CLI surface ────────────────────────────────────────────────────────────


class TestCliModes:
    """--help must say plainly which mode is approval-exempt."""

    def test_help_names_both_modes_and_the_exemption(self, capsys):
        """The help text states which mode is approval-exempt and which is not."""
        with mock.patch.object(sys, "argv", ["setup_branch_rules.py", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                sbr.parse_args()
            assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "--legacy-cpv-ruleset" in out
        assert "--ratified-baseline" in out
        assert "APPROVAL-EXEMPT" in out
        assert "NOT approval-exempt" in out
        assert HISTORY in out
        assert PR_CHECKS in out

    def test_ratified_is_the_default(self):
        """With no mode flag, legacy mode is off — the ratified pair is what runs."""
        with mock.patch.object(sys, "argv", ["setup_branch_rules.py", "Emasoft/x"]):
            args = sbr.parse_args()
        assert args.legacy_cpv_ruleset is False

    def test_modes_are_mutually_exclusive(self):
        """Asking for both modes at once is rejected rather than silently resolved."""
        with mock.patch.object(
            sys,
            "argv",
            ["setup_branch_rules.py", "Emasoft/x", "--ratified-baseline", "--legacy-cpv-ruleset"],
        ):
            with pytest.raises(SystemExit):
                sbr.parse_args()

    def test_adopt_bypass_actors_flag_parsed(self):
        """--adopt-bypass-actors is recognised."""
        with mock.patch.object(sys, "argv", ["setup_branch_rules.py", "Emasoft/x", "--adopt-bypass-actors"]):
            args = sbr.parse_args()
        assert args.adopt_bypass_actors is True
