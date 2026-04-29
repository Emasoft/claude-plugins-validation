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
import hashlib
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
    PHASE4_PATTERNS,
    SECRET_PATTERNS,
    TIMEBOMB_PATTERNS,
    USER_PATH_PATTERNS,
    ValidationReport,
    build_fence_state,
    disposition,
    effective_severity,
    find_obfuscated_exec,
    find_stemmed_injection_signal,
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
    (
        re.compile(r"\$\([^)]+\)"),
        "Shell command substitution `$(...)` — the inner command runs and its "
        "output is interpolated; if any operand crosses an attacker-controlled "
        "boundary (env var, file content, network input) this becomes RCE. "
        "Fix: prefer reading the value via API/file-read instead of shelling "
        "out; if shelling out is unavoidable, validate inputs and quote "
        "everything. Common-OK: read-only commands like `$(git rev-parse ...)` "
        "in a controlled template",
    ),
    # `command` - Legacy backtick command substitution
    (
        re.compile(r"`[^`]+`"),
        "Legacy backtick command substitution `…` — same RCE risk as `$(...)` "
        "plus harder to nest safely. Fix: prefer `$(...)` for new code; for "
        "non-shell text, wrap the value in code-fence formatting so the "
        "scanner doesn't treat it as a shell construct",
    ),
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
    (
        re.compile(r"\|\s*sh\b"),
        "[RC-114] Pipe-to-shell `| sh` — executes whatever produced the upstream "
        "stdout, no signature/integrity check. Fix: download to a file, "
        "verify checksum/signature, then invoke explicitly. Pattern catches "
        "the classic `curl … | sh` install footgun",
    ),
    (
        re.compile(r"\|\s*bash\b"),
        "[RC-115] Pipe-to-shell `| bash` — same RCE risk as RC-114 with bash "
        "explicitly named. Fix: download, verify, then `bash <file>`",
    ),
    (
        re.compile(r"\|\s*zsh\b"),
        "[RC-116] Pipe-to-shell `| zsh` — same RCE risk as RC-114 with zsh "
        "explicitly named. Fix: download, verify, then `zsh <file>`",
    ),
    (
        re.compile(r"\|\s*ksh\b"),
        "[RC-117] Pipe-to-shell `| ksh` — same RCE risk as RC-114 with ksh "
        "explicitly named. Fix: download, verify, then `ksh <file>`",
    ),
    (
        re.compile(r"\|\s*source\b"),
        "[RC-118] Pipe-to-source `| source` — like pipe-to-shell but loads "
        "into the current shell context, also leaking env vars and aliases. "
        "Fix: never source remote-fetched content; download, audit, source explicitly",
    ),
    (
        re.compile(r"\|\s*\.\s"),
        "[RC-119] Pipe-to-dot `| . ` (POSIX shorthand for `source`) — same "
        "risk as RC-118. Fix: same as RC-118",
    ),
]

# Eval patterns - code execution risks
EVAL_PATTERNS = [
    (
        re.compile(r"\beval\s+"),
        "[RC-120] Shell `eval` — runs an arbitrary string as code; if any "
        "part is attacker-influenced this is direct RCE. Fix: replace with "
        "explicit dispatch (case statement, function lookup table). Common-OK: "
        "documentation that explains why `eval` is dangerous",
    ),
    (
        re.compile(r"\bexec\s+"),
        "[RC-121] Shell `exec <cmd>` — replaces the current shell with the "
        "named command; if `<cmd>` is attacker-controlled this is RCE plus "
        "loss of cleanup handlers. Fix: don't pass user input to exec; if "
        "execvp-style replacement is genuinely needed, validate the command "
        "name against an allowlist first",
    ),
    # Python-specific
    (
        re.compile(r"\beval\s*\("),
        "[RC-122] Python `eval(…)` — evaluates an arbitrary Python expression; "
        "trivial RCE if any operand crosses an attacker boundary. Fix: use "
        "`ast.literal_eval` for data-only parsing, or write an explicit parser "
        "for the format you actually need",
    ),
    (
        re.compile(r"\bexec\s*\("),
        "[RC-123] Python `exec(…)` — runs an arbitrary statement block; "
        "trivial RCE if any operand crosses an attacker boundary. Fix: refactor "
        "to call a real function. Common-OK: a documentation file explaining "
        "what `exec()` does (CPV's own taint-engine source documents this)",
    ),
    (
        re.compile(r"\bcompile\s*\([^)]*\bexec\b"),
        "[RC-124] Python `compile(…, mode='exec')` — compiles arbitrary code "
        "for later execution; same RCE class as RC-123 with deferred trigger. "
        "Fix: same as RC-123",
    ),
    # JavaScript-specific
    (
        re.compile(r"\bFunction\s*\("),
        "[RC-125] JavaScript `Function(…)` constructor — eval-equivalent in "
        "JS; the string body is parsed as code. Fix: never construct Function "
        "from user input. Common-OK: ESLint rule documentation matching this "
        "pattern in its examples",
    ),
    (
        re.compile(r"\bnew\s+Function\s*\("),
        "[RC-126] JavaScript `new Function(…)` — eval-equivalent. Same fix "
        "as RC-125",
    ),
]

# =============================================================================
# Path Traversal Patterns
# =============================================================================

PATH_TRAVERSAL_PATTERNS = [
    # Directory traversal
    (
        re.compile(r"\.\./"),
        "[RC-110] Directory traversal sequence `../` — appears in a path that "
        "may be passed to file operations; if any segment is attacker-influenced "
        "the result can read or write outside the intended directory tree. "
        "Fix: anchor every relative path against a known root (Path.resolve() + "
        "is_relative_to() check) before opening. Common-OK: glob/regex patterns, "
        "config keys like `extraPaths: [\"../scripts\"]`, doc snippets",
    ),
    (
        re.compile(r"\.\.\\"),
        "[RC-111] Windows directory traversal sequence `..\\` — same risk as "
        "RC-110 on Windows paths; backslash variant must be checked separately "
        "because Path comparison is case- and separator-insensitive on Windows. "
        "Fix: same as RC-110 — anchor and check is_relative_to()",
    ),
    # Absolute paths to system directories (except env-var placeholders).
    # The "tmp" and "var" prefixes are EXCLUDED — the standard POSIX temp
    # dir (mktemp default) sits under one, and the macOS user-temp tree
    # under the other; both are routinely used by legitimate plugin
    # scripts. Writes to system-log directories under "var" are caught by
    # the more targeted RC-87 / RC-90 hardening rules.
    (
        re.compile(
            r"(?<!\$\{CLAUDE_PLUGIN_ROOT\})(?<!\$\{CLAUDE_PLUGIN_DATA\})(?<!\$\{CLAUDE_PROJECT_DIR\})(?<![\w$\{])/(?:usr|etc|opt|bin|sbin|lib|root)/"
        ),
        "[RC-112] Absolute Unix system path (`/usr|/etc|/opt|/bin|/sbin|/lib|/root`) "
        "— hardcoding a host-specific system path makes the plugin non-portable "
        "and may indicate a write into a system location it shouldn't touch. "
        "Fix: use `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, or "
        "`${CLAUDE_PROJECT_DIR}` for plugin-relative paths; for genuine system "
        "config (proxy CA, OS-managed settings) keep the path but document why. "
        "Common-OK: documentation describing where managed-settings.json lives, "
        "regex source for security validators that intentionally match these paths",
    ),
    # Windows absolute paths
    (
        re.compile(r"[A-Za-z]:\\"),
        "[RC-113] Windows absolute path (`C:\\…`) — same portability/leak concern "
        "as RC-112 on Windows. Fix: use Path placeholders relative to "
        "`${CLAUDE_PLUGIN_ROOT}` etc. Common-OK: documentation snippets "
        "describing managed-settings.json location",
    ),
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
        "[RC-127] Prompt injection — `ignore previous instructions` directive "
        "in plugin-shipped content; if loaded into a Claude turn this would "
        "attempt to override the system prompt. Fix: rephrase the documentation "
        "(e.g. \"DO NOT use the phrase 'ignore previous instructions' in "
        "skill content\"); for educational content explaining the attack, "
        "wrap the example in backticks or a fenced code block",
    ),
    (
        re.compile(r"you\s+are\s+now\s+(?:a|an)\b", re.IGNORECASE),
        "[RC-128] Prompt injection — identity override (`you are now a/an …`) "
        "in plugin-shipped content; classic role-rewrite attack vector. Fix: "
        "rephrase or wrap example in code fences",
    ),
    (
        re.compile(
            r"(?:forget|disregard|override)\s+(?:all\s+)?(?:your|the)\s+(?:instructions?|rules?|guidelines?|constraints?)",
            re.IGNORECASE,
        ),
        "[RC-129] Prompt injection — instruction-override directive "
        "(`forget/disregard/override … instructions/rules/guidelines/constraints`); "
        "attempts to wipe the system prompt. Fix: rephrase or fence",
    ),
    (
        re.compile(
            r"do\s+not\s+follow\s+(?:any|the)\s+(?:previous|above|prior)\s+(?:instructions?|rules?)", re.IGNORECASE
        ),
        "[RC-130] Prompt injection — instruction-negation directive "
        "(`do not follow … previous instructions/rules`); negation-form of "
        "RC-127. Fix: rephrase or fence",
    ),
    (
        re.compile(r"(?:system|hidden)\s*(?:prompt|instruction|message)\s*:", re.IGNORECASE),
        "[RC-131] Prompt injection — fake system-prompt marker "
        "(`system: …`, `hidden prompt: …`); pretends to be the privileged "
        "system channel so the model treats following text as authoritative. "
        "Fix: rephrase to plain words like \"system instructions\", or fence",
    ),
    (
        re.compile(r"<\s*(?:system|instructions?|context)\s*>", re.IGNORECASE),
        "[RC-132] Prompt injection — fake XML system tag "
        "(`<system>`, `<instructions>`, `<context>`); mimics the structured "
        "system-prompt boundary. Fix: rephrase or fence; for genuine XML "
        "documentation use a fenced code block tagged ```xml",
    ),
    (
        re.compile(r"\[INST\]|\[/INST\]|\[SYSTEM\]", re.IGNORECASE),
        "[RC-133] Prompt injection — fake instruction delimiters "
        "(`[INST]`, `[/INST]`, `[SYSTEM]`); mimic Llama-style chat-template "
        "boundaries. Fix: rephrase or fence",
    ),
    (
        re.compile(r"IMPORTANT:\s*(?:ignore|override|forget|disregard)", re.IGNORECASE),
        "[RC-134] Prompt injection — `IMPORTANT:` urgency-prefix combined "
        "with an override verb; emphasis-spam variant of RC-127/129. Fix: "
        "rephrase the IMPORTANT block to remove the override verb, or fence",
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
    # Shell interpreters — always suspicious when fed via curl/wget.
    # Benign forms (`bash --version`, `bash --help`) are extremely rare in
    # plugin scripts and the cost of flagging them is far below the cost of
    # missing a real `curl ... | bash` install attack.
    (
        re.compile(r"curl\s+.*\|\s*(?:sh|bash|zsh|ksh)\b"),
        "[RC-136] Supply-chain attack — `curl … | sh/bash/zsh/ksh` install "
        "footgun: downloads remote content and pipes directly to a shell "
        "interpreter, no signature/integrity check, full RCE if the URL is "
        "MITMed or the source is compromised. Fix: download to a file, verify "
        "checksum/signature, then `bash <file>`",
    ),
    (
        re.compile(r"wget\s+.*\|\s*(?:sh|bash|zsh|ksh)\b"),
        "[RC-137] Supply-chain attack — `wget … | sh/bash/zsh/ksh` install "
        "footgun: same RCE class as RC-136 with wget. Fix: same as RC-136",
    ),
    # Language interpreters (python/node) — only fire when the invocation is
    # clearly in exec mode. Skips read-only formatters such as
    # `python3 -m json.tool`, `python -m pprint`, `node --version`.
    # Exec markers: end-of-line, `-c CODE`, `-e CODE`, `-` (explicit stdin),
    # `-m pip` (pip can install from URL), or shell separator after the cmd.
    (
        re.compile(
            r"curl\s+.*\|\s*(?:python|python3|node)(?:\s*$|\s+-c\b|\s+-e\b|\s+-(?:\s|$)|\s+-m\s+pip\b|\s*[;&|<>])"
        ),
        "[RC-138] Supply-chain attack — `curl … | python/node` in exec mode "
        "(stdin/`-c`/`-e`/`-m pip`); same RCE class as RC-136 with a language "
        "interpreter. Fix: download, audit, run explicitly. The exec-mode "
        "guard already filters benign read-only formatters",
    ),
    (
        re.compile(
            r"wget\s+.*\|\s*(?:python|python3|node)(?:\s*$|\s+-c\b|\s+-e\b|\s+-(?:\s|$)|\s+-m\s+pip\b|\s*[;&|<>])"
        ),
        "[RC-139] Supply-chain attack — `wget … | python/node` in exec mode; "
        "same as RC-138 with wget. Fix: same as RC-138",
    ),
    (
        re.compile(r"pip\s+install\s+.*(?:https?://|git\+|--index-url\s+(?!https://pypi))"),
        "[RC-140] Supply-chain attack — `pip install` from non-PyPI source "
        "(http(s) URL, `git+…`, or `--index-url` pointing somewhere other than "
        "the canonical PyPI). The package is installed without PyPI's "
        "checksum/signature trail. Fix: prefer the canonical PyPI; if you "
        "MUST install from a URL, pin a commit hash and use `--require-hashes`",
    ),
    (
        re.compile(r"npm\s+install\s+.*(?:https?://|git\+|--registry\s+(?!https://registry\.npmjs))"),
        "[RC-141] Supply-chain attack — `npm install` from non-npm-registry "
        "source. Same risk as RC-140 in the JS ecosystem. Fix: same — prefer "
        "the canonical npm registry; pin commit + use a lockfile",
    ),
    (
        re.compile(r"curl\s+.*-[oO]\s+.*&&\s*(?:chmod|sh|bash|python|node)\b"),
        "[RC-142] Supply-chain attack — `curl -o … && chmod/sh/bash/python/node` "
        "(download-then-execute one-liner); skips integrity verification. "
        "Fix: split into download + verify-signature + execute, with the "
        "verify step refusing to proceed on mismatch",
    ),
    (
        re.compile(r"wget\s+.*-[oO]\s+.*&&\s*(?:chmod|sh|bash|python|node)\b"),
        "[RC-143] Supply-chain attack — `wget -O … && chmod/sh/bash/python/node` "
        "(download-then-execute one-liner). Same as RC-142 with wget. "
        "Fix: same as RC-142",
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
    (
        re.compile(r"~/\.ssh/|/\.ssh/|SSH_KEY|id_rsa|id_ed25519"),
        "[RC-144] Credential harvest — reference to SSH key file (`~/.ssh/`, "
        "`id_rsa`, `id_ed25519`, `SSH_KEY` env var). Plugins should never "
        "read user SSH keys. Fix: use `gh` / `git` CLI for git operations "
        "(they handle auth) or ssh-agent-forwarding; if you genuinely need "
        "to display the path in docs, fence it. Common-OK: documentation "
        "telling users where THEIR keys live",
    ),
    (
        re.compile(r"~/\.aws/|/\.aws/|AWS_SECRET|aws_secret_access_key", re.IGNORECASE),
        "[RC-145] Credential harvest — reference to AWS credentials file or "
        "secret-key env var. Fix: use AWS SDK's default credential chain "
        "(don't read the file directly); for docs, fence the path",
    ),
    (
        re.compile(r"~/\.gitconfig|/\.gitconfig|GIT_TOKEN|GITHUB_TOKEN", re.IGNORECASE),
        "[RC-146] Credential harvest — reference to git config or GitHub "
        "token env var. Fix: use `gh auth token` for GitHub auth, or read "
        "the env var via the standard CC env-var passthrough; for docs, fence",
    ),
    (
        re.compile(r"~/\.npmrc|/\.npmrc|NPM_TOKEN|npm_token", re.IGNORECASE),
        "[RC-147] Credential harvest — reference to npm credentials file or "
        "token env var. Fix: let npm CLI handle auth; for docs, fence",
    ),
    (
        re.compile(r"~/\.docker/|/\.docker/config\.json|DOCKER_PASSWORD", re.IGNORECASE),
        "[RC-148] Credential harvest — reference to Docker credentials store. "
        "Fix: use `docker login` and let the CLI manage credentials; for "
        "docs, fence",
    ),
    (
        re.compile(r"~/\.kube/|/\.kube/config|KUBECONFIG", re.IGNORECASE),
        "[RC-149] Credential harvest — reference to Kubernetes kubeconfig. "
        "Fix: use `kubectl` and let it pick up the user's KUBECONFIG; for "
        "docs, fence",
    ),
    (
        re.compile(r"~/\.gnupg/|/\.gnupg/|GPG_PASSPHRASE", re.IGNORECASE),
        "[RC-150] Credential harvest — reference to GPG keyring or "
        "GPG_PASSPHRASE env var. Fix: invoke `gpg` and let it prompt the "
        "user's agent; for docs, fence",
    ),
    (
        re.compile(r"(?:keychain|keyring|credential.?store|password.?store)", re.IGNORECASE),
        "[RC-151] Credential harvest — reference to system keystore "
        "(macOS Keychain, GNOME Keyring, KWallet, Windows credential store, "
        "or `pass`-style password-store). Plugins should not read user "
        "credentials directly. Fix: use the official CLI for the service "
        "(it handles keystore lookup); for docs, fence the term",
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
    (
        re.compile(r"--no-verify\b"),
        "[RC-152] Sandbox escape — `--no-verify` flag (`git push --no-verify`, "
        "`git commit --no-verify`) bypasses pre-push / pre-commit hooks "
        "including CPV's own publish gate. Fix: investigate why the hook "
        "is failing and address it; never ship a plugin that recommends "
        "`--no-verify` to its users",
    ),
    (
        re.compile(r"git\s+config\s+.*(?:core\.hooksPath|core\.autocrlf|safe\.directory)"),
        "[RC-153] Sandbox escape — `git config` mutation of `core.hooksPath` "
        "(redirects all hooks), `core.autocrlf` (silently rewrites line "
        "endings), or `safe.directory` (suppresses unsafe-repo warnings). "
        "Plugins must never mutate global git config silently. Fix: leave "
        "git config alone; if the user genuinely needs different hooks "
        "behavior, document the manual command",
    ),
    (
        re.compile(r"--dangerously-skip-permissions\b"),
        "[RC-154] Permission escalation — `--dangerously-skip-permissions` "
        "(or its `dangerouslySkipPermissions` settings field) disables CC's "
        "permission prompts wholesale. Plugins must NOT recommend this. "
        "Fix: declare the specific permissions needed in `permissions.allow`; "
        "for genuine worktree-isolation use cases, scope to the worktree "
        "agent only and document the rationale",
    ),
    (
        re.compile(r"chmod\s+(?:777|a\+rwx)\b"),
        "[RC-155] Sandbox escape — `chmod 777` / `chmod a+rwx` makes the "
        "target world-readable AND world-writable, defeating any per-user "
        "permission boundary. Fix: use the most restrictive mode that "
        "actually works (typically 644 for files, 755 for directories or "
        "executables, 600 for secrets)",
    ),
    (
        re.compile(r"(?:disable|bypass|skip)\s*(?:all\s+)?(?:hooks?|guard|safety|protection|sandbox)", re.IGNORECASE),
        "[RC-156] Sandbox escape — language pattern that talks about disabling, "
        "bypassing, or skipping safety controls (hooks/guards/safety/protection/"
        "sandbox). Often a sign of a script doing the disabling, sometimes a "
        "doc warning users not to do it. Fix: if the script does it, remove; "
        "if documentation, fence the example or rephrase to make the "
        "warning explicit",
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


def is_cpv_self_scan(plugin_path: Path) -> bool:
    """Detect whether the target plugin path IS the CPV plugin itself.

    The security validator's own source contains every detection pattern
    it knows how to match (regex sources, taint engine docs, fix-validation
    references, security TRDDs). When CPV scans itself, those literal
    pattern definitions self-match and produce thousands of false-positive
    CRITICALs.

    This check identifies CPV regardless of where it's deployed (dev
    checkout, `~/.claude/plugins/cache/`, vendored copy, fork) using two
    independent signals — either is enough:

    1. **plugin.json identity** — `.claude-plugin/plugin.json::name` equals
       `claude-plugins-validation`. Survives forks that keep the name.
    2. **Signature files** — the path contains BOTH
       `scripts/cpv_validation_common.py` AND `scripts/validate_plugin.py`.
       Survives forks that rename the plugin.

    Either signal returning True flips the entire scan into "self-scan
    mode" — the per-file scanners then treat fix-validation references,
    cpv_*.py modules, and security tests as documentation rather than
    source. Other plugins are unaffected because they cannot satisfy
    either signal accidentally.
    """
    # Signal 1 — plugin.json name match
    plugin_json = plugin_path / ".claude-plugin" / "plugin.json"
    if plugin_json.is_file():
        try:
            data = json.loads(plugin_json.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name") == "claude-plugins-validation":
                return True
        except (json.JSONDecodeError, OSError):
            pass

    # Signal 2 — both signature scripts present
    sig1 = plugin_path / "scripts" / "cpv_validation_common.py"
    sig2 = plugin_path / "scripts" / "validate_plugin.py"
    if sig1.is_file() and sig2.is_file():
        return True

    return False


# Module-level cache: set when validate_security() detects self-scan mode.
# Per-file scanners read this to apply CPV-only exclusions without
# threading the flag through every signature.
_CPV_SELF_SCAN_ACTIVE: bool = False
_CPV_SELF_HASH_MANIFEST: dict[str, str] = {}
_CPV_SELF_PLUGIN_ROOT: Path | None = None
_CPV_SELF_HASH_REPORTED_MISSING: set[str] = set()
_CPV_SELF_HASH_REPORTED_MODIFIED: set[str] = set()
_CPV_SELF_HASH_NOTICE_REPORT: ValidationReport | None = None

CPV_SELF_HASH_MANIFEST_NAME = ".cpv-self-hashes.json"


def _set_cpv_self_scan(
    active: bool,
    plugin_root: Path | None = None,
    notice_report: ValidationReport | None = None,
) -> None:
    """Set the module-level CPV-self-scan flag and load the hash manifest.

    Loads the canonical hash manifest used to gate self-scan skips. Two
    sources, picked by trust level:

    1. **Target IS the running CPV** — same plugin_root as where this
       module was loaded from. The local `.cpv-self-hashes.json` is
       trustworthy because the running CPV was already integrity-verified
       against GitHub at startup (see cpv_integrity.verify_self_integrity).

    2. **Target claims to be CPV but is a DIFFERENT directory** — could
       be a malicious plugin spoofing the name + signature files + local
       manifest to evade scanning. Don't trust the local manifest;
       fetch the GitHub canonical manifest for the target's claimed
       version. If GitHub fetch fails → refuse self-scan (scan everything).
    """
    global _CPV_SELF_SCAN_ACTIVE, _CPV_SELF_HASH_MANIFEST, _CPV_SELF_PLUGIN_ROOT
    global _CPV_SELF_HASH_NOTICE_REPORT
    _CPV_SELF_SCAN_ACTIVE = active
    _CPV_SELF_PLUGIN_ROOT = plugin_root.resolve() if active and plugin_root else None
    _CPV_SELF_HASH_MANIFEST = {}
    _CPV_SELF_HASH_REPORTED_MISSING.clear()
    _CPV_SELF_HASH_REPORTED_MODIFIED.clear()
    _CPV_SELF_HASH_NOTICE_REPORT = notice_report if active else None

    if not active or plugin_root is None:
        return

    target_root = plugin_root.resolve()
    running_cpv_root = Path(__file__).resolve().parent.parent
    is_running_cpv = (target_root == running_cpv_root)

    if is_running_cpv:
        # Trust the local manifest — running CPV's integrity was already
        # verified against GitHub at startup by cpv_integrity.
        manifest = _load_local_manifest(target_root, notice_report)
    else:
        # Target claims to be CPV but isn't the validator instance running.
        # Fetch the canonical manifest from GitHub for the target's
        # claimed version. If we can't reach GitHub, refuse to skip —
        # better to surface false-positives than to silently miss a
        # malicious plugin that spoofed its identity.
        target_version = _read_target_version(target_root)
        try:
            from cpv_integrity import fetch_canonical_manifest  # noqa: PLC0415
            manifest = fetch_canonical_manifest(target_version)
        except ImportError:
            manifest = None

        if manifest is None:
            if notice_report is not None:
                notice_report.major(
                    f"[RC-163] CPV self-scan: target plugin claims to be "
                    f"`claude-plugins-validation` (or has the signature files) "
                    f"but is NOT the running validator instance, AND the GitHub "
                    f"canonical manifest for v{target_version or '<unknown>'} "
                    f"could not be fetched. Cannot verify whether the target "
                    f"is genuine CPV or a spoofed lookalike — scanning every "
                    f"file as a safe default. Fix: ensure network access to "
                    f"raw.githubusercontent.com so the canonical manifest can "
                    f"be retrieved."
                )
            return

    if isinstance(manifest, dict):
        files = manifest.get("files", {})
        if isinstance(files, dict):
            for k, v in files.items():
                if isinstance(k, str) and isinstance(v, str):
                    _CPV_SELF_HASH_MANIFEST[k.replace("\\", "/")] = v


def _load_local_manifest(
    plugin_root: Path,
    notice_report: ValidationReport | None,
) -> dict[str, object] | None:
    """Read the local `.cpv-self-hashes.json` from plugin_root."""
    manifest_path = plugin_root / CPV_SELF_HASH_MANIFEST_NAME
    if not manifest_path.is_file():
        if notice_report is not None:
            notice_report.major(
                f"[RC-160] CPV self-scan: hash manifest "
                f"`{CPV_SELF_HASH_MANIFEST_NAME}` not found at plugin root. "
                f"Without the manifest CPV cannot verify which files are "
                f"genuine validator source vs. spoofed lookalikes; falling back "
                f"to scanning every file. Fix: regenerate the manifest with "
                f"`uv run python scripts/compute_cpv_self_hashes.py`."
            )
        return None
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, OSError) as e:
        if notice_report is not None:
            notice_report.major(
                f"[RC-160] CPV self-scan: hash manifest "
                f"`{CPV_SELF_HASH_MANIFEST_NAME}` could not be parsed ({e}); "
                f"falling back to scanning every file. Fix: regenerate with "
                f"`uv run python scripts/compute_cpv_self_hashes.py`."
            )
        return None


def _read_target_version(plugin_root: Path) -> str | None:
    """Read the target plugin's version from `.claude-plugin/plugin.json`."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        v = data.get("version")
        return str(v) if isinstance(v, str) else None
    except (json.JSONDecodeError, OSError):
        return None


def _sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


_DEV_SCRATCH_DIR_PARTS = (
    "/docs_dev/",
    "/scripts_dev/",
    "/tests_dev/",
    "/samples_dev/",
    "/examples_dev/",
    "/downloads_dev/",
    "/libs_dev/",
    "/builds_dev/",
    "/reports_dev/",
    "/reports/",
    "/design/tasks/",
)


def _is_dev_scratch_path(rel_or_abs: str) -> bool:
    """True for files inside a gitignored dev-scratch / design-spec dir.

    These dirs (docs_dev/, scripts_dev/, design/tasks/, …) are NEVER
    shipped — they're listed in compute_cpv_self_hashes.py's `skip_dirs`
    so they have no manifest entry. They're also documentation by
    example: audit reports in docs_dev/ legitimately quote secret-pattern
    fixtures, TRDDs in design/tasks/ describe wire formats that include
    pattern strings. Letting the scanner flag them produces noise with
    zero security signal — they can't reach a runtime code path because
    they're not imported and not loaded by Claude Code.

    Marker prefix `/` on each entry forces a directory-boundary match so
    a file literally named `docs_dev_helper.py` doesn't accidentally
    qualify.
    """
    p = "/" + rel_or_abs.lower().replace("\\", "/").lstrip("/")
    return any(part in p for part in _DEV_SCRATCH_DIR_PARTS)


def cpv_self_scan_skip(file_path: str) -> bool:
    """Return True if `file_path` should be skipped during a CPV-self-scan.

    Three-stage check:

    1. **Dev-scratch shortcut** — if the file lives in a gitignored
       dev-scratch directory (docs_dev/, design/tasks/, scripts_dev/,
       …), skip unconditionally. These dirs aren't in the hash manifest
       (compute_cpv_self_hashes.py skips them), they're not shipped,
       and they exist purely to document patterns by example.
    2. **Name-based eligibility** — does the path match a CPV-internal
       file pattern (validator script, fix-validation reference, security
       test, semantic-validation reference)? If not, no skip.
    3. **Hash verification** — compute the file's actual SHA256 and look
       it up in `.cpv-self-hashes.json`. Only skip if the hash matches
       the canonical value. Hash mismatch (file modified) or missing
       entry → don't skip; the file is scanned normally.

    Stages 2+3 defend against name-spoofing: a malicious plugin that
    names a file `cpv_taint_engine.py` cannot evade the security scan by
    relying on the name match — the hash check fails and the file is
    scanned. Stage 1 cannot be spoofed in a CPV self-scan: the only way
    for an attacker to land a file in `docs_dev/` is to already have
    write access to the validator's own source tree, in which case they
    don't need to spoof anything.
    """
    if not _CPV_SELF_SCAN_ACTIVE:
        return False

    # Tier 0 — dev-scratch directories: skip unconditionally.
    if _is_dev_scratch_path(file_path):
        return True

    if not _is_self_scan_eligible(file_path):
        return False

    # Hash verification — must match the canonical entry to skip.
    if _CPV_SELF_PLUGIN_ROOT is None:
        return False

    # Normalize to plugin-root-relative path. Some scanners pass
    # absolute paths (e.g., cc-audit external invocation); convert
    # back to rel-path so the manifest lookup matches.
    file_normalized = _normalize_to_relpath(file_path, _CPV_SELF_PLUGIN_ROOT)
    if file_normalized is None:
        return False  # File outside plugin_root — never a self-match.

    expected = _CPV_SELF_HASH_MANIFEST.get(file_normalized)
    if expected is None:
        # File matches the pattern but has no manifest entry — possibly
        # a new/renamed file the manifest wasn't regenerated for. Don't
        # skip; report once per file so reviewers refresh the manifest.
        if (
            _CPV_SELF_HASH_NOTICE_REPORT is not None
            and file_normalized not in _CPV_SELF_HASH_REPORTED_MISSING
        ):
            _CPV_SELF_HASH_REPORTED_MISSING.add(file_normalized)
            _CPV_SELF_HASH_NOTICE_REPORT.minor(
                f"[RC-161] CPV self-scan: file `{file_normalized}` matches a "
                f"self-scan pattern but is not in the hash manifest; scanning "
                f"normally. Fix: regenerate the manifest with "
                f"`uv run python scripts/compute_cpv_self_hashes.py` (the "
                f"manifest must be refreshed after any change to the "
                f"validator source set)."
            )
        return False

    actual = _sha256_file(_CPV_SELF_PLUGIN_ROOT / file_normalized)
    if actual is None:
        return False
    expected_hex = expected.split(":", 1)[-1] if expected.startswith("sha256:") else expected
    if actual != expected_hex:
        # Hash mismatch — file was modified. Could be a legitimate edit
        # in progress or a spoofed lookalike. Either way we DON'T skip;
        # scan it as if it were a normal plugin file. Report once.
        if (
            _CPV_SELF_HASH_NOTICE_REPORT is not None
            and file_normalized not in _CPV_SELF_HASH_REPORTED_MODIFIED
        ):
            _CPV_SELF_HASH_REPORTED_MODIFIED.add(file_normalized)
            _CPV_SELF_HASH_NOTICE_REPORT.warning(
                f"[RC-162] CPV self-scan: file `{file_normalized}` matches "
                f"a self-scan pattern but its SHA256 differs from the manifest "
                f"entry — scanning normally. If you edited this file, "
                f"regenerate the manifest with "
                f"`uv run python scripts/compute_cpv_self_hashes.py` and "
                f"re-run; otherwise treat the contents as untrusted."
            )
        return False

    return True


def _normalize_to_relpath(file_path: str, plugin_root: Path) -> str | None:
    """Convert any incoming file_path (rel or abs) to a normalized path
    relative to plugin_root, using forward slashes.

    Returns None if file_path resolves outside plugin_root — such files
    can never be self-scan candidates.
    """
    try:
        p = Path(file_path)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(plugin_root.resolve())
            except ValueError:
                return None  # Outside plugin_root.
            return str(rel).replace("\\", "/")
    except (OSError, ValueError):
        return None
    # Relative path — strip leading slash if any.
    return file_path.replace("\\", "/").lstrip("/")


def _is_self_scan_eligible(file_path: str) -> bool:
    """Path-only eligibility check — does this file LOOK like a CPV-internal
    pattern source? Same logic the manifest computation uses, so the two
    sets stay in lockstep.

    Handles both relative paths (from the in-process scan walker) and
    absolute paths (from external scanners like cc-audit).

    NOT a security check on its own — must be combined with hash verification.
    """
    if is_validator_script(file_path):
        return True
    if is_security_fix_reference(file_path):
        return True

    file_normalized = file_path.lower().replace("\\", "/")
    # For absolute paths, accept the eligibility check if the suffix
    # (anywhere in the path) matches a self-scan pattern. The hash check
    # later still requires plugin-root containment + manifest match —
    # this just lets cc-audit-style absolute paths through to that gate.
    basename = file_normalized.rsplit("/", 1)[-1] if "/" in file_normalized else file_normalized
    # ALL CPV test files — pytest discovery uses test_*.py, so the
    # validator's own test suite is anything matching that. Hash gate
    # still applies, so a malicious plugin renaming a payload to
    # `test_evil.py` cannot evade scanning.
    if basename.startswith("test_") and basename.endswith(".py"):
        return True
    # Test fixtures contain pattern strings by design.
    if "/tests/fixtures/" in file_normalized:
        return True
    if "/semantic-validation-skill/references/" in file_normalized:
        return True
    if "/skills/" in file_normalized and "/references/" in file_normalized and basename.endswith(".md"):
        return True
    # CPV's own AGENT / COMMAND / SKILL markdown — these document the
    # security patterns by example and the workflows that act on them.
    # Hash-verified so an unrelated plugin can't park a same-named file
    # in its own agents/ folder to evade scanning.
    if (
        ("/agents/" in file_normalized or file_normalized.startswith("agents/"))
        and basename.endswith(".md")
    ):
        return True
    if (
        ("/commands/" in file_normalized or file_normalized.startswith("commands/"))
        and basename.endswith(".md")
    ):
        return True
    if (
        ("/skills/" in file_normalized or file_normalized.startswith("skills/"))
        and basename.endswith(".md")
    ):
        return True
    # Templates CPV ships for downstream plugins (workflow snippets,
    # config seeds). They contain placeholder strings like "<TOKEN>" and
    # describe security knobs ("admin permission", "bypass branch
    # protection") that match prompt-injection heuristics by accident.
    if "/templates/" in file_normalized or file_normalized.startswith("templates/"):
        return True
    if "/design/tasks/" in file_normalized and basename.startswith("trdd-"):
        return True
    if "/docs_dev/" in file_normalized:
        # docs_dev/ is a private dev-only directory (gitignored). Audit
        # reports / changelogs inside it document patterns by example.
        return True
    return False


def is_validator_script(file_path: str) -> bool:
    """Check if file is a validator/scaffolder script that contains intentional pattern definitions.

    These files necessarily contain literal security patterns (regex sources,
    template strings emitted into other plugins, help-text examples) that
    would self-match. Skip is gated by hash verification — name match alone
    never grants the skip; only files whose SHA256 matches the GitHub
    canonical manifest are skipped.

    Recognises:
    - `validate_*.py` (per-validator scripts) and `cpv_*.py` (CPV-internal
      helpers — taint engine, SARIF writer, scope rules, validation common).
    - Scaffolder scripts: `generate_*.py`, `manage_*.py`, `setup_*.py`,
      `standardize_*.py`. These emit publish.py templates and shell
      examples as Python triple-quoted strings.
    - Pipeline scripts: `publish.py`, `smart_exec.py`, `lint_files.py`,
      `compute_cpv_self_hashes.py`, `cc_scope_rules.py`, `_minimal_yaml.py`.
    """
    file_lower = file_path.lower().replace("\\", "/")
    basename = file_lower.rsplit("/", 1)[-1] if "/" in file_lower else file_lower
    if not basename.endswith(".py"):
        return False

    # Per-validator (validate_plugin.py, validate_security.py, etc.) and
    # CPV-internal helpers (cpv_taint_engine.py, cpv_sarif_writer.py, etc.)
    if basename.startswith(("validate_", "cpv_")):
        return True

    # Scaffolder + pipeline scripts that emit shell/template content.
    if basename.startswith(("generate_", "manage_", "setup_", "standardize_")):
        return True

    # Specific pipeline scripts by exact name.
    if basename in {
        "publish.py",
        "smart_exec.py",
        "lint_files.py",
        "compute_cpv_self_hashes.py",
        "cc_scope_rules.py",
        "_minimal_yaml.py",
        "detect_lockfiles.py",
        "set_marketplace_pat.py",
    }:
        return True

    return False


def is_security_fix_reference(file_path: str) -> bool:
    """Check if file is a CPV reference doc that necessarily documents patterns.

    CPV ships skill reference markdown files that EXPLAIN security rules
    (CA-01 cache-audit, RC-110 path traversal, marketplace patterns,
    plugin structure) by quoting examples that contain the literal
    detection patterns. Scanning these always self-matches.

    Skip is gated by hash verification — name match alone never grants
    the skip; only files whose SHA256 matches the canonical manifest
    are skipped.

    Returns True for:
    - Any `.md` under `skills/<any>/references/` (CPV-shipped reference docs)
    - `*/design/tasks/TRDD-*.md` (CPV TRDDs documenting security work)
    - Specific `*-fixes.md` filenames anywhere (legacy direct match)
    """
    file_normalized = file_path.lower().replace("\\", "/")
    if not file_normalized.endswith((".md", ".mdx")):
        # Quick exit — references are markdown.
        if not file_normalized.endswith(".md"):
            return False

    # Any markdown under a skill's references/ folder is documentation that
    # may quote patterns. (Was narrow to fix-validation only; broadened
    # because every skill's references can document examples.)
    if "/skills/" in ("/" + file_normalized) and "/references/" in file_normalized:
        return True

    # Design TRDDs that explain security work / patterns.
    if "/design/tasks/" in ("/" + file_normalized) and "trdd-" in file_normalized:
        return True

    basename = file_normalized.rsplit("/", 1)[-1] if "/" in file_normalized else file_normalized
    if basename in {
        "cache-fixes.md",
        "security-fixes.md",
        "telemetry-hazard-fixes.md",
        "mcp-fixes.md",
        "hook-fixes.md",
        "skill-fixes.md",
        "plugin-structure-fixes.md",
        "encoding-fixes.md",
        "enterprise-fixes.md",
        "marketplace-fixes.md",
        "lsp-fixes.md",
        "settings-marketplace-fixes.md",
        "rules-fixes.md",
        "xref-fixes.md",
        "scoring-fixes.md",
        "documentation-fixes.md",
        "code-quality-fixes.md",
        "empirical-loading-bugs.md",
        "schema-parity-contract.md",
        "iterative-fix-loop.md",
    }:
        return True
    return False


def is_js_ts_file(file_path: str) -> bool:
    """JavaScript/TypeScript files use backticks for template literals.

    Backticks in JS/TS source/config (e.g. eslint.config.mjs, *.ts, *.tsx)
    are ES2015 template literals — the syntax for multi-line/interpolated
    strings. They are NEVER POSIX command substitution. Skip the
    backtick-pattern check on these files to avoid flagging code-quoted
    references inside `// comments` and template strings.
    """
    return file_path.lower().endswith(
        (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts")
    )


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
                # JS/TS files use backticks for template literals (ES2015), never
                # for POSIX command substitution. Skip backtick patterns there.
                # `$(...)` is also valid JS (DOM helpers, jQuery) but is rare in
                # plugin scripts; keep that pattern enabled for now.
                if is_js_ts_file(file_path) and "`...`" in msg:
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

    # Skip path checks for well-known IDE / typechecker / linter config
    # files. Keys like `extraPaths`, `paths`, `include`, `exclude`,
    # `rootDirs`, `outDir`, `baseUrl` legitimately use `../` to reference
    # sibling source dirs. These configs are consumed by tooling at
    # author time only — never by the plugin's runtime, never by Claude
    # Code's loader — so a `../` here cannot reach a file-open with
    # attacker-influenced segments. The rule's own help text labels this
    # exact pattern as "Common-OK", so suppress it here.
    basename = file_normalized.rsplit("/", 1)[-1] if "/" in file_normalized else file_normalized
    _TOOLING_CONFIG_BASENAMES = {
        "pyrightconfig.json",
        "pyproject.toml",
        "mypy.ini",
        ".mypy.ini",
        "ruff.toml",
        ".ruff.toml",
        "setup.cfg",
        "tsconfig.json",
        "jsconfig.json",
        "jest.config.json",
        ".eslintrc.json",
        ".eslintrc",
        "babel.config.json",
        ".babelrc",
        ".babelrc.json",
        ".prettierrc",
        ".prettierrc.json",
    }
    if (
        basename in _TOOLING_CONFIG_BASENAMES
        or basename.startswith("tsconfig.")
        or basename.startswith("jest.config.")
        or basename.startswith(".eslintrc.")
        or "/.vscode/" in file_normalized
        or "/.idea/" in file_normalized
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
                    f"[RC-135] Hardcoded user-home path (`{match.group()}`) "
                    f"— absolute path containing a username (`/Users/<name>/…`, "
                    f"`/home/<name>/…`, `C:\\Users\\<name>\\…`, `~/…`); the plugin "
                    f"will break for every other user and may leak the developer's "
                    f"identity in logs/diffs. Fix: replace with "
                    f"`${{CLAUDE_PLUGIN_ROOT}}` (plugin's own folder), "
                    f"`${{CLAUDE_PLUGIN_DATA}}` (writable per-plugin data), "
                    f"`${{CLAUDE_PROJECT_DIR}}` (project root), or `~` "
                    f"(POSIX home expansion). Common-OK: example output in docs, "
                    f"test fixtures with deliberately-fake usernames",
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

            # CPV self-scan: skip files that necessarily document the
            # security patterns CPV detects (validator scripts, fix-validation
            # references, security tests). Active only when the target IS the
            # CPV plugin itself (recognized by plugin.json name OR signature
            # files — see is_cpv_self_scan).
            if cpv_self_scan_skip(rel_path):
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

            # CPV self-scan: skip cc-audit findings on files the running CPV
            # has marked as canonical (validator source / fix-validation refs
            # / security tests). cc-audit hands back absolute paths;
            # cpv_self_scan_skip handles the abs→rel normalization.
            if file_ref and cpv_self_scan_skip(str(file_ref)):
                continue

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
    """Yield (file_path, rel_path, content) for every non-binary scannable file.

    Honors the same self-scan skip set as scan_all_files — checking only
    `is_validator_script` would let dev-scratch dirs (docs_dev/,
    design/tasks/, …) and hash-verified fix-validation references slip
    through and produce the same FPs the main scan loop suppresses.
    """
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
            if cpv_self_scan_skip(rel_path):
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


def check_phase10_taint(plugin_path: Path, report: ValidationReport) -> int:
    """Phase 10 — RC-73/74/75 AST-based Python taint analysis.

    Per-file analysis (intentionally not cross-file). Catches:
      RC-73: direct source-to-sink (e.g. `exec(os.environ.get('X'))`)
      RC-74: transitive source-to-sink via N-hop assignments
      RC-75: silently passes when sanitizers (shlex.quote, re.escape, ...)
             interrupt the chain
    """
    from cpv_taint_engine import analyze_plugin  # local — keeps cold path cheap
    issues = 0
    findings_by_file = analyze_plugin(plugin_path)
    for file_path, findings in findings_by_file.items():
        try:
            rel_path = str(file_path.relative_to(plugin_path))
        except ValueError:
            rel_path = str(file_path)
        for f in findings:
            severity = "major" if f.rule_id == "RC-73" else "minor"
            level = effective_severity(severity, rel_path)
            getattr(report, level)(
                f"{f.rule_id}: tainted '{f.var_name}' from {f.source} reaches "
                f"{f.sink} (hop_count={f.hop_count})",
                rel_path, f.line,
            )
            issues += 1
    return issues


def check_phase9_stemmed_injection(plugin_path: Path, report: ValidationReport) -> int:
    """Phase 9 — RC-76 stemmed semantic injection classifier.

    Catches paraphrased prompt-injection attempts that exact regex patterns
    miss because of word-form variation. Fires only when ≥3 trigger stems
    co-occur within an 80-char window — single keywords are too noisy.
    """
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        # Tighten further on test fixtures and validator sources to match
        # the same FP-reduction discipline as other phases.
        signals = find_stemmed_injection_signal(content)
        if not signals:
            continue
        for char_offset, stems in signals:
            line_no = content.count("\n", 0, char_offset) + 1
            level = effective_severity("major", rel_path)
            getattr(report, level)(
                f"RC-76: stemmed prompt-injection signal — {len(stems)} trigger stems "
                f"({', '.join(stems[:5])}) within 80-char window",
                rel_path, line_no,
            )
            issues += 1
    return issues


def check_phase4_all(plugin_path: Path, report: ValidationReport) -> int:
    """Phase 4 — minor / informational rules + verdict-tier classifier.

    Single-pass iteration of PHASE4_PATTERNS plus the disposition() helper
    (which doesn't produce findings — its output is in the report metadata).
    """
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        fence_state = build_fence_state(content)
        for line_no, line in enumerate(content.split("\n"), start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            for rule_id, severity, pattern, msg in PHASE4_PATTERNS:
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

    # RC-103 disposition is computed from the FINAL counts and added as INFO.
    # We can't compute it now (more checks may follow); the orchestrator
    # adds a disposition INFO line at the end of validate_security().
    return issues


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


# =============================================================================
# Phase 5 — Specialist-tool delegation (RC-102)
# =============================================================================
#
# Same external-binary pattern as check_cc_audit() and check_tirith_scanner().
# Each adds hundreds of patterns "for free" without copying any source code.
# All optional — emit a single WARNING when binary missing and skip.


def check_trufflehog(plugin_path: Path, report: ValidationReport) -> int:
    """Run trufflehog for credential detection if installed (RC-102 part 1).

    trufflehog ships ~700 verified-secret detectors. CPV's SECRET_PATTERNS
    has ~30. Delegating gives massive coverage without maintenance burden.
    """
    if not shutil.which("trufflehog"):
        report.warning(
            "trufflehog: binary not found — ~700 verified credential detectors skipped. "
            "Install via 'brew install trufflehog' or 'go install github.com/trufflesecurity/trufflehog/v3@latest'."
        )
        return 0

    issues = 0
    try:
        result = subprocess.run(
            ["trufflehog", "filesystem", str(plugin_path), "--json", "--no-update", "--fail"],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        report.warning("trufflehog: timed out after 180s — scan aborted")
        return 0
    except FileNotFoundError:
        report.warning("trufflehog: binary disappeared between probe and exec")
        return 0

    # trufflehog emits one JSON object per line for each detection
    for raw_line in (result.stdout or "").splitlines():
        raw_line = raw_line.strip()
        if not raw_line.startswith("{"):
            continue
        try:
            finding = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(finding, dict):
            continue
        detector = finding.get("DetectorName") or finding.get("detector") or "?"
        verified = finding.get("Verified") or finding.get("verified", False)
        source_metadata = finding.get("SourceMetadata", {}) or {}
        data = source_metadata.get("Data", {}) if isinstance(source_metadata, dict) else {}
        filesystem = data.get("Filesystem", {}) if isinstance(data, dict) else {}
        rel = filesystem.get("file", "?") if isinstance(filesystem, dict) else "?"
        line_no = filesystem.get("line", 0) if isinstance(filesystem, dict) else 0

        # Apply CPV's FP-reduction — demote in test/doc/sample contexts.
        # Also skip CPV's own validator-source files + tests + fixtures
        # under the same hash-gated self-scan rule (they contain regex
        # patterns and example tokens that look like secrets but are
        # intentional pattern-source material).
        if cpv_self_scan_skip(rel):
            continue
        base_level = "critical" if verified else "major"
        level = effective_severity(base_level, rel)
        getattr(report, level)(
            f"trufflehog {'VERIFIED' if verified else 'UNVERIFIED'} secret: detector={detector}",
            rel, line_no,
        )
        issues += 1

    if issues == 0 and result.returncode == 0:
        report.passed("trufflehog: no findings (700+ verified-secret detectors clean)")
    return issues


def check_gitleaks(plugin_path: Path, report: ValidationReport) -> int:
    """Run gitleaks for secret detection if installed (RC-102 part 2).

    gitleaks ships ~150 secret detectors with regex+entropy heuristics.
    Complements trufflehog (verified vs. heuristic) and CPV's own catalog.
    """
    if not shutil.which("gitleaks"):
        report.warning(
            "gitleaks: binary not found — ~150 secret detectors skipped. "
            "Install via 'brew install gitleaks' or 'docker run --rm -v $(pwd):/src zricethezav/gitleaks'."
        )
        return 0

    # gitleaks prefers --report-path for JSON output to a file
    with tempfile.NamedTemporaryFile(suffix=".json", prefix="gitleaks-", delete=False, mode="w") as tmp:
        tmp_path = tmp.name

    issues = 0
    try:
        # `detect` subcommand scans the directory (no .git history needed)
        subprocess.run(
            ["gitleaks", "detect", "--source", str(plugin_path),
             "--report-format", "json", "--report-path", tmp_path,
             "--no-banner", "--exit-code", "0"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        try:
            data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = []
        if not isinstance(data, list):
            data = []
        for finding in data:
            if not isinstance(finding, dict):
                continue
            rule_id = finding.get("RuleID") or finding.get("rule_id") or "?"
            description = finding.get("Description") or finding.get("description") or rule_id
            rel = finding.get("File") or finding.get("file") or "?"
            line_no = finding.get("StartLine") or finding.get("startLine") or 0
            # Skip CPV's own validator regex sources + tests/fixtures
            # under the same hash-gated self-scan rule applied elsewhere.
            # gitleaks operates on the raw file tree so its findings on
            # CPV-internal pattern fixtures (test_phase*.py with sample
            # tokens, fix-validation references with example secrets)
            # are FPs by construction; the hash gate stops a malicious
            # plugin from spoofing the path to evade detection.
            if cpv_self_scan_skip(rel):
                continue
            level = effective_severity("major", rel)
            getattr(report, level)(f"gitleaks {rule_id}: {description[:80]}", rel, line_no)
            issues += 1
    except subprocess.TimeoutExpired:
        report.warning("gitleaks: timed out after 180s")
    except FileNotFoundError:
        report.warning("gitleaks: binary disappeared between probe and exec")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if issues == 0:
        report.passed("gitleaks: no findings (150+ secret detectors clean)")
    return issues


def check_semgrep(plugin_path: Path, report: ValidationReport) -> int:
    """Run semgrep for static-analysis security checks if installed (RC-102 part 3).

    semgrep ships thousands of rules across many ecosystems via the
    p/security-audit and p/secrets rule packs. Use lightweight registry
    rules so the call is bounded.
    """
    if not shutil.which("semgrep"):
        report.warning(
            "semgrep: binary not found — thousands of static-analysis rules skipped. "
            "Install via 'brew install semgrep' or 'pipx install semgrep'."
        )
        return 0

    issues = 0
    try:
        result = subprocess.run(
            ["semgrep", "--config", "p/security-audit", "--config", "p/secrets",
             "--json", "--quiet", "--no-rewrite-rule-ids",
             "--metrics", "off", str(plugin_path)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        report.warning("semgrep: timed out after 300s — scan aborted")
        return 0
    except FileNotFoundError:
        report.warning("semgrep: binary disappeared between probe and exec")
        return 0

    if not (result.stdout or "").strip().startswith("{"):
        if result.returncode == 0:
            report.passed("semgrep: no findings (security-audit + secrets packs clean)")
        return 0

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        report.info(f"semgrep: could not parse JSON output (exit {result.returncode})")
        return 0

    severity_map = {
        "ERROR": "major",
        "WARNING": "minor",
        "INFO": "info",
    }
    for finding in data.get("results", []):
        if not isinstance(finding, dict):
            continue
        rule_id = finding.get("check_id") or "?"
        message = (finding.get("extra", {}) or {}).get("message", "")[:80]
        severity = (finding.get("extra", {}) or {}).get("severity", "WARNING")
        cpv_level = severity_map.get(severity, "warning")
        rel = finding.get("path", "?")
        try:
            rel = str(Path(rel).resolve().relative_to(plugin_path.resolve()))
        except (ValueError, OSError):
            pass
        line_no = (finding.get("start", {}) or {}).get("line", 0)
        # Skip CPV's own validator regex sources + tests + fixtures
        # under the same hash-gated self-scan rule applied elsewhere.
        if cpv_self_scan_skip(rel):
            continue
        cpv_level_eff = effective_severity(cpv_level, rel)
        getattr(report, cpv_level_eff)(f"semgrep {rule_id}: {message}", rel, line_no)
        issues += 1

    if issues == 0 and result.returncode == 0:
        report.passed("semgrep: no findings (security-audit + secrets packs clean)")
    return issues


def validate_security(
    plugin_path: Path,
    enable_tirith: bool = True,
    enable_trufflehog: bool = True,
    enable_gitleaks: bool = True,
    enable_semgrep: bool = True,
) -> ValidationReport:
    """Run all security validations on a plugin directory.

    This function performs comprehensive security analysis including:
    Traditional: injection, path traversal, secrets, user paths, dangerous files, permissions
    AI-specific: prompt injection, data exfiltration, supply chain, credential harvest,
    sandbox escape, hook abuse, MCP abuse, agent impersonation, permission escalation
    Phase 1-4 net-new: ~75 RC-NN rules (unicode, MCP, persistence, exfil, evasion, etc.)
    External: cc-audit (npx), tirith (PATH/docker/nix), trufflehog, gitleaks, semgrep

    Args:
        plugin_path: Path to the plugin directory
        enable_tirith: When False, skip Check #17 tirith.
        enable_trufflehog: When False, skip trufflehog (RC-102 part 1).
        enable_gitleaks: When False, skip gitleaks (RC-102 part 2).
        enable_semgrep: When False, skip semgrep (RC-102 part 3).

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

    # Detect whether the target IS the CPV plugin itself (any deployment
    # location). When True, per-file scanners skip CPV's own pattern-defining
    # source (validator scripts, fix-validation references, security-test
    # fixtures) — those files necessarily contain every detection pattern
    # CPV knows about, and scanning them always self-matches.
    #
    # The skip is gated by SHA256 verification against `.cpv-self-hashes.json`
    # so name-based spoofing cannot evade the scan, and tampering with the
    # validator source itself shows up as a "modified, scanning normally"
    # warning rather than a silent skip.
    self_scan = is_cpv_self_scan(plugin_path)
    _set_cpv_self_scan(self_scan, plugin_root=plugin_path, notice_report=report)
    if self_scan:
        report.info(
            "CPV self-scan mode active — skipping CPV-internal pattern-defining "
            "source (validator scripts, fix-validation references, security tests) "
            "after SHA256 verification against .cpv-self-hashes.json. Files that "
            "match the name pattern but fail hash check (modified or spoofed) are "
            "scanned normally."
        )

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

    # --- Phase 4 — Minor / informational + verdict-tier (RC-85/86/87/88/103/104) ---
    phase4_issues = check_phase4_all(plugin_path, report)
    if phase4_issues == 0:
        report.passed("No Phase 4 findings (minor/info + observability)")

    # --- Phase 9 — RC-76 stemmed semantic injection classifier ---
    phase9_issues = check_phase9_stemmed_injection(plugin_path, report)
    if phase9_issues == 0:
        report.passed("No Phase 9 findings (RC-76 stemmed semantic injection)")

    # --- Phase 10 — RC-73/74/75 AST-based Python taint engine ---
    phase10_issues = check_phase10_taint(plugin_path, report)
    if phase10_issues == 0:
        report.passed("No Phase 10 findings (RC-73/74/75 taint source→sink)")

    # --- RC-103 disposition — emitted as a single INFO line ---
    counts = {
        "CRITICAL": sum(1 for r in report.results if r.level == "CRITICAL"),
        "MAJOR": sum(1 for r in report.results if r.level == "MAJOR"),
        "MINOR": sum(1 for r in report.results if r.level == "MINOR"),
        "WARNING": sum(1 for r in report.results if r.level == "WARNING"),
    }
    verdict = disposition(counts)
    report.info(
        f"RC-103 disposition: {verdict} (counts: "
        f"CRITICAL={counts['CRITICAL']} MAJOR={counts['MAJOR']} "
        f"MINOR={counts['MINOR']} WARNING={counts['WARNING']})"
    )

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

    # Check 18-20 — Phase 5 specialist tools (RC-102). All optional.
    if enable_trufflehog:
        check_trufflehog(plugin_path, report)
    if enable_gitleaks:
        check_gitleaks(plugin_path, report)
    if enable_semgrep:
        check_semgrep(plugin_path, report)

    return report


# =============================================================================
# CLI Main
# =============================================================================


def _read_plugin_version(plugin_path: Path) -> str:
    """Read the plugin's declared version from .claude-plugin/plugin.json.

    Returns "0.0.0" if the manifest is missing or unparseable. Used to stamp
    SARIF tool.driver.version so downstream consumers can correlate findings
    with a specific plugin release.
    """
    manifest = plugin_path / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return "0.0.0"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return str(data.get("version", "0.0.0"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return "0.0.0"


def main() -> int:
    """CLI entry point for standalone security validation.

    First action: verify CPV's own source has not been tampered with by
    checking each validator file's SHA256 against the GitHub-published
    canonical manifest for the running version. On mismatch, exits with
    code 2 and refuses to run — a tampered validator cannot be trusted
    to produce honest findings.

    Set `CPV_SKIP_GITHUB_INTEGRITY=1` to bypass for development.
    """
    # FIRST: verify validator integrity against GitHub canonical hashes.
    # Done before argparse so even `--help` is gated by integrity.
    from cpv_integrity import verify_self_integrity  # noqa: PLC0415
    verify_self_integrity(quiet=True)

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
    parser.add_argument("--no-trufflehog", action="store_true",
                        help="Skip trufflehog (Phase 5 RC-102 part 1).")
    parser.add_argument("--no-gitleaks", action="store_true",
                        help="Skip gitleaks (Phase 5 RC-102 part 2).")
    parser.add_argument("--no-semgrep", action="store_true",
                        help="Skip semgrep (Phase 5 RC-102 part 3).")
    parser.add_argument("--sarif-out", type=Path, default=None,
                        help="Also emit findings as SARIF 2.1.0 JSON to the given path "
                             "(RC-105). Compatible with GitHub code scanning.")
    parser.add_argument("--sbom-out", type=Path, default=None,
                        help="Emit a CycloneDX 1.6 SBOM of declared dependencies to the "
                             "given path (RC-106). Reads package.json, requirements*.txt, "
                             "pyproject.toml, Cargo.toml, go.mod.")

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
    report = validate_security(
        plugin_path,
        enable_tirith=not args.no_tirith,
        enable_trufflehog=not args.no_trufflehog,
        enable_gitleaks=not args.no_gitleaks,
        enable_semgrep=not args.no_semgrep,
    )

    # Optional SARIF emit (RC-105) — always run when requested, regardless of
    # whether the user also asked for stdout JSON or a markdown report.
    if args.sarif_out is not None:
        from cpv_sarif_writer import write_sarif  # local import to keep cold-path cheap
        plugin_version = _read_plugin_version(plugin_path)
        sarif_path = write_sarif(
            report.results,
            args.sarif_out,
            plugin_path,
            tool_version=plugin_version,
        )
        print(f"SARIF report written to {sarif_path}", file=sys.stderr)

    # Optional CycloneDX SBOM (RC-106) — orthogonal to findings; reads manifests.
    if args.sbom_out is not None:
        from cpv_sbom_writer import write_sbom  # local import to keep cold-path cheap
        plugin_version = _read_plugin_version(plugin_path)
        sbom_path = write_sbom(
            plugin_path,
            args.sbom_out,
            tool_version=plugin_version,
        )
        print(f"CycloneDX SBOM written to {sbom_path}", file=sys.stderr)

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
