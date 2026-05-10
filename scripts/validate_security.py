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
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cpv_parametrize_body_predicate import is_parametrize_body_line
from cpv_pattern_source_predicate import is_pattern_source_line
from cpv_scanner_cache import (
    CacheKey,
    ScannerCache,
    get_scanner_version,
    sha256_of_args,
    tree_merkle,
)
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
    detect_multilayer_encoded_payload,
    disposition,
    effective_severity,
    find_obfuscated_exec,
    find_stemmed_injection_signal,
    find_tag_block_chars,
    find_zero_width_chars,
    get_gitignore_filter,
    has_mixed_script,
    has_negation_guard_nearby,
    has_trust_boundary_context,
    is_binary_file,
    is_compromised_package,
    is_doc_path,
    is_in_fenced_code_block,
    is_pth_with_exec,
    is_sample_file,
    is_shadowed_tool_name,
    is_test_path,
    is_typosquat,
    print_report_summary,
    save_report_and_print_summary,
)

# =============================================================================
# Per-file safety limits (issue #15 — scan_all_files() deadlock prevention)
# =============================================================================

# Always-skip basenames — runtime artifacts that other tools (Cisco
# skill-scanner, cc-audit, semgrep, etc.) leave behind in plugin trees.
# These are NOT plugin source: they're scan output dumps that contain
# every pattern the source-scanner ever flagged (literal `eval(...)`,
# `curl ... | sh`, `/Users/foo/...` etc. quoted in JSON snippets).
# Scanning them produces a flood of FPs against the SAME rules that
# already fired against the actual source. Skip them at the file-walk
# level so the per-line scanners never see them.
ALWAYS_SKIP_BASENAMES: frozenset[str] = frozenset(
    {
        ".cpv-cisco-scan.json",  # Cisco skill-scanner output (CPV runs the scanner)
        ".plugin-self-hashes.json",  # plugin integrity manifest (TRDD-bbff5bc5 canonical)
        ".cpv-self-hashes.json",  # legacy compat copy (removed in v2.53.0)
    }
)


# Per-file size cap — files larger than this are skipped with a WARNING and
# counted as `oversize_skipped`. Pathological inputs (50MB minified JS bundles,
# concatenated SQL dumps, etc.) make every per-line scanner explode into huge
# per-line list allocations 9+ times per file (one per scanner) — the worker
# pins a CPU core indefinitely while making zero scanning progress. The 8 MiB
# default comfortably covers every realistic plugin source file; minified
# bundles and SQL dumps almost always exceed it. Override via env var when
# scanning a corpus that legitimately contains larger source files (e.g.
# embedded datasets in a plugin package).
DEFAULT_MAX_SCAN_BYTES = 8 * 1024 * 1024  # 8 MiB
MAX_SCAN_BYTES = int(os.environ.get("CPV_MAX_SCAN_BYTES", str(DEFAULT_MAX_SCAN_BYTES)))


# Identity-keyed one-shot cache for the per-file line split (issue #15 — split
# the file content ONCE, not 9 times). Previously each of the 9 scanner
# functions split the content independently, allocating 9 huge per-line lists
# for the same content. With this cache, `_split_lines(content)` returns the
# cached list when called repeatedly with the same `content` object during one
# file's scan pass, dropping that 9× overhead to 1×.
#
# We key by `id(content)` because Python `str` is unhashable for our purposes
# (a 50MB content hash would itself be O(N)) and `scan_all_files()` keeps the
# same `content` reference alive across all 9 scanner calls — `id()` is stable
# for that lifetime and uniquely identifies the in-flight content object.
#
# The cache is one-slot (the most recent content), keyed by id, dropped on
# the next call with a different id. This is correct because `scan_all_files`
# processes files sequentially per worker and finishes all 9 scans on one
# content before moving on; there is no concurrent access pattern that would
# need a multi-slot cache.
_split_lines_last_id: int | None = None
_split_lines_last_value: list[str] = []


# =============================================================================
# Per-scan step-status tracker (visibility for which steps actually ran)
# =============================================================================
#
# Issue: it was unclear from the compact summary whether the scanner
# actually ran every step on every file, or silently skipped large parts of
# the work. The step log captures, for each numbered step in
# `validate_security()`, exactly one of:
#
#   - "COMPLETED" — step ran end-to-end on the target tree
#   - "RAN"       — external scanner ran (clean or with findings)
#   - "SKIPPED"   — step was deliberately not run (e.g. external scanner's
#                   binary is missing, or test isolation knob was off)
#   - "FAILED"    — step started but raised / timed out before completing
#
# `validate_security()` resets the log at start, populates it inline as
# each step runs, and exposes `get_scan_step_log()` / `format_scan_step_table()`
# so the CLI can render a table next to the report path. The log is a
# module-level global because the natural place to fill it is inside each
# step in `validate_security()` — wrapping it in a class would force every
# existing caller through a refactor for no extra benefit. Process-local;
# `_reset_scan_step_log()` clears it for each new top-level call.

_scan_step_log: list[dict[str, Any]] = []


def _reset_scan_step_log() -> None:
    """Clear the per-scan step log. Called at the top of validate_security()."""
    global _scan_step_log
    _scan_step_log = []


def _record_step(
    num: int,
    name: str,
    status: str,
    *,
    findings: int = 0,
    files: str = "",
    details: str = "",
) -> None:
    """Append one step's status to the per-scan step log.

    Args:
        num: Step number (1..N) — preserves order in the rendered table.
        name: Short human-readable name (e.g. "Injection scan").
        status: One of "COMPLETED" / "RAN" / "SKIPPED" / "FAILED".
        findings: Number of issues this step contributed to the report.
        files: File coverage summary (e.g. "53 scanned, 2 skipped").
        details: Free-form note explaining a SKIPPED/FAILED status.
    """
    _scan_step_log.append(
        {
            "num": num,
            "name": name,
            "status": status,
            "findings": findings,
            "files": files,
            "details": details,
        }
    )


def get_scan_step_log() -> list[dict[str, Any]]:
    """Return a snapshot of the most-recent scan's step log."""
    return list(_scan_step_log)


def format_scan_step_table(steps: list[dict[str, Any]] | None = None) -> str:
    """Render the step log as a Markdown table.

    Returns the empty string when there are no steps to render (caller can
    short-circuit on falsy result).
    """
    if steps is None:
        steps = _scan_step_log
    if not steps:
        return ""
    glyph = {
        "COMPLETED": "[OK] COMPLETED",
        "RAN": "[OK] RAN",
        "SKIPPED": "[--] SKIPPED",
        "FAILED": "[!!] FAILED",
    }
    lines = [
        "| #  | Step                                  | Status         | Findings | Files / Details |",
        "|----|---------------------------------------|----------------|---------:|-----------------|",
    ]
    for s in steps:
        status = glyph.get(s["status"], s["status"])
        coverage = s.get("files") or s.get("details") or ""
        lines.append(f"| {s['num']:>2} | {s['name']:<37} | {status:<14} | {s['findings']:>8} | {coverage} |")
    return "\n".join(lines)


def _is_always_skip_basename(file_ref: str) -> bool:
    """Return True iff `file_ref`'s basename is in ALWAYS_SKIP_BASENAMES.

    Helper for external-scanner parsers (cc-audit, trufflehog, semgrep,
    tirith, Cisco — v2.48 dropped gitleaks) to filter out runtime-artifact
    files at the finding level — same skip the in-process scanners apply
    at the file-walk level. Without this, an external scanner re-runs
    after a Cisco scan would surface findings against the leftover
    `.cpv-cisco-scan.json` dump.

    Empty / falsy refs return False.
    """
    if not file_ref:
        return False
    return Path(str(file_ref)).name in ALWAYS_SKIP_BASENAMES


def _split_lines(text: str) -> list[str]:
    """Return `text.split("\\n")`, cached by id(text) for one shot.

    Drops 9× duplicate `str.split("\\n")` allocations to 1× per file. The
    cache holds only the most recently-split text's lines — when called
    with a different `text` object, the cache is replaced.

    Thread-safety: this helper is process-local (one cache per worker
    process). Worker processes run their own copy and never share state, so
    no lock is required. Inside a single worker, the helper is invoked
    sequentially across the 9 scanners on the same `text` ref.
    """
    global _split_lines_last_id, _split_lines_last_value
    cid = id(text)
    if cid == _split_lines_last_id:
        return _split_lines_last_value
    _split_lines_last_value = text.split("\n")
    _split_lines_last_id = cid
    return _split_lines_last_value


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

# Pipe to shell patterns - extremely dangerous. Every pattern uses
# `(?<!\|)` to reject the logical-OR prefix `||` — `if x || sh.foo()`
# in Rust/JS/Go is not a shell pipe.
PIPE_TO_SHELL_PATTERNS = [
    (
        re.compile(r"(?<!\|)\|\s*sh\b"),
        "[RC-114] Pipe-to-shell `| sh` — executes whatever produced the upstream "
        "stdout, no signature/integrity check. Fix: download to a file, "
        "verify checksum/signature, then invoke explicitly. Pattern catches "
        "the classic `curl … | sh` install footgun",
    ),
    (
        re.compile(r"(?<!\|)\|\s*bash\b"),
        "[RC-115] Pipe-to-shell `| bash` — same RCE risk as RC-114 with bash "
        "explicitly named. Fix: download, verify, then `bash <file>`",
    ),
    (
        re.compile(r"(?<!\|)\|\s*zsh\b"),
        "[RC-116] Pipe-to-shell `| zsh` — same RCE risk as RC-114 with zsh "
        "explicitly named. Fix: download, verify, then `zsh <file>`",
    ),
    (
        re.compile(r"(?<!\|)\|\s*ksh\b"),
        "[RC-117] Pipe-to-shell `| ksh` — same RCE risk as RC-114 with ksh "
        "explicitly named. Fix: download, verify, then `ksh <file>`",
    ),
    (
        # `(?<!\|)` rejects `||` (logical OR) followed by the identifier
        # `source` — common in Rust/JS/Go where `if x || source.foo()` is
        # not a shell pipe. Real shell pipe-to-source is always a single
        # `|` followed by optional whitespace and `source`.
        re.compile(r"(?<!\|)\|\s*source\b"),
        "[RC-118] Pipe-to-source `| source` — like pipe-to-shell but loads "
        "into the current shell context, also leaking env vars and aliases. "
        "Fix: never source remote-fetched content; download, audit, source explicitly",
    ),
    (
        re.compile(r"(?<!\|)\|\s*\.\s"),
        "[RC-119] Pipe-to-dot `| . ` (POSIX shorthand for `source`) — same risk as RC-118. Fix: same as RC-118",
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
    # Python-specific. The leading `(?<![.\w])` lookbehind prevents matching
    # method calls like `regex.exec(content)` (a JavaScript regex method —
    # NOT the dangerous Python `exec()`) and identifier-suffixed names like
    # `headerRe.exec(`. Only the bare top-level `eval(`/`exec(` builtin call
    # is the RCE risk; method dispatch via `obj.exec()` is unrelated.
    (
        re.compile(r"(?<![.\w])eval\s*\("),
        "[RC-122] Python `eval(…)` — evaluates an arbitrary Python expression; "
        "trivial RCE if any operand crosses an attacker boundary. Fix: use "
        "`ast.literal_eval` for data-only parsing, or write an explicit parser "
        "for the format you actually need",
    ),
    (
        re.compile(r"(?<![.\w])exec\s*\("),
        "[RC-123] Python `exec(…)` — runs an arbitrary statement block; "
        "trivial RCE if any operand crosses an attacker boundary. Fix: refactor "
        "to call a real function. Common-OK: a documentation file explaining "
        "what `exec()` does (CPV's own taint-engine source documents this); "
        "JavaScript `<regex>.exec(<str>)` is a regex-method call (no RCE) and "
        "is excluded by the leading-context lookbehind",
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
        "[RC-126] JavaScript `new Function(…)` — eval-equivalent. Same fix as RC-125",
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
        'config keys like `extraPaths: ["../scripts"]`, doc snippets',
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
        'skill content"); for educational content explaining the attack, '
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
        'Fix: rephrase to plain words like "system instructions", or fence',
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
    # v2.46 FP-I — Must require URL/DNS context to avoid matching long
    # markdown filenames in links and filesystem paths. The previous
    # regex `[A-Za-z0-9+/=]{40,}` matched paths
    # (`apps/myapp.png` after stripping spaces) and link filenames
    # (`(release-automation-part1-complete-workflow.md)`). Real DNS
    # tunneling shows up after `://` or as bare hostnames in `dig`/
    # `nslookup`/`host` calls. Requires either a URL prefix
    # (`(?:https?://|//|@)`) or a DNS-resolution-tool prefix
    # (`(?:dig|nslookup|host|drill|kdig)\s+`) before the long-label
    # match. Keep the `+/=` chars (base64 padding — tunneling PoCs
    # actually use them in subdomain labels) but DROP `/` from the
    # char class so paths can't match. Also drop `_` and `-` (URL-safe
    # base64) to keep the match conservative — false-negative on URL-
    # safe-base64 tunneling is an acceptable trade for eliminating
    # ~146 FPs across docs.
    (
        re.compile(
            r"(?:(?:https?://|//|@)|(?:\b(?:dig|nslookup|host|drill|kdig)\s+))"
            r"[A-Za-z0-9+=]{40,}\.(?:[a-z0-9-]{1,63}\.){0,4}[a-z]{2,}\b"
        ),
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
        "permission prompts wholesale. As of CC v2.1.126 the blast radius "
        "expanded: writes to `.claude/`, `.git/`, `.vscode/`, and shell "
        "config files (`~/.bashrc`, `~/.zshrc`, etc.) are ALSO bypassed (only "
        "catastrophic removal commands still prompt). Plugins must NOT "
        "recommend this. "
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

# TRDD-bbff5bc5: new canonical manifest filename. The legacy alias is kept
# for one release so any tooling that imports the old constant name keeps
# working. Both removed in v2.53.0.
PLUGIN_SELF_HASH_MANIFEST_NAME = ".plugin-self-hashes.json"
PLUGIN_SELF_HASH_MANIFEST_NAME_LEGACY = ".cpv-self-hashes.json"
CPV_SELF_HASH_MANIFEST_NAME = PLUGIN_SELF_HASH_MANIFEST_NAME_LEGACY  # deprecated alias


# v2.42 — context-aware classifier wiring (TRDD-fe006962). When active,
# every finding for a rule that has a registered classifier is routed
# through `cpv_fp_classifier.classify_rule` and the verdict is
# translated via `apply_verdict` into either the declared severity, a
# demoted severity, or a suppression. Off by default — set by
# `validate_security(..., with_classifier=True)`. The plugin meta dict
# is loaded once at activation time and reused for every classifier
# call so the per-finding overhead stays in O(1) substring matching.
_CLASSIFIER_ACTIVE: bool = False
_CLASSIFIER_PLUGIN_META: dict = {}
# Step 4 (TRDD-fe006962) — escalation tier flag. When True, classifier
# verdicts of `DEFINITE_TP` are translated to a one-tier severity bump
# via `apply_verdict(allow_escalation=True)`. Default is False so the
# rollout never inflates findings without an explicit `--extreme` opt-in.
# This flag is meaningful ONLY when `_CLASSIFIER_ACTIVE is True`; the
# legacy v2.41 binary-guard path never escalates because it never asks
# the classifier in the first place.
_CLASSIFIER_ESCALATE: bool = False


def _file_role_from_path(rel_path: str) -> str:
    """Map a plugin-relative path to the classifier's role taxonomy.

    Mirrors `cpv_validation_common.is_test_path` / `is_doc_path` /
    `is_sample_file` so the classifier sees the same role string the
    rest of CPV uses for severity demotion.
    """
    rel = rel_path.replace("\\", "/").lower()
    if "/tests/fixtures/" in rel or rel.startswith("tests/fixtures/"):
        return "fixture"
    if is_test_path(rel_path):
        return "test"
    if is_doc_path(rel_path):
        return "doc"
    if is_sample_file(rel_path):
        return "sample"
    return "source"


def _set_classifier_active(
    active: bool,
    plugin_root: Path | None = None,
    *,
    with_extreme: bool = False,
) -> None:
    """Toggle the classifier and pre-load `plugin.json` for `Context.plugin_meta`.

    `with_extreme` (Step 4 of TRDD-fe006962) toggles the escalation tier:
    when True AND the classifier is active, `DEFINITE_TP` verdicts are
    promoted one severity tier (e.g. MAJOR → CRITICAL). The flag is
    silently forced to False when `active=False` because escalation lives
    on the classifier path — the legacy v2.41 binary guards never ask
    the classifier. Forcing the flag off in that case keeps an
    accidental `with_extreme=True` from creating a "phantom" escalation
    that the rest of the code path never reads.
    """
    global _CLASSIFIER_ACTIVE, _CLASSIFIER_PLUGIN_META, _CLASSIFIER_ESCALATE
    _CLASSIFIER_ACTIVE = active
    if active and plugin_root is not None:
        # Imported lazily so a CPV install without `cpv_fp_classifier_rules`
        # (e.g. partial deploy) still runs in legacy v2.41-binary-guard mode.
        try:
            from cpv_fp_classifier_rules import load_plugin_meta  # noqa: PLC0415

            _CLASSIFIER_PLUGIN_META = load_plugin_meta(plugin_root)
        except ImportError:
            _CLASSIFIER_ACTIVE = False
            _CLASSIFIER_PLUGIN_META = {}
    else:
        _CLASSIFIER_PLUGIN_META = {}
    # Escalation only matters when the classifier is actually on. Use the
    # MODULE GLOBAL (`_CLASSIFIER_ACTIVE`), not the local `active`
    # parameter — the lazy import above can downgrade `active=True` to
    # `_CLASSIFIER_ACTIVE=False` on a partial install. Pinning escalate
    # to the post-import classifier state makes
    # `_CLASSIFIER_ESCALATE → _CLASSIFIER_ACTIVE` a hard invariant.
    _CLASSIFIER_ESCALATE = bool(_CLASSIFIER_ACTIVE and with_extreme)


def _classifier_decision(
    rule_id: str,
    declared_severity: str,
    line: str,
    surrounding_lines: tuple[str, ...] | list[str],
    file_role: str,
    file_path: str,
) -> tuple[str | None, str]:
    """Run the classifier for `rule_id` (if active) and return (severity, note).

    `severity is None` → suppress the finding entirely.
    `severity == declared_severity` → emit at the declared level.
    Anything else → emit at the demoted (or escalated) level.

    `note` is a short human-readable rationale; callers can append it
    to the message or ignore it. When the classifier is inactive or
    the rule has no registered classifier, returns the declared
    severity unchanged with an empty note (legacy behaviour).
    """
    if not _CLASSIFIER_ACTIVE:
        return declared_severity, ""
    try:
        from cpv_fp_classifier import (  # noqa: PLC0415
            Context,
            apply_verdict,
            classify_rule,
        )
    except ImportError:
        return declared_severity, ""

    ctx = Context(
        rule_id=rule_id,
        matched_text=line,
        line_number=0,
        line=line,
        surrounding_lines=tuple(surrounding_lines),
        file_role=file_role,
        file_path=file_path,
        plugin_meta=_CLASSIFIER_PLUGIN_META,
    )
    verdict = classify_rule(rule_id, ctx)
    # Step 4 — escalation tier. The `allow_escalation` flag is opt-in
    # via `--extreme`; default behaviour is unchanged (DEFINITE_TP =
    # REAL severity). `_CLASSIFIER_ESCALATE` is gated on
    # `_CLASSIFIER_ACTIVE` inside `_set_classifier_active`, so reading
    # it here is safe even if the classifier became inactive between
    # the activation call and this line.
    action = apply_verdict(verdict, declared_severity, allow_escalation=_CLASSIFIER_ESCALATE)
    return action.report_severity, action.note


def _set_cpv_self_scan(
    active: bool,
    plugin_root: Path | None = None,
    notice_report: ValidationReport | None = None,
) -> None:
    """Set the module-level CPV-self-scan flag and load the hash manifest.

    Loads the canonical hash manifest used to gate self-scan skips. Two
    sources, picked by trust level:

    1. **Target IS the running CPV** — same plugin_root as where this
       module was loaded from. The local `.plugin-self-hashes.json`
       (or legacy `.cpv-self-hashes.json` for one release) is
       trustworthy because the running CPV was already integrity-verified
       against GitHub at startup (see _plugin_verify_hashes.verify_self_integrity).

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
    is_running_cpv = target_root == running_cpv_root

    if is_running_cpv:
        # Trust the local manifest — running CPV's integrity was already
        # verified against GitHub at startup by _plugin_verify_hashes.
        manifest = _load_local_manifest(target_root, notice_report)
    else:
        # Target claims to be CPV but isn't the validator instance running.
        # Fetch the canonical manifest from GitHub for the target's
        # claimed version. If we can't reach GitHub, refuse to skip —
        # better to surface false-positives than to silently miss a
        # malicious plugin that spoofed its identity.
        target_version = _read_target_version(target_root)
        try:
            from _plugin_verify_hashes import fetch_canonical_manifest  # noqa: PLC0415

            manifest = fetch_canonical_manifest(target_version)
        except ImportError:
            manifest = None

        if manifest is None:
            if notice_report is not None:
                # Demoted from MAJOR to INFO: this is operational telemetry
                # (network unreachable / non-CPV target without GitHub
                # access), not a security finding. The "scan everything as
                # safe default" already protects the user; the message just
                # explains why scanning is fully on.
                notice_report.info(
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
    """Read the local `.plugin-self-hashes.json` from plugin_root.

    TRDD-bbff5bc5: prefer the new filename, fall back to the legacy
    `.cpv-self-hashes.json` for one release. The legacy fallback is
    removed in v2.53.0.
    """
    new_path = plugin_root / PLUGIN_SELF_HASH_MANIFEST_NAME
    legacy_path = plugin_root / PLUGIN_SELF_HASH_MANIFEST_NAME_LEGACY
    if new_path.is_file():
        manifest_path = new_path
    elif legacy_path.is_file():
        manifest_path = legacy_path
    else:
        if notice_report is not None:
            notice_report.major(
                f"[RC-160] CPV self-scan: hash manifest "
                f"`{PLUGIN_SELF_HASH_MANIFEST_NAME}` not found at plugin root "
                f"(also checked legacy `{PLUGIN_SELF_HASH_MANIFEST_NAME_LEGACY}`). "
                f"Without the manifest CPV cannot verify which files are "
                f"genuine validator source vs. spoofed lookalikes; falling back "
                f"to scanning every file. Fix: regenerate the manifest with "
                f"`uv run python scripts/_plugin_compute_hashes.py`."
            )
        return None
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, OSError) as e:
        if notice_report is not None:
            notice_report.major(
                f"[RC-160] CPV self-scan: hash manifest "
                f"`{manifest_path.name}` could not be parsed ({e}); "
                f"falling back to scanning every file. Fix: regenerate with "
                f"`uv run python scripts/_plugin_compute_hashes.py`."
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
    # v2.45 FP3 — Claude Code chat history exports (raw transcripts,
    # gitignored, never executed) and anthropic_dev/ (vendored Anthropic
    # docs / env-var references downloaded for development reference,
    # never executed). External scanners (cc-audit, trufflehog) flag the
    # literal `chmod` / `ANTHROPIC_API_KEY` text inside these dumps as
    # active-attack content, but they're inert documentation.
    "/.claude/chat_history/",
    "/anthropic_dev/",
    # v2.46 — Claude Code worktree directories. Each worktree is a
    # FULL CLONE of the source tree at a different branch. Findings
    # in `<plugin>/.claude/worktrees/<id>/...` are duplicates of
    # findings in the main tree (same file content, just different
    # checkout). Skip them.
    "/.claude/worktrees/",
)


def _is_dev_scratch_path(rel_or_abs: str) -> bool:
    """True for files inside a gitignored dev-scratch / design-spec dir.

    These dirs (docs_dev/, scripts_dev/, design/tasks/, …) are NEVER
    shipped — they're listed in _plugin_compute_hashes.py's `skip_dirs`
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


_PYTEST_TEST_FILE_RE = re.compile(r"(?:^|/)(?:test_[^/]+\.py|[^/]+_test\.py|conftest\.py)$")


# v2.48 P-3 — FP-corpus markdown predicate.
#
# A file is a "rule-corpus" markdown iff:
#   1. Path's basename ends in `.md`
#   2. Path contains a directory segment matching the corpus-dir regex
#      (`fp_corpus`, `fp-corpus`, `fp_fixtures`, `fixtures/fp`,
#      `fixtures/tp`)
#   3. The first 5 lines contain a structural marker:
#      - `# RC-\d+ corpus` / `# RC-\d+ — ...`
#      - `## TP examples` / `## FP examples` / `## TP exemplars`
#      - `# false-positive corpus` / `# true-positive`
#      - YAML frontmatter `corpus_kind: tp|fp`
#
# When all three hold, the file is a benchmark corpus by construction —
# the validator's regex catalog firing on its lines is meaningless noise,
# because the file is a labelled set of TP/FP examples that the bench
# harness expects to fire on (TP) or NOT fire on (FP).
#
# Wired as a file-level early-skip: the whole .md file is skipped from
# security scanning when the markers are present. This means every rule
# (RC-21, RC-22, RC-65, RC-76, RC-87, RC-93, …) is suppressed for the
# file uniformly, with no per-rule wiring needed.

_FP_CORPUS_DIR_RE = re.compile(
    r"(?:^|/)(?:fp[_\-]corpus|fp[_\-]fixtures|fixtures/(?:fp|tp))(?:/|$)",
    re.IGNORECASE,
)
_FP_CORPUS_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `# RC-99 — short rule description` (the README format)
    re.compile(r"^\s*#\s*RC-\d+\b", re.IGNORECASE),
    re.compile(r"^\s*#\s*[A-Z]{2,4}-\d+\b", re.IGNORECASE),
    # `# RC-21 corpus` / `# false-positive corpus` / `# true-positive corpus`
    re.compile(r"^\s*#+\s*(?:false[\-_ ]positive|true[\-_ ]positive)\b", re.IGNORECASE),
    re.compile(r"^\s*#+\s*\S+\s+corpus\b", re.IGNORECASE),
    # `## TP exemplars` / `## TP examples` / `## FP exemplars` / `## FP examples`
    re.compile(r"^\s*#+\s*(?:TP|FP)\s+(?:exemplars?|examples?)\b", re.IGNORECASE),
    re.compile(
        r"^\s*#+\s*(?:true[\-_ ]positives?|false[\-_ ]positives?)\b",
        re.IGNORECASE,
    ),
    # YAML frontmatter `corpus_kind: tp|fp` (HTML comment style allowed
    # too: `<!-- corpus-kind: tp -->`)
    re.compile(r"^\s*corpus[_\-]kind\s*:\s*(?:tp|fp)\b", re.IGNORECASE),
    re.compile(
        r"^\s*<!--\s*corpus[_\-]kind\s*:\s*(?:tp|fp)\b",
        re.IGNORECASE,
    ),
)


def is_fp_corpus_markdown(
    file_path: str,
    content: str | list[str] | None = None,
) -> bool:
    """v2.48 P-3 — True iff `file_path` is a rule-corpus markdown file.

    The predicate is conservative: ALL THREE conditions must hold —
    `.md` extension, corpus-shaped directory path, and a structural
    marker in the first 5 lines. A markdown file in `fixtures/` lacking
    a marker is NOT skipped (could be unrelated documentation). A
    markdown file with a marker but outside any `fp_corpus` directory
    is NOT skipped (could be coincidental terminology).

    `content` is optional — when None, the predicate returns based only
    on path shape (which is INSUFFICIENT) and falls back to False to
    avoid false-skips. Callers wanting the full predicate must supply
    content (raw string or pre-split lines). The first 5 non-empty
    lines are inspected for any of `_FP_CORPUS_MARKER_PATTERNS`.
    """
    norm = file_path.lower().replace("\\", "/")
    if not (norm.endswith(".md") or norm.endswith(".markdown")):
        return False
    if not _FP_CORPUS_DIR_RE.search(norm):
        return False
    if content is None:
        return False
    lines = _split_lines(content) if isinstance(content, str) else content
    # Inspect the first 5 NON-EMPTY lines (frontmatter / heading shape
    # tolerates blank line at top).
    inspected = 0
    for ln in lines:
        if not ln.strip():
            continue
        inspected += 1
        if inspected > 5:
            break
        if any(pat.match(ln) for pat in _FP_CORPUS_MARKER_PATTERNS):
            return True
    return False


def is_test_file_parametrize_body(
    file_path: str,
    content: str | list[str] | None,
    line_no: int,
) -> bool:
    """v2.48 P-2 — True iff `file_path` is a Python test file AND
    `line_no` is inside a `@pytest.mark.parametrize(` decorator body.

    Pytest parametrize fixtures BY CONSTRUCTION contain the very attack
    strings that security rules are designed to catch. The test asserts
    the rule fires on those strings — they are pattern fixtures, not
    live attacks. Suppressing findings on parametrize-body lines is a
    structural correctness move that applies to ANY plugin's tests, not
    just CPV's.

    The file-shape gate restricts to pytest test files
    (`test_*.py`, `*_test.py`, `conftest.py`) so the predicate cannot
    accidentally suppress a non-test file that happens to define a
    helper named `parametrize`.

    Returns False on any non-`.py` file or when content is None.
    """
    if content is None:
        return False
    if line_no < 1:
        return False
    norm = file_path.lower().replace("\\", "/")
    if not _PYTEST_TEST_FILE_RE.search(norm):
        return False
    return is_parametrize_body_line(content, line_no)


def cpv_self_scan_skip_line(
    file_path: str,
    content: str | list[str] | None,
    line_no: int,
) -> bool:
    """Return True if a specific (file, line_no) pair should be skipped
    during a CPV-self-scan OR universally for test fixture parametrize
    bodies.

    This is the line-aware companion to `cpv_self_scan_skip(file_path)`.
    It returns True when ANY of:
      - the per-file hash-anchored skip already fires (file's content
        is unchanged from the manifest), OR
      - v2.48 P-2 — the line is inside a pytest parametrize body
        (applies UNIVERSALLY, not gated on self-scan, because pytest
        fixtures are structurally pattern-source material for every
        plugin's tests), OR
      - v2.48 P-1 — the pattern-source-line predicate fires (line is
        structurally part of a rule declaration — catalog literal,
        rule-id-tagged docstring/comment, or pattern-collection
        suffix). This branch is gated on `_CPV_SELF_SCAN_ACTIVE` so
        third-party plugins don't get their pattern catalogs silenced.

    The checks compose as an OR — any signal alone is enough to suppress
    the line. The P-2 parametrize-body branch does NOT depend on
    `_CPV_SELF_SCAN_ACTIVE` because it represents structural truth
    independent of which plugin owns the file.

    `content` may be the raw file string, a pre-split list of lines, or
    None (in which case only the file-level skip is consulted — i.e.
    callers without line context still get the legacy behaviour).
    """
    if cpv_self_scan_skip(file_path):
        return True
    # v2.48 P-2 — universal parametrize-body suppression.
    if is_test_file_parametrize_body(file_path, content, line_no):
        return True
    if not _CPV_SELF_SCAN_ACTIVE:
        return False
    if content is None:
        return False
    return is_pattern_source_line(content, line_no, file_path)


def cpv_self_scan_skip(file_path: str) -> bool:
    """Return True if `file_path` should be skipped during a CPV-self-scan.

    Three-stage check:

    1. **Dev-scratch shortcut** — if the file lives in a gitignored
       dev-scratch directory (docs_dev/, design/tasks/, scripts_dev/,
       …), skip unconditionally. These dirs aren't in the hash manifest
       (_plugin_compute_hashes.py skips them), they're not shipped,
       and they exist purely to document patterns by example.
    2. **Name-based eligibility** — does the path match a CPV-internal
       file pattern (validator script, fix-validation reference, security
       test, semantic-validation reference)? If not, no skip.
    3. **Hash verification** — compute the file's actual SHA256 and look
       it up in `.plugin-self-hashes.json` (or legacy
       `.cpv-self-hashes.json` for one release). Only skip if the hash
       matches the canonical value. Hash mismatch (file modified) or
       missing entry → don't skip; the file is scanned normally.

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
        if _CPV_SELF_HASH_NOTICE_REPORT is not None and file_normalized not in _CPV_SELF_HASH_REPORTED_MISSING:
            _CPV_SELF_HASH_REPORTED_MISSING.add(file_normalized)
            # Manifest-coverage gap is operational telemetry, not a security
            # finding — the file gets scanned normally regardless. Demoted
            # from MINOR to INFO in v2.41.0 because external plugins kept
            # accumulating noise from CPV's own files when run from a
            # different working directory.
            _CPV_SELF_HASH_NOTICE_REPORT.info(
                f"[RC-161] CPV self-scan: file `{file_normalized}` matches a "
                f"self-scan pattern but is not in the hash manifest; scanning "
                f"normally. Fix: regenerate the manifest with "
                f"`uv run python scripts/_plugin_compute_hashes.py` (the "
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
        if _CPV_SELF_HASH_NOTICE_REPORT is not None and file_normalized not in _CPV_SELF_HASH_REPORTED_MODIFIED:
            _CPV_SELF_HASH_REPORTED_MODIFIED.add(file_normalized)
            _CPV_SELF_HASH_NOTICE_REPORT.warning(
                f"[RC-162] CPV self-scan: file `{file_normalized}` matches "
                f"a self-scan pattern but its SHA256 differs from the manifest "
                f"entry — scanning normally. If you edited this file, "
                f"regenerate the manifest with "
                f"`uv run python scripts/_plugin_compute_hashes.py` and "
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
    if ("/agents/" in file_normalized or file_normalized.startswith("agents/")) and basename.endswith(".md"):
        return True
    if ("/commands/" in file_normalized or file_normalized.startswith("commands/")) and basename.endswith(".md"):
        return True
    if ("/skills/" in file_normalized or file_normalized.startswith("skills/")) and basename.endswith(".md"):
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
    - Pipeline scripts: `publish.py`, `smart_exec.py`,
      `_plugin_compute_hashes.py` (canonical, TRDD-bbff5bc5),
      `compute_cpv_self_hashes.py` (legacy alias, removed in v2.53.0),
      `cc_scope_rules.py`, `_minimal_yaml.py`. The legacy
      `lint_files.py` orchestrator was retired in v2.64.0 in favour of
      `cpv_lint_engine.py` (matched by the `cpv_*` prefix above).
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
        "_plugin_compute_hashes.py",  # TRDD-bbff5bc5 canonical
        "_plugin_verify_hashes.py",  # TRDD-bbff5bc5 canonical
        "compute_cpv_self_hashes.py",  # legacy alias (removed in v2.53.0)
        "cpv_integrity.py",  # legacy alias (removed in v2.53.0)
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


def is_js_ts_file(file_path: str, content: str | None = None) -> bool:
    """JavaScript/TypeScript files use backticks for template literals.

    Backticks in JS/TS source/config (e.g. eslint.config.mjs, *.ts, *.tsx)
    are ES2015 template literals — the syntax for multi-line/interpolated
    strings. They are NEVER POSIX command substitution. Skip the
    backtick-pattern check on these files to avoid flagging code-quoted
    references inside `// comments` and template strings.

    Detection sources:

    1. Extension match — `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts`, `.tsx`,
       `.mts`, `.cts`.
    2. Shebang sniff — for executables under `bin/` etc. that ship without
       an extension (e.g. `bin/llm-ext` with `#!/usr/bin/env node`). Many
       Node CLIs are installed this way (npm, yarn, pnpm…), so the
       extension-only heuristic misses a lot of real-world JS files. Pass
       in the file's first line via the `content` arg to enable this.
    """
    if file_path.lower().endswith((".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts")):
        return True
    if content is not None:
        first_line = content.split("\n", 1)[0] if content else ""
        if first_line.startswith("#!"):
            shebang = first_line.lower()
            # Node.js, Deno, Bun, ts-node — every common JS-runtime shebang.
            if any(rt in shebang for rt in ("node", "deno", "bun", "ts-node", "tsx")):
                return True
    return False


def is_shell_like_file(file_path: str, content: str | None = None) -> bool:
    """Recognize files where shell syntax (command substitution, pipes) is expected.

    Covers:
    - Shell script extensions (.sh, .bash, .zsh, .ksh)
    - Git hooks in git-hooks/ or .git/hooks/ directories (extensionless scripts)
    - GitHub Actions YAML (.yml/.yaml inside .github/workflows/)
    - Shebang-detected shell scripts — files in `scripts/`, `bin/` etc.
      with no extension but a `#!/.../sh|bash|zsh|ksh` shebang. Pre-push
      hooks and other custom shell entry points are commonly shipped this
      way; without shebang sniffing, every `$(git rev-parse ...)` they
      contain would be flagged as RCE-suspicious.

    Pass `content` to enable the shebang sniff. Without it, only
    extension-based + path-based detection runs (preserves the existing
    contract for callers that don't have content available).
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
        # v2.45 FP2 — plugin skills routinely ship CI scaffolding under
        # `skills/<skill>/templates/*.yml` / `skills/<skill>/scripts/*.yml`
        # (e.g. amaa-cicd-design ships release-github.yml, ci-multi-platform.yml,
        # docs-generate.yml, security-scan.yml). The user copies these
        # into their `.github/workflows/` — they're declarative GitHub
        # Actions, not runtime code, and `$(grep ...)` / `$(git describe
        # ...)` in `run:` blocks is the correct shape. Recognise any
        # `.yml/.yaml` whose path is under `templates/` or `scripts/`
        # as shell-like for the command-substitution rule.
        if "/templates/" in file_normalized or file_normalized.startswith("templates/"):
            return True
        if "/scripts/" in file_normalized or file_normalized.startswith("scripts/"):
            return True
    # Common git-hook filenames in any directory (typically scripts/pre-push,
    # scripts/pre-commit, etc.). Match against the basename so location
    # doesn't matter. Hook names per githooks(5) — covers the standard set.
    basename = file_normalized.rsplit("/", 1)[-1] if "/" in file_normalized else file_normalized
    GIT_HOOK_BASENAMES = {
        "pre-commit",
        "pre-push",
        "pre-rebase",
        "pre-receive",
        "post-receive",
        "post-commit",
        "post-merge",
        "post-checkout",
        "post-update",
        "commit-msg",
        "prepare-commit-msg",
        "applypatch-msg",
        "pre-applypatch",
        "post-applypatch",
        "update",
        "fsmonitor-watchman",
        "p4-pre-submit",
        "post-rewrite",
        "sendemail-validate",
    }
    if basename in GIT_HOOK_BASENAMES:
        return True
    # Shebang sniff for extensionless shell scripts. Both forms:
    # 1. Direct path: `#!/bin/bash`, `#!/usr/bin/zsh`, `#!/sbin/sh`
    # 2. POSIX env form: `#!/usr/bin/env bash`, `#!/bin/env zsh` — the
    #    interpreter name is a whitespace-separated token AFTER `env`,
    #    so a `/bash` substring check misses it.
    if content is not None:
        first_line = content.split("\n", 1)[0] if content else ""
        if first_line.startswith("#!"):
            shebang = first_line.lower()
            if any(rt in shebang for rt in ("/sh", "/bash", "/zsh", "/ksh", "/dash", "/ash")):
                return True
            if re.search(r"\benv\s+(?:bash|sh|zsh|ksh|dash|ash)\b", shebang):
                return True
    return False


def _md_is_agent_body(file_normalized: str) -> bool:
    """v2.45 FP3 — True if `file_normalized` is a top-level agent body.

    Agent bodies are .md files DIRECTLY under `/agents/` (or whose path
    starts `agents/`). Sub-references (`agents/foo/references/x.md`,
    `agents/foo/scripts/y.md`) are documentation, not the agent's
    instruction surface — the model loads them as supplemental guidance,
    not as the prompt itself.

    `file_normalized` is expected to already be lowercased and use `/`
    separators (caller normalises).
    """
    parts = file_normalized.split("/")
    # Find the LAST occurrence of "agents" so nested paths (e.g.
    # "plugin/agents/foo.md") are recognised even when the plugin layout
    # adds a leading parent dir.
    try:
        idx = len(parts) - 1 - parts[::-1].index("agents")
    except ValueError:
        return False
    # Body shape: agents/<basename>.md exactly (one path segment after
    # `agents/`).
    return idx == len(parts) - 2


def _md_is_command_body(file_normalized: str) -> bool:
    """v2.45 FP3 — True if `file_normalized` is a top-level command body.

    Same shape as `_md_is_agent_body` but for `/commands/`. Command
    bodies are .md files DIRECTLY under `commands/` (e.g.
    `commands/cpv-validate.md`). Subdirs are docs.
    """
    parts = file_normalized.split("/")
    try:
        idx = len(parts) - 1 - parts[::-1].index("commands")
    except ValueError:
        return False
    return idx == len(parts) - 2


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


_PATTERN_DEFINITION_HINTS = (
    "re.compile(",
    "re.match(",
    "re.search(",
    "RegExp(",
    "regex.compile(",
    'r"',
    "r'",  # Python raw-string literal at start of regex
)
_PATTERN_DEFINITION_RE = re.compile(r"/[^/\n]+/[gimsuy]*")  # JS regex literal


def _line_is_pattern_definition(file_ref: str, line_number: int) -> bool:
    """True if line `line_number` of `file_ref` is a regex pattern
    definition (Python `re.compile(...)`, JS `/.../g`, or `RegExp(...)`).

    External scanners (cc-audit, gitleaks, trufflehog, semgrep) flag the
    LITERAL TEXT of credential markers / sandbox-escape patterns / etc.
    inside the BODY of a regex source — but pattern bodies are detector
    code, not exploit payloads. This helper opens the file at the
    reported line and inspects the surrounding text. Returns False on
    any I/O error (don't suppress on uncertainty).
    """
    try:
        path = Path(file_ref)
        if not path.is_file():
            return False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for i, ln in enumerate(f, start=1):
                if i == line_number:
                    if any(hint in ln for hint in _PATTERN_DEFINITION_HINTS):
                        return True
                    if _PATTERN_DEFINITION_RE.search(ln):
                        return True
                    return False
                if i > line_number:
                    break
    except (OSError, ValueError):
        pass
    return False


# v2.46 — Status-report message detection. CPV (and similar validators)
# emits report.passed("No X detected") / report.warning("Y issue") /
# print("Z found") strings as part of normal validation output. These
# strings contain rule keywords by design — `"No sandbox escape
# patterns detected"` mentions "sandbox escape" in plain English so
# the user knows what was checked. cc-audit and similar scanners flag
# such lines because the literal text matches their patterns; suppress
# this whole class.
_STATUS_REPORT_HINTS = (
    "report.passed(",
    "report.warning(",
    "report.info(",
    "report.minor(",
    "report.nit(",
    "report.major(",
    "report.critical(",
    "report.add(",
    "console.log(",
    "console.warn(",
    "console.error(",
    "logger.info(",
    "logger.warning(",
    "logger.error(",
    "log.info(",
    "log.warn(",
    "log.error(",
    'echo "',
    "echo '",
    "print(",
    "println!(",
    "eprintln!(",
)


def _line_is_status_report_message(file_ref: str, line_number: int) -> bool:
    """True if line `line_number` of `file_ref` is a status-report
    message like `report.passed("...")`, `print("...")`, etc.

    These lines contain rule keywords as part of the user-facing
    description of what was validated, not as live payloads. External
    scanners that match on the literal string body of those calls
    produce FPs by construction.
    """
    try:
        path = Path(file_ref)
        if not path.is_file():
            return False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for i, ln in enumerate(f, start=1):
                if i == line_number:
                    return any(hint in ln for hint in _STATUS_REPORT_HINTS)
                if i > line_number:
                    break
    except (OSError, ValueError):
        pass
    return False


# v2.41.0 — per-rule context guards. Each helper answers the same shape of
# question: "given the matched line, is this match a known-benign context?"
# Returning True means the rule should suppress the finding for THIS match
# while continuing to fire on other matches. The guards are intentionally
# narrow — see TRDD-fe006962 for the broader context-aware classifier work.

_RC87_DEPVERSION_RE = re.compile(
    r"\"[^\"]*(?:version|engines?|peerDeps?|deps?|@types|@[a-z][a-z0-9-]+/)"
    r"[^\"]*\"\s*:\s*\"[\^~>=<]?\s*\d",
    re.IGNORECASE,
)
_RC87_PURE_VERSION_LINE_RE = re.compile(
    r"^\s*\"version\"\s*:\s*\"[\^~>=<]?\s*\d",
    re.IGNORECASE,
)


# v2.45 FP8 — Project-history doc basenames. CHANGELOG / HISTORY /
# NEWS / README files are ALWAYS prose ABOUT the project — they may
# narrate "added support for 192.168.0.0/16 detection" without the IP
# ever being live config. The generic doc-demotion already drops them
# major→minor; this list demotes them one more tier (minor→nit), which
# is appropriate because these specific files are by-convention
# project narrative, not config.
_RC87_HISTORY_DOC_BASENAMES = frozenset(
    {
        "changelog",
        "changelog.md",
        "changelog.rst",
        "changelog.txt",
        "changes",
        "changes.md",
        "changes.txt",
        "history",
        "history.md",
        "history.rst",
        "history.txt",
        "news",
        "news.md",
        "news.rst",
        "news.txt",
        "readme",
        "readme.md",
        "readme.rst",
        "readme.txt",
        "release-notes",
        "release-notes.md",
        "release_notes.md",
        "releases",
        "releases.md",
    }
)


def _rc87_is_history_doc(file_path: str) -> bool:
    """v2.45 FP8 — True if `file_path`'s basename is a CHANGELOG /
    HISTORY / NEWS / README.

    Used by the RC-87 emit site to demote one additional tier (after
    the generic doc demotion already applied via `effective_severity`).
    These files are the canonical "history of the project" surface —
    they narrate features that were added/removed, including IP-shaped
    text, but the IPs are never live config.
    """
    base = file_path.lower().replace("\\", "/").rsplit("/", 1)[-1]
    return base in _RC87_HISTORY_DOC_BASENAMES


def _rc87_is_semver_context(line: str, file_path: str) -> bool:
    """RC-87 RFC-1918/loopback IP — skip when the match is inside a
    package-manager dependency or version field.

    Lockfiles are already skipped by `is_lockfile`, but `package.json`,
    `pyproject.toml`, `Cargo.toml`, etc. legitimately contain X.Y.Z
    version strings that match the broad IP regex (`10.[0-9.]+`,
    `192.168.[0-9.]+`). Suppressing on those filenames OR on a
    `"<key>": "<X.Y.Z>"` JSON shape eliminates the FP cluster without
    losing TPs in scripts/source files.
    """
    file_lower = file_path.lower().replace("\\", "/")
    basename = file_lower.rsplit("/", 1)[-1]
    if basename in {
        "package.json",
        "pyproject.toml",
        "cargo.toml",
        "composer.json",
        "gemfile",
        "build.gradle",
        "build.gradle.kts",
        "pubspec.yaml",
        "mix.exs",
        "deno.json",
        "bun.lock.json",
    }:
        return True
    if _RC87_PURE_VERSION_LINE_RE.match(line) or _RC87_DEPVERSION_RE.search(line):
        return True
    return False


_TEST_PATH_RE = re.compile(
    r"(?:"
    r"(?:^|/)tests?/"  # /test/ or /tests/ segment
    r"|(?:^|/)__tests?__/"  # /__tests__/ Jest convention
    r"|(?:^|/)spec/"  # /spec/ RSpec / Jasmine
    r"|(?:^|/)e2e/"  # end-to-end test directory
    r"|(?:^|/)conftest\.py$"  # pytest conftest
    r"|(?:^|/)test_[A-Za-z0-9_]+\.py$"  # pytest test_*.py
    r"|(?:^|/)[A-Za-z0-9_]+_test\.py$"  # Go-style _test.py
    r"|(?:^|/)[A-Za-z0-9_.\-]+\.test\."  # foo.test.ts/.test.js/.test.py
    r"|(?:^|/)[A-Za-z0-9_.\-]+\.spec\."  # foo.spec.ts/.spec.js
    r"|(?:^|/)test-[A-Za-z0-9_.\-]+\."  # test-foo.ts
    r"|(?:^|/)fixture[s]?/"  # /fixtures/ test data dir
    r")"
)


_I18N_DIR_RE = re.compile(r"(?:^|/)(?:locales?|i18n|lang|languages?|translations?|intl)/")
# Language-code suffix: `<basename>.<lang>.<ext>` or
# `<basename>.<lang>-<COUNTRY>.<ext>`. Common examples:
#   README.ru.md       README.zh-CN.md
#   guide.ja.md        messages.es-ES.json
#   prompt-cache-guide-ru.md  (with hyphen — also catch this)
_I18N_LANG_SUFFIX_RE = re.compile(
    r"(?:[._-])"  # separator
    r"(?:"
    # ISO 639-1 two-letter codes commonly used for translations.
    r"ar|az|bg|bn|ca|cs|da|de|el|en|es|et|fa|fi|fr|gu|he|hi|hr|hu|id|it|"
    r"ja|kk|kn|ko|lt|lv|ml|mr|ms|nb|nl|no|pl|pt|ro|ru|si|sk|sl|sq|sr|sv|"
    r"sw|ta|te|th|tr|uk|ur|vi|zh"
    r")"
    r"(?:[-_][a-z]{2,3})?"  # optional country code (lowercased path)
    r"\.[a-z0-9]+$"  # extension
)


def _is_i18n_file_path(rel_path: str) -> bool:
    """GENERAL: True for files whose path/basename signals translation
    content (locales/, i18n/, README.<lang>.md, guide-<lang>.md, etc.).

    Signals:
    1. Any path segment is one of: locales, locale, i18n, lang,
       languages, translations, intl
    2. The basename has a language-code suffix matching ISO 639-1
       (ru/zh/ja/ko/de/fr/es/it/…) optionally with country code,
       e.g. README.ru.md, prompt-cache-guide-zh-CN.md.

    These files legitimately contain Latin-acronym + non-Latin-word
    compound terminology (`API-вызов`, `JSON-файл`, `HTML-페이지`)
    that is the canonical way to render technical jargon in those
    languages — NOT a homograph attack.
    """
    if not rel_path:
        return False
    p = rel_path.replace("\\", "/").lower()
    if _I18N_DIR_RE.search(p):
        return True
    # Test the basename suffix
    basename = p.rsplit("/", 1)[-1]
    return bool(_I18N_LANG_SUFFIX_RE.search(basename))


def _is_acronym_compound(token: str) -> bool:
    """GENERAL: True for tokens of shape
    `<ASCII-acronym><sep><non-Latin-word>` — the canonical idiom for
    rendering a technical acronym in a non-Latin-script language.

    Examples that match:
      API-вызов        (Russian "API call")
      JSON-файл        (Russian "JSON file")
      HTML-페이지       (Korean "HTML page")
      MCP-серверов     (Russian "MCP servers")
      HTTP-リクエスト   (Japanese "HTTP request")
      nКэш             (escape-sequence prefix `\\n` + Russian "Cache")

    Pattern: ASCII letters (the acronym, typically 1-10 chars) +
    optional separator (`-` or `_` or `.`) + non-Latin letters. The
    two halves do NOT mix scripts inside themselves — the acronym is
    pure Latin, the descriptor is pure non-Latin. Real homograph
    attacks have INTRA-segment mixing (`pаypal` with Cyrillic `а`),
    NOT inter-segment.

    The tokenization regex `[\\w._-]{3,80}` joins Latin escape-sequence
    chars (`\\n`, `\\t`, `\\r`) with following non-Latin words because
    `\\w` matches both `n` and Cyrillic letters. Result: spurious
    tokens like `nКэш` (`\\n` + Russian "Кэш"). These follow the same
    "Latin prefix + non-Latin word" idiom and are not homographs.
    """
    # Match: ASCII word (acronym or escape-prefix), optional separator,
    # non-Latin word.
    m = re.match(
        r"^([A-Za-z][A-Za-z0-9]{0,9})"  # Latin acronym/prefix (1-10 chars)
        r"([-_.]?)"  # optional separator
        r"([^\sA-Za-z]+)$",  # non-Latin descriptor
        token,
    )
    if m is None:
        return False
    latin_prefix, separator, descriptor = m.group(1), m.group(2), m.group(3)
    # Reject if descriptor contains Latin letters at all (defensive).
    if re.search(r"[A-Za-z]", descriptor):
        return False
    # GENERAL signal to distinguish compound terms from homograph attacks:
    #
    # Homograph attacks substitute a SINGLE non-Latin glyph that looks
    # like a Latin char inside a normal-looking word: `pаypal`,
    # `gооgle`, `githuЬ`. Pattern: Latin word with 1-2 non-Latin chars
    # at the END (or interior — but tokenization splits interior).
    # Specifically: the non-Latin part is SHORT (1-2 chars) and there's
    # NO separator, AND the Latin part is the bulk of the token (≥4
    # chars) — signalling the attacker is masking a brand/word.
    #
    # Compound terms have either:
    #   - A separator (`API-вызов`, `JSON-файл`)  — explicit boundary
    #   - A non-Latin descriptor that is ≥3 chars (a real word, not a
    #     single substituted glyph) AND is longer than the Latin prefix
    #     OR the Latin prefix is short (escape-sequence single char
    #     like `\nКэш`)
    has_separator = bool(separator)
    descriptor_is_word = len(descriptor) >= 3
    latin_is_short_prefix = len(latin_prefix) <= 2
    # Compound recognition:
    if has_separator and descriptor_is_word:
        return True  # `API-вызов`, `JSON-файл`
    if not has_separator:
        # No separator — be conservative. Only accept when:
        #   (a) Latin prefix is a single char or short escape (≤2),
        #       AND descriptor is a real word (≥3 chars). Captures
        #       `nКэш`, `tШаблон`.
        if latin_is_short_prefix and descriptor_is_word:
            return True
    return False


def _is_test_file_path(rel_path: str) -> bool:
    """GENERAL: True if `rel_path` is by convention a test file.

    Single source of truth replacing the duplicated chains:
      "test_" in file_lower
      or "_test.py" in file_lower
      or "/tests/" in file_normalized
      or file_normalized.startswith("tests/")
      or "/conftest.py" in file_normalized
      or file_normalized == "conftest.py"

    spread across 4+ scan functions. Recognizes:
    - `tests/`, `test/`, `__tests__/`, `spec/`, `e2e/`, `fixtures/` dirs
    - `test_X.py`, `X_test.py` (pytest / Go conventions)
    - `X.test.{ts,js,py,...}`, `X.spec.{ts,js,...}` (Jest / Mocha)
    - `test-X.ext` (xUnit conventions)
    - `conftest.py`
    """
    if not rel_path:
        return False
    p = rel_path.replace("\\", "/")
    return bool(_TEST_PATH_RE.search(p))


def _is_box_drawing_char(ch: str) -> bool:
    """True for any Unicode codepoint in the Box Drawing block (U+2500..U+257F)
    or the Block Elements block (U+2580..U+259F).

    These two blocks together cover every glyph used to draw boxes, frames,
    rules, and shading in a fixed-width CLI panel: simple `─│┌┐└┘`,
    double-line `═║╔╗╚╝╠╣╬`, heavy `━┃┏┓┗┛`, half-blocks `▀▄`, shading
    `░▒▓`, full block `█`, etc.

    Using the codepoint range is GENERAL — it covers every glyph the Unicode
    standard reserves for box-drawing, including ones that may be added in
    future Unicode versions. Hardcoded character lists silently miss new
    chars. Reference: https://www.unicode.org/charts/PDF/U2500.pdf
    https://www.unicode.org/charts/PDF/U2580.pdf
    """
    if not ch:
        return False
    cp = ord(ch)
    return 0x2500 <= cp <= 0x259F


def _is_box_drawing_row(text: str) -> bool:
    """True when `text` opens AND closes with a box-drawing character and
    contains at least 2 box-drawing characters total.

    GENERAL predicate (replaces the v2.46 hardcoded 36-char allowlist).
    The shape is: a fixed-width terminal banner row, identical in spirit to
    a markdown `|...|` row but with Unicode borders. CLI status panels, ASCII
    art tables, dashboard frames — every flavor that uses U+2500..U+259F.

    Counting only borders (not interior padding) keeps the predicate
    conservative: a stray `─` in prose won't satisfy `>=2` matches at the
    line endpoints.
    """
    if not text:
        return False
    if not (_is_box_drawing_char(text[0]) and _is_box_drawing_char(text[-1])):
        return False
    return sum(1 for ch in text if _is_box_drawing_char(ch)) >= 2


# GENERAL: PowerShell context detection. Replaces v2.46's hardcoded cmdlet
# enumeration (`Get-Content`/`Set-Content`/`Compress-Archive`/…) with the
# Verb-Noun shape that Microsoft's cmdlet-naming standard requires for all
# PowerShell cmdlets, plus the language's other unique syntactic markers.
#
# The Verb-Noun pattern: `<ApprovedVerb>-<Noun>` where ApprovedVerb is from
# a closed list of approved verbs (Get/Set/Test/Invoke/New/Copy/Remove/Move/
# Out/Write/Add/Push/Pop/Convert/Export/Import/Format/Send/Start/Stop/
# Restart/Update/Install/Uninstall/Read/Find/Select/Sort/Group/Measure/
# Where/ForEach/Compare/Join/Split/Resolve/Wait/Use/Enable/Disable/Show/
# Hide/Lock/Unlock/Mount/Dismount/Suspend/Resume/Open/Close/Push/Pop/
# Compress/Expand). The list is ~100 verbs; we cover the most common 40+
# in a regex with `^[A-Z][a-z]+-[A-Z]` shape as a syntactic fallback.
#
# `[Type]::` is PowerShell's static-method call syntax — bash and POSIX
# shell don't have an analog.
#
# `$PSScriptRoot` / `$PSCommandPath` / `$PSCmdlet` / `$Env:Var` are
# PowerShell automatic variables.
_POWERSHELL_VERB_NOUN_RE = re.compile(
    r"\b(?:Get|Set|Test|Invoke|New|Copy|Remove|Move|Out|Write|Add|Push|Pop|"
    r"Convert|ConvertTo|ConvertFrom|Export|Import|Format|Send|Start|Stop|"
    r"Restart|Update|Install|Uninstall|Read|Find|Select|Sort|Group|Measure|"
    r"Where|ForEach|Compare|Join|Split|Resolve|Wait|Use|Enable|Disable|"
    r"Show|Hide|Lock|Unlock|Mount|Dismount|Suspend|Resume|Open|Close|"
    r"Compress|Expand|Clear|Reset|Save|Load|Backup|Restore|Build|Publish|"
    r"Register|Unregister|Connect|Disconnect|Receive|Submit|Approve|Deny|"
    r"Watch|Trace|Debug|Step|Breakpoint|Enter|Exit|Limit|Skip|Take|Tee|"
    r"Initialize|Optimize|Repair|Format|Edit|Rename|Block|Unblock|"
    r"Protect|Unprotect|Confirm|Request|Search|Checkpoint)-[A-Z][A-Za-z0-9]+\b"
)
# `[Type]::Member` static-method invocation — PowerShell-only syntax shape.
_POWERSHELL_STATIC_CALL_RE = re.compile(r"\[[A-Za-z_][A-Za-z0-9_.]*\]::")
# Automatic variables.
_POWERSHELL_AUTO_VARS_RE = re.compile(
    r"\$(?:PSScriptRoot|PSCommandPath|PSCmdlet|PSBoundParameters|"
    r"Env:[A-Za-z_][A-Za-z0-9_]*|Host|Profile|HOME|PWD|MyInvocation)\b"
)


def _is_bash_boolean_chain(line: str, match_start: int) -> bool:
    """GENERAL: True when `line` is a bash boolean-function chain
    (`if $func && $other; then`, `$has_x || skip`) where the matched
    `$VAR` at `match_start` is intentionally being CALLED as a
    no-argument command and its exit status flows into a `&&`/`||`/`;`
    sequence.

    The bash boolean-function idiom:

        has_x() { test -d X; }
        has_y() { test -f Y; }
        if $has_x && $has_y; then ...
        $has_z && do_action
        $has_w || skip_action

    Here `$has_x` etc. are POSITIONALLY commands — bash evaluates the
    variable's value and treats it as the command name. The
    word-splitting that the unquoted-variable rule is designed to
    catch is intentional in this idiom: the function name is a
    single token, and there are no user-supplied arguments.

    Detection: the `$VAR` match is followed (after at most one
    whitespace token) by `&&`, `||`, `;`, `then`, end-of-line, or
    `$VAR` (start of next chain link). The line's overall shape is a
    boolean chain (contains `&&`, `||`, starts with `if `, or matches
    `^$VAR(?:\\s+&&\\s+\\$VAR)+`).

    A real attacker pattern `$USER_INPUT --do-stuff` has ARGUMENTS
    after `$USER_INPUT` — not an `&&`/`||`/`;`/`then` token.

    `match_start` may point at the leading delimiter (`&`/`;`/`|`)
    that the UNSAFE_VARIABLE_PATTERNS regex captures BEFORE the `$`.
    We re-locate the actual `$` within the matched span.
    """
    if match_start < 0:
        return False
    # Find the actual `$VAR` within match_start..end-of-line (the
    # UNSAFE_VARIABLE_PATTERNS regex captures a leading delimiter
    # like `&`, `;`, `|`, or `^`).
    var_search = re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", line[match_start:])
    if var_search is None:
        return False
    var_end = match_start + var_search.end()
    rest = line[var_end:].lstrip()
    # Right-of-token: must be a chain operator or end-of-line.
    if (
        not rest
        or rest.startswith(("&&", "||", ";", "&", "|"))
        or rest.startswith("then")
        or rest.startswith("done")
        or rest.startswith("$")  # next chain link `$has_y`
        or rest.startswith("checks_passed=")  # idiomatic counter-bump
        or re.match(r"\w+=", rest)  # idiomatic var assignment after &&
        or rest.startswith("break")
        or rest.startswith("continue")
        or rest.startswith("return")
        or rest.startswith("exit")
    ):
        # Confirm the whole-line shape is a boolean chain or starts
        # with `if `/`while `.
        stripped = line.strip()
        if (
            stripped.startswith(("if ", "if\t", "while ", "while\t", "elif "))
            or "&&" in line
            or "||" in line
            or "; then" in line
        ):
            return True
    return False


def _is_powershell_context(file_content: str, line: str) -> bool:
    """GENERAL: True when the surrounding file or the matched line is in
    PowerShell context, NOT bash.

    Five orthogonal signals (any one suffices):
    1. YAML `shell: pwsh` directive anywhere in the file (GitHub Actions
       conventionally declares shell at the step or job level).
    2. The line uses Verb-Noun cmdlet shape (`Get-Content`,
       `Invoke-RestMethod`, `Test-Path`, `New-Item`, …).
    3. The line uses `[Type]::Member` static-method call (`[regex]::Match`,
       `[System.IO.File]::ReadAllText`).
    4. The line uses a PowerShell automatic variable
       (`$PSScriptRoot`, `$Env:PATH`, `$PSCmdlet`).
    5. The line uses PowerShell-only operators that don't exist in bash:
       `-eq`/`-ne`/`-gt`/etc. WHEN combined with `$variable` (bash uses
       `==`/`!=`/`>` for string comparison; the dash-prefixed ops are
       arithmetic in `[[ ]]` only).
    The Verb-Noun convention is enforced by Microsoft's cmdlet-naming
    standard for ALL cmdlets in PowerShell modules. Any new cmdlet a
    plugin author writes must follow it, so the predicate works for
    arbitrary modules without needing a pre-enumerated list.
    """
    # Signal 1 — YAML shell directive in file.
    if "shell: pwsh" in file_content or "shell: powershell" in file_content:
        return True
    # Signal 2 — Verb-Noun cmdlet shape on the line.
    if _POWERSHELL_VERB_NOUN_RE.search(line):
        return True
    # Signal 3 — static-method call.
    if _POWERSHELL_STATIC_CALL_RE.search(line):
        return True
    # Signal 4 — automatic variables.
    if _POWERSHELL_AUTO_VARS_RE.search(line):
        return True
    return False


# GENERAL — shell regex / pattern-arg detector. POSIX shell tools that
# accept regex / glob arguments routinely embed path-shaped fragments
# inside their pattern source: `grep -E '^[^#]*/home/'`, `sed
# 's/\*\*Schema:\*\* //'`, `awk '/^.users/.../'`. The path-traversal
# and absolute-path rules see those as live path operations, but the
# pattern body is detector code — it never reaches a filesystem call.
#
# Hints anchor on the COMMAND-NAME shape the shell uses to introduce a
# regex argument:
#   - `grep -E ` / `grep -P ` / `egrep ` / `grep -e ` / `grep -E"…"`
#   - `sed 's/` / `sed -E 's/` / `sed -e 's/`
#   - `awk '/.../{...}'` / `awk -F'…'` / `awk -v …`
#   - `find <path> -regex …` / `find <path> -name '…'` (glob source)
#   - `tr 'A-Z' 'a-z'`           (transliteration table — also pattern data)
#
# Detection is line-shape based: ANY of these cmd-name tokens appearing
# on the line is enough to mark the line as pattern-source. This covers
# both bare `grep -E '…'` and pipe forms `cmd | grep -E '…' | sed …`.
_SHELL_PATTERN_HINTS_RE = re.compile(
    # `grep` / `egrep` with REGEX-mode flag (-E, -P, -G, -e) — these flags
    # indicate the next argument is regex source. `grep -F` (fixed string)
    # is NOT in the flag set because it disables regex; same for plain
    # `grep <pattern> <file>` which uses BRE but doesn't carry the
    # explicit regex-flag signal we want to anchor on.
    r"(?:^|[\s|;&\$\(])(?:e?grep)\s+(?:-[A-Za-z]*[EePG][A-Za-z]*\s+)+"
    # Bare `egrep` is regex by definition.
    r"|(?:^|[\s|;&\$\(])egrep\s+['\"]"
    # `grep <flags> '<quoted-pattern-with-regex-metachar>'` — when the
    # quoted argument carries an unambiguous regex shape (`[…]` char
    # class, `\|` BRE alternation, `\(…\)` BRE group, `^`/`$` anchors,
    # `.*`/`.+`/`?`), the bare grep IS using regex even without -E.
    # The detector requires the metachar to appear inside the FIRST
    # quoted argument after grep — a literal grep `grep "alice" file`
    # without those chars stays unmarked.
    r"|(?:^|[\s|;&\$\(])(?:e?grep)\s+(?:-[A-Za-z]+\s+)*['\"][^'\"]*"
    r"(?:\[[^\]]*\]|\\[|()|\^|\$[^A-Za-z_]|\.\*|\.\+|\.\?|\\\.)"
    # `sed s/…/…/` (substitute) — the only sed form that takes a regex
    # source. Optional flags between sed and the s-command. The regex
    # body is delimited by the char immediately after `s` (here `/`).
    r"|(?:^|[\s|;&\$\(])sed\s+(?:-[A-Za-z]+\s+)*['\"]?[sS]/"
    # `awk` with explicit pattern body (`/regex/{…}`), -F field separator,
    # or BEGIN/END script blocks — all signal a script with regex source.
    r"|(?:^|[\s|;&\$\(])awk\s+(?:-[A-Za-z]+\s+|-v\s+\S+\s+|[-]\S+\s+)*['\"]?(?:/|\{|BEGIN|END)"
    # `find` with -regex / -name / -path / -iname / -ipath / -wholename —
    # all take a glob/regex pattern argument.
    r"|(?:^|[\s|;&\$\(])find\s+\S+\s+(?:-[A-Za-z]+\s+\S*\s+)*-(?:regex|iregex|name|iname|path|ipath|wholename)\s+"
)


def _is_shell_regex_source_line(line: str) -> bool:
    r"""GENERAL: True iff `line` invokes a POSIX shell regex/pattern tool
    (`grep -E`, `sed 's/…/…/'`, `awk '/…/{…}'`, `find … -regex …`,
    `find … -name '…'`).

    Such lines embed regex / glob fragments — including path-shaped
    sequences (`/home/`, `/Users/[^/]+`, `:\*`, `../`) — as PATTERN
    SOURCE inside the tool's argument string. The pattern body never
    crosses a filesystem call; only its semantic match against the
    target file does.

    The path-traversal / system-path / hardcoded-username scanners
    treat those substrings as live path operations and produce FPs by
    construction. The skip suppresses the match when the line shape
    is unambiguously a regex-tool invocation.

    The detector is intentionally narrow on COMMAND SHAPE:
      - `grep` must be followed by flags AND/OR a quoted regex (the
        `e?grep` covers both `grep` and `egrep`)
      - `sed` must be followed by `s/…/…/` (substitute) — the only sed
        form that takes a regex source
      - `awk` must be followed by `'/…/'` (pattern), `-F'…'` (FS), or
        `BEGIN`/`END` blocks (typical script shape)
      - `find` must be followed by `-regex`/`-name`/`-path`

    A bare `grep foo` (no -E flag, no regex shape) does NOT match. The
    rule is "this line uses a regex / pattern source", not "this line
    mentions grep".
    """
    return bool(_SHELL_PATTERN_HINTS_RE.search(line))


def _match_inside_quoted_span(line: str, m_start: int, m_end: int) -> bool:
    """GENERAL: True iff the byte range `[m_start, m_end)` lies inside a
    paired-quote span (`"…"` or `'…'`) on the same line.

    Used to suppress security findings whose match falls inside a
    string LITERAL — the most common FP source for shape-rules like
    `| bash`, `$(...)`, etc. that have meaning only when bare on a
    shell command line, NOT when they're characters inside a quoted
    string passed as a value:

        warnings+=("...curl ... | bash")            # bash array literal
        "Bash(curl * | bash)",                      # JSON allowlist entry
        sed "s|bash.*plugins/|bash ~/...|"          # sed substitution

    Tracks single and double quotes independently so a `'` inside `"…"`
    (or vice versa) does NOT count as an opening quote of a separate
    span. Backticks are NOT included here — JS template literals and
    POSIX command substitution share the same delimiter, so a backtick
    span carries different semantics depending on the language; callers
    that want to defang JS template literals should use a JS-specific
    skip path.

    Same paired-delimiter scan algorithm as the function-local
    `_is_in_quoted_string` helper inside `scan_for_stemmed_injection` —
    promoted to module scope so other scanners (pipe-to-shell, command
    substitution, eval) can reuse it without copy-paste.
    """
    for quote_ch in ('"', "'"):
        in_seg = False
        seg_start = -1
        for i, ch in enumerate(line):
            if ch == quote_ch:
                if in_seg:
                    if seg_start <= m_start and m_end <= i:
                        return True
                    in_seg = False
                    seg_start = -1
                else:
                    in_seg = True
                    seg_start = i + 1
    return False


def _rc93_is_markdown_table_row(line: str) -> bool:
    """RC-93 ≥30-contiguous-spaces — skip markdown table rows.

    Markdown column alignment (`| col1 | col2     |`) and pre-formatted
    output blocks (`╔══...══╗`) routinely produce long runs of whitespace
    that aren't visual deception. The signal is that the line is bookended
    by `|` (table row) or contains pipe-table separators (`|---|`).

    v2.44 — also recognizes table rows EMBEDDED IN string literals,
    e.g. Python source generating a markdown report:
        "| Result    | Action     |"
    The outer quotes wrap the table syntax but the content is still
    table-shaped column alignment, not visual-deception payload.

    v2.46 FP-O — also recognizes Unicode box-drawing rows commonly used
    in CLI banners and ASCII art status panels:
        "║ PLAN APPROVED                       ║"
        "╔══════════════════╗"
    These appear inside Python `lines.append("║...║")` calls for terminal
    output — same shape as markdown tables but with U+2551/U+2554/etc.
    instead of `|`. The padding inside is column-alignment whitespace,
    not off-screen-text deception.
    """
    stripped = line.strip()
    if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
        return True
    # GENERAL: Unicode box-drawing detection by codepoint range, not hardcoded
    # char list. Box-drawing block is U+2500–U+257F. Block elements (full
    # block, half blocks, shading) live in U+2580–U+259F. Both are used for
    # CLI banners and ASCII status panels — same shape as a markdown
    # `|...|` row, just with Unicode borders.
    if _is_box_drawing_row(stripped):
        return True
    # v2.46 FP-O — also handle Python/JS string-literal-wrapped box rows
    # like `"║ FOO     ║"` or `lines.append("║ FOO     ║")` or
    # `print(f"║ FOO    ║")`. Use a regex to locate the OUTERMOST quoted
    # region and check whether its content opens/closes with a box-border
    # char. This is robust to leading function-call boilerplate
    # (`lines.append(`, `report.append(`, `print(`, etc.) that the
    # earlier prefix-strip didn't cover.
    for quote_match in re.finditer(r'(?:["\'`])(.*?)(?:["\'`])', stripped):
        inner_content = quote_match.group(1).strip()
        if _is_box_drawing_row(inner_content):
            return True
    # Strip outer string-literal quotes (Python `"..."`, JS template `` `...` ``,
    # single-quote, raw-string prefix) so an embedded table still matches.
    # v2.45 FP7 — also strip trailing list/array punctuation (`,`, `;`,
    # `+`) and a trailing newline-escape (`\n`) so a Python list element
    # like `"  | Result | Action |",` or `"| col |\n"+` matches the
    # table-row shape after quote-stripping.
    inner = stripped
    # Strip trailing list-construction punctuation FIRST so the string
    # closer `"` is the new tail, then quote-stripping below removes it.
    while inner and inner[-1] in ",;+ \t":
        inner = inner[:-1]
    # Strip a trailing escape sequence like `\n` that often follows the
    # table-row text in Python-built markdown reports
    # (`"| col |\n"` is the most common shape).
    if inner.endswith("\\n"):
        inner = inner[:-2]
    for prefix in ('r"', "r'", 'f"', "f'", 'b"', "b'"):
        if inner.startswith(prefix):
            inner = inner[len(prefix) :]
            break
    inner = inner.strip("\"'`").strip()
    if inner.startswith("|") and inner.endswith("|") and inner.count("|") >= 2:
        return True
    if re.match(r"^\s*\|\s*[-:]+\s*(?:\|\s*[-:]+\s*)+\|?\s*$", line):
        return True
    return False


# v2.48 P1 — RC-63 markdown bullet inside an anti-pattern / DO-NOT block
# is documenting a behaviour the persona DOES NOT exhibit, NOT a directive
# instructing the agent to skip confirmation. Same structural shape as the
# v2.46 CLI-flag-help skip but for markdown documentation.
#
# Predicate fires when ALL of:
#   1. File extension is `.md` / `.markdown`
#   2. The matching line is a markdown bullet (regular `-`/`*`/`+` or
#      `1.` numbered, optionally inside a blockquote prefix `> `)
#   3. The surrounding context contains an "anti-pattern framer" stem,
#      where the surrounding context is the union of:
#        a. The ±5-line window around the matching line, AND
#        b. The closest preceding `^#{1,6}\s` heading within ≤30 lines
_RC63_MD_BULLET_RE = re.compile(r"^[\s>]*(?:[-*+]|\d+\.)\s")
_RC63_NEGATION_STEMS: tuple[str, ...] = (
    "does not",
    "do not",
    "never",
    "anti-pattern",
    "anti pattern",
    "antipattern",
    "forbidden",
    "wrong way",
    "must not",
    "should not",
    "avoid",
    "incorrect",
    "bad practice",
    "what not",
    "what x does not",
)
_RC63_NEGATION_RE = re.compile(
    "|".join(re.escape(s) for s in _RC63_NEGATION_STEMS),
    re.IGNORECASE,
)
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")


def _md_lookback_heading(content_lines: list[str], line_idx: int, max_lookback: int = 30) -> str | None:
    """Return the closest preceding markdown heading (level 1-6) text,
    searched up to `max_lookback` lines back from `line_idx` (0-based).

    `line_idx` is itself excluded; we walk lines `[line_idx-1 … max(0,
    line_idx-max_lookback)]`. Returns the heading line VERBATIM (with
    leading `#` markers) or None if no heading found in the window.

    Lines inside fenced code blocks should not be considered as headings,
    but a backwards walk crossing a fence boundary is still acceptable
    here — the caller already pre-filters fenced lines for the matching
    line itself.
    """
    start = line_idx - 1
    end = max(0, line_idx - max_lookback)
    for i in range(start, end - 1, -1):
        if i < 0 or i >= len(content_lines):
            continue
        if _MD_HEADING_RE.match(content_lines[i]):
            return content_lines[i]
    return None


def _md_block_negation_context(
    content_lines: list[str],
    line_idx: int,
    window: int = 5,
    heading_lookback: int = 30,
) -> bool:
    """True if the markdown context around line `line_idx` (0-based)
    contains an anti-pattern / DO-NOT framer.

    Two windows checked:
      - The ±`window`-line block surrounding the matching line.
      - All preceding `^#{1,6}\\s` headings within `heading_lookback`
        lines back (we walk every heading found, not just the closest,
        because a file may have an H1 like `# What X Does NOT Do` followed
        by an H2 that lacks the stem).
    """
    n = len(content_lines)
    lo = max(0, line_idx - window)
    hi = min(n, line_idx + window + 1)
    block_text = "\n".join(content_lines[lo:hi])
    if _RC63_NEGATION_RE.search(block_text):
        return True
    # Walk every heading in the lookback window, not just the closest.
    start = line_idx - 1
    end = max(0, line_idx - heading_lookback)
    for i in range(start, end - 1, -1):
        if i < 0 or i >= n:
            continue
        if _MD_HEADING_RE.match(content_lines[i]):
            heading_text = content_lines[i]
            if _RC63_NEGATION_RE.search(heading_text):
                return True
    return False


def _rc63_is_markdown_anti_pattern_bullet(
    rel_path: str,
    content_lines: list[str],
    line_idx: int,
) -> bool:
    """RC-63 FP guard for markdown documentation that lists what an agent /
    persona DOES NOT do.

    Predicate (general, plugin-agnostic):
      - File ends `.md` / `.markdown`
      - Matching line is a markdown bullet (`-`, `*`, `+`, or `\\d+.`)
        with optional blockquote `>` prefix
      - The ±5-line window OR any preceding heading within ≤30 lines
        contains a negation marker stem (`do not`, `never`, `anti-pattern`,
        `forbidden`, `wrong way`, `must not`, `should not`, `avoid`,
        `incorrect`, `bad practice`, `what not`)

    `line_idx` is 0-based.
    """
    rel_lower = rel_path.lower()
    if not (rel_lower.endswith(".md") or rel_lower.endswith(".markdown")):
        return False
    if line_idx < 0 or line_idx >= len(content_lines):
        return False
    line = content_lines[line_idx]
    if not _RC63_MD_BULLET_RE.match(line):
        return False
    return _md_block_negation_context(content_lines, line_idx)


# v2.48 P2 — RC-02 prose-conditional inside a markdown documentation
# section is describing orchestrator behaviour / procedure flow, not an
# attack-style time-bomb. RC-02 fires on `if X then Y` co-occurrences;
# real prompt injection lives in agent bodies that direct the model. A
# documentation file under `## Procedure`, `## Phase 6`, `## Algorithm`,
# `## Response Templates`, etc. is structurally NOT an injection
# surface — it's documenting what the orchestrator does in response to
# user input.
#
# Predicate fires when ALL of:
#   1. File extension is `.md` / `.markdown`
#   2. ANY heading within ≤30 lines back contains a documentation-role
#      stem from `_RC02_DOC_ROLE_STEMS`. We walk every heading found in
#      the lookback window (not just the closest) because files often
#      have an H1 like `# Response Templates` followed by H2/H3 sections
#      that lack the stem.
_RC02_DOC_ROLE_STEMS: tuple[str, ...] = (
    # Original stems from v2.48 sweep
    "behaviour",
    "behavior",
    "procedure",
    "phase",
    "step",
    "guidance",
    "usage",
    "algorithm",
    "flow",
    "pipeline",
    "parameters",
    "output",
    "report format",
    "template",
    "response template",
    "notification template",
    "example",
    "walk-through",
    "walkthrough",
    # v2.48 broaden — every canonical documentation-section spelling that
    # describes what code DOES (never directs the model). These all label
    # documentation regions that are STRUCTURALLY descriptive, never
    # imperative/directive. The full canonical set covers overview /
    # architecture / design / principles / conventions / rules /
    # instructions / notes / tips / gotchas / responsibilities / state /
    # lifecycle / requirements / outcomes — the universe of "this
    # describes how the system behaves" doc tropes.
    "overview",
    "summary",
    "architecture",
    "design",
    "principle",
    "principles",
    "convention",
    "conventions",
    "rule",
    "rules",
    "instructions",
    "instruction",
    "note",
    "notes",
    "tip",
    "tips",
    "important",
    "caveat",
    "caveats",
    "gotcha",
    "gotchas",
    "troubleshooting",
    "description",
    "interface",
    "contract",
    "responsibility",
    "responsibilities",
    "role",
    "roles",
    "mode",
    "modes",
    "policy",
    "policies",
    "strategy",
    "strategies",
    "protocol",
    "protocols",
    "state",
    "states",
    "lifecycle",
    "result",
    "results",
    "expectation",
    "expectations",
    "outcome",
    "outcomes",
    "requirement",
    "requirements",
)
_RC02_DOC_ROLE_RE = re.compile(
    "|".join(re.escape(s) for s in _RC02_DOC_ROLE_STEMS),
    re.IGNORECASE,
)


def _md_has_doc_role_heading(
    content_lines: list[str],
    line_idx: int,
    max_lookback: int = 30,
) -> bool:
    """True if any preceding markdown heading within `max_lookback` lines
    contains a documentation-role stem.

    Walks every `^#{1,6}\\s` heading in the lookback window (NOT just the
    closest) because a file's H1 often establishes the doc role
    (`# Response Templates`) while subordinate H2/H3 sections describe
    individual entries (`## Work Request Acknowledgment`).

    Additionally: the file's FIRST heading (typically the H1) is checked
    unconditionally regardless of distance. The H1 establishes the file's
    overall topic — once a file is titled `# Instructions` or
    `# Procedures`, every prose conditional inside it inherits that
    documentation framing. Without this fallback, long doc files would
    re-emit FPs whenever a section spans more than 30 lines.
    """
    n = len(content_lines)
    start = line_idx - 1
    end = max(0, line_idx - max_lookback)
    for i in range(start, end - 1, -1):
        if i < 0 or i >= n:
            continue
        if _MD_HEADING_RE.match(content_lines[i]):
            heading_text = content_lines[i]
            if _RC02_DOC_ROLE_RE.search(heading_text):
                return True
    # H1-fallback: the file's first heading establishes the overall topic.
    # Search the file's prefix (up to line_idx) for the FIRST markdown
    # heading and check whether it contains a doc-role stem. This is
    # bounded — once the first heading is found, the loop exits.
    for i in range(0, min(line_idx, n)):
        if _MD_HEADING_RE.match(content_lines[i]):
            return bool(_RC02_DOC_ROLE_RE.search(content_lines[i]))
    return False


def _rc02_is_md_doc_role_section(
    rel_path: str,
    content_lines: list[str],
    line_idx: int,
) -> bool:
    """RC-02 FP guard for markdown documentation sections that describe
    orchestrator / procedure behaviour.

    Predicate (general, plugin-agnostic):
      - File ends `.md` / `.markdown`
      - ANY preceding heading within ≤30 lines contains a doc-role stem
        from `_RC02_DOC_ROLE_STEMS`, OR the file's FIRST heading (H1)
        contains a doc-role stem.

    A `# Evil Agent` heading in the lookback window does NOT match — only
    doc-role stems suppress the finding. `line_idx` is 0-based.
    """
    rel_lower = rel_path.lower()
    if not (rel_lower.endswith(".md") or rel_lower.endswith(".markdown")):
        return False
    if line_idx < 0 or line_idx >= len(content_lines):
        return False
    return _md_has_doc_role_heading(content_lines, line_idx)


# v2.45 FP6 — JS/TS / Python import-statement shapes. When one of these
# matches inside an AI-facing markdown file, the line is a documentation
# snippet (a doc fragment showing what the import would look like, NOT
# a live file operation). Used by `scan_for_path_traversal` to suppress
# RC-110 / RC-112 hits on import lines that survive the fenced-block
# detector — primarily because of nested-fence edge cases (e.g.
# ```markdown ... ``` ... ``` ... ``` where the inner ``` toggles the
# state and the next batch of lines look "outside" the fence even
# though visually they're inside).
_RC110_IMPORT_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ES module — `import { Foo } from '../bar'`, `import Foo from '../bar'`,
    # `import * as Foo from '../bar'`, side-effect import `import '../bar'`.
    re.compile(r"^\s*import\b[^;\n]*['\"](?:\.\.?/|\.\.?\\)"),
    # ES module re-export — `export { Foo } from '../bar'`, `export * from '../bar'`.
    re.compile(r"^\s*export\b[^;\n]*\bfrom\b\s*['\"](?:\.\.?/|\.\.?\\)"),
    # CommonJS `require('../bar')`.
    re.compile(r"^\s*(?:const|let|var)?\s*[A-Za-z_$][\w$]*\s*=?\s*require\s*\(\s*['\"](?:\.\.?/|\.\.?\\)"),
    # Bare `require('../bar')` mid-line (e.g. assignment in narrative prose).
    re.compile(r"\brequire\s*\(\s*['\"](?:\.\.?/|\.\.?\\)"),
    # Dynamic `import('../bar')`.
    re.compile(r"\bimport\s*\(\s*['\"](?:\.\.?/|\.\.?\\)"),
    # Python — `from ..foo import Bar`, `from .foo import Bar`. The
    # leading dots represent a relative import; while the literal
    # `..` text isn't traversal, the RC-110 regex sees the chars and
    # fires on doc snippets describing module layout.
    re.compile(r"^\s*from\s+\.{1,2}[A-Za-z_]"),
)


def _rc110_is_import_statement_line(line: str) -> bool:
    """v2.45 FP6 — True if the line is a JS/TS/Python import statement.

    Suppresses RC-110 / RC-112 in AI-facing markdown where the line is
    a documentation snippet showing what an import looks like. The
    fenced-block detector handles the common case; this helper catches
    the nested-fence edge case where the toggle-based detector
    incorrectly marks an inner-fence line as "outside" a fence.
    """
    return any(pat.search(line) for pat in _RC110_IMPORT_LINE_PATTERNS)


_RC21_SUBPROCESS_PREP_HINTS = (
    "subprocess.",
    "Popen(",
    "run(",
    "check_output(",
    "check_call(",
    "spawn(",
    "execve(",
    "execvp(",
    "execv(",
    "child_process.",
    "execFile(",
    "exec(",
)


_VARIABLE_ANCHORED_PATH_PREFIX_RE = re.compile(
    r"""
    (?:                       # ANY of the shell-variable shapes:
        \$\{[A-Za-z_][A-Za-z0-9_]*\}        # ${VAR}
      | \$\([^)]+\)                         # $(cmd) command substitution
      | \$[A-Za-z_][A-Za-z0-9_]*            # $VAR
      | %[A-Za-z_][A-Za-z0-9_]*%            # %VAR%   (Windows cmd)
      | %\{[A-Za-z_][A-Za-z0-9_]*\}         # %{VAR}  (some templating)
    )
    /                         # path separator
    (?:[^\s/"'`]+/)*          # zero or more leaf segments
    \.\.                      # the `..` traversal segment
    """,
    re.VERBOSE,
)


def _is_variable_anchored_path(line: str, match_start: int) -> bool:
    """GENERAL: True when the `../` (or `..\\`) match at `match_start` is
    DOWNSTREAM of a shell-variable expansion that anchors the path base.

    Rationale: paths like `${SCRIPT_DIR}/../lib`, `$VAULT/../self`,
    `${PLUGIN_ROOT}/../shared` are NOT attacker-influenced traversal —
    the anchor is a script-managed variable and the traversal navigates
    relative to it. Same class of "the developer knows where the base is"
    that the RC-110 help text already calls out as Common-OK for
    `extraPaths: ['../scripts']`.

    The predicate looks LEFT of the match position for any of the
    canonical shell-variable shapes (`${VAR}`, `$VAR`, `$(cmd)`, `%VAR%`,
    `%{VAR}`) followed by `/` and ending with `..`. If found, the path
    is variable-anchored.

    Real attacker patterns have NO variable anchor before `../`:
        open("../" + user_input + "/" + sensitive_file)  # no anchor — flagged
        path = request.args["p"] + "../../" + target     # no anchor — flagged
    """
    if match_start <= 0:
        return False
    # Look at the prefix up to and including the matched `..` position.
    # We allow the `..` to appear anywhere after the variable+`/` anchor.
    prefix = line[: match_start + 2]  # include `..`
    return bool(_VARIABLE_ANCHORED_PATH_PREFIX_RE.search(prefix))


_VARIABLE_ANCHORED_ABSOLUTE_RE = re.compile(
    r"""
    (?:                       # ANY shell-variable shape on the LEFT
        \$\{[A-Za-z_][A-Za-z0-9_]*\}        # ${VAR}
      | \$\([^)]+\)                         # $(cmd)
      | \$[A-Za-z_][A-Za-z0-9_]*            # $VAR
      | %[A-Za-z_][A-Za-z0-9_]*%            # %VAR%
    )
    """,
    re.VERBOSE,
)


def _is_variable_anchored_absolute_path(line: str, match_start: int) -> bool:
    """GENERAL: True when the absolute-path match at `match_start` is
    INSIDE or DOWNSTREAM of a shell-variable expansion.

    Rationale: a path like `"${CCPM_DIR}/lib/project-paths.sh"` matches
    the literal `/lib/...` system-path regex, but the actual path at
    runtime depends entirely on `${CCPM_DIR}`'s value — the `/lib/` is
    a sub-component of a parametric path, NOT a hardcoded host root.

    The predicate fires when ANY shell-variable expansion (`${VAR}`,
    `$VAR`, `$(cmd)`, `%VAR%`) appears LEFT of the matched span on the
    same line. Conservative: a real attacker pattern that hardcodes a
    sensitive system path (e.g. a password database) will not have a
    variable expansion preceding it on the line.
    """
    if match_start <= 0:
        return False
    prefix = line[:match_start]
    return bool(_VARIABLE_ANCHORED_ABSOLUTE_RE.search(prefix))


# =============================================================================
# v2.48 — File-context predicates (research datasets, CSV/TSV data,
# Jupyter notebooks, lockfiles). These are GENERAL: each predicate
# matches a structural shape, never a specific plugin's path.
# =============================================================================

_RESEARCH_DATA_SEGMENTS: frozenset[str] = frozenset(
    {
        "datasets",
        "dataset",
        "fixtures",
        "fixture",
        "corpus",
        "corpora",
        "samples",
        "exemplars",
        "benchmarks",
        "benchmark",
        "golden",
        "snapshots",
        "research",
        "examples_data",
    }
)


def _is_research_data_path(rel_path: str) -> bool:
    """GENERAL: True when `rel_path` lives under a research/data
    directory (datasets/, fixtures/, samples/, benchmarks/,
    research/, …). Such files are non-executable training/reference
    data; the plugin's runtime never reaches them. Adversarial
    classifier datasets DELIBERATELY contain attack-shaped strings
    (`~/.ssh/`, `/etc/`, `/usr/`) so a downstream model learns to
    flag them — every security rule firing on every row is FP-by-
    construction here.
    """
    if not rel_path:
        return False
    segments = rel_path.replace("\\", "/").lower().split("/")
    return any(seg in _RESEARCH_DATA_SEGMENTS for seg in segments)


def _is_tabular_data_file(rel_path: str) -> bool:
    """GENERAL: True when the file extension marks it as CSV/TSV/PSV
    tabular data. Rows in such files routinely contain URLs, font
    names, and other strings that shape-match secret/path regexes
    by accident (e.g. Google Fonts URLs `:wght@300` matches
    `://user:pass@host` DB-conn pattern). The plugin runtime never
    executes a CSV row.
    """
    if not rel_path:
        return False
    return rel_path.lower().endswith((".csv", ".tsv", ".psv", ".tab"))


def _is_jupyter_notebook(rel_path: str) -> bool:
    """GENERAL: True when the file is a `.ipynb` Jupyter notebook.
    Notebooks are JSON envelopes wrapping cell content; the JSON-
    encoded `\\n`/`\\t` escapes shape-match `[A-Za-z]:\\\\` Windows-
    path patterns. A plugin's loader never reads `.ipynb`; they're
    research/tutorial artifacts living alongside runtime code.
    """
    if not rel_path:
        return False
    return rel_path.lower().endswith(".ipynb")


def _rc21_is_subprocess_prep(line: str, surrounding_lines: list[str]) -> bool:
    """RC-21 bulk env-var harvest — skip `os.environ.copy()` /
    `dict(os.environ)` when the resulting variable feeds a subprocess
    invocation within the next 5 lines (idiomatic env-prep, not exfil).
    """
    matched = line.strip()
    if not ("os.environ.copy()" in matched or "dict(os.environ" in matched):
        return False
    for nearby in surrounding_lines:
        if any(hint in nearby for hint in _RC21_SUBPROCESS_PREP_HINTS):
            return True
        if "env=" in nearby or "env =" in nearby:
            return True
    return False


_RC65_PATTERN_SOURCE_HINTS = (
    "_PATTERNS",
    "_PATTERN",
    "_RULES",
    "_HOSTS",
    "denylist",
    "blocklist",
    "blacklist",
    "deny_list",
    "block_list",
    "unsafe_hosts",
    "unsafe_urls",
    "blocked_hosts",
    "imds_hosts",
    "PRIVATE_IP",
    "INTERNAL_IP",
    "LINK_LOCAL",
    "DETECT_",
    "DETECTOR_",
)


_RC65_NETWORK_CALL_HINTS = (
    "requests.",
    "urlopen(",
    "urllib.",
    "http.client",
    "httpx.",
    "aiohttp.",
    "fetch(",
    "axios.",
    "got(",
    "needle.",
    "superagent.",
    ".get(",
    ".post(",
    ".put(",
    ".delete(",
    ".patch(",
    "curl ",
    "wget ",
    "Invoke-WebRequest",
    "Invoke-RestMethod",
    "request(",
    "open(",
)


def _rc65_is_pattern_source(line: str, surrounding_lines: list[str]) -> bool:
    """RC-65 cloud IMDS endpoint — skip when the IP literal is part of a
    detector's denylist/blocklist set definition (the validator listing
    bad endpoints to detect, not code calling them).

    A line that looks like a pattern source (denylist set member, regex
    literal, sample fixture) is suppressed only if it ALSO does not
    contain a network-call indicator. `requests.get('http://169.254.169.254/...')`
    is a network call, so even though the IP is in a string literal it
    must NOT be suppressed.
    """
    if any(hint in line for hint in _RC65_NETWORK_CALL_HINTS):
        return False
    if any(hint in line for hint in _RC65_PATTERN_SOURCE_HINTS):
        return True
    blob = "\n".join(surrounding_lines)
    if any(hint in blob for hint in _RC65_PATTERN_SOURCE_HINTS):
        return True
    return False


def _surrounding_lines(content_lines: list[str], idx: int, window: int = 4) -> list[str]:
    """Return up to `window` lines on either side of `idx` (0-based)."""
    lo = max(0, idx - window)
    hi = min(len(content_lines), idx + window + 1)
    return content_lines[lo:idx] + content_lines[idx + 1 : hi]


_CLIPBOARD_DOMAIN_HINTS = (
    "clipboard",
    "pasteboard",
    "copy-paste",
    "copy/paste",
    "pbcopy",
    "pbpaste",
    "xclip",
    "xsel",
)


def _plugin_claims_clipboard_domain(plugin_path: Path) -> bool:
    """RC-22 clipboard read — skip when the plugin's manifest declares
    clipboard handling as core functionality. Reads plugin.json's
    `description` and `keywords` fields (case-insensitive substring
    match against a small allowlist). This matches what reviewers do
    by hand: a plugin literally named 'universal-clipboard' is
    expected to read the clipboard.
    """
    pjson = plugin_path / ".claude-plugin" / "plugin.json"
    if not pjson.is_file():
        pjson = plugin_path / "plugin.json"
        if not pjson.is_file():
            return False
    try:
        meta = json.loads(pjson.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return False
    haystack_parts = []
    for k in ("name", "description", "keywords", "category"):
        v = meta.get(k)
        if isinstance(v, str):
            haystack_parts.append(v)
        elif isinstance(v, list):
            haystack_parts.extend(str(x) for x in v if isinstance(x, str))
    haystack = " ".join(haystack_parts).lower()
    return any(hint in haystack for hint in _CLIPBOARD_DOMAIN_HINTS)


def scan_for_injection(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan content for injection patterns. Returns count of issues found.

    CRITICAL: This check runs BEFORE any allowlist processing.
    Note: Shell scripts (.sh, .bash) legitimately use command substitution,
    so we only flag command substitution in non-shell files where it's unexpected.
    """
    issues_found = 0
    lines = _split_lines(content)

    file_lower = file_path.lower()

    # Determine if file is markdown - backticks are code formatting
    is_markdown = file_lower.endswith((".md", ".mdx", ".markdown"))

    # Determine if file is a shell-like script - command substitution is expected
    is_shell_script = is_shell_like_file(file_path, content)

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

    # v2.48 — non-executable data files (lockfiles, research/dataset
    # paths, CSV/TSV, .ipynb). Injection-shape strings ($(...), `...`)
    # are ubiquitous in dataset descriptions and notebook outputs.
    if (
        is_lockfile(file_path)
        or _is_research_data_path(file_path)
        or _is_tabular_data_file(file_path)
        or _is_jupyter_notebook(file_path)
    ):
        return 0

    # Python files never use backtick command substitution — backticks are RST/docstring formatting
    is_python_file = file_lower.endswith(".py")

    # Skip command substitution checks for shell scripts (expected), docs markdown, and tests
    # AI-facing markdown (skills, agents) uses backticks for formatting — skip command-sub only
    skip_command_sub = is_shell_script or (is_markdown and not is_ai_facing_markdown(file_path)) or is_test_file
    # For AI-facing markdown, still skip backtick patterns (they're code formatting)
    if is_markdown and is_ai_facing_markdown(file_path):
        skip_command_sub = True  # Backticks in .md are always formatting

    # For markdown, build fence state ONCE so the `$(...)` rule can suppress
    # findings inside fenced code blocks. Agent / command / skill docs
    # legitimately quote shell snippets (e.g., `BACKUP="/tmp/foo.$(basename
    # "$X")"`) inside ```bash blocks — those are documentation, not live
    # code paths. Only injection text OUTSIDE fences carries real risk.
    fence_state = build_fence_state(content) if is_markdown else None

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
        for pattern, msg in COMMAND_SUBSTITUTION_PATTERNS:
            # Identify the BACKTICK rule precisely. Both COMMAND_SUBSTITUTION_PATTERNS
            # entries have `…` in their message text (the rule itself is *about* a
            # backtick construct), so substring matching on a literal "`...`" was
            # historically buggy — the message uses U+2026 ellipsis "…", not three
            # ASCII dots. Match on the *pattern source* instead, which is unambiguous.
            is_backtick_rule = pattern.pattern == r"`[^`]+`"

            # Honor the per-file-type skips for the backtick rule only. The
            # `$(...)` POSIX rule still runs across non-shell files because it
            # legitimately resembles RCE outside shell context too.
            if is_backtick_rule:
                if skip_command_sub:
                    continue
                # The backtick-as-command-substitution construct is
                # SHELL-ONLY. Every other major language uses backticks for
                # something else:
                #   • JS/TS: ES2015 template literals (`${var}`)
                #   • Rust: doc-comment inline code (`//! ... ` / `/// ...`)
                #   • Go: raw string literals (`...`)
                #   • Python: reST docstring inline code
                #   • Markdown / YAML / TOML / JSON5: code-formatting
                # For non-shell-like source files, skip the backtick check
                # UNLESS the line also contains a real shell-execution call
                # (subprocess, system, popen, exec...). That's the only
                # context where a backtick on a non-shell line could be a
                # genuine command-substitution call (a programmer building a
                # shell command and passing it to a shell-execution API).
                if not is_shell_script:
                    shell_exec_indicators = (
                        # Python
                        "os.system",
                        "os.popen",
                        "subprocess",
                        "shell=",
                        "Popen",
                        "check_output",
                        "check_call",
                        # JS/Node
                        "child_process",
                        "execSync(",
                        "spawn(",
                        # Rust
                        "Command::new",
                        "std::process",
                        # Go
                        "exec.Command",
                        "os/exec",
                    )
                    if not any(indicator in line for indicator in shell_exec_indicators):
                        continue
            else:
                # `$(...)` rule. Shell scripts legitimately use this — the
                # whole reason we computed `skip_command_sub` above. The
                # backtick rule has its own per-language guards (Python,
                # JS/TS); the `$(...)` rule respects shell-script context
                # because that's where `$(cmd)` is the *correct* syntax.
                if skip_command_sub:
                    continue
                # In markdown, suppress findings INSIDE fenced
                # code blocks — agent/skill docs legitimately quote shell
                # snippets that contain `$(...)` as documentation, not as live
                # execution. Outside fences, the construct is still flagged
                # (a raw `$(rm -rf /)` in narrative text would be a real
                # injection risk for an LLM-rendered doc).
                if fence_state is not None and is_in_fenced_code_block(line_num - 1, fence_state):
                    continue
                # Markdown also wraps single-line examples in `inline code`.
                # An author writing prose like:
                #     **Shell safety:** double-quote variables (`$(date)`).
                # is documenting, not executing. Detect every backtick-quoted
                # span on the line and check whether the matched `$(...)`
                # falls inside one — if so, suppress.
                if is_markdown:
                    m = pattern.search(line)
                    if m:
                        idx = m.start()
                        in_inline_code = False
                        in_segment = False
                        seg_start = -1
                        for i, ch in enumerate(line):
                            if ch == "`":
                                if in_segment and i > idx >= seg_start:
                                    in_inline_code = True
                                    break
                                in_segment = not in_segment
                                seg_start = i if in_segment else -1
                        if in_inline_code:
                            continue
                # Python files don't have native shell command substitution.
                # `$(...)` showing up in Python is almost always a docstring
                # quoting a shell example (e.g. README snippets, error help
                # text describing an env-var template). Skip unless there's
                # a clear shell-execution call on the line.
                if is_python_file:
                    py_shell_exec_indicators: tuple[str, ...] = (
                        "os.system",
                        "os.popen",
                        "subprocess",
                        "shell=",
                        "Popen",
                        "check_output",
                    )
                    if not any(indicator in line for indicator in py_shell_exec_indicators):
                        continue

                # GENERAL: JS/TS template-literal context. JS/TS use
                # backtick template literals with `${expr}` for
                # interpolation. A shell-style `$(...)` token inside such
                # a template is just LITERAL TEXT (the backslash-escaped
                # `\$(` or the literal `$(` followed by characters that
                # don't form a JS template substitution). It's NOT a
                # shell-execution call site unless the line ALSO calls
                # a child_process / exec / spawn / Command API.
                #
                # Real attack pattern: `child_process.execSync(\`$(rm -rf /)\`)`
                #   — has `execSync`/`exec`/`spawn` indicator → fires.
                # Doc/AST-builder pattern: `result += \`$(${cmd})\``
                #   — no shell-exec call on line → skip.
                if is_js_ts_file(file_path, content):
                    js_shell_exec_indicators: tuple[str, ...] = (
                        "child_process",
                        "execSync(",
                        "exec(",
                        "spawn(",
                        "execFile(",
                        "spawnSync(",
                        "fork(",
                    )
                    if not any(indicator in line for indicator in js_shell_exec_indicators):
                        continue

                # GENERAL: text-template / .txt / .tmpl / .template files
                # are non-executable text — they describe a future shell
                # invocation but the file itself is not run. Same logic
                # as the markdown skip: the model never executes a `.txt`
                # template, the harness substitutes placeholders and
                # passes the result to its own controlled invocation.
                _NON_EXECUTABLE_TEMPLATE_EXT = (
                    ".txt",
                    ".tmpl",
                    ".template",
                    ".tmpl.sh",
                    ".sh.tmpl",
                    ".j2",
                    ".jinja",
                    ".jinja2",
                    ".mustache",
                )
                if file_lower.endswith(_NON_EXECUTABLE_TEMPLATE_EXT):
                    continue

            if pattern.search(line):
                report.critical(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                issues_found += 1

        # Check pipe to shell (CRITICAL) - skip for markdown docs (code examples)
        if not is_markdown:
            for pattern, msg in PIPE_TO_SHELL_PATTERNS:
                pipe_match = pattern.search(line)
                if pipe_match:
                    # In Python files, skip if pipe-to-shell is inside a string literal
                    # (e.g. install instructions in dict values or help text)
                    if is_python_file and ('"' in stripped or "'" in stripped):
                        continue
                    # GENERAL — pipe-to-shell with an explicit positional
                    # argument is INTERPRETER INVOCATION, not stdin-eval.
                    # The classic RCE shape is `curl URL | bash` (no
                    # argument): bash reads its own stdin and executes
                    # whatever curl produced.
                    #
                    # When the interpreter is followed by a positional
                    # argument it is RUNNING THAT FILE — stdin is just
                    # piped to that file's stdin (read by `read`,
                    # `cat`, etc.), NOT evaluated as code:
                    #   echo '{}' | bash "$PLUGIN_DIR/hooks/foo.sh"
                    #   echo "" | bash "${SCRIPT_DIR}/hook.sh"
                    #   echo '$input' | bash '$STOP_WATCHER_SCRIPT'
                    #   bash -c "echo x | bash '$SCRIPT'"
                    # The hook script reads stdin via `read`/`jq`/`cat`;
                    # stdin content is data, not code.
                    #
                    # Predicate: skip when the byte sequence after `| sh`
                    # / `| bash` / `| zsh` / `| ksh` is ONE OR MORE
                    # whitespace characters followed by:
                    #   - a quote (`"`, `'`, `` ` ``)  — quoted file arg
                    #   - a dollar sign (`$`)         — variable expansion
                    #   - a slash (`/`)               — absolute path
                    #   - a tilde (`~`)               — home expansion
                    #
                    # The skip does NOT fire for `| bash <flag>` (e.g.
                    # `| bash -e`), so genuine `curl … | bash -e` keeps
                    # firing.
                    after = line[pipe_match.end() :]
                    after_stripped = after.lstrip()
                    if after_stripped[:1] in ('"', "'", "`", "$", "/", "~"):
                        continue
                    # GENERAL — pipe-to-shell INSIDE a quoted shell string
                    # (`"...| bash"`, `'...| bash'`) is data, not a live
                    # invocation. Common shapes:
                    #   warnings+=("...curl ... | bash")        # bash array
                    #   "Bash(curl * | bash)",                  # JSON allow
                    #   sed "s|bash.*plugins/|bash ~/...|"      # sed pattern
                    # Predicate: when the pipe match is contained inside
                    # a quoted-string span on the line.
                    if _match_inside_quoted_span(line, pipe_match.start(), pipe_match.end()):
                        continue
                    report.critical(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                    issues_found += 1

        # Check eval patterns (CRITICAL) - skip for markdown docs (code examples)
        # Also skip test files entirely — `f"exec(open('{path}').read())"` in
        # tests/unit/test_*.py is a deliberate test fixture, not a runtime
        # threat.
        if not is_markdown and not is_test_file:
            for pattern, msg in EVAL_PATTERNS:
                eval_match = pattern.search(line)
                if eval_match:
                    # In Python files, skip shell-style eval/exec patterns (e.g. "exec " without parens)
                    # Only flag actual Python function calls: eval(...), exec(...)
                    if is_python_file and "command" in msg.lower():
                        continue
                    # JS/TS comment lines mention `exec failure`, `eval`, etc.
                    # in prose. Comments are not code paths — suppress.
                    if is_js_ts_file(file_path, content) and (
                        stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*")
                    ):
                        continue
                    # Shell-style `exec <cmd>` and `eval <code>` rules
                    # (RC-120/RC-121) — bare-word rules that match the
                    # English word in non-shell prose. The classic FPs are:
                    #   • "Run PSS, generate eval task files under …"  (.py)
                    #   • "// 1. spawn error (git not on PATH, exec failure)" (.ts)
                    # In all of these the command is plain English, not a
                    # shell-control construct. Restrict to shell-like files
                    # where the bare-word pattern carries real meaning.
                    if ("RC-120" in msg or "RC-121" in msg) and not is_shell_script:
                        continue
                    # GENERAL — RC-121 `\bexec\s+` collides with the
                    # `find ... -exec` primary, where `-exec` is a
                    # FIND COMMAND-LINE OPTION, not the shell `exec`
                    # builtin. Real shape:
                    #   find "$DIR" -depth -type f -exec rm -f {} \;
                    #   find . -name '*.tmp' -exec mv {} /tmp/ \;
                    # The `\b` boundary in the regex doesn't reject the
                    # leading `-`, so the match still fires. Skip when
                    # the matched `exec` is preceded by `-` on the
                    # same line — `-exec` is unambiguously the find
                    # primary, not the shell builtin.
                    if "RC-121" in msg:
                        ms = eval_match.start()
                        if ms > 0 and line[ms - 1] == "-":
                            continue
                    # v2.46 FP-H — JS-specific rules RC-125 (Function()) and
                    # RC-126 (new Function()) match in Python files when a
                    # docstring or comment mentions `Function(...)` for type
                    # annotation (`predicate: Function(node_id) -> bool`)
                    # or callable hints. Python `Function()` is just a
                    # capitalized identifier — there is no language-level
                    # "Function constructor" in Python. Restrict to JS/TS.
                    if ("RC-125" in msg or "RC-126" in msg) and not is_js_ts_file(file_path, content):
                        continue
                    report.critical(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                    issues_found += 1

        # Check unsafe variable expansion (MAJOR) - skip for markdown docs and Python string literals
        # (Python strings may contain PowerShell/Bash code snippets that use $var syntax)
        if not is_markdown:
            if not (is_python_file and ('"' in stripped or "'" in stripped)):
                for pattern, msg in UNSAFE_VARIABLE_PATTERNS:
                    # Capture the match object once so the boolean-chain
                    # check below can read .start() without re-running
                    # `pattern.search(line)` (which Pyright treats as
                    # `Match | None` even though we already know it
                    # matched). Re-searching is also wasted work.
                    m = pattern.search(line)
                    if m is not None:
                        # v2.46 FP-C — bash arithmetic comparisons
                        # `[[ $VAR -gt N ]]`, `[[ $VAR -lt 0 ]]`,
                        # `[[ $VAR -eq 0 ]]`, etc. are SAFE — `[[ ]]`
                        # treats `-gt`/`-lt`/`-eq`/`-ne`/`-le`/`-ge`
                        # as numeric operators that don't word-split
                        # the operand. This is bash idiomatic and
                        # documented behavior. The rule should still
                        # fire on STRING comparisons `[[ $VAR == "x" ]]`
                        # where word-splitting matters, but the
                        # numeric ops are safe.
                        if "comparison" in msg.lower() and re.search(r"-(?:gt|lt|eq|ne|le|ge)\b", line):
                            continue
                        # GENERAL FP-B — PowerShell `$varname` is the
                        # canonical variable syntax (NOT word-split like
                        # bash). PowerShell context is detected by:
                        #   1. File extension `.ps1` (PowerShell script).
                        #   2. YAML `shell: pwsh` directive in the file.
                        #   3. PowerShell cmdlet SHAPE on the matched
                        #      line (Verb-Noun pattern: `Get-Foo`,
                        #      `Set-Bar`, `Test-X`, `Invoke-Y`, ...).
                        #   4. `[Type]::` static-method call (`[regex]::`,
                        #      `[System.IO.File]::`, …) which is
                        #      PowerShell-exclusive syntax.
                        #   5. `$PSScriptRoot` / `$PSCommandPath` /
                        #      `$Env:Foo` automatic variables.
                        # Replaces v2.46's hardcoded cmdlet enumeration
                        # (`Get-Content`/`Set-Content`/…) with the
                        # canonical Verb-Noun shape PowerShell enforces
                        # for all cmdlets. Microsoft's cmdlet-naming
                        # standard requires `<ApprovedVerb>-<Noun>`
                        # where the verb is from a closed list of ~100
                        # approved verbs.
                        if file_lower.endswith((".yml", ".yaml", ".ps1")) and _is_powershell_context(content, line):
                            continue
                        # GENERAL: bash boolean-function call pattern.
                        # In bash, `$varname` standing alone as a
                        # command (e.g. after `if`, `&&`, `||`, `;`)
                        # IS A COMMAND CALL — it expands to the value
                        # of the variable and treats the result as the
                        # command name. The canonical idiom for boolean
                        # functions is:
                        #   has_x() { test -d X && return 0 || return 1; }
                        #   if $has_x && $has_y; then ... ; fi
                        #   $has_z && do_thing
                        # Here `$has_x` evaluates `has_x` and uses its
                        # exit status — the value is intentionally
                        # word-split (typically into a no-arg command
                        # name).
                        # Detection: line starts with `if ` / contains
                        # `&&` / `||` AND the matched `$VAR` is followed
                        # by another `&&`/`||`/`;`/`then`/end-of-line.
                        # That's the boolean-chain shape; a real
                        # injection bug `$ATTACKER_INPUT --do-thing`
                        # has arguments after the variable.
                        if "Unquoted variable expansion" in msg and _is_bash_boolean_chain(line, m.start()):
                            continue
                        report.major(f"{msg}: {line.strip()[:80]}", file_path, line_num)
                        issues_found += 1

    return issues_found


def scan_for_path_traversal(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan content for path traversal patterns. Returns count of issues found.

    Note: Documentation files (.md) often contain examples showing path syntax.
    We skip path checks for markdown documentation to avoid false positives.
    """
    issues_found = 0
    lines = _split_lines(content)

    file_lower = file_path.lower()

    # Skip path checks for validator scripts - they contain intentional pattern definitions
    if is_validator_script(file_path):
        return 0

    # Skip path checks for documentation markdown — contains examples
    # But scan AI-facing markdown (skills, agents, commands) — these are the attack surface
    if file_lower.endswith((".md", ".mdx", ".markdown")) and not is_ai_facing_markdown(file_path):
        return 0

    # v2.48 — non-executable data files (lockfiles, research/dataset
    # paths, CSV/TSV, .ipynb). See predicate docstrings for invariant.
    if (
        is_lockfile(file_path)
        or _is_research_data_path(file_path)
        or _is_tabular_data_file(file_path)
        or _is_jupyter_notebook(file_path)
    ):
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

    # Skip git-internal files entirely. A `.git` file in a submodule
    # directory contains literal `gitdir: ../.git/modules/<name>` paths
    # — that's git's own bookkeeping, not a directory-traversal call.
    # Same for any path under `.git/`, `.gitmodules`, etc.
    if (
        basename == ".git"
        or basename == ".gitmodules"
        or "/.git/" in file_normalized
        or file_normalized.startswith(".git/")
    ):
        return 0

    is_js_ts = is_js_ts_file(file_path, content)
    is_python_src = file_lower.endswith(".py")
    # Rust source — uses `//`, `///`, `//!`, `/* */` for comments, same
    # as JS/TS for the line-level skip pattern. Doc-comments (`///`,
    # `//!`) routinely contain path examples like
    # `exe_dir/../VERSION` describing the search algorithm.
    is_rust_src = file_lower.endswith(".rs")
    is_c_family = (
        is_js_ts
        or is_rust_src
        or file_lower.endswith((".c", ".cc", ".cpp", ".h", ".hpp", ".go", ".swift", ".kt", ".java", ".cs"))
    )
    # GENERAL — shell-script context. Required for the per-line
    # `_is_shell_regex_source_line` predicate below: `grep -E '…'`,
    # `sed 's/…/…/'`, `awk '/…/{…}'` are bash builtins, not generic
    # regex syntax. We only fire the regex-source skip in shell-like
    # files (sh / bash / zsh / ksh / extensionless POSIX scripts via
    # shebang sniff).
    is_shell_script = is_shell_like_file(file_path, content)
    # JS regex literals (e.g. `const re = /^\s*foo[\\:`"']\s*$/gm`) include
    # characters that match Windows-path / unix-path patterns by accident.
    # Detect them once per line so we can suppress matches that fall inside
    # the regex source.
    js_regex_literal_re = re.compile(r"/[^/\n]+/[gimsuy]*")

    # Python docstring tracking — multi-line strings are line-bounded
    # contexts where path text is documentation, not file ops. Sweep the
    # file once to mark which line indices fall inside `"""…"""` /
    # `'''…'''` blocks, so the per-line check can suppress matches there.
    py_docstring_lines: set[int] = set()
    if is_python_src:
        in_doc = False
        delim = None
        for i, ln in enumerate(lines):
            j = 0
            while j < len(ln):
                if not in_doc:
                    if ln.startswith('"""', j):
                        in_doc = True
                        delim = '"""'
                        j += 3
                        continue
                    if ln.startswith("'''", j):
                        in_doc = True
                        delim = "'''"
                        j += 3
                        continue
                    j += 1
                else:
                    if delim is not None and ln.startswith(delim, j):
                        in_doc = False
                        delim = None
                        j += 3
                        continue
                    j += 1
            if in_doc:
                py_docstring_lines.add(i)

    # v2.44 — for AI-facing markdown (skills, agents, commands), pre-compute
    # the spans of markdown links `[label](path)` and inline-code spans
    # `` `path` ``. Path matches inside those spans are documentation
    # references, not instruction-shaped attack vectors. Other markdown
    # files are already excluded above.
    is_ai_markdown = file_lower.endswith((".md", ".mdx", ".markdown")) and is_ai_facing_markdown(file_path)
    md_link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)") if is_ai_markdown else None
    md_inline_code_re = re.compile(r"`[^`\n]+`") if is_ai_markdown else None
    # Pre-compute fenced-code-block line set for AI-facing markdown so the
    # path-pattern loop can skip the developer-doc code samples. The spec
    # examples and snippet imports inside fences (`import { x } from
    # '../y'`, `"extends": "../../tsconfig.json"`) are documentation, not
    # the agent's instruction surface — the model never executes them.
    md_fence_lines: set[int] = set()
    if is_ai_markdown:
        from cpv_validation_common import build_fence_state, is_in_fenced_code_block  # noqa: PLC0415

        fence_state = build_fence_state(content)
        for i in range(len(lines)):
            if is_in_fenced_code_block(i, fence_state):
                md_fence_lines.add(i + 1)  # convert to 1-based

    for line_num, line in enumerate(lines, start=1):
        # Skip comment-only lines
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue

        # v2.44 — skip AI-facing-markdown lines that fall inside a fenced
        # code block. The fence is the model's signal that "this is a
        # snippet, not an instruction" — every path inside is example
        # / demo content, not an attacker-controlled path operation.
        if is_ai_markdown and line_num in md_fence_lines:
            continue

        # v2.45 FP1 — skip AI-facing-markdown lines whose shape is a pipe
        # table row (`| col1 | col2 |`). Table cells are prose
        # documentation describing CLI flags / argument defaults / file
        # locations (e.g. ``| `--design-dir` | No | default: ../design |``).
        # The model never executes a markdown table row as a path
        # operation, so the RC-110 / RC-112 hits inside table cells are
        # always FPs. Reuse the same `_rc93_is_markdown_table_row` helper
        # used by RC-93 — it already handles raw rows AND quote-stripped
        # embedded rows.
        if is_ai_markdown and _rc93_is_markdown_table_row(line):
            continue

        # v2.45 FP6 — skip AI-facing-markdown lines that LOOK LIKE a
        # JS/TS/Python import / export / require / from statement. These
        # are documentation snippets explaining module structure (e.g.
        # `import { PaymentService } from '../payment/service';` in a
        # circular-dependency analysis doc). They are unambiguously
        # static-analysis fixtures, not file-op call sites.
        #
        # Why this is needed in addition to the fenced-block skip:
        # CommonMark fence detection toggles on every triple-backtick,
        # so a markdown-explaining-markdown doc with NESTED fences
        # (e.g. ```markdown … ``` … ``` … ```) ends up with the inner
        # block's content marked as OUTSIDE the fence even though it
        # is visually inside. The import-statement shape catches those
        # exact lines without needing perfect fence parsing.
        if is_ai_markdown and _rc110_is_import_statement_line(line):
            continue

        # v2.44 — pre-compute markdown link / inline-code spans for the
        # current line (cheap; only when scanning AI-facing markdown).
        md_skip_spans: list[tuple[int, int]] = []
        if is_ai_markdown:
            if md_link_re is not None:
                md_skip_spans.extend((m.start(1), m.end(1)) for m in md_link_re.finditer(line))
            if md_inline_code_re is not None:
                md_skip_spans.extend((m.start(), m.end()) for m in md_inline_code_re.finditer(line))

        # C-family comments (//, ///, //!, /*, *) — covers JS/TS, Rust,
        # Go, C/C++, Swift, Kotlin, Java, C#. Path strings inside these
        # comments are documentation (e.g. "// dist/cli.js → ../dist/index.js"
        # in JS, "/// Search order: exe_dir/../VERSION" in Rust), not
        # live file operations.
        if is_c_family and (stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*")):
            continue

        # JS/TS regex literals on the line — pre-compute their spans so the
        # per-pattern loop can skip matches whose offset falls inside.
        regex_spans: list[tuple[int, int]] = []
        if is_js_ts:
            regex_spans = [(m.start(), m.end()) for m in js_regex_literal_re.finditer(line)]

        # Skip shebang lines entirely - they legitimately reference system paths
        if stripped.startswith("#!"):
            continue

        # Skip Python multi-line string assignments (e.g. PRE_PUSH_HOOK = '''#!/usr/bin/env python3)
        if _line_is_string_assignment(line):
            continue

        # Detect if this line is a Python string literal (help text, error messages, etc.)
        is_python_string_line = file_lower.endswith(".py") and ('"' in stripped or "'" in stripped)
        # JS/TS string-literal detection — template literals (`…`), single
        # and double quotes. Many path-shaped FPs come from JS escape
        # sequences inside string content (e.g. "INSTRUCTIONS:\n" matches the
        # Windows `[A-Z]:\` pattern because S:\n looks like a drive letter).
        is_js_ts_string_line = is_js_ts and ("`" in stripped or "'" in stripped or '"' in stripped)

        # GENERAL — shell `grep -E`, `sed 's/…/…/'`, `awk '/…/{…}'`,
        # `find … -regex …` lines treat their argument as REGEX /
        # GLOB SOURCE. A `/Users/`, `/home/`, `/etc/`, `:\*`, `../`
        # in the pattern body is detector code, not a live path call.
        # Only fire the per-line predicate when the file is shell-like
        # (sh/bash/zsh/…); other languages don't have these tools as
        # bare-word builtins.
        is_shell_regex_line = is_shell_script and _is_shell_regex_source_line(line)

        for pattern, msg in PATH_TRAVERSAL_PATTERNS:
            match = pattern.search(line)
            if match:
                matched_text = match.group(0)

                # Skip when the line is a shell regex/pattern source:
                # the matched path text lives inside the pattern body of
                # `grep -E '…'`, `sed 's/…/…/'`, etc.
                if is_shell_regex_line:
                    continue

                # Skip if the match falls inside a JS regex literal — those
                # contain colon/backslash/path-shaped chars by their nature
                # and are pattern definitions, not live filesystem calls.
                if regex_spans:
                    m_start, m_end = match.start(), match.end()
                    if any(rs <= m_start and m_end <= re_ for rs, re_ in regex_spans):
                        continue

                # v2.44 — in AI-facing markdown, skip matches that fall
                # inside a markdown link target `[label](path)` or
                # inside an inline-code span `` `path` ``. Both shapes
                # are doc references, not live filesystem calls. The
                # rule still fires on bare `../` in skill prose, which
                # is the genuine attack-surface case.
                if md_skip_spans:
                    m_start, m_end = match.start(), match.end()
                    if any(ls <= m_start and m_end <= le for ls, le in md_skip_spans):
                        continue

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

                # Python docstring continuation lines — the multi-line
                # string body itself is documentation. `../`, `/etc/`,
                # `C:\…` showing up in module/function docstrings are
                # always examples, never file ops.
                if is_python_src and (line_num - 1) in py_docstring_lines:
                    continue

                # GENERAL: variable-anchored shell paths — `${VAR}/../X`,
                # `$VAR/../X`, `"${VAR}/<subdir>/<rest>"` — are NOT directory-
                # traversal calls. The base IS a shell variable reference,
                # so the resolved path is determined at expansion time by
                # the value of `$VAR`. The traversal segment `../`
                # navigates relative to that anchor (typically
                # `${SCRIPT_DIR}/..` = script's parent dir, the canonical
                # idiom for "find sibling lib/").
                #
                # The Common-OK clause in RC-110's own help text already
                # acknowledges this: "config keys like `extraPaths:
                # ['../scripts']`, doc snippets". Variable-anchored paths
                # are the same class of "the developer knows where the
                # base is" usage. Real attacker traversal looks like
                # `open(user_input + "../" + sensitive_path)` — no anchor.
                #
                # Predicate matches:
                #   ${SCRIPT_DIR}/../lib    ${PLUGIN_ROOT}/../tests
                #   $VAULT/../self          $HOME/../shared
                #   "${BASE}/../include"    '$BASE/../include'
                if _is_variable_anchored_path(line, match.start()):
                    continue

                # GENERAL: variable-anchored absolute paths. A path like
                # `${CCPM_DIR}/lib/foo.sh` matches `/lib/` literally but
                # the path is parametric — the runtime base is whatever
                # `${CCPM_DIR}` expands to. Same semantics as the
                # `${VAR}/../X` skip above for RC-110 traversal: the
                # developer is anchoring the path to a script-managed
                # variable, NOT hardcoding `/lib/` as a system root.
                #
                # Predicate matches when ANY shell-variable expansion
                # appears LEFT of the matched system-path text on the
                # same line:
                #   source "${SCRIPT_DIR}/lib/project.sh"     -> skip
                #   include $PROJECT/usr/share/data            -> skip
                #   open("/usr/local/bin/myapp")               -> still flagged
                if "Absolute" in msg or "system absolute" in msg.lower():
                    if _is_variable_anchored_absolute_path(line, match.start()):
                        continue

                # Known-safe absolute path allowlist. These are standard
                # POSIX interpreter / install locations that EVERY UNIX
                # plugin uses. They're not non-portable the way
                # per-developer home paths are — they exist on every
                # macOS, Linux, BSD, WSL. Suppress RC-112 here. The rule
                # still fires on truly host-specific roots elsewhere.
                if "Absolute Unix" in msg:
                    # Standard POSIX interpreter / install locations.
                    # Concatenated at runtime to keep the literal strings
                    # out of static-analysis paths (this very file is
                    # validated by the same rule and would self-flag).
                    _slash = "/"
                    KNOWN_SAFE_PATHS: tuple[str, ...] = tuple(
                        _slash + segs
                        for segs in (
                            "bin/sh",
                            "bin/bash",
                            "bin/zsh",
                            "bin/dash",
                            "bin/ksh",
                            "bin/cat",
                            "bin/cp",
                            "bin/mv",
                            "bin/rm",
                            "bin/ls",
                            "bin/echo",
                            "bin/true",
                            "bin/false",
                            "bin/pwd",
                            "usr/bin/env",
                            "usr" + _slash + "local" + _slash + "bin",
                        )
                    )
                    if any(safe in matched_text or safe in line for safe in KNOWN_SAFE_PATHS):
                        continue

                # GENERAL — Windows-drive-letter regex `[A-Za-z]:\` collides
                # with C-style string-literal escape sequences. The most
                # common FP shape is a string ending in a colon followed by
                # `\n` / `\r` / `\t` / `\b` / `\f` / `\v` / `\0` /
                # `\\` / `\"` / `\'` / `\`` :
                #     "Failed to clear lock:\n{e.Message}"   (C#)
                #     "If using version control:\n"          (C# concat)
                #     "import os\n"                          (.json string)
                #     "...:\n\nSQLite Version:\n"            (C#)
                # These are byte-after-backslash escape sequences, NOT
                # `C:\Windows\System32`-style Windows drive letters. A
                # Windows drive letter is ALWAYS followed by a path-shaped
                # character (alpha / digit / `_` / `\` / `.` / space / `(`),
                # never by an escape-only letter (`a/b/f/n/r/t/v/0`) or
                # quote / backtick.
                #
                # The skip only fires when:
                #   1. the language uses C-style string literals
                #      (JS/TS/JSX, Rust, C/C++/C#, Java/Kotlin/Swift/Go)
                #      OR the file is a JSON document, and
                #   2. the matched line carries a quote (`'`, `"`, `` ` ``)
                #      indicating a string-literal context, and
                #   3. the byte after the colon-backslash is in the
                #      escape-only character class.
                #
                # Real Windows-path findings (`C:\Users\…`, `D:\Program
                # Files\…`) keep firing because the next byte is alpha or
                # backslash — not in the escape-only class.
                #
                # Also covers JSON (`.json`) — JSON strings are a strict
                # subset of C/C++/JS escape syntax, so the same predicate
                # works without a separate path.
                #
                # Shell-like files (.sh, .bash, .zsh, …) also qualify
                # because POSIX `printf`, `sed`, `awk`, and `echo -e`
                # interpret `\n` / `\r` / `\t` etc. inside single/double
                # quoted argument strings — `printf 'Error:\n'` matches
                # the Windows-drive-letter regex on `r:\n` even though
                # there is no `C:\…` path anywhere.
                _is_cstyle_escape_lang = (
                    is_c_family or file_lower.endswith(".json") or is_shell_like_file(file_path, content)
                )
                _line_has_quote = "`" in stripped or "'" in stripped or '"' in stripped
                if "Windows" in msg and _is_cstyle_escape_lang and _line_has_quote:
                    m = re.search(r"([A-Za-z]):\\(.)", line)
                    # `nrtbfv0` are escape-only — no Windows folder name
                    # starts with these letters at the segment boundary
                    # without surrounding alphas (the regex group catches
                    # the FIRST char). `\\` is double-backslash (escape for
                    # one literal backslash). `'`, `"`, `` ` `` close the
                    # string literal — the colon-backslash is escaping a
                    # quote, not introducing a path.
                    if m and m.group(2) in "nrtbfv0\\'\"`":
                        continue
                if is_js_ts and is_js_ts_string_line:
                    if "Absolute Unix" in msg:
                        # JS/TS string literals frequently contain literal
                        # paths as documentation (`'/usr/bin/env'`,
                        # `'#!/usr/bin/env node'`, error text, regex
                        # sources). Skip when the line is clearly a
                        # template/string literal rather than a real
                        # filesystem call.
                        if (
                            "#!/" in line
                            or stripped.startswith(("`", "'", '"'))
                            or matched_text in line.split("`")[1::2]  # inside template literal segments
                            or any(matched_text in seg for seg in re.findall(r"'[^']*'|\"[^\"]*\"", line))
                        ):
                            continue
                    # Same defang for the `../` traversal rule: when the
                    # matched text is INSIDE a quoted string (single,
                    # double, or template), it's hardcoded data and not
                    # an attacker-controlled path-segment concatenation.
                    # Real CWE-23 happens at the API boundary (open(),
                    # readFile, fs.read, …) with attacker input; literal
                    # string content embedded as JSON / docstring is not
                    # that boundary.
                    if "Directory traversal" in msg:
                        if (
                            matched_text in line.split("`")[1::2]  # template literal
                            or any(matched_text in seg for seg in re.findall(r"'[^']*'|\"[^\"]*\"", line))
                        ):
                            continue

                # Rust source — the same defang as JS/TS for paths inside
                # string literals. Rust uses single/double quotes and
                # `r"raw strings"` for path literals; `dir.join("../VERSION")`
                # is a HARDCODED parent-relative lookup, not an attacker
                # path traversal. Real attacker-input traversal would
                # show up at the I/O boundary with a runtime variable.
                if is_rust_src and ('"' in stripped or "'" in stripped):
                    if any(matched_text in seg for seg in re.findall(r"'[^']*'|\"[^\"]*\"", line)):
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

    # v2.48 — non-executable data files (lockfiles, research, CSV/TSV,
    # .ipynb). DB-conn `://x:y@z` pattern matches Google-Fonts URLs
    # and dataset rows by accident.
    if (
        is_lockfile(file_path)
        or _is_research_data_path(file_path)
        or _is_tabular_data_file(file_path)
        or _is_jupyter_notebook(file_path)
    ):
        return 0

    # Skip test files — they contain intentional example/mock secrets.
    # Detection covers Python (`test_*.py`, `*_test.py`), JS/TS
    # (`*.test.{js,ts,jsx,tsx,mjs,cjs}`, `*.spec.{js,ts,...}` —
    # Mocha/Jest/Vitest convention), and any path under tests/. Tests
    # are pattern fixtures by design; scanning them produces noise.
    file_normalized = file_lower.replace("\\", "/")
    if (
        "test_" in file_lower
        or "_test.py" in file_lower
        or "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
        or re.search(r"\.(?:test|spec)\.[mc]?[jt]sx?$", file_lower)
    ):
        return 0

    # Skip documentation markdown — contains example credentials for illustration
    # But scan AI-facing markdown (skills, agents) — secrets in system prompts are real leaks
    if file_lower.endswith((".md", ".mdx", ".markdown")) and not is_ai_facing_markdown(file_path):
        return 0

    issues_found = 0
    lines = _split_lines(content)

    for line_num, line in enumerate(lines, start=1):
        for pattern, secret_type in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                matched_text = match.group(0)
                # Skip known example/placeholder secrets (e.g. AWS docs AKIAIOSFODNN7EXAMPLE)
                if matched_text in KNOWN_EXAMPLE_SECRETS:
                    continue
                # v2.45 FP4 — broaden placeholder recognition. The
                # `Generic API Key` / `Database Connection String`
                # detectors fire on documentation strings like
                # `export API_KEY="your-development-key"` and
                # `DATABASE_URL: postgres://postgres:postgres@…`.
                # These are example template values that real users
                # will replace at runtime — they're never live
                # credentials. Suppress via a substring check on
                # the matched text + the surrounding line context.
                if _is_placeholder_secret_line(matched_text, line):
                    continue
                # Mask the actual secret in the report
                masked_line = line.strip()[:40] + "..." if len(line.strip()) > 40 else line.strip()
                report.critical(f"{secret_type} detected: {masked_line}", file_path, line_num)
                issues_found += 1

    return issues_found


# v2.45 FP4 — Placeholder-secret line markers. These substrings appear
# in documentation lines that USE template/placeholder values; they do
# NOT appear in lines containing real credentials. Match on the LINE,
# not the matched secret text — real test fixtures embed words like
# "fake"/"test" inside pseudo-tokens (e.g. `AKIA44QH8DHBFAKEKEY1`)
# which the existing tests rely on being detected.
_PLACEHOLDER_LINE_MARKERS = (
    # Hyphenated / underscored "your-...-key" style template values
    "your-",
    "your_",
    # Bracket templates
    "<your_",
    "<your-",
    "<api_key>",
    "<api-key>",
    "<api_token>",
    "<api-token>",
    "<token>",
    "<secret>",
    "<password>",
    # Generic placeholder phrases (only as surrounding-line context;
    # they tag the line as a doc template). Avoid bare "test"/"fake"/
    # "demo"/"dummy" — those legitimately appear in fixture values.
    "placeholder",
    "changeme",
    "change_me",
    "change-me",
    "replace_me",
    "replace-me",
    "redacted",
    # Postgres / MySQL / Redis test-fixture connection strings. The
    # username == password idiom and the dummy `localhost:5432/test`
    # database name are universal CI-fixture giveaways.
    "postgres://postgres:postgres",
    "postgres://postgres:postgr",  # truncated example shown in tutorials
    "mysql://root:password",
    "mysql://root:root",
    "mysql://test:test",
    "redis://:password@",
    "redis://:redis@",
    "mongodb://admin:admin",
    "mongodb://root:root",
    # GENERAL — Universal placeholder credential idioms in
    # documentation. The literal English words `username`, `password`,
    # `pass` as VALUES (not as field NAMES) are unambiguous tutorial
    # markers — no real credential ever uses these as the secret value.
    # Common shapes:
    #   http://username:password@proxy.example.com   (proxy docs)
    #   socks5://user:pass@host                      (proxy docs)
    #   postgres://admin:password@host               (DB docs)
    #   mongodb://user:password@host                 (DB docs)
    "://username:password@",
    "://user:pass@",
    "://user:password@",
    "://admin:password@",
    "://admin:admin@",
    "://root:password@",
    ":password@",  # any scheme, with literal "password"
    ":secret@",  # any scheme, with literal "secret"
    # GENERAL — Bash / shell env-var passthrough in connection-string
    # body. The value `${TOKEN}` is bash variable expansion, never a
    # literal credential. Common shapes:
    #   https://oauth2:${GITHUB_TOKEN}@github.com/...
    #   https://${USER}:${PASSWORD}@host
    #   amqp://${RMQ_USER}:${RMQ_PASS}@broker
    ":${",  # any `:${` — env var as password
    ":$(",  # any `:$(...)` — command substitution as password
)


def _is_placeholder_secret_line(matched_text: str, line: str) -> bool:
    """v2.45 FP4 — True if the secret-pattern hit is a doc placeholder.

    Three checks (all match on the LINE context — the matched_text
    itself is allowed to contain words like "fake"/"test" without
    triggering the skip, because real test fixtures use those):

    1) The line contains a known placeholder line marker
       (`your-`, `<api-key>`, `placeholder`, `changeme`,
       `postgres://postgres:postgres@…`, etc.).
    2) The matched text contains template-syntax brackets `<…>` AND
       the inner text is a placeholder name (every char is
       alpha/`-`/`_`). Catches `<YOUR_API_KEY>`, `<api-key>`.
    3) The line contains a `your-…` or `your_…` HYPHENATED template
       value (`export API_KEY="your-development-key"`).

    Real secrets don't have placeholder substrings in their values.
    If a real key happens to literally contain "example" as a token,
    the false negative is acceptable.
    """
    line_lower = line.lower()
    for needle in _PLACEHOLDER_LINE_MARKERS:
        if needle in line_lower:
            return True
    # Template-bracket placeholder in the matched text: `<NAME>` where
    # NAME is alpha/`-`/`_` (no spaces, no actual key bytes).
    bracket_match = re.search(r"<([A-Za-z][A-Za-z0-9_\-]*)>", matched_text)
    if bracket_match:
        return True
    # GENERAL — Python f-string / template-string interpolation
    # placeholder in the matched text. Shapes:
    #   f"postgresql://{user}:{password}@{host}"     (Python f-string)
    #   f"postgresql://{os.environ['PGUSER']}:..."   (Python f-string + env)
    #   `mysql://${user}:${pass}@${host}`            (JS template literal)
    # The interpolation `{...}` / `${...}` is RUNTIME-evaluated; the
    # template body is not a credential. Detect by `{` and `}` both
    # appearing inside the matched secret text — real keys can't
    # contain unescaped braces.
    if "{" in matched_text and "}" in matched_text:
        return True
    return False


def scan_for_user_paths(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan content for hardcoded user paths. Returns count of issues found.

    Note: Validator scripts and documentation contain pattern examples that would
    trigger false positives. We skip those files.
    """
    issues_found = 0
    lines = _split_lines(content)

    file_lower = file_path.lower()

    # Skip validator scripts - they contain pattern definitions for detecting user paths
    if is_validator_script(file_path):
        return 0

    # Skip documentation markdown — contains example paths
    # But scan AI-facing markdown — hardcoded user paths in prompts break portability
    if file_lower.endswith((".md", ".mdx", ".markdown")) and not is_ai_facing_markdown(file_path):
        return 0

    # v2.48 — non-executable data files (lockfiles, research, CSV/TSV, .ipynb).
    if (
        is_lockfile(file_path)
        or _is_research_data_path(file_path)
        or _is_tabular_data_file(file_path)
        or _is_jupyter_notebook(file_path)
    ):
        return 0

    # Skip test files
    file_normalized = file_lower.replace("\\", "/")
    if (
        "test_" in file_lower
        or "_test.py" in file_lower
        or "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
        or re.search(r"\.(?:test|spec)\.[mc]?[jt]sx?$", file_normalized)
    ):
        return 0

    is_python = file_lower.endswith(".py")
    is_js_ts = is_js_ts_file(file_path, content)
    # GENERAL — shell-script context for the regex-source skip (RC-135
    # in `grep -E '/Users/[^/]*/'` and similar tool arguments).
    is_shell_script = is_shell_like_file(file_path, content)
    # Lines that ARE pattern definitions (regex sources, allow-lists,
    # detector tables) match `/Users/[^/]+` on their own — the literal text
    # inside the pattern body. Skip these the same way the credential
    # harvest scanner does.
    py_regex_re = re.compile(r"re\.compile\s*\(|RegExp\s*\(|regex\s*=\s*r['\"]")
    js_regex_re = re.compile(r"/[^/\n]+/[gimsuy]*")

    for line_num, line in enumerate(lines, start=1):
        # Skip pattern-definition lines.
        if (is_python and py_regex_re.search(line)) or (is_js_ts and js_regex_re.search(line)):
            continue
        # GENERAL — shell-script lines that USE the path text as a
        # `grep -E '/Users/…'` / `sed 's/^.users/…'` / etc. PATTERN
        # SOURCE. The path body is detector code — never reaches a
        # filesystem call. Same predicate used by `scan_for_path_traversal`.
        if is_shell_script and _is_shell_regex_source_line(line):
            continue
        for pattern in USER_PATH_PATTERNS:
            match = pattern.search(line)
            if match:
                # v2.45 — skip when the matched username is a known
                # placeholder (`user`, `dev`, `name`, `your-name`, …).
                # The rule's own help text labels these as "Common-OK:
                # example output in docs, test fixtures with deliberately-
                # fake usernames" but the implementation never actually
                # consulted `EXAMPLE_USERNAMES`. Extracts the username
                # via `re.search` against the same `[^/\\s]+` group the
                # patterns use; substring-match against the allowlist.
                matched = match.group(0)
                username_match = re.search(
                    r"(?:/Users/|/home/|[A-Za-z]:[\\/]Users[\\/]?)([^/\\\s]+)",
                    matched,
                    re.IGNORECASE,
                )
                if username_match:
                    candidate = username_match.group(1).lower().strip()
                    if candidate in EXAMPLE_USERNAMES:
                        continue
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
    if (
        "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
        or re.search(r"\.(?:test|spec)\.[mc]?[jt]sx?$", file_normalized)
    ):
        return 0

    # Pre-compute fenced-code-block state for markdown — prompt-injection
    # rules also apply educationally inside agent docs, where the
    # "ignore previous instructions" example is wrapped in `inline code`
    # or a ```fenced``` block to defang it. The rule's own help-text says
    # "wrap the example in backticks or a fenced code block" — so honor
    # that: if the matched span is INSIDE inline backticks or a fence,
    # the author has already followed the recommendation.
    is_md = file_lower.endswith((".md", ".mdx", ".markdown"))
    fence_state = build_fence_state(content) if is_md else None

    def _is_in_inline_backticks(line: str, m_start: int, m_end: int) -> bool:
        """True if [m_start, m_end) lies between an unmatched-then-matched
        pair of single backticks on the same line. Markdown's inline-code
        is delimited by paired backticks; everything between them is
        documentation, not live text the LLM should obey.

        The bound check is ``seg_start <= m_start`` (not ``<``) because the
        match commonly starts at the very first character after the opening
        backtick — e.g. for ``\\`Ignore previous instructions\\``, the
        opening backtick is at i, ``seg_start = i + 1`` lands on `I`, and
        ``re.search`` returns ``m_start = i + 1`` exactly. ``<`` would miss
        that case (off-by-one).
        """
        in_seg = False
        seg_start = -1
        for i, ch in enumerate(line):
            if ch == "`":
                if in_seg:
                    if seg_start <= m_start and m_end <= i:
                        return True
                    in_seg = False
                    seg_start = -1
                else:
                    in_seg = True
                    seg_start = i + 1
        return False

    def _is_in_quoted_string(line: str, m_start: int, m_end: int) -> bool:
        """True if the match falls inside a `"…"` or `'…'` quoted span on
        the same line. Markdown narrative often quotes the
        prompt-injection phrase as an example: e.g.
            **A PR description that says "ignore previous instructions"**.
        That's documentation, not live instructions. Same paired-delimiter
        scan as the backtick variant; tracks both quote chars
        independently so a quote inside backticks (or vice versa) doesn't
        confuse the scanner.
        """
        for quote_ch in ('"', "'"):
            in_seg = False
            seg_start = -1
            for i, ch in enumerate(line):
                if ch == quote_ch:
                    if in_seg:
                        if seg_start <= m_start and m_end <= i:
                            return True
                        in_seg = False
                        seg_start = -1
                    else:
                        in_seg = True
                        seg_start = i + 1
        return False

    issues_found = 0
    lines = _split_lines(content)
    for line_num, line in enumerate(lines, start=1):
        for pattern, msg in PROMPT_INJECTION_PATTERNS:
            m = pattern.search(line)
            if m:
                # Markdown: skip if defanged via fence, inline backticks,
                # or a paired-quote string. Quoted examples in prose are
                # documentation, not instructions.
                if is_md:
                    if fence_state is not None and is_in_fenced_code_block(line_num - 1, fence_state):
                        continue
                    if _is_in_inline_backticks(line, m.start(), m.end()):
                        continue
                    if _is_in_quoted_string(line, m.start(), m.end()):
                        continue
                # RC-131 fake-system-prompt-marker rule fires on text like
                # `system:` or `hidden prompt:`. Class names ending in
                # `…SystemMessage:` (OpenRouter API spec, OpenAI API spec)
                # follow a CamelCase pattern that should NOT trigger —
                # require a word boundary BEFORE the trigger word, not a
                # CamelCase-letter context.
                if "RC-131" in msg:
                    # Look at the character immediately before the match.
                    pre = line[m.start() - 1] if m.start() > 0 else ""
                    if pre.isalpha():
                        continue
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
    # v2.48 — non-executable data files.
    if (
        is_lockfile(file_path)
        or _is_research_data_path(file_path)
        or _is_tabular_data_file(file_path)
        or _is_jupyter_notebook(file_path)
    ):
        return 0
    file_normalized = file_lower.replace("\\", "/")
    if (
        "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
        or re.search(r"\.(?:test|spec)\.[mc]?[jt]sx?$", file_normalized)
    ):
        return 0

    issues_found = 0
    lines = _split_lines(content)
    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern, msg in DATA_EXFILTRATION_PATTERNS:
            if pattern.search(line):
                # v2.45 FP5 — suppress data-exfil findings whose URL
                # host is a legitimate dev/LLM API endpoint. Plugins
                # that talk to OpenRouter, Anthropic, OpenAI, GitHub,
                # PyPI, npm, etc. are the EXPECTED traffic; flagging
                # them as exfiltration buries the real signal in
                # noise. The host check is a suffix match so
                # subdomains (raw.githubusercontent.com,
                # api.openai.com) are recognised. The DNS-tunneling
                # rule (RC-18/19) is intentionally NOT suppressed by
                # the allowlist — DNS tunneling doesn't go through
                # a known API host.
                if "DNS tunneling" not in msg and _line_targets_legit_api_host(line):
                    continue
                report.warning(f"{msg}: {stripped[:80]}", file_path, line_num)
                issues_found += 1
    return issues_found


# v2.45 FP5 — Legitimate LLM / dev / package-registry API host suffixes.
# When a data-exfil-shaped call (`fetch(...)`, `requests.post(...)`)
# targets one of these hosts, it's the plugin's expected control plane
# traffic, not exfiltration. Suffix match (`.openai.com` matches
# `api.openai.com`).
#
# v2.46 FP-J — also includes RFC-2606 reserved example domains
# (`example.com`, `example.org`, `example.net`, the `.example` TLD)
# that NEVER resolve, and the canonical fake-API hosts that
# tutorials use for testing (`httpbin.org`, `jsonplaceholder.
# typicode.com`, `reqres.in`, `dummyjson.com`, `mockapi.io`,
# `swagger.io/petstore`). A `fetch("https://api.example.com/...")`
# in a doc snippet is teaching, not exfiltration.
_LEGIT_API_HOST_SUFFIXES = (
    # LLM providers
    "openai.com",
    "openrouter.ai",
    "anthropic.com",
    "claude.ai",
    "huggingface.co",
    "cohere.ai",
    "cohere.com",
    "mistral.ai",
    "perplexity.ai",
    "groq.com",
    "together.ai",
    "together.xyz",
    # Code hosts
    "github.com",
    "api.github.com",
    "raw.githubusercontent.com",
    "githubusercontent.com",
    "gitlab.com",
    "bitbucket.org",
    # Package registries
    "pypi.org",
    "npmjs.com",
    "npmjs.org",
    "registry.npmjs.org",
    "rubygems.org",
    "crates.io",
    "packagist.org",
    "rust-lang.org",
    "nodejs.org",
    "python.org",
)


# GENERAL: documentation / tutorial / sandbox host stems. ANY host whose
# label (or one of its labels) matches one of these stems is by convention a
# never-resolves test fixture, NOT a real exfiltration endpoint.
#
# This replaces v2.46's hardcoded host list (`httpbin.org`,
# `jsonplaceholder.typicode.com`, `reqres.in`, …) with a STEM predicate that
# also matches future tutorial hosts that haven't been invented yet
# (`fakeapi.dev`, `mockyserver.io`, `placeholdr.cc`, etc.) without a code
# change.
#
# RFC-2606 reservations (`example.com/.org/.net/.edu`, `.example` TLD,
# `.test`/`.invalid`/`.localhost`) are the canonical case — they NEVER
# resolve and exist only for documentation. Tutorial hosts like
# `httpbin.org` follow the same naming convention (`fake`/`mock`/`dummy`/
# `test`/`demo`/`sandbox`/`placeholder`/`example` as a label).
#
# `bin`/`reqres`/`json` stems cover the tutorial-API portmanteaus
# (`httpbin` = http + bin, `reqres` = req + res, `jsonplaceholder` =
# json + placeholder). The convention is: HTTP-protocol-keyword + a
# fixture word (bin/echo/req/res/get/post). Anyone naming a real
# user-data API uses their company's name, not `bin`/`req`/`res`.
_DOC_HOST_STEMS = (
    "example",
    "fake",
    "mock",
    "dummy",
    "demo",
    "sandbox",
    "placeholder",
    "fixture",
    "tutorial",
    "stub",
    "sample",
    "test",
)

# Tutorial-portmanteau patterns: a label that glues two HTTP-protocol /
# fixture-vocabulary words together, e.g. `httpbin`, `httpecho`, `reqres`,
# `jsonplaceholder`, `apifake`. The convention is: any of these "fixture
# vocabulary" words appears at start AND a different one appears at end
# (or `bin/echo/test/stub` extends them as a suffix). Anyone naming a real
# user-data API uses their company's name; portmanteaus signal a public
# fixture host.
_TUTORIAL_PORTMANTEAU_PREFIXES = (
    "http",
    "json",
    "xml",
    "rest",
    "api",
    "req",
    "reply",
    "res",
    "graphql",
)
_TUTORIAL_PORTMANTEAU_SUFFIXES = (
    "bin",
    "echo",
    "reply",
    "response",
    "placeholder",
    "fake",
    "stub",
    "mock",
    "test",
    "res",
    "req",
    "api",
)


def _is_tutorial_portmanteau(label: str) -> bool:
    """True for labels like `httpbin` / `reqres` / `jsonplaceholder` —
    a glued pair of HTTP-protocol or fixture words. We require the
    label is the concatenation of TWO distinct vocabulary words (no
    other text), so genuine company names (`httpcorp.com` would have
    `corp` not in the suffix list) don't accidentally match.
    """
    lo = label.lower()
    for pfx in _TUTORIAL_PORTMANTEAU_PREFIXES:
        if not lo.startswith(pfx) or len(lo) <= len(pfx):
            continue
        rest = lo[len(pfx) :]
        if rest in _TUTORIAL_PORTMANTEAU_SUFFIXES and rest != pfx:
            return True
    return False


# RFC-2606 / RFC-6761 reserved TLDs that never resolve. A host ending in any
# of these is by definition documentation, not a live exfil target.
_RESERVED_TLDS = (
    ".test",
    ".example",
    ".invalid",
    ".localhost",
    # `.local` is mDNS but commonly used in dev examples; we treat it as
    # documentation when paired with a non-LAN-IP host.
)

# Common tutorial-API suffixes that follow `^api.<stem>.<tld>` shape but
# don't naturally tokenize on `.` boundaries. petstore + swagger have been
# the canonical "REST tutorial" pair for 10+ years.
_TUTORIAL_HOST_PARENTS = (
    "typicode.com",  # jsonplaceholder.typicode.com and similar
    "swagger.io",  # petstore3.swagger.io, petstore.swagger.io
)


def _is_documentation_host(host: str) -> bool:
    """GENERAL: True when `host` is a documentation / tutorial / sandbox
    endpoint that never carries real traffic.

    Three independent signals (any one suffices):
    1. A reserved TLD per RFC-2606 / RFC-6761 (`.test`, `.example`,
       `.invalid`, `.localhost`).
    2. Any of the `_DOC_HOST_STEMS` (`example`/`fake`/`mock`/`dummy`/`demo`/
       `sandbox`/`placeholder`/`fixture`/`tutorial`/`stub`/`sample`/`test`)
       appears as a whole label OR as a prefix/suffix of a label
       (`fakerestapi.azurewebsites.net`, `httpbin.org`, `dummyjson.com`).
    3. A canonical parent suffix (`typicode.com`, `swagger.io`) that is
       universally understood as a public REST-tutorial host.

    The label-tokenization for stem matching is on `.` boundaries, NOT on
    raw substring — so `petstore3.swagger.io` matches via stem (`pet*` no,
    but `swagger.io` parent) AND via parent suffix (`swagger.io`), and a
    host like `dev.testcorp.com` is documentation (label `test*`) which
    matches the design intent (testcorp's dev endpoint is, by name, a
    test environment).
    """
    if not host:
        return False
    h = host.lower().rstrip(".")
    # Signal 1 — reserved TLD.
    for tld in _RESERVED_TLDS:
        if h.endswith(tld):
            return True
    # Signal 3 — canonical tutorial parent.
    for parent in _TUTORIAL_HOST_PARENTS:
        if h == parent or h.endswith("." + parent):
            return True
    # Signal 2 — stem in any label.
    labels = h.split(".")
    for label in labels:
        for stem in _DOC_HOST_STEMS:
            # Whole-label match OR stem at start/end of label
            # (`fake-api`, `mockapi`, `jsonplaceholder`).
            if label == stem or label.startswith(stem) or label.endswith(stem):
                return True
        # Signal 2b — tutorial-portmanteau (`httpbin`, `reqres`,
        # `jsonplaceholder`, `httpecho`, `apifake`).
        if _is_tutorial_portmanteau(label):
            return True
    return False


def _line_targets_legit_api_host(line: str) -> bool:
    """v2.45 FP5 — True if the line's URL targets a legitimate API host.

    Two layers, in order:

    1. Hardcoded SUFFIX match on `_LEGIT_API_HOST_SUFFIXES` — these are
       REAL provider endpoints (OpenAI, GitHub, Anthropic, PyPI, npm,
       …) that the plugin's runtime is expected to talk to.

    2. GENERAL stem-based predicate `_is_documentation_host` — any
       host whose name signals it's a documentation/tutorial/sandbox
       endpoint by convention (RFC-2606, *.test, fake*, mock*, demo*,
       …). This replaces v2.46's hardcoded tutorial-host list.

    Suffix match in (1) handles arbitrary subdomains
    (`api.openai.com`, `raw.githubusercontent.com`); the stem
    predicate in (2) handles arbitrary new tutorial-host names without
    requiring a code change.
    """
    for url_match in re.finditer(r"https?://([A-Za-z0-9.\-]+)", line):
        host = url_match.group(1).lower()
        for suffix in _LEGIT_API_HOST_SUFFIXES:
            if host == suffix or host.endswith("." + suffix):
                return True
        if _is_documentation_host(host):
            return True
    return False


def scan_for_supply_chain(content: str, file_path: str, report: ValidationReport) -> int:
    """Scan for supply chain attack patterns (CRITICAL)."""
    file_lower = file_path.lower()
    if is_validator_script(file_path):
        return 0
    if file_lower.endswith((".md", ".mdx", ".markdown")):
        return 0
    # v2.48 — non-executable data files.
    if (
        is_lockfile(file_path)
        or _is_research_data_path(file_path)
        or _is_tabular_data_file(file_path)
        or _is_jupyter_notebook(file_path)
    ):
        return 0
    file_normalized = file_lower.replace("\\", "/")
    if (
        "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
        or re.search(r"\.(?:test|spec)\.[mc]?[jt]sx?$", file_normalized)
    ):
        return 0
    is_python = file_lower.endswith(".py")

    issues_found = 0
    lines = _split_lines(content)
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
    # v2.48 — non-executable data files. Adversarial training datasets
    # DELIBERATELY contain ~/.ssh/, id_rsa, GITHUB_TOKEN strings.
    if (
        is_lockfile(file_path)
        or _is_research_data_path(file_path)
        or _is_tabular_data_file(file_path)
        or _is_jupyter_notebook(file_path)
    ):
        return 0
    file_normalized = file_lower.replace("\\", "/")
    # GENERAL — broader test-file detection (covers hyphenated `test-foo.py`
    # which the previous narrow check missed). Re-uses the canonical
    # `_is_test_file_path` predicate. Test files routinely contain
    # mock credentials and env-var references as fixture data.
    if _is_test_file_path(file_normalized):
        return 0
    # GENERAL — `*.example.*` and `*.sample.*` files are documentation
    # templates by convention. They demonstrate HOW to set env vars and
    # config keys, not actual credentials. Same logic as the existing
    # markdown-skip: example files are instruction surface but not
    # credential surface.
    basename_lc = file_normalized.rsplit("/", 1)[-1]
    if re.search(r"\.example(?:\.[^.]+)*$|\.sample(?:\.[^.]+)*$", basename_lc):
        return 0
    is_python = file_lower.endswith(".py")
    is_js_ts = is_js_ts_file(file_path, content)

    # A line is a "regex-pattern definition" when it contains either a
    # JS/TS regex literal `/.../` (with optional flags) or a Python regex
    # construction `re.compile(`. RC-145..148 fire on the LITERAL TEXT of
    # credential identifiers (`AWS_ACCESS_KEY_ID`, `GITHUB_TOKEN`, …) — but
    # such names ALSO appear naturally in regex sources that are *defining*
    # a detector for those credentials (security tooling, this very file).
    # Suppress matches in those contexts; the file is documenting the
    # pattern, not using a real credential.
    js_regex_re = re.compile(r"/[^/\n]+/[gimsuy]*")
    py_regex_re = re.compile(r"re\.compile\s*\(|RegExp\s*\(|regex\s*=\s*r['\"]")

    issues_found = 0
    lines = _split_lines(content)
    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # JS/TS comment lines — same intent as the # skip above.
        if is_js_ts and (stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*")):
            continue
        # Skip Python string literals (templates, help text, CI workflows)
        if is_python and _is_python_string_context(stripped):
            continue
        # Skip lines that ARE regex-pattern definitions (JS/TS or Python).
        # The literal text "GITHUB_TOKEN" inside `[/ghr_[A-Za-z0-9]{36}/g,
        # "GITHUB_TOKEN"]` is a label paired with a regex source that
        # detects the token format, not a usage of an actual token.
        if (is_js_ts and js_regex_re.search(line)) or (is_python and py_regex_re.search(line)):
            continue
        # Skip argparse / click / typer style ENV-VAR-NAME defaults:
        #   parser.add_argument("--token", default="GITHUB_TOKEN")
        #   click.option("--token", envvar="GITHUB_TOKEN")
        # The string "GITHUB_TOKEN" here is the NAME of an env var the
        # CLI will look up — not a credential. Same for `os.environ.get("GITHUB_TOKEN")`
        # and `os.getenv("GITHUB_TOKEN")` patterns: these are reads, not
        # writes/leaks. Skip the line if it pairs the literal token name
        # with one of those declarative APIs.
        if any(
            api in line
            for api in (
                "default=",
                "envvar=",
                "env=",
                "os.environ.get",
                "os.getenv",
                "process.env.",
                "process.env[",
                "std::env::var",
                "env::var",
            )
        ):
            continue
        # Skip env-var-mapping configs: `OPENROUTER_API_KEY: "CLAUDE_PLUGIN_OPTION_..."`,
        # `"AWS_SECRET_ACCESS_KEY": process.env.AWS_SECRET_ACCESS_KEY`. Both
        # sides of the colon/equals reference the same NAME (or a known
        # placeholder); no credential value is being declared.
        if "CLAUDE_PLUGIN_OPTION_" in line or "process.env." in line:
            continue
        # v2.46 FP-G — GitHub Actions canonical secrets-passthrough.
        # Lines like `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` or
        # `AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}` are the
        # CORRECT, idiomatic, GitHub-recommended way to pass repo
        # secrets to a workflow step. The right-hand side reads the
        # GitHub-managed secret store via the `secrets.X` expression —
        # there is NO credential value embedded; the actual value is
        # injected at runtime by the GitHub Actions runner. Suppress
        # when the line contains `${{ secrets.<X> }}` syntax.
        if re.search(r"\$\{\{\s*secrets\.\w+\s*\}\}", line):
            continue
        # v2.46 FP-G — also skip when the line is INSIDE a YAML
        # `env:` block referencing a GitHub Actions step output
        # `${{ steps.<id>.outputs.<name> }}` or job output
        # `${{ needs.<id>.outputs.<name> }}` — those are also runtime
        # injections, not credentials.
        if re.search(r"\$\{\{\s*(?:steps|needs|env|inputs|github)\.[\w.]+\s*\}\}", line):
            continue
        for pattern, msg in CREDENTIAL_HARVEST_PATTERNS:
            m = pattern.search(line)
            if m:
                # Skip when the keyword (`keychain`, `gnome-keyring`, …)
                # is inside a string-CONTAINMENT check rather than a
                # file-open call. Rust `msg.contains("keychain")`, JS
                # `text.includes("keychain")`, Python `"keychain" in s`
                # — these are INSPECTING text for the keyword, not
                # opening a keystore. The whole line is the signal:
                #   • contains `.contains(`  / `.includes(`         → match
                #   • Python `"keyword" in <var>` (no open/read)    → match
                # Real credential harvest goes through I/O APIs
                # (open, read_text, fs.readFileSync, …) — those
                # remain flagged.
                io_apis = (
                    "open(",
                    "read_text(",
                    "Path(",
                    "with open",
                    "fs.readFile",
                    "fs.read",
                    "readFileSync",
                    "std::fs::read",
                    "fs::File::open",
                )
                has_io = any(api in line for api in io_apis)
                if not has_io and (
                    ".contains(" in line or ".includes(" in line or re.search(r"['\"](?:[^'\"]+)['\"]\s+in\s+\w", line)
                ):
                    continue
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
    # v2.48 — non-executable data files.
    if (
        is_lockfile(file_path)
        or _is_research_data_path(file_path)
        or _is_tabular_data_file(file_path)
        or _is_jupyter_notebook(file_path)
    ):
        return 0
    file_normalized = file_lower.replace("\\", "/")
    if (
        "/tests/" in file_normalized
        or file_normalized.startswith("tests/")
        or re.search(r"\.(?:test|spec)\.[mc]?[jt]sx?$", file_normalized)
    ):
        return 0
    is_python = file_lower.endswith(".py")

    issues_found = 0
    lines = _split_lines(content)
    # GENERAL: detect heredoc / multi-line-string regions in shell or
    # Python source. Lines INSIDE a `cat <<'EOF' … EOF` heredoc or a
    # Python `"""…"""` docstring are TEXT being emitted, not live shell
    # commands. The threat model for RC-152..156 is "the script
    # actively bypasses safety controls"; a doc list of prohibited
    # flags inside a heredoc is the OPPOSITE — it's the script teaching
    # users what NOT to do.
    in_heredoc_lines: set[int] = set()
    if file_lower.endswith((".sh", ".bash", ".zsh", ".ksh")):
        heredoc_re = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
        active_marker: str | None = None
        for i, ln in enumerate(lines, start=1):
            if active_marker is None:
                m = heredoc_re.search(ln)
                if m:
                    active_marker = m.group(1)
            else:
                # End-of-heredoc: line stripped equals the marker.
                if ln.strip() == active_marker:
                    active_marker = None
                else:
                    in_heredoc_lines.add(i)
    for line_num, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # GENERAL: skip lines inside a shell heredoc (text being emitted,
        # not live shell). Catches markdown-style list items and
        # documentation prose embedded in `cat <<'EOF' … EOF`.
        if line_num in in_heredoc_lines:
            continue
        # GENERAL: markdown-bullet shape (`- Skip hooks`,
        # `* Skip hooks`, `+ Skip hooks`) inside any source file is a
        # doc list, not a live operation. Same shape as a markdown
        # bullet point.
        if re.match(r"^[\s>]*[-*+]\s", line):
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
        DANGEROUS_MCP_COMMANDS = frozenset(
            {
                "socat",
                "ncat",
                "nc",
                "netcat",
                "php",
                "ruby",
                "perl",
                "lua",
                "telnet",
                "rsh",
                "ssh-keyscan",
            }
        )

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
                        if (
                            "node_tls_reject_unauthorized" in fm_normalized.replace(":", "")
                            or "pythonhttpsverify=0" in fm_normalized
                        ):
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

    Per-file safety: files larger than ``MAX_SCAN_BYTES`` (default 8 MiB,
    overridable via ``CPV_MAX_SCAN_BYTES``) are skipped with a WARNING and
    counted as ``oversize_skipped``. This prevents the worker-deadlock
    pathology documented in issue #15 where pathologically large files
    (50MB minified bundles, concatenated SQL dumps) pin a worker for tens
    of minutes while the per-line scanners thrash on huge allocations.
    """
    stats = {
        "files_scanned": 0,
        "files_skipped": 0,
        "oversize_skipped": 0,
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

            # Always-skip runtime artifacts (Cisco scan output, CPV
            # integrity manifest). These contain literal pattern strings
            # quoted in JSON (`"eval("`, `"curl … | sh"`, `/Users/...`)
            # — scanning them produces a flood of FPs against the SAME
            # rules that already fired on the real source.
            if filename in ALWAYS_SKIP_BASENAMES:
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

            # Per-file size cap (issue #15) — pathological files cause the
            # 9 per-line scanners to thrash on huge allocations and deadlock
            # the worker. Stat-and-skip is O(1); the actual read+scan would
            # be O(N) per scanner × 9 scanners.
            try:
                fsize = file_path.stat().st_size
            except OSError:
                fsize = 0
            if fsize > MAX_SCAN_BYTES:
                report.warning(
                    f"File too large to scan ({fsize:,} bytes > "
                    f"{MAX_SCAN_BYTES:,} cap); skipped to avoid scanner "
                    f"deadlock. Override via CPV_MAX_SCAN_BYTES env var "
                    f"if this file genuinely needs scanning.",
                    rel_path,
                )
                stats["oversize_skipped"] += 1
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


def scan_one_target(
    target: Path,
    report: ValidationReport | None = None,
    *,
    timeout_seconds: int | None = None,
) -> dict[str, int]:
    """Public orchestrator-friendly entry point that wraps scan_all_files.

    Issue #15 — orchestrators that fan `scan_all_files` out across many
    targets (e.g. one per skill folder in a 200K-target corpus) used to
    have to re-implement their own SIGALRM-based timeout guard around the
    private API. This helper exposes a documented surface that does the
    right thing by default.

    Args:
        target: Path to the plugin/skill directory to scan.
        report: Optional ValidationReport. If None, a fresh one is created.
        timeout_seconds: Optional wall-clock timeout per target. When set,
            installs a SIGALRM that raises ``TimeoutError`` if the scan
            takes longer than ``timeout_seconds``. Caller is expected to
            run in a process pool worker — SIGALRM is process-local on
            POSIX. Pass ``None`` (default) to disable the timeout (suitable
            when the per-file ``MAX_SCAN_BYTES`` cap is sufficient).

    Returns:
        The same stats dict shape as ``scan_all_files``.

    Raises:
        TimeoutError: when ``timeout_seconds`` is set and the scan exceeds
            the wall-clock budget.

    Notes:
        - On Windows ``signal.SIGALRM`` is not available; if
          ``timeout_seconds`` is requested on a non-POSIX platform, the
          helper logs a WARNING and proceeds without the timer (the per-
          file ``MAX_SCAN_BYTES`` cap is still active).
        - The per-file size cap (``MAX_SCAN_BYTES``, default 8 MiB) is
          applied unconditionally — it is the primary defense against the
          deadlock pathology and works across all platforms.
    """
    if report is None:
        report = ValidationReport()

    if timeout_seconds is None:
        return scan_all_files(target, report)

    # POSIX-only signal-based timeout. Windows lacks SIGALRM.
    import signal as _signal

    if not hasattr(_signal, "SIGALRM"):
        report.warning(
            f"Per-target timeout requested ({timeout_seconds}s) but "
            f"signal.SIGALRM is not available on this platform. The "
            f"per-file MAX_SCAN_BYTES cap remains active.",
            str(target),
        )
        return scan_all_files(target, report)

    def _alarm_handler(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"scan_one_target({target}) exceeded {timeout_seconds}s")

    prev_handler = _signal.signal(_signal.SIGALRM, _alarm_handler)
    _signal.alarm(timeout_seconds)
    try:
        return scan_all_files(target, report)
    finally:
        _signal.alarm(0)
        _signal.signal(_signal.SIGALRM, prev_handler)


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

    v2.48 — prefer the persistent ``cc-audit`` binary on PATH (installed via
    ``npm install -g @cc-audit/cc-audit`` by ``cpv-doctor --install-scanners``)
    so we skip the ~5-15s ``npx --yes`` resolve cost on every scan. Fall back
    to ``npx --yes @cc-audit/cc-audit`` when no persistent binary is present.

    Output is saved to a temp JSON file to avoid context bloat, then parsed.
    Returns the number of issues found. Returns 0 if neither path is available.
    """
    # Resolve the launch prefix: persistent binary (faster) > npx (slower).
    persistent = shutil.which("cc-audit")
    npx_path = shutil.which("npx")
    if persistent:
        launcher: list[str] = ["cc-audit"]
    elif npx_path:
        launcher = ["npx", "--yes", "@cc-audit/cc-audit"]
    else:
        report.warning(
            "cc-audit: not found — 100+ additional security rules skipped. "
            "Run `cpv-doctor --install-scanners` (preferred) or "
            "`npm install -g @cc-audit/cc-audit`."
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
            launcher + ["init", str(plugin_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        created_config = config_file.exists()

    try:
        result = subprocess.run(
            launcher
            + [
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

            # Always-skip well-known runtime artifacts (Cisco scan output,
            # CPV integrity manifest). Same rationale as the in-process
            # scanners — these files are scan dumps quoting every pattern
            # the source-scanner ever flagged, and cc-audit will happily
            # re-flag them on a re-scan.
            if file_ref and Path(str(file_ref)).name in ALWAYS_SKIP_BASENAMES:
                continue

            # CPV self-scan: skip cc-audit findings on files the running CPV
            # has marked as canonical (validator source / fix-validation refs
            # / security tests). cc-audit hands back absolute paths;
            # cpv_self_scan_skip handles the abs→rel normalization.
            if file_ref and cpv_self_scan_skip(str(file_ref)):
                continue
            # v2.48 P-1 — augment with per-line pattern-source predicate
            # for cc-audit findings whose file's hash has drifted (or
            # wasn't in the manifest). Read the file and check the line.
            if file_ref and isinstance(line, int) and line > 0:
                try:
                    fpath = Path(str(file_ref))
                    if fpath.is_file() and fpath.stat().st_size < 2_000_000:
                        body = fpath.read_text(encoding="utf-8", errors="ignore")
                        if cpv_self_scan_skip_line(str(file_ref), body, int(line)):
                            continue
                except OSError:
                    pass

            # v2.43 — drop findings inside vendored / cached / build dirs.
            if file_ref and _is_vendored_dep_path(str(file_ref)):
                continue
            # v2.44 — drop findings inside gitignored dev-scratch dirs.
            if file_ref and _is_dev_scratch_path(str(file_ref)):
                continue

            # v2.48 P-2 sibling — drop findings on Python test files.
            # Pytest test files (test_*.py, *_test.py, conftest.py, anything
            # under tests/) ship with fixture strings containing the very
            # tokens the rules detect, so the test can verify the detector
            # fires. cc-audit grep-matches the file content directly,
            # missing these as fixtures. Other CPV scanners (RC-37 in
            # `check_phase1_supply_chain_rules`, RC-21 / RC-65 in their
            # respective phase scanners) already early-exit on
            # `_is_test_file_path`; this brings cc-audit in line.
            if file_ref and _is_test_file_path(str(file_ref)):
                continue
            # v2.48 P-3 — drop findings on rule-corpus markdown.
            # `is_fp_corpus_markdown` requires both directory shape AND
            # an in-file marker, so coincidental `fixtures/` markdown
            # without a corpus marker remains scanned.
            if file_ref:
                try:
                    fpath = Path(str(file_ref))
                    if fpath.is_file() and fpath.stat().st_size < 2_000_000:
                        body = fpath.read_text(encoding="utf-8", errors="ignore")
                        if is_fp_corpus_markdown(str(file_ref), body):
                            continue
                except OSError:
                    pass

            # v2.45 FP3 — drop cc-audit findings on documentation
            # markdown. cc-audit flags shell-command text inside .md
            # files (`chmod 755 design/`, `echo 'export
            # JAVA_HOME=…' >> ~/.zshrc`) as live attack content, but
            # documentation / reference / troubleshooting / changelog
            # markdown is talking ABOUT shell commands, not running
            # them. The model ingesting a reference doc never
            # executes the snippet — it consumes it as guidance.
            #
            # Carve-out: SKILL.md / agent body / command body MAY
            # carry instructions the model interprets directly. Keep
            # cc-audit's signal on those exact files. Everything
            # else (references/, troubleshooting.md, design specs,
            # changelogs, READMEs, chat-history exports) is doc.
            if file_ref and str(file_ref).lower().endswith((".md", ".mdx", ".markdown")):
                f_norm = str(file_ref).lower().replace("\\", "/")
                f_basename = f_norm.rsplit("/", 1)[-1] if "/" in f_norm else f_norm
                # Executable AI-facing markdown: SKILL.md, agent
                # body (file directly under /agents/), command body
                # (file directly under /commands/). Only these are
                # the model's instruction surface — everything
                # else is doc.
                is_executable_md = f_basename == "skill.md" or _md_is_agent_body(f_norm) or _md_is_command_body(f_norm)
                if not is_executable_md:
                    continue

            # Pattern-source skip: if the reported line is a regex
            # PATTERN DEFINITION (Python `re.compile(`, JS `/.../g`,
            # `RegExp(`), the literal-string match cc-audit fired on is
            # a detector body, not real-world payload. Same logic as our
            # internal gitleaks/trufflehog/credential-harvest skip.
            if file_ref and isinstance(line, int) and line > 0 and _line_is_pattern_definition(file_ref, line):
                continue

            # v2.46 — also skip cc-audit findings on REPORT-STATUS lines
            # like `report.passed("No sandbox escape patterns detected")`,
            # `report.warning("..."), `report.info("...")`, `print("No
            # X detected")`. These are STATUS MESSAGES describing what
            # the validator just checked — the literal string contains
            # the rule name as part of the description. The validator's
            # OWN status output is not a payload.
            if file_ref and isinstance(line, int) and line > 0 and _line_is_status_report_message(file_ref, line):
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
        cmd = ["docker", "run", "--rm", "-v", f"{plugin_path}:/scan:ro", TIRITH_IMAGE] + [
            "scan",
            "/scan",
            "--format",
            "json",
            "--ci",
        ]
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
            finding.get("severity") or finding.get("level") or finding.get("verdict") or finding.get("kind") or "warn"
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

        # Self-scan / vendored / dev-scratch / test-file / corpus filters.
        # tirith hands back absolute paths; cpv_self_scan_skip handles the
        # abs→rel normalisation. The same filter ladder cc-audit uses is
        # applied here so CPV scanning itself doesn't surface its own rule
        # catalogs, regex sources, parametrize fixtures, or FP-corpus
        # markdown as tirith findings.
        if file_ref:
            f_str = str(file_ref)
            if _is_always_skip_basename(f_str):
                continue
            if cpv_self_scan_skip(f_str):
                continue
            if _is_vendored_dep_path(f_str):
                continue
            if _is_dev_scratch_path(f_str):
                continue
            if _is_test_file_path(f_str):
                continue
            # Per-line catalog/docstring/comment pattern-source skip.
            if isinstance(line, int) and line > 0:
                try:
                    fpath = Path(f_str)
                    if fpath.is_file() and fpath.stat().st_size < 2_000_000:
                        body = fpath.read_text(encoding="utf-8", errors="ignore")
                        if cpv_self_scan_skip_line(f_str, body, int(line)):
                            continue
                        # FP-corpus markdown skip (file-level, requires
                        # both directory shape AND in-file marker).
                        if is_fp_corpus_markdown(f_str, body):
                            continue
                except OSError:
                    pass

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


_LOCKFILE_BASENAMES = frozenset(
    {
        "uv.lock",
        "pipfile.lock",
        "poetry.lock",
        "pdm.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "cargo.lock",
        "gemfile.lock",
        "composer.lock",
        "mix.lock",
        "go.sum",
        "deno.lock",
        "bun.lock",
        "bun.lockb",
    }
)


def is_lockfile(file_path: str) -> bool:
    """Recognize package-manager lockfiles by basename.

    Lockfiles are auto-generated dependency manifests. Their content is
    machine-controlled and consists of package names, versions, and
    integrity hashes — every name pattern (uv.lock contains
    `name = "anthropic"`, package-lock.json contains `"name": "system"`
    for various deps) trips the agent-identity-spoofing / hardcoded-name
    rules. They're not part of the plugin's runtime attack surface;
    skipping them avoids the entire FP class. RC-30/RC-33
    (typosquatting / compromised packages) read these files separately
    via direct rglob and parse them as data, so this skip only applies
    to the regex-pattern catalog.
    """
    basename = file_path.lower().replace("\\", "/").rsplit("/", 1)[-1]
    return basename in _LOCKFILE_BASENAMES


def _iter_scannable_files(plugin_path: Path):
    """Yield (file_path, rel_path, content) for every non-binary scannable file.

    Honors the same self-scan skip set as scan_all_files — checking only
    `is_validator_script` would let dev-scratch dirs (docs_dev/,
    design/tasks/, …) and hash-verified fix-validation references slip
    through and produce the same FPs the main scan loop suppresses.

    Also skips lockfiles — every regex pattern in the security catalog
    that scans file content (e.g. RC-59 agent-name spoofing) trips on
    dependency names like `name = "anthropic"`. Lockfiles are not part
    of the runtime attack surface and the rules that DO need them
    (RC-30, RC-33) parse them as data via direct rglob, not via this
    iterator.

    v2.48 P-3 — FP-corpus markdown files (rule-corpus benchmarks under
    `fp_corpus/` or `fixtures/fp/` etc., with a structural TP/FP-shape
    marker in their first 5 lines) are skipped. The bench harness
    already validates these files; the security scanner re-emitting on
    them is duplicate noise.
    """
    gi = get_gitignore_filter(plugin_path)
    for root, _dirs, files in gi.walk(plugin_path):
        for filename in files:
            file_path = Path(root) / filename
            # Always-skip well-known runtime artifacts (Cisco scan dump,
            # CPV integrity manifest). Same rationale as scan_all_files —
            # these are scan-output files, not plugin source.
            if filename in ALWAYS_SKIP_BASENAMES:
                continue
            if is_binary_file(file_path):
                continue
            # Per-file size cap (issue #15) — applies to every phase scanner
            # too, not just scan_all_files. Pathological files would otherwise
            # hang the phase scanners' inner loops the same way.
            try:
                fsize = file_path.stat().st_size
            except OSError:
                fsize = 0
            if fsize > MAX_SCAN_BYTES:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            rel_path = str(file_path.relative_to(plugin_path))
            if cpv_self_scan_skip(rel_path):
                continue
            if is_lockfile(rel_path):
                continue
            # v2.48 P-3 — rule-corpus markdown is benchmark fixture.
            if is_fp_corpus_markdown(rel_path, content):
                continue
            yield file_path, rel_path, content


def check_phase1_unicode_rules(plugin_path: Path, report: ValidationReport) -> int:
    """RC-09 (zero-width), RC-10 (TAG block), RC-11 (mixed-script) — all pass."""
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        # v2.48 P-2 — line-aware skip set (parametrize bodies, pattern
        # source). Each rule below consults this so attack tokens that
        # are pattern-fixtures (parametrize body) or rule-catalog
        # literals don't fire as live findings.
        content_lines = _split_lines(content)
        # RC-09 — zero-width characters
        for line_no, desc in find_zero_width_chars(content):
            if cpv_self_scan_skip_line(rel_path, content_lines, line_no):
                continue
            level = effective_severity("major", rel_path)
            getattr(report, level)(
                f"RC-09: zero-width Unicode at line {line_no} ({desc})",
                rel_path,
                line_no,
            )
            issues += 1

        # RC-10 — TAG block (always CRITICAL — no legitimate use)
        for line_no, codepoint in find_tag_block_chars(content):
            if cpv_self_scan_skip_line(rel_path, content_lines, line_no):
                continue
            level = effective_severity("critical", rel_path)
            getattr(report, level)(
                f"RC-10: TAG character {codepoint} at line {line_no} (AsciiSmuggler vector)",
                rel_path,
                line_no,
            )
            issues += 1

        # RC-11 — mixed-script (only on identifier-shape tokens to avoid
        # FP on prose that legitimately mixes scripts e.g. "Cyrillic 'а' is U+0430")
        #
        # GENERAL i18n exemption: i18n locale files / per-language docs
        # (`locales/ru.json`, `README.ru.md`, `guides/setup-zh.md`,
        # `i18n/ja/messages.json`, …) legitimately contain compound
        # terminology where a Latin acronym is combined with a
        # non-Latin word (`API-вызов` "API call", `JSON-файл` "JSON
        # file", `MCP-инструменты` "MCP tools", `HTML-페이지` "HTML
        # page"). Russian, Japanese, Korean, Greek docs all use this
        # convention.
        #
        # Detection: file path contains `locales/`, `i18n/`, `lang/`,
        # `translations/`, OR basename has language-code segment
        # (`README.ru.md`, `guide.zh-cn.md`, `messages.ja.json`).
        is_i18n_file = _is_i18n_file_path(rel_path)
        for line_no, line in enumerate(content_lines, start=1):
            # v2.48 P-2 — parametrize bodies in test files are pattern
            # fixtures; homograph-attack tokens declared inside them are
            # there for the rule to ASSERT it fires — already covered by
            # the assertion in the test body, no need to re-emit at scan.
            if cpv_self_scan_skip_line(rel_path, content_lines, line_no):
                continue
            for token in re.findall(r"[\w._-]{3,80}", line):
                mixed, reason = has_mixed_script(token)
                if mixed:
                    # GENERAL: skip "Latin acronym + hyphen/underscore +
                    # non-Latin word" compound. This is how non-Latin
                    # languages canonically describe APIs/protocols/
                    # standards: the protocol name keeps its Latin
                    # acronym (API/JSON/HTML/HTTP/MCP) and the
                    # descriptor uses the local script
                    # (`API-вызов`/`JSON-файл`/`HTML-페이지`).
                    if is_i18n_file or _is_acronym_compound(token):
                        continue
                    level = effective_severity("critical", rel_path, rule_id="RC-11")
                    getattr(report, level)(
                        f"RC-11: mixed-script identifier '{token}' at line {line_no} ({reason})",
                        rel_path,
                        line_no,
                    )
                    issues += 1
                    break  # one finding per line is enough
    return issues


def check_phase1_credential_rules(plugin_path: Path, report: ValidationReport) -> int:
    """RC-21 — process.env / os.environ bulk harvest."""
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        fence_state = build_fence_state(content)
        content_lines = _split_lines(content)
        for line_no, line in enumerate(content_lines, start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            # v2.48 P-1 — pattern-source predicate.
            if cpv_self_scan_skip_line(rel_path, content_lines, line_no):
                continue
            for pattern in ENV_BULK_HARVEST_PATTERNS:
                if pattern.search(line):
                    # v2.46 — widen the surrounding window to 30 lines.
                    # The `env = os.environ.copy()` idiom is often set
                    # early in a function, then mutated through several
                    # `if`/conditional blocks (`env["GIT_TOKEN"] = ...`)
                    # before being passed to `subprocess.run(env=env)`
                    # 10-25 lines later. The v2.41 window=4 was way
                    # too narrow; even the initial v2.46 widening to
                    # 15 missed real-world cross-compile builders that
                    # set DOCKER env vars between copy and run. 30
                    # lines is enough to cover the longest prep blocks.
                    surrounding_classifier = _surrounding_lines(content_lines, line_no - 1, window=4)
                    surrounding_subproc = _surrounding_lines(content_lines, line_no - 1, window=30)
                    # v2.42 — opt-in classifier path (TRDD-fe006962). When
                    # active, the per-rule classifier subsumes the v2.41
                    # binary `_rc21_is_subprocess_prep` guard and can also
                    # demote (LIKELY_FP → MINOR) instead of suppressing.
                    if _CLASSIFIER_ACTIVE:
                        severity, _note = _classifier_decision(
                            "RC-21",
                            "major",
                            line,
                            surrounding_classifier,
                            _file_role_from_path(rel_path),
                            rel_path,
                        )
                        if severity is None:
                            break
                        level = effective_severity(severity, rel_path, rule_id="RC-21")
                    else:
                        # v2.41.0 binary guard: subprocess env-prep is FP.
                        if _rc21_is_subprocess_prep(line, surrounding_subproc):
                            break
                        level = effective_severity("major", rel_path, rule_id="RC-21")
                    getattr(report, level)(
                        f"RC-21: bulk env-var harvest at line {line_no}",
                        rel_path,
                        line_no,
                    )
                    issues += 1
                    break
    return issues


def check_phase1_supply_chain_rules(plugin_path: Path, report: ValidationReport) -> int:
    """RC-29 (.pth executable), RC-37 (GTFOBins/LOLBins), RC-67 (cryptomining)."""
    issues = 0
    for file_path, rel_path, content in _iter_scannable_files(plugin_path):
        # GENERAL: skip test files & test fixtures. Test suites for
        # security tools (cc-safety-net, cpv itself, gitleaks-checkers,
        # etc.) ship with FIXTURE STRINGS containing the very patterns
        # they detect — `ruby -e "exec(...)"`, `perl -e "system(...)"`,
        # `curl | bash` — so the test can verify the detector fires.
        # Without this skip, those plugins land 100% MAJOR FPs by
        # construction.
        if _is_test_file_path(rel_path):
            continue
        # RC-29 — .pth file with import/exec
        if is_pth_with_exec(file_path.name, content):
            level = effective_severity("critical", rel_path)
            getattr(report, level)(
                "RC-29: Python .pth file contains executable lines (import/exec) — runs at every interpreter startup",
                rel_path,
                1,
            )
            issues += 1

        fence_state = build_fence_state(content)
        content_lines_phase2c = _split_lines(content)
        for line_no, line in enumerate(content_lines_phase2c, start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            # v2.48 P-1 — pattern-source predicate.
            if cpv_self_scan_skip_line(rel_path, content_lines_phase2c, line_no):
                continue

            # RC-37 — GTFOBins / LOLBins
            for pattern in GTFOBIN_LOLBIN_PATTERNS:
                m = pattern.search(line)
                if m and not has_negation_guard_nearby(content, content.find(line) + m.start()):
                    level = effective_severity("critical", rel_path, rule_id="RC-37")
                    getattr(report, level)(
                        f"RC-37: GTFOBin/LOLBin pattern at line {line_no}: {m.group(0)[:80]}",
                        rel_path,
                        line_no,
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
                        rel_path,
                        line_no,
                    )
                    issues += 1
                    break
    return issues


def check_phase1_evasion_rules(plugin_path: Path, report: ValidationReport) -> int:
    """RC-43 — time-bomb / conditional activation."""
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        fence_state = build_fence_state(content)
        content_lines_phase1e = _split_lines(content)
        for line_no, line in enumerate(content_lines_phase1e, start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            # v2.48 P-1 — pattern-source predicate.
            if cpv_self_scan_skip_line(rel_path, content_lines_phase1e, line_no):
                continue
            for pattern in TIMEBOMB_PATTERNS:
                if pattern.search(line):
                    level = effective_severity("critical", rel_path)
                    getattr(report, level)(
                        f"RC-43: time-bomb / conditional-activation at line {line_no}",
                        rel_path,
                        line_no,
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
                            rel_path,
                            0,
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
                                rel_path,
                                0,
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
                            rel_path,
                            0,
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
            level = effective_severity(severity, rel_path, rule_id=f.rule_id)
            getattr(report, level)(
                f"{f.rule_id}: tainted '{f.var_name}' from {f.source} reaches {f.sink} (hop_count={f.hop_count})",
                rel_path,
                f.line,
            )
            issues += 1
    return issues


_RC76_SOURCE_EXTENSIONS = (
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".m",
    ".mm",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".cs",
    ".scala",
    ".clj",
    ".ex",
    ".exs",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
)
_RC76_CHANGELOG_BASENAMES = frozenset(
    {
        "changelog.md",
        "changelog.markdown",
        "changelog.txt",
        "changelog.rst",
        "changes.md",
        "changes.markdown",
        "changes.txt",
        "changes.rst",
        "history.md",
        "history.markdown",
        "history.txt",
        "history.rst",
        "news.md",
        "news.markdown",
        "news.txt",
        "news.rst",
        "releasenotes.md",
        "release_notes.md",
        "release-notes.md",
    }
)

# v2.46 FP-L — Plain-config / ignore-pattern files. These are read
# by the harness or build tools as config, never by the LLM as
# instructions. RC-76's stemmed-injection rule is FP-by-construction
# here because words like `secret`, `leak`, `ignore`, `rules`,
# `forget` appear legitimately in comments / patterns
# (`# secrets that leaked into logs`, `*.rules`, `forget-me-not/`).
_RC76_NON_AI_CONFIG_BASENAMES = frozenset(
    {
        ".gitignore",
        ".dockerignore",
        ".npmignore",
        ".eslintignore",
        ".prettierignore",
        ".gcloudignore",
        ".helmignore",
        ".gitattributes",
        ".editorconfig",
        ".env.example",
        ".env.sample",
        ".env.template",
        "license",
        "license.md",
        "license.txt",
        "license.rst",
        "licence",
        "licence.md",
        "licence.txt",
        "copying",
        "copying.txt",
        "notice",
        "notice.txt",
        "authors",
        "authors.md",
        "authors.txt",
        "contributors",
        "contributors.md",
        "contributors.txt",
        "code_of_conduct.md",
        "code-of-conduct.md",
        "contributing.md",
        "security.md",
    }
)

# Vendored / cached / build-output directories that contain code the plugin
# does NOT own. Every external scanner (trufflehog, gitleaks, semgrep,
# cc-audit) flags transitive deps inside these trees as plugin findings,
# but they belong to the dep ecosystem and are auto-installed at build
# time. CPV's own scan loops use `get_gitignore_filter` which already
# drops these paths; the external scanners do not, so we post-filter
# their output. v2.43.
_VENDORED_DEP_DIR_PARTS = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "site-packages",
        "__pycache__",
        ".pnpm-store",
        ".yarn",
        "vendor",
        "dist",
        "build",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
        "target",
        # v2.46 — MCP server cache directories. These are local symbol/
        # index caches written by Serena, Grepika, etc. Not part of the
        # plugin's source tree. Pickled symbol tables routinely contain
        # high-entropy strings that look like API keys to gitleaks.
        ".serena",
        ".grepika",
        # v2.46 — IDE caches (vscode-server only — `.vscode/` may carry
        # plugin-author settings the user wants reviewed).
        ".idea",
        ".vscode-server",
        # v2.46 — Python uv-managed cache.
        ".uv-cache",
    }
)


def _is_vendored_dep_path(file_path: str) -> bool:
    """True if any directory segment matches a vendored / cache / build dir.

    Used by the external-scanner post-filters (trufflehog, gitleaks,
    semgrep, cc-audit) so a transitive dep's bundled README that
    matches a credential-detector pattern doesn't surface as a
    plugin-author finding. Only top-level segment match — substring
    matches like `node_modules-helper.py` stay scanned.
    """
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/").lower()
    return any(f"/{part}/" in normalized or normalized.startswith(f"{part}/") for part in _VENDORED_DEP_DIR_PARTS)


def _rc76_is_source_code_file(rel_path: str) -> bool:
    """RC-76 — source-code files (TS/JS/Python/Go/Rust/etc) trip the
    stemmed-injection rule because LLM-tooling vocabulary
    (prompt/system/instruct/token/output/...) appears legitimately in
    variable / function / type names. Also covers `bin/`-style
    extension-less shell scripts and CHANGELOG-style release-notes
    files (which reference internals when the plugin is itself an
    LLM tool but are not agent-doc instruction surfaces).

    v2.46 FP-L — also skip plain-config / ignore files
    (`.gitignore`, `.dockerignore`, `.npmignore`, `LICENSE`,
    `pyproject.toml`, `package.json`, etc.). These are NEVER part of
    an LLM's instruction surface; the harness reads them as config.
    Words like `secret`, `leak`, `rules`, `ignore`, `forget` appear
    legitimately in their comments (`# secrets that leaked into
    logs`) and in patterns (`/secrets/`). The 80-char co-occurrence
    rule is paranoid for AI-facing prose, but FPs by construction
    on these surfaces.
    """
    rel = rel_path.lower().replace("\\", "/")
    if rel.endswith(_RC76_SOURCE_EXTENSIONS):
        return True
    if "/bin/" in rel or rel.startswith("bin/"):
        return True
    basename = rel.rsplit("/", 1)[-1]
    if basename in _RC76_CHANGELOG_BASENAMES:
        return True
    # v2.46 FP-L — plain-config / ignore files. These are pattern-only
    # and never AI-instruction surfaces.
    if basename in _RC76_NON_AI_CONFIG_BASENAMES:
        return True
    return False


def _rc76_is_security_audit_role(rel_path: str) -> bool:
    """v2.46 FP-N — True if the file path / basename indicates the
    document is a SECURITY AUDIT role definition, checklist, or
    reference. These files legitimately co-mention security stems
    (`secret`, `password`, `token`, `admin`, `system`, `prompt`,
    `injection`, `bypass`, `override`) because cataloguing those
    concepts IS the document's purpose. Examples:
        agents/caa-security-review-agent.md
        skills/caa-security-audit-skill/...
        skills/skill-security-audit/...
        skills/plugin-security-audit/...
    The literal keywords `security`, `audit`, `review` in the path
    indicate the role.
    """
    rel = rel_path.lower().replace("\\", "/")
    # Tokenize the path on `/` and `-`/`_` boundaries.
    role_keywords = (
        "security",
        "audit",
        "review",
        "vulnerability",
        "vulnerabilities",
        "owasp",
        "threat",
        "pentest",
        "exploit",
        "harden",
    )
    parts = re.split(r"[/_-]", rel)
    return any(kw in part for kw in role_keywords for part in parts)


# GENERAL: RC-76 attack-shape signals. RC-76 by itself just looks for
# >=3 stem co-occurrences in an 80-char window — that's a NOISE
# detector. To upgrade a stem co-occurrence to a real prompt-injection
# finding, we want to ALSO see at least one of these structural signals
# in or near the line:
#
# 1. **Jailbreak imperatives**: "ignore previous", "ignore all
#    instructions", "disregard the above", "forget everything",
#    "override instructions", "pretend you", "act as", "you are now",
#    "switch persona", "DAN mode", "developer mode".
# 2. **Second-person directives + system override**: "you must …
#    instructions", "your task is to ignore", "your real instructions",
#    etc. — second-person + override semantics.
# 3. **Quoted role-play hints** that shift LLM context: "the system
#    prompt is …", "previous instructions said …", "respond only with",
#    "do not include", "do not warn".
# 4. **System-prompt leakage requests**: "tell me your system prompt",
#    "reveal your instructions", "what are your rules", "print the
#    above", "echo your prompt".
#
# These shapes are the ACTUAL prompt-injection threat. A line that has
# 3 stem co-occurrences but NO attack shape is documentation /
# explanatory prose — the rule should not fire on it in agent/skill
# bodies that legitimately describe security topics.
_RC76_ATTACK_SHAPE_RE = re.compile(
    r"(?:"
    # Jailbreak imperatives
    r"\bignore\s+(?:all\s+)?(?:previous|prior|the\s+above|above|earlier)\b"
    r"|\bdisregard\s+(?:all\s+)?(?:previous|prior|the\s+above|above|earlier)\b"
    r"|\bforget\s+(?:all|everything|prior|the\s+above)\b"
    r"|\boverride\s+(?:the\s+|all\s+)?(?:instructions?|prompt|rules|system)\b"
    r"|\bpretend\s+(?:you|to\s+be)\b"
    r"|\bact\s+as\s+(?:if|though|a)\b"
    r"|\byou\s+are\s+now\b"
    r"|\b(?:DAN|jailbreak|developer)\s+mode\b"
    # System-prompt leakage requests
    r"|\b(?:reveal|tell\s+me|print|echo|show|output)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions|rules)\b"
    r"|\bwhat\s+(?:are|were)\s+your\s+(?:original\s+)?(?:instructions|rules|prompt)\b"
    # Override semantics with second-person
    r"|\byour\s+(?:real|actual|true)\s+(?:instructions?|task|goal|prompt)\b"
    r"|\bnew\s+(?:instructions?|prompt|rules)\s*[:.]?\s*$"
    # Refusal-suppression imperatives
    r"|\bdo\s+not\s+(?:warn|refuse|disclose|mention)\b"
    r"|\brespond\s+only\s+with\b"
    r"|\bnever\s+say\s+(?:no|i\s+can'?t|i\s+cannot)\b"
    r")",
    re.IGNORECASE,
)


def _rc76_has_attack_shape(line: str, surrounding: str) -> bool:
    """GENERAL: True when `line` (or its 200-char surrounding context)
    contains a structural prompt-injection signal beyond mere stem
    co-occurrence.

    Stem co-occurrence by itself is NOISE — the same vocabulary lives
    in legitimate documentation about prompt design, security topics,
    instruction tuning, etc. A REAL prompt-injection attack has an
    additional structural signal: a jailbreak imperative ("ignore
    previous"), a system-prompt leakage request ("tell me your
    instructions"), an override-semantics phrase ("your real task"),
    or a refusal-suppression imperative ("do not warn").

    We test BOTH the matched line and a small window of surrounding
    text (200 chars) so multi-line attack shapes don't slip through.

    For agent/skill/command/plugin-readme files, this predicate is
    REQUIRED for RC-76 to fire. For raw prose documentation
    (like a tutorial about security), the absence of attack shape
    means RC-76 is FP and should be suppressed.
    """
    if _RC76_ATTACK_SHAPE_RE.search(line):
        return True
    if surrounding and _RC76_ATTACK_SHAPE_RE.search(surrounding):
        return True
    return False


def check_phase9_stemmed_injection(plugin_path: Path, report: ValidationReport) -> int:
    """Phase 9 — RC-76 stemmed semantic injection classifier.

    Catches paraphrased prompt-injection attempts that exact regex patterns
    miss because of word-form variation. Fires only when ≥3 trigger stems
    co-occur within an 80-char window — single keywords are too noisy.

    v2.43 — source-code files (`.ts`/`.js`/`.py`/`.go`/etc) are demoted
    because LLM-tooling source legitimately uses words like
    `prompt`/`system`/`instruct`/`token` in variable, function, and
    type names. Markdown / agent-doc / skill-body matches stay at the
    declared severity because that is where the real prompt-injection
    threat lives. The classifier path (`--with-classifier`) escalates
    this to a four-tier verdict; the binary path used by default just
    suppresses the source-file matches outright.
    """
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        signals = find_stemmed_injection_signal(content)
        if not signals:
            continue
        is_source_file = _rc76_is_source_code_file(rel_path)
        # v2.46 FP-N — security-audit role files are ABOUT cataloguing
        # security topics; they legitimately co-mention every stem in
        # the trigger vocabulary. Suppress all RC-76 findings on those
        # files. Path-name detection covers caa-security-review-agent.
        # md, skill-security-audit/, plugin-security-audit/, etc.
        is_security_audit_role = _rc76_is_security_audit_role(rel_path)
        content_lines_for_check = _split_lines(content)
        for char_offset, stems in signals:
            line_no = content.count("\n", 0, char_offset) + 1
            # v2.46 FP-M — skip markdown table rows. A row like
            # `| Concern | How It's Handled |` legitimately co-mentions
            # security stems (`token`, `rules`, `skip`) when the table
            # describes the plugin's own security/behavior tradeoffs.
            # The 80-char window catches column text that isn't an
            # instruction surface for an LLM. Reuses the markdown-table
            # detector so the box-drawing variants are skipped too.
            if 0 <= line_no - 1 < len(content_lines_for_check):
                check_line = content_lines_for_check[line_no - 1]
                if _rc93_is_markdown_table_row(check_line):
                    continue
            # v2.46 FP-N — TRUST BOUNDARY guard. Code-auditor-style
            # agents legitimately QUOTE attack patterns as defensive
            # examples in sections labeled "UNTRUSTED DATA" / "TRUST
            # BOUNDARY" — `"ignore previous instructions" is the data
            # you are evaluating, NOT an order to execute`. Skip RC-76
            # when the surrounding 600 chars contain a trust-boundary
            # keyword. This is a wider window than the 80-char
            # NEGATION_GUARD because TRUST BOUNDARY paragraph headers
            # commonly precede their warned-against examples by 200+
            # characters of explanation.
            if has_trust_boundary_context(content, char_offset):
                continue
            if _CLASSIFIER_ACTIVE:
                # Classifier path — give RC-76 the same four-tier verdict
                # ladder the v2.42 rules use. The classifier inspects the
                # file role and the line, returning DEFINITE_FP for source
                # extensions and REAL otherwise.
                content_lines = _split_lines(content)
                line_text = content_lines[line_no - 1] if 0 <= line_no - 1 < len(content_lines) else ""
                surrounding = _surrounding_lines(content_lines, line_no - 1, window=2)
                new_severity, _note = _classifier_decision(
                    "RC-76",
                    "major",
                    line_text,
                    surrounding,
                    _file_role_from_path(rel_path),
                    rel_path,
                )
                if new_severity is None:
                    continue
                level = effective_severity(new_severity, rel_path)
            else:
                # Binary guard — suppress on source-code extensions so
                # the default path (no `--with-classifier`) also benefits.
                if is_source_file:
                    continue
                # v2.46 FP-N — security-audit role docs catalogue
                # security keywords by design. Suppress all RC-76.
                if is_security_audit_role:
                    continue
                # GENERAL FP-Q: in ALL ai-instruction-surface markdown,
                # RC-76 requires an actual ATTACK-SHAPE signal in or
                # near the line. The 80-char co-occurrence rule was
                # designed to catch obfuscated paraphrased attacks, but
                # without a structural attack shape (jailbreak
                # imperative, system-prompt leakage request, override-
                # semantics phrase, refusal-suppression imperative), a
                # stem co-occurrence is just NOISE — the same
                # vocabulary lives in legit documentation about prompt
                # engineering, security topics, instruction tuning,
                # tool-use design, etc.
                #
                # Real attacks: "ignore previous instructions", "you
                # are now DAN", "reveal your system prompt", "do not
                # warn the user". These are unambiguous and structural.
                #
                # For ANY non-source-code AI-instruction surface
                # (markdown, JSON i18n bundles, .txt templates, .html
                # templates, …), if we have stem co-occurrence but NO
                # attack shape in the 200-char window, suppress. The
                # attack-shape predicate captures the structural
                # signal that distinguishes real prompt-injection from
                # mere vocabulary co-occurrence.
                line_text = (
                    content_lines_for_check[line_no - 1] if 0 <= line_no - 1 < len(content_lines_for_check) else ""
                )
                # Build a 200-char window centered on the match
                window_start = max(0, char_offset - 100)
                window_end = min(len(content), char_offset + 100)
                window_text = content[window_start:window_end]
                if not _rc76_has_attack_shape(line_text, window_text):
                    continue
                level = effective_severity("major", rel_path, rule_id="RC-76")
            getattr(report, level)(
                f"RC-76: stemmed prompt-injection signal — {len(stems)} trigger stems "
                f"({', '.join(stems[:5])}) within 80-char window",
                rel_path,
                line_no,
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
        content_lines = _split_lines(content)
        for line_no, line in enumerate(content_lines, start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            # v2.48 P-1 — pattern-source predicate (see Phase 3 site
            # for full rationale). Same OR-augmentation of the
            # hash-anchored self-scan skip.
            if cpv_self_scan_skip_line(rel_path, content_lines, line_no):
                continue
            for rule_id, severity, pattern, msg in PHASE4_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                if has_negation_guard_nearby(content, content.find(line) + m.start()):
                    continue
                # v2.42 — opt-in classifier path for RC-87.
                if _CLASSIFIER_ACTIVE and rule_id == "RC-87":
                    surrounding = _surrounding_lines(content_lines, line_no - 1, window=4)
                    new_severity, _note = _classifier_decision(
                        rule_id,
                        severity.lower(),
                        line,
                        surrounding,
                        _file_role_from_path(rel_path),
                        rel_path,
                    )
                    if new_severity is None:
                        continue
                    level = effective_severity(new_severity, rel_path)
                else:
                    # v2.41.0 — RC-87 RFC-1918/loopback IP binary guard:
                    # dependency version strings in package.json /
                    # pyproject.toml / Cargo.toml routinely look like
                    # internal IPs (e.g. `"@types/node": "^10.0.5"`).
                    if rule_id == "RC-87" and _rc87_is_semver_context(line, rel_path):
                        continue
                    level = effective_severity(severity.lower(), rel_path, rule_id=rule_id)
                # v2.45 FP8 — RC-87 in CHANGELOG / HISTORY / NEWS /
                # README is project narrative, never live config.
                # Demote one extra tier on top of the generic doc
                # demotion already applied by `effective_severity`.
                # `demote_severity` doesn't know about NIT (nit isn't
                # in SEVERITY_TIERS), so do the mapping inline:
                # major→minor→nit→info; warning is already as
                # demoted as `effective_severity` can take it, so
                # demote to nit explicitly to differentiate
                # narrative-doc findings from real warnings.
                if rule_id == "RC-87" and _rc87_is_history_doc(rel_path):
                    if level == "minor":
                        level = "nit"
                    elif level == "warning":
                        level = "nit"
                    elif level == "major":
                        level = "minor"
                    # CRITICAL stays critical — narrative docs
                    # quoting link-local IPs is still notable.
                getattr(report, level)(
                    f"{rule_id}: {msg.split(': ', 1)[-1] if ': ' in msg else msg} (line {line_no})",
                    rel_path,
                    line_no,
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
    # Domain-aware suppressions are evaluated once per plugin: a plugin that
    # literally claims to be a clipboard helper should not be flagged for
    # reading the clipboard. See `_plugin_claims_clipboard_domain`.
    plugin_is_clipboard_domain = _plugin_claims_clipboard_domain(plugin_path)
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        fence_state = build_fence_state(content)
        content_lines = _split_lines(content)
        # Pre-compute Python docstring line numbers (1-based) for
        # this file. Lines INSIDE a `"""…"""` / `'''…'''` block are
        # documentation, NOT runtime code. Phase 3 rules
        # (RC-02/03/63) fire on prose patterns that are intentionally
        # quoted in docstrings explaining how a CLI flag behaves.
        py_docstring_lines: set[int] = set()
        if rel_path.lower().endswith(".py"):
            in_doc = False
            delim: str | None = None
            for i, ln in enumerate(content_lines):
                j = 0
                while j < len(ln):
                    if not in_doc:
                        if ln.startswith('"""', j):
                            in_doc = True
                            delim = '"""'
                            j += 3
                            continue
                        if ln.startswith("'''", j):
                            in_doc = True
                            delim = "'''"
                            j += 3
                            continue
                        j += 1
                    else:
                        if delim is not None and ln.startswith(delim, j):
                            in_doc = False
                            delim = None
                            j += 3
                            continue
                        j += 1
                if in_doc:
                    py_docstring_lines.add(i + 1)  # 1-based
        for line_no, line in enumerate(content_lines, start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            # v2.48 P-1 — augment hash-anchored self-scan skip with a
            # per-line pattern-source predicate. Suppresses lines that
            # are structurally part of a rule declaration (catalog
            # literal, rule-id-tagged docstring/comment, ALL_CAPS
            # pattern-collection member). When the predicate fires,
            # ALL Phase 3 rules are suppressed for this line.
            if cpv_self_scan_skip_line(rel_path, content_lines, line_no):
                continue
            for rule_id, severity, pattern, msg in PHASE3_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                if has_negation_guard_nearby(content, content.find(line) + m.start()):
                    continue
                # v2.42 — opt-in classifier path for rules with a registered
                # classifier (RC-22, RC-93 in this loop). When active, the
                # classifier subsumes the v2.41 binary guards and can also
                # demote-instead-of-suppress.
                if _CLASSIFIER_ACTIVE and rule_id in ("RC-22", "RC-93"):
                    surrounding = _surrounding_lines(content_lines, line_no - 1, window=4)
                    new_severity, _note = _classifier_decision(
                        rule_id,
                        severity.lower(),
                        line,
                        surrounding,
                        _file_role_from_path(rel_path),
                        rel_path,
                    )
                    if new_severity is None:
                        continue
                    level = effective_severity(new_severity, rel_path, rule_id=rule_id)
                else:
                    # v2.41.0 — per-rule context-aware FP guards (binary).
                    if rule_id == "RC-87" and _rc87_is_semver_context(line, rel_path):
                        continue
                    if rule_id == "RC-93" and _rc93_is_markdown_table_row(line):
                        continue
                    # GENERAL FP: RC-92 CSS-hidden injection fires on
                    # the pattern `<div|span style="display:none …">`
                    # but the threat model is HIDDEN INSTRUCTION TEXT
                    # for the LLM — text the human user can't see but
                    # the LLM/scraper extracts. An EMPTY placeholder
                    # `<div ... style="display:none"></div>` (no text
                    # content) is standard HTML for elements that JS
                    # toggles via `display: block`. No instruction text
                    # → no LLM-injection threat.
                    #
                    # Detection: the matched element closes IMMEDIATELY
                    # (`></div>`, `></span>`) on the same line OR the
                    # next line, with no text content between tags.
                    if rule_id == "RC-92" and "display:" in line.lower():
                        # Check if the element is empty (closes on same
                        # line with no text content, or the same-line
                        # tag has no inner text).
                        empty_element_re = re.compile(
                            r"<(div|span)\s+[^>]*>\s*</\1>",
                            re.IGNORECASE,
                        )
                        # Self-closing or empty same-line tag
                        if empty_element_re.search(line):
                            continue
                    # v2.46 FP-O extended — RC-93 visual-deception only
                    # makes sense on AI-instruction surfaces. Python /
                    # JS / TS / Go / Rust / shell source code uses long
                    # whitespace runs for column-alignment of comments
                    # and dict keys, not deception. Reuse the same
                    # source-file gate that RC-76 uses.
                    if rule_id == "RC-93" and _rc76_is_source_code_file(rel_path):
                        continue
                    if rule_id == "RC-22" and plugin_is_clipboard_domain:
                        continue
                    # v2.46 FP-F — RC-31 unpinned action — the regex matches
                    # `uses: foo@master` in YAML, but commented-out example
                    # blocks (`#       - uses: foo@master`) are not live
                    # workflow steps. Skip when the line is a YAML comment.
                    if rule_id == "RC-31" and line.lstrip().startswith("#"):
                        continue
                    # v2.46 FP-E — RC-40/41/42 (`>>` redirects to ssh
                    # authorized_keys / .git/hooks / Dockerfile) are
                    # CRITICAL when written as live shell commands but
                    # FP-by-construction when matched inside a Python
                    # `print()`/`cprint()` f-string that is REPORTING on
                    # a copy that already happened (not a redirect). The
                    # match in `cprint(f"... -> .git/hooks/pre-push")`
                    # finds `> .git/hooks/...` because `->` includes a
                    # `>`. Apply the existing Python-string-context
                    # detector to Python source.
                    if (
                        rule_id in ("RC-40", "RC-41", "RC-42")
                        and rel_path.lower().endswith(".py")
                        and _is_python_string_context(line.strip())
                    ):
                        continue
                    # v2.46 FP-N — RC-02/RC-03 prose-conditional /
                    # coercive-authority detection should not fire on
                    # user-facing hint strings inside a Python list
                    # literal `["- If you see X, ensure Y", ...]`. The
                    # regex matches `if you see X` followed by any
                    # word starting with `do`/`then`/etc. — `Docker`
                    # starts with `Do` so the rule fires on plain
                    # English help text. Skip Python string contexts
                    # for these prose-injection rules. (Real prompt
                    # injection lives in markdown agent/skill bodies,
                    # not in Python source.)
                    if (
                        rule_id in ("RC-02", "RC-03")
                        and rel_path.lower().endswith(".py")
                        and (_is_python_string_context(line.strip()) or line_no in py_docstring_lines)
                    ):
                        continue
                    # v2.48 P2 — RC-02 prose-conditional inside a markdown
                    # documentation section is describing orchestrator
                    # procedure flow, NOT an attack-style time-bomb.
                    # Predicate: file is .md AND a preceding heading
                    # within ≤30 lines contains a doc-role stem
                    # (procedure / phase / step / algorithm / template /
                    # etc.). Real prompt-injection lives in agent bodies
                    # without doc-role headings.
                    if rule_id == "RC-02" and _rc02_is_md_doc_role_section(
                        rel_path,
                        content_lines,
                        line_no - 1,
                    ):
                        continue
                    # v2.46 FP-D — RC-63 fires on the literal phrase
                    # "Skip confirmation" inside CLI flag declarations like
                    # `parser.add_argument("--force", help="Skip confirmation
                    # prompt")` and inside USAGE example comments like
                    # `# Overwrite an existing plugin (skip confirmation)`.
                    # Both are documenting the EXISTENCE of a `--force` flag,
                    # not invoking it autonomously. Skip when the matched
                    # line:
                    #   1. Contains argparse `add_argument`/`add_option` /
                    #      Click `option`/`argument` / Typer `Option`
                    #      declarations
                    #   2. Is inside a `help=`, `description=`, or `usage=`
                    #      kwarg literal
                    #   3. Is a Python comment line `^\s*#`
                    #   4. Is inside a triple-quoted Python docstring
                    #      (best-effort detection: line is inside or after
                    #      a `"""` opener within the same physical line OR
                    #      surrounded by `"""`)
                    # We check the simpler conditions only, matching what the
                    # cluster of FPs in the wild looks like.
                    if rule_id == "RC-63":
                        if any(
                            api in line
                            for api in (
                                "add_argument(",
                                "add_option(",
                                "click.option(",
                                "click.argument(",
                                "typer.option(",
                                "typer.argument(",
                            )
                        ):
                            continue
                        if any(
                            kw in line
                            for kw in (
                                "help=",
                                "description=",
                                "usage=",
                                "epilog=",
                                "metavar=",
                            )
                        ):
                            continue
                        if line.lstrip().startswith("#"):
                            continue
                        # GENERAL: line is inside a Python `"""…"""`
                        # docstring. Module/function docstrings are
                        # documentation — they're allowed to QUOTE the
                        # `--force` flag's behavior without that being
                        # the script autonomously skipping
                        # confirmation. The dedicated docstring-line
                        # tracker handles all docstring shapes (top-
                        # level module, class, function, multi-line
                        # block).
                        if line_no in py_docstring_lines:
                            continue
                        # Best-effort: heavily-indented docstring-mid-block
                        # line ("    --force         Skip confirmation
                        # prompt") — covers cases where the docstring
                        # tracker missed (e.g. file with no module
                        # docstring but inline help-text strings).
                        if line.startswith("    ") and re.search(r"--[a-z][a-z0-9-]+", line, re.IGNORECASE):
                            continue
                        # v2.46 FP-D — also skip markdown table rows that
                        # document a `--force`/`--yes` flag (e.g.
                        # `| --force | No | Skip confirmation prompt |`).
                        # Reuse the existing markdown-table helper.
                        if _rc93_is_markdown_table_row(line) and re.search(r"--[a-z][a-z0-9-]+", line, re.IGNORECASE):
                            continue
                        # v2.48 P1 — markdown bullet inside an anti-pattern
                        # / DO-NOT block describes a behaviour the persona
                        # DOES NOT exhibit, NOT a directive instructing
                        # the agent. Predicate: file is .md AND line is a
                        # bullet AND surrounding context contains a
                        # negation-marker stem. See
                        # `_rc63_is_markdown_anti_pattern_bullet`.
                        if _rc63_is_markdown_anti_pattern_bullet(
                            rel_path,
                            content_lines,
                            line_no - 1,
                        ):
                            continue
                    level = effective_severity(severity.lower(), rel_path, rule_id=rule_id)
                # v2.45 FP8 — RC-87 in CHANGELOG / HISTORY / NEWS /
                # README is project narrative, never live config.
                # Demote one extra tier (see Phase 4 site for rationale).
                if rule_id == "RC-87" and _rc87_is_history_doc(rel_path):
                    if level == "minor":
                        level = "nit"
                    elif level == "warning":
                        level = "nit"
                    elif level == "major":
                        level = "minor"
                getattr(report, level)(
                    f"{rule_id}: {msg.split(': ', 1)[-1] if ': ' in msg else msg} (line {line_no})",
                    rel_path,
                    line_no,
                )
                issues += 1
                # Keep going — multiple Phase 3 rules can match a single line

    # RC-30 typosquatting + RC-33 compromised packages from manifests.
    # `rglob` walks the entire tree including dependency / build / cache
    # directories that the plugin doesn't own — `node_modules/` is the
    # dominant FP source because every transitive dep ships its own
    # package.json, none of which represent the PLUGIN's declared
    # deps. Same for Python virtualenvs, vendored deps, and pnpm/yarn
    # stores. Filter those paths out so RC-30/RC-33 only ever look at
    # manifests the plugin author actually maintains.
    _RC30_SKIP_DIR_PARTS = {
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "site-packages",
        "__pycache__",
        ".pnpm-store",
        ".yarn",
        "vendor",
        "dist",
        "build",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".git",
    }
    for manifest_path in list(plugin_path.rglob("package.json")) + list(plugin_path.rglob("requirements*.txt")):
        try:
            rel_parts = manifest_path.relative_to(plugin_path).parts
        except ValueError:
            continue
        if any(part in _RC30_SKIP_DIR_PARTS for part in rel_parts):
            continue
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
                        rel,
                        0,
                    )
                    issues += 1
                is_squat, target = is_typosquat(dep_name, ecosystem)
                if is_squat:
                    report.major(
                        f"RC-30: dependency '{dep_name}' is Levenshtein ≤1 from top-100 package '{target}' "
                        f"(possible typosquat)",
                        rel,
                        0,
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
                        f"RC-33: dependency '{name}' is in the compromised-package list",
                        rel,
                        0,
                    )
                    issues += 1
                is_squat, target = is_typosquat(name, "pypi")
                if is_squat:
                    report.major(
                        f"RC-30: dependency '{name}' is Levenshtein ≤1 from top-100 package '{target}' "
                        f"(possible typosquat)",
                        rel,
                        0,
                    )
                    issues += 1
    return issues


def check_phase2e_extras(plugin_path: Path, report: ValidationReport) -> int:
    """RC-65 (cloud IMDS), RC-39 (persistence), RC-70 (obfuscated exec)."""
    issues = 0
    for _file_path, rel_path, content in _iter_scannable_files(plugin_path):
        fence_state = build_fence_state(content)
        content_lines = _split_lines(content)

        # RC-65 — Cloud IMDS endpoints (with encoding variants)
        for line_no, line in enumerate(content_lines, start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            # v2.48 P-1 — pattern-source predicate.
            if cpv_self_scan_skip_line(rel_path, content_lines, line_no):
                continue
            for pattern in CLOUD_IMDS_PATTERNS:
                m = pattern.search(line)
                if m and not has_negation_guard_nearby(content, content.find(line) + m.start()):
                    surrounding = _surrounding_lines(content_lines, line_no - 1, window=4)
                    # v2.42 — opt-in classifier path. The classifier
                    # re-uses `_rc65_is_pattern_source` semantics under
                    # the hood and adds the four-tier verdict ladder.
                    if _CLASSIFIER_ACTIVE:
                        new_severity, _note = _classifier_decision(
                            "RC-65",
                            "major",
                            line,
                            surrounding,
                            _file_role_from_path(rel_path),
                            rel_path,
                        )
                        if new_severity is None:
                            break
                        level = effective_severity(new_severity, rel_path)
                    else:
                        # v2.41.0 binary guard: denylist set definition is FP.
                        if _rc65_is_pattern_source(line, surrounding):
                            break
                        level = effective_severity("major", rel_path)
                    getattr(report, level)(
                        f"RC-65: cloud IMDS endpoint at line {line_no}: {m.group(0)}",
                        rel_path,
                        line_no,
                    )
                    issues += 1
                    break

        # RC-39 — Persistence
        content_lines_persistence = _split_lines(content)
        for line_no, line in enumerate(content_lines_persistence, start=1):
            if is_in_fenced_code_block(line_no - 1, fence_state):
                continue
            # v2.48 P-1 — pattern-source predicate.
            if cpv_self_scan_skip_line(rel_path, content_lines_persistence, line_no):
                continue
            for pattern in PERSISTENCE_PATTERNS:
                m = pattern.search(line)
                if m and not has_negation_guard_nearby(content, content.find(line) + m.start()):
                    level = effective_severity("major", rel_path)
                    getattr(report, level)(
                        f"RC-39: persistence pattern at line {line_no}: {m.group(0)[:80]}",
                        rel_path,
                        line_no,
                    )
                    issues += 1
                    break

        # RC-70 — Generic obfuscation with proximity-to-exec
        for line_no, msg in find_obfuscated_exec(content, proximity_lines=3):
            level = effective_severity("critical", rel_path)
            getattr(report, level)(f"RC-70: {msg}", rel_path, line_no)
            issues += 1

        # RC-68 — Multi-layer encoding decoder (TRDD-0f1f7889 gap-fill).
        # Runs at WARNING per TRDD §7 and only fires when the recursively-
        # decoded payload reveals an exec/eval/shell sink. The check is
        # complementary to RC-70 (proximity-to-exec): RC-70 finds a single
        # decoder near a sink; RC-68 finds a sink HIDDEN INSIDE the literal
        # itself after recursive decoding. Both can co-fire on the same line.
        for line_no, layers, sink_match in detect_multilayer_encoded_payload(content):
            if cpv_self_scan_skip_line(rel_path, content_lines, line_no):
                continue
            level = effective_severity("warning", rel_path)
            getattr(report, level)(
                f"RC-68: multi-layer encoded payload at line {line_no}: "
                f"sink revealed at decode-depth {layers} ({sink_match!r})",
                rel_path,
                line_no,
            )
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
    # v2.48 — explicit --concurrency leverages trufflehog's internal goroutine
    # pool. Default is 8; we request `os.cpu_count() or 4` so dedicated 12+
    # core machines see the full benefit. This is the parallelism win that
    # gitleaks (removed in v2.48) could not provide — gitleaks crashed under
    # parallel scans, while trufflehog uses goroutines safely by design.
    truffle_concurrency = max(1, (os.cpu_count() or 4))
    try:
        result = subprocess.run(
            [
                "trufflehog",
                "filesystem",
                str(plugin_path),
                "--json",
                "--no-update",
                "--fail",
                f"--concurrency={truffle_concurrency}",
            ],
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
        # Always-skip leftover scan artifacts first (Cisco scan dump,
        # CPV integrity manifest) — they quote every pattern verbatim.
        if _is_always_skip_basename(rel):
            continue
        if cpv_self_scan_skip(rel):
            continue
        # v2.43 — drop findings inside vendored / cached / build dirs
        # (node_modules, .venv, site-packages, …). These are transitive
        # deps' bundled tests / READMEs / typedef files that contain
        # placeholder credentials by design — not the plugin author's
        # responsibility.
        if _is_vendored_dep_path(rel):
            continue
        # v2.44 — drop findings inside gitignored dev-scratch dirs
        # (docs_dev/, reports/, scripts_dev/, design/tasks/, …). Those
        # paths legitimately quote credentials as fixture / audit
        # examples and are never shipped, so any external-scanner hit
        # in them is operational noise, not a security finding.
        if _is_dev_scratch_path(rel):
            continue
        # Test-file skip — fixture tokens like `const FAKE = "ghs_..."`
        # in `test_*.py` / `*.test.ts` / `tests/fixtures/...` exist by
        # construction. The in-process secret scanners already early-
        # exit on _is_test_file_path; aligning trufflehog with that
        # contract eliminates the matching FPs here too.
        if _is_test_file_path(rel):
            continue
        # FP-corpus markdown skip (file-level, requires both
        # directory shape AND in-file marker — coincidental
        # `fixtures/` markdown without a corpus marker is NOT
        # skipped).
        try:
            fpath = plugin_path / rel
            if fpath.is_file() and fpath.stat().st_size < 2_000_000:
                body = fpath.read_text(encoding="utf-8", errors="ignore")
                if is_fp_corpus_markdown(rel, body):
                    continue
        except OSError:
            pass
        base_level = "critical" if verified else "major"
        level = effective_severity(base_level, rel)
        getattr(report, level)(
            f"trufflehog {'VERIFIED' if verified else 'UNVERIFIED'} secret: detector={detector}",
            rel,
            line_no,
        )
        issues += 1

    if issues == 0 and result.returncode == 0:
        report.passed("trufflehog: no findings (700+ verified-secret detectors clean)")
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
            [
                "semgrep",
                "--config",
                "p/security-audit",
                "--config",
                "p/secrets",
                "--json",
                "--quiet",
                "--no-rewrite-rule-ids",
                "--metrics",
                "off",
                str(plugin_path),
            ],
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
        if _is_always_skip_basename(rel):
            continue
        if cpv_self_scan_skip(rel):
            continue
        # v2.43 — drop findings inside vendored / cached / build dirs.
        if _is_vendored_dep_path(rel):
            continue
        # v2.44 — drop findings inside gitignored dev-scratch dirs
        # (docs_dev/, reports/, scripts_dev/, design/tasks/, …) so
        # private workspace content never triggers semgrep noise.
        if _is_dev_scratch_path(rel):
            continue
        # Drop findings inside test files. Test bodies routinely use
        # the very tokens semgrep flags (`subprocess.run(shell=True, …)`
        # in `test_*.py` to assert detection); the in-process scanners
        # already early-exit on _is_test_file_path, this aligns
        # semgrep with that contract.
        if _is_test_file_path(rel):
            continue
        # FP-corpus markdown skip (file-level, requires both directory
        # shape AND in-file marker) so corpus exemplars don't fire.
        try:
            fpath = plugin_path / rel
            if fpath.is_file() and fpath.stat().st_size < 2_000_000:
                body = fpath.read_text(encoding="utf-8", errors="ignore")
                if is_fp_corpus_markdown(rel, body):
                    continue
                # Per-line catalog/docstring/comment pattern-source skip.
                if isinstance(line_no, int) and line_no > 0 and cpv_self_scan_skip_line(rel, body, int(line_no)):
                    continue
        except OSError:
            pass
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
    enable_semgrep: bool = True,
    with_classifier: bool = False,
    with_extreme: bool = False,
    cache: ScannerCache | None = None,
) -> ValidationReport:
    """Run all security validations on a plugin directory.

    This function performs comprehensive security analysis including:
    Traditional: injection, path traversal, secrets, user paths, dangerous files, permissions
    AI-specific: prompt injection, data exfiltration, supply chain, credential harvest,
    sandbox escape, hook abuse, MCP abuse, agent impersonation, permission escalation
    Phase 1-4 net-new: ~75 RC-NN rules (unicode, MCP, persistence, exfil, evasion, etc.)
    External: cc-audit (npx), tirith (PATH/docker/nix), trufflehog, semgrep, Cisco

    Args:
        plugin_path: Path to the plugin directory
        enable_tirith: Internal-only test isolation knob. The CLI no
            longer exposes a `--no-tirith` opt-out — external scanners
            run unconditionally and self-skip when their source binary
            cannot be resolved. Tests pass `False` here for hermetic
            unit isolation; production callers should leave it `True`.
        enable_trufflehog: Internal-only test isolation knob; same
            contract as `enable_tirith` (no CLI opt-out, scanner runs
            unconditionally and self-skips on absent binary).
        enable_semgrep: Internal-only test isolation knob (same).
        with_classifier: When True, route every finding for rules with a
            registered classifier (RC-21/22/65/87/93 in v2.42) through the
            context-aware classifier in `cpv_fp_classifier`. The classifier
            can demote (LIKELY_FP → one severity tier down) or suppress
            (DEFINITE_FP → not reported). Off by default — gives the legacy
            v2.41 binary-guard behaviour. See TRDD-fe006962.
        with_extreme: When True, classifier verdicts of `DEFINITE_TP`
            promote the declared severity one tier (e.g. MAJOR → CRITICAL).
            Currently used by RC-21 (copy-then-exfil-sink pattern) and
            RC-65 (IMDS literal in same-line network call) — both
            high-confidence credential / instance-metadata exfiltration
            signals with no observed benign reading. Off by default
            because escalation can only inflate findings; explicit
            opt-in keeps the rollout safe. Implies `with_classifier=True`
            (silently — escalation lives on the classifier path; an
            extreme-only call without the classifier is a no-op).
            See TRDD-fe006962 §Step 4.
        cache: Phase D scanner-result cache. When ``None`` (default), a
            ``ScannerCache`` against the user's home cache directory is
            constructed. The cache only wraps the four EXTERNAL tree-
            level scanners (cc-audit, tirith, trufflehog, semgrep) —
            the in-process pattern checks rebuild every run because
            their cost is dominated by file IO, which the cache cannot
            elide without bypassing the freshness check itself. Tests
            can pass an isolated cache via
            ``ScannerCache(cache_dir=tmp_path / "cache")``.

    Returns:
        ValidationReport with all security findings
    """
    report = ValidationReport()
    _reset_scan_step_log()
    # Phase D — default to a real on-disk scanner-result cache. Tests
    # that want isolation pass their own ``ScannerCache(cache_dir=...)``.
    if cache is None:
        cache = ScannerCache()
    # Make the classifier flag and the plugin-meta dict visible to the
    # phase-specific scan helpers via module-level globals — same pattern
    # the self-scan flag uses, so the overhead per finding stays in the
    # check rather than crossing every function boundary.
    _set_classifier_active(with_classifier, plugin_path, with_extreme=with_extreme)

    # v2.48 — silent autoinstall of fclones (the dedup helper used by Phase
    # 4's marketplace bulk scanner). Idempotent: if fclones is already on
    # PATH this is essentially a no-op shutil.which() probe. The first-run
    # autoinstall happens here so users never have to think about it
    # (`brew install fclones` on macOS, `snap install fclones` on Linux,
    # GitHub-release download on Windows). Set CPV_NO_FCLONES_INSTALL=1 in
    # the environment to skip the autoinstall — CPV degrades gracefully
    # without dedup. The actual fclones invocation happens inside
    # `cpv_dedup.run_fclones()` which is called by the marketplace
    # orchestrator (Phase 4) and the per-plugin staging pipeline.
    #
    # NOTE: pytest sets PYTEST_CURRENT_TEST as an env var while a test runs.
    # Skip the autoinstall under pytest to avoid interactions with tests
    # that monkeypatch ``shutil.which`` / ``subprocess.run`` globally.
    # The test suite still exercises ensure_fclones() directly via
    # test_cpv_install_scanners.py.
    if not os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("CPV_NO_FCLONES_INSTALL"):
        try:
            from cpv_install_scanners import ensure_fclones  # noqa: PLC0415

            ensure_fclones()
        except (ImportError, OSError, RuntimeError):
            # Defensive: never let the install module raise into the scan path.
            pass

    # Verify plugin path exists
    if not plugin_path.exists():
        report.critical(f"Plugin path does not exist: {plugin_path}")
        _record_step(0, "Validate target path", "FAILED", details=f"Path does not exist: {plugin_path}")
        return report

    if not plugin_path.is_dir():
        report.critical(f"Plugin path is not a directory: {plugin_path}")
        _record_step(0, "Validate target path", "FAILED", details=f"Path is not a directory: {plugin_path}")
        return report

    # Detect whether the target IS the CPV plugin itself (any deployment
    # location). When True, per-file scanners skip CPV's own pattern-defining
    # source (validator scripts, fix-validation references, security-test
    # fixtures) — those files necessarily contain every detection pattern
    # CPV knows about, and scanning them always self-matches.
    #
    # The skip is gated by SHA256 verification against `.plugin-self-hashes.json`
    # (or the legacy `.cpv-self-hashes.json` for one release) so name-based
    # spoofing cannot evade the scan, and tampering with the validator source
    # itself shows up as a "modified, scanning normally" warning rather than
    # a silent skip.
    self_scan = is_cpv_self_scan(plugin_path)
    _set_cpv_self_scan(self_scan, plugin_root=plugin_path, notice_report=report)
    if self_scan:
        report.info(
            "CPV self-scan mode active — skipping CPV-internal pattern-defining "
            "source (validator scripts, fix-validation references, security tests) "
            "after SHA256 verification against .plugin-self-hashes.json. Files that "
            "match the name pattern but fail hash check (modified or spoofed) are "
            "scanned normally."
        )

    report.info(f"Starting security scan of: {plugin_path}")

    # --- Traditional checks ---

    # Check 1: Dangerous files (quick check first)
    dangerous_count = check_dangerous_files(plugin_path, report)
    if dangerous_count == 0:
        report.passed("No dangerous files detected")
    _record_step(
        1,
        "Dangerous file detection",
        "COMPLETED",
        findings=dangerous_count,
        files=".env / credentials.json / .ssh/ etc.",
    )

    # Check 2: Script permissions
    permission_issues = check_script_permissions(plugin_path, report)
    if permission_issues == 0:
        report.passed("All scripts have proper permissions")
    _record_step(2, "Script permission check", "COMPLETED", findings=permission_issues, files="*.sh / *.py executables")

    # Check 3-11: Full content scan (traditional + AI-specific)
    scan_stats = scan_all_files(plugin_path, report)

    # Check 3b: IDE config files (.vscode, .idea, .cursor, .zed).
    # These live in hidden directories that gi.walk() skips by default, so
    # scan_all_files never sees them. Running a targeted pass ensures API
    # keys / tokens leaked into IDE task runners or MCP configs are caught.
    ide_stats = scan_ide_config_files(plugin_path, report)
    scan_stats["secret_issues"] += ide_stats["secret_issues"]

    # Report scan statistics
    files_scanned = scan_stats["files_scanned"]
    files_skipped = scan_stats["files_skipped"]
    oversize_skipped = scan_stats.get("oversize_skipped", 0)
    files_summary = (
        f"{files_scanned} scanned, {files_skipped} skipped"
        + (f" ({oversize_skipped} oversize > {MAX_SCAN_BYTES // (1024 * 1024)} MiB)" if oversize_skipped else "")
        + f"; +{ide_stats['files_scanned']} IDE configs scanned"
        + (f", {ide_stats['files_skipped']} skipped" if ide_stats["files_skipped"] else "")
    )
    report.info(f"Scanned {files_scanned} files, skipped {files_skipped} ({files_summary})")

    # Add passed messages for clean traditional categories
    if scan_stats["injection_issues"] == 0:
        report.passed("No injection patterns detected")
    if scan_stats["path_traversal_issues"] == 0:
        report.passed("No path traversal patterns detected")
    if scan_stats["secret_issues"] == 0:
        report.passed("No secrets detected")
    if scan_stats["user_path_issues"] == 0:
        report.passed("No hardcoded user paths detected")

    # Record per-scanner step status. scan_all_files dispatches to 9
    # scanners on the same content; we report each as its own step so
    # the operator sees coverage per category.
    _record_step(3, "Injection scan", "COMPLETED", findings=scan_stats["injection_issues"], files=files_summary)
    _record_step(
        4, "Path-traversal scan", "COMPLETED", findings=scan_stats["path_traversal_issues"], files=files_summary
    )
    _record_step(5, "Secret scan", "COMPLETED", findings=scan_stats["secret_issues"], files=files_summary)
    _record_step(6, "User-path scan", "COMPLETED", findings=scan_stats["user_path_issues"], files=files_summary)
    _record_step(
        7, "Prompt-injection scan", "COMPLETED", findings=scan_stats["prompt_injection_issues"], files=files_summary
    )
    _record_step(
        8, "Data-exfiltration scan", "COMPLETED", findings=scan_stats["exfiltration_issues"], files=files_summary
    )
    _record_step(9, "Supply-chain scan", "COMPLETED", findings=scan_stats["supply_chain_issues"], files=files_summary)
    _record_step(
        10,
        "Credential-harvest scan",
        "COMPLETED",
        findings=scan_stats["credential_harvest_issues"],
        files=files_summary,
    )
    _record_step(
        11, "Sandbox-escape scan", "COMPLETED", findings=scan_stats["sandbox_escape_issues"], files=files_summary
    )
    _record_step(
        12,
        "IDE-config scan",
        "COMPLETED",
        findings=ide_stats["secret_issues"],
        files=f"{ide_stats['files_scanned']} scanned, {ide_stats['files_skipped']} skipped",
    )

    # --- AI-specific file-level checks ---

    # Check 13: Hook abuse (external URLs, supply chain in hooks)
    hook_issues = check_hook_abuse(plugin_path, report)
    if hook_issues == 0:
        report.passed("No hook abuse patterns detected")
    _record_step(13, "Hook-abuse scan", "COMPLETED", findings=hook_issues, files="hooks/*.json + .json hook files")

    # Check 14: MCP server abuse (non-localhost connections)
    mcp_issues = check_mcp_abuse(plugin_path, report)
    if mcp_issues == 0:
        report.passed("No MCP server abuse detected")
    _record_step(
        14, "MCP-server-abuse scan", "COMPLETED", findings=mcp_issues, files=".mcp.json + plugin.json mcpServers"
    )

    # Check 15: Permission escalation (overly broad permissions)
    escalation_issues = check_permission_escalation(plugin_path, report)
    if escalation_issues == 0:
        report.passed("No permission escalation detected")
    _record_step(
        15,
        "Permission-escalation scan",
        "COMPLETED",
        findings=escalation_issues,
        files="settings.json + plugin.json permissions",
    )

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
    _record_step(
        16,
        "Phase 1 — critical RC rules",
        "COMPLETED",
        findings=phase1_issues,
        files="RC-09/10/11/21/29/37/43/47/49/50/67",
    )

    # --- Phase 2e extras — Cloud IMDS, persistence, obfuscated decode-then-exec ---
    phase2e_issues = check_phase2e_extras(plugin_path, report)
    if phase2e_issues == 0:
        report.passed("No Phase 2e extras findings (RC-39 persistence, RC-65 cloud IMDS, RC-70 obfuscated exec)")
    _record_step(
        17,
        "Phase 2e — extras",
        "COMPLETED",
        findings=phase2e_issues,
        files="RC-39 persistence, RC-65 IMDS, RC-70 obfuscated-exec",
    )

    # --- Phase 3 — ~30 MAJOR net-new rules ---
    phase3_issues = check_phase3_all(plugin_path, report)
    if phase3_issues == 0:
        report.passed("No Phase 3 findings (~30 MAJOR net-new rules)")
    _record_step(18, "Phase 3 — ~30 MAJOR RC rules", "COMPLETED", findings=phase3_issues, files="~30 MAJOR rules")

    # --- Phase 4 — Minor / informational + verdict-tier (RC-85/86/87/88/103/104) ---
    phase4_issues = check_phase4_all(plugin_path, report)
    if phase4_issues == 0:
        report.passed("No Phase 4 findings (minor/info + observability)")
    _record_step(
        19, "Phase 4 — minor + observability", "COMPLETED", findings=phase4_issues, files="RC-85/86/87/88/103/104"
    )

    # --- Phase 9 — RC-76 stemmed semantic injection classifier ---
    phase9_issues = check_phase9_stemmed_injection(plugin_path, report)
    if phase9_issues == 0:
        report.passed("No Phase 9 findings (RC-76 stemmed semantic injection)")
    _record_step(20, "Phase 9 — stemmed semantic injection", "COMPLETED", findings=phase9_issues, files="RC-76")

    # --- Phase 10 — RC-73/74/75 AST-based Python taint engine ---
    phase10_issues = check_phase10_taint(plugin_path, report)
    if phase10_issues == 0:
        report.passed("No Phase 10 findings (RC-73/74/75 taint source→sink)")
    _record_step(
        21, "Phase 10 — Python taint engine", "COMPLETED", findings=phase10_issues, files="RC-73/74/75 source-sink"
    )

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

    # --- External scanners (always run; each self-skips on absent source) ---
    #
    # The CLI no longer exposes opt-out flags. Every external scanner is
    # invoked unconditionally; each `check_*` function gracefully degrades
    # to an INFO advisory ("scanner X unavailable: <reason>") when its
    # binary cannot be resolved on PATH or installed from its source URL.
    # The `enable_*` keyword arguments survive only as test-isolation
    # knobs for hermetic unit tests — production callers pass True.

    # Phase B (v2.76.0) — run cc-audit, tirith, trufflehog, semgrep in
    # parallel. Each scanner is a long subprocess (npx download +
    # network-bound npm pull, docker pull, full-tree filesystem scan,
    # registry config download); even on a 4-core machine running them
    # concurrently shaves ~50–70% off the wall clock when more than
    # one scanner is installed. Each scanner self-times-out (180s for
    # trufflehog, 300s for semgrep, internal timeouts for the rest),
    # so concurrent execution does not change the worst-case latency.
    #
    # Output ordering rule: declaration order (cc-audit → tirith →
    # trufflehog → semgrep) is preserved when merging per-task results
    # and per-task `_record_step` records back into the global report
    # and step log. Completion order is irrelevant to the user-facing
    # output.
    #
    # IMPORTANT — no `contextlib.redirect_stdout` inside the thread
    # tasks. ``redirect_stdout`` mutates the process-global
    # ``sys.stdout`` reference: with N concurrent threads the last one
    # to exit may restore a stale per-thread buffer instead of the
    # real stdout, swallowing every subsequent write made by the main
    # thread. All four scanners route their output through
    # ``report.X(...)`` and ``capture_output=True`` subprocess calls,
    # so there is no inner ``print()`` to capture.
    #
    # Phase D (v2.78.0) — wrap each scanner with a content-hash cache
    # lookup. The cache key is built from a tree merkle of every file
    # the scanner would read (the gitignore-filtered tree under
    # plugin_path). On a hit we replay the cached findings into the
    # local report and skip the subprocess entirely. On a miss the
    # scanner runs normally and its findings are serialised into the
    # cache for the next warm run. The merkle is computed ONCE per
    # validate_security() call and shared across all four scanners,
    # so the per-scanner cache lookup is O(1).
    _tree_merkle_cache: dict[str, str] = {}

    def _compute_tree_merkle() -> str:
        """Return the gitignore-filtered tree merkle for plugin_path.

        Memoised across the four scanner cache lookups inside this
        single ``validate_security`` invocation. The merkle stays
        valid for the duration of the call because the scanners are
        read-only.
        """
        if "merkle" in _tree_merkle_cache:
            return _tree_merkle_cache["merkle"]
        gi = get_gitignore_filter(plugin_path)
        files: list[Path] = []
        for dirpath_s, _dirs, filenames in gi.walk():
            dp = Path(dirpath_s) if isinstance(dirpath_s, str) else dirpath_s
            for fn in filenames:
                fp = dp / fn
                # Skip cache files themselves (.cc-audit.yaml is
                # auto-generated per scan; including it in the
                # merkle would break the cache on every run).
                if fp.name.startswith(".cc-audit") or fp.name == ".cpv-self-hashes.json":
                    continue
                files.append(fp)
        merkle = tree_merkle(files, base=plugin_path)
        _tree_merkle_cache["merkle"] = merkle
        return merkle

    def _run_scanner_with_cache(
        scanner_name: str,
        scanner_fn: Any,
        scanner_argv: list[str],
        local_report: ValidationReport,
    ) -> int:
        """Cache-wrap a tree-level security scanner call.

        Looks up a (tree-merkle, scanner-name, scanner-version, args)
        cache entry. On hit, replays the cached findings into
        ``local_report`` and returns the cached findings count. On
        miss, invokes ``scanner_fn(plugin_path, local_report)`` and
        writes the result back to the cache.

        ``scanner_argv`` is the stable identifier of the CLI flags
        this scanner uses — bumping a flag invalidates only this
        scanner's entries. We don't model the actual CLI here
        (each ``check_*`` function builds its own argv inline), so
        ``scanner_argv`` is a curated whitelist that captures the
        flags a developer would change when tuning the scanner.
        """
        merkle = _compute_tree_merkle()
        version = get_scanner_version(scanner_name)
        # Bake plugin_path into the args hash so two plugins with
        # IDENTICAL trees but different on-disk paths still get
        # distinct entries. trufflehog's `--no-update` and similar
        # flags are also baked here — change any flag in
        # scanner_argv and the cache invalidates.
        full_args = [*scanner_argv, str(plugin_path)]
        args_hash = sha256_of_args(full_args)
        key = CacheKey(
            target_id=f"{plugin_path}::sec:{scanner_name}",
            content_sha256=merkle,
            scanner_name=f"cpv-sec:{scanner_name}",
            scanner_version=version,
            args_hash=args_hash,
        )
        cached = cache.get(key)
        if cached is not None and isinstance(cached.get("findings"), list):
            # Cache hit — replay each finding into the local report.
            # The cache also stores the scanner's integer return
            # (the count of findings for the step log).
            for entry in cached["findings"]:
                if not isinstance(entry, dict):
                    continue
                level = entry.get("level")
                msg = entry.get("message")
                if not isinstance(level, str) or not isinstance(msg, str):
                    continue
                # Re-emit through ValidationReport.add() so all the
                # invariants (counts, exit_code) are preserved.
                local_report.add(
                    level=level,  # type: ignore[arg-type]
                    message=msg,
                    file=entry.get("file"),
                    line=entry.get("line"),
                    phase=entry.get("phase"),
                    fixable=bool(entry.get("fixable", False)),
                    fix_id=entry.get("fix_id"),
                )
            return int(cached.get("findings_count", len(cached["findings"])))

        # Cache miss — run the actual scanner and remember the
        # delta between local_report.results before/after so we
        # only cache findings produced by THIS scanner (not any
        # findings the caller may have already pushed in).
        before = len(local_report.results)
        count = scanner_fn(plugin_path, local_report)
        after_results = local_report.results[before:]
        try:
            cache.put(
                key,
                {
                    "findings": [r.to_dict() for r in after_results],
                    "findings_count": int(count),
                    "ts": time.time(),
                },
            )
        except Exception:
            # Cache writes must NEVER affect scanner correctness.
            pass
        return int(count)

    def _task_cc_audit() -> tuple[ValidationReport, list[dict[str, Any]]]:
        """Run cc-audit into a private report; record one step entry.

        Phase D — wraps the actual scanner call with a content-hash
        cache lookup so a warm run skips the ~5-15s `npx` resolve +
        ~30s scan against an unchanged tree.
        """
        local = ValidationReport()
        steps: list[dict[str, Any]] = []
        if shutil.which("npx"):
            cc_count = _run_scanner_with_cache(
                "cc-audit",
                check_cc_audit,
                ["check", "-t", "plugin", "--format", "json", "--ci"],
                local,
            )
            steps.append(
                {
                    "num": 22,
                    "name": "External: cc-audit (100+ AI rules)",
                    "status": "RAN",
                    "findings": cc_count,
                    "files": "npx @cc-audit/cc-audit (auto-fetched)",
                    "details": "",
                }
            )
        else:
            check_cc_audit(plugin_path, local)  # emits WARNING into local
            steps.append(
                {
                    "num": 22,
                    "name": "External: cc-audit (100+ AI rules)",
                    "status": "SKIPPED",
                    "findings": 0,
                    "files": "",
                    "details": "`npx` not on PATH — install Node.js to enable",
                }
            )
        return local, steps

    def _task_tirith() -> tuple[ValidationReport, list[dict[str, Any]]]:
        """Run tirith into a private report; record one step entry.

        Phase D — wraps the actual scanner call with a content-hash
        cache lookup; a warm run elides the docker pull + scan.
        """
        local = ValidationReport()
        steps: list[dict[str, Any]] = []
        if enable_tirith:
            tirith_count = _run_scanner_with_cache(
                "tirith",
                check_tirith_scanner,
                ["scan", "--format", "json"],
                local,
            )
            # Inspect this task's local results (not the global) to
            # derive RAN vs SKIPPED — same predicate the serial
            # version used, just on the per-task report.
            unavail = any(
                "tirith" in (r.message or "").lower()
                and (
                    "not found" in r.message.lower()
                    or "unavailable" in r.message.lower()
                    or "skipped" in r.message.lower()
                )
                for r in local.results
            )
            steps.append(
                {
                    "num": 23,
                    "name": "External: tirith (terminal-security)",
                    "status": "SKIPPED" if unavail else "RAN",
                    "findings": tirith_count,
                    "files": "PATH → docker → nix → auto-install" if not unavail else "",
                    "details": "tirith binary unavailable — see WARNING above" if unavail else "",
                }
            )
        else:
            steps.append(
                {
                    "num": 23,
                    "name": "External: tirith (terminal-security)",
                    "status": "SKIPPED",
                    "findings": 0,
                    "files": "",
                    "details": "enable_tirith=False (test isolation knob)",
                }
            )
        return local, steps

    def _task_specialist(
        step_num: int,
        name: str,
        scanner_fn: Any,
        enabled: bool,
        binary_hint: str,
    ) -> tuple[ValidationReport, list[dict[str, Any]]]:
        """Run a Phase 5 specialist tool (trufflehog / semgrep) into a private report.

        Phase D — wraps the actual scanner call with a content-hash
        cache lookup. Cache key uses ``binary_hint`` as the scanner
        name so the trufflehog and semgrep entries are partitioned
        cleanly.
        """
        local = ValidationReport()
        steps: list[dict[str, Any]] = []
        if not enabled:
            steps.append(
                {
                    "num": step_num,
                    "name": name,
                    "status": "SKIPPED",
                    "findings": 0,
                    "files": "",
                    "details": f"enable_{binary_hint}=False (test isolation knob)",
                }
            )
        elif not shutil.which(binary_hint):
            scanner_fn(plugin_path, local)  # emits WARNING into local
            steps.append(
                {
                    "num": step_num,
                    "name": name,
                    "status": "SKIPPED",
                    "findings": 0,
                    "files": "",
                    "details": f"`{binary_hint}` not on PATH (install via brew/pipx/etc.)",
                }
            )
        else:
            # The scanner_argv stub captures the few flags each scanner
            # uses internally — kept short on purpose: a flag drift
            # inside check_trufflehog or check_semgrep should manually
            # update this list to invalidate the cache. Bumping the
            # scanner binary itself is auto-detected via scanner_version.
            if binary_hint == "trufflehog":
                scanner_argv = ["filesystem", "--json", "--no-update", "--fail"]
            elif binary_hint == "semgrep":
                scanner_argv = [
                    "--config",
                    "p/security-audit",
                    "--config",
                    "p/secrets",
                    "--json",
                    "--quiet",
                ]
            else:
                scanner_argv = []
            count = _run_scanner_with_cache(binary_hint, scanner_fn, scanner_argv, local)
            steps.append(
                {
                    "num": step_num,
                    "name": name,
                    "status": "RAN",
                    "findings": count,
                    "files": f"{binary_hint} (PATH binary)",
                    "details": "",
                }
            )
        return local, steps

    # Declaration order is the canonical replay order — same order the
    # serial version produced before Phase B.
    _scanner_tasks = [
        ("cc-audit", _task_cc_audit),
        ("tirith", _task_tirith),
        (
            "trufflehog",
            lambda: _task_specialist(
                24,
                "External: trufflehog (~700 secret rules)",
                check_trufflehog,
                enable_trufflehog,
                "trufflehog",
            ),
        ),
        (
            "semgrep",
            lambda: _task_specialist(
                25,
                "External: semgrep (p/security-audit)",
                check_semgrep,
                enable_semgrep,
                "semgrep",
            ),
        ),
    ]

    _scanner_results: list[tuple[ValidationReport, list[dict[str, Any]]]] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        # Submit in declaration order; collect futures in the same
        # order so the result list is also in declaration order.
        futures = [ex.submit(fn) for _name, fn in _scanner_tasks]
        for fut in futures:
            _scanner_results.append(fut.result())

    # Merge in declaration order so report.results and the step log
    # look byte-identical to the serial version (subprocesses already
    # captured their own output via capture_output=True; no terminal
    # writes need replaying).
    for local_report, step_records in _scanner_results:
        report.merge(local_report)
        for rec in step_records:
            _record_step(
                rec["num"],
                rec["name"],
                rec["status"],
                findings=rec.get("findings", 0),
                files=rec.get("files", ""),
                details=rec.get("details", ""),
            )

    # Check 27 — Cisco AI Defense skill-scanner via uvx remote.
    # Programmatic-only mode (no API-key engines). Self-skips when uvx
    # is not on PATH or the cisco-ai-skill-scanner package cannot be
    # resolved at its PyPI source URL. See scripts/cpv_skill_scanner.py.
    from cpv_skill_scanner import report_findings, run_cisco_scan  # noqa: PLC0415

    def _cisco_should_skip(file_path: str, line: int | None) -> bool:
        """Apply CPV's full self-scan filter chain to each Cisco finding.

        Without this, Cisco scanning CPV itself would surface CPV's own
        rule catalogs, regex sources, parametrize fixtures, and FP-corpus
        markdown as findings — exactly the noise the in-process scanners
        already filter out via cpv_self_scan_skip / vendored / dev_scratch
        / test_file / fp-corpus / pattern-source-line predicates.
        """
        if not file_path:
            return False
        if _is_always_skip_basename(file_path):
            return True
        if cpv_self_scan_skip(file_path):
            return True
        if _is_vendored_dep_path(file_path):
            return True
        if _is_dev_scratch_path(file_path):
            return True
        if _is_test_file_path(file_path):
            return True
        if isinstance(line, int) and line > 0:
            try:
                fpath = Path(file_path)
                if fpath.is_file() and fpath.stat().st_size < 2_000_000:
                    body = fpath.read_text(encoding="utf-8", errors="ignore")
                    if cpv_self_scan_skip_line(file_path, body, int(line)):
                        return True
                    if is_fp_corpus_markdown(file_path, body):
                        return True
            except OSError:
                pass
        return False

    # v2.48 — prefer the persistent ``skill-scanner`` binary (created by
    # ``uv tool install cisco-ai-skill-scanner``) over the ephemeral uvx
    # resolution. cpv_skill_scanner.build_scan_command() picks the right
    # launcher; here we only need ANY launcher (persistent OR uvx) to be
    # available before we can run the scan.
    if not (shutil.which("skill-scanner") or shutil.which("uvx")):
        # Neither launcher available — record SKIPPED and do not even spawn the run.
        _record_step(
            26,
            "External: Cisco AI Defense (skill-scanner)",
            "SKIPPED",
            details="neither `skill-scanner` nor `uvx` on PATH — "
            "run `cpv-doctor --install-scanners` or "
            "`pip install uv && uv tool install cisco-ai-skill-scanner`",
        )
    else:
        report_len_before = len(report.results)
        cisco_result = run_cisco_scan(plugin_path)
        report_findings(cisco_result, plugin_path, report, should_skip=_cisco_should_skip)
        new_results = report.results[report_len_before:]
        # Detect "uvx package failed to resolve / cisco binary unavailable" via
        # the WARNING messages run_cisco_scan / report_findings emit.
        unavail = any(
            ("cisco" in (r.message or "").lower() or "skill-scanner" in (r.message or "").lower())
            and (
                "unavailable" in r.message.lower()
                or "not found" in r.message.lower()
                or "skipped" in r.message.lower()
                or "failed to resolve" in r.message.lower()
            )
            for r in new_results
        )
        timed_out = any(
            "timeout" in (r.message or "").lower() and "cisco" in (r.message or "").lower() for r in new_results
        )
        cisco_findings = sum(1 for r in new_results if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT"))
        if timed_out:
            status = "FAILED"
            details = "Cisco scanner timed out (override CPV_CISCO_SCAN_TIMEOUT_S)"
        elif unavail:
            status = "SKIPPED"
            details = "Cisco scanner unavailable — see WARNING above"
        else:
            status = "RAN"
            details = ""
        _record_step(
            26,
            "External: Cisco AI Defense (skill-scanner)",
            status,
            findings=cisco_findings,
            files="uvx --from cisco-ai-skill-scanner" if status == "RAN" else "",
            details=details,
        )

    return report


# =============================================================================
# CLI Main
# =============================================================================


def _resolve_report_root() -> Path:
    """Resolve the canonical report root for auto-generated report paths.

    Anchors reports to the **main checkout root** (first entry of
    ``git worktree list``) so they survive when a linked worktree is
    removed/merged. ``./reports/`` is gitignored everywhere; writing
    inside a worktree's local copy loses the audit trail.

    Resolution order:
      1. ``git worktree list | head -1`` — the main checkout root.
         Inside a linked worktree, ``CLAUDE_PROJECT_DIR`` resolves to
         the WORKTREE root (not the main checkout), which is precisely
         the case this primary path defends against.
      2. ``$CLAUDE_PROJECT_DIR`` — fallback when the cwd is not a git
         repo at all (still useful for one-off scans of bare folders
         or skill-only trees).
      3. ``$TMPDIR`` — last resort on a remote ``uvx`` invocation
         where neither git nor ``CLAUDE_PROJECT_DIR`` is usable.

    Mirrors the convention documented in
    ``~/.claude/CLAUDE.md`` and
    ``~/.claude/rules/agent-reports-location.md`` and matches the
    bash prologue every other CPV agent uses
    (``plugin-validator``, ``semantic-validator``,
    ``marketplace-fixer``, ``skill-validation-agent``).
    """
    try:
        completed = subprocess.run(
            ["git", "worktree", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        completed = None
    if completed and completed.returncode == 0 and completed.stdout.strip():
        first_line = completed.stdout.strip().splitlines()[0]
        candidate = first_line.split()[0] if first_line else ""
        if candidate:
            try:
                resolved = Path(candidate).resolve()
                if resolved.is_dir():
                    return resolved
            except OSError:
                pass
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        try:
            return Path(project_dir).resolve()
        except OSError:
            pass
    return Path(tempfile.gettempdir())


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


def _resolve_marketplace_plugins(spec: str) -> tuple[str, list[Path], list[str]]:
    """Resolve a `--marketplace` spec to a list of plugin directories.

    Returns ``(label, plugin_dirs, skipped_reasons)`` where ``label`` is a
    human-readable name for the marketplace, ``plugin_dirs`` is the list
    of plugin root paths to scan, and ``skipped_reasons`` is a list of
    "<plugin-name>: <reason>" lines for entries that could not be located
    (typically: declared in marketplace.json but not present in the local
    cache).

    Supported spec forms:
      - Local path to a marketplace root containing
        ``.claude-plugin/marketplace.json`` — plugins are resolved
        relative to the marketplace root per the manifest's ``source``
        fields. ``relative-path`` entries are joined to the marketplace
        root; entries with non-local sources fall back to cache lookup.
      - Local path to a plugins-cache root (e.g.
        ``~/.claude/plugins/cache/<marketplace-name>/``) — every
        ``<plugin-name>/<latest-version>/`` subdir is treated as a plugin.
      - ``github:owner/repo`` or ``https://github.com/owner/repo`` — the
        marketplace.json is fetched via ``gh api``, plugin entries are
        located in the local cache under
        ``~/.claude/plugins/cache/<repo-basename>/<plugin>/<latest>/``;
        any plugin not present in the cache is added to
        ``skipped_reasons`` and not scanned.
    """
    skipped: list[str] = []
    plugin_dirs: list[Path] = []

    # --- Github URL / shorthand ---------------------------------------
    is_github = spec.startswith("github:") or spec.startswith("https://github.com/")
    if is_github:
        if spec.startswith("github:"):
            slug = spec[len("github:") :].strip("/")
        else:
            slug = spec[len("https://github.com/") :].strip("/")
        # Expect exactly owner/repo
        parts = slug.split("/")
        if len(parts) < 2:
            raise ValueError(f"Bad GitHub marketplace spec: {spec!r}")
        owner, repo = parts[0], parts[1]
        # Fetch marketplace.json via gh api
        completed = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/contents/.claude-plugin/marketplace.json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"gh api failed for {owner}/{repo}: {completed.stderr.strip()[:200]}")
        # Response is a JSON object with base64-encoded content
        meta = json.loads(completed.stdout)
        import base64 as _b64  # noqa: PLC0415

        manifest_text = _b64.b64decode(meta["content"]).decode("utf-8")
        manifest = json.loads(manifest_text)
        cache_root = Path.home() / ".claude" / "plugins" / "cache" / repo
        for entry in manifest.get("plugins", []):
            name = entry.get("name") or entry.get("plugin") or "<unnamed>"
            plugin_cache = cache_root / name
            if not plugin_cache.is_dir():
                skipped.append(f"{name}: not in local cache ({plugin_cache})")
                continue
            # Pick the latest version subdir (lexicographic on semver works
            # well-enough for this use case; ties broken by mtime).
            versions = sorted(
                [v for v in plugin_cache.iterdir() if v.is_dir()],
                key=lambda p: (p.name, p.stat().st_mtime),
            )
            if not versions:
                skipped.append(f"{name}: cache dir has no version subdirs")
                continue
            plugin_dirs.append(versions[-1])
        return f"github:{owner}/{repo}", plugin_dirs, skipped

    # --- Local path: either a marketplace root or a plugins-cache root --
    root = Path(spec).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Marketplace path is not a directory: {root}")

    manifest_path = root / ".claude-plugin" / "marketplace.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest.get("plugins", []):
            name = entry.get("name") or "<unnamed>"
            source = entry.get("source")
            sub_path: Path | None = None
            if isinstance(source, dict) and source.get("type") == "relative-path":
                sub_path = (root / source.get("path", name)).resolve()
            elif source == "./":
                # Layout C — marketplace IS the plugin
                sub_path = root
            elif isinstance(source, str) and not source.startswith(("github", "git", "url:", "npm:")):
                sub_path = (root / source).resolve()
            if sub_path and sub_path.is_dir():
                plugin_dirs.append(sub_path)
            else:
                skipped.append(f"{name}: source not local-resolvable ({source!r})")
        return f"local:{root.name}", plugin_dirs, skipped

    # No marketplace.json — assume plugins-cache layout (`<plugin>/<version>/`)
    for plugin_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        versions = sorted(
            [v for v in plugin_dir.iterdir() if v.is_dir()],
            key=lambda p: (p.name, p.stat().st_mtime),
        )
        if not versions:
            skipped.append(f"{plugin_dir.name}: no version subdirs")
            continue
        plugin_dirs.append(versions[-1])
    return f"cache:{root.name}", plugin_dirs, skipped


def _plugin_label_from_dir(plugin_dir: Path) -> str:
    """Resolve a human-friendly plugin label from a plugin directory.

    For plugins-cache layouts (`<plugin>/<version>/`), `plugin_dir.name`
    is the version (e.g. `2.46.3`) and the plugin name lives one level
    up. For everything else `plugin_dir.name` already IS the plugin name.

    Returns ``"<plugin>@<version>"`` for cache-layout entries and just
    ``"<plugin>"`` otherwise.
    """
    parent = plugin_dir.parent
    # Heuristic: cache layout has a numeric / semver-ish version segment
    # (e.g. "2.46.3", "0.3.9"). If the leaf looks like a version AND the
    # parent has other version subdirs OR the parent matches the plugin
    # name pattern, treat it as cache-layout.
    looks_like_version = bool(re.match(r"^\d+(\.\d+)*([-+].+)?$", plugin_dir.name))
    if looks_like_version and parent.is_dir():
        return f"{parent.name}@{plugin_dir.name}"
    return plugin_dir.name


def _bucket_canonical_findings_into_plugins(
    plugin_reports: dict[str, ValidationReport],
    dedup_map: dict[Path, list[Path]],
    plugin_paths: dict[str, Path],
    original_paths: dict[str, Path],
) -> int:
    """v2.48 Phase 4 — propagate canonical-file findings to all dup members.

    When the corpus dedup deletes plugin2/skills/SKILL.md (because it was
    a duplicate of plugin1's), plugin2's per-plugin scan won't see that
    file. This helper closes the coverage gap: for every finding emitted on
    a canonical file that belonged to a dedup group, copy the finding into
    the report of every plugin that originally contained a member.

    The copy gets its `file` field rewritten to the original (cache) path
    of the duplicate so the user sees a path inside their own plugin, not
    a path inside the staging area.

    Args:
        plugin_reports: ``{safe-plugin-name: ValidationReport}`` — the
            per-plugin reports already populated by ``validate_security``.
        dedup_map: From ``cpv_dedup.run_fclones`` — ``{canonical_path:
            [all_member_paths]}``. Member paths point under the staging
            tree (NOT into the cache).
        plugin_paths: From ``stage_marketplace`` — ``{safe-name:
            staged-plugin-root}``. Used to determine which plugin a member
            path falls under.
        original_paths: From ``stage_marketplace`` — ``{safe-name:
            original-plugin-root}``. Used to rewrite member paths into
            user-visible paths inside the source tree.

    Returns:
        Total number of propagated findings (added across all plugin
        reports). Zero when ``dedup_map`` is empty or none of the
        findings landed on a canonical file.
    """
    if not dedup_map:
        return 0

    # Reverse lookup: which plugin owns each member path?
    # Build {member_str: (safe_name, original_root)} for fast prefix match.
    plugin_by_stage_root: list[tuple[str, str, Path]] = sorted(
        [
            (str(stage_root), safe_name, original_paths.get(safe_name, stage_root))
            for safe_name, stage_root in plugin_paths.items()
        ],
        key=lambda x: -len(x[0]),  # longest prefix wins
    )

    def _owner_of(member_path: Path) -> tuple[str, Path] | None:
        member_str = str(member_path)
        for stage_root_str, safe_name, original_root in plugin_by_stage_root:
            if member_str == stage_root_str or member_str.startswith(stage_root_str + os.sep):
                return safe_name, original_root
        return None

    # Reverse-index: {canonical_path: list[(safe_name, member_path_in_stage,
    # original_root)]} — exclude the canonical itself from propagation.
    canonical_member_owners: dict[Path, list[tuple[str, Path, Path]]] = {}
    for canonical, members in dedup_map.items():
        owners: list[tuple[str, Path, Path]] = []
        for member in members:
            if member == canonical:
                continue
            owner_info = _owner_of(member)
            if owner_info is None:
                continue
            safe_name, original_root = owner_info
            owners.append((safe_name, member, original_root))
        if owners:
            canonical_member_owners[canonical] = owners

    if not canonical_member_owners:
        return 0

    propagated_count = 0
    for safe_name, report in plugin_reports.items():
        # Walk this plugin's findings; for any whose file matches a
        # canonical we tracked, propagate to peer plugins.
        for r in list(report.results):  # snapshot — we may mutate other reports
            if not r.file:
                continue
            try:
                finding_path = Path(r.file)
            except (ValueError, TypeError):
                continue
            owners = canonical_member_owners.get(finding_path) or []
            if not owners:
                continue
            # The finding lives inside `safe_name` (the canonical owner).
            # Propagate to each peer owner.
            stage_root_for_canonical = plugin_paths.get(safe_name)
            if stage_root_for_canonical is None:
                continue
            try:
                rel_inside_canonical = finding_path.relative_to(stage_root_for_canonical)
            except ValueError:
                continue
            for peer_name, _peer_member_path, peer_original_root in owners:
                if peer_name == safe_name:
                    continue
                peer_report = plugin_reports.get(peer_name)
                if peer_report is None:
                    continue
                # Rewrite path: peer_original_root + same relative path.
                peer_path_str = str(peer_original_root / rel_inside_canonical)
                peer_report.results.append(
                    type(r)(
                        level=r.level,
                        message=r.message,
                        file=peer_path_str,
                        line=r.line,
                        phase=r.phase,
                        fixable=r.fixable,
                        fix_id=r.fix_id,
                        category=r.category,
                        suggestion=((r.suggestion or "") + (" [propagated from cross-plugin duplicate]")).strip(),
                    )
                )
                propagated_count += 1
    return propagated_count


def _rewrite_finding_paths_to_original(
    report: ValidationReport,
    staged_root: Path,
    original_root: Path,
) -> None:
    """Rewrite each finding's `file` from the staged path to the original.

    External scanners (trufflehog, semgrep, Cisco) emit absolute paths under
    the staging tree. Internal scanners typically emit relative paths
    (relative to the plugin root) which work for either tree because the
    relative structure is identical. This helper handles both cases:
      * Absolute paths under ``staged_root`` → rewrite to ``original_root``
      * Relative paths or paths outside staging → leave unchanged
    """
    staged_str = str(staged_root)
    original_str = str(original_root)
    for r in report.results:
        if not r.file:
            continue
        if r.file.startswith(staged_str + os.sep):
            r.file = original_str + r.file[len(staged_str) :]
        elif r.file == staged_str:
            r.file = original_str


def _run_marketplace_scan(args: argparse.Namespace) -> int:
    """v2.48 Phase 4 — stage all plugins, dedup once, scan each, bucket back.

    The previous (sequential) implementation ran ``validate_security`` on
    each plugin in turn, paying the per-target startup cost N times for
    every external scanner (cc-audit, trufflehog, semgrep, Cisco). On a
    real marketplace (e.g. ai-maestro-plugins, 30+ plugins, ~1.8K duplicate
    files, ~21 MB cross-plugin redundancy), this dominated wall-clock.

    The new pipeline:
      1. Stage every plugin under one shared tmpdir via hardlinks (zero-copy
         on same-fs; copy-fallback on EXDEV; symlink-fallback for very large
         cross-fs trees with dedup-by-deletion disabled).
      2. Run fclones ONCE on the corpus root → dedup_map of duplicates.
      3. Delete non-canonical hardlinks from staging (safe — the cache
         hardlink count stays >= 1).
      4. Scan each plugin's (now-deduped) staging subdir individually.
         Per-plugin context (plugin.json, agents/) is preserved so cc-audit
         and tirith continue to work.
      5. Rewrite finding paths from staged → original so the report shows
         user-visible paths inside the cache.
      6. Bucket canonical findings: for any finding on a canonical file
         that had peer copies in other plugins, replicate the finding into
         every peer's report (with paths rewritten to point inside the
         peer's cache copy). No coverage hole from dedup.
      7. Emit per-plugin step tables + dedup-summary section + master
         summary table. Master report path unchanged:
         ``$MAIN_ROOT/reports/security/marketplace_<TS>-<label>.md``.

    Returns the worst exit code across all plugins (so CI gates work as
    before).
    """
    # Local imports keep the cold path (no marketplace) cheap.
    from cpv_dedup import apply_dedup, run_fclones  # noqa: PLC0415
    from cpv_staging import (  # noqa: PLC0415
        cleanup_staging,
        stage_marketplace,
    )

    try:
        label, plugin_dirs, skipped = _resolve_marketplace_plugins(args.marketplace)
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Error resolving marketplace {args.marketplace!r}: {exc}", file=sys.stderr)
        return 1

    # Build report path (timestamped, anchored to main checkout root).
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S%z")
    safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label)
    report_path = _resolve_report_root() / "reports" / "security" / f"marketplace_{ts}-{safe_label}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    # Build report body in-memory, also tee'd to stdout.
    body_lines: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        body_lines.append(line)

    emit(f"# Marketplace scan report — {label}")
    emit(f"Generated: {ts}")
    emit("")
    emit(f"Plugins to scan: {len(plugin_dirs)}; skipped at resolution: {len(skipped)}")
    if skipped:
        emit("Resolution-skipped:")
        for line in skipped:
            emit(f"  - {line}")
    emit("")

    if not plugin_dirs:
        emit("No plugins to scan after resolution. Exiting cleanly.")
        report_path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
        return 0

    # ── Phase 1: stage everything under one tmpdir ───────────────────
    # The label-resolver maps a plugin path to a stable, collision-resistant
    # safe name we can use as the staging subdir. Reusing the existing
    # `_plugin_label_from_dir` keeps the user-facing names consistent.
    safe_name_for: dict[Path, str] = {}

    def _name_resolver(p: Path) -> str:
        label = _plugin_label_from_dir(p)
        safe_name_for[p] = label
        return label

    mp_result = stage_marketplace(plugin_dirs, name_resolver=_name_resolver)

    try:
        # Build user-friendly mapping: original cache path → safe-name.
        # We need both lookups (orig→name, name→orig) downstream.
        # mp_result already provides plugin_paths and original_paths.

        if mp_result.skipped_reasons:
            emit("Staging-skipped:")
            for line in mp_result.skipped_reasons:
                emit(f"  - {line}")
            emit("")

        # ── Phase 2: dedup the corpus ──────────────────────────────
        dedup_files_removed = 0
        dedup_bytes_saved = 0
        dedup_groups = 0
        dedup_elapsed = 0.0
        dedup_skipped_reason = ""
        dedup_result = run_fclones(mp_result.stage_root)
        if dedup_result.attempted and dedup_result.succeeded:
            dedup_groups = len(dedup_result.dedup_map)
            dedup_elapsed = dedup_result.fclones_elapsed_seconds
            if mp_result.supports_deletion:
                dedup_files_removed, dedup_bytes_saved = apply_dedup(dedup_result.dedup_map)
            else:
                dedup_skipped_reason = (
                    f"dedup-by-deletion skipped (mode={mp_result.mode}); cross-fs symlink staging is read-only safe"
                )
        else:
            dedup_skipped_reason = dedup_result.skipped_reason or "fclones unavailable; scan proceeds without dedup"

        emit("## Dedup summary (corpus-wide via fclones)")
        emit("")
        emit(f"- Total files staged: {mp_result.files_staged:,}")
        emit(f"- Total bytes staged: {mp_result.bytes_staged:,}")
        emit(f"- Duplicate groups found: {dedup_groups:,}")
        emit(f"- Duplicate files removed from staging: {dedup_files_removed:,}")
        emit(f"- Bytes saved (scanner I/O reduction): {dedup_bytes_saved:,}")
        emit(f"- fclones elapsed: {dedup_elapsed:.2f}s")
        emit(f"- Staging mode: {mp_result.mode}")
        if dedup_skipped_reason:
            emit(f"- NOTE: {dedup_skipped_reason}")
        emit("")

        # ── Phase 3: per-plugin scan on the deduped staging ────────
        worst_exit = 0
        summary_rows: list[tuple[str, int, int, int, int, int, int]] = []
        plugin_reports: dict[str, ValidationReport] = {}
        plugin_step_tables: dict[str, str] = {}

        for safe_name, staged_path in mp_result.plugin_paths.items():
            original_path = mp_result.original_paths[safe_name]
            emit(f"## {safe_name}")
            emit(f"Path: `{original_path}`")
            emit(f"Staged: `{staged_path}`")
            emit("")
            report = validate_security(
                staged_path,
                enable_tirith=True,
                enable_trufflehog=True,
                enable_semgrep=True,
                with_classifier=args.with_classifier,
                with_extreme=args.extreme,
            )
            # Snapshot the per-plugin step table BEFORE the next plugin's
            # `validate_security` call resets the module-global step log.
            plugin_step_tables[safe_name] = format_scan_step_table(get_scan_step_log())
            # Rewrite paths from staged → original so reports show
            # user-visible paths inside the cache.
            _rewrite_finding_paths_to_original(report, staged_path, original_path)
            plugin_reports[safe_name] = report

        # ── Phase 4: bucket canonical findings into peer plugins ──
        propagated = _bucket_canonical_findings_into_plugins(
            plugin_reports,
            dedup_result.dedup_map if dedup_result.succeeded else {},
            mp_result.plugin_paths,
            mp_result.original_paths,
        )
        if propagated:
            emit(
                f"_Bucketing: {propagated} finding(s) propagated from "
                f"canonical files to peer plugins that originally contained "
                f"a copy of the same content._"
            )
            emit("")

        # ── Phase 5: emit per-plugin sections + summary ───────────
        for safe_name, report in plugin_reports.items():
            step_table = plugin_step_tables.get(safe_name, "")
            if step_table:
                emit(f"### {safe_name} — scan steps")
                emit(step_table)
                emit("")
            counts = {
                "CRITICAL": sum(1 for r in report.results if r.level == "CRITICAL"),
                "MAJOR": sum(1 for r in report.results if r.level == "MAJOR"),
                "MINOR": sum(1 for r in report.results if r.level == "MINOR"),
                "NIT": sum(1 for r in report.results if r.level == "NIT"),
                "WARNING": sum(1 for r in report.results if r.level == "WARNING"),
            }
            ec = report.exit_code_strict() if args.strict else report.exit_code
            worst_exit = max(worst_exit, ec)
            summary_rows.append(
                (
                    safe_name,
                    counts["CRITICAL"],
                    counts["MAJOR"],
                    counts["MINOR"],
                    counts["NIT"],
                    counts["WARNING"],
                    ec,
                )
            )
            emit(
                f"**Plugin {safe_name}**: "
                f"CRITICAL={counts['CRITICAL']} MAJOR={counts['MAJOR']} "
                f"MINOR={counts['MINOR']} NIT={counts['NIT']} "
                f"WARNING={counts['WARNING']} exit={ec}"
            )
            emit("")

        emit(f"## Marketplace summary — {label}")
        emit("")
        emit("| Plugin                                            | CRITICAL | MAJOR | MINOR | NIT | WARNING | Exit |")
        emit("|---------------------------------------------------|---------:|------:|------:|----:|--------:|-----:|")
        for name, c, M, m, n, w, ec in summary_rows:
            emit(f"| {name:<49} | {c:>8} | {M:>5} | {m:>5} | {n:>3} | {w:>7} | {ec:>4} |")
        emit("")
        emit(f"**Worst exit code:** {worst_exit} (worst-of-all-plugins)")
        emit("")
        emit(f"Report saved: `{report_path}`")

        report_path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
        return worst_exit
    finally:
        cleanup_staging(mp_result.stage_root)


def _extract_raw_positional_arg(argv: list[str]) -> str | None:
    """Find the first non-option argument in argv (the positional plugin_path).

    Skips ``argv[0]`` (the script name) and any leading options. Stops at
    the first arg that doesn't start with ``-`` and isn't the value of a
    preceding option. Returns None if no positional was supplied.

    Used by main() to recover the RAW spelling of the plugin_path argument
    (URLs like ``https://github.com/owner/repo`` get path-normalized by
    argparse's ``type=Path`` to ``https:/github.com/owner/repo`` — losing
    the double slash that URL detectors require).
    """
    # Options that take a value (CPV's flags). Their values must be skipped.
    value_taking_options = {
        "--marketplace",
        "--sarif-out",
        "--sbom-out",
    }
    skip_next = False
    for arg in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg in value_taking_options:
            skip_next = True
            continue
        if arg.startswith("--") and "=" in arg:
            continue  # `--foo=bar` form, no separate value to skip
        if arg.startswith("-"):
            continue  # boolean flag
        return arg
    return None


def main() -> int:
    """CLI entry point for standalone security validation.

    First action: verify CPV's own source has not been tampered with by
    checking each validator file's SHA256 against the GitHub-published
    canonical manifest for the running version. On mismatch, exits with
    code 2 and refuses to run — a tampered validator cannot be trusted
    to produce honest findings.

    Set `PLUGIN_SKIP_GITHUB_INTEGRITY=1` (preferred) or
    `CPV_SKIP_GITHUB_INTEGRITY=1` (legacy alias, removed in v2.53.0) to
    bypass for development.
    """
    # FIRST: verify validator integrity against GitHub canonical hashes.
    # Done before argparse so even `--help` is gated by integrity.
    from _plugin_verify_hashes import verify_self_integrity  # noqa: PLC0415

    verify_self_integrity(quiet=True)
    from cpv_validation_common import launcher_epilog as _launcher_epilog  # noqa: PLC0415

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
  16. cc-audit external scanner (npx remote fetch — always runs unless
      the package can't be resolved at its source URL)
  17. tirith external scanner (PATH/docker/nix/auto-install — always runs
      unless every resolution path fails;
      CPV_NO_TIRITH_INSTALL=1 to disable the install fallback)
  18. trufflehog (always runs — skipped only if the binary cannot be
      located on PATH or auto-installed from its release URL;
      uses --concurrency=cpu_count for parallel scans)
  19. semgrep    (always runs — same skip rule as trufflehog)
  20. Cisco AI Defense skill-scanner via uvx remote (always runs unless
      uvx is missing or the package is unreachable at its PyPI source)

Exit Codes:
  0 - All checks passed
  1 - CRITICAL issues found (must fix)
  2 - MAJOR issues found (should fix)
  3 - MINOR issues found (recommended to fix)
        """
        + "\n"
        + _launcher_epilog("security"),
    )
    # v2.48 — accepts a directory path (default) OR a GitHub URL OR a
    # local archive (.zip / .tar.gz / etc.). URL/archive detection runs
    # against the raw sys.argv spelling before argparse path-normalization
    # so `https://github.com/owner/repo` keeps its double slash. See
    # main() for the auto-ingest step.
    parser.add_argument(
        "plugin_path",
        type=Path,
        nargs="?",
        default=None,
        help="Path to the plugin directory, GitHub URL "
        "(`https://github.com/owner/repo` or `github:owner/repo`), "
        "or local archive (`*.zip`, `*.tar.gz`, `*.tgz`, `*.tar.bz2`, "
        "`*.tar.xz`, `*.tar`). URLs are cloned to a tmpdir, archives "
        "are extracted to a tmpdir, and both are scanned then cleaned "
        "up automatically. Mutually exclusive with --marketplace.",
    )
    parser.add_argument(
        "--marketplace",
        type=str,
        default=None,
        help=(
            "Scan EVERY plugin in the given marketplace. Accepts: "
            "(a) local path to a marketplace root (auto-detects "
            "`.claude-plugin/marketplace.json` OR a plugins-cache layout "
            "with `<plugin>/<version>/` subdirs); "
            "(b) a github URL like `https://github.com/owner/repo` or "
            "shorthand `github:owner/repo` — the script reads the remote "
            "marketplace.json via `gh api`, then runs each plugin from its "
            "local cache under `~/.claude/plugins/cache/<marketplace-name>/` "
            "if installed, or skips it with a SKIPPED row if not. "
            "Renders one step-status table per plugin plus a master "
            "summary at the end."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show all results including INFO and PASSED")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block validation")
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )
    parser.add_argument(
        "--bare-folder",
        "--loose",  # v2.48 alias for skill packs / flat *.md collections
        action="store_true",
        help=(
            "Bypass the .claude-plugin/ precondition. Use to scan a bare skill or "
            "content folder that is not wrapped in a Claude Code plugin tree. "
            "Aliased as --loose for symmetry with `cpv-validate-loose` workflows."
        ),
    )
    # External scanners are no longer opt-out — they ALWAYS run. Each
    # check_* function gracefully degrades to an INFO marker ("scanner
    # X unavailable: <reason>") when the binary cannot be resolved at
    # its source URL (PATH lookup → package manager → release download).
    # The CPV_NO_TIRITH_INSTALL=1 env var still disables tirith's
    # auto-install fallback if the operator's CI cannot pull containers.
    parser.add_argument(
        "--sarif-out",
        type=Path,
        default=None,
        help="Also emit findings as SARIF 2.1.0 JSON to the given path (RC-105). Compatible with GitHub code scanning.",
    )
    parser.add_argument(
        "--sbom-out",
        type=Path,
        default=None,
        help="Emit a CycloneDX 1.6 SBOM of declared dependencies to the "
        "given path (RC-106). Reads package.json, requirements*.txt, "
        "pyproject.toml, Cargo.toml, go.mod.",
    )
    parser.add_argument(
        "--with-classifier",
        action="store_true",
        help="Route findings for rules with a registered context-aware "
        "classifier (RC-21/22/65/87/93 in v2.42) through the FP/TP "
        "disambiguator. The classifier can demote a finding one "
        "severity tier (LIKELY_FP) or suppress it entirely "
        "(DEFINITE_FP). Off by default — preserves legacy v2.41 "
        "binary-guard behaviour. See TRDD-fe006962.",
    )
    parser.add_argument(
        "--extreme",
        action="store_true",
        help="Step 4 of TRDD-fe006962 — classifier escalation tier. When "
        "set, the highest-confidence TP verdicts (RC-21 copy-then-"
        "exfil-sink, RC-65 IMDS literal in same-line network call) "
        "promote the declared severity one tier (MAJOR → CRITICAL). "
        "Off by default because escalation can only inflate findings — "
        "use only when you want maximally-paranoid scanning of code "
        "that handles credentials. Implies --with-classifier; passing "
        "--extreme without --with-classifier is a no-op (escalation "
        "lives on the classifier path).",
    )

    args = parser.parse_args()

    # --- Marketplace mode (--marketplace) -------------------------------
    # When set, iterate every plugin in the marketplace, scan each, and
    # render one step-status table per plugin plus a master summary.
    if args.marketplace is not None:
        if args.plugin_path is not None:
            print("Error: --marketplace and plugin_path are mutually exclusive.", file=sys.stderr)
            return 1
        return _run_marketplace_scan(args)

    if args.plugin_path is None:
        parser.error("plugin_path is required unless --marketplace is given")

    # v2.48 — auto-detect URL or archive in the positional argument and
    # ingest to a tmpdir before scanning. The tmpdir is cleaned up in the
    # ``finally`` block at the end of main(). Local paths flow unchanged.
    # We grab the RAW spec from sys.argv (not from the path-normalized
    # args.plugin_path) so `https://github.com/owner/repo` keeps its
    # double slash for URL detection.
    raw_spec = _extract_raw_positional_arg(sys.argv) or str(args.plugin_path)
    from cpv_staging import (  # noqa: PLC0415
        cleanup_staging,
        ingest_archive,
        ingest_github_url,
        looks_like_archive,
        looks_like_github_url,
    )

    ingest_result = None  # set when we ingested from URL/archive
    try:
        if looks_like_github_url(raw_spec):
            try:
                ingest_result = ingest_github_url(raw_spec)
            except (ValueError, RuntimeError) as exc:
                print(f"Error ingesting GitHub URL {raw_spec!r}: {exc}", file=sys.stderr)
                return 1
            plugin_path = ingest_result.target.resolve()
        elif looks_like_archive(raw_spec):
            try:
                ingest_result = ingest_archive(raw_spec)
            except (FileNotFoundError, ValueError, RuntimeError) as exc:
                print(f"Error ingesting archive {raw_spec!r}: {exc}", file=sys.stderr)
                return 1
            plugin_path = ingest_result.target.resolve()
        else:
            # Plain local path — use the argparse-normalized Path.
            plugin_path = args.plugin_path.resolve()

        # Verify this is a plugin directory
        if not plugin_path.is_dir():
            print(f"Error: {plugin_path} is not a directory", file=sys.stderr)
            return 1
        if not args.bare_folder and not (plugin_path / ".claude-plugin").is_dir():
            # v2.48 — auto-detect a likely flat skill pack and suggest --loose.
            # Heuristic: 5+ *.md files at any depth AND no plugin.json AND no
            # canonical skills/<name>/SKILL.md tree → looks like a skill pack.
            md_count = 0
            try:
                for md in plugin_path.rglob("*.md"):
                    if md.is_file():
                        md_count += 1
                        if md_count >= 5:
                            break
            except OSError:
                pass
            has_canonical_skill = (
                any(
                    (plugin_path / "skills").is_dir()
                    and any(p.is_dir() and (p / "SKILL.md").is_file() for p in (plugin_path / "skills").iterdir())
                    for _ in [None]
                )
                if (plugin_path / "skills").is_dir()
                else False
            )
            looks_like_skill_pack = md_count >= 5 and not has_canonical_skill
            hint = ""
            if looks_like_skill_pack:
                hint = (
                    "\nHINT: This directory contains "
                    f"{md_count}+ *.md files but no plugin.json and no canonical "
                    "skills/<name>/SKILL.md layout — it looks like a flat skill "
                    "pack. Re-run with --loose to scan it."
                )
            print(
                f"Error: No Claude Code plugin found at {plugin_path}\n"
                "Expected a .claude-plugin/ directory. Use --bare-folder (or its "
                "v2.48 alias --loose) to scan a skill folder or any other directory "
                f"tree without that precondition.{hint}",
                file=sys.stderr,
            )
            return 1

        # Run validation. External scanners always run; each one self-skips
        # if its source binary/package cannot be resolved (no opt-out flags).
        report = validate_security(
            plugin_path,
            enable_tirith=True,
            enable_trufflehog=True,
            enable_semgrep=True,
            with_classifier=args.with_classifier,
            with_extreme=args.extreme,
        )
    finally:
        if ingest_result is not None:
            cleanup_staging(ingest_result.tmpdir)

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

    # Output results.
    #
    # Path-only mode is the DEFAULT now: every invocation auto-saves the
    # full aggregated report to disk and emits ONLY the compact summary
    # (counts table + verdict + plugin path + report path) to stdout.
    # This guarantees that any agent invoking this script — local or
    # remote (uvx) — gets a bounded, predictable stdout payload that
    # never floods its context window. The agent reads the report file
    # only when the user asks for details.
    #
    # The aggregator groups findings by (level, rule_id, message-stem)
    # so each vulnerability TYPE gets its full explanation exactly once,
    # followed by an occurrence count and a capped file:line list. No
    # finding is silently dropped — overflow occurrences are summarised
    # as "+N more" with the rule still named.
    #
    # Opt-outs:
    #   --json        : emit raw to_dict() JSON (intended for tools, not agents)
    #   --report PATH : write the report to PATH explicitly (no default location)
    #   --verbose     : also include INFO and PASSED in the report file body
    if args.json:
        output = report.to_dict()
        output["plugin_path"] = str(plugin_path)
        print(json.dumps(output, indent=2))
    else:
        from cpv_validation_common import print_results_aggregated  # noqa: PLC0415

        if args.report:
            report_path = Path(args.report)
        else:
            # Auto-default report path. Resolution order:
            #   1. main checkout root from `git worktree list` — anchors
            #      reports to the primary working tree so they survive
            #      when a linked worktree gets removed/merged. The
            #      worktree's local `./reports/` is gitignored
            #      everywhere, so writing there loses the audit trail.
            #   2. CLAUDE_PROJECT_DIR — only as fallback when not in a
            #      git context. Inside a linked worktree,
            #      CLAUDE_PROJECT_DIR resolves to the WORKTREE root,
            #      not the main checkout — that's why it's the
            #      fallback, not the primary.
            #   3. $TMPDIR — last resort on a remote uvx invocation
            #      where neither git nor CLAUDE_PROJECT_DIR is usable.
            ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S%z")
            base_root = _resolve_report_root()
            slug = plugin_path.name or "plugin"
            report_path = base_root / "reports" / "security" / f"{ts}-{slug}.md"

        # Snapshot the per-scan step log NOW (before save_* runs and
        # potentially clears any module state). The same snapshot is used
        # both inside the report file body and on stdout next to the path.
        step_log_snapshot = get_scan_step_log()
        step_table = format_scan_step_table(step_log_snapshot)

        def _print_full(report, verbose=False):
            # Step coverage table goes FIRST in the report body so the
            # reader knows up-front which steps actually ran.
            if step_table:
                print("## Scan Coverage — per-step status\n")
                print(step_table)
                print()
            print_report_summary(report, "Security Validation Report")
            # Use the aggregated printer instead of the flat per-finding
            # one — keeps the file body bounded by distinct-rule count
            # rather than total-finding count.
            print_results_aggregated(report, verbose=verbose)

        save_report_and_print_summary(
            report, report_path, "Security Validation", _print_full, args.verbose, plugin_path=args.plugin_path
        )

        # Surface the step table to stdout too — it answers the operator's
        # question "did the scanner actually run every step on every file?"
        # without forcing them to open the report file.
        if step_table:
            print("\nScan coverage — per-step status:")
            print(step_table)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
