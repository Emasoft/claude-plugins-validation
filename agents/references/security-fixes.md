# Security Validation — Validation Issues and Fixes

Comprehensive remediation guide for all issues detected by `validate_security.py`.

## Overview of Security Checks

| Check | Severity | Function |
|-------|----------|----------|
| Command substitution `$(...)` or `` `...` `` | CRITICAL | `scan_for_injection()` |
| Pipe to shell (`\| sh`, `\| bash`, etc.) | CRITICAL | `scan_for_injection()` |
| `eval`/`exec` usage | CRITICAL | `scan_for_injection()` |
| Path traversal (`../`, `..\\`, absolute Unix/Windows paths) | CRITICAL | `scan_for_path_traversal()` |
| Secrets (API keys, private keys, tokens) | CRITICAL | `scan_for_secrets()` |
| Dangerous files (`.env`, `credentials.json`) | CRITICAL | `check_dangerous_files()` |
| World-writable scripts | CRITICAL | `check_script_permissions()` |
| World-writable Python scripts | CRITICAL | `check_script_permissions()` |
| Plugin path does not exist | CRITICAL | `validate_security()` |
| Plugin path is not a directory | CRITICAL | `validate_security()` |
| Hardcoded user paths (`/Users/xxx/`, `/home/xxx/`) | MAJOR | `scan_for_user_paths()` |
| Unquoted variable expansion | MAJOR | `scan_for_injection()` |
| Cannot check script permissions | MAJOR | `check_script_permissions()` |
| Shell script not executable | MINOR | `check_script_permissions()` |
| Shell script missing shebang | MINOR | `check_script_permissions()` |
| Cannot read file | MINOR | `scan_all_files()` |
| Non-standard shebang | INFO | `check_script_permissions()` |

## Table of Contents

- [1. Plugin Path Issues](#1-plugin-path-issues)
- [2. Injection Detection Issues](#2-injection-detection-issues)
- [3. Path Traversal Issues](#3-path-traversal-issues)
- [4. Secret Detection Issues](#4-secret-detection-issues)
- [5. Hardcoded User Path Issues](#5-hardcoded-user-path-issues)
- [6. Dangerous File Issues](#6-dangerous-file-issues)
- [7. Script Permission Issues](#7-script-permission-issues)
- [8. File Read Issues](#8-file-read-issues)

---

## 1. Plugin Path Issues

### [CRITICAL] Plugin path does not exist: {plugin_path}
**Source**: `validate_security.py` — `validate_security()`
**What it means**: The path passed to the security validator does not exist on disk.
**How to fix**:
1. Verify the path argument is correct.
2. Ensure the plugin directory exists: `ls -la path/to/plugin`.
3. If running in CI, check that the working directory is set correctly before invoking the validator.

### [CRITICAL] Plugin path is not a directory: {plugin_path}
**Source**: `validate_security.py` — `validate_security()`
**What it means**: The supplied path exists but is a file, not a directory.
**How to fix**:
1. The path should point to the plugin root folder, not to a file inside it.
2. Remove the filename from the path argument.

---

## 2. Injection Detection Issues

### [CRITICAL] Command substitution $(...) detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A non-shell, non-markdown, non-test file contains `$(...)` POSIX command substitution syntax. This can execute arbitrary commands and represents a severe injection risk in plugin files.
**How to fix**:
1. Remove or replace the `$(...)` construct.
2. If the value is dynamic, pass it as a parameter rather than constructing it with command substitution.
3. If the file is a shell script (`.sh`, `.bash`, `.zsh`, `.ksh`), command substitution is expected and this check is skipped automatically.
4. If the file is markdown documentation, it will also be skipped automatically.

### [CRITICAL] Command substitution `...` detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A non-shell, non-markdown, non-test file contains legacy backtick command substitution syntax. Equivalent risk to `$(...)`.
**How to fix**:
1. Remove the backtick command substitution.
2. Replace with a safe alternative or pass values as parameters.

### [CRITICAL] Pipe to sh detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A file contains `| sh` which pipes output directly to the `sh` shell. This executes arbitrary code from the pipe's source.
**How to fix**:
1. Never pipe untrusted or dynamic content to a shell.
2. Download scripts to a file, inspect them, then execute manually.
3. Use package managers or official install methods instead of pipe-to-shell patterns.

### [CRITICAL] Pipe to bash detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A file contains `| bash` which pipes output to bash. Same risk as pipe to `sh`.
**How to fix**: Same as "Pipe to sh detected" above — remove the pipe-to-shell pattern.

### [CRITICAL] Pipe to zsh detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A file contains `| zsh` which pipes output to the zsh shell.
**How to fix**: Same as "Pipe to sh detected" above.

### [CRITICAL] Pipe to ksh detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A file contains `| ksh` which pipes output to ksh.
**How to fix**: Same as "Pipe to sh detected" above.

### [CRITICAL] Pipe to source detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A file contains `| source` which pipes data into the shell's `source` built-in, executing it in the current shell context.
**How to fix**: Remove the pipe-to-source pattern. Source files from known, trusted local paths only.

### [CRITICAL] Pipe to dot (source) detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A file contains `| .` (pipe to dot, the POSIX equivalent of `source`).
**How to fix**: Same as "Pipe to source detected" above.

### [CRITICAL] eval command detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A shell `eval` command was detected. `eval` executes its string argument as shell code and is a common injection vector.
**How to fix**:
1. Remove the `eval` call entirely.
2. Use arrays or explicit variable handling instead of building commands dynamically.
3. If `eval` is genuinely required (rare), add it to the allowed list and document the reason.

### [CRITICAL] exec command detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A shell `exec` command was detected. While sometimes legitimate, `exec` replaces the current process and can be an injection risk.
**How to fix**:
1. Review whether `exec` is necessary.
2. If used to run a fixed, known binary, ensure the path is hardcoded and not user-controlled.

### [CRITICAL] Python eval() detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A Python `eval()` call was detected. `eval()` executes arbitrary Python code from a string.
**How to fix**:
1. Remove `eval()` and replace with safe alternatives:
   - For JSON: use `json.loads()`
   - For arithmetic: use `ast.literal_eval()`
   - For configuration: use a structured parser

### [CRITICAL] Python exec() detected: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A Python `exec()` call was detected. `exec()` executes arbitrary Python code.
**How to fix**:
1. Remove `exec()` and replace with safe, explicit logic.
2. If dynamic behavior is needed, use plugin hooks or configuration-driven dispatch instead.

### [CRITICAL] Python compile() with exec mode: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: `compile()` is called with `"exec"` mode, which is equivalent to `exec()`.
**How to fix**: Remove the `compile(..., 'exec')` call and replace with safe alternatives.

### [CRITICAL] JavaScript Function constructor (eval-like): {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: The `Function(...)` constructor was detected. It creates functions from strings, equivalent to `eval`.
**How to fix**:
1. Remove `Function(...)` and replace with explicit function definitions.
2. Use closures or configuration objects for dynamic behavior.

### [CRITICAL] JavaScript new Function() (eval-like): {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: `new Function(...)` creates a function from a string, equivalent to `eval`.
**How to fix**: Same as "JavaScript Function constructor" above.

### [MAJOR] Unquoted variable expansion may be unsafe: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A shell variable (`$VAR`) is used without surrounding quotes at the start of a command or after a pipe/semicolon. This can cause word splitting and glob expansion, leading to unexpected behavior or injection.
**How to fix**:
1. Always quote shell variables: `"$VAR"` instead of `$VAR`.
2. Use `"${VAR}"` for clarity with adjacent text.

### [MAJOR] Unquoted variable in comparison: {line}
**Source**: `validate_security.py` — `scan_for_injection()`
**What it means**: A shell variable is used unquoted inside a `[[ ... ]]` comparison expression.
**How to fix**:
1. Quote the variable in comparisons: `[[ "$VAR" == "value" ]]`.

---

## 3. Path Traversal Issues

### [CRITICAL] Path traversal ../ detected: {line}
**Source**: `validate_security.py` — `scan_for_path_traversal()`
**What it means**: A non-markdown, non-test, non-validator file contains `../` which can be used to escape the plugin directory and access parent directories.
**How to fix**:
1. Use `${CLAUDE_PLUGIN_ROOT}` as the base path and build paths from there, not by traversing upward.
2. Use absolute paths anchored to known safe roots.
3. Remove any `../` sequences from file paths.

### [CRITICAL] Path traversal ..\\ detected: {line}
**Source**: `validate_security.py` — `scan_for_path_traversal()`
**What it means**: A Windows-style path traversal sequence `..\` was detected.
**How to fix**: Same as `../` fix above. Avoid relative traversal in any direction.

### [CRITICAL] Absolute Unix system path detected: {line}
**Source**: `validate_security.py` — `scan_for_path_traversal()`
**What it means**: A hardcoded absolute path to a system directory (`/usr/`, `/etc/`, `/var/`, `/tmp/`, `/opt/`, `/bin/`, `/sbin/`, `/lib/`, `/root/`) was detected.
**How to fix**:
1. Use environment variables or configuration to locate system tools: `$(which tool)` is acceptable in shell scripts.
2. Use `${CLAUDE_PLUGIN_ROOT}` for plugin-relative paths.
3. For system binaries, use `command -v tool` or `which tool` instead of hardcoded paths.

### [CRITICAL] Windows absolute path detected: {line}
**Source**: `validate_security.py` — `scan_for_path_traversal()`
**What it means**: A Windows-style absolute path (`C:\`, `D:\`, etc.) was detected in a non-markdown file.
**How to fix**:
1. Use cross-platform path references or environment variables.
2. Plugin files should not assume a Windows filesystem layout.

---

## 4. Secret Detection Issues

### [CRITICAL] {secret_type} detected: {masked_line}
**Source**: `validate_security.py` — `scan_for_secrets()`
**What it means**: A pattern matching a secret (API key, private key, token, password, etc.) was detected in a plugin file. The actual value is masked in the report.
**How to fix**:
1. **Immediately revoke** the exposed credential at its issuing service.
2. Remove the secret from the file entirely.
3. Use environment variables or a secrets manager: reference secrets as `$MY_API_KEY` not inline.
4. Add the file to `.gitignore` if it must contain secrets (e.g., `.env`).
5. Use `git filter-branch` or BFG Repo Cleaner to purge the secret from git history if it was committed.
6. Common secret types detected include: AWS keys, private keys, API tokens, passwords, connection strings.

---

## 5. Hardcoded User Path Issues

### [MAJOR] Hardcoded user path detected (use ${CLAUDE_PLUGIN_ROOT} instead): {match}
**Source**: `validate_security.py` — `scan_for_user_paths()`
**What it means**: A non-markdown, non-test file contains a hardcoded path specific to a user's home directory (e.g., `/Users/alice/`, `/home/bob/`). This breaks portability across machines and users.
**How to fix**:
1. Replace the hardcoded path with `${CLAUDE_PLUGIN_ROOT}` for plugin-relative paths.
2. Use `$HOME` for home-directory-relative paths.
3. Use environment variables that the user can configure for external paths.
4. Example replacement:
   - Before: `/Users/alice/my-plugin/scripts/run.sh`
   - After: `${CLAUDE_PLUGIN_ROOT}/scripts/run.sh`

---

## 6. Dangerous File Issues

### [CRITICAL] Dangerous file detected: {rel_path}
**Source**: `validate_security.py` — `check_dangerous_files()`
**What it means**: A file known to contain sensitive information was found in the plugin directory. Common dangerous files include `.env`, `credentials.json`, `.netrc`, `.aws/credentials`, and similar files that store secrets.
**How to fix**:
1. Remove the dangerous file from the plugin directory: `git rm --cached {rel_path}`.
2. Add the file pattern to `.gitignore` to prevent future accidental commits.
3. Move sensitive configuration to environment variables or a secrets manager.
4. If the file was already committed, use `git filter-branch` or BFG Repo Cleaner to purge it from history.
5. Revoke any credentials that may have been exposed.

---

## 7. Script Permission Issues

### [CRITICAL] Script is world-writable: {rel_path}
**Source**: `validate_security.py` — `check_script_permissions()`
**What it means**: A shell script has world-writable permissions (`o+w`), meaning any user on the system can modify it. A malicious user could inject code that runs with the plugin's privileges.
**How to fix**:
1. Remove world-write permission: `chmod o-w {rel_path}`
2. Set recommended permissions: `chmod 755 {rel_path}` (owner: read/write/execute; group+others: read/execute)
3. Commit the permission change.

### [CRITICAL] Python script is world-writable: {rel_path}
**Source**: `validate_security.py` — `check_script_permissions()`
**What it means**: A Python script has world-writable permissions, carrying the same risk as world-writable shell scripts.
**How to fix**:
1. Remove world-write permission: `chmod o-w {rel_path}`
2. Set recommended permissions: `chmod 644 {rel_path}` for Python scripts (they are executed by the Python interpreter, not directly).

### [MAJOR] Cannot check script permissions: {rel_path} ({error})
**Source**: `validate_security.py` — `check_script_permissions()`
**What it means**: The validator could not read the file's permissions due to a `PermissionError` or `OSError`.
**How to fix**:
1. Check that the validator runs with sufficient privileges to `stat()` the file.
2. Verify the file exists and is accessible: `ls -la {rel_path}`.

### [MINOR] Shell script is not executable: {rel_path}
**Source**: `validate_security.py` — `check_script_permissions()`
**What it means**: A `.sh` script lacks the execute permission bit. If the script is referenced by a hook or command, it will fail to run.
**How to fix**:
1. Set execute permission: `chmod +x {rel_path}`
2. Commit the change: `git add {rel_path} && git commit -m "fix: make shell script executable"`

### [MINOR] Shell script missing shebang: {rel_path}
**Source**: `validate_security.py` — `check_script_permissions()`
**What it means**: A `.sh` file does not start with a shebang line (`#!`). Without a shebang, the OS does not know which interpreter to use.
**How to fix**:
1. Add a shebang as the very first line of the script.
2. Recommended shebangs:
   - `#!/usr/bin/env bash` — portable bash
   - `#!/bin/sh` — POSIX sh

### [INFO] Shell script has non-standard shebang: {first_line}
**Source**: `validate_security.py` — `check_script_permissions()`
**What it means**: The script's shebang does not reference `bash` or `sh`. This is informational — the script may intentionally use another interpreter (e.g., `python3`, `node`).
**How to fix**: No action required if the non-standard shebang is intentional. Verify the interpreter is available on target systems.

---

## 8. File Read Issues

### [MINOR] Cannot read file: {rel_path} ({error})
**Source**: `validate_security.py` — `scan_all_files()`
**What it means**: A file was found during the recursive scan but could not be read due to permissions or I/O errors. Security checks were skipped for this file.
**How to fix**:
1. Check file permissions: `ls -la {rel_path}`.
2. Ensure the file is readable by the current user.
3. Verify the file is not corrupted.
