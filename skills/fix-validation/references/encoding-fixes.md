# Encoding — Validation Issues and Fixes

## Table of Contents

- [1. Plugin Path Issues](#1-plugin-path-issues)
- [2. UTF-8 Encoding Issues](#2-utf-8-encoding-issues)
- [3. BOM (Byte Order Mark) Issues](#3-bom-byte-order-mark-issues)
- [4. JSON Unicode Issues](#4-json-unicode-issues)
- [5. Escape Sequence Issues](#5-escape-sequence-issues)
- [6. Line Ending Issues — Source Files](#6-line-ending-issues--source-files)
- [7. Line Ending Issues — Shell Scripts](#7-line-ending-issues--shell-scripts)
- [8. Line Ending Issues — Batch Scripts](#8-line-ending-issues--batch-scripts)
- [9. File Read Issues](#9-file-read-issues)

Comprehensive remediation guide for all issues detected by `validate_encoding.py`.

## Checklist

- [ ] Identify the offending file and byte offset from the finding
- [ ] Match to a numbered section below
- [ ] Open the file and inspect around the offset
- [ ] Apply the encoding fix (strip BOM, replace smart quotes, normalize line endings, etc.)
- [ ] Re-validate

## Overview

The encoding validator scans all text files in the plugin directory and checks seven rules:

1. UTF-8 encoding required (all text files)
2. No BOM (Byte Order Mark) detection
3. Proper Unicode handling in JSON files
4. Special characters properly escaped (no raw control characters)
5. LF line endings for source files (`.py`, `.sh`, `.md`, `.json`, etc.)
6. Shell scripts (`.sh`, `.bash`, `.zsh`, `.ksh`): LF endings REQUIRED (CRLF breaks execution)
7. Batch scripts (`.bat`, `.cmd`, `.ps1`): CRLF allowed

**Text files checked**: `.py`, `.sh`, `.bash`, `.zsh`, `.md`, `.json`, `.yaml`, `.yml`, `.toml`, `.txt`, `.js`, `.ts`, `.tsx`, `.jsx`, `.html`, `.htm`, `.css`, `.xml`, `.ini`, `.cfg`, `.conf`, and similar.

**Binary files and directories** (`.png`, `.jpg`, `.zip`, `.pyc`, `__pycache__`, `.git`, `node_modules`, etc.) are automatically skipped.

## 1. Plugin Path Issues

### [CRITICAL] Plugin path does not exist: {plugin_path}
**Source**: `validate_encoding.py` — `validate_encoding()`
**What it means**: The path passed to the validator does not exist on the filesystem.
**How to fix**:
1. Verify the path is correct: `ls /path/to/plugin/`
2. Pass the correct absolute path to the validator.

---

### [CRITICAL] Plugin path is not a directory: {plugin_path}
**Source**: `validate_encoding.py` — `validate_encoding()`
**What it means**: The path exists but points to a file, not a directory.
**How to fix**:
1. Pass the plugin root directory path, not a file path.

---

## 2. UTF-8 Encoding Issues

### [CRITICAL] File is not valid UTF-8: {file_path} (error at byte {N}: {reason})
**Source**: `validate_encoding.py` — `check_utf8_encoding()`
**What it means**: The file contains bytes that are not valid UTF-8. This means the file was saved in a different encoding (e.g., Latin-1, Windows-1252, UTF-16) or is corrupted.
**How to fix**:
1. Identify the current encoding:
   ```bash
   file -i path/to/file.py
   # or:
   python3 -c "import chardet; print(chardet.detect(open('path/to/file', 'rb').read()))"
   ```
2. Convert to UTF-8:
   ```bash
   # Using iconv (replace LATIN1 with detected encoding):
   iconv -f LATIN1 -t UTF-8 file.py > file_utf8.py && mv file_utf8.py file.py
   ```
3. In your editor, set the file encoding to UTF-8 and re-save.
4. Configure your editor to always save as UTF-8:
   - VS Code: `"files.encoding": "utf8"` in settings.json
   - Add a `.editorconfig` with `charset = utf-8`

---

## 3. BOM (Byte Order Mark) Issues

### [MAJOR] File has UTF-8 BOM (should be UTF-8 without BOM): {file_path}
**Source**: `validate_encoding.py` — `check_bom()`
**What it means**: The file starts with the UTF-8 BOM bytes (`0xEF 0xBB 0xBF`). While technically valid, UTF-8 BOM can cause issues with many Unix tools, shell scripts, and JSON parsers.
**How to fix**:
1. Remove the BOM using sed:
   ```bash
   sed -i 's/^\xEF\xBB\xBF//' path/to/file
   ```
2. Or using Python:
   ```bash
   python3 -c "
   with open('path/to/file', 'rb') as f: content = f.read()
   if content.startswith(b'\xef\xbb\xbf'):
       content = content[3:]
   with open('path/to/file', 'wb') as f: f.write(content)
   "
   ```
3. Configure your editor to save UTF-8 without BOM:
   - VS Code: `"files.encoding": "utf8"` (NOT `"utf8bom"`)
   - Add `.editorconfig`: `charset = utf-8` (not `utf-8-bom`)

---

### [CRITICAL] File has UTF-16 LE BOM (must use UTF-8): {file_path}
**Source**: `validate_encoding.py` — `check_bom()`
**What it means**: The file is encoded in UTF-16 Little Endian. Claude Code plugins require UTF-8.
**How to fix**:
1. Convert to UTF-8:
   ```bash
   iconv -f UTF-16LE -t UTF-8 file.txt > file_utf8.txt && mv file_utf8.txt file.txt
   ```
2. Or open the file in an editor, change encoding to UTF-8, and re-save.

---

### [CRITICAL] File has UTF-16 BE BOM (must use UTF-8): {file_path}
**Source**: `validate_encoding.py` — `check_bom()`
**What it means**: The file is encoded in UTF-16 Big Endian. Must be converted to UTF-8.
**How to fix**:
1. Convert to UTF-8:
   ```bash
   iconv -f UTF-16BE -t UTF-8 file.txt > file_utf8.txt && mv file_utf8.txt file.txt
   ```

---

### [CRITICAL] File has UTF-32 LE BOM (must use UTF-8): {file_path}
**Source**: `validate_encoding.py` — `check_bom()`
**What it means**: The file is encoded in UTF-32 Little Endian. Must be converted to UTF-8.
**How to fix**:
1. Convert to UTF-8:
   ```bash
   iconv -f UTF-32LE -t UTF-8 file.txt > file_utf8.txt && mv file_utf8.txt file.txt
   ```

---

### [CRITICAL] File has UTF-32 BE BOM (must use UTF-8): {file_path}
**Source**: `validate_encoding.py` — `check_bom()`
**What it means**: The file is encoded in UTF-32 Big Endian. Must be converted to UTF-8.
**How to fix**:
1. Convert to UTF-8:
   ```bash
   iconv -f UTF-32BE -t UTF-8 file.txt > file_utf8.txt && mv file_utf8.txt file.txt
   ```

---

## 4. JSON Unicode Issues

### [MAJOR] JSON Unicode error in {file_path}: {error}
**Source**: `validate_encoding.py` — `check_json_unicode()`
**What it means**: A `.json` file failed to parse due to a Unicode-related error. This indicates invalid Unicode escape sequences or raw non-ASCII bytes inside a JSON string.
**How to fix**:
1. Validate the JSON file:
   ```bash
   python3 -m json.tool path/to/file.json
   ```
2. Look for raw non-ASCII characters inside string values and replace with proper Unicode escapes:
   - Replace literal `©` with `\u00a9`
   - Replace literal `€` with `\u20ac`
3. Or ensure the file is valid UTF-8 and use actual Unicode characters (JSON supports both forms).
4. Common causes:
   - Pasting from Word/Office documents that use special quotes or dashes
   - Mixed encoding in the file
   - Malformed `\uXXXX` escape sequences

---

## 5. Escape Sequence Issues

### [MINOR] File contains raw control characters ({char_codes}): {file_path}
**Source**: `validate_encoding.py` — `check_escape_sequences()`
**What it means**: The file contains raw control characters (ASCII codes 0x00–0x08, 0x0B, 0x0C, 0x0E–0x1F, 0x7F) that are not newlines or tabs. These are invisible characters that can cause rendering problems, unexpected behavior, or security issues.
**How to fix**:
1. Find and view control characters:
   ```bash
   cat -A path/to/file | grep -n '\^'
   ```
2. Remove control characters using Python:
   ```bash
   python3 -c "
   import re
   with open('path/to/file', 'r', encoding='utf-8') as f:
       content = f.read()
   # Remove control chars except newline (0x0A) and tab (0x09)
   cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', content)
   with open('path/to/file', 'w', encoding='utf-8') as f:
       f.write(cleaned)
   "
   ```
3. Common sources: copy-paste from PDFs or rich-text editors, terminal escape sequences accidentally saved to files.

---

## 6. Line Ending Issues — Source Files

### [MINOR] Source file has CRLF line endings (should use LF): {file_path}
**Source**: `validate_encoding.py` — `check_line_endings()`
**What it means**: A source file (`.py`, `.md`, `.json`, `.yaml`, `.js`, `.ts`, etc.) uses Windows-style `\r\n` line endings instead of Unix-style `\n`.
**How to fix**:
1. Convert CRLF to LF:
   ```bash
   sed -i 's/\r//' path/to/file
   ```
2. Or using Python:
   ```bash
   python3 -c "
   with open('path/to/file', 'rb') as f: content = f.read()
   content = content.replace(b'\r\n', b'\n')
   with open('path/to/file', 'wb') as f: f.write(content)
   "
   ```
3. Prevent future CRLF issues:
   - Add `.gitattributes`:
     ```
     * text=auto eol=lf
     *.py eol=lf
     *.md eol=lf
     *.json eol=lf
     ```
   - Add `.editorconfig`:
     ```ini
     [*]
     end_of_line = lf
     ```

---

### [MINOR] Source file has old Mac-style CR line endings: {file_path}
**Source**: `validate_encoding.py` — `check_line_endings()`
**What it means**: A source file uses old Mac OS 9-style `\r` (carriage return only) line endings instead of `\n`.
**How to fix**:
1. Convert CR to LF:
   ```bash
   sed -i 's/\r/\n/g' path/to/file
   ```
2. Or using Python:
   ```bash
   python3 -c "
   with open('path/to/file', 'rb') as f: content = f.read()
   content = content.replace(b'\r', b'\n')
   with open('path/to/file', 'wb') as f: f.write(content)
   "
   ```

---

### [MINOR] Source file has mixed line endings: {file_path}
**Source**: `validate_encoding.py` — `check_line_endings()`
**What it means**: A source file has both `\r\n` and standalone `\n` line endings mixed within the same file.
**How to fix**:
1. Normalize all line endings to LF:
   ```bash
   python3 -c "
   with open('path/to/file', 'rb') as f: content = f.read()
   # Normalize: first collapse CRLF to LF, then CR to LF
   content = content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
   with open('path/to/file', 'wb') as f: f.write(content)
   "
   ```
2. Add `.gitattributes` to prevent future mixed line endings (see CRLF fix above).

---

## 7. Line Ending Issues — Shell Scripts

### [CRITICAL] Shell script has CRLF line endings (will break execution): {file_path}
**Source**: `validate_encoding.py` — `check_line_endings()`
**What it means**: A shell script (`.sh`, `.bash`, `.zsh`, `.ksh`) has Windows CRLF line endings. Shell scripts with CRLF will fail at runtime — the `\r` is treated as part of the command name, causing errors like `command not found: script.sh^M`.
**How to fix**:
1. Convert CRLF to LF immediately:
   ```bash
   sed -i 's/\r//' path/to/script.sh
   ```
2. Verify:
   ```bash
   file path/to/script.sh  # Should show: ASCII text
   # (NOT: ASCII text, with CRLF line terminators)
   ```
3. Add `.gitattributes` to prevent recurrence:
   ```
   *.sh eol=lf
   *.bash eol=lf
   *.zsh eol=lf
   ```

---

### [CRITICAL] Shell script has CR-only line endings (will break execution): {file_path}
**Source**: `validate_encoding.py` — `check_line_endings()`
**What it means**: A shell script uses old Mac OS 9-style `\r`-only line endings. Like CRLF, these break shell execution.
**How to fix**:
1. Convert CR to LF:
   ```bash
   sed -i 's/\r/\n/g' path/to/script.sh
   ```

---

### [MAJOR] Shell script has mixed line endings: {file_path}
**Source**: `validate_encoding.py` — `check_line_endings()`
**What it means**: A shell script contains a mix of `\r\n` and `\n` line endings. This is likely to cause execution errors at runtime.
**How to fix**:
1. Normalize to LF:
   ```bash
   python3 -c "
   with open('path/to/script.sh', 'rb') as f: content = f.read()
   content = content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
   with open('path/to/script.sh', 'wb') as f: f.write(content)
   "
   ```

---

## 8. Line Ending Issues — Batch Scripts

### [MINOR] Batch script has old Mac-style CR line endings: {file_path}
**Source**: `validate_encoding.py` — `check_line_endings()`
**What it means**: A Windows batch script (`.bat`, `.cmd`, `.ps1`) uses old Mac-style CR-only line endings. Batch scripts can use CRLF (which is acceptable) but CR-only is not supported.
**How to fix**:
1. Convert to CRLF (the Windows standard for batch files):
   ```bash
   python3 -c "
   with open('path/to/script.bat', 'rb') as f: content = f.read()
   content = content.replace(b'\r', b'\n').replace(b'\n', b'\r\n')
   with open('path/to/script.bat', 'wb') as f: f.write(content)
   "
   ```

---

## 9. File Read Issues

### [MINOR] Cannot read file: {file_path} ({error})
**Source**: `validate_encoding.py` — `validate_file()`
**What it means**: A text file could not be opened for reading. This is typically a permissions issue.
**How to fix**:
1. Check file permissions:
   ```bash
   ls -la path/to/file
   ```
2. Fix permissions:
   ```bash
   chmod 644 path/to/file
   ```
3. If the file is owned by another user, use `sudo chmod` or ask the owner to fix permissions.
