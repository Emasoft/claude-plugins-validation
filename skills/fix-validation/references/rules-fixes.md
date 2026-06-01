# Rules Validation — Validation Issues and Fixes

## Table of Contents

- [1. Rules Directory Issues](#1-rules-directory-issues)
- [2. Rule File Read and Encoding Issues](#2-rule-file-read-and-encoding-issues)
- [3. Rule File Content Issues](#3-rule-file-content-issues)
- [4. Frontmatter Issues](#4-frontmatter-issues)
- [5. Security Issues in Rule Files](#5-security-issues-in-rule-files)
- [6. Token Budget Issues](#6-token-budget-issues)

Comprehensive remediation guide for all issues detected by `validate_rules.py`.

## Checklist

- [ ] Identify the rules file with the finding
- [ ] Match to a numbered section below
- [ ] Open the rule file and inspect frontmatter + body
- [ ] Apply the fix (frontmatter, token budget, paths field)
- [ ] Re-validate

## Overview

Rule files are plain Markdown files placed in a plugin's `rules/` directory. They are loaded alongside `CLAUDE.md` into the model context for every session. Rules support optional YAML frontmatter with a `paths` field for path-specific activation.

**Token budget**: All rule files combined must stay under **10,000 estimated tokens**.

---

## 1. Rules Directory Issues

### [INFO] No rules/ directory found
**Source**: `validate_rules.py` — `validate_rules_directory()`
**What it means**: The plugin does not have a `rules/` directory. This is informational — rules are optional.
**How to fix**: No action required. If you want to add rules, create a `rules/` directory and add `.md` files to it.

### [INFO] No rule files (*.md) found in rules/
**Source**: `validate_rules.py` — `validate_rules_directory()`
**What it means**: A `rules/` directory exists but contains no `.md` files.
**How to fix**: Either add `.md` rule files to the `rules/` directory or remove the empty directory if rules are not needed.

### [INFO] Found {n} rule file(s)
**Source**: `validate_rules.py` — `validate_rules_directory()`
**What it means**: Informational count of rule files found. No action needed.

---

## 2. Rule File Read and Encoding Issues

### [MAJOR] Cannot read rule file: {error}
**Source**: `validate_rules.py` — `validate_rule_file()`
**What it means**: A rule file in `rules/` could not be read due to a filesystem error (permissions, I/O error, missing file).
**How to fix**:
1. Check file permissions: `ls -la rules/{filename}`.
2. Ensure the file is readable: `chmod 644 rules/{filename}`.
3. Verify the file is not corrupted or locked.

### [MAJOR] File has UTF-8 BOM (should be UTF-8 without BOM)
**Source**: `validate_rules.py` — `validate_rule_file()` via `check_utf8_encoding()` (from `cpv_validation_common.py`)
**What it means**: The rule file starts with a UTF-8 Byte Order Mark (BOM, `\xef\xbb\xbf`). Claude Code expects UTF-8 without BOM. The BOM may cause parsing issues.
**How to fix**:
1. Re-save the file without BOM:
   - In VS Code: Open the file → bottom bar → click "UTF-8 with BOM" → select "Save with Encoding" → choose "UTF-8" (without BOM).
   - With Python: `python3 -c "open('file.md','wb').write(open('file.md','rb').read().lstrip(b'\\xef\\xbb\\xbf'))"`
   - With `sed` (macOS): `sed -i '' '1s/^\xef\xbb\xbf//' rules/file.md`

### [MAJOR] File is not valid UTF-8: {error}
**Source**: `validate_rules.py` — `validate_rule_file()` via `check_utf8_encoding()` (from `cpv_validation_common.py`)
**What it means**: The file contains byte sequences that are not valid UTF-8. Claude Code requires all rule files to be UTF-8 encoded.
**How to fix**:
1. Identify the encoding: `file -i rules/{filename}` (Linux) or `file rules/{filename}` (macOS).
2. Convert to UTF-8:
   - With `iconv`: `iconv -f ISO-8859-1 -t UTF-8 rules/file.md > rules/file_utf8.md && mv rules/file_utf8.md rules/file.md`
   - In VS Code: Open → bottom bar → click encoding → "Reopen with Encoding" → select correct encoding → then "Save with Encoding" → UTF-8.

### [MAJOR] Rule file is not valid UTF-8
**Source**: `validate_rules.py` — `validate_rule_file()`
**What it means**: After stripping a BOM, the file still cannot be decoded as UTF-8.
**How to fix**: Same steps as "File is not valid UTF-8" above.

---

## 3. Rule File Content Issues

### [MINOR] Rule file is empty
**Source**: `validate_rules.py` — `validate_rule_file()`
**What it means**: A rule file exists but contains no content (or only whitespace). Empty rule files add no value to the model context.
**How to fix**:
1. Add content to the rule file describing the rule for the model.
2. Or remove the empty file if it is not needed.

### [MINOR] Rule file has frontmatter but no content body
**Source**: `validate_rules.py` — `validate_rule_file()`
**What it means**: The rule file starts with YAML frontmatter (`---`) but has no content after the closing `---`. The model receives no actual rule instructions.
**How to fix**:
1. Add rule content after the closing `---` frontmatter delimiter.
2. Example:
   ```markdown
   ---
   paths:
     - "src/**/*.py"
   ---

   Always add type hints to all Python function signatures.
   ```

### [MINOR] Frontmatter is not a YAML mapping
**Source**: `validate_rules.py` — `validate_rule_file()`
**What it means**: The YAML between `---` delimiters parsed successfully but is not a dictionary/mapping (e.g., it is a list or a scalar value).
**How to fix**:
1. Ensure the frontmatter is a valid YAML mapping (key-value pairs):
   ```yaml
   ---
   paths:
     - "src/**/*.py"
   ---
   ```
2. Check for indentation errors or missing colons.

### [MAJOR] Invalid YAML frontmatter: {error}
**Source**: `validate_rules.py` — `validate_rule_file()`
**What it means**: The YAML between the `---` delimiters is malformed and could not be parsed.
**How to fix**:
1. Check the YAML syntax in the frontmatter block.
2. Use a YAML linter: `python3 -c "import yaml; yaml.safe_load(open('rules/file.md').read().split('---')[1])"`.
3. Common issues:
   - Missing colons after keys
   - Wrong indentation (YAML requires consistent spaces, not tabs)
   - Unescaped special characters (`:`, `#`, `{`, `}`)

---

## 4. Frontmatter Issues

### [MINOR] Unknown frontmatter field '{key}' in rule file — only 'paths' is recognized by Claude Code.
**Source**: `validate_rules.py` — `_validate_frontmatter()` (emitted via `report.minor()`, so a typo of `paths:` stays visible)
**What it means**: The rule file's frontmatter contains a field that Claude Code does not recognize. Only the `paths` field is documented as a recognized frontmatter field for rule files. Unknown fields are silently ignored by Claude Code.
**How to fix**:
1. Remove the unknown field from the frontmatter if it serves no purpose.
2. If you need metadata, consider adding it as a comment in the rule body instead of frontmatter.
3. The only valid frontmatter field for rule files is `paths`.

### [MAJOR] 'paths' must be an array of glob patterns
**Source**: `validate_rules.py` — `_validate_frontmatter()`
**What it means**: The `paths` field in the frontmatter is not a YAML list. Claude Code expects `paths` to be a list of glob pattern strings.
**How to fix**:
1. Change `paths` to be a YAML list:
   ```yaml
   ---
   paths:
     - "src/**/*.py"
     - "tests/**/*.py"
   ---
   ```
2. Do not use a string or dictionary for `paths`.

### [MAJOR] paths[{i}] must be a string, got {type}
**Source**: `validate_rules.py` — `_validate_frontmatter()`
**What it means**: One of the entries in the `paths` array is not a string (e.g., it is a number, boolean, or nested object).
**How to fix**:
1. Ensure every item in the `paths` array is a quoted string:
   ```yaml
   ---
   paths:
     - "src/**/*.py"    # correct
     - "tests/"         # correct
   ---
   ```

### [MINOR] paths[{i}] is empty
**Source**: `validate_rules.py` — `_validate_frontmatter()`
**What it means**: One of the path glob patterns in the `paths` array is an empty string. Empty patterns match nothing useful.
**How to fix**:
1. Remove the empty entry from the `paths` array.
2. Or replace it with a valid glob pattern.

---

## 5. Security Issues in Rule Files

### [CRITICAL] Potential secret found: {description}
**Source**: `validate_rules.py` — `validate_rule_file()`
**What it means**: A pattern matching a secret (API key, private key, token, etc.) was detected in the rule file. Rule files are loaded into model context and should never contain secrets.
**How to fix**:
1. **Immediately revoke** the exposed credential at its issuing service.
2. Remove the secret from the rule file.
3. If you need to reference a secret pattern in a rule, describe it abstractly (e.g., "use your API key from environment variable `$MY_API_KEY`") rather than including an actual key.
4. If the secret was committed to git, use BFG Repo Cleaner or `git filter-branch` to purge it from history.

### [MAJOR] Private path found: {match}
**Source**: `validate_rules.py` — `validate_rule_file()`
**What it means**: A hardcoded path specific to a user's home directory (e.g., `/Users/alice/`, `/home/bob/`) was found in a rule file. This breaks portability.
**How to fix**:
1. Replace the hardcoded path with a portable reference:
   - Use `${CLAUDE_PLUGIN_ROOT}` for plugin-relative paths.
   - Use `$HOME` for home-directory-relative paths.
   - Use relative paths where possible.
2. Example fix:
   - Before: `<absolute-home>/my-plugin/rules/`
   - After: `${CLAUDE_PLUGIN_ROOT}/rules/`

---

## 6. Token Budget Issues

### [WARNING] Total rules content is ~{tokens} estimated tokens ({chars} chars, {lang} content) — exceeds {budget} token budget. Large rules consume model context and may degrade performance. Consider splitting into path-specific rules or reducing content.
**Source**: `validate_rules.py` — `validate_rules_directory()`
**What it means**: The combined content of all rule files in `rules/` exceeds the 10,000 estimated token budget. All rule files are loaded into model context alongside `CLAUDE.md`, so oversized rules reduce the context available for actual work.
**How to fix**:
1. Review all rule files for redundant or verbose content and trim them.
2. Split large rules into path-specific rules using `paths:` frontmatter — rules with path matchers only load when working on matching files.
   ```yaml
   ---
   paths:
     - "src/api/**"
   ---
   Only apply these API-specific coding standards when working in src/api/.
   ```
3. Move reference content (tables, lists) to a separate documentation file rather than a rule file.
4. Combine related rules into a single concise file instead of many small files with overlapping content.
5. Estimated token budget: 10,000 tokens total for all `rules/*.md` files combined.

### [WARNING] Total rules content is ~{tokens} estimated tokens ({chars} chars, {lang} content) — approaching {budget} token budget. Consider reviewing for redundancy.
**Source**: `validate_rules.py` — `validate_rules_directory()`
**What it means**: The combined rule files are between 80% and 100% of the 10,000 token budget. No immediate action is required, but it is recommended to review for opportunities to reduce size before it becomes a problem.
**How to fix**:
1. Review rule files for repetitive or redundant instructions.
2. Consider using path-specific `paths:` frontmatter to scope rules to relevant files only.
3. Consolidate overlapping rules.
