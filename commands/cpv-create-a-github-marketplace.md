---
name: cpv-create-a-github-marketplace
description: Create a GitHub marketplace with full CI/CD — scaffold, push, configure automation
allowed-tools: Read, Bash(git:*,gh:*,uv:*), Write, Edit, Glob, Grep, AskUserQuestion
argument-hint: "<owner/marketplace-name> [--add-plugin <owner/plugin-repo>]..."
agent: plugin-creator
user-invocable: true
---

# /cpv-create-a-github-marketplace

Create a complete GitHub marketplace repository for Claude Code plugins with full CI/CD automation. The marketplace is a HUB — it contains pointers to external plugin repos, never plugin code itself.

> **Note:** This command replaces the former `/cpv-setup-github-marketplace` and `/cpv-create-a-github-marketplace`. It performs the full workflow: scaffold, create GitHub repo, install CI/CD workflows, configure automation, and validate.

## Usage

```
/cpv-create-a-github-marketplace Emasoft/my-marketplace
/cpv-create-a-github-marketplace Emasoft/my-marketplace --add-plugin Emasoft/my-plugin
/cpv-create-a-github-marketplace Emasoft/my-marketplace --add-plugin Emasoft/plugin-a --add-plugin Emasoft/plugin-b
```

## What It Does

### Phase 1: Parse and validate arguments
Extract owner and marketplace name from `<owner/marketplace-name>`. Validate:
- Name is kebab-case
- Name is not reserved (e.g., "official", "anthropic", "claude")
- Owner is a valid GitHub user/org

### Phase 2: Generate marketplace scaffold
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_marketplace_repo.py" /tmp/marketplace-scaffold \
  --name <marketplace-name> \
  --owner-name <owner> \
  --description "Claude Code plugin marketplace" \
  --github-owner <owner> \
  [--add-plugin <owner/plugin-repo>]...
```

### Phase 3: Create GitHub repository
```bash
cd /tmp/marketplace-scaffold
git init
git config user.name "<owner>"
git config user.email "<nnn>+<owner>@users.noreply.github.com"
git add -A && git commit -m "Initial marketplace scaffold"
gh repo create <owner>/<marketplace-name> --public --source . --push
```

### Phase 4: Validate marketplace
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_marketplace.py" /tmp/marketplace-scaffold --verbose 2>&1 | sed 's/\x1b\[[0-9;]*m//g'
```

### Phase 5: Verify each linked plugin (if --add-plugin provided)
For each plugin repo:
1. Verify it exists: `gh repo view <owner/plugin> --json name`
2. Verify the owner matches the marketplace owner (REQUIRED — cross-owner plugins need explicit approval)
3. Verify it has `.claude-plugin/plugin.json`: `gh api repos/<owner/plugin>/contents/.claude-plugin/plugin.json`

### Phase 6: Report results

## Marketplace Architecture (CRITICAL)

The marketplace is a **hub with pointers**, NOT a monorepo:

```json
{
  "name": "my-marketplace",
  "owner": {"name": "<owner>"},
  "plugins": [
    {
      "name": "my-plugin",
      "description": "A plugin",
      "source": {"source": "github", "repo": "<owner>/my-plugin"},
      "repository": "https://github.com/<owner>/my-plugin"
    }
  ]
}
```

**NEVER** use local paths like `"source": "./plugins/..."`. Each plugin lives in its OWN GitHub repo with its own CI/CD, issues, PRs, and release cycle.

## Checklist

- [ ] Owner and name validated
- [ ] Marketplace scaffold generated
- [ ] GitHub repo created and pushed
- [ ] marketplace.json valid (hub-only, GitHub sources)
- [ ] CI/CD workflows installed (validate.yml, update-catalog.yml)
- [ ] Each linked plugin verified (exists, correct owner, has plugin.json)
- [ ] README plugin catalog generated
- [ ] Final validation passed

## Generated Files

| File | Purpose |
|------|---------|
| `.claude-plugin/marketplace.json` | Plugin registry (GitHub source pointers) |
| `README.md` | Auto-generated plugin catalog |
| `.gitignore` | Standard marketplace gitignore |
| `.github/workflows/validate.yml` | Marketplace validation CI |
| `.github/workflows/update-catalog.yml` | Auto-update README on marketplace.json changes |
| `scripts/update_catalog.py` | Regenerate README from marketplace.json |
| `git-hooks/pre-push` | Pre-push quality gate (thin bash delegator to publish.py --gate) |
| `cliff.toml` | Changelog configuration |

## Error Handling

| Error | Resolution |
|-------|------------|
| Name not kebab-case | Suggest kebab-case version |
| Reserved name | Choose a different name |
| Repo already exists | Ask: reinitialize or abort? |
| Plugin not found | Verify repo URL, check permissions |
| Owner mismatch | Warn and ask for explicit approval |
