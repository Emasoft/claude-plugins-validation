# Plugin Structure — Validation Issues and Fixes

## Table of Contents

- [1. Plugin Manifest Issues](#1-plugin-manifest-issues)
- [2. Directory Structure Issues](#2-directory-structure-issues)
- [3. Command File Issues](#3-command-file-issues)
- [4. Agent File Issues](#4-agent-file-issues)
- [5. Hook Configuration Issues](#5-hook-configuration-issues)
- [6. MCP Server Issues](#6-mcp-server-issues)
- [7. Script Quality Issues](#7-script-quality-issues)
- [8. Cross-Platform Compatibility Issues](#8-cross-platform-compatibility-issues)
- [9. Skill Validation Issues](#9-skill-validation-issues)
- [10. README and LICENSE Issues](#10-readme-and-license-issues)
- [11. Rules Validation Issues](#11-rules-validation-issues)
- [12. Path and Private Info Issues](#12-path-and-private-info-issues)
- [13. .gitignore Issues](#13-gitignore-issues)
- [14. Workflow Inline Python Issues](#14-workflow-inline-python-issues)

## Checklist

- [ ] Read the failing finding's message, file path, and line number
- [ ] Locate the matching section (1-14) in the TOC above
- [ ] Read the target file in full before editing (stale context = broken edits)
- [ ] Apply the exact edit pattern the section prescribes
- [ ] Re-validate the plugin to confirm the finding is gone and no new ones appeared

Comprehensive remediation guide for all issues detected by `validate_plugin.py`.
Every entry includes the **exact error message** (for automated matching), severity,
root cause, and step-by-step fix instructions.

---

## 1. Plugin Manifest Issues

### CRITICAL: plugin.json not found

**Error message**: `plugin.json not found`
**Severity**: CRITICAL
**File**: `.claude-plugin/plugin.json`
**Root cause**: The plugin directory is missing the required manifest file. Without it, Claude Code cannot identify or load the plugin.
**Fix**:
1. Create the `.claude-plugin/` directory at the plugin root:
   ```bash
   mkdir -p .claude-plugin
   ```
2. Create `plugin.json` inside it with the required `name` field plus recommended fields:
   ```json
   {
     "name": "my-plugin",
     "version": "1.0.0",
     "description": "What this plugin does"
   }
   ```
3. Re-run validation.

### CRITICAL: Invalid JSON in plugin.json

**Error message**: `Invalid JSON in plugin.json: <parse error details>`
**Severity**: CRITICAL
**File**: `.claude-plugin/plugin.json`
**Root cause**: The plugin.json file contains malformed JSON (missing commas, trailing commas, unquoted keys, etc.).
**Fix**:
1. Open `.claude-plugin/plugin.json` in an editor with JSON validation (e.g., VS Code).
2. Fix the syntax error reported in the message. Common issues:
   - Trailing commas after the last property
   - Missing quotes around keys or string values
   - Unescaped special characters in strings
3. Validate with `python -c "import json; json.load(open('.claude-plugin/plugin.json'))"`.
4. Re-run validation.

### CRITICAL: Missing required field 'name' in plugin.json

**Error message**: `Missing required field 'name' in plugin.json`
**Severity**: CRITICAL
**File**: `.claude-plugin/plugin.json`
**Root cause**: The `name` field is the only strictly required field per Anthropic docs. Without it, the plugin cannot be identified.
**Fix**:
1. Add the `name` field to your `plugin.json`:
   ```json
   {
     "name": "my-plugin-name"
   }
   ```
2. The name must be lowercase, kebab-case, no spaces, matching `^[a-z][a-z0-9-]*$`.
3. Re-run validation.

### MAJOR: plugin.json EXISTS but should NOT for marketplace-only (strict=false)

**Error message**: `plugin.json EXISTS but should NOT for marketplace-only (strict=false). Remove .claude-plugin/plugin.json to fix CLI uninstall issues.`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: When using `strict=false` (marketplace-only distribution), the plugin.json must not exist because it causes CLI uninstall issues.
**Fix**:
1. Delete the `.claude-plugin/plugin.json` file.
2. Ensure your marketplace.json at the repository root handles plugin metadata instead.
3. Re-run validation with `--marketplace-only` flag.

### MINOR: Missing recommended field in plugin.json

**Error message**: `Missing recommended field '<field>' in plugin.json`
**Severity**: MINOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: The `version` and/or `description` fields are recommended but missing. While not strictly required, they improve discoverability and version tracking.
**Fix**:
1. Add the missing field(s) to your `plugin.json`:
   ```json
   {
     "name": "my-plugin",
     "version": "1.0.0",
     "description": "A concise description of what this plugin does"
   }
   ```
2. Version must follow semver format (e.g., `1.0.0`, `2.3.1`).

### MAJOR: Plugin name must be lowercase

**Error message**: `Plugin name must be lowercase: <name>`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: Plugin names must be fully lowercase. Mixed or uppercase names will fail resolution.
**Fix**:
1. Change the `name` field to all lowercase:
   ```json
   { "name": "my-plugin" }
   ```
   Not: `{ "name": "My-Plugin" }` or `{ "name": "MY_PLUGIN" }`

### MAJOR: Plugin name cannot contain spaces

**Error message**: `Plugin name cannot contain spaces: <name>`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: Spaces in plugin names break CLI commands and path resolution.
**Fix**:
1. Replace spaces with hyphens:
   ```json
   { "name": "my-cool-plugin" }
   ```
   Not: `{ "name": "my cool plugin" }`

### MAJOR: Plugin name must be kebab-case

**Error message**: `Plugin name must be kebab-case: <name>`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: Plugin names must match the regex `^[a-z][a-z0-9-]*$` — start with a letter, lowercase alphanumeric and hyphens only.
**Fix**:
1. Rename to kebab-case format:
   ```json
   { "name": "my-plugin-v2" }
   ```
   Not: `{ "name": "my_plugin" }` or `{ "name": "123-plugin" }` or `{ "name": "my.plugin" }`

### MAJOR: Version must be semver format

**Error message**: `Version must be semver format: <version>`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: The version string does not match semver pattern `MAJOR.MINOR.PATCH`.
**Fix**:
1. Use a valid semver string:
   ```json
   { "version": "1.0.0" }
   ```
   Not: `{ "version": "v1.0" }` or `{ "version": "1.0" }` or `{ "version": "latest" }`
2. Pre-release suffixes are allowed: `1.0.0-beta.1`, `2.0.0-rc.1`.

### WARNING: Unknown manifest field

**Error message**: `Unknown manifest field '<key>' — not part of the Claude Code plugin spec. If used by plugin scripts, consider documenting it.`
**Severity**: WARNING
**File**: `.claude-plugin/plugin.json`
**Root cause**: A field in plugin.json is not part of the known Claude Code plugin spec. This is not blocking — custom fields are allowed — but should be documented.
**Fix**:
1. If the field is needed by your plugin scripts, add a comment in README.md explaining its purpose.
2. If it is a typo, correct it. Known fields (the exact `known_fields` set in `validate_plugin.py`) are: `name`, `$schema` (v2.1.120), `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `commands`, `agents`, `skills`, `hooks`, `mcpServers`, `outputStyles`, `themes` (v2.1.118), `lspServers`, `monitors` (v2.1.105), `userConfig` (v2.1.80), `channels` (v2.1.85), `dependencies` (v2.1.110), `defaultEnabled` (v2.1.154 — ship disabled when false), `experimental` (v2.1.129 — preferred wrapper for opt-in features like `themes`/`monitors`), and `cpv` (the CPV-managed `cpv.strip` config block emitted by the generator).
3. If it is truly unused, remove it.

### MAJOR: Field 'repository' must be a string URL

**Error message**: `Field 'repository' must be a string URL (e.g. "https://github.com/user/repo"), not <type>. Claude Code rejects object format like {"type":"git","url":"..."}.`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: Claude Code requires `repository` to be a plain string URL, not an npm-style object.
**Fix**:
1. Change from object to string:
   ```json
   {
     "repository": "https://github.com/user/repo"
   }
   ```
   Not:
   ```json
   {
     "repository": { "type": "git", "url": "https://github.com/user/repo.git" }
   }
   ```

### MAJOR: 'author' object missing required 'name' field

**Error message**: `'author' object missing required 'name' field`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: When `author` is an object, it must include a `name` field.
**Fix**:
1. Add `name` to the author object:
   ```json
   { "author": { "name": "Your Name", "email": "you@example.com" } }
   ```
2. Or use a simple string instead:
   ```json
   { "author": "Your Name <you@example.com>" }
   ```

### MAJOR: 'author.name' must be a string

**Error message**: `'author.name' must be a string`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: The `name` field inside the `author` object is not a string type.
**Fix**:
1. Ensure `author.name` is a string:
   ```json
   { "author": { "name": "Your Name" } }
   ```

### MAJOR: 'author' must be a string or object

**Error message**: `'author' must be a string or object, got <type>`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: The `author` field is an unexpected type (e.g., array, number, boolean).
**Fix**:
1. Use either string or object format:
   ```json
   { "author": "Your Name" }
   ```
   or:
   ```json
   { "author": { "name": "Your Name" } }
   ```

### MAJOR: 'keywords' must be an array

**Error message**: `'keywords' must be an array`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: The `keywords` field is not an array (e.g., it might be a string).
**Fix**:
1. Use an array of strings:
   ```json
   { "keywords": ["linting", "code-quality", "python"] }
   ```
   Not: `{ "keywords": "linting, code-quality" }`

### MAJOR: 'keywords' must contain only strings

**Error message**: `'keywords' must contain only strings`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: One or more items in the `keywords` array is not a string.
**Fix**:
1. Ensure every element is a string:
   ```json
   { "keywords": ["linting", "formatting", "python"] }
   ```
   Not: `{ "keywords": ["linting", 42, true] }`

### MAJOR: String field must be a string

**Error message**: `'<field>' must be a string, got <type>`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: The `homepage` or `license` field is not a string type.
**Fix**:
1. Use a plain string:
   ```json
   {
     "homepage": "https://example.com",
     "license": "MIT"
   }
   ```

### MAJOR: Component path must start with './'

**Error message**: `Field '<key>' path must start with './': <value>`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: Path fields (`commands`, `agents`, `skills`, `hooks`, `mcpServers`, `outputStyles`, `lspServers`) must use relative paths starting with `./`.
**Fix**:
1. Prefix the path with `./`:
   ```json
   {
     "commands": "./commands",
     "skills": "./skills"
   }
   ```
   Not: `{ "commands": "commands" }` or `{ "commands": "/absolute/path" }`

### MAJOR: Array element path must start with './'

**Error message**: `Field '<key>[<index>]' path must start with './': <path>`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: An element in a path array field does not start with `./`.
**Fix**:
1. Prefix each array element path with `./`:
   ```json
   { "skills": ["./skills/skill-a", "./skills/skill-b"] }
   ```

### MAJOR: Field must be a string path or array, not an object

**Error message**: `Field '<key>' must be a string path or array, not an object`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: A component field (like `commands`, `agents`, `skills`, `outputStyles`) uses an inline object, but only `hooks`, `mcpServers`, and `lspServers` support inline object configuration.
**Fix**:
1. Use a string path or array of paths instead:
   ```json
   { "commands": "./commands" }
   ```
2. Only `hooks`, `mcpServers`, and `lspServers` may use inline objects.

### CRITICAL / MINOR: Redundant default-path declaration (string)

**Severity depends on the field** (verified empirically 2026-04-18). `validate_plugin.py` splits this check by whether pointing at the default actually breaks loading:

- **`hooks` → CRITICAL** (`"hooks": "./hooks/"`, the directory): CC's manifest validator rejects the default-directory form with `hooks: Invalid input` and the **plugin will not load**.
  **Error message**: `Field 'hooks' points to './hooks/' which Claude Code rejects with `hooks: Invalid input` — the plugin will not load. Remove it from plugin.json — only non-standard paths need explicit declaration.`
- **`commands` / `skills` / `outputStyles` → MINOR** (redundancy nudge, harmless — this was previously CRITICAL and was a false positive; CC accepts these and the docs even endorse the array form): the field is simply redundant because CC auto-discovers the folder anyway.
  **Error message**: `Field '<key>' points to '<default_path>' which Claude Code auto-discovers anyway. This declaration is redundant. Remove the field from plugin.json (the default folder is scanned automatically).`
- **`agents` → handled by the dedicated agents-folder check below** (a folder path in `agents` is ALWAYS rejected — see "`agents` field contains a folder path"). The redundant-default check skips `agents` so the richer message wins.

**File**: `.claude-plugin/plugin.json`
**Root cause**: Claude Code auto-discovers the standard directories (`commands/`, `agents/`, `skills/`, `hooks/`, `output-styles/`) at the plugin root. For `hooks`, naming the default directory breaks loading; for the others it is merely redundant.
**Fix**:
1. For `hooks` (CRITICAL): remove `"hooks": "./hooks/"` from `plugin.json` — the default `hooks/hooks.json` is loaded automatically.
2. For `commands` / `skills` / `outputStyles` (MINOR): removing the redundant field is recommended but not required; only declare these when pointing to a **non-standard** location (e.g., `"commands": "./src/my-commands/"`).
3. Minimal correct manifest:
   ```json
   {
     "name": "my-plugin",
     "version": "1.0.0",
     "description": "My plugin"
   }
   ```
4. Default paths per field (the `auto_discovered_defaults` map): `commands` → `./commands/`, `agents` → `./agents/`, `skills` → `./skills/`, `hooks` → `./hooks/`, `outputStyles` → `./output-styles/`. Only `hooks` in this set is in `breaks_loading_when_default`.

### CRITICAL / MINOR: Redundant default-path declaration (array)

Same severity split as the string form, but the field is an **array of items inside** the default directory (e.g., `"commands": ["./commands/cmd-a.md", "./commands/cmd-b.md"]`).

- **`hooks` → CRITICAL**: `Field 'hooks' lists items inside './hooks/' which Claude Code rejects with `hooks: Invalid input` — the plugin will not load. Remove it from plugin.json.`
- **`commands` / `skills` / `outputStyles` → MINOR**: `Field '<key>' lists items inside '<default_path>' which Claude Code auto-discovers anyway. This is redundant. Remove the field from plugin.json (or include only items OUTSIDE the default folder).`
- **`agents` → handled by the dedicated agents-folder check below.**

**File**: `.claude-plugin/plugin.json`
**Fix**:
1. For `hooks` (CRITICAL): remove the field entirely.
2. For `commands` / `skills` / `outputStyles` (MINOR): the items inside the default folder are auto-discovered, so the list is redundant — remove it, or keep ONLY entries that point **outside** the default folder.
   ```diff
   {
     "name": "my-plugin",
   - "commands": ["./commands/cmd-a.md", "./commands/cmd-b.md"],
     "version": "1.0.0"
   }
   ```

### MAJOR: `agents` field contains a folder path

**Error message**: `Field 'agents' contains folder path '<path>' — Claude Code's manifest validator REJECTS folder paths in the 'agents' field with the cryptic error 'agents: Invalid input' (both string and array forms). Only '.md' file paths are accepted. ...`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: This is an **undocumented schema constraint** in CC's manifest validator (verified empirically 2026-04-18). The docs schema says `agents: string | array` and the docs' own complete-schema example shows `"./custom/agents/"` (a folder), but CC's validator REJECTS folder paths in the `agents` field with the message `agents: Invalid input`. Only `.md` file paths are accepted in both string and array form. Worse, if the plugin author skips `claude plugin validate` and publishes the plugin, **CC silently drops the agents at runtime** with no error in `--debug` — agents simply don't appear in the agent list.

This is a **publish-and-break** scenario. Your plugin will install but the agents won't work, and users won't know why.

**Fix**:
1. List specific `.md` file paths instead of folders:
   ```diff
   {
     "name": "my-plugin",
   - "agents": "./custom-agents/",
   + "agents": ["./custom-agents/reviewer.md", "./custom-agents/tester.md"]
   }
   ```
2. Or use the array form with one file per entry:
   ```json
   {
     "agents": [
       "./custom-agents/reviewer.md",
       "./custom-agents/tester.md"
     ]
   }
   ```
3. If the agents are in the default `agents/` folder, **remove the `agents` field entirely** — CC auto-discovers `agents/` at the plugin root.
4. **Note**: this constraint applies ONLY to `agents`. The `skills`, `commands`, and `outputStyles` fields accept folder paths normally. This is an `agents`-specific validator bug.

### MAJOR: `hooks` points at the default file (cascading MCP failure)

**Error message**: `Field 'hooks' contains '<path>' which resolves to the auto-discovered 'hooks/hooks.json' default. At runtime this triggers 'Duplicate hooks file detected' AND the cascading 'hook-load-failed' error DISABLES this plugin's MCP servers (silent partial failure — `claude plugin validate` does not catch it). Fix: remove the 'hooks' field from plugin.json (the default file is loaded automatically), or point it at a NON-default path like './hooks/extra.json'.`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: Empirically verified 2026-04-18 (test: `cpv-hooks-doublefire-test`). When `plugin.json` has `"hooks": "./hooks/hooks.json"` (or `"hooks/hooks.json"`), CC's runtime debug log shows:
```
[ERROR] Duplicate hooks file detected: ./hooks/hooks.json resolves to already-loaded file
[DEBUG] Plugin not available for MCP: <plugin>@inline - error type: hook-load-failed
```
The hook itself dedupes correctly (fires exactly once), BUT the `hook-load-failed` error CASCADES and disables this plugin's MCP servers entirely. `claude plugin validate` does NOT catch this — it passes silently — so the plugin author doesn't see the problem until users complain that MCP tools don't work.

**Fix**:
1. Remove the `hooks` field from `plugin.json`. CC auto-loads `hooks/hooks.json` at the plugin root automatically:
   ```diff
   {
     "name": "my-plugin",
   - "hooks": "./hooks/hooks.json"
   }
   ```
2. Or, if you genuinely need an additional hook file (separate from the auto-loaded one), point at a **non-default** path:
   ```json
   {
     "hooks": "./hooks/extra.json"
   }
   ```
3. Verify the fix: re-run validation and check the runtime debug log doesn't show `hook-load-failed`.

---

## 2. Directory Structure Issues

### CRITICAL: .claude-plugin directory not found

**Error message**: `.claude-plugin directory not found`
**Severity**: CRITICAL
**Root cause**: The `.claude-plugin/` directory is the primary marker that identifies a directory as a Claude Code plugin. Without it (in non-marketplace mode), the plugin cannot function.
**Fix**:
1. Create the directory at the plugin root:
   ```bash
   mkdir -p .claude-plugin
   ```
2. Add `plugin.json` inside it (see Section 1).

### CRITICAL: Component must be at plugin root, not in .claude-plugin/

**Error message**: `<component>/ must be at plugin root, not in .claude-plugin/`
**Severity**: CRITICAL
**Root cause**: Directories like `commands/`, `agents/`, `skills/`, `hooks/`, `schemas/`, `bin/`, `scripts/` were placed inside `.claude-plugin/` instead of at the plugin root. Claude Code looks for them at the root level.
**Fix**:
1. Move the directory to the plugin root:
   ```bash
   mv .claude-plugin/commands ./commands
   mv .claude-plugin/skills ./skills
   ```
2. The correct structure is:
   ```
   my-plugin/
   ├── .claude-plugin/
   │   └── plugin.json
   ├── commands/
   ├── agents/
   ├── skills/
   ├── hooks/
   ├── scripts/
   └── ...
   ```

### INFO: Optional directory not found

**Error message**: `Optional directory <dir>/ not found`
**Severity**: INFO
**Root cause**: An optional directory (`commands/`, `agents/`, `skills/`, `hooks/`, `scripts/`, `docs/`) is missing. This is purely informational — not all plugins need all directories.
**Fix**: No action required. Create the directory only if your plugin needs the corresponding feature.

### WARNING: Non-standard directory found

**Error message**: `Non-standard directory '<dirname>/' — not part of the plugin spec. If needed by plugin scripts, consider documenting its purpose in README.`
**Severity**: WARNING
**Root cause**: A directory at the plugin root is not part of the standard plugin directory set, is not a known common directory (like `lib/`, `resources/`, `assets/`, etc.), AND is not referenced from any manifest source via `${CLAUDE_PLUGIN_ROOT}/<dirname>/...`.

Since v2.23.1, CPV automatically suppresses this warning when the directory IS referenced from `.mcp.json`, `.lsp.json`, `hooks/hooks.json`, `monitors/monitors.json`, or any inline `mcpServers`/`lspServers`/`hooks`/`monitors`/`channels` field in `plugin.json`. So if you see this warning, it means the directory exists but nothing in your manifest refers to it.

**Fix** (in order of preference):
1. If the directory contains executables/scripts your plugin uses, REFERENCE them from the appropriate manifest source. For example, an MCP server bundle should be referenced from `.mcp.json`:
   ```json
   {"mcpServers": {"my-server": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/<dirname>/index.js"]}}}
   ```
   Once referenced, CPV will auto-suppress the warning.
2. If the directory is needed by your plugin scripts but not via `${CLAUDE_PLUGIN_ROOT}` substitution, document its purpose in README.md (the WARNING is advisory only — does not block publish).
3. If it is a leftover or artifact, remove it.
4. Known standard directories (always allowed): `.claude-plugin`, `commands`, `agents`, `skills`, `hooks`, `scripts`, `docs`, `rules`, `schemas`, `bin`, `monitors`, `servers`, `templates`, `tests`, `lib`, `libs`, `modules`, `resources`, `assets`, `data`, `config`, `configs`, `examples`, `samples`, `references`, `git-hooks`, `shared`, `fixtures`, `vendor`, `src`, `dist`, `build`, `out`, `target`, `output-styles`, `design`.

### MAJOR: Plugin has manifest but no content

**Error message**: `Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json`
**Severity**: MAJOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: The plugin has a `.claude-plugin/plugin.json` manifest but contains no actual content directories or configuration files. A plugin with only a manifest serves no purpose — it needs at least one component.
**Fix**:
1. Add at least one component to your plugin:
   - `commands/` — slash commands (`.md` files)
   - `agents/` — agent definitions (`.md` files)
   - `skills/` — skill directories (each with `SKILL.md`)
   - `hooks/` — hook configuration (`hooks.json`)
   - `scripts/` — utility scripts
   - `.mcp.json` — MCP server definitions
   - `.lsp.json` — LSP server definitions
2. A minimal plugin needs at least one of these to be useful.
3. Re-run validation after adding content.

### MAJOR: settings.json parse error

**Error message**: `settings.json: JSON parse error: <error>`
**Severity**: MAJOR
**File**: `settings.json`
**Root cause**: The plugin ships a `settings.json` file but it contains invalid JSON.
**Fix**:
1. Fix the JSON syntax in `settings.json`.
2. Validate with: `python -c "import json; json.load(open('settings.json'))"`

### MINOR: settings.json unrecognized key

**Error message**: `settings.json: unrecognized key '<key>' — supported plugin settings: agent, extraKnownMarketplaces, strictKnownMarketplaces, subagentStatusLine`
**Severity**: MINOR
**File**: `settings.json`
**Root cause**: The plugin-shipped `settings.json` contains a key that is not in CPV's `recognized_keys` set. The recognized plugin-level settings are `agent`, `extraKnownMarketplaces` (v2.1.80 inline-marketplace declaration), `strictKnownMarketplaces` (admin-managed scope — also MAJOR-checked separately), and `subagentStatusLine` (plugin-scoped status-line override).
**Fix**:
1. Remove or rename the unrecognized key.
2. Use one of the recognized keys, e.g.:
   ```json
   {
     "agent": "my-custom-agent"
   }
   ```
3. If the key is needed by your plugin scripts, consider moving it to a custom configuration file instead of `settings.json`.

---

## 3. Command File Issues

### CRITICAL: No frontmatter in command file

**Error message**: `No frontmatter in command file`
**Severity**: CRITICAL
**File**: `commands/<filename>.md`
**Root cause**: Command files must start with YAML frontmatter delimited by `---`. The file does not begin with `---`.
**Fix**:
1. Add YAML frontmatter to the top of the command file:
   ```markdown
   ---
   name: my-command
   description: What this command does
   ---

   # Command instructions here
   ```

### CRITICAL: Malformed frontmatter (missing closing ---)

**Error message**: `Malformed frontmatter (missing closing ---)`
**Severity**: CRITICAL
**File**: `commands/<filename>.md`
**Root cause**: The frontmatter block opens with `---` but never closes with a second `---`.
**Fix**:
1. Add the closing `---` delimiter:
   ```markdown
   ---
   name: my-command
   description: What this command does
   ---
   ```

### CRITICAL: Invalid YAML frontmatter

**Error message**: `Invalid YAML frontmatter: <yaml error>`
**Severity**: CRITICAL
**File**: `commands/<filename>.md`
**Root cause**: The YAML between the `---` delimiters has syntax errors.
**Fix**:
1. Fix the YAML syntax. Common issues:
   - Missing space after colon: `name:value` should be `name: value`
   - Unquoted special characters: use quotes around values with `:`, `#`, `[`, `{`
   - Incorrect indentation (YAML uses spaces, not tabs)
2. Validate with: `python -c "import yaml; yaml.safe_load(open('commands/my-command.md').read().split('---')[1])"`

### CRITICAL: Empty frontmatter

**Error message**: `Empty frontmatter`
**Severity**: CRITICAL
**File**: `commands/<filename>.md`
**Root cause**: The frontmatter section exists but contains no fields.
**Fix**:
1. Add at least the `name` field:
   ```markdown
   ---
   name: my-command
   description: What this command does
   ---
   ```

### CRITICAL: Missing 'name' in frontmatter (command)

**Error message**: `Missing 'name' in frontmatter`
**Severity**: CRITICAL
**File**: `commands/<filename>.md`
**Root cause**: The command file's frontmatter is missing the required `name` field.
**Fix**:
1. Add the `name` field. It should match the filename (without extension):
   ```markdown
   ---
   name: my-command
   ---
   ```
   For a file named `my-command.md`, the name should be `my-command`.

### MAJOR: Command name doesn't match filename

**Error message**: `Command name '<name>' doesn't match filename '<expected>'`
**Severity**: MAJOR
**File**: `commands/<filename>.md`
**Root cause**: The `name` in frontmatter does not match the filename stem. Claude Code uses filenames for command resolution, so a mismatch causes confusion.
**Fix**:
1. Either rename the file to match the name, or change the name to match the file:
   - File `deploy-app.md` → `name: deploy-app`
   - Or rename file to match the name in frontmatter.

### MAJOR: Missing 'description' in frontmatter (command)

**Error message**: `Missing 'description' in frontmatter`
**Severity**: MAJOR
**File**: `commands/<filename>.md`
**Root cause**: The command file is missing a `description` field. Descriptions are shown in help text and command listings.
**Fix**:
1. Add a description:
   ```markdown
   ---
   name: my-command
   description: Deploys the application to the staging environment
   ---
   ```

---

## 4. Agent File Issues

### CRITICAL: No frontmatter in agent file

**Error message**: `No frontmatter in agent file`
**Severity**: CRITICAL
**File**: `agents/<filename>.md`
**Root cause**: Agent files must start with YAML frontmatter delimited by `---`.
**Fix**:
1. Add YAML frontmatter to the top of the agent file:
   ```markdown
   ---
   name: my-agent
   description: What this agent does
   ---

   # Agent instructions here
   ```

### CRITICAL: Malformed frontmatter (missing closing ---) (agent)

**Error message**: `Malformed frontmatter (missing closing ---)`
**Severity**: CRITICAL
**File**: `agents/<filename>.md`
**Root cause**: The frontmatter block opens with `---` but has no closing `---`.
**Fix**: Same as the command file fix — add the closing `---` delimiter.

### CRITICAL: Invalid YAML frontmatter (agent)

**Error message**: `Invalid YAML frontmatter: <yaml error>`
**Severity**: CRITICAL
**File**: `agents/<filename>.md`
**Root cause**: YAML syntax errors in the agent frontmatter.
**Fix**: Same as the command file fix — correct the YAML syntax.

### CRITICAL: Empty frontmatter (agent)

**Error message**: `Empty frontmatter`
**Severity**: CRITICAL
**File**: `agents/<filename>.md`
**Root cause**: The agent frontmatter section is empty.
**Fix**: Add at least the `name` field to the frontmatter.

### CRITICAL: Missing 'name' in frontmatter (agent)

**Error message**: `Missing 'name' in frontmatter`
**Severity**: CRITICAL
**File**: `agents/<filename>.md`
**Root cause**: The agent file's frontmatter is missing the required `name` field.
**Fix**:
1. Add the `name` field:
   ```markdown
   ---
   name: my-agent
   description: An agent that handles code review
   ---
   ```

### MAJOR: Missing 'description' in frontmatter (agent)

**Error message**: `Missing 'description' in frontmatter`
**Severity**: MAJOR
**File**: `agents/<filename>.md`
**Root cause**: The agent file is missing a `description` field. Descriptions help users understand the agent's purpose.
**Fix**:
1. Add a description:
   ```markdown
   ---
   name: my-agent
   description: Handles automated code review and linting suggestions
   ---
   ```

### MAJOR: Plugin-shipped agent has forbidden field

**Error message**: `Field '<hooks|mcpServers|permissionMode>' is not supported for plugin-shipped agents`
**Severity**: MAJOR
**File**: `agents/<filename>.md`
**Source**: `cpv_validation_common.validate_plugin_shipped_restrictions()` — called from `validate_agent.py` and `validate_plugin.py`
**Root cause**: Per the plugins-reference security rule: "For security reasons, `hooks`, `mcpServers`, and `permissionMode` are not supported for plugin-shipped agents." These fields only make sense for user-defined agents where the user has explicit control over what runs.

The three forbidden fields are:
- `hooks` — would let an agent install its own hook handlers
- `mcpServers` — would let an agent launch arbitrary MCP servers
- `permissionMode` — would let an agent change its own permission posture (e.g., `dangerouslySkipPermissions`)

**Fix**: Remove the forbidden field from the agent's frontmatter:
```markdown
---
name: my-agent
description: ...
# REMOVE all of these from plugin-shipped agents:
# hooks: ...
# mcpServers: ...
# permissionMode: ...
---
```

If the agent genuinely needs one of these capabilities, it should NOT be shipped inside a plugin — move it to `~/.claude/agents/` where the user installs it manually.

### Monitor tool acceptance (v2.1.98)

**Error message**: (no error — Monitor is now a valid tool name as of v2.1.98)
**Severity**: INFO (not flagged)
**Source**: `cpv_validation_common.VALID_TOOLS`

The `Monitor` tool runs a command in the background and feeds each output line to Claude. It uses the same permission semantics as `Bash` (runs arbitrary shell commands). As of v2.1.98, `Monitor` is recognised in agent `tools:` lists alongside `Bash`, `Read`, `Edit`, and the rest of the standard tool names.

**Strict-mode restriction** (applies to skills only — see [skill-fixes.md](skill-fixes.md) §3): unscoped `Monitor` is forbidden in strict mode, just like unscoped `Bash`. Use scoped forms:
```yaml
allowed-tools:
  - Monitor(git:*)
  - Monitor(npm:run:*)
```

### WARNING: TaskOutput tool is deprecated

**Error message**: `Tool 'TaskOutput' is deprecated — prefer Read on the task's output file path`
**Severity**: WARNING
**File**: `agents/<filename>.md` or `skills/*/SKILL.md`
**Source**: `validate_agent.py` and `validate_skill_comprehensive.py`
**Root cause**: `TaskOutput` was introduced in v2.1.71 but has since been deprecated. Agents and skills should read the task's output file path directly with `Read` instead.

**Fix**:
```yaml
# WRONG — uses deprecated TaskOutput
tools: [Read, TaskOutput]

# RIGHT — use Read on the output file path
tools: [Read]
```

Read the task's output file path from whichever agent/tool exposed it, then use `Read` to pull the contents. This works across all tool chains and does not bind you to a deprecated tool name.

### WARNING: Task tool was renamed to Agent in v2.1.63

**Error message**: `Tool 'Task' was renamed to 'Agent' in v2.1.63; 'Task' still works as an alias`
**Severity**: WARNING
**File**: `agents/<filename>.md` or `skills/*/SKILL.md`
**Source**: `validate_agent.py` and `validate_skill_comprehensive.py`
**Root cause**: The tool formerly known as `Task` is now called `Agent`. Both names still work — `Task` is an alias.

**Fix**: Update to the new name when you touch the file next:
```yaml
# Before (still works):
tools: [Read, Task]

# After (recommended):
tools: [Read, Agent]
```

### WARNING: Legacy tool not in current tools-reference spec

**Error message**: `Tool '<TodoRead|Notebook|MultiEdit>' is not in the current tools-reference spec. Verify existence before shipping.`
**Severity**: WARNING
**Source**: `validate_agent.py` and `validate_skill_comprehensive.py`
**Root cause**: `TodoRead`, `Notebook`, and `MultiEdit` are legacy tool names that are no longer listed in the canonical `tools-reference`. They may still work on older Claude Code builds but should not be relied upon for new plugins.

**Fix**:
- `TodoRead` → use `TaskList` or `TaskGet`
- `Notebook` → use `NotebookEdit`
- `MultiEdit` → use `Edit` (current `Edit` tool supports one surgical change per call; for multiple changes, call `Edit` multiple times)

### WARNING: Legacy agent frontmatter fields

**Error message**: `Field '<capabilities|context|agent|user-invocable|system-prompt>' is not in the current sub-agents spec (v2.1.98). It may be legacy/extended. Verify it still works with your installed Claude Code version.` (each field emits its own message)
**Severity**: WARNING
**File**: `agents/<filename>.md`
**Source**: `validate_agent.py` — `validate_capabilities_field()`, `validate_context_field()`, `validate_agent_field()`, `validate_user_invocable_field()`, `validate_system_prompt_field()` (each calls `report.warning()`)
**Root cause**: The following fields are accepted but not part of the current sub-agents spec (v2.1.98):
- `capabilities` — superseded by `tools`
- `context` — only meaningful in some enterprise configurations
- `agent` — meaningful only with `context: fork`
- `user-invocable` — meaningful only on skills (via `disable-model-invocation`)
- `system-prompt` — agents use the markdown body as their system prompt; this field is redundant

**Fix**: Remove these fields unless you have a specific reason to keep them. If CPV reports them as WARNINGs you are free to leave them in place — they will not cause validation to fail — but newer plugins should avoid them.

### submodule detection INFO

**Error message**: `Plugin is a submodule of <parent path>. Parent repo CI will not run this plugin's pipeline automatically.`
**Severity**: INFO
**Source**: `validate_plugin.py::validate_submodule_containment()`
**Root cause**: The plugin lives inside a parent repository as a git submodule. Parent repos do NOT run their submodules' CI automatically — the plugin's own release pipeline must be triggered independently.

**Fix**: This is informational only. No action is required. However, you should know:
- `git push` inside the submodule updates the submodule repo, NOT the parent repo's reference to it.
- The parent repo still points at the previous submodule commit until you explicitly `git submodule update --remote` and commit that change in the parent.
- The plugin's own CI (release pipeline, validation, publish) must be configured and triggered separately from the parent's CI.

If you want the parent repo to pick up submodule changes automatically, configure a repository_dispatch workflow — see `scripts/notify-marketplace.yml` template in cpv-canonical-pipeline.

### language detection INFO

**Error message**: `Detected project languages: <summary>` or `No language markers detected (pyproject.toml, package.json, Cargo.toml, etc.)`
**Severity**: INFO
**Source**: `validate_plugin.py::validate_project_languages()`
**Root cause**: Informational — the validator lists which languages it detected in the plugin based on marker files (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc.). This is used to decide which linters to run.

**Fix**: No action required. If a language you use is missing from the summary, add the canonical marker file so CPV can find it (e.g., `pyproject.toml` for Python, `go.mod` for Go).

---

## 5. Hook Configuration Issues

Hook validation is delegated to `validate_hook.py`. All results from the hook validator are transferred into the main report with paths prefixed by `hooks/`. Refer to the hook validator's own documentation for the full list of hook-specific errors. Common issues include:

- Invalid JSON in `hooks/hooks.json`
- Invalid hook event names
- Missing `command` field in hook entries
- Commands referencing non-existent scripts
- Invalid `timeout` values
- Invalid `pattern` regex in hook matchers

**Fix**: Consult the hook validation output for specific errors. Ensure `hooks/hooks.json` follows the Claude Code hook specification.

---

## 6. MCP Server Issues

MCP validation is delegated to `validate_mcp.py`. All results are transferred directly into the main report. Common issues include:

- Invalid MCP server configuration format
- Missing required fields in server entries
- Non-existent server command paths
- Invalid transport configuration

**Fix**: Consult the MCP validation output for specific errors. Ensure your MCP server configuration matches the Claude Code MCP spec.

---

## 7. Script Quality Issues

### MAJOR: Ruff lint errors in Python scripts

**Error message**: `Ruff: <count> error(s) in <file>`
**Severity**: MAJOR
**File**: `scripts/<filename>.py`
**Root cause**: Python scripts in `scripts/` have lint errors detected by ruff (E/F/W categories, excluding E501 line length).
**Fix**:
1. Run ruff to see the specific errors:
   ```bash
   uv run ruff check --select E,F,W --ignore E501 scripts/
   ```
2. Review and manually fix reported issues.

### MINOR: ruff not available

**Error message**: `ruff not available locally or via uvx, skipping Python lint check`
**Severity**: MINOR
**Root cause**: The ruff linter is not installed in the environment.
**Fix**:
1. Install ruff:
   ```bash
   uv tool install ruff
   # or
   pip install ruff
   ```

### MINOR: Mypy type check issues

**Error message**: `Mypy: <error line>`
**Severity**: MINOR
**Root cause**: Python scripts have type errors detected by mypy.
**Fix**:
1. Run mypy directly to see all errors:
   ```bash
   uv run mypy --ignore-missing-imports scripts/*.py
   ```
2. Add type annotations and fix type mismatches as reported.

### MINOR: mypy not available

**Error message**: `mypy not available locally or via uvx, skipping type check`
**Severity**: MINOR
**Root cause**: The mypy type checker is not installed in the environment.
**Fix**:
1. Install mypy:
   ```bash
   uv tool install mypy
   # or
   pip install mypy
   ```

### MAJOR: Shell script not executable

**Error message**: `Shell script not executable: <filename>`
**Severity**: MAJOR
**File**: `scripts/<filename>.sh`
**Root cause**: A `.sh` file in `scripts/` does not have the executable permission bit set.
**Fix**:
1. Set the executable bit:
   ```bash
   chmod +x scripts/<filename>.sh
   ```
2. Make sure the file has a proper shebang line:
   ```bash
   #!/usr/bin/env bash
   ```

### MINOR: Shellcheck issues

**Error message**: `Shellcheck issues in <filename>`
**Severity**: MINOR
**File**: `scripts/<filename>.sh`
**Root cause**: Shell scripts have lint warnings from shellcheck.
**Fix**:
1. Run shellcheck directly:
   ```bash
   shellcheck scripts/<filename>.sh
   ```
2. Fix the reported issues (quoting, variable expansion, deprecated syntax, etc.).
3. For intentional patterns, add inline directives: `# shellcheck disable=SC2086`

### MINOR: shellcheck not available

**Error message**: `shellcheck not available locally or via bunx/npx, skipping shell lint`
**Severity**: MINOR
**Root cause**: shellcheck is not installed.
**Fix**:
1. Install shellcheck:
   ```bash
   brew install shellcheck   # macOS
   apt install shellcheck    # Ubuntu/Debian
   ```

### MINOR: Scripts missing shebang

**Error message**: `Scripts missing shebang (e.g. #!/usr/bin/env python3): <list>. Without a shebang, scripts may not run correctly across platforms.`
**Severity**: MINOR
**File**: `scripts/`
**Root cause**: One or more script files (`.py`, `.sh`, `.bash`, `.rb`, `.pl`, `.php`) in the plugin's `scripts/` directory are missing a shebang line (`#!...`). Without a shebang, the OS cannot determine the correct interpreter.
**Fix**:
1. Add a shebang as the very first line of each script:
   ```python
   #!/usr/bin/env python3
   ```
   For bash scripts:
   ```bash
   #!/usr/bin/env bash
   ```
   For Ruby:
   ```ruby
   #!/usr/bin/env ruby
   ```
2. Common shebangs:
   | Extension | Shebang |
   |-----------|---------|
   | `.py` | `#!/usr/bin/env python3` |
   | `.sh`, `.bash` | `#!/usr/bin/env bash` |
   | `.rb` | `#!/usr/bin/env ruby` |
   | `.pl` | `#!/usr/bin/env perl` |
   | `.php` | `#!/usr/bin/env php` |
3. Use `#!/usr/bin/env <interpreter>` instead of hardcoded paths like `#!/usr/bin/python3` for portability.

---

## 8. Cross-Platform Compatibility Issues

### WARNING: Platform-specific scripts found (with known platforms)

**Error message**: `Found <count> <language> script(s) (<ext>) — only natively available on <platforms>. <note>. Consider providing cross-platform alternatives or documenting requirements.`
**Severity**: WARNING
**Root cause**: Scripts using platform-specific languages (`.sh`/`.bash` for macOS/Linux, `.ps1` for Windows, `.zsh` for macOS only, `.bat`/`.cmd` for Windows only, `.nix` for Linux) were found.
**Fix**:
1. Provide cross-platform alternatives (Python `.py`, Node.js `.js`/`.ts`).
2. Or document the platform requirements in README.md.
3. Or provide equivalent scripts for each platform (e.g., `install.sh` + `install.ps1`).

### WARNING: Platform-specific scripts found (no known platforms)

**Error message**: `Found <count> <language> script(s) (<ext>) — <note>. Consider providing cross-platform alternatives.`
**Severity**: WARNING
**Root cause**: Scripts using a language that requires separate installation on all platforms (e.g., `.fish` Fish shell) were found.
**Fix**: Same as above — provide cross-platform alternatives or document requirements.

### MAJOR: Compiled source without binaries or build script

**Error message**: `Found <count> <language> source file(s) but no compiled binaries in bin/ and no build script (build.sh, install.sh, Makefile, etc.). Provide pre-compiled binaries or a build/install script.`
**Severity**: MAJOR
**Root cause**: The plugin contains compiled language source code (Rust, Go, C, C++, Swift, Zig) but no pre-compiled binaries in `bin/` and no build script to compile them.
**Fix**:
1. Add pre-compiled binaries for major platforms to `bin/`:
   ```
   bin/
   ├── my-tool-darwin-arm64
   ├── my-tool-darwin-amd64
   ├── my-tool-linux-amd64
   └── my-tool-windows-amd64.exe
   ```
2. Or provide a build/install script at the plugin root:
   - `build.sh`, `install.sh`, `setup.sh`, `compile.sh`
   - `build.py`, `install.py`, `setup.py`
   - `Makefile`, `justfile`, `Taskfile.yml`

### WARNING: Compiled source with build system but no binaries

**Error message**: `Found <count> <language> source file(s) with build system but no pre-compiled binaries in bin/. Users will need to compile before use.`
**Severity**: WARNING
**Root cause**: Source files have a build system (e.g., `Cargo.toml`, `go.mod`, `CMakeLists.txt`) but no pre-compiled binaries.
**Fix**:
1. Pre-compile binaries for major platforms and include them in `bin/`.
2. Or document the build instructions clearly in README.md.

### WARNING: Compiled binaries missing platform coverage

**Error message**: `Compiled binaries missing for: <platforms>. Detected platforms: <detected>. Consider providing binaries for all major platforms.`
**Severity**: WARNING
**Root cause**: The `bin/` directory has compiled binaries but does not cover all recommended platforms: macOS ARM64 (Apple Silicon), macOS x86_64 (Intel), and Linux x86_64.
**Fix**:
1. Cross-compile and add binaries for missing platforms:
   ```
   bin/
   ├── my-tool-darwin-arm64      # macOS Apple Silicon
   ├── my-tool-darwin-amd64      # macOS Intel
   └── my-tool-linux-amd64       # Linux x86_64
   ```
2. Use CI/CD to automate cross-compilation (GitHub Actions, etc.).

### WARNING: Binaries without platform identifiers

**Error message**: `Found <count> binary file(s) without platform identifiers in filename. Use naming convention like 'tool-darwin-arm64', 'tool-linux-amd64', 'tool-windows-amd64.exe' for multi-platform support.`
**Severity**: WARNING
**Root cause**: Binary files in `bin/` do not follow the platform naming convention, making it unclear which platforms they support.
**Fix**:
1. Rename binaries to include platform/arch suffixes:
   ```
   my-tool-darwin-arm64
   my-tool-darwin-amd64
   my-tool-linux-amd64
   my-tool-linux-arm64
   my-tool-windows-amd64.exe
   ```
2. Supported suffix patterns: `-darwin-arm64`, `-darwin-amd64`, `-linux-amd64`, `-linux-arm64`, `-windows-amd64.exe`, etc.

---

## 9. Skill Validation Issues

Skill validation is delegated to `validate_skill_comprehensive.py` which implements 190+ rules from the AgentSkills OpenSpec, Nixtla, and Meta-Skills specifications. All results are transferred to the main report with paths prefixed by `skills/<skill-name>/`.

Common issues include:
- Missing `SKILL.md` file
- Invalid YAML frontmatter in skill files
- Missing required metadata fields
- Platform-specific scripts without alternatives
- Invalid tool definitions

**Fix**: Consult the skill validation output for specific errors. Each skill directory should contain at minimum a `SKILL.md` file with proper frontmatter. Refer to the skill-semantic-validation reference for comprehensive details.

---

## 10. README and LICENSE Issues

### MINOR: README.md not found

**Error message**: `README.md not found`
**Severity**: MINOR
**Root cause**: No README.md file at the plugin root. While not strictly required, a README helps users understand and configure the plugin.
**Fix**:
1. Create a `README.md` at the plugin root with:
   - Plugin name and description
   - Installation instructions
   - Configuration options
   - Usage examples

### WARNING: README.md missing badge-automation markers

**Error message** (v2.26.0+): `README.md has badge markdown but is missing the automation markers (<!--BADGES-START--> / <!--BADGES-END-->). CI cannot regenerate badges without the markers — wrap the badge block with those HTML comments so scripts/update_badges.py (or equivalent) can refresh versions/CI status automatically.`
**Severity**: WARNING
**Source**: `validate_plugin.py` — `validate_readme()`
**When it fires (v2.26.0)**: ONLY when the README already contains literal badge markdown — either `[![alt]({img})]({href})` image-link badges, or a raw `shields.io` / `img.shields.io` URL. A README with no badges at all does NOT trip this warning anymore (fixed in v2.26.0 — previously it fired on every badge-less README). **An empty marker block** (`<!--BADGES-START-->\n<!--BADGES-END-->` with nothing between them, waiting for CI to populate) is a **valid and common pattern** and does NOT trigger this warning either — markers present → check passes.

**Root cause**: The plugin ships badges as literal markdown that CI cannot auto-refresh. Version numbers in badges drift out of sync with `plugin.json` on every release; CI-status badges cache stale results without an automated regeneration pass.

**Two legitimate fixes**:

#### Fix A: Wrap existing badges with the automation markers (preferred in almost all cases)

Add the `<!--BADGES-START-->` / `<!--BADGES-END-->` HTML comments around the existing badge block. The badge markdown stays where it is; only the two HTML-comment wrappers are added:

```markdown
# My Plugin

<!--BADGES-START-->
[![CI](https://github.com/owner/repo/actions/workflows/ci.yml/badge.svg)](https://github.com/owner/repo/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](https://github.com/owner/repo)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
<!--BADGES-END-->

<description>...
```

The `scripts/generate_plugin_repo.py` scaffold emits this form by default, so new plugins get the markers for free. For existing READMEs, just add the two comments around whatever badges are already there.

**Valid CI-placeholder variant**: if the plugin's CI populates badges on push, the wrapped region can be left empty — CI fills it in:

```markdown
<!--BADGES-START-->
<!--BADGES-END-->
```

The validator passes this form (markers present → pass) and the fixer MUST preserve it — treat empty-between-markers as intentional, not a cleanup target.

#### Fix B: Remove the literal badge MARKDOWN (not the markers)

Only use this when the plugin genuinely doesn't want badges at all (minimal README, no CI integration, hand-maintained project metadata). Delete the `[![...]({url})]({href})` and `shields.io` URLs. The HTML-comment markers can stay or go — if they stay empty, the validator still passes (they become harmless placeholders); if they go, a badge-less README is silent.

**Scope of this fix**: what is removed is the LITERAL badge markdown (`[![CI]({img})]({href})`, shields.io URLs, plain-image badge lines). NEVER interpret this fix as permission to delete `<!--BADGES-START-->` / `<!--BADGES-END-->` markers — those are a CI-integration signal, not "bad content". If in doubt, prefer Fix A.

#### Forbidden "fixes"

- ❌ Deleting `<!--BADGES-START-->` / `<!--BADGES-END-->` markers to "clean up" a README. These markers are a contract with CI workflows that regenerate badges on push; removing them silently breaks automation and is not reversible from the validator's output alone. If the markers look empty, assume CI populates them.
- ❌ Adding the markers around an empty region just to silence the warning while the actual badges live elsewhere in the file. CI regeneration scripts replace everything between the markers — the real badge block must be inside them or it will be overwritten.

### MINOR: No LICENSE file found

**Error message**: `No LICENSE file found`
**Severity**: MINOR
**Root cause**: No `LICENSE`, `LICENSE.md`, or `LICENSE.txt` file at the plugin root.
**Fix**:
1. Add a LICENSE file. Common choices:
   - `LICENSE` with MIT license text
   - `LICENSE.md` with Apache 2.0 text
2. Use GitHub's license picker or:
   ```bash
   curl -sL https://choosealicense.com/licenses/mit/ | grep -A999 'BEGIN LICENSE' > LICENSE
   ```

### MINOR: Broken file reference

**Error message** (v2.26.0+): `Broken file reference: [<path>] in <md_file> — file not found. Two legitimate fixes: (1) if the reference is meant to be real, create the missing file or correct the path; (2) if it's a prose example/placeholder, convert the path to a template-exempt form...`
**Severity**: MINOR
**Source**: `cpv_validation_common.py` — markdown-link resolution inside `.md` files.
**Root cause**: A markdown link `[text]({path})` or backticked path reference `` `{path}/to/file.ext` `` appears outside a fenced code block, and the path does not resolve to an existing file (tried relative to the containing `.md` first, then relative to the plugin root). This is a MINOR because documentation with stale links is confusing for readers and invisible to the progressive-discovery algorithm.

**Two legitimate fixes — diagnose which case applies first**:

#### Fix A: the reference is meant to be real → fix the path or create the file

If the doc intends to point at a genuine file, one of these is wrong:
- The path itself is stale (file was renamed or moved) — update the link target to the current path. Use the plugin-relative form when the link can be resolved from multiple `.md` locations (`references/foo.md`), or the `.md`-relative form for siblings (`./sibling.md`).
- The referenced file was deleted but the link was not — either restore the file or delete the link.
- The referenced file is planned but not yet written — write it, or remove the link from the doc until the file exists.

Anchor fragments (`file.md#section`) are OK — the validator strips the `#section` part before resolving. The file just has to exist.

#### Fix B: the "reference" is a prose example or placeholder → mark it so the validator skips it

The broken-reference check only runs on link targets that look like real paths. Several escape hatches make a prose example clearly non-real:

- **Brace placeholders**: `[text]({path})`, `[text]({href})`, `[text]({img})`. Any `{...}` or `<...>` in the path is treated as a template and skipped.
- **Angle-bracket placeholders**: `[text](<placeholder>)`.
- **Known placeholder names**: path basenames `foo`, `bar`, `baz`, `run`, `test`, `example`, `sample`, `demo`, `my`, `your` are exempt. Also prefixes `my-*`, `your-*`, `example-*`, and literal `placeholder`.
- **Ellipsis**: a path containing `...` (e.g. `references/...`) is treated as an example.
- **Dollar-variables**: paths like `$PATH`, `${CLAUDE_PLUGIN_ROOT}/foo.md` are template-exempt because they contain `$VAR` sequences.
- **Fenced code blocks**: anything inside a triple-backtick fence is stripped before the check. The most robust fix for multi-line examples is to wrap them in a code fence.

**Example**:

```markdown
# Before — validator flags img and href as broken refs

Use image-link badges like `[![alt](img)](href)`.

# After — placeholders in braces, validator skips

Use image-link badges like `[![alt]({img})]({href})`.
```

Or for longer examples, put them in a fenced block (use 4-backtick
outer fences when the inner content itself contains a 3-backtick
fence):

````markdown
Example README pattern:

```markdown
[![CI](https://img.shields.io/...)](https://github.com/...)
```
````

#### Forbidden "fix"

- ❌ Creating a stub file just to silence the warning when the link target was never meant to resolve. Prefer Fix B — mark the path as an example using one of the template-exempt forms above.

---

## 11. Rules Validation Issues

Rules validation is delegated to `validate_rules.py`. All results are transferred to the main report. Common issues include:

- Rule files not valid UTF-8
- Rule files with UTF-8 BOM
- Invalid YAML frontmatter in rule files
- Rules exceeding recommended token budget
- Invalid `paths` field in rule frontmatter

**Fix**: Ensure rule files are plain markdown with UTF-8 encoding (no BOM). If using frontmatter, ensure the YAML is valid.

---

## 12. Path and Private Info Issues

### CRITICAL: Private info leaked (specific username)

**Error message**: `Private info leaked: <description> - found '<matched_text>' (replace with relative path or ${CLAUDE_PLUGIN_ROOT})`
**Severity**: CRITICAL
**File**: Various
**Root cause**: A file contains a hardcoded path with a known private username (from the system's current user or known private usernames list). This leaks personal information.
**Fix**:
1. Replace the absolute path with a relative path or environment variable:
   ```
   # WRONG:
   /Users/johndoe/projects/my-plugin/scripts/run.sh

   # RIGHT (relative):
   ./scripts/run.sh

   # RIGHT (env var):
   ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh
   ```
2. Search and replace all instances: `grep -rn "/Users/<username>" .`

### CRITICAL: Private path leaked (absolute path with username)

**Error message**: `Private path leaked: <description> - '<matched_text>' (use relative path or ${CLAUDE_PLUGIN_ROOT})`
**Severity**: CRITICAL
**File**: Various
**Root cause**: Same as above — detected by the stricter `validate_no_absolute_paths` scan.
**Fix**: Same as above — replace with relative paths or `${CLAUDE_PLUGIN_ROOT}`.

### MAJOR: Hardcoded user path found

**Error message**: `Hardcoded user path found: '<matched_text>...' (use relative paths or ${CLAUDE_PLUGIN_ROOT})`
**Severity**: MAJOR
**File**: Various
**Root cause**: A file contains a path like `/Users/<name>/...` or `/home/<name>/...` with a non-example username. Even if it is not the current user's name, it still breaks portability.
**Fix**:
1. Replace with relative paths or environment variables:
   ```
   # WRONG:
   /home/deploy/app/config.json

   # RIGHT:
   ./config.json
   # or
   ${CLAUDE_PLUGIN_ROOT}/config.json
   # or
   ${HOME}/.config/my-plugin/config.json
   ```

### MAJOR: Absolute path found (home directory in documentation)

**Error message**: `Absolute path found: '<path>...' - use relative path, ${CLAUDE_PLUGIN_ROOT}, or ${CLAUDE_PROJECT_DIR}`
**Severity**: MAJOR
**File**: Various (documentation files)
**Root cause**: A documentation file contains an absolute home directory path that is not a generic example.
**Fix**:
1. Replace with environment variable references or generic examples:
   ```markdown
   <!-- WRONG -->
   Edit `/Users/john/projects/plugin/config.json`

   <!-- RIGHT -->
   Edit `${CLAUDE_PLUGIN_ROOT}/config.json`
   <!-- or use a generic example -->
   Edit `/Users/<your-username>/projects/plugin/config.json`
   ```

### MINOR: Absolute path found (system path in code)

**Error message**: `Absolute path found: '<path>...' - use relative path, ${CLAUDE_PLUGIN_ROOT}, or ${CLAUDE_PROJECT_DIR}`
**Severity**: MINOR
**File**: Various (code/script files)
**Root cause**: A script or code file contains a system absolute path (e.g., `/usr/local/bin/...`). In scripts, some system paths may be intentional.
**Fix**:
1. If intentional (e.g., referencing a system tool), this may be acceptable — consider adding a comment explaining why.
2. If not intentional, replace with a relative path or `which <tool>` lookup.

---

## 13. .gitignore Issues

### MAJOR: No .gitignore file found

**Error message**: `No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin`
**Severity**: MAJOR
**Root cause**: The plugin has no `.gitignore` file, so cached files, build artifacts, environment secrets, and editor temp files could be committed to the repository.
**Fix**:
1. Create a `.gitignore` at the plugin root:
   ```gitignore
   # Python
   __pycache__/
   *.pyc
   .mypy_cache/
   .ruff_cache/
   .pytest_cache/
   dist/
   build/
   *.egg-info/

   # Node
   node_modules/

   # OS
   .DS_Store
   Thumbs.db

   # Editor
   *.swp
   *.swo
   *~
   .idea/
   .vscode/

   # Secrets
   .env
   *.env

   # Virtual environments
   .venv/
   venv/
   ```

### MINOR: Could not read .gitignore

**Error message**: `Could not read .gitignore: <error>`
**Severity**: MINOR
**Root cause**: The `.gitignore` file exists but could not be read (encoding issue or permission error).
**Fix**:
1. Ensure the file is saved as UTF-8.
2. Check file permissions: `chmod 644 .gitignore`.

### WARNING: .gitignore missing coverage for Python cache files

**Error message**: `.gitignore missing coverage for: Python cache files (__pycache__ or *.pyc)`
**Severity**: WARNING
**Root cause**: The `.gitignore` does not include patterns for Python cache files.
**Fix**:
1. Add to `.gitignore`:
   ```gitignore
   __pycache__/
   *.pyc
   ```

### WARNING: .gitignore missing coverage for Node modules

**Error message**: `.gitignore missing coverage for: Node modules (node_modules/)`
**Severity**: WARNING
**Root cause**: The `.gitignore` does not include the `node_modules/` pattern.
**Fix**:
1. Add to `.gitignore`:
   ```gitignore
   node_modules/
   ```

### WARNING: .gitignore missing coverage for linter/type checker caches

**Error message**: `.gitignore missing coverage for: Linter/type checker caches`
**Severity**: WARNING
**Root cause**: The `.gitignore` does not cover `.mypy_cache`, `.ruff_cache`, or `.pytest_cache`.
**Fix**:
1. Add to `.gitignore`:
   ```gitignore
   .mypy_cache/
   .ruff_cache/
   .pytest_cache/
   ```

### WARNING: .gitignore missing coverage for build artifacts

**Error message**: `.gitignore missing coverage for: Build artifacts (dist/, build/, *.egg-info)`
**Severity**: WARNING
**Root cause**: The `.gitignore` does not cover build output directories.
**Fix**:
1. Add to `.gitignore`:
   ```gitignore
   dist/
   build/
   *.egg-info/
   ```

### WARNING: .gitignore missing coverage for OS metadata files

**Error message**: `.gitignore missing coverage for: OS metadata files (.DS_Store, Thumbs.db)`
**Severity**: WARNING
**Root cause**: The `.gitignore` does not cover OS-generated metadata files.
**Fix**:
1. Add to `.gitignore`:
   ```gitignore
   .DS_Store
   Thumbs.db
   ```

### WARNING: .gitignore missing coverage for editor temp files

**Error message**: `.gitignore missing coverage for: Editor temp files`
**Severity**: WARNING
**Root cause**: The `.gitignore` does not cover editor swap/temp files (`.swp`, `.swo`, `*~`, `.idea/`, `.vscode/`).
**Fix**:
1. Add to `.gitignore`:
   ```gitignore
   *.swp
   *.swo
   *~
   .idea/
   .vscode/
   ```

### MAJOR: .gitignore missing coverage for environment files

**Error message**: `.gitignore missing coverage for: Environment files (.env)`
**Severity**: MAJOR
**Root cause**: The `.gitignore` does not cover `.env` files, which often contain secrets and API keys.
**Fix**:
1. Add to `.gitignore`:
   ```gitignore
   .env
   *.env
   ```
2. If any `.env` files were already committed, remove them from tracking:
   ```bash
   git rm --cached .env
   ```

### WARNING: .gitignore missing coverage for virtual environments

**Error message**: `.gitignore missing coverage for: Virtual environment directories`
**Severity**: WARNING
**Root cause**: The `.gitignore` does not cover `.venv/` or `venv/` directories.
**Fix**:
1. Add to `.gitignore`:
   ```gitignore
   .venv/
   venv/
   ```

### MAJOR: .gitignore ignores all source files

**Error message**: `.gitignore ignores all source files (*.py, *.js, or *.ts) — this will exclude plugin code from distribution`
**Severity**: MAJOR
**Root cause**: The `.gitignore` contains `*.py`, `*.js`, or `*.ts` as a blanket pattern, which would exclude all plugin source code from the repository.
**Fix**:
1. Remove the blanket exclusion patterns from `.gitignore`.
2. If you need to ignore specific generated files, use targeted patterns:
   ```gitignore
   # WRONG - excludes ALL Python files:
   *.py

   # RIGHT - excludes only specific generated files:
   generated_output.py
   dist/**/*.py
   ```

### WARNING: Found artifact files that may not be gitignored

**Error message**: `Found <count> <description> file(s) (e.g. <sample>) that may not be gitignored`
**Severity**: WARNING
**Root cause**: Artifact files (`.pyc`, `.DS_Store`, `Thumbs.db`) exist in the plugin tree and may not be covered by `.gitignore`.
**Fix**:
1. Add the appropriate pattern to `.gitignore` (see specific patterns above).
2. Remove any already-tracked artifacts:
   ```bash
   git rm --cached -r __pycache__/
   git rm --cached .DS_Store
   ```

### MAJOR: git-tracked file(s) also match .gitignore (gitignore not enforced — plugin INVALID)

**Error message**: `N git-tracked file(s) also match .gitignore — gitignore is not enforced. A tracked+gitignored file still ships (.gitignore does not untrack it), creating a shipped-but-ignored artifact and a scan-evasion vector, so the plugin is INVALID. Untrack them with the fix agent … Files: …`
**Severity**: MAJOR (blocking — gitignore-evasion hardening)

**Root cause**: One or more files are BOTH git-tracked AND matched by `.gitignore`. `.gitignore` does not untrack an already-tracked file, so these files still ship in the published artifact even though they are declared ignored. This is (a) an ambiguous shipped-but-ignored state and (b) a scan-evasion vector — an author could `git add` a payload then `.gitignore` it to hide it from the scanners. CPV now scans such files regardless AND fails the plugin so the anti-pattern is removed, not merely scanned around.

**Fix** (automatic — this is what the fix agent runs):

1. List the offending files — the authoritative set; re-derive it, do NOT rely on the truncated list in the finding message:
   ```bash
   git ls-files --cached --ignored --exclude-standard
   ```
2. For each file decide:
   - **Dev-only / operational / regeneratable, should NOT ship** (the common case — reports, caches, logs, build artifacts) → **untrack** it, keeping the working-tree copy so nothing is lost:
     ```bash
     git ls-files --cached --ignored --exclude-standard -z | xargs -0 git rm --cached --
     ```
     (or `git rm --cached <file>` per file). The file leaves the index/published artifact but stays on disk; `.gitignore` is now enforced.
   - **Legitimately must ship** (e.g. vendored third-party reference docs) → it must NOT be ignored: **remove its pattern from `.gitignore`** so it is tracked-and-not-ignored (valid, shipped, and scanned). Do NOT keep it gitignored to skip scanning — vendored content is scanned by design; report any false-positives instead.
3. Re-validate; the check passes when this prints nothing:
   ```bash
   git ls-files --cached --ignored --exclude-standard
   ```

**Do NOT** resolve this by deleting the files from disk or by relaxing the gate — the only valid resolutions are untrack (`git rm --cached`) or un-ignore (edit `.gitignore`).

---

## 14. Workflow Inline Python Issues

### MAJOR: Inline Python uses dict bracket access in f-string

**Error message**: `Inline Python uses dict bracket access in f-string: <snippet> -- shell quoting will strip inner quotes causing NameError. Extract value into a local variable first.`
**Severity**: MAJOR
**File**: `.github/workflows/<filename>.yml`
**Root cause**: A GitHub Actions workflow uses `python3 -c "..."` with double-quoted shell strings that contain f-string dictionary access like `{data["key"]}`. The shell strips the inner quotes before Python sees the code, causing `NameError` at runtime.
**Fix**:
1. Extract dictionary values into local variables before the f-string:
   ```yaml
   # WRONG:
   - run: python3 -c "import json; data=json.load(open('config.json')); print(f'Repo: {data[\"repo\"]}')"

   # RIGHT:
   - run: |
       python3 -c "
       import json
       data = json.load(open('config.json'))
       repo = data['repo']
       print(f'Repo: {repo}')
       "
   ```
2. Or use a multiline `run:` block with a heredoc or separate script file:
   ```yaml
   - run: python3 scripts/my_step.py
   ```

---

## Appendix: Severity Levels Reference

| Level | Exit Code | Meaning |
|-------|-----------|---------|
| CRITICAL | 1 | Plugin will not work — must fix before use |
| MAJOR | 2 | Significant problems — will cause issues in many scenarios |
| MINOR | 3 | May affect UX — recommended to fix |
| NIT | 4 (strict only) | Style/polish issue — nice to fix |
| WARNING | 0 | Non-blocking advisory — consider addressing |
| INFO | 0 | Informational — no action needed |
| PASSED | 0 | Check passed successfully |

## Appendix: Encoding Validation (from cpv_validation_common)

These errors can appear for any text file scanned across the plugin:

### MAJOR: File has UTF-8 BOM

**Error message**: `File has UTF-8 BOM (should be UTF-8 without BOM)`
**Severity**: MAJOR
**Root cause**: The file starts with a UTF-8 Byte Order Mark (`EF BB BF`). Many tools do not handle BOM correctly.
**Fix**:
1. Re-save the file as UTF-8 without BOM. In VS Code: click the encoding in the status bar, choose "Save with Encoding" > "UTF-8".
2. Or strip the BOM programmatically:
   ```bash
   sed -i '1s/^\xEF\xBB\xBF//' <file>
   ```

### MAJOR: File is not valid UTF-8

**Error message**: `File is not valid UTF-8: <decode error>`
**Severity**: MAJOR
**Root cause**: The file is encoded in a non-UTF-8 encoding (e.g., Latin-1, Windows-1252).
**Fix**:
1. Convert to UTF-8:
   ```bash
   iconv -f WINDOWS-1252 -t UTF-8 <file> > <file>.tmp && mv <file>.tmp <file>
   ```
2. Or re-save as UTF-8 from your editor.

---

## New Validations (v2.11.0+)

### userConfig schema invalid

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py` |
| **Severity** | MAJOR |
| **Messages** | `'userConfig.<key>' missing required sub-field 'title' (spec requires type, title, description)` (likewise for `'type'` / `'description'`), `'userConfig.<key>.type' = '...' is not a valid type (expected one of: ['boolean', 'directory', 'file', 'number', 'string'])`, `'userConfig.<key>.sensitive' must be a boolean, got <type>`, `'userConfig.<key>.default' type (...) does not match declared type (...)` |

**Root Cause:** Claude Code's runtime Zod schema enforces stricter rules than the public docs suggest:
- `title` is REQUIRED (string) — missing title fails install with `userConfig.<key>.title: Invalid input: expected string, received undefined`
- `type` is REQUIRED and must be exactly one of `"string" | "number" | "boolean" | "directory" | "file"` — missing/invalid type fails install with `userConfig.<key>.type: Invalid option: expected one of "string"|"number"|"boolean"|"directory"|"file"`
- `"integer"`, `"array"`, `"object"` (listed in some docs / JSON Schema) are NOT accepted by the runtime
- `description` (string) is **REQUIRED** per spec (plugins-reference.md:473, "Required: Yes"). CPV's `validate_userconfig` enforces all three of `type`, `title`, `description` via its `required_sub` set — omitting `description` produces the MAJOR `'userConfig.<key>' missing required sub-field 'description'`.
- `sensitive` (boolean) is optional; `true` routes the value to the system keychain
- `default` (optional) must match the declared `type` — bool is rejected when `type="number"` even though bool is a Python int subclass

**Fix — complete template for all 5 valid types:**
```json
{
  "userConfig": {
    "api_endpoint": {
      "title": "API endpoint URL",
      "description": "Your team's API endpoint",
      "type": "string",
      "sensitive": false
    },
    "api_token": {
      "title": "API authentication token",
      "description": "Bearer token for API auth",
      "type": "string",
      "sensitive": true
    },
    "poll_interval_seconds": {
      "title": "Poll interval (seconds)",
      "description": "How often to poll (e.g. 900 = 15 min)",
      "type": "number",
      "default": 900
    },
    "enable_cache": {
      "title": "Enable cache",
      "description": "Whether to cache API responses",
      "type": "boolean",
      "default": true
    },
    "workspace_dir": {
      "title": "Workspace directory",
      "description": "Absolute path to the workspace folder",
      "type": "directory"
    },
    "config_file": {
      "title": "Config file path",
      "description": "Absolute path to the config YAML",
      "type": "file"
    }
  }
}
```

**Common mistake — numeric config fields:** Authors often forget `type` on numeric intervals. The correct form is `"type": "number"` with a matching numeric `default`. Do NOT use `"type": "integer"` — the runtime rejects it.

**Heuristic — infer `type` from the key name (use this when the report only tells you the type is missing/invalid):**

| Field-name pattern (case-insensitive) | Recommended `type` | Example |
|---|---|---|
| `*_interval`, `*_seconds`, `*_timeout`, `*_threshold`, `*_count`, `*_days`, `*_port`, `max_*`, `min_*` | `number` | `poll_interval`, `max_retries`, `stale_pr_days` |
| `enable_*`, `disable_*`, `use_*`, `is_*`, `has_*`, `*_flag` | `boolean` | `enable_cache`, `use_colors` |
| `*_dir`, `workspace_dir`, `output_dir`, `data_dir` (expects ABSOLUTE path) | `directory` | `workspace_dir`, `cache_dir` |
| `*_file`, `config_file`, `credentials_file` (expects ABSOLUTE path) | `file` | `config_file`, `ca_bundle_file` |
| URLs, repo slugs, tokens, API keys, relative paths, fallback | `string` | `github_repo`, `api_endpoint`, `trdd_path`, `api_token` |

**Extract `default` from the description text:** descriptions like `"Default: 900 (15 min)"` contain the intended numeric default — extract it and add `"default": 900`. Descriptions like `"Default: design/tasks/"` contain a string default — add `"default": "design/tasks/"`. When the description provides no default, omit the field (it's optional).

**Example repair (the ai-maestro-janitor v0.1.2 → v0.1.3 bug, 2026-04-18):** an 11-entry `userConfig` shipped without any `type` fields and passed CPV ≤v2.22.3. Runtime rejected all 11 at `claude plugin install` time. The repair added `"type": "string"` to path/slug fields and `"type": "number"` with extracted numeric defaults to every `*_interval`/`*_days`/`*_threshold` field. CPV v2.22.4+ catches this automatically — if you see this pattern in a report, the fix is mechanical.

---

### channels server field missing or invalid

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py` |
| **Severity** | MAJOR |
| **Message** | `channels[N] missing required server field` or `channels[N].server does not match any mcpServers key` |

**Root Cause:** Each channel entry must have a `server` field that matches a key in `mcpServers`.

**Fix:**
```json
{
  "mcpServers": {
    "telegram": { "command": "node", "args": ["server.js"] }
  },
  "channels": [
    { "server": "telegram" }
  ]
}
```

---

### LSP server missing required fields

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py` |
| **Severity** | MAJOR |
| **Message** | `LSP server NAME missing required command field` or `missing extensionToLanguage` |

**Fix:**
```json
{
  "lspServers": {
    "go": {
      "command": "gopls",
      "args": ["serve"],
      "extensionToLanguage": { ".go": "go" }
    }
  }
}
```

---

### Output style invalid frontmatter

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py` |
| **Severity** | MINOR/MAJOR |
| **Message** | `Output style NAME has invalid YAML` or `keep-coding-instructions must be boolean` |

**Fix:**
```yaml
---
name: My Style
description: Brief description
keep-coding-instructions: false
---

# Instructions here
```

Valid frontmatter fields: `name`, `description`, `keep-coding-instructions` (boolean).

---

### settings.json agent value does not match agent file

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py` |
| **Severity** | MINOR |
| **Message** | `settings.json agent value NAME does not match any agent file in agents/` |

**Fix:** Ensure the `agent` value matches an actual `.md` file in your `agents/` directory:
```json
// settings.json
{ "agent": "my-agent" }
// Must have: agents/my-agent.md
```

---

## 15. Layout C consistency (marketplace-in-plugin) — Phase 16, v2.32.0+

These rules fire only when both `.claude-plugin/plugin.json` AND `.claude-plugin/marketplace.json` exist at the SAME repo root (Layout C). They cross-validate the two manifests against each other.

### MAJOR: Layout C marketplace.json has no self-reference for plugin.json.name

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py::validate_layout_c_consistency` |
| **Severity** | MAJOR |
| **Message** | `Layout C: plugin.json declares name='X' but marketplace.json's plugins[] does not list a self-reference with that name. Add `{name: 'X', source: './'}` to marketplace.json's plugins array, or remove marketplace.json if this is meant to be a plain plugin.` |
| **Category** | architecture |

**Fix:** Add a self-reference entry to `marketplace.json`'s `plugins[]` whose `name` matches `plugin.json.name` (or remove `marketplace.json` if this is meant to be a plain plugin, not Layout C).

```json
// .claude-plugin/plugin.json
{ "name": "my-plugin", ... }

// .claude-plugin/marketplace.json
{
  "plugins": [
    { "name": "my-plugin", "source": "./", "version": "1.2.3" }
  ]
}
```

### MAJOR: Layout C self-entry source is not "./"

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py::validate_layout_c_consistency` |
| **Severity** | MAJOR |
| **Message** | `Layout C: marketplace.json's self-reference for plugin 'X' has source=...; must be './' (relative) so install resolves to the same repo. Other source types would re-clone the repository.` |

**Fix:** The self-entry's source MUST be `"./"` (or the bare `"."`). Any other source type re-clones the repo at install time.

```json
// WRONG
{ "name": "my-plugin", "source": "./plugins/my-plugin" }

// RIGHT
{ "name": "my-plugin", "source": "./" }
```

### MINOR: Layout C version drift between the two manifests

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py::validate_layout_c_consistency` |
| **Severity** | MINOR |
| **Message** | `Layout C: plugin.json version 'X' differs from marketplace.json plugins[<name>].version 'Y'. Bump both together to keep installation metadata consistent.` |

**Fix:** The check compares two slots — `plugin.json.version` and the self-entry's `plugins[<self>].version` — and only fires when BOTH are set and differ. Bump both to the same value (the standard `publish.py` from `generate_plugin_repo.py --self-marketplace` does this atomically).

Two slots that MUST agree:
- `.claude-plugin/plugin.json` → `version`
- `.claude-plugin/marketplace.json` → `plugins[N].version` (the self-entry, where `name == plugin.json.name`)

> Note: `marketplace.json` → `metadata.version` is NOT part of this specific cross-check (the metadata-version slot is validated elsewhere). Keep it in sync too as good practice, but `validate_layout_c_consistency` only diffs the two slots above.

For the canonical fix recipe and migration paths, see [layout-c-migration.md](../../cpv-migrate-marketplace-architecture/references/layout-c-migration.md).

---

## 16. Bundled slash-command collision (Phase 15, v2.31.0+)

### WARNING: Plugin command name collides with a built-in slash command

| Field | Value |
|-------|-------|
| **Script** | `validate_command.py` |
| **Severity** | WARNING |
| **Message** | `Command name '<name>' collides with a built-in Claude Code slash command. Users typing /<name> get the built-in; the plugin's command is only reachable via the namespaced form /<plugin>:<name>. Consider renaming.` |
| **Common collisions** (must each be present in `BUILTIN_SLASH_COMMANDS`) | `clear`, `compact`, `config`, `context`, `cost`, `doctor`, `exit`, `help`, `init`, `loop`, `mcp`, `model`, `permissions`, `plugin`, `release-notes`, `resume`, `review`, `security-review`, `skills`, `status`, `theme`, `ultrareview`, `usage` |

**Why it matters:** When the user types `/clear` (or whatever name), Claude Code dispatches to the built-in handler — your plugin's command is shadowed and never runs. Even worse, the user can't reach it any other way unless they uninstall the colliding plugin.

**Fix:** Rename the command to something namespaced or descriptive:

```bash
# WRONG: commands/clear.md (shadowed by /clear built-in)
# RIGHT: commands/cache-clear.md  → /cache-clear
# RIGHT: commands/myplugin-clear.md  → /myplugin-clear
```

If the plugin is named `myplugin`, prefix collision-prone names with `myplugin-`. If renaming breaks downstream users, consider if the command really needs to exist as a slash command at all — sometimes the work belongs in an agent or skill instead.

The full list of built-in names CPV checks against is `cpv_validation_common::BUILTIN_SLASH_COMMANDS`.

---

## 17. Cross-marketplace dependency blocked (TRDD-20108ab7, 2026-05-10)

### MAJOR: Cross-marketplace dependency not in hosting marketplace's allowlist

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py::validate_dependencies` |
| **Severity** | MAJOR |
| **Message** | `'dependencies[i].marketplace' = '<target>' is not in the hosting marketplace's allowCrossMarketplaceDependenciesOn allowlist (<list>) — cross-marketplace dependency is blocked at install time with a 'cross-marketplace' error (plugin-dependencies.md:54-79)` |
| **Spec** | [plugin-dependencies.md:54-79](https://code.claude.com/docs/en/plugin-dependencies.md) |

**Why it matters:** Cross-marketplace dependencies cross trust boundaries. When `plugin-foo@market-A` declares `dependencies: [{name: "shared-lib", marketplace: "market-B"}]`, Claude Code refuses the install at runtime with a `cross-marketplace` error UNLESS `market-A`'s `marketplace.json::allowCrossMarketplaceDependenciesOn` lists `"market-B"`. The allowlist is the marketplace owner's explicit consent that pulling from `market-B` is OK.

**Three legitimate fixes (pick one):**

1. **Add to the allowlist** (preferred when the dep IS legitimate):

   ```json
   // <hosting-marketplace>/.claude-plugin/marketplace.json
   {
     "name": "host-mkt",
     "owner": {"name": "..."},
     "plugins": [...],
     "allowCrossMarketplaceDependenciesOn": ["other-mkt"]
   }
   ```

   The marketplace owner must explicitly opt-in to each foreign marketplace. Don't add `"*"` — there is no wildcard in the spec.

2. **Remove the `marketplace` sub-field** if the dep actually lives in the SAME marketplace:

   ```json
   // BEFORE — pointless cross-mkt declaration
   "dependencies": [{"name": "sibling-plugin", "marketplace": "host-mkt"}]

   // AFTER — same-mkt dep, no marketplace field needed
   "dependencies": ["sibling-plugin"]
   // OR with version pin:
   "dependencies": [{"name": "sibling-plugin", "version": "~1.2.0"}]
   ```

3. **Pass `--marketplace-context PATH`** when validating in CI / outside the production marketplace tree:

   ```bash
   uv run python scripts/validate_plugin.py \
     --marketplace-context /path/to/host-marketplace/ \
     /path/to/plugin/
   ```

   This is the right answer when the plugin lives in a worktree, an extracted tarball, or a freshly cloned PR branch where the on-disk auto-discovery (Layout C / Layout B / cache layout) cannot find the production marketplace.

**Auto-discovery layouts CPV walks** (no `--marketplace-context` needed):

- **Layout C** — plugin's own `.claude-plugin/marketplace.json` (marketplace-in-plugin).
- **Layout B** — parent `.claude-plugin/marketplace.json` (nested monorepo, walked up to 3 levels).
- **Cache layout** — `~/.claude/plugins/cache/<mkt>/<plugin>/` (immediate parent's `marketplace.json`).

When auto-discovery finds nothing, CPV emits INFO and skips the allowlist check (rather than MAJOR-blocking a standalone clone). This is by design.

### NIT: Marketplace.json uses legacy 'allowedDependencyMarketplaces'

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py::validate_dependencies` |
| **Severity** | NIT |
| **Message** | `marketplace.json uses legacy 'allowedDependencyMarketplaces' — rename to the spec field 'allowCrossMarketplaceDependenciesOn' (plugin-dependencies.md:54-79)` |

**Fix:** A pre-spec CPV release used `allowedDependencyMarketplaces`. The canonical spec name is `allowCrossMarketplaceDependenciesOn`. Both are honoured for backward compatibility, but the legacy alias is removed in a future release.

```json
// BEFORE
{ "allowedDependencyMarketplaces": ["other-mkt"] }

// AFTER
{ "allowCrossMarketplaceDependenciesOn": ["other-mkt"] }
```

---

## 18. CC v2.1.207 plugin options (v2.158.0)

Claude Code v2.1.207 changed two things about plugin options. `validate_plugin.py` covers the
two surfaces it owns — **monitors** and **inline `plugin.json` hooks** (the shell-injection
rule) — plus the project-settings advisory. The same shell-injection rule on `hooks/hooks.json`
is in [hook-fixes.md](hook-fixes.md) §14, and on MCP `headersHelper` in [mcp-fixes.md](mcp-fixes.md) §14.

### CRITICAL: `[RC-USERCFG-SHELL-INJECT]` — monitor or inline hook interpolates a plugin option

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py` (monitors + inline `plugin.json` `hooks`) |
| **Severity** | CRITICAL (blocking) |
| **Message** | `[RC-USERCFG-SHELL-INJECT] <where> interpolates ${user_config.<key>} into a SHELL-FORM command. Claude Code v2.1.207 REJECTS this (shell-injection fix) …` |

**Fix (monitor):** a monitor's command is always shell-form — there is no `args` array to move
the value into. Read the option **inside the script**, from `$CLAUDE_PLUGIN_OPTION_<KEY>` or a
config file:

```jsonc
// BEFORE — rejected; the monitor never runs
{ "monitors": [{ "command": "tail -f ${user_config.log_path} | grep ERROR" }] }

// AFTER — the script reads the option itself
{ "monitors": [{ "command": "${CLAUDE_PLUGIN_ROOT}/scripts/watch-errors.sh" }] }
```

```bash
#!/usr/bin/env bash
set -euo pipefail
: "${CLAUDE_PLUGIN_OPTION_LOG_PATH:?plugin option log_path is not set}"
tail -f "$CLAUDE_PLUGIN_OPTION_LOG_PATH" | grep --line-buffered ERROR
```

**Fix (inline `plugin.json` hook):** identical to a `hooks/hooks.json` hook — switch to exec
form (`args`) or read `$CLAUDE_PLUGIN_OPTION_<KEY>` in the script. See
[hook-fixes.md](hook-fixes.md) §14 for both recipes.

Do **not** attempt to escape or quote the value. The rejection is on the SHAPE, not on the
value, so a quoted interpolation is still rejected — and would still be wrong the first time a
value contains a quote.

### WARNING: `[RC-USERCFG-PROJECT-SETTINGS]` — `pluginConfigs` in project-level settings

| Field | Value |
|-------|-------|
| **Script** | `validate_plugin.py` |
| **Severity** | WARNING (advisory — does not block the publish) |
| **Message** | `[RC-USERCFG-PROJECT-SETTINGS] .claude/settings.json sets 'pluginConfigs', but since Claude Code v2.1.207 plugin option values are NO LONGER read from project-level settings …` |

**Fix:** since v2.1.207 only **user** settings (`~/.claude/settings.json`), `--settings`, and
**managed** settings supply plugin option values. A `pluginConfigs` block left in
`.claude/settings.json` or `.claude/settings.local.json` is silently ignored at runtime — the
plugin falls back to its defaults with no error, which is exactly the failure that is hard to
diagnose from the outside. Move the block to the user settings file, pass it via `--settings`,
or ship it as managed settings.

This is a WARNING rather than an error because a checked-in project settings file may
legitimately carry the values for a *different* Claude Code version, or for humans to copy —
CPV tells you they are inert, it does not decide for you.
