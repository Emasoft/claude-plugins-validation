# Troubleshooting Python Scripts for Plugin Validation

## Table of Contents

1. [Bash Arithmetic Exit Codes](#1-bash-arithmetic-exit-codes-verified)
2. [Unused Variable Warnings - Pyright/ruff](#2-unused-variable-warnings---pyrightruff-verified)
3. [Missing Python Dependencies - ModuleNotFoundError](#3-missing-python-dependencies---modulenotfounderror-verified)
4. [Git Hook Not Running](#4-git-hook-not-running-verified)
5. [Plugin JSON Missing Required Fields](#5-plugin-json-missing-required-fields-verified)
6. [Ruff Linting - Unused Variable Error](#6-ruff-linting---unused-variable-error-verified)
7. [Marketplace Plugin Source Format](#7-marketplace-plugin-source-format-verified)
8. [Version Consistency Between Plugins and Marketplace](#8-version-consistency-between-plugins-and-marketplace-verified)
9. [Git Tag Already Exists Error](#9-git-tag-already-exists-error-verified)
10. [subprocess.run Output Truncation](#10-subprocessrun-output-truncation-verified)
11. [Best Practices Summary](#best-practices-summary-all-verified)
12. [Quick Diagnostic Commands](#quick-diagnostic-commands)

---

## Verified Issues and Working Solutions

This document contains issues encountered during development and their **verified working solutions**.

---

## 1. Bash Arithmetic Exit Codes (VERIFIED)

**Issue**: Bash scripts using `((var++))` return exit code 1 when the variable is 0, causing `set -e` to exit the script.

**Root Cause**: In bash, `((expression))` returns exit code based on the expression result. When `var=0`, `((var++))` evaluates to 0 (the original value), which bash interprets as false/failure (exit code 1).

**Example of broken code**:
```bash
#!/bin/bash
set -e
COUNTER=0
((COUNTER++))  # Returns exit code 1 - script exits!
echo "Never reached"
```

**Broken output**:
```
Exit code 1
```

**WORKING SOLUTION** (Verified):
```bash
((COUNTER++)) || true
```

**Or better - convert to Python**:
```python
counter = 0
counter += 1  # Python doesn't have this issue
```

**Verification**: This fix was applied to `verify-release.sh` and the script then completed successfully.

---

## 2. Unused Variable Warnings - Pyright/ruff (VERIFIED)

**Issue**: Pyright reports "variable is not accessed" for unused tuple unpacking from subprocess results.

**Example warning**:
```
★ [Line 132:15] "stdout" is not accessed (Pyright)
```

**Broken code**:
```python
code, stdout, stderr = run_command(cmd)
# Only using 'code', stdout and stderr trigger warnings
```

**WORKING SOLUTION** (Verified):
```python
# Use underscore for unused variables
code, stdout, _ = run_command(cmd)  # When stderr is unused
code, _, _ = run_command(cmd)       # When both stdout/stderr unused
```

**Verification**: Applied to `verify-release.py`, `pre-commit-hook.py` - all Pyright warnings resolved.

---

## 3. Missing Python Dependencies - ModuleNotFoundError (VERIFIED)

**Issue**: Validation scripts fail with `ModuleNotFoundError: No module named 'yaml'`

**Error output**:
```
Traceback (most recent call last):
  File "validate_plugin.py", line 38, in <module>
    import yaml
ModuleNotFoundError: No module named 'yaml'
```

**Root Cause**: The validation scripts require dependencies (pyyaml, ruff, mypy) that aren't installed in the environment.

**WORKING SOLUTION** (Verified):
```bash
# Install dependencies using uv in the claude-plugins-validation directory
cd claude-plugins-validation
uv sync

# Run scripts through uv (automatically uses correct environment)
uv run python scripts/validate_plugin.py ./my-plugin
```

**Verification**: After running `uv sync`, the `validate_plugin.py` script runs successfully.

---

## 4. Git Hook Not Running (VERIFIED)

**Issue**: Pre-commit hook doesn't execute when committing.

**Possible causes**:
1. Hook file not executable
2. Shebang line missing or incorrect
3. Script has syntax errors

**WORKING SOLUTION** (Verified):
```bash
# 1. Make executable
chmod +x .git/hooks/pre-commit

# 2. Verify shebang is correct (first line of file)
head -1 .git/hooks/pre-commit
# Must be: #!/usr/bin/env python3

# 3. Test manually before commit
python3 .git/hooks/pre-commit
```

**Verification**: After copying `pre-commit-hook.py` to `.git/hooks/pre-commit` and making it executable, manual test shows:
```
Running pre-commit validations...
Checking version consistency... ✔
Checking for sensitive data... ✔
Pre-commit validations passed
```

---

## 5. Plugin JSON Missing Required Fields (VERIFIED)

**Issue**: Plugin validation fails with "missing .claude-plugin/plugin.json"

**Root Cause**: Plugin directory exists but lacks the required manifest file.

**Error from validator agent**:
```
[CRITICAL] Missing required .claude-plugin/plugin.json
```

**WORKING SOLUTION** (Verified):
```json
// Create .claude-plugin/plugin.json with minimum required fields:
{
  "name": "my-plugin-name",
  "version": "1.0.0",
  "description": "What this plugin does"
}
```

**Full recommended structure**:
```json
{
  "name": "perfect-skill-suggester",
  "version": "1.1.0",
  "description": "High-accuracy skill activation with AI-analyzed keywords",
  "author": {
    "name": "AuthorName",
    "email": "email@example.com"
  },
  "license": "MIT",
  "keywords": ["skills", "activation", "hooks"]
}
```

**Verification**: After creating plugin.json for perfect-skill-suggester, validation passed.

---

## 6. Ruff Linting - Unused Variable Error (VERIFIED)

**Issue**: Ruff check fails with F841 error for unused variables.

**Error output**:
```
scripts/validate_marketplace.py:123:5: F841 Local variable 'total_issues' is assigned to but never used
```

**WORKING SOLUTION** (Verified):
```python
# Option 1: Remove the unused assignment
# Before:
total_issues = critical + major + minor  # Not used

# After:
# (line removed)

# Option 2: Prefix with underscore if intentional
_total_issues = critical + major + minor  # Tells ruff it's intentional
```

**Verification**: After removing the unused variable, ruff check passed.

---

## 7. Marketplace Plugin Source Format (VERIFIED)

**Issue**: Marketplace validation fails with "Invalid source format" error.

**Error**:
```
[CRITICAL] Invalid plugin source format for plugin X
```

**Root Cause**: For local plugins, the `source` field must be a string path, not an object.

**Broken format**:
```json
{
  "plugins": [{
    "name": "my-plugin",
    "source": {
      "type": "local",
      "path": "./my-plugin"
    }
  }]
}
```

**WORKING SOLUTION** (Verified):
```json
{
  "plugins": [{
    "name": "my-plugin",
    "source": "./my-plugin"
  }]
}
```

**Verification**: After changing source to string format, marketplace validation passed.

---

## 8. Version Consistency Between Plugins and Marketplace (VERIFIED)

**Issue**: Plugin versions in individual plugin.json files don't match versions in marketplace.json.

**WORKING SOLUTION** (Verified):
```bash
# Use the sync-versions.py script to synchronize
python3 scripts/sync-versions.py /path/to/marketplace

# Check if versions are in sync (returns exit 0 if synced)
python3 scripts/sync-versions.py --check /path/to/marketplace
```

**The script**:
1. Reads each plugin's `.claude-plugin/plugin.json`
2. Updates the corresponding entry in `marketplace.json`
3. Reports what was changed

**Verification**: After running sync-versions.py, marketplace.json correctly shows version 1.1.0 for both plugins.

---

## 9. Git Tag Already Exists Error (VERIFIED)

**Issue**: Creating a release fails because the tag already exists.

**Error**:
```
fatal: tag 'v1.1.0' already exists
```

**Root Cause**: Tag was created on a commit that was later amended, leaving orphan tag.

**WORKING SOLUTION** (Verified):
```bash
# Delete the old tag
git tag -d v1.1.0

# Recreate on current commit
git tag -a v1.1.0 -m "Release v1.1.0"
```

**Verification**: After deleting and recreating the tag, release workflow completed successfully.

---

## 10. subprocess.run Output Truncation (VERIFIED)

**Issue**: Long subprocess output gets cut off or causes memory issues.

**WORKING SOLUTION** (Verified):
```python
import subprocess

def run_command(cmd, cwd=None, timeout=60):
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout  # Always set timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "Command timed out"
    except Exception as e:
        return 1, "", str(e)
```

**Key practices**:
- Always set `timeout` parameter
- Use `capture_output=True` instead of `stdout=PIPE, stderr=PIPE`
- Handle `TimeoutExpired` exception
- Use `text=True` for string output

**Verification**: All validation scripts use this pattern and handle long-running validators correctly.

---

## Best Practices Summary (All Verified)

| Practice | Reason | Verified In |
|----------|--------|-------------|
| Use Python over Bash | Avoids shell quirks like arithmetic exit codes | All scripts converted |
| Use pathlib.Path | Proper cross-platform path handling | verify-release.py, release-plugin.py |
| Set subprocess timeouts | Prevent hangs | All subprocess calls |
| Use _ for unused vars | Satisfies Pyright/ruff linters | verify-release.py |
| Run uv sync first | Installs dependencies | claude-plugins-validation setup |
| chmod +x hooks | Make hooks executable | setup-hooks.py |
| String source in marketplace | Required format for local plugins | marketplace.json |
| sync-versions.py before release | Keep versions consistent | pre-commit hook |
| Delete orphan tags | Clean up before re-tagging | release-plugin.py |

---

## Quick Diagnostic Commands

```bash
# Check if Python validation environment is ready
cd claude-plugins-validation && uv run python -c "import yaml; print('OK')"

# Verify hook is executable and valid
chmod +x .git/hooks/pre-commit && python3 .git/hooks/pre-commit

# Check JSON validity
jq . .claude-plugin/plugin.json > /dev/null && echo "Valid JSON"

# Check version sync status
python3 scripts/sync-versions.py --check .

# Run full verification
python3 scripts/verify-release.py
```
