# Detailed Validation Procedures

## Table of Contents

1. [Validation Protocol](#validation-protocol)
2. [Validate-Fix-Revalidate Cycle](#validate-fix-revalidate-cycle)
3. [Backup Strategy](#backup-strategy)
4. [Corruption Detection](#corruption-detection)
5. [Operational Checklists](#operational-checklists)
6. [Common Issues and Fixes](#common-issues-and-fixes)

---

## Validation Protocol

**CRITICAL RULE: ALWAYS validate before fixing.**

### Core Principles

1. **Validate First** - Run validation to discover issues before proposing any changes
2. **Present Findings** - Show all validation results with severity levels and explanations
3. **Explain Impact** - For each issue, explain what breaks if not fixed
4. **Ask Permission** - Get explicit user approval before making ANY changes
5. **Confirm Changes** - After fixing, re-validate and report final status

### Permission Requirements

| Action | Permission Required |
|--------|---------------------|
| Running validation scripts | No - read-only operation |
| Reading files to analyze | No - read-only operation |
| Suggesting fixes | No - information only |
| Creating backup commits | YES - modifies git history |
| Editing plugin files | YES - modifies user code |
| Creating new files | YES - modifies filesystem |
| Deleting files | YES - ALWAYS ask explicitly |

### Example Permission Request

```
I found 3 issues in your plugin:

1. CRITICAL: scripts/check.sh is not executable
   - Impact: Hook will fail silently, plugin won't work
   - Fix: chmod +x scripts/check.sh

2. MAJOR: hooks.json references non-existent script
   - Impact: PreToolUse hook will error on every tool call
   - Fix: Create scripts/validate.sh or update path

3. MINOR: Missing README.md
   - Impact: Users won't know how to use the plugin
   - Fix: Create README.md with usage instructions

Would you like me to:
a) Fix all issues (I will create a backup commit first)
b) Fix only critical/major issues
c) Show me the exact changes first
d) Let me fix them manually

Please confirm your choice.
```

---

## Validate-Fix-Revalidate Cycle

```
┌─────────────────┐
│  1. VALIDATE    │ Run validation scripts
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  2. REPORT      │ Present all issues with severity
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  3. WAIT        │ Ask user permission to proceed
└────────┬────────┘
         │ (user approves)
         ▼
┌─────────────────┐
│  4. BACKUP      │ Create git commit before changes
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  5. FIX         │ Apply approved fixes ONLY
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  6. REVALIDATE  │ Run validation again
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  7. REPORT      │ Confirm fixes or report remaining issues
└─────────────────┘
```

### Detailed Steps

1. **VALIDATE** - Run the appropriate validation script for the target:
   ```bash
   uv run python scripts/validate_plugin.py /path/to/plugin --verbose
   ```

2. **REPORT** - Present findings in a structured format:
   - Group by severity (CRITICAL, MAJOR, MINOR)
   - Include file path and line number where applicable
   - Explain the impact of each issue

3. **WAIT** - Do NOT proceed until user explicitly approves

4. **BACKUP** - Before making any changes:
   ```bash
   git add -A && git commit -m "Backup before plugin validation fixes"
   ```

5. **FIX** - Apply only the approved fixes

6. **REVALIDATE** - Run the same validation again

7. **REPORT** - Final status with rollback instructions if needed

---

## Backup Strategy

**NEVER make changes without a backup.**

### Before Making Changes

```bash
# 1. Check for uncommitted changes
git status

# 2. If uncommitted changes exist, stash them
git stash push -m "Pre-validation stash $(date +%Y%m%d-%H%M%S)"

# 3. Create a backup commit
git add -A
git commit -m "Backup before plugin validation fixes - $(date +%Y%m%d-%H%M%S)"

# 4. Record the commit hash for rollback
BACKUP_COMMIT=$(git rev-parse HEAD)
echo "Backup commit: $BACKUP_COMMIT"
```

### After Making Changes

```bash
# If fixes successful, commit
git add -A
git commit -m "Fix plugin validation issues: [list issues fixed]"

# If fixes caused problems, rollback
git reset --hard $BACKUP_COMMIT

# If stash was used, restore
git stash pop
```

### Rollback Instructions Template

```
Changes have been applied. If you encounter issues:

To undo ALL changes:
  git reset --hard [BACKUP_COMMIT_HASH]

To see what was changed:
  git diff [BACKUP_COMMIT_HASH]..HEAD

To restore any stashed changes:
  git stash list
  git stash pop
```

---

## Corruption Detection

After editing any file, validate its syntax to detect corruption:

### JSON Files (plugin.json, hooks.json, .mcp.json)

```bash
jq . /path/to/file.json > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "CORRUPTION DETECTED: Invalid JSON"
    git checkout -- /path/to/file.json  # Restore from git
fi
```

### YAML Frontmatter (SKILL.md, agent .md files)

```bash
python3 -c "
import yaml
with open('/path/to/file.md') as f:
    content = f.read()
    if content.startswith('---'):
        end = content.find('---', 3)
        yaml.safe_load(content[3:end])
        print('Frontmatter valid')
"
```

### Shell Scripts

```bash
shellcheck /path/to/script.sh
```

### Python Scripts

```bash
python3 -m py_compile /path/to/script.py
ruff check /path/to/script.py
```

### Recovery from Corruption

1. Restore from git: `git checkout -- /path/to/corrupted/file`
2. Report to user with error message
3. Retry with smaller edits - one change at a time

---

## Operational Checklists

### Pre-Validation Checklist

- [ ] 1. Confirm target path exists and is accessible
- [ ] 2. Check if target is a plugin, marketplace, or component
- [ ] 3. Verify git repository is available for backups
- [ ] 4. Check for uncommitted changes (stash if needed)
- [ ] 5. Ensure validation scripts are available and updated

### Post-Validation Fix Checklist

- [ ] 1. Create backup commit with timestamp
- [ ] 2. Record backup commit hash
- [ ] 3. Apply fix to first issue
- [ ] 4. Validate syntax of changed file
- [ ] 5. Repeat steps 3-4 for each approved fix
- [ ] 6. Run full validation again
- [ ] 7. Report results and provide rollback instructions

### Git Hooks Setup Checklist

- [ ] 1. Verify .git/hooks directory exists
- [ ] 2. Create pre-commit hook script
- [ ] 3. Make hook executable (chmod +x)
- [ ] 4. Test hook with a sample commit
- [ ] 5. Verify hook blocks invalid changes
- [ ] 6. Document hook in plugin README

### Marketplace Publishing Checklist

- [ ] 1. Run marketplace validation with --github-deploy flag
- [ ] 2. Verify main README.md has all required sections
- [ ] 3. Check each plugin subfolder has README.md
- [ ] 4. Search for placeholder content ([TODO], [INSERT], etc.)
- [ ] 5. Test installation from local path
- [ ] 6. Verify all plugin paths resolve correctly
- [ ] 7. Create release tag with version number

---

## Common Issues and Fixes

### Plugin Manifest Issues

| Issue | Fix |
|-------|-----|
| Missing name | Add `"name": "my-plugin"` (kebab-case) |
| Invalid version | Use semver: `"version": "1.0.0"` |
| agents not array | Use `"agents": ["./agents/my-agent.md"]` |
| Components in wrong location | Move from `.claude-plugin/` to plugin root |

### Hook Issues

| Issue | Fix |
|-------|-----|
| Invalid event type | Use valid event from 13 allowed types |
| Script not found | Use `${CLAUDE_PLUGIN_ROOT}/scripts/name.sh` |
| Script not executable | Run `chmod +x scripts/*.sh` |
| Invalid matcher | Use tool name or valid regex |

### Skill Issues

| Issue | Fix |
|-------|-----|
| Missing SKILL.md | Create with frontmatter and content |
| Invalid frontmatter | Use YAML between `---` delimiters |
| Missing name/description | Add required fields to frontmatter |

### MCP Issues

| Issue | Fix |
|-------|-----|
| Missing command | Add `"command": "..."` for stdio servers |
| Absolute path | Use `${CLAUDE_PLUGIN_ROOT}/path` |
| Invalid transport | Use "stdio", "http", or "sse" |
| Deprecated sse | Migrate to "http" transport |

### GitHub Deployment Issues

| Issue | Fix |
|-------|-----|
| Missing marketplace README.md | Create README.md with installation instructions |
| Missing README sections | Add: ## Installation, ## Update, ## Uninstall, ## Troubleshooting |
| Incomplete installation steps | Include: add marketplace, install plugin, verify, restart |
| Plugin subfolder missing README | Add README.md describing the plugin |
| Placeholder content found | Replace [TODO], [INSERT], etc. with actual content |
