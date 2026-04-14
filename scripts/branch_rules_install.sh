#!/usr/bin/env bash
# branch_rules_install.sh — install a GitHub branch-protection ruleset.
#
# Small, self-contained Bash port of cpv-setup-branch-rules-generic. Drop
# this file into any project's scripts/ folder and run it — no Python,
# no uv, no package install, just `gh` + `jq`.
#
# What it enforces on the default branch:
#   - Required status checks (each --check flag becomes one required context)
#   - Block deletion
#   - Block non-fast-forward (force-push)
#   - Require a pull request (but 0 manual approvals — bots can auto-merge)
#   - strict_required_status_checks_policy = false (auto-merge friendly)
#
# Bypass_actors defaults:
#   - Admin role (actor_id=5) always bypasses
#   - Any pre-existing legacy ruleset's bypass_actors are adopted verbatim
#     on first run so trust already configured through the GitHub UI survives
#   - Users add specific bot apps via --add-bypass-app-id <id> after running
#     --list-apps to discover valid IDs (hardcoding app IDs that aren't
#     installed on the owner causes HTTP 422)
#
# Idempotent: running twice is a no-op — the script looks up the managed
# ruleset by name and updates it in place on subsequent runs.
#
# Requirements:
#   - gh CLI authenticated with repo + admin:repo_hook scopes
#   - jq
#
# Usage:
#   ./branch_rules_install.sh [OWNER/REPO] --check "CI / build" [--check ...]
#
# If OWNER/REPO is omitted the script auto-detects it from the current
# directory's `git config remote.origin.url`.
#
# Examples:
#   # In your project folder (auto-detect)
#   ./scripts/branch_rules_install.sh --check "CI / build" --check "CI / test"
#
#   # Explicit repo
#   ./scripts/branch_rules_install.sh Emasoft/my-project --check "CI / test"
#
#   # Preview only
#   ./scripts/branch_rules_install.sh --check "CI / test" --dry-run
#
#   # Custom ruleset name + add a bot to bypass
#   ./scripts/branch_rules_install.sh --ruleset-name "main-protection" \
#       --check "CI / test" --add-bypass-app-id 29110

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────

RULESET_NAME="branch-rules"
REPO=""
CHECK_CONTEXTS=()
BYPASS_APP_IDS=()
DRY_RUN=0
LIST_APPS=0
RESET_BYPASS=0

# ── Helpers ─────────────────────────────────────────────────────────────────

usage() {
  cat <<'EOF'
Usage: branch_rules_install.sh [OPTIONS] [OWNER/REPO]

Create/update a GitHub branch-protection ruleset on the default branch.

If OWNER/REPO is omitted, it is auto-detected from the current directory's
git remote.origin.url.

OPTIONS
  --check CONTEXT           Required status check context. Repeatable.
                            Example: --check "CI / build"
  --ruleset-name NAME       Ruleset name (default: branch-rules)
  --add-bypass-app-id ID    GitHub App ID to add to bypass_actors. Repeatable.
  --reset-bypass            Reset bypass_actors to defaults only
                            (WARNING: removes any manually configured trust)
  --dry-run                 Print the JSON payload and exit, do not apply
  --list-apps               List installed GitHub Apps on the owner and exit
  -h, --help                Show this help and exit

REQUIREMENTS
  gh          GitHub CLI (authenticated: gh auth login)
  jq          JSON processor

EXAMPLES
  # Run in the project folder, auto-detect the repo slug
  ./branch_rules_install.sh --check "CI / build" --check "CI / test"

  # Explicit slug
  ./branch_rules_install.sh Emasoft/my-project --check "CI / test"

  # Preview without applying
  ./branch_rules_install.sh --check "CI / test" --dry-run

  # List apps so you can find IDs to bypass
  ./branch_rules_install.sh --list-apps
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 2
}

check_deps() {
  command -v gh >/dev/null 2>&1 || die "gh CLI not installed (https://cli.github.com)"
  command -v jq >/dev/null 2>&1 || die "jq not installed (brew install jq / apt install jq)"
  gh auth status >/dev/null 2>&1 || die "gh CLI is not authenticated — run 'gh auth login' first"
}

detect_repo_slug() {
  local url
  url=$(git config --get remote.origin.url 2>/dev/null || echo "")
  [ -z "$url" ] && return 0
  # Handle:
  #   git@github.com:OWNER/REPO.git          → OWNER/REPO
  #   git@github.com:OWNER/REPO              → OWNER/REPO
  #   https://github.com/OWNER/REPO.git      → OWNER/REPO
  #   https://github.com/OWNER/REPO          → OWNER/REPO
  #   ssh://git@github.com/OWNER/REPO.git    → OWNER/REPO
  local path
  path=$(echo "$url" \
    | sed -E 's#^git@[^:]+:##' \
    | sed -E 's#^ssh://[^/]+/##' \
    | sed -E 's#^https?://[^/]+/##' \
    | sed -E 's#\.git$##')
  echo "$path"
}

# ── Arg parsing ─────────────────────────────────────────────────────────────

while [ $# -gt 0 ]; do
  case "$1" in
    --check)
      [ $# -ge 2 ] || die "--check requires an argument"
      CHECK_CONTEXTS+=("$2"); shift 2 ;;
    --ruleset-name)
      [ $# -ge 2 ] || die "--ruleset-name requires an argument"
      RULESET_NAME="$2"; shift 2 ;;
    --add-bypass-app-id)
      [ $# -ge 2 ] || die "--add-bypass-app-id requires an argument"
      case "$2" in
        ''|*[!0-9]*) die "--add-bypass-app-id must be an integer, got '$2'" ;;
      esac
      BYPASS_APP_IDS+=("$2"); shift 2 ;;
    --reset-bypass)    RESET_BYPASS=1; shift ;;
    --dry-run)         DRY_RUN=1;      shift ;;
    --list-apps)       LIST_APPS=1;    shift ;;
    -h|--help)         usage; exit 0 ;;
    --) shift; break ;;
    -*) die "Unknown option: $1" ;;
    */*)
      [ -z "$REPO" ] || die "Multiple repo slugs: '$REPO' and '$1'"
      REPO="$1"; shift ;;
    *)  die "Invalid argument: $1" ;;
  esac
done

check_deps

# ── Resolve repo slug ───────────────────────────────────────────────────────

if [ -z "$REPO" ]; then
  REPO=$(detect_repo_slug)
fi
if [ -z "$REPO" ]; then
  die "OWNER/REPO not provided and could not auto-detect from git remote.origin.url"
fi
if [[ "$REPO" != */* ]]; then
  die "repo slug must be OWNER/REPO, got '$REPO'"
fi
OWNER="${REPO%%/*}"
REPO_NAME="${REPO#*/}"
if [ -z "$OWNER" ] || [ -z "$REPO_NAME" ]; then
  die "repo slug must be OWNER/REPO, got '$REPO'"
fi

# ── --list-apps mode ────────────────────────────────────────────────────────

if [ $LIST_APPS -eq 1 ]; then
  echo "GitHub Apps installed on $OWNER:"
  gh api /user/installations --paginate 2>/dev/null \
    | jq -r '.installations[]? | "  app_id=\(.app_id // .id)\tslug=\(.app_slug // "?")\taccount=\(.account.login // "?")"' \
    | sort -u || echo "  (no apps found on user account — try --org scope)"
  echo
  echo "To bypass a specific app in the ruleset, run:"
  echo "  $0 $REPO --check '<ctx>' --add-bypass-app-id <app_id>"
  exit 0
fi

# ── Require at least one --check ────────────────────────────────────────────

if [ ${#CHECK_CONTEXTS[@]} -eq 0 ]; then
  echo "ERROR: at least one --check CONTEXT is required." >&2
  echo "Example: --check 'CI / build' --check 'CI / test'" >&2
  echo >&2
  usage >&2
  exit 1
fi

echo "Target: $OWNER/$REPO_NAME"
echo "Ruleset name: $RULESET_NAME"
echo "Required check contexts: ${CHECK_CONTEXTS[*]}"

# ── Find existing managed ruleset (idempotent update) ──────────────────────

ALL_RULESETS_JSON=$(gh api "repos/$OWNER/$REPO_NAME/rulesets" --paginate 2>/dev/null || echo "[]")

EXISTING_ID=$(echo "$ALL_RULESETS_JSON" \
  | jq -r --arg name "$RULESET_NAME" '[.[] | select(.name == $name)] | first.id // empty')

EXISTING_BYPASS_JSON="[]"
if [ -n "$EXISTING_ID" ]; then
  echo "Found existing managed ruleset (id=$EXISTING_ID) — will UPDATE in place"
  if [ $RESET_BYPASS -eq 0 ]; then
    EXISTING_BYPASS_JSON=$(gh api "repos/$OWNER/$REPO_NAME/rulesets/$EXISTING_ID" 2>/dev/null \
      | jq -c '.bypass_actors // []')
  fi
else
  echo "No existing managed ruleset — will CREATE"
  # Legacy adoption: if a non-managed protection-shaped ruleset exists,
  # preserve its bypass_actors so trust already configured via the UI survives.
  if [ $RESET_BYPASS -eq 0 ]; then
    LEGACY_IDS=$(echo "$ALL_RULESETS_JSON" \
      | jq -r --arg name "$RULESET_NAME" '.[] | select(.name != $name) | .id')
    for LEGACY_ID in $LEGACY_IDS; do
      LEGACY_FULL=$(gh api "repos/$OWNER/$REPO_NAME/rulesets/$LEGACY_ID" 2>/dev/null) || continue
      HAS_PROTECTION=$(echo "$LEGACY_FULL" \
        | jq -r '.rules // [] | map(.type) | any(. == "pull_request" or . == "required_status_checks" or . == "required_signatures" or . == "code_quality")')
      if [ "$HAS_PROTECTION" = "true" ]; then
        LEGACY_NAME=$(echo "$LEGACY_FULL" | jq -r '.name // "?"')
        echo "⚠ Adopting bypass_actors from legacy ruleset: $LEGACY_NAME (id=$LEGACY_ID)"
        EXISTING_BYPASS_JSON=$(echo "$LEGACY_FULL" | jq -c '.bypass_actors // []')
        echo "  After applying this ruleset, consider deleting the legacy one with:"
        echo "    gh api --method DELETE repos/$OWNER/$REPO_NAME/rulesets/$LEGACY_ID"
        break
      fi
    done
  fi
fi

# ── Build bypass_actors (existing + defaults + user-added, deduped) ────────

# Admin role (always valid, doesn't require app installation)
DEFAULT_BYPASS='[{"actor_id":5,"actor_type":"RepositoryRole","bypass_mode":"always"}]'

# User-added integrations
USER_BYPASS="[]"
for APP_ID in "${BYPASS_APP_IDS[@]}"; do
  USER_BYPASS=$(echo "$USER_BYPASS" \
    | jq -c --arg id "$APP_ID" \
        '. + [{actor_id: ($id | tonumber), actor_type: "Integration", bypass_mode: "always"}]')
done

BYPASS_ACTORS=$(jq -c -n \
  --argjson existing "$EXISTING_BYPASS_JSON" \
  --argjson defaults "$DEFAULT_BYPASS" \
  --argjson user "$USER_BYPASS" \
  '($existing + $defaults + $user) | unique_by([.actor_type, .actor_id])')

# ── Build required_status_checks array ─────────────────────────────────────

CHECK_CONTEXTS_JSON="[]"
for CTX in "${CHECK_CONTEXTS[@]}"; do
  CHECK_CONTEXTS_JSON=$(echo "$CHECK_CONTEXTS_JSON" \
    | jq -c --arg ctx "$CTX" '. + [{context: $ctx}]')
done

# ── Build the full ruleset payload ─────────────────────────────────────────

PAYLOAD=$(jq -n \
  --arg name "$RULESET_NAME" \
  --argjson bypass_actors "$BYPASS_ACTORS" \
  --argjson check_contexts "$CHECK_CONTEXTS_JSON" \
  '{
    name: $name,
    target: "branch",
    enforcement: "active",
    conditions: {
      ref_name: {
        include: ["~DEFAULT_BRANCH"],
        exclude: []
      }
    },
    rules: [
      {type: "deletion"},
      {type: "non_fast_forward"},
      {
        type: "pull_request",
        parameters: {
          required_approving_review_count: 0,
          dismiss_stale_reviews_on_push: false,
          require_code_owner_review: false,
          require_last_push_approval: false,
          required_review_thread_resolution: false,
          allowed_merge_methods: ["merge", "squash", "rebase"]
        }
      },
      {
        type: "required_status_checks",
        parameters: {
          strict_required_status_checks_policy: false,
          required_status_checks: $check_contexts
        }
      }
    ],
    bypass_actors: $bypass_actors
  }')

# ── Dry-run ─────────────────────────────────────────────────────────────────

if [ $DRY_RUN -eq 1 ]; then
  ACTION="CREATE"
  [ -n "$EXISTING_ID" ] && ACTION="UPDATE (id=$EXISTING_ID)"
  echo
  echo "# Dry run — $OWNER/$REPO_NAME"
  echo "# Action: $ACTION"
  echo "$PAYLOAD" | jq .

  # Diagnostic: actual check-run names reported on HEAD
  LIVE=$(gh api "repos/$OWNER/$REPO_NAME/commits/HEAD/check-runs" \
    --jq '.check_runs[].name' 2>/dev/null | sort -u || true)
  echo
  if [ -n "$LIVE" ]; then
    echo "# Diagnostic — check-runs currently reported on HEAD:" >&2
    # Prefix each line with "#   " without invoking sed (SC2001)
    while IFS= read -r _line; do echo "#   $_line" >&2; done <<< "$LIVE"
    echo "# If your --check values don't match any of these, the ruleset" >&2
    echo "# will block all PRs until the first matching check runs." >&2
  else
    echo "# Diagnostic — no check-runs reported on HEAD yet." >&2
    echo "# The first CI run must complete before the ruleset can pass." >&2
  fi
  exit 0
fi

# ── Apply ───────────────────────────────────────────────────────────────────

if [ -n "$EXISTING_ID" ]; then
  RESULT=$(echo "$PAYLOAD" \
    | gh api --method PUT "repos/$OWNER/$REPO_NAME/rulesets/$EXISTING_ID" --input -)
  NEW_ID=$(echo "$RESULT" | jq -r '.id')
  echo "✓ Ruleset updated: $RULESET_NAME (id=$NEW_ID)"
else
  RESULT=$(echo "$PAYLOAD" \
    | gh api --method POST "repos/$OWNER/$REPO_NAME/rulesets" --input -)
  NEW_ID=$(echo "$RESULT" | jq -r '.id')
  echo "✓ Ruleset created: $RULESET_NAME (id=$NEW_ID)"
fi

BYPASS_COUNT=$(echo "$BYPASS_ACTORS" | jq 'length')
echo "  Required checks: ${CHECK_CONTEXTS[*]}"
echo "  Bypass actors:   $BYPASS_COUNT"
echo "  View:            https://github.com/$OWNER/$REPO_NAME/rules/$NEW_ID"
