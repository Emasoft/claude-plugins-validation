# Cross-Reference Validation — Validation Issues and Fixes

Comprehensive remediation guide for all issues detected by `validate_xref.py`.

## Table of Contents

- [1. Plugin Directory Issues](#1-plugin-directory-issues)
- [2. Task() Agent Reference Issues](#2-task-agent-reference-issues)
- [3. Subagent_type Matching Issues](#3-subagent_type-matching-issues)
- [4. Version Synchronization Issues](#4-version-synchronization-issues)
- [5. Command Agent Reference Issues](#5-command-agent-reference-issues)
- [6. Skill Reference Issues](#6-skill-reference-issues)
- [7. Hook Script Reference Issues](#7-hook-script-reference-issues)
- [8. File Read Issues](#8-file-read-issues)

---

## Checklist

- [ ] Read the finding — what broken reference or missing file is reported
- [ ] Match to a numbered section below
- [ ] Resolve: fix the path, restore the missing file, or remove the stale reference
- [ ] Re-validate with `validate_xref.py`
- [ ] Re-run full `validate_plugin.py --strict` to catch cascading issues

## 1. Plugin Directory Issues

### [CRITICAL] Plugin directory does not exist: {plugin_root}
**Source**: `validate_xref.py` — `validate_cross_references()`
**What it means**: The path supplied to the validator does not exist on disk.
**How to fix**:
1. Verify the path you passed to the validator is correct.
2. Ensure the plugin directory has been created: `mkdir -p path/to/plugin`.
3. If running from CI, confirm the working directory is set correctly.

### [CRITICAL] Plugin path is not a directory: {plugin_root}
**Source**: `validate_xref.py` — `validate_cross_references()`
**What it means**: The supplied path exists but is a file, not a directory.
**How to fix**:
1. Check the path — it should point to the plugin root folder, not a file inside it.
2. Remove the filename from the path so it refers to the containing directory.

---

## 2. Task() Agent Reference Issues

### [MAJOR] Task() references non-existent agent '{ref_agent}'
**Source**: `validate_xref.py` — `validate_agent_task_refs()`
**What it means**: An agent `.md` file contains a `subagent_type` field referencing an agent name that has no matching `agents/{ref_agent}.md` file.
**How to fix**:
1. Check the spelling of `ref_agent` in the `subagent_type` field — it is case-sensitive.
2. If the agent should exist, create `agents/{ref_agent}.md` with the appropriate agent definition.
3. If the reference is stale, remove or update the `subagent_type` field in the offending agent file.
4. Run `ls agents/` to see all available agent names.

### [MINOR] Could not read agent file: {error}
**Source**: `validate_xref.py` — `validate_agent_task_refs()`
**What it means**: An agent `.md` file in `agents/` could not be read (permissions, encoding, or I/O error).
**How to fix**:
1. Check file permissions: `ls -la agents/{filename}`.
2. Ensure the file is not locked or corrupted.
3. Fix encoding issues: the file must be valid UTF-8.

---

## 3. Subagent_type Matching Issues

### [MAJOR] subagent_type '{ref_agent}' has no matching agents/{ref_agent}.md
**Source**: `validate_xref.py` — `validate_subagent_type_matching()`
**What it means**: A `subagent_type` value found in any `.md` file across the plugin does not match any agent filename in `agents/`.
**How to fix**:
1. Locate all `subagent_type:` fields that reference `{ref_agent}` (check commands, agents, and other `.md` files).
2. Either create `agents/{ref_agent}.md` or correct the `subagent_type` value to match an existing agent.
3. Agent names are the filename stem (without `.md`) and are matched case-sensitively.

---

## 4. Version Synchronization Issues

### [MAJOR] Version mismatch detected: {version_list}
**Source**: `validate_xref.py` — `validate_version_sync()`
**What it means**: The version number differs between two or more of these files: `.claude-plugin/plugin.json`, `README.md`, `marketplace.json`, or `pyproject.toml`.
**How to fix**:
1. Decide which version is the authoritative one (usually `.claude-plugin/plugin.json`).
2. Update all other files to match:
   - In `README.md`: update the `Version: X.Y.Z` badge or mention.
   - In `marketplace.json`: update `plugins[].version` for your plugin entry.
   - In `pyproject.toml`: update `version = "X.Y.Z"`.
3. Use a release script or `bump2version` / `bumpversion` to keep them in sync automatically.

### [INFO] Only {n} version source(s) found - sync check skipped
**Source**: `validate_xref.py` — `validate_version_sync()`
**What it means**: Fewer than two files contain a version number, so a mismatch comparison cannot be performed. This is informational only.
**How to fix**: No action required. Optionally add version numbers to `README.md` or `marketplace.json` to enable sync checking.

---

## 5. Command Agent Reference Issues

### [CRITICAL] Command references non-existent agent '{ref_agent}' - BREAKING
**Source**: `validate_xref.py` — `validate_command_agent_refs()`
**What it means**: A command `.md` file has a `subagent_type` value that references an agent which does not exist in `agents/`. This will cause the command to fail at runtime.
**How to fix**:
1. Open the offending command file in `commands/`.
2. Correct the `subagent_type` value to match an agent that exists in `agents/`.
3. If the agent is missing, create `agents/{ref_agent}.md` with the appropriate definition.

### [MAJOR] Command mentions unknown agent '{ref_agent}'
**Source**: `validate_xref.py` — `validate_command_agent_refs()`
**What it means**: A command file contains a spawn/invoke pattern referencing an agent name that is neither in `agents/` nor a recognized built-in type (`basic`, `task`, `explore`, `scout`, `oracle`, `haiku`, `sonnet`, `opus`).
**How to fix**:
1. Check the agent name for typos.
2. If it is a custom agent, create the corresponding `agents/{ref_agent}.md`.
3. If it is a built-in type, verify you are using one of the recognized names listed above.

### [MINOR] Could not read command file: {error}
**Source**: `validate_xref.py` — `validate_command_agent_refs()`
**What it means**: A command `.md` file in `commands/` could not be read.
**How to fix**:
1. Check file permissions: `ls -la commands/{filename}`.
2. Ensure the file is not corrupted and is valid UTF-8.

### [INFO] No commands/ directory found - skipping command agent ref check
**Source**: `validate_xref.py` — `validate_command_agent_refs()`
**What it means**: The plugin has no `commands/` directory, so this check is skipped. Informational only.
**How to fix**: No action required unless you intend to add commands to this plugin.

---

## 6. Skill Reference Issues

### [MAJOR] Reference to non-existent skill '{skill_name}'
**Source**: `validate_xref.py` — `validate_skill_refs()`
**What it means**: A file in the plugin references a skill by name (e.g., in a `skills/` path pattern) but no matching subdirectory exists in `skills/`.
**How to fix**:
1. Run `ls skills/` to see available skill names.
2. Correct the skill reference to match an existing skill directory name (comparison is case-insensitive).
3. If the skill should exist, create `skills/{skill_name}/SKILL.md` with the appropriate definition.

---

## 7. Hook Script Reference Issues

### [CRITICAL] Hook references non-existent script: {script_path}
**Source**: `validate_xref.py` — `validate_hook_script_refs()`
**What it means**: A hook configuration in `hooks/hooks.json` (or the file referenced by `plugin.json`) has a `command` field pointing to a script file that does not exist on disk.
**How to fix**:
1. Locate the hook entry in `hooks/hooks.json`.
2. Create the missing script at the referenced path, or fix the path to point to the correct existing script.
3. Script paths use `${CLAUDE_PLUGIN_ROOT}` as the base; ensure the relative path from plugin root is correct.

### [MINOR] Hook script is not executable: {script_path}
**Source**: `validate_xref.py` — `validate_hook_script_refs()`
**What it means**: A shell script (`.sh` or `.bash`) referenced by a hook exists on disk but lacks execute permission.
**How to fix**:
1. Run: `chmod +x path/to/script.sh`
2. Commit the permission change: `git add path/to/script.sh && git commit -m "fix: make hook script executable"`

### [MINOR] Could not parse hooks file: {error}
**Source**: `validate_xref.py` — `validate_hook_script_refs()`
**What it means**: A hooks JSON file could not be parsed (malformed JSON).
**How to fix**:
1. Open the hooks file and validate its JSON syntax.
2. Use `python -m json.tool hooks/hooks.json` to check for errors.
3. Fix the syntax error and re-run validation.

### [INFO] No hooks configuration found - skipping hook script check
**Source**: `validate_xref.py` — `validate_hook_script_refs()`
**What it means**: No `hooks/hooks.json` file was found and `plugin.json` does not reference a hooks file. Informational only.
**How to fix**: No action required unless your plugin uses hooks.

### [INFO] No agents/ directory found - skipping Task() reference check
**Source**: `validate_xref.py` — `validate_agent_task_refs()`
**What it means**: The plugin has no `agents/` directory, so Task() reference validation is skipped.
**How to fix**: No action required unless your plugin uses agents.

---

## 8. File Read Issues

### [MINOR] Cannot read file: {rel_path} ({error})
**Source**: `validate_xref.py` — `scan_all_files()` (via xref context)
**What it means**: A file was found during scanning but could not be read due to permissions or I/O errors.
**How to fix**:
1. Check file permissions: `ls -la {rel_path}`.
2. Ensure the file is not locked or corrupted.
3. Verify the file is readable by the current user.
