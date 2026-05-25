#!/usr/bin/env python3
"""CPV Token Estimate — a CONSERVATIVE Claude token-count estimator for size gates.

Pure Python standard library only. No external dependencies (no ``tiktoken``,
no ``regex`` module). The public entry point is :func:`estimate_tokens`, which
always rounds UP and is designed never to under-count — a size gate that
under-counts would let an oversized artifact slip through, so every tier here
biases toward over-estimation.

Tiers (best available is auto-selected)
---------------------------------------
1. **API** (opt-in only): when ``allow_api=True`` AND ``CPV_TOKEN_EXACT=1`` is
   set in the environment AND ``ANTHROPIC_API_KEY`` is present, call the
   Anthropic ``count_tokens`` endpoint for the exact Claude count. Best-effort;
   ANY failure (missing SDK, network error, HTTP error) falls through to tier 2.
2. **BPE** (default): a faithful pure-Python port of the o200k_base byte-level
   BPE tokenizer (the same vocab + pre-tokenizer regex that ``tiktoken`` and
   ``gpt-tokenizer`` use). The vocab ships GZIP-compressed under
   ``scripts/data/o200k_base.tiktoken.gz``. Because Claude's tokenizer is not
   public and tokenizes roughly 20-25% heavier than o200k on typical text, the
   raw o200k token count is multiplied by a Claude-correction factor of 1.3 and
   rounded up.
3. **Heuristic** (fallback): if the vendored vocab cannot be loaded, a
   per-script chars-per-token heuristic driven by ``unicodedata`` produces a
   conservative estimate.

Attribution
-----------
The o200k_base vocabulary and its pre-tokenizer regex originate from OpenAI's
``tiktoken`` (MIT License, https://github.com/openai/tiktoken). The BPE port
strategy follows ``gpt-tokenizer`` by niieani (MIT License,
https://github.com/niieani/gpt-tokenizer). The pre-tokenizer, which ``tiktoken``
expresses with Unicode property escapes (``\\p{L}`` etc.) that Python's stdlib
``re`` does not support, is re-implemented here as a hand-written scanner driven
by :func:`unicodedata.category`, validated to byte-exact token-id parity against
``tiktoken`` on tens of thousands of multilingual strings.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import math
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# The Claude correction factor. Claude's tokenizer is not public; empirically it
# produces ~20-25% more tokens than o200k_base on mixed natural-language + code.
# We use 1.3 (the high end + a small safety margin) so the estimate stays
# conservative (never under-counts) for size gates.
CLAUDE_CORRECTION: float = 1.3

# Heuristic margin applied on top of the per-script chars-per-token model when
# the vocab is unavailable. The heuristic is already a rough lower bound on token
# count, so we pad by 20% to preserve the never-under-count property.
HEURISTIC_MARGIN: float = 1.2

# Location of the vendored, gzip-compressed o200k_base vocab. Kept under
# ``scripts/`` so it ships in the hatchling wheel (``packages = ["scripts"]``);
# a repo-root data file would be absent from the wheel (cf. issue #32).
_VOCAB_PATH: Path = Path(__file__).parent / "data" / "o200k_base.tiktoken.gz"

# The exact o200k_base pre-tokenizer whitespace set. Derived from the codepoints
# that the ``regex`` module's ``\s`` matches (Zs/Zl/Zp + the 6 Cc whitespace
# controls), which is what ``tiktoken``'s pattern relies on. Hard-coded so the
# stdlib port matches ``\s`` exactly without depending on ``str.isspace`` quirks.
_WHITESPACE: frozenset[int] = frozenset(
    {
        0x09,
        0x0A,
        0x0B,
        0x0C,
        0x0D,
        0x20,
        0x85,
        0xA0,
        0x1680,
        0x2000,
        0x2001,
        0x2002,
        0x2003,
        0x2004,
        0x2005,
        0x2006,
        0x2007,
        0x2008,
        0x2009,
        0x200A,
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    }
)

# Case-insensitive contraction suffixes from the o200k_base pattern's
# ``(?i:'s|'t|'re|'ve|'m|'ll|'d)`` group. Ordered longest-first is unnecessary
# because the alternatives are mutually unambiguous after the leading quote, but
# we still test all of them.
_CONTRACTIONS: tuple[str, ...] = ("'s", "'t", "'re", "'ve", "'m", "'ll", "'d")

# Module-global cache of the parsed {token_bytes: rank} dict. Populated lazily on
# first use and reused for the lifetime of the process.
_RANKS: dict[bytes, int] | None = None
# Sentinel marking that we already tried (and failed) to load the vocab, so we
# do not re-attempt the gzip read on every call once tier 3 has been selected.
_RANKS_LOAD_FAILED: bool = False


@dataclass
class TokenEstimate:
    """Result of a token estimate.

    Attributes
    ----------
    tokens:
        The conservative token count (always rounded up).
    method:
        Which tier produced the count: ``"api"``, ``"bpe"``, or ``"heuristic"``.
    detail:
        Human-readable note about how the estimate was produced.
    """

    tokens: int
    method: str
    detail: str


# ---------------------------------------------------------------------------
# Unicode category predicates (the stdlib stand-ins for \p{...})
# ---------------------------------------------------------------------------
def _is_letter(ch: str) -> bool:
    """True for ``\\p{L}`` — any character whose general category starts with L."""
    return unicodedata.category(ch)[0] == "L"


def _is_number(ch: str) -> bool:
    """True for ``\\p{N}`` — any character whose general category starts with N."""
    return unicodedata.category(ch)[0] == "N"


def _is_upper_set(ch: str) -> bool:
    """True for ``[\\p{Lu}\\p{Lt}\\p{Lm}\\p{Lo}\\p{M}]``.

    This is the "first letter run" class in the o200k_base pattern's two letter
    alternatives. Note Lo/Lm and all M categories belong to BOTH this set and
    :func:`_is_lower_set`; the scanner relies on that overlap.
    """
    cat = unicodedata.category(ch)
    return cat in ("Lu", "Lt", "Lm", "Lo") or cat[0] == "M"


def _is_lower_set(ch: str) -> bool:
    """True for ``[\\p{Ll}\\p{Lm}\\p{Lo}\\p{M}]`` — the "second letter run" class."""
    cat = unicodedata.category(ch)
    return cat in ("Ll", "Lm", "Lo") or cat[0] == "M"


def _is_space(ch: str) -> bool:
    """True for ``\\s`` as the o200k_base pattern means it (see ``_WHITESPACE``)."""
    return ord(ch) in _WHITESPACE


def _match_contraction(text: str, i: int) -> int:
    """Return the length of a case-insensitive contraction suffix at ``text[i:]``.

    Returns 0 when no contraction matches. Mirrors ``(?i:'s|'t|'re|'ve|'m|'ll|'d)``.
    """
    window = text[i : i + 4].lower()
    for suffix in _CONTRACTIONS:
        if window.startswith(suffix):
            return len(suffix)
    return 0


# ---------------------------------------------------------------------------
# Pre-tokenizer: a hand-written scanner faithful to the o200k_base regex.
#
# The o200k_base split pattern is the ordered alternation (tiktoken source):
#   1. [^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:...)?
#   2. [^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:...)?
#   3. \p{N}{1,3}
#   4.  ?[^\s\p{L}\p{N}]+[\r\n/]*
#   5. \s*[\r\n]+
#   6. \s+(?!\S)
#   7. \s+
# Regex alternation is ORDERED: at each position the first alternative that can
# match wins (NOT leftmost-longest across alternatives). Within an alternative
# the quantifiers are greedy with backtracking. The scanner reproduces this
# exactly, including the backtracking in alternative 1's [upper]*[lower]+ where
# Lo/Lm/M chars belong to both classes.
# ---------------------------------------------------------------------------
def _pre_tokenize(text: str) -> list[str]:
    """Split ``text`` into o200k_base pre-tokens, byte-exact with tiktoken."""
    out: list[str] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]

        # Alternative 3: \p{N}{1,3} — must precede the letter alternatives only
        # for digits, which are not in the letter classes, so order vs 1/2 is
        # moot, but we check it first as a fast path.
        if _is_number(ch):
            j = i
            count = 0
            while j < n and count < 3 and _is_number(text[j]):
                j += 1
                count += 1
            out.append(text[i:j])
            i = j
            continue

        # Alternative 1, then 2 (ordered). Both start with an OPTIONAL leading
        # char that is neither CR/LF nor a letter/number.
        end = _match_letter_alt1(text, i, n)
        if end is not None:
            out.append(text[i:end])
            i = end
            continue
        end = _match_letter_alt2(text, i, n)
        if end is not None:
            out.append(text[i:end])
            i = end
            continue

        # Alternative 4: " ?[^\s\p{L}\p{N}]+[\r\n/]*"
        end = _match_symbol_run(text, i, n)
        if end is not None:
            out.append(text[i:end])
            i = end
            continue

        # Alternatives 5/6/7 — whitespace handling.
        if _is_space(ch):
            end = _match_whitespace(text, i, n)
            out.append(text[i:end])
            i = end
            continue

        # Defensive fallback. The seven alternatives above are exhaustive for
        # any input (every char is a letter, number, whitespace, or "other"
        # which alt 4 consumes), so this should be unreachable. We still consume
        # one char to guarantee forward progress and never loop forever.
        out.append(text[i : i + 1])
        i += 1
    return out


def _letter_prefix_end(text: str, i: int, n: int) -> int:
    """Consume the optional ``[^\\r\\n\\p{L}\\p{N}]?`` prefix; return new index."""
    if i < n:
        ch = text[i]
        if ch not in ("\r", "\n") and not _is_letter(ch) and not _is_number(ch):
            return i + 1
    return i


def _match_letter_alt1(text: str, i: int, n: int) -> int | None:
    """Match alternative 1: ``prefix? [upper]* [lower]+ contraction?``.

    The ``[upper]*`` quantifier is greedy but must leave at least one
    ``[lower]`` character; because Lo/Lm/M chars are in both classes this
    requires honest backtracking, which is what distinguishes alt1 from alt2.
    Returns the end index, or ``None`` if alt1 cannot match here.
    """
    base = _letter_prefix_end(text, i, n)
    # Greedily extend the upper-set run.
    upper_end = base
    while upper_end < n and _is_upper_set(text[upper_end]):
        upper_end += 1
    # Backtrack the upper run, looking for the longest overall match in which a
    # non-empty lower-set run begins.
    pos = upper_end
    while pos >= base:
        if pos < n and _is_lower_set(text[pos]):
            k = pos
            while k < n and _is_lower_set(text[k]):
                k += 1
            j = k + _match_contraction(text, k)
            return j if j > i else None
        pos -= 1
    return None


def _match_letter_alt2(text: str, i: int, n: int) -> int | None:
    """Match alternative 2: ``prefix? [upper]+ [lower]* contraction?``.

    Returns the end index, or ``None`` if alt2 cannot match here.
    """
    base = _letter_prefix_end(text, i, n)
    upper_end = base
    while upper_end < n and _is_upper_set(text[upper_end]):
        upper_end += 1
    if upper_end == base:
        return None
    j = upper_end
    while j < n and _is_lower_set(text[j]):
        j += 1
    j += _match_contraction(text, j)
    return j if j > i else None


def _match_symbol_run(text: str, i: int, n: int) -> int | None:
    """Match alternative 4: `` ?[^\\s\\p{L}\\p{N}]+[\\r\\n/]*``.

    Returns the end index, or ``None`` if alt4 cannot match here.
    """
    j = i
    # The optional leading single space only counts if it is followed by a
    # "symbol" character (not whitespace, letter, or number).
    if text[j] == " ":
        nxt = j + 1
        if nxt < n and not _is_space(text[nxt]) and not _is_letter(text[nxt]) and not _is_number(text[nxt]):
            j += 1
    sym_start = j
    while j < n and not _is_space(text[j]) and not _is_letter(text[j]) and not _is_number(text[j]):
        j += 1
    if j == sym_start:
        return None
    # Trailing [\r\n/]* .
    while j < n and text[j] in ("\r", "\n", "/"):
        j += 1
    return j


def _match_whitespace(text: str, i: int, n: int) -> int:
    """Match alternatives 5/6/7 for a whitespace run starting at ``i``.

    Encodes the ordered alternation:
      5. ``\\s*[\\r\\n]+`` — a whitespace run that ends at the last CR/LF.
      6. ``\\s+(?!\\S)`` — a whitespace run not immediately followed by a
         non-space (only fully satisfiable at end-of-string, else it yields the
         maximal run minus one).
      7. ``\\s+`` — the remaining single whitespace before a non-space.
    Returns the end index (always > ``i``).
    """
    run_end = i
    while run_end < n and _is_space(text[run_end]):
        run_end += 1
    # Alt 5: prefer a span ending at the LAST newline inside the run.
    last_newline = -1
    for t in range(i, run_end):
        if text[t] in ("\r", "\n"):
            last_newline = t
    if last_newline >= 0:
        return last_newline + 1
    # Alt 6: \s+(?!\S). If the run reaches end-of-string the whole run matches;
    # otherwise the char after the maximal run is non-space, so the longest span
    # satisfying the negative lookahead is the run minus its final char.
    if run_end == n:
        return run_end
    if run_end - 1 > i:
        return run_end - 1
    # Alt 7: a single whitespace char before a non-space.
    return run_end


# ---------------------------------------------------------------------------
# Byte-level BPE over the o200k_base ranks.
# ---------------------------------------------------------------------------
def _load_ranks() -> dict[bytes, int] | None:
    """Load and cache the o200k_base ``{token_bytes: rank}`` map.

    Returns ``None`` (and remembers the failure) if the vendored gzip vocab
    cannot be read or parsed, so callers fall back to the heuristic tier.
    """
    global _RANKS, _RANKS_LOAD_FAILED
    if _RANKS is not None:
        return _RANKS
    if _RANKS_LOAD_FAILED:
        return None
    try:
        with gzip.open(_VOCAB_PATH, "rb") as fh:
            raw = fh.read()
        ranks: dict[bytes, int] = {}
        for line in raw.split(b"\n"):
            line = line.strip()
            if not line:
                continue
            token_b64, rank_str = line.split()
            ranks[base64.b64decode(token_b64)] = int(rank_str)
        if not ranks:
            raise ValueError("empty vocab")
        _RANKS = ranks
        return _RANKS
    except (OSError, ValueError, binascii.Error):
        # OSError: file missing / unreadable / not a gzip.
        # ValueError: malformed line or empty vocab.
        # binascii.Error: bad base64 in a token.
        _RANKS_LOAD_FAILED = True
        return None


def _bpe(piece: bytes, ranks: dict[bytes, int]) -> int:
    """Return the number of BPE tokens for one pre-token's UTF-8 bytes.

    Standard byte-level byte-pair encoding: start from single bytes and
    repeatedly merge the adjacent pair with the lowest rank until no mergeable
    pair remains. We only need the COUNT, so we track segment boundaries rather
    than materializing the token bytes.
    """
    direct = ranks.get(piece)
    if direct is not None:
        return 1
    if len(piece) <= 1:
        # A single byte that is not in the vocab cannot happen for o200k_base
        # (all 256 bytes are present), but count it as one token to stay safe.
        return 1
    # ``parts`` holds the current byte segments.
    parts: list[bytes] = [piece[k : k + 1] for k in range(len(piece))]
    while len(parts) > 1:
        min_rank: int | None = None
        min_idx = -1
        for k in range(len(parts) - 1):
            rank = ranks.get(parts[k] + parts[k + 1])
            if rank is not None and (min_rank is None or rank < min_rank):
                min_rank = rank
                min_idx = k
        if min_idx < 0:
            break
        parts[min_idx : min_idx + 2] = [parts[min_idx] + parts[min_idx + 1]]
    return len(parts)


def _count_o200k(text: str, ranks: dict[bytes, int]) -> int:
    """Count o200k_base tokens for ``text`` (no Claude correction applied)."""
    total = 0
    for piece in _pre_tokenize(text):
        total += _bpe(piece.encode("utf-8"), ranks)
    return total


# ---------------------------------------------------------------------------
# Tier 3: per-script heuristic.
# ---------------------------------------------------------------------------
# Approximate characters-per-token by writing system. Lower ratio => more tokens
# per character => the script tokenizes "heavier". These are deliberately
# conservative (slightly low) so the resulting token count does not under-count.
_SCRIPT_RATIOS: dict[str, float] = {
    "latin": 3.5,
    "cyrillic": 3.5,
    "greek": 3.5,
    "cjk": 1.0,
    "kana": 1.0,
    "hangul": 1.0,
    "sea": 1.5,  # Thai, Lao, Khmer, Myanmar
    "indic": 1.6,
    "semitic": 2.0,  # Arabic, Hebrew
    "other": 1.0,  # symbols, emoji, unknown
}


def _script_of(ch: str) -> str:
    """Classify a character into a coarse writing-system bucket via its name."""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "other"
    cat = unicodedata.category(ch)
    if cat[0] not in ("L", "N", "M"):
        return "other"
    if name.startswith("CJK") or "IDEOGRAPH" in name:
        return "cjk"
    if name.startswith(("HIRAGANA", "KATAKANA")):
        return "kana"
    if name.startswith("HANGUL"):
        return "hangul"
    if name.startswith(("THAI", "LAO", "KHMER", "MYANMAR")):
        return "sea"
    if name.startswith(("DEVANAGARI", "BENGALI", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM", "GUJARATI", "GURMUKHI", "ORIYA", "SINHALA")):
        return "indic"
    if name.startswith(("ARABIC", "HEBREW")):
        return "semitic"
    if name.startswith("CYRILLIC"):
        return "cyrillic"
    if name.startswith("GREEK"):
        return "greek"
    if name.startswith(("LATIN", "DIGIT")) or cat[0] == "N":
        return "latin"
    return "other"


def _estimate_heuristic(text: str) -> int:
    """Conservative per-script token estimate when the vocab is unavailable."""
    if not text:
        return 0
    token_sum = 0.0
    for ch in text:
        ratio = _SCRIPT_RATIOS[_script_of(ch)]
        token_sum += 1.0 / ratio
    return math.ceil(token_sum * HEURISTIC_MARGIN)


# ---------------------------------------------------------------------------
# Tier 1: Anthropic count_tokens API.
# ---------------------------------------------------------------------------
def _estimate_api(text: str) -> int | None:
    """Return the exact Claude token count via the Anthropic API, or ``None``.

    Best-effort: any error (missing SDK, missing key, network/HTTP failure)
    returns ``None`` so the caller falls back to the BPE tier.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=api_key)
        model = os.environ.get("CPV_TOKEN_API_MODEL", "claude-sonnet-4-5")
        result = client.messages.count_tokens(
            model=model,
            messages=[{"role": "user", "content": text if text else " "}],
        )
        tokens = int(result.input_tokens)
        # The count_tokens endpoint includes a small fixed message-framing
        # overhead. For an empty input we report 0 to keep the contract that
        # empty text costs nothing in our gates.
        if not text:
            return 0
        return tokens
    except Exception:
        # Deliberately broad: the API tier is opt-in and best-effort, and the
        # SDK can raise a wide range of error types (auth, rate-limit, network,
        # serialization). Any failure must fall through to tier 2.
        return None


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def estimate_tokens(text: str, *, allow_api: bool = False) -> TokenEstimate:
    """Estimate the Claude token count of ``text``, conservatively (rounds UP).

    Parameters
    ----------
    text:
        The text to estimate. May be empty.
    allow_api:
        When True AND ``CPV_TOKEN_EXACT=1`` AND ``ANTHROPIC_API_KEY`` is set,
        the exact Anthropic ``count_tokens`` API is consulted first. Any failure
        silently falls back to the offline BPE estimate.

    Returns
    -------
    TokenEstimate
        The conservative token count plus which tier produced it.
    """
    # Tier 1: exact API (opt-in, best-effort).
    if allow_api and os.environ.get("CPV_TOKEN_EXACT") == "1":
        api_tokens = _estimate_api(text)
        if api_tokens is not None:
            return TokenEstimate(
                tokens=api_tokens,
                method="api",
                detail="exact count via Anthropic count_tokens API",
            )

    # Tier 2: offline o200k_base BPE port + Claude correction (default).
    ranks = _load_ranks()
    if ranks is not None:
        if not text:
            return TokenEstimate(
                tokens=0,
                method="bpe",
                detail="empty input",
            )
        raw = _count_o200k(text, ranks)
        corrected = math.ceil(raw * CLAUDE_CORRECTION)
        return TokenEstimate(
            tokens=corrected,
            method="bpe",
            detail=f"o200k_base BPE ({raw} tokens) x{CLAUDE_CORRECTION} Claude-correction, rounded up",
        )

    # Tier 3: per-script heuristic (vocab unavailable).
    tokens = _estimate_heuristic(text)
    return TokenEstimate(
        tokens=tokens,
        method="heuristic",
        detail=f"per-script chars/token heuristic x{HEURISTIC_MARGIN} margin (vendored vocab unavailable)",
    )


def count_o200k_tokens(text: str) -> int:
    """Return the raw o200k_base token count (no Claude correction).

    Exposed for parity testing against ``tiktoken``/``gpt-tokenizer``. Raises
    ``RuntimeError`` if the vendored vocab cannot be loaded.
    """
    ranks = _load_ranks()
    if ranks is None:
        raise RuntimeError(f"o200k_base vocab could not be loaded from {_VOCAB_PATH}")
    return _count_o200k(text, ranks)


if __name__ == "__main__":
    import sys

    _input = sys.stdin.read() if len(sys.argv) < 2 else sys.argv[1]
    _est = estimate_tokens(_input)
    print(f"{_est.tokens} tokens  [{_est.method}]  {_est.detail}")
