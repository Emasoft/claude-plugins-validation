# Skill Validation — Issues and Fixes

Comprehensive remediation guide for all issues detected by `validate_skill.py` and `validate_skill_comprehensive.py`.

## Table of Contents

- [1. Structure Issues](#1-structure-issues)
- [2. Frontmatter Issues](#2-frontmatter-issues)
- [3. Name Field Issues](#3-name-field-issues)
- [4. Description Quality Issues](#4-description-quality-issues)
- [5. Token Budget and Progressive Disclosure](#5-token-budget-and-progressive-disclosure)
- [6. Required Sections (Strict Mode)](#6-required-sections-strict-mode)
- [7. Reference File Issues](#7-reference-file-issues)
- [8. TOC Embedding Issues](#8-toc-embedding-issues)
- [9. Allowed-Tools Issues](#9-allowed-tools-issues)
- [10. Content Quality Issues](#10-content-quality-issues)
- [10a. String Substitutions](#10a-string-substitutions)
- [11. 8+1 Pillars Issues](#11-81-pillars-issues)
- [12. OpenSpec Mode Issues](#12-openspec-mode-issues)

## Checklist

- [ ] Identify the skill and the specific finding (file + severity)
- [ ] Match the finding to one of the 12 sections below
- [ ] Read SKILL.md + any referenced files mentioned in the finding
- [ ] Apply the fix — respect the 5000-char SKILL.md limit (move bulk to references/)
- [ ] Re-run `validate_skill.py --strict` against the skill

---

## 1. Structure Issues

### CRITICAL: Skill path does not exist

**Error message**: `Skill path does not exist: {path}`
**Severity**: CRITICAL
**Source**: `validate_skill_comprehensive.py` — `validate_skill()`
**Root cause**: The supplied path does not exist on the filesystem.
**Fix**:
1. Verify the path you passed to the validator actually exists
2. Check for typos in the directory name
3. Ensure you are passing the skill directory, not the SKILL.md file itself

### CRITICAL: Skill path is not a directory

**Error message**: `Skill path is not a directory: {path}`
**Severity**: CRITICAL
**Source**: Both scripts — `validate_skill()`
**Root cause**: The path exists but it is a file, not a directory. Skills must be directories containing a SKILL.md file.
**Fix**:
1. Pass the parent directory that contains the SKILL.md, not the file itself
2. Example: `validate_skill.py my-skill/` not `validate_skill.py my-skill/SKILL.md`

### CRITICAL: SKILL.md not found

**Error message**: `SKILL.md not found (required)`
**Severity**: CRITICAL
**Source**: Both scripts — `validate_skill_md_exists()`
**Root cause**: The directory does not contain a SKILL.md file (case-sensitive).
**Fix**:
1. Create a `SKILL.md` file in the skill directory root
2. At minimum, the file should contain a description of what the skill does
3. Example minimal SKILL.md:
```markdown
---
name: my-skill
description: "Use when the user asks to do X. Performs Y by Z."
---

# My Skill

Instructions for Claude when this skill is invoked.
```

### MINOR: SKILL.md should be uppercase

**Error message**: `SKILL.md should be uppercase (found 'skill.md')`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_skill_md_exists()`
**Root cause**: The file is named `skill.md` (lowercase) instead of `SKILL.md` (uppercase).
**Fix**:
1. Rename `skill.md` to `SKILL.md`:
```bash
mv skill.md SKILL.md
```

---

## 2. Frontmatter Issues

### INFO: No YAML frontmatter found

**Error message**: `No YAML frontmatter found (optional but recommended)`
**Severity**: INFO
**Source**: Both scripts — `validate_frontmatter()` / `validate_frontmatter_structure()`
**Root cause**: The SKILL.md file does not start with `---` frontmatter delimiters.
**Fix**:
1. Add YAML frontmatter at the very beginning of SKILL.md:
```markdown
---
name: my-skill
description: "Use when the user needs to ..."
---
```

### CRITICAL: Malformed YAML frontmatter

**Error message**: `Malformed YAML frontmatter (missing closing --- or invalid YAML)`
**Severity**: CRITICAL
**Source**: Both scripts — `validate_frontmatter()` / `validate_frontmatter_structure()`
**Root cause**: The file starts with `---` but the YAML block is malformed (missing closing `---`, invalid YAML syntax, or unclosed quotes).
**Fix**:
1. Ensure the frontmatter has both opening and closing `---` delimiters
2. Validate the YAML with an online YAML linter
3. Common issues:
   - Missing closing `---`
   - Unquoted colons in values (use quotes: `description: "Use when: X"`)
   - Tab characters instead of spaces
   - Incorrect indentation for nested fields

### WARNING: Unknown frontmatter field

**Error message**: `Unknown frontmatter field '{key}' (may be ignored by CLI)`
**Severity**: WARNING
**Source**: `validate_skill.py` — `validate_frontmatter()` / `validate_skill_comprehensive.py` — `validate_field_whitelist()`
**Root cause**: A field in the frontmatter is not recognized by the Claude Code CLI.
**Fix**:
1. Remove unrecognized fields, or verify they are intentional
2. Known fields for Claude Code (16 fields, aligned with skills.md v2.1.152):
   `name`, `description`, `when_to_use`, `argument-hint`, `arguments`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `disallowed-tools`, `model`, `context`, `agent`, `hooks`, `effort`, `paths`, `shell`
3. New in v2.1.121: `arguments` declares named positional args used by `$<name>` substitution in skill body (space-separated string OR YAML list).
4. Skill substitution variables: `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N` (positional), `$<name>` (must match `arguments:`), `${CLAUDE_SESSION_ID}`, `${CLAUDE_EFFORT}` (v2.1.120), `${CLAUDE_SKILL_DIR}`, `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, `${CLAUDE_PROJECT_DIR}`. Undeclared `$<name>` refs silently expand to "" — CPV emits MAJOR.
5. Additional enterprise/OpenSpec fields: `license`, `metadata`, `compatibility`, `version`, `author`, `mode`, `tags`

### MINOR: Deprecated field

**Error message**: `Deprecated field '{key}' (may be ignored by CLI)`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_field_whitelist()`
**Root cause**: A deprecated field (e.g., `when_to_use`) is present in the frontmatter.
**Fix**:
1. Replace deprecated fields with their modern equivalents
2. `when_to_use` -> include in the `description` field as a "Use when ..." phrase

### CRITICAL: Frontmatter exceeds character limit (error threshold)

**Error message**: `Frontmatter exceeds 15000 characters ({chars} chars)`
**Severity**: CRITICAL
**Source**: `validate_skill_comprehensive.py` — `validate_frontmatter_structure()`
**Root cause**: The YAML frontmatter is excessively large (>15,000 characters), consuming too many tokens.
**Fix**:
1. Move large content from frontmatter into the body of the SKILL.md
2. Keep frontmatter lean: only fields the CLI needs (name, description, allowed-tools, etc.)
3. Move long descriptions or instructions into the markdown body

### MINOR: Frontmatter exceeds character warning threshold

**Error message**: `Frontmatter exceeds 12000 characters ({chars} chars)`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_frontmatter_structure()`
**Root cause**: The YAML frontmatter is large (>12,000 characters).
**Fix**:
1. Same as above but less urgent. Consider trimming frontmatter content

### CRITICAL: Boolean field type mismatch

**Error message**: `'{field_name}' must be a boolean (true/false), got {type}`
**Severity**: CRITICAL
**Source**: Both scripts — `validate_boolean_field()`
**Root cause**: A boolean field (`user-invocable` or `disable-model-invocation`) has a non-boolean value.
**Fix**:
1. Use YAML booleans without quotes:
```yaml
user-invocable: true
disable-model-invocation: false
```
2. Do NOT use strings like `"true"`, `"false"`, `"yes"`, `"no"`

---

## 3. Name Field Issues

### INFO: No 'name' field (will use directory name)

**Error message**: `No 'name' field (will use directory name: {dir_name})`
**Severity**: INFO
**Source**: Both scripts — `validate_name_field()`
**Root cause**: The `name` field is missing. The CLI will use the directory name instead.
**Fix**: (Optional) Add an explicit `name` field:
```yaml
name: my-skill-name
```

### CRITICAL: Missing required field: 'name' (OpenSpec strict)

**Error message**: `Missing required field: 'name'`
**Severity**: CRITICAL
**Source**: `validate_skill_comprehensive.py` — `validate_name_field()` (when `--openspec` flag is used)
**Root cause**: In OpenSpec strict mode, the `name` field is required.
**Fix**: Add the `name` field to frontmatter.

### CRITICAL: Name must be a string

**Error message**: `'name' must be a string, got {type}`
**Severity**: CRITICAL
**Source**: Both scripts — `validate_name_field()`
**Root cause**: The `name` value is a number, boolean, or other non-string type.
**Fix**: Ensure name is a quoted string:
```yaml
name: "my-skill"
```

### MAJOR: Skill name exceeds 64 characters

**Error message**: `Skill name exceeds 64 characters ({len} chars): {name}`
**Severity**: MAJOR
**Source**: Both scripts — `validate_name_field()`
**Root cause**: Skill names have a maximum length of 64 characters per the official spec.
**Fix**: Shorten the skill name while keeping it descriptive.

### MAJOR: Skill name must be lowercase

**Error message**: `Skill name must be lowercase: {name}`
**Severity**: MAJOR
**Source**: Both scripts — `validate_name_field()`
**Root cause**: Skill names must use only lowercase characters.
**Fix**: Convert to lowercase:
```yaml
# Wrong
name: My-Skill
# Correct
name: my-skill
```

### MAJOR: Skill name invalid characters

**Error message**: `Skill name must use only lowercase letters, numbers, hyphens: {name}` (basic) or `Skill name must use only letters, numbers, hyphens: {name}` (comprehensive)
**Severity**: MAJOR
**Source**: Both scripts — `validate_name_field()`
**Root cause**: Skill name contains invalid characters (underscores, spaces, special characters).
**Fix**: Use only `a-z`, `0-9`, and `-`:
```yaml
# Wrong
name: my_skill
name: "my skill"
# Correct
name: my-skill
```

### MAJOR: Skill name cannot start or end with a hyphen

**Error message**: `Skill name cannot start or end with a hyphen`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_name_field()`
**Root cause**: Name begins or ends with `-`.
**Fix**: Remove leading/trailing hyphens:
```yaml
# Wrong
name: -my-skill-
# Correct
name: my-skill
```

### MAJOR: Skill name cannot contain consecutive hyphens

**Error message**: `Skill name cannot contain consecutive hyphens`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_name_field()`
**Root cause**: Name contains `--`.
**Fix**: Replace double hyphens with single:
```yaml
# Wrong
name: my--skill
# Correct
name: my-skill
```

### MAJOR: Skill name contains reserved word

**Error message**: `Skill name contains reserved word: {name}`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_name_field()`
**Root cause**: Skill name contains "anthropic" or "claude".
**Fix**: Rename the skill to avoid reserved words.

### CRITICAL: Skill name contains XML tags

**Error message**: `Skill name contains XML tags (forbidden): {name}`
**Severity**: CRITICAL
**Source**: `validate_skill_comprehensive.py` — `validate_name_field()`
**Root cause**: The name field contains HTML/XML tags like `<b>` or `<custom>`.
**Fix**: Remove all XML/HTML tags from the name field.

### MINOR: Skill name uses vague/generic words

**Error message**: `Skill name uses vague/generic word(s): {words} - consider more specific naming`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_name_field()`
**Root cause**: Name contains generic words like `helper`, `util`, `tool`, `data`, `file`, `misc`, `general`, `common`, `shared`, `base`, `core`.
**Fix**: Use domain-specific naming:
```yaml
# Wrong
name: pdf-helper
# Correct
name: processing-pdfs
```

### INFO: Consider gerund naming pattern

**Error message**: `Consider gerund naming pattern (verb + -ing) for skill: {name}`
**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_name_field()`
**Root cause**: Anthropic docs recommend verb + -ing format for skill names.
**Fix**: (Optional) Rename using gerund pattern:
```yaml
# Before
name: pdf-converter
# After
name: converting-pdfs
```

### INFO: Skill name differs from directory name

**Error message**: `Skill name '{name}' differs from directory name '{dir_name}'`
**Severity**: INFO (MAJOR in OpenSpec strict mode)
**Source**: Both scripts — `validate_name_field()`
**Root cause**: The `name` field value does not match the directory name.
**Fix**: Either rename the directory or update the `name` field to match:
```bash
# Option 1: Rename directory
mv old-name/ my-skill/
# Option 2: Update frontmatter
name: old-name
```

---

## 4. Description Quality Issues

### INFO: No 'description' field (body content fallback)

**Error message**: `No 'description' field (will use first paragraph of content)`
**Severity**: INFO
**Source**: Both scripts — `validate_description_field()`
**Root cause**: No explicit `description` in frontmatter; Claude will use the first paragraph of the body.
**Fix**: Add an explicit description for better discoverability:
```yaml
description: "Use when the user asks to analyze CSV files. Reads, validates, and summarizes tabular data."
```

### MAJOR: No description and no body content

**Error message**: `No 'description' field and no body content for fallback`
**Severity**: MAJOR
**Source**: Both scripts — `validate_description_field()`
**Root cause**: Neither frontmatter description nor body content exists.
**Fix**: Add a `description` field AND body content to the SKILL.md.

### MAJOR: Description must be a string

**Error message**: `'description' must be a string, got {type}`
**Severity**: MAJOR
**Source**: Both scripts — `validate_description_field()`
**Root cause**: The description value is not a string (e.g., a list or number).
**Fix**: Use a quoted string:
```yaml
description: "Analyzes CSV files and generates summary reports."
```

### MAJOR: Description contains XML tags

**Error message**: `Description contains XML tags (forbidden) - use plain text`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_description_field()`
**Root cause**: Description contains HTML/XML tags.
**Fix**: Remove all XML/HTML tags and use plain text only.

### MINOR: Description is very short

**Error message**: `Description is very short (may not help Claude decide when to use)` (basic, <10 chars) or `Description is very short (< 20 chars)` (comprehensive, <20 chars)
**Severity**: MINOR
**Source**: Both scripts — `validate_description_field()`
**Root cause**: The description is too short to be useful for Claude's skill selection.
**Fix**: Write a more detailed description that explains when and how to use the skill.

### MINOR: Description is long

**Error message**: `Description is long ({len} chars), consider shortening` (basic script `validate_skill.py`, >500 chars)
**Severity**: MINOR
**Source**: `validate_skill.py` — `validate_description_field()`
**Root cause**: The description exceeds the basic script's char-based readability advisory.
**Note**: The comprehensive script (`validate_skill_comprehensive.py`) no longer emits a char-based MINOR here — its description-size gate is the 200-token MAJOR limit (TRDD-021250b5; see "Description exceeds maximum length" above).
**Fix**: Move detailed instructions to the body content; keep description concise.

### MAJOR: Description exceeds maximum length

**Error message**: `'description' is ~N estimated Claude tokens (limit 200; N chars; o200k_base BPE (N tokens) x1.3 Claude-correction, rounded up). Tighten to a focused sentence...`
(The count is o200k BPE with a deliberate x1.3 Claude-correction — Claude's tokenizer is not public and runs ~20-25% over o200k, so the gate errs strict. Comparing the number against `tiktoken`/cl100k will read ~30% "high"; that is the disclosed margin, not a bug — issue #193.)
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_description_field()`
**Root cause**: Description exceeds the 200-token limit (TRDD-021250b5; was a 1024-character hard limit). The limit is now token-based and non-negotiable — there is no per-plugin override.
**Fix**: Trim the description to ≤200 tokens — keep one focused sentence describing WHAT the skill does and WHEN to use it. Move any detailed instructions, examples, or background into the SKILL.md body or `references/` (progressive disclosure).

### MAJOR: Description must include 'Use when' phrase (strict mode)

**Error message**: `Description must include 'Use when ...' phrase (Nixtla strict mode)`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_description_field()` (with `--strict`)
**Root cause**: In strict mode, the description must explicitly state when the skill should be triggered.
**Fix**:
```yaml
description: "Use when the user asks to refactor Python code. Applies PEP 8 formatting and type hints."
```

### MINOR: Description should include 'Trigger with' phrase (strict mode)

**Error message**: `Description should include 'Trigger with ...' phrase (Nixtla strict mode)`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_description_field()` (with `--strict`)
**Root cause**: In strict mode, the description should tell users how to invoke the skill.
**Fix**:
```yaml
description: "Use when analyzing data. Trigger with /analyze-data <filepath>."
```

### MINOR: Non-user-invocable skill should include 'Loaded by' or 'Used by'

**Error message**: `Non-user-invocable skill should include 'Loaded by <agent-name>' or 'Used by <agent-name>'`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_description_field()` (with `--strict`)
**Root cause**: Skills with `user-invocable: false` are only loaded by agents. The **frontmatter `description` field** must say which agent loads them so it's clear who consumes the skill.
**Fix**: Append "Loaded by <agent-name> agent." to the **`description:` field in the YAML frontmatter** — NOT to the markdown body.
```yaml
# WRONG — added to body (fixer agent mistake)
---
name: my-skill
description: "Use when processing data."
user-invocable: false
---
Loaded by data-agent

# CORRECT — in the description field
---
name: my-skill
description: "Use when processing data. Loaded by data-agent."
user-invocable: false
---
```
**Important**: The validator checks `RE_LOADED_BY.search(desc)` where `desc` is the frontmatter description. Adding "Loaded by" to the markdown body will NOT fix this issue.

### MAJOR: Description uses first person (strict mode)

**Error message**: `Description must NOT use first person (I can / I will)`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_description_field()` (with `--strict`)
**Root cause**: Description contains "I can", "I will", "I am", or "I help".
**Fix**: Rewrite in third person or imperative:
```yaml
# Wrong
description: "I can convert images to PDF format."
# Correct
description: "Use when converting images to PDF format. Supports JPEG, PNG, and TIFF."
```

### MAJOR: Description uses second person (strict mode)

**Error message**: `Description must NOT use second person (You can / You should)`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_description_field()` (with `--strict`)
**Root cause**: Description contains "You can", "You should", "You will", or "You need".
**Fix**: Rewrite without addressing the user:
```yaml
# Wrong
description: "You can use this to convert images."
# Correct
description: "Converts images to PDF format when requested."
```

### INFO: Description should include 'Use when' phrase (non-strict)

**Error message**: `Description should include 'Use when ...' phrase for better discoverability`
**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_description_field()` (without `--strict`)
**Root cause**: The description lacks a "Use when" trigger phrase.
**Fix**: Same as the strict-mode version but optional.

---

## 5. Token Budget and Progressive Disclosure

### MAJOR: SKILL.md exceeds line limit (comprehensive validator)

**Error message**: `SKILL.md has {lines} lines (max 500). Use progressive disclosure — move content to reference files, or split into smaller focused skills.`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_token_budget()`
**Root cause**: The SKILL.md file exceeds the 500-line structural progressive-disclosure guard (`MAX_SKILL_LINES = 500`). The comprehensive validator treats this as a hard MAJOR — there is no per-plugin override (TRDD-021250b5).
**Fix**:
1. Move detailed content into reference files under `references/`
2. Keep SKILL.md as a concise overview with links to detailed files
3. Example restructuring:
```
my-skill/
  SKILL.md              (< 500 lines: overview + core instructions)
  references/
    api-reference.md    (detailed API docs)
    examples.md         (code examples)
    troubleshooting.md  (error handling guide)
```

### MINOR: SKILL.md exceeds line recommendation (basic validator)

**Error message**: `SKILL.md has {lines} lines (recommended: under 500). Consider moving detailed content to supporting files.`
**Severity**: MINOR
**Source**: `validate_skill.py` — `validate_skill_content()`
**Root cause**: The SKILL.md file exceeds 500 lines. The basic validator surfaces this as a MINOR recommendation; the comprehensive validator surfaces the same 500-line threshold as the MAJOR above.
**Fix**: Same as above, but less urgent.

### MAJOR: SKILL.md body exceeds token budget

**Error message**: `SKILL.md body is ~{N} tokens (limit 5000; {method} estimate). Split into smaller, more focused skills — move detail to reference files (the body is kept only to ~5000 tokens after auto-compaction).`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_token_budget()` (via the shared `check_token_limit`)
**Root cause**: The body is gated against `SKILL_BODY_TOKEN_LIMIT = 5000` **tokens** (not words). The old word-count caps (`Content exceeds 5000 words`, `Content is lengthy`) were removed in favour of this token-based gate (TRDD-021250b5); there is no per-plugin override and no separate MINOR "lengthy" tier.
**Fix**: Move verbose content into reference files under `references/` and link from SKILL.md, or split the skill into smaller focused skills.

### MAJOR: SKILL.md has no content after frontmatter

**Error message**: `SKILL.md has no content after frontmatter`
**Severity**: MAJOR
**Source**: `validate_skill.py` — `validate_skill_content()`
**Root cause**: The file only has frontmatter with no body content.
**Fix**: Add instructional content after the closing `---`:
```markdown
---
name: my-skill
description: "..."
---

# My Skill

Step-by-step instructions for Claude when this skill is invoked.

1. First, do X
2. Then, do Y
3. Finally, verify Z
```

### INFO: Task-oriented skill without $ARGUMENTS placeholder

**Error message**: `Task-oriented skill without $ARGUMENTS placeholder (arguments will be appended automatically)`
**Severity**: INFO
**Source**: `validate_skill.py` — `validate_skill_content()`
**Root cause**: The skill has numbered steps or bash code blocks but does not reference `$ARGUMENTS`.
**Fix**: (Optional) Add `$ARGUMENTS` where user input should be substituted:
```markdown
1. Read the file specified by the user: $ARGUMENTS
2. Analyze its contents
```

---

## 6. Required Sections (Strict Mode)

These issues only appear when using the `--strict` flag (Nixtla strict mode).

### MAJOR: Required section missing

**Error message**: `Required section missing: '{section}' (Nixtla strict mode)`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_required_sections()` (with `--strict`)
**Root cause**: A required section header is missing from the body content.
**Required sections**:
- `## Overview`
- `## Prerequisites`
- `## Instructions`
- `## Output`
- `## Error Handling`
- `## Examples`
- `## Resources`

**Fix**: Add each missing section:
```markdown
## Overview
Brief description of what this skill does.

## Prerequisites
- Python 3.10+
- Required packages: pandas, numpy

## Instructions
1. Step one
2. Step two

## Output
Description of what the skill produces.

## Error Handling
What to do when things go wrong.

## Examples
Concrete input/output examples.

## Resources
Links to documentation, references.
```

### MAJOR: Instructions section lacks numbered steps

**Error message**: `'## Instructions' must include numbered step-by-step list`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_required_sections()` (with `--strict`)
**Root cause**: The `## Instructions` section exists but does not contain a numbered list (1. 2. 3. ...).
**Fix**: Add numbered steps inside the Instructions section:
```markdown
## Instructions
1. Read the input file
2. Parse the data structure
3. Apply transformations
4. Write the output file
5. Verify the result
```

---

## 7. Reference File Issues

### MAJOR: Referenced file not found

**Error message**: `Referenced file not found: {path}` or `Referenced script not found: '{baseDir}/scripts/{path}'`
**Severity**: MAJOR
**Source**: Both scripts — `validate_supporting_files()` / `validate_resource_references()`
**Root cause**: A markdown link in SKILL.md points to a local file that does not exist.
**Fix**:
1. Create the missing file at the referenced path
2. Or fix the link to point to the correct file
3. Verify paths are relative to the skill directory root

### MAJOR: Reference uses parent traversal

**Error message**: `Reference uses parent traversal '../': {path} - skill should be self-contained`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_resource_references()`
**Root cause**: A local link uses `../` to reference files outside the skill directory.
**Fix**: Copy the referenced file into the skill directory (e.g., into `references/`) and update the link:
```markdown
# Wrong
[Config](../../shared/config.md)
# Correct
[Config](references/config.md)
```

### MAJOR: Nested references directory

**Error message**: `Nested references directory found: references/{name}/ - references should be one level deep`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_reference_files()`
**Root cause**: The `references/` directory contains nested subdirectories with .md files.
**Fix**: Flatten the reference files into a single level:
```
# Wrong
references/
  category/
    file.md

# Correct
references/
  category-file.md
```

### INFO: Subdirectory in references

**Error message**: `Subdirectory in references: references/{name}/ (OK if for assets)`
**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_reference_files()`
**Root cause**: A subdirectory exists in references/ but does not contain .md files (e.g., an images folder).
**Fix**: No action needed if the subdirectory is for non-markdown assets.

### MINOR: Reference file lacks table of contents

**Error message**: `Reference file has no table of contents in the first 200 characters ({lines} lines): references/{filename}`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_reference_files()`
**Root cause**: A reference .md file has at least `TOC_MIN_LINES` (500) lines but no TOC indicator in its first 200 characters. Files **under 500 lines** without a TOC get an INFO ("OK for short files"), not this MINOR. The TOC must be discoverable in the first 200 chars — a `## Contents` / `## Table of Contents` heading, or markdown anchor links (`- [...](#...)` / `1. [...](#...)`).
**Fix**: Add a table of contents near the top of the reference file (within the first 200 characters):
```markdown
## Table of Contents
- [Section 1](#section-1)
- [Section 2](#section-2)
- [Section 3](#section-3)
```

### MINOR: Could not read reference file

**Error message**: `Could not read reference file: references/{filename}`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_reference_files()`
**Root cause**: The file exists but could not be read (permissions, encoding issues).
**Fix**: Check file permissions and encoding. Ensure it is UTF-8 readable text.

---

## 8. TOC Embedding Issues

### WARNING / MINOR: Partial TOC — `N/M TOC headings embedded`

**Error message** (verbatim from v2.26.0 validator):
- `Link to '<ref>.md' in a list entry of SKILL.md has N/M TOC headings embedded. SKILL.md must copy the COMPLETE TOC of each referenced .md file verbatim immediately after its link — no exceptions, no summaries, no partial lists...` (WARNING on ambiguous list entries)
- `Reference to '<ref>.md' in SKILL.md has N/M TOC headings embedded. ...` (MINOR on clear standalone references)
- `Backtick reference to '<ref>.md' in SKILL.md has N/M TOC headings embedded. Convert to a markdown link and copy the COMPLETE TOC...` (MINOR on backticked references — also flagged as "uses backtick format" MINOR)

**Severity**: WARNING (list-entry ambiguity) or MINOR (standalone / backtick)

**Root cause**: The progressive-discovery algorithm only surfaces reference-file content that is mirrored as a heading inline in SKILL.md. Any heading missing from SKILL.md is **invisible** to agents — they will never fetch that reference for that topic, no matter how relevant the content is. A partial TOC is worse than no TOC because it *looks* like coverage while silently hiding sections.

**The rule is absolute**: SKILL.md must copy the COMPLETE TOC of each referenced file **verbatim**. No summaries. No partial lists. No rephrasing. No omissions. Every heading in the reference file's TOC must appear in SKILL.md adjacent to the link.

**Two — and only two — legitimate fixes**:

#### Fix A: Embed the full TOC (the default answer)

If the reference file's TOC is accurate and all headings represent content worth discovering, copy every entry into SKILL.md immediately after the link.

```markdown
- [API Reference](references/api-reference.md)
  > Authentication · Endpoints · Error Codes · Rate Limits · Pagination ·
  > Webhooks · Idempotency · SDKs · Migration Guide
```

Format conventions that work (the validator matches on heading text, not bullet style): indented bullets, inline `>` blockquote with `·` separators, numbered lists — any form where every heading text appears within ~100 lines of the link.

**The text must be VERBATIM, not abbreviated — this is the #1 cause of a `0/N` score.** The match is a substring check against the reference file's *actual heading text*. A condensed per-link summary such as `> TOC A1.1–A1.13: what-it-does · dual-export · …` FAILS: `what-it-does` is not the heading `A1.1 What it does`, so every entry misses and the link scores `0/N` even though it *looks* like a complete index. Copy each heading's exact words (you may drop the leading `## ` and the `(#anchor)`; you may NOT hyphen-collapse `What it does` → `what-it-does`, abbreviate, or rephrase). If you also want a human-friendly abbreviated blurb per link, keep it **in addition** — but the embedded TOC the validator reads must be the verbatim headings.

#### Fix B: Reduce the reference file's own TOC — not SKILL.md

If the TOC is too long to embed comfortably (say, >15 headings), the fix is in **the reference file**, not in SKILL.md:

1. **Merge granular subsections into fewer, more encompassing headings (PREFERRED — content-preserving).** Replace five sibling subsections with one umbrella heading whose body covers all five concerns. Same coverage for agents, fewer TOC entries, and **no prose is lost** — you re-chapter the content, you do not delete it. This is the safe, default Fix B.
2. **Drop sections that are not worth discovering (only when genuinely dead).** A heading in the TOC is an advertisement: "agents should load this file when they need this content." If a subsection is *genuinely* not useful for the skill's purpose, delete its heading from the reference file's TOC (and usually the section). Use this sparingly — deleting load-bearing content to clear a finding is a worse regression than the finding (see the fixer's content-preservation guardrail).

Both changes happen in the reference file, then SKILL.md mirrors the new (shorter) TOC verbatim.

**Fix B is MANDATORY (not optional) for the references-heavy catch-22.** When a skill links many references (e.g. ~20), embedding every full TOC verbatim would push SKILL.md over the body-size cap (a MAJOR) — so Fix A is *mathematically impossible* and a condensed summary fails the verbatim match (§Fix A). The skill then oscillates: embed-verbatim → over-cap MAJOR → shrink → TOC MINOR returns. There is exactly one escape, and it is plugin-side: **merge the reference files' headings into fewer broad chapters (Fix B option 1) so each shortened TOC fits verbatim AND the SKILL.md stays under the cap, clearing the MINOR and the MAJOR together.** A fixer agent that hits this `CYCLE` (its standard "embed the TOC" fix oscillating against the size cap) switches to this Fix-B merge instead of re-applying the futile embed — see `iterative-fix-loop.md` and `agents/cpv-plugin-fixer-agent.md`. CPV's rule is never relaxed; the plugin is restructured to comply.

#### Forbidden "fixes"

- ❌ Writing a paragraph-long summary in SKILL.md and calling that "the TOC" — agents cannot discover individual topics from prose.
- ❌ Listing only the headings you *think* are important — the whole point of discovery is that agents, not authors, decide what's relevant.
- ❌ Using a backticked file reference without a markdown link (e.g., `` `references/foo.md` ``) — backtick references are invisible to the discovery algorithm. Always use `[Title](references/foo.md)` form.
- ❌ Using a hand-written "See references/ for details" stub — same problem, discovery sees nothing.

**Binary test**: Either the content is worth discovering (embed the full TOC) or it is not (remove it from the reference file's TOC). There is no middle ground.

**Full example of Fix A**:

Suppose `references/api-reference.md` has:
```markdown
## Table of Contents
- [Authentication](#authentication)
- [Endpoints](#endpoints)
- [Error Codes](#error-codes)
- [Rate Limits](#rate-limits)
```

In SKILL.md, instead of:
```markdown
See [API Reference](references/api-reference.md) for details.
```

Write:
```markdown
- [API Reference](references/api-reference.md) — full API documentation:
  - Authentication
  - Endpoints
  - Error Codes
  - Rate Limits
```

**Full example of Fix B (merging)**:

Before — reference file has 11 granular subsections:
```markdown
## Table of Contents
1. Setup Prerequisites
2. Install CLI
3. Install SDK
4. Install Runtime
5. Configure Environment
6. Configure Auth
7. Configure Networking
8. Verify Installation
9. Run Smoke Tests
10. Troubleshoot Install
11. Uninstall
```

After — same coverage consolidated into 4 encompassing headings:
```markdown
## Table of Contents
1. Installation (CLI, SDK, runtime, prerequisites)
2. Configuration (environment, auth, networking)
3. Verification (smoke tests, troubleshooting)
4. Uninstall
```

SKILL.md then embeds the shorter 4-heading TOC verbatim. Agents still discover everything (each umbrella heading advertises the subtopics in parentheses), and the TOC is embeddable.

---

## 9. Allowed-Tools Issues

### MAJOR: allowed-tools wrong type

**Error message**: `'allowed-tools' must be string or list, got {type}`
**Severity**: MAJOR
**Source**: Both scripts — `validate_allowed_tools_field()`
**Root cause**: The `allowed-tools` value is neither a string nor a list.
**Fix**: Use either format:
```yaml
# String (comma-separated)
allowed-tools: "Read, Write, Edit, Bash"
# List
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
```

### WARNING: allowed-tools is empty

**Error message**: `'allowed-tools' is empty ([]) — this forbids ALL tools, only chatting is allowed. If this is not intentional, fix it. If it was a mistaken attempt at allowing all tools, omitting the 'allowed-tools' field entirely is the correct syntax (an absent field means all tools allowed).`
**Severity**: WARNING (non-blocking — does not block publish)
**Source**: Both scripts — `validate_allowed_tools_field()`
**Root cause**: The field is present but the array is empty (`[]` or `""`). This is a **valid** declaration — it means the skill is permitted **no tools** (chat-only) — and is distinct from an **absent** field, which means "inherit **all** tools". It is surfaced (not silently accepted) because an empty array is most often a mistaken attempt at "allow everything".
**Fix**: No fix needed if you genuinely want a chat-only skill. If you meant to allow **all** tools, **omit** the `allowed-tools` field entirely (an absent field grants the full tool surface). If the skill needs a specific subset, list those tools.

### CRITICAL: body invokes a tool the allowed-tools field does not grant

**Error message**: `body invokes the tool '<Tool>' (line N) but the 'allowed-tools' field does not grant it — the call will fail at runtime (silent failure). Add '<Tool>' to 'allowed-tools', or remove the 'allowed-tools' field to allow all tools.` (a prose-only mention is the non-blocking WARNING variant: `body mentions the tool '<Tool>' … (in prose). If this is documentation, ignore it; …`)
**Severity**: CRITICAL when the usage is inside a fenced code block (an intended instruction) or the field is empty `[]`; WARNING when the usage is only a prose mention.
**Source**: `cpv_tool_permission_match.py` — `validate_body_tool_consistency()`, wired into all four validators (TRDD-94e06820).
**Root cause**: The SKILL.md body instructs the model to call a tool (`<Tool>(…)` syntax, or an `mcp__server__tool` reference) that the declared `allowed-tools` field does not grant. At runtime that tool call is denied and the skill fails **silently**. An empty `[]` declaration grants nothing, so any tool usage contradicts it.
**Fix**: Pick one — (a) add `<Tool>` to `allowed-tools` (for MCP: add `mcp__<server>__<tool>`, `mcp__<server>__*`, or the bare `mcp__<server>`); (b) remove the `allowed-tools` field entirely if the component should have all tools (an absent field = all tools allowed); or (c) if the body line is documentation, not real usage, the finding is the non-blocking WARNING — leave it or reword the line so it is not function-call syntax. Note: a declared `Edit(…)` rule also grants `Read`; a declared `Bash(…)` rule also grants `Monitor`; `Task` and `Agent` are aliases. The identical rule applies to command `allowed-tools` and agent `tools` fields.

### MAJOR: allowed-tools must be CSV string (strict mode)

**Error message**: `'allowed-tools' must be comma-separated string (CSV), not YAML array`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_allowed_tools_field()` (with `--strict`)
**Root cause**: In Nixtla strict mode, only CSV string format is accepted, not YAML arrays.
**Fix**:
```yaml
# Wrong (in strict mode)
allowed-tools:
  - Read
  - Write
# Correct
allowed-tools: "Read, Write"
```

### INFO: Unknown tool name

**Error message**: `Unknown tool '{name}' (may be valid if custom MCP tool)`
**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_allowed_tools_field()`
**Root cause**: A tool name is not in the known list of Claude Code built-in tools or MCP tools.
**Fix**: Verify the tool name is correct. Known tools: `Read`, `Write`, `Edit`, `Bash`, `Grep`, `Glob`, `WebFetch`, `WebSearch`, `Task`, `NotebookEdit`, `Skill`, `AskUserQuestion`, `EnterPlanMode`, `ExitPlanMode`, `EnterWorktree`, `TaskCreate`, `TaskUpdate`, `TaskList`, `TaskGet`, `TaskStop`, `ToolSearch`. MCP tools must start with `mcp__`.

### MAJOR: Unscoped Bash forbidden (strict mode)

**Error message**: `Unscoped 'Bash' forbidden in strict mode - use scoped Bash(git:*) or Bash(npm:*)`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_allowed_tools_field()` (with `--strict`)
**Root cause**: In strict mode, bare `Bash` is too permissive.
**Fix**: Use scoped Bash:
```yaml
allowed-tools: "Bash(git:*), Bash(npm:*), Read, Write"
```

### WARNING: Many tools permitted

**Error message** (v2.26.0+): `Many tools permitted ({effective_count} distinct tool surfaces; raw list has {raw_count} entries). Consider limiting...`
**Severity**: WARNING (advisory, non-blocking)
**Source**: `validate_skill_comprehensive.py` — `validate_allowed_tools_field()`
**Threshold**: Fires when **distinct tool surfaces** > 15 **and** the skill is not declared `user-invocable: false`.
**Counting rule (v2.26.0)**: `Bash(...)` sub-scopes collapse to 1 surface — `Bash(git:*), Bash(gh:*), Bash(uv:*)` counts as one Bash surface, not three. `Monitor(...)` collapses the same way. All other tools count individually.
**Root cause**: The skill requests a large tool surface area.

**Three legitimate fixes, pick the one that fits the skill's shape**:

#### Fix A: Trim the tool list

The default answer. Review every entry in `allowed-tools` and remove any tool the skill doesn't actually invoke. Over-permissioning increases the attack surface and confuses the discovery heuristics that pick a skill for a task.

#### Fix B: Consolidate Bash sub-scopes

If the skill binds to many `Bash(foo:*)` entries, merge them where the tooling allows — `Bash(git:*,gh:*,uv:*)` syntactically counts as 3 sub-scopes but is one inline list. Even post-collapse, three bound sub-scopes are cheaper than three separately-declared `Bash(git:*)`, `Bash(gh:*)`, `Bash(uv:*)` lines. Purely a style change, same effective permission.

#### Fix C: Declare the skill non-invocable and gate from the agent

If the skill is loaded only by an agent (not invoked directly by the user), add `user-invocable: false` to the frontmatter. The warning is then suppressed entirely because the agent's own `allowed-tools` list is the real least-privilege boundary — the skill's declared tools describe capability, not a direct grant to the user. Example:

```yaml
---
name: my-skill
description: ...
user-invocable: false   # loaded by agent X; tool surface gated by the agent
allowed-tools: Read, Write, Edit, Grep, Glob, Agent, WebFetch, ...
---
```

#### Forbidden "fix"

- ❌ Inflating the description to make it look invocable just to justify the tools. If the skill is agent-loaded, mark it `user-invocable: false` honestly.

---

## 10. Content Quality Issues

### MAJOR: Absolute/OS-specific path detected

**Error message**: `Line {n}: contains absolute/OS-specific path ({desc}) - use '{baseDir}/...'`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_path_formats()`
**Root cause**: A line outside code blocks contains `/home/user/`, `/Users/user/`, or `C:\Users\`.
**Fix**: Replace with `{baseDir}` placeholder:
```markdown
# Wrong
Read the config at /Users/john/myproject/config.yaml
# Correct
Read the config at {baseDir}/config.yaml
```

### MAJOR: Backslashes in path

**Error message**: `Line {n}: uses backslashes in path - use forward slashes`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_path_formats()`
**Root cause**: Path uses Windows-style backslashes (`\scripts\`, `\references\`).
**Fix**: Replace backslashes with forward slashes:
```markdown
# Wrong
{baseDir}\scripts\setup.sh
# Correct
{baseDir}/scripts/setup.sh
```

### MINOR: Possible Windows-style path

**Error message**: `Line {n}: possible Windows-style path (backslash) - use forward slashes for portability`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_path_formats()`
**Root cause**: A backslash followed by a letter was detected outside code blocks, shell continuations, and escape sequences.
**Fix**: Review the line. If it is a path, convert to forward slashes.

### MINOR: MCP tool reference may need qualification

**Error message**: `Line {n}: MCP tool reference may need qualification (ServerName:tool_name): '{tool_name}'`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_mcp_tool_references()`
**Root cause**: An MCP tool is referenced without the `ServerName:` prefix (e.g., "use the read_file tool" instead of "use the serena:read_file tool").
**Fix**: Add the server name prefix:
```markdown
# Wrong
Use the read_file tool to open the file.
# Correct
Use the serena:read_file tool to open the file.
```

### MINOR: Time-sensitive information detected

**Error message**: `Line {n}: Time-sensitive information may become stale: '{text}'`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_time_sensitive_info()`
**Root cause**: The content references dates, versions, or temporal phrases like "before January 2025" or "since v3.0".
**Fix**: Remove or generalize time-sensitive references:
```markdown
# Wrong
This feature was added after March 2024 in v2.1.
# Correct
This feature requires version 2.1 or later.
```

### MINOR: No checklist pattern found (strict mode)

**Error message**: `No checklist pattern found (best practice: use [ ] / [x] for complex workflows)`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_content_patterns()` (with `--strict`)
**Root cause**: The skill has no checkbox patterns (`- [ ]` or `- [x]`).
**Fix**: Add a checklist for multi-step workflows:
```markdown
## Checklist
Copy this checklist and track your progress:
- [ ] Step 1: Read input
- [ ] Step 2: Validate data
- [ ] Step 3: Transform
- [ ] Step 4: Write output
```

### MINOR: No clear input/output examples found (strict mode)

**Error message**: `No clear input/output examples found (best practice: include concrete examples)`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_content_patterns()` (with `--strict`)
**Root cause**: No code blocks or input/output patterns were found.
**Fix**: Add concrete examples:
````markdown
## Examples

Input:
```json
{"name": "John", "age": 30}
```

Output:
```
Name: John, Age: 30
```
````

### MINOR: Workflow mentioned but few numbered steps

**Error message**: `Workflow mentioned but few numbered steps found (best practice: use 1. 2. 3. format)`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_content_patterns()` (with `--strict`)
**Root cause**: The word "workflow" or "step" appears but there are fewer than 3 numbered steps.
**Fix**: Add numbered workflow steps.

### MINOR: Checklist missing 'Copy this checklist' phrase

**Error message**: `Checklist found but missing 'Copy this checklist and track your progress' phrase (best practice for complex workflows)`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_content_patterns()`
**Root cause**: A checklist exists and the workflow has 3+ steps, but the "Copy this checklist" preamble is missing.
**Fix**: Add the phrase before the checklist:
```markdown
Copy this checklist and track your progress:
- [ ] Step 1
- [ ] Step 2
```

### INFO: String substitution patterns detected

**Error messages**:
- `Skill uses $ARGUMENTS variable ({n} occurrence(s))`
- `Skill uses indexed arguments: $ARGUMENTS[{indices}]`
- `Skill uses shorthand arguments: ${indices}`
- `Skill uses ${CLAUDE_SESSION_ID} ({n} occurrence(s))`

**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_string_substitutions()`
**Root cause**: These are informational messages about detected substitution patterns.
**Fix**: No fix needed. These confirm that string substitutions are being used correctly.

### INFO: Dynamic context injection detected

**Error message**: `Skill uses dynamic context injection (! syntax): {n} occurrence(s)`
**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_dynamic_context()`
**Root cause**: Informational message about detected `!`command`` syntax.
**Fix**: No fix needed. Confirms dynamic context is used.

### INFO: Ultrathink keyword detected

**Error message**: `Skill contains 'ultrathink' keyword (enables extended thinking)`
**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_dynamic_context()`
**Root cause**: The skill explicitly uses the "ultrathink" keyword.
**Fix**: No fix needed. This enables extended thinking mode.

---

## 10a. String Substitutions

### WARNING: Unknown variable reference `${VAR}`

**Error message** (v2.26.0+): `Unknown variable reference: ${VAR}. Valid platform variables: ${CLAUDE_*}, ... (or any CLAUDE_PLUGIN_OPTION_<KEY>). If ${VAR} is a shell variable defined inside a code block, the check will accept it once it's assigned (VAR=..., export VAR=..., or local VAR=...) somewhere in SKILL.md. If it's documentation-only, wrap the reference in backticks (\`${VAR}\`) so the validator treats it as code, not prose.`
**Severity**: WARNING
**Source**: `validate_skill_comprehensive.py` — `validate_string_substitutions()`
**Root cause**: SKILL.md references `${VAR}` in prose and `VAR` is neither a Claude Code platform env var (`CLAUDE_*`, `CLAUDE_PLUGIN_OPTION_<KEY>`, etc.) nor a skill-local shell variable assigned anywhere in the file.

**What the v2.26.0 validator accepts without complaint**:
- Canonical platform env vars: `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, `${CLAUDE_PROJECT_DIR}`, `${CLAUDE_SESSION_ID}`, `${CLAUDE_SKILL_DIR}`, `${CLAUDECODE}`, `${TRACEPARENT}`, etc.
- Dynamic per-plugin-option vars: `${CLAUDE_PLUGIN_OPTION_<ANY_KEY>}`.
- Skill-local shell variables assigned in any code block: `MERGE_SCRIPT=/path/to/script`, `export REPORT_DIR="..."`, `local TS="$(date +%s)"` — once the variable is assigned, later `${VAR}` references in prose are accepted.
- Anything inside inline backticks — `` `${CUSTOM_VAR}` ``, `` `${SOME_VAR_DOC}` `` — the validator strips inline backtick content before the check.

**Three legitimate fixes**:

#### Fix A: Assign the variable somewhere in SKILL.md (preferred for variables the skill actually uses)

If `${MERGE_SCRIPT}` is a shell variable the skill sets up, add the assignment to the setup code block:

```bash
# Before (validator warns on ${MERGE_SCRIPT} in prose below)
Run the merge via ${MERGE_SCRIPT}.

# After — assignment in a code block tells the validator this is skill-local
\`\`\`bash
MERGE_SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/merge.sh"
\`\`\`
Run the merge via ${MERGE_SCRIPT}.
```

The assignment can be in any code block in the document, not necessarily the one preceding the reference.

#### Fix B: Wrap the reference in backticks (for documentation-only references)

If the skill is only *describing* a variable name the user will define elsewhere, wrap every mention in inline backticks:

```markdown
# Before — looks like a platform var reference
Export ${CUSTOM_CONFIG_DIR} before running.

# After — clearly marked as code/documentation
Export `${CUSTOM_CONFIG_DIR}` before running.
```

The v2.26.0 validator strips inline backtick content before the unknown-var check, so `` `${ANY_VAR}` `` is accepted even without an assignment.

#### Fix C: Replace with a canonical platform var

If the skill is actually trying to reference the plugin root, session id, or data dir, use the canonical spelling:

```markdown
# Before — unknown
Output goes to ${PLUGIN_DATA}/results.

# After — canonical
Output goes to ${CLAUDE_PLUGIN_DATA}/results.
```

#### Forbidden "fix"

- ❌ Adding the variable name to `is_valid_plugin_env_var` just to silence the warning. The whitelist is for real Claude Code platform env vars; anything else belongs in one of Fix A/B/C above.

---

## 11. 8+1 Pillars Issues

These issues only appear when using the `--pillars` flag. They apply exclusively to `lang-*` and `convert-*` skills.

### INFO: 8+1 Pillars validation skipped

**Error message**: `8+1 Pillars validation skipped (only for lang-* and convert-* skills)`
**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_pillars()`
**Root cause**: The skill name does not start with `lang-` or `convert-`.
**Fix**: No fix needed unless the skill is a language-specific skill. Rename accordingly.

### MINOR: Pillar has minimal coverage

**Error message**: `Pillar '{name}' has minimal coverage`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_pillars()`
**Root cause**: A pillar topic (Module, Error, Concurrency, Metaprogramming, Zero/Default, Serialization, Build, Testing, or Dev Workflow/REPL) has fewer than 2 keyword occurrences and no dedicated section.
**Fix**: Add content covering the missing pillar. Each language skill should address all 8 pillars:
1. **Module** — import/export, package management
2. **Error** — error handling patterns (Result, try/catch, etc.)
3. **Concurrency** — async/await, threads, channels
4. **Metaprogramming** — macros, decorators, annotations
5. **Zero/Default** — null handling (Option, Maybe, None)
6. **Serialization** — JSON, encoding, parsing
7. **Build** — build tools, dependency management
8. **Testing** — test frameworks, assertions

For REPL-centric languages (Clojure, Elixir, Haskell, etc.), also add:
9. **Dev Workflow/REPL** — interactive development, hot reload

### INFO: Pillar has partial coverage

**Error message**: `Pillar '{name}' has partial coverage`
**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_pillars()`
**Root cause**: A pillar topic has 2-4 keyword occurrences but no dedicated section.
**Fix**: Add more content or a dedicated section for the pillar.

### MAJOR: Pillars coverage is incomplete

**Error message**: `Pillars coverage is incomplete ({score}/{max})`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_pillars()`
**Root cause**: Overall pillar coverage is below 50%.
**Fix**: Add significant content for multiple missing pillars.

### MINOR: Pillars coverage needs improvement

**Error message**: `Pillars coverage needs improvement ({score}/{max})`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_pillars()`
**Root cause**: Overall pillar coverage is between 50% and 75%.
**Fix**: Improve coverage for partially covered pillars.

---

## 12. OpenSpec Mode Issues

These issues only appear when using the `--openspec` flag.

### MAJOR: Unexpected field in frontmatter (OpenSpec)

**Error message**: `Unexpected field '{key}' in frontmatter. OpenSpec allows: {fields}`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_field_whitelist()` (with `--openspec`)
**Root cause**: A frontmatter field is not in the OpenSpec allowed list.
**Allowed OpenSpec fields**: `name`, `description`, `license`, `allowed-tools`, `metadata`, `compatibility`
**Fix**: Remove non-OpenSpec fields or switch to non-OpenSpec mode.

### MAJOR: Directory name must match skill name (OpenSpec)

**Error message**: `Directory name '{dir}' must match skill name '{name}'`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_name_field()` (with `--openspec`)
**Root cause**: In OpenSpec strict mode, the directory name must exactly match the `name` field.
**Fix**: Rename the directory to match the name field or vice versa.

### MAJOR: metadata must be a key-value mapping

**Error message**: `'metadata' must be a key-value mapping (dict), got {type}`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_metadata_field()`
**Root cause**: The `metadata` field is not a dictionary.
**Fix**:
```yaml
metadata:
  author: "Your Name"
  version: "1.0.0"
  category: "development"
```

### MAJOR: metadata key must be string

**Error message**: `'metadata' key must be string, got {type}: {key}`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_metadata_field()`
**Root cause**: A metadata key is not a string (e.g., a number used as key).
**Fix**: Ensure all metadata keys are strings.

### MINOR: metadata value should be string

**Error message**: `'metadata.{key}' value should be string for OpenSpec compliance, got {type}`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_metadata_field()`
**Root cause**: A metadata value is not a string (e.g., a number or boolean).
**Fix**: Convert all values to strings:
```yaml
metadata:
  version: "1.0.0"
  priority: "high"
```

### MAJOR: compatibility must be a string

**Error message**: `'compatibility' must be a string, got {type}`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_compatibility_field()`
**Root cause**: The `compatibility` field is not a string.
**Fix**:
```yaml
compatibility: "Claude Code 1.0+"
```

### MAJOR: compatibility exceeds length limit

**Error message**: `'compatibility' exceeds 500 characters ({len} chars)`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_compatibility_field()`
**Root cause**: The compatibility string is too long.
**Fix**: Shorten to under 500 characters.

### MAJOR: license must be a string

**Error message**: `'license' must be a string, got {type}`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_license_field()`
**Root cause**: The `license` field is not a string.
**Fix**:
```yaml
license: "MIT"
```

### MINOR: license field is empty

**Error message**: `'license' field is empty`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_license_field()`
**Root cause**: The license field is present but empty.
**Fix**: Set a license identifier:
```yaml
license: "MIT"
```

---

## Additional Frontmatter Field Issues

### CRITICAL: context must be a string

**Error message**: `'context' must be a string, got {type}`
**Severity**: CRITICAL
**Source**: Both scripts — `validate_context_field()`
**Root cause**: The `context` field has a non-string value.
**Fix**:
```yaml
context: "fork"
```

### CRITICAL: Invalid context value

**Error message**: `Invalid 'context' value: '{value}'. Valid values: {'fork'}`
**Severity**: CRITICAL
**Source**: Both scripts — `validate_context_field()`
**Root cause**: The context value is not the single valid option. `VALID_CONTEXT_VALUES` is `{'fork'}` — `fork` is the only accepted value. Do NOT set `context: inline` or `context: none`; both are rejected with this CRITICAL.
**Valid values**: `fork` (the only one)
**Fix**: Use `fork`, or omit the field entirely if the skill should run inline (the default — omitting `context` is how you get inline execution, not `context: inline`):
```yaml
context: fork
```

### CRITICAL: agent must be a string

**Error message**: `'agent' must be a string, got {type}`
**Severity**: CRITICAL
**Source**: Both scripts — `validate_agent_field()`
**Root cause**: The `agent` field has a non-string value.
**Fix**:
```yaml
agent: "code"
```

### MAJOR: agent has no effect without context: fork

**Error message**: `'agent' field has no effect without 'context: fork'`
**Severity**: MAJOR
**Source**: Both scripts — `validate_agent_field()`
**Root cause**: The `agent` field is set but `context` is not `fork`.
**Fix**: Either add `context: fork` or remove the `agent` field:
```yaml
context: fork
agent: code
```

### INFO: agent not specified with context: fork

**Error message**: `'agent' not specified with context: fork (defaults to general-purpose)`
**Severity**: INFO
**Source**: Both scripts — `validate_agent_field()`
**Root cause**: `context: fork` is set but no specific agent type is chosen.
**Fix**: (Optional) Add an agent type:
```yaml
context: fork
agent: code
```

### INFO: Custom agent type

**Error message**: `'agent' value '{agent}' is not a built-in type (may be custom from .claude/agents/)`
**Severity**: INFO
**Source**: Both scripts — `validate_agent_field()`
**Root cause**: The agent value is not one of the built-in types.
**Fix**: Verify the custom agent exists in `.claude/agents/`.

### MAJOR: model must be a string

**Error message**: `'model' must be a string, got {type}`
**Severity**: MAJOR
**Source**: Both scripts — `validate_model_field()`
**Root cause**: The `model` field has a non-string value.
**Fix**:
```yaml
model: "sonnet"
```

### MAJOR: Invalid model value

**Error message**: `Invalid 'model' value: '{model}'. Valid: sonnet, opus, haiku, fable, inherit, default, opusplan (optionally with [1m]), or full ID like claude-opus-5`
**Severity**: MAJOR
**Source**: `validate_skill_comprehensive.py` — `validate_model_field()` (gated by the shared `is_valid_model`)
**Root cause**: The model value is not accepted by the shared `is_valid_model` gate. Accepted forms: the short aliases `sonnet`, `opus`, `haiku`, `fable`, `inherit`, `default`, `opusplan`, `best`; any of those (or a full ID) with a `[1m]` 1M-context suffix; or a full model ID like `claude-opus-5` / `claude-sonnet-4-5-20251001`.
**Fix**: Use any accepted form, e.g.:
```yaml
model: sonnet
# also valid: opus[1m], opusplan, fable, claude-opus-5
```

### MINOR: model: haiku less reliable

**Error message**: `'model: haiku' specified - haiku is less reliable for complex tasks. Consider using 'sonnet' or 'inherit' for better accuracy.`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_model_field()`
**Root cause**: Haiku may produce lower quality results for complex skills.
**Fix**: Consider upgrading to `sonnet` or `inherit`:
```yaml
model: sonnet
```

### MAJOR: argument-hint must be a string

**Error message**: `'argument-hint' must be a string, got {type}`
**Severity**: MAJOR
**Source**: Both scripts — `validate_argument_hint_field()`
**Root cause**: The `argument-hint` field has a non-string value.
**Fix**:
```yaml
argument-hint: "<file-path>"
```

### MINOR: argument-hint field is empty

**Error message**: `'argument-hint' field is empty`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_argument_hint_field()`
**Root cause**: The hint field is present but empty.
**Fix**: Provide a meaningful hint:
```yaml
argument-hint: "<file-path> [--format json]"
```

### MAJOR: hooks must be object or string

**Error message**: `'hooks' must be an object, got {type}` (basic) or `'hooks' must be a string (path) or dict (inline config), got {type}` (comprehensive)
**Severity**: MAJOR
**Source**: Both scripts — `validate_hooks_field()`
**Root cause**: The `hooks` field is not a valid type.
**Fix**: Use either inline config or a path:
```yaml
# Inline config
hooks:
  PreToolUse:
    - command: "my-hook.sh"
# Path to config
hooks: "hooks.json"
```

### MINOR: hooks field is empty string

**Error message**: `'hooks' field is empty string`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_hooks_field()`
**Root cause**: The hooks field is `""`.
**Fix**: Either remove the field or provide a valid hooks configuration.

### MINOR: Unknown hook event

**Error message**: `Unknown hook event '{name}'. Valid events: PreToolUse, PostToolUse, Stop, etc.`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_hooks_field()`
**Root cause**: A hook event name is not recognized (not in `VALID_HOOK_EVENTS`).
**Valid events** (the full `VALID_HOOK_EVENTS` set): `ConfigChange`, `CwdChanged`, `DirectoryAdded`, `Elicitation`, `ElicitationResult`, `FileChanged`, `InstructionsLoaded`, `MessageDisplay`, `Notification`, `PermissionDenied`, `PermissionRequest`, `PostCompact`, `PostModelSwitch`, `PostToolBatch`, `PostToolUse`, `PostToolUseFailure`, `PreCompact`, `PreModelSwitch`, `PreToolUse`, `SessionEnd`, `SessionStart`, `Setup`, `Stop`, `StopFailure`, `SubagentStart`, `SubagentStop`, `TaskCompleted`, `TaskCreated`, `TeammateIdle`, `UserPromptExpansion`, `UserPromptSubmit`, `WorktreeCreate`, `WorktreeRemove`
**Fix**: Use a valid hook event name from the list above.

---

## Scripts Directory Issues

### MAJOR: Script not executable

**Error message**: `Script not executable: scripts/{name}`
**Severity**: MAJOR
**Source**: Both scripts — `validate_directory_structure()` / `validate_scripts_directory()`
**Root cause**: A `.sh`, `.py`, or `.bash` file in `scripts/` lacks the executable permission bit.
**Fix**:
```bash
chmod +x scripts/my-script.sh
```

### MINOR: Script lacks shebang line

**Error message**: `Script lacks shebang line (e.g., #!/usr/bin/env python3): scripts/{name}`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_scripts_directory()`
**Root cause**: The script file does not start with `#!`.
**Fix**: Add a shebang as the first line:
```python
#!/usr/bin/env python3
```
```bash
#!/usr/bin/env bash
```

### MINOR: Python script has non-Python shebang

**Error message**: `Python script has non-Python shebang: {shebang}`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_scripts_directory()`
**Root cause**: A `.py` file has a shebang that does not contain "python".
**Fix**: Use a Python shebang:
```python
#!/usr/bin/env python3
```

### MINOR: Shell script has non-shell shebang

**Error message**: `Shell script has non-shell shebang: {shebang}`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_scripts_directory()`
**Root cause**: A `.sh` or `.bash` file has a shebang that does not contain "sh" or "bash".
**Fix**: Use a shell shebang:
```bash
#!/usr/bin/env bash
```

### MINOR: Python script lacks module docstring

**Error message**: `Python script lacks module docstring: scripts/{name} (best practice: add '''Description of what script does''')`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_scripts_directory()`
**Root cause**: A Python script has no triple-quoted docstring in the first 500 characters.
**Fix**: Add a module docstring after the shebang:
```python
#!/usr/bin/env python3
"""
Generate summary reports from CSV data.

Reads input CSV, applies transformations, and outputs
a formatted summary report.
"""
```

### MINOR: Could not read script

**Error message**: `Could not read script: scripts/{name}`
**Severity**: MINOR
**Source**: `validate_skill_comprehensive.py` — `validate_scripts_directory()`
**Root cause**: File exists but could not be read.
**Fix**: Check file permissions and encoding.

---

## Package Dependencies

### INFO: Package managers referenced

**Error message**: `Package managers referenced: {managers}`
**Severity**: INFO
**Source**: `validate_skill_comprehensive.py` — `validate_package_dependencies()`
**Root cause**: Informational message listing detected package managers (pip, npm, yarn, cargo, go, brew).
**Fix**: No fix needed. This confirms dependencies are documented.
