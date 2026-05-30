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
import os
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
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
    # Task #384 (Agent B1) — parallel-scan worker + helpers exposed
    # for tests/test_skillaudit_native_parallelism.py. The worker is
    # top-level (pickleable) and the env-var resolvers are public so
    # tests can monkeypatch them per-test without reloading the module.
    "_scan_one_file_skillaudit",
    "_parallel_enabled",
    "_parallel_threshold",
    # v2.104.0 (J5) — module version + catalog hash + feature
    # resolvers exposed so tests can pin them and so downstream
    # consumers (the cache module itself) can read the canonical
    # version constant rather than re-parse plugin.json.
    "__version__",
    "_CATALOG_HASH",
    "_cache_enabled",
    "_cache_deep_enabled",
    "_binary_enabled",
    "_re2_disabled",
    "_hybrid_matcher",
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


# Severity ordering for the skillaudit-internal severity vocabulary
# (NOT the CPV severity model — these are the raw rule/catalog levels
# scan_content emits before CPV maps them). Used by the (ruleId, line)
# dedup to keep the strongest finding when a catalog rule and a secondary
# scanner collide on one key (audit MINOR #4).
_SEVERITY_RANK: dict[str, int] = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def _severity_rank(severity: str) -> int:
    """Return a sortable rank for a skillaudit severity string.

    Unknown / empty severities rank below ``info`` so a well-formed
    finding always wins over a malformed one in the dedup.
    """
    return _SEVERITY_RANK.get(severity.lower(), -1)


# ────────────────────────────────────────────────────────────────────────
# v2.104.0 — module version + catalog hash + opt-in helper modules
# ────────────────────────────────────────────────────────────────────────
#
# `__version__` is bumped in lockstep with plugin.json. Bumping it
# invalidates every entry in the scan cache (the cache key includes
# the engine version), so a release that changes the scanning logic
# never serves stale findings from a previous version.
#
# `_CATALOG_HASH` is computed ONCE per process at import time and
# included in every cache key. Mutating the rule catalog on disk
# therefore invalidates the cache for that scan even if the engine
# version is unchanged — the same file content scanned against a
# different rule set must produce a fresh scan.
#
# The 3 helper modules below are LAZY-IMPORTED with graceful fallback
# (module-level try/except). If a module is missing, its feature is
# silently disabled — the legacy code path runs unchanged. This means:
#   - the module imports cleanly even on partial deployments where
#     J1/J2/J3 haven't shipped yet;
#   - the test_module_imports_only_stdlib guard in
#     tests/test_skillaudit_native.py keeps passing (lazy imports
#     inside try/except aren't flagged by the top-level regex walk
#     that gate uses);
#   - env-var opt-outs (CPV_SCAN_CACHE=0, CPV_BINARY_SCAN=0,
#     CPV_RE2_DISABLE=1) all funnel through the cached_enabled() /
#     binary_enabled() / hybrid_matcher() resolvers so per-test
#     monkeypatch.setenv works without reloading the module.


__version__ = "2.110.1"  # bumped in lockstep with plugin.json by publish.py


def _compute_catalog_hash() -> str:
    """Return SHA-256 of the rule catalog file, or empty string if missing.

    Computed ONCE per process. The cache key for a scan is
    ``(content_hash, _CATALOG_HASH, __version__)`` — when the catalog
    changes on disk between runs, the next scan is a cache miss even
    if the file content is unchanged. Returning the empty string when
    the catalog is absent (a packaging defect that
    ``run_skillaudit_scan`` already CRITICALs on) lets the constant
    initialise without raising at module-import time.
    """
    if not _RULES_PATH.is_file():
        return ""
    try:
        return hashlib.sha256(_RULES_PATH.read_bytes()).hexdigest()
    except OSError:
        return ""


_CATALOG_HASH: str = _compute_catalog_hash()


# Lazy imports of the 3 opt-in helper modules. Each import is wrapped
# in its own try/except so a missing module disables ONLY that feature
# rather than cascading. Tests can monkeypatch the resolver functions
# below to flip features on/off per-test without re-importing.

try:
    from cpv_scan_cache import (  # noqa: PLC0415
        get_cached_findings as _scan_cache_get,
    )
    from cpv_scan_cache import (
        put_cached_findings as _scan_cache_put,
    )

    _CACHE_AVAILABLE = True
except ImportError:  # pragma: no cover — feature absent
    _scan_cache_get = None  # type: ignore[assignment]
    _scan_cache_put = None  # type: ignore[assignment]
    _CACHE_AVAILABLE = False

try:
    from cpv_binary_scanner import BINARY_PREFIX as _BINARY_PREFIX  # noqa: PLC0415
    from cpv_binary_scanner import is_binary as _binary_is_binary
    from cpv_binary_scanner import scan_binary as _binary_scan_binary

    _BINARY_AVAILABLE = True
except ImportError:  # pragma: no cover — feature absent
    _binary_is_binary = None  # type: ignore[assignment]
    _binary_scan_binary = None  # type: ignore[assignment]
    _BINARY_PREFIX = "[extracted from binary] "
    _BINARY_AVAILABLE = False

try:
    # HybridMatcher is built lazily on first use because its
    # constructor walks the entire rule catalog to compile patterns.
    # We import the class here but only instantiate when first
    # requested via _hybrid_matcher().
    from cpv_re2_matcher import HybridMatcher as _HybridMatcherCls  # noqa: PLC0415

    _RE2_AVAILABLE = True
except ImportError:  # pragma: no cover — feature absent
    _HybridMatcherCls = None  # type: ignore[assignment,misc]
    _RE2_AVAILABLE = False


def _cache_enabled() -> bool:
    """Return True iff the cache module is available AND CPV_SCAN_CACHE != '0'.

    Resolved at call time so tests can ``monkeypatch.setenv`` per-test.
    """
    if not _CACHE_AVAILABLE:
        return False
    return os.environ.get("CPV_SCAN_CACHE", "1") != "0"


def _cache_deep_enabled() -> bool:
    """Return True iff CPV_SCAN_CACHE_DEEP=1 (skip GET, still PUT).

    Deep mode forces every scan to actually run (cache miss) but still
    writes results back to the cache. Used to refresh the cache after
    a catalog or engine change without paying the cost of clearing it.
    """
    return os.environ.get("CPV_SCAN_CACHE_DEEP", "0") == "1"


def _binary_enabled() -> bool:
    """Return True iff the binary scanner is available AND not opted out.

    Default ON when available. CPV_BINARY_SCAN=0 disables it; the
    legacy behaviour (skip binary files via extension filter) returns.
    """
    if not _BINARY_AVAILABLE:
        return False
    return os.environ.get("CPV_BINARY_SCAN", "1") != "0"


def _re2_disabled() -> bool:
    """Return True iff CPV_RE2_DISABLE=1 (force Python re fallback).

    Used for debugging differences between the Python re engine and
    the RE2 hybrid matcher.
    """
    return os.environ.get("CPV_RE2_DISABLE", "0") == "1"


_HYBRID_MATCHER: Any = None
_HYBRID_MATCHER_INIT_FAILED = False


def _hybrid_matcher() -> Any:
    """Return the lazily-constructed HybridMatcher, or None.

    Returns None when the module isn't available, when CPV_RE2_DISABLE
    is set, or when construction raised (we cache the failure so we
    don't retry every scan).

    HybridMatcher expects ``patterns: dict[str, str]`` (rule_id →
    pattern source). Our rule catalog has multiple patterns per rule —
    we flatten with synthetic per-pattern keys ``"<rule_id>#<idx>"``
    so HybridMatcher can route each pattern independently, and the
    caller maps the keys back to rule_ids by splitting on ``"#"``.
    """
    global _HYBRID_MATCHER, _HYBRID_MATCHER_INIT_FAILED
    if not _RE2_AVAILABLE or _HYBRID_MATCHER_INIT_FAILED:
        return None
    if _re2_disabled():
        return None
    if _HYBRID_MATCHER is not None:
        return _HYBRID_MATCHER
    if _HybridMatcherCls is None:
        # RE2 import failed (the `_RE2_AVAILABLE` guard above normally covers
        # this, but pyright can't connect that bool to the class binding).
        _HYBRID_MATCHER_INIT_FAILED = True
        return None
    try:
        # Build the flattened {rule_id#idx: pattern_source} dict.
        # Skip non-string patterns and malformed entries — same
        # tolerance as _compiled_rules().
        flat: dict[str, str] = {}
        for rule in _get_rules():
            rid = str(rule.get("id", "RULE_UNKNOWN"))
            patterns = rule.get("patterns") or []
            for idx, pat in enumerate(patterns):
                if isinstance(pat, str) and pat:
                    flat[f"{rid}#{idx}"] = pat
        if not flat:
            _HYBRID_MATCHER_INIT_FAILED = True
            return None
        _HYBRID_MATCHER = _HybridMatcherCls(flat)
    except Exception:  # pragma: no cover — defensive
        _HYBRID_MATCHER_INIT_FAILED = True
        return None
    return _HYBRID_MATCHER


def _prefilter_rule_ids(text: str) -> frozenset[str] | None:
    """Return the set of catalog rule_ids whose pattern matches ``text``
    somewhere, computed in ONE RE2 ``Set`` pass — or ``None`` when the
    fast matcher is unavailable / disabled (the caller then runs every
    rule, exactly as before).

    This is the wiring that finally delivers the module's advertised
    O(N_text) speedup (audit MAJOR #1). It is used as a PRE-FILTER, not a
    replacement: the expensive per-line Python ``re`` loop in
    ``scan_content`` still runs — but ONLY for the (usually small) subset
    of rules that the single-pass matcher proved can match. Rules with
    zero matches anywhere in the file are skipped entirely.

    Correctness contract (why this changes NOTHING about the findings):

    * The matcher compiles every pattern case-INSENSITIVE (``_case_insensitive``),
      the same as the live ``_compiled_rules`` IGNORECASE loop, so the
      pre-filter never excludes a rule the per-line loop would have hit.
    * ``RE2::Set::Match`` over the WHOLE text is a superset gate: if a
      pattern matches any single line, it also matches the joined text
      (line boundaries only ADD ``\n`` characters, never remove a match)
      — UNLESS the pattern is anchored to start/end-of-string in a way
      that behaves differently on a multi-line blob. To stay sound we
      compile the matcher per-line-agnostic and fall back to running ALL
      rules whenever the matcher is unavailable; we never DROP a rule on
      the basis of a non-match for a multiline-anchored pattern because
      such patterns would be in the result set anyway (``re2`` matches
      ``^``/``$`` against the blob's line boundaries by default, matching
      Python ``re`` line semantics under the same input).
    * The flattened keys are ``"<rule_id>#<idx>"``; we split on the LAST
      ``"#"`` so rule_ids that themselves contain ``#`` survive.

    Returns ``None`` (NOT an empty set) when the matcher can't run, so the
    caller distinguishes "no pre-filter available → run everything" from
    "pre-filter ran and matched nothing → run nothing".
    """
    matcher = _hybrid_matcher()
    if matcher is None:
        return None
    # ReDoS guard (issue #53 follow-up): a whole-blob ``scan()`` is only cheap
    # when a compiled RE2 Set backs it (linear matching). When google-re2 is
    # absent — exactly the CI configuration, where ``uv sync`` installs only
    # base deps and skips the optional ``performance`` extra — ``scan()`` runs
    # EVERY catalog pattern through the Python ``re`` fallback over the entire
    # unbounded blob. The catalog's chained-``.*`` rules (notably
    # ``A2A_CAPABILITY_ABUSE`` with 4 chained ``.*`` then a required literal)
    # then backtrack exponentially on a long non-matching line and the process
    # hangs (CI 15-min timeout). In that state the pre-filter also delivers
    # ZERO speedup (there is no RE2 Set to fast-skip rules), so the correct
    # behaviour is the documented legacy path: return ``None`` → the caller
    # runs every rule through its own per-line loop, which IS length-bounded by
    # ``_MAX_SCAN_LINE``. This keeps the all-Python-``re`` fallback ReDoS-safe
    # on its own without making google-re2 a hard dependency.
    if not matcher.has_re2_set:
        return None
    try:
        pairs = matcher.scan(text)
    except Exception:  # pragma: no cover — matcher must never break a scan
        return None
    rule_ids: set[str] = set()
    for flat_key, _match in pairs:
        # Flattened key is "<rule_id>#<idx>" — rsplit so a rule_id that
        # itself contains '#' is preserved.
        rule_ids.add(flat_key.rsplit("#", 1)[0])
    return frozenset(rule_ids)


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
        "github.com",
        "raw.githubusercontent.com",
        "gist.github.com",
        "npmjs.com",
        "registry.npmjs.org",
        "unpkg.com",
        "pypi.org",
        "crates.io",
        "rubygems.org",
        "stackoverflow.com",
        "developer.mozilla.org",
        "google.com",
        "googleapis.com",
        "cloudflare.com",
        "vercel.app",
        "netlify.app",
        "heroku.com",
        "docker.io",
        "hub.docker.com",
        "openai.com",
        "anthropic.com",
        "huggingface.co",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "medium.com",
        "dev.to",
        "hashnode.dev",
        "wikipedia.org",
        "wikimedia.org",
        "cdn.jsdelivr.net",
        "cdnjs.cloudflare.com",
    }
)

SUSPICIOUS_DOMAINS: frozenset[str] = frozenset(
    {
        "webhook.site",
        "requestbin.com",
        "pipedream.net",
        "ngrok.io",
        "ngrok-free.app",
        "burpcollaborator.net",
        "interact.sh",
        "oastify.com",
        "hookbin.com",
        "postb.in",
        "rbndr.us",
        "1u.ms",
        "nip.io",
        "xip.io",
        "pastebin.com",
        "transfer.sh",
        "file.io",
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
        # Canonical AWS DOCUMENTATION example credentials. AWS reserves the
        # ``EXAMPLE`` suffix for its docs (``AKIAIOSFODNN7EXAMPLE`` and the
        # secret ``wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`` appear in
        # millions of tutorials). A real access-key-id ending in exactly
        # ``EXAMPLE`` is astronomically improbable, so the suffix is a
        # 100%-certain documentation-placeholder signal.
        r"(?:AKIA|ASIA)[A-Z0-9]{8,}EXAMPLE\b",
        r"wJalrXUtnFEMI",
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
        # audit NIT #11: ``generate`` / ``guide`` / ``overview`` removed —
        # they are extremely common in legitimate code comments and
        # surrounding prose, so a ±5-line window around them over-demoted
        # CRED_ENV_READ / TOKEN_STEAL findings near the everyday word
        # "generate" ("We generate output here" one line from a real
        # ``os.environ['SECRET_TOKEN']``). The remaining triggers are
        # either multi-word doc phrases or unambiguous doc nouns.
        r"\bsave\s+your\b",
        r"\bstore\s+your\b",
        r"\bset\s+your\b",
        r"\badd\s+your\b",
        r"\bget\s+your\b",
        r"\bcreate\s+your\b",
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


def _code_block_has_placeholder(lines: list[str], ranges: list[_CodeBlockRange], line_idx: int) -> bool:
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
        "CRED_ENV_READ",
        "TOKEN_STEAL",
        "CRED_ENV_SAFE",
        "CMD_INJECTION",
        "SHELL_EXEC",
        "REVERSE_SHELL",
        "SUPPLY_CHAIN",
        "FS_WRITE",
        "FS_READ",
        "FS_RECURSIVE_RM",
        "SSRF_PATTERN",
        "NET_SUSPICIOUS",
        "DNS_REBIND",
        "INSECURE_CRYPTO",
        "OBFUSCATION",
        "REGEX_DOS",
        "INDIRECT_PROMPT_INJECT",
        "PROMPT_INJECT",
        "MCP_SCHEMA_POISON",
        "TOOL_POISONING",
        "A2A_AGENT_IMPERSONATION",
        "A2A_TASK_HIJACK",
        "A2A_CROSS_AGENT_INJECT",
        "A2A_DATA_LEAK",
        "A2A_CAPABILITY_ABUSE",
        "PERSISTENCE",
        "PRIVILEGE_ESC",
        "CONTAINER_ESCAPE",
        "ENV_RECON",
        "RESOURCE_ABUSE",
        "AGENT_MEMORY_MOD",
        "TOOL_SHADOW",
        "CROSS_TOOL_ACCESS",
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
        line.lstrip().startswith(("#!/usr/bin/env python", "#!/usr/bin/python")) for line in lines[:1]
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
    # Python / shell / YAML / TOML / make / Ruby — `#` line comments
    if (
        suffix.endswith((".py", ".sh", ".bash", ".zsh", ".fish", ".yml", ".yaml", ".toml", ".ini", ".conf", ".rb"))
        or suffix == "makefile"
    ):
        return stripped.startswith("#")
    # PHP supports BOTH `#` and C-style `//` `/* */` comments. (audit NIT #17)
    if suffix.endswith(".php"):
        return stripped.startswith(("#", "//", "/*", "*"))
    # JS / TS / Java / Go / C / C++ / Rust — C-style comments
    if suffix.endswith((".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx", ".java", ".go", ".c", ".cpp", ".cc", ".rs")):
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
_EXECUTION_CLASS_RULES: frozenset[str] = frozenset(
    {
        "CMD_INJECTION",
        "SHELL_EXEC",
        "REVERSE_SHELL",
        "OBFUSCATION",
        "PRIVILEGE_ESC",
        "TIME_BOMB",
        "INSECURE_CRYPTO",
        "REGEX_DOS",
        "MCP_SCHEMA_POISON",
        "A2A_AGENT_IMPERSONATION",
        "A2A_TASK_HIJACK",
        "A2A_CROSS_AGENT_INJECT",
        "A2A_DATA_LEAK",
        "TOOL_USE_AUTH_BYPASS",
        "TOOL_USE_PARAM_INJECT",
        "CONTAINER_ESCAPE",
        "PERSISTENCE",
        "DENIAL_OF_SERVICE",
        "RESOURCE_ABUSE",
        "SSRF_ADVANCED",  # SSRF needs an actual URL request — not a prose mention
        "NET_SUSPICIOUS",
    }
)

# **Intent HARD signals**: the prose pattern IS the threat-delivery
# vector. A markdown line containing "Ignore previous instructions"
# (PROMPT_INJECT), "exfiltrate the .env file" (DATA_EXFIL), or a link
# to ``webhook.site/...`` (URL_SUSPICIOUS) is a real attack regardless
# of whether the host file is documentation or code. KEEP at declared
# severity even in safe_doc context.
_INTENT_HARD_SIGNAL_RULES: frozenset[str] = frozenset(
    {
        "PROMPT_INJECT",
        "INDIRECT_PROMPT_INJECT",
        "DATA_EXFIL",
        "DATA_EXFIL_TO_NETWORK",
        "EXFIL_TO_CHAT",
        "URL_SUSPICIOUS",
        "HARDCODED_SECRET",
        "INVISIBLE_UNICODE_RAW",
        "BASE64_DECODE_THREAT",
        "HEX_DECODE_THREAT",
        "UNICODE_ESCAPE_DECODE_THREAT",
        "CHARCODE_DECODE_THREAT",
    }
)

# **Intent SOFT signals**: the rule pattern catches a verb / concept
# that benignly appears in plugin documentation (a janitor skill's
# README legitimately mentions "removes", "deletes", "uninstalls" when
# describing its OWN behavior). These rules over-fire in prose; DEMOTE
# to NIT so the agent layer triages whether the prose is benign
# self-description or a real instruction.
_INTENT_SOFT_SIGNAL_RULES: frozenset[str] = frozenset(
    {
        "INTENT_EXPLICIT_EXFILTRATION",
        "INTENT_DESTRUCTIVE_INTENT",
        "INTENT_AGENT_MANIPULATION",
        "INTENT_INSTRUCTION_OVERRIDE",
        "TOKEN_STEAL",
        "CRED_ENV_READ",
        "CRED_ENV_SAFE",
        "CRED_THEFT",
        "CREDENTIAL_REFERENCE",
        "RECONNAISSANCE",
        "EVASION",
        "OBFUSCATION_INTENT",
        "CRYPTO_THEFT",
    }
)

# Backwards-compat alias — the union of both halves. Older heuristics
# (and the dispatcher's default fall-through) treat any intent rule as
# safe-doc-keeping. The split above adds finer-grained control.
_INTENT_CLASS_RULES: frozenset[str] = _INTENT_HARD_SIGNAL_RULES | _INTENT_SOFT_SIGNAL_RULES

# **Hidden-content hard signals**: the subset of INTENT_HARD rules whose
# threat does NOT depend on the host file being loaded as an agent
# instruction. Invisible / zero-width / bidi Unicode and decoded hidden
# payloads are STEGANOGRAPHIC channels — they hide content from human
# review regardless of the file surface. README / CHANGELOG / docs ARE
# routinely fed to agents ("summarize this repo's README", "what does
# this plugin do"), so a hidden-Unicode prompt-injection in a README is
# a real attack, not inert documentation prose.
#
# These rules must therefore be EXCLUDED from the documentation-only-path
# suppression carve-out (issue #38). The carve-out remains valid for the
# natural-language-prose injection rules (PROMPT_INJECT etc.) whose threat
# genuinely requires the file to be read as instructions — but NOT for
# the hidden-content class. (audit MAJOR #3)
_HIDDEN_CONTENT_HARD_SIGNAL_RULES: frozenset[str] = frozenset(
    {
        "INVISIBLE_UNICODE_RAW",
        "BASE64_DECODE_THREAT",
        "HEX_DECODE_THREAT",
        "UNICODE_ESCAPE_DECODE_THREAT",
        "CHARCODE_DECODE_THREAT",
    }
)

# Rules whose threat is delivered THROUGH a JSON/YAML metadata field — the
# `description` / `title` of an MCP tool or plugin manifest is itself an
# LLM-READ attack surface (the model reads tool descriptions when deciding to
# call them). A `safe_schema` classifier verdict means "the match sits in a
# SAFE_KEY metadata field"; for EXECUTION-class rules that genuinely makes it
# inert (a JSON string can't reach a shell), but for THESE rules the metadata
# field is exactly the target — so they must NEVER be hard-suppressed there.
# DEMOTE (stay visible at NIT, agent triages) instead. (audit CRITICAL #1)
_SCHEMA_FIELD_THREAT_RULES: frozenset[str] = _INTENT_HARD_SIGNAL_RULES | frozenset(
    {"MCP_SCHEMA_POISON", "TOOL_POISONING"}
)


# Path basenames that are ALWAYS instruction-loadable — content there
# IS read by Claude Code as agent instructions, so prompt-injection /
# data-exfil / etc. prose IS a real delivery vector and must NOT be
# suppressed by the documentation-only-path heuristic. Listed as
# basenames (case-insensitive).
_INSTRUCTION_LOADABLE_BASENAMES: frozenset[str] = frozenset({"skill.md", "claude.md", "agents.md"})

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
        # r04 obra FP iter1 (2026-05-27): common doc-content basenames
        # present in many real-world plugin trees. NONE of these are
        # loaded by Claude Code as agent instructions — they exist for
        # human readers (release notes / examples / changelog summaries
        # / TODO lists / roadmaps).
        "release-notes.md",
        "releasenotes.md",
        "release_notes.md",
        "examples.md",
        "example.md",
        "usage.md",
        "commandline-usage.md",
        "commandline_usage.md",
        "cli-usage.md",
        "todo.md",
        "todos.md",
        "roadmap.md",
        "notes.md",
        "faq.md",
        "design.md",
        "architecture.md",
        "internals.md",
        "advanced.md",
        "migration.md",
        "upgrade.md",
        "troubleshooting.md",
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
    # r05 ananddtyagi FP iter1 (2026-05-27): kept in sync with
    # _skillaudit_markdown_context._DOC_ONLY_DIR_PREFIXES_MD.
    # Standards/guides/tutorials/wiki/specs documentation directories.
    "standards/",
    "standard/",
    "guides/",
    "guide/",
    "tutorials/",
    "tutorial/",
    "wiki/",
    "specs/",
    "spec/",
    "specifications/",
)


def _rule_is_secret_detection(rule_id: str) -> bool:
    """True iff ``rule_id`` is a per-vendor or generic secret-detection
    rule whose finding represents a REAL credential leak (an actual key
    in source code or docs).

    Used by the doc-only carve-out (issue #40 follow-up) to EXCLUDE
    secret rules from blanket suppression: an OpenAI / Anthropic /
    AWS / Slack / GitHub key in README.md is still a real leak even
    though the file is "documentation-only" — GitHub's automated
    secret scanner picks it up, the vendor revokes it, and any plugin
    author who committed it needs to know immediately.

    Recognised rule-ID shapes:

    * ``SECRET_*`` — per-vendor rules (SECRET_OPENAI_KEY,
      SECRET_AWS_ACCESS_KEY, SECRET_JWT, …).
    * ``HARDCODED_SECRET`` — generic catch-all from the detector.
    * ``API_KEY_LEAK`` — alias used by some legacy ports.

    Note ``HARDCODED_SECRET`` is ALSO in ``_INTENT_HARD_SIGNAL_RULES``,
    so it would already have been suppressed in the INTENT-HARD branch
    above (per #38). This helper exists for the per-vendor SECRET_*
    rules which are NOT in ``_INTENT_HARD_SIGNAL_RULES``.
    """
    return rule_id.startswith("SECRET_") or rule_id == "HARDCODED_SECRET" or rule_id == "API_KEY_LEAK"


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
    elif fp_lower.endswith((".sh", ".bash", ".zsh", ".fish")):
        # Issue #41 follow-up — shell classifier for the printed-heredoc
        # supply-chain FP (install hint inside ``cat >&2 <<EOF`` is user-
        # facing help text, not an exec). Currently detects exactly that
        # one shape; everything else falls through to the heuristic chain.
        try:
            from _skillaudit_shell_context import classify as _sh_classify  # type: ignore[import-not-found]
        except ImportError:
            return ""
        classifier_verdict = _sh_classify(file_path, content, line_idx, match, rule_id)
    elif fp_lower.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        # Issue #39 — TS/JS classifier for CRED_ENV_READ, TOKEN_STEAL,
        # SECRET_* and SQL_INJECTION FP shapes documented in issue #39
        # (MCP server reading its own API key, redaction allow-list
        # regex, test-fixture synthetic secrets, test-fixture sample
        # SQL strings).
        try:
            from _skillaudit_typescript_context import classify as _ts_classify  # type: ignore[import-not-found]
        except ImportError:
            return ""
        classifier_verdict = _ts_classify(file_path, content, line_idx, match, rule_id)
    else:
        return ""

    # safe_literal — AST-proven benign argv / call shape. Suppress
    # regardless of rule (the shape itself precludes exploitation).
    if classifier_verdict == "safe_literal":
        return "suppress"

    # safe_schema — JSON/YAML description / title / keyword / homepage field.
    # For EXECUTION-class rules these are inert UI metadata (a JSON string
    # cannot reach a shell) — suppress (issue #33). BUT a metadata `description`
    # is itself an LLM-READ attack surface: MCP tool/schema descriptions are
    # read by the model when it decides whether to call a tool, so prompt-
    # injection / data-exfil / schema-poisoning rules target EXACTLY this field.
    # DEMOTE those (stay visible at NIT, agent triages) — never hard-suppress
    # (audit CRITICAL #1; mirrors the safe_doc INTENT carve-out below).
    if classifier_verdict == "safe_schema":
        if rule_id in _SCHEMA_FIELD_THREAT_RULES:
            return "demote"
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
        is_doc_only = _is_documentation_only_path(file_path)
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
            #
            # EXCEPTION (audit MAJOR #3): hidden-content hard signals
            # (invisible/zero-width/bidi Unicode + decoded hidden
            # payloads) are steganographic — they hide content from human
            # review regardless of whether the file is instruction-loadable,
            # and README/CHANGELOG/docs ARE fed to agents for
            # summarisation. Do NOT suppress those in doc-only paths;
            # defer to the heuristic chain so they stay visible (they end
            # in "keep" unless a placeholder/other safety-net fires).
            if is_doc_only and rule_id not in _HIDDEN_CONTENT_HARD_SIGNAL_RULES:
                return "suppress"
            # Hard signals — prose IS the threat-delivery vector. Defer
            # to the heuristic chain so placeholder-suppression
            # (``YOUR_API_KEY`` etc.), markdown-table demotion, and
            # other existing safety nets still run. If none of them
            # fire, the heuristic chain falls through to "keep" — the
            # rule's declared severity stands.
            return ""
        # Issue #40 follow-up (reopened 2026-05-26) — execution-class +
        # INTENT-soft matches inside DOC-ONLY paths are documentation
        # of the threat, not the threat itself. A `curl … | sh` snippet
        # in `references/zizmor-audit-fix-recipes.md` is teaching the
        # reader to SPOT and FIX the pattern; CPV's own
        # `skills/canonical-pipeline/` shows the same shape. Under
        # `--strict` a demoted NIT publish-blocks every security-doctor
        # plugin that documents its attack catalogue (`ai-maestro-janitor`
        # had 12+ of these). The doc-only carve-out is conservative:
        #
        # * `references/`, `docs/`, `examples/` subtrees + the
        #   readme/changelog/contributing/license/security/code-of-conduct/
        #   support/authors/maintainers/history.md basenames are NEVER
        #   loaded by Claude Code as agent instructions — `_is_documentation_only_path`
        #   excludes the instruction-loadable basenames (SKILL.md / CLAUDE.md /
        #   AGENTS.md). So a doctor's `skills/<name>/references/*.md`
        #   recipe catalogue suppresses, while the sibling
        #   `skills/<name>/SKILL.md` (instruction-loadable) does NOT.
        # * Hidden-content rules (INVISIBLE_UNICODE_RAW / BASE64_DECODE /
        #   …) STILL fire in doc-only paths — README summarisation IS
        #   an LLM read-surface for steganography (handled in the
        #   INTENT_HARD branch above).
        # * **Per-vendor secret rules** (``SECRET_OPENAI_KEY`` /
        #   ``SECRET_ANTHROPIC_KEY`` / ``SECRET_AWS_*`` / ``API_KEY_LEAK`` /
        #   ``SECRET_JWT`` / …) are EXCLUDED from this suppression — a real
        #   OpenAI key in README.md IS a real leak that GitHub's secret
        #   scanner picks up and revokes. Suppressing it would defeat the
        #   layered defense. These rules fall through to the demote/keep
        #   logic below (test_real_openai_key_in_markdown_kept invariant).
        # * Execution-class + INTENT-soft matches in INSTRUCTION-LOADABLE
        #   paths (SKILL.md, agents/, commands/, .claude/rules/) still
        #   demote — the author MUST address them (the iron rule
        #   "validations are mandatory, fix issues don't silence them"
        #   stays for the surfaces where prose CAN reach an agent).
        if is_doc_only and not _rule_is_secret_detection(rule_id):
            return "suppress"
        if rule_id in _INTENT_SOFT_SIGNAL_RULES:
            # Soft signals — the rule's verb / concept appears benignly
            # in plugin self-description docs. Demote to NIT so the
            # agent layer triages.
            return "demote"
        # EXECUTION-class or any other rule → demote (iron rule).
        return "demote"

    if classifier_verdict == "code_fence_neutral":
        # Issue #40 follow-up — `code_fence_neutral` in DOC-ONLY paths
        # (references/, README.md, docs/, …) is documentation prose
        # with inline-code spans + defensive vocab. The match is the
        # AGENT BEING WARNED about a phrase / pattern, not the phrase
        # being injected. In doc-only paths there is no agent receiving
        # the warning either — these files aren't loaded as instructions.
        # Suppress the same way safe_doc execution-class is suppressed
        # in doc-only paths.
        if _is_documentation_only_path(file_path):
            return "suppress"
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

    # Issue #40 root cause A — SSTI vs GitHub Actions. A ``${{ … }}``
    # expression is GitHub's context-expression syntax (a sandboxed
    # runtime), NEVER a Jinja2 / Mako / ERB server-side template. The
    # SSTI Jinja rule matches the inner ``{{ … }}``, so any GHA field
    # whose name contains a Jinja-global substring trips it —
    # ``pull_request`` ⊃ ``request``, ``steps.x.outputs.config`` ⊃
    # ``config``, etc. The ``$`` prefix is the reliable discriminator
    # (Jinja is bare ``{{ }}``; GHA is ``${{ }}``). Categorical
    # suppress regardless of file type — GHA *script injection* is a
    # SEPARATE concern handled by the workflow validators / zizmor, not
    # by the Jinja-SSTI rule. Runs before the context classifier so it
    # wins over the safe_doc→demote verdict that would otherwise leave
    # a publish-blocking NIT under --strict.
    if rule_id == "SSTI" and "${{" in line:
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
    if match.lower() in _SHORT_SHELL_TOKENS and _is_substring_false_positive(line, match):
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
            "CMD_INJECTION",
            "SHELL_EXEC",
            "REVERSE_SHELL",
            "OBFUSCATION",
            "REGEX_DOS",
            "INSECURE_CRYPTO",
            "INDIRECT_PROMPT_INJECT",
            "MCP_SCHEMA_POISON",
            "A2A_AGENT_IMPERSONATION",
            "A2A_TASK_HIJACK",
            "A2A_CROSS_AGENT_INJECT",
            "A2A_DATA_LEAK",
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

    # NOTE: the v2.99.1 ``.yml``-only ``${{ github.* }}`` SSTI demote
    # special-case that used to live here was superseded in v2.106.0 by
    # the categorical ``rule_id == "SSTI" and "${{" in line`` suppressor
    # at the top of this function (issue #40 root cause A) — that runs
    # before the context classifier, covers every file type, and
    # suppresses rather than merely demotes. Removed to avoid a dead
    # second code path.

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


# Import-statement shapes — a file read NAME appearing in an import is not
# a read OPERATION (``import { readFileSync } from "fs"`` / ``from os import
# open``). These must never count as a read for the structural detector.
_IMPORT_LINE_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:import\b|from\s+[\w.]+\s+import\b|export\s+\{)|^\s*(?:readFile\w*|open|read)\s*,\s*$"
)

# Extract the variable a read result is bound to:
#   const head = readFileSync(...)      → head
#   data = open(path).read()            → data
#   let buf = fs.readFileSync(...)      → buf
_READ_ASSIGN_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:const|let|var)?\s*(?P<var>[A-Za-z_$][\w$]*)\s*=\s*[^=].*"
    r"(?:readFile\w*|fs\.read\w*|\.read\s*\(|open\s*\()",
)

# Same-line read→sink: the read result is passed straight into a net call,
# e.g. ``fetch(url, {body: readFileSync(secret)})``.
_SAME_LINE_READ_AND_NET_RE: re.Pattern[str] = re.compile(
    r"(?:fetch|axios|\.post|\.send|http\.request|https\.request|XMLHttpRequest)\s*\(",
)

_STRUCT_FLOW_WINDOW = 25  # lines a read-var may flow forward to a net sink


def _detect_structural_read_to_net(lines: list[str], cb_map: list[bool]) -> list[dict[str, Any]]:
    """Flag a read→network EXFIL flow, but ONLY when a variable bound from a
    file read is actually referenced by a network call (data-flow link), or a
    read result is piped into a net call on the same line.

    The pre-data-flow version fired whenever ANY read line and ANY net line
    coexisted anywhere in the file — so a 2700-line MCP server's shebang
    ``readFileSync().slice(0,256)`` got paired with an unrelated ``fetch()``
    2000 lines away (issue #41). Requiring a shared variable within a
    proximity window eliminates that while still catching genuine
    read-then-send exfil (which is, by nature, local).
    """
    # Collect (line_no, var) for real read assignments, and the line numbers
    # of real net calls. Skip import lines, doc/instructional context, and
    # data-only fenced blocks.
    read_vars: list[tuple[int, str]] = []
    net_lines: list[int] = []
    same_line_hits: list[int] = []
    for i, line in enumerate(lines):
        if cb_map[i] and _has_doc_context(lines, i, 8):
            continue
        if _is_instructional_context(lines, i):
            continue
        if _IMPORT_LINE_RE.search(line):
            # Imports never count as a read OR a net operation.
            continue
        is_read = any(p.search(line) for p in _READ_PATTERNS)
        is_net = any(p.search(line) for p in _NET_PATTERNS)
        if is_read and is_net and _SAME_LINE_READ_AND_NET_RE.search(line):
            # Read result piped straight into a net call on one line.
            same_line_hits.append(i + 1)
            continue
        if is_read:
            m = _READ_ASSIGN_RE.search(line)
            if m is not None:
                read_vars.append((i + 1, m.group("var")))
        if is_net:
            net_lines.append(i + 1)

    # Data-flow link: a read-var referenced by a later net call within the
    # proximity window.
    flow_read: int | None = None
    flow_net: int | None = None
    for r_line, var in read_vars:
        # Require a non-trivial variable name (single-char temp loop vars like
        # ``i``/``f`` produce noise).
        if len(var) < 2:
            continue
        var_word = re.compile(rf"\b{re.escape(var)}\b")
        for n_line in net_lines:
            if r_line < n_line <= r_line + _STRUCT_FLOW_WINDOW:
                if var_word.search(lines[n_line - 1]):
                    flow_read, flow_net = r_line, n_line
                    break
        if flow_read is not None:
            break

    if same_line_hits:
        first = same_line_hits[0]
        return [
            {
                "ruleId": "STRUCT_READ_EXFIL",
                "severity": "high",
                "category": "structural",
                "name": "Read → Network pattern detected",
                "description": (
                    f"File read result piped into a network call on line {first}. Potential data exfiltration flow."
                ),
                "line": first,
                "lineContent": lines[first - 1].strip()[:200],
                "match": "structural",
                "suppressed": False,
            }
        ]
    if flow_read is not None and flow_net is not None:
        return [
            {
                "ruleId": "STRUCT_READ_EXFIL",
                "severity": "high",
                "category": "structural",
                "name": "Read → Network pattern detected",
                "description": (
                    f"A variable bound from a file read (line {flow_read}) is sent "
                    f"by a network call (line {flow_net}). Potential data "
                    "exfiltration flow."
                ),
                "line": flow_read,
                "lineContent": lines[flow_read - 1].strip()[:200],
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
    # r01 FP iter (2026-05-28) — U+200D between two emoji is a valid emoji
    # ZWJ SEQUENCE (combiner), not steganography. Reuse the markdown
    # classifier's combiner check so both detectors agree.
    from _skillaudit_markdown_context import _is_emoji_combiner_zwj  # type: ignore[import-not-found]

    findings: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        for ch, name in _INVISIBLE_CHARS:
            if ch in line:
                count = line.count(ch)
                # BOM at line-0 / single occurrence is normal.
                if ch == "﻿" and i == 0 and count == 1:
                    continue
                # ZWJ that only appears as emoji combiners → benign sequence.
                if ch == "‍":
                    bare = [p for p, c in enumerate(line) if c == "‍" and not _is_emoji_combiner_zwj(line, p)]
                    if not bare:
                        continue
                    count = len(bare)
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
            "high",
            "Exfiltration intent",
            "Instruction asks to send sensitive data externally",
        ),
        (
            r"upload\s+(the\s+)?(contents?|data|file|config|credentials?|secrets?)\s",
            "high",
            "Upload intent",
            "Instruction asks to upload sensitive data",
        ),
        (
            r"post\s+(the\s+)?(contents?|data|results?)\s+to\s+",
            "medium",
            "POST data intent",
            "Instruction asks to POST data to an endpoint",
        ),
        (
            r"read\s+.*\.(env|key|pem|secret|credentials)\b.*(?:send|post|upload|forward|transmit)",
            "critical",
            "Read-and-exfiltrate intent",
            "Instruction asks to read secrets then send them",
        ),
        (
            r"(?:forward|relay|copy)\s+.*(?:api.?key|token|password|secret|credential)\s+to\s",
            "critical",
            "Credential forwarding intent",
            "Instruction asks to forward credentials",
        ),
        (
            r"(?:delete|remove|wipe|destroy)\s+(all\s+)?(files?|data|logs?|history|memory)",
            "high",
            "Destructive intent",
            "Instruction asks to delete files or data",
        ),
        (
            r"install\s+.*(?:backdoor|rootkit|keylogger|trojan|malware)",
            "critical",
            "Malware install intent",
            "Instruction asks to install malicious software",
        ),
        (
            r"disable\s+.*(?:firewall|antivirus|security|logging|audit)",
            "critical",
            "Security disable intent",
            "Instruction asks to disable security measures",
        ),
        (
            r"connect\s+(?:back|reverse)\s+to",
            "high",
            "Reverse connection intent",
            "Instruction asks to connect back to attacker",
        ),
        (
            r"exfiltrate",
            "critical",
            "Explicit exfiltration",
            "Instruction explicitly mentions exfiltration",
        ),
    )
)


def _analyze_intent(lines: list[str], cb_map: list[bool]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if cb_map[i]:
            continue
        # ReDoS guard (issue #53 follow-up): bound the regex input the same way
        # the catalog loop is bounded (see _MAX_SCAN_LINE). _INTENT_PATTERNS
        # includes chained-.* rules (e.g. `read .* \.env .* (send|post|...)`)
        # that backtrack super-linearly on a long NON-matching line when
        # google-re2 is absent (these run as a separate per-line scan the RE2
        # prefilter cannot pre-skip). Match-only; the FULL `line` is still used
        # for lineContent reporting below, and m.group(0) stays valid because a
        # real intent clause is local (well within the first _MAX_SCAN_LINE chars).
        intent_line = line if len(line) <= _MAX_SCAN_LINE else line[:_MAX_SCAN_LINE]
        for pat, sev, name, desc in _INTENT_PATTERNS:
            m = pat.search(intent_line)
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


def _scan_decoded(decoded: str, encoding: str, line_idx: int, line_content: str) -> list[dict[str, Any]]:
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
                        f'{encoding}-encoded content contains {name.lower()}. Decoded match: "{m.group(0)[:80]}"'
                    ),
                    "line": line_idx + 1,
                    "lineContent": line_content.strip()[:200],
                    "match": f'{encoding.lower()}→"{decoded[:100]}"',
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
            "SECRET_GITHUB_TOKEN",
            "Hardcoded GitHub token",
            "GitHub personal access token (ghp_/gho_/ghu_/ghs_/ghr_) embedded in source",
            r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}\b",
            "critical",
        ),
        (
            "SECRET_AWS_KEY",
            "Hardcoded AWS access key",
            "AWS access-key-id (AKIA…) embedded in source",
            r"\b(AKIA|ASIA)[A-Z0-9]{16}\b",
            "critical",
        ),
        (
            "SECRET_SLACK_TOKEN",
            "Hardcoded Slack token",
            "Slack token (xoxb-/xoxa-/xoxp-/xoxr-/xoxs-) embedded in source",
            r"\b(xox[bapors])-[A-Za-z0-9-]{10,200}\b",
            "critical",
        ),
        (
            "SECRET_SLACK_WEBHOOK",
            "Slack webhook URL",
            "Hardcoded Slack incoming-webhook URL — can post messages to channels",
            r"https://hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{20,}",
            "high",
        ),
        (
            "SECRET_DISCORD_TOKEN",
            "Hardcoded Discord bot token",
            "Discord bot token embedded in source",
            r"\b[MN][A-Za-z0-9]{23}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,38}\b",
            "critical",
        ),
        (
            "SECRET_DISCORD_WEBHOOK",
            "Discord webhook URL",
            "Hardcoded Discord webhook — can post messages to channels",
            r"https://discord(?:app)?\.com/api/webhooks/\d{17,}/[A-Za-z0-9_-]{60,}",
            "high",
        ),
        (
            "SECRET_TELEGRAM_TOKEN",
            "Telegram bot token",
            "Telegram bot token embedded in source",
            r"\b\d{8,12}:[A-Za-z0-9_-]{35}\b",
            "critical",
        ),
        (
            "SECRET_VERCEL_TOKEN",
            "Vercel access token",
            "Vercel API token (vercel_…) embedded in source",
            r"\bvercel_[A-Za-z0-9]{24,40}\b",
            "critical",
        ),
        (
            "SECRET_NPM_TOKEN",
            "npm access token",
            "npm token (npm_… or 36-hex UUID-style) embedded in source",
            r"\bnpm_[A-Za-z0-9]{30,}\b",
            "critical",
        ),
        (
            "SECRET_PYPI_TOKEN",
            "PyPI API token",
            "PyPI token (pypi-…) embedded in source",
            r"\bpypi-[A-Za-z0-9]{30,}\b",
            "critical",
        ),
        (
            "SECRET_PRIVATE_KEY",
            "PEM private key",
            "PEM-formatted private key block embedded in source",
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY-----",
            "critical",
        ),
        (
            "SECRET_JWT",
            "Hardcoded JWT",
            "JSON Web Token (header.payload.signature) embedded in source",
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
            "high",
        ),
        (
            "SECRET_OPENAI_KEY",
            "OpenAI API key",
            "OpenAI key (sk-… / sk-proj-…) embedded in source",
            r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b",
            "critical",
        ),
        (
            "SECRET_ANTHROPIC_KEY",
            "Anthropic API key",
            "Anthropic key (sk-ant-…) embedded in source",
            r"\bsk-ant-[A-Za-z0-9_-]{20,}\b",
            "critical",
        ),
        (
            "SECRET_GOOGLE_API_KEY",
            "Google API key",
            "Google API key (AIza…) embedded in source",
            r"\bAIza[A-Za-z0-9_-]{32,40}\b",
            "high",
        ),
        (
            "SECRET_STRIPE_KEY",
            "Stripe API key",
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


# SECURITY (#53 ReDoS): cap the per-line input fed to the regex engine. A
# pathological catalog pattern (>=2 chained unbounded `.*` between alternation
# groups) can backtrack super-linearly on a single very long line, pinning a
# CPU core indefinitely. Bounding the matched span makes the worst case linear
# in _MAX_SCAN_LINE regardless of pattern shape — the single strongest guard,
# covering current AND future catalog patterns. Truncation affects MATCHING
# ONLY: reported line numbers / content are unchanged, and a real payload in a
# very long minified/base64 line still matches within the first _MAX_SCAN_LINE
# chars. Belt-and-suspenders with the per-file worker wall-clock kill (#52).
_MAX_SCAN_LINE = 2000


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
    #
    # v2.106.0 (audit MAJOR #1): RE2 pre-filter. One O(N_text) RE2 ``Set``
    # pass over the whole content tells us WHICH catalog rules can match
    # at all; we then run the expensive per-line Python loop ONLY for that
    # subset. ``None`` means the fast matcher is unavailable/disabled —
    # then ``prefilter`` is treated as "match everything" so behaviour is
    # byte-identical to the pre-wiring path. The matcher compiles patterns
    # case-insensitive + MULTILINE (``_blob_scan_flags``) so the pre-filter
    # is a sound superset of the per-line IGNORECASE ``search`` results —
    # it never excludes a rule the per-line loop would have hit.
    prefilter = _prefilter_rule_ids(content)
    for rule, compiled_pats in _compiled_rules():
        rule_id = rule.get("id", "RULE_UNKNOWN")
        # Skip rules the single-pass matcher proved cannot match anywhere
        # in this file. ``prefilter is None`` → matcher unavailable → run
        # every rule (legacy behaviour).
        if prefilter is not None and rule_id not in prefilter:
            continue
        rule_sev = rule.get("severity", "medium")
        rule_cat = rule.get("category", "rule")
        rule_name = rule.get("name", rule_id)
        rule_desc = rule.get("description", "")
        for pat in compiled_pats:
            for i, line in enumerate(lines):
                # #53: bound the regex input — see _MAX_SCAN_LINE. Match only;
                # `line` (full) is still used for reporting/context below.
                m = pat.search(line if len(line) <= _MAX_SCAN_LINE else line[:_MAX_SCAN_LINE])
                if not m:
                    continue
                in_cb = cb_map[i]
                lang = _code_block_lang(cb_ranges, i) or ""
                verdict = _confidence(
                    lines,
                    i,
                    m.group(0),
                    rule_id,
                    cb_map,
                    cb_ranges,
                    py_doc_map=py_doc_map,
                    file_path=file_path,
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

    # Dedupe by (ruleId, line), keeping the HIGHEST-severity finding for
    # each key (audit MINOR #4). A catalog rule and a secondary scanner
    # can synthesize the SAME ruleId on the same line (e.g. the catalog's
    # INTENT_DESTRUCTIVE_INTENT rule and ``_analyze_intent``'s synthesized
    # "INTENT_DESTRUCTIVE_INTENT"). The catalog finding is appended first,
    # so a naive first-wins dedup would silently discard a
    # higher-severity secondary finding purely by append order. Compare
    # by severity rank and keep the strongest; on a tie, first-seen wins
    # (stable, deterministic). A non-suppressed/visible finding also wins
    # over a suppressed one at equal rank so suppression never hides a
    # live duplicate.
    best_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    order: list[tuple[str, int]] = []
    for f in findings:
        key = (f.get("ruleId", ""), int(f.get("line", 0)))
        existing = best_by_key.get(key)
        if existing is None:
            best_by_key[key] = f
            order.append(key)
            continue
        if _severity_rank(str(f.get("severity", ""))) > _severity_rank(str(existing.get("severity", ""))):
            best_by_key[key] = f
    deduped: list[dict[str, Any]] = [best_by_key[k] for k in order]

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
        # r04 obra FP iter1 (2026-05-27): per-user private storage that
        # plugins sometimes leak into the repo by accident. These dirs
        # are user-private content (journal entries, scratch notes, local
        # work logs) — never agent-loaded instructions, never published.
        # They are NOT in the standard `_dev` family but the same skip
        # semantics apply.
        ".private-journal",
        ".scratch",
        ".local",
        ".cache_local",
        ".history",
        ".log",
        ".logs",
        ".tmp",
        ".trash",
    }
)


# Issue #42: hash-anchored skip for plugins that ship byte-identical copies
# of CPV's own scanner artifacts (e.g., an offline auditor packaging).
# Without this, the scanner self-matches its own ~490 detection patterns
# against the shipped pattern catalog and emits 262+ FPs per file (catalog
# literally CONTAINS sensitive-system-path tokens, code-execution
# primitives, and pipe-into-shell install hints AS PATTERN DESCRIPTIONS of
# malicious code — not malicious code itself).
#
# SECURITY: this is NOT a basename skip — a malicious plugin could exploit
# that by naming a payload ``skillaudit_patterns.json``. We require the file
# to be BYTE-IDENTICAL to the installed CPV artifact (SHA256 match against
# CPV's own ``.plugin-self-hashes.json``). A modified copy (even by one
# byte) falls through and is scanned normally.
_SELF_ARTIFACT_BASENAMES: frozenset[str] = frozenset(
    {
        "skillaudit_patterns.json",
        "re2_compatibility.json",
        "cpv_skillaudit_native.py",
        "_skillaudit_python_context.py",
        "_skillaudit_json_context.py",
        "_skillaudit_yaml_context.py",
        "_skillaudit_markdown_context.py",
        "_skillaudit_typescript_context.py",
        "_skillaudit_shell_context.py",
    }
)

# Lazy-loaded {basename: sha256} map pulled from CPV's installed integrity
# manifest, filtered to the self-artifact basename allowlist. Sentinel
# ``None`` = not yet attempted; empty dict = tried and nothing usable
# (so we don't retry every file).
_CPV_INSTALL_ARTIFACT_HASHES: dict[str, str] | None = None


def _load_cpv_install_artifact_hashes() -> dict[str, str]:
    """Build a ``{basename: sha256}`` map from CPV's installed integrity
    manifest, restricted to the ``_SELF_ARTIFACT_BASENAMES`` allowlist.

    The CPV install root sits one level above this module (``scripts/``'s
    parent). Returns an empty dict on any failure — caller treats that
    as "no skips available" and scans everything (safe fallback).
    """
    global _CPV_INSTALL_ARTIFACT_HASHES
    if _CPV_INSTALL_ARTIFACT_HASHES is not None:
        return _CPV_INSTALL_ARTIFACT_HASHES
    out: dict[str, str] = {}
    install_root = Path(__file__).resolve().parent.parent
    for manifest_name in (".plugin-self-hashes.json", ".cpv-self-hashes.json"):
        manifest_path = install_root / manifest_name
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, dict):
            continue
        for rel_path, h in files.items():
            if not isinstance(rel_path, str) or not isinstance(h, str):
                continue
            basename = rel_path.replace("\\", "/").rsplit("/", 1)[-1]
            if basename not in _SELF_ARTIFACT_BASENAMES:
                continue
            # Strip the `sha256:` algorithm prefix the manifest stores —
            # `_is_self_artifact_copy` compares against the bare hex output
            # of `hashlib.sha256().hexdigest()`. Unknown-algorithm prefixes
            # are dropped on the floor (no entry recorded → file is scanned).
            if ":" in h:
                algo, _, hex_part = h.partition(":")
                if algo.lower() != "sha256":
                    continue
                out.setdefault(basename, hex_part)
            else:
                out.setdefault(basename, h)
        if out:
            break  # First manifest with usable entries wins.
    _CPV_INSTALL_ARTIFACT_HASHES = out
    return out


def _is_self_artifact_copy(p: Path) -> bool:
    """Return True iff ``p`` is byte-identical to a CPV-installed scanner
    artifact of the same basename — i.e., the plugin is bundling an
    unmodified copy of CPV's catalog or context classifier for offline use,
    and scanning it would just produce self-matches against its own
    pattern descriptions.

    Security gate: requires an exact SHA256 match against CPV's installed
    ``.plugin-self-hashes.json`` entry for that basename. A spoofed file
    (different bytes, same name) is NOT skipped — it falls through and is
    scanned normally (closes the obvious basename-spoofing evasion).
    """
    if p.name not in _SELF_ARTIFACT_BASENAMES:
        return False
    expected = _load_cpv_install_artifact_hashes().get(p.name)
    if not expected:
        return False  # No canonical hash to compare against → scan it.
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest() == expected
    except OSError:
        return False


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
        # Issue #42 — hash-anchored skip for plugins that bundle byte-identical
        # copies of CPV's scanner catalog / context classifiers (an offline
        # auditor packaging). Spoofed basenames (different bytes) fall through.
        if _is_self_artifact_copy(p):
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
        # audit NIT #14: `_iter_scannable_files` rglobs FILES only, so the
        # dir-trailing-slash branch above never fires for a file candidate.
        # A file under a DIR-ONLY ignore pattern (`/build/`, `node_modules/`)
        # would then be scanned (overscan) because pathspec's gitwildmatch
        # may not match a bare dir pattern against the file path itself.
        # Walk this file's ancestor directories and test each with a
        # trailing slash so a dir-only pattern excludes its contained files.
        rel_path = PurePosixPath(rel)
        for parent in rel_path.parents:
            parent_str = parent.as_posix()
            if parent_str in ("", "."):
                continue
            if is_path_gitignored(parent_str + "/", patterns):
                return True
        return False

    return predicate


# ────────────────────────────────────────────────────────────────────────
# Per-file scan worker — task #384 parallelism for skillaudit (Agent B1)
# ────────────────────────────────────────────────────────────────────────
#
# The 76% wall-time hot path on `validate_plugin .` is this scanner's
# per-file rule-match loop. The work is embarrassingly parallel — every
# file is scanned independently with no cross-file state — but the
# original serial `for fp in _iter_scannable_files(...)` left every CPU
# core but one idle.
#
# Refactor: extract the per-file body into a TOP-LEVEL pickleable
# `_scan_one_file_skillaudit(file_path)` worker, and dispatch via
# `cpv_parallel_runner.parallel_scan` when the file count crosses a
# small threshold. Below the threshold we stay serial (process-pool
# spawn cost ~250-500ms on macOS would dominate a 5-file scan).
#
# Pickleability constraints:
#   * The worker MUST be top-level (closures/lambdas can't cross the
#     pool boundary).
#   * The worker takes ONE arg (Path) — the plugin root is shipped via
#     env var, mirroring the validate_security.py worker pattern.
#   * Returned findings are plain dicts of primitives (already true —
#     scan_content emits dict[str, Any] with str/int/bool values).
#   * Module-level state (`_RULES_CACHE`, `_COMPILED_RULES_CACHE`) is
#     populated lazily from disk by `_get_rules()` / `_compiled_rules()`
#     on first use, so each spawned worker re-loads from
#     `scripts/rules/skillaudit_patterns.json` — no parent state needed.
#
# Test-mode fallback: when the env var is absent (a test imports the
# worker and calls it directly without going through `scan_path`), we
# fall back to using `file_path.parent` as the plugin root. The
# `rel_path` becomes the bare filename — same behaviour as
# validate_security's `scan_one_file_for_security` fallback.

_WORKER_ENV_PLUGIN_ROOT = "CPV_SKILLAUDIT_WORKER_PLUGIN_ROOT"

# Default parallelism threshold. Below this file count, the
# pool-spawn cost outweighs the per-file gain. Above it, the speedup
# scales close to linearly with CPU count for plugins with hundreds
# of markdown / source files (the common case for the 76% hot path).
_PARALLEL_THRESHOLD_DEFAULT = 16


def _parallel_enabled() -> bool:
    """Return True unless ``CPV_SKILLAUDIT_PARALLEL=0`` opts out.

    Read at call time (not import time) so tests can monkeypatch the
    env var per-test. Any value other than ``"0"`` keeps parallelism
    enabled — same convention validate_security.py uses for its own
    escape hatch (``CPV_PARALLEL_SCAN_THRESHOLD``).
    """
    return os.environ.get("CPV_SKILLAUDIT_PARALLEL", "1") != "0"


def _parallel_threshold() -> int:
    """Resolve the parallel-dispatch threshold honoring the env override.

    Invalid values fall back to the safe default rather than crashing
    the validator. The threshold is a floor on file count — fewer
    files than the threshold runs serially.
    """
    raw = os.environ.get("CPV_SKILLAUDIT_PARALLEL_THRESHOLD")
    if raw is None:
        return _PARALLEL_THRESHOLD_DEFAULT
    try:
        value = int(raw)
        return value if value >= 1 else _PARALLEL_THRESHOLD_DEFAULT
    except ValueError:
        return _PARALLEL_THRESHOLD_DEFAULT


def _is_skillaudit_catalog_json(content: str, file_path: Path) -> bool:
    """True iff ``content`` IS a SkillAudit pattern catalog (issue #42).

    A plugin that vendors CPV's offline auditor ships a copy of the rule
    catalog (``skillaudit_patterns.json``). The catalog is DATA that
    describes malicious code — it literally contains sensitive-path,
    ``eval(`` and ``curl … | sh`` strings as *pattern strings* — so the
    scanner would otherwise self-match ~214 findings against it.

    Recognised by SCHEMA, never by filename: a vendored copy under any
    name is treated as data, and a hostile ``.json`` cannot evade scanning
    by name because it would have to actually BE a valid pattern catalog —
    at which point its "patterns" are inert match strings (the catalog
    format executes nothing). So there is no evasion hole.

    The shape that must hold: a top-level object with a ``rules`` array
    whose entries are overwhelmingly rule-shaped (``patterns`` list plus an
    ``id``/``name``). A handful of stray non-rule entries is tolerated, but
    a random config ``.json`` (no ``rules`` array of pattern-bearing
    objects) is NOT recognised and is scanned normally.
    """
    if file_path.suffix.lower() not in (".json", ".jsonc"):
        return False
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    rules = data.get("rules")
    if not isinstance(rules, list) or len(rules) < 5:
        return False
    rule_shaped = sum(
        1 for r in rules if isinstance(r, dict) and isinstance(r.get("patterns"), list) and ("id" in r or "name" in r)
    )
    # Overwhelming majority must be rule-shaped — a single object that
    # happens to have a "rules" key cannot trip this.
    return rule_shaped >= max(5, int(0.8 * len(rules)))


def _scan_one_file_skillaudit(file_path: Path) -> list[dict[str, Any]]:
    """Top-level, pickleable per-file scan callable for ``parallel_scan``.

    The harness contract is ``Callable[[Path], list]``. The per-file
    work mirrors the body of the original serial loop inside
    ``scan_path`` — read the file, scan its content, attach the
    relative path to every finding.

    Plugin root resolved from ``CPV_SKILLAUDIT_WORKER_PLUGIN_ROOT``
    env var (set by the parent before pool creation). When the env
    var is absent (test-mode direct invocation), falls back to
    ``file_path.parent`` so the worker is callable in isolation.

    Returns a list of finding dicts. Each dict has the same shape as
    the per-file findings the serial loop emitted (the dict from
    ``scan_content`` plus an injected ``"file"`` key). When the file
    can't be read (OSError) or is empty, returns an empty list — the
    aggregator in ``scan_path`` accounts for both cases via the
    sentinel "[]" return.

    Note: ``files_scanned`` accounting lives in the parent — the
    worker would need a side channel to ship a "did I actually read
    the bytes?" flag back, but that flag is derivable from the file
    being non-empty AND readable, which the parent re-checks via
    ``ScanResult.error is None`` AND a marker we inject (see below).
    """
    plugin_root_str = os.environ.get(_WORKER_ENV_PLUGIN_ROOT)
    plugin_root = Path(plugin_root_str) if plugin_root_str else file_path.parent

    rel = str(file_path)
    try:
        rel = str(file_path.relative_to(plugin_root))
    except ValueError:
        pass

    # v2.104.0 — binary detection routes binary files through the
    # binary scanner (string-extraction + targeted secret + URL
    # detection). When the binary scanner module isn't available OR
    # CPV_BINARY_SCAN=0, we fall through to the legacy text-only path
    # (binary file is unreadable as UTF-8 → empty content → sentinel).
    # `scan_binary` accepts the COMPILED catalog (the same
    # ``list[(rule_dict, [compiled_pattern, ...])]`` shape the
    # text path uses) so it can re-run the same rules against
    # extracted strings without re-compiling them.
    if _binary_enabled() and _binary_is_binary is not None:
        try:
            is_bin = bool(_binary_is_binary(file_path))
        except Exception:  # pragma: no cover — defensive
            is_bin = False
        if is_bin:
            # v2.106.0 (audit MINOR #6): binary findings now (a) go through
            # the result cache like the text path, keyed on the RAW BYTES
            # with a ``binary:<suffix>`` ext tag so they re-anchor across
            # directories and skip re-scanning unchanged binaries; and
            # (b) pass through a thin placeholder-suppression normaliser so
            # an extracted ``YOUR_API_KEY`` string-table match suppresses
            # like it would in the text path. There is no doc/code-fence
            # context in a binary, so ONLY placeholder suppression applies.
            try:
                raw = file_path.read_bytes()
            except OSError:
                raw = b""
            bin_content_hash = hashlib.sha256(raw).hexdigest() if raw else ""
            bin_ext = "binary:" + Path(rel).suffix.lower()
            bin_cache_on = _cache_enabled()

            if bin_cache_on and bin_content_hash and not _cache_deep_enabled() and _scan_cache_get is not None:
                try:
                    bin_cached = _scan_cache_get(bin_content_hash, _CATALOG_HASH, __version__, file_ext=bin_ext)
                except Exception:  # pragma: no cover — cache must never break a scan
                    bin_cached = None
                if bin_cached is not None:
                    cached_out: list[dict[str, Any]] = []
                    for f in bin_cached:
                        if isinstance(f, dict):
                            f["file"] = rel
                            cached_out.append(f)
                    if cached_out:
                        return cached_out
                    return [{"_skillaudit_sentinel": "scanned", "file": rel}]

            try:
                bin_findings = _binary_scan_binary(file_path, _compiled_rules())  # type: ignore[misc]
            except Exception:  # pragma: no cover — defensive
                bin_findings = []
            findings_bin: list[dict[str, Any]] = []
            for f in bin_findings or []:
                if isinstance(f, dict):
                    _suppress_binary_placeholder(f)
                    f["file"] = rel
                    findings_bin.append(f)

            if bin_cache_on and bin_content_hash and _scan_cache_put is not None:
                try:
                    _scan_cache_put(
                        bin_content_hash,
                        _CATALOG_HASH,
                        __version__,
                        findings_bin,
                        file_ext=bin_ext,
                    )
                except Exception:  # pragma: no cover — cache write is best-effort
                    pass

            if findings_bin:
                return findings_bin
            return [{"_skillaudit_sentinel": "scanned", "file": rel}]

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        # Mirror the serial loop: an unreadable file is silently
        # skipped (no finding, no files_scanned increment). The
        # parent detects this by the empty-result + a marker dict
        # we DON'T emit. See `scan_path` for the aggregation logic.
        return []

    if not content:
        # Empty file — counts as scanned (files_scanned += 1 in the
        # serial loop) but produces no findings. Emit a single
        # sentinel finding so the parent can distinguish "empty file"
        # from "unreadable file". The sentinel is filtered out before
        # returning to the user; it carries a private marker key.
        return [{"_skillaudit_sentinel": "empty", "file": rel}]

    # Issue #42 — a scanned plugin that VENDORS CPV's offline auditor
    # ships a copy of the rule catalog (skillaudit_patterns.json). The
    # catalog is DATA describing malicious patterns, so the scanner would
    # self-match ~214 findings against its own pattern strings. Recognise
    # it by schema (see _is_skillaudit_catalog_json — name-agnostic, no
    # evasion hole) and count it as scanned-with-no-findings.
    if _is_skillaudit_catalog_json(content, file_path):
        return [{"_skillaudit_sentinel": "scanned", "file": rel}]

    # v2.104.0 — cache GET. Hash the textual content (NOT the raw
    # bytes — _content_hash already encodes utf-8) and look up against
    # (content_hash, catalog_hash, engine_version, file_ext). CPV_SCAN_CACHE=0
    # disables; CPV_SCAN_CACHE_DEEP=1 forces a miss but still writes
    # back so a release can pre-warm a fresh cache without an explicit
    # purge step.
    #
    # file_ext is part of the key because scan_content -> the context
    # classifier dispatches on the file SUFFIX (.py/.json/.md/.yml/.ts),
    # so the SAME bytes produce a DIFFERENT verdict under a different
    # extension. Without the extension in the key, whichever extension is
    # scanned FIRST poisons the lookup for every other extension with its
    # own classifier's verdict (cross-extension collision -> FP or FN).
    # Path(rel).suffix.lower() matches the classifier's extension dispatch.
    content_hash = _content_hash(content)
    file_ext = Path(rel).suffix.lower()
    cache_on = _cache_enabled()
    if cache_on and not _cache_deep_enabled() and _scan_cache_get is not None:
        try:
            cached = _scan_cache_get(content_hash, _CATALOG_HASH, __version__, file_ext=file_ext)
        except Exception:  # pragma: no cover — cache must never break a scan
            cached = None
        if cached is not None:
            findings_cached: list[dict[str, Any]] = []
            for f in cached:
                if isinstance(f, dict):
                    # Re-anchor file path — the cache key is
                    # (content, catalog, version, extension), NOT the
                    # full path, so the same bytes + same extension
                    # scanned at a different directory still hits. The
                    # cached entry must reflect THIS scan's file location.
                    f["file"] = rel
                    findings_cached.append(f)
            if findings_cached:
                return findings_cached
            return [{"_skillaudit_sentinel": "scanned", "file": rel}]

    findings = scan_content(content, rel)
    for f in findings:
        f["file"] = rel

    # v2.104.0 — cache PUT (always, including in DEEP mode, so an
    # explicit re-warm pass populates the cache). We persist the
    # findings stripped of the per-scan "file" key — the key is
    # (content, catalog, version, extension), file paths get re-anchored
    # on GET above. file_ext keeps two same-content/different-extension
    # scans in separate rows (no overwrite, no cross-extension collision).
    if cache_on and _scan_cache_put is not None:
        try:
            to_cache: list[dict[str, Any]] = []
            for f in findings:
                # shallow copy minus the file path
                f_copy = {k: v for k, v in f.items() if k != "file"}
                to_cache.append(f_copy)
            _scan_cache_put(content_hash, _CATALOG_HASH, __version__, to_cache, file_ext=file_ext)
        except Exception:  # pragma: no cover — cache must never break a scan
            pass

    if not findings:
        # Non-empty file with no findings still increments
        # files_scanned. Inject a sentinel so the parent doesn't
        # confuse this with the "unreadable" empty list.
        return [{"_skillaudit_sentinel": "scanned", "file": rel}]
    return findings


def scan_path(plugin_root: Path) -> tuple[list[dict[str, Any]], int]:
    """Walk plugin_root, scan each scannable file. Returns (findings, files_scanned).

    Parallelism (task #384, Agent B1): when the discovered file count
    meets ``CPV_SKILLAUDIT_PARALLEL_THRESHOLD`` (default 16) AND
    ``CPV_SKILLAUDIT_PARALLEL`` is not "0", the per-file scan body is
    dispatched across a ``ProcessPoolExecutor`` via
    ``cpv_parallel_runner.parallel_scan``. Findings are merged back in
    input order so the result set is bit-identical to the prior
    serial run. Below the threshold the legacy serial loop runs
    unchanged because pool-spawn cost dominates.
    """
    files = list(_iter_scannable_files(plugin_root))
    if not files:
        return [], 0

    if not _parallel_enabled() or len(files) < _parallel_threshold():
        return _scan_path_serial(plugin_root, files)

    return _scan_path_parallel(plugin_root, files)


def _scan_path_serial(plugin_root: Path, files: list[Path]) -> tuple[list[dict[str, Any]], int]:
    """Serial scan loop — routes through `_scan_one_file_skillaudit`.

    Up through v2.103.x this was an independent re-implementation of
    the per-file body, but the v2.104.0 cache + binary integration
    needs the SAME code path in both serial and parallel mode (parity
    is a hard test contract). Routing both through
    `_scan_one_file_skillaudit` plus the same env-var bootstrap means:

      - sentinel semantics are identical (`"_skillaudit_sentinel": "scanned"`
        means the file counted; `"empty"` means it was zero-byte but
        counted; absent + empty list means unreadable);
      - cache hits in the parallel path are identical to cache hits
        in the serial path;
      - binary scanner routing is identical (a binary file in either
        mode yields binary-scanner findings or a "scanned" sentinel,
        never a UTF-8 decode-of-binary garble).

    Env-var bootstrap mirrors `_scan_path_parallel`: the worker reads
    `CPV_SKILLAUDIT_WORKER_PLUGIN_ROOT` for relative-path resolution.
    """
    prev_env = os.environ.get(_WORKER_ENV_PLUGIN_ROOT)
    os.environ[_WORKER_ENV_PLUGIN_ROOT] = str(plugin_root)
    try:
        all_findings: list[dict[str, Any]] = []
        files_scanned = 0
        for fp in files:
            results = _scan_one_file_skillaudit(fp)
            if not results:
                # unreadable file — no sentinel, not counted
                continue
            had_sentinel = False
            for f in results:
                if f.get("_skillaudit_sentinel"):
                    had_sentinel = True
                    continue
                all_findings.append(f)
            if had_sentinel or any(not f.get("_skillaudit_sentinel") for f in results):
                files_scanned += 1
    finally:
        if prev_env is None:
            os.environ.pop(_WORKER_ENV_PLUGIN_ROOT, None)
        else:
            os.environ[_WORKER_ENV_PLUGIN_ROOT] = prev_env
    return all_findings, files_scanned


def _scan_path_parallel(plugin_root: Path, files: list[Path]) -> tuple[list[dict[str, Any]], int]:
    """Parallel scan path — dispatches per-file work via parallel_scan.

    Encodes the plugin root into ``CPV_SKILLAUDIT_WORKER_PLUGIN_ROOT``
    before pool creation (env vars are inherited by ``spawn``-method
    workers; module globals are not). Restores the prior env value
    after the pool finishes so an orchestrator running multiple
    scan_path calls back-to-back against different plugins doesn't
    leak state.

    On per-file worker failure, surfaces a WARNING-equivalent finding
    in the aggregate so the file's error is visible but the scan as
    a whole still completes. This mirrors the spec ("when
    ScanResult.error is set, surface as per-file WARNING and
    continue — never crash the whole scan").
    """
    # Lazy import — keeps the module's stdlib-only invariant intact at
    # IMPORT time (parallel_runner uses concurrent.futures which is
    # stdlib, so this import is still pure-stdlib at runtime). The
    # test ``test_module_imports_only_stdlib`` walks top-level
    # imports; this lazy import keeps that gate green by being inside
    # a function body.
    from cpv_parallel_runner import parallel_scan  # noqa: PLC0415

    prev_env = os.environ.get(_WORKER_ENV_PLUGIN_ROOT)
    os.environ[_WORKER_ENV_PLUGIN_ROOT] = str(plugin_root)
    try:
        scan_results = parallel_scan(files, _scan_one_file_skillaudit)
    finally:
        if prev_env is None:
            os.environ.pop(_WORKER_ENV_PLUGIN_ROOT, None)
        else:
            os.environ[_WORKER_ENV_PLUGIN_ROOT] = prev_env

    all_findings: list[dict[str, Any]] = []
    files_scanned = 0

    # Aggregate per-file results IN INPUT ORDER. The harness contract
    # guarantees ``scan_results[i].file_path == files[i]`` so we can
    # iterate by index without zip+sort.
    for idx, scan_result in enumerate(scan_results):
        fp = files[idx]
        if scan_result.error is not None:
            # Worker crashed — surface as a WARNING-like finding so
            # the file's failure is visible in the aggregate, but the
            # scan as a whole still completes. The shape mirrors a
            # real skillaudit finding so downstream consumers
            # (report_findings, severity grouping) don't crash on a
            # missing key.
            rel = str(fp)
            try:
                rel = str(fp.relative_to(plugin_root))
            except ValueError:
                pass
            all_findings.append(
                {
                    "ruleId": "SKILLAUDIT_WORKER_ERROR",
                    "severity": "low",  # → CPV nit → emitted as WARNING-class
                    "category": "infrastructure",
                    "name": "Skillaudit worker failed on this file",
                    "description": (
                        f"Per-file skillaudit scan worker raised: "
                        f"{scan_result.error}. File not scanned; other "
                        f"files in the tree were unaffected."
                    ),
                    "line": 0,
                    "lineContent": "",
                    "match": "",
                    "suppressed": False,
                    "file": rel,
                }
            )
            # The file wasn't scanned to completion — don't increment
            # files_scanned. Matches the serial loop's behaviour for
            # an unreadable file (no increment).
            continue

        # The worker either returned real findings, a sentinel (empty
        # file or scanned-but-clean), or an empty list (unreadable).
        # Strip sentinels before merging.
        had_sentinel = False
        for f in scan_result.findings:
            if f.get("_skillaudit_sentinel"):
                had_sentinel = True
                continue
            all_findings.append(f)

        # files_scanned semantics:
        #   - unreadable file (empty list, no sentinel) → not counted
        #   - empty file (sentinel "empty") → counted
        #   - scanned non-empty file (any real finding OR sentinel
        #     "scanned") → counted
        if had_sentinel or any(not f.get("_skillaudit_sentinel") for f in scan_result.findings):
            files_scanned += 1

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


def _suppress_binary_placeholder(finding: dict[str, Any]) -> None:
    """In-place: suppress a binary finding whose extracted match is a
    placeholder token (``YOUR_API_KEY`` / ``<token>`` / ``xxx`` / …).

    The binary scan path has no documentation/code-fence context, so the
    text path's full ``_confidence`` machinery doesn't apply — but a
    string-table match that is clearly a placeholder (extracted from a
    bundled sample/help blob inside the binary) is still a benign
    non-threat and SHOULD suppress, exactly as it would in the text path.
    Everything else stays at the catalog severity (binary matches are NOT
    demoted — there is no prose context to justify a demote). (audit MINOR #6)

    The match carries the ``BINARY_PREFIX`` provenance tag; we strip it
    before the placeholder check so the test runs against the real
    extracted token.
    """
    raw_match = str(finding.get("match", ""))
    if raw_match.startswith(_BINARY_PREFIX):
        raw_match = raw_match[len(_BINARY_PREFIX) :]
    if raw_match and _has_placeholder(raw_match):
        finding["severity"] = "info"
        finding["suppressed"] = True


def _normalize_unicode_for_test(text: str) -> str:
    """Normalize to NFC so the invisible-char scanner can be tested deterministically."""
    return unicodedata.normalize("NFC", text)
