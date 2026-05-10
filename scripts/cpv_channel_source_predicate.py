#!/usr/bin/env python3
"""TRDD-26446eed — Channel MCP server source-code prefilter.

Deterministic helper used by the ``semantic-validator`` agent for the
"Channel MCP Server Source-Code Security" pillar
(``skills/semantic-validation-skill/references/channel-source-security.md``).

The module does NOT replace the LLM; it bounds the LLM's reading by:

1. Detecting whether the pillar is in scope (plugin.json declares a
   non-empty ``channels`` array AND ships local MCP server source).
2. Resolving each channel server's entry-point source file from the
   ``mcpServers.<server>.command`` / ``args`` declaration.
3. Identifying candidate lines that forward inbound payloads to Claude
   so the LLM can audit them.
4. Spotting the obviously-safe shape (sender-ID allowlist + membership
   check) and the obviously-unsafe shape (chat-ID-only gating, no
   gating at all, ``claude/channel/permission`` capability without an
   accompanying gated permission handler).

If the prefilter classification is unanimous (no candidates, all-safe,
or all-unsafe), the agent can short-circuit and skip the Opus call —
saving the entire pillar's token budget.

The implementation is read-only, side-effect free, and never raises on
malformed input — every helper returns either an empty container or a
``PrefilterVerdict(in_scope=False, findings=[])``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


Severity = Literal["CRITICAL", "MAJOR", "PASSED", "INFO"]


@dataclass(frozen=True)
class ChannelSourceFinding:
    """A single prefilter finding ready for LLM enrichment.

    The agent renders these as report rows; the LLM uses them as
    bounded prompts ("examine ``server.ts:12`` for sender gating").
    """

    severity: Severity
    rule: str
    file: str
    line: int
    message: str


@dataclass(frozen=True)
class PrefilterVerdict:
    """End-to-end classification of a plugin's channel-source surface."""

    in_scope: bool
    findings: tuple[ChannelSourceFinding, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Pillar scope — plugin.json gating
# ---------------------------------------------------------------------------


def _load_plugin_manifest(plugin_root: Path) -> dict | None:
    """Read ``.claude-plugin/plugin.json`` defensively.

    Never raises — returns None on missing file, malformed JSON, or
    permission errors. The pillar is informational; a broken plugin
    gets caught by the syntactic validator and we must not double-fail.
    """
    manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
    if not manifest_path.is_file():
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def plugin_declares_channels(plugin_root: Path) -> bool:
    """Return True when the plugin declares a non-empty ``channels`` array."""
    manifest = _load_plugin_manifest(plugin_root)
    if manifest is None:
        return False
    channels = manifest.get("channels")
    return isinstance(channels, list) and len(channels) > 0


# ---------------------------------------------------------------------------
# Source resolution — mcpServers.<server> -> entry-point file
# ---------------------------------------------------------------------------


# ``${CLAUDE_PLUGIN_ROOT}`` (and the legacy ``${CLAUDE_PLUGIN_PATH}``) are
# the documented prefixes for plugin-relative paths in plugin.json. We
# also strip a literal ``./`` for relative paths.
_PLUGIN_ROOT_TOKENS: Final[tuple[str, ...]] = (
    "${CLAUDE_PLUGIN_ROOT}/",
    "${CLAUDE_PLUGIN_ROOT}",
    "${CLAUDE_PLUGIN_PATH}/",
    "${CLAUDE_PLUGIN_PATH}",
    "./",
)


def _strip_plugin_root_prefix(arg: str) -> str:
    """Remove the documented plugin-root tokens from an args entry."""
    for tok in _PLUGIN_ROOT_TOKENS:
        if arg.startswith(tok):
            return arg[len(tok) :]
    return arg


# Common patterns where the plugin-shipped build output is in dist/
# but the human-readable source is in src/. Bundled / minified output
# is unreliable for manual gating analysis, so the resolver prefers
# the matching src/ file when one exists.
_DIST_TO_SRC_MAP: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("/dist/", "/src/", (".ts", ".tsx", ".js", ".mjs")),
    ("/build/", "/src/", (".ts", ".tsx", ".js", ".mjs")),
    ("/lib/", "/src/", (".ts", ".tsx", ".js", ".mjs")),
)


def _coerce_dist_path_to_src(candidate: Path) -> Path:
    """If `candidate` lives under dist/ but a sibling src/ exists, prefer src/.

    The resolver only swaps when the dist/ file does NOT exist (e.g. the
    plugin author ships TypeScript source and expects the user to build).
    If both exist, dist/ wins because the user clearly committed both
    on purpose.
    """
    if candidate.exists():
        return candidate
    posix = candidate.as_posix()
    for dist_seg, src_seg, exts in _DIST_TO_SRC_MAP:
        if dist_seg in posix:
            base = posix.replace(dist_seg, src_seg, 1)
            stem = Path(base).with_suffix("")
            for ext in exts:
                alt = Path(str(stem) + ext)
                if alt.exists():
                    return alt
    return candidate


def resolve_channel_server_sources(plugin_root: Path) -> list[Path]:
    """Resolve each channel server's entry-point source file.

    Walks ``plugin.json.channels`` -> ``mcpServers[<server>]`` ->
    ``args[0]`` and returns absolute paths to source files that exist
    under the plugin tree. Paths that cannot be resolved (missing
    args, missing file, dist-only with no src/ counterpart) are
    silently dropped — the LLM cannot audit a missing file, and the
    agent surfaces the gap in its report from the empty result.
    """
    manifest = _load_plugin_manifest(plugin_root)
    if manifest is None:
        return []
    channels = manifest.get("channels")
    if not isinstance(channels, list) or not channels:
        return []
    mcp_servers = manifest.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return []
    out: list[Path] = []
    seen: set[Path] = set()
    for entry in channels:
        if not isinstance(entry, dict):
            continue
        server_name = entry.get("server")
        if not isinstance(server_name, str):
            continue
        server_decl = mcp_servers.get(server_name)
        if not isinstance(server_decl, dict):
            continue
        args = server_decl.get("args")
        if not isinstance(args, list) or not args:
            continue
        entry_arg = args[0]
        if not isinstance(entry_arg, str) or not entry_arg:
            continue
        rel = _strip_plugin_root_prefix(entry_arg)
        candidate = (plugin_root / rel).resolve()
        # Reject paths that escape the plugin root — defence-in-depth
        # against path-traversal attacks (e.g. dot-dot-slash sequences
        # pointing at sensitive files) in malicious plugin.json args.
        try:
            candidate.relative_to(plugin_root.resolve())
        except ValueError:
            continue
        # Prefer src/ over dist/ when only src/ exists.
        candidate = _coerce_dist_path_to_src(candidate)
        if candidate.is_file() and candidate not in seen:
            out.append(candidate)
            seen.add(candidate)
    return out


# ---------------------------------------------------------------------------
# Forward-call detection
# ---------------------------------------------------------------------------


# Match calls like:
#   mcp.notification("notifications/claude/channel", ...)
#   mcp.send_notification("notifications/claude/channel", ...)
#   server.notification('notifications/claude/channel', ...)
#   sendNotification('notifications/claude/channel', ...)
# in either string form. Word-boundary on the method name keeps us out
# of identifiers like ``my_send_notification_helper``.
_NOTIFICATION_METHOD_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:notification|send_notification|sendNotification)\s*\(",
)
_CHANNEL_NOTIFICATION_STRING_RE: Final[re.Pattern[str]] = re.compile(
    r"""['"]notifications/claude/channel(?:/permission)?['"]""",
)


def find_channel_forward_calls(source: str, *, language: str) -> list[int]:
    """Return 1-indexed line numbers of channel forward calls.

    Detection is line-locality based: a notification method invocation
    on the same line as the channel string OR within the next 4 lines
    (object-literal or named-arg style) counts. This catches both:

        mcp.notification("notifications/claude/channel", { ... })

    and:

        mcp.notification({
          method: "notifications/claude/channel",
          params: { ... }
        });
    """
    del language  # the regex patterns are language-agnostic
    if not source:
        return []
    lines = source.splitlines()
    method_lines = {i for i, line in enumerate(lines) if _NOTIFICATION_METHOD_RE.search(line)}
    string_lines = {i for i, line in enumerate(lines) if _CHANNEL_NOTIFICATION_STRING_RE.search(line)}
    matches: list[int] = []
    for m in sorted(method_lines):
        # accept if the channel string is on the same line or within the
        # 4-line argument window that follows the call.
        for s in string_lines:
            if m <= s <= m + 4:
                matches.append(m + 1)  # 1-indexed for human reading
                break
    return matches


# ---------------------------------------------------------------------------
# Sender-gating detection — the safe shapes from rule 1
# ---------------------------------------------------------------------------


# Sender-ID property paths the spec recognises (TypeScript / JavaScript
# / Python). The presence of the property name is necessary but not
# sufficient — we ALSO require evidence of an allowlist comparison
# (Set.has, "in", inclusion check).
_SENDER_PROPERTY_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    \b(?:
      msg\.from\.id            # Telegram TS/JS short-hand
      | message\.from\.id      # Telegram TS/JS verbose
      | message\.from_user\.id # aiogram (Python)
      | message\.author\.id    # Discord
      | message\.sender(?:_id)?\b
      | update\.message\.from\.id
      | ctx\.message\.from\.id
    )
    """,
    re.VERBOSE,
)
# Allowlist comparison shapes — Set.has / Array.includes / "in" / != / not in
_ALLOWLIST_COMPARE_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    (?:
      \.has\s*\(                     # Set.has(...)
      | \.includes\s*\(              # Array.includes(...)
      | \bin\s+ALLOW                 # Python: id in ALLOW...
      | \bnot\s+in\s+ALLOW           # Python: id not in ALLOW...
      | \bin\s+[A-Z][A-Z0-9_]+       # Python: id in ALL_CAPS
      | \bnot\s+in\s+[A-Z][A-Z0-9_]+ # Python: id not in ALL_CAPS
      | ==\s*ALLOW                   # naïve scalar compare
      | ===\s*ALLOW
    )
    """,
    re.VERBOSE,
)


def find_sender_gating_patterns(source: str, *, language: str) -> list[int]:
    """Return 1-indexed line numbers where sender-ID gating is present.

    A line counts as "gated" when it contains BOTH a recognised
    sender-ID property reference AND an allowlist-compare operator.
    Two-line gating ("``const id = msg.from.id;``" then
    "``if (!ALLOWED.has(id)) return;``") is also recognised when the
    compare line follows the property line within 3 lines.
    """
    del language  # patterns are language-agnostic
    if not source:
        return []
    lines = source.splitlines()
    prop_lines = [i for i, line in enumerate(lines) if _SENDER_PROPERTY_RE.search(line)]
    out: list[int] = []
    for p in prop_lines:
        # same line OR within 3 lines after the property reference
        window = lines[p : p + 4]
        for offset, line in enumerate(window):
            if _ALLOWLIST_COMPARE_RE.search(line):
                out.append(p + offset + 1)
                break
    return sorted(set(out))


# ---------------------------------------------------------------------------
# Chat-ID-only gating detection — the MAJOR shape from rule 3
# ---------------------------------------------------------------------------


_CHAT_ID_COMPARE_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    \b(?:
      msg\.chat\.id
      | message\.chat\.id
      | update\.message\.chat\.id
      | ctx\.message\.chat\.id
    )
    \s*
    (?:!==|!=|==|===)         # equality / inequality compare
    """,
    re.VERBOSE,
)


def find_chat_id_only_gating(source: str, *, language: str) -> list[int]:
    """Return 1-indexed lines where chat-ID is the ONLY gating mechanism.

    Fires when the source contains a chat-ID comparison AND has no
    sender-ID gating elsewhere. Compound gating (chat-ID + sender-ID)
    is silently safe.
    """
    if not source:
        return []
    sender_gating = find_sender_gating_patterns(source, language=language)
    if sender_gating:
        return []
    lines = source.splitlines()
    return [i + 1 for i, line in enumerate(lines) if _CHAT_ID_COMPARE_RE.search(line)]


# ---------------------------------------------------------------------------
# Permission-capability declaration detection — rule 2 prefilter
# ---------------------------------------------------------------------------


_PERMISSION_CAP_RE: Final[re.Pattern[str]] = re.compile(
    r"""
    ['"]claude/channel/permission['"]
    """,
    re.VERBOSE,
)


def find_permission_capability_declaration(source: str) -> bool:
    """Return True when source declares the permission-relay capability.

    The detection is intentionally broad: any literal occurrence of
    ``"claude/channel/permission"`` or ``'claude/channel/permission'``
    counts. The LLM verifies the surrounding shape (capability vs
    handler vs comment).
    """
    if not source:
        return False
    return _PERMISSION_CAP_RE.search(source) is not None


# ---------------------------------------------------------------------------
# Per-source classification + per-plugin verdict
# ---------------------------------------------------------------------------


def _detect_language(path: Path) -> str:
    """Map a file extension to the prefilter language hint."""
    suffix = path.suffix.lower()
    if suffix in {".ts", ".tsx", ".mts", ".cts"}:
        return "typescript"
    if suffix in {".js", ".mjs", ".cjs", ".jsx"}:
        return "javascript"
    if suffix in {".py", ".pyi"}:
        return "python"
    return "other"


def _classify_one_source(
    source_path: Path,
    plugin_root: Path,
) -> list[ChannelSourceFinding]:
    """Produce findings for a single MCP server source file."""
    try:
        text = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rel = str(source_path.relative_to(plugin_root))
    language = _detect_language(source_path)

    forwards = find_channel_forward_calls(text, language=language)
    if not forwards:
        # The file we resolved is a channel server entry-point but it
        # contains no recognised channel-forward call. The LLM should
        # still review (the file may use indirection), but the
        # prefilter has nothing concrete to report.
        return [
            ChannelSourceFinding(
                severity="INFO",
                rule="RULE-0-no-forward-call-detected",
                file=rel,
                line=1,
                message=(
                    "No 'notifications/claude/channel' forward call detected "
                    "by the prefilter. The Opus pillar must read this file "
                    "to verify whether the indirection is safe."
                ),
            ),
        ]

    findings: list[ChannelSourceFinding] = []
    sender_gating = find_sender_gating_patterns(text, language=language)
    chat_only = find_chat_id_only_gating(text, language=language)
    permission_cap = find_permission_capability_declaration(text)

    if not sender_gating and not chat_only:
        findings.append(
            ChannelSourceFinding(
                severity="CRITICAL",
                rule="RULE-1-no-sender-gating",
                file=rel,
                line=forwards[0],
                message=(
                    "Channel MCP server forwards inbound messages to Claude "
                    "without a sender-ID allowlist check. Add an early-return "
                    "comparing message.from.id (or transport equivalent) "
                    "against an allowlist before the forward call."
                ),
            ),
        )
    elif chat_only and not sender_gating:
        findings.append(
            ChannelSourceFinding(
                severity="MAJOR",
                rule="RULE-3-chat-id-only-gating",
                file=rel,
                line=chat_only[0],
                message=(
                    "Channel MCP server gates forwarding on chat/room ID only. "
                    "Anyone in the authorized room can inject prompts. Add a "
                    "sender-ID allowlist as the primary gate."
                ),
            ),
        )
    else:
        # Sender gating is present (possibly compound with chat-ID).
        findings.append(
            ChannelSourceFinding(
                severity="PASSED",
                rule="RULE-1-sender-gating-present",
                file=rel,
                line=sender_gating[0],
                message=(
                    "Sender-ID allowlist check is present before forward "
                    "calls. The Opus pillar should confirm the allowlist "
                    "is sourced from a constant or env var."
                ),
            ),
        )

    if permission_cap and not sender_gating:
        findings.append(
            ChannelSourceFinding(
                severity="CRITICAL",
                rule="RULE-2-permission-capability-ungated",
                file=rel,
                line=forwards[0],
                message=(
                    "Channel MCP server declares "
                    "capabilities.experimental['claude/channel/permission'] "
                    "without a sender-ID allowlist. Any inbound sender can "
                    "approve destructive tool calls. Either remove the "
                    "capability or gate the permission handler on "
                    "message.from.id."
                ),
            ),
        )
    return findings


def classify_channel_source(plugin_root: Path) -> PrefilterVerdict:
    """End-to-end classification — the entry point the agent uses.

    Returns a ``PrefilterVerdict`` with:

    - ``in_scope=False`` and empty findings if the plugin declares no
      channels OR has no resolvable MCP server source — the agent
      skips the pillar entirely (zero opus tokens).
    - ``in_scope=True`` and one or more findings otherwise. Each
      finding is a bounded prompt for the Opus pillar.
    """
    if not plugin_declares_channels(plugin_root):
        return PrefilterVerdict(in_scope=False, findings=())
    sources = resolve_channel_server_sources(plugin_root)
    if not sources:
        # Channels are declared but no source resolves — out-of-scope
        # for the source-gating pillar (the LLM has nothing to read).
        # The structural validator surfaces this separately.
        return PrefilterVerdict(in_scope=False, findings=())
    findings: list[ChannelSourceFinding] = []
    for src in sources:
        findings.extend(_classify_one_source(src, plugin_root))
    return PrefilterVerdict(in_scope=True, findings=tuple(findings))


__all__ = [
    "ChannelSourceFinding",
    "PrefilterVerdict",
    "Severity",
    "classify_channel_source",
    "find_channel_forward_calls",
    "find_chat_id_only_gating",
    "find_permission_capability_declaration",
    "find_sender_gating_patterns",
    "plugin_declares_channels",
    "resolve_channel_server_sources",
]
