#!/usr/bin/env python3
"""Set up branch-protection rules (GitHub rulesets) on a plugin/marketplace repo.

This script creates or updates a GitHub ruleset that enforces the CPV CI pipeline
as a required status check — the real security boundary that the local pre-push
hook alone cannot provide. See docs: any dev can bypass a local hook with
`git push --no-verify`, so the *enforceable* gate must live on the server.

Design goals:
  1. Enforceable — required_status_checks rule blocks PR merges until CI is green.
  2. Bot-friendly — trusted GitHub Apps (dependabot, github-actions, Claude,
     Copilot, etc.) bypass the PR-review requirement so the maintainer is not
     spammed with manual approvals for routine bot changes.
  3. Auto-merge friendly — does not block GitHub's auto-merge; once CI passes,
     the PR is merged automatically without requiring a manual approve click.
  4. Idempotent — running the script twice is a no-op. Updating preserves any
     existing bypass_actors that the user previously configured (so adding a
     new trusted App through the GitHub UI is not clobbered by a subsequent run).
  5. Reusable — same ruleset shape is applied to plugin repos and marketplace
     repos. Called from generate_plugin_repo.py and generate_marketplace_repo.py
     post-push, and also available as a standalone CLI.

Usage:
    # Create or update the ruleset on a repo (auto-detects plugin vs marketplace)
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin

    # Preview without applying
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin --dry-run

    # List installed GitHub Apps (so you can decide which to trust)
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin --list-apps

    # Reset bypass_actors instead of preserving existing ones
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin --reset-bypass

    # Add extra GitHub App IDs to bypass
    uv run python scripts/setup_branch_rules.py Emasoft/my-plugin \\
        --add-bypass-app-id 15368 --add-bypass-app-id 29110

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

RULESET_NAME = "cpv-branch-rules"


# ── Shell helpers ─────────────────────────────────────────────────────────

class ShellError(RuntimeError):
    """Raised when a subprocess returns non-zero."""


def run(cmd: list[str], *, check: bool = True,
        input_data: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=input_data,
        check=False,
    )
    if check and result.returncode != 0:
        raise ShellError(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr}"
        )
    return result


def check_gh_available() -> None:
    try:
        run(["gh", "--version"])
    except (FileNotFoundError, ShellError) as exc:
        sys.stderr.write(
            "ERROR: `gh` CLI not available. Install from https://cli.github.com\n"
            f"Details: {exc}\n"
        )
        sys.exit(2)


def check_gh_auth() -> None:
    result = run(["gh", "auth", "status"], check=False)
    if result.returncode != 0:
        sys.stderr.write(
            "ERROR: `gh` CLI is not authenticated. Run `gh auth login` first.\n"
        )
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
    distinct check-run names. The names returned by that endpoint already
    match the 'workflow_name / job_name' format that the ruleset
    required_status_checks rule expects.

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


def _fetch_all_rulesets(owner: str, repo: str) -> list[dict]:
    """Return every ruleset on the repo (list view — no rules array)."""
    result = run(
        ["gh", "api", f"repos/{owner}/{repo}/rulesets", "--paginate"],
        check=False,
    )
    if result.returncode != 0:
        return []
    try:
        rulesets = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(rulesets, list):
        return []
    return rulesets


def _fetch_full_ruleset(owner: str, repo: str, ruleset_id: int) -> dict | None:
    """Return the full ruleset JSON (rules + bypass_actors) for a given id."""
    result = run(
        ["gh", "api", f"repos/{owner}/{repo}/rulesets/{ruleset_id}"],
        check=False,
    )
    if result.returncode != 0:
        return None
    parsed = json.loads(result.stdout)
    if isinstance(parsed, dict):
        return parsed
    return None


def fetch_existing_ruleset(owner: str, repo: str) -> dict | None:
    """Return the CPV-managed ruleset (named cpv-branch-rules) if present."""
    for rs in _fetch_all_rulesets(owner, repo):
        if rs.get("name") == RULESET_NAME:
            return _fetch_full_ruleset(owner, repo, rs["id"])
    return None


def fetch_legacy_protection_rulesets(
    owner: str, repo: str,
) -> list[dict]:
    """Return non-CPV rulesets that look like branch-protection rules.

    Used to adopt bypass_actors from a pre-existing ruleset on first run.
    A ruleset is considered "protection-shaped" if its rules include any of:
    pull_request, required_status_checks, required_signatures, or code_quality.
    """
    legacy: list[dict] = []
    for rs in _fetch_all_rulesets(owner, repo):
        if rs.get("name") == RULESET_NAME:
            continue  # skip our managed one
        full = _fetch_full_ruleset(owner, repo, rs["id"])
        if not full:
            continue
        rule_types = {r.get("type") for r in full.get("rules", [])}
        protection_rules = {
            "pull_request",
            "required_status_checks",
            "required_signatures",
            "code_quality",
        }
        if rule_types & protection_rules:
            legacy.append(full)
    return legacy


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
        try:
            user_installations = json.loads(user_result.stdout)
            if isinstance(user_installations, list):
                apps.extend(user_installations)
        except json.JSONDecodeError:
            pass

    # Org-level installations (only works if owner is an org)
    org_result = run(
        ["gh", "api", f"/orgs/{owner}/installations", "--paginate", "--jq", ".installations"],
        check=False,
    )
    if org_result.returncode == 0 and org_result.stdout.strip():
        try:
            org_installations = json.loads(org_result.stdout)
            if isinstance(org_installations, list):
                apps.extend(org_installations)
        except json.JSONDecodeError:
            pass

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
    """Build the full ruleset payload for the POST/PUT call."""
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
                    "required_status_checks": [
                        {"context": ctx} for ctx in check_contexts
                    ],
                },
            },
        ],
        "bypass_actors": [a.to_dict() for a in bypass_actors],
    }


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

    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        sys.stderr.write(f"ERROR: unexpected response shape from {method} {endpoint}\n")
        sys.exit(1)
    return parsed


# ── CLI ───────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Create/update the CPV branch-protection ruleset on a plugin or "
            "marketplace repo. Idempotent. Preserves existing bypass_actors by default."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("repo", help="Target repo slug (OWNER/REPO)")
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
            "Reset bypass_actors to defaults only (ignores existing). "
            "WARNING: this removes any manually configured trust."
        ),
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


def main() -> int:
    args = parse_args()
    check_gh_available()
    check_gh_auth()

    owner, repo = parse_repo_slug(args.repo)

    if args.list_apps:
        return cmd_list_apps(owner, repo)

    # Pick default check contexts. User-supplied --check-context flags always
    # win. Otherwise use the hardcoded defaults from detect_repo_type().
    #
    # Note: we intentionally do NOT use the live check-runs reported by
    # `gh api /commits/HEAD/check-runs` as authoritative, because:
    #   1. On a fresh repo, no check-runs exist yet
    #   2. Check-run *names* and ruleset *contexts* aren't always the same
    #      (a multi-job workflow can report check-runs as bare job names on
    #      the API while the ruleset requires `workflow_name / job_name`)
    #   3. Stale workflows (e.g. Dependabot runs on main) pollute the name
    #      list with contexts that have nothing to do with CI validation
    # For dry-runs we print the live check-run names as a diagnostic so the
    # user can sanity-check that the hardcoded defaults match their actual CI.
    if args.check_context:
        check_contexts = args.check_context
        sys.stderr.write(
            f"Using user-specified check contexts: {', '.join(check_contexts)}\n"
        )
    else:
        repo_type = detect_repo_type(owner, repo)
        check_contexts = default_check_contexts_for(repo_type)
        sys.stderr.write(
            f"Detected repo type: {repo_type} "
            f"(defaults: {', '.join(check_contexts)})\n"
        )
        if args.dry_run:
            live = fetch_latest_check_contexts(owner, repo)
            if live:
                sys.stderr.write(
                    f"  For reference, check-runs currently reported on HEAD: "
                    f"{', '.join(live)}\n"
                )
                sys.stderr.write(
                    "  If these differ from the defaults above, pass "
                    "--check-context explicitly for each name you actually need.\n"
                )
            else:
                sys.stderr.write(
                    "  No check-runs reported on HEAD yet — the first CI run "
                    "must complete before the ruleset can be enforced.\n"
                )

    # Fetch CPV-managed ruleset (if any) — update in place to preserve history
    existing = fetch_existing_ruleset(owner, repo)
    existing_id = existing.get("id") if existing else None

    # Source bypass actors to preserve, in priority order:
    #   1. The CPV-managed ruleset (most recent state)
    #   2. Any legacy/pre-existing protection ruleset (first run adoption)
    #   3. Empty (only when --reset-bypass is passed)
    existing_actors: list[BypassActor] = []
    if not args.reset_bypass:
        source: dict | None = existing
        if source is None:
            legacy = fetch_legacy_protection_rulesets(owner, repo)
            if legacy:
                # Adopt bypass actors from the first legacy ruleset found.
                source = legacy[0]
                legacy_names = ", ".join(
                    str(rs.get("name", "?")) for rs in legacy
                )
                sys.stderr.write(
                    f"⚠ Found {len(legacy)} pre-existing protection ruleset(s): "
                    f"{legacy_names}\n"
                )
                sys.stderr.write(
                    f"  Adopting bypass_actors from '{source.get('name')}' "
                    f"(id={source.get('id')}).\n"
                )
                sys.stderr.write(
                    "  After applying cpv-branch-rules, consider deleting the "
                    "legacy ruleset(s) with:\n"
                )
                for rs in legacy:
                    sys.stderr.write(
                        f"    gh api --method DELETE "
                        f"repos/{owner}/{repo}/rulesets/{rs.get('id')}\n"
                    )
        if source is not None:
            existing_actors = [
                BypassActor(
                    actor_id=a.get("actor_id"),
                    actor_type=a.get("actor_type", "Integration"),
                    bypass_mode=a.get("bypass_mode", "always"),
                )
                for a in source.get("bypass_actors", [])
            ]

    # Merge: existing + defaults + CLI additions
    defaults = build_default_bypass_actors()
    additions = [BypassActor(app_id, "Integration", "always")
                 for app_id in args.add_bypass_app_id]
    bypass_actors = merge_bypass_actors(existing_actors, defaults + additions)

    ruleset = build_ruleset(check_contexts, bypass_actors)

    if args.dry_run:
        print(f"# Dry run — {owner}/{repo}")
        print(f"# Existing ruleset: {'found (id=' + str(existing_id) + ')' if existing else 'none'}")
        print(f"# Action: {'UPDATE' if existing_id else 'CREATE'}")
        print()
        print(json.dumps(ruleset, indent=2))
        return 0

    response = apply_ruleset(owner, repo, ruleset, existing_id)
    print(f"✓ Ruleset {'updated' if existing_id else 'created'}: "
          f"{RULESET_NAME} (id={response.get('id')})")
    print(f"  Check contexts required: {', '.join(check_contexts)}")
    print(f"  Bypass actors preserved/added: {len(bypass_actors)}")
    print(f"  View: https://github.com/{owner}/{repo}/rules/{response.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
