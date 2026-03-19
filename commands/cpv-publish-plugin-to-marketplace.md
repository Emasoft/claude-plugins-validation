---
name: cpv-publish-plugin-to-marketplace
description: Register a plugin in a marketplace — validate, verify ownership, add entry, configure CI/CD notification (replaces cpv-publish-to-marketplace)
allowed-tools: Read, Bash(git:*,gh:*,uv:*), Write, Edit, Glob, Grep, AskUserQuestion
argument-hint: "<owner/plugin-repo> [--marketplace <owner/marketplace-repo>]"
agent: plugin-manager
user-invocable: true
---

# /cpv-publish-plugin-to-marketplace

Register a plugin's GitHub repo in a marketplace, with owner verification and full CI/CD pipeline setup.

> **Note:** This command replaces the former `/cpv-publish-to-marketplace`. It performs all the same operations (PAT setup, notification workflow) plus remote validation, owner verification, and marketplace catalog update.

## Usage

```
/cpv-publish-plugin-to-marketplace Emasoft/my-plugin
/cpv-publish-plugin-to-marketplace Emasoft/my-plugin --marketplace Emasoft/emasoft-plugins
```

## What It Does

### Phase 1: Verify the plugin repo
```bash
# Verify repo exists and has plugin.json
gh repo view <owner/plugin-repo> --json name,owner
gh api repos/<owner/plugin-repo>/contents/.claude-plugin/plugin.json -q .content | base64 -d
```
Extract plugin name, version, and description from plugin.json.

### Phase 2: Validate the plugin remotely
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --plugin <owner/plugin-repo>
```
If CRITICAL/MAJOR issues found, report and ask user to fix first.

### Phase 3: Determine the marketplace
If --marketplace not provided, ask the user. Verify the marketplace exists:
```bash
gh repo view <owner/marketplace-repo> --json name,owner
```

### Phase 4: Owner verification (SECURITY)
**The plugin owner and marketplace owner MUST match.** This prevents unauthorized plugins from being added.
```bash
# Extract owners
PLUGIN_OWNER=$(gh repo view <owner/plugin-repo> --json owner -q .owner.login)
MARKETPLACE_OWNER=$(gh repo view <owner/marketplace-repo> --json owner -q .owner.login)
# Compare (case-insensitive)
```
If owners don't match:
- **BLOCK** the operation
- Report: "Plugin owner '<plugin-owner>' does not match marketplace owner '<marketplace-owner>'. Cross-owner publishing requires the marketplace owner to add the plugin manually."
- Do NOT proceed

### Phase 5: Clone marketplace and add plugin entry
```bash
gh repo clone <owner/marketplace-repo> /tmp/marketplace-update -- --depth 1
```

Read `/tmp/marketplace-update/.claude-plugin/marketplace.json`. Add or update the plugin entry:
```json
{
  "name": "<plugin-name>",
  "description": "<plugin-description>",
  "version": "<plugin-version>",
  "source": {"source": "github", "repo": "<owner/plugin-repo>"}
}
```

**NEVER use local paths.** Always `{"source": "github", "repo": "..."}`.

### Phase 6: Update marketplace README
```bash
cd /tmp/marketplace-update
uv run python scripts/update_catalog.py 2>/dev/null || true
```

### Phase 7: Validate marketplace
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/validate_marketplace.py" /tmp/marketplace-update --verbose
```

### Phase 8: Commit and push marketplace changes
```bash
cd /tmp/marketplace-update
git add -A
git commit -m "feat: add <plugin-name> v<version>"
git push origin main
```

### Phase 9: Configure plugin notification (if not already set)
Check if the plugin repo has notify-marketplace.yml:
```bash
gh api repos/<owner/plugin-repo>/contents/.github/workflows/notify-marketplace.yml 2>/dev/null
```
If not present:
1. Ask user for a PAT with `repo` scope
2. `gh secret set MARKETPLACE_PAT --repo <owner/plugin-repo>`
3. Add notify-marketplace.yml to the plugin repo (if user approves)

### Phase 10: Report results

## Checklist

- [ ] Plugin repo verified (exists, has plugin.json)
- [ ] Plugin validated remotely (no CRITICAL/MAJOR)
- [ ] Marketplace repo verified
- [ ] Owner match confirmed (plugin owner == marketplace owner)
- [ ] Plugin entry added to marketplace.json (GitHub source only)
- [ ] README catalog updated
- [ ] Marketplace validated
- [ ] Changes committed and pushed
- [ ] Notification workflow configured (optional)
- [ ] Verification: `claude plugin marketplace list` shows the new plugin

## Multi-Language Support

This command works for plugins in ANY language:
- **Python**: Standard PyPI-style with pyproject.toml
- **JavaScript/TypeScript**: package.json based
- **Rust**: Cargo.toml, compiled binaries attached to releases
- **Go**: go.mod, compiled binaries attached to releases
- **Shell**: Pure bash/sh scripts
- **Mixed**: Any combination of the above

For compiled plugins (Rust, Go, C/C++), the plugin's `build-binaries.yml` workflow handles compilation and release artifacts. The marketplace only references the repo — Claude Code downloads binaries from GitHub Releases at install time.

## Error Handling

| Error | Resolution |
|-------|------------|
| Plugin repo not found | Check URL, ensure it's public or gh has access |
| Plugin has CRITICAL issues | Fix validation issues first |
| Owner mismatch | Only marketplace owner can add plugins |
| Marketplace repo not found | Create one first with `/cpv-create-github-marketplace` |
| Push fails | Check branch protection, PAT scope |
| Plugin already in marketplace | Update existing entry with new version |
