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


# ── Issue #41 — exec-sink detection for the CMD_INJECTION discriminator ──
# In JavaScript / TypeScript a backtick `…` is ALWAYS a template-literal
# STRING — there is no command-substitution semantics (unlike shell, Perl,
# Ruby). So a CMD_INJECTION match on a backtick literal is a real threat
# ONLY if that literal is syntactically an argument to a process-spawning
# sink. If no sink is on the line, the match is provably an inert string.
_EXEC_SINK_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:exec|execSync|execFile|execFileSync|spawn|spawnSync|fork)\s*\("
    r"|child_process"
    r"|\.exec\s*\("
    r"|shell\s*:\s*true"
    r"|\bsh\s*-c\b"
)


def _match_is_backtick_literal(match: str) -> bool:
    """True iff ``match`` is a backtick-delimited template literal.

    The CMD_INJECTION catalog pattern that over-fires
    (``\\`\\s*\\b(?:curl|wget|cat|ls|whoami|id|uname)\\b…\\``) captures the
    surrounding backticks, so the matched text itself begins and ends with
    a backtick when it is a template literal. That is the cheap, certain
    signal that we are looking at a JS string, not a shell command.
    """
    m = match.strip()
    return len(m) >= 2 and m.startswith("`") and m.endswith("`")


def _line_has_exec_sink(line: str) -> bool:
    """True iff ``line`` contains a process-spawning sink (exec / spawn /
    child_process / shell:true / sh -c)."""
    return _EXEC_SINK_RE.search(line) is not None


# ── Issue #41 — ENV_RECON benign-read discriminator ──
# These reads gather environment facts but are inert unless the result is
# sent to a network sink. The ENV_RECON catalog patterns that bundle the
# sink in-pattern (``env | curl``, ``printenv … curl``) are NOT in this
# set, so they still fire.
_BENIGN_ENV_READ_RE: Final[re.Pattern[str]] = re.compile(
    r"process\.cwd\s*\(|process\.argv"
    r"|os\.hostname\s*\(|os\.platform\s*\(|os\.userInfo\s*\("
    r"|os\.homedir\s*\(|os\.networkInterfaces"
)


def _is_benign_env_read(line: str, match: str) -> bool:
    """True iff the match is one of the inert environment reads (cwd / argv
    / os.*), not an exfil-bundled pattern."""
    return bool(_BENIGN_ENV_READ_RE.search(match) or _BENIGN_ENV_READ_RE.search(line))


# ── Issue #41 — SSRF static-literal discriminator ──
# SSRF requires an ATTACKER-CONTROLLED destination. A URL that is a 100%
# static string literal (no ``${…}`` interpolation, no ``+`` concatenation
# with a variable) has a fixed author-time destination and is, by
# definition, not attacker-controlled — provably not SSRF.
# SSRF_PATTERN targets DANGEROUS FIXED hosts. Cloud-metadata endpoints,
# file://, gopher://, and the 169.254.0.0/16 link-local range are NEVER benign —
# they are dangerous PRECISELY because they are hardcoded internal targets, so
# "it's a static literal" does not make them safe (it makes them the exact
# threat: e.g. fetch("http://169.254.169.254/latest/meta-data/iam/…") steals
# IAM creds). Such a URL must NOT be certified a benign static literal. NOTE:
# plain localhost / 127.0.0.1 are intentionally NOT here — those are common
# benign dev-config defaults and stay suppressible when static (audit CRITICAL #2).
_SSRF_NEVER_BENIGN_HOST_RE = re.compile(
    r"169\.254\."  # link-local (AWS/Azure/GCP IMDS)
    r"|metadata\.google\.internal"
    r"|/latest/meta-data"  # AWS IMDS path
    r"|/computeMetadata"  # GCP metadata path
    r"|metadata\.azure\.com"
    r"|file://"  # local file read
    r"|gopher://"  # gopher SSRF
    r"|fd00:ec2",  # AWS IPv6 IMDS
    re.IGNORECASE,
)


def _ssrf_url_is_static_literal(line: str, match: str) -> bool:
    """True iff the matched URL substring sits inside a string literal whose
    value is fully static (no interpolation, no concatenation).

    ``defaultUrl: "http://localhost:1234"``        → static  (safe)
    ``fetch("http://localhost:" + req.query.port)``→ concat  (keep)
    ``fetch(`http://localhost:${port}`)``          → interp  (keep)

    A never-benign host (cloud-metadata / file:// / gopher:// / link-local) is
    NEVER a safe static literal — see ``_SSRF_NEVER_BENIGN_HOST_RE`` (CRITICAL #2).
    """
    if _SSRF_NEVER_BENIGN_HOST_RE.search(match):
        return False
    idx = line.find(match)
    if idx < 0:
        return False
    # The URL is DYNAMIC (attacker-influenceable) iff it is interpolated or
    # concatenated. Look at the tail of the URL token (up to the next
    # delimiter) for ``${`` interpolation, and at the chars adjacent to the
    # surrounding string for ``+`` concatenation. This works whether the URL
    # sits in a single-line literal OR inside a multi-line template literal
    # (help text), where no quote appears on the same line.
    tail = line[idx:]
    # The URL token ends at the first delimiter.
    m = re.search(r"[\s\"'`,)\]}]", tail)
    url_token = tail[: m.start()] if m else tail
    if "${" in url_token:
        return False
    # Try to find an enclosing single-line quote; if found, check the literal
    # body + adjacency. We do NOT break on punctuation while scanning left —
    # string CONTENT legitimately contains commas / parens. If no quote is on
    # the line (multi-line template literal / help text), fall back to a
    # same-line concatenation check.
    open_pos = max(line.rfind('"', 0, idx), line.rfind("'", 0, idx), line.rfind("`", 0, idx))
    if open_pos >= 0:
        quote = line[open_pos]
        close_pos = line.find(quote, idx + len(match))
        if open_pos >= 0 and close_pos >= 0:
            literal_body = line[open_pos + 1 : close_pos]
            if "${" in literal_body:
                return False
            after = line[close_pos + 1 :].lstrip()
            before = line[:open_pos].rstrip()
            if after.startswith("+") or before.endswith("+"):
                return False
            return True
    # No same-line enclosing quote → multi-line literal / help text. Static
    # unless the URL token itself is interpolated (already checked) or the
    # line splices a variable next to the URL with ``+``.
    if "+" in line[max(0, idx - 2) : idx] or url_token.endswith("+"):
        return False
    return True


# ── Issue #41 — CROSS_TOOL_ACCESS API-field-name discriminator ──
# The CROSS_TOOL_ACCESS rule mixes two very different signals: (a) RUNTIME
# DATA-GRAB shapes (get_tools / call_tool / previous_tool_output /
# tool_results[) which ARE the real threat, and (b) generic LLM-API FIELD
# NAMES (system_prompt / context_window / …) that are unavoidable vocabulary
# in any LLM-client tool. We only ever soften (b), and only when the token
# is used as code structure (interface field, object key, property access,
# assignment) — never a call.
_API_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "system_prompt",
        "system_message",
        "context_window",
        "full_context",
        "conversation_history",
        "message_history",
        "chat_history",
    }
)


# Hard runtime data-grab indicators — these are the CROSS_TOOL_ACCESS
# shapes that ARE the real threat (bulk retrieval of another tool's /
# the host agent's runtime data). If any appears on the line we never
# soften, even if a field name is also present.
_RETRIEVAL_GRAB_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:get_tools|list_tools|available_tools|call_tool|invoke_tool|use_tool)\b"
    r"|\bprevious_tool_output\b"
    r"|\btool_results?\s*\["
    # snake_case OR camelCase data-grab method name, e.g.
    # ``get_all_previous_messages`` / ``getAllPreviousMessages`` /
    # ``getRecentResponses``.
    r"|\bget[_]?(?:all|previous|recent)\w*(?:message|response|output)",
    re.IGNORECASE,
)


def _is_api_field_name_in_code_structure(line: str, match: str) -> bool:
    """True iff the CROSS_TOOL_ACCESS match is an LLM-API field NAME used as
    ordinary code vocabulary (declaration, object key, property access,
    assignment, function param, CLI flag, or inside a display/error string)
    — NOT a runtime data-grab.

    The CROSS_TOOL_ACCESS rule mixes two pattern families. The FIELD-NAME
    family (``system_prompt`` / ``context_window`` / …) produces field-name
    match text; the DATA-GRAB family (``get_tools()`` / ``tool_results[`` /
    ``previous_tool_output``) produces different match text. So a field-name
    match is, by construction, LLM-client domain vocabulary — unavoidable in
    any LLM-orchestration tool — and not the threat the rule targets. We
    suppress those, but ONLY when the line carries no hard data-grab
    indicator (belt-and-suspenders: a line that both names a field AND grabs
    bulk runtime data stays visible).
    """
    # Case-insensitive: the rule matches ``SYSTEM_PROMPT`` (a const) as well
    # as ``system_prompt`` (a property/key).
    match_l = match.lower()
    line_l = line.lower()
    has_field = any(name in match_l or name in line_l for name in _API_FIELD_NAMES)
    if not has_field:
        return False
    if _RETRIEVAL_GRAB_RE.search(line):
        return False
    return True


# ── Issue #41 — ENV_INJECTION generic-assignment discriminator ──
# Only the GENERIC ``process.env.X =`` / ``os.environ[X] =`` shape is
# softened (in test files). The dangerous specific-var injection patterns
# (LD_PRELOAD / NODE_OPTIONS / PYTHONSTARTUP / GIT_SSH_COMMAND / PATH / …)
# are SEPARATE catalog patterns whose match text contains those tokens, so
# this returns False for them and they stay visible.
_DANGEROUS_ENV_VARS_RE: Final[re.Pattern[str]] = re.compile(
    r"LD_PRELOAD|LD_LIBRARY_PATH|DYLD_|NODE_OPTIONS|PYTHONPATH|PYTHONSTARTUP"
    r"|RUBYLIB|PERL5LIB|CLASSPATH|GIT_SSH_COMMAND|\bPATH\b"
)
_GENERIC_ENV_ASSIGN_RE: Final[re.Pattern[str]] = re.compile(
    r"process\.env\.[A-Za-z_][A-Za-z0-9_]*\s*=|os\.environ\[[^\]]+\]\s*="
)


def _is_generic_env_assignment(line: str, match: str) -> bool:
    """True iff the line is a generic ``process.env.X =`` / ``os.environ[X] =``
    assignment that does NOT target a known hijack variable."""
    if _DANGEROUS_ENV_VARS_RE.search(line):
        return False
    return bool(_GENERIC_ENV_ASSIGN_RE.search(line) or _GENERIC_ENV_ASSIGN_RE.search(match))


_IMPORT_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:"
    r"import\b.*\bfrom\b\s*['\"`]"  # ES module import
    r"|"
    r"(?:const|let|var)\s*(?:\{[^}]*\}|[A-Za-z_$][\w$]*)\s*=\s*require\s*\("  # CommonJS require
    r"|"
    r"import\s+['\"`]"  # side-effect-only import
    r"|"
    r"export\s+\{?[^}]*\}?\s+from\s+['\"`]"  # re-export
    r")"
)


def _line_is_import_or_require(source_line: str) -> bool:
    """True iff ``source_line`` is a Python-style import or JS/TS
    ``import .. from ..`` / ``const X = require(..)`` line.

    SHELL_EXEC patterns like ``child_process``, ``execSync``, ``spawn``
    fire on the import line itself (``const { execSync } = require(
    'child_process');``). The import binds the function but does NOT
    invoke it — actual calls fire on separate lines and are scanned
    normally.
    """
    return bool(_IMPORT_LINE_RE.match(source_line))


_STATIC_EXEC_CALL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:execSync|exec|spawn|spawnSync|execFile|execFileSync|fork)\s*\(\s*['\"]"
    r"[^'\"]*['\"]\s*(?:,|\))"
)


def _line_is_static_exec_call(source_line: str) -> bool:
    """True iff ``source_line`` contains an ``execSync('static-string', ...)``
    / ``spawn("git", ["status"])`` etc. call whose FIRST arg is a pure
    string literal (no template literal, no variable, no concatenation).

    A static-string exec invocation is the same shape as
    ``subprocess.run(["git", "status"])`` in Python — argv is fixed by
    the author, no injection surface.
    """
    return bool(_STATIC_EXEC_CALL_RE.search(source_line))


# r05 ananddtyagi FP iter1 (2026-05-27) — JS/TS function-definition shapes.
# SSRF_ADVANCED fires on ``server.handleRequest(request)`` etc., but those
# are method calls on a local object, not outbound HTTP calls.
_FUNCTION_DEF_RES: Final[tuple[re.Pattern[str], ...]] = (
    # async / function / arrow function with explicit name
    re.compile(
        r"^\s*(?:async\s+|export\s+|public\s+|private\s+|protected\s+|static\s+)*"
        r"(?:function\s+|async\s+function\s+)?"
        r"[A-Za-z_$][\w$]*\s*\([^)]*\)\s*[{:=]"
    ),
    # Method definition: ``name(args) {`` inside a class body
    re.compile(
        r"^\s*(?:async\s+|public\s+|private\s+|protected\s+|static\s+|override\s+)*"
        r"[A-Za-z_$][\w$]*\s*\([^)]*\)\s*\{"
    ),
    # const / let / var = (args) =>
    re.compile(
        r"^\s*(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
    ),
    # method call on object: ``obj.handleRequest(request);`` — this is also
    # a function INVOCATION (not definition), but the invocation is on a
    # local method, not an HTTP function. The SSRF_ADVANCED pattern is too
    # generic to distinguish.
    re.compile(
        r"\b[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*\s*\("
    ),
)


# r07 jarrodwatts FP iter1 (2026-05-28) — Node.js test scaffolding shapes.
# Object.defineProperty(process.stdin, 'isTTY', {...}) and similar are
# the canonical way to monkeypatch stdin/stdout for test isolation.
_TEST_MONKEYPATCH_DEFINEPROPERTY_RE: Final[re.Pattern[str]] = re.compile(
    r"\bObject\.defineProperty\s*\(\s*"
    r"(?:process\.(?:stdin|stdout|stderr|env)|"
    r"globalThis|global|window|self|"
    r"[A-Za-z_$][\w$]*Stream)"
)


def _is_test_monkeypatch_defineProperty(source_line: str) -> bool:
    """True iff ``source_line`` contains a Node.js test-monkeypatch
    ``Object.defineProperty(process.<stream>, ...)`` shape used to
    fake TTY/env/etc. for test isolation.

    Iron rule preserved: only fires in test files (already gated by
    caller). Real ``Object.defineProperty(window, '__proto__', ...)``
    style tool-shadowing in production code stays visible.
    """
    return bool(_TEST_MONKEYPATCH_DEFINEPROPERTY_RE.search(source_line))


# r07 jarrodwatts FP iter1 (2026-05-28) — test fixture arrays of
# Unicode bidi/zero-width/format characters used by detection-tests.
_TEST_UNICODE_VOCAB_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:bidi|bidirectional|rtl|ltr|isolate|zero[-_]?width|"
    r"invisible|hidden|unicode|format[-_]?char|"
    r"zwnj|zwj|lrm|rlm|lre|rle|lro|rlo|pop|fsi|lri|rli|pdf|pdi)",
    re.IGNORECASE,
)
# Hidden/invisible Unicode codepoints (bidi controls, zero-width chars,
# format chars, line/paragraph separators) — matching either as raw
# code-points OR as ``\\uXXXX`` / ``\\xNN`` escape syntax in source.
_HIDDEN_UNICODE_CODEPOINTS: Final[tuple[str, ...]] = (
    "​",  # ZWSP
    "‌",  # ZWNJ
    "‍",  # ZWJ
    "‎",  # LRM
    "‏",  # RLM
    " ",  # LS
    " ",  # PS
    "‪",  # LRE
    "‫",  # RLE
    "‬",  # PDF
    "‭",  # LRO
    "‮",  # RLO
    "⁠",  # WJ
    "⁦",  # LRI
    "⁧",  # RLI
    "⁨",  # FSI
    "⁩",  # PDI
    "؜",  # ALM
    "­",  # SHY
    "﻿",  # BOM / ZWNBSP
)
_HIDDEN_UNICODE_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(
    r"\\u(?:202[a-eA-E]|200[b-fB-F]|2028|2029|2060|206[6-9]|061[cC]|00[aA][dD]|[fF][eE][fF][fF])|"
    r"\\x(?:[aA][dD])"
)


def _is_test_unicode_fixture_array(source: str, line_idx: int, line: str) -> bool:
    """True iff ``line`` is a JS array containing 2+ hidden-Unicode-class
    characters (either as raw code-points OR as ``\\uXXXX`` escape
    sequences), AND surrounding ±2 lines mention Unicode/bidi/zero-width
    test vocabulary.

    Iron rule check (hidden-content rules normally NEVER suppress):
      - File must be a test file (gated by caller)
      - The match line must contain ≥2 hidden-class Unicode chars
      - Must look like array literal (contains '[' / ']' / ',')
      - Nearby lines must mention bidi/unicode/zero-width keywords (proof
        of test-fixture intent, not data embedding)
    """
    # Count raw hidden-Unicode chars and ``\\uXXXX`` escape sequences
    raw_count = sum(line.count(cp) for cp in _HIDDEN_UNICODE_CODEPOINTS)
    escape_count = len(_HIDDEN_UNICODE_ESCAPE_RE.findall(line))
    total = raw_count + escape_count
    if total < 2:
        return False
    # Must look like an array element line: starts/contains '[' / ']' / ','
    if "[" not in line and "]" not in line and "," not in line:
        return False
    # Check ±2 surrounding lines for test-fixture vocabulary
    lines = source.splitlines()
    lo = max(0, line_idx - 2)
    hi = min(len(lines), line_idx + 3)
    window = "\n".join(lines[lo:hi])
    return bool(_TEST_UNICODE_VOCAB_RE.search(window))


def _line_is_function_definition(source_line: str) -> bool:
    """True iff ``source_line`` looks like a JS/TS function/method definition
    or a method INVOCATION on a local object (not an outbound HTTP call).

    SSRF_ADVANCED pattern fires on ``async handleRequest(request) {`` and
    ``server.handleRequest(request)`` because the catalog pattern matches
    any ``request(`` occurrence. Those are not network calls — the actual
    network functions (``fetch``, ``axios``, ``http.get``) are still
    matched by the same pattern when they appear standalone (``fetch(req.X)``
    has no preceding `.` so the method-invocation rule does NOT fire).
    """
    stripped = source_line.lstrip()
    # First, check the "method call on object" pattern — but ONLY accept
    # when the method name is NOT one of the network calls (fetch, axios,
    # http.get) because ``something.fetch(...)`` could be a third-party
    # HTTP client wrapper. The plain network names are still caught.
    method_call_re = re.compile(r"\b[A-Za-z_$][\w$]*\.([A-Za-z_$][\w$]*)\s*\(")
    for m in method_call_re.finditer(stripped):
        method_name = m.group(1)
        if method_name in ("fetch", "axios", "get", "post", "put", "delete", "patch", "request"):
            continue  # Could be a wrapped HTTP call; don't suppress
        return True
    # Then, check the function-definition shapes
    return any(p.match(stripped) for p in _FUNCTION_DEF_RES[:3])


_SSRF_RELATIVE_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:fetch|axios(?:\.[a-z]+)?|http\.get)\s*\(\s*['\"]/[^'\"]*['\"]"
)


def _ssrf_call_arg_is_relative_path(source_line: str) -> bool:
    """True iff a fetch/axios/http.get call on this line takes a STATIC
    relative path starting with ``/`` (same-origin) as its first arg.

    Same-origin relative paths cannot be SSRF — they target the same
    server hosting the code. Real SSRF needs an attacker-controlled
    absolute URL (``http://attacker.com/...``) or template-literal URL
    with user data interpolated.

    Examples:
      ``fetch('/api/users')``           → True (same-origin)
      ``fetch('/api/users', {...})``    → True (same-origin)
      ``axios.get('/v1/data')``         → True (same-origin)
      ``fetch('http://example.com')``   → False (absolute URL)
      ``fetch(`${URL}/api/${userId}`)`` → False (template literal)
    """
    return bool(_SSRF_RELATIVE_PATH_RE.search(source_line))


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

    # r04 obra FP iter1 (2026-05-27) — SHELL_EXEC pattern matched on
    # an import / require line. The import only BINDS the function —
    # invocation happens elsewhere where the rule fires again with
    # the actual call shape.
    if rule_id == "SHELL_EXEC" and _line_is_import_or_require(line):
        return "safe_literal"

    # r07 jarrodwatts FP iter1 (2026-05-28) — TOOL_SHADOW pattern fires
    # on ``Object.defineProperty(process.stdin, 'isTTY', {...})`` in
    # TEST FILES. This is the canonical Node.js stdin/stdout monkeypatch
    # shape used to fake TTY status for test isolation. Standard test
    # scaffolding, never a real tool-shadow attack.
    # Iron rule preserved: same pattern OUTSIDE a test file still fires
    # (real ``Object.defineProperty(window, '__proto__', ...)`` etc.).
    if rule_id == "TOOL_SHADOW" and is_test and _is_test_monkeypatch_defineProperty(line):
        return "safe_literal"

    # r07 jarrodwatts FP iter1 (2026-05-28) — INVISIBLE_UNICODE_RAW pattern
    # fires on test files that explicitly DECLARE arrays of
    # bidi/zero-width/format-character literals to TEST the detection
    # of these chars in user code. The test fixture's whole purpose is
    # to feed these chars to the SUT's detection code.
    # Iron rule check: this is a hidden-content rule (steganography),
    # which we normally NEVER suppress. The carve-out is narrow:
    # 1. File is a test file
    # 2. The matched chars sit inside a JS array-of-Unicode-escapes
    #    literal (``['‮', '‎', ...]``) where EVERY element is
    #    a Unicode-escape sequence (no real prose, no embedded payload)
    # 3. The match line contains ``unicode`` / ``bidi`` / ``zero-width``
    #    / ``invisible`` / ``hidden`` / ``rtl`` / ``ltr`` / ``isolate``
    #    keyword in the same line or ±2 lines (test-vocabulary signal)
    if rule_id == "INVISIBLE_UNICODE_RAW" and is_test and _is_test_unicode_fixture_array(source, line_idx, line):
        return "safe_literal"

    # r04 obra FP iter1 (2026-05-27) — SHELL_EXEC pattern matched on a
    # static-string exec call like ``execSync('dot -Tsvg', {...})``.
    # The argv is fixed at author-time; no dynamic input reaches the
    # shell. Iron-rule preserved: ``execSync(`dot ${userInput}`)`` /
    # ``execSync('dot -T' + format)`` / etc. still fire because the
    # static-string check rejects template literals and concatenation.
    if rule_id == "SHELL_EXEC" and _line_is_static_exec_call(line):
        return "safe_literal"

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

    # ── CMD_INJECTION — JS/TS template literal is NOT shell command-sub. ──
    # Issue #41 CRITICAL FP: ``return {reason: `id ${id} out of range`}``.
    # The over-broad catalog pattern matches a backtick literal that starts
    # with id/cat/ls/curl/etc. In JS a backtick is ALWAYS a string; the
    # match is a real threat ONLY if that literal is an argument to an exec
    # sink. No sink on the line → provably an inert string → safe_literal.
    # A literal that IS inside exec()/spawn()/execSync()/child_process keeps
    # the declared CRITICAL severity (the catalog's dedicated
    # ``exec\\(…\\$\\{`` patterns also cover that case).
    if rule_id == "CMD_INJECTION":
        if _match_is_backtick_literal(match) and not _line_has_exec_sink(line):
            return "safe_literal"
        return "unknown"

    # ── ENV_RECON — inert environment read with no exfil sink. ──
    # Issue #41 FP: ``return process.cwd()`` in a catch fallback.
    # Reconnaissance is gather→send; an inert read (cwd/argv/os.*) with no
    # network sink in the surrounding window cannot exfiltrate. The
    # exfil-bundled catalog patterns (``env | curl``, ``printenv … curl``)
    # are not backtick/benign reads, so they fall through to keep.
    if rule_id == "ENV_RECON":
        if _is_benign_env_read(line, match) and not _window_has_exfil_sink(source, line_idx, span=8):
            return "safe_literal"
        return "unknown"

    # ── SSRF_PATTERN — static literal destination is not attacker-controlled. ──
    # Issue #41 FP: ``defaultUrl: "http://localhost:1234"`` in a config map.
    # SSRF requires the destination to be influenced by an attacker; a fully
    # static string literal has a fixed author-time destination. A localhost
    # URL built by concatenation or interpolation stays visible (dynamic →
    # could be attacker-controlled).
    if rule_id == "SSRF_PATTERN":
        if _ssrf_url_is_static_literal(line, match):
            return "safe_literal"
        return "unknown"

    # ── CROSS_TOOL_ACCESS — display text OR LLM-client API field name. ──
    # Two FP shapes, both provably benign:
    #   1. A backtick template literal that is a string-array element (the
    #      canonical report-assembly shape, e.g.
    #      ``\`- **Tool**: \\\`${toolName}\\\`\```) — display output, not a
    #      namespace read.
    #   2. An LLM-client API FIELD NAME (system_prompt / context_window / …)
    #      used as code structure: ``context_window?: number;`` (interface
    #      field), ``profile.context_window`` (config read),
    #      ``body.system_prompt = …`` (request build). In an LLM tool these
    #      are unavoidable vocabulary.
    # The rule's DANGEROUS shapes are runtime data-grabs (``get_tools()``,
    # ``previous_tool_output``, ``tool_results[``, ``call_tool()``) — those
    # are NOT in ``_API_FIELD_NAMES`` and are not array elements, so they
    # fall through to keep.
    if rule_id == "CROSS_TOOL_ACCESS":
        if _match_is_backtick_literal(match) and _line_is_string_array_element(line):
            return "safe_literal"
        if _is_api_field_name_in_code_structure(line, match):
            return "safe_literal"
        return "unknown"

    # ── ENV_INJECTION — generic env set/restore in a test file is scaffolding. ──
    # Issue #41 FP: ``process.env.LLM_OUTPUT_DIR = ORIG_ENV.LLM_OUTPUT_DIR``
    # in an afterEach. The DANGEROUS env-injection patterns target specific
    # hijack vars (LD_PRELOAD / NODE_OPTIONS / PYTHONSTARTUP / GIT_SSH_COMMAND
    # / PATH …) — those are SEPARATE catalog patterns that still fire. We
    # suppress ONLY the GENERIC ``process.env.X =`` shape, and ONLY inside a
    # test file (where env set/restore is standard fixture teardown).
    if rule_id == "ENV_INJECTION":
        if is_test and _is_generic_env_assignment(line, match):
            return "safe_literal"
        return "unknown"

    # ── PATH_TRAVERSAL — contrived traversal string in a test fixture. ──
    # Issue #41 FP: ``"bundled:..%2F..%2F..%2Fsystem-file"`` — a deliberately
    # malicious sample input a test feeds to the traversal detector to prove
    # it FIRES. The string is test DATA (a string-array element / argument in
    # a test file), never a real filesystem access. Same shape the
    # SQL_INJECTION test handler above already trusts.
    if rule_id == "PATH_TRAVERSAL" and is_test:
        if _line_is_string_array_element(line) or _window_has_test_fixture_marker(source, line_idx, span=25):
            return "safe_literal"
        return "unknown"

    # r05 ananddtyagi FP iter1 (2026-05-27) — PATH_TRAVERSAL pattern matched
    # on a relative-path import / require statement like
    # ``import { foo } from '../../../scripts/file-path-resolver.cjs';``.
    # Relative imports up the directory tree are NORMAL JS/TS module
    # resolution — they're resolved by the module loader, not by
    # ``readFile()``. Real traversal attacks need a runtime filesystem
    # sink (readFile / fopen / createReadStream), which the dedicated
    # patterns 4/5 already cover.
    if rule_id == "PATH_TRAVERSAL" and _line_is_import_or_require(line):
        return "safe_literal"

    # r05 ananddtyagi FP iter1 (2026-05-27) — SSRF_ADVANCED pattern matched
    # on a JS/TS FUNCTION DEFINITION (not a call). The catalog pattern is
    # ``(?:fetch|axios|http\.get|\brequest)\(.*(?:req\.|...)`` and fires on
    # ``async handleRequest(request) {`` because ``\brequest\(`` matches
    # the name `request` in `handleRequest(request)`. Wait: with `\b`
    # boundaries this should already be excluded. But the function-call
    # match for ``server.handleRequest(request);`` still fires because
    # `request` appears as the parameter name. A method call like
    # `server.handleRequest(request)` invokes a USER-DEFINED method whose
    # implementation is NOT necessarily HTTP — it's just a method on a
    # local server object. Real SSRF needs an outbound network call
    # (`fetch(...)`, `axios.get(...)`, `http.get(...)`).
    if rule_id == "SSRF_ADVANCED" and _line_is_function_definition(line):
        return "safe_literal"

    # r05 ananddtyagi FP iter1 (2026-05-27) — SSRF_ADVANCED pattern 1 fires
    # on `fetch('/api/users', ...)` where the URL is a STATIC RELATIVE
    # PATH starting with `/`. Same-origin relative paths cannot be SSRF
    # — they target the same server that hosts the code. Real SSRF needs
    # an attacker-controlled absolute URL (http://attacker.com/...) or a
    # template-literal URL with user data interpolated.
    if rule_id == "SSRF_ADVANCED" and _ssrf_call_arg_is_relative_path(line):
        return "safe_literal"

    # ── OBFUSCATION — base64/charcode decode without exec sink. ──
    # Issue #41 FP: ``Buffer.from(pad, "base64").toString("utf-8")`` decoding
    # a JWT segment inside ``benchmark/fixtures/*.ts``.
    # r03 trailofbits FP iter1 extension (2026-05-27): also applies to
    # NON-test files like ``openai-develop-web-game/scripts/*.js``
    # decoding a canvas screenshot via ``Buffer.from(base64, "base64")``.
    # Real obfuscation is a decode chain that FEEDS exec sinks
    # (``eval(Buffer.from(payload, "base64").toString())`` etc.) — those
    # stay visible via the CMD_INJECTION / decode-threat rules with the
    # exec sink on the same line. A standalone decode (no exec sink) is
    # transformation of binary data (image / fixture / token / config),
    # not an attack.
    if rule_id == "OBFUSCATION" and not _line_has_exec_sink(line):
        return "safe_literal"

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
