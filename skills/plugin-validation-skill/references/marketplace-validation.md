# Marketplace Validation Reference

Complete reference for Claude Code plugin marketplace configuration and validation.

## Table of Contents

- [1. Marketplace Overview](#1-marketplace-overview)
- [2. marketplace.json Structure](#2-marketplacejson-structure)
- [3. Plugin Entry Configuration](#3-plugin-entry-configuration)
- [4. Source Types](#4-source-types)
- [5. Local Development Marketplace](#5-local-development-marketplace)
- [6. GitHub Deployment Validation](#6-github-deployment-validation)
- [7. Common Marketplace Errors](#7-common-marketplace-errors)
- [8. Validation Checklist](#8-validation-checklist)

---

## 1. Marketplace Overview

### What is a Marketplace?

A marketplace is a collection of plugins that can be installed via:

```bash
claude plugin install <plugin-name>@<marketplace-name>
```

### Marketplace Types

| Type | Description | Use Case |
|------|-------------|----------|
| Public | Published online | Community distribution |
| Private | Organization-internal | Team-specific plugins |
| Local | File-based | Development and testing |

### Adding Marketplaces

```bash
# Add public marketplace
claude plugin marketplace add https://example.com/marketplace

# Add local marketplace
claude plugin marketplace add ./my-marketplace

# List marketplaces
claude plugin marketplace list

# Remove marketplace
claude plugin marketplace remove marketplace-name
```

---

## 2. marketplace.json Structure

### Location

Place `marketplace.json` at marketplace root:

```
my-marketplace/
├── marketplace.json     # REQUIRED
├── plugins/             # Optional: local plugin storage
│   ├── plugin-a/
│   └── plugin-b/
└── README.md
```

### Required Fields

```json
{
  "name": "my-marketplace",
  "plugins": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| name | string | Unique marketplace identifier |
| plugins | array | List of plugin entries |

### Optional Fields

```json
{
  "name": "my-marketplace",
  "version": "1.0.0",
  "description": "My plugin marketplace",
  "plugins": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| version | string | Marketplace version (semver) |
| description | string | Human-readable description |

### Complete Example

```json
{
  "name": "dev-tools-marketplace",
  "version": "1.0.0",
  "description": "Development tools and utilities for Claude Code",
  "plugins": [
    {
      "name": "code-formatter",
      "version": "2.1.0",
      "description": "Automatic code formatting",
      "source": {
        "type": "git",
        "repository": "https://github.com/org/code-formatter"
      },
      "tags": ["formatting", "code-quality"]
    },
    {
      "name": "test-runner",
      "version": "1.5.0",
      "description": "Test automation",
      "path": "./plugins/test-runner"
    }
  ]
}
```

---

## 3. Plugin Entry Configuration

### Required Plugin Fields

| Field | Type | Description |
|-------|------|-------------|
| name | string | Plugin identifier (kebab-case) |

### Optional Plugin Fields

| Field | Type | Description |
|-------|------|-------------|
| version | string | Plugin version (semver) |
| description | string | What the plugin does |
| source | object/string | How to obtain the plugin |
| path | string | Local path to plugin |
| repository | string | Source repository URL |
| author | string/object | Plugin author |
| tags | array | Categorization tags |
| dependencies | array | Required plugins |
| enabled | boolean | Default enabled state |

### Minimal Plugin Entry

```json
{
  "name": "my-plugin"
}
```

### Full Plugin Entry

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Does something useful",
  "source": {
    "type": "git",
    "repository": "https://github.com/user/my-plugin",
    "branch": "main"
  },
  "author": {
    "name": "Developer",
    "email": "dev@example.com"
  },
  "tags": ["utility", "automation"],
  "dependencies": ["base-plugin"],
  "enabled": true
}
```

---

## 4. Source Types

### git Source

Clone from Git repository:

```json
{
  "name": "my-plugin",
  "source": {
    "type": "git",
    "repository": "https://github.com/user/my-plugin"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | `"git"` |
| repository | Yes | Git clone URL |
| branch | No | Branch to clone (default: main) |
| tag | No | Specific tag to clone |

### local Source

Reference local plugin directory:

```json
{
  "name": "my-plugin",
  "source": {
    "type": "local"
  },
  "path": "./plugins/my-plugin"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | `"local"` |
| path | Yes | Path to plugin directory |

Or shorthand:

```json
{
  "name": "my-plugin",
  "path": "./plugins/my-plugin"
}
```

### npm Source

Install from npm registry:

```json
{
  "name": "my-plugin",
  "source": {
    "type": "npm",
    "package": "@org/claude-plugin"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | `"npm"` |
| package | Yes | npm package name |
| version | No | Specific version or range |

### url Source

Download from URL:

```json
{
  "name": "my-plugin",
  "source": {
    "type": "url",
    "url": "https://example.com/plugins/my-plugin.tar.gz"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | `"url"` |
| url | Yes | Download URL |

---

## 5. Local Development Marketplace

### Purpose

Create a local marketplace for development and testing without publishing.

### Setup

1. Create marketplace directory:
```bash
mkdir my-dev-marketplace
cd my-dev-marketplace
```

2. Create marketplace.json:
```json
{
  "name": "dev-marketplace",
  "version": "0.1.0",
  "description": "Local development marketplace",
  "plugins": [
    {
      "name": "my-plugin-dev",
      "version": "0.0.1",
      "path": "../my-plugin"
    }
  ]
}
```

3. Add marketplace:
```bash
claude plugin marketplace add ./my-dev-marketplace
```

4. Install plugin:
```bash
claude plugin install my-plugin-dev@dev-marketplace
```

### Development Workflow

1. Make changes to plugin
2. Reinstall to pick up changes:
```bash
claude plugin install my-plugin-dev@dev-marketplace --force
```

Or use `--plugin-dir` flag:
```bash
claude --plugin-dir ./my-plugin
```

---

## 6. GitHub Deployment Validation

When deploying a marketplace to GitHub for public use, additional requirements ensure users can successfully install and use your plugins.

### Required Directory Structure

```
my-marketplace/
├── .claude-plugin/
│   └── marketplace.json   # Marketplace configuration
├── plugin-a/              # Plugin subfolder
│   ├── .claude-plugin/
│   │   └── plugin.json
│   └── README.md          # Plugin-specific documentation
├── plugin-b/
│   ├── .claude-plugin/
│   │   └── plugin.json
│   └── README.md
└── README.md              # Main marketplace README
```

### Main README.md Requirements

The main README.md at marketplace root MUST contain these sections:

#### Required Sections

| Section | Description |
|---------|-------------|
| Installation | How to add and install plugins |
| Update | How to update to latest version |
| Uninstall | How to remove the plugin |
| Troubleshooting | Common issues and solutions |

#### Installation Section Must Include

The Installation section should cover these 4 steps:

1. **Add Marketplace** - Command to add the marketplace
   ```bash
   claude plugin marketplace add https://github.com/user/my-marketplace
   ```

2. **Install Plugin** - Command to install a plugin
   ```bash
   claude plugin install plugin-name@my-marketplace
   ```

3. **Verify Installation** - How to confirm it worked
   ```bash
   claude plugin list
   ```

4. **Restart Claude Code** - Reminder to restart
   > Restart Claude Code for the plugin to take effect

#### Example README.md Template

```markdown
# My Plugin Marketplace

Description of the marketplace.

## Installation

### Step 1: Add this Marketplace

\`\`\`bash
claude plugin marketplace add https://github.com/user/my-marketplace
\`\`\`

### Step 2: Install a Plugin

\`\`\`bash
claude plugin install my-plugin@my-marketplace
\`\`\`

### Step 3: Verify Installation

\`\`\`bash
claude plugin list
\`\`\`

### Step 4: Restart Claude Code

Restart Claude Code to activate the plugin.

## Update to Latest Version

\`\`\`bash
claude plugin install my-plugin@my-marketplace --force
\`\`\`

## Uninstall

\`\`\`bash
claude plugin uninstall my-plugin
\`\`\`

## Troubleshooting

### Plugin not loading
1. Verify installation: \`claude plugin list\`
2. Check for errors: \`claude --debug\`
3. Reinstall: \`claude plugin install my-plugin@my-marketplace --force\`

### Marketplace not found
Ensure you added the marketplace first:
\`\`\`bash
claude plugin marketplace add https://github.com/user/my-marketplace
\`\`\`
```

### Plugin Subfolder README.md

Each plugin subfolder should have its own README.md containing:

- Plugin name and description
- What the plugin does
- Usage instructions
- Configuration options (if any)
- Examples

### Validation Checks

The validator checks:

1. **Main README.md exists** - At marketplace root
2. **Required sections present** - Installation, Update, Uninstall, Troubleshooting
3. **Installation completeness** - Contains all 4 steps
4. **Plugin READMEs** - Each plugin subfolder has README.md
5. **No placeholder content** - No [TODO], [INSERT], etc.

### Validation Command

```bash
uv run python scripts/validate_marketplace.py /path/to/marketplace --verbose
```

---

## 7. Common Marketplace Errors

### Error: Missing marketplace.json

**Symptom:** Can't add marketplace

**Fix:** Create marketplace.json at marketplace root:
```json
{
  "name": "my-marketplace",
  "plugins": []
}
```

### Error: Missing name Field

**Wrong:**
```json
{
  "plugins": [{"name": "plugin-a"}]
}
```

**Correct:**
```json
{
  "name": "my-marketplace",
  "plugins": [{"name": "plugin-a"}]
}
```

### Error: Plugin Missing name

**Wrong:**
```json
{
  "plugins": [
    {"version": "1.0.0", "path": "./plugins/a"}
  ]
}
```

**Correct:**
```json
{
  "plugins": [
    {"name": "plugin-a", "version": "1.0.0", "path": "./plugins/a"}
  ]
}
```

### Error: Duplicate Plugin Names

**Wrong:**
```json
{
  "plugins": [
    {"name": "my-plugin"},
    {"name": "my-plugin"}
  ]
}
```

**Fix:** Each plugin must have unique name

### Error: Local Path Not Found

**Wrong:**
```json
{"path": "./nonexistent/path"}
```

**Fix:** Verify path exists and is relative to marketplace.json:
```bash
ls -la ./plugins/my-plugin
```

### Error: Missing plugin.json in Plugin

**Symptom:** Plugin installs but doesn't work

**Fix:** Ensure plugin has `.claude-plugin/plugin.json`:
```
my-plugin/
├── .claude-plugin/
│   └── plugin.json    # Required!
└── ...
```

### Error: Invalid Source Type

**Wrong:**
```json
{"source": {"type": "svn"}}
```

**Valid types:** git, local, npm, url

### Error: Git Source Missing Repository

**Wrong:**
```json
{"source": {"type": "git"}}
```

**Correct:**
```json
{
  "source": {
    "type": "git",
    "repository": "https://github.com/user/repo"
  }
}
```

---

## 8. Validation Checklist

### Pre-publish Marketplace Checklist

- [ ] marketplace.json exists at root
- [ ] marketplace.json is valid JSON
- [ ] Required `name` field present
- [ ] Required `plugins` field is array
- [ ] Marketplace name is kebab-case
- [ ] Version follows semver (if present)
- [ ] Each plugin has unique `name`
- [ ] Plugin names are kebab-case
- [ ] Local paths resolve correctly
- [ ] Each plugin has valid source configuration
- [ ] Referenced plugins have plugin.json

### GitHub Deployment Checklist

- [ ] Main README.md exists at marketplace root
- [ ] README.md has Installation section
- [ ] Installation has add marketplace step
- [ ] Installation has install plugin step
- [ ] Installation has verify step
- [ ] Installation has restart reminder
- [ ] README.md has Update section
- [ ] README.md has Uninstall section
- [ ] README.md has Troubleshooting section
- [ ] Each plugin subfolder has README.md
- [ ] No placeholder content ([TODO], [INSERT], etc.)

### Validation Command

```bash
uv run python scripts/validate_marketplace.py /path/to/marketplace
```

### Testing Installation

```bash
# Add local marketplace
claude plugin marketplace add ./my-marketplace

# Try installing a plugin
claude plugin install my-plugin@my-marketplace

# Check it works
claude --plugin-dir ~/.claude/plugins/my-plugin

# Remove test marketplace
claude plugin marketplace remove my-marketplace
```

---

## Related References

- [Plugin Structure](plugin-structure.md) - Plugin requirements
- [Hook Validation](hook-validation.md) - Hook configuration
- [Skill Validation](skill-validation.md) - Skill structure
- [MCP Validation](mcp-validation.md) - MCP servers
