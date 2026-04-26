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
17. Tirith External Scanner (terminal-security rules: homograph URLs, ANSI/bidi/zero-width
    injection, pipe-to-shell, hidden Unicode, config poisoning — runs scan-only, no hooks)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from cpv_validation_common import (
    CLOUD_IMDS_PATTERNS,
    CRYPTOMINING_PATTERNS,
    DANGEROUS_FILES,
    ENV_BULK_HARVEST_PATTERNS,
    EXAMPLE_USERNAMES,
    GTFOBIN_LOLBIN_PATTERNS,
    KNOWN_EXAMPLE_SECRETS,
    MCP_DANGEROUS_ENV_KEYS,
    MCP_DESCRIPTION_INJECTION_PREFILTER,
    PERSISTENCE_PATTERNS,
    PHASE3_PATTERNS,
    SECRET_PATTERNS,
    TIMEBOMB_PATTERNS,
    USER_PATH_PATTERNS,
    ValidationReport,
    build_fence_state,
    effective_severity,
    find_obfuscated_exec,
    find_tag_block_chars,
    find_zero_width_chars,
    get_gitignore_filter,
    has_mixed_script,
    has_negation_guard_nearby,
    is_binary_file,
    is_compromised_package,
    is_in_fenced_code_block,
    is_pth_with_exec,
    is_shadowed_tool_name,
    is_typosquat,
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
        re.compile(
            r"(?<!\$\{CLAUDE_PLUGIN_ROOT\})(?<!\$\{CLAUDE_PLUGIN_DATA\})(?<!\$\{CLAUDE_PROJECT_DIR\})(?<![\w$\{])/(?:usr|etc|var|tmp|opt|bin|sbin|lib|root)/"
        ),
        "Absolute Unix system path detected",
    ),
    # Windows absolute paths
    (re.compile(r"[A-Za-z]:\\"), "Windows absolute path detected"),
]

# =============================================================================
# AI-Specific Threat Patterns (Checks 8-16)
# =============================================================================

# Prompt injection patterns — malicious instructions in skills/agents/commands
# Phase 2a (RC-01/04/06/07) added paraphrase template, typo variants, privilege
# roleplay, completion attacks, DAN/jailbreak modes, identity revocation, and
# reveal-directive detection on top of the original 8 patterns.
PROMPT_INJECTION_PATTERNS = [
    # Original 8 patterns
    (
        re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.IGNORECASE),
        "Prompt injection: ignore previous instructions",
    ),
    (re.compile(r"you\s+are\s+now\s+(?:a|an)\b", re.IGNORECASE), "Prompt injection: identity override ('you are now')"),
    (
        re.compile(
            r"(?:forget|disregard|override)\s+(?:all\s+)?(?:your|the)\s+(?:instructions?|rules?|guidelines?|constraints?)",
            re.IGNORECASE,
        ),
        "Prompt injection: instruction override",
    ),
    (
        re.compile(
            r"do\s+not\s+follow\s+(?:any|the)\s+(?:previous|above|prior)\s+(?:instructions?|rules?)", re.IGNORECASE
        ),
        "Prompt injection: instruction negation",
    ),
    (
        re.compile(r"(?:system|hidden)\s*(?:prompt|instruction|message)\s*:", re.IGNORECASE),
        "Prompt injection: fake system prompt marker",
    ),
    (re.compile(r"<\s*(?:system|instructions?|context)\s*>", re.IGNORECASE), "Prompt injection: fake XML system tag"),
    (re.compile(r"\[INST\]|\[/INST\]|\[SYSTEM\]", re.IGNORECASE), "Prompt injection: fake instruction delimiters"),
    (
        re.compile(r"IMPORTANT:\s*(?:ignore|override|forget|disregard)", re.IGNORECASE),
        "Prompt injection: IMPORTANT override",
    ),
    # Phase 2a — RC-01 paraphrase template (verb x noun x target with up to 3 intervening words)
    (
        re.compile(
            r"\b(?:bypass|skip|circumvent|workaround|sidestep|put\s+aside|set\s+aside)\s+"
            r"(?:all\s+)?(?:\w+\s+){0,3}"
            r"(?:previous|prior|above|earlier|original|system|prior\s+to\s+this)\s+"
            r"(?:instructions?|rules?|guidelines?|directives?|constraints?)",
            re.IGNORECASE,
        ),
        "Prompt injection: bypass/skip/circumvent variant (RC-01)",
    ),
    # RC-04 — typoglycemia evasion (1gnore, prev1ous, d1sregard, 0verride)
    (
        re.compile(r"\b(?:1gn0?re|1gnore|d1sregard|prev1ous|0verride|f0rget)\b", re.IGNORECASE),
        "Prompt injection: typoglycemia evasion variant (RC-04)",
    ),
    # RC-04 — privilege-via-roleplay (you are now root/admin/system/super/sudo).
    # The article (a/an/the) is OPTIONAL — many attack samples drop it for
    # naturalness ("you are now root" without "the").
    (
        re.compile(
            r"\byou\s+are\s+(?:now\s+)?(?:(?:a|an|the)\s+)?"
            r"(?:root|admin(?:istrator)?|system|super(?:user)?|sudo|kernel)\b",
            re.IGNORECASE,
        ),
        "Prompt injection: privilege-roleplay (RC-04)",
    ),
    # RC-04 — DAN / jailbreak mode invocation
    (
        re.compile(
            r"\b(?:DAN\s+(?:mode|prompt)|developer\s+mode|jailbreak\s+mode|unrestricted\s+mode|do\s+anything\s+now)\b",
            re.IGNORECASE,
        ),
        "Prompt injection: DAN / jailbreak mode (RC-04)",
    ),
    # RC-04 — identity revocation ("you have no restrictions", "you are not bound by")
    (
        re.compile(
            r"\byou\s+(?:have\s+no|are\s+not\s+bound\s+by|are\s+free\s+from|no\s+longer\s+have)\s+"
            r"(?:restrictions?|guidelines?|safety|limits?|rules?|constraints?)",
            re.IGNORECASE,
        ),
        "Prompt injection: identity revocation (RC-04)",
    ),
    # RC-07 — completion / end-of-task attack (task complete, end of instructions, begin new task)
    (
        re.compile(
            r"\b(?:task\s+complete\.?\s*now\s+(?:begin|start|do)|end\s+of\s+(?:instructions?|task|prompt)\.?\s*"
            r"(?:now|next)|begin\s+new\s+task|new\s+task\s+begins)\b",
            re.IGNORECASE,
        ),
        "Prompt injection: completion attack (RC-07)",
    ),
    # RC-06 — reveal-directive (show system prompt / what are your instructions)
    (
        re.compile(
            r"\b(?:reveal|show|print|output|display|repeat|echo)\s+(?:me\s+)?(?:your|the)\s+"
            r"(?:system\s+prompt|initial\s+instructions?|hidden\s+instructions?|"
            r"original\s+(?:instructions?|prompt)|configuration|prompt|rules)",
            re.IGNORECASE,
        ),
        "Prompt injection: reveal-directive (RC-06)",
    ),
    # RC-06 — what-are-you-told (questioning the system prompt)
    (
        re.compile(
            r"\bwhat\s+(?:are|is|were)\s+(?:your|the)\s+(?:initial\s+|original\s+|system\s+)?(?:instructions?|prompt|rules)",
            re.IGNORECASE,
        ),
        "Prompt injection: prompt-extraction question (RC-06)",
    ),
]

# Data exfiltration patterns — sending data to external servers
# Phase 2c (RC-17/19) added webhook host list (discord/slack/telegram) + DNS
# tunneling indicators per aguara DATA_EXFIL_001..006.
DATA_EXFILTRATION_PATTERNS = [
    (
        re.compile(r"curl\s+.*-[dX]\s+.*https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE),
        "Data exfiltration: curl POST/PUT to external URL",
    ),
    (
        re.compile(r"wget\s+.*--post-data.*https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE),
        "Data exfiltration: wget POST to external URL",
    ),
    (
        re.compile(r"fetch\s*\(\s*['\"]https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE),
        "Data exfiltration: fetch() to external URL",
    ),
    (
        re.compile(r"requests?\.\s*(?:post|put|patch)\s*\(\s*['\"]https?://(?!localhost|127\.0\.0\.1)", re.IGNORECASE),
        "Data exfiltration: Python requests POST to external URL",
    ),
    (
        re.compile(r"urllib\.\s*request\.\s*urlopen.*https?://(?!localhost|127\.0\.0\.1)"),
        "Data exfiltration: urllib to external URL",
    ),
    # Phase 2c — Webhook hosts (discord/slack/telegram/etc.). These are
    # almost always exfiltration channels; legitimate plugins should
    # configure them via env var, not hardcode the URL.
    (
        re.compile(
            r"https?://(?:discord\.com/api/webhooks|hooks\.slack\.com/services|"
            r"api\.telegram\.org/bot|outlook\.office\.com/webhook|"
            r"events\.pagerduty\.com|hooks\.zapier\.com|api\.sendgrid\.com|"
            r"webhook\.site|requestbin\.com|pipedream\.com|n8n\.cloud|webhookrelay\.com)",
            re.IGNORECASE,
        ),
        "Data exfiltration: hardcoded webhook host (RC-17 — discord/slack/telegram/etc.)",
    ),
    # Phase 2c — DNS tunneling pattern (long subdomain queries with base64-shape labels)
    (
        re.compile(r"\b[A-Za-z0-9+/=]{40,}\.(?:[a-z0-9-]{1,63}\.){0,4}[a-z]{2,}\b"),
        "Data exfiltration: long-label DNS pattern (RC-18/19 — possible DNS tunneling)",
    ),
]

# Supply chain attack patterns — downloading and executing code
# Phase 2d (RC-26/27/28) added redirect operators (`>`), command separators
# (`;`/`&&`), pip --no-deps + unhashed installs, and lifecycle script targeting.
SUPPLY_CHAIN_PATTERNS = [
    (re.compile(r"curl\s+.*\|\s*(?:sh|bash|zsh|python|python3|node)\b"), "Supply chain: curl piped to interpreter"),
    (re.compile(r"wget\s+.*\|\s*(?:sh|bash|zsh|python|python3|node)\b"), "Supply chain: wget piped to interpreter"),
    (
        re.compile(r"pip\s+install\s+.*(?:https?://|git\+|--index-url\s+(?!https://pypi))"),
        "Supply chain: pip install from non-PyPI source",
    ),
    (
        re.compile(r"npm\s+install\s+.*(?:https?://|git\+|--registry\s+(?!https://registry\.npmjs))"),
        "Supply chain: npm install from non-registry source",
    ),
    (
        re.compile(r"curl\s+.*-[oO]\s+.*&&\s*(?:chmod|sh|bash|python|node)\b"),
        "Supply chain: curl download then execute",
    ),
    (
        re.compile(r"wget\s+.*-[oO]\s+.*&&\s*(?:chmod|sh|bash|python|node)\b"),
        "Supply chain: wget download then execute",
    ),
    # Phase 2d RC-26 — separator-based execution (no pipe, but `;`/`&&` connect)
    (
        re.compile(r"curl\s+\S+\s+>\s+\S+\s*[;&]+\s*(?:sh|bash|zsh|python|node)\b"),
        "Supply chain: curl > file ; sh file (redirect-then-execute, RC-26)",
    ),
    (
        re.compile(r"(?:curl|wget)\s+\S+\s*[;&]{1,2}\s*(?:sh|bash|python|node)\s+\S+"),
        "Supply chain: curl/wget then exec via separator (RC-26)",
    ),
    # Phase 2d RC-28 — pip install without pinning / no hash check
    (
        re.compile(r"pip\s+install\s+.*--no-deps\b.*(?!--require-hashes)", re.IGNORECASE),
        "Supply chain: pip install --no-deps without --require-hashes (RC-28)",
    ),
    (
        re.compile(r"pip\s+install\s+--upgrade\s+--user\b.*(?!--require-hashes)", re.IGNORECASE),
        "Supply chain: pip install --upgrade --user without hash pinning (RC-28)",
    ),
    # Phase 2d RC-27 — lifecycle scripts in package.json (preinstall/postinstall
    # invoking shell commands). Real attack vector for npm supply-chain.
    (
        re.compile(
            r'"(?:preinstall|postinstall|prepare|preuninstall|install)"\s*:\s*'
            r'"(?:.*?(?:curl|wget|sh\s+|bash\s+|node\s+\S+\.js|python\s+\S+))',
            re.IGNORECASE,
        ),
        "Supply chain: package.json lifecycle script invokes downloader/interpreter (RC-27)",
    ),
    # Phase 2d RC-27 — process-substitution + `-enc` (PowerShell base64 exec)
    (
        re.compile(r"powershell(?:\.exe)?\s+-(?:enc|EncodedCommand|e)\s+[A-Za-z0-9+/=]{20,}", re.IGNORECASE),
        "Supply chain: PowerShell -enc base64 payload (RC-27)",
    ),
]

# Credential harvesting patterns — reading sensitive credential files
# Note: ~/.claude/ is EXCLUDED (legitimate for plugins)
# Phase 2c (RC-20) added Claude MEMORY/USER files, browser keystores, and
# Windows vault per vexscan FILE-001..005.
CREDENTIAL_HARVEST_PATTERNS = [
    (re.compile(r"~/\.ssh/|/\.ssh/|SSH_KEY|id_rsa|id_ed25519"), "Credential access: SSH key file reference"),
    (
        re.compile(r"~/\.aws/|/\.aws/|AWS_SECRET|aws_secret_access_key", re.IGNORECASE),
        "Credential access: AWS credentials reference",
    ),
    (
        re.compile(r"~/\.gitconfig|/\.gitconfig|GIT_TOKEN|GITHUB_TOKEN", re.IGNORECASE),
        "Credential access: Git credentials reference",
    ),
    (
        re.compile(r"~/\.npmrc|/\.npmrc|NPM_TOKEN|npm_token", re.IGNORECASE),
        "Credential access: npm credentials reference",
    ),
    (
        re.compile(r"~/\.docker/|/\.docker/config\.json|DOCKER_PASSWORD", re.IGNORECASE),
        "Credential access: Docker credentials reference",
    ),
    (
        re.compile(r"~/\.kube/|/\.kube/config|KUBECONFIG", re.IGNORECASE),
        "Credential access: Kubernetes config reference",
    ),
    (re.compile(r"~/\.gnupg/|/\.gnupg/|GPG_PASSPHRASE", re.IGNORECASE), "Credential access: GPG keyring reference"),
    (
        re.compile(r"(?:keychain|keyring|credential.?store|password.?store)", re.IGNORECASE),
        "Credential access: system keystore reference",
    ),
    # Phase 2c (RC-20) — Claude memory/agent files (MEMORY.md, CLAUDE.md user
    # mode, ~/.claude/USER.md). Reading these from a plugin can extract user
    # context and history. Plugin-shipped MEMORY.md is its own — only USER /
    # global memory paths trigger.
    (
        re.compile(r"~?/?\.claude/(?:USER|MEMORY)\.md|~/\.claude/projects/[^/]+/MEMORY\.md", re.IGNORECASE),
        "Credential access: Claude user memory / USER.md (RC-20)",
    ),
    # Phase 2c (RC-20) — Browser keystores (Login Data, Cookies, Local State)
    (
        re.compile(
            r"(?:Library/Application\s+Support/(?:Google/Chrome|Brave|Edge|Vivaldi|Arc)/[^\s]*"
            r"(?:Login\s+Data|Cookies|Local\s+State|Web\s+Data)|"
            r"~/\.config/(?:google-chrome|chromium|BraveSoftware)/[^\s]*Login\s+Data|"
            r"AppData/Local/Google/Chrome/User\s+Data/[^\s]*Login\s+Data)",
            re.IGNORECASE,
        ),
        "Credential access: browser keystore (RC-20)",
    ),
    # Phase 2c (RC-20) — Firefox profile credentials
    (
        re.compile(r"\.mozilla/firefox/[^\s]*(?:logins\.json|key[34]?\.db)", re.IGNORECASE),
        "Credential access: Firefox keystore (RC-20)",
    ),
    # Phase 2c (RC-20) — Windows credential vault / DPAPI
    (
        re.compile(
            r"(?:vaultcli\.dll|CryptUnprotectData|"
            r"Microsoft/Credentials|Microsoft/Vault|"
            r"vaultcmd(?:\.exe)?\s+(?:/list|/listcreds))",
            re.IGNORECASE,
        ),
        "Credential access: Windows credential vault (RC-20)",
    ),
]

# Sandbox escape patterns — bypassing safety controls
SANDBOX_ESCAPE_PATTERNS = [
    (re.compile(r"--no-verify\b"), "Sandbox escape: --no-verify bypasses git hooks"),
    (
        re.compile(r"git\s+config\s+.*(?:core\.hooksPath|core\.autocrlf|safe\.directory)"),
        "Sandbox escape: git config modification",
    ),
    (re.compile(r"--dangerously-skip-permissions\b"), "Permission escalation: dangerouslySkipPermissions flag"),
    (re.compile(r"chmod\s+(?:777|a\+rwx)\b"), "Sandbox escape: chmod 777 (world-writable)"),
    (
        re.compile(r"(?:disable|bypass|skip)\s*(?:all\s+)?(?:hooks?|guard|safety|protection|sandbox)", re.IGNORECASE),
        "Sandbox escape: safety bypass language",
    ),
    # Phase 2d RC-34 — Reverse-shell variants in 7 languages + msfvenom + socat
    (
        re.compile(
            r"\bbash\s+-i\s*>&\s*/dev/tcp/[\d.]+/\d+\s*0>&1|"
            r"\bsh\s+-i\s*>&\s*/dev/tcp/[\d.]+/\d+",
            re.IGNORECASE,
        ),
        "Sandbox escape: bash/sh reverse shell via /dev/tcp (RC-34)",
    ),
    (
        re.compile(
            r"\bpython3?\s+-c\s+['\"]?\s*import\s+(?:socket|subprocess).*"
            r"(?:socket\.socket|connect|dup2|fork)",
        ),
        "Sandbox escape: Python reverse shell (RC-34)",
    ),
    (
        re.compile(r"\bperl\s+-[eE]\s+['\"]?\s*use\s+Socket.*connect"),
        "Sandbox escape: Perl reverse shell (RC-34)",
    ),
    (
        re.compile(r"\bruby\s+-[rR]?[a-z]*\s+-[eE]\s+['\"]?.*TCPSocket\.(?:open|new)"),
        "Sandbox escape: Ruby reverse shell (RC-34)",
    ),
    (
        re.compile(r"\bphp\s+-r\s+['\"]?\s*\$sock\s*=\s*fsockopen"),
        "Sandbox escape: PHP reverse shell (RC-34)",
    ),
    (
        re.compile(r"\blua\s+-e\s+['\"]?.*socket\.tcp\(\)"),
        "Sandbox escape: Lua reverse shell (RC-34)",
    ),
    (
        re.compile(r"\bsocat\s+(?:tcp[46]?-listen|exec):", re.IGNORECASE),
        "Sandbox escape: socat reverse shell / bind shell (RC-34)",
    ),
    (
        re.compile(r"\bmsfvenom\s+-p\s+\S+\s+(?:lhost|rhost)=", re.IGNORECASE),
        "Sandbox escape: msfvenom payload generator (RC-34)",
    ),
    # Phase 2d RC-35 — SUID +s and octal SUID variants
    (
        re.compile(r"\bchmod\s+(?:[+]s|u\+s|g\+s|4[7-9][0-9]{2}|2[7-9][0-9]{2}|6[7-9][0-9]{2})\b"),
        "Sandbox escape: SUID / SGID set on file (RC-35 — escalation vector)",
    ),
    # Phase 2d RC-38 — Destructive file/disk operations
    (
        re.compile(r"\bwipefs\s+-a\s+/dev/", re.IGNORECASE),
        "Sandbox escape: wipefs -a on a block device (RC-38)",
    ),
    (
        re.compile(r"\bshred\s+-(?:[a-z]+\s+)?/(?!tmp/)", re.IGNORECASE),
        "Sandbox escape: shred against absolute path (RC-38)",
    ),
    (
        re.compile(r":\(\)\{\s*:\s*\|\s*:\s*&\s*\};:", re.IGNORECASE),
        "Sandbox escape: classic fork bomb (RC-38)",
    ),
    (
        re.compile(r"\bformat\s+[A-Z]:\s*/Q?\s*/Y", re.IGNORECASE),
        "Sandbox escape: Windows FORMAT command (RC-38)",
    ),
    # Phase 2d RC-36 — Symlink / hardlink to system-sensitive files.
    # Patterns accept `ln -s <source> <target>` and `ln <source> <target>`.
    # `<source>` and `<target>` can be any non-whitespace path; the regex
    # checks that the TARGET is a system-sensitive file.
    (
        re.compile(r"\bln\s+-s\s+\S+\s+/etc/(?:passwd|shadow|sudoers)\b", re.IGNORECASE),
        "Sandbox escape: symlink to /etc/passwd|shadow|sudoers (RC-36)",
    ),
    (
        re.compile(r"\bln\s+(?!-s)(?:-[a-zA-Z]+\s+)?\S+\s+/etc/(?:passwd|shadow|sudoers)\b", re.IGNORECASE),
        "Sandbox escape: HARD LINK to /etc/passwd|shadow|sudoers (RC-36)",
    ),
    (
        re.compile(r"\bln\s+-s\s+\S+\s+/Library/LaunchDaemons/", re.IGNORECASE),
        "Sandbox escape: symlink into /Library/LaunchDaemons (RC-36)",
    ),
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


def is_ai_facing_markdown(file_path: str) -> bool:
    """Check if a markdown file contains AI-facing content (not just documentation).

    AI-facing markdown: skills, agents, commands, rules, references loaded by agents.
    These files are part of the attack surface — their content becomes system prompts,
    tool instructions, or agent behavior definitions that Claude executes.

    Documentation markdown (README, CHANGELOG, docs/) contains examples that would
    cause false positives and is NOT part of the attack surface.
    """
    file_normalized = file_path.lower().replace("\\", "/")

    # Documentation files — NOT AI-facing
    doc_files = {"readme.md", "changelog.md", "contributing.md", "security.md", "license.md"}
    basename = file_normalized.rsplit("/", 1)[-1] if "/" in file_normalized else file_normalized
    if basename in doc_files:
        return False

    # Documentation directories — NOT AI-facing
    doc_dirs = {"/docs/", "/docs_dev/", "/examples/", "/samples/"}
    if any(d in file_normalized for d in doc_dirs):
        return False

    # AI-facing directories — MUST be scanned
    ai_dirs = {
        "/skills/",
        "/agents/",
        "/commands/",
        "/rules/",
        "/references/",  # Reference files loaded by agents
        "/output-styles/",  # Output style instructions
    }
    if any(d in file_normalized for d in ai_dirs):
        return True

    # SKILL.md anywhere is AI-facing
    if basename == "skill.md":
        return True

    # Default: treat other .md files as documentation (err on side of caution for FPs)
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
    is_test_file = (
        "test_" in file_lower
        or "_test.py" in file_lower
        or "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
        or "/conftest.py" in file_normalized
        or file_normalized == "conftest.py"
    )

    # Determine if file is a validator script - they contain intentional patterns
    is_validator = is_validator_script(file_path)

    # Skip all injection checks for validator scripts (they define patterns)
    if is_validator:
        return 0

    # Python files never use backtick command substitution — backticks are RST/docstring formatting
    is_python_file = file_lower.endswith(".py")

    # Skip command substitution checks for shell scripts (expected), docs markdown, and tests
    # AI-facing markdown (skills, agents) uses backticks for formatting — skip command-sub only
    skip_command_sub = is_shell_script or (is_markdown and not is_ai_facing_markdown(file_path)) or is_test_file
    # For AI-facing markdown, still skip backtick patterns (they're code formatting)
    if is_markdown and is_ai_facing_markdown(file_path):
        skip_command_sub = True  # Backticks in .md are always formatting

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

    # Skip path checks for documentation markdown — contains examples
    # But scan AI-facing markdown (skills, agents, commands) — these are the attack surface
    if file_lower.endswith((".md", ".mdx", ".markdown")) and not is_ai_facing_markdown(file_path):
        return 0

    # Skip path checks for test files - they contain example data
    file_normalized = file_lower.replace("\\", "/")
    if (
        "test_" in file_lower
        or "_test.py" in file_lower
        or "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
    ):
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
                    if "Absolute Unix" in msg and (
                        "#!/" in line
                        or "help" in stripped.lower()
                        or "epilog" in stripped.lower()
                        or stripped.startswith(("'", '"', "f'", 'f"', "r'", 'r"'))
                    ):
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
    if (
        "test_" in file_lower
        or "_test.py" in file_lower
        or "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
    ):
        return 0

    # Skip documentation markdown — contains example credentials for illustration
    # But scan AI-facing markdown (skills, agents) — secrets in system prompts are real leaks
    if file_lower.endswith((".md", ".mdx", ".markdown")) and not is_ai_facing_markdown(file_path):
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

    # Skip documentation markdown — contains example paths
    # But scan AI-facing markdown — hardcoded user paths in prompts break portability
    if file_lower.endswith((".md", ".mdx", ".markdown")) and not is_ai_facing_markdown(file_path):
        return 0

    # Skip test files
    file_normalized = file_lower.replace("\\", "/")
    if (
        "test_" in file_lower
        or "_test.py" in file_lower
        or "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
    ):
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
    # Skip documentation markdown — contains code examples
    # But scan AI-facing markdown — exfiltration patterns in prompts are real threats
    if file_lower.endswith((".md", ".mdx", ".markdown")) and not is_ai_facing_markdown(file_path):
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
                    report.warning(
                        f"{msg} (valid for worktree agents, verify intent): {stripped[:80]}", file_path, line_num
                    )
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
                            report.major(
                                f"Hook abuse: PreToolUse HTTP hook sends to external URL: {url[:60]}",
                                "hooks/hooks.json",
                            )
                            issues_found += 1

                    # PostToolUse hooks sending tool output externally
                    if event_name == "PostToolUse" and hook_type == "http" and url:
                        if not any(loc in url for loc in ("localhost", "127.0.0.1", "::1")):
                            report.major(
                                f"Hook abuse: PostToolUse HTTP hook may exfiltrate tool output to: {url[:60]}",
                                "hooks/hooks.json",
                            )
                            issues_found += 1

                    # Command hooks with suspicious commands
                    if cmd:
                        for sc_pattern, sc_msg in SUPPLY_CHAIN_PATTERNS + DATA_EXFILTRATION_PATTERNS:
                            if sc_pattern.search(cmd):
                                report.critical(
                                    f"Hook abuse ({event_name}): {sc_msg} in hook command", "hooks/hooks.json"
                                )
                                issues_found += 1

                    # Excessive timeout (> 1 hour) is suspicious
                    timeout = hook.get("timeout", 0)
                    if isinstance(timeout, (int, float)) and timeout > 3600:
                        report.warning(
                            f"Hook has excessive timeout ({timeout}s) on {event_name} — may indicate long-running exfiltration",
                            "hooks/hooks.json",
                        )
                        issues_found += 1

    except (ValueError, OSError):
        pass
    return issues_found


def check_mcp_abuse(plugin_path: Path, report: ValidationReport) -> int:
    """Check MCP config for non-localhost servers (WARNING — many valid remote MCPs).

    Phase 2e (RC-45) added detection for socat / php / ruby / nc / ncat in the
    `command` field — these are interpreter binaries that have no place running
    as an MCP server and almost always indicate a reverse-shell wrapper.
    """
    mcp_file = plugin_path / ".mcp.json"
    if not mcp_file.exists():
        return 0

    issues_found = 0
    try:
        import json as _json

        data = _json.loads(mcp_file.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", data) if isinstance(data, dict) else {}

        # Phase 2e RC-45 — interpreter / network-tool binaries that have no
        # legitimate place in an MCP `command` field.
        DANGEROUS_MCP_COMMANDS = frozenset({
            "socat", "ncat", "nc", "netcat",
            "php", "ruby", "perl", "lua",
            "telnet", "rsh", "ssh-keyscan",
        })

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

            # Phase 2e RC-45 — dangerous interpreter / net binary as command
            cmd_basename = cmd.split("/")[-1].lower() if cmd else ""
            if cmd_basename in DANGEROUS_MCP_COMMANDS:
                report.critical(
                    f"RC-45: MCP server '{name}' command is '{cmd_basename}' — "
                    f"interpreter / network binary, almost certainly a reverse-shell wrapper",
                    ".mcp.json",
                )
                issues_found += 1

            for sc_pattern, sc_msg in SUPPLY_CHAIN_PATTERNS:
                if sc_pattern.search(full_cmd):
                    report.critical(f"MCP server '{name}': {sc_msg}", ".mcp.json")
                    issues_found += 1

    except (ValueError, OSError):
        pass
    return issues_found


def check_permission_escalation(plugin_path: Path, report: ValidationReport) -> int:
    """Check for permission escalation in plugin manifest and agent frontmatter (WARNING).

    Phase 2e (RC-61, RC-62) extended to flag:
    * `permissionMode: bypassPermissions` — RC-62 (was missing)
    * `dangerouslyDisableSandbox` — RC-61 sandbox disable
    * TLS-bypass env vars (NODE_TLS_REJECT_UNAUTHORIZED=0, PYTHONHTTPSVERIFY=0)
    """
    issues_found = 0

    # Check plugin.json for overly broad tool permissions
    manifest = plugin_path / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            import json as _json

            data = _json.loads(manifest.read_text(encoding="utf-8"))
            # Check if plugin requests dangerous permission modes
            perm_mode = data.get("permissionMode", "")
            # Phase 2e RC-62 — bypassPermissions explicit catch
            if perm_mode in ("dangerouslySkipPermissions", "bypass", "bypassPermissions"):
                report.warning(
                    f"Permission escalation: plugin.json requests permissionMode '{perm_mode}' "
                    f"(RC-62 — bypassPermissions removes the user's safety gate)",
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
                        fm_normalized = fm.lower().replace("_", "").replace("-", "")
                        # Phase 2e RC-61 — also catch dangerouslyDisableSandbox
                        if "dangerouslyskippermissions" in fm_normalized:
                            report.warning(
                                "Permission escalation: agent requests dangerouslySkipPermissions "
                                "(valid for worktree agents, verify intent)",
                                f"agents/{agent_file.name}",
                            )
                            issues_found += 1
                        if "dangerouslydisablesandbox" in fm_normalized:
                            report.major(
                                "RC-61: agent requests dangerouslyDisableSandbox — disables the runtime "
                                "sandbox; plugins should never need this",
                                f"agents/{agent_file.name}",
                            )
                            issues_found += 1
                        # Phase 2e RC-61 — TLS-bypass env vars
                        if "node_tls_reject_unauthorized" in fm_normalized.replace(":", "") or \
                           "pythonhttpsverify=0" in fm_normalized:
                            report.major(
                                "RC-61: agent declares TLS-bypass env var (NODE_TLS_REJECT_UNAUTHORIZED / "
                                "PYTHONHTTPSVERIFY) — disables certificate validation",
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

    for root, _dirs, files in gi.walk(plugin_path):
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

    for root, _dirs, files in gi.walk(plugin_path):
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

    for root, _dirs, files in gi.walk(plugin_path):
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
# IDE Configuration File Scanner
# =============================================================================

# IDE-specific configuration files that commonly leak secrets.
# gi.walk() defaults to skip_hidden=True, so dot-prefixed directories like
# .vscode, .idea, .cursor, .zed are NEVER visited by scan_all_files. We must
# scan them explicitly here. Entries may be literal file paths or glob
# patterns (e.g. ".idea/*.xml").
IDE_CONFIG_PATHS: tuple[str, ...] = (
    ".vscode/settings.json",
    ".vscode/tasks.json",
    ".vscode/launch.json",
    ".idea/workspace.xml",
    ".idea/*.xml",
    ".cursor/mcp.json",
    ".cursor/settings.json",
    ".zed/settings.json",
    ".zed/tasks.json",
)


def scan_ide_config_files(plugin_path: Path, report: ValidationReport) -> dict[str, int]:
    """Scan IDE configuration files for secrets.

    IDE config directories (.vscode, .idea, .cursor, .zed) are hidden and
    therefore skipped by the default gi.walk() used in scan_all_files. This
    function walks them explicitly and runs the existing SECRET_PATTERNS regex
    suite via scan_for_secrets — matching the severity used for other secret
    leaks (CRITICAL).

    Respects .gitignore: if a matched IDE config file is gitignored, it is
    skipped (gitignored secrets are not shipped to git / the marketplace).

    Args:
        plugin_path: Plugin root directory
        report: ValidationReport to append findings to

    Returns:
        Dict with keys: files_scanned, files_skipped, secret_issues
    """
    stats = {"files_scanned": 0, "files_skipped": 0, "secret_issues": 0}

    gi = get_gitignore_filter(plugin_path)
    # Deduplicate — glob patterns can overlap with literal filenames
    # (e.g. ".idea/*.xml" matches ".idea/workspace.xml").
    seen: set[Path] = set()

    for entry in IDE_CONFIG_PATHS:
        # Path.glob handles both literal paths (returning 0 or 1 match) and
        # glob patterns (returning any matches). Using glob() uniformly keeps
        # the iteration logic simple.
        for match in plugin_path.glob(entry):
            if not match.is_file():
                continue
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)

            # Skip gitignored files — secrets in gitignored files are not
            # shipped, so flagging them would only create noise.
            if gi.is_ignored(match):
                stats["files_skipped"] += 1
                continue

            # Skip binary files defensively (XML/JSON should always be text,
            # but e.g. .idea/ may contain non-config files if the glob widens).
            if is_binary_file(match):
                stats["files_skipped"] += 1
                continue

            try:
                with open(match, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, PermissionError) as e:
                rel_path_err = str(match.relative_to(plugin_path))
                report.minor(f"Cannot read IDE config file: {rel_path_err} ({e})")
                stats["files_skipped"] += 1
                continue

            rel_path = str(match.relative_to(plugin_path))
            stats["files_scanned"] += 1

            # Re-use the existing secret regex suite. scan_for_secrets skips
            # validator scripts and test files, and non-AI markdown — none of
            # those guards apply to IDE config paths (.json/.xml), so the
            # suite runs the regexes against the file content directly.
            stats["secret_issues"] += scan_for_secrets(content, rel_path, report)

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
        report.warning(
            "cc-audit: npx not found — 100+ additional security rules skipped. Install Node.js to enable: https://nodejs.org/"
        )
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
                "npx",
                "--yes",
                "@cc-audit/cc-audit",
                "check",
                str(plugin_path),
                "-t",
                "plugin",
                "--format",
                "json",
                "--output",
                tmp_path,
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
        report.warning("cc-audit: npx command failed — external audit skipped")
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


# =============================================================================
# Tirith External Scanner Integration (Check #17)
# =============================================================================
#
# Tirith (https://github.com/sheeki03/tirith, AGPL-3.0) is invoked as an
# external binary — no source code from tirith is copied or linked into cpv,
# so the AGPL terms do not propagate. Only the SCAN feature is used; cpv
# never installs shell hooks, MCP gateways, or AI-tool setup configs.

# Official container image (any platform with Docker available)
TIRITH_IMAGE = "ghcr.io/sheeki03/tirith"

# Auto-install order: brew on macOS, then npm/cargo as cross-platform fallbacks.
# Each entry is a (probe-binary, install-command) pair. The probe must be on
# PATH; the install command runs only if the probe succeeds and the user has
# not opted out via CPV_NO_TIRITH_INSTALL=1.
_TIRITH_INSTALLERS: list[tuple[str, list[str]]] = [
    ("brew", ["brew", "install", "sheeki03/tap/tirith"]),
    ("npm", ["npm", "install", "-g", "tirith"]),
    ("cargo", ["cargo", "install", "tirith"]),
]


def _resolve_tirith_runner() -> tuple[list[str], str] | None:
    """Pick how to invoke tirith without modifying the user's environment.

    Resolution order, per the user constraint that we should prefer remote
    execution and only install as a last resort:

    1. ``tirith`` already on PATH       -> direct invocation
    2. ``docker`` on PATH               -> ``docker run --rm`` against the
                                           official container image (zero
                                           install footprint — image is
                                           pulled to the local Docker cache
                                           on first use, but nothing lands
                                           on the host outside Docker)
    3. ``nix`` on PATH                  -> ``nix run github:sheeki03/tirith``
                                           (also runs without leaving binaries
                                           in the user's shell PATH)
    4. Auto-install (brew/npm/cargo)    -> only if no remote path worked AND
                                           ``CPV_NO_TIRITH_INSTALL`` is unset

    Returns a ``(prefix_args, mode_label)`` tuple. The caller appends the
    tirith subcommand and arguments to ``prefix_args``. Returns ``None`` when
    no path is reachable (caller emits a single advisory WARNING and skips).
    """
    if shutil.which("tirith"):
        return (["tirith"], "local")

    if shutil.which("docker"):
        # The plugin path is mounted read-only inside the container at /scan.
        # The mount path is appended by the caller because it depends on the
        # specific plugin_path being scanned.
        return (["docker", "run", "--rm", "-i", TIRITH_IMAGE], "docker")

    if shutil.which("nix"):
        return (["nix", "run", "github:sheeki03/tirith", "--"], "nix")

    # No remote path — fall through to install attempt.
    if os.environ.get("CPV_NO_TIRITH_INSTALL", "").strip().lower() in {"1", "true", "yes"}:
        return None

    for probe, install_cmd in _TIRITH_INSTALLERS:
        if not shutil.which(probe):
            continue
        try:
            subprocess.run(install_cmd, capture_output=True, text=True, timeout=300, check=False)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
        # After install, re-probe PATH (npm/cargo write to ~/.npm/bin or
        # ~/.cargo/bin which may not be on PATH for the current process).
        if shutil.which("tirith"):
            return (["tirith"], f"installed-{probe}")

    return None


def check_tirith_scanner(plugin_path: Path, report: ValidationReport) -> int:
    """Run tirith's scan feature against the plugin and surface findings.

    Tirith is an external scanner with rules cpv does not natively cover:
    homograph domains, ANSI / bidi / zero-width injection, hidden Unicode,
    config-file prompt-injection comments, and supply-chain pipe-to-shell
    patterns in scripts. Only the ``tirith scan`` subcommand is invoked; the
    scanner never touches the user's shell hooks, MCP configs, or AI-tool
    setup state.

    Returns the number of issues converted into report findings. Returns 0
    when tirith is unavailable (and emits a single advisory WARNING) or when
    the scan completes with no findings.
    """
    runner = _resolve_tirith_runner()
    if runner is None:
        report.warning(
            "tirith: scanner not available and auto-install failed or disabled "
            "(CPV_NO_TIRITH_INSTALL). Install via 'brew install sheeki03/tap/tirith', "
            "'npm install -g tirith', 'cargo install tirith', or run with Docker "
            "available so 'docker run --rm ghcr.io/sheeki03/tirith ...' can be used."
        )
        return 0

    prefix, mode = runner

    # Build the scan command. Docker mode bind-mounts the plugin path to /scan
    # inside the container — same convention as the cc-audit integration uses
    # for npx temp paths.
    if mode == "docker":
        # Insert the bind-mount BEFORE the image name (-v image is wrong).
        # prefix is ["docker", "run", "--rm", "-i", TIRITH_IMAGE]
        cmd = (
            ["docker", "run", "--rm", "-v", f"{plugin_path}:/scan:ro", TIRITH_IMAGE]
            + ["scan", "/scan", "--format", "json", "--ci"]
        )
    else:
        cmd = prefix + ["scan", str(plugin_path), "--format", "json", "--ci"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    except subprocess.TimeoutExpired:
        report.warning(f"tirith ({mode}) timed out after 180s — scan aborted")
        return 0
    except FileNotFoundError:
        report.warning(f"tirith ({mode}): runner binary disappeared between probe and exec — scan skipped")
        return 0

    # Parse JSON. Per tirith's docs, ``scan --format json`` writes JSON to
    # stdout regardless of exit code. Exit codes: 0 = safe, 1 = block (high),
    # 2 = warn, 3 = warn-with-ack. We treat all of them as informational
    # signals and rely on the JSON content for the actual findings.
    raw = result.stdout.strip()
    if not raw:
        if result.returncode == 0:
            report.passed(f"tirith ({mode}): no findings (external scan clean)")
        else:
            err = (result.stderr or "").strip().splitlines()[-1:] or [""]
            report.info(f"tirith ({mode}) returned exit {result.returncode} with no JSON output: {err[0][:100]}")
        return 0

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        report.info(f"tirith ({mode}): could not parse JSON output ({e}); first 100 chars: {raw[:100]!r}")
        return 0

    # Tirith's scan JSON varies between versions — try a few shapes:
    # * top-level list of findings
    # * {"findings": [...]} or {"results": [...]} or {"verdicts": [...]}
    # * SARIF-shape {"runs": [{"results": [...]}]}
    findings: list = []
    if isinstance(data, list):
        findings = data
    elif isinstance(data, dict):
        for key in ("findings", "results", "verdicts", "issues"):
            v = data.get(key)
            if isinstance(v, list):
                findings = v
                break
        if not findings and isinstance(data.get("runs"), list):
            for run in data["runs"]:
                if isinstance(run, dict) and isinstance(run.get("results"), list):
                    findings.extend(run["results"])

    if not findings:
        if result.returncode == 0:
            report.passed(f"tirith ({mode}): no findings (external scan clean)")
        return 0

    # Map tirith verdict / severity strings to cpv levels. Tirith documents
    # high / medium / low / info severities and Allow/Block/Warn/WarnAck
    # verdicts; we treat Block + high as MAJOR (not CRITICAL — tirith findings
    # are advisory until the user confirms them; cpv stays conservative on its
    # own findings).
    severity_map = {
        "critical": "critical",
        "high": "major",
        "block": "major",
        "medium": "minor",
        "warn": "minor",
        "warnack": "minor",
        "low": "warning",
        "info": "info",
        "informational": "info",
        "allow": "info",
    }

    issues_found = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        sev_raw = (
            finding.get("severity")
            or finding.get("level")
            or finding.get("verdict")
            or finding.get("kind")
            or "warn"
        )
        sev = str(sev_raw).strip().lower()
        cpv_level = severity_map.get(sev, "warning")
        report_fn = getattr(report, cpv_level, report.warning)

        rule_id = finding.get("rule") or finding.get("ruleId") or finding.get("rule_id") or finding.get("code") or "?"
        msg = (
            finding.get("message")
            or finding.get("description")
            or finding.get("title")
            or finding.get("reason")
            or "tirith finding"
        )
        loc_raw = finding.get("location")
        loc: dict[str, Any] = loc_raw if isinstance(loc_raw, dict) else {}
        file_ref = finding.get("file") or loc.get("file") or finding.get("path") or ""
        line = finding.get("line") or loc.get("line") or 0
        if not isinstance(line, int):
            try:
                line = int(line)
            except (TypeError, ValueError):
                line = 0

        report_fn(f"tirith {rule_id}: {str(msg)[:120]}", file_ref, line)
        issues_found += 1

    return issues_found


# =============================================================================
# Phase 1 — Critical net-new rule checks (RC-09/10/11/21/29/37/43/47/49/50/67)
# =============================================================================
#
# Each check below scans plugin files for one rule class. All checks use
# the Phase 0 FP-reduction layer:
# * `is_validator_script(rel_path)` — skip CPV's own validator regex sources
# * `effective_severity(level, rel_path)` — RC-84 demotion in test/doc/sample
# * `is_in_fenced_code_block(line_idx, fence_state)` — RC-83 skip-in-fence
# * `has_negation_guard_nearby(content, pos)` — RC-83 negation context
#
# Rule metadata + FP-guard documentation lives in `cpv_validation_common.py`
# under the `RULE_REGISTRY` (RC-101 RuleSchema). This file owns orchestration
# only — patterns and helpers come from the common module.


def _iter_scannable_files(plugin_path: Path):
    """Yield (file_path, rel_path, content) for every non-binary scannable file."""
    gi = get_gitignore_filter(plugin_path)
    for root, _dirs, files in gi.walk(plugin_path):
        for filename in files:
            file_path = Path(root) / filename
            if is_binary_file(file_path):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            rel_path = str(file_path.relative_to(plugin_path))
            if is_validator_script(rel_path):
                continue
            yield file_path, rel_path, content


def check_phase1_unicode_rules(plugin_path: Path, report: ValidationReport) -> int:
    """RC-09 (zero-width), RC-10 (TAG block), RC-11 (mixed-script) — all pass."""
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        # RC-09 — zero-width characters
        for line_no, desc in find_zero_width_chars(content):
            level = effective_severity("major", rel_path)
            getattr(report, level)(
                f"RC-09: zero-width Unicode at line {line_no} ({desc})",
                rel_path, line_no,
            )
            issues += 1

        # RC-10 — TAG block (always CRITICAL — no legitimate use)
        for line_no, codepoint in find_tag_block_chars(content):
            level = effective_severity("critical", rel_path)
            getattr(report, level)(
                f"RC-10: TAG character {codepoint} at line {line_no} (AsciiSmuggler vector)",
                rel_path, line_no,
            )
            issues += 1

        # RC-11 — mixed-script (only on identifier-shape tokens to avoid
        # FP on prose that legitimately mixes scripts e.g. "Cyrillic 'а' is U+0430")
        for line_no, line in enumerate(content.split("\n"), start=1):
            for token in re.findall(r"[\w._-]{3,80}", line):
                mixed, reason = has_mixed_script(token)
                if mixed:
                    level = effective_severity("critical", rel_path)
                    getattr(report, level)(
                        f"RC-11: mixed-script identifier '{token}' at line {line_no} ({reason})",
                        rel_path, line_no,
                    )
                    issues += 1
                    break  # one finding per line is enough
    return issues


def check_phase1_credential_rules(plugin_path: Path, report: ValidationReport) -> int:
    """RC-21 — process.env / os.environ bulk harvest."""
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        fence_state = build_fence_state(content)
        for line_no, line in enumerate(content.split("\n"), start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            for pattern in ENV_BULK_HARVEST_PATTERNS:
                if pattern.search(line):
                    level = effective_severity("major", rel_path)
                    getattr(report, level)(
                        f"RC-21: bulk env-var harvest at line {line_no}",
                        rel_path, line_no,
                    )
                    issues += 1
                    break
    return issues


def check_phase1_supply_chain_rules(plugin_path: Path, report: ValidationReport) -> int:
    """RC-29 (.pth executable), RC-37 (GTFOBins/LOLBins), RC-67 (cryptomining)."""
    issues = 0
    for file_path, rel_path, content in _iter_scannable_files(plugin_path):
        # RC-29 — .pth file with import/exec
        if is_pth_with_exec(file_path.name, content):
            level = effective_severity("critical", rel_path)
            getattr(report, level)(
                "RC-29: Python .pth file contains executable lines (import/exec) — runs at every interpreter startup",
                rel_path, 1,
            )
            issues += 1

        fence_state = build_fence_state(content)
        for line_no, line in enumerate(content.split("\n"), start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue

            # RC-37 — GTFOBins / LOLBins
            for pattern in GTFOBIN_LOLBIN_PATTERNS:
                m = pattern.search(line)
                if m and not has_negation_guard_nearby(content, content.find(line) + m.start()):
                    level = effective_severity("critical", rel_path)
                    getattr(report, level)(
                        f"RC-37: GTFOBin/LOLBin pattern at line {line_no}: {m.group(0)[:80]}",
                        rel_path, line_no,
                    )
                    issues += 1
                    break

            # RC-67 — Cryptomining indicators
            for pattern in CRYPTOMINING_PATTERNS:
                m = pattern.search(line)
                if m:
                    level = effective_severity("critical", rel_path)
                    getattr(report, level)(
                        f"RC-67: cryptomining indicator at line {line_no}: {m.group(0)[:80]}",
                        rel_path, line_no,
                    )
                    issues += 1
                    break
    return issues


def check_phase1_evasion_rules(plugin_path: Path, report: ValidationReport) -> int:
    """RC-43 — time-bomb / conditional activation."""
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        fence_state = build_fence_state(content)
        for line_no, line in enumerate(content.split("\n"), start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            for pattern in TIMEBOMB_PATTERNS:
                if pattern.search(line):
                    level = effective_severity("critical", rel_path)
                    getattr(report, level)(
                        f"RC-43: time-bomb / conditional-activation at line {line_no}",
                        rel_path, line_no,
                    )
                    issues += 1
                    break
    return issues


def check_phase1_mcp_rules(plugin_path: Path, report: ValidationReport) -> int:
    """RC-47 (env-var injection), RC-49 (description injection prefilter), RC-50 (tool-name shadowing).

    Reads `.mcp.json` files in the plugin and inspects each declared MCP server.
    Each server may declare:
      - `command` / `args` — the binary to launch
      - `env` — extra env vars passed to the server (RC-47 target)
      - top-level keys are server names; their tool-list (if statically declared
        via a non-standard `tools` block) is RC-49/RC-50 target. The MCP wire
        protocol returns tools dynamically, so we scan only what's declared
        in the manifest.
    """
    issues = 0
    for mcp_path in plugin_path.rglob(".mcp.json"):
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rel_path = str(mcp_path.relative_to(plugin_path))
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            continue
        for server_name, server_cfg in servers.items():
            if not isinstance(server_cfg, dict):
                continue

            # RC-47 — dangerous env keys
            env_block = server_cfg.get("env", {})
            if isinstance(env_block, dict):
                for key in env_block:
                    if key in MCP_DANGEROUS_ENV_KEYS:
                        level = effective_severity("critical", rel_path)
                        getattr(report, level)(
                            f"RC-47: MCP server '{server_name}' sets dangerous env var {key} — "
                            f"RCE on config load via dynamic-loader / runtime-hook hijack",
                            rel_path, 0,
                        )
                        issues += 1

            # RC-49 — description prefilter (declared tools block, if present)
            tools = server_cfg.get("tools", [])
            if isinstance(tools, list):
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    desc = str(tool.get("description", ""))
                    for pattern in MCP_DESCRIPTION_INJECTION_PREFILTER:
                        if pattern.search(desc):
                            level = effective_severity("critical", rel_path)
                            getattr(report, level)(
                                f"RC-49: MCP tool '{tool.get('name', '?')}' description contains "
                                f"prompt-injection signature — consider /cpv-semantic-validation for LLM judgment",
                                rel_path, 0,
                            )
                            issues += 1
                            break

                    # RC-50 — tool-name shadowing
                    tool_name = str(tool.get("name", ""))
                    is_shadow, builtin = is_shadowed_tool_name(tool_name)
                    if is_shadow:
                        level = effective_severity("critical", rel_path)
                        getattr(report, level)(
                            f"RC-50: MCP tool name '{tool_name}' shadows Claude Code built-in '{builtin}' "
                            f"— impersonation vector",
                            rel_path, 0,
                        )
                        issues += 1
    return issues


def check_phase1_all(plugin_path: Path, report: ValidationReport) -> int:
    """Run all Phase 1 critical rule checks and return total finding count."""
    return (
        check_phase1_unicode_rules(plugin_path, report)
        + check_phase1_credential_rules(plugin_path, report)
        + check_phase1_supply_chain_rules(plugin_path, report)
        + check_phase1_evasion_rules(plugin_path, report)
        + check_phase1_mcp_rules(plugin_path, report)
    )


# =============================================================================
# Phase 2e — Cloud IMDS, persistence, generic obfuscation
# =============================================================================
# RC-65 cloud IMDS (with encoding variants), RC-39 persistence (cron / launchd /
# shell rc / Windows registry), RC-70 obfuscated decode-then-exec.


def check_phase3_all(plugin_path: Path, report: ValidationReport) -> int:
    """Phase 3 — single-pass iteration of PHASE3_PATTERNS across plugin files.

    Plus 2 helpers that don't fit the regex catalog:
    * RC-30 typosquatting — Levenshtein lookup on package.json deps + requirements.txt
    * RC-33 compromised-package check — exact-match lookup on the same
    """
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        fence_state = build_fence_state(content)
        for line_no, line in enumerate(content.split("\n"), start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            for rule_id, severity, pattern, msg in PHASE3_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                if has_negation_guard_nearby(content, content.find(line) + m.start()):
                    continue
                level = effective_severity(severity.lower(), rel_path)
                getattr(report, level)(
                    f"{rule_id}: {msg.split(': ', 1)[-1] if ': ' in msg else msg} (line {line_no})",
                    rel_path, line_no,
                )
                issues += 1
                # Keep going — multiple Phase 3 rules can match a single line

    # RC-30 typosquatting + RC-33 compromised packages from manifests
    for manifest_path in list(plugin_path.rglob("package.json")) + list(plugin_path.rglob("requirements*.txt")):
        try:
            text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(manifest_path.relative_to(plugin_path))
        ecosystem = "npm" if manifest_path.name == "package.json" else "pypi"

        # Extract dep names — different shapes per ecosystem
        if ecosystem == "npm":
            try:
                pkg = json.loads(text)
            except json.JSONDecodeError:
                continue
            deps = {}
            for k in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(pkg.get(k), dict):
                    deps.update(pkg[k])
            for dep_name, dep_ver in deps.items():
                if is_compromised_package(dep_name, dep_ver if isinstance(dep_ver, str) else None):
                    report.critical(
                        f"RC-33: dependency '{dep_name}' (version {dep_ver}) is in the compromised-package list",
                        rel, 0,
                    )
                    issues += 1
                is_squat, target = is_typosquat(dep_name, ecosystem)
                if is_squat:
                    report.major(
                        f"RC-30: dependency '{dep_name}' is Levenshtein ≤1 from top-100 package '{target}' "
                        f"(possible typosquat)",
                        rel, 0,
                    )
                    issues += 1
        else:  # pypi requirements.txt
            for raw in text.splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line or line.startswith(("-r ", "--", "-")):
                    continue
                # Take the dep name (before `==`, `>=`, `<`, `[`, `;`)
                name = re.split(r"[<>=!~\[;\s]", line, 1)[0].strip()
                if not name:
                    continue
                if is_compromised_package(name):
                    report.critical(
                        f"RC-33: dependency '{name}' is in the compromised-package list", rel, 0,
                    )
                    issues += 1
                is_squat, target = is_typosquat(name, "pypi")
                if is_squat:
                    report.major(
                        f"RC-30: dependency '{name}' is Levenshtein ≤1 from top-100 package '{target}' "
                        f"(possible typosquat)",
                        rel, 0,
                    )
                    issues += 1
    return issues


def check_phase2e_extras(plugin_path: Path, report: ValidationReport) -> int:
    """RC-65 (cloud IMDS), RC-39 (persistence), RC-70 (obfuscated exec)."""
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        fence_state = build_fence_state(content)

        # RC-65 — Cloud IMDS endpoints (with encoding variants)
        for line_no, line in enumerate(content.split("\n"), start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            for pattern in CLOUD_IMDS_PATTERNS:
                m = pattern.search(line)
                if m and not has_negation_guard_nearby(content, content.find(line) + m.start()):
                    level = effective_severity("major", rel_path)
                    getattr(report, level)(
                        f"RC-65: cloud IMDS endpoint at line {line_no}: {m.group(0)}",
                        rel_path, line_no,
                    )
                    issues += 1
                    break

        # RC-39 — Persistence
        for line_no, line in enumerate(content.split("\n"), start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            for pattern in PERSISTENCE_PATTERNS:
                m = pattern.search(line)
                if m and not has_negation_guard_nearby(content, content.find(line) + m.start()):
                    level = effective_severity("major", rel_path)
                    getattr(report, level)(
                        f"RC-39: persistence pattern at line {line_no}: {m.group(0)[:80]}",
                        rel_path, line_no,
                    )
                    issues += 1
                    break

        # RC-70 — Generic obfuscation with proximity-to-exec
        for line_no, msg in find_obfuscated_exec(content, proximity_lines=3):
            level = effective_severity("critical", rel_path)
            getattr(report, level)(f"RC-70: {msg}", rel_path, line_no)
            issues += 1
    return issues


def validate_security(plugin_path: Path, enable_tirith: bool = True) -> ValidationReport:
    """Run all security validations on a plugin directory.

    This function performs comprehensive security analysis including:
    Traditional: injection, path traversal, secrets, user paths, dangerous files, permissions
    AI-specific: prompt injection, data exfiltration, supply chain, credential harvest,
    sandbox escape, hook abuse, MCP abuse, agent impersonation, permission escalation
    External: cc-audit (npx), tirith (PATH/docker/nix/install fallback, scan-only)

    Args:
        plugin_path: Path to the plugin directory
        enable_tirith: When False, skip the Check #17 tirith pass entirely.
            Useful for offline runs, CI sandboxes that block container pulls,
            or callers that have already run tirith out-of-band.

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

    # Check 3b: IDE config files (.vscode, .idea, .cursor, .zed).
    # These live in hidden directories that gi.walk() skips by default, so
    # scan_all_files never sees them. Running a targeted pass ensures API
    # keys / tokens leaked into IDE task runners or MCP configs are caught.
    ide_stats = scan_ide_config_files(plugin_path, report)
    scan_stats["secret_issues"] += ide_stats["secret_issues"]

    # Report scan statistics
    report.info(
        f"Scanned {scan_stats['files_scanned']} files, "
        f"skipped {scan_stats['files_skipped']} binary files "
        f"(IDE config: {ide_stats['files_scanned']} scanned, {ide_stats['files_skipped']} skipped)"
    )

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

    # --- Phase 1 — Critical net-new rules (RC-09/10/11/21/29/37/43/47/49/50/67) ---
    phase1_issues = check_phase1_all(plugin_path, report)
    if phase1_issues == 0:
        report.passed("No Phase 1 critical-rule findings (RC-09/10/11/21/29/37/43/47/49/50/67)")

    # --- Phase 2e extras — Cloud IMDS, persistence, obfuscated decode-then-exec ---
    phase2e_issues = check_phase2e_extras(plugin_path, report)
    if phase2e_issues == 0:
        report.passed("No Phase 2e extras findings (RC-39 persistence, RC-65 cloud IMDS, RC-70 obfuscated exec)")

    # --- Phase 3 — ~30 MAJOR net-new rules ---
    phase3_issues = check_phase3_all(plugin_path, report)
    if phase3_issues == 0:
        report.passed("No Phase 3 findings (~30 MAJOR net-new rules)")

    # --- External scanners (optional) ---

    # Check 16: cc-audit external scanner (100+ rules, non-blocking if unavailable)
    check_cc_audit(plugin_path, report)

    # Check 17: tirith external scanner (terminal-security rules, scan-only).
    # Resolution order: PATH -> docker -> nix -> auto-install (brew/npm/cargo).
    # Set CPV_NO_TIRITH_INSTALL=1 to disable the install fallback. Pass
    # enable_tirith=False (or --no-tirith on the CLI) to skip the check
    # entirely.
    if enable_tirith:
        check_tirith_scanner(plugin_path, report)

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
  16. cc-audit external scanner (npx, optional)
  17. tirith external scanner (PATH/docker/nix/auto-install; --no-tirith to skip;
      CPV_NO_TIRITH_INSTALL=1 to disable install fallback)

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
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )
    parser.add_argument(
        "--bare-folder",
        action="store_true",
        help=(
            "Bypass the .claude-plugin/ precondition. Use to scan a bare skill or "
            "content folder that is not wrapped in a Claude Code plugin tree."
        ),
    )
    parser.add_argument(
        "--no-tirith",
        action="store_true",
        help=(
            "Skip the tirith external scanner (Check #17). Use offline, in CI "
            "sandboxes that block container pulls, or when tirith ran out of band."
        ),
    )

    args = parser.parse_args()

    # Resolve to absolute path so relative_to() works correctly
    plugin_path = args.plugin_path.resolve()

    # Verify this is a plugin directory
    if not plugin_path.is_dir():
        print(f"Error: {plugin_path} is not a directory", file=sys.stderr)
        return 1
    if not args.bare_folder and not (plugin_path / ".claude-plugin").is_dir():
        print(
            f"Error: No Claude Code plugin found at {plugin_path}\n"
            "Expected a .claude-plugin/ directory. Use --bare-folder to scan a "
            "skill folder or any other directory tree without that precondition.",
            file=sys.stderr,
        )
        return 1

    # Run validation
    report = validate_security(plugin_path, enable_tirith=not args.no_tirith)

    # Output results
    if args.json:
        output = report.to_dict()
        output["plugin_path"] = str(plugin_path)
        print(json.dumps(output, indent=2))
    elif args.report:

        def _print_full(report, verbose=False):
            print_report_summary(report, "Security Validation Report")
            print_results_by_level(report, verbose=verbose)

        save_report_and_print_summary(
            report, Path(args.report), "Security Validation", _print_full, args.verbose, plugin_path=args.plugin_path
        )
    else:
        print_results_by_level(report, verbose=args.verbose)
        print_report_summary(report, title=f"Security Validation: {plugin_path.name}")

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
