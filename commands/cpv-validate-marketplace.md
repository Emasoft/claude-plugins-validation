---
name: cpv-validate-marketplace
description: |
  Validate Claude Code marketplace configurations (marketplace.json). Checks plugin entries,
  git submodules, version consistency, GitHub deployment structure, and private path leaks.
  Use when publishing marketplaces or auditing marketplace configurations.
allowed-tools: Read, Bash, Glob, Grep, Task, AskUserQuestion
argument-hint: "<marketplace_path_or_name> [--verbose] [--json]"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-marketplace Command

Validates a Claude Code marketplace configuration directory.

## Privacy Check (REQUIRED)

Before running validation, ensure private path detection is configured:

1. **Auto-detect username**: `python3 -c "import getpass; print(getpass.getuser())"`
2. **If auto-detection fails**, ask the user for their system username
3. **Pass to script**: `CLAUDE_PRIVATE_USERNAMES="username" uv run python scripts/...`

## Usage

```
/cpv-validate-marketplace <marketplace_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `marketplace_path_or_name` | Yes | Path to marketplace directory OR just the marketplace name for auto-discovery |

### Auto-Discovery

If you provide just a name (e.g., `my-marketplace`), the agent will search for it in:
1. Current directory (`./my-marketplace/`)
2. OUTPUT_SKILLS folder (`./OUTPUT_SKILLS/my-marketplace/`)
3. Any directory containing `marketplace.json` with matching name

If multiple matches are found, you'll be asked to choose.

### Typo Tolerance

Names are normalized before searching:
- Converted to lowercase: `My-Marketplace` → `my-marketplace`
- Underscores become hyphens: `my_marketplace` → `my-marketplace`

If no exact match is found, fuzzy matching is used (e.g., `cpt-validate` → `cpv-validate`).
**Fuzzy matches always require your confirmation before proceeding.**

## Options

| Option | Description |
|--------|-------------|
| `--verbose` | Show all checks including passed |
| `--json` | Output results as JSON |

## What Gets Validated

### 1. Marketplace Manifest (`.claude-plugin/marketplace.json`)
- Required fields: `name`, `plugins`
- Name format validation (kebab-case)
- Version format (semver)

### 2. Plugin Entries
- Each plugin has required fields (name, version, source)
- Source paths resolve correctly
- Version consistency with plugin.json

### 3. Git Submodules
- Validates .gitmodules matches plugin entries
- Checks submodule paths exist
- Verifies submodule URLs are valid

### 4. GitHub Deployment Structure
- Validates GitHub Actions workflows
- Checks notify-marketplace.yml in plugin repos
- Checks update-submodules.yml in marketplace

### 5. Private Path Detection (CRITICAL)
- Scans all plugin content for private usernames
- Detects absolute home paths (/Users/name/, /home/name/)
- Flags any leaked private information

### 6. Repository URLs
- Validates plugins have repository field for GitHub publishing
- Checks repository URLs are valid GitHub URLs

## Examples

### Validate Marketplace

```
/cpv-validate-marketplace ./my-marketplace/
```

### Verbose Output

```
/cpv-validate-marketplace ./my-marketplace/ --verbose
```

### JSON Output for CI/CD

```
/cpv-validate-marketplace ./my-marketplace/ --json
```

## Output

Returns summary with:
- **Exit Code**: 0 (pass), 1 (critical), 2 (major), 3 (minor)
- **Counts**: Issues by severity level
- **Plugins Found**: List of plugins in marketplace
- **Details**: All validation results with file locations

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | CRITICAL issues (marketplace won't work or contains private info) |
| 2 | MAJOR issues (significant problems) |
| 3 | MINOR issues (may affect UX) |

## Execution

```bash
CLAUDE_PRIVATE_USERNAMES="$USERNAME" uv run python scripts/validate_marketplace.py "$MARKETPLACE_PATH" $OPTIONS
```

## Related Commands

- `/cpv-validate-plugin` - Single plugin validation
- `/cpv-validate-skill` - Skill validation
- `/cpv-validate-hooks` - Hook validation
- `/cpv-validate-mcp` - MCP server validation
