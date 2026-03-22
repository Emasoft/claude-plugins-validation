#!/usr/bin/env python3
"""
Claude Plugins Validation - Security Module

Performs comprehensive security validation across the entire plugin.
This module implements security checks that must run BEFORE any allowlists.

Security Checks Implemented:
1. Injection Detection (command substitution, variable expansion, eval patterns)
2. Path Traversal Blocking (../, absolute paths, Windows paths)
3. Secret Detection (AWS keys, private keys, API tokens)
4. Hardcoded User Path Detection (/Users/xxx/, /home/xxx/)
5. Dangerous File Detection (.env, credentials.json, etc.)
6. Script Permission Check (executable, shebang, world-writable)
7. Plugin-Wide Recursive Scan
8. Prompt Injection Detection (AI-specific: malicious instructions in skills/agents)
9. Data Exfiltration Detection (curl/wget/fetch to external URLs in hooks/scripts)
10. Permission Escalation Detection (dangerouslySkipPermissions, broad allowedTools)
11. Supply Chain Attack Detection (curl|sh, pip install from URL, npm from non-registry)
12. Credential Harvesting Detection (~/.ssh/, ~/.aws/, ~/.gitconfig reads)
13. Hook Abuse Detection (PreToolUse denying all, PostToolUse sending externally)
14. MCP Server Abuse Detection (non-localhost servers flagged as warning)
15. Sandbox Escape Detection (--no-verify, git config modification, hook bypass)
16. cc-audit External Scanner (100+ rules via npx, optional)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from cpv_validation_common import (
    DANGEROUS_FILES,
    EXAMPLE_USERNAMES,
    KNOWN_EXAMPLE_SECRETS,
    SECRET_PATTERNS,
    USER_PATH_PATTERNS,
    ValidationReport,
    get_gitignore_filter,
    is_binary_file,
    print_report_summary,
    print_results_by_level,
    save_report_and_print_summary,
)

# =============================================================================
# Injection Detection Patterns
# =============================================================================

# Command substitution patterns - MUST be checked BEFORE any allowlist
COMMAND_SUBSTITUTION_PATTERNS = [
    # $(command) - POSIX command substitution
    (re.compile(r"\$\([^)]+\)"), "Command substitution $(...) detected"),
    # `command` - Legacy backtick command substitution
    (re.compile(r"`[^`]+`"), "Command substitution `...` detected"),
]

# Variable expansion in unsafe contexts (unquoted)
# This pattern detects $VAR without surrounding quotes that could be injection vectors
UNSAFE_VARIABLE_PATTERNS = [
    # Unquoted variable at start of command or after pipe/semicolon
    (
        re.compile(r"(?:^|[|;&])\s*\$[A-Za-z_][A-Za-z0-9_]*(?:\s|$|[|;&])"),
        "Unquoted variable expansion may be unsafe",
    ),
    # Variable in arithmetic context without braces
    (
        re.compile(r"\[\[\s*\$[A-Za-z_][A-Za-z0-9_]*\s*(?:==|!=|<|>|-eq|-ne|-lt|-gt)"),
        "Unquoted variable in comparison",
    ),
]

# Pipe to shell patterns - extremely dangerous
PIPE_TO_SHELL_PATTERNS = [
    (re.compile(r"\|\s*sh\b"), "Pipe to sh detected"),
    (re.compile(r"\|\s*bash\b"), "Pipe to bash detected"),
    (re.compile(r"\|\s*zsh\b"), "Pipe to zsh detected"),
    (re.compile(r"\|\s*ksh\b"), "Pipe to ksh detected"),
    (re.compile(r"\|\s*source\b"), "Pipe to source detected"),
    (re.compile(r"\|\s*\.\s"), "Pipe to dot (source) detected"),
]

# Eval patterns - code execution risks
EVAL_PATTERNS = [
    (re.compile(r"\beval\s+"), "eval command detected"),
    (re.compile(r"\bexec\s+"), "exec command detected"),
    # Python-specific
    (re.compile(r"\beval\s*\("), "Python eval() detected"),
    (re.compile(r"\bexec\s*\("), "Python exec() detected"),
    (re.compile(r"\bcompile\s*\([^)]*\bexec\b"), "Python compile() with exec mode"),
    # JavaScript-specific
    (re.compile(r"\bFunction\s*\("), "JavaScript Function constructor (eval-like)"),
    (re.compile(r"\bnew\s+Function\s*\("), "JavaScript new Function() (eval-like)"),
]

# =============================================================================
# Path Traversal Patterns
# =============================================================================

PATH_TRAVERSAL_PATTERNS = [
    # Directory traversal
    (re.compile(r"\.\./"), "Path traversal ../ detected"),
    (re.compile(r"\.\.\\"), "Path traversal ..\\ detected"),
    # Absolute paths (except environment variable placeholders)
    (
        re.compile(r"(?<!\$\{CLAUDE_PLUGIN_ROOT\})(?<!\$\{CLAUDE_PLUGIN_DATA\})(?<!\$\{CLAUDE_PROJECT_DIR\})(?<![\w$\{])/(?:usr|etc|var|tmp|opt|bin|sbin|lib|root)/"),
        "Absolute Unix system path detected",
    ),
    # Windows absolute paths
    (re.compile(r"[A-Za-z]:\\"), "Windows absolute path detected"),
]

# =============================================================================
# AI-Specific Threat Patterns (Checks 8-16)
# =============================================================================

# Prompt injection patterns — malicious instructions in skills/agents/commands
PROMPT_INJECTION_PATTERNS = [
    (re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE), "Prompt injection: ignore previous instructions"),
    (re.compile(r"you\s+are\s+now\s+(?:a|an)\b", re.IGNORECASE), "Prompt injection: identity override ('you are now')"),
    (re.compile(r"(?:forget|disregard|override)\s+(?:all\s+)?(?:your|the)\s+(?:instructions?|rules?|guidelines?|constraints?)", re.IGNORECASE), "Prompt injection: instruction override"),
    (re.compile(r"do\s+not\s+follow\s+(?:any|the)\s+(?:previous|above|prior)\s+(?:instructions?|rules?)", re.IGNORECASE), "Prompt injection: instruction negation"),
    (re.compile(r"(?:system|hidden)\s*(?:prompt|instruction|message)\s*:", re.IGNORECASE), "Prompt injection: fake system prompt marker"),
    (re.compile(r"<\s*(?:system|instructions?|context)\s*>", re.IGNORECASE), "Prompt injection: fake XML system tag"),
    (re.compile(r"\[INST\]|\[/INST\]|\[SYSTEM\]", re.IGNORECASE), "Prompt injection: fake instruction delimiters"),
    (re.compile(r"IMPORTANT:\s*(?:ignore|override|forget|disregard)", re.IGNORECASE), "Prompt injection: IMPORTANT override"),
]

# Data exfiltration patterns — sending data to external servers
DATA_EXFILTRATION_PATTERNS = [
    (re.compile(r"curl\s+.*-[dX]\s+.*https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE), "Data exfiltration: curl POST/PUT to external URL"),
    (re.compile(r"wget\s+.*--post-data.*https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE), "Data exfiltration: wget POST to external URL"),
    (re.compile(r"fetch\s*\(\s*['\"]https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE), "Data exfiltration: fetch() to external URL"),
    (re.compile(r"requests?\.\s*(?:post|put|patch)\s*\(\s*['\"]https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE), "Data exfiltration: Python requests POST to external URL"),
    (re.compile(r"urllib\.\s*request\.\s*urlopen.*https?://(?!localhost|127\.0\.0\.1)"), "Data exfiltration: urllib to external URL"),
]

# Supply chain attack patterns — downloading and executing code
SUPPLY_CHAIN_PATTERNS = [
    (re.compile(r"curl\s+.*\|\s*(?:sh|bash|zsh|python|python3|node)\b"), "Supply chain: curl piped to interpreter"),
    (re.compile(r"wget\s+.*\|\s*(?:sh|bash|zsh|python|python3|node)\b"), "Supply chain: wget piped to interpreter"),
    (re.compile(r"pip\s+install\s+.*(?:https?://|git\+|--index-url\s+(?!https://pypi))"), "Supply chain: pip install from non-PyPI source"),
    (re.compile(r"npm\s+install\s+.*(?:https?://|git\+|--registry\s+(?!https://registry\.npmjs))"), "Supply chain: npm install from non-registry source"),
    (re.compile(r"curl\s+.*-[oO]\s+.*&&\s*(?:chmod|sh|bash|python|node)\b"), "Supply chain: curl download then execute"),
    (re.compile(r"wget\s+.*-[oO]\s+.*&&\s*(?:chmod|sh|bash|python|node)\b"), "Supply chain: wget download then execute"),
]

# Credential harvesting patterns — reading sensitive credential files
# Note: ~/.claude/ is EXCLUDED (legitimate for plugins)
CREDENTIAL_HARVEST_PATTERNS = [
    (re.compile(r"~/\.ssh/|/\.ssh/|SSH_KEY|id_rsa|id_ed25519"), "Credential access: SSH key file reference"),
    (re.compile(r"~/\.aws/|/\.aws/|AWS_SECRET|aws_secret_access_key", re.IGNORECASE), "Credential access: AWS credentials reference"),
    (re.compile(r"~/\.gitconfig|/\.gitconfig|GIT_TOKEN|GITHUB_TOKEN", re.IGNORECASE), "Credential access: Git credentials reference"),
    (re.compile(r"~/\.npmrc|/\.npmrc|NPM_TOKEN|npm_token", re.IGNORECASE), "Credential access: npm credentials reference"),
    (re.compile(r"~/\.docker/|/\.docker/config\.json|DOCKER_PASSWORD", re.IGNORECASE), "Credential access: Docker credentials reference"),
    (re.compile(r"~/\.kube/|/\.kube/config|KUBECONFIG", re.IGNORECASE), "Credential access: Kubernetes config reference"),
    (re.compile(r"~/\.gnupg/|/\.gnupg/|GPG_PASSPHRASE", re.IGNORECASE), "Credential access: GPG keyring reference"),
    (re.compile(r"(?:keychain|keyring|credential.?store|password.?store)", re.IGNORECASE), "Credential access: system keystore reference"),
]

# Sandbox escape patterns — bypassing safety controls
SANDBOX_ESCAPE_PATTERNS = [
    (re.compile(r"--no-verify\b"), "Sandbox escape: --no-verify bypasses git hooks"),
    (re.compile(r"git\s+config\s+.*(?:core\.hooksPath|core\.autocrlf|safe\.directory)"), "Sandbox escape: git config modification"),
    (re.compile(r"--dangerously-skip-permissions\b"), "Permission escalation: dangerouslySkipPermissions flag"),
    (re.compile(r"chmod\s+(?:777|a\+rwx)\b"), "Sandbox escape: chmod 777 (world-writable)"),
    (re.compile(r"(?:disable|bypass|skip)\s*(?:all\s+)?(?:hooks?|guard|safety|protection|sandbox)", re.IGNORECASE), "Sandbox escape: safety bypass language"),
]

# Agent impersonation — removed. Too many false positives: legitimate plugins
# contain "claude" in names (e.g. claude-plugins-validation, claude-plugin).
# This check would need semantic analysis to distinguish malicious impersonation
# from legitimate naming, which is beyond what a pattern-based scanner can do.

# =============================================================================
# Security Validation Functions
# =============================================================================


def is_validator_script(file_path: str) -> bool:
    """Check if file is a validator script that contains intentional pattern definitions.

    Validator scripts contain regex patterns, example shebangs, and documentation
    that would trigger false positives. These are safe to skip for certain checks.
    """
    file_lower = file_path.lower()
    # Validator scripts that contain intentional pattern definitions
    return ("validate_" in file_lower and file_lower.endswith(".py")) or "cpv_validation_common" in file_lower


def is_shell_like_file(file_path: str) -> bool:
    """Recognize files where shell syntax (command substitution, pipes) is expected.

    Covers:
    - Shell script extensions (.sh, .bash, .zsh, .ksh)
    - Git hooks in git-hooks/ or .git/hooks/ directories (extensionless scripts)
    - GitHub Actions YAML (.yml/.yaml inside .github/workflows/)
    """
    file_lower = file_path.lower()
    # Normalize backslashes for consistent matching
    file_normalized = file_lower.replace("\\", "/")
    # Standard shell extensions
    if file_lower.endswith((".sh", ".bash", ".zsh", ".ksh")):
        return True
    # Git hook scripts (extensionless files under hook directories)
    # Handles both absolute (/git-hooks/) and relative (git-hooks/) paths
    if "/git-hooks/" in file_normalized or file_normalized.startswith("git-hooks/"):
        return True
    if "/.git/hooks/" in file_normalized or file_normalized.startswith(".git/hooks/"):
        return True
    # GitHub Actions workflow YAML files contain shell commands in run: blocks
    # Also match template workflow directories (templates/github-workflows/)
    if file_lower.endswith((".yml", ".yaml")):
        if "/workflows/" in file_normalized or file_normalized.startswith(".github/workflows/"):
            return True
        if "github-workflows/" in file_normalized:
            return True
    return False


def _line_is_string_assignment(line: str) -> bool:
    """Detect Python multi-line string assignments like: VAR = '''#!/usr/bin/env python3.

    Matches patterns where an identifier is assigned a triple-quoted string
    containing content that looks like a shell shebang or path.
    """
    stripped = line.strip()
    # Match: IDENTIFIER = ''' or IDENTIFIER = \"\"\" (with optional space variations)
    return bool(re.match(r"[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:'''|\"\"\"|r'''|r\"\"\")", stripped))


def scan_for_injection(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan content for injection patterns. Returns count of issues found.

    CRITICAL: This check runs BEFORE any allowlist processing.
    Note: Shell scripts (.sh, .bash) legitimately use command substitution,
    so we only flag command substitution in non-shell files where it's unexpected.
    """
    issues_found = 0
    lines = content.split("\n")

    file_lower = file_path.lower()

    # Determine if file is markdown - backticks are code formatting
    is_markdown = file_lower.endswith((".md", ".mdx", ".markdown"))

    # Determine if file is a shell-like script - command substitution is expected
    is_shell_script = is_shell_like_file(file_path)

    # Determine if file is a test file - test files often have mock/example content
    # Handle both absolute (/tests/) and relative (tests/) paths, plus conftest.py
    file_normalized = file_lower.replace("\\", "/")
    is_test_file = "test_" in file_lower or "_test.py" in file_lower or "/tests/" in file_normalized or file_normalized.startswith("tests/") or "/conftest.py" in file_normalized or file_normalized == "conftest.py"

    # Determine if file is a validator script - they contain intentional patterns
    is_validator = is_validator_script(file_path)

    # Skip all injection checks for validator scripts (they define patterns)
    if is_validator:
        return 0

    # Python files never use backtick command substitution — backticks are RST/docstring formatting
    is_python_file = file_lower.endswith(".py")

    # Skip command substitution checks for shell scripts (it's expected) and markdown/tests
    skip_command_sub = is_shell_script or is_markdown or is_test_file

    for line_num, line in enumerate(lines, start=1):
        # Skip comment-only lines in shell scripts
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue

        # RST double-backtick filter: if every backtick segment is an RST ``code`` pair, skip
        # This avoids flagging Python docstrings that use reStructuredText formatting
        if "`" in line and not is_markdown:
            backtick_segments = re.findall(r"`[^`]*`", line)
            if backtick_segments and all(seg.startswith("``") and seg.endswith("``") for seg in backtick_segments):
                continue

        # Check command substitution (CRITICAL) - but not in shell scripts where it's expected
        if not skip_command_sub:
            for pattern, msg in COMMAND_SUBSTITUTION_PATTERNS:
                # Python files don't have native backtick command substitution —
                # backticks in .py are usually RST/docstring formatting. BUT backticks
                # inside shell-execution calls (os.system, os.popen, subprocess) are real threats.
                if is_python_file and "`...`" in msg:
                    shell_exec_indicators = ("os.system", "os.popen", "subprocess", "shell=", "Popen", "check_output")
                    if not any(indicator in line for indicator in shell_exec_indicators):
                        continue
                if pattern.search(line):
                    report.critical(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                    issues_found += 1

        # Check pipe to shell (CRITICAL) - skip for markdown docs (code examples)
        if not is_markdown:
            for pattern, msg in PIPE_TO_SHELL_PATTERNS:
                if pattern.search(line):
                    # In Python files, skip if pipe-to-shell is inside a string literal
                    # (e.g. install instructions in dict values or help text)
                    if is_python_file and ('"' in stripped or "'" in stripped):
                        continue
                    report.critical(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                    issues_found += 1

        # Check eval patterns (CRITICAL) - skip for markdown docs (code examples)
        if not is_markdown:
            for pattern, msg in EVAL_PATTERNS:
                if pattern.search(line):
                    # In Python files, skip shell-style eval/exec patterns (e.g. "exec " without parens)
                    # Only flag actual Python function calls: eval(...), exec(...)
                    if is_python_file and "command" in msg.lower():
                        continue
                    report.critical(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                    issues_found += 1

        # Check unsafe variable expansion (MAJOR) - skip for markdown docs and Python string literals
        # (Python strings may contain PowerShell/Bash code snippets that use $var syntax)
        if not is_markdown:
            if not (is_python_file and ('"' in stripped or "'" in stripped)):
                for pattern, msg in UNSAFE_VARIABLE_PATTERNS:
                    if pattern.search(line):
                        report.major(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                        issues_found += 1

    return issues_found


def scan_for_path_traversal(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan content for path traversal patterns. Returns count of issues found.

    Note: Documentation files (.md) often contain examples showing path syntax.
    We skip path checks for markdown documentation to avoid false positives.
    """
    issues_found = 0
    lines = content.split("\n")

    file_lower = file_path.lower()

    # Skip path checks for validator scripts - they contain intentional pattern definitions
    if is_validator_script(file_path):
        return 0

    # Skip path checks for markdown documentation - they contain examples
    if file_lower.endswith((".md", ".mdx", ".markdown")):
        return 0

    # Skip path checks for test files - they contain example data
    # Handle both absolute (/tests/) and relative (tests/) paths
    file_normalized = file_lower.replace("\\", "/")
    if "test_" in file_lower or "_test.py" in file_lower or "/tests/" in file_normalized or file_normalized.startswith("tests/"):
        return 0

    for line_num, line in enumerate(lines, start=1):
        # Skip comment-only lines
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue

        # Skip shebang lines entirely - they legitimately reference system paths
        if stripped.startswith("#!"):
            continue

        # Skip Python multi-line string assignments (e.g. PRE_PUSH_HOOK = '''#!/usr/bin/env python3)
        if _line_is_string_assignment(line):
            continue

        # Detect if this line is a Python string literal (help text, error messages, etc.)
        is_python_string_line = file_lower.endswith(".py") and ('"' in stripped or "'" in stripped)

        for pattern, msg in PATH_TRAVERSAL_PATTERNS:
            match = pattern.search(line)
            if match:
                matched_text = match.group(0)

                # Skip ..\ pattern when it's a Python string escape (e.g. "...\n" in f-strings)
                if "..\\" in msg and "..\\" in matched_text:
                    # Check if the backslash is followed by a common Python escape char
                    pos = line.find("..\\")
                    if pos >= 0 and pos + 3 < len(line) and line[pos + 3] in "nrtbf0'\"":
                        continue

                # For Windows path matches (C:\...), skip if they contain example usernames
                # e.g. C:\Users\you\... or C:\Users\alice\... in documentation
                # Handle both single-backslash (C:\Users\you) and double-backslash (C:\\Users\\you)
                # since raw file text may contain escaped backslashes
                if "\\" in matched_text or "Windows" in msg:
                    win_user_match = re.search(r"[A-Za-z]:\\\\?(?:Users|users)\\\\?([^\\]+)", line)
                    if win_user_match:
                        username = win_user_match.group(1).lower()
                        if username in EXAMPLE_USERNAMES:
                            continue

                # In Python files, skip paths inside string literals (help text, error messages)
                if is_python_string_line:
                    # Skip Windows paths and absolute paths in Python strings
                    if "Windows" in msg or "C:\\" in matched_text:
                        continue
                    # Skip absolute Unix paths in Python string literals
                    # (e.g. help text mentioning shebangs or system bin directories)
                    if "Absolute Unix" in msg and ("#!/" in line or "help" in stripped.lower() or "epilog" in stripped.lower() or stripped.startswith(("'", '"', "f'", 'f"', "r'", 'r"'))):
                        continue

                report.critical(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                issues_found += 1

    return issues_found


def scan_for_secrets(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan content for secret patterns. Returns count of issues found."""
    file_lower = file_path.lower()

    # Skip validator scripts — they define regex patterns that match secret formats
    if is_validator_script(file_path):
        return 0

    # Skip test files — they contain intentional example/mock secrets
    # Handle both absolute (/tests/) and relative (tests/) paths
    file_normalized = file_lower.replace("\\", "/")
    if "test_" in file_lower or "_test.py" in file_lower or "/tests/" in file_normalized or file_normalized.startswith("tests/"):
        return 0

    # Skip markdown documentation — contains example credentials for illustration
    if file_lower.endswith((".md", ".mdx", ".markdown")):
        return 0

    issues_found = 0
    lines = content.split("\n")

    for line_num, line in enumerate(lines, start=1):
        for pattern, secret_type in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                matched_text = match.group(0)
                # Skip known example/placeholder secrets (e.g. AWS docs AKIAIOSFODNN7EXAMPLE)
                if matched_text in KNOWN_EXAMPLE_SECRETS:
                    continue
                # Mask the actual secret in the report
                masked_line = line.strip()[:40] + "..." if len(line.strip()) > 40 else line.strip()
                report.critical(f"{secret_type} detected: {masked_line}", file_path, line_num)
                issues_found += 1

    return issues_found


def scan_for_user_paths(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan content for hardcoded user paths. Returns count of issues found.

    Note: Validator scripts and documentation contain pattern examples that would
    trigger false positives. We skip those files.
    """
    issues_found = 0
    lines = content.split("\n")

    file_lower = file_path.lower()

    # Skip validator scripts - they contain pattern definitions for detecting user paths
    if is_validator_script(file_path):
        return 0

    # Skip markdown documentation - they contain examples
    if file_lower.endswith((".md", ".mdx", ".markdown")):
        return 0

    # Skip test files - they contain example data
    # Handle both absolute (/tests/) and relative (tests/) paths
    file_normalized = file_lower.replace("\\", "/")
    if "test_" in file_lower or "_test.py" in file_lower or "/tests/" in file_normalized or file_normalized.startswith("tests/"):
        return 0

    for line_num, line in enumerate(lines, start=1):
        for pattern in USER_PATH_PATTERNS:
            match = pattern.search(line)
            if match:
                report.major(
                    f"Hardcoded user path detected (use ${{CLAUDE_PLUGIN_ROOT}} instead): {match.group()}",
                    file_path,
                    line_num,
                )
                issues_found += 1

    return issues_found


def _is_python_string_context(stripped_line: str) -> bool:
    """Check if a line is a Python string literal, template, print, or docstring.

    Used to skip false positives in generator scripts, help text, and templates.
    """
    # Lines that are clearly string content (quotes, f-strings, print, docstrings)
    if stripped_line.startswith(('"""', "'''", '"', "'", "f'", 'f"', "r'", 'r"')):
        return True
    # Template/generator assignments
    if any(kw in stripped_line for kw in ("print(", "cprint(", "_info(", "_warn(", "epilog", "help=", "description=")):
        return True
    # CI workflow template content (GitHub Actions secrets, workflow syntax)
    if "${{" in stripped_line:
        return True
    return False


def scan_for_prompt_injection(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan skill/agent/command content for prompt injection patterns (CRITICAL)."""
    file_lower = file_path.lower()
    # Only check files that contain instructions for the AI model
    ai_content_files = (".md", ".mdx", ".txt")
    if not any(file_lower.endswith(ext) for ext in ai_content_files):
        return 0
    # Skip test files and validator scripts
    if is_validator_script(file_path):
        return 0
    file_normalized = file_lower.replace("\\", "/")
    if "/tests/" in file_normalized or file_normalized.startswith("tests/"):
        return 0

    issues_found = 0
    lines = content.split("\n")
    for line_num, line in enumerate(lines, start=1):
        for pattern, msg in PROMPT_INJECTION_PATTERNS:
            if pattern.search(line):
                report.critical(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                issues_found += 1
    return issues_found


def scan_for_data_exfiltration(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan for data exfiltration patterns (WARNING — many legitimate uses)."""
    file_lower = file_path.lower()
    if is_validator_script(file_path):
        return 0
    # Skip markdown docs — they contain code examples
    if file_lower.endswith((".md", ".mdx", ".markdown")):
        return 0
    file_normalized = file_lower.replace("\\", "/")
    if "/tests/" in file_normalized or file_normalized.startswith("tests/"):
        return 0

    issues_found = 0
    lines = content.split("\n")
    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern, msg in DATA_EXFILTRATION_PATTERNS:
            if pattern.search(line):
                report.warning(f"{msg}: {stripped[:80]}", file_path, line_num)
                issues_found += 1
    return issues_found


def scan_for_supply_chain(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan for supply chain attack patterns (CRITICAL)."""
    file_lower = file_path.lower()
    if is_validator_script(file_path):
        return 0
    if file_lower.endswith((".md", ".mdx", ".markdown")):
        return 0
    file_normalized = file_lower.replace("\\", "/")
    if "/tests/" in file_normalized or file_normalized.startswith("tests/"):
        return 0
    is_python = file_lower.endswith(".py")

    issues_found = 0
    lines = content.split("\n")
    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Skip Python string literals (template generators, help text, install instructions)
        if is_python and _is_python_string_context(stripped):
            continue
        for pattern, msg in SUPPLY_CHAIN_PATTERNS:
            if pattern.search(line):
                report.critical(f"{msg}: {stripped[:80]}", file_path, line_num)
                issues_found += 1
    return issues_found


def scan_for_credential_harvest(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan for credential harvesting patterns (CRITICAL, except ~/.claude/ which is legitimate)."""
    file_lower = file_path.lower()
    if is_validator_script(file_path):
        return 0
    if file_lower.endswith((".md", ".mdx", ".markdown")):
        return 0
    file_normalized = file_lower.replace("\\", "/")
    if "/tests/" in file_normalized or file_normalized.startswith("tests/"):
        return 0
    is_python = file_lower.endswith(".py")

    issues_found = 0
    lines = content.split("\n")
    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Skip Python string literals (templates, help text, CI workflows)
        if is_python and _is_python_string_context(stripped):
            continue
        for pattern, msg in CREDENTIAL_HARVEST_PATTERNS:
            if pattern.search(line):
                report.critical(f"{msg}: {stripped[:80]}", file_path, line_num)
                issues_found += 1
    return issues_found


def scan_for_sandbox_escape(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan for sandbox escape patterns."""
    file_lower = file_path.lower()
    if is_validator_script(file_path):
        return 0
    if file_lower.endswith((".md", ".mdx", ".markdown")):
        return 0
    file_normalized = file_lower.replace("\\", "/")
    if "/tests/" in file_normalized or file_normalized.startswith("tests/"):
        return 0
    is_python = file_lower.endswith(".py")

    issues_found = 0
    lines = content.split("\n")
    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Skip Python string literals (templates, help text, generator output)
        if is_python and _is_python_string_context(stripped):
            continue
        # Skip reference .py files inside skills/ (they're templates, not executable code)
        if "/references/" in file_normalized or file_normalized.startswith("skills/"):
            continue
        for pattern, msg in SANDBOX_ESCAPE_PATTERNS:
            if pattern.search(line):
                # dangerouslySkipPermissions is valid for worktree agents — WARNING only
                if "dangerouslySkipPermissions" in msg:
                    report.warning(f"{msg} (valid for worktree agents, verify intent): {stripped[:80]}", file_path, line_num)
                else:
                    report.major(f"{msg}: {stripped[:80]}", file_path, line_num)
                issues_found += 1
    return issues_found


def check_hook_abuse(plugin_path: Path, report: ValidationReport) -> int:
    """Check hooks.json for abuse patterns (MAJOR)."""
    hooks_file = plugin_path / "hooks" / "hooks.json"
    if not hooks_file.exists():
        return 0

    issues_found = 0
    try:
        import json as _json
        data = _json.loads(hooks_file.read_text(encoding="utf-8"))
        hooks = data.get("hooks", data) if isinstance(data, dict) else {}

        for event_name, hook_list in hooks.items():
            if not isinstance(hook_list, list):
                continue
            for entry in hook_list:
                hook_defs = entry.get("hooks", []) if isinstance(entry, dict) else []
                for hook in hook_defs:
                    if not isinstance(hook, dict):
                        continue
                    cmd = hook.get("command", "")
                    url = hook.get("url", "")
                    hook_type = hook.get("type", "")

                    # PreToolUse hooks sending data externally
                    if event_name == "PreToolUse" and hook_type == "http" and url:
                        if not any(loc in url for loc in ("localhost", "127.0.0.1", "::1")):
                            report.major(f"Hook abuse: PreToolUse HTTP hook sends to external URL: {url[:60]}", "hooks/hooks.json")
                            issues_found += 1

                    # PostToolUse hooks sending tool output externally
                    if event_name == "PostToolUse" and hook_type == "http" and url:
                        if not any(loc in url for loc in ("localhost", "127.0.0.1", "::1")):
                            report.major(f"Hook abuse: PostToolUse HTTP hook may exfiltrate tool output to: {url[:60]}", "hooks/hooks.json")
                            issues_found += 1

                    # Command hooks with suspicious commands
                    if cmd:
                        for sc_pattern, sc_msg in SUPPLY_CHAIN_PATTERNS + DATA_EXFILTRATION_PATTERNS:
                            if sc_pattern.search(cmd):
                                report.critical(f"Hook abuse ({event_name}): {sc_msg} in hook command", "hooks/hooks.json")
                                issues_found += 1

                    # Excessive timeout (> 1 hour) is suspicious
                    timeout = hook.get("timeout", 0)
                    if isinstance(timeout, (int, float)) and timeout > 3600:
                        report.warning(f"Hook has excessive timeout ({timeout}s) on {event_name} — may indicate long-running exfiltration", "hooks/hooks.json")
                        issues_found += 1

    except (ValueError, OSError):
        pass
    return issues_found


def check_mcp_abuse(plugin_path: Path, report: ValidationReport) -> int:
    """Check MCP config for non-localhost servers (WARNING — many valid remote MCPs)."""
    mcp_file = plugin_path / ".mcp.json"
    if not mcp_file.exists():
        return 0

    issues_found = 0
    try:
        import json as _json
        data = _json.loads(mcp_file.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", data) if isinstance(data, dict) else {}

        for name, config in servers.items():
            if not isinstance(config, dict):
                continue
            # Check SSE/streamable-http transport pointing to external hosts
            url = config.get("url", "")
            if url and not any(loc in url for loc in ("localhost", "127.0.0.1", "::1")):
                report.warning(f"MCP server '{name}' connects to external host: {url[:60]} (verify trust)", ".mcp.json")
                issues_found += 1

            # Check command-based servers that download/execute
            cmd = config.get("command", "")
            args = config.get("args", [])
            full_cmd = f"{cmd} {' '.join(str(a) for a in args)}" if args else cmd
            for sc_pattern, sc_msg in SUPPLY_CHAIN_PATTERNS:
                if sc_pattern.search(full_cmd):
                    report.critical(f"MCP server '{name}': {sc_msg}", ".mcp.json")
                    issues_found += 1

    except (ValueError, OSError):
        pass
    return issues_found


def check_permission_escalation(plugin_path: Path, report: ValidationReport) -> int:
    """Check for permission escalation in plugin manifest and agent frontmatter (WARNING)."""
    issues_found = 0

    # Check plugin.json for overly broad tool permissions
    manifest = plugin_path / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            import json as _json
            data = _json.loads(manifest.read_text(encoding="utf-8"))
            # Check if plugin requests dangerous permission modes
            perm_mode = data.get("permissionMode", "")
            if perm_mode in ("dangerouslySkipPermissions", "bypass"):
                report.warning(
                    f"Permission escalation: plugin.json requests permissionMode '{perm_mode}'",
                    ".claude-plugin/plugin.json",
                )
                issues_found += 1
        except (ValueError, OSError):
            pass

    # Check agent frontmatter for broad tool access
    agents_dir = plugin_path / "agents"
    if agents_dir.is_dir():
        for agent_file in agents_dir.glob("*.md"):
            try:
                content = agent_file.read_text(encoding="utf-8")
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        fm = parts[1]
                        # Check for dangerouslySkipPermissions in agent frontmatter
                        if "dangerouslyskippermissions" in fm.lower().replace("_", "").replace("-", ""):
                            report.warning(
                                "Permission escalation: agent requests dangerouslySkipPermissions (valid for worktree agents, verify intent)",
                                f"agents/{agent_file.name}",
                            )
                            issues_found += 1
            except (OSError, UnicodeDecodeError):
                pass

    return issues_found


def check_dangerous_files(plugin_path: Path, report: ValidationReport) -> int:
    """Check for presence of dangerous files in the plugin. Returns count found."""
    issues_found = 0
    gi = get_gitignore_filter(plugin_path)

    for root, dirs, files in gi.walk(plugin_path):
        for filename in files:
            if filename in DANGEROUS_FILES:
                full_path = Path(root) / filename
                rel_path = full_path.relative_to(plugin_path)
                report.critical(f"Dangerous file detected: {rel_path}")
                issues_found += 1

    return issues_found


def check_script_permissions(plugin_path: Path, report: ValidationReport) -> int:
    """Check script files for proper permissions. Returns count of issues found."""
    issues_found = 0
    gi = get_gitignore_filter(plugin_path)

    for root, dirs, files in gi.walk(plugin_path):
        for filename in files:
            file_path = Path(root) / filename
            rel_path = file_path.relative_to(plugin_path)

            # Check shell scripts
            if filename.endswith(".sh"):
                try:
                    file_stat = file_path.stat()
                    mode = file_stat.st_mode

                    # Check if executable
                    if not (mode & stat.S_IXUSR):
                        report.minor(f"Shell script is not executable: {rel_path}")
                        issues_found += 1

                    # Check for world-writable (security risk)
                    if mode & stat.S_IWOTH:
                        report.critical(f"Script is world-writable: {rel_path}")
                        issues_found += 1

                    # Check for proper shebang
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        first_line = f.readline()
                        if not first_line.startswith("#!"):
                            report.minor(f"Shell script missing shebang: {rel_path}")
                            issues_found += 1
                        elif "bash" not in first_line and "sh" not in first_line:
                            report.info(f"Shell script has non-standard shebang: {first_line.strip()}", str(rel_path))

                except (OSError, PermissionError) as e:
                    report.major(f"Cannot check script permissions: {rel_path} ({e})")
                    issues_found += 1

            # Check Python scripts
            elif filename.endswith(".py"):
                try:
                    file_stat = file_path.stat()
                    mode = file_stat.st_mode

                    # Check for world-writable
                    if mode & stat.S_IWOTH:
                        report.critical(f"Python script is world-writable: {rel_path}")
                        issues_found += 1

                except (OSError, PermissionError) as e:
                    report.major(f"Cannot check script permissions: {rel_path} ({e})")
                    issues_found += 1

    return issues_found


def scan_all_files(plugin_path: Path, report: ValidationReport) -> dict[str, int]:
    """Recursively scan all text files in the plugin for security issues.

    Returns a dictionary with counts of issues found by category.
    """
    stats = {
        "files_scanned": 0,
        "files_skipped": 0,
        "injection_issues": 0,
        "path_traversal_issues": 0,
        "secret_issues": 0,
        "user_path_issues": 0,
        "prompt_injection_issues": 0,
        "exfiltration_issues": 0,
        "supply_chain_issues": 0,
        "credential_harvest_issues": 0,
        "sandbox_escape_issues": 0,
    }

    gi = get_gitignore_filter(plugin_path)

    for root, dirs, files in gi.walk(plugin_path):
        for filename in files:
            file_path = Path(root) / filename
            rel_path = str(file_path.relative_to(plugin_path))

            # Skip binary files
            if is_binary_file(file_path):
                stats["files_skipped"] += 1
                continue

            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                stats["files_scanned"] += 1

                # Run all content scans
                # CRITICAL: Injection detection runs FIRST, before any allowlisting
                stats["injection_issues"] += scan_for_injection(content, rel_path, report)
                stats["path_traversal_issues"] += scan_for_path_traversal(content, rel_path, report)
                stats["secret_issues"] += scan_for_secrets(content, rel_path, report)
                stats["user_path_issues"] += scan_for_user_paths(content, rel_path, report)
                # AI-specific threat scans
                stats["prompt_injection_issues"] += scan_for_prompt_injection(content, rel_path, report)
                stats["exfiltration_issues"] += scan_for_data_exfiltration(content, rel_path, report)
                stats["supply_chain_issues"] += scan_for_supply_chain(content, rel_path, report)
                stats["credential_harvest_issues"] += scan_for_credential_harvest(content, rel_path, report)
                stats["sandbox_escape_issues"] += scan_for_sandbox_escape(content, rel_path, report)

            except (OSError, PermissionError) as e:
                report.minor(f"Cannot read file: {rel_path} ({e})")
                stats["files_skipped"] += 1

    return stats


# =============================================================================
# Main Validation Function
# =============================================================================


def check_cc_audit(plugin_path: Path, report: ValidationReport) -> int:
    """Run cc-audit external scanner if available (optional, non-blocking).

    Uses npx @cc-audit/cc-audit to scan for AI-specific threats with 100+ rules.
    Output is saved to a temp JSON file to avoid context bloat, then parsed.
    Returns the number of issues found. Returns 0 if cc-audit is not installed.
    """
    # Check if npx is available
    if not shutil.which("npx"):
        report.info("cc-audit: npx not found, skipping external audit (install Node.js to enable)")
        return 0

    issues_found = 0
    # Write output to temp file — never floods context
    with tempfile.NamedTemporaryFile(suffix=".json", prefix="cc-audit-", delete=False, mode="w") as tmp:
        tmp_path = tmp.name

    # Auto-generate .cc-audit.yaml if not present (cc-audit requires it)
    config_file = plugin_path / ".cc-audit.yaml"
    created_config = False
    if not config_file.exists():
        subprocess.run(
            ["npx", "--yes", "@cc-audit/cc-audit", "init", str(plugin_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        created_config = config_file.exists()

    try:
        result = subprocess.run(
            [
                "npx", "--yes", "@cc-audit/cc-audit", "check",
                str(plugin_path),
                "-t", "plugin",
                "--format", "json",
                "--output", tmp_path,
                "--ci",
                "--no-telemetry",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Parse JSON output
        try:
            data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # cc-audit may not have written valid JSON (e.g., no findings)
            if result.returncode == 0:
                report.passed("cc-audit: no findings (external scan clean)")
            elif result.returncode == 2:
                report.info(f"cc-audit scan error: {result.stderr.strip()[:100]}")
            return 0

        # Map cc-audit severity to CPV report levels
        severity_map = {
            "critical": "critical",
            "high": "major",
            "medium": "minor",
            "low": "warning",
        }

        # Handle both possible JSON structures (array of findings or object with results key)
        findings: list = []
        if isinstance(data, list):
            findings = data
        elif isinstance(data, dict):
            # Use 'or []' to guard against None — data.get() may return None for missing keys
            raw = data.get("results") or data.get("findings") or data.get("vulnerabilities") or []
            findings = list(raw)

        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = finding.get("severity", "medium").lower()
            rule_id = finding.get("ruleId", finding.get("rule_id", finding.get("code", "?")))
            message = finding.get("message", finding.get("description", "unknown"))
            file_ref = finding.get("file", finding.get("location", {}).get("file", ""))
            line = finding.get("line", finding.get("location", {}).get("line", 0))

            cpv_level = severity_map.get(severity, "warning")
            report_fn = getattr(report, cpv_level)
            report_fn(f"cc-audit {rule_id}: {str(message)[:100]}", file_ref, line if isinstance(line, int) else 0)
            issues_found += 1

        if issues_found == 0 and result.returncode == 0:
            report.passed("cc-audit: no findings (external scan clean)")

    except subprocess.TimeoutExpired:
        report.warning("cc-audit timed out after 120s — scan aborted")
    except FileNotFoundError:
        report.info("cc-audit: npx command failed, skipping external audit")
    finally:
        # Clean up temp file and auto-generated config
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        if created_config:
            try:
                config_file.unlink(missing_ok=True)
            except OSError:
                pass

    return issues_found


def validate_security(plugin_path: Path) -> ValidationReport:
    """Run all security validations on a plugin directory.

    This function performs comprehensive security analysis including:
    Traditional: injection, path traversal, secrets, user paths, dangerous files, permissions
    AI-specific: prompt injection, data exfiltration, supply chain, credential harvest,
    sandbox escape, hook abuse, MCP abuse, agent impersonation, permission escalation

    Args:
        plugin_path: Path to the plugin directory

    Returns:
        ValidationReport with all security findings
    """
    report = ValidationReport()

    # Verify plugin path exists
    if not plugin_path.exists():
        report.critical(f"Plugin path does not exist: {plugin_path}")
        return report

    if not plugin_path.is_dir():
        report.critical(f"Plugin path is not a directory: {plugin_path}")
        return report

    report.info(f"Starting security scan of: {plugin_path}")

    # --- Traditional checks ---

    # Check 1: Dangerous files (quick check first)
    dangerous_count = check_dangerous_files(plugin_path, report)
    if dangerous_count == 0:
        report.passed("No dangerous files detected")

    # Check 2: Script permissions
    permission_issues = check_script_permissions(plugin_path, report)
    if permission_issues == 0:
        report.passed("All scripts have proper permissions")

    # Check 3-11: Full content scan (traditional + AI-specific)
    scan_stats = scan_all_files(plugin_path, report)

    # Report scan statistics
    report.info(f"Scanned {scan_stats['files_scanned']} files, skipped {scan_stats['files_skipped']} binary files")

    # Add passed messages for clean traditional categories
    if scan_stats["injection_issues"] == 0:
        report.passed("No injection patterns detected")
    if scan_stats["path_traversal_issues"] == 0:
        report.passed("No path traversal patterns detected")
    if scan_stats["secret_issues"] == 0:
        report.passed("No secrets detected")
    if scan_stats["user_path_issues"] == 0:
        report.passed("No hardcoded user paths detected")

    # --- AI-specific file-level checks ---

    # Check 12: Hook abuse (external URLs, supply chain in hooks)
    hook_issues = check_hook_abuse(plugin_path, report)
    if hook_issues == 0:
        report.passed("No hook abuse patterns detected")

    # Check 13: MCP server abuse (non-localhost connections)
    mcp_issues = check_mcp_abuse(plugin_path, report)
    if mcp_issues == 0:
        report.passed("No MCP server abuse detected")

    # Check 14: Permission escalation (overly broad permissions)
    escalation_issues = check_permission_escalation(plugin_path, report)
    if escalation_issues == 0:
        report.passed("No permission escalation detected")

    # Add passed messages for clean AI-specific categories
    if scan_stats["prompt_injection_issues"] == 0:
        report.passed("No prompt injection patterns detected")
    if scan_stats["exfiltration_issues"] == 0:
        report.passed("No data exfiltration patterns detected")
    if scan_stats["supply_chain_issues"] == 0:
        report.passed("No supply chain attack patterns detected")
    if scan_stats["credential_harvest_issues"] == 0:
        report.passed("No credential harvesting patterns detected")
    if scan_stats["sandbox_escape_issues"] == 0:
        report.passed("No sandbox escape patterns detected")

    # --- External scanner (optional) ---

    # Check 16: cc-audit external scanner (100+ rules, non-blocking if unavailable)
    check_cc_audit(plugin_path, report)

    return report


# =============================================================================
# CLI Main
# =============================================================================


def main() -> int:
    """CLI entry point for standalone security validation."""
    parser = argparse.ArgumentParser(
        description="Security validation for Claude Code plugins",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Security Checks Performed:
  1. Injection detection (command substitution, eval, pipe to shell)
  2. Path traversal blocking (../, absolute paths)
  3. Secret detection (API keys, private keys, tokens)
  4. Hardcoded user path detection (/Users/xxx/, /home/xxx/)
  5. Dangerous file detection (.env, credentials.json)
  6. Script permission check (executable, shebang, world-writable)
  7. Plugin-wide recursive scan of all text files

Exit Codes:
  0 - All checks passed
  1 - CRITICAL issues found (must fix)
  2 - MAJOR issues found (should fix)
  3 - MINOR issues found (recommended to fix)
        """,
    )
    parser.add_argument("plugin_path", type=Path, help="Path to the plugin directory to validate")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show all results including INFO and PASSED")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block validation")
    parser.add_argument("--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout")

    args = parser.parse_args()

    # Resolve to absolute path so relative_to() works correctly
    plugin_path = args.plugin_path.resolve()

    # Verify this is a plugin directory
    if not plugin_path.is_dir():
        print(f"Error: {plugin_path} is not a directory", file=sys.stderr)
        return 1
    if not (plugin_path / ".claude-plugin").is_dir():
        print(
            f"Error: No Claude Code plugin found at {plugin_path}\nExpected a .claude-plugin/ directory.",
            file=sys.stderr,
        )
        return 1

    # Run validation
    report = validate_security(plugin_path)

    # Output results
    if args.json:
        output = report.to_dict()
        output["plugin_path"] = str(plugin_path)
        print(json.dumps(output, indent=2))
    elif args.report:

        def _print_full(report, verbose=False):
            print_report_summary(report, "Security Validation Report")
            print_results_by_level(report, verbose=verbose)

        save_report_and_print_summary(report, Path(args.report), "Security Validation", _print_full, args.verbose, plugin_path=args.plugin_path)
    else:
        print_results_by_level(report, verbose=args.verbose)
        print_report_summary(report, title=f"Security Validation: {plugin_path.name}")

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
