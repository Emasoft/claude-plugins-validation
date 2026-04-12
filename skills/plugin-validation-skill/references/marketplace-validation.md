# Marketplace Validation Reference

Complete reference for Claude Code plugin marketplace configuration and validation.

## Table of Contents

- [1. Marketplace Overview](#1-marketplace-overview)
- [2. marketplace.json Structure](#2-marketplacejson-structure)
- [3. Plugin Entry Configuration](#3-plugin-entry-configuration)
- [4. Source Types](#4-source-types)
- [5. Local Development Marketplace](#5-local-development-marketplace)
- [6. GitHub Deployment Validation](#6-github-deployment-validation)
- [7. Git Submodule Validation](#7-git-submodule-validation)
- [8. Common Marketplace Errors](#8-common-marketplace-errors)
- [9. Validation Checklist](#9-validation-checklist)

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
  "owner": {
    "name": "My Organization"
  },
  "plugins": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| name | string | Unique marketplace identifier |
| owner | object | Marketplace owner information |
| owner.name | string | Owner name (required within owner) |
| plugins | array | List of plugin entries |

> **Note:** The following marketplace names are reserved and must not be used: `official`, `anthropic`, `claude`, `test`, `example`, `demo`.

### Optional Fields

```json
{
  "name": "my-marketplace",
  "owner": {
    "name": "My Organization",
    "url": "https://example.com",
    "email": "contact@example.com"
  },
  "version": "1.0.0",
  "description": "My plugin marketplace",
  "plugins": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| owner.url | string | Owner website URL (optional within owner) |
| owner.email | string | Owner contact email (optional within owner) |
| version | string | Marketplace version (semver X.Y.Z) |
| description | string | Human-readable description |

### Version Format

All `version` fields (marketplace and plugin) must follow strict semver format: `X.Y.Z` where X, Y, and Z are non-negative integers (e.g. `"1.0.0"`, `"2.3.14"`). Version strings like `"1.0"`, `"v1.0.0"`, or `"latest"` are not valid.

### Reserved Marketplace Names

The following marketplace names are reserved and will be rejected by the validator:

| Reserved Name | Reason |
|---------------|--------|
| `official` | Reserved for Anthropic official marketplace |
| `anthropic` | Reserved for Anthropic brand |
| `claude` | Reserved for Anthropic brand |
| `test` | Reserved to prevent accidental use in production |
| `example` | Reserved to prevent accidental use in production |
| `demo` | Reserved to prevent accidental use in production |

### Complete Example

```json
{
  "name": "dev-tools-marketplace",
  "owner": {
    "name": "Acme Corp",
    "url": "https://acme.example.com",
    "email": "plugins@acme.example.com"
  },
  "version": "1.0.0",
  "description": "Development tools and utilities for Claude Code",
  "plugins": [
    {
      "name": "code-formatter",
      "version": "2.1.0",
      "description": "Automatic code formatting",
      "source": {
        "source": "github",
        "repo": "org/code-formatter"
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
| source | object/string | How to obtain the plugin (required) |

### Optional Plugin Fields

| Field | Type | Description |
|-------|------|-------------|
| version | string | Plugin version (semver X.Y.Z) |
| description | string | What the plugin does |
| path | string | Local path to plugin |
| repository | string | Source repository URL |
| homepage | string | Plugin homepage URL |
| license | string | License identifier (e.g. "MIT") |
| keywords | array | Array of keyword strings for discovery |
| author | string/object | Plugin author (string or object with "name" field) |
| icon | string | URL to plugin icon image |
| tags | array | Categorization tags |
| dependencies | array | Required plugins |
| enabled | boolean | Default enabled state |

### Minimal Plugin Entry

```json
{
  "name": "my-plugin",
  "source": "./my-plugin"
}
```

### Full Plugin Entry

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Does something useful",
  "source": {
    "source": "github",
    "repo": "user/my-plugin",
    "ref": "main"
  },
  "homepage": "https://example.com/my-plugin",
  "license": "MIT",
  "keywords": ["utility", "automation", "formatting"],
  "author": {
    "name": "Developer",
    "email": "dev@example.com"
  },
  "icon": "https://example.com/my-plugin/icon.png",
  "tags": ["utility", "automation"],
  "dependencies": ["base-plugin"],
  "enabled": true
}
```

---

## 4. Source Types

The `source` field can be either:
- A **string path** (e.g. `"./my-plugin"`) for local plugin subdirectories, OR
- An **object** with a `source` key (the discriminator) that identifies one of the five valid source types.

**Valid source types** (from `VALID_SOURCE_TYPES` in `scripts/validate_marketplace.py`):

| Type | Purpose |
|------|---------|
| `github` | Plugin hosted in its own GitHub repository |
| `url` | Plugin distributed as a downloadable tarball |
| `npm` | Plugin published to the npm registry |
| `git-subdir` | Plugin lives in a subdirectory of a git repository (Claude Code 2.1.69+) |
| `settings` | Inline marketplace declared in user settings (Claude Code 2.1.80+) |

> **NOTE:** The discriminator key inside the source object is `source`, not `type`. Writing `{"type": "github", ...}` produces a MAJOR validation error.

### String-path Source (local)

Reference a local plugin subdirectory (the most common case for hub-and-spoke monorepos):

```json
{
  "name": "my-plugin",
  "source": "./plugins/my-plugin"
}
```

Any path starting with `./` is treated as a local source; the validator checks the directory exists. Local plugins MUST use this string form — wrapping them in `{"source": "github", ...}` is a CRITICAL error if the plugin exists locally as a submodule.

### github Source

Clone from a GitHub repository:

```json
{
  "name": "my-plugin",
  "source": {
    "source": "github",
    "repo": "user/my-plugin"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| source | Yes | `"github"` |
| repo | Yes | Repository slug in `owner/repo` format |
| ref | No | Branch or tag name (default: default branch) |
| sha | No | Full 40-character commit SHA (short SHAs are rejected) |

> **SHA Validation:** When providing a `sha` field, it must be exactly 40 hexadecimal characters (the full SHA-1 hash). Short SHAs (e.g. 7 or 12 characters) are rejected by the validator.

### npm Source

Install from the npm registry:

```json
{
  "name": "my-plugin",
  "source": {
    "source": "npm",
    "package": "@org/claude-plugin"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| source | Yes | `"npm"` |
| package | Yes | npm package name |
| version | No | Specific version or range |

### url Source

Download from a URL (typically a tarball):

```json
{
  "name": "my-plugin",
  "source": {
    "source": "url",
    "url": "https://example.com/plugins/my-plugin.tar.gz"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| source | Yes | `"url"` |
| url | Yes | Download URL |

### git-subdir Source

Reference a subdirectory within a git repository (added in Claude Code 2.1.69):

```json
{
  "name": "my-plugin",
  "source": {
    "source": "git-subdir",
    "url": "https://github.com/user/monorepo",
    "path": "packages/my-plugin"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| source | Yes | `"git-subdir"` |
| url | Yes | Git repository URL |
| path | Yes | Subdirectory path within the repo |
| ref | No | Branch or tag name |
| sha | No | Full 40-character commit SHA |

Use this when a plugin lives inside a larger monorepo rather than being the entire repository.

### settings Source

Inline marketplace declared in user settings (Claude Code 2.1.80+). Rarely used at the `marketplace.json` level — see the plugins reference for the settings-based marketplace format.

| Field | Required | Description |
|-------|----------|-------------|
| source | Yes | `"settings"` |
| name | Yes | Inline marketplace name |
| plugins | Yes | Array of plugin entries |

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

## 7. Git Submodule Validation

For proper marketplace development and version control, all plugins should be managed as git submodules of the marketplace repository. This enables independent plugin development while maintaining a unified marketplace.

### Why Use Submodules?

| Benefit | Description |
|---------|-------------|
| Version Independence | Each plugin has its own version history |
| Separate Development | Plugins can be developed in isolation |
| Clean Updates | Update plugins without touching marketplace code |
| Proper Attribution | Each plugin maintains its own commit history |
| Easy Distribution | Submodules auto-fetch on clone with --recursive |

### Required Structure

```
my-marketplace/               # Main marketplace git repo
├── .git/                     # Marketplace git directory
├── .gitmodules              # Submodule configuration
├── .claude-plugin/
│   └── marketplace.json
├── plugin-a/                 # Git submodule -> plugin-a repo
│   └── .git                  # Points to submodule git dir
├── plugin-b/                 # Git submodule -> plugin-b repo
│   └── .git
└── README.md
```

### .gitmodules File Format

```ini
[submodule "plugin-a"]
    path = plugin-a
    url = https://github.com/user/plugin-a.git

[submodule "plugin-b"]
    path = plugin-b
    url = https://github.com/user/plugin-b.git
```

### Setting Up Submodules

1. **Create plugin repository first**:
   ```bash
   # Create and push plugin to its own repo
   cd plugin-a
   git init
   git add .
   git commit -m "Initial commit"
   gh repo create user/plugin-a --push --source .
   ```

2. **Add as submodule to marketplace**:
   ```bash
   cd my-marketplace
   git submodule add https://github.com/user/plugin-a.git plugin-a
   git commit -m "Add plugin-a as submodule"
   ```

3. **Update marketplace.json with a LOCAL string-path source** (the plugin is a submodule so it exists locally — use `"./plugin-a"`, NOT a github object. The validator will flag the github object form with a warning when it detects a `.git` submodule marker):
   ```json
   {
     "name": "plugin-a",
     "source": "./plugin-a",
     "repository": "https://github.com/user/plugin-a"
   }
   ```

### Converting Existing Plugin to Submodule

If a plugin exists as a regular directory:

```bash
# 1. Remove from git (keep files)
git rm -r --cached plugin-a

# 2. Move to temp location
mv plugin-a /tmp/plugin-a-backup

# 3. Create plugin repo and push
cd /tmp/plugin-a-backup
git init
git add .
git commit -m "Initial commit"
gh repo create user/plugin-a --push --source .

# 4. Return to marketplace and add as submodule
cd /path/to/marketplace
git submodule add https://github.com/user/plugin-a.git plugin-a
git commit -m "Convert plugin-a to submodule"
```

### Validation Checks

The validator checks:

1. **Git repository** - Marketplace must be a git repo
2. **.gitmodules exists** - Required if plugin directories exist
3. **Each plugin is a submodule** - Plugin directories must be in .gitmodules
4. **URLs match** - Submodule URL should match source.repository in marketplace.json
5. **Submodules initialized** - Submodules should be initialized (not empty)

### Validation Command

```bash
uv run python scripts/validate_marketplace.py /path/to/marketplace --verbose
```

Example output:
```
[INFO] [submodule] Found 2 plugin(s) configured as git submodules
    Location: /path/to/marketplace/.gitmodules
```

### Common Submodule Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Plugin not a submodule | Directory added directly | Convert to submodule |
| Submodule not initialized | Cloned without --recursive | `git submodule update --init` |
| URL mismatch | Different URL in .gitmodules vs marketplace.json | Update to match |
| Empty plugin directory | Submodule not pulled | `git submodule update --init --recursive` |

### Cloning Marketplace with Submodules

Users must clone with `--recursive` to get plugin content:

```bash
# Correct way to clone
git clone --recursive https://github.com/user/marketplace.git

# Or if already cloned, initialize submodules
git submodule update --init --recursive
```

---

## 8. Common Marketplace Errors

### Error: Reserved Marketplace Name

**Wrong:**
```json
{
  "name": "official",
  "plugins": []
}
```

**Fix:** Choose a unique marketplace name that is not in the reserved list (`official`, `anthropic`, `claude`, `test`, `example`, `demo`).

### Error: Missing owner Field

**Wrong:**
```json
{
  "name": "my-marketplace",
  "plugins": []
}
```

**Correct:**
```json
{
  "name": "my-marketplace",
  "owner": {
    "name": "My Organization"
  },
  "plugins": []
}
```

### Error: Plugin Missing source Field

**Wrong:**
```json
{
  "plugins": [
    {"name": "plugin-a", "version": "1.0.0"}
  ]
}
```

**Correct:**
```json
{
  "plugins": [
    {"name": "plugin-a", "version": "1.0.0", "source": "./plugin-a"}
  ]
}
```

### Error: Short SHA in github source

**Wrong:**
```json
{
  "source": {
    "source": "github",
    "repo": "user/repo",
    "sha": "a1b2c3d"
  }
}
```

**Correct:**
```json
{
  "source": {
    "source": "github",
    "repo": "user/repo",
    "sha": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
  }
}
```

The `sha` field must be exactly 40 hexadecimal characters (full SHA-1 hash).

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
{"source": {"source": "svn"}}
```

**Valid types:** `github`, `url`, `npm`, `git-subdir`, `settings` (plus string-path local sources starting with `./`).

### Error: Wrong Discriminator Key (`type` instead of `source`)

**Wrong:**
```json
{"source": {"type": "github", "repo": "user/repo"}}
```

**Correct:**
```json
{"source": {"source": "github", "repo": "user/repo"}}
```

Inside the source object, the discriminator key is `source`, not `type`.

### Error: github Source Missing repo

**Wrong:**
```json
{"source": {"source": "github"}}
```

**Correct:**
```json
{
  "source": {
    "source": "github",
    "repo": "user/repo"
  }
}
```

The `repo` field is required for `github` sources and must be in `owner/repo` format.

### CRITICAL: Invalid Source Schema for Local Plugins

**Symptom:** When adding a local marketplace, you get:
```
✘ Failed to add marketplace: Invalid schema: plugins.0.source: Invalid input
```

**Cause:** Using `source: { "source": "github", "repo": "..." }` for plugins that exist as local directories (or git submodules) in the marketplace.

**Wrong (local marketplace with local plugin subdirectories):**
```json
{
  "plugins": [
    {
      "name": "my-plugin",
      "source": {
        "source": "github",
        "repo": "user/my-plugin"
      },
      "repository": "https://github.com/user/my-plugin"
    }
  ]
}
```

**Correct (for local marketplace with plugin subdirectories):**
```json
{
  "plugins": [
    {
      "name": "my-plugin",
      "source": "./my-plugin",
      "repository": "https://github.com/user/my-plugin"
    }
  ]
}
```

**Key points:**
- For **local marketplaces** where plugins are subdirectories (or git submodules), use **string path** for `source`
- The `source` field is for **where Claude Code finds the plugin** - use local paths for local directories
- The `repository` field (at plugin level) is for **reference only** - the remote URL for documentation/updates
- Claude Code's schema validation is strict: `source: { "source": "github", ... }` is NOT valid when the plugin exists locally as a submodule
- Inside the source object the discriminator key is `source`, not `type`. Writing `{"type": "github", ...}` is a MAJOR validation error.

**When to use each format:**

| Scenario | source Format | Example |
|----------|---------------|---------|
| Plugin as local subdirectory | String path | `"./my-plugin"` |
| Plugin as git submodule | String path | `"./my-plugin"` |
| Plugin from GitHub | Object with `source: github` | `{"source": "github", "repo": "owner/repo"}` |
| Plugin in monorepo subdir | Object with `source: git-subdir` | `{"source": "git-subdir", "url": "https://...", "path": "packages/my-plugin"}` |
| Plugin from npm | Object with `source: npm` | `{"source": "npm", "package": "@org/plugin"}` |
| Plugin from URL (tarball) | Object with `source: url` | `{"source": "url", "url": "https://.../plugin.tar.gz"}` |

---

## 9. Validation Checklist

### Pre-publish Marketplace Checklist

- [ ] marketplace.json exists at root
- [ ] marketplace.json is valid JSON
- [ ] Required `name` field present
- [ ] Marketplace name is not a reserved name (`official`, `anthropic`, `claude`, `test`, `example`, `demo`)
- [ ] Required `owner` field present with at minimum `owner.name`
- [ ] Required `plugins` field is array
- [ ] Marketplace name is kebab-case
- [ ] Version follows semver X.Y.Z format (if present)
- [ ] Each plugin has unique `name`
- [ ] Plugin names are kebab-case
- [ ] Each plugin has a `source` field (required)
- [ ] Local paths resolve correctly
- [ ] Each plugin has valid source configuration
- [ ] **CRITICAL**: Local plugins use string path source (`"./plugin-name"`), not git object
- [ ] SHA/commit fields are exactly 40 hex characters (no short SHAs)
- [ ] Plugin version fields follow semver X.Y.Z format (if present)
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

### Git Submodules Checklist

- [ ] Marketplace is a git repository
- [ ] .gitmodules file exists
- [ ] Each plugin directory is a git submodule
- [ ] Submodule URLs match source.repository in marketplace.json
- [ ] Submodules are initialized (not empty)
- [ ] README mentions `--recursive` for cloning

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
