#!/usr/bin/env python3
"""CPV Token Cost Reporter — accurate per-API-call token measurement.

Parses a Claude Code agent transcript (JSONL) to sum the full usage breakdown
(input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens)
from every assistant message, then computes exact cost using per-model pricing.

Dual-mode:
  1. SubagentStop hook: reads hook JSON from stdin, parses agent_transcript_path,
     outputs {"systemMessage": cost_summary} for display in orchestrator context.
  2. CLI: uv run python scripts/cpv_token_cost.py --transcript /path/to/agent.jsonl
  3. Library: from scripts.cpv_token_cost import parse_transcript, estimate_cost
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ── Per-model pricing (USD per million tokens, as of 2026-09) ──
# 5.x / 4.7 / 4.8 rows: input/output from the bundled claude-api skill's
# "Current Models" table (CC 2.1.281); cache_write = 1.25x input and
# cache_read = ~0.1x input per its prompt-caching.md ("Cache writes cost 1.25x
# for 5-minute TTL"), EXCEPT the two cache reads the skill states outright:
# Opus 5.5 $0.20 and Fable 5.1 $0.25. Mythos 5.1 is left out on purpose — the
# skill says its cache-read rate is "open at launch".
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.20},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_write": 12.5, "cache_read": 0.25},
    "claude-fable-5": {"input": 10.0, "output": 50.0, "cache_write": 12.5, "cache_read": 1.00},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.20},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-5": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-opus-4": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-opus-4-1": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-haiku-3-5": {"input": 0.80, "output": 4.0, "cache_write": 1.00, "cache_read": 0.08},
}
DEFAULT_PRICING: dict[str, float] = {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30}


def _resolve_pricing(model_name: str) -> tuple[dict[str, float], bool]:
    """Resolve pricing for a model id, returning ``(pricing, is_estimate)``.

    ``is_estimate=True`` means the id did not exactly (or via a dated-suffix
    substring) identify a listed ``MODEL_PRICING`` row, so the caller is
    getting a family/default fallback rather than a specific quoted price.
    """
    if not model_name:
        return DEFAULT_PRICING, True
    if model_name in MODEL_PRICING:
        return MODEL_PRICING[model_name], False
    # Try prefix/substring match, longest key first. A short key like
    # ``claude-opus-4`` is a substring of every ``claude-opus-4-X`` id, so
    # iterating in dict-insertion order would let the base key shadow the more
    # specific dated point-release (e.g. ``claude-opus-4-1-20250805`` would
    # resolve to base ``claude-opus-4`` pricing). Matching longest-key-first
    # makes the most specific key win regardless of dict order, so a future
    # point-release with different pricing is not silently mispriced.
    # (audit MINOR token #3)
    for key in sorted(MODEL_PRICING, key=len, reverse=True):
        if key in model_name or model_name.startswith(key):
            return MODEL_PRICING[key], False
    # Fuzzy family match. The 5.x branches come FIRST: before they existed an
    # unlisted 5.x id (e.g. "opus-5.5") fell through to the generic "opus"
    # branch and was billed at retired Opus 4.1 rates ($15/$75, ~4x too high),
    # and any Fable id fell to DEFAULT (Sonnet 4 rates, 5x too low).
    ml = model_name.lower()
    if "fable" in ml or "mythos" in ml:
        # ponytail: Mythos priced as Fable 5.1 (same $10/$50; its cache-read
        # rate is unannounced) — split it out once the skill publishes one.
        return MODEL_PRICING["claude-fable-5-1"], True
    if "opus" in ml and ("5-5" in ml or "5.5" in ml):
        return MODEL_PRICING["claude-opus-5-5"], True
    if "opus" in ml and re.search(r"(?:opus-5|5-opus|opus 5)", ml):
        return MODEL_PRICING["claude-opus-5"], True
    if "sonnet" in ml and re.search(r"(?:sonnet-5|5-sonnet|sonnet 5)", ml):
        return MODEL_PRICING["claude-sonnet-5"], True
    if "opus" in ml and ("4-6" in ml or "4.6" in ml):
        return MODEL_PRICING["claude-opus-4-6"], True
    if "opus" in ml and ("4-5" in ml or "4.5" in ml):
        return MODEL_PRICING["claude-opus-4-5"], True
    # An id in a KNOWN family that matched none of the specific rules above
    # resolves to that family's NEWEST listed row, never a retired legacy
    # bucket. Billing an unlisted `claude-opus-9` at today's Opus rate is a
    # closer estimate than billing it at Opus 4.1's retired $15/$75 — this is
    # why the old "generic opus -> opus-4-1" fallback was replaced.
    if "opus" in ml:
        return MODEL_PRICING["claude-opus-5-5"], True
    if "sonnet" in ml:
        return MODEL_PRICING["claude-sonnet-5"], True
    if "haiku" in ml:
        return MODEL_PRICING["claude-haiku-4-5"], True
    return DEFAULT_PRICING, True


def get_pricing(model_name: str) -> dict[str, float]:
    """Look up pricing for a model name, with fuzzy matching."""
    pricing, _ = _resolve_pricing(model_name)
    return pricing


def pricing_is_estimate(model_name: str) -> bool:
    """True when ``get_pricing(model_name)`` is a family/default fallback.

    False means the id exactly (or via a dated-suffix substring) matched a
    specific ``MODEL_PRICING`` row and the price is quoted, not guessed.
    """
    _, is_estimate = _resolve_pricing(model_name)
    return is_estimate


class TokenUsage:
    """Token usage summary from a parsed transcript."""

    __slots__ = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "message_count",
        "model",
    )

    def __init__(self) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cache_creation_input_tokens: int = 0
        self.cache_read_input_tokens: int = 0
        self.message_count: int = 0
        self.model: str = "unknown"

    def to_dict(self) -> dict[str, int | str]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "message_count": self.message_count,
            "model": self.model,
        }

    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


def _usage_int(usage: dict[str, object], key: str) -> int:
    """Return ``usage[key]`` as an int, or 0 for missing/null/non-int values.

    The transcript is JSON from Claude Code, but a truncated or hand-crafted
    transcript can carry ``null`` (or a string) where an int is expected.
    ``usage.get(key, 0)`` only defaults a MISSING key — a present ``null`` would
    propagate ``None`` into ``int += None`` and raise TypeError past the
    OSError-only handler below. Coercing here keeps the parser tolerant of a
    malformed value exactly as it already tolerates a missing one (bool is a
    subclass of int and is intentionally rejected — a usage count is never a
    boolean).
    """
    val = usage.get(key, 0)
    return val if isinstance(val, int) and not isinstance(val, bool) else 0


def parse_transcript(path: str | Path) -> TokenUsage:
    """Parse a JSONL transcript and sum token usage from all assistant messages."""
    result = TokenUsage()
    model_counts: dict[str, int] = {}
    seen_ids: set[str] = set()

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != "assistant":
                    continue
                msg = entry.get("message", {})
                if not isinstance(msg, dict):
                    continue

                # Deduplicate by message id
                mid = msg.get("id", "")
                if mid:
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)

                usage = msg.get("usage", {})
                # Mirror the msg/dict guard above: a non-dict (or empty) usage
                # has no token counts to add. A non-dict that is truthy (e.g. a
                # stray string) would otherwise AttributeError on .get below.
                if not isinstance(usage, dict) or not usage:
                    continue

                result.input_tokens += _usage_int(usage, "input_tokens")
                result.output_tokens += _usage_int(usage, "output_tokens")
                result.cache_creation_input_tokens += _usage_int(usage, "cache_creation_input_tokens")
                result.cache_read_input_tokens += _usage_int(usage, "cache_read_input_tokens")
                result.message_count += 1

                model = msg.get("model", "unknown")
                model_counts[model] = model_counts.get(model, 0) + 1
    except OSError as e:
        print(f"Warning: Could not read transcript: {e}", file=sys.stderr)

    # Most-used model
    if model_counts:
        result.model = max(model_counts, key=lambda m: model_counts[m])
    return result


def estimate_cost(usage: TokenUsage, model: str = "") -> float:
    """Compute exact USD cost from the 4-category token breakdown."""
    p = get_pricing(model or usage.model)
    return (
        (usage.input_tokens / 1e6) * p["input"]
        + (usage.output_tokens / 1e6) * p["output"]
        + (usage.cache_creation_input_tokens / 1e6) * p["cache_write"]
        + (usage.cache_read_input_tokens / 1e6) * p["cache_read"]
    )


def fmt_tok(n: int) -> str:
    """Format token count with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(n)


def format_cost_line(usage: TokenUsage, model: str = "") -> str:
    """One-line cost summary for terminal display."""
    cost = estimate_cost(usage, model)
    m = model or usage.model
    # Shorten model name for display
    short_model = m.replace("claude-", "").split("-2")[0]
    # Append an estimate marker when the price came from a family/default
    # fallback rather than a specific quoted MODEL_PRICING row, so a report
    # reader knows the dollar figure is a guess, not a billed rate.
    estimate_note = " (estimated: unlisted model)" if pricing_is_estimate(m) else ""
    return (
        f"Tokens: {fmt_tok(usage.total_tokens())} "
        f"(in:{fmt_tok(usage.input_tokens)} out:{fmt_tok(usage.output_tokens)} "
        f"cw:{fmt_tok(usage.cache_creation_input_tokens)} cr:{fmt_tok(usage.cache_read_input_tokens)}) "
        f"| Cost: ${cost:.4f} | Model: {short_model}{estimate_note}"
    )


def main() -> int:
    """Entry point — hook mode (stdin JSON) or CLI mode (--transcript)."""
    # CLI mode: --transcript PATH
    if "--transcript" in sys.argv:
        idx = sys.argv.index("--transcript")
        if idx + 1 >= len(sys.argv):
            print("Error: --transcript requires a path argument", file=sys.stderr)
            return 1
        transcript_path = sys.argv[idx + 1]
        if not Path(transcript_path).exists():
            print(f"Error: transcript not found: {transcript_path}", file=sys.stderr)
            return 1
        usage = parse_transcript(transcript_path)
        if usage.message_count == 0:
            print("No assistant messages found in transcript.", file=sys.stderr)
            return 1
        print(format_cost_line(usage))
        return 0

    # Hook mode: read JSON from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 1

    # Get the agent's own transcript path (SubagentStop provides this)
    agent_transcript = hook_input.get("agent_transcript_path", "")
    session_transcript = hook_input.get("transcript_path", "")

    # Prefer agent transcript; fall back to session transcript
    transcript = ""
    if agent_transcript and Path(agent_transcript).exists():
        transcript = agent_transcript
    elif session_transcript and Path(session_transcript).exists():
        transcript = session_transcript

    if not transcript:
        return 0

    usage = parse_transcript(transcript)
    if usage.message_count == 0:
        return 0

    cost_line = format_cost_line(usage)
    # Output as systemMessage so it appears in the orchestrator's context
    print(json.dumps({"systemMessage": f"  {cost_line}"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
