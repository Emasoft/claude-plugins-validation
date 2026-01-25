# Marketplace Publishing Pipeline Validation

## Table of Contents

- 1. Architecture Overview
  - 1.1 3-Repo Architecture
  - 1.2 Update Flow
- 2. Required Components
  - 2.1 Marketplace Repository Requirements
  - 2.2 Plugin Repository Requirements
  - 2.3 Secrets Configuration
- 3. Validation Commands
  - 3.1 Full Pipeline Validation
  - 3.2 Component-Level Validation
- 4. Auto-Setup
  - 4.1 Automatic Setup Script
  - 4.2 Manual Setup Steps
- 5. Troubleshooting
  - 5.1 notify-marketplace.yml Issues
  - 5.2 update-submodules.yml Issues
  - 5.3 Version Sync Issues
- 6. Scoring System
  - 6.1 Category Weights
  - 6.2 Grade Calculation

---

## 1. Architecture Overview

### 1.1 3-Repo Architecture

The marketplace publishing pipeline uses a 3-repo architecture:

1. **Marketplace Repository** - Central hub containing:
   - `.claude-plugin/marketplace.json` - Plugin registry
   - `.gitmodules` - Submodule definitions
   - `.github/workflows/update-submodules.yml` - Auto-update workflow
   - `scripts/sync_marketplace_versions.py` - Version sync script

2. **Plugin Repositories** (N plugins) - Each containing:
   - `.claude-plugin/plugin.json` - Plugin manifest
   - `.github/workflows/notify-marketplace.yml` - Notification workflow

### 1.2 Update Flow

```
Plugin Push → notify-marketplace.yml → repository_dispatch →
  update-submodules.yml → sync versions → commit → marketplace updated
```

---

## 2. Required Components

### 2.1 Marketplace Repository Requirements

| Component | Path | Purpose |
|-----------|------|---------|
| marketplace.json | `.claude-plugin/marketplace.json` | Plugin registry |
| .gitmodules | `.gitmodules` | Submodule definitions |
| update-submodules.yml | `.github/workflows/update-submodules.yml` | Auto-update on dispatch |
| validate.yml | `.github/workflows/validate.yml` | CI validation |
| sync script | `scripts/sync_marketplace_versions.py` | Version sync |

### 2.2 Plugin Repository Requirements

| Component | Path | Purpose |
|-----------|------|---------|
| plugin.json | `.claude-plugin/plugin.json` | Plugin manifest |
| notify-marketplace.yml | `.github/workflows/notify-marketplace.yml` | Notify marketplace on push |

### 2.3 Secrets Configuration

**MARKETPLACE_PAT is required because:**
- `GITHUB_TOKEN` cannot trigger workflows in other repos
- `GITHUB_TOKEN` cannot bypass branch protection
- PAT must have `repo` and `workflow` scopes

**Setup:**
```bash
# In each plugin repo
gh secret set MARKETPLACE_PAT --repo OWNER/plugin-name

# In marketplace repo
gh secret set MARKETPLACE_PAT --repo OWNER/marketplace-name
```

---

## 3. Validation Commands

### 3.1 Full Pipeline Validation

```bash
# Validate entire pipeline
uv run python scripts/validate_marketplace_pipeline.py /path/to/marketplace --verbose

# Output includes:
# - Marketplace structure score
# - Submodule health score
# - Workflow presence and correctness
# - Version sync status
# - Overall grade (A-F)
```

### 3.2 Component-Level Validation

```bash
# Marketplace structure only
uv run python scripts/validate_marketplace.py /path/to/marketplace

# Individual plugin
uv run python scripts/validate_plugin.py /path/to/plugin --verbose

# Check workflow files
gh workflow list --repo OWNER/REPO
gh run list --repo OWNER/REPO --limit 5
```

---

## 4. Auto-Setup

### 4.1 Automatic Setup Script

```bash
# Setup entire pipeline automatically
uv run python scripts/setup_marketplace_automation.py /path/to/marketplace \
  --plugins "plugin-a,plugin-b" \
  --setup-all

# This will:
# 1. Create/update marketplace.json
# 2. Create .github/workflows/update-submodules.yml
# 3. Create .github/workflows/validate.yml
# 4. Create scripts/sync_marketplace_versions.py
# 5. Create notify-marketplace.yml in each plugin
# 6. Print instructions for PAT configuration
```

### 4.2 Manual Setup Steps

**Step 1: Marketplace Repository**
```bash
mkdir -p .github/workflows scripts .claude-plugin

# Copy workflow templates
cp templates/github-workflows/update-submodules.yml .github/workflows/
cp templates/github-workflows/validate-marketplace.yml .github/workflows/validate.yml
cp templates/scripts/sync_marketplace_versions.py scripts/

# Create marketplace.json
echo '{"name":"my-marketplace","plugins":[]}' > .claude-plugin/marketplace.json
```

**Step 2: Add Plugins as Submodules**
```bash
git submodule add https://github.com/OWNER/plugin-a.git plugin-a
git submodule add https://github.com/OWNER/plugin-b.git plugin-b
```

**Step 3: Plugin Repositories**
```bash
# In each plugin repo
mkdir -p .github/workflows
cp templates/github-workflows/notify-marketplace.yml .github/workflows/
# Edit MARKETPLACE_REPO variable in the file
```

**Step 4: Configure Secrets**
```bash
# Create PAT at https://github.com/settings/tokens
# Scopes needed: repo, workflow

# Add to each plugin repo
gh secret set MARKETPLACE_PAT --repo OWNER/plugin-a

# Add to marketplace repo
gh secret set MARKETPLACE_PAT --repo OWNER/marketplace
```

---

## 5. Troubleshooting

### 5.1 notify-marketplace.yml Issues

**Problem: Workflow not triggering**
- Check: `MARKETPLACE_PAT` secret exists in plugin repo
- Check: `MARKETPLACE_REPO` variable is correct (format: `owner/repo`)
- Check: PAT has `repo` and `workflow` scopes

**Problem: repository_dispatch not working**
- Verify: PAT is not expired
- Verify: PAT owner has write access to marketplace repo

### 5.2 update-submodules.yml Issues

**Problem: Push fails with "Permission denied"**
- Solution: Use `MARKETPLACE_PAT` instead of `GITHUB_TOKEN` in checkout
- Verify: PAT has admin/bypass access to branch protection

**Problem: Submodule not updating**
- Check: `.gitmodules` has correct URLs
- Run: `git submodule update --remote --merge`

### 5.3 Version Sync Issues

**Problem: marketplace.json version outdated**
- Check: `sync_marketplace_versions.py` is being called
- Check: Plugin paths in script match actual directories
- Run manually: `python scripts/sync_marketplace_versions.py`

---

## 6. Scoring System

### 6.1 Category Weights

| Category | Weight | What's Checked |
|----------|--------|----------------|
| Marketplace Structure | 25 | marketplace.json, .gitmodules |
| Submodule Health | 20 | All submodules accessible |
| Marketplace Workflows | 20 | update-submodules.yml, validate.yml |
| Plugin Workflows | 15 | notify-marketplace.yml in each plugin |
| Sync Scripts | 10 | sync_marketplace_versions.py present |
| Documentation | 10 | README with architecture info |

### 6.2 Grade Calculation

| Grade | Score Range | Meaning |
|-------|-------------|---------|
| A | 90-100 | Pipeline fully operational |
| B | 80-89 | Minor gaps, mostly functional |
| C | 70-79 | Some automation missing |
| D | 60-69 | Manual updates required |
| F | <60 | Pipeline broken or not configured |
