#!/usr/bin/env python3
"""Two-sided regression tests for the TOKEN-ESTIMATION audit findings.

Source report: reports/audit/20260525_101646+0200-token-estimation.md
Owned files: scripts/cpv_token_estimate.py, scripts/cpv_token_cost.py
(plus read-only exercise of the gate wrapper in scripts/cpv_validation_common.py).

Findings covered here:

#1 MAJOR (already fixed in source) — the Tier-3 heuristic never under-counts
   emoji / symbol / combining-mark content. Corroborated below against the LIVE
   o200k BPE count (`estimate_tokens >= raw_o200k`) on the audit's exact evidence
   strings, in addition to the pin in
   test_audit_caches_token.TestHeuristicNeverUnderCountsEmoji.

#2 MINOR (fixed) — the module docstring no longer makes the false absolute
   "byte-exact ... on tens of thousands of multilingual strings" claim; it now
   states the ±1 caveat absorbed by the ×1.3 correction.

#3 MINOR (fixed) — get_pricing resolves the MOST SPECIFIC model key regardless
   of dict insertion order (longest-key-first), so a dated point-release id is no
   longer shadowed by its base key. Two-sided: every dict key still resolves to
   its own price, AND each price bracket (including boundary dated ids) is
   reachable.

#4 NIT (fixed) — _bpe is now O(n log n) via a heap + linked list. The refactor
   is OUTPUT-IDENTICAL: counts match the frozen tiktoken fixtures AND match a
   byte-for-byte reference re-implementation of the original quadratic algorithm
   on a broad corpus. A pathological single-repeated-char pre-token now completes
   quickly.

#5 NIT (fixed) — the gate wrapper check_token_limit and the deprecation helper
   removed_cpv_size_keys_present now have direct two-sided coverage, including the
   strict-`>` boundary.

The never-under-count guarantee is asserted after every change and is NEVER
weakened.
"""

from __future__ import annotations

import math
import sys
import time
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cpv_token_cost as ctc  # noqa: E402
import cpv_token_estimate as cte  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402

# U+0301 COMBINING ACUTE ACCENT (category Mn) — written as an escape so the
# source bytes are unambiguously base-letter + combining-mark, never a
# precomposed glyph.
_COMBINING_ACUTE = "́"


@pytest.fixture(autouse=True)
def _reset_module_cache() -> Iterator[None]:
    """Restore a clean vocab-cache state before AND after each test.

    Mirrors the sibling suite. The teardown (restore on the way OUT) is what
    stops this file from leaking a bogus ``_VOCAB_PATH`` / ``_RANKS_LOAD_FAILED``
    to whatever test file runs next in CI's single-process serial run.
    """

    def _clean() -> None:
        cte._RANKS = None
        cte._RANKS_LOAD_FAILED = False
        cte._VOCAB_PATH = Path(cte.__file__).parent / "data" / "o200k_base.tiktoken.gz"

    _clean()
    yield
    _clean()


# ===========================================================================
# Finding #1 (already fixed) — Tier-3 heuristic never under-counts emoji /
# symbol / combining content. Corroborated against the live BPE count.
# ===========================================================================
def _heuristic_tokens(text: str) -> int:
    """Force the Tier-3 heuristic by pointing the vocab path at a missing file."""
    cte._VOCAB_PATH = Path("/definitely/not/a/real/vocab.gz")
    cte._RANKS = None
    cte._RANKS_LOAD_FAILED = False
    try:
        return cte.estimate_tokens(text).tokens
    finally:
        cte._VOCAB_PATH = Path(cte.__file__).parent / "data" / "o200k_base.tiktoken.gz"
        cte._RANKS = None
        cte._RANKS_LOAD_FAILED = False


class TestFinding1HeuristicNeverUnderCounts:
    """token #1 — heuristic fallback stays >= the real o200k count on heavy content."""

    # The audit's exact evidence rows (pure emoji / combining / box-drawing /
    # math-symbol), plus a contraction/ascii control.
    _CASES = [
        ("pure_emoji", "\U0001f680\U0001f525✨\U0001f44d\U0001f3fd" * 4),
        ("combining", "".join(b + _COMBINING_ACUTE for b in "aeiou") * 4),
        ("box_drawing", "┌─┐│└┘├┤┬┴┼═" * 2),
        ("math_symbols", "∑∫√≠≤≥±∈∉⊂⊇"),
        ("ascii_control", "the quick brown fox jumps " * 10),
    ]

    @pytest.mark.parametrize("label,text", _CASES, ids=[c[0] for c in _CASES])
    def test_heuristic_ge_raw_o200k(self, label: str, text: str) -> None:
        """The heuristic count must never drop below the raw o200k count."""
        raw = cte.count_o200k_tokens(text)
        assert _heuristic_tokens(text) >= raw, f"heuristic under-counted on {label!r}"

    @pytest.mark.parametrize("label,text", _CASES, ids=[c[0] for c in _CASES])
    def test_heuristic_ge_corrected_estimate(self, label: str, text: str) -> None:
        """And it must stay >= the corrected (×1.3) BPE estimate the gate would use."""
        bpe_estimate = cte.estimate_tokens(text).tokens
        assert _heuristic_tokens(text) >= bpe_estimate, f"heuristic under-counted on {label!r}"

    def test_combining_mark_is_classified_heavy_not_base_script(self) -> None:
        """A standalone combining mark must cost >= its UTF-8 byte length (not 1/3.5)."""
        # U+0301 encodes to 2 UTF-8 bytes; the conservative bound must be >= 2,
        # not the ~0.29 it would get if folded into the latin (3.5) bucket.
        assert unicodedata.category(_COMBINING_ACUTE).startswith("M")
        single = cte._estimate_heuristic(_COMBINING_ACUTE)
        assert single >= len(_COMBINING_ACUTE.encode("utf-8"))


# ===========================================================================
# Finding #2 (fixed) — docstring no longer makes the false absolute parity claim.
# ===========================================================================
class TestFinding2DocstringTruthful:
    """token #2 — the module docstring states the real, caveated guarantee."""

    def test_no_false_absolute_parity_claim(self) -> None:
        """The old absolute 'tens of thousands of multilingual strings' claim is gone."""
        doc = cte.__doc__ or ""
        assert "tens of thousands of multilingual strings" not in doc

    def test_docstring_mentions_pm1_caveat(self) -> None:
        """The docstring now discloses the ±1 deviation absorbed by the correction."""
        doc = cte.__doc__ or ""
        assert "±1" in doc  # the literal "±1"
        assert "correction" in doc.lower()


# ===========================================================================
# Finding #3 (fixed) — get_pricing matches the most specific key, order-independent.
# ===========================================================================
class TestFinding3PricingLongestKeyFirst:
    """token #3 — dated point-release ids resolve to their own price, not the base."""

    def test_dated_opus_4_1_not_shadowed_by_base(self) -> None:
        """claude-opus-4-1-<date> must resolve via the claude-opus-4-1 key, not claude-opus-4."""
        # Both currently share $15 input, so assert via the resolved dict IDENTITY
        # (object equality) to prove the SPECIFIC key won — not merely the price.
        resolved = ctc.get_pricing("claude-opus-4-1-20250805")
        assert resolved is ctc.MODEL_PRICING["claude-opus-4-1"]

    def test_dated_opus_4_base_still_resolves_to_base(self) -> None:
        """claude-opus-4-<date> still resolves to the base claude-opus-4 key."""
        resolved = ctc.get_pricing("claude-opus-4-20250514")
        assert resolved is ctc.MODEL_PRICING["claude-opus-4"]

    @pytest.mark.parametrize("key", list(ctc.MODEL_PRICING))
    def test_every_key_resolves_to_itself(self, key: str) -> None:
        """Two-sided: an exact key, and that key + a date suffix, both resolve to it."""
        assert ctc.get_pricing(key) is ctc.MODEL_PRICING[key]
        # A dated id built from the key must not be shadowed by any shorter key.
        assert ctc.get_pricing(f"{key}-20250101") is ctc.MODEL_PRICING[key]

    def test_distinct_price_brackets_are_all_reachable(self) -> None:
        """Each distinct input-price bracket is reachable from a realistic dated id."""
        assert ctc.get_pricing("claude-opus-4-6-20251201")["input"] == 5.0
        assert ctc.get_pricing("claude-opus-4-1-20250805")["input"] == 15.0
        assert ctc.get_pricing("claude-sonnet-4-6-20251101")["input"] == 3.0
        assert ctc.get_pricing("claude-haiku-4-5-20251001")["input"] == 1.0
        assert ctc.get_pricing("claude-haiku-3-5-20240620")["input"] == 0.80

    def test_unknown_model_falls_back_to_default(self) -> None:
        """An unrelated id still hits the documented default pricing."""
        assert ctc.get_pricing("gpt-4o-mini") == ctc.DEFAULT_PRICING

    def test_empty_model_returns_default(self) -> None:
        """Empty model name returns the default pricing."""
        assert ctc.get_pricing("") == ctc.DEFAULT_PRICING


# ===========================================================================
# Finding #4 (fixed) — _bpe refactor is output-identical and no longer quadratic.
# ===========================================================================
def _bpe_reference(piece: bytes, ranks: dict[bytes, int]) -> int:
    """Byte-for-byte re-implementation of the ORIGINAL O(n^2) _bpe algorithm.

    This is the known-good reference: it merges the lowest-rank adjacent pair,
    leftmost on a tie (strict ``<``), exactly as the pre-refactor code did. The
    optimized _bpe must produce identical counts to this for every input.
    """
    direct = ranks.get(piece)
    if direct is not None:
        return 1
    if len(piece) <= 1:
        return 1
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


# Frozen tiktoken reference counts (subset of the sibling suite's FIXTURES) — the
# authoritative known-good o200k_base counts.
_TIKTOKEN_FIXTURES: list[tuple[str, str, int]] = [
    ("en", "The quick brown fox jumps over the lazy dog.", 10),
    ("en_punct", "Hello, world! It's a test.", 8),
    ("zh", "你好，世界！这是一个测试。", 8),
    ("code", "def foo(x):\n    return x * 2  # double it\n", 15),
    ("json", '{"key": "value", "n": 42, "arr": [1,2,3]}', 22),
    ("emoji", "\U0001f680\U0001f525✨\U0001f44d\U0001f3fd", 7),
    ("camel", "XMLHttpRequest getHTTPResponseCode CamelCase snake_case", 11),
]


class TestFinding4BpeRefactorIdentical:
    """token #4 — optimized _bpe is output-identical and no longer quadratic."""

    @pytest.mark.parametrize("label,text,expected", _TIKTOKEN_FIXTURES, ids=[f[0] for f in _TIKTOKEN_FIXTURES])
    def test_matches_frozen_tiktoken_counts(self, label: str, text: str, expected: int) -> None:
        """The full estimator still equals the frozen tiktoken reference counts."""
        assert cte.count_o200k_tokens(text) == expected

    def test_optimized_bpe_equals_reference_on_corpus(self) -> None:
        """Per-pre-token, optimized _bpe == the original quadratic reference."""
        ranks = cte._load_ranks()
        assert ranks is not None
        corpus = [
            "The quick brown fox jumps over the lazy dog.",
            "Hello, world! It's a test — y'all rock'n'roll.",
            "def estimate(text):\n    return len(text) * 2  # double\n",
            '{"key": "value", "nested": {"a": [1, 2, 3]}, "n": 42}',
            "人工智能正在改变世界。",  # CJK
            "नमस्ते दुनिया",  # Hindi w/ combining
            "\U0001f389\U0001f38a\U0001f680\U0001f525\U0001f4af",  # emoji
            "aaaaaaaaaaaaaaaaaaaa",  # repeated single char (the quadratic case, short)
            "================================",  # repeated symbol
            "----------",
            "MMMMMMMMMMmmmmmmmmmm",  # upper run + lower run (alt-1/alt-2 path)
            "café" + _COMBINING_ACUTE + "résumé",  # combining marks interleaved
            "",  # empty pre-token bytes guarded upstream, but be safe
            "a",  # single char
        ]
        for text in corpus:
            for piece in cte._pre_tokenize(text):
                raw = piece.encode("utf-8")
                assert cte._bpe(raw, ranks) == _bpe_reference(raw, ranks), f"mismatch on {piece!r}"

    def test_optimized_bpe_equals_reference_on_pathological_repeats(self) -> None:
        """Direct _bpe call on long single-repeated-byte pieces matches the reference."""
        ranks = cte._load_ranks()
        assert ranks is not None
        for piece in (b"a" * 300, b"=" * 200, b"-" * 150, b"x" * 257):
            assert cte._bpe(piece, ranks) == _bpe_reference(piece, ranks)

    def test_pathological_single_char_run_is_fast(self) -> None:
        """A long single-repeated-char pre-token completes quickly (no quadratic blowup)."""
        ranks = cte._load_ranks()
        assert ranks is not None
        start = time.perf_counter()
        count = cte._bpe(b"a" * 10000, ranks)
        elapsed = time.perf_counter() - start
        assert count > 0
        # The old quadratic path took ~3.2 s on this input; the heap version is
        # well under a second. Generous ceiling to stay non-flaky under load.
        assert elapsed < 2.0, f"_bpe on 10000 repeats took {elapsed:.2f}s (expected sub-second)"

    def test_estimate_still_conservative_after_refactor(self) -> None:
        """The never-under-count guarantee still holds after the _bpe refactor."""
        for _label, text, expected in _TIKTOKEN_FIXTURES:
            est = cte.estimate_tokens(text)
            assert est.tokens >= expected
            assert est.tokens == math.ceil(expected * cte.CLAUDE_CORRECTION)


# ===========================================================================
# Finding #5 (fixed) — gate wrapper + deprecation helper now have coverage.
# ===========================================================================
class TestFinding5CheckTokenLimit:
    """token #5 — check_token_limit two-sided boundary + message coverage."""

    _TEXT = "The quick brown fox jumps over the lazy dog. " * 6

    def _estimate(self) -> int:
        return cte.estimate_tokens(self._TEXT).tokens

    def test_at_limit_does_not_fire(self) -> None:
        """estimate == limit must PASS (strict `>`), emitting no MAJOR."""
        est = self._estimate()
        report = cvc.ValidationReport()
        fired = cvc.check_token_limit(self._TEXT, est, report, "f.md", "Body", "trim it")
        assert fired is False
        assert not any(r.level == "MAJOR" for r in report.results)

    def test_one_under_limit_fires_major(self) -> None:
        """estimate == limit-1 must FIRE a MAJOR finding."""
        est = self._estimate()
        report = cvc.ValidationReport()
        fired = cvc.check_token_limit(self._TEXT, est - 1, report, "f.md", "Body", "trim it")
        assert fired is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) == 1

    def test_message_contains_count_limit_method_and_advice(self) -> None:
        """The MAJOR message reports the estimate, the limit, the method, and the advice."""
        est = self._estimate()
        report = cvc.ValidationReport()
        cvc.check_token_limit(self._TEXT, est - 1, report, "skill.md", "Description", "shorten it")
        msg = next(r.message for r in report.results if r.level == "MAJOR")
        assert str(est) in msg
        assert str(est - 1) in msg
        assert "Description" in msg
        assert "shorten it" in msg
        # The method is surfaced for debuggability — since issue #193 as the
        # estimator's full detail line (raw o200k count + the x1.3 factor),
        # because the bare tier name "bpe estimate" read as a raw BPE count
        # and sent a reporter reverse-engineering a chars/3 divisor that
        # never existed.
        assert "o200k_base BPE (" in msg
        assert "x1.3 Claude-correction" in msg

    def test_empty_text_returns_false_no_finding(self) -> None:
        """Empty text short-circuits to no finding regardless of the limit."""
        report = cvc.ValidationReport()
        assert cvc.check_token_limit("", 0, report, "f.md", "Body", "x") is False
        assert report.results == []

    def test_file_path_is_attached_to_finding(self) -> None:
        """The reported file path is the one passed to the gate."""
        est = self._estimate()
        report = cvc.ValidationReport()
        cvc.check_token_limit(self._TEXT, est - 1, report, "the/file.md", "Body", "x")
        major = next(r for r in report.results if r.level == "MAJOR")
        assert major.file == "the/file.md"


class TestFinding5RemovedSizeKeys:
    """token #5 — removed_cpv_size_keys_present detects retired override keys."""

    def test_no_retired_keys_returns_empty(self) -> None:
        """A config with only live keys returns an empty list."""
        assert cvc.removed_cpv_size_keys_present({"unrelated": 1, "model": "opus"}) == []

    def test_empty_config_returns_empty(self) -> None:
        """An empty config returns an empty list."""
        assert cvc.removed_cpv_size_keys_present({}) == []

    @pytest.mark.parametrize("key", ["max_chars", "max_lines", "skill_size_severity"])
    def test_each_retired_key_is_detected(self, key: str) -> None:
        """Each individual retired key is reported when present."""
        assert cvc.removed_cpv_size_keys_present({key: 123}) == [key]

    def test_all_retired_keys_detected_together(self) -> None:
        """All three retired keys present together are all returned."""
        cfg = {"max_chars": 1, "max_lines": 2, "skill_size_severity": "MINOR", "other": True}
        result = cvc.removed_cpv_size_keys_present(cfg)
        assert set(result) == {"max_chars", "max_lines", "skill_size_severity"}
