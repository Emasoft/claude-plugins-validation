---
name: cpv-validate-marketplace
description: "Validate marketplace.json structure"
allowed-tools: Read, Bash, Glob, Grep, Task, AskUserQuestion
argument-hint: "<marketplace_path_or_name> [--verbose] [--json]"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-marketplace Command

Validates a Claude Code marketplace configuration directory.

## Privacy Check (REQUIRED)

Before running validation, ensure private path detection is configured:

1. **Auto-detect username**: `uv run python -c "import getpass; print(getpass.getuser())"`
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
| `--strict` | Treat NIT issues as blocking (exit 4) |
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

### 3. Plugin Sources
- Validates source format (URL-based git sources)
- Checks repository URLs are accessible
- Verifies source configuration structure

### 4. GitHub Deployment Structure
- Validates GitHub Actions workflows
- Checks deployment configuration in plugin repos

### 5. Private Path Detection (CRITICAL)
- Scans all plugin content for private usernames
- Detects absolute home directory paths in plugin content
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
- **Exit Code**: 0 (pass), 1 (CRITICAL), 2 (MAJOR), 3 (MINOR), 4 (NIT, --strict only)
- **Counts**: Issues by severity level (CRITICAL, MAJOR, MINOR, NIT, WARNING)
- **Plugins Found**: List of plugins in marketplace
- **Details**: All validation results with file locations

## Output & Exit Codes

Uses standard CPV severity levels and exit codes. With `--report`, saves full output to file and prints only a compact summary. See `/cpv-validate-plugin` for details.

## Execution

When running from the CPV plugin directory:
```bash
CLAUDE_PRIVATE_USERNAMES="$USERNAME" uv run python scripts/validate_marketplace.py "$MARKETPLACE_PATH" $OPTIONS --report docs_dev/validate_marketplace_$(date +%Y%m%d).md
```

When running from another plugin's directory (no pyproject.toml):
```bash
CLAUDE_PRIVATE_USERNAMES="$USERNAME" uv run --with pyyaml python scripts/validate_marketplace.py "$MARKETPLACE_PATH" $OPTIONS --report docs_dev/validate_marketplace_$(date +%Y%m%d).md
```

## Related Commands

- `/cpv-validate-plugin` - Single plugin validation
- `/cpv-validate-skill` - Skill validation
- `/cpv-validate-hooks` - Hook validation
- `/cpv-validate-mcp` - MCP server validation
- `/cpv-fix-validation` — Fix issues from a validation report
- `/cpv-semantic-validation` — Deep semantic analysis (uses opus)
