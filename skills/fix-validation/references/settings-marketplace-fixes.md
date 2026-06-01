# Settings Marketplace — Validation Issues and Fixes

## Table of Contents

- [1. settings.json Structure Issues](#1-settingsjson-structure-issues)
- [2. extraKnownMarketplaces Entry Issues](#2-extraknownmarketplaces-entry-issues)
- [3. Source Type Issues](#3-source-type-issues)
- [4. Differences from validate_marketplace.py](#4-differences-from-validate_marketplacepy)

---

Comprehensive remediation guide for all issues detected by `validate_settings_marketplace.py`. This validator checks the `extraKnownMarketplaces` block inside a Claude Code `settings.json` file.

**Not the same as `validate_marketplace.py`** — that validator operates on a `marketplace.json` file and checks per-plugin `source` entries. This validator operates on a `settings.json` file and checks marketplace-level `source` entries. The source types overlap but are not identical.

---

## Checklist

- [ ] Identify the settings.json file the finding references
- [ ] Match to a numbered section below
- [ ] Distinguish this from `validate_marketplace.py` findings (this is settings-scope)
- [ ] Apply the fix to settings.json
- [ ] Re-validate

## 1. settings.json Structure Issues

### CRITICAL: settings.json not found

**Error message**: `settings.json not found: <path>`
**Severity**: CRITICAL
**Source**: `validate_settings_marketplace.py` — top-level path check

**Root cause**: The path passed to the validator does not exist on disk.

**Fix**:
1. Verify the path is correct. Common locations:
   - `~/.claude/settings.json` — user-global settings
   - `<plugin>/settings.json` — plugin-shipped settings
   - `<repo>/.claude/settings.json` — project-local settings
2. If you intended to validate a per-plugin `marketplace.json`, use `validate_marketplace.py` instead.

---

### CRITICAL: settings.json path is not a regular file

**Error message**: `settings.json path is not a regular file`
**Severity**: CRITICAL
**Source**: `validate_settings_marketplace.py`

**Root cause**: The path exists but points to a directory, symlink loop, or special file.

**Fix**: Resolve any broken symlink and ensure the path points at a plain JSON file.

---

### CRITICAL: settings.json JSON parse error

**Error message**: `settings.json: JSON parse error: <error>`
**Severity**: CRITICAL
**Source**: `validate_settings_marketplace.py`

**Root cause**: The `settings.json` file is not valid JSON. Common causes: trailing commas, unquoted keys, single quotes, unterminated strings.

**Fix**:
1. Run `python3 -m json.tool settings.json` to see the exact parse error.
2. Fix the syntax error.
3. Re-run the validator.

NOTE: `settings.json` uses **plain JSON**, not JSONC. Comments are not allowed.

---

### CRITICAL: settings.json root must be a JSON object

**Error message**: `settings.json: root must be a JSON object`
**Severity**: CRITICAL
**Source**: `validate_settings_marketplace.py`

**Root cause**: The file parses successfully but the root is not an object (e.g., it is an array or a primitive).

**Fix**: The top level must be `{}`. Move your content inside an object:
```json
{
  "extraKnownMarketplaces": {
    "my-mp": { "source": { "source": "github", "repo": "owner/repo" } }
  }
}
```

---

## 2. extraKnownMarketplaces Entry Issues

### INFO: extraKnownMarketplaces block is empty

**Error message**: `extraKnownMarketplaces: block is empty`
**Severity**: INFO
**Source**: `validate_settings_marketplace.py` — `validate_extra_known_marketplaces()`

**Root cause**: The `extraKnownMarketplaces` key exists but has no entries.

**Fix**: This is informational only — no action required. The validator just notes the block is present but unused.

---

### MINOR: Marketplace entry key is not kebab-case

**Error message**: `extraKnownMarketplaces.<id>: name '<id>' should be kebab-case (lowercase, digits, hyphens)`
**Severity**: MINOR
**Source**: `validate_settings_marketplace.py` — `validate_extra_known_marketplaces()`

**Root cause**: A marketplace entry has NO separate `name` field — the entry's *key* under `extraKnownMarketplaces` IS the marketplace identifier. That key must be kebab-case.

**Fix**: Rename the entry key to lowercase letters, digits, and hyphens:
```json
{
  "extraKnownMarketplaces": {
    "my-marketplace": {
      "source": { "source": "github", "repo": "emasoft/claude-plugins" }
    }
  }
}
```

NOTE: The `name` field shown in some examples below is optional decoration; the validator never requires it on a marketplace entry. (A `name` field IS required on each *plugin* inside an inline `settings` marketplace — see §3.5.)

---

### MAJOR: Marketplace entry missing 'source'

**Error message**: `extraKnownMarketplaces.<id>: missing required 'source' object`
**Severity**: MAJOR
**Source**: `validate_settings_marketplace.py` — `validate_extra_known_marketplaces()`

**Root cause**: Each marketplace entry must have a `source` object that declares how Claude Code should fetch the marketplace.

**Fix**: Add a `source` block — see §3 below for the supported source types.

---

## 3. Source Type Issues

Each marketplace entry's `source` must be an object whose `source` key is one of:

| Type | Required fields | Notes |
|------|-----------------|-------|
| `github` | `repo` (`owner/name`) | Most common — resolves to `https://github.com/<repo>` |
| `url` | `url` | Arbitrary HTTPS URL for a marketplace tarball/repo |
| `git-subdir` | `url`, `path` | Points to a subdirectory within a git repo (v2.1.69+) |
| `npm` | `package` | Resolves via the npm registry |
| `settings` | `name`, `plugins` | **Inline marketplace** defined directly in this settings.json (v2.1.80) |
| `git` | `url` | Generic git URL (less common than github — use for self-hosted) |
| `directory` | `path` | **Dev only** — local filesystem path |
| `file` | `path` | Absolute path to a `marketplace.json` file (v2.1.98+) — machine-local |
| `hostPattern` | `hostPattern` | Regex matching a marketplace host (v2.1.98+) |
| `pathPattern` | `pathPattern` | Regex matching a filesystem path for self-hosted git (v2.1.98+) |

---

### 3.1 Source type `github`

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'github' missing required field(s): repo`
**Error pattern**: `extraKnownMarketplaces.<id>.source.repo: must be a string, got <type>`
**Error pattern**: `extraKnownMarketplaces.<id>.source.repo: '<value>' is not in 'owner/name' format`

**Fix**:
```json
{
  "extraKnownMarketplaces": {
    "my-mp": {
      "name": "My Marketplace",
      "source": {
        "source": "github",
        "repo": "emasoft/claude-plugins"
      }
    }
  }
}
```

The `repo` value must be a simple `owner/name` string. Do not include `https://github.com/`, `.git`, or a trailing slash.

---

### 3.2 Source type `url`

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'url' missing required field(s): url`
**Error pattern**: `extraKnownMarketplaces.<id>.source.url: must be a string`
**Error pattern**: `extraKnownMarketplaces.<id>.source.url: should start with http:// or https://` (MINOR)

**Fix**:
```json
{
  "source": {
    "source": "url",
    "url": "https://example.com/marketplace.tar.gz"
  }
}
```

Prefer HTTPS over HTTP for the same reasons HTTPS is recommended for MCP remote transports.

---

### 3.3 Source type `git-subdir`

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'git-subdir' missing required field(s): url, path`

**Fix** (points to a subfolder within a larger git repo, available since v2.1.69):
```json
{
  "source": {
    "source": "git-subdir",
    "url": "https://github.com/emasoft/monorepo",
    "path": "marketplaces/public"
  }
}
```

The `path` is interpreted relative to the repository root after cloning.

---

### 3.4 Source type `npm`

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'npm' missing required field(s): package`
**Error pattern**: `extraKnownMarketplaces.<id>.source.package: must be a string`
**Error pattern**: `extraKnownMarketplaces.<id>.source.package: must be a non-empty string`

**Fix**:
```json
{
  "source": {
    "source": "npm",
    "package": "@my-org/my-marketplace"
  }
}
```

---

### 3.5 Source type `settings` **[NEW in v2.1.80]**

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'settings' missing required field(s): name, plugins`

**Root cause**: This is the v2.1.80 **inline marketplace**. Instead of pointing at a remote repo, the marketplace definition (including its plugin list) lives directly inside the same settings.json file.

**Fix** — the `source` object itself must contain a complete marketplace payload with `name` and `plugins` array:
```json
{
  "extraKnownMarketplaces": {
    "my-inline": {
      "name": "My Inline Marketplace",
      "source": {
        "source": "settings",
        "name": "my-inline",
        "plugins": [
          {
            "name": "my-plugin",
            "source": {
              "source": "github",
              "repo": "emasoft/my-plugin"
            }
          }
        ]
      }
    }
  }
}
```

**Why this exists**: Inline marketplaces let enterprise admins ship a locked-down catalog via the settings layer without requiring a separate repository. They are the settings-layer equivalent of a full `marketplace.json`.

---

### 3.6 Source type `git`

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'git' missing required field(s): url`

**Root cause**: Generic git URL (use `github` instead for github.com repos — `git` is intended for self-hosted GitLab, Gitea, etc.).

**Fix**:
```json
{
  "source": {
    "source": "git",
    "url": "https://gitlab.example.com/infra/claude-marketplace.git"
  }
}
```

---

### 3.7 Source type `directory`

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'directory' missing required field(s): path`

**Root cause**: **Dev-only** source type — points at a local filesystem path. Not for distribution.

**Fix**:
```json
{
  "source": {
    "source": "directory",
    "path": "/Users/me/code/my-marketplace"
  }
}
```

**WARNING**: `directory` sources break if the settings.json is shared across machines. Use this only in your own development setup.

---

### 3.9 Source type `file` **[v2.1.98+]**

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'file' missing required field(s): path`
**Error pattern**: `extraKnownMarketplaces.<id>.source.path: must be a string, got <type>`

**Root cause**: Points at an **absolute path** to a `marketplace.json` file on the local machine. Like `directory`, it is machine-local and emits a WARNING advising against shipping it in a plugin settings snippet.

**Fix**:
```json
{
  "source": {
    "source": "file",
    "path": "/Users/me/marketplaces/hub/.claude-plugin/marketplace.json"
  }
}
```

---

### 3.10 Source type `hostPattern` **[v2.1.98+]**

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'hostPattern' missing required field(s): hostPattern`
**Error pattern**: `extraKnownMarketplaces.<id>.source.hostPattern: must be a string, got <type>`
**Error pattern**: `extraKnownMarketplaces.<id>.source.hostPattern: invalid regex '<value>' — <error>` (MINOR)

**Root cause**: The `hostPattern` value is a **regex** matched against a marketplace host. An invalid regex compiles to nothing and silently never matches.

**Fix**:
```json
{
  "source": {
    "source": "hostPattern",
    "hostPattern": "^github\\.example\\.com$"
  }
}
```

---

### 3.11 Source type `pathPattern` **[v2.1.98+]**

**Error pattern**: `extraKnownMarketplaces.<id>.source: source type 'pathPattern' missing required field(s): pathPattern`
**Error pattern**: `extraKnownMarketplaces.<id>.source.pathPattern: must be a string, got <type>`
**Error pattern**: `extraKnownMarketplaces.<id>.source.pathPattern: invalid regex '<value>' — <error>` (MINOR)

**Root cause**: The `pathPattern` value is a **regex** matched against a filesystem path (self-hosted git). An invalid regex silently never matches.

**Fix**:
```json
{
  "source": {
    "source": "pathPattern",
    "pathPattern": "^/srv/git/.*\\.git$"
  }
}
```

---

### 3.8 Unknown source type

**Error pattern**: `extraKnownMarketplaces.<id>.source: unknown source type '<type>' (valid: directory, file, git, git-subdir, github, hostPattern, npm, pathPattern, settings, url)`

**Fix**: Use one of the valid types from §3.1–3.11. Common typos:
- `"git-hub"` → `"github"`
- `"tarball"` → `"url"`
- `"subdir"` → `"git-subdir"`

---

## 4. Differences from validate_marketplace.py

`validate_settings_marketplace.py` and `validate_marketplace.py` are **different validators** that validate different files and use different source-type schemas:

| Aspect | validate_settings_marketplace.py | validate_marketplace.py |
|--------|---------------------------------|------------------------|
| Target file | `settings.json` | `marketplace.json` |
| Top-level key | `extraKnownMarketplaces` | `plugins` |
| Scope | Per-**marketplace** source entries | Per-**plugin** source entries |
| Valid source types | `github`, `url`, `git-subdir`, `npm`, `settings`, `git`, `directory`, `file`, `hostPattern`, `pathPattern` | `github`, `url`, `npm`, `git`, `git-subdir`, `directory` |
| `settings` source type | **Supported** (inline marketplace) | Not valid |
| `git` source type | **Supported** (generic git URL) | Accepted as a CPV-only alias for `url` (emits a NIT nudging to `url`) |
| Per-plugin field checks | Not performed | Performed (name, version, tags, author, …) |

**When to run which validator**:
- You are editing a `.claude/settings.json` or `~/.claude/settings.json` with `extraKnownMarketplaces` → use `validate_settings_marketplace.py`
- You are editing a `marketplace.json` file that declares plugins → use `validate_marketplace.py`
- You are editing a plugin's `.claude-plugin/plugin.json` → use `validate_plugin.py`

All three validators share the same `cpv_validation_common.ValidationReport` class, so reports from each can be combined when using the `cpv-doctor` or `cpv-validate-github-marketplace` commands.
