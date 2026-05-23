#!/usr/bin/env python3
"""TypeScript / JavaScript context classifier for SkillAudit (issue #39).

Given a TS/JS source file plus a line index that a SkillAudit regex
matched against, classify the surrounding source shape so the matcher
can distinguish real exploitation (credential exfiltration via fetch,
hardcoded secrets shipped to production, SQL injection of attacker
input) from legitimate idioms (an MCP server reading its OWN configured
API key, a redaction allow-list of secret NAMES used to scrub output,
test-fixture secrets / SQL strings used only inside `*.test.ts`).

The classifier never parses TS syntax — TypeScript / JSX would need a
heavyweight parser (tree-sitter / swc / esbuild). Instead we use
**line-window regex heuristics** strong enough to recognise the
canonical FP shapes from issue #39:

* ``process.env.<KEY>`` read with NO outbound HTTP sink in the
  surrounding ±5 lines AND no write to an attacker-controlled path →
  ``safe_literal``. This is the 12-factor pattern: a plugin reads its
  own configured API key.

* A regex-literal (``/pattern/flags``) that mentions secret NAMES
  (``DISCORD_TOKEN``, ``OPENAI_API_KEY``, …) is the canonical
  REDACTION allow-list shape — the plugin scrubs secrets from output
  BEFORE shipping. The TOKEN_STEAL rule should not flag the protection
  mechanism as the attack. → ``safe_literal``.

* Lines inside test/fixture files (``*.test.ts``, ``*.spec.ts``,
  ``__tests__/``, ``tests/``, ``mocks/``) where the matched secret is
  obviously synthetic (``sk-`` followed by repeated ``a``/``1``/etc.
  characters OR a single ``sk-1234…`` repeating placeholder) →
  ``safe_literal``. Real production secrets do NOT live in test files.

* SQL_INJECTION matches inside test-file string-array literals that
  are written to a tmp fixture file (``writeFileSync(secretFile, […])``)
  → ``safe_literal``. The vulnerable shape is sample data the test
  generates and feeds to the scanner, not production-vulnerable code.

Conservative: when in doubt, return ``"unknown"`` so the existing
heuristic chain runs. The iron rule — "better safe than sorry" —
stays in force; every safe_literal verdict is paid for by a tight
AST-like shape check, not a broad pattern allowlist.
"""

from __future__ import annotations

import re
from typing import Final, Literal

ContextVerdict = Literal["safe_literal", "safe_doc", "suspect", "unknown"]


# Regex patterns that signal an outbound credential-exfiltration sink
# on a line. If ANY of these appear in the ±5 line window AND on the
# same line as a `process.env.<KEY>` read, the env-read might be
# feeding a real exfiltration → suspect, not safe.
_EXFIL_SINK_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        # fetch / axios / XHR with the env value in the body or URL.
        r"\bfetch\s*\(\s*[`'\"]https?://(?!api\.openrouter\.|openrouter\.|api\.anthropic\.|api\.openai\.|api\.together\.xyz|api\.groq\.|api\.mistral\.|api\.cohere\.|api\.replicate\.|api\.deepseek\.|api\.fireworks\.|api\.huggingface\.|huggingface\.co|api\.x\.ai|api\.perplexity\.|api\.voyageai\.|api\.elevenlabs\.|generativelanguage\.googleapis\.com|api\.stability\.ai)",
        # axios.post/get to anywhere with a body containing API_KEY.
        r"\baxios\.(?:post|get|put|delete|request)\s*\(\s*[`'\"]https?://(?!api\.openrouter\.|openrouter\.|api\.anthropic\.|api\.openai\.|api\.together\.xyz|api\.groq\.|api\.mistral\.|api\.cohere\.|api\.replicate\.|api\.deepseek\.|api\.fireworks\.|api\.huggingface\.|huggingface\.co|api\.x\.ai|api\.perplexity\.|api\.voyageai\.|api\.elevenlabs\.|generativelanguage\.googleapis\.com|api\.stability\.ai)",
        # webhook.site / requestbin / ngrok / etc.
        r"https?://(?:webhook\.site|requestbin|pipedream|ngrok|burpcollaborator|interact\.sh|oastify|hookbin|postb\.in|transfer\.sh|file\.io|pastebin\.com)",
        # outbound IP literal HTTP/HTTPS (numbered IP).
        r"https?://(?:\d{1,3}\.){3}\d{1,3}",
        # Sending env var to clipboard / file outside cwd.
        r"\bclipboard\.write",
    )
)


# Known "trusted" host fragments — when an env-read line ALSO targets
# one of these as the destination, it's a legitimate API call (the
# plugin is calling its own configured service). Used in conjunction
# with the exfil-sink regex (which already excludes them).
_TRUSTED_API_HOST_FRAGMENTS: Final[frozenset[str]] = frozenset(
    {
        "openrouter.ai",
        "api.anthropic.com",
        "api.openai.com",
        "api.together.xyz",
        "api.groq.com",
        "api.mistral.ai",
        "api.cohere.ai",
        "api.replicate.com",
        "api.deepseek.com",
        "api.fireworks.ai",
        "huggingface.co",
        "api.x.ai",
        "api.perplexity.ai",
        "api.voyageai.com",
        "api.elevenlabs.io",
        "generativelanguage.googleapis.com",
        "api.stability.ai",
        "localhost",
        "127.0.0.1",
    }
)


# Env-var name fragments that are PROVABLY the plugin's own
# configured API key — reading them is the 12-factor pattern, not
# credential theft. The match must be a `process.env.<NAME>` shape
# where NAME contains one of these fragments.
_OWN_API_KEY_FRAGMENTS: Final[frozenset[str]] = frozenset(
    {
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "REPLICATE_API_TOKEN",
        "DEEPSEEK_API_KEY",
        "FIREWORKS_API_KEY",
        "HF_TOKEN",
        "HUGGINGFACE_TOKEN",
        "TOGETHER_API_KEY",
        "X_AI_API_KEY",
        "XAI_API_KEY",
        "PERPLEXITY_API_KEY",
        "VOYAGE_API_KEY",
        "ELEVENLABS_API_KEY",
        "STABILITY_API_KEY",
        "VLLM_API_KEY",
        "LM_API_TOKEN",
        "OLLAMA_HOST",
        # Plugin-system bridge: when the plugin sets userConfig in
        # plugin.json, Claude Code exports the value as
        # CLAUDE_PLUGIN_OPTION_<OPTION_NAME>.
        "CLAUDE_PLUGIN_OPTION_",
    }
)


_PROCESS_ENV_RE: Final[re.Pattern[str]] = re.compile(
    r"process\.env\.(?P<name>[A-Z][A-Z0-9_]*)"
)

_REGEX_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    # Naïve JS-regex-literal recognizer — ``/.../[gimsuy]*``. False
    # positives on division operators are tolerated because we only
    # treat the result as a HINT (downstream still checks for the
    # secret-name list pattern).
    r"/(?P<body>(?:\\.|[^/\\\n]){2,})/(?P<flags>[gimsuy]{0,6})"
)

# Markers that almost-certainly identify a test-fixture / sample-doc
# string inside a JS/TS file. We use these to decide that SQL/secret
# matches inside the string array are part of the test scaffolding.
_TEST_FIXTURE_MARKERS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        r"\bwriteFileSync\b",       # creating a test file on disk
        r"\bcreateClient\b",        # MCP test client setup
        r"\bcleanDir\b",            # test directory teardown
        r"\btmpDir\b",              # tmp-dir convention
        r"\b__tests__\b",
        r"\bvitest\b",
        r"\bjest\b",
        r"\bmocha\b",
        r"\bbeforeAll\b",
        r"\bafterAll\b",
        r"\bbeforeEach\b",
        r"\bdescribe\s*\(",
        r"\bit\s*\(",
    )
)


def _is_test_file(file_path: str) -> bool:
    """True iff ``file_path`` looks like a TS/JS test or fixture file."""
    fp = file_path.replace("\\", "/").lower()
    if not fp:
        return False
    if fp.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".test.mjs", ".test.cjs")):
        return True
    if fp.endswith((".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx", ".spec.mjs", ".spec.cjs")):
        return True
    parts = fp.split("/")
    if "__tests__" in parts:
        return True
    if "test" in parts or "tests" in parts:
        return True
    if "fixtures" in parts or "__fixtures__" in parts:
        return True
    if "mocks" in parts or "__mocks__" in parts:
        return True
    return False


def _line_window(source: str, line_idx: int, span: int = 5) -> list[str]:
    """Return the ±``span`` lines around ``line_idx``."""
    lines = source.splitlines()
    lo = max(0, line_idx - span)
    hi = min(len(lines), line_idx + span + 1)
    return lines[lo:hi]


def _window_has_exfil_sink(source: str, line_idx: int, span: int = 5) -> bool:
    """True iff the ±``span`` lines around ``line_idx`` contain an
    outbound HTTP sink to a non-trusted host (or a webhook collector
    / IP-literal / clipboard write)."""
    window = "\n".join(_line_window(source, line_idx, span))
    return any(p.search(window) for p in _EXFIL_SINK_PATTERNS)


def _line_inside_regex_literal(line: str, match: str) -> bool:
    """True iff ``match`` substring sits inside a JavaScript regex
    literal (``/<body>/<flags>``) on ``line``.

    Used to recognize the canonical redaction allow-list shape:

        const SECRET_NAMES = /OPENAI_API_KEY|GITHUB_TOKEN|DISCORD_TOKEN/gim;
        // …
        body.replace(SECRET_NAMES, '[REDACTED]')

    The TOKEN_STEAL rule's pattern matches `DISCORD_TOKEN` inside the
    regex body. The line is regex-literal source code, not a code
    path that exfiltrates a token. We classify as safe_literal.

    Conservative: only returns True when the match is FULLY inside a
    regex literal span AND that span contains an alternation (``|``)
    of multiple uppercase identifiers — characteristic of a name
    allow-list, not of a single-target regex.
    """
    if not match or match not in line:
        return False
    # Locate the first regex literal whose body fully covers the match.
    for rm in _REGEX_LITERAL_RE.finditer(line):
        body_start = rm.start() + 1  # +1 for opening '/'
        body_end = rm.start() + 1 + len(rm.group("body"))
        m_start = line.find(match)
        if m_start < 0:
            continue
        m_end = m_start + len(match)
        if body_start <= m_start and m_end <= body_end:
            body = rm.group("body")
            # Must look like an allow-list — at least one '|' AND at
            # least 2 uppercase tokens of length ≥ 3.
            if "|" not in body:
                continue
            tokens = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", body)
            if len(tokens) >= 2:
                return True
    return False


def _process_env_match_is_own_api_key(line: str, match: str) -> bool:
    """True iff the matched substring is a ``process.env.<KEY>`` read
    where ``<KEY>`` contains one of the plugin's own API-key
    fragments (e.g. OPENROUTER_API_KEY, CLAUDE_PLUGIN_OPTION_*).

    A read of an env var that the plugin itself documents and uses
    (the canonical 12-factor pattern) is not credential theft. The
    issue #39 FPs all match this shape:

        authToken = process.env.OPENROUTER_API_KEY ?? "";
    """
    # Walk every process.env.<NAME> reference on the line.
    for m in _PROCESS_ENV_RE.finditer(line):
        name = m.group("name")
        for frag in _OWN_API_KEY_FRAGMENTS:
            if frag in name:
                return True
    # Fallback: the matched text itself looks like a process.env access
    # — extract the name from the match.
    em = _PROCESS_ENV_RE.search(match)
    if em is not None:
        name = em.group("name")
        for frag in _OWN_API_KEY_FRAGMENTS:
            if frag in name:
                return True
    return False


_FAKE_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        # `sk-` + repeated single char: sk-aaaaaaaaaaa, sk-111111111
        r"\bsk-(?:proj-)?([A-Za-z0-9])\1{15,}\b",
        # `sk-1234567890abcdef…` test placeholder.
        r"\bsk-(?:proj-)?(?:0123456789|1234567890|abcdef|deadbeef|test|fake|dummy|sample|example)",
        # `sk-` + obvious sequential ASCII / hex repetition.
        r"\bsk-(?:proj-)?(?:[0-9]{1,5}[a-f]{1,5}){2,}\b",
    )
)


def _is_obviously_fake_secret(match: str) -> bool:
    """True iff ``match`` is a synthetic test-fixture secret rather
    than a real production key.

    Real OpenAI/Anthropic keys have high entropy and no obvious
    repetition. Test fixtures use ``sk-aaaa…``, ``sk-1234567890…``,
    ``sk-deadbeef…``, or similar patterns the human can spot by eye.
    The classifier accepts the obvious ones; entropy-based detectors
    aren't worth the false-positive risk on real secrets that happen
    to contain a short repeated run.
    """
    if not match:
        return False
    for p in _FAKE_SECRET_PATTERNS:
        if p.search(match):
            return True
    return False


def _window_has_test_fixture_marker(source: str, line_idx: int, span: int = 12) -> bool:
    """True iff the ±``span`` lines around ``line_idx`` contain a
    Jest / Vitest / writeFileSync / createClient marker — i.e. the
    line is inside test-suite code, not production code."""
    window = "\n".join(_line_window(source, line_idx, span))
    return any(p.search(window) for p in _TEST_FIXTURE_MARKERS)


def classify(
    file_path: str,
    source: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> ContextVerdict:
    """Classify a SkillAudit match in a TypeScript / JavaScript file.

    See module docstring for the full verdict matrix. The classifier
    is **only** called for the SkillAudit rule IDs documented in the
    issue #39 FP set; other matches fall through to "unknown" so the
    existing heuristic chain runs.
    """
    if not (0 <= line_idx < len(source.splitlines())):
        return "unknown"
    line = source.splitlines()[line_idx]
    is_test = _is_test_file(file_path)

    # ── CRED_ENV_READ — "MCP server reading its own API key" FP. ──
    # The matched substring is a `process.env.<KNOWN_KEY>` read. If
    # the surrounding ±5 lines contain no outbound HTTP sink to an
    # untrusted host (or a webhook collector / IP literal), this is
    # the 12-factor pattern, not credential theft.
    if rule_id == "CRED_ENV_READ":
        if _process_env_match_is_own_api_key(line, match):
            if not _window_has_exfil_sink(source, line_idx, span=5):
                return "safe_literal"
            # Sink found — keep at declared severity.
            return "suspect"
        # Match was not on a known own-API-key env var. Could be
        # legitimate ENV-driven config OR credential theft. Leave
        # unknown so the heuristic chain runs.
        return "unknown"

    # ── TOKEN_STEAL — "redaction allow-list regex" FP. ──
    # The matched substring sits inside a JavaScript regex literal
    # that alternates between secret-NAME tokens (canonical scan_secrets
    # / redact_secrets shape). The plugin uses the regex to FIND
    # secrets and REPLACE them with [REDACTED]; flagging the matcher
    # as the attack is backwards.
    if rule_id == "TOKEN_STEAL":
        if _line_inside_regex_literal(line, match):
            return "safe_literal"
        return "unknown"

    # ── SECRET_* — synthetic test-fixture secrets in *.test.ts FP. ──
    # Real plugin tests need to seed an obviously-fake secret into
    # their fixture so the scan_secrets / redact_secrets logic
    # actually trips. The fixture lives in a test file; the secret
    # value matches one of `_FAKE_SECRET_PATTERNS`. Both conditions
    # must hold — a real secret accidentally committed to a test
    # file still trips the rule.
    if rule_id.startswith("SECRET_"):
        if is_test and _is_obviously_fake_secret(match):
            return "safe_literal"
        # Real-looking secret in a non-test file → keep.
        return "unknown"

    # ── SQL_INJECTION inside test-fixture data → safe. ──
    # Real plugin tests need to write sample source code (sometimes
    # with intentional bugs the SUT is supposed to catch) into a tmp
    # file before invoking the production scanner against it. The
    # SQL_INJECTION rule matches the sample's deprecated query API
    # inside a writeFileSync([...] string array — but no production
    # code path runs the sample.
    #
    # Two cumulative signals are accepted (either suffices):
    #   1. fixture marker in ±25 lines (writeFileSync / createClient
    #      / cleanDir / etc.) AND we're in a test file
    #   2. line is a string-array element (single-quoted line whose
    #      stripped form starts with `'` and ends with `',`) and the
    #      same scan finds a writeFileSync upstream
    if rule_id == "SQL_INJECTION":
        if is_test:
            if _window_has_test_fixture_marker(source, line_idx, span=25):
                return "safe_literal"
            if _line_is_string_array_element(line) and _window_has_test_fixture_marker(
                source, line_idx, span=50
            ):
                return "safe_literal"
        return "unknown"

    return "unknown"


_ARRAY_ELEMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*['\"`].*['\"`]\s*,?\s*$"
)


def _line_is_string_array_element(line: str) -> bool:
    """True iff ``line`` looks like a single string literal that's an
    element of a string-array literal — the canonical shape inside

        writeFileSync(filename, [
          'line one',
          'line two',
          'line three',
        ].join('\\n'));

    Each element is a single-quoted (or double / backtick) string
    optionally followed by a comma. Lines that ARE the array opener
    or closer (``[``, ``]``) don't match — only the content lines.
    """
    return bool(_ARRAY_ELEMENT_RE.match(line))
