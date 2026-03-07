# Documentation Validation — Validation Issues and Fixes

Comprehensive remediation guide for all issues detected by `validate_documentation.py`.

## Table of Contents

- [1. README Existence Issues](#1-readme-existence-issues)
- [2. README Content Section Issues](#2-readme-content-section-issues)
- [3. Internal Link Issues](#3-internal-link-issues)
- [4. CHANGELOG Issues](#4-changelog-issues)
- [5. Heading Hierarchy Issues](#5-heading-hierarchy-issues)
- [6. Code Block Issues](#6-code-block-issues)
- [7. List Formatting Issues](#7-list-formatting-issues)
- [8. Table Structure Issues](#8-table-structure-issues)
- [9. Image Reference Issues](#9-image-reference-issues)

---

## 1. README Existence Issues

### [CRITICAL] README.md is missing at plugin root
**Source**: `validate_documentation.py` — `validate_readme_exists()`
**What it means**: No `README.md` file was found at the plugin's root directory. This is a required file for every plugin.
**How to fix**:
1. Create `README.md` in the plugin root directory.
2. At minimum include: a title (`# Plugin Name`), a description paragraph, an `## Installation` section, and a `## Usage` section.
3. Example minimal structure:
   ```markdown
   # My Plugin

   A short description of what this plugin does.

   ## Installation

   Copy this plugin to your Claude plugins directory.

   ## Usage

   Describe how to use the plugin here.
   ```

### [MINOR] README.md exists but uses lowercase (readme.md) - consider using README.md
**Source**: `validate_documentation.py` — `validate_readme_exists()`
**What it means**: A `readme.md` file exists (lowercase) but the standard convention is `README.md` (uppercase). This may cause issues on case-sensitive filesystems and with some tooling.
**How to fix**:
1. Rename the file: `git mv readme.md README.md`
2. Commit the rename: `git commit -m "fix: rename readme.md to README.md"`

---

## 2. README Content Section Issues

### [MAJOR] README missing installation section (## Installation, ## Getting Started, ## Setup, or ## Quick Start)
**Source**: `validate_documentation.py` — `validate_installation_section()`
**What it means**: The README does not contain any heading that serves as an installation guide. Users cannot determine how to install or set up the plugin.
**How to fix**:
1. Add one of the following sections to your README:
   - `## Installation`
   - `## Getting Started`
   - `## Setup`
   - `## Quick Start`
2. Include step-by-step instructions for installing the plugin.

### [MAJOR] README missing usage section (## Usage, ## Examples, or ## How to Use)
**Source**: `validate_documentation.py` — `validate_usage_section()`
**What it means**: The README does not contain any heading that shows how to use the plugin.
**How to fix**:
1. Add one of the following sections to your README:
   - `## Usage`
   - `## Examples`
   - `## How to Use`
2. Include concrete examples showing what commands/agents/skills are available and how to invoke them.

### [MAJOR] README missing title (# heading)
**Source**: `validate_documentation.py` — `validate_description_section()`
**What it means**: The README has no top-level `#` heading (h1 title).
**How to fix**:
1. Add a title as the very first line of the README: `# Your Plugin Name`

### [MAJOR] README missing description section after title (add content between # Title and first ## section)
**Source**: `validate_documentation.py` — `validate_description_section()`
**What it means**: The README has a title and sections but no description paragraph between the title and the first `##` section. Users cannot quickly understand what the plugin does.
**How to fix**:
1. Add at least one paragraph (20+ characters) immediately after the `# Title` heading and before the first `##` section.
2. Example:
   ```markdown
   # My Plugin

   This plugin provides X, Y, and Z capabilities for Claude Code users.

   ## Installation
   ```

### [MAJOR] Broken internal link: [{link_text}]({link_target})
**Source**: `validate_documentation.py` — `validate_broken_links()`
**What it means**: A markdown link in a documentation file points to a local file that does not exist. The link will be broken for users.
**How to fix**:
1. Locate the file containing the broken link.
2. Check that the target file exists at the referenced path.
3. Fix the path:
   - Links are resolved relative to the markdown file's own directory first, then relative to the plugin root.
   - Use relative paths: `[text](../other-file.md)` or `[text](subfolder/file.md)`.
4. If the target file is missing, create it or remove the broken link.

### [MAJOR] Missing image: ![{alt_text}]({img_path})
**Source**: `validate_documentation.py` — `validate_image_references()`
**What it means**: A markdown image reference points to a local image file that does not exist.
**How to fix**:
1. Add the missing image file at the referenced path.
2. Or update the image path to point to the correct existing image.
3. Image paths are resolved relative to the markdown file's directory first, then from the plugin root.
4. External image URLs (`http://`, `https://`, `data:`) are exempt from this check.

---

## 3. Internal Link Issues

*(See [MAJOR] Broken internal link entry above in Section 2.)*

---

## 4. CHANGELOG Issues

### [MINOR] CHANGELOG.md is recommended for tracking version history
**Source**: `validate_documentation.py` — `validate_changelog_exists()`
**What it means**: No changelog file was found. A changelog helps users understand what changed between versions. Accepted filenames: `CHANGELOG.md`, `changelog.md`, `CHANGES.md`, `HISTORY.md`.
**How to fix**:
1. Create `CHANGELOG.md` in the plugin root.
2. Follow the [Keep a Changelog](https://keepachangelog.com/) format:
   ```markdown
   # Changelog

   ## [Unreleased]

   ## [1.0.0] - 2024-01-01
   ### Added
   - Initial release
   ```

---

## 5. Heading Hierarchy Issues

### [MINOR] Heading hierarchy skip: level {current_level} to level {new_level} (line {line_num})
**Source**: `validate_documentation.py` — `validate_heading_hierarchy()`
**What it means**: The README jumps heading levels (e.g., goes from `##` directly to `####`), skipping `###`. This creates an inconsistent document structure that may confuse readers and tools.
**How to fix**:
1. Add the missing intermediate heading level between the two headings.
2. Example fix:
   ```markdown
   ## Section        (level 2)
   ### Subsection    (level 3)  ← add this
   #### Detail       (level 4)
   ```
3. Or restructure the headings so no levels are skipped.

---

## 6. Code Block Issues

### [MAJOR] Unclosed code block starting at line {line_num}
**Source**: `validate_documentation.py` — `validate_code_block_closed()`
**What it means**: A code fence (` ``` `) was opened at the given line but never closed with a matching ` ``` ` fence. The rest of the document will be rendered as a code block.
**How to fix**:
1. Locate the opening ` ``` ` at line `{line_num}` in `README.md`.
2. Add a closing ` ``` ` at the end of the code block.
3. Use a markdown linter or preview to visually confirm the fix.

### [MINOR] Code block at line {line_num} missing language tag
**Source**: `validate_documentation.py` — `validate_code_block_language_tags()`
**What it means**: A code fence (` ``` `) does not specify a language for syntax highlighting. This reduces readability and prevents syntax highlighting in editors and on GitHub.
**How to fix**:
1. Add a language tag immediately after the opening fence:
   - ` ```bash ` for shell commands
   - ` ```python ` for Python code
   - ` ```json ` for JSON
   - ` ```markdown ` for Markdown examples
   - ` ```text ` for plain text output
2. Example:
   ````markdown
   ```bash
   echo "Hello World"
   ```
   ````

---

## 7. List Formatting Issues

### [MINOR] Inconsistent list markers used: {markers} (prefer using one consistently)
**Source**: `validate_documentation.py` — `validate_list_formatting()`
**What it means**: The README uses multiple different unordered list markers (`-`, `*`, `+`) in the same document. Consistent formatting improves readability.
**How to fix**:
1. Choose one list marker style (recommended: `-`) and use it throughout the document.
2. Use find-and-replace to standardize all list markers.
3. Example: change all `* item` and `+ item` lines to `- item`.

---

## 8. Table Structure Issues

### [MINOR] Table separator row has {sep_cols} columns, header has {header_cols} (line {line_num})
**Source**: `validate_documentation.py` — `validate_table_structure()`
**What it means**: The separator row (the `|---|---|` line) in a markdown table has a different number of columns than the header row. This renders incorrectly in many markdown renderers.
**How to fix**:
1. Count the columns in the header row.
2. Ensure the separator row has the same number of `---` cells.
3. Example of correct table:
   ```markdown
   | Col A | Col B | Col C |
   |-------|-------|-------|
   | data  | data  | data  |
   ```

### [MINOR] Table row has {row_cols} columns, header has {header_cols} (line {line_num})
**Source**: `validate_documentation.py` — `validate_table_structure()`
**What it means**: A data row in a markdown table has a different number of columns than the header row. This causes misaligned or broken table rendering.
**How to fix**:
1. Count the columns in the header row.
2. Add or remove `|`-separated cells in the data row to match the column count.
3. If a cell should be empty, use `|  |` (space between pipes).

---

## 9. Image Reference Issues

*(See [MAJOR] Missing image entry above in Section 2.)*
