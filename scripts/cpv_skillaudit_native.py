#!/usr/bin/env python3
"""SkillAudit native port — MANDATORY in-process security scanner.

Origin: ported from megamind-0x/skillaudit (https://github.com/megamind-0x/skillaudit,
MIT-licensed). The original npm package was rejected for direct integration
because it pulls in ``ethers`` + ``@x402/evm`` + ``@x402/express`` + ``express``
+ ``express-rate-limit`` as runtime dependencies that the shipped CLI code
never imports — supply-chain bloat / untrusted single-author package risk.

Instead, the **safe parts** (50 rules / 490 regex patterns from
``scripts/rules/skillaudit_patterns.json``, the suppression heuristics, the
capability analyser, the secret detector, and the structural read→net
detector) are reimplemented here in pure Python. ZERO external
dependencies. ZERO subprocess. ZERO network. ZERO supply-chain surface.

This module is invoked by ``validate_security.py`` as Check 27 — every
CPV security validation pass runs it. It cannot be disabled by any
``CPV_NO_*`` / ``CPV_SKIP_*`` env var (the iron rule: no plugin with
issues must be pushed to GitHub ever).

The output is normalised into CPV's existing ``ValidationReport`` shape
via ``report_findings()`` so downstream consumers (the severity
summary, the breakdown matrix, the autofix loop) see the findings
exactly like any other CPV check.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "SkillAuditFinding",
    "SkillAuditScanResult",
    "scan_content",
    "scan_path",
    "run_skillaudit_scan",
    "report_findings",
    "SAFE_DOMAINS",
    "SUSPICIOUS_DOMAINS",
]


# ────────────────────────────────────────────────────────────────────────
# Rules + patterns loader
# ────────────────────────────────────────────────────────────────────────


_RULES_PATH = Path(__file__).resolve().parent / "rules" / "skillaudit_patterns.json"


def _load_rules() -> list[dict[str, Any]]:
    """Load the bundled skillaudit rule catalog."""
    if not _RULES_PATH.is_file():
        return []
    try:
        data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rules = data.get("rules", []) if isinstance(data, dict) else []
    return rules if isinstance(rules, list) else []


_RULES_CACHE: list[dict[str, Any]] | None = None


def _get_rules() -> list[dict[str, Any]]:
    global _RULES_CACHE
    if _RULES_CACHE is None:
        _RULES_CACHE = _load_rules()
    return _RULES_CACHE


# Pre-compile rule patterns once per process (re-compile is expensive
# under xdist if we do it per file).
_COMPILED_RULES_CACHE: list[tuple[dict[str, Any], list[re.Pattern[str]]]] | None = None


def _compiled_rules() -> list[tuple[dict[str, Any], list[re.Pattern[str]]]]:
    global _COMPILED_RULES_CACHE
    if _COMPILED_RULES_CACHE is not None:
        return _COMPILED_RULES_CACHE
    compiled: list[tuple[dict[str, Any], list[re.Pattern[str]]]] = []
    for rule in _get_rules():
        patterns = rule.get("patterns") or []
        compiled_patterns: list[re.Pattern[str]] = []
        for pat in patterns:
            if not isinstance(pat, str):
                continue
            try:
                compiled_patterns.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                # Skip malformed regex rather than crash the whole scan.
                continue
        if compiled_patterns:
            compiled.append((rule, compiled_patterns))
    _COMPILED_RULES_CACHE = compiled
    return compiled


# ────────────────────────────────────────────────────────────────────────
# Severity mapping (skillaudit → CPV)
# ────────────────────────────────────────────────────────────────────────


_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "high": "major",
    "medium": "minor",
    "low": "nit",
    "info": "info",
}


def _to_cpv_severity(skillaudit_severity: str) -> str:
    return _SEVERITY_MAP.get(skillaudit_severity.lower(), "minor")


# ────────────────────────────────────────────────────────────────────────
# Domain reputation
# ────────────────────────────────────────────────────────────────────────


SAFE_DOMAINS: frozenset[str] = frozenset(
    {
        "github.com", "raw.githubusercontent.com", "gist.github.com",
        "npmjs.com", "registry.npmjs.org", "unpkg.com",
        "pypi.org", "crates.io", "rubygems.org",
        "stackoverflow.com", "developer.mozilla.org",
        "google.com", "googleapis.com", "cloudflare.com",
        "vercel.app", "netlify.app", "heroku.com",
        "docker.io", "hub.docker.com",
        "openai.com", "anthropic.com", "huggingface.co",
        "linkedin.com", "twitter.com", "x.com",
        "medium.com", "dev.to", "hashnode.dev",
        "wikipedia.org", "wikimedia.org",
        "cdn.jsdelivr.net", "cdnjs.cloudflare.com",
    }
)

SUSPICIOUS_DOMAINS: frozenset[str] = frozenset(
    {
        "webhook.site", "requestbin.com", "pipedream.net",
        "ngrok.io", "ngrok-free.app", "burpcollaborator.net",
        "interact.sh", "oastify.com", "hookbin.com", "postb.in",
        "rbndr.us", "1u.ms", "nip.io", "xip.io",
        "pastebin.com", "transfer.sh", "file.io",
    }
)


# ────────────────────────────────────────────────────────────────────────
# Suppression heuristics — placeholder / doc-context / markdown
# ────────────────────────────────────────────────────────────────────────


_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"YOUR_",
        r"YOUR\s+",
        r"xxx+",
        r"REPLACE",
        r"<your[_-]",
        r"REPLACE_WITH",
        r"placeholder",
        r"example\.com",
        r"your[_-]api[_-]?key",
        r"your[_-]token",
        r"your[_-]secret",
        r"your[_-]access",
        r"your[_-]jwt",
        r"xxx_replace",
    )
)

_DOC_CONTEXT_WORDS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bexample\b",
        r"\busage\b",
        r"\bstep\s+\d",
        r"\bhow\s+to\b",
        r"\btutorial\b",
        r"\bsetup\b",
        r"\bconfiguration\b",
        r"\bgetting\s+started\b",
        r"\breference\b",
        r"\bquick\s+start\b",
        r"\bapi\s+reference\b",
        r"\bdocumentation\b",
        r"\bguide\b",
        r"\boverview\b",
        r"\bsave\s+your\b",
        r"\bstore\s+your\b",
        r"\bset\s+your\b",
        r"\badd\s+your\b",
        r"\bget\s+your\b",
        r"\bcreate\s+your\b",
        r"\bgenerate\b",
    )
)


def _has_placeholder(line: str) -> bool:
    return any(p.search(line) for p in _PLACEHOLDER_PATTERNS)


def _has_doc_context(lines: list[str], line_idx: int, span: int = 5) -> bool:
    lo = max(0, line_idx - span)
    hi = min(len(lines) - 1, line_idx + span)
    for i in range(lo, hi + 1):
        if any(p.search(lines[i]) for p in _DOC_CONTEXT_WORDS):
            return True
    return False


def _is_markdown_table(line: str) -> bool:
    return bool(re.match(r"^\s*\|", line))


def _is_markdown_heading(line: str) -> bool:
    return bool(re.match(r"^#+\s", line))


# ────────────────────────────────────────────────────────────────────────
# Code block tracker
# ────────────────────────────────────────────────────────────────────────


@dataclass
class _CodeBlockRange:
    start: int
    end: int
    lang: str


def _build_code_block_map(lines: list[str]) -> tuple[list[bool], list[_CodeBlockRange]]:
    """Match the JS scanner's behaviour: lines INSIDE a ``` fence are in_block."""
    in_block = False
    block_lang = ""
    block_start = -1
    cb_map = [False] * len(lines)
    ranges: list[_CodeBlockRange] = []
    fence_re = re.compile(r"^```")
    for i, line in enumerate(lines):
        if fence_re.match(line.strip()):
            if not in_block:
                in_block = True
                block_start = i
                block_lang = line.strip().removeprefix("```").strip().lower()
            else:
                in_block = False
                ranges.append(_CodeBlockRange(block_start, i, block_lang))
                block_lang = ""
        cb_map[i] = in_block
    return cb_map, ranges


def _code_block_lang(ranges: list[_CodeBlockRange], line_idx: int) -> str | None:
    for r in ranges:
        if r.start < line_idx < r.end:
            return r.lang
    return None


def _code_block_has_placeholder(
    lines: list[str], ranges: list[_CodeBlockRange], line_idx: int
) -> bool:
    for r in ranges:
        if r.start < line_idx < r.end:
            return any(_has_placeholder(lines[i]) for i in range(r.start, r.end + 1))
    return False


# ────────────────────────────────────────────────────────────────────────
# Should-suppress logic (mirrors src/scanner.js::shouldSuppress)
# ────────────────────────────────────────────────────────────────────────


def _is_instructional_context(lines: list[str], line_idx: int) -> bool:
    line = lines[line_idx]
    if _has_placeholder(line):
        return True
    if _is_markdown_table(line):
        return True
    if _is_markdown_heading(line):
        return True
    if _has_doc_context(lines, line_idx):
        return True
    if "`" in line and "`" in line.split("`", 1)[1] if "`" in line else False:  # noqa: SIM103
        if _has_doc_context(lines, line_idx, 8):
            return True
    return False


# v2.99.1 — rule families that should be suppressed inside markdown
# tables (table content is rendered, not executed; a table row can never
# be a live malicious payload — and `path/like/string` cells routinely
# embed substrings that look like shell keywords). Skillaudit's original
# JS scanner only suppressed CRED_ENV_READ/TOKEN_STEAL in tables;
# CPV broadens this to every category whose patterns suffer the same
# false-positive pressure on documentation.
_MD_TABLE_SUPPRESSED_RULES: frozenset[str] = frozenset(
    {
        "CRED_ENV_READ", "TOKEN_STEAL", "CRED_ENV_SAFE",
        "CMD_INJECTION", "SHELL_EXEC", "REVERSE_SHELL",
        "SUPPLY_CHAIN", "FS_WRITE", "FS_READ", "FS_RECURSIVE_RM",
        "SSRF_PATTERN", "NET_SUSPICIOUS", "DNS_REBIND",
        "INSECURE_CRYPTO", "OBFUSCATION", "REGEX_DOS",
        "INDIRECT_PROMPT_INJECT", "PROMPT_INJECT",
        "MCP_SCHEMA_POISON", "TOOL_POISONING",
        "A2A_AGENT_IMPERSONATION", "A2A_TASK_HIJACK",
        "A2A_CROSS_AGENT_INJECT", "A2A_DATA_LEAK", "A2A_CAPABILITY_ABUSE",
        "PERSISTENCE", "PRIVILEGE_ESC", "CONTAINER_ESCAPE",
        "ENV_RECON", "RESOURCE_ABUSE",
        "AGENT_MEMORY_MOD", "TOOL_SHADOW", "CROSS_TOOL_ACCESS",
    }
)

# Inside fenced code blocks whose language is a pure-data language (no
# executable semantics), suppress code-execution and obfuscation rules.
# A JSON/YAML/TOML config file CAN contain dangerous strings, but those
# strings only matter when something interprets them — the rule firing
# on the literal text is documentation noise.
_DATA_LANG_FENCES: frozenset[str] = frozenset(
    {"json", "yaml", "yml", "toml", "ini", "properties", "env", "dotenv", "xml", ""}
)

# Shell-keyword tokens that appear as SUBSTRINGS of common English words
# / path components, producing substring-match false positives when a
# pattern lacks word boundaries (e.g. `ls` inside `skills`, `id` inside
# `sandbox`, `cat` inside `concatenate`, etc.). When a match consists
# ONLY of one of these short shell tokens AND the surrounding source
# contains an alphanumeric character on either side, the match is a
# substring false-positive — suppress.
_SHORT_SHELL_TOKENS: frozenset[str] = frozenset(
    {"ls", "id", "cat", "nc", "sh", "su", "ps", "rm", "cp", "mv", "dd", "df"}
)


# v2.99.1 — Python docstring tracker. A `"""..."""` block contains
# documentation prose; matches inside should be demoted, not treated
# as live code.
_PY_DOCSTRING_FENCE = re.compile(r'"""|' + r"'''")


def _build_py_docstring_map(lines: list[str], file_path: str) -> list[bool]:
    """Return per-line in_docstring flags for Python files.

    Tracks triple-quoted strings (Python docstrings). For non-Python
    files, returns an all-False list. Naive (doesn't handle f-strings,
    multi-line normal strings) but sufficient for the docstring-match
    suppression heuristic.
    """
    is_python = file_path.endswith(".py") or any(
        line.lstrip().startswith(("#!/usr/bin/env python", "#!/usr/bin/python"))
        for line in lines[:1]
    )
    in_doc = [False] * len(lines)
    if not is_python:
        return in_doc
    inside = False
    for i, line in enumerate(lines):
        # Count triple-quote occurrences on this line.
        triples = len(_PY_DOCSTRING_FENCE.findall(line))
        if inside:
            in_doc[i] = True
            if triples % 2 == 1:
                inside = False
        else:
            if triples >= 1:
                in_doc[i] = True
                if triples % 2 == 1:
                    inside = True
    return in_doc


def _is_in_line_comment(line: str, file_path: str) -> bool:
    """True if the line is a comment in its host language.

    Conservative: returns True only for whole-line comments, not for
    trailing comments after code (since a real malicious call can sit
    before the `#`). This is just a demote heuristic.
    """
    stripped = line.lstrip()
    if not stripped:
        return False
    suffix = file_path.lower()
    # Python / shell / YAML / TOML / make
    if suffix.endswith((".py", ".sh", ".bash", ".zsh", ".fish", ".yml", ".yaml", ".toml", ".ini", ".conf")) or suffix == "makefile":
        return stripped.startswith("#")
    # JS / TS / Java / Go / C / C++ / Rust
    if suffix.endswith((".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx", ".java", ".go", ".c", ".cpp", ".cc", ".rs", ".rb", ".php")):
        return stripped.startswith(("//", "/*", "*"))
    return False


def _is_substring_false_positive(line: str, match: str) -> bool:
    """True when `match` appears in `line` as a strict substring of a
    longer alphanumeric token (no word boundary on at least one side).

    Catches the `ls` ⊂ `skills` / `id` ⊂ `valid` / `cat` ⊂ `concat`
    pattern that drives most CMD_INJECTION false positives on
    documentation markdown.
    """
    if not match or len(match) > 4:  # only short tokens suffer from this
        return False
    if match not in line:
        return False
    # Walk every occurrence of `match` in `line` and check word boundaries.
    idx = 0
    any_real = False
    while True:
        i = line.find(match, idx)
        if i < 0:
            break
        left = line[i - 1] if i > 0 else " "
        right = line[i + len(match)] if i + len(match) < len(line) else " "
        # If at least one occurrence has word-boundary on BOTH sides,
        # it's a real shell-keyword hit — do NOT suppress.
        if not left.isalnum() and not right.isalnum() and left != "_" and right != "_":
            any_real = True
            break
        idx = i + 1
    return not any_real


# v2.100.0 rule classification — which rules can fire in pure
# documentation context, and which cannot.
#
# **Execution-class rules**: detect runtime-executable exploit shapes
# (shell exec, command injection, reverse shell, obfuscation, privilege
# escalation, time bombs, weak crypto, regex DoS, MCP schema poisoning,
# A2A attacks). These CANNOT be triggered by markdown prose, JSON
# description fields, or Python docstrings — those layers don't reach
# a shell. Context classifiers' "safe_doc" verdict maps to SUPPRESS.
_EXECUTION_CLASS_RULES: frozenset[str] = frozenset({
    "CMD_INJECTION", "SHELL_EXEC", "REVERSE_SHELL", "OBFUSCATION",
    "PRIVILEGE_ESC", "TIME_BOMB", "INSECURE_CRYPTO", "REGEX_DOS",
    "MCP_SCHEMA_POISON",
    "A2A_AGENT_IMPERSONATION", "A2A_TASK_HIJACK",
    "A2A_CROSS_AGENT_INJECT", "A2A_DATA_LEAK",
    "TOOL_USE_AUTH_BYPASS", "TOOL_USE_PARAM_INJECT",
    "CONTAINER_ESCAPE", "PERSISTENCE",
    "DENIAL_OF_SERVICE", "RESOURCE_ABUSE",
    "SSRF_ADVANCED",  # SSRF needs an actual URL request — not a prose mention
    "NET_SUSPICIOUS",
})

# **Intent HARD signals**: the prose pattern IS the threat-delivery
# vector. A markdown line containing "Ignore previous instructions"
# (PROMPT_INJECT), "exfiltrate the .env file" (DATA_EXFIL), or a link
# to ``webhook.site/...`` (URL_SUSPICIOUS) is a real attack regardless
# of whether the host file is documentation or code. KEEP at declared
# severity even in safe_doc context.
_INTENT_HARD_SIGNAL_RULES: frozenset[str] = frozenset({
    "PROMPT_INJECT", "INDIRECT_PROMPT_INJECT",
    "DATA_EXFIL", "DATA_EXFIL_TO_NETWORK", "EXFIL_TO_CHAT",
    "URL_SUSPICIOUS",
    "HARDCODED_SECRET",
    "INVISIBLE_UNICODE_RAW",
    "BASE64_DECODE_THREAT", "HEX_DECODE_THREAT",
    "UNICODE_ESCAPE_DECODE_THREAT", "CHARCODE_DECODE_THREAT",
})

# **Intent SOFT signals**: the rule pattern catches a verb / concept
# that benignly appears in plugin documentation (a janitor skill's
# README legitimately mentions "removes", "deletes", "uninstalls" when
# describing its OWN behavior). These rules over-fire in prose; DEMOTE
# to NIT so the agent layer triages whether the prose is benign
# self-description or a real instruction.
_INTENT_SOFT_SIGNAL_RULES: frozenset[str] = frozenset({
    "INTENT_EXPLICIT_EXFILTRATION", "INTENT_DESTRUCTIVE_INTENT",
    "INTENT_AGENT_MANIPULATION", "INTENT_INSTRUCTION_OVERRIDE",
    "TOKEN_STEAL", "CRED_ENV_READ", "CRED_ENV_SAFE",
    "CRED_THEFT", "CREDENTIAL_REFERENCE",
    "RECONNAISSANCE", "EVASION", "OBFUSCATION_INTENT",
    "CRYPTO_THEFT",
})

# Backwards-compat alias — the union of both halves. Older heuristics
# (and the dispatcher's default fall-through) treat any intent rule as
# safe-doc-keeping. The split above adds finer-grained control.
_INTENT_CLASS_RULES: frozenset[str] = _INTENT_HARD_SIGNAL_RULES | _INTENT_SOFT_SIGNAL_RULES


# Path basenames that are ALWAYS instruction-loadable — content there
# IS read by Claude Code as agent instructions, so prompt-injection /
# data-exfil / etc. prose IS a real delivery vector and must NOT be
# suppressed by the documentation-only-path heuristic. Listed as
# basenames (case-insensitive).
_INSTRUCTION_LOADABLE_BASENAMES: frozenset[str] = frozenset(
    {"skill.md", "claude.md", "agents.md"}
)

# Basenames / dir prefixes that are NEVER loaded as instructions —
# pure documentation surfaces. Issue #38: a prompt-injection phrase
# living in `references/foo.md` cannot reach an agent because no
# production pipeline reads those files as instructions. Suppress
# matches there so plugin authors who describe the threat model in
# their own docs don't see CPV flag their warnings as the threat.
_DOC_ONLY_BASENAMES: frozenset[str] = frozenset(
    {
        "readme.md",
        "changelog.md",
        "contributing.md",
        "license.md",
        "license",
        "code_of_conduct.md",
        "security.md",
        "support.md",
        "authors.md",
        "maintainers.md",
        "history.md",
    }
)
_DOC_ONLY_DIR_PREFIXES: tuple[str, ...] = (
    "docs/",
    "doc/",
    "references/",
    "reference/",
    "examples/",
    "example/",
    "changelog/",
)


def _is_documentation_only_path(file_path: str) -> bool:
    """Return True when ``file_path`` is a pure-documentation surface
    that Claude Code NEVER loads as agent instructions.

    Used by the safe_doc dispatcher (issue #38) to suppress INTENT
    HARD-signal findings (PROMPT_INJECT, INDIRECT_PROMPT_INJECT,
    DATA_EXFIL, etc.) in files whose prose can never reach an agent.

    The check is conservative: a path is documentation-only when BOTH:

    * Its basename is in `_DOC_ONLY_BASENAMES` (README/CHANGELOG/…)
      OR it lives under a `_DOC_ONLY_DIR_PREFIXES` subtree, AND
    * Its basename is NOT in `_INSTRUCTION_LOADABLE_BASENAMES`
      (a `SKILL.md` inside a `references/` directory would still be
      treated as instruction-loadable, never doc-only).

    For everything else (including unknown `.md` files at plugin
    root, agents/, commands/, .claude/rules/), the function returns
    False and the existing dispatcher behaviour stands.
    """
    norm = file_path.replace("\\", "/").lstrip("./").lower()
    if not norm:
        return False
    parts = norm.split("/")
    basename = parts[-1]
    # Never doc-only if the basename is a known instruction-loadable
    # file. SKILL.md inside references/ stays instruction-loadable.
    if basename in _INSTRUCTION_LOADABLE_BASENAMES:
        return False
    # Doc-only if basename is on the allowlist (READMEs etc.) OR the
    # path is anchored under a doc-only directory subtree.
    if basename in _DOC_ONLY_BASENAMES:
        return True
    for prefix in _DOC_ONLY_DIR_PREFIXES:
        if norm.startswith(prefix) or ("/" + prefix) in ("/" + norm):
            return True
    return False


def _context_classifier_verdict(
    file_path: str,
    lines: list[str],
    line_idx: int,
    match: str,
    rule_id: str,
) -> str:
    """v2.100.0 — per-file-type context classification (TRDD-a4260cc6).

    Dispatches to the right context classifier based on the host file's
    extension:

    * ``.py`` → ``_skillaudit_python_context.classify``
    * ``.json`` / ``.jsonc`` → ``_skillaudit_json_context.classify``
    * ``.md`` / ``.markdown`` → ``_skillaudit_markdown_context.classify``
    * ``.yml`` / ``.yaml`` → ``_skillaudit_yaml_context.classify``

    The classifier returns one of ``safe_literal`` / ``safe_doc`` /
    ``safe_schema`` / ``code_fence_neutral`` / ``suspect`` / ``unknown``.

    This function maps those into the three-way ``_confidence`` enum,
    using rule-classification to decide whether documentation context
    fully suppresses or only demotes:

    * ``safe_literal`` (AST-proven not an exploit shape) → ``"suppress"``.
      The shape is provably benign regardless of rule.
    * ``safe_doc`` / ``safe_schema`` for an EXECUTION-class rule
      (CMD_INJECTION / SHELL_EXEC / TIME_BOMB / …) → ``"suppress"``.
      Markdown / JSON description cannot trigger a shell.
    * ``safe_doc`` / ``safe_schema`` for an INTENT-class rule
      (PROMPT_INJECT / INTENT_EXFIL / DATA_EXFIL / URL_SUSPICIOUS / …)
      → ``"demote"``. Malicious markdown / JSON CAN carry these — the
      agent layer triages, never silently drops.
    * ``code_fence_neutral`` → ``"demote"`` (the iron rule — agents
      triage suspicious-but-not-conclusive matches).
    * ``suspect`` → ``"keep"`` (preserve the rule's declared severity).
    * ``unknown`` → empty string. The caller falls through to the
      existing heuristic chain (placeholder, MD-table, short-shell-token,
      etc.) — preserving every v2.99.x suppression rule.
    """
    if not file_path:
        return ""
    fp_lower = file_path.lower()
    content = "\n".join(lines)

    classifier_verdict: str | None = None

    if fp_lower.endswith(".py"):
        try:
            from _skillaudit_python_context import classify as _py_classify  # type: ignore[import-not-found]
        except ImportError:
            return ""
        classifier_verdict = _py_classify(file_path, content, line_idx, match, rule_id)
    elif fp_lower.endswith((".json", ".jsonc")):
        try:
            from _skillaudit_json_context import classify as _json_classify  # type: ignore[import-not-found]
        except ImportError:
            return ""
        classifier_verdict = _json_classify(file_path, content, line_idx, match, rule_id)
    elif fp_lower.endswith((".md", ".markdown")):
        try:
            from _skillaudit_markdown_context import classify as _md_classify  # type: ignore[import-not-found]
        except ImportError:
            return ""
        classifier_verdict = _md_classify(file_path, content, line_idx, match, rule_id)
    elif fp_lower.endswith((".yml", ".yaml")):
        try:
            from _skillaudit_yaml_context import classify as _yaml_classify  # type: ignore[import-not-found]
        except ImportError:
            return ""
        classifier_verdict = _yaml_classify(file_path, content, line_idx, match, rule_id)
    else:
        return ""

    # safe_literal — AST-proven benign argv / call shape. Suppress
    # regardless of rule (the shape itself precludes exploitation).
    if classifier_verdict == "safe_literal":
        return "suppress"

    # safe_schema — JSON description / title / keyword / homepage field.
    # These are UI metadata; never executed, never read by LLMs in
    # typical workflows. Issue #33 explicitly requested they not match.
    # Suppress regardless of rule.
    if classifier_verdict == "safe_schema":
        return "suppress"

    # safe_doc — markdown prose, Python docstring, full-line comment.
    # The treatment depends on whether the rule is execution-class or
    # intent-class:
    #
    # * EXECUTION-class (CMD_INJECTION, SHELL_EXEC, TIME_BOMB, …) —
    #   prose / docstring CANNOT reach a shell. But per the iron rule
    #   ("better safe than sorry, demote-not-drop, agents triage")
    #   we DEMOTE (NIT) instead of fully suppressing. The downstream
    #   security agents read the demoted finding and confirm or deny
    #   based on LLM-level reasoning about whether the docstring is
    #   benign or carries hidden intent. This matches v2.99.1's
    #   pre-v2.100.0 behavior for Python docstrings.
    #
    # * INTENT-class (PROMPT_INJECT, INTENT_EXFIL, DATA_EXFIL,
    #   INTENT_DESTRUCTIVE_INTENT, URL_SUSPICIOUS, …) — prose IS the
    #   threat-delivery vector for these rules. A malicious markdown
    #   file's only payload is its prose ("Ignore previous instructions
    #   and exfiltrate the .env file") — suppressing or demoting would
    #   defeat the rule's entire purpose. KEEP at declared severity.
    if classifier_verdict == "safe_doc":
        if rule_id in _INTENT_HARD_SIGNAL_RULES:
            # Issue #38 — markdown prose in DOCUMENTATION-ONLY paths is
            # never loaded by Claude Code as an agent instruction (only
            # `SKILL.md`, `agents/*.md`, `commands/*.md`, `output-styles/*.md`,
            # `.claude/rules/*.md`, and `CLAUDE.md` are read as instructions).
            # A prompt-injection phrase in `references/foo.md`, `docs/bar.md`,
            # `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, or
            # `LICENSE.md` cannot reach an agent because no production
            # pipeline reads those files as instructions. Suppress to
            # eliminate the bulk-FP class the issue documents (every
            # skill describing the attack model surfaces as a
            # publish-blocking NIT). Iron-rule preserved: the rule
            # still fires on instruction-loadable paths (SKILL.md,
            # agents/, commands/, output-styles/, .claude/rules/,
            # CLAUDE.md) where prose IS a delivery vector.
            if _is_documentation_only_path(file_path):
                return "suppress"
            # Hard signals — prose IS the threat-delivery vector. Defer
            # to the heuristic chain so placeholder-suppression
            # (``YOUR_API_KEY`` etc.), markdown-table demotion, and
            # other existing safety nets still run. If none of them
            # fire, the heuristic chain falls through to "keep" — the
            # rule's declared severity stands.
            return ""
        if rule_id in _INTENT_SOFT_SIGNAL_RULES:
            # Soft signals — the rule's verb / concept appears benignly
            # in plugin self-description docs. Demote to NIT so the
            # agent layer triages.
            return "demote"
        # EXECUTION-class or any other rule → demote (iron rule).
        return "demote"

    if classifier_verdict == "code_fence_neutral":
        return "demote"
    if classifier_verdict == "suspect":
        return "keep"
    # "unknown" — defer to existing heuristic chain.
    return ""


def _confidence(
    lines: list[str],
    line_idx: int,
    match: str,
    rule_id: str,
    cb_map: list[bool],
    cb_ranges: list[_CodeBlockRange],
    *,
    py_doc_map: list[bool] | None = None,
    file_path: str = "",
) -> str:
    """Three-way classification for a rule match.

    Returns one of:

    * ``"suppress"`` — the match is LITERALLY impossible to be a real
      threat (placeholder tokens like ``YOUR_API_KEY``, or a placeholder
      sibling line inside the same code block). Hard-drop.
    * ``"demote"`` — there is reasonable documentation context that the
      match is descriptive (markdown-table cell, data-only fenced block,
      short-shell-token substring of a longer identifier). The finding
      is kept but emitted at WARNING-level so a reviewer (or downstream
      agent) can adjudicate. NEVER silently suppressed — per the
      principle "better safe than sorry, the agents will verify".
    * ``"keep"`` — no mitigation applies; emit at the rule's original
      severity.

    The split between *suppress* and *demote* is the calibration knob
    that distinguishes "no human would treat this as a threat" from
    "this MIGHT be a threat but the surrounding context suggests it's
    documentation". Demoted findings flow through to the security
    agents for LLM-based disambiguation.

    **v2.100.0 (TRDD-a4260cc6 / closes #33):** before the heuristic
    chain runs, the per-file-type context classifier runs first. If it
    returns a conclusive verdict (``suppress`` / ``demote`` / ``keep``),
    that verdict wins. ``unknown`` from the classifier falls through to
    the existing heuristics — preserving every v2.99.x suppression rule.
    """
    line = lines[line_idx]

    # ── Hard-suppress class: placeholder tokens make the match impossible ──
    # Run BEFORE the v2.100.0 context classifier so a documented
    # placeholder always suppresses, regardless of file type.
    if _has_placeholder(line):
        return "suppress"
    if cb_map[line_idx] and _code_block_has_placeholder(lines, cb_ranges, line_idx):
        return "suppress"
    if re.search(r"`credentials\.json`", line):
        return "suppress"

    # v2.100.0 — Layer 0: per-file-type context classifier (runs after
    # placeholder-suppression so documented placeholders always win).
    ctx_verdict = _context_classifier_verdict(file_path, lines, line_idx, match, rule_id)
    if ctx_verdict:
        return ctx_verdict

    # ── Demote class: contextual mitigations suggest documentation ──
    # Short shell tokens (ls/id/cat/nc/sh/su/etc.) appearing as
    # substrings of longer identifiers (skills, valid, concat, etc.)
    # are almost-certainly substring false positives. Demote rather
    # than drop — there's a non-zero chance a real shell `ls` appears
    # in the line elsewhere.
    if (
        match.lower() in _SHORT_SHELL_TOKENS
        and _is_substring_false_positive(line, match)
    ):
        return "demote"

    # Markdown-table cells are rendered, not executed. Demote
    # injection/supply-chain/obfuscation rules — they're descriptive
    # text in this context. Original skillaudit hard-suppressed
    # CRED_ENV_READ/TOKEN_STEAL here; CPV demotes broadly and keeps
    # the finding visible.
    if _is_markdown_table(line) and rule_id in _MD_TABLE_SUPPRESSED_RULES:
        return "demote"

    # Pure-data fenced code blocks (json/yaml/toml/ini/env/xml) cannot
    # execute code — demote code-execution / obfuscation rules.
    if cb_map[line_idx]:
        lang = (_code_block_lang(cb_ranges, line_idx) or "").lower()
        if lang in _DATA_LANG_FENCES and rule_id in {
            "CMD_INJECTION", "SHELL_EXEC", "REVERSE_SHELL",
            "OBFUSCATION", "REGEX_DOS", "INSECURE_CRYPTO",
            "INDIRECT_PROMPT_INJECT", "MCP_SCHEMA_POISON",
            "A2A_AGENT_IMPERSONATION", "A2A_TASK_HIJACK",
            "A2A_CROSS_AGENT_INJECT", "A2A_DATA_LEAK",
        }:
            return "demote"

    # Doc-context mitigations: credential rules inside surrounding
    # documentation keywords. CRED_ENV_READ/TOKEN_STEAL get demoted
    # (not suppressed) so a reviewer still sees the match.
    if _has_doc_context(lines, line_idx):
        if rule_id in ("CRED_ENV_READ", "TOKEN_STEAL", "CRED_ENV_SAFE"):
            return "demote"
        if cb_map[line_idx]:
            return "demote"
    if _is_markdown_table(line) and rule_id in ("CRED_ENV_READ", "TOKEN_STEAL"):
        return "demote"
    if re.search(r"Authorization:\s*Bearer", line, re.IGNORECASE):
        if cb_map[line_idx]:
            return "demote"
    if re.search(r"credentials\.json", match, re.IGNORECASE):
        if _has_doc_context(lines, line_idx, 8):
            return "demote"
    if re.search(r"process\.env\.", match, re.IGNORECASE):
        if _has_doc_context(lines, line_idx, 8):
            return "demote"
    if cb_map[line_idx] and _has_doc_context(lines, line_idx, 8):
        return "demote"

    # v2.99.1 — Python docstring + whole-line comment context.
    # Matches embedded in prose documentation are commentary, not
    # active threats. DEMOTE (not suppress) so a reviewer / agent
    # can still verify ambiguous cases.
    if py_doc_map is not None and 0 <= line_idx < len(py_doc_map) and py_doc_map[line_idx]:
        return "demote"
    if file_path and _is_in_line_comment(lines[line_idx], file_path):
        return "demote"

    # v2.99.1 — SSTI false positive on GitHub Actions ``${{ github.* }}``
    # expressions. These are GitHub's context-expression syntax (a
    # well-known, sandboxed runtime), not server-side templating.
    if rule_id == "SSTI" and file_path.lower().endswith((".yml", ".yaml")):
        if re.search(r"\$\{\{\s*github\.", lines[line_idx]):
            return "demote"

    return "keep"


def _should_suppress(
    lines: list[str],
    line_idx: int,
    match: str,
    rule_id: str,
    cb_map: list[bool],
    cb_ranges: list[_CodeBlockRange],
) -> bool:
    """Back-compat wrapper around _confidence — returns True for hard suppress only.

    External callers (and the rule-match loop) prefer the three-way
    classifier, but the old binary API is retained so existing tests
    that import _should_suppress continue to work.
    """
    return _confidence(lines, line_idx, match, rule_id, cb_map, cb_ranges) == "suppress"


# ────────────────────────────────────────────────────────────────────────
# Structural read → exfiltrate detector
# ────────────────────────────────────────────────────────────────────────


_READ_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"readFile",
        r"fs\.read",
        r"cat\s+",
        r"open\s*\(",
        r"read\s+.*file",
        r"load\s+.*config",
        r"read\s+.*\.env",
        r"fs\.readFileSync",
        r"readFileSync",
    )
)

_NET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"fetch\s*\(",
        r"axios",
        r"http\.request",
        r"https\.request",
        r"curl\s",
        r"wget\s",
        r"XMLHttpRequest",
        r"\.post\s*\(",
        r"send\s+.*to\s+http",
        r"POST\s+.*http",
    )
)


def _detect_structural_read_to_net(
    lines: list[str], cb_map: list[bool]
) -> list[dict[str, Any]]:
    read_lines: list[int] = []
    net_lines: list[int] = []
    for i, line in enumerate(lines):
        if cb_map[i] and _has_doc_context(lines, i, 8):
            continue
        if any(p.search(line) for p in _READ_PATTERNS):
            read_lines.append(i + 1)
        if any(p.search(line) for p in _NET_PATTERNS):
            net_lines.append(i + 1)
    real_read = [ln for ln in read_lines if not _is_instructional_context(lines, ln - 1)]
    real_net = [ln for ln in net_lines if not _is_instructional_context(lines, ln - 1)]
    if real_read and real_net:
        return [
            {
                "ruleId": "STRUCT_READ_EXFIL",
                "severity": "high",
                "category": "structural",
                "name": "Read → Network pattern detected",
                "description": (
                    f"Reads files (lines {','.join(map(str, real_read[:3]))}) and "
                    f"makes network requests (lines {','.join(map(str, real_net[:3]))}). "
                    "Potential data exfiltration flow."
                ),
                "line": real_read[0],
                "lineContent": lines[real_read[0] - 1].strip()[:200],
                "match": "structural",
                "suppressed": False,
            }
        ]
    return []


# ────────────────────────────────────────────────────────────────────────
# URL reputation
# ────────────────────────────────────────────────────────────────────────


_URL_RE = re.compile(r'https?://[^\s"\'<>\])}]+', re.IGNORECASE)
_RAW_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def _analyze_urls(lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        for match in _URL_RE.finditer(line):
            url = match.group(0)
            try:
                from urllib.parse import urlparse  # local import — std lib
                hostname = (urlparse(url).hostname or "").lower()
            except (ValueError, TypeError):
                continue
            if not hostname:
                continue
            for sd in SUSPICIOUS_DOMAINS:
                if hostname == sd or hostname.endswith(f".{sd}"):
                    findings.append(
                        {
                            "ruleId": "URL_SUSPICIOUS",
                            "severity": "high",
                            "category": "url_reputation",
                            "name": "Suspicious domain",
                            "description": f"URL points to known suspicious domain: {hostname}",
                            "line": i + 1,
                            "lineContent": line.strip()[:200],
                            "match": url[:100],
                            "suppressed": False,
                        }
                    )
            if _RAW_IP_RE.match(hostname):
                findings.append(
                    {
                        "ruleId": "URL_RAW_IP",
                        "severity": "medium",
                        "category": "url_reputation",
                        "name": "Raw IP address URL",
                        "description": f"URL uses raw IP address instead of domain: {hostname}",
                        "line": i + 1,
                        "lineContent": line.strip()[:200],
                        "match": url[:100],
                        "suppressed": False,
                    }
                )
    return findings


# ────────────────────────────────────────────────────────────────────────
# Invisible Unicode detector
# ────────────────────────────────────────────────────────────────────────


_INVISIBLE_CHARS: tuple[tuple[str, str], ...] = (
    ("​", "Zero-width space"),
    ("‌", "Zero-width non-joiner"),
    ("‍", "Zero-width joiner"),
    ("⁠", "Word joiner"),
    ("⁡", "Function application"),
    ("⁢", "Invisible times"),
    ("⁣", "Invisible separator"),
    ("⁤", "Invisible plus"),
    ("﻿", "Zero-width no-break space (BOM)"),
    ("­", "Soft hyphen"),
    ("͏", "Combining grapheme joiner"),
    ("؜", "Arabic letter mark"),
    ("᠎", "Mongolian vowel separator"),
    (" ", "Line separator"),
    (" ", "Paragraph separator"),
    ("‪", "LTR embedding"),
    ("‫", "RTL embedding"),
    ("‬", "Pop directional"),
    ("‭", "LTR override"),
    ("‮", "RTL override"),
)


def _detect_invisible_unicode(lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        for ch, name in _INVISIBLE_CHARS:
            if ch in line:
                count = line.count(ch)
                # BOM at line-0 / single occurrence is normal.
                if ch == "﻿" and i == 0 and count == 1:
                    continue
                findings.append(
                    {
                        "ruleId": "INVISIBLE_UNICODE_RAW",
                        "severity": "high",
                        "category": "obfuscation",
                        "name": f"Invisible character: {name}",
                        "description": (
                            f"Line contains {count} invisible {name} character(s) "
                            f"(U+{ord(ch):04X}). May hide malicious content from human review."
                        ),
                        "line": i + 1,
                        "lineContent": line.strip()[:200],
                        "match": f"U+{ord(ch):04X} x{count}",
                        "suppressed": False,
                    }
                )
    return findings


# ────────────────────────────────────────────────────────────────────────
# Intent patterns (natural language)
# ────────────────────────────────────────────────────────────────────────


_INTENT_PATTERNS: tuple[tuple[re.Pattern[str], str, str, str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), sev, name, desc)
    for pat, sev, name, desc in (
        (
            r"send\s+(the\s+)?(contents?|data|file|config|credentials?|secrets?|tokens?)\s+(of|from|to)\s",
            "high", "Exfiltration intent",
            "Instruction asks to send sensitive data externally",
        ),
        (
            r"upload\s+(the\s+)?(contents?|data|file|config|credentials?|secrets?)\s",
            "high", "Upload intent",
            "Instruction asks to upload sensitive data",
        ),
        (
            r"post\s+(the\s+)?(contents?|data|results?)\s+to\s+",
            "medium", "POST data intent",
            "Instruction asks to POST data to an endpoint",
        ),
        (
            r"read\s+.*\.(env|key|pem|secret|credentials)\b.*(?:send|post|upload|forward|transmit)",
            "critical", "Read-and-exfiltrate intent",
            "Instruction asks to read secrets then send them",
        ),
        (
            r"(?:forward|relay|copy)\s+.*(?:api.?key|token|password|secret|credential)\s+to\s",
            "critical", "Credential forwarding intent",
            "Instruction asks to forward credentials",
        ),
        (
            r"(?:delete|remove|wipe|destroy)\s+(all\s+)?(files?|data|logs?|history|memory)",
            "high", "Destructive intent",
            "Instruction asks to delete files or data",
        ),
        (
            r"install\s+.*(?:backdoor|rootkit|keylogger|trojan|malware)",
            "critical", "Malware install intent",
            "Instruction asks to install malicious software",
        ),
        (
            r"disable\s+.*(?:firewall|antivirus|security|logging|audit)",
            "critical", "Security disable intent",
            "Instruction asks to disable security measures",
        ),
        (
            r"connect\s+(?:back|reverse)\s+to",
            "high", "Reverse connection intent",
            "Instruction asks to connect back to attacker",
        ),
        (
            r"exfiltrate",
            "critical", "Explicit exfiltration",
            "Instruction explicitly mentions exfiltration",
        ),
    )
)


def _analyze_intent(lines: list[str], cb_map: list[bool]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if cb_map[i]:
            continue
        for pat, sev, name, desc in _INTENT_PATTERNS:
            m = pat.search(line)
            if m:
                findings.append(
                    {
                        "ruleId": "INTENT_" + re.sub(r"[^A-Z]", "_", name.upper()),
                        "severity": sev,
                        "category": "intent_analysis",
                        "name": name,
                        "description": desc,
                        "line": i + 1,
                        "lineContent": line.strip()[:200],
                        "match": m.group(0),
                        "suppressed": False,
                    }
                )
    return findings


# ────────────────────────────────────────────────────────────────────────
# Decoded-content threat scanner (base64 / hex / unicode-escape / charcode)
# ────────────────────────────────────────────────────────────────────────


_DECODED_THREATS: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), name, sev)
    for pat, name, sev in (
        (r"https?://\S+", "Hidden URL", "high"),
        (r"(?:curl|wget|fetch|axios|http\.request)\s", "Hidden network call", "critical"),
        (r"(?:eval|exec|system|spawn|Function)\s*\(", "Hidden code execution", "critical"),
        (r"(?:\.env|credentials|password|secret|token|api[_-]?key)", "Hidden credential reference", "high"),
        (r"(?:/bin/(?:ba)?sh|cmd\.exe|powershell)", "Hidden shell reference", "critical"),
        (r"(?:rm\s+-rf|del\s+/[fqs]|format\s+c:)", "Hidden destructive command", "critical"),
        (r"(?:webhook\.site|ngrok|requestbin|pipedream)", "Hidden exfiltration domain", "critical"),
        (r"(?:ignore\s+previous|ignore\s+all|new\s+instructions)", "Hidden prompt injection", "critical"),
        (r"(?:SELECT|INSERT|UPDATE|DELETE|DROP)\s+", "Hidden SQL", "high"),
        (r"<script[\s>]", "Hidden script tag", "high"),
        (r"(?:ssh|nc|ncat|socat)\s+", "Hidden network tool", "high"),
        (r"(?:PRIVATE KEY|BEGIN RSA|BEGIN EC)", "Hidden private key", "critical"),
    )
)


def _scan_decoded(
    decoded: str, encoding: str, line_idx: int, line_content: str
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for pat, name, sev in _DECODED_THREATS:
        m = pat.search(decoded)
        if m:
            findings.append(
                {
                    "ruleId": f"{encoding}_HIDDEN_" + re.sub(r"[^A-Z]", "_", name.upper()),
                    "severity": sev,
                    "category": "obfuscation",
                    "name": f"Obfuscated payload ({encoding.lower()}): {name}",
                    "description": (
                        f"{encoding}-encoded content contains {name.lower()}. "
                        f"Decoded match: \"{m.group(0)[:80]}\""
                    ),
                    "line": line_idx + 1,
                    "lineContent": line_content.strip()[:200],
                    "match": f"{encoding.lower()}→\"{decoded[:100]}\"",
                    "suppressed": False,
                }
            )
    return findings


_B64_RE = re.compile(r"""(?:['"`]|=\s*)([A-Za-z0-9+/]{40,}={0,2})(?:['"`]|$|\s)""")
_HEX_SEQ_RE = re.compile(r"(?:\\x[0-9a-fA-F]{2}){3,}")
_UNI_SEQ_RE = re.compile(r"(?:\\u[0-9a-fA-F]{4}){3,}")
_CHARCODE_RE = re.compile(r"String\.fromCharCode\s*\(\s*([\d,\s]+)\s*\)", re.IGNORECASE)
_ARR_CHARCODE_RE = re.compile(r"\[\s*(\d+(?:\s*,\s*\d+){2,})\s*\][\s.]*(?:map|forEach|reduce)", re.IGNORECASE)


def _printable_ratio(s: str) -> float:
    if not s:
        return 0.0
    printable = sum(1 for ch in s if 0x20 <= ord(ch) <= 0x7E or ch in "\n\r\t")
    return printable / len(s)


def _decode_and_scan_base64(lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if _has_placeholder(line):
            continue
        for m in _B64_RE.finditer(line):
            b64 = m.group(1)
            if re.fullmatch(r"[A-Fa-f0-9]+", b64):
                continue
            if re.match(r"^(?:data:image|iVBOR|AAAA|AQAB)", b64):
                continue
            try:
                decoded_bytes = base64.b64decode(b64, validate=False)
                decoded = decoded_bytes.decode("utf-8", errors="ignore")
            except (binascii.Error, ValueError):
                continue
            if _printable_ratio(decoded) < 0.7:
                continue
            findings.extend(_scan_decoded(decoded, "BASE64", i, line))
    return findings


def _decode_and_scan_escapes(lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if _has_placeholder(line):
            continue
        # Hex escapes \x41\x42…
        for seq in _HEX_SEQ_RE.findall(line):
            try:
                decoded = re.sub(
                    r"\\x([0-9a-fA-F]{2})",
                    lambda mm: chr(int(mm.group(1), 16)),
                    seq,
                )
            except ValueError:
                continue
            if _printable_ratio(decoded) >= 0.7:
                findings.extend(_scan_decoded(decoded, "HEX", i, line))
        # Unicode escapes AB…
        for seq in _UNI_SEQ_RE.findall(line):
            try:
                decoded = re.sub(
                    r"\\u([0-9a-fA-F]{4})",
                    lambda mm: chr(int(mm.group(1), 16)),
                    seq,
                )
            except ValueError:
                continue
            if _printable_ratio(decoded) >= 0.7:
                findings.extend(_scan_decoded(decoded, "UNICODE", i, line))
        # String.fromCharCode(…)
        for cc_match in _CHARCODE_RE.finditer(line):
            nums = re.findall(r"\d+", cc_match.group(0))
            if len(nums) >= 3:
                try:
                    decoded = "".join(chr(int(n)) for n in nums)
                except (ValueError, OverflowError):
                    continue
                if _printable_ratio(decoded) >= 0.7:
                    findings.extend(_scan_decoded(decoded, "CHARCODE", i, line))
        # Array of char codes: [99,117,114,108].map(...)
        for arr_match in _ARR_CHARCODE_RE.finditer(line):
            nums = re.findall(r"\d+", arr_match.group(0))
            if len(nums) >= 3 and all(32 <= int(n) <= 126 for n in nums):
                try:
                    decoded = "".join(chr(int(n)) for n in nums)
                except (ValueError, OverflowError):
                    continue
                findings.extend(_scan_decoded(decoded, "CHARCODE_ARRAY", i, line))
    return findings


# ────────────────────────────────────────────────────────────────────────
# Hardcoded-secret detector (port of src/secrets.js)
# ────────────────────────────────────────────────────────────────────────


_SECRET_DETECTORS: tuple[tuple[str, str, str, re.Pattern[str], str], ...] = tuple(
    (id_, name, description, re.compile(pat), severity)
    for id_, name, description, pat, severity in (
        (
            "SECRET_GITHUB_TOKEN", "Hardcoded GitHub token",
            "GitHub personal access token (ghp_/gho_/ghu_/ghs_/ghr_) embedded in source",
            r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b",
            "critical",
        ),
        (
            "SECRET_AWS_KEY", "Hardcoded AWS access key",
            "AWS access-key-id (AKIA…) embedded in source",
            r"\b(AKIA|ASIA)[A-Z0-9]{16}\b",
            "critical",
        ),
        (
            "SECRET_SLACK_TOKEN", "Hardcoded Slack token",
            "Slack token (xoxb-/xoxa-/xoxp-/xoxr-/xoxs-) embedded in source",
            r"\b(xox[bapors])-[A-Za-z0-9-]{10,200}\b",
            "critical",
        ),
        (
            "SECRET_SLACK_WEBHOOK", "Slack webhook URL",
            "Hardcoded Slack incoming-webhook URL — can post messages to channels",
            r"https://hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{20,}",
            "high",
        ),
        (
            "SECRET_DISCORD_TOKEN", "Hardcoded Discord bot token",
            "Discord bot token embedded in source",
            r"\b[MN][A-Za-z0-9]{23}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,38}\b",
            "critical",
        ),
        (
            "SECRET_DISCORD_WEBHOOK", "Discord webhook URL",
            "Hardcoded Discord webhook — can post messages to channels",
            r"https://discord(?:app)?\.com/api/webhooks/\d{17,}/[A-Za-z0-9_-]{60,}",
            "high",
        ),
        (
            "SECRET_TELEGRAM_TOKEN", "Telegram bot token",
            "Telegram bot token embedded in source",
            r"\b\d{8,12}:[A-Za-z0-9_-]{35}\b",
            "critical",
        ),
        (
            "SECRET_VERCEL_TOKEN", "Vercel access token",
            "Vercel API token (vercel_…) embedded in source",
            r"\bvercel_[A-Za-z0-9]{24,40}\b",
            "critical",
        ),
        (
            "SECRET_NPM_TOKEN", "npm access token",
            "npm token (npm_… or 36-hex UUID-style) embedded in source",
            r"\bnpm_[A-Za-z0-9]{30,}\b",
            "critical",
        ),
        (
            "SECRET_PYPI_TOKEN", "PyPI API token",
            "PyPI token (pypi-…) embedded in source",
            r"\bpypi-[A-Za-z0-9]{30,}\b",
            "critical",
        ),
        (
            "SECRET_PRIVATE_KEY", "PEM private key",
            "PEM-formatted private key block embedded in source",
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY-----",
            "critical",
        ),
        (
            "SECRET_JWT", "Hardcoded JWT",
            "JSON Web Token (header.payload.signature) embedded in source",
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
            "high",
        ),
        (
            "SECRET_OPENAI_KEY", "OpenAI API key",
            "OpenAI key (sk-… / sk-proj-…) embedded in source",
            r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b",
            "critical",
        ),
        (
            "SECRET_ANTHROPIC_KEY", "Anthropic API key",
            "Anthropic key (sk-ant-…) embedded in source",
            r"\bsk-ant-[A-Za-z0-9_-]{20,}\b",
            "critical",
        ),
        (
            "SECRET_GOOGLE_API_KEY", "Google API key",
            "Google API key (AIza…) embedded in source",
            r"\bAIza[A-Za-z0-9_-]{32,40}\b",
            "high",
        ),
        (
            "SECRET_STRIPE_KEY", "Stripe API key",
            "Stripe live secret key (sk_live_… / rk_live_…) embedded in source",
            r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}\b",
            "critical",
        ),
    )
)


def _detect_secrets(lines: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if _has_placeholder(line):
            continue
        for id_, name, desc, pat, sev in _SECRET_DETECTORS:
            m = pat.search(line)
            if m:
                findings.append(
                    {
                        "ruleId": id_,
                        "severity": sev,
                        "category": "credential_theft",
                        "name": name,
                        "description": desc,
                        "line": i + 1,
                        "lineContent": line.strip()[:200],
                        "match": m.group(0)[:80],
                        "suppressed": False,
                    }
                )
    return findings


# ────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SkillAuditFinding:
    """One normalised finding from the native skillaudit scan."""

    severity: str  # CPV-canonical: critical/major/minor/nit/info
    rule_id: str
    message: str
    file_path: str
    line_number: int | None
    category: str = ""  # skillaudit threat category (e.g. "credential_theft")
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillAuditScanResult:
    """Aggregate result of a native skillaudit scan over a plugin tree."""

    invoked: bool
    findings: tuple[SkillAuditFinding, ...]
    skipped_reason: str = ""
    files_scanned: int = 0


# ────────────────────────────────────────────────────────────────────────
# Per-content + per-tree entry points
# ────────────────────────────────────────────────────────────────────────


def scan_content(content: str, file_path: str = "") -> list[dict[str, Any]]:
    """Run the full skillaudit native scan on a single string of content.

    Mirrors src/scanner.js::scanContent. Returns a list of finding dicts
    in the skillaudit shape (ruleId / severity / category / line / etc.).
    The caller is responsible for mapping to CPV's severity model and
    relativising file paths.
    """
    if not content:
        return []
    lines = content.split("\n")
    cb_map, cb_ranges = _build_code_block_map(lines)
    # v2.99.1 — per-language docstring tracker (Python triple-quoted
    # blocks). Used to demote matches inside prose documentation.
    py_doc_map = _build_py_docstring_map(lines, file_path)

    findings: list[dict[str, Any]] = []

    # 1. Rule-based pattern matching with confidence-based severity
    # adjustment + code-block uplift. Per user guidance ("better safe
    # than sorry, the agents will verify"), we use a three-way
    # classifier instead of a binary suppress/keep: hard-suppress only
    # for placeholder-driven matches, demote-to-WARNING for plausible
    # documentation context, keep for high-confidence threats.
    for rule, compiled_pats in _compiled_rules():
        rule_id = rule.get("id", "RULE_UNKNOWN")
        rule_sev = rule.get("severity", "medium")
        rule_cat = rule.get("category", "rule")
        rule_name = rule.get("name", rule_id)
        rule_desc = rule.get("description", "")
        for pat in compiled_pats:
            for i, line in enumerate(lines):
                m = pat.search(line)
                if not m:
                    continue
                in_cb = cb_map[i]
                lang = _code_block_lang(cb_ranges, i) or ""
                verdict = _confidence(
                    lines, i, m.group(0), rule_id, cb_map, cb_ranges,
                    py_doc_map=py_doc_map, file_path=file_path,
                )
                suppressed = verdict == "suppress"
                demoted = verdict == "demote"
                # Demoted findings stay visible — emitted at "low" so the
                # CPV severity mapping renders them as NIT, which routes
                # to the security agents' WARNING bucket for LLM-based
                # disambiguation rather than being silently dropped.
                if suppressed:
                    adj_sev = "info"
                elif demoted:
                    adj_sev = "low"
                else:
                    adj_sev = rule_sev
                # Severity uplift inside executable code blocks
                # (shell-class fences) — only when the match wasn't
                # demoted/suppressed AND the host file is not pure
                # documentation. v2.99.1: bash fences inside .md files
                # are install / usage examples (legitimate or
                # documented threats); promote to "high" instead of
                # all the way to "critical", and keep the demote path
                # open so the agents triage.
                if verdict == "keep" and in_cb and lang in ("bash", "sh", "shell", "zsh"):
                    in_md = file_path.lower().endswith(".md")
                    if in_md:
                        # Documentation context — single-step uplift, no
                        # critical floor.
                        if adj_sev == "medium":
                            adj_sev = "high"
                    else:
                        if adj_sev == "medium":
                            adj_sev = "high"
                        elif adj_sev == "high":
                            adj_sev = "critical"
                findings.append(
                    {
                        "ruleId": rule_id,
                        "severity": adj_sev,
                        "category": rule_cat,
                        "name": rule_name,
                        "description": rule_desc,
                        "line": i + 1,
                        "lineContent": line.strip()[:200],
                        "match": m.group(0),
                        "suppressed": suppressed,
                        "demoted": demoted,
                    }
                )

    # 2-8. Secondary scanners that bypass the per-rule _confidence
    # loop. v2.100.0 (TRDD-a4260cc6): wrap each with the context
    # classifier so .md prose / JSON description fields / hardcoded-
    # literal Python don't produce phantom INTENT_DESTRUCTIVE_INTENT /
    # INTENT_EXPLICIT_EXFILTRATION / SSRF / etc.
    secondary_findings: list[dict[str, Any]] = []
    secondary_findings.extend(_detect_structural_read_to_net(lines, cb_map))
    secondary_findings.extend(_analyze_urls(lines))
    secondary_findings.extend(_analyze_intent(lines, cb_map))
    secondary_findings.extend(_detect_secrets(lines))
    secondary_findings.extend(_detect_invisible_unicode(lines))
    secondary_findings.extend(_decode_and_scan_base64(lines))
    secondary_findings.extend(_decode_and_scan_escapes(lines))

    # Apply per-file-type context classification to every secondary
    # finding. The verdict maps the same way as in the primary loop:
    #   suppress → drop entirely (mark severity=info, suppressed=True
    #              for traceability)
    #   demote   → emit at "low" with demoted=True
    #   keep     → emit at the scanner's declared severity
    for sf in secondary_findings:
        line_num = int(sf.get("line", 1))
        line_idx = max(0, line_num - 1)
        match_text = str(sf.get("match", "")) or str(sf.get("lineContent", ""))
        rule_id = str(sf.get("ruleId", ""))
        verdict = _context_classifier_verdict(file_path, lines, line_idx, match_text, rule_id)
        if verdict == "suppress":
            # Tag as suppressed so the renderer can drop it. Keep the
            # info-severity copy in case a future caller wants to audit
            # what was suppressed.
            sf["severity"] = "info"
            sf["suppressed"] = True
        elif verdict == "demote":
            sf["severity"] = "low"
            sf["demoted"] = True
        # verdict "" or "keep" → leave severity alone.
        findings.append(sf)

    # Dedupe by (ruleId, line).
    seen: set[tuple[str, int]] = set()
    deduped: list[dict[str, Any]] = []
    for f in findings:
        key = (f.get("ruleId", ""), int(f.get("line", 0)))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)

    # Attach the file path for downstream consumers.
    for f in deduped:
        f.setdefault("file", file_path)

    return deduped


# Files inside a plugin tree that are worth feeding to the scanner.
_SCAN_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".sh", ".bash", ".zsh", ".fish", ".py", ".js", ".ts", ".mjs", ".cjs", ".json", ".yaml", ".yml", ".toml"}
)

# Directories the walker MUST skip — vendored deps / VCS / build cruft.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".trashcan",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".cache",
        "docs_dev",
        "scripts_dev",
        "samples_dev",
        "examples_dev",
        "tests_dev",
        "downloads_dev",
        "libs_dev",
        "builds_dev",
        "reports_dev",
        "reports",
    }
)


def _iter_scannable_files(plugin_root: Path) -> Iterable[Path]:
    """Yield candidate files under plugin_root, skipping vendored / build dirs
    AND any path the plugin's `.gitignore` would exclude (issue #37).

    Plugins that keep research material, vendored reference repos, or
    training fixtures in a gitignored sub-tree (e.g. `INPUT_DEV/_extracted/`,
    `_research/`, `samples/`) would otherwise see CPV scan content that is
    not part of the published artefact, surface findings against it, and
    block publish.

    The filter is layered on top of the existing rglob walker — we keep
    scanning dot-prefixed Claude-plugin directories (`.claude-plugin/`,
    `.claude/`, `.github/`) which are first-class content, and only
    additionally skip whatever git would consider ignored. Pure-Python
    pattern matching via the existing `parse_gitignore` helper, so
    SkillAudit's zero-subprocess design contract is preserved.
    """
    if not plugin_root.is_dir():
        if plugin_root.is_file() and plugin_root.suffix.lower() in _SCAN_EXTENSIONS:
            yield plugin_root
        return
    # Build the gitignore predicate once — pure-Python pattern matching,
    # no subprocess. Used to filter each candidate path below.
    gi_predicate = _load_gitignore_predicate(plugin_root)
    for p in plugin_root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in _SCAN_EXTENSIONS:
            continue
        # Issue #37 — skip anything the plugin's .gitignore excludes.
        # Applied AFTER _SKIP_DIRS / extension filters because most
        # files won't be ignored and a cheap negative path is preferred.
        if gi_predicate is not None and gi_predicate(p):
            continue
        try:
            if p.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        yield p


def _load_gitignore_predicate(plugin_root: Path) -> Callable[[Path], bool] | None:
    """Compile the plugin's .gitignore into a `bool(Path) -> ignored?` predicate.

    Returns None if no .gitignore exists or the helper module cannot be
    imported (defensive — fall back to scanning everything is safer
    than silently dropping findings).

    Pure-Python: parses the file once, matches each candidate path with
    `is_path_gitignored`. No subprocess; SkillAudit's design contract
    holds (issue #37).
    """
    gitignore_path = plugin_root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    try:
        from cpv_validation_common import is_path_gitignored, parse_gitignore  # noqa: PLC0415
    except ImportError:
        return None
    try:
        patterns = parse_gitignore(gitignore_path)
    except (OSError, ValueError):
        return None
    if not patterns:
        return None

    def predicate(path: Path) -> bool:
        try:
            rel = path.relative_to(plugin_root).as_posix()
        except ValueError:
            return False
        # Filesystem-aware: query with trailing slash for directories so
        # dir-only patterns (`/INPUT_DEV/`, `node_modules/`) match
        # correctly via pathspec's gitwildmatch (issue #37).
        if is_path_gitignored(rel, patterns):
            return True
        try:
            if path.is_dir() and is_path_gitignored(rel.rstrip("/") + "/", patterns):
                return True
        except OSError:
            pass
        return False

    return predicate


def scan_path(plugin_root: Path) -> tuple[list[dict[str, Any]], int]:
    """Walk plugin_root, scan each scannable file. Returns (findings, files_scanned)."""
    all_findings: list[dict[str, Any]] = []
    files_scanned = 0
    for fp in _iter_scannable_files(plugin_root):
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        files_scanned += 1
        if not content:
            continue
        rel = str(fp)
        try:
            rel = str(fp.relative_to(plugin_root))
        except ValueError:
            pass
        for f in scan_content(content, rel):
            f["file"] = rel
            all_findings.append(f)
    return all_findings, files_scanned


def run_skillaudit_scan(plugin_path: Path) -> SkillAuditScanResult:
    """Top-level entry point for validate_security.py Check 27.

    NEVER raises and NEVER honours a skip env var. If the scan crashes
    on a specific file, the rest of the tree still gets scanned (we
    catch per-file OSError + UnicodeError internally).
    """
    if not _get_rules():
        return SkillAuditScanResult(
            invoked=False,
            findings=(),
            skipped_reason=(
                f"skillaudit rule catalog missing at {_RULES_PATH}; "
                "this is a CPV install integrity issue, not a runtime opt-out"
            ),
        )
    raw_findings, files_scanned = scan_path(plugin_path)
    # Filter out suppressed findings (informational, not actionable).
    actionable: list[SkillAuditFinding] = []
    for f in raw_findings:
        if f.get("suppressed"):
            continue
        sev = _to_cpv_severity(str(f.get("severity", "medium")))
        actionable.append(
            SkillAuditFinding(
                severity=sev,
                rule_id=str(f.get("ruleId", "skillaudit.unknown")),
                message=str(f.get("name", "") or f.get("description", "")),
                file_path=str(f.get("file", "")),
                line_number=int(f["line"]) if isinstance(f.get("line"), int) and f["line"] > 0 else None,
                category=str(f.get("category", "")),
                raw=f,
            )
        )
    return SkillAuditScanResult(
        invoked=True,
        findings=tuple(actionable),
        skipped_reason="",
        files_scanned=files_scanned,
    )


# ────────────────────────────────────────────────────────────────────────
# Report adapter — wires findings into ValidationReport
# ────────────────────────────────────────────────────────────────────────


def report_findings(
    result: SkillAuditScanResult,
    plugin_path: Path,
    report: Any,
    should_skip: "Callable[[str, int | None], bool] | None" = None,
) -> int:
    """Adapt a SkillAuditScanResult into ValidationReport.<severity>(...) calls.

    Iron-rule preservation: when ``result.invoked is False`` (rule catalog
    missing from the install — a CPV packaging defect, NOT a user
    opt-out), this function emits a CRITICAL finding so the validation
    still fails fast.
    """
    if not result.invoked:
        report.critical(
            f"SkillAudit native scan could not run — {result.skipped_reason}. "
            "Reinstall CPV — this is a packaging integrity issue.",
            "<skillaudit-native>",
        )
        return 1

    appended = 0
    for finding in result.findings:
        line = finding.line_number
        rel = _relativise(finding.file_path, plugin_path)
        if should_skip is not None and should_skip(finding.file_path or rel, line):
            continue
        # v2.99.1 — embed threat category so reviewers see the threat
        # domain at a glance (skillaudit ships 21 categories that CPV
        # didn't have before: credential_theft, crypto_theft,
        # data_exfiltration, prompt_injection, supply_chain, etc.).
        category = finding.category or "unknown"
        is_demoted = bool(finding.raw.get("demoted"))
        # Demoted matches get a ⚠ marker so reviewers (and downstream
        # security agents) see they need disambiguation.
        prefix = f"[skillaudit:{category} {finding.rule_id}]"
        if is_demoted:
            prefix = f"⚠ {prefix} (demoted, needs review)"
        message = f"{prefix} {finding.message}".strip()
        if finding.severity == "info":
            report.info(message, rel)
        else:
            method = getattr(report, finding.severity, None) or report.minor
            method(message, rel, line)
        appended += 1
    return appended


def _relativise(file_path: str, plugin_root: Path) -> str:
    if not file_path:
        return "<unknown>"
    candidate = Path(file_path)
    try:
        return str(candidate.relative_to(plugin_root))
    except ValueError:
        return file_path


# ────────────────────────────────────────────────────────────────────────
# Helpers exposed for testing
# ────────────────────────────────────────────────────────────────────────


def _content_hash(content: str) -> str:
    """SHA-256 of content — exposed so tests can pin the hash format."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalize_unicode_for_test(text: str) -> str:
    """Normalize to NFC so the invisible-char scanner can be tested deterministically."""
    return unicodedata.normalize("NFC", text)
