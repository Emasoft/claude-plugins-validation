# Troubleshooting Guide

Diagnostic reference for common issues encountered when setting up and operating a Claude Code plugin marketplace.

## Table of Contents

- [Authentication Issues](#authentication-issues)
- [Repository Creation Failures](#repository-creation-failures)
- [CI/CD Pipeline Issues](#cicd-pipeline-issues)
- [Notification Chain Failures](#notification-chain-failures)
- [Validation Failures](#validation-failures)
- [Secret Configuration Issues](#secret-configuration-issues)
- [Common Error Messages](#common-error-messages)
- [Debug Commands](#debug-commands)

---

## Authentication Issues

### gh CLI Not Authenticated

**Symptom:** Commands like `gh repo create` or `gh secret set` fail with `not logged in` or `authentication required`.

**Cause:** The GitHub CLI has not been authenticated, or the authentication token has expired.

**Fix:**
```bash
# Check current auth status
gh auth status

# If not logged in, authenticate interactively
gh auth login

# Verify scopes include repo, workflow, and admin:org (if using org repos)
gh auth status --show-token
```

### Token Missing Required Scopes

**Symptom:** API calls return `403 Forbidden` or `Resource not accessible by integration`.

**Cause:** The personal access token or OAuth token lacks the permissions needed for the operation.

**Fix:**
```bash
# Refresh authentication with the required scopes
gh auth refresh -s repo,workflow

# Verify the scopes
gh auth status
```

---

## Repository Creation Failures

### Repository Already Exists

**Symptom:** `gh repo create` fails with `Name already exists on this account` or HTTP 422.

**Cause:** A repository with the same name already exists under the target owner.

**Fix:**
```bash
# Check if the repository exists
gh repo view OWNER/REPO-NAME 2>/dev/null && echo "Exists" || echo "Does not exist"

# If it exists and you want to use it, skip creation and proceed with setup
# If it exists and is stale, delete it first (requires confirmation)
gh repo delete OWNER/REPO-NAME --yes
```

### Insufficient Permissions for Organization

**Symptom:** Repository creation under an organization fails with `403` or `Must have admin rights`.

**Cause:** Your account does not have permission to create repositories in the organization.

**Fix:**
- Ask an organization admin to grant you the `Repository creator` role
- Or have the admin create the repository and grant you push access

---

## CI/CD Pipeline Issues

### Workflow Not Triggering on Push

**Symptom:** Pushing to `main` does not trigger the expected GitHub Actions workflow.

**Cause:** The workflow file may have a syntax error, the paths filter may not match the changed files, or Actions may be disabled for the repository.

**Fix:**
```bash
# Verify Actions are enabled
gh api repos/OWNER/REPO/actions/permissions --jq '.enabled'

# Check workflow file syntax by listing workflows
gh workflow list --repo OWNER/REPO

# Verify the push was to the correct branch
git log --oneline -1

# Check if the changed file matches the paths filter in the workflow
git diff --name-only HEAD~1
```

### Workflow Fails with Permission Denied

**Symptom:** Workflow step fails with `Error: Resource not accessible by integration` or `HttpError: Resource not accessible`.

**Cause:** The workflow does not have write permissions. GitHub defaults to read-only for `GITHUB_TOKEN` in forked repositories and some org settings.

**Fix:**
1. Go to the repository **Settings > Actions > General**
2. Under **Workflow permissions**, select **Read and write permissions**
3. Click **Save**

Or add explicit permissions in the workflow YAML:

```yaml
permissions:
  contents: write
```

---

## Notification Chain Failures

### Dispatch Event Not Received by Marketplace

**Symptom:** The plugin repo's `notify-marketplace.yml` workflow runs successfully, but the marketplace repo's `update-submodules.yml` does not trigger.

**Cause:** The `repository_dispatch` event was not delivered. Common reasons: wrong repository target, expired PAT, or event type mismatch.

**Fix:**
```bash
# 1. Verify the target repository in the workflow file
grep "repository:" .github/workflows/notify-marketplace.yml

# 2. Verify the event type matches between sender and receiver
grep "event-type:" .github/workflows/notify-marketplace.yml
grep "event_type:" /path/to/marketplace/.github/workflows/update-submodules.yml

# 3. Check if the PAT is valid
gh auth status

# 4. Test the dispatch manually
gh api repos/OWNER/MARKETPLACE-REPO/dispatches \
  -f event_type="plugin-updated" \
  -f client_payload='{"plugin":"test","version":"0.0.1"}'
```

### Marketplace Workflow Triggers but Submodule Not Updated

**Symptom:** The `update-submodules.yml` workflow runs but the submodule still points to the old commit.

**Cause:** The submodule URL may be incorrect, or the plugin repo's `main` branch has no new commits relative to what the submodule already references.

**Fix:**
```bash
# Check the submodule URL
git config -f .gitmodules --get submodule.plugins/PLUGIN-NAME.url

# Manually update the submodule and verify
git submodule update --remote plugins/PLUGIN-NAME
git diff --submodule
```

---

## Validation Failures

### marketplace.json Parse Error

**Symptom:** Validation script reports `JSONDecodeError` or `Invalid JSON`.

**Cause:** The `marketplace.json` file contains a syntax error (trailing comma, unquoted key, missing bracket).

**Fix:**
```bash
# Validate JSON syntax
python -m json.tool .claude-plugin/marketplace.json

# Or use jq for detailed error location
jq . .claude-plugin/marketplace.json
```

### Plugin Repository Missing plugin.json

**Symptom:** Validation reports `plugin.json not found` or the sync script skips a plugin.

**Cause:** The plugin submodule does not contain `.claude-plugin/plugin.json` at its root.

**Fix:**
```bash
# Check if the file exists in the submodule
ls plugins/PLUGIN-NAME/.claude-plugin/plugin.json

# If missing, the plugin repo needs to be fixed first
# Navigate to the plugin repo and create the required structure
```

### Version Mismatch Between plugin.json and marketplace.json

**Symptom:** Validation warns that the version in `marketplace.json` does not match the version in the submodule's `plugin.json`.

**Cause:** The sync script was not run after a submodule update, or `marketplace.json` was edited manually with an incorrect version.

**Fix:**
```bash
# Re-run the sync script to reconcile versions
python scripts/sync_marketplace_versions.py

# Verify the versions now match
jq '.version' plugins/PLUGIN-NAME/.claude-plugin/plugin.json
jq '.plugins[] | select(.name == "PLUGIN-NAME") | .version' .claude-plugin/marketplace.json
```

---

## Secret Configuration Issues

### Secret Not Set

**Symptom:** Workflow fails with `Error: Input required and not supplied: token` or the dispatch step silently fails.

**Cause:** The `MARKETPLACE_PAT` secret was never set in the plugin repository, or it was set with a different name.

**Fix:**
```bash
# List secrets to verify the name
gh secret list --repo OWNER/PLUGIN-REPO

# Set the secret if missing
gh secret set MARKETPLACE_PAT --repo OWNER/PLUGIN-REPO --body "ghp_YOUR_TOKEN"
```

### Secret Set with Wrong Name

**Symptom:** The workflow references `secrets.MARKETPLACE_PAT` but the secret is named differently (e.g., `PAT_TOKEN`).

**Cause:** The secret name in the repository does not match the name referenced in the workflow YAML.

**Fix:**
Either rename the secret in the workflow file or delete the old secret and create one with the correct name:

```bash
# Delete the incorrectly named secret
gh secret delete PAT_TOKEN --repo OWNER/PLUGIN-REPO

# Set with the correct name
gh secret set MARKETPLACE_PAT --repo OWNER/PLUGIN-REPO --body "ghp_YOUR_TOKEN"
```

### Token Expired

**Symptom:** Dispatch step fails with `401 Unauthorized` or `Bad credentials`.

**Cause:** The fine-grained personal access token has passed its expiration date.

**Fix:**
1. Generate a new token at `https://github.com/settings/tokens?type=beta`
2. Update the secret in every plugin repository that uses it:
   ```bash
   gh secret set MARKETPLACE_PAT --repo OWNER/PLUGIN-REPO --body "ghp_NEW_TOKEN"
   ```

---

## Common Error Messages

| Error Message | Cause | Resolution |
|---------------|-------|------------|
| `fatal: not a git repository` | Command run outside a git repo | `cd` into the correct directory |
| `fatal: 'plugins/X' already exists in the index` | Submodule already added | Remove first or skip adding |
| `error: Server does not allow request for unadvertised object` | Submodule ref points to a force-pushed/deleted commit | `git submodule update --remote` to reset |
| `remote: Permission to OWNER/REPO.git denied` | Push access not granted | Check collaborator/team permissions |
| `GraphQL: Could not resolve to a Repository` | Wrong owner/repo name in gh command | Verify the exact owner and repo name |
| `HTTP 404: Not Found` on dispatch | Target repo does not exist or token cannot see it | Check repo name and token repo access list |
| `HTTP 422: Validation Failed` on repo create | Repo name invalid or already taken | Use a different name or delete the existing repo |
| `jq: error: null is not iterable` | Missing field in JSON | Check that the JSON file has all required fields |

---

## Debug Commands

A collection of diagnostic commands for investigating marketplace issues.

### Check Overall Status

```bash
# Marketplace repository health
gh repo view OWNER/MARKETPLACE-REPO --json name,defaultBranchRef,isPrivate

# List all submodules and their current commits
git submodule status

# Validate marketplace.json structure
python -m json.tool .claude-plugin/marketplace.json > /dev/null && echo "Valid JSON" || echo "Invalid JSON"
```

### Inspect Workflows

```bash
# List all workflows in a repository
gh workflow list --repo OWNER/REPO

# View recent runs for a specific workflow
gh run list --repo OWNER/REPO --workflow validate.yml --limit 5

# View logs for a failed run (use the run ID from the list above)
gh run view RUN_ID --repo OWNER/REPO --log-failed
```

### Inspect Secrets

```bash
# List secrets (names only, values are never exposed)
gh secret list --repo OWNER/PLUGIN-REPO

# Verify the secret name matches the workflow reference
grep "secrets\." .github/workflows/notify-marketplace.yml
```

### Test Dispatch Manually

```bash
# Send a test repository_dispatch event to the marketplace
gh api repos/OWNER/MARKETPLACE-REPO/dispatches \
  -f event_type="plugin-updated" \
  -f client_payload='{"plugin":"test-plugin","version":"0.0.0"}'

# If successful, this returns HTTP 204 with no body
# Then check the marketplace repo for triggered runs
gh run list --repo OWNER/MARKETPLACE-REPO --limit 3
```

### Inspect Submodule State

```bash
# Show submodule URLs and branches
git config -f .gitmodules --list

# Show what commit each submodule points to
git submodule foreach 'echo "$name: $(git rev-parse HEAD)"'

# Check if a submodule is behind its remote
git submodule foreach 'git fetch origin main && echo "$name behind by $(git rev-list HEAD..origin/main --count) commits"'
```

### Validate End-to-End

```bash
# Full validation pipeline
python scripts/validate_marketplace_pipeline.py .claude-plugin/marketplace.json

# Check each plugin's plugin.json individually
git submodule foreach 'python -m json.tool .claude-plugin/plugin.json > /dev/null 2>&1 && echo "$name: valid" || echo "$name: INVALID"'

# Compare marketplace.json versions with actual submodule versions
git submodule foreach 'echo "$name: $(jq -r .version .claude-plugin/plugin.json 2>/dev/null || echo MISSING)"'
```
