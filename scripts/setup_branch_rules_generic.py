#!/usr/bin/env python3
"""Generic GitHub branch-protection ruleset installer — works on any repo.

This is the project-agnostic variant of setup_branch_rules.py (the CPV-specific
version with plugin/marketplace auto-detection). Use this one for any GitHub
repository you want to enforce CI-passes-before-merge on.

Copy this file into your project's scripts/ folder and run it directly, or
invoke it via uvx without installing:

    uvx --from git+https://github.com/Emasoft/claude-plugins-validation \\
        branch-rules-install <OWNER>/<REPO> --check "CI / build" --check "CI / test"

What the ruleset enforces
    - Required status checks — each --check flag becomes a required status context
    - Block default-branch deletion
    - Block non-fast-forward (force-push)
    - Require a pull request for every change (no manual approval required)
    - Auto-merge friendly: strict policy OFF so GitHub can auto-merge when CI
      turns green without forcing a rebase loop

Why no hardcoded bot bypass list
    GitHub's Rulesets API rejects any bypass actor app_id that is not installed
    on the target owner's account with HTTP 422. Since every owner has a
    different set of installed apps, the ONLY portable defaults are:
        - admin role (actor_id=5, always valid)
        - whatever bypass_actors an existing legacy ruleset already had
          (auto-adopted on first run)
    Users add specific integrations (Dependabot, Claude Code, Copilot, etc.)
    via --add-bypass-app-id after running --list-apps to discover the right
    IDs for their account.

Idempotent
    Running the script twice is a no-op: the second run finds the existing
    ruleset (looked up by the name passed to --ruleset-name, default
    'branch-rules') and updates it in place.

Requirements
    - gh CLI authenticated with a token that has `repo` and `admin:repo_hook`
      scopes on the target repository
    - Python 3.10+
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass

# ── Constants ─────────────────────────────────────────────────────────────

DEFAULT_RULESET_NAME = "branch-rules"

# RepositoryRole ids are well-known GitHub constants (1=read, 2=triage, 4=write,
# 5=maintain). Maintain covers maintainer-style merges without requiring
# manual review and is safe to bypass by default.
DEFAULT_ADMIN_ROLE_IDS: list[int] = [5]


# ── Shell helpers ─────────────────────────────────────────────────────────

class ShellError(RuntimeError):
    """Raised when a subprocess returns non-zero."""


def run(
    cmd: list[str],
    *,
    check: bool = True,
    input_data: str | None = None,
) -> subprocess.CompletedProcess[str]:
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


def parse_repo_slug(slug: str) -> tuple[str, str]:
    if "/" not in slug:
        raise SystemExit(f"ERROR: repo slug must be OWNER/REPO, got '{slug}'")
    owner, repo = slug.split("/", 1)
    if not owner or not repo:
        raise SystemExit(f"ERROR: repo slug must be OWNER/REPO, got '{slug}'")
    return owner, repo


# ── Ruleset payload ───────────────────────────────────────────────────────

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


def build_default_bypass_actors() -> list[BypassActor]:
    """Return the admin-only default bypass_actors list for new rulesets."""
    return [BypassActor(role_id, "RepositoryRole", "always") for role_id in DEFAULT_ADMIN_ROLE_IDS]


def merge_bypass_actors(
    existing: list[BypassActor],
    additions: list[BypassActor],
) -> list[BypassActor]:
    """Merge two bypass_actor lists, preserving existing entries verbatim.

    Dedupes by (actor_type, actor_id). Existing entries win on collision so
    any bypass_mode the user has manually downgraded (always → pull_request)
    is not silently upgraded back.
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
    name: str,
    check_contexts: list[str],
    bypass_actors: list[BypassActor],
) -> dict:
    """Build the full ruleset payload for the POST/PUT call."""
    return {
        "name": name,
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {
                "include": ["~DEFAULT_BRANCH"],
                "exclude": [],
            },
        },
        "rules": [
            # Protect the default branch itself
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            # Require a pull request (but NOT a manual approval)
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
            # The real gate: required status checks
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


# ── Ruleset CRUD operations ──────────────────────────────────────────────

def _fetch_all_rulesets(owner: str, repo: str) -> list[dict]:
    """Return every ruleset on the repo (list view — no rules/bypass arrays)."""
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


def fetch_managed_ruleset(owner: str, repo: str, name: str) -> dict | None:
    """Return the ruleset with the given name, or None if not found."""
    for rs in _fetch_all_rulesets(owner, repo):
        if rs.get("name") == name:
            return _fetch_full_ruleset(owner, repo, rs["id"])
    return None


def fetch_legacy_protection_rulesets(
    owner: str, repo: str, managed_name: str,
) -> list[dict]:
    """Return non-managed rulesets that look like branch-protection rules.

    Used to adopt bypass_actors from a pre-existing ruleset on first run so
    the user doesn't lose trust already configured through the GitHub UI.
    """
    legacy: list[dict] = []
    for rs in _fetch_all_rulesets(owner, repo):
        if rs.get("name") == managed_name:
            continue
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

    Queries /user/installations for the authenticated user and
    /orgs/{owner}/installations if the owner is an organization. Results
    are deduplicated by app_id.
    """
    apps: list[dict] = []

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

    seen: set[int] = set()
    unique: list[dict] = []
    for app in apps:
        app_id = app.get("app_id") or app.get("id")
        if app_id is None or app_id in seen:
            continue
        seen.add(app_id)
        unique.append(app)
    return unique


def fetch_latest_check_contexts(owner: str, repo: str) -> list[str]:
    """Return the distinct check-run names currently reported on the repo's HEAD.

    Printed as a diagnostic during --dry-run so the caller can sanity-check
    that the --check values they passed match the names GitHub actually
    reports. Not used as authoritative defaults because check-run names and
    ruleset contexts have different formats.
    """
    result = run(
        [
            "gh", "api",
            f"repos/{owner}/{repo}/commits/HEAD/check-runs",
            "--jq", ".check_runs[].name",
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


def apply_ruleset(
    owner: str,
    repo: str,
    ruleset: dict,
    existing_id: int | None,
) -> dict:
    """POST (create) or PUT (update) the ruleset. Returns the server response."""
    if existing_id is None:
        endpoint = f"repos/{owner}/{repo}/rulesets"
        method = "POST"
    else:
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
            "Create/update a GitHub branch-protection ruleset that enforces "
            "CI as a required status check on the default branch. "
            "Idempotent, auto-merge friendly, project-agnostic."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Simplest: require three status checks on the default branch
  %(prog)s Emasoft/my-project --check "CI / build" --check "CI / test" --check "CI / lint"

  # Preview without applying
  %(prog)s Emasoft/my-project --check "CI / build" --dry-run

  # List installed GitHub Apps so you can pick which ones to bypass
  %(prog)s Emasoft/my-project --list-apps

  # Add a specific bot to bypass (e.g. Dependabot on YOUR account — run --list-apps first)
  %(prog)s Emasoft/my-project --check "CI / test" --add-bypass-app-id 29110

  # Use a custom ruleset name (default: "branch-rules")
  %(prog)s Emasoft/my-project --check "CI / test" --ruleset-name "my-project-rules"
""",
    )
    p.add_argument("repo", help="Target repo slug (OWNER/REPO)")
    p.add_argument(
        "--check",
        action="append",
        dest="check_contexts",
        default=[],
        metavar="CONTEXT",
        help=(
            "Required status check context name. Repeatable. "
            "Format must match exactly what GitHub reports "
            "(typically 'workflow_name / job_name')."
        ),
    )
    p.add_argument(
        "--ruleset-name",
        default=DEFAULT_RULESET_NAME,
        help=f"Name for the managed ruleset (default: {DEFAULT_RULESET_NAME!r})",
    )
    p.add_argument(
        "--add-bypass-app-id",
        action="append",
        type=int,
        default=[],
        metavar="APP_ID",
        help=(
            "GitHub App ID to add to bypass_actors. Repeatable. "
            "Run --list-apps first to find valid IDs for your account."
        ),
    )
    p.add_argument(
        "--reset-bypass",
        action="store_true",
        help=(
            "Reset bypass_actors to defaults only (admin role). "
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


def cmd_list_apps(owner: str) -> int:
    print(f"GitHub Apps installed on {owner}:")
    print()
    apps = list_installed_apps(owner)
    if not apps:
        print("  (no apps found — or gh token lacks org:read scope)")
        return 0
    for app in apps:
        app_id = app.get("app_id") or app.get("id")
        slug = app.get("app_slug") or (app.get("account") or {}).get("login") or "?"
        account = (app.get("account") or {}).get("login") or "?"
        print(f"  app_id={app_id:<8}  slug={slug:<30}  account={account}")
    print()
    print("To add one of these to the ruleset bypass list:")
    print("  branch-rules-install <OWNER>/<REPO> --check '...' --add-bypass-app-id <app_id>")
    return 0


def main() -> int:
    args = parse_args()
    check_gh_available()
    check_gh_auth()

    owner, repo = parse_repo_slug(args.repo)

    if args.list_apps:
        return cmd_list_apps(owner)

    if not args.check_contexts:
        sys.stderr.write(
            "ERROR: at least one --check CONTEXT is required.\n"
            "Example: --check 'CI / build' --check 'CI / test'\n\n"
            "Tip: to see what check contexts GitHub is currently reporting on "
            f"{owner}/{repo}, use --dry-run after passing any --check value.\n"
        )
        return 1

    check_contexts: list[str] = args.check_contexts
    ruleset_name: str = args.ruleset_name
    sys.stderr.write(
        f"Target: {owner}/{repo}\n"
        f"Ruleset name: {ruleset_name}\n"
        f"Required check contexts: {', '.join(check_contexts)}\n"
    )

    # Fetch managed ruleset (update in place if found)
    existing = fetch_managed_ruleset(owner, repo, ruleset_name)
    existing_id = existing.get("id") if existing else None

    # Preserve bypass_actors from existing/legacy ruleset unless --reset-bypass
    existing_actors: list[BypassActor] = []
    if not args.reset_bypass:
        source: dict | None = existing
        if source is None:
            legacy = fetch_legacy_protection_rulesets(owner, repo, ruleset_name)
            if legacy:
                source = legacy[0]
                legacy_names = ", ".join(str(rs.get("name", "?")) for rs in legacy)
                sys.stderr.write(
                    f"⚠ Found {len(legacy)} pre-existing protection ruleset(s): "
                    f"{legacy_names}\n"
                )
                sys.stderr.write(
                    f"  Adopting bypass_actors from '{source.get('name')}' "
                    f"(id={source.get('id')}).\n"
                )
                sys.stderr.write(
                    "  After applying this ruleset, consider deleting the "
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

    defaults = build_default_bypass_actors()
    additions = [BypassActor(app_id, "Integration", "always")
                 for app_id in args.add_bypass_app_id]
    bypass_actors = merge_bypass_actors(existing_actors, defaults + additions)

    ruleset = build_ruleset(ruleset_name, check_contexts, bypass_actors)

    if args.dry_run:
        print(f"# Dry run — {owner}/{repo}")
        print(f"# Existing ruleset: {'found (id=' + str(existing_id) + ')' if existing else 'none'}")
        print(f"# Action: {'UPDATE' if existing_id else 'CREATE'}")
        print()
        print(json.dumps(ruleset, indent=2))
        live = fetch_latest_check_contexts(owner, repo)
        if live:
            sys.stderr.write(
                f"\n# Diagnostic — check-runs currently reported on HEAD: "
                f"{', '.join(live)}\n"
            )
            sys.stderr.write(
                "# If your --check values don't match any of these, the "
                "ruleset will never pass.\n"
            )
        else:
            sys.stderr.write(
                "\n# Diagnostic — no check-runs reported on HEAD yet. "
                "The first CI run must complete before the ruleset can be enforced.\n"
            )
        return 0

    response = apply_ruleset(owner, repo, ruleset, existing_id)
    print(f"✓ Ruleset {'updated' if existing_id else 'created'}: "
          f"{ruleset_name} (id={response.get('id')})")
    print(f"  Check contexts required: {', '.join(check_contexts)}")
    print(f"  Bypass actors preserved/added: {len(bypass_actors)}")
    print(f"  View: https://github.com/{owner}/{repo}/rules/{response.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
