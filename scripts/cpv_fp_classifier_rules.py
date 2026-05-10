#!/usr/bin/env python3
"""Per-rule classifiers for CPV's context-aware FP/TP disambiguation.

Step 2 of TRDD-fe006962. Each classifier here translates the v2.41
binary "skip in this context" guards into the four-tier
`FindingVerdict` ladder so we can demote (instead of suppress) when
we are uncertain. The TP-vs-FP boundary tables in the TRDD are the
contract every classifier honours:

* RC-21 — `os.environ.copy()` for subprocess env-prep is FP, but the
  same call piped to a remote-write sink is TP.
* RC-22 — clipboard read in a clipboard plugin is FP; same call in a
  productivity plugin is TP.
* RC-65 — IMDS literal in a denylist set is FP; same literal in a
  network call is TP.
* RC-87 — RFC-1918 / loopback IP inside a package-manager dep version
  is FP; same shape in a runtime path is TP.
* RC-93 — ≥30 contiguous spaces inside a markdown table row is FP;
  the same run elsewhere is real visual deception.

Each classifier returns:

* `DEFINITE_FP` when the v2.41 guard would have suppressed the match
  outright. Same effect as the legacy guard.
* `LIKELY_FP` when the match is in a context that smells benign but
  has not been confirmed. This demotes the severity by one tier.
* `REAL` (the default) when nothing in the surrounding context
  contradicts the rule's signal.

`DEFINITE_TP` (escalation) is reserved for the highest-confidence TP
contexts — currently `RC-21` copy-then-exfil-sink and `RC-65` IMDS
literal in a same-line network call. Both are unambiguous credential
or instance-metadata exfiltration; neither has a benign reading we
have observed in production. The verdict is honoured only when the
caller passes `--extreme` to `validate_security.py` (Step 4 of
TRDD-fe006962). With the flag off, `DEFINITE_TP` is treated as `REAL`
— same severity as the legacy path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from cpv_fp_classifier import (
    Context,
    FindingVerdict,
    has_sink_nearby,
    register_classifier,
)

# -----------------------------------------------------------------------------
# RC-21 — process.env / os.environ bulk harvest
# -----------------------------------------------------------------------------

_RC21_SUBPROCESS_HINTS = (
    "subprocess.",
    "Popen(",
    "subprocess.run(",
    "check_output(",
    "check_call(",
    "spawn(",
    "execve(",
    "execvp(",
    "execv(",
    "child_process.",
    "execFile(",
)

# Sink hints used to recognize EXFIL contexts. Kept narrow on purpose —
# generic substrings like `open(` would match `Popen(` and re-introduce
# the FP this whole module exists to prevent. If a future TP needs
# `open(` for file-write exfil, the corpus will catch the regression
# and we'll add a more specific token (e.g. `open(filename, "w"`).
_RC21_EXFIL_SINK_HINTS = (
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "urlopen(",
    "http.client",
    "httpx.",
    "aiohttp.",
    "fetch(",
    "axios.post",
    "axios.put",
    "sendBeacon(",
    "send_beacon(",
    "writeFile(",
    "write_text(",
    "json.dump(",
    "ftplib.",
    "smtplib.",
    'boto3.client("s3")',
    'boto3.resource("s3")',
)


@register_classifier("RC-21")
def classify_rc21(ctx: Context) -> FindingVerdict:
    """Bulk env-var harvest — subprocess prep is FP, exfil sink is TP.

    Only the COPY-style patterns (`os.environ.copy()`, `dict(os.environ)`)
    are subject to the subprocess-prep FP suppression. Iteration patterns
    (`Object.keys`, `JSON.stringify`, `for k in os.environ`) stay at REAL
    because they imply consumption of every value, which is exactly the
    exfil signal. The line check is substring-based — fast and good
    enough for v1; the corpus regression suite catches edge cases.

    Test-fixture and doc-path findings are demoted to DEFINITE_FP
    because those files exist *to* contain the pattern (the test_
    files in CPV's own suite are an example).

    Step 4 (`--extreme`): when a copy pattern is followed by an exfil
    sink in the surrounding window, the verdict is `DEFINITE_TP` so the
    `--extreme` flag can promote the declared severity (MAJOR → CRITICAL).
    Without the flag the verdict is treated as `REAL` (same severity as
    the legacy path), so this change is backwards-compatible.
    """

    if ctx.file_role in ("fixture", "test"):
        return FindingVerdict.DEFINITE_FP
    if ctx.file_role == "doc":
        return FindingVerdict.LIKELY_FP

    is_copy_pattern = "os.environ.copy()" in ctx.line or "dict(os.environ" in ctx.line
    if not is_copy_pattern:
        return FindingVerdict.REAL

    if has_sink_nearby(ctx.surrounding_lines, _RC21_EXFIL_SINK_HINTS):
        # Strongest TP signal: copy + nearby exfil sink. No benign reading
        # has been observed in the v2.40.x sweep corpus or the seven
        # emasoft-plugins. Mark as DEFINITE_TP so --extreme can escalate.
        return FindingVerdict.DEFINITE_TP
    # Subprocess hints are checked ONLY in the surrounding lines so a
    # bare `env = os.environ.copy()` without nearby Popen/run stays
    # LIKELY_FP (insufficient context to call DEFINITE_FP).
    if has_sink_nearby(ctx.surrounding_lines, _RC21_SUBPROCESS_HINTS):
        return FindingVerdict.DEFINITE_FP
    return FindingVerdict.LIKELY_FP


# -----------------------------------------------------------------------------
# RC-22 — clipboard read
# -----------------------------------------------------------------------------

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


@register_classifier("RC-22")
def classify_rc22(ctx: Context) -> FindingVerdict:
    """Clipboard read — domain-aware exemption.

    Reads `plugin_meta` (populated by the scan loop from `plugin.json`)
    to decide whether the plugin literally claims clipboard handling
    is its purpose. If yes, every clipboard match in that plugin is
    DEFINITE_FP — the rule is detecting the plugin's declared core
    functionality.

    For plugins NOT in the clipboard domain, clipboard reads stay at
    REAL: a productivity tool reading the clipboard is exactly what
    the rule is meant to flag.
    """

    if _plugin_is_clipboard_domain(ctx.plugin_meta):
        return FindingVerdict.DEFINITE_FP
    if ctx.file_role in ("fixture", "test"):
        return FindingVerdict.DEFINITE_FP
    if ctx.file_role == "doc":
        return FindingVerdict.LIKELY_FP
    return FindingVerdict.REAL


def _plugin_is_clipboard_domain(plugin_meta: dict) -> bool:
    """Substring-match clipboard hints against name / description / keywords."""

    haystack_parts: list[str] = []
    for key in ("name", "description", "keywords", "category"):
        value = plugin_meta.get(key)
        if isinstance(value, str):
            haystack_parts.append(value)
        elif isinstance(value, list):
            haystack_parts.extend(str(x) for x in value if isinstance(x, str))
    haystack = " ".join(haystack_parts).lower()
    return any(hint in haystack for hint in _CLIPBOARD_DOMAIN_HINTS)


# -----------------------------------------------------------------------------
# RC-65 — cloud IMDS endpoint
# -----------------------------------------------------------------------------

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
)

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


@register_classifier("RC-65")
def classify_rc65(ctx: Context) -> FindingVerdict:
    """Cloud IMDS endpoint — denylist set member is FP, network call is TP.

    The discriminator is the network call: if any sink hint is on the
    same line, the IMDS literal is being USED (TP regardless of
    surrounding identifiers). Otherwise, surrounding identifiers like
    `unsafe_hosts`, `_PATTERNS`, etc. mark the line as a detector's
    pattern source and the verdict drops to DEFINITE_FP.

    Step 4 (`--extreme`): a same-line network call against the IMDS
    literal in a SOURCE-role file is unambiguous instance-metadata
    exfiltration — the canonical SSRF target. Returns `DEFINITE_TP` in
    that exact context so `--extreme` can escalate (MAJOR → CRITICAL).
    Test/fixture/doc roles still get `REAL` (no escalation) because
    detector test suites and prose docs legitimately reference IMDS
    addresses inside `requests.`-shaped exemplars.
    """

    role_allows_escalation = ctx.file_role == "source"

    if any(hint in ctx.line for hint in _RC65_NETWORK_CALL_HINTS):
        return FindingVerdict.DEFINITE_TP if role_allows_escalation else FindingVerdict.REAL
    if any(hint in ctx.line for hint in _RC65_PATTERN_SOURCE_HINTS):
        return FindingVerdict.DEFINITE_FP
    if any(hint in line for line in ctx.surrounding_lines for hint in _RC65_PATTERN_SOURCE_HINTS):
        return FindingVerdict.DEFINITE_FP
    if ctx.file_role in ("fixture", "test"):
        return FindingVerdict.DEFINITE_FP
    if ctx.file_role == "doc":
        return FindingVerdict.LIKELY_FP
    return FindingVerdict.REAL


# -----------------------------------------------------------------------------
# RC-87 — RFC-1918 / loopback IP outside known IMDS endpoints
# -----------------------------------------------------------------------------

_RC87_DEPVERSION_RE = re.compile(
    r"\"[^\"]*(?:version|engines?|peerDeps?|deps?|@types|@[a-z][a-z0-9-]+/)"
    r"[^\"]*\"\s*:\s*\"[\^~>=<]?\s*\d",
    re.IGNORECASE,
)
_RC87_PURE_VERSION_LINE_RE = re.compile(
    r"^\s*\"version\"\s*:\s*\"[\^~>=<]?\s*\d",
    re.IGNORECASE,
)
# Semver-shape value with `^` or `~` prefix is unambiguously a dep
# version pin. Real IP literals never start with `^`/`~`, so this
# catches arbitrary `"<dep>": "^X.Y.Z"` lines without needing a
# specific key allowlist.
_RC87_SEMVER_VALUE_RE = re.compile(
    r"\"[^\"]+\"\s*:\s*\"[\^~]\s*\d+\.\d+\.\d+",
)
_RC87_MANIFEST_BASENAMES = frozenset(
    {
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
    }
)


@register_classifier("RC-87")
def classify_rc87(ctx: Context) -> FindingVerdict:
    """RFC-1918 / loopback IP — package-manifest semver is FP.

    Manifests like `package.json` legitimately contain dep version
    strings that match the broad RFC-1918 regex (`10.[0-9.]+`).
    Suppress on those filenames OR on a `"<key>": "<X.Y.Z>"` JSON
    shape. Real internal-IP leaks in source files keep the REAL
    verdict.
    """

    rel = ctx.file_path.lower().replace("\\", "/")
    basename = rel.rsplit("/", 1)[-1]

    if basename in _RC87_MANIFEST_BASENAMES:
        return FindingVerdict.DEFINITE_FP
    if _RC87_PURE_VERSION_LINE_RE.match(ctx.line):
        return FindingVerdict.DEFINITE_FP
    if _RC87_DEPVERSION_RE.search(ctx.line):
        return FindingVerdict.DEFINITE_FP
    if _RC87_SEMVER_VALUE_RE.search(ctx.line):
        return FindingVerdict.DEFINITE_FP
    if ctx.file_role in ("fixture", "test"):
        return FindingVerdict.DEFINITE_FP
    if ctx.file_role == "doc":
        return FindingVerdict.LIKELY_FP
    return FindingVerdict.REAL


# -----------------------------------------------------------------------------
# RC-93 — line with ≥30 contiguous spaces (visual deception)
# -----------------------------------------------------------------------------

_RC93_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|\s*[-:]+\s*(?:\|\s*[-:]+\s*)+\|?\s*$")


# -----------------------------------------------------------------------------
# RC-76 — stemmed prompt-injection signal
# -----------------------------------------------------------------------------

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


@register_classifier("RC-76")
def classify_rc76(ctx: Context) -> FindingVerdict:
    """Stemmed prompt-injection signal — source code is FP, agent docs are TP.

    The rule fires on >=3 trigger-stem co-occurrence within 80 chars.
    The dominant FP is LLM-tooling source code where words like
    `prompt`/`system`/`instruct`/`token`/`output` are vocabulary
    (variable names, function parameters, type fields). The TP signal
    is the same stems appearing in agent-doc / skill-body content
    that the model executes as instructions.

    Strategy:
    * Source-extension files (`.ts`/`.js`/`.py`/`.go`/etc) →
      DEFINITE_FP — the stems are vocabulary, not instruction.
    * Files in `bin/` (extension-less shell scripts) → DEFINITE_FP.
    * Test / fixture roles → DEFINITE_FP.
    * Doc role → REAL — the doc IS instruction-shaped.
    * Other → REAL.
    """
    file_path_lower = ctx.file_path.lower().replace("\\", "/")
    if file_path_lower.endswith(_RC76_SOURCE_EXTENSIONS):
        return FindingVerdict.DEFINITE_FP
    if "/bin/" in file_path_lower or file_path_lower.startswith("bin/"):
        return FindingVerdict.DEFINITE_FP
    basename = file_path_lower.rsplit("/", 1)[-1]
    if basename in {
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
    }:
        return FindingVerdict.DEFINITE_FP
    if ctx.file_role in ("fixture", "test"):
        return FindingVerdict.DEFINITE_FP
    return FindingVerdict.REAL


@register_classifier("RC-93")
def classify_rc93(ctx: Context) -> FindingVerdict:
    """≥30 contiguous spaces — markdown table column padding is FP.

    The rule's signal is real visual deception: invisible text
    pushed off-screen by whitespace. Markdown column alignment
    (`| col1 | col2     |`) and table-separator rows
    (`|---|---|`) produce long whitespace runs that aren't
    deception. Same for ASCII art / box-drawing rows.
    """

    # Markdown table rows / separators are the only structural FP. The
    # rule's whole point is detecting hidden text in DOCS, so do NOT
    # demote based on file_role — a doc-role match is exactly the
    # threat model.
    stripped = ctx.line.strip()
    if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
        return FindingVerdict.DEFINITE_FP
    if _RC93_TABLE_SEPARATOR_RE.match(ctx.line):
        return FindingVerdict.DEFINITE_FP
    return FindingVerdict.REAL


# -----------------------------------------------------------------------------
# Plugin-meta loader — used by the scan loop to populate Context.plugin_meta.
# -----------------------------------------------------------------------------


def load_plugin_meta(plugin_root: Path) -> dict:
    """Read plugin.json (if present) and return its top-level dict.

    Tries both `.claude-plugin/plugin.json` and `plugin.json` at root,
    in that order — matches Claude Code's own resolution. Returns an
    empty dict on missing file or parse error so callers don't have
    to special-case None.
    """

    for candidate in (
        plugin_root / ".claude-plugin" / "plugin.json",
        plugin_root / "plugin.json",
    ):
        if not candidate.is_file():
            continue
        try:
            with open(candidate, "r", encoding="utf-8", errors="ignore") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            continue
    return {}


__all__ = [
    "classify_rc21",
    "classify_rc22",
    "classify_rc65",
    "classify_rc87",
    "classify_rc93",
    "load_plugin_meta",
]
