#!/usr/bin/env python3
"""Set up branch-protection rules (GitHub rulesets) on a plugin/marketplace repo.

This script installs the branch protection that the local pre-push hook alone
cannot provide: any dev can bypass a local hook with `git push --no-verify`, so
the *enforceable* gate must live on the server.

TWO MODES — read this before running it against a fleet repo
------------------------------------------------------------
RATIFIED (default)
    Applies the fleet-ratified baseline TRIO — `baseline-history-protect`,
    `baseline-pr-and-checks` and `baseline-tag-protect` — by NAME: it UPDATES
    them in place when they already exist and CREATES them when they do not.
    Applying the ratified baseline as-is (and restoring a drifted one back to
    it) is the approval-EXEMPT operation under `manager-approval-defaults.md`
    §F. Payload semantics follow the janitor's code SSOT
    (branch_protection_lib.baseline_ruleset_payloads), NEVER the machine-global
    prose, which still describes the pre-2026-08-13-ruling shape.

LEGACY (`--legacy-cpv-ruleset`)
    Applies CPV's own pre-#203 ruleset `cpv-branch-rules`. This is NOT
    approval-exempt: §F lists "adding a new ruleset that affects the default
    branch" as requiring MANAGER approval. It is kept only for non-fleet repos
    that already run on it, and it REFUSES to run on a repo that already
    carries any `baseline-*` ruleset (adding a parallel ruleset beside the
    ratified pair is exactly the §F violation issue #203 reported).

Two invariants this command will not break, whatever else changes:
  * A ruleset named `baseline-*` is NEVER nominated for deletion, in any mode.
  * `baseline-history-protect` carries `deletion` + `non_fast_forward` ONLY.
    `required_linear_history` was removed by an explicit owner ruling and MUST
    NEVER be re-added.

Design goals:
  1. Enforceable — required_status_checks blocks PR merges until CI is green.
  2. Fail closed — a repo whose ruleset state cannot be READ is never written
     to. "Could not list the rulesets" is reported as UNKNOWN, never as an
     empty list, because an empty list reads as "no ratified baseline here"
     and would step the guard aside on precisely the repo nobody could inspect.
  3. Idempotent — running the script twice is a no-op.
  4. Non-destructive — it never deletes a ruleset. Removal advice is printed
     only for a ruleset this command itself supersedes, by exact name.
  5. Reusable — called from generate_plugin_repo.py and
     generate_marketplace_repo.py post-push, and available as a standalone CLI.

Usage:
    # Bring a repo to the ratified baseline (auto-detects plugin vs marketplace)
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin

    # Preview without applying
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin --dry-run

    # List installed GitHub Apps (so you can decide which to trust)
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin --list-apps

    # Legacy, non-fleet, NOT approval-exempt
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin \\
        --legacy-cpv-ruleset

    # Add extra GitHub App IDs to bypass (a baseline DEVIATION in ratified mode)
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin \\
        --add-bypass-app-id 15368 --add-bypass-app-id 29110

Exit codes:
    0  applied (or dry-run printed)
    1  a GitHub API write failed
    2  refused — `gh` missing/unauthenticated, the repo's ruleset state could
       not be read, or legacy mode was asked for on a baselined repo

Requirements: `gh` CLI authenticated with a token that has `admin:repo_hook`
and `repo` scopes on the target repo.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass

# ── Defaults ──────────────────────────────────────────────────────────────

# Status check contexts emitted by the consolidated CI workflow (ci.yml).
#
# GitHub's check-runs API reports each job's *display name* (the `name:` field
# on the job definition) as the check-run name — NOT "workflow_name / job_name".
# The required_status_checks rule in a ruleset matches against those bare
# names, so the defaults below must match what GitHub actually reports.
#
# Verify with:
#   gh api repos/<owner>/<repo>/commits/HEAD/check-runs --jq '.check_runs[].name'
#
# Plugin repos (consolidated ci.yml) report three job display names:
#     Lint, Validate, Test
#
# Marketplace repos (validate.yml) report a single job. Older marketplaces
# report the job ID "validate" (lowercase — GitHub seems to use the ID when
# the `name:` field has non-alphanumerics like "(+ nested plugins …)").
# Newer marketplaces generated after v2.13.x use `name: Validate`, which
# GitHub reports as "Validate" (capital V).
#
# If neither bare name matches your repo's actual check-run output, override
# with --check-context. Run --dry-run first to see what's reported.
DEFAULT_PLUGIN_CHECK_CONTEXTS: list[str] = [
    "Lint",
    "Validate",
    "Test",
]
DEFAULT_MARKETPLACE_CHECK_CONTEXTS: list[str] = [
    "Validate",
]
# Back-compat alias for tests written against the pre-split name.
DEFAULT_CHECK_CONTEXTS = DEFAULT_PLUGIN_CHECK_CONTEXTS

# Integration (GitHub App) IDs that CPV tries to seed as bypass actors on
# a fresh ruleset. THIS LIST IS INTENTIONALLY EMPTY.
#
# The GitHub Rulesets API rejects any app_id that is not installed on the
# target owner's account with:
#     "Actor GitHub Actions integration must be part of the ruleset source
#      or owner organization" (HTTP 422)
# because apps vary per-repo and per-owner. Hardcoding an app_id that is
# not installed causes the entire ruleset creation to fail.
#
# The supported way to bypass integrations is:
#   1. Run once — bypass_actors is seeded from the admin role only
#   2. Any existing legacy ruleset's bypass_actors are auto-adopted
#      (preserved verbatim so already-installed apps keep their bypass)
#   3. Users add more apps explicitly via --add-bypass-app-id <id>
#      after checking `--list-apps` to find the correct IDs
DEFAULT_TRUSTED_APP_IDS: list[int] = []

# Repository role IDs — well-known GitHub values.
# actor_id: 1=read, 2=triage, 4=write, 5=maintain, ...=admin (varies)
DEFAULT_TRUSTED_ROLE_IDS: list[int] = [
    5,  # Maintain (covers maintainer merges without manual review)
]

# ── Ruleset names ─────────────────────────────────────────────────────────
#
# CPV's own pre-#203 ruleset. It is NOT the fleet-ratified shape, so creating
# it is not approval-exempt; it now lives behind --legacy-cpv-ruleset.
LEGACY_RULESET_NAME = "cpv-branch-rules"
# Back-compat alias: build_ruleset() and existing callers/tests bind this name.
RULESET_NAME = LEGACY_RULESET_NAME

# The fleet-ratified baseline is a PAIR of rulesets with fixed names. This
# command targets them BY NAME so that a repo already carrying the baseline is
# brought to spec instead of gaining a third, parallel ruleset beside it.
BASELINE_HISTORY_PROTECT_NAME = "baseline-history-protect"
BASELINE_PR_AND_CHECKS_NAME = "baseline-pr-and-checks"
BASELINE_TAG_PROTECT_NAME = "baseline-tag-protect"
RATIFIED_BASELINE_NAMES: tuple[str, ...] = (
    BASELINE_HISTORY_PROTECT_NAME,
    BASELINE_PR_AND_CHECKS_NAME,
    BASELINE_TAG_PROTECT_NAME,
)

# The release-tag pattern baseline-tag-protect makes immutable. Creating a NEW
# tag stays unrestricted (publish.py still cuts each vX.Y.Z release); only
# repointing or deleting an EXISTING one is blocked.
TAG_PROTECT_REF = "refs/tags/v*.*.*"

# Any ruleset whose name starts with this prefix is fleet-ratified and is
# NEVER nominated for deletion by this command, in any mode, for any reason.
#
# WHY THIS IS A PREFIX AND NOT THE TWO NAMES ABOVE: the fleet ships more
# baseline rulesets than this command manages (`baseline-tag-protect` is one),
# and the pre-#203 classifier nominated for deletion every ruleset that was
# not CPV's own — i.e. it keyed on the ABSENCE of a name match, so each new
# ratified ruleset was nominated the day it was introduced. Keying on a
# positive `baseline-` signal makes the protection hold for names this file
# has never heard of.
BASELINE_NAME_PREFIX = "baseline-"

# The ratified `baseline-pr-and-checks` bypass: the repository role the
# ratified spec designates for admin direct-push, so a scripted release
# (publish.py) can push to the default branch while everything outside a
# release stays gated by the pull_request + required_status_checks rules.
#
# Pinned here rather than derived from DEFAULT_TRUSTED_ROLE_IDS: that seed
# belongs to the legacy ruleset and may change independently. Note the two
# disagree on what role id 5 is CALLED (the legacy comment above says
# "Maintain", the ratified spec says admin) — the id is what both the API and
# the spec key on, so neither comment is silently "corrected" here.
RATIFIED_ADMIN_BYPASS_ROLE_ID = 5
RATIFIED_ADMIN_BYPASS_MODE = "always"


def is_ratified_baseline_name(name: object) -> bool:
    """True when `name` is a fleet-ratified `baseline-*` ruleset name.

    Used as a hard veto on the deletion-advice path. Non-string names (a
    malformed API response) are not baselines but are also never nominated —
    the nomination path requires an exact positive name match.
    """
    return isinstance(name, str) and name.startswith(BASELINE_NAME_PREFIX)


# ── Shell helpers ─────────────────────────────────────────────────────────


class ShellError(RuntimeError):
    """Raised when a subprocess returns non-zero."""


def run(
    cmd: list[str], *, check: bool = True, input_data: str | None = None, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with a default 60s timeout. Callers wanting longer
    operations (clone, push, archive download) must pass an explicit
    timeout. A hung gh-api call without a timeout used to block branch-rules
    install indefinitely; the timeout makes the failure surface as
    `subprocess.TimeoutExpired` instead of a silent stall.
    """
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input_data,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise ShellError(f"Command failed ({result.returncode}): {' '.join(cmd)}\nstderr: {result.stderr}")
    return result


def check_gh_available() -> None:
    try:
        run(["gh", "--version"])
    except (FileNotFoundError, ShellError) as exc:
        sys.stderr.write(f"ERROR: `gh` CLI not available. Install from https://cli.github.com\nDetails: {exc}\n")
        sys.exit(2)


def check_gh_auth() -> None:
    result = run(["gh", "auth", "status"], check=False)
    if result.returncode != 0:
        sys.stderr.write("ERROR: `gh` CLI is not authenticated. Run `gh auth login` first.\n")
        sys.exit(2)


# ── Repo metadata ─────────────────────────────────────────────────────────


def parse_repo_slug(slug: str) -> tuple[str, str]:
    if "/" not in slug:
        raise SystemExit(f"ERROR: repo slug must be OWNER/REPO, got '{slug}'")
    owner, repo = slug.split("/", 1)
    if not owner or not repo:
        raise SystemExit(f"ERROR: repo slug must be OWNER/REPO, got '{slug}'")
    return owner, repo


def detect_repo_type(owner: str, repo: str) -> str:
    """Probe a GitHub repo for a plugin.json / marketplace.json manifest.

    Returns:
        "plugin"      — if .claude-plugin/plugin.json exists on the default branch
        "marketplace" — if .claude-plugin/marketplace.json exists on the default branch
        "unknown"     — neither found, or the repo is not reachable

    The script uses this to pick the right default set of check contexts. Users
    can always override with --check-context so detection failures are
    recoverable without editing the script.
    """
    plugin_probe = run(
        ["gh", "api", f"repos/{owner}/{repo}/contents/.claude-plugin/plugin.json"],
        check=False,
    )
    if plugin_probe.returncode == 0:
        return "plugin"
    marketplace_probe = run(
        ["gh", "api", f"repos/{owner}/{repo}/contents/.claude-plugin/marketplace.json"],
        check=False,
    )
    if marketplace_probe.returncode == 0:
        return "marketplace"
    return "unknown"


def default_check_contexts_for(repo_type: str) -> list[str]:
    """Return the default required check contexts for the given repo type."""
    if repo_type == "marketplace":
        return DEFAULT_MARKETPLACE_CHECK_CONTEXTS[:]
    # plugin or unknown — default to plugin (the common case)
    return DEFAULT_PLUGIN_CHECK_CONTEXTS[:]


def fetch_latest_check_contexts(owner: str, repo: str) -> list[str]:
    """Return check contexts actually reported on the target repo's default branch.

    Queries `/repos/{owner}/{repo}/commits/HEAD/check-runs` and extracts the
    distinct check-run names. Those names are the bare job *display names*
    (the `name:` field on each job) — NOT 'workflow_name / job_name' — which
    is exactly the form the ruleset required_status_checks rule matches
    against (see the module-level note on DEFAULT_*_CHECK_CONTEXTS). This
    result is used only as a --dry-run diagnostic so the user can compare the
    live names against the hardcoded defaults; it is never wired straight
    into the applied ruleset.

    Returns an empty list when:
      - no check-runs have reported yet (fresh repo, pre-first-CI)
      - the API call fails or the response shape is unexpected
      - the gh token lacks the checks:read scope on the repo

    The caller is expected to fall back to detection-based defaults in
    those cases. This query is purely a safety net that rescues existing
    repos whose workflow shape pre-dates the consolidation (so the
    hardcoded defaults from default_check_contexts_for may not match).
    """
    result = run(
        [
            "gh",
            "api",
            f"repos/{owner}/{repo}/commits/HEAD/check-runs",
            "--jq",
            ".check_runs[].name",
        ],
        check=False,
    )
    if result.returncode != 0:
        return []
    names: list[str] = []
    for line in result.stdout.splitlines():
        name = line.strip()
        if name and name not in names:
            names.append(name)
    return names


# ── Ruleset operations ────────────────────────────────────────────────────


@dataclass
class BypassActor:
    actor_id: int | None
    actor_type: str  # "Integration" | "RepositoryRole" | "Team" | "OrganizationAdmin" | "DeployKey"
    bypass_mode: str  # "always" | "pull_request"

    def to_dict(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "bypass_mode": self.bypass_mode,
        }


class RulesetReadError(RuntimeError):
    """The repo's ruleset state could not be READ.

    Deliberately distinct from "this repo has no rulesets". Every caller on a
    decision path must refuse rather than treat an unreadable repo as an
    unprotected one — see the fail-closed note in the module docstring.
    """


def fetch_all_rulesets_or_unknown(owner: str, repo: str) -> list[dict] | None:
    """Return every ruleset on the repo (list view — no rules array).

    Returns ``None`` — UNKNOWN — on a gh-API or JSON-decode failure, and logs
    the underlying error to stderr. UNKNOWN is NOT ``[]``: an empty list reads
    as "this repo carries no ratified baseline", which would send the caller
    down the create-a-new-ruleset path against precisely the repo whose
    existing protection nobody was able to inspect.
    """
    result = run(
        ["gh", "api", f"repos/{owner}/{repo}/rulesets", "--paginate"],
        check=False,
    )
    if result.returncode != 0:
        print(
            f"warning: could not list rulesets for {owner}/{repo} "
            f"(gh exit {result.returncode}): {result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    try:
        rulesets = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"warning: rulesets response for {owner}/{repo} was not valid JSON: {exc}", file=sys.stderr)
        return None
    if not isinstance(rulesets, list):
        print(f"warning: rulesets response for {owner}/{repo} was not a JSON list", file=sys.stderr)
        return None
    return [rs for rs in rulesets if isinstance(rs, dict)]


def require_all_rulesets(owner: str, repo: str) -> list[dict]:
    """Return every ruleset on the repo, or raise :class:`RulesetReadError`.

    This is the reader every decision path uses. The raise is what makes the
    command fail CLOSED on an unreadable repo.
    """
    rulesets = fetch_all_rulesets_or_unknown(owner, repo)
    if rulesets is None:
        raise RulesetReadError(
            f"could not read the ruleset list for {owner}/{repo} — "
            "its current branch protection is UNKNOWN, not absent"
        )
    return rulesets


def _fetch_all_rulesets(owner: str, repo: str) -> list[dict]:
    """Lossy view over :func:`fetch_all_rulesets_or_unknown` — diagnostics only.

    It collapses UNKNOWN into "no rulesets", which is the exact confusion that
    let a compliant baseline become invisible to the guard (issue #203), so no
    decision path in this module calls it. Retained because external callers
    bind this name; use :func:`require_all_rulesets` for anything that then
    creates, updates, or nominates a ruleset.
    """
    return fetch_all_rulesets_or_unknown(owner, repo) or []


def _fetch_full_ruleset(owner: str, repo: str, ruleset_id: int) -> dict | None:
    """Return the full ruleset JSON (rules + bypass_actors) for a given id."""
    result = run(
        ["gh", "api", f"repos/{owner}/{repo}/rulesets/{ruleset_id}"],
        check=False,
    )
    if result.returncode != 0:
        print(
            f"warning: could not fetch ruleset {ruleset_id} for {owner}/{repo} "
            f"(gh exit {result.returncode}): {result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        # Was previously unguarded — a malformed response crashed the whole
        # setup with a raw traceback instead of degrading to "ruleset unknown".
        print(f"warning: ruleset {ruleset_id} response was not valid JSON: {exc}", file=sys.stderr)
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def require_full_ruleset(owner: str, repo: str, ruleset_id: int) -> dict:
    """Return the full ruleset JSON, or raise :class:`RulesetReadError`.

    Used before OVERWRITING a ruleset: we report which bypass actors the
    restore is about to drop, and a ruleset we could not read is one whose
    actors we cannot name. Refusing beats silently overwriting it.
    """
    full = _fetch_full_ruleset(owner, repo, ruleset_id)
    if full is None:
        raise RulesetReadError(f"could not read ruleset {ruleset_id} on {owner}/{repo} before overwriting it")
    return full


def find_rulesets_by_name(rulesets: list[dict], name: str) -> list[dict]:
    """Return EVERY list-view entry named `name`. Pure — no I/O.

    GitHub does not enforce ruleset-name uniqueness, so a repo can legitimately
    carry two rulesets called `baseline-pr-and-checks` with different rules.
    Callers act on the first and report the rest rather than pretending the
    duplicates are not there.
    """
    return [rs for rs in rulesets if rs.get("name") == name]


def find_ruleset_by_name(rulesets: list[dict], name: str) -> dict | None:
    """Return the FIRST list-view entry for `name`, or None. Pure — no I/O."""
    matches = find_rulesets_by_name(rulesets, name)
    return matches[0] if matches else None


def warn_duplicate_ruleset_names(rulesets: list[dict], names: tuple[str, ...]) -> list[str]:
    """Warn about duplicate names among `names`; return the warnings emitted.

    A duplicate is not fatal — updating the first match still brings a
    compliant ruleset into place — but the shadow copy keeps enforcing its own
    (possibly weaker or stricter) rules, so it must not pass unmentioned.
    """
    warnings: list[str] = []
    for name in names:
        matches = find_rulesets_by_name(rulesets, name)
        if len(matches) > 1:
            ids = ", ".join(str(rs.get("id")) for rs in matches)
            message = (
                f"⚠ {len(matches)} rulesets are named '{name}' (ids: {ids}). "
                f"Only the first is updated; the others are left untouched and keep enforcing "
                f"their own rules — review them."
            )
            warnings.append(message)
            sys.stderr.write(message + "\n")
    return warnings


def present_baseline_names(rulesets: list[dict]) -> list[str]:
    """Return the names of every `baseline-*` ruleset present. Pure — no I/O."""
    return [rs["name"] for rs in rulesets if is_ratified_baseline_name(rs.get("name"))]


# A ruleset is "protection-shaped" if its rules include any of these. This set
# answers ONE question — "is this a ruleset I can learn bypass actors from?" —
# and nothing else.
#
# It used to answer a second question as well ("is this a ruleset that should
# be removed?"), and that conflation is the root of issue #203, not the
# hardcoded name: `baseline-pr-and-checks` intersects the set, so the
# compliant configuration was nominated for deletion while
# `baseline-history-protect`, which does not intersect it, survived — leaving
# a repo that still LOOKED protected with its only merge gate deleted.
# Removal is now driven by a positive name signal instead; see
# rulesets_superseded_by_baseline().
PROTECTION_SHAPED_RULE_TYPES = frozenset(
    {
        "pull_request",
        "required_status_checks",
        "required_signatures",
        "code_quality",
    }
)


def is_protection_shaped(full_ruleset: dict) -> bool:
    """True when the ruleset carries at least one protection rule. Pure."""
    rule_types = {r.get("type") for r in full_ruleset.get("rules", []) if isinstance(r, dict)}
    return bool(rule_types & PROTECTION_SHAPED_RULE_TYPES)


def fetch_existing_ruleset(owner: str, repo: str) -> dict | None:
    """Return the legacy CPV-managed ruleset (cpv-branch-rules) if present.

    Raises :class:`RulesetReadError` when the repo's ruleset state cannot be
    read — "I could not look" must never be reported as "it is not there".
    """
    entry = find_ruleset_by_name(require_all_rulesets(owner, repo), LEGACY_RULESET_NAME)
    if entry is None:
        return None
    return require_full_ruleset(owner, repo, entry["id"])


def fetch_bypass_adoption_sources(
    owner: str,
    repo: str,
    rulesets: list[dict],
    *,
    exclude_names: tuple[str, ...] = (),
) -> list[dict]:
    """Return pre-existing protection-shaped rulesets to learn bypass actors from.

    This is the ADOPTION set and only the adoption set. Membership here says
    nothing about whether a ruleset should be removed — the two questions are
    answered by different functions on purpose (issue #203).

    `rulesets` is the already-read list view, so the caller has necessarily
    passed the fail-closed read first. A ruleset whose full body cannot be
    fetched is skipped: it contributes no actors, which is the conservative
    direction for adoption.

    A ratified `baseline-*` ruleset is never a source: its bypass list is
    pinned by the ratified spec, so there is nothing to learn from it that the
    spec does not already state. Skipping it here rather than at each call
    site makes that structural instead of something a future caller can forget.
    """
    sources: list[dict] = []
    for rs in rulesets:
        name = rs.get("name")
        if name in exclude_names or is_ratified_baseline_name(name):
            continue
        full = _fetch_full_ruleset(owner, repo, rs["id"])
        if not full:
            continue
        if is_protection_shaped(full):
            sources.append(full)
    return sources


def fetch_legacy_protection_rulesets(owner: str, repo: str) -> list[dict]:
    """Back-compat wrapper for the bypass-actor ADOPTION set.

    Kept for external callers. Note the historical name: these rulesets are
    adoption SOURCES, never deletion candidates.
    """
    rulesets = require_all_rulesets(owner, repo)
    return fetch_bypass_adoption_sources(owner, repo, rulesets, exclude_names=(LEGACY_RULESET_NAME,))


def rulesets_superseded_by_baseline(rulesets: list[dict]) -> list[dict]:
    """Return the rulesets this command supersedes when it applies the baseline.

    POSITIVE SIGNAL ONLY: a ruleset qualifies by being CPV's own legacy managed
    ruleset, by exact name — something this command created and is now
    replacing. Everything else, including every `baseline-*` ruleset and every
    ruleset an owner configured by hand, is left alone and never mentioned as
    a deletion candidate.

    The `is_ratified_baseline_name` veto below is redundant against the exact
    equality test on the line after it. It is deliberate belt-and-braces: the
    cost of the redundancy is one branch, and the cost of its absence was a
    printed `DELETE` aimed at the compliant configuration.
    """
    superseded: list[dict] = []
    for rs in rulesets:
        name = rs.get("name")
        if is_ratified_baseline_name(name):
            continue
        if name == LEGACY_RULESET_NAME:
            superseded.append(rs)
    return superseded


def _parse_paginated_jq_arrays(stdout: str, *, source: str) -> list[dict]:
    """Flatten the output of `gh api --paginate --jq <filter-emitting-an-array>`.

    Critical gotcha: `--paginate` combined with `--jq` does NOT produce one
    merged JSON document. gh runs the jq filter on EACH page separately and
    concatenates the results, so a multi-page response yields several
    newline-separated JSON arrays (NDJSON), e.g.::

        [{"app_id": 1}, {"app_id": 2}]
        [{"app_id": 3}]

    A bare ``json.loads(stdout)`` on that raises ``JSONDecodeError: Extra
    data`` — and the previous bare ``except: pass`` silently dropped EVERY
    installed app for any account whose installations spanned more than one
    page, weakening the generated ruleset (those apps never became bypass
    actors). gh merges array pages into a single document only when ``--jq``
    is absent, which is why the no-jq ``--paginate`` callers above are safe.

    Parse line-by-line: each non-empty line is one page's JSON value. Tolerate
    both the array shape (extend) and a lone object (append). A genuinely
    malformed line is logged, not silently dropped, so a broken response is
    distinguishable from "no apps installed".
    """
    apps: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError as exc:
            print(
                f"warning: {source} installations line was not valid JSON: {exc}",
                file=sys.stderr,
            )
            continue
        if isinstance(chunk, list):
            apps.extend(item for item in chunk if isinstance(item, dict))
        elif isinstance(chunk, dict):
            apps.append(chunk)
    return apps


def list_installed_apps(owner: str) -> list[dict]:
    """Return the list of GitHub Apps installed on the owner account.

    Queries /user/installations for the authenticated user, and
    /orgs/{owner}/installations if the owner is an organization. Results are
    deduplicated by app_id.
    """
    apps: list[dict] = []

    # User-level installations
    user_result = run(
        ["gh", "api", "/user/installations", "--paginate", "--jq", ".installations"],
        check=False,
    )
    if user_result.returncode == 0 and user_result.stdout.strip():
        apps.extend(_parse_paginated_jq_arrays(user_result.stdout, source="user"))

    # Org-level installations (only works if owner is an org)
    org_result = run(
        ["gh", "api", f"/orgs/{owner}/installations", "--paginate", "--jq", ".installations"],
        check=False,
    )
    if org_result.returncode == 0 and org_result.stdout.strip():
        apps.extend(_parse_paginated_jq_arrays(org_result.stdout, source="org"))

    # De-duplicate by app_id
    seen: set[int] = set()
    unique: list[dict] = []
    for app in apps:
        app_id = app.get("app_id") or app.get("id")
        if app_id is None or app_id in seen:
            continue
        seen.add(app_id)
        unique.append(app)
    return unique


def build_default_bypass_actors() -> list[BypassActor]:
    """Return the default bypass_actors seed for a brand-new ruleset."""
    actors: list[BypassActor] = []
    for role_id in DEFAULT_TRUSTED_ROLE_IDS:
        actors.append(BypassActor(role_id, "RepositoryRole", "always"))
    for app_id in DEFAULT_TRUSTED_APP_IDS:
        actors.append(BypassActor(app_id, "Integration", "always"))
    return actors


def merge_bypass_actors(
    existing: list[BypassActor],
    additions: list[BypassActor],
) -> list[BypassActor]:
    """Merge two bypass_actor lists, preserving existing entries and adding new ones.

    Deduplicates by (actor_type, actor_id). Preserves bypass_mode from the
    EXISTING list when there's a collision, because the user may have already
    downgraded an actor from 'always' to 'pull_request' and we don't want to
    silently upgrade them back.
    """
    by_key: dict[tuple[str, int | None], BypassActor] = {}
    for actor in existing:
        by_key[(actor.actor_type, actor.actor_id)] = actor
    for actor in additions:
        key = (actor.actor_type, actor.actor_id)
        if key not in by_key:
            by_key[key] = actor
    return list(by_key.values())


def build_ruleset(
    check_contexts: list[str],
    bypass_actors: list[BypassActor],
) -> dict:
    """Build the LEGACY `cpv-branch-rules` payload (--legacy-cpv-ruleset only).

    This is not the fleet-ratified shape, so creating it on a repo is not
    approval-exempt under `manager-approval-defaults.md` §F. The ratified trio
    is built by build_baseline_history_protect_ruleset(),
    build_baseline_pr_and_checks_ruleset() and
    build_baseline_tag_protect_ruleset().
    """
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": ["~DEFAULT_BRANCH"],
                "exclude": [],
            },
        },
        "rules": [
            # Block destructive ops on the default branch.
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            # Require a PR before merging, but do NOT require a manual
            # approving review. This is the key compromise:
            #   - Humans: admin bypass lets you merge your own PRs
            #   - Bots:   bypass_actors lets them skip the PR flow
            #   - Auto-merge: GitHub merges as soon as CI turns green
            # Teams can bump required_approving_review_count to 1 manually.
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": False,
                    "allowed_merge_methods": ["merge", "squash", "rebase"],
                },
            },
            # The real gate: CI must pass before merge.
            # strict policy = false means branch-up-to-date is NOT required.
            # This lets auto-merge retry merges without forcing a rebase loop.
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [{"context": ctx} for ctx in check_contexts],
                },
            },
        ],
        "bypass_actors": [a.to_dict() for a in bypass_actors],
    }


# ── The ratified baseline pair ────────────────────────────────────────────


def ratified_pr_and_checks_bypass_actors() -> list[BypassActor]:
    """The bypass_actors the ratified `baseline-pr-and-checks` carries.

    Exactly one entry: the admin repository role, direct-push always. That is
    what makes a scripted release possible; outside a release, pushes are
    still gated by the pull_request + required_status_checks rules.
    """
    return [BypassActor(RATIFIED_ADMIN_BYPASS_ROLE_ID, "RepositoryRole", RATIFIED_ADMIN_BYPASS_MODE)]


def build_baseline_history_protect_ruleset() -> dict:
    """Build the ratified `baseline-history-protect` payload.

    The OWNER (admin role) bypasses history-protect. USER Tier-3 ruling
    2026-08-13: "both baseline-history-protect and baseline-pr-and-checks must
    be changed to allow mutations in history and direct pushing/merging by the
    owner." This was `[]` — nobody, explicitly including the admin — which on
    a repo whose only human IS the owner is not protection but a lock with no
    key: an amend, a rebase, or a force-push to undo a bad commit becomes
    impossible for the one person entitled to do it. `deletion` +
    `non_fast_forward` still bind EVERY non-admin actor (CI, agents, outside
    contributors), so only the owner is exempt. Do NOT "restore" the empty
    list: the machine-global prose describing the baseline still states the
    pre-ruling shape — the prose is stale, this is not.

    DO NOT ADD `required_linear_history`. It was part of an earlier draft of
    this baseline and was REMOVED by an explicit owner ruling; a non-linear
    history is allowed in every repo.
    """
    return {
        "name": BASELINE_HISTORY_PROTECT_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": ["~DEFAULT_BRANCH"],
                "exclude": [],
            },
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
        ],
        "bypass_actors": [a.to_dict() for a in ratified_pr_and_checks_bypass_actors()],
    }


def build_baseline_pr_and_checks_ruleset(
    check_contexts: list[str],
    bypass_actors: list[BypassActor] | None = None,
    *,
    require_pull_request: bool = False,
) -> dict:
    """Build the ratified `baseline-pr-and-checks` payload.

    `bypass_actors=None` yields the ratified value (admin only), which is the
    approval-exempt form. Passing a list is a deliberate DEVIATION from the
    ratified baseline — adding or removing a bypass actor on a ratified
    ruleset is a §F non-exempt operation — and the caller is responsible for
    saying so out loud.

    The `pull_request` rule is CONDITIONAL (USER Tier-3 ruling 2026-08-13):
    on a solo-owner repo the author and the reviewer are the same person, so
    a PR is addressed to its own author — it reviews nothing and only blocks
    the merge. It is emitted only when `require_pull_request` is True (repo
    owned by someone other than the authenticated login, or forced via
    --require-pr). When emitted, `required_approving_review_count` is 0, NOT
    1: GitHub forbids an author approving their own PR, so on a solo-owner
    repo 1 is not strict — it is unsatisfiable, and branches pile up behind
    it forever. Do NOT "restore" it to 1; if a repo genuinely has two humans,
    raise it FOR THAT REPO, never in the fleet-wide baseline.

    The `required_status_checks` rule is OMITTED ENTIRELY when
    `check_contexts` is empty. That is forced by the API: GitHub 422s a
    `required_status_checks` rule whose context list is empty, and the 422
    fails the WHOLE ruleset write, taking the other rules down with it.
    """
    actors = ratified_pr_and_checks_bypass_actors() if bypass_actors is None else list(bypass_actors)
    rules: list[dict] = []
    if require_pull_request:
        rules.append(
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": True,
                },
            }
        )
    if check_contexts:
        rules.append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": ctx} for ctx in check_contexts],
                },
            }
        )
    return {
        "name": BASELINE_PR_AND_CHECKS_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": ["~DEFAULT_BRANCH"],
                "exclude": [],
            },
        },
        "rules": rules,
        "bypass_actors": [a.to_dict() for a in actors],
    }


def build_baseline_tag_protect_ruleset() -> dict:
    """Build the ratified `baseline-tag-protect` payload.

    No bypass actor: creating a NEW tag is unrestricted, so publish.py still
    cuts each vX.Y.Z release — zero publish-path impact, nobody needs to
    bypass. Rules are `deletion` + `update` (NOT non_fast_forward): `update`
    blocks EVERY repoint of an existing tag, including a fast-forward move
    onto a descendant commit — the bypass a non_fast_forward-only rule would
    miss (append a malicious child commit, ff-move vX.Y.Z onto it).
    """
    return {
        "name": BASELINE_TAG_PROTECT_NAME,
        "target": "tag",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": [TAG_PROTECT_REF],
                "exclude": [],
            },
        },
        "rules": [
            {"type": "deletion"},
            {"type": "update"},
        ],
        "bypass_actors": [],
    }


def require_pull_request_for(owner: str) -> bool:
    """Should the baseline demand a PULL REQUEST on this repo?

    True only when the repo is owned by SOMEONE ELSE than the authenticated
    gh login — collaborating on another owner's project means the PR is a
    genuine request to a genuine second party. False on your own repo (the
    common case): the same person writes and reviews, so a PR gates nothing
    and is the direct cause of repos "eternally stuck with dozens of feature
    branches" (USER ruling 2026-08-13).

    Fail-open toward the WORKFLOW: an undeterminable login returns False. A
    wrongly-DEMANDED PR silently halts all merging; a wrongly-omitted one
    only means a solo owner pushes to their own default branch.
    """
    result = run(["gh", "api", "user", "--jq", ".login"], check=False)
    if result.returncode != 0:
        return False
    login = result.stdout.strip()
    return bool(login) and login.lower() != owner.lower()


def non_ratified_bypass_actors(actors: list[BypassActor]) -> list[BypassActor]:
    """Return the actors that the ratified `baseline-pr-and-checks` does not carry.

    These are what a restore-to-baseline drops, and what --adopt-bypass-actors
    keeps. Either way they are NAMED in the output: silently removing a bypass
    actor an owner added by hand is destructive-adjacent, and silently keeping
    one turns an exempt restore into a non-exempt deviation.
    """
    ratified = {(a.actor_type, a.actor_id) for a in ratified_pr_and_checks_bypass_actors()}
    return [a for a in actors if (a.actor_type, a.actor_id) not in ratified]


def bypass_actors_from_ruleset(full_ruleset: dict) -> list[BypassActor]:
    """Read a ruleset's bypass_actors into BypassActor objects. Pure — no I/O."""
    return [
        BypassActor(
            actor_id=a.get("actor_id"),
            actor_type=a.get("actor_type", "Integration"),
            bypass_mode=a.get("bypass_mode", "always"),
        )
        for a in full_ruleset.get("bypass_actors", [])
        if isinstance(a, dict)
    ]


def ruleset_drift_notes(current: dict, ratified: dict) -> list[str]:
    """Describe what restoring `current` to `ratified` CHANGES. Pure — no I/O.

    A restore sends the full payload, so anything the ratified spec does not
    carry is stripped: an extra rule someone added, a downgraded enforcement,
    a hand-added bypass actor. Stripping it is the intended effect of a
    restore, but doing it silently is how a deliberate local decision
    disappears without anyone noticing. Every difference gets named.
    """
    notes: list[str] = []

    current_enforcement = current.get("enforcement")
    if current_enforcement != ratified.get("enforcement"):
        notes.append(f"enforcement {current_enforcement!r} -> {ratified.get('enforcement')!r}")

    current_rules = {r.get("type") for r in current.get("rules", []) if isinstance(r, dict)}
    ratified_rules = {r.get("type") for r in ratified.get("rules", []) if isinstance(r, dict)}
    for removed in sorted(str(t) for t in current_rules - ratified_rules):
        notes.append(f"rule removed: {removed}")
    for added in sorted(str(t) for t in ratified_rules - current_rules):
        notes.append(f"rule added: {added}")

    # Compared as (type, id) STRINGS so the two sides are one type: a
    # BypassActor carries `int | None` while the raw API dict carries Any, and
    # a DeployKey legitimately has actor_id null.
    current_actors = {(str(a.actor_type), str(a.actor_id)) for a in bypass_actors_from_ruleset(current)}
    ratified_actors = {
        (str(a.get("actor_type")), str(a.get("actor_id")))
        for a in ratified.get("bypass_actors", [])
        if isinstance(a, dict)
    }
    for actor_type, actor_id in sorted(current_actors - ratified_actors):
        notes.append(f"bypass actor removed: {actor_type} id={actor_id}")
    for actor_type, actor_id in sorted(ratified_actors - current_actors):
        notes.append(f"bypass actor added: {actor_type} id={actor_id}")

    return notes


@dataclass
class RulesetPlan:
    """One ruleset this run will write: what, where, and create-vs-update."""

    name: str
    action: str  # "CREATE" | "UPDATE"
    existing_id: int | None
    payload: dict


def plan_baseline_rulesets(
    rulesets: list[dict],
    check_contexts: list[str],
    pr_bypass_actors: list[BypassActor] | None = None,
    *,
    require_pull_request: bool = False,
) -> list[RulesetPlan]:
    """Plan the ratified trio against the repo's current rulesets. Pure — no I/O.

    Each ruleset is planned independently, so a repo carrying only some of
    them gets those UPDATED and the rest CREATED — the half-baselined
    state a partial hand-fix or an interrupted earlier run leaves behind.
    """
    plans: list[RulesetPlan] = []
    for name, payload in (
        (BASELINE_HISTORY_PROTECT_NAME, build_baseline_history_protect_ruleset()),
        (
            BASELINE_PR_AND_CHECKS_NAME,
            build_baseline_pr_and_checks_ruleset(
                check_contexts, pr_bypass_actors, require_pull_request=require_pull_request
            ),
        ),
        (BASELINE_TAG_PROTECT_NAME, build_baseline_tag_protect_ruleset()),
    ):
        entry = find_ruleset_by_name(rulesets, name)
        existing_id = entry.get("id") if entry else None
        plans.append(
            RulesetPlan(
                name=name,
                action="UPDATE" if existing_id is not None else "CREATE",
                existing_id=existing_id,
                payload=payload,
            )
        )
    return plans


def apply_ruleset(
    owner: str,
    repo: str,
    ruleset: dict,
    existing_id: int | None,
) -> dict:
    """POST (create) or PUT (update) the ruleset. Returns the server response."""
    if existing_id is None:
        # CREATE
        endpoint = f"repos/{owner}/{repo}/rulesets"
        method = "POST"
    else:
        # UPDATE
        endpoint = f"repos/{owner}/{repo}/rulesets/{existing_id}"
        method = "PUT"

    payload = json.dumps(ruleset)
    result = run(
        ["gh", "api", "--method", method, endpoint, "--input", "-"],
        input_data=payload,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(f"ERROR: {method} {endpoint} failed\n")
        sys.stderr.write(f"stderr: {result.stderr}\n")
        sys.exit(1)

    # Guard the parse like every other json.loads in this module: a gh exit 0
    # with a non-JSON body (proxy banner, truncated stream) would otherwise
    # crash apply with a raw traceback instead of a clean, actionable error.
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"ERROR: {method} {endpoint} returned non-JSON output: {exc}\n")
        sys.exit(1)
    if not isinstance(parsed, dict):
        sys.stderr.write(f"ERROR: unexpected response shape from {method} {endpoint}\n")
        sys.exit(1)
    return parsed


# ── CLI ───────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Bring a plugin or marketplace repo to the fleet-ratified branch-protection "
            "baseline. Idempotent, and it never deletes anything."
        ),
        epilog=(
            "MODES AND APPROVAL\n"
            "  default (ratified)     Applies the ratified trio 'baseline-history-protect' +\n"
            "                         'baseline-pr-and-checks' + 'baseline-tag-protect' BY NAME:\n"
            "                         updates them in place\n"
            "                         when present, creates them when absent. Applying the\n"
            "                         ratified baseline as-is, and restoring a drifted one back\n"
            "                         to it, are APPROVAL-EXEMPT under manager-approval-defaults\n"
            "                         section F.\n"
            "  --legacy-cpv-ruleset   Applies CPV's own 'cpv-branch-rules' instead. This is NOT\n"
            "                         approval-exempt: section F lists adding a new ruleset that\n"
            "                         affects the default branch as requiring MANAGER approval.\n"
            "                         It refuses to run on a repo that already carries any\n"
            "                         baseline-* ruleset.\n"
            "\n"
            "  Adding, removing, or adopting a bypass actor on a ratified ruleset is likewise\n"
            "  NOT exempt: --adopt-bypass-actors and --add-bypass-app-id produce a payload that\n"
            "  DEVIATES from the ratified baseline, and the run says so.\n"
            "\n"
            "  A ruleset named baseline-* is never nominated for deletion, in either mode.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("repo", help="Target repo slug (OWNER/REPO)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--ratified-baseline",
        action="store_true",
        help=(
            "Apply the ratified baseline pair (the DEFAULT; pass it to state the "
            "intent explicitly in automation). Approval-exempt."
        ),
    )
    mode.add_argument(
        "--legacy-cpv-ruleset",
        action="store_true",
        help=(
            "Apply CPV's legacy 'cpv-branch-rules' ruleset instead of the ratified "
            "pair. NOT approval-exempt, and refuses on a repo that already carries "
            "a baseline-* ruleset."
        ),
    )
    p.add_argument(
        "--adopt-bypass-actors",
        action="store_true",
        help=(
            "Ratified mode only: keep bypass actors found on a pre-existing ruleset "
            "instead of restoring 'baseline-pr-and-checks' to its ratified admin-only "
            "list. This DEVIATES from the ratified baseline and is not approval-exempt. "
            "It never touches 'baseline-history-protect' (pinned admin-only bypass) "
            "or 'baseline-tag-protect' (pinned no-bypass)."
        ),
    )
    p.add_argument(
        "--check-context",
        action="append",
        default=None,
        help=(
            "Required status check context. Repeatable. Defaults are "
            "auto-detected from the target repo type: plugins use "
            "'Lint', 'Validate', 'Test' (the three jobs of the consolidated "
            "CI workflow); marketplaces use 'Validate'. Check-run names are "
            "bare job display names, NOT 'workflow / job' format."
        ),
    )
    p.add_argument(
        "--add-bypass-app-id",
        action="append",
        type=int,
        default=[],
        help="GitHub App ID to add to bypass_actors. Repeatable.",
    )
    p.add_argument(
        "--reset-bypass",
        action="store_true",
        help=(
            "Legacy mode only: reset bypass_actors to defaults (ignores existing). "
            "WARNING: this removes any manually configured trust. In ratified mode "
            "the bypass list is pinned by the baseline, so the flag has no effect."
        ),
    )
    pr_group = p.add_mutually_exclusive_group()
    pr_group.add_argument(
        "--require-pr",
        dest="require_pr",
        action="store_true",
        default=None,
        help=(
            "Force the pull_request rule into 'baseline-pr-and-checks'. Default is "
            "AUTO: required only when the repo is owned by someone other than the "
            "authenticated gh login (solo-owner repos omit it — Tier-3 ruling "
            "2026-08-13: a PR addressed to its own author reviews nothing)."
        ),
    )
    pr_group.add_argument(
        "--no-require-pr",
        dest="require_pr",
        action="store_false",
        help="Force the pull_request rule OFF regardless of ownership detection.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ruleset payload and exit — do not apply.",
    )
    p.add_argument(
        "--list-apps",
        action="store_true",
        help="List GitHub Apps installed on the owner and exit.",
    )
    return p.parse_args()


def cmd_list_apps(owner: str, repo: str) -> int:
    print(f"GitHub Apps installed on {owner} (all apps, not just {owner}/{repo}):")
    print()
    apps = list_installed_apps(owner)
    if not apps:
        print("  (no apps found — or gh token lacks org:read scope)")
        return 0
    for app in apps:
        app_id = app.get("app_id") or app.get("id")
        slug = app.get("app_slug") or (app.get("account") or {}).get("login") or "?"
        account = (app.get("account") or {}).get("login") or "?"
        print(f"  actor_id={app_id:<8} slug={slug:<30} account={account}")
    print()
    print("To add one of these to the ruleset bypass list:")
    print(f"  uv run python scripts/setup_branch_rules.py {owner}/{repo} \\")
    print("    --add-bypass-app-id <actor_id>")
    return 0


def resolve_check_contexts(args: argparse.Namespace, owner: str, repo: str) -> list[str]:
    """Pick the required status-check contexts for this repo.

    User-supplied --check-context flags always win. Otherwise use the defaults
    for the detected repo type.

    We intentionally do NOT treat the live check-runs from
    `gh api /commits/HEAD/check-runs` as authoritative, because:
      1. On a fresh repo, no check-runs exist yet
      2. Check-run *names* and ruleset *contexts* aren't always the same
         (a multi-job workflow can report check-runs as bare job names on
         the API while the ruleset requires `workflow_name / job_name`)
      3. Stale workflows (e.g. Dependabot runs on main) pollute the name
         list with contexts that have nothing to do with CI validation
    For dry-runs we print the live check-run names as a diagnostic so the user
    can sanity-check that the defaults match their actual CI.
    """
    if args.check_context:
        check_contexts: list[str] = args.check_context
        sys.stderr.write(f"Using user-specified check contexts: {', '.join(check_contexts)}\n")
        return check_contexts

    repo_type = detect_repo_type(owner, repo)
    if repo_type == "unknown":
        # No CI contexts are DETECTABLE for a repo we cannot even type-probe.
        # Fabricating the plugin defaults here would require checks that may
        # never report (PRs pending forever); GitHub also 422s an EMPTY
        # required_status_checks list, so the ratified shape OMITS the rule
        # entirely and it reappears once the repo type is detectable (or via
        # explicit --check-context).
        sys.stderr.write(
            "Detected repo type: unknown — no check contexts detectable; the "
            "required_status_checks rule will be OMITTED (pass --check-context to force).\n"
        )
        return []
    check_contexts = default_check_contexts_for(repo_type)
    sys.stderr.write(f"Detected repo type: {repo_type} (defaults: {', '.join(check_contexts)})\n")
    if args.dry_run:
        live = fetch_latest_check_contexts(owner, repo)
        if live:
            sys.stderr.write(f"  For reference, check-runs currently reported on HEAD: {', '.join(live)}\n")
            sys.stderr.write(
                "  If these differ from the defaults above, pass "
                "--check-context explicitly for each name you actually need.\n"
            )
        else:
            sys.stderr.write(
                "  No check-runs reported on HEAD yet — the first CI run "
                "must complete before the ruleset can be enforced.\n"
            )
    return check_contexts


def report_superseded_rulesets(owner: str, repo: str, rulesets: list[dict]) -> None:
    """Print removal ADVICE for rulesets the ratified pair supersedes.

    Nothing is deleted here or anywhere else in this script. Only CPV's own
    legacy ruleset can appear, by exact name; a `baseline-*` ruleset never can.
    """
    superseded = rulesets_superseded_by_baseline(rulesets)
    if not superseded:
        return
    sys.stderr.write(
        f"\nNote: the ratified baseline supersedes CPV's own '{LEGACY_RULESET_NAME}' ruleset.\n"
        "  Nothing was deleted. Review it, and remove it yourself if you no longer want it:\n"
    )
    for rs in superseded:
        sys.stderr.write(f"    gh api --method DELETE repos/{owner}/{repo}/rulesets/{rs.get('id')}\n")


def fetch_existing_baseline_bodies(owner: str, repo: str, rulesets: list[dict]) -> dict[str, dict]:
    """Read the full body of every ratified ruleset already on the repo.

    Read ONCE, up front, and reused for both the bypass-actor decision and the
    drift report — and fail-closed: a ruleset we cannot read is one whose
    contents we cannot report, and we do not overwrite what we could not read.
    """
    bodies: dict[str, dict] = {}
    for name in RATIFIED_BASELINE_NAMES:
        entry = find_ruleset_by_name(rulesets, name)
        if entry is not None:
            bodies[name] = require_full_ruleset(owner, repo, entry["id"])
    return bodies


def resolve_ratified_pr_bypass_actors(
    args: argparse.Namespace,
    owner: str,
    repo: str,
    rulesets: list[dict],
    existing_pr_ruleset: dict | None,
) -> list[BypassActor] | None:
    """Decide the bypass_actors for `baseline-pr-and-checks`, and say why.

    Returns None to mean "the ratified value" (the approval-exempt form), or an
    explicit list when the user asked for a deviation. Either way, every actor
    that differs from the ratified list is NAMED on stderr before anything is
    written — an actor silently dropped by a restore, or silently kept, is the
    kind of change nobody notices until it matters.
    """
    candidates: list[BypassActor] = []

    # Actors already on the ratified ruleset we are about to overwrite.
    if existing_pr_ruleset is not None:
        candidates.extend(bypass_actors_from_ruleset(existing_pr_ruleset))

    # First-run adoption: actors configured on a pre-existing protection
    # ruleset that predates the baseline (the ratified pair is skipped by
    # fetch_bypass_adoption_sources itself — its bypass list is pinned).
    for source in fetch_bypass_adoption_sources(owner, repo, rulesets):
        found = bypass_actors_from_ruleset(source)
        if found:
            sys.stderr.write(
                f"⚠ Pre-existing protection ruleset '{source.get('name')}' "
                f"(id={source.get('id')}) carries {len(found)} bypass actor(s).\n"
            )
            candidates.extend(found)

    extras = non_ratified_bypass_actors(merge_bypass_actors(candidates, []))
    cli_additions = [BypassActor(app_id, "Integration", "always") for app_id in args.add_bypass_app_id]

    if not args.adopt_bypass_actors and not cli_additions:
        if extras:
            sys.stderr.write(
                "  These actors are NOT part of the ratified baseline and will not be applied:\n"
            )
            for actor in extras:
                sys.stderr.write(f"    {actor.actor_type} id={actor.actor_id} ({actor.bypass_mode})\n")
            sys.stderr.write(
                "  Restoring the ratified baseline drops them. Pass --adopt-bypass-actors "
                "to keep them (a baseline DEVIATION — not approval-exempt).\n"
            )
        return None

    kept = extras if args.adopt_bypass_actors else []
    actors = merge_bypass_actors(kept + cli_additions, ratified_pr_and_checks_bypass_actors())
    if not non_ratified_bypass_actors(actors):
        # --adopt-bypass-actors was passed but there was nothing extra to keep.
        # The payload is the ratified one, so do not claim a deviation.
        return None
    sys.stderr.write(
        f"⚠ '{BASELINE_PR_AND_CHECKS_NAME}' will carry {len(actors)} bypass actor(s), which "
        "DEVIATES from the ratified baseline.\n"
        "  Adding or removing a bypass actor on a ratified ruleset is NOT approval-exempt "
        "under manager-approval-defaults section F.\n"
    )
    return actors


def run_ratified_mode(args: argparse.Namespace, owner: str, repo: str, rulesets: list[dict]) -> int:
    """Apply the ratified baseline pair — update in place, or create if absent."""
    if args.reset_bypass:
        sys.stderr.write(
            "Note: --reset-bypass has no effect in ratified mode — the baseline pins its own "
            "bypass actors.\n"
        )

    check_contexts = resolve_check_contexts(args, owner, repo)
    existing_bodies = fetch_existing_baseline_bodies(owner, repo, rulesets)
    pr_bypass_actors = resolve_ratified_pr_bypass_actors(
        args,
        owner,
        repo,
        rulesets,
        existing_bodies.get(BASELINE_PR_AND_CHECKS_NAME),
    )
    if args.require_pr is None:
        want_pr = require_pull_request_for(owner)
    else:
        want_pr = args.require_pr
    sys.stderr.write(
        f"pull_request rule: {'REQUIRED' if want_pr else 'omitted (solo-owner repo — Tier-3 ruling 2026-08-13)'}\n"
    )
    plans = plan_baseline_rulesets(
        rulesets, check_contexts, pr_bypass_actors, require_pull_request=want_pr
    )

    for plan in plans:
        current = existing_bodies.get(plan.name)
        if current is None:
            continue
        for note in ruleset_drift_notes(current, plan.payload):
            sys.stderr.write(f"  {plan.name}: {note}\n")

    if args.dry_run:
        print(f"# Dry run — {owner}/{repo} (ratified baseline)")
        for plan in plans:
            existing = f" (id={plan.existing_id})" if plan.existing_id is not None else ""
            print(f"# {plan.action} {plan.name}{existing}")
            print(json.dumps(plan.payload, indent=2))
            print()
        report_superseded_rulesets(owner, repo, rulesets)
        return 0

    for plan in plans:
        response = apply_ruleset(owner, repo, plan.payload, plan.existing_id)
        verb = "updated" if plan.existing_id is not None else "created"
        print(f"✓ Ruleset {verb}: {plan.name} (id={response.get('id')})")
        print(f"  View: https://github.com/{owner}/{repo}/rules/{response.get('id')}")
    if check_contexts:
        print(f"  Check contexts required: {', '.join(check_contexts)}")
    else:
        print("  Check contexts required: (none — required_status_checks rule omitted)")
    report_superseded_rulesets(owner, repo, rulesets)
    return 0


def run_legacy_mode(args: argparse.Namespace, owner: str, repo: str, rulesets: list[dict]) -> int:
    """Apply CPV's legacy `cpv-branch-rules` ruleset.

    Refuses on a repo that already carries a ratified `baseline-*` ruleset:
    creating a parallel ruleset beside the ratified pair is the §F violation
    issue #203 reported, and CPV must not perform it unasked on a fleet repo.
    """
    baselines = present_baseline_names(rulesets)
    if baselines:
        sys.stderr.write(
            f"REFUSING: {owner}/{repo} already carries the ratified baseline "
            f"({', '.join(sorted(baselines))}).\n"
            f"  Creating '{LEGACY_RULESET_NAME}' beside it would add a parallel ruleset on the "
            "default branch, which is not approval-exempt.\n"
            "  Run without --legacy-cpv-ruleset to bring the ratified baseline to spec, or use "
            "scripts/setup_branch_rules_generic.py for a deliberately custom ruleset.\n"
        )
        return 2

    check_contexts = resolve_check_contexts(args, owner, repo)
    if not check_contexts:
        # The legacy build_ruleset always emits a required_status_checks rule,
        # and GitHub 422s an empty context list — keep the legacy mode's
        # historical behavior (plugin defaults) rather than a guaranteed 422.
        check_contexts = default_check_contexts_for("plugin")
        sys.stderr.write(
            f"Legacy mode: falling back to plugin default contexts: {', '.join(check_contexts)}\n"
        )

    entry = find_ruleset_by_name(rulesets, LEGACY_RULESET_NAME)
    existing = require_full_ruleset(owner, repo, entry["id"]) if entry else None
    existing_id = existing.get("id") if existing else None

    # Source bypass actors to preserve, in priority order:
    #   1. The CPV-managed ruleset (most recent state)
    #   2. Any pre-existing protection ruleset (first run adoption)
    #   3. Empty (only when --reset-bypass is passed)
    existing_actors: list[BypassActor] = []
    if not args.reset_bypass:
        source: dict | None = existing
        if source is None:
            sources = fetch_bypass_adoption_sources(
                owner,
                repo,
                rulesets,
                exclude_names=(LEGACY_RULESET_NAME,),
            )
            if sources:
                # Adopt bypass actors from the first source found. Adoption is
                # all this set is for — it nominates nothing for deletion.
                source = sources[0]
                source_names = ", ".join(str(rs.get("name", "?")) for rs in sources)
                sys.stderr.write(f"⚠ Found {len(sources)} pre-existing protection ruleset(s): {source_names}\n")
                sys.stderr.write(f"  Adopting bypass_actors from '{source.get('name')}' (id={source.get('id')}).\n")
        if source is not None:
            existing_actors = bypass_actors_from_ruleset(source)

    # Merge: existing + defaults + CLI additions
    defaults = build_default_bypass_actors()
    additions = [BypassActor(app_id, "Integration", "always") for app_id in args.add_bypass_app_id]
    bypass_actors = merge_bypass_actors(existing_actors, defaults + additions)

    ruleset = build_ruleset(check_contexts, bypass_actors)

    if args.dry_run:
        print(f"# Dry run — {owner}/{repo} (legacy {LEGACY_RULESET_NAME})")
        print(f"# Existing ruleset: {'found (id=' + str(existing_id) + ')' if existing else 'none'}")
        print(f"# Action: {'UPDATE' if existing_id else 'CREATE'}")
        print()
        print(json.dumps(ruleset, indent=2))
        return 0

    response = apply_ruleset(owner, repo, ruleset, existing_id)
    print(f"✓ Ruleset {'updated' if existing_id else 'created'}: {LEGACY_RULESET_NAME} (id={response.get('id')})")
    print(f"  Check contexts required: {', '.join(check_contexts)}")
    print(f"  Bypass actors preserved/added: {len(bypass_actors)}")
    print(f"  View: https://github.com/{owner}/{repo}/rules/{response.get('id')}")
    return 0


def main() -> int:
    args = parse_args()
    check_gh_available()
    check_gh_auth()

    owner, repo = parse_repo_slug(args.repo)

    if args.list_apps:
        return cmd_list_apps(owner, repo)

    # ONE read of the repo's ruleset state, and every decision below is taken
    # from it. Fail closed: an unreadable repo is UNKNOWN, not unprotected, so
    # nothing is created, updated, or nominated for removal.
    try:
        rulesets = require_all_rulesets(owner, repo)
    except RulesetReadError as exc:
        sys.stderr.write(f"REFUSING: {exc}.\n")
        sys.stderr.write(
            "  Nothing was applied. Re-run once the GitHub API is reachable and the token "
            "has repo admin scope.\n"
        )
        return 2

    warn_duplicate_ruleset_names(rulesets, RATIFIED_BASELINE_NAMES + (LEGACY_RULESET_NAME,))

    try:
        if args.legacy_cpv_ruleset:
            return run_legacy_mode(args, owner, repo, rulesets)
        return run_ratified_mode(args, owner, repo, rulesets)
    except RulesetReadError as exc:
        sys.stderr.write(f"REFUSING: {exc}.\n")
        sys.stderr.write("  Nothing was applied.\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
